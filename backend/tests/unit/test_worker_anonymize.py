"""D-13第二段月次匿名化(08 §3.4・V-02)のunit試験。

純関数(時間帯ビン・市区町村・人数ビン・responses/snapshot変換)と
AnonymizeJobのSQL・月初判定をスタブで検証する(test_worker_retention.py・
test_reset_job.pyと同一流儀。実DBでの変換結果は integration/test_anonymize_job.py)。
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.worker.anonymize import (
    AnonymizeJob,
    anonymize_responses,
    anonymize_snapshot,
    daypart_label,
    daypart_of_time_summary,
    headcount_bin,
    municipality_of,
)

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class ScriptedConn:
    def __init__(self, results: list):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = list(results)

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return FakeResult([])


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


# -- 純関数(08 §3.4の2・3) ---------------------------------------------------


def test_daypart_bins():
    assert daypart_label(10) == "昼"
    assert daypart_label(15) == "昼"
    assert daypart_label(16) == "夕"
    assert daypart_label(18) == "夕"
    assert daypart_label(19) == "夜"
    assert daypart_label(3) == "夜"
    assert daypart_label(9) == "夜"


def test_daypart_of_time_summary():
    assert daypart_of_time_summary("2026-10-01 20:00") == "夜"
    assert daypart_of_time_summary("2026-10-01 12:30") == "昼"
    assert daypart_of_time_summary(None) is None
    assert daypart_of_time_summary("short") is None
    assert daypart_of_time_summary("2026-10-01 xx:00") is None


def test_municipality():
    assert municipality_of("鹿児島市天文館") == "鹿児島市"
    assert municipality_of("鹿児島市泉町") == "鹿児島市"
    assert municipality_of("姶良郡湧水町木場") == "姶良郡湧水町"
    assert municipality_of("天文館") == "天文館"  # マッチしない地名はそのまま
    assert municipality_of(None) is None
    assert municipality_of("") is None


def test_headcount_bin():
    assert headcount_bin(2) == "2-3人"
    assert headcount_bin(3) == "2-3人"
    assert headcount_bin(4) == "4人"
    assert headcount_bin(None) is None
    assert headcount_bin("4") is None  # 型不明はビン化しない


def test_anonymize_responses_keeps_value_and_day_only():
    src = [
        {"user_id": "u-1", "value": "yes", "answered_at": "2026-10-01T20:11:22+09:00"},
        {"value": "defer", "answered_at": "2026-10-02T09:00:00+09:00"},
        {"value": "no", "answered_at": None},
    ]
    out = anonymize_responses(src)
    assert out == [
        {"value": "yes", "answered_date": "2026-10-01"},
        {"value": "defer", "answered_date": "2026-10-02"},
        {"value": "no"},
    ]
    # user_idは含まれない(第一段で除去済みだが仮に残っていても落とす)
    assert all("user_id" not in e for e in out)
    # 文字列jsonbも受ける(実DBはasyncpgでdict化済みだが安全側)
    as_text = json.dumps([{"value": "yes", "answered_at": "2026-10-01T20:00:00+09:00"}])
    assert anonymize_responses(as_text) == [
        {"value": "yes", "answered_date": "2026-10-01"}
    ]
    assert anonymize_responses("not-json") == []


def test_anonymize_snapshot_lowers_granularity():
    src = {
        "time_summary": "2026-10-01 20:00",
        "area_name": "鹿児島市天文館",
        "headcount": 3,
        "category_primary": "meal",
        "category_secondary": "焼肉",
        "budget": 5000,
        "match_level": "medium",
    }
    out = anonymize_snapshot(src)
    assert out == {
        "category_primary": "meal",
        "budget": 5000,
        "match_level": "medium",
        "time_of_day": "夜",
        "area_municipality": "鹿児島市",
        "headcount_bin": "2-3人",
    }
    # 除去対象が残っていない(対象日時・生地名・生人数・二次カテゴリ)
    for key in ("time_summary", "area_name", "headcount", "category_secondary"):
        assert key not in out


def test_anonymize_snapshot_minimal():
    """時間帯・地名が取れない行は当該キーを落とす(丸め過ぎないが残しもしない)。"""
    out = anonymize_snapshot({"headcount": 4, "category_primary": "drinking"})
    assert out == {"category_primary": "drinking", "headcount_bin": "4人"}
    assert anonymize_snapshot({}) == {}
    assert anonymize_snapshot("broken") == {}


# -- AnonymizeJob(SQL・月初判定) ---------------------------------------------


async def test_run_once_selects_first_stage_rows_and_updates():
    """第一段済み(latch_id NULL)かつ未変換(anonymized_at NULL)行を選び変換。"""
    engine = ScriptedEngine(
        [
            FakeResult(
                [
                    (
                        "r1",
                        [{"value": "yes", "answered_at": "2026-10-01T20:00:00+09:00"}],
                        {
                            "time_summary": "2026-10-01 20:00",
                            "area_name": "鹿児島市天文館",
                            "headcount": 2,
                            "category_primary": "meal",
                            "category_secondary": None,
                            "budget": None,
                            "match_level": "high",
                        },
                    ),
                ]
            ),
        ]
    )
    job = AnonymizeJob(engine=engine, clock=FakeClock(NOW))
    n = await job.run_once()
    assert n == 1
    select_sql, select_params = engine.conn.calls[0]
    assert "latch_id IS NULL" in select_sql
    assert "intent_ids IS NULL" in select_sql
    assert "anonymized_at IS NULL" in select_sql
    assert select_params == {"batch_limit": 500}
    update_sql, update_params = engine.conn.calls[1]
    assert "anonymized_at = CAST(:now AS timestamptz)" in update_sql
    assert "date_trunc('day', created_at AT TIME ZONE 'Asia/Tokyo')" in update_sql
    assert json.loads(update_params["responses"]) == [
        {"value": "yes", "answered_date": "2026-10-01"}
    ]
    assert json.loads(update_params["snapshot"]) == {
        "category_primary": "meal",
        "match_level": "high",
        "time_of_day": "夜",
        "area_municipality": "鹿児島市",
        "headcount_bin": "2-3人",
    }
    assert engine.begins == 1  # 単一tx


def _advancing_sleep(clock: FakeClock, stop: asyncio.Event, stop_at: int):
    """待機秒だけClockを前進させるsleep差し替え(test_reset_job.pyと同一流儀)。"""
    calls: list[float] = []

    async def _sleep(seconds: float) -> None:
        calls.append(seconds)
        clock.set(clock.now() + timedelta(seconds=seconds))
        if len(calls) >= stop_at:
            stop.set()

    return _sleep, calls


async def test_run_fires_only_on_month_first():
    """月中の0時は素通りし・月初(1日)0時にrun_onceする(注入sleepで決定的)。"""
    # JST 2026-10-15 06:00開始 → 10/16..10/31の0時16回は素通り → 11/1 0時で発火
    clock = FakeClock(datetime(2026, 10, 14, 21, 0, 0, tzinfo=UTC))
    engine = ScriptedEngine([FakeResult([])])
    stop = asyncio.Event()
    sleep, calls = _advancing_sleep(clock, stop, stop_at=18)
    job = AnonymizeJob(engine=engine, clock=clock, sleep=sleep)
    fired = 0
    orig = job.run_once

    async def counted() -> int:
        nonlocal fired
        fired += 1
        return await orig()

    job.run_once = counted  # type: ignore[method-assign]
    await job.run(stop=stop)
    assert fired == 1  # 11/1の0時1回のみ(月中16回の0時では呼ばれない)
    assert engine.begins == 1
