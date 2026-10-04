"""KpiJobのfixture DB実行(M4 ws-4・integration・test-ci)。

実データを伴うMLR計算の検証はstaging受け入れ(Task 13)が所有し、本試験は
「空DBに近い環境でrun_onceが例外なく終わり・kpi_dailyへ8指標×遡及4日分の
行が書かれる」こと(Review Focus 1)を所有する。
"""

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock
from latch.obs.kpi import KpiJob

pytestmark = pytest.mark.integration


async def test_kpi_job_writes_daily_rows_on_empty_source(db_engine):
    clock = SystemClock()
    job = KpiJob(engine=db_engine, clock=clock, backfill_days=4, interval_sec=60)
    await job.run_once()
    async with db_engine.connect() as conn:
        n_mlr = (
            await conn.execute(
                text("SELECT count(*) FROM kpi_daily WHERE metric_key='mlr'")
            )
        ).scalar()
        keys = {
            row[0]
            for row in await conn.execute(
                text("SELECT DISTINCT metric_key FROM kpi_daily")
            )
        }
    assert n_mlr == 4  # 遡及4日分
    assert keys >= {
        "mlr",
        "hold_expired_rate",
        "yes_rate",
        "intent_create_rate",
        "active_intent_per_dau",
        "candidate_generation_rate",
        "proposal_rate",
        "jev_executions_per_intent",
    }
    # 再実行で増えない(冪等・tickの毎回書込みを防ぐ)
    await job.run_once()
    async with db_engine.connect() as conn:
        n_mlr2 = (
            await conn.execute(
                text("SELECT count(*) FROM kpi_daily WHERE metric_key='mlr'")
            )
        ).scalar()
    assert n_mlr2 == n_mlr
    # 掃除(本試験が書いた行のみ・§2.2(c))
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM kpi_daily"))
        await conn.execute(text("DELETE FROM kpi_muted_user_snapshot"))
