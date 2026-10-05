# M4 ws-7(非機能試験・障害系)設計メモ

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

単位表(M4 ws-7)どおり、非機能試験の残り4領域をstaging環境で実施し、証拠を
docs/testassets/results/へ残す。

1. **可用性**(10 §4.3): 合成監視プローブ(1分間隔)でAPI成功率を測り、99.5%(01 §20)を合格とする。稼働中のAPIコンテナ1台を強制停止し、残存インスタンスでの処理継続と自動復旧を確認する
2. **バックアップ復旧**(10 §4.4): バックアップから新DBインスタンスへ復元し、embeddingモデル識別子(01 §18)の記録から再エンベディングを経てサービス再開、cross-check(復旧前後のactive Intent数一致)までを確認
3. **縮退**(10 §4.5の残部): D-16上限到達(80% alert・上限でJevスキップ・0時JSTリセット復帰・月次上限のClock操作)とresume/Bucket遅延回収(FR-18)。**M2繰越分**
4. **Queue障害**(10 §4.7): 毒ペイロードのquarantined隔離・削除済み参照のprocessed破棄・UNIQUE重複排除・滞留の非波及・Queue lag alert発報

実施はws-6と環境共有のため直列(単位表依存どおり。staging-resetから開始し、測定中はci一時停止=運用ルール6)。

### 1.2 引用確定値(節番号つき)

- **10 §4.3**: 「計画停止(デプロイ)を除外タグ付きで区別したうえで、APIリクエスト成功率を合成監視プローブ(1分間隔)で測定する。試験としては、稼働中のAPIコンテナを1インスタンス強制停止し、残存インスタンスでの処理継続と自動復旧を確認する。合格は試験期間中の成功率99.5%以上(01第20節)」。試験期間の値は本書に規定なし(承認事項§5-1)
- **10 §4.4**: 「定期バックアップから新しいDBインスタンスへ復元し、Embeddingモデル識別子の記録(01第18節)を使ったVector再構築(再エンベディング)を経てサービスを再開する一連の手順を試験する。復旧後、Intent検索・マッチング・通知が正常であること、およびcross-checkとして復旧前後のactive Intent数が一致することを確認する。RPO・RTOは目標値を持たず(01に規定なし)、試験ごとに実測を記録する」。バックアップの取得方式・周期は本書に規定なし(承認事項§5-3)
- **10 §4.5**のうち本単位の対象: **D-16上限到達**(「Jevカウンタを日次上限(30,000回)の手前まで投入して進める。80%(24,000回)でalertが発報され(alertにEvent源流上位ユーザーのレポートが添付されること含む)、上限到達でJev実行がスキップ・『Jev未判定』記録・提案なしとなること、0時(JST)のリセットで復帰することを確認する。月次上限(暦月初JST 0時リセット)はClock操作で月初境界を進めて日次カウンタとの独立性を確認する」)と**resume・Bucket遅延の回収(FR-18)**((a)「一方をpauseしたうえで対象開始時刻のBucketをClockで経過させ、その後resumeする。resume時にversion+1の再評価Eventが発行され(05第6節)、Embeddingの再実行なくCandidate Retrievalから再評価され、提案が発生し得る状態に復帰する」(b)「Bucket到来Eventのスケジューラを一時停止して欠落を作り、期限前catch-upスキャン(06 v0.3第9節)が回収する — expires_atまで2時間を切った未処理のIntentが、直近の詳細評価から30分経過後に再評価され、その際Embedding済みIntentが対象に含まれembedding IS NULLのIntentが除外されること」)
- **10 §4.5のLLM障害系((1)〜(6)・FR-18のLLM回復後再評価)は本単位の対象外** — M2 ws-8でci環境E2Eとして実施済み(切替・双障害skipped・breaker開放/半開往復・p95分離・復旧再評価・Worker一気通貫。M2 ws-8 report「完了条件の検証結果」・コミットdfcb917)
- **01 §20**: 可用性=「計画停止を除くAPIリクエスト成功率99.5%以上」。D-16=日次30,000回・月次600,000回・80%到達でalert(Event源流上位ユーザーのJev消費レポート添付)・上限到達時は「Jev実行をスキップして候補を『Jev未判定』として保留」。バックアップ=「Intent DBとVector DBは定期的にバックアップし、バックアップからの復旧でVector DBを再構築できること」
- **01 §18**: 「Intentには、Embeddingに用いたモデルの識別子と版も記録する。モデル変更時の再エンベディングの対象は、この記録で特定する」
- **04 §5 D-16**: 「カウンタはJev Workerではなく実行要求側(Matching Worker)で増やす」(並行実行でも超過幅を1要求分に抑える)
- **06 第9節**: 処理失敗=「5回まで再試行 → 失敗理由を保持して隔離(match_events.status=quarantined)」。**参照先Intent不在(削除済み)のEvent=「破棄とし、status=processedとして閉じる」**・**payload不正=「5回の再試行後に失敗理由を保持してquarantinedへ隔離する(…10第4.7節の毒ペイロード試験の対象はこちら)」**。catch-upスキャン=「expiry_sweeper(第6節)と同一の60秒周期スケジューラで、status='active'かつexpires_atまで2時間以内かつ直近の詳細評価の実施から30分以上経過のIntentを抽出し、Candidate Retrievalからパイプラインを直接投入する(Eventを発行しない…)。embedding IS NULLのIntentは対象から除外する」
- **01 §16**: resume時version+1の再評価Event発行・期限前catch-upスキャン(60秒周期)の2補償。同一評価世代のJev再実行スキップ(jev_result再利用)
- **10 §4.7**: 毒ペイロード注入→retry上限5回→quarantined・滞留が後続イベントに波及しない。削除済みIntent参照Eventはprocessedで破棄。UNIQUE制約による同一Event重複排除。「Queue滞留を作りQueue lagメトリック(01第20節)のalertが発報することも確認する」— **alert閾値の値はdocsに規定なし**(承認事項§5-5)
- **10 §5**: 証拠は「試験ID・実施日・環境・入力条件・合否」をdocs/testassets/results/へ保管

### 1.3 実装の接続点(既存コード・実測。すべてマージ済みmain)

- **compose.staging.yaml**: api1+api2+nginx前段(入口127.0.0.1:18000)・worker1/worker2・db(15432)・redis(16379)・pubsub(18085)。api/workerは`restart: unless-stopped`(**自動復旧の器**)・`LATCH_CLOCK_MODE: controlled`・LLM/FCM=stub。docker/nginx/staging.confは`proxy_next_upstream error timeout http_502 http_503 http_504`+`proxy_next_upstream_tries 2`のpassive死活除外で、**「ws-7がdocker killで検証する」旨のコメントがws-1設計時に織り込み済み**
- **Makefile**: staging-clock(show/set/advance・ControlledClock操作)・staging-publish(latch.events CLI。**`--raw-payload`で生JSON直投げが既存**・docstring「毒ペイロード試験用。3点組の組立より優先」)・staging-reset(down -v→up→migrate→geo-import)・staging-test(ws-3ハーネス型)・staging-obs-test(ws-4ハーネス型)
- **obs/collector.py**: workerプロセス内の60秒周期exporter。event_queue_pending・event_queue_oldest_pending_secondsのGaugeへDB集計値を写す。docstringに「**ws-7のQueue障害試験(10 §4.7 lag alert発報)はこのgaugeを消費する**」— 発報器自体は未実装で本単位が担う。**collectorはworker内で動くため「worker停止で滞留を作る」と発報器も止まる矛盾がある**→§2.5で置き場所を確定
- **worker/cost/guard.py**(D-16 alertの既存実装): DAILY_ALERT=24,000・MONTHLY_ALERT=480,000・**alertはINCR戻り値が丁度しきい値に等しいとき1回だけ発報**・媒体は構造化ログ(latch.cost.alertロガー)・alertに源流レポート(store.scan_report)添付済み
- **worker/cost/store.py**: Redisキー=`{prefix}jev:daily:{yyyymmdd}`(TTL 48時間)/`{prefix}jev:monthly:{yyyymm}`(TTL 45日)/jev:intent・jev:user・jev:exec。**delete_daily/delete_monthlyが既存**(テスト用)(※suervisor訂正: 当初の設計メモは日次TTLを45日相当と書き誤り・実コードのdocstringどおりに修正)
- **Jev予算(M2 ws-4)**: 1Intent 40回/日・1ユーザー120回/日・頻度制限30分が実Event投入の先に立つ → **実Eventのみで日次30,000回へ届けるにはws-6級の大規模投入(1万件超)が要る**(ws-6 run4で実際に自然到達)。境界試験には不経済
- **M2 ws-2資産**: バックフィル(worker/backfill.py・embedding IS NULLのIntentをEmbedding Workerで回収・embedding_completed発行)
- **ws-6 G4確認事項7・8**(report §8): bucket上限10秒の張り付き=解像度上限・**D-16縮退時の30分バケット再評価循環でqueue pendingが残存し続ける**(仕様動作の縮退と収束判定の相互作用)。§2.3の手順で観察記録を残す
- **ws-6実施教訓**(report §9.2): トークン事前取得(ウォームアップ)・検証チェーンにパイプを書かない・件数実査

## 2. 実装方式の選択肢と推奨

### 2.1 可用性プローブ — loadgenにprobeサブコマンド追加(案A・推奨)

- **案A: loadgenへのprobe新設(推奨)** — asyncioループで1分間隔にnginx(18000)へGET /healthと認証付きGET /v1/users/me(staging-tokenで事前発行・ws-6のウォームアップ知見)を打ち、成功/失敗とタイムスタンプをメモリ集計し、`--out`でYAML出力する。`--planned-outage`実行(または実行中のタグ記入)で計画停止サンプルを除外対象としてマーク。タイムアウトは10秒(nginxのproxy_next_upstream再送が収まる値)
- **案B: Prometheus blackbox exporter追加** — probe対象をPrometheus側のjobへ集約できるが、新規コンテナ依存・証拠はPromQL集計の取り直しが必要。10 §5の証拠形式(YAML・試験ID・合否)への落とし込みが遠回り
- **案C: curl+シェルループ** — 追加コードゼロだが、成功率の機械集計・タグ処理・証拠化をシェルで担保しにくい(誤認リスク。ws-6実施教訓と逆方向)
- 推奨理由: ws-6で作ったloadgen(users/perf/bruteforce/mass)と同型・追加依存ゼロ・証拠YAML化が一貫。**プローブが載るのはAPI層でない点にも注意**(プローブ自体は観測者であり、APIの内側に載せない)

**強制停止と自動復旧の手順**: `docker kill latch-staging-api1-1`(SIGKILL)→Dockerのrestart: unless-stoppedが自動再起動する(`docker stop`は意図的停止として再起動しないため使わない。nginx.confコメントと整合)→nginxがconnect失敗を検知しapi2へ再送(passive死活除外)→api1がhealthyに復帰したのち負荷分散へ復帰。プローブ結果と、composeの再起動・healthy復帰の時刻記録を証拠にする。

**成功率の定義**(承認事項§5-2): 成功プローブ数÷総プローブ数(計画停止タグ付きサンプルは分母外)。間隔は10 §4.3字義どおり1分間隔を本番とし、failoverの精度観察用に10秒間隔の密プローブを併設する(合否判定は1分間隔側のみ・密側は観察記録)。**計画停止の除外タグの動作確認**を兼ねて、試験中にapi2を意図的に1回stop→startし、その窓をタグ付きで除外集計できることを確認する。

### 2.2 バックアップ復旧 — pg_dump→db2復元→識別子で対象特定→embedding NULL化→backfill(案A・推奨)

- **案A: Makefile手順化+復元後にembedding列をNULL化(推奨)** — `make staging-dump`(docker execでpg_dump→.staging/へ)→db2コンテナをdocker runで新規ボリューム・別ポート(16543)へ立ててpg_restore→api/workerをdb2向きのLATCH_DATABASE_URLで再起動(compose.staging.yamlは触らず、起動時のenv上書きのみ)→**復元DB上で「embedding IS NOT NULL AND embedding_model IS NOT NULL」の行を対象にembedding/embedding_model列をNULL化**(01 §18どおり、識別子の記録で再エンベディング対象を特定する手順を検証する意図を兼ねる)→backfill実行→embedding_completed→パイプライン復帰→cross-check
- **案B: pg_dumpの段階でembedding列を除外** — 「バックアップにvectorが含まれない」前提に忠実だが、pg_dumpに列除外オプションがなく自作copy(c\copy SELECT)が必要。手順が増え、破損リスクが増える。NULL化(案A)と検証内容は同じ
- **案C: WALアーカイブ/PITR風の継続バックアップ** — 本番基盤の担い手(04・11 Phase 0)でありstaging試験には過剰(YAGNI)
- 推奨理由: 10 §4.4が試すのは**復旧手順**(復元→再エンベディング→再開→cross-check)であってバックアップの自動化そのものではない。pg_dump1回を「定期バックアップ1サイクル」と読み替える(承認事項§5-3)。案AのNULL化は06第9節「embedding IS NULLのIntentは対象から除外」の逆経路=「NULLになった行はEmbedding Worker対象」という既存規定に自然に乗る。なお本実装ではIntent DBとVector DB(pgvector)が同一のPostgreSQLインスタンスに統合されており、DB復元がDB復旧に直結する(「Intent DBとVector DBは定期的にバックアップ」(01 §20)の読み替えも承認事項§5-3へ含める)
- **cross-check**(10 §4.4字義): ①復旧前後のactive Intent数一致(SELECT count比較) ②Intent検索・マッチング・通知が正常=初回投入Event 1件→提案→通知記録までのスモークを復旧後に行う。③RPO/RTOを実測記録(ダンプ開始→プローブ復帰・復元開始→提案復帰。目標値なし・10 §4.4)
- 対象データ: ws-2のシード生成器でactive Intent数百件+Embedding済み(ws-6の実績からstagingで数十秒程度)にしてからダンプ

### 2.3 D-16縮退 — Redisカウンタ直接セット+実Event投入で境界を通す(案A・推奨)

- **案A: カウンタ直接セット+実行1〜2回でalert境界と上限到達を通す(推奨)** — D-16カウンタ操作の小CLI(§3のstaging-d16・jev:daily/jev:monthlyへのSET/GET/DELのみ・キー名はClock導出)で日次カウンタを23,999へセット→実Event 1件投入(INCR=24,000)→**daily_80pct alert発報+源流レポート添付+1回だけの発報**をworkerログで確認→29,999へセット→実Event 1件(INCR=30,000)→Jevスキップ・match_events/候補側の「Jev未判定」記録・**提案ゼロ**を確認→staging-clock setで翌日0時JSTを跨ぐ→新dailyキーに切替→実行が復帰することを確認
- **案B: 実Eventのみで24,000〜30,000回を投入** — Jev予算(1Intent 40回/日・1ユーザー120回/日)と頻度制限30分が先に効くため、ws-6級の1万件超投入の再現が必要。可能だが環境負荷・時間が大きく、境界の精密な観察には向かない
- **案C: 29,999へのセットのみ** — alert発報の観察ができない(INCR戻り値が丁度しきい値で1回だけ、という実装挙動の検証も落ちる)
- 推奨理由: 04 §5のalert機構(しきい値・1回だけ・レポート添付)は既存実装でunit試験済みであり、stagingで試すのは**実経路での発報と縮退への復帰**。案Aはその最小の実経路通し
- **月次との独立性**(10 §4.5字義): 月次カウンタを479,999へセット→実Event 1件(INCR=480,000)→**monthly_80pct alertのみ発報**(日次は未到達=両者の独立性の証明)→staging-clock setで月初境界(暦月初JST 0時)を進める→月次キーが新yyyymmへ切り替わり月次側の実行が復帰、日次カウンタは残留(TTL内)で独立していることを確認
- **ws-6 G4確認事項8の観察**: D-16上限到達状態で30分バケット再評価循環が走るとpendingが残存し続ける(ws-6の観察)。本試験でも上限到達後のpending推移を時系列で記録し、裏取りする(収束判定は要求しない)

### 2.4 resume・Bucket遅延回収 — Clock操作込みのstaging一気通貫(手順設計が本体)

resumeとcatch-upの実装自体はM2で済み(M2 ws-1のresume Event発行・06第9節のcatch-upスキャン)、ci環境でも部分的に試験済み。本単位は**Clock操作とworker停止を含む実環境での一気通貫と証拠採取**。

**(a) resume再評価**(10 §4.5(a)字義): 同一ユーザーで提案成立し得る条件のIntent 2件を作成(Embedding済み。ただし同一ユーザー間は自己ペア除外FR-17のため提案は成立しない — **「提案が発生し得る状態への復帰」の観察には別ユーザーの相手Intentを対で配置する**。10字義の「2 Intent」はpause対象とBucket観察の母体であり、提案相手は別ユーザーから用意する。supervisorレビュー指摘・計画書で配置を具体化)→一方をpause→`staging-clock advance`で対象開始時刻のBucketを経過させ、Bucket再評価ジョブに処理させる機会を通す→resume→version+1のupdate Event発行→**Embedding Workerを経ないこと**(embedding列・embedding_model不変をDB照合、Embedding実行ログなし)でCandidate Retrievalから再評価→提案が発生し得る状態への復帰を確認

**(b) Bucket到来Eventの欠落とcatch-up回収**: 「欠落」の作り方 — worker1/2をdocker stop→その間に`staging-clock advance`で30分Bucket時刻を通過させる(**Bucket到来Eventの発行者はworker内のジョブのため、停止中にはEventが発行されない=欠落が成立する**)→worker再開→catch-upスキャン(60秒周期・Event発行せずDB直接投入)が「active+expires_atまで2時間以内+直近詳細評価から30分以上経過」のIntentを拾い再評価・提案発生まで到達することを確認

- **embedding IS NULLの除外確認**: embedding NULLのIntentをDB直UPDATEで1件用意し、catch-upの対象抽出から除外されることをDB/ログで観察する。**backfill.pyがNULL行を拾うため観察順序が要る**(NULL化→catch-up観察→backfillへ戻す。承認事項§5-7)。「embedding済みIntentが対象に含まれる」は同一窓のembedding済み行の再評価記録で対で証明する

### 2.5 Queue障害 — publish --raw-payloadで注入、lag alert発報器はAPI側に新設(案A・推奨)

- **毒ペイロード**: `make staging-publish ARGS="publish created --intent-id <uuid> --raw-payload '<構造違反JSON>'"`(既存経路・追加コード不要)→workerが5回再試行→match_events.status=quarantined+失敗理由保持をDB確認→**滞留の非波及**として、毒の後に正常Eventを投入し提案まで到達することを確認
- **削除済み参照Event**: Intent作成→削除→同一ID・同一versionで再publish→**quarantinedでなくprocessed**で閉じる(破棄理由のpayload記録)ことを確認
- **UNIQUE重複排除**: 同一Eventを2回投入→match_candidatesが二重生成しない(M2 ws-8でci環境試験済みだが、10 §4.7は「あわせて確認」とstaging試験に同梱するよう要求するためstagingで1本にまとめて実施)
- **Queue滞留とlag alert**: 滞留はworker1/2停止+Event連続投入で作る(投入は既存手段・少量で足りる)。発報器の置き場所が本設計の判断点:
  - **案A: API側へ薄い発報器(obs/lag_alert.py)を新設(推奨)** — FastAPIのlifespanで60秒周期タスクを起動し、DBを直接SELECT(match_events pending件数・最古pending年齢)して閾値超過で構造化ログ(latch.pipeline.alert)を発報。**worker停止中も発報が続く**(滞留作成と矛盾しない)。閾値はenv設定可(LATCH_ALERT_QUEUE_PENDING/LATCH_ALERT_QUEUE_OLDEST_SECONDS・初期値は承認事項§5-5)。発報は条件成立中1回だけとし、条件解除をresolveログとして記録(発報→解除の往復を証拠化・D-16 alertと同型の規律)。**api1/api2の両インスタンスでlifespanタスクが走るため、単発化はプロセス内のフラグだけでなくRedisのアームキー(SET NX+TTL)でインスタンス間でも担保する**(supervisorレビュー指摘・計画書で具体化)
  - **案B: workerのcollectorへ判定を追加** — collector.docstringの「このgaugeを消費する」に最も近いが、**worker停止で滞留を作る試験と発報器が一緒に止まる矛盾**。滞留中の発報が不可能
  - **案C: alertmanager導入** — Prometheus観察経路の本道だが新規依存・G4の試験単位としては重い(YAGNI)。媒体差し替えはobs/metrics.pyと同じ差分面(承認事項2)の発展としてPhase 0以降に回せる
- 推奨理由: 04 §5の「本書は検知と制御の経路を確定する・媒体は運用設計に委ねる」の規律と、D-16 alertのci段階実体が構造化ログ(supervisor承認済み)である既存慣行に沿う。docstringの「このgaugeを消費する」は同じDB集計値を消費する意と読む(G4時確認事項候補)

## 3. ファイル構成

### 3.1 作るもの

```
backend/src/latch/loadgen/
  probe.py                  新設: 合成監視プローブ本体(集計・YAML出力・タグ)
  __main__.py               追記: probeサブコマンド
backend/src/latch/obs/
  lag_alert.py              新設: Queue lag alert発報器(判定ロジック+周期タスク)
backend/src/latch/nfrtools/
  __init__.py               新設: staging非機能試験運用CLIパッケージ
  d16.py                    新設: D-16カウンタ操作(set/get/delete・Clock導出キー)
backend/src/latch/main.py   追記: lifespanへlag_alertタスク登録
backend/pyproject.toml      追記: nfrtoolsのエントリ(latch.nfrtools CLI)
backend/staging_tests_nfr/
  conftest.py               新設: staging_tests/conftest.pyの型を踏襲(証拠YAML出力・
                            clock操作・DB SELECT読取・Redis操作ヘルパー)
  test_n1_availability.py   新設: プローブ結果YAMLの検証(成功率99.5%・タグ除外・自動復旧時刻)
  test_n2_backup.py         新設: cross-check(active Intent数前後一致・スモーク)
  test_n3_d16.py            新設: D-16(alert発報・上限・リセット復帰・月次独立性)
  test_n4_reeval.py         新設: resume・catch-up(embedding列不変・除外観察)
  test_n5_queue.py          新設: 毒・参照不在・重複・非波及・lag alert発報/解除
Makefile                    追記: staging-nfr-test・staging-probe・staging-d16・
                            staging-dump・staging-restore・staging-db2-up/down
compose.staging.recovery.yaml  新設: 復旧試験用のDB向き先差し替え(override。
                            compose.staging.yaml自体は触らない)
```

### 3.2 触らないもの

- compose.staging.yaml・compose.load.yaml・compose.yaml(ci)・compose.dashboard.yaml — 環境資産の既定値を変えない(復旧試験の向き先差し替えはoverrideファイルで完結)
- docker/nginx/staging.conf — ws-1で確定済み(proxy_next_upstream含む)
- 既存staging_tests/・staging_tests_obs/ — 変更せず、型だけ踏襲
- tests/配下の既存試験・obs/metrics.pyの既存メトリクス — 変更しない(lag alert判定はDB直SELECTで足り、新gaugeは不要)
- worker本体の処理ロジック(catch-up・quarantine・D-16・backfill) — 本単位で改修する仕様上の欠陥は想定していない。試験で欠陥が見つかった場合は実装時確認事項として持ち帰る

## 4. テスト方針

### 4.1 unit(`make test`・外部プロセス不要)

- **lag_alert**: 閾値超過で1回発報・条件解除でresolveログ+再武装・Clock導出(JST日次想定なし・年齢はClock差分)。閾値のenv上書き
- **probe集計**: 成功率計算(計画停止タグ付きサンプルの分母除外)・連続失敗の記録・タイムアウト扱い
- **d16**: キー名導出(day/monthをClockから・prefix対応)。set/get/delete
- いずれもFakeClock・fakeredis使用(既存慣行どおり)

### 4.2 環境実行(`make staging-nfr-test`・staging-reset直後のフル起動前提)

- 実行順序は環境を共有するため**固定手順とする**: n5(Queue障害・滞留込み)→n4(再評価)→n3(D-16)→n2(バックアップ復旧。環境の最後に実施し、db2へ切替えたまま既定へ戻す手順を明記)→n1(可用性・時間試験のため最後)
- 証拠は10 §5形式(試験ID・実施日・環境・入力条件・合否)でdocs/testassets/results/ws7-nfr-result-*.yamlへ
- **可用性(n1)だけは60分級の時間試験**のため、ハーネスは「プローブ出力YAMLの検証」を担い、プローブ実行自体は`make staging-probe`(バックグラウンド起動・試験窓指定)と手順書(§4.3)で行う

### 4.3 実施手順(計画書へ明記・スーパーバイザー検証用)

1. `make staging-reset`→シード投入(n1用プローブ対象を含む)→ci一時停止(運用ルール6)
2. n5→n4→n3をハーネスで実施(worker停止・Clock操作を含むため直列)
3. n2(バックアップ復旧)をMakefile手順+ハーネス検証で実施
4. n1(可用性)を手順書どおり実施: プローブ開始→安定15分→docker kill api1→復旧観察→api2計画停止(タグ確認)→試験窓終了→`staging-nfr-test test_n1`で検証
5. 検証チェーンはパイプを書かない・各段で件数実査(ws-6教訓)

## 5. supervisor承認事項(草案 — 承認後に計画書化)

1. **可用性の試験窓=60分・プローブ1分間隔**(10 §4.3に期間規定なし。サンプル60個)。failover精度観察用の10秒間隔密プローブを併設(合否判定外)
2. **成功率の定義**=成功プローブ数÷総プローブ数・計画停止タグ付きサンプルは分母除外。api2の意図的stop→startを試験内に1回行い、タグ除外の動作確認を兼ねる
3. **バックアップ「定期バックアップ1サイクル=pg_dump1回」の読み替え**(10 §4.4は手順試験が本体・本番側のバックアップ運用はws-8ロールバック手順書とPhase 0)。復元後のembedding NULL化を「識別子で対象特定→再エンベディング」の実施形として採用(01 §18の記録特定を検証対象に含める)
4. **D-16カウンタの直接セット**(staging-d16小CLI)を採用。実Eventは境界通過の1〜2件のみ(§2.3案A)
5. **lag alert発報器をAPI側に新設**し、閾値初期値=「pending件数≧1,000 **または** 最古pending年齢≧300秒」(OR・env設定可)。発報は構造化ログ(latch.pipeline.alert)+解除(resolve)ログ。数値はdocs未規定の初期値であり、計測を見て調整する(06第9節末尾の規律と同型)
6. **circuit breakerのRedis共有(M2 ws-8引継ぎ「Worker複数構成確定時」)は本単位のスコープ外**とする。実装変更を伴う構成対応であり、単位表の縮退スコープ(D-16・resume/catch-up)に含まれない。staging worker2台でbreaker状態がプロセス独立である点はG4時確認事項として記録
7. **catch-up観察中のbackfill競合は観察順序で回避**(embedding NULL化→catch-up観察→backfill復帰。実装変更なし)
8. **n2復旧試験のenv差し替えはcompose overrideファイル**(compose.staging.recovery.yaml)とし、既定環境は触らない
9. **メトリクスbucket上限10秒の拡張(ws-6 G4確認事項7)は本単位では実施しない**。縮退時のレイテンシ分布観察を要件化するかの判断はG4まで保留のままとし、本単位ではD-16縮退時のpending推移の観察(§2.3)とログ証拠で代替する

## 6. 未解決の論点 — なし(承認事項5の集約どおり)

docs内で解決済みの点と、承認事項に置いた読み替え・初期値(§5-1〜8)以外に、
本単位の設計を阻む論点はない。実装時に欠陥が見つかった場合は実装時確認事項
(ws-6 report §9.1と同型)として持ち帰る。
