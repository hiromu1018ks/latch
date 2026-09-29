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
from latch.geo.osm import iter_osm_pois
from latch.geo.service import GeoService

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "geo"
DEFAULT_CODES = {"46201"}
DEFAULT_BBOX = BBox.parse("130.5420,31.5825,130.5740,31.6095")
NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


# --- design §4-3: スキーマ(head追従)---


async def test_head_is_0005(db_engine):
    """0005がhead(migrated_dbがheadまで進めた結果・M2 ws-7で0005追加)。"""
    async with db_engine.connect() as conn:
        version = (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar()
    assert version == "0005"


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


# --- design §4-5: 正転(fixture ISJ取り込み後。期待値は名称照合なので座標非依存) ---


async def _import_isj(db_engine, fake_clock):
    return await import_features(db_engine, fake_clock, _isj_fixture_rows(), "isj_town")


async def test_forward_isj_town_name(db_engine, fake_clock):
    await _import_isj(db_engine, fake_clock)
    hit = await GeoService(db_engine).geocode_forward("伊敷町")
    assert hit is not None
    assert hit.source == "isj_town"
    assert hit.kind == "town"
    assert hit.city_name == "鹿児島市"
    assert hit.pref_name == "鹿児島県"
    assert hit.lon == pytest.approx(130.552)
    assert hit.lat == pytest.approx(31.605)


async def test_forward_full_city_name(db_engine, fake_clock):
    """市区町村名連結(「鹿児島市○○」形式)でもfull_normalized_name経由でヒット。"""
    await _import_isj(db_engine, fake_clock)
    hit = await GeoService(db_engine).geocode_forward("鹿児島市天文館一丁目")
    assert hit is not None
    assert hit.name == "天文館一丁目"


async def test_forward_normalizes_input(db_engine, fake_clock):
    """入力の表記ゆれ(算用数字丁目)は正規化で吸収される(design §2.4)。"""
    await _import_isj(db_engine, fake_clock)
    hit = await GeoService(db_engine).geocode_forward("天文館1丁目")
    assert hit is not None
    assert hit.name == "天文館一丁目"


async def test_forward_missing_returns_none(db_engine, fake_clock):
    """該当なし=None。M1が422 GEOCODING_FAILEDへ写像する(05 第5節)。"""
    await _import_isj(db_engine, fake_clock)
    assert await GeoService(db_engine).geocode_forward("存在しない町") is None


# --- design §4-6: 逆転(期待値を決定的にするため地物2行を直構築・遠隔配置) ---

_REVERSE_ROWS = [
    FeatureRow(
        kind="town",
        name="西之段町",
        pref_name="鹿児島県",
        city_name="鹿児島市",
        source_code="46201007001",
        lon=130.5450,
        lat=31.5850,
        attrs={},
    ),
    FeatureRow(
        kind="town",
        name="東之段町",
        pref_name="鹿児島県",
        city_name="鹿児島市",
        source_code="46201008001",
        lon=130.5720,
        lat=31.6080,
        attrs={},
    ),
]


async def _import_reverse_rows(db_engine, fake_clock):
    # 2行のみの状態を作る(osm_poiも消す — 試験ごとに状態を構築する原則)
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM geofeatures"))
    return await import_features(db_engine, fake_clock, _REVERSE_ROWS, "isj_town")


async def test_reverse_returns_city_plus_name(db_engine, fake_clock):
    """座標→「市区町村名+地物名」のarea_name(08 D-11の形式)。"""
    await _import_reverse_rows(db_engine, fake_clock)
    svc = GeoService(db_engine)
    # 入力=地物そのものの座標。代表点(≤707m)への最近傍はもう一方(約3.6km先)ではない
    assert await svc.reverse_geocode(130.5450, 31.5850) == "鹿児島市西之段町"
    assert await svc.reverse_geocode(130.5720, 31.6080) == "鹿児島市東之段町"


async def test_reverse_is_deterministic(db_engine, fake_clock):
    """同一入力の反復実行で同一結果(決定性 — design §2.5)。"""
    await _import_reverse_rows(db_engine, fake_clock)
    svc = GeoService(db_engine)
    results = [await svc.reverse_geocode(130.5500, 31.5900) for _ in range(3)]
    assert len(set(results)) == 1


async def test_reverse_grid_snap_within_1km_cell(db_engine, fake_clock):
    """Review Focus #3: 丸めは3857へ落として約1km — 代表点と入力の距離は
    1kmセルの対角の半分(≈707m)以内。4326のまま丸めると≈111km刻みになる。"""
    await _import_reverse_rows(db_engine, fake_clock)
    async with db_engine.connect() as conn:
        distance_m = (
            await conn.execute(
                text("""
                    WITH input AS (
                      SELECT ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) AS g),
                    grid AS (
                      SELECT ST_Transform(ST_SnapToGrid(
                        ST_Transform(input.g, 3857), 1000.0), 4326) AS g FROM input
                      )
                    SELECT ST_Distance(grid.g::geography, input.g::geography)
                    FROM input, grid
                """),
                {"lon": 130.5500, "lat": 31.5900},
            )
        ).scalar()
    assert distance_m is not None and distance_m < 750.0


async def test_reverse_grid_cell_points_share_result(db_engine, fake_clock):
    """(b) 同一の1kmグリッドセルに属す2点が同一のarea_name(design §4-6)。
    入力Pとその代表点R=snap(P)は同じセルに属し、共通の代表点を持つ
    (代表点自身は丸めで不動)→ 逆転結果も一致する。"""
    await _import_reverse_rows(db_engine, fake_clock)
    async with db_engine.connect() as conn:
        snapped = (
            await conn.execute(
                text("""
                    WITH input AS (
                      SELECT ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) AS g),
                    grid AS (
                      SELECT ST_Transform(ST_SnapToGrid(
                        ST_Transform(input.g, 3857), 1000.0), 4326) AS g FROM input
                      )
                    SELECT ST_X(grid.g::geometry), ST_Y(grid.g::geometry) FROM grid
                """),
                {"lon": 130.5500, "lat": 31.5900},
            )
        ).first()
    assert snapped is not None
    svc = GeoService(db_engine)
    from_point = await svc.reverse_geocode(130.5500, 31.5900)
    from_representative = await svc.reverse_geocode(snapped[0], snapped[1])
    assert from_point is not None
    assert from_point == from_representative


async def test_reverse_empty_table_returns_none(db_engine):
    """地物なし(未取り込み)=None(design §2.5)。"""
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM geofeatures"))
    assert await GeoService(db_engine).reverse_geocode(130.5585, 31.5965) is None


# --- design §4(OSM読み取り)+ §4-5(a)(e) ---


def _osm_fixture_rows():
    return list(iter_osm_pois(FIXTURES / "osm_sample.xml", bbox=DEFAULT_BBOX))


async def test_osm_fixture_rows_extracted():
    rows = _osm_fixture_rows()
    by_code = {r.source_code: r for r in rows}
    # 名称ありPOI+bbox内のみ(名称なし・東京駅は除外)
    assert set(by_code) == {"node/1", "node/2", "node/5", "way/10"}
    assert by_code["node/1"].kind == "district"
    assert by_code["node/1"].name == "天文館"
    assert by_code["node/2"].kind == "bar"
    assert by_code["node/5"].kind == "district"
    # 主要タグがattrsへ入る(Review Focus #5と同型の実読み取り)
    assert by_code["node/2"].attrs == {"amenity": "bar"}


async def test_osm_way_representative_point_is_node_average():
    row = {r.source_code: r for r in _osm_fixture_rows()}["way/10"]
    # wayの代表点=構成ノード座標の平均(閉ウェイの重複ノード含む — design §2.3)
    assert row.lon == pytest.approx((130.5610 + 130.5615 + 130.5612 + 130.5610) / 4)
    assert row.lat == pytest.approx((31.6040 + 31.6042 + 31.6041 + 31.6040) / 4)


async def test_forward_osm_poi(db_engine, fake_clock):
    """(a) osm_poiの名称がヒットし座標が返る(design §4-5)。"""
    await _import_isj(db_engine, fake_clock)
    await import_features(db_engine, fake_clock, _osm_fixture_rows(), "osm_poi")
    hit = await GeoService(db_engine).geocode_forward("バー宵待")
    assert hit is not None
    assert hit.source == "osm_poi"
    assert hit.kind == "bar"
    assert hit.lon == pytest.approx(130.5460)
    assert hit.lat == pytest.approx(31.5840)


async def test_forward_same_name_prefers_osm(db_engine, fake_clock):
    """(e) POIと町丁目の同名ではosm_poiが選ばれる(決定的順位 — design §2.4)。"""
    await _import_isj(db_engine, fake_clock)
    await import_features(db_engine, fake_clock, _osm_fixture_rows(), "osm_poi")
    hit = await GeoService(db_engine).geocode_forward("伊敷町")
    assert hit is not None
    assert hit.source == "osm_poi"
    assert hit.kind == "district"


async def test_reverse_osm_poi_city_complemented(db_engine, fake_clock):
    """逆転でOSM POIが最近傍のとき、市区町村名をISJから補完(design §2.5)。
    「バー宵待」(130.5460, 31.5840)の入力に対し、グリッド丸め(≤707m移動)を
    経ても最近傍がバー宵待のままになるよう、他地物は≥約1.8km離して配置。"""
    await _import_isj(db_engine, fake_clock)
    await import_features(db_engine, fake_clock, _osm_fixture_rows(), "osm_poi")
    area = await GeoService(db_engine).reverse_geocode(130.5460, 31.5840)
    assert area is not None
    assert area == "鹿児島市バー宵待"
