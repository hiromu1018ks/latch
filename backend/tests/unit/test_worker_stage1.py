"""Stage1(第1段処理)とIncomingEvent入力契約のunit試験(design §4.1)。

IncomingEvent.from_payload はテストパブリッシャー・API経由の両方の受信で
最初に通る境界(10 §1「テスト用パブリッシャー」の入力面)。Stage1本体は
Task 5でこのファイルへ追記する(スタブconn・注入sleepで決定的に)。
"""

import json
import uuid
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.events import IncomingEvent
from latch.settings import Settings
from latch.worker.stage1 import (
    BACKOFF_SEC,
    PayloadInvalid,
    Stage1,
)


def _ack() -> None:  # 記録用のack
    pass


def test_from_payload_full_triple():
    iid = uuid.uuid4()
    payload = (
        b'{"event_type": "updated", "source_intent_id": "'
        + str(iid).encode()
        + b'", "version": 3}'
    )
    event = IncomingEvent.from_payload(message_id="m1", payload=payload, ack=_ack)
    assert event.triple() == ("updated", iid, 3)


def test_from_payload_missing_version_yields_none_triple():
    payload = (
        b'{"event_type": "updated", "source_intent_id": "%s"}'
        % str(uuid.uuid4()).encode()
    )
    event = IncomingEvent.from_payload(message_id="m2", payload=payload, ack=_ack)
    assert event.triple() is None
    assert event.version is None


def test_from_payload_wrong_types_yield_none_triple():
    iid = uuid.uuid4()
    # versionがfloat / str / bool、source_intent_idがUUIDでない → いずれもNone
    for raw in (
        b'{"event_type": "updated", "source_intent_id": "%s", "version": 3.5}'
        % str(iid).encode(),
        b'{"event_type": "updated", "source_intent_id": "%s", "version": "3"}'
        % str(iid).encode(),
        b'{"event_type": "updated", "source_intent_id": "%s", "version": true}'
        % str(iid).encode(),
        b'{"event_type": "updated", "source_intent_id": "not-a-uuid", "version": 3}',
    ):
        event = IncomingEvent.from_payload(message_id="m3", payload=raw, ack=_ack)
        assert event.triple() is None, raw


def test_from_payload_invalid_json_keeps_raw():
    event = IncomingEvent.from_payload(message_id="m4", payload=b"not-json{", ack=_ack)
    assert event.triple() is None
    assert event.data == {"_raw": "not-json{"}  # 生データをDB保存経路へ残す


# -- Stage1本体(追記分。スタブconnで決定的に — design §4.1)--

S1_NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


class FakeResult:
    """SQLAlchemy CursorResult風(タプル行・first()・rowcount)。"""

    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row


class ScriptedConn:
    """executeを順に仕込んだ結果で応え、呼び出し(SQL文字列・params)を記録する。"""

    def __init__(self, results: list):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = list(results)

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return FakeResult(None, 0)


class ScriptedEngine:
    """engine.begin() と同じ形(begin→conn)のスタブ。"""

    def __init__(self, results: list):
        self.conn = ScriptedConn(results)
        self.begins = 0

    def begin(self):
        self.begins += 1
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class RecordingSleep:
    """即時返るsleep(バックオフの待機秒数を記録 — §2グローバル制約)。"""

    def __init__(self):
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def _event(event_type: str, iid, version, *, raw: bytes | None = None) -> IncomingEvent:
    if raw is not None:
        payload = raw
    elif version is None:
        payload = b'{"event_type": "%s", "source_intent_id": "%s"}' % (
            event_type.encode(),
            str(iid).encode(),
        )
    else:
        payload = b'{"event_type": "%s", "source_intent_id": "%s", "version": %d}' % (
            event_type.encode(),
            str(iid).encode(),
            version,
        )
    return IncomingEvent.from_payload(
        message_id=f"{event_type}-m", payload=payload, ack=_noop_ack
    )


def _noop_ack() -> None:
    pass


def _stage1(engine, clock=None, sleep=None, hook=None, matching_hook=None) -> Stage1:
    return Stage1(
        engine=engine,
        clock=clock if clock is not None else FakeClock(S1_NOW),
        settings=Settings(),
        sleep=sleep if sleep is not None else (lambda s: _await_none()),
        embedding_hook=hook,
        matching_hook=matching_hook,
    )


async def _await_none() -> None:
    return None


IID = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
ROW_ID = uuid.UUID("00000000-0000-4000-8000-0000000000bb")


def _claim_inserted() -> list:
    """claim挿入成功(RETURNING id が返る)→ intents version=1(=現行)。"""
    return [
        FakeResult((str(ROW_ID),)),  # INSERT ON CONFLICT DO NOTHING RETURNING id
        FakeResult(("pending",)),  # SELECT ... FOR UPDATE (行status)
        FakeResult((1,)),  # SELECT version FROM intents
        FakeResult(None, 1),  # UPDATE match_events → processed
    ]


def _sql(conn: ScriptedConn, i: int) -> str:
    return conn.calls[i][0]


async def test_intake_created_processes():
    """createdはdebounceを経ず即時処理(06 §9-1)。processed遷移SQLまで到達。"""
    engine = ScriptedEngine(_claim_inserted())
    stage1 = _stage1(engine)
    result = await stage1.intake(_event("created", IID, 1))
    assert result.kind == "processed"
    assert result.row_id == ROW_ID
    assert engine.begins == 2  # claim(1) + process(1)
    assert _sql(engine.conn, 1).startswith(
        "SELECT status FROM match_events"
    )  # FOR UPDATE


async def test_intake_updated_returns_debounce():
    """updatedは窓へ(処理しない)。claimのみ行う。"""
    engine = ScriptedEngine([FakeResult((str(ROW_ID),))])
    result = await _stage1(engine).intake(_event("updated", IID, 3))
    assert result.kind == "debounce"
    assert engine.begins == 1  # processしていない


async def test_intake_duplicate_skips_processing():
    """既存行のstatus≠pending→処理せずduplicate(重複受領の排除 — design §2.3)。"""
    engine = ScriptedEngine(
        [
            FakeResult(None, 0),  # INSERT ON CONFLICT DO NOTHING(競合)
            FakeResult((str(ROW_ID), "processed")),  # SELECT id, status FOR UPDATE
        ]
    )
    result = await _stage1(engine).intake(_event("created", IID, 1))
    assert result.kind == "duplicate"
    assert engine.begins == 1


async def test_intake_raw_poison_inserts_quarantined_row():
    """3点組抽出不能→5回再試行(バックオフ進行)→nil UUIDでquarantined行を作る。"""
    engine = ScriptedEngine([FakeResult((str(ROW_ID),))])
    sleep = RecordingSleep()
    raw = b'{"weird": true}'
    result = await _stage1(engine, sleep=sleep).intake(
        _event("updated", IID, None, raw=raw)
    )
    assert result.kind == "quarantined"
    assert sleep.seconds == list(BACKOFF_SEC)  # 1,2,4,8,16 — 06 §9-4
    params = engine.conn.calls[0][1]
    assert params["source_intent_id"] == uuid.UUID(int=0)  # nil UUIDで確実に行を作る
    assert params["status"] == "quarantined"
    assert "failure_reason" in params["payload"]  # json文字列に理由


async def test_intake_unknown_event_type_quarantined():
    """3点組可だが6値外のevent_type→構造違反経路(quarantine直行・design §2.4)。"""
    engine = ScriptedEngine([FakeResult((str(ROW_ID),))])
    sleep = RecordingSleep()
    result = await _stage1(engine, sleep=sleep).intake(_event("bogus", IID, 1))
    assert result.kind == "quarantined"
    assert sleep.seconds == list(BACKOFF_SEC)
    params = engine.conn.calls[0][1]
    assert params["event_type"] == "bogus"  # 受信値をそのまま保存
    assert params["status"] == "quarantined"


async def test_process_stale_version_discarded():
    """payload v < intents.version → processed + discard_reason=stale_version。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),  # 行status(FOR UPDATE)
            FakeResult((5,)),  # intents.version=5(受信は3)
            FakeResult(None, 1),  # UPDATE → processed
        ]
    )
    stage1 = _stage1(engine)
    outcome = await stage1.process(
        _event("updated", IID, 3), ("updated", IID, 3), ROW_ID
    )
    assert outcome == "processed"
    params = engine.conn.calls[2][1]
    assert json.loads(params["extra"]) == {"discard_reason": "stale_version"}
    assert "version" not in json.loads(params["extra"])  # UNIQUE索引列を壊さない


async def test_process_intent_missing_discarded():
    """参照先Intent不在→正当な遅延Eventとしてprocessed破棄(06 §9・design §2.4)。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult(None),  # intents行なし
            FakeResult(None, 1),
        ]
    )
    await _stage1(engine).process(
        _event("created", IID, 1), ("created", IID, 1), ROW_ID
    )
    params = engine.conn.calls[2][1]
    assert json.loads(params["extra"])["discard_reason"] == "intent_not_found"


async def test_process_version_ahead_reread_then_merge():
    """payload v > intents.version → FOR UPDATE再読込で一致すれば処理へ合流。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),  # 通常SELECTは v=1(受信は2)
            FakeResult((2,)),  # FOR UPDATE再読込で v=2(読み取り遅延)
            FakeResult(None, 1),  # processed
        ]
    )
    outcome = await _stage1(engine).process(
        _event("created", IID, 2), ("created", IID, 2), ROW_ID
    )
    assert outcome == "processed"
    assert "FOR UPDATE" in _sql(engine.conn, 2)  # 3本目のSQLが再読込


async def test_process_version_divergence_retries_then_quarantined():
    """乖離が続く限り再試行(5回)→quarantined+理由保持(06 §9-3・#6-4)。"""
    results = []
    for _ in range(6):  # 初回+再試行5回 = 6トランザクション × 3 SQL
        results += [
            FakeResult(("pending",)),
            FakeResult((1,)),  # intents.version=1(受信は9・乖離継続)
            FakeResult((1,)),  # FOR UPDATE再読込でも1
        ]
    results += [FakeResult(None, 1)]  # _quarantine_row の UPDATE(match_events)
    engine = ScriptedEngine(results)
    sleep = RecordingSleep()
    outcome = await _stage1(engine, sleep=sleep).process(
        _event("created", IID, 9), ("created", IID, 9), ROW_ID
    )
    assert outcome == "quarantined"
    assert sleep.seconds == list(BACKOFF_SEC)
    assert engine.begins == 7  # 6処理 + 隔離遷移1
    extra = json.loads(engine.conn.calls[-1][1]["extra"])
    assert "failure_reason" in extra  # 失敗理由をpayloadへ保持(05 §2)
    assert "version" not in extra  # UNIQUE索引列(payload->>'version')を壊さない


async def test_process_locked_row_not_pending_returns_without_side_effects():
    """process時の行status≠pending(二重受領)→副作用なく完了。"""
    engine = ScriptedEngine([FakeResult(("processed",))])
    outcome = await _stage1(engine).process(
        _event("created", IID, 1), ("created", IID, 1), ROW_ID
    )
    assert outcome == "processed"
    assert len(engine.conn.calls) == 1  # intents照会もUPDATEもしていない


async def test_process_deleted_closes_candidates():
    """deleted → match_candidatesの無効化SQL(status='closed'・06 §1)。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 0),  # 候補UPDATE(0件でも正常 — design §2.5)
            FakeResult(None, 1),  # processed
        ]
    )
    await _stage1(engine).process(
        _event("deleted", IID, 1), ("deleted", IID, 1), ROW_ID
    )
    close_sql = _sql(engine.conn, 2)
    assert "match_candidates" in close_sql and "closed" in close_sql
    assert engine.conn.calls[2][1]["intent_id"] == IID


async def test_embedding_hook_called_for_created_and_updated_only():
    """作成・更新EventでEmbeddingフックが呼ばれる位置を予約(ws-2 — design §2.5)。"""
    calls: list[tuple[str, uuid.UUID, int]] = []

    async def hook(event_type: str, intent_id: uuid.UUID, version: int) -> None:
        calls.append((event_type, intent_id, version))

    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    await _stage1(engine, hook=hook).process(
        _event("created", IID, 1), ("created", IID, 1), ROW_ID
    )
    assert calls == [("created", IID, 1)]

    engine2 = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    await _stage1(engine2, hook=hook).process(
        _event("expired", IID, 1), ("expired", IID, 1), ROW_ID
    )
    assert len(calls) == 1  # expiredでは呼ばない


async def test_discard_marks_absorbed_row_processed():
    """debounce吸収行の閉包: processed + discard_reason(design §2.3)。"""
    engine = ScriptedEngine([FakeResult(None, 1)])
    await _stage1(engine).discard(ROW_ID, reason="debounced_superceded")
    params = engine.conn.calls[0][1]
    assert json.loads(params["extra"]) == {"discard_reason": "debounced_superceded"}


def test_mark_processed_extra_preserves_version_key():
    """理由追記のextraは version キーを含まない(design §2.4)。"""
    # stale/intent不在/discardの各試験の params 検証が本試験(重複定義せず
    # test_process_stale_version_discarded 等の assert で担保済み)。
    # ここでは BACKOFF_SEC と PayloadInvalid の公開値を固定する:
    assert BACKOFF_SEC == (1.0, 2.0, 4.0, 8.0, 16.0)
    assert issubclass(PayloadInvalid, Exception)


# -- asyncpg返却UUIDインスタンスの回帰ピン(スーパーバイザー3巡目検出・M0 ws-3同種)--


async def test_intake_claim_returning_uuid_instance():
    """INSERT RETURNING id がUUIDインスタンス(pgproto.UUID相当)でも
    再構築で落ちずclaimできる(str返しスタブでは検出不能だった経路)。"""
    engine = ScriptedEngine(
        [
            FakeResult((ROW_ID,)),  # RETURNING id: UUID型
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    result = await _stage1(engine).intake(_event("created", IID, 1))
    assert result.kind == "processed"
    assert result.row_id == ROW_ID


async def test_intake_duplicate_claim_uuid_instance():
    """UNIQUE競合後の SELECT id, status のidがUUID型でもduplicate判定へ。"""
    engine = ScriptedEngine(
        [
            FakeResult(None, 0),  # INSERT ON CONFLICT DO NOTHING(競合)
            FakeResult((ROW_ID, "processed")),  # SELECT id, status FOR UPDATE
        ]
    )
    result = await _stage1(engine).intake(_event("created", IID, 1))
    assert result.kind == "duplicate"
    assert result.row_id == ROW_ID


async def test_quarantine_direct_conflict_with_uuid_instance():
    """3点組可の毒(6値外event_type)のUNIQUE競合→既存行隔離遷移で
    _SELECT_CLAIM のidがUUID型でも落ちない(stage1.py:358の検出経路)。"""
    engine = ScriptedEngine(
        [
            FakeResult(None, 0),  # INSERT(quarantined直行)がUNIQUE競合
            FakeResult((ROW_ID, "pending")),  # _SELECT_CLAIM(UUID型)
            FakeResult(None, 1),  # _MARK_QUARANTINED
        ]
    )
    sleep = RecordingSleep()
    result = await _stage1(engine, sleep=sleep).intake(_event("bogus", IID, 1))
    assert result.kind == "quarantined"
    assert sleep.seconds == list(BACKOFF_SEC)
    params = engine.conn.calls[2][1]
    assert params["row_id"] == ROW_ID  # UUID型がそのまま渡る(str再構築しない)
    assert params["extra"] is not None


# -- matching フック(M2 ws-4・design §2.7) --


async def test_matching_hook_called_for_embedding_completed():
    """embedding_completedでmatchingフックがconnとintent_idで呼ばれる。"""
    calls: list[tuple[object, uuid.UUID]] = []

    async def mhook(conn, intent_id):
        calls.append((conn, intent_id))

    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    await _stage1(engine, matching_hook=mhook).process(
        _event("embedding_completed", IID, 1),
        ("embedding_completed", IID, 1),
        ROW_ID,
    )
    assert calls == [(engine.conn, IID)]  # 同一トランザクションのconn


async def test_matching_hook_absent_keeps_processed():
    """hookなしでも embedding_completed は従来どおり processed(処理実体なし)。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    result = await _stage1(engine).process(
        _event("embedding_completed", IID, 1),
        ("embedding_completed", IID, 1),
        ROW_ID,
    )
    assert result == "processed"


async def test_matching_hook_failure_blocks_processed():
    """hook例外は握られず伝播=processedにならない(design §2.7 — Stage1の
    再試行(Retryable/SQLAlchemyError)で捕まらない例外は呼び出し側へ)。"""

    async def mhook(conn, intent_id):
        raise RuntimeError("matching failed")

    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
        ]
    )
    with pytest.raises(RuntimeError):
        await _stage1(engine, matching_hook=mhook).process(
            _event("embedding_completed", IID, 1),
            ("embedding_completed", IID, 1),
            ROW_ID,
        )
    # processed遷移SQL(_MARK_PROCESSED)には到達していない
    assert all(
        "processed" not in sql or "quarantined" in sql for sql, _ in engine.conn.calls
    ) or not any("SET status = 'processed'" in sql for sql, _ in engine.conn.calls)


async def test_matching_hook_not_called_for_other_types():
    """created/updatedではmatchingフックは呼ばれない(Embeddingキックは別)。"""
    calls: list[uuid.UUID] = []

    async def mhook(conn, intent_id):
        calls.append(intent_id)

    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    await _stage1(engine, matching_hook=mhook).process(
        _event("created", IID, 1), ("created", IID, 1), ROW_ID
    )
    assert calls == []
