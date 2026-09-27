"""DB接続基盤(core/db.py + conftest)の疎通(design §4.1 integration)。"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def test_db_connect_returns_postgres17(db_engine):
    async with db_engine.connect() as conn:
        version = await conn.scalar(text("SELECT version()"))
    assert version is not None
    assert "PostgreSQL 17" in version


async def test_connection_timezone_is_utc(db_engine):
    """接続はUTCに固定(design §2.3: timestamptzはinstant保持・
    JST運用はアプリ層表現)。"""
    async with db_engine.connect() as conn:
        tz = await conn.scalar(text("SHOW timezone"))
    assert tz == "UTC"
