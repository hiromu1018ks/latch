"""Layer 1参照実装のunit試験(M4 ws-2・design §4-3・06 §2確定表)。

既知の小ペア集合(時間交差境界・end null補完・距離境界・予算500円・人数・
飲酒年齢・自己ペア・ブロック)でpass/fail+fail_reasonを確認する。
実装はlayer1.pyのLAYER1_WHERE(06 §2)と1対1対応をモジュールdocstringで
明記している前提で、本試験がその挙動をピン留めする。
"""

from datetime import date, datetime

from latch.testassets.reference_l1 import (
    JST,
    age_years_at,
    judge_all,
    judge_pair,
)

BASE = datetime(2026, 10, 4, 11, 0, tzinfo=JST)


def _intent(
    sd: str,
    user: str,
    *,
    cat="drinking",
    alc=True,
    start="2026-10-05T20:00",
    end="2026-10-05T23:00",
    place="天文館",
    radius=1000,
    budget=3500,
    pmin=2,
    pmax=2,
):
    return {
        "id": sd,
        "user": user,
        "structured": {
            "category": {"primary": cat, "secondary": "x"},
            "alcohol_involved": alc,
            "time": {
                "start": datetime.fromisoformat(start).replace(tzinfo=JST),
                "end": None
                if end is None
                else datetime.fromisoformat(end).replace(tzinfo=JST),
            },
            "location": {"name": place, "radius_m": radius},
            "budget": {"max": budget},
            "participants": {"min": pmin, "max": pmax},
        },
    }


def _users(*ages: int) -> dict:
    """指定年齢のユーザー辞書(BASE時点の満年齢)。"""
    out = {}
    for i, a in enumerate(ages, 1):
        uid = f"SU-{i:03d}"
        out[uid] = {"id": uid, "birth_date": date(BASE.year - a, BASE.month, BASE.day)}
    return out


# --- 1. passの基本形と各fail_reason(06 §2確定表の1条件ずつ) ---


def test_pass_basic_overlap():
    """同カテゴリ・交差・同地名・予算・人数・20歳以上 → pass。"""
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", start="2026-10-05T20:30")
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (True, None)


def test_fail_category_mismatch():
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", cat="meal", alc=False)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (
        False,
        "category_mismatch",
    )


def test_fail_time_adjacent_boundary():
    """終了=開始の隣接は交差0(SQL: :origin_time_start < i.time_end が偽)。"""
    a = _intent("SD-001", "SU-001", start="2026-10-05T20:00", end="2026-10-05T21:30")
    b = _intent("SD-002", "SU-002", start="2026-10-05T21:30", end="2026-10-05T23:00")
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "time0")


def test_pass_end_null_completion():
    """end nullはstart+3h補完で交差成立(COALESCE(i.time_end, +3h))。"""
    a = _intent(
        "SD-001",
        "SU-001",
        cat="meal",
        alc=False,
        start="2026-10-05T12:00",
        end="2026-10-05T14:00",
    )
    b = _intent(
        "SD-002", "SU-002", cat="meal", alc=False, start="2026-10-05T13:00", end=None
    )
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (True, None)


def test_fail_time_disjoint():
    a = _intent("SD-001", "SU-001", start="2026-10-05T10:00", end="2026-10-05T11:00")
    b = _intent("SD-002", "SU-002", start="2026-10-05T15:00", end="2026-10-05T16:00")
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "time0")


# --- 2. 距離境界(ST_DWithin r_origin+COALESCE(r_target,1000)) ---


def test_fail_distance_far_with_null_radius():
    """radius null×null=1000+1000 < 加治屋町〜浜町(約2530m)→ distance。

    地名はsupervisor裁定2026-10-03のci-db代表点7地名(旧鹿児島中央駅前は
    正転ヒットせず廃止 — 実距離はreference_l1.haversine_mで確定)。
    """
    a = _intent("SD-001", "SU-001", place="加治屋町", radius=None)
    b = _intent("SD-002", "SU-002", place="浜町", radius=None)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "distance")


def test_pass_distance_exact_ceil():
    """半径和が実距離以上(haversineでceil側)→ pass・切る側はfail。"""
    import math

    from latch.testassets.reference_l1 import haversine_m
    from latch.testassets.vocab import LOCATIONS

    d = haversine_m(LOCATIONS["天文館"], LOCATIONS["高見馬場"])
    a = _intent("SD-001", "SU-001", place="天文館", radius=math.ceil(d) - 1)
    b = _intent("SD-002", "SU-002", place="高見馬場", radius=1)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (True, None)
    a2 = _intent("SD-003", "SU-003", place="天文館", radius=math.floor(d) - 1)
    assert judge_pair(a2, b, users=_users(30, 28, 27), now=BASE) == (False, "distance")


# --- 3. 予算・人数(06 §2のLEAST NULL無視・2∈[min,max]) ---


def test_fail_budget_below_500():
    a = _intent("SD-001", "SU-001", budget=499)
    b = _intent("SD-002", "SU-002", budget=499)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "budgetmin")


def test_pass_budget_500_exact_and_null_ignored():
    """500ちょうどは通る。NULLは無視(片方のみでもmin(有効値)>=500ならpass)。"""
    a = _intent("SD-001", "SU-001", budget=500)
    b = _intent("SD-002", "SU-002", budget=None)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (True, None)


def test_fail_people_out_of_range():
    """2∉[min,max]の双方パターン(min3超過・max1不足)。"""
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", pmin=3, pmax=4)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "people")
    c = _intent("SD-003", "SU-003", pmin=1, pmax=1)
    d = _intent("SD-004", "SU-004", pmin=2, pmax=2)
    assert judge_pair(c, d, users=_users(30, 28, 27, 26), now=BASE) == (False, "people")


# --- 4. 飲酒年齢(D-10・双方20歳以上) ---


def test_fail_age_alcohol_under20():
    """drinking×drinkingで他条件pass・片側19歳 → age_alcohol(D3の構成)。"""
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", alc=False, start="2026-10-05T20:30")
    users = _users(30, 19)
    assert judge_pair(a, b, users=users, now=BASE) == (False, "age_alcohol")


def test_pass_age_alcohol_both_20():
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", start="2026-10-05T20:30")
    assert judge_pair(a, b, users=_users(30, 20), now=BASE) == (True, None)


def test_age_boundary_on_20th_birthday():
    """満20歳の誕生日当日は条件を満たす(EXTRACT(YEAR FROM AGE())の規則)。"""
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", start="2026-10-05T20:30")
    users = _users(30, 20)
    users["SU-002"]["birth_date"] = date(2006, 10, 4)  # BASE日時点で満20歳
    assert judge_pair(a, b, users=users, now=BASE) == (True, None)


# --- 5. 自己ペア・ブロック・無向全ペア ---


def test_fail_self_pair():
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-001")
    assert judge_pair(a, b, users=_users(30), now=BASE) == (False, "self_pair")


def test_blocked_pair_symmetric():
    """ブロックは双方向(どちらがブロッカーでもfail)。"""
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002")
    assert judge_pair(
        a, b, users=_users(30, 28), now=BASE, blocks={("SU-001", "SU-002")}
    ) == (False, "blocked")
    assert judge_pair(
        a, b, users=_users(30, 28), now=BASE, blocks={("SU-002", "SU-001")}
    ) == (False, "blocked")


def test_judge_all_counts_undirected_pairs():
    """judge_allは無向ペア集合(n=3なら3組・id順キー)。"""
    a = _intent("SD-002", "SU-001")
    b = _intent("SD-001", "SU-002")
    c = _intent("SD-003", "SU-003", cat="meal", alc=False)
    got = judge_all([a, b, c], _users(30, 28, 27), now=BASE)
    assert set(got) == {
        ("SD-001", "SD-002"),
        ("SD-001", "SD-003"),
        ("SD-002", "SD-003"),
    }
    assert got[("SD-001", "SD-002")] == (True, None)
    assert got[("SD-001", "SD-003")] == (False, "category_mismatch")


def test_age_years_at_matches_full_age():
    """満年齢(誕生日前は-1)。users.service.age_yearsと同一規則。"""
    assert age_years_at(date(2006, 10, 4), date(2026, 10, 4)) == 20
    assert age_years_at(date(2006, 10, 5), date(2026, 10, 4)) == 19
