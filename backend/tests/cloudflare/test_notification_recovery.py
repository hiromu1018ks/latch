"""T10: 通知jobの配送回復(計画 §6 T10)。

検証面:
- 提示と通知jobの同時保存(apply_proposalが先行書き込み・通知行存在)
- 送信失敗はアプリ内通知へ到達(権限なしも同様)
- 送信後ack失敗(配送記録失敗)を想定した再配送は安定通知IDで冪等
- pushの重複可能性は安定ID+dedupeで扱う
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest

from latch.adapters.d1.database import LocalD1Database, canonical_json
from latch.adapters.d1.notification_jobs import (
    D1NotificationDelivery,
    PushSenderStub,
)
from latch.domain.time import micros

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def db():
    database = LocalD1Database()
    database.migrate()
    yield database
    database.close()


async def _user(db, *, push_enabled: bool | None = None) -> str:
    uid = str(uuid_mod.uuid4())
    prefs = (
        canonical_json({"push_enabled": push_enabled})
        if push_enabled is not None
        else None
    )
    await db.batch(
        [
            db.prepare(
                "INSERT INTO users (id, display_name, profile, birth_date,"
                " auth_provider, auth_subject, consent_at, created_at, updated_at,"
                " visibility_preferences) VALUES (?, 'u', '{}', '1995-06-15',"
                " 'google', ?, ?, ?, ?, ?)"
            ).bind(uid, f"s-{uid[:8]}", micros(NOW), micros(NOW), micros(NOW), prefs),
        ]
    )
    return uid


async def _notification(db, user_id: str, latch_id: str | None = None) -> str:
    nid = str(uuid_mod.uuid4())
    latch = latch_id or str(uuid_mod.uuid4())
    await db.batch(
        [
            db.prepare(
                "INSERT INTO notifications (id, user_id, type, payload,"
                " created_at) VALUES (?, ?, 'proposal', ?, ?)"
            ).bind(nid, user_id, canonical_json({"latch_id": latch}), micros(NOW)),
        ]
    )
    return nid, latch


async def test_push_permission_delivery(db):
    """push権限あり→push送信・記録。"""
    uid = await _user(db, push_enabled=True)
    nid, latch = await _notification(db, uid)
    push = PushSenderStub()
    delivery = D1NotificationDelivery(db, push=push)
    outcome = await delivery.deliver_notification(
        {"job_id": "j1", "kind": "notify", "ref_id": latch}, NOW
    )
    assert outcome.delivered and outcome.channel == "push"
    assert push.sent == [nid]


async def test_no_permission_falls_back_to_in_app(db):
    """権限なし→アプリ内通知(通知行)へ到達。"""
    uid = await _user(db, push_enabled=False)
    _nid, latch = await _notification(db, uid)
    push = PushSenderStub()
    delivery = D1NotificationDelivery(db, push=push)
    outcome = await delivery.deliver_notification(
        {"job_id": "j2", "kind": "notify", "ref_id": latch}, NOW
    )
    assert outcome.delivered and outcome.channel == "in_app"
    assert push.sent == [], "pushは送らない"


async def test_push_failure_falls_back_to_in_app(db):
    """送信失敗→アプリ内通知で到達(pushの失敗で通知を落とさない)。"""
    uid = await _user(db, push_enabled=True)
    nid, latch = await _notification(db, uid)
    push = PushSenderStub()
    push.fail_ids.add(nid)
    delivery = D1NotificationDelivery(db, push=push)
    outcome = await delivery.deliver_notification(
        {"job_id": "j3", "kind": "notify", "ref_id": latch}, NOW
    )
    assert outcome.delivered and outcome.channel == "in_app"


async def test_redelivery_after_ack_failure_is_idempotent(db):
    """送信後ack失敗の再配送: 安定通知IDでdedupe(二重pushなし)。"""
    uid = await _user(db, push_enabled=True)
    nid, latch = await _notification(db, uid)
    push = PushSenderStub()
    delivery = D1NotificationDelivery(db, push=push)
    first = await delivery.deliver_notification(
        {"job_id": "j4", "kind": "notify", "ref_id": latch}, NOW
    )
    assert first.delivered and not first.deduped
    # ack失敗を想定した同jobの再配送
    second = await delivery.deliver_notification(
        {"job_id": "j4", "kind": "notify", "ref_id": latch},
        NOW + timedelta(seconds=5),
    )
    assert second.deduped is True
    assert push.sent == [nid], "再送しない(安定通知ID)"


async def test_proposal_and_notification_saved_together(db):
    """提示と通知jobの同時保存(apply_proposalの先行書き込みを確認)。"""
    from latch.adapters.d1.atomic import D1AtomicRepository
    from latch.domain.types import CommandStatus

    u1 = await _user(db)
    u2 = await _user(db)
    intents = []
    for uid in (u1, u2):
        iid = str(uuid_mod.uuid4())
        await db.batch(
            [
                db.prepare(
                    "INSERT INTO intents (id, user_id, category_primary,"
                    " alcohol_involved, raw_text, structured_data, geo_lon,"
                    " geo_lat, status, version, time_start, expires_at,"
                    " created_at, updated_at)"
                    " VALUES (?, ?, 'meal', 0, 'x', '{}', 130.55, 31.59,"
                    " 'active', 1, ?, ?, ?, ?)"
                ).bind(
                    iid,
                    uid,
                    micros(NOW + timedelta(hours=6)),
                    micros(NOW + timedelta(days=3)),
                    micros(NOW),
                    micros(NOW),
                ),
            ]
        )
        intents.append(iid)
    repo = D1AtomicRepository(db)
    result = await repo.apply_proposal(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        {intents[0]: 1, intents[1]: 1},
        {
            "intent_ids": intents,
            "score": 0.9,
            "proposal": {"headcount": 2, "match_level": "high"},
        },
        NOW,
    )
    assert result.status is CommandStatus.APPLIED
    # 通知行とnotify jobが同じcommandの効果として存在
    notifs = (
        await db.prepare(
            "SELECT COUNT(*) AS c FROM notifications WHERE type = 'proposal'"
        ).first()
    )["c"]
    jobs = (
        await db.prepare("SELECT COUNT(*) AS c FROM jobs WHERE kind = 'notify'").first()
    )["c"]
    assert notifs == 2
    assert jobs == 1
