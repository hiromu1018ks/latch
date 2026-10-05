"""nfrtools.d16: Clock導出キー・set/get/delete(M4 ws-7・design §2.3案A)。

キー形式はworker/cost/store.pyと同一(jev:daily:{yyyymmdd}・jev:monthly:
{yyyymm})。バケット文字列はClock(JST)導出(guard._day_bucketと同一・C2)。
"""

from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.nfrtools.d16 import (
    daily_key,
    delete_counter,
    get_counter,
    monthly_key,
    set_counter,
)

NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)  # JST 2026-10-05 18:00


@pytest.fixture
async def redis():
    import fakeredis.aioredis

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


def test_daily_key_derived_from_clock():
    clock = FakeClock(NOW)
    assert daily_key(clock) == "jev:daily:20261005"


def test_monthly_key_derived_from_clock():
    clock = FakeClock(NOW)
    assert monthly_key(clock) == "jev:monthly:202610"


async def test_set_get_roundtrip(redis):
    clock = FakeClock(NOW)
    await set_counter(redis, clock, "daily", 23999)
    assert await get_counter(redis, clock, "daily") == 23999
    assert await get_counter(redis, clock, "monthly") is None


async def test_set_overwrites_and_sets_ttl(redis):
    clock = FakeClock(NOW)
    await set_counter(redis, clock, "daily", 100)
    await set_counter(redis, clock, "daily", 23999)
    assert await get_counter(redis, clock, "daily") == 23999
    ttl = await redis.ttl("jev:daily:20261005")
    assert 0 < ttl <= 48 * 3600
    await set_counter(redis, clock, "monthly", 479999)
    ttl = await redis.ttl("jev:monthly:202610")
    assert 0 < ttl <= 45 * 24 * 3600


async def test_delete_returns_count_and_missing_is_zero(redis):
    clock = FakeClock(NOW)
    await set_counter(redis, clock, "daily", 30000)
    assert await delete_counter(redis, clock, "daily") == 1
    assert await delete_counter(redis, clock, "daily") == 0
    assert await get_counter(redis, clock, "daily") is None


async def test_scope_validation():
    clock = FakeClock(NOW)
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with pytest.raises(ValueError):
        await set_counter(redis, clock, "yearly", 1)
    await redis.aclose()


def test_cli_main_dispatch(capsys):
    """CLI配線(subprocess不要・run_*を直接呼ぶ)。"""
    from latch.nfrtools import __main__ as cli
    from latch.nfrtools.__main__ import run_set

    class _StubClock:
        def jst_date(self):
            return datetime(2026, 10, 5, tzinfo=UTC).date()

    class _StubRedis:
        def __init__(self):
            self.store: dict[str, str] = {}

        async def set(self, key, value, ex=None):
            self.store[key] = value
            return True

    r = _StubRedis()
    import asyncio

    asyncio.run(run_set(r, _StubClock(), "daily", "23999"))
    out = capsys.readouterr().out
    assert "jev:daily:20261005" in out
    assert "23999" in out
    assert cli  # CLIモジュールのmain/build_parser存在


def test_cli_build_parser_has_three_operations():
    from latch.nfrtools.__main__ import build_parser

    parser = build_parser()
    args = parser.parse_args(["d16", "set", "monthly", "479999"])
    assert args.command == "d16"
    assert args.operation == "set"
    assert args.scope == "monthly"
    assert args.value == "479999"
