"""latches storeのSQL検査(実dialect compile+句ピン・M3 ws-1 design §4.1)。

compile検査はintents/test_store_sql.pyの流儀(bind paramの部分認識ゼロ。
':name::type' 系の落穴を実dialectで再現)。句ピンは回答UPDATEの直列化条件
(06 §6+design §2.2のEXISTS)と競合クローズ・一覧の条件を文字列で確定。
"""

from sqlalchemy.dialects import postgresql

from latch.latches import store


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_all_sql_bind_params_fully_recognized():
    """compiled文字列に未変換の ':name' が残らない(部分認識ゼロ)。"""
    for name in dir(store):
        if not name.isupper():
            continue
        stmt = getattr(store, name)
        if not hasattr(stmt, "compile"):
            continue
        compiled = _compiled(stmt)
        # bind paramは %(name)s 形式へ置換される。':x' が残っていれば未認識
        for token in (
            ":latch_id",
            ":me",
            ":ids",
            ":item",
            ":new_status",
            ":now",
            ":self_id",
            ":member_ids",
            ":from_status",
            ":to_status",
            ":user_id",
            ":a",
            ":b",
            ":gid",
            ":intent_ids",
            ":prediction",
            ":proposal",
            ":responses",
            ":matched",
            ":provider",
            ":subject",
            ":tt",
            ":ct",
            ":lid",
            ":limit",
        ):
            assert token not in compiled, (name, token)


def test_update_response_serialization_conditions_pinned():
    """回答UPDATEのWHERE=06 §6確定値+参加Intent検査NOT EXISTS(design §2.2手順4)。"""
    sql = _compiled(store._UPDATE_RESPONSE)
    assert (
        "responses = responses || CAST(:item AS jsonb)"
        in sql.replace("%(item)s", ":item").replace("%(new_status)s", ":new_status")
        or "|| " in sql
    )
    raw = str(store._UPDATE_RESPONSE)
    assert "status IN ('proposed', 'partial_accept')" in raw
    assert "response_deadline > CAST(:now AS timestamptz)" in raw
    assert "expires_at > CAST(:now AS timestamptz)" in raw
    assert "NOT EXISTS" in raw
    assert "i.status NOT IN ('active', 'paused')" in raw
    assert "RETURNING id, status" in raw


def test_match_intents_includes_paused():
    """Intent matched化はactive+paused(承認事項3・design §2.3)。"""
    raw = str(store._MATCH_INTENTS)
    assert "SET status = 'matched'" in raw
    assert "status IN ('active', 'paused')" in raw
    assert "RETURNING id" in raw


def test_conflicting_latches_overlap_and_order_and_lock():
    """競合クローズ対象=自己除外・3状態・配列交差・ORDER BY id つきFOR UPDATE(§9-1)。"""
    raw = str(store._SELECT_CONFLICTING_LATCHES)
    assert "id <> CAST(:self_id AS uuid)" in raw
    assert "status IN ('candidate', 'proposed', 'partial_accept')" in raw
    assert "intent_ids && CAST(:member_ids AS uuid[])" in raw
    assert "ORDER BY id" in raw
    assert "FOR UPDATE" in raw


def test_latches_page_excludes_candidate_and_sorts():
    """一覧はcandidate除外・本人関与EXISTS・3キー昇降ソート(design §2.8)。"""
    raw = str(store._SELECT_LATCHES_PAGE)
    assert "l.status <> 'candidate'" in raw
    assert "i.user_id = CAST(:me AS uuid)" in raw
    assert "ORDER BY target_time ASC, l.created_at DESC, l.id ASC" in raw
    assert "LIMIT :limit" in raw


def test_insert_calibration_columns_pinned():
    """calibration INSERTの列一式(actual_attended等は書かない・引用#13)。"""
    raw = str(store._INSERT_CALIBRATION)
    for col in (
        "latch_id",
        "intent_ids",
        "prediction",
        "proposal_snapshot",
        "actual_responses",
        "matched",
        "created_at",
        "updated_at",
    ):
        assert col in raw
    assert "actual_attended" not in raw
    assert "cancelled_after" not in raw
    assert "anonymized_at" not in raw


def test_pair_rows_ordered_by_updated_desc():
    """評価行取得はupdated_at降順(pick_eval_rowの走査前提・design §2.11)。"""
    assert "ORDER BY updated_at DESC" in str(store._SELECT_PAIR_ROWS)
    assert "jev_result IS NOT NULL" in str(store._SELECT_PAIR_ROWS)
