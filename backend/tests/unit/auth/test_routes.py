"""3エンドポイントのAPI挙動・エラーenvelope・400/422振り分け・ログ規約(design §4.1)。"""

import logging
from datetime import UTC, datetime

import fakeredis.aioredis
import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient

from latch.auth.service import build_auth_service
from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.core.clock import FakeClock
from latch.main import create_app
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


async def _none_lookup(provider: str, subject: str):
    return None


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def svc(clock, redis):
    return build_auth_service(
        clock=clock,
        settings=Settings(app_env="ci"),
        redis_client=redis,
        user_lookup=_none_lookup,
    )


@pytest.fixture
def app(clock, svc):
    return create_app(clock=clock, settings=Settings(app_env="ci"), auth_service=svc)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _idp_token(subject: str = "sub-1") -> str:
    s = Settings(app_env="ci")
    payload = {
        "iss": s.auth_idp_issuer_google,
        "aud": s.auth_idp_audience_google,
        "sub": subject,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 3600,
    }
    return pyjwt.encode(
        payload, load_private_key(), algorithm="RS256", headers={"kid": DEFAULT_KID}
    )


def _assert_envelope(body: dict, code: str) -> None:
    # 05 第5節 エラー形式: {"error": {code, message, details}}
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert body["error"]["details"] is None
    assert body["error"]["message"]


async def _token_pair(client, subject: str = "sub-1") -> dict:
    resp = await client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": _idp_token(subject)}
    )
    assert resp.status_code == 200
    return resp.json()


async def test_token_success_shape(client):
    body = await _token_pair(client)
    assert set(body) == {
        "access_token",
        "token_type",
        "expires_in",
        "refresh_token",
        "user",
    }
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["user"] == {"id": None, "profile_complete": False}


async def test_token_invalid_idp_token_envelope(client):
    resp = await client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": "garbage"}
    )
    assert resp.status_code == 401
    _assert_envelope(resp.json(), "INVALID_IDP_TOKEN")


async def test_refresh_success_shape_and_rotation(client):
    first = await _token_pair(client)
    resp = await client.post(
        "/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "refresh_token"}
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["refresh_token"] != first["refresh_token"]
    # 旧トークン再利用 → 401(回転の実効)
    reuse = await client.post(
        "/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert reuse.status_code == 401
    _assert_envelope(reuse.json(), "INVALID_REFRESH_TOKEN")


async def test_refresh_unknown_token(client):
    resp = await client.post("/v1/auth/refresh", json={"refresh_token": "unknown"})
    assert resp.status_code == 401
    _assert_envelope(resp.json(), "INVALID_REFRESH_TOKEN")


async def test_logout_returns_204_and_revokes(client):
    body = await _token_pair(client)
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    resp = await client.post("/v1/auth/logout", headers=headers)
    assert resp.status_code == 204
    assert resp.content == b""
    # 同JWTの再提示 → 401 UNAUTHENTICATED(失効リスト掲載)
    again = await client.post("/v1/auth/logout", headers=headers)
    assert again.status_code == 401
    _assert_envelope(again.json(), "UNAUTHENTICATED")
    # 当族のリフレッシュも無効
    rotated = await client.post(
        "/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert rotated.status_code == 401


async def test_logout_without_token(client):
    resp = await client.post("/v1/auth/logout")
    assert resp.status_code == 401
    _assert_envelope(resp.json(), "UNAUTHENTICATED")


async def test_invalid_provider_returns_422(client):
    resp = await client.post(
        "/v1/auth/token", json={"provider": "line", "idp_token": "x"}
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


async def test_missing_required_field_returns_422(client):
    resp = await client.post("/v1/auth/token", json={"provider": "google"})
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


async def test_malformed_json_returns_400(client):
    resp = await client.post(
        "/v1/auth/token",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    _assert_envelope(resp.json(), "MALFORMED_REQUEST")


async def test_logs_contain_no_token_or_subject(client, caplog):
    # Review Focus #4: ログはイベント名と結果/コードのみ(08 第2.4節)
    secret_subject = "secret-subject-42"
    with caplog.at_level(logging.INFO, logger="latch.auth"):
        resp = await client.post(
            "/v1/auth/token",
            json={"provider": "google", "idp_token": _idp_token(secret_subject)},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert secret_subject not in caplog.text
    assert body["access_token"] not in caplog.text
    assert body["refresh_token"] not in caplog.text
    assert body["access_token"].split(".")[1] not in caplog.text  # ペイロード断片も不出
    assert "auth.token ok" in caplog.text  # 成功ログは出る
