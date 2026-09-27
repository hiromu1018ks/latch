"""/health と Clock差し替えの両経路(design §4.4)。/health はv1配下に置かない。"""

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from latch.core.clock import FakeClock
from latch.core.deps import get_clock
from latch.main import create_app


async def _get_body(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    return resp.json()


async def test_health_returns_ok_and_clock_time(app, fake_clock):
    body = await _get_body(app)
    assert body["status"] == "ok"
    assert body["server_time"] == fake_clock.now().isoformat()


async def test_health_server_time_is_iso8601_utc(app):
    body = await _get_body(app)
    parsed = datetime.fromisoformat(body["server_time"])  # ISO8601として妥当
    assert parsed.tzinfo is not None  # naiveでない
    assert parsed.utcoffset().total_seconds() == 0  # UTC


async def test_health_create_app_injection():
    clock = FakeClock(datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC))
    app = create_app(clock=clock)
    body = await _get_body(app)
    assert body["server_time"] == clock.now().isoformat()


async def test_health_dependency_override(app):
    other = FakeClock(datetime(2030, 6, 15, 3, 30, 0, tzinfo=UTC))
    app.dependency_overrides[get_clock] = lambda: other
    try:
        body = await _get_body(app)
    finally:
        app.dependency_overrides.clear()
    assert body["server_time"] == other.now().isoformat()


async def test_health_not_under_v1(app, client):
    resp = await client.get("/v1/health")
    assert resp.status_code == 404  # v1契約の外(C3の例外とはしない)
