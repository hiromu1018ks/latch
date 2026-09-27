"""認証3エンドポイント+JWKS検証経路のci環境実証(design §4.2。G0の主要証拠)。

実HTTP(compose api=127.0.0.1:8000)・実Redis・実DB。実行は make test-ci
(ws-4並走中は作成のみ — 計画書§0のSTATUS運用ルール。スーパーバイザーが検証時に実行)。
subjectは実行ごとにユニークな値(uuid接尾辞)を用い、User行は試験内でDELETE
(共有ci-dbの汚染回避)。Redis鍵はTTL付きで自動消滅するため追加掃除はしない。
"""

import asyncio
import hashlib
import sys
import uuid as uuid_mod
from datetime import UTC, date, datetime, timedelta

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from latch.auth.service import build_auth_service
from latch.auth.tools import generate_keypair, issue_idp_token
from latch.core.clock import FakeClock
from latch.main import create_app
from latch.settings import Settings

pytestmark = pytest.mark.integration


def _unique_subject(prefix: str) -> str:
    return f"ws3-{prefix}-{uuid_mod.uuid4().hex[:12]}"


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


def _decode_unverified(token: str) -> dict:
    return pyjwt.decode(token, options={"verify_signature": False})


async def _exchange(api_client, provider: str, subject: str) -> dict:
    idp_token = await _cli_idp_token(provider, subject)
    resp = await api_client.post(
        "/v1/auth/token", json={"provider": provider, "idp_token": idp_token}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_1_token_exchange_via_cli_for_both_providers(api_client):
    """§4.2-1: ツール発行IdPトークン→200・Bearer・3600・user=null/false"""
    for provider in ("google", "apple"):
        body = await _exchange(api_client, provider, _unique_subject(provider))
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 3600
        assert body["refresh_token"]
        assert body["user"] == {"id": None, "profile_complete": False}


async def test_2_token_with_inserted_user_row(api_client, db_engine):
    """§4.2-2: User行INSERT→user.id=<uuid>・profile_complete=true"""
    provider, subject = "google", _unique_subject("u")
    ts = datetime(2026, 9, 27, 0, 0, 0, tzinfo=UTC)  # 固定リテラル時刻(ws-1試験規約)
    async with db_engine.begin() as conn:
        user_id = await conn.scalar(
            text("""
                INSERT INTO users
                    (display_name, birth_date, auth_provider, auth_subject,
                     created_at, updated_at)
                VALUES (:dn, :bd, :p, :s, :ts, :ts)
                RETURNING id
            """),
            {
                "dn": "ws3試験ユーザー",
                "bd": date(2000, 1, 1),
                "p": provider,
                "s": subject,
                "ts": ts,
            },
        )
    try:
        body = await _exchange(api_client, provider, subject)
        assert body["user"]["id"] == str(user_id)
        assert body["user"]["profile_complete"] is True
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM users WHERE id = :id"), {"id": user_id}
            )


async def test_3_wrong_key_idp_token_rejected(api_client, tmp_path):
    """§4.2-3: gen-keypairの別鍵で署名→401 INVALID_IDP_TOKEN(envelope)"""
    generate_keypair(out_dir=tmp_path)
    token = issue_idp_token(
        provider="google",
        subject=_unique_subject("k"),
        private_key_pem=(tmp_path / "idp_test_private.pem").read_bytes(),
    )
    resp = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": token}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_IDP_TOKEN"


async def test_4_refresh_rotation_and_reuse_revokes_family(api_client):
    """§4.2-4: 旧refresh再利用→401・回転後の新refreshも同一族なので401"""
    body = await _exchange(api_client, "google", _unique_subject("r"))
    old_refresh = body["refresh_token"]
    rotated = await api_client.post(
        "/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert rotated.status_code == 200, rotated.text
    new_refresh = rotated.json()["refresh_token"]
    reuse = await api_client.post(
        "/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"
    after = await api_client.post(
        "/v1/auth/refresh", json={"refresh_token": new_refresh}
    )
    assert after.status_code == 401


async def test_5_logout_revokes_access_and_family(api_client):
    """§4.2-5: 204→同JWT再利用401→当族refresh 401"""
    body = await _exchange(api_client, "google", _unique_subject("l"))
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    resp = await api_client.post("/v1/auth/logout", headers=headers)
    assert resp.status_code == 204
    again = await api_client.post("/v1/auth/logout", headers=headers)
    assert again.status_code == 401
    assert again.json()["error"]["code"] == "UNAUTHENTICATED"
    rotated = await api_client.post(
        "/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert rotated.status_code == 401


async def test_6_unknown_refresh_token_rejected(api_client):
    """§4.2-6: 不正なrefresh_token文字列→401"""
    resp = await api_client.post(
        "/v1/auth/refresh", json={"refresh_token": "unknown-token-value"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


async def test_7_redis_keys_directly(api_client, redis_client):
    """§4.2-7: revoked鍵の存在とTTL上限(≤3600s)・族鍵・rt鍵の削除"""
    body = await _exchange(api_client, "google", _unique_subject("rv"))
    access, refresh = body["access_token"], body["refresh_token"]
    claims = _decode_unverified(access)
    jti, sid = claims["jti"], claims["sid"]
    assert not await redis_client.exists(f"auth:revoked:{jti}")
    resp = await api_client.post(
        "/v1/auth/logout", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 204
    assert await redis_client.exists(f"auth:revoked:{jti}")
    ttl = await redis_client.ttl(f"auth:revoked:{jti}")
    assert 0 < ttl <= 3600  # TTL上限=残り有効期限
    assert not await redis_client.exists(f"auth:family:{sid}")  # 族索引削除
    sha = hashlib.sha256(refresh.encode()).hexdigest()
    assert not await redis_client.exists(f"auth:rt:{sha}")  # 族メンバーのrt鍵削除


async def test_8_expired_access_token_asgi_real_redis(redis_client):
    """design §4.2後段: ASGI+実Redis+FakeClockで期限切れを再現"""
    now = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now)

    async def _lookup(provider: str, subject: str):
        return None

    settings = Settings(app_env="ci")
    svc = build_auth_service(
        clock=clock, settings=settings, redis_client=redis_client, user_lookup=_lookup
    )
    app = create_app(clock=clock, settings=settings, auth_service=svc)
    idp_token = issue_idp_token(
        provider="google", subject="ws3-exp", iat=int(now.timestamp())
    )
    result = await svc.token(provider="google", idp_token=idp_token)
    headers = {"Authorization": f"Bearer {result.access_token}"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        ok = await client.post("/v1/auth/logout", headers=headers)
        assert ok.status_code == 204  # 期限内は通る(実Redisの失効照会経路も踏む)
    clock.advance(timedelta(seconds=3601))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        expired = await client.post("/v1/auth/logout", headers=headers)
        assert expired.status_code == 401  # FakeClock.advanceで期限切れ再現
