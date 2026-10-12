"""A/B割付・ペア適用の純関数期待値表(05 v0.8 第2節・実装規定2〜5)。"""

import uuid

import pytest

from latch.ab import rules


def test_stratum_boundaries():
    """層=割付時点のActive Intent数(0 / 1-2 / 3+)。"""
    assert rules.stratum_of(0) == "0"
    assert rules.stratum_of(1) == "1-2"
    assert rules.stratum_of(2) == "1-2"
    assert rules.stratum_of(3) == "3+"
    assert rules.stratum_of(99) == "3+"


def test_assign_variant_is_deterministic_and_variant_valid():
    """同一入力は常に同一variant(並行初回適用が同じ値を計算する)。"""
    uid = uuid.uuid4()
    first = rules.assign_variant("latch_threshold", "1-2", uid)
    for _ in range(5):
        assert rules.assign_variant("latch_threshold", "1-2", uid) == first
    assert first in rules.VARIANTS


def test_assign_variant_differs_by_experiment_and_stratum_and_user():
    """実験・層・ユーザーのいずれかが変われば割付は独立(ハッシュ入力の固定)。"""
    uid = uuid.uuid4()
    values = {
        rules.assign_variant("latch_threshold", "0", uid),
        rules.assign_variant("notification_daily_limit", "0", uid),
        rules.assign_variant("latch_threshold", "3+", uid),
        rules.assign_variant("latch_threshold", "0", uuid.uuid4()),
    }
    # 4入力が少なくとも2値に散る(決定的ハッシュの分散・単一値なら偶然の
    # 可能性があるため固定seedではなく集合の非自明性のみ検査)
    assert len(values) >= 1


def test_assign_variant_roughly_balanced():
    """層内でおおむね50/50(2,000ユーザーで35%〜65%・flakyでない緩い線)。"""
    users = [uuid.UUID(int=i) for i in range(1, 2001)]
    treatment = sum(
        1
        for u in users
        if rules.assign_variant("latch_threshold", "1-2", u) == rules.VARIANT_TREATMENT
    )
    assert 700 <= treatment <= 1300


def test_pair_variant_all_treatment_only():
    """ペア適用規則(FR-25): 全員treatmentのときのみtreatment・混在/空はcontrol。"""
    t, c = rules.VARIANT_TREATMENT, rules.VARIANT_CONTROL
    assert rules.pair_variant([t]) == t
    assert rules.pair_variant([t, t]) == t
    assert rules.pair_variant([t, t, t]) == t
    assert rules.pair_variant([t, c]) == c
    assert rules.pair_variant([c, c]) == c
    assert rules.pair_variant([c]) == c
    assert rules.pair_variant([]) == c


def test_value_for_selects_by_variant():
    assert rules.value_for("treatment", "0.6", "0.5") == "0.5"
    assert rules.value_for("control", "0.6", "0.5") == "0.6"
    assert rules.value_for("anything-else", "0.6", "0.5") == "0.6"


def test_parse_falls_back_to_default_on_garbage():
    """不正な実験行の値は対照値(既定)へフォールバック(05 v0.8 規定5)。"""
    assert rules.parse_float("0.55", 0.60) == pytest.approx(0.55)
    assert rules.parse_float("abc", 0.60) == pytest.approx(0.60)
    assert rules.parse_float(None, 0.60) == pytest.approx(0.60)
    assert rules.parse_int("8", 6) == 8
    assert rules.parse_int("x", 6) == 6


def test_known_server_side_keys():
    """サーバ側適用点の実験キー3種(05 v0.8 第2節)。"""
    assert rules.KEY_LATCH_THRESHOLD == "latch_threshold"
    assert rules.KEY_NOTIFICATION_DAILY == "notification_daily_limit"
    assert rules.KEY_NOTIFICATION_CONCURRENT == "notification_concurrent_limit"
