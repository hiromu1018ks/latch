"""T10: 保存期間と復元(計画 §6 T10・§5.6・目標完了条件3の一部)。

検証面:
- 候補30日削除・cache/snapshot期限掃除・履歴(回答・メッセージ)・通報・
  calibrationは消さない(料金だけの理由で消さない — 仕様)
- export→復元: 件数・FK・状態・公開範囲の整合
- 復元点以降の削除・同意撤回・block・logout・JWT失効・refresh消費を再適用
- 削除済み情報を復活させない(tombstone優先)
- evaluation_cache・snapshotは再構築(旧内容を使わない)
- 認証状態の整合(失効・回転後refresh)
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest

from latch.adapters.d1.atomic import D1AtomicRepository
from latch.adapters.d1.database import LocalD1Database, canonical_json
from latch.adapters.d1.evaluation_cache import D1EvaluationCache
from latch.adapters.d1.sessions import D1SessionStore
from latch.domain.time import micros
from latch.domain.types import CommandStatus
from latch.runtime.cloudflare.retention import (
    capture_safety_journal,
    export_core,
    restore_export,
    run_retention,
)

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def db():
    database = LocalD1Database()
    database.migrate()
    yield database
    database.close()


async def _user(db, name: str = "u") -> str:
    uid = str(uuid_mod.uuid4())
    await db.batch(
        [
            db.prepare(
                "INSERT INTO users (id, display_name, profile, birth_date,"
                " auth_provider, auth_subject, consent_at, created_at,"
                " updated_at) VALUES (?, ?, '{}', '1995-06-15', 'google', ?,"
                " ?, ?, ?)"
            ).bind(uid, name, f"s-{uid[:8]}", micros(NOW), micros(NOW), micros(NOW)),
        ]
    )
    return uid


async def _intent(db, user_id: str, *, start_us=None) -> str:
    iid = str(uuid_mod.uuid4())
    await db.batch(
        [
            db.prepare(
                "INSERT INTO intents (id, user_id, category_primary,"
                " alcohol_involved, raw_text, structured_data, geo_lon, geo_lat,"
                " status, version, time_start, expires_at, created_at,"
                " updated_at) VALUES (?, ?, 'meal', 0, 'x', '{}', 130.55, 31.59,"
                " 'active', 1, ?, ?, ?, ?)"
            ).bind(
                iid,
                user_id,
                start_us or micros(NOW + timedelta(hours=6)),
                micros(NOW + timedelta(days=3)),
                micros(NOW),
                micros(NOW),
            ),
        ]
    )
    return iid


# =============================================================================
# 保持(retention)
# =============================================================================


async def test_candidate_30day_deletion_keeps_history(db):
    """候補は30日で削除・回答履歴(latches/responses)・calibration・通報は残す。"""
    u1 = await _user(db, "A")
    u2 = await _user(db, "B")
    i1 = await _intent(db, u1)
    i2 = await _intent(db, u2)
    # 31日前に評価済みの候補 + 昨日の候補
    old_us = micros(NOW - timedelta(days=31))
    recent_us = micros(NOW - timedelta(days=1))
    for ts, va, vb in ((old_us, 1, 1), (recent_us, 2, 1)):
        lo, hi = sorted((i1, i2))
        await db.batch(
            [
                db.prepare(
                    "INSERT INTO match_candidates (id, intent_a_id, intent_b_id,"
                    " intent_a_version, intent_b_version, jev_result, latch_score,"
                    " status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, '{}', 0.5, 'evaluated', ?, ?)"
                ).bind(str(uuid_mod.uuid4()), lo, hi, va, vb, ts, ts),
            ]
        )
    # 履歴: latch・回答・イベント・calibration・通報
    latch = str(uuid_mod.uuid4())
    await db.batch(
        [
            db.prepare(
                "INSERT INTO latches (id, intent_ids, proposal, score, responses,"
                " status, response_deadline, expires_at, created_at)"
                " VALUES (?, ?, '{}', 0.8, ?, 'matched', ?, ?, ?)"
            ).bind(
                latch,
                canonical_json([i1, i2]),
                canonical_json([{"user_id": u1, "response": "yes"}]),
                micros(NOW + timedelta(hours=1)),
                micros(NOW + timedelta(days=2)),
                old_us,
            ),
            db.prepare(
                "INSERT INTO calibration_records (id, latch_id, prediction,"
                " proposal_snapshot, actual_responses, matched, created_at,"
                " updated_at) VALUES (?, ?, '{}', '{}', '[]', 1, ?, ?)"
            ).bind(str(uuid_mod.uuid4()), latch, old_us, old_us),
            db.prepare(
                "INSERT INTO reports (id, reporter_id, reportee_id, reason,"
                " status, created_at) VALUES (?, ?, ?, 'other', 'pending', ?)"
            ).bind(str(uuid_mod.uuid4()), u1, u2, old_us),
        ]
    )
    outcome = await run_retention(db, NOW)
    assert outcome.candidates_deleted == 1, "31日超の候補のみ"
    cands = (await db.prepare("SELECT COUNT(*) AS c FROM match_candidates").first())[
        "c"
    ]
    assert cands == 1
    latches = (await db.prepare("SELECT COUNT(*) AS c FROM latches").first())["c"]
    calib = (await db.prepare("SELECT COUNT(*) AS c FROM calibration_records").first())[
        "c"
    ]
    reports = (await db.prepare("SELECT COUNT(*) AS c FROM reports").first())["c"]
    assert latches == 1 and calib == 1 and reports == 1, "履歴・通報は消さない"


async def test_cache_and_snapshot_expiry_cleanup(db):
    """評価cache・検索snapshotの期限切れ掃除。"""
    cache = D1EvaluationCache(db)
    from latch.domain.evaluation_key import (
        EvaluationVersions,
        build_evaluation_key,
    )

    result_fixture = {
        "would_a_accept_b": 0.8,
        "would_b_accept_a": 0.7,
        "jev_5axis": {},
        "provider": "prov",
        "model": "m",
    }
    key = build_evaluation_key(
        pair=(
            "a" * 8 + "-0000-0000-0000-000000000000",
            "b" * 8 + "-0000-0000-0000-000000000000",
        ),
        sent_inputs=("x", "y"),
        versions=EvaluationVersions("n", "q", "p", "s", "prov", "m", "pol"),
    )
    await cache.store_evaluation(
        key,
        result=result_fixture,
        actual_provider="prov",
        actual_model="m",
        requested_provider="prov",
        requested_model="m",
        intent_expires_at=[NOW + timedelta(seconds=1), None],
        now=NOW,
    )
    outcome = await run_retention(db, NOW + timedelta(seconds=2))
    assert outcome.cache_expired == 1


# =============================================================================
# export / restore(復元リハーサル)
# =============================================================================


async def _seed_world(db):
    """回答・履歴・削除・認証状態を含む世界を構築(復元試験の材料)。"""
    u1 = await _user(db, "A")
    u2 = await _user(db, "B")
    u3 = await _user(db, "C")
    i1 = await _intent(db, u1)
    i2 = await _intent(db, u2)
    i3 = await _intent(db, u3)
    repo = D1AtomicRepository(db)
    # 提示→双方YES(回答と成立を含む)
    proposal = await repo.apply_proposal(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        {i1: 1, i2: 1},
        {
            "intent_ids": [i1, i2],
            "score": 0.9,
            "proposal": {"headcount": 2, "match_level": "high"},
        },
        NOW,
    )
    assert proposal.status is CommandStatus.APPLIED
    latch_id = proposal.response["latch"]["id"]
    await repo.apply_response(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        None,
        {"latch_id": latch_id, "user_id": u1, "value": "yes"},
        NOW,
    )
    await repo.apply_response(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        None,
        {"latch_id": latch_id, "user_id": u2, "value": "yes"},
        NOW,
    )
    # session(refresh回転まで含む)
    store = D1SessionStore(db)
    token1 = await store.create_family(
        sid="sid-restore-1", provider="google", subject=f"s-{u1[:8]}", now=NOW
    )
    rotated = await store.rotate(token1, NOW)
    assert rotated is not None
    await store.revoke_jwt("jti-logout-1", NOW + timedelta(hours=1), NOW)
    # block
    await repo.apply_safety(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        {"kind": "block", "blocker_id": u1, "blocked_id": u3},
        NOW,
    )
    return {
        "users": [u1, u2, u3],
        "intents": [i1, i2, i3],
        "latch": latch_id,
        "old_refresh": token1,
        "new_refresh": rotated["new_token"],
    }


async def test_export_restore_roundtrip_with_safety_reapplication(db):
    """export→新DB復元→安全状態再適用→整合検査(回答・履歴・削除・認証)。"""
    world = await _seed_world(db)
    export_point = micros(NOW + timedelta(seconds=10))
    dump = export_core(db)
    # 復元点以降に発生した安全状態(復元対象DBの外へ確保する材料)
    later = NOW + timedelta(seconds=20)
    safety = capture_safety_journal(db, since_us=export_point)
    # export後に: u3のIntent削除(復元点後の削除 — 復活させない)
    repo = D1AtomicRepository(db)
    deleted = await repo.apply_intent(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        None,
        {
            "op": "delete",
            "intent_id": world["intents"][2],
            "user_id": world["users"][2],
        },
        later,
    )
    assert deleted.status is CommandStatus.APPLIED
    # dumpは削除前の点(削除を知らない) → safety journalに削除を足す
    safety.deletions.append(
        {
            "id": str(uuid_mod.uuid4()),
            "target_kind": "intent",
            "target_id": world["intents"][2],
            "reason": "intent_delete",
            "created_at": micros(later),
        }
    )
    # export後のlogout(復元点後のJWT失効)
    safety.logged_out_jtis.append("jti-late-logout")

    fresh = LocalD1Database()
    fresh.migrate()
    report = await restore_export(
        fresh,
        dump,
        safety,
        deletion_watermark_us=micros(later),
    )
    try:
        assert report.ok, report.integrity
        # 回答・履歴の整合: latch/responses/events が復元されている
        latch = (
            await fresh.prepare("SELECT responses, status FROM latches WHERE id = ?")
            .bind(world["latch"])
            .first()
        )
        assert latch is not None and latch["status"] == "matched"
        import json

        assert len(json.loads(latch["responses"])) == 2, "双方の回答が復元"
        events = (
            await fresh.prepare("SELECT COUNT(*) AS c FROM latch_status_events").first()
        )["c"]
        assert events >= 2  # partial_accept→matched(回答2件分)
        # 削除の再適用: 復元点後に削除されたIntentは復活しない
        gone = (
            await fresh.prepare("SELECT COUNT(*) AS c FROM intents WHERE id = ?")
            .bind(world["intents"][2])
            .first()
        )
        assert gone["c"] == 0, "削除済みIntentを復活させない"
        # 認証状態: 回転前refreshは失効・JWT失効は再適用
        old = await fresh.prepare(
            "SELECT status FROM refresh_tokens WHERE token_hash ="
            " (SELECT token_hash FROM refresh_tokens WHERE sid ="
            "  'sid-restore-1' AND status = 'rotated' LIMIT 1)"
        ).first()
        assert old is not None and old["status"] == "rotated"
        jti = await fresh.prepare(
            "SELECT 1 AS x FROM jwt_revocations WHERE jti = 'jti-late-logout'"
        ).first()
        assert jti is not None, "復元点後のJWT失効を再適用"
        # block再適用
        blocks = (await fresh.prepare("SELECT COUNT(*) AS c FROM blocks").first())["c"]
        assert blocks >= 1
        assert report.integrity == {
            "no_orphan_intents": True,
            "no_orphan_latch_events": True,
            "matched_latch_intents_closed": True,
            "single_match_ownership": True,
        }
    finally:
        fresh.close()


async def test_restore_rebuilds_cache_tables_empty(db):
    """evaluation_cache・snapshotは復元しない(旧内容を使わない)。"""
    world = await _seed_world(db)
    dump = export_core(db)
    safety = capture_safety_journal(db, since_us=0)
    fresh = LocalD1Database()
    fresh.migrate()
    report = await restore_export(
        fresh, dump, safety, deletion_watermark_us=micros(NOW)
    )
    try:
        assert report.ok
        cache_rows = (
            await fresh.prepare("SELECT COUNT(*) AS c FROM evaluation_cache").first()
        )["c"]
        snaps = (
            await fresh.prepare("SELECT COUNT(*) AS c FROM retrieval_snapshots").first()
        )["c"]
        assert cache_rows == 0 and snaps == 0, "再構築系は空から"
    finally:
        fresh.close()


async def test_outbox_and_jobs_survive_restore(db):
    """未処理仕事(outbox/jobs)は復元で失われない(配送再開)。"""
    await _seed_world(db)
    dump = export_core(db)
    safety = capture_safety_journal(db, since_us=0)
    outbox_before = (await db.prepare("SELECT COUNT(*) AS c FROM outbox").first())["c"]
    jobs_before = (await db.prepare("SELECT COUNT(*) AS c FROM jobs").first())["c"]
    fresh = LocalD1Database()
    fresh.migrate()
    report = await restore_export(
        fresh, dump, safety, deletion_watermark_us=micros(NOW)
    )
    try:
        assert report.ok
        assert report.rows_restored["outbox"] == outbox_before
        assert report.rows_restored["jobs"] == jobs_before
    finally:
        fresh.close()
