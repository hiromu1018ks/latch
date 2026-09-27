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
from latch.llm.errors import LLMError, LLMProviderError, LLMTimeoutError
from latch.llm.providers import EmbeddingProvider, JevProvider, ParserProvider
from latch.llm.records import SendRecord, SendStatus, SystemName, send_log

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
        timeouts: Timeouts | None = None,
    ) -> None:
        self._clock = clock
        self._parser = parser
        self._embedding = embedding
        self._jev = jev
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
    ) -> dict:
        """07 第4節。2 Intent分の正規化テキスト→7設問JSON。timeout 6秒。"""
        return await self._call(
            system="jev",
            destination=self._jev.name,
            timeout_s=self._timeouts.jev_s,
            intent_ids=intent_ids,
            user_id=None,
            invoke=lambda: self._jev.judge(intent_a, intent_b),
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
