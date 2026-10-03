"""Layer 1 Hard Filter参照実装(M4 ws-2・06 §2確定表・design §2.2)。

layer1.pyのLAYER1_WHERE(SQL+PostGIS)の**Python独立実装**(二重実装の突合・
g2-plan §10方式の拡張)。シードYAMLの全ペア判定(無向・C(n,2))とEX-xxx
期待値の機械突合に使う。DB・embeddingに依存しない(06 §2はAI不使用)。

LAYER1_WHEREとの1対1対応(design §4-3・06 §2確定表):
- status='active'                              → シード全件active前提(判定対象外)
- embedding IS NOT NULL                        → stub付与前提(判定対象外・ws-1 §2.6)
- i.user_id <> :origin_user_id                 → self_pair
- i.category_primary = :origin_category        → category_mismatch
- 時間交差(end nullはstart+3h補完)           → time0
- ST_DWithin(r_origin+COALESCE(r_target,1000)) → distance(haversine近似)
- ペア予算LEAST(NULL無視)>=500円              → budgetmin
- 人数2∈[min,max]双方(1対1検索)             → people
- ブロック双方向                               → blocked(シードにblocksは作らない・D8)
- 飲酒は双方20歳以上                           → age_alcohol

fail_reason優先順位: self_pair > category_mismatch > time0 > distance >
people > budgetmin > blocked > age_alcohol(§2.0 D2)。
本判定は1対1検索基準(2∈[min,max]双方)。集合検索(group_candidates・起点
max≧3)は判定対象外(D9・ws-3側の試験)。判定条件は全て対称のため期待値は
無向ペア集合で定義する(design §2.2の対称性注記)。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone

from latch.testassets.vocab import LOCATIONS

# core.clock.JSTと同一値(testassetsを単一モジュール経路に保つため自前定義)
JST = timezone(timedelta(hours=9))

EARTH_RADIUS_M = 6_371_000.0
PAIR_BUDGET_MIN_YEN = 500  # 06 §2・layer1.py PAIR_BUDGET_MIN_YENと同一
END_NULL_COMPLETION = timedelta(hours=3)  # 03 D-19・LAYER1_WHEREの interval '3 hours'
DEFAULT_RADIUS_M = 1000  # LAYER1_WHEREの COALESCE(i.geo_radius_m, 1000)

FAIL_PRIORITY = (
    "self_pair",
    "category_mismatch",
    "time0",
    "distance",
    "people",
    "budgetmin",
    "blocked",
    "age_alcohol",
)


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """2地点(lat, lon)間の大円距離m(ST_DWithin geographyの球面近似)。"""
    lat1, lon1 = a
    lat2, lon2 = b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    h = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def completed_end(start: datetime, end: datetime | None) -> datetime:
    """end nullのstart+3h補完(COALESCE(i.time_end, i.time_start + 3h))。"""
    return start + END_NULL_COMPLETION if end is None else end


def age_years_at(birth: date, on: date) -> int:
    """満年齢(EXTRACT(YEAR FROM AGE(... AT TIME ZONE 'Asia/Tokyo'))と同一規則)。"""
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years


def _fail_reasons(
    a: dict,
    b: dict,
    *,
    users: dict[str, dict],
    now: datetime,
    blocks: frozenset[tuple[str, str]],
) -> list[str]:
    """該当する全fail条件(FAIL_PRIORITY順)。passなら空リスト。

    a/bはシードのintent内部表現(datetime実体・§2.3の_Slot形式)。
    """
    sa, sb = a["structured"], b["structured"]
    reasons: list[str] = []
    # 自己除外(LAYER1_WHERE: i.user_id <> :origin_user_id)。
    # 同一ユーザーペアは他条件と無関係に除外確定(早期return)
    if a["user"] == b["user"]:
        return ["self_pair"]
    # category完全一致(i.category_primary = :origin_category)
    if sa["category"]["primary"] != sb["category"]["primary"]:
        reasons.append("category_mismatch")
    # 時間交差(i.time_start < :origin_time_end AND :origin_time_start < COALESCE(...))
    a_end = completed_end(sa["time"]["start"], sa["time"]["end"])
    b_end = completed_end(sb["time"]["start"], sb["time"]["end"])
    if not (sa["time"]["start"] < b_end and sb["time"]["start"] < a_end):
        reasons.append("time0")
    # ST_DWithin(r_origin + COALESCE(r_target, 1000))。geographyの大円距離
    dist = haversine_m(
        LOCATIONS[sa["location"]["name"]], LOCATIONS[sb["location"]["name"]]
    )
    r_a = sa["location"]["radius_m"] or DEFAULT_RADIUS_M
    r_b = sb["location"]["radius_m"] or DEFAULT_RADIUS_M
    if dist > r_a + r_b:
        reasons.append("distance")
    # 人数2∈[min,max]双方(1対1検索・06 §2)
    pa, pb = sa["participants"], sb["participants"]
    if not (pa["min"] <= 2 <= pa["max"] and pb["min"] <= 2 <= pb["max"]):
        reasons.append("people")
    # ペア予算LEAST(NULL無視)>=500(NULL無視=双方制約なしはfailしない)
    budgets = [
        x["budget"]["max"]
        for x in (sa, sb)
        if x["budget"] is not None and x["budget"]["max"] is not None
    ]
    if budgets and min(budgets) < PAIR_BUDGET_MIN_YEN:
        reasons.append("budgetmin")
    # ブロック双方向(NOT EXISTS blocks ...)
    if tuple(sorted((a["user"], b["user"]))) in blocks:
        reasons.append("blocked")
    # 飲酒は双方20歳以上((NOT (a_alc OR b_alc)) OR (a_ge20 AND b_ge20))
    if sa.get("alcohol_involved") or sb.get("alcohol_involved"):
        jst_today = now.astimezone(JST).date()
        ge20_a = age_years_at(users[a["user"]]["birth_date"], jst_today) >= 20
        ge20_b = age_years_at(users[b["user"]]["birth_date"], jst_today) >= 20
        if not (ge20_a and ge20_b):
            reasons.append("age_alcohol")
    return reasons


def judge_pair(
    a: dict,
    b: dict,
    *,
    users: dict[str, dict],
    now: datetime,
    blocks: Iterable[tuple[str, str]] = (),
) -> tuple[bool, str | None]:
    """無向ペア1組のL1判定。戻り値=(pass, fail_reason|None)。"""
    # blocksは双方向照合のため(ユーザーidの辞書順小→大に)正規化してから
    # 集合化する。呼び出し側がどちら向きのタプルを渡しても同一視
    # (06 §2のNOT EXISTSのOR双方向と同一意味・§9 test_blocked_pair_symmetricが規定)
    canonical = frozenset(tuple(sorted(p)) for p in blocks)
    reasons = _fail_reasons(a, b, users=users, now=now, blocks=canonical)
    if not reasons:
        return True, None
    return False, reasons[0]  # 優先順位第一のみを記録(D2)


def judge_all(
    intents: list[dict],
    users: dict[str, dict],
    *,
    now: datetime,
    blocks: Iterable[tuple[str, str]] = (),
) -> dict[tuple[str, str], tuple[bool, str | None]]:
    """全無向ペアのL1判定。キー=(id_a, id_b)(idソート済み・n*(n-1)/2組)。"""
    result: dict[tuple[str, str], tuple[bool, str | None]] = {}
    ordered = sorted(intents, key=lambda i: i["id"])
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            result[(a["id"], b["id"])] = judge_pair(
                a, b, users=users, now=now, blocks=blocks
            )
    return result
