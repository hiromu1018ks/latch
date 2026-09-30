"""GroupEngine(グループマッチ)のintegration試験(M2 ws-7 design §4.2)。

実DB(compose常設・0005部分UNIQUE適用後)・実Redis(compose常設)。Worker
プロセスは起動しない(GroupEngine/JevWorker/LatchEngine直接構築・FakeClock
注入・JevはスタブGateway — test_matching_jev.py流儀)。
対抗策(test_matching_latchengine.py流儀+ws-7分): (1)時間窓はnow+5日
(BASE_HOURS=120)系へ統一し関数内パート間で重ならないよう+12h等へずらす
(2)subjectプレフィックス m2ws7- ・teardownは latch_status_events→
notifications→latches→group_candidates→match_candidates→match_events→
intents→users の順で完全削除(group_candidatesがlatchesのFK元のため
latchesの後・match_candidatesの前 — design §4) (3)Redisは ws7- prefixの
SCAN+DELETE掃き (4)jev値はStubLLMのjev_response指定(全ペア同一)または
match_candidatesへの直接UPDATE(ペアごとの値)。
収束は10試験。実行はスーパーバイザー検証時のtest-ci(STATUS運用ルール1〜3
・マイグレーション0005追加単位のため実装側ではcollectのみ)。
"""

import asyncio
import json
import sys
import time
import uuid as uuid_mod
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.geo.service import GeoService
from latch.llm.errors import LLMError
from latch.llm.gateway import LLMGateway
from latch.llm.stub import DEFAULT_JEV_RESPONSE, StubLLM
from latch.settings import Settings
from latch.worker.cost import JevCostGuard, JevCostStore
from latch.worker.jev import JevWorker
from latch.worker.matching import run_candidate_retrieval
from latch.worker.matching.group_engine import GroupEngine
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.stage1 import Stage1

pytestmark = pytest.mark.integration

CATEGORY = "meal"  # Literal実在値(分離は時間窓が担う — ws-4対抗策3)
BASE_HOURS = 120
SUBJECT_PREFIX = "m2ws7-"
REDIS_PREFIX = "ws7-"
FAR_EXPIRES_H = 144


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)  # 全員同一(similarity=1)


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
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除(対抗策)。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        # group_candidatesはlatchesのFK元 → latchesの後・match_candidatesの前
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
                "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
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
    """試験ごとのRedis接頭辞。teardownでSCAN+DELETE(対抗策3)。"""
    prefix = f"{REDIS_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
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
        json={"display_name": "m2ws7", "birth_date": birth_date, "profile": {}},
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
    # 既定summary_only: 全フィールドproposalを期待する試験が既定で通るように
    # (ws-6 test_matching_latchengine.pyと同一規約。hidden混在はtest_7が明示渡し)
    visibility: str = "summary_only",
    notification_level: str | None = None,
    secondary: str | None = None,
    budget_max: int | None = None,
    participants: tuple[int, int] | None = None,
) -> dict:
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": secondary},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }
    if expires is not None:
        d["expires_at"] = expires
    d["visibility"] = visibility
    if notification_level is not None:
        d["notification_level"] = notification_level
    if budget_max is not None:
        d["budget"] = {"max": budget_max}
    if participants is not None:
        d["participants"] = {"min": participants[0], "max": participants[1]}
    return d


def _payload(structured: dict) -> dict:
    return {
        "raw_text": "分類用テキスト",
        "status": "active",
        "structured_intent": structured,
    }


async def _embed(db_engine, intent_id: str, vec: str = E1) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:vec AS vector),"
                " embedding_model = 'fixture'"
                " WHERE id = CAST(:iid AS uuid)"
            ),
            {"vec": vec, "iid": intent_id},
        )


async def _intent(api_client, db_engine, headers, structured, *, vec: str = E1) -> dict:
    """active Intentを作りembeddingを直接挿入(ws-5流儀のfixture方針)。"""
    resp = await api_client.post(
        "/v1/intents", headers=headers, json=_payload(structured)
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    await _embed(db_engine, intent["id"], vec)
    return intent


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


def _jev_response(wa: float, wb: float) -> dict:
    """StubLLMのjev_response(would値を指定 — 全ペア同一値)。"""
    resp = deepcopy(DEFAULT_JEV_RESPONSE)
    resp["answers"]["would_a_accept_b"]["noul"] = wa
    resp["answers"]["would_b_accept_a"]["noul"] = wb
    return resp


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


def _stub_gateway(wa: float = 0.9, wb: float = 0.85) -> LLMGateway:
    return LLMGateway(
        clock=_clock(),
        parser=StubLLM(),
        embedding=StubLLM(),
        jev=StubLLM(jev_response=_jev_response(wa, wb)),
        jev_fallback=StubLLM(jev_response=_jev_response(wa, wb)),
    )


def _jev_worker(db_engine, clock, redis_client, sweep, wa=0.9, wb=0.85):
    store = JevCostStore(redis_client, key_prefix=sweep)
    return JevWorker(
        engine=db_engine,
        clock=clock,
        gateway=_stub_gateway(wa, wb),
        guard=JevCostGuard(store=store, clock=clock),
        cost_store=store,
    )


class _FailAfterJev:
    """n件目までは正常応答・以降はLLMError(試験3)。

    guard拒否(intent_daily等)で落とすと当該ペアは日次リセットまで再選択され
    ない(D-15)ため、「同じ日の次評価で未判定ペアが消化される」を観察するには
    再選択可能なllm_failureで落とす必要がある。jev/jev_fallbackへ同一インスタンス
    を渡しカウンタを共有する(第一候補失敗→フォールバックも失敗→llm_failure)。
    """

    name = "stub"  # JevProvider規約(Gatewayの送信記録が参照)

    def __init__(self, response: dict, ok: int) -> None:
        self._response = response
        self._ok = ok
        self.calls = 0

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        self.calls += 1
        if self.calls > self._ok:
            raise LLMError("test flaky after ok")
        return await StubLLM(jev_response=self._response).judge(intent_a, intent_b)


def _group_engine(db_engine, clock) -> GroupEngine:
    latch = LatchEngine(engine=db_engine, clock=clock, geo=GeoService(db_engine))
    return GroupEngine(
        engine=db_engine,
        clock=clock,
        geo=GeoService(db_engine),
        latch=latch,
    )


async def _group_of(db_engine, intent_ids: list[str]):
    """指定メンバー(sorted)と一致するgroup_candidates行(開いている行優先)。"""
    arr = ", ".join(f"CAST('{i}' AS uuid)" for i in sorted(intent_ids, key=str))
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    f"SELECT id, status, member_scores, aggregate_score"
                    f" FROM group_candidates"
                    f" WHERE intent_ids = ARRAY[{arr}]::uuid[]"
                    f" ORDER BY (status IN ('candidate','proposed')) DESC,"
                    f" updated_at DESC LIMIT 1"
                )
            )
        ).first()


async def _group_latch_of(db_engine, intent_ids: list[str]):
    """指定メンバー(sorted)と一致するlatches行(開いている行優先)。"""
    arr = ", ".join(f"CAST('{i}' AS uuid)" for i in sorted(intent_ids, key=str))
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    f"SELECT id, status, score, proposal, group_candidate_id"
                    f" FROM latches"
                    f" WHERE intent_ids = ARRAY[{arr}]::uuid[]"
                    f" ORDER BY (status IN ('candidate','proposed',"
                    f"'partial_accept')) DESC, created_at DESC LIMIT 1"
                )
            )
        ).first()


async def _pair_row(db_engine, a: str, b: str):
    lo, hi = sorted([a, b], key=str)
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT id, status, jev_result, latch_score FROM"
                    " match_candidates WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"a": lo, "b": hi},
            )
        ).first()


async def _seed_jev_pair(db_engine, a: str, b: str, wa: float, wb: float) -> None:
    """1ペアのpending行へjev_resultを直接投入(ペアごとの値制御・試験6)。"""
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
            {"jev": jev, "a": a, "b": b},
        )
        assert res.rowcount == 1, "jev直接投入対象行なし"


async def _jev_eval_all(jev, first: str, others: list[str], ctx) -> None:
    """全メンバー起点でJev評価(実運用では各メンバーのembedding_completedで走る)。

    JevWorkerは起点が端点のペア行しか選ばないため、種以外のメンバー間ペア
    (b×c等・design §2.5「評価は複数イベントにまたがって進む」)は
    各メンバー起点の評価で揃える。再評価はjev_result IS NULLガードで冪等。
    """
    await jev.handle(uuid_mod.UUID(first), ctx)
    for i in others:
        await jev.handle(uuid_mod.UUID(i))


# -- design §4.2 試験1〜10 --


async def test_1_three_member_group_e2e(
    api_client, db_engine, field, redis_client, redis_sweep, caplog
):
    """3人集合E2E(引用#18の下地): 生成→K_j配分→除外→集約→提案。"""
    import logging

    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    members: dict[str, dict] = {}
    for tag, budget, secondary in (
        ("a", 5000, "ビアバー"),
        ("b", 3000, None),
        ("c", 4000, None),
    ):
        h, _ = await _user(api_client, field)
        members[tag] = await _intent(
            api_client,
            db_engine,
            h,
            _structured(
                start=start,
                expires=expires,
                budget_max=budget,
                secondary=secondary,
                participants=(2, 4),
            ),
        )
    a, b, c = members["a"], members["b"], members["c"]

    group = _group_engine(db_engine, clock)
    import latch.worker.matching.group_engine as ge_mod

    with caplog.at_level(logging.INFO, logger=ge_mod.__name__):
        ctx = await group.handle(uuid_mod.UUID(a["id"]))
    assert ctx is not None and len(ctx.group_ids) == 1
    gc = await _group_of(db_engine, [a["id"], b["id"], c["id"]])
    assert gc is not None and gc[1] == "candidate"
    assert sorted(gc[2]["versions"]) == sorted(
        [a["id"], b["id"], c["id"]]
    )  # member_scores.versions
    # 3ペア(A×B・A×C・B×C)がpendingで生成される
    for x, y in ((a, b), (a, c), (b, c)):
        row = await _pair_row(db_engine, x["id"], y["id"])
        assert row is not None and row[1] == "pending" and row[2] is None

    # JevWorker(group_ctx付き) → 3ペアすべてjev_result(stub 0.9/0.85)。
    # 全メンバー起点で評価(b×cはb起点の評価で揃う)
    jev = _jev_worker(db_engine, clock, redis_client, redis_sweep, 0.9, 0.85)
    await _jev_eval_all(jev, a["id"], [b["id"], c["id"]], ctx)
    for x, y in ((a, b), (a, c), (b, c)):
        row = await _pair_row(db_engine, x["id"], y["id"])
        assert row[2] is not None, "jev_result未投入"

    # 集約 → aggregate=0.85・latches(candidate→try_promoteでproposed)
    await group.finalize(uuid_mod.UUID(a["id"]))
    gc2 = await _group_of(db_engine, [a["id"], b["id"], c["id"]])
    assert gc2 is not None and gc2[1] == "proposed"
    assert float(gc2[3]) == 0.85  # H(=1) × min(0.85) × C(=1.0)・丸めなし
    latch = await _group_latch_of(db_engine, [a["id"], b["id"], c["id"]])
    assert latch is not None and latch[1] == "proposed"
    assert str(latch[4]) == str(gc2[0])  # group_candidate_id紐付
    assert float(latch[2]) == 0.85
    proposal = latch[3]
    assert proposal["headcount"] == 3
    assert proposal["category_secondary"] == "ビアバー"  # 種(a)の値
    assert proposal["budget"] == {"max": 3000}  # min(budget_max)
    # events: candidate→proposed
    async with db_engine.connect() as conn:
        events = (
            await conn.execute(
                text(
                    "SELECT from_status, to_status FROM latch_status_events"
                    " WHERE latch_id = CAST(:l AS uuid) ORDER BY created_at"
                ),
                {"l": str(latch[0])},
            )
        ).all()
    assert [(e[0], e[1]) for e in events] == [
        (None, "candidate"),
        ("candidate", "proposed"),
    ]
    # notifications: 3名へ type='proposal'
    for iid in (a["id"], b["id"], c["id"]):
        uid = await _user_id_of(db_engine, iid)
        async with db_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT type, payload FROM notifications"
                        " WHERE user_id = CAST(:u AS uuid)"
                    ),
                    {"u": uid},
                )
            ).all()
        assert len(rows) == 1 and rows[0][0] == "proposal"
        assert rows[0][1] == {"latch_id": str(latch[0])}
    # グループペア行はlatch_score計算対象外(1対1除外・Review Focus 3)
    for x, y in ((a, b), (a, c), (b, c)):
        row = await _pair_row(db_engine, x["id"], y["id"])
        assert row[3] is None, "グループペア行のlatch_scoreがNULLでない"


async def test_2_pool_limit_15_recorded(api_client, db_engine, field, caplog):
    """Pool上限15(引用#16): 密集20件→Pool 15・cheap降順(similarity順)。"""
    import logging

    import latch.worker.matching.group_engine as ge_mod

    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    h_seed, _ = await _user(api_client, field)
    seed = await _intent(
        api_client,
        db_engine,
        h_seed,
        _structured(start=start, expires=expires, participants=(2, 4)),
        vec=E1,
    )
    # 候補20件: ベクトル(1.0, i*0.08)でsimilarityを散らす(i小=近い=cheap上位)
    cands: dict[int, dict] = {}
    for i in range(1, 21):
        h, _ = await _user(api_client, field)
        cands[i] = await _intent(
            api_client,
            db_engine,
            h,
            _structured(start=start, expires=expires, participants=(2, 4)),
            vec=_vec(1.0, i * 0.08),
        )
    group = _group_engine(db_engine, clock)
    with caplog.at_level(logging.INFO, logger=ge_mod.__name__):
        t0 = time.monotonic()
        ctx = await group.handle(uuid_mod.UUID(seed["id"]))
        elapsed = time.monotonic() - t0
    assert any(
        "group pool built" in r.message and "pool_size=15" in r.message
        for r in caplog.records
    )
    # Pool上位15 = i 1..15 → 集合は{種, i1, i2}(3人打ち切り)
    assert ctx is not None
    gc = await _group_of(db_engine, [seed["id"], cands[1]["id"], cands[2]["id"]])
    assert gc is not None, "Pool上位2件+種の集合が生成されていない"
    # Pool外(i16..20)とのペア行なし・group_candidatesに現れない
    for i in (16, 20):
        assert await _pair_row(db_engine, seed["id"], cands[i]["id"]) is None
    all_gids = [str(x) for x in gc[2]["versions"]]
    for i in (16, 20):
        assert cands[i]["id"] not in all_gids
    # HNSW 2回(1対1Layer2+Pool)の実行時間を記録(design §5-13・06 §1 ≤2秒)
    print(f"\n[ws7 test2] group.handle elapsed={elapsed:.3f}s (HNSW 2回含む)")


async def test_3_incomplete_group_continues_next_eval(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """未判定ペアの保留と継続優先(引用#6規則3・4・Review Focus 4)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ids: list[str] = []
    # 全員min=4: 貪欲法は3人で確定しない(design §2.3手順4)→4人集合(6ペア)を強制
    for _ in range(4):
        h, _ = await _user(api_client, field)
        it = await _intent(
            api_client,
            db_engine,
            h,
            _structured(start=start, expires=expires, participants=(4, 4)),
        )
        ids.append(it["id"])
    a, g1, g2, g3 = ids

    group = _group_engine(db_engine, clock)
    ctx = await group.handle(uuid_mod.UUID(a))
    assert ctx is not None
    gc = await _group_of(db_engine, ids)
    assert gc is not None and gc[1] == "candidate"

    # 1イベント(種起点のみ)で6ペア揃えない: 種起点が選べる種×メンバー3ペアの
    # うち2件のみ評価(3件目はllm_failureでskipped=再選択可能)→ 未判定4件残る
    store = JevCostStore(redis_client, key_prefix=redis_sweep)
    flaky = _FailAfterJev(_jev_response(0.9, 0.85), 2)
    jev_limited = JevWorker(
        engine=db_engine,
        clock=clock,
        gateway=LLMGateway(
            clock=clock,
            parser=StubLLM(),
            embedding=StubLLM(),
            jev=flaky,
            jev_fallback=flaky,
        ),
        guard=JevCostGuard(store=store, clock=clock),
        cost_store=store,
    )
    await jev_limited.handle(uuid_mod.UUID(a), ctx)
    async with db_engine.connect() as conn:
        n_eval = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM match_candidates"
                    " WHERE jev_result IS NOT NULL"
                    " AND (intent_a_id = ANY(ARRAY[CAST(:a AS uuid),"
                    " CAST(:b AS uuid), CAST(:c AS uuid), CAST(:d AS uuid)]::uuid[]))"
                    " AND (intent_b_id = ANY(ARRAY[CAST(:a AS uuid),"
                    " CAST(:b AS uuid), CAST(:c AS uuid), CAST(:d AS uuid)]::uuid[]))"
                ),
                {"a": a, "b": g1, "c": g2, "d": g3},
            )
        ).scalar_one()
    assert n_eval == 2, "guard制限で2ペアのみ評価される想定"
    # 未判定ペアが残る集合は提案化しない(引用#4)
    await group.finalize(uuid_mod.UUID(a))
    gc2 = await _group_of(db_engine, ids)
    assert gc2 is not None and gc2[1] == "candidate"
    assert await _group_latch_of(db_engine, ids) is None

    # 次評価サイクル(全メンバー起点・group_ctx=None=継続扱い): 未判定ペアが
    # 最優先で消化され全ペア揃う → 提案化
    jev_full = _jev_worker(db_engine, clock, redis_client, redis_sweep, 0.9, 0.85)
    await _jev_eval_all(jev_full, a, [g1, g2, g3], None)
    await group.finalize(uuid_mod.UUID(a))
    gc3 = await _group_of(db_engine, ids)
    assert gc3 is not None and gc3[1] == "proposed"
    latch = await _group_latch_of(db_engine, ids)
    assert latch is not None and latch[1] == "proposed"


async def test_4_kj_mixed_allocation(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """K_j配分の混在(引用#6): 1対1上位4件が先・残り枠がグループへ。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    h_a, _ = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        h_a,
        _structured(start=start, expires=expires, participants=(2, 4)),
    )
    # 1対1相手6件(min2max2・Layer1の人数条件双方)
    p_ids: list[str] = []
    for _ in range(6):
        h, _ = await _user(api_client, field)
        p_ids.append(
            (
                await _intent(
                    api_client,
                    db_engine,
                    h,
                    _structured(start=start, expires=expires, participants=(2, 2)),
                )
            )["id"]
        )
    # グループ候補3件(min4max4: 3人打ち切りを回避し4人集合{a,G1..3}を強制。
    # a×Gは1対1Layer1の人数条件を満たさない→1対1候補とはならない)
    g_ids: list[str] = []
    for _ in range(3):
        h, _ = await _user(api_client, field)
        g_ids.append(
            (
                await _intent(
                    api_client,
                    db_engine,
                    h,
                    _structured(start=start, expires=expires, participants=(4, 4)),
                )
            )["id"]
        )

    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    group = _group_engine(db_engine, clock)
    ctx = await group.handle(uuid_mod.UUID(a["id"]))
    assert ctx is not None
    jev = _jev_worker(db_engine, clock, redis_client, redis_sweep, 0.9, 0.85)
    await jev.handle(uuid_mod.UUID(a["id"]), ctx)
    # jev_resultの入った行を集合所属で分類。種起点1イベントの選択対象は
    # 1対1(a×P1..6)とグループ(a×G1..3・種×メンバー。メンバー相互のg1×g2等は
    # 各メンバー起点の評価で揃る)。配分: 1対1上位4→残り枠へグループ3件→
    # 繰上げで1対1+1 = 1対1 5件+グループ 3件の計8(K_j)
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT"
                    " EXISTS (SELECT 1 FROM group_candidates g"
                    "  WHERE g.status IN ('candidate','proposed')"
                    "  AND g.intent_ids @> ARRAY[mc.intent_a_id, mc.intent_b_id])"
                    " FROM match_candidates mc WHERE mc.jev_result IS NOT NULL"
                    " AND (mc.intent_a_id = CAST(:o AS uuid)"
                    " OR mc.intent_b_id = CAST(:o AS uuid))"
                ),
                {"o": a["id"]},
            )
        ).all()
    kinds = [bool(r[0]) for r in rows]
    assert len(rows) == 8  # K_j=8
    assert sum(1 for k in kinds if not k) == 5  # 1対1上位4+繰上げ1
    assert sum(1 for k in kinds if k) == 3  # 残り枠へグループ(a×G1..3)


async def test_5_participants_bounds(api_client, db_engine, field):
    """4人確定と人数不成立: 種min=4は3人では確定せず・min=5はPoolに入らない。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    h_s, _ = await _user(api_client, field)
    seed = await _intent(
        api_client,
        db_engine,
        h_s,
        _structured(start=start, expires=expires, participants=(4, 4)),
    )
    c_ids: list[str] = []
    for _ in range(3):
        h, _ = await _user(api_client, field)
        c_ids.append(
            (
                await _intent(
                    api_client,
                    db_engine,
                    h,
                    _structured(start=start, expires=expires, participants=(2, 4)),
                )
            )["id"]
        )
    # min=5/max=6のIntent(APIは(2,4)で作り直接UPDATE — Pool検索(min<=4)で除外)
    h_x, _ = await _user(api_client, field)
    x = await _intent(
        api_client,
        db_engine,
        h_x,
        _structured(start=start, expires=expires, participants=(2, 4)),
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET participants_min = 5, participants_max = 6"
                " WHERE id = CAST(:i AS uuid)"
            ),
            {"i": x["id"]},
        )

    group = _group_engine(db_engine, clock)
    ctx = await group.handle(uuid_mod.UUID(seed["id"]))
    assert ctx is not None
    # (1) 種min=4 → 4人集合確定
    gc = await _group_of(db_engine, [seed["id"], *c_ids])
    assert gc is not None, "min4種+候補3件の4人集合が生成されていない"
    assert gc[1] == "candidate"
    # (2) min=5/max=6 はPool検索(min<=4 AND max>=3)で除外
    assert await _pair_row(db_engine, seed["id"], x["id"]) is None
    async with db_engine.connect() as conn:
        n_x_groups = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM group_candidates"
                    " WHERE intent_ids @> ARRAY[CAST(:i AS uuid)]"
                ),
                {"i": x["id"]},
            )
        ).scalar_one()
    assert n_x_groups == 0


async def test_6_d06_top1_notification(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """D-06上位1集合(引用#8): メンバー重複2集合→proposedはaggregate上位1件のみ。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    # 決定的な2集合(距離でPool分離): a,b,c=天文館クラスタ・d,e=東約2.9km。
    # bだけ半径5km(橋)→a種のPool={b,c}(d,eは距離超過)・d種のPool={b,e}。
    # 生成集合は{a,b,c}(高位)と{d,b,e}(低位)の2つ・共有メンバーbで重複
    # (貪欲法の同点順はuuid順だが、Pool候補が確定2件ずつなので順序に依存しない)
    ids: list[str] = []
    for _ in range(5):  # a=種(高位), b=橋, c, d=種(低位), e
        h, _ = await _user(api_client, field)
        ids.append(
            (
                await _intent(
                    api_client,
                    db_engine,
                    h,
                    _structured(start=start, expires=expires, participants=(2, 4)),
                )
            )["id"]
        )
    a, b, c, d, e = ids
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE intents SET geo_radius_m = 5000 WHERE id = CAST(:i AS uuid)"),
            {"i": b},
        )
        await conn.execute(
            text(
                "UPDATE intents SET geo_center ="
                " ST_SetSRID(ST_MakePoint("
                "ST_X(geo_center::geometry) + 0.03, ST_Y(geo_center::geometry)"
                "), 4326)::geography"
                " WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [d, e]},
        )
    group = _group_engine(db_engine, clock)
    await group.handle(uuid_mod.UUID(a))  # Pool={b,c} → {a,b,c}
    await group.handle(uuid_mod.UUID(d))  # Pool={b,e} → {d,b,e}
    gc_hi = await _group_of(db_engine, [a, b, c])
    gc_lo = await _group_of(db_engine, [d, b, e])
    assert gc_hi is not None and gc_lo is not None

    # jev値を直接UPDATEで差をつける(2集合に共有ペアなし・b経由ペアのみ重複メンバー)
    await _seed_jev_pair(db_engine, a, b, 0.9, 0.9)
    await _seed_jev_pair(db_engine, a, c, 0.9, 0.9)
    await _seed_jev_pair(db_engine, b, c, 0.9, 0.9)
    await _seed_jev_pair(db_engine, d, b, 0.82, 0.82)
    await _seed_jev_pair(db_engine, d, e, 0.82, 0.82)
    await _seed_jev_pair(db_engine, b, e, 0.82, 0.82)
    await group.finalize(uuid_mod.UUID(a))  # {a,b,c} aggregate=0.9
    await group.finalize(uuid_mod.UUID(d))  # {d,b,e} aggregate=0.82
    hi = await _group_latch_of(db_engine, [a, b, c])
    lo = await _group_latch_of(db_engine, [d, b, e])
    assert hi is not None and lo is not None  # 両方candidateとしては作成
    assert hi[1] == "proposed"  # 上位1集合のみproposed化
    assert lo[1] == "candidate"  # 重複上位あり(b共有)→candidateのまま
    # 上位を閉じた後のdrain(LatchEngine.handle再実行)で第2集合がproposed化
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE latches SET status = 'expired' WHERE id = CAST(:l AS uuid)"),
            {"l": str(hi[0])},
        )
    latch_engine = LatchEngine(engine=db_engine, clock=clock, geo=GeoService(db_engine))
    await latch_engine.handle(uuid_mod.UUID(d))  # lo={d,b,e}はd起点のdrainで回収
    lo2 = await _group_latch_of(db_engine, [d, b, e])
    assert lo2 is not None and lo2[1] == "proposed"


async def test_7_visibility_branch(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """visibility分岐(引用#14): hidden_until_match混在はheadcount+match_levelのみ。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ids: list[str] = []
    visibilities = ("summary_only", "hidden_until_match", "summary_only")
    for v in visibilities:
        h, _ = await _user(api_client, field)
        ids.append(
            (
                await _intent(
                    api_client,
                    db_engine,
                    h,
                    _structured(
                        start=start,
                        expires=expires,
                        visibility=v,
                        participants=(2, 4),
                    ),
                )
            )["id"]
        )
    a, b, c = ids
    group = _group_engine(db_engine, clock)
    ctx = await group.handle(uuid_mod.UUID(a))
    assert ctx is not None
    jev = _jev_worker(db_engine, clock, redis_client, redis_sweep, 0.9, 0.9)
    await _jev_eval_all(jev, a, [b, c], ctx)
    await group.finalize(uuid_mod.UUID(a))
    latch = await _group_latch_of(db_engine, ids)
    assert latch is not None and latch[1] == "proposed"
    assert set(latch[3]) == {"headcount", "match_level"}
    assert latch[3]["headcount"] == 3


async def test_8_delete_event_closes_group(api_client, db_engine, field):
    """削除Eventで集合close(引用#15): stage1の_CLOSE_GROUPS。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ids: list[str] = []
    headers_of: dict[str, dict] = {}
    for _ in range(3):
        h, _ = await _user(api_client, field)
        it = await _intent(
            api_client,
            db_engine,
            h,
            _structured(start=start, expires=expires, participants=(2, 4)),
        )
        ids.append(it["id"])
        headers_of[it["id"]] = h
    a, b, c = ids
    group = _group_engine(db_engine, clock)
    assert await group.handle(uuid_mod.UUID(a)) is not None
    gc = await _group_of(db_engine, ids)
    assert gc is not None and gc[1] == "candidate"

    # メンバーbを削除(API・認証付き) → Stage1へ削除Eventを直接投入
    resp = await api_client.delete(f"/v1/intents/{b}", headers=headers_of[b])
    assert resp.status_code in (200, 204), resp.text
    from latch.events import IncomingEvent

    payload = json.dumps(
        {"event_type": "deleted", "source_intent_id": b, "version": 1}
    ).encode()
    event = IncomingEvent.from_payload(
        message_id=f"deleted-{b}", payload=payload, ack=lambda: None
    )
    stage1 = Stage1(engine=db_engine, clock=clock, settings=Settings())
    result = await stage1.intake(event)
    assert result.kind == "processed"

    gc2 = await _group_of(db_engine, ids)
    assert gc2 is not None and gc2[1] == "closed"
    # 構成ペア行は _CLOSE_CANDIDATES の既存動作どおりclosed
    row = await _pair_row(db_engine, a, b)
    assert row is not None and row[1] == "closed"


async def test_9_idempotent_double_handle(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """冪等性: 同一handle 2回でgroup_candidates二重なし・latches二重なし
    (design §5-12のON CONFLICT推論の実証・Review Focus 1)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ids: list[str] = []
    for _ in range(3):
        h, _ = await _user(api_client, field)
        ids.append(
            (
                await _intent(
                    api_client,
                    db_engine,
                    h,
                    _structured(start=start, expires=expires, participants=(2, 4)),
                )
            )["id"]
        )
    a, b, c = ids
    group = _group_engine(db_engine, clock)
    ctx1 = await group.handle(uuid_mod.UUID(a))
    assert ctx1 is not None
    jev = _jev_worker(db_engine, clock, redis_client, redis_sweep, 0.9, 0.85)
    await _jev_eval_all(jev, a, [b, c], ctx1)
    await group.finalize(uuid_mod.UUID(a))
    # 2回目: handle×2・finalize×2・全起点再評価(at-least-once再実行)
    await group.handle(uuid_mod.UUID(a))
    await _jev_eval_all(jev, a, [b, c], None)
    await group.finalize(uuid_mod.UUID(a))

    arr = ", ".join(f"CAST('{i}' AS uuid)" for i in sorted(ids, key=str))
    async with db_engine.connect() as conn:
        n_groups = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM group_candidates"
                    f" WHERE intent_ids = ARRAY[{arr}]::uuid[]"
                )
            )
        ).scalar_one()
        n_latches = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM latches"
                    f" WHERE intent_ids = ARRAY[{arr}]::uuid[]"
                )
            )
        ).scalar_one()
        latch_id = (
            await conn.execute(
                text(
                    "SELECT id FROM latches"
                    f" WHERE intent_ids = ARRAY[{arr}]::uuid[] LIMIT 1"
                )
            )
        ).scalar_one()
        n_candidate_events = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM latch_status_events"
                    " WHERE latch_id = CAST(:l AS uuid)"
                    " AND from_status IS NULL AND to_status = 'candidate'"
                ),
                {"l": str(latch_id)},
            )
        ).scalar_one()
    assert n_groups == 1  # 0005部分UNIQUE + ON CONFLICT DO NOTHING
    assert n_latches == 1  # 0004部分UNIQUE
    assert n_candidate_events == 1  # candidateイベントは1回のみ


async def test_10_one_on_one_and_group_parallel(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """1対1とグループの並走: 両提案生成・グループペアのlatch_score NULL(引用#12)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    # 1対1: a(min2max2)×b(min2max2) / グループ: s(種)・c・d(min2max4)
    h, _ = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        h,
        _structured(start=start, expires=expires, participants=(2, 2)),
    )
    h, _ = await _user(api_client, field)
    b = await _intent(
        api_client,
        db_engine,
        h,
        _structured(start=start, expires=expires, participants=(2, 2)),
    )
    # グループ面々はmin=3: a(2,2)・b(2,2)との1対1Layer1が不成立になり、
    # a起点の1対1候補がa×bのみに確定(D-08同時3件上限の非決定的抑制を回避)。
    # 3人集合{ s,c,d }は max(min)=3 <= 3 で確定
    g_ids: list[str] = []
    for _ in range(3):  # s, c, d
        h, _ = await _user(api_client, field)
        g_ids.append(
            (
                await _intent(
                    api_client,
                    db_engine,
                    h,
                    _structured(start=start, expires=expires, participants=(3, 4)),
                )
            )["id"]
        )
    s, c, d = g_ids
    group = _group_engine(db_engine, clock)
    jev = _jev_worker(db_engine, clock, redis_client, redis_sweep, 0.9, 0.85)

    # FullChain相当(_run_post_retrieval と同一手順)×2起点
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    ctx_a = await group.handle(uuid_mod.UUID(a["id"]))
    assert ctx_a is None  # 起点 max=2 → グループ生成no-op(承認事項2)
    await jev.handle(uuid_mod.UUID(a["id"]), ctx_a)
    latch_engine = LatchEngine(engine=db_engine, clock=clock, geo=GeoService(db_engine))
    await latch_engine.handle(uuid_mod.UUID(a["id"]))  # 1対1{latch}生成(proposed)
    await group.finalize(uuid_mod.UUID(a["id"]))
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(s))
    ctx_s = await group.handle(uuid_mod.UUID(s))
    assert ctx_s is not None
    # グループ側は s(種)と c の2起点評価で全3ペア(s×c・s×d・c×d)が揃う
    await _jev_eval_all(jev, s, [c], ctx_s)
    await group.finalize(uuid_mod.UUID(s))

    # 1対1提案(2要素)とグループ提案(3要素)が両方存在
    # (latches.intent_idsはsorted正規化で格納されるため照会も sorted で — ws-6
    #  _latch_ofと同一規約。作成順のまま渡すとuuid順で非決定的に不一致する)
    lo_id, hi_id = sorted([a["id"], b["id"]], key=str)
    async with db_engine.connect() as conn:
        latch_11 = (
            await conn.execute(
                text(
                    "SELECT id, status, score FROM latches"
                    " WHERE intent_ids = ARRAY[CAST(:a AS uuid),"
                    " CAST(:b AS uuid)]::uuid[]"
                ),
                {"a": lo_id, "b": hi_id},
            )
        ).first()
    assert latch_11 is not None and latch_11[1] == "proposed"
    latch_g = await _group_latch_of(db_engine, g_ids)
    assert latch_g is not None and latch_g[1] == "proposed"
    # グループ構成ペア行(s×c・s×d・c×d)のlatch_score は NULL のまま
    for x, y in ((s, c), (s, d), (c, d)):
        row = await _pair_row(db_engine, x, y)
        assert row is not None and row[3] is None, "グループペアのlatch_score NULL"


async def test_11_pure_min3_group_e2e(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """純min=3集合のE2E下地(設計補完・supervisor裁定): 3名ともmin=3/max=4で
    集合生成→JevWorkerのfallback起点読取→全ペア評価→集約→latches生成まで。
    min>=3起点はload_originのSKIP_PARTICIPANTSからload_group_originへfallback
    して評価を開始する(Important-1の実DB証明・07 §2「3人以上なら」)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ids: list[str] = []
    for _ in range(3):
        h, _ = await _user(api_client, field)
        ids.append(
            (
                await _intent(
                    api_client,
                    db_engine,
                    h,
                    _structured(start=start, expires=expires, participants=(3, 4)),
                )
            )["id"]
        )
    a, b, c = ids
    group = _group_engine(db_engine, clock)
    ctx = await group.handle(uuid_mod.UUID(a))
    assert ctx is not None  # 種a(max=4>=3)で集合{a,b,c}が生成される
    gc = await _group_of(db_engine, ids)
    assert gc is not None and gc[1] == "candidate"
    # JevWorker: a/b/c起点ともload_originはSKIP_PARTICIPANTS(min=3)→
    # load_group_originへfallbackして評価が走る
    jev = _jev_worker(db_engine, clock, redis_client, redis_sweep, 0.9, 0.85)
    await _jev_eval_all(jev, a, [b, c], ctx)
    for x, y in ((a, b), (a, c), (b, c)):
        row = await _pair_row(db_engine, x, y)
        assert row is not None and row[2] is not None, "fallback起点で全ペア評価"
    # 集約 → latches生成(candidate→proposed)
    await group.finalize(uuid_mod.UUID(a))
    gc2 = await _group_of(db_engine, ids)
    assert gc2 is not None and gc2[1] == "proposed"
    latch = await _group_latch_of(db_engine, ids)
    assert latch is not None and latch[1] == "proposed"
    assert float(latch[2]) == 0.85  # min(0.9,0.85)×C


async def test_promotion_overwrites_group_candidate_id(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """世代交代昇格: 開いているlatches行へ昇格時gidが新世代へ書き換わる(§2.10)。

    旧世代(gid_old・closed)が残したcandidate行latchesへ、同一メンバーの
    新世代集合(gid_new)のfinalizeがON CONFLICT昇格する。
    """
    import json as json_mod

    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    members: list[dict] = []
    for _ in range(4):
        h, _s = await _user(api_client, field)
        members.append(
            await _intent(
                api_client,
                db_engine,
                h,
                _structured(
                    start=start, expires=expires, budget_max=4000, participants=(2, 4)
                ),
            )
        )
    ids = [m["id"] for m in members]
    now = SystemClock().now()
    ms = {"seed_id": ids[0], "versions": {i: 1 for i in ids}}
    async with db_engine.begin() as conn:
        # 旧世代: 閉じた集合+candidatesのまま残っているlatches行
        res_old = await conn.execute(
            text("""
                INSERT INTO group_candidates
                    (intent_ids, member_scores, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:ms AS jsonb), 'closed',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in ids],
                "ms": json_mod.dumps(ms),
                "now": now,
            },
        )
        gid_old = str(res_old.first()[0])
        await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, group_candidate_id, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:gid AS uuid),
                        CAST(:proposal AS jsonb), :score, 'candidate',
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in ids],
                "gid": gid_old,
                "proposal": json_mod.dumps({"headcount": 4, "match_level": "low"}),
                "score": 0.5,
                "deadline": now + timedelta(hours=2),
                "expires": now + timedelta(days=5),
                "now": now,
            },
        )
        # 新世代: candidate集合(0005部分UNIQUEは旧がclosedのため共存可)
        res_new = await conn.execute(
            text("""
                INSERT INTO group_candidates
                    (intent_ids, member_scores, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:ms AS jsonb), 'candidate',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in ids],
                "ms": json_mod.dumps(ms),
                "now": now,
            },
        )
        gid_new = str(res_new.first()[0])
        # 全6ペアのjev_result直書き(世代=現行version 1)
        jev = {
            "would_a_accept_b": 0.9,
            "would_b_accept_a": 0.85,
            "jev_5axis": {},
            "provider": "fixture",
            "model": None,
        }
        for x in range(4):
            for y in range(x + 1, 4):
                await conn.execute(
                    text("""
                        INSERT INTO match_candidates
                            (intent_a_id, intent_b_id, intent_a_version,
                             intent_b_version, retrieval_score,
                             cheap_judge_score, jev_result, status,
                             created_at, updated_at)
                        VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 1, 1, 0.9,
                                0.9, CAST(:jev AS jsonb), 'evaluated',
                                CAST(:now AS timestamptz),
                                CAST(:now AS timestamptz))
                    """),
                    {
                        "a": uuid_mod.UUID(ids[x]),
                        "b": uuid_mod.UUID(ids[y]),
                        "jev": json_mod.dumps(jev),
                        "now": now,
                    },
                )
    group = _group_engine(db_engine, clock)
    await group.finalize(uuid_mod.UUID(ids[0]))
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, group_candidate_id FROM latches"
                    " WHERE intent_ids = CAST(:ids AS uuid[])"
                ),
                {"ids": [uuid_mod.UUID(i) for i in ids]},
            )
        ).first()
    assert row is not None
    assert row[0] in ("candidate", "proposed")  # try_promote後proposed
    assert str(row[1]) == gid_new  # 旧gid_old → 新gid_newへ書き換わる(§2.10)
