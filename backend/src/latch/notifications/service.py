"""notificationsユースケース(M3 ws-3 design §2.5・§2.6・§2.8)。

一覧(cursor 2キー・LEFT JOIN行の組み立て)と既読(所有検査→冪等UPDATE)。
storeはモジュール属性経由で呼ぶ(unit試験が差し替え可能 — latch_engineと
同一規律)。予期しない例外はlatchesと同じラップ方針(例外クラス名のみ
ログへ残して503)。
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.notifications import store
from latch.notifications.schemas import NotificationLatchOut, NotificationOut

logger = logging.getLogger("latch.notifications")


class NotificationsError(Exception):
    """notificationsドメインエラーの基底。http_status/codeを持つ。"""

    http_status: int
    code: str


class NotificationNotFoundError(NotificationsError):
    """対象が存在しない/他人のもの(区別しない・design §2.6)。未登録JWTも404。"""

    http_status = 404
    code = "NOT_FOUND"


class NotificationValidationError(NotificationsError):
    """リクエスト検証422(cursor形式不正等・intents/latchesと同型)。"""

    http_status = 422
    code = "VALIDATION_ERROR"


class DependencyUnavailableError(NotificationsError):
    """DB等の依存障害(05 §5「全API」)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"


def _wrap_unexpected(exc: Exception) -> DependencyUnavailableError:
    """予期しない例外を503へ包む。例外のクラス名のみログへ残す(08 §2.4)。"""
    logger.warning("notifications.unexpected class=%s", type(exc).__name__)
    return DependencyUnavailableError("notifications dependency unavailable")


def encode_cursor(created_at: datetime, notification_id: uuid.UUID) -> str:
    """キーセットcursor 2キー(design §2.5): base64url("ISO8601|uuid")。

    ソート順(created_at DESC, id DESC)をタプル比較で表現(intentsの
    encode_cursorパターン)。
    """
    raw = f"{created_at.isoformat()}|{notification_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """cursor復元。形式不正は422 VALIDATION_ERROR(intentsと同型)。"""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ts_part, id_part = raw.split("|")
        return datetime.fromisoformat(ts_part), uuid.UUID(id_part)
    except (ValueError, UnicodeDecodeError) as exc:
        raise NotificationValidationError("invalid cursor") from exc


def _to_uuid_or_none(raw: str | None) -> uuid.UUID | None:
    """payloadのlatch_id生文字列→uuid(失敗はnull・防御)。"""
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


class NotificationsService:
    """お知らせ一覧・既読のユースケース(design §2.5・§2.6)。"""

    def __init__(self, *, clock: Clock, engine: AsyncEngine) -> None:
        self._clock = clock
        self._engine = engine

    async def list(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[NotificationOut], str | None]:
        try:
            async with self._engine.connect() as conn:
                user_id = await store.fetch_user_id(conn, auth_provider, auth_subject)
                if user_id is None:
                    raise NotificationNotFoundError("user not found")
                before = decode_cursor(cursor) if cursor else None
                rows = await store.select_notifications_page(
                    conn, me=user_id, before=before, limit=limit + 1
                )
            items = []
            for r in rows[:limit]:
                latch_out = None
                if r.latch is not None:
                    latch_out = NotificationLatchOut(
                        id=r.latch.id,
                        status=r.latch.status,
                        response_deadline=r.latch.response_deadline,
                        expires_at=r.latch.expires_at,
                        completed_at=r.latch.completed_at,
                        proposal=r.latch.proposal,
                    )
                items.append(
                    NotificationOut(
                        id=r.id,
                        type=r.type,
                        latch_id=_to_uuid_or_none(r.latch_id_raw),
                        read_at=r.read_at,
                        created_at=r.created_at,
                        latch=latch_out,
                    )
                )
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = encode_cursor(last.created_at, last.id)
            return items, next_cursor
        except NotificationsError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    async def read(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        notification_id: uuid.UUID,
    ) -> None:
        """既読化(design §2.6)。所有検査Noneは404。既読済みはUPDATEせず完了。"""
        try:
            async with self._engine.begin() as conn:
                user_id = await store.fetch_user_id(conn, auth_provider, auth_subject)
                if user_id is None:
                    raise NotificationNotFoundError("user not found")
                row = await store.select_notification_owned(
                    conn, me=user_id, notification_id=notification_id
                )
                if row is None:
                    raise NotificationNotFoundError("notification not found")
                if row.read_at is None:
                    await store.mark_read(
                        conn,
                        me=user_id,
                        notification_id=notification_id,
                        now=self._clock.now(),
                    )
        except NotificationsError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc


def make_notifications_service(
    *, clock: Clock, engine: AsyncEngine
) -> NotificationsService:
    """main.py lifespan用の構築(latchesのmake_latches_serviceと同型)。"""
    return NotificationsService(clock=clock, engine=engine)
