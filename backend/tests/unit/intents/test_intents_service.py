"""IntentServiceユースケース(design §4.1-4)。スタブstore・スタブgeocoder・
FakeClock・FakeUowで決定的にdesign §2.2表の全操作を網羅する(Task 5〜9で
段階的に追記)。uowとreaderは別々のFakeConnを返し、Event INSERTが書き込み
トランザクション内で呼ばれたことを検証できるようにする。
"""

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.geo.service import Geofeature
from latch.intents import events as events_mod
from latch.intents.errors import (
    DependencyUnavailableError,
    GeocodingFailedError,
    IntentNotFoundError,
    IntentValidationError,
    UnderAgeError,
)
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.service import IntentService
from latch.intents.store import IntentRow, UserRow

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-27 21:00
USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
START = datetime(2026, 9, 27, 21, 0, 0, tzinfo=UTC)  # NOW+9h(active妥当値)
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
    def __init__(self):
        self.executes = []

    async def execute(self, stmt, params=None):
        self.executes.append((stmt, params))


class FakeCtx:
    """uow/reader用(engine.begin / engine.connect と同じ形のCallable)。"""

    def __init__(self, conn: FakeConn):
        self.conn = conn
        self.entries = 0

    def __call__(self):
        self.entries += 1
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


_UNSET = object()  # user_row=None(404試験)とデフォルト(26歳)を区別する


class StubStore:
    """IntentStoreのテストスタブ(結果を仕込む・呼び出しを記録する)。"""

    def __init__(self, *, user_row=_UNSET, rows=None, page=None):
        self.user_row = (
            UserRow(id=USER_ID, birth_date=date(2000, 1, 1))  # 26歳
            if user_row is _UNSET
            else user_row
        )
        self.rows = dict(rows or {})
        self.page = list(page or [])
        self.inserts = []
        self.updates = []
        self.status_updates = []
        self.list_calls = []
        self.next_id = uuid.uuid4()

    async def fetch_user_row(self, provider, subject):
        return self.user_row

    async def fetch(self, conn, intent_id):
        return self.rows.get(intent_id)

    async def fetch_for_update(self, conn, intent_id):
        return self.rows.get(intent_id)

    async def insert(self, conn, cols, *, user_id, status, now):
        self.inserts.append(
            {"cols": cols, "user_id": user_id, "status": status, "now": now}
        )
        return self.next_id

    async def update(
        self, conn, intent_id, cols, *, status, version, now, expected_status
    ):
        self.updates.append(
            {
                "intent_id": intent_id,
                "cols": cols,
                "status": status,
                "version": version,
                "now": now,
                "expected_status": expected_status,
            }
        )
        return 1

    async def update_status(
        self, conn, intent_id, *, status, version, now, expected_status
    ):
        self.status_updates.append(
            {
                "intent_id": intent_id,
                "status": status,
                "version": version,
                "now": now,
                "expected_status": expected_status,
            }
        )
        return 1

    async def list_page(self, conn, user_id, *, status, before, limit):
        self.list_calls.append(
            {"user_id": user_id, "status": status, "before": before, "limit": limit}
        )
        return self.page[:limit]


class StubGeocoder:
    def __init__(self, feature=None):
        self.feature = feature
        self.calls = []

    async def geocode_forward(self, name):
        self.calls.append(name)
        return self.feature


def _service(*, store=None, geocoder=None, clock=None):
    uow_conn = FakeConn()
    read_conn = FakeConn()
    store = store if store is not None else StubStore()
    geocoder = geocoder if geocoder is not None else StubGeocoder(TENMONKAN)
    svc = IntentService(
        clock=clock or FakeClock(NOW),
        store=store,
        uow=FakeCtx(uow_conn),
        reader=FakeCtx(read_conn),
        geocoder=geocoder,
    )
    return svc, store, geocoder, uow_conn, read_conn


def _event_calls(conn: FakeConn):
    """uowコネクション上のEvent INSERT呼び出し(event_type, params)一覧。"""
    out = []
    for stmt, params in conn.executes:
        if stmt is events_mod._INSERT_EVENT:
            out.append((params["event_type"], params))
    return out


def _active_input(**overrides) -> StructuredIntentInput:
    data = {
        "category": {"primary": "drinking", "secondary": "焼肉"},
        "alcohol_involved": True,
        "time": {"start": START.isoformat()},
        "location": {"name": "天文館", "radius_m": 2000},
        "budget": {"max": 5000},
        "participants": {"min": 2, "max": 4},
        "visibility": "summary_only",
        "notification_level": "nearby_also",
        "expires_at": (START + timedelta(hours=3)).isoformat(),
        "soft_constraints": ["軽く飲みたい"],
        "ng_unverifiable": ["会社関係の人は避けたい"],
        "negative_constraints": [],
    }
    data.update(overrides)
    return StructuredIntentInput.model_validate(data)


def _row(**overrides) -> IntentRow:
    base = dict(
        id=uuid.uuid4(),
        user_id=USER_ID,
        category_primary="drinking",
        alcohol_involved=True,
        raw_text="原文",
        structured_data={"location_name": "天文館"},
        geo_radius_m=2000,
        budget_max=5000,
        participants_min=2,
        participants_max=4,
        visibility="hidden_until_match",
        notification_level="proposals_only",
        status="draft",
        version=1,
        time_start=START,
        time_end=None,
        expires_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return IntentRow(**base)


# --- (a) POST activeハッピー(design §4.1-4a)---


async def test_post_active_inserts_and_emits_created_event_v1():
    svc, store, geo, uow_conn, _ = _service()
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="今夜天文館で飲みたい",
        status="active",
        structured_intent=_active_input(),
    )
    assert row.status == "active"
    assert row.version == 1
    assert row.expires_at is not None  # 補完済み(expires_at指定値)
    assert len(store.inserts) == 1
    ins = store.inserts[0]
    assert ins["user_id"] == USER_ID
    assert ins["status"] == "active"
    assert ins["cols"].geo_lon == TENMONKAN.lon  # ジオコーディング結果
    assert ins["cols"].geo_lat == TENMONKAN.lat
    assert geo.calls == ["天文館"]
    evs = _event_calls(uow_conn)
    assert len(evs) == 1
    etype, params = evs[0]
    assert etype == "created"
    assert json.loads(params["payload"]) == {"version": 1}
    # status='pending' はSQLリテラル(test_events.py のリテラル試験で担保)
    assert params["created_at"] == NOW


async def test_post_active_completes_defaults_in_saved_columns():
    """補完消費: time_end+3h・radius既定・expires_at補完(§4.1-2aと対の確認)。"""
    from latch.intents.completion import default_time_end, nearest_expires_at

    inp = _active_input(
        time={"start": START.isoformat()},
        location={"name": "天文館"},
        expires_at=None,
    )
    svc, store, _, _, _ = _service()
    await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="active",
        structured_intent=inp,
    )
    cols = store.inserts[0]["cols"]
    assert cols.time_end == default_time_end(START)
    assert cols.expires_at == nearest_expires_at(START, NOW)
    assert cols.geo_radius_m == 1000


# --- (b) POST draft(design §4.1-4b)---


async def test_post_draft_saves_without_event_and_geocoding():
    svc, store, geo, uow_conn, _ = _service()
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="下書きだけ",
        status="draft",
        structured_intent=None,
    )
    assert row.status == "draft"
    assert row.version == 1
    cols = store.inserts[0]["cols"]
    assert cols.geo_lon is None and cols.geo_lat is None
    assert cols.time_end is None
    assert cols.expires_at is None
    assert cols.visibility == "hidden_until_match"  # draftでも格納(確定値3)
    assert _event_calls(uow_conn) == []
    assert geo.calls == []


# --- (c) 検証切替 active/draft(design §4.1-4c)---


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"category": None}, id="category-missing"),
        pytest.param({"time": None}, id="time-missing"),
        pytest.param({"location": None}, id="location-missing"),
    ],
)
async def test_active_required3_missing_rejects_but_draft_accepts(overrides):
    """同一の欠落入力がactive=422・draft=受理になる対照(確定値3・8)。"""
    svc, store, _, _, _ = _service()
    with pytest.raises(IntentValidationError) as ei:
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(**overrides),
        )
    assert ei.value.code == "VALIDATION_ERROR"
    assert store.inserts == []  # 保存まで到達していない
    # draftは同じ入力を受理する(時刻・ジオコーディングも行わない)
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=_active_input(**overrides),
    )
    assert row.status == "draft"


async def test_active_past_time_rejected_draft_accepted():
    past = (NOW - timedelta(hours=1)).isoformat()
    svc, _, _, _, _ = _service()
    with pytest.raises(IntentValidationError):
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(time={"start": past}),
        )
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=_active_input(time={"start": past}),
    )
    assert row.status == "draft"


async def test_under_age_drinking_rejected_but_birthday_20_accepted():
    """19歳=422 UNDER_AGE・20歳の誕生日当日=受理(08 D-10・age_years仕様)。"""
    svc19, _, _, _, _ = _service(
        store=StubStore(user_row=UserRow(id=USER_ID, birth_date=date(2006, 9, 28)))
    )
    with pytest.raises(UnderAgeError) as ei:
        await svc19.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),  # drinking
        )
    assert ei.value.code == "UNDER_AGE"
    svc20, _, _, _, _ = _service(
        store=StubStore(user_row=UserRow(id=USER_ID, birth_date=date(2006, 9, 27)))
    )  # JST 2026-09-27時点で20歳ちょうど
    row = await svc20.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="active",
        structured_intent=_active_input(),
    )
    assert row.status == "active"


async def test_under_age_not_checked_for_non_drinking():
    """meal等のalcohol=falseでは19歳でも受理(確定値8はalcohol=trueのみ)。"""
    svc, _, _, _, _ = _service(
        store=StubStore(user_row=UserRow(id=USER_ID, birth_date=date(2006, 9, 28)))
    )
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="active",
        structured_intent=_active_input(
            category={"primary": "meal"}, alcohol_involved=False
        ),
    )
    assert row.status == "active"


async def test_geocoding_failure_rejects_active_but_not_draft():
    """ジオコーダNone=422 GEOCODING_FAILED(draftはジオコーディングしない)。"""
    svc, store, geo, _, _ = _service(geocoder=StubGeocoder(feature=None))
    with pytest.raises(GeocodingFailedError) as ei:
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),
        )
    assert ei.value.code == "GEOCODING_FAILED"
    assert store.inserts == []
    assert geo.calls == ["天文館"]
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=None,
    )
    assert row.status == "draft"


async def test_unregistered_jwt_returns_404():
    """user_lookup None=404(GET /v1/users/meと同一挙動 — design §2.6)。"""
    svc, _, _, _, _ = _service(store=StubStore(user_row=None))
    with pytest.raises(IntentNotFoundError):
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),
        )


async def test_store_failure_maps_to_503_dependency_unavailable():

    class FailingStore(StubStore):
        async def fetch_user_row(self, provider, subject):
            raise RuntimeError("db down")

    svc, _, _, _, _ = _service(store=FailingStore())
    with pytest.raises(DependencyUnavailableError):
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),
        )


async def test_raw_text_and_location_never_appear_in_logs_or_errors(caplog):
    """08 §2.4: ログ・例外に本文・地名を出さない(design §4.1-7)。"""
    secret_raw = "内緒の飲み会の件"
    secret_place = "秘密の場所"
    svc, _, _, _, _ = _service(geocoder=StubGeocoder(feature=None))
    with caplog.at_level(logging.INFO):
        with pytest.raises(GeocodingFailedError) as ei:
            await svc.create(
                auth_provider="google",
                auth_subject="s",
                raw_text=secret_raw,
                status="active",
                structured_intent=_active_input(location={"name": secret_place}),
            )
    assert secret_raw not in caplog.text
    assert secret_place not in caplog.text
    assert secret_raw not in str(ei.value)
    assert secret_place not in str(ei.value)
