"""Layer 5 LATCH Engineのintegration試験(M2 ws-6 design §4.2)。

実DB(compose常設・0004部分UNIQUE適用後)・実Redis(compose常設)。Worker
プロセスは起動しない(LatchEngine/ReevalRunner直接構築・FakeClock注入)。
対抗策(test_matching_jev.py流儀+ws-6分): (1)時間窓はnow+5日(BASE_HOURS=120)
系へ統一し関数内パート間で重ならないよう+12h等へずらす(75分/D-05系の
試験だけ専用の近い窓を使う) (2)active保存時のtime_end補完(start+3h)に注意
(3)subjectプレフィックス m2ws6- ・teardownは latch_status_events→
notifications→latches→match_candidates→intents→users の順で完全削除
(FK・部分UNIQUEの残存が次回試験の「開いている行あり」判定を壊すため。
latches は intent_ids && (prefix由来intents) で削除) (4)Redisは ws6- prefixの
SCAN+DELETE掃除 (5)jev_resultはmatch_candidatesへ直接UPDATE(would値直書き)
・embeddingはfixture直入れ(ws-5流儀)。

drain順序(試験8)の備考: FakeClock固定ではcreated_atが同一時刻になるため
promoted化の「順序」は直接観測できない。提示順の検証はD-08日次上限を
seedして「提示順の先頭行のみproposed化」されることで先頭特定する
(ORDER BY句自体はunit試験のSQLピンが担保)。
D-07のdefer抑制拒否(prev非NULL+|Δ|<0.05)について: _RECORD_SCOREの退避
構造上、latch_score NULL行の評価は常にprev=NULL(新評価世代=無条件変化あり・
06 §10手順5)となるためM2の現行経路では発生せず(design §2.10-7)、
unit試験(test_latch_engine.py)がprevスタブで骨格を検証済み。本ファイルは
「新世代無条件変化ありによる昇格」と「抑制期間経過による昇格」を検証する。

収集は10試験(実行はスーパーバイザー検証時のtest-ci — STATUS運用ルール1〜3)。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.geo.service import GeoService
from latch.worker.cost import ReevalGuard
from latch.worker.matching import run_candidate_retrieval
from latch.worker.matching.latch_calc import response_deadline
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.reeval import ReevalRunner

pytestmark = pytest.mark.integration

CATEGORY = "meal"  # Literal実在値(分離は時間窓が担う — ws-4対抗策3)
BASE_HOURS = 120
SUBJECT_PREFIX = "m2ws6-"


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)


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


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除(対抗策2・3)。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        # latches(部分UNIQUE残存が次回試験のON CONFLICT判定を壊すため完全削除)
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
    """試験ごとのRedis接頭辞。teardownでSCAN+DELETE(対抗策4)。"""
    prefix = f"ws6-{uuid_mod.uuid4().hex[:8]}-"
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
        json={
            "display_name": "m2ws6",
            "birth_date": birth_date,
            "profile": {},
        },
    )
    assert created.status_code == 201, created.text
    return headers, subject


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured(
    *,
    start: str | None = None,
    expires: str | None = None,
    visibility: str | None = None,
    notification_level: str | None = None,
    secondary: str | None = None,
    budget_max: int | None = None,
) -> dict:
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": secondary},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }
    if expires is not None:
        d["expires_at"] = expires
    if visibility is not None:
        d["visibility"] = visibility
    if notification_level is not None:
        d["notification_level"] = notification_level
    if budget_max is not None:
        d["budget"] = {"max": budget_max}
    return d


def _payload(structured: dict) -> dict:
    return {
        "raw_text": "分類用テキスト",
        "status": "active",
        "structured_intent": structured,
    }


async def _intent(api_client, db_engine, headers, structured) -> dict:
    """active Intentを作りembeddingを直接挿入(ws-5流儀のfixture方針)。"""
    resp = await api_client.post(
        "/v1/intents", headers=headers, json=_payload(structured)
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    await _embed(db_engine, intent["id"])
    return intent


async def _embed(db_engine, intent_id: str) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:vec AS vector),"
                " embedding_model = 'fixture'"
                " WHERE id = CAST(:iid AS uuid)"
            ),
            {"vec": E1, "iid": intent_id},
        )


async def _user_id_of(db_engine, intent_id: str) -> str:
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT user_id FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": intent_id},
            )
        ).first()
    assert row is not None
    return str(row[0])


async def _seed_jev(db_engine, a_id: str, b_id: str, wa: float, wb: float) -> None:
    """match_candidatesへjev_result付きevaluatedを直接投入(would値直書き)。

    status='pending'の行のみ(未評価行)。PATCH後の旧version行はjev_result
    済みのため対象外 — 対象行は常に1行(UNIQUE(a,b,va,vb)の最新世代)。
    """
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
        assert res.rowcount == 1, "jev直接投入対象行なし(retrieval先行の前提違反)"


def _latch_engine(db_engine, clock) -> LatchEngine:
    return LatchEngine(engine=db_engine, clock=clock, geo=GeoService(db_engine))


async def _handle(db_engine, clock, intent_id: str) -> None:
    await _latch_engine(db_engine, clock).handle(uuid_mod.UUID(intent_id))


async def _latch_of(db_engine, a_id: str, b_id: str):
    """latches行を正規化intent_idsで取得(閉じた行も含む最新)。"""
    lo, hi = sorted([a_id, b_id], key=str)
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT id, status, score, proposal, response_deadline"
                    " FROM latches"
                    " WHERE intent_ids = ARRAY[CAST(:a AS uuid),"
                    " CAST(:b AS uuid)]::uuid[]"
                    " ORDER BY created_at DESC,"
                    " (status IN ('candidate','proposed','partial_accept')) DESC"
                    " LIMIT 1"
                ),
                {"a": lo, "b": hi},
            )
        ).first()


async def _events_of(db_engine, latch_id) -> list:
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT from_status, to_status, user_id FROM latch_status_events"
                    " WHERE latch_id = CAST(:l AS uuid)"
                ),
                {"l": str(latch_id)},
            )
        ).all()
    return sorted(rows, key=lambda r: (r[0] or "", r[1]))


async def _notifications_of(db_engine, user_id: str, ntype: str | None = None):
    async with db_engine.connect() as conn:
        sql = "SELECT type, payload FROM notifications WHERE user_id = CAST(:u AS uuid)"
        params: dict = {"u": user_id}
        if ntype is not None:
            sql += " AND type = :t"
            params["t"] = ntype
        return (await conn.execute(text(sql + " ORDER BY created_at"), params)).all()


async def _seed_notifications(db_engine, user_id: str, n: int, now: datetime):
    async with db_engine.begin() as conn:
        for _ in range(n):
            await conn.execute(
                text(
                    "INSERT INTO notifications (user_id, type, payload, created_at)"
                    " VALUES (CAST(:u AS uuid), 'proposal', CAST('{}' AS jsonb),"
                    " CAST(:now AS timestamptz))"
                ),
                {"u": user_id, "now": now},
            )


async def _seed_latch(
    db_engine,
    *,
    a_id: str,
    b_id: str,
    status: str,
    score: float,
    expires: datetime,
    now: datetime,
    responses: list | None = None,
) -> None:
    lo, hi = sorted([a_id, b_id], key=str)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO latches"
                " (intent_ids, proposal, score, status, response_deadline,"
                "  expires_at, created_at, responses)"
                " VALUES (ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[],"
                " CAST('{}' AS jsonb), CAST(:score AS numeric), :status,"
                " CAST(:d AS timestamptz), CAST(:e AS timestamptz),"
                " CAST(:now AS timestamptz), CAST(:r AS jsonb))"
            ),
            {
                "a": lo,
                "b": hi,
                "score": score,
                "status": status,
                "d": now + timedelta(hours=2),
                "e": expires,
                "now": now,
                "r": json.dumps(responses or []),
            },
        )


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


FAR_EXPIRES_H = 144  # min_expires=now+6日(D-05期待値計算を固定するため明示)


# -- §4.2 試験1〜10 --


async def test_1_e2e_proposal_generation(api_client, db_engine, field):
    """E2E提案生成: evaluated行→latches(proposed)・events 2行・notifications 2行。"""
    clock = _clock()
    now = clock.now()
    same_start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(
            start=same_start,
            expires=expires,
            secondary="ランチ",
            visibility="summary_only",
        ),
    )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client,
        db_engine,
        hb,
        _structured(start=same_start, expires=expires, visibility="summary_only"),
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.85)
    await _handle(db_engine, clock, a["id"])

    latch = await _latch_of(db_engine, a["id"], b["id"])
    assert latch is not None, "latches行が生成されていない"
    latch_id, status, score, proposal, deadline = latch
    assert status == "proposed"
    assert float(score) == 0.85  # min(0.9, 0.85) × LATCH_C(=1.0)・丸めなし
    assert proposal["headcount"] == 2
    assert proposal["match_level"] == "medium"  # 0.85(0.80以上0.90未満)
    assert proposal["category_primary"] == CATEGORY
    assert proposal["category_secondary"] == "ランチ"  # 起点側(種Intent)の値
    assert proposal["budget"] is None  # 両方budget_max NULL
    assert proposal["area_name"] is None or isinstance(proposal["area_name"], str)
    target = datetime.fromisoformat(same_start)
    from latch.core.clock import JST

    assert proposal["time_summary"] == target.astimezone(JST).strftime("%Y-%m-%d %H:%M")
    # D-05提示時再計算値(min(max(now+15m, min(now+2h, target-60m)), min_expires))
    expected = response_deadline(now, target, datetime.fromisoformat(expires))
    assert deadline == expected
    # events: NULL→candidate→proposed の2行(user_id=NULL=システム起因)
    assert await _events_of(db_engine, latch_id) == [
        (None, "candidate", None),
        ("candidate", "proposed", None),
    ]
    # notifications: 2名へ type='proposal'・payload={"latch_id"}最小参照
    for uid in (
        await _user_id_of(db_engine, a["id"]),
        await _user_id_of(db_engine, b["id"]),
    ):
        rows = await _notifications_of(db_engine, uid, "proposal")
        assert len(rows) == 1
        assert rows[0][1] == {"latch_id": str(latch_id)}


async def test_2_visibility_branch(api_client, db_engine, field):
    """visibility生成分岐: 双方summary_only→全フィールド・片方hidden→最小構造。"""
    clock = _clock()
    start1 = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a1 = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=start1, expires=expires, visibility="summary_only"),
    )
    hb, _ = await _user(api_client, field)
    b1 = await _intent(
        api_client,
        db_engine,
        hb,
        _structured(start=start1, expires=expires, visibility="summary_only"),
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a1["id"]))
    await _seed_jev(db_engine, a1["id"], b1["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a1["id"])
    p1 = (await _latch_of(db_engine, a1["id"], b1["id"]))[3]
    assert set(p1) == {
        "time_summary",
        "area_name",
        "headcount",
        "category_primary",
        "category_secondary",
        "budget",
        "match_level",
    }  # summary_only×2 → 全フィールド

    # パート2(+12h窓): 片方hidden_until_match → headcountとmatch_levelのみ
    start2 = _future(BASE_HOURS + 12)
    hc, _ = await _user(api_client, field)
    a2 = await _intent(
        api_client, db_engine, hc, _structured(start=start2, expires=expires)
    )
    hd, _ = await _user(api_client, field)
    b2 = await _intent(
        api_client,
        db_engine,
        hd,
        _structured(start=start2, expires=expires, visibility="hidden_until_match"),
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a2["id"]))
    await _seed_jev(db_engine, a2["id"], b2["id"], 0.92, 0.92)
    await _handle(db_engine, clock, a2["id"])
    p2 = (await _latch_of(db_engine, a2["id"], b2["id"]))[3]
    assert p2 == {"headcount": 2, "match_level": "high"}  # 0.92→high

    # パート3(+24h窓): muted混在の提案 — proposed遷移は通常どおり・通知は
    # 非muted側のみ1行(muted側0行=上限も消費しない・引用#10)
    start3 = _future(BASE_HOURS + 24)
    he, _ = await _user(api_client, field)
    a3 = await _intent(
        api_client, db_engine, he, _structured(start=start3, expires=expires)
    )
    hf, _ = await _user(api_client, field)
    b3 = await _intent(
        api_client,
        db_engine,
        hf,
        _structured(start=start3, expires=expires, notification_level="muted"),
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a3["id"]))
    await _seed_jev(db_engine, a3["id"], b3["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a3["id"])
    latch3 = await _latch_of(db_engine, a3["id"], b3["id"])
    assert latch3 is not None and latch3[1] == "proposed"  # 遷移は通常どおり
    assert await _notifications_of(
        db_engine, await _user_id_of(db_engine, a3["id"]), "proposal"
    ) == [  # 非muted側のみ1行
        ("proposal", {"latch_id": str(latch3[0])})
    ]
    assert (
        await _notifications_of(db_engine, await _user_id_of(db_engine, b3["id"])) == []
    )  # muted側0行


async def test_3_d08_daily_limit(api_client, db_engine, field):
    """D-08日次上限: 7件目はcandidate保留・nearby存在通知は上限でスキップ。"""
    clock = _clock()
    now = clock.now()
    start1 = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a1 = await _intent(
        api_client, db_engine, ha, _structured(start=start1, expires=expires)
    )
    hb, _ = await _user(api_client, field)
    b1 = await _intent(
        api_client, db_engine, hb, _structured(start=start1, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a1["id"]))
    await _seed_jev(db_engine, a1["id"], b1["id"], 0.9, 0.9)
    # 通知対象1名(a1ユーザー)へ当日6件をseed → 7件目はproposed化されない
    uid_a1 = await _user_id_of(db_engine, a1["id"])
    await _seed_notifications(db_engine, uid_a1, 6, now)
    await _handle(db_engine, clock, a1["id"])
    latch = await _latch_of(db_engine, a1["id"], b1["id"])
    assert latch is not None and latch[1] == "candidate"  # 保留(破棄しない)
    rows = await _notifications_of(db_engine, uid_a1, "proposal")
    assert len(rows) == 6  # 増えない(seedの6件のみ)

    # パート2(+12h窓): nearby存在通知の上限スキップ(存在通知は保留できない)
    start2 = _future(BASE_HOURS + 12)
    hc, _ = await _user(api_client, field)
    a2 = await _intent(
        api_client,
        db_engine,
        hc,
        _structured(start=start2, expires=expires, notification_level="nearby_also"),
    )
    hd, _ = await _user(api_client, field)
    b2 = await _intent(
        api_client, db_engine, hd, _structured(start=start2, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a2["id"]))
    await _seed_jev(db_engine, a2["id"], b2["id"], 0.5, 0.5)  # 閾値未満
    uid_a2 = await _user_id_of(db_engine, a2["id"])
    await _seed_notifications(db_engine, uid_a2, 6, now)  # 当日上限済み
    await _handle(db_engine, clock, a2["id"])
    latch2 = await _latch_of(db_engine, a2["id"], b2["id"])
    assert latch2 is not None and latch2[1] == "candidate"  # 行自体は作る
    assert await _notifications_of(db_engine, uid_a2, "nearby_candidate") == []


async def test_4_d08_concurrent_limit(api_client, db_engine, field):
    """D-08同時上限: 参加Intentの開いているproposed 3件→4件目はcandidate保留。"""
    clock = _clock()
    now = clock.now()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    # aを含む開いているproposedを3件直接INSERT(相手は実在不要のダミーUUID)
    for _ in range(3):
        await _seed_latch(
            db_engine,
            a_id=a["id"],
            b_id=str(uuid_mod.uuid4()),
            status="proposed",
            score=0.9,
            expires=now + timedelta(days=30),
            now=now,
        )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a["id"])
    latch = await _latch_of(db_engine, a["id"], b["id"])
    assert latch is not None and latch[1] == "candidate"  # 4件目は保留
    # eventsはcandidate作成のみ(proposed遷移なし)
    assert await _events_of(db_engine, latch[0]) == [(None, "candidate", None)]


async def test_5_75min_rule(api_client, db_engine, field):
    """75分ルール: 対象時刻now+70分 → candidate→expired・通知ゼロ(専用近い窓)。"""
    clock = _clock()
    now = clock.now()
    start = (now + timedelta(minutes=70)).isoformat()  # 当試験専用の近い窓
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a["id"])
    latch = await _latch_of(db_engine, a["id"], b["id"])
    assert latch is not None and latch[1] == "expired"  # 通知せず破棄
    assert await _events_of(db_engine, latch[0]) == [
        (None, "candidate", None),
        ("candidate", "expired", None),
    ]
    for iid in (a["id"], b["id"]):
        assert (
            await _notifications_of(db_engine, await _user_id_of(db_engine, iid)) == []
        )


async def test_6_nearby_also(api_client, db_engine, field):
    """nearby_also: 閾値未満→candidate保持・存在通知はnearby側のみ・muted側0行。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=start, expires=expires, notification_level="nearby_also"),
    )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client,
        db_engine,
        hb,
        _structured(start=start, expires=expires, notification_level="muted"),
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.5, 0.5)
    await _handle(db_engine, clock, a["id"])
    latch = await _latch_of(db_engine, a["id"], b["id"])
    assert latch is not None
    latch_id, status, score, proposal, _ = latch
    assert status == "candidate"  # proposed遷移しない(引用#9)
    assert float(score) == 0.5  # 実値
    assert proposal == {"headcount": 2, "match_level": "low"}  # 最小構造
    # 存在通知: nearby_also側のみ1行・muted側0行
    rows = await _notifications_of(
        db_engine, await _user_id_of(db_engine, a["id"]), "nearby_candidate"
    )
    assert len(rows) == 1 and rows[0][1] == {"latch_id": str(latch_id)}
    assert (
        await _notifications_of(db_engine, await _user_id_of(db_engine, b["id"])) == []
    )


async def test_7_d07_defer_suppression(api_client, db_engine, field):
    """D-07: 開いている行+deferでも新評価世代(prev=None)は昇格・
    抑制期間経過(deferから24h超)でも昇格する。"""
    clock = _clock()
    now = clock.now()
    start1 = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=start1, expires=expires, notification_level="nearby_also"),
    )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start1, expires=expires)
    )
    # 開いているcandidate行をnearby経路で作り、responsesへdeferを直接注入
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.5, 0.5)
    await _handle(db_engine, clock, a["id"])  # nearby candidate行
    defer = [
        {
            "user_id": await _user_id_of(db_engine, a["id"]),
            "response": "defer",
            "answered_at": (now - timedelta(minutes=10)).isoformat(),
        }
    ]
    latch = await _latch_of(db_engine, a["id"], b["id"])
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET responses = CAST(:r AS jsonb)"
                " WHERE id = CAST(:l AS uuid)"
            ),
            {"r": json.dumps(defer), "l": str(latch[0])},
        )
    # aをPATCH(version↑・embedding再注入)→新評価世代で閾値超過→昇格する
    # (PATCHはstructured_intent必須 — 元の条件を保持して送る)
    patched = await api_client.patch(
        f"/v1/intents/{a['id']}",
        headers=ha,
        json={
            "raw_text": "更新テキスト(ws6 d07)",
            "structured_intent": _structured(
                start=start1,
                expires=expires,
                notification_level="nearby_also",
            ),
        },
    )
    assert patched.status_code == 200, patched.text
    await _embed(db_engine, a["id"])
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.88)
    await _handle(db_engine, clock, a["id"])
    latch2 = await _latch_of(db_engine, a["id"], b["id"])
    assert latch2 is not None
    assert latch2[0] == latch[0]  # 新規行を作らず既存candidate行を昇格
    assert latch2[1] == "proposed"  # prev=None=新世代は無条件変化あり
    assert float(latch2[2]) == 0.88  # 3列更新(score)

    # パート2(+12h窓): 閉じた過去行(expired)のdeferが抑制期間経過なら昇格
    start2 = _future(BASE_HOURS + 12)
    hc, _ = await _user(api_client, field)
    a2 = await _intent(
        api_client, db_engine, hc, _structured(start=start2, expires=expires)
    )
    hd, _ = await _user(api_client, field)
    b2 = await _intent(
        api_client, db_engine, hd, _structured(start=start2, expires=expires)
    )
    defer_old = [
        {
            "user_id": await _user_id_of(db_engine, a2["id"]),
            "response": "defer",
            "answered_at": (now - timedelta(hours=25)).isoformat(),
        }
    ]
    await _seed_latch(
        db_engine,
        a_id=a2["id"],
        b_id=b2["id"],
        status="expired",
        score=0.5,
        expires=now + timedelta(days=5),
        now=now,
        responses=defer_old,
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a2["id"]))
    await _seed_jev(db_engine, a2["id"], b2["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a2["id"])
    latch3 = await _latch_of(db_engine, a2["id"], b2["id"])
    assert latch3 is not None and latch3[1] == "proposed"  # min(24h, 残/2)経過


async def test_8_drain_order(api_client, db_engine, field):
    """drain提示順: 対象時刻昇順・score降順の先頭行のみproposed化する。"""
    clock = _clock()
    now = clock.now()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    # drain対象candidate 3件(直接INSERT・対象時刻は参加Intentのtime_startで制御)。
    # 対象時刻=max(time_start)のためB_iはA(120h)より後ろの窓へ置く(hours+120)。
    # 期待提示順: ①(123h, 0.85)→③(123h, 0.80)→②(124h, 0.95)※同時刻はscore降順
    plan = [
        ("b1", 3.0, 0.85),  # ① 対象時刻最早
        ("b2", 4.0, 0.95),
        ("b3", 3.0, 0.80),
    ]
    intents: dict[str, str] = {}
    for tag, hours, _score in plan:
        h, _ = await _user(api_client, field)
        # embedding不要(drainはintents読取のみ)・窓はBASE+48h系で他試験と分離
        resp = await api_client.post(
            "/v1/intents",
            headers=h,
            json=_payload(
                _structured(
                    start=(now + timedelta(hours=hours + 120)).isoformat(),
                    expires=(now + timedelta(days=6)).isoformat(),
                )
            ),
        )
        assert resp.status_code == 201, resp.text
        intents[tag] = resp.json()["intent"]["id"]
    for tag, _hours, score in plan:
        await _seed_latch(
            db_engine,
            a_id=a["id"],
            b_id=intents[tag],
            status="candidate",
            score=score,
            expires=now + timedelta(days=10),
            now=now,
        )
    # 通知対象aユーザーへ当日5件seed→提示順の先頭のみproposed化(6件目)
    await _seed_notifications(db_engine, await _user_id_of(db_engine, a["id"]), 5, now)
    await _handle(db_engine, clock, a["id"])  # 選択行なし→drainのみ
    l1 = await _latch_of(db_engine, a["id"], intents["b1"])
    l2 = await _latch_of(db_engine, a["id"], intents["b2"])
    l3 = await _latch_of(db_engine, a["id"], intents["b3"])
    assert l1[1] == "proposed"  # 提示順先頭(対象時刻最早・同点score最高)
    assert l2[1] == "candidate"  # 2番目以降は上限で保留
    assert l3[1] == "candidate"


async def test_9_idempotency(api_client, db_engine, field):
    """冪等: handle 2回→latches二重なし・candidateイベント1回・notifications再送なし
    (Review Focus 1・design §5-10のON CONFLICT実証)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.85)
    await _handle(db_engine, clock, a["id"])
    latch = await _latch_of(db_engine, a["id"], b["id"])
    assert latch is not None and latch[1] == "proposed"
    events_before = await _events_of(db_engine, latch[0])
    uid = await _user_id_of(db_engine, a["id"])
    notif_before = await _notifications_of(db_engine, uid, "proposal")
    # 2回目: latch_score IS NULLガードで選択0行・drainもproposed化対象なし
    await _handle(db_engine, clock, a["id"])
    async with db_engine.connect() as conn:
        n_latches = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM latches WHERE intent_ids &&"
                    " ARRAY[CAST(:i AS uuid)]"
                ),
                {"i": a["id"]},
            )
        ).scalar_one()
        n_mc = (
            await conn.execute(
                text(
                    "SELECT COUNT(*), COUNT(prev_latch_score) FROM match_candidates"
                    " WHERE intent_a_id = CAST(:i AS uuid)"
                    " OR intent_b_id = CAST(:i AS uuid)"
                ),
                {"i": a["id"]},
            )
        ).first()
    assert n_latches == 1  # 同一intent_idsで1行(部分UNIQUE+ON CONFLICT実証)
    assert n_mc == (1, 0)  # 行1本・prev退避はNULL(初回のみ・2回目の再計算なし)
    assert await _events_of(db_engine, latch[0]) == events_before  # 追加イベントなし
    assert await _notifications_of(db_engine, uid, "proposal") == notif_before


async def test_10_reeval_runner_path(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """ReevalRunner経路(02#8): catch-up抽出→直接投入→候補生成・ガードで
    30分以内の再実行がスキップされる。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    # 期限がcatch-up窓へ入るよう明示(expires_at<=now+2h・期限切れ前)
    expires = (clock.now() + timedelta(minutes=90)).isoformat()
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )

    calls: list[str] = []

    async def pipeline(intent_id):
        calls.append(str(intent_id))
        async with db_engine.begin() as conn:
            await run_candidate_retrieval(conn, clock, intent_id)

    runner = ReevalRunner(
        engine=db_engine,
        clock=clock,
        guard=ReevalGuard(redis_client, key_prefix=redis_sweep),
        pipeline=pipeline,
        interval_sec=0.01,
        batch_limit=50,
    )
    done = await runner.run_once()
    assert done >= 1  # catch-up対象2件(期限90分後)
    assert set(calls) >= {a["id"], b["id"]}
    async with db_engine.connect() as conn:
        n_mc = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM match_candidates WHERE"
                    " (intent_a_id = CAST(:x AS uuid)"
                    " AND intent_b_id = CAST(:y AS uuid))"
                    " OR (intent_a_id = CAST(:y AS uuid)"
                    " AND intent_b_id = CAST(:x AS uuid))"
                ),
                {"x": a["id"], "y": b["id"]},
            )
        ).scalar_one()
    assert n_mc == 1  # 候補生成の記録(正規化ペアで1行)
    # 2回目(クロック不進行): reevalガードが30分以内を弾く→pipeline不呼出
    calls.clear()
    assert await runner.run_once() == 0
    assert calls == []
