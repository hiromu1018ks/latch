.PHONY: setup up down ps logs lint test test-ci migrate geo-download geo-import geo-verify g1-gate embed-smoke jev-smoke g2-gate staging-up staging-down staging-ps staging-logs staging-migrate staging-geo-import staging-keys staging-token staging-clock staging-publish staging-reset staging-test staging-obs-test load-up load-down load-ps seed-generate seed-verify dashboard-up dashboard-down dashboard-ps dashboard-logs loadusers loadperf loadbf loadmass load-stub-flat load-migrate load-geo-import staging-nfr-reset staging-nfr-test staging-probe staging-d16 staging-dump staging-db2-up staging-restore staging-db2-switch staging-db2-down

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

test-ci: ## ci環境試験(worker停止→unit+integration→subscription掃除→worker復帰。--group geoでosmium込み)
	docker compose up -d --wait
	docker compose stop worker
	cd backend && uv run --group geo pytest; rc=$$?; \
	LATCH_PUBSUB_EMULATOR_HOST=127.0.0.1:8085 uv run python -m latch.events purge-match-sub; docker compose start worker; exit $$rc

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

g1-gate: ## G1精度ゲートharness(実API・.envにLATCH_LLM_MODE=real+LATCH_ANTHROPIC_API_KEY必須)
	cd backend && uv run --env-file ../.env python -m latch.g1gate

embed-smoke: ## Embedding実APIスモーク(1呼び出し・.envにLATCH_LLM_MODE=real+LATCH_GEMINI_API_KEY必須)
	cd backend && uv run --env-file ../.env python -m latch.llm.embed_smoke

jev-smoke: ## Jev実APIスモーク(1呼び出し・課金。.envにLATCH_LLM_MODE=real+3鍵必須。FALLBACK=1でフォールバック直接)
	cd backend && uv run --env-file ../.env python -m latch.llm.jev_smoke

g2-gate: ## G2日本語評価harness(実API・課金。.envにLATCH_LLM_MODE=real+3鍵必須。引数は make g2-gate ARGS="--limit 2 --route both" — makeは--以降の転送をしないため)
	cd backend && uv run --env-file ../.env python -m latch.g2gate $(ARGS)

# --- staging/load環境(M4 ws-1・別プロジェクト。ciのcompose.yamlと既存ターゲットは触らない)---

STAGING_COMPOSE = docker compose -f compose.staging.yaml
LOAD_COMPOSE = docker compose -f compose.load.yaml
LOAD_DB_URL = postgresql+asyncpg://latch:latch@127.0.0.1:25432/latch
LOADGEN_DSN = postgresql://latch:latch@127.0.0.1:25432/latch
DASHBOARD_COMPOSE = docker compose -f compose.dashboard.yaml
STAGING_DB_URL = postgresql+asyncpg://latch:latch@127.0.0.1:15432/latch
STAGING_NFR_COMPOSE = docker compose -f compose.staging.yaml -f compose.staging.nfr.yaml
RECOVERY_COMPOSE = docker compose -f compose.staging.yaml -f compose.staging.nfr.yaml -f compose.staging.recovery.yaml
STAGING_BACKUP_DIR = .staging/backup
NFR_FILES = staging_tests_nfr/test_n5_queue.py staging_tests_nfr/test_n4_reeval.py staging_tests_nfr/test_n3_d16.py staging_tests_nfr/test_n2_prep.py staging_tests_nfr/test_n2_backup.py staging_tests_nfr/test_n1_availability.py

SUBJECT ?= u1

staging-up: ## staging環境を起動(healthy待ち。初回は先行して make staging-keys)
	$(STAGING_COMPOSE) up -d --wait

staging-down: ## staging環境を停止(ボリューム保持)
	$(STAGING_COMPOSE) down

staging-ps: ## staging環境の状態
	$(STAGING_COMPOSE) ps

staging-logs: ## staging環境のログ(--tail 30。followは直接 logs -f を)
	$(STAGING_COMPOSE) logs --tail 30

staging-migrate: ## staging DBをheadまでマイグレーション(15432宛・明示実行)
	cd backend && LATCH_DATABASE_URL=$(STAGING_DB_URL) uv run alembic upgrade head

staging-geo-import: ## staging DBへ地物データを取り込み(15432宛。CSV/PBFはbackend/data/geo/・git管理外)
	cd backend && LATCH_DATABASE_URL=$(STAGING_DB_URL) uv run --group geo python -m latch.geo import-isj --csv data/geo/isj.csv
	cd backend && LATCH_DATABASE_URL=$(STAGING_DB_URL) uv run --group geo python -m latch.geo import-osm --pbf data/geo/kyushu-latest.osm.pbf

staging-keys: ## staging専用テスト鍵ペアを生成(.staging/keys・git管理外)
	mkdir -p .staging/keys
	cd backend && uv run python -m latch.auth gen-keypair --out-dir ../.staging/keys

staging-token: ## staging鍵でテストユーザーJWTを発行(make staging-token SUBJECT=u1)
	cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject $(SUBJECT) --private-key ../.staging/keys/idp_test_private.pem

staging-clock: ## staging Redisの制御Clock操作(make staging-clock ARGS="show|set <ISO>|advance <秒>")
	cd backend && LATCH_REDIS_URL=redis://127.0.0.1:16379/0 uv run python -m latch.core.clock_cli $(ARGS)

staging-publish: ## stagingへMatch Eventを手動発行(make staging-publish ARGS="publish created --intent-id <uuid>")
	cd backend && LATCH_PUBSUB_EMULATOR_HOST=127.0.0.1:18085 LATCH_PUBSUB_PROJECT_ID=latch-staging uv run python -m latch.events $(ARGS)

load-up: ## load環境を起動(healthy待ち。負荷試験中のci停止運用はws-6/ws-7手順の前段)
	$(LOAD_COMPOSE) up -d --wait

load-down: ## load環境を停止(ボリューム保持)
	$(LOAD_COMPOSE) down

load-ps: ## load環境の状態
	$(LOAD_COMPOSE) ps

loadusers: ## load環境へ負荷用ユーザーをAPI経由作成(users+consent。make loadusers ARGS="--count 1000")
	cd backend && uv run python -m latch.loadgen --keys ../.staging/keys users $(ARGS)

loadperf: ## 性能試験区間(相A parse→相B save→相C burst→収束待ち→証拠YAML。§8手順のci一時停止を明示実行してから)
	cd backend && uv run python -m latch.loadgen --keys ../.staging/keys perf $(ARGS)

loadmass: ## 総当たり母集合投入(Event発行なし。make loadmass ARGS="--stage 10000 --base-datetime <tz付きISO>")
	cd backend && uv run python -m latch.loadgen massload $(ARGS)

loadbf: ## 総当たり注入100件+収束待ち+証拠YAML(make loadbf ARGS="--stage 10000 --first-user 1")
	cd backend && uv run python -m latch.loadgen --keys ../.staging/keys bruteforce $(ARGS)

load-stub-flat: ## load環境のstub遅延分布を0へ切替(総当たり区間の前提・api/workerをenv差分で再作成)
	LATCH_LLM_STUB_LATENCY_PARSER_P50_MS=0 LATCH_LLM_STUB_LATENCY_PARSER_P95_MS=0 LATCH_LLM_STUB_LATENCY_JEV_P50_MS=0 LATCH_LLM_STUB_LATENCY_JEV_P95_MS=0 $(LOAD_COMPOSE) up -d --force-recreate api1 api2 worker1 worker2

load-migrate: ## load DBをheadまでマイグレーション(25432宛・staging-migrateの対称形)
	cd backend && LATCH_DATABASE_URL=$(LOAD_DB_URL) uv run alembic upgrade head

load-geo-import: ## load DBへ地物データを取り込み(25432宛。CSV/PBFはbackend/data/geo/・git管理外)
	cd backend && LATCH_DATABASE_URL=$(LOAD_DB_URL) uv run --group geo python -m latch.geo import-isj --csv data/geo/isj.csv
	cd backend && LATCH_DATABASE_URL=$(LOAD_DB_URL) uv run --group geo python -m latch.geo import-osm --pbf data/geo/kyushu-latest.osm.pbf

seed-generate: ## M4シード生成(決定的。ローカル・DB不要。ARGS="--base-datetime 2026-10-04T11:00:00+09:00" — tz付きISO必須。--count既定300)
	cd backend && uv run python -m latch.testassets.generator $(ARGS)

seed-verify: ## M4シード機械検査(構造・分布・意図的配置・aggregate照合・地名。ローカル・DB不要)
	cd backend && uv run python -m latch.testassets.verify $(ARGS)

staging-reset: ## staging環境をフルリセット(down -v→up→migrate→geo-import。latch-stagingボリュームのみ削除・ci無干渉。ハーネス実行の前提)
	$(STAGING_COMPOSE) down -v
	$(STAGING_COMPOSE) up -d --wait
	$(MAKE) staging-migrate
	$(MAKE) staging-geo-import

staging-nfr-reset: ## NFR試験用フルリセット(staging-reset+nfr overrideでapiを再作成=lag alert有効。ws-7手順の前提)
	$(MAKE) staging-reset
	$(STAGING_NFR_COMPOSE) up -d --force-recreate --wait api1 api2

staging-nfr-test: ## ws-7 NFRハーネス(順序固定 n5→n4→n3→n2→n1。部分実行は make staging-nfr-test NFR_FILES="staging_tests_nfr/test_n1_availability.py")
	cd backend && uv run pytest $(NFR_FILES) --import-mode=importlib

staging-probe: ## 合成監視プローブ(60分・main1分+dense10秒。バックグラウンドは手順§8のnohup)
	cd backend && uv run python -m latch.loadgen --base-url http://127.0.0.1:18000 --keys ../.staging/keys probe $(ARGS)

staging-d16: ## staging RedisのD-16カウンタ操作(make staging-d16 ARGS="d16 set daily 23999")
	cd backend && LATCH_REDIS_URL=redis://127.0.0.1:16379/0 uv run latch-nfrtools $(ARGS)

staging-dump: ## staging DBのpg_dumpを.staging/backup/へ(定期バックアップ1サイクルの読み替え・10 §4.4)
	mkdir -p $(STAGING_BACKUP_DIR)
	docker exec latch-staging-db-1 pg_dump -U latch -Fc latch > $(STAGING_BACKUP_DIR)/latch-staging.dump

staging-db2-up: ## 復旧試験用db2を起動(16543・新規ボリュームlatch-staging-db2)
	$(RECOVERY_COMPOSE) --profile recovery up -d --wait db2

staging-restore: ## db2へpg_restore(dumpファイルをdocker cpして復元)
	docker cp $(STAGING_BACKUP_DIR)/latch-staging.dump latch-staging-db2-1:/tmp/restore.dump
	docker exec latch-staging-db2-1 pg_restore -U latch -d latch --no-owner /tmp/restore.dump

staging-db2-switch: ## api/workerをdb2向きへ再作成(切替。nginx/db/redis/pubsubは触らない)
	$(RECOVERY_COMPOSE) up -d --force-recreate --wait api1 api2 worker1 worker2

staging-db2-down: ## db2を停止・削除(ボリュームlatch-staging-db2は残す=証拠確保)
	$(RECOVERY_COMPOSE) --profile recovery rm -sf db2

staging-test: ## staging機能試験ハーネス全実行(M4 ws-3・staging-reset直後のフル実行が前提・証拠YAMLをdocs/testassets/results/へ出力)
	cd backend && uv run pytest staging_tests

staging-obs-test: ## staging Observability受け入れハーネス(M4 ws-4・staging-reset直後からの一連実施を前提・証拠YAMLをdocs/testassets/results/へ出力)
	cd backend && uv run pytest staging_tests_obs --import-mode=importlib

dashboard-up: ## ダッシュボード環境を起動(prometheus 19090・grafana 13000・stagingとは別プロジェクト)
	$(DASHBOARD_COMPOSE) up -d --wait

dashboard-down: ## ダッシュボード環境を停止
	$(DASHBOARD_COMPOSE) down

dashboard-ps: ## ダッシュボード環境の状態
	$(DASHBOARD_COMPOSE) ps

dashboard-logs: ## ダッシュボード環境のログ(--tail 30)
	$(DASHBOARD_COMPOSE) logs --tail 30
