"""LLMGateway本体(design §2.1・§2.4)。Parser/Embedding/Jevの単一共通経路(C4)。

各系統メソッドは共通の形: asyncio.timeoutでProvider呼び出しを包み、成否に
かかわらずSendRecordを構築してsend_logで出し、成功なら応答を返し、timeoutは
LLMTimeoutError、プロバイダ例外はLLMProviderErrorで送出する。
送信記録が先・例外送出が後 — 失敗時も記録が漏れない。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from latch.core.clock import Clock
from latch.llm.anthropic import AnthropicParserProvider
from latch.llm.errors import (
    JevOutputInvalidError,
    LLMConnectionError,
    LLMError,
    LLMOverloadedError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from latch.llm.gemini import GeminiEmbeddingProvider
from latch.llm.jev import JevJudgment, validate_and_normalize
from latch.llm.providers import EmbeddingProvider, JevProvider, ParserProvider
from latch.llm.records import SendRecord, SendStatus, SystemName, send_log
from latch.llm.stub import StubLLM
from latch.settings import Settings

TIMEOUT_PARSER_S = 10.0  # 07 第1節(D-17)同期・再試行なし
TIMEOUT_EMBEDDING_S = 2.0  # 07 第1節 非同期・再試行なし
TIMEOUT_JEV_S = 6.0  # 07 第1節 非同期(timeoutは再試行せず縮退へ)


@dataclass(frozen=True)
class Timeouts:
    """系統別timeout(07 第1節の確定値がデフォルト)。

    unit試験で短縮注入するための上書き経路(design §2.4)。環境変数には
    出さない — docs確定値の恒久的な変更をenvで黙って行える経路を作らない。
    """

    parser_s: float = TIMEOUT_PARSER_S
    embedding_s: float = TIMEOUT_EMBEDDING_S
    jev_s: float = TIMEOUT_JEV_S


class LLMGateway:
    """3系統の外部LLM呼び出しの単一共通経路(C4)。送信記録とtimeoutをここで持つ。"""

    def __init__(
        self,
        *,
        clock: Clock,
        parser: ParserProvider,
        embedding: EmbeddingProvider,
        jev: JevProvider,
        jev_fallback: JevProvider | None = None,
        timeouts: Timeouts | None = None,
    ) -> None:
        self._clock = clock
        self._parser = parser
        self._embedding = embedding
        self._jev = jev
        # 未注入時は第一候補と同一(§9-5 — 既存のLLMGateway直構築試験を
        # 無変更で通すための既定。real構成ではbuild_worker_gatewayが明示渡し)
        self._jev_fallback = jev_fallback if jev_fallback is not None else jev
        self._timeouts = timeouts if timeouts is not None else Timeouts()

    async def parse_intent(
        self, *, text: str, current_date: date, user_id: str | None = None
    ) -> dict:
        """07 第2節。timeout 10秒・再試行なし。送信内容=ユーザー入力テキスト。

        design §1.3: Parser系統だけがユーザー生テキストを送る。
        """
        return await self._call(
            system="intent_parser",
            destination=self._parser.name,
            timeout_s=self._timeouts.parser_s,
            intent_ids=None,
            user_id=user_id,
            invoke=lambda: self._parser.complete_structured(text, current_date),
        )

    async def embed_intent(self, *, text: str, intent_id: str) -> list[float]:
        """07 第3節。正規化テキスト→768次元。timeout 2秒・再試行なし。"""
        return await self._call(
            system="embedding",
            destination=self._embedding.name,
            timeout_s=self._timeouts.embedding_s,
            intent_ids=[intent_id],
            user_id=None,
            invoke=lambda: self._embedding.embed(text),
        )

    async def judge_pair(
        self, *, intent_a: str, intent_b: str, intent_ids: list[str]
    ) -> JevJudgment:
        """07 第4節。2 Intent分の正規化テキスト→7設問JSON。timeout 6秒。

        第一候補=TypeSafe Jev。429/529/timeout/接続障害の4種でフォールバックLLM
        へ切替(07 §4切替表・design §2.2)。LLMProviderError(400系)と
        JevOutputInvalidError(出力検証失敗=実装不整合)は切替せず伝播する。
        切替時は各呼び出しが既存_callを通るため送信記録2件。フォールバック失敗
        (双障害)はそのまま伝播(D-15の縮退はLayer 4が記録に変換する)。
        """
        try:
            envelope = await self._jev_call(
                intent_a, intent_b, intent_ids, fallback=False
            )
            try:
                result = validate_and_normalize(envelope)
            except JevOutputInvalidError as exc:
                # 検証対象の経路を例外へ載せて再送出(§9-4 — Layer 4の内訳計上用)
                raise JevOutputInvalidError(str(exc), provider="typesafe_jev") from exc
            return JevJudgment(
                provider="typesafe_jev",
                model=_envelope_model(envelope),
                result=result,
            )
        except (LLMTimeoutError, LLMRateLimitError, LLMOverloadedError, LLMConnectionError):
            pass  # 07 §4の切替条件4種。LLMProviderError・JevOutputInvalidErrorは伝播
        envelope = await self._jev_call(intent_a, intent_b, intent_ids, fallback=True)
        try:
            result = validate_and_normalize(envelope)
        except JevOutputInvalidError as exc:
            raise JevOutputInvalidError(str(exc), provider="fallback_llm") from exc
        return JevJudgment(provider="fallback_llm", model=None, result=result)

    async def _jev_call(
        self, intent_a: str, intent_b: str, intent_ids: list[str], *, fallback: bool
    ) -> dict:
        """Jev呼び出し1回(fallback=TrueはフォールバックLLM側)。

        circuit breaker(ws-8)の注入ポイントは第一候補側(fallback=False)。
        本単位では実装しない(design §2.10)。
        """
        provider = self._jev_fallback if fallback else self._jev
        return await self._call(
            system="jev",
            destination=provider.name,
            timeout_s=self._timeouts.jev_s,
            intent_ids=intent_ids,
            user_id=None,
            invoke=lambda: provider.judge(intent_a, intent_b),
        )

    async def _call(
        self,
        *,
        system: SystemName,
        destination: str,
        timeout_s: float,
        intent_ids: list[str] | None,
        user_id: str | None,
        invoke: Callable[[], Awaitable[Any]],
    ) -> Any:
        """送信記録→応答/例外の共通経路。status: ok / timeout / error。"""
        occurred_at = self._clock.now()
        try:
            async with asyncio.timeout(timeout_s):
                result = await invoke()
        except asyncio.CancelledError:
            # 外部キャンセルも「送信した」事実には変わりない(08 第3節の開示要件)。
            # asyncio.timeout期限切れのキャンセルはここへ来る前にTimeoutErrorへ
            # 変換済みのため二重計上しない。キャンセルは飲み込まず伝播させる。
            send_log(
                SendRecord(
                    occurred_at=occurred_at,
                    system=system,
                    destination=destination,
                    status="error",
                    error_code="CancelledError",
                    intent_ids=intent_ids,
                    user_id=user_id,
                )
            )
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                status: SendStatus = "timeout"
                error_code = "LLMTimeoutError"
                wrapped: LLMError = LLMTimeoutError(
                    f"{system} timed out after {timeout_s}s"
                )
            elif isinstance(exc, LLMError):
                status = "error"
                error_code = type(exc).__name__
                wrapped = exc
            else:
                status = "error"
                error_code = type(exc).__name__
                wrapped = LLMProviderError(f"{system} provider failed: {error_code}")
            send_log(
                SendRecord(
                    occurred_at=occurred_at,
                    system=system,
                    destination=destination,
                    status=status,
                    error_code=error_code,
                    intent_ids=intent_ids,
                    user_id=user_id,
                )
            )
            raise wrapped from exc
        send_log(
            SendRecord(
                occurred_at=occurred_at,
                system=system,
                destination=destination,
                status="ok",
                error_code=None,
                intent_ids=intent_ids,
                user_id=user_id,
            )
        )
        return result


def _envelope_model(envelope: object) -> str | None:
    """envelopeから応答バージョンIDを取り出す(第一候補のJevJudgment.model)。"""
    model = envelope.get("model") if isinstance(envelope, dict) else None
    return str(model) if model is not None else None


def build_llm_gateway(
    clock: Clock,
    settings: Settings,
    *,
    parser_system_prompt: str | None = None,
    parser_output_schema: dict | None = None,
) -> LLMGateway:
    """設定からGatewayを構築する(design §2.7・ws-6 design §2.3/§2.4)。

    llm_mode="stub": 3系統すべてStubLLM(引数は無視 — 2引数呼び出し後方互換)。
    llm_mode="real": Parser系統のみAnthropicParserProvider(Embedding/Jevは
    StubLLM継続 — gemini・typesafe鍵は未設定・実装はM2)。鍵・プロンプト・
    スキーマの欠落はfail-fast(静かにスタブへ落ちない)。
    """
    stub = StubLLM(
        delay_parser_ms=settings.llm_stub_delay_parser_ms,
        delay_embedding_ms=settings.llm_stub_delay_embedding_ms,
        delay_jev_ms=settings.llm_stub_delay_jev_ms,
    )
    if settings.llm_mode == "stub":
        return LLMGateway(clock=clock, parser=stub, embedding=stub, jev=stub)
    if settings.llm_mode == "real":
        if not settings.llm_anthropic_api_key:
            raise ValueError("llm_mode='real' requires llm_anthropic_api_key")
        if parser_system_prompt is None or parser_output_schema is None:
            raise ValueError(
                "llm_mode='real' requires parser_system_prompt and parser_output_schema"
            )
        parser = AnthropicParserProvider(
            api_key=settings.llm_anthropic_api_key,
            system_prompt=parser_system_prompt,
            output_schema=parser_output_schema,
            base_url=settings.llm_anthropic_base_url,
        )
        return LLMGateway(clock=clock, parser=parser, embedding=stub, jev=stub)
    raise ValueError(f"unknown llm_mode: {settings.llm_mode!r} ('stub' or 'real')")


def build_embedding_gateway(clock: Clock, settings: Settings) -> LLMGateway:
    """Worker・スモーク用のGateway構築(M2 ws-2・design §2.8-B)。

    llm_mode="stub": 3系統すべてStubLLM(ci環境・unit/integration試験)。
    llm_mode="real": Embedding系統のみGeminiEmbeddingProvider実API
    (parser・jevはstub継続 — APIプロセスのbuild_llm_gateway契約は無変更)。
    gemini鍵の欠落はfail-fast(静かにスタブへ落ちない)。Jev系統のreal化は
    ws-5が同じ形で追加する。
    """
    stub = StubLLM(
        delay_parser_ms=settings.llm_stub_delay_parser_ms,
        delay_embedding_ms=settings.llm_stub_delay_embedding_ms,
        delay_jev_ms=settings.llm_stub_delay_jev_ms,
    )
    if settings.llm_mode == "stub":
        return LLMGateway(clock=clock, parser=stub, embedding=stub, jev=stub)
    if settings.llm_mode == "real":
        if not settings.llm_gemini_api_key:
            raise ValueError(
                "llm_mode='real' requires llm_gemini_api_key (embedding gateway)"
            )
        embedding = GeminiEmbeddingProvider(api_key=settings.llm_gemini_api_key)
        return LLMGateway(clock=clock, parser=stub, embedding=embedding, jev=stub)
    raise ValueError(f"unknown llm_mode: {settings.llm_mode!r} ('stub' or 'real')")
