"""QueueLagAlert: 閾値判定・単発化(アーム)・解除resolve・fail-open(M4 ws-7)。

collector.pyと同一のDB集計値(pending件数・最古pending年齢)を消費するが、
発報器自体はAPI側に置く(滞留の作り方=worker停止のため・design §2.5案A)。
年齢はClock差分で計算する(アプリ側・C2)。
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.obs.lag_alert import ARM_KEY, QueueLagAlert

NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)


class _StubResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _StubConn:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        return _StubResult(self._row)


class _StubEngine:
    def __init__(self, rows):
        self._rows = list(rows) if rows is not None else None
        self.connect_calls = 0

    def connect(self):
        if self._rows is None:
            raise RuntimeError("stub db down")
        row = self._rows.pop(0) if self._rows else (0, None)
        self.connect_calls += 1
        return _StubConn(row)


class _Events:
    def __init__(self):
        self.alerts: list[dict] = []
        self.resolves: list[dict] = []

    def alert(self, event):
        self.alerts.append(event)

    def resolve(self, event):
        self.resolves.append(event)


def _alert(engine, redis, *, pending=1000, oldest=300, events=None, interval=60):
    events = events or _Events()
    q = QueueLagAlert(
        engine=engine,
        clock=FakeClock(NOW),
        redis=redis,
        pending_threshold=pending,
        oldest_threshold=oldest,
        arm_ttl_s=180,
        interval_sec=interval,
        alert_emitter=events.alert,
        resolve_emitter=events.resolve,
    )
    return q, events


@pytest.fixture
async def redis():
    import fakeredis.aioredis

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


async def test_threshold_exceeded_emits_alert_once(redis):
    """pending件数閾値超過で1回発報・イベント辞書は数値を含む。"""
    engine = _StubEngine([(1500, None)])
    q, events = _alert(engine, redis, pending=1000, oldest=300)
    await q.check_once()
    assert len(events.alerts) == 1
    assert events.alerts[0]["kind"] == "queue_lag"
    assert events.alerts[0]["pending"] == 1500
    assert events.alerts[0]["oldest_pending_seconds"] == 0.0


async def test_alert_not_repeated_while_condition_holds(redis):
    """条件成立が続く限り2回目のcheckで再発報しない(プロセス内フラグ)。"""
    engine = _StubEngine([(1500, None), (1500, None)])
    q, events = _alert(engine, redis, pending=1000, oldest=300)
    await q.check_once()
    await q.check_once()
    assert len(events.alerts) == 1


async def test_condition_cleared_emits_resolve_and_rearms(redis):
    """条件解除でresolve 1回+フラグ解除・再び超過すると再発報できる。"""
    engine = _StubEngine([(1500, None), (0, None), (0, None), (9999, None)])
    q, events = _alert(engine, redis, pending=1000, oldest=300)
    await q.check_once()  # alert
    await q.check_once()  # cleared → resolve
    await q.check_once()  # cleared → 何もしない
    await q.check_once()  # 再超過 → alert
    assert len(events.alerts) == 2
    assert len(events.resolves) == 1
    assert events.resolves[0]["kind"] == "queue_lag_resolve"
    # resolve時のアーム解除は2回目の発報が成功したこと(SET NX成功=解除済み)
    # で証明済み。最終状態は再発報による再アームで「1」が正(計画テストコード
    # の欠陥修正: 4回目check後にNoneを要求するのは再アームと矛盾・2026-10-05)
    assert await redis.get(ARM_KEY) == "1"


async def test_redis_arm_key_prevents_cross_instance_duplicate(redis):
    """api1が発報済み(アーム済み)のとき・同一Redisを参照するapi2は発報しない。"""
    events1, events2 = _Events(), _Events()
    q1, _ = _alert(
        _StubEngine([(1500, None)]), redis, pending=1000, oldest=300, events=events1
    )
    q2, _ = _alert(
        _StubEngine([(1500, None)]), redis, pending=1000, oldest=300, events=events2
    )
    await q1.check_once()
    await q2.check_once()
    assert len(events1.alerts) == 1
    assert len(events2.alerts) == 0
    assert await redis.get(ARM_KEY) is not None


async def test_age_computed_from_clock_difference(redis):
    """最古pending年齢はClock差分で計算する(SQL側でなく・C2)。"""
    old = NOW - timedelta(seconds=400)
    engine = _StubEngine([(2, old)])
    q, events = _alert(engine, redis, pending=1000, oldest=300)
    await q.check_once()
    # 件数(2)は閾値未満・年齢(400秒)が閾値超過で発報する
    assert len(events.alerts) == 1
    assert events.alerts[0]["oldest_pending_seconds"] == 400.0


async def test_redis_failure_still_alerts(redis, monkeypatch):
    """Redis故障時も発報は落とさない(観測系fail-open・単発化のみ劣化)。"""
    import unittest.mock

    engine = _StubEngine([(1500, None)])
    q, events = _alert(engine, redis, pending=1000, oldest=300)
    with unittest.mock.patch.object(redis, "set", side_effect=ConnectionError("down")):
        await q.check_once()
    assert len(events.alerts) == 1


async def test_db_error_is_swallowed_and_run_continues(redis):
    """DB失敗は握って次tickへ(collector.runと同一契約)。stopで確実に抜ける。"""
    engine = _StubEngine([])
    engine._rows = None  # connect()が例外を投げる=DB故障の注入
    q, events = _alert(engine, redis, pending=1000, oldest=300, interval=0.01)
    stop = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.05)
        stop.set()

    task = asyncio.create_task(stop_soon())
    await q.run(stop=stop)  # 例外が外へ漏れない・stopで終了
    await task
    assert events.alerts == []


async def test_arm_ttl_is_set_on_redis_key(redis):
    """アームキーはSET NX+TTLで立てる(TTL失効で再武装できる)。"""
    engine = _StubEngine([(1500, None)])
    q, _ = _alert(engine, redis, pending=1000, oldest=300)
    await q.check_once()
    assert await redis.get(ARM_KEY) == "1"
    assert 0 < await redis.ttl(ARM_KEY) <= 180
