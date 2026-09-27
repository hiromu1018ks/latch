"""intent_inputモデルの受入・拒否(design §4.1-1・§2.3〜§2.4)。

拒否はRequestValidationError→既存422ハンドラ経路(FastAPIに載せるのは
Task 10)。ここではモデル単体の検証を確定する。
"""

import pytest
from pydantic import ValidationError

from latch.intents.intent_input import (
    IntentCreateRequest,
    IntentPatchRequest,
    StructuredIntentInput,
)


def _full_structured() -> dict:
    """05 §5 POST /v1/intents応答例と同じ形の全フィールド値。"""
    return {
        "category": {"primary": "drinking", "secondary": "焼肉"},
        "alcohol_involved": True,
        "time": {
            "start": "2026-09-27T21:00:00+09:00",
            "end": "2026-09-28T00:00:00+09:00",
        },
        "location": {"name": "天文館", "radius_m": 2000},
        "budget": {"max": 5000, "currency": "JPY"},
        "participants": {"min": 2, "max": 4},
        "visibility": "hidden_until_match",
        "notification_level": "proposals_only",
        "expires_at": "2026-09-27T23:30:00+09:00",
        "soft_constraints": ["軽く飲みたい"],
        "ng_unverifiable": ["会社関係の人は避けたい"],
        "negative_constraints": [],
    }


# --- 受入 ---


def test_create_request_accepts_full_structured_intent():
    req = IntentCreateRequest.model_validate(
        {
            "raw_text": "今夜20時から天文館で軽く飲みたい",
            "structured_intent": _full_structured(),
        }
    )
    assert req.status == "active"
    assert req.structured_intent.category.primary == "drinking"
    assert req.structured_intent.location.radius_m == 2000


def test_create_status_defaults_to_active_and_structured_optional():
    """status省略=active・structured_intent省略可(draftのraw_textのみ保存)。"""
    req = IntentCreateRequest.model_validate({"raw_text": "下書き"})
    assert req.status == "active"
    assert req.structured_intent is None


def test_partial_structured_intent_is_accepted_for_draft():
    """draftの部分的な中途データ(カテゴリのみ等)も形式正なら受理(確定値3)。"""
    inp = StructuredIntentInput.model_validate(
        {"category": {"primary": "meal"}, "soft_constraints": ["静かな店"]}
    )
    assert inp.category.primary == "meal"
    assert inp.time is None
    assert inp.location is None
    assert inp.soft_constraints == ["静かな店"]


def test_patch_request_accepts_null_status_and_structured():
    req = IntentPatchRequest.model_validate({"raw_text": "下書き"})
    assert req.status is None
    assert req.structured_intent is None


def test_extra_keys_are_ignored():
    inp = StructuredIntentInput.model_validate({"unknown_key": 1})
    assert inp.alcohol_involved is None


# --- 拒否(値域・形式)---


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(raw_text=""), id="raw-text-empty"),
        pytest.param(lambda d: d.update(raw_text="あ" * 301), id="raw-text-301"),
        pytest.param(
            lambda d: d["structured_intent"]["category"].update(primary="shopping"),
            id="primary-not-3-values",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["participants"].update(min=0),
            id="participants-min-0",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["participants"].update(max=5),
            id="participants-max-5",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["budget"].update(max=-1),
            id="budget-max-minus",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["location"].update(radius_m=0),
            id="radius-0",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["time"].update(
                start="2026-09-27T21:00:00"
            ),
            id="naive-time-start",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["time"].update(end="2026-09-27T23:00:00"),
            id="naive-time-end",
        ),
        pytest.param(
            lambda d: d["structured_intent"].update(expires_at="2026-09-27T23:30:00"),
            id="naive-expires-at",
        ),
        pytest.param(
            lambda d: d["structured_intent"].update(visibility="public"),
            id="visibility-invalid",
        ),
        pytest.param(
            lambda d: d["structured_intent"].update(notification_level="all"),
            id="notification-level-invalid",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["location"].update(name=""),
            id="location-name-empty",
        ),
    ],
)
def test_invalid_values_are_rejected(mutate):
    data = {"raw_text": "本文", "structured_intent": _full_structured()}
    mutate(data)
    with pytest.raises(ValidationError):
        IntentCreateRequest.model_validate(data)


@pytest.mark.parametrize("status", ["drafty", "ACTIVE", None])
def test_create_status_must_be_active_or_draft(status):
    data = {"raw_text": "本文", "status": status}
    with pytest.raises(ValidationError):
        IntentCreateRequest.model_validate(data)


def test_patch_raw_text_is_required():
    with pytest.raises(ValidationError):
        IntentPatchRequest.model_validate({"status": "draft"})
