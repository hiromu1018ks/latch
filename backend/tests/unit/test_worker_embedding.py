"""EmbeddingWorker.handleのunit試験(design §2.2・§4.1)。

スタブconn+記録Gateway+スタブbusで決定的に検証する: 2フェーズの順序・
ガード付きUPDATE・冪等(resume直接投入)・失敗経路の握り(D-15)・
SELECTがraw_textを含まないこと(確定値#10)。Worker配線はTask 8で追記。
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.events import IncomingEvent
from latch.intents.events import EVENT_EMBEDDING_COMPLETED
from latch.llm.errors import LLMProviderError, LLMTimeoutError
from latch.llm.providers import EMBEDDING_DIMENSIONS
from latch.worker.embedding import EmbeddingWorker
from latch.worker.main import Worker
from latch.worker.stage1 import IntakeResult

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
IID = uuid.UUID("00000000-0000-4000-8000-0000000000ee")
# JST 2026-09-28 20:00-23:00(月曜)= 07 §3の例 → 期待テキストは全文ピン
T_START = datetime(2026, 9, 28, 11, 0, 0, tzinfo=UTC)
T_END = datetime(2026, 9, 28, 14, 0, 0, tzinfo=UTC)
EXPECTED_TEXT = "drinking / 平日夜20-23時 / 天文館 / 2-4人 / 静かなお店で"


class FakeResult:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row


class ScriptedConn:
    def __init__(self, results: list):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = list(results)

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return FakeResult(None, 0)


class ScriptedEngine:
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


class StubGateway:
    """embed_intentの記録スタブ(失敗・次元不正を注入可)。"""

    def __init__(self, *, vec=None, error=None):
        self.calls: list[dict] = []
        self._vec = vec if vec is not None else [0.5] * EMBEDDING_DIMENSIONS
        self._error = error

    async def embed_intent(self, *, text: str, intent_id: str) -> list[float]:
        self.calls.append({"text": text, "intent_id": intent_id})
        if self._error is not None:
            raise self._error
        return list(self._vec)


class StubBus:
    def __init__(self, fail=False):
        self.published: list[tuple] = []
        self.fail = fail

    async def publish_match_event(self, *, event_type, intent_id, version):
        if self.fail:
            raise RuntimeError("pubsub down")
        self.published.append((event_type, intent_id, version))


def _phase1_row(*, version=1, status="active", has_embedding=False):
    """フェーズ1SELECTの結果行(列順は実装のSELECTどおり)。"""
    structured = {
        "location_name": "天文館",
        "soft_constraints": [{"text": "静かなお店で", "downgraded_from_ng": False}],
    }
    return (
        version,
        status,
        has_embedding,
        "drinking",
        json.dumps(structured),  # asyncpgのjsonbはstrで返る場合がある(store.py規律)
        2,
        4,
        T_START,
        T_END,
    )


def _worker(engine, gateway=None, bus=None) -> EmbeddingWorker:
    return EmbeddingWorker(
        engine=engine,
        clock=FakeClock(NOW),
        gateway=gateway if gateway is not None else StubGateway(),
        bus=bus if bus is not None else StubBus(),
    )


def _sql(conn: ScriptedConn, i: int) -> str:
    return conn.calls[i][0]


async def test_handle_embeds_and_emits_in_order():
    """NULL→フェーズ2: SELECT→embed(正規化text)→ガード付きUPDATE→行INSERT→publish。"""
    engine = ScriptedEngine(
        [
            FakeResult(_phase1_row()),  # フェーズ1 SELECT
            FakeResult((str(IID),)),  # UPDATE RETURNING id(1行)
            FakeResult(None, 0),  # embedding_completed INSERT
        ]
    )
    gateway = StubGateway()
    bus = StubBus()
    await _worker(engine, gateway, bus).handle(IID, 1)
    assert gateway.calls == [{"text": EXPECTED_TEXT, "intent_id": str(IID)}]
    assert "raw_text" not in _sql(engine.conn, 0)  # SELECT列のピン(確定値#10)
    upd_sql, upd_params = engine.conn.calls[1]
    assert "embedding IS NULL" in upd_sql and "version = :version" in upd_sql
    assert "CAST(:vec AS vector)" in upd_sql  # 明示CAST(M1 ws-3事故の回帰予防)
    assert upd_params["model"] == "gemini-embedding-001"
    ins_sql, ins_params = engine.conn.calls[2]
    assert "ON CONFLICT DO NOTHING" in ins_sql
    assert ins_params["event_type"] == EVENT_EMBEDDING_COMPLETED
    assert json.loads(ins_params["payload"]) == {"version": 1}
    assert bus.published == [(EVENT_EMBEDDING_COMPLETED, IID, 1)]


async def test_handle_existing_embedding_directly_emits():
    """embedding既存(resume等)→Gateway不呼び出し+行INSERT+publish(確定値#6)。"""
    engine = ScriptedEngine(
        [
            FakeResult(_phase1_row(has_embedding=True)),
            FakeResult(None, 0),  # INSERTのみ(UPDATEなし)
        ]
    )
    gateway = StubGateway()
    bus = StubBus()
    await _worker(engine, gateway, bus).handle(IID, 1)
    assert gateway.calls == []  # 再エンベディングなし(テキスト不変)
    assert len(engine.conn.calls) == 2
    assert "ON CONFLICT DO NOTHING" in _sql(engine.conn, 1)
    assert bus.published == [(EVENT_EMBEDDING_COMPLETED, IID, 1)]


async def test_handle_missing_intent_is_noop():
    """行なし→no-op(参照先不在は正当な遅延Event — 06 §9と同じ扱い)。"""
    engine = ScriptedEngine([FakeResult(None)])
    gateway = StubGateway()
    bus = StubBus()
    await _worker(engine, gateway, bus).handle(IID, 1)
    assert len(engine.conn.calls) == 1  # SELECTのみ
    assert gateway.calls == [] and bus.published == []


async def test_handle_version_mismatch_is_noop():
    """DB version ≠ 引数version → no-op(新versionのEventが担当)。"""
    engine = ScriptedEngine([FakeResult(_phase1_row(version=2))])
    gateway = StubGateway()
    await _worker(engine, gateway, bus=StubBus()).handle(IID, 1)
    assert len(engine.conn.calls) == 1
    assert gateway.calls == []


async def test_handle_status_out_of_target_is_noop():
    """status ∉ {active, paused}(expired等)→ no-op(Layer 1〜2の対象外)。"""
    engine = ScriptedEngine([FakeResult(_phase1_row(status="cancelled"))])
    gateway = StubGateway()
    await _worker(engine, gateway, bus=StubBus()).handle(IID, 1)
    assert len(engine.conn.calls) == 1
    assert gateway.calls == []


async def test_handle_llm_errors_swallowed_no_write():
    """LLMTimeout/ProviderError → 例外なし・書き込みなし・イベント行なし(D-15)。"""
    for error in (LLMTimeoutError("t"), LLMProviderError("p")):
        engine = ScriptedEngine([FakeResult(_phase1_row())])
        gateway = StubGateway(error=error)
        bus = StubBus()
        await _worker(engine, gateway, bus).handle(IID, 1)  # 例外が出ないこと
        assert len(engine.conn.calls) == 1, error
        assert bus.published == [], error


async def test_handle_wrong_dimension_treated_as_failure():
    """768以外の応答 → 失敗扱い(書き込みなし・publishなし・design §2.2)。"""
    engine = ScriptedEngine([FakeResult(_phase1_row())])
    gateway = StubGateway(vec=[0.1] * 767)
    bus = StubBus()
    await _worker(engine, gateway, bus).handle(IID, 1)
    assert len(engine.conn.calls) == 1
    assert bus.published == []


async def test_handle_update_zero_rows_no_event():
    """ガード付きUPDATE 0行(並行handleが先に書いた)→イベント行もpublishもなし。"""
    engine = ScriptedEngine(
        [
            FakeResult(_phase1_row()),
            FakeResult(None, 0),  # UPDATE 0行
        ]
    )
    gateway = StubGateway()
    bus = StubBus()
    await _worker(engine, gateway, bus).handle(IID, 1)
    assert len(engine.conn.calls) == 2  # INSERTしていない
    assert bus.published == []


async def test_handle_publish_failure_swallowed():
    """publish失敗は握る(行はpending=フォールバックリレーが30秒後に回収)。"""
    engine = ScriptedEngine(
        [
            FakeResult(_phase1_row(has_embedding=True)),
            FakeResult(None, 0),
        ]
    )
    await _worker(engine, bus=StubBus(fail=True)).handle(IID, 1)  # 例外が出ないこと


def test_select_does_not_read_raw_text():
    """フェーズ1のSELECT定数そのものにraw_textが含まれない(確定値#10のSQLピン)。"""
    from latch.worker.embedding import _SELECT_INTENT_FOR_EMBEDDING

    sql = str(_SELECT_INTENT_FOR_EMBEDDING)
    assert "raw_text" not in sql
    assert "embedding IS NOT NULL" in sql
    assert "time_end" in sql  # 正規化テキスト導出に必要な列は選択している


# -- Worker配線(M2 ws-2・design §2.1-B・§4.1)--
# test_worker.py(ws-1資産)は触らない(§0競合回避4)。embedding注入で配線を検証。


class _FakeBus:
    def __init__(self):
        self.subscribers: list = []
        self.ensured = 0
        self.closed = 0

    async def ensure(self):
        self.ensured += 1

    async def publish_match_event(self, *, event_type, intent_id, version):
        raise AssertionError("worker配線試験ではpublishしない")

    async def subscribe(self, on_message):
        self.subscribers.append(on_message)
        return _FakeSubscription()

    async def close(self):
        self.closed += 1


class _FakeSubscription:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class _RecordingStage1:
    def __init__(self, kind: str):
        self.kind = kind
        self.processed: list[tuple] = []
        self.discarded: list[uuid.UUID] = []

    async def intake(self, event):
        return IntakeResult(kind=self.kind, triple=event.triple(), row_id=uuid.uuid4())

    async def process(self, event, triple, row_id):
        self.processed.append((triple, row_id))
        return "processed"

    async def discard(self, row_id, *, reason):
        self.discarded.append(row_id)


class _RecordingEmbedding:
    def __init__(self, *, error: Exception | None = None):
        self.calls: list[tuple[uuid.UUID, int]] = []
        self.error = error

    async def handle(self, intent_id, version):
        self.calls.append((intent_id, version))
        if self.error is not None:
            raise self.error


def _make_event(event_type: str, iid, version) -> IncomingEvent:
    acks: list[int] = []

    def _ack() -> None:
        acks.append(1)

    payload = b'{"event_type": "%s", "source_intent_id": "%s", "version": %d}' % (
        event_type.encode(),
        str(iid).encode(),
        version,
    )
    event = IncomingEvent.from_payload(
        message_id=f"{event_type}-{version}", payload=payload, ack=_ack
    )
    event.ack_count = acks  # type: ignore[attr-defined]
    return event


async def _started_worker(fake_clock, stage1, embedding):
    worker = Worker(
        clock=fake_clock, bus=_FakeBus(), stage1=stage1, embedding=embedding
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.01)
    return worker, task


async def _stop(worker, task) -> None:
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=1.0)


async def test_dispatch_processed_created_kicks_embedding(fake_clock):
    """processed × created → handle(intent_id, version)をackの前に呼ぶ。"""
    embedding = _RecordingEmbedding()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker(fake_clock, stage1, embedding)
    try:
        iid = uuid.uuid4()
        event = _make_event("created", iid, 1)
        await worker._dispatch(event)
        assert embedding.calls == [(iid, 1)]
        assert event.ack_count == [1]
    finally:
        await _stop(worker, task)


async def test_dispatch_duplicate_created_kicks_embedding(fake_clock):
    """duplicate × created → handleを呼ぶ(実行中クラッシュの即時回収 — §2.1-B)。"""
    embedding = _RecordingEmbedding()
    stage1 = _RecordingStage1("duplicate")
    worker, task = await _started_worker(fake_clock, stage1, embedding)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("created", iid, 1))
        assert embedding.calls == [(iid, 1)]
    finally:
        await _stop(worker, task)


async def test_dispatch_quarantined_no_kick(fake_clock):
    """quarantined(毒ペイロード等)ではキックしない(§4.1配線表)。"""
    embedding = _RecordingEmbedding()
    stage1 = _RecordingStage1("quarantined")
    worker, task = await _started_worker(fake_clock, stage1, embedding)
    try:
        await worker._dispatch(_make_event("created", uuid.uuid4(), 1))
        assert embedding.calls == []
    finally:
        await _stop(worker, task)


async def test_dispatch_non_create_update_types_no_kick(fake_clock):
    """deleted/expired/scheduled/embedding_completedではキックしない。"""
    for event_type in ("deleted", "expired", "scheduled", "embedding_completed"):
        embedding = _RecordingEmbedding()
        stage1 = _RecordingStage1("processed")
        worker, task = await _started_worker(fake_clock, stage1, embedding)
        try:
            await worker._dispatch(_make_event(event_type, uuid.uuid4(), 1))
            assert embedding.calls == [], event_type
        finally:
            await _stop(worker, task)


async def test_on_release_kicks_latest_only(fake_clock):
    """debounce窓解放: latestの処理後にhandle 1回(absorbedでは呼ばない)。"""
    embedding = _RecordingEmbedding()
    stage1 = _RecordingStage1("debounce")
    worker, task = await _started_worker(fake_clock, stage1, embedding)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("updated", iid, 2))
        await worker._dispatch(_make_event("updated", iid, 3))
        fake_clock.advance(timedelta(seconds=10))
        await asyncio.sleep(0.2)  # debouncer tick(0.05秒)が解放を実行
        assert embedding.calls == [(iid, 3)]  # latestのみ
    finally:
        await _stop(worker, task)


async def test_kick_failure_skips_ack(fake_clock):
    """handleの例外(DB失敗)→ackしない=ack_deadline後に再配信が回収(§2.1-B)。"""
    embedding = _RecordingEmbedding(error=RuntimeError("db down"))
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker(fake_clock, stage1, embedding)
    try:
        event = _make_event("created", uuid.uuid4(), 1)
        await worker._dispatch(event)  # 既存exceptが握るため例外は外へ出ない
        assert event.ack_count == []  # ackされない
    finally:
        await _stop(worker, task)
