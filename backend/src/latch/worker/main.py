"""Worker本体(design §3.1)。ci環境では api と同じイメージ・別プロセス(Worker 1)。

M2 ws-1: Pub/Sub subscriptionをストリーミングpullで消費し、第1段
(Stage1)+更新Eventのdebounce(TrailingDebouncer)へ配線する。ackは
DBのstatus遷移コミット後(design §2.3)。bus/engine/stage1/debouncerは
注入可能(unit試験)、未注入分はrun()で構築する。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from latch.core.clock import Clock, SystemClock
from latch.core.db import create_db_engine
from latch.events import EventBus, IncomingEvent, make_event_bus
from latch.settings import Settings
from latch.worker.debounce import DebounceEntry, DebounceGroup, TrailingDebouncer
from latch.worker.stage1 import Stage1

logger = logging.getLogger(__name__)

DEBOUNCE_TICK_SEC = 0.05  # 窓判定のtick(実時間の待機間隔。判定はClock基準)


class Worker:
    """継続実行の土台。Clock は起動時の1回の明示的構築(design §2.4 DI方式)。"""

    def __init__(
        self,
        clock: Clock | None = None,
        settings: Settings | None = None,
        *,
        bus: EventBus | None = None,
        engine=None,
        stage1: Stage1 | None = None,
        debouncer: TrailingDebouncer | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.clock: Clock = clock if clock is not None else SystemClock()
        self.settings: Settings = settings if settings is not None else Settings()
        self._bus = bus
        self._engine = engine
        self._stage1 = stage1
        self._debouncer = debouncer
        self._sleep = sleep
        self._stop = asyncio.Event()
        self._subscription = None

    def request_shutdown(self) -> None:
        self._stop.set()

    @property
    def bus(self) -> EventBus:
        """注入/構築済みのbus(テスト・配線確認用)。"""
        assert self._bus is not None, "bus未構築(run()未実施・未注入)"
        return self._bus

    async def run(self) -> None:
        owns_bus = self._bus is None
        owns_engine = self._engine is None
        if owns_bus:
            self._bus = make_event_bus(self.settings)
        bus = self._bus
        engine = (
            self._engine
            if self._engine is not None
            else create_db_engine(self.settings)
        )
        try:
            stage1 = (
                self._stage1
                if self._stage1 is not None
                else Stage1(
                    engine=engine,
                    clock=self.clock,
                    settings=self.settings,
                    sleep=self._sleep,
                )
            )
            debouncer = (
                self._debouncer
                if self._debouncer is not None
                else TrailingDebouncer(
                    clock=self.clock,
                    window_sec=self.settings.event_debounce_window_sec,
                    tick_sec=DEBOUNCE_TICK_SEC,
                    on_release=self._on_release,
                )
            )
            self._stage1 = stage1
            self._debouncer = debouncer
            await bus.ensure()
            self._subscription = await bus.subscribe(self._dispatch)
            debouncer_task = asyncio.create_task(debouncer.run(stop=self._stop))
            logger.info("worker started (app_env=%s)", self.settings.app_env)
            await self._stop.wait()
            # graceful shutdown: 窓内entryは解放せず未ack再配信へ(design §2.3)
            if self._subscription is not None:
                self._subscription.stop()
            await debouncer_task
            logger.info("worker stopped")
        finally:
            if owns_engine and engine is not None:
                await engine.dispose()
            if owns_bus:
                await bus.close()

    # -- 配線(bus.callback→ここ。asyncioループ内で実行される)--

    async def _dispatch(self, event: IncomingEvent) -> None:
        try:
            result = await self._stage1.intake(event)
            if result.kind in ("processed", "duplicate", "quarantined"):
                event.ack()
                return
            if result.kind == "debounce":
                assert result.triple is not None and result.row_id is not None
                accepted = self._debouncer.submit(
                    DebounceEntry(
                        event=event, triple=result.triple, row_id=result.row_id
                    )
                )
                if not accepted:
                    # 既知versionの再受信: 窓を延長せず即ack(代表は最初のメッセージ)
                    event.ack()
                return
        except Exception:
            # ackせず終了 → ack_deadline後に再配信(at-least-onceの回収)
            logger.exception("event dispatch failed message_id=%s", event.message_id)

    async def _on_release(self, group: DebounceGroup) -> None:
        """窓解放: latestを処理し、吸収行は統合理由でprocessed閉包(design §2.3)。"""
        try:
            await self._stage1.process(
                group.latest.event, group.latest.triple, group.latest.row_id
            )
            group.latest.event.ack()
            for entry in group.absorbed:
                await self._stage1.discard(entry.row_id, reason="debounced_superceded")
                entry.event.ack()
        except Exception:
            logger.exception("debounce release failed intent_id=%s", group.intent_id)
