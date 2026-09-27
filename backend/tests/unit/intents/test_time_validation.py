"""時刻検証の境界詳細(design §4.1-6)。FakeClockでnowちょうど/now-1秒/
now+7日ちょうど/now+7日+1秒の4境界とexpires_at同一境界・draftの対照を
確定する(05 §5「過去(呼び出し時点より前)」「現在+7日を超える」の字義)。
test_intents_service.py とスタブを共有しない(相互import依存を避ける —
時刻境界に必要な最小の部品のみここで定義する)。
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.geo.service import Geofeature
from latch.intents.errors import IntentValidationError
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.service import IntentService
from latch.intents.store import UserRow

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
START = datetime(2026, 9, 27, 21, 0, 0, tzinfo=UTC)  # 妥当値(NOW+9h)

TENMONKAN = Geofeature(
    source="osm_poi",
    kind="amenity",
    name="天文館",
    city_name=None,
    pref_name=None,
    lon=130.5581,
    lat=31.5965,
)


class _Conn:
    async def execute(self, stmt, params=None):
        return None


class _Ctx:
    def __init__(self):
        self.conn = _Conn()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _Store:
    async def fetch_user_row(self, provider, subject):
        return UserRow(
            id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
            birth_date=date(2000, 1, 1),
        )

    async def insert(self, conn, cols, *, user_id, status, now):
        return uuid.uuid4()


class _Geocoder:
    async def geocode_forward(self, name):
        return TENMONKAN


def _svc():
    ctx = _Ctx()
    return IntentService(
        clock=FakeClock(NOW),
        store=_Store(),
        uow=ctx,
        reader=ctx,
        geocoder=_Geocoder(),
    )


def _input(
    start: datetime | None,
    expires_at: datetime | None = START + timedelta(hours=3),
) -> StructuredIntentInput:
    data = {
        "category": {"primary": "meal"},
        "time": {"start": start.isoformat()} if start else None,
        "location": {"name": "天文館"},
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
    return StructuredIntentInput.model_validate(data)


async def _create(svc, inp):
    return await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="active",
        structured_intent=inp,
    )


async def test_time_start_equal_now_is_accepted():
    await _create(_svc(), _input(NOW))  # 「過去(より前)」のみ422


async def test_time_start_one_second_past_is_rejected():
    with pytest.raises(IntentValidationError):
        await _create(_svc(), _input(NOW - timedelta(seconds=1)))


async def test_time_start_exactly_7days_is_accepted():
    await _create(_svc(), _input(NOW + timedelta(days=7)))  # 「超える」のみ422


async def test_time_start_7days_plus_one_second_is_rejected():
    with pytest.raises(IntentValidationError):
        await _create(_svc(), _input(NOW + timedelta(days=7, seconds=1)))


async def test_expires_at_boundaries_mirror_time_start():
    svc = _svc()
    await _create(svc, _input(START, expires_at=NOW))  # nowちょうど=受理
    await _create(svc, _input(START, expires_at=NOW + timedelta(days=7)))
    with pytest.raises(IntentValidationError):
        await _create(svc, _input(START, expires_at=NOW - timedelta(seconds=1)))
    with pytest.raises(IntentValidationError):
        await _create(
            svc,
            _input(START, expires_at=NOW + timedelta(days=7, seconds=1)),
        )


async def test_draft_accepts_past_and_far_future_times():
    """draftは形式検証のみ — 過去も+8日も受理される対照(確定値7)。"""
    svc = _svc()
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=_input(NOW - timedelta(days=1), expires_at=None),
    )
    assert row.status == "draft"
    row2 = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=_input(NOW + timedelta(days=8)),
    )
    assert row2.status == "draft"
