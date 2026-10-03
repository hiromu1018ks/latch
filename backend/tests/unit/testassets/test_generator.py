"""決定的シード生成器のunit試験(M4 ws-2・design §4-1・4-2)。

決定性(バイト同一)・分布比率の実現・意図的配置の成立・副次分布を検証する。
300件フル生成は全ペアL1判定(44,850組)を含むが純Pythonで1〜2秒程度。
"""

from collections import Counter
from datetime import datetime, timedelta

import pytest

from latch.testassets.generator import (
    DEFAULT_COUNT,
    EX_SLOT_COUNT,
    SEED,
    build_seed,
    write_yaml,
)
from latch.testassets.reference_l1 import JST, age_years_at, judge_all, judge_pair
from latch.testassets.vocab import (
    CATEGORY_SECONDARY,
    LOCATIONS,
    RAW_TEMPLATES,
    SOFT_CONSTRAINTS,
    TIME_PREFIX,
    time_bucket_of,
)

# --- 0. 語彙プール(M4 ws-2 Task 1) ---


def test_vocab_pools_are_valid():
    """語彙プールの基本性質(7地名・bbox内・カテゴリ網羅・テンプレート整合)。"""
    assert (
        len(LOCATIONS) == 7
    )  # 9→7はsupervisor裁定2026-10-03(ci-db実査・正転ヒット済みのみ)
    for lat, lon in LOCATIONS.values():  # settings.py geo_osm_bbox
        assert 31.5825 <= lat <= 31.6095
        assert 130.5420 <= lon <= 130.5740
    assert set(CATEGORY_SECONDARY) == {"drinking", "meal", "activity"}
    assert set(SOFT_CONSTRAINTS) == {"drinking", "meal", "activity"}
    assert set(RAW_TEMPLATES) == {"drinking", "meal", "activity"}
    for tpls in RAW_TEMPLATES.values():
        for t in tpls:
            assert t.format(time_prefix="x", place="y", secondary="z", soft="w")
    assert set(TIME_PREFIX) == {"day", "evening", "night", "other"}
    assert time_bucket_of(12) == "day"  # 昼=12:00-13:59(design §2.4)
    assert time_bucket_of(18) == "evening"  # 夕=18:00-19:59
    assert time_bucket_of(20) == "night"  # 夜=20:00-22:59
    assert time_bucket_of(13) == "day"
    assert time_bucket_of(19) == "evening"
    assert time_bucket_of(22) == "night"
    assert time_bucket_of(11) == "other"  # その他=上記以外
    assert time_bucket_of(14) == "other"
    assert time_bucket_of(23) == "other"


# --- 1〜3. 生成器本体(M4 ws-2 Task 3) ---

BASE = datetime(2026, 10, 4, 11, 0, tzinfo=JST)


@pytest.fixture(scope="module")
def seed():
    """300件フル生成(全ペアL1判定込み・module scopeで1回だけ実行)。"""
    return build_seed(BASE, seed=SEED, count=DEFAULT_COUNT)


def _by_id(seed):
    return {i["id"]: i for i in seed["intents"]}


def test_generation_is_byte_identical(tmp_path):
    """同一引数での2回生成がバイト同一(design §2.3・Review Focus 1)。"""
    p1, p2 = tmp_path / "a.yaml", tmp_path / "b.yaml"
    write_yaml(build_seed(BASE, seed=SEED, count=80), str(p1))
    write_yaml(build_seed(BASE, seed=SEED, count=80), str(p2))
    assert p1.read_bytes() == p2.read_bytes()


def test_small_count_is_deterministic(seed):
    """--count変更時も決定的(低件数生成・design §4-1)。count違いは別出力。"""
    a = build_seed(BASE, seed=SEED, count=100)
    b = build_seed(BASE, seed=SEED, count=100)
    assert a == b
    assert a != seed  # count違いは異なる生成物


def test_count_below_ex_pool_raises():
    """意図的配置(EX素材64件)未満のcountはValueError。"""
    with pytest.raises(ValueError, match="64"):
        build_seed(BASE, seed=SEED, count=30)


def test_intent_ids_unique_and_prefixes(seed):
    """SD-001〜300が一意連番・SU一意・ex_groupはEX素材64件のみ。"""
    ids = [i["id"] for i in seed["intents"]]
    assert ids == [f"SD-{n:03d}" for n in range(1, 301)]
    assert len({i["user"] for i in seed["intents"]}) <= 100
    assert all(u["id"].startswith("SU-") for u in seed["users"])
    assert {i["id"] for i in seed["intents"] if "ex_group" in i} == {
        f"SD-{n:03d}" for n in range(1, EX_SLOT_COUNT + 1)
    }


def test_time_bucket_targets(seed):
    """時間帯分布 60/90/120/30(10 §2・Review Focus 2)。"""

    def bucket(i):
        h = i["structured"]["time"]["start"].astimezone(JST).hour
        return time_bucket_of(h)

    got = Counter(bucket(i) for i in seed["intents"])
    assert got == Counter({"day": 60, "evening": 90, "night": 120, "other": 30})


def test_category_targets(seed):
    """カテゴリ分布 150/120/30(10 §2)。"""
    got = Counter(i["structured"]["category"]["primary"] for i in seed["intents"])
    assert got == Counter({"drinking": 150, "meal": 120, "activity": 30})


def test_orientation_targets(seed):
    """人数志向分布 1対1 210/グループ90(10 §2・min≧3=グループ)。"""
    got = Counter(
        "group" if i["structured"]["participants"]["min"] >= 3 else "one_to_one"
        for i in seed["intents"]
    )
    assert got == Counter({"one_to_one": 210, "group": 90})


def test_underage_users_are_8_and_stay_underage(seed):
    """18-19歳8人・base+7日でも18-19歳(D6・design §2.4)。"""
    underage = [
        u
        for u in seed["users"]
        if age_years_at(u["birth_date"], BASE.astimezone(JST).date()) < 20
    ]
    assert len(underage) == 8
    later = (BASE + timedelta(days=7)).astimezone(JST).date()
    for u in underage:
        assert age_years_at(u["birth_date"], later) < 20


def test_underage_intents_all_alcohol_false(seed):
    """18-19歳ユーザーの全intentがalcohol_involved=false(design §2.4)。"""
    underage_ids = {
        u["id"]
        for u in seed["users"]
        if age_years_at(u["birth_date"], BASE.astimezone(JST).date()) < 20
    }
    for i in seed["intents"]:
        if i["user"] in underage_ids:
            assert i["structured"]["alcohol_involved"] is False


def test_drinking_all_alcohol_true_and_activity_false(seed):
    """drinking→全true・activity→全false(規則7・design §2.4)。

    18-19歳ユーザーはdrinkingカテゴリ自体を持たない(2026-10-03スーパー
    バイザー裁定・test_underage_intents_never_drinking)ため例外なし —
    drinkingは実行時(mapping.py resolve_for_active)でalcohol trueへ
    強制されるためYAML上もtrueで一貫させる。
    """
    for i in seed["intents"]:
        cat = i["structured"]["category"]["primary"]
        alc = i["structured"]["alcohol_involved"]
        if cat == "activity":
            assert alc is False
        if cat == "drinking":
            assert alc is True


def test_underage_intents_never_drinking(seed):
    """18-19歳ユーザーのintentはdrinkingカテゴリを含まない(裁定2026-10-03)。

    drinkingはmapping.py:123でalcohol trueへ強制され、20歳未満は作成検証
    (service.py:638)で422 UNDER_AGEになる — シードの全IntentがAPIで
    作成可能であることの前提(ws-3 BLOCKED事由の再発防止)。
    """
    underage_ids = {
        u["id"]
        for u in seed["users"]
        if age_years_at(u["birth_date"], BASE.astimezone(JST).date()) < 20
    }
    bad = [
        i["id"]
        for i in seed["intents"]
        if i["user"] in underage_ids
        and i["structured"]["category"]["primary"] == "drinking"
    ]
    assert not bad, f"18-19歳ユーザー×drinking: {bad}"


def test_alcohol_true_implies_creator_20_or_older(seed):
    """alcohol_involved=trueのIntentは作成者が20歳以上(実行時作成契約のピン)。

    保存APIの作成検証(service.py:638-639・_require_age_20)はalcohol true
    ×20歳未満を422にするため、この不変式が「全Intent作成可能」と同値。
    """
    users = {u["id"]: u for u in seed["users"]}
    bad = [
        i["id"]
        for i in seed["intents"]
        if i["structured"]["alcohol_involved"]
        and age_years_at(users[i["user"]]["birth_date"], BASE.astimezone(JST).date())
        < 20
    ]
    assert not bad, f"alcohol true×20歳未満の作成者: {bad}"


def test_d10_material_is_runtime_creatable(seed):
    """EX-004/005(D-10素材)が実行時契約で成立する形であること。

    20歳以上側=alcohol true・18-19歳側=alcohol false×同一カテゴリ
    (category_mismatchを先に発火させない)×若年側はdrinkingでない
    (作成拒否にならない)。ペア判定がage_alcohol単独failであることは
    test_ex_pair_cases_match_expectedが担保。
    """
    by_id = _by_id(seed)
    users = {u["id"]: u for u in seed["users"]}
    for case_id in ("EX-004", "EX-005"):
        case = next(c for c in seed["expectations"]["cases"] if c["id"] == case_id)
        older, younger = (by_id[i] for i in case["intents"])
        assert (
            age_years_at(
                users[older["user"]]["birth_date"], BASE.astimezone(JST).date()
            )
            >= 20
        )
        assert (
            age_years_at(
                users[younger["user"]]["birth_date"], BASE.astimezone(JST).date()
            )
            < 20
        )
        assert older["structured"]["alcohol_involved"] is True
        assert younger["structured"]["alcohol_involved"] is False
        assert (
            older["structured"]["category"]["primary"]
            == younger["structured"]["category"]["primary"]
        )
        assert younger["structured"]["category"]["primary"] != "drinking"


def test_visibility_notification_coverage(seed):
    """visibility 2値・notification_level 3値の全値カバレッジ(D5)。"""
    assert {i["structured"]["visibility"] for i in seed["intents"]} == {
        "hidden_until_match",
        "summary_only",
    }
    assert {i["structured"]["notification_level"] for i in seed["intents"]} == {
        "proposals_only",
        "nearby_also",
        "muted",
    }


def test_intended_reject_cases_structure(seed):
    """reject素材5件: alcohol true・expected 422/UNDER_AGE・18-19歳ユーザー。"""
    users = {u["id"]: u for u in seed["users"]}
    rejects = seed["intended_reject_cases"]
    assert [r["id"] for r in rejects] == [f"EX-{n:03d}" for n in range(29, 34)]
    for r in rejects:
        assert r["expected_status"] == 422
        assert r["expected_code"] == "UNDER_AGE"
        assert r["structured_intent"]["alcohol_involved"] is True
        assert (
            age_years_at(users[r["user"]]["birth_date"], BASE.astimezone(JST).date())
            < 20
        )


def test_self_pair_materials(seed):
    """自己ペア3組素材: 同一ユーザー2 intents・同カテゴリ・同地名・交差(§2.2)。"""
    by_id = _by_id(seed)
    for a_id, b_id in [
        ("SD-001", "SD-002"),
        ("SD-003", "SD-004"),
        ("SD-005", "SD-006"),
    ]:
        a, b = by_id[a_id], by_id[b_id]
        assert a["user"] == b["user"]
        assert (
            a["structured"]["category"]["primary"]
            == b["structured"]["category"]["primary"]
        )
        assert (
            a["structured"]["location"]["name"] == b["structured"]["location"]["name"]
        )
        assert a["structured"]["time"]["start"] < b["structured"]["time"]["end"]


def test_self_dup_cluster_material(seed):
    """自己重複クラスタ: 4 intents・distinct users=3・min≧3(EX-025・FR-17)。"""
    by_id = _by_id(seed)
    members = [by_id[f"SD-{n:03d}"] for n in range(48, 52)]
    assert len({m["user"] for m in members}) == 3
    assert all(m["structured"]["participants"]["min"] >= 3 for m in members)


def test_ex_pair_cases_match_expected(seed):
    """EXペア全24件がreference_l1判定と一致(生成時ゲートの裏取り)。"""
    users = {u["id"]: u for u in seed["users"]}
    by_id = _by_id(seed)
    for case in seed["expectations"]["cases"]:
        if case["kind"] != "pair":
            continue
        a, b = (by_id[i] for i in case["intents"])
        ok, reason = judge_pair(a, b, users=users, now=BASE)
        if case["expected"] == "pass":
            assert (ok, reason) == (True, None), case["id"]
        else:
            assert (ok, reason) == (False, case["fail_reason"]), case["id"]


def test_intents_within_d_plus_1_to_5(seed):
    """全intentの時刻がbaseのD+1〜D+5内(design §2.3)。"""
    for i in seed["intents"]:
        delta = (i["structured"]["time"]["start"] - BASE).total_seconds() / 86400
        assert 0.9 < delta < 6.0, i["id"]


def test_raw_text_constraints(seed):
    """raw_textは1〜300字(参照値13)。"""
    for i in seed["intents"]:
        assert 1 <= len(i["raw_text"]) <= 300


def test_aggregate_matches_reference(seed):
    """aggregateがjudge_all再計算と一致・pair_total=44850(design §2.2)。"""
    users = {u["id"]: u for u in seed["users"]}
    verdicts = judge_all(seed["intents"], users, now=BASE)
    got_pass = sum(1 for ok, _ in verdicts.values() if ok)
    agg = seed["expectations"]["aggregate"]
    assert agg["pair_total"] == len(verdicts) == 44850
    assert agg["layer1_pass"] == got_pass
    assert agg["layer1_fail"] == len(verdicts) - got_pass
    assert sum(agg["fail_breakdown"].values()) == agg["layer1_fail"]
    assert agg["fail_breakdown"]["blocked"] == 0  # D8(シードにblocksなし)
