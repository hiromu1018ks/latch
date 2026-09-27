"""Worker起動経路(FakeClock注入・即時shutdown。design §4.4)。"""

import asyncio

from latch.core.clock import SystemClock
from latch.worker.main import Worker


async def test_worker_accepts_injected_clock_and_stops_immediately(fake_clock):
    worker = Worker(clock=fake_clock)
    assert worker.clock is fake_clock  # 注入したClockをそのまま使う
    worker.request_shutdown()  # shutdownイベントを先にセット
    await asyncio.wait_for(worker.run(), timeout=1.0)  # 即座にgraceful終了


async def test_worker_defaults_to_system_clock():
    worker = Worker()
    assert isinstance(worker.clock, SystemClock)
    worker.request_shutdown()
    await asyncio.wait_for(worker.run(), timeout=1.0)


async def test_worker_run_returns_after_shutdown_request():
    worker = Worker()
    task = asyncio.create_task(worker.run())
    # run() が待機に入ることを許す(asyncio.sleepは待機であり時刻参照ではない)
    await asyncio.sleep(0)
    assert not task.done()
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=1.0)
