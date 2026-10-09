"""notifications・safetyのD1版サービス(本体実装)。

参照実装(notifications/service.py・safety/service.py)と同一の検証順序・
応答構成。書き込みのうち block/unblock は atomic.apply_safety(D-23の
cancelled化込み)を使う。report・既読化は単一INSERT/UPDATE(参照と同一)。
"""

from __future__ import annotations

import base64
import uuid as uuid_mod
from datetime import datetime

from latch.adapters.d1.atomic import D1AtomicRepository
from latch.domain.time import from_micros, micros
from latch.domain.types import CommandStatus
from latch.notifications.schemas import (
    NotificationLatchOut,
    NotificationOut,
)

from latch_worker.gjson import loads_json


def _encode_cursor(created_at: datetime, notification_id) -> str:
    raw = f"{created_at.isoformat()}|{notification_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


class D1NotificationsService:
    """お知らせ一覧・既読(notifications/service.py相当)。"""

    def __init__(self, *, clock, db) -> None:
        self._clock = clock
        self._d1 = db

    async def _fetch_user_id(self, provider: str, subject: str):
        row = (
            await self._d1.prepare(
                "SELECT id FROM users WHERE auth_provider = ? AND auth_subject = ?"
            )
            .bind(provider, subject)
            .first()
        )
        return uuid_mod.UUID(str(row["id"])) if row is not None else None

    async def list(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        cursor: str | None = None,
        limit: int = 20,
    ):
        from latch.notifications.service import (
            DependencyUnavailableError,
            NotificationNotFoundError,
            NotificationValidationError,
            NotificationsError,
        )

        try:
            user_id = await self._fetch_user_id(auth_provider, auth_subject)
            if user_id is None:
                raise NotificationNotFoundError("user not found")
            before = None
            if cursor:
                padded = cursor + "=" * (-len(cursor) % 4)
                try:
                    raw = base64.urlsafe_b64decode(padded.encode()).decode()
                    ts_part, id_part = raw.split("|")
                    before = (datetime.fromisoformat(ts_part), uuid_mod.UUID(id_part))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise NotificationValidationError("invalid cursor") from exc
            clauses = ["n.user_id = ?"]
            params: list = [str(user_id)]
            if before is not None:
                clauses.append(
                    "(n.created_at < ? OR (n.created_at = ? AND n.id < ?))"
                )
                params.extend(
                    [micros(before[0]), micros(before[0]), str(before[1])]
                )
            rows = (
                await self._d1.prepare(
                    "SELECT n.id, n.type,"
                    " json_extract(n.payload, '$.latch_id') AS latch_id_raw,"
                    " n.read_at, n.created_at,"
                    " l.id AS l_id, l.status, l.response_deadline, l.expires_at,"
                    " l.completed_at, l.proposal, l.responses"
                    " FROM notifications n"
                    " LEFT JOIN latches l"
                    " ON l.id = json_extract(n.payload, '$.latch_id')"
                    " WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY n.created_at DESC, n.id DESC LIMIT ?"
                )
                .bind(*params, limit + 1)
                .all()
            )
            items = []
            for r in rows[:limit]:
                latch_out = None
                if r["l_id"] is not None:
                    responses = loads_json(r["responses"], [])
                    my = next(
                        (
                            resp.get("response")
                            for resp in responses
                            if resp.get("user_id") == str(user_id)
                        ),
                        None,
                    )
                    latch_out = NotificationLatchOut(
                        id=uuid_mod.UUID(str(r["l_id"])),
                        status=r["status"],
                        response_deadline=from_micros(r["response_deadline"]),
                        expires_at=from_micros(r["expires_at"]),
                        completed_at=(
                            from_micros(r["completed_at"])
                            if r["completed_at"]
                            else None
                        ),
                        proposal=loads_json(r["proposal"], {}),
                        my_response=my,
                    )
                latch_id_raw = r["latch_id_raw"]
                latch_id = None
                if latch_id_raw:
                    try:
                        latch_id = uuid_mod.UUID(str(latch_id_raw))
                    except ValueError:
                        latch_id = None
                items.append(
                    NotificationOut(
                        id=uuid_mod.UUID(str(r["id"])),
                        type=r["type"],
                        latch_id=latch_id,
                        read_at=from_micros(r["read_at"]) if r["read_at"] else None,
                        created_at=from_micros(r["created_at"]),
                        latch=latch_out,
                    )
                )
            next_cursor = None
            if len(rows) > limit:
                last = items[limit - 1]
                next_cursor = _encode_cursor(last.created_at, last.id)
            return items, next_cursor
        except NotificationsError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DependencyUnavailableError(
                "notifications dependency unavailable"
            ) from exc

    async def read(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        notification_id,
    ) -> None:
        from latch.notifications.service import (
            DependencyUnavailableError,
            NotificationNotFoundError,
            NotificationsError,
        )

        try:
            user_id = await self._fetch_user_id(auth_provider, auth_subject)
            if user_id is None:
                raise NotificationNotFoundError("user not found")
            row = (
                await self._d1.prepare(
                    "SELECT id, read_at FROM notifications"
                    " WHERE id = ? AND user_id = ?"
                )
                .bind(str(notification_id), str(user_id))
                .first()
            )
            if row is None:
                raise NotificationNotFoundError("notification not found")
            if row["read_at"] is None:
                await self._d1.batch(
                    [
                        self._d1.prepare(
                            "UPDATE notifications SET read_at = ?"
                            " WHERE id = ? AND user_id = ? AND read_at IS NULL"
                        ).bind(
                            micros(self._clock.now()),
                            str(notification_id),
                            str(user_id),
                        )
                    ]
                )
        except NotificationsError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DependencyUnavailableError(
                "notifications dependency unavailable"
            ) from exc
