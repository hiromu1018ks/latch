"""sessionsの実DB・/metricsの実HTTP検証(M4 ws-4・integration・test-ci)。

compose常設api+DBに対して実行する。ユーザーは実登録経路(POST /v1/users)
で作る(既存integration試験test_users_api.pyと同一作法・ヘルパーは重複を
嫌わず明示的に書く)。subjectは実行ごとにユニーク・試験が投入した行のみ
削除する(TRUNCATEは書かない — §2.2(c))。sessionsはFKなし独立表のため
users削除後も残る — user_idで絞って掃除する。
"""

import asyncio
import sys
import uuid as uuid_mod

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

API = "http://127.0.0.1:8000"


def _unique_subject() -> str:
    return f"obs-sess-{uuid_mod.uuid4().hex[:10]}"


async def _cli_idp_token(provider: str, subject: str) -> str:
    """内部ツール(python -m latch.auth)でテストユーザーJWTを発行(10 第1節)。"""
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


async def _register(api_client, provider: str, subject: str) -> tuple[str, str]:
    """POST /v1/auth/token→POST /v1/usersで登録し(access_token, user_id)を返す。"""
    idp_token = await _cli_idp_token(provider, subject)
    body = await api_client.post(
        "/v1/auth/token", json={"provider": provider, "idp_token": idp_token}
    )
    assert body.status_code == 200, body.text
    headers = {"Authorization": f"Bearer {body.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={
            "display_name": "観測試験太郎",
            "birth_date": "1990-04-01",
            "profile": {"bio": "obs"},
        },
    )
    assert created.status_code == 201, created.text
    return body.json()["access_token"], created.json()["user"]["id"]


async def _cleanup(db_engine, subject: str) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM sessions WHERE user_id IN "
                "(SELECT id FROM users WHERE auth_subject = :s)"
            ),
            {"s": subject},
        )
        await conn.execute(
            text("DELETE FROM users WHERE auth_subject = :s"), {"s": subject}
        )


async def test_post_sessions_inserts_row(api_client, db_engine):
    """POST /v1/sessions(204)→sessionsへ1行・user_id一致・列は3列のみ。"""
    provider, subject = "google", _unique_subject()
    try:
        token, user_id = await _register(api_client, provider, subject)
        resp = await api_client.post(
            "/v1/sessions", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 204, resp.text
        async with db_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, user_id, created_at FROM sessions "
                        "WHERE user_id = CAST(:uid AS uuid)"
                    ),
                    {"uid": user_id},
                )
            ).all()
            cols = (
                (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name='sessions' ORDER BY ordinal_position"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert str(rows[0][1]) == user_id  # users.idと一致
        assert rows[0][2] is not None  # created_at記録
        assert cols == ["id", "user_id", "created_at"]  # 3列のみ=Intent文言を運ばない
    finally:
        await _cleanup(db_engine, subject)


async def test_post_sessions_requires_auth(api_client, db_engine):
    """Authorizationなしで401 UNAUTHENTICATED。"""
    resp = await api_client.post(f"{API}/v1/sessions")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_metrics_endpoint_returns_text_plain(api_client):
    """GET /metricsが200・text/plain開始・latch_http_requests_totalを含む。"""
    resp = await api_client.get(f"{API}/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "latch_http_requests_total" in resp.text


async def test_sessions_body_not_empty_rejected(api_client, db_engine):
    """bodyつきPOSTは422 VALIDATION_ERROR(05 §19のbody空契約)。"""
    provider, subject = "google", _unique_subject()
    try:
        token, _ = await _register(api_client, provider, subject)
        resp = await api_client.post(
            "/v1/sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"note": "x"},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    finally:
        await _cleanup(db_engine, subject)
