# M0雛形(scaffold)設計メモ

- 作業単位: 雛形(docs/plans/STATUS.md M0表・依存「—」・後続ws-1〜ws-4の共通前提)
- 作成: 2026-09-27(agent1 / superpowers:brainstorming使用)
- 次工程: agent2が本書を `scaffold-plan.md` へ変換 → agent3がworktree内でTDD実装
- 実装言語: Python (FastAPI)(2026-09-27決定。選択の再検討対象外)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

依存ハード制約**C2(Clock最初・全時刻参照はClock経由)**を最初に満たす土台を作り、ws-1〜ws-4(スキーマ・Gateway・認証・地物データ)がすべて同一のリポジトリ構成・テスト基盤・ci環境の上で動くようにする。雛形単体ではDB接続・LLM・認証の実コードは持たない(各ws単位が追加する)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | ci環境は「最小構成(常設)。API 1インスタンス、Worker 1、DB共用」で、自回帰の機能試験(毎コミット)に使う | 10 第1節環境表 |
| 2 | テスト環境は本番と同じ**種類**の技術スタックで規模を縮小する(PostgreSQL+PostGIS+pgvector / Pub/Sub / Redis / コンテナ / FCM / Cloud Monitoring)。ただしciの常設をクラウドに置くとは書かれていない | 10 第1節冒頭 |
| 3 | 時刻の参照はすべてClockインターフェース経由。適用範囲は6点 — (1)APIの期限判定(DBのclock_timestamp()は使わない)(2)期限バッチ(expiry_sweeper)(3)30分Bucket境界(4)debounceの10秒窓(5)JST 0時リセットジョブとRedisカウンタのJST日付キー(6)作成・更新の時刻検証(過去不可・上限7日)。**expires_at・response_deadline・30分Bucketの前進はClock操作で再現し、実時間の待ちを排除する** | 04 第5節(FR-41)・10 第1節「時刻操作」 |
| 4 | リセットはTTL方式でなくスケジューラのリセットジョブ(UTC基準のTTLはJST 0時と9時間ずれるため不採用)。ジョブはClockのJST日付境界を参照する | 04 第5節 |
| 5 | テスト環境ではテスト用Clock制御を注入できる(staging・load。ciも同じ機構の土台) | 10 第1節 |
| 6 | Clockを最初に作ること自体が依存制約。テスト環境の再現性はこれに依存 | 12 第2節 C2 |
| 7 | G0完了条件に「時刻参照が全てClock経由であることを**コード検査**で確認」を含む | 12 第3節 M0・STATUS.md G0 |
| 8 | 技術スタック確定値: PostgreSQL(+PostGIS+pgvector)/ Google Cloud Pub/Sub / Redis(失効リスト・ブロック・友人関係・Jevカウンタの4用途)/ FCM / Cloud Monitoring+OTLP / コンテナ+マネージド基盤 | 04 第3節 |
| 9 | 実行基盤はコンテナ+マネージド(オートスケール・ヘルスチェック・マルチAZ)。APIは本番で常時2インスタンス以上だが、これは本番/stagingの話でありciは「API 1」 | 04 第6節・10 第1節 |
| 10 | スキーマ: PostgreSQL型名、時刻はtimestamptz(JST運用)。スキーマ実装自体はws-1であり雛形は構成前提のみ参照 | 05 冒頭取り決め・第2節 / STATUS.md ws-1行 |
| 11 | 認証不要エンドポイントはtoken/refreshのみ(本API契約。v1配下の話) | 12 第2節 C3(05 第5節による) |
| 12 | M1〜M3の実装期間中もci環境の機能試験は毎コミットで回し続ける | 12 第8節 運用ルール5 |
| 13 | フロントエンド実装基準は `prototype/`(Vite静的SPA)。雛形のスコープ外だが同一リポジトリに隣接して存在する | 00 運用ルール6 / 12 前提1.1 |
| 14 | mainへの直接コミットは雛形・計画書・STATUS.mdに限る | supervisor規律 |

### 1.3 スコープ外(後続単位へ渡すもの。雛形では作らない)

- PostgreSQLスキーマ・Index・マイグレーション、DB接続コード(asyncpg/SQLAlchemy等の依存追加) → **ws-1**
- LLM Gateway・スタブ → **ws-2** / 認証・Redis失効リストの実コード → **ws-3**(Redis**サービス**はci環境構成要素として雛形のcomposeに含める。実コードはない)
- 地物データ・PostGIS取り込み → **ws-4**(PostGIS/pgvector**拡張の同梱**は雛形のDBイメージで行い、`CREATE EXTENSION`はws-1の移行に任せる)
- Pub/Sub接続・emulator導入 → **M2**(10 第1節「疑似イベント注入」はMatch Eventが存在するM2以降に意味を持つ)
- Observability(OTLP)・staging/load環境 → **M4**(12 M4-1・M4-5)
- GitHub Actions等のリモートCI → 現時点でgit remoteが存在しないため導入不能。§6参照

## 2. 実装方式の選択肢と推奨

### 2.1 リポジトリ構成 — 推奨: monorepoに `backend/` を追加(srcレイアウト)

選択肢:

- **A(推奨). repo直下に `backend/` を作り、その中をuvプロジェクト(`backend/src/latch/` のsrcレイアウト)にする**
- B. repoルートをそのままPythonプロジェクトにする(pyprojectをルートに置く)
- C. バックエンドを別リポジトリに分ける

推奨の根拠: ルートにはdocs・prototype・.claudeが既にあり、BはPythonのビルド成果物とドキュメント群が混ざる。Cは12の作業単位がdocs(同一repo)と常に相互参照する運用(00 運用ル則6・7、T4常時トラック)と相性が悪く、またprototypeとの行き来が頻繁なM1・M3で開発摩擦になる。Aは `prototype/`(フロント)と `backend/`(サーバ)の並置となり、STATUS.mdの「prototypeは既存・隣接配置は意識してよい」補足にも合う。srcレイアウトは未インストール状態のパッケージを誤ってimportする事故を防ぐ標準手段。

トレードオフ: Aはルートとbackendの2層に設定ファイルが分かれる(compose・Makeはルート、pyprojectはbackend)。この分割は§3の構成で明示する。

### 2.2 ci環境の実体 — 推奨: ローカルdocker composeによる常設環境

選択肢:

- **A(推奨). `compose.yaml`(ルート)で db(PostgreSQL+PostGIS+pgvector)/ redis / api / worker の4サービスを定義。`make up` で常設(`restart: unless-stopped`)**
- B. 最初からクラウド(Cloud Run+マネージドDB)にciを常設する
- C. ci実行のたびにcomposeで一時的に立てる(非常設)

推奨の根拠: 10 第1節は「本番と同じ**種類**で規模を縮小」を要求するが、ciの稼働場所をクラウドに指定していない。Bはクラウド契約・課金・認証というdocsに存在しない外部前提を要求し、雛形単位で導入できない(契約は人間領域)。Cは「常設」の指定(10 第1節)に反し、毎コミット実行(12 運用ル則5)の起点を失う。Aはコンテナ(DB種類はPostgreSQL、拡張はPostGIS+pgvector)で種類の同一性を満たし、`docker compose up -d` 後は落ちない=常設であり、開発機(動作確認済み: Docker 29 + Compose v5)で即座に成立する。本番種類のうちPub/SubのみM2まで含めない(§1.3)。

**「DB共用」の解釈(設計判断)**: 10 第1節ci行の「DB共用」は「ci環境内でAPIとWorkerが同一DBインスタンスを共有する」と読む(compose上はdbサービス1つ、両者とも同じ接続先)。読み方のもう一方「stagingとDBを共用する」は、stagingが「縮小構成」で独立に規定されておりM4スコープであることから、雛形では採らない。ただし将来の分離に備え、接続先は環境変数(`LATCH_DATABASE_URL`)で与え、コードに埋め込まない。この解釈違いはいずれの読みでもcomposeの形が変わらないため、後からでも切り替え可能。

トレードオフ: Aではciの品質が開発機のdocker健全性に依存する。ヘルスチェックと `make ps` で状態を常に見えるようにする(§3)。

### 2.3 パッケージ管理と言語バージョン — 推奨: uv / Python 3.13

選択肢:

- **A(推奨). uv(プロジェクト+lock). Pythonは `.python-version` で3.13に固定。miseでuv自体を固定(`.mise.toml`)**
- B. poetry / pip + requirements.txt
- C. Python 3.14(開発機のシステムバージョン)

推奨の根拠: 依存解決が速くlockfileが決定的で、2026年のPython標準の実質解になっている。導入環境にもmise経由のuvが既にある(バージョン未固定のため `.mise.toml` で固定する)。バージョンは、ws-1が導入するasyncpg・SQLAlchemy・Alembic等のDB系エコシステムの3.14対応実績を現時点で確認しきれないため、1年以上成熟している3.13とする(uvは任意バージョンのPythonを自動導入するためシステム3.14と衝突しない)。

トレードオフ: 3.13固定は3.14の新機能を一時的に使えない。実運用で問題になった場合は `.python-version` の変更のみで移行できる。

### 2.4 ClockのインターフェースとDI — 推奨: 抽象基底クラス `Clock` + 実装2種、DIは「APIはapp.state・Workerは明示的構築」

インターフェース(要素):

```python
# backend/src/latch/core/clock.py(要旨)
JST = timezone(timedelta(hours=+9))  # 日本に夏時間なし。固定オフセットで確定

class Clock(ABC):
    def now(self) -> datetime: ...      # tz-aware UTC の現在時刻(抽象)
    def jst_date(self) -> date: ...     # now()をJST暦日付へ変換(具象。JST日付キー・リセットジョブ判定用)

class SystemClock(Clock): ...   # datetime.now(timezone.utc) を返す本番用実装
class FakeClock(Clock): ...     # テスト用。set(時刻) / advance(差分) を持ちnow()は決定的
```

選択肢と判断:

- **IFの形**: `typing.Protocol` vs ABC → **ABC(推奨)**。Protocolは構造的部分型で軽量だが、「Clockを実装する側」がこれから多数現れる(期限判定・バッチ・debounce・カウンタ・ジョブ)ため、継承時の実装漏れを静かに防げるABCを取る。トレードオフはProtocolより結合が強いこと程度で、実装は1モジュールに閉じる
- **タイムゾーン方針**: now()は**tz-aware UTC統一**(05「時刻はtimestamptz(JST運用)」と整合。DB比較・イベント順序はinstantで行い、JST暦日付が必要な箇所(カウンタ日付キー・リセット境界。04 第5節(5))だけ `jst_date()` を使う。naive datetimeはIFに登場させない)
- **DI方式**: (a) モジュールグローバル+setter、(b) contextvar、(c) **APIは `app.state.clock` + `get_clock()` dependency、Workerはmainで明示的構築(推奨)**。(a)(b)はグローバル可変状態を生み、テスト並行実行の干渉リスクがある。(c)はFastAPIの `dependency_overrides` と `create_app(clock=FakeClock(...))` の両経路で差し替えられ、Worker側は起動時の1回の明示的構築で済む。将来のlifespan(DBエンジン等)追加にも `app.state` 拡張で自然に繋がる
- **モックの能力**: `set()`(任意時刻へ移動・後退も可。時刻検証(6)の「過去不可」境界試験など両方向が必要)と `advance(delta)`(期限・debounce・バッチ周期の前進再現)。6点(04 第5節)すべてこの2 primitivesで再現できる(再現性は試験で証明する。§4.2)

トレードオフ: `jst_date()` をClockに持たせるのは時刻**参照**ではなく変換ユーティリティだ、という議論はある。しかしJST日付キーは「Clock操作で再現可能」であることが10 第1節(5)で要求される観測点そのものなので、Clockの傘に入れる。

### 2.5 DBイメージ — 推奨: 公式postgresイメージ+拡張2つをaptで足した自前Dockerfile

選択肢:

- **A(推奨). `docker/postgres/Dockerfile`: `FROM postgres:17` + `postgresql-17-postgis-3` `postgresql-17-pgvector` をapt導入**
- B. PostGIS+pgvector同梱のサードパーティ統合イメージを使う
- C. 素のpostgresにして拡張はws-1で差し替える

推奨の根拠: 公式postgresイメージのDebian版はPGDG aptリポジトリ由来であり、両拡張の導入は2パッケージのapt installで済む。Bは第三者の更新サイクルにDBという基盤を預けることになり、04 第3節の「コンポーネント数最小」という選定思想に反する。Cはws-1開始時に雛形の構成変更が発生し、作業単位の境界が濁る。拡張の`CREATE EXTENSION`はws-1のマイグレーションが行う(雛形は拡張の**同梱**まで)。

トレードオフ: Aはイメージビルド時間が初回only増える。バージョンはPG17(実績重視。PG18への更新は`FROM`行変更のみ)。

### 2.6 品質ツール — 推奨: ruff(lint+format)のみ。型チェック・カバレッジ閾値は導入しない

docsにlint/型/カバレッジの要件は存在しない(10は試験のグリーン/証拠だけを要求)。雛形の依存は最小に保ち、ruffだけで「毎コミットで回る静的検査」を担保する。mypy・カバレッジ閾値は要件が生じた時に追加する(YAGNI)。トレードオフ: 型検査の欠如はpydantic/FastAPIの実行時検証とテストで当面補う。

### 2.7 毎コミット実行の担い — 推奨: Makeターゲットを規律化し、リモートCIはリポジトリ整備後に

現状git remoteが存在しないためGitHub Actions等は導入できない。よって雛形では `make lint` `make test`(unit)をコミット前の規律とし、`make test-ci`(compose起動+unit+integration)で10 第1節のci環境試験の入口を提供する。リモートCIの導入時期は§6へ残す。

### 2.8 採用しないもの(YAGNIによる切り捨て一覧)

- DBドライバ・ORM・Alembic(ws-1)/ httpクライアント・Gateway IF(ws-2)/ JWT・Redisクライアント(ws-3)— 雛形の依存は `fastapi` `uvicorn` `pydantic-settings` のみ(devにpytest・pytest-asyncio・httpx・ruff)
- `/v1/*` エンドポイントの空ルータ・バージョンスタブ — ws-3の認証が最初のv1契約になる
- 設定項目の前倒し(DATABASE_URL等)— コードが消費しない設定は作らない。composeの環境変数も最小限
- OTLP・メトリクス(M4)/ Pub/Sub emulator(M2)/ カバレッジ・型チェック(§2.6)

## 3. ファイル構成

### 3.1 作るもの

```text
/ (repo root)
├── .mise.toml                        # tools: uvバージョン固定(ミニマル)
├── compose.yaml                      # ci環境(10 第1節)。name: latch-ci
├── Makefile                          # setup / up / down / ps / logs / lint / test / test-ci
├── docker/
│   └── postgres/
│       └── Dockerfile                # postgres:17 + postgis3 + pgvector(§2.5)
└── backend/
    ├── .python-version               # 3.13
    ├── pyproject.toml                # uvプロジェクト(hatchling, src/latch)。ruff・pytest設定を含む
    ├── uv.lock                       # 生成物(コミットする)
    ├── README.md                     # backend開発者向けクイックスタート(setup〜test-ci)
    ├── Dockerfile                    # api/worker共用イメージ(uv sync --frozen --no-dev・非root)
    ├── src/latch/
    │   ├── __init__.py
    │   ├── main.py                   # create_app() ファクトリ。GET /health(status+server_time)
    │   ├── settings.py               # pydantic-settings(env prefix LATCH_)。app_env / log_level のみ
    │   ├── core/
    │   │   ├── __init__.py
    │   │   ├── clock.py              # JST / Clock(ABC) / SystemClock / FakeClock(§2.4)
    │   │   └── deps.py               # get_clock(Request)->app.state.clock(FastAPI依存)
    │   └── worker/
    │       ├── __init__.py
    │       ├── main.py               # worker起動本体(明示的SystemClock構築・graceful shutdown)
    │       └── __main__.py           # python -m latch.worker の入口
    └── tests/
        ├── conftest.py               # fake_clock / app / client フィクスチャ
        ├── unit/
        │   ├── test_clock.py               # Clock基本(SystemClock妥当性・FakeClock決定性・JST境界)
        │   ├── test_clock_reproducibility.py # 適用範囲6点の再現性証明(§4.2)
        │   ├── test_arch_no_direct_time.py  # 禁止API検査(C2強制・§4.3)
        │   ├── test_app_health.py           # /healthとClock差し替え
        │   ├── test_settings.py             # デフォルトとenv上書き
        │   └── test_worker.py               # worker起動経路(FakeClock注入・即時shutdown)
        └── integration/
            └── test_compose_ports.py    # compose常設環境のdb/redis到達確認(mark: integration)
```

composeの構成(ci環境=10 第1節どおり):

| サービス | 内容 |
|---|---|
| `db` | ビルド `docker/postgres`。db名・ユーザーはcompose内固定値(開発用。環境変数で上書き可)。healthcheck `pg_isready`。ホスト側 `127.0.0.1:5432` 公開(integration試験とpsql確認用)。名前付きボリューム |
| `redis` | `redis:8-alpine`。healthcheck `redis-cli ping`。`127.0.0.1:6379` 公開 |
| `api` | ビルド `backend/`。`uvicorn latch.main:app --host 0.0.0.0 --port 8000`(**単一プロセス=API 1インスタンス**)。depends_on: db/redis(healthy)。`127.0.0.1:8000:8000`。healthcheckは `/health` |
| `worker` | apiと同じイメージ。`python -m latch.worker`(**Worker 1**)。depends_on: db/redis(healthy) |

- api・workerは `restart: unless-stopped`(常設)
- `GET /health` について: 12 C3「認証不要エンドポイントはtoken/refreshのみ」は**v1 API契約**の話であり、運用プローブ用の `/health`(v1配下に置かない)はci環境のhealthcheckとcompose健全性の要件(04 第6節ヘルスチェックの種類同一性)に必要。応答は `{"status":"ok","server_time":"<ISO8601 UTC>"}`。`server_time` は `get_clock()` 由来 — **Clock差し替えが全経路で効くことの、雛形における生の消費者**となる

### 3.2 触らないもの

- `docs/01〜12`・`docs/reviews/`(仕様書群。運用ル則7)
- `docs/plans/STATUS.md`(スーパーバイザー管理。本設計メモ以外のplans配下もagent2以降の成果物)
- `prototype/` 全体(00 運用ル則6。node_modules等も含め完全に非接触)
- `.claude/`(prompts・settings)
- `README.md`(ルート。docs索引としての役割を保つため、開発手順は `backend/README.md` に置く)
- `.gitignore` は**追記のみ**(Python系: `.venv/` `__pycache__/` `.pytest_cache/` `.ruff_cache/` `.coverage` `htmlcov/` 等。既存のnode/env規則は変更しない)

## 4. テスト方針

### 4.1 レイヤ分離と実行

- **unit**(デフォルト): 外部プロセスゼロ。`make test` = `uv run pytest -m "not integration"`。毎コミットの規律(12 運用ル則5のci試験のうち、雛形時点で存在するもの)
- **integration**: `make test-ci` が `docker compose up -d --wait`(ヘルスチェック待ち)後に `pytest -m integration` を実行。雛形ではdb/redisへのTCP到達と `/health` 応答のみを確認する(実際のSQL試験はws-1がドライバ導入後に追加)
- pytest設定: `asyncio_mode = "auto"`(httpx AsyncClient + ASGITransport でappを起動せず試験)。marker `integration` 登録

### 4.2 Clock試験 — 適用範囲6点の再現性証明(04 第5節・10 第1節)

`test_clock_reproducibility.py` は、ドメイン実装がまだ無い段階で「6点がすべてClock操作で再現できる」(12 M0スコープ3)を** primitives の組合せだけで証明する**表駆動試験:

| 適用範囲(04 第5節) | 試験内容(FakeClockのみ使用) |
|---|---|
| (1) API期限判定 | `deadline = fake.now()+Δ` に対し advance 前 `now < deadline` → advance(Δ+ε)後 `now >= deadline`(期限切れ再現。DB関数不使用の立証) |
| (2) 期限バッチ | `next_run = last + 60s` の判定が advance(59s)で未到来・advance(1s+ε)で到来 |
| (3) 30分Bucket境界 | now()から30分区切りへの丸め計算が、境界直前→advance(ε)で次bucketへ遷移(Bucket時刻の定義自体は06に従いM2で確定させる。雛形は境界計算がClock値から決定的に導けることの証明に留める) |
| (4) debounce 10秒窓 | 最終Event時刻から advance(9s)は窓内・advance(2s)は窓外 |
| (5) JST 0時・月初 | `jst_date()` が UTC 14:59:59→23:59:59(JST)/15:00:00→翌0:00(JST) 境界で 正しく切替。月初: 10-01 JST 0時 = 09-30 15:00 UTC で jst_date==10-01(TTL不採用の根拠となった9時間ずれを試験に刻む) |
| (6) 作成・更新の時刻検証 | 入力時刻が now() の前後両側で判定が反転することを set() で再現 |

加えて `test_clock.py` でSystemClock(tz-aware・UTC・現在時刻の概ね一致)とFakeClock(set/advanceの決定性・スレッド安全性)を検証する。

### 4.3 C2強制のarch test — G0「コード検査」の自動化

`test_arch_no_direct_time.py`: `backend/src/latch/` 配下の `.py` を走査し、`core/clock.py`(SystemClockの実装場所)を唯一の例外として、次を**検出したら失敗**させる:

- `datetime.now` / `datetime.utcnow` / `date.today` / `time.time` / `time.monotonic` / `time.sleep`(ソース内トークン照合。debounce・バッチ周期もClock経由とする10 第1節(2)(4)より、monotonic系とsleep系も製品コードから排除する。待機が必要な処理は将来のアダプタ層で限定的に許可する際、allowlistに明示的に追加する)

`asyncio.sleep` は待機であって時刻参照ではないため禁止対象外とする。ただしdebounce窓の判定・バッチ周期の境界計算はClock値から導くこと(待機自体の許容と、窓・境界のClock経由は別物である点を計画書・実装で混ぜない)

これはG0の「時刻参照がすべてClock経由であることをコード検査で確認」(12 M0)を毎コミットで自動化する仕組みであり、雛形が後続全単位へ課す規律の機械的な強制になる。テストコード自身(`tests/`)はスキャン対象外(実時間の使用が正当なのは試験のみ)。

### 4.4 その他のunit試験

- `test_app_health.py`: `/health` 200・`status=="ok"`・`server_time` が注入したFakeClockの時刻と一致(dependency_overrides と `create_app(clock=...)` の両経路)
- `test_settings.py`: デフォルト値と `LATCH_` プレフィックスの環境変数上書き
- `test_worker.py`: worker本体がFakeClockで起動し(shutdownイベント即時セットで)即座に終了する — Worker経路のClock注入可能性の担保

## 5. 完了条件(この単位の受渡し判定。agent2の計画書が参照する)

1. `make setup` → `make lint` `make test` がクリーンな環境で成功する
2. `make up` 後、`docker compose ps` で db・redis・api が healthy かつ worker が running、`curl 127.0.0.1:8000/health` が `status:ok` を返す(2026-09-27 supervisor裁定: workerは雛形スコープでプローブ表面を持たないためhealthcheckを定義しない。元文言「4サービスがhealthy」は§3.1のworker定義(healthcheckなし)と矛盾しており、実態に合わせて修正。G0(12 M0)はworker healthcheckを要求しない)
3. `make test-ci`(unit+integration)がグリーン
4. §4.2の6点再現性試験と§4.3のarch testが存在してグリーン(=C2が立証済み)
5. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` が `core/clock.py` のみにヒットする
6. `prototype/`・`docs/01〜12`・`README.md` に差分がない

## 6. 未解決の論点(設計は推奨で固定済み。supervisor確認事項)

1. **ci環境のクラウド昇格**: 本設計はciをローカルcompose常設とした(§2.2)。開発機単位を超えた常設(クラウド)が必要になった場合、契約(人間領域)を経てM4の環境整備と一緒に再検討する。雛形の構成はcompose→クラウド移行を妨げない(イメージはそのまま再利用)
2. **git remoteとリモートCI**: remote整備後、`make lint/test` と同等をpush毎に走らせるワークフロー導入を検討する(12 運用ル則5「毎コミット」の機械的強制)。設計は導入を妨げない
3. **DB共用の解釈**: §2.2のとおり「ci内でAPI/Worker共有」と解釈した。staging・loadとのDB関係(M4)が確定する際、接続先は環境変数で与えてあるため変更はcompose・envに閉じる
4. **バージョンの minor 固定**: Python 3.13・PostgreSQL 17・Redis 8は§2の根拠による初期固定。実装時(asyncpg等)に支障が出た場合、雛形の完了条件を満たす範囲で変更をplan側に記録してよい
