"""latch_calc純関数のunit試験(design §4.1・06 §6/§10・03 D-05/D-07)。"""

from datetime import UTC, datetime, timedelta

from latch.core.clock import JST
from latch.worker.matching.latch_calc import (
    BUCKET_MINUTES,
    D07_DELTA,
    D08_CONCURRENT_LIMIT,
    D08_DAILY_LIMIT,
    LATCH_C,
    LATCH_THRESHOLD,
    MATCH_LEVEL_HIGH_MIN,
    PROMPT_LEAD_MIN,
    bucket_start,
    d07_allows,
    d07_history_inputs,
    match_level,
    pair_target_time,
    response_deadline,
)

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)  # JST 2026-10-01 21:00
TARGET = NOW + timedelta(hours=30)
EXPIRES = NOW + timedelta(days=5)
DEFER_AT = NOW - timedelta(minutes=10)


def test_constants_pin_docs_values():
    assert LATCH_THRESHOLD == 0.60  # 06 §6(D-01・v0.6)
    assert LATCH_C == 1.0  # 01 §14・C初期値
    assert D07_DELTA == 0.05  # 06 §10
    assert D08_DAILY_LIMIT == 6  # 03 D-08
    assert D08_CONCURRENT_LIMIT == 3  # 03 D-08
    assert MATCH_LEVEL_HIGH_MIN == 0.90  # 05 §2
    assert PROMPT_LEAD_MIN == timedelta(minutes=75)  # 06 §10
    assert BUCKET_MINUTES == 30  # 06 §9


def test_deadline_two_hour_cap_applies():
    # 対象まで4時間 → min(now+2h, target-60m)=now+2h・max(now+15m, now+2h)=now+2h
    d = response_deadline(NOW, NOW + timedelta(hours=4), EXPIRES)
    assert d == NOW + timedelta(hours=2)


def test_deadline_inner_min_applies():
    # 対象まで1時間20分 → min(now+2h, now+20m)=now+20m・max(now+15m, now+20m)=now+20m
    d = response_deadline(NOW, NOW + timedelta(minutes=80), EXPIRES)
    assert d == NOW + timedelta(minutes=20)


def test_deadline_15min_floor_when_target_very_close():
    # 対象まで70分 → min(now+2h, now+10m)=now+10m・max(now+15m, now+10m)=15分下限
    d = response_deadline(NOW, NOW + timedelta(minutes=70), EXPIRES)
    assert d == NOW + timedelta(minutes=15)


def test_deadline_expires_at_is_minimum():
    d = response_deadline(NOW, NOW + timedelta(hours=4), NOW + timedelta(minutes=30))
    assert d == NOW + timedelta(minutes=30)  # expires_atが最少


def test_deadline_can_be_past_notify_time():
    # min_expiresがnow-5分(期限切れIntent)→ 期限が通知時刻を過去に突き抜ける
    # (引用#4「導出期限が通知時刻を過ぎないなら通知しない」の純関数側根拠)
    d = response_deadline(NOW, NOW + timedelta(hours=4), NOW - timedelta(minutes=5))
    assert d <= NOW


def test_match_level_boundaries():
    assert match_level(0.90) == "high"
    assert match_level(0.95) == "high"
    assert match_level(0.80) == "medium"  # 閾値ちょうどは提案(medium)
    assert match_level(0.899) == "medium"
    assert match_level(0.79) == "low"
    assert match_level(0.70) == "low"


def test_pair_target_time_takes_max():
    a = NOW + timedelta(hours=1)
    b = NOW + timedelta(hours=2)
    assert pair_target_time(a, b) == b  # max(参加Intentのtime_start)
    assert pair_target_time(b, a) == b


def test_d07_no_history_denies():
    assert (
        d07_allows(
            has_no_response=True,
            latest_defer_at=None,
            now=NOW,
            target_time=TARGET,
            new_score=0.9,
            prev_latch_score=None,
        )
        is False
    )  # no=Intent期限まで再提案しない


def test_d07_no_defer_allows():
    assert (
        d07_allows(
            has_no_response=False,
            latest_defer_at=None,
            now=NOW,
            target_time=TARGET,
            new_score=0.9,
            prev_latch_score=0.9,
        )
        is True
    )  # 無回答期限切れ等は抑制しない(仕様にない抑制を足さない)


def test_d07_defer_new_generation_allows_within_suppression():
    # 抑制期間内でも新評価世代(prev=None)は無条件変化あり(引用#7手順5)
    assert (
        d07_allows(
            has_no_response=False,
            latest_defer_at=DEFER_AT,
            now=NOW,
            target_time=TARGET,
            new_score=0.85,
            prev_latch_score=None,
        )
        is True
    )


def test_d07_defer_small_delta_denies_within_suppression():
    assert (
        d07_allows(
            has_no_response=False,
            latest_defer_at=DEFER_AT,
            now=NOW,
            target_time=TARGET,
            new_score=0.85,
            prev_latch_score=0.83,
        )
        is False
    )  # |Δ|=0.02 < 0.05


def test_d07_defer_delta_threshold_allows_within_suppression():
    assert (
        d07_allows(
            has_no_response=False,
            latest_defer_at=DEFER_AT,
            now=NOW,
            target_time=TARGET,
            new_score=0.90,
            prev_latch_score=0.85,
        )
        is True
    )  # |Δ|=0.05 ≧ 0.05(境界)


def test_d07_defer_suppression_elapsed_allows():
    # 対象まで3時間 → min(24h, 1.5h)=1.5h。deferから2時間経過=抑制期間経過
    assert (
        d07_allows(
            has_no_response=False,
            latest_defer_at=NOW - timedelta(hours=2),
            now=NOW,
            target_time=NOW + timedelta(hours=3),
            new_score=0.85,
            prev_latch_score=0.85,
        )
        is True
    )


def test_d07_defer_24h_cap():
    # 対象まで3日 → min(24h, 36h)=24h。deferから30時間経過=経過
    assert (
        d07_allows(
            has_no_response=False,
            latest_defer_at=NOW - timedelta(hours=30),
            now=NOW,
            target_time=NOW + timedelta(hours=72),
            new_score=0.85,
            prev_latch_score=0.85,
        )
        is True
    )


def test_d07_history_inputs_extracts_no_and_latest_defer():
    responses = [
        {
            "user_id": "u1",
            "response": "defer",
            "answered_at": "2026-10-01T11:40:00+00:00",
        },
        {
            "user_id": "u2",
            "response": "defer",
            "answered_at": "2026-10-01T11:50:00+00:00",
        },
        {
            "user_id": "u1",
            "response": "yes",
            "answered_at": "2026-10-01T11:55:00+00:00",
        },
    ]
    has_no, latest = d07_history_inputs(responses)
    assert has_no is False
    assert latest == datetime(
        2026, 10, 1, 11, 50, 0, tzinfo=UTC
    )  # answered_at最大のdefer


def test_d07_history_inputs_no_and_empty():
    has_no, latest = d07_history_inputs(
        [
            {
                "user_id": "u1",
                "response": "no",
                "answered_at": "2026-10-01T11:40:00+00:00",
            }
        ]
    )
    assert has_no is True and latest is None
    assert d07_history_inputs([]) == (False, None)


def test_bucket_start_boundaries():
    # JST 21:00→分0(00分台は切り下げ0)・JST 21:29:59→21:00・JST 21:30ちょうど→21:30
    assert bucket_start(datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)) == datetime(
        2026, 10, 1, 21, 0, 0, tzinfo=JST
    )
    assert bucket_start(datetime(2026, 10, 1, 12, 29, 59, tzinfo=UTC)) == datetime(
        2026, 10, 1, 21, 0, 0, tzinfo=JST
    )
    assert bucket_start(datetime(2026, 10, 1, 12, 30, 0, tzinfo=UTC)) == datetime(
        2026, 10, 1, 21, 30, 0, tzinfo=JST
    )
    # UTC 15:45 = JST 00:45(日付跨ぎ)→ JST 00:30
    assert bucket_start(datetime(2026, 10, 1, 15, 45, 0, tzinfo=UTC)) == datetime(
        2026, 10, 2, 0, 30, 0, tzinfo=JST
    )


def test_jst_day_start_reexported_from_layer4():
    from latch.worker.matching import layer4
    from latch.worker.matching.latch_calc import jst_day_start

    assert jst_day_start is layer4.jst_day_start  # 再実装しない(Review Focus 5)
