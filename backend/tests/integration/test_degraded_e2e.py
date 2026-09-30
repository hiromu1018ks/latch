"""縮退運転のE2E試験(M2 ws-8 design §2.4・10 §4.5)。

実DB(compose常設)・実Redis。Workerプロセスは立てない(試験1〜7はJevWorker
直接構築+注入Gateway・FakeClock。試験8のみテストプロセス内Worker+API実HTTP)。
対抗策(ws-5流儀): (1)時間窓をnow+5日(BASE_HOURS=120)へ統一(2)subject prefix
単位のFK順teardown(match_candidates→group_candidates/latches→match_events→
intents→users)(3)Redisは試験ごとのkey_prefixでSCAN+DELETE(4)FakeClockで
breaker 60秒・debounce窓を進行(実時間待ちなし)。
ci環境=スタブLLMで決定的(10 §1)。
"""

import asyncio
import copy
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.geo.service import GeoService
from latch.llm.breaker import CircuitBreaker
from latch.llm.errors import (
    LLMOverloadedError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from latch.llm.gateway import LLMGateway
from latch.llm.stub import DEFAULT_JEV_RESPONSE, StubLLM
from latch.worker.cost import JevCostGuard, JevCostStore
from latch.worker.jev import JevWorker
from latch.worker.matching import run_candidate_retrieval
from latch.worker.matching.latch_engine import LatchEngine

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120
SUBJECT_PREFIX = "m2ws8-"


def _vec(*components: float) -> str:
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)


def _high_prob_envelope() -> dict:
    """MutualScore=0.95≥0.80でlatches提案が成立するstub応答(試験7・8用)。"""
    env = copy.deepcopy(DEFAULT_JEV_RESPONSE)
    for key in ("would_a_accept_b", "would_b_accept_a"):
        env["answers"][key] = {"type": "noul", "noul": 0.95}
    return env


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
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
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
                "DELETE FROM latch_status_events WHERE latch_id IN"
                " (SELECT id FROM latches WHERE intent_ids && (SELECT array_agg(id)"
                " FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))::uuid[])"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM latches WHERE intent_ids && (SELECT array_agg(id)"
                " FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))::uuid[]"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM group_candidates WHERE intent_ids && (SELECT"
                " array_agg(id) FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))::uuid[]"
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
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)


@pytest.fixture
async def redis_sweep(redis_client):
    prefix = f"ws8-{uuid_mod.uuid4().hex[:8]}-"
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
        json={"display_name": "m2ws8", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured(*, start: str | None = None) -> dict:
    return {
        "category": {"primary": CATEGORY, "secondary": None},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }


def _payload(structured: dict) -> dict:
    return {
        "raw_text": "分類用テキスト",
        "status": "active",
        "structured_intent": structured,
    }


async def _intent(api_client, db_engine, headers, structured) -> dict:
    resp = await api_client.post(
        "/v1/intents", headers=headers, json=_payload(structured)
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:vec AS vector),"
                " embedding_model = 'fixture' WHERE id = CAST(:iid AS uuid)"
            ),
            {"vec": E1, "iid": intent["id"]},
        )
    return intent


class _CountingStub(StubLLM):
    """judge呼び出しを数えるスタブ(第一候補不呼出の検査用)。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.judge_calls = 0

    async def judge(self, intent_a, intent_b):
        self.judge_calls += 1
        return await super().judge(intent_a, intent_b)


class _FlakyFirst(_CountingStub):
    """最初のN回のjudgeだけ例外を投げ、以後正常へ回復する第一候補スタブ。"""

    def __init__(self, *, fail_calls: int, exc: Exception, **kw):
        super().__init__(**kw)
        self._fail_calls = fail_calls
        self._exc = exc

    async def judge(self, intent_a, intent_b):
        self.judge_calls += 1
        if self.judge_calls <= self._fail_calls:
            raise self._exc
        return await StubLLM.judge(self, intent_a, intent_b)


class _TimeoutPatternStub(_CountingStub):
    """指定呼び出し番号(1-indexed)だけLLMTimeoutError(p95再現・試験6)。"""

    def __init__(self, *, timeout_at: set[int], **kw):
        super().__init__(**kw)
        self._timeout_at = timeout_at

    async def judge(self, intent_a, intent_b):
        self.judge_calls += 1
        if self.judge_calls in self._timeout_at:
            raise LLMTimeoutError("stub: p95 pattern timeout")
        return await StubLLM.judge(self, intent_a, intent_b)


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


def _breaker_gateway(clock, jev, fb, *, breaker=None) -> LLMGateway:
    return LLMGateway(
        clock=clock,
        parser=StubLLM(),
        embedding=StubLLM(),
        jev=jev,
        jev_fallback=fb,
        breaker=breaker if breaker is not None else CircuitBreaker(clock=clock),
    )


def _stores(redis_client, redis_sweep: str, clock):
    store = JevCostStore(redis_client, key_prefix=redis_sweep)
    return JevCostGuard(store=store, clock=clock), store


async def _pair_of(db_engine, a: str, b: str):
    lo, hi = sorted([a, b], key=str)
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT status, skip_reason, jev_result FROM match_candidates"
                    " WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"a": lo, "b": hi},
            )
        ).first()


async def _latches_of(db_engine, intent_id: str) -> list:
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT id, status FROM latches"
                    " WHERE CAST(:i AS uuid) = ANY(intent_ids)"
                ),
                {"i": intent_id},
            )
        ).all()


# -- design §2.4 試験1〜8 --


async def test_1_rate_limit_switches_to_fallback(
    api_client, db_engine, field, redis_client, redis_sweep, caplog
):
    """切替(引用#6-1): 第一候補429→フォールバック評価継続・provider記録・送信2件。"""
    import json
    import logging

    from latch.llm.records import LOGGER_NAME

    clock = _clock()
    start = _future(BASE_HOURS)
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start))
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured(start=start))
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    first = _FlakyFirst(fail_calls=10**9, exc=LLMRateLimitError("429"))
    fb = _CountingStub()
    gateway = _breaker_gateway(clock, first, fb)
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway, guard=guard, cost_store=store
    )
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await worker.handle(uuid_mod.UUID(a["id"]))
    row = await _pair_of(db_engine, a["id"], b["id"])
    assert row is not None and row[0] == "evaluated"
    assert row[2]["provider"] == "fallback_llm"  # jev_resultのproviderキー
    assert fb.judge_calls == 1
    payloads = [
        json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME
    ]
    statuses = [p["status"] for p in payloads if p.get("system") == "jev"]
    assert "error" in statuses and "ok" in statuses  # 第一候補失敗+fb成功の2件


async def test_2_dual_failure_skips_and_zero_proposals(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """双障害(引用#6-2・3): skipped(llm_failure)・jev_result NULL・提案ゼロ。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start))
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured(start=start))
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    gateway = _breaker_gateway(
        clock,
        StubLLM(fail_jev_exc="ratelimit"),
        StubLLM(fail_jev_exc="timeout"),
    )
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway, guard=guard, cost_store=store
    )
    await worker.handle(uuid_mod.UUID(a["id"]))
    row = await _pair_of(db_engine, a["id"], b["id"])
    assert row[0] == "skipped"
    assert row[1] == "llm_failure"
    assert row[2] is None  # jev_resultはNULLのまま
    assert await _latches_of(db_engine, a["id"]) == []  # 提案ゼロ


async def test_3_breaker_opens_on_error_rate(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """breaker開放・エラー率(引用#6-4): 429を窓内2回→開放・以後第一候補不呼出。

    BreakerParamsは既定値のまま(60秒窓・min_samples=2)。FakeClockで時刻を
    進めない=同一窓内に収める(design §2.4「実値60秒窓のままで呼び出し2回の
    失敗だけで開放する性質を利用」)。実DB要素は開放中でも評価が継続する
    こと(jev_result.provider=fallback_llm)。
    """
    clock = _clock()
    start = _future(BASE_HOURS)
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start))
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    first = StubLLM(fail_jev_exc="ratelimit")
    fb = _CountingStub()
    breaker = CircuitBreaker(clock=clock)
    gateway = _breaker_gateway(clock, first, fb, breaker=breaker)
    # (a) judge_pair直接2回で開放(breaker状態の確認)
    for _ in range(2):
        judgment = await gateway.judge_pair(
            intent_a="A", intent_b="B", intent_ids=["x", "y"]
        )
        assert judgment.provider == "fallback_llm"
    assert breaker.state == "open"
    calls_before = first.judge_calls
    # (b) 開放後のjudge_pairは第一候補を呼ばない
    judgment = await gateway.judge_pair(
        intent_a="A", intent_b="B", intent_ids=["x", "y"]
    )
    assert judgment.provider == "fallback_llm"
    assert first.judge_calls == calls_before  # 増えていない
    # (c) 開放中でもDB評価はフォールバックで継続
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway, guard=guard, cost_store=store
    )
    await worker.handle(uuid_mod.UUID(a["id"]))
    assert first.judge_calls == calls_before  # handle経由でも第一候補不呼出
    rows = await _candidates(db_engine, a["id"])
    assert rows and rows[0][2] and rows[0][2]["provider"] == "fallback_llm"


async def _candidates(db_engine, origin_id: str) -> list:
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT status, skip_reason, jev_result FROM match_candidates"
                    " WHERE intent_a_id = CAST(:i AS uuid)"
                    " OR intent_b_id = CAST(:i AS uuid)"
                ),
                {"i": origin_id},
            )
        ).all()


async def test_4_half_open_success_closes(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """半開・成功で閉じ(引用#6-4): +60秒→第一候補を1回だけ試験→closed→復帰。"""
    clock = _clock()
    first = _FlakyFirst(fail_calls=2, exc=LLMRateLimitError("429"))
    fb = _CountingStub()
    breaker = CircuitBreaker(clock=clock)
    gateway = _breaker_gateway(clock, first, fb, breaker=breaker)
    for _ in range(2):
        await gateway.judge_pair(intent_a="A", intent_b="B", intent_ids=["x", "y"])
    assert breaker.state == "open"
    clock.advance(timedelta(seconds=60))  # 開放→半開
    judgment = await gateway.judge_pair(
        intent_a="A", intent_b="B", intent_ids=["x", "y"]
    )
    assert judgment.provider == "typesafe_jev"  # 試験リクエストは第一候補
    assert first.judge_calls == 3
    assert breaker.state == "closed"
    judgment2 = await gateway.judge_pair(
        intent_a="A", intent_b="B", intent_ids=["x", "y"]
    )
    assert judgment2.provider == "typesafe_jev"  # closed後は第一候補へ復帰
    assert first.judge_calls == 4


async def test_5_half_open_failure_reopens_and_round_trips(
    api_client, db_engine, field
):
    """半開・失敗で開放戻し(引用#1「往復は何度でも」): 2往復を実証。"""
    clock = _clock()
    first = _FlakyFirst(fail_calls=10**9, exc=LLMOverloadedError("529"))  # 常時失敗
    fb = _CountingStub()
    breaker = CircuitBreaker(clock=clock)
    gateway = _breaker_gateway(clock, first, fb, breaker=breaker)
    for _ in range(2):
        await gateway.judge_pair(intent_a="A", intent_b="B", intent_ids=["x", "y"])
    assert breaker.state == "open"
    for _round in range(2):  # 往復2回
        clock.advance(timedelta(seconds=60))  # 半開へ
        calls_before = first.judge_calls
        judgment = await gateway.judge_pair(
            intent_a="A", intent_b="B", intent_ids=["x", "y"]
        )
        assert judgment.provider == "fallback_llm"  # 試験は失敗→fb
        assert first.judge_calls == calls_before + 1  # 半開は1回だけ呼ぶ
        assert breaker.state == "open"  # 失敗→open戻し
        j2 = await gateway.judge_pair(intent_a="A", intent_b="B", intent_ids=["x", "y"])
        assert j2.provider == "fallback_llm"
        assert first.judge_calls == calls_before + 1  # open中は不呼出


async def test_6_p95_condition_opens_alone(api_client, db_engine, field):
    """p95条件単独開放(承認事項1の裏付け): 20呼び出し中2件timeout+18成功
    →エラー率10%≤50%・p95位置=timeout→開放。timeout呼び出しのレイテンシは
    打ち切り時点のtimeout_s(6秒)として母集団に入る(design §2.4試験6)。"""
    clock = _clock()
    first = _TimeoutPatternStub(timeout_at={19, 20})
    fb = _CountingStub()
    breaker = CircuitBreaker(clock=clock)
    gateway = _breaker_gateway(clock, first, fb, breaker=breaker)
    for _ in range(18):
        judgment = await gateway.judge_pair(
            intent_a="A", intent_b="B", intent_ids=["x", "y"]
        )
        assert judgment.provider == "typesafe_jev"
    assert breaker.state == "closed"  # 18成功(エラー率0%)
    for _ in range(2):  # 19・20呼び出し目がtimeout
        await gateway.judge_pair(intent_a="A", intent_b="B", intent_ids=["x", "y"])
    # エラー率2/20=10%≤50% だがp95位置(昇順18番目)=6.0≥6.0で開放
    assert breaker.state == "open"


async def test_7_recovery_reevaluates_skipped(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """復旧後再評価(FR-18・引用#6): 双障害skipped→フォールバック回復→
    同一起点の再handle(「障害回復後のMatch Event」相当)でRESELECT_ALWAYSが
    skipped行を再選択→evaluated・提案発生(高確率応答でL≥0.80)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start))
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured(start=start))
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    # 第一候補=常時429・フォールバック=最初の1回だけtimeout→以後回復(高確率応答)
    fb = _FlakyFirst(
        fail_calls=1,
        exc=LLMTimeoutError("fb timeout"),
        jev_response=_high_prob_envelope(),
    )
    gateway = _breaker_gateway(clock, StubLLM(fail_jev_exc="ratelimit"), fb)
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway, guard=guard, cost_store=store
    )
    latch = LatchEngine(engine=db_engine, clock=clock, geo=GeoService(db_engine))
    await worker.handle(uuid_mod.UUID(a["id"]))  # 双障害→skipped
    row = await _pair_of(db_engine, a["id"], b["id"])
    assert row[0] == "skipped" and row[1] == "llm_failure"
    await latch.handle(uuid_mod.UUID(a["id"]))
    assert await _latches_of(db_engine, a["id"]) == []
    # 回復後の再評価(同一起点のhandle再実行)
    await worker.handle(uuid_mod.UUID(a["id"]))
    row2 = await _pair_of(db_engine, a["id"], b["id"])
    assert row2[0] == "evaluated"
    assert row2[2]["provider"] == "fallback_llm"
    await latch.handle(uuid_mod.UUID(a["id"]))
    latches = await _latches_of(db_engine, a["id"])
    assert len(latches) == 1  # 提案発生(L=0.95≥0.80)


async def test_8_worker_e2e_with_faulty_first(
    api_client, db_engine, worker_env_factory, field, redis_client, redis_sweep
):
    """Worker一気通貫(design §2.4試験8): API→Event→Worker(stage1→embedding→
    L1〜3→Group→Jev→Latch)で第一候補429を注入してもフォールバック経由で
    latches提案まで到達する(Worker DI jev=注入・既存IF)。teardownはfield。"""
    clock = FakeClock(SystemClock().now())
    gateway = _breaker_gateway(
        clock,
        StubLLM(fail_jev_exc="ratelimit"),
        StubLLM(jev_response=_high_prob_envelope()),
    )
    store = JevCostStore(redis_client, key_prefix=redis_sweep)
    jev_worker = JevWorker(
        engine=db_engine,
        clock=clock,
        gateway=gateway,
        guard=JevCostGuard(store=store, clock=clock),
        cost_store=store,
    )
    async with worker_env_factory(db_engine, jev=jev_worker) as _:
        ha = await _user(api_client, field)
        a = await _intent(api_client, db_engine, ha, _structured())
        hb = await _user(api_client, field)
        await _intent(api_client, db_engine, hb, _structured())
        await _wait_processed(db_engine, a["id"])
        await _wait_latch(db_engine, a["id"])
        async with db_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT jev_result->>'provider' FROM match_candidates"
                        " WHERE intent_a_id = CAST(:i AS uuid)"
                        " OR intent_b_id = CAST(:i AS uuid)"
                    ),
                    {"i": a["id"]},
                )
            ).first()
        assert row is not None and row[0] == "fallback_llm"
        assert len(await _latches_of(db_engine, a["id"])) == 1  # 提案まで到達


async def _wait_processed(db_engine, intent_id: str, timeout=10.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with db_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT status FROM match_events"
                        " WHERE source_intent_id = CAST(:i AS uuid)"
                        " ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"i": intent_id},
                )
            ).first()
        if row is not None and row[0] == "processed":
            return
        await asyncio.sleep(0.2)
    pytest.fail(f"match_events not processed: {intent_id}")


async def _wait_latch(db_engine, intent_id: str, timeout=15.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await _latches_of(db_engine, intent_id):
            return
        await asyncio.sleep(0.2)
    pytest.fail(f"latch not created for {intent_id}")
