"""`python -m latch.worker` の入口(composeのworkerサービスが使用)。"""

from __future__ import annotations

import asyncio
import logging
import signal

from latch.settings import Settings
from latch.worker.main import Worker


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker = Worker(settings=settings)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, worker.request_shutdown)
        await worker.run()

    asyncio.run(run())


if __name__ == "__main__":
    main()
