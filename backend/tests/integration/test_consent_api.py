"""consent API(M4 ws-5 計画Task 8・design §2.3・§4)のintegration試験。

実HTTP(compose常設api)。POST/GET /v1/users/me/consent・401・404(未登録)・
422 CONSENT_REQUIREDの遮断3経路(parse・active作成・draft→active)と同意後の
解除、users.consent_at列の存在(0007)を検証する。実行はtest-ci
(スーパーバイザー検証時・apiイメージ再構築後)。レート制限はルータ単位の
api_rate_limited継承をtest_rate_limit_wiring.pyが静的担保する(M1 ws-4流儀)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration


async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        provider,
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def _token(api_client, subject: str) -> str:
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


async def _register(api_client, *, consent: bool) -> dict:
    """ユーザー登録(同意する/しないを選べる)。headersとsubjectを返す。"""
    subject = f"ws5-{uuid_mod.uuid4().hex[:12]}"
    access = await _token(api_client, subject)
    headers = {"Authorization": f"Bearer {access}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "ws5", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    if consent:
        resp = await api_client.post("/v1/users/me/consent", headers=headers)
        assert resp.status_code == 200, resp.text
    return {"headers": headers, "subject": subject}


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured() -> dict:
    return {
        "category": {"primary": "meal", "secondary": None},
        "alcohol_involved": False,
        "time": {"start": _future(3), "end": None},
        "location": {"name": "天文館"},
    }


# -- スキーマ(0007) --


async def test_users_consent_at_column_exists(db_engine):
    """0007適用でusersにconsent_at timestamptzがある。"""
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'users' AND column_name = 'consent_at'"
                )
            )
        ).first()
    assert row is not None and row[0] == "timestamp with time zone"


# -- 同意APIの基本挙動 --


async def test_get_returns_null_then_post_records(api_client):
    """未同意=200 null → POST 200(時刻入)→ GET 200(時刻入)。
    再POSTは後勝ちで時刻更新(冪等200)。"""
    user = await _register(api_client, consent=False)
    h = user["headers"]
    before = await api_client.get("/v1/users/me/consent", headers=h)
    assert before.status_code == 200, before.text
    assert before.json() == {"consented_at": None}

    first = await api_client.post("/v1/users/me/consent", headers=h)
    assert first.status_code == 200, first.text
    at1 = first.json()["consented_at"]
    assert isinstance(at1, str)

    again = await api_client.get("/v1/users/me/consent", headers=h)
    assert again.json() == {"consented_at": at1}

    second = await api_client.post("/v1/users/me/consent", headers=h)
    assert second.status_code == 200
    assert second.json()["consented_at"] >= at1  # 後勝ち(同時刻以上)


async def test_unauthenticated_consent_is_401(api_client):
    assert (await api_client.get("/v1/users/me/consent")).status_code == 401
    assert (await api_client.post("/v1/users/me/consent")).status_code == 401


async def test_unregistered_consent_is_404(api_client):
    """登録前JWT(行なし)は404 NOT_FOUND(users/meと同一挙動)。"""
    access = await _token(api_client, f"ws5-unreg-{uuid_mod.uuid4().hex[:8]}")
    h = {"Authorization": f"Bearer {access}"}
    resp = await api_client.get("/v1/users/me/consent", headers=h)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# -- 422 CONSENT_REQUIREDの遮断3経路+同意後の解除 --


async def test_parse_blocked_without_consent_then_allowed(api_client):
    """経路1: parse(登録済み未同意=422・LLM送信前)。同意後は200。"""
    user = await _register(api_client, consent=False)
    h = user["headers"]
    blocked = await api_client.post(
        "/v1/intents/parse", headers=h, json={"text": "今夜 天文館で飲みたい"}
    )
    assert blocked.status_code == 422, blocked.text
    # フロント誘導分岐の契約
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"

    await api_client.post("/v1/users/me/consent", headers=h)
    ok = await api_client.post(
        "/v1/intents/parse", headers=h, json={"text": "今夜 天文館で飲みたい"}
    )
    assert ok.status_code == 200, ok.text


async def test_active_create_blocked_without_consent_then_allowed(api_client):
    """経路2: POST /v1/intents(status省略=active既定も含む)。同意後は201。"""
    user = await _register(api_client, consent=False)
    h = user["headers"]
    payload = {
        "raw_text": "今夜 天文館で飲みたい",
        "structured_intent": _structured(),
    }
    blocked = await api_client.post("/v1/intents", headers=h, json=payload)
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"

    blocked_explicit = await api_client.post(
        "/v1/intents", headers=h, json={**payload, "status": "active"}
    )
    assert blocked_explicit.status_code == 422

    await api_client.post("/v1/users/me/consent", headers=h)
    ok = await api_client.post("/v1/intents", headers=h, json=payload)
    assert ok.status_code == 201, ok.text


async def test_draft_allowed_and_activation_blocked_then_allowed(api_client):
    """経路3: draft保存は許可・draft→active化は遮断。同意後は遷移できる。"""
    user = await _register(api_client, consent=False)
    h = user["headers"]
    draft = await api_client.post(
        "/v1/intents",
        headers=h,
        json={
            "raw_text": "今夜 天文館で飲みたい",
            "status": "draft",
            "structured_intent": _structured(),
        },
    )
    assert draft.status_code == 201, draft.text  # draftは同意不要(LLM送信ゼロ)
    intent_id = draft.json()["intent"]["id"]

    blocked = await api_client.patch(
        f"/v1/intents/{intent_id}",
        headers=h,
        json={
            "raw_text": "今夜 天文館で飲みたい",
            "status": "active",
            "structured_intent": _structured(),
        },
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"

    # draftは据え置き(遷移しない)
    still = await api_client.get(f"/v1/intents/{intent_id}", headers=h)
    assert still.json()["intent"]["status"] == "draft"

    await api_client.post("/v1/users/me/consent", headers=h)
    ok = await api_client.patch(
        f"/v1/intents/{intent_id}",
        headers=h,
        json={
            "raw_text": "今夜 天文館で飲みたい",
            "status": "active",
            "structured_intent": _structured(),
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["intent"]["status"] == "active"
