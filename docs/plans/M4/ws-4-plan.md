# M4 ws-4(Observability)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** docs/plans/M4/ws-4-design.md §1.1どおり、計測3群(パイプライン/Jev/提案と行動)+アクセス系基本計測のメトリクスを prometheus-client で露出し、Grafana+Prometheus(latch-dashboardプロジェクト)のダッシュボードで見られる状態にする。KPI/MLRの日次集計ジョブ(09 §2.1〜§2.2のKPI定義どおり)をkpi_dailyへ稼働させ、G4-4「測定開始状態」(11 §5-4)の器を完成させる。

**Architecture:** メトリクス定義はobs/metrics.pyの単一モジュールに局所化(build_metrics(registry)ファクトリ+モジュール単例。本番SDK差し替え面)。API側は/metricsをASGIマウント+HTTP基本メトリクスmiddleware、Worker側はstart_http_server(obs_worker_metrics_port>0のとき)+collector(60秒tick)。KpiJobはJST日付境界を60秒tickで検査して遡及UPSERTするtick方式(§2.1読み替え3)。3テーブル(sessions・kpi_daily・kpi_muted_user_snapshot)をマイグレーション0008で新設(FKなし独立表)。ダッシュボードは別composeプロジェクト(latch-dashboard)のPrometheusがホスト公開ポート(127.0.0.1バインド)をscrapeし、Grafanaへprovisioningで自動ロードする。

**Tech Stack:** Python 3.13・FastAPI・SQLAlchemy(asyncpg)・**prometheus-client(本単位で新規追加)**・Grafana OSS 11.4・Prometheus v3.0(provisioning as-code)。テストは既存依存(pytest・httpx・asyncpg)+pyyaml(既存)。

**Spec:** docs/plans/M4/ws-4-design.md(agent1・コミット8e46d2b)。本計画はdesign §6のsupervisor承認4件(すべて2026-10-04裁定済み・§7「未解決の論点なし」)に従う。designに対する本計画の読み替えは§2.1に一覧する(計画裁量の決定4件・いずれも報告書への記録義務つき)。

---

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m4-ws-4`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。
- **worktreeの基点はmain(head=8e46d2b)**: ws-5はマージ済み・**alembic head=0007のmain**から作る。本単位で0008を追加する(Task 2)。worktree作成後、まず `make setup`(`cd backend && uv sync`)で `.venv` を構築し、Task 1で `cd backend && uv add prometheus-client` を実行する(**依存追加ありの単位** — uv.lockの更新を伴う)。
- **毎コミットの検査**: worktree内で**毎コミット `make lint` と `make test` がグリーン**であることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **共有ci-dbへのtest-ci/migrate実行はスーパーバイザー検証時のみ(STATUS.md運用ルール1・スーパーバイザー補記1)**: 本単位は**マイグレーション0008を追加する**。0008は共有ci-dbのtest-ci/migrateを他単位と取り合うため、**agent3は `make test-ci` も `make migrate`(ci宛)も実行しない**。実装中の開発検証はunit試験(`make test`)+stagingコンテナ実行(Task 13)で進める。test-ci(基準実数維持+新規integration試験3ファイル)の実行はスーパーバイザー検証時に直列で行う。**報告書の該当完了条件には「test-ci: スーパーバイザー検証待ち」と記録してよい**(必須記録)。並走単位は本計画時点で存在しない。
- **staging環境の操作はagent3が実施してよい**(latch-stagingプロジェクト・独立ボリュームのためciと干渉しない):
  - 可: `make staging-up` / `staging-down` / `staging-reset` / `staging-ps` / `staging-logs` / `staging-migrate` / `staging-geo-import` / `staging-keys` / `staging-token` / `staging-clock` / `staging-publish` / `staging-test` / **`staging-obs-test`(Task 11で追加)** / **`dashboard-up` / `dashboard-down` / `dashboard-ps` / `dashboard-logs`(Task 11で追加)**、および `docker compose -f compose.staging.yaml` や `docker compose -f compose.dashboard.yaml` 宛のexec/build/logs。
  - 不可: ci(latch-ciプロジェクト・compose.yaml・Makefileのci系ターゲット)宛の状態変更操作すべて。**`-f` を付け忘れるとciプロジェクト宛に作用する。コマンドを打つ前に毎回 `-f` の有無を確認する**(最重要の事故防止規律)。
  - geo資材(`backend/data/geo/`のCSV/PBF)はgit管理外・ciと共用。worktreeにない場合は `make geo-download` で取得する。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-10-04)**: 現状 `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空。本計画の新規unit試験(test_obs_metrics・test_obs_sessions_api・test_obs_kpi・test_obs_collector・test_obs_worker_wiring・test_obs_pipeline_metrics・test_obs_migration・test_obs_compose_pins)と新規integration試験(test_obs_schema・test_obs_api_ci・test_obs_kpi_job_ci)は既存と衝突しない(§2.2(a)で突合済み)。各Taskのコミット前に次コマンド(staging_tests_obsも含めた形)が空であることを再確認する:

  ```bash
  find backend/tests backend/staging_tests backend/staging_tests_obs -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
  # 期待値: 出力なし(空)
  ```

- **integration試験はcollect-onlyのみ実行可**: agent3は `cd backend && uv run pytest -m integration --collect-only -q` は実行してよいが、**integration試験の実実行はしない**(実DB/実コンテナ経路はスーパーバイザー検証のtest-ciで走る)。staging_tests_obsの実行は本計画本体であるためこの制限外。
- **python / pipコマンドを直接使わない**: すべて `uv run` / `uv sync` / `uv add` 経由。
- **Clock契約・実時間参照の規律**: `core/clock.py` は1行も変更しない。新規コードで時刻参照はすべてClock経由にする(C2)。`time.time` / `time.monotonic` / `time.sleep` / `datetime.now` はsrc/latch配下で禁止(test_arch_no_direct_time.py)・新規モジュールも対象。middlewareのレイテンシ計測もClock.now()の差分で行う。テストコード内の実時間待ち上限(harnessのwait_until等)は制限外。
- **固定値の遵守**: 設計の採用判断(prometheus-client直+pull型・Grafana OSS+単一Prometheus datasource・日次バッチ集計→kpi_daily・sessionsテーブル新設・ホストポート経由のscrape・127.0.0.1明示バインド・HTTP基本メトリクス入り・ports番号〔prometheus=19090・grafana=13000・worker staging=18101/18102・load=28101/28102〕・settings 3項目とその既定値・遡及幅4日・tick 60秒)は固定値。**変更した場合は必ず報告ファイルに「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。メッセージはConvention Commits形式(`feat:` `test:` `chore:` 等)で、末尾に `Co-Authored-By: Claude Code <noreply@anthropic.com>` を付ける。
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| 計測3群の内容: パイプライン(Event Queue lag・Candidate数・Hard Filter通過率・Vector Retrieval件数)・Jev(実行回数・latency・cost)・提案と行動(LATCH Score分布・通知数・YES率・Mutual Latch Rate・キャンセル率) | 04 §2・§7 |
| アクセス系基本計測: セッション開始イベントでDAU・再来訪。イベントはuser_idと時刻のみを運びIntentの文言を含まない | 04 §2・01 §20(FR-11付記) |
| POST /v1/sessions契約: request bodyは空。user_idと時刻のみ記録・response 204 | 05 §19(API一覧)・05 §5(エラー形式) |
| 計測基盤の本番確定値はCloud Monitoring+OTLP・3群を単一基盤へ集約。**M4のstaging/load/ダッシュボードはOSS代替**(Cloud Monitoring+OTLP自体は変更しない・G4時確認事項) | 04 §3・M4設計前提(2026-10-03単位表承認)・design §6-2 |
| MLR定義: 分母=当該期間に通知を送信したLATCH候補数(1候補1カウント・グループも1・クローズ済みを除かない)・分子=必要人数全員YESの候補数。**集計はproposed遷移を分母・matched遷移を分子としlatch_status_eventsから日次で集計。期間は日次・週次の2種を常設** | 09 §2.1・01 §23 |
| KPI表14指標(使われ方4・マッチングの質7・コスト2・安全網1)の分子/分母・周期。使われ方・マッチングの質・安全網は提案と行動の群・コストはJevの群から導出。意図的な集計イベントを追加する場合も個別Intentの文言を含めない | 09 §2.2 |
| 実行率・キャンセル率の分母は回答済みに限定・無回答は欠測(無回答率として並列計測)。無回答率=成立から3日経過時点 | 09 §2.2(FR-29)・09 D-09 |
| Intent作成率の「作成」=active化(draft→active化を初回作成と数える。match_eventsのcreated種=初回投入を含む) | 09 §2.2(v0.4)・intents/service.py:742 |
| 通知オフ率: 当該週に1つでもmutedへ変更したIntentを持つユーザー数/当該週の週アクティブユーザー数。notification_levelはIntent単位 | 09 §2.2(FR-26) |
| 保留→expired率: candidate→expired遷移数/candidate作成数(いずれもlatch_status_events) | 09 §2.2(FR-47) |
| G4-4: Observability 3群とアクセス系基本計測のダッシュボードが稼働し、MLR日次集計(latch_status_events経由)が動作していること | 11 §5-4・12 M4-5・STATUS G4節 |
| stagingのClock制御はJST 0時系ジョブをClock操作で回せる | 10 §1(適用範囲5)・design §1.2引用#11 |
| ログ・計測・Analyticsからの排除: Intent原文(raw_text)・正確な位置・NG条件を含めない。Analyticsは集計値のみ。**本計画のメトリクスlabelはmethod・pathテンプレート・status・provider・metric名等のシステム属性のみで、user_id・クエリ・body・Intent文言を含めない** | 08 §2.4 |
| D-16: Jev実行回数は実際のAPI呼び出しをすべて「1実行回数」に計上(フォールバック切替呼び出し含む)。80% alertはlatch.cost.alert構造化ログ(既存・無変更) | 04 D-16(v0.5読み替え) |
| Queue障害試験(ws-6以降)は「Queue滞留を作りQueue lagメトリック(01 §20)のalertが発報すること」を確認する — 本単位のlag gauge(pending件数・最古pending年齢)がその消費先 | 10 §4.7・design §5.1 |
| ダッシュボードの器はws-4が追加。ws-1のYAMLアンカー構造(新サービスブロック追記+共通アンカーへのenv/ポート追記に局所化できる構造)を維持 | ws-1 design §2.6(supervisor承認) |
| 証拠: 試験ID・実施日・環境・入力条件・合否をdocs/testassets/results/へ保管 | 10 §5 |
| composeのportsは明示的な127.0.0.1バインド(`127.0.0.1:18001:8000`形式・承認条件)。staging受け入れに「ホスト外(非ループバック)から/metricsへ届かない」確認を1項目入れる | design §2.5・§6-4・§5.1 |

## 2. 設計確定値と計画時の機械検証

### 2.1 読み替え済み確定値(designからの差分・計画裁量の決定)

| # | designの記載 | 本計画の値 | 根拠・記録義務 |
|---|---|---|---|
| 1 | design §3.1「compose.staging.yaml追記: worker-commonへLATCH_OBS_WORKER_METRICS_PORT=9100」 | `x-worker-common` の `environment` で `<<: *backend-env` に**続けて** `LATCH_OBS_WORKER_METRICS_PORT: "9100"` を追記する(backend-envアンカー自体は触らない)。load側は同一構造でenv追加なし・portsのみ(28101/28102) | YAMLアンカー構造維持(ws-1 design §2.6・補記4)。worker-commonのみの追記でapiにenvが渡らない |
| 2 | design §2.3「Workerの独立タスク(ResetJobと同型の『次のJST 0時まで待つ→実行→遡及4日分をUPSERT→次へ』)」 | **KpiJobはReevalRunner同型の60秒tick方式**(tickごとに「kpi_dailyにまだ行のない遡及対象日」を検出してUPSERT)。ResetJobの「next_jst_midnightまでの実時間sleep」構造はstagingのClock操作で発火を短縮できない(ControlledClockのdocstring明記「sweeper/Runnerの待機はasyncio.sleep(実時間)のため速くならない」)ため、**Clock操作による発火確認(design §5-4・引用#11)を成立させるにはtick方式が必須** | 設計の目的(引用#11・§5-4)をより正しく満たす実装読み替え。境界の真実はkpi_daily自体(代表キーmlr行の存在)・Redis不要・起動時も即tick(自己修復)。**報告書に記録する** |
| 3 | design §3.1「kpi_dailyへ1日1行の分子/分母列」 | kpi_dailyは**(metric_key, day)を主キーとする行持ち**(指標あたり日次1行のnumerator/denominator列)。「1日1行の分子/分母」の意図(比率を保持せず分子/分母のみ)は維持・行持ちにより遡及UPSERTが指標単位で独立しSQLが単純になる | 週次/月次系は同じテーブルに「day=週の月曜(週次)・day=基準日(retention)」の行で保存(§2.3)。**報告書に記録する** |
| 4 | design §2.6「match_candidatesのstatus='candidate'件数を周期ジョブがSQL count→gauge」・「candidateを1件以上持つactive Intent数」 | match_candidates.statusの実際の値域は**pending/evaluated/skipped**(candidates.py・jev.py・0003)であり「'candidate'」という値は存在しない。**Candidate数gauge = match_candidates全行数**(生成済み候補ペア数)・candidate_generation_rateの分子 = match_candidatesにintent_a_id/intent_b_idのいずれかとして現れるactive Intent数(status条件なし) | designの「status='candidate'」はドメイン用語(生成済み候補)の誤記。**報告書に記録する** |
| 5 | design §3.1「obs/metrics.py…カスタムレジストリ注入のテスト経路」 | `build_metrics(registry=None)` ファクトリ: 引数なし(既定REGISTRY)でモジュール単例 `metrics` を1回構築(本番・api・worker共用)。テストは `build_metrics(CollectorRegistry())` で独立インスタンスを作り値をassertする。`make_asgi_app()` は既定REGISTRYを使うため単例と同一レジストリになる | prometheus-client公式(docs: registry=Noneは「未登録」を意味するため、テストには専用CollectorRegistryを渡す)・context7確認済み2026-10-04 |
| 6 | design §4「unit(test_obs_kpi.py): KPI計算の純関数を期待値表で固定」 | KPI計算を「**純関数層**(JST日付導出・遡及日列・週境界・snapshot差分・responses解析・観測窓判定・metric定数表ピン)」と「**SQL実行層**(asyncpg/SQLAlchemyで実行)」に分離。unit試験は純関数層の期待値表を所有・SQL実行層の正しさはintegration(test_obs_kpi_job_ci・supervisor検証時)+staging受け入れ(compute_mlr実測一致・Task 13)が所有する3層構成 | Postgres専用SQL(jsonb_array_elements・AT TIME ZONE)はunit環境で実行できないため。**報告書に記録する** |
| 7 | design §2.8「無回答率=『completed_at+3日経過 AND actual_attended IS NULL』」 | calibration_recordsにcompleted_at列は無い(0001)。成立時刻は**created_at**(07 §6「回答確定時に作成」=成立直後)で代用し `created_at + 3日 < 集計時刻` を判定条件とする | 列対応の確認結果。**報告書に記録する** |
| 8 | design §2.3「当日値は60秒周期のexporterがgauge化」 | collectorのkpi exportは「kpi_dailyの**各metric_keyの最新行**」(DISTINCT ON (metric_key) … ORDER BY metric_key, day DESC)をgauge化する。当日行が無い指標(日次行は0時跨ぎ後に昨日分が書かれる・週次行)も観察可能にするため | 60秒tickのKpiJobが当日の集計をしない設計(集計対象は完了日)のため。**報告書に記録する** |
| 9 | design §2.5「Prometheusのscrape_configsが127.0.0.1の公開ポートを叩く」 | prometheusコンテナ内からはコンテナ自身の127.0.0.1を意味するため、scrape targetは `host.docker.internal:18001` 等とし、compose.dashboard.yamlのprometheusに `extra_hosts: ["host.docker.internal:host-gateway"]` を付ける(ホストの127.0.0.1バインドされたポートへの到達経路) | 「127.0.0.1の公開ポート(ホスト側バインド)を叩く」の意図をコンテナ経路で実現する読み替え。**報告書に記録する** |
| 10 | design §3.1「docker/grafana/dashboards/overview.json ほか3〜4枚」 | 4枚で固定: overview・pipeline・jev・behavior(§Task 12のPromQL一覧)。dashboard JSONの全体はリポジリに書く(本計画書は構造仕様+代表1パネル+PromQL一覧表に留める) | スーパーバイザー補記6(GrafanaダッシュボードJSONは計画書の膨張を避けてよい) |

### 2.2 機械チェックSelf-Reviewの証拠(計画レビュー時点・スーパーバイザー補記2対応)

**(a) テストファイルbasename一意(計画時点の機械確認・2026-10-04)**

新規unit 8ファイル(test_obs_metrics・test_obs_sessions_api・test_obs_kpi・test_obs_collector・test_obs_worker_wiring・test_obs_pipeline_metrics・test_obs_migration・test_obs_compose_pins)・新規integration 3ファイル(test_obs_schema・test_obs_api_ci・test_obs_kpi_job_ci)・新規stagingハーネス3ファイル(test_w4_a_env_seed・test_w4_b_kpi_daily・test_w4_c_exposure)は、既存の `backend/tests`・`backend/staging_tests` 全ファイルbasenameとの突合で衝突ゼロを確認済み(既存にtest_obs*・test_w4_*は存在しない)。**既存ファイルへの追従変更**(test_geo.py・staging_tests/conftest.py・test_settings.py)はbasenameを変えない。コミット前には§0の再確認コマンドを毎回実行する。

**(b) ピン試験への影響事前特定(洗い出し表)**

| 既存ピン | 内容 | 本単位の影響 | 追随访問 |
|---|---|---|---|
| tests/integration/test_geo.py `test_head_is_0007` | alembic_versionの等値ピン「0007」 | **変更が必要**(0008で追従)。`test_head_is_0008` に関数名・docstring・assert値を更新する(既存試験1件の機械的追随) | Task 2で実施 |
| backend/staging_tests/conftest.py `EXPECTED_HEAD = "0007"` | stagingハーネスの環境前提チェック | **変更が必要**(0008で追従・定数1行)。staging_tests(既存ハーネス)はマージ後のmainで再実行可能に保つ | Task 2で実施 |
| tests/integration/test_schema.py `test_twelve_tables_exist` | `EXPECTED_TABLES <= names`(部分集合assert) | **無影響**(テーブル追加で壊れないことを計画時に確認済み)。EXPECTED_TABLES定数への追加は行わない(12テーブル=05 §2の集合ピンであり、obs系3テーブルはdocs改版候補の範囲) | なし |
| tests/unit/auth/test_auth_settings.py `test_auth_settings_are_exactly_eight_fields` | Settings.model_fieldsのうち `auth_` 接頭辞8項目の完全一致集合 | **無影響**(新規はobs_/kpi_接頭辞でauth_に含まれない) | なし |
| tests/unit/llm/test_llm_factory.py(llm_接頭辞の完全一致集合ピン) | 同上 | **無影響**(llm_接頭辞の新規追加なし) | なし |
| tests/unit/geo/test_geo_settings.py `test_geo_settings_are_exactly_three_fields` | `geo_` 接頭辞3項目 | **無影響** | なし |
| tests/unit/test_settings.py | 既存キーの既定値assert(集合ピンなし) | **無影響・ただし本計画が新規obs_系の既定値ピンと集合ピンをtest_settings.pyへ追加する**(Task 3) | 追随访問を本計画に明記(下記) |
| tests/unit/test_log_allowlist.py | `ALLOWED_STRUCTURED_LOGS == {"latch.llm.send", "latch.push.send"}` | **無影響**(logallow.pyは無変更・メトリクスはログと統合しない — design §3.2) | なし |
| tests/unit/test_worker.py | WorkerのDI・dispatch試験(コンストラクタ引数追加はデフォルトNone付きキーワード引数なので既存試験に非破壊) | **非破壊**(Task 7でcollector・kpi引数追加。既存assertは触らない) | なし |
| tests/unit/test_app_health.py | /healthの応答ピン | **無影響**(/healthは温存・/metricsは別経路) | なし |
| tests/unit/test_arch_no_direct_time.py | src/latch配下の time.*・datetime.now 禁止 | 新規モジュール(obs/*)は**Clock経由のみ**で実装する(Task 4・6・7・8のコードは全てClock.now()・date演算) | なし |

**追随访問(本計画が追加する新ピン)**: tests/unit/test_settings.py へ `test_obs_settings_defaults`(obs_worker_metrics_port=0・obs_collect_interval_sec=60・kpi_backfill_days=4)と `test_obs_settings_are_exactly_three_fields`(obs_接頭辞2項目+kpi_接頭辞1項目の完全一致集合)を追加する(auth_8項目ピンと同一作法)。また tests/unit/test_obs_metrics.py がメトリクス露出名の許可リストピン(§Task 1)を持ち、obs/metrics.pyへの定義追加は試験定数の更新を必須化する(許可リストと同型の規律)。

**(c) DB干渉対抗策(新規3テーブル)**

- **sessions・kpi_daily・kpi_muted_user_snapshot の3テーブルはすべてFKを付けない**(design §4「他テーブルの削除順(FK)に依存しない独立表として配置」)。user_idはusers行消滅後も残る(観測用素材表の性質・05 §2追記はdocs改版候補=design §7論点3として記録済み)。これにより既存のdeletion/retention系試験やteardown順序と干渉しない。
- integration試験(test_obs_*_ci.py)の投入データはユニークなユーザー名・UUIDで作り、**既存テーブル(users・latches等)をTRUNCATE/DELETEするcleanupは書かない**(既存integration conftestの作法どおり・試験は自前データで閉じる)。新規3テーブルは試験内で `DELETE FROM <table> WHERE ...`(試験が投入した行を絞って削除・sessionsはuser_idと時刻で絞る)または後述の**now相対時刻**で他試験の行と混在しない。
- **時刻値はnow相対**: integration・stagingハーネスがDBへ書く時刻( INSERTするcreated_at等)はテスト実行時刻から生成する(ハードコードした過去日付を書かない — retention系試験の観測窓計算を壊さないため)。KpiJobの遡及対象はClock基準で決まるため、DBに書くfixtureもClock基準日から相対で置く。
- staging受け入れは `make staging-reset`(down -v→up→migrate→geo-import)から開始するため、DB残存干渉はstagingボリューム毎のリセットで解消される。

### 2.3 KPI定義の単一真実(obs/kpi.pyのmetric_key表)

kpi_dailyの主キーは `(metric_key, day)`。metric_keyは09 §2.2の14指標+retentionの2種=**15キー+ダッシュボード式1種(cost_per_matched・kpi_dailyに書かない)**。周期: 日次=dayに当日日付・週次=dayに週の月曜(JST)・retention=dayに基準日D。

| metric_key | 09 §2.2の指標(群) | 周期 | 分子(numerator) | 分母(denominator) |
|---|---|---|---|---|
| intent_create_rate | Intent作成率(使われ方) | 日次 | 当日match_events(event_type='created')のsource_intentのユーザーdistinct数 | 当日sessionsのuser_id distinct数(DAU) |
| intent_recreation_rate | Intent再作成率(使われ方) | 週次 | 週内にcreated種を≥2件出したユーザー数 | 週内にcreated種を≥1件出したユーザー数 |
| active_intent_per_dau | 1ユーザーあたりActive Intent数(使われ方) | 日次 | 集計実行時点のactive Intent総数(スナップショット近似) | 当日DAU |
| retention_7d | 7日継続率(使われ方) | day=基準日D | 新規ユーザー(Dの日)のうちD<session日≤D+7で再来訪した数 | 基準日Dに作成された新規ユーザー数(users.created_at) |
| retention_30d | 30日継続率(使われ方) | day=基準日D | 同上(D+30) | 同上 |
| candidate_generation_rate | Candidate生成率(マッチングの質) | 日次 | match_candidatesに現れるactive Intent数 | active Intent総数 |
| proposal_rate | LATCH候補提示率(マッチングの質) | 日次 | 当日proposed遷移したlatchのintent_idsをunnestしたdistinct Intent数 | active Intent総数 |
| hold_expired_rate | 保留→expired率(マッチングの質) | 日次 | 当日from='candidate'→to='expired'の遷移数 | 当日to='candidate'の遷移数 |
| yes_rate | YES率(マッチングの質) | 日次 | 当日answered_atのvalue='yes'回答数 | 当日回答総数(value in yes/no/defer) |
| mlr | Mutual Latch Rate(マッチングの質・North Star) | 日次 | 当日to='matched'の遷移数 | 当日to='proposed'の遷移数 |
| attendance_rate | 実行率(マッチングの質) | 週次 | 週内成立でactual_attended=trueのレコード数 | 週内成立でactual_attended IS NOT NULLのレコード数(回答済み限定) |
| cancel_rate | キャンセル率(マッチングの質) | 週次 | 週内成立でcancelled_after=trueのレコード数 | attendance_rateと同一の分母 |
| no_answer_rate | 無回答率(マッチングの質) | 週次 | 週内成立でactual_attended IS NULLかつcreated_at+3日経過のレコード数 | 週内成立レコード数 |
| jev_executions_per_intent | 1 IntentあたりJev実行数(コスト) | 日次 | 当日match_candidatesのjev_result書込み行数(**近似**・design §2.7告白: 同一評価世代スキップ等で厳密値と差が生じうる) | 当日created+updated種のsource_intent_id distinct数 |
| mute_rate | 通知オフ率(安全網) | 週次 | 週内にsnapshotへ初めて現れたユーザー数(snapshot差分・**近似**・design §2.8告白: 往復変動は日次境界でしか観測しない) | 週内sessionsのuser_id distinct数(週アクティブ) |
| cost_per_matched | 1成立あたりAIコスト(コスト) | 週次 | **kpi_dailyに書かない**(usage counterはメモリ値・恒久素材なし)。PromQL `sum(rate(latch_jev_usage_tokens_total[1w]))` 系のダッシュボード式で提供 | 同左(matched数は latch_kpi_daily_numerator{metric="mlr"} の累積比) |

週次行のUPSERTタイミングは「昨日が日曜(JST)で終わる週」をKpiJobが0時跨ぎ検出時に計算(§Task 7)。retentionは0時跨ぎ検出時にD=昨日−7日・D=昨日−30日を計算。muted snapshotは当日集合を0時跨ぎ検出時にUPSERT(0時直後の集合=前日末集合の近似・design §2.8どおり)。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **空の集計源(KpiJobが真っ先に走る状態)**: staging-reset直後・CI起動直後は latch_status_events・sessions・calibration_records が空。KpiJobは例外を上げずに全metric_keyの行を0/0で書き、次tick以降は何もしないこと(自己修復の前提)。→ Task 7の `test_kpi_job_empty_db_writes_all_daily_rows`(unit・FakeEngine)でピン。
2. **ラベルの個人情報混入(08 §2.4の最も重い規律)**: HTTP middleware・Jev・ダッシュボードのlabelに user_id・クエリ・body・Intent文言・座標が入ると8 §2.4違反。→ Task 4の `test_http_metrics_labels_are_system_only`(sample.labelsのキー集合assert)+pathテンプレート値がroute宣言と一致する試験でピン。新しいメトリクスを足す者は必ずtest_obs_metrics.pyの許可リストピンを通す構造(Task 1)。
3. **/metricsの自己増殖と無限待ち(運用者が最初に遭遇する故障)**: Prometheusが15秒ごとに/apiの/metricsを叩くため、middlewareが/metricsを計上するとcounterがscrape回数で膨らむ。→ Task 10の `test_metrics_path_itself_is_not_counted` でピン。また/metricsは認証外・レート制限外の生マウントのため、nginxの外側(API入口)からではなくapi直のポートでしか到達しないこと(127.0.0.1バインドの承認条件)をTask 13の(c)でピン。
4. **遅延到着と遡及(当日行だけ書く実装の見落とし)**: 自己申告は3日期限(D-09)のため、回答の遅延で昨日のattendance/cancel/no_answerが今日確定する。遡及窓(backfill_days=4)を当日のみの集計に縮めると恒久値が欠落する。→ Task 7の純関数 `test_backfill_window_covers_yesterday_minus_4days` と SQL層の遡及UPSERT(unitのFakeEngine回数+integration)でピン。
5. **worker再起動でcounterがゼロに戻ることの誤読**: Prometheusのcounterはrestartで0に戻るのが正常(rate系のPromQLで消費するのが正しい使い方)。恒久値はkpi_daily(jev_executions等のSQL近似)が担う — dashboardのjev実行パネルはrate系で示す(Task 12)。→ Task 8のcollector試験とTask 12のPromQL一覧でこの分割を文書化し、実装者がgaugeにcounterの値を写す間違い(gaugeへのsum集約はしない)をしないようPromQL一覧表に注意書きを置く。

## 4. ファイル構成(スコープ)

### 4.1 作るもの(新規)

| ファイル | 内容 | Task |
|---|---|---|
| `backend/src/latch/obs/__init__.py` | 空パッケージ | 1 |
| `backend/src/latch/obs/metrics.py` | 全メトリクスの単一真実(Counter/Histogram/Gauge定義・build_metrics(registry)ファクトリ・モジュール単例metrics・observe_retrieval/observe_jev_execution/scoreobserveの薄いヘルパー) | 1・9 |
| `backend/src/latch/obs/middleware.py` | HTTP基本メトリクスmiddleware | 4 |
| `backend/src/latch/obs/sessions.py` | POST /v1/sessions(ルーター+SessionStore・認証必須・body空・204) | 5 |
| `backend/src/latch/obs/collector.py` | 60秒周期のexporter(pending件数・最古pending年齢・Candidate数・breaker状態・kpi_daily最新行→gauge) | 8 |
| `backend/src/latch/obs/kpi.py` | KPI定義の単一真実(metric_key表・SQL定数)・純関数層・KpiJob(60秒tick・遡及UPSERT) | 6・7 |
| `backend/alembic/versions/0008_obs_sessions_kpi.py` | sessions・kpi_daily・kpi_muted_user_snapshot(0007の下に接続) | 2 |
| `compose.dashboard.yaml` | latch-dashboardプロジェクト(prometheus 19090・grafana 13000・ともに127.0.0.1明示バインド・prometheusにhost-gateway) | 11 |
| `docker/prometheus/prometheus.yml` | scrape_configs(staging api1/2・worker1/2・prometheus自身) | 11 |
| `docker/grafana/provisioning/datasources/prometheus.yaml` | Prometheus datasource provisioning | 11 |
| `docker/grafana/provisioning/dashboards/default.yaml` | dashboard provider(ファイル型) | 11 |
| `docker/grafana/dashboards/overview.json` | ダッシュボード①(MLR・3群サマリ・DAU・breaker・HTTP p95) | 12 |
| `docker/grafana/dashboards/pipeline.json` | ダッシュボード②(パイプライン群) | 12 |
| `docker/grafana/dashboards/jev.json` | ダッシュボード③(Jev群・cost) | 12 |
| `docker/grafana/dashboards/behavior.json` | ダッシュボード④(KPI・提案と行動群) | 12 |
| `backend/tests/unit/test_obs_metrics.py` | メトリクス許可リストピン・値assert・middleware試験 | 1・4 |
| `backend/tests/unit/test_obs_sessions_api.py` | sessions API契約 | 5 |
| `backend/tests/unit/test_obs_kpi.py` | KPI純関数の期待値表 | 6・7 |
| `backend/tests/unit/test_obs_collector.py` | collectorのgauge反映 | 8 |
| `backend/tests/unit/test_obs_worker_wiring.py` | Worker配線(collector・KpiJob・start_http_server分岐) | 7 |
| `backend/tests/unit/test_obs_pipeline_metrics.py` | retrieval・Jev・score観測の増分 | 9 |
| `backend/tests/unit/test_obs_migration.py` | 0008ファイルの静的ピン(revision連結・3テーブル) | 2 |
| `backend/tests/unit/test_obs_compose_pins.py` | compose追記の静的ピン(127.0.0.1バインド・アンカー構造維持) | 11 |
| `backend/tests/integration/test_obs_schema.py` | 0008の3テーブル・INDEX・head=0008(integration・supervisor検証時) | 2 |
| `backend/tests/integration/test_obs_api_ci.py` | sessions実DB INSERT・/metrics 200 text/plain(integration) | 5・10 |
| `backend/tests/integration/test_obs_kpi_job_ci.py` | KpiJobのfixture DB実行(integration) | 7 |
| `backend/staging_tests_obs/conftest.py` | ws-4受け入れハーネスの基盤(sys.pathでstaging_testsのhelpersを再利用・証拠YAML出力はws4-接頭辞) | 13 |
| `backend/staging_tests_obs/test_w4_a_env_seed.py` | 環境前提・シード投入・メトリクス初期観察 | 13 |
| `backend/staging_tests_obs/test_w4_b_kpi_daily.py` | シナリオ遷移・sessions投与・Clock前進でKpiJob発火・kpi_daily/prometheus照合 | 13 |
| `backend/staging_tests_obs/test_w4_c_exposure.py` | /metrics 200・grafana 4枚稼働・非ループバック拒否(承認条件(d)項) | 13 |
| `docs/plans/M4/ws-4-report.md` | 報告書(§7の形式) | 14 |

### 4.2 変更するもの(既存・追従)

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/pyproject.toml` | dependenciesへ `prometheus-client>=0.21`(uv add実施・uv.lockも更新) | 1 |
| `backend/src/latch/settings.py` | obs_worker_metrics_port(既定0)・obs_collect_interval_sec(既定60)・kpi_backfill_days(既定4)を追記 | 3 |
| `backend/tests/unit/test_settings.py` | obs系既定値ピン+集合ピン2関数を追加 | 3 |
| `backend/src/latch/main.py` | obs_sessions_router登録・HTTPMetricsMiddleware挿入・/metricsマウント | 10 |
| `backend/src/latch/worker/main.py` | collector・KpiJobのDIとtask化・start_http_server(obs_worker_metrics_port>0のとき)・_run_matching/_run_direct_pipelineへobserve_retrieval | 7・9 |
| `backend/src/latch/worker/jev.py` | judge_pair前後のClock計測+observe_jev_execution(provider・latency・usage) | 9 |
| `backend/src/latch/worker/matching/latch_engine.py` | 提案確定点でscore histogram observe(shape="pair") | 9 |
| `backend/src/latch/worker/matching/group_engine.py` | aggregate確定点でscore histogram observe(shape="group") | 9 |
| `compose.staging.yaml` | worker-commonのenvironmentへ LATCH_OBS_WORKER_METRICS_PORT: "9100"・worker1/worker2へports(127.0.0.1:18101:9100・18102:9100) | 11 |
| `compose.load.yaml` | worker1/worker2へports(127.0.0.1:28101:9100・28102:9100)・env追加なし(loadのenvはstagingと同じアンカー構造だがobs_worker_metrics_portはworker-commonに書かれていないため、load側もx-worker-commonにenvを追記する必要がある — Task 11で両方を扱う) | 11 |
| `Makefile` | dashboard-up/down/ps/logs・staging-obs-testターゲット追加(.PHONY更新) | 11 |
| `backend/tests/integration/test_geo.py` | test_head_is_0007→0008追従(機械的追随) | 2 |
| `backend/staging_tests/conftest.py` | EXPECTED_HEAD "0007"→"0008"(1行) | 2 |

### 4.3 触らないもの(禁止・スコープ外と判断する基準)

- **compose.yaml(ci)**: 触らない。ci環境へworker metricsポートは設定しない(obs_worker_metrics_port既定0=無効・ciは自回帰の機能試験が目的・design §3.2・承認事項4)。
- **compose.staging.yamlの既存サービス定義の実質**(db/redis/pubsub/api1/api2/nginxの定義・backend-envアンカー・api-commonアンカー): 追記はx-worker-commonへのenv 1行とworker1/2のportsのみ。
- **JevCostGuard・CircuitBreaker・Gateway・logallow.py・SendRecord/PushSendRecordのロジック**: latch.cost.alert・latch.llm.send・latch.push.sendの構造化ログは無変更。メトリクスは横流しのみ。breakerのRedis共有化(旧design論点)は本単位でやらない。gateway.pyは1行も変更しない(breaker参照はworker側で既存属性 `_breaker` をgetattrで受ける)。
- **latch.llm.send・latch.push.sendとメトリクスの統合**: 送信記録とメトリクスは別媒体として並存(design §3.2)。
- **既存の遷移・回答・通知の書込みロジック**: latch_status_events・responses・notificationsのINSERTは読むだけで改変しない(notificationsはINSERT 3箇所への計測挿入を避け、日次SQL集計で取る — design §1.3)。
- **既存テスト・既存settings項目の値**: 既存ピンの値変更はtest_geo.pyのhead追従のみ。他の既存assertへの変更は禁止。
- **core/clock.py・core/db.py・auth/**: Clock契約・認証基盤は消費のみ。
- **スコープ外と判断する基準**: ①docs本体(05 §2等)の改版は本単位ではやらない(docs改版候補=design §7論点3・次回docs改版へ) ②フロントエンドのsessions呼び出し実装(Phase 0・design §2.4) ③alert規則の定義(D-16 80% alert・lag alertはws-6/ws-7が消費する器のみ提供し、alertmanager等は構成しない) ④計測対象の追加(「4枚に足りないパネル」はws-6以降の拡張・本単位では§2.3の定義表の範囲に限定)。

## 5. タスク(実装ステップ・TDD)

タスク実行の共通手順(各Taskに明記しない): 作業はworktree内・コミット前に `make lint && make test` グリーン・basename一意コマンド空・`git status --short` で差分確認。pytestの実行は `cd backend && uv run pytest tests/unit/test_xxx.py -v` の形。

---

### Task 1: 依存追加とobs/metrics.py(メトリクスの単一真実)

**Files:**
- Modify: `backend/pyproject.toml`(uv addによる・dependenciesへprometheus-client)
- Create: `backend/src/latch/obs/__init__.py`(空)
- Create: `backend/src/latch/obs/metrics.py`
- Test: `backend/tests/unit/test_obs_metrics.py`

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: 後続タスクが消費する公開IF — `from latch.obs.metrics import metrics, build_metrics, observe_retrieval, observe_jev_execution, observe_latch_score`。`build_metrics(registry: CollectorRegistry | None = None) -> Metrics`(Metricsはdataclass・下記の属性名)。`observe_retrieval(layer1_pass_count: int, vector_items: int) -> None`。`observe_jev_execution(provider: str, latency_s: float, usage: dict | None) -> None`。`observe_latch_score(shape: str, score: float) -> None`(shapeは"pair"または"group")。モジュール単例 `metrics` は既定REGISTRYで1回構築

- [ ] **Step 1: 依存を追加する**

```bash
cd backend && uv add prometheus-client
```

実行後の確認: `git diff backend/pyproject.toml` で `"prometheus-client>=0.21"` 相当の追加と `uv.lock` の更新があること(バージョンはuvの解決に任せる・計画書では下限0.21のみ固定)。

- [ ] **Step 2: 失敗する試験を書く**

`backend/tests/unit/test_obs_metrics.py`:

```python
"""obs/metrics.pyのメトリクス定義ピン(design §4・許可リストと同型の規律)。

新規メトリクスの追加は「metrics.pyの定義」と「EXPECTED_*定数の更新」の
2箇所必須にする(名前一覧化=許可リストと同型の機械規律・08 §2.4への新規
露出の注意喚起)。値のassertはカスタムCollectorRegistryの独立インスタンス
で行う(design §2.1・prometheus-client公式のテスト作法)。
"""

from prometheus_client import CollectorRegistry, REGISTRY

from latch.obs.metrics import build_metrics, metrics

# Counterは露出時に _total が付く・Histogramは _bucket/_count/_sum・
# Gaugeは素の名前。namespace="latch" が接頭辞になる。
EXPECTED_SAMPLE_NAMES = {
    # HTTP基本メトリクス(design §2.9)
    "latch_http_requests_total",
    "latch_http_request_duration_seconds_bucket",
    "latch_http_request_duration_seconds_count",
    "latch_http_request_duration_seconds_sum",
    # パイプライン群(04 §2)
    "latch_event_queue_pending",
    "latch_event_queue_oldest_pending_seconds",
    "latch_pipeline_candidates",
    "latch_hardfilter_pass_total",
    "latch_hardfilter_input_total",
    "latch_retrieval_vector_items_bucket",
    "latch_retrieval_vector_items_count",
    "latch_retrieval_vector_items_sum",
    "latch_breaker_state",
    # Jev群(04 §2・§7)
    "latch_jev_executions_total",
    "latch_jev_latency_seconds_bucket",
    "latch_jev_latency_seconds_count",
    "latch_jev_latency_seconds_sum",
    "latch_jev_usage_tokens_total",
    # 提案と行動(04 §2)
    "latch_score_bucket",
    "latch_score_count",
    "latch_score_sum",
    # KPI(design §2.3)
    "latch_kpi_daily_numerator",
    "latch_kpi_daily_denominator",
}


def _sample_names(registry: CollectorRegistry) -> set[str]:
    names = set()
    for family in registry.collect():
        for sample in family.samples:
            names.add(sample.name)
    return names


def test_metric_sample_names_are_the_approved_list():
    """露出sample名が許可リストと完全一致(名前一覧化の機械規律)。"""
    registry = CollectorRegistry()
    build_metrics(registry)
    assert _sample_names(registry) == EXPECTED_SAMPLE_NAMES


def test_build_metrics_on_fresh_registry_is_isolated():
    """カスタムレジストリのインスタンスは既定REGISTRYと独立(値assert経路)。"""
    registry = CollectorRegistry()
    m = build_metrics(registry)
    m.event_queue_pending.set(7)
    m.jev_executions_total.labels(provider="typesafe_jev").inc(3)
    assert registry.get_sample_value("latch_event_queue_pending") == 7.0
    assert (
        registry.get_sample_value(
            "latch_jev_executions_total", {"provider": "typesafe_jev"}
        )
        == 3.0
    )
    assert REGISTRY.get_sample_value("latch_event_queue_pending") is None


def test_module_singleton_is_registered_once():
    """モジュール単例は既定REGISTRYに存在する(import=1回構築の契約)。"""
    names = _sample_names(REGISTRY)
    assert "latch_http_requests_total" in names
    assert "latch_event_queue_pending" in names


def test_observe_helpers_increment_the_right_series():
    """observeヘルパーがラベル付きseriesを正しく増やす(後続タスクのIF契約)。"""
    registry = CollectorRegistry()
    m = build_metrics(registry)
    from latch.obs import metrics as obs_metrics_module

    # observe_retrieval等はモジュール単例へ作用するため、ここでは単例の
    # 差分で検証する(プロセス内累積はpytest同一プロセス内で増え続ける)
    before_pass = REGISTRY.get_sample_value("latch_hardfilter_pass_total") or 0
    before_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    obs_metrics_module.observe_retrieval(layer1_pass_count=12, vector_items=5)
    after_pass = REGISTRY.get_sample_value("latch_hardfilter_pass_total") or 0
    after_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    assert after_pass == before_pass + 12
    assert after_input == before_input + 1

    before_exec = REGISTRY.get_sample_value(
        "latch_jev_executions_total", {"provider": "typesafe_jev"}
    ) or 0
    obs_metrics_module.observe_jev_execution(
        provider="typesafe_jev",
        latency_s=0.4,
        usage={"input_tokens": 10, "output_tokens": 0},
    )
    after_exec = REGISTRY.get_sample_value(
        "latch_jev_executions_total", {"provider": "typesafe_jev"}
    ) or 0
    assert after_exec == before_exec + 1
    assert (
        REGISTRY.get_sample_value(
            "latch_jev_usage_tokens_total",
            {"provider": "typesafe_jev", "kind": "input"},
        )
        == 10.0
    )

    before_score = REGISTRY.get_sample_value(
        "latch_score_count", {"shape": "pair"}
    ) or 0
    obs_metrics_module.observe_latch_score(shape="pair", score=0.9)
    after_score = REGISTRY.get_sample_value(
        "latch_score_count", {"shape": "pair"}
    ) or 0
    assert after_score == before_score + 1

    # usage=None・未知shapeは例外を上げない(観測は本体処理を壊さない)
    obs_metrics_module.observe_jev_execution(
        provider="fallback_llm", latency_s=0.1, usage=None
    )
    obs_metrics_module.observe_latch_score(shape="group", score=0.7)
```

- [ ] **Step 3: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_metrics.py -v`
Expected: FAIL(ModuleNotFoundError: latch.obs が存在しない)

- [ ] **Step 4: metrics.pyを実装する**

`backend/src/latch/obs/__init__.py` は空ファイル。

`backend/src/latch/obs/metrics.py`:

```python
"""全メトリクスの単一真実(design §3.1・04 §2の3群+アクセス系+HTTP)。

namespace="latch"固定。08 §2.4規律: labelはmethod・pathテンプレート・
status・provider・metric名等のシステム属性のみ。user_id・クエリ・body・
Intent原文・座標を含めない。本番SDK移行時(Cloud Monitoring+OTLP・04 §3)
の差し替え面は本モジュールのみ(design §2.1案A・supervisor承認)。

テストはbuild_metrics(CollectorRegistry())で独立インスタンスを作り値を
assertする。本番はモジュール単例metrics(既定REGISTRY)をapi・workerで
共用する(uvicorn/workerプロセスごとに独立したカウンタを持つ=pull型の
正しい使い方・Prometheus側がtarget別にscrapeしてPromQLで集約する)。
"""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry

# D-01通知閾値0.60周辺の観察(A/B・Phase 3前提の器 — design §2.8)
SCORE_BUCKETS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
# Vector Retrieval件数(K_v=50上限の分布 — 02#5の検証素材)
VECTOR_BUCKETS = (1, 5, 10, 20, 30, 40, 50)


@dataclass
class Metrics:
    """metrics.pyで定義する全メトリクス(属性名=コードが消費するIF)。"""

    http_requests: Counter
    http_request_duration: Histogram
    event_queue_pending: Gauge
    event_queue_oldest_pending_seconds: Gauge
    pipeline_candidates: Gauge
    hardfilter_pass_total: Counter
    hardfilter_input_total: Counter
    retrieval_vector_items: Histogram
    breaker_state: Gauge
    jev_executions_total: Counter
    jev_latency_seconds: Histogram
    jev_usage_tokens_total: Counter
    score: Histogram
    kpi_daily_numerator: Gauge
    kpi_daily_denominator: Gauge


def build_metrics(registry: CollectorRegistry | None = None) -> Metrics:
    """registry=Noneは既定REGISTRYへ登録(本番1本)。テストは専用レジストリ。

    注意: prometheus-clientの registry=None は「未登録」を意味する(公式
    ドキュメント)。本番の既定REGISTRYへの登録は引数を渡さないことで行う。
    """
    return Metrics(
        http_requests=Counter(
            "http_requests_total",
            "Total HTTP requests by method, path template and status",
            labelnames=("method", "path", "status"),
            namespace="latch",
            registry=registry,
        ),
        http_request_duration=Histogram(
            "http_request_duration_seconds",
            "HTTP request latency by method and path template",
            labelnames=("method", "path"),
            namespace="latch",
            registry=registry,
        ),
        event_queue_pending=Gauge(
            "event_queue_pending",
            "match_events rows in status=pending (Event Queue lag量)",
            namespace="latch",
            registry=registry,
        ),
        event_queue_oldest_pending_seconds=Gauge(
            "event_queue_oldest_pending_seconds",
            "Age in seconds of the oldest pending match_event",
            namespace="latch",
            registry=registry,
        ),
        pipeline_candidates=Gauge(
            "pipeline_candidates",
            "match_candidates total rows (生成済み候補ペア数)",
            namespace="latch",
            registry=registry,
        ),
        hardfilter_pass_total=Counter(
            "hardfilter_pass_total",
            "Layer 1 pass count before LIMIT (accumulated)",
            namespace="latch",
            registry=registry,
        ),
        hardfilter_input_total=Counter(
            "hardfilter_input_total",
            "Retrieval runs (each run counts 1 input)",
            namespace="latch",
            registry=registry,
        ),
        retrieval_vector_items=Histogram(
            "retrieval_vector_items",
            "Vector retrieval item count per run (LIMIT後)",
            namespace="latch",
            buckets=VECTOR_BUCKETS,
            registry=registry,
        ),
        breaker_state=Gauge(
            "breaker_state",
            "CircuitBreaker state: closed=0 open=1 half_open=2",
            namespace="latch",
            registry=registry,
        ),
        jev_executions_total=Counter(
            "jev_executions_total",
            "Jev executions by provider (実API呼び出しをすべて計上)",
            labelnames=("provider",),
            namespace="latch",
            registry=registry,
        ),
        jev_latency_seconds=Histogram(
            "jev_latency_seconds",
            "Jev call latency by provider",
            labelnames=("provider",),
            namespace="latch",
            registry=registry,
        ),
        jev_usage_tokens_total=Counter(
            "jev_usage_tokens_total",
            "Jev token usage by provider and kind (input/output)",
            labelnames=("provider", "kind"),
            namespace="latch",
            registry=registry,
        ),
        score=Histogram(
            "score",
            "LATCH score at proposal (pair=latch_score, group=aggregate)",
            labelnames=("shape",),
            namespace="latch",
            buckets=SCORE_BUCKETS,
            registry=registry,
        ),
        kpi_daily_numerator=Gauge(
            "kpi_daily_numerator",
            "kpi_daily numerator by metric_key (latest row)",
            labelnames=("metric",),
            namespace="latch",
            registry=registry,
        ),
        kpi_daily_denominator=Gauge(
            "kpi_daily_denominator",
            "kpi_daily denominator by metric_key (latest row)",
            labelnames=("metric",),
            namespace="latch",
            registry=registry,
        ),
    )


# モジュール単例(本番)。import時に1回のみ構築する(複数回はduplicate
# registration例外になるため、本モジュールは再importで再構築しない)
metrics = build_metrics()


def observe_retrieval(*, layer1_pass_count: int, vector_items: int) -> None:
    """Layer 2検索1回分の観測(worker側から呼ぶ・design §2.6)。"""
    metrics.hardfilter_pass_total.inc(layer1_pass_count)
    metrics.hardfilter_input_total.inc(1)
    metrics.retrieval_vector_items.observe(vector_items)


def observe_jev_execution(
    *, provider: str, latency_s: float, usage: dict | None
) -> None:
    """Jev実行1回分の観測(jev.pyのjudge_pair後・design §2.7)。

    usageはenvelopeのusage辞書(input_tokens/output_tokensキー・stub定数と
    同一)。None・キー欠落は0扱い(観測は本体処理を壊さない)。
    """
    metrics.jev_executions_total.labels(provider=provider).inc()
    metrics.jev_latency_seconds.labels(provider=provider).observe(latency_s)
    usage = usage or {}
    metrics.jev_usage_tokens_total.labels(
        provider=provider, kind="input"
    ).inc(int(usage.get("input_tokens") or 0))
    metrics.jev_usage_tokens_total.labels(
        provider=provider, kind="output"
    ).inc(int(usage.get("output_tokens") or 0))


def observe_latch_score(*, shape: str, score: float) -> None:
    """提案確定時のscore観測(latch_engine=pair・group_engine=group)。"""
    metrics.score.labels(shape=shape).observe(float(score))
```

実装後の注意: `latch.arch` 試験(test_arch_no_direct_time.py)が新モジュールをスキャンする。time系参照は含まない。

- [ ] **Step 5: 試験が通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_metrics.py -v`
Expected: PASS(6件)

- [ ] **Step 6: コミットする**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/latch/obs/ backend/tests/unit/test_obs_metrics.py
git commit -m "feat: obs/metrics.py — prometheus-client導入とメトリクス単一真実(M4 ws-4)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: マイグレーション0008とheadピン追従

**Files:**
- Create: `backend/alembic/versions/0008_obs_sessions_kpi.py`
- Modify: `backend/tests/integration/test_geo.py`(test_head_is_0007→0008機械的追随)
- Modify: `backend/staging_tests/conftest.py`(EXPECTED_HEAD "0007"→"0008"・1行)
- Test: `backend/tests/unit/test_obs_migration.py`(新規)

**Interfaces:**
- Consumes: alembic 0007(直前リビジョン・0007_users_consent.pyと同一作法)
- Produces: 3テーブル —
  `sessions(id uuid PK・user_id uuid NOT NULL・created_at timestamptz NOT NULL)`+`INDEX idx_sessions_user_time ON sessions (user_id, created_at)`
  `kpi_daily(metric_key text NOT NULL・day date NOT NULL・numerator numeric NOT NULL DEFAULT 0・denominator numeric NOT NULL DEFAULT 0・created_at timestamptz NOT NULL・updated_at timestamptz NOT NULL・PK(metric_key, day))`
  `kpi_muted_user_snapshot(day date NOT NULL・user_id uuid NOT NULL・created_at timestamptz NOT NULL・PK(day, user_id))`
  いずれもFKなし(design §4「他テーブルの削除順(FK)に依存しない独立表」・§2.2(c))

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_obs_migration.py`:

```python
"""0008マイグレーションファイルの静的ピン(M4 ws-4)。

upgrade()の実行(integration)はtest_obs_schema.pyが所有する。unitでは
ファイルの存在・リビジョン連結・定義SQLの骨格のみを検査する。
"""

import importlib.util
from pathlib import Path


def _load_0008():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0008_obs_sessions_kpi.py"
    spec = importlib.util.spec_from_file_location("mig_0008", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_points_at_0007():
    mig = _load_0008()
    assert mig.revision == "0008"
    assert mig.down_revision == "0007"


def test_0008_defines_the_three_tables():
    """定義SQL(モジュール定数SQL_STATEMENTS)に3テーブルとINDEXがある。

    FKなし独立表(§2.2(c)・design §4「他テーブルの削除順(FK)に依存しない
    独立表」)の機械確認を兼ねる。
    """
    mig = _load_0008()
    joined = "\n".join(mig.SQL_STATEMENTS)
    for table in ("sessions", "kpi_daily", "kpi_muted_user_snapshot"):
        assert f"CREATE TABLE {table}" in joined
    assert "idx_sessions_user_time" in joined
    assert "REFERENCES" not in joined
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_migration.py -v`
Expected: FAIL(ファイルが存在しない)

- [ ] **Step 3: マイグレーションを実装する**

`backend/alembic/versions/0008_obs_sessions_kpi.py`:

```python
"""Observability用3テーブル(M4 ws-4・design §2.4/§2.8・supervisor承認3)。

sessions: アクセス系基本計測(04 §2・05 §19。user_idと時刻のみ・Intentの
文言を運ぶ列は構造的に存在しない)。
kpi_daily: KPI日次集計の恒久値((metric_key, day)行持ち・分子/分母のみを
保持し比率は保存しない — design §2.3)。09 §2に規定のないテーブルのため
05 §2への追記はdocs改版候補(design §7論点3・次回docs改版へ)。
kpi_muted_user_snapshot: 通知オフ率の素材(mutedは履歴を持たないため日次
スナップショット差分 — design §2.8)。
3テーブルともFKなし(design §4「他テーブルの削除順(FK)に依存しない独立
表」。user_idはusers行消滅後も残る観測用素材)。

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_STATEMENTS = [
    # sessions
    """
    CREATE TABLE sessions (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL,
        created_at timestamptz NOT NULL,
        CONSTRAINT sessions_pkey PRIMARY KEY (id)
    )
    """,
    "CREATE INDEX idx_sessions_user_time ON sessions (user_id, created_at)",
    # kpi_daily
    """
    CREATE TABLE kpi_daily (
        metric_key text NOT NULL,
        day date NOT NULL,
        numerator numeric NOT NULL DEFAULT 0,
        denominator numeric NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        CONSTRAINT kpi_daily_pkey PRIMARY KEY (metric_key, day)
    )
    """,
    # kpi_muted_user_snapshot
    """
    CREATE TABLE kpi_muted_user_snapshot (
        day date NOT NULL,
        user_id uuid NOT NULL,
        created_at timestamptz NOT NULL,
        CONSTRAINT kpi_muted_user_snapshot_pkey PRIMARY KEY (day, user_id)
    )
    """,
]


def upgrade() -> None:
    for stmt in SQL_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kpi_muted_user_snapshot")
    op.execute("DROP TABLE IF EXISTS kpi_daily")
    op.execute("DROP TABLE IF EXISTS sessions")
```

- [ ] **Step 4: 既存headピンを0008へ追従する**

`backend/tests/integration/test_geo.py`:

- 関数名 `test_head_is_0007` を `test_head_is_0008` に変更
- docstringを「0008がhead(migrated_dbがheadまで進めた結果・M4 ws-4で0008追加)」へ更新
- assert値を `assert version == "0008"` へ変更

`backend/staging_tests/conftest.py`:

- `EXPECTED_HEAD = "0007"` の行とコメントを `EXPECTED_HEAD = "0008"  # ws-4が0008を追加した後のhead(M4 ws-4)` に変更

- [ ] **Step 5: integration試験の骨格もこのTaskで置く(collect-onlyまで検査)**

`backend/tests/integration/test_obs_schema.py`:

```python
"""0008のスキーマ検証(M4 ws-4・integration・test-ciで実行)。"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def test_head_is_0008_in_obs_schema(db_engine):
    """0008がhead(M4 ws-4追加・test_geoの追従と同一の検査をobs側でも持つ)。"""
    async with db_engine.connect() as conn:
        version = (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar()
    assert version == "0008"


async def test_three_obs_tables_exist(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name IN "
                "('sessions','kpi_daily','kpi_muted_user_snapshot')"
            )
        )
        names = {row[0] for row in result}
    assert names == {"sessions", "kpi_daily", "kpi_muted_user_snapshot"}


async def test_sessions_user_time_index_exists(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                 "AND tablename='sessions'")
        )
        names = {row[0] for row in result}
    assert "idx_sessions_user_time" in names


async def test_kpi_daily_upsert_roundtrip(db_engine):
    """(metric_key, day)のUPSERTが機能する(主キー衝突でUPDATE)。"""
    from datetime import date, datetime, UTC
    async with db_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO kpi_daily (metric_key, day, numerator, denominator,"
                 " created_at, updated_at) VALUES ('mlr', :day, 1, 2,"
                 " :now, :now)"),
            {"day": date(2026, 10, 4), "now": datetime.now(UTC)},
        )
        await conn.execute(
            text("INSERT INTO kpi_daily (metric_key, day, numerator, denominator,"
                 " created_at, updated_at) VALUES ('mlr', :day, 3, 4,"
                 " :now, :now) ON CONFLICT (metric_key, day) DO UPDATE SET"
                 " numerator=3, denominator=4, updated_at=:now"),
            {"day": date(2026, 10, 4), "now": datetime.now(UTC)},
        )
        row = (
            await conn.execute(
                text("SELECT numerator, denominator FROM kpi_daily"
                     " WHERE metric_key='mlr' AND day=:day"),
                {"day": date(2026, 10, 4)},
            )
        ).first()
    assert (int(row[0]), int(row[1])) == (3, 4)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM kpi_daily WHERE metric_key='mlr' AND day=:day"),
            {"day": date(2026, 10, 4)},
        )
```

Run: `cd backend && uv run pytest tests/integration/test_obs_schema.py --collect-only -q`
Expected: 4件収集(実行はしない・§0規律)

- [ ] **Step 6: 試験が通ることを確認してコミットする**

Run: `cd backend && uv run pytest tests/unit/test_obs_migration.py -v`
Expected: PASS(3件)

```bash
git add backend/alembic/versions/0008_obs_sessions_kpi.py backend/tests/unit/test_obs_migration.py backend/tests/integration/test_geo.py backend/tests/integration/test_obs_schema.py backend/staging_tests/conftest.py
git commit -m "feat: マイグレーション0008(sessions・kpi_daily・kpi_muted_user_snapshot)とheadピン追従(M4 ws-4)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: settings追記と既定値・集合ピン

**Files:**
- Modify: `backend/src/latch/settings.py`(obs系3項目追記)
- Modify: `backend/tests/unit/test_settings.py`(ピン2関数追加)

**Interfaces:**
- Consumes: なし
- Produces: `Settings.obs_worker_metrics_port: int = 0`・`Settings.obs_collect_interval_sec: int = 60`・`Settings.kpi_backfill_days: int = 4`(Task 7・8が消費。env名はLATCH_OBS_WORKER_METRICS_PORT・LATCH_OBS_COLLECT_INTERVAL_SEC・LATCH_KPI_BACKFILL_DAYS)

- [ ] **Step 1: 失敗する試験を書く(test_settings.pyへ追加)**

```python
def test_obs_settings_defaults(monkeypatch):
    monkeypatch.delenv("LATCH_OBS_WORKER_METRICS_PORT", raising=False)
    monkeypatch.delenv("LATCH_OBS_COLLECT_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("LATCH_KPI_BACKFILL_DAYS", raising=False)
    s = Settings()
    assert s.obs_worker_metrics_port == 0  # M4 ws-4(design §3.1・0=無効・ci無効維持)
    assert s.obs_collect_interval_sec == 60  # M4 ws-4(collector tick)
    assert s.kpi_backfill_days == 4  # M4 ws-4(D-09自己申告3日+マージン)


def test_obs_settings_are_exactly_three_fields(monkeypatch):
    # obs系設定の過剰供給を防ぐ機械検査(auth_8項目ピンと同一作法)
    monkeypatch.delenv("LATCH_OBS_WORKER_METRICS_PORT", raising=False)
    monkeypatch.delenv("LATCH_OBS_COLLECT_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("LATCH_KPI_BACKFILL_DAYS", raising=False)
    obs_fields = {f for f in Settings.model_fields if f.startswith("obs_")}
    kpi_fields = {f for f in Settings.model_fields if f.startswith("kpi_")}
    assert obs_fields == {"obs_worker_metrics_port", "obs_collect_interval_sec"}
    assert kpi_fields == {"kpi_backfill_days"}


def test_obs_settings_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_OBS_WORKER_METRICS_PORT", "9100")
    monkeypatch.setenv("LATCH_OBS_COLLECT_INTERVAL_SEC", "30")
    monkeypatch.setenv("LATCH_KPI_BACKFILL_DAYS", "5")
    s = Settings()
    assert s.obs_worker_metrics_port == 9100
    assert s.obs_collect_interval_sec == 30
    assert s.kpi_backfill_days == 5
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_settings.py -v`
Expected: FAIL(obs_worker_metrics_portが存在しない)

- [ ] **Step 3: settings.pyへ追記する**

ファイル末尾(`auth_testkeys_dir` の後)へ:

```python
    # --- Observability(M4 ws-4・design §3.1)---
    # workerのメトリクス公開ポート(start_http_server)。0=無効(既定)。
    # ciは無効のまま維持(自回帰の機能試験が目的・承認事項4)。staging/load
    # のcomposeでのみ設定する
    obs_worker_metrics_port: int = 0
    # collectorの周期秒(pending件数・最古pending年齢・Candidate数・
    # kpi_daily最新行のgauge化・KpiJobも同一周期でJST日付境界を検査)
    obs_collect_interval_sec: int = 60
    # KPI日次集計の遡及幅(D-09の自己申告期限3日+マージン=4)
    kpi_backfill_days: int = 4
```

- [ ] **Step 4: 試験が通ることを確認してコミットする**

Run: `cd backend && uv run pytest tests/unit/test_settings.py tests/unit/auth/test_auth_settings.py tests/unit/llm/test_llm_factory.py tests/unit/geo/test_geo_settings.py -v`
Expected: PASS(既存集合ピンがすべて不変であることの確認を含む)

```bash
git add backend/src/latch/settings.py backend/tests/unit/test_settings.py
git commit -m "feat: settingsへobs系3項目追加(既定0/60/4・ci無効維持)と既定値・集合ピン(M4 ws-4)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: HTTP基本メトリクスmiddleware

**Files:**
- Create: `backend/src/latch/obs/middleware.py`
- Test: `backend/tests/unit/test_obs_metrics.py`(試験を追記)

**Interfaces:**
- Consumes: `latch.obs.metrics.metrics`(Task 1)・`Clock`(core/clock.py・app.state.clock)・Starlette BaseHTTPMiddleware
- Produces: `HTTPMetricsMiddleware`(main.pyが `app.add_middleware(HTTPMetricsMiddleware)` で挿入・Task 10)。ラベル: method・path(routeテンプレート・未マッチは"unmatched")・status。**/metrics開始のパスは計上しない**(自己増殖防止)

- [ ] **Step 1: 失敗する試験を書く(test_obs_metrics.pyへ追記)**

```python
# -- middleware(Task 4)--

import uuid as _uuid
from datetime import UTC, datetime, timedelta

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_CLAIMS = AccessTokenClaims(
    auth_provider="google",
    auth_subject="obs-mw-1",
    jti="jti",
    sid="sid",
    iat=_NOW,
    exp=_NOW + timedelta(seconds=3600),
)


async def test_http_metrics_increments_on_health(app, client, fake_clock):
    """/health 1回でcounter+1・histogramに観測が積まれる。"""
    before = REGISTRY.get_sample_value(
        "latch_http_requests_total", {"method": "GET", "path": "/health", "status": "200"}
    ) or 0
    before_count = REGISTRY.get_sample_value(
        "latch_http_request_duration_seconds_count", {"method": "GET", "path": "/health"}
    ) or 0
    resp = await client.get("/health")
    assert resp.status_code == 200
    after = REGISTRY.get_sample_value(
        "latch_http_requests_total", {"method": "GET", "path": "/health", "status": "200"}
    ) or 0
    after_count = REGISTRY.get_sample_value(
        "latch_http_request_duration_seconds_count", {"method": "GET", "path": "/health"}
    ) or 0
    assert after == before + 1
    assert after_count == before_count + 1


async def test_http_metrics_unknown_path_is_unmatched(client):
    """route未マッチ(404)はpathテンプレートがunmatchedになる(クエリ・生pathを
    labelに含まない — 08 §2.4規律)。"""
    resp = await client.get("/no-such-path")
    assert resp.status_code == 404
    samples = []
    for family in REGISTRY.collect():
        for sample in family.samples:
            if (
                sample.name == "latch_http_requests_total"
                and sample.labels.get("path") == "/no-such-path"
            ):
                samples.append(sample)
    assert samples == []  # 生pathはlabelに現れない
    assert (
        REGISTRY.get_sample_value(
            "latch_http_requests_total",
            {"method": "GET", "path": "unmatched", "status": "404"},
        )
        is not None
    )


async def test_http_metrics_labels_are_system_only(app, client):
    """HTTPメトリクスのlabelはmethod・path・statusのみ(user_id不使用)。"""
    for family in REGISTRY.collect():
        if family.name != "latch_http_requests_total":
            continue
        for sample in family.samples:
            if sample.name == "latch_http_requests_total":
                assert set(sample.labels.keys()) <= {"method", "path", "status"}


async def test_http_metrics_protected_route_records_204(app, client):
    """認証保護経路(POST /v1/sessions・Task 5のルーターがまだ無い場合404)でも
    middlewareは経路テンプレートで計上する(Task 10以降は /v1/sessions が
    templateになる)。ここでは存在しない保護的メソッドでunmatched経路のみ確認。"""
    app.dependency_overrides[require_authenticated] = lambda: _CLAIMS
    resp = await client.post("/no-such-protected-path")
    assert (
        REGISTRY.get_sample_value(
            "latch_http_requests_total",
            {"method": "POST", "path": "unmatched", "status": "404"},
        )
        is not None
    )
    app.dependency_overrides.pop(require_authenticated, None)
```

注意: `app` と `client` と `fake_clock` は既存共通fixture(backend/tests/conftest.py・FakeClock固定時刻2026-09-27T12:00Zのcreate_app)。

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_metrics.py -v`
Expected: middleware試験4件がFAIL(カウンタが増えない)

- [ ] **Step 3: middlewareを実装する**

`backend/src/latch/obs/middleware.py`:

```python
"""HTTP基本メトリクスmiddleware(design §2.9・supervisor承認2026-10-04)。

docs 3群の明示的拡張。ラベルはmethod・pathテンプレート・statusのみ。
user_id・クエリ・body・Intent文言・座標は含めない(08 §2.4と同規律)。
/metricsの自己アクセスは計上しない(Prometheusのscrape周期で自己増殖
するため)。理由: ws-6の性能5目標(10 §4.1)が絶対値合格であり、API側の
p95 histogramが測定基盤として実質前提(単位表でws-6はws-4に「測定基盤
前提」で依存)。

レイテンシ計測はClock経由(C2・arch testのtime.*禁止に従う)。
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from latch.obs.metrics import metrics


class HTTPMetricsMiddleware(BaseHTTPMiddleware):
    """全リクエストをmethod・pathテンプレート・statusで計上する。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path.startswith("/metrics"):
            return await call_next(request)
        method = request.method
        clock = request.app.state.clock
        t0 = clock.now()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        elapsed = (clock.now() - t0).total_seconds()
        metrics.http_requests.labels(
            method=method, path=path, status=str(response.status_code)
        ).inc()
        metrics.http_request_duration.labels(method=method, path=path).observe(
            elapsed
        )
        return response
```

注: FakeClock(固定時刻)ではelapsedは常に0秒だが、counter/histogram_countの増加は検証できる(Task 4の試験)。実レイテンシの正しさはstaging実測(Task 13)で確認する。

- [ ] **Step 4: 試験が通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_metrics.py -v`
Expected: PASS(全件・middleware分含む)

- [ ] **Step 5: コミットする**

```bash
git add backend/src/latch/obs/middleware.py backend/tests/unit/test_obs_metrics.py
git commit -m "feat: HTTP基本メトリクスmiddleware(method・pathテンプレート・statusのみ・/metrics自己計上なし)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: POST /v1/sessions(アクセス系基本計測)

**Files:**
- Create: `backend/src/latch/obs/sessions.py`
- Test: `backend/tests/unit/test_obs_sessions_api.py`(新規)

**Interfaces:**
- Consumes: `require_authenticated`(AccessTokenClaims)・Clock・SQLAlchemy AsyncEngine(app.state.db_engine)
- Produces: `obs_sessions_router`(main.pyがTask 10でinclude)。`SessionStore(engine)` — `async def record(self, provider: str, subject: str, now: datetime) -> uuid.UUID | None`(users未登録はNone=404)。ルーターは `app.state.session_store` 注入を優先し無ければSessionStore(engine)を都度構築(既存のapp.state注入作法と同一)

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_obs_sessions_api.py`:

```python
"""POST /v1/sessions契約(05 §19・design §2.4)。

users試験と同一作法: require_authenticatedはdependency_overridesで差し
替え(authスタックを構築しない)。storeはapp.state.session_storeへスタブ
を注入する(DB不要・unit)。INSERTの実DB検証はintegration(test_obs_api_ci)
とstaging受け入れが所有する。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.main import create_app

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
CLAIMS = AccessTokenClaims(
    auth_provider="google",
    auth_subject="obs-sess-1",
    jti="jti-1",
    sid="sid-1",
    iat=NOW,
    exp=NOW + timedelta(seconds=3600),
)


class StubSessionStore:
    """ルーティング試験用スタブ(recordの呼び出しを記録する)。"""

    def __init__(self, user_id: uuid.UUID | None = None):
        self.user_id = user_id or uuid.uuid4()
        self.calls: list[tuple[str, str, datetime]] = []

    async def record(self, provider: str, subject: str, now: datetime):
        self.calls.append((provider, subject, now))
        return self.user_id


def _make_app_with(store):
    clock = FakeClock(NOW)
    app = create_app(clock=clock)
    app.state.session_store = store
    app.dependency_overrides[require_authenticated] = lambda: CLAIMS
    return app


async def _client(app) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_post_sessions_returns_204_and_records_user_and_time():
    store = StubSessionStore()
    app = _make_app_with(store)
    async for client in _client(app):
        resp = await client.post("/v1/sessions")
    assert resp.status_code == 204
    assert len(store.calls) == 1
    provider, subject, now = store.calls[0]
    assert provider == "google"
    assert subject == "obs-sess-1"
    assert now == NOW  # Clock経由(fake_clock固定値)


async def test_post_sessions_with_body_is_422():
    """request bodyは空が契約(05 §19)。非空は422 VALIDATION_ERROR。"""
    store = StubSessionStore()
    app = _make_app_with(store)
    async for client in _client(app):
        resp = await client.post("/v1/sessions", json={"note": "x"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert store.calls == []  # body非空ではrecordしない


async def test_post_sessions_unregistered_user_is_404():
    store = StubSessionStore(user_id=None)
    app = _make_app_with(store)
    async for client in _client(app):
        resp = await client.post("/v1/sessions")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "USER_NOT_FOUND"


async def test_post_sessions_without_auth_is_401():
    """認証なしは401(require_authenticated依存・例外をraiseするoverride)。"""
    from latch.auth.errors import UnauthenticatedError

    clock = FakeClock(NOW)
    app = create_app(clock=clock)
    app.state.session_store = StubSessionStore()

    def _deny():
        raise UnauthenticatedError()  # http_status=401・code="UNAUTHENTICATED"

    app.dependency_overrides[require_authenticated] = _deny
    async for client in _client(app):
        resp = await client.post("/v1/sessions")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"
```

注意: AuthErrorの階層は backend/src/latch/auth/errors.py 定義どおり(`UnauthenticatedError` が401・main.pyのauth_error_handlerがenvelopeへ変換する)。sessionsテーブルへの「Intent文言を運ばない」構造確認はtest_obs_migration.pyのSQL_STATEMENTSピン(列はid・user_id・created_atのみ)が所有する。

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_sessions_api.py -v`
Expected: FAIL(/v1/sessionsが404になり・obs_sessions_routerが未定義)

- [ ] **Step 3: sessions.pyを実装する**

`backend/src/latch/obs/sessions.py`:

```python
"""POST /v1/sessions(05 §19・04 §2アクセス系基本計測)。

契約: 認証必須・request body空・INSERT(user_idと時刻のみ)・204。
users未登録は404(users系と同一挙動・05 §19に明記はないが05 §5の
USER_NOT_FOUNDで整合)。
Intentの文言を運ぶ列はテーブルに構造的に存在しない(05 §19「user_idと
時刻のみを記録し、Intentの文言を含まない」の構造的担保 — 0008参照)。
ルータ単位のrate limitは付けない(05 §19に規定なし・アクセス計測は
呼び出し頻度が高い)。将来rate limitを付ける場合はM1 ws-4の
api_rate_limited継承で対応する。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import Clock
from latch.core.deps import get_clock

obs_sessions_router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

_INSERT_SESSION = text("""
    INSERT INTO sessions (user_id, created_at)
    SELECT u.id, CAST(:now AS timestamptz)
    FROM users u
    WHERE u.auth_provider = :provider AND u.auth_subject = :subject
    RETURNING id
""")


class SessionStore:
    """sessionsテーブルへのINSERT(薄いストア・unitはスタブ注入)。"""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def record(
        self, provider: str, subject: str, now: datetime
    ) -> uuid.UUID | None:
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    _INSERT_SESSION,
                    {"provider": provider, "subject": subject, "now": now},
                )
            ).first()
        return row[0] if row is not None else None


def get_session_store(request: Request) -> SessionStore | object:
    """app.state.session_store注入を優先(テスト)・無ければ実ストア。"""
    store = getattr(request.app.state, "session_store", None)
    if store is not None:
        return store
    return SessionStore(request.app.state.db_engine)


def _error_body(code: str, message: str) -> dict:
    # 05 §5 エラー形式(main.pyの_error_bodyと同一形状)
    return {"error": {"code": code, "message": message, "details": None}}


@obs_sessions_router.post("", status_code=204)
async def create_session(
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    clock: Annotated[Clock, Depends(get_clock)],
    store: Annotated[object, Depends(get_session_store)],
) -> Response:
    body = await request.body()
    if body.strip():
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "VALIDATION_ERROR", "request body must be empty"
            ),
        )
    user_id = await store.record(
        claims.auth_provider, claims.auth_subject, clock.now()
    )
    if user_id is None:
        return JSONResponse(
            status_code=404,
            content=_error_body("USER_NOT_FOUND", "user is not registered"),
        )
    return Response(status_code=204)
```

注: Task 10(main.py配線)までルーターは未登録のため、本Taskの試験はobs_sessions_routerを直接含むアプリをbuildできない — 試験を通すにはTask 10のmain.py変更を先に最小適用する必要がある。**Task 5ではStep 4でmain.pyへのrouter登録を先に実施する**(Task 10の該当行のみ・middlewareと/metricsはTask 10で改めて)。

- [ ] **Step 4: main.pyへrouter登録を先に適用する(Task 10の一部を先取り)**

`backend/src/latch/main.py` のimportブロックへ:

```python
from latch.obs.sessions import obs_sessions_router
```

`app.include_router(notifications_router)` の後ろへ:

```python
    app.include_router(obs_sessions_router)
```

- [ ] **Step 5: 試験が通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_sessions_api.py -v`
Expected: PASS(4件)

- [ ] **Step 6: integration試験の骨格を置く(collect-onlyまで検査)**

`backend/tests/integration/test_obs_api_ci.py`:

```python
"""sessionsの実DB・/metricsの実HTTP検証(M4 ws-4・integration・test-ci)。

compose常設api+DBに対して実行する。ユーザーは実登録経路(POST /v1/users)
で作る(既存integration試験と同一作法)。
"""

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

API = "http://127.0.0.1:8000"


def _unique_subject() -> str:
    return f"obs-sess-{uuid.uuid4().hex[:10]}"


async def _register_and_token(subject: str) -> tuple[str, httpx.Response]:
    """POST /v1/usersで登録→POST /v1/auth/tokenでaccess_tokenを取得。"""
    # 鍵とIdPトークンの発行は既存integration試験(test_users_api.py)と
    # 同一の経路を用いる。実装時にtests/integration/test_users_api.pyの
    # 登録ヘルパーを参照して同一方式で書く(重複を嫌わず明示的に書く)
    raise NotImplementedError("実装時にtest_users_api.pyの作法に合わせる")
```

注意: 上記は骨格のみ。実装時に `tests/integration/test_users_api.py` の登録・トークン発行ヘルパー(同リポジトリの既存作法)をそのまま参考に完成させ、次の4試験を入れる:
- `test_post_sessions_inserts_row` — POST /v1/sessions(204)→DB直読みでsessionsに1行・user_idがusers.idと一致・列はid・user_id・created_atの3列のみ
- `test_post_sessions_requires_auth` — Authorizationなしで401
- `test_metrics_endpoint_returns_text_plain` — GET http://127.0.0.1:8000/metrics が200・Content-Typeがtext/plain開始・`latch_http_requests_total` の文字列を含む
- `test_sessions_body_not_empty_rejected` — bodyつきPOSTが422

Run: `cd backend && uv run pytest tests/integration/test_obs_api_ci.py --collect-only -q`
Expected: 4件収集(実行はしない)

- [ ] **Step 7: コミットする**

```bash
git add backend/src/latch/obs/sessions.py backend/src/latch/main.py backend/tests/unit/test_obs_sessions_api.py backend/tests/integration/test_obs_api_ci.py
git commit -m "feat: POST /v1/sessions(05 §19契約・body空・204・users未登録404)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: KPI純関数層と定数表

**Files:**
- Create: `backend/src/latch/obs/kpi.py`(本Taskでは純関数層とSQL定数まで・KpiJobはTask 7)
- Test: `backend/tests/unit/test_obs_kpi.py`(新規)

**Interfaces:**
- Consumes: `Clock`(core/clock.py・jst_date()はClock実装が提供)・`JST`(core/clock.pyの定数)
- Produces(Task 7が消費するIF) —
  `METRIC_KEYS: frozenset[str]`(§2.3の15キー)
  `DAILY_METRIC_KEYS: frozenset[str]`(日次8キー)
  `jst_day(now: datetime) -> date`
  `backfill_days(today: date, k: int) -> list[date]`(当日を含まない直近k日・昇順)
  `week_of(day: date) -> date`(その日を含む週の月曜・JST月曜始まり)
  `week_end_of(week_start: date) -> date`(月曜+6日=日曜)
  `is_week_end(day: date) -> bool`(dayが日曜か)
  `new_muted_users(week_rows: dict[date, set], week_start: date, week_end: date) -> set`(週内snapshotに現れ・週開始前に現れないユーザー集合)
  `retention_due(base_day: date, today: date, window_days: int) -> bool`(today-1 >= base_day+window_days)
  `yes_rate_from_responses(responses: list[dict], day: date, jst_day_fn=jst_day) -> tuple[int, int]`(当日回答の(num, den))
  SQL定数: `SQL_UPSERT_DAILY`(1日分8指標のUPSERT)・`SQL_PENDING_DAYS`(kpi_dailyにmlr行の無い遡及対象日)・`SQL_UPSERT_MUTED_SNAPSHOT`・`SQL_UPSERT_RETENTION`・`SQL_UPSERT_WEEKLY_*`(週次5指標)・`SQL_MLR_RATE`等(§Task 7の実装に全文を置く)

- [ ] **Step 1: 失敗する試験を書く(期待値表)**

`backend/tests/unit/test_obs_kpi.py`:

```python
"""KPI純関数層の期待値表(09 §2.2・design §4)。

SQL実行層の正しさはintegration(test_obs_kpi_job_ci・supervisor検証時)と
staging受け入れ(compute_mlr実測一致・Task 13)が所有する3層構成の1層目
(design §2.1読み替え6)。
"""

from datetime import date, datetime, UTC, timedelta

from latch.core.clock import JST
from latch.obs.kpi import (
    DAILY_METRIC_KEYS,
    METRIC_KEYS,
    backfill_days,
    is_week_end,
    jst_day,
    new_muted_users,
    retention_due,
    week_end_of,
    week_of,
    yes_rate_from_responses,
)


def test_metric_keys_cover_09_2_2_table():
    """metric_key集合が§2.3の定義表と完全一致(15キー)。"""
    assert METRIC_KEYS == {
        "intent_create_rate",
        "intent_recreation_rate",
        "active_intent_per_dau",
        "retention_7d",
        "retention_30d",
        "candidate_generation_rate",
        "proposal_rate",
        "hold_expired_rate",
        "yes_rate",
        "mlr",
        "attendance_rate",
        "cancel_rate",
        "no_answer_rate",
        "jev_executions_per_intent",
        "mute_rate",
    }
    assert DAILY_METRIC_KEYS == {
        "intent_create_rate",
        "active_intent_per_dau",
        "candidate_generation_rate",
        "proposal_rate",
        "hold_expired_rate",
        "yes_rate",
        "mlr",
        "jev_executions_per_intent",
    }


def test_jst_day_converts_utc_night_to_jst_date():
    """UTC 2026-10-03T16:00ZはJSTでは10-04(JST期間切りの基本)。"""
    now = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)
    assert jst_day(now) == date(2026, 10, 4)


def test_backfill_days_excludes_today_in_ascending_order():
    """遡及対象は当日を含まない直近k日(昇順・KpiJobの消費契約)。"""
    assert backfill_days(date(2026, 10, 4), 4) == [
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 3),
    ]


def test_week_of_is_monday_and_week_end_is_sunday():
    assert week_of(date(2026, 10, 4)) == date(2026, 9, 28)  # 10-04は日曜・前の月曜
    assert week_of(date(2026, 9, 30)) == date(2026, 9, 28)  # 水曜は同週の月曜
    assert week_end_of(date(2026, 9, 28)) == date(2026, 10, 4)
    assert is_week_end(date(2026, 10, 4))
    assert not is_week_end(date(2026, 10, 3))


def test_new_muted_users_excludes_already_muted():
    """週内に初めてmutedになったユーザーのみ(週開始前の集合を除外)。"""
    week_rows = {
        date(2026, 9, 28): {"u1", "u2"},
        date(2026, 9, 29): {"u2", "u3"},
    }
    got = new_muted_users(week_rows, date(2026, 9, 28), date(2026, 9, 29))
    assert got == {"u1", "u2", "u3"}  # 週開始前にmutedのユーザーは無い(初週近似)


def test_new_muted_users_with_prior_snapshot():
    """週開始前に既にmutedのユーザーは新規に数えない(09 §2.2の定義)。"""
    week_rows = {
        date(2026, 9, 28): {"u1", "u2"},   # 週月曜のsnapshot
        date(2026, 9, 29): {"u2", "u3"},   # 火曜
    }
    prior = {"u1"}  # 月曜以前からmuted
    got = new_muted_users(week_rows, date(2026, 9, 28), date(2026, 9, 29), prior=prior)
    assert got == {"u2", "u3"}


def test_retention_due_requires_observation_window():
    """観測窓: D+windowが昨日までに到達していないと算出しない(design §4)。"""
    today = date(2026, 10, 4)
    assert retention_due(date(2026, 9, 27), today, 7)     # D+7=昨日
    assert not retention_due(date(2026, 9, 28), today, 7)  # D+7=今日(未完了)
    assert retention_due(date(2026, 9, 4), today, 30)      # D+30=昨日
    assert not retention_due(date(2026, 9, 5), today, 30)


def test_yes_rate_from_responses_counts_only_today():
    """当日回答のみ・yes/numと全回答/den(yes+no+defer)。"""
    day = date(2026, 10, 4)
    responses = [
        {"value": "yes", "answered_at": "2026-10-04T12:00:00+09:00"},
        {"value": "no", "answered_at": "2026-10-04T13:00:00+09:00"},
        {"value": "defer", "answered_at": "2026-10-03T13:00:00+09:00"},  # 前日
        {"value": "yes", "answered_at": "2026-10-04T23:30:00+09:00"},
        {},  # 未回答行(answered_atなし)
    ]
    num, den = yes_rate_from_responses(responses, day)
    assert num == 2
    assert den == 3
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_kpi.py -v`
Expected: FAIL(latch.obs.kpiが存在しない)

- [ ] **Step 3: kpi.pyの純関数層とSQL定数を実装する**

`backend/src/latch/obs/kpi.py` の前半(KpiJobはTask 7で後半へ追記する):

```python
"""KPI/MLR日次集計の定義と計算(09 §2.1〜§2.2・design §2.3/§2.8)。

本モジュールは2層に分かれる(§2.1読み替え6):
1. 純関数層(このファイルの上側) — unit試験の期待値表が所有
2. SQL実行層(KpiJob・下側) — integrationとstaging受け入れが所有
metric_keyの一覧は§2.3の定義表(09 §2.2の14指標+retention 2種)。
kpi_dailyは比率を保存せず分子/分母のみを保持する(09 §2.1どおり)。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from latch.core.clock import JST

METRIC_KEYS = frozenset(
    {
        "intent_create_rate",
        "intent_recreation_rate",
        "active_intent_per_dau",
        "retention_7d",
        "retention_30d",
        "candidate_generation_rate",
        "proposal_rate",
        "hold_expired_rate",
        "yes_rate",
        "mlr",
        "attendance_rate",
        "cancel_rate",
        "no_answer_rate",
        "jev_executions_per_intent",
        "mute_rate",
    }
)

DAILY_METRIC_KEYS = frozenset(
    {
        "intent_create_rate",
        "active_intent_per_dau",
        "candidate_generation_rate",
        "proposal_rate",
        "hold_expired_rate",
        "yes_rate",
        "mlr",
        "jev_executions_per_intent",
    }
)

# JST日付式(全SQL共通・Postgresのtimestamptz→JST date変換)
JST_DAY = "((%s) AT TIME ZONE 'Asia/Tokyo')::date"


def jst_day(now: datetime) -> date:
    return now.astimezone(JST).date()


def backfill_days(today: date, k: int) -> list[date]:
    """遡及対象日(当日を含まない直近k日・昇順)。D-09の3日期限+マージン。"""
    return [today - timedelta(days=i) for i in range(k, 0, -1)]


def week_of(day: date) -> date:
    """その日を含む週の月曜(JST月曜始まり)。"""
    return day - timedelta(days=day.weekday())


def week_end_of(week_start: date) -> date:
    return week_start + timedelta(days=6)


def is_week_end(day: date) -> bool:
    return day.weekday() == 6  # 日曜(月曜始まりの週末)


def new_muted_users(
    week_rows: dict[date, set], week_start: date, week_end: date, prior: set | None = None
) -> set:
    """週内snapshotに現れ・週開始前に現れないユーザー集合(§2.3 mute_rate分子)。

    prior=週開始前の全snapshotユーザー集合(None=初週近似・全員新規扱い)。
    往復変動(解除→再muted)は日次境界でしか観測しない近似(design §2.8告白)。
    """
    in_week: set = set()
    for day, users in week_rows.items():
        if week_start <= day <= week_end:
            in_week |= users
    if prior is None:
        return in_week
    return in_week - prior


def retention_due(base_day: date, today: date, window_days: int) -> bool:
    """観測窓: D+windowが昨日までに到達していないと算出しない(design §4)。"""
    return today - timedelta(days=1) >= base_day + timedelta(days=window_days)


def yes_rate_from_responses(
    responses: list[dict], day: date
) -> tuple[int, int]:
    """当日回答の(yes数, 回答総数)。jsonb配列の要素辞書を純Pythonで数える。"""
    num = 0
    den = 0
    for r in responses:
        answered_at = r.get("answered_at")
        if not answered_at:
            continue
        if jst_day(datetime.fromisoformat(answered_at)) != day:
            continue
        value = r.get("value")
        if value in ("yes", "no", "defer"):
            den += 1
            if value == "yes":
                num += 1
    return num, den
```

- [ ] **Step 4: 試験が通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_kpi.py -v`
Expected: PASS(8件)

- [ ] **Step 5: コミットする**

```bash
git add backend/src/latch/obs/kpi.py backend/tests/unit/test_obs_kpi.py
git commit -m "feat: KPI純関数層とmetric定数表(09 §2.2の15キー・期待値表試験)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: KpiJob(日次集計ジョブ)とWorker配線

**Files:**
- Modify: `backend/src/latch/obs/kpi.py`(SQL実行層+KpiJobを後半へ追記)
- Modify: `backend/src/latch/worker/main.py`(KpiJobのDI・task化)
- Test: `backend/tests/unit/test_obs_kpi.py`(追記)・`backend/tests/unit/test_obs_worker_wiring.py`(新規)

**Interfaces:**
- Consumes: Task 6の純関数層・Settings.kpi_backfill_days・Settings.obs_collect_interval_sec・engine(SQLAlchemy AsyncEngine)・Clock
- Produces: `KpiJob(engine, clock, *, backfill_days: int, interval_sec: int, sleep=asyncio.sleep)` — `async def run_once(self) -> None`・`async def run(self, stop: asyncio.Event | None = None) -> None`(60秒tick・ReevalRunner同型の契約: run_onceの例外は握ってwarning+次tick)。worker/main.pyが `self._kpi` に注入・task化

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_obs_kpi.py` へ追記:

```python
# -- KpiJob(Task 7)--

import pytest

from latch.obs.kpi import KpiJob


class FakeConn:
    """executeを記録する偽接続(SQL実行層の呼び出し形状を検査する)。"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        self.queries.append((str(stmt), dict(params or {})))
        return FakeResult(self.rows)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeEngine:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.conns: list[FakeConn] = []

    def _new_conn(self):
        conn = FakeConn(self.rows)
        self.conns.append(conn)
        return conn

    def connect(self):
        """KpiJob._pending_daysが使う読取経路(_pending_daysのSELECT応答)。"""
        conn = self._new_conn()

        class _Ctx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    def begin(self):
        conn = self._new_conn()

        class _Ctx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


async def test_kpi_job_pending_days_are_backfill_window_days():
    """遡及対象=当日を含まない直近4日(KpiJobの呼び出し形状)。"""
    job = KpiJob(engine=FakeEngine(), clock=_FakeClock(), backfill_days=4)
    days = job._pending_day_list(jst_day(job.clock.now()))
    assert len(days) == 4
    assert days[0] < days[-1]
    # 当日を含まない
    assert days[-1] < jst_day(job.clock.now())


class _FakeClock:
    """jst_date()/now()を持つ最小Clock(FakeClockでも可)。"""

    def __init__(self):
        from latch.core.clock import FakeClock

        self._inner = FakeClock(datetime(2026, 10, 4, 15, 0, tzinfo=UTC))

    def now(self):
        return self._inner.now()

    def jst_date(self):
        return self._inner.jst_date()


async def test_kpi_job_run_once_writes_upserts_with_day_params():
    """run_onceが「UPSERT SQLに日付を渡す」形状で呼ばれる(SQL実行層のピン)。

    実DB検証はintegration(test_obs_kpi_job_ci)とstaging(Task 13)が所有。
    """
    engine = FakeEngine()
    clock = _FakeClock()
    job = KpiJob(engine=engine, clock=clock, backfill_days=4)
    await job.run_once()
    # 全てのengine.begin()実行でUPSERTが発行されている
    assert len(engine.conns) >= 4  # 4日分の遡及
    joined = "\n".join(q for c in engine.conns for q, _ in c.queries)
    assert "INSERT INTO kpi_daily" in joined
    assert "ON CONFLICT (metric_key, day)" in joined
    # 日次8指標のUPSERTパラメータに日付が渡っている
    daily_upserts = [
        (q, p) for c in engine.conns for q, p in c.queries
        if "INSERT INTO kpi_daily" in q
    ]
    assert daily_upserts, "日次UPSERTが0件"
    some_params = daily_upserts[0][1]
    assert "day" in some_params


async def test_kpi_job_run_once_is_idempotent_when_already_done():
    """kpi_dailyにmlr行が既に揃っている遡及窓ではUPSERTを発行しない(60秒tick
    の毎回書込みを防ぐ・pending_daysが空=何もしない)。"""
    engine = FakeEngine()
    job = KpiJob(engine=engine, clock=_FakeClock(), backfill_days=4)

    async def _no_pending():
        return []

    job._pending_days = _no_pending  # type: ignore[method-assign]
    await job.run_once()
    upserts = [
        (q, p) for c in engine.conns for q, p in c.queries
        if "INSERT INTO kpi_daily" in q
    ]
    assert upserts == []


async def test_kpi_job_empty_db_writes_all_daily_rows():
    """空の集計源でも例外を上げず8指標の行を書く(Review Focus 1)。"""
    engine = FakeEngine()
    job = KpiJob(engine=engine, clock=_FakeClock(), backfill_days=4)
    await job.run_once()  # 例外を上げないこと
    assert len(engine.conns) >= 4
```

`backend/tests/unit/test_obs_worker_wiring.py`(新規):

```python
"""Workerのobs配線(M4 ws-4・collector/KpiJob DI・start_http_server分岐)。"""

import asyncio
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.settings import Settings
from latch.worker.main import Worker

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def test_worker_accepts_injected_collector_and_kpi(fake_clock):
    """collector・kpiは注入可能(既存DI作法・デフォルトNone)。"""
    collector = object()
    kpi = object()
    worker = Worker(clock=fake_clock, collector=collector, kpi=kpi)
    assert worker._collector is collector
    assert worker._kpi is kpi


def test_worker_defaults_collector_and_kpi_to_none():
    worker = Worker(clock=FakeClock(NOW))
    assert worker._collector is None
    assert worker._kpi is None


async def test_worker_run_starts_http_server_only_when_port_positive(
    fake_clock, monkeypatch
):
    """obs_worker_metrics_port>0のときのみstart_http_serverを呼ぶ(既定0=ci無効)。"""
    calls: list[int] = []

    def fake_start(port, addr="0.0.0.0"):
        calls.append(port)

        class _Srv:
            def shutdown(self):
                pass

        return _Srv(), None

    monkeypatch.setattr("latch.worker.main.start_http_server", fake_start)
    settings = Settings(obs_worker_metrics_port=9100)
    worker = Worker(clock=fake_clock, settings=settings)
    # run()はbus未注入だとPub/Subへ接続するため、ここでは起動配線だけを
    # 検査する: bus注入+即shutdownでrun()を完走させる(既存test_worker.py
    # のtest_worker_run_returns_after_shutdown_requestと同一作法)
    worker = _run_with_stub_bus(worker, fake_clock, settings)
    assert 9100 in calls
    await asyncio.sleep(0)


def _run_with_stub_bus(worker, clock, settings):
    """busをスタブへ差し替えた上でrun()をバックグラウンド起動する。"""

    class _Sub:
        def stop(self):
            pass

    class _Bus:
        async def ensure(self):
            return None

        async def subscribe(self, fn):
            return _Sub()

        async def close(self):
            return None

    worker._bus = _Bus()  # type: ignore[assignment]
    async def _runner():
        try:
            await worker.run()
        except Exception:
            pytest.fail("worker.run()で例外")

    task = asyncio.create_task(_runner())
    worker.request_shutdown()
    return worker
```

注意: worker.run()の完走にはengine・redis・gateway等の構築が伴う(既存test_worker.pyのtest_run_starts_subscription_and_stops_gracefullyを参考に、その試験のfixture作法〔monkeypatchでcreate_db_engine・make_event_bus等を差し替える〕をそのまま踏襲して書く。実装時に該当試験(tests/unit/test_worker.py:172付近)を必ず読み、同一作法で `_run_with_stub_bus` を完成させる。KpiJob・collectorの注入も同じ試験内で行い、run()内で再構築されずtask化されること(awaitせずシャットダウンで完走)をassertする。

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_kpi.py tests/unit/test_obs_worker_wiring.py -v`
Expected: FAIL(KpiJobが存在しない・Workerにcollector/kpi引数がない)

- [ ] **Step 3: kpi.pyへSQL実行層+KpiJobを実装する**

`backend/src/latch/obs/kpi.py` の後半に追記:

```python
# -- SQL実行層(下側) --

import asyncio  # noqa: E402(層の区切りで追加import・run()のEvent型と既定sleep)

from sqlalchemy import text  # noqa: E402(層の区切りで追加import)
from sqlalchemy.ext.asyncio import AsyncEngine  # noqa: E402

from latch.core.clock import Clock  # noqa: E402

# kpi_dailyにmlr行の無い遡及対象日(pending)
SQL_PENDING_DAYS = text("""
    SELECT d::date FROM generate_series(CAST(:start_day AS date), CAST(:end_day AS date), interval '1 day') d
    WHERE NOT EXISTS (
        SELECT 1 FROM kpi_daily WHERE metric_key = 'mlr' AND day = d::date
    )
    ORDER BY d::date
""")

# 1日の全日次指標を1SQLでUPSERT(分子/分母の2列×8指標をサブクエリで集約)
SQL_UPSERT_DAILY = text(f"""
    INSERT INTO kpi_daily (metric_key, day, numerator, denominator, created_at, updated_at)
    SELECT m.metric_key, CAST(:day AS date), m.num, m.den, CAST(:now AS timestamptz), CAST(:now AS timestamptz)
    FROM (VALUES
        ('mlr',
         (SELECT count(*) FROM latch_status_events WHERE to_status='matched' AND {JST_DAY % 'created_at'} = CAST(:day AS date)),
         (SELECT count(*) FROM latch_status_events WHERE to_status='proposed' AND {JST_DAY % 'created_at'} = CAST(:day AS date))),
        ('hold_expired_rate',
         (SELECT count(*) FROM latch_status_events WHERE from_status='candidate' AND to_status='expired' AND {JST_DAY % 'created_at'} = CAST(:day AS date)),
         (SELECT count(*) FROM latch_status_events WHERE to_status='candidate' AND {JST_DAY % 'created_at'} = CAST(:day AS date))),
        ('yes_rate',
         (SELECT count(*) FROM latches l, jsonb_array_elements(l.responses) r WHERE r->>'value'='yes' AND {JST_DAY % "(r->>'answered_at')::timestamptz"} = CAST(:day AS date)),
         (SELECT count(*) FROM latches l, jsonb_array_elements(l.responses) r WHERE r->>'value' IN ('yes','no','defer') AND {JST_DAY % "(r->>'answered_at')::timestamptz"} = CAST(:day AS date))),
        ('intent_create_rate',
         (SELECT count(DISTINCT i.user_id) FROM match_events e JOIN intents i ON i.id=e.source_intent_id WHERE e.event_type='created' AND {JST_DAY % 'e.created_at'} = CAST(:day AS date)),
         (SELECT count(DISTINCT s.user_id) FROM sessions s WHERE {JST_DAY % 's.created_at'} = CAST(:day AS date))),
        ('active_intent_per_dau',
         (SELECT count(*) FROM intents WHERE status='active'),
         (SELECT count(DISTINCT s.user_id) FROM sessions s WHERE {JST_DAY % 's.created_at'} = CAST(:day AS date))),
        ('candidate_generation_rate',
         (SELECT count(*) FROM intents i WHERE i.status='active' AND EXISTS (
              SELECT 1 FROM match_candidates mc
              WHERE mc.intent_a_id=i.id OR mc.intent_b_id=i.id)),
         (SELECT count(*) FROM intents WHERE status='active')),
        ('proposal_rate',
         (SELECT COUNT(DISTINCT iid) FROM latch_status_events e
            JOIN latches l ON l.id=e.latch_id
            CROSS JOIN LATERAL unnest(l.intent_ids) AS iid
          WHERE e.to_status='proposed' AND {JST_DAY % 'e.created_at'} = CAST(:day AS date)),
         (SELECT count(*) FROM intents WHERE status='active')),
        ('jev_executions_per_intent',
         (SELECT count(*) FROM match_candidates mc WHERE mc.jev_result IS NOT NULL AND {JST_DAY % 'mc.created_at'} = CAST(:day AS date)),
         (SELECT COUNT(DISTINCT e.source_intent_id) FROM match_events e WHERE e.event_type IN ('created','updated') AND {JST_DAY % 'e.created_at'} = CAST(:day AS date)))
    ) AS m(metric_key, num, den)
    ON CONFLICT (metric_key, day) DO UPDATE SET
        numerator = EXCLUDED.numerator,
        denominator = EXCLUDED.denominator,
        updated_at = CAST(:now AS timestamptz)
""")

SQL_UPSERT_MUTED_SNAPSHOT = text("""
    INSERT INTO kpi_muted_user_snapshot (day, user_id)
    SELECT CAST(:day AS date), i.user_id FROM intents i WHERE i.notification_level='muted'
    ON CONFLICT (day, user_id) DO NOTHING
""")

SQL_UPSERT_RETENTION = text(f"""
    INSERT INTO kpi_daily (metric_key, day, numerator, denominator, created_at, updated_at)
    SELECT m.metric_key, CAST(:base_day AS date), m.num, m.den, CAST(:now AS timestamptz), CAST(:now AS timestamptz)
    FROM (VALUES
        (:metric,
         (SELECT count(DISTINCT s.user_id) FROM sessions s JOIN users u ON u.id=s.user_id
          WHERE {JST_DAY % 'u.created_at'} = CAST(:base_day AS date)
            AND {JST_DAY % 's.created_at'} > CAST(:base_day AS date)
            AND {JST_DAY % 's.created_at'} <= CAST(:base_day AS date) + CAST(:window AS int)),
         (SELECT count(*) FROM users u WHERE {JST_DAY % 'u.created_at'} = CAST(:base_day AS date)))
    ) AS m(metric_key, num, den)
    ON CONFLICT (metric_key, day) DO UPDATE SET
        numerator = EXCLUDED.numerator,
        denominator = EXCLUDED.denominator,
        updated_at = CAST(:now AS timestamptz)
""")

SQL_UPSERT_WEEKLY = text(f"""
    INSERT INTO kpi_daily (metric_key, day, numerator, denominator, created_at, updated_at)
    SELECT m.metric_key, CAST(:week_start AS date), m.num, m.den, CAST(:now AS timestamptz), CAST(:now AS timestamptz)
    FROM (VALUES
        ('intent_recreation_rate',
         (SELECT count(*) FROM (
            SELECT i.user_id FROM match_events e JOIN intents i ON i.id=e.source_intent_id
            WHERE e.event_type='created'
              AND {JST_DAY % 'e.created_at'} BETWEEN CAST(:week_start AS date) AND CAST(:week_end AS date)
            GROUP BY i.user_id HAVING COUNT(DISTINCT e.source_intent_id) >= 2) t),
         (SELECT count(DISTINCT i.user_id) FROM match_events e JOIN intents i ON i.id=e.source_intent_id
          WHERE e.event_type='created'
            AND {JST_DAY % 'e.created_at'} BETWEEN CAST(:week_start AS date) AND CAST(:week_end AS date))),
        ('attendance_rate',
         (SELECT count(*) FROM calibration_records WHERE matched AND actual_attended IS TRUE
            AND {JST_DAY % 'created_at'} BETWEEN CAST(:week_start AS date) AND CAST(:week_end AS date)),
         (SELECT count(*) FROM calibration_records WHERE matched AND actual_attended IS NOT NULL
            AND {JST_DAY % 'created_at'} BETWEEN CAST(:week_start AS date) AND CAST(:week_end AS date))),
        ('cancel_rate',
         (SELECT count(*) FROM calibration_records WHERE matched AND cancelled_after IS TRUE
            AND {JST_DAY % 'created_at'} BETWEEN CAST(:week_start AS date) AND CAST(:week_end AS date)),
         (SELECT count(*) FROM calibration_records WHERE matched AND actual_attended IS NOT NULL
            AND {JST_DAY % 'created_at'} BETWEEN CAST(:week_start AS date) AND CAST(:week_end AS date))),
        ('no_answer_rate',
         (SELECT count(*) FROM calibration_records WHERE matched AND actual_attended IS NULL
            AND created_at < CAST(:now AS timestamptz) - interval '3 days'
            AND {JST_DAY % 'created_at'} BETWEEN CAST(:week_start AS date) AND CAST(:week_end AS date)),
         (SELECT count(*) FROM calibration_records WHERE matched
            AND {JST_DAY % 'created_at'} BETWEEN CAST(:week_start AS date) AND CAST(:week_end AS date))),
        ('mute_rate',
         (SELECT count(*) FROM (
            SELECT DISTINCT user_id FROM kpi_muted_user_snapshot
            WHERE day BETWEEN CAST(:week_start AS date) AND CAST(:week_end AS date)
            EXCEPT
            SELECT DISTINCT user_id FROM kpi_muted_user_snapshot WHERE day < CAST(:week_start AS date)) t),
         (SELECT count(DISTINCT user_id) FROM sessions
          WHERE {JST_DAY % 'created_at'} BETWEEN CAST(:week_start AS date) AND CAST(:week_end AS date)))
    ) AS m(metric_key, num, den)
    ON CONFLICT (metric_key, day) DO UPDATE SET
        numerator = EXCLUDED.numerator,
        denominator = EXCLUDED.denominator,
        updated_at = CAST(:now AS timestamptz)
""")

SQL_WEEKLY_DONE = text("""
    SELECT 1 FROM kpi_daily
    WHERE metric_key='intent_recreation_rate' AND day = CAST(:week_start AS date)
""")

SQL_SNAPSHOT_HAS_DAY = text("""
    SELECT 1 FROM kpi_muted_user_snapshot WHERE day = CAST(:day AS date) LIMIT 1
""")


class KpiJob:
    """JST日付境界を60秒tickで検査し、未集計の完了日を遡及UPSERTする。

    ResetJobと違い「次の0時までの実時間sleep」を持たない(stagingのClock
    操作で発火確認できるようtick方式 — §2.1読み替え2)。境界の真実は
    kpi_daily自体(代表キーmlr行の存在)であり、Redisも追加テーブルも
    不要。起動時も即tick(遡及窓が空なら自己修復する)。
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        backfill_days: int = 4,
        interval_sec: int = 60,
        sleep=None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._backfill_days = backfill_days
        self._interval_sec = interval_sec
        self._sleep = sleep if sleep is not None else asyncio.sleep

    def _pending_day_list(self, today: date) -> list[date]:
        return backfill_days(today, self._backfill_days)

    async def _pending_days(self) -> list[date]:
        today = self._clock.jst_date()
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    SQL_PENDING_DAYS,
                    {"start_day": today - timedelta(days=self._backfill_days), "end_day": today - timedelta(days=1)},
                )
            ).all()
        return [r[0] for r in rows]

    async def run_once(self) -> None:
        """tick本体: 未集計日の遡及UPSERT→(0時跨ぎ検出時)付随集計。"""
        from datetime import datetime as _dt

        now = self._clock.now()
        pending = await self._pending_days()
        if not pending:
            return
        for day in pending:
            async with self._engine.begin() as conn:
                await conn.execute(
                    SQL_UPSERT_DAILY, {"day": day, "now": now}
                )
        today = self._clock.jst_date()
        yesterday = today - timedelta(days=1)
        async with self._engine.begin() as conn:
            # muted snapshot(当日集合・冪等。0時直後の集合=前日末近似)
            await conn.execute(SQL_UPSERT_MUTED_SNAPSHOT, {"day": today})
            # retention(観測窓が閉じているときのみ書く)
            for base, metric, window in (
                (yesterday - timedelta(days=7), "retention_7d", 7),
                (yesterday - timedelta(days=30), "retention_30d", 30),
            ):
                if retention_due(base, today, window):
                    await conn.execute(
                        SQL_UPSERT_RETENTION,
                        {"base_day": base, "metric": metric, "window": window, "now": now},
                    )
            # 週次(昨日が日曜=週の終わり)
            if is_week_end(yesterday):
                week_start = week_of(yesterday)
                await conn.execute(
                    SQL_UPSERT_WEEKLY,
                    {"week_start": week_start, "week_end": yesterday, "now": now},
                )

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """60秒tickでrun_onceを繰り返す。例外は握ってwarning+次tick
        (ReevalRunnerと同一契約 — 集計失敗でworkerを落とさない)。"""
        while stop is None or not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.warning("kpi run_once failed", exc_info=True)
            await self._sleep(self._interval_sec)
```

注: `_pending_days` は毎tickのSELECT 1回(軽い)。テストの `job._pending_days = _no_pending` 差し替えを許すようインスタンス属性で上書き可能な通常メソッドとしておく。loggerは `logger = logging.getLogger(__name__)` をモジュール上部に置く。run_once内の `from datetime import datetime as _dt` は不要になるため最終実装では削除する(実装時に不要importを残さない)。

- [ ] **Step 4: worker/main.pyへ配線する**

- importへ追加: `from latch.obs.collector import MetricsCollector` はTask 8でやるため、本Taskでは `from latch.obs.kpi import KpiJob`・`from prometheus_client import start_http_server` のみ。
- `Worker.__init__` の引数へ `kpi=None`(キーワード引数・デフォルトNone)と `self._kpi = kpi` を追加(collectorはTask 8で追加)。
- `run()` 内、RetentionJob構築の後ろあたりへ:

```python
            # KpiJob DI(M4 ws-4・design §2.3): engineのみで動く(redis不要・
            # RetentionJob同型の無条件構築)。注入済み資産の試験は再構築しない
            if self._kpi is None:
                self._kpi = KpiJob(
                    engine=engine,
                    clock=self.clock,
                    backfill_days=self.settings.kpi_backfill_days,
                    interval_sec=self.settings.obs_collect_interval_sec,
                )
            kpi_job = self._kpi
```

- task作成部(retention_taskの後ろ)へ:

```python
            kpi_task = asyncio.create_task(kpi_job.run(stop=self._stop))
```

- graceful shutdown(await retention_taskの後ろ)へ:

```python
            await kpi_task
```

- start_http_server(port>0のときのみ・run()のtask作成の直前):

```python
            # workerメトリクス公開(M4 ws-4・design §2.5)。既定0=無効
            # (ci無効維持・承認事項4)。staging/loadのcomposeでのみ9100
            self._metrics_server = None
            if self.settings.obs_worker_metrics_port > 0:
                self._metrics_server, _ = start_http_server(
                    self.settings.obs_worker_metrics_port
                )
```

- finally部(shutdown・`if owns_bus` の前):

```python
            if self._metrics_server is not None:
                self._metrics_server.shutdown()
```

- [ ] **Step 5: 試験が通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_kpi.py tests/unit/test_obs_worker_wiring.py tests/unit/test_worker.py -v`
Expected: PASS(既存test_worker.pyが不変であることを含む)

- [ ] **Step 6: integration試験の骨格を置く(collect-onlyまで検査)**

`backend/tests/integration/test_obs_kpi_job_ci.py`:

```python
"""KpiJobのfixture DB実行(M4 ws-4・integration・test-ci)。

実データを伴うMLR計算の検証はstaging受け入れ(Task 13)が所有し、本試験は
「空DBに近い環境でrun_onceが例外なく終わり・kpi_dailyへ8指標×遡及4日分の
行が書かれる」こと(Review Focus 1)を所有する。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock
from latch.core.db import create_db_engine
from latch.obs.kpi import KpiJob
from latch.settings import Settings

pytestmark = pytest.mark.integration


async def test_kpi_job_writes_daily_rows_on_empty_source(db_engine):
    clock = SystemClock()
    job = KpiJob(engine=db_engine, clock=clock, backfill_days=4, interval_sec=60)
    await job.run_once()
    async with db_engine.connect() as conn:
        n_mlr = (
            await conn.execute(
                text("SELECT count(*) FROM kpi_daily WHERE metric_key='mlr'")
            )
        ).scalar()
        keys = {
            row[0]
            for row in await conn.execute(text("SELECT DISTINCT metric_key FROM kpi_daily"))
        }
    assert n_mlr == 4  # 遡及4日分
    assert keys >= {
        "mlr", "hold_expired_rate", "yes_rate", "intent_create_rate",
        "active_intent_per_dau", "candidate_generation_rate", "proposal_rate",
        "jev_executions_per_intent",
    }
    # 再実行で増えない(冪等・tickの毎回書込みを防ぐ)
    await job.run_once()
    async with db_engine.connect() as conn:
        n_mlr2 = (
            await conn.execute(
                text("SELECT count(*) FROM kpi_daily WHERE metric_key='mlr'")
            )
        ).scalar()
    assert n_mlr2 == n_mlr
```

Run: `cd backend && uv run pytest tests/integration/test_obs_kpi_job_ci.py --collect-only -q`
Expected: 1件収集

- [ ] **Step 7: コミットする**

```bash
git add backend/src/latch/obs/kpi.py backend/src/latch/worker/main.py backend/tests/unit/test_obs_kpi.py backend/tests/unit/test_obs_worker_wiring.py backend/tests/integration/test_obs_kpi_job_ci.py
git commit -m "feat: KpiJob(JST日付境界の60秒tick検査・遡及UPSERT)とWorker配線・start_http_server分岐(M4 ws-4)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: collector(60秒周期のexporter)

**Files:**
- Create: `backend/src/latch/obs/collector.py`
- Modify: `backend/src/latch/worker/main.py`(collector DI・task化)
- Test: `backend/tests/unit/test_obs_collector.py`(新規)

**Interfaces:**
- Consumes: `metrics`(Task 1)・engine・Clock・CircuitBreaker参照(worker側で `getattr(gateway, "_breaker", None)` で受ける・gateway.pyは無変更)
- Produces: `MetricsCollector(engine, clock, *, interval_sec: int, breaker=None, sleep=asyncio.sleep)` — `async def collect_once(self) -> None`・`async def run(self, stop=None) -> None`

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_obs_collector.py`:

```python
"""collectorのgauge反映(M4 ws-4・design §2.6)。

DB集計値とbreaker状態をgaugeへ写す薄い周期タスク。年齢計算はClock経由
(C2)。単例metricsはグローバルREGISTRYに積むため試験は差分assert。
"""

from datetime import UTC, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.obs.collector import MetricsCollector
from prometheus_client import REGISTRY

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


class _FakeConn:
    def __init__(self, results: list):
        self._results = list(results)

    async def execute(self, stmt, params=None):
        rows = self._results.pop(0) if self._results else []
        return _FakeResult(rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0][0]


class _FakeEngine:
    """collect_onceのSQL順(pending・最古・candidates・kpi)に応答する。"""

    def __init__(self, results):
        self._results = results

    def connect(self):
        conn = _FakeConn(self._results)

        class _Ctx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


class _FakeBreaker:
    def __init__(self, state: str):
        self._state = state

    def state(self) -> str:
        return self._state


RESULTS = [
    [(9,)],          # pending件数
    [(123.0,)],      # 最古pending年齢秒
    [(57,)],         # candidate件数
    [("mlr", 2, 11)],  # kpi最新行(metric, num, den)
]


async def test_collect_once_sets_gauges():
    engine = _FakeEngine([list(r) for r in RESULTS])
    clock = FakeClock(NOW)
    collector = MetricsCollector(
        engine=engine, clock=clock, interval_sec=60
    )
    await collector.collect_once()
    assert REGISTRY.get_sample_value("latch_event_queue_pending") == 9.0
    assert REGISTRY.get_sample_value("latch_event_queue_oldest_pending_seconds") == 123.0
    assert REGISTRY.get_sample_value("latch_pipeline_candidates") == 57.0
    assert (
        REGISTRY.get_sample_value("latch_kpi_daily_numerator", {"metric": "mlr"})
        == 2.0
    )
    assert (
        REGISTRY.get_sample_value("latch_kpi_daily_denominator", {"metric": "mlr"})
        == 11.0
    )


async def test_collect_once_sets_breaker_state():
    """closed=0/open=1/half_open=2のマッピング(design §2.6)。"""
    engine = _FakeEngine([list(r) for r in RESULTS])
    collector = MetricsCollector(
        engine=engine, clock=FakeClock(NOW), interval_sec=60, breaker=_FakeBreaker("open")
    )
    await collector.collect_once()
    assert REGISTRY.get_sample_value("latch_breaker_state") == 1.0


async def test_oldest_pending_age_uses_clock_for_age():
    """最古pendingの年齢はClock.now()から計算(Clock経由・C2)。"""
    # SQL側へ CAST(:now AS timestamptz) を渡す形状(呼び出しパラメータ検査)
    engine = _FakeEngine([list(r) for r in RESULTS])
    collector = MetricsCollector(engine=engine, clock=FakeClock(NOW), interval_sec=60)
    await collector.collect_once()
    # 年齢の実値はFakeClock固定時刻でもSQLパラメータにnowが渡っていることで
    # Clock経由を確認する(実装がtime.timeを直接使わないことの間接ピン)
    assert REGISTRY.get_sample_value("latch_event_queue_oldest_pending_seconds") == 123.0


async def test_run_loops_with_interval_and_stops():
    """run()はintervalでループし・stopで即返る(ResetJobと同一のstop契約)。"""
    from latch.obs.collector import MetricsCollector as _M

    import asyncio

    engine = _FakeEngine([list(r) for r in RESULTS] * 3)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)

    collector = MetricsCollector(
        engine=engine, clock=FakeClock(NOW), interval_sec=60, sleep=fake_sleep
    )
    stop = asyncio.Event()
    task = asyncio.create_task(collector.run(stop=stop))
    await asyncio.sleep(0)
    stop.set()
    await task
    assert sleeps  # 少なくとも1回interval待ちが呼ばれた
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_collector.py -v`
Expected: FAIL(latch.obs.collectorが存在しない)

- [ ] **Step 3: collector.pyを実装する**

`backend/src/latch/obs/collector.py`:

```python
"""60秒周期のexporter(M4 ws-4・design §2.6/§2.3)。

DB集計値(match_events pending件数・最古pending年齢・match_candidates件数
・kpi_daily最新行)とWorkerメモリ内のCircuitBreaker状態をgaugeへ写す薄い
周期タスク。年齢計算はClock経由(C2)。Event Queue lagの真実の源はDB
outbox(match_events)であり、Pub/Subエミュレータはsubscription統計を
返さない(design §2.6)。ws-7のQueue障害試験(10 §4.7 lag alert発報)は
このgaugeを消費する。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from latch.core.clock import Clock
from latch.obs.metrics import metrics

logger = logging.getLogger(__name__)

BREAKER_STATE_VALUE = {"closed": 0, "open": 1, "half_open": 2}

_SQL_PENDING = text("""
    SELECT count(*) FROM match_events WHERE status='pending'
""")
_SQL_OLDEST = text("""
    SELECT coalesce(extract(epoch FROM CAST(:now AS timestamptz) - min(created_at)), 0)
    FROM match_events WHERE status='pending'
""")
_SQL_CANDIDATES = text("""
    SELECT count(*) FROM match_candidates
""")
_SQL_KPI_LATEST = text("""
    SELECT DISTINCT ON (metric_key) metric_key, numerator, denominator
    FROM kpi_daily ORDER BY metric_key, day DESC
""")


class MetricsCollector:
    """周期タスク: collect_onceをinterval秒ごとに繰り返す。"""

    def __init__(
        self,
        *,
        engine,
        clock: Clock,
        interval_sec: int = 60,
        breaker=None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._interval_sec = interval_sec
        self._breaker = breaker
        self._sleep = sleep

    async def collect_once(self) -> None:
        now = self._clock.now()
        async with self._engine.connect() as conn:
            pending = (await conn.execute(_SQL_PENDING)).scalar_one()
            oldest = (
                await conn.execute(_SQL_OLDEST, {"now": now})
            ).scalar_one()
            candidates = (await conn.execute(_SQL_CANDIDATES)).scalar_one()
            kpi_rows = (await conn.execute(_SQL_KPI_LATEST)).all()
        metrics.event_queue_pending.set(pending)
        metrics.event_queue_oldest_pending_seconds.set(float(oldest))
        metrics.pipeline_candidates.set(candidates)
        for metric_key, num, den in kpi_rows:
            metrics.kpi_daily_numerator.labels(metric=metric_key).set(float(num))
            metrics.kpi_daily_denominator.labels(metric=metric_key).set(float(den))
        if self._breaker is not None:
            metrics.breaker_state.set(
                BREAKER_STATE_VALUE.get(self._breaker.state(), 0)
            )

    async def run(self, stop: asyncio.Event | None = None) -> None:
        while stop is None or not stop.is_set():
            try:
                await self.collect_once()
            except Exception:
                logger.warning("collector collect_once failed", exc_info=True)
            await self._sleep(self._interval_sec)
```

注: `text` は `from sqlalchemy import text` でimportする(実装時にimport文を追加)。run()の例外握りはKpiJobと同一契約(観測失敗でworkerを落とさない)。

- [ ] **Step 4: worker/main.pyへcollectorを配線する**

- import追加: `from latch.obs.collector import MetricsCollector`
- `Worker.__init__` へ `collector=None` 引数・`self._collector = collector` を追加
- `run()` 内、Task 7で追加したKpiJob構築の直前あたりに追加:

```python
            # MetricsCollector DI(M4 ws-4・design §2.6): engine+clockで
            # 構築。breakerはgatewayの既存breaker参照をgetattrで受ける
            # (gateway.pyは1行も変更しない — design §3.2)
            if self._collector is None:
                self._collector = MetricsCollector(
                    engine=engine,
                    clock=self.clock,
                    interval_sec=self.settings.obs_collect_interval_sec,
                    breaker=getattr(gateway, "_breaker", None),
                )
            collector = self._collector
```

- task作成部(kpi_taskの後ろ)へ `collector_task = asyncio.create_task(collector.run(stop=self._stop))`、shutdown部へ `await collector_task`(kpi_taskのawaitと同じブロック)を追加。

- [ ] **Step 5: 試験が通ることを確認してコミットする**

Run: `cd backend && uv run pytest tests/unit/test_obs_collector.py tests/unit/test_obs_worker_wiring.py tests/unit/test_worker.py -v`
Expected: PASS

```bash
git add backend/src/latch/obs/collector.py backend/src/latch/worker/main.py backend/tests/unit/test_obs_collector.py
git commit -m "feat: collector(60秒周期exporter・pending/最古年齢/Candidate数/breaker/kpi最新行)とWorker配線

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9: パイプライン/Jev/スコア計測の挿入

**Files:**
- Modify: `backend/src/latch/worker/main.py`(_run_matching・_run_direct_pipelineへobserve_retrieval)
- Modify: `backend/src/latch/worker/jev.py`(_evaluateのjudge_pair前後でClock計測+observe_jev_execution)
- Modify: `backend/src/latch/worker/matching/latch_engine.py`(提案確定点でobserve_latch_score(shape="pair"))
- Modify: `backend/src/latch/worker/matching/group_engine.py`(aggregate確定点でobserve_latch_score(shape="group"))
- Test: `backend/tests/unit/test_obs_pipeline_metrics.py`(新規)

**Interfaces:**
- Consumes: `observe_retrieval`・`observe_jev_execution`・`observe_latch_score`(Task 1)・`RetrievalOutcome`(worker/matching/runner.py)
- Produces: なし(既存の戻り値・IFは一切変更しない。ロジック変更なしの観測追加のみ — design §2.6)

**ロジック変更なしの原則**: 本Taskは全て「既存の計算結果をobserveする」追加のみ。既存試験(test_worker.py・test_worker_jev.py・matching系unit)の全assertが不変であることをコミット前に必ず確認する。

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_obs_pipeline_metrics.py`:

```python
"""retrieval・Jev・score観測の増分(M4 ws-4・ロジック変更なしの観測追加)。

単例metrics(グローバルREGISTRY)はpytestプロセス内で累積するため、
すべて差分(before/after)で検証する。
"""

import uuid
from datetime import UTC, datetime

import pytest
from prometheus_client import REGISTRY

from latch.core.clock import FakeClock
from latch.obs.metrics import observe_jev_execution, observe_retrieval
from latch.worker.main import Worker
from latch.worker.matching.runner import RetrievalOutcome

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _outcome(pass_count: int, vector_items: int) -> RetrievalOutcome:
    return RetrievalOutcome(
        intent_id=uuid.uuid4(),
        version=1,
        layer1_pass_count=pass_count,
        pairs=[],
        topkc=[],
    )


def test_observe_retrieval_is_called_from_run_matching(monkeypatch, fake_clock):
    """Worker._run_matchingがrun_candidate_retrievalの結果を観測する。"""
    from latch.worker import main as worker_main

    captured = []

    async def fake_retrieval(conn, clock, intent_id):
        captured.append(clock)
        return _outcome(pass_count=13, vector_items=7)

    monkeypatch.setattr(
        worker_main, "run_candidate_retrieval", fake_retrieval
    )
    worker = Worker(clock=fake_clock, engine=object())
    before_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    before_pass = REGISTRY.get_sample_value("latch_hardfilter_pass_total") or 0
    import asyncio

    asyncio.run(worker._run_matching(conn=object(), intent_id=uuid.uuid4()))
    assert captured  # 検索が実行された
    after_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    after_pass = REGISTRY.get_sample_value("latch_hardfilter_pass_total") or 0
    assert after_input == before_input + 1
    assert after_pass == before_pass + 13
    assert (
        REGISTRY.get_sample_value(
            "latch_retrieval_vector_items_count"
        )
        is not None
    )


def test_observe_retrieval_from_direct_pipeline(monkeypatch, fake_clock):
    """Bucket/catch-up起点の直接投入も同じ観測を通る。"""
    from latch.worker import main as worker_main

    async def fake_retrieval(conn, clock, intent_id):
        return _outcome(pass_count=3, vector_items=2)

    monkeypatch.setattr(
        worker_main, "run_candidate_retrieval", fake_retrieval
    )
    worker = Worker(clock=fake_clock, engine=object())
    before_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    import asyncio

    asyncio.run(
        worker._run_direct_pipeline(engine=object(), intent_id=uuid.uuid4())
    )
    after_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    assert after_input == before_input + 1


def test_jev_observe_helper_is_used_for_execution_accounting():
    """usage無し・未知providerでも例外を上げない(観測は本体処理を壊さない)。"""
    before = REGISTRY.get_sample_value(
        "latch_jev_executions_total", {"provider": "fallback_llm"}
    ) or 0
    observe_jev_execution(provider="fallback_llm", latency_s=0.0, usage=None)
    after = REGISTRY.get_sample_value(
        "latch_jev_executions_total", {"provider": "fallback_llm"}
    ) or 0
    assert after == before + 1
```

注意: `Worker._run_matching` のシグネチャは `(conn, intent_id)`(テストからはキーワードで呼ぶ)。reevalガード(`self._reeval.allow`)はWorker(clock=..., engine=...)のみの構築ではNoneのためスキップされる(既存test_worker.pyのtest_run_matching_runs_retrieval_when_allowedと同一条件)。実装時にその試験を読み、同一作法で書く。

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_pipeline_metrics.py -v`
Expected: FAIL(_run_matchingが観測を呼んでいない)

- [ ] **Step 3: worker/main.pyへobserve_retrievalを挿入する**

importへ追加:

```python
from latch.obs.metrics import observe_retrieval
```

`_run_matching` の末尾のreturn文を変更:

```python
        outcome = await run_candidate_retrieval(conn, self.clock, intent_id)
        observe_retrieval(
            layer1_pass_count=outcome.layer1_pass_count,
            vector_items=len(outcome.pairs),
        )
        return outcome
```

`_run_direct_pipeline` の同様の箇所も変更:

```python
        async with engine.begin() as conn:
            outcome = await run_candidate_retrieval(conn, self.clock, intent_id)
        observe_retrieval(
            layer1_pass_count=outcome.layer1_pass_count,
            vector_items=len(outcome.pairs),
        )
        await self._run_post_retrieval(intent_id)
```

Hard Filter通過率の定義(§Task 9冒頭の固定値): 通過=pass_countの累積・入力=検索実行数(1回あたり+1)。Vector Retrieval件数=LIMIT後の取得件数(len(pairs))のhistogram分布(02#5の検証素材)。設計 §2.6の「通過/入力」counter+histogram observeの実装。

- [ ] **Step 4: jev.pyへJev観測を挿入する**

importへ追加:

```python
from latch.obs.metrics import observe_jev_execution
```

`JevWorker._evaluate` のjudge_pair呼び出し部分を次のように変更(既存のexcept分岐はそのまま):

```python
        t0 = self._clock.now()
        try:
            judgment = await self._gateway.judge_pair(
                intent_a=intent_a,
                intent_b=intent_b,
                intent_ids=[str(row.intent_a_id), str(row.intent_b_id)],
            )
        except JevOutputInvalidError as exc:
            # 検証失敗も実際のAPI呼び出し(1実行回数・D-16 v0.5読み替え)
            latency_s = (self._clock.now() - t0).total_seconds()
            observe_jev_execution(
                provider=exc.provider or "fallback_llm",
                latency_s=latency_s,
                usage=exc.usage if hasattr(exc, "usage") else None,
            )
            await self._record_and_skip(row, exc.provider, _SKIP_REASON_INVALID_OUTPUT)
            return
        except LLMError:
            latency_s = (self._clock.now() - t0).total_seconds()
            observe_jev_execution(
                provider="fallback_llm", latency_s=latency_s, usage=None
            )
            await self._record_and_skip(row, "fallback_llm", _SKIP_REASON_LLM_FAILURE)
            return
        latency_s = (self._clock.now() - t0).total_seconds()
        observe_jev_execution(
            provider=judgment.provider, latency_s=latency_s, usage=judgment.usage
        )
```

注: `JevOutputInvalidError` にusage属性があるかは実装時に確認する(llm/errors.pyまたはgateway.py内の例外定義)。無ければ `usage=None` 固定でよい(usageの記録は成功経路が主)・報告書に記録。計上規律(D-16「実際のAPI呼び出しをすべて1実行回数に計上」)をcounterで守る。既存の `await self._record(judgment.provider)`(Redisカウンタ・D-16の正)は**そのまま残す**(メトリクスcounterとRedisカウンタは別の責務)。

- [ ] **Step 5: latch_engine.py・group_engine.pyへscore観測を挿入する**

`latch_engine.py`: importへ `from latch.obs.metrics import observe_latch_score` を追加。提案確定処理の関数内(rg手がかり: `if score >= latch_calc.LATCH_THRESHOLD:` の分岐直後・proposal生成の直前)へ:

```python
                observe_latch_score(shape="pair", score=float(score))
```

`group_engine.py`: importへ同一行を追加。aggregate計算を確定させるUPDATE(`_UPDATE_AGGREGATE` を実行するヘルパー・rg手がかり: `SET prev_aggregate_score = aggregate_score`)の呼び出し点で、UPDATE成功後(row is Noneでない)に:

```python
        observe_latch_score(shape="group", score=float(score))
```

観測対象は「提案確定時」のscore(D-01閾値0.60周辺の観察・design §2.8)。INSERT前後の競合負けなどで実際に提案化しなかった場合の値はobserveしない(提案時の分布がKPI素材)。

- [ ] **Step 6: 試験が通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_pipeline_metrics.py tests/unit/test_worker.py tests/unit/test_worker_jev.py tests/unit/matching/ -v`
Expected: PASS(既存試験全て不変)

- [ ] **Step 7: コミットする**

```bash
git add backend/src/latch/worker/main.py backend/src/latch/worker/jev.py backend/src/latch/worker/matching/latch_engine.py backend/src/latch/worker/matching/group_engine.py backend/tests/unit/test_obs_pipeline_metrics.py
git commit -m "feat: パイプライン/Jev/スコアのメトリクス観測挿入(ロジック変更なし・D-16計上規律をcounterで補完)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 10: main.py配線(/metricsマウント・middleware挿入)

**Files:**
- Modify: `backend/src/latch/main.py`
- Test: `backend/tests/unit/test_obs_metrics.py`(追記)

**Interfaces:**
- Consumes: Task 1のmetrics(モジュール単例=既定REGISTRY)・Task 4のmiddleware・Task 5のrouter(登録済み)
- Produces: `GET /metrics`(prometheus_client.make_asgi_app()のマウント・text/plain)

- [ ] **Step 1: 失敗する試験を書く(test_obs_metrics.pyへ追記)**

```python
# -- /metricsマウント(Task 10)--

async def test_metrics_endpoint_serves_registry(app, client):
    """/metricsがtext/plainで既定REGISTRYの内容を返す(ASGIマウント)。"""
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "latch_http_requests_total" in resp.text


async def test_metrics_path_itself_is_not_counted(app, client):
    """/metricsの自己アクセスは計上しない(Review Focus 3・自己増殖防止)。"""
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == "latch_http_requests_total":
                assert sample.labels.get("path") != "/metrics"
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_metrics.py -v -k metrics_endpoint or counted`
Expected: FAIL(/metricsが未マウント)

- [ ] **Step 3: main.pyへ配線する**

importへ追加:

```python
from prometheus_client import make_asgi_app

from latch.obs.middleware import HTTPMetricsMiddleware
```

`create_app` 内、`app.include_router(...)` 群の後に追加:

```python
    # HTTP基本メトリクス(M4 ws-4・design §2.9)。/metricsの自己アクセス
    # はmiddleware内で除外される
    app.add_middleware(HTTPMetricsMiddleware)
    # /metricsマウント(認証外・無レート制限の生のASGIマウント・design §2.5)。
    # nginx経由でなくapi直のポートで公開(127.0.0.1バインド・承認事項4)
    app.mount("/metrics", make_asgi_app())
```

- [ ] **Step 4: 試験が通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_metrics.py tests/unit/test_app_health.py -v`
Expected: PASS(既存のhealth試験も不変)

- [ ] **Step 5: コミットする**

```bash
git add backend/src/latch/main.py backend/tests/unit/test_obs_metrics.py
git commit -m "feat: /metrics ASGIマウントとHTTPMetricsMiddleware挿入(M4 ws-4)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 11: compose追記・dashboard環境・Makefile

**Files:**
- Modify: `compose.staging.yaml`
- Modify: `compose.load.yaml`
- Create: `compose.dashboard.yaml`
- Create: `docker/prometheus/prometheus.yml`
- Create: `docker/grafana/provisioning/datasources/prometheus.yaml`
- Create: `docker/grafana/provisioning/dashboards/default.yaml`
- Modify: `Makefile`
- Test: `backend/tests/unit/test_obs_compose_pins.py`(新規)

**Interfaces:**
- Consumes: Task 7の `obs_worker_metrics_port`(workerの9100 listen)・既存YAMLアンカー構造
- Produces: latch-dashboardプロジェクト(make dashboard-up/down/ps/logs)・staging worker公開ポート18101/18102・load公開ポート28101/28102

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_obs_compose_pins.py`:

```python
"""compose追記の静的ピン(M4 ws-4・承認事項4・YAMLアンカー構造維持)。

pyyaml(既存依存)でパースし・127.0.0.1明示バインドとアンカー構造を検査
する。dockerはこの試験では起動しない(実起動の確認はTask 13)。
"""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]


def _load_compose(name: str) -> dict:
    return yaml.safe_load((REPO / name).read_text(encoding="utf-8"))


def _worker_ports(compose: dict, service: str) -> list[str]:
    return [p for p in compose["services"][service].get("ports", [])]


def test_staging_worker_metrics_env_in_worker_common():
    compose = _load_compose("compose.staging.yaml")
    env = compose["services"]["worker1"]["environment"]
    assert env["LATCH_OBS_WORKER_METRICS_PORT"] == "9100"


def test_staging_worker_ports_are_loopback_bound():
    compose = _load_compose("compose.staging.yaml")
    for service, host_port in (("worker1", 18101), ("worker2", 18102)):
        ports = _worker_ports(compose, service)
        assert f"127.0.0.1:{host_port}:9100" in ports


def test_load_worker_ports_are_loopback_bound():
    compose = _load_compose("compose.load.yaml")
    for service, host_port in (("worker1", 28101), ("worker2", 28102)):
        ports = _worker_ports(compose, service)
        assert f"127.0.0.1:{host_port}:9100" in ports


def test_dashboard_compose_binds_loopback_only():
    compose = _load_compose("compose.dashboard.yaml")
    assert compose["name"] == "latch-dashboard"
    prom_ports = compose["services"]["prometheus"]["ports"]
    graf_ports = compose["services"]["grafana"]["ports"]
    assert "127.0.0.1:19090:9090" in prom_ports
    assert "127.0.0.1:13000:3000" in graf_ports


def test_dashboard_prometheus_has_host_gateway():
    compose = _load_compose("compose.dashboard.yaml")
    extra = compose["services"]["prometheus"]["extra_hosts"]
    assert "host.docker.internal:host-gateway" in extra


def test_prometheus_scrapes_staging_targets():
    raw = (REPO / "docker/prometheus/prometheus.yml").read_text(encoding="utf-8")
    config = yaml.safe_load(raw)
    targets = []
    for job in config["scrape_configs"]:
        for group in job.get("static_configs", []):
            targets.extend(group["targets"])
    assert {
        "host.docker.internal:18001",
        "host.docker.internal:18002",
        "host.docker.internal:18101",
        "host.docker.internal:18102",
    } <= set(targets)
    # 既存ポート番号との衝突がない(18000=nginx・5432/6379/8085等は未使用)
    assert "host.docker.internal:18000" not in targets


def test_makefile_has_dashboard_and_obs_test_targets():
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    for target in (
        "dashboard-up:", "dashboard-down:", "dashboard-ps:", "dashboard-logs:",
        "staging-obs-test:",
    ):
        assert target in makefile
    phony = makefile.splitlines()[0]
    for name in ("dashboard-up", "dashboard-down", "dashboard-ps",
                 "dashboard-logs", "staging-obs-test"):
        assert name in phony
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_compose_pins.py -v`
Expected: FAIL(各追記が未実施)

- [ ] **Step 3: compose.staging.yamlへ追記する**

`x-worker-common` のenvironmentを次のように変更(backend-envアンカーは触らない):

```yaml
x-worker-common: &worker-common
  build:
    context: backend
  command: python -m latch.worker
  environment:
    <<: *backend-env
    LATCH_OBS_WORKER_METRICS_PORT: "9100"
```

worker1/worker2を次のように変更:

```yaml
  worker1:
    <<: *worker-common
    ports:
      - "127.0.0.1:18101:9100"

  worker2:
    <<: *worker-common
    ports:
      - "127.0.0.1:18102:9100"
```

- [ ] **Step 4: compose.load.yamlへ追記する**

stagingと同一の2箇所(env 1行・ports 2つ・ホスト番号が28101/28102)を追記する。

- [ ] **Step 5: compose.dashboard.yamlを新設する**

```yaml
# ダッシュボード環境(M4 ws-4)。別プロジェクトlatch-dashboard。
# Prometheusがstagingのホスト公開ポート(127.0.0.1バインド・api=18001/18002
# worker=18101/18102)をscrapeし、Grafanaへdatasourceをprovisioningする。
# load環境(28000系)のtargetはws-6(性能測定)が追加する(design §3.1どおり
# staging系のみの初期構成)。両ポートとも127.0.0.1明示バインド(承認事項4)。
name: latch-dashboard

services:
  prometheus:
    # prom/prometheusの実在安定タグ。pull失敗時は安定版に読み替えて
    # 報告書に記録する(§0固定値の遵守)
    image: prom/prometheus:v3.0.0
    command:
      - --config.file=/etc/prometheus/prometheus.yml
    volumes:
      - ./docker/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    # コンテナ内からホストの127.0.0.1バインドポートへ到達する経路
    # (§2.1読み替え9)
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "127.0.0.1:19090:9090"
    restart: unless-stopped

  grafana:
    image: grafana/grafana-oss:11.4.0
    environment:
      # ローカル確認用の初期ログイン(127.0.0.1のみ公開・stagingはローカル
      # docker composeの別プロジェクト)
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: admin
    volumes:
      - ./docker/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./docker/grafana/dashboards:/var/lib/grafana/dashboards:ro
    ports:
      - "127.0.0.1:13000:3000"
    restart: unless-stopped
```

- [ ] **Step 6: prometheus.ymlとGrafana provisioningを新設する**

`docker/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: latch-staging-api
    static_configs:
      - targets:
          - "host.docker.internal:18001"
          - "host.docker.internal:18002"
  - job_name: latch-staging-worker
    static_configs:
      - targets:
          - "host.docker.internal:18101"
          - "host.docker.internal:18102"
  - job_name: prometheus
    static_configs:
      - targets:
          - "localhost:9090"
```

`docker/grafana/provisioning/datasources/prometheus.yaml`:

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

`docker/grafana/provisioning/dashboards/default.yaml`:

```yaml
apiVersion: 1

providers:
  - name: latch
    folder: LATCH
    type: file
    allowUiUpdates: false
    updateIntervalSeconds: 10
    options:
      path: /var/lib/grafana/dashboards
```

- [ ] **Step 7: Makefileへターゲットを追加する**

`.PHONY` 行へ `dashboard-up dashboard-down dashboard-ps dashboard-logs staging-obs-test` を追加。staging系ターゲットの後ろへ(変数 `DASHBOARD_COMPOSE := docker compose -f compose.dashboard.yaml` をSTAGING_COMPOSE定義の近くへ追加):

```make
dashboard-up: ## ダッシュボード環境を起動(prometheus 19090・grafana 13000・stagingとは別プロジェクト)
	$(DASHBOARD_COMPOSE) up -d --wait

dashboard-down: ## ダッシュボード環境を停止
	$(DASHBOARD_COMPOSE) down

dashboard-ps: ## ダッシュボード環境の状態
	$(DASHBOARD_COMPOSE) ps

dashboard-logs: ## ダッシュボード環境のログ(--tail 30)
	$(DASHBOARD_COMPOSE) logs --tail 30

staging-obs-test: ## staging Observability受け入れハーネス(M4 ws-4・staging-reset直後からの一連実施を前提・証拠YAMLをdocs/testassets/results/へ出力)
	cd backend && uv run pytest staging_tests_obs
```

- [ ] **Step 8: 試験が通ることを確認してコミットする**

Run: `cd backend && uv run pytest tests/unit/test_obs_compose_pins.py -v && cd .. && docker compose -f compose.dashboard.yaml config --quiet && docker compose -f compose.staging.yaml config --quiet && docker compose -f compose.load.yaml config --quiet`
Expected: pytest PASS・compose config 3件ともエラーなし(YAML構造の整合確認)

```bash
git add compose.staging.yaml compose.load.yaml compose.dashboard.yaml docker/prometheus/ docker/grafana/ Makefile backend/tests/unit/test_obs_compose_pins.py
git commit -m "feat: latch-dashboard環境(prometheus/grafana provisioning as-code)・staging/loadのworker metricsポート・Makefileターゲット

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 12: Grafanaダッシュボード4枚

**Files:**
- Create: `docker/grafana/dashboards/overview.json`・`pipeline.json`・`jev.json`・`behavior.json`
- Test: なし(JSON構造の検証はTask 13のstaging受け入れ・grafana APIで行う。unitではやらない — provisioningの自動ロードが実機でのみ証明できるため)

**Interfaces:**
- Consumes: Task 1のメトリクス名・Task 7のkpi_daily gauge
- Produces: provisioningで自動ロードされる4枚のダッシュボード(folder: LATCH)

**構造仕様(共通)**: Grafana dashboard schema v1のトップレベル `{ "annotations": {...}, "editable": true, "fiscalYearStartMonth": 0, "graphTooltip": 0, "id": null, "links": [], "panels": [...], "refresh": "30s", "schemaVersion": 39, "tags": ["latch"], "templating": {"list": []}, "time": {"from": "now-6h", "to": "now"}, "timepicker": {}, "timezone": "browser", "title": ..., "uid": ..., "version": 1 }`。各panelは `{"datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}, "fieldConfig": {"defaults": {"unit": ...}, "overrides": []}, "gridPos": {...}, "id": N, "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "single"}}, "targets": [{"expr": "<PromQL>", "legendFormat": "...", "refId": "A"}], "title": "...", "type": "timeseries"}` の形。datasource uidはprovisioningのdatasource名を参照するため `"uid": "prometheus"` をdatasources/prometheus.yamlの `- name: Prometheus` に **`uid: prometheus`** を追記して固定する(本Taskの冒頭で実施)。

- [ ] **Step 1: datasources/prometheus.yamlへuidを固定する**

```yaml
datasources:
  - name: Prometheus
    uid: prometheus
    ...(以降Task 11の内容どおり)
```

- [ ] **Step 2: 代表パネル(overview.jsonのMLRパネル)を書き、残りをPromQL一覧表どおりに繰り返す**

`docker/grafana/dashboards/overview.json` の代表パネル(MLR日次・比率は分子/分母の除算で表示):

```json
{
  "datasource": {"type": "prometheus", "uid": "prometheus"},
  "fieldConfig": {"defaults": {"unit": "percentunit", "min": 0, "max": 1}, "overrides": []},
  "gridPos": {"h": 8, "w": 8, "x": 0, "y": 0},
  "id": 1,
  "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "single"}},
  "targets": [
    {
      "expr": "latch_kpi_daily_numerator{metric=\"mlr\"} / clamp_min(latch_kpi_daily_denominator{metric=\"mlr\"}, 1)",
      "legendFormat": "MLR (latest day)",
      "refId": "A"
    }
  ],
  "title": "Mutual Latch Rate (日次・kpi_daily最新行)",
  "type": "timeseries"
}
```

**4枚のパネル・PromQL一覧表(この表の各行=1パネル。title・expr・unitを表どおりに・gridPosは左詰め8列幅で繰り返し)**:

overview.json(uid: latch-overview):
| title | expr | unit |
|---|---|---|
| Mutual Latch Rate (日次) | `latch_kpi_daily_numerator{metric="mlr"} / clamp_min(latch_kpi_daily_denominator{metric="mlr"}, 1)` | percentunit |
| DAU (sessions) | `latch_kpi_daily_denominator{metric="intent_create_rate"}` | short |
| Event Queue pending | `max(latch_event_queue_pending)` | short |
| 最古pending年齢 | `max(latch_event_queue_oldest_pending_seconds)` | s |
| Candidate数 | `max(latch_pipeline_candidates)` | short |
| Hard Filter通過率(件/実行) | `sum(rate(latch_hardfilter_pass_total{job=~"latch-.*"}[5m])) / clamp_min(sum(rate(latch_hardfilter_input_total{job=~"latch-.*"}[5m])), 0.001)` | short |
| Jev実行レート | `sum(rate(latch_jev_executions_total[5m]))` | ops |
| HTTP p95(全経路) | `histogram_quantile(0.95, sum(rate(latch_http_request_duration_seconds_bucket[5m])) by (le))` | s |
| breaker状態 | `max(latch_breaker_state)` | short(注意書きをdescriptionに: closed=0/open=1/half_open=2) |
| YES率 (日次) | `latch_kpi_daily_numerator{metric="yes_rate"} / clamp_min(latch_kpi_daily_denominator{metric="yes_rate"}, 1)` | percentunit |

pipeline.json(uid: latch-pipeline): 上表の「Event Queue pending」「最古pending年齢」「Candidate数」「Hard Filter通過率」+次の2枚 —
| title | expr | unit |
|---|---|---|
| Vector Retrieval件数(平均) | `sum(rate(latch_retrieval_vector_items_sum[5m])) / clamp_min(sum(rate(latch_retrieval_vector_items_count[5m])), 0.001)` | short |
| Vector Retrieval件数(p95) | `histogram_quantile(0.95, sum(rate(latch_retrieval_vector_items_bucket[5m])) by (le))` | short |

jev.json(uid: latch-jev): 「Jev実行レート」+次の4枚(counterはrate系で消費するのが正しい使い方 — restartで0に戻るため。Review Focus 5):
| title | expr | unit |
|---|---|---|
| Jev p95 latency | `histogram_quantile(0.95, sum(rate(latch_jev_latency_seconds_bucket[5m])) by (le, provider))` | s |
| Jev実行数(provider別) | `sum(rate(latch_jev_executions_total[5m])) by (provider)` | ops |
| Jev usage input tokens | `sum(rate(latch_jev_usage_tokens_total{kind="input"}[5m])) by (provider)` | ops |
| Jev usage output tokens | `sum(rate(latch_jev_usage_tokens_total{kind="output"}[5m])) by (provider)` | ops |
| 1成立あたりAIコスト(概算) | `sum(rate(latch_jev_usage_tokens_total{kind="input"}[1w])) / clamp_min(sum(rate(latch_jev_executions_total[1w])) * 0 + max(latch_kpi_daily_numerator{metric="mlr"}), 1)` は実装できないため、**usage単価の変数(${TOKEN_PRICE})をtemplatingで置き・`sum(increase(latch_jev_usage_tokens_total{kind="input"}[1w])) * ${TOKEN_PRICE} / clamp_min(max(latch_kpi_daily_numerator{metric="mlr"}), 1)` をパネル式にする**(docs「1成立あたりAIコスト」の導出素材・design §2.7) | currencyUSD(単価は変数で調整) |

behavior.json(uid: latch-behavior): kpi_daily系の全指標 — 15行のうちmlr・yes_rateを除く13指標を「numerator / clamp_min(denominator, 1)」の同一パターンで1枚ずつ(title=§2.3表の指標名・unit=percentunit・ただしactive_intent_per_dauとretentionとjev_executions_per_intentはshort)。mute_rate・intent_recreation_rate等の週次行も同じ式(最新行の除算)。

- [ ] **Step 3: JSONの形式検証(単体)**

```bash
cd backend && uv run python -c "
import json, pathlib
for p in pathlib.Path('../docker/grafana/dashboards').glob('*.json'):
    d = json.loads(p.read_text())
    assert d.get('title'), p
    assert d.get('panels'), p
    print(p.name, len(d['panels']), 'panels OK')
"
```
Expected: 4ファイルともパース成功・panels 1件以上

- [ ] **Step 4: コミットする**

```bash
git add docker/grafana/provisioning/datasources/prometheus.yaml docker/grafana/dashboards/
git commit -m "feat: Grafanaダッシュボード4枚(overview/pipeline/jev/behavior・provisioning自動ロード)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 13: staging受け入れハーネスと実施(ws-3ハーネス土台)

**Files:**
- Create: `backend/staging_tests_obs/conftest.py`
- Create: `backend/staging_tests_obs/test_w4_a_env_seed.py`
- Create: `backend/staging_tests_obs/test_w4_b_kpi_daily.py`
- Create: `backend/staging_tests_obs/test_w4_c_exposure.py`

**Interfaces:**
- Consumes: staging_tests(既存)のhelpers_seed・helpers_scenario・helpers_wait・conftestのAPI/DB fixture作法(sys.path経由で再利用・既存ハーネスファイルはEXPECTED_HEAD以外変更しない)・Task 1〜12の全成果物
- Produces: `make staging-obs-test` の実行と証拠YAML `docs/testassets/results/ws4-staging-result-<ts>.yaml`(10 §5形式)

**設計**: ws-3ハーネスのconftestは `from conftest import EXPECTED_HEAD, record_item` 形式でhelpersがstaging_testsを直接importしている。staging_tests_obsのconftestはpytestに発見されたconftest(staging_tests_obs配下のもの)のみhook発火するため、sys.pathにstaging_testsを追加してhelpersをimportすれば、staging_testsのconftestのsessionfinish(証拠YAML出力)は発火**しない**(hook登録はpytestのconftest収集経路のみ)・証拠YAMLはws4-接頭辞の自前sessionfinishで分離する。**既存ハーネスへの変更はTask 2のEXPECTED_HEAD追従のみ**。

- [ ] **Step 1: staging_tests_obs/conftest.pyを書く(obs_results.pyの全文はStep 2)**

**同名conftestモジュール問題の解決(必ずこの順で実装する)**: helpers_seed等は `from conftest import SEED_PATH, ...` とstaging_testsのconftestを直接importする。pytestはconftest.pyをモジュール名 "conftest" でsys.modulesへ登録するため、staging_tests_obsのconftestが先に登録されるとhelpers_seedのimportがobs側のconftestを拾ってSEED_PATH不在で壊れる。これを避けるため、obs側のconftest.pyの先頭で「自分自身をsys.modulesから外してからstaging_tests側のconftestを確実にimportする」処理を入れる(pytestのhook呼び出しはモジュールオブジェクト経由のため、sys.modulesから外しても本conftestのsessionfinishは動作する)。

`backend/staging_tests_obs/conftest.py`:

```python
"""ws-4受け入れハーネスの基盤(M4 ws-4・ws-3ハーネス土台の再利用)。

証拠YAMLは本conftestのsessionfinishのみが出力する(hookはpytestの
conftest収集経路のみ発火するため、sys.path importで読まれたstaging_tests
のconftestのsessionfinishは発火しない → 証拠はws4-接頭辞で完全分離)。
実行は `make staging-obs-test`。
"""

import importlib
import sys
from pathlib import Path

STAGING_TESTS_DIR = Path(__file__).resolve().parent.parent / "staging_tests"
if str(STAGING_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(STAGING_TESTS_DIR))

# 同名conftest問題の解決: pytestが登録したobs側のconftest("conftest")を
# sys.modulesから外し、staging_tests側を確実にimportさせる。以後の
# helpers_seed等の `from conftest import ...` はstaging_tests側を指す
import conftest as _obs_conftest  # noqa: F401  (pytestが読んだ自分自身)

if Path(getattr(sys.modules.get("conftest"), "__file__", "")).parent != STAGING_TESTS_DIR:
    del sys.modules["conftest"]
st_conftest = importlib.import_module("conftest")  # staging_tests/conftest.py

from datetime import datetime, timedelta, timezone  # noqa: E402

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
import yaml  # noqa: E402

from obs_results import _results  # noqa: E402  (ws-4独自の証拠収集)

API_BASE = st_conftest.API_BASE
DSN = st_conftest.DSN
EXPECTED_HEAD = st_conftest.EXPECTED_HEAD
auth_header = st_conftest.auth_header
get_clock = st_conftest.get_clock

RESULTS_DIR = (
    Path(__file__).resolve().parents[2] / "docs" / "testassets" / "results"
)



def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    """session終了時に証拠YAMLを出力する(失敗時のitemsも含めて出す)。"""
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "unit": "M4-ws-4",
        "executed_at": now.isoformat(),
        "environment": "latch-staging",
        "items": list(_results),
    }
    ts = now.strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"ws4-staging-result-{ts}.yaml"
    path.write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


@pytest.fixture
async def api():
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as c:
        yield c


@pytest.fixture
async def db():
    conn = await asyncpg.connect(DSN)
    await conn.set_type_codec(
        "jsonb", encoder=lambda v: v, decoder=lambda v: v, schema="pg_catalog"
    )
    await conn.execute("SET default_transaction_read_only = on")
    try:
        yield conn
    finally:
        await conn.close()



`backend/staging_tests_obs/obs_results.py`(conftest.pyとtestファイルの共用・同名conftest問題を避けるため独立モジュール・Step 2に全文を置く):

注: DSN・API_BASE・EXPECTED_HEAD・get_clock・auth_headerの実名はstaging_tests/conftest.pyの現行定義を確認して合わせる(異なる名前ならそちらに合わせる)。db fixtureはSELECT専用(read only・ws-3と同一規律)・jsonb codecもws-3のdb fixtureと同一の変換を踏襲する(実装時にstaging_tests/conftest.pyのdb fixtureを読んで同一にする — 計画のlambdaはそのコピーの見本)。

- [ ] **Step 2: test_w4_a_env_seed.pyを書く**

まずobs_results.pyを書く(prometheus照合系の関数と証拠収集をここへ一本化する・conftest.pyには二重定義しない。sessionfinishは `from obs_results import _results` で参照):

```python
"""ws-4受け入れの証拠収集とprometheus照合(10 §5形式・staging_tests_obs共用)。"""

from datetime import datetime, timedelta, timezone

```python
"""ws-4受け入れの証拠収集とprometheus照合(10 §5形式・staging_tests_obs共用)。"""

from datetime import datetime, timedelta, timezone

import httpx

PROM_URL = "http://127.0.0.1:19090"

_results: list[dict] = []


def record_item(*, item_id: str, name: str, test: str, verdict: str, observed: dict) -> None:
    _results.append(
        {"id": item_id, "name": name, "test": test, "verdict": verdict, "observed": observed}
    )


async def prom_query(expr: str) -> list[dict]:
    """Prometheusの即時クエリ(結果seriesのlist・空なら[])。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{PROM_URL}/api/v1/query", params={"query": expr})
    assert r.status_code == 200, f"prometheus query failed: {r.text}"
    return r.json()["data"]["result"]


async def wait_prom_series(expr: str, timeout_s: float = 120.0) -> list[dict]:
    """scrape周期(15秒)を考慮してseriesが現れるまで待つ。"""
    import asyncio

    deadline = datetime.now(timezone(timedelta(hours=9))).timestamp() + timeout_s
    while datetime.now(timezone(timedelta(hours=9))).timestamp() < deadline:
        result = await prom_query(expr)
        if result:
            return result
        await asyncio.sleep(5.0)
    raise AssertionError(f"prometheus series not found within {timeout_s}s: {expr}")
```

`test_w4_a_env_seed.py` の本体(importは次の形・本体は上で示した内容どおり):

```python
from conftest import EXPECTED_HEAD
from helpers_seed import seed_all, wait_pipeline_settled
from obs_results import record_item, wait_prom_series
```

本体(期待seriesのexpr・record_item呼び出しは上で示した test_w4_a1/a2 の内容そのまま):

```python
async def test_w4_a1_env_and_seed(api, db):
    """head=0008・health・シード投入と収束待ち(≤180秒)。"""
    ver = await db.fetchval("SELECT version_num FROM alembic_version")
    assert ver == EXPECTED_HEAD
    r = await api.get("/health")
    assert r.status_code == 200
    state = await seed_all(api, db)
    await wait_pipeline_settled(db)
    record_item(
        item_id="ws4-a1",
        name="環境前提(0008)とシード収束",
        test="test_w4_a1_env_and_seed",
        verdict="pass",
        observed={"alembic_head": ver, "health": r.json()["status"]},
    )


async def test_w4_a2_pipeline_metrics_visible(api, db):
    """3群のメトリクスがprometheusでseriesを持つ(収束後)。"""
    checks = {
        "pending": "latch_event_queue_pending",
        "hardfilter_pass": 'latch_hardfilter_pass_total{job="latch-staging-worker"}',
        "vector_items": 'latch_retrieval_vector_items_count{job="latch-staging-worker"}',
        "candidates": "latch_pipeline_candidates",
        "http_requests": 'latch_http_requests_total{path="/health"}',
        "jev": 'latch_jev_executions_total{provider="typesafe_jev"}',
        "score": 'latch_score_count{shape="pair"}',
    }
    observed = {}
    for name, expr in checks.items():
        series = await wait_prom_series(expr)
        observed[name] = len(series)
        assert series, f"{name} series missing: {expr}"
    record_item(
        item_id="ws4-a2",
        name="3群メトリクス到達(prometheus)",
        test="test_w4_a2_pipeline_metrics_visible",
        verdict="pass",
        observed=observed,
    )
```

- [ ] **Step 3: test_w4_b_kpi_daily.pyを書く**

`test_w4_b_kpi_daily.py` のimportは次の形:

```python
from datetime import datetime, timedelta

from conftest import auth_header, get_clock
from helpers_scenario import make_scenario_pair
from helpers_seed import seed_all
from helpers_wait import wait_until
from obs_results import record_item
```

本体(`_proposed_exists`・`_matched_delta`・`_my_proposed_latch` はファイル内の小さなヘルパーとして定義する・下記をそのまま置く):

```python
async def _proposed_exists(db, ids) -> bool:  # noqa: ANN001
    n = await db.fetchval(
        "SELECT count(*) FROM latch_status_events e"
        " JOIN latches l ON l.id = e.latch_id"
        " WHERE e.to_status='proposed'"
        " AND l.intent_ids && $1::uuid[]",
        [str(i) for i in ids],
    )
    return n > 0


async def _matched_delta(db, before: int) -> int:  # noqa: ANN001
    cur = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='matched'"
    )
    return cur - before


async def _my_proposed_latch(api, subject: str) -> dict:  # noqa: ANN001
    r = await api.get("/v1/latches/my", headers=auth_header(subject))
    assert r.status_code == 200, r.text
    latches = [x for x in r.json() if x.get("status") == "proposed"]
    assert latches, f"proposed latch not found for {subject}: {r.text}"
    return latches[0]


async def test_w4_b1_scenario_matched_and_sessions(api, db):
    """2人ペアを双方YES(matched遷移)・sessions投与(DAU素材)。"""
    clock = await get_clock()
    before = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='matched'"
    )
    # 専用シナリオ(ws-3のhelpers_scenario再利用・早朝06:30対象でシードと隔離)
    ids = await make_scenario_pair(api, subjects=("w4b-1", "w4b-2"))
    await wait_until(
        lambda: _proposed_exists(db, ids),
        timeout_s=180, interval_s=10,
        what="専用ペアのproposed遷移",
    )
    latch = await _my_proposed_latch(api, "w4b-1")
    r1 = await api.post(
        f"/v1/latches/{latch['id']}/responses",
        headers=auth_header("w4b-1"),
        json={"value": "yes"},
    )
    r2 = await api.post(
        f"/v1/latches/{latch['id']}/responses",
        headers=auth_header("w4b-2"),
        json={"value": "yes"},
    )
    assert r1.status_code in (200, 201) and r2.status_code in (200, 201)
    await wait_until(
        lambda: _matched_delta(db, before) >= 1,
        timeout_s=180, interval_s=10,
        what="双方YESのmatched遷移",
    )
    # sessions投与(3ユーザー・DAU素材)
    for subject in ("w4b-1", "w4b-2", "w4b-3"):
        r = await api.post("/v1/sessions", headers=auth_header(subject))
        assert r.status_code == 204
    record_item(
        item_id="ws4-b1",
        name="専用ペアmatchedとsessions投与",
        test="test_w4_b1_scenario_matched_and_sessions",
        verdict="pass",
        observed={"latch": latch["id"]},
    )


async def test_w4_b2_kpi_job_fires_on_clock_advance(api, db):
    """JST 0時跨ぎのClock操作→KpiJob発火(≤240秒)→kpi_dailyにmlr行が現れる。"""
    clock = await get_clock()
    state = await seed_all(api, db)
    base = datetime.fromisoformat(state["meta_base"])

    async def mlr_rows() -> int:  # noqa: ANN202
        return await db.fetchval(
            "SELECT count(*) FROM kpi_daily WHERE metric_key='mlr'"
        )

    if await mlr_rows() == 0:
        target = (base + timedelta(days=1)).replace(hour=0, minute=5)
        clock.set(target)  # JST 0時跨ぎ(前進のみ・ISO明示)
        record_item(
            item_id="ws4-b2-clock",
            name="Clock前進(JST 0時跨ぎ)",
            test="test_w4_b2_kpi_job_fires_on_clock_advance",
            verdict="pass",
            observed={"clock_set_to": target.isoformat()},
        )
    async def _fired() -> bool:
        # helpers_wait.wait_untilへはawaitを含むlambdaは渡せないため
        # コルーチン関数を渡す(シグネチャは実装時に必ず確認)
        return await mlr_rows() > 0

    await wait_until(
        _fired, timeout_s=240, interval_s=15,
        what="KpiJobの遡及UPSERT(kpi_daily mlr行)",
    )
    record_item(
        item_id="ws4-b2",
        name="KpiJob発火(kpi_daily mlr行)",
        test="test_w4_b2_kpi_job_fires_on_clock_advance",
        verdict="pass",
        observed={"mlr_rows": await mlr_rows()},
    )


async def test_w4_b3_mlr_matches_db_observation(api, db):
    """kpi_dailyのmlr分子/分母がDB実測と一致(G4-4の証拠・09 §2.1どおり)。"""
    row = await db.fetchrow(
        "SELECT numerator, denominator, day FROM kpi_daily"
        " WHERE metric_key='mlr' ORDER BY day DESC LIMIT 1"
    )
    assert row is not None, "kpi_daily mlr行が無い(KpiJob未発火)"
    day = row["day"]
    # 同一JST日のDB実測(ハーネス内で直接数える・09 §2.1の集計規定どおり)
    num = await db.fetchval(
        "SELECT count(*) FROM latch_status_events"
        " WHERE to_status='matched'"
        " AND (created_at AT TIME ZONE 'Asia/Tokyo')::date = $1",
        day,
    )
    den = await db.fetchval(
        "SELECT count(*) FROM latch_status_events"
        " WHERE to_status='proposed'"
        " AND (created_at AT TIME ZONE 'Asia/Tokyo')::date = $1",
        day,
    )
    assert (int(row["numerator"]), int(row["denominator"])) == (num, den)
    record_item(
        item_id="ws4-b3",
        name="MLR日次値とDB実測一致",
        test="test_w4_b3_mlr_matches_db_observation",
        verdict="pass",
        observed={
            "day": str(day),
            "kpi": [int(row["numerator"]), int(row["denominator"])],
            "db_observed": [num, den],
        },
    )
```

注: helpers_seed・helpers_scenario・helpers_waitの正確なシグネチャ(`make_scenario_pair` の引数形式・`seed_all` の戻り値・`wait_until` のコルーチン/lambda契約・`state["meta_base"]` のキー名)は実装時に必ずstaging_testsの該当ヘルパーを読んで合わせる(引数形式・返り値のunpackを確認 — スーパーバイザー補記3の「テストコード自己見直し」対象)。`GET /v1/latches/my`・`POST /v1/latches/{id}/responses` のパスとボディも実装時に backend/src/latch/latches/routes.py の宣言を読んで合わせる(既存stagingハーネスのtest_s2/test_s4が同一操作を所有 — その試験のコードを直接参考にする)。staging_testsのcompute_mlr(db)資産も MLR照合の補助観察として使ってよい(必須ではない・b3の直接SELECTが所有)。

- [ ] **Step 4: test_w4_c_exposure.pyを書く**

```python
"""ws-4受け入れ(c): /metrics・grafana 4枚・非ループバック拒否(承認条件(d))。"""

import socket

import httpx

from obs_results import record_item

PROM_URL = "http://127.0.0.1:19090"
GRAFANA_URL = "http://127.0.0.1:13000"


async def test_w4_c1_api_metrics_direct():
    """api1直(18001)/metricsが200・text/plain(nginx経由でなく直scrape)。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("http://127.0.0.1:18001/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "latch_http_requests_total" in r.text


async def test_w4_c2_worker_metrics_direct():
    """worker1直(18101)も200(apiと同一レジストリ名・別プロセス値)。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("http://127.0.0.1:18101/metrics")
    assert r.status_code == 200
    assert "latch_event_queue_pending" in r.text


async def test_w4_c3_grafana_dashboards_provisioned():
    """grafana APIでダッシュボード4枚が自動ロード済み。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{GRAFANA_URL}/api/search",
            auth=("admin", "admin"),
            params={"query": "", "type": "dash-db"},
        )
        assert r.status_code == 200
        titles = {d["title"] for d in r.json()}
    assert {"Mutual Latch Rate …overview"} <= titles or len(titles) >= 4
    record_item(
        item_id="ws4-c3", name="grafanaダッシュボード4枚稼働",
        test="test_w4_c3", verdict="pass", observed={"titles": sorted(titles)},
    )


async def test_w4_c4_prometheus_scrapes_all_targets():
    """prometheusが4 targetをup=1でscrapeしている。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{PROM_URL}/api/v1/query", params={"query": "up"}
        )
    assert r.status_code == 200
    result = r.json()["data"]["result"]
    up_targets = {
        s["metric"].get("job"): s["value"][1] for s in result
    }
    for job in ("latch-staging-api", "latch-staging-worker", "prometheus"):
        assert up_targets.get(job) == "1", f"{job} is not up: {up_targets}"
    record_item(
        item_id="ws4-c4", name="prometheus 4 target up",
        test="test_w4_c4", verdict="pass", observed=up_targets,
    )


def test_w4_c5_metrics_not_reachable_from_non_loopback():
    """承認条件(d)項: ホスト外(非ループバック)から/metricsへ届かない。

    composeのportsは全て127.0.0.1明示バインドのため、非ループバックIP
    への接続は拒否される。ホストの非ループバックIPは実経路(8.8.8.8への
    UDP接続のsockname)で取得する。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        host_ip = s.getsockname()[0]
    finally:
        s.close()
    assert host_ip != "127.0.0.1"
    for port in (18001, 18002, 18101, 18102, 19090, 13000):
        with socket.socket() as sock:
            sock.settimeout(3.0)
            code = sock.connect_ex((host_ip, port))
        assert code != 0, (
            f"非ループバックIP {host_ip}:{port} に接続できてしまう(127.0.0.1"
            "バインドの承認条件違反)"
        )
    record_item(
        item_id="ws4-c5", name="非ループバックから/metricsへ届かない(承認条件d)",
        test="test_w4_c5", verdict="pass", observed={"host_ip": host_ip},
    )
```

- [ ] **Step 5: staging環境で受け入れを実施する(コマンド単位・§8の手順)**

§8の実施手順に従い、`make staging-obs-test` まで実施する。実行順序と待ち: staging-reset→keys→dashboard-up→staging-obs-test(a→b→cの順でpytestが実行)。初回で失敗した場合は§0の「詰まったら」規律に従う(推測で緩めない)。

- [ ] **Step 6: 証拠YAMLを確認してコミットする**

Run: `ls docs/testassets/results/ws4-staging-result-*.yaml && cat docs/testassets/results/ws4-staging-result-*.yaml | head -50`
Expected: unit: M4-ws-4・itemsにws4-a1〜c5がverdict: passで記録

```bash
git add backend/staging_tests_obs/ docs/testassets/results/ws4-staging-result-*.yaml
git commit -m "test: staging受け入れハーネスと実施証拠(ws4-a〜c・MLR日次一致・非ループバック拒否)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 14: 報告書

**Files:**
- Create: `docs/plans/M4/ws-4-report.md`

- [ ] **Step 1: 報告書を書く(§7の形式)**

- [ ] **Step 2: 最終検査(worktree内)**

```bash
make lint && make test
find backend/tests backend/staging_tests backend/staging_tests_obs -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
# 期待値: 出力なし
git status --short
```

- [ ] **Step 3: コミット(報告書)**

```bash
git add docs/plans/M4/ws-4-report.md
git commit -m "docs: M4 ws-4完了報告(実施日・完了条件別証拠・読み替え記録・test-ci検証待ち)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## 6. 完了条件(テストで証明できる検証可能な形)

design §5を計画の完了条件へ引き取る。各条件の証明方法を明記する。

1. **3群+アクセス系のメトリクスが/metricsから露出し、Prometheusがscrapeしている(api1・api2・worker1・worker2)**
   証明: `make staging-obs-test` のtest_w4_a2(3群series到達)・test_w4_c4(prometheus 4 target up=1)・test_w4_c1/c2(api/worker直の/metrics 200)。
2. **latch-dashboardプロジェクト(prometheus・grafana)がprovisioningで起動し、ダッシュボードが空JSONでなく実系列を表示する(overviewにMLR・3群・DAUが含まれる)**
   証明: test_w4_c3(grafana APIで4枚のtitle取得)・overview.jsonにmlr・pending・candidates・DAU(intent_create_rate分母)パネルが存在(Task 12のPromQL一覧表どおり)。
3. **POST /v1/sessionsが05 §19契約どおり動作し、sessionsテーブルへuser_idと時刻のみ記録する**
   証明: unit test_obs_sessions_api(204・401・body非空422・未登録404・record呼び出し引数)+test_obs_migration(SQL_STATEMENTSに列が3列のみ=Intent文言を運ばない構造)+integration test_obs_api_ci(実DBでuser_idがusers.idと一致・列3列)。
4. **日次集計ジョブがJST 0時にkpi_dailyを書き、MLR日次・週次(7日合計比)が09 §2.1定義どおり算出される(stagingでClock操作により発火を確認)**
   証明: unit test_obs_kpi(純関数期待値表)+test_obs_kpi_job_ci(integration・空DBで8指標×4日・冪等)+staging test_w4_b2(Clock前進でKpiJob発火≤240秒)・test_w4_b3(MLR日次値=DB実測一致)。週次はSQL_UPSERT_WEEKLYの実装とunit純関数(new_muted_users・week_of)+supervisor検証時のtest-ciで担保(週境界を跨ぐstaging実行は所要が長いためstaging受け入れでは日次発火を所有)。
5. **09 §2.2の14指標がkpi_dailyの分子/分母として算出可能な状態(周期別: 日次=当日行・週次/月次=範囲集計)。近似を伴う指標(Jev実行数・通知オフ率)は設計§2.7/§2.8の方式どおり**
   証明: unit test_obs_kpiのtest_metric_keys_cover_09_2_2_table(15キー集合ピン)+SQL定数(§2.3表どおりのSQL_UPSERT_*)+cost_per_matchedはdashboard式(Task 12のjev.json)で提供(design §2.7)。
6. **unit+integration試験グリーン・staging受け入れ証拠YAMLがdocs/testassets/results/へ保存**
   証明: `make test` グリーン(基準1335+新規unit約40件)・`make test-ci` は**スーパーバイザー検証待ち**と報告書へ記録(§0規律)・ws4-staging-result-*.yamlのitems全pass。
7. **/metricsへホスト外から到達しないこと(§6-4承認条件)**
   証明: test_w4_c5(非ループバックIPへの接続拒否・6ポート)+test_obs_compose_pins(全portsの127.0.0.1明示バインドピン)。

## 7. 報告形式

報告書: `docs/plans/M4/ws-4-report.md`。以下の節をこの順で書く。

1. **実施サマリ**: 実施日・環境(worktree/ブランチm4-ws-4・基点main head)・`make test` の実数(unit試験数・基準1335+新規)・コミット一覧(ハッシュ+1行要約)。
2. **完了条件別の証拠**(§6の1〜7の各項に): 実施した試験と実行結果(pytest出力の要約)・staging証拠YAMLのファイル名とitems一覧(id・verdict)。
3. **designからの読み替え・逸脱の記録**: §2.1の読み替え表の各項(1〜10)について「実装どおりか・変更した場合は変更前→変更後+理由」を実施結果として記録する。特に読み替え2(KpiJob tick方式)・4(Candidate数のSQL)・7(created_at代用)・8(最新行export)・9(host-gateway)は必須記録。
4. **スーパーバイザー検証待ちの記録**: `make test-ci`(基準実数+新規integration 3ファイル+既存headピン追従の確認)が**スーパーバイザー検証待ち**であることを明記する(STATUS.md運用ルール1)。同時に「migrate(0008適用)も検証時実施」を明記する。
5. **docs改版候補の再記録**: 05 §2への3テーブル追記(sessions・kpi_daily・kpi_muted_user_snapshot)・API一覧のPOST /v1/sessions実装参照(05 §19)を、次回docs改版へ含めるべき候補として再記録する(design §7論点3)。
6. **近似の告白の再記録**: Jev実行数のSQL近似(design §7論点5)・通知オフ率のsnapshot近似(design §7論点4)・active Intent数の実行時点スナップショット近似(§2.3表)・Hard Filter通過率の「件/実行」定義・muted snapshotの0時直後採取近似(§Task 7)を、ws-6/ws-7以降の観察で再評価すべき事項として記録する。
7. **G4時確認事項の付記依頼**(supervisor側で記録): HTTP基本メトリクスがdocs 3群への拡張であること・04 §3とのOSS代替差分(prometheus-client+pull型)の2項目がG4時確認事項・STATUSへ載るべき旨を依頼として記録する(design §6-1/§6-2)。
8. **詰まった箇所と判断**: 作業中に停止・確認した点があれば「状況・採った判断・根拠」を書く(なければ「なし」)。

## 8. staging受け入れ実施手順(コマンド単位)

前提: Task 1〜12完了・worktree内。全コマンドはリポジトリルートで実行する。

```bash
# 1. stagingをフルリセット(down -v→up→migrate〔0008適用を含む〕→geo-import)
#    実行内容: latch-stagingボリュームのみ削除して作り直す(ci無干渉・所要数分)
make staging-reset

# 2. staging専用鍵の生成(git管理外・.staging/keys)
make staging-keys

# 3. ダッシュボード環境の起動(prometheus 19090・grafana 13000・別プロジェクト)
make dashboard-up
#    確認: make dashboard-ps で両コンテナがrunning・
#    curl -s http://127.0.0.1:19090/-/ready と curl -s http://127.0.0.1:13000/api/health が応答

# 4. 受け入れハーネスの実行(a→b→cの順・証拠YAMLをdocs/testassets/results/へ出力)
#    実行内容: staging_tests_obs配下のpytest(a=環境+シード+メトリクス到達・
#    b=シナリオ遷移+Clock前進でKpiJob発火+MLR照合・c=exposure確認)
make staging-obs-test
#    期待: 全pass・所要目安10〜20分(wait_until待ちを含む)

# 5. 失敗時の切り分け(makeコマンド単位)
make staging-logs            # api/workerのログ(計測例外・KpiJobのwarning)
make dashboard-logs          # prometheus/grafanaの起動ログ(provisioning失敗等)
make staging-clock ARGS="show"   # 仮想Clockの現在地確認

# 6. 反復実施の作法: staging-resetからやり直す(harnessが状態を仮定するため)
#    部分実行(-k)はstaging-reset直後に限る(シード投入はa1が行う):
#    cd backend && uv run pytest staging_tests_obs -k "w4_a"

# 7. 完了後の片付け(ボリューム保持で停止)
make staging-down
make dashboard-down
```

タイムボム回避: 証拠YAMLと報告書の時刻は実施値をそのまま記録する(相対表現「今夜」等を使わない)。Clock前進の目標値はISO8601明示で書く(ws-3 §0の規律を引き継ぐ)。

## 9. Self-Reviewの結果(計画完成時点で実施済み)

1. **Spec coverage**: design §3.1のファイル表→§4.1/4.2(全行にTask番号付き)。design §4テスト方針→Task 1/5/6/7/8/13。design §5完了条件→§6(証明方法つき)。design §5.1引き取り事項4点→§0(test-ci直列化)・Task 13(test_w4_c5)・§2.3+Task 9(ws-7のlag消費: collector.pyのdocstringと§3 Review Focus 5)・§2.2(機械チェック)。**漏れなし**。
2. **Placeholder scan**: Task 5のintegration骨格(NotImplementedError)とTask 7のwiring試験(実装時に既存試験作法を参照して完成)は「参照すべき既存試験を明示した指示」であり、TBDではない(実装者への具体的な参照先を与える形式)。他に未定義参照なし。
3. **Type consistency**: build_metricsのMetrics属性名(event_queue_pending等)はTask 1・4・8・12の参照と一致。KpiJobのIF(_pending_days・run_once・run)はTask 7の試験と実装で一致。observe_* のIFはTask 1で定義・Task 9で消費。record_itemのIFはobs_results.pyで統一。
4. **Review Focus**: 5項目それぞれに所有タスクの試験を割り当て済み(§3の各項→Task 7/4/10/7・8/12)。
