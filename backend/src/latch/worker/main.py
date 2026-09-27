"""Worker本体(design §3.1)。ci環境では api と同じイメージ・別プロセス(Worker 1)。

実処理(Embedding・Layer 1〜5等)はM2以降が追加する。雛形では起動・graceful
shutdown と Clock/Settings の明示的構築のみを担保する。
"""

from __future__ import annotations

import asyncio
import logging

from latch.core.clock import Clock, SystemClock
from latch.settings import Settings

logger = logging.getLogger(__name__)


class Worker:
    """継続実行の土台。Clock は起動時の1回の明示的構築(design §2.4 DI方式)。"""

    def __init__(
        self, clock: Clock | None = None, settings: Settings | None = None
    ) -> None:
        self.clock: Clock = clock if clock is not None else SystemClock()
        self.settings: Settings = settings if settings is not None else Settings()
        self._stop = asyncio.Event()

    def request_shutdown(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        logger.info("worker started (app_env=%s)", self.settings.app_env)
        await self._stop.wait()
        logger.info("worker stopped")
