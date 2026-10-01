"""チャット+attendanceのstore SQL検査(M3 ws-4 design §4.1)。

test_latches_store_sql.pyと同型: 実dialect compile検査(bind paramの
部分認識ゼロ・':name::type' 落穴の再現)+新規SQLの句ピン(design §2.1・
§2.2・§2.3)。実DBでの挙動はintegration(test_chat_attendance_api.py)。
"""

from sqlalchemy.dialects import postgresql

from latch.latches import store

_NEW_SQL = (
    "_INSERT_MESSAGE",
    "_SELECT_MESSAGES_PAGE",
    "_SELECT_PARTICIPANT_USER_IDS",
    "_SELECT_BLOCK_BETWEEN",
    "_SELECT_CALIBRATION_ATTENDANCE",
    "_UPDATE_ATTENDANCE",
)


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_ws4_sql_bind_params_fully_recognized():
    """compiled文字列に未変換の ':name' が残らない(部分認識ゼロ)。"""
    for name in _NEW_SQL:
        stmt = getattr(store, name)
        compiled = _compiled(stmt)
        for token in (
            ":latch_id",
            ":sender_id",
            ":body",
            ":now",
            ":others",
            ":me",
            ":ids",
            ":ct",
            ":mid",
            ":limit",
            ":attended",
        ):
            assert token not in compiled, (name, token)


def test_insert_message_columns_pinned():
    """messages挿入は4列・idはDB DEFAULT→RETURNING(design §2.1手順6)。"""
    raw = str(store._INSERT_MESSAGE)
    assert "INSERT INTO messages" in raw
    for col in ("latch_id", "sender_id", "body", "created_at"):
        assert col in raw
    assert "RETURNING id, latch_id, sender_id, body, created_at" in raw


def test_messages_page_keyset_pinned():
    """改頁はlatch_id絞り+(created_at,id)2キーセット昇順(design §2.1)。

    Review Focus 5: created_at同値のタプルブレーカー(id > :mid)必須。
    """
    raw = str(store._SELECT_MESSAGES_PAGE)
    assert "latch_id = CAST(:latch_id AS uuid)" in raw
    assert "CAST(:ct AS timestamptz) IS NULL" in raw
    assert "created_at > CAST(:ct AS timestamptz)" in raw
    assert "(created_at = CAST(:ct AS timestamptz)" in raw
    assert "id > CAST(:mid AS uuid)" in raw
    assert "ORDER BY created_at ASC, id ASC" in raw
    assert "LIMIT :limit" in raw


def test_block_between_bidirectional_pinned():
    """blocks判定は(me→others) OR (others→me)の双方向EXISTS(引用#7)。

    Review Focus 2: 片方向だとB→A送信が通る。ANY(:others) が2回出現。
    """
    raw = str(store._SELECT_BLOCK_BETWEEN)
    assert "SELECT EXISTS" in raw
    assert "blocker_id = CAST(:me AS uuid)" in raw
    assert "blocked_id = ANY(CAST(:others AS uuid[]))" in raw
    assert "blocked_id = CAST(:me AS uuid)" in raw
    assert raw.count("ANY(CAST(:others AS uuid[]))") == 2


def test_attendance_update_conditional_pinned():
    """条件付きUPDATE: 排他の本体(design §2.3手順6)。

    Review Focus 4: WHERE actual_attended IS NULL 忘れで2人目が上書き得る。
    actual_attended/cancelled_afterは同時代入で排他を構造担保。
    """
    raw = str(store._UPDATE_ATTENDANCE)
    assert "actual_attended = :attended" in raw
    assert "cancelled_after = NOT :attended" in raw
    assert "updated_at = CAST(:now AS timestamptz)" in raw
    assert "latch_id = CAST(:latch_id AS uuid)" in raw
    assert "actual_attended IS NULL" in raw
    assert "RETURNING id" in raw


def test_select_calibration_attendance_pinned():
    """事前読取はactual_attendedのみ(design §2.3手順7の分類用)。"""
    raw = str(store._SELECT_CALIBRATION_ATTENDANCE)
    assert "SELECT actual_attended FROM calibration_records" in raw
    assert "latch_id = CAST(:latch_id AS uuid)" in raw
    # LatchRowはcompleted_atを持つ(attendance手順5・§4のスコープ注記)
    assert "completed_at" in store.LatchRow.__dataclass_fields__


def test_latch_selects_now_read_completed_at():
    """_SELECT_LATCH(_FOR_UPDATE)はcompleted_atを取得する(手順5の入力)。"""
    assert "completed_at" in str(store._SELECT_LATCH)
    assert "completed_at" in str(store._SELECT_LATCH_FOR_UPDATE)
