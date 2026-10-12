"""A-03 MLR前期比−5pt判定の純関数期待値表(09 §6 D-02・05 v0.8)。

DB読取・記録層はintegration(test_mlr_ci)が所有する。ここは判定数学のみ。
"""

import pytest

from latch.obs.mlr import (
    BASELINE_WEEKS,
    WITHIN_BASELINE_PT,
    diff_pt,
    median_of_weeks,
    weekly_mlr_value,
    within_baseline,
)


def test_weekly_mlr_value_none_on_zero_denominator():
    assert weekly_mlr_value(3, 10) == pytest.approx(0.3)
    assert weekly_mlr_value(0, 10) == pytest.approx(0.0)
    assert weekly_mlr_value(0, 0) is None  # 欠測(分母0)


def test_median_of_four_weeks_ignores_missing():
    values = [0.10, None, 0.20, 0.30]
    assert median_of_weeks(values) == pytest.approx(0.20)
    assert median_of_weeks([None, None, None, None]) is None
    assert median_of_weeks([0.10, None, None, None]) is None  # 2週未満は記録不能


def test_diff_pt_is_percentage_points():
    assert diff_pt(0.25, 0.30) == pytest.approx(-5.0)
    assert diff_pt(0.30, 0.25) == pytest.approx(5.0)
    assert diff_pt(0.30, 0.30) == pytest.approx(0.0)


def test_within_baseline_minus_5pt_boundary():
    """維持条件: 前期(ベースライン)比マイナス5ポイント以内(09 D-02)。"""
    assert within_baseline(0.30, 0.30)  # 同値
    assert within_baseline(0.25, 0.30)  # 丁度−5pt=以内
    assert not within_baseline(0.2499, 0.30)  # −5.01pt=違反
    assert within_baseline(0.40, 0.30)  # 向上は問題なし


def test_constants_pin_d02_values():
    assert WITHIN_BASELINE_PT == 5.0
    assert BASELINE_WEEKS == 4
