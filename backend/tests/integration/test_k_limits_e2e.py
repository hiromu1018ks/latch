"""K上限裏付け・冪等性・02#7更新・02#8 Bucket再評価のE2E
(M2 ws-8 design §2.6・10 §4.6〜§4.7)。

実DB・実Redis。Workerプロセスは立てない(試験1・3・4は部品直接構成・
試験2のみworker_env)。対抗策はtest_degraded_e2eと同一(時間窓now+5日統一・
subject prefix teardown・Redis prefix掃除・FakeClock)。
"""

import asyncio
import copy
import json
import math
import random
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.geo.service import GeoService
from latch.llm.gateway import LLMGateway
from latch.llm.stub import DEFAULT_JEV_RESPONSE, StubLLM
from latch.worker.cost import JevCostGuard, JevCostStore, ReevalGuard
from latch.worker.jev import JevWorker
from latch.worker.matching import run_candidate_retrieval
from latch.worker.matching.group_engine import GroupEngine
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.reeval import ReevalRunner

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120
SUBJECT_PREFIX = "m2ws8k-"


def _unique_vec() -> str:
    """テスト毎に一意な768次元ランダム単位ベクトル(pgvector文字列)。

    pgvectorのHNSW indexは削除行の死エントリが残るため、全テストが同一
    ベクトルを使うと先行テストの死エントリが後続テストのK_v=50近似探索の
    予算を食い潰す。各テストの先頭で1回呼びテスト内では同一ベクトルを
    使い回す(全員同一=同点→intent_id昇順の決定性はテスト内で維持)。
    """
    components = [random.random() for _ in range(768)]
    norm = math.sqrt(sum(c * c for c in components)) or 1.0
    return "[" + ",".join(repr(c / norm) for c in components) + "]"


def _high_prob_envelope() -> dict:
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
    await _teardown_prefix(db_engine, prefix)


async def _teardown_prefix(db_engine, prefix: str) -> None:
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
        # notificationsはuser_id FKでusersを参照(提案経路がuser単位で書く)。
        # latch_id経由ではNULL/範囲外行が残りfk_notifications_user違反になる
        await conn.execute(
            text(
                "DELETE FROM notifications WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
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
    prefix = f"ws8k-{uuid_mod.uuid4().hex[:8]}-"
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
        json={"display_name": "m2ws8k", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured(*, start: str | None = None, expires: str | None = None) -> dict:
    structured = {
        "category": {"primary": CATEGORY, "secondary": None},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }
    if expires is not None:
        structured["expires_at"] = expires  # 明示期限(補完のスナップ回避)
    return structured


def _payload(structured: dict) -> dict:
    return {
        "raw_text": "分類用テキスト",
        "status": "active",
        "structured_intent": structured,
    }


async def _intent(api_client, db_engine, headers, structured, vec) -> dict:
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
            {"vec": vec, "iid": intent["id"]},
        )
    return intent


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


def _breaker_free_gateway(clock) -> LLMGateway:
    """K上限試験はbreaker無しのstub gateway(breakerは別資産が検証済み)。"""
    return LLMGateway(
        clock=clock,
        parser=StubLLM(),
        embedding=StubLLM(),
        jev=StubLLM(jev_response=_high_prob_envelope()),
        jev_fallback=StubLLM(),
    )


class _RecordingGateway(LLMGateway):
    """judge_pairの呼び出し順を記録(決定性検証・§2.6試験1の⑧)。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.judge_order: list[tuple[str, str]] = []

    async def judge_pair(self, *, intent_a, intent_b, intent_ids):
        self.judge_order.append(tuple(sorted(intent_ids)))
        return await super().judge_pair(
            intent_a=intent_a, intent_b=intent_b, intent_ids=intent_ids
        )


def _stores(redis_client, redis_sweep: str, clock):
    store = JevCostStore(redis_client, key_prefix=redis_sweep)
    return JevCostGuard(store=store, clock=clock), store


def _peer_ids(outcome) -> list[str]:
    """Outcomeのpairsから起点以外の側のidを行順に取り出す
    (test_matching_retrieval._peer_idsと同一流儀)。"""
    out = []
    for p in outcome.pairs:
        a, b = str(p.intent_a_id), str(p.intent_b_id)
        out.append(b if str(outcome.intent_id) == a else a)
    return out


async def _full_chain(db_engine, clock, gateway, guard, store, origin_id):
    """L1〜3→Group→Jev→Latch→Group.finalizeの共通チェーン
    (worker_envの_run_post_retrieval相当を試験内で直列呼び出し)。"""
    async with db_engine.begin() as conn:
        outcome = await run_candidate_retrieval(conn, clock, uuid_mod.UUID(origin_id))
    latch = LatchEngine(engine=db_engine, clock=clock, geo=GeoService(db_engine))
    group = GroupEngine(
        engine=db_engine, clock=clock, geo=GeoService(db_engine), latch=latch
    )
    group_ctx = await group.handle(uuid_mod.UUID(origin_id))
    jev = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway, guard=guard, cost_store=store
    )
    await jev.handle(uuid_mod.UUID(origin_id), group_ctx)
    await latch.handle(uuid_mod.UUID(origin_id))
    await group.finalize(uuid_mod.UUID(origin_id))
    return outcome


async def test_1_k_limits_all_layers(
    api_client, db_engine, field, redis_client, redis_sweep, caplog
):
    """K上限一気通貫(10 §4.6・引用#7・#11・#5観察・G2条件②)。

    配置(§9-15): 起点1(min=2/max=4 — Layer1通過とGroupEngine種トリガー
    〔起点max>=3・ws-7 load_group_origin〕の両立)+Layer1純通過組101
    (min=2/max=2)+min=3混入10(max=4・Pool用・Layer1の1対1では不通過)
    +除外要因6(時間交差なし3+ペア予算300円3)=計118 Intent。全員同一時間帯・
    同一ベクトル(同点→intent_id昇順)。検証: ①layer1_pass_count>100
    ②pairs≤50=同点intent_id昇順上位50 ③topkc≤20 ④Jev≤8・1対1最低4回
    ⑤`group pool built pool_size=`≤15 ⑥latchesのproposed/partial_acceptが
    D-08上限内(同時3件/Intent・candidateは06 v0.4により上限外)
    ⑦除外ペア非生成 ⑧決定性(同一入力2回実行で同一結果)。
    """
    import logging
    import time

    import latch.worker.matching.group_engine as ge_mod

    clock = _clock()
    vec = _unique_vec()  # テスト内全員同一(同点→intent_id昇順)・テスト間は一意
    start = _future(BASE_HOURS)
    h0 = await _user(api_client, field)
    origin_struct = _structured(start=start)
    origin_struct["participants"] = {"min": 2, "max": 4}
    origin_struct["budget"] = {"max": 5000}
    origin = await _intent(api_client, db_engine, h0, origin_struct, vec)
    pure_ids: list[str] = []  # Layer1純通過組101(min=2/max=2・budget5000)
    for _ in range(101):
        h = await _user(api_client, field)
        t = await _intent(api_client, db_engine, h, _structured(start=start), vec)
        pure_ids.append(t["id"])
    group_ids: list[str] = []  # min=3/max=4混入10(Pool用・Layer1では不通過)
    for _ in range(10):
        h = await _user(api_client, field)
        s = _structured(start=start)
        s["participants"] = {"min": 3, "max": 4}
        s["budget"] = {"max": 5000}
        t = await _intent(api_client, db_engine, h, s, vec)
        group_ids.append(t["id"])
    excluded: dict[str, str] = {}  # 除外要因6(3=時間交差なし・3=予算300)
    for _ in range(3):
        h = await _user(api_client, field)
        # +43h=163h<168h(+7日expires_at上限内)かつ基準120hとのΔ=43h>3h
        # (flex)で時間非交差は維持(+72hは上限超過で422になる)
        t = await _intent(
            api_client, db_engine, h, _structured(start=_future(BASE_HOURS + 43)), vec
        )
        excluded[t["id"]] = "time"
    for _ in range(3):
        h = await _user(api_client, field)
        s = _structured(start=start)
        s["budget"] = {"max": 300}
        t = await _intent(api_client, db_engine, h, s, vec)
        excluded[t["id"]] = "budget"

    guard, store = _stores(redis_client, redis_sweep, clock)
    gateway = _RecordingGateway(
        clock=clock,
        parser=StubLLM(),
        embedding=StubLLM(),
        jev=StubLLM(jev_response=_high_prob_envelope()),
        jev_fallback=StubLLM(),
    )
    with caplog.at_level(logging.INFO, logger=ge_mod.__name__):
        t0 = time.monotonic()
        outcome = await _full_chain(
            db_engine, clock, gateway, guard, store, origin["id"]
        )
        elapsed = time.monotonic() - t0
    print(f"\n[ws8k test1] chain elapsed={elapsed:.3f}s")
    # ①一次候補100件超(10 §4.6)。layer1_pass_countは起点を除く通過数
    # (test_2_kv流儀) = 純通過組101(min3組10・除外6はLayer1不通過)
    assert outcome.layer1_pass_count > 100
    assert outcome.layer1_pass_count == 101
    # ②Vector出力≤50・同点はintent_id昇順上位50(D-24・_peer_ids流儀)
    assert len(outcome.pairs) == 50
    assert _peer_ids(outcome) == sorted(pure_ids)[:50]
    # ③Cheap Judge出力≤20(match_candidates生成)
    assert len(outcome.topkc) <= 20
    # ④Jev実行回数≤8・1対1最低4回保証(06 §5配分)
    async with db_engine.connect() as conn:
        evaluated = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM match_candidates"
                    " WHERE (intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid))"
                    " AND status = 'evaluated'"
                ),
                {"o": origin["id"]},
            )
        ).scalar_one()
    assert evaluated <= 8
    assert evaluated >= 4  # 1対1最低4回
    # ⑤Pool≤15(ws-7のログ・D-24)。Pool候補は人数緩和検索(min<=4 AND max>=3)
    # でmin3混入10名+起点は種(純通過組はmax=2でPool外)→pool_size=10
    pool_logs = [r.message for r in caplog.records if "group pool built" in r.message]
    assert pool_logs  # 起点max=4>=3でPool構築が走る
    for m in pool_logs:
        size = int(m.split("pool_size=")[1].split()[0])
        assert size <= 15
    # ⑥latchesの同時進行上限(D-08)はproposed数の上限 — candidateのままの
    # 存在通知には適用しない(06 v0.4)。proposed/partial_acceptのみ<=3を
    # 検証(candidate行は上限外のため数えない)
    origin_latches = await _latches_of(db_engine, origin["id"])
    proposed = [r for r in origin_latches if r[1] in ("proposed", "partial_accept")]
    assert len(proposed) <= 3
    # ⑦除外ペア非生成(#9のE2E側)
    for ex_id in excluded:
        assert await _pair_of(db_engine, origin["id"], ex_id) is None
    # ⑧決定性: 掃除→同一入力(同一uuid)で再実行→切り詰め選択・Jev呼び出し順
    # ・行集合が同一(引用#7「同一入力での2回実行が同一結果」)
    snap1 = await _snapshot(db_engine, origin["id"])
    order1 = list(gateway.judge_order)
    async with db_engine.begin() as conn:
        # notificationsにlatch_id列はなくpayload->>'latch_id'(JSONB)に格納される
        # (ws-6設計§2.6)。uuidの正規形文字列で突き合わせる
        await conn.execute(
            text(
                "DELETE FROM notifications WHERE payload->>'latch_id' IN"
                " (SELECT id::text FROM latches"
                " WHERE CAST(:o AS uuid) = ANY(intent_ids))"
            ),
            {"o": origin["id"]},
        )
        # latchesはlatch_status_eventsからFK参照されるため先に削除
        # (field teardownと同順序)
        await conn.execute(
            text(
                "DELETE FROM latch_status_events WHERE latch_id IN"
                " (SELECT id FROM latches"
                " WHERE CAST(:o AS uuid) = ANY(intent_ids))"
            ),
            {"o": origin["id"]},
        )
        await conn.execute(
            text("DELETE FROM latches WHERE CAST(:o AS uuid) = ANY(intent_ids)"),
            {"o": origin["id"]},
        )
        await conn.execute(
            text(
                "DELETE FROM group_candidates WHERE CAST(:o AS uuid) = ANY(intent_ids)"
            ),
            {"o": origin["id"]},
        )
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id = CAST(:o AS uuid)"
                " OR intent_b_id = CAST(:o AS uuid)"
            ),
            {"o": origin["id"]},
        )
    # 再チェーン(_full_chainと同一構成・RecordingGatewayで呼び出し順を採取)
    rec_guard, rec_store = _stores(redis_client, redis_sweep, clock)
    rec_gateway = _RecordingGateway(
        clock=clock,
        parser=StubLLM(),
        embedding=StubLLM(),
        jev=StubLLM(jev_response=_high_prob_envelope()),
        jev_fallback=StubLLM(),
    )
    outcome2 = await _full_chain(
        db_engine, clock, rec_gateway, rec_guard, rec_store, origin["id"]
    )
    assert _peer_ids(outcome2) == _peer_ids(outcome)  # 切り詰め選択の同一性
    assert rec_gateway.judge_order == order1  # Jev呼び出し順の同一性
    snap2 = await _snapshot(db_engine, origin["id"])
    assert snap1 == snap2  # ペア・status・cheap_judge_score・latches・group


async def _pair_of(db_engine, a: str, b: str):
    lo, hi = sorted([a, b], key=str)
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT id FROM match_candidates"
                    " WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid) LIMIT 1"
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


async def _snapshot(db_engine, origin_id: str):
    """決定性検証の行集合(ペア・status・cheap_judge_score+latches+group)。"""
    async with db_engine.connect() as conn:
        mc = (
            await conn.execute(
                text(
                    "SELECT intent_a_id, intent_b_id, status,"
                    " cheap_judge_score::float8 FROM match_candidates"
                    " WHERE intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid)"
                    " ORDER BY intent_a_id, intent_b_id"
                ),
                {"o": origin_id},
            )
        ).all()
        latches = (
            await conn.execute(
                text(
                    "SELECT intent_ids, status, score::float8 FROM latches"
                    " WHERE CAST(:o AS uuid) = ANY(intent_ids)"
                    " ORDER BY intent_ids::text"
                ),
                {"o": origin_id},
            )
        ).all()
        gc = (
            await conn.execute(
                text(
                    "SELECT intent_ids::text, status, aggregate_score::float8"
                    " FROM group_candidates WHERE CAST(:o AS uuid) = ANY(intent_ids)"
                    " ORDER BY intent_ids::text"
                ),
                {"o": origin_id},
            )
        ).all()
    return {
        "mc": sorted((str(a), str(b), s, c) for a, b, s, c in mc),
        "latches": sorted((ids, s, sc) for ids, s, sc in latches),
        "group": sorted(gc),
    }


async def test_2_duplicate_event_no_double_candidates(
    api_client, db_engine, worker_env, field
):
    """冪等性(10 §4.7・引用#8・G2条件③): 同一Event2回投入でmatch_candidates
     が二重生成しない(独立簡易配置: 起点U0+相手15・同一時間帯)。

     検証対象は「行の二重生成なし」=ペア集合(重複込み)の不変(10 §4.7の文言
     「match_candidatesが二重生成しない」)。status(pending→evaluated)はJev評価の
     進行で正当に遷移するため比較対象に含めない。生成がまだ進行中の比較を避ける
    ため、ペア集合が安定(1秒間隔で2回連続同一)してから重複を投入する。
     teardownはfield fixture(assert失敗時も必ず走る)。"""
    vec = _unique_vec()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(), vec)
    for _ in range(15):  # 相手15(dense相当・全員同一時間帯)
        h = await _user(api_client, field)
        await _intent(api_client, db_engine, h, _structured(), vec)
    # Workerにcreated Eventを処理させる(embedding→L1〜3まで走る)のち
    # ペア集合の安定を待つ(status遷移は待ち対象外)
    before = await _stable_candidate_pairs(db_engine, a["id"])
    assert len(before) >= 1
    # 同一3点組ペイロードを2回投入(Stage1のUNIQUEで2回目以降はduplicate)
    payload = json.dumps(
        {"event_type": "created", "source_intent_id": a["id"], "version": 1}
    ).encode()
    await worker_env.bus.publish_raw(payload)
    await worker_env.bus.publish_raw(payload)
    await asyncio.sleep(2.0)  # Workerの処理settle
    after = await _candidate_pairs(db_engine, a["id"])
    assert after == before  # ペア集合と行数が不変(二重生成なし)


async def _candidate_pairs(db_engine, origin_id) -> list[tuple[str, str]]:
    """候補のペア集合(重複込み・昇順)。二重生成はここで重複として現れる。"""
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT intent_a_id, intent_b_id FROM match_candidates"
                    " WHERE intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid)"
                ),
                {"o": origin_id},
            )
        ).all()
    return sorted((str(r[0]), str(r[1])) for r in rows)


async def _stable_candidate_pairs(db_engine, origin_id, *, tries=10):
    """ペア集合が安定するまで1秒間隔で取得(2回連続同一で安定とみなす)。"""
    prev = await _candidate_pairs(db_engine, origin_id)
    for _ in range(tries):
        await asyncio.sleep(1.0)
        cur = await _candidate_pairs(db_engine, origin_id)
        if cur == prev and cur:
            return cur
        prev = cur
    pytest.fail(f"candidate rows not stabilized: {origin_id}")


async def _wait_candidate_count(db_engine, origin_id, *, expected_ge, timeout=20.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        rows = await _candidate_rows(db_engine, origin_id)
        if len(rows) >= expected_ge:
            return rows
        await asyncio.sleep(0.3)
    pytest.fail("candidates not generated in time")


async def _candidate_rows(db_engine, origin_id):
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT intent_a_id, intent_b_id, status,"
                    " cheap_judge_score::float8 FROM match_candidates"
                    " WHERE intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid)"
                    " ORDER BY intent_a_id, intent_b_id"
                ),
                {"o": origin_id},
            )
        ).all()


async def test_3_intent_update_drops_stale_pair(
    api_client, db_engine, worker_env, field
):
    """02#7更新E2E(design §2.5-A): 2 Intent→候補生成→PATCH(時間帯を交差
    しない値へ)→debounce窓解放(FakeClock)→再評価で旧ペアが新評価世代で
    再生成されない=消失。teardownはfield fixture(assert失敗時も必ず走る)。"""
    vec = _unique_vec()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(), vec)
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured(), vec)
    await _wait_candidate_count(db_engine, a["id"], expected_ge=1)
    row = await _pair_of(db_engine, a["id"], b["id"])
    assert row is not None  # PATCH前: 候補生成の記録(02#6も兼ねる)
    # PATCH: Bの時間帯を交差しない値へ(+43時間=163h<168hの+7日上限内・
    # 基準120hとのΔ=43h>3h flexで非交差維持。+72hは8日で上限超過422)
    new_payload = _payload(_structured(start=_future(BASE_HOURS + 43)))
    new_payload["raw_text"] = "別の日の別の時間帯で"
    resp = await api_client.patch(
        f"/v1/intents/{b['id']}",
        headers=hb,
        json=new_payload,
    )
    assert resp.status_code == 200, resp.text
    new_version = resp.json()["intent"]["version"]
    # debounce窓解放(ws-1流儀: 受信settle後にClockを進める)
    await asyncio.sleep(1.0)
    worker_env.clock.advance(timedelta(seconds=10))
    await _wait_processed_version(db_engine, b["id"], new_version)
    await asyncio.sleep(1.0)  # 再評価チェーン(_run_post_retrieval)のsettle
    # 旧ペアは新評価世代で再生成されない(候補の消失)。主検証は
    # 「A現行version×B新versionの行が存在しない」=新評価世代での非再生成
    async with db_engine.connect() as conn:
        latest = (
            await conn.execute(
                text(
                    "SELECT intent_a_version, intent_b_version FROM"
                    " match_candidates WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"a": min(a["id"], b["id"]), "b": max(a["id"], b["id"])},
            )
        ).first()
    assert latest is not None  # 旧評価世代の行は残る(履歴)
    async with db_engine.connect() as conn:
        new_gen = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM match_candidates"
                    " WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                    " AND intent_a_version = :av AND intent_b_version = :bv"
                ),
                {
                    "a": min(a["id"], b["id"]),
                    "b": max(a["id"], b["id"]),
                    "av": a["version"],
                    "bv": new_version,
                },
            )
        ).scalar_one()
    assert new_gen == 0  # 新評価世代のペア行は存在しない=消失
    # 起点Aの現行評価対象(次のJev選択)に旧ペアが入らない
    async with db_engine.connect() as conn:
        pending = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM match_candidates"
                    " WHERE (intent_a_id = CAST(:a AS uuid)"
                    " OR intent_b_id = CAST(:a AS uuid))"
                    " AND jev_result IS NULL AND status = 'pending'"
                    " AND intent_a_version = :av"
                ),
                {"a": a["id"], "av": a["version"]},
            )
        ).scalar_one()
    assert pending == 0


async def _wait_processed_version(db_engine, intent_id, version, timeout=15.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with db_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT status FROM match_events"
                        " WHERE source_intent_id = CAST(:i AS uuid)"
                        " AND payload->>'version' = :v"
                    ),
                    {"i": intent_id, "v": str(version)},
                )
            ).first()
        if row is not None and row[0] == "processed":
            return
        await asyncio.sleep(0.2)
    pytest.fail(f"updated event not processed: {intent_id} v{version}")


async def test_4_bucket_reeval_generates_candidates(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """02#8 Bucket再評価(design §2.5-B・06 §9 FR-09): time_startを未来Bucketに
    置いた2 Intent→FakeClockでBucket境界を経過→run_onceで抽出→候補生成。
    _SELECT_BUCKET_TARGETS側の明示試験(test_10はcatch-up側)。"""
    clock = _clock()
    vec = _unique_vec()
    # 未来Bucket: 現在の30分Bucketの2つ先(境界経過の余地を持たせる)。
    # time_startが未来ならexpires_at(作成時の+7日上限内)は十分遠方で
    # catch-up対象外になる
    bucket_ahead = clock.now() + timedelta(minutes=45)
    start = bucket_ahead.isoformat()
    # 明示expires(supervisor直接修正・2026-09-30): 期限nullの補完は
    # 「今夜JST 23:30/翌日12:00/翌日23:30/now+72h」の最寄りへスナップするため、
    # 21:30〜23:30 JST帯の実行では今夜23:30(=now+2時間以内)が最寄りになり
    # a/bがcatch-up対象化してrun_once()!=0で失敗する(日次の時限爆弾)。
    # 期限を明示的に十分遠方へ置き、時刻帯に依存しない決定性を担保する。
    expires = (clock.now() + timedelta(hours=6)).isoformat()
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires), vec
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires), vec
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
    assert await runner.run_once() == 0  # 未来Bucket・期限遠方→対象なし
    assert calls == []
    clock.advance(timedelta(minutes=45))  # Bucket境界を経過
    done = await runner.run_once()
    assert done >= 1
    assert set(calls) >= {a["id"], b["id"]}
    row = await _pair_of(db_engine, a["id"], b["id"])
    assert row is not None  # 時刻到来による候補生成(02#8)
