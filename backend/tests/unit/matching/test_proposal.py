"""proposal生成のunit試験(05 §2・08 §2.3・design §2.5)。"""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

from latch.worker.matching.proposal import (
    LatchIntentInputs,
    build_proposal,
    nearby_proposal,
)

T0 = datetime(2026, 10, 2, 11, 0, 0, tzinfo=UTC)  # JST 2026-10-02 20:00


def _inp(n: int = 1, **over) -> LatchIntentInputs:
    base = dict(
        intent_id=uuid.UUID(f"00000000-0000-4000-8000-{n:012d}"),
        user_id=uuid.UUID(f"00000000-0000-4000-8000-{n + 50:012d}"),
        visibility="summary_only",
        notification_level="proposals_only",
        time_start=T0,
        expires_at=T0 + timedelta(days=5),
        budget_max=5000,
        category_primary="drinking",
        category_secondary="ビール",
        geo_lon=130.558,
        geo_lat=31.596,
    )
    base.update(over)
    return LatchIntentInputs(**base)


def test_both_summary_only_builds_all_fields():
    p = build_proposal(
        origin=_inp(1, category_secondary="ビール"),
        peer=_inp(2, budget_max=4000, category_secondary="ワイン"),
        score=0.85,
        area_name="鹿児島市天文館",
    )
    assert p == {
        "time_summary": "2026-10-02 20:00",  # JST YYYY-MM-DD HH:MM(承認済み解釈)
        "area_name": "鹿児島市天文館",
        "headcount": 2,
        "category_primary": "drinking",
        "category_secondary": "ビール",  # 起点側(引用#8)
        "budget": {"max": 4000},  # min(5000, 4000)・NULL無視
        "match_level": "medium",  # 0.85
    }


def test_hidden_until_match_minimizes_fields():
    p = build_proposal(
        origin=_inp(1, visibility="hidden_until_match"),
        peer=_inp(2),
        score=0.95,
        area_name="x",
    )
    assert p == {"headcount": 2, "match_level": "high"}  # headcountとmatch_levelのみ


def test_hidden_on_either_side_minimizes():
    # 双方のvisibilityが異なる場合はより厳しい方(いずれかがhiddenならhidden扱い)
    p = build_proposal(
        origin=_inp(1),
        peer=_inp(2, visibility="hidden_until_match"),
        score=0.85,
        area_name="x",
    )
    assert p == {"headcount": 2, "match_level": "medium"}


def test_budget_null_handling():
    p = build_proposal(
        origin=_inp(1, budget_max=None),
        peer=_inp(2, budget_max=4000),
        score=0.85,
        area_name=None,
    )
    assert p["budget"] == {"max": 4000}  # 片方NULLは無視
    p2 = build_proposal(
        origin=_inp(1, budget_max=None),
        peer=_inp(2, budget_max=None),
        score=0.85,
        area_name=None,
    )
    assert p2["budget"] is None  # 両方NULLならnull


def test_area_name_none_is_allowed():
    p = build_proposal(origin=_inp(1), peer=_inp(2), score=0.85, area_name=None)
    assert p["area_name"] is None  # 地物なし(M3表示側フォールバック対象)


def test_time_summary_uses_max_time_start():
    p = build_proposal(
        origin=_inp(1, time_start=T0),
        peer=_inp(2, time_start=T0 + timedelta(minutes=90)),
        score=0.85,
        area_name=None,
    )
    assert p["time_summary"] == "2026-10-02 21:30"  # max(time_start)のJST


def test_nearby_proposal_is_minimal_and_fresh():
    assert nearby_proposal() == {"headcount": 2, "match_level": "low"}
    d = nearby_proposal()
    d["match_level"] = "changed"
    assert nearby_proposal() == {"headcount": 2, "match_level": "low"}  # 新規dict


def test_latch_intent_inputs_holds_no_forbidden_fields():
    names = {f.name for f in dataclasses.fields(LatchIntentInputs)}
    assert names == {
        "intent_id",
        "user_id",
        "visibility",
        "notification_level",
        "time_start",
        "expires_at",
        "budget_max",
        "category_primary",
        "category_secondary",
        "geo_lon",
        "geo_lat",
    }
    # 格納禁止の生データを保持しない構造ピン
    assert not names & {"raw_text", "soft_constraints", "structured_data"}
