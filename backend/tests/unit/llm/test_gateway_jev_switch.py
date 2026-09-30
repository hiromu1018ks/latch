"""judge_pair切替マトリクスのunit試験(design §2.2の表・§4.1)。"""

import copy
import json
import logging
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import (
    JevOutputInvalidError,
    LLMConnectionError,
    LLMOverloadedError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from latch.llm.gateway import LLMGateway
from latch.llm.jev import JevJudgment
from latch.llm.providers import JevProvider
from latch.llm.records import LOGGER_NAME
from latch.llm.stub import DEFAULT_JEV_RESPONSE, StubLLM

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
IA = "Intent A:\n[hard] category: drinking"
IB = "Intent B:\n[hard] category: meal"


class _ThrowingJev(JevProvider):
    """指定例外を投げる第一候補スタブ(切替条件の注入)。"""

    name = "throwing"

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        raise self._exc


class _RecordingStub(StubLLM):
    """フォールバック側の正常スタブ(呼び出し回数を記録)。"""

    name = "fb"

    def __init__(self) -> None:
        super().__init__(
            jev_response={
                "model": None,
                "answers": {
                    k: (
                        {"type": "noul", "noul": 0.5}
                        if k in ("would_a_accept_b", "would_b_accept_a", "latent_yes")
                        else {"type": "score", "score": 2.0, "confidence": None}
                    )
                    for k in (
                        "would_a_accept_b",
                        "would_b_accept_a",
                        "latent_yes",
                        "purpose_fit",
                        "mood_fit",
                        "timing_fit",
                        "social_fit",
                    )
                },
                "usage": {},
            }
        )
        self.name = "fb"  # StubLLM.__init__のname上書きを差し替える
        self.calls = 0

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        self.calls += 1
        return await super().judge(intent_a, intent_b)


def _stub_envelope(model: str = "jev-1.13.0") -> dict:
    env = copy.deepcopy(DEFAULT_JEV_RESPONSE)
    env["model"] = model
    return env


def _gateway(first: JevProvider, fallback: JevProvider) -> LLMGateway:
    return LLMGateway(
        clock=FakeClock(NOW),
        parser=StubLLM(),
        embedding=StubLLM(),
        jev=first,
        jev_fallback=fallback,
    )


def _send_payloads(caplog) -> list[dict]:
    messages = [r for r in caplog.records if r.name == LOGGER_NAME]
    return [json.loads(m.getMessage()) for m in messages]


async def test_primary_ok_returns_typesafe_judgment(caplog):
    fb = _RecordingStub()
    gw = _gateway(StubLLM(jev_response=_stub_envelope("jev-1.13.0")), fb)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        j = await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert isinstance(j, JevJudgment)
    assert j.provider == "typesafe_jev"
    assert j.model == "jev-1.13.0"
    assert j.result["jev_5axis"]["purpose_fit"]["value"] == 0.5  # 2.0/4
    assert fb.calls == 0  # 切替不要
    assert len(_send_payloads(caplog)) == 1  # 第一候補のみ


async def test_primary_timeout_switches_to_fallback(caplog):
    fb = _RecordingStub()
    gw = _gateway(_ThrowingJev(LLMTimeoutError("t")), fb)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        j = await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert j.provider == "fallback_llm" and j.model is None
    assert fb.calls == 1
    payloads = _send_payloads(caplog)
    assert [p["destination"] for p in payloads] == ["throwing", "fb"]  # 送信記録2件
    assert payloads[0]["status"] == "error"


@pytest.mark.parametrize(
    "exc",
    [LLMRateLimitError("429"), LLMOverloadedError("529"), LLMConnectionError("conn")],
)
async def test_switch_conditions_all_switch(exc, caplog):
    gw = _gateway(_ThrowingJev(exc), _RecordingStub())
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        j = await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert j.provider == "fallback_llm"
    assert len(_send_payloads(caplog)) == 2


async def test_primary_provider_error_does_not_switch(caplog):
    """400系(LLMProviderError)は切替条件外で伝播(07 §4)。"""
    fb = _RecordingStub()
    gw = _gateway(_ThrowingJev(LLMProviderError("400")), fb)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMProviderError):
            await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert fb.calls == 0
    assert len(_send_payloads(caplog)) == 1  # 切替なし


async def test_primary_invalid_output_does_not_switch(caplog):
    """出力検証失敗は再試行も切替もしない(Review Focus 1・07 §4)。"""
    bad = _stub_envelope()
    del bad["answers"]["latent_yes"]  # キー欠損=検証失敗
    fb = _RecordingStub()
    gw = _gateway(StubLLM(jev_response=bad), fb)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(JevOutputInvalidError) as ei:
            await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert fb.calls == 0
    assert ei.value.provider == "typesafe_jev"  # 検証対象の経路が載る(§9-4)
    assert len(_send_payloads(caplog)) == 1


async def test_fallback_failure_propagates(caplog):
    """フォールバック失敗(双障害)はそのまま伝播(D-15)。"""
    gw = _gateway(
        _ThrowingJev(LLMTimeoutError("t")), _ThrowingJev(LLMTimeoutError("fb"))
    )
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMTimeoutError):
            await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert len(_send_payloads(caplog)) == 2


async def test_fallback_invalid_output_propagates(caplog):
    bad = StubLLM(jev_response={"answers": {}})
    gw = _gateway(_ThrowingJev(LLMRateLimitError("429")), bad)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(JevOutputInvalidError) as ei:
            await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert ei.value.provider == "fallback_llm"


def test_jev_fallback_defaults_to_first_candidate():
    """jev_fallback未注入=第一候補と同一(既存直構築試験を無変更で通す既定)。"""
    stub = StubLLM()
    gw = LLMGateway(clock=FakeClock(NOW), parser=stub, embedding=stub, jev=stub)
    assert gw._jev_fallback is gw._jev


# -- 公開直呼びIF call_jev_first/call_jev_fallback(ws-8 design §2.8・§9-5) --


async def test_call_jev_first_returns_judgment_with_usage(caplog):
    """公開直呼びIF(第一候補): provider/model/usageをJevJudgmentへ載せる。"""
    gw = _gateway(StubLLM(), _RecordingStub())
    judgment = await gw.call_jev_first(
        intent_a=IA, intent_b=IB, intent_ids=["i-a", "i-b"]
    )
    assert judgment.provider == "typesafe_jev"
    assert judgment.model == "jev-1.13.0"
    assert judgment.usage == {"input_tokens": 0, "output_tokens": 0}
    assert judgment.result["would_a_accept_b"] == 0.5


async def test_call_jev_fallback_returns_judgment():
    """公開直呼びIF(フォールバック): provider=fallback_llm・model=None。"""
    gw = _gateway(StubLLM(), _RecordingStub())
    judgment = await gw.call_jev_fallback(
        intent_a=IA, intent_b=IB, intent_ids=["i-a", "i-b"]
    )
    assert judgment.provider == "fallback_llm"
    assert judgment.model is None
    assert judgment.result["would_b_accept_a"] == 0.5


async def test_call_jev_first_does_not_switch_on_rate_limit():
    """直呼びIFは切替しない(第一候補の例外はそのまま伝播・測定の分離)。"""
    first = _ThrowingJev(LLMRateLimitError("429"))
    gw = _gateway(first, _RecordingStub())
    with pytest.raises(LLMRateLimitError):
        await gw.call_jev_first(intent_a=IA, intent_b=IB, intent_ids=["i-a", "i-b"])


async def test_call_jev_first_invalid_output_propagates_with_provider():
    """出力検証失敗はprovider="typesafe_jev"付きで伝播(judge_pairと同一経路)。"""

    class _Invalid(JevProvider):
        name = "invalid"

        async def judge(self, intent_a: str, intent_b: str) -> dict:
            return {"model": "jev-1.13.0", "answers": {}, "usage": {}}

    gw = _gateway(_Invalid(), _RecordingStub())
    with pytest.raises(JevOutputInvalidError) as ei:
        await gw.call_jev_first(intent_a=IA, intent_b=IB, intent_ids=["i-a", "i-b"])
    assert ei.value.provider == "typesafe_jev"
