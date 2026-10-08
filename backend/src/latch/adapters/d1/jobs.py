"""D1版・jobs/outbox配送と回収(T5・計画§4)。

- dispatch_job: outboxの原子INSERT(0001の_outbox_stmtと併用)から
  queue投入相当へ。event_id UNIQUEが再配送の二重job化を防ぐ。
- claim: 期限到来の仕事をlease付きで確保(fencing tokenはjobs列)。
- 回収: lease切れのrunningをretryableへ戻す(上限付き)。
- 業務回数guard(D-16・Jev予算)は usage_counters の原子的予約で実装
  (並行jobが同じ残枠を二重に消費しない)。
"""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import micros

_JOB_KINDS = ("evaluation", "notify", "retention", "deletion")


@dataclass(frozen=True)
class JobClaim:
    job_id: str
    kind: str
    ref_id: str | None
    fencing_token: int
    attempts: int


class D1JobCoordinator:
    """jobs/outboxの配送・claim・回収(§4)。"""

    def __init__(self, db: LocalD1Database, *, lease_seconds: float = 90.0):
        self.db = db
        self.lease_us = int(lease_seconds * 1_000_000)

    async def enqueue(
        self,
        *,
        kind: str,
        ref_id: str | None,
        now,
        due_at=None,
        event_id: str | None = None,
    ) -> str:
        """job新規登録(event_id指定時はoutbox同期列へも書く)。"""
        if kind not in _JOB_KINDS:
            raise ValueError(f"unknown job kind: {kind}")
        job_id = str(uuid_mod.uuid4())
        now_us = micros(now)
        stmts = [
            self.db.prepare(
                "INSERT INTO jobs (id, kind, ref_id, status, attempts,"
                " next_due_at, created_at, updated_at)"
                " VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)"
            ).bind(job_id, kind, ref_id, micros(due_at or now), now_us, now_us),
        ]
        if event_id is not None:
            stmts.append(
                self.db.prepare(
                    "INSERT OR IGNORE INTO outbox"
                    " (id, event_id, intent_id, intent_version, reason,"
                    "  due_generation, writer_generation,"
                    "  payload_schema_version, status, created_at)"
                    " VALUES (?, ?, NULL, NULL, ?, 1, 1, 1, 'sent', ?)"
                ).bind(str(uuid_mod.uuid4()), event_id, kind, now_us),
            )
        await self.db.batch(stmts)
        return job_id

    async def claim_due(self, now, *, owner: str, limit: int = 10) -> list[JobClaim]:
        """期限到来のpendingをlease付きで確保(条件付きUPDATEが仲裁)。"""
        now_us = micros(now)
        rows = (
            await self.db.prepare(
                "SELECT id, kind, ref_id, fencing_token, attempts FROM jobs"
                " WHERE status = 'pending' AND next_due_at <= ?"
                " ORDER BY next_due_at ASC, id ASC LIMIT ?"
            )
            .bind(now_us, limit)
            .all()
        )
        claims: list[JobClaim] = []
        for r in rows:
            res = (
                await self.db.prepare(
                    "UPDATE jobs SET status = 'running', lease_owner = ?,"
                    " lease_expires_at = ?, fencing_token = fencing_token + 1,"
                    " attempts = attempts + 1, updated_at = ?"
                    " WHERE id = ? AND status = 'pending'"
                )
                .bind(owner, now_us + self.lease_us, now_us, r["id"])
                .run()
            )
            if res.changes == 1:
                claims.append(
                    JobClaim(
                        job_id=r["id"],
                        kind=r["kind"],
                        ref_id=r["ref_id"],
                        fencing_token=int(r["fencing_token"]) + 1,
                        attempts=int(r["attempts"]) + 1,
                    )
                )
        return claims

    async def complete(self, claim: JobClaim, now) -> bool:
        """成功確定(古いfencing tokenならFalse=破棄)。"""
        res = (
            await self.db.prepare(
                "UPDATE jobs SET status = 'succeeded', lease_owner = NULL,"
                " lease_expires_at = NULL, updated_at = ?"
                " WHERE id = ? AND status = 'running' AND fencing_token = ?"
            )
            .bind(micros(now), claim.job_id, claim.fencing_token)
            .run()
        )
        return res.changes == 1

    async def retry_later(self, claim: JobClaim, *, delay_seconds: float, now) -> bool:
        res = (
            await self.db.prepare(
                "UPDATE jobs SET status = 'retryable', lease_owner = NULL,"
                " lease_expires_at = NULL, next_due_at = ?, updated_at = ?"
                " WHERE id = ? AND status = 'running' AND fencing_token = ?"
            )
            .bind(
                micros(now) + int(delay_seconds * 1_000_000),
                micros(now),
                claim.job_id,
                claim.fencing_token,
            )
            .run()
        )
        if res.changes == 0:
            return False
        await (
            self.db.prepare(
                "UPDATE jobs SET status = 'pending' WHERE id = ?"
                " AND status = 'retryable'"
            )
            .bind(claim.job_id)
            .run()
        )
        return True

    async def mark_unknown(self, claim: JobClaim, now) -> bool:
        """LLM送信後timeout等: 結果不明(課金された可能性)として記録。"""
        res = (
            await self.db.prepare(
                "UPDATE jobs SET status = 'outcome_unknown', lease_owner = NULL,"
                " last_error = 'outcome_unknown', updated_at = ?"
                " WHERE id = ? AND status = 'running' AND fencing_token = ?"
            )
            .bind(micros(now), claim.job_id, claim.fencing_token)
            .run()
        )
        return res.changes == 1

    async def recover_expired_leases(self, now, *, max_attempts: int = 5) -> int:
        """lease切れのrunningを回収(上限内はpending・超過はfailed)。"""
        now_us = micros(now)
        res = (
            await self.db.prepare(
                "UPDATE jobs SET status = 'pending', lease_owner = NULL,"
                " lease_expires_at = NULL, next_due_at = ?, updated_at = ?"
                " WHERE status = 'running' AND lease_expires_at <= ?"
                " AND attempts < ?"
            )
            .bind(now_us, now_us, now_us, max_attempts)
            .run()
        )
        recovered = res.changes
        await (
            self.db.prepare(
                "UPDATE jobs SET status = 'failed', lease_owner = NULL,"
                " lease_expires_at = NULL, last_error = 'max_attempts',"
                " updated_at = ?"
                " WHERE status = 'running' AND lease_expires_at <= ?"
            )
            .bind(now_us, now_us)
            .run()
        )
        return recovered

    async def outbox_event_seen(self, event_id: str) -> bool:
        row = (
            await self.db.prepare("SELECT 1 AS x FROM outbox WHERE event_id = ?")
            .bind(event_id)
            .first()
        )
        return row is not None


class D1UsageGuard:
    """業務回数guardの原子的予約(usage_counters・Redis置換)。

    並行jobが同じ残枠を二重に消費しない: 予約は
    INSERT-or-UPDATE ... RETURNING を単一文で行う。
    """

    def __init__(self, db: LocalD1Database):
        self.db = db

    async def reserve(
        self, *, key: str, limit: int, window_start, window_expires, now
    ) -> bool:
        """上限内で1つ予約。True=予約成功・False=上限到達。"""
        start_us = micros(window_start)
        expires_us = micros(window_expires)
        await (
            self.db.prepare(
                "INSERT OR IGNORE INTO usage_counters"
                " (key, value, window_start, expires_at)"
                " VALUES (?, 0, ?, ?)"
            )
            .bind(key, start_us, expires_us)
            .run()
        )
        row = (
            await self.db.prepare(
                "UPDATE usage_counters SET value = value + 1"
                " WHERE key = ? AND value < ?"
                " RETURNING value"
            )
            .bind(key, limit)
            .first()
        )
        return row is not None

    async def remaining(self, key: str) -> int | None:
        row = (
            await self.db.prepare("SELECT value FROM usage_counters WHERE key = ?")
            .bind(key)
            .first()
        )
        return None if row is None else int(row["value"])
