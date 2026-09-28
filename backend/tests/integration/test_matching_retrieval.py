"""02#10・K_v切り詰め決定性・対象外・冪等(M2 ws-3 design §4.2)。

Worker不使用の直接関数呼び出し・FakeClock注入。ベクトルは基底+摂動で
cosine類似度を制御する(高類似=同一ベクトル・低類似=直交ベクトル)。
55対象の配置は1ユーザー1Intent(レート制限はuser_id/subject単位のため
60req/分の上限には触らない — limiter.py §2.4)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.worker.matching import load_origin, run_candidate_retrieval

pytestmark = pytest.mark.integration


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)
E2 = _vec(0.0, 1.0)  # E1と直交(cosine 0)


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


def _structured() -> dict:
    now = SystemClock().now()
    return {
        "category": {"primary": "meal", "secondary": None},
        "alcohol_involved": False,
        "time": {"start": (now + timedelta(hours=3)).isoformat(), "end": None},
        "location": {"name": "天文館"},
    }


def _payload(raw_text: str) -> dict:
    return {
        "raw_text": raw_text,
        "status": "active",
        "structured_intent": _structured(),
    }


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"m2ws3r-{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
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


async def _user(api_client, prefix: str):
    """1ユーザーを登録してAuthorizationヘッダーを返す。"""
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
        json={"display_name": "m2ws3r", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


async def _intent(api_client, db_engine, headers, raw_text: str, vec: str) -> dict:
    """1Intent作成してembeddingを直接挿入する。"""
    resp = await api_client.post(
        "/v1/intents", headers=headers, json=_payload(raw_text)
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    await _set_embedding(db_engine, intent["id"], vec)
    return intent


async def _set_embedding(db_engine, intent_id: str, vec: str | None) -> None:
    sql = (
        "embedding = CAST('" + vec + "' AS vector), embedding_model = 'fixture'"
        if vec is not None
        else "embedding = NULL"
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            text(f"UPDATE intents SET {sql} WHERE id = CAST(:iid AS uuid)"),
            {"iid": intent_id},
        )


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


def _peer_ids(outcome) -> list[str]:
    """Outcomeのpairsから起点以外の側(候補対象)のidを行順に取り出す。"""
    out = []
    for p in outcome.pairs:
        a, b = str(p.intent_a_id), str(p.intent_b_id)
        out.append(b if str(outcome.intent_id) == a else a)
    return out


async def _run(db_engine, clock, origin_intent_id: str):
    async with db_engine.begin() as conn:
        return await run_candidate_retrieval(
            conn, clock, uuid_mod.UUID(origin_intent_id)
        )


# -- §4.2 試験1〜4 --


async def test_1_semantic_pair_generated(api_client, db_engine, field):
    """02#10: 語彙不一致の意味的近接ペア(焼肉/肉系なら何でも)が生成される
    (status='pending'・score記録・正規化a<b・version組記録)。"""
    clock = _clock()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, "焼肉が食べたい", E1)
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, "肉系なら何でもいい", E1)

    # design §5-2: asyncpg の vector 列読み取り型の実確認
    async with db_engine.connect() as conn:
        loaded = await load_origin(conn, clock, uuid_mod.UUID(a["id"]))
    assert loaded.origin is not None
    assert isinstance(loaded.origin.embedding, str)  # 文字列で読める(想定どおり)

    outcome = await _run(db_engine, clock, a["id"])
    assert outcome.skip_reason is None
    assert outcome.layer1_pass_count == 1
    assert len(outcome.pairs) == 1
    pair = outcome.pairs[0]
    ai, bi = uuid_mod.UUID(a["id"]), uuid_mod.UUID(b["id"])
    assert (pair.intent_a_id, pair.intent_b_id) == (min(ai, bi), max(ai, bi))
    assert pair.retrieval_score == pytest.approx(1.0)  # cosine類似度記録

    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT intent_a_version, intent_b_version, status"
                    " FROM match_candidates WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                ),
                {"a": pair.intent_a_id, "b": pair.intent_b_id},
            )
        ).first()
    assert row is not None
    assert row[0] == 1 and row[1] == 1  # 評価時点のversion組(05 §2)
    assert row[2] == "pending"


async def test_2_kv_truncation_deterministic(api_client, db_engine, field):
    """K_v=50切り詰めの決定性(10 §4.6): 同点はintent_id昇順・同一入力2回で
    同一結果・embedding NULL対象は除外され件数不変・非同点は距離上位。"""
    clock = _clock()
    ho = await _user(api_client, field)
    origin = await _intent(api_client, db_engine, ho, "金曜の夜 飲みたい", E1)

    creation_order: list[str] = []  # 対象55件(作成順)
    for _ in range(55):
        h = await _user(api_client, field)
        t = await _intent(api_client, db_engine, h, "一緒に飲みましょう", E1)
        creation_order.append(t["id"])

    # フェーズA: 全員同一点(cosine 1.0完全同点)→ UUID昇順先頭50件
    outcome_a = await _run(db_engine, clock, origin["id"])
    assert outcome_a.layer1_pass_count == 55
    assert len(outcome_a.pairs) == 50
    uuid_sorted = sorted(creation_order)
    assert _peer_ids(outcome_a) == uuid_sorted[:50]  # 同点=UUID昇順(D-24)

    # フェーズB: 1件を embedding NULL へ → 除外されて件数不変(design §4.2-3)
    nulled = creation_order[-1]
    await _set_embedding(db_engine, nulled, None)
    valid = [i for i in creation_order if i != nulled]
    outcome_b = await _run(db_engine, clock, origin["id"])
    assert outcome_b.layer1_pass_count == 54
    assert len(outcome_b.pairs) == 50  # 件数不変
    assert _peer_ids(outcome_b) == sorted(valid)[:50]

    # フェーズC: 同一入力2回実行で同一結果(10 §4.6)
    outcome_c = await _run(db_engine, clock, origin["id"])
    assert _peer_ids(outcome_c) == _peer_ids(outcome_b)
    assert outcome_c.pairs == outcome_b.pairs  # 順序・score含む完全一致

    # フェーズD: 類似度差つき(creation順に eps=(i+1)*0.01)→ 距離上位50件
    for i, iid in enumerate(valid):
        await _set_embedding(db_engine, iid, _vec(1.0, (i + 1) * 0.01))
    outcome_d = await _run(db_engine, clock, origin["id"])
    assert len(outcome_d.pairs) == 50
    assert _peer_ids(outcome_d) == valid[:50]  # eps昇順=類似度降順の先頭50
    # 完全同点の崩れ: フェーズDの選択はUUID順と無相関(creation順で決まる)
    assert set(_peer_ids(outcome_d)) != set(_peer_ids(outcome_b))


async def test_3_origin_noop_cases(api_client, db_engine, field):
    """起点 draft / embedding NULL は no-op(match_candidates に1行も増えない)。"""
    clock = _clock()
    ho = await _user(api_client, field)
    origin = await _intent(api_client, db_engine, ho, "起点", E1)
    # 対照: 1件だけ候補になる通常起点
    h1 = await _user(api_client, field)
    await _intent(api_client, db_engine, h1, "対象", E1)
    outcome = await _run(db_engine, clock, origin["id"])
    assert len(outcome.pairs) == 1

    # draft起点(structured込みで作成しembeddingも入れる → statusによる除外)
    h2 = await _user(api_client, field)
    resp = await api_client.post(
        "/v1/intents",
        headers=h2,
        json={
            "raw_text": "下書き",
            "status": "draft",
            "structured_intent": _structured(),
        },
    )
    assert resp.status_code == 201, resp.text
    draft = resp.json()["intent"]
    await _set_embedding(db_engine, draft["id"], E1)
    out_draft = await _run(db_engine, clock, draft["id"])
    assert out_draft.skip_reason == "origin_not_active"
    assert out_draft.pairs == []

    # embedding NULL 起点(active・embedding未投入)
    h3 = await _user(api_client, field)
    resp3 = await api_client.post(
        "/v1/intents", headers=h3, json=_payload("まだ埋め込みなし")
    )
    assert resp3.status_code == 201, resp3.text
    noemb = resp3.json()["intent"]
    out_null = await _run(db_engine, clock, noemb["id"])
    assert out_null.skip_reason == "origin_embedding_null"
    assert out_null.pairs == []

    async with db_engine.connect() as conn:
        cnt = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM match_candidates"
                    " WHERE intent_a_id = CAST(:d AS uuid)"
                    " OR intent_b_id = CAST(:d AS uuid)"
                    " OR intent_a_id = CAST(:n AS uuid)"
                    " OR intent_b_id = CAST(:n AS uuid)"
                ),
                {"d": draft["id"], "n": noemb["id"]},
            )
        ).scalar()
    assert cnt == 0  # no-op起点のペアは1行も増えない


async def test_4_idempotent_upsert(api_client, db_engine, field):
    """冪等性(10 §4.7): 同一 (a,b,av,bv) 2回実行で1行・scoreはDO UPDATEで
    更新される・status='pending' は維持される(Review Focus 4)。"""
    clock = _clock()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, "起点", E1)
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, "対象", E1)

    await _run(db_engine, clock, a["id"])  # 1回目(score ≈ 1.0)
    # 対象のembeddingを直交ベクトルへ → 2回目のscoreは ≈ 0.0 に変わる
    await _set_embedding(db_engine, b["id"], E2)
    await _run(db_engine, clock, a["id"])  # 2回目

    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT retrieval_score, status, created_at, updated_at"
                    " FROM match_candidates"
                    " WHERE (intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid))"
                    " OR (intent_a_id = CAST(:b AS uuid)"
                    " AND intent_b_id = CAST(:a AS uuid))"
                ),
                {"a": a["id"], "b": b["id"]},
            )
        ).all()
    assert len(rows) == 1  # 二重生成なし(UNIQUE+UPSERT — 10 §4.7)
    score, status, created_at, updated_at = rows[0]
    assert float(score) == pytest.approx(0.0, abs=1e-6)  # 更新されている
    assert status == "pending"  # DO UPDATEはstatusを壊さない
    assert created_at < updated_at  # 2回目でupdated_atが進んだ
