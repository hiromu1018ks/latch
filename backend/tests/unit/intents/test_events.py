"""events(design §4.1-3)。event_type定数のピン留めとinsertパラメータの検証。
実INSERTはintegration(test-ci)。ここではconnスタブの呼び出し記録で検証する。
"""

import json
import uuid
from datetime import UTC, datetime

from latch.intents import events

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
INTENT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


class FakeConn:
    def __init__(self):
        self.executes = []  # (stmt, params)

    async def execute(self, stmt, params=None):
        self.executes.append((stmt, params))


def test_event_type_constants_are_pinned():
    """design §2.2: embedding_completed(05 §2)の過去分詞形に揃えた確定値。"""
    assert events.EVENT_CREATED == "created"
    assert events.EVENT_UPDATED == "updated"
    assert events.EVENT_DELETED == "deleted"
    assert events.EVENT_EXPIRED == "expired"
    assert events.EVENT_SCHEDULED == "scheduled"


async def test_insert_match_event_passes_version_payload_and_pending():
    conn = FakeConn()
    await events.insert_match_event(
        conn,
        event_type=events.EVENT_CREATED,
        intent_id=INTENT_ID,
        version=1,
        now=NOW,
    )
    assert len(conn.executes) == 1
    stmt, params = conn.executes[0]
    assert stmt is events._INSERT_EVENT
    assert params["event_type"] == "created"
    assert params["source_intent_id"] == INTENT_ID
    assert json.loads(params["payload"]) == {"version": 1}
    assert params["created_at"] == NOW  # Clock由来の明示値


def test_insert_sql_pins_pending_status_literal():
    """status='pending'はSQLリテラル(design §2.2A)。"""
    assert "'pending'" in str(events._INSERT_EVENT)
    assert "match_events" in str(events._INSERT_EVENT)


def test_insert_event_sql_has_on_conflict_do_nothing():
    """同一3点組の再発行(cancelled再DELETE等)はUNIQUE索引で挿入しない
    (M3 ws-6 design §2.2・ux_match_events_idempotency)。"""
    compiled = str(events._INSERT_EVENT)
    assert "ON CONFLICT DO NOTHING" in compiled
