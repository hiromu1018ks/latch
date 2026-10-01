"""30日定期削除ジョブ(M3 ws-6・design §2.5・08 §2.5 FR-22)。

match_candidates・group_candidatesの評価確定から30日経過行を日次削除する
(削除要求がない場合も。削除されたユーザーの条件由来の判定値が相手側
レコードに恒久残存する経路を断つ)。対象は全status一律でupdated_at < cutoff
(pendingも含む — 起点Intentは確実に失効しているため安全側)。
latchesは削除しない(履歴維持)。group削除の各周でlatches.group_candidate_id
をNULL化してから削除する(FK RESTRICTの解消・proposal表示は残る)。
runループ・graceful shutdown・注入sleepはResetJobと同一契約(0時発火・
失敗時retry_sec再試行)。30日判定はClock.now()(UTC)の実時間。
Indexは追加しない(日次1回・ベータ規模ではフルスキャンで十分 — design §2.5)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.worker.reset import next_jst_midnight

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
BATCH_LIMIT = 500

_DETACH_GROUP_LATCHES = text("""
    UPDATE latches SET group_candidate_id = NULL
     WHERE group_candidate_id IN (
         SELECT id FROM group_candidates
          WHERE updated_at < CAST(:cutoff AS timestamptz)
          ORDER BY id LIMIT :batch_limit)
""")
_DELETE_GROUP_BATCH = text("""
    DELETE FROM group_candidates
     WHERE id IN (SELECT id FROM group_candidates
                   WHERE updated_at < CAST(:cutoff AS timestamptz)
                   ORDER BY id LIMIT :batch_limit)
""")
_DELETE_MATCH_BATCH = text("""
    DELETE FROM match_candidates
     WHERE id IN (SELECT id FROM match_candidates
                   WHERE updated_at < CAST(:cutoff AS timestamptz)
                   ORDER BY id LIMIT :batch_limit)
""")


class RetentionJob:
    """次のJST 0時まで待機→run_once(30日経過候補の削除)。独立task。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        retry_sec: int = 300,
        batch_limit: int = BATCH_LIMIT,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._retry_sec = retry_sec
        self._batch_limit = batch_limit
        self._sleep = sleep

    async def run_once(self) -> tuple[int, int]:
        """0時発火の本体: 窓で空になるまで削除。戻り=(group, match)件数。"""
        cutoff = self._clock.now() - timedelta(days=RETENTION_DAYS)
        groups = matches = 0
        async with self._engine.begin() as conn:
            while True:
                params = {"cutoff": cutoff, "batch_limit": self._batch_limit}
                await conn.execute(_DETACH_GROUP_LATCHES, params)
                n = (await conn.execute(_DELETE_GROUP_BATCH, params)).rowcount
                groups += n
                if n < self._batch_limit:
                    break
            while True:
                params = {"cutoff": cutoff, "batch_limit": self._batch_limit}
                n = (await conn.execute(_DELETE_MATCH_BATCH, params)).rowcount
                matches += n
                if n < self._batch_limit:
                    break
        logger.info(
            "retention.run_once deleted group_candidates=%d match_candidates=%d",
            groups,
            matches,
        )
        return groups, matches

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """次のJST 0時まで待機→run_once(失敗時はretry_secで再試行)。"""
        while stop is None or not stop.is_set():
            now = self._clock.now()
            await self._wait((next_jst_midnight(now) - now).total_seconds(), stop)
            if stop is not None and stop.is_set():
                break
            while True:  # 失敗時はretry_secで再試行(翌0時まで放置しない)
                try:
                    await self.run_once()
                    break
                except Exception:
                    logger.warning("retention run_once failed", exc_info=True)
                    await self._wait(self._retry_sec, stop)
                    if stop is not None and stop.is_set():
                        return

    async def _wait(self, seconds: float, stop: asyncio.Event | None) -> None:
        """待機(ResetJob._waitと同一契約 — 注入sleepでunit試験が決定的に回る)。"""
        if stop is None:
            await self._sleep(seconds)
            return
        sleep_task = asyncio.create_task(self._sleep(seconds))
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
