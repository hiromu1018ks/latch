"""GeminiEmbeddingProviderの契約ピン(design §2.6・§4.1)。

client注入スタブで実APIなしに検証する。実機挙動(HttpRetryOptionsの実効性・
応答形式・タイムアウト例外型)はmake embed-smoke(スーパーバイザー実行)が
初回検証する — design §5-1(ws-6の401事故と同じ位置づけ)。
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import LLMProviderError
from latch.llm.gateway import TIMEOUT_EMBEDDING_S
from latch.llm.gemini import (
    GEMINI_EMBEDDING_MODEL,
    GEMINI_EMBEDDING_TIMEOUT_S,
    GEMINI_HTTP_TIMEOUT_MS,
    GeminiEmbeddingProvider,
)
from latch.llm.providers import EMBEDDING_DIMENSIONS
from latch.settings import Settings


class FakeAioModels:
    """client.aio.models.embed_contentのスタブ(呼び出し引数を記録)。"""

    def __init__(self, values, error=None):
        self.values = values
        self.error = error
        self.calls: list[dict] = []

    async def embed_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(embeddings=[SimpleNamespace(values=list(self.values))])


def _provider(values=None, error=None):
    aio = FakeAioModels(values or [0.25] * EMBEDDING_DIMENSIONS, error)
    client = SimpleNamespace(aio=SimpleNamespace(models=aio))
    return GeminiEmbeddingProvider(api_key="test-key", client=client), aio


def test_timeout_pin_matches_gateway():
    """SDK側timeoutはGateway TIMEOUT_EMBEDDING_Sと同値(design §2.6)。"""
    assert GEMINI_EMBEDDING_TIMEOUT_S == TIMEOUT_EMBEDDING_S
    assert GEMINI_HTTP_TIMEOUT_MS == 2000


def test_http_options_pin_no_retry():
    """HttpOptions: timeout=2000ms・retry attempts=1(SDK既定再試行の無効化)。"""
    from latch.llm import gemini

    opts = gemini._http_options()
    assert opts.timeout == GEMINI_HTTP_TIMEOUT_MS
    assert opts.retry_options.attempts == 1


async def test_embed_calls_with_model_contents_and_768():
    """embed_contentの引数: model=gemini-embedding-001・contents=text・768指定。"""
    provider, aio = _provider()
    vec = await provider.embed("drinking / 平日夜20-23時 / 天文館")
    assert len(vec) == EMBEDDING_DIMENSIONS
    call = aio.calls[0]
    assert call["model"] == GEMINI_EMBEDDING_MODEL
    assert call["contents"] == "drinking / 平日夜20-23時 / 天文館"
    assert call["config"].output_dimensionality == EMBEDDING_DIMENSIONS


async def test_dimension_mismatch_raises_provider_error():
    """768以外の応答は失敗扱い(LLMProviderError — design §2.6)。"""
    provider, _ = _provider(values=[0.1] * 767)
    with pytest.raises(LLMProviderError):
        await provider.embed("text")


async def test_sdk_exception_passes_through():
    """SDK例外はこの層で握らない(Gatewayのwrapと送信記録が最終関門)。"""
    provider, _ = _provider(error=RuntimeError("sdk boom"))
    with pytest.raises(RuntimeError):
        await provider.embed("text")


def test_empty_api_key_fails_fast():
    with pytest.raises(ValueError):
        GeminiEmbeddingProvider(api_key="")


def test_name_is_google_for_send_record():
    """name='google'(08 §3送信記録の送信先・design §2.6)。"""
    provider, _ = _provider()
    assert provider.name == "google"


# -- build_embedding_gateway(design §2.8-B・§4.1 Gateway構成)--


def _clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC))


def test_build_embedding_gateway_stub_mode_all_stub():
    from latch.llm import StubLLM, build_embedding_gateway

    gw = build_embedding_gateway(_clock(), Settings())
    assert isinstance(gw._embedding, StubLLM)
    assert isinstance(gw._parser, StubLLM)
    assert isinstance(gw._jev, StubLLM)


def test_build_embedding_gateway_real_embeds_only():
    from latch.llm import StubLLM, build_embedding_gateway
    from latch.llm.gemini import GeminiEmbeddingProvider

    settings = Settings(llm_mode="real", llm_gemini_api_key="gk-test")
    gw = build_embedding_gateway(_clock(), settings)
    assert isinstance(gw._embedding, GeminiEmbeddingProvider)
    assert isinstance(gw._parser, StubLLM)  # parser/jevはstub継続(design §2.8-B)
    assert isinstance(gw._jev, StubLLM)


def test_build_embedding_gateway_real_without_key_fails_fast():
    from latch.llm import build_embedding_gateway

    settings = Settings(llm_mode="real", llm_gemini_api_key="")
    with pytest.raises(ValueError):
        build_embedding_gateway(_clock(), settings)
