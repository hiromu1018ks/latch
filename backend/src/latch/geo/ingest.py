"""地物のDB取り込み(design §3.1)。

FeatureRow=取り込み行のモデル(ISJ/OSM共通)、BBox=エリアフィルタ(design §2.6)。
import_featuresはTask 5で追加する(フルリロード: DELETE→一括INSERT)。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.geo.normalize import normalize_name


class FeatureRow(BaseModel):
    """geofeaturesへ入る地物1行(代表点+表示名+系統固有の補助列)。"""

    kind: str  # isj_town='town' / osm=タグ由来('restaurant'等・place系='district')
    name: str  # 表示名(ISJ=大字町丁目名、OSM=name)
    pref_name: str | None = None  # ISJ由来(OSMはNone)
    city_name: str | None = None  # 同上
    source_code: str  # ISJ=大字町丁目コード / OSM="node/12345" 等
    lon: float
    lat: float
    attrs: dict[str, str] = {}  # ISJ=原典/区分コード / OSM=主要タグ


@dataclass(frozen=True)
class BBox:
    """エリアの外接矩形(design §2.6)。OSM bboxとISJ代表点フィルタの共通基準。"""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @classmethod
    def parse(cls, raw: str) -> BBox:
        """'min_lon,min_lat,max_lon,max_lat' 文字列を構築。形式破損はValueError。"""
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError(f"bbox must be 'min_lon,min_lat,max_lon,max_lat': {raw!r}")
        try:
            min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
        except ValueError as exc:
            raise ValueError(f"bbox values must be numbers: {raw!r}") from exc
        if not (-180 <= min_lon < max_lon <= 180) or not (
            -90 <= min_lat < max_lat <= 90
        ):
            raise ValueError(f"invalid bbox range: {raw!r}")
        return cls(min_lon, min_lat, max_lon, max_lat)

    def contains(self, lon: float, lat: float) -> bool:
        return (
            self.min_lon <= lon <= self.max_lon and self.min_lat <= lat <= self.max_lat
        )


SOURCES = ("isj_town", "osm_poi")

_INSERT = """
    INSERT INTO geofeatures
      (source, kind, name, normalized_name, full_normalized_name, pref_name,
       city_name, source_code, attrs, geom, created_at)
    VALUES
      (:source, :kind, :name, :normalized_name, :full_normalized_name, :pref_name,
       :city_name, :source_code, CAST(:attrs AS jsonb),
       ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :created_at)
"""


async def import_features(
    engine: AsyncEngine,
    clock: Clock,
    rows: Iterable[FeatureRow],
    source: str,
) -> int:
    """sourceの行をフルリロード(DELETE WHERE source=... → 一括INSERT)し件数を返す。

    冪等: 同一入力を再実行しても同一状態になる(設計§4-4で試験)。
    normalized_nameはここで計算(design §2.4 — 入力と同じ関数)。
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source!r} (must be one of {SOURCES})")
    created_at = clock.now()
    params = [
        {
            "source": source,
            "kind": row.kind,
            "name": row.name,
            "normalized_name": normalize_name(row.name),
            "full_normalized_name": (
                normalize_name(f"{row.city_name}{row.name}") if row.city_name else None
            ),
            "pref_name": row.pref_name,
            "city_name": row.city_name,
            "source_code": row.source_code,
            "attrs": json.dumps(row.attrs, ensure_ascii=False),
            "lon": row.lon,
            "lat": row.lat,
            "created_at": created_at,
        }
        for row in rows
    ]
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM geofeatures WHERE source = :source"), {"source": source}
        )
        if params:
            await conn.execute(text(_INSERT), params)
    return len(params)
