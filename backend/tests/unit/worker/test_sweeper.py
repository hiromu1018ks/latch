"""ExpirySweeperのunit試験(M3 ws-2 design §2.2〜2.4・§2.7・§4.1)。

SQLピン(対象条件・SKIP LOCKED・LIMIT・部分索引条件との一致)とcompile検査は
SQL定数へ直接(reeval流儀)。run_onceの分岐は抽出関数をmonkeypatchで差し替え、
行単位処理はFakeEngine/FakeConnスタブで検証する。
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.worker import sweeper as sweeper_mod
from latch.worker.sweeper import (
    _COMPLETE_LATCH,
    _EXPIRE_CANDIDATE_LATCH,
    _EXPIRE_INTENT,
    _EXPIRE_RESPONSE_LATCH,
    _INSERT_ATTENDANCE_NOTIFICATION,
    _INSERT_LATCH_EVENT_SQL,
    _SELECT_COMPLETION_TARGETS,
    _SELECT_EXPIRING_INTENTS,
    _SELECT_EXPIRING_LATCHES,
    _SELECT_LATCH_PARTICIPANT_USERS,
    _SELECT_RECENT_CLOSE,
    ExpirySweeper,
)

LATCH1 = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
LATCH2 = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
INTENT1 = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
INTENT2 = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
USER1 = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
USER2 = uuid.UUID("00000000-0000-4000-8000-0000000000c2")

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


# --- 1. latches期限切れ抽出SQLピン(引用#2・design §2.2) ---


def test_select_expiring_latches_pins_conditions():
    sql = str(_SELECT_EXPIRING_LATCHES)
    assert "status IN ('proposed', 'partial_accept')" in sql
    assert "response_deadline <= CAST(:now AS timestamptz)" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    # candidateは保留キュー(期限のみ)
    assert "status = 'candidate' AND expires_at <= CAST(:now AS timestamptz)" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT :batch_limit" in sql


def test_expire_response_latch_pins_same_truth_as_response_api():
    """proposed/partial_accept行のUPDATEは回答API(_UPDATE_RESPONSE)と同一の
    真実(期限とstatus)を再検査する(引用#3・C9)。"""
    sql = str(_EXPIRE_RESPONSE_LATCH)
    assert "SET status = 'expired'" in sql
    assert "status IN ('proposed', 'partial_accept')" in sql
    assert "response_deadline <= CAST(:now AS timestamptz)" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "RETURNING id" in sql


def test_expire_candidate_ignores_response_deadline():
    """candidate行はresponse_deadline(昇格時への暫定値)を判定に使わない
    (引用#2・Review Focus 2)。"""
    sql = str(_EXPIRE_CANDIDATE_LATCH)
    assert "SET status = 'expired'" in sql
    assert "status = 'candidate'" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "response_deadline" not in sql
    assert "RETURNING id" in sql


# --- 2. Intent期限切れSQLピン(引用#5/#6・design §2.3) ---


def test_select_expiring_intents_matches_partial_index():
    """抽出WHEREはidx_intents_expiresの部分索引条件と同一(design引用#6)。"""
    sql = str(_SELECT_EXPIRING_INTENTS)
    assert "status IN ('draft', 'active', 'paused')" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT :batch_limit" in sql


def test_expire_intent_pins_update_and_version_return():
    sql = str(_EXPIRE_INTENT)
    assert "SET status = 'expired', updated_at = CAST(:now AS timestamptz)" in sql
    assert "status IN ('draft', 'active', 'paused')" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "RETURNING version" in sql


# --- 3. matched→completed SQLピン(引用#15・design §2.4) ---


def test_select_completion_targets_pins_max_time_start():
    sql = str(_SELECT_COMPLETION_TARGETS)
    assert "l.status = 'matched'" in sql
    assert "(SELECT max(i.time_start) FROM intents i" in sql
    assert "<= CAST(:now AS timestamptz)" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


def test_complete_latch_pins_matched_guard_and_completed_at():
    """cancelled(解散済み)はstatus='matched'でなく対象外=通知を送らない
    ことを構造的に担保(引用#15・Review Focus 3)。"""
    sql = str(_COMPLETE_LATCH)
    assert "SET status = 'completed', completed_at = CAST(:now AS timestamptz)" in sql
    assert "AND status = 'matched'" in sql
    assert "RETURNING intent_ids" in sql


# --- 4. クローズ検知観測SQLピン(design §2.7) ---


def test_select_recent_close_pins_window_and_statuses():
    sql = str(_SELECT_RECENT_CLOSE)
    assert "created_at > CAST(:last_tick AS timestamptz)" in sql
    assert "to_status IN ('rejected', 'expired', 'cancelled', 'matched')" in sql
    assert "LIMIT 1" in sql


# --- 5. compile検査(postgresql dialectで未変換の ':name' が残らない) ---


def test_sql_bind_params_compile():
    from sqlalchemy.dialects import postgresql

    targets = {
        _SELECT_EXPIRING_LATCHES: {"now", "batch_limit"},
        _EXPIRE_RESPONSE_LATCH: {"latch_id", "now"},
        _EXPIRE_CANDIDATE_LATCH: {"latch_id", "now"},
        _SELECT_EXPIRING_INTENTS: {"now", "batch_limit"},
        _EXPIRE_INTENT: {"intent_id", "now"},
        _SELECT_COMPLETION_TARGETS: {"now", "batch_limit"},
        _COMPLETE_LATCH: {"latch_id", "now"},
        _SELECT_LATCH_PARTICIPANT_USERS: {"ids"},
        _INSERT_LATCH_EVENT_SQL: {
            "latch_id",
            "from_status",
            "to_status",
            "user_id",
            "now",
        },
        _INSERT_ATTENDANCE_NOTIFICATION: {"user_id", "type", "payload", "now"},
        _SELECT_RECENT_CLOSE: {"last_tick"},
    }
    for stmt, keys in targets.items():
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        for key in keys:
            assert f":{key}" not in compiled, (key, compiled)


# --- 6. 行単位txスタブ(FakeEngine/FakeConn) ---


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """SQL定数→戻り値の辞書。実行記録(stmt, params)を保持。"""

    def __init__(self, results=None):
        self.calls: list[tuple[object, dict]] = []
        self._results = results or {}

    async def execute(self, stmt, params=None):
        self.calls.append((stmt, dict(params or {})))
        return FakeResult(self._results.get(stmt))


class _BeginCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakeEngine:
    """engine.begin()のスタブ(単一connを返す・begin回数を記録)。"""

    def __init__(self, conn):
        self._conn = conn
        self.begins = 0

    def begin(self):
        self.begins += 1
        return _BeginCtx(self._conn)


class RecordingLatch:
    """LatchEngineスタブ(drain呼び出し記録)。"""

    def __init__(self):
        self.drains = 0

    async def drain(self):
        self.drains += 1


def _sweeper(engine=None, clock=None, latch=None, batch_limit=50):
    return ExpirySweeper(
        engine=engine or object(),  # 抽出関数はmonkeypatchで差し替え
        clock=clock or FakeClock(NOW),
        latch=latch or RecordingLatch(),
        batch_limit=batch_limit,
    )


# --- 7. _expire_latch: 条件付きUPDATE+イベント同一tx(§4.1-2/3) ---


async def test_expire_latch_proposed_writes_event_same_tx():
    """proposed行: 影響1→expired遷移+イベントを同一tx(user_id=NULL)。"""
    conn = FakeConn({_EXPIRE_RESPONSE_LATCH: [("ok",)]})
    engine = FakeEngine(conn)
    sw = _sweeper(engine=engine)
    assert await sw._expire_latch(LATCH1, "proposed", NOW) is True
    assert engine.begins == 1  # 単一tx
    stmts = [s for s, _ in conn.calls]
    assert stmts == [_EXPIRE_RESPONSE_LATCH, _INSERT_LATCH_EVENT_SQL]
    params = conn.calls[1][1]
    assert params["from_status"] == "proposed"
    assert params["to_status"] == "expired"
    assert params["user_id"] is None  # システム起因(引用#13)
    assert params["now"] == NOW  # 1tick=1時刻


async def test_expire_latch_candidate_uses_candidate_sql():
    """candidate行: candidate用UPDATE(期限のみ)を使う。"""
    conn = FakeConn({_EXPIRE_CANDIDATE_LATCH: [("ok",)]})
    engine = FakeEngine(conn)
    sw = _sweeper(engine=engine)
    assert await sw._expire_latch(LATCH1, "candidate", NOW) is True
    assert conn.calls[0][0] is _EXPIRE_CANDIDATE_LATCH
    assert conn.calls[1][1]["from_status"] == "candidate"


async def test_expire_latch_no_row_no_event():
    """影響0(他経路で遷移済み)→イベント挿入しない・False(引用#3・
    Review Focus 1)。"""
    conn = FakeConn({_EXPIRE_RESPONSE_LATCH: []})
    sw = _sweeper(engine=FakeEngine(conn))
    assert await sw._expire_latch(LATCH1, "proposed", NOW) is False
    assert len(conn.calls) == 1  # UPDATEのみ・イベントなし


# --- 8. _expire_intent: expired化+expiredイベント同一tx(§4.1-3) ---


async def test_expire_intent_publishes_expired_event_same_tx(monkeypatch):
    """影響1→UPDATE RETURNING versionでexpiredイベントを同一tx発行(引用#19)。"""
    events = []

    async def fake_insert(conn, *, event_type, intent_id, version, now):
        events.append((event_type, intent_id, version, now))

    monkeypatch.setattr(sweeper_mod, "insert_match_event", fake_insert)
    conn = FakeConn({_EXPIRE_INTENT: [(3,)]})  # RETURNING version=3
    engine = FakeEngine(conn)
    sw = _sweeper(engine=engine)
    assert await sw._expire_intent(INTENT1, NOW) is True
    assert engine.begins == 1  # 同一tx
    assert events == [("expired", INTENT1, 3, NOW)]


async def test_expire_intent_no_row_no_event(monkeypatch):
    async def fake_insert(conn, **kwargs):  # pragma: no cover - 呼ばれない
        raise AssertionError("影響0ではイベント発行しない")

    monkeypatch.setattr(sweeper_mod, "insert_match_event", fake_insert)
    conn = FakeConn({_EXPIRE_INTENT: []})
    sw = _sweeper(engine=FakeEngine(conn))
    assert await sw._expire_intent(INTENT1, NOW) is False


# --- 9. _complete_latch: completed化+イベント+attendance通知(§4.1-3) ---


async def test_complete_latch_writes_event_then_notifications():
    """影響1→イベント(matched→completed・user_id=NULL)→参加者全員へ
    attendance_request通知(payload={latch_id}最小参照)。同一tx。"""
    conn = FakeConn(
        {
            _COMPLETE_LATCH: [([INTENT1, INTENT2],)],  # RETURNING intent_ids
            _SELECT_LATCH_PARTICIPANT_USERS: [(USER1,), (USER2,)],
        }
    )
    engine = FakeEngine(conn)
    sw = _sweeper(engine=engine)
    assert await sw._complete_latch(LATCH1, NOW) is True
    assert engine.begins == 1  # 単一tx
    stmts = [s for s, _ in conn.calls]
    assert stmts == [
        _COMPLETE_LATCH,
        _INSERT_LATCH_EVENT_SQL,
        _SELECT_LATCH_PARTICIPANT_USERS,
        _INSERT_ATTENDANCE_NOTIFICATION,
        _INSERT_ATTENDANCE_NOTIFICATION,
    ]
    ev = conn.calls[1][1]
    assert ev["from_status"] == "matched"
    assert ev["to_status"] == "completed"
    assert ev["user_id"] is None
    notified = [conn.calls[3][1]["user_id"], conn.calls[4][1]["user_id"]]
    assert notified == [USER1, USER2]
    n1 = conn.calls[3][1]
    assert n1["type"] == "attendance_request"
    assert json.loads(n1["payload"]) == {"latch_id": str(LATCH1)}
    assert n1["now"] == NOW


async def test_complete_latch_no_row_no_side_effects():
    conn = FakeConn({_COMPLETE_LATCH: []})
    sw = _sweeper(engine=FakeEngine(conn))
    assert await sw._complete_latch(LATCH1, NOW) is False
    assert len(conn.calls) == 1


# --- 10. run_once: 処理順序(書き込み→観測→drain)・クローズ分岐・last_tick ---


async def test_run_once_order_and_drain_on_close(monkeypatch):
    """処理順序はlatches→intents→completed→観測→drain(§2.7・Review Focus 4)。
    クローズあり→drain呼出・last_tick更新。"""
    calls = []

    async def sel_latches(engine, now, limit):
        calls.append("select_latches")
        return [(LATCH1, "proposed")]

    async def sel_intents(engine, now, limit):
        calls.append("select_intents")
        return [(INTENT1, "active")]

    async def sel_completed(engine, now, limit):
        calls.append("select_completed")
        return [LATCH2]

    monkeypatch.setattr(sweeper_mod, "_select_expiring_latches", sel_latches)
    monkeypatch.setattr(sweeper_mod, "_select_expiring_intents", sel_intents)
    monkeypatch.setattr(sweeper_mod, "_select_completion_targets", sel_completed)

    async def fake_close(engine, last_tick):
        calls.append(f"observe:{last_tick == NOW}")  # 初回last_tick=起動時刻
        return True

    monkeypatch.setattr(sweeper_mod, "_has_recent_close", fake_close)
    latch = RecordingLatch()
    sw = _sweeper(latch=latch)

    async def fake_expire_l(latch_id, from_status, now):
        calls.append(f"expire_latch:{latch_id}:{from_status}")
        return True

    async def fake_expire_i(intent_id, now):
        calls.append(f"expire_intent:{intent_id}")
        return True

    async def fake_complete(latch_id, now):
        calls.append(f"complete:{latch_id}")
        return True

    monkeypatch.setattr(sw, "_expire_latch", fake_expire_l)
    monkeypatch.setattr(sw, "_expire_intent", fake_expire_i)
    monkeypatch.setattr(sw, "_complete_latch", fake_complete)

    done = await sw.run_once()
    assert done == 3  # 遷移行数(§9-1)
    assert calls == [
        "select_latches",
        f"expire_latch:{LATCH1}:proposed",
        "select_intents",
        f"expire_intent:{INTENT1}",
        "select_completed",
        f"complete:{LATCH2}",
        "observe:True",
    ]
    assert latch.drains == 1  # クローズ観測→drain(順序: 観測がdrainの前)
    assert sw._last_tick == NOW  # 成功時に更新


async def test_run_once_no_close_no_drain(monkeypatch):
    """観測なし→drain呼ばない・last_tickは更新する。"""

    async def sel_latches(engine, now, limit):
        return []

    async def sel_intents(engine, now, limit):
        return []

    async def sel_completed(engine, now, limit):
        return []

    async def fake_close(engine, last_tick):
        return False

    monkeypatch.setattr(sweeper_mod, "_select_expiring_latches", sel_latches)
    monkeypatch.setattr(sweeper_mod, "_select_expiring_intents", sel_intents)
    monkeypatch.setattr(sweeper_mod, "_select_completion_targets", sel_completed)
    monkeypatch.setattr(sweeper_mod, "_has_recent_close", fake_close)
    latch = RecordingLatch()
    sw = _sweeper(latch=latch)
    assert await sw.run_once() == 0
    assert latch.drains == 0
    assert sw._last_tick == NOW


async def test_run_once_propagates_exception_and_keeps_last_tick(monkeypatch):
    """行処理の例外は握らない(ReevalRunner側で握る)・last_tickは更新しない
    (観測窓の取りこぼし防止・Review Focus 4)。"""
    clock = FakeClock(NOW)

    async def sel_latches(engine, now, limit):
        return [(LATCH1, "proposed")]

    async def sel_intents(engine, now, limit):
        return []

    async def sel_completed(engine, now, limit):
        return []

    async def raising(latch_id, from_status, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(sweeper_mod, "_select_expiring_latches", sel_latches)
    monkeypatch.setattr(sweeper_mod, "_select_expiring_intents", sel_intents)
    monkeypatch.setattr(sweeper_mod, "_select_completion_targets", sel_completed)
    sw = _sweeper(clock=clock)
    monkeypatch.setattr(sw, "_expire_latch", raising)
    clock.advance(timedelta(seconds=60))  # run_onceのnow=NOW+60s
    with pytest.raises(RuntimeError):
        await sw.run_once()
    assert sw._last_tick == NOW  # 例外時は更新しない(旧値のまま)


async def test_drain_error_propagates_from_run_once(monkeypatch):
    """drain(第4処理)の例外も握らない(run_once全体の契約)。"""

    async def sel_latches(engine, now, limit):
        return []

    async def sel_intents(engine, now, limit):
        return []

    async def sel_completed(engine, now, limit):
        return []

    async def fake_close(engine, last_tick):
        return True

    monkeypatch.setattr(sweeper_mod, "_select_expiring_latches", sel_latches)
    monkeypatch.setattr(sweeper_mod, "_select_expiring_intents", sel_intents)
    monkeypatch.setattr(sweeper_mod, "_select_completion_targets", sel_completed)
    monkeypatch.setattr(sweeper_mod, "_has_recent_close", fake_close)

    class BrokenLatch:
        async def drain(self):
            raise RuntimeError("drain boom")

    sw = _sweeper(latch=BrokenLatch())
    with pytest.raises(RuntimeError):
        await sw.run_once()
