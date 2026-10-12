"""segment別YES率の集計(A-04・09 §2.3仮説2の判定材料)。

09 §2.3の仮説2「Jevの意味的近接判定(semantic)は語彙一致(lexical)と同等以上に
提案受容を呼び込むか」の判定に必要な材料を導出する:

- weekly_segment_yes(calibration_records.prediction.segment × actual_responsesの
  回答値を週へ集計)= segment別の(YES数, 回答数)
- 仮説2の事前固定基準: semanticのYES率が10%以上 かつ lexicalのYES率の50%以上
  (09 §2.3)。判定は両条件のAND・材料が揃わない週は判定不能(no_data)

kpi_dailyへの組み込みは行わない(設計判断・根拠はmatrix §12): 仮説2の判定は
週次・Phase 2の一回物に近く、日次の集計基盤(kpi_daily)に常設行を持たせる
利益が判定材料の導出に対して小さいため。mlr/surveyと同型の純関数+CLI
(python -m latch.obs.segment)で導出する。

匿名化済み行(anonymized_at NOT NULL)はactual_responsesが回答種別+日付のみの
形式(anonymize.anonymize_responses)のため、answered_date(日付)を週集計に
使う。未匿名化行はanswered_at(ISO時刻)のJST日付を使う。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import JST, Clock

# 09 §2.3仮説2の固定値(変更はdocs改版を経る)
SEMANTIC_MIN_RATE = 0.10  # semanticのYES率10%以上
LEXICAL_HALF_FACTOR = 0.5  # かつlexicalのYES率の50%以上

_SQL_ROWS = text("""
    SELECT prediction, actual_responses FROM calibration_records
""")

AnswerRow = tuple[str, date]  # (value, answered_day)


def _day_of(answered_at: object) -> date | None:
    """ISO時刻文字列→JST暦日付。形式不明はNone。"""
    from datetime import datetime

    if not isinstance(answered_at, str) or len(answered_at) < 10:
        return None
    try:
        return datetime.fromisoformat(answered_at).astimezone(JST).date()
    except ValueError:
        # ISO形式でない(anonymize済みのYYYY-MM-DDを含む)→日付部分を直接解釈
        try:
            return date.fromisoformat(answered_at[:10])
        except ValueError:
            return None


def iter_answers(actual_responses: object) -> list[AnswerRow]:
    """actual_responses(jsonb)→(回答値, 回答日)のlist。集計の単位=回答1件。"""
    if isinstance(actual_responses, str):
        try:
            actual_responses = json.loads(actual_responses)
        except json.JSONDecodeError:
            return []
    if not isinstance(actual_responses, list):
        return []
    out: list[AnswerRow] = []
    for e in actual_responses:
        if not isinstance(e, dict):
            continue
        value = e.get("value")
        if value not in ("yes", "no", "defer"):
            continue
        day = None
        if e.get("answered_at") is not None:
            day = _day_of(e.get("answered_at"))
        elif e.get("answered_date") is not None:
            day = _day_of(e.get("answered_date"))
        if day is None:
            continue
        out.append((value, day))
    return out


def segment_of_prediction(prediction: object) -> str | None:
    """prediction.segment(lexical/semantic)。構造不明はNone(集計外)。"""
    if isinstance(prediction, str):
        try:
            prediction = json.loads(prediction)
        except json.JSONDecodeError:
            return None
    if not isinstance(prediction, dict):
        return None
    seg = prediction.get("segment")
    return seg if seg in ("lexical", "semantic") else None


def weekly_segment_yes(
    rows: list[tuple[object, object]], week_start: date
) -> dict[str, dict[str, int]]:
    """(prediction, actual_responses)行リスト→segment別の週集計。

    戻り= {"lexical": {"yes": n, "answers": n}, "semantic": {...}}
    (両segmentとも0件でもキーは置く=欠測との区別)。
    """
    week_end = week_start + timedelta(days=6)
    out = {
        "lexical": {"yes": 0, "answers": 0},
        "semantic": {"yes": 0, "answers": 0},
    }
    for prediction, responses in rows:
        segment = segment_of_prediction(prediction)
        if segment is None:
            continue
        for value, day in iter_answers(responses):
            if not (week_start <= day <= week_end):
                continue
            out[segment]["answers"] += 1
            if value == "yes":
                out[segment]["yes"] += 1
    return out


def yes_rate(counts: dict[str, int]) -> float | None:
    """(yes, answers)→YES率。分母0はNone(測定不能)。"""
    if counts["answers"] <= 0:
        return None
    return counts["yes"] / counts["answers"]


def hypothesis2_verdict(
    lexical_rate: float | None, semantic_rate: float | None
) -> dict:
    """09 §2.3仮説2の判定(両率が揃う場合のみ判定可能)。"""
    out: dict = {
        "semantic_min_rate": SEMANTIC_MIN_RATE,
        "lexical_half_factor": LEXICAL_HALF_FACTOR,
    }
    if semantic_rate is None or lexical_rate is None:
        out["verdict"] = "no_data"
        return out
    sem_ok = semantic_rate >= SEMANTIC_MIN_RATE
    half_ok = semantic_rate >= LEXICAL_HALF_FACTOR * lexical_rate
    out["semantic_ge_10pct"] = sem_ok
    out["semantic_ge_half_of_lexical"] = half_ok
    out["verdict"] = "pass" if (sem_ok and half_ok) else "fail"
    return out


async def report(
    engine: AsyncEngine, *, clock: Clock, week: date | None = None
) -> dict:
    """週のsegment別YES率と仮説2判定の材料(JSON出力用)。"""
    from latch.obs.mlr import last_completed_week_start

    week_start = (
        week if week is not None else last_completed_week_start(clock.jst_date())
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(_SQL_ROWS)).fetchall()
    counts = weekly_segment_yes(list(rows), week_start)
    lexical_rate = yes_rate(counts["lexical"])
    semantic_rate = yes_rate(counts["semantic"])
    return {
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "lexical": {
            **counts["lexical"],
            "yes_rate": round(lexical_rate, 4) if lexical_rate is not None else None,
        },
        "semantic": {
            **counts["semantic"],
            "yes_rate": round(semantic_rate, 4) if semantic_rate is not None else None,
        },
        "hypothesis2": hypothesis2_verdict(lexical_rate, semantic_rate),
    }


async def _build_engine_from_env():
    from latch.core.db import create_db_engine
    from latch.settings import Settings

    return create_db_engine(Settings())


def main() -> int:
    """CLI: python -m latch.obs.segment [--week YYYY-MM-DD]。"""
    import argparse

    parser = argparse.ArgumentParser(prog="latch.obs.segment", description=__doc__)
    parser.add_argument(
        "--week",
        default=None,
        help="集計週のweek_start(月曜・YYYY-MM-DD)。省略=直近の完了週",
    )
    args = parser.parse_args()

    async def run() -> dict:
        engine = await _build_engine_from_env()
        from latch.core.clock import SystemClock

        try:
            return await report(
                engine,
                clock=SystemClock(),
                week=date.fromisoformat(args.week) if args.week else None,
            )
        finally:
            await engine.dispose()

    print(json.dumps(asyncio.run(run()), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
