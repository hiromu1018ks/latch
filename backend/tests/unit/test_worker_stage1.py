"""Stage1(第1段処理)とIncomingEvent入力契約のunit試験(design §4.1)。

IncomingEvent.from_payload はテストパブリッシャー・API経由の両方の受信で
最初に通る境界(10 §1「テスト用パブリッシャー」の入力面)。Stage1本体は
Task 5でこのファイルへ追記する(スタブconn・注入sleepで決定的に)。
"""

import uuid

from latch.events import IncomingEvent


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
