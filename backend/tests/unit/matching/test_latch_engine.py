"""LatchEngine.handle(選択・latch_score・latches生成・昇格・nearby・提示)のunit試験。

design §2.2〜2.7・§4.1。DB操作はlatch_engineのモジュール関数をmonkeypatch
して分岐ロジックを検証する(runnerと同一規律)。SQL文字列はtext()定数への
直接ピンで検証する。
"""

import uuid
from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.worker.matching import latch_engine as le
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
    monkeypatch.setattr(le.LatchEngine, "_try_promote", fake_promote)
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
    """_record_score None(他の実行が先に計算済み) → 以降の読取なし。"""
    log = _patch(monkeypatch, org=_origin(1), rows=[_row(1)], record_result=None)
    await _engine().handle(_uid(1))
    assert log["record"]
    assert log["read_inputs"] == []
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
