"""uowコミット後のpublish接続(design §2.2-B)。

publishはトランザクション外・失敗は握る・発行箇所とevent_typeは
M1実装のまま(insert_match_eventと同一条件)であることを検証する。
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from latch.core.clock import FakeClock
from latch.geo.service import Geofeature
from latch.intents import EVENT_CREATED, EVENT_DELETED, EVENT_UPDATED
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.service import IntentService
from latch.intents.store import IntentRow, UserRow

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
START = datetime(2026, 9, 28, 21, 0, 0, tzinfo=UTC)  # NOW+9h(active妥当値)
TENMONKAN = Geofeature(
    source="osm_poi",
    kind="amenity",
    name="天文館",
    city_name=None,
    pref_name=None,
    lon=130.5581,
    lat=31.5965,
)


class FakeConn:
    """insert_match_event(outbox INSERT)のconn.executeを受け流す記録スタブ。"""

    def __init__(self):
        self.executes: list = []

    async def execute(self, stmt, params=None):
        self.executes.append((stmt, params))


class FakeCtx:
    """uow/reader用(engine.begin / engine.connect と同じ形のCallable)。"""

    def __init__(self, conn: FakeConn):
        self.conn = conn
        self.exited = 0  # __aexit__(=コミット)回数

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        self.exited += 1
        return False


class StubStore:
    """状態持ちスタブ(pause→resume→deleteの遷移を通す)。"""

    def __init__(self):
        self.next_id = uuid.uuid4()
        self.status = "active"
        self.version = 1

    async def fetch_user_row(self, provider, subject):
        return UserRow(id=USER_ID, birth_date=date(2000, 1, 1))

    async def fetch(self, conn, intent_id):
        return self._row()

    async def fetch_for_update(self, conn, intent_id):
        return self._row()

    async def insert(self, conn, cols, *, user_id, status, now):
        return self.next_id

    async def update(
        self, conn, intent_id, cols, *, status, version, now, expected_status
    ):
        self.version = version
        return 1

    async def update_status(
        self, conn, intent_id, *, status, version, now, expected_status
    ):
        self.status = status
        self.version = version
        return 1

    async def list_page(self, conn, user_id, *, status, before, limit):
        return []

    async def lock_user_row(self, conn, user_id):
        pass

    async def count_active(self, conn, user_id, *, now):
        return 0

    def _row(self) -> IntentRow:
        return IntentRow(
            id=self.next_id,
            user_id=USER_ID,
            category_primary="drinking",
            alcohol_involved=False,
            raw_text="原文",
            structured_data={"location_name": "天文館"},
            geo_radius_m=2000,
            budget_max=5000,
            participants_min=2,
            participants_max=4,
            visibility="hidden_until_match",
            notification_level="proposals_only",
            status=self.status,
            version=self.version,
            time_start=START,
            time_end=None,
            expires_at=None,
            created_at=NOW,
            updated_at=NOW,
        )


class StubGeocoder:
    async def geocode_forward(self, name: str):
        return TENMONKAN


class StubBus:
    def __init__(self):
        self.published: list[dict] = []

    async def publish_match_event(self, *, event_type, intent_id, version):
        self.published.append(
            {"event_type": event_type, "intent_id": intent_id, "version": version}
        )


class FailingBus(StubBus):
    async def publish_match_event(self, *, event_type, intent_id, version):
        raise RuntimeError("pubsub down")


def _service(bus, uow: FakeCtx) -> IntentService:
    return IntentService(
        clock=FakeClock(NOW),
        store=StubStore(),
        uow=uow,
        reader=FakeCtx(FakeConn()),
        geocoder=StubGeocoder(),
        event_bus=bus,
    )


def _active_input() -> StructuredIntentInput:
    return StructuredIntentInput.model_validate(
        {
            "category": {"primary": "drinking", "secondary": None},
            "alcohol_involved": False,
            "time": {"start": START.isoformat()},
            "location": {"name": "天文館"},
            "expires_at": (START + timedelta(hours=3)).isoformat(),
        }
    )


async def test_publish_after_commit_on_active_create():
    """active作成 → uowコミット後(event=created, version=1)を1回publish。"""
    bus, uow = StubBus(), FakeCtx(FakeConn())
    svc = _service(bus, uow)
    row = await svc.create(
        auth_provider="google",
        auth_subject="s1",
        raw_text="x",
        status="active",
        structured_intent=_active_input(),
    )
    assert bus.published == [
        {"event_type": EVENT_CREATED, "intent_id": row.id, "version": 1}
    ]
    assert uow.exited >= 1  # uow(コミット)の外でpublish


async def test_no_publish_for_draft_create():
    """draft作成は発行しない(06 §9-0)。"""
    bus, uow = StubBus(), FakeCtx(FakeConn())
    svc = _service(bus, uow)
    await svc.create(
        auth_provider="google",
        auth_subject="s1",
        raw_text="x",
        status="draft",
        structured_intent=None,
    )
    assert bus.published == []


async def test_publish_failure_is_swallowed():
    """publish失敗でもAPI応答は正常(例外にしない — design §2.2-B)。"""
    bus, uow = FailingBus(), FakeCtx(FakeConn())
    svc = _service(bus, uow)
    row = await svc.create(
        auth_provider="google",
        auth_subject="s1",
        raw_text="x",
        status="active",
        structured_intent=_active_input(),
    )
    assert row.status == "active"


async def test_publish_on_update_resume_delete_but_not_pause():
    """update/resumeはupdated・deleteはdeleted・pauseは発行なし(M1発行表)。"""
    bus, uow = StubBus(), FakeCtx(FakeConn())
    svc = _service(bus, uow)
    intent_id = uuid.uuid4()
    await svc.update(
        auth_provider="google",
        auth_subject="s1",
        intent_id=intent_id,
        raw_text="y",
        status=None,
        structured_intent=_active_input(),
    )
    await svc.pause(auth_provider="google", auth_subject="s1", intent_id=intent_id)
    await svc.resume(auth_provider="google", auth_subject="s1", intent_id=intent_id)
    await svc.delete(auth_provider="google", auth_subject="s1", intent_id=intent_id)
    types = [p["event_type"] for p in bus.published]
    assert types == [EVENT_UPDATED, EVENT_UPDATED, EVENT_DELETED]


async def test_make_intent_service_accepts_event_bus():
    import inspect

    from latch.intents.service import make_intent_service

    sig = inspect.signature(make_intent_service)
    assert "event_bus" in sig.parameters
