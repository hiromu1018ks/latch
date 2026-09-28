"""AnthropicParserProviderのunit(design §4-1)。SDKクライアントは注入モック・
実API呼び出しゼロ。公式SDKのレスポンス型を合成して応答経路を検証する。"""

import json
from datetime import date
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from latch.intents.schema import ParserOutput
from latch.llm.anthropic import (
    ANTHROPIC_PARSER_MAX_TOKENS,
    ANTHROPIC_PARSER_MODEL,
    ANTHROPIC_PARSER_TIMEOUT_S,
    AnthropicParserProvider,
    adapt_schema_for_anthropic,
)
from latch.llm.gateway import TIMEOUT_PARSER_S
from latch.llm.providers import ParserProvider

SYSTEM_PROMPT = "現在日付は {current_date} とする。"  # {current_date}入り簡易プロンプト

# structured outputs用の最小スキーマ(後加工の入力。properties値は任意)
MIN_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


class _RecordingMessages:
    """AsyncAnthropic.messages のテストダブル(呼び出し記録+応答注入)。"""

    def __init__(self, responses=None, error=None):
        self.calls: list[dict] = []
        self._responses = list(responses or [])
        self._error = error

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, messages):
        self.messages = messages


def _sdk_message(text: str) -> anthropic.types.Message:
    """公式SDKのMessage型を合成(最初のtextブロック=有効JSON)。"""
    return anthropic.types.Message(
        id="msg_test",
        type="message",
        role="assistant",
        model=ANTHROPIC_PARSER_MODEL,
        content=[anthropic.types.TextBlock(type="text", text=text)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=anthropic.types.Usage(input_tokens=1, output_tokens=1),
    )


def _sdk_status_error() -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(500, request=request)
    return anthropic.APIStatusError("test error", response=response, body=None)


def test_timeout_constant_matches_gateway():
    # design §2.5: SDK timeoutとGateway timeoutを同値にする(循環import回避のため
    # 定数は自前定義+この試験で同値を強制)
    assert ANTHROPIC_PARSER_TIMEOUT_S == TIMEOUT_PARSER_S


def test_rejects_empty_api_key():
    with pytest.raises(ValueError, match="api_key"):
        AnthropicParserProvider(
            api_key="", system_prompt=SYSTEM_PROMPT, output_schema=MIN_SCHEMA
        )


def test_default_client_options():
    provider = AnthropicParserProvider(
        api_key="test-key", system_prompt=SYSTEM_PROMPT, output_schema=MIN_SCHEMA
    )
    client = provider._client  # AsyncAnthropic実体(この試験のみ構築)
    assert type(client).__name__ == "AsyncAnthropic"
    assert client.max_retries == 0
    # SDK 1.8.0はスカラーtimeoutをそのまま保持(float)。connect/read/write/pool
    # の全ソケットへ同値が適用される(httpx2のスカラーtimeout仕様)
    assert client.timeout == ANTHROPIC_PARSER_TIMEOUT_S


def test_default_client_targets_official_api(monkeypatch):
    # ws-6 supervisor裁定: SDKは明示api_key指定でも環境変数ANTHROPIC_BASE_URLを
    # 自動採用する。汚染値があってもproviderの接続先は既定の公式APIであること
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil-proxy.example/api")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "polluting-token")
    provider = AnthropicParserProvider(
        api_key="test-key", system_prompt=SYSTEM_PROMPT, output_schema=MIN_SCHEMA
    )
    assert str(provider._client.base_url) == "https://api.anthropic.com"


def test_base_url_is_injectable():
    provider = AnthropicParserProvider(
        api_key="test-key",
        system_prompt=SYSTEM_PROMPT,
        output_schema=MIN_SCHEMA,
        base_url="https://api.anthropic.com/",
    )
    assert str(provider._client.base_url) == "https://api.anthropic.com/"


def test_provider_is_parser_provider():
    provider = AnthropicParserProvider(
        api_key="test-key", system_prompt=SYSTEM_PROMPT, output_schema=MIN_SCHEMA
    )
    assert isinstance(provider, ParserProvider)
    assert provider.name == "anthropic"


async def test_request_shape():
    msgs = _RecordingMessages(responses=[_sdk_message('{"ok": true}')])
    provider = AnthropicParserProvider(
        api_key="test-key",
        system_prompt=SYSTEM_PROMPT,
        output_schema=MIN_SCHEMA,
        client=_FakeClient(msgs),
    )
    result = await provider.complete_structured("テキスト", date(2026, 10, 1))
    assert result == {"ok": True}
    (call,) = msgs.calls
    assert call["model"] == ANTHROPIC_PARSER_MODEL
    assert call["system"] == "現在日付は 2026-10-01 とする。"  # {current_date}差し替え
    assert call["messages"] == [{"role": "user", "content": "テキスト"}]
    # SDK 1.xはcreate()からtemperature引数を廃止(渡すとTypeError)。出力の決定性
    # はstructured outputs(output_config.format)がAPI側で保証する
    assert "temperature" not in call
    assert call["max_tokens"] == ANTHROPIC_PARSER_MAX_TOKENS
    assert call["output_config"]["format"]["type"] == "json_schema"
    schema = call["output_config"]["format"]["schema"]
    # 生スキーマではなく後加工済みが渡る証明(required埋め+additionalProperties)
    assert schema["required"] == ["x"]
    assert schema["additionalProperties"] is False


async def test_returns_first_text_block_as_dict():
    # thinking風ブロック(type!="text")が先行しても最初のtextブロックを採る
    thinking = SimpleNamespace(type="thinking", thinking="...")
    text = SimpleNamespace(type="text", text='{"a": 1}')
    msgs = _RecordingMessages(responses=[SimpleNamespace(content=[thinking, text])])
    provider = AnthropicParserProvider(
        api_key="test-key",
        system_prompt=SYSTEM_PROMPT,
        output_schema=MIN_SCHEMA,
        client=_FakeClient(msgs),
    )
    result = await provider.complete_structured("t", date(2026, 10, 1))
    assert result == {"a": 1}


async def test_sdk_exception_passthrough():
    # SDK例外は素通り(design §3.1: Gatewayの既存wrapと送信記録が最終関門)
    msgs = _RecordingMessages(error=_sdk_status_error())
    provider = AnthropicParserProvider(
        api_key="test-key",
        system_prompt=SYSTEM_PROMPT,
        output_schema=MIN_SCHEMA,
        client=_FakeClient(msgs),
    )
    with pytest.raises(anthropic.APIStatusError):
        await provider.complete_structured("t", date(2026, 10, 1))


def _assert_all_objects_strict(node) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            assert node["required"] == sorted(node["properties"])
            assert node["additionalProperties"] is False
        for value in node.values():
            _assert_all_objects_strict(value)
    elif isinstance(node, list):
        for item in node:
            _assert_all_objects_strict(item)


def test_adapt_schema_structure_pins():
    # Review Focus #1(構造面): ParserOutput.model_json_schema()から決定的導出され、
    # Anthropic制約(全required+additionalProperties=false+$defs解決済み)を満たす
    adapted = adapt_schema_for_anthropic(ParserOutput.model_json_schema())
    assert "$defs" not in adapted
    assert "$ref" not in json.dumps(adapted)
    _assert_all_objects_strict(adapted)
    assert set(adapted["properties"]) == {
        "category",
        "alcohol_involved",
        "time",
        "location",
        "budget",
        "participants",
        "soft_constraints",
        "negative_constraints",
        "ng_unverifiable",
    }
    primary = adapted["properties"]["category"]["properties"]["primary"]
    assert set(primary["enum"]) == {"meal", "drinking", "activity"}
    assert adapted["properties"]["alcohol_involved"]["type"] == "boolean"
    start = adapted["properties"]["time"]["properties"]["start"]
    assert start["type"] == "string" and start.get("format") == "date-time"


def test_adapt_schema_is_deterministic():
    a = adapt_schema_for_anthropic(ParserOutput.model_json_schema())
    b = adapt_schema_for_anthropic(ParserOutput.model_json_schema())
    assert a == b


def test_adapt_schema_strips_unsupported_keywords():
    # Review Focus #1(負のピン): Anthropic structured outputsが対応しない
    # 制約キーワードは残らない — 残ると実APIが400で拒否し得る(合成モックでは
    # 検出不能)。検証関門はサービス層ParserOutput.model_validateが保持する
    def _collect_keys(node, acc):
        if isinstance(node, dict):
            acc.update(node.keys())
            for value in node.values():
                _collect_keys(value, acc)
        elif isinstance(node, list):
            for item in node:
                _collect_keys(item, acc)
        return acc

    adapted = adapt_schema_for_anthropic(ParserOutput.model_json_schema())
    keys = _collect_keys(adapted, set())
    unsupported = (
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "multipleOf",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
    )
    assert not keys & set(unsupported)
