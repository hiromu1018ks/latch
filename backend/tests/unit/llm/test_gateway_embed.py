"""Embedding系統: 768次元応答・送信記録・timeout・エラー(design §4-1〜3)。"""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import LLMProviderError, LLMTimeoutError
from latch.llm.gateway import LLMGateway, Timeouts
from latch.llm.providers import EMBEDDING_DIMENSIONS
from latch.llm.records import LOGGER_NAME
from latch.llm.stub import StubLLM

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _gateway(clock: FakeClock, stub: StubLLM, **timeout_s: float) -> LLMGateway:
    timeouts = Timeouts(**timeout_s) if timeout_s else None
    return LLMGateway(
        clock=clock, parser=stub, embedding=stub, jev=stub, timeouts=timeouts
    )


def _send_payloads(caplog) -> list[dict]:
    messages = [r for r in caplog.records if r.name == LOGGER_NAME]
    return [json.loads(m.getMessage()) for m in messages]


async def test_embed_returns_768_dim_vector(clock):
    vector = await _gateway(clock, StubLLM()).embed_intent(
        text="meal / 平日夜20-23時 / 東京駅 / 2人 / 軽く", intent_id="i-1"
    )
    assert len(vector) == EMBEDDING_DIMENSIONS  # 05 第2節 vector(768)


async def test_embed_records_send_record(clock, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).embed_intent(
            text="meal / 平日夜20-23時 / 東京駅 / 2人 / 軽く", intent_id="i-1"
        )
    (payload,) = _send_payloads(caplog)
    occurred = datetime.fromisoformat(payload["occurred_at"])
    assert occurred == NOW  # Clock由来
    assert occurred.utcoffset() == timedelta(0)
    assert payload["system"] == "embedding"
    assert payload["destination"] == "stub"
    assert payload["status"] == "ok"
    assert payload["intent_ids"] == ["i-1"]  # Embedding=1件(design §3.1)
    assert payload["user_id"] is None


async def test_embed_timeout_records_then_raises(clock, caplog):
    stub = StubLLM(delay_embedding_ms=200)
    gw = _gateway(clock, stub, embedding_s=0.05)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMTimeoutError):
            await gw.embed_intent(text="t", intent_id="i-1")
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "timeout"
    assert payload["error_code"] == "LLMTimeoutError"


async def test_embed_provider_error_records_then_raises(clock, caplog):
    gw = _gateway(clock, StubLLM(fail_embedding=True))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMProviderError):
            await gw.embed_intent(text="t", intent_id="i-1")
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "error"
    assert payload["error_code"] == "LLMProviderError"


async def test_embed_input_text_never_appears_in_send_record(clock, caplog):
    # 正規化テキストは構造化データ由来だが、記録は内容を含まない(08 第2.4節)
    secret = "天文館周辺で軽く飲みたい"
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).embed_intent(text=secret, intent_id="i-1")
    assert secret not in caplog.text
