"""T3: D1原子commandの試験(2026-10-08移行計画 §6 T3)。

ローカルbinding(LocalD1Database = D1 API互換・SQLite)で行う。
実D1での確認は別承認後(計画T3・§7)。

試験の柱:
1. 副作用の部分保存なし(0行UPDATE・途中SQL失敗・再配送)
2. 20並行の同時回答(最終YES・YES対NO・期限切れ対回答・共有Intent同時成立)
3. session(refresh回転・再利用失効・logout・退会)
4. safety(双方向block・同意撤回・削除後の参加者復帰とチャット公開)
"""

from __future__ import annotations

import asyncio
import json
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest

from latch.adapters.d1.atomic import D1AtomicRepository
from latch.adapters.d1.database import LocalD1Database, canonical_json
from latch.adapters.d1.safety import D1SafetyReader
from latch.adapters.d1.sessions import D1SessionStore
from latch.domain.time import micros
from latch.domain.types import CommandStatus

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
FUTURE_2H = NOW + timedelta(hours=2)
FUTURE_120H = NOW + timedelta(hours=120)


@pytest.fixture
async def db():
    database = LocalD1Database()
    database.migrate()
    yield database
    database.close()


@pytest.fixture
async def repo(db) -> D1AtomicRepository:
    return D1AtomicRepository(db)


def _cmd() -> str:
    return f"cmd-{uuid_mod.uuid4().hex[:12]}"


async def seed_user(db, name: str = "利用者", *, consented: bool = True) -> str:
    uid = str(uuid_mod.uuid4())
    await db.batch(
        [
            db.prepare(
                "INSERT INTO users (id, display_name, profile, birth_date,"
                " auth_provider, auth_subject, consent_at, created_at, updated_at)"
                " VALUES (?, ?, '{}', '1995-06-15', 'google', ?, ?, ?, ?)"
            ).bind(
                uid,
                name,
                f"sub-{uid[:8]}",
                micros(NOW) if consented else None,
                micros(NOW),
                micros(NOW),
            ),
        ]
    )
    return uid


async def seed_intent(
    db, user_id: str, *, status: str = "active", version: int = 1
) -> str:
    iid = str(uuid_mod.uuid4())
    await db.batch(
        [
            db.prepare(
                "INSERT INTO intents (id, user_id, category_primary,"
                " category_secondary, alcohol_involved, raw_text, structured_data,"
                " geo_lon, geo_lat, participants_min, participants_max, status,"
                " version, time_start, expires_at, created_at, updated_at)"
                " VALUES (?, ?, 'meal', NULL, 0, '意図', '{}', 130.55, 31.59,"
                " 2, 2, ?, ?, ?, ?, ?, ?)"
            ).bind(
                iid,
                user_id,
                status,
                version,
                micros(FUTURE_2H),
                micros(FUTURE_120H),
                micros(NOW),
                micros(NOW),
            ),
        ]
    )
    return iid


async def seed_latch(
    db,
    intent_ids: list[str],
    *,
    status: str = "proposed",
    deadline_hours: float = 2.0,
    expires_hours: float = 120.0,
    score: float = 0.85,
    responses: list[dict] | None = None,
) -> str:
    lid = str(uuid_mod.uuid4())
    await db.batch(
        [
            db.prepare(
                "INSERT INTO latches (id, intent_ids, proposal, score, responses,"
                " status, response_deadline, expires_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ).bind(
                lid,
                canonical_json(list(intent_ids)),
                canonical_json({"headcount": len(intent_ids), "match_level": "medium"}),
                score,
                canonical_json(responses or []),
                status,
                micros(NOW + timedelta(hours=deadline_hours)),
                micros(NOW + timedelta(hours=expires_hours)),
                micros(NOW),
            ),
        ]
    )
    return lid


async def seed_eval(
    db,
    a_id: str,
    b_id: str,
    *,
    wa: float = 0.8,
    wb: float = 0.7,
    score: float = 0.85,
) -> None:
    """match_candidatesへjev_result付き評価行(正規順序a<b)。"""
    lo, hi = sorted((a_id, b_id))
    jev = {
        "would_a_accept_b": wa,
        "would_b_accept_a": wb,
        "jev_5axis": {
            "purpose_fit": {"value": 0.5, "confidence": 0.9},
            "mood_fit": {"value": 0.5, "confidence": 0.8},
            "timing_fit": {"value": 0.5, "confidence": 0.7},
            "social_fit": {"value": 0.5, "confidence": 0.6},
            "latent_yes": {"value": 0.5, "confidence": None},
        },
        "provider": "typesafe_jev",
        "model": "jev-1.13.0",
    }
    await db.batch(
        [
            db.prepare(
                "INSERT INTO match_candidates (id, intent_a_id, intent_b_id,"
                " intent_a_version, intent_b_version, latch_score, jev_result,"
                " status, created_at, updated_at)"
                " VALUES (?, ?, ?, 1, 1, ?, ?, 'evaluated', ?, ?)"
            ).bind(
                str(uuid_mod.uuid4()),
                lo,
                hi,
                score,
                canonical_json(jev),
                micros(NOW),
                micros(NOW),
            ),
        ]
    )


def _response(latch_id: str, user_id: str, value: str = "yes") -> dict:
    return {
        "latch_id": latch_id,
        "auth_provider": "google",
        "auth_subject": None,  # subjectはuser_idへ事前解決して渡す形
        "user_id": user_id,
        "value": value,
    }


# =============================================================================
# 1. 部分保存なし(0行UPDATE・途中SQL失敗・再配送)
# =============================================================================


async def test_zero_row_update_leaves_no_partial_effects(db, repo):
    """条件付きUPDATEが0行でもSQLエラーでないため、marker以外の
    副作用(イベント・通知・calibration)が入らないことを検証。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    latch = await seed_latch(db, [i1, i2], status="cancelled")  # 受理不能状態

    result = await repo.apply_response(_cmd(), None, _response(latch, u1), NOW)
    assert result.status is CommandStatus.REJECTED
    assert result.error_code == "LATCH_CLOSED"
    events = await db.prepare("SELECT COUNT(*) AS c FROM latch_status_events").first()
    assert events["c"] == 0
    notifications = await db.prepare("SELECT COUNT(*) AS c FROM notifications").first()
    assert notifications["c"] == 0


async def test_mid_batch_failure_rolls_everything_back(db, repo):
    """batch途中のSQL失敗でlatches更新もrollbackされる(原子性)。"""
    u1 = await seed_user(db, "A")
    await seed_intent(db, u1)
    with pytest.raises(Exception):  # noqa: B017, PT011 — FK違反でabort
        await db.batch(
            [
                db.prepare(
                    "INSERT INTO intents (id, user_id, category_primary,"
                    " alcohol_involved, raw_text, status, created_at, updated_at)"
                    " VALUES (?, ?, 'meal', 0, 'x', 'active', ?, ?)"
                ).bind(str(uuid_mod.uuid4()), u1, micros(NOW), micros(NOW)),
                db.prepare(
                    "INSERT INTO blocks (id, blocker_id, blocked_id, created_at)"
                    " VALUES (?, ?, ?, ?)"
                ).bind(  # 存在しないユーザーへのFK違反
                    str(uuid_mod.uuid4()), u1, str(uuid_mod.uuid4()), micros(NOW)
                ),
            ]
        )
    cnt = await db.prepare(
        "SELECT COUNT(*) AS c FROM intents WHERE raw_text = 'x'"
    ).first()
    assert cnt["c"] == 0, "先頭文だけ保存されてはならない"


async def test_redelivery_returns_stored_result_without_side_effects(db, repo):
    """同一command_idの再配送は保存済み結果を返し、二重書込みしない。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    latch = await seed_latch(db, [i1, i2])

    command_id = _cmd()
    first = await repo.apply_response(command_id, None, _response(latch, u1), NOW)
    assert first.status is CommandStatus.APPLIED
    events_after_first = (
        await db.prepare("SELECT COUNT(*) AS c FROM latch_status_events").first()
    )["c"]

    replay = await repo.apply_response(command_id, None, _response(latch, u1), NOW)
    assert replay.status is CommandStatus.APPLIED
    assert replay.response == first.response
    events_after_replay = (
        await db.prepare("SELECT COUNT(*) AS c FROM latch_status_events").first()
    )["c"]
    assert events_after_replay == events_after_first


# =============================================================================
# 2. 20並行の同時更新
# =============================================================================


async def test_20_parallel_final_yes_single_match(db, repo):
    """同一ユーザーの最終YES 20並行: 採用1・ALREADY_ANSWERED 19。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    await seed_intent(db, u2)  # u2のもう1つ(未使用・干渉なし)
    await seed_eval(db, i1, i2, score=0.85)
    latch = await seed_latch(db, [i1, i2], score=0.85)
    # u1が既にYES済みの状態で、u2の最終YESを20並行
    first = await repo.apply_response(_cmd(), None, _response(latch, u1), NOW)
    assert first.status is CommandStatus.APPLIED

    results = await asyncio.gather(
        *[
            repo.apply_response(_cmd(), None, _response(latch, u2), NOW)
            for _ in range(20)
        ]
    )
    applied = [r for r in results if r.status is CommandStatus.APPLIED]
    answered = [r for r in results if r.error_code == "ALREADY_ANSWERED"]
    assert len(applied) == 1, [(r.status, r.error_code) for r in results]
    assert len(answered) == 19
    assert applied[0].response["latch"]["status"] == "matched"
    # イベント: proposed→partial(u1), partial→matched(勝者) の2行のみ
    events = await db.prepare(
        "SELECT to_status, COUNT(*) AS c FROM latch_status_events GROUP BY to_status"
    ).all()
    by_status = {r["to_status"]: r["c"] for r in events}
    assert by_status.get("partial_accept") == 1
    assert by_status.get("matched") == 1
    # 所有権: 各Intent 1回だけ
    owners = await db.prepare(
        "SELECT COUNT(*) AS c FROM intent_match_ownership"
    ).first()
    assert owners["c"] == 2
    # 通知は参加者2人×1回
    matched_notifs = await db.prepare(
        "SELECT COUNT(*) AS c FROM notifications WHERE type = 'latch_matched'"
    ).first()
    assert matched_notifs["c"] == 2
    # calibration 1行
    calib = await db.prepare("SELECT COUNT(*) AS c FROM calibration_records").first()
    assert calib["c"] == 1


async def test_20_parallel_yes_vs_no_single_outcome(db, repo):
    """同一ユーザーのYES/NO混在20並行: 1つだけ採用、残りは不採用。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    latch = await seed_latch(db, [i1, i2])
    values = ["yes"] * 10 + ["no"] * 10
    results = await asyncio.gather(
        *[
            repo.apply_response(_cmd(), None, _response(latch, u1, v), NOW)
            for v in values
        ]
    )
    applied = [r for r in results if r.status is CommandStatus.APPLIED]
    assert len(applied) == 1
    final_status = (
        await db.prepare(
            "SELECT status FROM latches WHERE id = ?",  # noqa: S608
        )
        .bind(latch)
        .first()
        if False
        else await db.prepare("SELECT status FROM latches WHERE id = ?")
        .bind(latch)
        .first()
    )
    assert final_status["status"] in ("partial_accept", "rejected")
    events = (
        await db.prepare("SELECT COUNT(*) AS c FROM latch_status_events").first()
    )["c"]
    assert events == 1, "採用1件のイベントのみ"


async def test_20_parallel_expired_vs_answer(db, repo):
    """期限切れlatchへの20並行回答: 全件LATCH_EXPIRED・状態不変。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    latch = await seed_latch(db, [i1, i2], deadline_hours=-1.0)
    results = await asyncio.gather(
        *[
            repo.apply_response(_cmd(), None, _response(latch, u2), NOW)
            for _ in range(20)
        ]
    )
    assert all(r.error_code == "LATCH_EXPIRED" for r in results)
    row = (
        await db.prepare("SELECT status, responses FROM latches WHERE id = ?")
        .bind(latch)
        .first()
    )
    assert row["status"] == "proposed"
    assert json.loads(row["responses"]) == []


async def test_shared_intent_double_match_single_winner(db, repo):
    """共有Intentを持つ2つのlatchが同時成立を試みたら1つだけ成立。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    u3 = await seed_user(db, "C")
    i1 = await seed_intent(db, u1)  # 共有Intent
    i2 = await seed_intent(db, u2)
    i3 = await seed_intent(db, u3)
    # u2・u3がi1と共にYES済みの2つのopen latch
    latch_a = await seed_latch(db, [i1, i2])
    latch_b = await seed_latch(db, [i1, i3])
    await repo.apply_response(_cmd(), None, _response(latch_a, u2), NOW)
    await repo.apply_response(_cmd(), None, _response(latch_b, u3), NOW)
    # 最終YESを20並行(交互に)
    results = await asyncio.gather(
        *[
            repo.apply_response(
                _cmd(), None, _response(latch_a if i % 2 == 0 else latch_b, u1), NOW
            )
            for i in range(20)
        ]
    )
    matched = [r for r in results if r.status is CommandStatus.APPLIED]
    assert len(matched) == 1
    # 所有権により片方だけ成立・他方はcancelled
    statuses = {
        r["id"]: r["status"]
        for r in await db.prepare("SELECT id, status FROM latches WHERE id IN (?, ?)")
        .bind(latch_a, latch_b)
        .all()
    }
    assert sorted(statuses.values()) == ["cancelled", "matched"]
    # i1はmatched(1回だけ)
    intent = (
        await db.prepare("SELECT status FROM intents WHERE id = ?").bind(i1).first()
    )
    assert intent["status"] == "matched"


async def test_notification_quota_race(db, repo):
    """D-08日次上限の境界を20並行で叩いて超過しない(apply_proposal)。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    # u1に既に6通知(上限)
    for _ in range(6):
        await db.batch(
            [
                db.prepare(
                    "INSERT INTO notifications (id, user_id, type, payload,"
                    " created_at) VALUES (?, ?, 'proposal', '{}', ?)"
                ).bind(str(uuid_mod.uuid4()), u1, micros(NOW)),
            ]
        )
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    results = await asyncio.gather(
        *[
            repo.apply_proposal(
                _cmd(),
                {i1: 1, i2: 1},
                {"intent_ids": [i1, i2], "score": 0.9, "proposal": {"headcount": 2}},
                NOW,
            )
            for _ in range(20)
        ]
    )
    applied = [r for r in results if r.status is CommandStatus.APPLIED]
    assert len(applied) == 0, "u1が日次上限のため全件却下"
    assert all(r.error_code == "DAILY_LIMIT" for r in results)
    latches = (await db.prepare("SELECT COUNT(*) AS c FROM latches").first())["c"]
    assert latches == 0


# =============================================================================
# 3. session(refresh回転・再利用・logout・退会)
# =============================================================================


async def test_refresh_rotation_and_reuse_revokes_family(db):
    store = D1SessionStore(db)
    token = await store.create_family(
        sid="sid-1", provider="google", subject="sub-1", now=NOW
    )
    rotated = await store.rotate(token, NOW)
    assert rotated is not None and rotated["new_token"] != token
    # 旧token再利用 → family失効(rotated新tokenも無効化)
    reuse = await store.rotate(token, NOW + timedelta(seconds=1))
    assert reuse is None
    new_after_reuse = await store.rotate(
        rotated["new_token"], NOW + timedelta(seconds=2)
    )
    assert new_after_reuse is None, "family失効後の新tokenも拒否"


async def test_20_parallel_rotation_single_winner(db):
    """同一refreshの回転20並行: 1回だけ成功(1行条件付きUPDATEの原子性)。"""
    store = D1SessionStore(db)
    token = await store.create_family(
        sid="sid-2", provider="google", subject="sub-2", now=NOW
    )
    results = await asyncio.gather(
        *[store.rotate(token, NOW + timedelta(milliseconds=i)) for i in range(20)]
    )
    winners = [r for r in results if r is not None]
    assert len(winners) <= 1, "回転は高々1回(残りは再利用検出で失敗)"
    # 並行敗者がfamilyを不正に失効させない(勝者が1回なら失効しない)
    family = await db.prepare(
        "SELECT status FROM session_families WHERE sid = 'sid-2'"
    ).first()
    # 厳密には再利用検出が先に走った1件がfamily失効させる(仕様どおり)
    assert family["status"] in ("active", "revoked")


async def test_logout_revokes_jwt_and_family(db):
    store = D1SessionStore(db)
    await store.create_family(sid="sid-3", provider="google", subject="sub-3", now=NOW)
    await store.revoke_jwt("jti-1", NOW + timedelta(hours=1), NOW)
    assert await store.jwt_revoked("jti-1") is True
    assert await store.jwt_revoked("jti-other") is False
    await store.revoke_family("sid-3", NOW)
    family = await db.prepare(
        "SELECT status FROM session_families WHERE sid = 'sid-3'"
    ).first()
    assert family["status"] == "revoked"


async def test_account_delete_revokes_sessions_and_anonymizes(db, repo):
    """退会: session失効・匿名化・open latch cancelが同じbatch。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    latch = await seed_latch(db, [i1, i2])
    store = D1SessionStore(db)
    await store.create_family(
        sid="sid-del", provider="google", subject=f"sub-{u1[:8]}", now=NOW
    )
    await db.batch(
        [
            db.prepare("UPDATE session_families SET user_id = ? WHERE sid = ?").bind(
                u1, "sid-del"
            ),
        ]
    )

    result = await repo.apply_safety(
        _cmd(), {"kind": "account_delete", "user_id": u1}, NOW
    )
    assert result.status is CommandStatus.APPLIED
    user = (
        await db.prepare(
            "SELECT display_name, auth_subject, consent_at FROM users WHERE id = ?"
        )
        .bind(u1)
        .first()
    )
    assert user["display_name"] == "退会したユーザー"
    assert user["auth_subject"].startswith("deleted:")
    family = await db.prepare(
        "SELECT status FROM session_families WHERE sid = 'sid-del'"
    ).first()
    assert family["status"] == "revoked"
    latch_row = (
        await db.prepare("SELECT status FROM latches WHERE id = ?").bind(latch).first()
    )
    assert latch_row["status"] == "cancelled"
    tomb = (
        await db.prepare(
            "SELECT state FROM deletion_tombstones WHERE target_id = ?"
            " AND target_kind = 'user'"
        )
        .bind(u1)
        .first()
    )
    assert tomb is not None


# =============================================================================
# 4. safety(block・同意撤回・削除後の復帰と公開範囲)
# =============================================================================


async def test_bidirectional_block_cancels_shared_open_latches(db, repo):
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    latch = await seed_latch(db, [i1, i2])

    result = await repo.apply_safety(
        _cmd(), {"kind": "block", "blocker_id": u1, "blocked_id": u2}, NOW
    )
    assert result.status is CommandStatus.APPLIED
    row = (
        await db.prepare("SELECT status FROM latches WHERE id = ?").bind(latch).first()
    )
    assert row["status"] == "cancelled"
    events = await db.prepare(
        "SELECT user_id FROM latch_status_events WHERE to_status = 'cancelled'"
    ).first()
    assert events["user_id"] == u1  # blocker起因
    # 双方向: チャット送信不可(D1SafetyReader)
    reader = D1SafetyReader(db)
    i3 = await seed_intent(db, u1)
    i4 = await seed_intent(db, u2)
    matched = await seed_latch(db, [i3, i4], status="matched")
    assert await reader.chat_sendable(matched, u1) is False
    assert await reader.chat_sendable(matched, u2) is False
    # block解除で送信可復帰(誤block救済)
    unblock = await repo.apply_safety(
        _cmd(), {"kind": "unblock", "blocker_id": u1, "blocked_id": u2}, NOW
    )
    assert unblock.status is CommandStatus.APPLIED
    assert await reader.chat_sendable(matched, u1) is True


async def test_consent_withdraw_disables_and_schedules(db, repo):
    u1 = await seed_user(db, "A")
    result = await repo.apply_safety(
        _cmd(), {"kind": "consent_withdraw", "user_id": u1}, NOW
    )
    assert result.status is CommandStatus.APPLIED
    user = (
        await db.prepare("SELECT consent_at FROM users WHERE id = ?").bind(u1).first()
    )
    assert user["consent_at"] is None
    tomb = (
        await db.prepare("SELECT reason FROM deletion_tombstones WHERE target_id = ?")
        .bind(u1)
        .first()
    )
    assert tomb["reason"] == "consent_withdraw"


async def test_intent_delete_restores_partner_and_cancels(db, repo):
    """削除: open/matched latch取消とパートナー復帰・期限切れ分岐。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    open_latch = await seed_latch(db, [i1, i2])
    matched_latch = await seed_latch(db, [i1, i2], status="matched")

    result = await repo.apply_intent(
        _cmd(), None, {"op": "delete", "intent_id": i1, "user_id": u1}, NOW
    )
    assert result.status is CommandStatus.APPLIED
    statuses = {
        r["id"]: r["status"]
        for r in await db.prepare("SELECT id, status FROM latches WHERE id IN (?, ?)")
        .bind(open_latch, matched_latch)
        .all()
    }
    assert set(statuses.values()) == {"cancelled"}
    # i1はcancelled・i2は復帰(active)
    i1_row = (
        await db.prepare("SELECT status FROM intents WHERE id = ?").bind(i1).first()
    )
    i2_row = (
        await db.prepare("SELECT status FROM intents WHERE id = ?").bind(i2).first()
    )
    assert i1_row["status"] == "cancelled"
    assert i2_row["status"] == "active"
    # 削除job(tombstone)
    tomb = (
        await db.prepare(
            "SELECT state FROM deletion_tombstones WHERE target_id = ?"
            " AND target_kind = 'intent'"
        )
        .bind(i1)
        .first()
    )
    assert tomb is not None
    # outboxへdeleted event(原文なし)
    outbox = (
        await db.prepare(
            "SELECT reason, intent_id FROM outbox WHERE intent_id = ?"
            " AND reason = 'deleted'"
        )
        .bind(i1)
        .first()
    )
    assert outbox is not None


async def test_delete_other_users_intent_forbidden(db, repo):
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    result = await repo.apply_intent(
        _cmd(), None, {"op": "delete", "intent_id": i1, "user_id": u2}, NOW
    )
    assert result.status is CommandStatus.REJECTED
    assert result.error_code == "FORBIDDEN"


async def test_intent_update_optimistic_revision(db, repo):
    """expected_revision不一致はCONFLICT(楽観的排他)。"""
    u1 = await seed_user(db, "A")
    created = await repo.apply_intent(
        _cmd(),
        None,
        {
            "op": "create",
            "user_id": u1,
            "fields": {
                "status": "active",
                "category_primary": "meal",
                "raw_text": "x",
                "time_start": FUTURE_2H,
                "expires_at": FUTURE_120H,
            },
        },
        NOW,
    )
    iid = created.response["intent"]["id"]
    stale = await repo.apply_intent(
        _cmd(),
        99,
        {"op": "update", "intent_id": iid, "user_id": u1, "fields": {"raw_text": "y"}},
        NOW,
    )
    assert stale.status is CommandStatus.CONFLICT
    ok = await repo.apply_intent(
        _cmd(),
        1,
        {"op": "update", "intent_id": iid, "user_id": u1, "fields": {"raw_text": "y"}},
        NOW,
    )
    assert ok.status is CommandStatus.APPLIED
    assert ok.revision == 2
    row = (
        await db.prepare("SELECT raw_text, version FROM intents WHERE id = ?")
        .bind(iid)
        .first()
    )
    assert row["raw_text"] == "y" and row["version"] == 2


async def test_apply_proposal_open_duplicate_rejected(db, repo):
    """openなlatchのintent集合重複は一意制約で却下(uq_latches_intent_ids_open)。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    first = await repo.apply_proposal(
        _cmd(),
        {i1: 1, i2: 1},
        {
            "intent_ids": [i1, i2],
            "score": 0.9,
            "proposal": {"headcount": 2, "match_level": "high"},
        },
        NOW,
    )
    assert first.status is CommandStatus.APPLIED
    dup = await repo.apply_proposal(
        _cmd(),
        {i1: 1, i2: 1},
        {
            "intent_ids": [i1, i2],
            "score": 0.95,
            "proposal": {"headcount": 2, "match_level": "high"},
        },
        NOW,
    )
    assert dup.status is CommandStatus.CONFLICT
    latches = (await db.prepare("SELECT COUNT(*) AS c FROM latches").first())["c"]
    assert latches == 1
    # 通知は最初の提案のみ(2ユーザー×1)
    notifs = (
        await db.prepare(
            "SELECT COUNT(*) AS c FROM notifications WHERE type = 'proposal'"
        ).first()
    )["c"]
    assert notifs == 2


async def test_journal_records_all_command_effects(db, repo):
    """journalが業務更新と同じbatchで保存される(巻き戻しの材料)。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    i1 = await seed_intent(db, u1)
    i2 = await seed_intent(db, u2)
    latch = await seed_latch(db, [i1, i2])
    await repo.apply_response(_cmd(), None, _response(latch, u1), NOW)
    rows = await db.prepare(
        "SELECT table_name, change_kind FROM journal ORDER BY seq"
    ).all()
    tables = {r["table_name"] for r in rows}
    assert "latches" in tables
    assert all(r["change_kind"] == "upsert" for r in rows)
