"""build_llm_gatewayとLLM設定(design §4-7・§2.6)。"""

import json
import logging
from datetime import UTC, date, datetime
from time import perf_counter  # 実時間計測はテストコードのみ(design §4)

import pytest

from latch.core.clock import FakeClock
from latch.llm.anthropic import AnthropicParserProvider
from latch.llm.anthropic_jev import AnthropicJevFallbackProvider
from latch.llm.gemini import GeminiEmbeddingProvider
from latch.llm.gateway import build_llm_gateway, build_worker_gateway
from latch.llm.records import LOGGER_NAME
from latch.llm.stub import StubLLM
from latch.llm.typesafe import TypeSafeJevProvider
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)

LLM_ENV_VARS = (
    "LATCH_LLM_MODE",
    "LATCH_LLM_STUB_DELAY_PARSER_MS",
    "LATCH_LLM_STUB_DELAY_EMBEDDING_MS",
    "LATCH_LLM_STUB_DELAY_JEV_MS",
    "LATCH_ANTHROPIC_API_KEY",
    "LATCH_ANTHROPIC_BASE_URL",
    "LATCH_TYPESAFE_API_KEY",
    "LATCH_TYPESAFE_BASE_URL",
)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _clean_settings(monkeypatch, **overrides) -> Settings:
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(**overrides)


def test_llm_settings_defaults(monkeypatch):
    s = _clean_settings(monkeypatch)
    assert s.llm_mode == "stub"
    assert s.llm_anthropic_api_key == ""
    assert s.llm_anthropic_base_url == "https://api.anthropic.com"
    assert s.llm_typesafe_api_key == ""
    assert s.llm_typesafe_base_url == "https://api.typesafe.ai"
    assert s.llm_stub_delay_parser_ms == 0
    assert s.llm_stub_delay_embedding_ms == 0
    assert s.llm_stub_delay_jev_ms == 0


def test_llm_settings_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_LLM_STUB_DELAY_JEV_MS", "120")
    for var in LLM_ENV_VARS:
        if var != "LATCH_LLM_STUB_DELAY_JEV_MS":
            monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.llm_stub_delay_jev_ms == 120


def test_llm_settings_env_reads_anthropic_key(monkeypatch):
    # design §2.3/§2.4: API鍵のenv名はLATCH_ANTHROPIC_API_KEY(T1・.env・
    # make g1-gateと同一)。env_prefix=LATCH_の自動写像だと
    # LATCH_LLM_ANTHROPIC_API_KEYを探してしまうためaliasが必要
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LATCH_ANTHROPIC_API_KEY", "env-key")
    s = Settings()
    assert s.llm_anthropic_api_key == "env-key"


def test_llm_settings_env_reads_anthropic_base_url(monkeypatch):
    # API鍵と同一パターンの写像(ws-6 supervisor裁定・design §3.2拡張)
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LATCH_ANTHROPIC_BASE_URL", "https://proxy.example/api")
    s = Settings()
    assert s.llm_anthropic_base_url == "https://proxy.example/api"


def test_llm_settings_are_exactly_nine_fields(monkeypatch):
    # Review Focus #5: timeout・failフラグのenv経路を作らない(design §2.4・§2.6)。
    # LLM系設定はこの9項目のみであることを機械検査する(base_urlはdesign §3.2の
    # supervisor承認済み拡張・ws-6のANTHROPIC_BASE_URL汚染対策。
    # gemini_api_keyはM2 ws-2のsupervisor許可による追従 — 期待値1項目追加のみ。
    # typesafe_api_key/base_urlはM2 ws-5の機械的追随(supervisor承認事項))。
    _clean_settings(monkeypatch)
    llm_fields = {f for f in Settings.model_fields if f.startswith("llm_")}
    assert llm_fields == {
        "llm_mode",
        "llm_anthropic_api_key",
        "llm_anthropic_base_url",
        "llm_gemini_api_key",
        "llm_typesafe_api_key",
        "llm_typesafe_base_url",
        "llm_stub_delay_parser_ms",
        "llm_stub_delay_embedding_ms",
        "llm_stub_delay_jev_ms",
    }


def test_llm_settings_env_reads_typesafe_key(monkeypatch):
    # anthropic_api_keyと同一パターンの写像(M2 ws-5・AliasChoices形式)
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LATCH_TYPESAFE_API_KEY", "env-key")
    s = Settings()
    assert s.llm_typesafe_api_key == "env-key"


def test_llm_settings_env_reads_typesafe_base_url(monkeypatch):
    # API鍵と同一パターンの写像(M2 ws-5)
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LATCH_TYPESAFE_BASE_URL", "https://proxy.example/api")
    s = Settings()
    assert s.llm_typesafe_base_url == "https://proxy.example/api"


async def test_factory_builds_working_stub_gateway(clock, caplog, monkeypatch):
    gw = build_llm_gateway(clock, _clean_settings(monkeypatch))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = await gw.parse_intent(text="t", current_date=date(2026, 9, 27))
    assert result["category"]["primary"] == "meal"
    messages = [r for r in caplog.records if r.name == LOGGER_NAME]
    (payload,) = [json.loads(m.getMessage()) for m in messages]
    assert payload["destination"] == "stub"  # StubLLM内包の挙動証明
    assert payload["status"] == "ok"


async def test_factory_passes_delay_settings_to_stub(clock, monkeypatch):
    gw = build_llm_gateway(
        clock, _clean_settings(monkeypatch, llm_stub_delay_jev_ms=50)
    )
    start = perf_counter()
    await gw.judge_pair(intent_a="a", intent_b="b", intent_ids=["i-1", "i-2"])
    assert (perf_counter() - start) * 1000 >= 50


def test_factory_rejects_unknown_mode(clock, monkeypatch):
    # stub/real以外は拒否(realの引数完備は別試験が担保)
    s = _clean_settings(monkeypatch, llm_mode="production")
    with pytest.raises(ValueError, match="llm_mode"):
        build_llm_gateway(clock, s)


def test_factory_real_builds_anthropic_parser(clock, monkeypatch):
    # design §2.4: real=Parser系統のみ実装プロバイダ。Embedding/JevはStubLLM継続
    gw = build_llm_gateway(
        clock,
        _clean_settings(monkeypatch, llm_mode="real", llm_anthropic_api_key="test-key"),
        parser_system_prompt="p {current_date}",
        parser_output_schema={"type": "object", "properties": {}},
    )
    assert isinstance(gw._parser, AnthropicParserProvider)
    assert gw._parser.name == "anthropic"
    assert isinstance(gw._embedding, StubLLM)
    assert isinstance(gw._jev, StubLLM)
    # settingsのbase_urlがproviderの接続先へ明示渡しされる(環境変数非依存)
    assert str(gw._parser._client.base_url) == "https://api.anthropic.com"


def test_factory_real_passes_custom_base_url(clock, monkeypatch):
    gw = build_llm_gateway(
        clock,
        _clean_settings(
            monkeypatch,
            llm_mode="real",
            llm_anthropic_api_key="test-key",
            llm_anthropic_base_url="https://mirror.example/api",
        ),
        parser_system_prompt="p",
        parser_output_schema={"type": "object", "properties": {}},
    )
    # httpx2はURL末尾へスラッシュを正規化する
    assert str(gw._parser._client.base_url) == "https://mirror.example/api/"


def test_factory_real_requires_api_key(clock, monkeypatch):
    s = _clean_settings(monkeypatch, llm_mode="real")  # 鍵空=既定
    with pytest.raises(ValueError, match="api_key"):
        build_llm_gateway(clock, s, parser_system_prompt="p", parser_output_schema={})


def test_factory_real_requires_prompt_and_schema(clock, monkeypatch):
    s = _clean_settings(monkeypatch, llm_mode="real", llm_anthropic_api_key="test-key")
    with pytest.raises(ValueError, match="parser_system_prompt"):
        build_llm_gateway(clock, s)
    with pytest.raises(ValueError, match="parser_system_prompt"):
        build_llm_gateway(clock, s, parser_system_prompt="p")
    with pytest.raises(ValueError, match="parser_system_prompt"):
        build_llm_gateway(clock, s, parser_output_schema={"type": "object"})


# -- build_worker_gateway(M2 ws-5・design §2.8) --


def test_worker_gateway_real_builds_typesafe_and_fallback(clock, monkeypatch):
    """real=embedding+jev+フォールバックの3系統real化(design §2.8)。"""
    gw = build_worker_gateway(
        clock,
        _clean_settings(
            monkeypatch,
            llm_mode="real",
            llm_gemini_api_key="gk",
            llm_typesafe_api_key="tk",
            llm_anthropic_api_key="ak",
        ),
    )
    assert isinstance(gw._embedding, GeminiEmbeddingProvider)
    assert isinstance(gw._jev, TypeSafeJevProvider)
    assert gw._jev.name == "typesafe"
    assert isinstance(gw._jev_fallback, AnthropicJevFallbackProvider)
    assert gw._jev_fallback.name == "anthropic"
    assert isinstance(gw._parser, StubLLM)  # parser系統はstub継続


@pytest.mark.parametrize(
    "missing", ["llm_gemini_api_key", "llm_typesafe_api_key", "llm_anthropic_api_key"]
)
def test_worker_gateway_real_fails_fast_without_key(clock, monkeypatch, missing):
    kwargs = {
        "llm_mode": "real",
        "llm_gemini_api_key": "gk",
        "llm_typesafe_api_key": "tk",
        "llm_anthropic_api_key": "ak",
    }
    kwargs.pop(missing)
    with pytest.raises(ValueError, match="api_key"):
        build_worker_gateway(clock, _clean_settings(monkeypatch, **kwargs))


def test_worker_gateway_stub_shares_stub_for_fallback(clock, monkeypatch):
    gw = build_worker_gateway(clock, _clean_settings(monkeypatch))
    assert gw._jev is gw._jev_fallback  # stub時は同一インスタンス(design §2.8)


async def test_make_intent_parse_service_passes_prompt_and_schema(monkeypatch):
    # design §2.3: ファクトリがPARSER_SYSTEM_PROMPTとParserOutputスキーマを渡す。
    # real設定で構築できれば注入は機能している(欠落ならValueError)
    from latch.intents.prompt import PARSER_SYSTEM_PROMPT
    from latch.intents.schema import ParserOutput
    from latch.intents.service import make_intent_parse_service

    monkeypatch.delenv("LATCH_ANTHROPIC_API_KEY", raising=False)
    settings = Settings(llm_mode="real", llm_anthropic_api_key="test-key")
    service = make_intent_parse_service(
        clock=FakeClock(NOW), settings=settings, user_lookup=None
    )
    # stubでもserviceは構築できる(llm/のimportはファクトリに限る規律どおり)
    service_stub = make_intent_parse_service(
        clock=FakeClock(NOW), settings=Settings(), user_lookup=None
    )
    assert service is not None and service_stub is not None
    assert PARSER_SYSTEM_PROMPT  # (docstring参照の実在確認)
    assert ParserOutput.model_json_schema()


def test_public_api_reexports():
    import latch.llm as api

    for name in (
        "LLMGateway",
        "build_llm_gateway",
        "Timeouts",
        "TIMEOUT_PARSER_S",
        "TIMEOUT_EMBEDDING_S",
        "TIMEOUT_JEV_S",
        "LLMError",
        "LLMTimeoutError",
        "LLMProviderError",
        "ParserProvider",
        "EmbeddingProvider",
        "JevProvider",
        "EMBEDDING_DIMENSIONS",
        "StubLLM",
        "SendRecord",
        "send_log",
    ):
        assert getattr(api, name, None) is not None, name
