"""バックフィル周期タスク(06 D-15・design §2.5)。

Worker内の周期タスク: embedding IS NULL かつ status='active' のIntentを
抽出してEmbeddingWorker.handleへ委譲する。プロバイダ障害中は失敗がNULLの
まま残り、次周期で自然に再試行される=「回復後の一括再エンベディング」の
自動達成。対象をembedding IS NULLのみに絞るのは、ws-3がfixture直入れする
embedding(embedding_model NULL)を誤って再エンベディングしないための防線
でもある(design §1.4-2)。初回は周期待ちから開始(起動直後のバースト回避・
ws-3統合試験への干渉防止)。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_SELECT_BACKFILL_TARGETS = text("""
    SELECT id, version FROM intents
    WHERE embedding IS NULL AND status = 'active'
    ORDER BY updated_at
    LIMIT :limit
""")


def _coerce_uuid(value: object) -> uuid.UUID:
    """asyncpgはuuid列をUUIDインスタンスで返す(M0 ws-3と同じ対策)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


class BackfillRunner:
    """周期ごとに失敗Intent(embedding NULL・active)をhandleへ渡す(design §2.5)。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        embedding,
        interval_sec: float,
        batch_limit: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._embedding = embedding
        self._interval_sec = interval_sec
        self._batch_limit = batch_limit
        self._sleep = sleep

    async def run_once(self) -> int:
        """1周期分: 対象抽出→handle委譲(項目単位の例外は握って継続)。"""
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _SELECT_BACKFILL_TARGETS, {"limit": self._batch_limit}
            )
            rows = res.fetchall()
        done = 0
        for row_id, version in rows:
            intent_id = _coerce_uuid(row_id)
            try:
                await self._embedding.handle(intent_id, version)
                done += 1
            except Exception:
                logger.warning(
                    "backfill handle failed intent_id=%s version=%s",
                    intent_id,
                    version,
                )
        return done

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """sleep-first周期ループ。待機中のstopで周期処理を挟まず終了(design §2.5)。"""
        while stop is None or not stop.is_set():
            await self._wait_interval(stop)
            if stop is not None and stop.is_set():
                break
            try:
                await self.run_once()
            except Exception:
                logger.warning("backfill run_once failed", exc_info=True)

    async def _wait_interval(self, stop: asyncio.Event | None) -> None:
        if stop is None:
            await self._sleep(self._interval_sec)
            return
        try:
            # 300秒の待機中でもshutdownに追従(design §2.5)
            await asyncio.wait_for(stop.wait(), timeout=self._interval_sec)
        except TimeoutError:
            pass
