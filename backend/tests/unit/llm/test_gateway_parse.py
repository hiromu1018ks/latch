"""Parser系統: 応答・送信記録・timeout・エラー(design §4-1〜3)。

送信内容=ユーザー入力テキスト(design §1.3) — だからこそ記録に内容が出ない
こと(08 第2.4節)をこの系統で最も厳しく検証する。
"""

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import LLMProviderError, LLMTimeoutError
from latch.llm.gateway import (
    TIMEOUT_EMBEDDING_S,
    TIMEOUT_JEV_S,
    TIMEOUT_PARSER_S,
    LLMGateway,
    Timeouts,
)
from latch.llm.records import LOGGER_NAME
from latch.llm.stub import StubLLM

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 9, 27)


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


def test_timeout_constants_match_07_section1():
    assert TIMEOUT_PARSER_S == 10.0
    assert TIMEOUT_EMBEDDING_S == 2.0
    assert TIMEOUT_JEV_S == 6.0


def test_default_timeouts_are_the_constants():
    t = Timeouts()
    assert (t.parser_s, t.embedding_s, t.jev_s) == (10.0, 2.0, 6.0)


async def test_parse_returns_stub_response(clock):
    result = await _gateway(clock, StubLLM()).parse_intent(
        text="今週末20時から駅前で軽く飲みたい", current_date=TODAY
    )
    assert result["category"]["primary"] == "meal"  # デフォルト応答(07 第2節スキーマ)
    assert result["time"]["start"] == "2026-09-27T19:00:00+09:00"


async def test_parse_records_send_record_with_clock_time(clock, caplog):
    # G0の証明(12 M0): スタブで3系統の呼び出しを記録できる — Parser系統分
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).parse_intent(
            text="今週末20時から駅前で軽く飲みたい",
            current_date=TODAY,
            user_id="user-1",
        )
    payloads = _send_payloads(caplog)
    assert len(payloads) == 1
    payload = payloads[0]
    # Review Focus #3: occurred_atはClock由来(FakeClockの時刻と一致=実時間ではない)
    occurred = datetime.fromisoformat(payload["occurred_at"])
    assert occurred == NOW
    assert occurred.utcoffset() == timedelta(0)
    assert payload["system"] == "intent_parser"
    assert payload["destination"] == "stub"
    assert payload["status"] == "ok"
    assert payload["error_code"] is None
    assert payload["intent_ids"] is None  # parseはIntent未作成(design §6-2)
    assert payload["user_id"] == "user-1"


async def test_parse_send_record_keys_are_exactly_the_allowlist(clock, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).parse_intent(text="t", current_date=TODAY)
    (payload,) = _send_payloads(caplog)
    assert set(payload) == {
        "occurred_at",
        "system",
        "destination",
        "status",
        "error_code",
        "intent_ids",
        "user_id",
    }


async def test_parse_input_text_never_appears_in_send_record(clock, caplog):
    # Review Focus #1: Parser系統の送信内容=ユーザー生テキスト。記録は内容を含まない
    secret = "会社の同僚と内緒の飲み会"
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).parse_intent(text=secret, current_date=TODAY)
    assert secret not in caplog.text
    assert "内緒" not in caplog.text


async def test_parse_timeout_records_then_raises(clock, caplog):
    # design §2.4/§2.6: 遅延>timeoutでtimeoutが発火(10 第4.5節のtimeoutエラー注入)
    stub = StubLLM(delay_parser_ms=200)
    gw = _gateway(clock, stub, parser_s=0.05)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMTimeoutError):
            await gw.parse_intent(text="t", current_date=TODAY)
    # Review Focus #2: 例外送出の前に記録が出ている(失敗時も記録が漏れない)
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "timeout"
    assert payload["error_code"] == "LLMTimeoutError"
    assert payload["system"] == "intent_parser"


async def test_parse_provider_error_records_then_raises(clock, caplog):
    # 10 第4.5節: 100%エラー注入(failフラグ)の再現
    gw = _gateway(clock, StubLLM(fail_parser=True))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMProviderError):
            await gw.parse_intent(text="t", current_date=TODAY)
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "error"
    assert payload["error_code"] == "LLMProviderError"


async def test_parse_unexpected_exception_is_wrapped(clock, caplog):
    # プロバイダがLLMError以外を送出してもLLMProviderErrorへ包む(design §3.1)
    class BrokenParser(StubLLM):
        async def complete_structured(self, text: str, current_date: date) -> dict:
            raise RuntimeError("provider crashed")

    gw = _gateway(clock, BrokenParser())
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMProviderError):
            await gw.parse_intent(text="t", current_date=TODAY)
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "error"
    assert payload["error_code"] == "RuntimeError"  # 元例外のIDのみ(08 第2.4節)
    assert "provider crashed" not in caplog.text  # 例外メッセージ(自由文)は記録しない


async def test_parse_cancellation_records_then_reraises(clock, caplog):
    # 08 第3節の開示要件: プロバイダ呼び出し開始後にキャンセルされても
    # 送信の事実は記録する(成否にかかわらず。design §3.1)。
    # キャンセル自体は飲み込まない — CancelledError をそのまま伝播させる。
    stub = StubLLM(delay_parser_ms=200)
    gw = _gateway(clock, stub)
    task = asyncio.create_task(gw.parse_intent(text="t", current_date=TODAY))
    await asyncio.sleep(0.02)  # 呼び出し開始後まで待つ(数十msの実時間待機)
    task.cancel()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(asyncio.CancelledError):
            await task
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "error"
    assert payload["error_code"] == "CancelledError"
    assert payload["system"] == "intent_parser"
