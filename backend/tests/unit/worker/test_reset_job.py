"""ResetJobのunit試験(M3 ws-2 design §2.5〜2.6・§4.1-4/5)。

スタブcost_store・スタブlatch(記録)・FakeClockで決定的に検証する。
0時跨ぎ・月末・年跨ぎはFakeClock.setとnext_jst_midnightの純関数性で再現。
"""

from datetime import UTC, date, datetime

from latch.core.clock import JST, FakeClock
from latch.worker.reset import ResetJob, _prev_month_key, next_jst_midnight

# JST 2026-10-01 21:00(月末・月初判定用に日付を都度setする)
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


class RecordingStore:
    """JevCostStoreスタブ(掃除メソッドの呼び出し記録)。"""

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self._error = error

    async def delete_daily(self, day: str) -> int:
        self.calls.append(("daily", day))
        if self._error is not None:
            raise self._error
        return 1

    async def delete_monthly(self, month: str) -> int:
        self.calls.append(("monthly", month))
        return 1

    async def scan_delete(self, pattern: str) -> int:
        self.calls.append(("scan", pattern))
        return 1


class RecordingLatch:
    """LatchEngineスタブ(drain呼び出し記録・初回失敗差し替え可)。"""

    def __init__(self, fail_first: bool = False):
        self.drains = 0
        self._fail_first = fail_first

    async def drain(self) -> None:
        self.drains += 1
        if self._fail_first and self.drains == 1:
            raise RuntimeError("drain boom")


def _job(*, clock, store=None, latch=None, retry_sec=300, sleep=None):
    return ResetJob(
        cost_store=store or RecordingStore(),
        clock=clock,
        latch=latch or RecordingLatch(),
        retry_sec=retry_sec,
        **({} if sleep is None else {"sleep": sleep}),
    )


# --- 1. next_jst_midnight(§4.1-4) ---


def test_next_jst_midnight_typical_evening():
    """JST 21:00 → 翌日JST 0時(= 当日UTC 15:00)。"""
    now = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)  # JST 21:00
    assert next_jst_midnight(now) == datetime(2026, 10, 1, 15, 0, 0, tzinfo=UTC)


def test_next_jst_midnight_exactly_midnight_returns_next_day():
    """JST 0時丁度 → 翌日0時(24時間後)。待機0秒を生まない。"""
    now = datetime(2026, 10, 1, 15, 0, 0, tzinfo=UTC)  # JST 10-02 00:00
    assert next_jst_midnight(now) == datetime(2026, 10, 2, 15, 0, 0, tzinfo=UTC)


def test_next_jst_midnight_month_end():
    """JST 10-31 23:59 → 11-01 00:00(月末跨ぎ)。"""
    now = datetime(2026, 10, 31, 14, 59, 0, tzinfo=UTC)  # JST 23:59
    assert next_jst_midnight(now) == datetime(2026, 10, 31, 15, 0, 0, tzinfo=UTC)


def test_next_jst_midnight_year_boundary():
    """JST 12-31 → 翌日=2027-01-01 00:00 JST(年跨ぎ)。"""
    now = datetime(2026, 12, 31, 14, 0, 0, tzinfo=UTC)  # JST 12-31 23:00
    assert next_jst_midnight(now) == datetime(2026, 12, 31, 15, 0, 0, tzinfo=UTC)


# --- 2. _prev_month_key ---


def test_prev_month_key_regular_and_year_boundary():
    assert _prev_month_key(date(2026, 11, 1)) == "202610"
    assert _prev_month_key(date(2027, 1, 1)) == "202612"  # 年跨ぎ
    assert _prev_month_key(date(2026, 2, 1)) == "202601"


# --- 3. run_once: 前日キー掃除 + 月初判定 + drain(§4.1-5) ---


async def test_run_once_cleans_prev_day_keys_and_drains():
    """月初(11-01)0時発火: 前日(10-31)の4キー+前月(10月)月次を削除しdrain。"""
    clock = FakeClock(datetime(2026, 11, 1, 0, 0, 5, tzinfo=JST))
    store, latch = RecordingStore(), RecordingLatch()
    await _job(clock=clock, store=store, latch=latch).run_once()
    assert ("daily", "20261031") in store.calls
    assert ("scan", "jev:exec:20261031:*") in store.calls
    assert ("scan", "jev:intent:*:20261031") in store.calls
    assert ("scan", "jev:user:*:20261031") in store.calls
    assert ("monthly", "202610") in store.calls  # 月初(day==1)
    assert latch.drains == 1  # 完了時に保留キュー再評価(引用#11・§2.6)
    assert len(store.calls) == 5


async def test_run_once_keeps_monthly_on_non_first_day():
    """月初以外(10-15): 月次キーの削除を呼ばない(当月キー残存)。"""
    clock = FakeClock(datetime(2026, 10, 15, 0, 0, 5, tzinfo=JST))
    store, latch = RecordingStore(), RecordingLatch()
    await _job(clock=clock, store=store, latch=latch).run_once()
    assert ("daily", "20261014") in store.calls
    assert all(kind != "monthly" for kind, _ in store.calls)
    assert latch.drains == 1
