"""proposal生成(05 §2・08 §2.3・D-11・design §2.5)。

visibility生成分岐(引用#12)と各フィールドの生成規則。格納禁止
(raw_text・soft/NG条件の文言・座標)は構造上入らない。area_nameは
geo中点(集合は全メンバーgeo_center平均点)の逆転ジオコーディング結果を
呼び出し側(tx外)が渡す(承認済み解釈 — 純関数はDBを持たない)。
ws-7: build_group_proposal(集合版・3〜4人)を追加。headcount=|S|・
category_secondaryは「種Intentの値」規則を集合の種(members[0])へ適用。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from latch.core.clock import JST
from latch.worker.matching.group_calc import group_target_time
from latch.worker.matching.latch_calc import match_level, pair_target_time

NEARBY_HEADCOUNT = 2


def nearby_proposal() -> dict:
    """nearby存在通知用の最小構造(08 §2.2・design §2.7)。常に新規dict。"""
    return {"headcount": NEARBY_HEADCOUNT, "match_level": "low"}


@dataclass(frozen=True)
class LatchIntentInputs:
    """build_proposal入力の1Intent分(_SELECT_INTENT_INPUTS由来)。

    格納禁止フィールド(raw_text・soft/NG文言・座標文字列)を保持しない
    構造ピン(unit試験がdataclasses.fieldsで検証)。geo_lon/geo_latは
    area_name生成のための読取専用値(proposalへは格納しない)。
    """

    intent_id: uuid.UUID
    user_id: uuid.UUID
    visibility: str  # 'summary_only' | 'hidden_until_match'
    notification_level: str  # 'proposals_only' | 'nearby_also' | 'muted'
    time_start: datetime
    expires_at: datetime
    budget_max: int | None
    category_primary: str
    category_secondary: str | None  # structured_data.category.secondary
    geo_lon: float
    geo_lat: float


def build_proposal(
    *,
    origin: LatchIntentInputs,
    peer: LatchIntentInputs,
    score: float,
    area_name: str | None,
) -> dict:
    """proposal生成(05 §2・design §2.5の表)。origin=評価の種(起点Intent)。

    visibility分岐: 双方summary_only→全フィールド。いずれかが
    hidden_until_match→headcountとmatch_levelのみ(より厳しい方を優先)。
    """
    if origin.visibility != "summary_only" or peer.visibility != "summary_only":
        return {"headcount": 2, "match_level": match_level(score)}
    budgets = [b for b in (origin.budget_max, peer.budget_max) if b is not None]
    return {
        "time_summary": pair_target_time(origin.time_start, peer.time_start)
        .astimezone(JST)
        .strftime("%Y-%m-%d %H:%M"),
        "area_name": area_name,
        "headcount": 2,
        "category_primary": origin.category_primary,  # Layer 1完全一致で同一
        "category_secondary": origin.category_secondary,  # 起点側(引用#8)
        "budget": {"max": min(budgets)} if budgets else None,
        "match_level": match_level(score),
    }


def build_group_proposal(
    *,
    members: list[LatchIntentInputs],  # 種を先頭(design §2.7-3)
    score: float,
    area_name: str | None,
) -> dict:
    """集合版proposal生成(05 §2・引用#14・design §2.7-3)。

    visibility分岐は1対1と同一: 全員summary_onlyでなければheadcountと
    match_levelのみ。全フィールド側は time_summary=max(time_start)のJST
    書式(ws-6 §2.5と同一)・category_secondaryは種(members[0])の値・
    budget=参加budget_maxの最小値(NULLは無視)・headcount=|S|。
    """
    if any(m.visibility != "summary_only" for m in members):
        return {"headcount": len(members), "match_level": match_level(score)}
    budgets = [m.budget_max for m in members if m.budget_max is not None]
    seed = members[0]
    return {
        "time_summary": group_target_time(m.time_start for m in members)
        .astimezone(JST)
        .strftime("%Y-%m-%d %H:%M"),
        "area_name": area_name,
        "headcount": len(members),
        "category_primary": seed.category_primary,  # Layer 1完全一致で同一
        "category_secondary": seed.category_secondary,  # 種の値(引用#14)
        "budget": {"max": min(budgets)} if budgets else None,
        "match_level": match_level(score),
    }
