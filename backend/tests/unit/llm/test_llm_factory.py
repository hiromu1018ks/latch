"""build_llm_gatewayとLLM設定(design §4-7・§2.6)。"""

import json
import logging
from datetime import UTC, date, datetime
from time import perf_counter  # 実時間計測はテストコードのみ(design §4)

import pytest

from latch.core.clock import FakeClock
from latch.llm.gateway import build_llm_gateway
from latch.llm.records import LOGGER_NAME
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)

LLM_ENV_VARS = (
    "LATCH_LLM_MODE",
    "LATCH_LLM_STUB_DELAY_PARSER_MS",
    "LATCH_LLM_STUB_DELAY_EMBEDDING_MS",
    "LATCH_LLM_STUB_DELAY_JEV_MS",
    "LATCH_ANTHROPIC_API_KEY",
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


def test_llm_settings_are_exactly_five_fields(monkeypatch):
    # Review Focus #5: timeout・failフラグのenv経路を作らない(design §2.4・§2.6)。
    # LLM系設定はこの5項目のみであることを機械検査する。
    _clean_settings(monkeypatch)
    llm_fields = {f for f in Settings.model_fields if f.startswith("llm_")}
    assert llm_fields == {
        "llm_mode",
        "llm_anthropic_api_key",
        "llm_stub_delay_parser_ms",
        "llm_stub_delay_embedding_ms",
        "llm_stub_delay_jev_ms",
    }


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
    # M0では"stub"のみ。将来の"real"(T1確定後)もM0の時点では拒否する
    s = _clean_settings(monkeypatch, llm_mode="real")
    with pytest.raises(ValueError, match="llm_mode"):
        build_llm_gateway(clock, s)
    s = _clean_settings(monkeypatch, llm_mode="production")
    with pytest.raises(ValueError, match="llm_mode"):
        build_llm_gateway(clock, s)


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
