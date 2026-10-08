"""T8: due時刻と変更世代による再評価・期限処理(計画 §6 T8・§5.4・I3)。

検証面(§5.4):
- v1処理中のv2更新: 完了確定はFalse・v2の世代は消えずdueに残る
- 抑制(lease)中の複数更新が残る
- 時刻条件: 30分bucket・期限2時間前・期限切れ(次回なし)・D-07抑制解除・
  通知日次上限解除(JST 0時)
- pause/resume
- scheduler停止後もdue仕事を回収(lease期限切れ)
- 50件超のdueをcursorで公平に全件回収(先頭偏りなし)
- 失敗したbatchの残りも回収
- 次の機会で未成立ペアが成立する機会が残る(2時間前未成立→bucket継続)
"""

from __future__ import annotations

import asyncio
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest

from latch.adapters.d1.database import LocalD1Database
from latch.adapters.d1.schedule import (
    D1ScheduleRepository,
    ScheduleClaim,
    compute_next_due,
    jst_next_midnight,
)
from latch.runtime.cloudflare.scheduled import ScheduledEvaluator

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
START = NOW + timedelta(hours=6)
EXPIRES = NOW + timedelta(days=3)


@pytest.fixture
async def db():
    database = LocalD1Database()
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def repo(db) -> D1ScheduleRepository:
    return D1ScheduleRepository(db, lease_seconds=120.0)


def _intent_id() -> str:
    return str(uuid_mod.uuid4())


async def _noop_evaluator(claim: ScheduleClaim, now: datetime) -> dict:
    return {"evaluated": claim.intent_id, "generation": claim.requested_generation}


# =============================================================================
# 世代の保全(v1処理中のv2更新)
# =============================================================================


async def test_v2_update_during_v1_processing_survives(db, repo):
    """v1評価中にv2要求→v1完了確定はFalse・v2はdueに残る。"""
    iid = _intent_id()
    await repo.request_evaluation(iid, 1, "initial", NOW)
    claims = await repo.claim_due(NOW, limit=10)
    assert len(claims) == 1 and claims[0].requested_generation == 1
    # 処理中にv2(ユーザー更新)が届く
    gen2 = await repo.request_evaluation(iid, 2, "updated", NOW + timedelta(seconds=1))
    assert gen2 == 2
    # v1の完了確定 → claim後の更新を消さないためFalse
    ok = await repo.complete_generation(
        claims[0], {"v": 1}, NOW + timedelta(minutes=30), NOW + timedelta(seconds=2)
    )
    assert ok is False, "v2が来た後のv1確定は不採用"
    # v2は未評価のままdue
    assert await repo.pending_generation(iid) == 2
    later = NOW + timedelta(seconds=5)
    claims2 = await repo.claim_due(later, limit=10)
    assert len(claims2) == 1
    assert claims2[0].requested_generation == 2
    assert claims2[0].requested_revision == 2


async def test_multiple_updates_while_suppressed_all_kept(db, repo):
    """lease(抑制)中の複数更新が消えない(旧Redis抑制の欠陥対策)。"""
    iid = _intent_id()
    await repo.request_evaluation(iid, 1, "initial", NOW)
    claim = (await repo.claim_due(NOW, limit=10))[0]
    # 抑制中に3回更新
    for rev in (2, 3, 4):
        await repo.request_evaluation(iid, rev, "updated", NOW + timedelta(seconds=rev))
    # 最終要求の情報が見える(中間の2・3も消えていない=世代が進んだ記録)
    assert await repo.pending_generation(iid) == 4
    # 完了確定は不採用(claimはgen1のまま)
    assert (
        await repo.complete_generation(claim, {}, None, NOW + timedelta(seconds=10))
        is False
    )
    row = (
        await db.prepare(
            "SELECT requested_generation, requested_revision, status"
            " FROM intent_evaluation_schedule WHERE intent_id = ?"
        )
        .bind(iid)
        .first()
    )
    assert row["requested_generation"] == 4
    assert row["status"] == "due"


# =============================================================================
# 時刻条件(compute_next_due)
# =============================================================================


def test_next_due_bucket_and_pre_expiry():
    """bucket境界・期限2時間前のmin選択。"""

    # 12:00 → 次bucket 12:30
    assert compute_next_due(
        NOW, time_start=START, expires_at=EXPIRES
    ) == NOW + timedelta(minutes=30)
    # time_start - 2h が次bucketより前ならそちらが採れる
    # (start-2h が (12:00, 12:30) に入る start=14:15)
    start = NOW + timedelta(hours=2, minutes=15)
    nd = compute_next_due(NOW, time_start=start, expires_at=EXPIRES)
    assert nd == start - timedelta(hours=2)


def test_next_due_defer_and_daily_cap():
    """D-07抑制解除とJST 0時(日次上限解除)の候補。"""
    defer_until = NOW + timedelta(hours=20)
    nd = compute_next_due(
        NOW, time_start=START, expires_at=EXPIRES, defer_until=defer_until
    )
    assert nd == NOW + timedelta(minutes=30)  # bucketが最短
    midnight = jst_next_midnight(NOW)
    nd2 = compute_next_due(
        NOW, time_start=START, expires_at=EXPIRES, daily_cap_release=midnight
    )
    assert nd2 == NOW + timedelta(minutes=30)


def test_next_due_expired_returns_none():
    """期限切れは評価機会を残さない(None)。"""
    assert (
        compute_next_due(NOW, time_start=START, expires_at=NOW - timedelta(minutes=1))
        is None
    )
    assert compute_next_due(EXPIRES, time_start=START, expires_at=EXPIRES) is None


def test_pre_expiry_unmatched_pair_keeps_opportunity():
    """期限2時間前で未成立でも30分bucketの機会が残る(§5.4)。"""
    from latch.adapters.d1.schedule import _bucket_after

    # 2時間前の時点(未成立) → 次回もbucket機会
    t_minus_2h = START - timedelta(hours=2)
    nd = compute_next_due(t_minus_2h, time_start=START, expires_at=EXPIRES)
    assert nd == _bucket_after(t_minus_2h, 30)
    # 期限直前も機会が継続(bucket境界)
    t_minus_5m = START - timedelta(minutes=5)
    nd2 = compute_next_due(t_minus_5m, time_start=START, expires_at=EXPIRES)
    assert nd2 == _bucket_after(t_minus_5m, 30)
    assert nd2 > t_minus_5m


# =============================================================================
# pause/resume・回収・公平性
# =============================================================================


async def test_pause_excluded_resume_immediate(db, repo):
    """pause中はdue照会から外れ・resumeは即時due。"""
    iid = _intent_id()
    await repo.request_evaluation(iid, 1, "initial", NOW)
    await repo.mark_paused(iid, paused=True)
    assert await repo.claim_due(NOW, limit=10) == []
    await repo.mark_paused(iid, paused=False)
    claims = await repo.claim_due(NOW, limit=10)
    assert len(claims) == 1


async def test_scheduler_stop_then_recovery(db, repo):
    """scheduler停止(lease放置)→ 期限切れ後に回収され再実行。"""
    iid = _intent_id()
    await repo.request_evaluation(iid, 1, "initial", NOW)
    (await repo.claim_due(NOW, limit=10))[0]  # 停止を模擬(完了せず)
    # lease 120s切れ
    later = NOW + timedelta(seconds=130)
    recovered = await repo.recover_expired_leases(later)
    assert recovered == 1
    claims = await repo.claim_due(later, limit=10)
    assert len(claims) == 1, "停止後もdue仕事を回収"


async def test_50_plus_due_all_claimed_fairly(db, repo):
    """120件のdueをbatch50のcursorで偏りなく全件回収。"""
    ids = []
    for i in range(120):
        iid = _intent_id()
        ids.append(iid)
        # due時刻を散らす(同一時刻も混ぜる)
        await repo.request_evaluation(iid, 1, "initial", NOW - timedelta(seconds=i % 7))
    scheduler = ScheduledEvaluator(repo, _noop_evaluator, batch_limit=50)
    result = await scheduler.run_once(NOW)
    assert result.claimed == 120, "全件回収(先頭偏りなし)"
    assert result.completed == 120
    # 全て評価済み世代に
    pending = await db.prepare(
        "SELECT COUNT(*) AS c FROM intent_evaluation_schedule"
        " WHERE evaluated_generation IS NULL"
        "    OR evaluated_generation < requested_generation"
    ).first()
    assert pending["c"] == 0


async def test_failed_batch_remaining_recovered(db, repo):
    """評価中の失敗(batch中断)でも残りは次周期で回収。"""
    boom_id = None
    processed: list = []

    async def evaluator(claim: ScheduleClaim, now: datetime) -> dict:
        nonlocal boom_id
        processed.append(claim.intent_id)
        if len(processed) == 2:
            boom_id = claim.intent_id
            raise RuntimeError("simulated evaluator failure")
        return {}

    for i in range(6):
        await repo.request_evaluation(
            _intent_id(), 1, "initial", NOW - timedelta(seconds=i)
        )
    scheduler = ScheduledEvaluator(repo, evaluator, batch_limit=50)
    with pytest.raises(RuntimeError):
        await scheduler.run_once(NOW)
    # 失敗で抜けた分はlease切れ後に回収される
    later = NOW + timedelta(seconds=130)
    recovered = await repo.recover_expired_leases(later)
    assert recovered >= 1
    # 残りの再実行(今度は成功)
    ok_scheduler = ScheduledEvaluator(repo, _noop_evaluator, batch_limit=50)
    result = await ok_scheduler.run_once(later)
    assert result.completed + len(processed) - 1 >= 6, "最終的に全件評価"


async def test_immediate_request_claims_now(db, repo):
    """ユーザー更新は即時due(due_at=nowで届く)。"""
    iid = _intent_id()
    await repo.request_evaluation(iid, 1, "initial", NOW + timedelta(hours=1))
    # 初期は将来due → まだclaimされない
    assert await repo.claim_due(NOW, limit=10) == []
    # ユーザー更新(即時)
    await repo.request_evaluation(iid, 2, "updated", NOW)
    claims = await repo.claim_due(NOW, limit=10)
    assert len(claims) == 1


async def test_new_due_pulls_earlier_not_later(db, repo):
    """新しい要求は既存dueより後ろへ動かさない(MIN)。"""
    iid = _intent_id()
    await repo.request_evaluation(iid, 1, "timer", NOW + timedelta(minutes=30))
    await repo.request_evaluation(iid, 2, "updated", NOW + timedelta(hours=2))
    row = (
        await db.prepare(
            "SELECT next_evaluate_at FROM intent_evaluation_schedule"
            " WHERE intent_id = ?"
        )
        .bind(iid)
        .first()
    )
    from latch.domain.time import micros

    assert row["next_evaluate_at"] == micros(NOW + timedelta(minutes=30))


async def test_expired_intent_no_next_due(db, repo):
    """期限切れIntentの完了: next_due=Noneでdue照会対象外へ。"""
    iid = _intent_id()
    await repo.request_evaluation(iid, 1, "timer", NOW)
    claim = (await repo.claim_due(NOW, limit=10))[0]
    ok = await repo.complete_generation(claim, {}, None, NOW)
    assert ok is True
    later = NOW + timedelta(minutes=10)
    assert await repo.claim_due(later, limit=10) == []
    assert await repo.pending_generation(iid) is None


async def test_concurrent_claims_single_winner(db, repo):
    """同一dueの並行claimは1件だけ(条件付きUPDATE仲裁)。"""
    iid = _intent_id()
    await repo.request_evaluation(iid, 1, "initial", NOW)
    results = await asyncio.gather(*[repo.claim_due(NOW, limit=10) for _ in range(20)])
    total = sum(len(r) for r in results)
    assert total == 1
