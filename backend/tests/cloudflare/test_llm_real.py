"""llm_real(Workers実LLM・fetch直書き)の単体試験(2026-10-12)。

transportをset_transportで差し替え、プロトコル(URL・ヘッダー・body)と
応答解析・エラー写像・Gateway経由のフォールバック/記録規律を検証する。
実APIへの接続はない(実配備スモークはremote試験・別資産)。
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from latch_worker import llm_real
from latch_worker.llm_real import (
    FetchAnthropicJevFallback,
    FetchAnthropicParser,
    FetchGeminiEmbedding,
    FetchTypeSafeJev,
    build_real_gateway,
    default_parser_assets,
)

from latch.llm.errors import (
    LLMConnectionError,
    LLMOverloadedError,
    LLMProviderError,
    LLMRateLimitError,
)
from latch.llm.gateway import LLMGateway
from latch.llm.jev import JEV_MODEL, JEV_QUESTIONS, validate_and_normalize

# ============================================================================
# transport差し替え(リクエスト記録つき)
# ============================================================================


class FakeTransport:
    """URLごとに応答を返すtransport。requestを記録してassertに使う。"""

    def __init__(self, routes: dict) -> None:  # routes: url -> (status, obj|text)
        self.routes = routes
        self.requests: list[dict] = []

    async def __call__(self, url, *, headers, body):
        self.requests.append({"url": url, "headers": dict(headers), "body": body})
        for prefix, (status, payload) in self.routes.items():
            if url.startswith(prefix):
                text = payload if isinstance(payload, str) else json.dumps(payload)
                return status, text
        raise AssertionError(f"unexpected url: {url}")


@pytest.fixture(autouse=True)
def _reset_transport():
    yield
    llm_real.set_transport(None)


def _anthropic_ok(text_json: str) -> dict:
    return {
        "content": [{"type": "text", "text": text_json}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _systemone_ok() -> dict:
    answers = {
        "would_a_accept_b": {"type": "noul", "noul": 0.8},
        "would_b_accept_a": {"type": "noul", "noul": 0.7},
        "latent_yes": {"type": "noul", "noul": 0.6},
        "purpose_fit": {"type": "score", "score": 4.0, "confidence": 0.9},
        "mood_fit": {"type": "score", "score": 3.0, "confidence": 0.8},
        "timing_fit": {"type": "score", "score": 4.0, "confidence": 0.7},
        "social_fit": {"type": "score", "score": 3.0, "confidence": 0.6},
    }
    return {
        "model": "jev-1.13.0-ver123",
        "answers": answers,
        "usage": {"input_tokens": 120, "output_tokens": 60},
    }


# ============================================================================
# Parser(実Anthropic・Messages直書き)
# ============================================================================


async def test_parser_request_shape_and_parse():
    expected = {"category": {"primary": "meal"}, "ok": True}
    transport = FakeTransport(
        {
            "https://api.anthropic.com/v1/messages": (
                200,
                _anthropic_ok(json.dumps(expected)),
            )
        }
    )
    llm_real.set_transport(transport)
    prompt, schema = default_parser_assets()
    parser = FetchAnthropicParser(
        api_key="sk-test", system_prompt=prompt, output_schema=schema
    )
    assert parser.name == "anthropic"
    got = await parser.complete_structured("今夜 天文館で軽く", date(2026, 10, 12))
    assert got == expected
    req = transport.requests[0]
    assert req["headers"]["x-api-key"] == "sk-test"
    assert req["headers"]["anthropic-version"] == "2023-06-01"
    assert req["body"]["model"] == "claude-haiku-4-5"
    assert req["body"]["max_tokens"] == 1024
    assert "2026-10-12" in req["body"]["system"]
    assert "{current_date}" not in req["body"]["system"]
    assert req["body"]["messages"] == [{"role": "user", "content": "今夜 天文館で軽く"}]
    assert req["body"]["output_config"]["format"]["type"] == "json_schema"
    # schema後加工(本体adapt_schema_for_anthropicと同一): $ref解決済みで
    # ルートがrequired全埋め・additionalProperties=false
    adapted = req["body"]["output_config"]["format"]["schema"]
    assert "$defs" not in adapted
    assert adapted["additionalProperties"] is False
    assert set(adapted["required"]) == set(adapted["properties"])


async def test_parser_fail_fast_without_key():
    _, schema = default_parser_assets()
    with pytest.raises(ValueError):
        FetchAnthropicParser(api_key="", system_prompt="p", output_schema=schema)


# ============================================================================
# Embedding(実Gemini・embedContent直書き)
# ============================================================================


async def test_embedding_request_and_768_dims():
    transport = FakeTransport(
        {
            "https://generativelanguage.googleapis.com/v1beta/models/": (
                200,
                {"embedding": {"values": [0.01] * 768}},
            )
        }
    )
    llm_real.set_transport(transport)
    embed = FetchGeminiEmbedding(api_key="g-test")
    assert embed.name == "google"
    values = await embed.embed("category: meal\n[hard] time: ...")
    assert len(values) == 768
    req = transport.requests[0]
    assert "gemini-embedding-001:embedContent" in req["url"]
    assert req["headers"]["x-goog-api-key"] == "g-test"
    assert req["body"]["outputDimensionality"] == 768
    assert req["body"]["content"]["parts"] == [
        {"text": "category: meal\n[hard] time: ..."}
    ]


async def test_embedding_dimension_mismatch_is_provider_error():
    llm_real.set_transport(
        FakeTransport(
            {
                "https://generativelanguage.googleapis.com/": (
                    200,
                    {"embedding": {"values": [0.5] * 100}},
                )
            }
        )
    )
    with pytest.raises(LLMProviderError):
        await FetchGeminiEmbedding(api_key="g").embed("t")


# ============================================================================
# Jev第一候補(実TypeSafe・system one直書き)
# ============================================================================


async def test_typesafe_jev_wire_format():
    transport = FakeTransport(
        {"https://api.typesafe.ai/v1/systemone": (200, _systemone_ok())}
    )
    llm_real.set_transport(transport)
    jev = FetchTypeSafeJev(api_key="ts-test")
    assert jev.name == "typesafe"
    envelope = await jev.judge("A:\n[hard]...", "B:\n[hard]...")
    assert envelope["model"] == "jev-1.13.0-ver123"
    assert envelope["usage"] == {"input_tokens": 120, "output_tokens": 60}
    validate_and_normalize(envelope)  # 7キー検証を通る形状
    req = transport.requests[0]
    assert req["headers"]["authorization"] == "Bearer ts-test"
    assert req["body"]["model"] == JEV_MODEL
    assert req["body"]["state"] == {
        "intent_a": "A:\n[hard]...",
        "intent_b": "B:\n[hard]...",
    }
    # questionsはJEV_QUESTIONS定数が単一の真実(SDKのnormalize_questionsと同一wire)
    assert req["body"]["questions"] == JEV_QUESTIONS


# ============================================================================
# Jevフォールバック(実Anthropic Haiku)
# ============================================================================


async def test_fallback_jev_prompt_schema_and_answers_conversion():
    flat = {
        "would_a_accept_b": 0.8,
        "would_b_accept_a": 0.75,
        "purpose_fit": 4.0,
        "mood_fit": 3.0,
        "timing_fit": 4.0,
        "social_fit": 3.0,
        "latent_yes": 0.5,
    }
    transport = FakeTransport(
        {
            "https://api.anthropic.com/v1/messages": (
                200,
                _anthropic_ok(json.dumps(flat)),
            )
        }
    )
    llm_real.set_transport(transport)
    fallback = FetchAnthropicJevFallback(api_key="sk-test")
    assert fallback.name == "anthropic"
    envelope = await fallback.judge("A:...", "B:...")
    assert envelope["model"] is None  # 引用#8: フォールバック時のmodelはnull
    assert envelope["answers"]["would_a_accept_b"] == {"type": "noul", "noul": 0.8}
    assert envelope["answers"]["purpose_fit"] == {
        "type": "score",
        "score": 4.0,
        "confidence": None,
    }
    assert envelope["usage"] == {"input_tokens": 10, "output_tokens": 5}
    validate_and_normalize(envelope)
    req = transport.requests[0]
    assert req["body"]["model"] == "claude-haiku-4-5"
    assert req["body"]["thinking"] == {"type": "disabled"}
    assert req["body"]["max_tokens"] == 512
    assert req["body"]["messages"] == [{"role": "user", "content": "A:...\n\nB:..."}]
    # systemは本体のFALLBACK_SYSTEM_PROMPTと同一(手書き複製なし)
    from latch.llm.anthropic_jev import FALLBACK_SYSTEM_PROMPT

    assert req["body"]["system"] == FALLBACK_SYSTEM_PROMPT
    schema = req["body"]["output_config"]["format"]["schema"]
    assert set(schema["properties"]) == set(flat)


# ============================================================================
# エラー写像(_post_json)
# ============================================================================


@pytest.mark.parametrize(
    ("status", "text", "exc"),
    [
        (429, "{}", LLMRateLimitError),
        (529, "{}", LLMOverloadedError),
        (500, "boom", LLMProviderError),
        (200, "not-json", LLMProviderError),
    ],
)
async def test_post_json_error_mapping(status, text, exc):
    llm_real.set_transport(FakeTransport({"https://x.test/": (status, text)}))
    with pytest.raises(exc):
        await llm_real._post_json("https://x.test/", headers={}, body={"a": 1})


async def test_post_json_connection_error(monkeypatch):
    async def fail(url, *, headers, body):
        raise OSError("fetch failed")

    llm_real.set_transport(fail)
    with pytest.raises(LLMConnectionError):
        await llm_real._post_json("https://x.test/", headers={}, body={})


# ============================================================================
# 構築(fail-fast・Gateway結合)
# ============================================================================


def test_build_real_gateway_fail_fast_on_missing_keys():
    from latch.core.clock import SystemClock

    prompt, schema = default_parser_assets()
    base = dict(
        anthropic_key="a",
        gemini_key="g",
        typesafe_key="t",
        parser_system_prompt=prompt,
        parser_output_schema=schema,
    )
    for key in ("anthropic_key", "gemini_key", "typesafe_key"):
        env_name = {
            "anthropic_key": "LATCH_ANTHROPIC_API_KEY",
            "gemini_key": "LATCH_GEMINI_API_KEY",
            "typesafe_key": "LATCH_TYPESAFE_API_KEY",
        }[key]
        with pytest.raises(ValueError, match=env_name):
            build_real_gateway(SystemClock(), **{**base, key: ""})
    with pytest.raises(ValueError, match="parser_system_prompt"):
        build_real_gateway(
            SystemClock(),
            anthropic_key="a",
            gemini_key="g",
            typesafe_key="t",
            parser_system_prompt=None,
            parser_output_schema=schema,
        )


def _real_gateway_with_fake_transport(transport):
    from latch.core.clock import SystemClock

    llm_real.set_transport(transport)
    prompt, schema = default_parser_assets()
    return build_real_gateway(
        SystemClock(),
        anthropic_key="a",
        gemini_key="g",
        typesafe_key="t",
        parser_system_prompt=prompt,
        parser_output_schema=schema,
    )


async def test_gateway_judge_pair_uses_typesafe_first():
    transport = FakeTransport(
        {"https://api.typesafe.ai/v1/systemone": (200, _systemone_ok())}
    )
    gateway = _real_gateway_with_fake_transport(transport)
    assert isinstance(gateway, LLMGateway)
    judgment = await gateway.judge_pair(
        intent_a="A:...", intent_b="B:...", intent_ids=["i1", "i2"]
    )
    assert judgment.provider == "typesafe_jev"
    assert judgment.model == "jev-1.13.0-ver123"
    assert judgment.usage == {"input_tokens": 120, "output_tokens": 60}
    assert len(transport.requests) == 1  # フォールバック呼出なし


async def test_gateway_judge_pair_falls_back_on_rate_limit():
    flat = {
        "would_a_accept_b": 0.6,
        "would_b_accept_a": 0.6,
        "purpose_fit": 3.0,
        "mood_fit": 3.0,
        "timing_fit": 3.0,
        "social_fit": 3.0,
        "latent_yes": 0.5,
    }
    transport = FakeTransport(
        {
            "https://api.typesafe.ai/v1/systemone": (429, "{}"),
            "https://api.anthropic.com/v1/messages": (
                200,
                _anthropic_ok(json.dumps(flat)),
            ),
        }
    )
    gateway = _real_gateway_with_fake_transport(transport)
    judgment = await gateway.judge_pair(
        intent_a="A:...", intent_b="B:...", intent_ids=["i1", "i2"]
    )
    assert judgment.provider == "fallback_llm"
    assert judgment.model is None
    assert judgment.result["would_a_accept_b"] == 0.6
    assert [r["url"].split("/v1/")[1] for r in transport.requests] == [
        "systemone",
        "messages",
    ]


# ============================================================================
# 記録adapter(RecordingParser・RealPipelineLLM)
# ============================================================================


class FakeRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def prepare(self, **kwargs):
        self.calls.append(("prepare", kwargs))
        return "attempt-1"

    async def finish(self, attempt_id, **kwargs):
        self.calls.append(("finish", attempt_id, kwargs))
        return True


async def test_recording_parser_success_and_failure():
    from latch.core.clock import SystemClock

    recorder = FakeRecorder()
    transport = FakeTransport(
        {"https://api.anthropic.com/v1/messages": (200, _anthropic_ok('{"a": 1}'))}
    )
    gateway = _real_gateway_with_fake_transport(transport)
    parser = llm_real.RecordingParser(gateway, recorder, SystemClock())
    got = await parser.parse_intent(text="t", current_date=date(2026, 10, 12))
    assert got == {"a": 1}
    assert recorder.calls[0][1]["system"] == "parser"
    assert recorder.calls[1][2]["actual_provider"] == "anthropic"
    # 失敗: 429→LLMRateLimitErrorを記録して伝播
    recorder2 = FakeRecorder()
    transport2 = FakeTransport({"https://api.anthropic.com/v1/messages": (429, "{}")})
    gateway2 = _real_gateway_with_fake_transport(transport2)
    parser2 = llm_real.RecordingParser(gateway2, recorder2, SystemClock())
    with pytest.raises(LLMRateLimitError):
        await parser2.parse_intent(text="t", current_date=date(2026, 10, 12))
    assert recorder2.calls[-1][2]["status"] == "failed"
    assert recorder2.calls[-1][2]["error_kind"] == "LLMRateLimitError"


async def test_real_pipeline_llm_embed_records_attempt():
    from latch.core.clock import SystemClock

    transport = FakeTransport(
        {
            "https://generativelanguage.googleapis.com/": (
                200,
                {"embedding": {"values": [0.02] * 768}},
            )
        }
    )
    gateway = _real_gateway_with_fake_transport(transport)
    recorder = FakeRecorder()
    llm = llm_real.RealPipelineLLM(gateway, recorder, SystemClock())
    vector = await llm.embed("text", intent_id="i-1")
    assert len(vector) == 768
    assert recorder.calls[0][1]["system"] == "embedding"
    assert recorder.calls[0][1]["requested_provider"] == "google"
    assert recorder.calls[1][2]["status"] == "succeeded"
    # 失敗(次元不一致)もfailedとして記録して伝播
    transport.routes["https://generativelanguage.googleapis.com/"] = (
        200,
        {"embedding": {"values": [0.1] * 3}},
    )
    recorder2 = FakeRecorder()
    llm2 = llm_real.RealPipelineLLM(gateway, recorder2, SystemClock())
    with pytest.raises(LLMProviderError):
        await llm2.embed("text", intent_id="i-2")
    assert recorder2.calls[-1][2]["status"] == "failed"


async def test_real_pipeline_llm_judge_returns_gateway_judgment():
    from latch.core.clock import SystemClock

    transport = FakeTransport(
        {"https://api.typesafe.ai/v1/systemone": (200, _systemone_ok())}
    )
    gateway = _real_gateway_with_fake_transport(transport)
    llm = llm_real.RealPipelineLLM(gateway, FakeRecorder(), SystemClock())
    assert llm.mode == "real"
    judgment = await llm.judge("A:...", "B:...", intent_ids=["i1", "i2"])
    assert judgment.provider == "typesafe_jev"
    assert judgment.result["would_a_accept_b"] == 0.8


async def test_stub_pipeline_llm_shape_is_unchanged():
    """stub実装のJevJudgment形状ピン(_evaluate_pairの記録値が従来どおり)。"""
    import sys
    from pathlib import Path

    cf_api = str(Path(__file__).resolve().parents[3] / "cloudflare" / "api")
    if cf_api not in sys.path:
        sys.path.insert(0, cf_api)

    from latch_worker.pipeline import STUB_JEV_RESPONSE, StubPipelineLLM

    from latch.llm.stub import StubLLM

    stub = StubPipelineLLM(StubLLM(jev_response=STUB_JEV_RESPONSE))
    assert stub.mode == "stub"
    judgment = await stub.judge("A:...", "B:...", intent_ids=["i1", "i2"])
    assert judgment.provider == "stub"
    assert judgment.model == JEV_MODEL
    # 従来のfingerprint・costと同じ値になる材料(provider/model/usage)
    assert judgment.usage == {"input_tokens": 0, "output_tokens": 0}
    assert (
        min(judgment.result["would_a_accept_b"], judgment.result["would_b_accept_a"])
        == 0.9
    )
