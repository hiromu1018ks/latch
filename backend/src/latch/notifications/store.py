"""notifications永続化(M3 ws-3 design §2.5・§2.6・§3.1)。

text()生SQL・_coerce_uuid規律(latches/intentsと同一)。users解決SQLは
パッケージ自前(latches/store.pyと同一慣行 — uq_users_auth_provider_subject
索引へのSELECT 1本)。一覧はpayload->>'latch_id'でlatchesをLEFT JOIN
(design §2.5案A): 参照先不明(削除等)・形式不正はlatch=null(防御)。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_SELECT_USER_ID = text("""
    SELECT id FROM users
    WHERE auth_provider = :provider AND auth_subject = :subject
""")

# お知らせ一覧(design §2.5案A)。cursorは2キー(created_at, id)のキーセット。
# 同一tx内の複数行(D-20同時送信)が同時刻になるためid降順タイブレークが必須。
# LEFT JOINのON句のuuid形式ガードは不正なpayloadでCAST例外→一覧500を防ぐ
# (Review Focus 5・先行書き込み経路は常に正しいuuidを書くため通常通らない防御)
_SELECT_NOTIFICATIONS_PAGE = text("""
    SELECT n.id, n.type, n.payload->>'latch_id', n.read_at, n.created_at,
           l.id, l.status, l.response_deadline, l.expires_at, l.completed_at,
           l.proposal
    FROM notifications n
    LEFT JOIN latches l
      ON n.payload->>'latch_id'
         ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     AND l.id = CAST(n.payload->>'latch_id' AS uuid)
    WHERE n.user_id = CAST(:me AS uuid)
      AND (CAST(:ct AS timestamptz) IS NULL
           OR n.created_at < CAST(:ct AS timestamptz)
           OR (n.created_at = CAST(:ct AS timestamptz)
               AND n.id < CAST(:lid AS uuid)))
    ORDER BY n.created_at DESC, n.id DESC
    LIMIT :limit
""")

# 既読API手順1(design §2.6): 対象行のSELECT(本人のみ。不在=None)
_SELECT_NOTIFICATION_OWNED = text("""
    SELECT id, read_at FROM notifications
    WHERE id = CAST(:nid AS uuid) AND user_id = CAST(:me AS uuid)
""")

# 既読API手順2(design §2.6): 未読のみUPDATE(既読済みは影響0=冪等)
_MARK_READ = text("""
    UPDATE notifications SET read_at = :now
    WHERE id = CAST(:nid AS uuid) AND user_id = CAST(:me AS uuid)
      AND read_at IS NULL
""")


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策・latchesと同一)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _jsonb(value: object) -> object:
    """jsonb列の復元(latches/store.pyと同一規律)。"""
    if isinstance(value, str):
        return json.loads(value)
    return value


@dataclass(frozen=True)
class LatchJoinedRow:
    """LEFT JOINで埋めたlatch要素(design §2.5の応答latchブロック)。"""

    id: uuid.UUID
    status: str
    response_deadline: datetime
    expires_at: datetime
    completed_at: datetime | None
    proposal: dict


@dataclass(frozen=True)
class NotificationPageRow:
    """一覧行(payloadのlatch_idは未変換の生文字列で持つ・serviceで変換)。"""

    id: uuid.UUID
    type: str
    latch_id_raw: str | None
    read_at: datetime | None
    created_at: datetime
    latch: LatchJoinedRow | None


@dataclass(frozen=True)
class OwnedRow:
    """既読APIの所有検査結果。"""

    id: uuid.UUID
    read_at: datetime | None


async def fetch_user_id(
    conn: AsyncConnection, provider: str, subject: str
) -> uuid.UUID | None:
    """認証subject→users.id。未登録JWT=None(呼び出し側は404・引用#17)。"""
    res = await conn.execute(
        _SELECT_USER_ID, {"provider": provider, "subject": subject}
    )
    row = res.first()
    return _coerce_uuid(row[0]) if row is not None else None


async def select_notifications_page(
    conn: AsyncConnection,
    *,
    me: uuid.UUID,
    before: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[NotificationPageRow]:
    """一覧(design §2.5)。before=cursor位置(created_at, id)。"""
    params: dict = {
        "me": me,
        "ct": before[0] if before else None,
        "lid": before[1] if before else None,
        "limit": limit,
    }
    res = await conn.execute(_SELECT_NOTIFICATIONS_PAGE, params)
    out: list[NotificationPageRow] = []
    for r in res.fetchall():
        latch: LatchJoinedRow | None = None
        if r[5] is not None:  # LEFT JOIN成立(l.id)
            latch = LatchJoinedRow(
                id=_coerce_uuid(r[5]),
                status=r[6],
                response_deadline=r[7],
                expires_at=r[8],
                completed_at=r[9],
                proposal=dict(_jsonb(r[10]) or {}),
            )
        out.append(
            NotificationPageRow(
                id=_coerce_uuid(r[0]),
                type=r[1],
                latch_id_raw=r[2],
                read_at=r[3],
                created_at=r[4],
                latch=latch,
            )
        )
    return out


async def select_notification_owned(
    conn: AsyncConnection, *, me: uuid.UUID, notification_id: uuid.UUID
) -> OwnedRow | None:
    """所有検査(本人の行のみ。他人・不存在は同じNone=区別しない404)。"""
    res = await conn.execute(
        _SELECT_NOTIFICATION_OWNED, {"nid": notification_id, "me": me}
    )
    row = res.first()
    if row is None:
        return None
    return OwnedRow(id=_coerce_uuid(row[0]), read_at=row[1])


async def mark_read(
    conn: AsyncConnection,
    *,
    me: uuid.UUID,
    notification_id: uuid.UUID,
    now: datetime,
) -> None:
    """既読化(design §2.6)。未読のみUPDATE(影響は確認不要=冪等204)。"""
    await conn.execute(_MARK_READ, {"nid": notification_id, "me": me, "now": now})
