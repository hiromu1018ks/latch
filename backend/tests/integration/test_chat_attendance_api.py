"""チャット+実施自己申告のintegration試験(M3 ws-4 design §4.2の17試験)。

実DB(compose常設)・実Redis・api常設(実HTTP)。users/intents/latches行は
fixtureで直接INSERT(パイプラインを走らせない・ws-1のtest_latches_api.py
と同型)。completed化・3日経過はDB値の直接UPDATE(api常設のClockは差し替え
不能なための等価置換・design §2.4)。時間値はすべてnow相対(タイムボム回避)。
teardownはFK順+blocks掃除(SUBJECT_PREFIX=m3ws4-)。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120
SUBJECT_PREFIX = "m3ws4-"


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        # FK順: messages → latch_status_events → calibration_records →
        # notifications → latches → (match/group/events) → blocks → intents → users
        await conn.execute(
            text(
                "DELETE FROM messages WHERE latch_id IN"
                " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
            ),
            p,
        )
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
                "DELETE FROM calibration_records WHERE latch_id IN"
                " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
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
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_events WHERE source_intent_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM blocks WHERE blocker_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                " OR blocked_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
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


async def _user(api_client, prefix: str):
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
        json={"display_name": "m3ws4", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured(*, secondary: str | None = None, start: str | None = None) -> dict:
    return {
        "category": {"primary": CATEGORY, "secondary": secondary},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }


async def _intent(
    api_client, headers, *, secondary: str | None = None, start: str | None = None
) -> dict:
    resp = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={
            "raw_text": "分類用テキスト",
            "status": "active",
            "structured_intent": _structured(secondary=secondary, start=start),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]


async def _latch(
    db_engine,
    intent_ids: list[str],
    *,
    status: str = "matched",
    score: float = 0.85,
    gid: str | None = None,
    proposal: dict | None = None,
) -> str:
    """latches行を直接INSERT(fixture・design §4.2)。既定はmatched。"""
    now = SystemClock().now()
    if proposal is None:
        proposal = {"headcount": len(intent_ids), "match_level": "medium"}
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
                "proposal": json.dumps(proposal, ensure_ascii=False),
                "score": score,
                "status": status,
                "deadline": now + timedelta(hours=2.0),
                "expires": now + timedelta(hours=120.0),
                "now": now,
            },
        )
    return str(res.first()[0])


async def _complete(db_engine, latch_id: str, *, hours_ago: float = 0.0) -> None:
    """matched→completed相当へDB直接UPDATE(completed_at=now相対・§2.4等価置換)。"""
    completed_at = SystemClock().now() - timedelta(hours=hours_ago)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET status = 'completed',"
                " completed_at = CAST(:c AS timestamptz)"
                " WHERE id = CAST(:l AS uuid)"
            ),
            {"c": completed_at, "l": latch_id},
        )


async def _calibration_row(db_engine, latch_id: str, intent_ids: list[str]) -> None:
    """calibration_records行を直接INSERT(actual_attended=NULL・design §4.2)。

    created_at/updated_atはnow-1h(attendance後のupdated_at進行検証用)。
    """
    now = SystemClock().now() - timedelta(hours=1)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO calibration_records
                    (latch_id, intent_ids, prediction, proposal_snapshot,
                     actual_responses, matched, created_at, updated_at)
                VALUES (CAST(:l AS uuid), CAST(:ids AS uuid[]),
                        CAST(:pred AS jsonb), CAST(:prop AS jsonb),
                        CAST(:resp AS jsonb), true,
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
            """),
            {
                "l": latch_id,
                "ids": [uuid_mod.UUID(i) for i in intent_ids],
                "pred": json.dumps({"placeholder": True}),
                "prop": json.dumps({"headcount": len(intent_ids)}),
                "resp": json.dumps([]),
                "now": now,
            },
        )


async def _block(
    db_engine, blocker_intent: str, blocked_intent: str, redis_client=None
) -> None:
    """blocks行を直接INSERT(ws-5適用後の整合: blk:u:手動DELつき)。

    API経由でない行追加はキャッシュ更新経由を通らないため、該当2
    ユーザーのキーを手動DELして送信判定へ反映させる(design §2.2整合・
    ws-5計画§4のスコープ外最小変更)。
    """
    async with db_engine.begin() as conn:
        a = (
            await conn.execute(
                text("SELECT user_id FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": blocker_intent},
            )
        ).scalar_one()
        b = (
            await conn.execute(
                text("SELECT user_id FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": blocked_intent},
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO blocks (blocker_id, blocked_id, created_at)"
                " VALUES (CAST(:a AS uuid), CAST(:b AS uuid),"
                " CAST(:now AS timestamptz))"
            ),
            {"a": a, "b": b, "now": SystemClock().now()},
        )
    if redis_client is not None:
        await redis_client.delete(f"blk:u:{a}", f"blk:u:{b}")


async def _pair_eval(db_engine, a_id: str, b_id: str, *, wa: float, wb: float) -> None:
    """match_candidatesへjev_result付きevaluatedを直接INSERT(試験17用)。"""
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
    now = SystemClock().now()
    lo_id, hi_id = sorted((a_id, b_id))
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO match_candidates
                    (intent_a_id, intent_b_id, intent_a_version,
                     intent_b_version, retrieval_score, cheap_judge_score,
                     jev_result, latch_score, status, created_at, updated_at)
                VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 1, 1,
                        0.9, 0.9, CAST(:jev AS jsonb), 0.85, 'evaluated',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                ON CONFLICT (intent_a_id, intent_b_id, intent_a_version,
                             intent_b_version) DO UPDATE
                SET jev_result = EXCLUDED.jev_result,
                    latch_score = EXCLUDED.latch_score,
                    status = 'evaluated', updated_at = EXCLUDED.updated_at
            """),
            {
                "a": uuid_mod.UUID(lo_id),
                "b": uuid_mod.UUID(hi_id),
                "jev": json.dumps(jev, ensure_ascii=False),
                "now": now,
            },
        )


async def _send(api_client, headers, latch_id: str, body: str):
    return await api_client.post(
        f"/v1/latches/{latch_id}/messages", headers=headers, json={"body": body}
    )


async def _msgs(api_client, headers, latch_id: str, **params):
    return await api_client.get(
        f"/v1/latches/{latch_id}/messages", headers=headers, params=params or None
    )


async def _attendance(api_client, headers, latch_id: str, attended: bool):
    return await api_client.post(
        f"/v1/latches/{latch_id}/attendance",
        headers=headers,
        json={"attended": attended},
    )


async def _cal(db_engine, latch_id: str) -> dict | None:
    async with db_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT actual_attended, cancelled_after, created_at,"
                        " updated_at FROM calibration_records"
                        " WHERE latch_id = CAST(:l AS uuid)"
                    ),
                    {"l": latch_id},
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


# -- 試験1: matchedで双方向送信・GET一覧 --


async def test_1_pair_chat_roundtrip(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    r1 = await _send(api_client, h1, latch, "はじめまして、よろしくお願いします")
    assert r1.status_code == 201, r1.text  # 承認事項⑦: POSTは201
    m1 = r1.json()["message"]
    assert set(m1) == {"id", "latch_id", "sender_id", "body", "created_at"}
    assert m1["latch_id"] == latch
    assert m1["body"] == "はじめまして、よろしくお願いします"
    r2 = await _send(api_client, h2, latch, "こちらこそ!")
    assert r2.status_code == 201
    listed = await _msgs(api_client, h1, latch)
    assert listed.status_code == 200
    body = listed.json()
    assert [m["body"] for m in body["items"]] == [
        "はじめまして、よろしくお願いします",
        "こちらこそ!",
    ]  # created_at昇順(引用#4)
    assert body["next_cursor"] is None


# -- 試験2: GET改頁 --


async def test_2_messages_pagination(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)  # 同一ユーザー2Intent(1対1作成用)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _send(api_client, h1, latch, "1通目")
    await _send(api_client, h1, latch, "2通目")
    p1 = await _msgs(api_client, h1, latch, limit=1)
    body1 = p1.json()
    assert len(body1["items"]) == 1
    assert body1["items"][0]["body"] == "1通目"
    assert isinstance(body1["next_cursor"], str)  # cursorは不透明(引用#4)
    p2 = await _msgs(api_client, h1, latch, limit=1, cursor=body1["next_cursor"])
    body2 = p2.json()
    assert body2["items"][0]["body"] == "2通目"
    assert body2["next_cursor"] is None  # 終端
    # limit超過は422(共通規定)
    over = await _msgs(api_client, h1, latch, limit=101)
    assert over.status_code == 422


# -- 試験3: matched以前への送信 --


async def test_3_send_before_match_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch_p = await _latch(db_engine, [i1["id"], i2["id"]], status="proposed")
    r = await _send(api_client, h1, latch_p, "x")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CHAT_READONLY"
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 12))
    i4 = await _intent(api_client, h1, start=_future(BASE_HOURS + 12))
    latch_pa = await _latch(db_engine, [i3["id"], i4["id"]], status="partial_accept")
    r2 = await _send(api_client, h1, latch_pa, "x")
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "CHAT_READONLY"


# -- 試験4: completed後の送信 --


async def test_4_send_after_completed_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    r = await _send(api_client, h1, latch, "x")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CHAT_READONLY"  # 引用#5


# -- 試験5: cancelled後の送信とGET --


async def test_5_send_after_cancelled_409_get_ok(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _send(api_client, h1, latch, "残る1通")
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE latches SET status = 'cancelled' WHERE id = CAST(:l AS uuid)"),
            {"l": latch},
        )
    r = await _send(api_client, h1, latch, "x")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CHAT_READONLY"
    listed = await _msgs(api_client, h1, latch)  # 閲覧は継続(引用#6)
    assert listed.status_code == 200
    assert [m["body"] for m in listed.json()["items"]] == ["残る1通"]


# -- 試験6: blocks双方向 --


async def test_6_block_both_directions_409(api_client, db_engine, redis_client, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _send(api_client, h1, latch, "ブロック前")
    await _block(db_engine, i1["id"], i2["id"], redis_client)  # (A,B)のみ挿入
    ra = await _send(api_client, h1, latch, "x")
    assert ra.status_code == 409
    assert ra.json()["error"]["code"] == "CHAT_READONLY"
    rb = await _send(api_client, h2, latch, "x")  # (B,A)行は無い(引用#7)
    assert rb.status_code == 409
    assert rb.json()["error"]["code"] == "CHAT_READONLY"
    listed = await _msgs(api_client, h1, latch)  # 閲覧は可(引用#1)
    assert listed.status_code == 200
    assert [m["body"] for m in listed.json()["items"]] == ["ブロック前"]


# -- 試験7: 参加者以外 --


async def test_7_outsider_messages_403(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    outsider = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    r = await _send(api_client, outsider, latch, "x")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    listed = await _msgs(api_client, outsider, latch)
    assert listed.status_code == 403
    assert listed.json()["error"]["code"] == "FORBIDDEN"


# -- 試験8: 不在latch --


async def test_8_not_found(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    missing = str(uuid_mod.uuid4())
    r = await _send(api_client, h1, missing, "x")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    listed = await _msgs(api_client, h1, missing)
    assert listed.status_code == 404
    r3 = await _attendance(api_client, h1, missing, True)
    assert r3.status_code == 404


# -- 試験9: body検証 --


async def test_9_body_validation_422(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    for bad in ("", "   ", "\n\t", "あ" * 1001):
        r = await _send(api_client, h1, latch, bad)
        assert r.status_code == 422, bad
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    ok = await _send(api_client, h1, latch, " こんにちは ")
    assert ok.status_code == 201  # 前後空白入りもtrim後1字以上で受理(design §2.1)


# -- 試験10: attendance true --


async def test_10_attendance_true(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    await _calibration_row(db_engine, latch, [i1["id"], i2["id"]])
    r = await _attendance(api_client, h1, latch, True)
    assert r.status_code == 200, r.text
    assert r.json() == {"latch_id": latch, "actual_attended": True}  # 引用#9
    cal = await _cal(db_engine, latch)
    assert cal["actual_attended"] is True
    assert cal["cancelled_after"] is False  # 排他(引用#12・#13)
    assert cal["updated_at"] > cal["created_at"]  # 収集時更新で進む(引用#12)


# -- 試験11: attendance false --


async def test_11_attendance_false(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    await _calibration_row(db_engine, latch, [i1["id"], i2["id"]])
    r = await _attendance(api_client, h1, latch, False)
    assert r.status_code == 200
    assert r.json() == {"latch_id": latch, "actual_attended": False}
    cal = await _cal(db_engine, latch)
    assert cal["actual_attended"] is False
    assert cal["cancelled_after"] is True  # 引用#10・#13の対応


# -- 試験12: 先着確定(1対1の2人目・グループ3人の2人目) --


async def test_12_first_answer_wins(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    await _calibration_row(db_engine, latch, [i1["id"], i2["id"]])
    r1 = await _attendance(api_client, h1, latch, True)
    assert r1.status_code == 200
    r2 = await _attendance(api_client, h2, latch, False)  # 相手は上書けない
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "ATTENDANCE_ALREADY_SUBMITTED"
    cal = await _cal(db_engine, latch)
    assert cal["actual_attended"] is True  # 先着のまま(訂正不可)
    # グループ3人: 2人目も同じ
    hs = [await _user(api_client, field) for _ in range(3)]
    intents = [await _intent(api_client, api_h) for api_h in hs]
    ids = [x["id"] for x in intents]
    latch_g = await _latch(db_engine, ids)
    await _complete(db_engine, latch_g, hours_ago=1.0)
    await _calibration_row(db_engine, latch_g, ids)
    assert (await _attendance(api_client, hs[0], latch_g, False)).status_code == 200
    r3 = await _attendance(api_client, hs[1], latch_g, True)
    assert r3.status_code == 409
    assert r3.json()["error"]["code"] == "ATTENDANCE_ALREADY_SUBMITTED"


# -- 試験13: 3日窓(超過と境界) --


async def test_13_attendance_window(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=73.0)  # 3日+1時間: 閉じ
    await _calibration_row(db_engine, latch, [i1["id"], i2["id"]])
    r = await _attendance(api_client, h1, latch, True)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ATTENDANCE_WINDOW_CLOSED"
    # 境界(ちょうど3日)は受理: completed_atを now+10秒-72h へ置き、API実行時点
    # まで窓内を保つ等価置換(§2.4・api常設Clockは差し替え不能)
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 24))
    i4 = await _intent(api_client, h1, start=_future(BASE_HOURS + 24))
    latch2 = await _latch(db_engine, [i3["id"], i4["id"]])
    await _calibration_row(db_engine, latch2, [i3["id"], i4["id"]])
    edge = SystemClock().now() - timedelta(hours=72) + timedelta(seconds=10)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET status = 'completed',"
                " completed_at = CAST(:c AS timestamptz)"
                " WHERE id = CAST(:l AS uuid)"
            ),
            {"c": edge, "l": latch2},
        )
    r2 = await _attendance(api_client, h1, latch2, True)
    assert r2.status_code == 200, r2.text


# -- 試験14: 未completedへのattendance --


async def test_14_attendance_not_completed_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch_m = await _latch(db_engine, [i1["id"], i2["id"]])  # matchedのまま
    r = await _attendance(api_client, h1, latch_m, True)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "LATCH_CLOSED"
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 36))
    i4 = await _intent(api_client, h2, start=_future(BASE_HOURS + 36))
    latch_c = await _latch(db_engine, [i3["id"], i4["id"]], status="cancelled")
    r2 = await _attendance(api_client, h1, latch_c, True)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "LATCH_CLOSED"


# -- 試験15: 参加者以外のattendance(存在秘匿404) --


async def test_15_attendance_outsider_404(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    outsider = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    await _calibration_row(db_engine, latch, [i1["id"], i2["id"]])
    r = await _attendance(api_client, outsider, latch, True)
    assert r.status_code == 404  # messagesの403と扱いを分ける(引用#9)
    assert r.json()["error"]["code"] == "NOT_FOUND"


# -- 試験16: calibration行不在 --


async def test_16_calibration_missing_503(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    # calibration_records行は作らない(ws-1の作成スキップと同根・承認事項⑤)
    r = await _attendance(api_client, h1, latch, True)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


# -- 試験17: E2E(02#19の本体): matched→双方向送信→completed→attendance --


async def test_17_chat_to_attendance_e2e(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    await _pair_eval(db_engine, i1["id"], i2["id"], wa=0.9, wb=0.9)
    latch = await _latch(db_engine, [i1["id"], i2["id"]], status="proposed")
    # 回答APIの実流れでmatched化+Calibration作成(ws-1実装)
    for h in (h1, h2):
        resp = await api_client.post(
            f"/v1/latches/{latch}/response", headers=h, json={"response": "yes"}
        )
        assert resp.status_code == 200, resp.text
    assert resp.json()["latch"]["status"] == "matched"
    # 双方向の送受信記録(引用#20)
    assert (
        await _send(api_client, h1, latch, "当日はよろしくお願いします")
    ).status_code == 201
    assert (await _send(api_client, h2, latch, "こちらこそ!")).status_code == 201
    listed = await _msgs(api_client, h2, latch)
    assert len(listed.json()["items"]) == 2
    # 対象時刻経過→completed(sweeper相当をDB値で・§2.4)
    await _complete(db_engine, latch, hours_ago=0.5)
    assert (await _send(api_client, h1, latch, "x")).status_code == 409  # 送信关闭
    # 申告→respond経由で作られた実Calibration行へ反映
    r = await _attendance(api_client, h1, latch, True)
    assert r.status_code == 200, r.text
    cal = await _cal(db_engine, latch)
    assert cal["actual_attended"] is True
    assert cal["cancelled_after"] is False
