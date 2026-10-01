"""notifications store/serviceのunit試験(M3 ws-3 design §4.1-3/4)。

store検査はlatches/test_latches_store_sql.pyの流儀(実dialect compileで
bind paramの部分認識ゼロ + 句ピン)。service検証はスタブstoreで分岐のみ。
"""

import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from latch.notifications import store
from latch.notifications.schemas import NotificationOut
from latch.notifications.service import (
    NotificationNotFoundError,
    NotificationsService,
    NotificationValidationError,
    decode_cursor,
    encode_cursor,
)
from latch.notifications.store import LatchJoinedRow, NotificationPageRow, OwnedRow


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_store_sql_bind_params_fully_recognized():
    """compiled文字列に未変換の ':name' が残らない(部分認識ゼロ)。"""
    for name in (
        "_SELECT_USER_ID",
        "_SELECT_NOTIFICATIONS_PAGE",
        "_SELECT_NOTIFICATION_OWNED",
        "_MARK_READ",
    ):
        compiled = _compiled(getattr(store, name))
        for token in (
            ":provider",
            ":subject",
            ":me",
            ":ct",
            ":lid",
            ":limit",
            ":nid",
            ":now",
        ):
            assert token not in compiled, (name, token)


def test_select_page_pins_join_order_and_tiebreak():
    """一覧SQLの確定値(design §2.5): 本人絞り込み・LEFT JOIN(uuid形式
    ガード=Review Focus 5のCAST失敗防止)・created_at降順+id降順タイブレーク・
    2キーキーセット条件。句ピンは生文字列(latches流儀・bind paramは:name)。"""
    raw = str(store._SELECT_NOTIFICATIONS_PAGE)
    assert "FROM notifications n" in raw
    assert "LEFT JOIN latches l" in raw
    # 不正なlatch_id文字列でCAST例外にならないよう形式ガード(§9-6)
    assert "n.payload->>'latch_id'" in raw
    assert "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'" in raw
    assert "l.id = CAST(n.payload->>'latch_id' AS uuid)" in raw
    # 本人のみ(design §2.6・Review Focus 4)
    assert "n.user_id = CAST(:me AS uuid)" in raw
    # 2キーキーセット(created_at DESC, id DESC と同値の厳密小条件)
    assert "n.created_at < CAST(:ct AS timestamptz)" in raw
    assert "n.created_at = CAST(:ct AS timestamptz)" in raw
    assert "n.id < CAST(:lid AS uuid)" in raw
    assert "ORDER BY n.created_at DESC, n.id DESC" in raw
    assert "LIMIT :limit" in raw


def test_mark_read_pins_owner_guard_and_null_condition():
    """既読UPDATE(design §2.6): 本人条件・未読条件(既読済みなら影響0=冪等)。"""
    raw = str(store._MARK_READ)
    assert "UPDATE notifications SET read_at = :now" in raw
    assert "WHERE id = CAST(:nid AS uuid)" in raw
    assert "user_id = CAST(:me AS uuid)" in raw
    assert "read_at IS NULL" in raw


# -- service: cursor・read分岐・list組み立て(スタブstore・design §4.1-3) --

SVC_NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
PROVIDER = "google"
SUBJECT = "svc-subject"


class _FakeConn:
    """store呼び出しを記録し固定行を返すスタブconn。"""

    def __init__(self, page_rows=None, owned=None, user_id=None):
        self.page_rows = page_rows or []
        self.owned = owned
        self.user_id = user_id
        self.mark_read_calls: list[dict] = []
        self.last_page_args: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeEngine:
    """serviceのengineスタブ(connectもbeginも同一connを返す)。"""

    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn

    def begin(self):
        return self._conn


class _StubStore:
    """store関数のスタブ(serviceと同一シグネチャ)。"""

    def __init__(self, conn):
        self._conn = conn

    async def fetch_user_id(self, conn, provider, subject):
        return self._conn.user_id

    async def select_notifications_page(self, conn, *, me, before, limit):
        self._conn.last_page_args = {"me": me, "before": before, "limit": limit}
        return self._conn.page_rows

    async def select_notification_owned(self, conn, *, me, notification_id):
        return self._conn.owned

    async def mark_read(self, conn, *, me, notification_id, now):
        self._conn.mark_read_calls.append(
            {"me": me, "nid": notification_id, "now": now}
        )


def _service(conn) -> NotificationsService:
    return NotificationsService(clock=FakeClockForService(), engine=_FakeEngine(conn))


class FakeClockForService:
    def now(self):
        return SVC_NOW


def _page_row(n: int, *, latch=True) -> NotificationPageRow:
    lid = uuid_mod.UUID(f"00000000-0000-4000-8000-{n:012d}")
    latch_row = (
        LatchJoinedRow(
            id=lid,
            status="proposed",
            response_deadline=SVC_NOW + timedelta(hours=2),
            expires_at=SVC_NOW + timedelta(days=5),
            completed_at=None,
            proposal={"headcount": 2, "match_level": "medium"},
        )
        if latch
        else None
    )
    return NotificationPageRow(
        id=uuid_mod.UUID(f"00000000-0000-4000-8000-{1000 + n:012d}"),
        type="proposal",
        latch_id_raw=str(lid) if latch else "not-a-uuid",
        read_at=None,
        created_at=SVC_NOW,
        latch=latch_row,
    )


def test_encode_decode_cursor_roundtrip():
    """2キーcursor(created_at, id)の往復(design §2.5)。"""
    nid = uuid_mod.UUID("00000000-0000-4000-8000-0000000000d1")
    cursor = encode_cursor(SVC_NOW, nid)
    assert decode_cursor(cursor) == (SVC_NOW, nid)


def test_decode_cursor_invalid_raises_422():
    """形式不正はNotificationValidationError(422 VALIDATION_ERROR)。"""
    with pytest.raises(NotificationValidationError):
        decode_cursor("!!!not-base64!!!")
    with pytest.raises(NotificationValidationError):
        decode_cursor("YWJj")  # base64urlとしては有効だが区切りなし


async def test_read_marks_unread():
    """未読行 → mark_read呼出(例外なし=204相当)。"""
    nid = uuid_mod.UUID("00000000-0000-4000-8000-0000000000d1")
    uid = uuid_mod.UUID("00000000-0000-4000-8000-0000000000e1")
    conn = _FakeConn(
        owned=OwnedRow(id=nid, read_at=None),
        user_id=uid,
    )
    stub = _StubStore(conn)
    service = _service(conn)
    _monkeypatch_store(stub)
    await service.read(
        auth_provider=PROVIDER, auth_subject=SUBJECT, notification_id=nid
    )
    assert conn.mark_read_calls == [{"me": uid, "nid": nid, "now": SVC_NOW}]


async def test_read_already_read_is_idempotent():
    """既読行 → UPDATEせず完了(冪等・design §2.6)。"""
    nid = uuid_mod.UUID("00000000-0000-4000-8000-0000000000d1")
    conn = _FakeConn(
        owned=OwnedRow(id=nid, read_at=SVC_NOW),
        user_id=uuid_mod.UUID("00000000-0000-4000-8000-0000000000e1"),
    )
    service = _service(conn)
    _monkeypatch_store(_StubStore(conn))
    await service.read(
        auth_provider=PROVIDER, auth_subject=SUBJECT, notification_id=nid
    )
    assert conn.mark_read_calls == []


async def test_read_not_owned_or_missing_raises_404():
    """所有検査None(他人・不存在を区別しない)→ 404(design §2.6)。"""
    conn = _FakeConn(
        owned=None,
        user_id=uuid_mod.UUID("00000000-0000-4000-8000-0000000000e1"),
    )
    service = _service(conn)
    _monkeypatch_store(_StubStore(conn))
    with pytest.raises(NotificationNotFoundError):
        await service.read(
            auth_provider=PROVIDER,
            auth_subject=SUBJECT,
            notification_id=uuid_mod.UUID("00000000-0000-4000-8000-0000000000d2"),
        )


def _monkeypatch_store(stub):
    """service.store属性をスタブへ差し替え(monkeypatchなしの簡易版)。"""
    import latch.notifications.service as service_module

    service_module.store = stub
    return stub


async def test_list_builds_items_with_latch():
    """list応答組み立て: latch要素埋め込み・limit+1でnext_cursor。"""
    rows = [_page_row(1), _page_row(2)]
    conn = _FakeConn(
        page_rows=rows,
        user_id=uuid_mod.UUID("00000000-0000-4000-8000-0000000000e1"),
    )
    service = _service(conn)
    _monkeypatch_store(_StubStore(conn))
    items, next_cursor = await service.list(
        auth_provider=PROVIDER, auth_subject=SUBJECT, limit=1
    )
    assert len(items) == 1
    assert isinstance(items[0], NotificationOut)
    assert items[0].latch is not None
    assert items[0].latch.proposal == {"headcount": 2, "match_level": "medium"}
    assert items[0].latch_id == uuid_mod.UUID(rows[0].latch_id_raw)
    assert next_cursor is not None  # 2行取得(>limit)のため次頁あり
    assert conn.last_page_args["limit"] == 2  # limit+1


async def test_list_latch_null_defensive():
    """参照先不明(latch=null)・不正latch_id文字列 → itemはnullで落ちない。"""
    row = _page_row(3, latch=False)
    conn = _FakeConn(
        page_rows=[row],
        user_id=uuid_mod.UUID("00000000-0000-4000-8000-0000000000e1"),
    )
    service = _service(conn)
    _monkeypatch_store(_StubStore(conn))
    items, next_cursor = await service.list(
        auth_provider=PROVIDER, auth_subject=SUBJECT, limit=20
    )
    assert items[0].latch is None
    assert items[0].latch_id is None  # uuid変換失敗はnull(防御)
    assert next_cursor is None
