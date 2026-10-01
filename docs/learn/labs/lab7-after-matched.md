# Lab 7 成立のあとを一巡する: チャットと実施自己申告

- 種別: チュートリアル(手を動かして必ず成功体験を得る。壊しません)
- 前提知識: 第20章(チャットと申告のAPI)・第19章(お知らせ一覧とプッシュ記録)。
  Lab 5のトークン発行・Intent保存・評価行の投入・latches行の直接INSERTと、
  Lab 6の時間を進める工夫(DBの値を過去へ書き換える)は思い出せるとスムーズです
  (同じコマンドを使います)
- 所要目安: 30〜40分(うち数分は、60秒周期のsweeperを待つ時間です)
- このLabでできるようになること: 成立(matched)したLATCHの**そのあと**を、
  自分の手で最初から最後まで歩ける。具体的には、チャットの送信(201)と取得
  (昇順・改頁)、ブロックで書く道だけが閉じる様子(409)、第三者の締め出し
  (403)、時間の経過による完了(completed)とプッシュのドライラン記録、
  完了後に読めるけれど書けないこと(409)、実施自己申告の受理(200)と二重回答
  (409)・3日窓(409)、そしてお知らせ一覧(LEFT JOIN)と既読(204)まで
- 次に読むもの: なし(ここまでが、いま実装済みの最先端です)

## 0. このLabで何をするか

第20章で、成立のあとの2つのAPIを読みました。チャットはstatus='matched'の
間だけ開き、申告はcompletedから3日以内・LATCH単位で先着1名。読んだだけの
状態では、これらは「そういうもの」です。このLabでは、**成立から申告までを
一筆書きで**歩きます。

道のりはこうです。

1. Lab 5と同じ手法で、2人にとっての「成立済み(matched)」を作る
2. matchedの間: チャットを送る・読む・ブロックで閉じる・第三者を締め出す
3. Lab 6と同じ手法で時間を進め、completedにする(プッシュ記録も見る)
4. completedのあと: 読めるけれど書けない。申告を1回だけ通す
5. お知らせ一覧に、申告の通知が並んでいるのを見る

使う道具はLab 5・6と同じです。curlでAPIを叩き、psqlでDBを書き換え、
`docker compose logs` でworkerのログを見ます。壊しません(データは最後に
全部消します)。

## 1. 三人のユーザーと、二人分のIntentを用意する

登場人物は3人。lab7a・lab7bの2人がLATCHを成立させ、lab7cは**参加していない
第三者**として、締め出し(403)を確かめる役です。Lab 5§1と同じ手順で、
backend ディレクトリで実行します。

```bash
cd /home/misty/Projects/latch/backend
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab7a > /tmp/lab7-idp-a.txt
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab7b > /tmp/lab7-idp-b.txt
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab7c > /tmp/lab7-idp-c.txt
```

トークンを交換して、3人分のユーザー登録まで一気にやります。

```bash
for s in a b c; do
  ACCESS=$(curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
    -H "Content-Type: application/json" \
    -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab7-idp-$s.txt)\"}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
  echo "$ACCESS" > /tmp/lab7-access-$s.txt
  curl -s -o /dev/null -w "lab7$s users: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/users \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"display_name":"Lab7'$s'","birth_date":"1990-04-01","profile":{}}'
done
```

期待される出力:

```
lab7a users: 201
lab7b users: 201
lab7c users: 201
```

2人のIntentを保存します。Lab 5§2と同じく、time.startは未来(120時間後)、
secondaryは2人とも「焼肉」。ここから先の `docker compose exec` はリポジトリの
ルート(`latch/`)で実行するので、Intentを保存したら戻っておいてください。

```bash
cd /home/misty/Projects/latch
START=$(python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(hours=120)).replace(microsecond=0).isoformat())")
for s in a b; do
  ACCESS=$(cat /tmp/lab7-access-$s.txt)
  curl -s -o /tmp/lab7-intent-$s.json -w "lab7$s intents: %{http_code}\n" \
    -X POST http://127.0.0.1:8000/v1/intents \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"raw_text":"Lab7用:今度の金曜、天文館のあたりで焼肉を食べたい","status":"active","structured_intent":{"category":{"primary":"meal","secondary":"焼肉"},"alcohol_involved":false,"time":{"start":"'$START'","end":null},"location":{"name":"天文館"}}}'
done
INTENT_A=$(python3 -c "import json; print(json.load(open('/tmp/lab7-intent-a.json'))['intent']['id'])")
INTENT_B=$(python3 -c "import json; print(json.load(open('/tmp/lab7-intent-b.json'))['intent']['id'])")
echo "A: $INTENT_A"
echo "B: $INTENT_B"
```

期待される出力(値は環境ごとに違います):

```
lab7a intents: 201
lab7b intents: 201
A: f1fe242e-4f18-48be-88e1-5eaf0a616c6e
B: b915000c-3c35-4d43-bda0-eaaea326e369
```

## 2. 提案を、成立まで持っていく

§8で申告を通すには、calibration_recordsの行が要ります(第20章20.5。ないと
503になります)。そこでLab 5§9の工夫を先に済ませます。**評価行を先に
投入してから**提案を作り、本物の回答APIで成立させれば、Calibrationの行が
確実に作られます。

評価行の投入はLab 5§9と同じSQLです(第13章のLayer 4が書く行の試験用複製。
latch_score=0.85が、このあと作るlatches行のscoreと一致するようにしてある
工夫も同じです)。

```bash
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO match_candidates
  (intent_a_id, intent_b_id, intent_a_version, intent_b_version,
   retrieval_score, cheap_judge_score, jev_result, latch_score, status,
   created_at, updated_at)
VALUES
  (LEAST('$INTENT_A'::uuid,'$INTENT_B'::uuid),
   GREATEST('$INTENT_A'::uuid,'$INTENT_B'::uuid),
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

Lab 5§3と同じ形で提案を1行作ります。今回は最初から `status='proposed'` で。

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
RETURNING id, status;"
```

期待される出力(idは環境ごとに違います。書き写して、次のようにシェル変数へ
入れてください。Lab 6§4と同じ作法です):

```
                  id                  | status
--------------------------------------+---------
 adf10b0b-ad45-46db-b158-4411b01ded12 | proposed
(1 row)
```

```bash
LATCH=adf10b0b-ad45-46db-b158-4411b01ded12
```

2人のyesで成立させます。Lab 5§4〜5と同じ窓口です。

```bash
ACCESS=$(cat /tmp/lab7-access-a.txt)
curl -s -X POST http://127.0.0.1:8000/v1/latches/$LATCH/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"response":"yes"}' | python3 -c "import json,sys; d=json.load(sys.stdin)['latch']; print(d['status'], d['remaining_responses'])"
ACCESS=$(cat /tmp/lab7-access-b.txt)
curl -s -X POST http://127.0.0.1:8000/v1/latches/$LATCH/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"response":"yes"}' | python3 -c "import json,sys; d=json.load(sys.stdin)['latch']; print(d['status'], d['remaining_responses'])"
```

期待される出力:

```
partial_accept 1
matched 0
```

matchedです。Calibrationの行も、この2回目の応答と同じトランザクションで
作られています(第17章17.7)。あとで§8の中身を見ます。

## 3. チャットを送る・読む

いよいよ第20章のAPIです。Aさんから送ります。**実行する前に、応答の形を
予測してください**(ステータスコードは?応答にどんな鍵が並ぶ?)。

```bash
ACCESS=$(cat /tmp/lab7-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"19時の天文館の改札で会いましょう!"}'
```

期待される出力(uuidと時刻は環境ごとに違います。**この応答の sender_id は§4で
使うので、書き留めておいてください**):

```
{"message":{"id":"39238a61-1ab8-4d4e-adcf-a06d4748d065","latch_id":"adf10b0b-ad45-46db-b158-4411b01ded12","sender_id":"de3386ad-aa13-40d2-9396-8b51eda97fe3","body":"19時の天文館の改札で会いましょう!","created_at":"2026-10-01T06:47:04.038905Z"}}
HTTP 201
```

**201 Created**。応答の `message` には、書いた文面(body)と、DBが決めた
id・created_atが並びます。表示名は含まれません——名前はLATCH詳細の
participantsで分かる(第17章17.6)ので、chatの応答には最小の要素だけが
返ります(第20章20.1)。

Bさんも返します。1秒ほど空けるのは、created_atで順序を付けるためです
(同一秒でもidで順序が決まる仕組みですが、見た目をはっきりさせます)。

```bash
sleep 1
ACCESS=$(cat /tmp/lab7-access-b.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"了解です!ちょっと遅れていくかもしれません。"}'
```

期待される出力:

```
{"message":{"id":"36919826-f0c5-490c-a6a3-1a0faf9e9e27","latch_id":"adf10b0b-…","sender_id":"e589418d-69a3-4b99-9670-34bd1e1a7e18","body":"了解です!ちょっと遅れていくかもしれません。","created_at":"2026-10-01T06:47:05.079230Z"}}
HTTP 201
```

では読みます。Aさんの立場で全件取得。

```bash
ACCESS=$(cat /tmp/lab7-access-a.txt)
curl -s "http://127.0.0.1:8000/v1/latches/$LATCH/messages" \
  -H "Authorization: Bearer $ACCESS" | python3 -m json.tool --no-ensure-ascii
```

期待される出力(要点):

```json
{
    "items": [
        {
            "id": "39238a61-1ab8-4d4e-adcf-a06d4748d065",
            "sender_id": "de3386ad-aa13-40d2-9396-8b51eda97fe3",
            "body": "19時の天文館の改札で会いましょう!",
            "created_at": "2026-10-01T06:47:04.038905Z"
        },
        {
            "id": "36919826-f0c5-490c-a6a3-1a0faf9e9e27",
            "sender_id": "e589418d-69a3-4b99-9670-34bd1e1a7e18",
            "body": "了解です!ちょっと遅れていくかもしれません。",
            "created_at": "2026-10-01T06:47:05.079230Z"
        }
    ],
    "next_cursor": null
}
```

(latch_idの鍵も各itemに入ります。上では省略しました)

**昇順**——古い発言が先です。お知らせ一覧(第19章)が新しい順だったのと
対照的で、会話は自然な読み順に並びます(第20章20.3)。全2件なので
`next_cursor` はnull。改頁も1回試します。

```bash
curl -s "http://127.0.0.1:8000/v1/latches/$LATCH/messages?limit=1" \
  -H "Authorization: Bearer $ACCESS" | python3 -c "import json,sys; d=json.load(sys.stdin); print('items:', len(d['items'])); print('cursor:', d['next_cursor'])"
```

期待される出力:

```
items: 1
cursor: MjAyNi0xMC0wMVQwNjo0NzowNC4wMzg5MDUrMDA6MDB8MzkyMzhhNjEtMWFiOC00ZDRlLWFkY2YtYTA2ZDQ3NDhkMDY1
```

この不透明な文字列が、`(created_at, id)` の2キーをbase64urlに詰めたcursor
です(第19章19.5)。もう1件を取るには、これを `cursor=` に渡します
(コマンド中のcursorの値は、いまあなたの手元に出力された文字列に置き換えて
ください。上の値はこのLabを検証した環境のものです)。

```bash
curl -s "http://127.0.0.1:8000/v1/latches/$LATCH/messages?limit=1&cursor=MjAyNi0xMC0wMVQwNjo0NzowNC4wMzg5MDUrMDA6MDB8MzkyMzhhNjEtMWFiOC00ZDRlLWFkY2YtYTA2ZDQ3NDhkMDY1" \
  -H "Authorization: Bearer $ACCESS" | python3 -c "import json,sys; d=json.load(sys.stdin); print('items:', len(d['items']), '/ body:', d['items'][0]['body'], '/ next:', d['next_cursor'])"
```

期待される出力:

```
items: 1 / body: 了解です!ちょっと遅れていくかもしれません。 / next: None
```

## 4. ブロックで、書く道だけが閉じる

第20章20.4を読んだら予想がつくはずです。blocksに行を1本入れると、
**双方向**の送信が409になり、**読む方は200のまま**。これを確かめます。
ブロックを登録するAPIはまだ実装されていないので、Lab 5・6と同じく
fixtureとして直接INSERTします。

§3の1通目の応答にあった sender_id(Aさん)と、2通目の sender_id(Bさん)を
使います。AがBをブロックした、という1行です(単方向の記録)。

```bash
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO blocks (blocker_id, blocked_id, created_at)
VALUES ('<Aのsender_id>'::uuid, '<Bのsender_id>'::uuid, now())
RETURNING blocker_id, blocked_id;"
```

期待される出力:

```
              blocker_id              |              blocked_id
--------------------------------------+--------------------------------------
 de3386ad-aa13-40d2-9396-8b51eda97fe3 | e589418d-69a3-4b99-9670-34bd1e1a7e18
(1 row)
```

**予測してから**、Aさん(Bをブロックした側)が送ってみます。

```bash
ACCESS=$(cat /tmp/lab7-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"まだ書けるかな"}'
```

期待される出力:

```
{"error":{"code":"CHAT_READONLY","message":"chat readonly","details":null}}
HTTP 409
```

次にBさん(ブロック**された**側)が送ってみます。

```bash
ACCESS=$(cat /tmp/lab7-access-b.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"逆向きもかな"}'
```

期待される出力:

```
{"error":{"code":"CHAT_READONLY","message":"chat readonly","details":null}}
HTTP 409
```

blocksは単方向の記録なのに、**どちら向きも409**。判定SQLが「私→誰か」と
「誰か→私」のORで書かれていたこと(第20章20.4)の実演です。読む方はどう
でしょう。

```bash
curl -s -o /dev/null -w "GET: %{http_code}\n" "http://127.0.0.1:8000/v1/latches/$LATCH/messages" \
  -H "Authorization: Bearer $ACCESS"
```

期待される出力:

```
GET: 200
```

読める。書く道だけが閉じました。ブロックを外すと書けるようにもどります。

```bash
docker compose exec -T db psql -U latch -d latch -c "
DELETE FROM blocks WHERE blocker_id='<Aのsender_id>'::uuid AND blocked_id='<Bのsender_id>'::uuid;"
ACCESS=$(cat /tmp/lab7-access-a.txt)
curl -s -o /dev/null -w "POST: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"ブロックを外したら戻りました"}'
```

期待される出力:

```
DELETE 1
POST: 201
```

## 5. 第三者と、からの文

lab7cさん(参加していない)には、送ることも読むことも許されません。
**両方とも何が返るか予測してから**、確かめます。

```bash
ACCESS_C=$(cat /tmp/lab7-access-c.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS_C" \
  -d '{"body":"乱入"}'
curl -s -w "\nHTTP %{http_code}\n" "http://127.0.0.1:8000/v1/latches/$LATCH/messages" \
  -H "Authorization: Bearer $ACCESS_C"
```

期待される出力:

```
{"error":{"code":"FORBIDDEN","message":"not a participant","details":null}}
HTTP 403
{"error":{"code":"FORBIDDEN","message":"not a participant","details":null}}
HTTP 403
```

403 FORBIDDEN。このAPIは、参加していない人にも「あなたはこのLATCHに
関与していません」と答えます。一方、§8の申告では同じ第三者が**404**に
なります。申告通知は参加者にしか届かないので、そちらでは関与の有無自体を
開示しないのです(第20章20.5)。この違い、後で思い出してください。

本文の検証も1つ。空白だけの文は弾かれます(第20章20.3)。

```bash
ACCESS=$(cat /tmp/lab7-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"   "}'
```

期待される出力:

```
{"error":{"code":"VALIDATION_ERROR","message":"request validation failed","details":null}}
HTTP 422
```

## 6. 時間を進めて、完了させる

Lab 6§4と同じ手法です。matchedは、会の対象時刻(参加Intentのtime_startの
最大値)が過ぎるとcompletedへ進むのでした。2人のIntentのtime_startを30分前に
書き換えて、60秒周期のsweeperを待ちます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
UPDATE intents SET time_start = now() - interval '30 minutes'
WHERE id IN ('$INTENT_A'::uuid, '$INTENT_B'::uuid) RETURNING id;"
sleep 75
docker compose exec -T db psql -U latch -d latch -c "SELECT status, completed_at FROM latches WHERE id='$LATCH'::uuid;"
```

期待される出力(UPDATEの結果2行のあと、75秒待ってから):

```
                  id
--------------------------------------
 f1fe242e-4f18-48be-88e1-5eaf0a616c6e
 b915000c-3c35-4d43-bda0-eaaea326e369
(2 rows)

UPDATE 2

   status   |         completed_at
------------+------------------------------
 completed | 2026-10-01 06:48:10.646198+00
(1 row)
```

workerのログを見ます。Lab 6では `sweeper.completed` の1行だけでしたが、
第19章の実装が入った今、**その直後にもう1種類**流れています。

```bash
docker compose logs --since 2m worker 2>&1 | rg "sweeper.completed|push.send" | tail -3
```

期待される出力:

```
worker-1  | 2026-10-01 06:48:10,679 INFO latch.worker.sweeper sweeper.completed latch_id=adf10b0b-ad45-46db-b158-4411b01ded12
worker-1  | 2026-10-01 06:48:10,686 INFO latch.push.send {"occurred_at":"2026-10-01T06:48:10.686055Z","user_id":"de3386ad-…","notification_type":"attendance_request","latch_id":"adf10b0b-…","title":"LATCH","body":"LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。","status":"ok","error_code":null}
worker-1  | 2026-10-01 06:48:10,686 INFO latch.push.send {"occurred_at":"2026-10-01T06:48:10.686332Z","user_id":"e589418d-…",…}
```

(2本目のpush.sendはBさん宛。user_idだけが違う同じ形の行です)

`latch.push.send` が2件。参加者2人それぞれへ、実施自己申告の通知が
「送られた」ことのドライラン記録(第19章19.3)です。見るとおり、スマホへの
電波は飛んでいません。代わりに**本文が丸ごと記録に残ります**。
「LATCHからのお知らせがあります。詳細はアプリでご確認ください。」という
第二の汎用文であること、条件サマリが1字も出ていないこと——この2点を、
記録で検証できるのです(10 §1)。

## 7. 完了後: 読めるけれど、書けない

completedになりました。**送る前に予測を**: ステータスコードは?codeは?

```bash
ACCESS=$(cat /tmp/lab7-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"終わったあとに送れるかな"}'
curl -s -o /dev/null -w "GET: %{http_code}\n" "http://127.0.0.1:8000/v1/latches/$LATCH/messages?limit=1" \
  -H "Authorization: Bearer $ACCESS"
```

期待される出力:

```
{"error":{"code":"CHAT_READONLY","message":"chat readonly","details":null}}
HTTP 409
GET: 200
```

書く方はブロック時と同じ409 CHAT_READONLY。理由が違う(completedだから)のに
コードが同じ——「クライアントのすることはどの理由でも同じ」だからでした
(第20章20.2)。読む方は200のまま、これまでの発言が全部見られます。

## 8. 申告を、1回だけ通す

本題の実施自己申告です。まず**3日の窓の外**を体験します。Lab 6と同じ
等価置換で、completed_atを4日前へ書き換えます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
UPDATE latches SET completed_at = now() - interval '4 days' WHERE id='$LATCH'::uuid RETURNING completed_at;"
ACCESS=$(cat /tmp/lab7-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/attendance \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"attended":true}'
```

期待される出力:

```
{"error":{"code":"ATTENDANCE_WINDOW_CLOSED","message":"attendance window closed","details":null}}
HTTP 409
```

窓を戻して(5分前に)、Aさんが答えます。会えた、と。

```bash
docker compose exec -T db psql -U latch -d latch -c "
UPDATE latches SET completed_at = now() - interval '5 minutes' WHERE id='$LATCH'::uuid RETURNING completed_at;"
ACCESS=$(cat /tmp/lab7-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/attendance \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"attended":true}'
```

期待される出力:

```
{"latch_id":"adf10b0b-ad45-46db-b158-4411b01ded12","actual_attended":true}
HTTP 200
```

通りました。**ここでBさんも答えようとします**。予測は?Bさんは「会えなかった」
側の回答(false)を送ります。

```bash
ACCESS=$(cat /tmp/lab7-access-b.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/attendance \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"attended":false}'
```

期待される出力:

```
{"error":{"code":"ATTENDANCE_ALREADY_SUBMITTED","message":"already submitted","details":null}}
HTTP 409
```

申告の回答単位はLATCH単位・先着1名(第20章20.5)。Aさんの1回答でこのLATCHの
記録は確定済みで、Bさんの回答はもう入りません。では、その「記録」がどこに
入ったか。§2で仕込んだCalibrationの行を見ます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT actual_attended, cancelled_after, updated_at
FROM calibration_records WHERE latch_id='$LATCH'::uuid;"
```

期待される出力:

```
 actual_attended | cancelled_after |          updated_at
-----------------+-----------------+-------------------------------
 t               | f               | 2026-10-01 06:49:24.393129+00
(1 row)
```

`actual_attended=t`、`cancelled_after=f`。第17章17.7で「予測」と並べて保存
されていた「実際」の、最後の1列がAさんの1タップで埋まりました。第20章の
`SET actual_attended = :attended, cancelled_after = NOT :attended` のとおり、
2つの列は**1つのUPDATEで同時に**、反対の値に入っています。

おまけに、第三者lab7cさんの申告も試しておきます。

```bash
ACCESS_C=$(cat /tmp/lab7-access-c.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/attendance \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS_C" \
  -d '{"attended":true}'
```

期待される出力:

```
{"error":{"code":"NOT_FOUND","message":"not a participant","details":null}}
HTTP 404
```

§5のmessagesが403だったのに対し、こちらは404。参加していない人には、この
LATCHとの関与の有無すら教えない、という扱いの違いです(第20章20.5)。

## 9. お知らせに、それが並んでいる

最後に、届けた先を見ます。§6のcompleted化のとき、notificationsには
attendance_requestの行が2人分書かれ、プッシュ記録も出ました。Aさんとして、
お知らせ一覧(第19章19.5)を開きます。

```bash
ACCESS=$(cat /tmp/lab7-access-a.txt)
curl -s "http://127.0.0.1:8000/v1/notifications?limit=3" \
  -H "Authorization: Bearer $ACCESS" | python3 -m json.tool --no-ensure-ascii
```

期待される出力(要点):

```json
{
    "items": [
        {
            "id": "dc51b182-2d6d-48f4-bc78-d8d58f038e98",
            "type": "attendance_request",
            "latch_id": "adf10b0b-ad45-46db-b158-4411b01ded12",
            "read_at": null,
            "created_at": "2026-10-01T06:48:10.646198Z",
            "latch": {
                "id": "adf10b0b-ad45-46db-b158-4411b01ded12",
                "status": "completed",
                "completed_at": "2026-10-01T06:44:24.377022Z",
                "proposal": {"headcount": 2, "match_level": "medium"}
            }
        }
    ],
    "next_cursor": null
}
```

(latchの中には response_deadline・expires_at も入ります。上では省略しました)

notificationsの行(payloadはlatch_idだけの最小参照)に、latchesの内容が
**LEFT JOINで埋め込まれて**届いています。文言はこの構造からクライアントが
組み立てる——プッシュの文面は汎用文に縛られ(第19章19.2)、詳細はアプリ内で
だけ見せる、という分担の受け取り側です。`read_at` はnull(未読)。既読にします。

```bash
NID=$(curl -s "http://127.0.0.1:8000/v1/notifications?limit=1" \
  -H "Authorization: Bearer $ACCESS" | python3 -c "import json,sys; print(json.load(sys.stdin)['items'][0]['id'])")
curl -s -o /dev/null -w "read: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/notifications/$NID/read \
  -H "Authorization: Bearer $ACCESS"
curl -s -o /dev/null -w "read again: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/notifications/$NID/read \
  -H "Authorization: Bearer $ACCESS"
ACCESS_B=$(cat /tmp/lab7-access-b.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/notifications/$NID/read \
  -H "Authorization: Bearer $ACCESS_B"
```

期待される出力:

```
read: 204
read again: 204
{"error":{"code":"NOT_FOUND","message":"notification not found","details":null}}
HTTP 404
```

1回目も2回目も204(冪等・第19章19.6)。そして、Bさんが**Aさんのお知らせ**を
既読にしようとしても404。他人の通知は、存在しないのと同じ扱いです。

## 10. 片付け

Lab 5・6と同じ流儀で、lab7プレフィックスのデータをFK順に削除します。今回の
Labが書いたテーブルは、Lab 6の7つに加えて、messages(§3)・blocks(§4)・
calibration_records(§2・§8で対象になった行)の3つです。

```bash
docker compose exec -T db psql -U latch -d latch -c "
DELETE FROM match_candidates WHERE intent_a_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab7%')) OR intent_b_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab7%'));
DELETE FROM messages WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab7%')));
DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab7%')));
DELETE FROM calibration_records WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab7%')));
DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab7%');
DELETE FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab7%'));
DELETE FROM blocks WHERE blocker_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab7%') OR blocked_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab7%');
DELETE FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab7%');
DELETE FROM users WHERE auth_subject LIKE 'learn-lab7%';
SELECT (SELECT count(*) FROM users WHERE auth_subject LIKE 'learn-lab7%') AS users_left,
       (SELECT count(*) FROM messages WHERE sender_id NOT IN (SELECT id FROM users)) AS msgs_orphan;"
```

期待される出力(DELETEの行数は環境によって多少変わります。最後の行の
2つの数値が0になれば成功です):

```
DELETE 1
DELETE 3
DELETE 3
DELETE 1
DELETE 2
DELETE 1
DELETE 0
DELETE 2
DELETE 3
 users_left | msgs_orphan
------------+-------------
          0 |           0
(1 row)
```

## 11. 振り返りとノート

このLabで、あなたはlatchesの行の一生の**最後の区間**を、自分の手で歩きま
した。my-notes.mdに、次の3つを書き残すのをおすすめします。

1. **409が3種類出た経路のちがい**(CHAT_READONLY・ATTENDANCE_WINDOW_CLOSED・
   ATTENDANCE_ALREADY_SUBMITTED)。それぞれ、どの検査がはじいたか。中でも
   CHAT_READONLYが「ブロック(§4)」と「完了(§7)」の両方で出た理由を、
   第20章20.2の自分の言葉で
2. **§8の一巡**。評価行(§2)→回答APIで成立→Calibrationの「実際」が埋まる。
   第17章17.7の「予測」と今回のactual_attendedが、どう1つの行で出会ったか
3. **§6と§9の対**。同じ事実(attendance_request)が、プッシュ記録(ログ)と
   お知らせ一覧(DB+LEFT JOIN)の2経路で観察できた。それぞれの役割の違いを
   第19章の2層(事実の行と届け方)で説明する

疑問が残ったら、それもノートに書いてください。ブロックの登録API・お知らせの
画面・30日の定期削除は、次の実装単位以降の題材です。

## 12. このLabの対象になったコード

- `backend/src/latch/latches/routes.py` — POST/GET messages・POST attendanceの
  3エンドポイント(latches_routerへの追記)
- `backend/src/latch/latches/service.py` — send_message・list_messages・
  submit_attendance(FOR UPDATE→検査→INSERT・条件付きUPDATE)
- `backend/src/latch/latches/store.py` — messages挿入・改頁選択・blocks双方向
  判定・calibrationの条件付きUPDATE
- `backend/src/latch/notifications/` — GET /v1/notifications・既読(§9)
- `backend/src/latch/worker/sweeper.py`・`worker/matching/latch_engine.py` —
  コミット直後のプッシュ送信(§6のログ)
- `backend/alembic/versions/0006_chat_indexes.py` — messagesの複合索引と
  calibration_recordsの部分UNIQUE
