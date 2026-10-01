"""safety永続化(text()生SQL・M3 ws-5 design §2.2〜§2.5)。

SQL定数+connを受け取るasync関数群(latches/store.py流儀。トランザクション
はserviceが統轄)。asyncpgのUUID復元は_coerce_uuid規律・uuid[]のbindは
list[uuid.UUID]+SQL側CAST。blocksのUNIQUEなし(引用#16)は存在検査で
アプリ層担保、reportsのstatus値域(pending→reviewed→resolved)もアプリ層
(design §2.5・DB CHECKなし)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT/RETURNING結果のUUID列復元(asyncpgサブクラス対策・latchesと同型)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


@dataclass(frozen=True)
class BlockRow:
    """blocks一覧の1行(users JOIN・design §2.4)。"""

    id: uuid.UUID
    blocked_id: uuid.UUID
    display_name: str
    created_at: datetime


# -- 相手実在・blocks書込(design §2.4手順3〜5) --

_SELECT_USER_EXISTS = text("""
    SELECT 1 FROM users WHERE id = CAST(:u AS uuid)
""")

_SELECT_BLOCK_EXISTS = text("""
    SELECT 1 FROM blocks
    WHERE blocker_id = CAST(:me AS uuid) AND blocked_id = CAST(:target AS uuid)
""")

_INSERT_BLOCK = text("""
    INSERT INTO blocks (blocker_id, blocked_id, created_at)
    VALUES (CAST(:blocker AS uuid), CAST(:blocked AS uuid),
            CAST(:now AS timestamptz))
    RETURNING id
""")

_DELETE_BLOCK = text("""
    DELETE FROM blocks
    WHERE blocker_id = CAST(:blocker AS uuid) AND blocked_id = CAST(:blocked AS uuid)
    RETURNING id
""")

# -- 一覧改頁(created_at DESC, id DESCの2キーセット・design §2.4) --

_SELECT_BLOCKS_PAGE = text("""
    SELECT b.id, b.blocked_id, u.display_name, b.created_at
    FROM blocks b JOIN users u ON u.id = b.blocked_id
    WHERE b.blocker_id = CAST(:me AS uuid)
      AND (CAST(:ct AS timestamptz) IS NULL
           OR b.created_at < CAST(:ct AS timestamptz)
           OR (b.created_at = CAST(:ct AS timestamptz)
               AND b.id < CAST(:bid AS uuid)))
    ORDER BY b.created_at DESC, b.id DESC
    LIMIT :limit
""")

# -- キャッシュミス用の一括読み(design §2.2) --

_SELECT_BLOCKED_IDS_MAP = text("""
    SELECT blocker_id, blocked_id FROM blocks
    WHERE blocker_id = ANY(CAST(:ids AS uuid[]))
""")

# -- D-23 cancelled化対象(design §2.3。両ユーザーのIntentを共に含む進行中
#    latches。ORDER BY id でロック順序を固定 — 競合クローズ
#    (_SELECT_CONFLICTING_LATCHES)と同型) --

_SELECT_USER_INTENT_IDS = text("""
    SELECT id FROM intents WHERE user_id = CAST(:u AS uuid)
""")

_SELECT_OPEN_LATCHES_BETWEEN = text("""
    SELECT l.id, l.status FROM latches l
    WHERE l.status IN ('candidate', 'proposed', 'partial_accept')
      AND l.intent_ids && CAST(:my_intents AS uuid[])
      AND l.intent_ids && CAST(:their_intents AS uuid[])
    ORDER BY l.id
    FOR UPDATE
""")

# -- reports(design §2.5・単一INSERTのみ) --

_INSERT_REPORT = text("""
    INSERT INTO reports (reporter_id, reportee_id, latch_id, reason, status,
                         created_at)
    VALUES (CAST(:reporter AS uuid), CAST(:reportee AS uuid),
            CAST(:latch_id AS uuid), :reason, :status, CAST(:now AS timestamptz))
    RETURNING id
""")


async def user_exists(conn: AsyncConnection, user_id: uuid.UUID) -> bool:
    """相手ユーザーの実在検査(design §2.4手順3)。"""
    res = await conn.execute(_SELECT_USER_EXISTS, {"u": user_id})
    return res.first() is not None


async def block_exists(
    conn: AsyncConnection, *, blocker: uuid.UUID, target: uuid.UUID
) -> bool:
    """自分→相手のblocks行の存在検査(冪等201の分岐・引用#16)。"""
    res = await conn.execute(_SELECT_BLOCK_EXISTS, {"me": blocker, "target": target})
    return res.first() is not None


async def insert_block(
    conn: AsyncConnection,
    *,
    blocker: uuid.UUID,
    blocked: uuid.UUID,
    now: datetime,
) -> uuid.UUID:
    """blocks行挿入(design §2.4手順5)。idはDB DEFAULT→RETURNING。"""
    res = await conn.execute(
        _INSERT_BLOCK, {"blocker": blocker, "blocked": blocked, "now": now}
    )
    row = res.first()
    assert row is not None
    return _coerce_uuid(row[0])


async def delete_block(
    conn: AsyncConnection, *, blocker: uuid.UUID, blocked: uuid.UUID
) -> bool:
    """blocks行削除(design §2.4)。False=行なし(呼び出し側は404)。"""
    res = await conn.execute(_DELETE_BLOCK, {"blocker": blocker, "blocked": blocked})
    return res.first() is not None


async def select_blocks_page(
    conn: AsyncConnection,
    *,
    me: uuid.UUID,
    before: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[BlockRow]:
    """blocks一覧(design §2.4)。before=cursor位置(created_at, id)。"""
    res = await conn.execute(
        _SELECT_BLOCKS_PAGE,
        {
            "me": me,
            "ct": before[0] if before else None,
            "bid": before[1] if before else None,
            "limit": limit,
        },
    )
    return [
        BlockRow(
            id=_coerce_uuid(r[0]),
            blocked_id=_coerce_uuid(r[1]),
            display_name=r[2],
            created_at=r[3],
        )
        for r in res.fetchall()
    ]


async def select_blocked_ids_map(
    conn: AsyncConnection, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """blocker_id→blocked_id一覧(キャッシュミス時の一括DB読み・§2.2)。

    ブロックゼロのユーザーはキーなし(呼び出し側が空リスト扱い)。
    """
    res = await conn.execute(_SELECT_BLOCKED_IDS_MAP, {"ids": list(user_ids)})
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for blocker, blocked in res.fetchall():
        out.setdefault(_coerce_uuid(blocker), []).append(_coerce_uuid(blocked))
    return out


async def select_user_intent_ids(
    conn: AsyncConnection, user_id: uuid.UUID
) -> list[uuid.UUID]:
    """ユーザーの全Intent id(全status・design §2.3の対象検索の入力)。"""
    res = await conn.execute(_SELECT_USER_INTENT_IDS, {"u": user_id})
    return [_coerce_uuid(r[0]) for r in res.fetchall()]


async def select_open_latches_between(
    conn: AsyncConnection,
    my_intents: list[uuid.UUID],
    their_intents: list[uuid.UUID],
) -> list[tuple[uuid.UUID, str]]:
    """D-23 cancelled化対象の行ロック取得(ORDER BY id・design §2.3)。"""
    res = await conn.execute(
        _SELECT_OPEN_LATCHES_BETWEEN,
        {"my_intents": list(my_intents), "their_intents": list(their_intents)},
    )
    return [(_coerce_uuid(r[0]), r[1]) for r in res.fetchall()]


async def insert_report(
    conn: AsyncConnection,
    *,
    reporter: uuid.UUID,
    reportee: uuid.UUID,
    latch_id: uuid.UUID | None,
    reason: str,
    status: str,
    now: datetime,
) -> uuid.UUID:
    """reports挿入(design §2.5)。statusはserviceが'pending'固定で渡す。"""
    res = await conn.execute(
        _INSERT_REPORT,
        {
            "reporter": reporter,
            "reportee": reportee,
            "latch_id": latch_id,
            "reason": reason,
            "status": status,
            "now": now,
        },
    )
    row = res.first()
    assert row is not None
    return _coerce_uuid(row[0])
