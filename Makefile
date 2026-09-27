.PHONY: setup up down ps logs lint test test-ci migrate geo-download geo-import geo-verify

setup: ## uv依存の導入
	cd backend && uv sync

up: ## ci常設環境を起動(healthy待ち)
	docker compose up -d --wait

down: ## ci常設環境を停止(ボリューム保持)
	docker compose down

ps: ## ci常設環境の状態
	docker compose ps

logs: ## ci常設環境のログ(follow)
	docker compose logs -f

lint: ## ruff(format検査+lint)
	cd backend && uv run ruff format --check . && uv run ruff check .

test: ## unit試験(毎コミットの規律)
	cd backend && uv run pytest -m "not integration"

test-ci: ## ci環境試験(compose起動 → unit+integration。--group geoでosmium込み)
	docker compose up -d --wait
	cd backend && uv run --group geo pytest

migrate: ## DBマイグレーションをheadまで適用(明示実行。API/Workerの起動時自動実行はしない)
	cd backend && uv run alembic upgrade head

geo-download: ## 地物データ取得(OSM九州extractをcurl。ISJ入手先と手順を表示)
	mkdir -p backend/data/geo
	curl -L -C - -o backend/data/geo/kyushu-latest.osm.pbf https://download.geofabrik.de/asia/japan/kyushu-latest.osm.pbf
	@echo "ISJ(大字・町丁目レベル)は下記から手動取得し backend/data/geo/ へ配置:"
	@echo "  https://nlftp.mlit.go.jp/cgi-bin/isj/dls/_choose_method.cgi (都道府県単位のzip→解凍したCSV)"

geo-import: ## settingsのエリア設定でISJ+OSMをPostGISへ取り込み(CSV/PBFはgeo-downloadの配置先)
	cd backend && uv run --group geo python -m latch.geo import-isj --csv data/geo/isj.csv
	cd backend && uv run --group geo python -m latch.geo import-osm --pbf data/geo/kyushu-latest.osm.pbf

geo-verify: ## 正転・逆転のサンプル確認(PostGIS完結の動作確認・座標は出さない)
	cd backend && uv run --group geo python -m latch.geo verify
