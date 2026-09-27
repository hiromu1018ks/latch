"""mapping変換(design §4.1-2)。alcohol確定・ng降格・補完消費・draft正規化・
実質変更判定・応答再構成を純関数レベルで確定する。"""

import uuid
from datetime import UTC, datetime

import pytest

from latch.intents.completion import (
    DEFAULT_PARTICIPANTS,
    DEFAULT_RADIUS_M,
    default_time_end,
    nearest_expires_at,
)
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.mapping import (
    columns_from_row,
    differs_from_row,
    resolve_for_active,
    resolve_for_draft,
    to_response_structured,
)
from latch.intents.store import IntentRow

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-27 21:00
START = datetime(2026, 9, 27, 21, 0, 0, tzinfo=UTC)  # tz表現はどちらでも同じ瞬間


def _input(**overrides) -> StructuredIntentInput:
    data = {
        "category": {"primary": "drinking", "secondary": "焼肉"},
        "alcohol_involved": True,
        "time": {"start": START.isoformat(), "end": "2026-09-28T00:00:00+09:00"},
        "location": {"name": "天文館", "radius_m": 2000},
        "budget": {"max": 5000, "currency": "JPY"},
        "participants": {"min": 2, "max": 4},
        "visibility": "summary_only",
        "notification_level": "nearby_also",
        "expires_at": "2026-09-27T23:30:00+09:00",
        "soft_constraints": ["軽く飲みたい"],
        "ng_unverifiable": ["会社関係の人は避けたい"],
        "negative_constraints": [],
    }
    data.update(overrides)
    return StructuredIntentInput.model_validate(data)


def _row(**overrides) -> IntentRow:
    base = dict(
        id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        user_id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
        category_primary="drinking",
        alcohol_involved=True,
        raw_text="原文",
        structured_data={},
        geo_radius_m=2000,
        budget_max=5000,
        participants_min=2,
        participants_max=4,
        visibility="summary_only",
        notification_level="nearby_also",
        status="draft",
        version=1,
        time_start=START,
        time_end=datetime(2026, 9, 28, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2026, 9, 27, 23, 30, 0, tzinfo=UTC),
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return IntentRow(**base)


# --- (a) resolve_for_active ---


def test_active_drinking_forces_alcohol_true_over_client_value():
    """07規則7: drinkingはクライアント修正値(false)より優先してtrue確定。"""
    cols = resolve_for_active(_input(alcohol_involved=False), now=NOW)
    assert cols.alcohol_involved is True


def test_active_non_drinking_keeps_client_alcohol():
    cols = resolve_for_active(
        _input(category={"primary": "meal"}, alcohol_involved=True), now=NOW
    )
    assert cols.alcohol_involved is True
    cols2 = resolve_for_active(
        _input(category={"primary": "meal"}, alcohol_involved=False), now=NOW
    )
    assert cols2.alcohol_involved is False


def test_active_unspecified_alcohol_defaults_false():
    data = _input().model_dump(exclude_none=True)
    data["category"] = {"primary": "meal"}
    data["alcohol_involved"] = None
    cols = resolve_for_active(StructuredIntentInput.model_validate(data), now=NOW)
    assert cols.alcohol_involved is False


def test_active_completes_time_end_from_start_plus_3h():
    inp = _input()
    inp = StructuredIntentInput.model_validate(
        {**inp.model_dump(exclude_none=True), "time": {"start": START.isoformat()}}
    )
    cols = resolve_for_active(inp, now=NOW)
    assert cols.time_end == default_time_end(START)


def test_active_completes_participants_radius_and_expires_at():
    inp = StructuredIntentInput.model_validate(
        {
            "category": {"primary": "meal"},
            "time": {"start": START.isoformat()},
            "location": {"name": "天文館"},
        }
    )
    cols = resolve_for_active(inp, now=NOW)
    assert (cols.participants_min, cols.participants_max) == DEFAULT_PARTICIPANTS
    assert cols.geo_radius_m == DEFAULT_RADIUS_M
    assert cols.expires_at == nearest_expires_at(START, NOW)
    assert cols.visibility == "hidden_until_match"
    assert cols.notification_level == "proposals_only"


def test_active_keeps_explicit_expires_at():
    explicit = "2026-09-28T12:00:00+09:00"
    inp = _input(expires_at=explicit)
    cols = resolve_for_active(inp, now=NOW)
    assert cols.expires_at is not None
    assert cols.expires_at.isoformat() == explicit


def test_structured_data_shape_with_location_name_and_downgraded():
    """05 §2の5キー+location_name・ng由来はdowngraded_from_ng=true(確定値10・34)。"""
    cols = resolve_for_active(_input(), now=NOW)
    sd = cols.structured_data
    assert set(sd) == {
        "category_secondary",
        "soft_constraints",
        "negative_constraints",
        "time_flexibility_minutes",
        "location_flexibility",
        "location_name",
    }
    assert sd["category_secondary"] == "焼肉"
    assert sd["location_name"] == "天文館"
    assert sd["soft_constraints"] == [
        {"text": "軽く飲みたい", "downgraded_from_ng": False},
        {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
    ]
    assert sd["negative_constraints"] == []
    assert sd["time_flexibility_minutes"] is None
    assert sd["location_flexibility"] is None


def test_non_empty_negative_constraints_are_saved_as_empty():
    """FR-42: negative_constraintsを受け取っても保存は常に空配列。"""
    cols = resolve_for_active(_input(negative_constraints=["上司は避けたい"]), now=NOW)
    assert cols.structured_data["negative_constraints"] == []


def test_active_without_required_fields_raises_value_error():
    """resolve_for_activeは検証済み前提(design §2.3)。未検証入力はValueError。"""
    with pytest.raises(ValueError):
        resolve_for_active(StructuredIntentInput.model_validate({}), now=NOW)


# --- (b) resolve_for_draft ---


def test_draft_resolves_without_completion():
    inp = _input()
    cols = resolve_for_draft(inp)
    assert cols.category_primary == "drinking"
    assert cols.time_start == START
    assert cols.time_end is not None  # 指定値のみ(この入力はend付き)


def test_draft_empty_input_normalizes_to_defaults():
    """§2.4表: 未指定は ''/False/(2,2)/既定値/NULL。補完なし。"""
    cols = resolve_for_draft(StructuredIntentInput.model_validate({}))
    assert cols.category_primary == ""
    assert cols.alcohol_involved is False
    assert (cols.participants_min, cols.participants_max) == DEFAULT_PARTICIPANTS
    assert cols.visibility == "hidden_until_match"
    assert cols.notification_level == "proposals_only"
    assert cols.time_start is None
    assert cols.time_end is None
    assert cols.expires_at is None
    assert cols.geo_radius_m is None
    assert cols.structured_data["soft_constraints"] == []
    assert cols.structured_data["location_name"] is None


def test_draft_does_not_force_alcohol_for_drinking():
    """draftではdrinkingでもクライアント値のまま(確定はactive保存時のみ)。"""
    cols = resolve_for_draft(_input(alcohol_involved=False))
    assert cols.alcohol_involved is False


# --- 実質変更判定(design §2.7)---


def test_differs_from_row_false_for_identical_draft_columns():
    cols = resolve_for_draft(_input())
    row = _row(
        raw_text="原文",
        category_primary=cols.category_primary,
        alcohol_involved=cols.alcohol_involved,
        structured_data=cols.structured_data,
        budget_max=cols.budget_max,
        participants_min=cols.participants_min,
        participants_max=cols.participants_max,
        visibility=cols.visibility,
        notification_level=cols.notification_level,
        time_start=cols.time_start,
        time_end=cols.time_end,
        expires_at=cols.expires_at,
        geo_radius_m=cols.geo_radius_m,
    )
    assert differs_from_row(_with_raw(cols, "原文"), row) is False


def _with_raw(cols, raw_text):
    from dataclasses import replace

    return replace(cols, raw_text=raw_text)


def _row_of(cols) -> IntentRow:
    """colsと同一の保存列を持つ行(differs_from_rowの整合確認用)。"""
    return _row(
        raw_text=cols.raw_text,
        category_primary=cols.category_primary,
        alcohol_involved=cols.alcohol_involved,
        structured_data=cols.structured_data,
        budget_max=cols.budget_max,
        participants_min=cols.participants_min,
        participants_max=cols.participants_max,
        visibility=cols.visibility,
        notification_level=cols.notification_level,
        time_start=cols.time_start,
        time_end=cols.time_end,
        expires_at=cols.expires_at,
        geo_radius_m=cols.geo_radius_m,
    )


def test_differs_from_row_true_for_any_compared_field_change():
    from dataclasses import replace as dc_replace

    cols = _with_raw(resolve_for_draft(_input()), "原文")
    same = _row_of(cols)
    assert differs_from_row(_with_raw(cols, "変更後"), same) is True  # raw_text
    assert differs_from_row(cols, same) is False  # 整合確認
    assert differs_from_row(cols, dc_replace(same, category_primary="meal")) is True
    assert differs_from_row(cols, dc_replace(same, geo_radius_m=999)) is True


# --- columns_from_row / to_response_structured ---


def test_columns_from_row_round_trips_saved_columns():
    row = _row(structured_data={"location_name": "天文館"})
    cols = columns_from_row(row)
    assert cols.raw_text == "原文"
    assert cols.category_primary == "drinking"
    assert cols.structured_data == {"location_name": "天文館"}
    assert cols.geo_lon is None and cols.geo_lat is None


def test_to_response_structured_splits_downgraded_and_location_name():
    sd = {
        "category_secondary": "焼肉",
        "soft_constraints": [
            {"text": "軽く飲みたい", "downgraded_from_ng": False},
            {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
        ],
        "negative_constraints": [],
        "time_flexibility_minutes": None,
        "location_flexibility": None,
        "location_name": "天文館",
    }
    out = to_response_structured(_row(structured_data=sd))
    assert out["category"] == {"primary": "drinking", "secondary": "焼肉"}
    assert out["location"]["name"] == "天文館"
    assert out["location"]["radius_m"] == 2000
    assert out["location"]["flexibility"] is None
    assert out["soft_constraints"] == ["軽く飲みたい"]
    assert out["ng_unverifiable"] == ["会社関係の人は避けたい"]
    assert out["negative_constraints"] == []
    assert out["visibility"] == "summary_only"
    assert out["budget"] == {"max": 5000, "currency": "JPY"}
    assert out["participants"] == {"min": 2, "max": 4}


def test_to_response_structured_partial_draft_row():
    """draft部分行(category=''・時刻NULL)の再構成(§4.1-2c)。"""
    out = to_response_structured(
        _row(
            category_primary="",
            alcohol_involved=False,
            structured_data={},
            geo_radius_m=None,
            budget_max=None,
            time_start=None,
            time_end=None,
            expires_at=None,
        )
    )
    assert out["category"]["primary"] is None
    assert out["category"]["secondary"] is None
    assert out["time"]["start"] is None
    assert out["location"]["name"] is None
    assert out["location"]["radius_m"] is None
    assert out["expires_at"] is None
    assert out["alcohol_involved"] is False
