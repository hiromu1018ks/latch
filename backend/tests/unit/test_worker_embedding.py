"""EmbeddingWorker.handleのunit試験(design §2.2・§4.1)。

スタブconn+記録Gateway+スタブbusで決定的に検証する: 2フェーズの順序・
ガード付きUPDATE・冪等(resume直接投入)・失敗経路の握り(D-15)・
SELECTがraw_textを含まないこと(確定値#10)。Worker配線はTask 8で追記。
"""

import json
import uuid
from datetime import UTC, datetime

from latch.core.clock import FakeClock
from latch.intents.events import EVENT_EMBEDDING_COMPLETED
from latch.llm.errors import LLMProviderError, LLMTimeoutError
from latch.llm.providers import EMBEDDING_DIMENSIONS
from latch.worker.embedding import EmbeddingWorker

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
