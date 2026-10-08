"""契約試験の共通フィクスチャ(参照実装 = compose常設環境)。

integration/conftest.py と同じ資源(api 127.0.0.1:8000・ci DB)へ
接続する。実行は make test-ci と同じ前提(compose常設・worker停止)。
"""

from pathlib import Path

import pytest
import redis.asyncio as redis_async  # noqa: F401 (将来のseed用に確保)
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.db import create_db_engine
from latch.settings import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def migrated_db() -> None:
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
async def db_engine(migrated_db) -> AsyncEngine:
    engine = create_db_engine(Settings())
    yield engine
    await engine.dispose()


@pytest.fixture
async def api_client():
    async with AsyncClient(base_url="http://127.0.0.1:8000") as c:
        yield c
