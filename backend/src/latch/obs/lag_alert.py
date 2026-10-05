"""Queue lag alert発報器(M4 ws-7・design §2.5案A)。API側の周期タスク。

滞留の作り方がworker停止(10 §4.7)のため・worker内のcollector
(docstring「ws-7のQueue障害試験はこのgaugeを消費する」)を発報器にすると
worker停止で発報器も止まる。同じDB集計値(match_events pending件数・最古
pending年齢)をAPIプロセスから直接SELECTして発報する。
発報は条件成立中1回だけ: プロセス内フラグ+Redisアームキー(SET NX EX)で
api1/api2の2インスタンス間でも単発化を担保(supervisor訂正・2026-10-05)。
解除(resolve)はアーム済みプロセスが条件不成立を観測したときにログ出力+キー
削除(発報→解除の往復を証拠化・D-16 alertと同型の規律)。アームした
プロセスが死亡した場合はTTL失効後に別インスタンスが再発報できる(観測の
回復)。Redis故障時も発報は落とさない(fail-open・単発化のみ劣化)・DB失敗
は握って次tick(collector.runと同一契約)。年齢はClock差分(C2)。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from sqlalchemy import text

from latch.core.clock import Clock

logger = logging.getLogger("latch.pipeline.alert")

# アームキー(staging Redisはstaging専用のため接頭辞なし)
ARM_KEY = "latch:alert:queue:arm"

_SQL_QUEUE_LAG = text("""
    SELECT count(*) AS pending, min(created_at) AS oldest
    FROM match_events WHERE status = 'pending'
""")


def structured_log_alert(event: dict) -> None:
    """alert媒体の段階実体(構造化ログ・guard.structured_log_alertと同型)。"""
    logger.warning("queue lag alert %s", json.dumps(event, ensure_ascii=False))


def structured_log_resolve(event: dict) -> None:
    logger.info("queue lag resolve %s", json.dumps(event, ensure_ascii=False))


class QueueLagAlert:
    """周期タスク: check_onceをinterval秒ごとに繰り返す。"""

    def __init__(
        self,
        *,
        engine,
        clock: Clock,
        redis,
        pending_threshold: int,
        oldest_threshold: int,
        arm_ttl_s: int = 180,
        interval_sec: int = 60,
        alert_emitter: Callable[[dict], None] = structured_log_alert,
        resolve_emitter: Callable[[dict], None] = structured_log_resolve,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._redis = redis
        self._pending_threshold = pending_threshold
        self._oldest_threshold = oldest_threshold
        self._arm_ttl_s = arm_ttl_s
        self._alert_emitter = alert_emitter
        self._resolve_emitter = resolve_emitter
        self._interval_sec = interval_sec
        self._alerted = False

    async def check_once(self) -> None:
        """DB集計1回分: 閾値判定→発報/解除。例外は握らずrunが握る。"""
        now = self._clock.now()
        async with self._engine.connect() as conn:
            row = (await conn.execute(_SQL_QUEUE_LAG)).first()
        pending = int(row[0]) if row is not None else 0
        oldest = row[1] if row is not None else None
        age = (now - oldest).total_seconds() if oldest is not None else 0.0
        event = {
            "kind": "queue_lag",
            "pending": pending,
            "oldest_pending_seconds": round(age, 1),
        }
        if pending >= self._pending_threshold or age >= self._oldest_threshold:
            if self._alerted:
                return  # 条件成立中の再発報を抑制(プロセス内フラグ)
            if not await self._arm():
                return  # 別インスタンスが発報済み(クロスインスタンス単発化)
            self._alerted = True
            self._alert_emitter(event)
            return
        if self._alerted:
            self._alerted = False
            await self._disarm()
            # resolveは発報と区別できるkindにする(テストピン・解除の証拠化)
            self._resolve_emitter({**event, "kind": "queue_lag_resolve"})

    async def _arm(self) -> bool:
        """アームキー確保(SET NX+TTL)。Redis故障はfail-openでTrue扱い。"""
        try:
            return bool(
                await self._redis.set(ARM_KEY, "1", nx=True, ex=self._arm_ttl_s)
            )
        except Exception:
            logger.warning("queue alert arm failed", exc_info=True)
            return True

    async def _disarm(self) -> None:
        try:
            await self._redis.delete(ARM_KEY)
        except Exception:
            logger.warning("queue alert disarm failed", exc_info=True)

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """起動時即check→interval待ち→check→…。例外は握ってwarning+次tick
        (MetricsCollector.runと同一契約)。待機はstop即応(MetricsCollector
        ._wait_intervalと同一契約)。"""
        while stop is None or not stop.is_set():
            try:
                await self.check_once()
            except Exception:
                logger.warning("queue lag alert check failed", exc_info=True)
            await self._wait_interval(stop)

    async def _wait_interval(self, stop: asyncio.Event | None) -> None:
        if stop is None:
            await asyncio.sleep(self._interval_sec)
            return
        sleep_task = asyncio.create_task(asyncio.sleep(self._interval_sec))
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
