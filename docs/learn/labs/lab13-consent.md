# Lab 13 同意の関所を、自分の手で通る: 422で断られ、同意し、開かれるまで

- 種別: チュートリアル(手を動かして成功体験を得る)
- 前提知識: Lab 1(curlの5つの窓)・Lab 4(HTTPの送り方)・第5章(422と503)・
  第6章(draftとactive)・第11章(Layer 1)・**第27章**(このLabの読み物です)
- 前提の環境: `make up` でci環境(5サービス)が動いていること。**マージ後すぐに
  試す場合は、`docker compose build api worker` でイメージを作り直してから
  `make up` し直してください**(起動済みの容器は古いコードのまま動き続けます。
  Lab 6冒頭と同じ注意です)
- このLabを終えるとできること:
  - 同意なしでは parse・active作成・active化が422 CONSENT_REQUIREDで断られることを、
    自分の目で確認できる
  - draft(下書き)だけが同意なしで保存できることを確認できる
  - 同意APIの窓口(GET/POST /v1/users/me/consent)を叩き、DBの1列に日時が入る
    ところまで追える
  - ログ許可リストの機械検査(番人試験)を自分で走らせ、グリーンを確認できる
- 手順の所要: 約10分。費用はゼロです(LLMはstubモード。外部APIを呼びません)

## 0. このLabで何をするか

第27章で読んだ「同意の2層の防御」を、curlで一巡します。

流れはこうです。まず**同意しない**ユーザーを1人作り、LLM送信の入口(parse)と
マッチングへの入口(active作成)が断られることを見ます。次に、draftだけが
通ること——LLM送信ゼロの経路は開いている——を確かめます。そこで同意APIを叩き、
もう一度同じ入口を叩くと開く。最後に、DBの`consent_at`列と、番人試験を覗いて
終わりです。

作業の記録を取るユーザー名は`learn-lab13a`です。もう1人いたら名前を変えて
やり直してください(第2章の「変えて→予測→確かめる」も、このLabで効きます)。

自分のノート(`my-notes.md`)を用意してください。「わかったこと」と「疑問」を
手順の間に書き足していきます。

## 1. 同意しないユーザーを1人作る

まず、このLabの学習者になるユーザーのトークンを発行します。`backend/` で実行します
(Lab 7と同じ作法。トークンの発行はローカルの計算で、通信はしません)。

```bash
cd backend
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab13a > /tmp/lab13-idp-a.txt
head -c 40 /tmp/lab13-idp-a.txt; echo "...(省略)"
```

`python -m latch.auth issue-idp-token` は「認証の練習用に、外部IdP(Google)が
発行したことにする仮の身元トークンを作る」コマンドです。実行すると、長い
文字列が1行だけ出ます。

**期待される出力**(先頭40文字のみ表示。値は実行ごとに変わります):

```text
eyJhbGciOiJSUzI1NiIsImtpZCI6InRlc3QtaWRw...(省略)
```

この身元トークンをLATCHに渡して、アクセストークン(第8章8.4の「鍵」)に
交換します。これが `POST /v1/auth/token` です。

```bash
curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
  -H "Content-Type: application/json" \
  -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab13-idp-a.txt)\"}" \
  > /tmp/lab13-token.json
head -c 120 /tmp/lab13-token.json; echo "...(省略)"
```

**期待される出力**:

```json
{"access_token":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJsYXRjaC1hcGkiLCJhdWQiOiJsYXRjaC1hcHAiLCJzdWIiOiJnb29nbG...(省略)"}
```

次に、このユーザーをLATCHに登録します。**ここで同意はしません**——それがこのLabの
前提です。ユーザー登録は第4章の`POST /v1/users`です。トークンは長いので、
シェルの変数(作業中に使える名前の付いた入れ物)に入れておくと、あとの手順が
楽になります。

```bash
ACCESS=$(python -c 'import json;print(json.load(open("/tmp/lab13-token.json"))["access_token"])')
curl -s -w "\n[HTTP %{http_code}]\n" -X POST http://127.0.0.1:8000/v1/users \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"display_name":"lab13","birth_date":"1990-04-01","profile":{}}'
```

`-w "\n[HTTP %{http_code}]\n"` は、curlに「応答のステータスコード(3桁の数字)を
最後に表示して」と頼むオプションです。

**期待される出力**:

```json
{"user":{"id":"fa8add4c-f669-4099-b065-322bedc92e34","display_name":"lab13"}}
[HTTP 201]
```

201(成功・作成された)。**idの値はあなたの実行で別の文字列になります**。
登録できました。まだ誰も同意していません。

## 2. 同意なしで入口を叩く: 2つの422

いよいよです。まず、LLM送信の入口であるparseを叩きます(第5章のAPI。stubモード
なので外部AIは呼ばれません)。

```bash
curl -s -w "\n[HTTP %{http_code}]\n" -X POST http://127.0.0.1:8000/v1/intents/parse \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"text":"今夜 天文館で飲みたい"}'
```

**期待される出力**:

```json
{"error":{"code":"CONSENT_REQUIRED","message":"consent required","details":null}}
[HTTP 422]
```

422で、codeが`CONSENT_REQUIRED`。文章は300字以内・内容にも問題がないのに、
断られました。断ったのは第27章27.3の第1層。LLMに送る**前**に止まったこと
(エラーまでLLMの出力に触れる余地がなかったこと)が、何よりの証拠です。

次に、マッチングに乗る入口のactive作成も叩きます(第6章の`POST /v1/intents`)。

```bash
curl -s -w "\n[HTTP %{http_code}]\n" -X POST http://127.0.0.1:8000/v1/intents \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"raw_text":"今夜 天文館で飲みたい","structured_intent":{"category":{"primary":"drinking","secondary":null},"alcohol_involved":false,"time":{"start":"2026-10-03T21:00:00+09:00","end":null},"location":{"name":"天文館"}}}'
```

**期待される出力**:

```json
{"error":{"code":"CONSENT_REQUIRED","message":"consent required","details":null}}
[HTTP 422]
```

同じcodeで断られました。こちらはLLMを呼ばない経路ですが、保存と同時にEventが
発行され、Embedding(外部送信)へつながる。だから入口で閉じてあります。

**疑問の種**: この2つの422、同じcodeです。フロントエンドはこのcodeだけで
「同意画面へ誘導」と分岐できます(第27章27.5)。もしcodeがなく
`message`の文章だけだったら、画面の分岐はどう書くことになるか、
ノートに書いてみてください。

## 3. draftだけは通る

同じ内容を、今度はdraft(下書き)として送ります。リクエストに`"status":"draft"`
を1つ足すだけです。

```bash
curl -s -w "\n[HTTP %{http_code}]\n" -X POST http://127.0.0.1:8000/v1/intents \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"raw_text":"今夜 天文館で飲みたい","status":"draft","structured_intent":{"category":{"primary":"drinking","secondary":null},"alcohol_involved":false,"time":{"start":"2026-10-03T21:00:00+09:00","end":null},"location":{"name":"天文館"}}}'
```

**期待される出力**(`id`は実行ごとに変わります。**次の手順で使うので控えてください**):

```json
{"intent":{"id":"1ad41438-6188-4c07-a5ef-d8421cac3f7d","status":"draft",... ...(中略)... }}
[HTTP 201]
```

201で通りました。第27章27.3のとおり——draftはLLMを呼ばずEventも出さないので、
「送信の条件」に当たらない。書きかけを自分の棚にしまうことは、同意していなくても
できるのです。

## 4. 同意の窓口を叩く

では、同意の窓口(第27章27.5)を叩きます。まず状態を聞くGETです。

```bash
curl -s -w "\n[HTTP %{http_code}]\n" http://127.0.0.1:8000/v1/users/me/consent \
  -H "Authorization: Bearer $ACCESS"
```

**期待される出力**:

```json
{"consented_at":null}
[HTTP 200]
```

200が返り、中身は`null`。「404ではなく、200とnullで『同意していない』を表す」のが
この窓口の約束でした。次に、いよいよ同意を記録します。

```bash
curl -s -w "\n[HTTP %{http_code}]\n" -X POST http://127.0.0.1:8000/v1/users/me/consent \
  -H "Authorization: Bearer $ACCESS"
```

**期待される出力**(日時は実行した時刻になります):

```json
{"consented_at":"2026-10-03T09:07:02.726512Z"}
[HTTP 200]
```

200で、同意した日時が返ってきました。`Z`で終わる時刻はUTC(世界標準時)です。
時計はClock由来(第3章)。もう一度GETを叩けば、今度はnullではなくこの日時が
返ります。何度押しても行が増えず日時が更新されるだけ(冪等・後勝ち)のことも、
POSTを繰り返して確かめてみてください。

## 5. 同意後、入口が開く

同じ鍵のままで、入口をもう一度叩きます。まずparse。

```bash
curl -s -w "\n[HTTP %{http_code}]\n" -X POST http://127.0.0.1:8000/v1/intents/parse \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"text":"今夜 天文館で飲みたい"}'
```

**期待される出力**(stubモードの固定応答。練習用の実装なので、入力によらず
この内容が返ります):

```json
{"structured_intent":{"category":{"primary":"meal","secondary":null},... ...(中略)... },"warnings":[]}
[HTTP 200]
```

200。断られていた入口が、同意1つで開きました。次に、§3で控えたdraftを
activeへ確定させます(PATCH。`status`をactiveにして全置換する、第6章の更新APIです)。
URLの末尾は、**§3であなたが控えたid**に差し替えてください(下の例の
`1ad41438-…`は、このLabを書いたときの実行のものです)。

```bash
curl -s -w "\n[HTTP %{http_code}]\n" -X PATCH http://127.0.0.1:8000/v1/intents/<§3で控えたid> \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"raw_text":"今夜 天文館で飲みたい","status":"active","structured_intent":{"category":{"primary":"drinking","secondary":null},"alcohol_involved":false,"time":{"start":"2026-10-03T21:00:00+09:00","end":null},"location":{"name":"天文館"}}}'
```

**期待される出力**(`id`はあなたが§3で控えた値):

```json
{"intent":{"id":"1ad41438-6188-4c07-a5ef-d8421cac3f7d","status":"active",... ...(中略)... }}
[HTTP 200]
```

200で`"status":"active"`。draftとして預けておいたものが、同意の後に確定できました。
同意前のPATCHは422だったはずです——試すなら、**同意しない2人目のユーザー**
(`learn-lab13b`)で§1〜§3をやり直し、draft作成後にPATCHを叩いてみてください。
422 CONSENT_REQUIREDが返り、GETを叩けばdraftのまま据え置かれているのが分かります
(統合試験`test_consent_api.py`の経路3が確かめている動きそのものです)。

## 6. DBの1列を見る

APIの向こう側で何が変わったか、DB(第1章1.7の「長期記憶」)で確認します。
リポジトリのルートに戻ってから実行してください。

```bash
docker compose exec -T db psql -U latch -d latch \
  -c "SELECT auth_subject, consent_at FROM users WHERE auth_subject LIKE '%lab13%';"
```

**期待される出力**(日時はあなたが同意した時刻):

```text
 auth_subject  |          consent_at           
---------------+-------------------------------
 learn-lab13a  | 2026-10-03 09:07:02.726512+00
(1 row)
```

§4のPOSTで返ってきた日時と、**同じ1行・同じ値**がここにあります。第27章27.2の
「同意はusers.consent_atの1列で持つ」の実体です。列の定義も見ておきましょう。

```bash
docker compose exec -T db psql -U latch -d latch \
  -c "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'consent_at';"
```

**期待される出力**:

```text
 column_name |        data_type         | is_nullable 
-------------+--------------------------+-------------
 consent_at  | timestamp with time zone | YES
(1 row)
```

`is_nullable`が`YES`(NULLを許す)なのが、27.2の「NULL=未同意」の土台です。
この列を作ったのはマイグレーション0007。`make migrate` でDB定義を最新にして
おけば、誰の環境でも同じ形に揃います。

## 7. 番人を走らせる

最後は、この単位のもう1つの成果——ログ許可リストの機械検査(第27章27.6)を
自分で走らせます。`backend/` で実行します。DBもAPIも使わないunit試験
(第3章3.4の「部品単体の試験」)なので、数秒で終わります。

```bash
cd backend
uv run pytest tests/unit/test_log_allowlist.py tests/unit/matching/test_layer_sql.py -v
```

**期待される出力**(最後の5行。途中の行は省略します):

```text
tests/unit/matching/test_layer_sql.py::test_layer1_where_requires_peer_consent PASSED [ 87%]
tests/unit/matching/test_layer_sql.py::test_upsert_pair_pins_on_conflict_and_returning PASSED [ 91%]
tests/unit/matching/test_layer_sql.py::test_upsert_pair_do_update_touches_scores_only PASSED [ 95%]
tests/unit/matching/test_layer_sql.py::test_upsert_pair_all_bind_params_recognized PASSED [100%]

============================== 24 passed in 0.04s ==============================
```

24件が全部PASSED(合格)。`test_log_allowlist.py`の5件がログ許可リストの番人で、
`test_layer_sql.py`の19件のうちの1件(`test_layer1_where_requires_peer_consent`)
が、Layer 1のSQLに「同意条件が入っているか」を見る番人です。**SQLの文字列を
試験が読んで検査している**ので、誰かがlayer1.pyから`AND u.consent_at IS NOT NULL`
の1行を消すと、この試験が赤くなって見つかります(第27章27.6の「教える代わりに、
忘れたら落ちる世界」の実例です)。

## 8. 振り返りとノート

`my-notes.md` に書き足して終わりです。

- §2と§5で、同じリクエストの応答がどう変わったか。変えたのは何1つですか
- draftだけが通った理由を、あなたの言葉で1文で
- 「SQLの文字列を試験が読む」仕組みについて、効くところと限界を1つずつ
- まだ分からないことが1つ。分からないままにしておく(第1章の原則です)

## このLabで叩いた窓口と応答のまとめ

| 手順 | 窓口 | 応答 |
|---|---|---|
| §2 | `POST /v1/intents/parse` | 422 CONSENT_REQUIRED(同意なし) |
| §2 | `POST /v1/intents`(active) | 422 CONSENT_REQUIRED(同意なし) |
| §3 | `POST /v1/intents`(draft) | 201(同意なしでも通る) |
| §4 | `GET /v1/users/me/consent` | 200 `{"consented_at":null}` |
| §4 | `POST /v1/users/me/consent` | 200(日時入・冪等・後勝ち) |
| §5 | `POST /v1/intents/parse` | 200(同意後) |
| §5 | `PATCH /v1/intents/{id}` | 200(同意後・active化) |

第27章27.3の遮断3経路のうち、このLabで直接叩いたのは2経路(parse・draft→active化)
です。残りの1経路(active作成での422→同意後の201)は、統合試験
`backend/tests/integration/test_consent_api.py`が同じ窓口で確かめています
(test-ciで毎回走る試験です。読むと、このLabで手で叩いたことと同じことを
機械が毎回確認しているのが分かります)。

後片付け: このLabが作ったユーザー(`learn-lab13a`・draftはactive化済み)は、
ci環境のDBに残ります。第1章1.9のgitの話にもありましたが、この環境は学習用です。
触りたければ、第22章の退会経路(削除API)を歩いて消すのも1つの学びです。
