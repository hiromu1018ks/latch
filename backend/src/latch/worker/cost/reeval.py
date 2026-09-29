"""再評価頻度ガード(06 §5・design §2.6)。

同一Intentの更新による再評価は直近の詳細評価(パイプライン投入)から30分
空ける(06 §5 FR-03(c))。キー reeval:{intent_id}(06 §5のキー名そのまま)の
SET NX EX 1800 で判定する。Redis例外はfail-closed(design §2.4-7 —
フックが例外を出せばstage1の再試行5回→quarantinedの既存経路に載る)。
Bucket再評価・catch-upスキャン(ws-6)も同一部品を再利用する(design §5-7)。
"""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis

from latch.worker.cost.errors import JevCostDependencyError
from latch.worker.cost.store import JevCostStore


class ReevalGuard:
    """「直近の詳細評価から30分」の統一判定(起点を問わず — design §2.6)。"""

    def __init__(self, redis: aioredis.Redis, *, key_prefix: str = "") -> None:
        self._store = JevCostStore(redis, key_prefix=key_prefix)

    async def allow(self, intent_id: uuid.UUID) -> bool:
        """True=評価してよい(初回or30分経過)・False=30分以内の再評価。"""
        try:
            return await self._store.set_reeval_nx(str(intent_id))
        except Exception as exc:
            raise JevCostDependencyError("jev cost store unavailable") from exc
