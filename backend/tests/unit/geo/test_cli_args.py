"""CLI引数解析とsettings既定値の解決(design §4-7)。

サブコマンド実行そのもの(実DB接続)はスーパーバイザーによる make geo-import /
geo-verify 実行で確認する — ここでは引数解析とエリア解決の純函数部分のみ。
"""

import pytest

from latch.geo.__main__ import _effective_area, build_parser
from latch.geo.ingest import BBox
from latch.settings import Settings

GEO_ENV_VARS = ("LATCH_GEO_AREA_NAME", "LATCH_GEO_ISJ_CITY_CODES", "LATCH_GEO_OSM_BBOX")


def _settings(monkeypatch) -> Settings:
    for var in GEO_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings()


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


def test_subcommand_is_required(capsys):
    with pytest.raises(SystemExit):
        _parse([])


def test_import_isj_args():
    args = _parse(["import-isj", "--csv", "data/geo/isj.csv"])
    assert args.command == "import-isj"
    assert args.csv == "data/geo/isj.csv"
    assert args.city_codes is None
    assert args.bbox is None


def test_import_isj_explicit_overrides():
    args = _parse(
        [
            "import-isj",
            "--csv",
            "x.csv",
            "--city-codes",
            "46201,46202",
            "--bbox",
            "1.0,2.0,3.0,4.0",
        ]
    )
    assert args.city_codes == "46201,46202"
    assert args.bbox == "1.0,2.0,3.0,4.0"


def test_import_osm_args():
    args = _parse(["import-osm", "--pbf", "data/geo/kyushu-latest.osm.pbf"])
    assert args.command == "import-osm"
    assert args.pbf == "data/geo/kyushu-latest.osm.pbf"
    assert args.bbox is None


def test_verify_args_defaults():
    args = _parse(["verify"])
    assert args.command == "verify"
    assert args.forward == "天文館"
    assert args.reverse is None


def test_verify_reverse_takes_lon_lat():
    args = _parse(["verify", "--reverse", "130.5585", "31.5965"])
    assert args.reverse == [130.5585, 31.5965]


def test_effective_area_defaults_to_settings(monkeypatch):
    # Review Focus #2: 引数省略時はsettings既定値(ci暫定エリア)へ解決される
    args = _parse(["import-isj", "--csv", "x.csv"])
    city_codes, bbox = _effective_area(args, _settings(monkeypatch))
    assert city_codes == {"46201"}
    assert bbox == BBox.parse("130.5420,31.5825,130.5740,31.6095")


def test_effective_area_args_override_settings(monkeypatch):
    args = _parse(["import-osm", "--pbf", "x.pbf", "--bbox", "127.0,26.0,128.0,27.0"])
    city_codes, bbox = _effective_area(args, _settings(monkeypatch))
    assert city_codes == {"46201"}  # osm側は未指定 → settings既定
    assert bbox == BBox.parse("127.0,26.0,128.0,27.0")


def test_effective_area_rejects_bad_bbox(monkeypatch):
    # Review Focus #2: 形式破損はValueErrorで即失敗(黙って続けない)
    args = _parse(["import-osm", "--pbf", "x.pbf", "--bbox", "broken"])
    with pytest.raises(ValueError, match="bbox"):
        _effective_area(args, _settings(monkeypatch))


def test_public_api_reexports():
    import latch.geo as api

    for name in (
        "GeoService",
        "Geofeature",
        "FeatureRow",
        "BBox",
        "SOURCES",
        "import_features",
        "normalize_name",
        "iter_isj_towns",
    ):
        assert getattr(api, name, None) is not None, name


def test_effective_area_rejects_non_numeric_city_code(monkeypatch):
    # Review Focus #2(市コード側): 形式破損は明確なValueErrorで即失敗。
    # タイポが通ると0行フルリロード(既存isj_town行のDELETEを含む)が黙って成功する
    args = _parse(["import-isj", "--csv", "x.csv", "--city-codes", "4620l,46202"])
    with pytest.raises(ValueError, match="city"):
        _effective_area(args, _settings(monkeypatch))


def test_effective_area_rejects_empty_city_codes(monkeypatch):
    # カンマ・空白のみの指定は空セット=全行除外になるためValueError
    args = _parse(["import-isj", "--csv", "x.csv", "--city-codes", ","])
    with pytest.raises(ValueError, match="city"):
        _effective_area(args, _settings(monkeypatch))
