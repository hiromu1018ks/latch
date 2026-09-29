"""Jev系統: 7設問応答・送信記録(intent_ids 2件)・timeout・エラー(design §4-1〜3)。"""

import json
import logging
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import LLMProviderError, LLMTimeoutError
from latch.llm.gateway import LLMGateway, Timeouts
from latch.llm.records import LOGGER_NAME
from latch.llm.stub import StubLLM

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
INTENT_A = "Intent A:\n[hard] category: drinking\n[hard] time: 2026-09-26 20:00–23:00"
INTENT_B = "Intent B:\n[hard] category: meal\n[hard] time: 2026-09-26 19:30–22:30"


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


async def test_judge_returns_judgment_from_stub_envelope(clock):
    resp = await _gateway(clock, StubLLM()).judge_pair(
        intent_a=INTENT_A, intent_b=INTENT_B, intent_ids=["i-1", "i-2"]
    )
    assert resp.provider == "typesafe_jev"  # 第一候補経路(スタブでも)
    assert resp.model == "jev-1.13.0"
    assert resp.result["would_a_accept_b"] == 0.5
    assert resp.result["jev_5axis"]["purpose_fit"] == {"value": 0.5, "confidence": 0.5}


async def test_judge_records_send_record_with_two_intent_ids(clock, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).judge_pair(
            intent_a=INTENT_A, intent_b=INTENT_B, intent_ids=["i-1", "i-2"]
        )
    (payload,) = _send_payloads(caplog)
    assert datetime.fromisoformat(payload["occurred_at"]) == NOW  # Clock由来
    assert payload["system"] == "jev"
    assert payload["destination"] == "stub"
    assert payload["status"] == "ok"
    assert payload["intent_ids"] == ["i-1", "i-2"]  # Jev=2件(design §3.1)
    assert payload["user_id"] is None


async def test_judge_timeout_records_then_raises(clock, caplog):
    stub = StubLLM(delay_jev_ms=200)
    gw = _gateway(clock, stub, jev_s=0.05)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMTimeoutError):
            await gw.judge_pair(
                intent_a=INTENT_A, intent_b=INTENT_B, intent_ids=["i-1", "i-2"]
            )
    # jev_fallback未注入=第一候補と同一スタブ → timeoutは双障害(送信記録2件)
    p1, p2 = _send_payloads(caplog)
    assert [p["status"] for p in (p1, p2)] == ["timeout", "timeout"]
    assert [p["error_code"] for p in (p1, p2)] == [
        "LLMTimeoutError",
        "LLMTimeoutError",
    ]


async def test_judge_provider_error_records_then_raises(clock, caplog):
    gw = _gateway(clock, StubLLM(fail_jev=True))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMProviderError):
            await gw.judge_pair(
                intent_a=INTENT_A, intent_b=INTENT_B, intent_ids=["i-1", "i-2"]
            )
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "error"
    assert payload["error_code"] == "LLMProviderError"


async def test_judge_input_texts_never_appear_in_send_record(clock, caplog):
    # 正規化テキスト(soft/NG条件を含む)が記録へ出ない(01 第21節・design §1.3)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).judge_pair(
            intent_a="Intent A:\n[soft] 会社関係の人は避けたい",
            intent_b="Intent B:\n[soft] 軽く飲したい",
            intent_ids=["i-1", "i-2"],
        )
    assert "会社関係" not in caplog.text
    assert "飲したい" not in caplog.text
