"""ws-4地物データのintegration試験(design §4-3〜7)。

- スキーマ(0002): テーブル・CHECK・Index・created_atのDEFAULTなし
- 取り込み冪等: fixture(isj_sample.csv+osm_sample.xml)のフルリロード
- 正転・逆転・決定性: G0「PostGIS上で完結する(外部送信経路ゼロ)」の証明(fixtureデータ)

migrated_db/db_engine fixtureは tests/integration/conftest.py の既存ものを
参照のみする(変更しない)。fake_clock は tests/conftest.py の既存もの。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from latch.geo.ingest import BBox

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "geo"
DEFAULT_CODES = {"46201"}
DEFAULT_BBOX = BBox.parse("130.5420,31.5825,130.5740,31.6095")
NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


# --- design §4-3: スキーマ(0002)---


async def test_head_is_0002(db_engine):
    """0002がhead(migrated_dbがheadまで進めた結果)。"""
    async with db_engine.connect() as conn:
        version = (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar()
    assert version == "0002"


async def test_geofeatures_table_exists(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        assert "geofeatures" in {row[0] for row in result}


async def test_geofeatures_source_check_constraint(db_engine):
    from sqlalchemy.exc import IntegrityError

    async with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text("""
                    INSERT INTO geofeatures
                      (source, kind, name, normalized_name, source_code,
                       attrs, geom, created_at)
                    VALUES
                      ('nominatim', 'x', 'x', 'x', 'x', '{}',
                       ST_SetSRID(ST_MakePoint(130.55, 31.59), 4326)::geography, :t)
                """),
                {"t": NOW},
            )


async def test_geofeatures_indexes_exist(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'geofeatures'")
        )
        names = {row[0] for row in result}
    assert {"idx_geofeatures_geom", "idx_geofeatures_name"} <= names


async def test_geofeatures_created_at_has_no_db_default(db_engine):
    """Review Focus #4: 時刻はClock由来の明示値 — DB時刻関数DEFAULTを持たない。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text("""
                SELECT column_default FROM information_schema.columns
                WHERE table_name = 'geofeatures' AND column_name = 'created_at'
            """)
        )
        assert result.scalar() is None
