# Lab 15 ダッシュボードを立てる: システムが数えた数を、外から読む

- 種別: チュートリアル(手を動かして必ず成功体験を得る。ci環境には触れません)
- 前提知識: 第29章(計測の設計)・第25章とLab 11(staging環境と共有時計)。
  Lab 14までにstagingを立てた経験があれば、操作の流れは見覚えのあるものばかりです
- 所要目安: **30〜45分**。うち `make staging-reset` が約6〜7分・
  `make dashboard-up` が初回数分(イメージの取得)。テストと同じく、stubで動くため
  お金はかかりません
- このLabでできるようになること: stagingの上にダッシュボード環境
  (Prometheus+Grafana・`latch-dashboard`プロジェクト)を立てて、
  ①システムの数の窓口(`/metrics`)を覗き、②叩いたリクエストが数として
  増えるのをPrometheus経由で確認し、③時計を1日進めて日次集計
  (KpiJob)がkpi_daily表に値を書くのを見て、④Grafanaの4枚の板が
  provisioningで勝手に置かれるのを確認できる
- 次に読むもの: Lab 14をまだ回していない人は、**Lab 14は必ず
  `make staging-reset` から始めてください**(このLabは時計を1日進めます。
  進んだ時計の世界は、Lab 14の「きれいな世界」の前提ではありません)。
  片付けの節(§8)で、戻し方も書いておきます

## 0. このLabで何をするか

Lab 11であなたは、staging環境を立てて**時計**を動かしました。このLabでは、
同じ世界の上に**もう1つの世界**(ダッシュボード環境)を立てます。登場人物は4つです。

| 登場人物 | 何をするもの | 入り口 |
|---|---|---|
| staging | 動かされる側の世界(8コンテナ・Lab 11と同じ) | `make staging-reset` |
| latch-dashboard | 数を取りに来て、溜めて、描く世界(2コンテナ) | `make dashboard-up` |
| `/metrics` | 各コンテナが持つ「数の窓口」(第29章29.6節) | curl |
| KpiJob | 60秒ごとに集計し忘れを確認する集計ジョブ(29.8節) | (workerが勝手に動かす) |

stagingとlatch-dashboardは**別プロジェクト**です。コンテナ名(`latch-staging-`と
`latch-dashboard-`)もポートも分かれています。このLabで何をしても、
あなたが普段使っているci環境には触れません(Lab 11と同じ安心の仕組みです)。

以降、すべての手順をリポジトリのルート(`latch/`)で実行します。

## 1. 世界を立てる: staging-resetとdashboard-up

まずstagingを立てます。Lab 11やLab 14と同じ手順です(鍵が無ければ `make staging-keys`
を先に)。イメージが古いと新しいコードが動かない点もLab 14と同じで、
git pull等のあとにstagingを立てる場合は、先にbuildしてください:

```bash
docker compose -f compose.staging.yaml build api1 api2 worker1 worker2
make staging-reset
```

何が起きるか: stagingの8コンテナをデータごと作り直します(約6〜7分)。
**このLabではシード(Lab 12の300件)は不要です。**数える対象は、あなたが
このLabの中で叩くリクエストだけで足りるからです。

次にダッシュボード環境を立てます。

```bash
make dashboard-up
```

何が起きるか: `latch-dashboard`プロジェクトの2コンテナ
(prometheus・grafana)を立て、両方の健康診断が通るまで待ちます。
Grafanaのイメージ取得が初回は数分かかります。

```
 Container latch-dashboard-grafana-1 Healthy
 Container latch-dashboard-prometheus-1 Healthy
```

状態を確認しておきます。

```bash
make dashboard-ps
```

2行が並び、どちらも `(healthy)` なら大丈夫です。

## 2. Prometheusが取りに来ているのを見る

第29章29.7節で読んだ「pull型」の、目で見える証拠です。Prometheusは
15秒ごとに、stagingの中の4つの窓口へ取りに来ています。取りに行き先
(target)の健康状態を、Prometheus自身の窓口(127.0.0.1:19090)から
聞いてみましょう。

```bash
curl -s 'http://127.0.0.1:19090/api/v1/targets' | python3 -c "
import json,sys
d=json.load(sys.stdin)
for t in d['data']['activeTargets']:
    print(t['labels']['job'], t['scrapeUrl'], t['health'])
"
```

何が起きるか: PrometheusのAPI(target一覧)にJSONで尋ね、各行の
「どの窓口か」と「健康か」を1行ずつ表示します。`python3 -c "..."`
は「受け取ったJSONを整形して表示する」だけの小さなスクリプトです。

期待される出力(2026-10-04に実行したもの。順序は環境によります):

```
latch-staging-api http://api1:8000/metrics up
latch-staging-api http://api2:8000/metrics up
latch-staging-worker http://worker1:9100/metrics up
latch-staging-worker http://worker2:9100/metrics up
prometheus http://localhost:9090/metrics up
```

4つのstagingの窓口と、Prometheus自身を含む5行が並び、`health` が
すべて `up` です。同じapiなのにapi1とapi2が**別々の行**になっている点を
確認してください。第29章29.4節の「数える側は自分のプロセスの分だけを
正直に数える。集約は後で」が、この一覧の形に表れています。

なお、`dashboard-up` 直後は一時的に `unknown` が混ざります
(まだ1回も取りに行っていない窓口の値です)。15秒経っても `up` にならない
行があれば、`make dashboard-logs` で様子を見てください。

## 3. 数の窓口 /metrics を覗く

Prometheusが取りに来ている中身を、あなた自身も同じ窓口で覗いてみます。
api1直(127.0.0.1:18001・Lab 11と同じ窓口)に、`/metrics` というパスで
聞きます。

```bash
curl -s -o /dev/null -w "status: %{http_code}\n" http://127.0.0.1:18001/metrics
curl -s http://127.0.0.1:18001/metrics | rg "^latch_" | head -6
```

何が起きるか: 1行目はステータスコードだけを確認します。2行目は
`/metrics` の応答から、`latch_` で始まる行だけを `rg`(検索コマンド)で
取り出し、最初の6行を表示します。

期待される出力(数字は実行時点の累積で、環境により変わります):

```
status: 200
latch_http_requests_total{method="GET",path="/health",status="200"} 55.0
latch_http_requests_created{method="GET",path="/health",status="200"} 1.7910910807984207e+09
latch_http_request_duration_seconds_bucket{le="0.005",method="GET",path="/health"} 55.0
latch_http_request_duration_seconds_bucket{le="0.01",method="GET",path="/health"} 55.0
latch_http_request_duration_seconds_bucket{le="0.025",method="GET",path="/health"} 55.0
latch_http_request_duration_seconds_bucket{le="0.05",method="GET",path="/health"} 55.0
```

第29章29.6節で読んだテキスト形式が、実際に並んでいます。1行が
「ラベル付きの数」です。`latch_http_requests_total` の行に
`method="GET",path="/health"` というラベルが付いていて、ここまでの
リクエスト数が `55.0` と数えられています。

**ラベルに注目してください。**pathが `/v1/latches/7c9e...` のような
実際のURLではなく `/health` のような宣言の形(pathテンプレート)に
なっていること、user_idのような個人の情報がどこにも現れないことを、
自分の目で確かめてください(第29章29.5節の約束です)。

ここで1つ、worker側も覗いておきましょう。workerは計測専用の別ポート
(9100番・ホストの18101番から届く)を持っています。

```bash
curl -s http://127.0.0.1:18101/metrics | rg "^latch_(breaker_state|event_queue_pending|pipeline_candidates)"
```

期待される出力(数字は環境により変わります):

```
latch_event_queue_pending 0.0
latch_pipeline_candidates 0.0
latch_breaker_state 0.0
```

workerが60秒周期のcollectorで写している数(第29章29.9節)が並んでいます。
`breaker_state` が `0.0`、つまり回路遮断器が「閉じている=順調」を
表しています(第16章16.4の状態の話)。

## 4. 叩いた回数が、数になる

「数える」の実感が一番出る手順です。ヘルスチェックを3回叩いてから、
数が増えるのを確認します。

```bash
for i in 1 2 3; do curl -s -o /dev/null http://127.0.0.1:18001/health; done
echo "3回叩きました。Prometheusのscrape周期(15秒)を待ちます..."
sleep 20
curl -s 'http://127.0.0.1:19090/api/v1/query' --data-urlencode 'query=latch_http_requests_total{path="/health"}' | python3 -c "
import json,sys
d=json.load(sys.stdin)
for r in d['data']['result']:
    print(r['metric']['instance'], '=', r['value'][1])
"
```

何が起きるか: api1の `/health` を3回叩いたあと、Prometheusが次の
scrapeを終えるのを20秒待ち、`query` というAPIで「pathが/healthの
リクエスト数」を聞きます。

期待される出力(2026-10-04に実行したもの。数字は実行時点の累積です):

```
3回叩きました。Prometheusのscrape周期(15秒)を待ちます...
api2:8000 = 59
api1:8000 = 61
```

§3で `55.0` だったapi1の数が `61` に増えています(増分は3を超えます。
dockerの健康診断も同じ `/health` を叩いていて、それも数に入るからです)。
api2はあなたが叩いていないのに `59` と数えられています。api2自身の
健康診断とPrometheusのscrapeが数に入るためです。
Prometheusは「取りに来た瞬間の各プロセスの数」を溜めているので、
あなたが見るときには常に数分〜数秒前の値です。pull型のこの感覚、
覚えておいてください。

このAPIで聞いた問い(`latch_http_requests_total{path="/health"}`)は、
Grafanaの板が内部で聞いている問いと同じ形式です。Grafanaのパネルの
正体は「この問いの結果をどう描くか」の定義なのです。

## 5. 新しい窓口 POST /v1/sessions を叩く

第29章29.2節の「アクセス系計測」の入口です。Lab 11と同じ流儀で
トークンを発行し、ユーザー登録まで進めてから、新しい窓口を叩きます。

```bash
make staging-token SUBJECT=learn-lab15 | tail -1 > /tmp/lab15-idp.txt
head -c 30 /tmp/lab15-idp.txt; echo " ...(JWT)"
ACCESS=$(curl -s -X POST http://127.0.0.1:18000/v1/auth/token \
  -H "Content-Type: application/json" \
  -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab15-idp.txt)\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
echo "$ACCESS" > /tmp/lab15-access.txt
curl -s -o /dev/null -w "users: %{http_code}\n" -X POST http://127.0.0.1:18000/v1/users \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"display_name":"lab15","birth_date":"1990-04-01","profile":{}}'
curl -s -o /dev/null -w "sessions(bodyなし): %{http_code}\n" -X POST http://127.0.0.1:18000/v1/sessions \
  -H "Authorization: Bearer $ACCESS"
curl -s -w "\nsessions(bodyあり): %{http_code}\n" -X POST http://127.0.0.1:18000/v1/sessions \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" -d '{"x":1}'
```

何が起きるか: テストIdPの手形を発行し(1〜2行目)、アクセストークンと
交換して `/tmp/lab15-access.txt` に置き(3〜7行目)、ユーザー登録をし、
新しい窓口に**2回**叩きます。1回目はbodyを付けず、2回目は
`{"x":1}` という、契約に反するbodyを付けて。

期待される出力(2026-10-04に実行したもの):

```
eyJhbGciOiJSUzI1NiIsImtpZCI6In ...(JWT)
users: 201
sessions(bodyなし): 204
{"error":{"code":"VALIDATION_ERROR","message":"request body must be empty","details":null}}
sessions(bodyあり): 422
```

契約どおりの応答が2つ返りました。bodyなしの「セッション開始」は
`204`(成功・返す中身なし)、bodyありは `422` `VALIDATION_ERROR` で
断られます。第29章29.2節の「受け取れるのはuser_idと時刻だけ」を、
APIが構造的に守っている(余計なものを送ると断る)のが、この断られ方です。

この2つの応答が、数としてどう記録されたかを見ます。§4と同じ
Prometheusへの問い合わせで、pathを `/v1/sessions` に変えて聞きます。

```bash
sleep 16
curl -s 'http://127.0.0.1:19090/api/v1/query' --data-urlencode 'query=latch_http_requests_total{path="/v1/sessions"}' | python3 -c "
import json,sys
d=json.load(sys.stdin)
for r in d['data']['result']:
    m=r['metric']; print(m['method'], m['path'], m['status'], '=', r['value'][1])
"
```

期待される出力(2026-10-04に実行したもの):

```
POST /v1/sessions 204 = 1
POST /v1/sessions 422 = 1
```

同じ窓口への2回のリクエストが、**ステータスコードのラベル違いの
2つの行**として数えられています。204と422が混ざって合計2に
なっていない点に注目してください。「うまくいった数」と「断られた数」が
別々に見えるのは、ラベルが方法と結果を運んでいるからです(§3の
pathテンプレートと並ぶ、ラベル設計の実感です)。

## 6. 時計を1日進めて、毎日の集計を発火させる

いよいよこのLabの山場です。第29章29.8節のKpiJobは「60秒ごとに、
集計し忘れた日はあるか」を確認します。時計を1日進めれば、
JSTの日付境界を越えて、取り残しの日が生まれるはずです。

まず、時計の現状を確認してから進めます。

```bash
make staging-clock ARGS="show"
make staging-clock ARGS="advance 86400"
```

何が起きるか: `show` は共有時計の基準点の有無と現在値を表示し
(Lab 11と同じ)、`advance 86400` は**86400秒=ちょうど1日**進めます。
「1日」なら、今が何時であってもJST 0時を必ず1回越えるので、
日時を読み替える必要がありません(「翌日0時を指定する」Lab 11のset
と違い、advanceなら読み替え不要です)。

期待される出力(2026-10-04に実行したもの。時刻は実行時によります):

```
[clock] control: unset (real time)
[clock] virtual now: 2026-10-04T05:29:19.485647+00:00
[clock] advanced by 86400.0s -> 2026-10-05T05:29:19.674760+00:00
```

続く手順の前に、**予測を書いてください**(my-notes.mdへ)。60秒以内に
KpiJobが動いたとき、DBの `kpi_daily` 表には何が書かれると予想しますか?
・どの日の分が書かれるか(1日?それとも複数日?)・何種類の行が
書かれるか、の2点です。ヒントは第29章29.8節の「遡及4日の窓」と
「15個の指標」です。

では、90秒待ってから表を覗きます。

```bash
echo "KpiJobのtick(60秒周期)を待ちます..."
sleep 90
docker exec latch-staging-db-1 psql -U latch -d latch \
  -c "SELECT metric_key, day, numerator, denominator FROM kpi_daily ORDER BY day, metric_key;"
```

何が起きるか: workerのKpiJobが集計を終えるのを90秒待ち(60秒tickの
1回分+余裕)、stagingのDBコンテナの中でpsql(データベースにSQLで
問い合わせるコマンド道具・第1章1.7)を動かして `kpi_daily` の全行を
表示します。90秒は「確実に1回以上のtickを迎える」ための待ち時間で、
それ以上の意味はありません。

期待される出力の例(2026-10-04に実行したもの。**行数と日付は実行日と
実行内容により変わります**。全部で49行ありました):

```
        metric_key         |    day     | numerator | denominator
---------------------------+------------+-----------+-------------
 retention_30d             | 2026-09-03 |         0 |           0
 retention_30d             | 2026-09-04 |         0 |           0
 retention_7d              | 2026-09-26 |         0 |           0
 retention_7d              | 2026-09-27 |         0 |           0
 attendance_rate           | 2026-09-28 |         0 |           0
 cancel_rate               | 2026-09-28 |         0 |           0
 intent_recreation_rate    | 2026-09-28 |         0 |           0
 mute_rate                 | 2026-09-28 |         0 |           1
 no_answer_rate            | 2026-09-28 |         0 |           0
 active_intent_per_dau     | 2026-09-30 |         0 |           0
(中略。日次8指標が10-01〜10-04の4日分並ぶ)
 mlr                       | 2026-10-04 |         0 |           0
 proposal_rate             | 2026-10-04 |         0 |           0
 yes_rate                  | 2026-10-04 |         0 |           0
(49 rows)
```

あなたの予測と答え合わせをしましょう。私が実行したときは、こう読めました。

- **日次8指標の行**が、遡及4日の窓(時計上の10-01〜10-04)にそって
  4日分並ぶ。値はほとんど `0 | 0`(分子0・分母0)です。このLabでは
  シードを入れず、提案も作っていないので、何も起きていない日の
  集計とはこうなる、という正しい値です
- **週次5指標**(`intent_recreation_rate`・`attendance_rate`・
  `cancel_rate`・`no_answer_rate`・`mute_rate`)が
  `2026-09-28` の1行ずつ。時計上の「昨日」(10-04)が日曜だったので、
  その週の週次が書かれました。**日曜を越えて進めた週にだけ書かれる**
  のが週次の条件です
- **retentionの行**が、観測窓が閉じた日だけ。`retention_7d` は
  「登録から7日経った人の分」なので、窓が閉じた2日分しかありません
- `mute_rate` の `2026-09-28` 行だけ `denominator` が `1`。分母は
  「その週にセッションを開いた人の数」で、§5であなたが投げた
  1セッションが数えられています

値がゼロばかりなのに、これで「動いた」の証拠になるのは、
**「何も起きていない日」も確実に0/0の行として書かれる**のが
この集計の契約だからです(第29章29.8節の「代表キーmlrの行の
有無=その日の集計が完成したか」の話)。行が並ぶこと自体が、
60秒tickが遡及を回収したことの証明です。

もう1つ、細かい観察をどうぞ。この集計値は、ダッシュボード側の
数にも写っています(60秒周期のcollectorがkpi_dailyの最新行を
gaugeに写す・第29章29.8節の末尾)。

```bash
curl -s 'http://127.0.0.1:19090/api/v1/query' --data-urlencode 'query=latch_kpi_daily_numerator{metric="mlr"}' | python3 -c "
import json,sys
d=json.load(sys.stdin)
for r in d['data']['result']: print('kpi_daily_numerator(mlr) =', r['value'][1])
"
```

期待される出力:

```
kpi_daily_numerator(mlr) = 0
```

「毎日の正はDBに」「見せる写しはgaugeに」という第29章の構えが、
DBの表とgaugeの2か所で確かめられました。

## 7. Grafanaの板を開く

最後はGrafanaです。板(dashboard)は**手で作っていない**のに置かれて
いるはずです。provisioning(第29章29.7節)の確認です。まずAPIで
置かれている板の一覧を聞きます。

```bash
curl -s -u admin:admin http://127.0.0.1:13000/api/search | python3 -c "
import json,sys
for d in json.load(sys.stdin): print(d['type'], d['title'], d['uid'])
"
```

何が起きるか: Grafanaの検索APIに、初期ユーザー(admin・パスワードも
admin。compose.dashboard.yamlの環境変数で設定してあるもの)で
尋ね、置かれている板の一覧を1行ずつ表示します。

期待される出力(2026-10-04に実行したもの):

```
dash-folder LATCH ag06lfcflrk74e
dash-db LATCH Behavior / KPI latch-behavior
dash-db LATCH Jev latch-jev
dash-db LATCH Overview latch-overview
dash-db LATCH Pipeline latch-pipeline
```

4枚の板(`Behavior / KPI`・`Jev`・`Overview`・`Pipeline`)が
`LATCH` フォルダに置かれています。この4枚は、起動時に
`docker/grafana/dashboards/` のJSONファイルから読み込まれたものです。
あなたは1枚も作っていません。**定義がファイルだから、世界が
また作り直されても同じ板が置かれる**。これがprovisioningの実感です。

次に、ブラウザで `http://127.0.0.1:13000` を開いてください。
`admin` / `admin` でログインすると、左のメニューの
Dashboards → LATCH フォルダに4枚が並んでいます。**LATCH Overview**
を開いてみてください。§6のkpi関連パネルや、§4〜5であなたが
増やしたリクエスト数が、時間の折れ線として描かれています。
自分が叩いたcurlの回数がグラフの点になっているのを見ると、
「数える」の終わりから「見る」の始まりまでが、1本の線でつながるはずです。

## 8. 片付けと、次のLabへの注意

使った世界を順に下げます。

```bash
make dashboard-down
make staging-down
```

何が起きるか: ダッシュボード環境(latch-dashboard)とstaging環境
(latch-staging)を順に停止します。stagingのデータ置き場(ボリューム)は
残るので、次に `make staging-up` したときは「時計が1日進んだ・
セッションが1件ある」世界から再開します。

**Lab 14(またはもう1回のフル実行)をあとで回す人は、必ず
`make staging-reset` からやり直してください。**このLabは時計を
1日進め、テストユーザーとセッションを1件作りました。「reset直後の
きれいな世界」の前提は、もう満たされていません(Lab 14の§0に書いた
注意どおりです)。

もし Prometheus の画面で「target がずっと down のまま」になることが
あったら、第1章1.11の注意(起動済みコンテナは古いイメージのまま、という
話)を思い出してください。stagingを立て直すときは、buildを挟むのが
確実です(§1で書いたとおり)。

## 9. このLabで触れたものの、対応表

学んだ部品と、このLabで触った窓口の対応を、ノートに写して
おくと役立ちます。

| 第29章の節 | 部品 | このLabで触った窓口 |
|---|---|---|
| 29.4 | obs/metrics.py(定義の単一真実) | `/metrics` の応答に並ぶ数 |
| 29.6 | middleware・`/metrics` | 127.0.0.1:18001/metrics |
| 29.2・29.6 | POST /v1/sessions | 127.0.0.1:18000/v1/sessions |
| 29.7 | Prometheus(pull型) | 127.0.0.1:19090 のtargets・query |
| 29.7 | Grafana・provisioning | 127.0.0.1:13000 の4枚の板 |
| 29.8 | KpiJob・kpi_daily | staging-clock advance → psql |
| 29.9 | collector(60秒) | 127.0.0.1:18101/metrics のgauge |

どの数がどの窓口から来るかを口で説明できるようになったら、
このLabは合格です。分からなくなったら、第29章の該当節へ戻って
ください。自分のノート(`my-notes.md`)に、予測と答え合わせの結果も
書き残しておきましょう。
