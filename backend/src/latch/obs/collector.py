"""60秒周期のexporter(M4 ws-4・design §2.6/§2.3)。

DB集計値(match_events pending件数・最古pending年齢・match_candidates件数
・kpi_daily最新行)とWorkerメモリ内のCircuitBreaker状態をgaugeへ写す薄い
周期タスク。年齢計算はClock経由(C2)。Event Queue lagの真実の源はDB
outbox(match_events)であり、Pub/Subエミュレータはsubscription統計を
返さない(design §2.6)。ws-7のQueue障害試験(10 §4.7 lag alert発報)は
このgaugeを消費する。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import text

from latch.core.clock import Clock
from latch.obs.metrics import metrics

logger = logging.getLogger(__name__)

BREAKER_STATE_VALUE = {"closed": 0, "open": 1, "half_open": 2}

_SQL_PENDING = text("""
    SELECT count(*) FROM match_events WHERE status='pending'
""")
_SQL_OLDEST = text("""
    SELECT coalesce(extract(epoch FROM CAST(:now AS timestamptz) - min(created_at)), 0)
    FROM match_events WHERE status='pending'
""")
_SQL_CANDIDATES = text("""
    SELECT count(*) FROM match_candidates
""")
_SQL_KPI_LATEST = text("""
    SELECT DISTINCT ON (metric_key) metric_key, numerator, denominator
    FROM kpi_daily ORDER BY metric_key, day DESC
""")


class MetricsCollector:
    """周期タスク: collect_onceをinterval秒ごとに繰り返す。"""

    def __init__(
        self,
        *,
        engine,
        clock: Clock,
        interval_sec: int = 60,
        breaker=None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._interval_sec = interval_sec
        self._breaker = breaker
        self._sleep = sleep

    async def collect_once(self) -> None:
        now = self._clock.now()
        async with self._engine.connect() as conn:
            pending = (await conn.execute(_SQL_PENDING)).scalar_one()
            oldest = (await conn.execute(_SQL_OLDEST, {"now": now})).scalar_one()
            candidates = (await conn.execute(_SQL_CANDIDATES)).scalar_one()
            kpi_rows = (await conn.execute(_SQL_KPI_LATEST)).all()
        metrics.event_queue_pending.set(pending)
        metrics.event_queue_oldest_pending_seconds.set(float(oldest))
        metrics.pipeline_candidates.set(candidates)
        for metric_key, num, den in kpi_rows:
            metrics.kpi_daily_numerator.labels(metric=metric_key).set(float(num))
            metrics.kpi_daily_denominator.labels(metric=metric_key).set(float(den))
        if self._breaker is not None:
            # stateは@property(属性アクセス・llm/breaker.py)。メソッド呼び出しは
            # 実行時TypeErrorになる(supervisor検証で発見・スタブ乖離の系統)
            metrics.breaker_state.set(BREAKER_STATE_VALUE.get(self._breaker.state, 0))

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """起動時即collect→interval待ち→collect→…。例外は握ってwarning+
        次tick(KpiJobと同一契約 — 観測失敗でworkerを落とさない)。待機は
        RetentionJob._waitと同一契約(stop即応・注入sleepでunitが決定的)。"""
        while stop is None or not stop.is_set():
            try:
                await self.collect_once()
            except Exception:
                logger.warning("collector collect_once failed", exc_info=True)
            await self._wait_interval(stop)

    async def _wait_interval(self, stop: asyncio.Event | None) -> None:
        """interval待ち(stop即応・RetentionJob._waitと同一契約)。"""
        if stop is None:
            await self._sleep(self._interval_sec)
            return
        sleep_task = asyncio.create_task(self._sleep(self._interval_sec))
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
