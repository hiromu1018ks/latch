# M0 ws-1(PostgreSQLスキーマ+Index+マイグレーション)実行報告

- **ブランチ**: `m0-ws-1`
- **ベースコミット**: `e21138c9809aff10d0cc6a878405c9cd140c8fba`(main側 `docs: M0 ws-2の実装計画書を追加`)
- **日付**: 2026-09-27
- **実行者**: agent3(superpowers:executing-plans + test-driven-development)
- **計画書**: `docs/plans/M0/ws-1-plan.md` / **設計**: `docs/plans/M0/ws-1-design.md`

## 1. 完了条件の検証結果(design §5・計画書§6)

| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | `make migrate` がci環境DBで成功し `alembic current` が head | **PASS** | `make migrate` exit 0 / `uv run alembic current` → `0001 (head)` |
| 2 | `make test-ci` で design §4.2 の9検証がグリーン | **PASS** | `make test-ci` → `62 passed`(unit 32 + integration 30)。`pytest tests/integration/test_schema.py -v` → 25件すべて PASSED(#1〜#9対応の試験は下表トレース参照) |
| 3 | `make lint` / `make test` がグリーン(既存試験への回帰なし) | **PASS** | lint → `24 files already formatted` / `All checks passed!`。test → `32 passed, 30 deselected`(雛形既存33件中3件はintegration側で計上。unit 32件+追加2件とも green) |
| 4 | 05 §2〜§3・06 §9 の全要素がスキーマへ反映(§1.2対応表でトレース可能) | **PASS** | 下記トレース表(design §1.2 #1〜#16 → 実装箇所 → 検証試験名) |
| 5 | `prototype/`・`docs/01〜12`・`compose.yaml`・`docker/` に差分なし | **PASS** | `git diff --name-only main -- prototype compose.yaml docker 'docs/0*.md' 'docs/1*.md' docs/reviews docs/plans/STATUS.md` → 出力空(exit 0) |

## 2. トレース表(design §1.2対応表 #1〜#16 → 実装箇所 → 検証試験)

`0001` = `backend/alembic/versions/0001_initial_schema.py`。

| design §1.2 # | 確定値 | 実装箇所 | 検証試験(design §4.2対応) |
|---|---|---|---|
| 1 | RDBMS=PostgreSQL(+PostGIS)・Geo Index/JSONB/pgvector併用 | ci-dbイメージ(雛形)+0001全DDL(PostgreSQL型名・JSONB) | #2 `test_twelve_tables_exist` / `test_db_connect_returns_postgres17` |
| 2 | Vector DBはpgvector・Vector検索はリポジトリ層で抽象化(M1) | 0001 `intents.embedding vector(768)` + HNSW索引(ws-1はDB実体のみ) | #1 `test_extensions_installed` / #7 `test_vector_insert_and_cosine_distance` |
| 3 | カラム型はPostgreSQL型名・時刻timestamptz(JST運用)・金額は整数(円) | 0001 全テーブル(`budget_max integer`・`created_at timestamptz NOT NULL` 等) | #2 `test_twelve_tables_exist` / #7 `test_geography_point_insert_and_dwithin` |
| 4 | 12テーブルのカラム・型・制約・DEFAULT(05 §2どおり) | 0001 CREATE TABLE ×12(カラム順は05 §2の表順) | #2 `test_twelve_tables_exist` / #9 DEFAULT試験4件 / `test_unique_constraints_exist` |
| 5 | friendshipsはMVP対象外・テーブルを作らない | 0001 にfriendshipsのDDL不在 | #2 `test_friendships_table_absent` |
| 6 | Index 7本(部分Index 4本)+GIST+HNSW・05 §3のSQLそのまま | 0001 CREATE INDEX ×7(idx_intents_*) | #3 `test_intents_indexes_exist_with_partial_where` / `test_index_access_methods` |
| 7 | match_events UNIQUE(event_type, source_intent_id, payload内version)=idempotencyキー | 0001 `ux_match_events_idempotency`(式UNIQUE索引・`(payload->>'version')` はtextのまま) | #4 `test_match_events_idempotency_is_expression_unique_index` / #5 `test_match_events_duplicate_version_rejected`・`test_match_events_different_version_allowed`・`test_match_events_missing_version_duplicates_allowed` |
| 8 | 削除済みIntentへの遅延Eventは正当として保存(FKを付けない) | 0001 `match_events.source_intent_id uuid NOT NULL`(FKなし) | #8 `test_match_events_has_no_foreign_key` / `test_expected_foreign_keys`(14本) |
| 9 | CHECK方針: 3列CHECK IN+intent_ids長CHECK+匿名化一貫CHECKはDB強制。geo_centerのactive NULL不可・同一user検査はアプリ層 | 0001 CHECK句(auth_provider / visibility / notification_level / latches 2≦n≦4 / group_candidates 3≦n≦4 / calibration 2≦n≦4 / 匿名化一貫) | #6 `test_users_auth_provider_check`・`test_intents_visibility_check`・`test_intents_notification_level_check`・`test_latches_intent_ids_length_check`・`test_group_candidates_intent_ids_length_check`・`test_calibration_records_anonymization_check` |
| 10 | status値域はアプリ層の遷移管理(CHECKにしない) | 0001 `status text NOT NULL`(CHECKなし) | #2 DDL実体 + #9 `test_intents_defaults`(status='draft'がCHECKなしでINSERT可) |
| 11 | embedding は vector(768)・embedding_model は識別子+版 | 0001 `embedding vector(768)`・`embedding_model text` | #7 `test_vector_insert_and_cosine_distance`・`test_vector_wrong_dimensions_rejected` |
| 12 | 削除・退会方針(候補削除・latchesはcancelledで履歴維持・calibrationは匿名化維持) | 0001 FK全NO ACTION(デフォルト)・`latches.completed_at` NULL可・`calibration_records.anonymized_at`+匿名化一貫CHECK | #8 `test_expected_foreign_keys` / #6 `test_calibration_records_anonymization_check` |
| 13 | APIの期限判定にDBのclock_timestamp()を使わない(時刻はClock経由) | 0001 時刻列にDB時刻関数DEFAULTなし(uuid PKのgen_random_uuid()のみ付与) | #9 `test_created_at_has_no_db_default` / 既存arch test `test_arch_no_direct_time`(毎コミット green) |
| 14 | ci環境は常設最小構成・機能試験を毎コミットで実行 | Makefile `test-ci`(compose起動→pytest)・integration conftest(sessionスコープ upgrade head) | `make test-ci` → 62 passed(毎コミットで実施) |
| 15 | CREATE EXTENSION はws-1の移行で実施 | 0001 `CREATE EXTENSION IF NOT EXISTS postgis` / `vector`(downgradeではDROPしない) | #1 `test_extensions_installed` |
| 16 | DB系エコシステム(asyncpg・SQLAlchemy・Alembic)はPython 3.13選定済み | `backend/pyproject.toml`(requires-python>=3.13・sqlalchemy[asyncio]>=2.0 / asyncpg>=0.30 / alembic>=1.14) | `make lint && make test`(uv run = 3.13実行) |

## 3. design §6 論点の扱い(4論点)

| 論点 | 扱い |
|---|---|
| 1. latches.created_at の制約欄空白 | **推奨どおり(変更なし)** — 0001 は `created_at timestamptz NOT NULL` / `completed_at timestamptz NULL` |
| 2. reports.status の値域・blocks の一意性 | **推奨どおり(変更なし)** — statusはCHECKなし・blocksにUNIQUEなし(M3で追加マイグレーション対応) |
| 3. embedding 次元 768 | **推奨どおり(変更なし)** — `vector(768)` のまま(T1確定時に要変更ならALTER追加移行) |
| 4. 本番・stagingでのマイグレーション実行主体 | **推奨どおり(変更なし)** — 明示実行(`make migrate`)のみ。実行主体はM4/11 |

## 4. 設定追記の記録(計画書予告分 + 計画書から外れた判断)

計画書予告どおり実施:

- `backend/pyproject.toml` に `[tool.ruff.lint.per-file-ignores]` `"alembic/*" = ["E501"]`(DDL長行対策。Task 2)
- `Makefile` に `migrate` ターゲット追加(.PHONY 行更新。Task 2)
- `backend/README.md` にマイグレーション手順セクション追記(Task 2)

計画書から外れた判断(すべて ledger 記録済み・要contextは§5補足):

1. **`sqlalchemy>=2.0` → `sqlalchemy[asyncio]>=2.0`**(Task 2)— SQLAlchemy 2.x のasyncモジュールは greenlet 必須。`alembic current` が `ModuleNotFoundError: greenlet` で即死するため、design §2.3「SQLAlchemy 2.x(async)」を実装する公式extras指定へ。追加パッケージは greenlet のみで、「依存3つのみ」制約の趣旨(geoalchemy2・pgvector-python導入禁止)は不変
2. **env.py `with connectable.connect()` → `async with`**(Task 2)— AsyncConnection は同期コンテキストマネージャ非対応(TypeError)。公式asyncテンプレートどおりの表記(design §2.1「生成テンプレートをほぼそのまま」)
3. **alembic.ini `script_location` は生成物既定 `%(here)s/alembic` を維持**(Task 2)— 計画書コードブロックの `script_location = alembic` は alembic 1.14系テンプレートの出力形であり、導入版 1.20 の生成物は `%(here)s/alembic`。指示本文「sqlalchemy.url の行を削除(他セクションは既定のまま)」に従い生成物既定を維持(機能等価・ini位置基準でカレントディレクトリ非依存)
4. **conftest `BACKEND_DIR = parents[1]` → `parents[2]`**(Task 3)— conftestは `backend/tests/integration/` 直下のため parents[1] は `backend/tests` を指し、alembic.ini が解決できない(CommandError: No 'script_location' key)
5. **`create_async_engine(url, server_settings=...)` → `connect_args={"server_settings": ...}`**(Task 3)— SQLAlchemy 2.x はasyncpg接続引数を connect_args 経由で渡す(TypeError: Invalid argument 'server_settings')。design §2.3 の意図(server_settings UTC固定)は不変
6. **`idx_intents_expires` のWHERE句検査を両形式受け入れへ**(Task 5)— PostgreSQL 17 の indexdef は `IN ('draft',...)` を `ANY (ARRAY['draft'::text,...])` へdeparseするため、計画書の部分一致 `"status IN ('draft'"` が不成立。Review Focus #5 の指針(内部正規化への過剰依存を避ける)どおりスペース除去+両形式チェック。DDL修正は不要(適用済みindexdefは想定どおり)
7. **`_uuid_array` は配列リテラル文字列でなく `list[uuid.UUID]` を返す**(Task 6)— asyncpg の uuid[] バインドは `CAST(:x AS uuid[])` 付きでも文字列を拒否(sized iterable expected)。Review Focus #3「CASTで受ける」規約は uuid/jsonb/vector には有効だが uuid[] には不十分だった。テストユーティリティの変更でDDL無関係
8. **timestamptzバインドは文字列でなく datetime**(Task 6)— `CAST(:anonymized AS timestamptz)` への str バインドも asyncpg は datetime 要求。`ANON_TS`(TSリテラルと同instant)を導入
9. **Task 4 RED確認の実績は7 FAIL+2 PASS**(計画書Expected「9件すべてFAIL」)— friendships不在・match_events FKなしの否定形検証2件は空DBで必然真(検証内容は正しくTask 5適用後に意味を持つ)。計画書Expected行の記載不正確
10. **docstring・SQL断片のE501折り返し数件**(Task 3・4・6)— 全角docstring・長い列リスト行が ruff 88文字制限を超過。折り返しのみで意味不変

## 5. コミット一覧

```
6006215 test: スキーマ挙動検証(UNIQUE・CHECK・geography/vector型・DEFAULTの実効)
6dc3990 feat: 0001初期スキーマ(PostGIS+pgvector拡張・12テーブル・Index・idempotency式UNIQUE)
b264910 test: スキーマカタログ検証(拡張・12テーブル・Index・式UNIQUE・FK)のRED
99c6372 feat: core/db.pyエンジン工場(UTC固定)とintegration接続基盤(conftestでalembic upgrade head)
e90ed38 feat: Alembic環境(asyncテンプレート・URLはlatch.settings一元化・make migrate)
e346c05 feat: DB系依存(sqlalchemy/asyncpg/alembic)とSettings.database_url
```

## 6. 補足

- **DDL修正は不発生**: Task 5 の `0001_initial_schema.py` は初回適用で `alembic current` → `0001 (head)`・カタログ検験9件 GREEN(§0のdowngrade→upgrade再適用規律は未発動)。検証試験側の修正のみ(§4の6〜8)
- **TDD実績**: Task 1(2試験 RED→GREEN)/ Task 3(2試験 RED→GREEN)/ Task 4(9試験 RED — 7 FAIL 2 PASS → Task 5でGREEN)/ Task 5(実装)/ Task 6(16試験、DDL完成済みのため即GREEN想定どおり — ただしasyncpgバインド規約の不足で4+1件の試験コード修正が発生)
- **毎コミット検査**: 全6コミットで `make lint && make test` green を確認(test-ci もTask 3・5・6で実施)
- **マイグレーション実行**: `make migrate`(明示実行)のみ。API/Worker起動時自動実行なし・compose.yaml への `LATCH_DATABASE_URL` 注入なし(M1)
- **最終レビュー**(別モデル・独立検証: psqlによる実カタログ突合・integration再実行・migration編集履歴確認): Critical 0 / Important 0 / 判定 **Ready to merge: Yes**。Minor 5件は後続単位への引継ぎ(下記)
- **スーパーバイザーへの引継ぎ事項(レビュー勧告)**:
  - **並列worktree×常設ci-dbのマイグレーションhead分岐** — ws-4等が別worktreeで 0002 を追加すると、共有ci-dbの `alembic_version` がworktree間で行き来し、他方のworktreeの `upgrade head`/conftest が "Can't locate revision" 系エラーで落下する可能性。マージ前に `alembic downgrade base` で揃える運用か、worktree毎のDB/スキーマ分離のいずれかをM0中に決定し開発READMEかSTATUSへの明記を推奨(ws-1単体では不発生)
- **後続単位へのMinor引継ぎ**(fix pass対象外・計画書どおりの範囲):
  1. `pytest.raises(IntegrityError)` に SQLSTATE(23505/23514)のピン留めなし(design §4.2が番号を明記。将来のスキーマdriftで偽陽性窓)→ M1で試験強化
  2. arch test(`test_arch_no_direct_time.py`)のスキャン範囲は `src/latch` のみで `alembic/env.py` を含まない — env.py自体は現状クリーンだが、雛形既存試験は§5禁止のためws-1では触れられず。M1でスキャン対象追加を検討
  3. 匿名化一貫CHECKの試験は latch_id 分岐のみ(intent_ids NOT NULL 分岐は未検証。design §4.2 #6の記載どおり)
  4. `env.py` の `if connectable is None: raise RuntimeError` は到達不能(`async_engine_from_config` はNoneを返さない)— 計画書掲載コードのまま無害
  5. DDLがスキーマ非修飾(`public.` なし)。ci環境(owner+search_path=public)で動作実証済み。M4の本番実行主体検討時にschema修飾/`search_path`明示を議論
- **push なし・main へのコミットなし**(マージはスーパーバイザー待ち)
