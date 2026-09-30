# Lab 5 提案に答える: LATCH応答系をcurlで動かして成立までを見る

- 種別: チュートリアル(手を動かして必ず成功体験を得る。壊しません)
- 前提知識: 第17章(LATCH応答系の全体)・第14章(提案がどう作られるか)。Lab 1の
  psql・redis-cli の使い方と、Lab 4のトークン発行・Intent保存の手順は思い出せると
  スムーズです(同じコマンドを使います)
- 所要目安: 30〜40分
- このLabでできるようになること: 提案への回答APIを自分の手で叩いて、
  partial_accept(1人目のyes)からmatched(成立)までを観察できる。成立後にだけ
  解放される情報・409の断られ方・遷移の台帳(latch_status_events)・Calibration
  レコードの中身を、実データで確認できる
- 次に読むもの: なし(ここまでが、いま実装済みの範囲。期限切れを掃除する
  expiry_sweeperと通知の章は、実装が進めば追加されます)

## 0. このLabで何をするか

第17章で、提案への返事がシステムを動かす仕組みを読みました。FOR UPDATEが並びを
整え、条件付きUPDATEが検品し、全員のYESが揃うと世界が一気に確定する——と。
このLabでは、そのすべてを**自分の手で起こして観察します**。

手順の性質上、1点だけ普段と違うことがあります。提案(latches行)を作るのは
Workerのマッチングパイプラインの仕事で、その全体を走らせるのは大がかりです
(第11章〜第15章の道のり)。そこでこのLabでは、**提案がもう届いている状態から
始めます**。latchesの行をpsqlで直接1行作り、そこから先(回答・成立・記録)を
本物のAPIで進めます。これはLATCHの開発現場でも同じやり方です。応答系の
試験(`backend/tests/integration/test_latches_api.py`)も、latches行はfixtureで
直接INSERTして始まります。主役以外の舞台を作るのに、主役を呼ぶ必要はない。

以下は2026-09-30に実行して確認した手順です。compose常設環境(api・db・redis)が
動いている前提です。動いていなければ `make up` してから始めてください
(Lab 1の§1)。

```bash
make ps    # api と db が (healthy) であることを確認
```

期待される出力(該当行だけ抜粋):

```
latch-ci-api-1   …   Up … (healthy)   127.0.0.1:8000->8000/tcp
latch-ci-db-1    …   Up … (healthy)   127.0.0.1:5432->5432/tcp
```

## 1. 二人のユーザーを用意する

登場人物は2人。Lab 4と同じ手順で、開発用トークンを発行して交換して、ユーザー
登録まで済ませます。backend ディレクトリで実行します。

```bash
cd /home/misty/Projects/latch/backend
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab5a > /tmp/lab5-idp-a.txt
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab5b > /tmp/lab5-idp-b.txt
```

出力はありません(ファイルへ保存しました)。続いて、トークンを交換して
access_token を得て、ユーザー登録まで一気にやります。2人分を続けて実行します。

```bash
for s in a b; do
  ACCESS=$(curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
    -H "Content-Type: application/json" \
    -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab5-idp-$s.txt)\"}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
  echo "$ACCESS" > /tmp/lab5-access-$s.txt
  curl -s -o /dev/null -w "lab5$s users: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/users \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"display_name":"Lab5'$s'","birth_date":"1990-04-01","profile":{}}'
done
```

期待される出力:

```
lab5a users: 201
lab5b users: 201
```

`for s in a b` は「aとbで同じことを繰り返す」の意味です(Lab 4では省略して
1人ずつ書きましたが、2人いるのでここでは繰り返しにまとめました)。
access_token は1時間で切れます。途中で401が出たら、§1の発行と交換をやり直して
ください。

ここから先の§2も backend で実行しますが、§3以降の `docker compose exec` は
リポジトリのルート(`latch/`)で実行するコマンドです(compose.yamlのある場所。
第1章1.11)。§3に入る前に、こうして戻っておいてください。

```bash
cd /home/misty/Projects/latch
```

## 2. 二人のIntentを、未来の時刻で保存する

次に、2人それぞれの「やりたいこと」を保存します。ここで3つの工夫が要ります。

- **statusはactive** — draftではマッチングの対象になりません
- **time.startは未来** — 保存する時点で7日以内・かつ未来である必要があります
  (第6章6.3)。Stubのparseが返す日付は固定(2026-09-27)でもう過去なので、
  ここではstructured_intentを手で書いて未来の時刻を入れます(Lab 4の道3と同じ工夫)
- **secondaryは二人とも「焼肉」** — あとでCalibrationのsegmentを確認するとき、
  語彙が一致するこの2人ならlexicalと判定されるはず、を見るためです(17.7)

未来の時刻を変数に入れてから、2人分を保存します。

```bash
START=$(python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(hours=120)).replace(microsecond=0).isoformat())")
echo "$START"
```

期待される出力(実行した日時で変わります。今から120時間後=5日後):

```
2026-10-05T13:24:28+00:00
```

```bash
for s in a b; do
  ACCESS=$(cat /tmp/lab5-access-$s.txt)
  curl -s -o /tmp/lab5-intent-$s.json -w "lab5$s intents: %{http_code}\n" \
    -X POST http://127.0.0.1:8000/v1/intents \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"raw_text":"Lab5用:今度の金曜、天文館のあたりで焼肉を食べたい","status":"active","structured_intent":{"category":{"primary":"meal","secondary":"焼肉"},"alcohol_involved":false,"time":{"start":"'$START'","end":null},"location":{"name":"天文館"}}}'
done
```

期待される出力:

```
lab5a intents: 201
lab5b intents: 201
```

保存したIntentのidを、これから何度も使うので変数に入れておきます。

```bash
INTENT_A=$(python3 -c "import json; print(json.load(open('/tmp/lab5-intent-a.json'))['intent']['id'])")
INTENT_B=$(python3 -c "import json; print(json.load(open('/tmp/lab5-intent-b.json'))['intent']['id'])")
echo "A: $INTENT_A"
echo "B: $INTENT_B"
```

期待される出力(値は環境ごとに違います):

```
A: 122f740f-f559-4fa2-b169-b843a6069811
B: f0b34124-5b5b-4b01-9965-620967e720d4
```

**観察(やらなくても先に進めます)**。実はこの2本のIntent、保存した瞬間から
裏で本物の処理が走っています。常設のworkerが、あなたのIntentの組み合わせを
評価しているのです(第9章〜第14章の道のり)。20秒ほど待ってから、評価の記録
(match_candidates・第11章)を見てみましょう。

```bash
sleep 20
docker compose exec -T db psql -U latch -d latch -c "
SELECT status, latch_score IS NOT NULL AS has_ls, jev_result IS NOT NULL AS has_jev
FROM match_candidates
WHERE intent_a_id IN ('$INTENT_A'::uuid,'$INTENT_B'::uuid)
  AND intent_b_id IN ('$INTENT_A'::uuid,'$INTENT_B'::uuid);"
```

期待される出力(評価が終わっていれば1行):

```
 status  | has_ls | has_jev
---------+--------+----------
 evaluated | t      | t
(1 row)
```

まだ `(0 rows)` なら、評価が進むまで少し待ちます。この評価行が何に効くかは、
§9でCalibrationを作るときに見えます。

## 3. 提案を1行、用意する

いよいよlatchesへ行を作ります。Lab 1の流儀でpsqlを開いて、1行INSERTします。

```bash
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO latches
  (intent_ids, group_candidate_id, proposal, score, status,
   response_deadline, expires_at, created_at)
VALUES
  (ARRAY['$INTENT_A'::uuid, '$INTENT_B'::uuid],
   NULL,
   '{\"headcount\": 2, \"match_level\": \"medium\"}'::jsonb,
   0.85, 'proposed',
   now() + interval '2 hours',
   now() + interval '120 hours',
   now())
RETURNING id, status, response_deadline;"
```

期待される出力(時刻は実行時によります):

```
                  id                  | status  |     response_deadline
--------------------------------------+---------+---------------------------
 7224031c-7607-414b-8101-c530a7d2e64c | proposed | 2026-09-30 15:25:03.308262+00
(1 row)
```

SQLの読み方をしておきます。WorkerのLayer 5が書き込む列に、それらしい値を手で
入れています。`status='proposed'` は「提示済み・回答待ち」。`response_deadline` は
回答期限(2時間後)。`expires_at` はIntentの寿命と揃えた期限(120時間後)。
`proposal` は提案の中身(第14章14.4)。`score` は提示時のlatch_score。ここでは
0.85にしておきます(あとで§7のCalibrationでこの値が効きます)。`group_candidate_id`
は1対1ならNULLです(グループ提案だけで入る・第15章)。

作れた行のidを変数に入れます。

```bash
LATCH=$(docker compose exec -T db psql -U latch -d latch -tA -c \
  "SELECT id FROM latches WHERE intent_ids @> ARRAY['$INTENT_A'::uuid] AND status='proposed';")
echo "LATCH=$LATCH"
```

期待される出力:

```
LATCH=7224031c-7607-414b-8101-c530a7d2e64c
```

(`-tA` は「ヘッダーと行の詰めを消して値だけを出す」オプションです)

## 4. 1人目のyes: 提案は「部分受諾」へ進む

Aさんの立場で、参加すると返事をします。**実行する前に、応答がどうなるか予測を
書いてください**(statusは? my_responseは? remaining_responsesは?)。第17章17.2と
17.5を読んだばかりなら、予測できるはずです。

```bash
ACCESS=$(cat /tmp/lab5-access-a.txt)
curl -s -X POST http://127.0.0.1:8000/v1/latches/$LATCH/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"response":"yes"}' | python3 -m json.tool --no-ensure-ascii
```

期待される出力:

```json
{
  "latch": {
    "id": "7224031c-7607-414b-8101-c530a7d2e64c",
    "status": "partial_accept",
    "response_deadline": "2026-09-30T15:25:03.308262Z",
    "expires_at": "2026-10-05T13:25:03.308262Z",
    "created_at": "2026-09-30T13:25:03.308262Z",
    "completed_at": null,
    "proposal": {
      "headcount": 2,
      "match_level": "medium"
    },
    "is_group": false,
    "my_response": "yes",
    "remaining_responses": 1
  }
}
```

読みどころは3つです。

- `status` は **partial_accept**。1人目のyesではまだ成立しません(1対1なら2人必要)
- `remaining_responses` は **1**。「あと1人の回答が必要」です。誰が答えたかは
  出ていません——第17章17.2で読んだ情報最小化そのものです
- 応答のどこを探しても、Bさんのuser_idや回答状況はありません

## 5. 2人目のyes: 成立(matched)の瞬間

続いてBさん。同じ窓口に、同じyesを送ります。

```bash
ACCESS=$(cat /tmp/lab5-access-b.txt)
curl -s -X POST http://127.0.0.1:8000/v1/latches/$LATCH/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"response":"yes"}' | python3 -m json.tool --no-ensure-ascii
```

期待される出力(§4と同じ形なので、変わった行だけを見てください):

```json
{
  "latch": {
    "id": "7224031c-7607-414b-8101-c530a7d2e64c",
    "status": "matched",
    "response_deadline": "2026-09-30T15:25:03.308262Z",
    "expires_at": "2026-10-05T13:25:03.308262Z",
    "created_at": "2026-09-30T13:25:03.308262Z",
    "completed_at": null,
    "proposal": {
      "headcount": 2,
      "match_level": "medium"
    },
    "is_group": false,
    "my_response": "yes",
    "remaining_responses": 0
  }
}
```

`status: "matched"`。この1行の応答が返るまでの間に、DBの中では(第17章17.5で
読んだとおり)主な4つのことが**同じトランザクションで**起きています。
responsesへの追記・statusの更新・2人のIntentのmatched化・(もしあれば)競合
クローズ(このほか遷移の記録とCalibrationも同じトランザクションです。§8・§9で
見ます)。このあと§6で中身を見て確かめます。

## 6. 成立したら見せられるものがある: 詳細API

成立後、Aさんとして詳細を取り直します。**ここで初めて**、相手の情報が解放
されます(第17章17.6)。

```bash
ACCESS=$(cat /tmp/lab5-access-a.txt)
curl -s http://127.0.0.1:8000/v1/latches/$LATCH \
  -H "Authorization: Bearer $ACCESS" | python3 -m json.tool --no-ensure-ascii
```

期待される出力(user_idの値は環境ごとに違います):

```json
{
  "latch": {
    "id": "7224031c-7607-414b-8101-c530a7d2e64c",
    "status": "matched",
    "response_deadline": "2026-09-30T15:25:03.308262Z",
    "expires_at": "2026-10-05T13:25:03.308262Z",
    "created_at": "2026-09-30T13:25:03.308262Z",
    "completed_at": null,
    "proposal": {
      "headcount": 2,
      "match_level": "medium"
    },
    "is_group": false,
    "my_response": "yes",
    "remaining_responses": 0,
    "participants": [
      {
        "user_id": "55b8a6c4-0817-4db0-8a1e-434547a688d3",
        "display_name": "Lab5a",
        "profile": {}
      },
      {
        "user_id": "32d3a2b4-29fc-47ec-8821-7ed1324c6917",
        "display_name": "Lab5b",
        "profile": {}
      }
    ],
    "time_summary": "2026-10-05 22:24",
    "area_name": "鹿児島市クックレモン"
  }
}
```

§4・§5の応答にはなかった3つの鍵が現れました。

- `participants` — 2人の表示名。§4の段階では、この情報はAPIの外に出ませんでした
- `time_summary` — 「2026-10-05 22:24」。§2で指定したtime.start(120時間後)を
  JSTで整形したものです
- `area_name` — 「鹿児島市クックレモン」。2人のIntentの位置(天文館)の中点を
  逆ジオコードした地名です(第17章17.6)

一覧も見ておきましょう。

```bash
curl -s http://127.0.0.1:8000/v1/latches -H "Authorization: Bearer $ACCESS" \
  | python3 -m json.tool --no-ensure-ascii | head -8
```

期待される出力:

```json
{
    "items": [
        {
            "id": "7224031c-7607-414b-8101-c530a7d2e64c",
            "status": "matched",
            ...
```

自分が関与しているLATCHだけが並びます。`cursor` を指定しない1頁目は既定で
20件まで(05 §5)——このLabでは1件しかありません。

## 7. 断られ方を体験する: 409・422・403・404

回答APIは、頼みを断るときも理由をcodeで教えてくれます(第17章17.4)。4通り、
まとめて体験します。**それぞれ、何が返るか予測してから実行してください**。

**二重回答(409)**。Aさんがもう一度yesを送ります。

```bash
ACCESS=$(cat /tmp/lab5-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" -d '{"response":"yes"}'
```

期待される出力:

```
{"error":{"code":"ALREADY_ANSWERED","message":"already answered","details":null}}
HTTP 409
```

**値が不正(422)**。yes/no/defer以外を送ります。

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" -d '{"response":"maybe"}'
```

期待される出力:

```
{"error":{"code":"VALIDATION_ERROR","message":"request validation failed","details":null}}
HTTP 422
```

**参加者以外(403)**。第三者のCさんを作って、Cさんの立場で同じlatchに回答します。
トークンの発行だけ backend ディレクトリで実行します(§1と同じです)。

```bash
cd /home/misty/Projects/latch/backend
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab5c > /tmp/lab5-idp-c.txt
ACCESS_C=$(curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
  -H "Content-Type: application/json" \
  -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab5-idp-c.txt)\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
curl -s -o /dev/null -w "lab5c users: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/users \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS_C" \
  -d '{"display_name":"Lab5c","birth_date":"1990-04-01","profile":{}}'
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS_C" -d '{"response":"yes"}'
```

期待される出力:

```
lab5c users: 201
{"error":{"code":"FORBIDDEN","message":"not a participant","details":null}}
HTTP 403
```

**不在(404)**。存在しないlatchのidに回答します。

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/00000000-0000-0000-0000-000000000000/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS_C" -d '{"response":"yes"}'
```

期待される出力:

```
{"error":{"code":"NOT_FOUND","message":"latch not found","details":null}}
HTTP 404
```

4つの応答を見比べてください。エラーの形式はどれも
`{"error": {"code", "message", "details"}}` で同じ(05 §5)。クライアントは
HTTPステータスでなくcodeで分岐できます(第8章8.5)。403が「見つからない」と
偽らない点にも注目してください——存在は隠さず、「あなたは関係ない」と伝えます。

ここでまたディレクトリを戻します。§8以降の `docker compose exec` は
リポジトリのルートで実行します。

```bash
cd /home/misty/Projects/latch
```

## 8. 台帳を開く: 遷移の記録とIntentの引退

ここまでAPIの応答だけを見てきました。DBの中身も確認します。

**latch_status_events(遷移の台帳)**。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT from_status, to_status, user_id IS NOT NULL AS by_user, created_at
FROM latch_status_events WHERE latch_id = '$LATCH'::uuid
ORDER BY created_at;"
```

期待される出力:

```
 from_status  |   to_status    | by_user |          created_at
--------------+----------------+---------+------------------------------
 proposed     | partial_accept | t       | 2026-09-30 13:25:11.251525+00
 partial_accept | matched      | t       | 2026-09-30 13:25:21.321147+00
(2 rows)
```

返事のたびに1行ずつ、from→toが記録されています。`by_user` はuser_idが入って
いるかどうか(t=ユーザーの返事が引き金)。競合クローズのようなシステム判断の
遷移は、ここがNULLになります(第17章17.5)——通知の実装(ws-3)はこの台帳を
見て「何が起きたか」を知る予定です。

**intents(2人の引退)**。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT id, status FROM intents WHERE id IN ('$INTENT_A'::uuid, '$INTENT_B'::uuid);"
```

期待される出力:

```
                  id                  | status
--------------------------------------+---------
 122f740f-f559-4fa2-b169-b843a6069811 | matched
 f0b34124-5b5b-4b01-9965-620967e720d4 | matched
(2 rows)
```

§5の回答1本で、2人のIntentが active から matched へ変わっています。もう
マッチング市場にいません。

## 9. Calibrationレコードを作って、中身を見る

最後に、第17章17.7で読んだCalibrationを実データで確認します。

まず、§5でmatchedにした1本目のlatch($LATCH)のレコードを見てみます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT prediction->>'segment' AS segment, prediction->>'L' AS L, matched
FROM calibration_records WHERE latch_id = '$LATCH'::uuid;"
```

ここから先は、環境によって2通りの結果があり得ます。**どちらも正しい挙動です**。

**レコードがある場合**(2026-09-30の検証環境ではこうでした):

```
 segment |  L   | matched
---------+------+---------
 lexical | 0.85 | t
(1 row)
```

これは、§2で保存したあなたのIntentを、常設のworkerが本物のパイプラインで
評価していたからです。Intentが保存されると、第9章〜第14章の道のり(知らせ→
Embedding→Layer 4の評価)が裏で走り、match_candidatesへ評価行が書かれます
(§2の観察で見た行です)。回答がmatchedへ確定した瞬間、その評価行がpredictionの
材料になりました。segmentがlexicalなのは、§2で二人のsecondaryをどちらも
「焼肉」に揃えたから(第17章17.7)。

**レコードがない場合**(0行): workerの評価がまだ終わっておらず、評価行から
predictionの材料を特定できなかった、という道です。このとき回答の受理自体は
成功していて、代わりにapiのログへ1行だけ証拠が残ります。

```bash
docker compose logs api 2>&1 | rg "latch.calibration.missing" | tail -3
```

`latch.calibration.missing latch_id=…` という形式の行が並びます(共有環境では、
過去に走った試験などの同じ形式の行も混ざっています。あなたの $LATCH の
UUIDと一致する行を探してください。レコードが作られた場合は、この行は
**増えていません**)。

どちらに転んでも損得はありません。**本編(回答の受理)は、記録の材料がなくても
成功する**——第17章17.7で読んだ「学習の材料集めは、本編を邪魔しない」優先順位の
実演そのものです。

では、レコードが作られる経路を確実に見ます。評価行を自分で用意したうえで
2本目のlatchesを作り、noで断ります。手順は§2と同じです(2人のIntentをもう1本ずつ)。

```bash
for s in a b; do
  ACCESS=$(cat /tmp/lab5-access-$s.txt)
  curl -s -o /tmp/lab5-intent2-$s.json -w "lab5$s intents2: %{http_code}\n" \
    -X POST http://127.0.0.1:8000/v1/intents \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"raw_text":"Lab5用2本目:焼肉を食べたい","status":"active","structured_intent":{"category":{"primary":"meal","secondary":"焼肉"},"alcohol_involved":false,"time":{"start":"'$START'","end":null},"location":{"name":"天文館"}}}'
done
INTENT2_A=$(python3 -c "import json; print(json.load(open('/tmp/lab5-intent2-a.json'))['intent']['id'])")
INTENT2_B=$(python3 -c "import json; print(json.load(open('/tmp/lab5-intent2-b.json'))['intent']['id'])")
echo "A2: $INTENT2_A / B2: $INTENT2_B"
```

期待される出力:

```
lab5a intents2: 201
lab5b intents2: 201
A2: 8b5ab3bf-6207-4162-b77a-22d3e647a2bf / B2: f8bf6a50-9a4a-47dc-a6ff-a1ddcd2ed704
```

評価行をmatch_candidatesへ入れます。これは第13章でLayer 4が書く行の、試験用の
複製です(would_a_accept_b=0.9・would_b_accept_a=0.85)。`latch_score` を §3 と
同じ0.85に揃えるのがコツで、これで「latches.scoreと一致する評価行」の特定が
一発で決まります(第17章17.7の第1段)。`LEAST`/`GREATEST` は、実物のパイプライン
と同じ「小さいUUIDをaにする」正規化(第11章)です。§2の観察でworkerの評価行が
すでに見えていた場合も心配いりません。文末の `ON CONFLICT … DO UPDATE` が、
同じ組み合わせの行をこの値で上書きします。

```bash
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO match_candidates
  (intent_a_id, intent_b_id, intent_a_version, intent_b_version,
   retrieval_score, cheap_judge_score, jev_result, latch_score, status,
   created_at, updated_at)
VALUES
  (LEAST('$INTENT2_A'::uuid,'$INTENT2_B'::uuid),
   GREATEST('$INTENT2_A'::uuid,'$INTENT2_B'::uuid),
   1, 1, 0.9, 0.9,
   '{\"would_a_accept_b\": 0.9, \"would_b_accept_a\": 0.85,
      \"jev_5axis\": {\"purpose_fit\": {\"value\": 0.5, \"confidence\": 0.9},
                     \"mood_fit\": {\"value\": 0.5, \"confidence\": 0.8},
                     \"timing_fit\": {\"value\": 0.5, \"confidence\": 0.7},
                     \"social_fit\": {\"value\": 0.5, \"confidence\": 0.6},
                     \"latent_yes\": {\"value\": 0.5, \"confidence\": null}},
      \"provider\": \"typesafe_jev\", \"model\": \"jev-1.13.0\"}'::jsonb,
   0.85, 'evaluated', now(), now())
ON CONFLICT (intent_a_id, intent_b_id, intent_a_version, intent_b_version)
DO UPDATE SET jev_result = EXCLUDED.jev_result, latch_score = EXCLUDED.latch_score,
              status = 'evaluated', updated_at = EXCLUDED.updated_at
RETURNING latch_score;"
```

期待される出力:

```
 latch_score
-------------
        0.85
(1 row)
```

2本目のlatchesを作って、Aさんがnoで断ります。

```bash
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO latches
  (intent_ids, group_candidate_id, proposal, score, status,
   response_deadline, expires_at, created_at)
VALUES
  (ARRAY['$INTENT2_A'::uuid, '$INTENT2_B'::uuid],
   NULL, '{\"headcount\": 2, \"match_level\": \"medium\"}'::jsonb,
   0.85, 'proposed',
   now() + interval '2 hours', now() + interval '120 hours', now())
RETURNING id;"
LATCH2=$(docker compose exec -T db psql -U latch -d latch -tA -c \
  "SELECT id FROM latches WHERE intent_ids @> ARRAY['$INTENT2_A'::uuid] AND status='proposed';")
ACCESS=$(cat /tmp/lab5-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH2/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" -d '{"response":"no"}'
```

期待される出力:

```
{"latch":{"id":"0c3396ff-…","status":"rejected","response_deadline":"…","expires_at":"…","created_at":"…","completed_at":null,"proposal":{"headcount":2,"match_level":"medium"},"is_group":false,"my_response":"no","remaining_responses":2}}
HTTP 200
```

`status: "rejected"`。noは1人でも即不成立です(第17章17.5)。
calibration_recordsを見ます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT prediction, matched, jsonb_array_length(actual_responses::jsonb) AS answers
FROM calibration_records WHERE latch_id = '$LATCH2'::uuid;" | head -8
```

期待される出力(1行。長いので折り返されます):

```
                                                       prediction                                                       | matched | answers
----------------------------------------------------------------------------------------------------------------------+---------+---------
 {"L": 0.85, "model": "jev-1.13.0", "segment": "lexical", "provider": "typesafe_jev", "jev_5axis": {…}, "MutualScore": 0.85, "would_a_accept_b": 0.9, "would_b_accept_a": 0.85} | f       |       1
(1 row)
```

predictionの中に、いまINSERTした評価の写しが並んでいます。

- `would_a_accept_b: 0.9` / `would_b_accept_a: 0.85` / `MutualScore: 0.85` —
  match_candidatesから写した「予測」
- `L: 0.85` — 提示時のlatch_score(latches.score)
- `segment: "lexical"` — §2で二人のsecondaryを「焼肉」で揃えた結果です。文字bigram
  の重なりがあったのでlexical。もし「焼肉」と「イタリアン」にしていれば
  semanticになっていたはずです(第17章17.7)
- `matched: f` / `answers: 1` — 「実際」側。noで断られた1件の回答

予測と実際が同じ行に並んだ。この行が積み重なれば「0.9と予測した組み合わせは
実際に何%成立するか」が計算できる——それがCalibrationの目的でした。

## 10. 掃除する

このLabが作ったデータを消して、環境を元に戻します。subjectを `learn-lab5` で
始まるユーザーに絞って、FKの順序(参照されている側から)で消していきます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
DELETE FROM latch_status_events WHERE latch_id IN
  (SELECT l.id FROM latches l WHERE l.intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab5%')));
DELETE FROM calibration_records WHERE latch_id IN
  (SELECT l.id FROM latches l WHERE l.intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab5%')));
DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab5%');
DELETE FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab5%'));
DELETE FROM match_candidates WHERE intent_a_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab5%'))
   OR intent_b_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab5%'));
DELETE FROM match_events WHERE source_intent_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab5%'));
DELETE FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab5%');
DELETE FROM users WHERE auth_subject LIKE 'learn-lab5%';
SELECT (SELECT count(*) FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab5%'))) AS latches_left,
       (SELECT count(*) FROM users WHERE auth_subject LIKE 'learn-lab5%') AS users_left;"
```

期待される出力(DELETEの行数は、workerが裏で書いた評価行などの分だけ環境ごとに
多少変わります。最後の2行が0になれば成功です):

```
DELETE 3
DELETE 2
DELETE 0
DELETE 2
DELETE 2
DELETE 8
DELETE 4
DELETE 3
 latches_left | users_left
--------------+------------
            0 |          0
(1 row)
```

なぜAPIの `DELETE /v1/intents` で掃除しないのか。matchedなIntentは削除APIの
対象外だからです(削除APIはdraft・active・pausedだけが対象・第6章6.5)。成立済み・不成立済みの
学習データは、開発環境ではこうしてDBから直接消します。試験環境でも同じ発想で、
integration試験のteardownがFK順のDELETEを行っています(test_latches_api.pyの
`field` fixture)。

## 11. 振り返りとノート

このLabで、あなたは第17章の全体を手でなぞりました。curl 1本ごとに、DBの状態と
応答がどう変わったか——自分のノート(my-notes.md)に、次の3つを書き残すのを
おすすめします。

1. **partial_acceptの応答を見て、残り1人だと分かった根拠**。どの鍵を見ましたか
2. **matchedの前に、誰にも見えていなかった情報**。participants・time_summary・
   area_nameは、なぜ§4の段階では出なかったのか
3. **calibration_recordsのpredictionを見て、予測と実際がどこに並んだか**。
   segmentがlexicalになった理由

疑問が残ったら、それもノートに書いてください。次の章(expiry_sweeperと通知)は、
このLabで「まだ実装されていない」とした部分——期限が切れた提案を掃除する
処理と、成立・不成立を知らせる通知——を埋める単位です。

## 12. このLabの対象になったコード

読み返すときの対応表です。

| このLabで起きたこと | コード |
|---|---|
| 回答の受付と成立の連鎖 | `backend/src/latch/latches/service.py`(respond と _on_matched) |
| FOR UPDATE読取・条件付きUPDATE | `backend/src/latch/latches/store.py`(_SELECT_LATCH_FOR_UPDATE・_UPDATE_RESPONSE) |
| 409/422/403/404の分類 | `service.py`(手順2)と `errors.py` |
| 解放情報の組み立て | `service.py`(get。participants・time_summary・area_name) |
| Calibrationの組立 | `backend/src/latch/latches/calibration.py` |
| 遷移の台帳 | テーブル `latch_status_events`(挿入は store.insert_latch_event) |
