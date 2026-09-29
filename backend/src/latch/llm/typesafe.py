"""第一候補Jevプロバイダ(TypeSafe AI System One。07 §4・design §1.3 T1〜T7)。

PyPIパッケージ `typesafe-sdk`(実装時確認: ==0.7.2)・retry引数は `retry`
(design §5-4予測どおり)。SDK既定のbackoff retry(RetryPolicy.max_retries=2・
backoff 0.5〜5.0s)は無効化する(T5: 再試行なし — 429/529/timeoutは
Gatewayの切替条件で吸収し二重払いを防ぐ)。
SDK署名準拠の差分(§0規律「docs確定値とSDK署名が異なるときはSDK署名に
合わせる・定数・値は不変」): system_oneのquestions引数はSDK pydanticモデル
(Noul/Score)を期待するため、llm/jev.pyのJEV_QUESTIONS定数(単一の真実)から
model_validateで変換する。wire形式は定数と完全一致。
SDK例外はjudge()内で翻訳するのみで握らない。メッセージにIntent本文を入れない
(08 §2.4「例外・エラーはIDのみ」)。
"""

from __future__ import annotations

from typing import Any

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Noul,
    RetryPolicy,
    Score,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeRateLimitError,
)

from latch.llm.errors import (
    LLMConnectionError,
    LLMOverloadedError,
    LLMProviderError,
    LLMRateLimitError,
)
from latch.llm.jev import JEV_MODEL, JEV_QUESTIONS
from latch.llm.providers import JevProvider

TYPESAFE_JEV_TIMEOUT_S = 6.0  # TIMEOUT_JEV_Sと同値(unit試験が同値性を強制)
TYPESAFE_JEV_RETRIES = 0  # T5: RetryPolicy(max_retries=0)相当
TYPESAFE_JEV_BASE_URL = "https://api.typesafe.ai"

# SDK署名(questions: Mapping[str, Noul | Choice | Score])への変換(1回のみ)。
# JEV_QUESTIONSのwire形式(型・instructions・criteria)は変換で不変
_SDK_QUESTIONS: dict = {
    name: (Score.model_validate(q) if q["type"] == "score" else Noul.model_validate(q))
    for name, q in JEV_QUESTIONS.items()
}


class TypeSafeJevProvider(JevProvider):
    """第一候補(07 §4)。name="typesafe"(08 §3送信記録・§9-1)。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = TYPESAFE_JEV_BASE_URL,
        client: AsyncTypeSafeClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("TypeSafeJevProvider requires api_key")  # fail-fast
        self.name = "typesafe"
        self._api_key = api_key
        self._base_url = base_url
        self._client = client  # Noneならjudge()内で都度構築(async context manager)

    def _build_client(self) -> AsyncTypeSafeClient:
        return AsyncTypeSafeClient(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=TYPESAFE_JEV_TIMEOUT_S,
            retry=RetryPolicy(max_retries=TYPESAFE_JEV_RETRIES),
        )

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        client = self._client if self._client is not None else self._build_client()
        try:
            async with client as c:
                resp = await c.system_one(
                    state={"intent_a": intent_a, "intent_b": intent_b},
                    model=JEV_MODEL,
                    questions=_SDK_QUESTIONS,
                )
        except TypeSafeRateLimitError as exc:
            raise LLMRateLimitError("typesafe rate limited") from exc
        except TypeSafeAPIConnectionError as exc:
            raise LLMConnectionError("typesafe connection failed") from exc
        except TypeSafeAPIError as exc:
            if getattr(exc, "status", None) == 529:
                raise LLMOverloadedError("typesafe overloaded") from exc
            raise LLMProviderError("typesafe provider failed") from exc
        return _envelope(resp)


def _envelope(resp: Any) -> dict:
    """SDK応答(SystemOneResponse)をSystem One envelope dictへ正規化(§9-6)。

    answersはdict[str, Answer]のまま通す(検証はvalidate_and_normalizeが
    _field経由で担う)。usageはinput_tokens/output_tokens(SDK: int | None)。
    """
    return {
        "model": resp.model,
        "answers": resp.answers,
        "usage": {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        },
    }
