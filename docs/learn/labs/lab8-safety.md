# Lab 8 ブロックと通報を、自分の手で入れる

- 種別: チュートリアル(手を動かして必ず成功体験を得る。壊しません)
- 前提知識: 第21章(ブロックの3つの効果とキャッシュ)・第20章(409 CHAT_READONLY)・
  第17章(latch_status_events)・第18章(sweeperのクローズと台帳のuser_id=NULL)・
  第7章(Redisの鍵をredis-cliで見る)。Lab 7のトークン発行・Intent保存・
  評価行の投入・latches行の直接INSERTは思い出せるとスムーズです(同じコマンドを
  使います)
- 所要目安: 25〜35分
- このLabでできるようになること: ブロックの登録APIと通報APIを叩き、その効果を
  **キャッシュの中身ごと**観察できる。具体的には、Redisに覚えられたブロック一覧
  (`blk:u:`)をredis-cliでのぞき、登録で鍵が消えて読み直される様子・matchedな
  LATCHはcancelled化されずチャットだけ閉じる様子・解除で送信が復帰する様子・
  進行中の提案(proposed)が登録と同時にcancelledへ閉じられ、台帳に「誰が閉じたか」
  が記録される様子を、すべて自分の手で確かめられる
- 次に読むもの: なし(ここまでが、いま実装済みの最先端です)

## 0. このLabで何をするか

第21章で、ブロックの登録APIが1回のリクエストで3つの効果を連れて歩くと読み
ました。読んだだけでは「即時に効く」の実感は薄いでしょう。このLabでは、効果を
1つずつ確認します。

道のりはこうです。

1. Lab 7と同じ手法で、2人にとっての「成立済み(matched)」を作る
2. チャットを1通送り、その瞬間にRedisへキャッシュが**生まれる**のを見る
3. ブロックAPIで登録する。matchedなLATCHはcancelled化されず、チャットだけが
   409で閉じる。キャッシュの中身が変わるのも見る
4. 一覧を出し、解除して、送信が復帰するのを確かめる
5. もう1本、進行中の提案(proposed)を作り、ブロックでcancelledへ閉じさせる。
   台帳(latch_status_events)に「閉じた人」が記録されるのを見る
6. 通報を投げる。受付(201)と、弾かれる場合(422)を両方見る

使う道具はLab 7と同じです。curlでAPIを叩き、psqlでDBを覗き、redis-cliでRedisを
覗きます。壊しません(データは最後に全部消します)。

## 1. 2人のユーザーと、matchedなLATCHを用意する

Lab 7§1〜§2と同じ手順です。lab8a・lab8bの2人でLATCHを成立させます(第三者は
このLabでは使いません。通報の参加者検査にはlab8a自身で足ります)。
backend ディレクトリで実行します。

```bash
cd /home/misty/Projects/latch/backend
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab8a > /tmp/lab8-idp-a.txt
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab8b > /tmp/lab8-idp-b.txt
```

トークンを交換して、2人分のユーザー登録まで一気にやります。

```bash
for s in a b; do
  ACCESS=$(curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
    -H "Content-Type: application/json" \
    -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab8-idp-$s.txt)\"}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
  echo "$ACCESS" > /tmp/lab8-access-$s.txt
  curl -s -o /dev/null -w "lab8$s users: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/users \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"display_name":"Lab8'$s'","birth_date":"1990-04-01","profile":{}}'
done
```

期待される出力:

```
lab8a users: 201
lab8b users: 201
```

2人のIntentを保存します。time.startは未来(120時間後)。ここから先の
`docker compose exec` はリポジトリのルート(`latch/`)で実行するので、Intentを
保存したら戻っておいてください。

```bash
cd /home/misty/Projects/latch
START=$(python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(hours=120)).replace(microsecond=0).isoformat())")
for s in a b; do
  ACCESS=$(cat /tmp/lab8-access-$s.txt)
  curl -s -o /tmp/lab8-intent-$s.json -w "lab8$s intents: %{http_code}\n" \
    -X POST http://127.0.0.1:8000/v1/intents \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"raw_text":"Lab8用:今度の金曜、天文館のあたりで焼肉を食べたい","status":"active","structured_intent":{"category":{"primary":"meal","secondary":"焼肉"},"alcohol_involved":false,"time":{"start":"'$START'","end":null},"location":{"name":"天文館"}}}'
done
INTENT_A=$(python3 -c "import json; print(json.load(open('/tmp/lab8-intent-a.json'))['intent']['id'])")
INTENT_B=$(python3 -c "import json; print(json.load(open('/tmp/lab8-intent-b.json'))['intent']['id'])")
echo "A: $INTENT_A"
echo "B: $INTENT_B"
```

期待される出力(値は環境ごとに違います):

```
lab8a intents: 201
lab8b intents: 201
A: 6e15e4e4-1e8a-4123-b2c0-5305e4b2ed25
B: 66583a6b-7197-4923-bafc-019da320b519
```

Lab 7§2と同じく、評価行を投入してから提案を作り、本物の回答APIで成立させます。

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

期待される出力(idは環境ごとに違います。書き写して、シェル変数へ入れてください。

Lab 6§4と同じ作法です):

```
                  id                  | status
--------------------------------------+---------
 492dd0af-6321-4ad6-b111-8aa9d1f5aa86 | proposed
(1 row)
```

```bash
LATCH=492dd0af-6321-4ad6-b111-8aa9d1f5aa86
```

2人のyesで成立させます。

```bash
ACCESS=$(cat /tmp/lab8-access-a.txt)
curl -s -X POST http://127.0.0.1:8000/v1/latches/$LATCH/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"response":"yes"}' | python3 -c "import json,sys; d=json.load(sys.stdin)['latch']; print(d['status'], d['remaining_responses'])"
ACCESS=$(cat /tmp/lab8-access-b.txt)
curl -s -X POST http://127.0.0.1:8000/v1/latches/$LATCH/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"response":"yes"}' | python3 -c "import json,sys; d=json.load(sys.stdin)['latch']; print(d['status'], d['remaining_responses'])"
```

期待される出力:

```
partial_accept 1
matched 0
```

## 2. キャッシュが「覚える」瞬間を見る

まず下ごしらえとして、Aさんが1通送ります。**実行する前に、Redisの鍵がどう
変化するか予測してください**(ブロックしていない2人の一覧は、どんな値として
覚えられるでしょう?)。

```bash
ACCESS=$(cat /tmp/lab8-access-a.txt)
curl -s -o /dev/null -w "POST: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"19時の天文館の改札で会いましょう!"}'
```

期待される出力:

```
POST: 201
```

送れました。この1回の送信の中で、送信時のブロック判定が走り、判定に必要な
「2人のブロック一覧」がRedisに覚えられたはずです(第21章21.4のread-through)。
このあとの観察で2人のuser_idを何度か使うので、psqlで取っておきます(§5の
Cさんも同じ方法で取ります)。

```bash
docker compose exec -T db psql -U latch -d latch -t -c "SELECT id FROM users WHERE display_name IN ('Lab8a','Lab8b') ORDER BY display_name;"
```

期待される出力(上がAさん、下がBさん。値は環境ごとに違います。書き写して、
シェル変数へ入れてください):

```
 223a79f7-b039-4457-affa-a80f6871cdc9
 75863070-b96d-45c6-9f0e-f62c52413bf4
```

```bash
A_USER=223a79f7-b039-4457-affa-a80f6871cdc9
B_USER=75863070-b96d-45c6-9f0e-f62c52413bf4
```

redis-cliでのぞいてみます。

```bash
docker compose exec -T redis redis-cli --scan --pattern 'blk:u:*'
```

期待される出力(並び順は保証されません。2人のuser_idの鍵が1本ずつ、計2本
できています):

```
blk:u:223a79f7-b039-4457-affa-a80f6871cdc9
blk:u:75863070-b96d-45c6-9f0e-f62c52413bf4
```

Aさんの鍵の中身と、残りの生存時間を見ます。

```bash
docker compose exec -T redis redis-cli MGET blk:u:$A_USER
docker compose exec -T redis redis-cli TTL blk:u:$A_USER
```

期待される出力(2行目の数字は数えるタイミングで減っていきます):

```
[]
3585
```

`[]` は「この人は誰もブロックしていない」。**ブロックゼロも、ちゃんと覚えられて
います**。TTLは約3600秒で、第21章で読んだ「消し忘れの保険」の時計です。

## 3. ブロックAPIで登録する。matchedは閉じず、チャットだけ閉じる

いよいよ本題です。AさんがBさんをブロックします。**実行する前に、次の3つを
予測してください**。①LATCHのstatusはどうなる? ②AさんとBさん、それぞれの
送信はどうなる? ③Redisの2本の鍵はどうなる?

相手には、§2で取っておいたBさんのuser_id($B_USER)を指定します。

```bash
ACCESS=$(cat /tmp/lab8-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/users/$B_USER/block \
  -H "Authorization: Bearer $ACCESS"
```

期待される出力:

```
{"blocked_id":"75863070-b96d-45c6-9f0e-f62c52413bf4"}
HTTP 201
```

201 Created。まずLATCHのstatusを確かめます。D-23はmatchedをcancelledにしない
のでした(第21章21.3)。

```bash
docker compose exec -T db psql -U latch -d latch -c "SELECT id, status FROM latches WHERE id='$LATCH'::uuid;"
```

期待される出力:

```
                  id                  | status
--------------------------------------+---------
 492dd0af-6321-4ad6-b111-8aa9d1f5aa86 | matched
(1 row)
```

matchedのまま。次に送信です。Aさん(Bをブロックした側)から。

```bash
ACCESS=$(cat /tmp/lab8-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"まだ書けるかな"}'
```

期待される出力:

```
{"error":{"code":"CHAT_READONLY","message":"chat readonly","details":null}}
HTTP 409
```

Bさん(ブロックされた側)も送ってみます。

```bash
ACCESS=$(cat /tmp/lab8-access-b.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"逆向きもかな"}'
```

期待される出力:

```
{"error":{"code":"CHAT_READONLY","message":"chat readonly","details":null}}
HTTP 409
```

どちら向きも409。単方向の記録なのに双方向で閉じるのは、第20章20.4で読んだ
とおりです。読む方は残ります。

```bash
ACCESS=$(cat /tmp/lab8-access-b.txt)
curl -s -o /dev/null -w "GET: %{http_code}\n" "http://127.0.0.1:8000/v1/latches/$LATCH/messages" \
  -H "Authorization: Bearer $ACCESS"
```

期待される出力:

```
GET: 200
```

最後にキャッシュ。登録APIはコミット後に2本の鍵をDELし、この409を起こした
送信の中でread-throughが**新しい内容を覚え直した**はずです。

```bash
docker compose exec -T redis redis-cli MGET blk:u:$A_USER blk:u:$B_USER
```

期待される出力(1つ目がAさんの鍵、2つ目がBさんの鍵):

```
["75863070-b96d-45c6-9f0e-f62c52413bf4"]
[]
```

Aさんの覚えは `[B]`(Bをブロック中)、Bさんの覚えは `[]` のまま。自分の「ブロック
している相手の一覧」だけが入る鍵なので、Bさんの鍵が変わらないのは当然、と
言えます。判定はこの2つの鍵を突き合わせて(Aの一覧にBがいる)閉じました。

## 4. 一覧を出し、解除して、復帰を確かめる

ブロック一覧のAPIは、自分がブロックした相手を新しい順に返します。

```bash
ACCESS=$(cat /tmp/lab8-access-a.txt)
curl -s http://127.0.0.1:8000/v1/users/me/blocks -H "Authorization: Bearer $ACCESS" | python3 -m json.tool --no-ensure-ascii
```

期待される出力:

```json
{
    "items": [
        {
            "blocked_id": "75863070-b96d-45c6-9f0e-f62c52413bf4",
            "display_name": "Lab8b",
            "created_at": "2026-10-01T11:06:40.891626Z"
        }
    ],
    "next_cursor": null
}
```

blocksテーブルはuuidしか持たないのに、応答に表示名が並びます。usersをJOINした
SELECTが裏で走っているからです(第21章21.5)。

では解除します。**予測**: 解除した直後に送ると、Aさんの送信は何になりますか?

```bash
ACCESS=$(cat /tmp/lab8-access-a.txt)
curl -s -o /dev/null -w "DELETE: %{http_code}\n" -X DELETE http://127.0.0.1:8000/v1/users/$B_USER/block \
  -H "Authorization: Bearer $ACCESS"
curl -s -o /dev/null -w "POST: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/latches/$LATCH/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"body":"ブロックを外したら戻りました"}'
```

期待される出力:

```
DELETE: 204
POST: 201
```

解除(204)と同時に送信が201へ復帰しました。解除APIもコミット後に鍵をDELする
ので、この送信の中でread-throughが「もうブロックしていない」を読み直した、
という流れです。過去を巻き戻す操作は何もしていません(LATCHは最初からmatched
のままですし、キャッシュは消して覚え直しただけ)。

もう一度解除を頼むと、消す行がないので404です。

```bash
ACCESS=$(cat /tmp/lab8-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X DELETE http://127.0.0.1:8000/v1/users/$B_USER/block \
  -H "Authorization: Bearer $ACCESS"
```

期待される出力:

```
{"error":{"code":"NOT_FOUND","message":"block not found","details":null}}
HTTP 404
```

ついでに、自分自身はブロックできません。相手には§2の $A_USER を指定します。

```bash
ACCESS=$(cat /tmp/lab8-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/users/$A_USER/block \
  -H "Authorization: Bearer $ACCESS"
```

期待される出力:

```
{"error":{"code":"VALIDATION_ERROR","message":"cannot block yourself","details":null}}
HTTP 422
```

## 5. 進行中の提案を、ブロックで閉じさせる

§3ではmatchedだったので、cancelled化は起きませんでした。D-23のもう一方の
効果——進行中の提案が登録と同時にcancelledへ閉じられる——を、proposedで
確かめます。

今度はAさんともう1人、Cさんを作って提案を1本作ります。AさんのIntentは§1の
ものが成立でmatchedになってもう使えないので、AさんのIntentを新しく1件保存
します。Cさんはここで初登場です。

```bash
cd /home/misty/Projects/latch/backend
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab8c > /tmp/lab8-idp-c.txt
ACCESS=$(curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
  -H "Content-Type: application/json" \
  -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab8-idp-c.txt)\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
echo "$ACCESS" > /tmp/lab8-access-c.txt
curl -s -o /dev/null -w "lab8c users: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/users \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"display_name":"Lab8c","birth_date":"1990-04-01","profile":{}}'
```

期待される出力:

```
lab8c users: 201
```

Aさんの2つ目のIntentと、CさんのIntentを保存します(ここからは再びリポジトリ
ルートで実行します)。

```bash
cd /home/misty/Projects/latch
for s in a c; do
  ACCESS=$(cat /tmp/lab8-access-$s.txt)
  curl -s -o /tmp/lab8-intent2-$s.json -w "lab8$s intents2: %{http_code}\n" \
    -X POST http://127.0.0.1:8000/v1/intents \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"raw_text":"Lab8用2本目:土曜にカフェで勉強したい","status":"active","structured_intent":{"category":{"primary":"meal","secondary":"カフェ"},"alcohol_involved":false,"time":{"start":"'$START'","end":null},"location":{"name":"天文館"}}}'
done
```

期待される出力:

```
lab8a intents2: 201
lab8c intents2: 201
```

proposedを1本だけ作ります(§1と違い、成立はさせません。評価行も、申告をしない
ので不要です)。

```bash
INTENT_A2=$(python3 -c "import json; print(json.load(open('/tmp/lab8-intent2-a.json'))['intent']['id'])")
INTENT_C=$(python3 -c "import json; print(json.load(open('/tmp/lab8-intent2-c.json'))['intent']['id'])")
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO latches
  (intent_ids, group_candidate_id, proposal, score, status,
   response_deadline, expires_at, created_at)
VALUES
  (ARRAY['$INTENT_A2'::uuid, '$INTENT_C'::uuid],
   NULL,
   '{\"headcount\": 2, \"match_level\": \"medium\"}'::jsonb,
   0.85, 'proposed',
   now() + interval '2 hours',
   now() + interval '120 hours',
   now())
RETURNING id, status;"
```

期待される出力(§1と同じく、idを書き写してシェル変数へ):

```
                  id                  | status
--------------------------------------+---------
 7ed85a60-8f35-4865-b2d2-f98fbcb0eb31 | proposed
(1 row)
```

```bash
LATCH2=7ed85a60-8f35-4865-b2d2-f98fbcb0eb31
```

Cさんのuser_idを確認して、AさんがCさんをブロックします。

```bash
docker compose exec -T db psql -U latch -d latch -t -c "SELECT id FROM users WHERE display_name='Lab8c';"
```

期待される出力(先頭の空白を除いたuuid。書き写してください):

```
 efd62d9c-d9b3-4822-bc57-8e3b2c45eb0f
```

```bash
C_USER=efd62d9c-d9b3-4822-bc57-8e3b2c45eb0f
ACCESS=$(cat /tmp/lab8-access-a.txt)
curl -s -o /dev/null -w "block A->C: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/users/$C_USER/block \
  -H "Authorization: Bearer $ACCESS"
```

期待される出力:

```
block A->C: 201
```

**この201の中で**、2人を共に含む進行中のLATCHがcancelled化されたはずです。
LATCH2と、参加Intentの状態と、台帳を見ます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT id, status FROM latches WHERE id='$LATCH2'::uuid;
SELECT id, status FROM intents WHERE id IN ('$INTENT_A2'::uuid, '$INTENT_C'::uuid);
SELECT latch_id, from_status, to_status, user_id FROM latch_status_events WHERE latch_id='$LATCH2'::uuid;"
```

期待される出力:

```
                  id                  | status
--------------------------------------+---------
 7ed85a60-8f35-4865-b2d2-f98fbcb0eb31 | cancelled
(1 row)

                  id                  | status
--------------------------------------+----------------------------------
 faf33aa0-8ade-4163-8147-876ece4533c3 | active
 06cfa2b0-6e8d-4ee7-8af7-8b1942f5997d | active
(2 rows)

               latch_id               | from_status | to_status |               user_id
--------------------------------------+-------------+-----------+--------------------------------------
 7ed85a60-8f35-4865-b2d2-f98fbcb0eb31 | proposed    | cancelled | 223a79f7-b039-4457-affa-a80f6871cdc9
(1 row)
```

3つの観察が揃いました。①proposedがcancelledへ。②参加Intentはどちらも
activeのまま(第21章21.3のとおり、手を触れません)。③台帳のuser_idは
Aさん(223a79f7-…)——第18章で見たsweeperのuser_id=NULL(システム起因)との
違いです。誰が閉じた遷移か、台帳は覚えています。

Cさんには、この提案が閉じた理由は伝わりません。notificationsのテーブルと
pushのドライラン記録に、cancelledに伴う行が**ない**ことも確かめられます
(ブロックされた事実を開示しない設計・第21章21.3)。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT count(*) FROM notifications WHERE latch_id='$LATCH2'::uuid;"
```

期待される出力:

```
 count
-------
     0
(1 row)
```

## 6. 通報を投げる

最後に通報です。§3〜§4でブロックして解除したBさんを、Aさんが通報します。
latch_idには§1のLATCH(2人とも参加者)を付けます。**予測**: reasonに
`inappropriate_content` の代わりに `bad_reason` という4つにない値を渡すと
何が返るでしょう?(あとで試します)

```bash
ACCESS=$(cat /tmp/lab8-access-a.txt)
curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8000/v1/reports \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"reportee_id":"'$B_USER'","latch_id":"'$LATCH'","reason":"inappropriate_content"}'
```

期待される出力:

```
{"report_id":"3b0fd61e-ef17-48fe-bb9b-f51863007fa5"}
HTTP 201
```

受付の201と、report_idだけ。弾かれる場合を3つ、続けて見ます。自分自身への
通報、理由の値域外、そして「自分が参加していないLATCH」を指定した通報です
(3つ目はCさんに、AとBのLATCHを材料にした通報を頼みます)。

```bash
ACCESS=$(cat /tmp/lab8-access-a.txt)
echo '--- 自分自身 ---'
curl -s -o /dev/null -w "self: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/reports \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"reportee_id":"'$A_USER'","reason":"other"}'
echo '--- 理由の値域外 ---'
curl -s -o /dev/null -w "bad reason: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/reports \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"reportee_id":"'$B_USER'","reason":"bad_reason"}'
echo '--- 関わっていないLATCH ---'
ACCESS=$(cat /tmp/lab8-access-c.txt)
curl -s -o /dev/null -w "not participant: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/reports \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"reportee_id":"'$B_USER'","latch_id":"'$LATCH'","reason":"other"}'
```

期待される出力:

```
self: 422
bad reason: 422
not participant: 422
```

3つとも422 VALIDATION_ERRORでした。DBに残った記録を見ます。

```bash
docker compose exec -T db psql -U latch -d latch -c "SELECT reporter_id, reportee_id, latch_id, reason, status FROM reports;"
```

期待される出力:

```
             reporter_id              |             reportee_id              |               latch_id               |         reason          | status
--------------------------------------+--------------------------------------+--------------------------------------+-------------------------+--------
 223a79f7-b039-4457-affa-a80f6871cdc9 | 75863070-b96d-45c6-9f0e-f62c52413bf4 | 492dd0af-6321-4ad6-b111-8aa9d1f5aa86 | inappropriate_content   | pending
(1 row)
```

latch_idつきの1行だけが通りました。statusは `pending`(受付)。ここから先は
運用の人間が見るもので、このAPIは何も自動化しません(第21章21.6)。latch_idを
省いた通報も受け付けてもらえます。興味があれば、`"latch_id"` を抜いた同じ
コマンドをもう1回投げてみてください(2行目が増えます。重複制限がないことの
確認にもなります)。

## 7. 片付け

Labが書いたテーブルは、Lab 7の7つに加えて blocks・reports の2つです
(latch_status_eventsにもcancelledの行が増えました)。全部消します。

```bash
docker compose exec -T db psql -U latch -d latch -c "
DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && (SELECT array_agg(id) FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab8%')));
DELETE FROM calibration_records WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && (SELECT array_agg(id) FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab8%')));
DELETE FROM messages WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && (SELECT array_agg(id) FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab8%')));
DELETE FROM reports WHERE reporter_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab8%');
DELETE FROM blocks WHERE blocker_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab8%') OR blocked_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab8%');
DELETE FROM latches WHERE intent_ids && (SELECT array_agg(id) FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab8%'));
DELETE FROM match_candidates WHERE intent_a_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab8%')) OR intent_b_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab8%'));
DELETE FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab8%');
DELETE FROM users WHERE auth_subject LIKE 'learn-lab8%';"
```

期待される出力(削除行数の並び。個数はあなたが追加で試した回数で変わります):

```
DELETE 4
DELETE 1
DELETE 3
DELETE 2
DELETE 2
DELETE 3
DELETE 2
DELETE 4
DELETE 3
```

Redisの鍵はTTL任せでも1時間で消えますが、残しておく理由もないので消して
しまいます。**scanの前に予測してください**: この時点で何本残っているでしょう?

- §5のブロック登録は、コミット後にAさんとCさんの鍵をDELしました
- けれどCさんの鍵は、このLabでは一度も書かれていません(送信判定が走らないと
  read-throughは働きません)
- Bさんの鍵は、§4の復帰の送信で `[]` として覚え直されたままです

つまり手順どおりなら、残っているのは**Bさんの鍵1本**のはずです。

```bash
docker compose exec -T redis redis-cli --scan --pattern 'blk:u:*'
```

期待される出力(Bさんのuser_idの鍵1本):

```
blk:u:75863070-b96d-45c6-9f0e-f62c52413bf4
```

消します(`FLUSHDB` は使いません。他の用途の鍵 `auth:`・`rl:` が同じDBに
入っているため、鍵を指定して消します)。

```bash
docker compose exec -T redis redis-cli DEL blk:u:$B_USER
```

期待される出力(消えた鍵の本数):

```
1
```

## 8. 確認問題

1. §2で覚えられた `[]` は何を表していますか? 「ブロックがない」ことを毎回DBへ
   確しに行くのと、`[]` を覚えておくのと、どちらが何回のDB読み込みを節約するか
   説明してください
2. §3でAさんの鍵は `["B"]` に、Bさんの鍵は `[]` のままでした。双方向の409は
   この2つの鍵からどう導かれますか?
3. §4の解除直後、送信は201に戻りました。この復帰に、blocksテーブルの他に
   どの仕組み(どんな順で)が働きましたか?
4. §5の台帳のuser_idがAさんだったのはなぜですか? 第18章のsweeperによる
   expired遷移のuser_idと比べてください
5. §5でnotificationsに行が増えなかったのは、どの設計判断によるものですか?
6. 通報のreasonが422で弾かれる仕組みは、第5章・第6章で読んだどこに似ていますか?

疑問が残ったら、それもノートに書いてください。ブロックの管理画面(自分の
一覧を出すUI)と通報フォームは、フロント側の後続単位で実装されます。

## このLabで出てきたファイル

- `backend/src/latch/safety/routes.py` — 4つの窓口(ブロック登録・解除・一覧・通報)
- `backend/src/latch/safety/service.py` — 冪等201・D-23のcancelled化・コミット後DEL
- `backend/src/latch/safety/cache.py` — BlockCache(read-through・TTL・フォールバック)
- `backend/src/latch/safety/store.py` — blocks/reportsのSQL一式
- `backend/src/latch/latches/service.py` — send_messageの判定差し替え(266-271)
