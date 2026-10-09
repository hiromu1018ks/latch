-- geofeaturesシード(ci実データ7行・2026-10-09エクスポート)
-- 正規化列は geo.normalize.normalize_name と同一規則で計算済み。
INSERT OR REPLACE INTO geofeatures (id, source, kind, name, city_name, pref_name, lon, lat, normalized_name, full_normalized_name) VALUES ('17831', 'isj_town', 'town', '伊敷町', '鹿児島市', '鹿児島県', 130.552, 31.605, '伊敷町', '鹿児島市伊敷町');
INSERT OR REPLACE INTO geofeatures (id, source, kind, name, city_name, pref_name, lon, lat, normalized_name, full_normalized_name) VALUES ('17832', 'isj_town', 'town', '天文館一丁目', '鹿児島市', '鹿児島県', 130.5585, 31.5965, '天文館一丁目', '鹿児島市天文館一丁目');
INSERT OR REPLACE INTO geofeatures (id, source, kind, name, city_name, pref_name, lon, lat, normalized_name, full_normalized_name) VALUES ('17833', 'isj_town', 'town', '大字草牟田', '鹿児島市', '鹿児島県', 130.564, 31.601, '草牟田', '鹿児島市大字草牟田');
INSERT OR REPLACE INTO geofeatures (id, source, kind, name, city_name, pref_name, lon, lat, normalized_name, full_normalized_name) VALUES ('17834', 'osm_poi', 'district', '天文館', NULL, NULL, 130.559, 31.5962, '天文館', NULL);
INSERT OR REPLACE INTO geofeatures (id, source, kind, name, city_name, pref_name, lon, lat, normalized_name, full_normalized_name) VALUES ('17835', 'osm_poi', 'bar', 'バー宵待', NULL, NULL, 130.546, 31.584, 'ばー宵待', NULL);
INSERT OR REPLACE INTO geofeatures (id, source, kind, name, city_name, pref_name, lon, lat, normalized_name, full_normalized_name) VALUES ('17836', 'osm_poi', 'district', '伊敷町', NULL, NULL, 130.5515, 31.6055, '伊敷町', NULL);
INSERT OR REPLACE INTO geofeatures (id, source, kind, name, city_name, pref_name, lon, lat, normalized_name, full_normalized_name) VALUES ('17837', 'osm_poi', 'hotel', '城山ホテル', NULL, NULL, 130.561175, 31.604074999999998, '城山ほてる', NULL);
