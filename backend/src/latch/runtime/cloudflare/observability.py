"""Workers版・監視(T10・計画§5.6)。

規律:
- 業務KPIと課金集計は永続データから再計算できるようにする
  (Workersの一時process counterやサンプリングログを件数の正にしない)
- user・Intent IDを系列labelにしない
- 待ち時間ヒストグラムはbucket上限で打ち切らず実測値を持つ

実装: D1永続のmetrics表(series・labelなし・count・sum・buckets)へ
集計し、KPIは latch_status_events 等の実データから再計算する。
alert判定はscheduled実行でしきい値を切ったらalert行を残す
(実際に人が受け取る経路の接続は運用文書化 — 実配備後)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import JST, jst_day_start, micros

# 待ち時間histogram(秒境界 — 上限で打ち切らない)
LATENCY_BUCKETS_SEC = (1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600)


async def ensure_metrics_table(db: LocalD1Database) -> None:
    await db.batch(
        [
            db.prepare(
                "CREATE TABLE IF NOT EXISTS metrics_series ("
                " name TEXT NOT NULL, bucket_key TEXT NOT NULL,"
                " bucket INTEGER NOT NULL DEFAULT 0, count INTEGER NOT NULL,"
                " sum REAL NOT NULL DEFAULT 0, max REAL NOT NULL DEFAULT 0,"
                " updated_at INTEGER NOT NULL,"
                " PRIMARY KEY (name, bucket_key, bucket))"
            ),
        ]
    )


class ObservabilityCollector:
    """永続集計へ書くcollector(process counterは正にしない)。"""

    def __init__(self, db: LocalD1Database) -> None:
        self.db = db

    async def observe(self, name: str, value: float, now: datetime) -> None:
        await ensure_metrics_table(self.db)
        us = micros(now)
        # bucket_key=JST日(集計日)
        bucket_key = now.astimezone(JST).date().isoformat()
        bucket = _bucket_of(value)
        await self.db.batch(
            [
                self.db.prepare(
                    "INSERT INTO metrics_series"
                    " (name, bucket_key, bucket, count, sum, max, updated_at)"
                    " VALUES (?, ?, ?, 1, ?, ?, ?)"
                    " ON CONFLICT (name, bucket_key, bucket) DO UPDATE SET"
                    " count = count + 1, sum = sum + excluded.sum,"
                    " max = MAX(max, excluded.max),"
                    " updated_at = excluded.updated_at"
                ).bind(name, bucket_key, bucket, value, value, us),
            ]
        )

    async def inc(self, name: str, now: datetime, by: int = 1) -> None:
        await self.observe(name, float(by), now)

    async def p95(self, name: str, *, bucket_key: str) -> float | None:
        rows = (
            await self.db.prepare(
                "SELECT bucket, count FROM metrics_series"
                " WHERE name = ? AND bucket_key = ? ORDER BY bucket ASC"
            )
            .bind(name, bucket_key)
            .all()
        )
        total = sum(r["count"] for r in rows)
        if total == 0:
            return None
        threshold = total * 0.95
        acc = 0
        for r in rows:
            acc += r["count"]
            if acc >= threshold:
                return float(r["bucket"])
        return None


def _bucket_of(value: float) -> int:
    for b in LATENCY_BUCKETS_SEC:
        if value <= b:
            return b
    return LATENCY_BUCKETS_SEC[-1] * 10  # 上限を超える値も保持(打ち切らない)


@dataclass(frozen=True)
class DailyKpi:
    jst_date: str
    proposals: int
    matched: int
    rejected: int
    expired: int
    completed: int


async def recompute_daily_kpi(db: LocalD1Database, now: datetime) -> DailyKpi:
    """業務KPIをlatch_status_events(永続データ)から再計算。

    分母・定義は現行KPI(PostgreSQL版)と同一(to_status件数・JST集計日)。
    """
    day = now.astimezone(JST).date()
    start_us = micros(jst_day_start(day))
    end_us = start_us + 24 * 3600 * 1_000_000
    rows = (
        await db.prepare(
            "SELECT to_status, COUNT(*) AS c FROM latch_status_events"
            " WHERE created_at >= ? AND created_at < ? GROUP BY to_status"
        )
        .bind(start_us, end_us)
        .all()
    )
    by = {r["to_status"]: r["c"] for r in rows}
    return DailyKpi(
        jst_date=day.isoformat(),
        proposals=by.get("proposed", 0),
        matched=by.get("matched", 0),
        rejected=by.get("rejected", 0),
        expired=by.get("expired", 0),
        completed=by.get("completed", 0),
    )


async def check_alerts(
    db: LocalD1Database, now: datetime, *, oldest_pending_limit_sec: int = 300
) -> list[dict]:
    """alert判定(しきい値超過でalert行を残す・通知経路は運用接続)。"""
    now_us = micros(now)
    alerts: list[dict] = []
    oldest = await db.prepare(
        "SELECT MIN(created_at) AS oldest FROM outbox WHERE status = 'pending'"
    ).first()
    if oldest is not None and oldest["oldest"] is not None:
        age_s = (now_us - oldest["oldest"]) / 1_000_000
        if age_s > oldest_pending_limit_sec:
            alerts.append({"kind": "outbox_oldest_pending", "age_seconds": age_s})
    unknown = await db.prepare(
        "SELECT COUNT(*) AS c FROM jobs WHERE status = 'outcome_unknown'"
    ).first()
    if unknown["c"] > 0:
        alerts.append({"kind": "outcome_unknown_jobs", "count": unknown["c"]})
    for alert in alerts:
        await db.batch(
            [
                db.prepare(
                    "CREATE TABLE IF NOT EXISTS alerts ("
                    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " kind TEXT NOT NULL, detail TEXT NOT NULL,"
                    " created_at INTEGER NOT NULL, resolved_at INTEGER)"
                ),
                db.prepare(
                    "INSERT INTO alerts (kind, detail, created_at) VALUES (?, ?, ?)"
                ).bind(alert["kind"], str(alert), now_us),
            ]
        )
    return alerts
