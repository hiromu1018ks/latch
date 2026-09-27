# M0 ws-1(PostgreSQLスキーマ+Index+マイグレーション)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依存ハード制約C1の実体として、PostgreSQL(+PostGIS+pgvector)上の12テーブル・Index 7本・idempotency式UNIQUE索引を版管理されたAlembicマイグレーション `0001_initial_schema` として作り、ws-3/ws-4/M1以降がすべて同じスキーマ定義を参照して動く土台を完成させる。

**Architecture:** DDLは `op.execute()` の生SQLで05 §2〜§3の規定を1:1で写す(誤訳の余地の最小化。geoalchemy2・pgvector-pythonはws-1では未導入)。DB接続はSQLAlchemy 2.x async engine(asyncpg)とし、`core/db.py` に工場関数1つのみ置く(DI・モデル定義・リポジトリ層はM1)。マイグレーション実行は `make migrate` の明示実行で、API/Workerの起動時自動実行はしない。integration試験のconftestがsessionスコープで `alembic upgrade head`(冪等)してから実DBを検証する。

**Tech Stack:** Python 3.13 / uv / SQLAlchemy 2.x(async) / asyncpg / Alembic / pytest(+asyncio) / PostgreSQL 17 + PostGIS + pgvector(compose常設ci環境・イメージ作成済み)

**Spec:** `docs/plans/M0/ws-1-design.md`(agent1設計メモ。本計画はこの文書の§5完了条件・§3構成・§2確定値を各タスクへ展開したもの。矛盾した場合はdesign.mdが本計画より優先)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m0-ws-1`。**mainへの直接コミット・push・マージは禁止**(マージはスーパーバイザーが行う)
- **設計判断の固定値**: design.md §6の4論点は**推奨で固定済み**として本計画に落とし込み済み(latches.created_at NOT NULL / reports.status値域・blocks一意性はM3 / embedding次元768 / 本番マイグレーション実行主体はM4)。**固定値を変更した場合は必ず報告ファイル(§8)に「変更前→変更後+理由」を記録する**。黙って変えない
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Committers形式(`feat:` `test:` `docs:` 等)。**pushはしない**
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする(どちらもunit側のみ。integration試験のREDはTDDの途中状態として許容 — Task 4が意図的に作る)。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由
- **ci環境(compose常設)は起動済み**: db(postgres:17 + PostGIS 3.6.4 + pgvector 0.8.6)は `127.0.0.1:5432` で公開中(ユーザー/パス/DB名いずれも `latch`)。`make up` は冪等なので、DBに到達できない場合のみ実行して `make ps` でhealthyを確認する
- **マイグレーション編集の規律**: `0001_initial_schema.py` を適用後に編集した場合は、必ず `cd backend && uv run alembic downgrade base && uv run alembic upgrade head` で適用し直してから検証する(常設ボリュームのため、alembic_versionだけ進んで実物が古い状態を防ぐ — Review Focus #1)
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイル(§8)に状況を書いて作業を停止する

## 1. 参照仕様節(本計画の根拠。docsの節番号)

| テーマ | 出典 |
|---|---|
| PostgreSQL+PostGIS+pgvector上でのGeo判定・Vector検索が全基盤(依存ハード制約C1) | 12 第2節 C1 |
| RDBMS=PostgreSQL(+PostGIS)。Geo Index・JSONB・pgvector併用 | 04 第3節 RDBMS行 |
| Vector DBはpgvector(コンポーネント数最小・Intentとのトランザクション整合)。Vector検索はリポジトリ層で抽象化 | 04 第3節 Vector DB行・第2節 |
| カラム型はPostgreSQL型名。時刻はtimestamptz(JST運用)、金額は整数(円) | 05 冒頭取り決め |
| 12テーブル(users/intents/match_candidates/group_candidates/latches/match_events/latch_status_events/blocks/reports/notifications/messages/calibration_records)のカラム・型・制約・DEFAULT | 05 第2節 |
| friendshipsはMVP対象外。テーブルを作らない(Entity定義は仕様書上にのみ残置) | 05 第1〜2節 / 02 v0.3 D-03再決定 |
| Index 7本(部分Index 4本)+ GIST 1本 + HNSW 1本(SQLはそのまま) | 05 第3節 |
| match_eventsのUNIQUE(event_type, source_intent_id, payload内version)= idempotencyキー。DBレベルで重複排除 | 05 第2節 match_events行 / 06 第9節手順2 |
| 削除済みIntentへの参照Eventは正当な遅延Eventとしてstatus=processedで保存・破棄。payload不正はquarantinedへ | 06 第9節 |
| geo_centerのactive行NULL不可はアプリ層保証。intent_ids配列長CHECK(2≦n≦4 / 3≦n≦4)とcalibration匿名化一貫CHECKはDBで強制 | 05 第2節 各行 |
| status値列挙はアプリ層の状態遷移管理。users.auth_provider・intents.visibility・notification_levelのみCHECK IN | 05 第2節・第6節 |
| embedding は vector(768)。embedding_model は識別子+版 | 05 第2節 |
| APIの期限判定にDBのclock_timestamp()を使わない。時刻参照はすべてClock経由 | 10 第1節「時刻操作」(1) / 04 第5節 FR-41 |
| CREATE EXTENSION はws-1の移行で実施(雛形は拡張同梱まで) | STATUS.md 完了記録 / scaffold-design §1.3・§2.5 |
| DB系エコシステム(asyncpg・SQLAlchemy・Alembic)はPython 3.13で選定済み | scaffold-design §2.3 |
| 毎コミットでci環境の機能試験を回し続ける | 12 第8節 運用ル則5 |

実装方式の細部(Alembic採用・生SQL・async engine・式UNIQUE索引・FK無しのsource_intent_id・DEFAULT/CHECKの方針・概略表テーブルの解釈・Index範囲・移行分割・明示実行)はすべて design.md §2.1〜§2.12 の確定値どおり。

## 2. グローバル制約(全タスクに暗黙に適用)

- DDLの表現は `op.execute()` の生SQL(05のSQLを1:1で写す)。`op.create_table()` は使わない(design §2.2)
- **DEFAULTは05に明記のある列のみ**(structured_data `'{}'` / profile `'{}'` / member_scores `'{}'` / responses `'[]'` / participants_min・max `2` / visibility `'hidden_until_match'` / notification_level `'proposals_only'` / version `1`)。**created_at / updated_at にDB時刻関数のDEFAULTを付けない**。uuid PKには `DEFAULT gen_random_uuid()` を付ける(design §2.8)
- **CHECKは05明記のみ**: auth_provider IN ('google','apple') / visibility IN ('hidden_until_match','summary_only') / notification_level IN ('proposals_only','nearby_also','muted') / latches.intent_ids 2≦n≦4 / group_candidates.intent_ids 3≦n≦4 / calibration_records.intent_ids 2≦n≦4 / calibration_records の匿名化一貫(`anonymized_at IS NULL OR (latch_id IS NULL AND intent_ids IS NULL)`)。status値域はCHECKにしない(design §2.8)
- **match_events.source_intent_id はFKなし**(削除済みIntentへの遅延Event保存経路の担保。design §2.6)。他の参照列は05第1節ERどおりFK(名前は `fk_<table>_<列>` で明示命名)。FKのON DELETEはすべてデフォルト(NO ACTION)(design §2.7)
- `ux_match_events_idempotency` は `(payload->>'version')` を**textのまま**(integerキャスト不可。design §2.5)
- 製品コード(`backend/src/latch/`)で実時間への直接参照を禁止(C2。例外は `core/clock.py` のみ。arch testが毎コミットで強制)。`core/db.py` と `alembic/env.py` を書く際も時刻参照を混ぜない
- 依存追加は `sqlalchemy>=2.0` / `asyncpg>=0.30` / `alembic>=1.14` の3つのみ。geoalchemy2・pgvector-python はM1まで導入禁止(design §1.3)
- テストのINSERT時刻は固定リテラル `TIMESTAMPTZ '2026-09-27 12:00:00+00'` を使う(決定性。design §4.2)
- マイグレーションrevisionは `0001`(ファイル名 `0001_initial_schema.py`)。拡張DROPはdowngradeに含めない(design §2.11)

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクでピン留めする)

1. **編集済みマイグレーションの再適用漏れ** — 常設ボリューム(ci-db)に対し途中の状態で `upgrade head` した後にDDLを修正すると、alembic_versionだけ進んで実物が古いままになる → §0の規律(downgrade→upgrade再適用)+ 完了条件1の `alembic current` と Task 4/6 の実DB検証の突合が所有
2. **常設DBへのテストデータ蓄積による再実行衝突** — 固定UUIDでINSERTした試験データが次回実行のUNIQUE検証と衝突する → Task 6 の挙動検証はすべて `uuid.uuid4()` の新規行で行う規約が所有
3. **asyncpgの型推論エラー**(uuid / uuid[] / vector / jsonb への文字列バインド失敗)→ Task 6 のコード規約「値パラメータは必ず `CAST(:x AS <型>)` で受ける」が所有
4. **`core/db.py`・`alembic/env.py` への実時間参照の混入** — C2違反が紛れ込むとarch testで即レッド → Task 2・3 の毎コミット `make test`(arch test込み)が所有
5. **`pg_indexes.indexdef` の内部正規化(スペース・`::text` 付与)への過剰依存** — PostgreSQLのdeparse形式に正確一致させるとバージョン差で壊れる → Task 4 の包含チェックは `replace(" ", "")` 正規化の併用が所有

## 4. スコープ(作成・変更するファイル一覧)

作成(design.md §3.1どおり):

```text
backend/alembic.ini                       (Task 2: alembic init -t async で生成し修正)
backend/alembic/env.py                    (Task 2: 生成物を latch.settings 差し替え版へ)
backend/alembic/script.py.mako            (Task 2: 生成物をそのまま)
backend/alembic/versions/.gitkeep         (Task 2: 空ディレクトリ対策)
backend/alembic/versions/0001_initial_schema.py  (Task 5)
backend/src/latch/core/db.py              (Task 3)
backend/tests/integration/conftest.py     (Task 3)
backend/tests/integration/test_db_connection.py  (Task 3)
backend/tests/integration/test_schema.py  (Task 4・6)
docs/plans/M0/ws-1-report.md              (Task 7: 報告ファイル)
```

変更:

- `backend/pyproject.toml` — dependencies に sqlalchemy / asyncpg / alembic を追加(Task 1)。`[tool.ruff.lint.per-file-ignores]` に `"alembic/*" = ["E501"]` を追加(Task 2。DDL文字列の長行対策。設定追記の旨は報告ファイルへ記録)
- `backend/uv.lock` — `uv sync` の再生成物。コミットする(Task 1)
- `backend/src/latch/settings.py` — `database_url` フィールドを追加(Task 1)
- `backend/tests/unit/test_settings.py` — database_url の2試験を追記(Task 1)
- `Makefile` — `migrate` ターゲットを追加(.PHONY 行も更新)(Task 2)
- `backend/README.md` — マイグレーション手順を追記(Task 2)

生成されるがコミットしないもの: `backend/.venv/`(gitignore対象)。

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- `docs/00〜12`・`docs/reviews/`(仕様書群。運用ル則7)
- `docs/plans/STATUS.md`(スーパーバイザー管理)
- `docs/plans/M0/ws-1-design.md`(入力設計メモ。読み取り専用)
- `prototype/` 全体(完全非接触。運用ル則6)
- `.claude/`(prompts・settings)
- ルート `README.md`(docs索引の役割を保つ)
- `compose.yaml`(api/workerはまだDBを消費しない。`LATCH_DATABASE_URL` 注入は最初の消費単位/M1。design §1.3)
- `docker/postgres/Dockerfile`(拡張同梱済み。backend依存追加は `uv sync --frozen` がコンテナビルドで自動反映)
- 雛形の既存コード(`src/latch/core/clock.py`・`core/deps.py`・`src/latch/main.py`・`src/latch/worker/`・`tests/conftest.py`・既存試験一式)
- スコープ外と判断する基準: Python側のモデル定義・リポジトリ層・geoalchemy2/pgvector-python(M1)/ API・WorkerへのDBランタイム統合・lifespan DI(M1)/ 地物テーブル(ws-4)/ alembic autogenerate運用(M1)/ staging・prodのマイグレーション実行主体(M4)。これらが必要になったと感じても作らない — design.md §1.3に列挙された後続単位のスコープ
- mainブランチへのコミット・push・マージ

## 6. 完了条件(design.md §5の5項目。検証コマンドつき。Task 7で全て実行し報告ファイルに証拠を残す)

1. **`make migrate` がci環境DBで成功し、`alembic current` が head を示す**
   検証: `make migrate && cd backend && uv run alembic current` — どちらもexit 0、current の出力に `0001` と `(head)` を含む
2. **`make test-ci` で design §4.2 の9検証がグリーン**
   検証: `make test-ci` — exit 0。`cd backend && uv run pytest tests/integration/test_schema.py -v` の `-v` 出力で、§4.2対応表 #1〜#9 に対応する試験がすべて `PASSED` であることを確認
3. **`make lint` / `make test` がグリーン(既存試験への回帰なし)**
   検証: `make lint && make test` — どちらもexit 0(unit 33件+追加分が通る。雛形の既存試験を1つも壊さない)
4. **05 §2〜§3・06 §9 の全要素(12テーブル・7 Index・式UNIQUE・CHECK・型)がスキーマへ反映済み(§1.2対応表でトレース可能)**
   検証: 報告ファイルに「design §1.2対応表 #1〜#16 → 実装箇所(マイグレーション内の該当DDL)→ 検証試験名」のトレース表を記載する(Task 7)
5. **`prototype/`・`docs/01〜12`・`compose.yaml`・`docker/` に差分なし**
   検証: `git diff --name-only main -- prototype compose.yaml docker 'docs/0*.md' 'docs/1*.md' docs/reviews docs/plans/STATUS.md` — 出力が空

## 7. 実装タスク

### Task 1: 依存追加と Settings.database_url

**Files:**
- Modify: `backend/pyproject.toml`(dependencies に3行追加)
- Modify: `backend/uv.lock`(uv sync で再生成)
- Modify: `backend/src/latch/settings.py`
- Test: `backend/tests/unit/test_settings.py`(追記)

**Interfaces:**
- Consumes: 既存 `Settings`(pydantic-settings・env_prefix="LATCH_")
- Produces: `Settings.database_url: str` — デフォルト `postgresql+asyncpg://latch:latch@127.0.0.1:5432/latch`。環境変数 `LATCH_DATABASE_URL` で上書き(Task 2のenv.py・Task 3のdb.pyが消費)

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_settings.py` の末尾に追記:

```python
def test_settings_database_url_default(monkeypatch):
    monkeypatch.delenv("LATCH_DATABASE_URL", raising=False)
    s = Settings()
    assert s.database_url == "postgresql+asyncpg://latch:latch@127.0.0.1:5432/latch"


def test_settings_database_url_env_override(monkeypatch):
    monkeypatch.setenv(
        "LATCH_DATABASE_URL", "postgresql+asyncpg://u:p@db.example.com:5432/latchdb"
    )
    s = Settings()
    assert s.database_url == "postgresql+asyncpg://u:p@db.example.com:5432/latchdb"
```

- [ ] **Step 2: 試験が失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_settings.py -v`
Expected: 追加した2件が FAIL(`AttributeError: 'Settings' object has no attribute 'database_url'`)。既存3件は PASS

- [ ] **Step 3: 依存を追加して uv sync**

`backend/pyproject.toml` の `[project]` dependencies を次へ(3行追加):

```toml
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "pydantic-settings>=2.6",
    "sqlalchemy>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.14",
]
```

Run: `cd backend && uv sync`
Expected: exit 0(uv.lock が更新される)

- [ ] **Step 4: Settings へ database_url を実装**

`backend/src/latch/settings.py` を次へ:

```python
"""アプリ設定(design §2.8: コードが消費しない設定は作らない)。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LATCH_")

    app_env: str = "ci"  # ci / staging / prod(10 第1節の環境)
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://latch:latch@127.0.0.1:5432/latch"
```

- [ ] **Step 5: 試験が通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_settings.py -v`
Expected: 5件すべて PASS

- [ ] **Step 6: lint・unit 全体を確認してコミット**

Run: `make lint && make test`
Expected: どちらも exit 0

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/latch/settings.py backend/tests/unit/test_settings.py
git commit -m "feat: DB系依存(sqlalchemy/asyncpg/alembic)とSettings.database_url"
```

### Task 2: Alembic 環境構築(asyncテンプレート・URL一元化)

**Files:**
- Create: `backend/alembic.ini`(生成後に修正)
- Create: `backend/alembic/env.py`(生成後に差し替え)
- Create: `backend/alembic/script.py.mako`(生成物をそのまま)
- Create: `backend/alembic/versions/.gitkeep`
- Modify: `Makefile`(migrate ターゲット)
- Modify: `backend/pyproject.toml`(ruff per-file-ignores)
- Modify: `backend/README.md`(マイグレーション手順)

**Interfaces:**
- Consumes: `Settings().database_url`(Task 1)
- Produces: `cd backend && uv run alembic upgrade head` が動作する状態・`make migrate` ターゲット。`alembic/versions/` 配下にrevisionファイルを置ける状態(Task 5が消費)

- [ ] **Step 1: 公式asyncテンプレートで初期化**

Run: `cd backend && uv run alembic init -t async alembic`
Expected: `alembic.ini`・`alembic/env.py`・`alembic/script.py.mako`・`alembic/versions/` が生成されexit 0

- [ ] **Step 2: alembic.ini を修正(URLをenv.pyへ委譲)**

生成された `alembic.ini` の `[alembic]` セクションから `sqlalchemy.url = driver://user:pass@localhost/dbname` の行を削除し、代わりにコメントを置く(ロギング等の他セクションは既定のまま):

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
# URLは設定しない — env.py が latch.settings.database_url から設定する(design §2.12: 設定の一元化)
```

- [ ] **Step 3: env.py を latch.settings 差し替え版へ**

`backend/alembic/env.py` の内容を次へ(公式asyncテンプレートからURL取得のみ差し替え。`target_metadata = None` はM1のモデル導入まで維持 — design §2.4):

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from latch.settings import Settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def get_url() -> str:
    """URLは latch.settings から一元取得(design §2.12: alembic.iniに置かない)。"""
    return Settings().database_url


def run_migrations_offline() -> None:
    """offline mode: DB接続なしでSQL文面を生成する。"""
    url = get_url()
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """online mode: async engine(asyncpg)で接続して実行する。"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    if connectable is None:
        raise RuntimeError("Failed to create engine from config")

    with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: versions/ の空ディレクトリ対策と ruff 例外**

```bash
touch backend/alembic/versions/.gitkeep
```

`backend/pyproject.toml` の `[tool.ruff.lint]` の下に追記(DDL文字列の長行対策。追記した旨は報告ファイルへ記録):

```toml
[tool.ruff.lint.per-file-ignores]
"alembic/*" = ["E501"]
```

- [ ] **Step 5: 空状態で alembic が動くことを確認**

Run: `make up && cd backend && uv run alembic current`
Expected: exit 0(適用前なので版表示は空。接続エラーが出ないこと)

- [ ] **Step 6: Makefile に migrate を追加**

`Makefile` を次へ(.PHONY 行に `migrate` を追加し、ターゲットを追記):

```makefile
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
```

- [ ] **Step 7: README にマイグレーション手順を追記**

`backend/README.md` の「規律」セクションの前に追記:

```markdown
## マイグレーション(ws-1)

- `make migrate` — ci常設DBへマイグレーションを適用(`alembic upgrade head`)。**明示実行のみ**(API/Workerの起動時自動実行はしない)
- 接続先は `LATCH_DATABASE_URL`(デフォルト `postgresql+asyncpg://latch:latch@127.0.0.1:5432/latch`)
- 現行版の確認: `cd backend && uv run alembic current`
- 新しいマイグレーション追加: `cd backend && uv run alembic revision -m "説明"`(0001_initial_schema は手作業で作成済み)
```

- [ ] **Step 8: lint・unit を確認してコミット**

Run: `make lint && make test`
Expected: どちらも exit 0

```bash
git add backend/alembic.ini backend/alembic backend/pyproject.toml Makefile backend/README.md
git commit -m "feat: Alembic環境(asyncテンプレート・URLはlatch.settings一元化・make migrate)"
```

### Task 3: core/db.py と integration 接続基盤

**Files:**
- Create: `backend/src/latch/core/db.py`
- Create: `backend/tests/integration/conftest.py`
- Test: `backend/tests/integration/test_db_connection.py`

**Interfaces:**
- Consumes: `Settings`(Task 1)・`alembic upgrade head` が動く状態(Task 2)
- Produces: `create_db_engine(settings: Settings) -> sqlalchemy.ext.asyncio.AsyncEngine`(`server_settings={"timezone": "UTC"}` 固定。M1のDIが消費)。`db_engine` fixture(functionスコープのAsyncEngine — Task 4/6のtest_schema.pyが消費)と `migrated_db` fixture(sessionスコープで `alembic upgrade head` を1回実行)

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/integration/test_db_connection.py` を作成:

```python
"""DB接続基盤(core/db.py + conftest)の疎通(design §4.1 integration)。"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def test_db_connect_returns_postgres17(db_engine):
    async with db_engine.connect() as conn:
        version = await conn.scalar(text("SELECT version()"))
    assert version is not None
    assert "PostgreSQL 17" in version


async def test_connection_timezone_is_utc(db_engine):
    """接続はUTCに固定(design §2.3: timestamptzはinstant保持・JST運用はアプリ層表現)。"""
    async with db_engine.connect() as conn:
        tz = await conn.scalar(text("SHOW timezone"))
    assert tz == "UTC"
```

- [ ] **Step 2: 試験が失敗することを確認**

Run: `cd backend && uv run pytest tests/integration/test_db_connection.py -v`
Expected: 2件とも ERROR(fixture `db_engine` not found)

- [ ] **Step 3: core/db.py を実装**

`backend/src/latch/core/db.py` を作成(時刻参照を含めない — arch testのスキャン対象。design §3.2):

```python
"""DB接続の工場(design §2.3: API/WorkerへのDIはM1。ここには工場のみを置く)。"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from latch.settings import Settings


def create_db_engine(settings: Settings) -> AsyncEngine:
    """settings.database_url のasync engine(asyncpg)を生成する。

    - 接続のタイムゾーンはUTCに固定(timestamptzはinstantで保持され、JST運用は
      アプリ層の表現。Clock契約 now()=tz-aware UTC と同じ規約)
    - 時刻の生成はアプリ層のClock由来の明示値(DB時刻関数DEFAULTは持たない — design §2.8)
    """
    return create_async_engine(
        settings.database_url,
        server_settings={"timezone": "UTC"},
    )
```

- [ ] **Step 4: integration conftest を実装**

`backend/tests/integration/conftest.py` を作成:

```python
"""integration試験の共通フィクスチャ(design §3.1・§4.1)。

compose常設DBに対し、sessionスコープで alembic upgrade head(冪等)してから
functionスコープの AsyncEngine を提供する。エンジンは試験ごとに生成・破棄する
(イベントループをまたぐ接続再利用を避ける)。
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.db import create_db_engine
from latch.settings import Settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def migrated_db() -> None:
    """DBをheadまでマイグレーションする(冪等・sessionで1回)。"""
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
async def db_engine(migrated_db) -> AsyncEngine:
    engine = create_db_engine(Settings())
    yield engine
    await engine.dispose()
```

- [ ] **Step 5: 試験が通ることを確認**

Run: `cd backend && uv run pytest tests/integration/test_db_connection.py -v`
Expected: 2件とも PASS(versions/ が空のためupgradeはno-op。接続とUTC固定だけが立証される)

- [ ] **Step 6: lint・unit・test-ci を確認してコミット**

Run: `make lint && make test && make test-ci`
Expected: どちらも exit 0(integration 5件 = 既存3件+追加2件)

```bash
git add backend/src/latch/core/db.py backend/tests/integration/conftest.py backend/tests/integration/test_db_connection.py
git commit -m "feat: core/db.pyエンジン工場(UTC固定)とintegration接続基盤(conftestでalembic upgrade head)"
```

### Task 4: スキーマ・カタログ検証試験(RED — design §4.2 #1〜#4・#8)

**Files:**
- Test: `backend/tests/integration/test_schema.py`(新規作成)

**Interfaces:**
- Consumes: `db_engine` fixture(Task 3)
- Produces: design §4.2対応表 #1(拡張)・#2(12テーブル存在+friendships不在)・#3(Index 7本+WHERE句+GIST/HNSW/ops)・#4(式UNIQUE索引)・#8(FK)の検証試験。定数 `EXPECTED_TABLES` / `EXPECTED_FKS` / `EXPECTED_UNIQUE_CONSTRAINTS`(Task 6も同一ファイルに追記)

- [ ] **Step 1: 検証試験を書く**

`backend/tests/integration/test_schema.py` を作成:

```python
"""スキーマ実体の検証 — design.md §4.2対応表の9検証のうちカタログ検証(#1〜#4・#8)。

05 §2〜§3・06 §9 の要素が実DBへ反映済みであることを情報スキーマ/カタログで証明する。
挙動検証(#5〜#7・#9)は後段のタスクでこのファイルへ追記する。
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "users",
    "intents",
    "match_candidates",
    "group_candidates",
    "latches",
    "match_events",
    "latch_status_events",
    "blocks",
    "reports",
    "notifications",
    "messages",
    "calibration_records",
}

# 05 §1 ERどおりのFK(design §2.6: match_events.source_intent_id を除く)
EXPECTED_FKS = {
    "fk_intents_user",
    "fk_match_candidates_intent_a",
    "fk_match_candidates_intent_b",
    "fk_latches_group_candidate",
    "fk_latch_status_events_latch",
    "fk_blocks_blocker",
    "fk_blocks_blocked",
    "fk_reports_reporter",
    "fk_reports_reportee",
    "fk_reports_latch",
    "fk_notifications_user",
    "fk_messages_latch",
    "fk_messages_sender",
    "fk_calibration_records_latch",
}

EXPECTED_UNIQUE_CONSTRAINTS = {
    "uq_users_auth_provider_subject",
    "uq_match_candidates_intent_versions",
}


async def test_extensions_installed(db_engine):
    """#1: postgis・vectorがpg_extensionに存在(design §1.2 #15 — 拡張は本移行で作成)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(text("SELECT extname FROM pg_extension"))
        names = {row[0] for row in result}
    assert {"postgis", "vector"} <= names


async def test_twelve_tables_exist(db_engine):
    """#2: 12テーブルがpublicスキーマに存在(05 §2)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        names = {row[0] for row in result}
    assert EXPECTED_TABLES <= names


async def test_friendships_table_absent(db_engine):
    """#2: friendshipsはMVP対象外のため存在しない(05 §1〜2・02 v0.4)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'friendships'"
            )
        )
        assert result.first() is None


async def test_intents_indexes_exist_with_partial_where(db_engine):
    """#3: 05 §3のIndex 7本が存在。部分IndexのWHERE句・opsまで確認。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
            )
        )
        defs = {row[0]: row[1] for row in result}
    for name in (
        "idx_intents_matching",
        "idx_intents_geo",
        "idx_intents_budget",
        "idx_intents_participants",
        "idx_intents_expires",
        "idx_intents_user",
        "idx_intents_embedding",
    ):
        assert name in defs, f"{name} が存在しない"
    # 部分IndexのWHERE句(05 §3。indexdefの内部正規化に強い部分一致で見る)
    assert "status = 'active'" in defs["idx_intents_matching"]
    assert "status = 'active'" in defs["idx_intents_budget"]
    assert "status = 'active'" in defs["idx_intents_participants"]
    assert "status IN ('draft'" in defs["idx_intents_expires"]
    # ops(pgvector)
    assert "vector_cosine_ops" in defs["idx_intents_embedding"]


async def test_index_access_methods(db_engine):
    """#3: GIST(geo)とHNSW(embedding)がpg_am上正しい(design §4.2 #3)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT i.relname, am.amname FROM pg_index x "
                "JOIN pg_class i ON i.oid = x.indexrelid "
                "JOIN pg_class t ON t.oid = x.indrelid "
                "JOIN pg_am am ON am.oid = i.relam "
                "WHERE t.relname = 'intents'"
            )
        )
        methods = {row[0]: row[1] for row in result}
    assert methods["idx_intents_geo"] == "gist"
    assert methods["idx_intents_embedding"] == "hnsw"


async def test_match_events_idempotency_is_expression_unique_index(db_engine):
    """#4: ux_match_events_idempotencyが式UNIQUE索引として存在(06 §9手順2)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND indexname = 'ux_match_events_idempotency'"
            )
        )
        (indexdef,) = result.one()
    normalized = indexdef.replace(" ", "")
    assert "CREATEUNIQUEINDEX" in normalized
    assert "payload->>'version'" in normalized


async def test_expected_foreign_keys(db_engine):
    """#8: 05 §1 ERどおりの14本のFKが存在。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'f' AND connamespace = 'public'::regnamespace"
            )
        )
        names = {row[0] for row in result}
    assert EXPECTED_FKS <= names


async def test_match_events_has_no_foreign_key(db_engine):
    """#8: match_eventsにはFKが無い(削除済みIntentへの遅延Event保存の担保。design §2.6)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT conname FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE c.contype = 'f' AND t.relname = 'match_events'"
            )
        )
        assert result.first() is None


async def test_unique_constraints_exist(db_engine):
    """design §2.10: UNIQUE制約2件(users認証キー・候補バージョン組)。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'u' AND connamespace = 'public'::regnamespace"
            )
        )
        names = {row[0] for row in result}
    assert EXPECTED_UNIQUE_CONSTRAINTS <= names
```

- [ ] **Step 2: 試験が失敗することを確認(RED)**

Run: `cd backend && uv run pytest tests/integration/test_schema.py -v`
Expected: 9件すべて FAIL(スキーマ未適用のため。例: `relation "users" does not exist`・`assert {'postgis', 'vector'} <= names` の assert失敗。postgisは無いので #1 も失敗する)

- [ ] **Step 3: lint・unit を確認してコミット(意図的なREDコミット)**

Run: `make lint && make test`
Expected: どちらも exit 0(unit側は影響なし。integrationのREDはTDDの途中状態)

```bash
git add backend/tests/integration/test_schema.py
git commit -m "test: スキーマカタログ検証(拡張・12テーブル・Index・式UNIQUE・FK)のRED"
```

### Task 5: 0001_initial_schema マイグレーション(GREEN)

**Files:**
- Create: `backend/alembic/versions/0001_initial_schema.py`(手作業で作成 — `alembic revision` ではなくこのファイルをそのまま置く)

**Interfaces:**
- Consumes: Alembic環境(Task 2)・Task 4の検証試験
- Produces: revision `0001`(head)。publicスキーマの12テーブル+Index(後続のws-3/ws-4/M1が参照)。downgradeは `alembic downgrade base` で全テーブルをDROPする(拡張は残す)

- [ ] **Step 1: マイグレーションファイルを作成**

`backend/alembic/versions/0001_initial_schema.py` を作成(§2グローバル制約のとおり、05 §2〜§3・06 §9・design §2の確定値を1:1で写す。**カラム順は05 §2の表の順どおり**):

```python
"""初期スキーマ: PostgreSQL+PostGIS+pgvector上の12テーブルとIndex(05 §2〜§3・06 §9)。

- CREATE EXTENSION(postgis / vector)— IF NOT EXISTS付きで冪等。downgradeでは
  DROPしない(public拡張はws-4の地物テーブル等が共有するリソース。design §2.11)
- friendshipsはMVP対象外のため作らない(05 §1〜2)
- CHECKは05が明記したもののみ(§2.8)。status値域はアプリ層の遷移管理(05 §6)
- 時刻列にDB時刻関数のDEFAULTを付けない(時刻はClock由来の明示値 — 10 第1節)
- uuid PKにはDEFAULT gen_random_uuid()を付ける(05はUUID生成方法を規定しない)
- 本番・stagingでは拡張作成権限を持つロールでの実行を前提(design §2.11)

Revision ID: 0001
Revises:
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 拡張(design §2.11: IF NOT EXISTS付きで冪等)
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- 12テーブル(FK依存順。05 §2の表のカラム順どおり) ---

    # users
    op.execute("""
        CREATE TABLE users (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            display_name text NOT NULL,
            profile jsonb NOT NULL DEFAULT '{}',
            birth_date date NOT NULL,
            auth_provider text NOT NULL
                CHECK (auth_provider IN ('google', 'apple')),
            auth_subject text NOT NULL,
            location_preferences jsonb,
            visibility_preferences jsonb,
            trust_score numeric,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT users_pkey PRIMARY KEY (id),
            CONSTRAINT uq_users_auth_provider_subject UNIQUE (auth_provider, auth_subject)
        )
    """)

    # intents
    op.execute("""
        CREATE TABLE intents (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL,
            category_primary text NOT NULL,
            alcohol_involved boolean NOT NULL,
            raw_text text NOT NULL,
            structured_data jsonb NOT NULL DEFAULT '{}',
            geo_center geography(Point, 4326),
            geo_radius_m integer,
            budget_max integer,
            participants_min smallint NOT NULL DEFAULT 2,
            participants_max smallint NOT NULL DEFAULT 2,
            visibility text NOT NULL DEFAULT 'hidden_until_match'
                CHECK (visibility IN ('hidden_until_match', 'summary_only')),
            notification_level text NOT NULL DEFAULT 'proposals_only'
                CHECK (notification_level IN ('proposals_only', 'nearby_also', 'muted')),
            status text NOT NULL,
            version integer NOT NULL DEFAULT 1,
            time_start timestamptz,
            time_end timestamptz,
            expires_at timestamptz,
            embedding vector(768),
            embedding_model text,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT intents_pkey PRIMARY KEY (id),
            CONSTRAINT fk_intents_user FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # match_candidates
    op.execute("""
        CREATE TABLE match_candidates (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            intent_a_id uuid NOT NULL,
            intent_b_id uuid NOT NULL,
            intent_a_version integer NOT NULL,
            intent_b_version integer NOT NULL,
            prev_latch_score numeric,
            prev_evaluated_at timestamptz,
            retrieval_score numeric,
            cheap_judge_score numeric,
            jev_result jsonb,
            latch_score numeric,
            status text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT match_candidates_pkey PRIMARY KEY (id),
            CONSTRAINT uq_match_candidates_intent_versions
                UNIQUE (intent_a_id, intent_b_id, intent_a_version, intent_b_version),
            CONSTRAINT fk_match_candidates_intent_a
                FOREIGN KEY (intent_a_id) REFERENCES intents (id),
            CONSTRAINT fk_match_candidates_intent_b
                FOREIGN KEY (intent_b_id) REFERENCES intents (id)
        )
    """)

    # group_candidates(intent_idsは配列参照のためFKなし。同一user検査はアプリ層 — 05 §2)
    op.execute("""
        CREATE TABLE group_candidates (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            intent_ids uuid[] NOT NULL
                CHECK (cardinality(intent_ids) BETWEEN 3 AND 4),
            member_scores jsonb NOT NULL DEFAULT '{}',
            aggregate_score numeric,
            prev_aggregate_score numeric,
            status text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT group_candidates_pkey PRIMARY KEY (id)
        )
    """)

    # latches(created_at NOT NULL / completed_at NULL は design §2.9 の解釈)
    op.execute("""
        CREATE TABLE latches (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            intent_ids uuid[] NOT NULL
                CHECK (cardinality(intent_ids) BETWEEN 2 AND 4),
            group_candidate_id uuid,
            proposal jsonb NOT NULL,
            score numeric NOT NULL,
            responses jsonb NOT NULL DEFAULT '[]',
            status text NOT NULL,
            response_deadline timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL,
            completed_at timestamptz,
            CONSTRAINT latches_pkey PRIMARY KEY (id),
            CONSTRAINT fk_latches_group_candidate
                FOREIGN KEY (group_candidate_id) REFERENCES group_candidates (id)
        )
    """)

    # match_events(source_intent_id はFKなし — 削除済みIntentへの遅延Event保存の担保。
    # design §2.6)
    op.execute("""
        CREATE TABLE match_events (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            event_type text NOT NULL,
            source_intent_id uuid NOT NULL,
            payload jsonb NOT NULL,
            status text NOT NULL,
            created_at timestamptz NOT NULL,
            processed_at timestamptz,
            CONSTRAINT match_events_pkey PRIMARY KEY (id)
        )
    """)

    # latch_status_events(from_status NULL可 / user_id NULL可はシステム起因 — 05 §2)
    op.execute("""
        CREATE TABLE latch_status_events (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            latch_id uuid NOT NULL,
            from_status text,
            to_status text NOT NULL,
            user_id uuid,
            created_at timestamptz NOT NULL,
            CONSTRAINT latch_status_events_pkey PRIMARY KEY (id),
            CONSTRAINT fk_latch_status_events_latch
                FOREIGN KEY (latch_id) REFERENCES latches (id)
        )
    """)

    # blocks(UNIQUE(blocker_id, blocked_id)は05に明記がなくアプリ層 — design §2.9)
    op.execute("""
        CREATE TABLE blocks (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            blocker_id uuid NOT NULL,
            blocked_id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT blocks_pkey PRIMARY KEY (id),
            CONSTRAINT fk_blocks_blocker FOREIGN KEY (blocker_id) REFERENCES users (id),
            CONSTRAINT fk_blocks_blocked FOREIGN KEY (blocked_id) REFERENCES users (id)
        )
    """)

    # reports(latch_id NULL可 = LATCH文脈を欠く通報も想定。status値域はM3 — design §2.9)
    op.execute("""
        CREATE TABLE reports (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            reporter_id uuid NOT NULL,
            reportee_id uuid NOT NULL,
            latch_id uuid,
            reason text NOT NULL,
            status text NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT reports_pkey PRIMARY KEY (id),
            CONSTRAINT fk_reports_reporter FOREIGN KEY (reporter_id) REFERENCES users (id),
            CONSTRAINT fk_reports_reportee FOREIGN KEY (reportee_id) REFERENCES users (id),
            CONSTRAINT fk_reports_latch FOREIGN KEY (latch_id) REFERENCES latches (id)
        )
    """)

    # notifications
    op.execute("""
        CREATE TABLE notifications (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL,
            type text NOT NULL,
            payload jsonb NOT NULL,
            read_at timestamptz,
            created_at timestamptz NOT NULL,
            CONSTRAINT notifications_pkey PRIMARY KEY (id),
            CONSTRAINT fk_notifications_user FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # messages
    op.execute("""
        CREATE TABLE messages (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            latch_id uuid NOT NULL,
            sender_id uuid NOT NULL,
            body text NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT messages_pkey PRIMARY KEY (id),
            CONSTRAINT fk_messages_latch FOREIGN KEY (latch_id) REFERENCES latches (id),
            CONSTRAINT fk_messages_sender FOREIGN KEY (sender_id) REFERENCES users (id)
        )
    """)

    # calibration_records(匿名化一貫CHECK — 05 §2・design §2.8)
    op.execute("""
        CREATE TABLE calibration_records (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            latch_id uuid,
            intent_ids uuid[]
                CHECK (cardinality(intent_ids) BETWEEN 2 AND 4),
            prediction jsonb NOT NULL,
            proposal_snapshot jsonb NOT NULL,
            actual_responses jsonb NOT NULL,
            matched boolean NOT NULL,
            actual_attended boolean,
            cancelled_after boolean,
            anonymized_at timestamptz,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT calibration_records_pkey PRIMARY KEY (id),
            CONSTRAINT fk_calibration_records_latch
                FOREIGN KEY (latch_id) REFERENCES latches (id),
            CONSTRAINT ck_calibration_records_anonymized
                CHECK (anonymized_at IS NULL OR (latch_id IS NULL AND intent_ids IS NULL))
        )
    """)

    # --- Index(05 §3のSQLそのまま) ---

    # アクティブIntentのマッチング検索(Layer 1)
    op.execute("""
        CREATE INDEX idx_intents_matching ON intents
          (category_primary, time_start, expires_at)
          WHERE status = 'active'
    """)
    # 地理(PostGIS)。draftのgeo_centerはNULLのため対象外(05 §3)
    op.execute("CREATE INDEX idx_intents_geo ON intents USING GIST (geo_center)")
    # Hard Constraint用の部分Index
    op.execute(
        "CREATE INDEX idx_intents_budget ON intents (budget_max) WHERE status = 'active'"
    )
    op.execute("""
        CREATE INDEX idx_intents_participants ON intents (participants_min, participants_max)
          WHERE status = 'active'
    """)
    # 期限処理・所有者参照。draftを含む(v0.4 — 05 §3)
    op.execute("""
        CREATE INDEX idx_intents_expires ON intents (expires_at)
          WHERE status IN ('draft', 'active', 'paused')
    """)
    op.execute("CREATE INDEX idx_intents_user ON intents (user_id, status)")
    # Vector(pgvector, HNSW)。draftはEmbeddingしないためNULL(05 §3)
    op.execute("""
        CREATE INDEX idx_intents_embedding ON intents
          USING hnsw (embedding vector_cosine_ops)
    """)

    # --- idempotencyの式UNIQUE索引(06 §9手順2・design §2.5 ---
    # versionはtextのままintegerキャストしない: 毒ペイロードのINSERT自体が
    # 索引評価エラーで失敗するとquarantined保存経路がDB側で塞がれるため)
    op.execute("""
        CREATE UNIQUE INDEX ux_match_events_idempotency ON match_events
          (event_type, source_intent_id, (payload->>'version'))
    """)


def downgrade() -> None:
    # テーブルを逆順DROP(拡張はDROPしない — design §2.11)
    op.execute("DROP TABLE calibration_records")
    op.execute("DROP TABLE messages")
    op.execute("DROP TABLE notifications")
    op.execute("DROP TABLE reports")
    op.execute("DROP TABLE blocks")
    op.execute("DROP TABLE latch_status_events")
    op.execute("DROP TABLE match_events")
    op.execute("DROP TABLE latches")
    op.execute("DROP TABLE group_candidates")
    op.execute("DROP TABLE match_candidates")
    op.execute("DROP TABLE intents")
    op.execute("DROP TABLE users")
```

- [ ] **Step 2: マイグレーションを適用**

Run: `make migrate`
Expected: exit 0(`0001_initial_schema` が適用される)

- [ ] **Step 3: 現行版が head を示すことを確認**

Run: `cd backend && uv run alembic current`
Expected: `0001 (head)` を含む出力

- [ ] **Step 4: カタログ検証が通ることを確認(GREEN)**

Run: `cd backend && uv run pytest tests/integration/test_schema.py -v`
Expected: 9件すべて PASS

**もし FAIL した場合**: DDLに誤りがある。`0001_initial_schema.py` を修正し、`cd backend && uv run alembic downgrade base && uv run alembic upgrade head` で適用し直して再実行する(§0の規律)。修正内容は報告ファイルに記録する

- [ ] **Step 5: lint・unit・test-ci を確認してコミット**

Run: `make lint && make test && make test-ci`
Expected: どちらも exit 0(integration 14件 = 5件 + カタログ検証9件)

```bash
git add backend/alembic/versions/0001_initial_schema.py
git commit -m "feat: 0001初期スキーマ(PostGIS+pgvector拡張・12テーブル・Index・idempotency式UNIQUE)"
```

### Task 6: 挙動検証試験(UNIQUE・CHECK・型・DEFAULT — design §4.2 #5〜#7・#9)

**Files:**
- Test: `backend/tests/integration/test_schema.py`(追記)

**Interfaces:**
- Consumes: `db_engine` fixture(Task 3)・revision `0001`(Task 5)
- Produces: design §4.2対応表 #5(UNIQUE実効)・#6(CHECK実効)・#7(geography・vector型実効)・#9(DEFAULT実効)の検証試験

**規約(Review Focus #2・#3)**: 挿入行のIDはすべて `uuid.uuid4()` の新規値で生成する(固定UUIDにしない — 常設DBの再実行衝突を防ぐ)。値パラメータは必ず `CAST(:x AS <型>)` で受ける(asyncpgの型推論エラーを避ける)。時刻は固定リテラル `TS` を使う。

- [ ] **Step 1: 挙動検証を test_schema.py へ追記**

`backend/tests/integration/test_schema.py` の import 部を次へ差し替え(`uuid`・`IntegrityError`・`DBAPIError` を追加):

```python
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
```

ファイル末尾に追記:

```python
# --- 挙動検証(design §4.2 #5〜#7・#9)。時刻は固定リテラル(決定性) ---

TS = "TIMESTAMPTZ '2026-09-27 12:00:00+00'"

VEC768 = "[" + ",".join(["0.001"] * 768) + "]"

INSERT_LATCH = (
    "INSERT INTO latches "
    "(id, intent_ids, proposal, score, status, response_deadline, expires_at, created_at) "
    "VALUES (CAST(:id AS uuid), CAST(:ids AS uuid[]), CAST(:proposal AS jsonb), "
    f"0.85, 'candidate', {TS}, {TS}, {TS})"
)

INSERT_CALIBRATION = (
    "INSERT INTO calibration_records "
    "(latch_id, intent_ids, prediction, proposal_snapshot, actual_responses, "
    "matched, anonymized_at, created_at, updated_at) "
    "VALUES (CAST(:latch_id AS uuid), CAST(:intent_ids AS uuid[]), "
    f"CAST(:prediction AS jsonb), CAST(:snapshot AS jsonb), "
    f"CAST(:responses AS jsonb), true, CAST(:anonymized AS timestamptz), {TS}, {TS})"
)


def _uuid_array(count: int) -> str:
    """PostgreSQL配列リテラル('{u1,u2,...}')を新規UUIDで組み立てる。"""
    return "{" + ",".join(str(uuid.uuid4()) for _ in range(count)) + "}"


async def insert_user(conn, auth_provider: str = "google") -> str:
    """usersの最小行(DEFAULT検証のためprofileは省略)。user_id(uuid文字列)を返す。"""
    user_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO users "
            "(id, display_name, birth_date, auth_provider, auth_subject, created_at, updated_at) "
            f"VALUES (CAST(:id AS uuid), 'test', DATE '2000-01-01', :provider, "
            f":subject, {TS}, {TS})"
        ),
        {"id": user_id, "provider": auth_provider, "subject": f"sub-{user_id}"},
    )
    return user_id


async def insert_minimal_intent(conn, user_id: str) -> str:
    """intentsの最小行(05明記DEFAULT列はすべて省略)。intent_id(uuid文字列)を返す。"""
    intent_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO intents "
            "(id, user_id, category_primary, alcohol_involved, raw_text, status, "
            "created_at, updated_at) "
            f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, 'raw', "
            f"'draft', {TS}, {TS})"
        ),
        {"id": intent_id, "uid": user_id},
    )
    return intent_id


async def insert_latch(conn) -> str:
    """latchesの最小行(responsesはDEFAULT検証のため省略)。latch_idを返す。"""
    latch_id = str(uuid.uuid4())
    await conn.execute(
        text(INSERT_LATCH),
        {"id": latch_id, "ids": _uuid_array(2), "proposal": "{}"},
    )
    return latch_id


async def _insert_match_event(conn, sid: str, payload: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO match_events "
            "(event_type, source_intent_id, payload, status, created_at) "
            f"VALUES ('create', CAST(:sid AS uuid), CAST(:payload AS jsonb), "
            f"'pending', {TS})"
        ),
        {"sid": sid, "payload": payload},
    )


# --- #5: UNIQUE実効(06 §9手順2) ---


async def test_match_events_duplicate_version_rejected(db_engine):
    """同一(event_type, source_intent_id, version)の2行目INSERTは拒否される。"""
    sid = str(uuid.uuid4())
    async with db_engine.connect() as conn:
        await _insert_match_event(conn, sid, '{"version": 1}')
        await conn.commit()
        with pytest.raises(IntegrityError):
            await _insert_match_event(conn, sid, '{"version": 1}')
        await conn.rollback()


async def test_match_events_different_version_allowed(db_engine):
    """異なるversionのEventは同一source_intent_idでも保存できる。"""
    sid = str(uuid.uuid4())
    async with db_engine.connect() as conn:
        await _insert_match_event(conn, sid, '{"version": 1}')
        await _insert_match_event(conn, sid, '{"version": 2}')
        await conn.commit()


async def test_match_events_missing_version_duplicates_allowed(db_engine):
    """version欠落(NULL)はUNIQUE対象外 — 毒ペイロードの隔離保管経路の担保(design §2.5)。"""
    sid = str(uuid.uuid4())
    async with db_engine.connect() as conn:
        await _insert_match_event(conn, sid, "{}")
        await _insert_match_event(conn, sid, "{}")
        await conn.commit()


# --- #6: CHECK実効(05 §2明記分のみ) ---


async def test_users_auth_provider_check(db_engine):
    """auth_provider不正値は拒否(05 §2)。"""
    async with db_engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, display_name, birth_date, auth_provider, auth_subject, "
                    "created_at, updated_at) "
                    f"VALUES (CAST(:id AS uuid), 't', DATE '2000-01-01', 'twitter', "
                    f"'s', {TS}, {TS})"
                ),
                {"id": str(uuid.uuid4())},
            )
        await conn.rollback()


async def test_intents_visibility_check(db_engine):
    """visibility不正値は拒否(05 §2)。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO intents "
                    "(id, user_id, category_primary, alcohol_involved, raw_text, "
                    "visibility, status, created_at, updated_at) "
                    f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, "
                    f"'raw', 'invalid', 'draft', {TS}, {TS})"
                ),
                {"id": str(uuid.uuid4()), "uid": user_id},
            )
        await conn.rollback()


async def test_intents_notification_level_check(db_engine):
    """notification_level不正値は拒否(05 §2)。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO intents "
                    "(id, user_id, category_primary, alcohol_involved, raw_text, "
                    "notification_level, status, created_at, updated_at) "
                    f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, "
                    f"'raw', 'invalid', 'draft', {TS}, {TS})"
                ),
                {"id": str(uuid.uuid4()), "uid": user_id},
            )
        await conn.rollback()


async def test_latches_intent_ids_length_check(db_engine):
    """latches.intent_idsは2≦n≦4(05 §2)。1・5は拒否・2は成功。"""
    async with db_engine.connect() as conn:
        for count in (1, 5):
            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(INSERT_LATCH),
                    {"id": str(uuid.uuid4()), "ids": _uuid_array(count), "proposal": "{}"},
                )
            await conn.rollback()
        await insert_latch(conn)  # 2件(下限)は成功
        await conn.commit()


async def test_group_candidates_intent_ids_length_check(db_engine):
    """group_candidates.intent_idsは3≦n≦4(05 §2)。2・5は拒否・3は成功。"""
    insert = (
        "INSERT INTO group_candidates (intent_ids, status, created_at, updated_at) "
        f"VALUES (CAST(:ids AS uuid[]), 'candidate', {TS}, {TS})"
    )
    async with db_engine.connect() as conn:
        for count in (2, 5):
            with pytest.raises(IntegrityError):
                await conn.execute(text(insert), {"ids": _uuid_array(count)})
            await conn.rollback()
        await conn.execute(text(insert), {"ids": _uuid_array(3)})  # 下限は成功
        await conn.commit()


async def test_calibration_records_anonymization_check(db_engine):
    """anonymized_at NOT NULLならlatch_id・intent_idsはNULL(05 §2)。"""
    async with db_engine.connect() as conn:
        latch_id = await insert_latch(conn)
        base = {
            "intent_ids": None,
            "prediction": "{}",
            "snapshot": "{}",
            "responses": "[]",
        }
        # 未匿名化(latch_idあり)はOK
        await conn.execute(
            text(INSERT_CALIBRATION),
            {"latch_id": latch_id, "anonymized": None, **base},
        )
        # 匿名化済み(ID系NULL)はOK
        await conn.execute(
            text(INSERT_CALIBRATION),
            {"latch_id": None, "anonymized": "2026-09-27 12:00:00+00", **base},
        )
        await conn.commit()
        # 匿名化済みなのにlatch_idが残るのは違反
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(INSERT_CALIBRATION),
                {"latch_id": latch_id, "anonymized": "2026-09-27 12:00:00+00", **base},
            )
        await conn.rollback()


# --- #7: 型実効(geography・vector。05 §2・§3) ---


async def test_geography_point_insert_and_dwithin(db_engine):
    """geography(Point,4326)へのINSERT可・ST_DWithinが正しく距離判定する。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        intent_id = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO intents "
                "(id, user_id, category_primary, alcohol_involved, raw_text, "
                "geo_center, geo_radius_m, status, created_at, updated_at) "
                f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, "
                f"'raw', ST_SetSRID(ST_MakePoint(139.767, 35.681), 4326), 1000, "
                f"'draft', {TS}, {TS})"
            ),
            {"id": intent_id, "uid": user_id},
        )
        await conn.commit()
        near = (
            await conn.execute(
                text(
                    "SELECT ST_DWithin(geo_center, "
                    "ST_SetSRID(ST_MakePoint(139.767, 35.681), 4326), 500) "
                    "FROM intents WHERE id = CAST(:id AS uuid)"
                ),
                {"id": intent_id},
            )
        ).scalar()
        far = (
            await conn.execute(
                text(
                    "SELECT ST_DWithin(geo_center, "
                    "ST_SetSRID(ST_MakePoint(139.78, 35.69), 4326), 500) "
                    "FROM intents WHERE id = CAST(:id AS uuid)"
                ),
                {"id": intent_id},
            )
        ).scalar()
    assert near is True  # 同一地点(0m)は500m圏内
    assert far is False  # 約1.5km離れた地点は圏外


async def test_vector_insert_and_cosine_distance(db_engine):
    """vector(768)へのINSERT可・cosine距離演算(<=>)が動く。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        intent_id = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO intents "
                "(id, user_id, category_primary, alcohol_involved, raw_text, "
                "embedding, embedding_model, status, created_at, updated_at) "
                f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, "
                f"'raw', CAST(:vec AS vector), 'test-model/v1', 'draft', {TS}, {TS})"
            ),
            {"id": intent_id, "uid": user_id, "vec": VEC768},
        )
        await conn.commit()
        distance = (
            await conn.execute(
                text(
                    "SELECT embedding <=> CAST(:q AS vector) "
                    "FROM intents WHERE id = CAST(:id AS uuid)"
                ),
                {"q": VEC768, "id": intent_id},
            )
        ).scalar()
    assert distance is not None
    assert distance == 0.0  # 同一ベクトルとのcosine距離は0


async def test_vector_wrong_dimensions_rejected(db_engine):
    """768以外の次元はINSERT失敗(型のtypmod固定)。"""
    vec767 = "[" + ",".join(["0.001"] * 767) + "]"
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        with pytest.raises(DBAPIError) as excinfo:
            await conn.execute(
                text(
                    "INSERT INTO intents "
                    "(id, user_id, category_primary, alcohol_involved, raw_text, "
                    "embedding, status, created_at, updated_at) "
                    f"VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), 'meal', false, "
                    f"'raw', CAST(:vec AS vector), 'draft', {TS}, {TS})"
                ),
                {"id": str(uuid.uuid4()), "uid": user_id, "vec": vec767},
            )
        await conn.rollback()
    assert "dimension" in str(excinfo.value).lower()


# --- #9: DEFAULT実効(05明記分のみ。design §2.8) ---


async def test_users_profile_default(db_engine):
    """profile省略で '{}' が入る(05 §2)。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        profile = (
            await conn.execute(
                text("SELECT profile FROM users WHERE id = CAST(:id AS uuid)"),
                {"id": user_id},
            )
        ).scalar()
    assert profile == {}


async def test_intents_defaults(db_engine):
    """structured_data/participants/visibility/notification_level/versionのDEFAULT(05 §2)。"""
    async with db_engine.connect() as conn:
        user_id = await insert_user(conn)
        intent_id = await insert_minimal_intent(conn, user_id)
        row = (
            await conn.execute(
                text(
                    "SELECT structured_data, participants_min, participants_max, "
                    "visibility, notification_level, version "
                    "FROM intents WHERE id = CAST(:id AS uuid)"
                ),
                {"id": intent_id},
            )
        ).one()
    assert row[0] == {}
    assert (row[1], row[2]) == (2, 2)
    assert row[3] == "hidden_until_match"
    assert row[4] == "proposals_only"
    assert row[5] == 1


async def test_latches_and_group_candidates_defaults(db_engine):
    """latches.responses='[]' / group_candidates.member_scores='{}'(05 §2)。"""
    async with db_engine.connect() as conn:
        latch_id = await insert_latch(conn)
        responses = (
            await conn.execute(
                text("SELECT responses FROM latches WHERE id = CAST(:id AS uuid)"),
                {"id": latch_id},
            )
        ).scalar()
        gc_insert = (
            "INSERT INTO group_candidates "
            "(id, intent_ids, status, created_at, updated_at) "
            f"VALUES (CAST(:id AS uuid), CAST(:ids AS uuid[]), 'candidate', {TS}, {TS})"
        )
        gc_id = str(uuid.uuid4())
        await conn.execute(
            text(gc_insert), {"id": gc_id, "ids": _uuid_array(3)}
        )
        member_scores = (
            await conn.execute(
                text(
                    "SELECT member_scores FROM group_candidates "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": gc_id},
            )
        ).scalar()
    assert responses == []
    assert member_scores == {}


async def test_created_at_has_no_db_default(db_engine):
    """created_at省略はNOT NULL違反 — 時刻はClock由来の明示値のみ(design §2.8)。"""
    async with db_engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, display_name, birth_date, auth_provider, auth_subject, "
                    f"updated_at) "
                    f"VALUES (CAST(:id AS uuid), 't', DATE '2000-01-01', 'google', "
                    f"'s', {TS})"
                ),
                {"id": str(uuid.uuid4())},
            )
        await conn.rollback()
```

- [ ] **Step 2: 挙動検証を実行**

Run: `cd backend && uv run pytest tests/integration/test_schema.py -v`
Expected: 全25件(カタログ9件+挙動16件)PASS。DDLはTask 5で完成済みのため即GREEN想定

**もし FAIL した場合**: DDLまたは試験コードに誤りがある。DDL側の場合は `0001_initial_schema.py` を修正し、`cd backend && uv run alembic downgrade base && uv run alembic upgrade head` で適用し直して再実行する(§0の規律)。修正内容は報告ファイルに記録する

- [ ] **Step 3: lint・unit・test-ci を確認してコミット**

Run: `make lint && make test && make test-ci`
Expected: どちらも exit 0

```bash
git add backend/tests/integration/test_schema.py
git commit -m "test: スキーマ挙動検証(UNIQUE・CHECK・geography/vector型・DEFAULTの実効)"
```

### Task 7: 完了条件の検証と報告ファイル作成

**Files:**
- Create: `docs/plans/M0/ws-1-report.md`

**Interfaces:**
- Consumes: Task 1〜6の成果物一式
- Produces: 報告ファイル(スーパーバイザーのゲート判断材料)

- [ ] **Step 1: 完了条件5項目を検証**

§6の検証コマンドを順に実行し、出力の要点を控える:

```bash
make migrate && cd backend && uv run alembic current        # 条件1
make test-ci                                                # 条件2(§4.2の9検証を-v出力で確認)
cd backend && uv run pytest tests/integration/test_schema.py -v  # 条件2の明細
make lint && make test                                      # 条件3
git diff --name-only main -- prototype compose.yaml docker 'docs/0*.md' 'docs/1*.md' docs/reviews docs/plans/STATUS.md  # 条件5(出力空)
git log --oneline main..HEAD                                # コミット一覧(報告用)
```

- [ ] **Step 2: 報告ファイルを作成**

`docs/plans/M0/ws-1-report.md` を§8の形式どおり作成する。トレース表(条件4)は design.md §1.2対応表 #1〜#16 の各行について「実装箇所(0001_initial_schema.py 内のDDL/設定)→ 検証試験名」を記す

- [ ] **Step 3: コミット**

```bash
git add docs/plans/M0/ws-1-report.md
git commit -m "docs: ws-1実行報告(完了条件5項目の証拠)"
```

pushはしない。以降の統合(レビュー・マージ)はスーパーバイザーが行う。

## 8. 報告形式

**結果ファイル**: `docs/plans/M0/ws-1-report.md`(scaffold-report.md と同形式)

**記載内容**:

1. **ヘッダ**: ブランチ(`m0-ws-1`)/ ベースコミット(mainからworktreeを切った時点のsha)/ 日付 / 実行者(agent3)
2. **完了条件の検証結果表**: §6の5項目それぞれについて「条件 / 結果(PASS・FAIL) / 証拠(コマンド出力の要点)」
3. **トレース表(条件4)**: design.md §1.2対応表 #1〜#16 → 実装箇所 → 検証試験名(design §4.2対応表 #1〜#9 の試験名に対応づける)
4. **design §6 論点の扱い**: 4論点それぞれ「推奨どおり(変更なし)」または「変更前→変更後+理由」
5. **設定追記の記録**: ruff per-file-ignores(alembicのE501)など、計画書で予告した設定変更の実施記録。計画書から外れた判断をした場合はすべてここに書く
6. **コミット一覧**: `git log --oneline main..HEAD` の出力
7. **補足**: 詰まった点・判断した点・DDL修正が発生した場合はその内容と理由

## 9. Self-Review 記録(計画書作成時点の確認)

- **仕様カバレッジ**: design.md §1.2 の16確定値はすべて§1参照仕様節・§2グローバル制約・Task 5のDDL・Task 4/6の試験のいずれかに対応(#1〜#3型と時刻→DDL・#4テーブル→DDL+#2検証・#5 friendships不在→DDL+#2検証・#6 Index→DDL+#3検証・#7 idempotency→式UNIQUE+#4/#5検証・#8遅延Event→FKなしDDL+#8検証・#9 CHECK方針→DDL+#6検証・#10 status値域アプリ層→DDL(CHECKなし)+設計根拠・#11 vector(768)→DDL+#7検証・#12削除方針→FK NO ACTION+latches/calibrationスキーマ・#13 clock_timestamp不使用→DEFAULTなしDDL+#9検証・#14 ci常設→test-ci構成・#15 CREATE EXTENSION→DDL+#1検証・#16 Python 3.13→依存バージョン)
- **プレースホルダスキャン**: 全ステップに実行コマンドと完全なコードを掲載。「 TBD」「後で」「適切に」なし
- **型整合**: `create_db_engine(Settings()) -> AsyncEngine`(Task 3)を conftest(Task 3)・設計どおりM1が消費。`Settings().database_url`(Task 1)を env.py(Task 2)・db.py(Task 3)が消費。fixture `db_engine` を test_db_connection(Task 3)・test_schema(Task 4/6)が消費。FK名・テーブル名は Task 5 のDDLと Task 4 の EXPECTED_FKS/EXPECTED_TABLES で完全一致
- **Review Focus**: 5項目すべてに所有タスクと手段を割り当て済み(§3)
