"""GroupEngine.handle/finalizeのunit試験(design §4.1)。DB操作はgroup_engineの
モジュール関数をmonkeypatchして分岐ロジックを検証(runnerと同一規律)。
SQL文字列はtext()定数への直接ピンで検証する。"""

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import NamedTuple

from latch.core.clock import FakeClock
from latch.worker.matching import group_engine as ge
from latch.worker.matching.group_engine import (
    SKIP_GROUP_ORIGIN_MAX,
    GroupEngine,
    load_group_origin,
)
from latch.worker.matching.origin import Origin, OriginLoad

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
START = NOW + timedelta(hours=30)  # 将来窓(75分ルール回避)

_DEFAULT = object()  # insert_group_result「未指定」のsentinel


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


def _origin(n: int = 1, pmin: int = 2, pmax: int = 4) -> Origin:
    return Origin(
        intent_id=_uid(n),
        version=1,
        user_id=_uid(n + 1),
        category_primary="drinking",
        alcohol_involved=False,
        budget_max=5000,
        participants_min=pmin,
        participants_max=pmax,
        geo_lon=130.55,
        geo_lat=31.59,
        geo_radius_m=2000,
        time_start=START,
        time_end=START + timedelta(hours=3),
        embedding="[0.1, 0.2]",
        soft_texts=(),
        user_ge_20=True,
        evaluated_at=NOW,
    )


class _PoolRow(NamedTuple):
    """_POOL_SEARCH戻り相当(§9-4手順2のSELECT列順)。"""

    intent_id: uuid.UUID
    version: int
    user_id: uuid.UUID
    participants_min: int
    participants_max: int
    time_start: datetime
    budget_max: int | None
    structured_data: dict
    similarity: float


def _pool_row(
    n: int, *, pmin: int = 2, pmax: int = 4, similarity: float = 0.9, budget: int = 5000
) -> _PoolRow:
    return _PoolRow(
        intent_id=_uid(n),
        version=1,
        user_id=_uid(1000 + n),
        participants_min=pmin,
        participants_max=pmax,
        time_start=START,
        budget_max=budget,
        structured_data={
            "location_name": "天文館",
            "soft_constraints": [{"text": "静かな場所", "downgraded_from_ng": False}],
        },
        similarity=similarity,
    )


class _FakeEngine:
    """engine.begin()がconn番号つきフェイクtxを返す(呼出順・同一tx検証用)。"""

    def __init__(self):
        self._n = 0

    def begin(self):
        self._n += 1
        return _FakeTx(self._n)


class _FakeTx:
    def __init__(self, n):
        self.n = n  # conn識別子(スタブの呼出順記録に使う)

    async def __aenter__(self):
        return self  # conn本体はmonkeypatchした関数が受けない

    async def __aexit__(self, *exc):
        return False


def _engine() -> GroupEngine:
    return GroupEngine(engine=_FakeEngine(), clock=FakeClock(NOW))


def _full_pair_info(ids: list[uuid.UUID], sim: float = 0.9, cheap: float = 0.8) -> dict:
    """全組み合わせを互換(sim/cheap固定)とする互換行列。"""
    out: dict = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            key = (a, b) if a < b else (b, a)
            out[key] = (sim, cheap)
    return out


def _patch_group(
    monkeypatch,
    *,
    org: Origin | None = None,
    skip_reason: str | None = None,
    pool_rows: list[_PoolRow] | None = None,
    pair_info: dict | None = None,
    insert_group_result=_DEFAULT,
):
    """GroupEngine.handleのDB部品を記録スタブへ差し替え(test_worker_jev流儀)。"""
    if pool_rows is None:
        pool_rows = []
    log = {
        "pool": [],
        "compat": [],
        "upserts": [],
        "insert_groups": [],
        "versions": [],
    }
    upsert_n = [0]

    async def fake_load(conn, clock, intent_id):
        if skip_reason is not None:
            return OriginLoad(origin=None, skip_reason=skip_reason)
        return OriginLoad(origin=org, skip_reason=None)

    async def fake_pool_search(conn, origin):
        log["pool"].append(origin.intent_id)
        return list(pool_rows)

    async def fake_pair_compat(conn, ids, now):
        log["compat"].append((tuple(ids), now))
        if pair_info is None:
            return _full_pair_info(list(ids))
        return pair_info

    async def fake_upsert_pair(conn, **kw):
        log["upserts"].append(
            (
                kw["intent_a_id"],
                kw["intent_b_id"],
                kw["intent_a_version"],
                kw["intent_b_version"],
            )
        )
        upsert_n[0] += 1
        return _uid(5000 + upsert_n[0])

    async def fake_insert_group(conn, *, ids, member_scores, now):
        log["insert_groups"].append((tuple(ids), member_scores, now))
        if insert_group_result is _DEFAULT:
            return _uid(9000)
        return insert_group_result

    async def fake_select_versions(conn, ids):
        log["versions"].append(tuple(ids))
        return [
            (i, 1, _uid(1000 + int(i.node)), 2, 4)
            for i in ids  # user_id相異
        ]

    monkeypatch.setattr(ge, "load_group_origin", fake_load)
    monkeypatch.setattr(ge, "_pool_search", fake_pool_search)
    monkeypatch.setattr(ge, "_pair_compat", fake_pair_compat)
    monkeypatch.setattr(ge.candidates, "upsert_pair", fake_upsert_pair)
    monkeypatch.setattr(ge, "_insert_group", fake_insert_group)
    monkeypatch.setattr(ge, "_select_group_versions", fake_select_versions)
    return log


# --- 起点読取(Review Focus 2・承認事項2) ---


class _FakeMappings:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return _FakeMappings(self._row)


class StubConn:
    """load_group_originが1回呼ぶexecuteへスタブ行を返す。"""

    def __init__(self, row):
        self.row = row

    async def execute(self, stmt, params=None):
        return _FakeResult(self.row)


def _origin_row(**overrides):
    """_SELECT_GROUP_ORIGIN相当の標準行(test_origin.pyの_rowと同一構成)。"""
    base = dict(
        id=str(_uid(1)),
        version=1,
        user_id=str(_uid(2)),
        category_primary="drinking",
        alcohol_involved=False,
        budget_max=5000,
        participants_min=2,
        participants_max=4,
        geo_radius_m=None,
        time_start=START,
        time_end=None,
        structured_data=None,
        status="active",
        embedding="[1.0,0.0]",
        lon=130.5581,
        lat=31.5965,
        birth_date=date(1990, 4, 1),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_min3_origin_passes_origin_gate():
    """min=3/max=4の起点はskipされない(Review Focus 2・07 §2「3人以上なら」)。"""
    loaded = await load_group_origin(
        StubConn(_origin_row(participants_min=3, participants_max=4)),
        FakeClock(NOW),
        _uid(1),
    )
    assert loaded.skip_reason is None
    assert loaded.origin is not None
    assert loaded.origin.participants_min == 3


async def test_origin_max_lt_3_gate_returns_skip():
    """max=2の起点はSKIP_GROUP_ORIGIN_MAX(種になれない・承認事項2)。"""
    loaded = await load_group_origin(
        StubConn(_origin_row(participants_min=2, participants_max=2)),
        FakeClock(NOW),
        _uid(1),
    )
    assert loaded.origin is None
    assert loaded.skip_reason == SKIP_GROUP_ORIGIN_MAX
    assert SKIP_GROUP_ORIGIN_MAX == "origin_not_group"


# --- handleのno-op分岐 ---


async def test_origin_not_found_noop(monkeypatch):
    """起点skip(不在)→handleはNone・Pool検索しない。"""
    log = _patch_group(monkeypatch, skip_reason="origin_not_found")
    out = await _engine().handle(_uid(1))
    assert out is None
    assert log["pool"] == []


async def test_origin_max_lt_3_noop(monkeypatch):
    """起点(max=2)→SKIP_GROUP_ORIGIN_MAXでno-op(承認事項2のトリガー)。"""
    log = _patch_group(monkeypatch, skip_reason=SKIP_GROUP_ORIGIN_MAX)
    out = await _engine().handle(_uid(1))
    assert out is None
    assert log["pool"] == []


async def test_empty_pool_noop(monkeypatch, caplog):
    """Pool 0件→None・「group pool empty」経路。"""
    log = _patch_group(monkeypatch, org=_origin(1), pool_rows=[])
    with caplog.at_level("INFO", logger="latch.worker.matching.group_engine"):
        out = await _engine().handle(_uid(1))
    assert out is None
    assert log["pool"] == [_uid(1)]
    assert any("group pool empty" in r.message for r in caplog.records)


# --- SQLピン ---


def test_pool_search_sql_pins():
    from sqlalchemy.dialects import postgresql

    from latch.worker.matching import layer1

    sql = str(ge._POOL_SEARCH)
    assert layer1.LAYER1_WHERE_BASE in sql
    assert "i.participants_min <= 4" in sql
    assert "i.participants_max >= 3" in sql
    assert "i.time_start >= CAST(:bucket_start AS timestamptz)" in sql
    assert "i.time_start < CAST(:bucket_end AS timestamptz)" in sql
    assert "LIMIT 50" in sql
    assert "ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC," in sql
    assert " i.id ASC" in sql
    compiled = str(ge._POOL_SEARCH.compile(dialect=postgresql.dialect()))
    for key in (
        "bucket_start",
        "bucket_end",
        "origin_embedding",
        "origin_user_id",
        "origin_category",
        "origin_time_start",
        "origin_time_end",
        "origin_lon",
        "origin_lat",
        "origin_radius_m",
        "origin_budget",
        "origin_alcohol",
        "origin_user_ge_20",
        "now",
    ):
        assert f":{key}" not in compiled, key


def test_pair_compat_sql_pins():
    sql = str(ge._PAIR_COMPAT)
    assert "i1.id < i2.id" in sql
    assert "ST_DWithin" in sql
    assert "i1.user_id <> i2.user_id" in sql
    assert "NOT EXISTS" in sql and "blocks" in sql
    assert sql.count("EXTRACT(YEAR FROM AGE(") == 2  # 飲酒年齢は両者
    assert sql.count("ANY(CAST(:ids AS uuid[]))") == 2


def test_insert_group_sql_pins():
    sql = str(ge._INSERT_GROUP)
    assert "ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed')" in sql
    assert "DO NOTHING" in sql
    assert "RETURNING id" in sql
    assert "'candidate'" in sql
    assert "CAST(:ids AS uuid[])" in sql
    assert "CAST(:member_scores AS jsonb)" in sql


# --- Pool構築 ---


async def test_pool_top15_cheap_desc_intent_id_asc(monkeypatch, caplog):
    """Pool上限15(D-24): 20件→15件・cheap降順(similarity降順と一致)。"""
    rows = [_pool_row(n, similarity=0.5 + n * 0.01) for n in range(1, 21)]
    log = _patch_group(
        monkeypatch,
        org=_origin(1),
        pool_rows=rows,
        pair_info=_full_pair_info([_uid(1), *[r.intent_id for r in rows]]),
    )
    with caplog.at_level("INFO", logger="latch.worker.matching.group_engine"):
        await _engine().handle(_uid(1))
    assert any(
        "group pool built" in r.message and "pool_size=15" in r.message
        for r in caplog.records
    )
    # upsert(4b)はPool上位15件のみ(similarity上位=nが大きい側)
    pool_upserts = {u[1] for u in log["upserts"][:15]}
    assert _uid(6) in pool_upserts  # similarity 0.56(n=6)
    assert _uid(20) in pool_upserts  # similarity 0.70(最高)
    assert _uid(1) not in pool_upserts  # 起点自身
    # 全upsertのうち起点×Poolペアは15件
    assert len(pool_upserts) == 15


# --- 集合生成 ---


async def test_handle_builds_group_with_seed_origin(monkeypatch):
    """全互換2件 → 集合{起点,2,3}・member_scores={seed_id,versions}・sorted ids。"""
    log = _patch_group(
        monkeypatch, org=_origin(1), pool_rows=[_pool_row(2), _pool_row(3)]
    )
    out = await _engine().handle(_uid(1))
    assert out is not None
    ((ids, ms, now),) = log["insert_groups"]
    assert list(ids) == sorted([_uid(1), _uid(2), _uid(3)])  # sorted正規化
    assert ms == {
        "seed_id": str(_uid(1)),
        "versions": {str(_uid(1)): 1, str(_uid(2)): 1, str(_uid(3)): 1},
    }
    assert now == NOW  # org.evaluated_at


async def test_handle_upserts_pool_pairs_then_member_pairs(monkeypatch):
    """upsert順: Pool全員×起点(手順4b)→集合のメンバー間ペア(手順7-d・起点除く)。"""
    log = _patch_group(
        monkeypatch,
        org=_origin(1),
        pool_rows=[_pool_row(2), _pool_row(3), _pool_row(4)],
    )
    await _engine().handle(_uid(1))
    # 第1集合{1,2,3}で確定(3人打ち切り)。4b=Pool3行×起点 → 7-d={2,3}
    assert [(u[0], u[1]) for u in log["upserts"]] == [
        (_uid(1), _uid(2)),
        (_uid(1), _uid(3)),
        (_uid(1), _uid(4)),
        (_uid(2), _uid(3)),
    ]


async def test_handle_duplicate_group_skipped(monkeypatch, caplog):
    """_INSERT_GROUPがNone(0005部分UNIQUE競合)→スキップ・GroupContext.group_ids空。"""
    log = _patch_group(
        monkeypatch,
        org=_origin(1),
        pool_rows=[_pool_row(2), _pool_row(3)],
        insert_group_result=None,
    )
    with caplog.at_level("INFO", logger="latch.worker.matching.group_engine"):
        out = await _engine().handle(_uid(1))
    assert out is not None
    assert out.group_ids == frozenset()
    assert any("group duplicate skipped" in r.message for r in caplog.records)
    assert log["insert_groups"]  # INSERT自体は試行(ON CONFLICT DO NOTHING)


async def test_handle_user_conflict_skips_insert(monkeypatch):
    """user_id重複メンバー(05 §2のINSERT前検査)→_INSERT_GROUP不呼出。"""
    log = _patch_group(
        monkeypatch, org=_origin(1), pool_rows=[_pool_row(2), _pool_row(3)]
    )

    async def fake_versions(conn, ids):
        same_user = _uid(7777)
        return [
            (i, 1, same_user if i in (_uid(2), _uid(3)) else _uid(1000), 2, 4)
            for i in ids
        ]

    monkeypatch.setattr(ge, "_select_group_versions", fake_versions)
    out = await _engine().handle(_uid(1))
    assert out is not None
    assert log["insert_groups"] == []  # INSERT前検査でスキップ
    assert out.group_ids == frozenset()
    # 4bのPool×起点ペアは書かれている(手順4bは集合生成と独立)
    assert [u[:2] for u in log["upserts"]] == [(_uid(1), _uid(2)), (_uid(1), _uid(3))]


async def test_group_context_contents(monkeypatch):
    """GroupContext.group_ids・new_pair_row_ids(upsert_pair戻りidの集合)。"""
    _patch_group(monkeypatch, org=_origin(1), pool_rows=[_pool_row(2), _pool_row(3)])
    out = await _engine().handle(_uid(1))
    assert out.group_ids == frozenset({_uid(9000)})
    # 4bの2件 + 7-dの1件 = 3件(upsertスタブは5001..連番)
    assert out.new_pair_row_ids == frozenset({_uid(5001), _uid(5002), _uid(5003)})


# =====================================================================
# finalize(M2 ws-7・Task 8・design §2.5)
# =====================================================================


from latch.worker.matching.proposal import LatchIntentInputs  # noqa: E402


def _member_inputs(n: int, **over) -> LatchIntentInputs:
    base = dict(
        intent_id=_uid(n),
        user_id=_uid(n + 50),
        visibility="summary_only",
        notification_level="proposals_only",
        time_start=START,
        expires_at=NOW + timedelta(days=5),
        budget_max=5000,
        category_primary="drinking",
        category_secondary="ビアバー",
        geo_lon=130.558,
        geo_lat=31.596,
    )
    base.update(over)
    return LatchIntentInputs(**base)


def _pair_row(
    a: int, b: int, *, av: int = 1, bv: int = 1, wa: float = 0.9, wb: float = 0.85
):
    """_SELECT_GROUP_PAIRS相当の1行(jev_result=Noneは未判定)。"""
    a_id, b_id = _uid(a), _uid(b)
    lo, hi = sorted((a_id, b_id))
    return (
        _uid(6000 + a * 10 + b),
        lo,
        hi,
        av if a_id == lo else bv,
        bv if a_id == lo else av,
        None if wa is None else {"would_a_accept_b": wa, "would_b_accept_a": wb},
    )


def _pending_group(
    ids: list[uuid.UUID], *, gid: int = 9000, versions: int = 1, aggregate=None
):
    """_SELECT_PENDING_GROUPS相当の1行(member_scores.versionsは全員=versions)。"""
    return (
        _uid(gid),
        ids,
        {"seed_id": str(ids[0]), "versions": {str(i): versions for i in ids}},
        aggregate,
    )


def _patch_finalize(
    monkeypatch,
    *,
    org: Origin | None = None,
    skip_reason: str | None = None,
    groups=(),
    pair_rows=(),
    versions_map: dict | None = None,
    pair_info: dict | None = None,
    member_inputs: dict | None = None,
    responses=(),
    update_aggregate_result=_DEFAULT,
    insert_latch_result=_DEFAULT,
    find_open_result=None,
    d07_allow: bool = True,
):
    """finalizeのDB部品を記録スタブへ(txの呼出順検証用にconn番号も記録)。"""
    log = {
        "pending": [],
        "reset": [],
        "pair_rows": [],
        "compat": [],
        "close_group": [],
        "member_inputs": [],
        "responses": [],
        "update_aggregate": [],
        "insert_latch": [],
        "find_open": [],
        "update_promotion": [],
        "events": [],
        "mark_proposed": [],
        "promote": [],
        "tx": [],
    }
    promote_log = log

    class _LatchStub:
        async def try_promote(self, latch_id):
            promote_log["promote"].append(latch_id)

    async def fake_load(conn, clock, intent_id):
        if skip_reason is not None:
            return OriginLoad(origin=None, skip_reason=skip_reason)
        return OriginLoad(origin=org, skip_reason=None)

    async def fake_pending(conn, origin_id):
        log["pending"].append(origin_id)
        return list(groups)

    async def fake_versions(conn, ids):
        if versions_map is not None:
            return [(i, *versions_map[i]) for i in ids]
        return [(i, 1, _uid(1000 + int(i.node)), 2, 4) for i in ids]

    async def fake_reset(conn, gid, member_scores, now):
        log["reset"].append((gid, member_scores))

    async def fake_select_pairs(conn, ids):
        log["pair_rows"].append(tuple(ids))
        return list(pair_rows)

    async def fake_pair_compat(conn, ids, now):
        log["compat"].append(tuple(ids))
        if pair_info is None:
            return _full_pair_info(list(ids))
        return pair_info

    async def fake_close_group(conn, gid, now):
        log["close_group"].append(gid)

    async def fake_read_member_inputs(engine, intent_id):
        log["member_inputs"].append(intent_id)
        if member_inputs is None:
            return _member_inputs(int(intent_id.node))
        return member_inputs.get(intent_id)

    async def fake_responses(conn, ids):
        log["responses"].append(tuple(ids))
        return list(responses)

    async def fake_update_aggregate(conn, gid, score, now):
        log["tx"].append((getattr(conn, "n", -1), "update_aggregate"))
        log["update_aggregate"].append((gid, score, now))
        if update_aggregate_result is _DEFAULT:
            return (gid, None)
        return update_aggregate_result

    async def fake_insert_latch(conn, **kw):
        log["tx"].append((getattr(conn, "n", -1), "insert_latch"))
        log["insert_latch"].append(kw)
        if insert_latch_result is _DEFAULT:
            return _uid(9500)
        return insert_latch_result

    async def fake_find_open(conn, ids):
        log["tx"].append((getattr(conn, "n", -1), "find_open"))
        log["find_open"].append(tuple(ids))
        return find_open_result

    async def fake_update_promotion(conn, latch_id, *, score, proposal, deadline):
        log["tx"].append((getattr(conn, "n", -1), "update_promotion"))
        log["update_promotion"].append(latch_id)
        return True

    async def fake_event(conn, latch_id, from_status, to_status, user_id, now):
        log["tx"].append((getattr(conn, "n", -1), "event"))
        log["events"].append((latch_id, from_status, to_status))

    async def fake_mark_proposed(conn, gid, now):
        log["tx"].append((getattr(conn, "n", -1), "mark_proposed"))
        log["mark_proposed"].append(gid)

    def fake_d07(**kw):  # 本物のd07_allowsと同じく同期純関数
        return d07_allow

    monkeypatch.setattr(ge, "load_group_origin", fake_load)
    monkeypatch.setattr(ge, "_select_pending_groups", fake_pending)
    monkeypatch.setattr(ge, "_select_group_versions", fake_versions)
    monkeypatch.setattr(ge, "_reset_generation", fake_reset)
    monkeypatch.setattr(ge, "_select_group_pairs", fake_select_pairs)
    monkeypatch.setattr(ge, "_pair_compat", fake_pair_compat)
    monkeypatch.setattr(ge, "_close_group_by_id", fake_close_group)
    monkeypatch.setattr(ge, "_read_member_inputs", fake_read_member_inputs)
    monkeypatch.setattr(ge, "_select_group_latch_responses", fake_responses)
    monkeypatch.setattr(ge, "_update_aggregate", fake_update_aggregate)
    monkeypatch.setattr(ge, "_insert_group_latch", fake_insert_latch)
    monkeypatch.setattr(ge, "_find_open_group_latch", fake_find_open)
    monkeypatch.setattr(ge, "_update_group_latch_for_promotion", fake_update_promotion)
    monkeypatch.setattr(ge, "_insert_latch_event", fake_event)
    monkeypatch.setattr(ge, "_mark_group_proposed", fake_mark_proposed)
    monkeypatch.setattr(ge.latch_calc, "d07_allows", fake_d07)
    return log, _LatchStub()


_IDS3 = [_uid(1), _uid(2), _uid(3)]


async def test_finalize_origin_noop(monkeypatch):
    """起点skip→何もしない(pending読取なし)。"""
    log, _ = _patch_finalize(monkeypatch, skip_reason="origin_not_found")
    await _engine().finalize(_uid(1))
    assert log["pending"] == []


async def test_finalize_generation_reset_when_versions_diverge(monkeypatch):
    """versions不一致→_RESET_GENERATION(現行組)→現行組で続行(Review Focus 5)。"""
    # member_scores.versions=1・現行version=2 → リセット
    log, _ = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[_pending_group(_IDS3, versions=1)],
        versions_map={i: (2, _uid(1000 + n), 2, 4) for n, i in enumerate(_IDS3)},
        pair_rows=[
            _pair_row(1, 2, av=2, bv=2),
            _pair_row(1, 3, av=2, bv=2),
            _pair_row(2, 3, av=2, bv=2),
        ],
    )
    await _engine().finalize(_uid(1))
    assert [r[0] for r in log["reset"]] == [_uid(9000)]
    assert log["reset"][0][1] == {
        "seed_id": str(_uid(1)),
        "versions": {str(i): 2 for i in _IDS3},
    }
    assert log["update_aggregate"]  # リセット後に現行組で集約まで進む


async def test_finalize_incomplete_pairs_do_nothing(monkeypatch):
    """jev_result無しペアが1つでも→INSERT/UPDATEなし(status=candidate保持・引用#4)。"""
    log, _ = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[_pending_group(_IDS3)],
        pair_rows=[_pair_row(1, 2), _pair_row(1, 3), _pair_row(2, 3, wa=None)],
    )
    await _engine().finalize(_uid(1))
    assert log["update_aggregate"] == []
    assert log["insert_latch"] == []
    assert log["close_group"] == []


async def test_finalize_h_broken_closes_group(monkeypatch):
    """人数包含不合格(min=5メンバー)→_CLOSE_GROUP_BY_IDのみ・ペア行closeなし。"""
    log, _ = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[_pending_group(_IDS3)],
        versions_map={i: (1, _uid(1000 + n), 5, 6) for n, i in enumerate(_IDS3)},
        pair_rows=[_pair_row(1, 2), _pair_row(1, 3), _pair_row(2, 3)],
    )
    await _engine().finalize(_uid(1))
    assert log["close_group"] == [_uid(9000)]
    assert log["update_aggregate"] == []
    assert log["insert_latch"] == []


async def test_finalize_aggregate_and_latch_in_same_tx(monkeypatch):
    """全ペア揃い・score>=0.80→集約tx内でUPDATE→INSERT→イベント→MARKの順。"""
    log, _ = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[_pending_group(_IDS3)],
        pair_rows=[_pair_row(1, 2), _pair_row(1, 3), _pair_row(2, 3)],
    )
    await _engine().finalize(_uid(1))
    # 同一tx(conn番号同一)でこの順
    tx_seq = [name for _, name in log["tx"]]
    assert tx_seq == [
        "update_aggregate",
        "insert_latch",
        "event",
        "mark_proposed",
    ]
    conn_ids = {n for n, _ in log["tx"]}
    assert len(conn_ids) == 1  # 全部同一tx
    assert log["mark_proposed"] == [_uid(9000)]


async def test_finalize_threshold_boundary_060(monkeypatch):
    """mutualsのmin=0.60ちょうど→提案(score >= LATCH_THRESHOLDは等号付き・v0.6閾値)。"""
    log, _ = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[_pending_group(_IDS3)],
        pair_rows=[
            _pair_row(1, 2, wa=0.60, wb=0.9),
            _pair_row(1, 3, wa=0.9, wb=0.9),
            _pair_row(2, 3, wa=0.9, wb=0.9),
        ],
    )
    await _engine().finalize(_uid(1))
    assert log["update_aggregate"][0][1] == 0.60  # min=0.60ちょうど(v0.6閾値)
    assert log["insert_latch"]  # 提案化


async def test_finalize_below_threshold_keeps_candidate(monkeypatch):
    """score<0.60→_UPDATE_AGGREGATEのみ・INSERTなし(candidateのまま・v0.6閾値)。"""
    log, _ = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[_pending_group(_IDS3)],
        pair_rows=[
            _pair_row(1, 2, wa=0.59, wb=0.9),
            _pair_row(1, 3, wa=0.9, wb=0.9),
            _pair_row(2, 3, wa=0.9, wb=0.9),
        ],
    )
    await _engine().finalize(_uid(1))
    assert log["update_aggregate"]  # aggregate_scoreは入る
    assert log["insert_latch"] == []
    assert log["mark_proposed"] == []


async def test_finalize_d07_denied(monkeypatch, caplog):
    """d07_allows=False→_UPDATE_AGGREGATEのみ・「group d07 denied」ログ。"""
    import logging

    log, _ = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[_pending_group(_IDS3)],
        pair_rows=[_pair_row(1, 2), _pair_row(1, 3), _pair_row(2, 3)],
        d07_allow=False,
    )
    with caplog.at_level(logging.INFO, logger="latch.worker.matching.group_engine"):
        await _engine().finalize(_uid(1))
    assert log["update_aggregate"]
    assert log["insert_latch"] == []
    assert any("group d07 denied" in r.message for r in caplog.records)


async def test_finalize_calls_try_promote(monkeypatch):
    """INSERT成功→latch.try_promote(latch_id)呼出(latchスタブ)。"""
    log, latch = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[_pending_group(_IDS3)],
        pair_rows=[_pair_row(1, 2), _pair_row(1, 3), _pair_row(2, 3)],
    )
    engine = GroupEngine(engine=_FakeEngine(), clock=FakeClock(NOW), latch=latch)
    await engine.finalize(_uid(1))
    assert log["promote"] == [_uid(9500)]


async def test_finalize_read_failure_leaves_aggregate_null(monkeypatch):
    """_read_member_inputsがNone(expires NULL)→_UPDATE_AGGREGATE呼ばれず。"""
    log, _ = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[_pending_group(_IDS3)],
        pair_rows=[_pair_row(1, 2), _pair_row(1, 3), _pair_row(2, 3)],
        member_inputs={
            _uid(1): _member_inputs(1),
            _uid(2): None,
            _uid(3): _member_inputs(3),
        },
    )
    await _engine().finalize(_uid(1))
    assert log["update_aggregate"] == []  # aggregate NULLのまま(I-1対策)


async def test_finalize_aggregate_guard_skips_computed(monkeypatch):
    """aggregate_score計算済み行→_UPDATE_AGGREGATE戻り0行→何もしない(冪等)。"""
    log, _ = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[_pending_group(_IDS3, aggregate=0.85)],
        pair_rows=[_pair_row(1, 2), _pair_row(1, 3), _pair_row(2, 3)],
        update_aggregate_result=None,
    )
    await _engine().finalize(_uid(1))
    assert log["update_aggregate"]
    assert log["insert_latch"] == []
    assert log["mark_proposed"] == []
    assert log["promote"] == []


def test_insert_group_latch_sql_pins():
    sql = str(ge._INSERT_GROUP_LATCH)
    assert (
        "ON CONFLICT (intent_ids) WHERE status IN"
        " ('candidate', 'proposed', 'partial_accept')" in sql
    )
    assert "DO NOTHING" in sql
    assert "group_candidate_id" in sql
    assert "RETURNING id" in sql
    assert "CAST(:ids AS uuid[])" in sql
    assert "CAST(:gid AS uuid)" in sql


async def test_finalize_d06_order_processes_highest_first(monkeypatch):
    """D-06順の処理(設計補完): 読取順と無関係にaggregate高位→低位の順で
    集約tx+try_promoteを実行(try_promoteの上位チェックは作成済みlatchesを
    見るため、高位→低位で下位が正しく抑制される)。"""
    hi_ids = [_uid(1), _uid(2), _uid(3)]
    lo_ids = [_uid(4), _uid(5), _uid(6)]
    hi_rows = [
        _pair_row(1, 2, wa=0.9, wb=0.9),
        _pair_row(1, 3, wa=0.9, wb=0.9),
        _pair_row(2, 3, wa=0.9, wb=0.9),
    ]
    lo_rows = [
        _pair_row(4, 5, wa=0.82, wb=0.82),
        _pair_row(4, 6, wa=0.82, wb=0.82),
        _pair_row(5, 6, wa=0.82, wb=0.82),
    ]
    # DB返却順: 低(9001)→高(9000)
    log, latch = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[
            _pending_group(lo_ids, gid=9001),
            _pending_group(hi_ids, gid=9000),
        ],
        pair_rows=[*lo_rows, *hi_rows],
    )
    engine = GroupEngine(engine=_FakeEngine(), clock=FakeClock(NOW), latch=latch)
    await engine.finalize(_uid(1))
    # 集約tx・try_promoteとも高位(gid=9000)が先
    assert [g for g in log["mark_proposed"]] == [_uid(9000), _uid(9001)]
    assert log["promote"] == [_uid(9500), _uid(9500)]  # insertスタブは固定id


async def test_finalize_d06_order_robust_to_reversed_input(monkeypatch):
    """入力順を逆(高→低)に与えても処理順は同一(高位先)。"""
    hi_ids = [_uid(1), _uid(2), _uid(3)]
    lo_ids = [_uid(4), _uid(5), _uid(6)]
    hi_rows = [
        _pair_row(1, 2, wa=0.9, wb=0.9),
        _pair_row(1, 3, wa=0.9, wb=0.9),
        _pair_row(2, 3, wa=0.9, wb=0.9),
    ]
    lo_rows = [
        _pair_row(4, 5, wa=0.82, wb=0.82),
        _pair_row(4, 6, wa=0.82, wb=0.82),
        _pair_row(5, 6, wa=0.82, wb=0.82),
    ]
    log, _ = _patch_finalize(
        monkeypatch,
        org=_origin(1),
        groups=[
            _pending_group(hi_ids, gid=9000),
            _pending_group(lo_ids, gid=9001),
        ],
        pair_rows=[*hi_rows, *lo_rows],
    )
    await _engine().finalize(_uid(1))
    assert [g for g in log["mark_proposed"]] == [_uid(9000), _uid(9001)]


def test_select_group_pairs_orders_for_determinism():
    """同ペア複数行の選択順をORDER BYで確定(ws-7 Minor(a)・ws-8 design §2.9)。"""
    from latch.worker.matching.group_engine import _SELECT_GROUP_PAIRS

    sql = str(_SELECT_GROUP_PAIRS)
    assert "ORDER BY intent_a_id, intent_b_id, updated_at DESC" in sql


def test_promotion_update_writes_group_candidate_id():
    """昇格UPDATEはgroup_candidate_idも書く(§2.10・ws-7引継ぎの確定)。

    ON CONFLICTで既存の開いているlatches行へ昇格するとき、旧gidのまま
    残っていた問題への対処。SET句とbind paramのピン。
    """
    raw = str(ge._UPDATE_GROUP_LATCH_FOR_PROMOTION)
    assert "group_candidate_id = CAST(:gid AS uuid)" in raw
    assert "score = :score" in raw
    assert "response_deadline = CAST(:deadline AS timestamptz)" in raw
    assert "WHERE id = CAST(:latch_id AS uuid) AND status = 'candidate'" in raw
