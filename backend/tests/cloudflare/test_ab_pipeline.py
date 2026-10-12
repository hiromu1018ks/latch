"""A/B基盤のCF(D1)側パイプライン試験(05 v0.8・V1-P4・E-01体制)。

in-process D1(LocalD1Database)+実pipeline(build_pipeline)+ASGI appで、
ab_experiments行の有無による提案閾値の適用と割付の永続化を検証する:
- 実験なし: 従来どおりscore 0.9が提案される(挙動不変・05 v0.8 規定5)
- 処置閾値0.95(treatment×treatment): score 0.9は提案されない・割付が発生
- 混在ペアと停止(stopped): 対照値0.60で評価(FR-25・09 §5持続規定)

stub Jevはnoul固定のためpair score=0.9で確定する。割付ハッシュは決定的
なため、actorを列挙して期待variantの組み合わせを選ぶ(参照実装の
test_ab_api.pyと同一手法)。
"""

from __future__ import annotations

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
    async def send(self, body: str) -> None:
        pass


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
        LATCH_ENV = "ab-local"

    app = create_workers_app()
    await build_state(app, EnvBox)
    services = build_pipeline(EnvBox)
    yield app, db, services
    db.close()


async def _actor(client: httpx.AsyncClient, name: str) -> dict:
    from latch.auth.tools import issue_idp_token

    subject = f"ab-{name}-{uuid_mod.uuid4().hex[:8]}"
    resp = await client.post(
        "/v1/auth/token",
        json={
            "provider": "google",
            "idp_token": issue_idp_token(provider="google", subject=subject),
        },
    )
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    resp = await client.post(
        "/v1/users",
        headers=headers,
        json={
            "display_name": f"ab-{name}",
            "birth_date": "1995-06-15",
            "profile": {},
        },
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["user"]["id"]
    resp = await client.post("/v1/users/me/consent", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    return {"headers": headers, "user_id": user_id}


async def _create_intent(client: httpx.AsyncClient, actor: dict) -> str:
    from latch.core.clock import SystemClock

    start = (SystemClock().now() + timedelta(hours=6)).isoformat()
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


def _variant_at_eval(user_id: str, key: str = "latch_threshold") -> str:
    """評価時点の期待variant(各actorは自分のIntent 1件のみactive→層'1-2')。"""
    from latch.ab.rules import assign_variant

    return assign_variant(key, "1-2", uuid_mod.UUID(user_id))


async def _pick_actors(client: httpx.AsyncClient, want_a: str, want_b: str):
    from latch.ab.rules import VARIANT_CONTROL, VARIANT_TREATMENT

    pool = {VARIANT_TREATMENT: [], VARIANT_CONTROL: []}
    for i in range(12):
        actor = await _actor(client, f"p{i}")
        pool[_variant_at_eval(actor["user_id"])].append(actor)
        if (
            pool[want_a]
            and pool[want_b]
            and (want_a != want_b or len(pool[want_a]) >= 2)
        ):
            break
    a = pool[want_a].pop(0)
    b = pool[want_b].pop(0)
    return a, b


async def _insert_experiment(
    db, *, status: str = "running", control: str = "0.6", treatment: str = "0.95"
) -> None:
    from latch.core.clock import SystemClock
    from latch.domain.time import micros

    now = micros(SystemClock().now())
    if status == "running":
        await (
            db.prepare(
                "INSERT INTO ab_experiments (experiment_key, status,"
                " control_value, treatment_value, started_at, stopped_at)"
                " VALUES ('latch_threshold', 'running', ?, ?, ?, NULL)"
                " ON CONFLICT (experiment_key) DO UPDATE SET"
                " status='running', control_value=excluded.control_value,"
                " treatment_value=excluded.treatment_value,"
                " started_at=excluded.started_at, stopped_at=NULL"
            )
            .bind(control, treatment, now)
            .first()
        )
    else:
        await (
            db.prepare(
                "INSERT INTO ab_experiments (experiment_key, status,"
                " control_value, treatment_value, started_at, stopped_at)"
                " VALUES ('latch_threshold', 'stopped', ?, ?, ?, ?)"
                " ON CONFLICT (experiment_key) DO UPDATE SET"
                " status='stopped', control_value=excluded.control_value,"
                " treatment_value=excluded.treatment_value,"
                " started_at=excluded.started_at, stopped_at=excluded.stopped_at"
            )
            .bind(control, treatment, now, now)
            .first()
        )


async def _evaluate(services, intent_id: str) -> None:
    from latch_worker.pipeline import evaluate_intent

    from latch.core.clock import SystemClock

    await evaluate_intent(services, intent_id, now=SystemClock().now(), owner="ab-test")


async def _latch_statuses(db, intent_id: str) -> list[str]:
    rows = (
        await db.prepare(
            "SELECT status FROM latches WHERE EXISTS ("
            " SELECT 1 FROM json_each(latches.intent_ids) je"
            " WHERE je.value = ?)"
        )
        .bind(intent_id)
        .all()
    )
    return sorted(r["status"] for r in rows)


async def _ab_assignment_count(db, *user_ids: str) -> int:
    placeholders = ",".join("?" for _ in user_ids)
    row = (
        await db.prepare(
            f"SELECT COUNT(*) AS c FROM ab_assignments"
            f" WHERE user_id IN ({placeholders})"
        )
        .bind(*user_ids)
        .first()
    )
    return int(row["c"])


async def test_pipeline_without_experiment_proposes(stack):
    """実験行なし=従来どおり(score 0.9 ≥ 0.60で提案・割付は発生しない)。"""
    from latch.ab.rules import VARIANT_TREATMENT

    app, db, services = stack
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        a, b = await _pick_actors(client, VARIANT_TREATMENT, VARIANT_TREATMENT)
        a_intent = await _create_intent(client, a)
        b_intent = await _create_intent(client, b)
    # 両起点を評価(2つ目の評価で1つ目=embedding済みとペアになる・e2eと同型)
    await _evaluate(services, a_intent)
    await _evaluate(services, b_intent)
    statuses = await _latch_statuses(db, a_intent)
    assert statuses and all(s == "proposed" for s in statuses)
    assert await _ab_assignment_count(db) == 0


async def test_treatment_threshold_blocks_proposal_and_assigns(stack):
    """treatment×treatment+処置閾値0.95: score 0.9は提案されない・割付は永続化。"""
    from latch.ab.rules import VARIANT_TREATMENT

    app, db, services = stack
    await _insert_experiment(db, control="0.6", treatment="0.95")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        a, b = await _pick_actors(client, VARIANT_TREATMENT, VARIANT_TREATMENT)
        a_intent = await _create_intent(client, a)
        b_intent = await _create_intent(client, b)
    await _evaluate(services, a_intent)
    await _evaluate(services, b_intent)
    assert await _latch_statuses(db, a_intent) == []
    assert await _ab_assignment_count(db, a["user_id"], b["user_id"]) == 2
    rows = (
        await db.prepare("SELECT variant FROM ab_assignments WHERE user_id IN (?, ?)")
        .bind(a["user_id"], b["user_id"])
        .all()
    )
    assert {r["variant"] for r in rows} == {"treatment"}


async def test_mixed_pair_and_stopped_use_control(stack):
    """混在ペアは対照値で提案・停止(stopped)は全群対照値+割付なし。"""
    from latch.ab.rules import VARIANT_CONTROL, VARIANT_TREATMENT

    app, db, services = stack
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        a, b = await _pick_actors(client, VARIANT_TREATMENT, VARIANT_CONTROL)
        a_intent = await _create_intent(client, a)
        b_intent = await _create_intent(client, b)
    await _insert_experiment(db, control="0.6", treatment="0.95")
    await _evaluate(services, a_intent)
    await _evaluate(services, b_intent)
    statuses = await _latch_statuses(db, a_intent)
    assert statuses and all(s == "proposed" for s in statuses)  # 対照値0.60

    # stoppedへ切替(判定不能時の持続規定の表現・05 v0.8 規定6)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        c, d = await _pick_actors(client, VARIANT_TREATMENT, VARIANT_TREATMENT)
        c_intent = await _create_intent(client, c)
        d_intent = await _create_intent(client, d)
    await _insert_experiment(db, status="stopped", control="0.6", treatment="0.95")
    # 実験行は30秒キャッシュ(05 v0.8 規定7)のため、停止の伝播を新しい
    # AbServiceの注入で表現する(TTL経過と同一の効果)
    from latch_worker.svc_ab import build_ab_service

    services.ab = build_ab_service(db, services.clock)
    await _evaluate(services, c_intent)
    await _evaluate(services, d_intent)
    statuses = await _latch_statuses(db, c_intent)
    assert statuses and all(s == "proposed" for s in statuses)
    assert await _ab_assignment_count(db, c["user_id"], d["user_id"]) == 0
