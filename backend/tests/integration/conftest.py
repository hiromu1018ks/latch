"""integration試験の共通フィクスチャ(design §3.1・§4.1)。

compose常設DBに対し、sessionスコープで alembic upgrade head(冪等)してから
functionスコープの AsyncEngine を提供する。エンジンは試験ごとに生成・破棄する
(イベントループをまたぐ接続再利用を避ける)。
"""

from pathlib import Path

import pytest
import redis.asyncio as redis_async
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.db import create_db_engine
from latch.settings import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def migrated_db() -> None:
    """DBをheadまでマイグレーションする(冪等・sessionで1回)。"""
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
async def db_engine(migrated_db) -> AsyncEngine:
    engine = create_db_engine(Settings())
    yield engine
    await engine.dispose()


@pytest.fixture
async def redis_client():
    """compose常設Redis(127.0.0.1:6379)へのクライアント(design §4.2-7)。"""
    client = redis_async.Redis.from_url(Settings().redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def api_client():
    """compose常設api(127.0.0.1:8000)へのHTTPクライアント(G0の実HTTP証拠)。"""
    async with AsyncClient(base_url="http://127.0.0.1:8000") as c:
        yield c
