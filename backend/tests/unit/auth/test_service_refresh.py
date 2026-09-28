"""refresh/logoutユースケース: 回転応答・族失効連鎖・logout一連(design §4.1-4)。

test_service_token.py と同じ構成のヘルパー群を使う(ws-2の慣例: conftestなし)。
"""

from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import jwt as pyjwt
import pytest

from latch.auth.errors import InvalidRefreshTokenError, UnauthenticatedError
from latch.auth.idp import IdPVerifier, IdPVerifyConfig
from latch.auth.service import AuthService
from latch.auth.sessions import SessionStore
from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.core.clock import FakeClock
from latch.ratelimit.errors import RateLimitedError
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
SETTINGS = Settings(app_env="ci")
ISSUER_G = SETTINGS.auth_idp_issuer_google
AUDIENCE = SETTINGS.auth_idp_audience_google
SECRET = "unit-test-access-secret-0123456789abcdef"


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


def _idp_token(subject: str = "sub-1") -> str:
    payload = {
        "iss": ISSUER_G,
        "aud": AUDIENCE,
        "sub": subject,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 3600,
    }
    return pyjwt.encode(
        payload, load_private_key(), algorithm="RS256", headers={"kid": DEFAULT_KID}
    )


def _service(clock, redis, *, limiter=None) -> AuthService:
    return AuthService(
        clock=clock,
        secret=SECRET,
        sessions=SessionStore(redis),
        idp=IdPVerifier(
            configs={"google": IdPVerifyConfig(issuer=ISSUER_G, audience=AUDIENCE)}
        ),
        user_lookup=_none_lookup,
        limiter=limiter,
    )


async def _token_pair(svc: AuthService) -> tuple[str, str]:
    result = await svc.token(provider="google", idp_token=_idp_token())
    return result.access_token, result.refresh_token


async def test_refresh_returns_rotated_pair(redis, clock):
    svc = _service(clock, redis)
    access, refresh = await _token_pair(svc)
    rotated = await svc.refresh(refresh_token=refresh)
    assert rotated.token_type == "Bearer"
    assert rotated.expires_in == 3600
    assert rotated.access_token
    assert rotated.refresh_token != refresh  # 回転(旧トークンは無効化済)


async def test_refresh_reuse_revokes_family_chain(redis, clock):
    # Review Focus #2(service経由): 旧再利用→401・回転後の新トークンも401
    svc = _service(clock, redis)
    _, refresh = await _token_pair(svc)
    rotated = await svc.refresh(refresh_token=refresh)
    with pytest.raises(InvalidRefreshTokenError):
        await svc.refresh(refresh_token=refresh)
    with pytest.raises(InvalidRefreshTokenError):
        await svc.refresh(refresh_token=rotated.refresh_token)


async def test_refresh_unknown_token_rejected(redis, clock):
    svc = _service(clock, redis)
    with pytest.raises(InvalidRefreshTokenError):
        await svc.refresh(refresh_token="unknown-token")


async def test_logout_revokes_access_and_family(redis, clock):
    svc = _service(clock, redis)
    access, refresh = await _token_pair(svc)
    claims = await svc.authenticate(token=access)
    assert claims.auth_subject == "sub-1"  # logout前は有効
    await svc.logout(claims=claims)
    # 同JWTの再提示 → 401 UNAUTHENTICATED(失効リスト掲載)
    with pytest.raises(UnauthenticatedError):
        await svc.authenticate(token=access)
    # 当族のリフレッシュトークンも無効
    with pytest.raises(InvalidRefreshTokenError):
        await svc.refresh(refresh_token=refresh)


async def test_authenticate_expired_token(redis, clock):
    # design §4.2後段(FakeClock再現): 1時間超過で401
    svc = _service(clock, redis)
    access, _ = await _token_pair(svc)
    clock.advance(timedelta(seconds=3601))
    with pytest.raises(UnauthenticatedError):
        await svc.authenticate(token=access)


async def test_authenticate_garbage_token(redis, clock):
    svc = _service(clock, redis)
    with pytest.raises(UnauthenticatedError):
        await svc.authenticate(token="not-a-jwt")


# --- M1 ws-4: refreshの429フック(design §2.5)---


class _StubAuthLimiter:
    """check_authのスタブ(呼び出し記録・任意回数でRateLimitedError)。"""

    def __init__(self, *, allowed: int = 60):
        self.allowed = allowed
        self.calls = []

    async def check_auth(self, *, provider: str, subject: str):
        self.calls.append((provider, subject))
        self.allowed -= 1
        if self.allowed < 0:
            raise RateLimitedError("rate limit exceeded")


async def test_refresh_counts_after_rotation(redis, clock):
    """rotate(検証を兼ねる)の後にINCR — provider+subjectは回転結果由来。"""
    limiter = _StubAuthLimiter()
    issue_svc = _service(clock, redis)  # 発行側はlimiterなし
    _, refresh = await _token_pair(issue_svc)
    svc = _service(clock, redis, limiter=limiter)
    await svc.refresh(refresh_token=refresh)
    assert limiter.calls == [("google", "sub-1")]


async def test_refresh_invalid_token_is_401_not_counted(redis, clock):
    limiter = _StubAuthLimiter(allowed=0)
    svc = _service(clock, redis, limiter=limiter)
    with pytest.raises(InvalidRefreshTokenError):
        await svc.refresh(refresh_token="unknown-token-value")
    assert limiter.calls == []
