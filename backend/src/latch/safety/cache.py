"""Redisブロックキャッシュ(M3 ws-5 design §2.2)。

鍵(接頭辞 blk: — auth:・rl: と名前空間を分ける):
  ユーザー単位JSON一覧:  blk:u:{user_id} = '["<blocked_uuid>", ...]'  SET EX 3600

read-through: MGETで一括取得し、欠落キーはDBから一括読みしてSET(空は"[]"。
ブロックゼロの大多数の参照も1回のDB読みで賄える)。TTL 3600は掃除用では
なくDEL失敗時の最終収束期間(08 §5.1の「反映はキャッシュ更新を経由」は
登録・解除のコミット後に両者のキーをDELする — コミット前DELは並行
read-throughがコミット前DBで旧値を再キャッシュする窓を残すため)。
Redis断(RedisError)は安全性優先でDBのselect_block_betweenへフォール
バックする(キャッシュは性能の最適化であって真実はDBにある・warning 1行)。
DB読み込みは自分のengine接続で行い、送信トランザクション(FOR UPDATE
保持中)と直列しない(blocks行をロックしない単純SELECTのため競合しない)。
"""

from __future__ import annotations

import json
import logging
import uuid

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.latches import store as latches_store
from latch.safety import store

logger = logging.getLogger("latch.safety")

_TTL_S = 3600


def _key(user_id: uuid.UUID) -> str:
    return f"blk:u:{user_id}"


class BlockCache:
    """ブロック一覧のread-throughキャッシュ(decode_responses=TrueのRedisを注入)。"""

    def __init__(self, *, redis_client: aioredis.Redis, engine: AsyncEngine) -> None:
        self._redis = redis_client
        self._engine = engine

    async def is_blocked_between(self, me: uuid.UUID, others: list[uuid.UUID]) -> bool:
        """自分とothersの間のblocks双方向判定(08 §5.1・引用#1)。

        自分の一覧にothersの誰かが含まれる、またはothersのいずれかの
        一覧に自分が含まれればTrue(双方向)。
        """
        if not others:
            return False
        try:
            user_ids = [me, *others]
            raw = await self._redis.mget([_key(u) for u in user_ids])
            lists: dict[str, set[str]] = {}
            missing: list[uuid.UUID] = []
            for user_id, value in zip(user_ids, raw, strict=True):
                if value is None:
                    missing.append(user_id)
                else:
                    lists[str(user_id)] = set(json.loads(value))
            if missing:
                lists.update(await self._load(missing))
            me_key = str(me)
            if lists[me_key] & {str(o) for o in others}:
                return True
            return any(me_key in lists[str(o)] for o in others)
        except RedisError:
            logger.warning("safety.cache.redis_error fallback=db")
            async with self._engine.connect() as conn:
                return await latches_store.select_block_between(conn, me, others)

    async def _load(self, user_ids: list[uuid.UUID]) -> dict[str, set[str]]:
        """欠落キーをDBから一括読みし、SET EX 3600して値を返す(read-through)。"""
        async with self._engine.connect() as conn:
            blocked_map = await store.select_blocked_ids_map(conn, user_ids)
        out: dict[str, set[str]] = {}
        for user_id in user_ids:
            ids = [str(x) for x in blocked_map.get(user_id, [])]
            await self._redis.set(_key(user_id), json.dumps(ids), ex=_TTL_S)
            out[str(user_id)] = set(ids)
        return out

    async def invalidate(self, user_ids: list[uuid.UUID]) -> None:
        """登録・解除コミット後のキー削除(§2.2)。

        失敗(Redis断)は例外を出さずTTL 3600秒で収束(warning 1行のみ。
        呼び出し元のAPIはDBコミット済みなので成功扱い)。
        """
        try:
            await self._redis.delete(*[_key(u) for u in user_ids])
        except RedisError:
            logger.warning("safety.cache.invalidate_failed ttl_converges")
