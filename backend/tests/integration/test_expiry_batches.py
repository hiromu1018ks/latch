"""期限切れバッチ群のintegration試験(M3 ws-2 design §4.2・02#4本体)。

実DB(compose常設)・実Redis・api常設(実HTTP)。sweeper/resetはFakeClock注入で
直接構成しrun_onceを呼ぶ(Workerプロセスは立てない — test_k_limits_e2eの
ReevalRunner扱いと同型)。期限切れの再現はFakeClock.advance(時刻源を1本化。
試験3のみapiプロセスClock(SystemClock)基準のためDB値操作 — ws-1計画§9-2規律)。
latches行はfixtureで直接INSERT(回答APIはlatches行しか読まない・ws-1流儀)。
"""

import asyncio
import json
import math
import random
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import JST, FakeClock, SystemClock
from latch.worker.cost import JevCostStore
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.reeval import ReevalRunner
from latch.worker.reset import ResetJob
from latch.worker.sweeper import ExpirySweeper

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120  # 時間窓はnow+5日系と分離(ws-1・M2 ws-8の対抗策規律)
SUBJECT_PREFIX = "m3ws2-"


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
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
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)


@pytest.fixture
async def redis_sweep(redis_client):
    """Redis試験ごとのkey_prefix(teardownで掃除・test_k_limits流儀)。"""
    prefix = f"m3ws2r-{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    cursor = 0
    keys: list[str] = []
    while True:
        cursor, batch = await redis_client.scan(
            cursor=cursor, match=f"{prefix}*", count=100
        )
        keys.extend(batch)
        if cursor == 0:
            break
    if keys:
        await redis_client.delete(*keys)


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
        json={"display_name": "m3ws2", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured(*, start: str | None = None, expires: str | None = None) -> dict:
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": None},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }
    if expires is not None:
        d["expires_at"] = expires  # 明示期限(補完のスナップ回避・k_limits流儀)
    return d


async def _intent(
    api_client,
    headers,
    *,
    status: str = "active",
    start: str | None = None,
    expires: str | None = None,
) -> dict:
    resp = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={
            "raw_text": "分類用テキスト",
            "status": status,
            "structured_intent": _structured(start=start, expires=expires),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]


async def _latch(
    db_engine,
    intent_ids: list[str],
    *,
    now: datetime,
    status: str = "proposed",
    score: float = 0.85,
    deadline_offset: timedelta = timedelta(hours=2),
    expires_offset: timedelta = timedelta(hours=120),
) -> str:
    """latches行を直接INSERT(fixture)。期限列はnow基準のオフセット指定。"""
    proposal = {"headcount": len(intent_ids), "match_level": "medium"}
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (CAST(:ids AS uuid[]),
                        CAST(:proposal AS jsonb), :score, :status,
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in intent_ids],
                "proposal": json.dumps(proposal, ensure_ascii=False),
                "score": score,
                "status": status,
                "deadline": now + deadline_offset,
                "expires": now + expires_offset,
                "now": now,
            },
        )
    return str(res.first()[0])


def _unique_vec() -> str:
    """テスト毎に一意な768次元ランダム単位ベクトル(k_limits流儀)。"""
    components = [random.random() for _ in range(768)]
    norm = math.sqrt(sum(c * c for c in components)) or 1.0
    return "[" + ",".join(repr(c / norm) for c in components) + "]"


async def _set_embedding(db_engine, intent_id: str) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:vec AS vector),"
                " embedding_model = 'test' WHERE id = CAST(:id AS uuid)"
            ),
            {"vec": _unique_vec(), "id": intent_id},
        )


def _sweeper(db_engine, clock):
    """実LatchEngineをdrain対象に持つExpirySweeper(試験ごとに構築)。"""
    latch_engine = LatchEngine(engine=db_engine, clock=clock)
    return ExpirySweeper(
        engine=db_engine, clock=clock, latch=latch_engine, batch_limit=50
    )


async def _status(db_engine, table: str, row_id: str) -> str:
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(f"SELECT status FROM {table} WHERE id = CAST(:id AS uuid)"),
            {"id": row_id},
        )
        return res.scalar_one()


# --- 試験1: 02#4本体(G1引継ぎ・引用#20のlatch同時閉鎖・Review Focus 5) ---


async def test_1_intent_expiry_transition_and_event(api_client, db_engine, field):
    """API保存(expires明示)→期限内は対象外→FakeClock進行→expired遷移+
    match_eventsのexpiredイベント1回。参加candidateのlatchesも同tickで閉じる
    (latches.expires_at=参加Intent期限の最小値・引用#20)。"""
    clock = FakeClock(SystemClock().now())
    headers = await _user(api_client, field)
    intent = await _intent(
        api_client,
        headers,
        expires=(clock.now() + timedelta(hours=1)).isoformat(),
    )
    # 参加candidate行(期限はIntentと同時刻に切れるよう固定で直書き)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (ARRAY[CAST(:iid AS uuid)] || CAST(:other AS uuid),
                        CAST(:proposal AS jsonb), 0.85, 'candidate',
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
            """),
            {
                "iid": intent["id"],
                "other": uuid_mod.uuid4(),  # 単独Intent扱いの埋め草(評価は走らない)
                "proposal": json.dumps({"headcount": 2, "match_level": "medium"}),
                "deadline": clock.now() + timedelta(hours=2),
                "expires": clock.now() + timedelta(hours=1),  # Intent期限と同時
                "now": clock.now(),
            },
        )
    sw = _sweeper(db_engine, clock)
    assert await sw.run_once() == 0  # 期限内→対象なし(遷移なし)

    clock.advance(timedelta(minutes=61))  # 期限(expires=+1h)を経過
    assert await sw.run_once() == 2  # intent expired + latch candidate expired

    assert await _status(db_engine, "intents", intent["id"]) == "expired"
    async with db_engine.begin() as conn:
        ev = await conn.execute(
            text(
                "SELECT payload->>'version' FROM match_events"
                " WHERE source_intent_id = CAST(:iid AS uuid)"
                "   AND event_type = 'expired'"
            ),
            {"iid": intent["id"]},
        )
        rows = ev.fetchall()
    assert rows == [(str(intent["version"]),)]  # イベント1回・version一致
    # 参加candidate行もexpired(引用#20・Review Focus 5)
    async with db_engine.begin() as conn:
        latch_status = await conn.execute(
            text(
                "SELECT l.status FROM latches l"
                " WHERE CAST(:iid AS uuid) = ANY(l.intent_ids)"
                "   AND l.status <> 'candidate'"
            ),
            {"iid": intent["id"]},
        )
        assert latch_status.scalar_one() == "expired"

    # 再実行で変化なし(冪等・イベントは1回のまま)
    clock.advance(timedelta(minutes=1))
    assert await sw.run_once() == 0
    async with db_engine.begin() as conn:
        cnt = await conn.execute(
            text(
                "SELECT COUNT(*) FROM match_events"
                " WHERE source_intent_id = CAST(:iid AS uuid)"
                "   AND event_type = 'expired'"
            ),
            {"iid": intent["id"]},
        )
        assert cnt.scalar_one() == 1


# --- 試験2: latches期限切れmatrix(引用#2・Review Focus 2) ---


async def test_2_latches_expiry_matrix(api_client, db_engine, field):
    """①proposed×deadline切れ②proposed×expires切れ③candidate(nearby含む)×
    expires切れ④期限前proposed不変⑤candidate×暫定deadline過去・expires未来は
    expiredにしない(candidateはresponse_deadlineを判定に使わない・引用#2。
    ①②③のexpiredをクローズ検知が観測してl5は同tickdrainでproposedへ昇格 —
    design §2.7「期限切れで空いた枠の同tick昇格」の設計どおり)。"""
    clock = FakeClock(SystemClock().now())
    base = clock.now()
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    i1 = await _intent(api_client, ha)
    i2 = await _intent(api_client, hb)
    i3 = await _intent(api_client, hb)
    i4 = await _intent(api_client, hb)
    i5 = await _intent(api_client, hb)
    l1 = await _latch(  # ① proposed×response_deadline切れ
        db_engine,
        [i1["id"], i2["id"]],
        now=base,
        status="proposed",
        deadline_offset=timedelta(hours=-1),
        expires_offset=timedelta(hours=120),
    )
    l2 = await _latch(  # ② proposed×expires_at切れ
        db_engine,
        [i1["id"], i3["id"]],
        now=base,
        status="proposed",
        deadline_offset=timedelta(hours=2),
        expires_offset=timedelta(hours=-1),
    )
    l3 = await _latch(  # ③ candidate(nearby・score<0.60)×expires切れ
        db_engine,
        [i1["id"], i4["id"]],
        now=base,
        status="candidate",
        score=0.30,
        deadline_offset=timedelta(hours=-1),
        expires_offset=timedelta(minutes=-1),
    )
    l4 = await _latch(  # ④ 期限前proposed(対象外)
        db_engine,
        [i1["id"], i5["id"]],
        now=base,
        status="proposed",
        deadline_offset=timedelta(hours=2),
        expires_offset=timedelta(hours=120),
    )
    l5 = await _latch(  # ⑤ candidate×暫定deadline過去・expires未来(不変)
        db_engine,
        [i2["id"], i3["id"]],
        now=base,
        status="candidate",
        score=0.85,
        deadline_offset=timedelta(hours=-1),
        expires_offset=timedelta(hours=120),
    )
    sw = _sweeper(db_engine, clock)
    assert await sw.run_once() == 3  # ①②③のみ
    got = {
        l1: "expired",
        l2: "expired",
        l3: "expired",
        l4: "proposed",
        # l5は期限切れで破棄されない(⑤の本質)・空いた枠を同tickdrainが昇格
        l5: "proposed",
    }
    for latch_id, expected in got.items():
        assert await _status(db_engine, "latches", latch_id) == expected, latch_id
    # latch_status_events: expired遷移3行(システム起因=user_id NULL)
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT latch_id, from_status, to_status, user_id"
                " FROM latch_status_events WHERE to_status = 'expired'"
                "   AND latch_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [uuid_mod.UUID(x) for x in (l1, l2, l3)]},
        )
        rows = res.fetchall()
    assert len(rows) == 3
    assert all(r[2] == "expired" and r[3] is None for r in rows)
    from_statuses = {str(r[0]): r[1] for r in rows}
    assert from_statuses[l1] == "proposed"
    assert from_statuses[l3] == "candidate"
    # l4(期限前)へのイベント書き込みなし・l5の遷移はcandidate→proposedのみ
    # (to_status='expired'がない=暫定deadlineでの誤破棄なし・引用#2)
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT to_status FROM latch_status_events"
                " WHERE latch_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [uuid_mod.UUID(x) for x in (l4, l5)]},
        )
        rows = res.fetchall()
    assert rows == [("proposed",)]  # l4=0行・l5=昇格1行のみ


# --- 試験3: 期限切れ直後の回答は409(api Clock=SystemClock基準・引用#3) ---


async def test_3_response_after_deadline_is_409(api_client, db_engine, field):
    """sweeper未実行のまま期限切れ(status=proposed)でも回答は受理されない
    (409 LATCH_EXPIRED)→sweeperでexpired化→expired後も409。
    apiプロセスのClockは差し替え不能のためdeadlineをDB直書きで過去へ
    (ws-1計画§9-2の等価置換と同一規律)。"""
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    i1 = await _intent(api_client, ha)
    i2 = await _intent(api_client, hb)
    now = SystemClock().now()
    latch_id = await _latch(
        db_engine,
        [i1["id"], i2["id"]],
        now=now,
        status="proposed",
        deadline_offset=timedelta(hours=2),
        expires_offset=timedelta(hours=120),
    )
    # deadlineを過去へ(sweeper遅延でstatusがproposedのままの状態を再現)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET response_deadline ="
                " CAST(:past AS timestamptz) WHERE id = CAST(:id AS uuid)"
            ),
            {"past": SystemClock().now() - timedelta(hours=1), "id": latch_id},
        )
    resp = await api_client.post(
        f"/v1/latches/{latch_id}/response",
        headers=ha,
        json={"response": "yes"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "LATCH_EXPIRED"
    assert await _status(db_engine, "latches", latch_id) == "proposed"  # 不変

    clock = FakeClock(SystemClock().now())
    assert await _sweeper(db_engine, clock).run_once() == 1
    assert await _status(db_engine, "latches", latch_id) == "expired"

    resp2 = await api_client.post(
        f"/v1/latches/{latch_id}/response",
        headers=ha,
        json={"response": "yes"},
    )
    assert resp2.status_code == 409, resp2.text
    assert resp2.json()["error"]["code"] == "LATCH_EXPIRED"  # expired後も同分類


# --- 試験4: draft→expired(05 §6・引用#5) ---


async def test_4_draft_intent_expires(api_client, db_engine, field):
    """active化されないまま期限が切れた下書きも対象(05 §6)。"""
    clock = FakeClock(SystemClock().now())
    headers = await _user(api_client, field)
    draft = await _intent(
        api_client,
        headers,
        status="draft",
        expires=(clock.now() + timedelta(hours=1)).isoformat(),
    )
    clock.advance(timedelta(minutes=61))
    assert await _sweeper(db_engine, clock).run_once() == 1
    assert await _status(db_engine, "intents", draft["id"]) == "expired"
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT COUNT(*) FROM match_events"
                " WHERE source_intent_id = CAST(:iid AS uuid)"
                "   AND event_type = 'expired'"
            ),
            {"iid": draft["id"]},
        )
        assert res.scalar_one() == 1  # draftもexpiredイベントを発行


# --- 試験5: matched→completed + attendance通知先行書き込み(引用#15/#16・
#     Review Focus 3) ---


async def test_5_matched_to_completed_attendance_notifications(
    api_client, db_engine, field
):
    """対象時刻(max time_start)経過のmatched→completed+completed_at+
    イベント(matched→completed・user_id NULL)+attendance_request通知
    (参加者全員・payload={latch_id})。cancelled(解散済み)には通知なし。"""
    clock = FakeClock(SystemClock().now())
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    # expires明示(+150h≦7日上限): 補完値(最大now+72h)はadvance(121h)で
    # 切れrun_onceのカウントに混入するため(k_limits流儀のsnap回避)
    long_expires = (clock.now() + timedelta(hours=150)).isoformat()
    i1 = await _intent(api_client, ha, expires=long_expires)  # time_start=+120h
    i2 = await _intent(api_client, hb, expires=long_expires)
    i3 = await _intent(api_client, ha, expires=long_expires)
    i4 = await _intent(api_client, hb, expires=long_expires)
    base = clock.now()
    l_matched = await _latch(
        db_engine,
        [i1["id"], i2["id"]],
        now=base,
        status="matched",
        deadline_offset=timedelta(hours=240),
        expires_offset=timedelta(hours=240),
    )
    l_cancelled = await _latch(
        db_engine,
        [i3["id"], i4["id"]],
        now=base,
        status="matched",
        deadline_offset=timedelta(hours=240),
        expires_offset=timedelta(hours=240),
    )
    # 解散済み(cancelled)を再現: 対象時刻は経過させるがstatusがmatchedでない
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET status = 'cancelled' WHERE id = CAST(:id AS uuid)"
            ),
            {"id": l_cancelled},
        )

    clock.advance(timedelta(hours=BASE_HOURS + 1))  # 対象時刻(+120h)を経過
    assert await _sweeper(db_engine, clock).run_once() == 1  # matchedのみ

    async with db_engine.begin() as conn:
        row = await conn.execute(
            text(
                "SELECT status, completed_at FROM latches WHERE id = CAST(:id AS uuid)"
            ),
            {"id": l_matched},
        )
        status, completed_at = row.one()
    assert status == "completed"
    assert completed_at is not None
    # イベント(matched→completed・user_id NULL)
    async with db_engine.begin() as conn:
        ev = await conn.execute(
            text(
                "SELECT from_status, to_status, user_id FROM latch_status_events"
                " WHERE latch_id = CAST(:id AS uuid)"
            ),
            {"id": l_matched},
        )
        assert ev.one() == ("matched", "completed", None)
        # attendance_request通知: matched分は参加者2人・cancelled分は0件
        n_matched = await conn.execute(
            text(
                "SELECT COUNT(*) FROM notifications"
                " WHERE type = 'attendance_request'"
                "   AND payload->>'latch_id' = :lid"
            ),
            {"lid": l_matched},
        )
        assert n_matched.scalar_one() == 2
        n_cancelled = await conn.execute(
            text(
                "SELECT COUNT(*) FROM notifications"
                " WHERE type = 'attendance_request'"
                "   AND payload->>'latch_id' = :lid"
            ),
            {"lid": l_cancelled},
        )
        assert n_cancelled.scalar_one() == 0
    # cancelled行はcompletedにしない(対象外のまま)
    assert await _status(db_engine, "latches", l_cancelled) == "cancelled"


# --- 試験6: SKIP LOCKED簡易並行性(引用#3・Review Focus 1) ---


async def test_6_concurrent_run_once_single_transition(api_client, db_engine, field):
    """同一sweeperのrun_onceを並行2重実行→同一行が二重に遷移しない
    (latch_status_eventsのexpired遷移は行ごとに1行のみ)。"""
    clock = FakeClock(SystemClock().now())
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    i1 = await _intent(api_client, ha)
    i2 = await _intent(api_client, hb)
    i3 = await _intent(api_client, ha)
    i4 = await _intent(api_client, hb)
    i5 = await _intent(api_client, ha)
    i6 = await _intent(api_client, hb)
    base = clock.now()
    ids = []
    for a, b in ((i1, i2), (i3, i4), (i5, i6)):
        ids.append(
            await _latch(
                db_engine,
                [a["id"], b["id"]],
                now=base,
                status="proposed",
                deadline_offset=timedelta(hours=-1),
                expires_offset=timedelta(hours=120),
            )
        )
    sw = _sweeper(db_engine, clock)
    await asyncio.gather(sw.run_once(), sw.run_once())
    for latch_id in ids:
        assert await _status(db_engine, "latches", latch_id) == "expired"
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT latch_id, COUNT(*) FROM latch_status_events"
                " WHERE to_status = 'expired'"
                "   AND latch_id = ANY(CAST(:ids AS uuid[]))"
                " GROUP BY latch_id HAVING COUNT(*) > 1"
            ),
            {"ids": [uuid_mod.UUID(x) for x in ids]},
        )
        assert res.fetchall() == []  # 二重遷移なし


# --- 試験7: クローズ検知drain(§2.7・06 §10クローズ側トリガーの消費) ---


async def test_7_close_detection_drain_promotes_candidate(api_client, db_engine, field):
    """D-08同時3件上限で保留(candidate)の行が、参加提案の1つのrejectedを
    sweeperが観測(latch_status_events)してdrain→proposedへ昇格する。
    response_deadlineはD-05再計算・proposal通知は参加者2人分。"""
    clock = FakeClock(SystemClock().now())
    base = clock.now()
    ha = await _user(api_client, field)  # a1〜a4の所有者
    hb = await _user(api_client, field)  # w1の所有者
    a1 = await _intent(api_client, ha)
    a2 = await _intent(api_client, ha)
    a3 = await _intent(api_client, ha)
    a4 = await _intent(api_client, ha)
    w1 = await _intent(api_client, hb)
    # w1のD-08同時3件を消費済みの既存proposed×3(期限は未来・sweeper対象外)。
    # D-08同時上限の集計はintent単位(_COUNT_OPEN_PROPOSEDの
    # intent_ids @> ARRAY[:intent_id]・引用#10)のため、既存3行すべてに
    # w1を含める(計画書のha単位構成は実装と不合致)
    l_a1 = await _latch(db_engine, [a1["id"], w1["id"]], now=base, status="proposed")
    await _latch(db_engine, [a2["id"], w1["id"]], now=base, status="proposed")
    await _latch(db_engine, [a3["id"], w1["id"]], now=base, status="proposed")
    # 対象の保留行(a4・w1: 上限が空けば昇格できる)
    target = await _latch(
        db_engine,
        [a4["id"], w1["id"]],
        now=base,
        status="candidate",
        score=0.85,
    )

    latch_engine = LatchEngine(engine=db_engine, clock=clock)
    sweeper = ExpirySweeper(
        engine=db_engine, clock=clock, latch=latch_engine, batch_limit=50
    )
    # 事前: drainを直接呼んでもa4はproposed 3件で上限→candidateのまま
    await latch_engine.drain()
    assert await _status(db_engine, "latches", target) == "candidate"

    # a1参加のlatchを回答APIでno→rejected(latch_status_events挿入)
    resp = await api_client.post(
        f"/v1/latches/{l_a1}/response", headers=ha, json={"response": "no"}
    )
    assert resp.status_code == 200, resp.text

    # sweeper: 期限切れ対象なし・観測でrejectedを検知→drain→保留行が昇格
    assert await sweeper.run_once() == 0
    assert await _status(db_engine, "latches", target) == "proposed"
    async with db_engine.begin() as conn:
        row = await conn.execute(
            text("SELECT response_deadline FROM latches WHERE id = CAST(:id AS uuid)"),
            {"id": target},
        )
        deadline = row.scalar_one()
    assert deadline > clock.now()  # D-05再計算(未来)
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT COUNT(*) FROM notifications WHERE type = 'proposal'"
                "   AND payload->>'latch_id' = :lid"
            ),
            {"lid": target},
        )
        assert res.scalar_one() == 2  # a4・w1の参加者2人分(mutedなし)


# --- 試験8: catch-up統合の回帰(design §2.1・§4.2-7) ---


async def test_8_reeval_runner_integrates_sweeper(api_client, db_engine, field):
    """ReevalRunner+sweeper注入構成: run_onceが「期限切れ処理→catch-up抽出」
    の順に両方実行する。期限切れintentはexpired化・catch-up対象(期限2時間
    以内・embeddingあり)はpipelineへ投入。既存注入なし構成はunit側で担保。"""
    clock = FakeClock(SystemClock().now())
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    # a: catch-up対象(期限2時間以内・embedding直書き)
    a = await _intent(
        api_client, ha, expires=(clock.now() + timedelta(minutes=90)).isoformat()
    )
    await _set_embedding(db_engine, a["id"])
    # b: 期限切れ対象(+60分 → advance 61分で経過)
    b = await _intent(
        api_client, hb, expires=(clock.now() + timedelta(minutes=60)).isoformat()
    )

    pipelined: list[str] = []
    latch_engine = LatchEngine(engine=db_engine, clock=clock)
    sweeper = ExpirySweeper(
        engine=db_engine, clock=clock, latch=latch_engine, batch_limit=50
    )

    async def pipeline(intent_id):
        pipelined.append(str(intent_id))

    runner = ReevalRunner(
        engine=db_engine,
        clock=clock,
        guard=None,  # ガード判定不要(投入の記録のみ・実L1〜3はk_limitsで担保)
        pipeline=pipeline,
        interval_sec=0.01,
        batch_limit=50,
        sweeper=sweeper,
    )
    clock.advance(timedelta(minutes=61))  # b期限切れ・aは残29分(2時間以内)
    done = await runner.run_once()
    assert done == 1  # catch-upはaのみ(bはsweeperがexpired化済みで対象外)
    assert pipelined == [a["id"]]
    assert await _status(db_engine, "intents", b["id"]) == "expired"  # sweeper先行


# --- 試験9a: リセットジョブのキー掃除(design §2.5・§4.2-8) ---


async def test_9a_reset_job_key_cleanup(db_engine, redis_client, redis_sweep):
    """run_once(0時発火の本体): 前日キー4種を消滅・当日キーは保持。
    月初(11-01)は前月(10月)月次も消滅・月初以外(10-15)は当月月次が残存。"""
    store = JevCostStore(redis_client, key_prefix=redis_sweep)
    # 月初0時5秒(2026-11-01 JST)を再現
    clock = FakeClock(datetime(2026, 11, 1, 0, 0, 5, tzinfo=JST))
    await store.incr_daily("20261031")
    await store.record_execution("typesafe_jev", "20261031")
    await store.incr_intent("i-1", "20261031")
    await store.incr_user("u-1", "20261031")
    await store.incr_daily("20261101")  # 当日分(消さない)
    await store.incr_monthly("202610")  # 前月(月初→掃除対象)
    job = ResetJob(
        cost_store=store,
        clock=clock,
        latch=LatchEngine(engine=db_engine, clock=clock),
        retry_sec=300,
    )
    await job.run_once()
    p = redis_sweep
    assert await redis_client.get(f"{p}jev:daily:20261031") is None
    assert await redis_client.get(f"{p}jev:exec:20261031:typesafe_jev") is None
    assert await redis_client.get(f"{p}jev:intent:i-1:20261031") is None
    assert await redis_client.get(f"{p}jev:user:u-1:20261031") is None
    assert await redis_client.get(f"{p}jev:daily:20261101") == "1"  # 当日保持
    assert await redis_client.get(f"{p}jev:monthly:202610") is None  # 月初掃除

    # 月初以外(2026-10-15): 当月月次は残存
    clock.set(datetime(2026, 10, 15, 0, 0, 5, tzinfo=JST))
    await store.incr_monthly("202610")
    await job.run_once()
    assert await redis_client.get(f"{p}jev:monthly:202610") == "1"  # 残存


# --- 試験9b: 0時跨ぎdrainでD-08日次上限が回復(引用#11/#17) ---


async def test_9b_reset_job_drain_recovers_d08_daily_quota(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """前日に通知6件(D-08日次上限到達)のユーザーの保留candidateが、
    0時跨ぎ後のdrainで提示される(カウント真実=notificationsの日付条件切替)。"""
    clock = FakeClock(SystemClock().now())
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    a = await _intent(api_client, ha)
    w = await _intent(api_client, hb)
    target = await _latch(
        db_engine,
        [a["id"], w["id"]],
        now=clock.now(),
        status="candidate",
        score=0.85,
    )
    # haの当日分通知6件(type='proposal'・上限到達の再現)。
    # user_idはAPIレスポンスのintent表現に含まれないためDBから取得
    async with db_engine.begin() as conn:
        uid = (
            await conn.execute(
                text("SELECT user_id FROM intents WHERE id = CAST(:iid AS uuid)"),
                {"iid": a["id"]},
            )
        ).scalar_one()
        for _ in range(6):
            await conn.execute(
                text(
                    "INSERT INTO notifications (user_id, type, payload, created_at)"
                    " VALUES (CAST(:uid AS uuid), 'proposal',"
                    " CAST(:payload AS jsonb), CAST(:now AS timestamptz))"
                ),
                {
                    "uid": uid,
                    "payload": json.dumps({"latch_id": str(uuid_mod.uuid4())}),
                    "now": clock.now(),
                },
            )
    latch_engine = LatchEngine(engine=db_engine, clock=clock)
    # 事前(当日): 6件上限で昇格しない
    await latch_engine.drain()
    assert await _status(db_engine, "latches", target) == "candidate"

    # 翌日JST 0時5秒へ跨ぐ(リセットジョブの発火時刻)
    now_jst = clock.now().astimezone(JST)
    next_day = now_jst + timedelta(days=1)
    clock.set(
        datetime(next_day.year, next_day.month, next_day.day, 0, 0, 5, tzinfo=JST)
    )
    # cost_storeは実JevCostStore(掃除対象キーがなくても無害・teardownで掃除)
    job = ResetJob(
        cost_store=JevCostStore(redis_client, key_prefix=redis_sweep),
        clock=clock,
        latch=latch_engine,
    )
    await job.run_once()
    assert await _status(db_engine, "latches", target) == "proposed"
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT COUNT(*) FROM notifications WHERE type = 'proposal'"
                "   AND payload->>'latch_id' = :lid"
                "   AND created_at >= CAST(:day AS timestamptz)"
            ),
            {"lid": target, "day": clock.now()},
        )
        assert res.scalar_one() == 2  # 翌日分として参加者2人へ提示
