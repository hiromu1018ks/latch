"""tokenユースケース: user応答分岐・claim連鎖・IdP失敗・依存障害503(design §4.1-3)。"""

import uuid
from datetime import UTC, datetime

import fakeredis.aioredis
import jwt as pyjwt
import pytest

from latch.auth.errors import DependencyUnavailableError, InvalidIdpTokenError
from latch.auth.idp import IdPVerifier, IdPVerifyConfig
from latch.auth.service import AuthService, make_user_lookup
from latch.auth.sessions import SessionStore
from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.auth.tokens import verify_access_token
from latch.core.clock import FakeClock
from latch.ratelimit.errors import RateLimitedError
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
SETTINGS = Settings(app_env="ci")
ISSUER_G = SETTINGS.auth_idp_issuer_google
AUDIENCE = SETTINGS.auth_idp_audience_google
SECRET = "unit-test-access-secret-0123456789abcdef"
USER_ID = uuid.uuid4()


async def _none_lookup(provider: str, subject: str) -> uuid.UUID | None:
    return None


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _idp() -> IdPVerifier:
    return IdPVerifier(
        configs={
            "google": IdPVerifyConfig(issuer=ISSUER_G, audience=AUDIENCE),
            "apple": IdPVerifyConfig(
                issuer=SETTINGS.auth_idp_issuer_apple,
                audience=SETTINGS.auth_idp_audience_apple,
            ),
        }
    )


def _idp_token(subject: str = "sub-1", provider: str = "google") -> str:
    issuer = ISSUER_G if provider == "google" else SETTINGS.auth_idp_issuer_apple
    payload = {
        "iss": issuer,
        "aud": AUDIENCE,
        "sub": subject,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 3600,
    }
    return pyjwt.encode(
        payload, load_private_key(), algorithm="RS256", headers={"kid": DEFAULT_KID}
    )


def _service(clock, redis, *, lookup=None, idp=None, limiter=None) -> AuthService:
    return AuthService(
        clock=clock,
        secret=SECRET,
        sessions=SessionStore(redis),
        idp=idp if idp is not None else _idp(),
        user_lookup=lookup if lookup is not None else _none_lookup,
        limiter=limiter,
    )


async def test_token_without_user_returns_null_and_incomplete(redis, clock):
    # §1.3-3/4: User行なし → id=null・profile_complete=false(契約上正当な初回登録待ち)
    result = await _service(clock, redis).token(
        provider="google", idp_token=_idp_token("sub-new")
    )
    assert result.user_id is None
    assert result.profile_complete is False
    assert result.token_type == "Bearer"
    assert result.expires_in == 3600
    assert result.refresh_token


async def test_token_with_user_returns_id_and_complete(redis, clock):
    async def lookup(provider: str, subject: str) -> uuid.UUID | None:
        assert (provider, subject) == ("google", "sub-1")
        return USER_ID

    result = await _service(clock, redis, lookup=lookup).token(
        provider="google", idp_token=_idp_token("sub-1")
    )
    assert result.user_id == USER_ID
    assert result.profile_complete is True


async def test_token_uses_settings_default_idp_config(redis, clock):
    # Settings既定issuer/audience(テストIdP値)と同梱JWKSが最初から噛み合っていること
    result = await _service(clock, redis).token(
        provider="apple", idp_token=_idp_token("a-1", "apple")
    )
    assert result.user_id is None


async def test_access_token_carries_provider_subject_sid(redis, clock):
    result = await _service(clock, redis).token(
        provider="google", idp_token=_idp_token("sub-1")
    )
    claims = verify_access_token(clock=clock, secret=SECRET, token=result.access_token)
    assert claims.auth_provider == "google"
    assert claims.auth_subject == "sub-1"
    assert claims.sid  # リフレッシュ族id(logoutが族を特定する)


async def test_token_invalid_idp_token_is_401(redis, clock):
    with pytest.raises(InvalidIdpTokenError):
        await _service(clock, redis).token(provider="google", idp_token="garbage")


async def test_token_lookup_failure_is_503(redis, clock):
    async def broken(provider: str, subject: str) -> uuid.UUID | None:
        raise RuntimeError("db down")

    with pytest.raises(DependencyUnavailableError):
        await _service(clock, redis, lookup=broken).token(
            provider="google", idp_token=_idp_token()
        )


async def test_token_redis_failure_is_503(clock):
    import redis as redis_lib

    class _BrokenSessions:
        async def create_refresh(self, *, provider: str, subject: str):
            raise redis_lib.exceptions.ConnectionError("redis down")

    svc = AuthService(
        clock=clock,
        secret=SECRET,
        sessions=_BrokenSessions(),  # type: ignore[arg-type]
        idp=_idp(),
        user_lookup=_none_lookup,
    )
    with pytest.raises(DependencyUnavailableError):
        await svc.token(provider="google", idp_token=_idp_token())


# --- make_user_lookup の行値変換(スーパーバイザー検証test-ci失敗1の回帰)---


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, stmt, params):
        return _FakeResult(self._row)


class _FakeEngine:
    """asyncpgの行値型を模倣する最小engineスタブ(async_engine_from_config不要)。"""

    def __init__(self, row):
        self._row = row

    def connect(self):
        return _FakeConn(self._row)


async def test_user_lookup_returns_uuid_instance_as_is():
    # asyncpgはuuid列をUUID「インスタンス」で返す(uuid.UUID(row[0]) は
    # AttributeError('UUID' object has no attribute 'replace') になる — 503の原因)
    uid = uuid.uuid4()
    lookup = make_user_lookup(_FakeEngine(row=(uid,)))  # type: ignore[arg-type]
    assert await lookup("google", "sub-1") == uid


async def test_user_lookup_builds_uuid_from_string_row_value():
    uid = uuid.uuid4()
    lookup = make_user_lookup(_FakeEngine(row=(str(uid),)))  # type: ignore[arg-type]
    assert await lookup("google", "sub-1") == uid


async def test_user_lookup_returns_none_when_no_row():
    lookup = make_user_lookup(_FakeEngine(row=None))  # type: ignore[arg-type]
    assert await lookup("google", "sub-1") is None


# --- M1 ws-4: token発行の429フック(design §2.5)---


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


async def test_token_counts_provider_subject_after_idp_verify(redis, clock):
    limiter = _StubAuthLimiter()
    await _service(clock, redis, limiter=limiter).token(
        provider="google", idp_token=_idp_token("sub-1")
    )
    assert limiter.calls == [("google", "sub-1")]


async def test_token_61st_is_rate_limited(redis, clock):
    limiter = _StubAuthLimiter(allowed=1)
    svc = _service(clock, redis, limiter=limiter)
    await svc.token(provider="google", idp_token=_idp_token("sub-1"))
    with pytest.raises(RateLimitedError) as ei:
        await svc.token(provider="google", idp_token=_idp_token("sub-1"))
    assert ei.value.http_status == 429
    assert ei.value.code == "RATE_LIMITED"


async def test_token_401_precedes_rate_limit(redis, clock):
    """無効idp_tokenはIdP検証で401(INCRされない — design §2.5の401優先)。"""
    limiter = _StubAuthLimiter(allowed=0)
    with pytest.raises(InvalidIdpTokenError):
        await _service(clock, redis, limiter=limiter).token(
            provider="google", idp_token="garbage"
        )
    assert limiter.calls == []


async def test_token_no_limiter_keeps_behavior(redis, clock):
    """limiter=None(既定)は既存挙動のまま(M0回帰 — design §2.8)。"""
    result = await _service(clock, redis).token(
        provider="google", idp_token=_idp_token("sub-1")
    )
    assert result.token_type == "Bearer"
