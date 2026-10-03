# Lab 11 staging環境で時計を操る: 動いている世界の針を、外から動かす

- 種別: チュートリアル(手を動かして必ず成功体験を得る。壊しません)
- 前提知識: 第25章(共有時計 `ControlledClock` の設計)。Lab 1の `make ps` とcurlの使い方、
  Lab 6で出てきた「トークンを発行して本人参照を叩く」流れを思い出せるとスムーズです
- 所要目安: 20〜30分(うち数分は、staging環境の起動待ちの時間です)
- このLabでできるようになること: 本物に近いstaging環境(nginx+api2台+worker2台)を立てて、
  入口・各api直と3つの窓から応答を観察できる。Redisに置かれた共有時計を `set`・`advance` で
  動かし、**プロセスを1つも再起動せずに**3つの窓の `server_time` がそろうのを自分の目で
  確認できる。最後に、時計を1日進めた瞬間に、先ほどまで有効だったアクセストークンが
  期限切れ(401)になるのを観察できる
- 次に読むもの: `concepts/24-acceptance-tests.md` をまだ読んでいない人は先へ、読んでいる人は
  第25章・Lab 11が「後続の単位がstagingで何をするか」の入口です。時計を動かして試す考え方は、
  負荷試験・障害試験の単位の下地になります

## 0. このLabで何をするか

これまでのLabは、ci環境(毎コミットの試験で使う常設の5サービス)で行ってきました。
このLabで使うのは、M4で増えた**もう1つの世界**、staging環境です。

stagingは、ci環境とは**別プロジェクト**として立てられます。Docker Composeのプロジェクト名
(`latch-staging`)が違うため、コンテナ名・ネットワーク・データ置き場(ボリューム)が
自動で分かれます。つまりこのLabで何をしても、ci環境(あなたがこれまでのLabで使ってきた
世界)には触れません。データベースも別物です。安心して動かしてください。

構成の違いはこうです。ci環境はapi1台・worker1台でした。stagingは入口に **nginx** が立ち、
その後ろに **api2台**・worker2台が並びます。ポート番号も世界ごとに決まっています。

| 窓口 | ci環境 | staging環境 |
|---|---|---|
| 入口(nginx経由) | (なし) | `127.0.0.1:18000` |
| api1直 | `127.0.0.1:8000` | `127.0.0.1:18001` |
| api2直 | (1台のみ) | `127.0.0.1:18002` |

このLabの流れはこうです。環境を立てる → 3つの窓を叩く → 共有時計を合わせる →
1日進めてトークンを期限切れにする → 時計を戻して片付ける。各手順の「なぜ」は第25章で
読んでいるので、ここでは手を動かして確認します。

以降、すべての手順をリポジトリのルート(`latch/`)で実行します。

## 1. 鍵を作って環境を立てる

stagingのapiは、JWTの署名検証に使う鍵ペアを `.staging/keys/` から読みます(第25章では
触れませんでしたが、ci環境とstaging環境で鍵を分けるのがstaging専用鍵の規律です。鍵の
置き場所はcomposeファイルの `LATCH_AUTH_TESTKEYS_DIR` で指定してあります)。
まず鍵を作ります。

```bash
make staging-keys
```

何が起きるか: `.staging/keys/` に、テストIdP用の鍵ペア(秘密鍵とJWKS)を作ります。
すでに鍵があるときは、**何も聞かずに作り直します**(ファイルが上書きされます。うっかり
動いているstagingの途中で再実行すると、前の鍵で発行したトークンが無効になるので、
このLabの途中で再実行しないでください)。終わっても画面には作業のエコーしか出ません。

```bash
ls .staging/keys/
```

期待される出力(2つのファイルが並んでいれば十分です):

```
idp_test_jwks.json
idp_test_private.pem
```

次に、環境を立てます。

```bash
make staging-up
```

何が起きるか: 8つのコンテナ(db・redis・pubsub・api1・api2・nginx・worker1・worker2)を
順に立て、全員が健康診断を通るまで待ってから終わります。初回はイメージの作成で数分、
すでにイメージがあるときは1〜2分です。最後に、全サービスの名前の後ろに `Healthy` が
並びます。workerは健康診断を持たないため表示されませんが、立てばUpしています。

状態を確認します。

```bash
make staging-ps
```

期待される出力(2026-10-03に実行したもの。順序は環境によります):

```
NAME                      IMAGE             SERVICE   STATUS
latch-staging-api1-1      latch-staging-api1    api1      Up 18 seconds (healthy)
latch-staging-api2-1      latch-staging-api2    api2      Up 19 seconds (healthy)
latch-staging-db-1        latch-staging-db      db        Up 25 seconds (healthy)
latch-staging-nginx-1     nginx:alpine          nginx     Up 13 seconds
latch-staging-pubsub-1    gcr.io/...emulators   pubsub    Up 25 seconds
latch-staging-redis-1     redis:8-alpine        redis     Up 25 seconds (healthy)
latch-staging-worker1-1   latch-staging-worker1 worker1   Up 18 seconds
latch-staging-worker2-1   latch-staging-worker2 worker2   Up 19 seconds
```

コンテナ名がすべて `latch-staging-` で始まっている点に注目してください。ci環境の
コンテナ(latch-ci-api-1など)とは別名で並んでいます。**1台のPCの中に2つの世界が
同居している**のが、ここから確認できます。

## 2. 入口と、2台の裏側を叩く

まずは入口(nginx)から。ヘルスチェックを3つの窓で順に叩きます。

```bash
curl -s http://127.0.0.1:18000/health; echo
curl -s http://127.0.0.1:18001/health; echo
curl -s http://127.0.0.1:18002/health; echo
```

期待される出力(時刻は実行時によります。18000番=nginx経由・18001番=api1直・
18002番=api2直):

```
{"status":"ok","server_time":"2026-10-03T04:51:50.657856+00:00"}
{"status":"ok","server_time":"2026-10-03T04:51:50.663265+00:00"}
{"status":"ok","server_time":"2026-10-03T04:51:50.669200+00:00"}
```

3行とも `status: ok` で、`server_time` は3つともほぼ同じ実時間のはずです。
いまこの瞬間、api1とapi2は**別々のプロセス**なのに、同じ時刻を答えています。
答え合わせをしているからではありません。第1章で読んだとおり、apiの中の時計
(`SystemClock`)はOSの時計を聞いているだけで、聞いた瞬間の時刻を返すからです。
プロセスが違っても、元の時計が同じなら答えはそろいます。

**実行する前に予測を書いてください**(my-notes.mdへ)。このLabの後半で、時計を
合わせたとき、この3行の出力はどうなると予想しますか?予測の理由も一緒に書いておくと、
後で見返したときに「予測と同じ」か「違った」かが分かります。

## 3. 手形を発行して、本人参照まで通す

時計を動かす前に、「いま有効な通行手形」を用意します。Lab 6と同じ流儀で、
テストIdPのトークンを発行して、stagingのapiと交換します。まず手形(テストIdPのJWT)です。

```bash
make staging-token SUBJECT=learn-lab11 | tail -1 > /tmp/lab11-idp.txt
head -c 40 /tmp/lab11-idp.txt; echo " ...(JWT)"
```

何が起きるか: `make staging-token` が、§1で作ったstagingの秘密鍵でJWTを発行します。
コマンドのエコー行とJWTの2行が出るので、`tail -1` でJWTの行だけをファイルへ
書き出します。先頭40文字を表示して、JWT(`eyJ`で始まる長い文字列)が入ったことを
確かめます。

```
eyJhbGciOiJSUzI1NiIsImtpZCI6InRlc3QtaWRw ...(JWT)
```

次に、この手形をstagingのapiに見せて、アクセストークンと交換します
(Lab 4で読んだ、認証の2段構えです)。交換したら、ユーザー登録と本人参照まで通します。

```bash
ACCESS=$(curl -s -X POST http://127.0.0.1:18000/v1/auth/token \
  -H "Content-Type: application/json" \
  -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab11-idp.txt)\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
echo "$ACCESS" > /tmp/lab11-access.txt
curl -s -o /dev/null -w "users: %{http_code}\n" -X POST http://127.0.0.1:18000/v1/users \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"display_name":"lab11","birth_date":"1990-04-01","profile":{}}'
curl -s http://127.0.0.1:18000/v1/users/me -H "Authorization: Bearer $ACCESS"; echo
```

期待される出力(idと時刻は環境によります):

```
users: 201
{"id":"fa333989-5fa8-405a-8df2-cc63cd69d723","display_name":"lab11","profile":{},"birth_date":"1990-04-01","profile_complete":true}
```

`201` で登録でき、本人参照(`/v1/users/me`)が `200` で自分の行を返しました。
この `ACCESS` には「有効期限つきの通行手形」が入っています。有効期限はいつか。
答えは、**stagingの時計**が決めます。このapiは第25章で読んだ `ControlledClock` で
動いていますが、まだ誰も基準点を書いていないので、実時間で動いています。
いま手元にある手形は、その実時間で1時間後まで有効です。

## 4. 時計を合わせる: set

いよいよ、時計を合わせます。まず、時計の制御状態を覗きましょう。

```bash
make staging-clock ARGS="show"
```

期待される出力(時刻は実行時によります):

```
[clock] key: latch:clock:control
[clock] control: unset (real time)
[clock] virtual now: 2026-10-03T04:52:01.311350+00:00
[clock] real now:    2026-10-03T04:52:01.311362+00:00
```

`control: unset (real time)` は第25章の判断1です。基準点がまだ書かれていないので、
実時間がそのまま仮想時間として使われています(`virtual now` と `real now` が
ほぼ同じ値です)。APIが3つの窓でそろって返した時刻も、これと同じはずでした。

では、基準点を書き込みます。日本時間の翌日0時へ合わせてみます。
**日時は、実行した日の「翌日0時」をJST付きで指定します**(自分が実行する日で
読み替えてください。タイムボムを避ける作法——時刻を画面や手順の例に固定しない——を
ここでも守ります)。

```bash
make staging-clock ARGS="set 2026-10-04T00:00:00+09:00"
```

期待される出力(先ほどの実行例は日本時間の10月3日に実行したものです):

```
[clock] set virtual time -> 2026-10-03T15:00:00.005409+00:00
```

日本時間の10月4日0時は、世界標準時(UTC)では10月3日の15時です。第3章3.4で読んだ
「内部はUTCの一点」が、この表示にも出ています。

では§2の予測を検証します。3つの窓をもう一度叩きます。

```bash
curl -s http://127.0.0.1:18000/health; echo
curl -s http://127.0.0.1:18001/health; echo
curl -s http://127.0.0.1:18002/health; echo
```

期待される出力(set直後、数秒置いて実行したもの。3つとも**翌日0時台**の実時間+経過秒
に変わっている点に注目してください):

```
{"status":"ok","server_time":"2026-10-03T15:00:10.015486+00:00"}
{"status":"ok","server_time":"2026-10-03T15:00:10.020988+00:00"}
{"status":"ok","server_time":"2026-10-03T15:00:10.026564+00:00"}
```

3つの窓が、**1プロセスも再起動せずに**、翌日0時の10秒後という同じ時刻を答え始めました。
api1もapi2も、nginxの前で順番待ちしていたworkerも、全員が同じ基準点(Redisの
`latch:clock:control`)を覗きに行った結果です。

もう1つ、細かい観察をどうぞ。`set` の出力は `15:00:00` ちょうどでした。ところが
`server_time` は `15:00:10` になっています。**合わせた時刻から、実際の時間の速さで
進み続けている**のです(第25章25.3の足し算)。clockは「時が止まった偽の世界」を作る
道具ではなく、「本物の世界の針をずらす」道具だという話の、目で見える証拠です。

## 5. 1日進めて、手形を期限切れにする

続いて、`advance` です。§3で発行した通行手形が手元にあります(環境変数ではなく、
`/tmp/lab11-access.txt` に入れてあります)。まず、いま動かせることを1度確認します。

```bash
ACCESS=$(cat /tmp/lab11-access.txt)
curl -s -o /dev/null -w "%{http_code}\n" \
  http://127.0.0.1:18000/v1/users/me -H "Authorization: Bearer $ACCESS"
```

期待される出力:

```
200
```

**実行する前に予測を書いてください**。この手形の有効期限は、§3で発行されたときの
stagingの時計(=合わせたあとの仮想時間)から1時間後です。ここで時計を1日進めたら、
この手形はどうなりますか?ステータスコードで予測してください。

では、進めます。86400は86400秒=ちょうど1日です。

```bash
make staging-clock ARGS="advance 86400"
```

期待される出力(前の節の続きの時刻で実行したもの):

```
[clock] advanced by 86400.0s -> 2026-10-04T05:03:15.443009+00:00
```

先ほど合わせた15:00台からさらに1日進みました(実行した時間によって表示は変わります)。
§3と同じ問いを、もう1度投げます。

```bash
curl -s http://127.0.0.1:18000/v1/users/me -H "Authorization: Bearer $ACCESS"
```

期待される出力(ステータスコードは401です):

```json
{
    "error": {
        "code": "UNAUTHENTICATED",
        "message": "access token expired",
        "details": null
    }
}
```

401・`access token expired` です。予測どおりでしたか?ここで起きたことを整理します。
手形そのものは1文字も変わっていません。変わったのは、手形を検査する側が見ている**時計**
です。有効期限とは「検査する時刻と、手形の期限を比べた結果」であって、手形に刻印された
絶対的な資格ではありません。第25章で読んだ「期限判定はClock由来の時刻で行う」設計が、
時計を動かしただけで破られた実演です。

余裕があれば、/healthをもう1度叩いてみてください。3つの窓が、またそろって
1日後の時刻を答えているはずです。

## 6. 時計を戻す・覚えを消す

遊び終えたら、時計を実時間へ戻します。`set` は後退もできる(第25章25.4)ので、
「いまの実時間」へ合わせ直せば戻ります。コマンドの中で現在のUTCを組み立てて渡します。

```bash
make staging-clock ARGS="set $(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
```

何が起きるか: `date -u` が現在の世界標準時を、apiが理解する書式
(`2026-10-03T05:10:00+00:00` のような形)で出力し、それを `set` の引数に渡します。
時計が実時間へ戻ります。

```
[clock] set virtual time -> 2026-10-03T05:10:30.005476+00:00
```

この状態でも、制御そのもの(Redisの基準点)は残っています。次に環境ごと止めれば、
覚えも消えます。

```bash
make staging-down
```

何が起きるか: 8つのコンテナを順に止めて取り除きます。ci環境の `make down` と同じく
データ置き場(ボリューム)は残ります。stagingのRedisはボリュームを持たないため、
**Clockの制御の覚え(基準点)もここで消えます**。次に `make staging-up` で立て直したときは、
`show` が再び `control: unset (real time)` を返す——制御していない状態が、初期化手順なしで
実時間に戻っている(第25章の判断1)ことを、このLabの検証でも確認しました。

## 7. 振り返りとノート

このLabで、あなたは「動いている世界の時計」を外から動かしました。my-notes.mdに、
次の3つを書き残すのをおすすめします。

1. **§4の3行のserver_time**。api1とapi2が答え合わせをしたのではなく、同じ場所を覗いて
   いるだけだという説明を、自分の言葉で書く
2. **§4で `15:00:00` に合わせたのに `15:00:10` と返ってきた理由**。第25章25.3の足し算を
   「写真と経過秒」の言葉で説明する
3. **§5の401**。手形が壊れたのではなく検査側の時計が動いた、という点を、
   「もしClockが実時間に静かに戻っていたら(fail-openの世界)何が起きていたか」と
   組み合わせて考える

疑問が残ったら、それもノートに書いてください。このLabで使った道具(Clock制御・
イベント注入・staging環境)は、次の単位たち——27項目のstagingでの試験、負荷試験、
障害試験——の基礎道具です。「時計を動かして試す」という発想がどう本格化していくかは、
それらの単位が教材に開放されたときの楽しみに取っておいてください。
