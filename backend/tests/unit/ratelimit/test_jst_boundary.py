"""JSTバケット境界: 日次・時・分の切替=リセットのClock再現(design §2.2・確定値8)。

日付は clock.jst_date()、時・分は clock.now().astimezone(JST) から導出することを
FakeClockの固定時刻で検証する(UTC文字列をキーに混ぜない)。
"""

import uuid
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest

from latch.core.clock import FakeClock
from latch.ratelimit import RateLimiter, RateLimits
from latch.ratelimit.errors import RateLimitedError
from latch.ratelimit.store import RateLimitStore

UID = uuid.UUID("00000000-0000-4000-8000-00000000000a")


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


def _limiter(redis, clock, **overrides) -> RateLimiter:
    return RateLimiter(
        store=RateLimitStore(redis), clock=clock, limits=RateLimits(**overrides)
    )


async def test_daily_bucket_rolls_at_jst_midnight(redis):
    # JST 2026-09-28 23:59 → 00:00 で日付キーが切り替わる
    clock = FakeClock(datetime(2026, 9, 28, 14, 59, 0, tzinfo=UTC))
    limiter = _limiter(redis, clock, create_per_day=1)
    await limiter.check_create(user_id=UID)  # 当日1件目OK
    with pytest.raises(RateLimitedError):
        await limiter.check_create(user_id=UID)  # 同日2件目429
    clock.advance(timedelta(minutes=1))  # JST 0時突破
    await limiter.check_create(user_id=UID)  # 新しい日付キー→受理
    keys = sorted(await redis.keys("rl:create:*"))
    assert len(keys) == 2
    assert "20260928" in keys[0] and "20260929" in keys[1]  # JST暦日付


async def test_hourly_bucket_rolls_on_the_hour(redis):
    # JST 21:59 → 22:00 で時キーが切り替わる
    clock = FakeClock(datetime(2026, 9, 28, 12, 59, 0, tzinfo=UTC))
    limiter = _limiter(redis, clock, update_per_hour=1)
    await limiter.check_update(intent_id=UID)
    with pytest.raises(RateLimitedError):
        await limiter.check_update(intent_id=UID)
    clock.advance(timedelta(minutes=1))
    await limiter.check_update(intent_id=UID)
    keys = sorted(await redis.keys("rl:update:*"))
    assert "2026092821" in keys[0] and "2026092822" in keys[1]


async def test_minute_bucket_rolls_each_minute(redis):
    # JST 21:00:59 → 21:01:00 で分キーが切り替わる
    clock = FakeClock(datetime(2026, 9, 28, 12, 0, 59, tzinfo=UTC))
    limiter = _limiter(redis, clock, api_per_min=1)
    await limiter.check_api(user_key=str(UID))
    with pytest.raises(RateLimitedError):
        await limiter.check_api(user_key=str(UID))
    clock.advance(timedelta(seconds=1))
    await limiter.check_api(user_key=str(UID))
    keys = sorted(await redis.keys("rl:api:*"))
    assert "202609282100" in keys[0] and "202609282101" in keys[1]


async def test_date_uses_jst_calendar_not_utc(redis):
    # UTC 2026-09-28 15:00 = JST 2026-09-29 00:00(UTC日付とは異なる暦日)
    clock = FakeClock(datetime(2026, 9, 28, 15, 0, 0, tzinfo=UTC))
    limiter = _limiter(redis, clock, create_per_day=1)
    await limiter.check_create(user_id=UID)
    keys = await redis.keys("rl:create:*")
    assert "20260929" in keys[0]  # jst_date()由来(20260928でない)
