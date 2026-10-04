"""テストモードのスタブプロバイダ(10 第1節「疑似イベント注入」)。

3系統のABCを実装する単一クラス。乱数・実時間参照なし=決定的
(同一入力・同一設定→同一応答)。応答はコンストラクタ引数で差し替え可能。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
from datetime import date

from latch.llm.errors import (
    LLMConnectionError,
    LLMOverloadedError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
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

# 10 §1 v0.4: System One互換のanswers形式(model+answers同キー+noul[0,1]・
# score[0,4]+confidence+usage)。スタブ値は決定的に固定(§9-7)
DEFAULT_JEV_RESPONSE: dict = {
    "model": "jev-1.13.0",
    "answers": {
        "would_a_accept_b": {"type": "noul", "noul": 0.5},
        "would_b_accept_a": {"type": "noul", "noul": 0.5},
        "latent_yes": {"type": "noul", "noul": 0.5},
        "purpose_fit": {"type": "score", "score": 2.0, "confidence": 0.5},
        "mood_fit": {"type": "score", "score": 2.0, "confidence": 0.5},
        "timing_fit": {"type": "score", "score": 2.0, "confidence": 0.5},
        "social_fit": {"type": "score", "score": 2.0, "confidence": 0.5},
    },
    "usage": {"input_tokens": 0, "output_tokens": 0},
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


# 決定的ハッシュ割付のレンジ境界(256スロット・ws-6 design §2.2案A)。
# 0〜127→p50値(50.0%)・128〜239→中間値(43.75%)・240〜255→p95値(6.25%)。
# 下位2グループの累積93.75%<95%のため・nearest-rank定義の95%ランク
# (ceil(0.95N)番目の標本)は常にp95値グループに含まれる
_DELAY_SPLIT_P50 = 128
_DELAY_SPLIT_MID = 240


def distributed_delay_ms(p50_ms: int, p95_ms: int, key: str) -> int:
    """決定的ハッシュ割付の遅延ms(10 §4.1のp50/p95分布再現・M4 ws-6)。

    sha256(key)の先頭バイトのレンジで50%/43.75%/6.25%へ振る。中間値は
    線形補間。同一key→同一遅延(10 §1「スタブで決定的にする」)。
    理論分布としてnearest-rankの50分位点=p50値・95分位点=p95値。
    機械検証はtests/unit/llm/test_llm_stub_latency.py(固定key集合で計画時
    実計算済み: keys=[str(i) for i in range(10000)]・p50=1000/p95=2500で
    counts={1000: 5058, 1750: 4329, 2500: 613}・p50ランク=1000・p95ランク=2500)。
    """
    slot = hashlib.sha256(key.encode()).digest()[0]
    if slot < _DELAY_SPLIT_P50:
        return p50_ms
    if slot < _DELAY_SPLIT_MID:
        return p50_ms + (p95_ms - p50_ms) // 2
    return p95_ms


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
        latency_parser_p50_ms: int = 0,
        latency_parser_p95_ms: int = 0,
        latency_jev_p50_ms: int = 0,
        latency_jev_p95_ms: int = 0,
        fail_parser: bool = False,
        fail_embedding: bool = False,
        fail_jev: bool = False,
        fail_jev_exc: str | None = None,
    ) -> None:
        self.name = "stub"
        # 防御的コピー: 消費者が応答を書き換えてもデフォルト応答(モジュール定数)や
        # 他インスタンスが汚染されない(決定性の前提。10 第1節)。
        self._parser_response = copy.deepcopy(
            parser_response if parser_response is not None else DEFAULT_PARSER_RESPONSE
        )
        self._embedding_response = (
            copy.deepcopy(embedding_response)
            if embedding_response is not None
            else None
        )
        self._jev_response = copy.deepcopy(
            jev_response if jev_response is not None else DEFAULT_JEV_RESPONSE
        )
        self._delay_parser_ms = delay_parser_ms
        self._delay_embedding_ms = delay_embedding_ms
        self._delay_jev_ms = delay_jev_ms
        # 分布遅延のp50/p95(M4 ws-6)。p50_ms>0で有効・既存固定msより優先
        self._latency_parser_p50_ms = latency_parser_p50_ms
        self._latency_parser_p95_ms = latency_parser_p95_ms
        self._latency_jev_p50_ms = latency_jev_p50_ms
        self._latency_jev_p95_ms = latency_jev_p95_ms
        self._fail_parser = fail_parser
        self._fail_embedding = fail_embedding
        self._fail_jev = fail_jev
        # 07 §4切替条件の例外種別指定(ws-8 design §2.3)。
        # None | "ratelimit" | "overloaded" | "timeout" | "connection"
        self._fail_jev_exc = fail_jev_exc

    def _delay_ms(self, system: str, key: str | None = None) -> int:
        """系統→遅延ms。分布設定非0(p50_ms>0)のとき決定的ハッシュ割付へ
        切替する(優先順位: 分布 > 固定ms・M4 ws-6 design §2.2案A)。keyは
        呼び出し入力由来の決定的文字列(parser=テキスト・jev=ペアテキスト)。
        embeddingは分布を作らない(10 §4.1に注入規定なし・YAGNI)。
        """
        if key is not None:
            cfg = {
                "intent_parser": (
                    self._latency_parser_p50_ms,
                    self._latency_parser_p95_ms,
                ),
                "jev": (self._latency_jev_p50_ms, self._latency_jev_p95_ms),
            }.get(system)
            if cfg is not None and cfg[0] > 0:
                return distributed_delay_ms(cfg[0], cfg[1], key)
        return {
            "intent_parser": self._delay_parser_ms,
            "embedding": self._delay_embedding_ms,
            "jev": self._delay_jev_ms,
        }[system]

    async def _apply_delay(self, system: str, key: str | None = None) -> None:
        delay_ms = self._delay_ms(system, key)
        if delay_ms > 0:
            # 待機であり時刻参照ではない(arch testの禁止対象外。既存規律)
            await asyncio.sleep(delay_ms / 1000)

    async def complete_structured(self, text: str, current_date: date) -> dict:
        await self._apply_delay("intent_parser", text)
        if self._fail_parser:
            raise LLMProviderError("stub: fail_parser=True")
        # 呼び出し毎に新しいコピーを返す(消費者の書き換えが内部状態を汚染しない)
        return copy.deepcopy(self._parser_response)

    async def embed(self, text: str) -> list[float]:
        await self._apply_delay("embedding")
        if self._fail_embedding:
            raise LLMProviderError("stub: fail_embedding=True")
        if self._embedding_response is not None:
            return copy.deepcopy(self._embedding_response)
        return _stub_vector(text)

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        await self._apply_delay("jev", f"{intent_a}|{intent_b}")
        if self._fail_jev_exc is not None:
            # fail_jev_excがfail_jevより優先(種別指定時はそちらを使う)
            if self._fail_jev_exc == "ratelimit":
                raise LLMRateLimitError("stub: fail_jev_exc=ratelimit")
            if self._fail_jev_exc == "overloaded":
                raise LLMOverloadedError("stub: fail_jev_exc=overloaded")
            if self._fail_jev_exc == "timeout":
                raise LLMTimeoutError("stub: fail_jev_exc=timeout")
            if self._fail_jev_exc == "connection":
                raise LLMConnectionError("stub: fail_jev_exc=connection")
            raise ValueError(
                f"fail_jev_excの不明な値: {self._fail_jev_exc!r}"
                "(ratelimit/overloaded/timeout/connection)"
            )
        if self._fail_jev:
            raise LLMProviderError("stub: fail_jev=True")
        return copy.deepcopy(self._jev_response)
