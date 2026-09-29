"""Layer 3 Cheap Judge・reevalガード・配線のintegration試験(M2 ws-4 design §4.2)。

実DB(compose常設・pgvector)・実Redis(compose常設)。Workerプロセスは起動
しない(直接関数呼び出し・FakeClock注入・Stage1直構築 — ws-3流儀)。
対抗策(design §4.2): category_primary=ws4cheap で他試験由来の残存Intentと
構造的に交差しない(Layer 1完全一致条件)。teardownはsubjectプレフィックス
単位のFK順削除(match_candidates→match_events→intents→users)+Redis prefix掃除。
soft_constraints/ng_unverifiable はAPI入力から投入し、mapping.pyの
{text, downgraded_from_ng} 形式で保存されたものをLayer 3が読む通しを検証。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import timedelta

import fakeredis.aioredis
import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.events import IncomingEvent
from latch.settings import Settings
from latch.worker.cost.reeval import ReevalGuard
from latch.worker.matching import run_candidate_retrieval
from latch.worker.stage1 import Stage1

pytestmark = pytest.mark.integration

# テスト専用カテゴリ(design §4.2対抗策1・試験ファイル内定数)
CATEGORY = "ws4cheap"


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)  # 全候補同一(sim=1.0固定でLayer 3要素を分離)


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
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除(対抗策2)。"""
    prefix = f"m2ws4-{uuid_mod.uuid4().hex[:8]}-"
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


@pytest.fixture
async def redis_sweep(redis_client):
    """試験ごとのRedis接頭辞。teardownでSCAN+DELETE(対抗策4)。"""
    prefix = f"ws4-{uuid_mod.uuid4().hex[:8]}-"
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
            "display_name": "m2ws4",
            "birth_date": birth_date,
            "profile": {},
        },
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured(
    *,
    start: str | None = None,
    budget: int | None = None,
    soft: list[str] | None = None,
    ng: list[str] | None = None,
) -> dict:
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": None},
        "alcohol_involved": False,
        "time": {"start": start or _future(3), "end": None},
        "location": {"name": "天文館"},
    }
    if budget is not None:
        d["budget"] = {"max": budget}
    if soft is not None:
        d["soft_constraints"] = soft
    if ng is not None:
        d["ng_unverifiable"] = ng
    return d


def _payload(structured: dict) -> dict:
    return {
        "raw_text": "分類用テキスト",
        "status": "active",
        "structured_intent": structured,
    }


async def _intent(api_client, db_engine, headers, structured) -> dict:
    """active Intentを作りembeddingを直接挿入(design §4.2のfixture方針)。"""
    resp = await api_client.post(
        "/v1/intents", headers=headers, json=_payload(structured)
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:vec AS vector),"
                " embedding_model = 'fixture'"
                " WHERE id = CAST(:iid AS uuid)"
            ),
            {"vec": E1, "iid": intent["id"]},
        )
    return intent


async def _run(db_engine, clock, origin_intent_id: str):
    async with db_engine.begin() as conn:
        return await run_candidate_retrieval(
            conn, clock, uuid_mod.UUID(origin_intent_id)
        )


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


def _peer_scores(outcome) -> dict[str, float]:
    """Outcomeのtopkcから(候補id → cheap_score)の対応。"""
    out: dict[str, float] = {}
    for s in outcome.topkc:
        out[str(s.intent_id)] = s.cheap_score
    return out


# -- §4.2 試験1〜5 --


async def test_1_cheap_judge_score_recorded(api_client, db_engine, field):
    """cheap_judge_scoreの実DB記録(design §4.2-1)。

    sim=1.0(同一ベクトル)・時間Δ0・起点budget=3000×対象NULL(中立0.5)・
    語彙「焼肉」vs「焼肉好き」(1/3)→ rule=(1.0+0.5)/2=0.75。
    ng_unverifiable(降格)は語彙に入らない(Review Focus 1)。
    """
    clock = _clock()
    same_start = _future(3)  # 起点と対象で同一文字列=Δ0ピン
    ha = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=same_start, budget=3000, soft=["焼肉"], ng=["個室"]),
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client,
        db_engine,
        hb,
        _structured(start=same_start, soft=["焼肉好き"], ng=["静か"]),
    )

    outcome = await _run(db_engine, clock, a["id"])
    assert outcome.skip_reason is None and len(outcome.pairs) == 1
    expected = 0.5 * 1.0 + 0.3 * 0.75 + 0.2 * (1 / 3)
    assert outcome.topkc[0].cheap_score == pytest.approx(expected, abs=1e-9)

    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT retrieval_score, cheap_judge_score, status,"
                    " intent_a_version, intent_b_version"
                    " FROM match_candidates"
                    " WHERE intent_a_id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                    " AND intent_b_id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                ),
                {"a": a["id"], "b": b["id"]},
            )
        ).first()
    assert row is not None
    assert float(row[0]) == pytest.approx(1.0)  # retrieval_score=cosine
    assert float(row[1]) == pytest.approx(expected, abs=1e-9)
    assert row[2] == "pending"  # Layer 1〜3時点(05 §2)
    assert row[3] == 1 and row[4] == 1  # バージョン組はSELECT時点


async def test_2_kc_truncation_deterministic(api_client, db_engine, field):
    """K_c=20切り詰めの決定性(design §4.2-2・10 §4.6)。

    対象25件を完全同点(同一embedding・同一start・同予算・同語彙)で配置。
    全25件にcheap_judge_score記録・topkcは20件・同点はintent_id昇順・
    同一入力2回実行で同一結果。
    """
    clock = _clock()
    same_start = _future(3)
    ho = await _user(api_client, field)
    origin = await _intent(
        api_client,
        db_engine,
        ho,
        _structured(start=same_start, soft=["静かな店"]),
    )
    creation_ids: list[str] = []
    for _ in range(25):
        h = await _user(api_client, field)
        t = await _intent(
            api_client,
            db_engine,
            h,
            _structured(start=same_start, soft=["静かな店"]),
        )
        creation_ids.append(t["id"])

    outcome = await _run(db_engine, clock, origin["id"])
    assert outcome.layer1_pass_count == 25
    assert len(outcome.pairs) == 25  # Layer 2通過全件を記録(design §2.3)
    assert len(outcome.topkc) == 20  # K_c=20(D-24)
    uuid_sorted = sorted(creation_ids)
    assert [str(s.intent_id) for s in outcome.topkc] == uuid_sorted[:20]

    # DB上も全25件にcheap_judge_scoreが入っている(全件記録)
    async with db_engine.connect() as conn:
        cnt = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM match_candidates"
                    " WHERE cheap_judge_score IS NOT NULL"
                    " AND (intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid))"
                ),
                {"o": origin["id"]},
            )
        ).scalar()
    assert cnt == 25

    # 同一入力2回実行で同一結果(10 §4.6)
    outcome2 = await _run(db_engine, clock, origin["id"])
    assert outcome2.topkc == outcome.topkc  # 順序・score含む完全一致


async def test_3_score_components_contrast(api_client, db_engine, field):
    """スコア構成の実挙動対照(design §4.2-3・§2.2の表の値を実測と照合)。

    sim=1.0固定(E1)で時間・予算・語彙だけを動かす。各対照の期待値:
      max:  時間Δ0(1.0)・予算同額(1.0)・語彙同一(1.0) → 1.0
      t_only(時間のみ遠い):  Δ180(0.0)・1.0・1.0 → 0.7
      b_only(予算のみ遠い):  1.0・差3000(0.0)・1.0 → 0.8
      v_only(語彙のみ不一致): 1.0・1.0・0.0 → 0.8
      mid:  Δ90(≈0.5)・NULL(0.5)・1/3 → ≈0.7167
      min:  Δ180(0.0)・差3000(0.0)・0.0 → 0.5
    時刻はAPI呼び出し時刻基準のため数秒のずれが入り、Δ90系は approx で検証。
    """
    clock = _clock()
    same_start = _future(3)
    far_start = _future(6)  # Δ180分
    mid_start = _future(4.5)  # Δ90分
    ho = await _user(api_client, field)
    origin = await _intent(
        api_client,
        db_engine,
        ho,
        _structured(start=same_start, budget=3000, soft=["焼肉"]),
    )

    async def _cand(start: str, budget: int | None, soft: list[str]) -> str:
        h = await _user(api_client, field)
        it = await _intent(
            api_client,
            db_engine,
            h,
            _structured(start=start, budget=budget, soft=soft),
        )
        return it["id"]

    c_max = await _cand(same_start, 3000, ["焼肉"])
    c_t = await _cand(far_start, 3000, ["焼肉"])
    c_b = await _cand(same_start, 0, ["焼肉"])  # 差3000
    c_v = await _cand(same_start, 3000, ["寿司"])
    c_mid = await _cand(mid_start, None, ["焼肉好き"])
    c_min = await _cand(far_start, 0, ["寿司"])

    outcome = await _run(db_engine, clock, origin["id"])
    scores = _peer_scores(outcome)
    assert len(outcome.topkc) == 6
    assert scores[c_max] == pytest.approx(1.0, abs=1e-9)
    assert scores[c_t] == pytest.approx(0.7, abs=0.01)
    assert scores[c_b] == pytest.approx(0.8, abs=1e-9)
    assert scores[c_v] == pytest.approx(0.8, abs=1e-9)
    assert scores[c_mid] == pytest.approx(0.5 + 0.15 + 0.2 / 3, abs=0.01)
    assert scores[c_min] == pytest.approx(0.5, abs=0.01)
    # 大小関係(定義どおり)
    assert scores[c_max] > scores[c_mid] > scores[c_min]
    # 降順(同点はintent_id昇順 — c_bとc_vは同点0.8)
    top_ids = [str(s.intent_id) for s in outcome.topkc]
    i_b, i_v = top_ids.index(c_b), top_ids.index(c_v)
    if c_b > c_v:
        assert i_b > i_v  # UUID昇順で c_v が先
    else:
        assert i_b < i_v


async def test_4_reeval_guard_real_redis(redis_client, redis_sweep):
    """reevalガード(実Redis・design §4.2-4)。TTL切れはキー消失で再現(§9-7)。"""
    guard = ReevalGuard(redis_client, key_prefix=redis_sweep)
    iid = uuid_mod.uuid4()
    assert await guard.allow(iid) is True  # 初回
    assert await guard.allow(iid) is False  # 30分以内
    ttl = await redis_client.ttl(f"{redis_sweep}reeval:{iid}")
    assert 0 < ttl <= 1800
    await redis_client.delete(f"{redis_sweep}reeval:{iid}")  # TTL切れ相当
    assert await guard.allow(iid) is True


async def test_5_stage1_matching_hook_wiring(api_client, db_engine, field):
    """配線: embedding_completed Event→stage1→matchingフック(design §4.2-5)。

    fakeredisのReevalGuard・実DB・実runnerで match_candidates 行が増えること・
    Event行がprocessedに遷移すること。2回目(version=2)はガードにより行数不変
    のままprocessedで閉じる(06 §5(c))。
    """
    clock = _clock()
    same_start = _future(3)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=same_start, soft=["焼肉"])
    )
    hb = await _user(api_client, field)
    await _intent(
        api_client, db_engine, hb, _structured(start=same_start, soft=["焼肉"])
    )

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        guard = ReevalGuard(fake_redis)

        async def hook(conn, intent_id):
            if not await guard.allow(intent_id):
                return
            await run_candidate_retrieval(conn, clock, intent_id)

        stage1 = Stage1(
            engine=db_engine, clock=clock, settings=Settings(), matching_hook=hook
        )

        def _event(version: int) -> IncomingEvent:
            payload = json.dumps(
                {
                    "event_type": "embedding_completed",
                    "source_intent_id": a["id"],
                    "version": version,
                }
            ).encode()
            return IncomingEvent.from_payload(
                message_id=f"m2ws4-{version}", payload=payload, ack=lambda: None
            )

        # 1回目: 評価が走り1行生成・Event行はprocessed
        result1 = await stage1.intake(_event(1))
        assert result1.kind == "processed"
        async with db_engine.connect() as conn:
            cnt1 = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM match_candidates"
                        " WHERE intent_a_id = CAST(:i AS uuid)"
                        " OR intent_b_id = CAST(:i AS uuid)"
                    ),
                    {"i": a["id"]},
                )
            ).scalar()
            ev1 = (
                await conn.execute(
                    text(
                        "SELECT status FROM match_events"
                        " WHERE event_type = 'embedding_completed'"
                        " AND source_intent_id = CAST(:i AS uuid)"
                        " AND payload->>'version' = '1'"
                    ),
                    {"i": a["id"]},
                )
            ).first()
        assert cnt1 == 1
        assert ev1 is not None and ev1[0] == "processed"

        # 2回目: intents.versionを2へ進めて再投入 → ガードでスキップ・行数不変
        async with db_engine.begin() as conn:
            await conn.execute(
                text("UPDATE intents SET version = 2 WHERE id = CAST(:i AS uuid)"),
                {"i": a["id"]},
            )
        result2 = await stage1.intake(_event(2))
        assert result2.kind == "processed"  # Eventは処理済みで閉じる(06 §5(c))
        async with db_engine.connect() as conn:
            cnt2 = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM match_candidates"
                        " WHERE intent_a_id = CAST(:i AS uuid)"
                        " OR intent_b_id = CAST(:i AS uuid)"
                    ),
                    {"i": a["id"]},
                )
            ).scalar()
        assert cnt2 == 1  # 30分以内の再評価は行われない
    finally:
        await fake_redis.aclose()
