"""OSM(pbf/xml)読み取り — 名称付きPOI抽出(design §2.3-A)。

pyosmium(FileProcessor)でローカルファイルを読むのみ(osmiumのネットワーク
機能は使わない — design §1.5)。抽出条件:
  amenity ∈ {bar,pub,biergarten,cafe,restaurant,fast_food,food_court,
              ice_cream,nightclub} / railway=station / highway=bus_stop /
  tourism=hotel / place ∈ {suburb,quarter,neighbourhood}
name(またはname:ja)を持たない要素は除外。geomは node=その点、
way=構成ノード座標の平均(代表点)。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from latch.geo.ingest import BBox, FeatureRow

AMENITY_KINDS = frozenset(
    {
        "bar",
        "pub",
        "biergarten",
        "cafe",
        "restaurant",
        "fast_food",
        "food_court",
        "ice_cream",
        "nightclub",
    }
)
PLACE_KINDS = frozenset({"suburb", "quarter", "neighbourhood"})
# attrsへ保存する主要タグ(design §3.1: OSM=主要タグ)
_MAJOR_TAGS = ("amenity", "railway", "highway", "tourism", "place")


def classify(tags: dict[str, str]) -> str | None:
    """タグ→kind。抽出対象外(None)は呼び出し側で除外する。"""
    amenity = tags.get("amenity")
    if amenity in AMENITY_KINDS:
        return amenity
    if tags.get("railway") == "station":
        return "station"
    if tags.get("highway") == "bus_stop":
        return "bus_stop"
    if tags.get("tourism") == "hotel":
        return "hotel"
    if tags.get("place") in PLACE_KINDS:
        return "district"
    return None


def iter_osm_pois(
    pbf_path: Path | str, *, bbox: BBox | None = None
) -> Iterator[FeatureRow]:
    """OSMファイル(pbf/xml自動判別)から名称付きPOIを列挙する。"""
    import osmium  # geo dependency-group専用(遅延import — design §2.7)

    fp = osmium.FileProcessor(str(pbf_path)).with_locations()
    for obj in fp:
        tags = dict(obj.tags)
        kind = classify(tags)
        if kind is None:
            continue
        name = tags.get("name") or tags.get("name:ja")
        if not name:
            continue  # 名称なし要素は除外(design §2.3)
        if obj.is_node():
            lon, lat = obj.location.lon, obj.location.lat
            element = "node"
        elif obj.is_way():
            coords = [(n.lon, n.lat) for n in obj.nodes if n.location.valid()]
            if not coords:
                continue
            lon = sum(c[0] for c in coords) / len(coords)
            lat = sum(c[1] for c in coords) / len(coords)
            element = "way"
        else:
            continue  # relationは対象外
        if bbox is not None and not bbox.contains(lon, lat):
            continue
        yield FeatureRow(
            kind=kind,
            name=name,
            source_code=f"{element}/{obj.id}",
            lon=lon,
            lat=lat,
            attrs={k: tags[k] for k in _MAJOR_TAGS if k in tags},
        )
