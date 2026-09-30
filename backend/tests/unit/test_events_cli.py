"""latch.events CLI(purge-match-sub)のunit試験(ws-8 design §2.7)。

PubsubEventBusをスタブへ差し替え、構築→delete_subscription→close→exit 0を
検証する(実エミュレータ不要・外部プロセスなし)。
"""

import pytest

import latch.events.__main__ as events_cli


class _StubBus:
    instances: list["_StubBus"] = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.deleted = 0
        self.closed = False
        _StubBus.instances.append(self)

    def delete_subscription(self) -> None:
        self.deleted += 1

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def stub_bus(monkeypatch):
    _StubBus.instances = []
    monkeypatch.setattr(events_cli, "PubsubEventBus", _StubBus)
    return _StubBus


async def test_purge_deletes_subscription_and_closes(stub_bus, capsys):
    rc = await events_cli._run_purge(events_cli.Settings())
    assert rc == 0
    (bus,) = stub_bus.instances
    assert bus.deleted == 1
    assert bus.closed is True
    out = capsys.readouterr().out
    assert "purged subscription" in out
    assert "match-events-sub" in out  # 設定の常設subscription名


def test_main_dispatches_purge_match_sub(stub_bus, monkeypatch):
    import asyncio

    rc = events_cli.main(["purge-match-sub"])
    assert rc == 0
    assert stub_bus.instances[0].deleted == 1
    assert asyncio is not None  # mainはasyncio.runで_run_purgeを呼ぶ


def test_main_requires_command():
    with pytest.raises(SystemExit) as ei:
        events_cli.main([])
    assert ei.value.code == 2  # argparse required
