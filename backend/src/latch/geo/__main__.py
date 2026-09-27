"""地物データ取り込みCLI(design §2.2-A・§3.1)。

実行形態はalembic migrateと同じ「ホストのuvからcompose常設DB(127.0.0.1:5432)
へ接続」パターン。Makeターゲット(geo-download/geo-import/geo-verify)から
 `uv run --group geo python -m latch.geo ...` で起動する。

出力規律(08 第2.4節・design §6-5): verifyは名称・市区町村名・件数のみ出力し、
座標は出さない(出力の最小化に統一)。
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from latch.core.clock import SystemClock
from latch.core.db import create_db_engine
from latch.geo.ingest import BBox, import_features
from latch.geo.isj import iter_isj_towns
from latch.geo.service import GeoService
from latch.settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.geo",
        description="地物データの取り込みとジオコーディング確認(ws-4)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_isj = sub.add_parser("import-isj", help="位置参照情報CSVを取り込み")
    p_isj.add_argument("--csv", required=True, help="ISJ CSVパス(cp932/utf-8自動判定)")
    p_isj.add_argument(
        "--city-codes", default=None, help="市区町村コード(カンマ区切り)"
    )
    p_isj.add_argument("--bbox", default=None, help="min_lon,min_lat,max_lon,max_lat")

    p_osm = sub.add_parser("import-osm", help="OSM(pbf/xml)の名称付きPOIを取り込み")
    p_osm.add_argument("--pbf", required=True, help=".osm.pbf / .osm ファイルパス")
    p_osm.add_argument("--bbox", default=None, help="min_lon,min_lat,max_lon,max_lat")

    p_verify = sub.add_parser("verify", help="正転・逆転のサンプル確認(座標は出さない)")
    p_verify.add_argument("--forward", default="天文館", help="正転する地名")
    p_verify.add_argument(
        "--reverse",
        nargs=2,
        type=float,
        metavar=("LON", "LAT"),
        default=None,
        help="逆転する座標(経度 緯度)",
    )
    return parser


def _effective_area(
    args: argparse.Namespace, settings: Settings
) -> tuple[set[str], BBox]:
    """CLI引数 → エリア設定へ解決(省略時はsettings既定値=ci暫定エリア)。"""
    raw_codes = getattr(args, "city_codes", None) or settings.geo_isj_city_codes
    city_codes = {c.strip() for c in raw_codes.split(",") if c.strip()}
    bbox = BBox.parse(getattr(args, "bbox", None) or settings.geo_osm_bbox)
    return city_codes, bbox


async def _run_import_isj(args: argparse.Namespace, settings: Settings) -> int:
    city_codes, bbox = _effective_area(args, settings)
    rows = list(iter_isj_towns(args.csv, city_codes=city_codes, bbox=bbox))
    engine = create_db_engine(settings)
    try:
        count = await import_features(engine, SystemClock(), rows, "isj_town")
    finally:
        await engine.dispose()
    print(f"[import-isj] area={settings.geo_area_name} isj_town: {count} rows")
    return 0


async def _run_import_osm(args: argparse.Namespace, settings: Settings) -> int:
    from latch.geo.osm import iter_osm_pois  # 遅延import(osmiumはgeoグループ専用)

    _, bbox = _effective_area(args, settings)
    rows = list(iter_osm_pois(args.pbf, bbox=bbox))
    engine = create_db_engine(settings)
    try:
        count = await import_features(engine, SystemClock(), rows, "osm_poi")
    finally:
        await engine.dispose()
    print(f"[import-osm] area={settings.geo_area_name} osm_poi: {count} rows")
    return 0


async def _run_verify(args: argparse.Namespace, settings: Settings) -> int:
    engine = create_db_engine(settings)
    try:
        async with engine.connect() as conn:
            counts = (
                await conn.execute(
                    text("SELECT source, count(*) FROM geofeatures GROUP BY source")
                )
            ).all()
        print(f"[verify] area={settings.geo_area_name}")
        for source, count in sorted(counts):
            print(f"  {source}: {count} rows")
        service = GeoService(engine)
        hit = await service.geocode_forward(args.forward)
        if hit is None:
            print(f"  forward {args.forward!r}: -> None (422 GEOCODING_FAILED相当)")
        else:
            city = hit.city_name or ""
            print(
                f"  forward {args.forward!r}: -> {city}{hit.name} "
                f"({hit.source}/{hit.kind})"
            )
        if args.reverse is not None:
            lon, lat = args.reverse
            area = await service.reverse_geocode(lon, lat)
            print(f"  reverse: -> {area!r}")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    if args.command == "import-isj":
        return asyncio.run(_run_import_isj(args, settings))
    if args.command == "import-osm":
        return asyncio.run(_run_import_osm(args, settings))
    return asyncio.run(_run_verify(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
