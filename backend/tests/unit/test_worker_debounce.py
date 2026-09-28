"""TrailingDebouncerの窓管理(design §4.1)。

FakeClock+poll()/短tickのrun()で、窓統合・即時判定・再受信非延長・
窓吸収行の閉包をClock操作のみで再現する(実時間待ちなし — 10 §1)。
"""

import asyncio
import uuid
from datetime import timedelta

from latch.core.clock import FakeClock
from latch.events import IncomingEvent
from latch.worker.debounce import DebounceEntry, DebounceGroup, TrailingDebouncer


def _entry(event_type: str, iid: uuid.UUID, version: int) -> DebounceEntry:
    def _ack() -> None:
        pass

    payload = b'{"event_type": "%s", "source_intent_id": "%s", "version": %d}' % (
        event_type.encode(),
        str(iid).encode(),
        version,
    )
    event = IncomingEvent.from_payload(
        message_id=f"{event_type}-{version}", payload=payload, ack=_ack
    )
    triple = event.triple()
    assert triple is not None
    return DebounceEntry(event=event, triple=triple, row_id=uuid.uuid4())


def _debouncer(clock: FakeClock, released: list[DebounceGroup]) -> TrailingDebouncer:
    async def _on_release(group: DebounceGroup) -> None:
        released.append(group)

    return TrailingDebouncer(
        clock=clock, window_sec=10.0, tick_sec=0.001, on_release=_on_release
    )


def test_window_release_merges_updates(fake_clock):
    """窓内の v2→v3→v4 は v4 のみ処理対象(latest)・v2/v3 は吸収(absorbed)。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    iid = uuid.uuid4()
    assert d.submit(_entry("updated", iid, 2)) is True
    fake_clock.advance(timedelta(seconds=3))
    assert d.submit(_entry("updated", iid, 3)) is True  # 未知version→延長
    fake_clock.advance(timedelta(seconds=3))
    assert d.submit(_entry("updated", iid, 4)) is True
    assert d.poll() == []  # 最後の到着から10秒未満
    fake_clock.advance(timedelta(seconds=10))  # v4到着から10秒
    groups = d.poll()
    assert len(groups) == 1
    assert groups[0].latest.triple[2] == 4
    assert [e.triple[2] for e in groups[0].absorbed] == [2, 3]


def test_release_at_is_arrival_plus_window(fake_clock):
    """解放時刻は未知version到着時刻+10秒(Clock基準・design §2.3)。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    iid = uuid.uuid4()
    d.submit(_entry("updated", iid, 2))
    fake_clock.advance(timedelta(seconds=9, milliseconds=900))
    assert d.poll() == []
    fake_clock.advance(timedelta(milliseconds=100))
    assert len(d.poll()) == 1


def test_same_version_resubmit_does_not_extend_window(fake_clock):
    """同一version再受信で解放時刻が延びない(窓の永久延長競合の排除 — design §2.3)。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    iid = uuid.uuid4()
    d.submit(_entry("updated", iid, 2))
    fake_clock.advance(timedelta(seconds=9))
    assert d.submit(_entry("updated", iid, 2)) is False  # 既知→非延長
    fake_clock.advance(timedelta(seconds=1))  # 到着から10秒(再受信から1秒)
    assert len(d.poll()) == 1  # 延長されていればここで解放されない


def test_resubmit_after_release_starts_new_window(fake_clock):
    """解放済み後の再受信は新規窓(version検査でstale破棄される経路 — design §2.3)。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    iid = uuid.uuid4()
    d.submit(_entry("updated", iid, 2))
    fake_clock.advance(timedelta(seconds=10))
    assert len(d.poll()) == 1
    assert d.submit(_entry("updated", iid, 2)) is True  # グループ削除後は新規


def test_groups_are_per_intent(fake_clock):
    """source_intent_idごとに独立した窓。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    a, b = uuid.uuid4(), uuid.uuid4()
    d.submit(_entry("updated", a, 1))
    d.submit(_entry("updated", b, 5))
    fake_clock.advance(timedelta(seconds=10))
    groups = d.poll()
    assert {g.intent_id for g in groups} == {a, b}


async def test_run_releases_and_acks_through_callback(fake_clock):
    """run()のtickループがClock操作だけで解放し、on_releaseへ渡す。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    iid = uuid.uuid4()
    d.submit(_entry("updated", iid, 2))
    stop = asyncio.Event()
    task = asyncio.create_task(d.run(stop=stop))
    await asyncio.sleep(0)  # runが待機に入るのを許す
    fake_clock.advance(timedelta(seconds=10))
    await asyncio.wait_for(_wait_len(released, 1), timeout=1.0)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


async def _wait_len(lst: list, n: int) -> None:
    while len(lst) < n:
        await asyncio.sleep(0.001)
