# 第29章 システムが自分を数える: 計測の設計と毎日の値の残し方

- 種別: 解説(通読して理解を積む章)
- 前提知識: 第28章までの読了を前提にします。特に第9章(match_events)・第16章
  (circuit breaker)・第25章(staging環境と`ControlledClock`)。忘れた概念は、
  本文に都度「戻る案内」を置くので、そこへ戻れば読み続けられます
- この章を読み終えるとできるようになること:
  - 「計測3群+アクセス系」に何が入るか、docsの定義どおりに言える
  - メトリクスの3つの形(Counter・Gauge・Histogram)を、実際のコードと照らして
    使い分けられる
  - メトリクスのラベルにuser_idを載せない約束が、どんな事故を防ぐのか説明できる
  - Prometheusが「送りつけられる」のではなく「取りに来る」方式(pull型)である
    理由を語れる
  - kpi_dailyという表が、比率を保存せず分子と分母を保存する理由・JST 0時の
    検査を60秒tickで回す理由・4日遡及する理由を説明できる
  - 厳密に数えられない値を「近似」と正直に書き残す設計文化を、実例で語れる
- 対応コード: `backend/src/latch/obs/`(metrics.py・middleware.py・sessions.py・
  collector.py・kpi.py)・`backend/alembic/versions/0008_obs_sessions_kpi.py`・
  `backend/src/latch/main.py`・`backend/src/latch/worker/main.py`・
  `compose.dashboard.yaml`・`docker/prometheus/prometheus.yml`・
  `docker/grafana/dashboards/`(4枚のダッシュボード定義)
- 設計の根拠: `docs/plans/M4/ws-4-design.md`(選択肢と承認記録)・
  `ws-4-report.md`(実行結果と読み替え記録)。仕様の元は
  `docs/04-system-architecture.md`第2節・第7節(3群の定義)と
  `docs/09-verification-evaluation.md`第2.1節・第2.2節(KPIの定義)です
- 次に読むもの: `labs/lab15-dashboard.md`(Lab 15)。この章で読んだ部品を、
  stagingの上で自分の手で動かします

## 29.1 この章の題材: 動いているシステムの内側を、外から知るには

ここまでの章で、あなたはLATCHの主要な部品を一通り読んできました。Intentが保存され、
知らせが運ばれ、マッチングが進み、提案が届き、返事で閉じる。しかし一つ疑問が残ります。
**動いている最中のシステムの内側で何が起きているかを、外にいるあなたはどうやって知るのか。**

画面を見ても分かりません。データベースの行数を数えても、それは「結果」であって
「今どう動いているか」ではありません。提案が1件も出ていない朝、問題は
「提案が出ていない」という結果からでは遅すぎて、その手前——例えばAIの評価を
呼ぶ処理が遅くなっている——をつかみたいはずです。

答えは、システム自身が自分の様子を数値として数え続け、それを1か所から
読める形で置いておく、です。この仕事を **計測**(けいそく)と呼び、
数えた値のことを **メトリクス** と呼びます。M4 ws-4(2026-10-04マージ)の
仕事は、この計測の土台をLATCHに組み込むことでした。この章で読むのは、
「数を数える」という単純な仕事に、なぜこれほど多くの設計判断があるのかです。

先に結論を並べると、この単位で作られたものは4つです。

1. 数の定義を全部集めた1つのファイル(`obs/metrics.py`)
2. リクエストを自動で数える仕組み(middleware)と、数を読む窓口(`/metrics`)
3. PrometheusとGrafanaという2つの道具を立てる環境(`latch-dashboard`プロジェクト)
4. 「毎日の値」を表に書き残す集計ジョブ(KpiJob)と、その表(kpi_daily)

この章の残りで、順に読みます。手を動かすのは次のLab 15です。

## 29.2 数える対象は4つに分かれる: 計測3群とアクセス系

「全部を数える」は答えになりません。数えられる値は無限にあるので、何を数えるかを
先に決めないと、どこにも届かない大量の数だけが溜まります。docs
04-system-architecture.md第7節は、LATCHが数えるべきものを次の3つの群(ぐん)に
分けています。

- **パイプライン群**: Intentが保存されてから候補が並ぶまでの流れの数。
  Event Queueのたまり具合・候補の件数・Hard Filterを通った割合・
  ベクトル検索で取れた件数
- **Jev群**: AIに評価を頼む処理(Jev・第13章)の数。実行回数・かかった時間・
  使ったトークン量
- **提案と行動群**: 提案が確定するときの点数の分布・YES率・成立率(MLR)・
  実際に会いに行った率・キャンセル率

第4群として、M4で **アクセス系基本計測** が加わりました。これは
「アプリを開いた」という出来事を数えるものです。ユーザーがシステムを
訪れた回数(日ごとの訪問者数=DAU)と、また来てくれた率(再来訪)が、
あとの章で使う各種の「率」の分母になります。実装は `POST /v1/sessions`
という新しい窓口で、受け取れるのはuser_idと時刻だけです。

さらに、この単位で**全APIのリクエスト数と応答時間**を数える仕組みが
3群への拡張として足されました(29.6節)。この基盤は、後の単位(M4 ws-6)で
「APIの応答時間の95パーセント点(p95)が500ミリ秒以内」という性能の約束を
実際に測るときの器になりました(第30章)。

この「3群+アクセス系」という切り分けの意図は、**数の消費者から考える**ことです。
パイプライン群は「流れが詰まっていないか」を見る人の数、Jev群は「お金と時間が
どれだけかかっているか」を見る人の数、提案と行動群は「このサービスは約束どおりの
結果を出せているか」を見る人の数です。数える側の都合でなく、読む側の問いに
合わせて群を作っている点が、この切り分けの肝心なところです。

## 29.3 数は3つの形しかない: Counter・Gauge・Histogram

計測の世界では、扱う数はたった3種類の形に分かれます。LATCHが使う
prometheus-clientというライブラリ(公式のPython用ライブラリ)も、この3種類を
提供します。3種類を「いつ・どう値が動くか」で区別してください。

**1つ目はCounter(カウンター、ただ増やし続ける数)** です。Jevの実行回数や
リクエスト数がこれです。減ることはなく、増えるだけです。再起動すると0に戻る点は
覚えておいてください(戻っても困らないように集計側が工夫します。29.9節)。

**2つ目はGauge(ゲージ、今どれくらいかの値)** です。いま詰まっている
知らせの件数・いまDBにある候補の件数がこれです。増えも減りもします。
「いまの瞬間の値」を上書きし続ける温度計の針だと思ってください。

**3つ目はHistogram(ヒストグラム、値のばらつきの分布)** です。Jevに
かかった時間・APIの応答時間がこれです。「平均0.8秒」だけでは
「いつも0.8秒」なのか「9割が0.1秒で残りが10秒」なのか分からないからです。
ヒストグラムは、観測した値をあらかじめ決めた区間に振り分けて数えます。
かかった時間をそのまま記録するのでなく「0.5秒以下が何回・1秒以下が何回…」と
区間ごとの回数を数える仕組みです。

`obs/metrics.py`では、この3種が`Metrics`という名簿にまとめて宣言されています。
名簿の各行が、このシステムが持つ数の一覧です。

```python
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
    pipeline_layer_seconds: Histogram
    latch_initial_e2e_seconds: Histogram
    notification_latency_seconds: Histogram
```

(`backend/src/latch/obs/metrics.py` 52〜73行。19個の数が宣言されています。
下の3本のヒストグラムはM4 ws-6で足されたもので、処理の各区間の時間の分布を
観測します。何のために必要になったかは第30章で読みます)

それぞれがどの群に属するかを見てみましょう。`event_queue_pending`と
`event_queue_oldest_pending_seconds`と`pipeline_candidates`と
`hardfilter_*`と`retrieval_vector_items`がパイプライン群。`jev_*`の3つが
Jev群。`score`と`kpi_daily_*`のgaugeが提案と行動群の入口です。
`breaker_state`(第16章の回路遮断器の状態)は、動作確認の見える化用の
おまけとして1つだけ足されています。閉じている=0・開いている=1・
半開き=2という数字に置き換えた「いまの状態」をgaugeに写します。

「名簿に宣言してから使う」には実利があります。試験がこの名簿を**機械的に
列挙して、許可リストと突き合わせる**からです。意図せず数を増やしたら、
試験が「この数は誰の承認もない」と赤で教えてくれます。第27章で読んだ
ログ許可リスト(logallow)と同じ発想の、名簿による守りです。

## 29.4 定義は1つのファイルに: obs/metrics.pyという単一真実

「数の定義があちこちに散らばる」ことほど、あとでつらいことはありません。
API側とWorker側の両方で使う数なのに、定義が2か所にあると、片方だけ
区間が変わって、ダッシュボードの足し算が静かに壊れます。

LATCHでは、**全部のメトリクスの定義を `obs/metrics.py` の1ファイルに集める**
という約束を設計の段階で決めました。設計メモ(`ws-4-design.md` §2.1)では
これを「単一真実」と呼び、この判断にはもう一つ実利があります。
第29章の後半で述べる本番の計測基盤と、この単位で作るローカルの計測基盤は
**別の道具**です。定義が1か所にあれば、道具を取り替える仕事も1か所に
収まります。

もう一つ押さえたい構造が、数の「実体」です。`metrics.py`の末尾にこうあります。

```python
# モジュール単例(本番)。import時に1回のみ構築する(複数回はduplicate
# registration例外になるため、本モジュールは再importで再構築しない)
metrics = build_metrics()
```

(`backend/src/latch/obs/metrics.py` 161〜163行)

「モジュール単例(たんれい)」とは、このモジュールが読み込まれたときに
1個だけ作られ、あとは全員がそれを共有する、という作り方です(第2章のDIと
対になる考え方です。作り直さず共有する)。ここで大事な視点があります。
apiのプロセスとworkerのプロセスは**別のプログラム**なので、それぞれが
自分用のカウンタを持ちます。プロセスをまたいで足し算する仕事は数を
集める側(29.7節のPrometheus)が担当します。「数える側は自分のプロセスの
分だけを正直に数える。集約は後で」という役割分担がpull型の考え方です。

試験はこの単例をそのまま使いません。`build_metrics()`に専用の名簿を渡して
独立した数の置き場を作り、その値が期待どおり動くかを確かめます。本番と
試験で「数の置き場」が混ざらないのは、第3章のClock注入と同じ発想です。

## 29.5 ラベルには人の情報を載せない: 数え方の約束

各メトリクスには、**ラベル**(label)と呼ばれる付箋をつけられます。
「どのAI事業者への呼び出しか」を区別したいなら`provider`ラベル、
「どの窓口へのリクエストか」を区別したいなら`path`ラベル、という具合です。

ここに、この単位で最も明確に決まった約束があります。docs
08-privacy-safety.md第2.4節(第27章で読んだ、構造化ログの許可リストの節)と
同じ規律です。

> ラベルはmethod・pathテンプレート・status・providerのような
> **システム側の属性のみ**。user_id・クエリ・body・Intentの原文・
> 座標は載せない

なぜこれが重要か。メトリクスは**誰でも・繰り返し・見るもの**だからです。
認証のない`/metrics`窓口(29.6節)から読める値に、誰かのuser_idや
Intentの文言が混ざっていたら、計測の仕組みがそのまま情報の出口に
なってしまいます。だから「数を数えるときに、人の情報を付箋に書かない」という
約束を、コードの構造(ラベル名の宣言)で固定しました。フロントエンドの
`expiry-options`で見た「1か所の正しい実装に縛る」発想(第8章)と、
通知の文面を`type`しか受け取れない関数に縛ったFR-21(第19章)と、
まったく同じ筋の守りです。

`/metrics`に触れるのは Prometheus 1本、という使い方をしても、構造的な
約束は壊れません。「今は1人しか見ない」は運用の話で、しかも変わる可能性が
あります。契約として守るべき情報の守りは、運用に依存しない形(ここでは
ラベル名の宣言と、それを試験する名簿の試験)で残す。第27章の
「使っていい条件を形で固定する」作法の、計測版と読めます。

## 29.6 リクエストは自動で数える: middlewareと/metrics

「全APIのリクエスト数と応答時間を数える」を、各窓口(ルート関数)に
1行ずつ書いていく方式を想像してください。窓口が30個になると30か所に
同じコードが並び、1か所の書き忘れが静かな穴になります。第7章で
「ミドルウェアではなく依存関数でレート制限をかけた」話を覚えていますか。
あそこでは「認証を内包する依存にすれば、401→429の順が構造で保証される」
という理由で、全リクエストの一括処理を避けました。

計測では逆に、**全リクエストを一括で掴む仕組み(middleware)を積極的に
使います**。数え忘れては意味がないからです。`obs/middleware.py`は
こうなっています。

```python
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
        metrics.http_request_duration.labels(method=method, path=path).observe(elapsed)
        return response
```

(`backend/src/latch/obs/middleware.py` 22〜41行)

読みどころは3つです。1つ目は`path`の作り方。`request`そのものではなく、
`route`の`path`を取りに行っています。これが「pathテンプレート」です。
`/v1/latches/7c9e...`という**実際の値**を含むURLを数えたら、ラベルが
URLの種類だけ無限に増えます。そこで「リクエストがどの宣言に
一致したか」の宣言側の形(`/v1/latches/{latch_id}`)を数えることで、
ラベルの種類が有限に保たれます。ラベルの種類が無限に増えるのは、
メトリクスの世界では実害を生む設計の禁じ手です。先の約束と並んで、
ここは数え方の作法として覚えておいてください。

2つ目は`/metrics`自身のアクセスを数えない最初の分岐。この窓口は
Prometheusが15秒ごとに叩き続けます。これを数えると「数を取りに来る
アクセスで、数が増える」という自己増殖が起きます。

3つ目は時間の測り方。`time.time()`でなく`clock.now()`を使っています。
第3章以来の規律(テスト可能にするため、時刻はClockから取る)が、
計測のコードにもそのまま効いています。

`main.py`への組み込みは3行の並びです。

```python
    app.include_router(obs_sessions_router)

    # HTTP基本メトリクス(M4 ws-4・design §2.9)。/metricsの自己アクセス
    # はmiddleware内で除外される
    app.add_middleware(HTTPMetricsMiddleware)
```

(`backend/src/latch/main.py` 266〜270行。この直後に`/metrics`窓口の
本体がある)

では、数えた値はどこから読むのか。答えが `/metrics` 窓口です。
`main.py`の`metrics_endpoint`(279行目)が、ライブラリに「数の置き場の
中身をテキストで吐き出して」と頼むだけで実装されています。テキストの
形式は決まっていて、最初に見たときは少し驚くかもしれません。

```
# HELP latch_http_requests_total Total HTTP requests by method, path template and status
# TYPE latch_http_requests_total counter
latch_http_requests_total{method="GET",path="/health",status="200"} 5.0
latch_http_requests_total{method="POST",path="/v1/sessions",status="204"} 3.0
latch_event_queue_pending 0.0
```

(期待される出力の例。Lab 15で実際に見ます。先頭が`#`の2行は説明と種別、
それ以外が「ラベル付きの数」です)

この窓口には注意が2つあります。1つ目は**認証がない**こと。設計で
承認済みの判断で、理由は「Prometheusが機械的に取りに来る窓口であり、
それに認証を通すには道具側の追加設定が要る」です。代わりに
**127.0.0.1バインド**という守りを入れました。これは「自分のPCの内側からだけ
届く窓口」にする、という意味です(1.11の第1章で見た`127.0.0.1`は
「自分自身」を指すアドレスでした)。この窓口を開ける
コンテナのポートはすべて`127.0.0.1:…`の形で宣言され、他のPCの
同じアドレスに届きません。試験はこの宣言そのものを検査する
(`test_obs_compose_pins.py`)うえ、staging受け入れ試験では**実際に
別のPCから届かないこと**を接続して確かめています(ws4-c5)。
「宣言と実動作の両方を試す」は、受け入れ試験らしさです。

2つ目はレート制限(第7章)を付けないこと。こちらも認証と同じ理由です。
15秒に1回、機械が叩きに来る窓口に人間向けの回数制限は不要で、
付けると本来の使い方を壊します。「v1 APIの契約の外の窓口」という位置づけは、
`/health`と同じです。

## 29.7 Prometheusが取りに来る: pull型の方式と、ダッシュボードの器

数が置いてあっても、人間が`/metrics`を時々覗いてグラフを描いていたら
仕事になりません。ここで登場するのが2つの道具です。

**Prometheus(プロメテウス)** は、メトリクスを集めて溜め込み、問い合わせ
(足し算・割り算・絞り込み)できるようにする専門のサーバーです。
特徴的なのは集め方です。Prometheusは**自分から各システムの`/metrics`へ
取りに来ます**。この方式を **pull型**(プルがた。引っ張って取る)と呼び、
反対に各システムが送りつける方式はpush型と呼びます。

pull型が採用された理由は設計メモに3つ書かれています。要約すると、
(a)「ローカルの計測はOSSツールで代替」という方針に最も忠実な最小構成である、
(b)後の単位でやる性能測定は「負荷をかけたまま、何分も連続で観察する」
仕事で、取りに来る方式とヒストグラムの組み合わせが向いている、
(c)将来本番の基盤へ差し替えるときの工事範囲が`obs/metrics.py`の
1ファイルに閉じている。docs 04の本番の確定値はクラウドの
監視サービス(Cloud Monitoring+OTLP)ですが、これはM4の開発用の
代替構成で、**本番の決定を書き換えるものではありません**。docsの確定値と
実装の代替構成の差分は、ゲート時の確認事項として記録されています。
この「仕様は変えない・代替は承認して記録する」手順の踏襲も、
この単位の学びどころです。

**Grafana(グラファナ)** は、溜めた数をグラフの**ダッシュボード**
(一望できる板)に仕立てる道具です。Prometheusに溜まった数を
問い合わせて、折れ線グラフや数値パネルに描きます。

この2つは、既存の環境と混ぜず、**別プロジェクト** `latch-dashboard`
として立てます。composeファイルの冒頭に目的が書いてあります。

```yaml
# ダッシュボード環境(M4 ws-4)。別プロジェクトlatch-dashboard。
# Prometheusがstagingのメトリクスをscrapeし、Grafanaへdatasourceを
# provisioningする。
name: latch-dashboard
```

(`compose.dashboard.yaml` 1〜5行。実行コマンドは`make dashboard-up`)

Prometheusが「どこから取りに行くか」の設定は
`docker/prometheus/prometheus.yml`にあります。

```yaml
global:
  scrape_interval: 15s

# targetはstagingのcomposeネットワーク内のコンテナ名直結
# (compose.dashboard.yamlのstaging-obsネットワーク参加を参照。apiコンテナ内
# ポート8000・worker内ポート9100)。load環境(28000系)はload-別名でscrapeする
# (ws-6・compose.dashboard.yamlのload-obsネットワーク参加とcompose.load.yamlの
# aliases参照 — 同一ネットワーク越しの同名コンテナのDNSあいまい解決を避ける)
scrape_configs:
  - job_name: latch-staging-api
    static_configs:
      - targets:
          - "api1:8000"
          - "api2:8000"
  - job_name: latch-staging-worker
    static_configs:
      - targets:
          - "worker1:9100"
          - "worker2:9100"
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
  - job_name: prometheus
    static_configs:
      - targets:
          - "localhost:9090"
```

(`docker/prometheus/prometheus.yml` 1〜33行。下の2本のload系jobは
M4 ws-6で足されたもので、load環境(第30章)の数を別のjobラベルで
分けて取りに行きます)

「15秒ごとに、stagingのapi1とapi2の8000番、worker1とworker2の9100番
(さらにload環境のload-api1/2・load-worker1/2)へ取りに行く」と読めます。apiは8000番(アプリの窓口`/metrics`は
その中)、workerは9100番(計測専用の別ポート)です。worker側に
専用ポートがあるのは、workerがHTTPサーバーを持たない裏方だからです
(第9章)。設定は `LATCH_OBS_WORKER_METRICS_PORT` という環境変数で
切り替わり、既定は0=無効。つまり**ci環境のworkerは計測ポートを
開かない**。環境ごとに必要な仕事だけが動く、settingsの作法(第25章で
judgment値をenvで固定した話)と同じ筋です。

もう1つ、実は悩みのあった点があります。Prometheusはstagingの外側
(別プロジェクト)にいるので、「どうやってstagingの中のコンテナへ
届くか」という問題が起きます。最初の設計は「ホストの公開ポート経由」
でしたが、実測で届きませんでした(届かない理由の技術的な詳細は
`ws-4-report.md`の§3-5にあります)。最終案は、Prometheusをstagingの
通信路(composeが作るネットワーク)に**参加させて、コンテナ名で
直接叩く**方式です。「設計どおりに作ったら実測で動かない。動かない
ものを直す判断は、実測と証拠で記録する」——これはこのリポジトリで
何度も見てきた手順そのものです。

最後にGrafanaの設定です。Grafanaは「どのPrometheusに繋ぐか」
(datasource)や「どんな板を置くか」(dashboard)を、起動時に
ファイルから自動で読み込めます。この自動読み込みを
**provisioning**(プロビジョニング、自動設営)と呼びます。
datasourceの設定・板の4枚(overview・pipeline・jev・behavior)の
定義JSONが、全部リポジトリの中に置かれています
(`docker/grafana/`)。手でポチポチ作った板は誰も再現できませんが、
ファイルから立ち上がる板は`make dashboard-up`だけで誰の環境でも
同じ形で再現できます。「作り方をファイルとして残す」は、
このリポジトリ全体を貫く原則の、ダッシュボード版です。

## 29.8 毎日の値は表に書き残す: kpi_dailyとKpiJob

ここからが、この章の山場です。

29.7までの数には共通の性質があります。**プロセスのメモリの中に置いてある**
ので、プロセスが再起動したら消える(Counterが0に戻る)し、「先週の月曜の
値」のように過去を遡って読めません。グラフとして見るにはこれで十分ですが、
「MLR(成立率)は今週いくつか」「先月はいくつだったか」という
**毎日の値として残る集計**には足りません。サービスの成長を判断する数
(KPI・ケーピーアイ=重要な成果指標)は、消えない形で残す必要があります。

そのために作られたのが、2つの部品です。表 `kpi_daily` と、
集計ジョブ `KpiJob` です。まず表から。マイグレーション0008で
作られる定義はこうです。

```sql
CREATE TABLE kpi_daily (
    metric_key text NOT NULL,
    day date NOT NULL,
    numerator numeric NOT NULL DEFAULT 0,
    denominator numeric NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT kpi_daily_pkey PRIMARY KEY (metric_key, day)
)
```

(`backend/alembic/versions/0008_obs_sessions_kpi.py` 41〜49行)

6列しかありません。1行は「どの指標の・何日の・分子と分母」です。
ここに、この章で一番学びの多い設計判断があります。

**この表は比率を保存しません。分子と分母だけを保存します。**
たとえばMLR(Mutual Latch Rate・成立率。docs 09 §2.1)なら、
「成立に至った数(分子)÷提案した数(分母)」の**割り算の結果でなく、
割る前の2つの数**を書きます。

なぜ比率を書かないのか。割り算を先にすると、**遡り直しができなくなる**
からです。docs 09の集計では「自己申告は3日まで遅れて到着する」などの
事情があり、すでに計算した日の値が後から変わることがあります。分母を
あとから受け取ったとき、「分子7・分母10」の行に分母11を上書きすれば
正しい率0.636に直せますが、「0.7」という率を保存してしまっていたら、
あとから修正のしようがありません。「割る前の数を残す」のは、
**データは加工前の形で持っておき、加工は最後の瞬間まで行わない**
という経験則の、集計への適用です。週次の集計(7日分の合計を割る)が、
日次の行を集め直すだけでできるのも同じ理由です。もし日次に率を
保存していたら、週次を正確に出すには率の平均を取るしかなく、
正しい「7日の合計の比」にはなりません。

指標の一覧は `obs/kpi.py` の先頭に定数として置かれています。

```python
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
```

(`backend/src/latch/obs/kpi.py` 24〜42行。15個の指標名)

docs 09 §2.2の表は14行(指標)ですが、継続率(retention)は
「登録してから7日後にまた来た率」と「30日後にまた来た率」の
2つの観測窓で別々に数えるので、metric_keyとしては15個になります。
unit試験がこの15個の**集合そのもの**を固定しており
(`test_metric_keys_cover_09_2_2_table`)、指標が1つ増えると
この試験が赤になります。docsの表とコードの定数が、試験を通して
結ばれているわけです。

次に集計ジョブ `KpiJob` です。動きを端的に言うと、
**60秒ごとに「集計し忘れた日はあるか」を確認して、あれば集計する**。
区切りの瞬間にぴったり動くのを狙う方式は採らない。とにかく毎分
確認する、という動き方です(設計ではこれを「tick方式」と呼びます)。
「集計し忘れた日」の判断は、表そのものを見て行います。

```python
# kpi_dailyにmlr行の無い遡及対象日(pending)
SQL_PENDING_DAYS = text("""
    SELECT d::date FROM generate_series(
        CAST(:start_day AS date), CAST(:end_day AS date), interval '1 day'
    ) d
    WHERE NOT EXISTS (
        SELECT 1 FROM kpi_daily WHERE metric_key = 'mlr' AND day = d::date
    )
    ORDER BY d::date
""")
```

(`backend/src/latch/obs/kpi.py` 129〜137行)

「遡及4日の窓(昨日から4日前)の中で、mlr行の無い日を列挙する」
SQLです。ここに2つの設計判断が隠れています。

1つ目は「真実をどこに置くか」です。日付が切り替わったかの判断に
Redisも新しい表も使いません。**kpi_daily表の行の有無そのもの**を
「まだ集計していない」の真実にします。行があれば集計済み、
なければ集計対象。しかも代表キーを`mlr`(毎日必ず0/0でも行が
書かれる指標)にしているので、「その日の集計が完成したか」を
1つのキーで確定できます。ジョブが落ちて数分止まっても、
次のtickで自動的に取り残しを回収します。この「止まっても自己修復
する」形は、第18章のsweeper・リセットジョブと同じ型です。

2つ目は「なぜtick方式か」です。既存のリセットジョブ(第18章)は
「次のJST 0時まで眠る」方式でした。眠る方式は実行時刻が正確な反面、
「時計を動かしたら即座に動く」デモができません。staging環境では
時計を前進させて日付境界を試験します(第25章・第28章)。眠り続ける
ジョブは、時計を進めても起きてこないからです。60秒ごとに目を開ける
tick方式なら、時計を進めてから最大60秒待つだけで発火が観察できます。
staging受け入れ試験(ws4-b2)は実際にそうしています。
「試験のしやすさが実装方式を選ぶ」実例として、第25章の
ControlledClockと並べて覚えておくと良い実感です。

run_onceの中身(当日分のsnapshot・retention・週次の付随集計を含む)
は`kpi.py` 349〜384行にあります。図式的に書くとこうです。

1. 未集計日(遡及窓内)を探す → あれば日次8指標を一気にUPSERT
2. 当日のmuted集合を保存(29.10節)
3. 継続率の観測窓が閉じた分だけretention_7d・retention_30dを書く
4. 昨日が日曜だったら、その週の週次5指標を書く

「UPSERT(アップサート)」は「行がなければ挿入・あれば更新」を
1文で行う操作です(INSERT と UPDATE の合成語)。遡及で同じ日を
何度書いても、同じ行を更新するだけなので、処理は何度走っても
安全です(第6章のidempotency keyと同じ、やり直しても壊れない性質)。

この集計の材料になるのが、29.2節のアクセス系計測です。
sessions表(0008で新設・user_idと時刻だけの6列構造)に
`POST /v1/sessions`が記録した行が「何人来たか」の元データになり、
intents・latch_status_events(第17章の遷移の台帳)・calibration_records
(第17章・第20章)などの既存の表を横断して、分母と分子がSQLで
計算されます。新しいテーブルを1つ足しただけで集計が成立するのは、
ここまでの章で読んできた「出来事は台帳に残す」設計の復利です。

workerへの配線は`worker/main.py`の243〜263行で、collector(29.9節)と
ともに、既存のタスクたちと同じ`asyncio.create_task(...run(stop=...))`
の形(282〜283行)で並べられています。第18章で見た
「各ジョブは独立したタスクで、stopイベントを共有する」構成の延長に
すぎません。

最後に、集計した値がダッシュボードに出るまでの道を辿ります。
kpi_dailyの行は、60秒周期のexporter(29.9節)がgaugeに写し、
Prometheusが15秒ごとに取りに来て、Grafanaのoverviewの板の
パネルが問い合わせて描く。**「毎日の正」はDBに、「見せるための
写し」はgaugeに**——真実が1か所(kpi_daily)にあり、残りが写しと
いう構成が、ここでも貫かれています。

## 29.9 即時の数と毎日の数: 2つの速さを混ぜない

`obs/collector.py` は「60秒ごとに、いまの値をgaugeに写すだけの
薄い周期タスク」です。写すのは、待たれている知らせの件数、最も古い
知らせの年齢、候補の件数、kpi_dailyの最新行、breakerの状態です。

```python
    async def collect_once(self) -> None:
        now = self._clock.now()
        async with self._engine.connect() as conn:
            pending = (await conn.execute(_SQL_PENDING)).scalar_one()
            oldest = (await conn.execute(_SQL_OLDEST, {"now": now})).scalar_one()
            candidates = (await conn.execute(_SQL_CANDIDATES)).scalar_one()
            kpi_rows = (await conn.execute(_SQL_KPI_LATEST)).all()
        metrics.event_queue_pending.set(pending)
        metrics.event_queue_oldest_pending_seconds.set(float(oldest))
        metrics.pipeline_candidates.set(candidates)
```

(`backend/src/latch/obs/collector.py` 60〜69行。kpiの写しと
breaker状態を写す続きは70〜76行)

「毎日集計して表に書く」KpiJobと、「毎分いまの値を数えてgaugeに
写す」collector。この2つを、**即時の数と毎日の数**と呼び分けて、
混ぜない設計になっています。理由は3つあります。

1つ目は、数える仕組みが違うから。即時の数はプロセスのメモリで
増減する(counter/gauge)のに対し、毎日の数はDBで
「distinct(重複を除いた集まり)」の演算が必要です。今日このアプリを
開いたのは何人か、という数は、1行足すcounterでは出せません
(同じ人が3回開いたら3になる)。だからDAU・再来訪・継続率は全部、
sessions表を横断するSQLで出す、という実装になりました。

2つ目は、寿命が違うから。プロセスのメモリの数は再起動で消えます。
消えてよい数(いまの詰まり具合)はメモリに、消えてはいけない数
(先月のMLR)はDBに、と寿命で置き場所を分けます。

3つ目は、見る間隔が違うから。詰まり具合は数分の遅れが許されませんが、
MLRの今日の値が60秒遅れて見えても誰も困りません。間隔に合わせて、
仕組みの重さ(実行頻度・作り込み)を決める。全部を秒単位の即時集計に
したら、DBへの問い合わせが激増して本体の処理を遅くします。
**観察したい速さが、仕組みの設計を決める**という視点は、
この章で最初に触れた「読み手の問いから群を作る」と同じ方向の判断です。

なお、この章で扱う監視は「人間が見る・後の単位が読む」ためのものです。
「値が閾値を超えたら人に伝える」(アラート)は、次の単位以降の
消費者です。特にEvent Queueのたまり(pending件数と最古の年齢)は、
ws-7の「Queue障害の検知」試験がそのまま読む器になっています。
器だけを先に整え、消費者とともに試験する——これもM4の
単位の組み立て方として覚えておきたいところです。

## 29.10 近似を正直に書く: 「告白」としての設計記録

最後に、この単位で最も性格のあらわれる設計記録を1つ読みます。

完全に正確には数えられない値があります。代表がJevの実行回数です。
ダッシュボードに見えるメモリのcounterは、正確に1回ずつ増えますが、
再起動で消えます。そこで「毎日の恒久値」は、DBに残った評価結果
(match_candidatesのjev_result列が書かれた行の日付)から数えます。
ただし、これは厳密な「実行回数」と一致しません。同一評価世代の
スキップ(第13章)のように「呼ばなかった回」の違いで、ずれが生じ得ます。

このずれに対するLATCHの選択は、**ずれうる値を、ずれうると明記した
うえで採用する**でした。設計メモには「近似の告白」として§7論点5が
置かれ、完了報告書の§6には「近似の告白の再記録」として
「ws-6/ws-7以降で再評価すべき」とまで書かれています。別の例も2つ。
mute_rate(通知オフ率)は、設定変更の履歴を持つ表が存在しないため、
毎日の集合を写した表(kpi_muted_user_snapshot)の**前日との差分**
で数えます(なので「解除してまたmutedにした往復」は日付の境界でしか
観測できません)。無回答率は「提案から3日経っても回答がない」を
その日のSQLで判定します。

ここに学べるのは、**近似を隠さず、どこまで近似かを文書に書く**
という作法です。数は正確であってほしい。でも、正確さを得るには
新しい表と処理の追加が必要で、その効果が指標1つの粒度改善に
限定されるなら、費用対効果として近似で足りると判断する——
そして判断したことを、報告書に「近似の告白」という名前をつけて
残す。告白という、少し大げさに聞こえる名前までつけて。

初心者のうちは「不完全な実装は正直に書くと恥ずかしい」と感じる
かもしれません。けれども、後でこのコードを読む人(数か月後の
あなた自身を含む)にとって、価値があるのは完璧なコードでなく、
**なぜその形なのか・どこに限界があるのかが読める記録**です。
設計メモに根拠を書く・報告書に読み替えを書く・近似を告白する。
このリポジトリの一連の作法は、計測という「誤差の住む分野」で
いちばん活きることが、この単位から読み取れます。

## 29.11 この章の流れを、1つの問いで確かめる

章を締める前に、最初の問いに戻ります。「動いているシステムの内側を、
外から知るには」——この章の答えはこうでした。

**システム自身が4種の数を数える。数え方には約束がある(ラベル・
pathテンプレート・3形の使い分け)。数の定義は1か所に置く。プロセスの
メモリの数はPrometheusが取りに来て集める。毎日の数はSQLでDBの表に
書き残す。そして厳密に数えられない数は、近似だと正直に書く。**

Lab 15では、この章の部品がstagingの上でどう連動して動くかを、
自分の手で確かめます。数の窓口を覗き、Prometheusの取った数を
問い合わせ、Grafanaの板が描くところまで。手順どおり進めば、
この章のどの節のどの部品が、どの画面のどのパネルに対応しているかを
自分の目で結べるはずです。

## 29.12 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| 計測 / メトリクス | システムが自分の様子を数値として数え続けること / その数えた値 |
| 計測3群 / アクセス系 | パイプライン・Jev・提案と行動の3つに分けた数の切り分け / アプリを開いたという出来事の数(DAU・再来訪の元) |
| Counter / Gauge / Histogram | ただ増やし続ける数 / いまの値を上書きする数 / 区間ごとの回数を数える分布 |
| ラベル | 数に付ける付箋(provider・path等)。システムの属性のみ・人の情報は書かない |
| pathテンプレート | 実際のURL値でなく宣言側の形(/v1/latches/{latch_id})。ラベルの種類を有限に保つ |
| middleware | 全リクエストを一括で掴む仕組み。数え忘れない計測に適する |
| /metrics | 数の置き場をテキストで吐き出す窓口。認証なし・レート制限なし・127.0.0.1バインド |
| 127.0.0.1バインド | 自分のPCの内側からだけ届く窓口にする守り。外部のPCから届かない |
| Prometheus / pull型 | メトリクスを取りに来て溜めるサーバー / 送りつけでなく取りに来る集め方 |
| Grafana / ダッシュボード | 溜めた数をグラフに仕立てる道具 / 数を一望できる板 |
| provisioning | datasourceや板の定義をファイルから自動で読み込む仕組み。再現可能 |
| kpi_daily | 「どの指標の・何日の・分子と分母」を1行に持つ表。毎日の値の正 |
| 分子 / 分母 | 割り算の前の2つの数を保存する設計。遡り直しと週次集計ができる |
| KpiJob / tick方式 | 毎日の集計ジョブ / 60秒ごとに「集計し忘れはないか」を確認する動き方 |
| 遡及(そきゅう)UPSERT | 取り残し・遅着の分を、遡って集計し直すこと(行は挿入または更新で安全) |
| collector / exporter | 60秒ごとにいまの値をDBから読んでgaugeに写す薄い周期タスク / その役割の一般名 |
| 単一真実 | 定義や値の正を1か所に置く設計(obs/metrics.py・kpi_daily) |
| 近似の告白 | 厳密に数えられない値について、近似であることを設計・報告に書き残す作法 |

## 29.13 確認問題

1. 「今日このアプリを開いたユーザー数(DAU)」を、プロセスのメモリの
   counterだけで数えられない理由を、29.9節の言葉で説明してください
2. kpi_dailyが「0.7」という比率でなく「分子7・分母10」を保存する利点を、
   「分母があとから1増えた場合」を例に説明してください
3. middlewareで`path`ラベルに実際のURL(`…/latches/7c9e…`)でなく
   pathテンプレートを使う理由を、ラベルの種類の数に触れて説明してください
4. KpiJobが「次のJST 0時まで眠る」方式でなく60秒tick方式にした理由を、
   staging環境の時計の話と結びつけて説明してください
5. `/metrics`が認証なし・レート制限なしである判断と、それを支える
   127.0.0.1バインドの関係を、`/health`の位置づけと比べながら
   説明してください
6. あなたがJevの実行回数の設計を任されたとしたら、
   「メモリのcounter」「DBからの近似」「履歴テーブルの新設」の
   3択をどのように比較しますか。29.10節の告白の考え方を使って、
   自分のノート(`my-notes.md`)に書いてみてください

(解答例は用意していません。自分の言葉で答えられたら合格です。
詰まった節が、読み返すべき節です)
