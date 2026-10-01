"""RetentionJob(30日定期削除)のunit試験(M3 ws-6 design §2.5・§4.1)。

スタブconnでSQL・cutoff・バッチ窓を検証。runループはClockを前進させる
sleep差し替え(_advancing_sleep — test_reset_job.py:128 と同一流儀)で
決定的に回す。実DBでの削除結果は integration/test_retention_job.py。
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from latch.core.clock import FakeClock
from latch.worker.retention import BATCH_LIMIT, RETENTION_DAYS, RetentionJob

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


class FakeResult:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class ScriptedConn:
    def __init__(self, results: list):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = list(results)

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return FakeResult(None, 0)


class ScriptedEngine:
    def __init__(self, results: list):
        self.conn = ScriptedConn(results)
        self.begins = 0

    def begin(self):
        self.begins += 1
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


def _sql(conn, i: int) -> str:
    return conn.calls[i][0]


async def test_run_once_sqls_cutoff_and_returns_counts():
    """detach→group削除→match削除の順・cutoff=now-30日・件数を返す。"""
    engine = ScriptedEngine(
        [
            FakeResult(None, 0),  # ① latches NULL化
            FakeResult(None, 3),  # ② group DELETE(3<500で終了)
            FakeResult(None, 2),  # ③ match DELETE
        ]
    )
    job = RetentionJob(engine=engine, clock=FakeClock(NOW))
    groups, matches = await job.run_once()
    assert (groups, matches) == (3, 2)
    assert engine.begins == 1  # 単一tx
    detach_sql = _sql(engine.conn, 0)
    assert "UPDATE latches" in detach_sql
    assert "group_candidate_id = NULL" in detach_sql
    gdel = _sql(engine.conn, 1)
    assert "DELETE FROM group_candidates" in gdel
    assert "updated_at < CAST(:cutoff AS timestamptz)" in gdel
    assert "LIMIT :batch_limit" in gdel
    cutoff = engine.conn.calls[1][1]["cutoff"]
    assert (NOW - cutoff).total_seconds() == RETENTION_DAYS * 86400
    assert engine.conn.calls[1][1]["batch_limit"] == BATCH_LIMIT
    assert "DELETE FROM match_candidates" in _sql(engine.conn, 2)
    assert "status" not in gdel  # 全status一律(pending含む・design §2.5)
    assert "status" not in _sql(engine.conn, 2)


async def test_run_once_batches_until_window_under_limit():
    """group削除がbatch_limit到達なら再周(各周でdetach→DELETE)。"""
    engine = ScriptedEngine(
        [
            FakeResult(None, 0),  # detach 1周目
            FakeResult(None, BATCH_LIMIT),  # group 1周目(=limit)
            FakeResult(None, 0),  # detach 2周目
            FakeResult(None, 120),  # group 2周目(<limitで終了)
            FakeResult(None, 7),  # match
        ]
    )
    job = RetentionJob(engine=engine, clock=FakeClock(NOW))
    groups, matches = await job.run_once()
    assert (groups, matches) == (BATCH_LIMIT + 120, 7)
    # 各周の順序: detachがgroup DELETEの直前(2周目もdetachから)
    assert "UPDATE latches" in _sql(engine.conn, 2)
    assert "DELETE FROM group_candidates" in _sql(engine.conn, 3)


def _advancing_sleep(clock: FakeClock, stop: asyncio.Event, stop_at: int):
    """待機秒だけClockを前進させるsleep差し替え(0時跨ぎの決定的再現)。
    test_reset_job.py と同一流儀 — stop_at回目の呼び出しでstopを立てrunを終了。"""
    calls: list[float] = []

    async def _sleep(seconds: float) -> None:
        calls.append(seconds)
        clock.set(clock.now() + timedelta(seconds=seconds))
        if len(calls) >= stop_at:
            stop.set()

    return _sleep, calls


async def test_run_waits_until_midnight_then_runs_once():
    """0時待機(秒数=next_jst_midnightとの差)→run_once→次周期待待ちで終了。"""
    clock = FakeClock(NOW)  # JST 2026-10-01 21:00 → 次0時まで3時間
    stop = asyncio.Event()
    sleep, calls = _advancing_sleep(clock, stop, stop_at=2)
    engine = ScriptedEngine(
        [FakeResult(None, 0), FakeResult(None, 0), FakeResult(None, 0)]
    )
    job = RetentionJob(engine=engine, clock=clock, sleep=sleep)
    await job.run(stop=stop)
    assert calls[0] == 3 * 3600.0  # 21:00→24:00(JST)
    assert engine.begins == 1  # 0時到達でrun_once実行
    assert calls[1] == 24 * 3600.0  # 次周期の待機(翌0時まで丸1日)


class FlakyConn:
    """1回目のexecuteでSQLAlchemyError・2回目以降は正常応答。"""

    def __init__(self):
        self.calls = 0

    async def execute(self, stmt, params=None):
        self.calls += 1
        if self.calls == 1:
            raise SQLAlchemyError("boom")
        return FakeResult(None, 0)


class FlakyEngine:
    def __init__(self):
        self.conn = FlakyConn()

    def begin(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


async def test_run_retries_after_failure_with_retry_sec():
    """run_once失敗(例外)→retry_sec待機→再試行で成功(翌0時まで放置しない)。"""
    clock = FakeClock(NOW)
    stop = asyncio.Event()
    sleep, calls = _advancing_sleep(clock, stop, stop_at=3)
    job = RetentionJob(engine=FlakyEngine(), clock=clock, retry_sec=300, sleep=sleep)
    await job.run(stop=stop)
    assert calls[0] == 3 * 3600.0  # 0時まで
    assert calls[1] == 300.0  # retry_secでの待機
    assert job._engine.conn.calls >= 4  # 1回目boom(1本)+2回目成功(3本)


def test_constants_pinned():
    """FR-22の保持期間と窓の固定値(design §2.5)。"""
    assert RETENTION_DAYS == 30
    assert BATCH_LIMIT == 500
