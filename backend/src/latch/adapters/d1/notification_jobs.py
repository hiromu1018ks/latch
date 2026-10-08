"""D1版・通知jobの配送(T10・計画 §6 T10)。

- 提示と通知jobは同じbatchで保存(atomic.apply_proposal が先行書き込み)
- ここでは配送(delivery)を担う: push(実装はstub)とアプリ内通知の
  到達保証。権限なし・送信失敗はアプリ内通知へ到達させる(§T10-4)
- 送信後のack失敗を冪等に耐える: 通知IDは安定(jobのref_id由来)で
  再配送では再送しない
- pushの重複可能性は安定した通知IDとclient側処理で扱う(§T10-3)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import micros

CHANNEL_PUSH = "push"
CHANNEL_IN_APP = "in_app"


@dataclass(frozen=True)
class DeliveryOutcome:
    delivered: bool
    channel: str  # push / in_app
    notification_id: str
    deduped: bool = False
    error: str | None = None


class PushSenderStub:
    """実pushの未実装を別作業として残す(ws-9資材待ち・計画§T10-4)。

    send()は例外を投げない・結果を記録するだけ(外部送信なし)。
    """

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail_ids: set[str] = set()

    async def send(self, *, user_id: str, notification_id: str) -> bool:
        if notification_id in self.fail_ids:
            return False
        self.sent.append(notification_id)
        return True


class D1NotificationDelivery:
    """deliver_notification(job)(§T10 Interfaces)。"""

    def __init__(self, db: LocalD1Database, *, push: PushSenderStub | None = None):
        self.db = db
        self.push = push or PushSenderStub()

    async def has_push_permission(self, user_id: str) -> bool:
        """push権限(現行users.visibility_preferences相当の境界)。

        権限なし・未設定はFalse → アプリ内通知へ到達。
        """
        row = (
            await self.db.prepare(
                "SELECT visibility_preferences FROM users WHERE id = ?"
            )
            .bind(user_id)
            .first()
        )
        if row is None or row["visibility_preferences"] is None:
            return False
        import json

        prefs = json.loads(row["visibility_preferences"])
        return bool(prefs.get("push_enabled"))

    async def deliver_notification(self, job: dict, now: datetime) -> DeliveryOutcome:
        """job = {job_id, kind='notify', ref_id=latch_id}。

        - 通知行(先行書き込み済み)をref_idから特定
        - push権限あり→push送信・失敗/権限なし→アプリ内通知で到達
          (通知行そのものがアプリ内通知の一覧)
        - 安定通知IDで配送済みならdedupe(ack失敗の再配送対策)
        """
        latch_id = job.get("ref_id")
        rows = (
            await self.db.prepare(
                "SELECT id, user_id, type FROM notifications"
                " WHERE json_extract(payload, '$.latch_id') = ? AND type IN"
                " ('proposal', 'latch_matched', 'attendance_request',"
                "  'nearby_candidate')"
            )
            .bind(latch_id)
            .all()
        )
        if not rows:
            return DeliveryOutcome(
                delivered=False,
                channel="",
                notification_id="",
                error="notification rows not found",
            )
        delivered_any = True
        deduped = False
        channel = CHANNEL_IN_APP
        for row in rows:
            if await self._already_delivered(row["id"]):
                deduped = True
                continue
            if await self.has_push_permission(row["user_id"]):
                sent = await self.push.send(
                    user_id=row["user_id"], notification_id=row["id"]
                )
                channel = CHANNEL_PUSH if sent else CHANNEL_IN_APP
                delivered_any = delivered_any and True
            else:
                # 権限なし → 通知行(=アプリ内通知)で到達
                channel = CHANNEL_IN_APP
            await self._mark_delivered(row["id"], now)
        return DeliveryOutcome(
            delivered=delivered_any,
            channel=channel,
            notification_id=rows[0]["id"],
            deduped=deduped,
        )

    async def _already_delivered(self, notification_id: str) -> bool:
        row = (
            await self.db.prepare(
                "SELECT 1 AS x FROM notification_deliveries"
                " WHERE notification_id = ? AND delivered_at IS NOT NULL"
            )
            .bind(notification_id)
            .first()
        )
        return row is not None

    async def _mark_delivered(self, notification_id: str, now: datetime) -> None:
        await self.db.batch(
            [
                self.db.prepare(
                    "INSERT OR IGNORE INTO notification_deliveries"
                    " (notification_id, delivered_at) VALUES (?, ?)"
                ).bind(notification_id, micros(now)),
            ]
        )
