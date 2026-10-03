"""events publishサブコマンドのunit試験(M4 ws-1 design §4.1)。

PubsubEventBusをスタブへ差し替え、3点組の組立とraw-payload優先、宛先
Settingsの引き渡しを検証する(実エミュレータ不要)。
"""

import uuid

import pytest

import latch.events.__main__ as events_cli
from latch.settings import Settings

INTENT = uuid.UUID("11111111-1111-1111-1111-111111111111")


class _StubBus:
    instances: list["_StubBus"] = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.published: list[dict] = []
        self.raw: list[bytes] = []
        self.closed = False
        _StubBus.instances.append(self)

    async def publish_match_event(self, *, event_type, intent_id, version) -> None:
        self.published.append(
            {"event_type": event_type, "intent_id": intent_id, "version": version}
        )

    async def publish_raw(self, data: bytes) -> None:
        self.raw.append(data)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def stub_bus(monkeypatch):
    _StubBus.instances = []
    monkeypatch.setattr(events_cli, "PubsubEventBus", _StubBus)
    return _StubBus


def _parse_args(argv: list[str]):
    return events_cli.build_parser().parse_args(argv)


async def test_publish_created_assembles_triple(stub_bus, capsys):
    rc = await events_cli._run_publish(
        Settings(),
        _parse_args(
            ["publish", "created", "--intent-id", str(INTENT), "--version", "3"]
        ),
    )
    assert rc == 0
    (bus,) = stub_bus.instances
    assert bus.published == [
        {"event_type": "created", "intent_id": INTENT, "version": 3}
    ]
    assert bus.raw == []
    assert bus.closed is True
    assert f"intent={INTENT}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "event_type",
    ["created", "updated", "deleted", "expired", "scheduled", "embedding_completed"],
)
async def test_publish_accepts_all_six_event_types(stub_bus, event_type):
    # 6種すべて手動発火可(scheduledの常時発行経路は存在しないため、
    # 手動発火はこの種別を観察する唯一の経路 — design §2.5)
    rc = await events_cli._run_publish(
        Settings(),
        _parse_args(["publish", event_type, "--intent-id", str(INTENT)]),
    )
    assert rc == 0
    bus = stub_bus.instances[0]
    assert bus.published[0]["event_type"] == event_type
    assert bus.published[0]["version"] == 1  # 既定


def test_publish_rejects_unknown_event_type(stub_bus):
    with pytest.raises(SystemExit) as ei:
        _parse_args(["publish", "bogus", "--intent-id", str(INTENT)])
    assert ei.value.code == 2
    assert stub_bus.instances == []


def test_publish_requires_intent_id():
    with pytest.raises(SystemExit) as ei:
        _parse_args(["publish", "created"])
    assert ei.value.code == 2


def test_publish_rejects_invalid_uuid():
    with pytest.raises(SystemExit) as ei:
        _parse_args(["publish", "created", "--intent-id", "not-a-uuid"])
    assert ei.value.code == 2


async def test_raw_payload_takes_priority_over_triple(stub_bus):
    # 毒ペイロード・参照不在Event試験用: raw指定時は3点組を発行しない
    rc = await events_cli._run_publish(
        Settings(),
        _parse_args(
            [
                "publish",
                "created",
                "--intent-id",
                str(INTENT),
                "--raw-payload",
                '{"event_type": 123}',
            ]
        ),
    )
    assert rc == 0
    (bus,) = stub_bus.instances
    assert bus.raw == [b'{"event_type": 123}']
    assert bus.published == []


async def test_destination_settings_reach_the_bus(stub_bus, monkeypatch):
    monkeypatch.setenv("LATCH_PUBSUB_EMULATOR_HOST", "127.0.0.1:18085")
    monkeypatch.setenv("LATCH_PUBSUB_PROJECT_ID", "latch-staging")
    rc = await events_cli._run_publish(
        Settings(),
        _parse_args(["publish", "created", "--intent-id", str(INTENT)]),
    )
    assert rc == 0
    (bus,) = stub_bus.instances
    assert bus.settings.pubsub_emulator_host == "127.0.0.1:18085"
    assert bus.settings.pubsub_project_id == "latch-staging"


def test_main_dispatches_publish(stub_bus):
    assert events_cli.main(["publish", "created", "--intent-id", str(INTENT)]) == 0
    assert stub_bus.instances[0].published[0]["intent_id"] == INTENT
