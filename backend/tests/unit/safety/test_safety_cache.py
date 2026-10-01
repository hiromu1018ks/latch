"""BlockCacheのunit試験(M3 ws-5 design §4.1)。

Redisはスタブ(asyncメソッドを持つ偽オブジェクト・decode_responses=True
相当のstr値を保持)。DB読み込みはsafety.store.select_blocked_ids_mapの
monkeypatch差し替え、フォールバックはlatches.store.select_block_betweenの
差し替え(SQLに依存しない・test_chat_service.pyと同型)。
"""

import json
import uuid

from redis.exceptions import RedisError

from latch.latches import store as latches_store
from latch.safety import store as safety_store
from latch.safety.cache import BlockCache

ME = uuid.uuid4()
P1 = uuid.uuid4()
P2 = uuid.uuid4()


class FakeRedis:
    """decode_responses=True相当の最小スタブ(mget/set/delete)。"""

    def __init__(self, data: dict | None = None, fail: bool = False):
        self.data: dict[str, str] = dict(data or {})
        self.fail = fail
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def mget(self, keys):
        if self.fail:
            raise RedisError("down")
        return [self.data.get(k) for k in keys]

    async def set(self, key, value, ex=None):
        if self.fail:
            raise RedisError("down")
        self.set_calls.append((key, value, ex))
        self.data[key] = value

    async def delete(self, *keys):
        if self.fail:
            raise RedisError("down")
        for k in keys:
            self.data.pop(k, None)


class _NoopEngine:
    """store差し替え用の空エンジン(conn不使用・ws-4と同型)。"""

    def connect(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _ret(value):
    """monkeypatch差し替え用の「常にvalueを返すasync関数」ファクトリ。"""

    async def _inner(*args, **kwargs):
        return value

    return _inner


def _make(data: dict | None = None, fail: bool = False):
    redis = FakeRedis(data, fail=fail)
    cache = BlockCache(redis_client=redis, engine=_NoopEngine())
    return redis, cache


async def test_read_through_miss_loads_and_sets(monkeypatch):
    """ミス→DB一括読み→SET EX 3600のJSON配列(design §2.2)。"""
    redis, cache = _make()  # Redis空=全面ミス
    monkeypatch.setattr(
        safety_store, "select_blocked_ids_map", _ret({ME: [P1], P1: []})
    )
    assert await cache.is_blocked_between(ME, [P1]) is True
    assert redis.set_calls == [
        (f"blk:u:{ME}", json.dumps([str(P1)]), 3600),
        (f"blk:u:{P1}", json.dumps([]), 3600),
    ]


async def test_hit_skips_db(monkeypatch):
    """MGETヒットはDB問い合わせゼロ(§2.2 — 参照は毎回DBを見ない)。"""
    redis, cache = _make(
        {
            f"blk:u:{ME}": json.dumps([str(P1)]),
            f"blk:u:{P1}": json.dumps([]),
        }
    )
    called = []

    async def _db(*args, **kwargs):
        called.append(1)
        return {}

    monkeypatch.setattr(safety_store, "select_blocked_ids_map", _db)
    assert await cache.is_blocked_between(ME, [P1]) is True
    assert called == []


async def test_empty_blocks_cached_as_empty_json(monkeypatch):
    """ブロックゼロの大多数も"[]"でキャッシュ(案A・§2.2)。"""
    redis, cache = _make()
    monkeypatch.setattr(safety_store, "select_blocked_ids_map", _ret({}))
    assert await cache.is_blocked_between(ME, [P1]) is False
    assert redis.data[f"blk:u:{ME}"] == "[]"
    assert redis.data[f"blk:u:{P1}"] == "[]"


async def test_bidirectional_detection():
    """双方向判定: 片方向のみでTrue・逆視点もTrue・無関係False(引用#1)。"""
    # (ME→P1)のみの行: 自分視点True
    _, cache = _make(
        {f"blk:u:{ME}": json.dumps([str(P1)]), f"blk:u:{P1}": json.dumps([])}
    )
    assert await cache.is_blocked_between(ME, [P1]) is True
    # 同じ行を相手視点で: P1の一覧にMEは無いがMEの一覧にP1 → True(双方向)
    _, cache_r = _make(
        {f"blk:u:{ME}": json.dumps([str(P1)]), f"blk:u:{P1}": json.dumps([])}
    )
    assert await cache_r.is_blocked_between(P1, [ME]) is True
    # 相手(P2)が自分(ME)をブロック: 自分の一覧は空でもTrue
    _, cache_p2 = _make(
        {f"blk:u:{ME}": json.dumps([]), f"blk:u:{P2}": json.dumps([str(ME)])}
    )
    assert await cache_p2.is_blocked_between(ME, [P2]) is True
    # グループ複数相手: others同士のブロック(P1→P2)は自分の送信を止めない
    _, cache_g = _make(
        {
            f"blk:u:{ME}": json.dumps([]),
            f"blk:u:{P1}": json.dumps([str(P2)]),
            f"blk:u:{P2}": json.dumps([]),
        }
    )
    assert await cache_g.is_blocked_between(ME, [P1, P2]) is False
    # グループで自分が誰か(P1)をブロック済み → True
    _, cache_g2 = _make(
        {
            f"blk:u:{ME}": json.dumps([str(P1)]),
            f"blk:u:{P1}": json.dumps([]),
            f"blk:u:{P2}": json.dumps([]),
        }
    )
    assert await cache_g2.is_blocked_between(ME, [P1, P2]) is True


async def test_invalidate_deletes_keys():
    """invalidateは指定ユーザーのキーをDEL(§2.2)。無関係キーは残る。"""
    redis, cache = _make(
        {f"blk:u:{ME}": "[]", f"blk:u:{P1}": "[]", f"blk:u:{P2}": "[]"}
    )
    await cache.invalidate([ME, P1])  # 両者のキーをDEL
    assert f"blk:u:{ME}" not in redis.data
    assert f"blk:u:{P1}" not in redis.data
    assert f"blk:u:{P2}" in redis.data


async def test_redis_error_falls_back_to_db(monkeypatch):
    """Redis断は安全性優先でDBのselect_block_betweenへ(§2.2・Review Focus 4)。"""
    redis, cache = _make(fail=True)
    assert redis.fail is True
    monkeypatch.setattr(latches_store, "select_block_between", _ret(True))
    assert await cache.is_blocked_between(ME, [P1]) is True


async def test_no_others_returns_false():
    """others空はFalse・Redisにも触れない(1人LATCH等の防御)。"""
    redis, cache = _make()
    assert await cache.is_blocked_between(ME, []) is False
    assert redis.data == {}
