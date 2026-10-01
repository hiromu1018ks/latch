# Lab 6 時間を進める: 期限切れバッチを自分の手で起こす

- 種別: チュートリアル(手を動かして必ず成功体験を得る。壊しません)
- 前提知識: 第18章(ExpirySweeperとResetJobの全体)。Lab 1のpsql・`docker compose logs` の
  使い方と、Lab 5のトークン発行・Intent保存・latches行の直接INSERTは思い出せると
  スムーズです(同じコマンドを使います)
- 所要目安: 30〜40分(うち数分は、60秒周期のバッチを待つ時間です)
- このLabでできるようになること: 時間が経つのを待たずに**期限切れを自分で起こして**、
  ExpirySweeperが60秒周期でDBを閉じていくのを観察できる。具体的には、
  Intentの期限切れ(expiredイベント)、提案(proposed)の回答期限切れ(遷移の台帳)、
  成立済み(matched)の完了(completed)と実施自己申告の通知、をそれぞれ実データで
  確認できる。最後に、閉じた提案への返事が409で断られることも確かめる
- 次に読むもの: `concepts/19-notifications.md`(第19章)。このLabで先行書き込み
  された通知が、どう届けられるかを読みます

## 0. このLabで何をするか

第18章で、誰も操作しない遷移(期限切れ・完了)を60秒周期のバッチが閉じていく
仕組みを読みました。読んだだけの状態では、sweeperは「いずれ動いているもの」です。
このLabでは、**期限切れを自分の手で起こして**、その瞬間をログとDBで観察します。

時計を進める代わりに、DBの期限の列を過去へ書き換えます。期限を現在時刻より前に
すれば、「もう期限が切れている」状態が即座に完成するからです。保存APIは過去の
`expires_at` を受け付けないので(422・第6章6.3)、psqlでの書き換えが必要になります。
開発現場でも同じです。期限切れの試験は、APIでは保存できない過去時刻をfixtureで
DBに直接書いて再現します(ws-2の統合試験 `test_expiry_batches.py` がまさにこの
作りです。FakeClockを進める経路と、データを過去にする経路の2つがあります)。

観察するのは、第18章の表の1〜3番です。

1. Intentの期限切れ(statusがexpiredへ・expiredイベントがmatch_eventsへ)
2. 提案の回答期限切れ(latchesがexpiredへ・台帳latch_status_eventsへ)
3. 成立済みの完了(matched→completed・実施自己申告の通知2人分)

### 始める前に: workerのイメージは新しいか

このLabの主役はworkerの中のsweeperです。1つだけ、大事な確認が要ります。
**動いているコンテナは、イメージを作った時点のコードのまま動き続けます**。
gitで新しいコードを取得しても、イメージを作り直していなければ、コンテナの中は
古いコードのままです。sweeperが入っていないかもしれません。実際、このLabを
検証したとき、workerイメージがマージ前のままで、いつまで待ってもsweeperが
動かない、ということが起きました。

```bash
make ps    # api・db・redis が (healthy) で、worker が Up であることを確認
docker compose exec -T worker ls src/latch/worker/
```

期待される出力(2026-10-01に実行したもの。末尾の `sweeper.py` と `reset.py` が
並んでいること。__pycache__ は実行環境で自動的にできるキャッシュです):

```
__init__.py
__main__.py
__pycache__
backfill.py
cost
debounce.py
embedding.py
embedding_text.py
jev.py
main.py
matching
reeval.py
reset.py
stage1.py
sweeper.py
```

`ls` の結果に `sweeper.py` がなければ、イメージを作り直してから起動し直します
(数分かかります)。

```bash
docker compose build worker && docker compose up -d worker
```

workerは常設で動いていて構いません。このLabでは、常設のworkerが本物の時計で
sweeperを回してくれるのを観察します(test-ciとは違い、workerを止めません)。

以降、§1は `backend` ディレクトリで、§2以降のpsqlとログはリポジトリのルート
(`latch/`)で実行します(Lab 5と同じ約束です)。

## 1. ユーザーと、期限つきIntentを用意する

登場人物は2人。Lab 4・Lab 5と同じ手順で、開発用トークンを発行して交換して、
ユーザー登録まで済ませます。backend ディレクトリで実行します。

```bash
cd /home/misty/Projects/latch/backend
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab6a > /tmp/lab6-idp-a.txt
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab6b > /tmp/lab6-idp-b.txt
for s in a b; do
  ACCESS=$(curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
    -H "Content-Type: application/json" \
    -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab6-idp-$s.txt)\"}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
  echo "$ACCESS" > /tmp/lab6-access-$s.txt
  curl -s -o /dev/null -w "lab6$s users: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/users \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"display_name":"Lab6'$s'","birth_date":"1990-04-01","profile":{}}'
done
```

期待される出力:

```
lab6a users: 201
lab6b users: 201
```

次にIntentを2人分保存します。このLabならではの工夫が1つ。**有効期限
(expires_at)を3時間後に指定**します。期限は `structured_intent` の**中に**入れ
ます(第6章の保存形式。リクエストのトップレベルに置いても無視されるので注意)。
time.startはLab 5と同じく未来の値を手で入れます(§4で過去へ書き換えます)。

```bash
START=$(python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(hours=120)).replace(microsecond=0).isoformat())")
EXP=$(python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(hours=3)).replace(microsecond=0).isoformat())")
for s in a b; do
  ACCESS=$(cat /tmp/lab6-access-$s.txt)
  curl -s -o /tmp/lab6-intent-$s.json -w "lab6$s intents: %{http_code}\n" \
    -X POST http://127.0.0.1:8000/v1/intents \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"raw_text":"Lab6用:今度の金曜、天文館のあたりで焼肉を食べたい","status":"active","structured_intent":{"category":{"primary":"meal","secondary":"焼肉"},"alcohol_involved":false,"time":{"start":"'$START'","end":null},"location":{"name":"天文館"},"expires_at":"'$EXP'"}}'
done
```

期待される出力:

```
lab6a intents: 201
lab6b intents: 201
```

保存したidを変数に入れて、リポジトリのルートへ移動します。

```bash
INTENT_A=$(python3 -c "import json; print(json.load(open('/tmp/lab6-intent-a.json'))['intent']['id'])")
INTENT_B=$(python3 -c "import json; print(json.load(open('/tmp/lab6-intent-b.json'))['intent']['id'])")
echo "A: $INTENT_A"
echo "B: $INTENT_B"
cd /home/misty/Projects/latch
```

期待される出力(値は環境ごとに違います):

```
A: 73a6becc-ced2-4250-8de2-e5546d98653c
B: 797416b8-2ac5-4adf-822b-7f4f3fa1e67b
```

## 2. Intentの期限切れを起こす

まず、今の状態を確認します。AさんのIntentは `status=active` で、`expires_at` は
3時間後のはずです。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT id, status, expires_at FROM intents WHERE id = '$INTENT_A'::uuid;"
```

期待される出力(時刻は実行時によります。expires_atは保存から3時間後のはずです):

```
                  id                  | status |      expires_at
--------------------------------------+--------+------------------------
 73a6becc-ced2-4250-8de2-e5546d98653c | active | 2026-10-01 05:25:20+00
(1 row)
```

期限を1分前へ書き換えます。これでこのIntentは「もう期限切れ」です。

```bash
docker compose exec -T db psql -U latch -d latch -c "
UPDATE intents SET expires_at = now() - interval '1 minute'
WHERE id = '$INTENT_A'::uuid RETURNING id, status, expires_at;"
```

期待される出力:

```
                  id                  | status |          expires_at
--------------------------------------+--------+-------------------------------
 73a6becc-ced2-4250-8de2-e5546d98653c | active | 2026-10-01 02:24:30.945596+00
(1 row)

UPDATE 1
```

statusはまだ `active` のままです。**sweeperが来ていないから**です。ここからが
このLabの主役の時間です。workerのログを見ながら、60秒周期のtickを待ちます。

```bash
docker compose logs -f --since 1m worker
```

**実行する前に予測を書いてください**(my-notes.md へ)。ログには何という単語が
出てくるでしょうか?第18章18.5で読んだ、イベント発行の慣行を思い出すと、
DBに何が起きるかも予測できます。

最大60秒ほど待つと、次の1行が流れてきます(見たらCtrl+Cで止めて構いません)。

```
worker-1  | 2026-10-01 02:26:02,212 INFO latch.worker.sweeper sweeper.intent_expired intent_id=73a6becc-ced2-4250-8de2-e5546d98653c
```

`latch.worker.sweeper` がログの出どころ(第18章のsweeper.py)、
`sweeper.intent_expired` が「Intentを期限切れにした」の印です。DBを確かめます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT id, status, expires_at FROM intents WHERE id = '$INTENT_A'::uuid;"
docker compose exec -T db psql -U latch -d latch -c "
SELECT event_type, payload->>'version' AS version, processed_at IS NOT NULL AS processed
FROM match_events WHERE source_intent_id = '$INTENT_A'::uuid ORDER BY created_at;"
```

期待される出力(2つの問い合わせを順に):

```
                  id                  | status  |          expires_at           
--------------------------------------+---------+-------------------------------
 73a6becc-ced2-4250-8de2-e5546d98653c | expired | 2026-10-01 02:24:30.945596+00
(1 row)

     event_type      | version | processed
---------------------+---------+-----------
 created             | 1       | t
 embedding_completed | 1       | t
 expired             | 1       | t
(3 rows)
```

`created` と `embedding_completed` は第9章・第10章で見たとおり、保存と埋め込みの
ときに書かれた行です。その下に、今のtickで **`expired`** が1行増えています。
第18章18.5のとおり、遷移とイベントが同じトランザクションで書かれ、Stage1が
「処理実体なし」で受理した結果(processed=t)です。BさんのIntentはactiveのまま、
次の節で使います。

## 3. 提案の回答期限を切らす

次はlatchesのほうです。latches行には**2人ぶんのIntentのid**が要るので、まず
Aさんでもう1本、Intentを保存します(Aさんの1本目は§2で期限切れにしたので、
新しいidが必要です)。backend ディレクトリで実行します。

```bash
cd /home/misty/Projects/latch/backend
ACCESS=$(cat /tmp/lab6-access-a.txt)
START=$(python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(hours=120)).replace(microsecond=0).isoformat())")
curl -s -o /tmp/lab6-intent-c.json -w "lab6c intents: %{http_code}\n" \
  -X POST http://127.0.0.1:8000/v1/intents \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"raw_text":"Lab6用C:今度の金曜、天文館のあたりで焼肉を食べたい","status":"active","structured_intent":{"category":{"primary":"meal","secondary":"焼肉"},"alcohol_involved":false,"time":{"start":"'$START'","end":null},"location":{"name":"天文館"}}}'
INTENT_C=$(python3 -c "import json; print(json.load(open('/tmp/lab6-intent-c.json'))['intent']['id'])")
echo "C: $INTENT_C"
cd /home/misty/Projects/latch
```

期待される出力:

```
lab6c intents: 201
C: ad7e6f84-9cff-4e72-b4a7-e3a89f75ff8a
```

材料が揃ったので、Lab 5と同じ流儀で提案の行を1行、psqlで直接作ります。
返事待ち(proposed)・回答期限は2時間後・Intentの寿命(expires_at)はその先、
という設定です。

```bash
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO latches
  (intent_ids, group_candidate_id, proposal, score, status,
   response_deadline, expires_at, created_at)
VALUES
  (ARRAY['$INTENT_B'::uuid, '$INTENT_C'::uuid],
   NULL,
   '{\"headcount\": 2, \"match_level\": \"medium\"}'::jsonb,
   0.85, 'proposed',
   now() + interval '2 hours',
   now() + interval '120 hours',
   now())
RETURNING id, status, response_deadline;"
```

期待される出力(時刻は実行時によります。idはこのあと何度も使うので控えておきます):

```
                  id                  | status  |     response_deadline
--------------------------------------+---------+---------------------------
 00825b40-8493-42d8-8b30-015f249c23fb | proposed | 2026-10-01 04:27:14.527909+00
(1 row)

INSERT 0 1
```

あとは§2と同じ作法です。回答期限を1分前へ書き換えて、sweeperを待ちます。

```bash
LATCH1=00825b40-8493-42d8-8b30-015f249c23fb   # さっき控えたid
docker compose exec -T db psql -U latch -d latch -c "
UPDATE latches SET response_deadline = now() - interval '1 minute'
WHERE id = '$LATCH1'::uuid RETURNING id, status, response_deadline;"
docker compose logs -f --since 1m worker
```

期待される出力(UPDATE。時刻は実行時によります):

```
                  id                  | status  |     response_deadline
--------------------------------------+---------+---------------------------
 00825b40-8493-42d8-8b30-015f249c23fb | proposed | 2026-10-01 02:26:23.500042+00
(1 row)

UPDATE 1
```

60秒ほどで、今度はこういう行が流れます。

```
worker-1  | 2026-10-01 02:28:02,249 INFO latch.worker.sweeper sweeper.expired latch_id=00825b40-8493-42d8-8b30-015f249c23fb from=proposed
```

`from=proposed` は、遷移前のstatusを出力しています。DBで、latches本体と、
遷移の台帳(latch_status_events・第17章17.6)を確認します。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT l.status, e.from_status, e.to_status, e.user_id, e.created_at
FROM latches l JOIN latch_status_events e ON e.latch_id = l.id
WHERE l.id = '$LATCH1'::uuid;"
```

期待される出力:

```
 status  | from_status | to_status | user_id |          created_at
---------+-------------+-----------+---------+-------------------------------
 expired | proposed    | expired   |         | 2026-10-01 02:28:02.232786+00
(1 row)
```

`user_id` の欄が**空(NULL)**であることがポイントです。Lab 5で見た回答経路の
台帳には、返事をした本人のuser_idが入っていました。今回は遷移を起こしたのは
ユーザーではなくシステム(sweeper)なので、NULL。第18章18.4で読んだ約束が、
実データでこの1行に現れています。

## 4. 成立済みの提案を完了させる

3つめは、時間が状態を**前へ**進めるほうの遷移です(matched→completed)。第18章
18.6で読んだとおり、対象時刻は「参加Intentのtime_startの最大値」。そこで、

1. latches行を `status='matched'`(成立済み)で作る
2. 参加Intentのtime_startを過去へ書き換える

この2段階で「会の対象時刻がもう過ぎている成立済みの提案」を作ります。参加する
Intentは§3と同じ2本(BさんとCさん)で構いません。同じ組み合わせなのに作れるのは、
§3の行がもうexpiredに閉じているからです(第14章14.3の部分UNIQUE索引0004は
「開いている提案」の重複だけを防ぎます)。

matchedなlatches行を作ります。回答期限や作成時刻はもう過去の値にしておきます
(成立済みなら返事の期限は意味を持ちません)。

```bash
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO latches
  (intent_ids, group_candidate_id, proposal, score, status,
   response_deadline, expires_at, created_at)
VALUES
  (ARRAY['$INTENT_B'::uuid, '$INTENT_C'::uuid],
   NULL,
   '{\"headcount\": 2, \"match_level\": \"medium\"}'::jsonb,
   0.85, 'matched',
   now() - interval '1 hours',
   now() + interval '120 hours',
   now() - interval '2 hours')
RETURNING id, status;"
```

期待される出力:

```
                  id                  | status
--------------------------------------+---------
 9b98838e-1bc8-479d-89f5-c8e7c4ceffa7 | matched
(1 row)

INSERT 0 1
```

**ここで一旦止まって予測してください**。この行はまだcompletedになりません。
なぜか、第18章18.6の抽出SQLのWHERE句から説明できるはずです。

予測を書いたら、参加2人のIntentのtime_startを30分前へ書き換えます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
UPDATE intents SET time_start = now() - interval '30 minutes'
WHERE id IN ('$INTENT_B'::uuid, '$INTENT_C'::uuid) RETURNING id, time_start;"
LATCH2=9b98838e-1bc8-479d-89f5-c8e7c4ceffa7
docker compose logs -f --since 1m worker
```

期待される出力(UPDATE):

```
                  id                  |          time_start
--------------------------------------+-------------------------------
 797416b8-2ac5-4adf-822b-7f4f3fa1e67b | 2026-10-01 01:59:06.685213+00
 ad7e6f84-9cff-4e72-b4a7-e3a89f75ff8a | 2026-10-01 01:59:06.685213+00
(2 rows)

UPDATE 2
```

60秒ほど待つと、3種類めのログが流れます。

```
worker-1  | 2026-10-01 02:30:02,286 INFO latch.worker.sweeper sweeper.completed latch_id=9b98838e-1bc8-479d-89f5-c8e7c4ceffa7
```

DBで、latches・台帳・notificationsの3つを見ます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
SELECT status, completed_at FROM latches WHERE id = '$LATCH2'::uuid;"
docker compose exec -T db psql -U latch -d latch -c "
SELECT e.from_status, e.to_status, e.user_id FROM latch_status_events e WHERE e.latch_id = '$LATCH2'::uuid;"
docker compose exec -T db psql -U latch -d latch -c "
SELECT type, payload->>'latch_id' AS latch_id, created_at FROM notifications
WHERE payload->>'latch_id' = '$LATCH2' ORDER BY created_at;"
```

期待される出力(3つの問い合わせを順に):

```
  status   |         completed_at          
-----------+-------------------------------
 completed | 2026-10-01 02:30:02.262324+00
(1 row)

 from_status | to_status | user_id 
-------------+-----------+---------
 matched     | completed | 
(1 row)

        type        |               latch_id               |          created_at
--------------------+--------------------------------------+-------------------------------
 attendance_request | 9b98838e-1bc8-479d-89f5-c8e7c4ceffa7 | 2026-10-01 02:30:02.262324+00
 attendance_request | 9b98838e-1bc8-479d-89f5-c8e7c4ceffa7 | 2026-10-01 02:30:02.262324+00
(2 rows)
```

参加者が2人(lab6a・lab6b)なので、`type='attendance_request'` の通知が**2行**、
同じ時刻で書かれています。payloadは `{"latch_id": ...}` の1つだけ——第18章18.6で
読んだ「最小参照」の先行書き込みです。手順を書いた時点ではスマホへの送信は
まだ実装されていませんでしたが、今は実装済みです(第19章。workerログに
`latch.push.send` のドライラン記録が流れるようになっています)。送るべき事実だけが、
完了と同じトランザクションで記録される、というこの構造は変わりません。
`created_at` がcompleted_atと同一時刻であることにも注目してください。1tick=1時刻
(第18章18.2)の約束が、ここにも現れています。

## 5. 閉じた提案に答えてみる

最後に、第17章との接続を1つ確かめます。§3で期限切れ(expired)にした提案に、
今から返事を送ったらどうなるでしょう。**実行する前に予測を書いてください**。
ステータスコードは?エラーのcodeは何でしょう?

```bash
ACCESS=$(cat /tmp/lab6-access-a.txt)
curl -s -X POST http://127.0.0.1:8000/v1/latches/$LATCH1/response \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"response":"yes"}' | python3 -m json.tool --no-ensure-ascii
```

期待される出力:

```json
{
    "error": {
        "code": "LATCH_EXPIRED",
        "message": "response deadline passed",
        "details": null
    }
}
```

409 LATCH_EXPIREDです。curlの `-s` 出力にはHTTPステータスが含まれないので、
気になるなら `-w "\n%{http_code}\n"` を付けて確かめてください(409が返ります)。
回答APIは、自分のWHERE句で「いま回答できる状態か」を検品します(第17章17.5)。
sweeperが先にこの行をexpiredにしていても、いなくても、期限の切れた提案への
返事は受理されません。**期限の真実は行そのもの**にあり、sweeperは行を期限切れの
形に整える係——第18章18.4の分担を、APIの応答で確かめられました。

## 6. 片付け

Lab 5と同じ流儀で、lab6プレフィックスのデータをFK順に削除します。今回の
Labが書いたテーブルはlatches・latch_status_events・notifications・match_events・
match_candidates・intents・usersです。

```bash
docker compose exec -T db psql -U latch -d latch -c "
DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab6%')));
DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab6%');
DELETE FROM match_candidates WHERE intent_a_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab6%'))
   OR intent_b_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab6%'));
DELETE FROM match_events WHERE source_intent_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab6%'));
DELETE FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab6%'));
DELETE FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab6%');
DELETE FROM users WHERE auth_subject LIKE 'learn-lab6%';
SELECT (SELECT count(*) FROM latch_status_events WHERE latch_id NOT IN (SELECT id FROM latches)) AS events_orphan,
       (SELECT count(*) FROM users WHERE auth_subject LIKE 'learn-lab6%') AS users_left;"
```

期待される出力(DELETEの行数は、裏でworkerが書いた評価行などの分だけ環境ごとに
多少変わります。最後の2つの数値が0になれば成功です):

```
DELETE 2
DELETE 2
DELETE 2
DELETE 7
DELETE 2
DELETE 3
DELETE 2
 events_orphan | users_left
---------------+------------
            0 |          0
(1 row)
```

`events_orphan`(孤児=参照先のlatchesがもうない台帳の行)が0であることは、
latch_status_eventsをlatchesより先に消したことの確認です。FKを持つテーブルは
参照される側より先に消す——Lab 5と同じ約束を、今回増えた2つのテーブル
(latch_status_events・notifications)にも広げました。

## 7. 振り返りとノート

このLabで、あなたは第18章のバッチを自分の手で起こして観察しました。my-notes.md
に、次の3つを書き残すのをおすすめします。

1. **§2〜§4の3種類のログ**(`sweeper.intent_expired`・`sweeper.expired`・
   `sweeper.completed`)。それぞれ何が遷移して、DBのどのテーブルに何が書かれたか。
   台帳(latch_status_events)だけは3つとも同じ形式だった理由
2. **§4で matched 行を作っただけではcompletedにならなかった理由**。抽出SQLの
   WHERE句のどの部分が、あなたの書き換えで初めて真になったか
3. **§5の409**。sweeperがいなかったら(イメージが古いworkerだったら)、この返事は
   受理されたと思うか、されなかったと思うか。その根拠を、回答APIの検品の
   仕組みから説明する

疑問が残ったら、それもノートに書いてください。通知の送信(このLabで先行書き込み
されたattendance_requestをどう届けるか)は第19章の題材です。プッシュの文面を型で
縛る話と、お知らせ一覧(このLabで見たnotificationsの2行を、届いた形で読み出すAPI)
がそこで待っています。
