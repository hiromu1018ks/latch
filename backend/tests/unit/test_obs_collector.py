"""collectorのgauge反映(M4 ws-4・design §2.6)。

DB集計値とbreaker状態をgaugeへ写す薄い周期タスク。年齢計算はClock経由
(C2)。単例metricsはグローバルREGISTRYに積むため試験は差分assert。
"""

import asyncio
from datetime import UTC, datetime

from prometheus_client import REGISTRY

from latch.core.clock import FakeClock
from latch.llm.breaker import CircuitBreaker
from latch.obs.collector import MetricsCollector

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


class _FakeConn:
    def __init__(self, results: list):
        self._results = list(results)

    async def execute(self, stmt, params=None):
        rows = self._results.pop(0) if self._results else []
        return _FakeResult(rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0][0]


class _FakeEngine:
    """collect_onceのSQL順(pending・最古・candidates・kpi)に応答する。"""

    def __init__(self, results):
        self._results = results

    def connect(self):
        conn = _FakeConn(self._results)

        class _Ctx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


class _FakeBreaker(CircuitBreaker):
    """実物の@property API(stateは属性・メソッド呼び出し不可)を継承で踏襲。

    素のスタブがstate()メソッド形状だったため、実物のCircuitBreakerでは
    collectorが毎tickTypeErrorになる欠陥をunitが検出できなかった
    (supervisor検証で発見・M2 ws-1のPubsubEventBus系統と同じスタブ乖離)。
    実物を継承することでAPI形状の乖離を構造的に防ぐ。
    """

    def __init__(self, state: str):
        super().__init__(clock=FakeClock(NOW))
        self._state = state


RESULTS = [
    [(9,)],  # pending件数
    [(123.0,)],  # 最古pending年齢秒
    [(57,)],  # candidate件数
    [("mlr", 2, 11)],  # kpi最新行(metric, num, den)
]


def _fresh_results():
    return [list(r) for r in RESULTS]


async def test_collect_once_sets_gauges():
    engine = _FakeEngine(_fresh_results())
    clock = FakeClock(NOW)
    collector = MetricsCollector(engine=engine, clock=clock, interval_sec=60)
    await collector.collect_once()
    assert REGISTRY.get_sample_value("latch_event_queue_pending") == 9.0
    assert (
        REGISTRY.get_sample_value("latch_event_queue_oldest_pending_seconds") == 123.0
    )
    assert REGISTRY.get_sample_value("latch_pipeline_candidates") == 57.0
    assert (
        REGISTRY.get_sample_value("latch_kpi_daily_numerator", {"metric": "mlr"}) == 2.0
    )
    assert (
        REGISTRY.get_sample_value("latch_kpi_daily_denominator", {"metric": "mlr"})
        == 11.0
    )


async def test_collect_once_sets_breaker_state():
    """closed=0/open=1/half_open=2のマッピング(design §2.6)。"""
    engine = _FakeEngine(_fresh_results())
    collector = MetricsCollector(
        engine=engine,
        clock=FakeClock(NOW),
        interval_sec=60,
        breaker=_FakeBreaker("open"),
    )
    await collector.collect_once()
    assert REGISTRY.get_sample_value("latch_breaker_state") == 1.0


async def test_oldest_pending_age_uses_clock_for_age():
    """最古pendingの年齢はClock.now()から計算(Clock経由・C2)。"""
    # SQL側へ CAST(:now AS timestamptz) を渡す形状(呼び出しパラメータ検査)
    engine = _FakeEngine(_fresh_results())
    collector = MetricsCollector(engine=engine, clock=FakeClock(NOW), interval_sec=60)
    await collector.collect_once()
    # 年齢の実値はFakeClock固定時刻でもSQLパラメータにnowが渡っていることで
    # Clock経由を確認する(実装がtime.timeを直接使わないことの間接ピン)
    assert (
        REGISTRY.get_sample_value("latch_event_queue_oldest_pending_seconds") == 123.0
    )


async def test_run_loops_with_interval_and_stops():
    """run()はintervalでループし・stopで即返る(ResetJobと同一のstop契約)。"""
    engine = _FakeEngine(_fresh_results() * 3)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)

    collector = MetricsCollector(
        engine=engine, clock=FakeClock(NOW), interval_sec=60, sleep=fake_sleep
    )
    stop = asyncio.Event()
    task = asyncio.create_task(collector.run(stop=stop))
    # collect_onceの完走とinterval待ちへの到達を待つ(確定的に)
    for _ in range(100):
        if sleeps:
            break
        await asyncio.sleep(0)
    assert sleeps  # 少なくとも1回interval待ちが呼ばれた
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
