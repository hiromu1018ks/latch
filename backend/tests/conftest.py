"""共通フィクスチャ(design §3.1)。"""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from latch.core.clock import FakeClock
from latch.main import create_app

# tests直下をimport可能にする(contracts資産をcloudflare試験からも使うため —
# 2026-10-08移行計画T0。pytest prepend modeは各試験ディレクトリしか置かない)
_TESTS_ROOT = str(Path(__file__).resolve().parent)
if _TESTS_ROOT not in sys.path:
    sys.path.insert(0, _TESTS_ROOT)


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
