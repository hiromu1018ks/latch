# M4 ws-1(staging・load環境構築)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M1〜M3がci環境(in-process試験)でグリーンになった機能を、**常設の実コンテナ環境(staging)と隔離の負荷試験環境(load)**で動かせる器を作る — ①staging・loadのcompose(別プロジェクト`latch-staging`/`latch-load`・別ボリューム・別ポート18000系/28000系・api2台+nginx前段+worker2台)②Redis共有の制御Clock(`ControlledClock`+`make_clock`+操作CLI。api/workerの複数プロセスが同一の時刻を見る)③疑似イベントパブリッシャー(既存events CLIへ`publish`サブコマンド追加・6種手動発火+毒ペイロード)④staging専用テスト鍵ペアの注入(`LATCH_AUTH_TESTKEYS_DIR`・M0 ws-3資産の再利用)。**マイグレーション追加なし・依存追加なし・ci環境(compose.yaml・ci系Makefileターゲット)への変更ゼロ**。

**Architecture:** design §2.1案B(2026-10-03 supervisor承認)— ciのcompose.yamlは一切触らず`compose.staging.yaml`/`compose.load.yaml`を新設。プロジェクト名が違うためコンテナ名・ネットワーク・ボリュームが自動分離し、**DB独立ボリュームで運用ルール1〜3(alembic_version取り合い)が構造的に発生しない**。Clock制御はdesign §2.2案A — Redisキー`latch:clock:control`に基準点JSON(real↔virtual)を置き、`now() = 仮想基準点 + (実時間経過)`。キー不在=オフセット0=実時間・Redis到達不能は例外でfail-closed。計算部分は純関数`_compute_now`に分離しunit試験は決定的。nginxは`docker/nginx/staging.conf`1ファイルをstaging/loadの両composeからマウント(upstream=api1:8000 api2:8000・proxy_next_upstreamのpassive死活除外)。ホスト側CLI(make staging-*)は既存M0規律どおりenv上書き(`LATCH_DATABASE_URL`等)でstaging/load宛へ向ける。外部SDKを扱わない(FCM・LLMともstubのみ・design冒頭)ためcontext7確認は不要。

**Tech Stack:** 変更なし(Python 3.13 / FastAPI / Redis / docker compose / nginx:alpine / PostgreSQL17+PostGIS+pgvector / Pub/Subエミュレータ)。テストは既存dev依存(fakeredis・pytest)のみ。

**Spec:** `docs/plans/M4/ws-1-design.md`(agent1設計メモ。**未解決論点なし** — §5のsupervisor承認事項5件〔A1〜A5〕は2026-10-03承認済み)。design.mdが本計画より優先(§1の規定)。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m4-ws-1`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`cd backend && uv sync`)を実行して `.venv` を構築する(**依存追加・マイグレーション追加ともになし**のためlockもalembicも進まない)。
- **毎コミットの検査**: worktree内で**毎コミット `make lint` と `make test` がグリーン**であることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-10-03)**: 現状 `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空(既存133ファイル)。本計画の新規4ファイル(`test_controlled_clock.py`・`test_clock_cli.py`・`test_clock_wiring.py`・`test_events_publish_cli.py`)は既存と衝突しない(`test_clock.py`・`test_clock_reproducibility.py`・`test_events_cli.py`・`test_events_relay.py`は別名)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **共有ci-db運用とtest-ciの扱い**(2026-10-03スーパーバイザー指示): 本単位は**マイグレーションを追加しない**(design冒頭・alembic head=0006不変)ため運用ルール1〜3の対象外だが、念のため**agent3は `make test-ci` を実行しない**。報告書の該当完了条件には **「make test-ci(1480維持): スーパーバイザー検証待ち」** と記録する(マージ前のスーパーバイザー検証で実施される)。開発の検証は `make test`(unit)+staging/load実コンテナの受け入れ確認(design §4.2・**docker利用可**)で行う。
- **staging/loadのdocker操作は可・ci宛は読み取りのみ**:
  - 可: staging/load宛のbuild/up/down/ps/logs/exec(`-f compose.staging.yaml` / `-f compose.load.yaml` 付き)。design §4.2の受け入れ確認9項目はagent3が実施して証拠を報告書へ記録してよい(staging/loadは独立プロジェクト・独立ボリュームのためciと干渉しない)。
  - 可(ci宛の**読み取りのみ**・受け入れ7/9の証拠収集のため): `docker compose ps`・`docker compose logs <svc>`・`docker compose exec -T db psql -U latch -d latch -tAc "SELECT …"`(読み取りSELECTのみ)。`make ps` / `make logs`(followはCtrl-Cまで・証拠は `logs --tail` 推奨)も読み取りとして可。
  - **不可**: ci宛の状態変更操作すべて(`make up` / `make down` / `make migrate` / `make test-ci` / `docker compose build` / `stop` / `start` / `restart` / `kill` / `docker volume` の削除・作成)。**latch-ci-dbボリューム・latch-ci-*リソース・ci常設環境には絶対に触らない**。
- **最重要の事故防止規律**: staging/load宛のdockerコマンドは**必ず `-f compose.staging.yaml` または `-f compose.load.yaml` を付ける**。`-f` を付け忘れるとカレントディレクトリのcompose.yaml(=ciプロジェクト)宛に作用する。コマンドを打つ前に毎回 `-f` の有無を確認する。
- **compose.yaml(ci)とci系Makefileターゲットは触らない**(design §3.2): Makefileは**新規ターゲットの追記と `.PHONY` 行への名前追記のみ**。既存行の削除・変更はしない(完了条件4で機械検証する)。
- **Makefileのレシピ行はハードタブ**: 本計画のMakefileコードブロックのレシピ行は先頭がタブ1文字である。コピー時にスペースへ化すと `make` が "missing separator" で失敗する。追記後に `make -n staging-up` で解釈確認をすること(ドライラン・実行はされない)。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **依存追加なし**: `backend/pyproject.toml`・`backend/uv.lock`・`.env`・`.env.example` に触らない(新settingsキー2つは既定値付きのためenvなしで動く)。
- **実時間参照は `core/clock.py` のみ**(arch test `test_arch_no_direct_time.py`・design引用#12): 本単位で実時間を参照する新規コードはclock.pyへ追記する`ControlledClock`のみ。`clock_cli.py`は `SystemClock().now()` 経由で実時間を得る(datetime.nowを直接書かない)。
- **Clock契約の変更禁止**: `core/clock.py` の既存3クラス(Clock・SystemClock・FakeClock)は**1行も変更しない**(追記のみ)。既存DI試験・arch testの`ALLOWED`定義も変更しない。
- **タイムボム回避**(design §2.6・M3 ws-1の実例): stagingのClock操作・シード投入の証拠は実行時刻依存の相対表現(「今夜23:30」等)に固定せず**ISO8601明示**(例: `2026-10-04T00:00:00+09:00`)で行い、実施値を報告書へ記録する。
- **固定値の遵守**: design §2の採用判断(案A〔Redis制御Clock〕・案B〔別プロジェクト〕・ポート表§2.1・キー名`latch:clock:control`・基準点JSON形式`{"real":…,"virtual":…}`・6種choices・`.staging/keys`パス)は固定値。**変更した場合は必ず報告ファイルに「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `chore:` 等)。
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| 環境は3種。staging=縮小構成(API 1〜2・Worker 0〜2・単一ゾーン)で27項目機能検証+非機能スモーク・load=隔離環境で負荷試験・総当たり検出 | 10 第1節表・12 M4-1 |
| 本番と同じ種類で規模縮小(PostgreSQL+PostGIS+pgvector / Pub/Sub / Redis / コンテナ基盤 / FCM / Cloud Monitoring) | 10 第1節・04技術スタック |
| 時刻操作: staging・loadではテスト用のClock制御を注入できる。適用範囲6項目=(1)期限判定(2)Intent期限切れ・LATCH期限切れバッチ60秒周期(3)30分Bucket境界(4)debounce 10秒窓(5)JST 0時リセット+Redis JST日付キー(6)作成・更新の時刻検証。expires_at・response_deadline・30分Bucketの前進はClock操作で再現し実時間待ちを排除 | 10 第1節「時刻操作」・04 §5(FR-41適用範囲と同一) |
| 疑似イベント注入: Match Event 5種+派生embedding_completedを手動発火できる。LLM呼び出しはテストモード(stub+レイテンシ注入)で決定的。FCMはドライラン(記録のみ) | 10 第1節「疑似イベント注入」 |
| Match Event 5種=作成・更新・削除・期限切れ・指定時刻の到来(expired・scheduled)。実装済みEvent種別6値と一致 | 01 §5・intents/events.py |
| 認証のテスト構成(D-21): 本番と同じJWT検証経路(JWKS参照)を通しつつstaging専用の鍵ペアを注入し、テストユーザーのJWTを発行する内部ツール。実IdPに依存しない | 10 第1節「認証のテスト構成」・05 §5 |
| 可用性: 稼働中のAPIコンテナを1インスタンス強制停止し、残存インスタンスでの処理継続と自動復旧を確認。合成監視プローブ(1分間隔)で成功率99.5%以上 | 10 §4.3 |
| 性能の負荷条件: Intent保存50rps・Match Event 100イベント/秒バースト・Jevレイテンシ注入(p50 2秒/p95 5秒)・Parserレイテンシ注入(p50 1秒/p95 2.5秒) | 10 §4.1 |
| 総当たり検出: Active Intent 1万→5万→10万と段階増加し同一のイベント注入で観測。Vector Retrieval件数はK_v=50以内でIntent数と無関係に安定 | 10 §4.2 |
| 障害系: 復元+再エンベディング(cross-check)・D-16上限到達(**月初境界はClock操作で進める**)・resume/Bucket遅延のClock操作での回収・毒ペイロード注入(5回再試行→quarantined)・Queue lag alert発報 | 10 §4.4・§4.5・§4.7 |
| 証拠: 試験ID・実施日・環境・入力条件・合否とともにdocs/testassets/results/へ保管 | 10 第5節 |
| 時刻の参照はすべてClock経由でarch testが毎コミット強制。実時間の直接参照が許されるのは core/clock.py のみ(ALLOWED=単一ファイル) | 04 §5・test_arch_no_direct_time.py:27 |
| Event処理: 失敗は5回再試行→match_events.status=quarantinedへ隔離。削除済みIntentへの参照Eventはquarantinedでなくprocessedで破棄。UNIQUE制約で同一Eventの重複処理を排除(idempotency key=(event_type, source_intent_id, version)) | 10 §4.7・06 §9 |
| apiイメージはciと同一(backend/Dockerfile・uv sync --frozen)。dbイメージはdocker/postgres(postgres:17+PostGIS+pgvector) | compose.yaml・docker/postgres/Dockerfile(引用#15) |
| ClockはDI可能: create_app(clock=…)・Worker(clock=…)とも未注入時にSystemClock。/healthのserver_timeがClock由来(get_clock())でClock差し替えの生の消費者 | main.py・worker/main.py(引用#16) |
| テスト用認証資産は既存: 同梱testkeys(RS256・kid=test-idp-1・ci/試験専用・prodは起動ガードで拒否)・`python -m latch.auth issue-idp-token --provider google --subject <sub> --private-key <pem>`・`gen-keypair --out-dir <dir>` | auth/testkeys/README.md・auth/tools.py(引用#17) |
| Event発行の公開IFは`publish_match_event(event_type, intent_id, version)`の3点組(publish形式は実装の契約)。運用CLIは`python -m latch.events` | events/bus.py・events/__main__.py(引用#18) |
| stubのレイテンシ注入はsettings既存(llm_stub_delay_parser_ms/_embedding_ms/_jev_ms・push_stub_delay_ms・既定0=無効)。p50/p95分布のスタブ拡張はws-6スコープ | settings.py(引用#19・design §2.6) |
| push_mode=stubが既定(ドライラン・構造化ログlatch.push.send)。real(実FCM)はws-9・未実装値指定はValueError | settings.py(引用#20) |
| staging・load・ダッシュボードはローカルdocker composeの別プロジェクト・LLMはスタブで実課金ゼロ(設計前提) | 12 M4設計前提(STATUS 2026-10-03承認) |
| 消費者の器の要求(27項目ハーネス=staging up/token/clock/publish・ws-6=load隔離+delay env・ws-7=api2台+nginx+worker stop/start+毒+Cliock操作・ws-9=push切替余地) | design §1.3・10 §3〜§5 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **実時間参照は `core/clock.py` のみ**。`clock_cli.py`・`testkeys/__init__.py`・`events/__main__.py` 等の新規・変更コードで `datetime.now` / `time.time` 等を書かない(実時間が必要なら `SystemClock().now()` を経由する。arch testが毎コミットで強制)。
- **clock.pyへの追記のみ**: 既存のClock ABC・SystemClock・FakeClockは変更しない。`ControlledClock`・`_compute_now`・`make_clock` をファイル末尾へ追記する。
- **発行形式の二重管理を作らない**: publish CLIは既存 `EventBus.publish_match_event`(3点組)をそのまま呼ぶ(design §2.5)。payloadのJSON組み立てをCLI側で再実装しない。`--raw-payload` のみ例外(生bytes直接publish・毒ペイロード専用)。
- **認証は単一の鍵ディレクトリ経路**: `load_private_key()`(発行側)と `load_jwks()`(検証側)が同一の `_keys_dir()` を見ることでstaging鍵ペアが自然に揃う(design §2.4)。
- **staging/load composeの全サービスで接続envを網羅する**(YAMLアンカー共通化 — design §1.4の再発防止)。apiとworkerでアンカーを分けず、env部分は単一アンカーからmerge(`<<:`)する。
- **LLM/FCMはstub明示**: staging/load composeは `LATCH_LLM_MODE=stub`・`LATCH_PUSH_MODE=stub` を必ず設定(実課金ゼロ・実送信ゼロの保証・design §2.6)。
- **マイグレーション・geo投入の起動時自動実行はしない**(既存規律どおり・design §2.6): `make staging-migrate` / `make staging-geo-import` は明示実行。
- **`make lint`(ruff E,F,I,UP,B・format行長88)と `make test` を毎コミット通す**。unit試験は外部プロセス不要・実時間待ちなし(fakeredis・スタブ注入)。
- **レシピ・ Makefile追記は `.PHONY` 行の名前追記とターゲット追加のみ**(完了条件4で検証)。
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` が§4の一覧どおり・basename一意コマンドが空。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **Redis一時障害時にControlledClockが静かに実時間へ戻る**(fail-closed契約の逆) — `now()` をtry/exceptで包む・None既定で握る等の実装をすると、「制御下にあるつもりが実時間」の誤検証がstaging試験全体を汚染する(期限試験が実時間のままグリーンに見える)。例外は握らず伝播させる → Task 2の `test_redis_failure_propagates_fail_closed` がピン・コード規約としてnow()/set()/advance()に例外握りを書かない
2. **clock_cliのnaive ISO8601受付でJST意図の時刻がUTC解釈される**(9時間ずれのタイムボム) — `datetime.fromisoformat` はnaive文字列を素通しする。naiveは拒否しJSTオフセット付き明示を強制する(design §2.6のタイムボム規律) → Task 3の `test_parse_iso_rejects_naive` + `test_run_set_writes_anchor_json`(JST入力→UTC基準点の正規化)
3. **publish CLIの `--raw-payload` が3点組と混ざる**(二重発行・無視) — 毒ペイロード試験(ws-7)で3点組も同時に飛ぶと観察対象が汚れる。raw指定時は3点組を発行しない分岐をピン → Task 6の `test_raw_payload_takes_priority_over_triple`(raw発行・published空)
4. **testkeys差し替えが発行側だけ効き検証側(JWKS)が同梱のまま**(stagingでJWT検証が全滅) — `load_private_key` と `load_jwks` が別々のディレクトリ解決をすると発行と検証の鍵が食い違う → Task 5の `test_testkeys_dir_env_swaps_keypair_for_both_sides` が「env下で発行→同一envの検証側で成功・envを外すと同梱JWKSでは失敗」の両面でピン
5. **composeのworker系env欠落(既知欠陥の再発)とポート衝突** — apiとworkerでアンカーを分けて書くとworkerだけenvが欠ける(2026-09-30まで実在した欠陥)。staging(18000系)とload(28000系)・ci(8000/5432/6379/8085)のポート重複は起動時衝突になる → Task 7で単一envアンカー+`docker compose config`解決検証・§7手順1/8で実up・完了条件7でファイル一覧照合

### 既存試験への影響事前特定(Self-Review・計画時点で全数確認済み)

- **`backend/tests/unit/test_settings.py`**: `test_settings_defaults` へピン2行(clock_mode・auth_testkeys_dir)を**追記のみ**(既存assertは1行も変えない)・env上書き試験1件を追加。既存4試験は無傷(Task 1)。
- **Clock DI試験**: `tests/conftest.py` の `app` fixtureと `test_app_health.py::test_health_create_app_injection` は **clock引数明示**のため `make_clock` を通らず無傷。`test_worker.py::test_worker_defaults_to_system_clock` は `Worker()` 直構築で **`worker/main.py` の既定(SystemClock)を変更しない**ため無傷(本計画は `worker/__main__.py` のみ変更)。`test_clock.py`・`test_clock_reproducibility.py` はclock.py追記で既存契約が変わらないため無傷。新規の既定経路ピンは `test_clock_wiring.py`(Task 4)へ分離する。
- **arch test(`test_arch_no_direct_time.py`)**: `ALLOWED = {core/clock.py}` が**既存のまま**である根拠=design引用#12(実時間参照が許されるのはclock.pyのみ)。ControlledClockの `datetime.now(UTC)` 参照はこのファイル内なので合法・`ALLOWED` 定義は変更しない。`clock_cli.py` は `SystemClock().now()` 経由で禁止トークンなし。追記ファイル全てのruff検査は `make lint` が担当。
- **`main.py` のmodule-level `app = create_app()`**(import時実行): clock_mode既定systemのため `make_clock(Settings())` は `SystemClock()` を返すのみ・Redis接続なし・副作用なし(Task 4で擬態試験も追加)。
- **`test_events_cli.py`**(既存): purge-match-subの既存2試験は `__main__.py` へのサブコマンド追記で無傷(parserは既存サブコマンドを保持・dispatchは追加のみ)。

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1+§3のテスト分割):

```text
backend/src/latch/core/clock_cli.py                  (Task 3。show/set/advance・同期redisクライアント)
backend/tests/unit/test_controlled_clock.py          (Task 2。純関数・set/advance・キー不在・fail-closed・make_clock分岐)
backend/tests/unit/test_clock_cli.py                 (Task 3。パーサ・キー書込値・dispatch)
backend/tests/unit/test_clock_wiring.py              (Task 4。create_app・worker入口の既定Clock経路)
backend/tests/unit/test_events_publish_cli.py        (Task 6。6値choices・3点組・raw優先・宛先Settings)
compose.staging.yaml                                 (Task 7。latch-stagingプロジェクト・8サービス)
compose.load.yaml                                    (Task 7。latch-loadプロジェクト・同種構成・隔離)
docker/nginx/staging.conf                            (Task 7。upstream+proxy_next_upstream)
docs/plans/M4/ws-1-report.md                         (Task 9。報告ファイル・受け入れ証拠つき)
```

変更:

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/settings.py` | 末尾へ `clock_mode`(既定system)・`auth_testkeys_dir`(既定空)の2キー追記のみ | 1 |
| `backend/tests/unit/test_settings.py` | defaultsピン2行追記+env上書き試験1件追加 | 1 |
| `backend/src/latch/core/clock.py` | **追記のみ**: `ControlledClock`・`_compute_now`・`make_clock`(既存3クラス不変) | 2 |
| `backend/src/latch/main.py` | 既定Clock構築を `make_clock(settings)` 経由へ(import行含め実質2行変更) | 4 |
| `backend/src/latch/worker/__main__.py` | Worker構築時のClock既定を `make_clock(settings)` へ(import行含め実質2行変更) | 4 |
| `backend/src/latch/auth/testkeys/__init__.py` | `_keys_dir()` 追記+`load_private_key`/`load_jwks` の読み出し先差し替え | 5 |
| `backend/tests/unit/auth/test_tools.py` | 鍵ディレクトリ差し替え試験1件追記 | 5 |
| `backend/src/latch/events/__main__.py` | `publish` サブコマンド追記(6値・3点組・--raw-payload) | 6 |
| `Makefile` | staging-*/load-*ターゲット13個の追記+`.PHONY` 行への名前追記のみ | 8 |
| `.gitignore` | `.staging/` 1行追記(design §2.4「git管理外の固定パス」の実現手段) | 7 |

design §3.1に対する本計画の追加2点(理由つき・報告書に記載不要の既定差分): ①`test_clock_wiring.py`(design §3.1の「test_settings.py ほか 追随」の趣旨内・既定経路変更のピン)②`.gitignore`(design §2.4が鍵を「git管理外」と規定するための実現手段・426b0aの前例どおり)。

コミットしないもの: `backend/.venv/`・`__pycache__/`・`.staging/`(鍵ペア。.gitignoreで管理外)・`backend/data/geo/`(git管理外の既定)。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **`compose.yaml`(ci・一切無変更)**・`docker/` のうち `docker/postgres/`(既存のまま流用・変更しない)。`docker/nginx/` は新規ファイル `staging.conf` のみ作成可
- **ci系Makefileターゲットの定義行**(setup・up・down・ps・logs・lint・test・test-ci・migrate・geo-download・geo-import・geo-verify・g1-gate・embed-smoke・jev-smoke・g2-gate)。Makefileは§4の追記のみ
- **ci環境への状態変更操作**(§0のとおり)。ci-db(latch-ci-db)・latch-ci-*ボリュームへの書込み
- **Clock契約**: `core/clock.py` の既存クラス3つ・既存DI試験・arch testの`ALLOWED`・`FORBIDDEN_TOKENS` 定義
- **API/Worker/パイプライン本体**: `intents/`・`users/`・`latches/`・`notifications/`・`safety/`・`ratelimit/`・`geo/`・`llm/`・`g1gate/`・`g2gate/`・`auth/` のうち `testkeys/__init__.py` 以外(prod起動ガード含む・design §2.4)。`worker/` のうち `__main__.py` 以外全て。`events/` のうち `__main__.py` 以外(`bus.py`・`pubsub_bus.py`・`relay.py` — publish形式の契約は変更しない)
- **DBスキーマ**: `backend/alembic/`(0001〜0006不変・参照のみ・追加もしない)
- **依存・秘匿値**: `backend/pyproject.toml`・`backend/uv.lock`・`.env`・`.env.example`(新settingsキーは既定値付きのためenv追加不要)
- **レイテンシのp50/p95分布スタブ拡張** — ws-6スコープ(design §2.6・settings引用#19の既定どおり)。本単位では固定msのdelay envをcomposeで設定可能にするまで
- **ダッシュボード(latch-dashboard)の器・計測系ポート/サービス** — ws-4(design §2.6・supervisor承認A3)。ws-1が保証するのはYAMLアンカー切り出しによる追加しやすさのみ
- **rate(速度倍率)の実装** — design §2.2にYAGNI明記(sweeper/Runnerの待機はasyncio.sleep=実時間のため速くならない)
- **staging/loadの監視プローブ・合成監視・バックアップ/復元先DB** — ws-7設計で(design §1.3)
- **frontend・prototype・docs/docs本文**・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/` の既存ファイル(M0〜M3のdesign・plan・report・ws-2-design)・`docs/testassets/`(本単位はdocs改版なし・design §3.2)
- **`backend/tests/` の§4に列挙した以外の既存試験**(既存試験の期待値変更は一切しない — 追記のみ)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 9で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(unit=1219+新規43件=1262件。実数を報告書へ記録)
   検証: `make lint && make test` — ともにexit 0。`make test` の末尾の件数を報告書へ
2. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
3. **実時間参照が `core/clock.py` のみ・arch testグリーン**
   検証: `rg -n 'datetime\.now|utcnow|date\.today|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
4. **ci環境への変更ゼロ・依存・マイグレーション無変更**
   検証: `git diff main -- compose.yaml docker/postgres backend/alembic backend/pyproject.toml backend/uv.lock .env.example` — 出力なし(空)。`git diff main -- Makefile` の削除行(`-`始まり)が `.PHONY` の1行のみ(レシピ・ターゲット定義の削除なし)
5. **compose定義の妥当性**
   検証: `docker compose -f compose.staging.yaml config -q; echo $?` と `docker compose -f compose.load.yaml config -q; echo $?` — ともに `0`
6. **受け入れ確認(§7手順1〜9)がすべてグリーン・証拠が報告書に記録**(10 §5の形式: 試験ID・実施日・環境・入力条件・合否)。ただし手順7・9の `make test-ci` は **「スーパーバイザー検証待ち」と報告書へ記録**(§0)
7. **変更ファイルが§4の一覧どおり**
   検証: 報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み・19ファイル)と完全一致。`git status --short` が空(`.staging/`・`backend/data/` が現れない)
8. **worker実稼働確認が報告書にある**(design §1.4(b)・§7手順1のsubscription作成ログ+手順5のEvent消化=workerからDB・Pub/Subへの実到達の証拠)

## 7. 報告形式と検証手順

**結果ファイル**: `docs/plans/M4/ws-1-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M4 ws-1(staging・load環境構築)実行報告

- ブランチ: m4-ws-1 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test(unit件数) | PASS/FAIL | <出力末尾とunit全件数> |
| 2 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 3 | 実時間参照がclock.pyのみ・arch test | PASS/FAIL | <rg出力の要点+arch test結果> |
| 4 | ci無変更・依存・alembic無変更 | PASS/FAIL | <git diff出力(空なら「空」)> |
| 5 | compose config妥当性 | PASS/FAIL | <両configのexit code> |
| 6 | 受け入れ手順1〜9 | PASS/FAIL | <下の受け入れ記録参照> |
| 7 | 変更ファイル=§4の19ファイル | PASS/FAIL | <git diff --name-only出力> |
| 8 | worker実稼働確認 | PASS/FAIL | <subscription作成ログの行+手順5のprocessed> |

## make test-ci(1480維持)
- **スーパーバイザー検証待ち**(§0の指示によりagent3は未実行。本単位はマイグレーション追加なし)

## 受け入れ確認の記録(10 第5節の形式: 試験ID・実施日・環境・入力条件・合否)
| 試験ID | 手順 | 実施日 | 環境 | 入力条件 | 合否 | 証拠(要点) |
|---|---|---|---|---|---|---|
| ws1-acc-1 | 手順1 staging-up+worker実稼働 | <日> | latch-staging(worktree) | make staging-up | | <ps表示+workerログの該当行> |
| ws1-acc-2 | 手順2 migrate+別DB確認 | <日> | 両プロジェクト | alembic upgrade head | | <両alembic_version・両users件数> |
| ws1-acc-3 | 手順3 nginx越しAPI | <日> | latch-staging | staging-token→token→users | | <201・/health 3ポート> |
| ws1-acc-4 | 手順4 Clock制御 | <日> | latch-staging | 実施ISO: <値> | | <set前後のserver_time> |
| ws1-acc-5 | 手順5 イベント注入 | <日> | latch-staging | created/参照不在/毒 | | <match_events 3行のstatus> |
| ws1-acc-6 | 手順6 worker複数台冪等 | <日> | latch-staging | 同一Event2回 | | <重複0行・restart 0> |
| ws1-acc-7 | 手順7 隔離(ci起動確認) | <日> | ci+latch-staging | staging起動中 | | <ci ps表示・make test> |
| ws1-acc-8 | 手順8 load環境up+同時稼働 | <日> | latch-load | make load-up | | <両ps表示> |
| ws1-acc-9 | 手順9 既存回帰+ci継続 | <日> | 両方 | lint/test/staging-down | | <lint・test件数・ci ps> |

## 固定値の変更有無(design §2・本計画)
- 案B(別プロジェクト)・ポート表・latch:clock:control・基準点JSON形式・6種choices・.staging/keys: 変更なし / 変更あり(<前→後+理由>)
- 本計画の追加2点(test_clock_wiring.py・.gitignore): 実施どおり / 差分あり(<内容>)

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

**検証手順(受け入れ確認。design §4.2の9項目 → 手順1〜9。Task 9で実施)**:

- 事前: `make lint && make test` がグリーンであること(Task 8完了後)。
- 変数は手順3で採取した `IDP`(IdPトークン)・`ACC`(アクセストークン)・`INTENT`(intent UUID)を以降の手順で使い回す(同じシェルセッション内で実施)。

手順1(staging-upとworker実稼働・design §4.2-1):

```bash
make staging-keys
ls .staging/keys
make staging-up
docker compose -f compose.staging.yaml ps
docker compose -f compose.staging.yaml logs worker1 2>&1 | tail -5
docker compose -f compose.staging.yaml logs worker2 2>&1 | tail -5
```

期待: `ls` に `idp_test_private.pem` と `idp_test_jwks.json`。`staging-up` がhealthy待ちでexit 0。`ps` は8サービス(db・redis・pubsub・api1・api2・nginx・worker1・worker2)がRunning・うちapi1/api2/nginx/db/redisはhealthy。workerログに `pubsub topic created`・`pubsub subscription created`・`worker started (app_env=staging)`。restart回数0(`docker compose -f compose.staging.yaml ps` のStateから確認)。

手順2(migrateと別DBの確認・design §4.2-2):

```bash
make staging-migrate
docker compose -f compose.staging.yaml exec -T db psql -U latch -d latch -tAc "SELECT version_num FROM alembic_version;"
docker compose exec -T db psql -U latch -d latch -tAc "SELECT version_num FROM alembic_version;"
docker compose -f compose.staging.yaml exec -T db psql -U latch -d latch -tAc "SELECT COUNT(*) FROM users;"
docker compose exec -T db psql -U latch -d latch -tAc "SELECT COUNT(*) FROM users;"
```

期待: staging側のalembic_version=`0006`・ci側も`0006`(バージョンは同一でも**DB実体が別**のことが次の行で確定する)。stagingのusers=0(未シード)。ciのusers件数は手順9の再取得時に**不変**であること(記録して比較)。

手順3(nginx越しのAPI・design §4.2-3):

```bash
IDP=$(make staging-token SUBJECT=u1 | tail -1)
ACC=$(curl -s http://127.0.0.1:18000/v1/auth/token -H 'Content-Type: application/json' \
  -d "{\"provider\":\"google\",\"idp_token\":\"$IDP\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:18000/v1/users \
  -H "Authorization: Bearer $ACC" -H 'Content-Type: application/json' \
  -d '{"display_name":"staging-u1","birth_date":"1995-06-15"}'
curl -s http://127.0.0.1:18001/health; echo
curl -s http://127.0.0.1:18002/health; echo
curl -s http://127.0.0.1:18000/health; echo
```

期待: users POST=`201`。/healthが3ポートとも `{"status": "ok", "server_time": "<UTC ISO8601>"}`(18001=api1直・18002=api2直・18000=nginx経由。引用#16のClock消費点)。JWTがstaging鍵ペアで署名され、api側JWKS(同ペアのマウント)で検証が通ることがこの一連で立証される。

手順4(Clock制御・design §4.2-4・ISO明示規律):

```bash
make staging-clock ARGS="show"
curl -s http://127.0.0.1:18000/health; echo
make staging-clock ARGS="set 2026-10-04T00:00:00+09:00"
curl -s http://127.0.0.1:18000/health; echo
make staging-clock ARGS="advance 86400"
curl -s http://127.0.0.1:18000/health; echo
make staging-clock ARGS="show"
```

期待: 未操作時の `show` は `control: unset (real time)`・server_timeは実時間(±数十秒)。set後のserver_timeは `2026-10-03T15:00:xx+00:00` 近傍(JST 10-04 0時=UTC 10-03 15:00)に**追随**。advance 86400後は `2026-10-04T15:00:xx+00:00` 近傍。2回目のshowが基準点(real/virtual)を表示する。実施したISO値と実際のserver_timeを報告書へ記録する。

手順5(イベント注入・design §4.2-5):

```bash
INTENT=$(curl -s -X POST http://127.0.0.1:18000/v1/intents \
  -H "Authorization: Bearer $ACC" -H 'Content-Type: application/json' \
  -d '{"status":"draft","raw_text":"土曜の夜は博多で焼肉に行きたい"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["intent"]["id"])')
make staging-publish ARGS="publish created --intent-id $INTENT"
sleep 5
docker compose -f compose.staging.yaml exec -T db psql -U latch -d latch -c \
  "SELECT event_type, source_intent_id, status FROM match_events ORDER BY created_at;"
make staging-publish ARGS="publish created --intent-id 00000000-0000-0000-0000-000000000000"
sleep 5
cd backend && LATCH_PUBSUB_EMULATOR_HOST=127.0.0.1:18085 LATCH_PUBSUB_PROJECT_ID=latch-staging \
  uv run python -m latch.events publish created --intent-id "$INTENT" \
  --raw-payload "{\"event_type\":\"created\",\"source_intent_id\":\"$INTENT\",\"version\":\"not-int\"}"
cd ..
sleep 40
docker compose -f compose.staging.yaml exec -T db psql -U latch -d latch -c \
  "SELECT event_type, source_intent_id, status FROM match_events ORDER BY created_at;"
```

期待: (a)実在Intent宛のcreated → `status=processed`(workerが消化した=worker→Pub/Sub→DBの実到達。design §1.4(b)のDB接続証拠でもある)。(b)全ゼロUUID(参照不在)→ `processed` で破棄(**quarantinedでない** — 06 §9の区別)。(c)raw-payload(型不正version)→ 最終 `quarantined`(毒経路。再試行を待つためsleep 40)。注: (c)をmakeでなく直接コマンドで実行するのはmakeの引数展開とJSON引用の衝突を避けるため(env値はmake staging-publishと同一)。

手順6(worker複数台の冪等・design §4.2-6):

```bash
make staging-publish ARGS="publish created --intent-id $INTENT"
sleep 3
make staging-publish ARGS="publish created --intent-id $INTENT"
sleep 5
docker compose -f compose.staging.yaml exec -T db psql -U latch -d latch -c \
  "SELECT event_type, source_intent_id, payload->>'version' AS v, COUNT(*) FROM match_events GROUP BY 1,2,3 HAVING COUNT(*) > 1;"
docker compose -f compose.staging.yaml ps
```

注: match_eventsにversion列はなく `payload->>'version'`(一意索引ux_match_events_idempotencyと同一式)で重複を見る。


期待: 重複行のSELECTは**0行**(同一Event 2回投入でもUNIQUE制約で1行・worker1/worker2が競合して消化しても二重処理しない)。worker2台ともRunning・restart 0。

手順7(隔離の確認・design §4.2-7):

```bash
docker compose ps
cd backend && uv run pytest -m "not integration" -q; cd ..
```

期待: staging起動中でもci常設環境(`latch-ci`)の全サービスがRunning/healthyのまま。unit試験が通常どおりグリーン。**`make test-ci` は実行しない**(§0)→ 報告書へ「スーパーバイザー検証待ち」と記録。

手順8(load環境と同時稼働・design §4.2-8):

```bash
make load-up
docker compose -f compose.load.yaml ps
docker compose -f compose.staging.yaml ps
```

期待: load側8サービスがRunning(28000系ポート・healthyはstagingと同基準)。**stagingとloadが同時起動でも無干渉**(双方のpsが全Running・ポート28000系と18000系の分離)。確認後に `make load-down`(手順9の前にstaging/downへ進むため)。

手順9(既存回帰と後片付け・design §4.2-9):

```bash
make lint && make test
make staging-down
docker compose ps
```

期待: lint・unit全件グリーン(件数を記録)。`staging-down` 後もci常設環境がRunning/healthyで**継続稼働**。ci users件数を手順2の記録と比較して不変であることを確認。staging/loadのボリュームは保持(downのみ・`down -v` はしない)。

---

## 8. 実装ステップ(TDD。Task 1〜9の順で実行する)

### Task 1: settings.pyへclock_mode・auth_testkeys_dirを追加(機械ピン)

**Files:**
- Modify: `backend/src/latch/settings.py`(末尾へ追記)
- Test: `backend/tests/unit/test_settings.py`(追記)

**Interfaces:**
- Produces: `Settings.clock_mode: str = "system"`(Task 2の`make_clock`が消費)・`Settings.auth_testkeys_dir: str = ""`(Task 5の`_keys_dir`が消費)。どちらもenv_prefix=`LATCH_`のため `LATCH_CLOCK_MODE` / `LATCH_AUTH_TESTKEYS_DIR` で上書き可能(pydantic-settingsの既定経路)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_settings.py` の `test_settings_defaults` を次のように2箇所追記する(delenv 2行・assert 2行。**既存行は1行も削除・変更しない**):

```python
def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("LATCH_APP_ENV", raising=False)
    monkeypatch.delenv("LATCH_LOG_LEVEL", raising=False)
    monkeypatch.delenv("LATCH_CLOCK_MODE", raising=False)
    monkeypatch.delenv("LATCH_AUTH_TESTKEYS_DIR", raising=False)
    s = Settings()
    assert s.app_env == "ci"
    assert s.log_level == "INFO"
    assert s.reeval_runner_interval_sec == 60  # M2 ws-6(design §2.8)
    assert s.reeval_runner_batch_limit == 50
    assert s.sweeper_batch_limit == 50  # M3 ws-2(06 §6・期限切れバッチ1tick上限)
    assert s.reset_retry_sec == 300  # M3 ws-2(04 §5・リセット失敗時の再試行間隔)
    assert s.push_mode == "stub"  # M3 ws-3(design §3.2・realはG3後)
    assert s.push_stub_delay_ms == 0  # M3 ws-3(スタブ遅延注入)
    assert s.clock_mode == "system"  # M4 ws-1(design §2.2・ciはSystemClock既定)
    assert s.auth_testkeys_dir == ""  # M4 ws-1(design §2.4・空=同梱鍵)
```

同ファイルの末尾へ新試験を追記:

```python
def test_settings_clock_mode_and_testkeys_dir_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_CLOCK_MODE", "controlled")
    monkeypatch.setenv("LATCH_AUTH_TESTKEYS_DIR", "/keys")
    s = Settings()
    assert s.clock_mode == "controlled"
    assert s.auth_testkeys_dir == "/keys"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_settings.py -v`
Expected: FAIL(`AttributeError: 'Settings' object has no attribute 'clock_mode'`。既存4試験はPASS)

- [ ] **Step 3: 最小実装**

`backend/src/latch/settings.py` の**末尾**(`push_stub_delay_ms` の行の後)へ追記:

```python

    # --- staging/loadのテスト注入経路(M4 ws-1・design §2.2/§2.4)---
    # "system": SystemClock(既定・ciはこのまま) / "controlled": Redis基準の
    # 共有制御Clock(latch:clock:control・staging/load composeで設定)。
    # 未実装値の指定はmake_clockがValueError(push_modeと同一のfail-fast)
    clock_mode: str = "system"
    # staging専用テスト鍵ペアのディレクトリ(idp_test_private.pem /
    # idp_test_jwks.json)。空=同梱ディレクトリ(ci既定・無影響)。発行側
    # (issue-idp-token)と検証側(JWKS)が同一値を見る(auth/testkeys/_keys_dir)
    auth_testkeys_dir: str = ""
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_settings.py -v`
Expected: PASS(既存4+追記分)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/settings.py backend/tests/unit/test_settings.py
git commit -m "feat: add clock_mode and auth_testkeys_dir settings for ws-1"
```

### Task 2: ControlledClock・_compute_now・make_clock(clock.py追記)

**Files:**
- Modify: `backend/src/latch/core/clock.py`(import 2行+末尾追記。**既存3クラスは1行も変更しない**)
- Test: `backend/tests/unit/test_controlled_clock.py`(新規作成)

**Interfaces:**
- Consumes: `Settings.clock_mode`・`Settings.redis_url`(Task 1)
- Produces: `ControlledClock(client, *, key="latch:clock:control")` — `now() -> datetime`(tz-aware UTC)・`set(when: datetime)`(後退可・naive拒否)・`advance(delta: timedelta)`・`read_base() -> tuple[float, float] | None`(Task 3のCLIが消費)。`_compute_now(base_real: float, base_virtual: float, real_now: float) -> datetime`(純関数)。`make_clock(settings, *, redis_client=None) -> Clock`(Task 3/4が消費。`redis_client`はunit試験がfakeredisを注入するためのDI)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_controlled_clock.py` を新規作成:

```python
"""ControlledClockとmake_clockのunit試験(M4 ws-1 design §4.1)。

時刻計算は純関数_compute_nowで決定的に、Redisはfakeredis(同期・
decode_responses=True)で決定的に検証する。実時間待ちは置かない。
"""

from datetime import UTC, datetime, timedelta, timezone

import fakeredis
import pytest

from latch.core.clock import (
    ControlledClock,
    SystemClock,
    _compute_now,
    make_clock,
)
from latch.settings import Settings

JST = timezone(timedelta(hours=9))


def _clock() -> ControlledClock:
    return ControlledClock(fakeredis.FakeRedis(decode_responses=True))


def test_compute_now_offsets_by_elapsed_real_time():
    # 基準(real=1000.0, virtual=2000.0)から実時間が5秒経過 → 仮想2005.0
    assert _compute_now(1000.0, 2000.0, 1005.0) == datetime.fromtimestamp(
        2005.0, tz=UTC
    )


def test_compute_now_returns_tz_aware_utc():
    got = _compute_now(0.0, 0.0, 10.0)
    assert got.tzinfo is not None
    assert got.utcoffset() == timedelta(0)


def test_key_absent_now_is_real_time():
    # キー不在=オフセット0=SystemClockと等価(design §2.2)
    clock = _clock()
    before = SystemClock().now()
    got = clock.now()
    after = SystemClock().now()
    assert before <= got <= after
    assert clock.read_base() is None


def test_set_writes_anchor_and_now_follows():
    clock = _clock()
    when = datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC)
    clock.set(when)
    base = clock.read_base()
    assert base[1] == when.timestamp()  # virtual基準点=指定時刻
    assert abs(base[0] - SystemClock().now().timestamp()) < 60  # real=発行時点
    first = clock.now()
    assert abs((first - when).total_seconds()) < 5  # 実時間経過分だけ進む


def test_set_accepts_jst_and_normalizes_to_utc():
    clock = _clock()
    clock.set(datetime(2026, 10, 4, 9, 0, 0, tzinfo=JST))
    assert clock.read_base()[1] == datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC).timestamp()


def test_set_rejects_naive():
    with pytest.raises(ValueError):
        _clock().set(datetime(2026, 10, 4, 0, 0, 0))


def test_set_backwards_moves_virtual_back():
    # 後退可(FakeClock.setと同一契約。「過去不可」境界試験の両方向に対応)
    clock = _clock()
    clock.set(datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC))
    clock.set(datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC))
    assert clock.now() < datetime(2026, 9, 1, 0, 1, 0, tzinfo=UTC)


def test_advance_adds_to_virtual_keeping_real_anchor():
    clock = _clock()
    clock.set(datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC))
    base_before = clock.read_base()
    clock.advance(timedelta(days=1))
    base_after = clock.read_base()
    assert base_after[0] == base_before[0]  # 実アンカー保持(経過分はnow()が加算)
    assert base_after[1] == base_before[1] + 86400.0


def test_advance_on_uncontrolled_key_initializes_anchor():
    clock = _clock()
    clock.advance(timedelta(days=1))
    assert clock.read_base() is not None  # 制御未開始でも基準点を作る
    expected = SystemClock().now() + timedelta(days=1)
    assert abs((clock.now() - expected).total_seconds()) < 5


def test_now_is_tz_aware_utc_under_control():
    clock = _clock()
    clock.set(datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC))
    got = clock.now()
    assert got.tzinfo is not None
    assert got.utcoffset() == timedelta(0)


def test_redis_failure_propagates_fail_closed():
    # Redis到達不能は例外で伝播(「制御下にあるつもりが実時間」の静かな誤りを
    # 許さない — design §2.2)。握って実時間へ落とす実装を禁止するピン
    class _BrokenClient:
        def get(self, key):
            raise ConnectionError("redis down")

        def set(self, key, value):
            raise ConnectionError("redis down")

    clock = ControlledClock(_BrokenClient())
    with pytest.raises(ConnectionError):
        clock.now()
    with pytest.raises(ConnectionError):
        clock.set(datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC))


def test_make_clock_system_mode(monkeypatch):
    monkeypatch.delenv("LATCH_CLOCK_MODE", raising=False)
    assert isinstance(make_clock(Settings()), SystemClock)


def test_make_clock_controlled_mode_with_injected_client():
    client = fakeredis.FakeRedis(decode_responses=True)
    clock = make_clock(Settings(clock_mode="controlled"), redis_client=client)
    assert isinstance(clock, ControlledClock)
    clock.set(datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC))  # 注入クライアントで動く
    assert clock.read_base() is not None


def test_make_clock_unknown_mode_raises():
    # 未実装値はValueError(push_mode/llm_modeと同一のfail-fast規律)
    with pytest.raises(ValueError):
        make_clock(Settings(clock_mode="bogus"))
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_controlled_clock.py -v`
Expected: FAIL with `ImportError: cannot import name 'ControlledClock'`

- [ ] **Step 3: 最小実装**

`backend/src/latch/core/clock.py` のimport部を次のようにする(既存行は保持・`json`とSettingsのimportを追加):

```python
from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, timedelta, timezone

from latch.settings import Settings
```

同ファイルの**末尾**(`FakeClock` の `now()` の後)へ追記:

```python

class ControlledClock(Clock):
    """staging/load用の共有制御Clock(10 §1「時刻操作」・M4 ws-1 design §2.2)。

    Redis 1キーに「基準点(実時刻↔仮想時刻の対応)」を置き、api 2台+worker
    の複数プロセスが同一キー参照で同一の仮想時刻を見る。操作はホスト側CLI
    (python -m latch.core.clock_cli)から行う。
    - キー不在 = オフセット0 = 実時間(「制御していない状態」はSystemClockと等価)
    - Redis到達不能は例外でfail-closed(例外を握って実時間へ落とさない。
      stagingのRedisは常設restart: unless-stopped)
    - rate(速度倍率)は設けない(YAGNI): sweeper/Runnerの待機はasyncio.sleep
      (実時間)のため速くならない。Clock操作で期限を未来へ動かすのが10 §1の
      規定手段
    """

    DEFAULT_KEY = "latch:clock:control"  # ci環境のRedisとは別インスタンスのため名前衝突なし

    def __init__(self, client, *, key: str = DEFAULT_KEY) -> None:
        # clientは同期redisクライアント(get/setを持つオブジェクト。unit試験はfakeredis)
        self._client = client
        self._key = key

    def read_base(self) -> tuple[float, float] | None:
        """基準点(real, virtual)の生値をunix秒floatで返す。キー不在はNone。"""
        raw = self._client.get(self._key)
        if raw is None:
            return None
        data = json.loads(raw)
        return float(data["real"]), float(data["virtual"])

    def set(self, when: datetime) -> None:
        """基準点を「いま実時間→when仮想時間」へ書き換える(後退可 — FakeClock.setと同一契約)。"""
        if when.tzinfo is None:
            raise ValueError("ControlledClock.set は tz-aware な時刻を要求する")
        self._write_base(
            datetime.now(UTC).timestamp(), when.astimezone(UTC).timestamp()
        )

    def advance(self, delta: timedelta) -> None:
        """仮想基準点をdeltaだけ前進させる(実アンカーは保持 — 経過分はnow()が加算)。"""
        base = self.read_base()
        if base is None:
            base = self._initialize_base()
        self._write_base(base[0], base[1] + delta.total_seconds())

    def now(self) -> datetime:
        base = self.read_base()
        if base is None:
            return datetime.now(UTC)
        return _compute_now(base[0], base[1], datetime.now(UTC).timestamp())

    def _write_base(self, real: float, virtual: float) -> None:
        self._client.set(self._key, json.dumps({"real": real, "virtual": virtual}))

    def _initialize_base(self) -> tuple[float, float]:
        """制御未開始の状態で基準点を作る(いま実時間=いま仮想時間)。"""
        real_now = datetime.now(UTC).timestamp()
        self._write_base(real_now, real_now)
        return real_now, real_now


def _compute_now(
    base_real: float, base_virtual: float, real_now: float
) -> datetime:
    """基準点+実時間経過から仮想時刻を計算する(純関数 — unit試験は決定的)。"""
    return datetime.fromtimestamp(base_virtual + (real_now - base_real), tz=UTC)


def make_clock(settings: Settings, *, redis_client=None) -> Clock:
    """clock_mode設定でSystemClock/ControlledClockを選ぶ組立ヘルパー(design §2.2)。

    create_app()・worker/__main__.pyの既定構築経路。clock引数を明示する既存DI
    (unit/integration試験)はこの関数を通らないため無傷。redis_clientはunit
    試験がfakeredisを注入するためのDI(既定=redis_urlから構築。redis-pyは
    遅延接続のため構築のみでI/Oは起きない)。
    """
    mode = settings.clock_mode
    if mode == "system":
        return SystemClock()
    if mode == "controlled":
        if redis_client is None:
            import redis as redis_sync  # 遅延import(clock.pyの依存を最小化)

            redis_client = redis_sync.Redis.from_url(
                settings.redis_url, decode_responses=True
            )
        return ControlledClock(redis_client)
    raise ValueError(f"unknown clock_mode: {mode!r} (system/controlled)")
```

注: `datetime.now(UTC)` は本ファイルのみarch testのALLOWED(引用#12・`ALLOWED`定義は変更しない)。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_controlled_clock.py tests/unit/test_clock.py tests/unit/test_clock_reproducibility.py tests/unit/test_arch_no_direct_time.py -v`
Expected: PASS(新規14件+既存clock系・arch test全件 — 既存契約が無傷であることの確認)

- [ ] **Step 5: コミット**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
git add backend/src/latch/core/clock.py backend/tests/unit/test_controlled_clock.py
git commit -m "feat: add ControlledClock with redis-backed shared anchor"
```

### Task 3: clock_cli新設(show/set/advance)

**Files:**
- Create: `backend/src/latch/core/clock_cli.py`
- Test: `backend/tests/unit/test_clock_cli.py`(新規作成)

**Interfaces:**
- Consumes: `ControlledClock`(Task 2の`read_base`/`set`/`advance`)・`Settings.redis_url`(env上書きでstaging/load宛へ)
- Produces: `python -m latch.core.clock_cli show|set <ISO8601>|advance <秒>`(Makefileターゲット`staging-clock`がラップ — Task 8)。`build_parser() -> argparse.ArgumentParser`・`_parse_iso(raw: str) -> datetime`(naive拒否)・`_build_clock() -> ControlledClock`・`run_show/run_set/run_advance(clock) -> int`(unit試験が直接呼ぶ)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_clock_cli.py` を新規作成:

```python
"""clock_cli(制御Clock操作CLI)のunit試験(M4 ws-1 design §4.1)。

Redisはfakeredis(同期)へ差し替え、実Redis不要でキーへの書込値を検証する。
"""

import json
from datetime import UTC, datetime

import fakeredis
import pytest

import latch.core.clock_cli as clock_cli
from latch.core.clock import ControlledClock, SystemClock

CONTROL_KEY = "latch:clock:control"
JST_10_04_0 = datetime.fromisoformat("2026-10-04T00:00:00+09:00")
UTC_10_03_15 = datetime(2026, 10, 3, 15, 0, 0, tzinfo=UTC)


def _clock() -> ControlledClock:
    return ControlledClock(fakeredis.FakeRedis(decode_responses=True))


def test_parse_iso_accepts_jst_offset():
    dt = clock_cli._parse_iso("2026-10-04T00:00:00+09:00")
    assert dt.isoformat() == "2026-10-04T00:00:00+09:00"


def test_parse_iso_rejects_naive():
    # naiveは拒否(JST意図の入力がUTC解釈される9時間ずれのタイムボム防止)
    with pytest.raises(ValueError):
        clock_cli._parse_iso("2026-10-04T00:00:00")


def test_parser_advance_accepts_float_seconds():
    args = clock_cli.build_parser().parse_args(["advance", "86400.5"])
    assert args.seconds == 86400.5


def test_run_set_writes_anchor_json():
    clock = _clock()
    assert clock_cli.run_set(clock, JST_10_04_0) == 0
    base = clock.read_base()
    assert base[1] == UTC_10_03_15.timestamp()  # JST入力がUTC基準点へ正規化
    assert abs(base[0] - SystemClock().now().timestamp()) < 60


def test_run_advance_moves_virtual_and_prints(capsys):
    clock = _clock()
    clock_cli.run_set(clock, JST_10_04_0)
    assert clock_cli.run_advance(clock, 86400) == 0
    base = clock.read_base()
    assert base[1] == pytest.approx(
        datetime(2026, 10, 4, 15, 0, 0, tzinfo=UTC).timestamp()
    )
    assert "advanced by 86400.0s" in capsys.readouterr().out


def test_run_show_uncontrolled_prints_unset(capsys):
    assert clock_cli.run_show(_clock()) == 0
    out = capsys.readouterr().out
    assert "unset" in out
    assert "virtual now" in out


def test_run_show_controlled_prints_base(capsys):
    clock = _clock()
    clock_cli.run_set(clock, JST_10_04_0)
    assert clock_cli.run_show(clock) == 0
    out = capsys.readouterr().out
    assert "control base" in out
    assert CONTROL_KEY in out


def test_main_set_dispatches_to_fakeredis(monkeypatch):
    clients = []

    def _fake_build() -> ControlledClock:
        client = fakeredis.FakeRedis(decode_responses=True)
        clients.append(client)
        return ControlledClock(client)

    monkeypatch.setattr(clock_cli, "_build_clock", _fake_build)
    assert clock_cli.main(["set", "2026-10-04T00:00:00+09:00"]) == 0
    data = json.loads(clients[0].get(CONTROL_KEY))
    assert data["virtual"] == UTC_10_03_15.timestamp()


def test_main_set_rejects_naive_with_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(clock_cli, "_build_clock", lambda: _clock())
    assert clock_cli.main(["set", "2026-10-04T00:00:00"]) == 2
    assert "error" in capsys.readouterr().out


def test_main_show_dispatches(monkeypatch, capsys):
    monkeypatch.setattr(clock_cli, "_build_clock", lambda: _clock())
    assert clock_cli.main(["show"]) == 0
    assert "virtual now" in capsys.readouterr().out
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_clock_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.core.clock_cli'`

- [ ] **Step 3: 最小実装**

`backend/src/latch/core/clock_cli.py` を新規作成:

```python
"""staging/loadの制御Clock操作CLI(M4 ws-1 design §2.2)。

python -m latch.core.clock_cli show — 制御状態(基準点)と仮想時刻を表示
python -m latch.core.clock_cli set <ISO8601> — 仮想時刻を移動する(後退可)
python -m latch.core.clock_cli advance <秒> — 仮想時刻を前進させる

宛先Redisは LATCH_REDIS_URL のenv上書きでstaging/load宛へ向ける(Makefileの
staging-clockターゲットが127.0.0.1:16379/26379を設定)。同期redisクライアント
を使用する(ホスト側からの単発操作・イベントループ不要)。実時間の表示は
SystemClock経由(arch test — 本モジュールではdatetime.nowを直接書かない)。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from latch.core.clock import ControlledClock, SystemClock
from latch.settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.core.clock_cli",
        description="共有制御Clock(latch:clock:control)の操作CLI(M4 ws-1)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="制御状態(基準点)と仮想時刻を表示")
    p_set = sub.add_parser("set", help="仮想時刻をISO8601へ移動する(後退可)")
    p_set.add_argument(
        "when",
        help="タイムゾーン付きISO8601(例: 2026-10-04T00:00:00+09:00)",
    )
    p_adv = sub.add_parser("advance", help="仮想時刻を<秒>だけ前進させる")
    p_adv.add_argument("seconds", type=float, help="前進秒(例: 86400 = 1日)")
    return parser


def _parse_iso(raw: str) -> datetime:
    """ISO8601をパースする(naiveは拒否 — JST意図のUTC解釈ずれを防ぐ)。"""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError(
            "タイムゾーン付きで指定する(例: 2026-10-04T00:00:00+09:00)"
        )
    return dt


def _build_clock() -> ControlledClock:
    """LATCH_REDIS_URL(Settings・env上書き可)宛の同期クライアントで構築する。"""
    import redis as redis_sync

    settings = Settings()
    client = redis_sync.Redis.from_url(settings.redis_url, decode_responses=True)
    return ControlledClock(client)


def run_show(clock: ControlledClock) -> int:
    base = clock.read_base()
    print(f"[clock] key: {clock.DEFAULT_KEY}")
    if base is None:
        print("[clock] control: unset (real time)")
    else:
        print(f"[clock] control base: real={base[0]:.3f} virtual={base[1]:.3f}")
    print(f"[clock] virtual now: {clock.now().isoformat()}")
    print(f"[clock] real now:    {SystemClock().now().isoformat()}")
    return 0


def run_set(clock: ControlledClock, when: datetime) -> int:
    clock.set(when)
    print(f"[clock] set virtual time -> {clock.now().isoformat()}")
    return 0


def run_advance(clock: ControlledClock, seconds: float) -> int:
    clock.advance(timedelta(seconds=seconds))
    print(f"[clock] advanced by {seconds}s -> {clock.now().isoformat()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    clock = _build_clock()
    if args.command == "show":
        return run_show(clock)
    if args.command == "set":
        try:
            when = _parse_iso(args.when)
        except ValueError as exc:
            print(f"[clock] error: {exc}")
            return 2
        return run_set(clock, when)
    if args.command == "advance":
        return run_advance(clock, args.seconds)
    raise AssertionError(f"unreachable: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_clock_cli.py -v`
Expected: PASS(10件)

- [ ] **Step 5: コミット**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
git add backend/src/latch/core/clock_cli.py backend/tests/unit/test_clock_cli.py
git commit -m "feat: add clock control CLI (show/set/advance)"
```

### Task 4: create_app・worker入口の既定Clockをmake_clock経由へ

**Files:**
- Modify: `backend/src/latch/main.py`(import 1行+既定構築 2行)
- Modify: `backend/src/latch/worker/__main__.py`(import 1行+Worker構築 1行)
- Test: `backend/tests/unit/test_clock_wiring.py`(新規作成)

**Interfaces:**
- Consumes: `make_clock`(Task 2)
- Produces: 変更なしの公開契約 — `create_app(clock=None)`・`Worker(clock=None)` の未注入時が「clock_mode設定に従う」挙動になる(clock_mode既定systemのためci挙動は不変)。staging/load composeはenv `LATCH_CLOCK_MODE=controlled` だけでapi/workerの両方を制御Clockへ切り替えられる

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_clock_wiring.py` を新規作成:

```python
"""create_app・worker入口のClock既定経路(M4 ws-1 design §2.2の組立差し替え)。

既存DI試験(clock引数明示)は無傷であることの裏面として、未注入時に
make_clock(Settings)を通ること(=LATCH_CLOCK_MODE設定がapi/workerの両入口で
効くこと)をピンする。controlled構築はredis-pyの遅延接続のためI/Oなし。
"""

import latch.main as main_module
from latch.core.clock import ControlledClock, SystemClock
from latch.main import create_app
from latch.settings import Settings


def test_create_app_default_clock_goes_through_make_clock(monkeypatch):
    sentinel = SystemClock()
    seen = {}

    def _spy(settings):
        seen["settings"] = settings
        return sentinel

    monkeypatch.setattr(main_module, "make_clock", _spy)
    app = create_app()
    assert app.state.clock is sentinel
    assert isinstance(seen["settings"], Settings)


def test_create_app_default_is_system_clock_when_mode_unset(monkeypatch):
    monkeypatch.delenv("LATCH_CLOCK_MODE", raising=False)
    app = create_app()
    assert isinstance(app.state.clock, SystemClock)


def test_worker_main_builds_worker_with_make_clock(monkeypatch):
    import latch.worker.__main__ as worker_main

    captured = {}

    class _StubWorker:
        def __init__(self, settings=None, clock=None):
            captured["settings"] = settings
            captured["clock"] = clock

        def request_shutdown(self) -> None:
            return None

        async def run(self) -> None:
            return None

    monkeypatch.setattr(worker_main, "Worker", _StubWorker)
    monkeypatch.delenv("LATCH_CLOCK_MODE", raising=False)
    worker_main.main()
    assert isinstance(captured["clock"], SystemClock)


def test_worker_main_controlled_mode_uses_redis_url(monkeypatch):
    # staging composeの経路: LATCH_CLOCK_MODE=controlled + LATCH_REDIS_URLで
    # worker入口がControlledClockを構築すること(遅延接続のためI/Oは起きない)
    import latch.worker.__main__ as worker_main

    captured = {}

    class _StubWorker:
        def __init__(self, settings=None, clock=None):
            captured["settings"] = settings
            captured["clock"] = clock

        def request_shutdown(self) -> None:
            return None

        async def run(self) -> None:
            return None

    monkeypatch.setattr(worker_main, "Worker", _StubWorker)
    monkeypatch.setenv("LATCH_CLOCK_MODE", "controlled")
    monkeypatch.setenv("LATCH_REDIS_URL", "redis://127.0.0.1:16379/0")
    worker_main.main()
    assert isinstance(captured["clock"], ControlledClock)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_clock_wiring.py -v`
Expected: FAIL with `AttributeError: <module 'latch.main'> does not have the attribute 'make_clock'`(spyのmonkeypatch先が存在しない。制御モード試験はPASS — 既存SystemClock経路がまだ生きているため)

- [ ] **Step 3: 最小実装**

`backend/src/latch/main.py`: import行を1行変更し、`create_app` 冒頭の既定構築2行を差し替える:

```python
from latch.core.clock import Clock, make_clock
```

```python
    app = FastAPI(title="LATCH API", lifespan=_lifespan)
    app.state.settings = settings if settings is not None else Settings()
    app.state.clock = clock if clock is not None else make_clock(app.state.settings)
```

(既存はclock→settingsの順で `SystemClock()` 直構築。**settingsを先に**置いてからmake_clockへ渡す。`SystemClock` はmain.pyから参照しなくなるためimportから外す — 残すとruff F401。)

`backend/src/latch/worker/__main__.py`: import行とWorker構築行を変更:

```python
from latch.core.clock import make_clock
from latch.settings import Settings
from latch.worker.main import Worker
```

```python
    worker = Worker(settings=settings, clock=make_clock(settings))
```

注: **`worker/main.py`(Worker本体)は変更しない**(`Worker()` 直構築の既定はSystemClockのまま — `test_worker.py::test_worker_defaults_to_system_clock` が無傷である根拠)。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_clock_wiring.py tests/unit/test_app_health.py tests/unit/test_worker.py -v`
Expected: PASS(新規4件+既存DI系全件)

- [ ] **Step 5: コミット**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
git add backend/src/latch/main.py backend/src/latch/worker/__main__.py backend/tests/unit/test_clock_wiring.py
git commit -m "feat: wire default clock through make_clock in api and worker entry"
```

### Task 5: testkeysディレクトリ差し替え(LATCH_AUTH_TESTKEYS_DIR)

**Files:**
- Modify: `backend/src/latch/auth/testkeys/__init__.py`(`_keys_dir` 追記+2関数の読み出し先差し替え)
- Test: `backend/tests/unit/auth/test_tools.py`(追記)

**Interfaces:**
- Consumes: `Settings.auth_testkeys_dir`(Task 1)
- Produces: `load_private_key()`・`load_jwks()` が同一の `_keys_dir()` 経由で鍵ディレクトリを解決する(既定=同梱ディレクトリ・挙動不変)。`LATCH_AUTH_TESTKEYS_DIR` で差し替えると発行側(issue-idp-token)と検証側(JWKS)が自然に揃う。関数シグネチャ・戻り値型は変更なし(idp.py・tools.py・service.pyの呼び出し側は無傷)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/auth/test_tools.py` の末尾へ追記(既存のimport群はそのまま使う):

```python
async def test_testkeys_dir_env_swaps_keypair_for_both_sides(monkeypatch, tmp_path):
    """LATCH_AUTH_TESTKEYS_DIRで発行側と検証側が同一のstaging鍵ペアへ揃うこと
    (M4 ws-1 design §2.4・10 §1「認証のテスト構成」)。"""
    from latch.auth import testkeys

    generate_keypair(out_dir=tmp_path)
    monkeypatch.setenv("LATCH_AUTH_TESTKEYS_DIR", str(tmp_path))
    # 発行側(load_private_key)が生成鍵を見る
    token = issue_idp_token(
        provider="google", subject="staging-u1", iat=int(NOW.timestamp())
    )
    # 検証側(load_jwks)も同一ディレクトリを見る → 検証が通る
    provider, subject = await _verifier().verify(
        provider="google", idp_token=token, clock=FakeClock(NOW)
    )
    assert (provider, subject) == ("google", "staging-u1")
    # 環境変数を外すと同梱JWKSでは検証できない(鍵が本当に差し替わっている
    # ことの裏付け — 片側だけ差し替わる実装を禁止するピン)
    monkeypatch.delenv("LATCH_AUTH_TESTKEYS_DIR")
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(
            provider="google", idp_token=token, clock=FakeClock(NOW)
        )
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_tools.py::test_testkeys_dir_env_swaps_keypair_for_both_sides -v`
Expected: FAIL(環境変数を無視して同梱鍵で発行されるため、末尾の同梱JWKS検証が**成功してしまい**assert `pytest.raises` が失敗する — 差し替えが効いていないことの失敗)

- [ ] **Step 3: 最小実装**

`backend/src/latch/auth/testkeys/__init__.py` のimport部と関数を次のように変更する(定数・`_DIR`は保持):

```python
from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from latch.settings import Settings

_DIR = Path(__file__).resolve().parent

DEFAULT_KID = "test-idp-1"
TEST_ACCESS_SECRET = "latch-ci-test-access-secret-0123456789abcdef"
TEST_ISSUER_PREFIX = "https://idp.ci.latch.test/"
TEST_AUDIENCE = "latch-test-app"


def _keys_dir() -> Path:
    """鍵ディレクトリを決める(既定=同梱・LATCH_AUTH_TESTKEYS_DIRで差し替え)。

    staging専用鍵ペアの注入(10 §1「認証のテスト構成」・M4 ws-1 design §2.4)。
    発行側(issue-idp-token)と検証側(JWKS)が同一関数経由のため、ディレクトリ
    差し替えで自然に揃う。空=同梱(ci既定・無影響)。
    """
    override = Settings().auth_testkeys_dir.strip()
    return Path(override) if override else _DIR


def load_private_key() -> RSAPrivateKey:
    """ci/試験用RS256秘密鍵を返す(テストユーザーJWT発行の署名鍵)。"""
    key = serialization.load_pem_private_key(
        (_keys_dir() / "idp_test_private.pem").read_bytes(), password=None
    )
    assert isinstance(key, RSAPrivateKey)
    return key


def load_jwks() -> dict:
    """JWKS(kid=DEFAULT_KID)をdictで返す(検証側の静的鍵ソース)。"""
    return json.loads((_keys_dir() / "idp_test_jwks.json").read_text(encoding="utf-8"))
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/ -v`
Expected: PASS(追記1件+auth系既存全件 — 既定経路が不変であることの確認)

- [ ] **Step 5: コミット**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
git add backend/src/latch/auth/testkeys/__init__.py backend/tests/unit/auth/test_tools.py
git commit -m "feat: allow staging testkeys dir override via env"
```

### Task 6: events publishサブコマンド(疑似イベント注入)

**Files:**
- Modify: `backend/src/latch/events/__main__.py`(docstring・import・`publish`サブコマンド・`_run_publish`追記)
- Test: `backend/tests/unit/test_events_publish_cli.py`(新規作成)

**Interfaces:**
- Consumes: `EventBus.publish_match_event(event_type=…, intent_id=…, version=…)`(3点組・pubsub_busの既存契約)・`publish_raw(data: bytes)`(毒ペイロード投入用の既存IF)・`Settings.pubsub_emulator_host` / `pubsub_project_id`(env上書きでstaging/load宛)
- Produces: `python -m latch.events publish <event_type> --intent-id <uuid> [--version N] [--raw-payload '<json>']`(Makefileターゲット`staging-publish`がラップ — Task 8)。`PUBLISH_EVENT_TYPES` = 6値タプル・`_run_publish(settings, args) -> int`(unit試験が直接呼ぶ)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_events_publish_cli.py` を新規作成:

```python
"""events publishサブコマンドのunit試験(M4 ws-1 design §4.1)。

PubsubEventBusをスタブへ差し替え、3点組の組立とraw-payload優先、宛先
Settingsの引き渡しを検証する(実エミュレータ不要)。
"""

import uuid

import pytest

import latch.events.__main__ as events_cli
from latch.settings import Settings

INTENT = uuid.UUID("11111111-1111-1111-1111-111111111111")


class _StubBus:
    instances: list["_StubBus"] = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.published: list[dict] = []
        self.raw: list[bytes] = []
        self.closed = False
        _StubBus.instances.append(self)

    async def publish_match_event(self, *, event_type, intent_id, version) -> None:
        self.published.append(
            {"event_type": event_type, "intent_id": intent_id, "version": version}
        )

    async def publish_raw(self, data: bytes) -> None:
        self.raw.append(data)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def stub_bus(monkeypatch):
    _StubBus.instances = []
    monkeypatch.setattr(events_cli, "PubsubEventBus", _StubBus)
    return _StubBus


def _parse_args(argv: list[str]):
    return events_cli.build_parser().parse_args(argv)


async def test_publish_created_assembles_triple(stub_bus, capsys):
    rc = await events_cli._run_publish(
        Settings(),
        _parse_args(["publish", "created", "--intent-id", str(INTENT), "--version", "3"]),
    )
    assert rc == 0
    (bus,) = stub_bus.instances
    assert bus.published == [
        {"event_type": "created", "intent_id": INTENT, "version": 3}
    ]
    assert bus.raw == []
    assert bus.closed is True
    assert f"intent={INTENT}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "event_type",
    ["created", "updated", "deleted", "expired", "scheduled", "embedding_completed"],
)
async def test_publish_accepts_all_six_event_types(stub_bus, event_type):
    # 6種すべて手動発火可(scheduledの常時発行経路は存在しないため、
    # 手動発火はこの種別を観察する唯一の経路 — design §2.5)
    rc = await events_cli._run_publish(
        Settings(),
        _parse_args(["publish", event_type, "--intent-id", str(INTENT)]),
    )
    assert rc == 0
    bus = stub_bus.instances[0]
    assert bus.published[0]["event_type"] == event_type
    assert bus.published[0]["version"] == 1  # 既定


def test_publish_rejects_unknown_event_type(stub_bus):
    with pytest.raises(SystemExit) as ei:
        _parse_args(["publish", "bogus", "--intent-id", str(INTENT)])
    assert ei.value.code == 2
    assert stub_bus.instances == []


def test_publish_requires_intent_id():
    with pytest.raises(SystemExit) as ei:
        _parse_args(["publish", "created"])
    assert ei.value.code == 2


def test_publish_rejects_invalid_uuid():
    with pytest.raises(SystemExit) as ei:
        _parse_args(["publish", "created", "--intent-id", "not-a-uuid"])
    assert ei.value.code == 2


async def test_raw_payload_takes_priority_over_triple(stub_bus):
    # 毒ペイロード・参照不在Event試験用: raw指定時は3点組を発行しない
    rc = await events_cli._run_publish(
        Settings(),
        _parse_args(
            [
                "publish",
                "created",
                "--intent-id",
                str(INTENT),
                "--raw-payload",
                '{"event_type": 123}',
            ]
        ),
    )
    assert rc == 0
    (bus,) = stub_bus.instances
    assert bus.raw == [b'{"event_type": 123}']
    assert bus.published == []


async def test_destination_settings_reach_the_bus(stub_bus, monkeypatch):
    monkeypatch.setenv("LATCH_PUBSUB_EMULATOR_HOST", "127.0.0.1:18085")
    monkeypatch.setenv("LATCH_PUBSUB_PROJECT_ID", "latch-staging")
    rc = await events_cli._run_publish(
        Settings(),
        _parse_args(["publish", "created", "--intent-id", str(INTENT)]),
    )
    assert rc == 0
    (bus,) = stub_bus.instances
    assert bus.settings.pubsub_emulator_host == "127.0.0.1:18085"
    assert bus.settings.pubsub_project_id == "latch-staging"


def test_main_dispatches_publish(stub_bus):
    assert events_cli.main(["publish", "created", "--intent-id", str(INTENT)]) == 0
    assert stub_bus.instances[0].published[0]["intent_id"] == INTENT
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_events_publish_cli.py -v`
Expected: FAIL with `SystemExit: 2`(`publish` サブコマンドが未存在のためargparseが拒否)

- [ ] **Step 3: 最小実装**

`backend/src/latch/events/__main__.py` を次のようにする(既存の `purge-match-sub` 経路は1行も変更しない):

```python
"""イベント基盤の運用CLI(ws-8 design §2.7・M4 ws-1 design §2.5)。

python -m latch.events purge-match-sub — 常設match-events subscriptionを削除
する。make test-ci のpytest終了後・worker復帰前に実行し、テスト中にAPIが
publishしたEventの常設subscriptionへの滞留(未配信メッセージ)を構造的に
全廃する(Pub/Subはsubscription削除で未配信メッセージごと消える)。
worker復帰(run)時の await bus.ensure() がcreate_subscription冪等で再作成
するため、workerは次の新規メッセージから処理を始める。存在しない
(NotFound)は「既に滞留なし」と同じ結果のため成功扱い(exit 0)。

python -m latch.events publish <event_type> --intent-id <uuid> [--version N]
[--raw-payload '<json>'] — Match Eventを手動発行する(10 §1「疑似イベント
注入」・M4 ws-1)。6種(created/updated/deleted/expired/scheduled/
embedding_completed)すべてを手動発火可(scheduledの常時発行経路は存在しない
ため、手動発火はこの種別を観察する唯一の経路)。発行は
EventBus.publish_match_eventの同一経路(3点組 — 発行形式の二重管理を
作らない)。--raw-payloadは生bytesを直接publishする(毒ペイロード・参照不在
Event試験用。3点組より優先)。宛先は LATCH_PUBSUB_EMULATOR_HOST /
LATCH_PUBSUB_PROJECT_ID のenv上書きでstaging/load宛へ向ける(purgeと同一方式)。
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from latch.events.pubsub_bus import PubsubEventBus
from latch.intents.events import (
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_EMBEDDING_COMPLETED,
    EVENT_EXPIRED,
    EVENT_SCHEDULED,
    EVENT_UPDATED,
)
from latch.settings import Settings

PUBLISH_EVENT_TYPES = (
    EVENT_CREATED,
    EVENT_UPDATED,
    EVENT_DELETED,
    EVENT_EXPIRED,
    EVENT_SCHEDULED,
    EVENT_EMBEDDING_COMPLETED,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.events",
        description="イベント基盤の運用CLI(ws-8・M4 ws-1)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "purge-match-sub",
        help="常設match-events subscriptionを削除(滞留メッセージ全廃・"
        "worker復帰時にensure()が再作成)",
    )
    publish = sub.add_parser(
        "publish",
        help="Match Eventを手動発行(6種。10 §1「疑似イベント注入」)",
    )
    publish.add_argument(
        "event_type",
        choices=list(PUBLISH_EVENT_TYPES),
        help="発行するEvent種別",
    )
    publish.add_argument(
        "--intent-id",
        required=True,
        type=uuid.UUID,
        help="対象IntentのUUID",
    )
    publish.add_argument(
        "--version", type=int, default=1, help="3点組のversion(既定1)"
    )
    publish.add_argument(
        "--raw-payload",
        default=None,
        help="生JSON文字列をそのままpublishする(毒ペイロード試験用。"
        "3点組の組立より優先)",
    )
    return parser


async def _run_purge(settings: Settings) -> int:
    bus = PubsubEventBus(settings)
    try:
        bus.delete_subscription()  # NotFoundは握る(pubsub_busと同一契約)
    finally:
        await bus.close()
    print(
        "[events] purged subscription:"
        f" {settings.pubsub_project_id}/"
        f"{settings.pubsub_subscription_match_events}"
    )
    return 0


async def _run_publish(settings: Settings, args: argparse.Namespace) -> int:
    bus = PubsubEventBus(settings)
    try:
        if args.raw_payload is not None:
            # 毒ペイロード・参照不在Eventの投入経路(10 §4.7)。3点組より優先
            await bus.publish_raw(args.raw_payload.encode())
            print(
                "[events] published raw payload"
                f" ({len(args.raw_payload.encode())} bytes)"
            )
        else:
            await bus.publish_match_event(
                event_type=args.event_type,
                intent_id=args.intent_id,
                version=args.version,
            )
            print(
                f"[events] published {args.event_type}"
                f" intent={args.intent_id} version={args.version}"
            )
    finally:
        await bus.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "purge-match-sub":
        return asyncio.run(_run_purge(Settings()))
    if args.command == "publish":
        return asyncio.run(_run_publish(Settings(), args))
    raise AssertionError(f"unreachable: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_events_publish_cli.py tests/unit/test_events_cli.py -v`
Expected: PASS(新規13件+既存purge系3件)

- [ ] **Step 5: コミット**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
git add backend/src/latch/events/__main__.py backend/tests/unit/test_events_publish_cli.py
git commit -m "feat: add manual match event publisher to events CLI"
```

### Task 7: staging・loadのcompose・nginx設定・.gitignore

**Files:**
- Create: `compose.staging.yaml`
- Create: `compose.load.yaml`
- Create: `docker/nginx/staging.conf`
- Modify: `.gitignore`(1行追記)

**Interfaces:**
- Produces: プロジェクト `latch-staging` / `latch-load`(コンテナ名・ネットワーク・ボリュームがciと自動分離)。サービス名 `db`・`redis`・`pubsub`・`api1`・`api2`・`nginx`・`worker1`・`worker2`(nginx.confのupstreamとTask 8のMakefile・§7手順がこの名前を参照)。ポート=design §2.1表(nginx 18000/28000・api 18001/18002・28001/28002・db 15432/25432・redis 16379/26379・pubsub 18085/28085・すべて127.0.0.1公開)。api/worker共通のenvは単一アンカー `x-backend-env` から `<<:` merge(design §1.4の再発防止)。鍵マウント `./.staging/keys:/keys:ro`(Task 5の `LATCH_AUTH_TESTKEYS_DIR=/keys` と対)
- Consumes: `backend/Dockerfile`(build context=backend・ciと同一イメージ定義)・`docker/postgres/Dockerfile`(db・流用のみ)・Task 5のenv名

- [ ] **Step 1: .gitignoreへ1行追記**

`.gitignore` の末尾へ追記(鍵ペアはgit管理外 — design §2.4):

```text

# staging/load環境の鍵ペア(M4 ws-1・gen-keypairの出力先)
.staging/
```

- [ ] **Step 2: nginx設定を作る**

`docker/nginx/staging.conf` を新規作成:

```nginx
# staging/load共通の入口設定(M4 ws-1 design §2.3案A・supervisor承認A2)。
# 模倣するのは3点: 入口1つ・upstream分散・死んだインスタンスへの振りの再送
# (proxy_next_upstream=passive死活除外)。模倣しないのはTLS終端・activeヘル
# スチェック・オートスケール連動(本番基盤の担い手 — 04・11 Phase 0)。
# staging・loadの両composeから同じファイルをマウントする(upstream名api1/api2
# はプロジェクト内DNSで解決されるため共通化できる)。
upstream latch_api {
    server api1:8000;
    server api2:8000;
}

server {
    listen 80;

    location / {
        proxy_pass http://latch_api;
        # 死んだインスタンスへの振りはもう1台へ再送(10 §4.3「残存インスタンス
        # での処理継続」の器。ws-7がdocker killで検証する)
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_next_upstream_tries 2;
        proxy_connect_timeout 2s;
        proxy_read_timeout 30s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

- [ ] **Step 3: compose.staging.yamlを作る**

`compose.staging.yaml` を新規作成:

```yaml
# staging環境(10 第1節「staging=縮小構成」・M4 ws-1)。ci(compose.yaml)とは
# 別プロジェクト・別ボリューム・別ポートで完全分離(design §2.1案B)。
# api 2台+nginx前段(10 §4.3可用性試験の「入口1つ+残存インスタンス継続」の器)。
# マイグレーション・geo投入は起動時自動実行しない(make staging-migrate /
# staging-geo-import・既存規律どおり)。LLM/FCMはstub明示(実課金・実送信ゼロ)。
name: latch-staging

x-backend-env: &backend-env
  # 全サービスで接続envを網羅(既知欠陥の再発防止 — design §1.4)。
  # api/workerともこの単一アンカーからmergeする
  LATCH_APP_ENV: staging
  LATCH_DATABASE_URL: postgresql+asyncpg://latch:latch@db:5432/latch
  LATCH_REDIS_URL: redis://redis:6379/0
  LATCH_PUBSUB_EMULATOR_HOST: pubsub:8085
  LATCH_PUBSUB_PROJECT_ID: latch-staging
  LATCH_LLM_MODE: stub
  LATCH_PUSH_MODE: stub
  LATCH_CLOCK_MODE: controlled
  LATCH_AUTH_TESTKEYS_DIR: /keys

x-api-common: &api-common
  build:
    context: backend
  command: uvicorn latch.main:app --host 0.0.0.0 --port 8000
  environment:
    <<: *backend-env
  volumes:
    - ./.staging/keys:/keys:ro
  depends_on:
    db:
      condition: service_healthy
    redis:
      condition: service_healthy
    pubsub:
      condition: service_started
  healthcheck:
    test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')\""]
    interval: 10s
    timeout: 5s
    retries: 12
    start_period: 10s
  restart: unless-stopped

x-worker-common: &worker-common
  build:
    context: backend
  command: python -m latch.worker
  environment:
    <<: *backend-env
  depends_on:
    db:
      condition: service_healthy
    redis:
      condition: service_healthy
    pubsub:
      condition: service_started
  restart: unless-stopped

services:
  db:
    build:
      context: docker/postgres
    environment:
      POSTGRES_USER: ${LATCH_DB_USER:-latch}
      POSTGRES_PASSWORD: ${LATCH_DB_PASSWORD:-latch}
      POSTGRES_DB: ${LATCH_DB_NAME:-latch}
    ports:
      - "127.0.0.1:15432:5432"
    volumes:
      - latch-staging-db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 3s
      retries: 12
    restart: unless-stopped

  redis:
    image: redis:8-alpine
    ports:
      - "127.0.0.1:16379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 12
    restart: unless-stopped

  pubsub:
    # ciと同一イメージ(エミュレータ)。project_idはlatch-staging(settings側)
    image: gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators
    command: ["gcloud", "beta", "emulators", "pubsub", "start", "--host-port=0.0.0.0:8085"]
    ports:
      - "127.0.0.1:18085:8085"
    restart: unless-stopped

  api1:
    <<: *api-common
    ports:
      - "127.0.0.1:18001:8000"

  api2:
    <<: *api-common
    ports:
      - "127.0.0.1:18002:8000"

  nginx:
    image: nginx:alpine
    volumes:
      - ./docker/nginx/staging.conf:/etc/nginx/conf.d/default.conf:ro
    ports:
      - "127.0.0.1:18000:80"
    depends_on:
      api1:
        condition: service_healthy
      api2:
        condition: service_healthy
    restart: unless-stopped

  worker1:
    <<: *worker-common

  worker2:
    <<: *worker-common

volumes:
  latch-staging-db:
```

- [ ] **Step 4: compose.load.yamlを作る**

`compose.load.yaml` を新規作成(stagingと**同種構成**・隔離。api/workerのstop/start運用余地は2サービス定義のまま — design §2.3・§2.6):

```yaml
# load環境(10 第1節「load=隔離」・M4 ws-1)。stagingと同種構成で完全隔離
# (別プロジェクト・別ボリューム・別ポート28000系)。ws-6(負荷・総当たり)と
# ws-7(可用性=api 2台+nginxが必要)が使う。負荷試験実行中のci停止運用は
# ws-6/ws-7の手順の前段(design §2.6・supervisor承認A4)。
name: latch-load

x-backend-env: &backend-env
  LATCH_APP_ENV: load
  LATCH_DATABASE_URL: postgresql+asyncpg://latch:latch@db:5432/latch
  LATCH_REDIS_URL: redis://redis:6379/0
  LATCH_PUBSUB_EMULATOR_HOST: pubsub:8085
  LATCH_PUBSUB_PROJECT_ID: latch-load
  LATCH_LLM_MODE: stub
  LATCH_PUSH_MODE: stub
  LATCH_CLOCK_MODE: controlled
  LATCH_AUTH_TESTKEYS_DIR: /keys

x-api-common: &api-common
  build:
    context: backend
  command: uvicorn latch.main:app --host 0.0.0.0 --port 8000
  environment:
    <<: *backend-env
  volumes:
    - ./.staging/keys:/keys:ro
  depends_on:
    db:
      condition: service_healthy
    redis:
      condition: service_healthy
    pubsub:
      condition: service_started
  healthcheck:
    test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')\""]
    interval: 10s
    timeout: 5s
    retries: 12
    start_period: 10s
  restart: unless-stopped

x-worker-common: &worker-common
  build:
    context: backend
  command: python -m latch.worker
  environment:
    <<: *backend-env
  depends_on:
    db:
      condition: service_healthy
    redis:
      condition: service_healthy
    pubsub:
      condition: service_started
  restart: unless-stopped

services:
  db:
    build:
      context: docker/postgres
    environment:
      POSTGRES_USER: ${LATCH_DB_USER:-latch}
      POSTGRES_PASSWORD: ${LATCH_DB_PASSWORD:-latch}
      POSTGRES_DB: ${LATCH_DB_NAME:-latch}
    ports:
      - "127.0.0.1:25432:5432"
    volumes:
      - latch-load-db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 3s
      retries: 12
    restart: unless-stopped

  redis:
    image: redis:8-alpine
    ports:
      - "127.0.0.1:26379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 12
    restart: unless-stopped

  pubsub:
    image: gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators
    command: ["gcloud", "beta", "emulators", "pubsub", "start", "--host-port=0.0.0.0:8085"]
    ports:
      - "127.0.0.1:28085:8085"
    restart: unless-stopped

  api1:
    <<: *api-common
    ports:
      - "127.0.0.1:28001:8000"

  api2:
    <<: *api-common
    ports:
      - "127.0.0.1:28002:8000"

  nginx:
    image: nginx:alpine
    volumes:
      - ./docker/nginx/staging.conf:/etc/nginx/conf.d/default.conf:ro
    ports:
      - "127.0.0.1:28000:80"
    depends_on:
      api1:
        condition: service_healthy
      api2:
        condition: service_healthy
    restart: unless-stopped

  worker1:
    <<: *worker-common

  worker2:
    <<: *worker-common

volumes:
  latch-load-db:
```

- [ ] **Step 5: 定義の妥当性を検証**

Run: `docker compose -f compose.staging.yaml config -q; echo "staging=$?"; docker compose -f compose.load.yaml config -q; echo "load=$?"; docker compose config -q; echo "ci=$?"`
Expected: `staging=0`・`load=0`・`ci=0`(ciは読み取り検証のみ・変更していないことの確認)

Run: `docker compose -f compose.staging.yaml config | rg -c "LATCH_CLOCK_MODE: controlled"`
Expected: `4`(api1・api2・worker1・worker2に配線されている=アンカーmergeの実証)

Run: `docker compose -f compose.load.yaml config | rg "127.0.0.1:(28000|28001|28002|25432|26379|28085)"`
Expected: 6ポートすべてが表示される

- [ ] **Step 6: コミット**

```bash
make lint && make test
git add .gitignore docker/nginx/staging.conf compose.staging.yaml compose.load.yaml
git commit -m "chore: add staging and load compose projects with nginx front end"
```

### Task 8: Makefileへstaging・loadターゲットを追記

**Files:**
- Modify: `Makefile`(`.PHONY` 行の名前追記+末尾へブロック追記。**既存行の削除・変更はしない**)

**Interfaces:**
- Consumes: `compose.staging.yaml` / `compose.load.yaml`(Task 7のサービス名・ポート)・`python -m latch.auth`(既存CLI)・`python -m latch.core.clock_cli`(Task 3)・`python -m latch.events`(Task 6)・`uv run alembic`(既存migrateと同一)
- Produces: `make staging-up / staging-down / staging-ps / staging-logs / staging-migrate / staging-geo-import / staging-keys / staging-token [SUBJECT=…] / staging-clock [ARGS=…] / staging-publish [ARGS=…] / load-up / load-down / load-ps`(§7手順とws-3以降の消費者が使う)

- [ ] **Step 1: .PHONY行を置き換える**

先頭の `.PHONY:` 行を次の内容に置き換える(**既存13名の並びと順序を保持し、末尾へ13名追記**。これが完了条件4で許容される唯一の既存行変更):

```makefile
.PHONY: setup up down ps logs lint test test-ci migrate geo-download geo-import geo-verify g1-gate embed-smoke jev-smoke g2-gate staging-up staging-down staging-ps staging-logs staging-migrate staging-geo-import staging-keys staging-token staging-clock staging-publish load-up load-down load-ps
```

- [ ] **Step 2: 末尾へターゲットブロックを追記**

`Makefile` の末尾(`g2-gate` ターゲットの後)へ追記。**レシピ行の先頭はハードタブ1文字**(スペースに化すと `make` が失敗する。追記後に `make -n staging-up` で解釈確認):

```makefile

# --- staging/load環境(M4 ws-1・別プロジェクト。ciのcompose.yamlと既存ターゲットは触らない)---

STAGING_COMPOSE = docker compose -f compose.staging.yaml
LOAD_COMPOSE = docker compose -f compose.load.yaml
STAGING_DB_URL = postgresql+asyncpg://latch:latch@127.0.0.1:15432/latch

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
```

注: `make staging-geo-import` は `backend/data/geo/`(git管理外)のCSV/PBFを要する。worktreeに無い場合はmain checkoutから実パスでコピーしてよい(`cp /home/misty/Projects/latch/backend/data/geo/isj.csv backend/data/geo/` 等)。データ無しで実行しない(§7手順では必須でない)。

- [ ] **Step 3: 解釈を確認する(実行はしない)**

Run: `make -n staging-up && make -n staging-clock ARGS="show" && make -n load-up`
Expected: それぞれ `docker compose -f compose.staging.yaml up -d --wait`・`cd backend && LATCH_REDIS_URL=… uv run python -m latch.core.clock_cli show`・`docker compose -f compose.load.yaml up -d --wait` が表示される(タブが解釈されている証拠。`make -n` はドライランで実行されない)

- [ ] **Step 4: コミット**

```bash
make lint && make test
git add Makefile
git commit -m "chore: add staging and load make targets"
```

### Task 9: 受け入れ確認・報告書・完了条件の最終検証

**Files:**
- Create: `docs/plans/M4/ws-1-report.md`(§7のテンプレートどおり)

**Interfaces:**
- Consumes: §7の検証手順1〜9(コマンド列と期待値はそこに定義)・Tasks 1〜8の成果物全部

- [ ] **Step 1: 自動試験の最終確認**

Run: `make lint && make test`
Expected: ともにexit 0。unit件数=**1262件**(既存1219+本計画の新規43件。計画時点の設計値 — 実数をそのまま報告書へ記録する。ズレが出た場合は実数と内訳を報告書の補足へ書く)

Run: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d`
Expected: 出力なし(空)

Run: `rg -n 'datetime\.now|utcnow|date\.today|time\.time|time\.monotonic|time\.sleep|from time import' backend/src`
Expected: ヒットが `backend/src/latch/core/clock.py` の行のみ

Run: `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v; cd ..`
Expected: PASS

- [ ] **Step 2: ci無変更の検証**

Run: `git diff main -- compose.yaml docker/postgres backend/alembic backend/pyproject.toml backend/uv.lock .env.example`
Expected: 出力なし(空)

Run: `git diff main -- Makefile | rg "^-[^-]" `
Expected: `.PHONY:` 行の削除1行のみが表示される(レシピ・ターゲット定義の削除がゼロであることの確認)

Run: `docker compose -f compose.staging.yaml config -q; echo $?; docker compose -f compose.load.yaml config -q; echo $?`
Expected: `0` と `0`

- [ ] **Step 3: 受け入れ確認を実施する**

§7の**手順1〜9を順に実行**する。規律の再掲: staging/load宛コマンドに必ず `-f` を付ける・ci宛は読み取りのみ・`make test-ci` は実行しない(報告書へ「スーパーバイザー検証待ち」)。期待値の判定は§7の各手順に従い、**実際の出力の要点をその場で§7の報告テンプレートの表へ書き込む**(後回しにすると証拠が失われる)。

- [ ] **Step 4: 報告書を作成してコミット**

`docs/plans/M4/ws-1-report.md` を§7のテンプレートどおり作成する(完了条件8項目の表・test-ci待ちの記録・受け入れ9行の証拠表・固定値の変更有無・コミット一覧)。未実施項目・期待と異なった項目は誤魔化さずFAILと実測値を書き、補足に状況を残す。

```bash
git add docs/plans/M4/ws-1-report.md
git commit -m "docs: add ws-1 execution report with acceptance evidence"
```

- [ ] **Step 5: 変更ファイル一覧の最終突合**

Run: `git diff --name-only main | sort` と `git status --short`
Expected: diff一覧が§4(作成9+変更10=**19ファイル**)と完全一致。`git status --short` が空(`.staging/`・`backend/data/` が現れない=.gitignoreが効いている)

ここまでで本計画の実装は完了。以降はスーパーバイザーへの報告(最終返信は報告書の要点+「test-ci スーパーバイザー検証待ち」の明示)。
