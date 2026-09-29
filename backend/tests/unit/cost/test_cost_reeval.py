"""ReevalGuard: 初回allow・窓内deny・TTL・キー分離・fail-closed(design §4.1)。

Redis側TTLは実時間で減るため、TTL切れの再許可はキーDELETEで「切れた状態」を
再現して検証する(§9-7。FakeClockを進めても実Redis/fakeredisのTTLは変化しない)。
"""

import uuid

import fakeredis.aioredis
import pytest

from latch.worker.cost.errors import JevCostDependencyError
from latch.worker.cost.reeval import ReevalGuard

IID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
OTHER = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


async def test_first_call_allows_second_denies(redis):
    guard = ReevalGuard(redis)
    assert await guard.allow(IID) is True
    assert await guard.allow(IID) is False  # 30分以内(06 §5(c))


async def test_key_ttl_is_1800(redis):
    guard = ReevalGuard(redis)
    await guard.allow(IID)
    assert 0 < await redis.ttl(f"reeval:{IID}") <= 1800


async def test_allows_again_after_key_expiry_equivalent(redis):
    """TTL切れ相当(キー消失)で再びallow(30分経過後の次のEventで再評価)。"""
    guard = ReevalGuard(redis)
    assert await guard.allow(IID) is True
    assert await guard.allow(IID) is False
    await redis.delete(f"reeval:{IID}")  # TTL切れの相当(§9-7)
    assert await guard.allow(IID) is True


async def test_distinct_intents_are_independent(redis):
    guard = ReevalGuard(redis)
    await guard.allow(IID)
    assert await guard.allow(OTHER) is True  # Intent単位の鍵


async def test_key_prefix_isolation(redis):
    guard = ReevalGuard(redis, key_prefix="it-")
    await guard.allow(IID)
    assert await redis.exists(f"it-reeval:{IID}") == 1
    assert await redis.exists(f"reeval:{IID}") == 0


async def test_redis_exception_fail_closed(redis, monkeypatch):
    async def broken(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis, "set", broken)
    guard = ReevalGuard(redis)
    with pytest.raises(JevCostDependencyError):
        await guard.allow(IID)
