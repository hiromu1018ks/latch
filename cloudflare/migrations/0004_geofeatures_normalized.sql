-- geofeatures正規化名列(本体実装・2026-10-09)
--
-- PostgreSQL版(alembic 0002)の正転照合列をD1へ追加する。
-- 値は取り込み時と同じ geo.normalize.normalize_name で計算する:
--   normalized_name      = normalize_name(name)
--   full_normalized_name = normalize_name(city_name || name) -- city_nameはNULL可
-- (LocalD1Database.migrate() は *.sql を名前順で適用するため既存環境にも冪等)

ALTER TABLE geofeatures ADD COLUMN normalized_name TEXT;
ALTER TABLE geofeatures ADD COLUMN full_normalized_name TEXT;
CREATE INDEX IF NOT EXISTS idx_geofeatures_normalized ON geofeatures (normalized_name);
