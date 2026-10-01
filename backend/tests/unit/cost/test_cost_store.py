"""JevCostStore: キー形式・TTL・INCR+EXPIRE・key_prefix(design §2.4・§4.1)。

fakeredis(redis-pyのコマンド解釈経路をそのまま実行)。ratelimit/test_store.py流儀。
"""

import fakeredis.aioredis
import pytest

from latch.worker.cost.store import JevCostStore


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


async def test_incr_daily_key_and_ttl(redis):
    store = JevCostStore(redis)
    assert await store.incr_daily("20260929") == 1
    assert await store.incr_daily("20260929") == 2  # 積算
    assert await redis.get("jev:daily:20260929") == "2"
    assert 0 < await redis.ttl("jev:daily:20260929") <= 48 * 3600


async def test_incr_monthly_key_and_ttl(redis):
    store = JevCostStore(redis)
    await store.incr_monthly("202609")
    assert await redis.get("jev:monthly:202609") == "1"
    assert 0 < await redis.ttl("jev:monthly:202609") <= 45 * 24 * 3600


async def test_incr_intent_and_user_keys(redis):
    store = JevCostStore(redis)
    await store.incr_intent("i-1", "20260929")
    await store.incr_user("u-1", "20260929")
    assert await redis.get("jev:intent:i-1:20260929") == "1"
    assert await redis.get("jev:user:u-1:20260929") == "1"
    assert 0 < await redis.ttl("jev:intent:i-1:20260929") <= 48 * 3600


async def test_record_execution_provider_keys(redis):
    """第一候補/フォールバックの経路別キー(D-16内訳・design §2.4)。"""
    store = JevCostStore(redis)
    await store.record_execution("typesafe_jev", "20260929")
    await store.record_execution("typesafe_jev", "20260929")
    await store.record_execution("fallback_llm", "20260929")
    assert await redis.get("jev:exec:20260929:typesafe_jev") == "2"
    assert await redis.get("jev:exec:20260929:fallback_llm") == "1"


async def test_day_key_switch_resets_counter(redis):
    """日付キー切替=リセット(JST 0時を跨ぐと新キーで0から — design §2.5)。"""
    store = JevCostStore(redis)
    assert await store.incr_daily("20260929") == 1
    assert await store.incr_daily("20260930") == 1  # 新キー=別カウンタ
    assert await store.incr_intent("i-1", "20260930") == 1


async def test_set_reeval_nx_and_ttl(redis):
    store = JevCostStore(redis)
    assert await store.set_reeval_nx("i-1") is True
    assert await store.set_reeval_nx("i-1") is False  # 30分以内
    assert 0 < await redis.ttl("reeval:i-1") <= 1800  # 06 §5の30分


async def test_key_prefix_isolates_namespace(redis):
    """key_prefix=integration試験での共用Redis干渉防止(design §3.1)。"""
    store = JevCostStore(redis, key_prefix="it-")
    await store.incr_daily("20260929")
    await store.set_reeval_nx("i-1")
    assert await redis.get("it-jev:daily:20260929") == "1"
    assert await redis.exists("it-reeval:i-1") == 1
    assert await redis.keys("jev:*") == []
    assert await redis.keys("reeval:*") == []


async def test_scan_report_aggregates_top_and_breakdown(redis):
    """80% alert添付レポートの集計(design §2.4-6)。別日は入らない。"""
    store = JevCostStore(redis)
    for _ in range(3):
        await store.incr_intent("i-a", "20260929")
    await store.incr_intent("i-b", "20260929")
    for _ in range(2):
        await store.incr_user("u-x", "20260929")
    await store.record_execution("typesafe_jev", "20260929")
    await store.record_execution("fallback_llm", "20260929")
    await store.incr_intent("i-a", "20260928")  # 別日=対象外

    report = await store.scan_report("20260929")
    assert report["intent_top"][0] == ("jev:intent:i-a:20260929", 3)
    assert ("jev:intent:i-b:20260929", 1) in report["intent_top"]
    assert report["user_top"][0] == ("jev:user:u-x:20260929", 2)
    assert report["exec_breakdown"] == {
        "jev:exec:20260929:fallback_llm": 1,
        "jev:exec:20260929:typesafe_jev": 1,
    }


# -- 掃除メソッド(M3 ws-2・リセットジョブ用・design §2.5) --


async def test_delete_daily_removes_key_and_is_idempotent(redis):
    """旧日次キーの即時解放(TTL 48hを待たない)。削除数を返す・冪等。"""
    store = JevCostStore(redis)
    await store.incr_daily("20260930")
    assert await store.delete_daily("20260930") == 1
    assert await redis.get("jev:daily:20260930") is None
    assert await store.delete_daily("20260930") == 0  # 既にない(冪等)


async def test_delete_monthly_removes_key(redis):
    """旧月次キーの即時解放(TTL 45日を待たない・月初0時)。"""
    store = JevCostStore(redis)
    await store.incr_monthly("202609")
    assert await store.delete_monthly("202609") == 1
    assert await redis.get("jev:monthly:202609") is None


async def test_scan_delete_removes_matching_keys_only(redis):
    """SCAN一致キーの一括削除(jev:exec:{day}:* 等)。対象外は保持。"""
    store = JevCostStore(redis)
    await store.record_execution("typesafe_jev", "20260930")
    await store.record_execution("typesafe_jev", "20260930")
    await store.record_execution("fallback_llm", "20260930")
    await store.incr_daily("20260930")  # パターン外(掃除対象は日次DELが担う)
    assert await store.scan_delete("jev:exec:20260930:*") == 2
    assert await redis.get("jev:exec:20260930:typesafe_jev") is None
    assert await redis.get("jev:exec:20260930:fallback_llm") is None
    assert await redis.get("jev:daily:20260930") == "1"  # 対象外は保持


async def test_cleanup_methods_respect_key_prefix(redis):
    """key_prefix付きでも同一prefix空間を掃除(integration共用Redis対策)。"""
    store = JevCostStore(redis, key_prefix="rj-")
    await store.incr_daily("20260930")
    await store.record_execution("typesafe_jev", "20260930")
    await store.incr_monthly("202609")
    assert await store.delete_daily("20260930") == 1
    assert await store.scan_delete("jev:exec:20260930:*") == 1
    assert await store.delete_monthly("202609") == 1
    assert await redis.get("rj-jev:daily:20260930") is None
