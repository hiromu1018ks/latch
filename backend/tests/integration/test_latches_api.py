"""LATCH応答系コアのintegration試験(M3 ws-1 design §4.2の17試験)。

実DB(compose常設)・実Redis・api常設(実HTTP)。latches行はfixtureで直接
INSERT(回答APIはlatches行しか読まないためパイプラインを走らせない)。
jev_resultはmatch_candidatesへ直接INSERT(Calibration試験用・ws流儀)。
期限切れ試験はDB値を過去へUPDATE(§9-2・apiプロセスのClockは差し替え
不能のため等価置換)。stage1拡張(試験17)はclose_latches_on_deleteを
直接呼ぶ(§9-6)。
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
SUBJECT_PREFIX = "m3ws1-"


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
        json={"display_name": "m3ws1", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured(*, secondary: str | None = None, start: str | None = None) -> dict:
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": secondary},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }
    return d


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
    status: str = "proposed",
    score: float = 0.85,
    gid: str | None = None,
    deadline_hours: float = 2.0,
    expires_hours: float = 120.0,
    proposal: dict | None = None,
) -> str:
    """latches行を直接INSERT(fixture・回答APIはlatches行のみ読む)。"""
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
                "deadline": now + timedelta(hours=deadline_hours),
                "expires": now + timedelta(hours=expires_hours),
                "now": now,
            },
        )
    return str(res.first()[0])


async def _pair_eval(
    db_engine,
    a_id: str,
    b_id: str,
    *,
    wa: float,
    wb: float,
    latch_score: float | None = None,
) -> None:
    """match_candidatesへjev_result付きevaluatedを直接INSERT(試験13/14用)。"""
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
    # 実物パイプライン(candidates.pyのnormalize_pair)と同一のa<b正規化・UPSERT
    lo_id, hi_id = sorted((a_id, b_id))
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO match_candidates
                    (intent_a_id, intent_b_id, intent_a_version,
                     intent_b_version, retrieval_score, cheap_judge_score,
                     jev_result, latch_score, status, created_at, updated_at)
                VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 1, 1,
                        0.9, 0.9, CAST(:jev AS jsonb), :ls, 'evaluated',
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
                "ls": latch_score,
                "now": now,
            },
        )


async def _respond(api_client, headers, latch_id: str, response: str):
    return await api_client.post(
        f"/v1/latches/{latch_id}/response",
        headers=headers,
        json={"response": response},
    )


async def _latch_row(db_engine, latch_id: str) -> dict:
    async with db_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT status, responses FROM latches"
                        " WHERE id = CAST(:l AS uuid)"
                    ),
                    {"l": latch_id},
                )
            )
            .mappings()
            .first()
        )
    assert row is not None
    return dict(row)


async def _events(db_engine, latch_id: str) -> list[tuple]:
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT from_status, to_status, user_id FROM latch_status_events"
                    " WHERE latch_id = CAST(:l AS uuid) ORDER BY created_at"
                ),
                {"l": latch_id},
            )
        ).fetchall()
    return [(str(r[0]), r[1], None if r[2] is None else str(r[2])) for r in rows]


async def _calibration(db_engine, latch_id: str) -> dict | None:
    async with db_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT prediction, proposal_snapshot, actual_responses,"
                        " matched FROM calibration_records"
                        " WHERE latch_id = CAST(:l AS uuid)"
                    ),
                    {"l": latch_id},
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


# -- 試験1: 1対1の双方YES --


async def test_1_pair_double_yes(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    r1 = await _respond(api_client, h1, latch, "yes")
    assert r1.status_code == 200, r1.text
    assert r1.json()["latch"]["status"] == "partial_accept"
    r2 = await _respond(api_client, h2, latch, "yes")
    assert r2.status_code == 200, r2.text
    body = r2.json()["latch"]
    assert body["status"] == "matched"
    assert body["remaining_responses"] == 0
    row = await _latch_row(db_engine, latch)
    assert len(row["responses"]) == 2
    for r in row["responses"]:
        datetime.fromisoformat(r["answered_at"])  # Clock由来ISO(§9-2注記)
    assert row["status"] == "matched"


# -- 試験2: noとdefer --


async def test_2_no_and_defer_rejected(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch_no = await _latch(db_engine, [i1["id"], i2["id"]])
    r = await _respond(api_client, h1, latch_no, "no")
    assert r.json()["latch"]["status"] == "rejected"
    row = await _latch_row(db_engine, latch_no)
    assert row["responses"][0]["response"] == "no"  # 区別保持(引用#2)
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 12))
    i4 = await _intent(api_client, h2, start=_future(BASE_HOURS + 12))
    latch_defer = await _latch(db_engine, [i3["id"], i4["id"]])
    r = await _respond(api_client, h1, latch_defer, "defer")
    assert r.json()["latch"]["status"] == "rejected"
    row = await _latch_row(db_engine, latch_defer)
    assert row["responses"][0]["response"] == "defer"


# -- 試験3: 二重回答 --


async def test_3_double_answer_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    assert (await _respond(api_client, h1, latch, "yes")).status_code == 200
    r = await _respond(api_client, h1, latch, "yes")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ALREADY_ANSWERED"


# -- 試験4: 期限切れ(DB値操作・§9-2) --


async def test_4_expired_409_without_transition(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    # api常設のClockは動かせないため期限値を過去へ(等価置換・§9-2)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET response_deadline = CAST(:d AS timestamptz)"
                " WHERE id = CAST(:l AS uuid)"
            ),
            {"d": SystemClock().now() - timedelta(hours=1), "l": latch},
        )
    r = await _respond(api_client, h1, latch, "yes")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "LATCH_EXPIRED"
    row = await _latch_row(db_engine, latch)
    assert row["status"] == "proposed"  # 遷移しない(expired遷移はws-2・引用#8)
    assert row["responses"] == []


# -- 試験5: 競合クローズ --


async def test_5_conflict_close_on_match(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    i3 = await _intent(api_client, h3)
    latch_a = await _latch(db_engine, [i1["id"], i2["id"]])
    latch_b = await _latch(db_engine, [i2["id"], i3["id"]])
    await _respond(api_client, h1, latch_a, "yes")  # partial
    r = await _respond(api_client, h2, latch_a, "yes")  # matched
    assert r.status_code == 200
    assert r.json()["latch"]["status"] == "matched"
    row_b = await _latch_row(db_engine, latch_b)
    assert row_b["status"] == "cancelled"  # I2を含む他の開いているlatches
    events_b = await _events(db_engine, latch_b)
    assert ("proposed", "cancelled", None) in events_b  # user_id=NULL(引用#12)


# -- 試験6: クローズ後の回答 --


async def test_6_answer_to_cancelled_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]], status="cancelled")
    r = await _respond(api_client, h1, latch, "yes")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "LATCH_CLOSED"


# -- 試験7: 認可・不在・未登録 --


async def test_7_authorization(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    outsider = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)  # 同一ユーザー2Intent(1対1作成用)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    # 参加者以外は403(存在秘匿しない・引用#17)
    r = await _respond(api_client, outsider, latch, "yes")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    # 存在しないidは404
    r = await _respond(api_client, h1, str(uuid_mod.uuid4()), "yes")
    assert r.status_code == 404
    # 未登録JWTも404(intentsと同型)
    subject = f"{field}{uuid_mod.uuid4().hex[:8]}"
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    unregistered = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    r = await _respond(api_client, unregistered, latch, "yes")
    assert r.status_code == 404


# -- 試験8: 422 --


async def test_8_invalid_response_value_422(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    r = await _respond(api_client, h1, latch, "maybe")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# -- 試験9: グループ3人(全員YES成立・部分成立禁止・D-06) --


async def test_9_group_of_three(api_client, db_engine, field):
    hs = [await _user(api_client, field) for _ in range(3)]
    intents = [await _intent(api_client, h) for h in hs]
    latch = await _latch(db_engine, [i["id"] for i in intents])
    r1 = await _respond(api_client, hs[0], latch, "yes")
    assert r1.status_code == 200
    body = r1.json()["latch"]
    assert body["status"] == "partial_accept"
    # remaining=len(intent_ids)−yes数=3-1(§9-10・人数のみ引用#22)
    assert body["remaining_responses"] == 2
    await _respond(api_client, hs[1], latch, "yes")  # 2人yesでも未成立
    assert (await _latch_row(db_engine, latch))["status"] == "partial_accept"
    r3 = await _respond(api_client, hs[2], latch, "yes")
    assert r3.json()["latch"]["status"] == "matched"  # 3人目で成立
    # 別latch: 誰かのnoで即rejected(3人YESでも成立しない逆側・部分成立なし)
    intents2 = [
        await _intent(api_client, h, start=_future(BASE_HOURS + 24)) for h in hs
    ]
    latch2 = await _latch(db_engine, [i["id"] for i in intents2])
    await _respond(api_client, hs[0], latch2, "yes")
    await _respond(api_client, hs[1], latch2, "yes")
    r = await _respond(api_client, hs[2], latch2, "no")
    assert r.json()["latch"]["status"] == "rejected"


# -- 試験10: グループ4人・group_candidate_id --


async def test_10_group_of_four_keeps_gid(api_client, db_engine, field):
    hs = [await _user(api_client, field) for _ in range(4)]
    intents = [await _intent(api_client, h) for h in hs]
    ids = [i["id"] for i in intents]
    now = SystemClock().now()
    member_scores = {
        "seed_id": ids[0],
        "versions": {i: 1 for i in ids},
    }
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO group_candidates
                    (intent_ids, member_scores, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:ms AS jsonb), 'proposed',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in ids],
                "ms": json.dumps(member_scores),
                "now": now,
            },
        )
        gid = str(res.first()[0])
    latch = await _latch(db_engine, ids, gid=gid)
    for h in hs:
        r = await _respond(api_client, h, latch, "yes")
        assert r.status_code == 200, r.text
    assert (await _latch_row(db_engine, latch))["status"] == "matched"
    async with db_engine.connect() as conn:
        got = (
            await conn.execute(
                text(
                    "SELECT group_candidate_id FROM latches WHERE id = CAST(:l AS uuid)"
                ),
                {"l": latch},
            )
        ).scalar_one()
    assert str(got) == gid  # 回答APIはgidを壊さない(§2.10と対の検証)


# -- 試験11: Intent遷移・競合(成立時) --


async def test_11_intent_transitions_on_match(api_client, db_engine, field):
    hs = [await _user(api_client, field) for _ in range(3)]
    i1 = await _intent(api_client, hs[0])
    i2 = await _intent(api_client, hs[1])
    i3 = await _intent(api_client, hs[2])
    latch_a = await _latch(db_engine, [i1["id"], i2["id"]])
    latch_b = await _latch(db_engine, [i2["id"], i3["id"]])
    await _respond(api_client, hs[0], latch_a, "yes")
    await _respond(api_client, hs[1], latch_a, "yes")  # matched
    async with db_engine.connect() as conn:
        statuses = (
            await conn.execute(
                text(
                    "SELECT id, status FROM intents"
                    " WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [uuid_mod.UUID(x) for x in (i1["id"], i2["id"], i3["id"])]},
            )
        ).fetchall()
    by_id = {str(r[0]): r[1] for r in statuses}
    assert by_id[i1["id"]] == "matched"  # 参加Intent=matched(引用#9)
    assert by_id[i2["id"]] == "matched"
    assert by_id[i3["id"]] == "active"  # 競合側の参加Intentは untouched
    assert (await _latch_row(db_engine, latch_b))["status"] == "cancelled"


# -- 試験12: latch_status_events(回答起因とシステム起因) --


async def test_12_status_events_user_and_system(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    i3 = await _intent(api_client, h3)
    latch_a = await _latch(db_engine, [i1["id"], i2["id"]])
    latch_b = await _latch(db_engine, [i2["id"], i3["id"]])
    await _respond(api_client, h1, latch_a, "yes")  # proposed→partial_accept
    await _respond(api_client, h2, latch_a, "yes")  # partial→matched
    events_a = await _events(db_engine, latch_a)
    async with db_engine.connect() as conn:
        u1 = str(
            (
                await conn.execute(
                    text("SELECT user_id FROM intents WHERE id = CAST(:i AS uuid)"),
                    {"i": i1["id"]},
                )
            ).scalar_one()
        )
        u2 = str(
            (
                await conn.execute(
                    text("SELECT user_id FROM intents WHERE id = CAST(:i AS uuid)"),
                    {"i": i2["id"]},
                )
            ).scalar_one()
        )
    assert ("proposed", "partial_accept", u1) in events_a  # 回答起因=回答者
    assert ("partial_accept", "matched", u2) in events_a
    events_b = await _events(db_engine, latch_b)
    assert ("proposed", "cancelled", None) in events_b  # システム起因=NULL


# -- 試験13: Calibration(matched/rejected/グループminペア) --


async def test_13_calibration_records(api_client, db_engine, field):
    # 1対1 matched: 評価行のlatch_scoreをlatches.scoreと一致させる(§2.11第1段)
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    await _pair_eval(db_engine, i1["id"], i2["id"], wa=0.9, wb=0.8, latch_score=0.85)
    latch = await _latch(db_engine, [i1["id"], i2["id"]], score=0.85)
    await _respond(api_client, h1, latch, "yes")
    await _respond(api_client, h2, latch, "yes")  # matched
    cal = await _calibration(db_engine, latch)
    assert cal is not None
    pred = cal["prediction"]
    assert pred["would_a_accept_b"] == 0.9
    assert pred["would_b_accept_a"] == 0.8
    assert pred["MutualScore"] == 0.8
    assert float(pred["L"]) == 0.85
    assert pred["provider"] == "typesafe_jev"
    assert pred["model"] == "jev-1.13.0"
    assert pred["segment"] in ("lexical", "semantic")
    assert cal["proposal_snapshot"]["headcount"] == 2
    assert len(cal["actual_responses"]) == 2
    assert cal["matched"] is True
    # rejected側: matched=False
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 36))
    i4 = await _intent(api_client, h2, start=_future(BASE_HOURS + 36))
    await _pair_eval(db_engine, i3["id"], i4["id"], wa=0.7, wb=0.7, latch_score=0.7)
    latch2 = await _latch(db_engine, [i3["id"], i4["id"]], score=0.7)
    await _respond(api_client, h1, latch2, "no")
    cal2 = await _calibration(db_engine, latch2)
    assert cal2 is not None and cal2["matched"] is False
    assert len(cal2["actual_responses"]) == 1


async def test_13b_group_calibration_uses_min_pair(api_client, db_engine, field):
    """グループのprediction=minペアの値(§2.12・MutualScore最小=ACの0.6)。"""
    hs = [await _user(api_client, field) for _ in range(3)]
    intents = [await _intent(api_client, h) for h in hs]
    a, b, c = (i["id"] for i in intents)
    now = SystemClock().now()
    member_scores = {"seed_id": a, "versions": {a: 1, b: 1, c: 1}}
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO group_candidates
                    (intent_ids, member_scores, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:ms AS jsonb), 'proposed',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(x) for x in (a, b, c)],
                "ms": json.dumps(member_scores),
                "now": now,
            },
        )
        gid = str(res.first()[0])
    await _pair_eval(db_engine, a, b, wa=0.9, wb=0.8)  # mutual 0.8
    await _pair_eval(db_engine, a, c, wa=0.6, wb=0.9)  # mutual 0.6 ← min
    await _pair_eval(db_engine, b, c, wa=0.7, wb=0.7)  # mutual 0.7
    latch = await _latch(db_engine, [a, b, c], gid=gid, score=0.6)
    for h in hs:
        await _respond(api_client, h, latch, "yes")
    cal = await _calibration(db_engine, latch)
    assert cal is not None
    pred = cal["prediction"]
    assert pred["MutualScore"] == 0.6  # minペア(AC)
    assert pred["would_a_accept_b"] == 0.6
    assert pred["would_b_accept_a"] == 0.9
    assert float(pred["L"]) == 0.6  # latches.score(aggregate)


# -- 試験14: segment(lexical/semantic) --


async def test_14_segment_lexical_and_semantic(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    h4 = await _user(api_client, field)
    # lexical: category_secondary一致(「焼肉」/「焼肉」)
    i1 = await _intent(api_client, h1, secondary="焼肉")
    i2 = await _intent(api_client, h2, secondary="焼肉")
    await _pair_eval(db_engine, i1["id"], i2["id"], wa=0.9, wb=0.9, latch_score=0.9)
    latch_lex = await _latch(db_engine, [i1["id"], i2["id"]], score=0.9)
    await _respond(api_client, h1, latch_lex, "yes")
    await _respond(api_client, h2, latch_lex, "yes")
    cal = await _calibration(db_engine, latch_lex)
    assert cal["prediction"]["segment"] == "lexical"
    # semantic: 不一致(「焼肉」/「イタリアン」)
    i3 = await _intent(api_client, h3, secondary="焼肉", start=_future(BASE_HOURS + 48))
    i4 = await _intent(
        api_client, h4, secondary="イタリアン", start=_future(BASE_HOURS + 48)
    )
    await _pair_eval(db_engine, i3["id"], i4["id"], wa=0.9, wb=0.9, latch_score=0.9)
    latch_sem = await _latch(db_engine, [i3["id"], i4["id"]], score=0.9)
    await _respond(api_client, h3, latch_sem, "no")
    cal2 = await _calibration(db_engine, latch_sem)
    assert cal2["prediction"]["segment"] == "semantic"


# -- 試験15: GET一覧 --


async def test_15_list_excludes_candidate_sorts_and_paginates(
    api_client, db_engine, field
):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1, start=_future(BASE_HOURS))
    i2 = await _intent(api_client, h1, start=_future(BASE_HOURS + 6))
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 12))
    # candidate(未提示・nearby的位置づけ)は一覧に出ない(引用#24)
    await _latch(db_engine, [i1["id"], i3["id"]], status="candidate")
    # target_time=max(time_start): early=+6(i1,i2)・late=+12(i2,i3)で昇順検証
    latch_early = await _latch(db_engine, [i1["id"], i2["id"]])
    latch_late = await _latch(db_engine, [i2["id"], i3["id"]])
    await _respond(api_client, h1, latch_early, "yes")  # my_response検証用
    resp = await api_client.get("/v1/latches", headers=h1)
    assert resp.status_code == 200
    body = resp.json()
    ids = [item["id"] for item in body["items"]]
    assert ids.count(latch_early) == 1
    assert ids.count(latch_late) == 1  # 2行とも出る(candidateは出ない)
    assert body["items"][0]["id"] == latch_early  # 対象時刻昇順
    assert body["items"][0]["my_response"] == "yes"
    assert body["items"][0]["remaining_responses"] == 1
    assert body["items"][1]["my_response"] is None
    assert "responses" not in body["items"][0]  # 引用#22
    # limit=1で2頁(cursor改頁)
    resp2 = await api_client.get("/v1/latches", headers=h1, params={"limit": 1})
    body2 = resp2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is not None
    resp3 = await api_client.get(
        "/v1/latches",
        headers=h1,
        params={"limit": 1, "cursor": body2["next_cursor"]},
    )
    body3 = resp3.json()
    assert body3["items"][0]["id"] == latch_late
    assert body3["next_cursor"] is None


# -- 試験16: GET詳細(解放情報) --


async def test_16_detail_release_after_match(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    outsider = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    # proposed: participants等は出ない(成立前の情報最小化・design §2.8)
    resp = await api_client.get(f"/v1/latches/{latch}", headers=h1)
    assert resp.status_code == 200
    latch_body = resp.json()["latch"]
    assert latch_body["participants"] is None
    assert latch_body["time_summary"] is None
    assert latch_body["area_name"] is None
    assert latch_body["proposal"]["match_level"] == "medium"
    # matched: 解放(表示名・time_summaryのJST書式・area_name=実geo)
    await _respond(api_client, h1, latch, "yes")
    await _respond(api_client, h2, latch, "yes")
    resp = await api_client.get(f"/v1/latches/{latch}", headers=h1)
    latch_body = resp.json()["latch"]
    names = sorted(p["display_name"] for p in latch_body["participants"])
    assert names == ["m3ws1", "m3ws1"]  # fixtureのdisplay_name
    assert all(p["profile"] == {} for p in latch_body["participants"])
    datetime.strptime(latch_body["time_summary"], "%Y-%m-%d %H:%M")
    assert latch_body["area_name"] is not None  # 実geofeaturesで天文館周辺
    # 参加者以外は403
    resp = await api_client.get(f"/v1/latches/{latch}", headers=outsider)
    assert resp.status_code == 403
    resp = await api_client.get(f"/v1/latches/{uuid_mod.uuid4()}", headers=h1)
    assert resp.status_code == 404


# -- 試験17: 削除経路(stage1拡張・close_latches_on_delete直呼び・§9-6) --


async def test_17_deletion_closes_and_restores(api_client, db_engine, field):
    from latch.intents import deletion as deletion_mod

    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    # (a) 開いているlatchesのクローズ
    latch_open = await _latch(db_engine, [i1["id"], i2["id"]])
    del1 = await api_client.delete(f"/v1/intents/{i1['id']}", headers=h1)
    assert del1.status_code == 204
    async with db_engine.begin() as conn:
        await deletion_mod.close_latches_on_delete(
            conn, uuid_mod.UUID(i1["id"]), SystemClock().now()
        )
    assert (await _latch_row(db_engine, latch_open))["status"] == "cancelled"
    assert ("proposed", "cancelled", None) in await _events(db_engine, latch_open)
    # (b) matched解散+残Intent復帰(active側)
    # Intent作成のtime.start上限は7日(168h)。BASE_HOURS=120のため
    # 追加オフセットは+48hまでしか置けない(試験13/14と同窓)
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 40))
    i4 = await _intent(api_client, h2, start=_future(BASE_HOURS + 40))
    latch_m = await _latch(db_engine, [i3["id"], i4["id"]])
    await _respond(api_client, h1, latch_m, "yes")
    await _respond(api_client, h2, latch_m, "yes")  # matched
    # matched IntentはDELETE APIで削除不可(allowed_from=draft/active/pausedの
    # M1実装)のため、削除Event相当の状態をDBで整えてから関数を直接呼ぶ(§9-6の
    # 「実HTTP DELETEの後」は(b)(c)では実行不可 — 報告書記録)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE intents SET status = 'cancelled' WHERE id = CAST(:i AS uuid)"),
            {"i": i3["id"]},
        )
    async with db_engine.begin() as conn:
        await deletion_mod.close_latches_on_delete(
            conn, uuid_mod.UUID(i3["id"]), SystemClock().now()
        )
    assert (await _latch_row(db_engine, latch_m))["status"] == "cancelled"
    assert ("matched", "cancelled", None) in await _events(db_engine, latch_m)
    async with db_engine.connect() as conn:
        restored = (
            await conn.execute(
                text("SELECT status FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": i4["id"]},
            )
        ).scalar_one()
    assert restored == "active"  # 残Intent復帰(expires_at未経過・引用#9)
    # (c) expires_at経過側はexpired復帰
    i5 = await _intent(api_client, h1, start=_future(BASE_HOURS + 44))
    i6 = await _intent(api_client, h2, start=_future(BASE_HOURS + 44))
    latch_e = await _latch(db_engine, [i5["id"], i6["id"]])
    await _respond(api_client, h1, latch_e, "yes")
    await _respond(api_client, h2, latch_e, "yes")  # matched
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET expires_at = CAST(:d AS timestamptz)"
                " WHERE id = CAST(:i AS uuid)"
            ),
            {"d": SystemClock().now() - timedelta(hours=1), "i": i6["id"]},
        )
    # matched削除不可のため(b)と同様にDBで状態を整えてから直接呼ぶ
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE intents SET status = 'cancelled' WHERE id = CAST(:i AS uuid)"),
            {"i": i5["id"]},
        )
    async with db_engine.begin() as conn:
        await deletion_mod.close_latches_on_delete(
            conn, uuid_mod.UUID(i5["id"]), SystemClock().now()
        )
    async with db_engine.connect() as conn:
        restored2 = (
            await conn.execute(
                text("SELECT status FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": i6["id"]},
            )
        ).scalar_one()
    assert restored2 == "expired"  # 経過済み復帰(引用#9・#20)
