"""ParserOutput受入・拒否(design §4.1-2)。拒否はすべてValidationError→422。"""

import copy
from datetime import datetime

import pytest
from pydantic import ValidationError

from latch.intents import WARNING_MESSAGE_NG_DOWNGRADED, ParserOutput
from latch.llm.stub import DEFAULT_PARSER_RESPONSE


def _response() -> dict:
    return copy.deepcopy(DEFAULT_PARSER_RESPONSE)


def test_accepts_stub_default_response():
    out = ParserOutput.model_validate(_response())
    assert out.category.primary == "meal"
    assert out.alcohol_involved is False
    assert out.time.start == datetime.fromisoformat("2026-09-27T19:00:00+09:00")
    assert out.time.end is None
    assert out.location.name == "東京駅"
    assert out.location.radius_m is None
    assert out.budget.max is None
    assert out.budget.currency == "JPY"
    assert out.participants.min is None
    assert out.soft_constraints == []
    assert out.negative_constraints == []
    assert out.ng_unverifiable == []


def test_accepts_full_response_with_all_fields():
    raw = _response()
    raw["category"]["secondary"] = "焼肉"
    raw["time"]["end"] = "2026-09-27T22:00:00+09:00"
    raw["location"]["radius_m"] = 2000
    raw["budget"]["max"] = 5000
    raw["participants"] = {"min": 2, "max": 4}
    raw["soft_constraints"] = ["軽く飲みたい"]
    raw["ng_unverifiable"] = ["会社関係の人は避けたい"]
    out = ParserOutput.model_validate(raw)
    assert out.category.secondary == "焼肉"
    assert out.time.end == datetime.fromisoformat("2026-09-27T22:00:00+09:00")
    assert out.location.radius_m == 2000
    assert out.budget.max == 5000
    assert out.participants.min == 2
    assert out.participants.max == 4
    assert out.soft_constraints == ["軽く飲みたい"]


def test_accepts_minimal_response_with_optional_groups_omitted():
    """budget/participants/3配列の省略は既定で受理(規則1への寛容な解釈)。"""
    raw = _response()
    del raw["budget"]
    del raw["participants"]
    del raw["soft_constraints"]
    del raw["negative_constraints"]
    del raw["ng_unverifiable"]
    out = ParserOutput.model_validate(raw)
    assert out.budget.max is None
    assert out.budget.currency == "JPY"
    assert out.participants.min is None
    assert out.soft_constraints == []
    assert out.negative_constraints == []
    assert out.ng_unverifiable == []


def test_accepts_non_jst_offset():
    """tz-awareなら受理(JST限定しない。design §6-5)。"""
    raw = _response()
    raw["time"]["start"] = "2026-09-27T10:00:00+00:00"
    out = ParserOutput.model_validate(raw)
    assert out.time.start.utcoffset().total_seconds() == 0


def test_ignores_extra_keys():
    raw = _response()
    raw["unknown_field"] = {"nested": 1}
    out = ParserOutput.model_validate(raw)
    assert out.category.primary == "meal"


EXPECTED_WARNING_MESSAGE = "この条件は確実には除外できません。参考条件として扱います"


def test_warning_message_constant():
    assert WARNING_MESSAGE_NG_DOWNGRADED == EXPECTED_WARNING_MESSAGE


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.pop("category"), id="category-missing"),
        pytest.param(
            lambda r: r["category"].update({"primary": "shopping"}),
            id="primary-invalid",
        ),
        pytest.param(lambda r: r.pop("time"), id="time-missing"),
        pytest.param(
            lambda r: r["time"].update({"start": "2026-09-27T19:00:00"}),
            id="start-naive",
        ),
        pytest.param(
            lambda r: r["time"].update({"end": "2026-09-27T22:00:00"}),
            id="end-naive",
        ),
        pytest.param(lambda r: r.pop("location"), id="location-missing"),
        pytest.param(lambda r: r["location"].update({"name": ""}), id="name-empty"),
        pytest.param(lambda r: r.pop("alcohol_involved"), id="alcohol-missing"),
        pytest.param(
            lambda r: r["time"].update({"flexibility_minutes": 30}),
            id="flexibility-minutes-non-null",
        ),
        pytest.param(
            lambda r: r["location"].update({"flexibility": "near"}),
            id="location-flexibility-non-null",
        ),
        pytest.param(
            lambda r: r["budget"].update({"currency": "USD"}), id="currency-usd"
        ),
    ],
)
def test_rejects_invalid_responses(mutate):
    raw = _response()
    mutate(raw)
    with pytest.raises(ValidationError):
        ParserOutput.model_validate(raw)


def test_rejects_non_dict_input():
    """JSON文字列等のdictでない応答もValidationError(design §2.4の一元)。"""
    with pytest.raises(ValidationError):
        ParserOutput.model_validate('{"category": "broken"}')
