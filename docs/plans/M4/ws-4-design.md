# M4 ws-4(Observability)設計メモ

- 作成: 2026-10-04(agent1)
- 前提: M4 ws-1(staging・load環境・マージ165242e)・ws-2(共有テスト資産)・ws-3(stagingフル実行
  ハーネス・マージ0b1e3a6)・ws-5(告知・同意+ログ許可リスト)完了。マージ後main test-ci 1604 passed・
  alembic head=0007。本単位はマイグレーション追加あり(0008新設 → 運用ルール1のtest-ci直列化は
  スーパーバイザー検証時の前提を引き継ぐ)
- 参照仕様: 09 §2.1〜§2.2・04 §2・§3・§7・05 §19(sessions契約)・12 M4-5・10 §1・10 §4.5〜4.7・
  11 §5(G4-4)・08 §2.4(ログ許可リスト)・STATUS G4節・ws-1 design §2.6(ダッシュボードの器)
- 本単位は**マイグレーション0008新設**(sessions・kpi_daily・kpi_muted_user_snapshot。05 §2に
  規定のないテーブルの追加を伴う — docs改版候補として記録・§6-3で承認済み)
- **確定**: supervisor承認事項4件とも裁定済み(2026-10-04・§6)。未解決の論点なし
  (docs改版候補・近似の告白・将来課題は§7に記録)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

計測3群(パイプライン/Jev/提案と行動)+アクセス系基本計測のメトリクスをダッシュボードで
見られる状態にし、MLR日次/週次集計(09 §2.1〜2.2のKPI定義どおり)を稼働させる。G4-4
「測定開始状態」(11 §5-4)とSTATUS G4節4項目目の先行確認を達成する器。消費者は
ws-6(性能5目標測定・層別予算トレース分離)・ws-7(Queue障害のlag alert・縮退・可用性)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | 3群の内容: パイプライン(Event Queue lag・Candidate数・Hard Filter通過率・Vector Retrieval件数)・Jev(実行回数・latency・cost)・提案と行動(LATCH Score分布・通知数・YES率・Mutual Latch Rate・キャンセル率) | 04 §2・§7 |
| 2 | アクセス系基本計測: セッション開始(アプリ起動・フォアグラウンド復帰)イベントでDAU・再来訪。エンドポイントPOST /v1/sessions。**イベントはuser_idと時刻のみを運び、Intentの文言を含まない** | 04 §2・01 §20(FR-11付記) |
| 3 | POST /v1/sessions契約: request body空・response 204・user_idと時刻のみ記録 | 05 §19 |
| 4 | 計測基盤(本番確定値): Cloud Monitoring + OTLPメトリクス。3群を**単一基盤へ集約**。02#5の検証(Vector件数・レイテンシ観察)とD-16のalertがこの基盤に依存 | 04 §3 |
| 5 | MLR = 分子(必要人数全員YESの候補数)÷分母(当該期間に通知を送信したLATCH候補数・1候補1カウント・グループも1候補・クローズ済みを除かない)。**proposed遷移を分母・matched遷移を分子とし、latch_status_eventsから日次で集計。期間は日次・週次の2種を常設** | 09 §2.1 |
| 6 | KPI表14指標(使われ方4・マッチングの質7・コスト2・安全網1)の分子/分母・周期(日次/週次/月次)は表どおり。**意図的な集計イベントを追加する場合も個別Intentの文言を含めない(08 §2.4)**。「使われ方・マッチングの質・安全網は提案と行動の群、コストはJevの群から導出」 | 09 §2.2 |
| 7 | G4-4: Observability 3群とアクセス系基本計測の**ダッシュボードが稼働**し、MLR日次集計(latch_status_events経由)が動作していること | 11 §5-4・12 M4-5・STATUS G4節 |
| 8 | staging・load・ダッシュボードはローカルdocker composeの別プロジェクト。**計測はOSSツールで代替**。クラウド本番同構成は不採用 | M4設計前提(2026-10-03単位表承認)・スーパーバイザー補記1 |
| 9 | ダッシュボードの器はws-4が追加。ws-1は「新サービスブロックの追記+共通アンカーへのenv/ポート追記」に局所化できる構造(YAMLアンカー)を保証済み | ws-1 design §2.6(supervisor承認) |
| 10 | Queue lagのalert・D-16 80% alert・circuit breaker動作確認がこの基盤に依存する(G4-6) | 04 §3・10 §4.5〜4.7 |
| 11 | stagingのClock制御はJST 0時系ジョブをClock操作で回せる(リセットジョブと同型に集計ジョブを設計する) | 10 §1(適用範囲5) |

### 1.3 実装の接続点(既存コード・実測)

| 素材 | 現状 | 接続点 |
|---|---|---|
| latch_status_events | 0001定義(latch_id・from_status・to_status・user_id・created_at)。遷移txと同時挿入済み | MLR・保留→expired率・提示率の集計源(そのまま) |
| latches.responses | jsonb配列[{user_id, value, answered_at}]・回答単位でanswered_atあり | YES率の日次集計(jsonb_array_elements) |
| match_events | (event_type・source_intent_id・version…・status・created_at・processed_at)。**created種=draft→active化を含む初回投入**(intents/service.py:742「初回投入は作成種」)。resume(paused→active)はupdated種・pauseは発行なし。idempotency UNIQUE(event_type, source_intent_id, version) | Intent作成率・再作成率の「作成(=active化)」の集計源 |
| Layer 2(candidates.py) | Layer 1通過件数(LIMIT前)とK_v=50上位を同一SQLで返す | Hard Filter通過率・Vector Retrieval件数の観測点(戻り値をinc/observe) |
| match_candidates | latch_score・jev_result jsonb・status | Candidate数gauge・Jev実行数の日次近似値(§2.7告白) |
| LLM Gateway(gateway.py) | Jev呼び出しのレイテンシ計測済み(latency_s→breaker.record) | Jev群latencyのhistogram観測点(既存計測の横流し) |
| JevCostGuard(cost/guard.py) | D-16 4カウンタ(Redis INCR)・80% alert=latch.cost.alert構造化ログ(emitter差し替え可能) | Jev群・コスト保護の可視化は**既存ログとカウンタをメトリクスへ投影**(改変しない) |
| CircuitBreaker(llm/breaker.py) | 状態はWorkerメモリ内・遷移はlatch.breakerロガー | 状態gaugeをWorkerメトリクスで公開(Redis共有化は既存論点のまま・触らない) |
| notifications | (type・payload・created_at)。INSERT経路は3箇所(store・latch_engine・sweeper) | 通知数は**日次SQL集計**で取る(3箇所への計測挿入を避ける) |
| calibration_records | actual_attended・cancelled_after・updated_at | 実行率・キャンセル率・無回答率(週次・遅延到着は遡及再計算で吸収) |
| intents | notification_level(muted)・status | 通知オフ率のmuted変更は**履歴を持たない**ためスナップショット差分(§2.8) |
| 構造化ログ | latch.llm.send・latch.push.send・logallow.py許可リスト(ws-5) | メトリクスはlabelにuser_id・Intent文言を**含めない**(08 §2.4と同規律) |

## 2. 実装方式の選択肢と推奨

### 2.1 メトリクス生成ライブラリ — prometheus-client直+pull型(推奨・2026-10-04 supervisor承認)

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A. prometheus-client直+pull型(推奨)** | 公式Python client(/prometheus/client_python・context7確認済み 2026-10-04)でCounter/Histogram/Gaugeを定義。APIは`make_asgi_app()`を/metricsへマウント・Workerは`start_http_server`。別プロジェクトのPrometheusがpull | 最小構成・unit試験が容易(カスタムレジストリ注入で値をassert)・api2台/worker2台はPrometheus側がtarget別にscrapeしPromQLでsum。**04 §3「Cloud Monitoring+OTLP」とは別のAPI**になる(§7論点2)。差し替え面を1モジュール(obs/metrics.py)に局所化し、本番同構成の費用判断時にSDK移行を1箇所に閉じる |
| B. OTel SDK→OTLP→OTel Collector→Prometheus形式 | docs本番確定値(引用#4)に最も近い。SDKがOTLP送信しCollectorがexport | サービス追加(Collector)・OTel Python SDKの初期化が重い・ローカルOSS代替には過剰構成。テストはバッチャー込みで手間 |
| C. Pushgateway(worker用push型) | workerがpushgatewayへpush | pull型でapi/workerとも問題なく観測できるため構成要素が増えるだけ。不採用 |

**採用: 案A。** 根拠: (a)引用#8の「計測はOSSツールで代替」に最も忠実 (b)ws-6が性能絶対値
(p95≦500ms等)の測定で負荷をかけた連続観測を要し、pull型+histogramで十分 (c)案Bの本番同型性は
Phase 0以降の費用判断で必要になった時点でobs/metrics.pyの差し替えとして後付け可能。

### 2.2 ダッシュボード — Grafana OSS+Prometheus・別composeプロジェクト latch-dashboard(推奨)

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A. Grafana OSS+Prometheus datasource(推奨)** | `compose.dashboard.yaml`(latch-dashboardプロジェクト)にprometheus・grafanaサービス。provisioning as-code(datasource YAML・dashboard provider YAML・dashboard JSONをリポジリ管理=Grafana起動時に自動ロード・context7でprovisioning手順確認済み 2026-10-04) | ダッシュボード定義がバージョン管理されG4証拠に使える。既存15ポートと衝突しないポート(prometheus=19090・grafana=13000) |
| B. Grafana Postgres datasourceで直接SQL | kpi_daily等をSQLで可視化 | メトリクスとSQLの2系統の見せ方に分かれ、引用#4「単一基盤へ集約」から外れる。KPI定義がダッシュボード定義内に埋もれる |
| C. 自作HTML等 | — | 稼働確認・証拠の標準性で劣る。不採用 |

**採用: 案A(単一datasource)。** KPI日次集計は「集計ジョブ→kpi_daily(正)→当日値をgaugeで
export」でPrometheusへ寄せ、ダッシュボードは全てPrometheus 1系統で見る。

### 2.3 KPI/MLR集計 — 日次バッチ集計ジョブ→kpi_daily(正)→gauge export(推奨・2026-10-04 supervisor承認)

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A. 日次バッチ集計→集計テーブル(推奨)** | Workerの独立タスク(ResetJobと同型の「次のJST 0時まで待つ→実行→遡及4日分をUPSERT→次へ」)がlatch_status_events等からSQL集計しkpi_dailyへ1日1行の**分子/分母列**を書く。周期は引用#5(日次・週次の2種を常設)に従い、週次/月次は日次行の範囲集計+遡及可能なSQLで算出。当日値は60秒周期のexporterがgauge化 | 09 §2.1の「日次で集計する」に忠実・KPI定義がコードで固定されunit試験できる・遅延到着(自己申告3日等)を遡及UPSERTで吸収・G4証拠はSQLで値が取れる・stagingはClock操作で日次ジョブを即時発火できる(引用#11) |
| B. Grafana SQL直接 | 集計ジョブなし | 「日次集計が動作していること」(G4-4)の実体がダッシュボード依存になり証拠・テストが弱い。不採用 |
| C. リアルタイムcounterのみ | 全指標をプロセス内counter | DAU・再来訪・継続率の**重複排除(distinct user)がcounterでは表現できない**。再起動でcounterがゼロに戻り遡及修正不可。不採用 |

kpi_dailyは比率ではなく分子/分母のみを保持(MLR日次=当日の分子/分母・週次=7日分の合計比)。
遡及幅(既定4日)はD-09の自己申告期限3日+マージン。

### 2.4 アクセス系基本計測 — sessionsテーブル新設+POST /v1/sessions(推奨・2026-10-04 supervisor承認)

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A. DBテーブル新設(推奨)** | sessions(id・user_id・created_at。INDEX(user_id, created_at))。APIは認証必須・body空・INSERT・204。DAU・再来訪・継続率はsessions横断SQLで遡及算出 | distinct user集合演算が正確(案Cの欠点を解消)。05 §2にテーブル規定がない(引用#3は記録内容のみ)ため**docs改版候補**(§7論点3) |
| B. 構造化ログのみ | latch.sessions.send的なログ | 集計バッチがdocker logsを読むことになり脆い。logallowへの新規登録も要る。不採用 |
| C. counterのみ | +1計測 | 重複排除不可(DAU不可)。不採用 |

フロントエンド(起動時呼び出し)の改修は本単位スコープ外(単位表は「POST /v1/sessions新設」まで)。
stagingでの実測はAPI直叩き(JWT発行済みテストユーザー)で足りる。Phase 0でフロントから接続。

### 2.5 scrape接続 — ホスト公開ポート経由(推奨・2026-10-04 supervisor承認・条件つき)

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A. ホストポート経由(推奨)** | Prometheusのscrape_configs(static_configs・context7確認済み)が127.0.0.1の公開ポートを叩く: api1=18001/metrics・api2=18002/metrics・worker1=18101・worker2=18102(loadは28000系) | composeプロジェクト間のネットワーク結合ゼロ。既存のports公開に追記するだけ(ws-1設計§2.6(b)の「外部公開もportsへの追記だけで足りる」どおり) |
| B. externalネットワーク共有 | latch-stagingネットワークへlatch-dashboardが参加・DNS名でscrape | ネットワーク名の固定化でcompose間結合が生まれる。不採用 |

APIの/metricsはnginx経由でなく**api1・api2直をそれぞれscrape**(nginxで振ると2台の値が混ざり
混乱する。Prometheus側でsum()する)。/metricsは認証外・無レート制限の生のASGIマウント。
**承認条件(§6-4)**: composeのportsは明示的な127.0.0.1バインド(既存規約どおり)とし、
staging受け入れに「ホスト外(非ループバック)から/metricsへ届かない」確認を1項目入れる(§4)。

### 2.6 パイプライン群の実装点

- **Event Queue lag**: Pub/Subエミュレータはsubscription統計(num_undelivered_messages等)を
  返さないため、**DB outbox(match_events)を真実の源泉**にする。exporterの周期ジョブ(60秒)が
  `status='pending'`件数と最古行の年齢(created_atからの秒数)をgauge化。
  (04 §3「Queue lagはCloud Monitoringで標準計測」の本番差分・§7論点2に含める)
  ws-7のQueue障害試験(毒ペイロード・lag alert発報・10 §4.7)はこのgaugeを消費する
- **Hard Filter通過率・Vector Retrieval件数**: layer2(candidates.py)が既に「Layer 1通過件数
  (LIMIT前)」と「K_v上位件数」を返すため、呼び出し元(run_candidate_retrieval経路)で
  counter増分(通過/入力)とhistogram observe(件数分布)のみ追加。**ロジック変更なし**
- **Candidate数**: match_candidatesのstatus='candidate'件数を周期ジョブがSQL count→gauge
- **breaker状態**: WorkerのCircuitBreaker.state(メモリ内)をWorkerメトリクスでgauge公開
  (closed=0/open=1/half_open=2。動作確認の見える化用)

### 2.7 Jev群の実装点

- **実行回数**: JevWorkerの実呼び出し点でcounter増分(label: provider・第一候補/フォールバックは
  jev_result記録と同一の区別)
- **latency**: Gatewayが既に計測済みのlatency_sを同じ観測点でhistogram.observe(label: provider)
- **cost**: 実課金ゼロ(M4前提・LLMはstub)。usage(input/output tokens)をcounterで積み、
  単価変換はダッシュボード側の変数で表現可能にする(docs「1成立あたりAIコスト」の導出素材)。
  stubのusageは0(stub.py定数)のため実測値は0 — docsのcost計測の器だけ用意する
- **日次恒久値(kpi_daily.jev_executions)**: counterはプロセス内メモリで再起動でリセットされる
  ため、恒久集計はmatch_candidatesのjev_result書込み行(created_at日付)からSQL近似とする
  (**近似の告白・§7論点5**: 「同一評価世代スキップでH再検証のみ」の行の扱い等、実行回数の
  厳密値との差が生じうる。厳密化は将来の構造化ログ/DB記録追加で)

### 2.8 提案と行動群の実装点

- **LATCH Score分布**: latch_engineの提案確定点でhistogram.observe(1対1=latch_score・
  グループ=aggregate_scoreを別label)。監視対象はD-01通知閾値0.60周辺(A/B・Phase 3前提の器)
- **通知数**: 日次SQL集計(notificationsのtype別count)→kpi_daily。INSERT 3箇所への計測挿入を
  避ける(§1.3)。リアルタイム必要なし(09 §2.2は日次)
- **YES率・MLR・保留→expired率・提示率・Candidate生成率**: kpi_dailyのSQL集計(§2.3)
- **実行率・キャンセル率・無回答率(週次)**: calibration_records集計・遡及4日で自己申告の
  3日期限を吸収。無回答率=「completed_at+3日経過 AND actual_attended IS NULL」
- **通知オフ率(安全網・週次)**: muted変更は履歴を持たないため、**kpi_muted_user_snapshot**
  テーブルに日次のmutedユーザー集合を保存し「前日集合に無く当日集合にあるユーザー」を新規muted
  として日次カウント。週次はsnapshot横断で重複排除したユーザー数(「当該週に1つでもmutedへ変更」
  の正確近似。**仕様解釈の告白・§7論点4**: 往復変動(解除→再muted)は日次境界でしか観測しない)
- **DAU・再来訪・継続率・Intent作成率・再作成率・Active Intent数**: sessions・match_events
  (created種)・intents(users.created_at=新規基準日)からのSQL集計。kpi_user_daily型の
  ユーザー日次ファクトは**作らない**(既存テーブル横断SQLで遡及算出可能なため。mutedのみ
  履歴不在のためsnapshotが要る)

### 2.9 HTTP基本メトリクス — 追加する(2026-10-04 supervisor承認)

docs 3群の明示的な拡張: 全APIのリクエスト数counter+レイテンシhistogram(method・pathテンプレート
・status label)。**user_id・クエリ・bodyはlabelに含めない**(08 §2.4規律)。理由: ws-6の性能5目標
(Intent保存p95 500ms等・10 §4.1)が絶対値合格であり、測定基盤としてAPIのp95を取れることが
実質前提(単位表でws-6はws-4に「測定基盤前提」で依存)。ラベルにpathテンプレートのみ使う。
docs 3群への拡張である旨は**G4時確認事項**への記録対象(記録はsupervisor側・§6-1)。

## 3. ファイル構成

### 3.1 作るもの

| ファイル | 内容 |
|---|---|
| `backend/src/latch/obs/__init__.py` | 空パッケージ |
| `backend/src/latch/obs/metrics.py` | **全メトリクスの単一真実**。namespace=latch・Counter/Histogram/Gauge定義(3群+アクセス系+HTTP)。カスタムレジストリ注入のテスト経路。本番SDK移行時の差し替え面(§2.1) |
| `backend/src/latch/obs/middleware.py` | HTTP基本メトリクスmiddleware(§2.9・承認済み) |
| `backend/src/latch/obs/sessions.py` | POST /v1/sessions(ルーター+INSERT store・認証必須・body空・204) |
| `backend/src/latch/obs/collector.py` | 60秒周期のexporter: pending件数/最古pending年齢/Candidate数/breaker状態/kpi_daily当日値→gauge。Clock注入(年齢計算はClock経由) |
| `backend/src/latch/obs/kpi.py` | 日次集計ジョブ(次のJST 0時まで待つ→遡及4日分UPSERT)・KPI計算SQL(分子/分母)・09 §2.2の14指標定義の定数表(KPI定義の単一真実) |
| `backend/alembic/versions/0008_obs_sessions_kpi.py` | sessions・kpi_daily・kpi_muted_user_snapshot(§2.4・§2.8) |
| `backend/src/latch/settings.py` | 追記: `obs_worker_metrics_port`(既定0=無効・workerのstart_http_server)・`obs_collect_interval_sec`(既定60)・`kpi_backfill_days`(既定4) |
| `backend/src/latch/main.py` | 追記: /metricsマウント(make_asgi_app)・obs_sessions_router登録・middleware挿入 |
| `backend/src/latch/worker/main.py` | 追記: collector・日次集計ジョブのDIと起動(start_http_serverは`obs_worker_metrics_port>0`のとき) |
| `backend/src/latch/worker/matching/candidates.py` への呼び出し元 | 追記: 通過率counter・Vector件数histogram(§2.6・ロジック変更なし) |
| `backend/src/latch/worker/jev.py` | 追記: 実行counter・latency histogram・usage counter |
| `backend/src/latch/worker/matching/latch_engine.py` | 追記: 提案時のscore histogram observe |
| `compose.staging.yaml` | 追記: worker-commonへLATCH_OBS_WORKER_METRICS_PORT=9100・worker1/2へports(18101/18102→9100)。api-commonへ変更なし(/metricsはアプリ内) |
| `compose.load.yaml` | 追記: 同様(28101系) |
| `compose.dashboard.yaml` | 新設: latch-dashboardプロジェクト(prometheus 19090・grafana 13000・ホストポート127.0.0.1のみ) |
| `docker/prometheus/prometheus.yml` | scrape_configs(static_configs: 18001・18002・18101・18102・prometheus自身) |
| `docker/grafana/provisioning/datasources/prometheus.yaml` | Prometheus datasource provisioning |
| `docker/grafana/provisioning/dashboards/default.yaml` | dashboard provider(ファイル型) |
| `docker/grafana/dashboards/overview.json` ほか3〜4枚 | ダッシュボード定義(①overview: 3群+アクセス+MLR・②pipeline・③jev/cost・④behavior/KPI) |
| `Makefile` | 追記: dashboard-up/down/ps/logs |
| `backend/tests/unit/test_obs_metrics.py` ほか | §4のとおり |
| `docs/plans/M4/ws-4-report.md` | 報告書(受け入れ確認・証拠つき) |

### 3.2 触らないもの

- **compose.yaml(ci)**・**compose.staging.yamlの既存サービス定義の実質**(追記はアンカーへのenv/
  ポート追加に局所化 — ws-1設計§2.6(a)どおり)。ci環境へworker metricsポートは設定しない
  (既定0=無効・ciは自回帰の機能試験が目的)
- **JevCostGuard・CircuitBreakerのロジック**(latch.cost.alert構造化ログ・emitter経路は無変更。
  メトリクスは横流しのみ)。breakerのRedis共有化(旧design §2.11-1論点)は本単位でやらない
- **latch.llm.send・latch.push.send・logallow.py**(ws-5資産。メトリクスとの統合はしない。
  送信記録とメトリクスは別の媒体として並存)
- 既存の遷移・回答・通知の書込みロジック(latch_status_events・responses・notifications INSERTは
  読むだけで改変しない)
- 既存テスト・既存settings項目の値

## 4. テスト方針

- **unit(backend/tests/unit/・basename一意規約)**:
  - `test_obs_metrics.py`: メトリクス定義(名前・label・namespace)・カスタムレジストリで値assert・
    新規メトリクスはmetrics.pyに登録必須の機械検査(全メトリクス名の一覧化=許可リストと同型の規律)
  - `test_obs_sessions_api.py`: POST 204・401(未認証)・body非空400/422・INSERT検証(user_id・
    時刻のみでIntent文言を運ばないことの構造確認)
  - `test_obs_kpi.py`: **KPI計算の純関数を期待値表で固定**(09 §2.2の14指標×代表ケース: proposed/
    matched遷移→MLR・回答→YES率・calibration→実行率・snapshot差分→通知オフ率等)。遡及UPSERT
    (4日幅)・JST期間切り(FakeClock)・継続率の観測窓(30日未満は算出しない)
  - `test_obs_collector.py`: pending件数・最古年齢・Candidate数のgauge反映・kpi当日値export
- **integration(test-ci)**: sessions実DB・/metrics 200(Content-Type text/plain)・集計ジョブの
  fixture DB実行・マイグレーション0008のhead検証
- **staging受け入れ(ws-3ハーネス流の証拠YAML)**: staging-reset→シード→API操作で
  proposed/matched遷移を作る→sessions投与→`make staging-clock`でJST 0時跨ぎ→日次集計発火→
  `make dashboard-up`→prometheus/grafanaで(a)3群メトリクス到達(b)ダッシュボード4枚稼働
  (c)MLR日次値がkpi_daily・ダッシュボード・シード期待値と一致することを証拠YAMLへ
  (docs/testassets/results/へ・10 §5形式)。(d)**ホスト外(非ループバック)から/metricsへ
  届かないことの確認**(§6-4承認条件・証拠YAMLへ1項目として記録)
- ci-db干渉: kpi_daily等の新テーブルは既存試験のfixture・teardown規約に従い、他テーブルの
  削除順(FK)に依存しない独立表として配置

## 5. 完了条件(計画書への引き取り用ドラフト)

1. 3群+アクセス系のメトリクスが/metricsから露出し、Prometheusがscrapeしている
   (api1・api2・worker1・worker2)
2. latch-dashboardプロジェクト(prometheus・grafana)がprovisioningで起動し、ダッシュボードが
   空JSONでなく実系列を表示する(overviewにMLR・3群・DAUが含まれる)
3. POST /v1/sessionsが05 §19契約どおり動作し、sessionsテーブルへuser_idと時刻のみ記録する
4. 日次集計ジョブがJST 0時にkpi_dailyを書き、MLR日次・週次(7日合計比)が09 §2.1定義どおり
   算出される(stagingでClock操作により発火を確認)
5. 09 §2.2の14指標がkpi_dailyの分子/分母として算出可能な状態(周期別: 日次=当日行・週次/
   月次=範囲集計)。近似を伴う指標(Jev実行数・通知オフ率)は設計§2.7/§2.8の方式どおり
6. unit+integration試験グリーン・staging受け入れ証拠YAMLがdocs/testassets/results/へ保存
7. /metricsへホスト外から到達しないこと(§6-4承認条件・(d)項の証拠)

### 5.1 計画書への引き取り事項(スーパーバイザー指示・2026-10-04)

- **運用ルール1の明記**: 0008はマイグレーション追加単位のため、「マイグレーション追加単位と
  DB消費単位のtest-ci直列化」を計画書§0へ明記する
- **127.0.0.1明示バインド**: composeのportsは`127.0.0.1:ポート:コンテナポート`形式で明示し、
  検証手順に「ホスト外(非ループバック)から/metricsへ届かない」確認を1項目入れる
- **ws-7との整合**: Queue lag=match_events pending計測がws-7のlag alert発報試験(10 §4.7)の
  消費先として成立することを計画書で担保する(呼び出し・参照の接続を明記)
- **機械チェック**: 計画書レビュー時のbasename一意・ピン試験追随访問・DB干渉対策は既存規約
  どおりに実施する

## 6. supervisor承認事項(2026-10-04・4件とも裁定済み — 未解決の論点なし)

1. **HTTP基本メトリクス: 入れる(承認)**(§2.9)。根拠: 単位表でws-6はws-4に「測定基盤前提」で
   依存し、04 §7の性能目標(Intent保存p95 500ms等)は絶対値合格のためAPI側p95 histogramが
   実質前提。label規律(method・pathテンプレート・statusのみ・user_id不使用)は08 §2.4と同規律で
   承認。docs 3群への拡張であることは**G4時確認事項**へ記録対象(記録はsupervisor側)
2. **04 §3とのdocs差分: 承認**(§2.1・§2.6)。M4設計前提(2026-10-03単位表承認と同時確定・
   計測はOSSツールで代替)どおり。本番確定値(Cloud Monitoring+OTLP)自体は変更しない。
   ゲート時ユーザー確認事項への記録はsupervisor側でSTATUSへ載せる。obs/metrics.py 1モジュール
   局所化も承認。Queue lag=match_events pending計測の読み替えも、エミュレータがsubscription
   統計を返さない制約からの必然で承認(**ws-7のlag alert消費と整合することを計画書で担保**
   — §5.1の引き取り事項)
3. **マイグレーション0008の3テーブル新設: 承認**(§2.4・§2.8)。05 §2追記はdocs改版候補として
   記録(M2 ws-7 group_candidates・M3 ws-4 Indexの前例と同型)。kpi_dailyの分子/分母保持・
   遡及UPSERT方針も承認
4. **/metrics認証外公開: 承認・条件つき**(§2.5)。composeのportsは明示的な127.0.0.1バインド
   (`127.0.0.1:18001:8000`形式)とすること。計画書の検証手順に「ホスト外(非ループバック)から
   /metricsへ届かない」確認を1項目入れる(§4(d)・§5-7)。ci環境は無効既定
   (obs_worker_metrics_port=0)のまま維持

## 7. 未解決の論点 — なし(docs改版候補・告白・将来課題の記録のみ)

1. ~~HTTP基本メトリクスのスコープ~~ → **解消**(§6-1で承認)
2. ~~本番計測基盤との差分~~ → **解消**(§6-2で承認・G4時確認事項とSTATUSへの記録はsupervisor側)
3. **05 §2のテーブル規定追記**(docs改版候補・前例: M1 ws-3のlocation_name追記): 本単位では
   実装を先に進め、改版は次回のdocs改版へ含める(§6-3)
4. **通知オフ率の近似**(§2.8): 「当該週に1つでもmutedへ変更したユーザー数」を日次スナップ
   ショット差分で観測する。muted履歴テーブルの追加(正確な変更時刻の記録)は行わない
   — スキーマ拡張の効果が週次KPIの粒度改善に限定されるため。将来、正確な変更時刻が必要に
   なった時点でイベント記録の追加を再検討する(08 §2.4の「個別Intentの文言を含めない」規定は
   変更イベント記録にも適用)
5. **Jev実行回数の日次恒久値の近似**(§2.7): kpi_dailyのjev_executionsはjev_result書込み行からの
   SQL近似。実行回数の厳密値はPrometheus counter(増分)で補完。差異の規模はstaging運用で
   比較観測し、無視できない場合にログ/DB記録の追加を提案する
6. **breaker状態の可視化はメモリ内gauge**(§2.6): Worker再起動で値が初期化される既存挙動のまま。
   Redis共有化は旧design論点のままで本単位では扱わない
