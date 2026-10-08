"""D1版・再評価スケジュール(T8・計画§5.4・I3)。

- request_evaluation: 仕事はgenerationを進めて残す(抑制中も消さない)。
  初期更新・ユーザー更新はdue_at=nowで即時。時間条件は将来時刻。
- claim_due: due到達をindexから上限付きbatch・cursorで公平に取得。
  Cronの遅延・停止後もdueの仕事は回収される(遅れ分も一括で取れる)。
- complete_generation: claim後の更新(新しいgeneration)を消さない
  条件的更新。次回必要時刻は compute_next_due で決める。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import JST, jst_day_start, micros

BUCKET_MINUTES = 30  # 06 §9(30分Bucket — 旧構成と同一)
PRE_EXPIRY_WINDOW = timedelta(hours=2)  # 期限2時間前catch-up(旧構成と同一)


@dataclass(frozen=True)
class ScheduleClaim:
    intent_id: str
    requested_revision: int
    requested_generation: int
    dirty_reason: str
    fencing_token: int
    next_evaluate_at: int = 0  # cursor継続用(claim時点のdue時刻)


def compute_next_due(
    now: datetime,
    *,
    time_start: datetime | None,
    expires_at: datetime | None,
    defer_until: datetime | None = None,
    daily_cap_release: datetime | None = None,
) -> datetime | None:
    """次回必要時刻 = 候補のうち最も近い将来時刻(§5.4)。

    候補:
    - 30分bucketの次の境界(評価機会を定期的に残す)
    - time_start - 2時間(期限前catch-up)
    - defer_until(D-07再提案抑制の解除)
    - daily_cap_release(通知日次上限の解除=JST 0時)
    expires_atを過ぎている場合はNone(評価機会を残さない)。
    """
    if expires_at is not None and expires_at <= now:
        return None
    candidates: list[datetime] = [_bucket_after(now, BUCKET_MINUTES)]
    if time_start is not None:
        candidates.append(time_start - PRE_EXPIRY_WINDOW)
    if defer_until is not None:
        candidates.append(defer_until)
    if daily_cap_release is not None:
        candidates.append(daily_cap_release)
    future = [c for c in candidates if c > now]
    return min(future) if future else None


def _bucket_after(now: datetime, minutes: int) -> datetime:
    """現在を含むbucketの次の境界(旧reevalと同一の30分区切り)。"""
    epoch = int(now.timestamp() * 1_000_000)
    bucket_us = minutes * 60 * 1_000_000
    next_us = (epoch // bucket_us + 1) * bucket_us
    return datetime.fromtimestamp(next_us / 1_000_000, tz=now.tzinfo)


def jst_next_midnight(now: datetime) -> datetime:
    """次のJST 0時(通知日次上限の解除時刻)。"""
    jst_today = now.astimezone(JST).date()
    return jst_day_start(jst_today) + timedelta(days=1)


class D1ScheduleRepository:
    """ports.schedule.ScheduleRepository のD1実装。"""

    def __init__(self, db: LocalD1Database, *, lease_seconds: float = 120.0):
        self.db = db
        self.lease_us = int(lease_seconds * 1_000_000)

    async def request_evaluation(
        self, intent_id: str, revision: int, reason: str, due_at: datetime
    ) -> int:
        """要求を記録しgenerationを進める。戻り値=新しいgeneration。"""
        now_us = micros(due_at)
        row = (
            await self.db.prepare(
                "SELECT requested_generation FROM intent_evaluation_schedule"
                " WHERE intent_id = ?"
            )
            .bind(intent_id)
            .first()
        )
        if row is None:
            generation = 1
            await self.db.batch(
                [
                    self.db.prepare(
                        "INSERT INTO intent_evaluation_schedule"
                        " (intent_id, requested_revision, requested_generation,"
                        "  evaluated_generation, dirty_reason, next_evaluate_at,"
                        "  status, created_at, updated_at)"
                        " VALUES (?, ?, ?, NULL, ?, ?, 'due', ?, ?)"
                    ).bind(
                        intent_id,
                        revision,
                        generation,
                        reason,
                        now_us,
                        now_us,
                        now_us,
                    ),
                ]
            )
            return generation
        generation = int(row["requested_generation"]) + 1
        # 新しい要求は既存のdueより早い時刻へ引き寄せる(後ろへ動かさない)
        await self.db.batch(
            [
                self.db.prepare(
                    "UPDATE intent_evaluation_schedule"
                    " SET requested_revision = ?, requested_generation = ?,"
                    " dirty_reason = ?,"
                    " next_evaluate_at = MIN(next_evaluate_at, ?),"
                    " status = 'due', updated_at = ?"
                    " WHERE intent_id = ?"
                ).bind(revision, generation, reason, now_us, now_us, intent_id),
            ]
        )
        return generation

    async def mark_paused(self, intent_id: str, paused: bool) -> None:
        """pause中はdue照会から外す(resumeで即時dueへ戻す)。"""
        if paused:
            await self.db.batch(
                [
                    self.db.prepare(
                        "UPDATE intent_evaluation_schedule SET status = 'paused',"
                        " updated_at = (SELECT COALESCE(MAX(updated_at), 0)"
                        "   FROM intent_evaluation_schedule) WHERE intent_id = ?"
                    ).bind(intent_id),
                ]
            )
        else:
            await self.db.batch(
                [
                    self.db.prepare(
                        "UPDATE intent_evaluation_schedule SET status = 'due',"
                        " next_evaluate_at = 0, updated_at ="
                        " (SELECT COALESCE(MAX(updated_at), 0)"
                        "   FROM intent_evaluation_schedule)"
                        " WHERE intent_id = ?"
                    ).bind(intent_id),
                ]
            )

    @staticmethod
    def _cursor_of(claim: ScheduleClaim) -> tuple[int, str]:
        return (claim.next_evaluate_at, claim.intent_id)

    async def claim_due(
        self, now: datetime, limit: int, cursor: tuple[int, str] | None = None
    ) -> list[ScheduleClaim]:
        """due到達の仕事を公平にclaim(cursorは(next_evaluate_at, intent_id))。"""
        now_us = micros(now)
        if cursor is None:
            rows = (
                await self.db.prepare(
                    "SELECT intent_id, requested_revision, requested_generation,"
                    " dirty_reason, fencing_token, next_evaluate_at"
                    " FROM intent_evaluation_schedule"
                    " WHERE status = 'due' AND next_evaluate_at <= ?"
                    " ORDER BY next_evaluate_at ASC, intent_id ASC LIMIT ?"
                )
                .bind(now_us, limit)
                .all()
            )
        else:
            rows = (
                await self.db.prepare(
                    "SELECT intent_id, requested_revision, requested_generation,"
                    " dirty_reason, fencing_token, next_evaluate_at"
                    " FROM intent_evaluation_schedule"
                    " WHERE status = 'due' AND next_evaluate_at <= ?"
                    " AND (next_evaluate_at, intent_id) > (?, ?)"
                    " ORDER BY next_evaluate_at ASC, intent_id ASC LIMIT ?"
                )
                .bind(now_us, cursor[0], cursor[1], limit)
                .all()
            )
        claims: list[ScheduleClaim] = []
        for r in rows:
            res = (
                await self.db.prepare(
                    "UPDATE intent_evaluation_schedule"
                    " SET status = 'claimed', lease_owner = 'scheduler',"
                    " lease_expires_at = ?, fencing_token = fencing_token + 1,"
                    " updated_at = ?"
                    " WHERE intent_id = ? AND status = 'due'"
                )
                .bind(now_us + self.lease_us, now_us, r["intent_id"])
                .run()
            )
            if res.changes == 1:
                claims.append(
                    ScheduleClaim(
                        intent_id=r["intent_id"],
                        requested_revision=r["requested_revision"],
                        requested_generation=r["requested_generation"],
                        dirty_reason=r["dirty_reason"],
                        fencing_token=int(r["fencing_token"]) + 1,
                        next_evaluate_at=int(r["next_evaluate_at"])
                        if "next_evaluate_at" in r.keys()
                        else 0,
                    )
                )
        return claims

    async def complete_generation(
        self,
        claim: ScheduleClaim,
        result: dict[str, Any],
        next_due_at: datetime | None,
        now: datetime,
    ) -> bool:
        """完了の条件付き更新(claim後の更新を消さない)。

        - claim後により新しいgenerationが来た場合(False): 評価済みに
          しない・next_dueも動かさない(新しい世代がdueのまま残る)
        - next_due_at=None(期限切れ等): 評価済みとし次回を置かない
        """
        now_us = micros(now)
        due_us = micros(next_due_at) if next_due_at is not None else None
        res = (
            await self.db.prepare(
                "UPDATE intent_evaluation_schedule"
                " SET evaluated_generation = ?, status = 'due',"
                " next_evaluate_at = COALESCE(?, 9223372036854775807),"
                " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE intent_id = ? AND requested_generation = ?"
                " AND fencing_token = ? AND status = 'claimed'"
            )
            .bind(
                claim.requested_generation,
                due_us,
                now_us,
                claim.intent_id,
                claim.requested_generation,
                claim.fencing_token,
            )
            .run()
        )
        if res.changes == 1 and next_due_at is None:
            # 次回なし: due照会の対象外へ(next_evaluate_atを最大値に置いた)
            pass
        return res.changes == 1

    async def recover_expired_leases(self, now: datetime) -> int:
        """lease切れのclaimedをdueへ戻す(scheduler停止後の回収)。"""
        now_us = micros(now)
        res = (
            await self.db.prepare(
                "UPDATE intent_evaluation_schedule SET status = 'due',"
                " lease_owner = NULL, lease_expires_at = NULL,"
                " next_evaluate_at = ?, updated_at = ?"
                " WHERE status = 'claimed' AND lease_expires_at <= ?"
            )
            .bind(now_us, now_us, now_us)
            .run()
        )
        return res.changes

    async def pending_generation(self, intent_id: str) -> int | None:
        """未評価のgeneration(None=解消済み・評価不要)。"""
        row = (
            await self.db.prepare(
                "SELECT requested_generation, evaluated_generation"
                " FROM intent_evaluation_schedule WHERE intent_id = ?"
            )
            .bind(intent_id)
            .first()
        )
        if row is None:
            return None
        if row["evaluated_generation"] is None:
            return int(row["requested_generation"])
        if row["evaluated_generation"] < row["requested_generation"]:
            return int(row["requested_generation"])
        return None
