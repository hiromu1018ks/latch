"""体感アンケート回答の集計(05 v0.7・09 §2.3・仮説1/5の判定材料)。

kpi.pyの日次集計(JSO暦日と分子/分母)と性質が違い、アンケート集計は
「全回答を一括で読み、ラウンド別の平均・分布を出す」だけのため独立
モジュールとする(判定の実施はPhase 2以降・本モジュールは導出のみ)。

- 仮説1(待つ体験を便利と感じる): week4のconvenience平均が4.0以上
- 仮説5(検索しない様式が継続につながる): week4のcontinuationが4点以上の
  回答者が過半(行動面の再作成率・継続率はkpi.py既存の指標による)

純関数層(summarize)をunit試験が、CLI(python -m latch.obs.survey)を
運用手順(1コマンドで判定材料を出力)として使う。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# 09 §2.3の事前固定値(判定基準の変更はdocs改版を経る)
HYPOTHESIS1_CONVENIENCE_MEAN_MIN = 4.0
HYPOTHESIS5_GE4_RATIO_MIN = 0.5  # 「4点以上過半」= ge4_count / n > 0.5

ROUNDS = ("onboarding", "week4")


def _round_summary(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    convenience = [int(r["answers"]["convenience"]) for r in rows]
    continuation = [
        int(r["answers"]["continuation"])
        for r in rows
        if "continuation" in r["answers"]
    ]
    dist = {str(v): 0 for v in range(1, 6)}
    for v in continuation:
        dist[str(v)] += 1
    ge4 = sum(1 for v in continuation if v >= 4)
    summary: dict[str, Any] = {
        "n": n,
        "convenience_mean": (round(sum(convenience) / n, 2) if convenience else None),
        "continuation_distribution": dist,
        "continuation_ge4_count": ge4,
        "free_comment_count": sum(1 for r in rows if r.get("free_comment")),
    }
    if rows and rows[0].get("round") == "week4":
        # 判定はweek4ラウンドの材料に対して(仮説1は「4週後の5段階平均」)
        summary["hypothesis1_survey_pass"] = (
            summary["convenience_mean"] is not None
            and summary["convenience_mean"] >= HYPOTHESIS1_CONVENIENCE_MEAN_MIN
        )
        summary["hypothesis5_survey_pass"] = (
            n > 0 and ge4 > HYPOTHESIS5_GE4_RATIO_MIN * n
        )
    return summary


def summarize(rows: list[dict]) -> dict[str, Any]:
    """回答行(round/answers/free_comment)からラウンド別の集計を導出する。

    answersは5段階値のdict(convenience・continuation)。期待値表の所有は
    unit試験(kpi.pyの2層構造と同じ分け方・SQL実行層はfetch_rowsのみ)。
    """
    by_round: dict[str, list[dict]] = {round_: [] for round_ in ROUNDS}
    for row in rows:
        round_ = row.get("round")
        if round_ in by_round:
            by_round[round_].append(row)
    return {
        "onboarding": _round_summary(by_round["onboarding"]),
        "week4": _round_summary(by_round["week4"]),
    }


async def fetch_rows(engine: AsyncEngine) -> list[dict]:
    """survey_responsesの全行(answersをdictへ・順序はuser_id昇順で決定的)。"""
    async with engine.connect() as conn:
        rows = (await conn.execute(text(_SELECT_ROWS))).all()
    return [
        {
            "round": r[0],
            "answers": r[1] if isinstance(r[1], dict) else json.loads(r[1]),
            "free_comment": r[2],
        }
        for r in rows
    ]


_SELECT_ROWS = """
    SELECT round, answers, free_comment FROM survey_responses
     ORDER BY user_id, round
"""


async def build_summary(engine: AsyncEngine) -> dict[str, Any]:
    return summarize(await fetch_rows(engine))


def main() -> int:
    """判定材料を1コマンドで出力する運用手順(05 v0.7 集計)。"""
    import asyncio
    import sys

    import yaml

    from latch.core.db import create_db_engine
    from latch.settings import Settings

    async def run() -> dict[str, Any]:
        engine = create_db_engine(Settings())
        try:
            return await build_summary(engine)
        finally:
            await engine.dispose()

    summary = asyncio.run(run())
    yaml.safe_dump(summary, sys.stdout, allow_unicode=True, sort_keys=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
