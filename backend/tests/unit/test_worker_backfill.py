"""BackfillRunnerのunit試験(design §2.5・§4.1)。

対象抽出SQL(embedding IS NULL AND status='active'のみ)・handleへの委譲・
項目単位の例外握りと継続・sleep-firstのstop応答ループを検証する。
ws-3がfixture直入れするembedding(embedding_model NULL)を誤って
再エンベディングしないための防線(design §1.4-2)。
"""

import asyncio
import uuid

from latch.worker.backfill import BackfillRunner

IID1 = uuid.UUID("00000000-0000-4000-8000-0000000000f1")
IID2 = uuid.UUID("00000000-0000-4000-8000-0000000000f2")


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class ScriptedConn:
    def __init__(self, result):
        self.result = result
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        return self.result


class ScriptedEngine:
    def __init__(self, result):
        self.conn = ScriptedConn(result)

    def begin(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class RecordingEmbedding:
    """handleの記録スタブ(指定intentで例外を注入可)。"""

    def __init__(self, fail_on=None):
        self.calls: list[tuple[uuid.UUID, int]] = []
        self.fail_on = fail_on or set()

    async def handle(self, intent_id, version):
        self.calls.append((intent_id, version))
        if intent_id in self.fail_on:
            raise RuntimeError("db down")


def _runner(engine, embedding, *, interval_sec=300.0, batch_limit=50):
    return BackfillRunner(
        engine=engine,
        embedding=embedding,
        interval_sec=interval_sec,
        batch_limit=batch_limit,
    )


async def test_run_once_selects_targets_sql_pin():
    """対象抽出SQL: embedding IS NULL AND status='active'・ORDER BY・LIMIT。"""
    engine = ScriptedEngine(FakeResult([(IID1, 3), (IID2, 5)]))
    embedding = RecordingEmbedding()
    done = await _runner(engine, embedding, batch_limit=50).run_once()
    sql, params = engine.conn.calls[0]
    assert "embedding IS NULL" in sql
    assert "status = 'active'" in sql
    assert "ORDER BY updated_at" in sql
    assert params == {"limit": 50}
    assert embedding.calls == [(IID1, 3), (IID2, 5)]
    assert done == 2


async def test_run_once_swallows_item_failure_and_continues():
    """項目単位の例外は握って次の対象へ(1件失敗しても残りを処理)。"""
    engine = ScriptedEngine(FakeResult([(IID1, 1), (IID2, 2)]))
    embedding = RecordingEmbedding(fail_on={IID1})
    done = await _runner(engine, embedding).run_once()  # 例外が外へ出ないこと
    assert embedding.calls == [(IID1, 1), (IID2, 2)]
    assert done == 1


async def test_run_sleep_first_immediate_stop_runs_nothing():
    """stop済みなら待機もrun_onceもしない(sleep-first・実時間待ちなし)。"""
    engine = ScriptedEngine(FakeResult([]))
    embedding = RecordingEmbedding()
    runner = _runner(engine, embedding, interval_sec=300.0)
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(runner.run(stop=stop), timeout=1.0)
    assert embedding.calls == []
    assert engine.conn.calls == []


async def test_run_waits_interval_before_first_cycle():
    """初回は周期待ちから(起動直後のバースト回避・design §2.5)。"""
    engine = ScriptedEngine(FakeResult([(IID1, 1)]))
    embedding = RecordingEmbedding()
    runner = _runner(engine, embedding, interval_sec=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop=stop))
    # 即座にはrun_onceしていないことを確認(短intervalでも待機が先)
    await asyncio.sleep(0)
    assert embedding.calls == []
    for _ in range(200):  # 周期(0.01秒)の到来を待つ(実時間・軽微)
        if embedding.calls:
            break
        await asyncio.sleep(0.01)
    assert embedding.calls == [(IID1, 1)]
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
