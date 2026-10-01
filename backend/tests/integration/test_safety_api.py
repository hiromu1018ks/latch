"""ブロック・通報のintegration試験(M3 ws-5 design §4.2の9試験)。

実DB(compose常設)・実Redis・api常設(実HTTP)。users/intents/latches行は
fixtureで直接INSERT(ws-4のtest_chat_attendance_api.pyと同型・パイプライン
を走らせない)。ブロック登録・解除・通報は実API(D-23 cancelled化・キャッシュ
DELを含む本体が対象のため)。時間値はnow相対(タイムボム回避)。teardownは
FK順+blocks/reports+Redisキーblk:u:掃除(SUBJECT_PREFIX=m3ws7-)。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120
SUBJECT_PREFIX = "m3ws7-"


@pytest.fixture
async def field(db_engine, redis_client):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除+
    Redisキーblk:u:掃除(users削除前にid一覧を取得)。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT id FROM users WHERE auth_subject LIKE :p"), p
            )
        ).fetchall()
    ids = [str(r[0]) for r in rows]
    if ids:
        await redis_client.delete(*[f"blk:u:{i}" for i in ids])
    async with db_engine.begin() as conn:
        # FK順: reports → messages → latch_status_events →
        # calibration_records → notifications → latches →
        # (match/group/events) → blocks → intents → users
        await conn.execute(
            text(
                "DELETE FROM reports WHERE reporter_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                " OR reportee_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
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
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
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
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
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


async def _user(api_client, prefix: str, name: str = "m3ws5") -> dict:
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


async def _ghost_headers(api_client, prefix: str) -> dict:
    """未登録JWTのヘッダー(トークン発行のみ・/v1/users未登録)。"""
    subject = f"{prefix}{uuid_mod.uuid4().hex[:8]}"
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


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


async def _latch(
    db_engine,
    intent_ids: list,
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


async def _send(api_client, headers, latch_id: str, body: str):
    return await api_client.post(
        f"/v1/latches/{latch_id}/messages", headers=headers, json={"body": body}
    )


def _uuids(values: list) -> list:
    return [uuid_mod.UUID(v) for v in values]


# -- 試験1: 登録→一覧→解除のサイクル(冪等201・display_name・再解除404) --


async def test_1_block_register_list_unblock_cycle(api_client, db_engine, field):
    h1 = await _user(api_client, field, name="登録者")
    h2 = await _user(api_client, field, name="ふたりめ")
    h3 = await _user(api_client, field, name="さんばんめ")
    r1 = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r1.status_code == 201, r1.text
    assert r1.json() == {"blocked_id": h2["id"]}
    r1b = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r1b.status_code == 201  # 冪等201(design §2.4手順4)
    assert r1b.json() == {"blocked_id": h2["id"]}
    r2 = await api_client.post(f"/v1/users/{h3['id']}/block", headers=h1["headers"])
    assert r2.status_code == 201
    # blocks行は相手ごとに1行(存在検査・引用#16)
    async with db_engine.connect() as conn:
        n = (
            await conn.execute(
                text("SELECT COUNT(*) FROM blocks WHERE blocker_id = CAST(:a AS uuid)"),
                {"a": h1["id"]},
            )
        ).scalar_one()
    assert n == 2  # 二重POSTしても増えない
    listed = await api_client.get("/v1/users/me/blocks", headers=h1["headers"])
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {i["blocked_id"] for i in body["items"]} == {h2["id"], h3["id"]}
    assert {i["display_name"] for i in body["items"]} == {"ふたりめ", "さんばんめ"}
    cts = [
        datetime.fromisoformat(i["created_at"].replace("Z", "+00:00"))
        for i in body["items"]
    ]
    assert cts[0] >= cts[1]  # created_at降順(同一時刻はid DESCで決まる)
    assert body["next_cursor"] is None
    d = await api_client.delete(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert d.status_code == 204
    d2 = await api_client.delete(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert d2.status_code == 404  # 再解除は404(冪等204にしない・§2.4)
    assert d2.json()["error"]["code"] == "NOT_FOUND"
    listed2 = await api_client.get("/v1/users/me/blocks", headers=h1["headers"])
    assert {i["blocked_id"] for i in listed2.json()["items"]} == {h3["id"]}


# -- 試験2: 検証エラー(自分422・相手不在404・未登録JWT 404) --


async def test_2_block_validation_errors(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    r = await api_client.post(f"/v1/users/{h1['id']}/block", headers=h1["headers"])
    assert r.status_code == 422  # 自分自身(引用#15 — 専用codeなし)
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    r2 = await api_client.post(
        f"/v1/users/{uuid_mod.uuid4()}/block", headers=h1["headers"]
    )
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "NOT_FOUND"
    ghost = await _ghost_headers(api_client, field)
    r3 = await api_client.post(f"/v1/users/{h1['id']}/block", headers=ghost)
    assert r3.status_code == 404  # 未登録JWT(design §2.4手順1)
    r4 = await api_client.delete(
        f"/v1/users/{uuid_mod.uuid4()}/block", headers=h1["headers"]
    )
    assert r4.status_code == 404
    assert r4.json()["error"]["code"] == "NOT_FOUND"


# -- 試験3: 一覧のcursor改頁 --


async def test_3_block_list_pagination(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    targets = [(await _user(api_client, field))["id"] for _ in range(3)]
    for t in targets:
        r = await api_client.post(f"/v1/users/{t}/block", headers=h1["headers"])
        assert r.status_code == 201
    p1 = await api_client.get(
        "/v1/users/me/blocks", headers=h1["headers"], params={"limit": 2}
    )
    body1 = p1.json()
    assert len(body1["items"]) == 2
    assert isinstance(body1["next_cursor"], str)  # cursorは不透明(引用#8)
    p2 = await api_client.get(
        "/v1/users/me/blocks",
        headers=h1["headers"],
        params={"limit": 2, "cursor": body1["next_cursor"]},
    )
    body2 = p2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None  # 終端
    got = {i["blocked_id"] for i in body1["items"] + body2["items"]}
    assert got == set(targets)  # 重複・欠落なし
    over = await api_client.get(
        "/v1/users/me/blocks", headers=h1["headers"], params={"limit": 101}
    )
    assert over.status_code == 422
    bad = await api_client.get(
        "/v1/users/me/blocks", headers=h1["headers"], params={"cursor": "!!!"}
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"


# -- 試験4: ブロック後のチャット送信409(キャッシュ経由: 温め→DEL→再構築) --


async def test_4_block_cache_chat_409(api_client, db_engine, redis_client, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    # 登録前に一度送信してキャッシュを温める(design §4.2試験2)
    warm = await _send(api_client, h1["headers"], latch, "こんにちは")
    assert warm.status_code == 201, warm.text
    assert await redis_client.exists(f"blk:u:{h1['id']}") == 1
    assert await redis_client.exists(f"blk:u:{h2['id']}") == 1
    # ブロック登録 → コミット後DEL(§2.2)
    r = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r.status_code == 201, r.text
    assert await redis_client.exists(f"blk:u:{h1['id']}") == 0
    assert await redis_client.exists(f"blk:u:{h2['id']}") == 0
    # 再送信は再構築(read-through)でblocks行を見て409
    ra = await _send(api_client, h1["headers"], latch, "x")
    assert ra.status_code == 409
    assert ra.json()["error"]["code"] == "CHAT_READONLY"
    rb = await _send(api_client, h2["headers"], latch, "x")  # 双方向(引用#1)
    assert rb.status_code == 409
    assert rb.json()["error"]["code"] == "CHAT_READONLY"


# -- 試験5: 解除後は現物参照で送信可に戻る(supervisor承認③) --


async def test_5_unblock_restores_chat(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    r = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r.status_code == 201, r.text
    assert (await _send(api_client, h1["headers"], latch, "x")).status_code == 409
    d = await api_client.delete(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert d.status_code == 204
    after = await _send(api_client, h1["headers"], latch, "解除後もよろしく")
    assert after.status_code == 201, after.text  # §2.4 — 遡及なし・未来は従う


# -- 試験6: D-23本体(candidate/proposed/partial_accept閉鎖・matched維持) --


async def test_6_d23_cancels_open_latches(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    made = []  # (status, latch_id, u1側intent_id)
    for status in ("candidate", "proposed", "partial_accept", "matched"):
        ia = await _intent(api_client, h1["headers"])
        ib = await _intent(api_client, h2["headers"])
        latch = await _latch(db_engine, [ia["id"], ib["id"]], status=status)
        made.append((status, latch, ia["id"]))
    r = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r.status_code == 201, r.text
    latch_ids = [lid for _, lid, _ in made]
    intent_ids = [i for _, _, i in made]
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT status FROM latches"
                    " WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
                ),
                {"ids": _uuids(latch_ids)},
            )
        ).fetchall()
        statuses = {row[0] for row in rows}
        # candidate/proposed/partial_acceptはcancelled・matchedは維持(引用#4)
        assert statuses == {"cancelled", "matched"}
        assert [row[0] for row in rows].count("cancelled") == 3
        ev = (
            await conn.execute(
                text("""
                    SELECT from_status, to_status, user_id
                    FROM latch_status_events
                    WHERE latch_id = ANY(CAST(:ids AS uuid[]))
                      AND to_status = 'cancelled'
                """),
                {"ids": _uuids(latch_ids)},
            )
        ).fetchall()
        assert sorted(e[0] for e in ev) == [
            "candidate",
            "partial_accept",
            "proposed",
        ]
        assert all(e[2] == uuid_mod.UUID(h1["id"]) for e in ev)  # user_id=blocker
        st = (
            await conn.execute(
                text(
                    "SELECT DISTINCT status FROM intents"
                    " WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": _uuids(intent_ids)},
            )
        ).fetchall()
        assert st == [("active",)]  # 参加Intentはactiveのまま(引用#11)
        nn = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM notifications"
                    " WHERE user_id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                ),
                {"a": h1["id"], "b": h2["id"]},
            )
        ).scalar_one()
        assert nn == 0  # 通知なし(引用#13)
    matched_latch = [lid for s, lid, _ in made if s == "matched"][0]
    rr = await _send(api_client, h1["headers"], matched_latch, "x")
    assert rr.status_code == 409  # matchedは送信だけ409(読取専用化)
    assert rr.json()["error"]["code"] == "CHAT_READONLY"


# -- 試験7: グループLATCH(第三メンバーを含む提案のcancelled) --


async def test_7_d23_group_latch(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    i3 = await _intent(api_client, h3["headers"])
    # u1・u2・u3の3人latch(u1とu2が共に参加)
    latch_g = await _latch(db_engine, [i1["id"], i2["id"], i3["id"]], status="proposed")
    # u2・u3のみのlatch(u1不参加 — ブロック者を含まない提案)
    i2b = await _intent(api_client, h2["headers"])
    i3b = await _intent(api_client, h3["headers"])
    latch_other = await _latch(db_engine, [i2b["id"], i3b["id"]], status="proposed")
    r = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r.status_code == 201, r.text
    async with db_engine.connect() as conn:
        sg = (
            await conn.execute(
                text("SELECT status FROM latches WHERE id = CAST(:l AS uuid)"),
                {"l": latch_g},
            )
        ).scalar_one()
        so = (
            await conn.execute(
                text("SELECT status FROM latches WHERE id = CAST(:l AS uuid)"),
                {"l": latch_other},
            )
        ).scalar_one()
    assert sg == "cancelled"  # 第三メンバー(u3)を含んでも閉じる(§2.3)
    assert so == "proposed"  # u1を含まない提案は閉じない


# -- 試験8: reportsの受付サイクル --


async def test_8_reports_cycle(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    # 受付201(latch_idつき・引用#2)
    r = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={
            "reportee_id": h2["id"],
            "latch_id": latch,
            "reason": "inappropriate_content",
        },
    )
    assert r.status_code == 201, r.text
    report_id = r.json()["report_id"]
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT reason, status, latch_id FROM reports"
                    " WHERE id = CAST(:r AS uuid)"
                ),
                {"r": report_id},
            )
        ).first()
    assert row[0] == "inappropriate_content"
    assert row[1] == "pending"  # 固定投入(§2.5)
    assert str(row[2]) == latch
    # latch_idなし受付(引用#6)
    r2 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"reportee_id": h2["id"], "reason": "other"},
    )
    assert r2.status_code == 201
    # 自分自身は422
    r3 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"reportee_id": h1["id"], "reason": "other"},
    )
    assert r3.status_code == 422
    assert r3.json()["error"]["code"] == "VALIDATION_ERROR"
    # reason値域外は422(4値コード・§2.5)
    r4 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"reportee_id": h2["id"], "reason": "unknown"},
    )
    assert r4.status_code == 422
    # reportee不在は404
    r5 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"reportee_id": str(uuid_mod.uuid4()), "reason": "other"},
    )
    assert r5.status_code == 404
    assert r5.json()["error"]["code"] == "NOT_FOUND"
    # 未登録JWTは404
    ghost = await _ghost_headers(api_client, field)
    r6 = await api_client.post(
        "/v1/reports",
        headers=ghost,
        json={"reportee_id": h2["id"], "reason": "other"},
    )
    assert r6.status_code == 404


# -- 試験9: reportsのlatch_id参加者検査 --


async def test_9_reports_latch_participation(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    i3 = await _intent(api_client, h3["headers"])
    # h1+h2のlatch(正常系)とh1+h3のlatch(reportee=h2が非参加)
    latch_ok = await _latch(db_engine, [i1["id"], i2["id"]])
    latch_no_reportee = await _latch(db_engine, [i1["id"], i3["id"]])
    # latch不在は404
    r0 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={
            "reportee_id": h2["id"],
            "latch_id": str(uuid_mod.uuid4()),
            "reason": "other",
        },
    )
    assert r0.status_code == 404
    # reporteeが非参加のlatch → 422(§2.5)
    r1 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={
            "reportee_id": h2["id"],
            "latch_id": latch_no_reportee,
            "reason": "other",
        },
    )
    assert r1.status_code == 422
    assert r1.json()["error"]["code"] == "VALIDATION_ERROR"
    # 自分が非参加のlatch(h2+h3)へ h1 が通報 → 422
    latch_no_me = await _latch(db_engine, [i2["id"], i3["id"]])
    r2 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={
            "reportee_id": h2["id"],
            "latch_id": latch_no_me,
            "reason": "other",
        },
    )
    assert r2.status_code == 422
    # 正常系: 自分+reporteeとも参加 → 201
    r3 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={
            "reportee_id": h2["id"],
            "latch_id": latch_ok,
            "reason": "suspected_impersonation",
        },
    )
    assert r3.status_code == 201, r3.text


# -- 試験10: 案X reportee_id省略+解決(ws-7 design §6-5) --


async def test_10_reports_reportee_resolution(api_client, db_engine, field):
    """案X: 省略+1対1latchで相手に解決201・グループ422・両方省略422・
    非参加422・latch不在404・明示経路無傷(ws-7 design §2.7)。"""
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    i3 = await _intent(api_client, h3["headers"])
    pair = await _latch(db_engine, [i1["id"], i2["id"]])
    # グループlatchのgroup_candidate_idはgroup_candidates行の実idでなければ
    # FK違反になる(supervisor修正: 元は存在しないランダムuuidを渡していた)
    async with db_engine.begin() as conn:
        gid_row = await conn.execute(
            text("""
                INSERT INTO group_candidates
                    (intent_ids, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), 'candidate',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": sorted(
                    [uuid_mod.UUID(i) for i in (i1["id"], i2["id"], i3["id"])],
                    key=str,
                ),
                "now": SystemClock().now(),
            },
        )
    gid = str(gid_row.first()[0])
    group = await _latch(db_engine, [i1["id"], i2["id"], i3["id"]], gid=gid)
    # 省略+1対1 → 201・reportee_idはh2へ解決される
    r1 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"latch_id": pair, "reason": "other"},
    )
    assert r1.status_code == 201, r1.text
    async with db_engine.connect() as conn:
        reportee = (
            await conn.execute(
                text("SELECT reportee_id FROM reports WHERE id = CAST(:r AS uuid)"),
                {"r": r1.json()["report_id"]},
            )
        ).scalar_one()
    assert str(reportee) == h2["id"]  # 通報者(h1)以外の1人
    # 省略+グループlatch → 422(対象特定不能)
    r2 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"latch_id": group, "reason": "other"},
    )
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "VALIDATION_ERROR"
    # 両方省略 → 422
    r3 = await api_client.post(
        "/v1/reports", headers=h1["headers"], json={"reason": "other"}
    )
    assert r3.status_code == 422
    # 省略+自分非参加のlatch → 422
    other = await _latch(db_engine, [i2["id"], i3["id"]])
    r4 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"latch_id": other, "reason": "other"},
    )
    assert r4.status_code == 422
    # 省略+latch不在 → 404
    r5 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"latch_id": str(uuid_mod.uuid4()), "reason": "other"},
    )
    assert r5.status_code == 404
    # 明示経路(reportee_id指定)は現行どおり201
    r6 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"reportee_id": h2["id"], "latch_id": pair, "reason": "other"},
    )
    assert r6.status_code == 201, r6.text
