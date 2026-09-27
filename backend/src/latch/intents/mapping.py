"""入力→保存列への変換(design §2.3〜§2.4・§2.6〜§2.7)。

alcohol確定(07規則7)・補完規則の消費(completion.py)・draft正規化を
1箇所に集約する。検証(必須3・時刻・年齢)はserviceが先行する前提の純変換。
structured_dataは05 §2の5キー+location_name(design §6-1)。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING

from latch.intents.completion import (
    DEFAULT_PARTICIPANTS,
    DEFAULT_RADIUS_M,
    default_time_end,
    nearest_expires_at,
)
from latch.intents.intent_input import StructuredIntentInput

if TYPE_CHECKING:
    from latch.intents.store import IntentRow

DEFAULT_VISIBILITY = "hidden_until_match"
DEFAULT_NOTIFICATION_LEVEL = "proposals_only"


@dataclass(frozen=True)
class ResolvedColumns:
    """保存列一式(INSERT/UPDATEの直接の入力 — design §3.1)。

    raw_textはリクエスト直下の値のためserviceがreplaceで設定する。
    geo_lon/geo_latはactive経路でジオコーディング結果をserviceが設定
    (draft・未確定はNone)。versionはserviceが上書き(design §2.7)。
    """

    raw_text: str = ""
    category_primary: str = ""
    alcohol_involved: bool = False
    structured_data: dict = field(default_factory=dict)
    budget_max: int | None = None
    participants_min: int = DEFAULT_PARTICIPANTS[0]
    participants_max: int = DEFAULT_PARTICIPANTS[1]
    visibility: str = DEFAULT_VISIBILITY
    notification_level: str = DEFAULT_NOTIFICATION_LEVEL
    time_start: datetime | None = None
    time_end: datetime | None = None
    expires_at: datetime | None = None
    geo_radius_m: int | None = None
    geo_lon: float | None = None
    geo_lat: float | None = None
    version: int = 1


def _soft_constraints(inp: StructuredIntentInput) -> list[dict]:
    """soft由来が先・ng_unverifiable由来はdowngraded_from_ng=true(05 §2)。"""
    return [
        {"text": t, "downgraded_from_ng": False} for t in (inp.soft_constraints or [])
    ] + [{"text": t, "downgraded_from_ng": True} for t in (inp.ng_unverifiable or [])]


def _structured_data(inp: StructuredIntentInput) -> dict:
    """05 §2の5キー+location_name(design §6-1)。negative_constraintsは常に空配列。"""
    secondary = inp.category.secondary if inp.category else None
    location_name = inp.location.name if inp.location else None
    return {
        "category_secondary": secondary,
        "soft_constraints": _soft_constraints(inp),
        "negative_constraints": [],  # FR-42(常に空配列が正常系)
        "time_flexibility_minutes": None,  # MVP固定扱い(03 D-19)
        "location_flexibility": None,
        "location_name": location_name,
    }


def resolve_for_draft(inp: StructuredIntentInput) -> ResolvedColumns:
    """draft正規化(design §2.4表)。補完なし・意味検証なし(形式はPydantic済み)。"""
    participants = inp.participants
    return ResolvedColumns(
        category_primary=(
            inp.category.primary if inp.category and inp.category.primary else ""
        ),
        alcohol_involved=inp.alcohol_involved or False,
        structured_data=_structured_data(inp),
        budget_max=inp.budget.max if inp.budget else None,
        participants_min=(
            participants.min
            if participants and participants.min is not None
            else DEFAULT_PARTICIPANTS[0]
        ),
        participants_max=(
            participants.max
            if participants and participants.max is not None
            else DEFAULT_PARTICIPANTS[1]
        ),
        visibility=inp.visibility or DEFAULT_VISIBILITY,
        notification_level=inp.notification_level or DEFAULT_NOTIFICATION_LEVEL,
        time_start=inp.time.start if inp.time else None,
        time_end=inp.time.end if inp.time else None,
        expires_at=inp.expires_at,
        geo_radius_m=inp.location.radius_m if inp.location else None,
    )


def resolve_for_active(inp: StructuredIntentInput, *, now: datetime) -> ResolvedColumns:
    """active正規化。必須3検証済み前提(service)。補完を消費(design §2.3表)。"""
    if (
        inp.category is None
        or inp.category.primary is None
        or inp.time is None
        or inp.time.start is None
        or inp.location is None
        or inp.location.name is None
    ):
        raise ValueError("resolve_for_active requires validated input")
    primary = inp.category.primary
    time_start = inp.time.start
    cols = resolve_for_draft(inp)  # 共通部分はdraft正規化を再利用
    return replace(
        cols,
        category_primary=primary,
        alcohol_involved=(
            True if primary == "drinking" else (inp.alcohol_involved or False)
        ),
        time_end=inp.time.end or default_time_end(time_start),
        expires_at=inp.expires_at or nearest_expires_at(time_start, now),
        geo_radius_m=(
            inp.location.radius_m
            if inp.location.radius_m is not None
            else DEFAULT_RADIUS_M
        ),
    )


_COMPARE_FIELDS = (
    "raw_text",
    "category_primary",
    "alcohol_involved",
    "structured_data",
    "budget_max",
    "participants_min",
    "participants_max",
    "visibility",
    "notification_level",
    "time_start",
    "time_end",
    "expires_at",
    "geo_radius_m",
)


def differs_from_row(cols: ResolvedColumns, row: IntentRow) -> bool:
    """draft表現での実質変更判定(design §2.7)。geo_center(導出値)は比較外。"""
    for name in _COMPARE_FIELDS:
        if getattr(cols, name) != getattr(row, name):
            return True
    return False


def columns_from_row(row: IntentRow) -> ResolvedColumns:
    """保存行→ResolvedColumns(draft再保存でstructured_intent省略時の保持用)。"""
    return ResolvedColumns(
        raw_text=row.raw_text,
        category_primary=row.category_primary,
        alcohol_involved=row.alcohol_involved,
        structured_data=dict(row.structured_data or {}),
        budget_max=row.budget_max,
        participants_min=row.participants_min,
        participants_max=row.participants_max,
        visibility=row.visibility,
        notification_level=row.notification_level,
        time_start=row.time_start,
        time_end=row.time_end,
        expires_at=row.expires_at,
        geo_radius_m=row.geo_radius_m,
        version=row.version,
    )


def to_response_structured(row: IntentRow) -> dict:
    """保存行→応答structured_intent(リクエストと同形に再構成 — design §2.10)。

    soft_constraintsをdowngraded_from_ngでsoft/ng_unverifiableへ分割し、
    location.nameはstructured_data.location_nameから戻す(design §6-1)。
    """
    sd = row.structured_data or {}
    soft = sd.get("soft_constraints") or []
    return {
        "category": {
            "primary": row.category_primary or None,
            "secondary": sd.get("category_secondary"),
        },
        "alcohol_involved": row.alcohol_involved,
        "time": {
            "start": row.time_start,
            "end": row.time_end,
            "flexibility_minutes": None,
        },
        "location": {
            "name": sd.get("location_name"),
            "radius_m": row.geo_radius_m,
            "flexibility": None,
        },
        "budget": {"max": row.budget_max, "currency": "JPY"},
        "participants": {"min": row.participants_min, "max": row.participants_max},
        "visibility": row.visibility,
        "notification_level": row.notification_level,
        "expires_at": row.expires_at,
        "soft_constraints": [
            c["text"] for c in soft if not c.get("downgraded_from_ng")
        ],
        "ng_unverifiable": [c["text"] for c in soft if c.get("downgraded_from_ng")],
        "negative_constraints": [],
    }
