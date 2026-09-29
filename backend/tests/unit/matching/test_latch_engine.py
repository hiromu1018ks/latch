"""LatchEngine.handle(選択・latch_score・latches生成・昇格・nearby・提示)のunit試験。

design §2.2〜2.7・§4.1。DB操作はlatch_engineのモジュール関数をmonkeypatch
して分岐ロジックを検証する(runnerと同一規律)。SQL文字列はtext()定数への
直接ピンで検証する。
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from latch.core.clock import FakeClock
from latch.worker.matching import latch_engine as le
from latch.worker.matching import layer4
from latch.worker.matching.latch_engine import (
    NOTIFICATION_NEARBY,
    NOTIFICATION_PROPOSAL,
    LatchEngine,
    LatchTargetRow,
)
from latch.worker.matching.origin import Origin, OriginLoad
from latch.worker.matching.proposal import LatchIntentInputs

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


NEW_LATCH_ID = _uid(9000)  # insertスタブの既定戻り値(新規INSERT成功)

_DEFAULT = object()  # _patchパラメータ「未指定」のsentinel


def _origin(n: int = 1) -> Origin:
    """起点(time_startは将来窓・test_worker_jev.pyの_originと同一構成)。"""
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
        time_start=NOW + timedelta(hours=30),
        time_end=NOW + timedelta(hours=33),
        embedding="[0.1, 0.2]",
        soft_texts=(),
        user_ge_20=True,
        evaluated_at=NOW,
    )


def _row(n: int = 1, wa: float = 0.9, wb: float = 0.85) -> LatchTargetRow:
    return LatchTargetRow(
        row_id=_uid(n),
        intent_a_id=_uid(n),
        intent_b_id=_uid(n + 100),
        jev_result={
            "would_a_accept_b": wa,
            "would_b_accept_a": wb,
            "provider": "typesafe_jev",
            "model": "jev-1.13.0",
        },
    )


def _inputs(n: int, **over) -> LatchIntentInputs:
    base = dict(
        intent_id=_uid(n),
        user_id=_uid(n + 50),
        visibility="summary_only",
        notification_level="proposals_only",
        time_start=NOW + timedelta(hours=30),
        expires_at=NOW + timedelta(days=5),
        budget_max=5000,
        category_primary="drinking",
        category_secondary=None,
        geo_lon=130.558,
        geo_lat=31.596,
    )
    base.update(over)
    return LatchIntentInputs(**base)


class _FakeEngine:
    def begin(self):
        return _FakeTx()


class _FakeTx:
    async def __aenter__(self):
        return object()  # conn本体はmonkeypatchした関数が受けない

    async def __aexit__(self, *exc):
        return False


class _RecordingGeo:
    def __init__(self, name="鹿児島市天文館"):
        self._name = name
        self.calls: list[tuple[float, float]] = []

    async def reverse_geocode(self, lon, lat):
        self.calls.append((lon, lat))
        return self._name


_DEFER_HISTORY = [
    {"user_id": "u1", "response": "defer", "answered_at": "2026-10-01T11:50:00+00:00"},
]
_NO_HISTORY = [
    {"user_id": "u1", "response": "no", "answered_at": "2026-10-01T11:40:00+00:00"},
]


def _patch(
    monkeypatch,
    *,
    org: Origin | None = None,
    skip_reason: str | None = None,
    rows=(),
    h_holds: bool = True,
    record_result=_DEFAULT,
    inputs: dict | None = None,
    responses=(),
    insert_result=_DEFAULT,
    find_open_result=None,
    daily: dict | None = None,
):
    """LatchEngineのDB部品を記録スタブへ差し替え(戻り値=呼び出し記録log)。

    record_result/insert_result は未指定で成功系の既定((row_id, None)・
    新規UUID)を返す。None を明示渡しすると競合負け/ON CONFLICT を表す。
    """
    log = {
        "selected": [],
        "h": [],
        "closed": [],
        "record": [],
        "read_inputs": [],
        "responses": [],
        "insert": [],
        "find_open": [],
        "update_promo": [],
        "events": [],
        "notifications": [],
        "daily": [],
        "promote": [],
        "drain": [],
    }
    if inputs is None:
        inputs = {_uid(1): _inputs(1), _uid(101): _inputs(101)}

    async def fake_load_origin(conn, clock, intent_id):
        return OriginLoad(origin=org, skip_reason=skip_reason)

    async def fake_select(conn, origin_id, origin_version):
        log["selected"].append((origin_id, origin_version))
        return list(rows)

    async def fake_h(conn, origin, candidate_id):
        log["h"].append(candidate_id)
        return h_holds

    async def fake_close(conn, row_id, now):
        log["closed"].append((row_id, now))
        return True

    async def fake_record(conn, row_id, score, now):
        log["record"].append((row_id, score, now))
        if record_result is _DEFAULT:
            return (row_id, None)
        return record_result

    async def fake_read_inputs(engine, intent_id):
        log["read_inputs"].append(intent_id)
        return inputs.get(intent_id)

    async def fake_responses(engine, a_id, b_id):
        log["responses"].append((a_id, b_id))
        return list(responses)

    async def fake_insert(conn, *, a_id, b_id, proposal, score, deadline, expires, now):
        log["insert"].append(
            {
                "a": a_id,
                "b": b_id,
                "proposal": proposal,
                "score": score,
                "deadline": deadline,
                "expires": expires,
                "now": now,
            }
        )
        if insert_result is _DEFAULT:
            return NEW_LATCH_ID
        return insert_result

    async def fake_find_open(conn, a_id, b_id):
        log["find_open"].append((a_id, b_id))
        return find_open_result

    async def fake_update_promo(conn, latch_id, *, score, proposal, deadline):
        log["update_promo"].append((latch_id, score, proposal, deadline))
        return True

    async def fake_event(conn, latch_id, from_status, to_status, user_id, now):
        log["events"].append((latch_id, from_status, to_status, user_id))

    async def fake_notification(conn, user_id, ntype, latch_id, now):
        log["notifications"].append((user_id, ntype, latch_id))

    async def fake_daily(conn, user_ids, day_start, day_next):
        log["daily"].append((tuple(user_ids), day_start, day_next))
        return daily if daily is not None else {}

    async def fake_promote(engine_self, latch_id):
        log["promote"].append(latch_id)

    async def fake_drain(engine_self, now, threshold):
        log["drain"].append((now, threshold))
        return []

    monkeypatch.setattr(le.origin_mod, "load_origin", fake_load_origin)
    monkeypatch.setattr(le, "_select_target_rows", fake_select)
    monkeypatch.setattr(le.layer4, "hard_constraint_holds", fake_h)
    monkeypatch.setattr(le, "_close_h_broken", fake_close)
    monkeypatch.setattr(le, "_record_score", fake_record)
    monkeypatch.setattr(le, "_read_intent_inputs", fake_read_inputs)
    monkeypatch.setattr(le, "_read_latch_responses", fake_responses)
    monkeypatch.setattr(le, "_insert_latch", fake_insert)
    monkeypatch.setattr(le, "_find_open_latch", fake_find_open)
    monkeypatch.setattr(le, "_update_for_promotion", fake_update_promo)
    monkeypatch.setattr(le, "_insert_latch_event", fake_event)
    monkeypatch.setattr(le, "_insert_notification", fake_notification)
    monkeypatch.setattr(le, "_count_daily_notifications", fake_daily)
    monkeypatch.setattr(le.LatchEngine, "try_promote", fake_promote)
    monkeypatch.setattr(le, "_drain_candidates", fake_drain)
    return log


def _engine(clock=None, geo=None) -> LatchEngine:
    return LatchEngine(engine=_FakeEngine(), clock=clock or FakeClock(NOW), geo=geo)


# --- 1. no-op分岐 ---


async def test_origin_noop_skips_selection(monkeypatch):
    """skip_reason付き起点 → 対象選択なし・例外なし(design §2.9の表)。"""
    log = _patch(monkeypatch, skip_reason="origin_not_active")
    await _engine().handle(_uid(1))
    assert log["selected"] == []


# --- 2. H再検証不成立 ---


async def test_h_broken_closes_row_without_score(monkeypatch):
    """H再検証False → 当該行をclosedへ・latch_score計算なし(belt-and-suspenders)。"""
    row = _row(1)
    log = _patch(monkeypatch, org=_origin(1), rows=[row], h_holds=False)
    await _engine().handle(_uid(1))
    assert [c[0] for c in log["closed"]] == [row.row_id]
    assert log["record"] == []
    assert log["insert"] == []


# --- 3. latch_score計算 ---


async def test_mutual_score_is_min_times_c(monkeypatch):
    """wa=0.9/wb=0.85 → score=0.85(=min×LATCH_C・丸めなし)。"""
    row = _row(1, wa=0.9, wb=0.85)
    log = _patch(monkeypatch, org=_origin(1), rows=[row])
    await _engine().handle(_uid(1))
    assert log["record"] == [(row.row_id, 0.85, log["record"][0][2])]


# --- 4. 競合負け ---


async def test_record_score_conflict_returns_early(monkeypatch):
    """_record_score None(他の実行が先に計算済み) → 生成物なし。

    I-1改修(ws-7)で読取はtx前に移動したためread_inputsは呼ばれるが、
    latches生成・イベントは行わない。
    """
    log = _patch(monkeypatch, org=_origin(1), rows=[_row(1)], record_result=None)
    await _engine().handle(_uid(1))
    assert log["record"]
    assert log["insert"] == []


# --- 5. 閾値境界 ---


async def test_threshold_boundary_is_proposal_path(monkeypatch):
    """wa=wb=0.80(閾値ちょうど) → 提案経路(06 §6「L >= 0.80」)。"""
    row = _row(1, wa=0.80, wb=0.80)
    log = _patch(monkeypatch, org=_origin(1), rows=[row])
    await _engine().handle(_uid(1))
    assert len(log["insert"]) == 1
    assert log["insert"][0]["score"] == 0.80
    assert log["insert"][0]["proposal"]["match_level"] == "medium"


# --- 6. nearby経路 ---


async def test_nearby_path_creates_candidate_and_notifies(monkeypatch):
    """閾値未満+nearby_also側のみ → candidate行+存在通知1件・try_promote不呼出。"""
    row = _row(1, wa=0.5, wb=0.5)
    inputs = {
        _uid(1): _inputs(1, notification_level="nearby_also"),
        _uid(101): _inputs(101, notification_level="muted"),
    }
    log = _patch(monkeypatch, org=_origin(1), rows=[row], inputs=inputs)
    await _engine().handle(_uid(1))
    (ins,) = log["insert"]
    assert ins["proposal"] == {"headcount": 2, "match_level": "low"}
    assert ins["score"] == 0.5
    assert log["events"] == [(NEW_LATCH_ID, None, "candidate", None)]
    # 存在通知はnearby_also側のみ(muted側なし)
    assert log["notifications"] == [
        (_inputs(1).user_id, NOTIFICATION_NEARBY, NEW_LATCH_ID)
    ]
    assert log["promote"] == []  # nearbyはtry_promoteしない(引用#9)


# --- 7. nearby日次上限 ---


async def test_nearby_daily_limit_skips_notification_only(monkeypatch):
    """nearby_also側が当日6件済み → 存在通知ゼロ・candidate行自体は作る。"""
    row = _row(1, wa=0.5, wb=0.5)
    inputs = {
        _uid(1): _inputs(1, notification_level="nearby_also"),
        _uid(101): _inputs(101),
    }
    log = _patch(
        monkeypatch,
        org=_origin(1),
        rows=[row],
        inputs=inputs,
        daily={_inputs(1).user_id: 6},
    )
    await _engine().handle(_uid(1))
    assert len(log["insert"]) == 1  # 行は作る
    assert log["notifications"] == []  # 通知は切り捨て


# --- 8. 閾値未満+nearby_alsoなし ---


async def test_below_threshold_without_nearby_makes_nothing(monkeypatch):
    """両方proposals_only → latches行を作らない(design §2.7)。"""
    row = _row(1, wa=0.5, wb=0.5)
    log = _patch(monkeypatch, org=_origin(1), rows=[row])
    await _engine().handle(_uid(1))
    assert log["insert"] == [] and log["notifications"] == []


# --- 9. nearby+no履歴 ---


async def test_nearby_with_no_history_makes_nothing(monkeypatch):
    """no履歴 → 存在通知も出さない(design §2.7 — D-07の一貫適用)。"""
    row = _row(1, wa=0.5, wb=0.5)
    inputs = {
        _uid(1): _inputs(1, notification_level="nearby_also"),
        _uid(101): _inputs(101),
    }
    log = _patch(
        monkeypatch, org=_origin(1), rows=[row], inputs=inputs, responses=_NO_HISTORY
    )
    await _engine().handle(_uid(1))
    assert log["insert"] == [] and log["notifications"] == []


# --- 10. 提案経路のD-07 no ---


async def test_proposal_path_d07_no_denies(monkeypatch):
    """閾値超過でもno履歴 → latches生成なし(Intent期限まで再提案しない)。"""
    row = _row(1, wa=0.9, wb=0.9)
    log = _patch(monkeypatch, org=_origin(1), rows=[row], responses=_NO_HISTORY)
    await _engine().handle(_uid(1))
    assert log["record"]  # スコア計算は行う
    assert log["insert"] == []


# --- 11. 提案経路のD-07 defer抑制 ---


async def test_proposal_path_defer_small_delta_denies_then_new_gen_allows(
    monkeypatch,
):
    """defer抑制内+|Δ|<0.05 → INSERTなし。prev=None(新世代)ならINSERTする。"""
    row = _row(1, wa=0.85, wb=0.83)  # score=0.83・prev=0.83 → |Δ|=0
    log = _patch(
        monkeypatch,
        org=_origin(1),
        rows=[row],
        responses=_DEFER_HISTORY,
        record_result=(row.row_id, 0.83),
    )
    await _engine().handle(_uid(1))
    assert log["insert"] == []  # 抑制内+変化なし
    log2 = _patch(
        monkeypatch,
        org=_origin(1),
        rows=[row],
        responses=_DEFER_HISTORY,
        record_result=(row.row_id, None),
    )  # prev=None=新世代
    await _engine().handle(_uid(1))
    assert len(log2["insert"]) == 1


# --- 12. INSERT成功 ---


async def test_insert_success_writes_event_and_promotes(monkeypatch):
    """INSERT成功 → candidateイベント(user_id=None)+try_promote(引用#16)。"""
    row = _row(1, wa=0.9, wb=0.85)
    log = _patch(monkeypatch, org=_origin(1), rows=[row])
    await _engine().handle(_uid(1))
    assert log["events"] == [(NEW_LATCH_ID, None, "candidate", None)]
    assert log["promote"] == [NEW_LATCH_ID]


# --- 13. ON CONFLICT→既存candidate ---


async def test_on_conflict_existing_candidate_promotes(monkeypatch):
    """ON CONFLICT+既存candidate+D-07通過 → 3列更新+try_promote(昇格)。"""
    row = _row(1, wa=0.9, wb=0.85)
    lid = _uid(8000)
    log = _patch(
        monkeypatch,
        org=_origin(1),
        rows=[row],
        insert_result=None,
        find_open_result=(lid, "candidate"),
    )
    await _engine().handle(_uid(1))
    (upd,) = log["update_promo"]
    assert upd[0] == lid
    assert upd[1] == 0.85  # score
    assert upd[2]["match_level"] == "medium"  # proposalは通常生成に切替
    assert upd[3] == log["insert"][0]["deadline"]  # 暫定deadline
    assert log["promote"] == [lid]


# --- 14. ON CONFLICT→既存proposed ---


async def test_on_conflict_existing_proposed_skips(monkeypatch):
    """ON CONFLICT+既存proposed → 更新も提示もしない(重複提案の遮断)。"""
    row = _row(1, wa=0.9, wb=0.85)
    log = _patch(
        monkeypatch,
        org=_origin(1),
        rows=[row],
        insert_result=None,
        find_open_result=(_uid(8000), "proposed"),
    )
    await _engine().handle(_uid(1))
    assert log["update_promo"] == [] and log["promote"] == []


# --- 15. 昇格時D-07拒否 ---


async def test_promotion_d07_denied_keeps_candidate(monkeypatch):
    """既存candidateだがD-07抑制内+Δ小 → 3列更新も提示もしない。"""
    row = _row(1, wa=0.85, wb=0.83)  # score=0.83
    log = _patch(
        monkeypatch,
        org=_origin(1),
        rows=[row],
        insert_result=None,
        find_open_result=(_uid(8000), "candidate"),
        responses=_DEFER_HISTORY,
        record_result=(row.row_id, 0.83),
    )
    await _engine().handle(_uid(1))
    assert log["update_promo"] == [] and log["promote"] == []


# --- 16. 閾値未満評価での既存行不更新 ---


async def test_below_threshold_keeps_existing_row(monkeypatch):
    """nearby評価でON CONFLICT → 既存行の特定・更新をしない(design §2.3)。"""
    row = _row(1, wa=0.5, wb=0.5)
    inputs = {
        _uid(1): _inputs(1, notification_level="nearby_also"),
        _uid(101): _inputs(101),
    }
    log = _patch(
        monkeypatch, org=_origin(1), rows=[row], inputs=inputs, insert_result=None
    )
    await _engine().handle(_uid(1))
    assert log["find_open"] == [] and log["update_promo"] == []


# --- 17. _read_intent_inputsの片方None ---


async def test_peer_inputs_none_returns_silently(monkeypatch):
    """相手削除・期限NULL等でNone → latches生成なし・例外なし。"""
    row = _row(1, wa=0.9, wb=0.85)
    log = _patch(
        monkeypatch,
        org=_origin(1),
        rows=[row],
        inputs={_uid(1): _inputs(1), _uid(101): None},
    )
    await _engine().handle(_uid(1))
    assert log["insert"] == []


# --- 18. intent_ids正規化 ---


async def test_intent_ids_normalized_sorted(monkeypatch):
    """_insert_latchのa_id < b_id(Python側sorted・design §2.3)。"""
    row = _row(1, wa=0.9, wb=0.85)  # 起点がa側
    log = _patch(monkeypatch, org=_origin(1), rows=[row])
    await _engine().handle(_uid(1))
    (ins,) = log["insert"]
    assert ins["a"] == _uid(1) and ins["b"] == _uid(101)
    # 起点が大きい側でも正規化される
    row2 = LatchTargetRow(
        row_id=_uid(2),
        intent_a_id=_uid(300),
        intent_b_id=_uid(250),
        jev_result={"would_a_accept_b": 0.9, "would_b_accept_a": 0.85},
    )
    log2 = _patch(
        monkeypatch,
        org=_origin(250),
        rows=[row2],
        inputs={_uid(250): _inputs(250), _uid(300): _inputs(300)},
    )
    await _engine().handle(_uid(250))
    (ins2,) = log2["insert"]
    assert ins2["a"] == _uid(250) and ins2["b"] == _uid(300)


# --- 19. area_name中点 ---


async def test_area_name_uses_geo_midpoint(monkeypatch):
    """geo注入 → reverse_geocodeは両者geoの中点を1回・geo=Noneならarea_name=None。"""
    row = _row(1, wa=0.9, wb=0.85)
    inputs = {
        _uid(1): _inputs(1, geo_lon=130.0, geo_lat=31.0),
        _uid(101): _inputs(101, geo_lon=132.0, geo_lat=33.0),
    }
    geo = _RecordingGeo()
    log = _patch(monkeypatch, org=_origin(1), rows=[row], inputs=inputs)
    await _engine(geo=geo).handle(_uid(1))
    assert geo.calls == [((130.0 + 132.0) / 2, (31.0 + 33.0) / 2)]
    assert log["insert"][0]["proposal"]["area_name"] == "鹿児島市天文館"
    log2 = _patch(monkeypatch, org=_origin(1), rows=[row], inputs=inputs)
    await _engine(geo=None).handle(_uid(1))
    assert log2["insert"][0]["proposal"]["area_name"] is None


# --- 20. SQLピン(test_layer_sql.py流儀・Review Focus 1) ---


def test_select_targets_pins_idempotent_guard():
    """選択SQL: latch_score IS NULL(冪等ガード)・evaluated・jev_result・
    相手現行version一致EXISTS・cheap順。"""
    sql = str(le._SELECT_TARGETS)
    assert "mc.latch_score IS NULL" in sql
    assert "mc.status = 'evaluated'" in sql
    assert "mc.jev_result IS NOT NULL" in sql
    assert "AND p.version =" in sql  # EXISTS句: 相手version=相手現行
    assert "ORDER BY mc.cheap_judge_score DESC NULLS LAST" in sql


def test_record_score_pins_prev_retreat_and_guard():
    """退避つきUPDATE: prev退避・latch_score IS NULLガード・RETURNING prev。"""
    sql = str(le._RECORD_SCORE)
    assert "prev_latch_score = latch_score" in sql
    assert "prev_evaluated_at = updated_at" in sql
    assert "WHERE id = CAST(:row_id AS uuid) AND latch_score IS NULL" in sql
    assert "RETURNING id, prev_latch_score" in sql


def test_insert_latch_pins_on_conflict_partial_unique():
    """latches INSERT: 0004部分UNIQUEへのON CONFLICT DO NOTHING(Review Focus 1)。"""
    sql = str(le._INSERT_LATCH)
    assert (
        "ON CONFLICT (intent_ids) WHERE status IN"
        " ('candidate', 'proposed', 'partial_accept')" in sql
    )
    assert "DO NOTHING" in sql
    assert "RETURNING id" in sql
    assert "'candidate'" in sql  # status初期値


def test_insert_latch_event_pins_five_columns():
    """latch_status_events: 5列INSERT・CAST形式(from/user_idはNULL可)。"""
    sql = str(le._INSERT_LATCH_EVENT)
    assert "(latch_id, from_status, to_status, user_id, created_at)" in sql
    assert "CAST(:latch_id AS uuid)" in sql
    assert "CAST(:from_status AS text)" in sql
    assert "CAST(:user_id AS uuid)" in sql


def test_select_latch_responses_pins_nonempty_filter():
    sql = str(le._SELECT_LATCH_RESPONSES)
    assert "responses <> CAST('[]' AS jsonb)" in sql
    assert "ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]" in sql


def test_sql_bind_params_compile():
    """postgresql dialectでcompile → 未変換の ':name' が残らない(部分認識ゼロ)。"""
    from sqlalchemy.dialects import postgresql

    targets = {
        le._SELECT_TARGETS: {"origin", "origin_version"},
        le._CLOSE_H_BROKEN: {"row_id", "now"},
        le._RECORD_SCORE: {"row_id", "score", "now"},
        le._SELECT_INTENT_INPUTS: {"intent_id"},
        le._SELECT_LATCH_RESPONSES: {"a", "b"},
        le._INSERT_LATCH: {"a", "b", "proposal", "score", "deadline", "expires", "now"},
        le._FIND_OPEN_LATCH: {"a", "b"},
        le._UPDATE_FOR_PROMOTION: {"latch_id", "score", "proposal", "deadline"},
        le._INSERT_LATCH_EVENT: {
            "latch_id",
            "from_status",
            "to_status",
            "user_id",
            "now",
        },
        le._INSERT_NOTIFICATION: {"user_id", "type", "payload", "now"},
    }
    for stmt, keys in targets.items():
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        for key in keys:
            assert f":{key}" not in compiled, (key, compiled)


# --- 21. notifications種別ピン(§9-10のCHECKなし確認とセット) ---


def test_notification_type_pins():
    assert NOTIFICATION_PROPOSAL == "proposal"
    assert NOTIFICATION_NEARBY == "nearby_candidate"


# =====================================================================
# 後半(Task 5): try_promote・drain(design §2.6・§2.9のtx分割(4)・(5))
# =====================================================================

LID = _uid(7000)


def _latch_row(status="candidate", ids=None, expires=None):
    """_select_latch_for_updateの1行(6要素・group_candidate_id=Noneは1対1)。"""
    ids = ids or [_uid(1), _uid(101)]
    return (
        LID,
        status,
        ids,
        expires or NOW + timedelta(days=5),
        None,
        Decimal("0.85"),
    )


def _participant(n: int, **over):
    """try_promote用の参加Intent情報(_SELECT_INTENT_INPUTS由来)。"""
    base = dict(
        intent_id=_uid(n),
        user_id=_uid(n + 50),
        notification_level="proposals_only",
        time_start=NOW + timedelta(hours=30),
        expires_at=NOW + timedelta(days=5),
    )
    base.update(over)
    return le.Participant(**base)


def _patch_promote(
    monkeypatch,
    *,
    row=None,
    parts=None,
    daily=None,
    open_counts=0,
    promote_ok=True,
):
    """try_promote/drain系のDB部品を記録スタブへ差し替え。"""
    log = {
        "row": None,
        "parts": None,
        "daily": [],
        "open": [],
        "expire": [],
        "promote": [],
        "events": [],
        "notifications": [],
    }

    async def fake_select_lu(conn, latch_id):
        log["row"] = latch_id
        return row

    async def fake_read_parts(conn, intent_ids):
        log["parts"] = tuple(intent_ids)
        return (
            list(parts)
            if parts is not None
            else [
                _participant(1),
                _participant(101),
            ]
        )

    async def fake_daily(conn, user_ids, day_start, day_next):
        log["daily"].append((tuple(user_ids), day_start, day_next))
        return daily if daily is not None else {}

    async def fake_open(conn, intent_id):
        log["open"].append(intent_id)
        return open_counts

    async def fake_expire(conn, latch_id, now):
        log["expire"].append((latch_id, now))
        return True

    async def fake_promote_latch(conn, latch_id, deadline, now):
        log["promote"].append((latch_id, deadline, now))
        return promote_ok

    async def fake_event(conn, latch_id, from_status, to_status, user_id, now):
        log["events"].append((latch_id, from_status, to_status, user_id))

    async def fake_notification(conn, user_id, ntype, latch_id, now):
        log["notifications"].append((user_id, ntype, latch_id))

    monkeypatch.setattr(le, "_select_latch_for_update", fake_select_lu)
    monkeypatch.setattr(le, "_read_participants", fake_read_parts)
    monkeypatch.setattr(le, "_count_daily_notifications", fake_daily)
    monkeypatch.setattr(le, "_count_open_proposed", fake_open)
    monkeypatch.setattr(le, "_expire_latch", fake_expire)
    monkeypatch.setattr(le, "_promote_latch", fake_promote_latch)
    monkeypatch.setattr(le, "_insert_latch_event", fake_event)
    monkeypatch.setattr(le, "_insert_notification", fake_notification)
    return log


# --- 後半1. 75分ルール(Review Focus 4・通知ゼロ) ---


async def test_75min_rule_expires_without_notification(monkeypatch):
    """対象時刻まで70分 → candidate→expired+イベント・promote/通知ゼロ。"""
    parts = [
        _participant(1, time_start=NOW + timedelta(minutes=70)),
        _participant(101, time_start=NOW + timedelta(minutes=70)),
    ]
    log = _patch_promote(monkeypatch, row=_latch_row(), parts=parts)
    await _engine().try_promote(LID)
    assert log["expire"] == [(LID, NOW)]
    assert log["events"] == [(LID, "candidate", "expired", None)]
    assert log["promote"] == [] and log["notifications"] == []


# --- 後半2. status≠candidate ---


async def test_promote_skips_non_candidate_status(monkeypatch):
    """FOR UPDATEでstatus='proposed' → 何もしない(他経路で遷移済み)。"""
    log = _patch_promote(monkeypatch, row=_latch_row(status="proposed"))
    await _engine().try_promote(LID)
    assert log["parts"] is None
    assert log["expire"] == [] and log["promote"] == []
    assert log["notifications"] == []


# --- 後半3. 行なし ---


async def test_promote_skips_missing_row(monkeypatch):
    log = _patch_promote(monkeypatch, row=None)
    await _engine().try_promote(LID)
    assert log["parts"] is None and log["promote"] == []


# --- 後半4. expires_at<=now ---


async def test_promote_skips_expired_row(monkeypatch):
    """latches.expires_at<=now → 対象外化のみ(expiry_sweeper=M3-3担当)。"""
    log = _patch_promote(monkeypatch, row=_latch_row(expires=NOW))
    await _engine().try_promote(LID)
    assert log["parts"] is not None  # 手順2(参加者読取)までは進む
    assert log["expire"] == [] and log["promote"] == []
    assert log["events"] == [] and log["notifications"] == []


# --- 後半5. deadline<=now(防御・75分と同一扱い) ---


async def test_promote_deadline_past_notify_time_expires(monkeypatch):
    """min_expiresが過去 → 導出期限<=now → expired(引用#4・防御)。"""
    parts = [
        _participant(
            1,
            time_start=NOW + timedelta(minutes=80),
            expires_at=NOW - timedelta(minutes=5),
        ),
        _participant(
            101,
            time_start=NOW + timedelta(minutes=80),
            expires_at=NOW - timedelta(minutes=5),
        ),
    ]
    log = _patch_promote(
        monkeypatch, row=_latch_row(expires=NOW + timedelta(hours=1)), parts=parts
    )
    await _engine().try_promote(LID)
    assert log["expire"] == [(LID, NOW)]
    assert log["events"] == [(LID, "candidate", "expired", None)]
    assert log["promote"] == [] and log["notifications"] == []


# --- 後半6. D-08日次上限 ---


async def test_promote_daily_limit_keeps_candidate(monkeypatch):
    """通知対象1名が当日6件済み → candidateのまま・promote/eventsなし。"""
    parts = [_participant(1), _participant(101, notification_level="muted")]
    log = _patch_promote(
        monkeypatch, row=_latch_row(), parts=parts, daily={_participant(1).user_id: 6}
    )
    await _engine().try_promote(LID)
    assert log["promote"] == []
    assert log["events"] == [] and log["notifications"] == []


# --- 後半7. D-08同時3件 ---


async def test_promote_concurrent_limit_keeps_candidate(monkeypatch):
    """参加Intentの開いているproposedが3件 → candidateのまま(muted含む計上)。"""
    log = _patch_promote(monkeypatch, row=_latch_row(), open_counts=3)
    await _engine().try_promote(LID)
    assert log["promote"] == []
    assert log["events"] == [] and log["notifications"] == []


# --- 後半8. muted(Review Focus 3) ---


async def test_promote_muted_writes_no_notifications(monkeypatch):
    """両参加者muted → proposed遷移+イベントは通常どおり・notifications 0件・
    日次カウントの引数user_idsは空(上限を消費しない)。"""
    parts = [
        _participant(1, notification_level="muted"),
        _participant(101, notification_level="muted"),
    ]
    log = _patch_promote(monkeypatch, row=_latch_row(), parts=parts)
    await _engine().try_promote(LID)
    assert len(log["promote"]) == 1  # proposed遷移は行う(引用#10)
    assert log["events"] == [(LID, "candidate", "proposed", None)]
    assert log["notifications"] == []
    assert log["daily"][0][0] == ()  # user_ids空=カウント対象なし


# --- 後半9. 正常プロモート ---


async def test_promote_success_writes_event_and_notifications(monkeypatch):
    """上限内 → promote(D-05再計算deadline)・イベント・通知対象2名へnotifications。"""
    parts = [_participant(1), _participant(101, notification_level="muted")]
    log = _patch_promote(monkeypatch, row=_latch_row(), parts=parts)
    await _engine().try_promote(LID)
    expected_deadline = le.latch_calc.response_deadline(
        NOW, NOW + timedelta(hours=30), NOW + timedelta(days=5)
    )
    assert log["promote"] == [(LID, expected_deadline, NOW)]
    assert log["events"] == [(LID, "candidate", "proposed", None)]
    assert log["notifications"] == [
        (_participant(1).user_id, NOTIFICATION_PROPOSAL, LID),
    ]  # mutedは書かない=上限も消費しない


# --- 後半10. 提示時deadline再計算(FOR UPDATE後のnow) ---


async def test_promote_deadline_uses_now_after_lock(monkeypatch):
    """deadlineはFOR UPDATE取得後に採取したnowで再計算(FakeClock進行で検証)。"""
    clock = FakeClock(NOW)
    parts = [_participant(1), _participant(101)]

    async def fake_select_lu(conn, latch_id):
        clock.advance(timedelta(minutes=5))  # ロック取得後に時刻経過
        return _latch_row()

    log = _patch_promote(monkeypatch, row=_latch_row(), parts=parts)
    monkeypatch.setattr(le, "_select_latch_for_update", fake_select_lu)
    await _engine(clock=clock).try_promote(LID)
    expected = le.latch_calc.response_deadline(
        NOW + timedelta(minutes=5),
        NOW + timedelta(hours=30),
        NOW + timedelta(days=5),
    )
    assert log["promote"] == [(LID, expected, NOW + timedelta(minutes=5))]


# --- 後半11. promote競合負け ---


async def test_promote_conflict_writes_nothing(monkeypatch):
    """条件付きUPDATEが行数0 → イベント・notifications不呼出。"""
    log = _patch_promote(monkeypatch, row=_latch_row(), promote_ok=False)
    await _engine().try_promote(LID)
    assert log["promote"]
    assert log["events"] == [] and log["notifications"] == []


# --- 後半12. drain順序 ---


async def test_drain_promotes_in_order(monkeypatch):
    """drainは提示順(抽出行)の順にtry_promoteへ渡す(行毎に独立tx)。"""
    order = []

    async def fake_drain_ids(engine_, now, threshold):
        return [_uid(11), _uid(12), _uid(13)]

    async def fake_try(self, latch_id):
        order.append(latch_id)

    monkeypatch.setattr(le, "_drain_candidates", fake_drain_ids)
    monkeypatch.setattr(le.LatchEngine, "try_promote", fake_try)
    await _engine()._drain()
    assert order == [_uid(11), _uid(12), _uid(13)]


# --- 後半13. drainのnearby除外(Review Focus 2) ---


async def test_drain_passes_threshold_and_excludes_nearby(monkeypatch):
    """drainはLATCH_THRESHOLDを渡す・SQLにscore閾値条件(nearby行を弾く)。"""
    captured = []

    async def fake_drain_ids(engine_, now, threshold):
        captured.append((now, threshold))
        return []

    monkeypatch.setattr(le, "_drain_candidates", fake_drain_ids)
    clock = FakeClock(NOW)
    await _engine(clock=clock)._drain()
    assert captured == [(NOW, le.latch_calc.LATCH_THRESHOLD)]
    assert "l.score >= CAST(:threshold AS numeric)" in str(le._DRAIN_CANDIDATES)


# --- 後半14. drain SQLピン ---


def test_drain_sql_pins_order_and_guards():
    sql = str(le._DRAIN_CANDIDATES)
    assert "l.status = 'candidate'" in sql
    assert "l.expires_at > CAST(:now AS timestamptz)" in sql
    assert "ORDER BY target_time ASC, l.score DESC, l.id ASC" in sql
    assert "max(i.time_start)" in sql  # 対象時刻=相関サブクエリで都度導出


# --- 後半15. D-08カウントSQLピン(Review Focus 5) ---


def test_daily_count_sql_pins_type_and_jst_window():
    sql = str(le._COUNT_DAILY_NOTIFICATIONS)
    assert "type IN ('proposal', 'nearby_candidate')" in sql
    assert "created_at >= CAST(:day_start AS timestamptz)" in sql
    assert "created_at < CAST(:day_next AS timestamptz)" in sql


async def test_promote_daily_window_is_jst_day(monkeypatch):
    """day_start/day_nextはjst_day_start(clock.jst_date())由来+1日(0時リセット)。"""
    from latch.worker.matching.layer4 import jst_day_start

    log = _patch_promote(monkeypatch, row=_latch_row())
    clock = FakeClock(NOW)
    await _engine(clock=clock).try_promote(LID)
    day_start = jst_day_start(clock.jst_date())
    assert log["daily"][0][1:] == (day_start, day_start + timedelta(days=1))


# --- 後半16. _COUNT_OPEN_PROPOSEDピン ---


def test_count_open_proposed_pins_status_and_contains():
    sql = str(le._COUNT_OPEN_PROPOSED)
    assert "status IN ('proposed', 'partial_accept')" in sql
    assert "intent_ids @> ARRAY[CAST(:intent_id AS uuid)]" in sql


# --- 後半17. _SELECT_LATCH_FOR_UPDATEピン ---


def test_select_latch_for_update_pins():
    sql = str(le._SELECT_LATCH_FOR_UPDATE)
    assert "FOR UPDATE" in sql
    assert "WHERE id = CAST(:latch_id AS uuid)" in sql


# --- 後半18. _PROMOTE_LATCHピン ---


def test_promote_latch_pins_conditional_update():
    sql = str(le._PROMOTE_LATCH)
    assert (
        "SET status = 'proposed', response_deadline = CAST(:deadline AS timestamptz)"
        in sql
    )
    assert "AND status = 'candidate'" in sql
    assert "RETURNING id" in sql


# --- 後半19. _EXPIRE_LATCHピン ---


def test_expire_latch_pins_conditional_update():
    sql = str(le._EXPIRE_LATCH)
    assert "SET status = 'expired'" in sql
    assert "AND status = 'candidate'" in sql


# --- 後半追加(最終レビューC-1): _count_daily_notifications本体の要素数対応 ---


class _ScriptedResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _ScriptedConn:
    """本物SQLを流す想定の最小conn(パラメータ記録・行を返す)。"""

    def __init__(self, rows):
        self._rows = rows
        self.calls: list[dict] = []

    async def execute(self, stmt, params=None):
        self.calls.append(params)
        return _ScriptedResult(self._rows)


async def test_count_daily_notifications_single_user_no_unpack_error():
    """通知対象1名(片方muted等)でも正常動作(ANY配列で要素数に依存しない)。"""
    uid = _uid(1)
    conn = _ScriptedConn([(uid, 3)])
    out = await le._count_daily_notifications(conn, [uid], NOW, NOW + timedelta(days=1))
    assert out == {uid: 3}
    assert conn.calls[0]["users"] == "{" + f'"{uid}"' + "}"


async def test_count_daily_notifications_empty_users_skips_sql():
    """空リスト(全員muted)はSQLを実行せず{}(上限を消費しない)。"""
    conn = _ScriptedConn([])
    out = await le._count_daily_notifications(conn, [], NOW, NOW + timedelta(days=1))
    assert out == {}
    assert conn.calls == []


async def test_count_daily_notifications_two_users():
    u_a, u_b = _uid(1), _uid(101)
    conn = _ScriptedConn([(u_a, 2), (u_b, 6)])
    out = await le._count_daily_notifications(
        conn, [u_a, u_b], NOW, NOW + timedelta(days=1)
    )
    assert out == {u_a: 2, u_b: 6}
    assert conn.calls[0]["users"] == (
        "{" + f'"{u_a}"' + "," + f'"{u_b}"' + "}"
    )  # uuid_array_text形式


# -- グループ共存改修・I-1改修(M2 ws-7・design §2.6〜2.7) --


def test_select_targets_excludes_group_pairs():
    """グループ所属ペアはlatch_score計算対象外(design §2.7-1)。"""
    sql = str(le._SELECT_TARGETS)
    assert f"AND NOT {layer4.GROUP_PAIR_EXISTS}" in sql


def test_count_daily_notifications_uses_any_uuid_array():
    """|S|人対応: user_id = ANY(:users)(design §2.7-2)。"""
    sql = str(le._COUNT_DAILY_NOTIFICATIONS)
    assert "user_id = ANY(CAST(:users AS uuid[]))" in sql
    assert "IN (CAST(:u0" not in sql


def test_select_latch_for_update_carries_group_columns():
    sql = str(le._SELECT_LATCH_FOR_UPDATE)
    assert "group_candidate_id" in sql
    assert ", score" in sql


def test_higher_group_latch_sql_pins():
    sql = str(le._SELECT_HIGHER_GROUP_LATCH)
    assert "l.group_candidate_id IS NOT NULL" in sql
    assert "l.intent_ids && CAST(:my_ids AS uuid[])" in sql
    assert "l.id <> CAST(:self AS uuid)" in sql


def _patch_for_promote(
    monkeypatch,
    *,
    group_candidate_id=None,
    score=Decimal("0.85"),
    higher=False,
):
    """try_promoteのDB部品を記録スタブへ(グループ列つき6要素行・D-06制御)。

    _select_latch_for_updateは(latch_id, status, ids, expires,
    group_candidate_id, score)を返す。_read_participantsは3要素idsに
    対しParticipant 3件を返す。
    """
    log = {
        "row": None,
        "parts": None,
        "higher": [],
        "daily": [],
        "open": [],
        "expire": [],
        "promote_latch": [],
        "events": [],
        "notifications": [],
    }
    ids = [_uid(1), _uid(2), _uid(3)]

    async def fake_select_lu(conn, latch_id):
        log["row"] = latch_id
        return (
            NEW_LATCH_ID,
            "candidate",
            ids,
            NOW + timedelta(days=5),
            group_candidate_id,
            score,
        )

    async def fake_read_parts(conn, intent_ids):
        log["parts"] = tuple(intent_ids)
        return [
            le.Participant(
                intent_id=iid,
                user_id=_uid(500 + i),
                notification_level="proposals_only",
                time_start=NOW + timedelta(hours=30),
                expires_at=NOW + timedelta(days=5),
            )
            for i, iid in enumerate(intent_ids)
        ]

    async def fake_higher(conn, self_id, self_ids, self_score):
        log["higher"].append((self_id, tuple(self_ids), self_score))
        return higher

    async def fake_daily(conn, user_ids, day_start, day_next):
        log["daily"].append((tuple(user_ids), day_start, day_next))
        return {}

    async def fake_open(conn, intent_id):
        log["open"].append(intent_id)
        return 0

    async def fake_expire(conn, latch_id, now):
        log["expire"].append((latch_id, now))
        return True

    async def fake_promote_latch(conn, latch_id, deadline, now):
        log["promote_latch"].append((latch_id, deadline, now))
        return True

    async def fake_event(conn, latch_id, from_status, to_status, user_id, now):
        log["events"].append((latch_id, from_status, to_status, user_id))

    async def fake_notification(conn, user_id, ntype, latch_id, now):
        log["notifications"].append((user_id, ntype, latch_id))

    monkeypatch.setattr(le, "_select_latch_for_update", fake_select_lu)
    monkeypatch.setattr(le, "_read_participants", fake_read_parts)
    monkeypatch.setattr(le, "_has_higher_group_latch", fake_higher)
    monkeypatch.setattr(le, "_count_daily_notifications", fake_daily)
    monkeypatch.setattr(le, "_count_open_proposed", fake_open)
    monkeypatch.setattr(le, "_expire_latch", fake_expire)
    monkeypatch.setattr(le, "_promote_latch", fake_promote_latch)
    monkeypatch.setattr(le, "_insert_latch_event", fake_event)
    monkeypatch.setattr(le, "_insert_notification", fake_notification)
    return log


async def test_try_promote_skips_when_higher_group_latch_exists(monkeypatch):
    """D-06: メンバーが重なる上位集合があればproposed化しない(design §2.6)。"""

    class _Conn:  # _has_higher_group_latchがTrueを返すスタブ
        async def execute(self, stmt, params=None):
            class _R:
                def fetchall(self):
                    return [(Decimal("0.9"), [_uid(1), _uid(2), _uid(9)])]

            return _R()

    assert (
        await le._has_higher_group_latch(
            _Conn(), _uid(5), [_uid(1), _uid(2), _uid(3)], 0.85
        )
        is True
    )  # 0.9 > 0.85で上位

    # try_promote本体内: 上位あり→_promote_latch呼ばれない
    log = _patch_for_promote(
        monkeypatch, group_candidate_id=_uid(80), score=Decimal("0.85"), higher=True
    )
    await _engine().try_promote(NEW_LATCH_ID)
    assert log["promote_latch"] == []
    assert log["events"] == []  # proposed遷移イベントなし(candidateのまま)


async def test_try_promote_promotes_group_when_no_higher(monkeypatch):
    """上位なし(または1対1行)は従来どおりproposed化。"""
    log = _patch_for_promote(
        monkeypatch, group_candidate_id=_uid(80), score=Decimal("0.85"), higher=False
    )
    await _engine().try_promote(NEW_LATCH_ID)
    assert [e[0] for e in log["promote_latch"]] == [NEW_LATCH_ID]
    assert ("candidate", "proposed") in [(e[1], e[2]) for e in log["events"]]


async def test_try_promote_one_on_one_skips_d06_check(monkeypatch):
    """group_candidate_id NULL(1対1)はD-06チェックを行わない。"""
    log = _patch_for_promote(
        monkeypatch, group_candidate_id=None, score=Decimal("0.85"), higher=True
    )
    await _engine().try_promote(NEW_LATCH_ID)
    assert [e[0] for e in log["promote_latch"]] == [NEW_LATCH_ID]  # higher=Trueでも昇格


async def test_evaluate_pair_missing_peer_inputs_keeps_score_null(monkeypatch):
    """I-1改修: peer入力欠損→_record_scoreを呼ばない(latch_score NULL維持)。"""
    log = _patch(
        monkeypatch,
        org=_origin(),
        rows=[_row(1)],
        inputs={_uid(1): _inputs(1)},  # peer(_uid(101))の入力なし
    )
    await _engine().handle(_uid(1))
    assert log["record"] == []  # 計算だけ成功させて生成物なし、を作らない
    assert log["insert"] == []


async def test_evaluate_pair_completes_after_peer_inputs_restored(monkeypatch):
    """復旧後の再handleでlatches生成まで完走(I-1改修の回収経路)。"""
    org = _origin()
    inputs = {_uid(1): _inputs(1), _uid(101): _inputs(101)}
    # 1回目: peer欠損
    log1 = _patch(monkeypatch, org=org, rows=[_row(1)], inputs={_uid(1): _inputs(1)})
    await _engine().handle(_uid(1))
    assert log1["record"] == []
    # 2回目: 完全な入力(latch_score IS NULLガードで再選択される)
    log2 = _patch(monkeypatch, org=org, rows=[_row(1, wa=0.9, wb=0.85)], inputs=inputs)
    await _engine().handle(_uid(1))
    assert [c[1] for c in log2["record"]] == [0.85]  # LATCH_C×min(0.9,0.85)
    assert log2["insert"]  # latches生成
    assert (None, "candidate") in [(e[1], e[2]) for e in log2["events"]]
