"""latchesドメインservice・例外・スキーマのunit試験(M3 ws-1 design §4.1)。

storeはスタブ(行値を返すだけ)でSQLに依存しない。時刻はFakeClock。
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from latch.core.clock import FakeClock
from latch.latches.errors import (
    AlreadyAnsweredError,
    DependencyUnavailableError,
    ForbiddenError,
    LatchClosedError,
    LatchesError,
    LatchExpiredError,
    LatchNotFoundError,
    LatchValidationError,
)
from latch.latches.schemas import (
    LatchDetailOut,
    LatchSummaryOut,
    ParticipantOut,
    ResponseRequest,
)
from latch.latches.service import LatchesService, compute_new_status

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def test_error_codes_and_statuses():
    """例外はhttp_status+code固定(design §2.9の対応表・05 §5エラー形式)。"""
    cases = [
        (LatchNotFoundError, 404, "NOT_FOUND"),
        (ForbiddenError, 403, "FORBIDDEN"),
        (LatchValidationError, 422, "VALIDATION_ERROR"),
        (LatchExpiredError, 409, "LATCH_EXPIRED"),
        (AlreadyAnsweredError, 409, "ALREADY_ANSWERED"),
        (LatchClosedError, 409, "LATCH_CLOSED"),
        (DependencyUnavailableError, 503, "DEPENDENCY_UNAVAILABLE"),
    ]
    for exc_cls, status, code in cases:
        assert exc_cls.http_status == status, exc_cls
        assert exc_cls.code == code, exc_cls
        assert issubclass(exc_cls, LatchesError)


def test_response_request_accepts_three_values_only():
    """responseはyes/no/deferの3値のみ(引用#2)。値域外はValidationError。"""
    assert ResponseRequest(response="yes").response == "yes"
    assert ResponseRequest(response="no").response == "no"
    assert ResponseRequest(response="defer").response == "defer"
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ResponseRequest(response="maybe")


def test_summary_shape_has_no_responses_field():
    """LatchSummaryOutはresponses配列を持たない(引用#22・Review Focus 5)。

    応答へ他者の回答種別が漏れない構造ピン。
    """
    fields = set(LatchSummaryOut.model_fields)
    assert "responses" not in fields
    assert fields == {
        "id",
        "status",
        "response_deadline",
        "expires_at",
        "created_at",
        "completed_at",
        "proposal",
        "is_group",
        "my_response",
        "remaining_responses",
    }


def test_detail_extends_summary_with_release_fields():
    """詳細は一覧要素+解放情報3字段(matched/completedのみ値が入る・design §2.8)。"""
    assert set(LatchDetailOut.model_fields) - set(LatchSummaryOut.model_fields) == {
        "participants",
        "time_summary",
        "area_name",
    }
    assert "user_id" in ParticipantOut.model_fields
    assert "display_name" in ParticipantOut.model_fields


def _latch_row(**overrides) -> object:
    """store.LatchRow相当のスタブ(serviceは属性アクセスのみ)。"""
    base = dict(
        id=uuid.uuid4(),
        status="proposed",
        intent_ids=[uuid.uuid4(), uuid.uuid4()],
        responses=[],
        response_deadline=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(days=5),
        score=Decimal("0.85"),
        proposal={"headcount": 2, "match_level": "medium"},
        group_candidate_id=None,
        created_at=NOW - timedelta(hours=1),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class StubStore:
    """store関数のスタブ(serviceはモジュール関数をmonkeypatchで差し替え)。"""


def test_compute_new_status_matrix():
    """手順3の純計算(1対1・3人・no/defer・design §2.4)。"""
    # 1対1: 片方yes→partial(必要人数2・まだ1)/揃う→matched
    assert compute_new_status("yes", [{"response": "yes"}], 2) == "partial_accept"
    assert (
        compute_new_status("yes", [{"response": "yes"}, {"response": "yes"}], 2)
        == "matched"
    )
    # 3人: 2人yesのあとの3人目yes→matched(必要人数=|S|・D-06)
    assert (
        compute_new_status(
            "yes",
            [{"response": "yes"}, {"response": "yes"}, {"response": "yes"}],
            3,
        )
        == "matched"
    )
    assert (
        compute_new_status("yes", [{"response": "yes"}, {"response": "yes"}], 3)
        == "partial_accept"
    )
    # no/deferは即rejected(グループでも部分成立なし・引用#10)
    assert compute_new_status("no", [{"response": "no"}], 2) == "rejected"
    assert (
        compute_new_status("defer", [{"response": "yes"}, {"response": "defer"}], 3)
        == "rejected"
    )


class FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class ScriptedConn:
    """store呼び出しの結果を順に返すスタブ(intentsのStubStoreより薄い層)。"""

    def __init__(self, results: list):
        self._results = list(results)

    async def execute(self, stmt, params=None):
        if self._results:
            return self._results.pop(0)
        return FakeResult()


class ScriptedEngine:
    def __init__(self, results: list):
        self.conn = ScriptedConn(results)

    def begin(self):
        return self

    def connect(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


def _svc(engine) -> LatchesService:
    return LatchesService(clock=FakeClock(NOW), engine=engine, geo=None)


ME = uuid.uuid4()
PEER = uuid.uuid4()
I1, I2 = uuid.uuid4(), uuid.uuid4()
ROW = _latch_row()


def _mapping(row) -> dict:
    """store._latch_row相当のmapping(dict)。select_latch_for_update用。"""
    return {
        "id": row.id,
        "status": row.status,
        "intent_ids": row.intent_ids,
        "responses": row.responses,
        "response_deadline": row.response_deadline,
        "expires_at": row.expires_at,
        "score": row.score,
        "proposal": row.proposal,
        "group_candidate_id": row.group_candidate_id,
        "created_at": row.created_at,
    }


def _ret(value):
    """monkeypatch差し替え用の「常にvalueを返すasync関数」ファクトリ。"""

    async def _inner(*args, **kwargs):
        return value

    return _inner


async def test_respond_unregistered_jwt_404(monkeypatch):
    """未登録JWTは404(引用#17・intentsと同型)。"""
    from latch.latches import store as store_mod

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(None))
    with pytest.raises(LatchNotFoundError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="unknown",
            latch_id=uuid.uuid4(),
            response="yes",
        )


async def test_respond_participant_forbidden(monkeypatch):
    """参加者以外は403(存在秘匿しない・引用#17)。"""
    from latch.latches import store as store_mod

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(ROW))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(None))
    with pytest.raises(ForbiddenError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="s",
            latch_id=ROW.id,
            response="yes",
        )


async def test_respond_classification_matrix(monkeypatch):
    """手順2の分類表を全分岐(期限切れ・既回答・終端済み・candidate・design §2.9)。"""
    from latch.latches import store as store_mod

    # 参加Intentは常に自分のもの
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))

    # 期限切れ(status=proposedのまま・期限後回答は409 LATCH_EXPIRED・引用#6)
    expired = _latch_row(response_deadline=NOW - timedelta(minutes=1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(expired))
    with pytest.raises(LatchExpiredError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="s",
            latch_id=expired.id,
            response="yes",
        )

    # expires_at切れもLATCH_EXPIRED
    expired2 = _latch_row(expires_at=NOW - timedelta(minutes=1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(expired2))
    with pytest.raises(LatchExpiredError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="s",
            latch_id=expired2.id,
            response="yes",
        )

    # 終端済み(rejected等・期限前)はLATCH_CLOSED
    closed = _latch_row(status="rejected")
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(closed))
    with pytest.raises(LatchClosedError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="s",
            latch_id=closed.id,
            response="yes",
        )

    # 未提示candidateもLATCH_CLOSED(引用#24)
    cand = _latch_row(status="candidate")
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(cand))
    with pytest.raises(LatchClosedError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="s",
            latch_id=cand.id,
            response="yes",
        )

    # 二重回答はALREADY_ANSWERED(期限前・proposed)
    answered = _latch_row(responses=[{"user_id": str(ME), "response": "yes"}])
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(answered))
    with pytest.raises(AlreadyAnsweredError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="s",
            latch_id=answered.id,
            response="yes",
        )


async def test_respond_update_conflict_reraises_classification(monkeypatch):
    """手順5: UPDATE影響0は事前検査の分類で409(削除レース等・引用#4)。"""
    from latch.latches import store as store_mod

    row = _latch_row()  # proposed・期限前
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "update_response", _ret(None))  # 影響0
    with pytest.raises(LatchClosedError):  # 期限前のためCLOSED(EXISTS検査負け)
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="s",
            latch_id=row.id,
            response="yes",
        )


async def test_respond_yes_full_flow_partial_then_matched(monkeypatch):
    """1対1: 1人目yes→partial_accept・2人目yes→matched(イベント・matched化)。"""
    from latch.latches import store as store_mod

    calls: list[tuple[str, dict]] = []

    async def _event(
        conn, latch_id, from_status=None, to_status=None, user_id=None, now=None
    ):
        calls.append(("event", {"from": from_status, "to": to_status, "uid": user_id}))

    async def _match(conn, ids, now):
        calls.append(("match", {"ids": ids}))
        return list(ids)

    async def _conflicting(conn, self_id, member_ids):
        calls.append(("conflicting", {"self": self_id}))
        return []

    async def _cancel(conn, latch_id):
        return False

    async def _calibration(conn=None, **kwargs):
        calls.append(("calibration", kwargs))
        return uuid.uuid4()

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "insert_latch_event", _event)
    monkeypatch.setattr(store_mod, "match_intents", _match)
    monkeypatch.setattr(store_mod, "select_conflicting_latches", _conflicting)
    monkeypatch.setattr(store_mod, "cancel_latch", _cancel)
    monkeypatch.setattr(store_mod, "insert_calibration", _calibration)
    # 評価行あり(第1段一致)でCalibration作成まで通す
    from latch.latches.calibration import EvalRow

    eval_row = EvalRow(
        jev_result={
            "would_a_accept_b": 0.6,
            "would_b_accept_a": 0.7,
            "jev_5axis": {},
            "provider": "typesafe_jev",
            "model": "jev-1.13.0",
        },
        latch_score=Decimal("0.85"),
        updated_at=NOW,
    )
    monkeypatch.setattr(store_mod, "fetch_pair_rows", _ret([eval_row]))
    monkeypatch.setattr(store_mod, "fetch_intents_structured", _ret([{}, {}]))

    # 1人目yes → partial_accept
    row1 = _latch_row()
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row1))
    monkeypatch.setattr(store_mod, "update_response", _ret((row1.id, "partial_accept")))
    out = await _svc(ScriptedEngine([])).respond(
        auth_provider="google",
        auth_subject="s",
        latch_id=row1.id,
        response="yes",
    )
    assert out.status == "partial_accept"
    assert out.my_response == "yes"
    assert out.remaining_responses == 1  # 2人中1人yes
    assert calls[0] == (
        "event",
        {"from": "proposed", "to": "partial_accept", "uid": ME},
    )
    # partial_acceptではmatched化もCalibrationもしない(design §2.2手順6d)
    assert not any(c[0] == "match" for c in calls)
    assert not any(c[0] == "calibration" for c in calls)

    # 2人目yes → matched(イベント・Intent matched化・Calibration)
    # row2の事前responsesは1人目(PEER)のyes・回答者はME
    calls.clear()
    row2 = _latch_row(responses=[{"user_id": str(PEER), "response": "yes"}])
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row2))
    monkeypatch.setattr(store_mod, "update_response", _ret((row2.id, "matched")))
    out = await _svc(ScriptedEngine([])).respond(
        auth_provider="google",
        auth_subject="s",
        latch_id=row2.id,
        response="yes",
    )
    assert out.status == "matched"
    assert out.remaining_responses == 0  # matched以降は0(design §2.8)
    assert calls[0][0] == "event"  # partial_accept→matched(user_id=回答者)
    assert any(c[0] == "match" for c in calls)
    assert any(c[0] == "calibration" for c in calls)  # matched=Trueで呼ばれる
    assert any(c[0] == "conflicting" for c in calls)


async def test_respond_no_creates_rejected_calibration(monkeypatch):
    """no→rejectedでCalibration作成(matched=False・design §2.5案A)。"""
    from latch.latches import store as store_mod
    from latch.latches.calibration import EvalRow

    cal_calls: list[dict] = []

    async def _calibration(conn=None, **kwargs):
        cal_calls.append(kwargs)
        return uuid.uuid4()

    row = _latch_row()
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "update_response", _ret((row.id, "rejected")))
    monkeypatch.setattr(store_mod, "insert_calibration", _calibration)
    # 評価行あり(第1段: latch_score一致)でCalibration作成まで通す
    monkeypatch.setattr(
        store_mod,
        "fetch_pair_rows",
        _ret(
            [
                EvalRow(
                    jev_result={
                        "would_a_accept_b": 0.6,
                        "would_b_accept_a": 0.7,
                        "jev_5axis": {},
                        "provider": "typesafe_jev",
                        "model": "jev-1.13.0",
                    },
                    latch_score=Decimal("0.85"),
                    updated_at=NOW,
                )
            ]
        ),
    )
    monkeypatch.setattr(store_mod, "fetch_intents_structured", _ret([{}, {}]))
    out = await _svc(ScriptedEngine([])).respond(
        auth_provider="google",
        auth_subject="s",
        latch_id=row.id,
        response="no",
    )
    assert out.status == "rejected"
    assert len(cal_calls) == 1
    assert cal_calls[0]["matched"] is False
    assert cal_calls[0]["responses"] == [
        {
            "user_id": str(ME),
            "intent_id": str(I1),
            "response": "no",
            "answered_at": NOW.isoformat(),
        }
    ]


async def test_respond_matched_intent_shortfall_503(monkeypatch):
    """matched化の戻り行数不一致はtx失敗(503・design §2.3の防御)。"""
    from latch.latches import store as store_mod

    # 1人目(PEER)は回答済み・自分(ME)は未回答の状態でmatchedへ向かう
    row = _latch_row(responses=[{"user_id": str(PEER), "response": "yes"}])
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "update_response", _ret((row.id, "matched")))
    monkeypatch.setattr(store_mod, "insert_latch_event", _ret(None))
    monkeypatch.setattr(store_mod, "match_intents", _ret([]))  # 0行=全員不一致
    with pytest.raises(DependencyUnavailableError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="s",
            latch_id=row.id,
            response="yes",
        )


async def test_respond_calibration_missing_row_logs_and_succeeds(monkeypatch):
    """評価行が特定できない場合はレコードを作らず回答自体は成功(design §2.5)。"""
    from latch.latches import store as store_mod

    row = _latch_row()
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "update_response", _ret((row.id, "rejected")))
    monkeypatch.setattr(store_mod, "insert_latch_event", _ret(None))
    monkeypatch.setattr(store_mod, "insert_calibration", _ret(uuid.uuid4()))
    monkeypatch.setattr(store_mod, "fetch_pair_rows", _ret([]))  # 評価行ゼロ→全段失敗
    out = await _svc(ScriptedEngine([])).respond(
        auth_provider="google",
        auth_subject="s",
        latch_id=row.id,
        response="no",
    )
    assert out.status == "rejected"  # 回答は成功
