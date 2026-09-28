"""api_rate_limited依存関数の分岐(未載荷スルー・user_idキー・anonフォールバック・503)。

design §2.4・計画Task 4。最終レビューが指摘した直接試験の欠落を埋める
(wiring試験は静的構成検査・integrationはuserキー/401経路のみのため)。
実RateLimiter(fakeredis)でキー形式まで検証する。
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from starlette.requests import Request

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.ratelimit import RateLimiter, RateLimits
from latch.ratelimit.deps import _anon_key, api_rate_limited
from latch.ratelimit.errors import RateLimitDependencyError, RateLimitedError
from latch.ratelimit.store import RateLimitStore

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-28 21:00
UID = uuid.UUID("00000000-0000-4000-8000-00000000000a")


def _claims(provider: str = "google", subject: str = "sub-1") -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider=provider,
        auth_subject=subject,
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW.replace(hour=13),
    )


def _app(**state_attrs) -> SimpleNamespace:
    """app.state相当を持つ最小のapp(request.app.state のみ使われる)。"""
    return SimpleNamespace(state=SimpleNamespace(**state_attrs))


def _request(app: SimpleNamespace) -> Request:
    return Request({"type": "http", "app": app})


def _limiter(redis) -> RateLimiter:
    return RateLimiter(
        store=RateLimitStore(redis), clock=FakeClock(NOW), limits=RateLimits()
    )


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


async def _ok_lookup(provider: str, subject: str) -> uuid.UUID | None:
    return UID


async def _none_lookup(provider: str, subject: str) -> uuid.UUID | None:
    return None


async def _broken_lookup(provider: str, subject: str) -> uuid.UUID | None:
    raise ConnectionError("db down")


async def test_limiter_not_loaded_passes_through(redis):
    """app.state.rate_limiter未載荷=無効(unit試験のASGITransportと同じ状態)。"""
    app = _app()  # rate_limiter未載荷
    returned = await api_rate_limited(_request(app), _claims())
    assert returned.auth_subject == "sub-1"  # claimsがそのまま返る
    assert await redis.keys("rl:*") == []  # INCRしていない


async def test_registered_user_counts_by_user_id(redis):
    app = _app(rate_limiter=_limiter(redis), user_lookup=_ok_lookup)
    await api_rate_limited(_request(app), _claims())
    assert await redis.keys(f"rl:api:{UID}:*") == [f"rl:api:{UID}:202609282100"]


async def test_unregistered_user_falls_back_to_anon_key(redis):
    app = _app(rate_limiter=_limiter(redis), user_lookup=_none_lookup)
    await api_rate_limited(_request(app), _claims())
    expected = f"rl:api:{_anon_key('google', 'sub-1')}:202609282100"
    assert await redis.keys("rl:api:*") == [expected]
    assert "sub-1" not in expected  # PII不混入(design §2.5)


async def test_missing_user_lookup_falls_back_to_anon_key(redis):
    """user_lookup未載荷でもanonキーで計上(計画Task 4)。"""
    app = _app(rate_limiter=_limiter(redis))  # user_lookup未載荷
    await api_rate_limited(_request(app), _claims())
    expected = f"rl:api:{_anon_key('google', 'sub-1')}:202609282100"
    assert await redis.keys("rl:api:*") == [expected]


async def test_lookup_failure_raises_503(redis):
    """user_lookupのDB障害は503へ包む(fail-closed — design §2.6)。"""
    app = _app(rate_limiter=_limiter(redis), user_lookup=_broken_lookup)
    with pytest.raises(RateLimitDependencyError) as ei:
        await api_rate_limited(_request(app), _claims())
    assert ei.value.http_status == 503
    assert ei.value.code == "DEPENDENCY_UNAVAILABLE"


async def test_61st_request_raises_429(redis):
    """60req/分の超過は依存関数からRateLimitedErrorとして出る(INCR先行)。"""
    app = _app(rate_limiter=_limiter(redis), user_lookup=_ok_lookup)
    request = _request(app)
    claims = _claims()
    for _ in range(60):
        await api_rate_limited(request, claims)
    with pytest.raises(RateLimitedError) as ei:
        await api_rate_limited(request, claims)
    assert ei.value.http_status == 429
    assert ei.value.code == "RATE_LIMITED"
