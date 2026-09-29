"""TypeSafeJevProviderの契約ピン(design §4.1・§1.3 T1〜T7)。

client注入スタブで実APIなしに検証する。実機挙動(応答形式・noulのJSON形状)は
make jev-smoke(スーパーバイザー実行)が初回検証する — design §5-7
(embed-smokeの401事故と同じ位置づけ)。
"""

from types import SimpleNamespace

import pytest
from typesafe_sdk import (  # noqa: E402
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
    LLMTimeoutError,
)
from latch.llm.gateway import TIMEOUT_JEV_S
from latch.llm.jev import JEV_MODEL, JEV_QUESTIONS
from latch.llm.typesafe import (
    TYPESAFE_JEV_RETRIES,
    TYPESAFE_JEV_TIMEOUT_S,
    TypeSafeJevProvider,
)


class _StubClient:
    """AsyncTypeSafeClientスタブ(async context manager・呼び出し記録)。"""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls: list[dict] = []
        self.entered = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *exc):
        return False

    async def system_one(self, *, state, model, questions):
        self.calls.append({"state": state, "model": model, "questions": questions})
        if self._error is not None:
            raise self._error
        return self._response


def _sdk_response() -> SimpleNamespace:
    """SystemOneResponse相当(SDK応答はpydanticのためオブジェクトで再現)。"""
    answers = {
        "would_a_accept_b": SimpleNamespace(noul=0.83),
        "would_b_accept_a": SimpleNamespace(noul=0.71),
        "latent_yes": SimpleNamespace(noul=0.40),
        "purpose_fit": SimpleNamespace(score=3.0, confidence=0.8),
        "mood_fit": SimpleNamespace(score=2.0, confidence=0.6),
        "timing_fit": SimpleNamespace(score=2.0, confidence=0.7),
        "social_fit": SimpleNamespace(score=3.0, confidence=0.6),
    }
    return SimpleNamespace(
        model="jev-1.13.0",
        answers=answers,
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
    )


def _provider(client) -> TypeSafeJevProvider:
    return TypeSafeJevProvider(api_key="tk", client=client)


async def test_provider_returns_envelope_from_sdk_response():
    client = _StubClient(response=_sdk_response())
    out = await _provider(client).judge("Intent A:\n[hard] category: drinking", "B")
    assert out["model"] == "jev-1.13.0"
    assert out["answers"]["would_a_accept_b"].noul == 0.83  # answersはそのまま通す
    assert out["usage"] == {"input_tokens": 11, "output_tokens": 7}
    assert set(out) == {"model", "answers", "usage"}


async def test_system_one_call_args_pin():
    """state/model/questionsの引数ピン(modelはエイリアス不使用)。"""
    client = _StubClient(response=_sdk_response())
    await _provider(client).judge("A TEXT", "B TEXT")
    (call,) = client.calls
    assert call["state"] == {"intent_a": "A TEXT", "intent_b": "B TEXT"}
    assert call["model"] == JEV_MODEL == "jev-1.13.0"
    # questionsはSDK署名(Noul/Scoreモデル)に合わせた変換後群。
    # wire形式はJEV_QUESTIONS定数と完全一致(SDK署名準拠の差分・§0規律)
    questions = call["questions"]
    assert set(questions) == set(JEV_QUESTIONS)
    for name, q in JEV_QUESTIONS.items():
        expected_type = "score" if isinstance(questions[name], Score) else "noul"
        assert expected_type == q["type"], name
        wire = questions[name].model_dump(mode="json", exclude_none=True)
        assert wire == q, name


def test_retry_and_timeout_pins():
    """timeoutはGatewayと同値6秒・retryはRetryPolicy(max_retries=0)相当(T5)。"""
    assert TYPESAFE_JEV_TIMEOUT_S == 6.0 == TIMEOUT_JEV_S
    assert TYPESAFE_JEV_RETRIES == 0
    assert RetryPolicy(max_retries=TYPESAFE_JEV_RETRIES).max_retries == 0


def test_build_client_pins():
    """実構築(注入なし)でretry無効化・timeout・base_urlが渡る。"""
    provider = TypeSafeJevProvider(api_key="tk")
    client = provider._build_client()
    # retry無効化の実効確認: SDK既定2→0=初回のみで停止=再試行なし(T5)。
    # tenacityのstop条件へ落とされて *_retry.stop に入る(SDK内部表現の検査)
    attempts = [
        getattr(s, "max_attempt_number", None) for s in client._retry.stop.stops
    ]
    assert 1 in [a for a in attempts if a is not None]
    assert client._config.timeout == TYPESAFE_JEV_TIMEOUT_S
    assert client._config.base_url == "https://api.typesafe.ai"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TypeSafeRateLimitError(429, None, {}), LLMRateLimitError),
        (TypeSafeAPIError(529, None, {}), LLMOverloadedError),
        (TypeSafeAPIError(400, None, {}), LLMProviderError),
        (TypeSafeAPIConnectionError("conn"), LLMConnectionError),
    ],
)
async def test_exception_translation(exc, expected):
    client = _StubClient(error=exc)
    with pytest.raises(expected):
        await _provider(client).judge("A", "B")


async def test_translated_message_has_no_intent_body():
    """例外メッセージにIntent本文を入れない(08 §2.4)。"""
    client = _StubClient(error=TypeSafeRateLimitError(429, None, {}))
    with pytest.raises(LLMRateLimitError) as ei:
        await _provider(client).judge("SECRET-A", "SECRET-B")
    assert "SECRET" not in str(ei.value)


async def test_unknown_exception_passes_through():
    """SDK例外以外は翻訳せず素通り(LLMError継承のみで握らない)。"""
    client = _StubClient(error=RuntimeError("sdk boom"))
    with pytest.raises(RuntimeError):
        await _provider(client).judge("A", "B")


def test_empty_api_key_fails_fast():
    with pytest.raises(ValueError):
        TypeSafeJevProvider(api_key="")


def test_name_is_typesafe_for_send_record():
    """name='typesafe'(08 §3送信記録の送信先・§9-1)。"""
    assert TypeSafeJevProvider(api_key="tk").name == "typesafe"


def test_llm_error_not_raised_as_is():
    """翻訳後はLLMError(LLMTimeoutError含む例外群)側で切替条件へ乗る。"""
    from latch.llm.errors import LLMError

    for exc in (
        LLMRateLimitError("429"),
        LLMOverloadedError("529"),
        LLMConnectionError("conn"),
        LLMTimeoutError("t"),
    ):
        assert isinstance(exc, LLMError)
