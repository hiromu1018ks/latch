"""parseユースケース(design §2.7)。

IntentParseService 本体は LLM 非依存・DB非依存: Gateway は
SupportsParseIntent Protocol への構造的適合(design §2.2)、user_lookup は
Callable注入。フロー: user_lookup(失敗→503)→ parse_intent(current_date=
clock.jst_date()、失敗→503 LLM_UNAVAILABLE)→ 規則5正規化 →
ParserOutput.model_validate(ValidationError→422)→ warnings構築。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from pydantic import ValidationError

from latch.core.clock import Clock
from latch.intents.errors import (
    DependencyUnavailableError,
    LLMUnavailableError,
    UnstructurableError,
)
from latch.intents.schema import WARNING_MESSAGE_NG_DOWNGRADED, ParserOutput
from latch.llm.gateway import build_llm_gateway
from latch.settings import Settings

UserLookup = Callable[[str, str], Awaitable[uuid.UUID | None]]


class SupportsParseIntent(Protocol):
    """Parser系統の構造的Protocol(design §2.2)。LLMGateway.parse_intent と適合。

    失敗は Gateway 契約どおり LLMTimeoutError / LLMProviderError
    (latch.llm.errors)を送出する(サービスは基底をimportせず
    Exception として受ける — intents/のllm非依存を守るため)。
    """

    async def parse_intent(
        self, *, text: str, current_date: date, user_id: str | None = None
    ) -> dict: ...


@dataclass(frozen=True)
class ParseWarning:
    """D-04の注意表示1件(05 §5応答例のwarnings要素と同形)。"""

    code: str  # "NG_CONDITION_DOWNGRADED"
    condition: str
    message: str  # WARNING_MESSAGE_NG_DOWNGRADED


@dataclass(frozen=True)
class ParseResult:
    """parseユースケースの結果(routes が応答へ変換する)。"""

    structured_intent: ParserOutput
    warnings: list[ParseWarning]


def _normalize_rule5(raw: dict) -> dict:
    """規則5違反の回復(design §2.5)。

    negative_constraints 非空(LLM違反)の要素を ng_unverifiable へ結合し、
    negative_constraints は空配列で応答する(FR-42の不変式回復。「常に空」の
    回復できる唯一の場所=parse境界)。結合由来の条件は warnings 生成対象に
    なるため D-04 の注意表示も出る(黙って降格させない)。
    """
    normalized = dict(raw)
    negative = normalized.get("negative_constraints") or []
    ng = list(normalized.get("ng_unverifiable") or [])
    normalized["negative_constraints"] = []
    normalized["ng_unverifiable"] = [*ng, *negative]
    return normalized


class IntentParseService:
    """POST /v1/intents/parse のユースケース(同期・再試行なしはGateway側)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        parser: SupportsParseIntent,
        user_lookup: UserLookup,
    ) -> None:
        self._clock = clock
        self._parser = parser
        self._user_lookup = user_lookup

    async def parse(
        self, *, text: str, auth_provider: str, auth_subject: str
    ) -> ParseResult:
        try:
            user_id = await self._user_lookup(auth_provider, auth_subject)
        except Exception as exc:
            raise DependencyUnavailableError(
                "intent parse dependency unavailable"
            ) from exc
        try:
            raw = await self._parser.parse_intent(
                text=text,
                current_date=self._clock.jst_date(),
                user_id=str(user_id) if user_id is not None else None,
            )
        except Exception as exc:
            # Gateway契約上ここで飛ぶのはLLMError系(timeout・API障害)のみ
            # (design §2.7)。検証(ValidationError)は後段なので含まれない。
            raise LLMUnavailableError("intent parser unavailable") from exc
        if not isinstance(raw, dict):
            raise UnstructurableError("structured intent is not extractable")
        try:
            structured = ParserOutput.model_validate(_normalize_rule5(raw))
        except ValidationError as exc:
            raise UnstructurableError("structured intent is not extractable") from exc
        warnings = [
            ParseWarning(
                code="NG_CONDITION_DOWNGRADED",
                condition=condition,
                message=WARNING_MESSAGE_NG_DOWNGRADED,
            )
            for condition in structured.ng_unverifiable
        ]
        return ParseResult(structured_intent=structured, warnings=warnings)


def make_intent_parse_service(
    *, clock: Clock, settings: Settings, user_lookup: UserLookup
) -> IntentParseService:
    """設定からIntentParseServiceを構築する(design §2.7)。

    llm/へのimport(build_llm_gateway)はこのファクトリに限る — サービス本体は
    LLM非依存(design §2.2)。llm_mode="stub" は build_llm_gateway が検証する
    (M0と同一パターン)。
    """
    gateway = build_llm_gateway(clock, settings)
    return IntentParseService(clock=clock, parser=gateway, user_lookup=user_lookup)
