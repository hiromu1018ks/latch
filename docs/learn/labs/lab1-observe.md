# Lab 1 動いているものを観察する: 4サービスの生きている姿を見る

- 種別: チュートリアル(手を動かして必ず成功体験を得る。何も壊しません)
- 前提知識: 第1章(特に1.3 HTTP・1.4 Docker・1.11 コマンド集)
- 所要目安: 20〜30分
- このLabでできるようになること: 動いているシステムを「観察する手段」を一通り手持ちにする。
  ログ・DB・Redis・テストの4つの窓から、中で起きていることを見る癖がつく
- 次に読むもの: 第2章(観察した `/health` をコードで追います)

## 0. なぜ「読む前に観察」なのか

コードだけを読んで理解したつもりになっても、それは頭の中の想像です。動いているものを
先に見ておくと、コードを読んだときに「あのログの一行はここで出ていたのか」と実感を
伴って繋がります。5分の観察が、あと1時間の読解を速くします。このLabはその土台作りです。

すべての操作はリポジトリのルート(`latch/`)で実行します。何も壊さないので安心してください。

## 1. 4サービスの生存確認

```bash
make ps
```

期待される出力(Up の時間は環境で異なります):

```
SERVICE   STATUS
api       Up 3 hours (healthy)
db        Up 3 hours (healthy)
redis     Up 3 hours (healthy)
worker    Up 3 hours
```

**何を見ているか**。`make ps` は `docker compose ps` のショートカットで、compose.yaml から
起動した4つの容器の状態を一覧します。`(healthy)` は各容器に付いている健康診断
(定期的に「本当に仕事できる状態か」を自己点検する仕組み)に合格している印です。
apiの点検は「自分の `/health` を自分で叩く」、db は `pg_isready` という専用コマンド、
redis は `PING` に `PONG` が返るか、で判定します。

**観察のポイント**。worker だけ `(healthy)` が付いていません。workerには健康診断そのものが
定義されていません。理由は、workerがHTTPの窓口を持たない裏方プロセスだからです
(判断の経緯は `docs/plans/M0/scaffold-design.md` §5 に記録があります)。
「全部healthyであるべき」と思い込んで質問するより、「なぜここだけ違うのか」と理由を
探せる方が、コードベースとの付き合い方は上手くなります。

すべて止まっていたら `make up` して1分待ってから再実行してください。

## 2. APIを叩く: 一番小さいやり取り

```bash
curl http://127.0.0.1:8000/health
```

期待される出力:

```json
{"status":"ok","server_time":"2026-09-27T09:41:23.123456+00:00"}
```

**何を見ているか**。第1章1.3で学んだHTTPの一回です。`GET` メソッドで `/health` を要求し、
ステータスコード200と本文が返りました。curlは `-i` を付けるとステータス行まで見えます。

```bash
curl -i http://127.0.0.1:8000/health
```

```
HTTP/1.1 200 OK
content-type: application/json
...
```

`server_time` の末尾 `+00:00` は「UTCのタイムゾーン付き時刻です」の印(第3章3.4)。
もう1回叩くと時刻が少し進みます。この時刻がどの部品で作られているかは、第2章のトレース1の
主題でした。

**存在しない場所も叩いてみましょう**。

```bash
curl -i http://127.0.0.1:8000/v1/health
```

404 Not Found が返るはずです。LATCHでは `/health` は運用のための窓口で、正式なAPIの窓口群
(`/v1/...`)とは別枠。だから `/v1/health` は「そんな場所はない」と答えます。
試験(`test_health_not_under_v1`)でわざわざ404になることを確認している、**仕様**です。

## 3. ログを流して見る: リクエストの跡を見る

```bash
make logs
```

画面に4サービスのログが流れ続けます(Ctrl+Cで止めます)。この状態で**別のターミナル**を
開いて、もう一度 `curl http://127.0.0.1:8000/health` を実行してください。

apiのログにこういう行が現れます。

```
api-1  | INFO:     172.18.0.1:54321 - "GET /health HTTP/1.1" 200 OK
```

**何を見ているか**。「どのアドレスから・どんなリクエストが来て・どんな結果コードが返ったか」の
記録です。`INFO:` はログの重要度(情報)で、エラーのときはWARNINGやERRORになります。
認証まわりの試験を行うと、`auth.error code=INVALID_IDP_TOKEN` のような行が流れるのを
観察できます(試すなら `make test-ci` を走らせている間に眺めてみてください)。

workerのログには起動時の `worker started` と停止時の行しかありません。今のworkerは
「終了指示を待つだけ」の土台だからです(中身はM2以降)。

## 4. データベースの中を覗く: 表の世界

```bash
docker compose exec -T db psql -U latch -d latch -c "\dt"
```

**コマンドの読み方**。`docker compose exec` は「指定した容器の中でコマンドを実行する」です。
`db` 容器の中で `psql`(PostgreSQLに話しかける公式ツール)を `-U latch`(ユーザー名 latch)
`-d latch`(データベース名 latch)で起動し、`\dt`(テーブル一覧を表示するpsqlの命令)を
実行しています。コマンドが二重(容器+psql)になっているのが分かれば十分です。

`users`、`intents`、`match_events`、`geofeatures` など13の表(テーブル)が一覧されるはずです。
これらの定義元は `docs/05-data-model-api.md` 第2節で、実体は `backend/alembic/versions/` の
マイグレーションが作りました(第1章1.7)。

地物データの表の中身も見てみます。

```bash
docker compose exec -T db psql -U latch -d latch \
  -c "SELECT source, count(*) FROM geofeatures GROUP BY source"
```

期待される出力:

```
 source  | count
---------+-------
 isj_town |    46
 osm_poi  |   563
```

**何を見ているか**。これはSQL(データベースに話しかける言語)の一文で、
「geofeatures表を source 列の値ごとにまとめて、件数を数えろ」という意味です。
鹿児島市周辺の実データ(国交省位置参照情報46行+OpenStreetMap 563行)が入っていることが
確認できます。 地名⇔座標の変換はこの表だけで完結します(トレース3で中身を追います)。

もう一つ、試しに生のSQLで「天文館」を探してみましょう。

```bash
docker compose exec -T db psql -U latch -d latch \
  -c "SELECT name, kind, city_name FROM geofeatures WHERE name LIKE '%天文館%'"
```

`LIKE '%天文館%'` は「name に天文館を含む行」を探す条件です。実データに触れると、
抽象だった「ジオコーディング」がぐっと具体化します。

## 5. Redisを覗く: PONGの世界

```bash
docker compose exec -T redis redis-cli ping
```

期待される出力:

```
PONG
```

**何を見ているか**。redis-cli はRedisに話しかけるツールで、`ping` と送ると生きていれば
`PONG` と返す約束になっています。dbコンテナでpsqlを使ったのと同じ構図です。

試しに、認証まわりで実際に使われている鍵を見てみます(何もなければ空です)。

```bash
docker compose exec -T redis redis-cli --scan --pattern 'auth:*'
```

`auth:revoked:...` のような名前の鍵は、ログアウトされたトークンの失効リスト(第1章1.7)です。
`make test-ci` を走らせた直後なら、試験が残した鍵が観察できることがあります。

## 6. テストを走らせる: 2秒の意味

```bash
make test
```

期待される出力の末尾:

```
====================== 302 passed, 72 deselected in 2.67s ======================
```

**何を見ているか**。302件のunit テストが全部合格し、72件のintegration テストは
「選別から外された(deselected)」状態です。integrationは実DB・実Redisを使うので、
`make test-ci` で別途走らせる運用になっています(第1章1.11、第3章3.6)。

1件だけ指定して詳細表示(-v)で実行することもできます。

```bash
cd backend && uv run pytest tests/unit/test_app_health.py -v
```

```
tests/unit/test_app_health.py::test_health_returns_ok_and_clock_time PASSED
tests/unit/test_app_health.py::test_health_server_time_is_iso8601_utc PASSED
...
```

試験名を縦に読むと、「何を保証しているか」の目録になります。テスト名は仕様書の
もっとも細かい形です(第3章3.6)。

## 7. (おまけ)ジオコーディングの動作確認

```bash
make geo-verify
```

期待される出力(抜粋):

```
  isj_town: 46 rows
  osm_poi: 563 rows
  forward '天文館': -> 天文館 (osm_poi/bus_stop)
```

「天文館」という地名を投げると、DBの地物表に照合して結果を返す体験です。
`forward`(正転=地名→座標)の他、逆転(座標→地名)も試せます。

```bash
cd backend && uv run --group geo python -m latch.geo verify --reverse 130.5581 31.5963
# reverse: -> '鹿児島市泉町'
```

## 8. 振り返りと考察問題

自分のノート(`docs/learn/my-notes.md` を作って使ってください)に答えを書いてみましょう。

1. `(healthy)` が付くサービスと付かないサービスの違いと、その理由
2. `curl -i /v1/health` が404を返すことは何の仕様か(`test_health_not_under_v1` と照合)
3. `make test` の「302 passed, 72 deselected」の両方の数字が意味すること
4. psqlとredis-cliの使い方の共通点(どちらも「容器の中で、専門ツールを起動する」構造)
5. ログの1行 `INFO: ... "GET /health HTTP/1.1" 200 OK` を、第1章の用語で全文解釈する

次は第2章「コードの読み方」です。今日観察した `/health` が、コードのどこで作られているかを追います。
