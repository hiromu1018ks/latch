"""KPI/MLR日次集計の定義と計算(09 §2.1〜§2.2・design §2.3/§2.8)。

本モジュールは2層に分かれる(§2.1読み替え6):
1. 純関数層(このファイルの上側) — unit試験の期待値表が所有
2. SQL実行層(KpiJob・下側) — integrationとstaging受け入れが所有
metric_keyの一覧は§2.3の定義表(09 §2.2の14指標+retention 2種)。
kpi_dailyは比率を保存せず分子/分母のみを保持する(09 §2.1どおり)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import JST, Clock

logger = logging.getLogger(__name__)

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
        "report_rate",
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


# -- SQL実行層(下側) --

# kpi_dailyにmlr行の無い遡及対象日(pending)
SQL_PENDING_DAYS = text("""
    SELECT d::date FROM generate_series(
        CAST(:start_day AS date), CAST(:end_day AS date), interval '1 day'
    ) d
    WHERE NOT EXISTS (
        SELECT 1 FROM kpi_daily WHERE metric_key = 'mlr' AND day = d::date
    )
    ORDER BY d::date
""")

# 1日の全日次指標を1SQLでUPSERT(分子/分母の2列×8指標をサブクエリで集約)
SQL_UPSERT_DAILY = text(f"""
    INSERT INTO kpi_daily (metric_key, day, numerator, denominator,
                           created_at, updated_at)
    SELECT m.metric_key, CAST(:day AS date), m.num, m.den,
           CAST(:now AS timestamptz), CAST(:now AS timestamptz)
    FROM (VALUES
        ('mlr',
         (SELECT count(*) FROM latch_status_events
          WHERE to_status='matched'
            AND {JST_DAY % "created_at"} = CAST(:day AS date)),
         (SELECT count(*) FROM latch_status_events
          WHERE to_status='proposed'
            AND {JST_DAY % "created_at"} = CAST(:day AS date))),
        ('hold_expired_rate',
         (SELECT count(*) FROM latch_status_events
          WHERE from_status='candidate' AND to_status='expired'
            AND {JST_DAY % "created_at"} = CAST(:day AS date)),
         (SELECT count(*) FROM latch_status_events
          WHERE to_status='candidate'
            AND {JST_DAY % "created_at"} = CAST(:day AS date))),
        ('yes_rate',
         (SELECT count(*) FROM latches l, jsonb_array_elements(l.responses) r
          WHERE r->>'value'='yes'
            AND {JST_DAY % "(r->>'answered_at')::timestamptz"} = CAST(:day AS date)),
         (SELECT count(*) FROM latches l, jsonb_array_elements(l.responses) r
          WHERE r->>'value' IN ('yes','no','defer')
            AND {JST_DAY % "(r->>'answered_at')::timestamptz"} = CAST(:day AS date))),
        ('intent_create_rate',
         (SELECT count(DISTINCT i.user_id)
          FROM match_events e JOIN intents i ON i.id=e.source_intent_id
          WHERE e.event_type='created'
            AND {JST_DAY % "e.created_at"} = CAST(:day AS date)),
         (SELECT count(DISTINCT s.user_id) FROM sessions s
          WHERE {JST_DAY % "s.created_at"} = CAST(:day AS date))),
        ('active_intent_per_dau',
         (SELECT count(*) FROM intents WHERE status='active'),
         (SELECT count(DISTINCT s.user_id) FROM sessions s
          WHERE {JST_DAY % "s.created_at"} = CAST(:day AS date))),
        ('candidate_generation_rate',
         (SELECT count(*) FROM intents i WHERE i.status='active' AND EXISTS (
              SELECT 1 FROM match_candidates mc
              WHERE mc.intent_a_id=i.id OR mc.intent_b_id=i.id)),
         (SELECT count(*) FROM intents WHERE status='active')),
        ('proposal_rate',
         (SELECT COUNT(DISTINCT iid) FROM latch_status_events e
            JOIN latches l ON l.id=e.latch_id
            CROSS JOIN LATERAL unnest(l.intent_ids) AS iid
          WHERE e.to_status='proposed'
            AND {JST_DAY % "e.created_at"} = CAST(:day AS date)),
         (SELECT count(*) FROM intents WHERE status='active')),
        ('jev_executions_per_intent',
         (SELECT count(*) FROM match_candidates mc
          WHERE mc.jev_result IS NOT NULL
            AND {JST_DAY % "mc.created_at"} = CAST(:day AS date)),
         (SELECT COUNT(DISTINCT e.source_intent_id) FROM match_events e
          WHERE e.event_type IN ('created','updated')
            AND {JST_DAY % "e.created_at"} = CAST(:day AS date)))
    ) AS m(metric_key, num, den)
    ON CONFLICT (metric_key, day) DO UPDATE SET
        numerator = EXCLUDED.numerator,
        denominator = EXCLUDED.denominator,
        updated_at = CAST(:now AS timestamptz)
""")

SQL_UPSERT_MUTED_SNAPSHOT = text("""
    INSERT INTO kpi_muted_user_snapshot (day, user_id)
    SELECT CAST(:day AS date), i.user_id FROM intents i
    WHERE i.notification_level='muted'
    ON CONFLICT (day, user_id) DO NOTHING
""")

SQL_UPSERT_RETENTION = text(f"""
    INSERT INTO kpi_daily (metric_key, day, numerator, denominator,
                           created_at, updated_at)
    SELECT m.metric_key, CAST(:base_day AS date), m.num, m.den,
           CAST(:now AS timestamptz), CAST(:now AS timestamptz)
    FROM (VALUES
        (:metric,
         (SELECT count(DISTINCT s.user_id)
          FROM sessions s JOIN users u ON u.id=s.user_id
          WHERE {JST_DAY % "u.created_at"} = CAST(:base_day AS date)
            AND {JST_DAY % "s.created_at"} > CAST(:base_day AS date)
            AND {JST_DAY % "s.created_at"} <= CAST(:base_day AS date)
                                        + CAST(:window AS int)),
         (SELECT count(*) FROM users u
          WHERE {JST_DAY % "u.created_at"} = CAST(:base_day AS date)))
    ) AS m(metric_key, num, den)
    ON CONFLICT (metric_key, day) DO UPDATE SET
        numerator = EXCLUDED.numerator,
        denominator = EXCLUDED.denominator,
        updated_at = CAST(:now AS timestamptz)
""")

SQL_UPSERT_WEEKLY = text(f"""
    INSERT INTO kpi_daily (metric_key, day, numerator, denominator,
                           created_at, updated_at)
    SELECT m.metric_key, CAST(:week_start AS date), m.num, m.den,
           CAST(:now AS timestamptz), CAST(:now AS timestamptz)
    FROM (VALUES
        ('intent_recreation_rate',
         (SELECT count(*) FROM (
            SELECT i.user_id
            FROM match_events e JOIN intents i ON i.id=e.source_intent_id
            WHERE e.event_type='created'
              AND {JST_DAY % "e.created_at"} BETWEEN CAST(:week_start AS date)
                                                AND CAST(:week_end AS date)
            GROUP BY i.user_id
            HAVING COUNT(DISTINCT e.source_intent_id) >= 2) t),
         (SELECT count(DISTINCT i.user_id)
          FROM match_events e JOIN intents i ON i.id=e.source_intent_id
          WHERE e.event_type='created'
            AND {JST_DAY % "e.created_at"} BETWEEN CAST(:week_start AS date)
                                              AND CAST(:week_end AS date))),
        ('attendance_rate',
         (SELECT count(*) FROM calibration_records
          WHERE matched AND actual_attended IS TRUE
            AND {JST_DAY % "created_at"} BETWEEN CAST(:week_start AS date)
                                               AND CAST(:week_end AS date)),
         (SELECT count(*) FROM calibration_records
          WHERE matched AND actual_attended IS NOT NULL
            AND {JST_DAY % "created_at"} BETWEEN CAST(:week_start AS date)
                                               AND CAST(:week_end AS date))),
        ('cancel_rate',
         (SELECT count(*) FROM calibration_records
          WHERE matched AND cancelled_after IS TRUE
            AND {JST_DAY % "created_at"} BETWEEN CAST(:week_start AS date)
                                               AND CAST(:week_end AS date)),
         (SELECT count(*) FROM calibration_records
          WHERE matched AND actual_attended IS NOT NULL
            AND {JST_DAY % "created_at"} BETWEEN CAST(:week_start AS date)
                                               AND CAST(:week_end AS date))),
        ('no_answer_rate',
         (SELECT count(*) FROM calibration_records
          WHERE matched AND actual_attended IS NULL
            AND created_at < CAST(:now AS timestamptz) - interval '3 days'
            AND {JST_DAY % "created_at"} BETWEEN CAST(:week_start AS date)
                                               AND CAST(:week_end AS date)),
         (SELECT count(*) FROM calibration_records WHERE matched
            AND {JST_DAY % "created_at"} BETWEEN CAST(:week_start AS date)
                                               AND CAST(:week_end AS date))),
        ('mute_rate',
         (SELECT count(*) FROM (
            SELECT DISTINCT user_id FROM kpi_muted_user_snapshot
            WHERE day BETWEEN CAST(:week_start AS date)
                            AND CAST(:week_end AS date)
            EXCEPT
            SELECT DISTINCT user_id FROM kpi_muted_user_snapshot
            WHERE day < CAST(:week_start AS date)) t),
         (SELECT count(DISTINCT user_id) FROM sessions
          WHERE {JST_DAY % "created_at"} BETWEEN CAST(:week_start AS date)
                                               AND CAST(:week_end AS date))),
        ('report_rate',
         (SELECT count(*) FROM reports
          WHERE {JST_DAY % "created_at"} BETWEEN CAST(:week_start AS date)
                                              AND CAST(:week_end AS date)),
         (SELECT count(*) FROM latch_status_events
          WHERE to_status='proposed'
            AND {JST_DAY % "created_at"} BETWEEN CAST(:week_start AS date)
                                              AND CAST(:week_end AS date)))
    ) AS m(metric_key, num, den)
    ON CONFLICT (metric_key, day) DO UPDATE SET
        numerator = EXCLUDED.numerator,
        denominator = EXCLUDED.denominator,
        updated_at = CAST(:now AS timestamptz)
""")

SQL_WEEKLY_DONE = text("""
    SELECT 1 FROM kpi_daily
    WHERE metric_key='intent_recreation_rate' AND day = CAST(:week_start AS date)
""")

SQL_SNAPSHOT_HAS_DAY = text("""
    SELECT 1 FROM kpi_muted_user_snapshot WHERE day = CAST(:day AS date) LIMIT 1
""")


class KpiJob:
    """JST日付境界を60秒tickで検査し、未集計の完了日を遡及UPSERTする。

    ResetJobと違い「次の0時までの実時間sleep」を持たない(stagingのClock
    操作で発火確認できるようtick方式 — §2.1読み替え2)。境界の真実は
    kpi_daily自体(代表キーmlr行の存在)であり、Redisも追加テーブルも
    不要。起動時も即tick(遡及窓が空なら自己修復する)。
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        backfill_days: int = 4,
        interval_sec: int = 60,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._backfill_days = backfill_days
        self._interval_sec = interval_sec
        self._sleep = sleep

    def _pending_day_list(self, today: date) -> list[date]:
        return backfill_days(today, self._backfill_days)

    async def _pending_days(self) -> list[date]:
        today = self._clock.jst_date()
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    SQL_PENDING_DAYS,
                    {
                        "start_day": today - timedelta(days=self._backfill_days),
                        "end_day": today - timedelta(days=1),
                    },
                )
            ).all()
        return [r[0] for r in rows]

    async def run_once(self) -> None:
        """tick本体: 未集計日の遡及UPSERT→(付随集計)muted snapshot・retention。"""
        now = self._clock.now()
        pending = await self._pending_days()
        if not pending:
            return
        for day in pending:
            async with self._engine.begin() as conn:
                await conn.execute(SQL_UPSERT_DAILY, {"day": day, "now": now})
        today = self._clock.jst_date()
        yesterday = today - timedelta(days=1)
        async with self._engine.begin() as conn:
            # muted snapshot(当日集合・冪等。0時直後の集合=前日末近似)
            await conn.execute(SQL_UPSERT_MUTED_SNAPSHOT, {"day": today})
            # retention(観測窓が閉じているときのみ書く)
            for base, metric, window in (
                (yesterday - timedelta(days=7), "retention_7d", 7),
                (yesterday - timedelta(days=30), "retention_30d", 30),
            ):
                if retention_due(base, today, window):
                    await conn.execute(
                        SQL_UPSERT_RETENTION,
                        {
                            "base_day": base,
                            "metric": metric,
                            "window": window,
                            "now": now,
                        },
                    )
            # 週次(昨日が日曜=週の終わり)
            if is_week_end(yesterday):
                week_start = week_of(yesterday)
                await conn.execute(
                    SQL_UPSERT_WEEKLY,
                    {"week_start": week_start, "week_end": yesterday, "now": now},
                )

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """起動時即tick→interval待ち→tick→…(run_once-first)。例外は握って
        warning+次tick(ReevalRunnerと同一契約 — 集計失敗でworkerを落とさない)。
        待機はRetentionJob._waitと同一契約(stop即応・注入sleepでunitが決定的)。"""
        while stop is None or not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.warning("kpi run_once failed", exc_info=True)
            await self._wait_interval(stop)

    async def _wait_interval(self, stop: asyncio.Event | None) -> None:
        """interval待ち(stop即応・RetentionJob._waitと同一契約)。"""
        if stop is None:
            await self._sleep(self._interval_sec)
            return
        sleep_task = asyncio.create_task(self._sleep(self._interval_sec))
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
