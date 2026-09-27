"""スキーマ実体の検証 — design.md §4.2対応表の9検証のうちカタログ検証(#1〜#4・#8)。

05 §2〜§3・06 §9 の要素が実DBへ反映済みであることを情報スキーマ/カタログで証明する。
挙動検証(#5〜#7・#9)は後段のタスクでこのファイルへ追記する。
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "users",
    "intents",
    "match_candidates",
    "group_candidates",
    "latches",
    "match_events",
    "latch_status_events",
    "blocks",
    "reports",
    "notifications",
    "messages",
    "calibration_records",
}

# 05 §1 ERどおりのFK(design §2.6: match_events.source_intent_id を除く)
EXPECTED_FKS = {
    "fk_intents_user",
    "fk_match_candidates_intent_a",
    "fk_match_candidates_intent_b",
    "fk_latches_group_candidate",
    "fk_latch_status_events_latch",
    "fk_blocks_blocker",
    "fk_blocks_blocked",
    "fk_reports_reporter",
    "fk_reports_reportee",
    "fk_reports_latch",
    "fk_notifications_user",
    "fk_messages_latch",
    "fk_messages_sender",
    "fk_calibration_records_latch",
}

EXPECTED_UNIQUE_CONSTRAINTS = {
    "uq_users_auth_provider_subject",
    "uq_match_candidates_intent_versions",
}


async def test_extensions_installed(db_engine):
    """#1: postgis・vectorがpg_extensionに存在
    (design §1.2 #15 — 拡張は本移行で作成)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(text("SELECT extname FROM pg_extension"))
        names = {row[0] for row in result}
    assert {"postgis", "vector"} <= names


async def test_twelve_tables_exist(db_engine):
    """#2: 12テーブルがpublicスキーマに存在(05 §2)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        names = {row[0] for row in result}
    assert EXPECTED_TABLES <= names


async def test_friendships_table_absent(db_engine):
    """#2: friendshipsはMVP対象外のため存在しない(05 §1〜2・02 v0.4)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'friendships'"
            )
        )
        assert result.first() is None


async def test_intents_indexes_exist_with_partial_where(db_engine):
    """#3: 05 §3のIndex 7本が存在。部分IndexのWHERE句・opsまで確認。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
            )
        )
        defs = {row[0]: row[1] for row in result}
    for name in (
        "idx_intents_matching",
        "idx_intents_geo",
        "idx_intents_budget",
        "idx_intents_participants",
        "idx_intents_expires",
        "idx_intents_user",
        "idx_intents_embedding",
    ):
        assert name in defs, f"{name} が存在しない"
    # 部分IndexのWHERE句(05 §3。indexdefの内部正規化に強い部分一致で見る)
    assert "status = 'active'" in defs["idx_intents_matching"]
    assert "status = 'active'" in defs["idx_intents_budget"]
    assert "status = 'active'" in defs["idx_intents_participants"]
    # PostgreSQL 17はINを ANY (ARRAY[...]) へdeparseするため両形式を受け入れる
    expires_def = defs["idx_intents_expires"].replace(" ", "")
    assert (
        "statusIN('draft'" in expires_def
        or "status=ANY(ARRAY['draft'::text" in expires_def
    )
    # ops(pgvector)
    assert "vector_cosine_ops" in defs["idx_intents_embedding"]


async def test_index_access_methods(db_engine):
    """#3: GIST(geo)とHNSW(embedding)がpg_am上正しい(design §4.2 #3)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT i.relname, am.amname FROM pg_index x "
                "JOIN pg_class i ON i.oid = x.indexrelid "
                "JOIN pg_class t ON t.oid = x.indrelid "
                "JOIN pg_am am ON am.oid = i.relam "
                "WHERE t.relname = 'intents'"
            )
        )
        methods = {row[0]: row[1] for row in result}
    assert methods["idx_intents_geo"] == "gist"
    assert methods["idx_intents_embedding"] == "hnsw"


async def test_match_events_idempotency_is_expression_unique_index(db_engine):
    """#4: ux_match_events_idempotencyが式UNIQUE索引として存在(06 §9手順2)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND indexname = 'ux_match_events_idempotency'"
            )
        )
        (indexdef,) = result.one()
    normalized = indexdef.replace(" ", "")
    assert "CREATEUNIQUEINDEX" in normalized
    assert "payload->>'version'" in normalized


async def test_expected_foreign_keys(db_engine):
    """#8: 05 §1 ERどおりの14本のFKが存在。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'f' AND connamespace = 'public'::regnamespace"
            )
        )
        names = {row[0] for row in result}
    assert EXPECTED_FKS <= names


async def test_match_events_has_no_foreign_key(db_engine):
    """#8: match_eventsにはFKが無い
    (削除済みIntentへの遅延Event保存の担保。design §2.6)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT conname FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE c.contype = 'f' AND t.relname = 'match_events'"
            )
        )
        assert result.first() is None


async def test_unique_constraints_exist(db_engine):
    """design §2.10: UNIQUE制約2件(users認証キー・候補バージョン組)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'u' AND connamespace = 'public'::regnamespace"
            )
        )
        names = {row[0] for row in result}
    assert EXPECTED_UNIQUE_CONSTRAINTS <= names
