# M0 ws-1(PostgreSQLスキーマ+Index+マイグレーション)設計メモ

- 作業単位: ws-1(docs/plans/STATUS.md M0表「PostgreSQLスキーマ+Index+マイグレーション(friendshipsはEntity定義のみ)」/ 出典 M0-2 / 依存ハード制約 C1 / 前提: 雛形=完了済み d2b53d7)
- 作成: 2026-09-27(agent1 / superpowers:brainstorming使用)
- 次工程: agent2が本書を `ws-1-plan.md` へ変換 → agent3がworktree内でTDD実装
- 本書の役割: 05 §2〜§3(スキーマ・Index)・06 §9(idempotency)・04 §3(DB技術スタック)を「実装者が迷わず写せる形」へ固定する。仕様の再解釈は行わない

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

依存ハード制約**C1(PostgreSQL+PostGIS+pgvector上でのGeo判定・Vector検索の土台)**を満たすスキーマ実体を、版管理されたマイグレーションとして作る。ws-3(users)・ws-4(地物テーブル)・M1〜M3(ドメイン実装)がすべて同じスキーマ定義を参照して動くようにする。**ws-1が作るのはDB側の実体(DDL)と接続基盤のみ**であり、Python側のモデル定義・リポジトリ層は最初にDBを消費する単位(M1)まで作らない。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | RDBMSはPostgreSQL(+PostGIS)。Geo Index・JSONB・pgvector併用のため | 04 第3節 RDBMS行 |
| 2 | Vector DBはpgvector(コンポーネント数最小・Intentとのトランザクション整合・バックアップ一元化)。**Vector検索はリポジトリ層で抽象化**し専用Vector DB移行を可能にする | 04 第3節 Vector DB行・第2節「DBを1本化」 |
| 3 | カラム型はPostgreSQLの型名で書く。時刻は**timestamptz(JST運用)**、金額は整数(円) | 05 冒頭取り決め |
| 4 | テーブル: users / intents / match_candidates / group_candidates / latches / match_events / latch_status_events / blocks / reports / notifications / messages / calibration_records の12テーブル。カラム・型・制約・DEFAULTは05第2節の表のとおり | 05 第2節 |
| 5 | **friendshipsはMVP対象外。テーブルを作らない**(v0.4で実装から外れ、Entity定義は仕様書上にのみ残置。MVPの参照経路は存在しない) | 05 第1節・第2節 friendships行 / 02 v0.3 D-03再決定 |
| 6 | Index 7本(部分Index 4本含む)+ GIST 1本 + HNSW 1本。**05第3節のSQLをそのまま**用いる(idx_intents_matching / idx_intents_geo / idx_intents_budget / idx_intents_participants / idx_intents_expires / idx_intents_user / idx_intents_embedding) | 05 第3節 |
| 7 | match_eventsのUNIQUE(event_type, source_intent_id, payload内version)= **idempotencyキー(06第9節手順2)。DBレベルで重複を排除する** | 05 第2節 match_events行・06 第9節 |
| 8 | 削除済みIntentへの参照Eventは**正当な遅延Eventとしてstatus=processedで保存・破棄**する。payload不正はquarantinedへ(失敗理由をpayloadに保持) | 06 第9節 |
| 9 | intents.geo_center の「active行ではNULL不可」は**DB CHECKではなくアプリ層+active化経路で保証**(draftはNULL可)。group_candidatesの同一user_id検査も**INSERT前検査+アプリ層**(配列参照のためCHECKで書けない)。逆に intent_ids 配列長CHECK(2≦n≦4 / 3≦n≦4)とcalibration_recordsの匿名化NULL一貫CHECKはDBで強制する | 05 第2節 各行 |
| 10 | status列の値列挙(Intent遷移6値・LATCH 8値等)は**状態遷移表(05第6節)でアプリ層管理**。users.auth_provider・intents.visibility・notification_levelのみ「CHECK IN」を明記 | 05 第2節・第6節 |
| 11 | embedding は vector(768)(拡張次元はEmbeddingモデル確定時に固定)。embedding_model は識別子+版 | 05 第2節 |
| 12 | 削除・退会時: 候補は処理済み含め削除、latchesはcancelledで閉じ**履歴として維持**、calibration_recordsは**匿名化して維持**(行削除しない)。候補2テーブルは評価確定から30日定期削除 | 08 第2.5節 |
| 13 | APIの期限判定に**DBのclock_timestamp()を使わない**。時刻参照はすべてClock経由 | 10 第1節「時刻操作」(1) / 04 第5節 FR-41 |
| 14 | ci環境は「最小構成(常設)。API 1・Worker 1・DB共用」。機能試験を毎コミットで実行 | 10 第1節 / 12 第8節 運用ル則5 |
| 15 | DBイメージ(postgres:17 + PostGIS 3.6.4 + pgvector 0.8.6同梱)は雛形で作成済み。**CREATE EXTENSION はws-1の移行で実施する**ことが確定指示 | STATUS.md 完了記録 / scaffold-design §1.3・§2.5 |
| 16 | ws-1が導入するDB系エコシステム(asyncpg・SQLAlchemy・Alembic)のPythonバージョンは3.13固定で選定済み | scaffold-design §2.3 |

### 1.3 スコープ外(後続単位へ渡すもの。ws-1では作らない)

- **Python側のモデル定義(SQLAlchemy MetaData / Pydantic Entity)・リポジトリ層** — M1。geoalchemy2・pgvector-pythonの導入もその時(本設計§2.4)
- API/WorkerプロセスへのDBランタイム統合(lifespanでのエンジン生成・`app.state.db` DI)— DBを最初に消費する単位(M1。ws-3の認証はRedis+JWKSでDB未消費)。composeへの `LATCH_DATABASE_URL` 注入もその時
- 地物テーブル(位置参照情報・OSM取り込み)— ws-4。本移行で作るpublicスキーマに後追い追加される
- alembic autogenerate によるモデル↔スキーマ自動追従運用 — M1(モデル導入時)
- staging/prodでのマイグレーション実行主体(デプロイパイプライン)— M4 / 11
- `docker/postgres/Dockerfile`(拡張同梱済みのため変更不要)・`compose.yaml`(api/workerはまだDBを消費しないため変更不要)

## 2. 実装方式の選択肢と推奨

### 2.1 マイグレーション手法 — 推奨: Alembic

選択肢:

- **A(推奨). Alembic(SQLAlchemyエコシステムの標準マイグレーションツール)**
- B. SQLファイル管理ツール(dbmate・sqitch・yoyo等)+ 生SQL
- C. `docker-entrypoint-initdb.d` へのSQL配置(マイグレーションツールなし)

推奨の根拠: AはPython/FastAPIエコシステムの事実上の標準であり、雛形設計もws-1の想定として名指ししていた(scaffold-design §2.3)。**バージョン表(alembic_version)による冪等適用**が、常設ci環境の名前付きボリューム(`latch-ci-db`。初期化はもう行われない)への中間追加DDLを安全にする — Cは空DBの初回起動しか扱えず不採用。Bでも目的は達するが、§2.3でSQLAlchemyを接続に使うためツールだけ別系統を導入する理由がない。downgrade手順の標準装備も、試験環境のリセット手段として有用。

トレードオフ: SQLAlchemyへの依存が必須になる(Alembic自体が要求)。async URL のため env.py が公式asyncテンプレート起步となり定型的に長くなる — 生成テンプレートをほぼそのまま使い、URL取得だけ `latch.settings` 差し替えに留める。

### 2.2 DDLの表現 — 推奨: `op.execute()` の生SQL

選択肢:

- **A(推奨). マイグレーション内のDDLを `op.execute()` の生SQLで書く(05のSQLを1:1で写す)**
- B. `op.create_table()` 等のSQLAlchemy APIで書く(+geoalchemy2・pgvector-pythonの型サポート)

推奨の根拠: 05第2〜3節はPostgreSQL型名(uuid / jsonb / timestamptz / geography(Point,4326) / vector(768) / uuid配列 / 部分Index / GIST / HNSW / 式索引)で直接規定されており、生SQLは**仕様→実装の写像が1:1**で誤訳の余地が最小。BはPostGIS型・pgvector型の表現にgeoalchemy2・pgvector-pythonが必須となるが、それらは「モデル定義」のためのライブラリで、モデルを作らないws-1では依存する理由がない(§2.4)。将来autogenerateを使い始めても比較対象はDB実スキーマなので、生SQLで作ったオブジェクトの追跡性は失われない。

トレードオフ: 型安全性なし(誤りは実行時まで分からない)→ §4のintegration試験で実DB上の検証により補う。

### 2.3 DB接続 — 推奨: SQLAlchemy 2.x(async)+ asyncpg

選択肢:

- **A(推奨). SQLAlchemy async engine(`postgresql+asyncpg://`)**。`core/db.py` に工場関数のみ置く
- B. asyncpg を直接使う(AlembicのみSQLAlchemy)
- C. psycopg(3)系ドライバ

推奨の根拠: Alembicが§2.1によりSQLAlchemyを要求するため、接続も同一経路に統一するのが管理面を最小にする。FastAPIのasync前提と整合し、M1以降のリポジトリ層(04第3節「Vector検索はリポジトリ層で抽象化」の受け皿)がSQLAlchemy Core/Query on async engineで自然に書ける。BはAlembicとアプリで接続管理が二系統になる。Cも有力だが、エコシステムの主流(非同期+SQLAlchemy+Alembic)から外れる理由がない。

トレードオフ: 依存が `sqlalchemy` `asyncpg` `alembic` の3つ増える。将来psycopg系への乗換はURLスキーマ差し替えに閉じる(接続コードは工場1関数)。

エンジン生成は `core/db.py` に1関数(`create_db_engine(settings)`)だけ置く。**API/WorkerへのDIは行わない**(§1.3)。タイムゾーンは `server_settings={"timezone": "UTC"}` で接続をUTCに固定する(timestamptzはinstantで保持され、JST運用はアプリ層の表現。Clock契約 now()=tz-aware UTC と同じ規約)。

### 2.4 Python側モデル定義 — 推奨: ws-1では作らない(マイグレーションが真実源)

選択肢: (a) ws-1でSQLAlchemy MetaData(12テーブル分)を定義してマイグレーションと二重管理 / **(b) 推奨: マイグレーションのみ**。

推奨の根拠: ws-1にはモデルの消費者がいない(リポジトリ層はM1)。モデルを先に作ると05仕様との突合が二重になり、拡張型対応(geoalchemy2等)まで前倒しになる。**friendshipsの「Entity定義のみ残置」は05仕様書上の話であり、実装コードには何も残さない**(コメント付きのモデル骨格等も、存在しないテーブルの定義として混乱を招くため置かない)。将来の復帰は05第2節 friendships行の定義から新規マイグレーションで作る。

トレードオフ: M1でモデル導入時に「モデル↔実スキーマ」の突合コストが発生する → M1側の試験(実DBに対するCRUD試験)で自然に検証される。

### 2.5 match_events の idempotency UNIQUE — 推奨: 式UNIQUE索引(textのまま)

05は「UNIQUE(event_type, source_intent_id, payload内version)」という**JSON内列を含む一意性**を要求する。実装は次の式UNIQUE索引とする(postgresの生成列は05にない列を増やすため使わない):

```sql
CREATE UNIQUE INDEX ux_match_events_idempotency ON match_events
  (event_type, source_intent_id, (payload->>'version'));
```

- `(payload->>'version')` はtext。**integerへのキャストを付けない**: 付けると version が数値でない毒ペイロード(`"version": "abc"` 等)のINSERT自体が索引評価エラーで失敗し、**「失敗理由をpayloadに保持してquarantinedへ保存する」経路(06第9節)がDB側で塞がれる**。textのままであれば構造違反の値も保存でき、version欠落(NULL)はUNIQUE索引の対象外(重複排除されない)という挙動も「version不正のEventを隔離保管する」運用と整合する
- `'1'` / `'01'` の表記差によるすり抜けは、payload.version を**JSON number として必ず生成する**(アプリ側規約。M2のEvent発行実装に課す)ことで封じる。JSON numberのtext化は正規化されるため実害はない
- index名 `ux_match_events_idempotency` は05に命名がないため本設計で固定する

### 2.6 match_events.source_intent_id — FKを付けない(設計判断)

06第9節は「削除済みIntentへの参照Eventは正当な遅延Eventとしてstatus=processedで保存」を要求する = **Intent削除後に同Intentを参照するEvent行のINSERTが発生し得る**。FK制約だとこのINSERTが違反になる。05第2節の同表もFK表記を持たない。よって `source_intent_id uuid NOT NULL` はFKなしの列とする(検証も§4で実施)。それ以外の参照列(latch_status_events.latch_id 等)は05のER(第1節)どおりFKを付ける。

### 2.7 FKの ON DELETE — 推奨: すべてデフォルト(NO ACTION)

08第2.5節の削除は**アプリ層の順序制御**で行う(候補は処理済み含め削除・latchesはcancelledで閉じて履歴維持・calibration_recordsは匿名化して行維持)。CASCADEはこの制御をバイパスし、SET NULLは履歴の紐付きを切る。よって全FKをデフォルト(NO ACTION)とし、削除順序の誤りはFK違反として最後の砦で検出する。

### 2.8 DEFAULT・CHECK・時刻列の方針 —「05の明記のみ」

- **DEFAULT**: 05にDEFAULT明記のある列のみ(structured_data `'{}'` / profile `'{}'` / member_scores `'{}'` / responses `'[]'` / participants_min・max `2` / visibility / notification_level / version `1`)。それ以外に付けない
- **created_at / updated_at にDB時刻関数のDEFAULTを付けない**: 時刻の生成もClock由来の明示値で挿入させる(10第1節「DBのclock_timestamp()を使わない」の思想をDDLまで一貫させる。テストデータもClock差し替えの下で決定的になる)
- **uuid PK には `DEFAULT gen_random_uuid()` を付ける**: 05はUUIDの生成方法を規定しない(=実装自由)。INSERT側の明示値を妨げないまま、テスト・将来の実装の利便を確保する
- **CHECK**: 05が「CHECK IN」と明記した3列(auth_provider / visibility / notification_level)と、明記のある配列長CHECK(latches 2≦n≦4・group_candidates 3≦n≦4・calibration_records 2≦n≦4)とcalibration_recordsの匿名化一貫CHECK(`anonymized_at IS NULL OR (latch_id IS NULL AND intent_ids IS NULL)`)のみ。status値域はアプリ遷移管理(05第6節)なのでCHECKにしない(値追加時にDDL変更を強いられない)
- **geo_center の active NULL不可・group_candidates の user_id一意はCHECKにしない**(05明記のとおりアプリ層。§1.2 #9)

### 2.9 概略表テーブルの詳細確定(解釈表)

05第2節の match_events / latch_status_events / blocks / reports / notifications / messages は「主要カラム」の概略表記のため、次の解釈で固定する(05に詳細が確定した時点で必要ならマイグレーション追加):

| テーブル | 解釈(確定させる列定義) |
|---|---|
| match_events | id PK / event_type text NOT NULL / source_intent_id uuid NOT NULL(FKなし §2.6) / payload jsonb NOT NULL / status text NOT NULL(pending/processed/quarantined。CHECKなし) / created_at timestamptz NOT NULL / processed_at timestamptz NULL + §2.5の式UNIQUE索引 |
| latch_status_events | id PK / latch_id uuid NOT NULL FK→latches / from_status text NULL(candidate作成時はNULL) / to_status text NOT NULL / user_id uuid NULL(システム起因はNULL) / created_at timestamptz NOT NULL |
| blocks | id PK / blocker_id・blocked_id uuid NOT NULL FK→users / created_at NOT NULL。UNIQUE(blocker_id, blocked_id)は05に明記がないため付けない(重複防止はアプリ層) |
| reports | id PK / reporter_id・reportee_id uuid NOT NULL FK→users / latch_id uuid NULL FK→latches(LATCH文脈を欠く通報も想定) / reason text NOT NULL / status text NOT NULL(値域はM3で確定。CHECK・DEFAULTなし) / created_at NOT NULL |
| notifications | id PK / user_id uuid NOT NULL FK→users / type text NOT NULL / payload jsonb NOT NULL / read_at timestamptz NULL / created_at NOT NULL |
| messages | id PK / latch_id uuid NOT NULL FK→latches / sender_id uuid NOT NULL FK→users / body text NOT NULL / created_at NOT NULL |
| latches の時刻列 | 05の制約欄空白(created_at / completed_at)は、他テーブルがすべて created_at NOT NULL を明記する表記整合から **created_at NOT NULL / completed_at NULL(completed遷移時のみ設定)** と解釈する |

### 2.10 Indexの範囲 — 05第3節・06第9節由来のみ

作るIndex: 05第3節の7本(idx_intents_matching・idx_intents_geo・idx_intents_budget・idx_intents_participants・idx_intents_expires・idx_intents_user・idx_intents_embedding。SQLはそのまま)+ 06第9節由来の ux_match_events_idempotency(§2.5)+ UNIQUE制約に伴う暗黙索引(uq_users_auth_provider_subject・uq_match_candidates_intent_versions)。**FK列への追加Index等は作らない**(性能要件は10第4節の試験時に必要になった時点で追加マイグレーション)。HNSW索引にWHERE句は不要(pgvectorのHNSWはNULL行を格納しない。05のSQLどおり)。

### 2.11 マイグレーションの分割 — 1件(初期スキーマ)

`0001_initial_schema` に「CREATE EXTENSION(postgis, vector)→ 12テーブル(FK依存順)→ Index・UNIQUE」を収める。05第2〜3節が一括確定しており分割根拠がない。downgradeはテーブルを逆順DROPし、**拡張はDROPしない**(publicの拡張はws-4の地物テーブル等が共有するリソースであり、消す意味がdowngradeにない)。CREATE EXTENSIONは `IF NOT EXISTS` 付きで冪等化する(ci環境のdbユーザーは公式postgresイメージの初期化ユーザー=スーパーユーザーのため作成可能。マネージド本番では拡張作成権限を持つロールでの実行を前提に注記)。

### 2.12 マイグレーションの実行方式 — 推奨: 明示実行(make migrate)

- `make migrate`(= `uv run alembic upgrade head`)をオペレータが明示実行。**API/Workerコンテナの起動時自動実行はしない**(失敗時の再起動ループ・複数コンテナ間の実行競合を避ける)
- integration試験のconftestはsessionスコープで `alembic.command.upgrade(head)` を呼ぶ(冪等)。よって `make test-ci` のフロー変更は不要(compose起動→pytest、のまま)
- URLは `alembic.ini` に置かず `env.py` から `latch.settings.database_url` を読む(設定の一元化。pydantic-settingsが環境変数 `LATCH_DATABASE_URL` を優先)

## 3. ファイル構成

### 3.1 作るもの(実装agent3が触る範囲)

```text
/ (repo root)
├── Makefile                           # +migrate ターゲット(upgrade head)
└── backend/
    ├── pyproject.toml                 # deps追加: sqlalchemy>=2.0 / asyncpg>=0.30 / alembic>=1.14
    ├── uv.lock                        # 再生成(コミット)
    ├── README.md                      # マイグレーション手順(make migrate)を追記
    ├── alembic.ini                    # 最小(script_location等。URLはenv.pyに委譲)
    ├── alembic/
    │   ├── env.py                     # 公式asyncテンプレート起步。URL=latch.settings.database_url
    │   ├── script.py.mako
    │   └── versions/
    │       └── 0001_initial_schema.py # CREATE EXTENSION + 12テーブル + Index(§2の確定値どおり)
    ├── src/latch/
    │   ├── settings.py                # +database_url(デフォルト postgresql+asyncpg://latch:latch@127.0.0.1:5432/latch)
    │   └── core/
    │       └── db.py                  # create_db_engine(settings)->AsyncEngine(+dispose補助)
    └── tests/
        ├── unit/test_settings.py      # database_urlのデフォルトとenv上書き(既存ファイルへ追記)
        └── integration/
            ├── conftest.py            # session: alembic upgrade head → AsyncEngine fixture
            └── test_schema.py         # §4.2の検証
```

### 3.2 触らないもの

- `docs/01〜12`・`docs/reviews/`(仕様書群)/ `docs/plans/STATUS.md`(スーパーバイザー管理)
- `prototype/` 全体・`.claude/`・ルート `README.md`
- `compose.yaml`(api/workerはまだDBを消費しない。`LATCH_DATABASE_URL` 注入は最初の消費単位/M1)
- `docker/postgres/Dockerfile`(拡張同梱済み。backend依存追加は `uv sync --frozen` が自動反映)
- 雛形の既存コード(`core/clock.py`・`core/deps.py`・`main.py`・`worker/`・既存試験)。arch test(時刻直接参照禁止)のスキャン対象に `core/db.py` が入るが、時刻参照を含まないため影響なし

## 4. テスト方針

### 4.1 レイヤ分離

- **unit**: `test_settings.py` へ database_url のデフォルト・`LATCH_DATABASE_URL` 上書きを追記するのみ(追加の製品ロジックはエンジン工場1関数で、DBなしのunit試験に価値がない)
- **integration**(compose常設DBに対して。`mark: integration`): `make test-ci` で実行。conftestが `alembic upgrade head`(冪等)してから検証する

### 4.2 test_schema.py の検証項目(05・06の要素→実DB検証の対応表)

| # | 検証 | 根拠 |
|---|---|---|
| 1 | `pg_extension` に postgis・vector が存在 | §1.2 #15(拡張は本移行で作成) |
| 2 | 12テーブルが `information_schema.tables` に存在 / **friendships が存在しない** | 05 §1〜2(本単位の受け入れ要件) |
| 3 | 05 §3の7 Indexが `pg_indexes` に存在(部分Indexの `WHERE` 句・GIST・HNSW(`pg_am`の`hnsw`)・ops(`vector_cosine_ops`)まで確認) | 05 §3 |
| 4 | ux_match_events_idempotency が式索引として存在 | 06 §9 |
| 5 | **UNIQUE実効**: match_events 同一(event_type, source_intent_id, version)の2行目INSERTが23505。異なるversionは成功。version欠落(NULL)の重複INSERTは成功(隔離保管経路の担保) | 06 §9 |
| 6 | **CHECK実効**: auth_provider 不正値23514 / visibility・notification_level 不正値23514 / latches.intent_ids 長1・5で23514 / group_candidates 長2・5で23514 / calibration_records の anonymized_at NOT NULL+latch_id NOT NULL で23514 | 05 §2 |
| 7 | **型実効**: geography — `ST_SetSRID(ST_MakePoint(…),4326)` のINSERT可・`ST_DWithin` が結果を返す。vector — 768次元リテラルのINSERT可・`<=>`(cosine距離)演算可。768以外の次元で失敗 | 05 §2・§3(型のtypmod固定) |
| 8 | **FK実効**: source_intent_id にFK制約が存在しない(information_schema)。他FKは存在 | §2.6 |
| 9 | **DEFAULT実効**: 05明記のDEFAULTを持つ列への INSERT 省略で成功・created_at 省略はNOT NULL違反(時刻の明示挿入強制) | §2.8 |

試験データ INSERT の時刻値は固定リテラル(テストはarch testのスキャン対象外だが、決定性のために固定値を用いる)。

### 4.3 既存資産への回帰

`make lint`・`make test`(unit)・`make test-ci` がすべてグリーンであること(雛形の33試験+arch testを壊さない)。

## 5. 完了条件(この単位の受渡し判定。agent2の計画書が参照する)

1. `make migrate` がci環境DB(compose常設)で成功し、`alembic current` が head を示す
2. `make test-ci` で §4.2 の9検証がグリーン
3. `make lint` / `make test` がグリーン(既存試験への回帰なし)
4. 05 §2〜§3・06 §9 の全要素(12テーブル・7 Index・式UNIQUE・CHECK・型)がスキーマへ反映済み(§1.2対応表でトレース可能)
5. `prototype/`・`docs/01〜12`・`compose.yaml`・`docker/` に差分なし

## 6. 未解決の論点(設計は推奨で固定済み。supervisor確認事項)

1. **latches.created_at の制約欄空白(05 §2)**: §2.9のとおり NOT NULL と解釈した。05側の表記修正(明記)を要するかは軽微なため本書の解釈で前進するが、05 v0.5系の更新時に確定されたい
2. **reports.status の値域・blocks の一意性**: M3(通報・ブロック実装)で確定する。それまでの追加制約はマイグレーション追加で対応(§2.9)
3. **embedding 次元 768**: 05の確定値どおり作るが、T1(LLMプロバイダ契約)確定時にEmbeddingモデルが768以外になる可能性が残る。その場合は `ALTER TABLE ... TYPE vector(N)` + HNSW再構築の追加マイグレーションで対応(05 §2「拡張次元はEmbeddingモデル確定時に固定」)
4. **本番・stagingでのマイグレーション実行主体**: M4/11の環境整備で確定(§1.3)。本設計の明示実行方針はそれを妨げない
