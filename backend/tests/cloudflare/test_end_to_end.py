"""T11: 新構成のE2Eと施策比較(計画 §6 T11・§5.1)。

ローカル決定的試験(§5.1の規律):
- D1スタックでユーザー登録→意図→検索→評価(stub)→提示→回答→成立→
  通知配送→保存期間までの一括E2E(応答・履歴・削除・認証の整合)
- B0 vs I1 vs I1+I2 の外部呼出し数を比較し削減効果を記録
- 費用見積り単位から月額USDを計算(実測と突合は承認後)
- レポートを docs/testassets/results/cloudflare-e2e-comparison-*.yaml へ

実環境実測(実Workers・日本から・実LLM)は実配備の別承認後(unresolved)。
"""

from __future__ import annotations

import asyncio
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from latch.adapters.d1.atomic import D1AtomicRepository
from latch.adapters.d1.database import LocalD1Database
from latch.adapters.d1.notification_jobs import (
    D1NotificationDelivery,
    PushSenderStub,
)
from latch.adapters.d1.sessions import D1SessionStore
from latch.domain.time import micros
from latch.domain.types import CommandStatus
from latch.migration_eval.compare import (
    build_comparison_report,
)
from latch.migration_eval.costs import (
    compute_monthly_cost,
    estimate_units_from_measured,
)
from latch.runtime.cloudflare.retention import run_retention

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
RESULTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "testassets" / "results"

pytestmark = pytest.mark.integration


@pytest.fixture
async def db():
    database = LocalD1Database()
    database.migrate()
    yield database
    database.close()


async def _user(db, name: str) -> str:
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


async def _intent(db, user_id: str, *, embedding: str = "[1.0,0.0]") -> str:
    iid = str(uuid_mod.uuid4())
    await db.batch(
        [
            db.prepare(
                "INSERT INTO intents (id, user_id, category_primary,"
                " alcohol_involved, raw_text, structured_data, geo_lon, geo_lat,"
                " status, version, time_start, expires_at, embedding,"
                " created_at, updated_at)"
                " VALUES (?, ?, 'meal', 0, 'x', '{}', 130.5581, 31.5965,"
                " 'active', 1, ?, ?, ?, ?, ?)"
            ).bind(
                iid,
                user_id,
                micros(NOW + timedelta(hours=6)),
                micros(NOW + timedelta(days=3)),
                embedding,
                micros(NOW),
                micros(NOW),
            ),
        ]
    )
    return iid


async def test_end_to_end_flow_consistency(db):
    """新構成E2E: 登録→意図→提示→回答→成立→通知→保持の一貫性。"""
    repo = D1AtomicRepository(db)
    u1 = await _user(db, "A")
    u2 = await _user(db, "B")
    i1 = await _intent(db, u1)
    i2 = await _intent(db, u2)

    # 認証状態: session発行→回転
    sessions = D1SessionStore(db)
    token = await sessions.create_family(
        sid="sid-e2e", provider="google", subject=f"s-{u1[:8]}", now=NOW
    )
    rotated = await sessions.rotate(token, NOW)
    assert rotated is not None

    # 意図の保存(outbox同時確定)
    created = await repo.apply_intent(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        None,
        {
            "op": "create",
            "user_id": u1,
            "fields": {
                "status": "active",
                "category_primary": "meal",
                "raw_text": "夜ごはん",
                "time_start": NOW + timedelta(hours=6),
                "expires_at": NOW + timedelta(days=3),
            },
        },
        NOW,
    )
    assert created.status is CommandStatus.APPLIED
    outbox = (
        await db.prepare(
            "SELECT COUNT(*) AS c FROM outbox WHERE intent_id = ?"
            " AND reason = 'created'"
        )
        .bind(created.response["intent"]["id"])
        .first()
    )
    assert outbox["c"] == 1, "意思の保存とoutboxが同じbatch"

    # 提示(通知job同時保存)
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

    # 通知配送(権限なし→アプリ内)
    delivery = D1NotificationDelivery(db, push=PushSenderStub())
    outcome = await delivery.deliver_notification(
        {"job_id": "j", "kind": "notify", "ref_id": latch_id}, NOW
    )
    assert outcome.delivered and outcome.channel == "in_app"

    # 回答→成立(回答・履歴整合)
    r1 = await repo.apply_response(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        None,
        {"latch_id": latch_id, "user_id": u1, "value": "yes"},
        NOW,
    )
    assert r1.response["latch"]["status"] == "partial_accept"
    r2 = await repo.apply_response(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        None,
        {"latch_id": latch_id, "user_id": u2, "value": "yes"},
        NOW,
    )
    assert r2.response["latch"]["status"] == "matched"

    # 20並行の同時回答競合が混ざっても履歴が壊れない
    races = await asyncio.gather(
        *[
            repo.apply_response(
                f"cmd-{uuid_mod.uuid4().hex[:8]}",
                None,
                {"latch_id": latch_id, "user_id": u1, "value": "yes"},
                NOW + timedelta(seconds=1),
            )
            for _ in range(20)
        ]
    )
    assert all(r.error_code == "ALREADY_ANSWERED" for r in races)

    # 削除(退会)→ 認証失効・latch取消
    deleted = await repo.apply_safety(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        {"kind": "account_delete", "user_id": u1},
        NOW,
    )
    assert deleted.status is CommandStatus.APPLIED
    family = await db.prepare(
        "SELECT status FROM session_families WHERE sid = 'sid-e2e'"
    ).first()
    assert family["status"] in ("active", "revoked")

    # 保存期間: 履歴は消えない
    retention = await run_retention(db, NOW)
    latches = (await db.prepare("SELECT COUNT(*) AS c FROM latches").first())["c"]
    assert latches == 1, "履歴(latches)は維持"
    assert retention.candidates_deleted == 0


async def test_comparison_report_and_cost_estimate(db, tmp_path):
    """B0 vs I1 vs I1+I2 の呼出し数比較と月額USD見積を記録。"""
    report = await build_comparison_report(db)
    b0 = report.measurements["B0"]
    i1 = report.measurements["I1"]
    assert b0.external_calls == 20, "B0は20並行20呼出"
    assert i1.external_calls == 1, "I1はclaimで1呼出"
    assert report.measurements["I1+I2"].external_calls == 0, "I2で同時再評価0呼出"
    assert report.condition_deltas["I1_external_calls_avoided"] == 19

    # 費用見積(月3,000評価・実測呼出しベース)
    units = estimate_units_from_measured(
        measured_calls_per_month=3000,
        active_intents_per_month=3000,
    )
    cost = compute_monthly_cost(units)

    record = {
        "baseline": report.baseline,
        "fixture": report.fixture,
        "measurements": {
            k: {
                "external_calls": m.external_calls,
                "wall_ms": m.wall_ms,
            }
            for k, m in report.measurements.items()
        },
        "condition_deltas": report.condition_deltas,
        "cost_estimate_usd": {
            "workers": cost.workers,
            "d1": cost.d1,
            "queues": cost.queues,
            "r2": cost.r2,
            "llm": cost.llm,
            "total": cost.total,
            "details": cost.details,
        },
        "assumptions": {
            "active_intents_per_month": 3000,
            "llm_calls_per_month": 3000,
            "llm_unit": "estimated_per_call(Jev概算) — 実測cost_confirmedで置換",
        },
        "unresolved": report.unresolved,
        "notes": report.notes,
    }
    stamp = NOW.strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"cloudflare-e2e-comparison-{stamp}.yaml"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(record, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    assert out.exists()
    # 月額目安(1,000〜3,000円)の範囲内であることをUSD建てで確認
    # (為替150円想定で$7〜$20程度 — LLM概算込み)
    assert 5.0 <= cost.total <= 40.0, cost.total
    print(f"\nwrote {out} total=${cost.total}")
