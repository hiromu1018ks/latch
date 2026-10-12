"""MLR前期比−5pt判定とベースライン記録(A-03・09 §6 D-02・05 v0.8)。

D-02の「Phase 2の4週間のMLR週次値の中央値をベースラインとして記録し、
以降は前期比マイナス5ポイント以内を維持条件とする」の装置:

- weekly_mlr(kpi_dailyのmlr日次行を週へ集約)= 週のmatched数/proposed数
  (比率は保存しない規律どおり集計時に計算)
- baseline(4週の中央値をmlr_baselinesへ記録・追記のみ。判定は最新行)
- check(直近完了週のweekly_mlrとベースライン値・前週値との差分と
  −5ptフラグ。CLIはJSONで出力・違反時はWARNINGログも出す)

「前期比」の読み: D-02はベースライン(前期=密度構築完了後4週間)との比較を
維持条件とするため、**ベースライン値との差分を判定本体**とし、直前週との
差分は参考値として並記する(設計判断・根拠はmatrix §11)。
判定の実施(ベースライン記録そのもの)はPhase 2以降の運用であり、本モジュール
は判定装置とその試験まで(09 §6は仕様正本のまま)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.obs.kpi import week_of

logger = logging.getLogger("latch.kpi.mlr")

# 09 D-02の固定値(変更はdocs改版を経る)
WITHIN_BASELINE_PT = 5.0
BASELINE_WEEKS = 4

_SQL_WEEKLY_MLR = text("""
    SELECT
      (SELECT COALESCE(SUM(numerator), 0) FROM kpi_daily
        WHERE metric_key='mlr' AND day BETWEEN CAST(:week_start AS date)
                                          AND CAST(:week_end AS date)) AS num,
      (SELECT COALESCE(SUM(denominator), 0) FROM kpi_daily
        WHERE metric_key='mlr' AND day BETWEEN CAST(:week_start AS date)
                                          AND CAST(:week_end AS date)) AS den
""")

_SQL_LATEST_BASELINE = text("""
    SELECT value, source_weeks, recorded_at FROM mlr_baselines
    ORDER BY recorded_at DESC LIMIT 1
""")

_SQL_INSERT_BASELINE = text("""
    INSERT INTO mlr_baselines (value, source_weeks, recorded_at)
    VALUES (CAST(:value AS numeric), CAST(:source_weeks AS jsonb),
            CAST(:now AS timestamptz))
""")


def last_completed_week_start(today: date) -> date:
    """直近の完了週(日曜終了)のweek_start(月曜)。"""
    week_end = today - timedelta(days=1)
    while week_end.weekday() != 6:
        week_end -= timedelta(days=1)
    return week_of(week_end)


def weekly_mlr_value(num: int, den: int) -> float | None:
    """週のMLR(matched/proposed)。分母0はNone(測定不能・欠測扱い)。"""
    if den <= 0:
        return None
    return num / den


def median_of_weeks(values: Sequence[float | None]) -> float | None:
    """4週の中央値(None=欠測の週は除外。2週未満ならNone=記録不能)。"""
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return None
    return float(statistics.median(present))


def diff_pt(current: float, baseline: float) -> float:
    """ベースライン比の差分(ポイント)。current−baseline(負=低下)。"""
    return (current - baseline) * 100.0


def within_baseline(current: float, baseline: float) -> bool:
    """維持条件: 前期(ベースライン)比マイナス5ポイント以内(09 D-02)。"""
    return diff_pt(current, baseline) >= -WITHIN_BASELINE_PT


async def fetch_weekly_mlr(engine: AsyncEngine, week_start: date) -> float | None:
    """1週のweekly MLR(kpi_daily集約・比率はここで初めて計算)。"""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                _SQL_WEEKLY_MLR,
                {
                    "week_start": week_start,
                    "week_end": week_start + timedelta(days=6),
                },
            )
        ).first()
    if row is None:
        return None
    return weekly_mlr_value(int(row[0]), int(row[1]))


async def record_baseline(
    engine: AsyncEngine,
    *,
    clock: Clock,
    end_week_start: date,
    weeks: int = BASELINE_WEEKS,
) -> dict:
    """end_week_startを最終週とする直近`weeks`週の中央値を記録(追記)。"""
    starts = [end_week_start - timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]
    values = [await fetch_weekly_mlr(engine, ws) for ws in starts]
    median = median_of_weeks(values)
    if median is None:
        return {
            "recorded": False,
            "reason": "insufficient_weeks",
            "weeks": [
                {"week_start": ws.isoformat(), "weekly_mlr": v}
                for ws, v in zip(starts, values, strict=True)
            ],
        }
    source_weeks = [
        {
            "week_start": ws.isoformat(),
            "weekly_mlr": round(v, 6) if v is not None else None,
        }
        for ws, v in zip(starts, values, strict=True)
    ]
    async with engine.begin() as conn:
        await conn.execute(
            _SQL_INSERT_BASELINE,
            {
                "value": median,
                "source_weeks": json.dumps(source_weeks, ensure_ascii=False),
                "now": clock.now(),
            },
        )
    return {
        "recorded": True,
        "value": round(median, 6),
        "source_weeks": source_weeks,
    }


async def latest_baseline(engine: AsyncEngine) -> dict | None:
    """記録済みの最新ベースライン(None=未記録)。"""
    async with engine.connect() as conn:
        row = (await conn.execute(_SQL_LATEST_BASELINE)).first()
    if row is None:
        return None
    source = row[1]
    if isinstance(source, str):
        source = json.loads(source)
    return {"value": float(row[0]), "source_weeks": source, "recorded_at": row[2]}


async def check(engine: AsyncEngine, *, clock: Clock) -> dict:
    """直近完了週の判定材料(維持条件の判定+参考値)。違反時はWARNINGログ。"""
    week_start = last_completed_week_start(clock.jst_date())
    current = await fetch_weekly_mlr(engine, week_start)
    prev = await fetch_weekly_mlr(engine, week_start - timedelta(days=7))
    baseline = await latest_baseline(engine)
    out: dict = {
        "week_start": week_start.isoformat(),
        "weekly_mlr": round(current, 6) if current is not None else None,
        "prev_week_mlr": round(prev, 6) if prev is not None else None,
        "baseline": baseline,
    }
    if current is None:
        out["verdict"] = "no_data"
        return out
    if baseline is None:
        out["verdict"] = "no_baseline"
        return out
    baseline_value = float(baseline["value"])
    out["diff_vs_baseline_pt"] = round(diff_pt(current, baseline_value), 2)
    out["within_baseline"] = within_baseline(current, baseline_value)
    if prev is not None:
        out["diff_vs_prev_week_pt"] = round(diff_pt(current, prev), 2)
    out["verdict"] = "ok" if out["within_baseline"] else "below_baseline"
    if not out["within_baseline"]:
        logger.warning(
            "mlr below baseline %s",
            json.dumps(
                {
                    "week_start": out["week_start"],
                    "weekly_mlr": out["weekly_mlr"],
                    "baseline": round(baseline_value, 6),
                    "diff_pt": out["diff_vs_baseline_pt"],
                },
                ensure_ascii=False,
            ),
        )
    return out


async def _build_engine_from_env():
    from latch.core.db import create_db_engine
    from latch.settings import Settings

    return create_db_engine(Settings())


def main() -> int:
    """CLI: python -m latch.obs.mlr [baseline --week YYYY-MM-DD | show | check]。"""
    import argparse

    parser = argparse.ArgumentParser(prog="latch.obs.mlr", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_base = sub.add_parser("baseline", help="直近4週の中央値をベースライン記録")
    p_base.add_argument(
        "--week",
        required=True,
        help="最終週のweek_start(月曜・YYYY-MM-DD)。省略不可(誤記録防止)",
    )
    sub.add_parser("show", help="最新ベースラインの表示")
    sub.add_parser("check", help="直近完了週の前期比判定")
    args = parser.parse_args()

    async def run() -> dict:
        engine = await _build_engine_from_env()
        from latch.core.clock import SystemClock

        try:
            if args.cmd == "baseline":
                return await record_baseline(
                    engine,
                    clock=SystemClock(),
                    end_week_start=date.fromisoformat(args.week),
                )
            if args.cmd == "show":
                result = await latest_baseline(engine)
                return result if result is not None else {"baseline": None}
            return await check(engine, clock=SystemClock())
        finally:
            await engine.dispose()

    print(json.dumps(asyncio.run(run()), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
