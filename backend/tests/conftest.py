"""共通フィクスチャ(design §3.1)。"""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from latch.core.clock import FakeClock
from latch.main import create_app


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def app(fake_clock):
    return create_app(clock=fake_clock)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
