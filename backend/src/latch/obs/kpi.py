"""KPI/MLR日次集計の定義と計算(09 §2.1〜§2.2・design §2.3/§2.8)。

本モジュールは2層に分かれる(§2.1読み替え6):
1. 純関数層(このファイルの上側) — unit試験の期待値表が所有
2. SQL実行層(KpiJob・下側) — integrationとstaging受け入れが所有
metric_keyの一覧は§2.3の定義表(09 §2.2の14指標+retention 2種)。
kpi_dailyは比率を保存せず分子/分母のみを保持する(09 §2.1どおり)。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from latch.core.clock import JST

METRIC_KEYS = frozenset(
    {
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
)

DAILY_METRIC_KEYS = frozenset(
    {
        "intent_create_rate",
        "active_intent_per_dau",
        "candidate_generation_rate",
        "proposal_rate",
        "hold_expired_rate",
        "yes_rate",
        "mlr",
        "jev_executions_per_intent",
    }
)

# JST日付式(全SQL共通・Postgresのtimestamptz→JST date変換)
JST_DAY = "((%s) AT TIME ZONE 'Asia/Tokyo')::date"


def jst_day(now: datetime) -> date:
    return now.astimezone(JST).date()


def backfill_days(today: date, k: int) -> list[date]:
    """遡及対象日(当日を含まない直近k日・昇順)。D-09の3日期限+マージン。"""
    return [today - timedelta(days=i) for i in range(k, 0, -1)]


def week_of(day: date) -> date:
    """その日を含む週の月曜(JST月曜始まり)。"""
    return day - timedelta(days=day.weekday())


def week_end_of(week_start: date) -> date:
    return week_start + timedelta(days=6)


def is_week_end(day: date) -> bool:
    return day.weekday() == 6  # 日曜(月曜始まりの週末)


def new_muted_users(
    week_rows: dict[date, set],
    week_start: date,
    week_end: date,
    prior: set | None = None,
) -> set:
    """週内snapshotに現れ・週開始前に現れないユーザー集合(§2.3 mute_rate分子)。

    prior=週開始前の全snapshotユーザー集合(None=初週近似・全員新規扱い)。
    往復変動(解除→再muted)は日次境界でしか観測しない近似(design §2.8告白)。
    """
    in_week: set = set()
    for day, users in week_rows.items():
        if week_start <= day <= week_end:
            in_week |= users
    if prior is None:
        return in_week
    return in_week - prior


def retention_due(base_day: date, today: date, window_days: int) -> bool:
    """観測窓: D+windowが昨日までに到達していないと算出しない(design §4)。"""
    return today - timedelta(days=1) >= base_day + timedelta(days=window_days)


def yes_rate_from_responses(responses: list[dict], day: date) -> tuple[int, int]:
    """当日回答の(yes数, 回答総数)。jsonb配列の要素辞書を純Pythonで数える。"""
    num = 0
    den = 0
    for r in responses:
        answered_at = r.get("answered_at")
        if not answered_at:
            continue
        if jst_day(datetime.fromisoformat(answered_at)) != day:
            continue
        value = r.get("value")
        if value in ("yes", "no", "defer"):
            den += 1
            if value == "yes":
                num += 1
    return num, den
