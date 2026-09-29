"""JevWorker.handleの全分岐(design §2.9の表)・冪等・versionガード・計上。"""

import json
import uuid
from datetime import UTC, datetime

import fakeredis.aioredis
import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import JevOutputInvalidError, LLMError, LLMTimeoutError
from latch.llm.jev import JevJudgment
from latch.worker import jev as jev_mod
from latch.worker.cost import JevCostDependencyError, JevCostStore
from latch.worker.jev import JevWorker
from latch.worker.matching.layer4 import JevCandidateRow
from latch.worker.matching.origin import Origin, OriginLoad

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-29 21:00
DAY = "20260929"


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


def _origin(n: int = 1) -> Origin:
    """起点(Origin・time_endは補完済みの契約値)。"""
    return Origin(
        intent_id=_uid(n),
        version=1,
        user_id=_uid(n + 1),
        category_primary="drinking",
        alcohol_involved=False,
        budget_max=5000,
        participants_min=2,
        participants_max=4,
        geo_lon=130.55,
        geo_lat=31.59,
        geo_radius_m=2000,
        time_start=datetime(2026, 9, 26, 11, 0, 0, tzinfo=UTC),
        time_end=datetime(2026, 9, 26, 14, 0, 0, tzinfo=UTC),
        embedding="[0.1, 0.2]",
        soft_texts=(),
        user_ge_20=True,
        evaluated_at=NOW,
    )


def _row(
    n: int = 1,
    status: str = "pending",
    skip_reason: str | None = None,
    cheap: float = 0.9,
) -> JevCandidateRow:
    """起点intent_a_id=_uid(1)・相手intent_b_id=_uid(101)の候補行。"""
    return JevCandidateRow(
        row_id=_uid(n),
        intent_a_id=_uid(n),
        intent_b_id=_uid(n + 100),
        intent_a_version=1,
        intent_b_version=1,
        cheap_judge_score=cheap,
        status=status,
        skip_reason=skip_reason,
    )


def _jev_row(version: int = 1, time_end=None, location: str = "天文館周辺") -> tuple:
    """_SELECT_PEER相当の1行(起点・相手共用の形)。"""
    return (
        version,
        "drinking",
        {
            "location_name": location,
            "soft_constraints": [
                {"text": "軽く飲みたい", "downgraded_from_ng": False},
            ],
        },
        2,
        4,
        datetime(2026, 9, 26, 11, 0, 0, tzinfo=UTC),
        time_end,
        5000,
        2000,
    )


def _judgment(
    provider: str = "typesafe_jev", model: str | None = "jev-1.13.0"
) -> JevJudgment:
    return JevJudgment(
        provider=provider,
        model=model,
        result={
            "would_a_accept_b": 0.83,
            "would_b_accept_a": 0.71,
            "jev_5axis": {
                "purpose_fit": {"value": 0.75, "confidence": 0.8},
                "mood_fit": {"value": 0.5, "confidence": 0.6},
                "timing_fit": {"value": 0.5, "confidence": 0.7},
                "social_fit": {"value": 0.75, "confidence": 0.6},
                "latent_yes": {"value": 0.4, "confidence": None},
            },
        },
    )


class _FakeGateway:
    """judge_pairの戻り/例外を差し替え・呼び出し引数を記録。"""

    def __init__(self, judgment=None, error=None):
        self._judgment = judgment
        self._error = error
        self.calls: list[dict] = []

    async def judge_pair(self, *, intent_a, intent_b, intent_ids):
        self.calls.append(
            {"intent_a": intent_a, "intent_b": intent_b, "intent_ids": intent_ids}
        )
        if self._error is not None:
            raise self._error
        return self._judgment


class _FakeEngine:
    """engine.begin()がフェイクconnを返す最小エンジン(DBはmonkeypatchで吸収)。"""

    def begin(self):
        return _FakeTx()


class _FakeTx:
    async def __aenter__(self):
        return object()  # conn本体はmonkeypatchした関数が受けない

    async def __aexit__(self, *exc):
        return False


class _FakeGuard:
    """decision/deny_reasonを差し替え可能なGuardスタブ(呼び出しを記録)。"""

    def __init__(
        self,
        *,
        allowed: bool = True,
        deny_reason: str | None = None,
        error: Exception | None = None,
    ):
        self.allowed = allowed
        self.deny_reason = deny_reason
        self.error = error
        self.calls: list[tuple] = []

    async def request_execution(self, intent_id, user_id):
        self.calls.append((intent_id, user_id))
        if self.error is not None:
            raise self.error
        from latch.worker.cost import JevDecision

        return JevDecision(allowed=self.allowed, deny_reason=self.deny_reason)


class _RecordingCostStore:
    """JevCostStoreラッパ(record_executionの呼び出しを記録)。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def record_execution(self, provider, day):
        self.calls.append((provider, day))
        return await self._inner.record_execution(provider, day)


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


def _make_worker(gateway, guard, cost_store) -> JevWorker:
    return JevWorker(
        engine=_FakeEngine(),
        clock=FakeClock(NOW),
        gateway=gateway,
        guard=guard,
        cost_store=cost_store,
    )


def _patch_phase1(monkeypatch, org=None, skip_reason=None, rows=(), origin_row=None):
    """フェーズ1のDB部品をmonkeypatch(close_broken_pairsの呼出も記録)。"""
    closed: list = []
    selected: list = []

    async def fake_load_origin(conn, clock, intent_id):
        if skip_reason is not None:
            return OriginLoad(origin=None, skip_reason=skip_reason)
        return OriginLoad(origin=org, skip_reason=None)

    async def fake_close(conn, origin, now):
        closed.append((origin.intent_id, now))
        return 0

    async def fake_select_rows(conn, origin_id, origin_version, day_start, month_start):
        selected.append((origin_id, origin_version, day_start, month_start))
        return list(rows)

    async def fake_fetch(conn, intent_id):
        return origin_row

    monkeypatch.setattr(jev_mod.origin_mod, "load_origin", fake_load_origin)
    monkeypatch.setattr(jev_mod.layer4, "close_broken_pairs", fake_close)
    monkeypatch.setattr(jev_mod.layer4, "select_jev_rows", fake_select_rows)
    monkeypatch.setattr(jev_mod, "_fetch_jev_input", fake_fetch)
    return closed, selected


def _patch_eval(monkeypatch, *, h_recheck=True, peer_row=None):
    """フェーズ2のDB部品(H再検証・peer読取)をmonkeypatch。"""
    if peer_row is None:
        peer_row = _jev_row()
    h_calls: list = []

    async def fake_h(conn, origin, candidate_id, *, relaxed=False):
        h_calls.append(candidate_id)
        return h_recheck

    async def fake_read_peer(engine, intent_id):
        return peer_row

    monkeypatch.setattr(jev_mod.layer4, "hard_constraint_holds", fake_h)
    monkeypatch.setattr(jev_mod, "_read_peer", fake_read_peer)
    return h_calls


def _patch_writes(monkeypatch, *, complete_error=None):
    """UPDATE系モジュール関数をmonkeypatch(呼び出し引数を記録)。"""
    complete_calls: list = []
    skip_calls: list = []

    async def fake_complete(engine, row_id, jev_result, now):
        if complete_error is not None:
            raise complete_error
        complete_calls.append((row_id, jev_result, now))
        return True

    async def fake_skip(engine, row_id, reason, now):
        skip_calls.append((row_id, reason, now))
        return True

    monkeypatch.setattr(jev_mod, "_complete_row", fake_complete)
    monkeypatch.setattr(jev_mod, "_skip_row", fake_skip)
    return complete_calls, skip_calls


async def test_success_path_writes_jev_result_and_counts(redis, monkeypatch):
    """成功経路: jev_result={**result, provider, model}で書込・計上1回・起点側課税。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    complete_calls, skip_calls = _patch_writes(monkeypatch)
    guard = _FakeGuard()
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(judgment=_judgment())
    worker = _make_worker(gw, guard, cost)
    await worker.handle(_uid(1))
    assert len(complete_calls) == 1
    row_id, jev_result, _ = complete_calls[0]
    assert row_id == _uid(1)
    assert jev_result == {
        **_judgment().result,
        "provider": "typesafe_jev",
        "model": "jev-1.13.0",
    }
    assert cost.calls == [("typesafe_jev", DAY)]  # 計上1回・最終経路
    assert guard.calls == [(org.intent_id, org.user_id)]  # 起点側のみ課税
    assert skip_calls == []


async def test_guard_deny_records_skip_without_api_call(redis, monkeypatch):
    """deny経路: skipped/intent_daily・jev_result NULLのまま・API呼び出しなし。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    complete_calls, skip_calls = _patch_writes(monkeypatch)
    guard = _FakeGuard(allowed=False, deny_reason="intent_daily")
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(judgment=_judgment())
    worker = _make_worker(gw, guard, cost)
    await worker.handle(_uid(1))
    assert gw.calls == []  # judge_pair不呼出
    assert cost.calls == []  # record_execution不呼出
    assert complete_calls == []
    assert skip_calls == [(_uid(1), "intent_daily", skip_calls[0][2])]


async def test_deny_does_not_backfill_but_continues(redis, monkeypatch):
    """deny後の補充なし=選択済みの残り行をそのまま消化(2件ともdenyでINCR 2回)。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1), _row(2)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    _, skip_calls = _patch_writes(monkeypatch)
    guard = _FakeGuard(allowed=False, deny_reason="intent_daily")
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(judgment=_judgment())
    worker = _make_worker(gw, guard, cost)
    await worker.handle(_uid(1))
    assert len(guard.calls) == 2  # 選択済み2件分のINCR(補充なし・有限)
    assert [r for _, r, _ in skip_calls] == ["intent_daily", "intent_daily"]
    assert gw.calls == []


async def test_double_failure_records_llm_failure_and_continues(redis, monkeypatch):
    """双障害: skipped/llm_failure・fallback_llmで計上・以降の行も処理される。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1), _row(2)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    complete_calls, skip_calls = _patch_writes(monkeypatch)
    guard = _FakeGuard()
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(error=LLMTimeoutError("t"))
    worker = _make_worker(gw, guard, cost)
    await worker.handle(_uid(1))
    assert len(gw.calls) == 2  # 以降の行も処理
    assert [r for _, r, _ in skip_calls] == ["llm_failure", "llm_failure"]
    assert cost.calls == [("fallback_llm", DAY), ("fallback_llm", DAY)]
    assert complete_calls == []


async def test_invalid_output_records_provider_and_skips(redis, monkeypatch):
    """検証失敗: skipped/invalid_output・exc.provider(typesafe_jev)で計上。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    complete_calls, skip_calls = _patch_writes(monkeypatch)
    guard = _FakeGuard()
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(
        error=JevOutputInvalidError("jev answers invalid", provider="typesafe_jev")
    )
    worker = _make_worker(gw, guard, cost)
    await worker.handle(_uid(1))
    assert skip_calls == [(_uid(1), "invalid_output", skip_calls[0][2])]
    assert cost.calls == [("typesafe_jev", DAY)]  # 検証対象の経路で計上
    assert complete_calls == []


async def test_h_fails_leaves_pending_and_evaluates_next(redis, monkeypatch):
    """H不成立: 評価せずguard不呼出・行状態不変・次の行は評価される。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1), _row(2)], origin_row=_jev_row())
    h_calls = _patch_eval(monkeypatch, h_recheck=False)
    complete_calls, skip_calls = _patch_writes(monkeypatch)
    guard = _FakeGuard()
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(judgment=_judgment())
    worker = _make_worker(gw, guard, cost)
    await worker.handle(_uid(1))
    # 2行目は行のpeer_idが同じでも、H再検証は行ごとに呼ばれ2回→不成立が続く前提
    # (h_recheck=False固定のため両行とも評価されない) — 行ごとのH呼出と
    # guard不呼出を検査する
    assert len(h_calls) == 2
    assert guard.calls == []
    assert gw.calls == []
    assert complete_calls == [] and skip_calls == []


async def test_h_fails_pending_then_success_on_next_row(redis, monkeypatch):
    """H不成立の次の行は評価される(h_recheckを行番号で切替)。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1), _row(2)], origin_row=_jev_row())
    outcomes = {0: False, 1: True}
    h_index: list[int] = []

    async def fake_h(conn, origin, candidate_id, *, relaxed=False):
        h_index.append(candidate_id)
        return outcomes.get(len(h_index) - 1, True)

    async def fake_read_peer(engine, intent_id):
        return _jev_row()

    monkeypatch.setattr(jev_mod.layer4, "hard_constraint_holds", fake_h)
    monkeypatch.setattr(jev_mod, "_read_peer", fake_read_peer)
    complete_calls, _ = _patch_writes(monkeypatch)
    guard = _FakeGuard()
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(judgment=_judgment())
    worker = _make_worker(gw, guard, cost)
    await worker.handle(_uid(1))
    assert len(gw.calls) == 1  # 1行目のみ不成立・2行目は評価
    assert len(complete_calls) == 1 and complete_calls[0][0] == _uid(2)


async def test_phase1_closes_broken_pairs(redis, monkeypatch):
    """フェーズ1でclose_broken_pairsが呼ばれる(起点・org.evaluated_at)。"""
    org = _origin(1)
    closed, _ = _patch_phase1(monkeypatch, org=org, rows=[], origin_row=_jev_row())
    _patch_writes(monkeypatch)
    worker = _make_worker(
        _FakeGateway(), _FakeGuard(), _RecordingCostStore(JevCostStore(redis))
    )
    await worker.handle(_uid(1))
    assert closed == [(org.intent_id, org.evaluated_at)]


async def test_peer_version_mismatch_skips_evaluation(redis, monkeypatch):
    """versionガード: 相手version不一致→judge_pair・guard不呼出・行状態不変。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch, peer_row=_jev_row(version=2))  # 不一致
    complete_calls, skip_calls = _patch_writes(monkeypatch)
    guard = _FakeGuard()
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(judgment=_judgment())
    worker = _make_worker(gw, guard, cost)
    await worker.handle(_uid(1))
    assert gw.calls == [] and guard.calls == []
    assert complete_calls == [] and skip_calls == []


def test_update_sql_pins_idempotent_guard():
    """_COMPLETE/_SKIPのUPDATE WHEREに jev_result IS NULL(二重排除の冪等ガード)。"""
    assert "jev_result IS NULL" in str(jev_mod._COMPLETE)
    assert "jev_result IS NULL" in str(jev_mod._SKIP)
    assert "CAST(:jev AS jsonb)" in str(jev_mod._COMPLETE)
    assert "skip_reason = NULL" in str(jev_mod._COMPLETE)


async def test_no_rows_makes_no_calls(redis, monkeypatch):
    """冪等: 選択0行(=評価済み/新世代なし)→API・guard不呼出。"""
    org = _origin(1)
    _, selected = _patch_phase1(monkeypatch, org=org, rows=[], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    complete_calls, skip_calls = _patch_writes(monkeypatch)
    guard = _FakeGuard()
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(judgment=_judgment())
    worker = _make_worker(gw, guard, cost)
    await worker.handle(_uid(1))
    assert gw.calls == [] and guard.calls == []
    assert complete_calls == [] and skip_calls == []
    assert selected == [(_uid(1), 1, selected[0][2], selected[0][3])]


async def test_guard_redis_failure_propagates(redis, monkeypatch):
    """Guard Redis例外は伝播(fail-closed・skipped記録に変換しない)。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    _patch_writes(monkeypatch)
    guard = _FakeGuard(error=JevCostDependencyError("jev cost store unavailable"))
    worker = _make_worker(
        _FakeGateway(), guard, _RecordingCostStore(JevCostStore(redis))
    )
    with pytest.raises(JevCostDependencyError):
        await worker.handle(_uid(1))


async def test_db_write_failure_propagates(redis, monkeypatch):
    """DB書込失敗は伝播(fail-closed→ackなし再配信が回収)。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    _patch_writes(monkeypatch, complete_error=RuntimeError("db down"))
    worker = _make_worker(
        _FakeGateway(judgment=_judgment()),
        _FakeGuard(),
        _RecordingCostStore(JevCostStore(redis)),
    )
    with pytest.raises(RuntimeError):
        await worker.handle(_uid(1))


async def test_origin_noop_makes_no_layer_calls(redis, monkeypatch):
    """起点no-op: skip_reasonあり→close_broken_pairs・select_jev_rows不呼出。"""
    closed, selected = _patch_phase1(
        monkeypatch, skip_reason="origin_not_active", rows=[]
    )
    worker = _make_worker(
        _FakeGateway(judgment=_judgment()),
        _FakeGuard(),
        _RecordingCostStore(JevCostStore(redis)),
    )
    await worker.handle(_uid(1))  # 例外なくno-op
    assert closed == [] and selected == []


async def test_build_jev_text_pin_and_normalized_intent_ids(redis, monkeypatch):
    """build_jev_text呼び出しピン: [hard]/[soft]行テキスト・intent_idsは正規化順。"""
    org = _origin(1)
    _patch_phase1(
        monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row(location="天文館周辺")
    )
    _patch_eval(monkeypatch, peer_row=_jev_row(location="高見橋"))
    _patch_writes(monkeypatch)
    gw = _FakeGateway(judgment=_judgment())
    worker = _make_worker(gw, _FakeGuard(), _RecordingCostStore(JevCostStore(redis)))
    await worker.handle(_uid(1))
    (call,) = gw.calls
    assert "[hard] category: drinking" in call["intent_a"]
    assert "[hard] category: drinking" in call["intent_b"]
    assert "[hard] location: 天文館周辺 半径2km" in call["intent_a"]  # 起点側=A
    assert "[hard] location: 高見橋 半径2km" in call["intent_b"]  # 相手側=B
    assert "[soft] 軽く飲みたい" in call["intent_a"]
    assert "Intent A:" in call["intent_a"] and "Intent B:" in call["intent_b"]
    assert call["intent_ids"] == [str(_uid(1)), str(_uid(101))]  # 正規化順


async def test_accounting_provider_and_day_pin(redis, monkeypatch):
    """経路providerの計上ピン: 成功=typesafe_jev・フォールバック=fallback_llm。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    _patch_writes(monkeypatch)
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(judgment=_judgment(provider="fallback_llm", model=None))
    worker = _make_worker(gw, _FakeGuard(), cost)
    await worker.handle(_uid(1))
    assert cost.calls == [("fallback_llm", DAY)]  # 実際に呼ばれた最終経路
    assert worker._clock.jst_date().strftime("%Y%m%d") == DAY


async def test_peer_time_end_filled_with_default(redis, monkeypatch):
    """相手time_end NULL→default_time_end(time_start+3時間)で補完。"""
    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch, peer_row=_jev_row(time_end=None))
    _patch_writes(monkeypatch)
    gw = _FakeGateway(judgment=_judgment())
    worker = _make_worker(gw, _FakeGuard(), _RecordingCostStore(JevCostStore(redis)))
    await worker.handle(_uid(1))
    (call,) = gw.calls
    # time_start JST 20:00 → 終了側は23:00(補完後)
    assert "[hard] time: 2026-09-26 20:00–23:00" in call["intent_b"]


async def test_llm_error_base_class_catches_all(redis, monkeypatch):
    """LLMError継承のその他例外もllm_failureへ(skipped記録・例外にしない)。"""

    class _WeirdLLMError(LLMError):
        pass

    org = _origin(1)
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    _, skip_calls = _patch_writes(monkeypatch)
    cost = _RecordingCostStore(JevCostStore(redis))
    gw = _FakeGateway(error=_WeirdLLMError("weird"))
    worker = _make_worker(gw, _FakeGuard(), cost)
    await worker.handle(_uid(1))
    assert [r for _, r, _ in skip_calls] == ["llm_failure"]
    assert cost.calls == [("fallback_llm", DAY)]


def test_structured_data_str_is_parsed():
    """structured_dataがstrで返る場合のjson.loads(embedding.pyと同一規律)。"""
    from latch.llm.jev import JevTextInput

    row = list(_jev_row())
    row[2] = json.dumps(row[2])
    inp = jev_mod._jev_input_from_row(tuple(row))
    assert isinstance(inp, JevTextInput)
    assert isinstance(inp.structured_data, dict)
    assert inp.structured_data["location_name"] == "天文館周辺"


# -- group_ctx拡張(M2 ws-7・design §2.4) --


async def test_handle_default_group_ctx_is_backward_compatible(redis, monkeypatch):
    """group_ctx省略(既定None)は全グループペアを継続扱い・既存経路不変。"""
    org = _origin()
    rows = [_row(1), _row(2)]
    _patch_phase1(monkeypatch, org=org, rows=rows, origin_row=_jev_row())
    h_calls = _patch_eval(monkeypatch)
    _patch_writes(monkeypatch)
    recorded: list = []

    def fake_targets(rows_in, new_pair_row_ids=frozenset()):
        recorded.append((tuple(r.row_id for r in rows_in), new_pair_row_ids))
        return list(rows_in)

    monkeypatch.setattr(jev_mod.layer4, "select_jev_targets", fake_targets)
    worker = _make_worker(
        _FakeGateway(_judgment()),
        _FakeGuard(),
        _RecordingCostStore(JevCostStore(redis)),
    )
    await worker.handle(_uid(1))
    assert recorded[0][1] == frozenset()  # None→frozenset
    assert len(h_calls) == 2  # 既存経路(H再検証)も不変


async def test_handle_passes_new_pair_row_ids_to_targets(redis, monkeypatch):
    """group_ctx.new_pair_row_idsがselect_jev_targetsへ流れる(§2.4)。"""

    class _Ctx:
        new_pair_row_ids = frozenset({_uid(50)})

    org = _origin()
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    _patch_writes(monkeypatch)
    recorded: list = []

    def fake_targets(rows_in, new_pair_row_ids=frozenset()):
        recorded.append(new_pair_row_ids)
        return list(rows_in)

    monkeypatch.setattr(jev_mod.layer4, "select_jev_targets", fake_targets)
    worker = _make_worker(
        _FakeGateway(_judgment()),
        _FakeGuard(),
        _RecordingCostStore(JevCostStore(redis)),
    )
    await worker.handle(_uid(1), _Ctx())
    assert recorded[0] == frozenset({_uid(50)})


async def test_group_pair_uses_relaxed_h_recheck(redis, monkeypatch):
    """pair_kind='group'の行はH再検証へrelaxed=True(§2.4)。"""
    import dataclasses

    org = _origin()
    grp = dataclasses.replace(_row(1), pair_kind="group")
    _patch_phase1(monkeypatch, org=org, rows=[grp], origin_row=_jev_row())
    relaxed_calls: list = []

    async def fake_h(conn, origin, candidate_id, *, relaxed=False):
        relaxed_calls.append(relaxed)
        return True

    async def fake_read_peer(engine, pid):
        return _jev_row()

    monkeypatch.setattr(jev_mod.layer4, "hard_constraint_holds", fake_h)
    monkeypatch.setattr(jev_mod, "_read_peer", fake_read_peer)
    _patch_writes(monkeypatch)
    worker = _make_worker(
        _FakeGateway(_judgment()),
        _FakeGuard(),
        _RecordingCostStore(JevCostStore(redis)),
    )
    await worker.handle(_uid(1))
    assert relaxed_calls == [True]
