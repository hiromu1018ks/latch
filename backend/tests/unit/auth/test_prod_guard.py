"""build_auth_serviceのprod拒否とci/staging既定受理(design §2.5-3・§4.1-8)。"""

import base64
import json
from datetime import UTC, datetime

import fakeredis.aioredis
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from latch.auth.service import build_auth_service
from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.core.clock import FakeClock
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
REAL_SECRET = "prod-like-secret-0123456789abcdef0123456789"
REAL_JWKS = "https://real-idp.example.com/jwks.json"
REAL_ISSUER = "https://accounts.example.com"
REAL_AUDIENCE = "latch-prod-app"

AUTH_ENV_VARS = (
    "LATCH_REDIS_URL",
    "LATCH_AUTH_ACCESS_SECRET",
    "LATCH_AUTH_IDP_JWKS_URL_GOOGLE",
    "LATCH_AUTH_IDP_JWKS_URL_APPLE",
    "LATCH_AUTH_IDP_ISSUER_GOOGLE",
    "LATCH_AUTH_IDP_ISSUER_APPLE",
    "LATCH_AUTH_IDP_AUDIENCE_GOOGLE",
    "LATCH_AUTH_IDP_AUDIENCE_APPLE",
)


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


def _clean(monkeypatch, **overrides) -> Settings:
    for var in AUTH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(**overrides)


def _prod(**overrides) -> Settings:
    base = dict(
        app_env="prod",
        auth_access_secret=REAL_SECRET,
        auth_idp_jwks_url_google=REAL_JWKS,
        auth_idp_jwks_url_apple=REAL_JWKS,
        auth_idp_issuer_google=REAL_ISSUER,
        auth_idp_issuer_apple=REAL_ISSUER,
        auth_idp_audience_google=REAL_AUDIENCE,
        auth_idp_audience_apple=REAL_AUDIENCE,
    )
    base.update(overrides)
    return Settings(**base)


async def test_ci_defaults_build_and_issue(clock, redis, monkeypatch):
    # Review Focus #5: ciは同梱JWKS+テストissuer/audience+テストsecretでそのまま動く
    settings = _clean(monkeypatch, app_env="ci")
    svc = build_auth_service(
        clock=clock, settings=settings, redis_client=redis, user_lookup=_none_lookup
    )
    payload = {
        "iss": "https://idp.ci.latch.test/google",
        "aud": "latch-test-app",
        "sub": "sub-ci",
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 3600,
    }
    idp_token = pyjwt.encode(
        payload, load_private_key(), algorithm="RS256", headers={"kid": DEFAULT_KID}
    )
    result = await svc.token(provider="google", idp_token=idp_token)
    assert result.token_type == "Bearer"
    assert result.user_id is None


async def test_staging_with_injected_pair_builds(clock, redis, tmp_path):
    # staging: gen-keypairで生成した専用ペアを注入(design §2.5-1)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def b64u(value: int) -> str:
        size = (value.bit_length() + 7) // 8
        return (
            base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()
        )

    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": DEFAULT_KID,
        "use": "sig",
        "alg": "RS256",
        "n": b64u(numbers.n),
        "e": b64u(numbers.e),
    }
    jwks_path = tmp_path / "idp_test_jwks.json"
    jwks_path.write_text(json.dumps({"keys": [jwk]}))
    settings = Settings(
        app_env="staging",
        auth_access_secret="staging-secret-0123456789abcdef",
        auth_idp_jwks_url_google=jwks_path.as_uri(),
        auth_idp_jwks_url_apple=jwks_path.as_uri(),
    )
    svc = build_auth_service(
        clock=clock, settings=settings, redis_client=redis, user_lookup=_none_lookup
    )
    assert svc is not None  # 構築成功(発行検証はtools試験が所有)


def test_prod_rejects_empty_secret(clock, redis):
    with pytest.raises(ValueError, match="auth_access_secret"):
        build_auth_service(
            clock=clock,
            settings=_prod(auth_access_secret=""),
            redis_client=redis,
            user_lookup=_none_lookup,
        )


def test_prod_rejects_test_secret(clock, redis):
    from latch.auth.testkeys import TEST_ACCESS_SECRET

    with pytest.raises(ValueError, match="auth_access_secret"):
        build_auth_service(
            clock=clock,
            settings=_prod(auth_access_secret=TEST_ACCESS_SECRET),
            redis_client=redis,
            user_lookup=_none_lookup,
        )


def test_prod_rejects_missing_jwks_url(clock, redis):
    with pytest.raises(ValueError, match="jwks_url"):
        build_auth_service(
            clock=clock,
            settings=_prod(auth_idp_jwks_url_google=""),
            redis_client=redis,
            user_lookup=_none_lookup,
        )


def test_prod_rejects_test_issuer(clock, redis):
    with pytest.raises(ValueError, match="issuer"):
        build_auth_service(
            clock=clock,
            settings=_prod(auth_idp_issuer_google="https://idp.ci.latch.test/google"),
            redis_client=redis,
            user_lookup=_none_lookup,
        )


def test_prod_rejects_test_audience(clock, redis):
    with pytest.raises(ValueError, match="audience"):
        build_auth_service(
            clock=clock,
            settings=_prod(auth_idp_audience_apple="latch-test-app"),
            redis_client=redis,
            user_lookup=_none_lookup,
        )


def test_prod_with_real_values_builds(clock, redis):
    svc = build_auth_service(
        clock=clock, settings=_prod(), redis_client=redis, user_lookup=_none_lookup
    )
    assert svc is not None
