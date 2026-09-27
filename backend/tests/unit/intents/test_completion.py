"""D-19補完の単一規則(design §4.1-3)。すべてFakeClock由来の固定時刻で決定的。"""

from datetime import UTC, datetime, timedelta

from latch.core.clock import JST
from latch.intents import (
    DEFAULT_PARTICIPANTS,
    DEFAULT_RADIUS_M,
    default_time_end,
    expires_at_candidates,
    nearest_expires_at,
)


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def _jst(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=JST)


def test_constants():
    assert DEFAULT_RADIUS_M == 1000
    assert DEFAULT_PARTICIPANTS == (2, 2)


def test_default_time_end_is_plus_3_hours():
    start = _jst(2026, 9, 27, 19, 0)
    assert default_time_end(start) == _jst(2026, 9, 27, 22, 0)


def test_expires_at_candidates_afternoon_jst():
    """JST 15:00 → 今夜23:30 / 明日12:00 / 明日23:30 / now+72h(03 §3の順)。"""
    now = _utc(2026, 9, 27, 6, 0)  # JST 09-27 15:00
    c = expires_at_candidates(now)
    assert c[0] == _jst(2026, 9, 27, 23, 30)
    assert c[1] == _jst(2026, 9, 28, 12, 0)
    assert c[2] == _jst(2026, 9, 28, 23, 30)
    assert c[3] == now + timedelta(hours=72)
    assert c == sorted(c)


def test_expires_at_candidates_around_jst_midnight():
    """JST 0時跨ぎ: UTC 16:00 = JST 翌01:00 → 「当日」はJST暦日付の09-28。"""
    now = _utc(2026, 9, 27, 16, 0)  # JST 09-28 01:00
    c = expires_at_candidates(now)
    assert c[0] == _jst(2026, 9, 28, 23, 30)
    assert c[1] == _jst(2026, 9, 29, 12, 0)
    assert c[2] == _jst(2026, 9, 29, 23, 30)
    assert c[3] == now + timedelta(hours=72)


def test_nearest_expires_at_prefers_closest_future():
    """time.start 19:00 JST → +3h=22:00 → 最近傍は今夜23:30。"""
    now = _utc(2026, 9, 27, 6, 0)  # JST 15:00
    time_start = _jst(2026, 9, 27, 19, 0)
    assert nearest_expires_at(time_start, now) == _jst(2026, 9, 27, 23, 30)


def test_nearest_expires_at_excludes_passed_tonight():
    """JST 23:45 → 「今夜23:30」は過ぎているので除外(03 §3 disabled規定)。"""
    now = _utc(2026, 9, 27, 14, 45)  # JST 09-27 23:45
    time_start = _jst(2026, 9, 27, 20, 0)  # target 23:00(過去側)
    assert nearest_expires_at(time_start, now) == _jst(2026, 9, 28, 12, 0)


def test_nearest_expires_at_tie_breaks_earliest():
    """target=翌17:45 は明日12:00と明日23:30のちょうど中間 → 最早(12:00)。"""
    now = _utc(2026, 9, 27, 3, 0)  # JST 09-27 12:00
    time_start = _jst(2026, 9, 28, 14, 45)  # target = 09-28 17:45
    assert nearest_expires_at(time_start, now) == _jst(2026, 9, 28, 12, 0)


def test_nearest_expires_at_with_past_time_start_returns_earliest_future():
    """time.startが過去でも最早の選択可能候補を返す(ws-3のdraft保存等での利用)。"""
    now = _utc(2026, 9, 27, 14, 45)  # JST 09-27 23:45
    time_start = _jst(2026, 9, 27, 10, 0)  # 過去
    assert nearest_expires_at(time_start, now) == _jst(2026, 9, 28, 12, 0)
