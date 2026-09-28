"""Redis上のレート制限カウンタ(design §2.7)。

鍵(接頭辞 rl: — auth: と名前空間を分ける。JevカウンタはM2で別接頭辞):
  API全体:   INCR rl:api:{user_key}:{yyyymmddHHMM}   TTL 120秒
  作成:      INCR rl:create:{user_id}:{yyyymmdd}      TTL 48時間
  更新:      INCR rl:update:{intent_id}:{yyyymmddHH}  TTL 13時間
  auth系:    INCR rl:auth:{provider}:{sha256(subject)}:{yyyymmddHHMM}  TTL 120秒

INCRとEXPIREはpipelineで毎回併発する。バケットキーは時間の進行とともに新キーへ
切替わるため(=リセット)、EXPIREの毎回上書きは無害。TTLは掃除用に留め、リセット
表現には使わない(04 §5 — TTL方式はUTC基準になりJST 0時とずれるため不採用)。
バケット文字列(yyyymmddHHMM等)は呼び出し側(limiter)がClockから導出する。
"""

from __future__ import annotations

import redis.asyncio as aioredis

_TTL_API_S = 120
_TTL_AUTH_S = 120
_TTL_CREATE_S = 48 * 3600
_TTL_UPDATE_S = 13 * 3600


class RateLimitStore:
    """Redis操作を閉じ込めるStore(decode_responses=True のRedisを注入)。"""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def _incr(self, key: str, ttl_s: int) -> int:
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_s)
        result = await pipe.execute()
        return int(result[0])

    async def incr_api(self, user_key: str, bucket: str) -> int:
        """API全体60req/分(user_key=user_id文字列 or anon-… — design §2.7)。"""
        return await self._incr(f"rl:api:{user_key}:{bucket}", _TTL_API_S)

    async def incr_create(self, user_id: str, bucket: str) -> int:
        """Intent作成20件/日(bucket=yyyymmdd)。"""
        return await self._incr(f"rl:create:{user_id}:{bucket}", _TTL_CREATE_S)

    async def incr_update(self, intent_id: str, bucket: str) -> int:
        """Intent更新6回/時(bucket=yyyymmddHH)。"""
        return await self._incr(f"rl:update:{intent_id}:{bucket}", _TTL_UPDATE_S)

    async def incr_auth(self, provider: str, subject_sha: str, bucket: str) -> int:
        """auth/token・refresh 60req/分(subjectはsha256hex — design §2.5)。"""
        return await self._incr(
            f"rl:auth:{provider}:{subject_sha}:{bucket}", _TTL_AUTH_S
        )
