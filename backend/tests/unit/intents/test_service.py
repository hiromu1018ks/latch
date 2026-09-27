"""IntentParseService ユースケース(design §4.1-4)。

Protocolスタブで決定的に、(c)(d)は実Gateway+StubLLMで実際の例外経路を検証。
ログ・例外にユーザー本文が出ないことも確認する(08 §2.4)。
"""

import copy
import logging
import uuid
from datetime import UTC, date, datetime

import pytest

from latch.core.clock import FakeClock
from latch.intents.errors import (
    DependencyUnavailableError,
    LLMUnavailableError,
    UnstructurableError,
)
from latch.intents.schema import WARNING_MESSAGE_NG_DOWNGRADED, ParserOutput
from latch.intents.service import IntentParseService
from latch.llm.gateway import LLMGateway, Timeouts
from latch.llm.stub import DEFAULT_PARSER_RESPONSE, StubLLM

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)  # JST 09-27 21:00


class StubParser:
    """SupportsParseIntent のテストスタブ。受け取った引数を記録する。"""

    def __init__(self, response: object = None, error: Exception | None = None):
        self.response = (
            copy.deepcopy(DEFAULT_PARSER_RESPONSE) if response is None else response
        )
        self.error = error
        self.calls: list[dict] = []

    async def parse_intent(
        self, *, text: str, current_date: date, user_id: str | None = None
    ) -> dict:
        self.calls.append(
            {"text": text, "current_date": current_date, "user_id": user_id}
        )
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.response)


def _clock() -> FakeClock:
    return FakeClock(NOW)


def _service(parser: StubParser, lookup) -> IntentParseService:
    return IntentParseService(clock=_clock(), parser=parser, user_lookup=lookup)


def _lookup_returning(user_id):
    async def lookup(provider: str, subject: str):
        return user_id

    return lookup


async def _lookup_none(provider: str, subject: str):
    return None


def _lookup_failing():
    async def lookup(provider: str, subject: str):
        raise RuntimeError("db down")

    return lookup


# --- (a) ハッパス(design §4.1-4a)---


async def test_happy_path_returns_parse_result_with_no_warnings():
    parser = StubParser()
    svc = _service(parser, _lookup_returning(None))
    result = await svc.parse(
        text="今夜20時から天文館で軽く飲みたい",
        auth_provider="google",
        auth_subject="sub-1",
    )
    assert result.structured_intent == ParserOutput.model_validate(
        copy.deepcopy(DEFAULT_PARSER_RESPONSE)
    )
    assert result.warnings == []
    assert parser.calls[0]["text"] == "今夜20時から天文館で軽く飲みたい"


async def test_multiple_ng_unverifiable_produce_warning_per_condition():
    """(a/D-04) ng_unverifiableの要素ごとに1件のwarning。"""
    response = copy.deepcopy(DEFAULT_PARSER_RESPONSE)
    response["ng_unverifiable"] = ["会社関係の人は避けたい", "元同僚は避けたい"]
    svc = _service(StubParser(response=response), _lookup_none)
    result = await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert len(result.warnings) == 2
    first, second = result.warnings
    assert first.code == "NG_CONDITION_DOWNGRADED"
    assert first.condition == "会社関係の人は避けたい"
    assert first.message == WARNING_MESSAGE_NG_DOWNGRADED
    assert second.condition == "元同僚は避けたい"
    assert second.message == WARNING_MESSAGE_NG_DOWNGRADED


# --- (b) 規則5正規化(design §4.1-4b・§2.5)---


async def test_negative_constraints_are_normalized_into_ng_unverifiable():
    """LLMが規則5違反でnegative_constraints非空→ng_unverifiableへ結合し警告も出る。"""
    response = copy.deepcopy(DEFAULT_PARSER_RESPONSE)
    response["negative_constraints"] = ["会社関係の人は避けたい"]
    svc = _service(StubParser(response=response), _lookup_none)
    result = await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert result.structured_intent.negative_constraints == []
    assert result.structured_intent.ng_unverifiable == ["会社関係の人は避けたい"]
    assert [w.condition for w in result.warnings] == ["会社関係の人は避けたい"]


# --- (c) 503切替: 実Gateway+StubLLM(design §4.1-4c)---


def _real_gateway(clock: FakeClock, stub: StubLLM, **timeout_s: float) -> LLMGateway:
    return LLMGateway(
        clock=clock,
        parser=stub,
        embedding=stub,
        jev=stub,
        timeouts=Timeouts(**timeout_s) if timeout_s else None,
    )


async def test_provider_error_maps_to_503_llm_unavailable():
    clock = _clock()
    gateway = _real_gateway(clock, StubLLM(fail_parser=True))
    svc = IntentParseService(clock=clock, parser=gateway, user_lookup=_lookup_none)
    with pytest.raises(LLMUnavailableError) as ei:
        await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert ei.value.http_status == 503
    assert ei.value.code == "LLM_UNAVAILABLE"


async def test_timeout_maps_to_503_llm_unavailable():
    clock = _clock()
    gateway = _real_gateway(clock, StubLLM(delay_parser_ms=200), parser_s=0.05)
    svc = IntentParseService(clock=clock, parser=gateway, user_lookup=_lookup_none)
    with pytest.raises(LLMUnavailableError):
        await svc.parse(text="t", auth_provider="google", auth_subject="s")


# --- (d) 422切替(design §4.1-4d・§2.4)---


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.pop("category"), id="category-missing"),
        pytest.param(lambda r: r.pop("time"), id="time-missing"),
        pytest.param(lambda r: r.pop("location"), id="location-missing"),
    ],
)
async def test_missing_required_field_maps_to_422_unstructurable(mutate):
    response = copy.deepcopy(DEFAULT_PARSER_RESPONSE)
    mutate(response)
    svc = _service(StubParser(response=response), _lookup_none)
    with pytest.raises(UnstructurableError) as ei:
        await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert ei.value.http_status == 422
    assert ei.value.code == "VALIDATION_ERROR"


async def test_non_dict_response_maps_to_422_unstructurable():
    """JSON文字列を返した場合も422へ一元(design §2.4)。"""
    svc = _service(StubParser(response='{"category": "broken"'), _lookup_none)
    with pytest.raises(UnstructurableError):
        await svc.parse(text="t", auth_provider="google", auth_subject="s")


# --- (e) user_lookup帰属(design §4.1-4e・§2.3)---


async def test_lookup_hit_passes_str_user_id_to_parser():
    user_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    parser = StubParser()
    svc = _service(parser, _lookup_returning(user_id))
    await svc.parse(text="t", auth_provider="google", auth_subject="sub-1")
    assert parser.calls[0]["user_id"] == str(user_id)


async def test_lookup_none_passes_none_user_id():
    parser = StubParser()
    svc = _service(parser, _lookup_none)
    await svc.parse(text="t", auth_provider="google", auth_subject="sub-1")
    assert parser.calls[0]["user_id"] is None


async def test_lookup_failure_maps_to_503_dependency_unavailable():
    svc = _service(StubParser(), _lookup_failing())
    with pytest.raises(DependencyUnavailableError) as ei:
        await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert ei.value.http_status == 503
    assert ei.value.code == "DEPENDENCY_UNAVAILABLE"


# --- (f) current_date はJST暦日付(design §4.1-4f・確定値13)---


async def test_current_date_uses_jst_calendar_date_across_midnight():
    """UTC 14:59=JST 23:59(同日) / UTC 15:00=JST 翌0:00(翌日)。"""
    parser = StubParser()
    clock = FakeClock(datetime(2026, 9, 27, 14, 59, 0, tzinfo=UTC))
    svc = IntentParseService(clock=clock, parser=parser, user_lookup=_lookup_none)
    await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert parser.calls[0]["current_date"] == date(2026, 9, 27)

    clock.set(datetime(2026, 9, 27, 15, 0, 0, tzinfo=UTC))
    await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert parser.calls[1]["current_date"] == date(2026, 9, 28)


# --- (g) 機微非混入(08 §2.4・Review Focus #4)---


async def test_user_text_never_appears_in_logs(caplog):
    secret = "会社の同僚と内緒の飲み会"
    svc = _service(StubParser(), _lookup_none)
    with caplog.at_level(logging.INFO):
        await svc.parse(text=secret, auth_provider="google", auth_subject="s")
    assert secret not in caplog.text


async def test_error_messages_contain_no_user_text():
    """422/503例外のメッセージは固定文言のみ(ハンドラがenvelopeへ出すため)。"""
    response = copy.deepcopy(DEFAULT_PARSER_RESPONSE)
    response.pop("category")
    secret = "内緒の飲み会の件"
    svc = _service(StubParser(response=response), _lookup_none)
    with pytest.raises(UnstructurableError) as ei:
        await svc.parse(text=secret, auth_provider="google", auth_subject="s")
    assert secret not in str(ei.value)
