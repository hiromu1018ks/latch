"""D1版ジオコーディング(geofeatures表・本体実装)。

正転は geo/normalize.py(純関数)で正規化した列(normalized_name /
full_normalized_name — 0004で追加)へ完全一致・osm_poi優先→id昇順の
決定的順位で1件(参照実装GeoService.geocode_forwardと同一規則)。
逆転は約1kmグリッド丸め(3857相当のWeb Mercator)→ 代表点に最も近い地物
→「市区町村名+地物名」(距離はdomain/geo_distanceのWGS84測地線)。
"""

from __future__ import annotations

import math

from latch.geo.normalize import normalize_name

from latch_worker.gjson import loads_json

_M_PER_DEGREE_EQ = 111_319.49  # 赤道1度あたりのメートル(概算・丸め用途)


class D1GeoService:
    """正転・逆転(geofeatures D1読取のみ・外部送信なし)。"""

    def __init__(self, db) -> None:
        self._db = db

    async def geocode_forward(self, name: str):
        """地名→代表点(Geofeature互換)。該当なし=None。"""
        normalized = normalize_name(name)
        if not normalized:
            return None
        row = (
            await self._db.prepare(
                "SELECT id, source, kind, name, city_name, pref_name, lon, lat"
                " FROM geofeatures"
                " WHERE normalized_name = ? OR full_normalized_name = ?"
                " ORDER BY CASE source WHEN 'osm_poi' THEN 0 ELSE 1 END, id"
                " LIMIT 1"
            )
            .bind(normalized, normalized)
            .first()
        )
        if row is None:
            return None
        from latch.geo.service import Geofeature

        return Geofeature(
            source=row["source"],
            kind=row["kind"],
            name=row["name"],
            city_name=row["city_name"],
            pref_name=row["pref_name"],
            lon=float(row["lon"]),
            lat=float(row["lat"]),
        )

    async def reverse_geocode(self, lon: float, lat: float) -> str | None:
        """座標→地域名(「市区町村名+地物名」)。地物なし=None。

        参照実装は3857へ落として1kmグリッド丸め→最近傍。ここでは同等の
        Web Mercator丸めをPythonで行い、距离はWGS84測地線(Vincenty)。
        """
        glon, glat = _snap_grid_1km(lon, lat)
        rows = (
            await self._db.prepare(
                "SELECT source, kind, name, city_name, lon, lat FROM geofeatures"
            )
            .all()
        )
        if not rows:
            return None
        from latch.domain.geo_distance import geodesic_distance_m

        def dist(r) -> float:
            return geodesic_distance_m(glon, glat, float(r["lon"]), float(r["lat"]))

        best = min(
            rows,
            key=lambda r: (
                dist(r),
                0 if r["source"] == "osm_poi" else 1,
                str(r["name"]),
            ),
        )
        city_name = best["city_name"]
        if city_name is None:
            # OSM POIは市区町村名を持たない → 最近傍ISJ町丁目の市区町村名で補完
            isj = [r for r in rows if r["source"] == "isj_town" and r["city_name"]]
            if isj:
                city_name = min(
                    isj,
                    key=lambda r: (dist(r), str(r["name"])),
                )["city_name"]
        name = best["name"]
        if city_name:
            return f"{city_name}{name}"
        return name


def _snap_grid_1km(lon: float, lat: float) -> tuple[float, float]:
    """約1kmグリッド丸め(Web Mercator相当・原点基準)。

    Web Mercator: x = lon(度), y = ln(tan(45°+lat/2)) を度換算した空間で
    1000m単位へ丸めてから緯度経度へ戻す(ST_SnapToGrid(3857, 1000)相当)。
    """
    lat = max(min(lat, 85.05112878), -85.05112878)
    # メートル空間(3857)
    mx = lon * _M_PER_DEGREE_EQ
    my = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * _M_PER_DEGREE_EQ
    sx = round(mx / 1000.0) * 1000.0
    sy = round(my / 1000.0) * 1000.0
    glon = sx / _M_PER_DEGREE_EQ
    gy = sy / _M_PER_DEGREE_EQ
    glat = math.degrees(2 * math.atan(math.exp(gy / _M_PER_DEGREE_EQ)) - math.pi / 2)
    return glon, glat
