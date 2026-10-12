"""体感アンケートAPI(05 v0.7・09 §2.3)のintegration試験。

実HTTP(compose常設api)。GET/POST /v1/users/me/surveyの往復・due遷移
(draftは不可・active化でdue)・week4の28日境界(SQL過去化で駆動)・
409/422系・survey_responses表の存在(0010)を検証する。レート制限は
ルータ単位のapi_rate_limited継承をtest_rate_limit_wiring.pyが静的担保。
"""

from __future__ import annotations

import asyncio
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration

PREFIX = "v1p3sv-"


@pytest.fixture(autouse=True)
async def _purge_survey_users(db_engine):
    """各試験後にv1p3sv-ユーザー系の行をFK順に全削除する(teardown完全性)。

    active Intentを残すとsweeper系試験(run_onceの件数期待)が崩れるため
    test_consent_api.pyと同一の対抗策を取る。
    """
    yield
    p = {"p": f"{PREFIX}%"}
    async with db_engine.begin() as conn:
        for sql in (
            "DELETE FROM survey_responses WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM latch_status_events WHERE latch_id IN"
            " (SELECT id FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p)))",
            "DELETE FROM calibration_records WHERE latch_id IN"
            " (SELECT id FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p)))",
            "DELETE FROM notifications WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM match_candidates WHERE intent_a_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            " OR intent_b_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM match_events WHERE source_intent_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM users WHERE auth_subject LIKE :p",
        ):
            await conn.execute(text(sql), p)


async def _token(api_client, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        "google",
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    idp = stdout.decode().strip()
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


async def _register(api_client) -> dict:
    subject = f"{PREFIX}{uuid_mod.uuid4().hex[:12]}"
    access = await _token(api_client, subject)
    headers = {"Authorization": f"Bearer {access}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "v1p3", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["user"]["id"]
    consent = await api_client.post("/v1/users/me/consent", headers=headers)
    assert consent.status_code == 200, consent.text
    return {"headers": headers, "subject": subject, "user_id": user_id}


def _structured() -> dict:
    now = SystemClock().now()
    return {
        "category": {"primary": "meal", "secondary": None},
        "alcohol_involved": False,
        "time": {"start": (now + timedelta(hours=3)).isoformat(), "end": None},
        "location": {"name": "天文館"},
    }


async def _create_intent(api_client, headers, status: str = "active") -> str:
    resp = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={
            "raw_text": "今度 天文館で軽くごはん",
            "status": status,
            "structured_intent": _structured(),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]["id"]


async def _answer_onboarding(api_client, headers, convenience: int = 4) -> dict:
    resp = await api_client.post(
        "/v1/users/me/survey",
        headers=headers,
        json={"round": "onboarding", "answers": {"convenience": convenience}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# -- スキーマ(0010) --


async def test_survey_responses_table_exists(db_engine):
    """0010適用でsurvey_responses(round CHECK・UNIQUE(user_id, round))がある。"""
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'survey_responses' AND column_name = 'answers'"
                )
            )
        ).first()
        idx = (
            await conn.execute(
                text(
                    "SELECT 1 FROM pg_indexes WHERE indexname ="
                    " 'uq_survey_responses_user_round'"
                )
            )
        ).first()
    assert row is not None and row[0] == "jsonb"
    assert idx is not None


# -- GET status のdue遷移 --


async def test_status_not_due_without_intent(api_client):
    user = await _register(api_client)
    resp = await api_client.get("/v1/users/me/survey", headers=user["headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["onboarding"]["status"] == "not_due"
    assert body["onboarding"]["available_at"] is None
    assert body["week4"]["status"] == "not_due"
    assert body["week4"]["available_at"] is None


async def test_status_draft_intent_still_not_due(api_client):
    """draftは保存でありactive化でない=配信契機1の対象外(05 v0.7)。"""
    user = await _register(api_client)
    await _create_intent(api_client, user["headers"], status="draft")
    resp = await api_client.get("/v1/users/me/survey", headers=user["headers"])
    assert resp.json()["onboarding"]["status"] == "not_due"


async def test_status_due_after_activation(api_client):
    user = await _register(api_client)
    await _create_intent(api_client, user["headers"], status="active")
    resp = await api_client.get("/v1/users/me/survey", headers=user["headers"])
    body = resp.json()
    assert body["onboarding"]["status"] == "due"
    # week4はonboarding未回答のため発生しない(未回答者への再催促なし)
    assert body["week4"]["status"] == "not_due"
    assert body["week4"]["available_at"] is None


async def test_unauthenticated_survey_is_401(api_client):
    assert (await api_client.get("/v1/users/me/survey")).status_code == 401
    assert (await api_client.post("/v1/users/me/survey")).status_code == 401


async def test_unregistered_survey_is_404(api_client):
    access = await _token(api_client, f"{PREFIX}unreg-{uuid_mod.uuid4().hex[:8]}")
    resp = await api_client.get(
        "/v1/users/me/survey", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# -- POST onboarding --


async def test_submit_onboarding_then_answered(db_engine, api_client):
    user = await _register(api_client)
    await _create_intent(api_client, user["headers"])
    body = await _answer_onboarding(api_client, user["headers"], convenience=4)
    assert body["survey"]["round"] == "onboarding"
    assert isinstance(body["survey"]["answered_at"], str)

    status = await api_client.get("/v1/users/me/survey", headers=user["headers"])
    assert status.json()["onboarding"]["status"] == "answered"
    week4 = status.json()["week4"]
    assert week4["status"] == "not_due"
    assert week4["available_at"] is not None  # onboarding回答+28日

    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT round, answers, free_comment FROM survey_responses"
                    " WHERE user_id = CAST(:u AS uuid)"
                ),
                {"u": user["user_id"]},
            )
        ).first()
    assert row is not None
    assert row[0] == "onboarding"
    assert row[1] == {"convenience": 4}  # asyncpgはjsonbをdictへ自動変換
    assert row[2] is None


async def test_submit_onboarding_without_intent_409_not_due(api_client):
    user = await _register(api_client)
    resp = await api_client.post(
        "/v1/users/me/survey",
        headers=user["headers"],
        json={"round": "onboarding", "answers": {"convenience": 4}},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SURVEY_NOT_DUE"


async def test_submit_duplicate_409(api_client):
    user = await _register(api_client)
    await _create_intent(api_client, user["headers"])
    await _answer_onboarding(api_client, user["headers"])
    resp = await api_client.post(
        "/v1/users/me/survey",
        headers=user["headers"],
        json={"round": "onboarding", "answers": {"convenience": 5}},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SURVEY_ALREADY_ANSWERED"


# -- week4(28日境界・SQL過去化で駆動) --


async def test_week4_too_early_409(api_client):
    user = await _register(api_client)
    await _create_intent(api_client, user["headers"])
    await _answer_onboarding(api_client, user["headers"])
    resp = await api_client.post(
        "/v1/users/me/survey",
        headers=user["headers"],
        json={
            "round": "week4",
            "answers": {"convenience": 4, "continuation": 5},
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SURVEY_NOT_DUE"


async def test_week4_flow_after_28d(db_engine, api_client):
    user = await _register(api_client)
    await _create_intent(api_client, user["headers"])
    await _answer_onboarding(api_client, user["headers"], convenience=3)

    # 初回回答を29日前へ過去化(E2Eの期限系と同じSQL駆動手法)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE survey_responses SET created_at ="
                " CAST(:at AS timestamptz)"
                " WHERE user_id = CAST(:u AS uuid) AND round = 'onboarding'"
            ),
            {
                "at": SystemClock().now() - timedelta(days=29),
                "u": user["user_id"],
            },
        )

    status = await api_client.get("/v1/users/me/survey", headers=user["headers"])
    assert status.json()["week4"]["status"] == "due"
    assert status.json()["onboarding"]["status"] == "answered"

    resp = await api_client.post(
        "/v1/users/me/survey",
        headers=user["headers"],
        json={
            "round": "week4",
            "answers": {"convenience": 4, "continuation": 5},
            "free_comment": "  待っているだけで届くのが楽しい  ",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["survey"]["round"] == "week4"

    status = await api_client.get("/v1/users/me/survey", headers=user["headers"])
    assert status.json()["week4"]["status"] == "answered"

    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT answers, free_comment FROM survey_responses"
                    " WHERE user_id = CAST(:u AS uuid) AND round = 'week4'"
                ),
                {"u": user["user_id"]},
            )
        ).first()
    assert row[0] == {"convenience": 4, "continuation": 5}
    assert row[1] == "待っているだけで届くのが楽しい"  # 空白は正規化


# -- 422系(構造検証) --


async def test_submit_invalid_values_422(api_client):
    user = await _register(api_client)
    await _create_intent(api_client, user["headers"])
    cases = [
        {"round": "weekly", "answers": {"convenience": 4}},  # 不正round
        {"round": "onboarding", "answers": {"convenience": 6}},  # 値域外
        {"round": "onboarding", "answers": {}},  # 必須欠落
        {
            "round": "onboarding",
            "answers": {"convenience": 4, "continuation": 5},  # week4専用
        },
        {
            "round": "onboarding",
            "answers": {"convenience": 4},
            "free_comment": "早期の自由記述",  # week4専用
        },
        {
            "round": "week4",
            "answers": {"convenience": 4},  # continuation欠落
        },
        {
            "round": "week4",
            "answers": {"convenience": 4, "continuation": 5},
            "free_comment": "あ" * 2001,  # 2000字超過
        },
    ]
    for payload in cases:
        resp = await api_client.post(
            "/v1/users/me/survey", headers=user["headers"], json=payload
        )
        assert resp.status_code == 422, (payload, resp.text)
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
