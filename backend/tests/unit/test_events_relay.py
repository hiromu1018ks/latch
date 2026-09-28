"""フォールバックリレー(design §2.2-B)。

再publish対象が「created_atが一定時間(既定30秒)以上前のpending行」のみ
であること・publish例外で中断せず次行/次周期へ続くことを検証する。
"""

import uuid
from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.events.relay import FallbackRelay
from latch.settings import Settings

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
IID = uuid.UUID("00000000-0000-4000-8000-0000000000cc")


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


class StubBus:
    def __init__(self, fail: bool = False):
        self.published: list[tuple[str, uuid.UUID, int]] = []
        self.fail = fail

    async def publish_match_event(self, *, event_type, intent_id, version):
        if self.fail:
            raise RuntimeError("pubsub down")
        self.published.append((event_type, intent_id, version))


def _relay(engine, clock=None, bus=None) -> tuple[FallbackRelay, ScriptedConn]:
    relay = FallbackRelay(
        engine=engine,
        clock=clock if clock is not None else FakeClock(NOW),
        bus=bus if bus is not None else StubBus(),
        settings=Settings(),
    )
    return relay, engine.conn


async def test_republish_only_stale_pending_rows():
    """SELECTはstatus=pendingかつcreated_at < now-30秒。該当行をpublishする。"""
    engine = ScriptedEngine(FakeResult([("updated", IID, "3"), ("updated", IID, "4")]))
    bus = StubBus()
    relay, conn = _relay(engine, bus=bus)
    assert await relay.run_once() == 2
    sql, params = conn.calls[0]
    assert "pending" in sql and "created_at" in sql
    assert params["threshold"] == NOW - timedelta(seconds=30)  # しきい値=30秒
    assert bus.published == [("updated", IID, 3), ("updated", IID, 4)]


async def test_no_rows_means_no_publish():
    engine = ScriptedEngine(FakeResult([]))
    bus = StubBus()
    relay, _ = _relay(engine, bus=bus)
    assert await relay.run_once() == 0
    assert bus.published == []


async def test_publish_failure_is_swallowed():
    """publish例外で中断せず次行も試み、run_onceは正常終了(design §2.2-B)。"""
    engine = ScriptedEngine(FakeResult([("updated", IID, 3), ("updated", IID, 4)]))
    bus = StubBus(fail=True)
    relay, _ = _relay(engine, bus=bus)
    assert await relay.run_once() == 0  # 例外を外へ出さない
    assert len(bus.published) == 0
