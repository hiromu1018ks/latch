"""地物のDB取り込み(design §3.1)。

FeatureRow=取り込み行のモデル(ISJ/OSM共通)、BBox=エリアフィルタ(design §2.6)。
import_featuresはTask 5で追加する(フルリロード: DELETE→一括INSERT)。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock


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


async def import_features(
    engine: AsyncEngine,
    clock: Clock,
    rows: Iterable[FeatureRow],
    source: str,
) -> int:
    """sourceの行をフルリロードして件数を返す(Task 5で実装)。"""
    raise NotImplementedError("Task 5で実装")
