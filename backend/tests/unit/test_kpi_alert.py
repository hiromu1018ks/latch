"""O-06週次警報の純関数期待値表とKpiAlertJobの単発化(unit)。

D-02警報線: YES率<10%・通知オフ率週比2倍。A/B有害シグナル: 通報率週比2倍
(09 §5)。ジョブのDB読取層はintegration(test_kpi_alert_ci)が所有する。
"""

from datetime import date

import pytest

from latch.obs.kpi_alert import (
    MIN_DEN,
    SPIKE_FACTOR,
    YES_RATE_MIN,
    rate_spike,
    weekly_yes_totals,
    yes_rate_low,
)


def test_yes_rate_low_boundaries():
    """10%未満で発報(丁度10%は発報しない)。分母min_den未満は判定しない。"""
    assert yes_rate_low(0, 100, min_rate=YES_RATE_MIN, min_den=MIN_DEN)
    assert yes_rate_low(9, 100, min_rate=YES_RATE_MIN, min_den=MIN_DEN)
    assert not yes_rate_low(10, 100, min_rate=YES_RATE_MIN, min_den=MIN_DEN)
    assert not yes_rate_low(50, 100, min_rate=YES_RATE_MIN, min_den=MIN_DEN)
    # 分母不足(9回答・全員no)はノイズ抑止で判定しない
    assert not yes_rate_low(0, 9, min_rate=YES_RATE_MIN, min_den=MIN_DEN)


def test_weekly_yes_totals_sums_daily_rows():
    rows = [(1, 10), (0, 10), (2, 5), (0, 0)]
    assert weekly_yes_totals(rows) == (3, 25)


def test_rate_spike_requires_double_and_nonzero_prev():
    f = SPIKE_FACTOR
    # 丁度2倍は発報(>=)
    assert rate_spike(20, 100, 10, 100, factor=f)
    assert rate_spike(25, 100, 10, 100, factor=f)
    # 2倍未満は発報しない
    assert not rate_spike(19, 100, 10, 100, factor=f)
    # 前週分子0(0→n)は急増としない(倍率が定義できない・設計判断)
    assert not rate_spike(50, 100, 0, 100, factor=f)
    # 分母0は判定しない
    assert not rate_spike(50, 0, 10, 100, factor=f)
    assert not rate_spike(50, 100, 10, 0, factor=f)


def test_constants_pin_d02_lines():
    """D-02の固定値(09 §6)。変更はdocs改版を経る。"""
    assert YES_RATE_MIN == 0.10
    assert SPIKE_FACTOR == 2


def test_week_selection_is_monday_start():
    """完了週の選択: 昨日以前で最も近い日曜が終わりの週(月曜始まり)。"""
    from latch.obs.mlr import last_completed_week_start

    # 2026-10-12(月)→直近完了週=10-05(月)〜10-11(日)
    assert last_completed_week_start(date(2026, 10, 12)) == date(2026, 10, 5)
    # 2026-10-14(水)→同じ週(10-11が日曜)
    assert last_completed_week_start(date(2026, 10, 14)) == date(2026, 10, 5)
    # 2026-10-18(日)→当日の週はまだ完了していない(前週10-05〜10-11)
    assert last_completed_week_start(date(2026, 10, 18)) == date(2026, 10, 5)
    # 2026-10-19(月)→昨日が日曜=直前の週(10-12〜10-18)が完了
    assert last_completed_week_start(date(2026, 10, 19)) == date(2026, 10, 12)


class _ArmRecorder:
    """KpiAlertJobのRedis単発化をfakeredisなしで検査する偽redis。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True


@pytest.mark.asyncio
async def test_arm_once_per_week_and_kind():
    """(週, kind)単位の単発化(Redisアームキー・lag_alertと同一規律)。"""
    from latch.obs.kpi_alert import ARM_KEY_PREFIX, KpiAlertJob

    job = KpiAlertJob.__new__(KpiAlertJob)
    fake = _ArmRecorder()
    job._redis = fake
    job._arm_ttl_s = 8 * 24 * 3600
    job._local_armed = set()
    key = f"{ARM_KEY_PREFIX}:kpi_yes_rate_low:2026-10-05"
    assert await job._arm(key) is True
    assert await job._arm(key) is False  # 同週同kindは再発報しない
    other = f"{ARM_KEY_PREFIX}:kpi_mute_rate_spike:2026-10-05"
    assert await job._arm(other) is True  # 別kindは独立
    next_week = f"{ARM_KEY_PREFIX}:kpi_yes_rate_low:2026-10-12"
    assert await job._arm(next_week) is True  # 翌週は再検知可能


@pytest.mark.asyncio
async def test_arm_local_fallback_without_redis():
    from latch.obs.kpi_alert import ARM_KEY_PREFIX, KpiAlertJob

    job = KpiAlertJob.__new__(KpiAlertJob)
    job._redis = None
    job._arm_ttl_s = 8 * 24 * 3600
    job._local_armed = set()
    key = f"{ARM_KEY_PREFIX}:x:2026-10-05"
    assert await job._arm(key) is True
    assert await job._arm(key) is False
