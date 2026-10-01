"""退会API DELETE /v1/users/me のintegration試験(M3 ws-6 design §4.2)。

実DB(compose常設)・api常設(実HTTP)・Redis失効リスト(compose常設)。
退会でusers.auth_subjectが'deleted:<id>'へ置換されるため、teardownは
prefix照会に加えて試験中に登録したuser_idを直接指定して掃く(§9-3)。

注: 計画書test_2(latches詳細participantsの「退会したユーザー」表示)は
現行契約と不成立のためスキップ(報告書に記録)。退会cascadeは参加Intentを
物理削除しLATCHをcancelled化する(FR-19)のに対し、latches詳細APIは
matched/completedのみparticipantsを解決する(design §2.8・latches不変)。
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
SUBJECT_PREFIX = "m3ws6-"


@pytest.fixture
async def field(db_engine):
    """subjectプレフィックス+user_id追跡リスト。teardownは双方で削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    user_ids: list[str] = []
    yield prefix, user_ids
    p = {"p": prefix + "%", "ids": [uuid_mod.UUID(i) for i in user_ids]}
    async with db_engine.begin() as conn:
        # users特定は prefix OR id(退会済み行はauth_subject置換でprefix不一致)
        who = (
            "(SELECT id FROM users WHERE auth_subject LIKE :p"
            " OR id = ANY(CAST(:ids AS uuid[])))"
        )
        await conn.execute(
            text(
                "DELETE FROM messages WHERE latch_id IN (SELECT id FROM latches"
                f" WHERE intent_ids && ARRAY(SELECT id FROM intents"
                f" WHERE user_id IN {who}))"
                " OR sender_id IN " + who
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM"
                f" latches WHERE intent_ids && ARRAY(SELECT id FROM intents"
                f" WHERE user_id IN {who}))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM calibration_records WHERE intent_ids && ARRAY("
                f"SELECT id FROM intents WHERE user_id IN {who})"
            ),
            p,
        )
        await conn.execute(text(f"DELETE FROM notifications WHERE user_id IN {who}"), p)
        await conn.execute(
            text(
                "DELETE FROM latches WHERE intent_ids && ARRAY("
                f"SELECT id FROM intents WHERE user_id IN {who})"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
                f"SELECT id FROM intents WHERE user_id IN {who})"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id IN"
                f"(SELECT id FROM intents WHERE user_id IN {who})"
                " OR intent_b_id IN"
                f"(SELECT id FROM intents WHERE user_id IN {who})"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_events WHERE source_intent_id IN"
                f"(SELECT id FROM intents WHERE user_id IN {who})"
            ),
            p,
        )
        await conn.execute(
            text(
                f"DELETE FROM reports WHERE reporter_id IN {who}"
                f" OR reportee_id IN {who}"
            ),
            p,
        )
        await conn.execute(
            text(
                f"DELETE FROM blocks WHERE blocker_id IN {who} OR blocked_id IN {who}"
            ),
            p,
        )
        await conn.execute(text(f"DELETE FROM intents WHERE user_id IN {who}"), p)
        await conn.execute(
            text(
                "DELETE FROM users WHERE auth_subject LIKE :p"
                " OR id = ANY(CAST(:ids AS uuid[]))"
            ),
            p,
        )


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
    """ユーザー登録 → {"headers", "id", "display_name", "subject"}。

    subjectは退会後の同一IdP再ログイン検証で使う(実装注記1)。"""
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
    return {
        "headers": headers,
        "id": user["id"],
        "display_name": name,
        "subject": subject,
    }


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


async def _candidate(db_engine, a: str, b: str, status: str) -> None:
    """match_candidates行を直接INSERT(test_deletion_api.pyと同型)。"""
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO match_candidates
                    (intent_a_id, intent_b_id, intent_a_version,
                     intent_b_version, status, created_at, updated_at)
                VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 1, 1, :status,
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
            """),
            {
                "a": a if a < b else b,
                "b": b if a < b else a,
                "status": status,
                "now": now,
            },
        )


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


async def _scalar(db_engine, sql: str, params: dict):
    async with db_engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def _user_tracked(api_client, field_tuple, name="退会者"):
    prefix, user_ids = field_tuple
    u = await _user(api_client, prefix, name)
    user_ids.append(u["id"])
    return u


async def test_1_delete_users_me_204_full_cleanup(api_client, db_engine, field):
    """design §4.2: 全Intent消滅・候補消滅・messages(sender分)・notifications
    消滅・blocks/reports残置・users行は置換(残置)。"""
    field_tuple = field
    u1 = await _user_tracked(api_client, field_tuple, "退会する人")
    u2 = await _user_tracked(api_client, field_tuple, "残る人")
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    i2 = (await _intent(api_client, u2["headers"]))["id"]
    await _candidate(db_engine, i1, i2, "evaluated")
    lid = await _latch(db_engine, [i1, i2], status="matched")
    async with db_engine.begin() as conn:  # messages・notifications・blocks・reports
        await conn.execute(
            text(
                "INSERT INTO messages (latch_id, sender_id, body, created_at)"
                " VALUES (CAST(:l AS uuid), CAST(:s AS uuid), 'hello',"
                " CAST(:n AS timestamptz))"
            ),
            {
                "l": uuid_mod.UUID(lid),
                "s": uuid_mod.UUID(u1["id"]),
                "n": SystemClock().now(),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO messages (latch_id, sender_id, body, created_at)"
                " VALUES (CAST(:l AS uuid), CAST(:s AS uuid), 'stay',"
                " CAST(:n AS timestamptz))"
            ),
            {
                "l": uuid_mod.UUID(lid),
                "s": uuid_mod.UUID(u2["id"]),
                "n": SystemClock().now(),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO notifications (user_id, type, payload, created_at)"
                " VALUES (CAST(:u AS uuid), 'latch_proposed', '{}',"
                " CAST(:n AS timestamptz))"
            ),
            {"u": uuid_mod.UUID(u1["id"]), "n": SystemClock().now()},
        )
        await conn.execute(
            text(
                "INSERT INTO blocks (blocker_id, blocked_id, created_at)"
                " VALUES (CAST(:a AS uuid), CAST(:b AS uuid),"
                " CAST(:n AS timestamptz))"
            ),
            {
                "a": uuid_mod.UUID(u1["id"]),
                "b": uuid_mod.UUID(u2["id"]),
                "n": SystemClock().now(),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO reports (reporter_id, reportee_id, reason, status,"
                " created_at) VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 'other',"
                " 'pending', CAST(:n AS timestamptz))"
            ),
            {
                "a": uuid_mod.UUID(u1["id"]),
                "b": uuid_mod.UUID(u2["id"]),
                "n": SystemClock().now(),
            },
        )

    resp = await api_client.delete("/v1/users/me", headers=u1["headers"])
    assert resp.status_code == 204, resp.text

    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM intents WHERE user_id = CAST(:u AS uuid)",
            {"u": u1["id"]},
        )
        == 0
    )  # 全Intent消滅
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
            "SELECT count(*) FROM messages WHERE sender_id = CAST(:u AS uuid)",
            {"u": u1["id"]},
        )
        == 0
    )  # 送信分のみ削除
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM messages WHERE sender_id = CAST(:u AS uuid)",
            {"u": u2["id"]},
        )
        == 1
    )  # 相手分は履歴維持
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM notifications WHERE user_id = CAST(:u AS uuid)",
            {"u": u1["id"]},
        )
        == 0
    )
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM blocks WHERE blocker_id = CAST(:u AS uuid)",
            {"u": u1["id"]},
        )
        == 1
    )  # 残置(design §2.7)
    assert (
        await _scalar(
            db_engine,
            "SELECT count(*) FROM reports WHERE reporter_id = CAST(:u AS uuid)",
            {"u": u1["id"]},
        )
        == 1
    )  # 残置
    row = await _scalar(
        db_engine,
        "SELECT display_name FROM users WHERE id = CAST(:u AS uuid)",
        {"u": u1["id"]},
    )
    assert row == "退会したユーザー"  # users行は残置+置換
    subj = await _scalar(
        db_engine,
        "SELECT auth_subject FROM users WHERE id = CAST(:u AS uuid)",
        {"u": u1["id"]},
    )
    assert subj == f"deleted:{u1['id']}"
    assert (
        await _scalar(
            db_engine,
            "SELECT status FROM latches WHERE id = CAST(:l AS uuid)",
            {"l": lid},
        )
        == "cancelled"
    )  # FR-19(退会でも一致)


async def test_3_token_after_delete_is_new_user(api_client, field):
    """同一IdP subjectでの再ログインは旧Userに紐付かない(新規フロー)。"""
    field_tuple = field
    u1 = await _user_tracked(api_client, field_tuple, "再ログイン")
    # 退会前にIdPトークンを取得(実装注記2: 退会前取得→退会→token交換の順)
    idp = await _cli_idp_token("google", u1["subject"])
    resp = await api_client.delete("/v1/users/me", headers=u1["headers"])
    assert resp.status_code == 204
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    assert tok.json()["user"] == {"id": None, "profile_complete": False}


async def test_4_old_jwt_revoked_401(api_client, field):
    """退会前JWT(同jti)は失効リストにより401(即時反映・引用#13)。"""
    field_tuple = field
    u1 = await _user_tracked(api_client, field_tuple, "失効確認")
    resp = await api_client.delete("/v1/users/me", headers=u1["headers"])
    assert resp.status_code == 204
    me = await api_client.get("/v1/users/me", headers=u1["headers"])
    assert me.status_code == 401, me.text


async def test_5_delete_users_me_unregistered_404(api_client, field):
    """退会済みsubjectでの再発行JWT(未登録)の退会要求は404(get_meと同型)。"""
    field_tuple = field
    u1 = await _user_tracked(api_client, field_tuple, "二回目")
    idp = await _cli_idp_token("google", u1["subject"])
    resp = await api_client.delete("/v1/users/me", headers=u1["headers"])
    assert resp.status_code == 204
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200
    headers2 = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    second = await api_client.delete("/v1/users/me", headers=headers2)
    assert second.status_code == 404, second.text  # 未登録JWTと同型


async def test_6_delete_users_me_requires_auth_401(api_client, field):
    resp = await api_client.delete("/v1/users/me")
    assert resp.status_code == 401, resp.text
