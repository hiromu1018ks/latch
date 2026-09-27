"""正転・逆転ジオコーディング(04 第3節・08 D-11)。PostGIS上のSQLのみ —
位置情報を外部サービスへ送る経路を持たない(geo配下network import禁止の
arch testが強制)。

- 正転: normalize_name(name) で normalized_name / full_normalized_name へ
  完全一致。候補は osm_poi優先→id昇順 の決定的順位で1件(design §2.4)
- 逆転: 約1kmグリッド丸め(3857でST_SnapToGrid)→代表点に最も近い地物→
  「市区町村名+地物名」(design §2.5)。OSM POIは市区町村名を第2クエリで補完
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.geo.normalize import normalize_name

# 約1kmグリッド丸め(04 第3節「投影座標系へ落として丸め」)。原点(0,0)基準で一意。
_GRID_CTE = """
    WITH input AS (SELECT ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) AS g),
    grid AS (
      SELECT ST_Transform(
               ST_SnapToGrid(ST_Transform(input.g, 3857), 1000.0), 4326) AS g
      FROM input
    )
"""

_FORWARD_SQL = text("""
    SELECT source, kind, name, city_name, pref_name,
           ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat
    FROM geofeatures
    WHERE normalized_name = :n OR full_normalized_name = :n
    ORDER BY CASE source WHEN 'osm_poi' THEN 0 ELSE 1 END, id
    LIMIT 1
""")

_NEAREST_SQL = text(
    _GRID_CTE
    + """
    SELECT source, kind, name, city_name
    FROM geofeatures, grid
    ORDER BY ST_Distance(geofeatures.geom, grid.g::geography),
             CASE source WHEN 'osm_poi' THEN 0 ELSE 1 END,
             id
    LIMIT 1
"""
)

_NEAREST_ISJ_CITY_SQL = text(
    _GRID_CTE
    + """
    SELECT city_name
    FROM geofeatures, grid
    WHERE source = 'isj_town' AND city_name IS NOT NULL
    ORDER BY ST_Distance(geofeatures.geom, grid.g::geography), id
    LIMIT 1
"""
)


class Geofeature(BaseModel):
    """正転の結果(geo_centerの供給源。M1がintents.geo_centerへ格納する)。"""

    source: Literal["isj_town", "osm_poi"]
    kind: str
    name: str
    city_name: str | None
    pref_name: str | None
    lon: float
    lat: float


class GeoService:
    """正転・逆転(04 第3節)。外部送信経路を持たない(SQLのみ)。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def geocode_forward(self, name: str) -> Geofeature | None:
        """地名→代表点。該当なし=None(M1が422 GEOCODING_FAILEDへ写像)。"""
        normalized = normalize_name(name)
        if not normalized:
            return None
        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(_FORWARD_SQL, {"n": normalized})).mappings().first()
            )
        if row is None:
            return None
        return Geofeature(**row)

    async def reverse_geocode(self, lon: float, lat: float) -> str | None:
        """座標→地域名(「市区町村名+地物名」。08 D-11)。地物なし=None。"""
        params = {"lon": lon, "lat": lat}
        async with self._engine.connect() as conn:
            nearest = (await conn.execute(_NEAREST_SQL, params)).mappings().first()
            if nearest is None:
                return None
            city_name = nearest["city_name"]
            if city_name is None:
                # OSM POIは市区町村名を持たない → 同じ代表点に最も近いISJ町丁目の
                # 市区町村名で補完(design §2.5)。ISJが無い場合のみname単独
                city_row = (
                    (await conn.execute(_NEAREST_ISJ_CITY_SQL, params))
                    .mappings()
                    .first()
                )
                city_name = city_row["city_name"] if city_row is not None else None
        if city_name:
            return f"{city_name}{nearest['name']}"
        return nearest["name"]
