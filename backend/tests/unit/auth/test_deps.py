"""require_authenticated: 401系バリエーション・ダミー保護ルート(design §4.1-6)。"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import fakeredis.aioredis
import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.service import build_auth_service
from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.auth.tokens import AccessTokenClaims
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
def app(clock, svc) -> FastAPI:
    a = create_app(clock=clock, settings=Settings(app_env="ci"), auth_service=svc)

    @a.get("/_test/protected")
    async def protected(
        claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    ) -> dict:
        return {
            "provider": claims.auth_provider,
            "subject": claims.auth_subject,
            "jti": claims.jti,
        }

    return a


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


async def _access(svc) -> str:
    result = await svc.token(provider="google", idp_token=_idp_token())
    return result.access_token


async def test_missing_authorization_header(client):
    resp = await client.get("/_test/protected")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_non_bearer_scheme(client, svc):
    access = await _access(svc)
    resp = await client.get(
        "/_test/protected", headers={"Authorization": f"Basic {access}"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_garbage_bearer_token(client):
    resp = await client.get(
        "/_test/protected", headers={"Authorization": "Bearer junk"}
    )
    assert resp.status_code == 401


async def test_valid_token_passes(client, svc):
    access = await _access(svc)
    resp = await client.get(
        "/_test/protected", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "google"
    assert body["subject"] == "sub-1"
    assert body["jti"]


async def test_tampered_token_rejected(client, svc):
    access = await _access(svc)
    tampered = access[:-4] + ("AAAA" if not access.endswith("AAAA") else "BBBB")
    resp = await client.get(
        "/_test/protected", headers={"Authorization": f"Bearer {tampered}"}
    )
    assert resp.status_code == 401


async def test_expired_token_rejected(client, svc, clock):
    access = await _access(svc)
    clock.advance(timedelta(seconds=3601))
    resp = await client.get(
        "/_test/protected", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 401


async def test_revoked_token_rejected(client, svc):
    access = await _access(svc)
    claims = await svc.authenticate(token=access)
    await svc.logout(claims=claims)
    resp = await client.get(
        "/_test/protected", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"
