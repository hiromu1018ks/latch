"""テストモードのスタブプロバイダ(10 第1節「疑似イベント注入」)。

3系統のABCを実装する単一クラス。乱数・実時間参照なし=決定的
(同一入力・同一設定→同一応答)。応答はコンストラクタ引数で差し替え可能。
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import date

from latch.llm.errors import LLMProviderError
from latch.llm.providers import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    JevProvider,
    ParserProvider,
)

# 07 第2節 出力JSONスキーマ適合の固定応答(必須3フィールドを含む)
DEFAULT_PARSER_RESPONSE: dict = {
    "category": {"primary": "meal", "secondary": None},
    "alcohol_involved": False,
    "time": {
        "start": "2026-09-27T19:00:00+09:00",
        "end": None,
        "flexibility_minutes": None,
    },
    "location": {"name": "東京駅", "radius_m": None, "flexibility": None},
    "budget": {"max": None, "currency": "JPY"},
    "participants": {"min": None, "max": None},
    "soft_constraints": [],
    "negative_constraints": [],
    "ng_unverifiable": [],
}

# 07 第4節 7設問JSONスキーマ適合の固定応答(根拠なき0.50=規則3)
DEFAULT_JEV_RESPONSE: dict = {
    "would_a_accept_b": {"score": 0.5, "reason": "根拠なしのため0.50(規則3)"},
    "would_b_accept_a": {"score": 0.5, "reason": "根拠なしのため0.50(規則3)"},
    "purpose_fit": 0.5,
    "mood_fit": 0.5,
    "timing_fit": 0.5,
    "social_fit": 0.5,
    "latent_yes": 0.5,
}


def _stub_vector(text: str) -> list[float]:
    """入力テキスト由来の決定的768次元ベクトル。値に意味はない(スタブである以上)。"""
    values: list[float] = []
    block = 0
    while len(values) < EMBEDDING_DIMENSIONS:
        digest = hashlib.sha256(f"{block}:{text}".encode()).digest()
        values.extend(byte / 255.0 for byte in digest)
        block += 1
    return values[:EMBEDDING_DIMENSIONS]


class StubLLM(ParserProvider, EmbeddingProvider, JevProvider):
    """テストモード(10 第1節)。name="stub"。"""

    def __init__(
        self,
        *,
        parser_response: dict | None = None,
        embedding_response: list[float] | None = None,
        jev_response: dict | None = None,
        delay_parser_ms: int = 0,
        delay_embedding_ms: int = 0,
        delay_jev_ms: int = 0,
        fail_parser: bool = False,
        fail_embedding: bool = False,
        fail_jev: bool = False,
    ) -> None:
        self.name = "stub"
        self._parser_response = (
            parser_response if parser_response is not None else DEFAULT_PARSER_RESPONSE
        )
        self._embedding_response = embedding_response
        self._jev_response = (
            jev_response if jev_response is not None else DEFAULT_JEV_RESPONSE
        )
        self._delay_parser_ms = delay_parser_ms
        self._delay_embedding_ms = delay_embedding_ms
        self._delay_jev_ms = delay_jev_ms
        self._fail_parser = fail_parser
        self._fail_embedding = fail_embedding
        self._fail_jev = fail_jev

    def _delay_ms(self, system: str) -> int:
        """系統→遅延ms。p50/p95分布版(M4)への差し替えはこの1関数で完結する。"""
        return {
            "intent_parser": self._delay_parser_ms,
            "embedding": self._delay_embedding_ms,
            "jev": self._delay_jev_ms,
        }[system]

    async def _apply_delay(self, system: str) -> None:
        delay_ms = self._delay_ms(system)
        if delay_ms > 0:
            # 待機であり時刻参照ではない(arch testの禁止対象外。design §2.6)
            await asyncio.sleep(delay_ms / 1000)

    async def complete_structured(self, text: str, current_date: date) -> dict:
        await self._apply_delay("intent_parser")
        if self._fail_parser:
            raise LLMProviderError("stub: fail_parser=True")
        return self._parser_response

    async def embed(self, text: str) -> list[float]:
        await self._apply_delay("embedding")
        if self._fail_embedding:
            raise LLMProviderError("stub: fail_embedding=True")
        if self._embedding_response is not None:
            return self._embedding_response
        return _stub_vector(text)

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        await self._apply_delay("jev")
        if self._fail_jev:
            raise LLMProviderError("stub: fail_jev=True")
        return self._jev_response
