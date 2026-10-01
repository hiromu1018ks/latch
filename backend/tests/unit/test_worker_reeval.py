"""ReevalRunnerのunit試験(design §2.8・§4.1)。

catch-up抽出・Bucket処理(同一Bucket再処理なし・境界切替)・ガードdenyで
pipeline不呼出・例外握り継続・stop追従を検証する。SQL抽出関数は
monkeypatchで差し替え(runner流儀)、SQL文字列ピンはtext()定数へ直接。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.worker import reeval as reeval_mod
from latch.worker.reeval import ReevalRunner

IID1 = uuid.UUID("00000000-0000-4000-8000-0000000000f1")
IID2 = uuid.UUID("00000000-0000-4000-8000-0000000000f2")
IID3 = uuid.UUID("00000000-0000-4000-8000-0000000000f3")

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)  # JST 2026-10-01 21:00


class RecordingPipeline:
    """pipeline本体のスタブ(呼び出し記録・例外差し替え)。"""

    def __init__(self, error: Exception | None = None):
        self.calls: list[uuid.UUID] = []
        self._error = error

    async def __call__(self, intent_id):
        self.calls.append(intent_id)
        if self._error is not None:
            raise self._error


class FakeGuard:
    """ReevalGuardスタブ(allowの呼び出し記録)。"""

    def __init__(self, allowed: bool = True):
        self.allowed = allowed
        self.calls: list[uuid.UUID] = []

    async def allow(self, intent_id):
        self.calls.append(intent_id)
        return self.allowed


def _runner(*, clock=None, guard="default", pipeline=None, interval_sec=60.0):
    if guard == "default":
        guard = FakeGuard()
    return ReevalRunner(
        engine=object(),  # 抽出関数はmonkeypatchで差し替え
        clock=clock or FakeClock(NOW),
        guard=guard,
        pipeline=pipeline or RecordingPipeline(),
        interval_sec=interval_sec,
        batch_limit=50,
    )


def _patch_select(monkeypatch, *, catchup=(), bucket=()):
    """抽出2関数を記録スタブへ差し替え。"""
    log = {"catchup": [], "bucket": []}

    async def fake_catchup(engine, now, batch_limit):
        log["catchup"].append((now, batch_limit))
        return list(catchup)

    async def fake_bucket(engine, b_start, b_end):
        log["bucket"].append((b_start, b_end))
        return list(bucket)

    monkeypatch.setattr(reeval_mod, "_select_catchup_targets", fake_catchup)
    monkeypatch.setattr(reeval_mod, "_select_bucket_targets", fake_bucket)
    return log


# --- 1. catch-up抽出SQLピン(引用#19) ---


def test_catchup_sql_pins_window_and_limit():
    sql = str(reeval_mod._SELECT_CATCHUP_TARGETS)
    assert "status = 'active'" in sql
    assert "embedding IS NOT NULL" in sql
    assert "expires_at IS NOT NULL" in sql
    assert "expires_at > CAST(:now AS timestamptz)" in sql
    assert "expires_at <= CAST(:now AS timestamptz) + interval '2 hours'" in sql
    assert "LIMIT :batch_limit" in sql
    # 「直近評価から30分」条件はSQLに入れない(reevalガードが入口で担う)
    assert "30" not in sql


# --- 2. Bucket抽出SQLピン ---


def test_bucket_sql_pins_window():
    sql = str(reeval_mod._SELECT_BUCKET_TARGETS)
    assert "status = 'active'" in sql
    assert "embedding IS NOT NULL" in sql
    assert "time_start >= CAST(:bucket_start AS timestamptz)" in sql
    assert "time_start < CAST(:bucket_end AS timestamptz)" in sql


# --- 3. run_onceがcatch-up+Bucket対象をpipelineへ(seen重複排除) ---


async def test_run_once_pipelines_catchup_and_bucket_deduped(monkeypatch):
    """catch-up+Bucketの両方へ同一IDがある場合は1回のみpipeline(seen)。"""
    _patch_select(monkeypatch, catchup=[IID1, IID2], bucket=[IID2, IID3])
    pipeline = RecordingPipeline()
    done = await _runner(pipeline=pipeline).run_once()
    assert sorted(pipeline.calls) == [IID1, IID2, IID3]  # 重複なし
    assert done == 3


# --- 4. 同一Bucketの再処理なし ---


async def test_same_bucket_not_reprocessed(monkeypatch):
    """クロック不進行の2回目 → Bucket抽出不呼出(_last_bucket保持)。"""
    log = _patch_select(monkeypatch, catchup=[IID1], bucket=[IID2])
    pipeline = RecordingPipeline()
    runner = _runner(pipeline=pipeline)
    await runner.run_once()
    await runner.run_once()
    assert len(log["bucket"]) == 1  # 2回目はBucket抽出なし
    assert pipeline.calls == [IID1, IID2, IID1]  # 2回目はcatch-upのみ


# --- 5. Bucket境界の切り替わり ---


async def test_bucket_boundary_advances(monkeypatch):
    """31分進行 → 新しいbucket_startでBucket抽出が再度呼ばれる。"""
    clock = FakeClock(NOW)
    log = _patch_select(monkeypatch, catchup=[], bucket=[IID1])
    runner = _runner(clock=clock)
    await runner.run_once()
    clock.advance(timedelta(minutes=31))
    await runner.run_once()
    assert len(log["bucket"]) == 2
    assert log["bucket"][1][0] > log["bucket"][0][0]  # 新しいBucket


# --- 6. 初回は現在Bucketを処理 ---


async def test_first_cycle_processes_current_bucket(monkeypatch):
    """_last_bucket=None → 1回目からBucket抽出が呼ばれる(再起動直後)。"""
    log = _patch_select(monkeypatch, catchup=[], bucket=[IID1])
    pipeline = RecordingPipeline()
    await _runner(pipeline=pipeline).run_once()
    assert len(log["bucket"]) == 1
    assert pipeline.calls == [IID1]


# --- 7. ガードdeny ---


async def test_guard_deny_skips_pipeline(monkeypatch):
    """ガードFalse → 当該IDのpipeline不呼出・他IDは呼ばれる(30分以内)。"""
    guard = FakeGuard(allowed=False)
    _patch_select(monkeypatch, catchup=[IID1, IID2], bucket=())
    pipeline = RecordingPipeline()
    done = await _runner(guard=guard, pipeline=pipeline).run_once()
    assert pipeline.calls == []
    assert guard.calls == [IID1, IID2]
    assert done == 0


# --- 8. guard=None ---


async def test_guard_none_pipelines_all(monkeypatch):
    """guard=None → ガード判定をスキップして全IDsへpipeline。"""
    _patch_select(monkeypatch, catchup=[IID1], bucket=())
    pipeline = RecordingPipeline()
    done = await _runner(guard=None, pipeline=pipeline).run_once()
    assert pipeline.calls == [IID1]
    assert done == 1


# --- 9. pipeline例外は握らず伝播・run()は握って継続 ---


async def test_pipeline_error_propagates_from_run_once(monkeypatch):
    """pipeline例外 → run_onceはそのまま送出(design §2.8-3・握らない)。"""
    import pytest

    _patch_select(monkeypatch, catchup=[IID1], bucket=())
    pipeline = RecordingPipeline(error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await _runner(pipeline=pipeline).run_once()


async def test_run_swallows_run_once_error_and_continues(monkeypatch):
    """run()のループはrun_onceの例外を握って次周期で回収(stopで終了)。"""
    _patch_select(monkeypatch, catchup=[IID1], bucket=())
    pipeline = RecordingPipeline(error=RuntimeError("boom"))
    runner = _runner(pipeline=pipeline, interval_sec=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop=stop))
    await asyncio.sleep(0.1)  # 数周期回す(例外を握り続ける)
    assert not task.done()  # ループ継続
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)  # 例外なく終了


# --- 10. run_onceの戻り値 ---


async def test_run_once_returns_done_count(monkeypatch):
    _patch_select(monkeypatch, catchup=[IID1, IID2], bucket=[])
    pipeline = RecordingPipeline()
    assert await _runner(pipeline=pipeline).run_once() == 2


# --- 11. stop追従(sleep-first) ---


async def test_run_is_sleep_first_and_stops_on_event(monkeypatch):
    """runはsleep-first(即pipelineしない)・event.set()後に終了。"""
    _patch_select(monkeypatch, catchup=[IID1], bucket=())
    pipeline = RecordingPipeline()
    runner = _runner(pipeline=pipeline, interval_sec=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop=stop))
    await asyncio.sleep(0)  # 起動直後はpipelineしていない
    assert pipeline.calls == []
    for _ in range(200):  # 周期(0.01秒)の到来を待つ
        if pipeline.calls:
            break
        await asyncio.sleep(0.01)
    assert pipeline.calls == [IID1]
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


# --- 12. compile検査 ---


def test_sql_bind_params_compile():
    """postgresql dialectでcompile → 未変換の ':name' が残らない。"""
    from sqlalchemy.dialects import postgresql

    targets = {
        reeval_mod._SELECT_CATCHUP_TARGETS: {"now", "batch_limit"},
        reeval_mod._SELECT_BUCKET_TARGETS: {"bucket_start", "bucket_end"},
    }
    for stmt, keys in targets.items():
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        for key in keys:
            assert f":{key}" not in compiled, (key, compiled)


# --- 13. ExpirySweeper注入(M3 ws-2・design §2.1案A・§4.1統合回帰のunit側) ---


class RecordingSweeper:
    """ExpirySweeperスタブ(run_onceの呼び出し記録)。"""

    def __init__(self, error: Exception | None = None):
        self.calls = 0
        self._error = error

    async def run_once(self) -> int:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return 0


async def test_sweeper_runs_first_when_injected(monkeypatch):
    """注入あり→sweeper.run_onceがcatch-up/Bucket投入の前に実行される
    (design §2.1「先頭実行により期限切れ確定がパイプラインの重さに
    後ろ倒しにならない」)。"""
    _patch_select(monkeypatch, catchup=[IID1], bucket=())
    order: list[str] = []

    class OrderedSweeper(RecordingSweeper):
        async def run_once(self) -> int:
            order.append("sweeper")
            return await super().run_once()

    sweeper = OrderedSweeper()
    pipeline = RecordingPipeline()

    async def ordered_pipeline(intent_id):
        order.append("pipeline")
        await pipeline(intent_id)

    runner = ReevalRunner(
        engine=object(),
        clock=FakeClock(NOW),
        guard=None,
        pipeline=ordered_pipeline,
        interval_sec=60.0,
        batch_limit=50,
        sweeper=sweeper,
    )
    done = await runner.run_once()
    assert done == 1
    assert order == ["sweeper", "pipeline"]  # sweeper先行
    assert sweeper.calls == 1


async def test_sweeper_none_keeps_current_behavior(monkeypatch):
    """注入なし(既定None)→従動作。既存構成(test_k_limits_e2e等)は無傷。"""
    _patch_select(monkeypatch, catchup=[IID1], bucket=())
    pipeline = RecordingPipeline()
    runner = _runner(pipeline=pipeline)  # 既存ヘルパ(sweeper渡さず)
    assert await runner.run_once() == 1
    assert pipeline.calls == [IID1]


async def test_sweeper_error_propagates_from_run_once(monkeypatch):
    """sweeper.run_onceの例外は握らず伝播(run()が握って次周期で回収)。"""
    import pytest

    _patch_select(monkeypatch, catchup=[IID1], bucket=())
    runner = ReevalRunner(
        engine=object(),
        clock=FakeClock(NOW),
        guard=None,
        pipeline=RecordingPipeline(),
        interval_sec=60.0,
        batch_limit=50,
        sweeper=RecordingSweeper(error=RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        await runner.run_once()
