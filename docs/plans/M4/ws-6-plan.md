# M4 ws-6(性能負荷試験+総当たり検出の器)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** docs/plans/M4/ws-6-design.md §1.1どおり、負荷条件下で性能5目標(10 §4.1)を測定する器 — stub遅延のp50/p95分布化・層別/初期判定E2E/通知のヒストグラム3本追加・自作asyncio注入器(loadgen)・load環境のPrometheus接続 — と、総当たり母集合のDB直接投入器(massload)を作る。10 §4.2の1万→5万→10万段階観測を手順(§8)として成立させ、G4完了条件「非機能試験グリーン」の証拠採取を可能にする。

**Architecture:** stub遅延の分布化は`_delay_ms`1関数の差し替え+新env 4本で完結し・gateway.pyはstub構築点2箇所への引数追加のみ(応答契約・既存固定ms envは無変更)。計測はobs/metrics.pyへヒストグラム3本とhttp bucket追加のみ・挿入点は既存観測(observe_retrieval・observe_latch_score)の隣の横流し。初期判定E2Eの起点・投入経路・初回提案の3条件はLatchEngineが提案確定時にmatch_eventsのUNIQUE索引照合で判定する(worker側の状態保持を作らない)。注入器は`backend/src/latch/loadgen/`へ新設: 時刻駆動の決定的スケジューラ(オープンモデル)・httpxクライアント・母集合投入・Prometheus照合と証拠YAML出力。依存追加はゼロ(既存httpx+asyncpg+pyyamlのみ)。

**Tech Stack:** Python 3.13・FastAPI・SQLAlchemy(asyncpg)+asyncpg(既存)・httpx(既存>=0.27・追加依存ゼロ)・prometheus-client(既存)・Prometheus v3.0+Grafana OSS 11.4(ws-4既存・dashboard JSONは無変更)。テストは既存依存のみ。

**Spec:** docs/plans/M4/ws-6-design.md(agent1・コミット7f8cc75)。本計画はdesign §5のsupervisor承認8件(すべて2026-10-04裁定済み・§6「未解決の論点なし」)に従う。designに対する本計画の読み替えは§2.1に一覧する(計画裁量の決定9件・いずれも報告書への記録義務つき)。実装詳細の確定指示(design §2.3・§5-7)は§2.4で確定する。

---

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m4-ws-6`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。
- **worktreeの基点はmain(head=6a0f943)**: ws-4・ws-5はマージ済み・**alembic head=0008・test-ci 1664 passedのmain**から作る。**本単位はマイグレーションを追加しない**(design冒頭の前提)。worktree作成後、まず `make setup`(`cd backend && uv sync`)で `.venv` を構築する。**依存追加なしの単位**(uv.lockは更新しない)。
- **毎コミットの検査**: worktree内で**毎コミット `make lint` と `make test` がグリーン**であることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **共有ci-dbへのtest-ci/migrate実行はスーパーバイザー検証時のみ(STATUS.md運用ルール1)**: 本単位はマイグレーションを追加しないが、integration試験(test_loadgen_massload_ci.py)は実DBを伴うため・**agent3は `make test-ci` も `make migrate`(ci宛)も実行しない**。実装中の開発検証はunit試験(`make test`)+collect-only確認で進める。test-ciの実行はスーパーバイザー検証時に直列で行う。**報告書の該当完了条件には「test-ci: スーパーバイザー検証待ち」と記録してよい**(必須記録)。並走単位は本計画時点で存在しない。
- **負荷試験・総当たりの実施はスーパーバイザー検証時**(実環境試験=手順証拠が本体・design §4.2): agent3は実施コード(loadgen・compose・Makefile)までを作り・**load環境の起動・ci一時停止・試験実行・ci復帰はしない**。実施は§8の手順でスーパーバイザーが行う。**`make down` をagent3は実行しない**(ci常設を止める操作は検証時の明示手順)。
- **python / pipコマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **Clock契約・実時間参照の規律**: `core/clock.py` は1行も変更しない。新規コードで時刻参照はすべてClock経由にする(C2)。`time.time` / `time.monotonic` / `time.sleep` / `datetime.now` はsrc/latch配下で禁止(test_arch_no_direct_time.py)・**新規loadgenも対象**。注入器の実行時刻と収束待ちはClock(SystemClock)経由で取る。**テストコード内の実時間測定(perf_counter)と実時間待ち上限(harnessのwait_until等)は制限外**(既存test_llm_factory.py・test_stub.pyと同一作法)。stub遅延の`asyncio.sleep`は待機であり時刻参照ではない(既存stub.pyの規律を引き継ぐ)。
- **固定値の遵守**: 設計の採用判断(自作asyncio注入器・決定的ハッシュ割付50/44/6・新env 4本・追加メトリクス3本+http bucket追加のみ・総当たり母集合=DB直接投入・load接続=staging同型ネットワーク参加・総当たりはレイテンシ注入なし・ci停止/復帰はMakefileに組み込まない・HTTP bucket値§2.3・perf区間設計§2.4・massload列設計§2.5)は固定値。**変更した場合は必ず報告ファイルに「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。メッセージはConvention Commits形式(`feat:` `test:` `chore:` 等)で、末尾に `Co-Authored-By: Claude Code <noreply@anthropic.com>` を付ける。
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| 負荷条件: Intent保存API 50 rps・Match Event 100イベント/秒のバースト。Jevはレイテンシ注入(p50 2秒/p95 5秒=フォールバックLLM切替時のレイテンシ上限を検証する条件)・Parserはレイテンシ注入(p50 1秒/p95 2.5秒) | 10 §4.1 |
| 性能5目標(絶対値合格): ①Intent保存 p95 500ms ②Parser構造化プレビュー p95 3秒・p99 8秒(POST /v1/intents/parseのレイテンシ分布) ③Candidate Retrieval p95 1秒(Layer 1〜2) ④初期LATCH判定 p95 10秒(Event発火(active化の保存コミット)から通知記録まで・初回投入Event〔active作成またはdraft→active化〕経由の初回提案) ⑤通知は判定後5秒以内(LATCH Engine確定からFCM記録まで) | 10 §4.1 |
| 層別予算のトレース分離: Embedding ≤2秒 / Layer 1〜3 ≤1秒 / Jev ≤5秒 / Layer 5+通知 ≤2秒のどの区間が予算を超えたか特定できること。投入経路はトレース属性で区別して記録(ローカル代替=ヒストグラムのroute label) | 10 §4.1・12 §7 |
| 総当たり検出: Active Intent数を1万→5万→10万と段階的に増やし・各段階で同一のイベント注入(新規Intent作成100件)。観測は1イベントあたりのVector Retrieval件数・判定レイテンシ・Jev実行回数。合格=処理件数とレイテンシが急増しない・Vector Retrieval件数がK_v=50以内でIntent数と無関係に安定 | 10 §4.2 |
| load環境: 隔離環境(別プロジェクトlatch-load・28000系ポート・独立DB/Redis)。Workerオートスケールは手動scale読み替え(台数の段階変更=stop/start)と確定済み | 10 §1・ws-1 design §2.3 |
| 時刻参照はすべてClock経由・load composeは`LATCH_CLOCK_MODE: controlled`。K_v・K_c・K_j等の運用パラメータはゲート回避のために変えない | 10 §1・04 §5・12 §7 |
| スタブは決定的: LLM呼び出しはテストモード(記録済み応答スタブ+レイテンシ注入)で決定的にする。乱数を持ち込まない | 10 §1 |
| 証拠: 試験ID・実施日・環境・入力条件・合否とともにdocs/testassets/results/へ保管。非機能試験はリリース前に1回 | 10 §5 |
| 負荷試験測定中のci一時停止: make down(ボリューム保持)→load環境で試験→load-down→make upで復帰。同一ホストのリソース競合が絶対値合格へノイズを乗せるため | STATUS運用ルール6・ws-1 design §2.6 |
| レート制限(負荷用ユーザー設計の制約): Active 5件・作成20件/日・更新6回/時・API 60req/分/ユーザー。制限は源流保護の本体であり試験用に外せない(母集合投入をAPI経由にしない根拠) | 08 §5.4 |
| 測定はレイテンシ分布による。ローカル代替=Prometheus+histogram(ws-4で承認済み=G4時確認事項2) | 04 §7・G4時確認事項2 |
| 「急増しない」の量的合格線はdocs未規定。証拠の形式(段階比較表+増幅率参考値+Vector件数≤50の機械判定)を固定し・量的線の新設は10 §6のdocs改版が前提・正式判定はG4の人間側 | design §2.7・§6記録事項1 |
| 初回投入Event=active作成またはdraft→active化(06 §9-0「初回投入は作成種」)。draft→active化は作成Eventと同一の即時処理(窓なし) | 10 §4.1・06 §9-0 |
| Layer 1〜2の対象条件(active条件): status='active'・embedding非null・geo非null・時間交差・ペア予算≥500(または双方NULL)・users.consent_at非NULL・ブロックなし・飲酒年齢・2∈[min,max] | 06 §2・layer1.py LAYER1_WHERE |
| 正規化テキストはraw_text不使用・embeddingは入力テキスト由来の決定的7次元ベクトル(stub) | 07 §3・embedding_text.py・stub.py `_stub_vector` |
| expires_atの既定規則=time.start+3時間に最も近い未来候補(今夜23:30/明日12:00/明日23:30/now+72h) | 03 §3(FR-13)・completion.py `nearest_expires_at` |
| 証拠YAMLの器と照合作法はobs_results.py(ws-4)が前例。Prometheus照合は/api/v1/query・scrape周期は15秒 | ws-4 staging_tests_obs |

## 2. 設計確定値と計画時の機械検証

### 2.1 読み替え済み確定値(designからの差分・計画裁量の決定)

| # | designの記載 | 本計画の値 | 根拠・記録義務 |
|---|---|---|---|
| 1 | §2.3「draft→active化の起点候補はupdated_at — 後続処理で更新されない列であることを実装の既存起点読取と突合して計画書で確定」 | **起点列=match_events.created_at(created種・source_intent_id=起点・payload->>'version'一致)**。intentsテーブルに「active化コミット時刻」を残す列は存在せず・updated_at候補は**不採用**(embedding書込みが `SET embedding=..., updated_at = :now` で更新する — backend/src/latch/worker/embedding.pyの_UPDATE_EMBEDDING実突合済み)。match_events.created_atはAPI側が遷移tx内のClock値でINSERTするため「Event発火(active化の保存コミット)」そのものであり・**active作成ではintents.created_atと同一値**・draft→active化ではactive化コミット時刻になる(intents/service.pyの_create・_update_draft内insert_match_eventが同一now変数を使用する実突合済み)。designの「active作成=intents.created_at・draft→active化=active化コミット時刻」の2列使い分けを1つのDB読取で満たす | 起点列の実突合結果。**報告書に記録する** |
| 2 | §4.1「E2E観測の絞り込み: version==1チェーンかつ初回提案のときだけobserve」 | **「version==1チェーン」は「created種Eventのversion一致チェーン」へ訂正**。draft→active化は内容変更があるとversionを+1してcreated種Eventを発行する(service.pyの `new_version = row.version + 1 if differs... else row.version` 実突合済み)ため・version==1に絞るとdraft→active化の大部分が観測対象から漏れる。判定基準は「起点Intentのmatch_eventsに・起点version一致のcreated種Eventが存在するか」(10 §4.1の「初回投入Event(active作成またはdraft→active化)」の字義どおり)。チェーンフラグの受け渡し(design §5-7)は**worker側の状態保持を作らず・LatchEngineが提案確定時にDB照合する方式**で確定(_dispatchの非同期並行下でも決定的・UNIQUE索引(event_type, source_intent_id, payload->>'version')でピンポイント) | チェーン判定の実装方式確定。**報告書に記録する** |
| 3 | §2.5「性能試験の負荷構成: ①定常50rps区間(POST /v1/intents・60秒想定=3000件)と②バースト区間(100件を約1秒)」 | perf区間を**3相+収束待ち**に確定する: 相A=parse注入(50rps×60秒=3000件・POST /v1/intents/parse)→相B=保存注入(50rps×60秒=3000件・active作成2980+draft作成10+active化10)→相C=バースト(100件・約1秒)→相D=収束待ち+証拠採取。**parse相を追加する根拠**: 目標②の測定方法は「POST /v1/intents/parseのレイテンシ分布」(10 §4.1の表)であり・parse注入がないと目標②を証明できない。負荷条件(Intent保存API 50 rps+Match Event 100イベント/秒バースト)は相B+相Cで字義どおり維持し・parse相は負荷条件の対象外の独立相である旨を証拠YAMLに明記する(目標②は「保存負荷なし・同一ホスト」の独立相で採取する代替差分としてG4時確認事項2と同型に記録) | 負荷構成の実装確定。**報告書に記録する** |
| 4 | §2.5「総当たりの注入: 作成ユーザーは母集合ユーザーから1ユーザー1件(Active残枠≥1…)」 | **注入ユーザー=perf用の負荷ユーザー(loadusersで作成した1000人)へ読み替え**。generator.pyはusersを100人固定で生成し(--count拡張はintentsのみ)・massloadの母集合では1人がActive 5件を超過するため・母集合ユーザーのActive残枠はすべてゼロ(API経由作成不可・08 §5.4のActive 5件制限)。Active残枠≥1を満たすのは負荷ユーザーのみ(1人あたりperf区間で約3件・bf注入で+1件=上限5件未満) | 注入ユーザーの読み替え。**報告書に記録する** |
| 5 | §3.1「compose.load.yaml: 遅延分布envをapi-common・worker-commonのenvironmentへ(値は試験実行時に指定できるよう既定0で記載とするか・10 §4.1値を既定とするかは計画書で確定)」 | **10 §4.1値をcompose既定とする**(parser 1000/2500・jev 2000/5000)・形式は `${VAR:-既定}` の置換形で・ホスト環境変数で上書き可能。perf区間がmake loadperf 1コマンドで決定的条件になる・bf前の切替はMakefileターゲット `load-stub-flat`(4本を明示0でup -d)で1回行う。既存固定ms env(llm_stub_delay_*)はload composeへ設定しない(0維持) | design §3.1の確定依頼への回答。**報告書に記録する** |
| 6 | §2.6「prometheus.ymlへload targets追記(api1:8000・api2:8000・worker1:9100・worker2:9100)」 | target名は**load側コンテナに付与する別名(alias)「load-api1」「load-api2」「load-worker1」「load-worker2」**へ読み替え。prometheusはstagingとloadの両ネットワークに参加するため・同一コンテナ名(api1等)のDNS解決があいまいになる(Docker DNSはネットワーク越しに同名コンテナを区別しない)ため。compose.load.yamlのapi1/api2/worker1/worker2へ `networks: default: aliases` を追加し・prometheus.ymlのload jobはalias名をscrapeする | 名前衝突の実対策。**報告書に記録する** |
| 7 | §3.1「Makefile: loadperf・loadbf・loadmass・loadusers」 | 加えて**load-migrate・load-geo-import・load-stub-flatの3ターゲットを追加**。load-migrate・load-geo-importはstaging系ターゲットの対称形で・design §4.2手順1「make migrate(load宛)→geo-import」を手順化する。geo-importはPOST /v1/intents(active)のジオコーディング(C5・不通なら422 GEOCODING_FAILED)に必須のため、注入器の動作前提となる | 手順化に必要な対称ターゲット。**報告書に記録する** |
| 8 | §4.1「massload: 少量fixture DBでINSERT+embedding列の検証(unit)」 | massloadは**純関数層(unit)+実DB検証(integration・test-ci)**の2層に分割する。行組み立て(YAML→INSERTパラメータ・structured_data平ら化・embedding決定生成)はDB不要の純関数としてunit試験が所有し・asyncpgによる実INSERTとactive条件照合はintegration(test_loadgen_massload_ci.py・スーパーバイザー検証時のtest-ciで走る)が所有する(ws-4のtest_obs_kpi純関数+test_obs_kpi_job_ci integrationと同一の3層構成) | fixture DBはunit環境に存在しないため。**報告書に記録する** |
| 9 | §3.1「loadgen client.py: token発行〔既存CLI関数の呼出し〕」 | `latch.auth.tools.issue_idp_token(provider=..., subject=..., private_key_pem=...)` を関数として直接呼ぶ(鍵=.staging/keys/idp_test_private.pem・load composeが同一keysをroマウント済み=ws-1)・発行したIdPトークンをPOST /v1/auth/tokenへ渡してaccess_tokenへ回す(auth/routes.pyのTokenRequest契約: `{provider, idp_token}`)。既存CLI(`python -m latch.auth issue-idp-token`)のサブプロセス呼び出しはしない | 既存関数の実突合(backend/src/latch/auth/tools.py:34)。CLI慣行に合わせる |

### 2.2 機械チェックSelf-Reviewの証拠(計画レビュー時点・スーパーバイザー補記2対応)

**(a) テストファイルbasename一意(計画時点の機械確認・2026-10-04)**

本計画の実行結果(現在のmain・コマンド `find backend/tests backend/staging_tests backend/staging_tests_obs -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d`):

```text
(出力なし = 衝突ゼロ)
```

新規unit 6ファイル(test_llm_stub_latency・test_loadgen_schedule・test_loadgen_massload・test_loadgen_collect・test_loadgen_compose_pins・新規integrationのtest_loadgen_massload_ci)は、既存の `backend/tests`(unit/integration全体)・`backend/staging_tests`・`backend/staging_tests_obs` 全ファイルbasenameとの突合で衝突ゼロを確認済み(既存にtest_loadgen*・test_llm_stub_latency*は存在しない。既存の類似名 test_llm_stub_response_settings.py・test_stub.pyとは別名)。**既存ファイルへの追随変更**(test_llm_factory.py・test_settings.py・test_obs_metrics.py・test_obs_pipeline_metrics.py・tests/unit/matching/test_latch_engine.py)はbasenameを変えない。コミット前には次コマンドが空であることを毎回再確認する:

```bash
find backend/tests backend/staging_tests backend/staging_tests_obs -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
# 期待値: 出力なし(空)
```

**(b) ピン試験への影響事前特定(洗い出し表)**

| 既存ピン | 内容 | 本単位の影響 | 追随访問 |
|---|---|---|---|
| tests/unit/llm/test_llm_factory.py `test_llm_settings_are_exactly_eleven_fields` | llm_接頭辞11項目の完全一致集合ピン | **変更が必要**(新env 4本=llm_接頭辞で15項目へ)。関数名を `test_llm_settings_are_exactly_fifteen_fields` へ・集合へ4項目を追加(docstringへ機械的追随の注記)。あわせて `LLM_ENV_VARS`(delenv一覧)へ4変数を追加・`test_llm_settings_defaults` へ4本の既定値assertを追加 | Task 1で実施 |
| tests/unit/test_settings.py | 既存キーの既定値assert・obs_系集合ピン2関数 | **無影響**(新envはllm_接頭辞でobs_集合に含まれない・llm系の既定値ピンはtest_llm_factory側が所有する慣行)。新規ピンの追加はしない | なし |
| tests/unit/test_obs_metrics.py `EXPECTED_SAMPLE_NAMES` | 露出sample名の許可リスト完全一致ピン | **変更が必要**(ヒストグラム3本×(_bucket/_count/_sum)=9名追加)。`EXPECTED_SAMPLE_NAMES` へ9名を追加・新observe3関数の単例差分assertとbucket境界のle集合assertを追加 | Task 3で実施 |
| tests/unit/llm/test_stub.py `test_stub_delay_injects_real_latency`・`test_stub_delay_is_per_system`・`test_stub_zero_delay_by_default` | 固定ms遅延の実測試験(分布env無しの既定構成) | **無影響**(分布envが0の既定構成では従来経路を通る・`_delay_ms`の第2引数は既定Noneで既存呼び出し形を壊さない)。**既存assertは1行も触らない**(design §3.2) | なし(無傷確認のみ) |
| tests/unit/llm/test_gateway_parse.py・test_gateway_embed.py・test_gateway_jev.py・test_gateway_jev_switch.py・test_llm_stub_response_settings.py | gateway構築・stub応答ピン | **無影響**(gateway.pyはstub構築点への引数追加のみでIF変更なし・settings既定値はすべて既存挙動と同一) | なし(無傷確認のみ) |
| tests/unit/test_obs_pipeline_metrics.py `test_observe_retrieval_*` | Worker両経路のretrieval観測増分 | **追随**(retrieval区間のlayer観測追加を増分assertへ追加・2関数にbefore/after差分2行ずつ)・新試験(embedding区間の観測)を同ファイルへ追加 | Task 4で実施 |
| tests/unit/matching/test_latch_engine.py `_patch` | DB部品の記録スタブ・`try_promote`はfake_promoteで置換 | **機械的追随2件**: ①`fake_promote(engine_self, latch_id)` を `(..., latch_id, **kwargs)` へ1行変更(キーワード引数e2e_ctxの受入・既存試験の呼び出し形は不変) ②`log`辞書へ"e2e"キー追加とE2E部品2関数のパッチ追加(**デフォルトは観測なしを返す**ため既存試験のassertは1行も変更しない)。新試験(E2E観測の絞り込み・route・通知区間)を同ファイルへ追加 | Task 5で実施 |
| tests/unit/test_worker.py・test_worker_embedding.py・test_worker_reeval.py・test_events_pipeline.py・test_degraded_e2e.py | Worker DI・embedding・チェーンの既存試験 | **非破壊**(embedding.handleはtry/finallyラップで本体IF不変・latch_engine.handleはIF不変・try_promoteの新引数は既定None付きキーワード引数)。観測追加によるREGISTRY値の増加は差分assertと共存する | なし(無傷確認のみ) |
| tests/unit/test_arch_no_direct_time.py | src/latch配下の time.*・datetime.now・date.today 禁止 | 新規モジュール(loadgen/*・stub追記・latch_engine追記)は**Clock経由のみ**で実装する。loadgenの収束待ち・実行時刻は注入したClock(SystemClock)で取る・**asyncio.sleepは待機であり禁止対象外**(worker/main.py・stub.py既存と同一) | なし(Task 6〜8のコード規律) |
| tests/unit/test_log_allowlist.py | 構造化ログ許可リスト2種 | **無影響**(logallow.pyは無変更・メトリクスはログと統合しない) | なし |
| tests/unit/test_obs_compose_pins.py | dashboard composeの127.0.0.1バインド・ネットワーク構造ピン | **無影響**(dashboard composeへの追記はnetworksへのload-obs追加で・既存ports・既存staging-obs定義は不変)。load compose・prometheus.ymlのピンは本計画の新規試験(test_loadgen_compose_pins.py)が所有する | なし(Task 9) |
| tests/unit/test_compose_ports.py | compose全系のports形状ピン(127.0.0.1明示バインド) | **無影響**(load composeのportsは既存値を1つも変えない・aliases追加のみ) | なし(Task 9で無傷確認) |

**追随访問(本計画が追加する新ピン)**: tests/unit/test_loadgen_compose_pins.py が load composeの分布env既定値と`${VAR:-}`形式・load側aliases・dashboard composeのload-obsネットワーク・prometheus.ymlのload job(targets=load-別名)・Makefile新規ターゲット7本の存在を機械検査する(§Task 9)。tests/unit/test_obs_metrics.pyの許可リストピンがメトリクス追加を2箇所必須化する既存規律を継承する。

**(c) DB干渉対抗策(マイグレーションなし・massload・unit fixture)**

- **本単位はマイグレーションを追加しない**(design冒頭前提)。alembic head=0008・既存headピン(test_geo.py・staging_tests/conftest.py EXPECTED_HEAD="0008")への追随変更は**なし**。
- **massload(loadgen)の投入先はload環境DB(127.0.0.1:25432)で・test-ci対象外**(compose.load.yamlの独立ボリューム・latch-load-db)。ci環境・staging環境のDBへは一切書かない。
- **massloadのINSERTはべき等にする**: usersは `ON CONFLICT (auth_provider, auth_subject) DO NOTHING`(0001のUNIQUE制約どおり)・intentsは **idをmassload側で決定的生成(uuid5・namespace+seed id文字列)して明示INSERTし `ON CONFLICT (id) DO NOTHING`** とする。段階間の再投入(1万→5万→10万)で既存行はスキップされ・母集合は累積的に増える(手順§8・同一--base-datetimeを全段階で使い回す規律とあわせて段階比較の公平性を担保)。
- integration試験(test_loadgen_massload_ci.py)は**少量fixture(20件)を投げ・試験が投入した行を絞ってDELETEする**(massloadに投入元を識別させるため・auth_subjectに試験専用接頭辞 `masstest-` を使い `DELETE FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'masstest-%')` → `DELETE FROM users WHERE auth_subject LIKE 'masstest-%'` の順)。既存テーブルのTRUNCATEや他試験の行への触りはしない(既存integration conftestの作法どおり)。
- **unit試験はDB不使用**(純関数・静的ピンのみ)のため・ci-dbを汚さない。`make test`(unit)は毎コミットで回す。
- **loadgenのseed YAML出力先はgit管理外**(backend/data/loadgen/。既存のgeo資材と同一の方針・決定的に再生成可能なため)。docs/testassets/results/へは証拠YAML(loadgen collectが出力)のみを置く。
- **test-ciでtest_loadgen_massload_ci.pyが走る前提=load環境DBの起動**(make load-up+make load-migrate): make test-ciのpytestは**ホスト実行**(Makefile実突合・コンテナ内ではない)のため・127.0.0.1:25432へ到達できる。スーパーバイザー検証時はci常設とload環境の同時稼働(通常時の共存可・運用ルール6は「測定中」のみ停止)で実行する。

### 2.3 bucket設計の単一真実(metrics.pyの定数表)

histogram_quantileの補間推定が注入値と目標境界で一致するよう、bucketは次の定数で統一する(design §2.2・§2.3)。既存メトリクスのbucket値は**retrieval_vector_items・jev_latency_seconds・scoreの3本は触らない**(design §3.2「既存name/bucketの変更はしない」)。http_request_duration_secondsは**bucket追加のみ**。

| 定数 | 値 | 使うメトリクス | 根拠 |
|---|---|---|---|
| `HTTP_BUCKETS` | (0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 3.0, 5.0, 7.5, 8.0, 10.0) | latch_http_request_duration_seconds(既存・明示bucketへ) | prometheus-client既定DEFAULT_BUCKETSに3.0(目標②のp95 3秒)と8.0(目標②のp99 8秒)を追加した列。**既存値は1つも変えない**(既定値の写しに追加のみ)。0.5(目標①のp95 500ms)は既存どおり含む |
| `LATENCY_BUCKETS` | (0.1, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0) | latch_pipeline_layer_seconds(新)・latch_latch_initial_e2e_seconds(新) | 注入値(p50=1秒・2秒/p95=2.5秒・5秒)と予算境界(Embedding 2秒・Retrieval 1秒・L5+通知 2秒)とE2E目標10秒を含む。E2Eの合成分布(p50≈2秒台/p95≈5秒台)の観測分位点が注入値と一致する位置 |
| `NOTIFY_BUCKETS` | (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0) | latch_notification_latency_seconds(新) | 通知区間(提案確定→push記録)はStubPushSender構成でごく小さい。構造上の大きさを観察し・実FCM時の上限(目標⑤=判定後5秒以内)を推定できる解像度。5秒の目標境界を含む |

層別ヒストグラムのlabel: `layer` = embedding / retrieval / proposal(既存jev_latency_secondsがJevを観測済みで・層別予算4区間の分離が完成する)。E2Eヒストグラムのlabel: `route` = create / activate(投入経路の区別記録・10 §4.1「投入経路をトレース属性で区別して記録」のローカル代替)。通知ヒストグラムはlabelなし。

### 2.4 stub遅延分布の割付と機械検証値(計画時点で実計算済み)

決定的ハッシュ割付(design §2.2案A・supervisor承認2)の実装確定値:

- 割付: sha256(key)の先頭バイト(0〜255)のレンジ3分割 — `slot < 128` → p50値(50.0%)・`128 <= slot < 240` → 中間値(線形補間・43.75%)・`slot >= 240` → p95値(6.25%)。**下位2グループの累積93.75%<95%**のため・nearest-rank定義の95%ランク(ceil(0.95N)番目)は常にp95値グループに含まれる。
- key: parser系統=入力テキストそのもの・jev系統=`f"{intent_a}|{intent_b}"`(2 Intent分の正規化テキスト)。同一入力→同一遅延(10 §1決定性)。
- 有効条件: p50側env > 0のとき分布側へ切替(優先順位「分布env非0 > 固定ms」)。embedding系統は分布を作らない(design §2.2・YAGNI)。

**機械検証の期待値(計画時に実計算済み — agent3がテストを写してFAILしないための固定値)**:

```text
keys = [str(i) for i in range(10000)](固定key集合)
p50=1000 / p95=2500 のとき: counts = {1000: 5058, 1750: 4329, 2500: 613}
  nearest-rank p50(5000番目) = 1000・p95(9500番目) = 2500
p50=2000 / p95=5000 のとき: counts = {2000: 5058, 3500: 4329, 5000: 613}
  nearest-rank p50 = 2000・p95 = 5000(同一割付で比例する)
```

テストはこの固定key集合で実測が期待値と完全一致することを検証する(test_llm_stub_latency.py・Task 2)。理論割付(50/43.75/6.25)は大数の法則で実負荷の分布を近似し・分位点の整合は固定集合で機械保証する。

### 2.5 perf区間・総当たりの注入設計(確定)

**perf区間(§2.1読み替え3・3相+収束待ち)**:

| 相 | 内容 | 件数・時間 | 測定対象 |
|---|---|---|---|
| A parse | POST /v1/intents/parse(50rps・決定的な多様なraw_text) | 3000件・60秒 | 目標②(parse p95 3秒・p99 8秒) |
| B save | POST /v1/intents active作成2980件+draft作成10件+draft→active化10件(50rps) | 3000リクエスト・60秒 | 目標①(保存p95 500ms)・目標③④⑤(パイプライン区間) |
| C burst | POST /v1/intents active作成を約1秒で100件 | 100件 | Match Event 100イベント/秒バースト(10 §4.1負荷条件) |
| D settle | event_queue_pending=0の連続確認(30秒間不変)+安定確認・証拠採取 | 上限600秒 | 収束確認・全分布採取 |

- **負荷ユーザー**: 1000人(loadusersで事前作成・POST /v1/users+POST /v1/users/me/consent・helpers_seed.pyと同一経路)。birth_dateは全員20歳以上(1990-01-01)・display_nameは `LoadNNNN`・auth_subjectは `load-uNNNN`(perfとbfで共用・1人あたりActive約3〜4件=制限余裕)。
- **注入ペイロードは決定的生成**: seq番号から time_start=実行時刻+3時間+seq分(7日上限内・try_promoteの75分ルール安全)・地名=vocab.LOCATIONSの7つから決定的選択・category=drinking/meal/activity分散・raw_text=seq+地名を含む300字内の決定的文。5目標のどれも運用パラメータ(K上限・閾値)は変更しない。
- **draft→active化10件**は相B末尾に置き・E2Eヒストグラムのroute=activateで区別記録する(起草時間を起点に含まない=読み替え1の起点列)。
- **クライアント側応答時間は参考値として証拠YAMLに併記**(測定の主系統はサーバ側ヒストグラム=読み替えなし・design §2.1案A)。

**総当たり(design §2.4・§2.5)**:

- 段階: 母集合を `massload --stage 10000` → `bruteforce` → `--stage 50000` → `--stage 100000` と累積投入(§2.2(c)のべき等INSERT)。
- **注入は負荷ユーザーから100件**(バースト=新規Intent作成100件・正規経路でcreated Event 100件を発行)・読み替え4どおり。
- **総当たり区間はレイテンシ注入なし**で実施(load-stub-flatで4本を0へ切替=読み替え5)。
- 観測の機械判定: Vector件数≤50(Prometheus照合)+段階比較表(Vector件数分布・Retrieval p95・L1通過率・イベントあたりJev回数)+増幅率参考値(段階5倍に対する指標増幅がおおむね整数倍以内)。正式判定はG4の人間側(design §2.7)。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **既存固定ms envとの優先順位の逆転(ci挙動の最も重い回帰)**: 分布envが非0のとき固定msを無視すると既存ciの遅延注入が効かなくなり・逆に固定msが優先されるとload環境で分布が効かなくなる。→ Task 2の `test_stub_prefers_distribution_over_fixed_ms`・`test_stub_uses_fixed_ms_when_distribution_disabled` で両方向をピン。既存test_stub.py・test_gateway_*が無傷であることを毎コミット確認する。
2. **E2E観測の混入防止(目標④の証拠が無意味になる最短経路)**: 更新Event(version>1)由来の再評価・Bucket/catch-up再評価・2回目以降の提案・draft作成時刻を起点に含む経路の4つが観測に混ざると、初期判定のp95が過大に歪む。→ Task 5の4試験(更新Intent不観測・再評価不観測・2回目不観測・route=create/activate分離)でピン。
3. **オープンモデル性の喪失(coordinated omission)**: 注入器が応答完了を待ってしまうと、遅延が大きい区間で投入間隔が伸び・測定が甘くなる。→ Task 6の `test_open_model_does_not_slow_down` で・応答遅延を模擬したモッククライアント下でも送出間隔が保たれることをピン。
4. **母集合がLayer 1の対象条件を満たさない(総当たりの検出力が消える)**: consent_at NULL・geo NULL・expires_at過去・embedding NULLの行はLayer 1から見えず「1万件」でも「0件」と同じ観測になる。→ Task 7のintegration試験(active条件の全列照合・consent_at非NULL・expires_at>now)と純関数試験(平ら化・embedding生成)でピン。
5. **load・staging環境のメトリクス名前衝突**: prometheusが両ネットワークに参加した状態で同一コンテナ名をscrapeすると、targetの帰属が不安定になり・stagingの既存ダッシュボードがloadのseriesで汚れる。→ Task 9の `test_load_targets_use_aliased_names`(targetsがload-別名であること・job名がload系であること)+無傷確認(test_obs_compose_pins.py・test_compose_ports.pyが壊れないこと)でピン。

## 4. ファイル構成(スコープ)

### 4.1 作るもの(新規)

| ファイル | 内容 | Task |
|---|---|---|
| `backend/src/latch/loadgen/__init__.py` | 空パッケージ | 6 |
| `backend/src/latch/loadgen/schedule.py` | 決定的送出時刻列(オープンモデル実行器込み) | 6 |
| `backend/src/latch/loadgen/client.py` | 負荷注入のHTTPクライアント(認証・users・consent・Intent作成・parse・クライアント側応答時間記録) | 7 |
| `backend/src/latch/loadgen/massload.py` | 総当たり母集合のDB直接投入(asyncpg・embedding直書き・Event発行なし) | 7 |
| `backend/src/latch/loadgen/collect.py` | Prometheus照合・perf/総当たりの証拠採取・証拠YAML出力(obs_results.py同型) | 8 |
| `backend/src/latch/loadgen/__main__.py` | CLI: `python -m latch.loadgen users|perf|massload|bruteforce` | 8 |
| `backend/tests/unit/test_llm_stub_latency.py` | stub遅延分布の決定性・分位点(§2.4の固定値)・優先順位 | 2 |
| `backend/tests/unit/test_loadgen_schedule.py` | 送出時刻列・オープンモデル性・エラー集計 | 6 |
| `backend/tests/unit/test_loadgen_massload.py` | 行組み立て純関数の期待値表・決定性・match_events非触りの静的ピン | 7 |
| `backend/tests/unit/test_loadgen_collect.py` | PromQL組み立て・照合結果の組み立て(モック照合) | 8 |
| `backend/tests/unit/test_loadgen_compose_pins.py` | compose・prometheus.yml・Makefileの静的ピン | 9 |
| `backend/tests/integration/test_loadgen_massload_ci.py` | 少量fixture投入→DB照合→自前掃除(integration・supervisor検証時) | 7 |
| `docs/plans/M4/ws-6-report.md` | 報告書(§7の形式) | 10 |

### 4.2 変更するもの(既存・追従)

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/settings.py` | 遅延分布env 4本追記(llm_stub_latency_parser_p50_ms/p95_ms・llm_stub_latency_jev_p50_ms/p95_ms・既定0) | 1 |
| `backend/tests/unit/llm/test_llm_factory.py` | LLM_ENV_VARS+4・既定値assert+4・集合ピン11→15項目(関数名eleven→fifteen) | 1 |
| `backend/src/latch/llm/stub.py` | distributed_delay_ms純関数新設・StubLLMへ分布引数4本・_delay_msへkey引数と分布切替・3呼び出し点でkey渡し | 2 |
| `backend/src/latch/llm/gateway.py` | stub構築点2箇所(build_llm_gateway・build_worker_gateway)へ分布引数4本の追記 | 2 |
| `backend/src/latch/obs/metrics.py` | HTTP_BUCKETS/LATENCY_BUCKETS/NOTIFY_BUCKETS定数・http_request_durationへbucket明示化(値追加のみ)・ヒストグラム3本+observe3関数追加 | 3 |
| `backend/tests/unit/test_obs_metrics.py` | 許可リスト+9名・observe3関数の差分assert・bucket境界assert | 3 |
| `backend/src/latch/worker/embedding.py` | handle全体をtry/finallyで包みEmbedding区間をobserve(本体ロジック無変更) | 4 |
| `backend/src/latch/worker/main.py` | _run_matching・_run_direct_pipelineのrun_candidate_retrieval前後にRetrieval区間observe | 4 |
| `backend/tests/unit/test_obs_pipeline_metrics.py` | 既存2試験にlayer観測の差分assert追加・embedding観測試験を追加 | 4 |
| `backend/src/latch/worker/matching/latch_engine.py` | E2eObsContext・起点読取SQL2本・_evaluate_pair戻り値化とE2Eコンテキスト組み立て・try_promoteへe2e_ctxキーワード引数(既定None)・L5+通知区間・E2E・通知区間のobserve | 5 |
| `backend/tests/unit/matching/test_latch_engine.py` | _patchへE2E部品パッチ追加(デフォルト観測なし・既存assert無変更)・fake_promoteへ**kwargs1行・E2E絞り込み試験4件+通知区間試験1件を追加 | 5 |
| `compose.load.yaml` | backend-envへ分布env 4本(`${VAR:-10 §4.1値}`形式)・api1/api2/worker1/worker2へload-別名aliases | 9 |
| `compose.dashboard.yaml` | networksへload-obs external追加・prometheusへ参加・コメントのws-6予約を消化 | 9 |
| `docker/prometheus/prometheus.yml` | latch-load-api・latch-load-workerの2 job追加(targets=load-別名) | 9 |
| `Makefile` | loadusers・loadperf・loadbf・loadmass・load-stub-flat・load-migrate・load-geo-importの7ターゲット追加(.PHONY更新・LOAD_DB_URL定数) | 9 |
| `backend/tests/integration/` 既存追随 | なし(マイグレーションなし・headピンは不変) | — |

### 4.3 触らないもの(禁止・スコープ外と判断する基準)

- **compose.yaml(ci)・compose.staging.yaml・ci環境のMakefileターゲット**: 触らない。分布env既定は0(無効)のためci/staging挙動は不変。
- **パイプライン本体のロジック**(stage1・EmbeddingWorkerの本体・JevWorker・GroupEngine・guard・sweeper・KpiJob・ReevalRunner等): 観測は横流しのみ。Jev・GroupEngineの関数は1行も変更しない(latch_engine.py内の変更のみで層別・E2E・通知観測が完結する)。
- **既存固定遅延env(llm_stub_delay_*_ms)・StubLLMの応答契約・stub応答値env(llm_stub_jev_noul/score)・Gatewayのtimeout・breaker**: 既存試験・ci挙動の互換のため無変更。既存env値はload composeでも設定しない(0維持)。
- **既存メトリクスの定義**(name・label・bucket): latch_http_request_duration_secondsはbucket追加のみ(既存値不変・§2.3)・retrieval_vector_items・jev_latency_seconds・score・gauge/counter系は無変更。dashboard JSON(docker/grafana/dashboards/*.json)は**無変更**(job=ラベルでload seriesは既存パネルに入らず・証拠採取はloadgen collectが所有する)。
- **JevCostGuard・CircuitBreaker・KpiJob・push_stub_delay_ms・運用パラメータ**(K_v・K_c・K_j・D-08上限・閾値0.60・debounce 10秒等): ゲート回避のために変えない(10 §1・12 §7)。
- **DBスキーマ**(マイグレーションなし)・staging_tests系(ws-3/ws-4資産)・frontend・prototype。
- **docs本文**(本単位でdocs改版なし・改版候補もdesign §7に記載なし)。
- **スコープ外と判断する基準**: ①性能5目標の絶対値合格判定(実施・判定はスーパーバイザー検証時の手順§8+G4の人間側) ②「急増しない」の量的合格線の新設(10 §6のdocs改版が前提) ③alert規則・alertmanagerの構成(ws-4が器を提供済み・消費は本単位でもしない) ④KpiJobへの恒久値化(E2E等のp95をkpi_dailyへ書かない・design §6記録事項2) ⑤Worker台数の段階変更(scale読み替え・引用#5は「手動scale読み替えと確定済み」で本単位の手順では2台固定)。

## 5. タスク(実装ステップ・TDD)

タスク実行の共通手順(各Taskに明記しない): 作業はworktree内・コミット前に `make lint && make test` グリーン・basename一意コマンド空・`git status --short` で差分確認。pytestの実行は `cd backend && uv run pytest tests/unit/llm/test_llm_stub_latency.py -v` の形。

---

### Task 1: settingsへ遅延分布env 4本追加とllmピン追随

**Files:**
- Modify: `backend/src/latch/settings.py`(遅延分布4本追記)
- Modify: `backend/tests/unit/llm/test_llm_factory.py`(LLM_ENV_VARS+4・既定値assert+4・集合ピン15項目化)

**Interfaces:**
- Consumes: なし
- Produces: `Settings.llm_stub_latency_parser_p50_ms: int = 0`・`llm_stub_latency_parser_p95_ms: int = 0`・`llm_stub_latency_jev_p50_ms: int = 0`・`llm_stub_latency_jev_p95_ms: int = 0`(env名はLATCH_接頭辞で自動写像。Task 2のStubLLM・Task 9のcompose loadが消費)

- [ ] **Step 1: 失敗する試験を書く(test_llm_factory.pyへの追随)**

`backend/tests/unit/llm/test_llm_factory.py` を3箇所変更する。

変更1: `LLM_ENV_VARS` へ4変数を追加(現行10変数の末尾に続ける):

```python
LLM_ENV_VARS = (
    "LATCH_LLM_MODE",
    "LATCH_LLM_STUB_DELAY_PARSER_MS",
    "LATCH_LLM_STUB_DELAY_EMBEDDING_MS",
    "LATCH_LLM_STUB_DELAY_JEV_MS",
    "LATCH_LLM_STUB_JEV_NOUL",
    "LATCH_LLM_STUB_JEV_SCORE",
    "LATCH_LLM_STUB_LATENCY_PARSER_P50_MS",
    "LATCH_LLM_STUB_LATENCY_PARSER_P95_MS",
    "LATCH_LLM_STUB_LATENCY_JEV_P50_MS",
    "LATCH_LLM_STUB_LATENCY_JEV_P95_MS",
    "LATCH_ANTHROPIC_API_KEY",
    "LATCH_ANTHROPIC_BASE_URL",
    "LATCH_TYPESAFE_API_KEY",
    "LATCH_TYPESAFE_BASE_URL",
)
```

変更2: `test_llm_settings_defaults` のassert群へ4行を追加(`llm_stub_jev_score == 2.0` の後):

```python
    assert s.llm_stub_latency_parser_p50_ms == 0  # M4 ws-6(10 §4.1分布・既定0=無効)
    assert s.llm_stub_latency_parser_p95_ms == 0
    assert s.llm_stub_latency_jev_p50_ms == 0
    assert s.llm_stub_latency_jev_p95_ms == 0
```

変更3: `test_llm_settings_are_exactly_eleven_fields` を `test_llm_settings_are_exactly_fifteen_fields` へ改名し・集合へ4項目を追加:

```python
def test_llm_settings_are_exactly_fifteen_fields(monkeypatch):
    # Review Focus #5: timeout・failフラグのenv経路を作らない(design §2.4・§2.6)。
    # LLM系設定はこの15項目のみであることを機械検査する(base_urlはdesign §3.2の
    # supervisor承認済み拡張・ws-6のANTHROPIC_BASE_URL汚染対策。
    # gemini_api_keyはM2 ws-2・typesafe_api_key/base_urlはM2 ws-5の機械的追随。
    # llm_stub_jev_noul/scoreはM4 ws-3。llm_stub_latency_*4本はM4 ws-6
    # (10 §4.1のstub遅延分布・p50_ms>0で有効)の機械的追随)。
    _clean_settings(monkeypatch)
    llm_fields = {f for f in Settings.model_fields if f.startswith("llm_")}
    assert llm_fields == {
        "llm_mode",
        "llm_anthropic_api_key",
        "llm_anthropic_base_url",
        "llm_gemini_api_key",
        "llm_typesafe_api_key",
        "llm_typesafe_base_url",
        "llm_stub_delay_parser_ms",
        "llm_stub_delay_embedding_ms",
        "llm_stub_delay_jev_ms",
        "llm_stub_jev_noul",
        "llm_stub_jev_score",
        "llm_stub_latency_parser_p50_ms",
        "llm_stub_latency_parser_p95_ms",
        "llm_stub_latency_jev_p50_ms",
        "llm_stub_latency_jev_p95_ms",
    }
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/llm/test_llm_factory.py -v`
Expected: FAIL(llm_stub_latency_*が存在しない)

- [ ] **Step 3: settings.pyへ追記する**

`llm_stub_jev_score` の直後(`--- 認証` コメントの前)へ:

```python
    # --- レイテンシ注入の分布(M4 ws-6・10 §4.1)---
    # stub遅延のp50/p95分布注入(決定的ハッシュ割付・stub.py
    # distributed_delay_ms)。p50_ms>0で有効・優先順位は「分布 > 既存固定ms
    # (llm_stub_delay_*)」。既定0=無効(ci/test-ci既存挙動不変)。load compose
    # は10 §4.1値を既定とする(compose.load.yaml・§2.1読み替え5)。embedding
    # 系統は分布を作らない(10 §4.1に注入規定なし・YAGNI)
    llm_stub_latency_parser_p50_ms: int = 0
    llm_stub_latency_parser_p95_ms: int = 0
    llm_stub_latency_jev_p50_ms: int = 0
    llm_stub_latency_jev_p95_ms: int = 0
```

- [ ] **Step 4: 試験が通ることを確認してコミットする**

Run: `cd backend && uv run pytest tests/unit/llm/test_llm_factory.py tests/unit/test_settings.py tests/unit/test_llm_stub_response_settings.py -v`
Expected: PASS(集合ピン・既定値・既存stub応答ピンがすべて不変)

```bash
git add backend/src/latch/settings.py backend/tests/unit/llm/test_llm_factory.py
git commit -m "feat: settingsへstub遅延分布env 4本追加(既定0=無効)とllm集合ピン15項目化(M4 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: stub遅延の決定的ハッシュ分布化とgateway構築点追記

**Files:**
- Modify: `backend/src/latch/llm/stub.py`(distributed_delay_ms純関数・StubLLM分布引数4本・_delay_ms切替・3呼び出し点)
- Modify: `backend/src/latch/llm/gateway.py`(stub構築点2箇所へ引数追記)
- Create: `backend/tests/unit/llm/test_llm_stub_latency.py`

**Interfaces:**
- Consumes: `Settings.llm_stub_latency_*`(Task 1)
- Produces: `latch.llm.stub.distributed_delay_ms(p50_ms: int, p95_ms: int, key: str) -> int`(モジュール純関数・Task 2の試験が消費)。`StubLLM(latency_parser_p50_ms=, latency_parser_p95_ms=, latency_jev_p50_ms=, latency_jev_p95_ms=)` キーワード引数4本(既定0・既存呼び出し非破壊)。`StubLLM._delay_ms(system: str, key: str | None = None) -> int`(第2引数は既定付きで既存呼び出し非破壊)。**分布はparser・jev系統のみ**(embeddingは既存固定msのみ・design §2.2 YAGNI)

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/llm/test_llm_stub_latency.py`:

```python
"""stub遅延分布の決定的ハッシュ割付(M4 ws-6・design §2.2案A・10 §4.1)。

分位点の期待値は計画時に実計算した固定key集合の値(計画書 §2.4)。
割付は50%→p50値・43.75%→中間値・6.25%→p95値で・下位累積93.75%<95%の
ためnearest-rankのp95ランクは常にp95値グループに含まれる。
"""

from latch.llm.stub import StubLLM, distributed_delay_ms

# 計画書 §2.4の機械検証値(固定key集合・実計算済み)
_KEYS_10K = [str(i) for i in range(10000)]
_EXPECTED_COUNTS_1000_2500 = {1000: 5058, 1750: 4329, 2500: 613}


def test_distributed_delay_is_deterministic():
    """同一key→同一遅延(10 §1決定性)。"""
    assert distributed_delay_ms(1000, 2500, "same-input") == distributed_delay_ms(
        1000, 2500, "same-input"
    )


def test_distributed_delay_percentiles_match_targets():
    """固定key集合10万点弱の分位点が注入値と一致(§2.4の機械検証値)。"""
    delays = sorted(distributed_delay_ms(1000, 2500, k) for k in _KEYS_10K)
    counts = {}
    for d in delays:
        counts[d] = counts.get(d, 0) + 1
    assert counts == _EXPECTED_COUNTS_1000_2500
    assert delays[4999] == 1000  # nearest-rank p50 = 1000ms
    assert delays[9499] == 2500  # nearest-rank p95 = 2500ms


def test_distributed_delay_jev_targets_proportionally():
    """p50=2000/p95=5000も同一割付で比例(§2.4の2例目・load既定値)。"""
    delays = sorted(distributed_delay_ms(2000, 5000, k) for k in _KEYS_10K)
    counts = {}
    for d in delays:
        counts[d] = counts.get(d, 0) + 1
    assert counts == {2000: 5058, 3500: 4329, 5000: 613}
    assert delays[4999] == 2000
    assert delays[9499] == 5000


def test_stub_prefers_distribution_over_fixed_ms():
    """分布env非0 > 既存固定ms(優先順位・design §2.2案A)。"""
    stub = StubLLM(
        delay_jev_ms=50,
        latency_jev_p50_ms=2000,
        latency_jev_p95_ms=5000,
    )
    # 固定50msではなく分布割付の値(2000/3500/5000のいずれか)を返す
    assert stub._delay_ms("jev", "a|b") in (2000, 3500, 5000)


def test_stub_uses_fixed_ms_when_distribution_disabled():
    """分布0のときは既存固定ms経路を維持(ci挙動不変の根拠)。"""
    stub = StubLLM(delay_jev_ms=50)
    assert stub._delay_ms("jev", "a|b") == 50
    assert stub._delay_ms("jev") == 50  # keyなし呼び出し(既存形)


def test_stub_zero_delay_when_both_disabled():
    stub = StubLLM()
    assert stub._delay_ms("jev", "a|b") == 0
    assert stub._delay_ms("intent_parser", "t") == 0


def test_stub_judge_key_is_pair_of_texts():
    """jevの分布keyはペアテキスト(同一ペア→同一遅延)。"""
    stub = StubLLM(latency_jev_p50_ms=2000, latency_jev_p95_ms=5000)
    key_a = "intent-A-text|intent-B-text"
    key_b = "intent-A-text|intent-B-text"
    assert stub._delay_ms("jev", key_a) == stub._delay_ms("jev", key_b)


def test_stub_parser_key_is_input_text():
    """parserの分布keyは入力テキスト。"""
    stub = StubLLM(
        latency_parser_p50_ms=1000, latency_parser_p95_ms=2500
    )
    assert stub._delay_ms("intent_parser", "user raw text") in (1000, 1750, 2500)


def test_stub_embedding_has_no_distribution():
    """embeddingは分布を作らない(10 §4.1に注入規定なし・YAGNI)。"""
    stub = StubLLM(
        delay_embedding_ms=30,
        latency_parser_p50_ms=1000,
        latency_parser_p95_ms=2500,
        latency_jev_p50_ms=2000,
        latency_jev_p95_ms=5000,
    )
    assert stub._delay_ms("embedding", "any text") == 30


async def test_stub_judge_delay_runs_through_distribution():
    """judge呼び出しが分布遅延で決定的に完了する(実時間のassertは既存
    test_stub.pyの実測試験が所有するため・ここでは例外なし完了のみ)。"""
    stub = StubLLM(
        delay_jev_ms=0,
        latency_jev_p50_ms=30,
        latency_jev_p95_ms=60,
    )
    # 分布値(30/45/60ms)のいずれかで2回とも例外なく完了する
    await stub.judge("a", "b")
    await stub.judge("a", "b")
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/llm/test_llm_stub_latency.py -v`
Expected: FAIL(ImportError: distributed_delay_msが存在しない)

- [ ] **Step 3: stub.pyへ分布を実装する**

変更1: モジュール定数と純関数を `_stub_vector` の直後に追加:

```python
# 決定的ハッシュ割付のレンジ境界(256スロット・ws-6 design §2.2案A)。
# 0〜127→p50値(50.0%)・128〜239→中間値(43.75%)・240〜255→p95値(6.25%)。
# 下位2グループの累積93.75%<95%のため・nearest-rank定義の95%ランク
# (ceil(0.95N)番目の標本)は常にp95値グループに含まれる
_DELAY_SPLIT_P50 = 128
_DELAY_SPLIT_MID = 240


def distributed_delay_ms(p50_ms: int, p95_ms: int, key: str) -> int:
    """決定的ハッシュ割付の遅延ms(10 §4.1のp50/p95分布再現・M4 ws-6)。

    sha256(key)の先頭バイトのレンジで50%/43.75%/6.25%へ振る。中間値は
    線形補間。同一key→同一遅延(10 §1「スタブで決定的にする」)。
    理論分布としてnearest-rankの50分位点=p50値・95分位点=p95値。
    機械検証はtests/unit/llm/test_llm_stub_latency.py(固定key集合で計画時
    実計算済み: keys=[str(i) for i in range(10000)]・p50=1000/p95=2500で
    counts={1000: 5058, 1750: 4329, 2500: 613}・p50ランク=1000・p95ランク=2500)。
    """
    slot = hashlib.sha256(key.encode()).digest()[0]
    if slot < _DELAY_SPLIT_P50:
        return p50_ms
    if slot < _DELAY_SPLIT_MID:
        return p50_ms + (p95_ms - p50_ms) // 2
    return p95_ms
```

変更2: `StubLLM.__init__` の引数と保存(`delay_jev_ms: int = 0,` の後へ4行・保存は `self._delay_jev_ms` の後):

```python
        latency_parser_p50_ms: int = 0,
        latency_parser_p95_ms: int = 0,
        latency_jev_p50_ms: int = 0,
        latency_jev_p95_ms: int = 0,
```

```python
        # 分布遅延のp50/p95(M4 ws-6)。p50_ms>0で有効・既存固定msより優先
        self._latency_parser_p50_ms = latency_parser_p50_ms
        self._latency_parser_p95_ms = latency_parser_p95_ms
        self._latency_jev_p50_ms = latency_jev_p50_ms
        self._latency_jev_p95_ms = latency_jev_p95_ms
```

変更3: `_delay_ms` と `_apply_delay` を入れ替える(docstring約束「p50/p95分布版(M4)への差し替えはこの1関数で完結する」の実装):

```python
    def _delay_ms(self, system: str, key: str | None = None) -> int:
        """系統→遅延ms。分布設定非0(p50_ms>0)のとき決定的ハッシュ割付へ
        切替する(優先順位: 分布 > 固定ms・M4 ws-6 design §2.2案A)。keyは
        呼び出し入力由来の決定的文字列(parser=テキスト・jev=ペアテキスト)。
        embeddingは分布を作らない(10 §4.1に注入規定なし・YAGNI)。
        """
        if key is not None:
            cfg = {
                "intent_parser": (
                    self._latency_parser_p50_ms,
                    self._latency_parser_p95_ms,
                ),
                "jev": (self._latency_jev_p50_ms, self._latency_jev_p95_ms),
            }.get(system)
            if cfg is not None and cfg[0] > 0:
                return distributed_delay_ms(cfg[0], cfg[1], key)
        return {
            "intent_parser": self._delay_parser_ms,
            "embedding": self._delay_embedding_ms,
            "jev": self._delay_jev_ms,
        }[system]

    async def _apply_delay(self, system: str, key: str | None = None) -> None:
        delay_ms = self._delay_ms(system, key)
        if delay_ms > 0:
            # 待機であり時刻参照ではない(arch testの禁止対象外。既存規律)
            await asyncio.sleep(delay_ms / 1000)
```

変更4: 3呼び出し点へkey渡し(既存行の引数変更のみ):

```python
    async def complete_structured(self, text: str, current_date: date) -> dict:
        await self._apply_delay("intent_parser", text)
```

```python
    async def embed(self, text: str) -> list[float]:
        await self._apply_delay("embedding")
```

(embedはkeyなし=既存固定msのみ。変更なし・明記のため置いた)

```python
    async def judge(self, intent_a: str, intent_b: str) -> dict:
        await self._apply_delay("jev", f"{intent_a}|{intent_b}")
```

- [ ] **Step 4: gateway.pyのstub構築点2箇所へ追記する**

`build_llm_gateway` と `build_worker_gateway` の両方の `StubLLM(...)` 構築(現在・3系統とも同じ4行引数)へ・`delay_jev_ms=...` の行の後に4行を追加する(両関数とも同一):

```python
    stub = StubLLM(
        delay_parser_ms=settings.llm_stub_delay_parser_ms,
        delay_embedding_ms=settings.llm_stub_delay_embedding_ms,
        delay_jev_ms=settings.llm_stub_delay_jev_ms,
        latency_parser_p50_ms=settings.llm_stub_latency_parser_p50_ms,
        latency_parser_p95_ms=settings.llm_stub_latency_parser_p95_ms,
        latency_jev_p50_ms=settings.llm_stub_latency_jev_p50_ms,
        latency_jev_p95_ms=settings.llm_stub_latency_jev_p95_ms,
        jev_response=_stub_jev_response(settings),
    )
```

- [ ] **Step 5: 試験が通ること・既存stub系が無傷であることを確認する**

Run: `cd backend && uv run pytest tests/unit/llm/ -v`
Expected: PASS(test_llm_stub_latency.py 9件+既存test_stub.py・test_gateway_*.py・test_llm_factory.pyがすべて不変)

- [ ] **Step 6: コミットする**

```bash
git add backend/src/latch/llm/stub.py backend/src/latch/llm/gateway.py backend/tests/unit/llm/test_llm_stub_latency.py
git commit -m "feat: stub遅延の決定的ハッシュ分布化(50/44/6割付・p50>0で有効・既存固定msは無変更)(M4 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 層別・E2E・通知ヒストグラム3本追加とhttp bucket拡張

**Files:**
- Modify: `backend/src/latch/obs/metrics.py`(bucket定数3種・http_request_durationのbucket明示化・Metrics属性3本・observe3関数)
- Modify: `backend/tests/unit/test_obs_metrics.py`(許可リスト+9名・observe差分・bucket境界)

**Interfaces:**
- Consumes: なし
- Produces: `build_metrics` が返す `Metrics` に新属性 `pipeline_layer_seconds: Histogram`・`latch_initial_e2e_seconds: Histogram`・`notification_latency_seconds: Histogram`。モジュール関数3本 — `observe_pipeline_layer(*, layer: str, elapsed_s: float) -> None`・`observe_initial_e2e(*, route: str, elapsed_s: float) -> None`・`observe_notification_latency(*, elapsed_s: float) -> None`(Task 4・5が消費)。bucket定数 `HTTP_BUCKETS`・`LATENCY_BUCKETS`・`NOTIFY_BUCKETS`(§2.3の表どおり)

- [ ] **Step 1: 失敗する試験を書く(test_obs_metrics.pyへの追随)**

変更1: `EXPECTED_SAMPLE_NAMES` の集合に9名を追加(`# 提案と行動` のブロックの後に):

```python
    # 負荷試験の計測(M4 ws-6・design §2.3。designの「latch_initial_e2e_seconds」
    # は露出名(=namespace接頭辞込み)なので・metrics.pyの定義名は
    # initial_e2e_secondsとして露出名latch_initial_e2e_seconds_*を揃える)
    "latch_pipeline_layer_seconds_bucket",
    "latch_pipeline_layer_seconds_count",
    "latch_pipeline_layer_seconds_sum",
    "latch_initial_e2e_seconds_bucket",
    "latch_initial_e2e_seconds_count",
    "latch_initial_e2e_seconds_sum",
    "latch_notification_latency_seconds_bucket",
    "latch_notification_latency_seconds_count",
    "latch_notification_latency_seconds_sum",
```

変更2: ファイル末尾へbucket境界とobserve3関数の試験を追加:

```python
# -- 負荷試験の計測(Task 3・M4 ws-6)--

from latch.obs.metrics import (
    HTTP_BUCKETS,
    LATENCY_BUCKETS,
    NOTIFY_BUCKETS,
    observe_initial_e2e,
    observe_notification_latency,
    observe_pipeline_layer,
)


def test_http_buckets_keep_defaults_and_add_3s_8s():
    """httpのbucketは既定DEFAULT_BUCKETSの写しに3.0と8.0を追加した列
    (既存値は1つも変えない・§2.3の表)。"""
    # 既定DEFAULT_BUCKETSの値と順序を直接書く(既存値不変の機械確認)
    assert HTTP_BUCKETS[:10] == (
        0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5,
    )
    assert HTTP_BUCKETS[10:] == (3.0, 5.0, 7.5, 8.0, 10.0)


def test_latency_buckets_contain_injection_targets_and_budgets():
    """層別・E2Eのbucketに注入値(p50 1秒/2秒・p95 2.5秒/5秒)と目標境界10秒が
    ある(histogram_quantileの補間推定が注入値と一致する位置・§2.3)。"""
    assert 1.0 in LATENCY_BUCKETS
    assert 2.0 in LATENCY_BUCKETS
    assert 2.5 in LATENCY_BUCKETS
    assert 5.0 in LATENCY_BUCKETS
    assert LATENCY_BUCKETS[-1] == 10.0


def test_notify_buckets_contain_5s_target():
    assert 5.0 in NOTIFY_BUCKETS
    assert NOTIFY_BUCKETS[0] == 0.001


def test_new_histograms_are_registered_with_labels():
    """新3本がlabelつきで登録される(layer=embedding等でseriesが生える)。"""
    registry = CollectorRegistry()
    m = build_metrics(registry)
    m.pipeline_layer_seconds.labels(layer="retrieval").observe(0.4)
    m.latch_initial_e2e_seconds.labels(route="create").observe(2.1)
    m.notification_latency_seconds.observe(0.01)
    assert (
        registry.get_sample_value(
            "latch_pipeline_layer_seconds_count", {"layer": "retrieval"}
        )
        == 1.0
    )
    assert (
        registry.get_sample_value(
            "latch_initial_e2e_seconds_count", {"route": "create"}
        )
        == 1.0
    )
    assert registry.get_sample_value("latch_notification_latency_seconds_count") == 1.0


def test_injection_value_buckets_are_exposed():
    """露出bucketのleに注入値と目標境界が現れる(クエリ側の前提)。"""
    registry = CollectorRegistry()
    build_metrics(registry)
    les_pipeline: set[str] = set()
    les_e2e: set[str] = set()
    for family in registry.collect():
        for sample in family.samples:
            if sample.name == "latch_pipeline_layer_seconds_bucket":
                les_pipeline.add(sample.labels["le"])
            if sample.name == "latch_initial_e2e_seconds_bucket":
                les_e2e.add(sample.labels["le"])
    # prometheus-clientはleをrepr形式の文字列で出す
    assert {"1.0", "2.0", "2.5", "5.0", "10.0"} <= les_pipeline
    assert {"1.0", "2.0", "2.5", "5.0", "10.0"} <= les_e2e


def test_observe_helpers_for_load_metrics_increment_the_right_series():
    """observe3関数が単例のseriesを増やす(既存observe_*と同型の差分検証)。"""
    from latch.obs import metrics as obs_metrics_module

    before = (
        REGISTRY.get_sample_value(
            "latch_pipeline_layer_seconds_count", {"layer": "embedding"}
        )
        or 0
    )
    obs_metrics_module.observe_pipeline_layer(layer="embedding", elapsed_s=0.5)
    after = (
        REGISTRY.get_sample_value(
            "latch_pipeline_layer_seconds_count", {"layer": "embedding"}
        )
        or 0
    )
    assert after == before + 1

    before_e2e = (
        REGISTRY.get_sample_value(
            "latch_initial_e2e_seconds_count", {"route": "activate"}
        )
        or 0
    )
    obs_metrics_module.observe_initial_e2e(route="activate", elapsed_s=3.2)
    after_e2e = (
        REGISTRY.get_sample_value(
            "latch_initial_e2e_seconds_count", {"route": "activate"}
        )
        or 0
    )
    assert after_e2e == before_e2e + 1

    before_notify = REGISTRY.get_sample_value(
        "latch_notification_latency_seconds_count"
    )
    obs_metrics_module.observe_notification_latency(elapsed_s=0.002)
    after_notify = REGISTRY.get_sample_value(
        "latch_notification_latency_seconds_count"
    )
    assert (after_notify or 0) == (before_notify or 0) + 1
```

注意: `test_http_buckets_keep_defaults_and_add_3s_8s` の冒頭にある `from prometheus_client.core import _DEFAULT_BUFFERS  # noqa: F401  (未使用確認)` の行は**入れない**(既定値は上記のとおりタプル値で直接ピンする)。

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_metrics.py -v`
Expected: FAIL(HTTP_BUCKETS等が存在しない・許可リスト9名不足)

- [ ] **Step 3: metrics.pyを実装する**

変更1: 既存 `SCORE_BUCKETS`・`VECTOR_BUCKETS` の下へbucket定数3種を追加:

```python
# HTTPのbucket(M4 ws-6・§2.3の表)。prometheus-client既定DEFAULT_BUCKETSに
# 3.0(目標②のp95 3秒)と8.0(目標②のp99 8秒)を追加した列 — 既存値は1つも
# 変えず・明示列へ切り替える(bucket追加は既存クエリに破壊なし)
HTTP_BUCKETS = (
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    3.0,
    5.0,
    7.5,
    8.0,
    10.0,
)
# 層別・E2Eの共通bucket(M4 ws-6)。10 §4.1注入値(p50 1秒/2秒・p95 2.5秒/5秒)と
# 予算境界(Embedding 2秒・Retrieval 1秒・L5+通知 2秒)とE2E目標10秒を含む
LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0)
# 通知区間(提案確定→push記録)のbucket。StubPushSender構成ではごく小さい。
# 構造上の大きさを観察し実FCM時の上限を推定できる解像度・目標⑤の5秒境界込み
NOTIFY_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0)
```

変更2: `Metrics` dataclassへ属性3本を追加(`kpi_daily_denominator: Gauge` の後):

```python
    pipeline_layer_seconds: Histogram
    latch_initial_e2e_seconds: Histogram
    notification_latency_seconds: Histogram
```

変更3: `http_request_duration` のHistogram定義へ `buckets=HTTP_BUCKETS,` を追加(既存bucket指定は無し=既定のため・明示列への切替は値の追加のみ):

```python
        http_request_duration=Histogram(
            "http_request_duration_seconds",
            "HTTP request latency by method and path template",
            labelnames=("method", "path"),
            namespace="latch",
            buckets=HTTP_BUCKETS,
            registry=registry,
        ),
```

変更4: `build_metrics` の戻り値へ新3本を追加(`kpi_daily_denominator=` の後に):

```python
        pipeline_layer_seconds=Histogram(
            "pipeline_layer_seconds",
            "Layer latency (layer=embedding/retrieval/proposal; jev is separate)",
            labelnames=("layer",),
            namespace="latch",
            buckets=LATENCY_BUCKETS,
            registry=registry,
        ),
        # 定義名はinitial_e2e_seconds・露出名はlatch_initial_e2e_seconds_*
        # (design §2.3の「latch_initial_e2e_seconds」は露出名の表記)
        latch_initial_e2e_seconds=Histogram(
            "initial_e2e_seconds",
            "Initial LATCH decision e2e by injection route (create/activate)",
            labelnames=("route",),
            namespace="latch",
            buckets=LATENCY_BUCKETS,
            registry=registry,
        ),
        notification_latency_seconds=Histogram(
            "notification_latency_seconds",
            "Proposal decision to push send record",
            namespace="latch",
            buckets=NOTIFY_BUCKETS,
            registry=registry,
        ),
```

変更5: モジュール末尾へobserve3関数を追加(`observe_latch_score` の後):

```python
def observe_pipeline_layer(*, layer: str, elapsed_s: float) -> None:
    """層別区間1回分の観測(M4 ws-6・layer=embedding/retrieval/proposal)。

    層別予算(Embedding ≤2秒/Layer 1〜3 ≤1秒/L5+通知 ≤2秒・12 §7)の
    どの区間が超えたか特定する器。Jevは既存jev_latency_secondsが観測済み。
    """
    metrics.pipeline_layer_seconds.labels(layer=layer).observe(float(elapsed_s))


def observe_initial_e2e(*, route: str, elapsed_s: float) -> None:
    """初期判定E2Eの観測(M4 ws-6・route=create/activate)。

    初回投入Event(created種)チェーンかつ初回提案のみを観測する
    (呼び出し側=latch_engineの絞り込み条件・design §2.3)。
    """
    metrics.latch_initial_e2e_seconds.labels(route=route).observe(float(elapsed_s))


def observe_notification_latency(*, elapsed_s: float) -> None:
    """通知区間(提案確定→push送信記録)の観測(M4 ws-6)。"""
    metrics.notification_latency_seconds.observe(float(elapsed_s))
```

- [ ] **Step 4: 試験が通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_metrics.py tests/unit/test_obs_pipeline_metrics.py -v`
Expected: PASS(既存observe・middleware・許可リスト更新後すべて不変)

- [ ] **Step 5: コミットする**

```bash
git add backend/src/latch/obs/metrics.py backend/tests/unit/test_obs_metrics.py
git commit -m "feat: 層別/E2E/通知ヒストグラム3本追加とhttp bucket拡張(既存bucket値不変)(M4 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: worker側のEmbedding区間・Retrieval区間の観測挿入

**Files:**
- Modify: `backend/src/latch/worker/embedding.py`(handle全体をtry/finallyラップ)
- Modify: `backend/src/latch/worker/main.py`(_run_matching・_run_direct_pipelineへRetrieval区間observe)
- Modify: `backend/tests/unit/test_obs_pipeline_metrics.py`(既存2試験に差分assert追加・embedding観測試験追加)

**Interfaces:**
- Consumes: `observe_pipeline_layer`(Task 3)
- Produces: IF変更なし(embedding.handle(intent_id, version)・Worker両経路の既存シグネチャは不変)。層別ヒストグラムへの観測経路が確定する(layer=embeddingはEmbeddingWorker.handleの全経路・layer=retrievalはEvent経路とBucket/catch-up直接経路の両方)

- [ ] **Step 1: 失敗する試験を書く(test_obs_pipeline_metrics.pyへの追従)**

変更1: importへ1行追加:

```python
from latch.worker.embedding import EmbeddingWorker
```

変更2: `test_observe_retrieval_is_called_from_run_matching` へ・`before_input = ...` の行の直前にbefore取得と・`assert after_pass == before_pass + 13` の後に差分assertを追加(既存行は1行も変更しない):

```python
    # 既存の before_input = ... の直前へ:
    before_layer = (
        REGISTRY.get_sample_value(
            "latch_pipeline_layer_seconds_count", {"layer": "retrieval"}
        )
        or 0
    )
    # 既存の assert after_pass == before_pass + 13 の直後へ:
    after_layer = (
        REGISTRY.get_sample_value(
            "latch_pipeline_layer_seconds_count", {"layer": "retrieval"}
        )
        or 0
    )
    assert after_layer == before_layer + 1
```

変更3: `test_observe_retrieval_from_direct_pipeline` へ同型の追記(`before_input = ...` の直前にbefore・末尾assertの後にafter):

```python
    # 既存の before_input = ... の直前へ:
    before_layer = (
        REGISTRY.get_sample_value(
            "latch_pipeline_layer_seconds_count", {"layer": "retrieval"}
        )
        or 0
    )
    # 既存の assert after_input == before_input + 1 の直後へ:
    after_layer = (
        REGISTRY.get_sample_value(
            "latch_pipeline_layer_seconds_count", {"layer": "retrieval"}
        )
        or 0
    )
    assert after_layer == before_layer + 1
```

変更4: ファイル末尾へembedding観測試験を追加:

```python
def test_embedding_handle_observes_pipeline_layer(fake_clock, monkeypatch):
    """EmbeddingWorker.handleの全経路(早期return含む)が層別観測を1回通る。"""
    from latch.worker import embedding as embedding_mod

    class _Res:
        def first(self):
            return None  # 起点なし=早期return経路

    class _Conn:
        async def execute(self, stmt, params=None):
            return _Res()

    class _Tx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *exc):
            return False

    class _Engine:
        def begin(self):
            return _Tx()

    worker = EmbeddingWorker(
        engine=_Engine(),
        clock=fake_clock,
        gateway=object(),
        bus=object(),
    )
    before = (
        REGISTRY.get_sample_value(
            "latch_pipeline_layer_seconds_count", {"layer": "embedding"}
        )
        or 0
    )
    import asyncio

    asyncio.run(worker.handle(uuid.uuid4(), 1))
    after = (
        REGISTRY.get_sample_value(
            "latch_pipeline_layer_seconds_count", {"layer": "embedding"}
        )
        or 0
    )
    assert after == before + 1
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_pipeline_metrics.py -v`
Expected: FAIL(layer観測の差分assertが3件とも +0のまま)

- [ ] **Step 3: embedding.pyを実装する(handle全体をtry/finallyで包む)**

`EmbeddingWorker.handle` を次の形へ書き換える(**本体の行は1行も変更しない・インデントを一段下げるだけ**):

```python
    async def handle(self, intent_id: uuid.UUID, version: int) -> None:
        t0 = self._clock.now()
        try:
            row = await self._read_target(intent_id)
            if row is None:
                return
            db_version, status, has_embedding = row[0], row[1], row[2]
            if db_version != version:
                return  # 旧Eventの焼き直し(新versionのEventが担当)
            if status not in _STATUS_TARGETS:
                return  # 削除済み等(Layer 1〜2の対象外)
            if has_embedding:
                # 既存(resume由来 — テキスト不変)は再エンベディングせず直接投入(06 §9)
                await self._emit_completed(intent_id, version)
                return
            await self._embed_and_emit(intent_id, version, row)
        finally:
            # Embedding区間の観測(M4 ws-6・LLM込みのhandle全体・早期returnも
            # 対象=finally。観測は本体を壊さない・design §2.3)
            observe_pipeline_layer(
                layer="embedding",
                elapsed_s=(self._clock.now() - t0).total_seconds(),
            )
```

importブロックへ1行追加:

```python
from latch.obs.metrics import observe_pipeline_layer
```

- [ ] **Step 4: worker/main.pyへRetrieval区間の観測を挿入する**

importの変更(既存行を差し替え):

```python
from latch.obs.metrics import observe_pipeline_layer, observe_retrieval
```

`_run_matching` の該当部分を次の形へ(reevalガードの後に挿入):

```python
        t0 = self._clock.now()
        outcome = await run_candidate_retrieval(conn, self.clock, intent_id)
        # Retrieval区間の観測(M4 ws-6・Layer 1〜3の区間=層別予算の境界)
        observe_pipeline_layer(
            layer="retrieval",
            elapsed_s=(self._clock.now() - t0).total_seconds(),
        )
```

`_run_direct_pipeline` の該当部分も同一形(engine.begin()直後):

```python
        t0 = self._clock.now()
        async with engine.begin() as conn:
            outcome = await run_candidate_retrieval(conn, self.clock, intent_id)
        observe_pipeline_layer(
            layer="retrieval",
            elapsed_s=(self._clock.now() - t0).total_seconds(),
        )
```

- [ ] **Step 5: 試験が通ること・既存worker系が無傷であることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_pipeline_metrics.py tests/unit/test_worker.py tests/unit/test_worker_embedding.py tests/unit/test_worker_reeval.py -v`
Expected: PASS

- [ ] **Step 6: コミットする**

```bash
git add backend/src/latch/worker/embedding.py backend/src/latch/worker/main.py backend/tests/unit/test_obs_pipeline_metrics.py
git commit -m "feat: Embedding区間・Retrieval区間の層別観測挿入(本体ロジック無変更)(M4 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: LatchEngineのL5+通知区間・初期判定E2E・通知区間の観測

**Files:**
- Modify: `backend/src/latch/worker/matching/latch_engine.py`(E2eObsContext・起点読取SQL・_evaluate_pair戻り値化とE2Eコンテキスト・try_promoteへe2e_ctxキーワード引数・observe3点)
- Modify: `backend/tests/unit/matching/test_latch_engine.py`(_patch追随2件・新試験6件)

**Interfaces:**
- Consumes: `observe_pipeline_layer`・`observe_initial_e2e`・`observe_notification_latency`(Task 3)
- Produces: `LatchEngine.try_promote(latch_id: uuid.UUID, *, e2e_ctx: E2eObsContext | None = None) -> None`(既存呼び出し — GroupEngine.finalize・drain・既存試験のfake — は既定Noneで非破壊)。`latch_engine.E2eObsContext(origin_ts: datetime, route: str)`(frozen dataclass・routeは"create"または"activate")。モジュール関数 `_read_e2e_context(conn, org) -> E2eObsContext | None` と `_count_origin_proposals(conn, intent_id) -> int`(monkeypatch可能なDB部品・既存規律どおり)。`_evaluate_pair(org, row) -> bool`(内部IF・提案確定でTrue)。**LatchEngine.handleのIFは不変**(intent_id単一引数)

観測の絞り込み規則(design §2.3・§2.1読み替え1/2の確定):

- **E2E観測は次の3条件がすべて揃ったとき1回だけ**: (a)起点Intentのmatch_eventsに起点version一致のcreated種Eventが存在(初回投入チェーン) (b)当該提案が起点Intentを含むlatches行の1件目(初回提案) (c)try_promoteがproposed遷移とnotifications書込みまで到達した(通知記録まで)。
- **起点時刻=match_events.created_at(created種・version一致)**。route判定は `intents.created_at == 起点時刻` の等値比較(同一コミットは同一now値 — intents/service.pyの_create・_update_draft実突合済み)。等しければcreate・不一致ならactivate。
- **L5+通知区間**=LatchEngine.handle全体(提案を確定したときのみ観測・push送信はhandleの呼び出しスタック内で完了する)。**通知区間**=try_promote冒頭〜push送信完了(通知を書き込んだときのみ観測)。

- [ ] **Step 1: 失敗する試験を書く(test_latch_engine.pyへの追従)**

変更1: `_patch` の関数シグネチャへ引数2本を追加(`insert_result=_DEFAULT,` の行の後あたり・任意引数として末尾へ):

```python
def _patch(
    monkeypatch,
    *,
    org: Origin | None = None,
    skip_reason: str | None = None,
    rows=(),
    h_holds: bool = True,
    record_result=_DEFAULT,
    inputs: dict | None = None,
    responses=(),
    insert_result=_DEFAULT,
    find_open_result=None,
    daily: dict | None = None,
    e2e_context=None,      # 追加: E2eObsContext | None(既定None=created種なし扱い)
    origin_proposals=2,    # 追加: 起点を含むlatches行数(既定2=初回でない扱い)
):
```

変更2: `log` 辞書へ2キー追加(`"drain": [],` の後):

```python
        "promote_ctx": [],
        "e2e": [],
```

変更3: fake群へ2関数を追加し・`fake_promote` を書き換える(`fake_promote` の現行2行を差し替え):

```python
    async def fake_read_e2e_context(conn, org_obj):
        log["e2e"].append(("context", org_obj.intent_id))
        return e2e_context

    async def fake_count_proposals(conn, intent_id):
        log["e2e"].append(("count", intent_id))
        return origin_proposals

    async def fake_promote(engine_self, latch_id, **kwargs):
        log["promote"].append(latch_id)  # 既存試験のassertは不変
        log["promote_ctx"].append(kwargs.get("e2e_ctx"))
```

変更4: monkeypatchsetattr群へ2行を追加(`monkeypatch.setattr(le.LatchEngine, "try_promote", fake_promote)` の前後):

```python
    monkeypatch.setattr(le, "_read_e2e_context", fake_read_e2e_context)
    monkeypatch.setattr(le, "_count_origin_proposals", fake_count_proposals)
```

**既存試験は1行もassert変更しない**(e2e_context既定Noneでは_count_origin_proposalsは呼ばれず・"promote_ctx"は既存assertの対象外)。

変更5: ファイル末尾へ新試験6件を追加:

```python
# =====================================================================
# 負荷試験の観測(M4 ws-6・design §2.3・§2.1読み替え1/2)
# =====================================================================


def _e2e_observed_total() -> float:
    """E2Eヒストグラム_countの全route合計(ラベル和・REGISTRY累積差分用)。"""
    total = 0.0
    for family in REGISTRY.collect():
        if family.name != "latch_initial_e2e_seconds":
            continue
        for sample in family.samples:
            if sample.name == "latch_initial_e2e_seconds_count":
                total += sample.value
    return total


def _notify_observed_total() -> float:
    total = 0.0
    for family in REGISTRY.collect():
        if family.name != "notification_latency_seconds":
            continue
        for sample in family.samples:
            if sample.name == "latch_notification_latency_seconds_count":
                total += sample.value
    return total


async def test_handle_passes_e2e_ctx_on_first_injection(monkeypatch):
    """created種Event(version一致)+初回提案 → try_promoteへE2Eコンテキスト。"""
    row = _row(1, wa=0.9, wb=0.85)
    ctx = le.E2eObsContext(origin_ts=NOW, route="create")
    log = _patch(
        monkeypatch,
        org=_origin(1),
        rows=[row],
        e2e_context=ctx,
        origin_proposals=1,
    )
    await _engine().handle(_uid(1))
    assert log["promote_ctx"] == [ctx]


async def test_handle_drops_ctx_when_not_first_proposal(monkeypatch):
    """初回でない提案(起点を含むlatches行が2件目以降) → 観測対象外。"""
    row = _row(1, wa=0.9, wb=0.85)
    ctx = le.E2eObsContext(origin_ts=NOW, route="activate")
    log = _patch(
        monkeypatch,
        org=_origin(1),
        rows=[row],
        e2e_context=ctx,
        origin_proposals=2,
    )
    await _engine().handle(_uid(1))
    assert log["promote_ctx"] == [None]


async def test_handle_passes_none_ctx_without_created_event(monkeypatch):
    """created種Eventなし(更新Event・再評価由来の起点) → 観測しない。"""
    row = _row(1, wa=0.9, wb=0.85)
    log = _patch(monkeypatch, org=_origin(1), rows=[row])  # e2e_context既定None
    await _engine().handle(_uid(1))
    assert log["promote_ctx"] == [None]
    assert ("context", _uid(1)) not in log["e2e"]  # 起点読取自体が走らない


async def test_promote_observes_e2e_with_route(monkeypatch):
    """e2e_ctx付きproposed遷移+通知 → E2E(route付き)と通知区間を観測。"""
    _patch_promote(monkeypatch, row=_latch_row())
    before_e2e = _e2e_observed_total()
    before_notify = _notify_observed_total()
    ctx = le.E2eObsContext(origin_ts=NOW - timedelta(seconds=3), route="create")
    await _engine().try_promote(LID, e2e_ctx=ctx)
    assert _e2e_observed_total() == before_e2e + 1
    assert _notify_observed_total() == before_notify + 1


async def test_promote_without_ctx_observes_notify_only(monkeypatch):
    """e2e_ctx=None(既存呼び出し形) → 通知区間のみ・E2Eは観測しない。"""
    _patch_promote(monkeypatch, row=_latch_row())
    before_e2e = _e2e_observed_total()
    before_notify = _notify_observed_total()
    await _engine().try_promote(LID)
    assert _e2e_observed_total() == before_e2e
    assert _notify_observed_total() == before_notify + 1


async def test_promote_expired_writes_no_load_observation(monkeypatch):
    """75分切れ(candidate→expired・通知ゼロ) → 両観測とも0増。"""
    parts = [
        _participant(1, time_start=NOW + timedelta(minutes=70)),
        _participant(101, time_start=NOW + timedelta(minutes=70)),
    ]
    _patch_promote(monkeypatch, row=_latch_row(), parts=parts)
    before_e2e = _e2e_observed_total()
    before_notify = _notify_observed_total()
    ctx = le.E2eObsContext(origin_ts=NOW, route="create")
    await _engine().try_promote(LID, e2e_ctx=ctx)
    assert _e2e_observed_total() == before_e2e
    assert _notify_observed_total() == before_notify


def test_e2e_origin_sql_pins_unique_index_columns():
    """起点読取SQL: created種・source_intent・payload version(UNIQUE索引)。"""
    sql = str(le._SELECT_INJECTION_ORIGIN)
    assert "event_type = 'created'" in sql
    assert "me.source_intent_id = CAST(:intent_id AS uuid)" in sql
    assert "me.payload->>'version' = :version" in sql
```

注意: `REGISTRY` はファイル冒頭へ `from prometheus_client import CollectorRegistry, REGISTRY` を追加する(現行のimport文にCollectorRegistryは不要なため・`from prometheus_client import REGISTRY` の1行追加でよい)。`timedelta` は現行import済み。

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py -v`
Expected: FAIL(E2eObsContext・_SELECT_INJECTION_ORIGINが存在しない・promote_ctxキー未定義)

- [ ] **Step 3: latch_engine.pyを実装する**

変更1: importブロックへ観測3関数を追加(既存 `from latch.obs.metrics import observe_latch_score` を差し替え):

```python
from latch.obs.metrics import (
    observe_initial_e2e,
    observe_latch_score,
    observe_notification_latency,
    observe_pipeline_layer,
)
```

変更2: `LatchTargetRow` のdataclass定義の後に観測コンテキストを追加:

```python
@dataclass(frozen=True)
class E2eObsContext:
    """初期判定E2Eの観測コンテキスト(M4 ws-6・初回投入チェーンの初回提案)。

    origin_ts=起点時刻(match_events.created_at・created種version一致=
    Event発火(active化の保存コミット))。route=投入経路(create=active作成・
    activate=draft→active化)。
    """

    origin_ts: datetime
    route: str
```

変更3: SQL定数を追加(`_DRAIN_CANDIDATES` の後に):

```python
# 初回投入Event(created種・version一致)の起点読取(M4 ws-6)。
# ux_match_events_idempotency(event_type, source_intent_id, payload->>'version')
# のUNIQUE索引でピンポイント(05 §2)。created_atはAPI側が遷移tx内のClock値で
# INSERTするため「Event発火(active化の保存コミット)」そのもの
_SELECT_INJECTION_ORIGIN = text("""
    SELECT me.created_at
    FROM match_events me
    WHERE me.event_type = 'created'
      AND me.source_intent_id = CAST(:intent_id AS uuid)
      AND me.payload->>'version' = :version
""")
# 起点Intentの作成時刻(route判定用・active作成ではcreated種Eventと同一now値)
_SELECT_INTENT_CREATED_AT = text("""
    SELECT created_at FROM intents WHERE id = CAST(:intent_id AS uuid)
""")
# 起点Intentを含むlatches行数(初回提案判定・提案INSERT後に実行=自分を含む)
_COUNT_PROPOSALS_FOR_ORIGIN = text("""
    SELECT count(*) FROM latches
    WHERE intent_ids @> ARRAY[CAST(:intent_id AS uuid)]
""")
```

変更4: モジュール関数2本を `_drain_candidates` の後に追加:

```python
async def _read_e2e_context(conn, org: Origin) -> E2eObsContext | None:
    """初回投入チェーンの観測コンテキスト(M4 ws-6・design §2.3)。

    起点Intentのcreated種Event(起点version一致)が無ければNone(更新Event・
    Bucket/catch-up再評価由来=観測対象外)。routeはintents.created_atと起点
    時刻の等値比較で判定: 同一コミット(同一now変数・intents/service.pyの
    _create・_update_draft実装どおり)ならcreate・不一致ならactivate。
    """
    row = (
        await conn.execute(
            _SELECT_INJECTION_ORIGIN,
            {"intent_id": org.intent_id, "version": str(org.version)},
        )
    ).first()
    if row is None:
        return None
    intent_row = (
        await conn.execute(_SELECT_INTENT_CREATED_AT, {"intent_id": org.intent_id})
    ).first()
    if intent_row is None:
        return None
    origin_ts = row[0]
    route = "create" if intent_row[0] == origin_ts else "activate"
    return E2eObsContext(origin_ts=origin_ts, route=route)


async def _count_origin_proposals(conn, intent_id: uuid.UUID) -> int:
    """起点Intentを含むlatches行数(初回提案判定・提案INSERT後に実行)。"""
    res = await conn.execute(_COUNT_PROPOSALS_FOR_ORIGIN, {"intent_id": intent_id})
    return int(res.scalar_one())
```

変更5: `handle` を次の形へ書き換える(既存本体の順序は不変・観測を追加):

```python
    async def handle(self, intent_id: uuid.UUID) -> None:
        """フェーズ1(読取)→各行評価→drain(design §2.9のtx分割)。"""
        t0 = self._clock.now()
        proposed = False
        async with self._engine.begin() as conn:
            loaded = await origin_mod.load_origin(conn, self._clock, intent_id)
        if loaded.origin is None:
            logger.info(
                "latch origin no-op intent_id=%s reason=%s",
                intent_id,
                loaded.skip_reason,
            )
            return
        org = loaded.origin
        async with self._engine.begin() as conn:
            rows = await _select_target_rows(conn, org.intent_id, org.version)
        for row in rows:
            if await self._evaluate_pair(org, row):
                proposed = True
        await self.drain()
        if proposed:
            # L5+通知区間の観測(M4 ws-6・handle全体=提案を確定したときのみ。
            # push送信は_evaluate_pair→try_promote内で完了済み。層別予算の
            # 「Layer 5+通知 ≤2秒」の観測・12 §7)
            observe_pipeline_layer(
                layer="proposal",
                elapsed_s=(self._clock.now() - t0).total_seconds(),
            )
```

変更6: `_evaluate_pair` を次の形へ書き換える(**docstringと材料読取部分は既存どおり・戻り値bool化とE2Eコンテキストの3点が差分**):

```python
    async def _evaluate_pair(self, org, row: LatchTargetRow) -> bool:
        """1ペア: 材料読取(tx前)→tx1(H再検証+退避つきUPDATE+D-07+latches+events)。

        戻り値は「提案(candidate以上)を確定したか」(M4 ws-6・handleの
        proposal層観測の絞り込みに使う・nearby/candidate生成なしはFalse)。

        I-1対策(design §2.7-4・承認事項4): 失敗しうる読取をtx前に済ませ、
        計算(latch_score退避UPDATE)と生成物(latches INSERT・status_events)を
        同一txで書く。読取段階の失敗はlatch_score NULLのまま残るため、
        復旧後の再handleで再選択される(ws-6の引継ぎ空白の構造解消)。
        """
        now = self._clock.now()
        peer_id = (
            row.intent_b_id if row.intent_a_id == org.intent_id else row.intent_a_id
        )
        a_id, b_id = sorted((org.intent_id, peer_id))
        # tx前の読取(全材料・短tx内包): peer入力が欠けていれば書かない
        origin_inputs = await _read_intent_inputs(self._engine, org.intent_id)
        peer_inputs = await _read_intent_inputs(self._engine, peer_id)
        if origin_inputs is None or peer_inputs is None:
            logger.info(
                "latch inputs missing row_id=%s peer_id=%s", row.row_id, peer_id
            )
            return False
        target = latch_calc.pair_target_time(
            origin_inputs.time_start, peer_inputs.time_start
        )
        min_expires = min(origin_inputs.expires_at, peer_inputs.expires_at)
        area = await self._area_name(origin_inputs, peer_inputs)
        responses = await _read_latch_responses(self._engine, a_id, b_id)
        has_no, latest_defer_at = latch_calc.d07_history_inputs(responses)
        mutual = min(
            float(row.jev_result["would_a_accept_b"]),
            float(row.jev_result["would_b_accept_a"]),
        )
        score = latch_calc.LATCH_C * mutual
        deadline0 = latch_calc.response_deadline(now, target, min_expires)
        # tx1: H再検証(SELECT) + 退避つきlatch_score UPDATE + 生成物
        latch_id: uuid.UUID | None = None
        nearby_latch_id: uuid.UUID | None = None
        e2e_ctx: E2eObsContext | None = None
        notified_users: list[uuid.UUID] = []
        async with self._engine.begin() as conn:
            if not await layer4.hard_constraint_holds(conn, org, peer_id):
                await _close_h_broken(conn, row.row_id, now)
                return False
            rec = await _record_score(conn, row.row_id, score, now)
            if rec is None:
                return False
            _, prev_latch_score = rec
            if score >= latch_calc.LATCH_THRESHOLD:
                if not latch_calc.d07_allows(
                    has_no_response=has_no,
                    latest_defer_at=latest_defer_at,
                    now=now,
                    target_time=target,
                    new_score=score,
                    prev_latch_score=prev_latch_score,
                ):
                    logger.info("latch d07 denied a=%s b=%s", a_id, b_id)
                    return False
                # 提案確定(d07通過後・このtxで提案を構築する点)のscore観測
                # (M4 ws-4・design §2.8。d07 denied・競合負けは分布に入れない)
                observe_latch_score(shape="pair", score=float(score))
                # 初回投入チェーンの起点読取(M4 ws-6・提案確定時のみ)。
                # created種Eventが無い起点(更新Event・再評価)は観測対象外
                e2e_ctx = await _read_e2e_context(conn, org)
                proposal = proposal_mod.build_proposal(
                    origin=origin_inputs,
                    peer=peer_inputs,
                    score=score,
                    area_name=area,
                )
                latch_id = await _insert_latch(
                    conn,
                    a_id=a_id,
                    b_id=b_id,
                    proposal=proposal,
                    score=score,
                    deadline=deadline0,
                    expires=min_expires,
                    now=now,
                )
                if latch_id is not None:
                    await _insert_latch_event(
                        conn, latch_id, None, "candidate", None, now
                    )
                else:
                    found = await _find_open_latch(conn, a_id, b_id)
                    if found is None:
                        return False
                    lid, status = found
                    if status != "candidate":
                        logger.info(
                            "latch open row exists latch_id=%s status=%s",
                            lid,
                            status,
                        )
                        return False
                    if not latch_calc.d07_allows(
                        has_no_response=has_no,
                        latest_defer_at=latest_defer_at,
                        now=now,
                        target_time=target,
                        new_score=score,
                        prev_latch_score=prev_latch_score,
                    ):
                        logger.info(
                            "latch d07 denied on promotion a=%s b=%s", a_id, b_id
                        )
                        return False
                    if not await _update_for_promotion(
                        conn, lid, score=score, proposal=proposal, deadline=deadline0
                    ):
                        return False
                    latch_id = lid
                # 初回提案でなければE2E観測から外す(design §2.3条件b・
                # 提案INSERT後に数える=自分を含み・1件のときが初回)
                if e2e_ctx is not None:
                    if await _count_origin_proposals(conn, org.intent_id) != 1:
                        e2e_ctx = None
            else:
                # nearbyはtry_promoteしない(candidateのまま・引用#9)
                nearby_latch_id, notified_users = await self._nearby_in_tx(
                    conn,
                    a_id=a_id,
                    b_id=b_id,
                    score=score,
                    origin_inputs=origin_inputs,
                    peer_inputs=peer_inputs,
                    deadline0=deadline0,
                    min_expires=min_expires,
                    has_no=has_no,
                    now=now,
                )
        if latch_id is not None:
            await self.try_promote(latch_id, e2e_ctx=e2e_ctx)
        if nearby_latch_id is not None:
            # 存在通知の送信もtxコミット後(design §2.1案A・§3.2④)
            await self._send_pushes(
                [(uid, NOTIFICATION_NEARBY, nearby_latch_id) for uid in notified_users]
            )
        return latch_id is not None
```

変更7: `try_promote` を次の形へ(シグネチャへキーワード引数・冒頭にt0・tx末尾でproposed_at・コミット後に観測2点。**既存の本体判定は1行も変更しない**):

```python
    async def try_promote(
        self, latch_id: uuid.UUID, *, e2e_ctx: E2eObsContext | None = None
    ) -> None:
        """提示判定(1tx・design §2.6手順1〜9・Review Focus 4の手順順序)。

        e2e_ctxは初期判定E2Eの観測コンテキスト(初回投入チェーンの初回提案
        のみ非None・M4 ws-6)。既存呼び出し(GroupEngine.finalize・drain)は
        既定Noneで非破壊。
        手順: 行ロック→(now採取・参加者読取)→75分ルール→expires_at切れ
        対象外化→D-06重複上位チェック(グループのみ)→D-05再計算(deadline<=nowは
        75分と同一扱い)→D-08日次→D-08同時→条件付きproposed遷移→
        イベント+notifications(muted除外)。上限超過はcandidateのままreturn
        (破棄しない — 引用#8)。ws-7で|S|人対応(2〜4要素のintent_ids)。
        M3 ws-3: 通知対象はtx内で収集しtxコミット後にプッシュ送信(§2.1案A)。
        """
        pending: list[tuple[uuid.UUID, str, uuid.UUID]] = []
        proposed_at = None
        t0 = self._clock.now()  # 通知区間の起点(提案確定=try_promote開始)
        async with self._engine.begin() as conn:
            row = await _select_latch_for_update(conn, latch_id)
            if row is None or row[1] != "candidate":
                return
            now = self._clock.now()  # FOR UPDATE取得後に採取
            parts = await _read_participants(conn, row[2])
            if len(parts) < len(row[2]):
                return  # 参加Intent欠損(削除等)は対象外
            target = max(p.time_start for p in parts)
            min_expires = min(p.expires_at for p in parts)
            # 75分ルール: 通知せず破棄(candidate→expired・引用#5)
            if target - now < latch_calc.PROMPT_LEAD_MIN:
                await _expire_latch(conn, latch_id, now)
                await _insert_latch_event(
                    conn, latch_id, "candidate", "expired", None, now
                )
                return
            # expires_at切れはexpiry_sweeper(M3-3)の担当・ここでは対象外化のみ
            if row[3] <= now:
                return
            if row[4] is not None:  # グループlatchesのみ(design §2.6)
                if await _has_higher_group_latch(conn, latch_id, row[2], float(row[5])):
                    return  # candidateのまま(上位の行が閉じた後のdrainで提示)
            deadline = latch_calc.response_deadline(now, target, min_expires)
            if deadline <= now:
                # 導出期限が通知時刻を過ぎない場合は通知しない(引用#4・防御)
                await _expire_latch(conn, latch_id, now)
                await _insert_latch_event(
                    conn, latch_id, "candidate", "expired", None, now
                )
                return
            notify = [p for p in parts if p.notification_level != "muted"]
            day_start = layer4.jst_day_start(self._clock.jst_date())
            day_next = day_start + timedelta(days=1)
            counts = await _count_daily_notifications(
                conn, [p.user_id for p in notify], day_start, day_next
            )
            if any(
                counts.get(p.user_id, 0) >= latch_calc.D08_DAILY_LIMIT for p in notify
            ):
                return  # candidateのまま(破棄しない)
            for p in parts:  # muted含む全参加者がproposed数に計上(引用#10)
                if (
                    await _count_open_proposed(conn, p.intent_id)
                    >= latch_calc.D08_CONCURRENT_LIMIT
                ):
                    return
            if not await _promote_latch(conn, latch_id, deadline, now):
                return  # 他経路で遷移済み(条件付きUPDATEの行数0)
            await _insert_latch_event(
                conn, latch_id, "candidate", "proposed", None, now
            )
            for p in notify:
                await _insert_notification(
                    conn, p.user_id, NOTIFICATION_PROPOSAL, latch_id, now
                )
                pending.append((p.user_id, NOTIFICATION_PROPOSAL, latch_id))
            if pending:
                proposed_at = now  # 通知記録時刻(E2E終点・10 §4.1)
        # txコミット後のみ送信(§2.1: コミット前に送るとnotifications行が
        # ないままプッシュが飛び得る)。早期return(candidate保留・競合負け)
        # はpending空のままtxを抜けるため送信されない。
        await self._send_pushes(pending)
        if pending:
            # 通知区間の観測(M4 ws-6・提案確定→push送信記録。早期return経路は
            # 観測しない=通知が書き込まれたときのみ)
            observe_notification_latency(
                elapsed_s=(self._clock.now() - t0).total_seconds()
            )
            if e2e_ctx is not None and proposed_at is not None:
                # 初期判定E2E(10 §4.1・Event発火の保存コミット→通知記録)
                observe_initial_e2e(
                    route=e2e_ctx.route,
                    elapsed_s=(proposed_at - e2e_ctx.origin_ts).total_seconds(),
                )
```

- [ ] **Step 4: 試験が通ること・既存latch_engine系が無傷であることを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py tests/unit/test_worker.py tests/unit/matching/ -v`
Expected: PASS(既存試験のassertは1行も変更せず・fake_promoteの**kwargs追随とlogキー追加のみで共存)

- [ ] **Step 5: コミットする**

```bash
git add backend/src/latch/worker/matching/latch_engine.py backend/tests/unit/matching/test_latch_engine.py
git commit -m "feat: LatchEngineへL5+通知区間・初期判定E2E(route=create/activate)・通知区間の観測(M4 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: loadgen/schedule.py(決定的送出時刻列とオープンモデル実行器)

**Files:**
- Create: `backend/src/latch/loadgen/__init__.py`(空)
- Create: `backend/src/latch/loadgen/schedule.py`
- Test: `backend/tests/unit/test_loadgen_schedule.py`

**Interfaces:**
- Consumes: `Clock`(core/clock.py・実行時刻の採取)
- Produces: `SendPlan(at: float, kind: str, seq: int)`(frozen dataclass・atは開始からの相対秒)。`constant_rate_plan(*, rate_per_sec: float, count: int, kind: str) -> list[SendPlan]`・`burst_plan(*, count: int, duration_s: float, kind: str) -> list[SendPlan]`。`RunStats`(planned/sent/ok/error/errors/client_elapsed_ms・`client_p95_ms` property)。`run_open_model(plans, *, sender, clock: Clock) -> RunStats`(senderは `async def sender(plan: SendPlan) -> None`・例外は集計して続行)。Task 8のCLIが消費

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_loadgen_schedule.py`:

```python
"""決定的送出時刻列とオープンモデル実行器(M4 ws-6・design §2.1案A)。

実時間の測定(perf_counter)はテストコードのみ(§0規律・既存test_llm_factory
と同一作法)。オープンモデル性(応答待ちが送出間隔を遅らせない)は・遅い
応答を模擬したモック送出下で・クローズドモデルの所要を大きく下回ることで
検証する。
"""

import asyncio
import time

from latch.core.clock import SystemClock
from latch.loadgen.schedule import burst_plan, constant_rate_plan, run_open_model


def test_constant_rate_plan_is_deterministic():
    plans = constant_rate_plan(rate_per_sec=50.0, count=500, kind="create")
    assert [p.at for p in plans[:5]] == [0.0, 0.02, 0.04, 0.06, 0.08]
    assert len(plans) == 500
    assert plans[499].at == 499 * 0.02
    assert all(p.kind == "create" for p in plans)
    assert [p.seq for p in plans] == list(range(500))


def test_burst_plan_spans_one_second():
    plans = burst_plan(count=100, duration_s=1.0, kind="create")
    assert len(plans) == 100
    assert plans[0].at == 0.0
    assert abs(plans[-1].at - 0.99) < 1e-9
    assert all(plans[i].at < plans[i + 1].at for i in range(99))


async def test_open_model_sends_all_plans_in_order():
    """予定どおりの件数を送出する(senderは送出順に呼ばれる)。"""
    sent: list[tuple[str, int]] = []

    async def sender(plan):
        sent.append((plan.kind, plan.seq))

    plans = constant_rate_plan(rate_per_sec=1000.0, count=20, kind="parse")
    stats = await run_open_model(plans, sender=sender, clock=SystemClock())
    assert stats.planned == 20
    assert stats.sent == 20
    assert stats.ok == 20
    assert stats.error == 0
    assert sent == [("parse", i) for i in range(20)]


async def test_open_model_does_not_slow_down_with_slow_responses():
    """応答待ちが送出間隔を遅らせない(coordinated omissionの原理回避)。"""
    import sys

    if sys.platform == "win32":
        return  # Windowsのtimer精度では検証にならない(実環境はLinux)

    async def sender(plan):
        await asyncio.sleep(0.3)  # 遅い応答を模擬

    plans = constant_rate_plan(rate_per_sec=200.0, count=20, kind="create")
    start = time.perf_counter()
    stats = await run_open_model(plans, sender=sender, clock=SystemClock())
    elapsed = time.perf_counter() - start
    assert stats.ok == 20
    # クローズドモデルなら 20 × (0.005 + 0.3) ≈ 6.1秒。オープンモデルでは
    # 送出が0.1秒で終わり・全体は最後の応答(約0.3秒)待ちのみ
    assert elapsed < 2.0


async def test_open_model_records_client_elapsed_ms():
    async def sender(plan):
        await asyncio.sleep(0.01)

    plans = burst_plan(count=5, duration_s=0.05, kind="create")
    stats = await run_open_model(plans, sender=sender, clock=SystemClock())
    assert len(stats.client_elapsed_ms) == 5
    assert stats.client_p95_ms >= 10.0  # nearest-rank p95は応答時間を下回らない


async def test_open_model_counts_errors_and_continues():
    async def sender(plan):
        if plan.seq % 3 == 0:
            raise RuntimeError("injected")

    plans = constant_rate_plan(rate_per_sec=1000.0, count=9, kind="parse")
    stats = await run_open_model(plans, sender=sender, clock=SystemClock())
    assert stats.ok == 6
    assert stats.error == 3
    assert len(stats.errors) == 3
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_schedule.py -v`
Expected: FAIL(ModuleNotFoundError: latch.loadgenが存在しない)

- [ ] **Step 3: schedule.pyを実装する**

`backend/src/latch/loadgen/__init__.py` は空ファイル。

`backend/src/latch/loadgen/schedule.py`:

```python
"""決定的送出時刻列とオープンモデル実行器(M4 ws-6・design §2.1案A)。

予定時刻(開始からの相対秒)の列を決定的に生成する。実行側は予定時刻まで
待って送出し・応答の完了を送出間隔に含めない(k6のconstant-arrival-rateと
同型のオープンモデル — coordinated omission〔応答遅延が投入間隔を遅らせて
測定を甘くする現象〕を原理回避)。時刻参照はClock経由(C2・arch test対象)。
待機はasyncio.sleep(待機であり時刻参照ではない — stub.pyと同一規律)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from latch.core.clock import Clock


@dataclass(frozen=True)
class SendPlan:
    """1送出の予定(開始からの相対秒・種別・順序番号)。"""

    at: float  # 開始からの相対秒(送出予定時刻)
    kind: str  # "parse" | "create" | "draft_activate"
    seq: int   # シナリオ内の通し番号(ペイロード決定生成の入力)


def constant_rate_plan(*, rate_per_sec: float, count: int, kind: str) -> list[SendPlan]:
    """固定到着率の予定列(0, 1/rate, 2/rate, ...秒・決定的)。"""
    interval = 1.0 / rate_per_sec
    return [SendPlan(at=i * interval, kind=kind, seq=i) for i in range(count)]


def burst_plan(*, count: int, duration_s: float, kind: str) -> list[SendPlan]:
    """バーストの予定列(duration_sへ均し配置・決定的)。"""
    interval = duration_s / count
    return [SendPlan(at=i * interval, kind=kind, seq=i) for i in range(count)]


@dataclass
class RunStats:
    """オープンモデル実行の結果(送出数・成否・クライアント側応答時間)。"""

    planned: int
    sent: int = 0
    ok: int = 0
    error: int = 0
    errors: list[str] = field(default_factory=list)
    client_elapsed_ms: list[float] = field(default_factory=list)

    @property
    def client_p95_ms(self) -> float:
        """クライアント側応答時間の参考値(nearest-rank p95)。"""
        if not self.client_elapsed_ms:
            return 0.0
        ordered = sorted(self.client_elapsed_ms)
        return ordered[max(0, round(0.95 * len(ordered)) - 1)]


async def run_open_model(
    plans: list[SendPlan],
    *,
    sender,
    clock: Clock,
) -> RunStats:
    """オープンモデル実行(予定時刻まで待つ・応答は送出間隔に含めない)。

    sender(plan)は応答時間を内部で測るためawaitするが・その完了は次の
    送出予定を遅らせない(create_taskで投げっぱなし・送出ループは予定時刻
    だけを見る)。例外は集計して続行(注入器の失敗が試験を止めない)。
    戻り値の統計は全応答完了後に返す(収束待ち=wait_settledは別・Task 8)。
    """
    stats = RunStats(planned=len(plans))
    start = clock.now()

    async def _one(plan: SendPlan) -> None:
        t0 = clock.now()
        stats.sent += 1
        try:
            await sender(plan)
            stats.ok += 1
            stats.client_elapsed_ms.append((clock.now() - t0).total_seconds() * 1000)
        except Exception as exc:
            stats.error += 1
            stats.errors.append(f"{plan.kind}#{plan.seq}: {type(exc).__name__}")

    tasks: list[asyncio.Task] = []
    try:
        for plan in plans:
            now = clock.now()
            delay = plan.at - (now - start).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(_one(plan)))
        if tasks:
            await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    return stats
```

- [ ] **Step 4: 試験が通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_schedule.py -v`
Expected: PASS(6件)

- [ ] **Step 5: コミットする**

```bash
git add backend/src/latch/loadgen/ backend/tests/unit/test_loadgen_schedule.py
git commit -m "feat: loadgen/schedule — 決定的送出時刻列とオープンモデル実行器(M4 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: loadgen/client.py・massload.py(注入クライアントと母集合投入)

**Files:**
- Create: `backend/src/latch/loadgen/client.py`
- Create: `backend/src/latch/loadgen/massload.py`
- Test: `backend/tests/unit/test_loadgen_massload.py`(純関数)
- Create: `backend/tests/integration/test_loadgen_massload_ci.py`(骨格・collect-only検査)

**Interfaces:**
- Consumes: `latch.auth.tools.issue_idp_token`(既存関数)・`latch.testassets.vocab.LOCATIONS`(7地名)・`latch.llm.stub._stub_vector`・`latch.worker.embedding_text.build_embedding_text`・`latch.intents.completion.nearest_expires_at`・`latch.llm.gemini.GEMINI_EMBEDDING_MODEL`
- Produces(Task 8が消費):
  `DEFAULT_BASE_URL = "http://127.0.0.1:28000"`・`DEFAULT_KEYS_DIR = ".staging/keys"`・`LOAD_USER_SUBJECT = "load-u{nnnn:04d}"`(`.format(nnnn=i+1)`で使用)・`DEFAULT_DSN = "postgresql://latch:latch@127.0.0.1:25432/latch"`
  `class LoadClient(base_url=, keys_dir=)` — `create_user(http, subject, display_name, birth_date, bio) -> int`(201=作成・409=既存・他は例外)・`consent(http, subject) -> None`・`create_intent(http, subject, payload) -> tuple[int, str]`(201時はintent_id)・`draft_activate(http, subject, payload) -> int`(draft作成→同内容でPATCH active化)・`parse_intent(http, subject, text) -> int`
  `perf_intent_payload(seq: int, *, started_at: datetime) -> dict`・`parse_text(seq: int) -> str`
  `massload.load_population(*, dsn: str, seed: dict, now: datetime) -> dict`(投入集計)・`massload.to_user_rows(seed, now)`・`massload.to_intent_rows(seed, user_ids, now)`・`massload.FLAT_STRUCTURED(i)`(平ら化純関数・試験が消費)

- [ ] **Step 1: 失敗する試験を書く(massload純関数)**

`backend/tests/unit/test_loadgen_massload.py`:

```python
"""massload行組み立て純関数の期待値(M4 ws-6・design §2.4案A)。

実DBのINSERT・active条件照合はintegration(test_loadgen_massload_ci.py)が
所有する。unitでは決定性・structured_dataの保存形式・embeddingの決定生成・
match_events非触りの静的検証を行う(DB不使用)。
"""

import uuid
from datetime import UTC, datetime

import pytest

from latch.llm.gemini import GEMINI_EMBEDDING_MODEL
from latch.llm.stub import _stub_vector
from latch.loadgen import massload
from latch.worker.embedding_text import EmbeddingTextInput, build_embedding_text

NOW = datetime(2026, 10, 4, 3, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def seed() -> dict:
    """決定的生成器の小さい実出力(count=70=EX64+残余6・DB不要)。"""
    from latch.testassets.generator import build_seed

    return build_seed(datetime(2026, 10, 4, 2, 0, 0), count=70)


def test_to_user_rows_uses_load_prefix_and_passes_seed_fields(seed):
    rows = massload.to_user_rows(seed, NOW)
    assert len(rows) == 100  # generatorのusersは100人固定
    first = rows[0]
    assert first[0] == seed["users"][0]["display_name"]  # display_name
    assert first[3].startswith("massload-")  # auth_subjectはmassload接頭辞
    assert first[4] == NOW  # consent_at・created_at・updated_atは同一now


def test_flat_structured_matches_saved_shape(seed):
    """平らな保存形式(mapping.pyと同型の5キー+location_name)。"""
    i = seed["intents"][0]
    sd = massload.FLAT_STRUCTURED(i)
    assert set(sd.keys()) == {
        "category_secondary",
        "soft_constraints",
        "negative_constraints",
        "time_flexibility_minutes",
        "location_flexibility",
        "location_name",
    }
    assert sd["negative_constraints"] == []
    for c in sd["soft_constraints"]:
        assert set(c.keys()) == {"text", "downgraded_from_ng"}


def test_user_ids_map_is_required_for_intents(seed):
    """intents行はuser_idを引くため・users投入後のsubject→idマップを要求する。"""
    ids = {"massload-SU-001": uuid.uuid4()}
    rows = massload.to_intent_rows(seed, ids, NOW)
    assert len(rows) == len(seed["intents"])
    first = rows[0]
    assert first[1] == ids["massload-SU-001"]


def test_intent_rows_are_deterministic(seed):
    ids = {"massload-SU-001": uuid.uuid4()}
    rows_a = massload.to_intent_rows(seed, ids, NOW)
    rows_b = massload.to_intent_rows(seed, ids, NOW)
    assert rows_a == rows_b
    # idは決定的uuid5(再投入べき等の根拠)
    assert rows_a[0][0] == uuid.uuid5(massload.MASSLOAD_NS, seed["intents"][0]["id"])


def test_intent_rows_embedding_matches_stub_vector(seed):
    """embedding列=_stub_vector(正規化テキスト)・768次元・model=既存の
    スタブ系記録と同一値(design §2.4)。列は文字列化済みのvectorリテラル。"""
    i = seed["intents"][0]
    rows = massload.to_intent_rows(seed, {"massload-SU-001": uuid.uuid4()}, NOW)
    row = rows[0]
    s = i["structured"]
    expected_text = build_embedding_text(
        EmbeddingTextInput(
            category_primary=s["category"]["primary"],
            structured_data=massload.FLAT_STRUCTURED(i),
            participants_min=s["participants"]["min"],
            participants_max=s["participants"]["max"],
            time_start=rows[0][14],
            time_end=rows[0][15],
        )
    )
    expected_vec = _stub_vector(expected_text)
    expected_literal = "[" + ",".join(repr(float(v)) for v in expected_vec) + "]"
    assert row[17] == expected_literal  # embedding列(位置17)
    assert row[17].startswith("[") and row[17].endswith("]")
    assert row[18] == GEMINI_EMBEDDING_MODEL  # model列(位置18)


def test_intent_rows_active_conditions(seed):
    """active条件の列: expires_at未来・geoあり・time列あり。status='active'は
    INSERT SQLのリテラル(静的ピン)・§2.2(c)。"""
    ids = {"massload-SU-001": uuid.uuid4()}
    rows = massload.to_intent_rows(seed, ids, NOW)
    for row in rows:
        assert row[16] > NOW  # expires_at(位置16)
        assert row[6] is not None and row[7] is not None  # geo lon/lat
        assert row[14] is not None  # time_start
    assert "'active'" in massload.INSERT_INTENT  # statusはSQLリテラル
    assert "consent_at" in massload.INSERT_USER  # Layer 1対象条件


def test_insert_statements_never_touch_match_events():
    """母集合投入はEvent発行しない(match_eventsへのINSERTなし・design §2.4)。"""
    for sql in (massload.INSERT_USER, massload.SELECT_USER_IDS, massload.INSERT_INTENT):
        assert "match_events" not in sql


def test_insert_user_uses_google_provider_and_consent():
    """auth_providerはCHECK制約('google','apple')上'google'でINSERT・consent_at設定。"""
    sql = massload.INSERT_USER
    assert "'google'" in sql
    assert "consent_at" in sql
    assert "ON CONFLICT (auth_provider, auth_subject) DO NOTHING" in sql


def test_insert_intent_is_idempotent_on_id():
    """intentsは決定的idでON CONFLICT DO NOTHING(段階間の累積投入)。"""
    assert "ON CONFLICT (id) DO NOTHING" in massload.INSERT_INTENT
    assert "ST_SetSRID(ST_MakePoint" in massload.INSERT_INTENT  # geo_center直書き
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_massload.py -v`
Expected: FAIL(MASSLOAD_NS等が存在しない)

- [ ] **Step 3: client.pyを実装する**

`backend/src/latch/loadgen/client.py`:

```python
"""負荷注入のHTTPクライアント(M4 ws-6)。

宛先はload環境のnginx入口(既定 http://127.0.0.1:28000)。IdPトークンは
latch.auth.tools.issue_idp_token(既存関数の直接呼出し・鍵=.staging/keys/
idp_test_private.pem・load composeが同一keysをroマウント=ws-1)で発行し・
POST /v1/auth/tokenでaccess_tokenへ回す(auth/routes.py TokenRequest契約・
stagingハーネスと同一経路)。クライアント側応答時間は参考値(測定の主系統
はサーバ側Prometheusヒストグラム・design §2.1案A)。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import httpx

from latch.auth.tools import issue_idp_token
from latch.testassets.vocab import LOCATIONS

DEFAULT_BASE_URL = "http://127.0.0.1:28000"
DEFAULT_KEYS_DIR = ".staging/keys"
DEFAULT_DSN = "postgresql://latch:latch@127.0.0.1:25432/latch"
LOAD_USER_SUBJECT = "load-u{nnnn:04d}"

# perf・総当たりの注入ペイロードの決定的語彙(10 §2の分散に寄せる)
_CATEGORIES = ("drinking", "meal", "activity")


def perf_intent_payload(seq: int, *, started_at: datetime) -> dict:
    """perf区間・総当たりのPOST /v1/intentsペイロード(決定的)。

    time_start=実行時刻+3時間+seq分(05 §5の7日上限内・try_promoteの
    75分ルールを満たす)。地名・カテゴリはseqから決定的選択(7地名分散・
    stub遅延のハッシュ入力を多様化)。alcohol_involvedはdrinkingのみTrue
    (負荷ユーザーは全員20歳以上で作成可)。raw_textは300字上限内。
    """
    places = list(LOCATIONS)
    place = places[seq % len(places)]
    category = _CATEGORIES[seq % len(_CATEGORIES)]
    start = started_at + timedelta(hours=3, minutes=seq % 60)
    end = start + timedelta(minutes=120)
    return {
        "raw_text": f"負荷試験{seq:05d}: {place}あたりで{category}の集まりを始めます",
        "status": "active",
        "structured_intent": {
            "category": {"primary": category, "secondary": None},
            "alcohol_involved": category == "drinking",
            "time": {"start": start.isoformat(), "end": end.isoformat()},
            "location": {"name": place},
            "budget": {"max": 3000},
            "participants": {"min": 2, "max": 4},
            "soft_constraints": [],
            "visibility": "hidden_until_match",
            "notification_level": "proposals_only",
        },
    }


def parse_text(seq: int) -> str:
    """parse相の入力テキスト(決定的・300字上限内)。"""
    places = list(LOCATIONS)
    place = places[seq % len(places)]
    return f"{seq % 7 + 1}日の夜に{place}あたりで{seq % 3 + 2}人ほど話したい"


def user_subject(seq: int) -> str:
    """注入のラウンドロビン先ユーザー(perf側・Active余裕は§2.5)。

    総当たり側は段階ごとにCLI引数(--first-user)で範囲を変える(Task 8)。
    """
    return LOAD_USER_SUBJECT.format(nnnn=(seq % 1000) + 1)


class LoadClient:
    """注入用の非同期クライアント(subject→access_tokenをキャッシュ)。"""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        keys_dir: str | Path = DEFAULT_KEYS_DIR,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._private_key = (Path(keys_dir) / "idp_test_private.pem").read_bytes()
        self._tokens: dict[str, str] = {}

    async def _ensure_token(self, http: httpx.AsyncClient, subject: str) -> None:
        if subject in self._tokens:
            return
        idp_token = issue_idp_token(
            provider="google", subject=subject, private_key_pem=self._private_key
        )
        r = await http.post(
            "/v1/auth/token",
            json={"provider": "google", "idp_token": idp_token},
        )
        r.raise_for_status()
        self._tokens[subject] = r.json()["access_token"]

    def _headers(self, subject: str) -> dict:
        return {"Authorization": f"Bearer {self._tokens[subject]}"}

    async def create_user(
        self,
        http: httpx.AsyncClient,
        subject: str,
        display_name: str,
        birth_date,
        bio: str,
    ) -> int:
        """POST /v1/users。戻り値201=作成・409=既存(再実行べき等)・他は例外。"""
        await self._ensure_token(http, subject)
        r = await http.post(
            "/v1/users",
            headers=self._headers(subject),
            json={
                "display_name": display_name,
                "birth_date": birth_date.isoformat(),
                "profile": {"bio": bio},
            },
        )
        if r.status_code not in (201, 409):
            raise RuntimeError(f"users {subject}: {r.status_code} {r.text[:200]}")
        return r.status_code

    async def consent(self, http: httpx.AsyncClient, subject: str) -> None:
        await self._ensure_token(http, subject)
        r = await http.post("/v1/users/me/consent", headers=self._headers(subject))
        if r.status_code != 200:
            raise RuntimeError(f"consent {subject}: {r.status_code} {r.text[:200]}")

    async def create_intent(
        self, http: httpx.AsyncClient, subject: str, payload: dict
    ) -> tuple[int, str]:
        """POST /v1/intents。201時は(201, intent_id)を返す。"""
        await self._ensure_token(http, subject)
        r = await http.post(
            "/v1/intents", headers=self._headers(subject), json=payload
        )
        if r.status_code != 201:
            raise RuntimeError(
                f"intent create {subject}: {r.status_code} {r.text[:200]}"
            )
        return 201, str(r.json()["intent"]["id"])

    async def draft_activate(
        self, http: httpx.AsyncClient, subject: str, payload: dict
    ) -> int:
        """draft作成→同内容でPATCH active化(design §2.5のactivate経路)。

        E2Eの起点はactive化コミット時刻(match_events.created_at・route=
        activate)。draft作成のevent発行はないため計画数の1計画で2リクエスト。
        """
        await self._ensure_token(http, subject)
        draft_payload = dict(payload)
        draft_payload["status"] = "draft"
        r = await http.post(
            "/v1/intents", headers=self._headers(subject), json=draft_payload
        )
        if r.status_code != 201:
            raise RuntimeError(f"draft create {subject}: {r.status_code} {r.text}")
        intent_id = str(r.json()["intent"]["id"])
        # PATCH契約(IntentPatchRequest・intent_input.py): raw_text必須・
        # status active・structured_intentで全置換
        r2 = await http.patch(
            f"/v1/intents/{intent_id}",
            headers=self._headers(subject),
            json={
                "raw_text": payload["raw_text"],
                "status": "active",
                "structured_intent": payload["structured_intent"],
            },
        )
        if r2.status_code != 200:
            raise RuntimeError(
                f"draft activate {subject}: {r2.status_code} {r2.text[:200]}"
            )
        return 200

    async def parse_intent(
        self, http: httpx.AsyncClient, subject: str, text: str
    ) -> int:
        """POST /v1/intents/parse(目標②の測定対象・DB書込みなし)。"""
        await self._ensure_token(http, subject)
        r = await http.post(
            "/v1/intents/parse",
            headers=self._headers(subject),
            json={"text": text},
        )
        if r.status_code != 200:
            raise RuntimeError(f"parse {subject}: {r.status_code} {r.text[:200]}")
        return 200
```

注意: `draft_activate` のPATCHボディ(`{"status": "active", "structured_intent": ...}`)は実装時に backend/src/latch/intents/routes.py のPATCH宣言を読んで合わせる(§0の「詰まったら」規律)。DraftPayloadは必須3を含む同内容のため・draft作成は形式検証を通る。

- [ ] **Step 4: massload.pyを実装する**

`backend/src/latch/loadgen/massload.py`:

```python
"""総当たり母集合のDB直接投入(M4 ws-6・design §2.4案A)。

generatorの決定的シード(dict)を受け・users/intentsをバルクINSERT・
embedding列へ直書き(_stub_vector・正規化テキストはbuild_embedding_text
経由=workerと同一の決定規則)。Event発行なし(match_events無増加=観測を
汚さない)。API側レート制限は源流保護の本体(08 §5.4)で試験用に外せない
ためAPI経由を採らない。usersのauth_subjectはmassload-接頭辞で・既存データ
(integration試験のmasstest-・stagingハーネス)と混在しない。宛先は
load環境DB(127.0.0.1:25432)でtest-ci対象外(§2.2(c))。
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path

import asyncpg

from latch.intents.completion import nearest_expires_at
from latch.llm.gemini import GEMINI_EMBEDDING_MODEL
from latch.llm.stub import _stub_vector
from latch.testassets.vocab import LOCATIONS
from latch.worker.embedding_text import EmbeddingTextInput, build_embedding_text

# 決定的id生成のnamespace(同一seed id→同一UUID=段階間の累積投入がべき等)
MASSLOAD_NS = uuid.UUID("6f6f6f6f-6f6f-4f6f-8f6f-6f6f6f6f6f6f")
BATCH_SIZE = 1000
USER_PREFIX = "massload-"

# users(auth_subjectはmassload-接頭辞・consent_at=投入時刻でLayer 1対象に)
INSERT_USER = """
    INSERT INTO users (display_name, profile, birth_date, auth_provider,
                       auth_subject, consent_at, created_at, updated_at)
    VALUES ($1, $2::jsonb, $3, 'google', $4, $5, $5, $5)
    ON CONFLICT (auth_provider, auth_subject) DO NOTHING
"""
SELECT_USER_IDS = "SELECT id, auth_subject FROM users WHERE auth_provider = 'google'"
INSERT_INTENT = """
    INSERT INTO intents (id, user_id, category_primary, alcohol_involved, raw_text,
        structured_data, geo_center, geo_radius_m, budget_max, participants_min,
        participants_max, visibility, notification_level, status, version,
        time_start, time_end, expires_at, embedding, embedding_model,
        created_at, updated_at)
    VALUES ($1, $2, $3, $4, $5, $6::jsonb,
        ST_SetSRID(ST_MakePoint($7, $8), 4326)::geography,
        $9, $10, $11, $12, $13, $14, 'active', 1,
        $15, $16, $17, $18, $19, $20, $20)
    ON CONFLICT (id) DO NOTHING
"""


def FLAT_STRUCTURED(i: dict) -> dict:
    """YAML structured(API形式)→DB保存形式(mapping.pyと同型の平らな5キー
    +location_name・05 §2)。"""
    s = i["structured"]
    return {
        "category_secondary": s["category"].get("secondary"),
        "soft_constraints": [
            {"text": t, "downgraded_from_ng": False}
            for t in (s.get("soft_constraints") or [])
        ],
        "negative_constraints": [],
        "time_flexibility_minutes": None,
        "location_flexibility": None,
        "location_name": s["location"]["name"],
    }


def to_user_rows(seed: dict, now: datetime) -> list[tuple]:
    """YAML users→INSERT_USERパラメータ(決定的)。"""
    rows = []
    for u in seed["users"]:
        rows.append(
            (
                u["display_name"],
                json.dumps(u.get("profile") or {}, ensure_ascii=False),
                date.fromisoformat(str(u["birth_date"])[:10]),
                f"{USER_PREFIX}{u['id']}",
                now,
            )
        )
    return rows


def to_intent_rows(
    seed: dict, user_ids: dict[str, uuid.UUID], now: datetime
) -> list[tuple]:
    """YAML intents→INSERT_INTENTパラメータ(決定的・embedding直書き)。

    id=uuid5(MASSLOAD_NS, seed id)。expires_at=nearest_expires_at(time_start,
    now)(API保存と同一規則・active条件の「未来」を満たす)。embedding=
    _stub_vector(build_embedding_text(...))・model=GEMINI_EMBEDDING_MODEL
    (workerのスタブ系記録と同一値・design §2.4)。status='active'・version=1。
    """
    rows: list[tuple] = []
    for i in seed["intents"]:
        s = i["structured"]
        flat = FLAT_STRUCTURED(i)
        time_start = datetime.fromisoformat(str(s["time"]["start"]))
        time_end_raw = s["time"].get("end")
        time_end = (
            datetime.fromisoformat(str(time_end_raw)) if time_end_raw else None
        )
        text = build_embedding_text(
            EmbeddingTextInput(
                category_primary=s["category"]["primary"],
                structured_data=flat,
                participants_min=s["participants"]["min"],
                participants_max=s["participants"]["max"],
                time_start=time_start,
                time_end=time_end,
            )
        )
        vec = _stub_vector(text)
        vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
        lat, lon = LOCATIONS[s["location"]["name"]]
        rows.append(
            (
                uuid.uuid5(MASSLOAD_NS, i["id"]),
                user_ids[f"{USER_PREFIX}{i['user']}"],
                s["category"]["primary"],
                bool(s["alcohol_involved"]),
                i["raw_text"],
                json.dumps(flat, ensure_ascii=False),
                lon,
                lat,
                s["location"].get("radius_m"),
                s.get("budget", {}).get("max"),
                s["participants"]["min"],
                s["participants"]["max"],
                s["visibility"],
                s["notification_level"],
                time_start,
                time_end,
                nearest_expires_at(time_start, now),
                vec_literal,
                GEMINI_EMBEDDING_MODEL,
                now,
            )
        )
    return rows


async def load_population(*, dsn: str, seed: dict, now: datetime) -> dict:
    """母集合投入(決定的・べき等)。戻り値は投入・母集合の集計。

    INSERT前後のcount比較で実挿入数を計上する(executemanyはON CONFLICTの
    分も進行するため)。match_eventsへは一切書かない(INSERT_USER・
    INSERT_INTENTのみ)。
    """
    conn = await asyncpg.connect(dsn)
    try:
        before_users = await conn.fetchval(
            "SELECT count(*) FROM users WHERE auth_provider = 'google'"
            " AND auth_subject LIKE $1",
            f"{USER_PREFIX}%",
        )
        await conn.executemany(
            INSERT_USER, to_user_rows(seed, now)
        )
        after_users = await conn.fetchval(
            "SELECT count(*) FROM users WHERE auth_provider = 'google'"
            " AND auth_subject LIKE $1",
            f"{USER_PREFIX}%",
        )
        ids = {
            row["auth_subject"]: row["id"] for row in await conn.fetch(SELECT_USER_IDS)
        }
        rows = to_intent_rows(seed, ids, now)
        before_intents = await conn.fetchval(
            "SELECT count(*) FROM intents"
        )
        for start in range(0, len(rows), BATCH_SIZE):
            await conn.executemany(
                INSERT_INTENT, rows[start : start + BATCH_SIZE]
            )
        after_intents = await conn.fetchval("SELECT count(*) FROM intents")
        active_total = await conn.fetchval(
            "SELECT count(*) FROM intents WHERE status='active'"
        )
        events_total = await conn.fetchval("SELECT count(*) FROM match_events")
        return {
            "users_requested": len(seed["users"]),
            "users_inserted": after_users - before_users,
            "intents_requested": len(rows),
            "intents_inserted": after_intents - before_intents,
            "active_total": active_total,
            "match_events_total": events_total,
        }
    finally:
        await conn.close()
```

注意: to_intent_rowsの埋め込みテキストは・generator YAMLのtime列がISO文字列である点(fromisoformatで変換)・YAMLのbirth_dateが「YYYY-MM-DD」表記である点を・実装時にdocs/testassets/m4-seed.yaml(既存生成物)で確認する。

- [ ] **Step 5: unit試験が通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_massload.py -v`
Expected: PASS(9件)

- [ ] **Step 6: integration試験の骨格を置く(collect-onlyまで検査)**

`backend/tests/integration/test_loadgen_massload_ci.py`:

```python
"""massloadの実DB検証(M4 ws-6・integration・test-ciで実行)。

少量fixture(20件)を投げ・active条件の全列を照合し・投入した行を自前で
掃除する(masstest-接頭辞・既存行への触りなし・§2.2(c))。
"""

import pytest
import yaml

pytestmark = pytest.mark.integration

# 実装時に次の4試験をこの骨格へ入れる(既存integration試験
# test_obs_schema.pyのconftest fixture[db_engine]と同一作法):
# - test_massload_inserts_active_rows_with_embedding — 20件投入→
#   status='active'・embedding IS NOT NULL・embedding_model='gemini-embedding-001'
#   ・geo_center IS NOT NULL・expires_at > now・users.consent_at IS NOT NULL
# - test_massload_publishes_no_events — 投入前後でmatch_events countが不変
# - test_massload_is_idempotent — 2回投入で行数不変(ON CONFLICT DO NOTHING)
# - test_massload_cleanup_after_run — 投入行のDELETE(接頭辞で絞る)で
#   users/intentsとも元の件数へ戻る
# 投入は massload.load_population(dsn=, seed=小さなbuild_seed(--count 70を
# 20件に絞るため・to_user_rows/to_intent_rowsのスライスではなく
# build_seed(count=70)からusersはそのまま・intentsはSD-001〜020の辞書を
# {"users": [...], "intents": [...]}へ組んで渡す — EX素材は先頭64件・
# 決定的)。dsnは環境変数LATCH_TEST_MASSLOAD_DSN(既定127.0.0.1:25432・
# test-ci実行時はスーパーバイザーがload環境DB宛を指定)。

async def test_placeholder_will_be_filled(db_engine):  # noqa: ARG001
    """骨格(収集確認のみ)。実装は上記コメントどおり・collect-only対象。"""
    pytest.fail("integration本体は実装時に書く(§0規律でagent3は実行しない)")
```

注意: 上記は骨格のみ(実DB経路はスーパーバイザー検証時のtest-ciで走る)。**agent3はcollect-only確認まで(実行はしない)**。ただし**本Stepで4試験の本体コードを書き切ってからコミットする**(ファイル内コメントの指示どおり・骨格の `test_placeholder_will_be_filled` はプレースホルダーであり**コミット対象外**)。手順: ①骨格を置いてcollect-onlyで1件収集を確認 → ②4試験の本体を実装(指示の4試験・fixtureはtest_obs_schema.pyの[db_engine]と同一作法) → ③collect-onlyで4件収集を確認。**実行はスーパーバイザー検証時のみ**(§0規律):

Run: `cd backend && uv run pytest tests/integration/test_loadgen_massload_ci.py --collect-only -q`
Expected: 4件収集(実行はしない)

- [ ] **Step 7: コミットする**

```bash
git add backend/src/latch/loadgen/client.py backend/src/latch/loadgen/massload.py backend/tests/unit/test_loadgen_massload.py backend/tests/integration/test_loadgen_massload_ci.py
git commit -m "feat: loadgen/client・massload — 注入クライアントと母集合DB直接投入(イベント発行ゼロ)(M4 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

**Task 7の補足(client.pyの公開関数)**: 注入ユーザーの選択関数は `user_subject(seq: int) -> str` としてclient.pyへ置く(`_user_subject` から名前をpublic化・`LOAD_USER_SUBJECT.format(nnnn=(seq % 1000) + 1)` を返す・perf側が使う)。総当たり側は段階ごとに注入ユーザー範囲をCLI引数で変える(Task 8・Active 5件制限の余裕維持)。

---

### Task 8: loadgen/collect.py(証拠採取)と__main__.py(CLI)

**Files:**
- Create: `backend/src/latch/loadgen/collect.py`
- Create: `backend/src/latch/loadgen/__main__.py`
- Test: `backend/tests/unit/test_loadgen_collect.py`

**Interfaces:**
- Consumes: `schedule.run_open_model`・`client.LoadClient`・`massload.load_population`・`testassets.generator.build_seed/write_yaml`(既存)・`Clock`
- Produces: `collect.PROM_URL`・`collect.RESULTS_DIR`・`PERF_QUERIES`・`BRUTEFORCE_QUERIES`(辞書・クエリ内に`{WINDOW}`プレースホルダ)・`_q(expr, window) -> str`・`prom_query(expr) -> list[dict]`・`query_scalar(expr) -> float | None`・`env_note() -> dict`・`collect_perf_report(*, window, stats_summary, env_note, clock) -> dict`・`collect_bruteforce_report(*, stage, window, injected_events, env_note, clock) -> dict`・`write_evidence(report, *, prefix, results_dir=RESULTS_DIR) -> Path`。Task 9のピン試験がPERF_QUERIESの構造を、Task 10の報告書が証拠YAMLを消費

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_loadgen_collect.py`:

```python
"""証拠採取の照合と組み立て(M4 ws-6)。

Prometheus照合の実動作は手順(§8)が所有する。unitではクエリの静的性質
(load側jobのみ・5目標と層別の網羅)と・モック照合での機械判定を検証する。
"""

from latch.core.clock import SystemClock
from latch.loadgen import collect


def test_perf_queries_cover_five_targets_and_layers():
    """5目標(①保存②parse③retrieval④E2E⑤通知)+層別+queueのクエリが揃う。"""
    keys = set(collect.PERF_QUERIES.keys())
    assert {
        "save_p95_s",
        "save_p99_s",
        "parse_p95_s",
        "parse_p99_s",
        "retrieval_p95_s",
        "embedding_p95_s",
        "proposal_p95_s",
        "jev_p95_s",
        "e2e_p95_create_s",
        "e2e_p95_activate_s",
        "notify_p95_s",
        "queue_pending_max",
    } <= keys


def test_queries_target_load_jobs_only():
    """perf・bfの全クエリはload側jobのみを叩く(stagingのseriesを混ぜない)。"""
    for expr in collect.PERF_QUERIES.values():
        assert 'job="latch-load-' in expr
        assert 'job="latch-staging-' not in expr
    for expr in collect.BRUTEFORCE_QUERIES.values():
        assert 'job="latch-load-' in expr


def test_window_replacement():
    assert collect._q("foo_bar[{WINDOW}]", "15m") == "foo_bar[15m]"


async def test_query_scalar_returns_none_when_empty(monkeypatch):
    async def fake_query(expr):
        return []

    monkeypatch.setattr(collect, "prom_query", fake_query)
    assert await collect.query_scalar("anything") is None


async def test_bruteforce_verdict_marks_vector_le50(monkeypatch):
    """le=50累積==総累積 → vector_le_50_ok=True(10 §4.2の機械判定)。"""
    values = {
        "vector_items_le50_rate": 123.0,
        "vector_items_inf_rate": 123.0,
        "retrieval_p95_s": 0.4,
        "hardfilter_pass_rate": 0.6,
        "jev_executions_total_increase": 132.0,
    }

    async def fake_scalar(expr):
        for name, expr_raw in collect.BRUTEFORCE_QUERIES.items():
            if collect._q(expr_raw, "30m") == expr:
                return values[name]
        return None

    monkeypatch.setattr(collect, "query_scalar", fake_scalar)
    report = await collect.collect_bruteforce_report(
        stage=10000,
        window="30m",
        injected_events=100,
        env_note_data={"host": "test"},
        clock=SystemClock(),
    )
    assert report["verdict"]["vector_le_50_ok"] is True
    assert report["input_conditions"]["injected_events"] == 100
    assert report["input_conditions"]["latency_injection"] == "none"


async def test_bruteforce_verdict_marks_violation(monkeypatch):
    """le=50を超える観測がある → 機械判定はFalse(修正を待たずに明示)。"""
    values = {
        "vector_items_le50_rate": 120.0,
        "vector_items_inf_rate": 123.0,
        "retrieval_p95_s": 0.4,
        "hardfilter_pass_rate": 0.6,
        "jev_executions_total_increase": 132.0,
    }

    async def fake_scalar(expr):
        for name, expr_raw in collect.BRUTEFORCE_QUERIES.items():
            if collect._q(expr_raw, "30m") == expr:
                return values[name]
        return None

    monkeypatch.setattr(collect, "query_scalar", fake_scalar)
    report = await collect.collect_bruteforce_report(
        stage=10000,
        window="30m",
        injected_events=100,
        env_note_data={"host": "test"},
        clock=SystemClock(),
    )
    assert report["verdict"]["vector_le_50_ok"] is False
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_collect.py -v`
Expected: FAIL(PERF_QUERIES等が存在しない)

- [ ] **Step 3: collect.pyを実装する**

`backend/src/latch/loadgen/collect.py`:

```python
"""負荷試験・総当たりの証拠採取(M4 ws-6・10 §5形式)。

Prometheus照合(staging_tests_obs/obs_results.pyと同型の/api/v1/query)・
histogram_quantileによる分布採取・総当たりの機械判定・証拠YAML出力。
絶対値5目標の合否判定はG4の人間側(design §2.7)であり・本モジュールは
測定値と機械判定(Vector件数≤50)を記録する。実行時刻はClock経由(C2)。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import yaml

from latch.core.clock import Clock

PROM_URL = "http://127.0.0.1:19090"
RESULTS_DIR = Path("docs/testassets/results")

# perf区間のPromQL(§2.5の表・jobはload側のみ)。{WINDOW}は実行時に差し替える。
# label値はprometheus-clientの露出形式(reprの浮動小数表記・例 le="50.0")
PERF_QUERIES = {
    # 目標① Intent保存 p95 500ms(10 §4.1)
    "save_p95_s": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'latch_http_request_duration_seconds_bucket{job="latch-load-api",'
        'method="POST",path="/v1/intents"}[{WINDOW}])))'
    ),
    "save_p99_s": (
        'histogram_quantile(0.99, sum by (le) (rate('
        'latch_http_request_duration_seconds_bucket{job="latch-load-api",'
        'method="POST",path="/v1/intents"}[{WINDOW}])))'
    ),
    # 目標② Parser構造化プレビュー p95 3秒・p99 8秒(相A=parse独立相で採取)
    "parse_p95_s": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'latch_http_request_duration_seconds_bucket{job="latch-load-api",'
        'method="POST",path="/v1/intents/parse"}[{WINDOW}])))'
    ),
    "parse_p99_s": (
        'histogram_quantile(0.99, sum by (le) (rate('
        'latch_http_request_duration_seconds_bucket{job="latch-load-api",'
        'method="POST",path="/v1/intents/parse"}[{WINDOW}])))'
    ),
    # 目標③ Candidate Retrieval p95 1秒(層別=Retrieval区間)
    "retrieval_p95_s": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'latch_pipeline_layer_seconds_bucket{job="latch-load-worker",'
        'layer="retrieval"}[{WINDOW}])))'
    ),
    # 層別予算の残り(Embedding 2秒・L5+通知 2秒)とJev(5秒=既存ヒストグラム)
    "embedding_p95_s": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'latch_pipeline_layer_seconds_bucket{job="latch-load-worker",'
        'layer="embedding"}[{WINDOW}])))'
    ),
    "proposal_p95_s": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'latch_pipeline_layer_seconds_bucket{job="latch-load-worker",'
        'layer="proposal"}[{WINDOW}])))'
    ),
    "jev_p95_s": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'latch_jev_latency_seconds_bucket{job="latch-load-worker"}[{WINDOW}])))'
    ),
    # 目標④ 初期判定E2E p95 10秒(route=create/activateで経路別・読み替え1)
    "e2e_p95_create_s": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'latch_initial_e2e_seconds_bucket{job="latch-load-worker",'
        'route="create"}[{WINDOW}])))'
    ),
    "e2e_p95_activate_s": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'latch_initial_e2e_seconds_bucket{job="latch-load-worker",'
        'route="activate"}[{WINDOW}])))'
    ),
    # 目標⑤ 通知は判定後5秒以内
    "notify_p95_s": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'latch_notification_latency_seconds_bucket{job="latch-load-worker"}'
        '[{WINDOW}])))'
    ),
    # 収束観察(処理待ちの滞留)
    "queue_pending_max": (
        'max_over_time(latch_event_queue_pending{job="latch-load-worker"}'
        '[{WINDOW}])'
    ),
}

# 総当たり段階のPromQL(10 §4.2・観測=Vector件数・判定レイテンシ・Jev回数)
BRUTEFORCE_QUERIES = {
    # Vector件数≤50の機械判定: le="50.0"の累積=総累積なら50超えゼロ
    "vector_items_le50_rate": (
        'sum by (le) (increase(latch_retrieval_vector_items_bucket'
        '{job="latch-load-worker",le="50.0"}[{WINDOW}]))'
    ),
    "vector_items_inf_rate": (
        'sum by (le) (increase(latch_retrieval_vector_items_bucket'
        '{job="latch-load-worker",le="+Inf"}[{WINDOW}]))'
    ),
    "retrieval_p95_s": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'latch_pipeline_layer_seconds_bucket{job="latch-load-worker",'
        'layer="retrieval"}[{WINDOW}])))'
    ),
    # Layer 1通過率(通過/入力・段階比較表の素材)
    "hardfilter_pass_rate": (
        'sum(increase(latch_hardfilter_pass_total{job="latch-load-worker"}'
        '[{WINDOW}])) / clamp_min(sum(increase('
        'latch_hardfilter_input_total{job="latch-load-worker"}[{WINDOW}])), 1)'
    ),
    # Jev実行回数の増分(1イベントあたり値は報告書がinjected_eventsで割る)
    "jev_executions_total_increase": (
        'sum(increase(latch_jev_executions_total{job="latch-load-worker"}'
        '[{WINDOW}]))'
    ),
}


def _q(expr: str, window: str) -> str:
    return expr.replace("{WINDOW}", window)


async def prom_query(expr: str) -> list[dict]:
    """Prometheusの即時クエリ(結果seriesのlist・obs_results.pyと同型)。"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{PROM_URL}/api/v1/query", params={"query": expr})
    if r.status_code != 200:
        raise RuntimeError(f"prometheus query failed: {r.status_code} {r.text[:200]}")
    return r.json()["data"]["result"]


async def query_scalar(expr: str) -> float | None:
    """即時クエリの単値(結果が空ならNone=観測窓に標本なし)。"""
    result = await prom_query(expr)
    if not result:
        return None
    return float(result[0]["value"][1])


def env_note() -> dict:
    """実施環境の記録(10 §5の「環境」欄・ローカルdockerの申告込み)。"""
    import platform

    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "note": (
            "ローカルdocker(latch-loadプロジェクト・ci一時停止運用)。"
            "絶対値の解釈はG4の人間側が本記録込みで判定(design §2.7)"
        ),
    }


async def collect_perf_report(
    *,
    window: str,
    stats_summary: dict,
    env_note_data: dict,
    clock: Clock,
) -> dict:
    """perf区間の証拠(10 §5形式)。判定はrecorded・G4の人間側(design §2.7)。"""
    measurements = {}
    for name, expr in PERF_QUERIES.items():
        measurements[name] = await query_scalar(_q(expr, window))
    return {
        "unit": "M4-ws-6",
        "test_id": "perf-load",
        "executed_at": clock.now().isoformat(),
        "environment": env_note_data,
        "input_conditions": {
            "load_profile": (
                "相A parse 50rps x60s → 相B save 50rps x60s"
                "(active主経路+draft→active化) → 相C burst 100件/1s → 収束待ち"
            ),
            "window": window,
            "parse_phase_note": "目標②はparse独立相で採取(負荷条件は相B+相Cで字義どおり)",
            "injection_stats": stats_summary,
        },
        "verdict": "recorded (絶対値5目標の判定はG4の人間側・design §2.7)",
        "measurements": measurements,
    }


async def collect_bruteforce_report(
    *,
    stage: int,
    window: str,
    injected_events: int,
    env_note_data: dict,
    clock: Clock,
) -> dict:
    """総当たり段階の証拠。Vector件数≤50は機械判定(10 §4.2)。

    機械判定: le="50.0"の累積が総累積(+Inf)と一致(±丸め許容)なら
    Vector件数50超えの観測はゼロ。段階比較・増幅率は報告書(Task 10)が
    複数段階の証拠から組み立てる。
    """
    measurements = {}
    for name, expr in BRUTEFORCE_QUERIES.items():
        measurements[name] = await query_scalar(_q(expr, window))
    le50 = measurements["vector_items_le50_rate"]
    total = measurements["vector_items_inf_rate"]
    vector_ok: bool | None = None
    if le50 is not None and total is not None and total > 0:
        vector_ok = le50 >= total - 0.5
    return {
        "unit": "M4-ws-6",
        "test_id": f"bruteforce-stage-{stage}",
        "executed_at": clock.now().isoformat(),
        "environment": env_note_data,
        "input_conditions": {
            "stage_active_intents": stage,
            "injected_events": injected_events,
            "latency_injection": "none",
            "window": window,
        },
        "verdict": {
            "vector_le_50_ok": vector_ok,
            "note": (
                "正式判定はG4の人間側(段階比較表+増幅率参考値・design §2.7)。"
                "量的合格線の新設は10 §6のdocs改版が前提"
            ),
        },
        "measurements": measurements,
    }


def write_evidence(
    report: dict, *, prefix: str, clock: Clock, results_dir: Path = RESULTS_DIR
) -> Path:
    """証拠YAML出力(10 §5・タイムスタンプ付き・docs/testassets/results/)。

    ファイル名の一意化のためのタイムスタンプもClock経由(C2・arch testの
    datetime.now/time.time禁止に従う)。
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = clock.now().strftime("%Y%m%d-%H%M%S")
    path = results_dir / f"{prefix}-{ts}.yaml"
    path.write_text(
        yaml.safe_dump(report, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path
```

- [ ] **Step 4: __main__.pyを実装する**

`backend/src/latch/loadgen/__main__.py`:

```python
"""負荷注入器CLI(M4 ws-6・design §3.1)。

python -m latch.loadgen users --count 1000      # 負荷用ユーザー事前作成
python -m latch.loadgen perf                    # 性能試験区間(3相+収束+証拠)
python -m latch.loadgen massload --stage 10000  # 母集合投入(base-datetime必須)
python -m latch.loadgen bruteforce --stage 10000 # 注入100件+収束+証拠

宛先の既定はload環境(base=28000系・dsn=25432)。ci・staging宛には使わない。
実施手順はdocs/plans/M4/ws-6-plan.md §8(makeラップあり)。実行時刻と収束
待ちはClock経由(C2)。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

import httpx

from latch.core.clock import Clock, SystemClock
from latch.loadgen import collect, massload
from latch.loadgen.client import (
    DEFAULT_BASE_URL,
    DEFAULT_DSN,
    DEFAULT_KEYS_DIR,
    LOAD_USER_SUBJECT,
    LoadClient,
    parse_text,
    perf_intent_payload,
    user_subject,
)
from latch.loadgen.schedule import (
    SendPlan,
    burst_plan,
    constant_rate_plan,
    run_open_model,
)
from latch.testassets.generator import build_seed, write_yaml

SEED_DIR = Path("backend/data/loadgen")
SETTLE_STABLE_SEC = 30.0
SETTLE_TIMEOUT_SEC = 600.0


async def wait_settled(
    *, dsn: str, clock: Clock, timeout_s: float = SETTLE_TIMEOUT_SEC
) -> float:
    """event_queue_pending=0が30秒連続するまで待つ(戻り値=所要秒)。"""
    import asyncpg

    start = clock.now()
    conn = await asyncpg.connect(dsn)
    try:
        stable_since: datetime | None = None
        while (clock.now() - start).total_seconds() < timeout_s:
            pending = await conn.fetchval(
                "SELECT count(*) FROM match_events WHERE status='pending'"
            )
            if pending == 0:
                if stable_since is None:
                    stable_since = clock.now()
                elif (clock.now() - stable_since).total_seconds() >= SETTLE_STABLE_SEC:
                    return (clock.now() - start).total_seconds()
            else:
                stable_since = None
            await asyncio.sleep(5.0)
    finally:
        await conn.close()
    raise TimeoutError(f"queue not settled within {timeout_s}s")


def _perf_plans(args) -> list[SendPlan]:
    """perf区間の全予定列(§2.5の3相・相間に1秒の空白)。

    seqは全体で一意(ペイロード決定生成とユーザー選択の多様化に使う)。
    相B末尾のdraft_activateは・draft作成→同senderでPATCH active化の連鎖。
    """
    plans: list[SendPlan] = []
    plans.extend(
        constant_rate_plan(rate_per_sec=args.rate, count=args.parse_count, kind="parse")
    )
    offset = plans[-1].at + 1.0
    save = constant_rate_plan(
        rate_per_sec=args.rate, count=args.save_active, kind="create"
    )
    plans.extend(
        SendPlan(at=p.at + offset, kind=p.kind, seq=p.seq + args.parse_count)
        for p in save
    )
    offset = plans[-1].at + 1.0
    seq_base = args.parse_count + args.save_active
    plans.extend(
        SendPlan(at=offset + i * 0.2, kind="draft_activate", seq=seq_base + i)
        for i in range(args.save_draft)
    )
    offset = plans[-1].at + 1.0
    plans.extend(
        SendPlan(at=offset + p.at, kind="create", seq=seq_base + args.save_draft + p.seq)
        for p in burst_plan(count=args.burst, duration_s=1.0, kind="create")
    )
    return plans


async def cmd_users(args) -> int:
    client = LoadClient(base_url=args.base_url, keys_dir=args.keys)
    created = existing = 0
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as http:
        for i in range(args.count):
            subject = LOAD_USER_SUBJECT.format(nnnn=i + 1)
            status = await client.create_user(
                http,
                subject,
                f"Load{i + 1:04d}",
                datetime(1990, 1, 1),
                "負荷試験用ユーザー",
            )
            if status == 201:
                created += 1
            else:
                existing += 1  # 409=既存(再実行べき等)
            await client.consent(http, subject)
    print(f"users ready: created={created} existing={existing} (consent済み)")
    return 0


async def cmd_perf(args) -> int:
    clock = SystemClock()
    client = LoadClient(base_url=args.base_url, keys_dir=args.keys)
    started_at = clock.now()
    plans = _perf_plans(args)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as http_client:

        async def sender(plan: SendPlan) -> None:
            subject = user_subject(plan.seq)
            if plan.kind == "parse":
                await client.parse_intent(
                    http_client, subject, parse_text(plan.seq)
                )
            elif plan.kind == "create":
                status, _ = await client.create_intent(
                    http_client,
                    subject,
                    perf_intent_payload(plan.seq, started_at=started_at),
                )
            elif plan.kind == "draft_activate":
                await client.draft_activate(
                    http_client,
                    subject,
                    perf_intent_payload(plan.seq, started_at=started_at),
                )
            else:
                raise ValueError(f"unknown kind: {plan.kind}")

        stats = await run_open_model(plans, sender=sender, clock=clock)
    print(
        f"injection done: planned={stats.planned} ok={stats.ok} "
        f"error={stats.error} client_p95_ms={stats.client_p95_ms:.1f}"
    )
    settle = await wait_settled(dsn=args.dsn, clock=clock)
    print(f"queue settled in {settle:.0f}s")
    report = await collect.collect_perf_report(
        window=args.window,
        stats_summary={
            "planned": stats.planned,
            "ok": stats.ok,
            "error": stats.error,
            "errors": stats.errors[:10],
            "client_p95_ms": round(stats.client_p95_ms, 1),
        },
        env_note_data=collect.env_note(),
        clock=clock,
    )
    path = collect.write_evidence(report, prefix="ws6-load", clock=clock)
    print(f"evidence: {path}")
    return 0


async def cmd_massload(args) -> int:
    base = datetime.fromisoformat(args.base_datetime)
    if base.tzinfo is None:
        raise SystemExit("--base-datetime はタイムゾーン付きISO8601で指定してください")
    clock = SystemClock()
    seed = build_seed(base, count=args.stage)
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    out = SEED_DIR / f"loadgen-seed-{args.stage}.yaml"
    write_yaml(seed, str(out))  # 手順の再現性のためgit管理外へ書き出し(§2.2(c))
    result = await massload.load_population(dsn=args.dsn, seed=seed, now=clock.now())
    print(f"massload: {result} (seed yaml: {out})")
    return 0


async def cmd_bruteforce(args) -> int:
    clock = SystemClock()
    client = LoadClient(base_url=args.base_url, keys_dir=args.keys)
    started_at = clock.now()
    plans = burst_plan(count=args.injected, duration_s=1.0, kind="create")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as http_client:

        async def sender(plan: SendPlan) -> None:
            subject = LOAD_USER_SUBJECT.format(
                nnnn=args.first_user + (plan.seq % args.users)
            )
            payload = perf_intent_payload(
                args.seq_offset + plan.seq, started_at=started_at
            )
            status, _ = await client.create_intent(http_client, subject, payload)
            if status != 201:
                raise RuntimeError(f"bf create {subject}: {status}")

        stats = await run_open_model(plans, sender=sender, clock=clock)
    print(f"bf injection done: ok={stats.ok} error={stats.error}")
    settle = await wait_settled(dsn=args.dsn, clock=clock)
    print(f"queue settled in {settle:.0f}s")
    report = await collect.collect_bruteforce_report(
        stage=args.stage,
        window=args.window,
        injected_events=args.injected,
        env_note_data=collect.env_note(),
        clock=clock,
    )
    path = collect.write_evidence(
        report, prefix=f"ws6-bruteforce-{args.stage}", clock=clock
    )
    print(f"evidence: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="負荷注入器(M4 ws-6)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--keys", default=DEFAULT_KEYS_DIR)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    sub = parser.add_subparsers(dest="command", required=True)

    p_users = sub.add_parser("users", help="負荷用ユーザー事前作成(users+consent)")
    p_users.add_argument("--count", type=int, default=1000)
    p_users.set_defaults(func=lambda a: asyncio.run(cmd_users(a)))

    p_perf = sub.add_parser("perf", help="性能試験区間(3相+収束待ち+証拠YAML)")
    p_perf.add_argument("--rate", type=float, default=50.0)
    p_perf.add_argument("--parse-count", type=int, default=3000)
    p_perf.add_argument("--save-active", type=int, default=2980)
    p_perf.add_argument("--save-draft", type=int, default=10)
    p_perf.add_argument("--burst", type=int, default=100)
    p_perf.add_argument("--window", default="15m")
    p_perf.set_defaults(func=lambda a: asyncio.run(cmd_perf(a)))

    p_ml = sub.add_parser("massload", help="総当たり母集合投入(Event発行なし)")
    p_ml.add_argument("--stage", type=int, required=True)
    p_ml.add_argument(
        "--base-datetime",
        required=True,
        help="タイムゾーン付きISO8601(全段階で同一値を使う=段階比較の公平性)",
    )
    p_ml.set_defaults(func=lambda a: asyncio.run(cmd_massload(a)))

    p_bf = sub.add_parser("bruteforce", help="総当たり注入100件+収束待ち+証拠YAML")
    p_bf.add_argument("--stage", type=int, default=10000)
    p_bf.add_argument("--injected", type=int, default=100)
    p_bf.add_argument(
        "--first-user",
        type=int,
        default=1,
        help="注入ユーザーの開始番号(段階間で101→201と変える・Active 5件余裕)",
    )
    p_bf.add_argument("--users", type=int, default=100, help="注入ユーザー数(1人1件)")
    p_bf.add_argument(
        "--seq-offset", type=int, default=11000, help="payload多様化のseq基点"
    )
    p_bf.add_argument("--window", default="30m")
    p_bf.set_defaults(func=lambda a: asyncio.run(cmd_bruteforce(a)))

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 試験が通ること・CLIが引数を解釈することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_collect.py -v`
Expected: PASS(6件)

Run: `cd backend && uv run python -m latch.loadgen --help && uv run python -m latch.loadgen perf --help`
Expected: 4サブコマンドの説明・perf引数一覧が表示される(実行はしない)

- [ ] **Step 6: コミットする**

```bash
git add backend/src/latch/loadgen/collect.py backend/src/latch/loadgen/__main__.py backend/tests/unit/test_loadgen_collect.py
git commit -m "feat: loadgen/collect・CLI — 証拠採取(Prometheus照合+機械判定)と4サブコマンド(M4 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9: load環境のPrometheus接続・stub既定値・Makefileターゲット

**Files:**
- Modify: `compose.load.yaml`(分布env 4本・4サービスへload-別名aliases)
- Modify: `compose.dashboard.yaml`(networksへload-obs追加・コメント消化)
- Modify: `docker/prometheus/prometheus.yml`(load job 2本追加)
- Modify: `Makefile`(ターゲット7本追加・.PHONY更新)
- Test: `backend/tests/unit/test_loadgen_compose_pins.py`(新規)

**Interfaces:**
- Consumes: なし(静的ピン)
- Produces: 試験手順(§8)の前提 — load composeのstub遅延が10 §4.1値で立ち上がる・prometheusがload targetsをscrapeする・make loadperf/loadbf/loadmass/loadusers/load-stub-flat/load-migrate/load-geo-importが使える

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_loadgen_compose_pins.py`:

```python
"""load環境接続の静的ピン(M4 ws-6)。

compose.load.yamlのstub遅延既定(10 §4.1値・${VAR:-既定}形式)・load側
別名aliases(prometheusの両ネットワーク参加時のDocker DNSあいまい解決の
回避・読み替え6)・dashboardのloadネットワーク参加・prometheus.ymlの
load job・Makefileターゲット。
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _load_compose() -> dict:
    return yaml.safe_load((ROOT / "compose.load.yaml").read_text(encoding="utf-8"))


def test_load_compose_defaults_to_10_4_1_latency_targets():
    env = _load_compose()["x-backend-env"]
    assert env["LATCH_LLM_STUB_LATENCY_PARSER_P50_MS"] == (
        "${LATCH_LLM_STUB_LATENCY_PARSER_P50_MS:-1000}"
    )
    assert env["LATCH_LLM_STUB_LATENCY_PARSER_P95_MS"] == (
        "${LATCH_LLM_STUB_LATENCY_PARSER_P95_MS:-2500}"
    )
    assert env["LATCH_LLM_STUB_LATENCY_JEV_P50_MS"] == (
        "${LATCH_LLM_STUB_LATENCY_JEV_P50_MS:-2000}"
    )
    assert env["LATCH_LLM_STUB_LATENCY_JEV_P95_MS"] == (
        "${LATCH_LLM_STUB_LATENCY_JEV_P95_MS:-5000}"
    )


def test_load_compose_has_no_fixed_ms_env():
    """既存固定ms env(llm_stub_delay_*)はload composeへ設定しない(0維持)。"""
    for key in _load_compose()["x-backend-env"]:
        assert not key.startswith("LATCH_LLM_STUB_DELAY_"), key


def test_load_services_have_aliased_names():
    """api1/api2/worker1/worker2へload-別名(prometheus target用)。"""
    compose = _load_compose()
    for svc, alias in (
        ("api1", "load-api1"),
        ("api2", "load-api2"),
        ("worker1", "load-worker1"),
        ("worker2", "load-worker2"),
    ):
        networks = compose["services"][svc]["networks"]
        assert networks["default"]["aliases"] == [alias], svc


def test_ports_stay_loopback_bound():
    """load composeのportsは既存値のまま(127.0.0.1明示バインド・承認条件)。"""
    compose = _load_compose()
    assert compose["services"]["api1"]["ports"] == ["127.0.0.1:28001:8000"]
    assert compose["services"]["worker1"]["ports"] == ["127.0.0.1:28101:9100"]


def test_dashboard_joins_load_network():
    compose = yaml.safe_load(
        (ROOT / "compose.dashboard.yaml").read_text(encoding="utf-8")
    )
    assert compose["networks"]["load-obs"] == {
        "external": True,
        "name": "latch-load_default",
    }
    assert "load-obs" in compose["services"]["prometheus"]["networks"]
    # 既存staging参加の無傷
    assert "staging-obs" in compose["services"]["prometheus"]["networks"]


def test_prometheus_targets_aliased_load_containers():
    text = (ROOT / "docker" / "prometheus" / "prometheus.yml").read_text(
        encoding="utf-8"
    )
    assert "job_name: latch-load-api" in text
    assert "job_name: latch-load-worker" in text
    assert "load-api1:8000" in text
    assert "load-api2:8000" in text
    assert "load-worker1:9100" in text
    assert "load-worker2:9100" in text
    # 既存staging jobの無傷
    assert "job_name: latch-staging-api" in text
    assert "job_name: latch-staging-worker" in text


def test_makefile_has_load_targets():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in (
        "loadusers:",
        "loadperf:",
        "loadbf:",
        "loadmass:",
        "load-stub-flat:",
        "load-migrate:",
        "load-geo-import:",
    ):
        assert target in mk, target
    phony = mk.split(".PHONY:")[1].split("\n")[0]
    for name in (
        "loadusers",
        "loadperf",
        "loadbf",
        "loadmass",
        "load-stub-flat",
        "load-migrate",
        "load-geo-import",
    ):
        assert name in phony, name
```

- [ ] **Step 2: 試験が失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_compose_pins.py -v`
Expected: FAIL(7件がFAIL — env未追加・aliases未追加等)

- [ ] **Step 3: compose.load.yamlへ追記する**

`x-backend-env` の `LATCH_AUTH_TESTKEYS_DIR: /keys` の後へ4行を追加:

```yaml
  # stub遅延分布(M4 ws-6・10 §4.1値を既定・`${VAR:-既定}`でホスト環境変数
  # 上書き可)。perf区間はこの既定のままで・総当たり区間はmake load-stub-flat
  # で0へ切替する(§2.1読み替え5)。既存固定ms env(llm_stub_delay_*)は設定しない
  LATCH_LLM_STUB_LATENCY_PARSER_P50_MS: ${LATCH_LLM_STUB_LATENCY_PARSER_P50_MS:-1000}
  LATCH_LLM_STUB_LATENCY_PARSER_P95_MS: ${LATCH_LLM_STUB_LATENCY_PARSER_P95_MS:-2500}
  LATCH_LLM_STUB_LATENCY_JEV_P50_MS: ${LATCH_LLM_STUB_LATENCY_JEV_P50_MS:-2000}
  LATCH_LLM_STUB_LATENCY_JEV_P95_MS: ${LATCH_LLM_STUB_LATENCY_JEV_P95_MS:-5000}
```

api1・api2・worker1・worker2の各サービス定義へ `networks` を追加(portsの後。既存portsは1行も変更しない):

```yaml
  api1:
    <<: *api-common
    ports:
      - "127.0.0.1:28001:8000"
    networks:
      default:
        aliases:
          - load-api1
```

(api2は `load-api2`・worker1は `load-worker1`・worker2は `load-worker2`)

- [ ] **Step 4: compose.dashboard.yamlへ追記する**

コメントの追記(`load環境(28000系)のtargetはws-6…` の行を消化):

```yaml
# load環境(28000系)のtargetもprometheus.ymlへ追加した(ws-6)。scrape経路は
# loadのcomposeネットワーク(latch-load_default)への参加+load側コンテナの
# 別名(load-api1等・同一ネットワーク名のDocker DNSあいまい解決を避ける)。
```

prometheusサービスのnetworksへ1行追加:

```yaml
    networks:
      - default
      - staging-obs
      - load-obs
```

トップレベル `networks` へ1ブロック追加:

```yaml
networks:
  # stagingのcomposeが作る既定ネットワーク(コンテナ名直結scrape用・external)
  staging-obs:
    external: true
    name: latch-staging_default
  # loadのcomposeが作る既定ネットワーク(ws-6・external。load-up後に存在する
  # ため起動順序は load-up → dashboard-up。dashboard稼働中にload-upした場合は
  # prometheus再起動〔dashboard-down/up〕を手順§8で明示する)
  load-obs:
    external: true
    name: latch-load_default
```

- [ ] **Step 5: prometheus.ymlへload jobを追加する**

scrape_configsの末尾(`- job_name: prometheus` の前後どちらでも・staging系の後に)へ:

```yaml
  - job_name: latch-load-api
    static_configs:
      - targets:
          - "load-api1:8000"
          - "load-api2:8000"
  - job_name: latch-load-worker
    static_configs:
      - targets:
          - "load-worker1:9100"
          - "load-worker2:9100"
```

- [ ] **Step 6: Makefileへターゲットを追加する**

`.PHONY:` 行のターゲット一覧の末尾へ ` loadusers loadperf loadbf loadmass load-stub-flat load-migrate load-geo-import` を追加。`LOAD_COMPOSE = ...` の定義の後に変数2つを追加:

```makefile
LOAD_DB_URL = postgresql+asyncpg://latch:latch@127.0.0.1:25432/latch
LOADGEN_DSN = postgresql://latch:latch@127.0.0.1:25432/latch
```

`load-ps` の後へ7ターゲットを追加:

```makefile
loadusers: ## load環境へ負荷用ユーザーをAPI経由作成(users+consent。make loadusers ARGS="--count 1000")
	cd backend && uv run python -m latch.loadgen users $(ARGS)

loadperf: ## 性能試験区間(相A parse→相B save→相C burst→収束待ち→証拠YAML。§8手順のci一時停止を明示実行してから)
	cd backend && uv run python -m latch.loadgen perf $(ARGS)

loadmass: ## 総当たり母集合投入(Event発行なし。make loadmass ARGS="--stage 10000 --base-datetime <tz付きISO>")
	cd backend && uv run python -m latch.loadgen massload $(ARGS)

loadbf: ## 総当たり注入100件+収束待ち+証拠YAML(make loadbf ARGS="--stage 10000 --first-user 1")
	cd backend && uv run python -m latch.loadgen bruteforce $(ARGS)

load-stub-flat: ## load環境のstub遅延分布を0へ切替(総当たり区間の前提・api/workerをenv差分で再作成)
	LATCH_LLM_STUB_LATENCY_PARSER_P50_MS=0 LATCH_LLM_STUB_LATENCY_PARSER_P95_MS=0 LATCH_LLM_STUB_LATENCY_JEV_P50_MS=0 LATCH_LLM_STUB_LATENCY_JEV_P95_MS=0 $(LOAD_COMPOSE) up -d --force-recreate api1 api2 worker1 worker2

load-migrate: ## load DBをheadまでマイグレーション(25432宛・staging-migrateの対称形)
	cd backend && LATCH_DATABASE_URL=$(LOAD_DB_URL) uv run alembic upgrade head

load-geo-import: ## load DBへ地物データを取り込み(25432宛。CSV/PBFはbackend/data/geo/・git管理外)
	cd backend && LATCH_DATABASE_URL=$(LOAD_DB_URL) uv run --group geo python -m latch.geo import-isj --csv data/geo/isj.csv
	cd backend && LATCH_DATABASE_URL=$(LOAD_DB_URL) uv run --group geo python -m latch.geo import-osm --pbf data/geo/kyushu-latest.osm.pbf
```

(注: loadgen CLIの既定値はload宛のため・makeターゲットはARGS転送のみ。`.PHONY` の更新と・既存ターゲット・既存変数は1行も変更しない)

- [ ] **Step 7: 試験が通ること・既存composeピンが無傷であることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_compose_pins.py tests/unit/test_obs_compose_pins.py tests/unit/test_compose_ports.py -v`
Expected: PASS(load系ピン7件・既存obs/composeピンは無傷)

- [ ] **Step 8: コミットする**

```bash
git add compose.load.yaml compose.dashboard.yaml docker/prometheus/prometheus.yml Makefile backend/tests/unit/test_loadgen_compose_pins.py
git commit -m "feat: load環境のPrometheus接続(別名targets)・stub遅延の10 §4.1既定・Makefileターゲット7本(M4 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 10: 報告書(スーパーバイザー検証時の実施後に作成)

**Files:**
- Create: `docs/plans/M4/ws-6-report.md`

このTaskは**手順§8の実施(スーパーバイザー検証時)後に実行する**。agent3は実施コード(Task 1〜9)の完了時に「Task 1〜9完了・実施待ち」を報告し・実施証拠(loadgenの証拠YAML・段階比較表)が出揃った段階で報告書を書く。

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
git add docs/plans/M4/ws-6-report.md
git commit -m "docs: M4 ws-6完了報告(実施日・5目標測定値と段階比較表・読み替え記録・test-ci検証待ち)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## 6. 完了条件(テストで証明できる検証可能な形)

design §4(テスト方針)を計画の完了条件へ引き取る。各条件の証明方法を明記する。

1. **stub遅延の分布化が決定的で・p50/p95注入値が分位点として現れる(既存固定ms env・既存試験・ci挙動の互換維持)**
   証明: unit test_llm_stub_latency(決定性・固定key集合でのnearest-rank分位点=§2.4の機械検証値・優先順位「分布>固定ms」・embedding無分布)+既存test_stub.py・test_gateway_*.py・test_llm_stub_response_settings.pyが無傷(test_llm_factory.pyの集合ピン15項目化を含む)。
2. **層別・初期判定E2E・通知のヒストグラムが露出し・区間の分離観測ができる**
   証明: unit test_obs_metrics(許可リスト+9名・le集合に注入値と目標境界)+test_obs_pipeline_metrics(embedding区間=handle全体・retrieval区間=両経路)+test_latch_engine(L5+通知区間=提案確定経路のみ・通知区間=proposed遷移時のみ)。Jev区間は既存jev_latency_seconds(ws-4実装済み)。
3. **初期判定E2Eの観測が「初回投入チェーン(created種)かつ初回提案かつ通知記録まで」に絞られ・起点と経路が正しい**
   証明: unit test_latch_engine(test_handle_passes_e2e_ctx_on_first_injection・test_handle_drops_ctx_when_not_first_proposal・test_handle_passes_none_ctx_without_created_event・test_promote_observes_e2e_with_route・test_promote_expired_writes_no_load_observation・起点読取SQLピン)。route判定の同一コミット等値比較はintents/service.py実装との実突合を§2.1読み替え1に記録。
4. **注入器が決定したレートで・応答を待たずに送出し・収束待ちと証拠採取まで完結する**
   証明: unit test_loadgen_schedule(決定的時刻列・オープンモデル性=応答遅延下で送出間隔維持・エラー集計)+test_loadgen_collect(load側jobのみのクエリ・window差し替え・Vector≤50の機械判定)+CLIの--help確認。実際の負荷条件下での5目標測定値は手順§8実施の証拠YAML(スーパーバイザー検証時・報告書へ記録)が所有する。
5. **総当たり母集合がLayer 1の対象条件を満たす行として決定的・べき等に投入でき・Event発行しない**
   証明: unit test_loadgen_massload(決定性・平ら化・embedding=_stub_vector・expires_at未来・match_events非触りの静的ピン)+integration test_loadgen_massload_ci(実INSERT・active条件・冪等性・スーパーバイザー検証時のtest-ci)。
6. **load環境がPrometheusへ接続し・stub遅延が10 §4.1値で立ち上がる**
   証明: unit test_loadgen_compose_pins(分布env既定・load-別名targets・dashboardネットワーク・Makefile 7ターゲット)+既存test_obs_compose_pins.py・test_compose_ports.pyが無傷。実スクrapeのup=1確認は手順§8の観察事項。
7. **unit+integration試験グリーン・実施証拠がdocs/testassets/results/へ保存**
   証明: `make test` グリーン(基準1386・新規unit約45件。integrationはcollect-onlyで収集確認)・`make test-ci` は**スーパーバイザー検証待ち**と報告書へ記録(§0規律)・ws6-load-*.yaml・ws6-bruteforce-*.yamlのitems(実施時)。

## 7. 報告形式

報告書: `docs/plans/M4/ws-6-report.md`。以下の節をこの順で書く。

1. **実施サマリ**: 実施日・環境(worktree/ブランチm4-ws-6・基点main head)・`make test` の実数(基準1386+新規unit)・コミット一覧(ハッシュ+1行要約)。
2. **完了条件別の証拠**(§6の1〜7の各項に): 実施した試験と実行結果(pytest出力の要約)・実施時の証拠YAMLファイル名と機械判定(vector_le_50_ok等)。
3. **性能5目標の測定値と合否の記録**: 各目標の測定値(p95/p99・route別)・層別4区間の内訳・queue pendingの推移・クライアント側応答時間の参考値。**合否の判定はG4の人間側**であることを明記(design §2.7)。
4. **総当たりの段階比較表**: 1万/5万/10万×(Vector件数分布・Retrieval p95・L1通過率・イベントあたりJev回数)・増幅率の参考値(段階5倍に対する指標増幅)・Vector件数≤50の機械判定結果。
5. **designからの読み替え・逸脱の記録**: §2.1の読み替え表の各項(1〜9)について「実装どおりか・変更した場合は変更前→変更後+理由」を実施結果として記録する。特に読み替え1(起点列=match_events.created_at)・2(チェーン判定のDB照合方式)・3(perf 3相)・4(注入ユーザー)・5(stub既定値)・6(別名targets)は必須記録。
6. **実施パラメータの実測記録**: 手順§8の実測値(区間秒数・件数・母集合投入所要時間・収束所要時間)と・手順初期値との差分。
7. **スーパーバイザー検証待ちの記録**: `make test-ci`(既存1664+新規integration test_loadgen_massload_ci)が**スーパーバイザー検証待ち**であることを明記する(STATUS.md運用ルール1)。
8. **G4時確認事項の付記依頼**(supervisor側で記録): 「層別・E2E・通知の計測をCloud Monitoring+OTLPでなくPrometheusヒストグラムで実装した代替差分(G4時確認事項2の延長)」と「目標②をparse独立相で採取した負荷構成の読み替え」の2項目がG4時確認事項へ載るべき旨を記録する。
9. **詰まった箇所と判断**: 作業中に停止・確認した点があれば「状況・採った判断・根拠」を書く(なければ「なし」)。

## 8. 実施手順(負荷試験・総当たりの実行。スーパーバイザー検証時)

前提: Task 1〜9完了・worktreeはマージ済みmain。全コマンドはリポジトリルートで実行。STATUS運用ルール6(ws-1 design §2.6確定手順)どおり・**同一ホストのリソース競合が絶対値合格へノイズを乗せるため・測定中はci常設環境を止める**。

```bash
# 1. load環境の起動と準備
#    実行内容: load compose起動(healthy待ち・stub遅延は10 §4.1値で立ち上がる)
make load-up
#    実行内容: load DBをheadまでマイグレーション(25432宛。マイグレーション追加なしで0008まで)
make load-migrate
#    実行内容: 地物データ取り込み(25432宛。POST /v1/intentsのジオコーディング前提)
make load-geo-import
#    確認: make load-ps でworker2台がrunning・
#    docker compose -f compose.load.yaml logs --tail 30 worker1 でエラー無し

# 2. 負荷用ユーザーの事前作成(1000人・API経由=正規経路)
make loadusers ARGS="--count 1000"
#    期待: created=1000 existing=0

# 3. ci一時停止(ボリューム保持・運用ルール6)
#    実行内容: ci常設環境(latch-ciプロジェクト)を停止する
make down

# 4. 観察環境の起動(load-up後が前提=dashboardのexternalネットワークが存在)
make dashboard-up
#    確認: curl -s http://127.0.0.1:19090/api/v1/targets | grep -o 'load-api1' が
#    ヒットし・/api/v1/query?query=up でlatch-load-api・latch-load-workerが1
#    (dashboard稼働中にload-upした場合はprometheus再起動=dashboard-down/up)

# 5. 性能試験区間(相A parse→相B save→相C burst→収束待ち→証拠YAML)
make loadperf
#    期待: injection done: ok=6090 error=0(3000 parse+2980 save+10 draft+100 burst計画)
#    queue settled が数分以内・evidence: docs/testassets/results/ws6-load-*.yaml
#    採取は収束完了後に走るため・5目標の分布はこの窓に載る。pending推移は
#    処理中の値を目視で記録する(観察事項・design §1.3)

# 6. 総当たり段階1(1万) — stub遅延を0へ切替してから
make load-stub-flat
#    実行内容: 4本の分布envを0でapi1/api2/worker1/worker2を再作成(§2.1読み替え5)
make loadmass ARGS="--stage 10000 --base-datetime 2026-10-04T11:00:00+09:00"
#    期待: intents_inserted=10000 users_inserted=100(所要は実測を記録)
make loadbf ARGS="--stage 10000 --first-user 1"
#    期待: ok=100・evidence: ws6-bruteforce-10000-*.yaml(vector_le_50_ok: true)

# 7. 総当たり段階2・3(5万・10万) — 同一--base-datetime・注入ユーザー範囲だけ変える
make loadmass ARGS="--stage 50000 --base-datetime 2026-10-04T11:00:00+09:00"
make loadbf ARGS="--stage 50000 --first-user 101"
make loadmass ARGS="--stage 100000 --base-datetime 2026-10-04T11:00:00+09:00"
make loadbf ARGS="--stage 100000 --first-user 201"
#    各段階の証拠YAMLを残したまま次へ(prometheus再起動をする場合は採取を先に)

# 8. 片付け
make load-down
make dashboard-down
#    実行内容: ci常設環境を復帰する(運用ルール6どおり・必須)
make up
#    確認: make ps でci環境がhealthy・機能試験が毎コミットで回る通常状態へ

# 9. 報告書(Task 10)へ実測値を記録する
```

実測パラメータ(区間秒数・件数・母集合投入時間・収束時間)は手順実行時に記録し・手順の初期値との差分は報告書へ残す。段階間のprometheus再起動(dashboard-down/up)を行う場合は採取を先に済ませる(seriesはprometheusのメモリに載るため)。

## 9. Self-Reviewの結果(計画完成時点で実施済み)

1. **Spec coverage**: design §3.1のファイル表→§4.1/4.2(全行にTask番号付き・dashboard JSONは「無変更」を§4.3へ明記)。design §2.1〜§2.7の採用案→Task 2(注入器・stub分布)・Task 3〜5(計測)・Task 7(母集合)・Task 9(load接続)・§8(手順・注入なし・ci停止)。design §4テスト方針→Task 1/2/3/5/6/7/8+§6。design §5承認8件→§2.4(注入器)・§2.4(分布)・§2.3(メトリクス)・§2.4(母集合)・§2.6(Prometheus)・§2.5(注入なし)・§2.4(E2E絞り込み=読み替え1/2)・Task 9(Makefile範囲)。design §6記録事項→§2.5(量的線・KpiJob非恒久)・§8(並行モデルの観察事項)。**漏れなし**。
2. **Placeholder scan**: integration骨格(test_loadgen_massload_ci.py)は「参照すべきfixture・追加すべき4試験を明示した指示」であり・TBDではない(ws-4のTask 5と同型)。他に未定義参照なし。`import re`・`import uuid`・`_DEFAULT_BUFFERS` の未使用行は執筆段階で除去済み。
3. **Type consistency**: observe_pipeline_layer/observe_initial_e2e/observe_notification_latencyのIFはTask 3定義とTask 4/5消費・test_obs_metrics試験と一致。distributed_delay_ms・StubLLM分布引数はTask 2定義とtest_llm_stub_latency・Task 9のcompose既定値と一致。SendPlan/RunStats/run_open_modelはTask 6定義とTask 8消費と一致。LoadClient IF(user_subject・perf_intent_payload・parse_text)はTask 7定義とTask 8消費と一致。massload列タプル位置はTask 7試験のインデックスと一致(位置6/7=lon/lat・14/15=time・16=expires・17=embedding・18=model)。E2eObsContextはTask 5定義と試験で一致。
4. **Review Focus**: 5項目それぞれに所有タスクの試験を割り当て済み(§3の各項→Task 2/5/6/7/9)。
5. **機械チェック**: §2.2の(a)basename一意(実行結果記録済み)・(b)ピン影響表(test_llm_factory・test_obs_metrics・test_latch_engineの追随と無傷系の明示)・(c)DB干渉対抗策(マイグレーションなし・massloadはload宛・unitはDB不使用・integrationは自前掃除)を記録済み。
6. **supervisor修正(2026-10-04・計画レビュー)**: ①§6-7・§7-1の「make test 基準1664」→**1386**(1664はtest-ciの値・unit基準は1386。ホスト実行 `pytest --co` で1386件を確認) ②Task 7 Step 6へ「4試験の本体コードを本Stepで書き切る・プレースホルダーはコミット対象外・collect-only 4件」を明示(骨格のままコミットされるとスーパーバイザー検証時のtest-ciが確定失敗するため) ③§2.2(c)へ「test-ciのmassload試験はload DB起動が前提・test-ciのpytestはホスト実行」を追記(Makefile実突合)。機械チェックの独立再実行: basename一意=空・§2.4の実計算値({1000:5058,1750:4329,2500:613}ほか)=完全一致・ux_match_events_idempotency(0001:295)=実在。読み替え1・2・4・9のコード主張(embeddingのupdated_at更新・service.pyのversion+1・generator USER_COUNT=100・auth/tools.py:34)も実突合で一致。
