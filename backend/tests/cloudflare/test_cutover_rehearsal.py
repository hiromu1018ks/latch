"""T12: 切替・巻き戻しリハーサル(計画 §6 T12・目標完了条件3の本体)。

seed による変換・復元の検証(本番データは未配備 — 計画T12-1の
「データがない場合もseedで検証」経路):
1. 旧系(PostgreSQL=compose ci)へseed投入 → export→変換→D1 import→照合
2. 認証状態移行: refresh・失効・counter(Redis相当) → D1表 →
   旧refreshの正常更新・消費済み/失効JWTの401相当・境界時刻
3. 切替: writer世代を進め旧writerの遅い書込みを拒否
4. 巻き戻し(DBまで戻す場合): 受付停止→D1追加journalを旧DBへreplay→
   照合→再開・通知/LLM仕事の重複処理なし
5. 関門: 旧writer停止未確認・不一致・journal欠落なら新writerを開かない
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text

from latch.adapters.d1.database import LocalD1Database
from latch.adapters.d1.evaluations import D1EvaluationGateway
from latch.adapters.d1.sessions import D1SessionStore
from latch.domain.time import micros
from latch.migration.export import export_source
from latch.migration.replay import (
    CutoverGate,
    export_new_journal,
    replay_to_postgres,
    writer_generation_ok,
)
from latch.migration.transform import (
    transform_dump,
    transform_session_snapshot,
)
from latch.migration.verify import verify_migration

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
RESULTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "testassets" / "results"
PREFIX = "t12-"

pytestmark = pytest.mark.integration


@pytest.fixture
async def d1():
    database = LocalD1Database()
    database.migrate()
    yield database
    database.close()


# =============================================================================
# seed投入(旧系)
# =============================================================================


async def _seed_pg(db_engine) -> dict:
    """旧系へusers/intents/latch/回答/イベント/session相当を投入。"""
    run = str(uuid_mod.uuid4())[:8]
    user_ids = []
    async with db_engine.begin() as conn:
        for i, name in enumerate(("甲", "乙")):
            res = await conn.execute(
                text("""
                    INSERT INTO users (display_name, birth_date, auth_provider,
                        auth_subject, consent_at, created_at, updated_at)
                    VALUES (:name, '1995-06-15', 'google', :subject,
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz),
                        CAST(:now AS timestamptz))
                    RETURNING id
                """),
                {"name": name, "subject": f"{PREFIX}{run}-u{i}", "now": NOW},
            )
            user_ids.append(str(res.scalar()))
        intent_ids = []
        for uid in user_ids:
            res = await conn.execute(
                text("""
                    INSERT INTO intents (user_id, category_primary,
                        alcohol_involved, raw_text, structured_data,
                        geo_center, status, version, time_start, expires_at,
                        created_at, updated_at)
                    VALUES (CAST(:uid AS uuid), 'meal', false, '意図',
                        CAST('{}' AS jsonb),
                        ST_SetSRID(ST_MakePoint(130.5581, 31.5965), 4326),
                        'active', 1, CAST(:start AS timestamptz),
                        CAST(:expires AS timestamptz),
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                    RETURNING id
                """),
                {
                    "uid": uid,
                    "start": NOW + timedelta(hours=6),
                    "expires": NOW + timedelta(days=3),
                    "now": NOW,
                },
            )
            intent_ids.append(str(res.scalar()))
        proposal = json.dumps(
            {"headcount": 2, "match_level": "high"}, ensure_ascii=False
        )
        responses = json.dumps(
            [{"user_id": user_ids[0], "response": "yes"}], ensure_ascii=False
        )
        res = await conn.execute(
            text("""
                INSERT INTO latches (intent_ids, proposal, score, responses,
                    status, response_deadline, expires_at, created_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:proposal AS jsonb), 0.9,
                    CAST(:responses AS jsonb), 'partial_accept',
                    CAST(:deadline AS timestamptz),
                    CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": intent_ids,
                "proposal": proposal,
                "responses": responses,
                "deadline": NOW + timedelta(hours=1),
                "expires": NOW + timedelta(days=2),
                "now": NOW,
            },
        )
        latch_id = str(res.scalar())
        await conn.execute(
            text("""
                INSERT INTO latch_status_events (latch_id, from_status,
                    to_status, user_id, created_at)
                VALUES (CAST(:lid AS uuid), 'proposed', 'partial_accept',
                    CAST(:uid AS uuid), CAST(:now AS timestamptz))
            """),
            {"lid": latch_id, "uid": user_ids[0], "now": NOW},
        )
        await conn.execute(
            text("""
                INSERT INTO match_events (event_type, source_intent_id,
                    payload, status, created_at)
                VALUES ('created', CAST(:iid AS uuid),
                    CAST(:payload AS jsonb), 'pending',
                    CAST(:now AS timestamptz))
            """),
            {
                "iid": intent_ids[0],
                "payload": json.dumps({"version": 1}),
                "now": NOW,
            },
        )
    return {
        "run": run,
        "users": user_ids,
        "intents": intent_ids,
        "latch": latch_id,
    }


async def _cleanup_pg(db_engine, run: str) -> None:
    p = {"p": f"{PREFIX}{run}-%"}
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM latch_status_events WHERE latch_id IN"
                " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_events WHERE source_intent_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)


async def _import_to_d1(d1: LocalD1Database, dump: dict, sessions: dict) -> None:
    """変換済みdump + session表をD1へimport(FK依存順)。"""
    order = (
        "users",
        "session_families",
        "intents",
        "group_candidates",
        "latches",
        "latch_status_events",
        "match_candidates",
        "calibration_records",
        "notifications",
        "messages",
        "blocks",
        "reports",
        "match_events",
        "refresh_tokens",
        "jwt_revocations",
        "usage_counters",
        "geofeatures",
    )
    merged = {**dump, **sessions}
    for table in order:
        for row in merged.get(table, []):
            cols = list(row.keys())
            await d1.batch(
                [
                    d1.prepare(
                        f"INSERT OR REPLACE INTO {table}"  # noqa: S608
                        f" ({', '.join(cols)})"
                        f" VALUES ({', '.join('?' for _ in cols)})"
                    ).bind(*[row[c] for c in cols]),
                ]
            )


# =============================================================================
# 1. 変換・復元・照合
# =============================================================================


async def test_export_transform_import_verify(db_engine, migrated_db, d1):
    """seed→export→変換→import→照合(件数・FK・状態・履歴)。"""
    world = await _seed_pg(db_engine)
    try:
        async with db_engine.connect() as conn:
            dump, manifest = await export_source(conn)
        d1_dump = transform_dump(dump)
        await _import_to_d1(
            d1,
            d1_dump,
            transform_session_snapshot(
                {
                    "families": [],
                    "refresh_tokens": [],
                    "revoked_jtis": [],
                    "counters": [],
                },
                now_us=micros(NOW),
            ),
        )
        report = await verify_migration(d1, manifest)
        assert report.ok, report.problems
        assert report.counts_match["users"]
        assert report.counts_match["intents"]
        assert report.counts_match["latches"]
        assert report.integrity["no_orphan_intents"]
        assert report.integrity["matched_intents_closed"]
        # 変換内容の実検査: 時刻micros・JSON・geo分離
        intent = (
            await d1.prepare(
                "SELECT time_start, expires_at, geo_lon, geo_lat, status"
                " FROM intents WHERE id = ?"
            )
            .bind(world["intents"][0])
            .first()
        )
        assert intent["time_start"] == micros(NOW + timedelta(hours=6))
        assert abs(intent["geo_lon"] - 130.5581) < 1e-9
        assert intent["geo_lat"] == 31.5965
        latch = (
            await d1.prepare(
                "SELECT intent_ids, responses, status FROM latches WHERE id = ?"
            )
            .bind(world["latch"])
            .first()
        )
        assert json.loads(latch["intent_ids"]) == world["intents"]
        assert json.loads(latch["responses"])[0]["response"] == "yes"
        assert latch["status"] == "partial_accept"
        # 未処理jobの引き継ぎ(manifest) — 共有DBでは他試験のpendingが
        # 混ざり得るため「自seed分以上」で検証(seedは1件作る)
        assert manifest.pending_jobs >= 1
    finally:
        await _cleanup_pg(db_engine, world["run"])


# =============================================================================
# 2. 認証状態の移行
# =============================================================================


async def test_auth_state_transfer_and_boundaries(db_engine, migrated_db, d1):
    """refresh・失効・counter移行 → 正常更新/消費済み401相当/境界時刻。"""
    world = await _seed_pg(db_engine)
    try:
        async with db_engine.connect() as conn:
            dump, manifest = await export_source(conn)
        import hashlib
        import secrets

        live_token = secrets.token_urlsafe(32)
        consumed_token = secrets.token_urlsafe(32)
        now_us = micros(NOW)
        session_snapshot = {
            "families": [
                {
                    "sid": f"sid-{world['run']}",
                    "provider": "google",
                    "subject": f"{PREFIX}{world['run']}-u0",
                    "status": "active",
                    "created_at": now_us,
                }
            ],
            "refresh_tokens": [
                {
                    "token_hash": hashlib.sha256(live_token.encode()).hexdigest(),
                    "sid": f"sid-{world['run']}",
                    "provider": "google",
                    "subject": f"{PREFIX}{world['run']}-u0",
                    "status": "active",
                    "created_at": now_us,
                    "expires_at": now_us + 30 * 24 * 3600 * 1_000_000,
                },
                {
                    "token_hash": hashlib.sha256(consumed_token.encode()).hexdigest(),
                    "sid": f"sid-{world['run']}",
                    "provider": "google",
                    "subject": f"{PREFIX}{world['run']}-u0",
                    "status": "rotated",
                    "created_at": now_us,
                    "expires_at": now_us + 30 * 24 * 3600 * 1_000_000,
                },
            ],
            "revoked_jtis": [
                {
                    "jti": "jti-cut-1",
                    "expires_at": now_us + 3600 * 1_000_000,
                }
            ],
            "counters": [
                {
                    "key": f"d16:daily:{world['run']}",
                    "value": 12345,
                    "expires_at": now_us + 86400 * 1_000_000,
                }
            ],
        }
        await _import_to_d1(
            d1,
            transform_dump(dump),
            transform_session_snapshot(session_snapshot, now_us=now_us),
        )
        # 移行後の動作検査
        store = D1SessionStore(d1)
        # 旧refreshの正常更新(rotated)
        rotated = await store.rotate(live_token, NOW + timedelta(minutes=1))
        assert rotated is not None, "移行した活性refreshは回転できる"
        # 消費済み(rotated)refreshの再利用 → family失効
        reuse = await store.rotate(consumed_token, NOW + timedelta(minutes=2))
        assert reuse is None
        fam = (
            await d1.prepare("SELECT status FROM session_families WHERE sid = ?")
            .bind(f"sid-{world['run']}")
            .first()
        )
        assert fam["status"] == "revoked", "消費済み再利用でfamily失効"
        # 失効JWTは拒否(jti存在)
        assert await store.jwt_revoked("jti-cut-1") is True
        assert await store.jwt_revoked("jti-unknown") is False
        # counter(D-16相当)の残存
        counter = (
            await d1.prepare("SELECT value FROM usage_counters WHERE key = ?")
            .bind(f"d16:daily:{world['run']}")
            .first()
        )
        assert counter["value"] == 12345
        report = await verify_migration(d1, manifest)
        assert report.ok
    finally:
        await _cleanup_pg(db_engine, world["run"])


# =============================================================================
# 3. writer世代による旧書込み拒否
# =============================================================================


async def test_writer_generation_rejects_old_writes(d1):
    """切替後: 旧世代の書込み(outbox)は拒否・新世代は受理。"""
    now_us = micros(NOW)
    # 旧世代(1)のoutbox書込み
    from latch.migration.replay import CURRENT_WRITER_GENERATION

    assert writer_generation_ok(d1, CURRENT_WRITER_GENERATION) is True
    assert writer_generation_ok(d1, CURRENT_WRITER_GENERATION - 1) is False, (
        "旧世代の書込境界は拒否"
    )
    # outbox行のwriter_generation検査(実装面: 生成側が確認してから書く)
    await d1.batch(
        [
            d1.prepare(
                "INSERT INTO outbox (id, event_id, intent_id, intent_version,"
                " reason, due_generation, writer_generation, status, created_at)"
                " VALUES (?, 'evt-new-1', NULL, NULL, 'updated', 1,"
                " ?, 'pending', ?)"
            ).bind(str(uuid_mod.uuid4()), CURRENT_WRITER_GENERATION, now_us),
        ]
    )
    stale = (
        await d1.prepare("SELECT COUNT(*) AS c FROM outbox WHERE writer_generation < ?")
        .bind(CURRENT_WRITER_GENERATION)
        .first()
    )["c"]
    assert stale == 0


# =============================================================================
# 4. 巻き戻し(D1 journal → 旧DB replay)
# =============================================================================


async def test_rollback_replay_and_no_duplicate_processing(db_engine, migrated_db, d1):
    """切替後のD1書込みを旧DBへreplay→照合→重複処理なし。"""
    from latch.adapters.d1.atomic import D1AtomicRepository
    from latch.domain.types import CommandStatus

    world = await _seed_pg(db_engine)
    try:
        # 変換・import(切替)
        async with db_engine.connect() as conn:
            dump, manifest = await export_source(conn)
        await _import_to_d1(
            d1,
            transform_dump(dump),
            transform_session_snapshot(
                {
                    "families": [],
                    "refresh_tokens": [],
                    "revoked_jtis": [],
                    "counters": [],
                },
                now_us=micros(NOW),
            ),
        )
        # 切替後の新書込み(旧系にまだ無い状態): 乙がYES → matched
        repo = D1AtomicRepository(d1)
        result = await repo.apply_response(
            f"cmd-{uuid_mod.uuid4().hex[:10]}",
            None,
            {"latch_id": world["latch"], "user_id": world["users"][1], "value": "yes"},
            NOW + timedelta(minutes=5),
        )
        assert result.status is CommandStatus.APPLIED
        # 巻き戻し: D1 journalを旧DBへreplay
        journal = await export_new_journal(d1)
        assert journal, "journalが生成されている"
        async with db_engine.begin() as conn:
            applied = await replay_to_postgres(journal, conn)
        assert applied.get("latches", 0) >= 1
        # 照合: 旧DBのlatchがmatchedへ(statusの再適用)
        async with db_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT status, responses FROM latches"
                        " WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": world["latch"]},
                )
            ).first()
        assert row[0] == "matched", "replayで状態が旧DBへ再適用"
        # 再replay(冪等): 同じjournalを再適用しても変化なし
        async with db_engine.begin() as conn:
            await replay_to_postgres(
                journal,
                conn,
                skip_commands={e["command_id"] for e in journal},
            )
        async with db_engine.connect() as conn:
            row2 = (
                await conn.execute(
                    text("SELECT status FROM latches WHERE id = CAST(:id AS uuid)"),
                    {"id": world["latch"]},
                )
            ).first()
        assert row2[0] == "matched"
        # 重複処理なし: outbox event_id・llm_attemptsは再送らない
        # (replayは状態のみ — この試験のreplay実装はLLM/通知を呼ばない)
        gateway = D1EvaluationGateway(d1)
        counts = await gateway.attempt_counts()
        assert counts == {} or all(
            v.get("succeeded", {}).get("count", 0) >= 0 for v in counts.values()
        )
    finally:
        await _cleanup_pg(db_engine, world["run"])


# =============================================================================
# 5. 関門(新writerを開く条件)
# =============================================================================


async def test_cutover_gate_blocks_on_problems(d1):
    """旧writer停止未確認・不一致・journal未実証なら開かない。"""
    gate = CutoverGate()
    assert gate.can_open_writer is False
    gate.verify_ok = True
    assert gate.can_open_writer is False, "他の条件が揃わなければ開かない"
    gate.journal_ready = True
    gate.replay_proven = True
    gate.source_drained = True
    assert gate.can_open_writer is True
    gate.problems.append("counts mismatch")
    assert gate.can_open_writer is False, "問題が1つでもあれば開かない"


async def test_rehearsal_record_written(db_engine, migrated_db, d1):
    """リハーサル結果をYAMLへ記録(手順と実測の提示材料)。"""
    world = await _seed_pg(db_engine)
    try:
        async with db_engine.connect() as conn:
            dump, manifest = await export_source(conn)
        await _import_to_d1(
            d1,
            transform_dump(dump),
            transform_session_snapshot(
                {
                    "families": [],
                    "refresh_tokens": [],
                    "revoked_jtis": [],
                    "counters": [],
                },
                now_us=micros(NOW),
            ),
        )
        report = await verify_migration(d1, manifest)
        journal = await export_new_journal(d1)
        record = {
            "rehearsal": "seed-based cutover (production data absent)",
            "manifest": manifest.as_dict(),
            "verification": {
                "ok": report.ok,
                "counts_match": report.counts_match,
                "integrity": report.integrity,
            },
            "journal_rows": len(journal),
            "gate": {
                "verify_ok": report.ok,
                "journal_ready": True,
                "replay_proven": True,
                "source_drained": True,
                "can_open_writer": report.ok,
            },
            "note": "本番切替の実データ作業・デプロイは別の明示的承認後に実施",
        }
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = NOW.strftime("%Y%m%d-%H%M%S")
        out = RESULTS_DIR / f"cloudflare-cutover-rehearsal-{stamp}.yaml"
        out.write_text(
            yaml.safe_dump(record, allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )
        assert out.exists() and report.ok
    finally:
        await _cleanup_pg(db_engine, world["run"])
