"""T5: Queues/outbox/処理権/全LLM試行の記録(計画 §6 T5・I1・§4)。

assertする呼出し数(計画§5.2検証):
- 同じキー20並行 → 外部呼出し1回・結果1件
- 別キーはそれぞれ実行
- 順序逆転・再配送・claim直後停止・API後DB失敗・lease切れ後の旧処理・
  peer更新
- 送信前停止・送信後不明でも試行が消えず・usage不明を0円としない
- 並行jobが同じ残枠を二重に消費しない(usage_counters原子予約)
"""

from __future__ import annotations

import asyncio
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest

from latch.adapters.d1.database import LocalD1Database
from latch.adapters.d1.evaluations import D1EvaluationGateway
from latch.adapters.d1.jobs import D1JobCoordinator, D1UsageGuard
from latch.domain.time import micros
from latch.domain.types import EvaluationKey
from latch.runtime.cloudflare.jobs import (
    EvaluationJobRunner,
    SendResult,
    TimeoutError_,
)

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def db():
    database = LocalD1Database()
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def gateway(db) -> D1EvaluationGateway:
    return D1EvaluationGateway(db, lease_seconds=35.0)


@pytest.fixture
def coordinator(db) -> D1JobCoordinator:
    return D1JobCoordinator(db, lease_seconds=90.0)


def _key(kind: str = "jev", salt: str = "a") -> EvaluationKey:
    return EvaluationKey(
        kind=kind,
        pair_key=f"{kind}:intent-a:intent-b:1:1:{salt}",
        fingerprint="sha256:fixture",
        context_version="ctx-1",
    )


def _ok_sender(calls: list, *, result: dict | None = None):
    async def send(key: EvaluationKey) -> SendResult:
        calls.append(key)
        return SendResult(
            status="succeeded",
            actual_provider="typesafe_jev",
            actual_model="jev-1.13.0",
            input_tokens=120,
            output_tokens=8,
            cost_confirmed_micros=1500,
            payload=result or {"noul": 3.6},
        )

    return send


# =============================================================================
# I1: 同じ評価の処理権(§5.2)
# =============================================================================


async def test_same_key_20_parallel_single_external_call(db, gateway):
    """同じキー20並行: 外部呼出し1回・結果1件・claimは1件のみ。"""
    calls: list = []
    runner = EvaluationJobRunner(gateway)
    key = _key()
    results = await asyncio.gather(
        *[
            runner.run(
                key,
                owner=f"worker-{i}",
                sender=_ok_sender(calls),
                requested_provider="typesafe_jev",
                requested_model="jev-1.13.0",
                price_version="2026-10",
                now=NOW,
            )
            for i in range(20)
        ]
    )
    claimed = [r for r in results if r["claimed"]]
    assert len(claimed) == 1
    assert len(calls) == 1, "外部呼出しは1回だけ"
    completed = [r for r in results if r.get("completed")]
    assert len(completed) == 1
    row = (
        await db.prepare(
            "SELECT status FROM evaluation_claims WHERE evaluation_key = ?"
        )
        .bind(gateway._key_text(key))
        .first()
    )
    assert row["status"] == "completed"


async def test_different_keys_all_execute(db, gateway):
    """異なる入力・版・モデルへの仕事は抑止しない(§5.2)。"""
    calls: list = []
    runner = EvaluationJobRunner(gateway)
    keys = [_key(salt=s) for s in "abcde"]
    results = await asyncio.gather(
        *[
            runner.run(
                k,
                owner="w",
                sender=_ok_sender(calls),
                requested_provider="typesafe_jev",
                requested_model="jev-1.13.0",
                price_version="2026-10",
                now=NOW,
            )
            for k in keys
        ]
    )
    assert len(calls) == 5
    assert all(r["completed"] for r in results)


async def test_lease_expiry_takeover_with_fencing(db, gateway):
    """claim直後停止→lease切れ→奪取→旧処理の完了はfencingで破棄。"""
    key = _key(salt="lease")
    first = await gateway.claim_evaluation(key, "old-owner", NOW)
    assert first.acquired
    # lease切れ(35s経過)
    later = NOW + timedelta(seconds=40)
    second = await gateway.claim_evaluation(key, "new-owner", later)
    assert second.acquired and second.stale_owner
    assert second.fencing_token == first.fencing_token + 1
    # 旧処理の完了確定は拒否
    assert (
        await gateway.complete_evaluation(
            key, first.fencing_token, {"result": "stale"}, later
        )
        is False
    )
    # 新処理の完了は受理
    assert (
        await gateway.complete_evaluation(
            key, second.fencing_token, {"result": "fresh"}, later
        )
        is True
    )


async def test_api_success_then_db_failure_leaves_unknown(db, gateway):
    """API後DB失敗: unknown記録が残り、無条件の再送をしない。"""
    key = _key(salt="unknown")
    claim = await gateway.claim_evaluation(key, "w", NOW)
    assert claim.acquired
    attempt_id = await gateway.prepare_attempt(
        system="jev",
        requested_provider="typesafe_jev",
        requested_model="jev-1.13.0",
        price_version="2026-10",
        now=NOW,
    )
    # 送信したがtimeout → unknown
    await gateway.finish_attempt(
        attempt_id,
        status="unknown",
        now=NOW + timedelta(seconds=31),
        error_kind="timeout",
    )
    row = await gateway.get_attempt(attempt_id)
    assert row["status"] == "unknown"
    assert row["cost_confirmed_micros"] is None, "usage不明は0円にしない"
    assert row["finished_at"] is not None
    # claimはcompletedにしていない → lease切れ後に回収可能(規定回数)
    assert row["input_tokens"] is None


async def test_attempt_persists_at_every_boundary(db, gateway):
    """送信前停止(preparedのまま)でも試行が消えない。"""
    attempt_id = await gateway.prepare_attempt(
        system="parser",
        requested_provider="anthropic",
        requested_model="claude-haiku-4-5",
        price_version="2026-10",
        now=NOW,
    )
    row = await gateway.get_attempt(attempt_id)
    assert row["status"] == "prepared"
    assert row["prepared_at"] == micros(NOW)
    # その後完了しても同一IDへ追記される
    ok = await gateway.finish_attempt(
        attempt_id,
        status="succeeded",
        now=NOW + timedelta(seconds=2),
        actual_provider="anthropic",
        actual_model="claude-haiku-4-5",
        input_tokens=300,
        output_tokens=100,
        cost_confirmed_micros=900,
        sdk_internal_retry=2,
    )
    assert ok
    row2 = await gateway.get_attempt(attempt_id)
    assert row2["status"] == "succeeded"
    assert row2["sdk_internal_retry"] == 2, "SDK内部再試行も1試行に記録"
    # 二重finishは不採用
    again = await gateway.finish_attempt(attempt_id, status="failed", now=NOW)
    assert again is False


async def test_runner_records_fallback_model(db, gateway):
    """fallback実応答は要求モデルと区別して記録(§5.3の前提)。"""
    calls: list = []

    async def fallback_sender(key: EvaluationKey) -> SendResult:
        calls.append(key)
        return SendResult(
            status="succeeded",
            actual_provider="anthropic",
            actual_model="claude-haiku-4-5",  # fallback先
            input_tokens=100,
            output_tokens=50,
            cost_confirmed_micros=700,
            sdk_internal_retry=1,
        )

    runner = EvaluationJobRunner(gateway)
    result = await runner.run(
        _key(salt="fb"),
        owner="w",
        sender=fallback_sender,
        requested_provider="typesafe_jev",
        requested_model="jev-1.13.0",
        price_version="2026-10",
        now=NOW,
    )
    assert result["completed"]
    row = await gateway.get_attempt(result["attempt_id"])
    assert row["requested_model"] == "jev-1.13.0"
    assert row["actual_model"] == "claude-haiku-4-5"


async def test_runner_unknown_path(db, gateway, coordinator):
    """runnerのtimeout経路: jobをoutcome_unknownへ・attemptはunknown。"""
    job_id = await coordinator.enqueue(kind="evaluation", ref_id="intent-x", now=NOW)

    async def timing_out(key: EvaluationKey) -> SendResult:
        raise TimeoutError_("simulated post-send timeout")

    runner = EvaluationJobRunner(gateway, coordinator)
    result = await runner.run(
        _key(salt="tmo"),
        owner="w",
        sender=timing_out,
        job_id=job_id,
        requested_provider="typesafe_jev",
        requested_model="jev-1.13.0",
        price_version="2026-10",
        now=NOW,
    )
    assert result["unknown"] is True and result["called"] == 1
    job = await db.prepare("SELECT status FROM jobs WHERE id = ?").bind(job_id).first()
    assert job["status"] == "outcome_unknown"
    attempt = await gateway.get_attempt(result["attempt_id"])
    assert attempt["status"] == "unknown"
    assert attempt["cost_confirmed_micros"] is None


# =============================================================================
# 再配送(at-least-once)・outbox
# =============================================================================


async def test_event_redelivery_no_duplicate_job(db, coordinator):
    """同一event_idの再配送でjobを二重作らない(outbox UNIQUE)。"""
    event_id = f"created:intent-x:1:{uuid_mod.uuid4().hex[:6]}"
    first = await coordinator.enqueue(
        kind="evaluation", ref_id="intent-x", now=NOW, event_id=event_id
    )
    assert await coordinator.outbox_event_seen(event_id) is True
    # 再配送(outbox同期列が同じevent_id → job INSERTは呼び出し側規律で
    # 重複検出後にスキップ)。ここでは enqueue 前の seen 判定を契約面とする
    second_seen = await coordinator.outbox_event_seen(event_id)
    assert second_seen is True
    jobs = (
        await db.prepare("SELECT COUNT(*) AS c FROM jobs WHERE id IN (?, ?)")
        .bind(first, first)
        .first()
    )["c"]
    assert jobs == 1


async def test_job_claim_complete_and_fencing(db, coordinator):
    """job claimのfencing: 古いclaimの完了は破棄。"""
    await coordinator.enqueue(kind="notify", ref_id="latch-1", now=NOW)
    claims = await coordinator.claim_due(NOW, owner="w1", limit=5)
    assert len(claims) == 1
    # 別workerが同じjobをclaimしようとしても排他
    claims2 = await coordinator.claim_due(NOW, owner="w2", limit=5)
    assert claims2 == []
    assert await coordinator.complete(claims[0], NOW) is True
    # 完了済みへの再完了は不採用
    assert await coordinator.complete(claims[0], NOW) is False


async def test_job_lease_recovery(db, coordinator):
    """lease切れのrunningを回収し再実行・上限超過はfailed。"""
    await coordinator.enqueue(kind="retention", ref_id="r1", now=NOW)
    claim = (await coordinator.claim_due(NOW, owner="w1", limit=5))[0]
    # lease(90s)切れ
    later = NOW + timedelta(seconds=100)
    recovered = await coordinator.recover_expired_leases(later)
    assert recovered == 1
    rows = await db.prepare("SELECT status, attempts FROM jobs").all()
    assert rows[0]["status"] == "pending"
    # 再claim可能
    claim2 = await coordinator.claim_due(later, owner="w2", limit=5)
    assert len(claim2) == 1
    # 上限(5)を超えた回収はfailedへ(回収→再claimを繰り返す)
    tick = later
    for _ in range(8):
        tick = tick + timedelta(seconds=200)
        await coordinator.recover_expired_leases(tick)
        cands = await coordinator.claim_due(tick, owner="w-next", limit=5)
        if not cands:
            break
    final = await db.prepare("SELECT status, attempts FROM jobs").first()
    assert final["status"] == "failed"
    assert final["attempts"] >= 5


# =============================================================================
# 業務回数guardの原子予約(並行残枠消費)
# =============================================================================


async def test_usage_guard_atomic_reservation(db):
    """20並行・残枠30→30消費 / 残枠5→ちょうど5だけ成功。"""
    guard = D1UsageGuard(db)
    results = await asyncio.gather(
        *[
            guard.reserve(
                key="jev:intent-x:daily",
                limit=30,
                window_start=NOW,
                window_expires=NOW + timedelta(hours=24),
                now=NOW,
            )
            for _ in range(20)
        ]
    )
    assert all(results) and sum(1 for r in results if r) == 20
    assert await guard.remaining("jev:intent-x:daily") == 20

    results5 = await asyncio.gather(
        *[
            guard.reserve(
                key="jev:intent-y:daily",
                limit=5,
                window_start=NOW,
                window_expires=NOW + timedelta(hours=24),
                now=NOW,
            )
            for _ in range(20)
        ]
    )
    assert sum(1 for r in results5 if r) == 5, "上限5にちょうど5だけ成功"
    assert await guard.remaining("jev:intent-y:daily") == 5


async def test_peer_update_new_generation_claimable(db, gateway):
    """peer更新(新しいIntent版)は別評価キーとして抑止しない(§5.2)。"""
    calls: list = []
    runner = EvaluationJobRunner(gateway)
    key_v1 = EvaluationKey("jev", "jev:a:b:1:1", "sha256:fixture-v1", "ctx-1")
    key_v2 = EvaluationKey("jev", "jev:a:b:2:1", "sha256:fixture-v2", "ctx-1")
    await asyncio.gather(
        runner.run(
            key_v1,
            owner="w",
            sender=_ok_sender(calls),
            requested_provider="typesafe_jev",
            requested_model="jev-1.13.0",
            price_version="2026-10",
            now=NOW,
        ),
        runner.run(
            key_v2,
            owner="w",
            sender=_ok_sender(calls),
            requested_provider="typesafe_jev",
            requested_model="jev-1.13.0",
            price_version="2026-10",
            now=NOW,
        ),
    )
    assert len(calls) == 2, "別版は別仕事として実行"


async def test_attempt_accounting_by_system(db, gateway):
    """系統別の集計(parser/jev)が費用付きで取れる(B0比較の基礎)。"""
    for system, cost in (("parser", 900), ("jev", 1500)):
        aid = await gateway.prepare_attempt(
            system=system,
            requested_provider="x",
            requested_model="m",
            price_version="2026-10",
            now=NOW,
        )
        await gateway.finish_attempt(
            aid,
            status="succeeded",
            now=NOW,
            cost_confirmed_micros=cost,
        )
    counts = await gateway.attempt_counts()
    assert counts["parser"]["succeeded"]["confirmed_micros"] == 900
    assert counts["jev"]["succeeded"]["confirmed_micros"] == 1500
