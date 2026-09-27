.PHONY: setup up down ps logs lint test test-ci migrate

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

test-ci: ## ci環境試験(compose起動 → unit+integration)
	docker compose up -d --wait
	cd backend && uv run pytest

migrate: ## DBマイグレーションをheadまで適用(明示実行。API/Workerの起動時自動実行はしない)
	cd backend && uv run alembic upgrade head
