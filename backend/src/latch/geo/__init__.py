"""地物データ取り込み・ジオコーディング(M0 ws-4)。PostGIS上で完結(外部送信経路ゼロ)。

M1(保存API)とM2(Layer 5)はこのパッケージ越しにGeoService等を利用する
(design §3.1)。iter_osm_poisのみosmium(geoグループ)依存のため再exportしない
— 必要な場合は latch.geo.osm から直接importすること。
"""

from latch.geo.ingest import SOURCES, BBox, FeatureRow, import_features
from latch.geo.isj import iter_isj_towns
from latch.geo.normalize import normalize_name
from latch.geo.service import Geofeature, GeoService

__all__ = [
    "BBox",
    "FeatureRow",
    "Geofeature",
    "GeoService",
    "SOURCES",
    "import_features",
    "iter_isj_towns",
    "normalize_name",
]
