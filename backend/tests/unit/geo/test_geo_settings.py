"""geo設定3項目(design §2.6)。エリアは設定で与える(T2確定後は値変更+再取り込み)。"""

from latch.settings import Settings

GEO_ENV_VARS = ("LATCH_GEO_AREA_NAME", "LATCH_GEO_ISJ_CITY_CODES", "LATCH_GEO_OSM_BBOX")


def _clean_settings(monkeypatch, **overrides) -> Settings:
    for var in GEO_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(**overrides)


def test_geo_settings_defaults(monkeypatch):
    s = _clean_settings(monkeypatch)
    assert s.geo_area_name == "ci-provisional"
    assert s.geo_isj_city_codes == "46201"
    # ci暫定エリア=鹿児島市天文館周辺約3km四方(design §2.6。
    # 中心約(130.558,31.596)±約1.5km)
    assert s.geo_osm_bbox == "130.5420,31.5825,130.5740,31.6095"


def test_geo_settings_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_GEO_ISJ_CITY_CODES", "47201")
    monkeypatch.setenv("LATCH_GEO_OSM_BBOX", "127.0,26.0,128.0,27.0")
    s = Settings()
    assert s.geo_isj_city_codes == "47201"
    assert s.geo_osm_bbox == "127.0,26.0,128.0,27.0"


def test_geo_settings_are_exactly_three_fields(monkeypatch):
    # エリア差し替え(T2後)は設定値変更+再取り込みで済む構造の検査。
    # geo系設定がこの3項目のみであることを機械検査する(過剰な設定項目を防ぐ)。
    _clean_settings(monkeypatch)
    geo_fields = {f for f in Settings.model_fields if f.startswith("geo_")}
    assert geo_fields == {"geo_area_name", "geo_isj_city_codes", "geo_osm_bbox"}
