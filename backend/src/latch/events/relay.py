"""フォールバックリレー(design §2.2-B)。

APIプロセス内で5秒周期に回し、created_at が既定30秒以上前のpending行のみ
を再publishする(publishはEventBusへ・SELECTはLIMIT付き・時刻はClock)。
通常処理(debounce 10秒+処理)はしきい値に到達しないため再publishゼロ。
APIのpublish失敗・Pub/Subメッセージ喪失・Worker長期停止を同一条件で回収。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.events.bus import EventBus
from latch.settings import Settings

logger = logging.getLogger(__name__)

_SELECT_STALE_PENDING = text("""
    SELECT event_type, source_intent_id, payload->>'version'
    FROM match_events
    WHERE status = 'pending' AND created_at < :threshold
    ORDER BY created_at
    LIMIT :limit
""")
_BATCH_LIMIT = 100


class FallbackRelay:
    def __init__(
        self, *, engine: AsyncEngine, clock: Clock, bus: EventBus, settings: Settings
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._bus = bus
        self._settings = settings

    async def run_once(self) -> int:
        threshold = self._clock.now() - timedelta(
            seconds=self._settings.event_fallback_relay_after_sec
        )
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _SELECT_STALE_PENDING, {"threshold": threshold, "limit": _BATCH_LIMIT}
            )
            rows = res.fetchall()
        published = 0
        for event_type, intent_id, version_text in rows:
            try:
                await self._bus.publish_match_event(
                    event_type=event_type,
                    intent_id=intent_id,
                    version=int(version_text),
                )
                published += 1
            except Exception:
                # publish失敗は握る(行はpendingのまま次周期で再対象 — design §2.2-B)
                logger.warning(
                    "relay publish failed event_type=%s intent_id=%s",
                    event_type,
                    intent_id,
                )
        return published

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        while stop is None or not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                # SELECT失敗(db一時障害)も握って次周期へ
                logger.warning("relay run_once failed", exc_info=True)
            await asyncio.sleep(self._settings.event_fallback_poll_sec)
