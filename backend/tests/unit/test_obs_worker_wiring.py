"""Workerのobs配線(M4 ws-4・collector/KpiJob DI・start_http_server分岐)。

既存test_worker.pyの作法(FakeBus注入+run()タスク起動→即shutdown)を踏襲
する。KpiJob・collectorはスタブ注入で「run()内で再構築されずtask化される」
ことを確定的に確認する(awaitせずシャットダウンで完走)。
"""

import asyncio
from datetime import UTC, datetime

from latch.core.clock import FakeClock
from latch.settings import Settings
from latch.worker.main import Worker

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


class _FakeBus:
    """EventBusスタブ(subscribeを記録。ensure/closeも数える)。"""

    def __init__(self):
        self.subscribers: list = []
        self.ensured = 0
        self.closed = 0

    async def ensure(self):
        self.ensured += 1

    async def publish_match_event(self, *, event_type, intent_id, version):
        raise AssertionError("worker配線ではpublishしない")

    async def subscribe(self, on_message):
        self.subscribers.append(on_message)
        return _FakeSubscription()

    async def close(self):
        self.closed += 1


class _FakeSubscription:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class _StubPeriodicJob:
    """KpiJob/collectorの注入用スタブ(run(stop=...)でstop待ちする)。"""

    def __init__(self):
        self.started = False
        self.stop_seen = False

    async def run(self, stop=None):
        self.started = True
        if stop is not None:
            await stop.wait()
        self.stop_seen = True


def test_worker_accepts_injected_collector_and_kpi(fake_clock):
    """collector・kpiは注入可能(既存DI作法・デフォルトNone)。"""
    collector = object()
    kpi = object()
    worker = Worker(clock=fake_clock, collector=collector, kpi=kpi)
    assert worker._collector is collector
    assert worker._kpi is kpi


def test_worker_defaults_collector_and_kpi_to_none():
    worker = Worker(clock=FakeClock(NOW))
    assert worker._collector is None
    assert worker._kpi is None


async def test_worker_run_starts_http_server_only_when_port_positive(
    fake_clock, monkeypatch
):
    """obs_worker_metrics_port>0のときのみstart_http_serverを呼ぶ(既定0=ci無効)。"""
    calls: list[int] = []

    def fake_start(port, addr="0.0.0.0"):
        calls.append(port)

        class _Srv:
            def shutdown(self):
                pass

        return _Srv(), None

    monkeypatch.setattr("latch.worker.main.start_http_server", fake_start)
    settings = Settings(obs_worker_metrics_port=9100)
    collector, kpi = _StubPeriodicJob(), _StubPeriodicJob()
    worker = Worker(
        clock=fake_clock,
        settings=settings,
        bus=_FakeBus(),
        collector=collector,
        kpi=kpi,
    )
    task = asyncio.create_task(worker.run())
    # task化(スタブのrun開始)を待つ
    for _ in range(100):
        if collector.started and kpi.started:
            break
        await asyncio.sleep(0)
    assert calls == [9100]  # start_http_serverが9100で呼ばれた
    assert worker._collector is collector  # 再構築されない(注入資産のtask化)
    assert worker._kpi is kpi
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=1.0)  # シャットダウンで完走
    assert collector.stop_seen and kpi.stop_seen


async def test_worker_run_without_injection_builds_kpi_and_collector(fake_clock):
    """未注入時はrun()内でKpiJob/collectorが構築されtask化される。

    engine=object(): 未注入のままだとrun()が実DB(ci-db)へ接続しKpiJobの
    即tickが共有DBへ書く(unit試験のDB隔離・test_worker.pyの注記参照)。
    """
    settings = Settings(obs_worker_metrics_port=0)  # 既定=無効(ciと同一)
    worker = Worker(
        clock=fake_clock, settings=settings, bus=_FakeBus(), engine=object()
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.01)
    assert worker._kpi is not None
    assert worker._collector is not None
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=1.0)
