# M4 ws-4 完了報告

## 1. 実施サマリ

- **実施日**: 2026-10-04
- **環境**: git worktree(`.herdr/worktrees/latch/m4-ws-4`)・ブランチ `m4-ws-4`・基点 main(head 8e46d2b)
- **`make test`**: 1386 passed(基準1335+新規51: unit試験31・既存ピン追随2〔test_rate_limit_wiring・test_settings〕を含む)。`make lint` グリーン。integration試験3ファイル(test_obs_schema・test_obs_api_ci・test_obs_kpi_job_ci)はcollect-only確認済み(実行はスーパーバイザー検証のtest-ci)
- **staging受け入れ**: `make staging-obs-test` **10件全pass**(75秒)。証拠YAML `docs/testassets/results/ws4-staging-result-20261004-124823.yaml`(10 §5形式・items 8項目すべてverdict: pass)
- **途中経過**: Task 5で計画書ピン影響表に漏れた `test_rate_limit_wiring.py` との仕様矛盾を検出し一度停止(途中報告 0af3a41)→ スーパーバイザー裁定(案B: api_rate_limited付与)を得て再開・完遂

### コミット一覧(15件・main..m4-ws-4)

| ハッシュ | 要約 |
|---|---|
| 91c6547 | feat: obs/metrics.py — prometheus-client導入とメトリクス単一真実(M4 ws-4) |
| 8a6f30a | feat: マイグレーション0008(sessions・kpi_daily・kpi_muted_user_snapshot)とheadピン追従(M4 ws-4) |
| 5aa89a9 | feat: settingsへobs系3項目追加(既定0/60/4・ci無効維持)と既定値・集合ピン(M4 ws-4) |
| cce7c53 | feat: HTTP基本メトリクスmiddleware(method・pathテンプレート・statusのみ・/metrics自己計上なし) |
| 0af3a41 | docs: M4 ws-4途中報告(仕様矛盾検出・スーパーバイザー裁定待ち) |
| 577f68a | feat: POST /v1/sessions(05 §19契約)とapi_rate_limited付与(スーパーバイザー裁定2026-10-04) |
| 669c3bc | feat: KPI純関数層とmetric定数表(09 §2.2の15キー・期待値表試験) |
| eaa759f | feat: KpiJob(JST日付境界の60秒tick検査・遡及UPSERT)とWorker配線・start_http_server分岐 |
| 8798bad | feat: collector(60秒周期exporter)とWorker配線 |
| 0af12ae | feat: パイプライン/Jev/スコアのメトリクス観測挿入(ロジック変更なし) |
| ee624be | feat: /metricsエンドポイント(prometheus text exposition・無認証・無レート制限)とHTTPMetricsMiddleware挿入 |
| 9073555 | feat: latch-dashboard環境(provisioning as-code)・staging/loadのworker metricsポート・Makefileターゲット |
| 6809d85 | feat: Grafanaダッシュボード4枚(overview/pipeline/jev/behavior) |
| 1893cb5 | feat: staging受け入れハーネス(staging_tests_obs・10件)とscrape経路のネットワーク参加化(実施時修正) |
| 906a298 | test: staging受け入れ再実施証拠(ws4-a〜c 10件全pass) |

## 2. 完了条件別の証拠(§6の1〜7)

1. **3群+アクセス系のメトリクス露出とscrape**: 証拠YAML ws4-a2(pending・hardfilter_pass・vector_items・candidates・http_requests・jev・scoreの7種series到達)・ws4-c4(prometheus 4 target up=1: api1/api2/worker1/worker2)・ws4-c1/c2は `/metrics` 直200(unit test_obs_metrics 15件・integration test_obs_api_ci 4件はtest-ciで実行)
2. **latch-dashboard稼働**: ws4-c3でgrafana APIから4枚のtitle取得(LATCH Overview・Pipeline・Jev・Behavior/KPI)。overview.jsonにmlr・DAU・pending・candidates・HTTP p95等10パネル・全4枚は計画書PromQL一覧表どおり
3. **POST /v1/sessions契約**: unit test_obs_sessions_api 4件(204・body非空422・未登録404・認証なし401)+test_obs_migration(SQL_STATEMENTS列ピン=3列のみ)+integration test_obs_api_ci(test-ci実行待ち)
4. **日次集計ジョブのJST 0時発火**: ws4-b2(Clock前進2026-10-05T00:05+09:00→KpiJob発火・kpi_daily mlr 4行=遡及4日分)・ws4-b3(kpi_daily値とDB実測一致: 0/0)・unit test_obs_kpi 14件(純関数+KpiJob)・integration test_obs_kpi_job_ci(test-ci実行待ち)
5. **09 §2.2の14指標+retention 2種**: unit test_obs_kpi::test_metric_keys_cover_09_2_2_table(15キー集合ピン)+kpi.pyのSQL定数(§2.3表どおり)。cost_per_matchedはjev.jsonのダッシュボード式(${TOKEN_PRICE}変数)
6. **試験グリーン・証拠YAML保存**: `make test` 1386 passed・`make test-ci` は**スーパーバイザー検証待ち**(下記4)・ws4-staging-result-20261004-124823.yamlのitems全pass
7. **/metricsへのホスト外到達不可**: ws4-c5(非ループバックIP 192.168.10.111宛6ポート接続拒否)+test_obs_compose_pins(全ports 127.0.0.1明示バインドピン)

## 3. designからの読み替え・逸脱の記録(§2.1表1〜10)

| # | 実装どおりか | 内容 |
|---|---|---|
| 1 | **実装どおり** | x-worker-commonのenvironmentへ `LATCH_OBS_WORKER_METRICS_PORT: "9100"` をbackend-envマージ後に追記(load側も同構造・env追加はworker-commonのみ) |
| 2 | **実装どおり** | KpiJob=ReevalRunner同型の60秒tick方式(起動時即tick・pending検出で自己修復)。ws4-b2でClock操作による発火確認済み |
| 3 | **実装どおり** | kpi_daily=(metric_key, day)行持ち・分子/分母のみ保持。週次=週月曜・retention=基準日Dの行 |
| 4 | **実装どおり** | Candidate数gauge=match_candidates全行数・candidate_generation_rate分子=status条件なしのactive Intent一致 |
| 5 | **環境差吸収あり**(下記「実装時判断1・2」) | build_metrics(registry)ファクトリ+モジュール単例。0.26ではregistry=None明示渡しが未登録を意味するため単例はREGISTRY明示渡し |
| 6 | **実装どおり** | 純関数層(unit期待値表14件)+SQL実行層(integration+staging)の3層構成 |
| 7 | **実装どおり** | 無回答率の判定はcreated_at+3日<集計時刻(SQL_UPSERT_WEEKLY内) |
| 8 | **実装どおり** | collectorのkpi export=DISTINCT ON (metric_key)…ORDER BY metric_key, day DESCの最新行 |
| 9 | **実施時修正あり**(下記「実施時判断5」) | scrape経路をhost-gatewayからstagingネットワーク参加(コンテナ名直結)へ変更。127.0.0.1バインド承認条件は維持 |
| 10 | **実装どおり** | ダッシュボード4枚(overview 10・pipeline 6・jev 6・behavior 13パネル)。jev.jsonは${TOKEN_PRICE}変数をtemplating(textbox・既定0.000004)で追加 |

### 実装時判断(prometheus-client 0.26対応等・スーパーバイザー承認済み4件+追加)

1. **prometheus-client 0.26のsample遅延生成対応**(Task 1): 0.26はcollect()のfamily名からCounterの `_total` が除かれ、ラベル付きseriesはlabels()呼び出しまでsampleを持たない。テストヘルパー `_sample_names` を「family名+typeからの決定的展開(Counter=+_total・Histogram=+_bucket/_count/_sum・Gauge=素の名前・`_created` は技術seriesとして除外)」に変更。EXPECTED_SAMPLE_NAMES・許可リストピンの意図は不変。
2. **build_metricsの既定REGISTRY登録**(Task 1): 0.26では `registry=None` の明示渡しが「未登録」。`build_metrics` 内で `registry is None` のときREGISTRYを明示渡し(モジュール単例の契約=import時にapi・worker共用でREGISTRY登録を維持)。
3. **テスト2の独立性assert**(Task 1): 計画書の `is None` assertはテスト3(単例がREGISTRYに存在)と排他のため「set(7)前後で単例値が変わらない」差分assertへ修正。Task 8でさらにpytest実行順序非依存化(collector試験が単例をsetするためbefore/after比較)。
4. **StubSessionStoreのNone指定バグ**(Task 5・unit): 計画書の `user_id or uuid.uuid4()` は「未登録の明示None」を潰すため未指定(_UNSET)との差で分ける形へ修正。
5. **scrape経路のネットワーク参加化**(Task 11・13・実施時修正): host-gatewayはホストの非ループバックIPを指すため、127.0.0.1バインドポートには届かない(実測: 全target context deadline exceeded)。**変更前**: prometheusにextra_hosts host-gateway+targets `host.docker.internal:18001等`。**変更後**: prometheusをlatch-staging_default(external network)へ参加+targets `api1:8000`・`worker1:9100` 等(コンテナ名直結)。127.0.0.1明示バインドの承認条件はdashboard/staging両composeのportsで維持(test_obs_compose_pins・ws4-c5で検証)。api/workerのネットワーク変更なし。
6. **KpiJob・collectorのrun待機をRetentionJob._waitと同一契約に**(Task 7・8): 計画書のrunは「run_once後の単純await sleep」でstop即応性がなく、既存test_worker.pyの1秒タイムアウト(graceful shutdown契約)を壊す。RetentionJob._waitと同一のrace待機(注入sleep+stop即応)へ修正。「起動時即tick」(§2.1読み替え2の要件)は維持。計画書テストは「sleepsが埋まるまで待ってからstop.set()」の確定化も併せて実施。
7. **score観測点を「d07通過後・proposal生成直前」に**(Task 9): 計画書の「分岐直後」「UPDATE成功後」はd07 denied・threshold未満も計上してしまう。注記の「実際に提案化しなかった場合の値はobserveしない」に従い、latch_engine/group_engineともproposal構築直前(競合負け前・d07通過後)へ置いた。group側は`float(r.score)`(手順gループの値)。
8. **observe_retrievalにNoneガード**(Task 9): 既存試験の戻り値なしスタブと非破壊に共存させるため `if outcome is not None` を付けた(観測は本体処理を壊さない規律)。
9. **テスト系の個別修正**: retention_dueの期待値表の日付補正(計画書はコメント「D+7=昨日」と日付が1日ズレ)・KpiJob試験の `_clock` 属性参照修正・direct_pipeline試験のengineスタブ(begin()付き)修正。
10. **stagingハーネスの作法適応**(Task 13・計画注記どおり): `make_scenario_pair(api, db, key=, when=, name=)`・`POST /v1/latches/{id}/response` のbody `{"response": "yes"}`・`GET /v1/latches`(items)へ実名合わせ。jsonb codecはws-3の `json.dumps/json.loads` を踏襲。wait_untilにはlambdaでなくcoroutine関数を渡す。3人目のsessions投与ユーザーはシード先頭ユーザー(w4b-3未登録404回避)。
11. **conftest同名問題の解決方法変更**(Task 13): 計画書の「自分をsys.modulesから外す」トリックはpytest 9のimport_path検証(ImportPathMismatchError)と衝突。`--import-mode=importlib` での実行(make staging-obs-test)+収集ディレクトリのsys.path明示追加+sys.modules差し替えの組み合わせで解決(hook発火の分離は計画どおり・証拠YAMLはws4-接頭辞)。
12. **/metricsエンドポイントの実装方式**(Task 10): `app.mount("/metrics", make_asgi_app())` はStarletteのMount仕様でスラッシュ無しリクエストを307リダイレクトする(Prometheus scrape先が307になる)。**変更前**: make_asgi_app()のMount。**変更後**: `@app.get("/metrics")` の通常ルートで `Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)`(exposition形式の生成はprometheus_clientに一任・gzip非対応のみ差)。無認証・無レート制限は承認事項どおり。wiringピン試験のUNPROTECTEDへ `/metrics` 追加(healthと同一の「v1契約の外」カテゴリ)。
13. **Task 7でtest_obs_worker_wiring.pyをコミットから除外**(Task 8コミットに含めた): 毎コミットのmake testグリーン規律を優先(collector未配線ではwiring試験3件がRED)。Task 8コミット8798badでグリーン状態で追加。

## 4. スーパーバイザー検証待ちの記録

- **`make test-ci` はスーパーバイザー検証待ち**(STATUS.md運用ルール1)。対象: 基準実数(1386+integration)+新規integration 3ファイル(test_obs_schema 4件・test_obs_api_ci 4件・test_obs_kpi_job_ci 1件)+既存headピン追従(test_geo.py `test_head_is_0008`)の確認。
- **`make migrate`(ci宛の0008適用)も検証時実施**。agent3は§0規律どおりci-dbへtest-ci/migrateを実行していない。
- stagingはイメージ再ビルドが必要な点に注意: `make staging-reset` の `up -d` はイメージを再ビルドしないため、コード変更後は `docker compose -f compose.staging.yaml build` が先(本実施ではこれが原因で1回失敗・下記8参照)。

## 5. docs改版候補の再記録(design §7論点3)

次回docs改版へ含めるべき: ①05 §2への3テーブル追記(sessions・kpi_daily・kpi_muted_user_snapshot・FKなし独立表)・②API一覧のPOST /v1/sessions実装参照(05 §19)・③/metricsが通常ルートとして実装された旨(設計の「ASGIマウント」からの実装差・本報告書3-12)

## 6. 近似の告白の再記録(ws-6/ws-7以降で再評価すべき)

- Jev実行数のSQL近似(match_candidates.jev_result書込み行数・design §7論点5: 同一評価世代スキップ等で厳密値と差が生じうる)
- 通知オフ率のsnapshot近似(日次境界でのみ観測・往復変動は不可視・design §7論点4)
- active Intent数の実行時点スナップショット近似(§2.3表)
- Hard Filter通過率の「件/実行」定義(pass累積/検索実行数)
- muted snapshotの0時直後採取近似(前日末集合の近似)
- JevOutputInvalidError経路のusage=None固定(JevOutputInvalidErrorにusage属性が無いため。成功経路のusage計上は主経路)

## 7. G4時確認事項の付記依頼(supervisor側でSTATUSへ記録願う)

- HTTP基本メトリクスがdocs 3群への明示的拡張であること(design §2.9・supervisor承認2026-10-04)
- 04 §3の本番確定値(Cloud Monitoring+OTLP)とstaging/load実装のOSS代替差分(prometheus-client直+pull型・Grafana OSS 11.4・Prometheus v3.0)がG4時確認事項であること

## 8. 詰まった箇所と判断

1. **test_rate_limit_wiringピンとの仕様矛盾**(Task 5): 実装を停止し報告(0af3a41)→ スーパーバイザー裁定(案B: api_rate_limited付与)を得て再開。Counter辞書への機械的追随+docstring更新を実施。401試験は無修正で通過(依存内包の構造どおり)。
2. **staging鍵ディレクトリがroot所有で生成不可**: dockerが `.staging/keys` をroot所有で先に作成したため、`make staging-keys` がPermissionError。sudo無し環境のため `docker run --user 0:0 -v ...:/out <イメージ> python -m latch.auth gen-keypair` でコンテナ内root経由で生成した(root所有644=api/pytest読取可)。根本対処(ユーザー所有ディレクトリの事前作成)は環境固有のため報告のみ。
3. **stagingの旧イメージ問題**: 1回目の受け入れで `/metrics` 404・メトリクス不在。`up -d` はイメージ再ビルドをしないため(test_users_api.pyのdocstringと同様の既知挙動)。`docker compose -f compose.staging.yaml build` を挟んで解消。Makefileのstaging-resetはbuildを含まない点は既存挙動のまま維持(変更は本単位のスコープ外)。
4. **host-gateway経由のscrape不可**(§3-5の変更5): 実測で全target down。ネットワーク参加化で解消・ws4-c4で4 target up=1を証明。
5. **b1のwait条件バグ**(自前コード): lambdaがcoroutineをawaitしていない(RuntimeWarning検知)。coroutine関数化して解消・再実行で全pass。
6. **中途実行の証拠YAML**: 失敗経過のYAML(5・0・7 pass版)とcollect-only時の空YAMLは削除し、全pass版(20261004-124823)のみを保持。
7. **prometheus-client 0.26.0**: 計画書は下限0.21のみ固定のためuv解決の0.26.0を採用。挙動差(sample遅延生成・registry=None=未登録・expositionに `_created` 含む)への対応は上記実装時判断1・2。破壊的変更ではなくdocs上の仕様追従のため、バージョン固定(<0.22等)へは変更しなかった(固定値の変更には当たらないと判断・疑義があれば指示願う)。
