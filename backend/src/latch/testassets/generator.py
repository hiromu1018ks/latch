"""決定的シード生成器(M4 ws-2・design §2.3・10 §2)。

固定シード(42)のテンプレート直交生成。--base-datetime(JST)を基準に対象
時刻をD+1〜D+5へ割り付け、同一引数での再生成はバイト同一(design §2.3)。
--count変更時も決定的(ws-6の拡張生成1万〜10万が同一コード経路・§3.3)。

配分(10 §2完全一致・300件時):
- 時間帯: 昼60/夕90/夜120/その他30(開始時刻・design §2.4の解釈)
- カテゴリ: drinking 150/meal 120/activity 30
- 人数志向: 1対1 210/グループ(min≧3)90

構成: 意図的配置(EX素材64 intents・SD-001〜064)を先に組み立て、残余を
比率配分(最大剰余法)+乱数シャッフルで割り当てる(D10・意図的配置は乱数
不使用)。全ペアL1判定はreference_l1(独立実装)で計算し、YAMLには
EX-xxx個別期待+aggregate集計のみを書き出す(design §2.2の2層)。
"""

from __future__ import annotations

import argparse
import copy
import math
import random
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from latch.testassets.reference_l1 import (
    FAIL_PRIORITY,
    JST,
    haversine_m,
    judge_all,
    judge_pair,
)
from latch.testassets.vocab import (
    CATEGORY_SECONDARY,
    LOCATIONS,
    RAW_TEMPLATES,
    SOFT_CONSTRAINTS,
    TIME_PREFIX,
    time_bucket_of,
)

SEED = 42
SEED_CREATED = "2026-10-03"
USER_COUNT = 100
DEFAULT_COUNT = 300
EX_SLOT_COUNT = 64
COUNT_MIN = EX_SLOT_COUNT + 1  # 意図的配置64件+残余1件未満は生成不可
DEFAULT_OUT = "../docs/testassets/m4-seed.yaml"

# 分布目標(10 §2・300件時の実現値)。残余には消費差し引き後の比率を適用する
TIME_TARGETS = {"day": 60, "evening": 90, "night": 120, "other": 30}
CATEGORY_TARGETS = {"drinking": 150, "meal": 120, "activity": 30}
ORIENTATION_TARGETS = {"one_to_one": 210, "group": 90}
CATEGORY_ORDER = ["drinking", "meal", "activity"]
BUCKET_ORDER = ["day", "evening", "night", "other"]

# 残余スロットの対象日割付(D+1〜D+5をラウンドロビン・design §2.3)
DAY_OFFSETS = [1, 2, 3, 4, 5]

# 残余スロットの値域(実装契約・参照値13)
BUDGET_CHOICES = [None, None, 1500, 2000, 2500, 3000, 3500, 5000]  # null率25%
RADIUS_CHOICES = [None, None, None, 1000, 2000, 3000]  # null率60%(補完カバレッジ)
END_NULL_RATIO = 0.1  # end null(start+3h補完)のカバレッジ
MEAL_ALCOHOL_RATIO = 0.15  # meal×alcohol true(20歳以上のユーザーのみ)
VISIBILITY_CHOICES = ["hidden_until_match", "summary_only"]
NOTIFICATION_CHOICES = ["proposals_only", "nearby_also", "muted"]
OTHER_BUCKET_HOURS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 14, 15, 16, 17, 23]
# D+1(初日)の「その他」は0.9日境界(base=11時基準)を満たす時間帯に制限する
# (design §2.3の対象時刻D+1〜D+5 = verify/unitの 0.9 < delta < 6.0 日)。
# day/evening/nightは12時以降のため無関係
OTHER_BUCKET_HOURS_DAY1 = [9, 10, 11, 14, 15, 16, 17, 23]
NAME_POOL = [
    "さくら",
    "はると",
    "ゆい",
    "そうた",
    "あおい",
    "かえで",
    "りく",
    "ひな",
    "ゆうと",
    "みお",
]

# 18-19歳8人の生年月日基準(D6・base年月日から年月を引いた値)。
# (ユーザー, 年, 月) — base+7日時点でも18-19歳を保つ3ヶ月以上のマージン
UNDERAGE_BIRTHS = [
    ("SU-001", 18, 6),  # EX-004素材(18歳)
    ("SU-002", 19, 6),  # EX-005素材(19歳)
    ("SU-003", 18, 4),  # EX-029 reject素材
    ("SU-004", 18, 7),  # EX-030
    ("SU-005", 18, 10),  # EX-031
    ("SU-006", 19, 2),  # EX-032
    ("SU-007", 19, 8),  # EX-033
    ("SU-008", 19, 9),  # 通常ユーザー(残余スロット・全intentでalcohol false)
]

# reject素材(EX-029〜033・D7)。作成試行のみでシードintents配列には含めない
REJECT_CASES: list[dict] = [
    {
        "id": "EX-029",
        "user": "SU-003",
        "raw_text": "今夜ワインを飲みたい。落ち着いた雰囲気で",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "ワイン"},
            "alcohol_involved": True,
            "time": {"start": "D+1T20:00"},
            "location": {"name": "天文館"},
        },
        "expected_status": 422,
        "expected_code": "UNDER_AGE",
        "basis": "D-10(10 §3 #27)・05 §5(年齢検証)",
    },
    {
        "id": "EX-030",
        "user": "SU-004",
        "raw_text": "居酒屋でしっぽりと飲みたい",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "居酒屋"},
            "alcohol_involved": True,
            "time": {"start": "D+2T20:00"},
            "location": {"name": "呉服町"},
        },
        "expected_status": 422,
        "expected_code": "UNDER_AGE",
        "basis": "同上",
    },
    {
        "id": "EX-031",
        "user": "SU-005",
        "raw_text": "ハイボール片手に1杯だけ。ちょい飲みして帰りたい",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "ハイボール"},
            "alcohol_involved": True,
            "time": {"start": "D+3T18:00"},
            "location": {"name": "高見馬場"},
        },
        "expected_status": 422,
        "expected_code": "UNDER_AGE",
        "basis": "同上",
    },
    {
        "id": "EX-032",
        "user": "SU-006",
        "raw_text": "焼肉のあと日本酒を飲みたい。まったり語りたい",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "日本酒"},
            "alcohol_involved": True,
            "time": {"start": "D+4T20:00"},
            "location": {"name": "加治屋町"},
        },
        "expected_status": 422,
        "expected_code": "UNDER_AGE",
        "basis": "同上",
    },
    {
        "id": "EX-033",
        "user": "SU-007",
        "raw_text": "おしゃれなバルでバル巡り。まずは1杯だけ様子を見たい",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "おしゃれなバル"},
            "alcohol_involved": True,
            "time": {"start": "D+5T19:00"},
            "location": {"name": "鹿児島駅前"},
        },
        "expected_status": 422,
        "expected_code": "UNDER_AGE",
        "basis": "同上",
    },
]

# EXケース期待値(EX-001〜028・D11)。kind=pairはreference_l1と機械突合、
# kind=group_clusterはverifyが構成属性を機械検査する
EX_CASES: list[dict] = [
    {
        "id": "EX-001",
        "kind": "pair",
        "intents": ["SD-001", "SD-002"],
        "expected": "fail",
        "fail_reason": "self_pair",
        "basis": "10 §3(FR-17自己ペア)・06 §2(自己除外)",
    },
    {
        "id": "EX-002",
        "kind": "pair",
        "intents": ["SD-003", "SD-004"],
        "expected": "fail",
        "fail_reason": "self_pair",
        "basis": "同上",
    },
    {
        "id": "EX-003",
        "kind": "pair",
        "intents": ["SD-005", "SD-006"],
        "expected": "fail",
        "fail_reason": "self_pair",
        "basis": "同上",
    },
    {
        "id": "EX-004",
        "kind": "pair",
        "intents": ["SD-007", "SD-008"],
        "expected": "fail",
        "fail_reason": "age_alcohol",
        "basis": "D-10(10 §3 #27)・06 §2(飲酒は双方20歳以上)",
    },
    {
        "id": "EX-005",
        "kind": "pair",
        "intents": ["SD-009", "SD-010"],
        "expected": "fail",
        "fail_reason": "age_alcohol",
        "basis": "同上",
    },
    {
        "id": "EX-006",
        "kind": "pair",
        "intents": ["SD-011", "SD-012"],
        "expected": "pass",
        "fail_reason": None,
        "basis": "06 §2(end nullはstart+3h補完で交差成立)",
    },
    {
        "id": "EX-007",
        "kind": "pair",
        "intents": ["SD-013", "SD-014"],
        "expected": "fail",
        "fail_reason": "time0",
        "basis": "06 §2(時間交差・境界隣接は交差0)",
    },
    {
        "id": "EX-008",
        "kind": "pair",
        "intents": ["SD-015", "SD-016"],
        "expected": "pass",
        "fail_reason": None,
        "basis": "06 §2(交差1分は境界以上で通る)",
    },
    {
        "id": "EX-009",
        "kind": "pair",
        "intents": ["SD-017", "SD-018"],
        "expected": "fail",
        "fail_reason": "time0",
        "basis": "06 §2(完全非交差)",
    },
    {
        "id": "EX-010",
        "kind": "pair",
        "intents": ["SD-019", "SD-020"],
        "expected": "pass",
        "fail_reason": None,
        "basis": "06 §2(ST_DWithin・半径和>=実距離[切り上げ])",
    },
    {
        "id": "EX-011",
        "kind": "pair",
        "intents": ["SD-021", "SD-022"],
        "expected": "fail",
        "fail_reason": "distance",
        "basis": "06 §2(半径和<実距離[切り下げ])",
    },
    {
        "id": "EX-012",
        "kind": "pair",
        "intents": ["SD-023", "SD-024"],
        "expected": "fail",
        "fail_reason": "distance",
        "basis": "06 §2(radius null補完1000+1000<実距離約2530m)",
    },
    {
        "id": "EX-013",
        "kind": "pair",
        "intents": ["SD-025", "SD-026"],
        "expected": "pass",
        "fail_reason": None,
        "basis": "06 §2(radius null補完1000+1000>=実距離約1590m)",
    },
    {
        "id": "EX-014",
        "kind": "pair",
        "intents": ["SD-027", "SD-028"],
        "expected": "fail",
        "fail_reason": "budgetmin",
        "basis": "06 §2(LEAST=400<500)",
    },
    {
        "id": "EX-015",
        "kind": "pair",
        "intents": ["SD-029", "SD-030"],
        "expected": "pass",
        "fail_reason": None,
        "basis": "06 §2(500ちょうどは通る)",
    },
    {
        "id": "EX-016",
        "kind": "pair",
        "intents": ["SD-031", "SD-032"],
        "expected": "fail",
        "fail_reason": "budgetmin",
        "basis": "06 §2(NULL無視=実効499)",
    },
    {
        "id": "EX-017",
        "kind": "pair",
        "intents": ["SD-033", "SD-034"],
        "expected": "pass",
        "fail_reason": None,
        "basis": "06 §2(双方NULL=予算制約なし)",
    },
    {
        "id": "EX-018",
        "kind": "pair",
        "intents": ["SD-035", "SD-036"],
        "expected": "fail",
        "fail_reason": "people",
        "basis": "06 §2(2∉[3,4])",
    },
    {
        "id": "EX-019",
        "kind": "pair",
        "intents": ["SD-037", "SD-038"],
        "expected": "fail",
        "fail_reason": "people",
        "basis": "06 §2(2∉[1,1])",
    },
    {
        "id": "EX-020",
        "kind": "pair",
        "intents": ["SD-039", "SD-040"],
        "expected": "pass",
        "fail_reason": None,
        "basis": "06 §2(2∈[2,4])",
    },
    {
        "id": "EX-021",
        "kind": "pair",
        "intents": ["SD-041", "SD-043"],
        "expected": "pass",
        "fail_reason": None,
        "basis": "10 §3(競合クローズ素材・同一ユーザーの2 Intentが同一相手と双方候補)",
    },
    {
        "id": "EX-022",
        "kind": "pair",
        "intents": ["SD-042", "SD-043"],
        "expected": "pass",
        "fail_reason": None,
        "basis": "同上(SD-041×SD-042は同一ユーザーでself_pair)",
    },
    {
        "id": "EX-023",
        "kind": "pair",
        "intents": ["SD-044", "SD-045"],
        "expected": "fail",
        "fail_reason": "category_mismatch",
        "basis": "06 §2(category完全一致)",
    },
    {
        "id": "EX-024",
        "kind": "pair",
        "intents": ["SD-046", "SD-047"],
        "expected": "fail",
        "fail_reason": "category_mismatch",
        "basis": "同上",
    },
    {
        "id": "EX-025",
        "kind": "group_cluster",
        "intents": ["SD-048", "SD-049", "SD-050", "SD-051"],
        "expected": {
            "distinct_users": 3,
            "all_min_ge_3": True,
            "note": "同一ユーザー(SU-040)の2 Intentを含む構成要件 — group_candidatesに"
            "生成されないこと(FR-17自己重複集合)。集合生成自体はws-3で確認",
        },
        "basis": "10 §3(FR-17)・design §2.2",
    },
    {
        "id": "EX-026",
        "kind": "group_cluster",
        "intents": ["SD-052", "SD-053", "SD-054", "SD-055", "SD-056", "SD-057"],
        "expected": {
            "distinct_users": 6,
            "all_min_ge_3": True,
            "same_bucket": "night",
            "same_place": "天文館",
            "same_category": "drinking",
            "note": "同Bucket・同地域・同カテゴリのmin≧3×6件 — 複数集合が立つ通知順序"
            "素材(D-06)をws-3で消費",
        },
        "basis": "10 §3(通知順序)・design §2.2",
    },
    {
        "id": "EX-027",
        "kind": "group_cluster",
        "intents": ["SD-058", "SD-059", "SD-060", "SD-061"],
        "expected": {
            "distinct_users": 4,
            "all_min_ge_3": True,
            "all_pairs_pass_l1": True,
            "note": "部分YESクラスタ(3〜4人集合素材) — 一部のみyes回答で"
            "response_deadline後へ(10 §3)をws-3で消費",
        },
        "basis": "10 §3(部分YES)・design §2.2",
    },
    {
        "id": "EX-028",
        "kind": "group_cluster",
        "intents": ["SD-062", "SD-063", "SD-064"],
        "expected": {
            "distinct_users": 3,
            "all_min_ge_3": True,
            "all_pairs_pass_l1": True,
            "note": "同上(3人集合)",
        },
        "basis": "同上",
    },
]


def _at(base: datetime, day_offset: int, hour: int, minute: int = 0) -> datetime:
    """baseの日(JST)からday_offset日後のhour:minute(JST)を返す(design §2.3)。"""
    d = base.astimezone(JST) + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=JST)


def _minus_ym(d: date, years: int, months: int) -> date:
    """日付から年月を引く(月末クランプ・うるう年対応)。"""
    total = d.year * 12 + d.month - 1 - years * 12 - months
    y, m0 = divmod(total, 12)
    m = m0 + 1
    if m == 12:
        next_first = date(y + 1, 1, 1)
    else:
        next_first = date(y, m + 1, 1)
    return date(y, m, min(d.day, (next_first - timedelta(days=1)).day))


def _boundary_radii() -> tuple[int, int, int, int]:
    """EX-010/011の半径境界値(haversine実距離から決定的に導出)。

    天文館〜高見馬場の実距離は約389m: hi側=合計ceil(d)(pass)・
    lo側=合計floor(d)-1(fail)。verifyが同一計算で再現する。
    """
    d = haversine_m(LOCATIONS["天文館"], LOCATIONS["高見馬場"])
    hi_a, hi_b = math.ceil(d) - 1, 1  # 合計=ceil(d) >= d → pass
    lo_a, lo_b = math.floor(d) - 1, 1  # 合計=floor(d)-1 < d → fail
    return hi_a, hi_b, lo_a, lo_b


# 意図的配置テーブル(EX素材64 intents・乱数不使用・D10)。
# 行: (day, hh, mm, dur_min|None(end null), 地名, カテゴリ, secondary,
#      alcohol, radius|int|"HI_A"|"HI_B"|"LO_A"|"LO_B", budget|None, pmin, pmax)
# radius文字列は_boundary_radii()の実行時計算値へ置換する。
# 地名はsupervisor裁定2026-10-03のci-db代表点7地名(vocab.LOCATIONS)。
# 同列の並行リスト: _EX_USERS(ユーザー)・_EX_GROUPS(ex_group表記)
_EX_ROWS: list[tuple] = [
    # -- EX-001..003 自己ペア3組(同一ユーザーの2 intents・10 §3 FR-17)
    (
        1,
        20,
        0,
        180,
        "天文館",
        "drinking",
        "居酒屋",
        True,
        1000,
        3500,
        2,
        2,
    ),  # SD-001 SU-010
    (
        1,
        20,
        30,
        180,
        "天文館",
        "drinking",
        "居酒屋",
        True,
        1000,
        3500,
        2,
        2,
    ),  # SD-002 SU-010
    (
        1,
        18,
        0,
        120,
        "呉服町",
        "meal",
        "カフェ",
        False,
        1000,
        2000,
        2,
        2,
    ),  # SD-003 SU-011
    (
        1,
        18,
        30,
        120,
        "呉服町",
        "meal",
        "カフェ",
        False,
        1000,
        2000,
        2,
        2,
    ),  # SD-004 SU-011
    (
        1,
        12,
        0,
        120,
        "高見馬場",
        "activity",
        "街歩き",
        False,
        1000,
        2000,
        2,
        2,
    ),  # SD-005 SU-012
    (
        1,
        12,
        30,
        120,
        "高見馬場",
        "activity",
        "街歩き",
        False,
        1000,
        2000,
        2,
        2,
    ),  # SD-006 SU-012
    # -- EX-004/005 D-10ペア(18-19歳×drinking false vs 20歳以上×true・D3)
    (
        1,
        20,
        0,
        180,
        "天文館",
        "drinking",
        "居酒屋",
        True,
        1000,
        3500,
        2,
        2,
    ),  # SD-007 SU-021
    (
        1,
        20,
        30,
        180,
        "天文館",
        "drinking",
        "ハイボール",
        False,
        1000,
        3500,
        2,
        2,
    ),  # SD-008 SU-001(18歳)
    (
        2,
        20,
        0,
        180,
        "加治屋町",
        "drinking",
        "バー",
        True,
        1000,
        3000,
        2,
        2,
    ),  # SD-009 SU-022
    (
        2,
        20,
        30,
        180,
        "加治屋町",
        "drinking",
        "ビール",
        False,
        1000,
        3000,
        2,
        2,
    ),  # SD-010 SU-002(19歳)
    # -- EX-006..009 時間交差境界
    (1, 12, 0, 120, "呉服町", "meal", "定食", False, 1000, 1500, 2, 2),  # SD-011 SU-023
    (
        1,
        13,
        0,
        None,
        "呉服町",
        "meal",
        "定食",
        False,
        1000,
        1500,
        2,
        2,
    ),  # SD-012 SU-024(end null→補完16:00で交差pass)
    (
        2,
        18,
        0,
        90,
        "天文館",
        "meal",
        "とんこつラーメン",
        False,
        1000,
        2000,
        2,
        2,
    ),  # SD-013 SU-025
    (
        2,
        19,
        30,
        90,
        "天文館",
        "meal",
        "とんこつラーメン",
        False,
        1000,
        2000,
        2,
        2,
    ),  # SD-014 SU-026(境界隣接→time0)
    (
        2,
        20,
        0,
        61,
        "天文館",
        "drinking",
        "ビール",
        True,
        1000,
        2500,
        2,
        2,
    ),  # SD-015 SU-027
    (
        2,
        21,
        0,
        90,
        "天文館",
        "drinking",
        "ビール",
        True,
        1000,
        2500,
        2,
        2,
    ),  # SD-016 SU-028(交差1分→pass)
    (
        3,
        10,
        0,
        60,
        "浜町",
        "activity",
        "読書",
        False,
        1000,
        1500,
        2,
        2,
    ),  # SD-017 SU-029
    (
        3,
        15,
        0,
        60,
        "浜町",
        "activity",
        "読書",
        False,
        1000,
        1500,
        2,
        2,
    ),  # SD-018 SU-030(完全非交差→time0)
    # -- EX-010..013 距離境界
    (
        3,
        18,
        0,
        120,
        "天文館",
        "activity",
        "観光",
        False,
        "HI_A",
        2000,
        2,
        2,
    ),  # SD-019 SU-031
    (
        3,
        18,
        30,
        120,
        "高見馬場",
        "activity",
        "観光",
        False,
        "HI_B",
        2000,
        2,
        2,
    ),  # SD-020 SU-032(半径和切り上げ→pass)
    (
        4,
        18,
        0,
        120,
        "天文館",
        "activity",
        "観光",
        False,
        "LO_A",
        2000,
        2,
        2,
    ),  # SD-021 SU-033
    (
        4,
        18,
        30,
        120,
        "高見馬場",
        "activity",
        "観光",
        False,
        "LO_B",
        2000,
        2,
        2,
    ),  # SD-022 SU-034(切り下げ→distance)
    (
        3,
        10,
        0,
        120,
        "加治屋町",
        "drinking",
        "バル",
        True,
        None,
        2500,
        2,
        2,
    ),  # SD-023 SU-035(radius null)
    (
        3,
        10,
        30,
        120,
        "浜町",
        "drinking",
        "バル",
        True,
        None,
        2500,
        2,
        2,
    ),  # SD-024 SU-036(null補完1000+1000<約2530m→distance)
    (
        4,
        10,
        0,
        120,
        "天文館",
        "activity",
        "水族館",
        False,
        None,
        2000,
        2,
        2,
    ),  # SD-025 SU-037(radius null)
    (
        4,
        10,
        30,
        120,
        "城山",
        "activity",
        "水族館",
        False,
        None,
        2000,
        2,
        2,
    ),  # SD-026 SU-038(補完2000>=約1590m→pass)
    # -- EX-014..017 予算境界
    (
        4,
        12,
        0,
        120,
        "加治屋町",
        "meal",
        "定食",
        False,
        1000,
        400,
        2,
        2,
    ),  # SD-027 SU-039
    (
        4,
        12,
        30,
        120,
        "加治屋町",
        "meal",
        "定食",
        False,
        1000,
        400,
        2,
        2,
    ),  # SD-028 SU-040(LEAST=400→budgetmin)
    (
        5,
        12,
        0,
        120,
        "加治屋町",
        "meal",
        "定食",
        False,
        1000,
        500,
        2,
        2,
    ),  # SD-029 SU-041
    (
        5,
        12,
        30,
        120,
        "加治屋町",
        "meal",
        "定食",
        False,
        1000,
        500,
        2,
        2,
    ),  # SD-030 SU-042(500ちょうど→pass)
    (
        5,
        10,
        0,
        120,
        "城山",
        "drinking",
        "日本酒",
        True,
        1000,
        None,
        2,
        2,
    ),  # SD-031 SU-043(budget null)
    (
        5,
        10,
        30,
        120,
        "城山",
        "drinking",
        "日本酒",
        True,
        1000,
        499,
        2,
        2,
    ),  # SD-032 SU-044(実効499→budgetmin)
    (
        5,
        10,
        0,
        120,
        "浜町",
        "activity",
        "ハイキング",
        False,
        1000,
        None,
        2,
        2,
    ),  # SD-033 SU-045
    (
        5,
        10,
        30,
        120,
        "浜町",
        "activity",
        "ハイキング",
        False,
        1000,
        None,
        2,
        2,
    ),  # SD-034 SU-046(双方null→pass)
    # -- EX-018..020 人数境界(1対1検索の2∈[min,max])
    (
        5,
        20,
        0,
        180,
        "天文館",
        "drinking",
        "立ち飲み",
        True,
        1000,
        3000,
        2,
        2,
    ),  # SD-035 SU-047
    (
        5,
        20,
        30,
        180,
        "天文館",
        "drinking",
        "立ち飲み",
        True,
        1000,
        3000,
        3,
        4,
    ),  # SD-036 SU-048(3/4→people)
    (
        5,
        21,
        0,
        120,
        "呉服町",
        "meal",
        "寿司",
        False,
        1000,
        4000,
        1,
        1,
    ),  # SD-037 SU-049(1/1)
    (
        5,
        21,
        30,
        120,
        "呉服町",
        "meal",
        "寿司",
        False,
        1000,
        4000,
        2,
        2,
    ),  # SD-038 SU-050(2∉[1,1]→people)
    (
        5,
        22,
        0,
        120,
        "呉服町",
        "activity",
        "カラオケ",
        False,
        1000,
        2500,
        2,
        2,
    ),  # SD-039 SU-051
    (
        5,
        22,
        30,
        120,
        "呉服町",
        "activity",
        "カラオケ",
        False,
        1000,
        2500,
        2,
        4,
    ),  # SD-040 SU-052(2∈[2,4]→pass)
    # -- EX-021/022 競合クローズ素材(SU-053の2 intents×SU-054。SD-041×SD-042はself_pair)
    (
        1,
        21,
        0,
        150,
        "天文館",
        "drinking",
        "ハイボール",
        True,
        1000,
        3000,
        2,
        2,
    ),  # SD-041 SU-053
    (
        1,
        22,
        30,
        150,
        "天文館",
        "drinking",
        "ハイボール",
        True,
        1000,
        3000,
        2,
        2,
    ),  # SD-042 SU-053(22:30-翌1:00・日跨ぎ)
    (
        1,
        21,
        30,
        150,
        "天文館",
        "drinking",
        "ハイボール",
        True,
        1000,
        3000,
        2,
        2,
    ),  # SD-043 SU-054
    # -- EX-023/024 カテゴリ不一致(同時間・同地域でcategory違い)
    (
        2,
        18,
        0,
        120,
        "呉服町",
        "drinking",
        "ワイン",
        True,
        1000,
        3000,
        2,
        2,
    ),  # SD-044 SU-055
    (
        2,
        18,
        30,
        120,
        "呉服町",
        "meal",
        "焼肉",
        False,
        1000,
        3000,
        2,
        2,
    ),  # SD-045 SU-056
    (
        3,
        12,
        0,
        120,
        "加治屋町",
        "activity",
        "映画",
        False,
        1000,
        2000,
        2,
        2,
    ),  # SD-046 SU-057
    (
        3,
        12,
        30,
        120,
        "加治屋町",
        "drinking",
        "焼酎",
        True,
        1000,
        2000,
        2,
        2,
    ),  # SD-047 SU-058
    # -- EX-025 自己重複集合クラスタ(SU-059の2 intentsを含む4 intents・min≧3)
    (
        1,
        20,
        0,
        180,
        "天文館",
        "meal",
        "しゃぶしゃぶ",
        False,
        1000,
        4000,
        3,
        4,
    ),  # SD-048 SU-059
    (
        1,
        20,
        30,
        180,
        "天文館",
        "meal",
        "しゃぶしゃぶ",
        False,
        1000,
        4000,
        3,
        4,
    ),  # SD-049 SU-059(同ユーザー)
    (
        1,
        21,
        0,
        180,
        "天文館",
        "meal",
        "しゃぶしゃぶ",
        False,
        1000,
        4000,
        3,
        4,
    ),  # SD-050 SU-060
    (
        1,
        21,
        30,
        180,
        "天文館",
        "meal",
        "しゃぶしゃぶ",
        False,
        1000,
        4000,
        3,
        4,
    ),  # SD-051 SU-061
    # -- EX-026 通知順序クラスタ(同Bucket・同地域・同カテゴリのmin≧3×6件)
    (
        2,
        20,
        30,
        180,
        "天文館",
        "drinking",
        "居酒屋",
        True,
        1000,
        3500,
        3,
        4,
    ),  # SD-052 SU-062
    (
        2,
        20,
        30,
        180,
        "天文館",
        "drinking",
        "居酒屋",
        True,
        1000,
        3500,
        3,
        4,
    ),  # SD-053 SU-063
    (
        2,
        20,
        30,
        180,
        "天文館",
        "drinking",
        "居酒屋",
        True,
        1000,
        3500,
        3,
        4,
    ),  # SD-054 SU-064
    (
        2,
        20,
        30,
        180,
        "天文館",
        "drinking",
        "居酒屋",
        True,
        1000,
        3500,
        3,
        4,
    ),  # SD-055 SU-065
    (
        2,
        20,
        30,
        180,
        "天文館",
        "drinking",
        "居酒屋",
        True,
        1000,
        3500,
        3,
        4,
    ),  # SD-056 SU-066
    (
        2,
        20,
        30,
        180,
        "天文館",
        "drinking",
        "居酒屋",
        True,
        1000,
        3500,
        3,
        4,
    ),  # SD-057 SU-067
    # -- EX-027 部分YESクラスタ(4人集合・相互交差で全ペアL1 pass)
    (
        3,
        18,
        0,
        180,
        "呉服町",
        "meal",
        "海鮮丼",
        False,
        1000,
        4000,
        3,
        4,
    ),  # SD-058 SU-068
    (
        3,
        18,
        30,
        180,
        "呉服町",
        "meal",
        "海鮮丼",
        False,
        1000,
        4000,
        3,
        4,
    ),  # SD-059 SU-069
    (
        3,
        19,
        0,
        180,
        "呉服町",
        "meal",
        "海鮮丼",
        False,
        1000,
        4000,
        3,
        4,
    ),  # SD-060 SU-070
    (
        3,
        19,
        30,
        180,
        "呉服町",
        "meal",
        "海鮮丼",
        False,
        1000,
        4000,
        3,
        4,
    ),  # SD-061 SU-071
    # -- EX-028 部分YESクラスタ(3人集合)
    (
        4,
        21,
        0,
        150,
        "加治屋町",
        "activity",
        "カラオケ",
        False,
        1000,
        3000,
        3,
        4,
    ),  # SD-062 SU-072
    (
        4,
        21,
        30,
        150,
        "加治屋町",
        "activity",
        "カラオケ",
        False,
        1000,
        3000,
        3,
        4,
    ),  # SD-063 SU-073
    (
        4,
        22,
        0,
        150,
        "加治屋町",
        "activity",
        "カラオケ",
        False,
        1000,
        3000,
        3,
        4,
    ),  # SD-064 SU-074
]

# _EX_ROWSと同列の並行リスト(ユーザー・ex_group表記)。要素数=64
_EX_USERS: list[str] = [
    "SU-010",
    "SU-010",
    "SU-011",
    "SU-011",
    "SU-012",
    "SU-012",
    "SU-021",
    "SU-001",
    "SU-022",
    "SU-002",
    "SU-023",
    "SU-024",
    "SU-025",
    "SU-026",
    "SU-027",
    "SU-028",
    "SU-029",
    "SU-030",
    "SU-031",
    "SU-032",
    "SU-033",
    "SU-034",
    "SU-035",
    "SU-036",
    "SU-037",
    "SU-038",
    "SU-039",
    "SU-040",
    "SU-041",
    "SU-042",
    "SU-043",
    "SU-044",
    "SU-045",
    "SU-046",
    "SU-047",
    "SU-048",
    "SU-049",
    "SU-050",
    "SU-051",
    "SU-052",
    "SU-053",
    "SU-053",
    "SU-054",
    "SU-055",
    "SU-056",
    "SU-057",
    "SU-058",
    "SU-059",
    "SU-059",
    "SU-060",
    "SU-061",
    "SU-062",
    "SU-063",
    "SU-064",
    "SU-065",
    "SU-066",
    "SU-067",
    "SU-068",
    "SU-069",
    "SU-070",
    "SU-071",
    "SU-072",
    "SU-073",
    "SU-074",
]
_EX_GROUPS: list[str] = [
    "EX-001",
    "EX-001",
    "EX-002",
    "EX-002",
    "EX-003",
    "EX-003",
    "EX-004",
    "EX-004",
    "EX-005",
    "EX-005",
    "EX-006",
    "EX-006",
    "EX-007",
    "EX-007",
    "EX-008",
    "EX-008",
    "EX-009",
    "EX-009",
    "EX-010",
    "EX-010",
    "EX-011",
    "EX-011",
    "EX-012",
    "EX-012",
    "EX-013",
    "EX-013",
    "EX-014",
    "EX-014",
    "EX-015",
    "EX-015",
    "EX-016",
    "EX-016",
    "EX-017",
    "EX-017",
    "EX-018",
    "EX-018",
    "EX-019",
    "EX-019",
    "EX-020",
    "EX-020",
    "EX-021",
    "EX-022",
    "EX-021,EX-022",
    "EX-023",
    "EX-023",
    "EX-024",
    "EX-024",
    "EX-025",
    "EX-025",
    "EX-025",
    "EX-025",
    "EX-026",
    "EX-026",
    "EX-026",
    "EX-026",
    "EX-026",
    "EX-026",
    "EX-027",
    "EX-027",
    "EX-027",
    "EX-027",
    "EX-028",
    "EX-028",
    "EX-028",
]


def _intentional_slots(base: datetime) -> list[dict]:
    """意図的配置intents(SD-001〜064)を組み立てる(乱数不使用・D10)。

    消費集計(残余割付が差し引く前提値・unit試験の期待値):
    時間帯=昼10/夕14/夜30/その他10・カテゴリ=drinking 25/meal 21/
    activity 18・志向=1対1 46/グループ 18。
    """
    if not (len(_EX_ROWS) == len(_EX_USERS) == len(_EX_GROUPS) == EX_SLOT_COUNT):
        raise AssertionError("EX素材テーブルの要素数不一致")
    radii = _boundary_radii()
    radius_map = {
        "HI_A": radii[0],
        "HI_B": radii[1],
        "LO_A": radii[2],
        "LO_B": radii[3],
    }
    intents: list[dict] = []
    for i, row in enumerate(_EX_ROWS):
        day, hh, mm, dur_min, place, cat, sec, alc, radius, budget, pmin, pmax = row
        radius = radius_map.get(radius, radius)
        start = _at(base, day, hh, mm)
        end = None if dur_min is None else start + timedelta(minutes=dur_min)
        bucket = time_bucket_of(start.hour)
        soft = SOFT_CONSTRAINTS[cat][i % len(SOFT_CONSTRAINTS[cat])]
        raw = RAW_TEMPLATES[cat][i % len(RAW_TEMPLATES[cat])].format(
            time_prefix=TIME_PREFIX[bucket], place=place, secondary=sec, soft=soft
        )
        intents.append(
            {
                "id": f"SD-{i + 1:03d}",
                "user": _EX_USERS[i],
                "ex_group": _EX_GROUPS[i],
                "raw_text": raw,
                "structured": {
                    "category": {"primary": cat, "secondary": sec},
                    "alcohol_involved": alc,
                    "time": {"start": start, "end": end},
                    "location": {"name": place, "radius_m": radius},
                    "budget": {"max": budget},
                    "participants": {"min": pmin, "max": pmax},
                    "soft_constraints": [soft],
                    "visibility": "hidden_until_match",
                    "notification_level": "proposals_only",
                },
            }
        )
    return intents


# 18-19歳ユーザー(残余スロットのalcohol割付でFalse強制する集合・D6)
UNDERAGE_USERS = {u for u, _, _ in UNDERAGE_BIRTHS}


def _build_users(base_date: date, rng: random.Random) -> list[dict]:
    """合成ユーザー100人(SU-001〜100・10 §2・design §2.4)。

    SU-001〜008は18-19歳(UNDERAGE_BIRTHS・base+7日でも18-19歳を保つ
    マージン)。SU-009〜100は20〜50歳から決定的選択。display_nameは
    仮名+番号(実在しない氏名・10 §2)。連絡先は実装契約に存在しない
    (users: display_name/birth_date/profile.bioのみ)ため持たない。
    """
    users: list[dict] = []
    for i in range(1, USER_COUNT + 1):
        uid = f"SU-{i:03d}"
        if i <= len(UNDERAGE_BIRTHS):
            _, years, months = UNDERAGE_BIRTHS[i - 1]
            birth = _minus_ym(base_date, years, months)
        else:
            birth = _minus_ym(base_date, rng.randint(20, 50), rng.randint(0, 11))
        users.append(
            {
                "id": uid,
                "display_name": f"合成{NAME_POOL[i % len(NAME_POOL)]}{i:03d}",
                "birth_date": birth,
                "profile": {"bio": f"テスト用合成ユーザー({uid})"},
            }
        )
    return users


def _max_remainder(total: int, weights: list[int]) -> list[int]:
    """最大剰余法(合計が厳密にtotal・同点は元順序・決定的)。"""
    s = sum(weights)
    if s == 0 or total == 0:
        return [0] * len(weights)
    raw = [w * total / s for w in weights]
    out = [int(math.floor(x)) for x in raw]
    rest = total - sum(out)
    order = sorted(
        range(len(weights)), key=lambda i: (raw[i] - out[i], -i), reverse=True
    )
    for k in range(rest):
        out[order[k]] += 1
    return out


def _assign_users(remaining: int, ex_counts: Counter) -> list[str]:
    """残余スロットのユーザー割付(決定的・SU-001から順に循環)。

    EX素材を持つユーザーは残余最大2件・それ以外は最大3件 — ユーザーごとの
    総intent数をActive上限5件未満に保つ(08 §5.4・参照値14。ws-3の
    staging投入がrate limitに触れない条件)。容量: EX素材ユーザー59人×2+
    その他41人×3=241 >= 残余236(300件時)。
    """
    caps = {
        f"SU-{i:03d}": (2 if ex_counts.get(f"SU-{i:03d}", 0) > 0 else 3)
        for i in range(1, USER_COUNT + 1)
    }
    counts: dict[str, int] = dict.fromkeys(caps, 0)
    assignments: list[str] = []
    while len(assignments) < remaining:
        progressed = False
        for u in caps:
            if len(assignments) >= remaining:
                break
            if counts[u] < caps[u]:
                assignments.append(u)
                counts[u] += 1
                progressed = True
        if not progressed:
            raise ValueError("残余スロットがユーザーキャップ内に収まらない")
    return assignments


def _build_remaining_intent(
    base: datetime,
    rng: random.Random,
    idx: int,
    sd_id: str,
    user: str,
    bucket: str,
    cat: str,
    ori: str,
) -> dict:
    """残余スロット1件を決定的に組み立てる(rngはこの関数順に消費・D10)。

    対象日はD+1〜D+5のラウンドロビン・開始hourはバケット内ラウンドロビン
    (偏りを避ける決定的配分)・分と語彙はrng選択。
    """
    day_offset = DAY_OFFSETS[idx % len(DAY_OFFSETS)]
    if bucket == "day":
        hour = rng.choice([12, 13])
    elif bucket == "evening":
        hour = rng.choice([18, 19])
    elif bucket == "night":
        hour = rng.choice([20, 21, 22])
    else:
        hours = OTHER_BUCKET_HOURS if day_offset > 1 else OTHER_BUCKET_HOURS_DAY1
        hour = hours[idx % len(hours)]  # 偏りを避ける決定的配分
    start = _at(base, day_offset, hour, rng.choice([0, 15, 30, 45]))
    if rng.random() < END_NULL_RATIO:
        end = None  # end null(start+3h補完)のカバレッジ
    else:
        end = start + timedelta(minutes=rng.choice([60, 90, 120, 180]))
    place = rng.choice(list(LOCATIONS))
    secondary = rng.choice(CATEGORY_SECONDARY[cat])
    # alcohol割付(design §2.4規則7+18-19歳例外): 18-19歳ユーザーは全カテゴリで
    # 常にfalse(alcohol trueは作成検証(service.py:430)で422になる「作成拒否
    # 相当」を避ける)・drinking→常にtrue(20歳以上)・activity→常にfalse・
    # meal→20歳以上のみ確率MEAL_ALCOHOL_RATIO(規約を満たす)
    if user in UNDERAGE_USERS:
        alcohol = False
    elif cat == "drinking":
        alcohol = True
    elif cat == "activity":
        alcohol = False
    else:
        alcohol = rng.random() < MEAL_ALCOHOL_RATIO
    if ori == "group":
        pmin, pmax = 3, rng.choice([3, 4])  # グループ=min≧3(design §2.4)
    else:
        pmin, pmax = rng.choice([(2, 2), (2, 3), (2, 4)])  # 1対1=それ以外
    budget = rng.choice(BUDGET_CHOICES)
    radius = rng.choice(RADIUS_CHOICES)
    soft = rng.sample(SOFT_CONSTRAINTS[cat], k=rng.choice([1, 2]))
    raw = rng.choice(RAW_TEMPLATES[cat]).format(
        time_prefix=TIME_PREFIX[bucket],
        place=place,
        secondary=secondary,
        soft=soft[0],
    )
    return {
        "id": sd_id,
        "user": user,
        "raw_text": raw,
        "structured": {
            "category": {"primary": cat, "secondary": secondary},
            "alcohol_involved": alcohol,
            "time": {"start": start, "end": end},
            "location": {"name": place, "radius_m": radius},
            "budget": {"max": budget},
            "participants": {"min": pmin, "max": pmax},
            "soft_constraints": soft,
            "visibility": rng.choice(VISIBILITY_CHOICES),
            "notification_level": rng.choice(NOTIFICATION_CHOICES),
        },
    }


def _remaining_slots(
    base: datetime, rng: random.Random, remaining: int, ex_intents: list[dict]
) -> list[dict]:
    """残余スロット(300件時236件)を比率配分+乱数シャッフルで組み立てる。

    意図的配置の消費(§2.3その2のdocstring・_intentional_slots)を先取りした
    残余目標に10 §2の比率を最大剰余法で適用し、**残余件数(remaining)へ
    スケールする**(任意のcountで決定的 — ws-6の--count 1万〜10万が同一
    コード経路・design §3.3)。時間帯×カテゴリは直交割付(バケット比率→
    バケット内でカテゴリを配分)・人数志向は残余全体へ配分。300件時の
    残余配分: 時間帯=昼50/夕76/夜90/その他20・カテゴリ=drinking 125/
    meal 99/activity 12・志向=1対1 164/グループ 72。
    """
    consumed_bucket = Counter(
        time_bucket_of(i["structured"]["time"]["start"].astimezone(JST).hour)
        for i in ex_intents
    )
    consumed_cat = Counter(i["structured"]["category"]["primary"] for i in ex_intents)
    consumed_ori = Counter(
        "group" if i["structured"]["participants"]["min"] >= 3 else "one_to_one"
        for i in ex_intents
    )
    time_rem = {b: TIME_TARGETS[b] - consumed_bucket.get(b, 0) for b in BUCKET_ORDER}
    cat_rem = {c: CATEGORY_TARGETS[c] - consumed_cat.get(c, 0) for c in CATEGORY_ORDER}
    ori_rem = {
        "one_to_one": ORIENTATION_TARGETS["one_to_one"]
        - consumed_ori.get("one_to_one", 0),
        "group": ORIENTATION_TARGETS["group"] - consumed_ori.get("group", 0),
    }
    if min(*time_rem.values(), *cat_rem.values(), *ori_rem.values()) < 0:
        raise AssertionError("意図的配置の消費が分布目標を超過")
    # 残余件数へ時間帯比率をスケールしてから、バケット内でカテゴリを配分する
    # (300件時は残余目標の合計がremainingと一致するため計画値どおりの配分)
    bucket_cells = _max_remainder(remaining, [time_rem[b] for b in BUCKET_ORDER])
    combos: list[tuple[str, str]] = []
    for bucket, n_bucket in zip(BUCKET_ORDER, bucket_cells, strict=True):
        cells = _max_remainder(n_bucket, [cat_rem[c] for c in CATEGORY_ORDER])
        for cat, n in zip(CATEGORY_ORDER, cells, strict=True):
            combos.extend([(bucket, cat)] * n)
    ori_counts = _max_remainder(remaining, [ori_rem["one_to_one"], ori_rem["group"]])
    ori_list = ["one_to_one"] * ori_counts[0] + ["group"] * ori_counts[1]
    if len(combos) != remaining or len(ori_list) != remaining:
        raise AssertionError("残余割付の総数不一致")
    slots = list(zip(combos, ori_list, strict=True))
    rng.shuffle(slots)  # D10: 固定シードで決定的
    user_assign = _assign_users(remaining, Counter(i["user"] for i in ex_intents))
    return [
        _build_remaining_intent(
            base,
            rng,
            idx,
            f"SD-{EX_SLOT_COUNT + idx + 1:03d}",
            user_assign[idx],
            bucket,
            cat,
            ori,
        )
        for idx, ((bucket, cat), ori) in enumerate(slots)
    ]


def _resolve_reject(base: datetime) -> list[dict]:
    """reject素材の"D+1T20:00"表記を実日時(JST・ISO文字列)へ解決する。"""
    out: list[dict] = []
    for case in REJECT_CASES:
        c = copy.deepcopy(case)
        token = c["structured_intent"]["time"]["start"]
        d_part, h_part = token.split("T")
        start = _at(
            base, int(d_part[1:]), int(h_part.split(":")[0]), int(h_part.split(":")[1])
        )
        c["structured_intent"]["time"]["start"] = _iso(start)
        out.append(c)
    return out


def _iso(dt: datetime) -> str:
    return dt.astimezone(JST).isoformat()


def build_seed(
    base_datetime: datetime, *, seed: int = SEED, count: int = DEFAULT_COUNT
) -> dict:
    """シード定義(内部表現dict)を組み立てる。同一引数→同一出力(design §2.3)。

    生成時にEX-xxxペアケースをreference_l1で判定し、EX_CASESの期待値と
    不一致ならValueError(二重実装の突合・生成側ゲート)。
    """
    if count < COUNT_MIN:
        raise ValueError(
            f"--count は {COUNT_MIN} 以上が必要(意図的配置{EX_SLOT_COUNT}件+残余1件)"
        )
    base = base_datetime.astimezone(JST)
    rng = random.Random(seed)
    users = _build_users(base.date(), rng)
    users_by_id = {u["id"]: u for u in users}
    ex_intents = _intentional_slots(base)
    intents = ex_intents + _remaining_slots(
        base, rng, count - len(ex_intents), ex_intents
    )

    verdicts = judge_all(intents, users_by_id, now=base)
    breakdown = Counter(r for ok, r in verdicts.values() if not ok)
    n_pass = sum(1 for ok, _ in verdicts.values() if ok)

    by_id = {i["id"]: i for i in intents}
    for case in EX_CASES:
        if case["kind"] != "pair":
            continue
        a, b = (by_id[i] for i in case["intents"])
        ok, reason = judge_pair(a, b, users=users_by_id, now=base)
        exp_pass = case["expected"] == "pass"
        if ok != exp_pass or (not exp_pass and reason != case["fail_reason"]):
            raise ValueError(
                f"{case['id']}: 期待不一致 "
                f"expected={case['expected']}/{case['fail_reason']} "
                f"actual={'pass' if ok else 'fail'}/{reason}"
            )
    aggregate = {
        "pair_total": len(verdicts),
        "layer1_pass": n_pass,
        "layer1_fail": len(verdicts) - n_pass,
        "fail_breakdown": {r: breakdown.get(r, 0) for r in FAIL_PRIORITY},
    }
    return {
        "meta": {
            "status": "draft",
            "created": SEED_CREATED,
            "generator": "latch.testassets.generator",
            "base_datetime": _iso(base),
            "random_seed": seed,
            "user_count": len(users),
            "intent_count": len(intents),
            "pair_total": len(verdicts),
            "time_bucket_targets": dict(TIME_TARGETS),
            "category_targets": dict(CATEGORY_TARGETS),
            "orientation_targets": dict(ORIENTATION_TARGETS),
        },
        "users": users,
        "intents": intents,
        "intended_reject_cases": _resolve_reject(base),
        "expectations": {"cases": EX_CASES, "aggregate": aggregate},
    }


def _to_plain(value: object) -> object:
    """datetime/date→ISO文字列へ再帰変換(YAML決定化・JST表記)。"""
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}  # type: ignore[union-attr]
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    return value


def write_yaml(seed: dict, out_path: str) -> None:
    """YAML書き出し(挿入順序固定・allow_unicode・バイト同一性)。"""
    text = yaml.safe_dump(
        _to_plain(seed), allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    Path(out_path).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI: make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"。"""
    parser = argparse.ArgumentParser(description="決定的シード生成器(M4 ws-2)")
    parser.add_argument(
        "--base-datetime",
        required=True,
        help="基準日時(タイムゾーン付きISO8601。例: 2026-10-04T11:00:00+09:00)",
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    base = datetime.fromisoformat(args.base_datetime)
    if base.tzinfo is None:
        raise SystemExit("--base-datetime はタイムゾーン付きISO8601で指定してください")
    seed = build_seed(base, seed=args.seed, count=args.count)
    write_yaml(seed, args.out)
    agg = seed["expectations"]["aggregate"]
    print(
        f"generated {args.out}: intents={len(seed['intents'])} "
        f"users={len(seed['users'])} pair_total={agg['pair_total']} "
        f"layer1_pass={agg['layer1_pass']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
