"""0008〜0011のスキーマ検証(M4 ws-4・UI v2 P1B・integration・test-ciで実行)。"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def test_head_is_0011_in_obs_schema(db_engine):
    """0011がhead(V1-P4追加・test_geoの追従と同一の検査をobs側でも持つ)。"""
    async with db_engine.connect() as conn:
        version = (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar()
    assert version == "0011"


async def test_three_obs_tables_exist(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name IN "
                "('sessions','kpi_daily','kpi_muted_user_snapshot')"
            )
        )
        names = {row[0] for row in result}
    assert names == {"sessions", "kpi_daily", "kpi_muted_user_snapshot"}


async def test_sessions_user_time_index_exists(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                "AND tablename='sessions'"
            )
        )
        names = {row[0] for row in result}
    assert "idx_sessions_user_time" in names


async def test_kpi_daily_upsert_roundtrip(db_engine):
    """(metric_key, day)のUPSERTが機能する(主キー衝突でUPDATE)。"""
    from datetime import UTC, date, datetime

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO kpi_daily (metric_key, day, numerator, denominator,"
                " created_at, updated_at) VALUES ('mlr', :day, 1, 2,"
                " :now, :now)"
            ),
            {"day": date(2026, 10, 4), "now": datetime.now(UTC)},
        )
        await conn.execute(
            text(
                "INSERT INTO kpi_daily (metric_key, day, numerator, denominator,"
                " created_at, updated_at) VALUES ('mlr', :day, 3, 4,"
                " :now, :now) ON CONFLICT (metric_key, day) DO UPDATE SET"
                " numerator=3, denominator=4, updated_at=:now"
            ),
            {"day": date(2026, 10, 4), "now": datetime.now(UTC)},
        )
        row = (
            await conn.execute(
                text(
                    "SELECT numerator, denominator FROM kpi_daily"
                    " WHERE metric_key='mlr' AND day=:day"
                ),
                {"day": date(2026, 10, 4)},
            )
        ).first()
    assert (int(row[0]), int(row[1])) == (3, 4)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM kpi_daily WHERE metric_key='mlr' AND day=:day"),
            {"day": date(2026, 10, 4)},
        )
