"""再評価Runner(06 §9・design §2.8)。BackfillRunnerと同型の周期ジョブ。

catch-upスキャン(expires_atまで2時間以内のactive)と30分Bucket再評価
(time_startが当該Bucketに属するIntent・design §2.8-2の読み)を抽出し、
reevalガード(ws-4資産・入口で共用)を通してpipelineを直接投入する
(Eventは発行しない — 同一versionのidempotencyキー衝突のため・06 §9)。
sleep-first・stop追従・run_once内の例外は握らずrun()が握って次周期で回収。

M3 ws-2(design §2.1案A): ExpirySweeperをオプション注入(sweeper=Noneで
従動作)。run_onceの**先頭**でsweeper.run_once()を実行してからcatch-up/
Bucket投入へ(期限切れ確定がパイプラインの重さに後ろ倒しにならない)。
60秒tickの1本化でlatches・Intent期限切れ・catch-upの切替・停止は単一ジョブ。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.worker.matching import latch_calc

logger = logging.getLogger(__name__)

_SELECT_CATCHUP_TARGETS = text("""
    SELECT id FROM intents
    WHERE status = 'active'
      AND embedding IS NOT NULL
      AND expires_at IS NOT NULL
      AND expires_at > CAST(:now AS timestamptz)
      AND expires_at <= CAST(:now AS timestamptz) + interval '2 hours'
    ORDER BY expires_at, id
    LIMIT :batch_limit
""")

_SELECT_BUCKET_TARGETS = text("""
    SELECT id FROM intents
    WHERE status = 'active'
      AND embedding IS NOT NULL
      AND time_start >= CAST(:bucket_start AS timestamptz)
      AND time_start < CAST(:bucket_end AS timestamptz)
    ORDER BY id
""")


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def _select_catchup_targets(
    engine: AsyncEngine, now: datetime, batch_limit: int
) -> list[uuid.UUID]:
    """catch-up対象抽出(expires_atまで2時間以内・引用#19)。

    「直近の詳細評価から30分以上経過」はSQLに入れない — reevalガードが
    入口で同じ意味をアトミックに判定・消費する(design §2.8-1)。
    """
    async with engine.begin() as conn:
        res = await conn.execute(
            _SELECT_CATCHUP_TARGETS, {"now": now, "batch_limit": batch_limit}
        )
        rows = res.fetchall()
    return [_coerce_uuid(r[0]) for r in rows]


async def _select_bucket_targets(
    engine: AsyncEngine, b_start: datetime, b_end: datetime
) -> list[uuid.UUID]:
    """30分Bucket対象抽出(time_startが当該Bucketに属するIntent)。"""
    async with engine.begin() as conn:
        res = await conn.execute(
            _SELECT_BUCKET_TARGETS, {"bucket_start": b_start, "bucket_end": b_end}
        )
        rows = res.fetchall()
    return [_coerce_uuid(r[0]) for r in rows]


class ReevalRunner:
    """周期ごとにcatch-up+Bucket対象をpipelineへ渡す(design §2.8)。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        guard,  # ReevalGuard | None(Noneならガード判定をスキップ)
        pipeline,  # Callable[[uuid.UUID], Awaitable[None]]
        interval_sec: float,
        batch_limit: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        sweeper=None,  # ExpirySweeper | None(M3 ws-2・design §2.1案A)
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._guard = guard
        self._pipeline = pipeline
        self._interval_sec = interval_sec
        self._batch_limit = batch_limit
        self._sleep = sleep
        self._sweeper = sweeper  # ExpirySweeper | None(M3 ws-2・design §2.1案A)
        self._last_bucket: datetime | None = None  # 前回処理Bucket(メモリ保持)

    async def run_once(self) -> int:
        """1周期分: sweeper(注入時)→catch-up抽出→Bucket処理→直接投入。

        例外は握らない(sweeper内も含む・design §2.1)。
        """
        if self._sweeper is not None:
            await self._sweeper.run_once()  # 先頭(期限切れ確定を先行)
        now = self._clock.now()
        ids = await _select_catchup_targets(self._engine, now, self._batch_limit)
        b_start = latch_calc.bucket_start(now)
        if self._last_bucket is None or b_start > self._last_bucket:
            b_end = b_start + timedelta(minutes=latch_calc.BUCKET_MINUTES)
            ids = ids + await _select_bucket_targets(self._engine, b_start, b_end)
            self._last_bucket = b_start
        seen: set[uuid.UUID] = set()
        done = 0
        for intent_id in ids:
            if intent_id in seen:  # catch-upとBucketの重複は1回のみ
                continue
            seen.add(intent_id)
            if self._guard is not None and not await self._guard.allow(intent_id):
                continue  # 30分以内の再評価(06 §5(c)・抽出条件とガードで結果は等しい)
            await self._pipeline(intent_id)
            done += 1
        return done

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """sleep-first周期ループ(BackfillRunnerと同一)。"""
        while stop is None or not stop.is_set():
            await self._wait_interval(stop)
            if stop is not None and stop.is_set():
                break
            try:
                await self.run_once()
            except Exception:
                logger.warning("reeval run_once failed", exc_info=True)

    async def _wait_interval(self, stop: asyncio.Event | None) -> None:
        if stop is None:
            await self._sleep(self._interval_sec)
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=self._interval_sec)
        except TimeoutError:
            pass
