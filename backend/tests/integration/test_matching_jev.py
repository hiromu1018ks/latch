"""Layer 4 Jevのintegration試験(M2 ws-5 design §4.2)。

実DB(compose常設・pgvector)・実Redis(compose常設)。Workerプロセスは起動
しない(JevWorker直接構築・FakeClock注入・スタブGateway — ws-4流儀)。
対抗策4項目(design §4.2): (1)テストIntentの時間窓をnow+5日(BASE_HOURS=120)
へ統一し他試験由来の残存IntentとLayer 1の狭義時間交差で構造的に交差しない
(2)subjectプレフィックス単位のFK順teardown(match_candidates→match_events
→intents→users) (3)カテゴリはLiteral値を使い分離には使わない (4)Redisは
試験ごとのkey_prefixでSCAN+DELETE掃除。
JevはStubLLM(System One envelope・決定的固定値)で決定的に検証する。収集は
8試験(design §4.2-9の既存全数グリーン維持はスーパーバイザー検証時のtest-ciで
確認するため本ファイルには試験を置かない)。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.events import IncomingEvent
from latch.llm.errors import LLMTimeoutError
from latch.llm.gateway import LLMGateway
from latch.llm.providers import JevProvider
from latch.llm.stub import StubLLM
from latch.settings import Settings
from latch.worker.cost import JevCostGuard, JevCostStore
from latch.worker.jev import JevWorker
from latch.worker.matching import run_candidate_retrieval
from latch.worker.stage1 import Stage1

pytestmark = pytest.mark.integration

CATEGORY = "meal"  # Literal実在値(分離は時間窓が担う — ws-4対抗策3)
BASE_HOURS = 120

SUBJECT_PREFIX = "m2ws5-"


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)  # 全候補同一(同点を構造的に作る)


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
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
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
    prefix = f"ws5-{uuid_mod.uuid4().hex[:8]}-"
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
        json={"display_name": "m2ws5", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured(*, start: str | None = None, soft: list[str] | None = None) -> dict:
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": None},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }
    if soft is not None:
        d["soft_constraints"] = soft
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


async def _run_retrieval(db_engine, clock, origin_intent_id: str):
    async with db_engine.begin() as conn:
        return await run_candidate_retrieval(
            conn, clock, uuid_mod.UUID(origin_intent_id)
        )


class _CountingStub(StubLLM):
    """judge呼び出しを数えるスタブ(API呼び出し0回の検査用)。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.judge_calls = 0

    async def judge(self, intent_a, intent_b):
        self.judge_calls += 1
        return await super().judge(intent_a, intent_b)


class _ThrowingJev(JevProvider):
    """指定例外を投げるスタブ(双障害の再現)。"""

    name = "throwing"

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def judge(self, intent_a, intent_b) -> dict:
        raise self._exc


class _CountingGuard:
    """request_executionの呼び出しを数えるラップ(guard不呼出の検査用)。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0

    async def request_execution(self, intent_id, user_id):
        self.calls += 1
        return await self._inner.request_execution(intent_id, user_id)


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


def _stub_gateway(stub: StubLLM) -> LLMGateway:
    """stub modeと同一構成のGateway(build_worker_gatewayを通さない — §4.2)。"""
    return LLMGateway(
        clock=_clock(), parser=stub, embedding=stub, jev=stub, jev_fallback=stub
    )


def _jev_worker(db_engine, clock, gateway, guard, cost_store) -> JevWorker:
    return JevWorker(
        engine=db_engine,
        clock=clock,
        gateway=gateway,
        guard=guard,
        cost_store=cost_store,
    )


def _stores(redis_client, redis_sweep: str, clock) -> tuple[JevCostGuard, JevCostStore]:
    store = JevCostStore(redis_client, key_prefix=redis_sweep)
    return JevCostGuard(store=store, clock=clock), store


async def _candidates(db_engine, origin_id: str) -> list:
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT status, skip_reason, jev_result FROM match_candidates"
                    " WHERE intent_a_id = CAST(:i AS uuid)"
                    " OR intent_b_id = CAST(:i AS uuid)"
                    " ORDER BY intent_a_id, intent_b_id"
                ),
                {"i": origin_id},
            )
        ).all()


# -- §4.2 試験1〜8 --


async def test_1_evaluate_records_jev_result(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """Layer 4実行でjev_resultが記録される(design §4.2-1)。"""
    clock = _clock()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured())
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured())
    await _run_retrieval(db_engine, clock, a["id"])
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = _jev_worker(db_engine, clock, _stub_gateway(StubLLM()), guard, store)
    await worker.handle(uuid_mod.UUID(a["id"]))
    rows = await _candidates(db_engine, a["id"])
    assert len(rows) == 1
    status, skip_reason, jev_result = rows[0]
    assert status == "evaluated"
    assert skip_reason is None
    assert jev_result["provider"] == "typesafe_jev"
    assert jev_result["model"] == "jev-1.13.0"  # stub固定値
    assert jev_result["would_a_accept_b"] == 0.5
    assert jev_result["would_b_accept_a"] == 0.5
    axis = jev_result["jev_5axis"]
    assert axis["purpose_fit"] == {"value": 0.5, "confidence": 0.5}  # 2.0/4
    assert axis["latent_yes"] == {"value": 0.5, "confidence": None}


async def test_2_kj8_truncation_deterministic(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """K_j=8切り詰めが決定的(10件→8件・同点tie-break=相手intent_id昇順)。"""
    clock = _clock()
    same_start = _future(BASE_HOURS)
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=same_start))
    peer_ids: list[str] = []
    for _ in range(10):
        hb = await _user(api_client, field)
        b = await _intent(api_client, db_engine, hb, _structured(start=same_start))
        peer_ids.append(b["id"])
    await _run_retrieval(db_engine, clock, a["id"])
    guard, store = _stores(redis_client, redis_sweep, clock)
    stub = _CountingStub()
    worker = _jev_worker(db_engine, clock, _stub_gateway(stub), guard, store)
    await worker.handle(uuid_mod.UUID(a["id"]))
    async with db_engine.connect() as conn:
        evaluated = (
            (
                await conn.execute(
                    text(
                        "SELECT intent_b_id FROM match_candidates"
                        " WHERE intent_a_id = CAST(:i AS uuid)"
                        " AND status = 'evaluated'"
                        " ORDER BY intent_b_id ASC LIMIT 20"
                    ),
                    {"i": a["id"]},
                )
            )
            .scalars()
            .all()
        )
        pending = (
            (
                await conn.execute(
                    text(
                        "SELECT intent_b_id FROM match_candidates"
                        " WHERE intent_a_id = CAST(:i AS uuid)"
                        " AND status = 'pending'"
                    ),
                    {"i": a["id"]},
                )
            )
            .scalars()
            .all()
        )
    assert len(evaluated) == 8
    assert len(pending) == 2
    # 同点tie-breakの決定性: 評価された8件の集合=相手intent_id昇順の先頭8件
    # (実行順序ではなく集合で検証 — 10 §4.6のJev部分)
    expected = sorted(peer_ids)[:8]
    assert sorted(str(u) for u in evaluated) == expected
    # 同一入力2回handleで同一結果(jev_result値の決定性)
    before = await _candidates(db_engine, a["id"])
    await worker.handle(uuid_mod.UUID(a["id"]))
    after = await _candidates(db_engine, a["id"])
    assert before == after
    assert stub.judge_calls == 8  # 再実行では追加API呼び出しなし


async def test_3_generation_skip_is_idempotent(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """FR-07: 同一評価世代の再実行はAPI・guard不呼出・行不変。"""
    clock = _clock()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured())
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured())
    await _run_retrieval(db_engine, clock, a["id"])
    guard_store = JevCostStore(redis_client, key_prefix=redis_sweep)
    guard = _CountingGuard(JevCostGuard(store=guard_store, clock=clock))
    stub = _CountingStub()
    worker = _jev_worker(db_engine, clock, _stub_gateway(stub), guard, guard_store)
    await worker.handle(uuid_mod.UUID(a["id"]))
    assert guard.calls == 1
    assert stub.judge_calls == 1
    before = await _candidates(db_engine, a["id"])
    # 再実行: jev_result既存在→選択0行→API・guard不呼出
    await worker.handle(uuid_mod.UUID(a["id"]))
    assert stub.judge_calls == 1  # API呼び出し0回
    assert guard.calls == 1  # request_execution不呼出
    assert await _candidates(db_engine, a["id"]) == before  # updated_at不変


async def test_4_h_recheck_closes_evaluated_pair(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """H再検証で不成立になったevaluated行のみclosed・jev_resultは保持。"""
    clock = _clock()
    past = _future(-400)  # 時間窓外(Layer 1不成立にする)
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured())
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured())
    hc = await _user(api_client, field)
    c = await _intent(api_client, db_engine, hc, _structured())  # pending対照
    await _run_retrieval(db_engine, clock, a["id"])
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = _jev_worker(db_engine, clock, _stub_gateway(StubLLM()), guard, store)
    await worker.handle(uuid_mod.UUID(a["id"]))
    rows = {str(r[0]): r for r in await _candidates(db_engine, a["id"])}
    assert all(r[0] == "evaluated" for r in rows.values()), rows
    # 相手bを時間窓外へUPDATE(version不変)→起点handle再実行→H再検証でclose
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET time_start = CAST(:ts AS timestamptz)"
                " WHERE id = CAST(:i AS uuid)"
            ),
            {"ts": past, "i": b["id"]},
        )
    await worker.handle(uuid_mod.UUID(a["id"]))
    async with db_engine.connect() as conn:
        out = (
            await conn.execute(
                text(
                    "SELECT intent_b_id, status, jev_result"
                    " FROM match_candidates"
                    " WHERE intent_a_id = CAST(:i AS uuid)"
                ),
                {"i": a["id"]},
            )
        ).all()
    by_peer = {str(r[0]): r for r in out}
    assert by_peer[b["id"]][1] == "closed"  # evaluated行のみclose
    assert by_peer[b["id"]][2] is not None  # jev_resultは保持(値不変)
    assert by_peer[c["id"]][1] == "evaluated"  # H成立の行は閉じない


async def test_5_guard_deny_records_reason(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """Guard deny: skipped/intent_daily・API呼び出し0回・denyもカウンタ増加。"""
    clock = _clock()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured())
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured())
    await _run_retrieval(db_engine, clock, a["id"])
    guard_store = JevCostStore(redis_client, key_prefix=redis_sweep)
    day = clock.jst_date().strftime("%Y%m%d")
    # intent別カウンタを40へseed(INCR後41>40でdenyになる)
    for _ in range(40):
        await guard_store.incr_intent(a["id"], day)
    guard = JevCostGuard(store=guard_store, clock=clock)
    stub = _CountingStub()
    worker = _jev_worker(db_engine, clock, _stub_gateway(stub), guard, guard_store)
    await worker.handle(uuid_mod.UUID(a["id"]))
    assert stub.judge_calls == 0  # API呼び出し0回
    rows = await _candidates(db_engine, a["id"])
    assert rows[0][0] == "skipped" and rows[0][1] == "intent_daily"
    assert rows[0][2] is None  # jev_resultはNULLのまま
    count = await redis_client.get(f"{redis_sweep}jev:intent:{a['id']}:{day}")
    assert int(count) == 41  # seed40→41 = INCR先行(denyも消費)


async def test_6_reselection_rules(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """再選択規則: llm_failure=即時・intent_daily=JST翌日以降。"""
    # -- llm_failureのskipped行は直後のhandleで評価される --
    clock = _clock()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured())
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured())
    await _run_retrieval(db_engine, clock, a["id"])
    guard, store = _stores(redis_client, redis_sweep, clock)
    failing = LLMGateway(
        clock=clock,
        parser=StubLLM(),
        embedding=StubLLM(),
        jev=_ThrowingJev(LLMTimeoutError("t")),
        jev_fallback=StubLLM(fail_jev=True),
    )
    worker = _jev_worker(db_engine, clock, failing, guard, store)
    await worker.handle(uuid_mod.UUID(a["id"]))
    rows = await _candidates(db_engine, a["id"])
    assert rows[0][0] == "skipped" and rows[0][1] == "llm_failure"
    # 直後のhandle(正常スタブ)で再選択・評価される
    worker2 = _jev_worker(db_engine, clock, _stub_gateway(StubLLM()), guard, store)
    await worker2.handle(uuid_mod.UUID(a["id"]))
    rows = await _candidates(db_engine, a["id"])
    assert rows[0][0] == "evaluated"
    # -- intent_dailyのskipped行は同日は不可・翌日のみ --
    clock2 = _clock()
    ha2 = await _user(api_client, field)
    a2 = await _intent(api_client, db_engine, ha2, _structured())
    hb2 = await _user(api_client, field)
    b2 = await _intent(api_client, db_engine, hb2, _structured())
    await _run_retrieval(db_engine, clock2, a2["id"])
    guard_store2 = JevCostStore(redis_client, key_prefix=redis_sweep)
    day2 = clock2.jst_date().strftime("%Y%m%d")
    for _ in range(40):
        await guard_store2.incr_intent(a2["id"], day2)
    guard2 = JevCostGuard(store=guard_store2, clock=clock2)
    worker3 = _jev_worker(
        db_engine, clock2, _stub_gateway(StubLLM()), guard2, guard_store2
    )
    await worker3.handle(uuid_mod.UUID(a2["id"]))
    rows2 = await _candidates(db_engine, a2["id"])
    assert rows2[0][0] == "skipped" and rows2[0][1] == "intent_daily"
    # JST翌日へ進める(同日では再選択されない)
    clock2.advance(timedelta(hours=14))
    await worker3.handle(uuid_mod.UUID(a2["id"]))
    rows2 = await _candidates(db_engine, a2["id"])
    assert rows2[0][0] == "evaluated"  # updated_at < jst_day_start分岐


async def test_7_record_execution_breakdown(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """計上内訳: 成功=typesafe_jev・双障害=fallback_llm(最後に呼んだ経路)。"""
    clock = _clock()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured())
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured())
    await _run_retrieval(db_engine, clock, a["id"])
    guard, store = _stores(redis_client, redis_sweep, clock)
    day = clock.jst_date().strftime("%Y%m%d")
    worker = _jev_worker(db_engine, clock, _stub_gateway(StubLLM()), guard, store)
    await worker.handle(uuid_mod.UUID(a["id"]))
    exec_key = f"{redis_sweep}jev:exec:{day}:typesafe_jev"
    assert int(await redis_client.get(exec_key)) == 1
    # 月次の内訳キーは存在しない(内訳は日次のみ)
    cursor, keys = await redis_client.scan(
        cursor=0, match=f"{redis_sweep}jev:exec:*", count=100
    )
    assert all(":" not in k.rsplit(":", 1)[1] for k in keys)
    # -- 双障害: 第一候補LLMTimeoutError + フォールバックfail_jev --
    clock3 = _clock()
    ha3 = await _user(api_client, field)
    a3 = await _intent(api_client, db_engine, ha3, _structured())
    hb3 = await _user(api_client, field)
    b3 = await _intent(api_client, db_engine, hb3, _structured())
    await _run_retrieval(db_engine, clock3, a3["id"])
    guard3, store3 = _stores(redis_client, redis_sweep, clock3)
    day3 = clock3.jst_date().strftime("%Y%m%d")
    double_fail = LLMGateway(
        clock=clock3,
        parser=StubLLM(),
        embedding=StubLLM(),
        jev=_ThrowingJev(LLMTimeoutError("t")),
        jev_fallback=StubLLM(fail_jev=True),
    )
    worker3 = _jev_worker(db_engine, clock3, double_fail, guard3, store3)
    await worker3.handle(uuid_mod.UUID(a3["id"]))
    fb_key = f"{redis_sweep}jev:exec:{day3}:fallback_llm"
    assert int(await redis_client.get(fb_key)) == 1


async def test_8_stage1_wiring_to_jev(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """stage1.intake(embedding_completed)→_kick_jev相当=JevWorker.handle直接呼び出し。"""
    clock = _clock()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured())
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured())
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = _jev_worker(db_engine, clock, _stub_gateway(StubLLM()), guard, store)

    async def hook(conn, intent_id):
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
            message_id=f"m2ws5-{version}", payload=payload, ack=lambda: None
        )

    result = await stage1.intake(_event(1))
    assert result.kind == "processed"  # match_candidates生成(processed)
    # _kick_jev相当: JevWorker.handleの直接呼び出し(workerプロセス起動不要)
    await worker.handle(uuid_mod.UUID(a["id"]))
    rows = await _candidates(db_engine, a["id"])
    assert len(rows) == 1 and rows[0][0] == "evaluated"  # 評価行の存在確認
