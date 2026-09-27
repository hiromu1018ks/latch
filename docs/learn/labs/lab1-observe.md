# Lab 1: 動いているものを観察する

- 種別: チュートリアル(手を動かして必ず成功体験を得る)
- 前提: `00-environment.md` 読了。所要目安: 20〜30分
- 次に読むもの: `01-reading-paths.md`(観察したものをコードで追います)

このLabの目的: **コードを読む前に、動いているシステムを五感で観察する**。
「動くものを見てから読む」と「読んでから動かす」では、理解の速さが全然違います。

## 0. 安全確認

このLabは**何も壊しません**。見るだけ・叩くだけです。

## 1. 4サービスが生きていることを確認する

```bash
make ps
```

期待される出力( uptime は異なります):

```
SERVICE   STATUS
api       Up 3 hours (healthy)
db        Up 3 hours (healthy)
redis     Up 3 hours (healthy)
worker    Up 3 hours
```

観察メモ:

- api・db・redis に `(healthy)` が付く — 「容器ごとに付けられた健康診断」に合格している意味
- worker には付いていない — workerはHTTP口を持たないため、健康診断の方法が定義されていない
  (設計判断の記録: `docs/plans/M0/scaffold-design.md` §5)

もし全部止まっていたら `make up` で起動して1分待ってから再実行してください。

## 2. APIを叩く

```bash
curl http://127.0.0.1:8000/health
```

期待される出力:

```json
{"status":"ok","server_time":"2026-09-27T09:41:23.123456+00:00"}
```

観察メモ:

- `server_time` の末尾に `+00:00` — タイムゾーン付き(UTC)である印
- もう1回叩くと時刻が進む — この時刻がどこで作られているかはトレース1で追います

## 3. ログを「流して」観察する

```bash
make logs
```

画面にログが流れ続けます(止めるときは Ctrl+C)。この状態で**別のターミナル**から
もう一度 `curl http://127.0.0.1:8000/health` を実行してください。

apiのログにこんな行が現れます:

```
api-1  | INFO:     172.18.0.1:54321 - "GET /health HTTP/1.1" 200 OK
```

観察メモ: リクエスト1回にログ1行。「誰がどのURLを叩いて、どんな結果コード(200=成功)が
返ったか」が記録されます。`worker-1` のログには起動時の1行だけがあるはず —
今は裏仕事がまだ無いからです(実装は M2以降)。

## 4. データベースの中を覗く

```bash
docker compose exec -T db psql -U latch -d latch -c "\dt"
```

期待される出力(抜粋): `users`, `intents`, `match_events`, `geofeatures` など表の一覧。

もう一つ、地物データを見てみます:

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

観察メモ: 鹿児島市周辺の実データ(国交省位置参照情報46行+OpenStreetMap 563行)が
PostgreSQLの中に入っています。地名→座標変換はこの表だけで完結します。

## 5. Redisを覗く

```bash
docker compose exec -T redis redis-cli ping
```

期待される出力: `PONG`

Redisは「おーい、生きてる?」と聞くと「PONG」と返す約束のサーバーです。
認証の失効リスト等が入る箱として使われます(中身は認証を扱うトレース2で)。

## 6. テストを走らせる

```bash
make test
```

期待される出力の末尾:

```
====================== 209 passed, 61 deselected in 2.5s =======================
```

観察メモ:

- **209件の試験が約2秒で終わる** — テストが速いと、何度でも実験できる
  (この速さ自体が FakeClock と「サーバーを起動しないテスト」の成果物です)
- `61 deselected` — 統合試験(integration)は除外されている印。実DB・実Redisを使う
  試験は `make test-ci` で別途走らせます

## 7. (おまけ)Geoの動作確認

```bash
make geo-verify
```

期待される出力(抜粋):

```
  forward '天文館': -> 天文館 (osm_poi/bus_stop)
```

「天文館」という地名を投げると、DBの地物に照合して結果を返す、の一部始終です。

## 振り返り(自分のノートに書いてみよう)

- 4サービスの役割を自分の言葉で1行ずつ
- `/health` の応答の意味
- テストが「速い」ことは何をもたらすか

疑問が残ったことは `my-notes.md` の「疑問」欄へ。次は `01-reading-paths.md` で、
今日観察した `/health` がコードのどこで作られているかを追います。
