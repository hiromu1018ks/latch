"""KPI純関数層の期待値表(09 §2.2・design §4)。

SQL実行層の正しさはintegration(test_obs_kpi_job_ci・supervisor検証時)と
staging受け入れ(compute_mlr実測一致・Task 13)が所有する3層構成の1層目
(design §2.1読み替え6)。
"""

from datetime import UTC, date, datetime

from latch.core.clock import JST
from latch.obs.kpi import (
    DAILY_METRIC_KEYS,
    METRIC_KEYS,
    backfill_days,
    is_week_end,
    jst_day,
    new_muted_users,
    retention_due,
    week_end_of,
    week_of,
    yes_rate_from_responses,
)


def test_metric_keys_cover_09_2_2_table():
    """metric_key集合が§2.3の定義表と完全一致(15キー)。"""
    assert METRIC_KEYS == {
        "intent_create_rate",
        "intent_recreation_rate",
        "active_intent_per_dau",
        "retention_7d",
        "retention_30d",
        "candidate_generation_rate",
        "proposal_rate",
        "hold_expired_rate",
        "yes_rate",
        "mlr",
        "attendance_rate",
        "cancel_rate",
        "no_answer_rate",
        "jev_executions_per_intent",
        "mute_rate",
    }
    assert DAILY_METRIC_KEYS == {
        "intent_create_rate",
        "active_intent_per_dau",
        "candidate_generation_rate",
        "proposal_rate",
        "hold_expired_rate",
        "yes_rate",
        "mlr",
        "jev_executions_per_intent",
    }


def test_jst_day_converts_utc_night_to_jst_date():
    """UTC 2026-10-03T16:00ZはJSTでは10-04(JST期間切りの基本)。"""
    now = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)
    assert jst_day(now) == date(2026, 10, 4)


def test_backfill_days_excludes_today_in_ascending_order():
    """遡及対象は当日を含まない直近k日(昇順・KpiJobの消費契約)。"""
    assert backfill_days(date(2026, 10, 4), 4) == [
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 3),
    ]


def test_week_of_is_monday_and_week_end_is_sunday():
    assert week_of(date(2026, 10, 4)) == date(2026, 9, 28)  # 10-04は日曜・前の月曜
    assert week_of(date(2026, 9, 30)) == date(2026, 9, 28)  # 水曜は同週の月曜
    assert week_end_of(date(2026, 9, 28)) == date(2026, 10, 4)
    assert is_week_end(date(2026, 10, 4))
    assert not is_week_end(date(2026, 10, 3))


def test_new_muted_users_excludes_already_muted():
    """週内に初めてmutedになったユーザーのみ(週開始前の集合を除外)。"""
    week_rows = {
        date(2026, 9, 28): {"u1", "u2"},
        date(2026, 9, 29): {"u2", "u3"},
    }
    got = new_muted_users(week_rows, date(2026, 9, 28), date(2026, 9, 29))
    assert got == {"u1", "u2", "u3"}  # 週開始前にmutedのユーザーは無い(初週近似)


def test_new_muted_users_with_prior_snapshot():
    """週開始前に既にmutedのユーザーは新規に数えない(09 §2.2の定義)。"""
    week_rows = {
        date(2026, 9, 28): {"u1", "u2"},  # 週月曜のsnapshot
        date(2026, 9, 29): {"u2", "u3"},  # 火曜
    }
    prior = {"u1"}  # 月曜以前からmuted
    got = new_muted_users(week_rows, date(2026, 9, 28), date(2026, 9, 29), prior=prior)
    assert got == {"u2", "u3"}


def test_retention_due_requires_observation_window():
    """観測窓: D+windowが昨日までに到達していないと算出しない(design §4)。

    境界値は「D+window=昨日」でTrue・「D+window=今日」でFalse(計画書の
    期待値表は日付が1日ズレていたためコメントと整合する日付へ補正)。
    """
    today = date(2026, 10, 4)
    assert retention_due(date(2026, 9, 26), today, 7)  # D+7=昨日
    assert not retention_due(date(2026, 9, 27), today, 7)  # D+7=今日(未完了)
    assert retention_due(date(2026, 9, 3), today, 30)  # D+30=昨日
    assert not retention_due(date(2026, 9, 4), today, 30)  # D+30=今日(未完了)


def test_yes_rate_from_responses_counts_only_today():
    """当日回答のみ・yes/numと全回答/den(yes+no+defer)。"""
    day = date(2026, 10, 4)
    responses = [
        {"value": "yes", "answered_at": "2026-10-04T12:00:00+09:00"},
        {"value": "no", "answered_at": "2026-10-04T13:00:00+09:00"},
        {"value": "defer", "answered_at": "2026-10-03T13:00:00+09:00"},  # 前日
        {"value": "yes", "answered_at": "2026-10-04T23:30:00+09:00"},
        {},  # 未回答行(answered_atなし)
    ]
    num, den = yes_rate_from_responses(responses, day)
    assert num == 2
    assert den == 3


def test_jst_constant_matches_clock_module():
    """JST定数はcore/clock.pyの単一真実を参照(C2・二重定義しない)。"""
    assert JST.utcoffset(None).total_seconds() == 9 * 3600
