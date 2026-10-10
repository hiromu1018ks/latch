"""マッチングパイプラインE2E単体試験(Workers組立・in-process・2026-10-10)。

契約試験(test_cloudflare_parity.py)と同じASGI構成(local in-process:
latch_worker.app + LocalD1Database + build_state)で、意図保存から
LATCH成立・通知記録までの非同期一連を検証する:

  意図2件API作成(hook: outbox+schedule) → post-commit配送 →
  queue consumer評価(embedding→T4検索→cheap judge→stub jev) →
  apply_proposal(提案・通知job) → cron(notify配送・schedule・retention) →
  回答yes×2 → matched → cronで成立通知配送

LLMはstub(実LLM呼出し無し)。queue producerはfake(送信envelopeを記録)。
"""

from __future__ import annotations

import json
import sys
import uuid as uuid_mod
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
_WORKER_DIR = str(Path(__file__).resolve().parents[3] / "cloudflare" / "api")
if _WORKER_DIR not in sys.path:
    sys.path.insert(0, _WORKER_DIR)

_GEO_SEED = BACKEND_DIR / "cloudflare" / "fixtures" / "geofeatures_seed.sql"

pytestmark = pytest.mark.integration


class FakeJobs:
    """Queues producerのfake(送信envelopeを記録・失敗注入可)。"""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail_next = False

    async def send(self, body: str) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("queue unavailable (test)")
        self.sent.append(body)


@pytest.fixture
async def stack():
    from latch.adapters.d1.database import LocalD1Database

    sys.path.insert(0, _WORKER_DIR)
    from latch_worker.app import create_workers_app
    from latch_worker.pipeline import build_pipeline
    from latch_worker.state import build_state

    db = LocalD1Database()
    db.migrate()
    db._conn.executescript(_GEO_SEED.read_text(encoding="utf-8"))  # noqa: SLF001
    jobs = FakeJobs()

    class EnvBox:
        DB = db
        JOBS = jobs
        LATCH_ENV = "pipeline-local"

    app = create_workers_app()
    await build_state(app, EnvBox)
    services = build_pipeline(EnvBox)
    yield app, db, services, jobs
    db.close()


async def _actor(client: httpx.AsyncClient, name: str) -> dict:
    """登録→同意→token(契約harnessのactor provisioningと同一手順)。"""
    from latch.auth.tools import issue_idp_token

    subject = f"pipe-{name}-{uuid_mod.uuid4().hex[:8]}"
    resp = await client.post(
        "/v1/auth/token",
        json={"provider": "google", "idp_token": issue_idp_token(provider="google", subject=subject)},
    )
    assert resp.status_code == 200, resp.text
    access = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}
    resp = await client.post(
        "/v1/users",
        headers=headers,
        json={
            "display_name": f"パイプライン-{name}",
            "birth_date": "1995-06-15",
            "profile": {},
        },
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["user"]["id"]
    resp = await client.post("/v1/users/me/consent", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    return {"headers": headers, "user_id": user_id}


async def _create_intent(
    client: httpx.AsyncClient, actor: dict, *, start_in_h: float = 6.0
) -> str:
    """matching可能なactive意図を作成(hook: outbox行+schedule行+post-commit配送)。"""
    from latch.core.clock import SystemClock

    start = (SystemClock().now() + timedelta(hours=start_in_h)).isoformat()
    resp = await client.post(
        "/v1/intents",
        headers=actor["headers"],
        json={
            "raw_text": "今度の夜、天文館あたりで軽くごはん。",
            "status": "active",
            "structured_intent": {
                "category": {"primary": "meal", "secondary": None},
                "alcohol_involved": False,
                "time": {"start": start, "end": None},
                "location": {"name": "天文館"},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]["id"]


async def _count(db, sql: str, *params) -> int:
    row = await db.prepare(sql).bind(*params).first()
    return int(row["c"])


async def test_pipeline_end_to_end_flow(stack):
    """意図2件→提案自動生成→回答→matched・通知配送・履歴の一貫性。"""
    app, db, services, jobs = stack
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        u1 = await _actor(client, "a")
        u2 = await _actor(client, "b")
        i1 = await _create_intent(client, u1)
        i2 = await _create_intent(client, u2)

    # -- hook: outbox(pending→post-commit配送でsent)・schedule(due) ----------
    assert await _count(db, "SELECT COUNT(*) AS c FROM outbox") == 2
    assert (
        await _count(db, "SELECT COUNT(*) AS c FROM outbox WHERE status = 'sent'") == 2
    ), "post-commitベストエフォート配送がoutboxをsent化"
    assert len(jobs.sent) == 2, "Queuesへenvelope(JSON文字列・IDのみ)送信"
    for body in jobs.sent:
        envelope = json.loads(body)
        assert set(envelope) == {
            "event_id",
            "intent_id",
            "intent_version",
            "reason",
            "due_generation",
            "writer_generation",
            "payload_schema_version",
        }, "envelopeは§4の参照のみ(原文なし)"
        assert envelope["reason"] == "created"
    assert (
        await _count(
            db,
            "SELECT COUNT(*) AS c FROM intent_evaluation_schedule"
            " WHERE status = 'due' AND next_evaluate_at <="
            " (SELECT MAX(created_at) FROM outbox) + 1",
        )
        == 2
    ), "意図確定と同時に評価schedule登録(即時due)"

    # -- queue consumer: 評価実行(embedding→検索→jev→提案) -------------------
    i2_eval = await _drain(services, jobs)
    assert i2_eval["jev_called"] == 1, "相手を見つけた側がstub jevを1回呼ぶ"
    assert len(i2_eval["proposals"]) == 1, "閾値超えペアの提案が1件生成"
    latch_id = i2_eval["proposals"][0]

    latch = (
        await db.prepare(
            "SELECT status, score, intent_ids, response_deadline FROM latches"
            " WHERE id = ?"
        )
        .bind(latch_id)
        .first()
    )
    assert latch["status"] == "proposed"
    assert float(latch["score"]) == pytest.approx(0.9), "stub jev決定論: 1.0×min(0.9,0.9)"
    assert latch["response_deadline"] is not None, "D-05回答期限式で期限設定"
    assert await _count(
        db,
        "SELECT COUNT(*) AS c FROM match_candidates WHERE jev_result IS NOT NULL"
        " AND latch_score IS NOT NULL AND status = 'evaluated'",
    ) == 1
    assert await _count(
        db, "SELECT COUNT(*) AS c FROM latches WHERE status = 'proposed'"
    ) == 1
    # 提案と通知job・通知行の同時保存(atomic.apply_proposal)
    assert await _count(
        db, "SELECT COUNT(*) AS c FROM jobs WHERE kind = 'notify' AND status = 'pending'"
    ) == 1
    assert await _count(
        db, "SELECT COUNT(*) AS c FROM notifications WHERE type = 'proposal'"
    ) == 2, "参加者2人へ提案通知"
    # llm_attempts: 送信前prepared→応答後finish(stub・usage確定)
    assert await _count(
        db, "SELECT COUNT(*) AS c FROM llm_attempts WHERE system = 'jev'"
        " AND status = 'succeeded'"
    ) == 1

    # -- cron #1: schedule評価(冪等・再提案なし)・notify配送・retention -----
    summary1 = await _cron(services, jobs)
    assert summary1["schedule"]["completed"] >= 1
    assert summary1["jobs"]["notify"] == 1, "提案通知jobの配送(in_app到達)"
    assert (
        await _count(db, "SELECT COUNT(*) AS c FROM notification_deliveries") == 2
    ), "通知行2件の配送記録(安定ID冪等)"
    assert await _count(
        db, "SELECT COUNT(*) AS c FROM latches WHERE status = 'proposed'"
    ) == 1, "再評価で提案は重複しない(open latch UNIQUE+対象外)"
    assert "retention" in summary1, "retention段も実行(snapshotはTTL 2分後掃除)"

    # -- 回答 → matched ------------------------------------------------------
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            f"/v1/latches/{latch_id}/response",
            headers=u1["headers"],
            json={"response": "yes"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["latch"]["status"] == "partial_accept"
        resp = await client.post(
            f"/v1/latches/{latch_id}/response",
            headers=u2["headers"],
            json={"response": "yes"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["latch"]["status"] == "matched"

    assert await _count(
        db, "SELECT COUNT(*) AS c FROM intents WHERE status = 'matched'"
    ) == 2, "全Intent成立"
    assert await _count(
        db, "SELECT COUNT(*) AS c FROM notifications WHERE type = 'latch_matched'"
    ) == 2, "成立通知の先行書き込み(参加者全員)"
    matched_notify_pending = await _count(
        db,
        "SELECT COUNT(*) AS c FROM jobs WHERE kind = 'notify' AND status = 'pending'",
    )
    assert matched_notify_pending == 1, "成立通知job"

    # -- cron #2: 成立通知配送・job収束 --------------------------------------
    summary2 = await _cron(services, jobs)
    assert summary2["jobs"]["notify"] == 1
    assert (
        await _count(db, "SELECT COUNT(*) AS c FROM notification_deliveries") == 4
    ), "提案2+成立2の配送記録"
    assert (
        await _count(
            db,
            "SELECT COUNT(*) AS c FROM jobs WHERE status NOT IN ('succeeded')",
        )
        == 0
    ), "全job収束"
    assert await _count(
        db, "SELECT COUNT(*) AS c FROM outbox WHERE status != 'sent'"
    ) == 0

    # -- 評価済みIntentはscheduleでpaused化(以降の評価機会から外れる) ----------
    assert await _count(
        db,
        "SELECT COUNT(*) AS c FROM intent_evaluation_schedule WHERE status = 'paused'",
    ) == 2


async def _drain(services, jobs) -> dict:
    """queue consumer相当: 送信済みenvelopeを順に処理(後発側の評価結果を返す)。"""
    from latch_worker.pipeline import handle_queue_message

    last = None
    for body in list(jobs.sent):
        last = await handle_queue_message(services, body)
    return last


async def _cron(services, jobs) -> dict:
    from latch_worker.pipeline import run_cron

    return await run_cron(services, now=services.now(), queue_producer=jobs)


async def test_outbox_sweep_resends_pending_at_least_once(stack):
    """送信失敗(outbox pending残存)はcron掃きで再送(at-least-once)。"""
    app, db, services, jobs = stack
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        u1 = await _actor(client, "a")
        await _create_intent(client, u1)

    # post-commit配送を失敗させた状況を再現(outboxをpendingへ戻す)
    await db.prepare(
        "UPDATE outbox SET status = 'pending', sent_at = NULL"
    ).run()
    jobs.sent.clear()
    assert await _count(db, "SELECT COUNT(*) AS c FROM outbox WHERE status = 'pending'") == 1

    jobs.fail_next = True
    summary = await _cron(services, jobs)
    assert summary["outbox"] == {
        "pending": 1,
        "sent": 0,
        "send_failed": 1,
    }, "送信失敗でも行はpendingのまま"
    assert await _count(db, "SELECT COUNT(*) AS c FROM outbox WHERE status = 'pending'") == 1

    summary = await _cron(services, jobs)
    assert summary["outbox"]["sent"] == 1, "次周期で再送"
    assert await _count(db, "SELECT COUNT(*) AS c FROM outbox WHERE status != 'sent'") == 0


async def test_redelivery_and_cron_do_not_duplicate_latch(stack):
    """envelope再配送+cron再評価が重なっても提案・Jev試行は1つのまま(冪等)。"""
    app, db, services, jobs = stack
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        u1 = await _actor(client, "a")
        u2 = await _actor(client, "b")
        await _create_intent(client, u1)
        await _create_intent(client, u2)

    # 意図的な二重配送(同一envelope 2回)+ cron 1周期
    from latch_worker.pipeline import handle_queue_message

    for body in list(jobs.sent) * 2:
        await handle_queue_message(services, body)
    await _cron(services, jobs)

    assert await _count(db, "SELECT COUNT(*) AS c FROM latches") == 1
    assert await _count(
        db, "SELECT COUNT(*) AS c FROM llm_attempts WHERE system = 'jev'"
    ) == 1, "I1 claim: 同一評価キーの再実行は外部呼出し0回"
    assert await _count(
        db, "SELECT COUNT(*) AS c FROM match_candidates"
    ) == 1, "候補行はUNIQUE(a,b,va,vb)で1行"


async def test_cron_alone_proposes_without_queue_dispatch(stack):
    """post-commit配送が全滅してもcron(schedule due+outbox掃き)で提案まで到達。"""
    app, db, services, _jobs = stack
    # dispatcher無効構成で意図作成(post-commit配送なし)
    from latch_worker.app import create_workers_app
    from latch_worker.state import build_state

    class EnvBoxNoJobs:
        DB = db
        LATCH_ENV = "pipeline-local-noqueue"

    app2 = create_workers_app()
    await build_state(app2, EnvBoxNoJobs)
    transport = httpx.ASGITransport(app=app2)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        u1 = await _actor(client, "x1")
        u2 = await _actor(client, "x2")
        await _create_intent(client, u1)
        await _create_intent(client, u2)

    assert await _count(db, "SELECT COUNT(*) AS c FROM outbox WHERE status = 'pending'") == 2

    # cron only: outbox掃き→(fake producerで受信)→schedule評価で提案まで
    drain_producer = FakeJobs()
    summary = await _cron(services, drain_producer)
    assert summary["outbox"]["sent"] == 2
    from latch_worker.pipeline import handle_queue_message

    for body in drain_producer.sent:
        await handle_queue_message(services, body)
    assert await _count(db, "SELECT COUNT(*) AS c FROM latches WHERE status = 'proposed'") == 1
    # さらにcronを回しても重複しない
    await _cron(services, drain_producer)
    assert await _count(db, "SELECT COUNT(*) AS c FROM latches") == 1
