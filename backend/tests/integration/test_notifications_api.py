"""通知の媒体確定のintegration試験(M3 ws-3 design §4.2・02#13下地)。

実DB(compose常設)・実Redis・api常設(実HTTP・一覧/既読/認証)。プッシュは
StubPushSenderを注入したLatchEngine/ExpirySweeperをテストプロセス内で直接
構成しcaplogで記録を検証する(Workerプロセスは立てない —
test_matching_latchengineと同型)。sweeper試験(試験6)はFakeClockで
run_onceを1回呼ぶ(test_expiry_batchesと同型)。
"""

import asyncio
import json
import logging
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.geo.service import GeoService
from latch.notifications.records import LOGGER_NAME
from latch.notifications.sender import StubPushSender
from latch.notifications.templates import PUSH_BODY_LATCH, PUSH_BODY_NOTICE
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.matching.runner import run_candidate_retrieval
from latch.worker.sweeper import ExpirySweeper

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120  # now+5日系(ws-1/ws-2と同一の対抗策・時間窓分離)
FAR_EXPIRES_H = 144
SUBJECT_PREFIX = "m3ws3-"


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        for sql in (
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
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM match_candidates WHERE intent_a_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            " OR intent_b_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM match_events WHERE source_intent_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM users WHERE auth_subject LIKE :p",
        ):
            await conn.execute(text(sql), p)


# -- ヘルパー(test_matching_latchengine.pyと同一形式・自前定義) --


async def _cli_idp_token(provider: str, subject: str) -> str:
    """テストIdPでJWT発行(認証サービスのci構成・各integrationファイルが
    自前定義する流儀 — tests配下に__init__.pyがないためimport不可)。"""
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


async def _user(api_client, prefix: str, birth_date: str = "1990-04-01"):
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
        json={"display_name": "m3ws3", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured(
    *,
    start: str | None = None,
    expires: str | None = None,
    visibility: str | None = None,
    notification_level: str | None = None,
    secondary: str | None = None,
    alcohol: bool = False,
    category: str | None = None,
) -> dict:
    d = {
        "category": {"primary": category or CATEGORY, "secondary": secondary},
        "alcohol_involved": alcohol,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }
    if expires is not None:
        d["expires_at"] = expires
    if visibility is not None:
        d["visibility"] = visibility
    if notification_level is not None:
        d["notification_level"] = notification_level
    return d


async def _intent(api_client, db_engine, headers, structured) -> dict:
    """active Intentを作りembeddingを直接挿入(ws流儀のfixture方針)。"""
    resp = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={
            "raw_text": "分類用テキスト",
            "status": "active",
            "structured_intent": structured,
        },
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:v AS vector)"
                " WHERE id = CAST(:id AS uuid)"
            ),
            {
                "v": "[" + ",".join(["0.1"] * 768) + "]",
                "id": intent["id"],
            },
        )
    return intent


async def _seed_jev(db_engine, a_id: str, b_id: str, wa: float, wb: float) -> None:
    """match_candidatesへjev_result付きevaluatedを直接投入。"""
    jev = json.dumps(
        {
            "would_a_accept_b": wa,
            "would_b_accept_a": wb,
            "provider": "fixture",
            "model": None,
        }
    )
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE match_candidates SET jev_result = CAST(:jev AS jsonb),"
                " status = 'evaluated'"
                " WHERE status = 'pending'"
                " AND ((intent_a_id = CAST(:a AS uuid)"
                " AND intent_b_id = CAST(:b AS uuid))"
                " OR (intent_a_id = CAST(:b AS uuid)"
                " AND intent_b_id = CAST(:a AS uuid)))"
            ),
            {"jev": jev, "a": a_id, "b": b_id},
        )
        assert res.rowcount == 1, "jev直接投入対象行なし"


def _latch_engine(db_engine, clock, push=None) -> LatchEngine:
    return LatchEngine(
        engine=db_engine, clock=clock, geo=GeoService(db_engine), push=push
    )


async def _handle(db_engine, clock, intent_id: str, push=None) -> None:
    await _latch_engine(db_engine, clock, push).handle(uuid_mod.UUID(intent_id))


def _push_records(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME]


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


async def _user_id_of(db_engine, intent_id: str) -> str:
    async with db_engine.connect() as conn:
        return str(
            (
                await conn.execute(
                    text("SELECT user_id FROM intents WHERE id = CAST(:id AS uuid)"),
                    {"id": intent_id},
                )
            ).scalar_one()
        )


async def _notifications_rows(db_engine, user_id: str) -> list[tuple]:
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, type, read_at, created_at FROM notifications"
                    " WHERE user_id = CAST(:u AS uuid)"
                    " ORDER BY created_at DESC, id DESC"
                ),
                {"u": user_id},
            )
        ).fetchall()
    return rows


# -- §4.2 試験1〜8(12件) --


async def test_1_e2e_push_sent_and_listed(api_client, db_engine, field, caplog):
    """E2E送信(02#13下地): proposed遷移→tx後ok記録2件(汎用文フル文字列)→
    GET /v1/notificationsで本人にお知らせ1件(latch.status=proposed)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.85)
    sender = StubPushSender(clock=clock)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _handle(db_engine, clock, a["id"], push=sender)
    recs = _push_records(caplog)
    uid_a = await _user_id_of(db_engine, a["id"])
    uid_b = await _user_id_of(db_engine, b["id"])
    assert len(recs) == 2
    assert {r["user_id"] for r in recs} == {uid_a, uid_b}
    for r in recs:
        assert r["status"] == "ok"
        assert r["title"] == "LATCH"
        assert r["body"] == PUSH_BODY_LATCH  # 汎用文フル文字列
        assert r["notification_type"] == "proposal"
        assert r["latch_id"]
    # お知らせ一覧(本人のみ・latch埋め込み)
    resp = await api_client.get("/v1/notifications", headers=ha)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) >= 1
    mine = [i for i in body["items"] if i["type"] == "proposal"]
    assert len(mine) == 1
    assert mine[0]["latch_id"] in {
        recs[0]["latch_id"],
        recs[1]["latch_id"],
    }
    assert mine[0]["latch"]["status"] == "proposed"
    assert mine[0]["latch"]["proposal"]["headcount"] == 2


async def test_2_push_body_identical_across_visibility_and_category(
    api_client, db_engine, field, caplog
):
    """FR-21(引用#1・#11): summary_only×2・hidden混在・drinkingの3ケースで
    プッシュ本文がbyte同一(文言分岐なしの証明)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    cases = [
        # (a側structured, b側structured)
        (
            _structured(start=start, expires=expires, visibility="summary_only"),
            _structured(start=start, expires=expires, visibility="summary_only"),
        ),
        (
            _structured(start=start, expires=expires, visibility="hidden_until_match"),
            _structured(start=start, expires=expires, visibility="summary_only"),
        ),
        # drinking(カテゴリ推察防止の様式同一・引用#1)
        (
            _structured(
                start=start, expires=expires, alcohol=True, category="drinking"
            ),
            _structured(
                start=start, expires=expires, alcohol=True, category="drinking"
            ),
        ),
    ]
    for sa, sb in cases:
        ha = await _user(api_client, field)
        a = await _intent(api_client, db_engine, ha, sa)
        hb = await _user(api_client, field)
        b = await _intent(api_client, db_engine, hb, sb)
        async with db_engine.begin() as conn:
            await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
        await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            await _handle(db_engine, clock, a["id"], push=StubPushSender(clock=clock))
    recs = [r for r in _push_records(caplog) if r["status"] == "ok"]
    assert len(recs) == 6  # 3ケース×2名
    assert {r["body"] for r in recs} == {PUSH_BODY_LATCH}  # byte同一(集合1要素)
    assert {r["title"] for r in recs} == {"LATCH"}


async def test_3_muted_participant_no_push(api_client, db_engine, field, caplog):
    """#13 muted下地(引用#6): 片方muted→push記録1件のみ・notifications行は
    muted側0行・latches.status=proposed(遷移は通常どおり)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client,
        db_engine,
        hb,
        _structured(start=start, expires=expires, notification_level="muted"),
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.85)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _handle(db_engine, clock, a["id"], push=StubPushSender(clock=clock))
    recs = _push_records(caplog)
    uid_a = await _user_id_of(db_engine, a["id"])
    uid_b = await _user_id_of(db_engine, b["id"])
    assert len(recs) == 1
    assert recs[0]["user_id"] == uid_a  # muted側(b)には出ない
    # DB: a側1行・b側0行・latchはproposed
    assert len(await _notifications_rows(db_engine, uid_a)) == 1
    assert len(await _notifications_rows(db_engine, uid_b)) == 0
    async with db_engine.connect() as conn:
        status = (
            await conn.execute(
                text(
                    "SELECT status FROM latches"
                    " WHERE intent_ids && ARRAY[CAST(:a AS uuid),"
                    " CAST(:b AS uuid)]"
                ),
                {"a": a["id"], "b": b["id"]},
            )
        ).scalar_one()
    assert status == "proposed"


async def test_4a_list_desc_order_with_id_tiebreak(api_client, db_engine, field):
    """一覧: created_at降順・同一時刻行はid降順タイブレーク(Review Focus 5)。

    FakeClock固定なので2回のhandleが同一created_atになる → タイブレーク検証。
    """
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    # latchを2つ作る(相手を変える・同一起点ユーザー)
    partners = []
    for _ in range(2):
        hb = await _user(api_client, field)
        partners.append(
            await _intent(
                api_client,
                db_engine,
                hb,
                _structured(start=start, expires=expires),
            )
        )
    for b in partners:
        async with db_engine.begin() as conn:
            await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
        await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
        await _handle(db_engine, clock, a["id"])  # push無しでもnotificationsは書く
    resp = await api_client.get("/v1/notifications", headers=ha)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 2  # 起点ユーザーに提案通知2件
    assert items[0]["created_at"] == items[1]["created_at"]  # 同一時刻(FakeClock)
    assert items[0]["id"] > items[1]["id"]  # id降順タイブレーク
    assert [i["type"] for i in items] == ["proposal", "proposal"]


async def test_4b_pagination_and_validation_errors(api_client, db_engine, field):
    """改頁(limit=1で2頁)・limit=101→422・cursor形式不正→422(05 §5共通規定)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    for _ in range(2):
        hb = await _user(api_client, field)
        b = await _intent(
            api_client, db_engine, hb, _structured(start=start, expires=expires)
        )
        async with db_engine.begin() as conn:
            await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
        await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
        await _handle(db_engine, clock, a["id"])
    # 1頁目
    resp = await api_client.get("/v1/notifications?limit=1", headers=ha)
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert len(first["items"]) == 1
    assert first["next_cursor"]
    # 2頁目
    resp2 = await api_client.get(
        f"/v1/notifications?limit=1&cursor={first['next_cursor']}", headers=ha
    )
    assert resp2.status_code == 200, resp2.text
    second = resp2.json()
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] != first["items"][0]["id"]  # 取りこぼし・重複なし
    assert second["next_cursor"] is None
    # limit超過422
    resp3 = await api_client.get("/v1/notifications?limit=101", headers=ha)
    assert resp3.status_code == 422, resp3.text
    # cursor形式不正422
    resp4 = await api_client.get("/v1/notifications?cursor=%21%21invalid", headers=ha)
    assert resp4.status_code == 422, resp4.text


async def test_4c_hidden_row_proposal_minimal(api_client, db_engine, field):
    """hidden_until_match行: item.latch.proposalがheadcount+match_levelのみ
    (#22の下地・引用#2/#8・表示規制のサーバ側構造担保)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=start, expires=expires, visibility="hidden_until_match"),
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a["id"])
    resp = await api_client.get("/v1/notifications", headers=ha)
    assert resp.status_code == 200, resp.text
    (item,) = resp.json()["items"]
    assert item["type"] == "proposal"
    assert item["latch"]["proposal"] == {
        "headcount": 2,
        "match_level": "high",
    }  # 0.9=high。条件サマリ(time_summary等)は出ない


async def test_5a_read_marks_and_idempotent(api_client, db_engine, field):
    """既読: 204・read_at反映・二回目も204(冪等・design §2.6)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a["id"])
    resp = await api_client.get("/v1/notifications", headers=ha)
    (item,) = resp.json()["items"]
    assert item["read_at"] is None
    # 1回目: 204
    r1 = await api_client.post(f"/v1/notifications/{item['id']}/read", headers=ha)
    assert r1.status_code == 204, r1.text
    resp = await api_client.get("/v1/notifications", headers=ha)
    (after,) = resp.json()["items"]
    assert after["read_at"] is not None
    # 2回目: 204(冪等)
    r2 = await api_client.post(f"/v1/notifications/{item['id']}/read", headers=ha)
    assert r2.status_code == 204, r2.text


async def test_5b_read_not_found_for_others_and_missing(api_client, db_engine, field):
    """404系(Review Focus 4): 他人のid・存在しないidを区別しない404。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a["id"])
    resp = await api_client.get("/v1/notifications", headers=ha)
    (item,) = resp.json()["items"]
    # 他人(hb)から: 404
    r_other = await api_client.post(f"/v1/notifications/{item['id']}/read", headers=hb)
    assert r_other.status_code == 404, r_other.text
    assert r_other.json()["error"]["code"] == "NOT_FOUND"
    # 存在しないid: 404
    r_missing = await api_client.post(
        f"/v1/notifications/{uuid_mod.uuid4()}/read", headers=ha
    )
    assert r_missing.status_code == 404, r_missing.text


async def test_6_attendance_push_and_list(api_client, db_engine, field, caplog):
    """attendance送信(引用#10): matched→completed(sweeper)→push記録(第二汎用文)・
    一覧にattendance_request行・latch.status=completed。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    # matchedなlatches行を直接INSERT(回答APIを回すより早い・ws-2流儀)
    lo, hi = sorted([a["id"], b["id"]], key=str)
    async with db_engine.begin() as conn:
        latch_id = (
            await conn.execute(
                text("""
                    INSERT INTO latches
                        (intent_ids, proposal, score, status, responses,
                         response_deadline, expires_at, created_at)
                    VALUES (ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[],
                            CAST(:proposal AS jsonb), 0.9, 'matched',
                            CAST(:responses AS jsonb),
                            CAST(:deadline AS timestamptz),
                            CAST(:expires AS timestamptz),
                            CAST(:created AS timestamptz))
                    RETURNING id
                """),
                {
                    "a": lo,
                    "b": hi,
                    "proposal": json.dumps({"headcount": 2, "match_level": "high"}),
                    "responses": json.dumps([]),
                    "deadline": clock.now() + timedelta(hours=2),
                    "expires": clock.now() + timedelta(hours=FAR_EXPIRES_H),
                    "created": clock.now(),
                },
            )
        ).scalar_one()
        # 対象時刻経過を再現(intents.time_startを過去へ・apiのClockは差し替え不能
        # なためDB値操作 — ws-1計画§9-2規律)
        await conn.execute(
            text(
                "UPDATE intents SET time_start = CAST(:past AS timestamptz)"
                " WHERE id = ANY(ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[])"
            ),
            {"past": clock.now() - timedelta(hours=1), "a": lo, "b": hi},
        )
    sender = StubPushSender(clock=clock)

    class _NoopLatch:
        async def drain(self):
            return None

    sweeper = ExpirySweeper(
        engine=db_engine,
        clock=clock,
        latch=_NoopLatch(),
        batch_limit=50,
        push=sender,
    )
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await sweeper.run_once()
    recs = [r for r in _push_records(caplog) if r["latch_id"] == str(latch_id)]
    uid_a = await _user_id_of(db_engine, a["id"])
    uid_b = await _user_id_of(db_engine, b["id"])
    assert len(recs) == 2  # 参加者全員
    assert {r["user_id"] for r in recs} == {uid_a, uid_b}
    for r in recs:
        assert r["body"] == PUSH_BODY_NOTICE  # 第二汎用文
        assert r["notification_type"] == "attendance_request"
    resp = await api_client.get("/v1/notifications", headers=ha)
    types = [i["type"] for i in resp.json()["items"]]
    assert "attendance_request" in types
    att = next(i for i in resp.json()["items"] if i["type"] == "attendance_request")
    assert att["latch"]["status"] == "completed"


async def test_7a_nearby_push_notification(api_client, db_engine, field, caplog):
    """nearby(引用#5): 閾値未満+nearby_also→存在通知のpush記録(提案と同一文言)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=start, expires=expires, notification_level="nearby_also"),
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.5, 0.5)  # 閾値未満
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _handle(db_engine, clock, a["id"], push=StubPushSender(clock=clock))
    recs = _push_records(caplog)
    uid_a = await _user_id_of(db_engine, a["id"])
    assert len(recs) == 1
    assert recs[0]["user_id"] == uid_a  # nearby_also側のみ
    assert recs[0]["notification_type"] == "nearby_candidate"
    assert recs[0]["body"] == PUSH_BODY_LATCH  # 提案と同一文言(承認②)
    resp = await api_client.get("/v1/notifications", headers=ha)
    (item,) = resp.json()["items"]
    assert item["type"] == "nearby_candidate"
    assert item["latch"]["proposal"] == {"headcount": 2, "match_level": "low"}


async def test_7b_nearby_daily_limit_no_push(api_client, db_engine, field, caplog):
    """nearby日次上限(引用#5): 当日6件済み→存在通知なし(push記録なし)。
    candidate行自体は作る(上限はcandidateを破棄しない・ws-6)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=start, expires=expires, notification_level="nearby_also"),
    )
    uid_a = await _user_id_of(db_engine, a["id"])
    # 当日分の通知を6件事前投入(JST日付窓=FakeClockのnow)
    async with db_engine.begin() as conn:
        for _i in range(6):
            await conn.execute(
                text(
                    "INSERT INTO notifications (user_id, type, payload, created_at)"
                    " VALUES (CAST(:u AS uuid), 'proposal',"
                    " CAST(:payload AS jsonb), CAST(:now AS timestamptz))"
                ),
                {
                    "u": uid_a,
                    "payload": json.dumps({"latch_id": str(uuid_mod.uuid4())}),
                    "now": clock.now(),
                },
            )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.5, 0.5)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _handle(db_engine, clock, a["id"], push=StubPushSender(clock=clock))
    recs = [r for r in _push_records(caplog) if r["user_id"] == uid_a]
    assert recs == []  # 上限到達→存在通知なし
    # 事前投入6件のみ(当日分は増えない)
    rows = await _notifications_rows(db_engine, uid_a)
    assert len([r for r in rows if r[1] == "nearby_candidate"]) == 0


async def test_8_unauthenticated_401(api_client, db_engine, field):
    """無token 401(GET・POST両経路・C3)。"""
    resp = await api_client.get("/v1/notifications")
    assert resp.status_code == 401, resp.text
    resp2 = await api_client.post(f"/v1/notifications/{uuid_mod.uuid4()}/read")
    assert resp2.status_code == 401, resp2.text
