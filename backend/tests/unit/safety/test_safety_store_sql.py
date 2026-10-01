"""safety永続化のSQL検査(M3 ws-5 design §4.1)。

intents/latchesのtest_store_sql.pyと同型: 実dialect compile検査(bind paramの
部分認識ゼロ)+新規SQLの句ピン(design §2.3〜§2.5)。実DBでの挙動は
integration(test_safety_api.py)の担い。
"""

from sqlalchemy.dialects import postgresql

from latch.safety import store

_NEW_SQL = (
    "_SELECT_USER_EXISTS",
    "_SELECT_BLOCK_EXISTS",
    "_INSERT_BLOCK",
    "_DELETE_BLOCK",
    "_SELECT_BLOCKS_PAGE",
    "_SELECT_BLOCKED_IDS_MAP",
    "_SELECT_USER_INTENT_IDS",
    "_SELECT_OPEN_LATCHES_BETWEEN",
    "_INSERT_REPORT",
)


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_sql_bind_params_fully_recognized():
    """compiled文字列に未変換の ':name' が残らない(部分認識ゼロ)。"""
    tokens = (
        ":u",
        ":me",
        ":target",
        ":blocker",
        ":blocked",
        ":now",
        ":ct",
        ":bid",
        ":limit",
        ":ids",
        ":my_intents",
        ":their_intents",
        ":reporter",
        ":reportee",
        ":latch_id",
        ":reason",
        ":status",
    )
    for name in _NEW_SQL:
        compiled = _compiled(getattr(store, name))
        for token in tokens:
            assert token not in compiled, (name, token)


def test_block_insert_exists_delete_pinned():
    """blocks書込3種: 存在検査・INSERT・DELETE RETURNING(design §2.4)。

    Review Focus 1: UNIQUEなし(引用#16)のため存在検査が二重行防止の本体。
    """
    raw_ex = str(store._SELECT_BLOCK_EXISTS)
    assert "SELECT 1 FROM blocks" in raw_ex
    assert "blocker_id = CAST(:me AS uuid)" in raw_ex
    assert "blocked_id = CAST(:target AS uuid)" in raw_ex
    raw_in = str(store._INSERT_BLOCK)
    assert "INSERT INTO blocks (blocker_id, blocked_id, created_at)" in raw_in
    assert "RETURNING id" in raw_in
    raw_de = str(store._DELETE_BLOCK)
    assert "DELETE FROM blocks" in raw_de
    assert "blocker_id = CAST(:blocker AS uuid)" in raw_de
    assert "blocked_id = CAST(:blocked AS uuid)" in raw_de
    assert "RETURNING id" in raw_de


def test_blocks_page_keyset_pinned():
    """一覧はusers JOIN・created_at DESC,id DESCの2キーセット(design §2.4)。"""
    raw = str(store._SELECT_BLOCKS_PAGE)
    assert "JOIN users u ON u.id = b.blocked_id" in raw
    assert "blocker_id = CAST(:me AS uuid)" in raw
    assert "CAST(:ct AS timestamptz) IS NULL" in raw
    assert "b.created_at < CAST(:ct AS timestamptz)" in raw
    assert "(b.created_at = CAST(:ct AS timestamptz)" in raw
    assert "b.id < CAST(:bid AS uuid)" in raw
    assert "ORDER BY b.created_at DESC, b.id DESC" in raw
    assert "LIMIT :limit" in raw


def test_blocked_ids_map_pinned():
    """キャッシュミス用の一括読み(blocker_id→blocked_id・design §2.2)。"""
    raw = str(store._SELECT_BLOCKED_IDS_MAP)
    assert "SELECT blocker_id, blocked_id FROM blocks" in raw
    assert "blocker_id = ANY(CAST(:ids AS uuid[]))" in raw


def test_user_intent_ids_pinned():
    """D-23対象検索の入力: ユーザーの全Intent(全status・design §2.3)。"""
    raw = str(store._SELECT_USER_INTENT_IDS)
    assert "SELECT id FROM intents" in raw
    assert "user_id = CAST(:u AS uuid)" in raw


def test_open_latches_between_pinned():
    """cancelled化対象: status 3値・intent_ids &&×2・ORDER BY id FOR UPDATE。

    Review Focus 2: candidate抜きだとブロック済み相手への提示が残り、
    matched入りだと成立済みを潰す。design §2.3のSQLどおり(承認事項②)。
    """
    raw = str(store._SELECT_OPEN_LATCHES_BETWEEN)
    assert "IN ('candidate', 'proposed', 'partial_accept')" in raw
    assert "l.intent_ids && CAST(:my_intents AS uuid[])" in raw
    assert "l.intent_ids && CAST(:their_intents AS uuid[])" in raw
    assert "ORDER BY l.id" in raw
    assert "FOR UPDATE" in raw


def test_insert_report_pinned():
    """reports INSERTは6列(statusはアプリ層値・CHECKなし・design §2.5)。"""
    raw = str(store._INSERT_REPORT)
    assert "INSERT INTO reports" in raw
    for col in (
        "reporter_id",
        "reportee_id",
        "latch_id",
        "reason",
        "status",
        "created_at",
    ):
        assert col in raw
    assert "RETURNING id" in raw


def test_user_exists_pinned():
    """相手実在検査(users 1行・design §2.4手順3)。"""
    raw = str(store._SELECT_USER_EXISTS)
    assert "SELECT 1 FROM users" in raw
    assert "id = CAST(:u AS uuid)" in raw
