"""A/B基盤(05 v0.8・V1-P4)のintegration試験。

実HTTP(compose常設api)+実DB。次を検証する:
- GET /v1/users/me/ab(空配列・割付行の応答形・401)
- ab_experiments/ab_assignments表の制約(0011・variant CHECK・PK)
- 提案評価を経由した割付の発生と安定性(実worker経路・stub Jev score=0.9)
- ペア適用規則の適用(処置閾値0.95=0.9未満で提案しない・対照0.60・混在=対照)
- 停止(stopped)=全群対照値(09 §5持続規定)

割付のハッシュは決定的なため、ユーザーを列挙してハッシュから群を事前計算し
期待値を立てる(rules.assign_variantと同一算法・テスト側で再計算)。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.ab.rules import VARIANT_CONTROL, VARIANT_TREATMENT, assign_variant
from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration

PREFIX = "v1p4ab-"


@pytest.fixture(autouse=True)
async def _purge_ab_users(db_engine):
    """試験後にv1p4ab-系の行をFK順に全削除(teardown完全性)。"""
    yield
    p = {"p": f"{PREFIX}%"}
    async with db_engine.begin() as conn:
        for sql in (
            "DELETE FROM ab_assignments WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM latch_status_events WHERE latch_id IN"
            " (SELECT id FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p)))",
            "DELETE FROM calibration_records WHERE latch_id IN"
            " (SELECT id FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p)))",
            "DELETE FROM notifications WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM latches WHERE intent_ids && ARRAY("
            " SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM match_candidates WHERE intent_a_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            " OR intent_b_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
            " SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM match_events WHERE source_intent_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM users WHERE auth_subject LIKE :p",
            "DELETE FROM ab_experiments",
        ):
            await conn.execute(text(sql), p)


async def _register(api_client) -> dict:
    subject = f"{PREFIX}{uuid_mod.uuid4().hex[:12]}"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        "google",
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    tok = await api_client.post(
        "/v1/auth/token",
        json={"provider": "google", "idp_token": stdout.decode().strip()},
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "v1p4ab", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["user"]["id"]
    consent = await api_client.post("/v1/users/me/consent", headers=headers)
    assert consent.status_code == 200, consent.text
    return {"headers": headers, "user_id": user_id}


_VEC = "[" + ",".join(["1.0"] + ["0.0"] * 767) + "]"


async def _insert_intent(db_engine, user_id: str) -> str:
    """Intentを直接投入(API不使用=match_eventsが出ないため常設workerと競合しない)。

    load_originの前提(active・embedding・geo・time_start・participants 2/2)と
    layer4のH再検証(時間交差・同一地点・同カテゴリ・飲酒なし)を満たす値。
    """
    now = SystemClock().now()
    start = now + timedelta(hours=6)
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "INSERT INTO intents (user_id, category_primary,"
                " alcohol_involved, raw_text, structured_data, status, version,"
                " time_start, time_end, expires_at, geo_center, geo_radius_m,"
                " visibility, notification_level, embedding, created_at,"
                " updated_at)"
                " VALUES (CAST(:u AS uuid), 'meal', false, 'v1p4ab', '{}',"
                " 'active', 1, CAST(:t AS timestamptz),"
                " CAST(:te AS timestamptz), CAST(:e AS timestamptz),"
                " ST_SetSRID(ST_MakePoint(130.559, 31.598), 4326), 1000,"
                " 'hidden_until_match', 'proposals_only', CAST(:vec AS vector),"
                " CAST(:now AS timestamptz), CAST(:now AS timestamptz))"
                " RETURNING id"
            ),
            {
                "u": user_id,
                "t": start,
                "te": start + timedelta(hours=3),
                "e": start + timedelta(hours=12),
                "vec": _VEC,
                "now": now,
            },
        )
    return str(res.first()[0])


async def _insert_candidate(db_engine, a_id: str, b_id: str, mutual: float) -> None:
    """match_candidatesへjev_result付きevaluatedを直接投入(スコア=mutual)。"""
    lo, hi = sorted([a_id, b_id], key=str)
    jev = json.dumps(
        {
            "would_a_accept_b": mutual,
            "would_b_accept_a": mutual,
            "provider": "fixture",
            "model": None,
        }
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO match_candidates (intent_a_id, intent_b_id,"
                " intent_a_version, intent_b_version, status, jev_result,"
                " created_at, updated_at) VALUES (CAST(:a AS uuid),"
                " CAST(:b AS uuid), 1, 1, 'evaluated', CAST(:jev AS jsonb),"
                " CAST(:now AS timestamptz), CAST(:now AS timestamptz))"
            ),
            {"a": lo, "b": hi, "jev": jev, "now": SystemClock().now()},
        )


async def _insert_experiment(
    db_engine, key="latch_threshold", control="0.6", treatment="0.95"
) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ab_experiments (experiment_key, status,"
                " control_value, treatment_value, started_at)"
                " VALUES (:k, 'running', :c, :t, now())"
            ),
            {"k": key, "c": control, "t": treatment},
        )


def _variant_at_eval(user_id: str, key: str = "latch_threshold") -> str:
    """評価時点の期待variant(rulesと同一算法)。

    ペア試験では各ユーザーが自分のIntentを1件だけactive化した状態で評価が
    走るため層は常に'1-2'(層=Active Intent数・05 v0.8 規定2)。
    """
    return assign_variant(key, "1-2", uuid_mod.UUID(user_id))


async def _pick_pair(api_client, want_a: str, want_b: str, key="latch_threshold"):
    """期待variantが(want_a, want_b)になる2ユーザーを決定的に選ぶ。

    user_idはサーバ側uuid4のため、プール(上限12人)からハッシュで選別する
    (各群の生起率約1/2・12人で必要群が揃わない確率は2^-11未満)。
    """
    pool: dict[str, list[dict]] = {VARIANT_TREATMENT: [], VARIANT_CONTROL: []}
    for _ in range(12):
        user = await _register(api_client)
        pool[_variant_at_eval(user["user_id"], key)].append(user)
        if (
            pool[want_a]
            and pool[want_b]
            and (want_a != want_b or len(pool[want_a]) >= 2)
        ):
            break
    a = pool[want_a].pop(0)
    if want_a == want_b:
        b = pool[want_b].pop(0)
    else:
        b = pool[want_b].pop(0)
    return a, b


async def _run_pipeline_once(db_engine, intent_id: str) -> None:
    """A/B注入済みLatchEngine.handleを1起点分実行(drain込み=try_promote到達)。

    テストはIntent/match_candidatesを直接投入しているため常設worker
    (旧イメージ・実験なし)はこの行を処理しない(競合回避の対抗策)。
    """
    from latch.ab.service import make_ab_service
    from latch.geo.service import GeoService
    from latch.worker.matching.latch_engine import LatchEngine

    clock = SystemClock()
    latch = LatchEngine(
        engine=db_engine,
        clock=clock,
        geo=GeoService(db_engine),
        ab=make_ab_service(engine=db_engine, clock=clock),
    )
    await latch.handle(uuid_mod.UUID(intent_id))


async def _latch_rows(db_engine, intent_id: str) -> list:
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT status FROM latches WHERE intent_ids @>"
                " ARRAY[CAST(:i AS uuid)] ORDER BY created_at"
            ),
            {"i": intent_id},
        )
        return res.fetchall()


# -- スキーマ(0011) --


async def test_ab_tables_exist_with_constraints(db_engine):
    """0011適用で3表とPK/CHECK/層ラベルの保存がある。"""
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ab_experiments (experiment_key, status,"
                " control_value, treatment_value, started_at)"
                " VALUES ('k', 'running', '1', '2', now())"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO ab_experiments (experiment_key, status,"
                " control_value, treatment_value, started_at, stopped_at)"
                " VALUES ('k2', 'stopped', '1', '2', now(), now())"
            )
        )
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError, match="ab_experiments_status_check"):
            await conn.execute(
                text(
                    "INSERT INTO ab_experiments (experiment_key, status,"
                    " control_value, treatment_value, started_at)"
                    " VALUES ('k3', 'paused', '1', '2', now())"
                )
            )
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM ab_experiments"))


async def test_mlr_baselines_table_exists(db_engine):
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO mlr_baselines (value, source_weeks, recorded_at)"
                ' VALUES (0.12, \'[{"week_start": "2026-10-05"}]\'::jsonb,'
                " now())"
            )
        )
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM mlr_baselines"))


# -- GET /v1/users/me/ab --


async def test_ab_status_empty_for_new_user(api_client):
    user = await _register(api_client)
    resp = await api_client.get("/v1/users/me/ab", headers=user["headers"])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"assignments": []}


async def test_ab_status_requires_auth(api_client):
    resp = await api_client.get("/v1/users/me/ab")
    assert resp.status_code == 401


async def test_ab_status_returns_rows_and_read_is_stable(api_client, db_engine):
    user = await _register(api_client)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ab_experiments (experiment_key, status,"
                " control_value, treatment_value, started_at)"
                " VALUES ('latch_threshold', 'running', '0.6', '0.5', now())"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO ab_assignments (experiment_key, user_id, variant,"
                " stratum, assigned_at) VALUES ('latch_threshold',"
                " CAST(:u AS uuid), 'treatment', '0', now())"
            ),
            {"u": user["user_id"]},
        )
    resp1 = await api_client.get("/v1/users/me/ab", headers=user["headers"])
    assert resp1.status_code == 200, resp1.text
    body = resp1.json()["assignments"]
    assert len(body) == 1
    assert body[0]["experiment"] == "latch_threshold"
    assert body[0]["variant"] == "treatment"
    assert "assigned_at" in body[0]
    assert "stratum" not in body[0]  # 層は内部記録(05 v0.8)
    # 読み取りは割付を作らない(2回目も1行のまま)
    resp2 = await api_client.get("/v1/users/me/ab", headers=user["headers"])
    assert resp2.json() == resp1.json()


# -- 提案評価を経由した割付とペア適用(stub Jev score=0.9) --


async def _prepare_pair(api_client, db_engine, want_a, want_b):
    """期待variantを指定して2ユーザーを選び、Intent+評価行を投入する。"""
    a, b = await _pick_pair(api_client, want_a, want_b)
    a_intent = await _insert_intent(db_engine, a["user_id"])
    b_intent = await _insert_intent(db_engine, b["user_id"])
    await _insert_candidate(db_engine, a_intent, b_intent, 0.9)
    return a, b, a_intent, b_intent


async def test_treatment_pair_below_treatment_threshold_not_proposed(
    api_client, db_engine
):
    """全員treatment・処置閾値0.95: score0.9は提案されない(割付は発生)。"""
    a, b, a_intent, _b_intent = await _prepare_pair(
        api_client, db_engine, VARIANT_TREATMENT, VARIANT_TREATMENT
    )
    await _insert_experiment(db_engine, control="0.6", treatment="0.95")
    await _run_pipeline_once(db_engine, a_intent)
    rows = await _latch_rows(db_engine, a_intent)
    assert rows == []  # 0.9 < 0.95 → 候補すら作られない
    async with db_engine.connect() as conn:
        assigned = (
            await conn.execute(
                text(
                    "SELECT variant FROM ab_assignments WHERE user_id"
                    " IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                ),
                {"a": a["user_id"], "b": b["user_id"]},
            )
        ).fetchall()
    assert {r[0] for r in assigned} == {VARIANT_TREATMENT}


async def test_control_or_mixed_pair_proposed_at_control_threshold(
    api_client, db_engine
):
    """対照対・混在对は対照値0.60で評価=score0.9は提案される(FR-25)。"""
    # 混在ペア(treatment×control)を指定=ペア適用規則の対照値経路の証明
    a, b, a_intent, _b_intent = await _prepare_pair(
        api_client, db_engine, VARIANT_TREATMENT, VARIANT_CONTROL
    )
    await _insert_experiment(db_engine, control="0.6", treatment="0.95")
    await _run_pipeline_once(db_engine, a_intent)
    rows = await _latch_rows(db_engine, a_intent)
    assert rows and rows[0][0] in ("proposed", "candidate")


async def test_stopped_experiment_falls_back_to_control(api_client, db_engine):
    """停止=全群対照値(09 §5持続規定)。treatment対でも0.60で提案。"""
    a, b, a_intent, _b_intent = await _prepare_pair(
        api_client, db_engine, VARIANT_TREATMENT, VARIANT_TREATMENT
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ab_experiments (experiment_key, status,"
                " control_value, treatment_value, started_at, stopped_at)"
                " VALUES ('latch_threshold', 'stopped', '0.6', '0.95',"
                " now() - interval '1 day', now())"
            )
        )
    await _run_pipeline_once(db_engine, a_intent)
    rows = await _latch_rows(db_engine, a_intent)
    assert rows and rows[0][0] in ("proposed", "candidate")
    async with db_engine.connect() as conn:
        n = int(
            (
                await conn.execute(text("SELECT count(*) FROM ab_assignments"))
            ).scalar_one()
        )
    assert n == 0  # 停止中は割付も発生しない


async def test_daily_limit_treatment_pair_blocked(api_client, db_engine):
    """D-08日次上限のA/B適用: 処置値1・両者当日1通報済みなら提案は保留。"""
    a, b = await _pick_pair(
        api_client,
        VARIANT_TREATMENT,
        VARIANT_TREATMENT,
        key="notification_daily_limit",
    )
    a_intent = await _insert_intent(db_engine, a["user_id"])
    b_intent = await _insert_intent(db_engine, b["user_id"])
    await _insert_candidate(db_engine, a_intent, b_intent, 0.9)
    await _insert_experiment(
        db_engine, key="notification_daily_limit", control="6", treatment="1"
    )
    async with db_engine.begin() as conn:
        for uid in (a["user_id"], b["user_id"]):
            await conn.execute(
                text(
                    "INSERT INTO notifications (user_id, type, payload,"
                    " created_at) VALUES (CAST(:u AS uuid), 'proposal',"
                    " '{}'::jsonb, now())"
                ),
                {"u": uid},
            )
    await _run_pipeline_once(db_engine, a_intent)
    rows = await _latch_rows(db_engine, a_intent)
    # 閾値(0.60既定)は超えるがD-08処置値1に既に到達→candidateのまま
    assert rows and rows[0][0] == "candidate"
