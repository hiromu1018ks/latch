"""レート制限4種+auth系429のci環境実証(M1 ws-4 design §4.2)。

実HTTP(compose api=127.0.0.1:8000)・実Redis・実DB。**実行はapiイメージ
再ビルド後の make test-ci のみ**(compose upはapiイメージを再ビルドしない —
STATUS運用ルール4。スーパーバイザー検証時に実行)。subjectは実行ごとに
ユニーク、DB行は試験内で後始末(共有ci-db汚染回避 — M1 ws-3と同じ規約)。
Redis鍵(rl:*)はTTL付きで自動消滅するため追加掃除はしない(test_auth_api規約)。
api 60req/分の検証ではregister(users POST 1回)もカウントに計上されるため
GET 59回で到達・60回目で429になる(INCR先行 — design §2.4)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration


def _unique_subject(prefix: str) -> str:
    return f"m1ws4-{prefix}-{uuid_mod.uuid4().hex[:12]}"


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


async def _register(api_client, subject: str, birth_date: str = "1990-04-01"):
    """token発行→User登録→(headers, user_id)を返す(M1 ws-3流儀)。"""
    idp_token = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "ws4", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["user"]["id"]


def _future(hours: float) -> str:
    """api実Clock(SystemClock)基準の未来ISO時刻(境界から離れた値のみ使う)。"""
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured() -> dict:
    return {
        "category": {"primary": "drinking", "secondary": "焼肉"},
        "alcohol_involved": True,
        "time": {"start": _future(3), "end": _future(6)},
        "location": {"name": "天文館", "radius_m": 2000},
        "budget": {"max": 5000, "currency": "JPY"},
        "participants": {"min": 2, "max": 4},
        "visibility": "hidden_until_match",
        "notification_level": "proposals_only",
        "expires_at": _future(6),
        "soft_constraints": ["軽く飲みたい"],
        "ng_unverifiable": [],
        "negative_constraints": [],
    }


def _active_payload(
    structured: dict | None = None, raw: str = "今夜20時から天文館で軽く飲みたい"
) -> dict:
    return {
        "raw_text": raw,
        "status": "active",
        "structured_intent": structured if structured is not None else _structured(),
    }


def _draft_payload(raw: str = "下書き") -> dict:
    return {"raw_text": raw, "status": "draft"}


async def _cleanup(db_engine, user_id, subject: str) -> None:
    async with db_engine.begin() as conn:
        if user_id:
            await conn.execute(
                text(
                    "DELETE FROM match_events WHERE source_intent_id IN "
                    "(SELECT id FROM intents WHERE user_id = :uid)"
                ),
                {"uid": user_id},
            )
            await conn.execute(
                text("DELETE FROM intents WHERE user_id = :uid"), {"uid": user_id}
            )
        await conn.execute(
            text("DELETE FROM users WHERE auth_subject = :s"), {"s": subject}
        )


async def _post_active(api_client, headers, raw: str):
    return await api_client.post(
        "/v1/intents", headers=headers, json=_active_payload(raw=raw)
    )


# --- §4.2-1 作成20件/日 ---


async def test_1_create_daily_limit(api_client, db_engine):
    subject = _unique_subject("c20")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        for i in range(20):
            resp = await api_client.post(
                "/v1/intents", headers=headers, json=_draft_payload(f"下書き{i}")
            )
            assert resp.status_code == 201, (i, resp.text)
        exceeded = await api_client.post(
            "/v1/intents", headers=headers, json=_draft_payload("21件目")
        )
        assert exceeded.status_code == 429, exceeded.text
        assert exceeded.json()["error"]["code"] == "RATE_LIMITED"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-2 更新6回/時(単位=Intent) ---


async def test_2_update_hourly_limit(api_client, db_engine):
    subject = _unique_subject("u6")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        created = await _post_active(api_client, headers, "更新対象")
        assert created.status_code == 201, created.text
        intent_id = created.json()["intent"]["id"]
        moved = dict(_structured())
        moved["participants"] = {"min": 3, "max": 4}
        for i in range(6):
            resp = await api_client.patch(
                f"/v1/intents/{intent_id}",
                headers=headers,
                json=_active_payload(structured=moved, raw=f"更新{i}"),
            )
            assert resp.status_code == 200, (i, resp.text)
        exceeded = await api_client.patch(
            f"/v1/intents/{intent_id}",
            headers=headers,
            json=_active_payload(structured=moved, raw="更新7"),
        )
        assert exceeded.status_code == 429, exceeded.text
        assert exceeded.json()["error"]["code"] == "RATE_LIMITED"
        # 別Intentは単位分離(同一ユーザーでも別行は受理)
        other = await _post_active(api_client, headers, "別件")
        assert other.status_code == 201, other.text
        other_id = other.json()["intent"]["id"]
        resp = await api_client.patch(
            f"/v1/intents/{other_id}",
            headers=headers,
            json=_active_payload(structured=moved, raw="別件更新"),
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-3 API 60req/分(単位=ユーザー)・401優先 ---


async def test_3_api_per_min_and_401_priority(api_client, db_engine):
    subject = _unique_subject("a60")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        # register(users POST)を含め同一分バケットの61リクエスト目→429
        for i in range(59):
            resp = await api_client.get("/v1/intents", headers=headers)
            assert resp.status_code == 200, (i, resp.text)
        exceeded = await api_client.get("/v1/intents", headers=headers)
        assert exceeded.status_code == 429, exceeded.text
        assert exceeded.json()["error"]["code"] == "RATE_LIMITED"
        # 401優先: 超過状態でも無効JWTは401(認証が先でINCRされない)
        unauthorized = await api_client.get(
            "/v1/intents", headers={"Authorization": "Bearer invalid.jwt.value"}
        )
        assert unauthorized.status_code == 401, unauthorized.text
        assert unauthorized.json()["error"]["code"] == "UNAUTHENTICATED"
        # 別ユーザーは同時刻に受理される(単位=ユーザー)
        other_subject = _unique_subject("a60b")
        other_id = None
        try:
            other_headers, other_id = await _register(api_client, other_subject)
            for _ in range(5):
                resp = await api_client.get("/v1/intents", headers=other_headers)
                assert resp.status_code == 200, resp.text
        finally:
            await _cleanup(db_engine, other_id, other_subject)
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-4 Active 5件: 作成・枠解放 ---


async def test_4_active_limit_create_and_release(api_client, db_engine):
    subject = _unique_subject("act5")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        first_id = None
        for i in range(5):
            resp = await _post_active(api_client, headers, f"act{i}")
            assert resp.status_code == 201, (i, resp.text)
            if first_id is None:
                first_id = resp.json()["intent"]["id"]
        exceeded = await _post_active(api_client, headers, "6件目")
        assert exceeded.status_code == 422, exceeded.text
        assert exceeded.json()["error"]["code"] == "ACTIVE_INTENT_LIMIT"
        # 枠解放: 1件pauseすると6件目が受理(pauseはActive数を減らす)
        paused = await api_client.post(f"/v1/intents/{first_id}/pause", headers=headers)
        assert paused.status_code == 200, paused.text
        after = await _post_active(api_client, headers, "pause後")
        assert after.status_code == 201, after.text
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-4b Active 5件: draft→active化・resume ---


async def test_5_active_limit_on_activate_and_resume(api_client, db_engine):
    subject = _unique_subject("d2a")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        first_id = None
        for i in range(5):
            resp = await _post_active(api_client, headers, f"a{i}")
            assert resp.status_code == 201, (i, resp.text)
            if first_id is None:
                first_id = resp.json()["intent"]["id"]
        # draft→active化は6件目として422
        draft = await api_client.post(
            "/v1/intents", headers=headers, json=_draft_payload("後でactive化")
        )
        assert draft.status_code == 201, draft.text
        draft_id = draft.json()["intent"]["id"]
        activated = await api_client.patch(
            f"/v1/intents/{draft_id}",
            headers=headers,
            json=_active_payload(raw="active化"),
        )
        assert activated.status_code == 422, activated.text
        assert activated.json()["error"]["code"] == "ACTIVE_INTENT_LIMIT"
        # 1件pause→draft→active化は受理(5件目として)
        paused = await api_client.post(f"/v1/intents/{first_id}/pause", headers=headers)
        assert paused.status_code == 200, paused.text
        activated2 = await api_client.patch(
            f"/v1/intents/{draft_id}",
            headers=headers,
            json=_active_payload(raw="active化2"),
        )
        assert activated2.status_code == 200, activated2.text
        # paused行のresumeは6件目として422
        resumed = await api_client.post(
            f"/v1/intents/{first_id}/resume", headers=headers
        )
        assert resumed.status_code == 422, resumed.text
        assert resumed.json()["error"]["code"] == "ACTIVE_INTENT_LIMIT"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-4c 期限切れactive残留行は計上から除外 ---


async def test_6_active_limit_excludes_expired_rows(api_client, db_engine):
    subject = _unique_subject("exp")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        ids = []
        for i in range(5):
            resp = await _post_active(api_client, headers, f"e{i}")
            assert resp.status_code == 201, (i, resp.text)
            ids.append(resp.json()["intent"]["id"])
        exceeded = await _post_active(api_client, headers, "6件目")
        assert exceeded.status_code == 422, exceeded.text
        # 1件を期限切れへ(DB直接UPDATE — sweeperはM3-3のため残留する行)
        async with db_engine.begin() as conn:
            await conn.execute(
                text("UPDATE intents SET expires_at = :past WHERE id = :i"),
                {"past": SystemClock().now() - timedelta(hours=1), "i": ids[0]},
            )
        # 期限切れ行は計上から除外→6件目が受理(design §2.3告白2)
        after = await _post_active(api_client, headers, "期限切れ後")
        assert after.status_code == 201, after.text
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-5 resume連打の更新カウント ---


async def test_7_resume_counts_toward_update_limit(api_client, db_engine):
    subject = _unique_subject("res")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        created = await _post_active(api_client, headers, "resume対象")
        assert created.status_code == 201, created.text
        intent_id = created.json()["intent"]["id"]
        for i in range(6):
            paused = await api_client.post(
                f"/v1/intents/{intent_id}/pause", headers=headers
            )
            assert paused.status_code == 200, (i, paused.text)
            resumed = await api_client.post(
                f"/v1/intents/{intent_id}/resume", headers=headers
            )
            assert resumed.status_code == 200, (i, resumed.text)
        # 7回目のresumeは429(pauseは対象外・更新6回/時)
        paused = await api_client.post(
            f"/v1/intents/{intent_id}/pause", headers=headers
        )
        assert paused.status_code == 200, paused.text
        exceeded = await api_client.post(
            f"/v1/intents/{intent_id}/resume", headers=headers
        )
        assert exceeded.status_code == 429, exceeded.text
        assert exceeded.json()["error"]["code"] == "RATE_LIMITED"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-6 auth/token 60req/分(provider+subject単位) ---


async def test_8_auth_token_rate_limit(api_client, db_engine):
    subject = _unique_subject("tok")
    user_id = None
    try:
        idp_token = await _cli_idp_token("google", subject)
        for i in range(60):
            resp = await api_client.post(
                "/v1/auth/token",
                json={"provider": "google", "idp_token": idp_token},
            )
            assert resp.status_code == 200, (i, resp.text)
        exceeded = await api_client.post(
            "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
        )
        assert exceeded.status_code == 429, exceeded.text
        assert exceeded.json()["error"]["code"] == "RATE_LIMITED"
        # 無効idp_tokenの連打は401のまま(IdP検証後INCRのため — design §2.5)
        invalid = await api_client.post(
            "/v1/auth/token", json={"provider": "google", "idp_token": "garbage"}
        )
        assert invalid.status_code == 401, invalid.text
        assert invalid.json()["error"]["code"] == "INVALID_IDP_TOKEN"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-7 /health は対象外 ---


async def test_9_health_is_not_rate_limited(api_client):
    for i in range(65):
        resp = await api_client.get("/health")
        assert resp.status_code == 200, i
