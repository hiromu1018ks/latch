"""O-06週次警報のintegration試験(実DB・kpi_dailyへ直接投入してcheck_once)。

判定数学のunit試験(test_kpi_alert.py)に対し、ここはDB読取・単発化・
発報イベントの実往復を検証する。週境界は実Clockから導く(タイムボムなし)。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock
from latch.obs.kpi_alert import KpiAlertJob
from latch.obs.mlr import last_completed_week_start

pytestmark = pytest.mark.integration

PREFIX = "v1p4ka-"


@pytest.fixture(autouse=True)
async def _purge_kpi_rows(db_engine):
    yield
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM kpi_daily WHERE metric_key IN"
                " ('yes_rate','mute_rate','report_rate')"
            )
        )


def _week_start() -> date:
    return last_completed_week_start(SystemClock().jst_date())


def _prev_week_start() -> date:
    return _week_start() - timedelta(days=7)


def _days_of(week_start: date) -> list[date]:
    return [week_start + timedelta(days=i) for i in range(7)]


async def _put_daily(db_engine, metric: str, day, num: int, den: int) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO kpi_daily (metric_key, day, numerator, denominator,"
                " created_at, updated_at) VALUES (:k, CAST(:d AS date), :n,"
                " :den, now(), now()) ON CONFLICT (metric_key, day) DO UPDATE"
                " SET numerator = EXCLUDED.numerator,"
                " denominator = EXCLUDED.denominator"
            ),
            {"k": metric, "d": day, "n": num, "den": den},
        )


async def _job(db_engine) -> KpiAlertJob:
    fired: list[dict] = []

    def emitter(event: dict) -> None:
        fired.append(event)

    job = KpiAlertJob(
        engine=db_engine,
        clock=SystemClock(),
        redis=None,  # プロセス内単発化(試験の独立性)
        alert_emitter=emitter,
    )
    return job, fired


async def test_yes_rate_low_fires_once(db_engine):
    """完了週のYES率5%(分母40)→発報1回・再checkは単発化。"""
    week_start = _week_start()
    for day in _days_of(week_start):
        await _put_daily(db_engine, "yes_rate", day, 1, 40)
    job, fired = await _job(db_engine)
    events = await job.check_once()
    assert len(events) == 1
    assert events[0]["kind"] == "kpi_yes_rate_low"
    assert events[0]["week_start"] == week_start.isoformat()
    assert fired == events
    # 同週の再検査は発報しない(単発化)
    assert await job.check_once() == []


async def test_yes_rate_low_not_fired_below_min_den(db_engine):
    """週合計分母7(<10)はノイズ抑止で発報しない(min_denは週合計へ適用)。"""
    week_start = _week_start()
    for day in _days_of(week_start):
        await _put_daily(db_engine, "yes_rate", day, 0, 1)
    job, _ = await _job(db_engine)
    assert await job.check_once() == []


async def test_mute_rate_spike_fires(db_engine):
    """通知オフ率 1%→3%(週比3倍)=2倍以上→発報。"""
    week_start = _week_start()
    prev = _prev_week_start()
    await _put_daily(db_engine, "mute_rate", week_start, 30, 1000)
    await _put_daily(db_engine, "mute_rate", prev, 10, 1000)
    # yes_rateは判定不能にしておく(分母不足ではない空データ)
    job, _ = await _job(db_engine)
    events = await job.check_once()
    kinds = [e["kind"] for e in events]
    assert "kpi_mute_rate_spike" in kinds
    evt = next(e for e in events if e["kind"] == "kpi_mute_rate_spike")
    assert evt["curr_num"] == 30 and evt["prev_num"] == 10


async def test_report_spike_fires(db_engine):
    """通報率 2%→5%(週比2.5倍)=A/B有害シグナルも同一経路で発報。"""
    week_start = _week_start()
    prev = _prev_week_start()
    await _put_daily(db_engine, "report_rate", week_start, 50, 1000)
    await _put_daily(db_engine, "report_rate", prev, 20, 1000)
    job, _ = await _job(db_engine)
    events = await job.check_once()
    assert any(e["kind"] == "kpi_report_spike" for e in events)


async def test_no_data_no_events(db_engine):
    job, _ = await _job(db_engine)
    assert await job.check_once() == []


async def test_spike_not_fired_when_prev_zero(db_engine):
    """前週0→今週5%は「急増」の定義外(倍率不能)・発報しない。"""
    week_start = _week_start()
    prev = _prev_week_start()
    await _put_daily(db_engine, "report_rate", week_start, 50, 1000)
    await _put_daily(db_engine, "report_rate", prev, 0, 1000)
    job, _ = await _job(db_engine)
    events = await job.check_once()
    assert not any(e["kind"] == "kpi_report_spike" for e in events)
