"""G1ゲートの実行経路(design §2.8-A・§2.11)。

Gateway+IntentParseServiceを本番ロジックそのまで通す(HTTP層を経ない)。
- current_date用ClockはFakeClock(基準日時 — README「共通の前提」)
- user_lookupは常にNone(未登録JWT経路・M1 ws-2 §2.3と同一挙動・DB非依存)
- 実行時刻(証拠の実施日)は__main__がSystemClockで別途取得(arch test対応)
- 呼び出しは直列(§2.11 — レート制限との余裕・精度測定が目的でレイテンシ計測はM4)
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from latch.core.clock import FakeClock
from latch.g1gate.cases import BASE_CURRENT_DATETIME, AlcoholSet, ParserStructSet
from latch.g1gate.compare import (
    AlcoholCaseOutcome,
    ErrorCaseOutcome,
    StructCaseOutcome,
    compare_alcohol_case,
    compare_struct_case,
    error_outcome,
)
from latch.intents.errors import IntentsError, LLMUnavailableError, UnstructurableError
from latch.intents.service import (
    IntentParseService,
    ParseResult,
    make_intent_parse_service,
)
from latch.settings import Settings

GATE_AUTH_PROVIDER = "g1gate"
GATE_AUTH_SUBJECT = "g1gate"

ERROR_CODE_UNSTRUCTURABLE = "422 VALIDATION_ERROR"
ERROR_CODE_LLM_UNAVAILABLE = "503 LLM_UNAVAILABLE"


async def _none_user_lookup(provider: str, subject: str) -> uuid.UUID | None:
    return None


def build_gate_service(settings: Settings) -> IntentParseService:
    """基準日時のFakeClock+未登録JWT経路でIntentParseServiceを構成(§2.8-A)。

    llm_mode/鍵/プロンプト/スキーマの検証はmake_intent_parse_service→
    build_llm_gatewayが持つ(fail-fast)。
    """
    clock = FakeClock(BASE_CURRENT_DATETIME)
    return make_intent_parse_service(
        clock=clock, settings=settings, user_lookup=_none_user_lookup
    )


async def _parse_classified(
    service: IntentParseService, text: str
) -> tuple[ParseResult | None, str | None]:
    """1ケース実行。errorは "unstructurable"|"llm_unavailable"|"unexpected:<Class>"。"""
    try:
        result = await service.parse(
            text=text,
            auth_provider=GATE_AUTH_PROVIDER,
            auth_subject=GATE_AUTH_SUBJECT,
        )
        return result, None
    except UnstructurableError:
        return None, "unstructurable"
    except LLMUnavailableError:
        return None, "llm_unavailable"
    except IntentsError as exc:
        return None, f"unexpected:{type(exc).__name__}"


def _http_code(error: str | None) -> str:
    """error種を05 §5のcode表記へ(error_casesのレポート用)。"""
    if error == "unstructurable":
        return ERROR_CODE_UNSTRUCTURABLE
    if error == "llm_unavailable":
        return ERROR_CODE_LLM_UNAVAILABLE
    return error or ""


def _is_incomplete(error: str | None) -> bool:
    """503系(LLMUnavailable・予期しないIntentsError)は実行不完全(design §2.9)。"""
    return error is not None and (
        error == "llm_unavailable" or error.startswith("unexpected")
    )


@dataclass(frozen=True)
class GateRunResult:
    struct_outcomes: list[StructCaseOutcome]
    alcohol_outcomes: list[AlcoholCaseOutcome]
    error_outcomes: list[ErrorCaseOutcome]
    incomplete: bool


async def run_all(
    service: IntentParseService,
    struct_set: ParserStructSet,
    alcohol_set: AlcoholSet,
    *,
    limit: int | None = None,
    echo: Callable[[str], None] | None = None,
) -> GateRunResult:
    """struct 32件+alcohol 36件+error 3件を直列実行し、照合結果を返す。

    limitは各リストの先頭N件へ適用(部分実行・デバッグ用。集計形式は同一)。
    """
    struct_cases = struct_set.cases[:limit] if limit is not None else struct_set.cases
    alcohol_cases = (
        alcohol_set.cases[:limit] if limit is not None else alcohol_set.cases
    )
    error_cases = (
        struct_set.error_cases[:limit] if limit is not None else struct_set.error_cases
    )
    incomplete = False

    struct_outcomes: list[StructCaseOutcome] = []
    for case in struct_cases:
        result, error = await _parse_classified(service, case.text)
        structured = result.structured_intent if result is not None else None
        struct_outcomes.append(compare_struct_case(case, structured, error))
        incomplete = incomplete or _is_incomplete(error)
        if echo is not None:
            echo(f"{case.id}: {'ok' if error is None else error}")

    alcohol_outcomes: list[AlcoholCaseOutcome] = []
    for case in alcohol_cases:
        result, error = await _parse_classified(service, case.text)
        structured = result.structured_intent if result is not None else None
        alcohol_outcomes.append(compare_alcohol_case(case, structured, error))
        incomplete = incomplete or _is_incomplete(error)
        if echo is not None:
            echo(f"{case.id}: {'ok' if error is None else error}")

    error_outcomes: list[ErrorCaseOutcome] = []
    for case in error_cases:
        _, error = await _parse_classified(service, case.text)
        error_outcomes.append(error_outcome(case, _http_code(error)))
        incomplete = incomplete or _is_incomplete(error)
        if echo is not None:
            echo(f"{case.id}: {_http_code(error) or 'ok'}")

    return GateRunResult(
        struct_outcomes=struct_outcomes,
        alcohol_outcomes=alcohol_outcomes,
        error_outcomes=error_outcomes,
        incomplete=incomplete,
    )
