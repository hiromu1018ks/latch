"""RateLimiter: 上限境界・INCR先行・503ラップ・上限差し替え・種別分離。"""

import uuid
from datetime import UTC, datetime

import fakeredis.aioredis
import pytest

from latch.core.clock import FakeClock
from latch.ratelimit import RateLimiter, RateLimits
from latch.ratelimit.errors import RateLimitDependencyError, RateLimitedError
from latch.ratelimit.store import RateLimitStore

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-28 21:00
UID = uuid.UUID("00000000-0000-4000-8000-00000000000a")
IID = uuid.UUID("00000000-0000-4000-8000-00000000000b")


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _limiter(redis, clock, **overrides) -> RateLimiter:
    limits = RateLimits(**overrides) if overrides else RateLimits()
    return RateLimiter(store=RateLimitStore(redis), clock=clock, limits=limits)


async def test_api_allows_60th_and_rejects_61st(redis, clock):
    limiter = _limiter(redis, clock)
    for _ in range(60):
        await limiter.check_api(user_key=str(UID))
    with pytest.raises(RateLimitedError) as ei:
        await limiter.check_api(user_key=str(UID))
    assert ei.value.http_status == 429
    assert ei.value.code == "RATE_LIMITED"


async def test_rejected_request_is_still_counted(redis, clock):
    """INCR先行(design §2.4告白4): 429を返したリクエストもカウント済み。"""
    limiter = _limiter(redis, clock, api_per_min=2)
    await limiter.check_api(user_key=str(UID))
    await limiter.check_api(user_key=str(UID))
    with pytest.raises(RateLimitedError):
        await limiter.check_api(user_key=str(UID))
    with pytest.raises(RateLimitedError):
        await limiter.check_api(user_key=str(UID))  # 連打はすべて429
    key = (await redis.keys("rl:api:*"))[0]
    assert await redis.get(key) == "4"  # 429分も加算されている


async def test_failed_create_still_consumes_count(redis, clock):
    """422で失敗する作成もカウントを消費(INCR先行 — design §2.4)。"""
    limiter = _limiter(redis, clock, create_per_day=1)
    await limiter.check_create(user_id=UID)
    with pytest.raises(RateLimitedError):
        await limiter.check_create(user_id=UID)


async def test_counter_kinds_are_separated(redis, clock):
    limiter = _limiter(redis, clock, api_per_min=1, create_per_day=1)
    await limiter.check_api(user_key=str(UID))  # api種で上限到達
    with pytest.raises(RateLimitedError):
        await limiter.check_api(user_key=str(UID))
    await limiter.check_create(user_id=UID)  # 作成種は無関係
    await limiter.check_update(intent_id=IID)  # 更新種も無関係


async def test_users_are_separated(redis, clock):
    limiter = _limiter(redis, clock, api_per_min=1)
    other = uuid.UUID("00000000-0000-4000-8000-00000000000c")
    await limiter.check_api(user_key=str(UID))
    with pytest.raises(RateLimitedError):
        await limiter.check_api(user_key=str(UID))
    await limiter.check_api(user_key=str(other))  # 別ユーザーは受理


async def test_auth_check_hashes_subject_in_key(redis, clock):
    limiter = _limiter(redis, clock)
    await limiter.check_auth(provider="google", subject="sub@example.com")
    keys = await redis.keys("rl:auth:*")
    assert len(keys) == 1
    assert "sub@example.com" not in keys[0]  # PII不混入(design §2.5)


async def test_redis_failure_is_wrapped_as_503(clock):
    """fail-closed(design §2.6): Redis断絶は503へ包む。"""

    class BrokenRedis:
        def pipeline(self):
            raise ConnectionError("redis down")

    limiter = RateLimiter(
        store=RateLimitStore(BrokenRedis()),  # type: ignore[arg-type]
        clock=clock,
        limits=RateLimits(),
    )
    with pytest.raises(RateLimitDependencyError) as ei:
        await limiter.check_api(user_key=str(UID))
    assert ei.value.http_status == 503
    assert ei.value.code == "DEPENDENCY_UNAVAILABLE"


async def test_limit_override_via_limits(redis, clock):
    limiter = _limiter(redis, clock, update_per_hour=3)
    for _ in range(3):
        await limiter.check_update(intent_id=IID)
    with pytest.raises(RateLimitedError):
        await limiter.check_update(intent_id=IID)


def test_active_limit_property(redis, clock):
    """serviceは limiter.active_limit で422判定の閾値を知る(design §2.3)。"""
    limiter = _limiter(redis, clock)
    assert limiter.active_limit == 5
    assert _limiter(redis, clock, active_intents=2).active_limit == 2
