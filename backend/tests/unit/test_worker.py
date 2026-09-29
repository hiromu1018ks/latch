"""Worker起動経路(FakeClock注入・即時shutdown。design §4.4)+配線(M2 ws-1)。"""

import asyncio
import uuid
from datetime import timedelta

from latch.core.clock import SystemClock
from latch.events import IncomingEvent
from latch.worker.main import Worker
from latch.worker.stage1 import IntakeResult


async def test_worker_accepts_injected_clock_and_stops_immediately(fake_clock):
    worker = Worker(clock=fake_clock, bus=_FakeBus())
    assert worker.clock is fake_clock  # 注入したClockをそのまま使う
    worker.request_shutdown()  # shutdownイベントを先にセット
    await asyncio.wait_for(worker.run(), timeout=1.0)  # 即座にgraceful終了


async def test_worker_defaults_to_system_clock():
    worker = Worker(bus=_FakeBus())
    assert isinstance(worker.clock, SystemClock)
    worker.request_shutdown()
    await asyncio.wait_for(worker.run(), timeout=1.0)


async def test_worker_run_returns_after_shutdown_request():
    worker = Worker(bus=_FakeBus())
    task = asyncio.create_task(worker.run())
    # run() が待機に入ることを許す(asyncio.sleepは待機であり時刻参照ではない)
    await asyncio.sleep(0)
    assert not task.done()
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=1.0)


# -- 配線(M2 ws-1)。FakeBus+記録Stage1+実debouncer(run()内構成)で検証 --


class _FakeBus:
    """EventBusスタブ(subscribeを記録。ensure/closeも数える)。"""

    def __init__(self):
        self.subscribers: list = []
        self.ensured = 0
        self.closed = 0

    async def ensure(self):
        self.ensured += 1

    async def publish_match_event(self, *, event_type, intent_id, version):
        raise AssertionError("worker配線ではpublishしない")

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


class _RecordingStage1:
    """Stage1スタブ(intake/process/discardの呼び出しを記録)。"""

    def __init__(self, kind: str):
        self.kind = kind
        self.intakes: list[IncomingEvent] = []
        self.processed: list[tuple] = []
        self.discarded: list[uuid.UUID] = []

    async def intake(self, event):
        self.intakes.append(event)
        return IntakeResult(kind=self.kind, triple=event.triple(), row_id=uuid.uuid4())

    async def process(self, event, triple, row_id):
        self.processed.append((triple, row_id))
        return "processed"

    async def discard(self, row_id, *, reason):
        self.discarded.append(row_id)


async def _started_worker(fake_clock, stage1):
    """run()を起動し(bus=FakeBus・stage1=スタブ)、subscribe・debouncer構築を待つ。"""
    from latch.worker.main import Worker

    worker = Worker(clock=fake_clock, bus=_FakeBus(), stage1=stage1)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.01)
    return worker, task


async def _stop(worker, task) -> None:
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=1.0)


async def test_dispatch_immediate_event_acks_after_intake(fake_clock):
    """created等の即時種別: intake完了後にack(順序含む — Review Focus 5)。"""
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker(fake_clock, stage1)
    try:
        event = _make_event("created", uuid.uuid4(), 1)
        await worker._dispatch(event)
        assert stage1.intakes == [event]
        assert event.ack_count == [1]  # intake完了後にack
    finally:
        await _stop(worker, task)


async def test_dispatch_duplicate_and_quarantined_ack_only(fake_clock):
    """duplicate/quarantinedはackのみ(処理副作用なし)。"""
    for kind in ("duplicate", "quarantined"):
        stage1 = _RecordingStage1(kind)
        worker, task = await _started_worker(fake_clock, stage1)
        try:
            event = _make_event("created", uuid.uuid4(), 1)
            await worker._dispatch(event)
            assert event.ack_count == [1], kind
            assert stage1.processed == [], kind
        finally:
            await _stop(worker, task)


async def test_dispatch_updated_debounce_release_acks_all(fake_clock):
    """updated: 受信時ack保留→窓解放でlatest処理+absorbed閉包→両方ack。"""
    stage1 = _RecordingStage1("debounce")
    worker, task = await _started_worker(fake_clock, stage1)
    try:
        iid = uuid.uuid4()
        e2 = _make_event("updated", iid, 2)
        e3 = _make_event("updated", iid, 3)
        await worker._dispatch(e2)
        await worker._dispatch(e3)
        assert e2.ack_count == [] and e3.ack_count == []  # 解放前はackしない
        fake_clock.advance(timedelta(seconds=10))
        await asyncio.sleep(0.2)  # debouncer tick(0.05秒)が解放を実行
        assert stage1.processed, "latest未処理"
        assert stage1.processed[0][0][2] == 3  # latest=v3(design §2.3)
        assert len(stage1.discarded) == 1  # v2は統合理由でprocessed閉包
        assert e2.ack_count == [1] and e3.ack_count == [1]
    finally:
        await _stop(worker, task)


async def test_run_starts_subscription_and_stops_gracefully(fake_clock):
    """run()がensure+subscribeを開始し、shutdownでsubscription.stopへ至る。"""
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker(fake_clock, stage1)
    assert worker.bus.ensured >= 1
    assert worker.bus.subscribers  # subscribe済み
    await _stop(worker, task)
    assert worker._subscription.stopped == 1


# -- _run_matching DI配線(M2 ws-4・design §2.6・§2.7)。ReevalGuardスタブで検証 --

IID = uuid.UUID("00000000-0000-4000-8000-0000000000aa")


class _FakeReeval:
    """ReevalGuardスタブ(allowの呼び出しを記録)。"""

    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[uuid.UUID] = []

    async def allow(self, intent_id):
        self.calls.append(intent_id)
        return self.allowed


async def test_run_matching_runs_retrieval_when_allowed(fake_clock, monkeypatch):
    """ガード許可時: run_candidate_retrieval を conn・clock・intent_id で呼ぶ。"""
    called: list[tuple[object, object, uuid.UUID]] = []

    async def fake_retrieval(conn, clock, intent_id):
        called.append((conn, clock, intent_id))

    monkeypatch.setattr("latch.worker.main.run_candidate_retrieval", fake_retrieval)
    guard = _FakeReeval(True)
    worker = Worker(clock=fake_clock, bus=_FakeBus(), reeval=guard)
    await worker._run_matching(None, IID)
    assert called == [(None, fake_clock, IID)]
    assert guard.calls == [IID]


async def test_run_matching_suppressed_when_reeval_denies(fake_clock, monkeypatch):
    """ガード拒否時: run_candidate_retrieval は呼ばれない(design §2.6)。"""

    async def fake_retrieval(conn, clock, intent_id):
        raise AssertionError("ガード拒否では呼ばれない")

    monkeypatch.setattr("latch.worker.main.run_candidate_retrieval", fake_retrieval)
    guard = _FakeReeval(False)
    worker = Worker(clock=fake_clock, bus=_FakeBus(), reeval=guard)
    await worker._run_matching(None, IID)  # 例外なくreturn(スキップ)
    assert guard.calls == [IID]


# -- _kick_jev 配線(M2 ws-5・design §2.1案B)。embeddingと対称の検証 --


class _RecordingJev:
    """JevWorkerスタブ(handleの呼び出しを記録)。group_ctxはws-7で追加。"""

    def __init__(self):
        self.calls: list[uuid.UUID] = []

    async def handle(self, intent_id, group_ctx=None):
        self.calls.append(intent_id)


async def _started_worker_with_jev(fake_clock, stage1, jev, embedding=None):
    from latch.worker.main import Worker

    worker = Worker(
        clock=fake_clock, bus=_FakeBus(), stage1=stage1, embedding=embedding, jev=jev
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.01)
    return worker, task


async def test_dispatch_processed_embedding_completed_kicks_jev(fake_clock):
    """processed × embedding_completed → ackの前にhandleを呼ぶ(§2.1案B)。"""
    jev = _RecordingJev()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_jev(fake_clock, stage1, jev)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert jev.calls == [iid]
    finally:
        await _stop(worker, task)


async def test_dispatch_duplicate_embedding_completed_kicks_jev(fake_clock):
    """duplicate × embedding_completed → handleを呼ぶ(再配信回収 — §2.9)。"""
    jev = _RecordingJev()
    stage1 = _RecordingStage1("duplicate")
    worker, task = await _started_worker_with_jev(fake_clock, stage1, jev)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert jev.calls == [iid]
    finally:
        await _stop(worker, task)


async def test_dispatch_created_does_not_kick_jev(fake_clock):
    """created/updatedでは呼ばない(06 §1: embedding_completed起点のみ)。"""
    jev = _RecordingJev()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_jev(fake_clock, stage1, jev)
    try:
        await worker._dispatch(_make_event("created", uuid.uuid4(), 1))
        assert jev.calls == []
    finally:
        await _stop(worker, task)


async def test_jev_not_injected_is_noop(fake_clock):
    """Jev未注入(ws-1資産の試験)は何もしない。"""
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker(fake_clock, stage1)  # jev未注入
    try:
        await worker._dispatch(_make_event("embedding_completed", uuid.uuid4(), 1))
    finally:
        await _stop(worker, task)


# -- 配線(M2 ws-6)。_kick_jev直列LatchEngine・直接投入(design §2.1案A・§2.8-3) --


class _RecordingLatch:
    """LatchEngineスタブ(handleの呼び出しを記録)。"""

    def __init__(self):
        self.calls: list[uuid.UUID] = []

    async def handle(self, intent_id):
        self.calls.append(intent_id)


async def _started_worker_with_latch(fake_clock, stage1, jev, latch):
    from latch.worker.main import Worker

    worker = Worker(
        clock=fake_clock, bus=_FakeBus(), stage1=stage1, jev=jev, latch=latch
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.01)
    return worker, task


async def test_kick_jev_runs_latch_after_jev(fake_clock):
    """embedding_completed → JevWorker完了後にLatchEngineを直列実行(§2.1案A)。"""
    jev = _RecordingJev()
    latch = _RecordingLatch()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_latch(fake_clock, stage1, jev, latch)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert jev.calls == [iid]
        assert latch.calls == [iid]  # JevWorkerの後にLayer 5
    finally:
        await _stop(worker, task)


async def test_kick_jev_latch_not_injected_is_noop(fake_clock):
    """Latch未注入(ws-1/ws-5資産の試験)は何もしない。"""
    jev = _RecordingJev()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_latch(fake_clock, stage1, jev, None)
    try:
        await worker._dispatch(_make_event("embedding_completed", uuid.uuid4(), 1))
        assert jev.calls  # jevのみ
    finally:
        await _stop(worker, task)


async def test_kick_jev_jev_not_injected_latch_runs(fake_clock):
    """Jev未注入でもlatch注入ならlatchを実行(構成上の独立性)。"""
    latch = _RecordingLatch()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_latch(fake_clock, stage1, None, latch)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert latch.calls == [iid]
    finally:
        await _stop(worker, task)


async def test_created_does_not_kick_latch(fake_clock):
    """created/updatedではLayer 5も起動しない(06 §1)。"""
    latch = _RecordingLatch()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_latch(fake_clock, stage1, None, latch)
    try:
        await worker._dispatch(_make_event("created", uuid.uuid4(), 1))
        assert latch.calls == []
    finally:
        await _stop(worker, task)


class _FakePipelineEngine:
    """直接投入用の最小engine(begin()のみ・retrievalはmonkeypatch)。"""

    def begin(self):
        return _FakePipelineTx()


class _FakePipelineTx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


async def test_run_direct_pipeline_order(fake_clock, monkeypatch):
    """直接投入: L1〜3tx→コミット後Jev→Latchの順・同一intent_id(design §2.8-3)。"""
    from latch.worker import main as main_mod

    order: list[str] = []

    async def fake_retrieval(conn, clock, intent_id):
        order.append("l123")

    class _Ordered:
        def __init__(self, tag: str):
            self._tag = tag
            self.calls: list[uuid.UUID] = []

        async def handle(self, intent_id, group_ctx=None):
            self.calls.append(intent_id)
            order.append(self._tag)

    monkeypatch.setattr(main_mod, "run_candidate_retrieval", fake_retrieval)
    jev = _Ordered("jev")
    latch = _Ordered("latch")
    worker = Worker(
        clock=fake_clock,
        bus=_FakeBus(),
        stage1=_RecordingStage1("processed"),
        jev=jev,
        latch=latch,
    )
    iid = uuid.uuid4()
    await worker._run_direct_pipeline(_FakePipelineEngine(), iid)
    assert order == ["l123", "jev", "latch"]
    assert jev.calls == [iid] and latch.calls == [iid]


# -- 配線(M2 ws-7)。_run_post_retrieval共通チェーン(design §2.1案A) --


class _RecordingGroup:
    """GroupEngineスタブ(handle/finalizeの呼び出しを記録・handleは空ctx)。"""

    def __init__(self):
        self.handles: list[uuid.UUID] = []
        self.finalizes: list[uuid.UUID] = []

    async def handle(self, intent_id):
        self.handles.append(intent_id)
        return None

    async def finalize(self, intent_id):
        self.finalizes.append(intent_id)


async def test_kick_jev_chain_group_jev_latch_finalize(fake_clock):
    """embedding_completed → group.handle→jev→latch→group.finalizeの順(§2.1案A)。"""
    order: list[str] = []

    class _Chain:
        def __init__(self, tag: str):
            self._tag = tag
            self.calls: list[uuid.UUID] = []

        async def handle(self, intent_id, group_ctx=None):
            self.calls.append(intent_id)
            order.append(self._tag)
            return None

        async def finalize(self, intent_id):
            self.calls.append(intent_id)
            order.append(f"{self._tag}!")
            return None

    group = _Chain("group")
    jev = _Chain("jev")
    latch = _Chain("latch")
    stage1 = _RecordingStage1("processed")
    worker = Worker(
        clock=fake_clock,
        bus=_FakeBus(),
        stage1=stage1,
        jev=jev,
        latch=latch,
        group=group,
    )
    task = asyncio.create_task(worker.run())
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert order == ["group", "jev", "latch", "group!"]
        assert group.calls == [iid, iid]  # handleとfinalize
    finally:
        await _stop(worker, task)


async def test_kick_jev_group_not_injected_noop(fake_clock):
    """group未注入(ws-6資産の試験)はjev→latchのみ・例外なし。"""
    jev = _RecordingJev()
    latch = _RecordingLatch()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_latch(fake_clock, stage1, jev, latch)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert jev.calls == [iid] and latch.calls == [iid]
    finally:
        await _stop(worker, task)


async def test_kick_jev_passes_group_ctx_to_jev(fake_clock):
    """group.handleの戻り値がjev.handleの第2引数へ渡る。"""
    marker = object()

    class _CtxGroup:
        async def handle(self, intent_id):
            return marker

        async def finalize(self, intent_id):
            return None

    seen: list = []

    class _Jev:
        async def handle(self, intent_id, group_ctx=None):
            seen.append(group_ctx)

    stage1 = _RecordingStage1("processed")
    worker = Worker(
        clock=fake_clock,
        bus=_FakeBus(),
        stage1=stage1,
        jev=_Jev(),
        latch=None,
        group=_CtxGroup(),
    )
    task = asyncio.create_task(worker.run())
    try:
        await worker._dispatch(_make_event("embedding_completed", uuid.uuid4(), 1))
        assert seen == [marker]
    finally:
        await _stop(worker, task)


async def test_run_direct_pipeline_uses_post_retrieval(fake_clock, monkeypatch):
    """直接投入: L1〜3tx→共通チェーン(group→jev→latch→finalize)。"""
    from latch.worker import main as main_mod

    order: list[str] = []

    async def fake_retrieval(conn, clock, intent_id):
        order.append("l123")

    monkeypatch.setattr(main_mod, "run_candidate_retrieval", fake_retrieval)

    class _C:
        def __init__(self, tag):
            self._tag = tag

        async def handle(self, intent_id, group_ctx=None):
            order.append(self._tag)
            return None

        async def finalize(self, intent_id):
            order.append(f"{self._tag}!")

    worker = Worker(
        clock=fake_clock,
        bus=_FakeBus(),
        stage1=_RecordingStage1("processed"),
        jev=_C("jev"),
        latch=_C("latch"),
        group=_C("group"),
    )
    await worker._run_direct_pipeline(_FakePipelineEngine(), uuid.uuid4())
    assert order == ["l123", "group", "jev", "latch", "group!"]
