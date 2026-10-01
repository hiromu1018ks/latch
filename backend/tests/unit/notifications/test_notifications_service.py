"""notifications store/serviceのunit試験(M3 ws-3 design §4.1-3/4)。

store検査はlatches/test_latches_store_sql.pyの流儀(実dialect compileで
bind paramの部分認識ゼロ + 句ピン)。service検証はスタブstoreで分岐のみ。
"""

from sqlalchemy.dialects import postgresql

from latch.notifications import store


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
