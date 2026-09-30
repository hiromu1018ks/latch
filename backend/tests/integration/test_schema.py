"""スキーマ実体の検証 — design.md §4.2対応表の9検証のうちカタログ検証(#1〜#4・#8)。

05 §2〜§3・06 §9 の要素が実DBへ反映済みであることを情報スキーマ/カタログで証明する。
挙動検証(#5〜#7・#9)は後段のタスクでこのファイルへ追記する。
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from latch.core.db import create_db_engine
from latch.settings import Settings

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


# --- 挙動検証(design §4.2 #5〜#7・#9)。時刻は固定リテラル(決定性) ---

TS = "TIMESTAMPTZ '2026-09-27 12:00:00+00'"


async def _sweep_fixed_ts_rows() -> None:
    """このファイルが固定TSで挿入・COMMITした行をFK依存の葉→根で削除する
    (ws-8 supervisor追加指示・design §2.7対抗策実証のため)。

    挿入行はlatches/group_candidatesの構造的孤立行(intent_idsが参照先不在の
    ランダムuuid)とmatch_eventsの毒payload行({})で、放置するとci-dbへ
    累積し検証手順4(孤立行0件)を成立不能にする。全INSERTが固定TSリテラルを
    使うため created_at = TS固定値 で一意に識別できる(rollbackするCHECK試験は
    行を残さないため影響なし)。latches→group_candidatesの順は
    fk_latches_group_candidate(latchesがgroup_candidatesを参照)のため。
    """
    engine = create_db_engine(Settings())
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(f"DELETE FROM calibration_records WHERE created_at = {TS}")
            )
            await conn.execute(
                text(
                    "DELETE FROM latch_status_events WHERE latch_id IN"
                    f" (SELECT id FROM latches WHERE created_at = {TS})"
                )
            )
            await conn.execute(
                text(
                    "DELETE FROM notifications WHERE user_id IN"
                    f" (SELECT id FROM users WHERE created_at = {TS})"
                )
            )
            await conn.execute(text(f"DELETE FROM latches WHERE created_at = {TS}"))
            await conn.execute(
                text(f"DELETE FROM group_candidates WHERE created_at = {TS}")
            )
            await conn.execute(
                text(f"DELETE FROM match_events WHERE created_at = {TS}")
            )
            await conn.execute(text(f"DELETE FROM intents WHERE created_at = {TS}"))
            await conn.execute(text(f"DELETE FROM users WHERE created_at = {TS}"))
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _sweep_fixed_ts_rows_on_exit():
    """ファイル内全試験終了後に固定TS行を掃除(autouse・試験本体は無変更)。"""
    yield
    asyncio.run(_sweep_fixed_ts_rows())


# バインドパラメータ用の同instant(asyncpgのtimestamptzバインドはdatetime要求)
ANON_TS = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)

VEC768 = "[" + ",".join(["0.001"] * 768) + "]"

INSERT_LATCH = (
    "INSERT INTO latches "
    "(id, intent_ids, proposal, score, status, response_deadline, expires_at, "
    "created_at) "
    "VALUES (CAST(:id AS uuid), CAST(:ids AS uuid[]), CAST(:proposal AS jsonb), "
    f"0.85, 'candidate', {TS}, {TS}, {TS})"
)

INSERT_CALIBRATION = (
    "INSERT INTO calibration_records "
    "(latch_id, intent_ids, prediction, proposal_snapshot, actual_responses, "
    "matched, anonymized_at, created_at, updated_at) "
    "VALUES (CAST(:latch_id AS uuid), CAST(:intent_ids AS uuid[]), "
    f"CAST(:prediction AS jsonb), CAST(:snapshot AS jsonb), "
    f"CAST(:responses AS jsonb), true, CAST(:anonymized AS timestamptz), {TS}, {TS})"
)


def _uuid_array(count: int) -> list[uuid.UUID]:
    """uuid[] バインド値を新規UUIDで組み立てる。

    asyncpgはuuid[]パラメータに文字列リテラル('{u1,u2,...}')ではなく
    シーケンス(list[uuid.UUID])を要求する(Review Focus #3の変種。
    CAST(:x AS uuid[]) を付けても文字列は「sized iterable expected」で拒否)。
    """
    return [uuid.uuid4() for _ in range(count)]


async def insert_user(conn, auth_provider: str = "google") -> str:
    """usersの最小行(DEFAULT検証のためprofileは省略)。user_id(uuid文字列)を返す。"""
    user_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO users "
            "(id, display_name, birth_date, auth_provider, auth_subject, "
            "created_at, updated_at) "
            f"VALUES (CAST(:id AS uuid), 'test', DATE '2000-01-01', :provider, "
            f":subject, {TS}, {TS})"
        ),
        {"id": user_id, "provider": auth_provider, "subject": f"sub-{user_id}"},
    )
    return user_id


async def insert_minimal_intent(conn, user_id: str) -> str:
    """intentsの最小行(05明記DEFAULT列はすべて省略)。intent_id(uuid文字列)を返す。"""
    intent_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO intents "
            "(id, user_id, category_primary, alcohol_involved, raw_text, status, "
            "created_at, updated_at) "
            f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, 'raw', "
            f"'draft', {TS}, {TS})"
        ),
        {"id": intent_id, "uid": user_id},
    )
    return intent_id


async def insert_latch(conn) -> str:
    """latchesの最小行(responsesはDEFAULT検証のため省略)。latch_idを返す。"""
    latch_id = str(uuid.uuid4())
    await conn.execute(
        text(INSERT_LATCH),
        {"id": latch_id, "ids": _uuid_array(2), "proposal": "{}"},
    )
    return latch_id


async def _insert_match_event(conn, sid: str, payload: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO match_events "
            "(event_type, source_intent_id, payload, status, created_at) "
            f"VALUES ('create', CAST(:sid AS uuid), CAST(:payload AS jsonb), "
            f"'pending', {TS})"
        ),
        {"sid": sid, "payload": payload},
    )


# --- #5: UNIQUE実効(06 §9手順2) ---


async def test_match_events_duplicate_version_rejected(db_engine):
    """同一(event_type, source_intent_id, version)の2行目INSERTは拒否される。"""
    sid = str(uuid.uuid4())
    async with db_engine.connect() as conn:
        await _insert_match_event(conn, sid, '{"version": 1}')
        await conn.commit()
        with pytest.raises(IntegrityError):
            await _insert_match_event(conn, sid, '{"version": 1}')
        await conn.rollback()


async def test_match_events_different_version_allowed(db_engine):
    """異なるversionのEventは同一source_intent_idでも保存できる。"""
    sid = str(uuid.uuid4())
    async with db_engine.connect() as conn:
        await _insert_match_event(conn, sid, '{"version": 1}')
        await _insert_match_event(conn, sid, '{"version": 2}')
        await conn.commit()


async def test_match_events_missing_version_duplicates_allowed(db_engine):
    """version欠落(NULL)はUNIQUE対象外 — 毒ペイロードの
    隔離保管経路の担保(design §2.5)。"""
    sid = str(uuid.uuid4())
    async with db_engine.connect() as conn:
        await _insert_match_event(conn, sid, "{}")
        await _insert_match_event(conn, sid, "{}")
        await conn.commit()


# --- #6: CHECK実効(05 §2明記分のみ) ---


async def test_users_auth_provider_check(db_engine):
    """auth_provider不正値は拒否(05 §2)。"""
    async with db_engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, display_name, birth_date, auth_provider, auth_subject, "
                    "created_at, updated_at) "
                    f"VALUES (CAST(:id AS uuid), 't', DATE '2000-01-01', 'twitter', "
                    f"'s', {TS}, {TS})"
                ),
                {"id": str(uuid.uuid4())},
            )
        await conn.rollback()


async def test_intents_visibility_check(db_engine):
    """visibility不正値は拒否(05 §2)。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO intents "
                    "(id, user_id, category_primary, alcohol_involved, raw_text, "
                    "visibility, status, created_at, updated_at) "
                    f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, "
                    f"'raw', 'invalid', 'draft', {TS}, {TS})"
                ),
                {"id": str(uuid.uuid4()), "uid": user_id},
            )
        await conn.rollback()


async def test_intents_notification_level_check(db_engine):
    """notification_level不正値は拒否(05 §2)。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO intents "
                    "(id, user_id, category_primary, alcohol_involved, raw_text, "
                    "notification_level, status, created_at, updated_at) "
                    f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, "
                    f"'raw', 'invalid', 'draft', {TS}, {TS})"
                ),
                {"id": str(uuid.uuid4()), "uid": user_id},
            )
        await conn.rollback()


async def test_latches_intent_ids_length_check(db_engine):
    """latches.intent_idsは2≦n≦4(05 §2)。1・5は拒否・2は成功。"""
    async with db_engine.connect() as conn:
        for count in (1, 5):
            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(INSERT_LATCH),
                    {
                        "id": str(uuid.uuid4()),
                        "ids": _uuid_array(count),
                        "proposal": "{}",
                    },
                )
            await conn.rollback()
        await insert_latch(conn)  # 2件(下限)は成功
        await conn.commit()


async def test_group_candidates_intent_ids_length_check(db_engine):
    """group_candidates.intent_idsは3≦n≦4(05 §2)。2・5は拒否・3は成功。"""
    insert = (
        "INSERT INTO group_candidates (intent_ids, status, created_at, updated_at) "
        f"VALUES (CAST(:ids AS uuid[]), 'candidate', {TS}, {TS})"
    )
    async with db_engine.connect() as conn:
        for count in (2, 5):
            with pytest.raises(IntegrityError):
                await conn.execute(text(insert), {"ids": _uuid_array(count)})
            await conn.rollback()
        await conn.execute(text(insert), {"ids": _uuid_array(3)})  # 下限は成功
        await conn.commit()


async def test_calibration_records_anonymization_check(db_engine):
    """anonymized_at NOT NULLならlatch_id・intent_idsはNULL(05 §2)。"""
    async with db_engine.connect() as conn:
        latch_id = await insert_latch(conn)
        base = {
            "intent_ids": None,
            "prediction": "{}",
            "snapshot": "{}",
            "responses": "[]",
        }
        # 未匿名化(latch_idあり)はOK
        await conn.execute(
            text(INSERT_CALIBRATION),
            {"latch_id": latch_id, "anonymized": None, **base},
        )
        # 匿名化済み(ID系NULL)はOK
        await conn.execute(
            text(INSERT_CALIBRATION),
            {"latch_id": None, "anonymized": ANON_TS, **base},
        )
        await conn.commit()
        # 匿名化済みなのにlatch_idが残るのは違反
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(INSERT_CALIBRATION),
                {"latch_id": latch_id, "anonymized": ANON_TS, **base},
            )
        await conn.rollback()


# --- #7: 型実効(geography・vector。05 §2・§3) ---


async def test_geography_point_insert_and_dwithin(db_engine):
    """geography(Point,4326)へのINSERT可・ST_DWithinが正しく距離判定する。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        intent_id = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO intents "
                "(id, user_id, category_primary, alcohol_involved, raw_text, "
                "geo_center, geo_radius_m, status, created_at, updated_at) "
                f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, "
                f"'raw', ST_SetSRID(ST_MakePoint(139.767, 35.681), 4326), 1000, "
                f"'draft', {TS}, {TS})"
            ),
            {"id": intent_id, "uid": user_id},
        )
        await conn.commit()
        near = (
            await conn.execute(
                text(
                    "SELECT ST_DWithin(geo_center, "
                    "ST_SetSRID(ST_MakePoint(139.767, 35.681), 4326), 500) "
                    "FROM intents WHERE id = CAST(:id AS uuid)"
                ),
                {"id": intent_id},
            )
        ).scalar()
        far = (
            await conn.execute(
                text(
                    "SELECT ST_DWithin(geo_center, "
                    "ST_SetSRID(ST_MakePoint(139.78, 35.69), 4326), 500) "
                    "FROM intents WHERE id = CAST(:id AS uuid)"
                ),
                {"id": intent_id},
            )
        ).scalar()
    assert near is True  # 同一地点(0m)は500m圏内
    assert far is False  # 約1.5km離れた地点は圏外


async def test_vector_insert_and_cosine_distance(db_engine):
    """vector(768)へのINSERT可・cosine距離演算(<=>)が動く。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        intent_id = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO intents "
                "(id, user_id, category_primary, alcohol_involved, raw_text, "
                "embedding, embedding_model, status, created_at, updated_at) "
                f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, "
                f"'raw', CAST(:vec AS vector), 'test-model/v1', 'draft', {TS}, {TS})"
            ),
            {"id": intent_id, "uid": user_id, "vec": VEC768},
        )
        await conn.commit()
        distance = (
            await conn.execute(
                text(
                    "SELECT embedding <=> CAST(:q AS vector) "
                    "FROM intents WHERE id = CAST(:id AS uuid)"
                ),
                {"q": VEC768, "id": intent_id},
            )
        ).scalar()
    assert distance is not None
    assert distance == 0.0  # 同一ベクトルとのcosine距離は0


async def test_vector_wrong_dimensions_rejected(db_engine):
    """768以外の次元はINSERT失敗(型のtypmod固定)。"""
    vec767 = "[" + ",".join(["0.001"] * 767) + "]"
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        with pytest.raises(DBAPIError) as excinfo:
            await conn.execute(
                text(
                    "INSERT INTO intents "
                    "(id, user_id, category_primary, alcohol_involved, raw_text, "
                    "embedding, status, created_at, updated_at) "
                    f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, "
                    f"'raw', CAST(:vec AS vector), 'draft', {TS}, {TS})"
                ),
                {"id": str(uuid.uuid4()), "uid": user_id, "vec": vec767},
            )
        await conn.rollback()
    assert "dimension" in str(excinfo.value).lower()


# --- #9: DEFAULT実効(05明記分のみ。design §2.8) ---


async def test_users_profile_default(db_engine):
    """profile省略で '{}' が入る(05 §2)。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        profile = (
            await conn.execute(
                text("SELECT profile FROM users WHERE id = CAST(:id AS uuid)"),
                {"id": user_id},
            )
        ).scalar()
    assert profile == {}


async def test_intents_defaults(db_engine):
    """structured_data/participants/visibility/notification_level/versionの
    DEFAULT(05 §2)。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        intent_id = await insert_minimal_intent(conn, user_id)
        row = (
            await conn.execute(
                text(
                    "SELECT structured_data, participants_min, participants_max, "
                    "visibility, notification_level, version "
                    "FROM intents WHERE id = CAST(:id AS uuid)"
                ),
                {"id": intent_id},
            )
        ).one()
    assert row[0] == {}
    assert (row[1], row[2]) == (2, 2)
    assert row[3] == "hidden_until_match"
    assert row[4] == "proposals_only"
    assert row[5] == 1


async def test_latches_and_group_candidates_defaults(db_engine):
    """latches.responses='[]' / group_candidates.member_scores='{}'(05 §2)。"""
    async with db_engine.connect() as conn:
        latch_id = await insert_latch(conn)
        responses = (
            await conn.execute(
                text("SELECT responses FROM latches WHERE id = CAST(:id AS uuid)"),
                {"id": latch_id},
            )
        ).scalar()
        gc_insert = (
            "INSERT INTO group_candidates "
            "(id, intent_ids, status, created_at, updated_at) "
            f"VALUES (CAST(:id AS uuid), CAST(:ids AS uuid[]), 'candidate', {TS}, {TS})"
        )
        gc_id = str(uuid.uuid4())
        await conn.execute(text(gc_insert), {"id": gc_id, "ids": _uuid_array(3)})
        member_scores = (
            await conn.execute(
                text(
                    "SELECT member_scores FROM group_candidates "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": gc_id},
            )
        ).scalar()
    assert responses == []
    assert member_scores == {}


async def test_created_at_has_no_db_default(db_engine):
    """created_at省略はNOT NULL違反 — 時刻はClock由来の明示値のみ(design §2.8)。"""
    async with db_engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, display_name, birth_date, auth_provider, auth_subject, "
                    f"updated_at) "
                    f"VALUES (CAST(:id AS uuid), 't', DATE '2000-01-01', 'google', "
                    f"'s', {TS})"
                ),
                {"id": str(uuid.uuid4())},
            )
        await conn.rollback()
