"""RateLimitStore: INCR積算・TTL設定・rl:キー形式・subjectハッシュ化(design §2.7)。

fakeredis(redis-pyのコマンド解釈経路をそのまま実行)で決定的に検証する
(tests/unit/auth/test_sessions.py と同じ手法 — design §3.1の「スタブRedis」に相当)。
"""

import fakeredis.aioredis
import pytest

from latch.ratelimit.store import RateLimitStore


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


def _store(redis) -> RateLimitStore:
    return RateLimitStore(redis)


async def test_incr_api_accumulates_and_sets_ttl(redis):
    store = _store(redis)
    assert await store.incr_api("u-1", "202609282159") == 1
    assert await store.incr_api("u-1", "202609282159") == 2  # 積算
    key = "rl:api:u-1:202609282159"
    assert await redis.get(key) == "2"
    assert 0 < await redis.ttl(key) <= 120  # TTL=2分(掃除用・design §2.7)


async def test_incr_create_key_uses_day_bucket_and_48h_ttl(redis):
    store = _store(redis)
    await store.incr_create(str(1), "20260928")
    key = "rl:create:1:20260928"
    assert await redis.get(key) == "1"
    assert 0 < await redis.ttl(key) <= 48 * 3600


async def test_incr_update_key_uses_hour_bucket_and_13h_ttl(redis):
    store = _store(redis)
    await store.incr_update("i-1", "2026092821")
    key = "rl:update:i-1:2026092821"
    assert await redis.get(key) == "1"
    assert 0 < await redis.ttl(key) <= 13 * 3600


async def test_incr_auth_hashes_subject_in_key(redis):
    store = _store(redis)
    subject_sha = "a" * 64  # limiterが計算したsha256(ここでは任意のhex)
    await store.incr_auth("google", subject_sha, "202609282159")
    keys = await redis.keys("rl:auth:*")
    assert keys == [f"rl:auth:google:{subject_sha}:202609282159"]
    assert 0 < await redis.ttl(keys[0]) <= 120


async def test_different_buckets_are_separate_counters(redis):
    store = _store(redis)
    await store.incr_api("u-1", "202609282159")
    assert await store.incr_api("u-1", "202609282200") == 1  # 分が進む=新キー
    assert await store.incr_api("u-2", "202609282159") == 1  # ユーザーが違う=別鍵


async def test_namespace_is_separated_from_auth_prefix(redis):
    store = _store(redis)
    await store.incr_api("u-1", "202609282159")
    assert await redis.keys("auth:*") == []  # auth:と衝突しない(04 §3)
