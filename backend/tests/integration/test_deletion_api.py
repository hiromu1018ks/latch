"""単発Intent削除API+物理削除カスケードのintegration試験(M3 ws-6 design §4.2)。

実DB(compose常設)・api常設(実HTTP)。物理削除はstage1のEvent処理経由のため、
DELETE APIを叩いた後、Stage1を直接構築して削除Eventを投入(groupengine
test_8流儀)。候補・latches・calibrationはfixtureで直接INSERT(パイプラインを
走らせない — test_safety_api.py流儀)。subjectプレフィックス m3ws6- ・
teardownはFK順+本単位テーブル。時間値はnow相対。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock
from latch.settings import Settings
from latch.worker.stage1 import Stage1

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120
SUBJECT_PREFIX = "m3ws6-"


@pytest.fixture
async def field(db_engine):
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        # FK順: messages → latch_status_events → calibration_records →
        # notifications → latches → group_candidates → match_candidates →
        # match_events → blocks → intents → users
        await conn.execute(
            text(
                "DELETE FROM messages WHERE latch_id IN (SELECT id FROM latches"
                " WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM"
                " latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE"
                " user_id IN (SELECT id FROM users WHERE auth_subject LIKE :p)))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM calibration_records WHERE intent_ids && ARRAY("
                "SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM notifications WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM latches WHERE intent_ids && ARRAY("
                "SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
                "SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
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


async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        provider,
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def _user(api_client, prefix: str, name: str = "m3ws6") -> dict:
    """ユーザー登録 → {"headers", "id", "display_name"}(登録応答のuser.id)。"""
    subject = f"{prefix}{uuid_mod.uuid4().hex[:8]}"
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": name, "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    user = created.json()["user"]
    return {"headers": headers, "id": user["id"], "display_name": name}


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured(*, secondary: str | None = None, start: str | None = None) -> dict:
    return {
        "category": {"primary": CATEGORY, "secondary": secondary},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }


async def _intent(api_client, headers, *, start: str | None = None) -> dict:
    resp = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={
            "raw_text": "分類用テキスト",
            "status": "active",
            "structured_intent": _structured(start=start),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]


async def _candidate(
    db_engine, a: str, b: str, status: str, a_version: int = 1
) -> None:
    """match_candidates行を直接INSERT(全status仕込み用)。

    UNIQUE(intent_a_id, intent_b_id, intent_a_version, intent_b_version)
    があるため、同一ペアへ複数行仕込むときはa_versionを変える
    (再評価世代違い=処理済み行の実態)。
    """
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO match_candidates
                    (intent_a_id, intent_b_id, intent_a_version,
                     intent_b_version, status, created_at, updated_at)
                VALUES (CAST(:a AS uuid), CAST(:b AS uuid), :av, 1, :status,
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
            """),
            {
                "a": a if a < b else b,
                "b": b if a < b else a,
                "av": a_version,
                "status": status,
                "now": now,
            },
        )


async def _group(db_engine, intent_ids: list) -> str:
    """group_candidates行を直接INSERT(3〜4 Intent)。"""
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO group_candidates
                    (intent_ids, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), 'candidate',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in sorted(intent_ids, key=str)],
                "now": now,
            },
        )
    return str(res.first()[0])


async def _latch(
    db_engine, intent_ids: list, *, status: str, gid: str | None = None
) -> str:
    """latches行を直接INSERT(test_safety_api.pyの_latchと同型)。"""
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, group_candidate_id, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:gid AS uuid),
                        CAST(:proposal AS jsonb), :score, :status,
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in intent_ids],
                "gid": uuid_mod.UUID(gid) if gid else None,
                "proposal": json.dumps(
                    {"headcount": len(intent_ids), "match_level": "medium"},
                    ensure_ascii=False,
                ),
                "score": 0.85,
                "status": status,
                "deadline": now + timedelta(hours=2.0),
                "expires": now + timedelta(hours=120.0),
                "now": now,
            },
        )
    return str(res.first()[0])


async def _calibration(
    db_engine, latch_id: str, intent_ids: list, user_ids: list
) -> None:
    """calibration_records行を直接INSERT(actual_responsesにuser_idを含む)。

    answered_atは要素ごとに1分差(匿名化後の配列順検証用・idx=0が先頭)。
    """
    now = SystemClock().now()
    responses = [
        {
            "user_id": u,
            "response": "yes",
            "answered_at": (now - timedelta(minutes=5 - idx)).isoformat(),
        }
        for idx, u in enumerate(user_ids)
    ]
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO calibration_records
                    (latch_id, intent_ids, prediction, proposal_snapshot,
                     actual_responses, matched, created_at, updated_at)
                VALUES (CAST(:lid AS uuid), CAST(:ids AS uuid[]),
                        CAST(:prediction AS jsonb),
                        CAST(:snapshot AS jsonb),
                        CAST(:responses AS jsonb), true,
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
            """),
            {
                "lid": uuid_mod.UUID(latch_id),
                "ids": [uuid_mod.UUID(i) for i in intent_ids],
                "prediction": json.dumps({"latch_score": 0.8}),
                "snapshot": json.dumps({"headcount": 2}),
                "responses": json.dumps(responses, ensure_ascii=False),
                "now": now,
            },
        )


async def _run_deleted_event(db_engine, intent_id: str, version: int) -> str:
    """APIが発行した削除Eventと同一3点組をStage1へ直接投入(test_8流儀)。"""
    from latch.core.clock import FakeClock
    from latch.events import IncomingEvent

    payload = json.dumps(
        {
            "event_type": "deleted",
            "source_intent_id": intent_id,
            "version": version,
        }
    ).encode()
    event = IncomingEvent.from_payload(
        message_id=f"deleted-{intent_id}", payload=payload, ack=lambda: None
    )
    stage1 = Stage1(
        engine=db_engine,
        clock=FakeClock(SystemClock().now()),
        settings=Settings(),
    )
    result = await stage1.intake(event)
    assert result.kind == "processed"
    return "processed"


async def _scalar(db_engine, sql: str, params: dict):
    async with db_engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def _first(db_engine, sql: str, params: dict):
    async with db_engine.connect() as conn:
        return (await conn.execute(text(sql), params)).first()


async def test_1_delete_api_cascade_via_stage1(api_client, db_engine, field):
    """design §4.1①〜⑥: 処理済み含む全候補削除・latchesクローズ・復帰・
    calibration匿名化・Intent行消滅・他ユーザー無傷。"""
    u1 = await _user(api_client, field, "退会者")
    u2 = await _user(api_client, field, "相手")
    u3 = await _user(api_client, field, "第三者")
    i1 = (await _intent(api_client, u1["headers"]))["id"]  # 削除対象
    i2 = (await _intent(api_client, u2["headers"], start=_future(130)))["id"]
    i3 = (await _intent(api_client, u3["headers"]))["id"]
    i2b = (await _intent(api_client, u2["headers"]))["id"]  # 他ユーザー専用
    # ① match_candidates 4status(処理済み含む — UNIQUE回避のためa_version違い)
    for idx, st in enumerate(("pending", "evaluated", "skipped", "closed")):
        await _candidate(db_engine, i1, i2, st, a_version=idx + 1)
    await _candidate(db_engine, i2, i2b, "evaluated")  # 無傷検証用
    # ②③ group+latches(開いている)
    gid = await _group(db_engine, [i1, i2, i3])
    open_latch = await _latch(db_engine, [i1, i2, i3], status="proposed", gid=gid)
    # ④ matched LATCH 2本(復帰2分岐: i2=expires未来→active・期限切れi4→expired)
    m1 = await _latch(db_engine, [i1, i2], status="matched")
    i4 = (await _intent(api_client, u2["headers"]))["id"]
    async with db_engine.begin() as conn:  # i4を期限切れmatchedへ直UPDATE
        await conn.execute(
            text(
                "UPDATE intents SET status='matched',"
                " expires_at = now() - interval '1 hour' WHERE id = CAST(:i AS uuid)"
            ),
            {"i": i4},
        )
    await _latch(db_engine, [i1, i4], status="matched")  # m2(expired復帰検証用)
    # ⑤ calibration(actual_responsesにu1/u2のuser_id)
    await _calibration(db_engine, m1, [i1, i2], [u1["id"], u2["id"]])

    resp = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert resp.status_code == 204, resp.text
    await _run_deleted_event(db_engine, i1, 1)

    # ① 候補全行消滅(処理済み含む)・他ユーザー行は無傷
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM match_candidates WHERE"
            " intent_a_id = CAST(:i AS uuid) OR intent_b_id = CAST(:i AS uuid)",
            {"i": i1},
        )
        == 0
    )
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM match_candidates WHERE"
            " intent_a_id = CAST(:a AS uuid) AND intent_b_id = CAST(:b AS uuid)",
            {"a": min(i2, i2b), "b": max(i2, i2b)},
        )
        == 1
    )
    # ②③ group消滅・latchesのFK NULL化+cancelled
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM group_candidates WHERE"
            " intent_ids && ARRAY[CAST(:i AS uuid)]",
            {"i": i1},
        )
        == 0
    )
    assert (
        await _scalar(
            db_engine,
            "SELECT status FROM latches WHERE id = CAST(:l AS uuid)",
            {"l": open_latch},
        )
        == "cancelled"
    )
    assert (
        await _scalar(
            db_engine,
            "SELECT group_candidate_id FROM latches WHERE id = CAST(:l AS uuid)",
            {"l": open_latch},
        )
        is None
    )
    # ④ matched解散+イベント(user_id NULL)+復帰2分岐
    assert (
        await _scalar(
            db_engine,
            "SELECT status FROM latches WHERE id = CAST(:l AS uuid)",
            {"l": m1},
        )
        == "cancelled"
    )
    ev = await _scalar(
        db_engine,
        "SELECT user_id FROM latch_status_events WHERE latch_id = CAST(:l AS uuid)"
        " AND to_status = 'cancelled'",
        {"l": m1},
    )
    assert ev is None
    assert (
        await _scalar(
            db_engine,
            "SELECT status FROM intents WHERE id = CAST(:i AS uuid)",
            {"i": i2},
        )
        == "active"
    )  # expires_at未来→active復帰
    assert (
        await _scalar(
            db_engine,
            "SELECT status FROM intents WHERE id = CAST(:i AS uuid)",
            {"i": i4},
        )
        == "expired"
    )  # expires_at経過→expired復帰
    # ⑤ calibration匿名化(D-13第一段)
    row = await _first(
        db_engine,
        "SELECT latch_id, intent_ids, actual_responses FROM"
        " calibration_records WHERE latch_id = CAST(:l AS uuid)",
        {"l": m1},
    )
    assert row is not None
    latch_id_v, intent_ids_v, responses = row
    if isinstance(responses, str):  # asyncpgのjsonbはstrで返る場合がある
        responses = json.loads(responses)
    assert latch_id_v is None
    assert intent_ids_v is None
    assert [r for r in responses if "user_id" in r] == []
    assert responses[0]["response"] == "yes"  # 回答種別は残す
    assert responses[0]["answered_at"]  # 時刻は残す
    # 配列順保存(WITH ORDINALITY): answered_at降順=idx0(u1分)→idx1(u2分)
    assert responses[0]["answered_at"] > responses[1]["answered_at"]
    # ⑥ Intent行消滅
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM intents WHERE id = CAST(:i AS uuid)",
            {"i": i1},
        )
        == 0
    )
    # 他ユーザー無傷
    assert (
        await _scalar(
            db_engine,
            "SELECT display_name FROM users WHERE id = CAST(:u AS uuid)",
            {"u": u2["id"]},
        )
        == "相手"
    )


async def test_2_delete_matched_intent_204(api_client, db_engine, field):
    """FR-19: matched IntentのDELETEは204(従来422・M3 ws-6拡張)。"""
    u1 = await _user(api_client, field)
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE intents SET status='matched' WHERE id = CAST(:i AS uuid)"),
            {"i": i1},
        )
    resp = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert resp.status_code == 204, resp.text


async def test_3_delete_expired_intent_204(api_client, db_engine, field):
    """expired IntentのDELETEも204(raw_text残存回避)。"""
    u1 = await _user(api_client, field)
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE intents SET status='expired' WHERE id = CAST(:i AS uuid)"),
            {"i": i1},
        )
    resp = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert resp.status_code == 204, resp.text


async def test_4_delete_cancelled_twice_204_idempotent(api_client, db_engine, field):
    """cancelled済みへの再DELETEも204・Event行は重複しない(ON CONFLICT)。"""
    u1 = await _user(api_client, field)
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    first = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert first.status_code == 204
    second = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert second.status_code == 204
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM match_events WHERE"
            " source_intent_id = CAST(:i AS uuid) AND event_type = 'deleted'",
            {"i": i1},
        )
        == 1
    )


async def test_5_delete_other_users_intent_403(api_client, field):
    u1 = await _user(api_client, field)
    u2 = await _user(api_client, field)
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    resp = await api_client.delete(f"/v1/intents/{i1}", headers=u2["headers"])
    assert resp.status_code == 403, resp.text


async def test_6_delete_unknown_intent_404(api_client, field):
    u1 = await _user(api_client, field)
    resp = await api_client.delete(
        f"/v1/intents/{uuid_mod.uuid4()}", headers=u1["headers"]
    )
    assert resp.status_code == 404, resp.text


async def test_7_late_event_after_delete_discarded(api_client, db_engine, field):
    """削除後の遅延Eventはintent_not_foundでprocessed破棄(引用#10)。"""
    u1 = await _user(api_client, field)
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    resp = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert resp.status_code == 204
    await _run_deleted_event(db_engine, i1, 1)  # 削除実行
    # 到着が遅れた created Event(v=1)
    from latch.core.clock import FakeClock
    from latch.events import IncomingEvent

    payload = json.dumps(
        {"event_type": "created", "source_intent_id": i1, "version": 1}
    ).encode()
    event = IncomingEvent.from_payload(
        message_id=f"late-{i1}", payload=payload, ack=lambda: None
    )
    stage1 = Stage1(
        engine=db_engine,
        clock=FakeClock(SystemClock().now()),
        settings=Settings(),
    )
    assert (await stage1.intake(event)).kind == "processed"
    assert (
        await _scalar(
            db_engine,
            "SELECT payload->>'discard_reason' FROM match_events WHERE"
            " event_type = 'created' AND source_intent_id = CAST(:i AS uuid)",
            {"i": i1},
        )
        == "intent_not_found"
    )
