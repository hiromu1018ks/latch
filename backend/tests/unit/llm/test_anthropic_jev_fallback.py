"""AnthropicJevFallbackProviderの契約ピン(design §4.1・§1.3 A1〜A4)。

client注入スタブでAsyncAnthropicを置換し実APIなしに検証する。
実機挙動はmake jev-smoke FALLBACK=1(スーパーバイザー実行)が初回検証する。
"""

import json
from types import SimpleNamespace

import pytest

from latch.llm.anthropic import ANTHROPIC_PARSER_BASE_URL
from latch.llm.anthropic_jev import (
    ANTHROPIC_JEV_BASE_URL,
    ANTHROPIC_JEV_FALLBACK_MAX_TOKENS,
    ANTHROPIC_JEV_FALLBACK_MODEL,
    ANTHROPIC_JEV_FALLBACK_TIMEOUT_S,
    FALLBACK_SYSTEM_PROMPT,
    AnthropicJevFallbackProvider,
)
from latch.llm.gateway import TIMEOUT_JEV_S

# フォールバック応答のJSON(structured outputのtextブロック1つ)
_FALLBACK_JSON = json.dumps(
    {
        "would_a_accept_b": 0.8,
        "would_b_accept_a": 0.7,
        "purpose_fit": 3.0,
        "mood_fit": 2.0,
        "timing_fit": 2.0,
        "social_fit": 3.0,
        "latent_yes": 0.4,
    }
)


class _StubMessages:
    """messages.createのスタブ(呼び出し引数を記録)。"""

    def __init__(self, text: str, error: Exception | None = None):
        self._text = text
        self._error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._text)],
            usage=SimpleNamespace(input_tokens=5, output_tokens=9),
        )


class _StubClient:
    def __init__(self, text: str = _FALLBACK_JSON, error: Exception | None = None):
        self.messages = _StubMessages(text, error)


def _provider(
    error: Exception | None = None,
) -> tuple[AnthropicJevFallbackProvider, _StubClient]:
    client = _StubClient(error=error)
    return AnthropicJevFallbackProvider(api_key="ak", client=client), client


async def test_create_args_pin():
    """messages.createの引数ピン(model・system・messages・output_config・thinking・max_tokens)。"""
    provider, client = _provider()
    await provider.judge(
        "Intent A:\n[hard] category: drinking", "Intent B:\n[hard] category: meal"
    )
    (call,) = client.messages.calls
    assert call["model"] == ANTHROPIC_JEV_FALLBACK_MODEL == "claude-sonnet-5"
    assert call["system"] == FALLBACK_SYSTEM_PROMPT
    assert call["messages"] == [
        {
            "role": "user",
            "content": (
                "Intent A:\n[hard] category: drinking"
                "\n\nIntent B:\n[hard] category: meal"
            ),
        }
    ]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert set(call["output_config"]["format"]["schema"]["properties"]) == {
        "would_a_accept_b",
        "would_b_accept_a",
        "purpose_fit",
        "mood_fit",
        "timing_fit",
        "social_fit",
        "latent_yes",
    }
    assert call["thinking"] == {"type": "disabled"}
    assert call["max_tokens"] == ANTHROPIC_JEV_FALLBACK_MAX_TOKENS == 512


def test_create_args_have_no_sampling_params():
    """sampling系(temperature/top_p/top_k)は廃止のため送らない(A2: 400)。"""
    provider, client = _provider()
    import asyncio

    asyncio.run(provider.judge("A", "B"))
    (call,) = client.messages.calls
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in call, banned


def test_schema_pin_adapted_for_anthropic():
    """スキーマはadapt済み($ref等なし・number型7キー・required完備)。"""
    provider, _ = _provider()
    schema = provider._schema
    text = json.dumps(schema)
    for banned in ("$ref", "$defs", "minimum", "maximum", "minLength"):
        assert banned not in text, banned
    assert schema["properties"] == {
        "would_a_accept_b": {"type": "number"},
        "would_b_accept_a": {"type": "number"},
        "purpose_fit": {"type": "number"},
        "mood_fit": {"type": "number"},
        "timing_fit": {"type": "number"},
        "social_fit": {"type": "number"},
        "latent_yes": {"type": "number"},
    }
    assert schema["required"] == sorted(schema["properties"])
    assert schema["additionalProperties"] is False


async def test_envelope_pin():
    """応答→System One envelope(model=None・confidence=None・usage)。"""
    provider, _ = _provider()
    out = await provider.judge("A", "B")
    assert set(out) == {"model", "answers", "usage"}
    assert out["model"] is None  # 引用#8: フォールバック時のmodelはnull
    answers = out["answers"]
    assert answers["would_a_accept_b"] == {"type": "noul", "noul": 0.8}
    assert answers["would_b_accept_a"] == {"type": "noul", "noul": 0.7}
    assert answers["latent_yes"] == {"type": "noul", "noul": 0.4}
    assert answers["purpose_fit"] == {"type": "score", "score": 3.0, "confidence": None}
    assert answers["mood_fit"]["confidence"] is None
    assert answers["timing_fit"]["confidence"] is None
    assert answers["social_fit"]["confidence"] is None
    assert out["usage"] == {"input_tokens": 5, "output_tokens": 9}


def test_timeout_and_retry_pins():
    """timeout=6秒(Gatewayと同値)・base_url=公式API(parserと同値)。"""
    assert ANTHROPIC_JEV_FALLBACK_TIMEOUT_S == 6.0 == TIMEOUT_JEV_S
    assert (
        ANTHROPIC_JEV_BASE_URL
        == ANTHROPIC_PARSER_BASE_URL
        == "https://api.anthropic.com"
    )
    provider = AnthropicJevFallbackProvider(api_key="ak")
    assert provider._client.max_retries == 0  # A4: SDK既定2の無効化
    assert str(provider._client.base_url) == "https://api.anthropic.com"


def test_empty_api_key_fails_fast():
    with pytest.raises(ValueError):
        AnthropicJevFallbackProvider(api_key="")


def test_prompt_full_pin():
    """FALLBACK_SYSTEM_PROMPTの全文ピン(§9-6・変更検知)。"""
    assert FALLBACK_SYSTEM_PROMPT == (
        "あなたは2つのIntentペアの相互受け入れ可能性を評価する判定器です。"
        "入力される2つのIntent(正規化テキスト)に対し、次の7つの質問に答えるJSONのみを"
        "出力します。JSON以外の文章・説明・根拠文は一切出力しません。\n"
        "\n"
        "質問と値域:\n"
        "- would_a_accept_b: Intent Aの作成者の立場でBとの成立にyesと答える確率"
        "(0〜1の数値)\n"
        "- would_b_accept_a: Intent Bの作成者の立場でAとの成立にyesと答える確率"
        "(0〜1の数値)\n"
        "- purpose_fit: 目的・カテゴリの適合度(0〜4の数値)\n"
        "- mood_fit: 雰囲気・軽さの適合度(0〜4の数値)\n"
        "- timing_fit: 時間帯・所要時間の適合度(0〜4の数値)\n"
        "- social_fit: 人数・社会的文脈の適合度(0〜4の数値)\n"
        "- latent_yes: どちらかが明示していないが、そのIntentの記述の範囲内で"
        "YESになり得る可能性(0〜1の数値)\n"
        "\n"
        "判定規則:\n"
        "- would_*は相手側の条件も考慮し、相手の[hard]条件を満たさない場合、"
        "または相手の[soft]条件・(システムで判定不能)と付いた条件に触れる場合は"
        "yesから遠ざけます\n"
        "- [hard]条件との意味的矛盾(予算感の著しい乖離が食事内容を成立させない等)は"
        "yesから遠ざけます\n"
        "- 根拠の説明は出力しません(値のみ)\n"
    )


async def test_sdk_exception_passes_through():
    """SDK例外はこの層で握らない(Gatewayのwrapが最終関門 — anthropic.pyと同一規律)。"""
    provider, _ = _provider(error=RuntimeError("sdk boom"))
    with pytest.raises(RuntimeError):
        await provider.judge("A", "B")


def test_name_is_anthropic_for_send_record():
    """name='anthropic'(08 §3送信記録の送信先・§9-1)。"""
    assert AnthropicJevFallbackProvider(api_key="ak").name == "anthropic"
