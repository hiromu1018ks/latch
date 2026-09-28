"""Embedding系統の実プロバイダ(Google Gemini API・T1 v0.2・07 §3)。

design §2.6: google-genai(Python公式統一SDK)・明示api_key(SDKが環境変数
GEMINI_API_KEY/GOOGLE_API_KEYを自動採用するのを排除 — llm_anthropic_base_url
と同じ規律)・HttpRetryOptions(attempts=1)でSDK既定の再試行を無効化
(「再試行なし」07 §1の必須条件)・output_dimensionality=768(パラメータ指定)。
次元不一致はLLMProviderError(=失敗扱い・D-15経路)。SDK例外はこの層で
握らず素通り — Gatewayの既存wrap(LLMTimeoutError/LLMProviderError)と
送信記録が最終関門(anthropic.pyと同じ)。
"""

from __future__ import annotations

from latch.llm.errors import LLMProviderError
from latch.llm.providers import EMBEDDING_DIMENSIONS, EmbeddingProvider

GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"  # T1 v0.2 §2.4(768はパラメータ指定)
# Gateway TIMEOUT_EMBEDDING_Sと同値(design §2.6: SDK側で過剰に待つ時間を作らない)。
# 循環import回避のため値を自前定義し、同値性はunit試験が強制する
GEMINI_EMBEDDING_TIMEOUT_S = 2.0
GEMINI_HTTP_TIMEOUT_MS = int(GEMINI_EMBEDDING_TIMEOUT_S * 1000)


def _http_options():
    """SDKのHTTP構成(timeout=2000ms・再試行1回のみ)。"""
    from google.genai import types

    return types.HttpOptions(
        timeout=GEMINI_HTTP_TIMEOUT_MS,
        retry_options=types.HttpRetryOptions(attempts=1),  # 再試行なし(07 §1)
    )


def _embed_config():
    from google.genai import types

    return types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS)


class GeminiEmbeddingProvider(EmbeddingProvider):
    """Embedding系統の実プロバイダ(07 §3・T1 v0.2)。name="google"(08 §3)。"""

    def __init__(self, *, api_key: str, client: object | None = None) -> None:
        if not api_key:
            # fail-fast: 鍵の不在を静かに握りつぶさない(design §2.8-B)
            raise ValueError("GeminiEmbeddingProvider requires api_key")
        self.name = "google"
        if client is not None:
            self._client = client
        else:
            self._client = _make_client(api_key)  # モジュール末尾定義(実行時解決)

    async def embed(self, text: str) -> list[float]:
        """07 §3。768次元(パラメータ指定)。次元不一致はLLMProviderError。"""
        response = await self._client.aio.models.embed_content(
            model=GEMINI_EMBEDDING_MODEL,
            contents=text,
            config=_embed_config(),
        )
        values = list(response.embeddings[0].values)
        if len(values) != EMBEDDING_DIMENSIONS:
            raise LLMProviderError(
                f"embedding dimension mismatch: {len(values)} != {EMBEDDING_DIMENSIONS}"
            )
        return values


def _make_client(api_key: str):
    """genai.Client構築(明示api_key — 環境変数の暗黙採用を排除・design §2.6)。"""
    from google import genai

    return genai.Client(api_key=api_key, http_options=_http_options())
