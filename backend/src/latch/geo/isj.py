"""位置参照情報(大字・町丁目レベル)CSV読み取り(design §1.5)。

文字コードはSHIFT-JIS(cp932)が公式形式。UTF-8配信も例外にせず自動判定
(Review Focus #1)。10列固定: 都道府県コード/都道府県名/市区町村コード/
市区町村名/大字町丁目コード/大字町丁目名/緯度/経度/原典資料コード/区分コード。
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from latch.geo.ingest import BBox, FeatureRow


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932")


def _to_float(raw: str) -> float | None:
    value = raw.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def iter_isj_towns(
    csv_path: Path | str,
    *,
    city_codes: set[str] | None = None,
    bbox: BBox | None = None,
) -> Iterator[FeatureRow]:
    """ISJ CSVを読み、フィルタを通る行をFeatureRow(kind='town')として列挙する。

    - フィルタ: 市区町村コードがcity_codesに含まれる かつ 代表点がbbox内(AND)
    - 列数不足行・緯度経度欠損/不正行は例外にせずスキップ(design §4-2)
    """
    text = _decode(Path(csv_path).read_bytes())
    for row in csv.reader(text.splitlines()):
        if len(row) != 10:
            continue  # 列数不足(ヘッダー混入・破損行)
        lat = _to_float(row[6])
        lon = _to_float(row[7])
        name = row[5].strip()
        if lat is None or lon is None or not name:
            continue  # 代表点欠損(代表点を持たない町丁目)
        if city_codes is not None and row[2] not in city_codes:
            continue
        if bbox is not None and not bbox.contains(lon, lat):
            continue
        yield FeatureRow(
            kind="town",
            name=name,
            pref_name=row[1] or None,
            city_name=row[3] or None,
            source_code=row[4],
            lon=lon,
            lat=lat,
            attrs={
                "source_material_code": row[8],
                "town_class_code": row[9],
            },
        )
