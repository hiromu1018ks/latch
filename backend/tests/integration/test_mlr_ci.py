"""A-03 MLRベースライン記録・前期比判定のintegration試験(実DB)。

kpi_dailyへmlr行を直接投入し、record_baseline(4週中央値の記録)と
check(ベースライン比−5pt判定・WARNINGログ)の往復を検証する。
週境界は実Clockから導く(タイムボムなし)。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock
from latch.obs.mlr import (
    check,
    fetch_weekly_mlr,
    last_completed_week_start,
    record_baseline,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _purge(db_engine):
    yield
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM mlr_baselines"))
        await conn.execute(text("DELETE FROM kpi_daily WHERE metric_key = 'mlr'"))


async def _put_mlr(db_engine, week_start, num: int, den: int) -> None:
    """1週分のmlr日次行(週集計はSUMで行われるため月曜へ全量・他6日は0)。"""
    async with db_engine.begin() as conn:
        for i in range(7):
            await conn.execute(
                text(
                    "INSERT INTO kpi_daily (metric_key, day, numerator,"
                    " denominator, created_at, updated_at) VALUES ('mlr',"
                    " CAST(:d AS date), :n, :den, now(), now())"
                    " ON CONFLICT (metric_key, day) DO UPDATE SET"
                    " numerator = EXCLUDED.numerator,"
                    " denominator = EXCLUDED.denominator"
                ),
                {
                    "d": week_start + timedelta(days=i),
                    "n": num if i == 0 else 0,
                    "den": den if i == 0 else 0,
                },
            )


async def test_record_baseline_and_check_within(db_engine):
    """4週[0.30, 欠測, 0.20, 0.10]→中央値0.20を記録。0.19は−1pt=維持。"""
    end = last_completed_week_start(SystemClock().jst_date())
    await _put_mlr(db_engine, end, 300, 1000)  # 0.30
    # end-1週: 欠測(行なし)
    await _put_mlr(db_engine, end - timedelta(days=14), 200, 1000)  # 0.20
    await _put_mlr(db_engine, end - timedelta(days=21), 100, 1000)  # 0.10
    recorded = await record_baseline(db_engine, clock=SystemClock(), end_week_start=end)
    assert recorded["recorded"] is True
    assert recorded["value"] == pytest.approx(0.20)
    assert len(recorded["source_weeks"]) == 4
    # source_weeksは古い週から昇順: [0.10, 0.20, 欠測, 0.30]
    assert recorded["source_weeks"][2]["weekly_mlr"] is None  # 欠測週の記録

    # 完了週の値を0.19へ置き換えて判定(−1pt=維持条件内)
    await _put_mlr(db_engine, end, 190, 1000)
    result = await check(db_engine, clock=SystemClock())
    assert result["verdict"] == "ok"
    assert result["within_baseline"] is True
    assert result["diff_vs_baseline_pt"] == pytest.approx(-1.0)


async def test_check_below_baseline_warns(db_engine):
    """ベースライン0.20に対し0.14(−6pt)=違反・verdict below_baseline。"""
    end = last_completed_week_start(SystemClock().jst_date())
    for i in range(4):
        await _put_mlr(db_engine, end - timedelta(days=7 * i), 200, 1000)
    await record_baseline(db_engine, clock=SystemClock(), end_week_start=end)
    await _put_mlr(db_engine, end, 140, 1000)
    result = await check(db_engine, clock=SystemClock())
    assert result["verdict"] == "below_baseline"
    assert result["within_baseline"] is False
    assert result["diff_vs_baseline_pt"] == pytest.approx(-6.0)


async def test_check_without_baseline_or_data(db_engine):
    """ベースライン未記録はno_baseline・データなき週はno_data(誤判定しない)。"""
    result = await check(db_engine, clock=SystemClock())
    assert result["verdict"] in ("no_data", "no_baseline")

    end = last_completed_week_start(SystemClock().jst_date())
    await _put_mlr(db_engine, end, 10, 1000)
    result = await check(db_engine, clock=SystemClock())
    assert result["verdict"] == "no_baseline"


async def test_rerecording_appends_and_latest_wins(db_engine):
    """再ベースラインは行追加・判定はrecorded_at最新行(A/B採用後の再計測)。"""
    end = last_completed_week_start(SystemClock().jst_date())
    for i in range(4):
        await _put_mlr(db_engine, end - timedelta(days=7 * i), 200, 1000)
    await record_baseline(db_engine, clock=SystemClock(), end_week_start=end)
    for i in range(4):
        await _put_mlr(db_engine, end - timedelta(days=7 * i), 400, 1000)
    await record_baseline(db_engine, clock=SystemClock(), end_week_start=end)
    async with db_engine.connect() as conn:
        n = int(
            (
                await conn.execute(text("SELECT count(*) FROM mlr_baselines"))
            ).scalar_one()
        )
    assert n == 2
    value = await fetch_weekly_mlr(db_engine, end)
    assert value == pytest.approx(0.4)
    result = await check(db_engine, clock=SystemClock())
    assert result["baseline"]["value"] == pytest.approx(0.4)
