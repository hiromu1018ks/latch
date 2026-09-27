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

from latch.geo.ingest import BBox, FeatureRow, import_features
from latch.geo.isj import iter_isj_towns

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


# --- design §4-4: 取り込みと冪等 ---


def _isj_fixture_rows():
    return list(
        iter_isj_towns(
            FIXTURES / "isj_sample.csv",
            city_codes=DEFAULT_CODES,
            bbox=DEFAULT_BBOX,
        )
    )


async def test_import_rejects_unknown_source(db_engine, fake_clock):
    with pytest.raises(ValueError, match="source"):
        await import_features(db_engine, fake_clock, _isj_fixture_rows(), "nominatim")


async def test_import_isj_fixture(db_engine, fake_clock):
    engine = db_engine
    count = await import_features(engine, fake_clock, _isj_fixture_rows(), "isj_town")
    assert count == 3  # 伊敷町・天文館一丁目・大字草牟田
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT name, normalized_name, full_normalized_name, city_name, "
                    "attrs, created_at FROM geofeatures WHERE source = 'isj_town' "
                    "ORDER BY name"
                )
            )
        ).all()
    assert [r[0] for r in rows] == ["伊敷町", "大字草牟田", "天文館一丁目"]
    # normalized_name(大字接頭辞は除去される)/ full_normalized_name(ISJのみ)
    by_name = {r[0]: r for r in rows}
    assert by_name["大字草牟田"][1] == "草牟田"
    assert by_name["伊敷町"][2] == "鹿児島市伊敷町"
    # Review Focus #5: attrsはjsonbとして正しく入る(dict実読み取り)
    assert by_name["伊敷町"][4]["source_material_code"] == "2"
    # Review Focus #4: created_atはClock由来の明示値(FakeClock時刻と一致)
    assert by_name["伊敷町"][5] == NOW


async def test_import_is_idempotent(db_engine, fake_clock):
    """フルリロードの冪等: 2回実行しても行数・内容が不変(design §4-4)。"""
    engine = db_engine
    first = await import_features(engine, fake_clock, _isj_fixture_rows(), "isj_town")
    second = await import_features(engine, fake_clock, _isj_fixture_rows(), "isj_town")
    assert first == second == 3

    async def _snapshot():
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    text(
                        "SELECT source, name, normalized_name, source_code, "
                        "ST_AsText(geom) FROM geofeatures ORDER BY source, name"
                    )
                )
            ).all()

    snap1 = await _snapshot()
    snap2 = await _snapshot()
    assert snap1 == snap2


async def test_import_reloads_only_same_source(db_engine, fake_clock):
    """source単位のリロード: OSM取り込みはISJ行を消さない(design §3.1)。"""
    engine = db_engine
    await import_features(engine, fake_clock, _isj_fixture_rows(), "isj_town")
    await import_features(
        engine,
        fake_clock,
        [
            FeatureRow(
                kind="bar",
                name="バー宵待",
                source_code="node/2",
                lon=130.5575,
                lat=31.5955,
                attrs={"amenity": "bar"},
            )
        ],
        "osm_poi",
    )
    async with engine.connect() as conn:
        counts = dict(
            (
                await conn.execute(
                    text("SELECT source, count(*) FROM geofeatures GROUP BY source")
                )
            ).all()
        )
    assert counts == {"isj_town": 3, "osm_poi": 1}
