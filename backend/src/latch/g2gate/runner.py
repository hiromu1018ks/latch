"""G2評価の実行経路(goldset-plan §11手順3〜4・design §2.8)。

各ペアを正規化テキスト(07 §4形式)へ組み立て、route別にGatewayの公開直呼び
IF(call_jev_first/call_jev_fallback)で1ペア1リクエスト。直列実行
(g1流儀・レート制限余裕 — 引用#13で520×2は制約にならない規模)。
JevOutputInvalidError・LLMErrorはそのペアの失敗として記録し継続する
(再試行しない — 07 §4・design §2.11-7)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from latch.llm.errors import JevOutputInvalidError, LLMError
from latch.llm.jev import JevJudgment, build_jev_text

ROUTES = ("first", "fallback")


@dataclass(frozen=True)
class PairOutcome:
    pair_id: str
    route: str
    judgment: JevJudgment | None
    error: str | None

    @property
    def mutual_score(self) -> float | None:
        if self.judgment is None:
            return None
        result = self.judgment.result
        return min(result["would_a_accept_b"], result["would_b_accept_a"])


def _classify(exc: Exception) -> str:
    if isinstance(exc, JevOutputInvalidError):
        return "JevOutputInvalidError"
    return type(exc).__name__


async def run_routes(
    gateway,
    goldset,
    *,
    route: str = "both",
    limit: int | None = None,
    echo: Callable[[str], None] | None = None,
) -> list[PairOutcome]:
    """route="both"はfirst→fallbackの順に全ペアを実行(--routeで限定可)。"""
    if route == "both":
        routes = ROUTES
    elif route in ROUTES:
        routes = (route,)
    else:
        raise ValueError(f"unknown route: {route!r} ({'/'.join(ROUTES)}/both)")
    pairs = goldset.pairs[:limit] if limit is not None else goldset.pairs
    outcomes: list[PairOutcome] = []
    for rt in routes:
        call = gateway.call_jev_first if rt == "first" else gateway.call_jev_fallback
        for pair in pairs:
            text_a = build_jev_text(goldset.inputs[pair.intent_a], label="Intent A")
            text_b = build_jev_text(goldset.inputs[pair.intent_b], label="Intent B")
            judgment: JevJudgment | None = None
            error: str | None = None
            try:
                judgment = await call(
                    intent_a=text_a,
                    intent_b=text_b,
                    intent_ids=[pair.intent_a, pair.intent_b],
                )
            except (JevOutputInvalidError, LLMError) as exc:
                error = _classify(exc)
            outcomes.append(
                PairOutcome(pair_id=pair.id, route=rt, judgment=judgment, error=error)
            )
            if echo is not None:
                echo(f"{rt} {pair.id}: {error or 'ok'}")
    return outcomes


def usage_totals(outcomes: list[PairOutcome]) -> dict[str, dict[str, int]]:
    """route別のusage合計(goldset-plan §8「実測時はusage(input_tokens)を記録」)。"""
    totals: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        if outcome.judgment is None or not outcome.judgment.usage:
            continue
        bucket = totals.setdefault(
            outcome.route, {"input_tokens": 0, "output_tokens": 0}
        )
        for key in ("input_tokens", "output_tokens"):
            bucket[key] += int(outcome.judgment.usage.get(key, 0))
    return totals
