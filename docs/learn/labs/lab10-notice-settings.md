# Lab 10 お知らせと設定を動かす: 5種の通知とドットの一生

- 種別: チュートリアル(手を動かして必ず成功体験を得る。壊しません)
- 前提知識: 第23章(文言のマトリクスとCLOSED_TEXT・「開いた=見た」)・
  第19章(お知らせ一覧のLEFT JOIN)・第21章(ブロックの効果)。
  Lab 5/7のトークン発行とlatches行の直接INSERT・Lab 9の配信ビルドと
  トークンパネルは、同じコマンドを使うので思い出せるとスムーズです
- 所要目安: 30〜40分
- このLabでできるようになること: 第23章で読んだ表示の取り決めを、
  実データと本物のブラウザで確かめられる。具体的には、5種の通知fixtureを
  DBに作り、**API応答をそのまま本物の `notificationLines` に通して文言を
  確かめる**こと・起動時のpreloadでドットが点き、popoverを開くと「開いた=見た」
  の既読で消えること・設定画面(`#/settings`)の通知許可とブロック管理・
  成立済み詳細からのブロック登録と、解除の404までを歩ける
- 次に読むもの: 2026-10-02時点で、このLabが読む順路の最後です。以降の章
  (ジオコーディングのトレース等)は実装の進行とともに追加されます
- 使うもの: ブラウザ1窓と、node(フロントの純関数を呼ぶのに使います)

## 0. このLabで何をするか

第23章で、お知らせと設定の2画面を読みました。行の文言はtype×statusの表で
決まり、不成立の一文は定数1つから全画面へ行き渡る。ドットは「開いていない
新着がある」を意味して、popoverを開けば表示された行が既読になる。

このLabでは、その全部を自分の手で確かめます。道のりはこうです。

1. apiと画面を用意する(Lab 9と同じ、配信ビルド+preview)
2. 2人のユーザーと、4件のIntentを用意する(Lab 5/7と同じ手法)
3. 5本のlatches行と5行のnotifications行を直接INSERTして、**5種のお知らせを
   仕込む**(終了済み・hidden・近くの人・申告の問いかけ…)
4. curlで一覧を取り、LEFT JOINの応答構造を見る(第19章の復習)
5. 実API応答を、本物の `notificationLines` に通す——unit試験と同じ関数が
   本物のデータをどう訳すかを見る
6. ブラウザで: 接続→再読み込み→**ドットが点く**→popoverを開く(5行)→
   **ドットが消える**→開き直して全部既読
7. 「開いた=見た」の裏側をcurlで追試する(もう1回の既読は204・他人は404)
8. 設定画面を開く(通知許可の状態と、空のブロック一覧)
9. 成立済み詳細からブロックを登録し、設定の一覧で解除する。二重解除の404と、
   ブロックがlatchesを閉じた痕跡も見る
10. 片付け

curlとpsqlで仕込み、nodeで確かめ、ブラウザで見る。壊しません(データは
最後に全部消します)。

## 1. apiと画面を用意する

Lab 9 §1〜2と同じです。apiが`(healthy)`であることを確認し、frontendを
配信用にビルドしてpreviewで開きます(ws-8はbackendを1行も変えていないので、
apiのビルドし直しはws-7以降行っていなければ不要です)。

```bash
cd /home/misty/Projects/latch
docker compose ps
curl -s http://127.0.0.1:8000/health
```

期待される出力(apiの行が`(healthy)`であること・server_timeは実行時刻):

```
{"status":"ok","server_time":"2026-10-02T01:04:49.123456+00:00"}
```

```bash
cd /home/misty/Projects/latch/frontend
npm install
npm run build
npm run preview
```

ビルドの出力(ファイル名の英数字部分はビルドごとに変わります):

```
dist/client/assets/index-QlrbVJYN.js  45.78 kB │ gzip:  15.36 kB
✓ built in 284ms
```

previewは http://localhost:4173/ で配信を始めます。このターミナルは
開いたままにして、次からは別のターミナルで作業します。

## 2. 2人のユーザーと、4件のIntentを用意する

Lab 7 §1と同じ手順です(backendディレクトリでIdPトークンを発行し、交換して
ユーザー登録まで)。違うのは**AさんのIntentを3件**保存する点だけです。

```bash
cd /home/misty/Projects/latch/backend
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab10a > /tmp/lab10-idp-a.txt
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab10b > /tmp/lab10-idp-b.txt
```

トークンを交換して、2人分のユーザー登録まで一気にやります(Lab 7 §1と
同じ形)。

```bash
for s in a b; do
  ACCESS=$(curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
    -H "Content-Type: application/json" \
    -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab10-idp-$s.txt)\"}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
  echo "$ACCESS" > /tmp/lab10-access-$s.txt
  curl -s -o /dev/null -w "lab10$s users: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/users \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"display_name":"Lab10'$s'","birth_date":"1990-04-01","profile":{}}'
done
```

期待される出力:

```
lab10a users: 201
lab10b users: 201
```

Intentを保存します。Aさんが3件(a1・a2・a3)、Bさんが1件。ここから先の
`docker compose exec` はリポジトリのルート(`latch/`)で実行するので、
保存し終えたら戻っておいてください。

```bash
cd /home/misty/Projects/latch
START=$(python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(hours=120)).replace(microsecond=0).isoformat())")
ACCESS=$(cat /tmp/lab10-access-a.txt)
for i in "" 2 3; do
  curl -s -o /tmp/lab10-intent-a$i.json -w "lab10a intent$i: %{http_code}\n" \
    -X POST http://127.0.0.1:8000/v1/intents \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"raw_text":"Lab10用'"${i:-1}"':今度の金曜、天文館のあたりで焼肉を食べたい","status":"active","structured_intent":{"category":{"primary":"meal","secondary":"焼肉"},"alcohol_involved":false,"time":{"start":"'$START'","end":null},"location":{"name":"天文館"}}}'
  python3 -c "import json; print(json.load(open('/tmp/lab10-intent-a$i.json'))['intent']['id'])" > /tmp/lab10-intent-a$i.id
done
ACCESS=$(cat /tmp/lab10-access-b.txt)
curl -s -o /tmp/lab10-intent-b.json -w "lab10b intent: %{http_code}\n" \
  -X POST http://127.0.0.1:8000/v1/intents \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
  -d '{"raw_text":"Lab10用:今度の金曜、天文館のあたりで焼肉を食べたい","status":"active","structured_intent":{"category":{"primary":"meal","secondary":"焼肉"},"alcohol_involved":false,"time":{"start":"'$START'","end":null},"location":{"name":"天文館"}}}'
python3 -c "import json; print(json.load(open('/tmp/lab10-intent-b.json'))['intent']['id'])" > /tmp/lab10-intent-b.id
```

期待される出力:

```
lab10a intent: 201
lab10a intent2: 201
lab10a intent3: 201
lab10b intent: 201
```

なぜAさんが3件かというと、latchesには**同じ2人の組で「まだ終わっていない」
行は1本まで**という制約があるからです(第14章の部分UNIQUE索引
`uq_latches_intent_ids_open`・statusがcandidate/proposed/partial_acceptの行)。
このLabで作る5本のうち3本(proposed×2とcandidate=近い候補)は「終わって
いない」行なので、Intentの組が3つ要ります。終わっている2行(cancelledと
completed)は制約の対象外なので、a1×bの組を使い回せます。

Aさんのuser_idも取っておきます(通知の宛先に使います)。

```bash
UID_A=$(docker compose exec -T db psql -U latch -d latch -tA -c "SELECT id FROM users WHERE display_name='Lab10a'")
echo "$UID_A" > /tmp/lab10-uid-a.id
echo "UID_A: $UID_A"
```

## 3. 5種のお知らせを仕込む

作るのは、この5行です(第23章23.2の表を埋める全部入り)。

| # | 通知type | latchのstatus | proposalの形 | 見たいもの |
|---|---|---|---|---|
| 1 | proposal | proposed | 全フィールド版 | 「LATCH候補があります。」+条件サマリ |
| 2 | proposal | proposed | 最小構成(hidden) | 「条件が合う候補があります」+残時間 |
| 3 | proposal | cancelled | 全フィールド版 | **「この提案は成立しませんでした」だけ** |
| 4 | nearby_candidate | candidate | 最小構成 | 存在の一文・**リンクなし** |
| 5 | attendance_request | completed | 全フィールド版 | 「実際に会いましたか?」 |

latches行を5本、まとめてINSERTします(Lab 5 §3・Lab 7 §2と同じ形。
proposalの2つの形は第14章14.4のbuild_proposalの写しです)。

```bash
INTENT_A1=$(cat /tmp/lab10-intent-a.id); INTENT_A2=$(cat /tmp/lab10-intent-a2.id); INTENT_A3=$(cat /tmp/lab10-intent-a3.id); INTENT_B=$(cat /tmp/lab10-intent-b.id)
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO latches (intent_ids, group_candidate_id, proposal, score, status, response_deadline, expires_at, completed_at, created_at)
VALUES
  (ARRAY['$INTENT_A1'::uuid, '$INTENT_B'::uuid], NULL,
   '{\"time_summary\":\"2026-10-09 19:00\",\"area_name\":\"天文館\",\"headcount\":2,\"category_primary\":\"meal\",\"category_secondary\":\"焼肉\",\"budget\":{\"max\":3000},\"match_level\":\"medium\"}'::jsonb,
   0.85, 'proposed', now()+interval '2 hours', now()+interval '120 hours', NULL, now()-interval '10 minutes'),
  (ARRAY['$INTENT_A2'::uuid, '$INTENT_B'::uuid], NULL,
   '{\"headcount\":2,\"match_level\":\"medium\"}'::jsonb,
   0.82, 'proposed', now()+interval '3 hours', now()+interval '120 hours', NULL, now()-interval '40 minutes'),
  (ARRAY['$INTENT_A1'::uuid, '$INTENT_B'::uuid], NULL,
   '{\"time_summary\":\"2026-10-08 18:00\",\"area_name\":\"天文館\",\"headcount\":2,\"category_primary\":\"meal\",\"category_secondary\":\"焼肉\",\"budget\":{\"max\":3000},\"match_level\":\"low\"}'::jsonb,
   0.80, 'cancelled', now()+interval '1 hour', now()+interval '100 hours', NULL, now()-interval '3 hours'),
  (ARRAY['$INTENT_A1'::uuid, '$INTENT_B'::uuid], NULL,
   '{\"time_summary\":\"2026-10-02 19:00\",\"area_name\":\"天文館\",\"headcount\":2,\"category_primary\":\"meal\",\"category_secondary\":\"焼肉\",\"budget\":{\"max\":3000},\"match_level\":\"medium\"}'::jsonb,
   0.85, 'completed', now()+interval '1 hour', now()+interval '100 hours', now()-interval '1 hour', now()-interval '26 hours'),
  (ARRAY['$INTENT_A3'::uuid, '$INTENT_B'::uuid], NULL,
   '{\"headcount\":2,\"match_level\":\"low\"}'::jsonb,
   0.60, 'candidate', now()+interval '1 hour', now()+interval '100 hours', NULL, now()-interval '6 hours')
RETURNING id, status;"
```

期待される出力(idは環境ごとに違います。上から順にL1〜L5と呼び、あとで
使うのは5行目のcompletedのidです):

```
                  id                  |  status
--------------------------------------+---------
 f104ae49-a643-4694-baae-3e43ceb02ee3 | proposed
 850ab326-d80f-4737-b4ba-ed4c56af605c | proposed
 22f40ba8-aa5c-4b7b-aa0a-1777fa6fa13f | cancelled
 0a2ed1a9-fac9-4755-9ade-69fd69033c16 | completed
 38590fa2-5e68-43e5-b522-845306a6ddc2 | candidate
(5 rows)
```

notifications行を5行、Aさん宛てにINSERTします。payloadが
`{"latch_id": "…"}` だけなのは第19章で読んだとおり——先行書き込みは常に
この最小参照で、本文はlatchesをLEFT JOINで埋め込むからです。**作る順番を
上から(L1がいちばん新しい)にしている**のは、一覧がcreated_at降順に並ぶのを
そのまま行番号にするためです。

```bash
UID_A=$(cat /tmp/lab10-uid-a.id)
L1=f104ae49-a643-4694-baae-3e43ceb02ee3; L2=850ab326-d80f-4737-b4ba-ed4c56af605c; L3=22f40ba8-aa5c-4b7b-aa0a-1777fa6fa13f; L5=38590fa2-5e68-43e5-b522-845306a6ddc2
# ↑L1〜L3・L5は、直前のRETURNINGに出た自分の環境のidに読み替えてください
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO notifications (user_id, type, payload, read_at, created_at)
VALUES
  ('$UID_A'::uuid, 'proposal',           '{\"latch_id\":\"$L1\"}', NULL, now()-interval '10 minutes'),
  ('$UID_A'::uuid, 'proposal',           '{\"latch_id\":\"$L2\"}', NULL, now()-interval '40 minutes'),
  ('$UID_A'::uuid, 'proposal',           '{\"latch_id\":\"$L3\"}', NULL, now()-interval '3 hours'),
  ('$UID_A'::uuid, 'nearby_candidate',   '{\"latch_id\":\"$L5\"}', NULL, now()-interval '6 hours'),
  ('$UID_A'::uuid, 'attendance_request', '{\"latch_id\":\"0a2ed1a9-fac9-4755-9ade-69fd69033c16\"}', NULL, now()-interval '26 hours')
RETURNING type, payload->>'latch_id' AS latch_id, created_at;"
```

期待される出力(5行目のattendance_requestのlatch_idは、自分の環境の
completed行=L4のidに読み替えてください):

```
        type           |               latch_id                |          created_at
-----------------------+---------------------------------------+------------------------------
 proposal              | f104ae49-a643-4694-baae-3e43ceb02ee3  | 2026-10-02 01:04:49.53918+00
 proposal              | 850ab326-d80f-4737-b4ba-ed4c56af605c  | 2026-10-02 00:34:49.53918+00
 proposal              | 22f40ba8-aa5c-4b7b-aa0a-1777fa6fa13f  | 2026-10-01 22:14:49.53918+00
 nearby_candidate      | 38590fa2-5e68-43e5-b522-845306a6ddc2  | 2026-10-01 19:14:49.53918+00
 attendance_request    | 0a2ed1a9-fac9-4755-9ade-69fd69033c16  | 2026-09-30 23:14:49.53918+00
(5 rows)
```

## 4. curlで一覧を取る

Aさんでお知らせ一覧を取ります。

```bash
ACCESS=$(cat /tmp/lab10-access-a.txt)
curl -s http://127.0.0.1:8000/v1/notifications?limit=20 -H "Authorization: Bearer $ACCESS" > /tmp/lab10-notifications.json
python3 -c "
import json
d = json.load(open('/tmp/lab10-notifications.json'))
print(json.dumps(d['items'][0], ensure_ascii=False, indent=2))
print('items数:', len(d['items']), '/ next_cursor:', d['next_cursor'])"
```

期待される出力(1行目。時刻とuuidは環境ごとに違います):

```json
{
  "id": "7ceeb3be-b2eb-412a-aa7e-5aa4132b6a04",
  "type": "proposal",
  "latch_id": "f104ae49-a643-4694-baae-3e43ceb02ee3",
  "read_at": null,
  "created_at": "2026-10-02T01:04:49.539180Z",
  "latch": {
    "id": "f104ae49-a643-4694-baae-3e43ceb02ee3",
    "status": "proposed",
    "response_deadline": "2026-10-02T03:14:40.246339Z",
    "expires_at": "2026-10-07T01:14:40.246339Z",
    "completed_at": null,
    "proposal": {
      "budget": {"max": 3000},
      "area_name": "天文館",
      "headcount": 2,
      "match_level": "medium",
      "time_summary": "2026-10-09 19:00",
      "category_primary": "meal",
      "category_secondary": "焼肉"
    }
  }
}
items数: 5 / next_cursor: None
```

第19章19.5の応答例と、形が同じであることを確認してください。`read_at` が
全部null(未読)であること・payloadに書いたlatch_idだけでなくlatchの塊が
埋め込まれていること(LEFT JOIN)・`my_response` という鍵が**ない**こと
(第23章23.2の終了行が一律一文になる理由)。

## 5. 実API応答を、本物の純関数に通す

ここがこのLabの山場です。画面は `view.js` の `notificationLines` にこの
応答を渡して文言を組んでいます。ならば、同じことをここでやってみれば、
**本物のAPI応答が本物の関数でどう訳されるか**を、画面なしで確かめられます。
frontendはES Modulesなので、nodeから直接importできます。

```bash
cat > /tmp/lab10-view.mjs <<'EOF'
import { notificationLines, notificationHref } from "/home/misty/Projects/latch/frontend/src/latch/view.js";
import { readFileSync } from "node:fs";

const data = JSON.parse(readFileSync(process.argv[2], "utf8"));
const nowIso = new Date().toISOString();
for (const item of data.items) {
  const lines = notificationLines(item, nowIso);
  const href = notificationHref(item);
  console.log(`--- type=${item.type} latch.status=${item.latch?.status ?? "null"}`);
  console.log(`  行の文言: ${JSON.stringify(lines)}`);
  console.log(`  タップ先: ${href ?? "(リンクなし)"}`);
}
EOF
node /tmp/lab10-view.mjs /tmp/lab10-notifications.json
```

期待される出力(「あと2時間59分で締切」の行は残時間なので、実行時刻に
よって変わります):

```
--- type=proposal latch.status=proposed
  行の文言: ["LATCH候補があります。","2026-10-09 19:00","天文館","2人","焼肉","ひとり3,000円まで","一致度が高い候補です。"]
  タップ先: #/latches/f104ae49-a643-4694-baae-3e43ceb02ee3
--- type=proposal latch.status=proposed
  行の文言: ["条件が合う候補があります","一致度が高い候補です。","あと2時間59分で締切"]
  タップ先: #/latches/850ab326-d80f-4737-b4ba-ed4c56af605c
--- type=proposal latch.status=cancelled
  行の文言: ["この提案は成立しませんでした"]
  タップ先: #/latches/22f40ba8-aa5c-4b7b-aa0a-1777fa6fa13f
--- type=nearby_candidate latch.status=candidate
  行の文言: ["近い条件の候補があるようです。"]
  タップ先: (リンクなし)
--- type=attendance_request latch.status=completed
  行の文言: ["実際に会いましたか?"]
  タップ先: #/latches/0a2ed1a9-fac9-4755-9ade-69fd69033c16
```

第23章23.2の表が、そのまま出ました。2行目はproposalが最小構成
(`time_summary`がない)なので条件サマリが出ず、代わりに締切の残時間が
並ぶ。3行目はcancelledなのでCLOSED_TEXTの1行。4行目のnearbyは文言が
存在の一文だけで、タップ先がない。5行目の申告は問いかけだけ。

試験(`npm test`のlatch-notice.test.js)が検証している関数と、ブラウザが
使っている関数と、このnode実行で呼んだ関数は**同じ1つ**です。この一致が、
「217件がブラウザなしで走る」こと(第8章8.7)の実感になります。

## 6. ブラウザでドットの一生を見る

ブラウザで http://localhost:4173/ を開きます(Lab 9 §3と同じ導線)。

1. **接続する**: トークンパネルに `/tmp/lab10-idp-a.txt` のIdPトークンを
   貼って「接続する」を押します。接続直後は、まだドットは点いていません
   (起動時のpreloadはページ読み込みのタイミングで走るもので、接続の直後には
   呼ばれない設計です。次にpopoverを開いたときに取得されます)
2. **再読み込みする**: F5でページを再読み込みしてください。refreshトークン
   からaccessが回復し(第8章8.4)、起動時のpreloadが走ります。**ベルの横に
   ドットが点きます**(未読5行があるため)
3. **popoverを開く**: ベルをクリック。5行が並びます。§5で確かめた文言が
   そのまま並んでいること・いちばん下のnearbyの行(「近い条件の候補が
   あるようです。」)にだけ**カーソルが乗らない**(リンクがない)こと・
   未読の行に強調(unreadスタイル)が付いていること・右端に「10月2日 10:04」
   のような時刻が付いていることを見てください
4. **ドットが消える**: 開いた時点で「開いた=見た」の既読が走り、ドットが
   消えます
5. **開き直す**: 一度閉じて(ベルをもう1回)、もう1度開く。今度は未読の
   強調が全部消えています(全行既読)

## 7. 「開いた=見た」の裏側をcurlで追試する

popoverを開いただけで、何が起きたでしょう。curlで確かめます。

```bash
ACCESS=$(cat /tmp/lab10-access-a.txt)
ACCESS_B=$(cat /tmp/lab10-access-b.txt)
N1=$(python3 -c "import json; print(json.load(open('/tmp/lab10-notifications.json'))['items'][0]['id'])")
curl -s -o /dev/null -w "もう1回の既読: %{http_code}\n" -X POST http://127.0.0.1:8000/v1/notifications/$N1/read -H "Authorization: Bearer $ACCESS"
curl -s -o /dev/null -w "Bさん(他人): %{http_code}\n" -X POST http://127.0.0.1:8000/v1/notifications/$N1/read -H "Authorization: Bearer $ACCESS_B"
curl -s http://127.0.0.1:8000/v1/notifications?limit=20 -H "Authorization: Bearer $ACCESS" | python3 -c "
import json,sys
for i in json.load(sys.stdin)['items']:
    print(i['type'], i['latch']['status'], 'read_at=' + ('null' if i['read_at'] is None else i['read_at'][:19]))"
```

期待される出力(最後の5行の時刻は、popoverを開いた時刻になります):

```
もう1回の既読: 204
Bさん(他人): 404
proposal proposed read_at=2026-10-02T01:15:14
proposal proposed read_at=2026-10-02T01:15:14
proposal cancelled read_at=2026-10-02T01:15:14
nearby_candidate candidate read_at=2026-10-02T01:15:14
attendance_request completed read_at=2026-10-02T01:15:14
```

読み取ってほしいことは3つです。①popoverを開いただけで5行全部のread_atが
埋まっている(あなたが何も送っていなくても、開いた時点で画面がPOST readを
送った)②すでに既読の行にもう1回送っても**204**(冪等だから、二重送信は
無害)③Bさん(通知の持ち主でない人)が同じidへ送ると**404**(他人のidは
存在しないidと同じ扱い。第19章19.6)。ブラウザの「開いた=見た」は、この
契約の上に載っています(第23章23.4)。

## 8. 設定画面を開く

右上のアカウントボタン(丸アイコン)を押すとメニュー(popover)が出るので、
「設定」をクリックします。URLが `http://localhost:4173/#/settings` に
変わって、設定画面が開きます(Lab 9で読んだhashルーターが、この1画面の
ために増えた分岐です)。

見てください。

- **通知セクション**: 「通知は許可されていません」(許可をまだ決めていない
  =defaultならこの文言)と、[通知の許可を求める]ボタン。そしてどの状態にも
  添えられる「許可しなくても、アプリ内のお知らせで確認できます。」
  (D-18のフォールバック保証)。ボタンはdefaultの間だけ表示されます。押すと
  ブラウザの許可を尋ねる操作が始まり、その結果で文言が切り替わります
  (grantedなら「許可されています」へ。ブロックされると、再要求は効かなく
  なるので案内文言へ)。実際に押して、自分のブラウザでの挙動を確かめて
  みてください
- **ブロック管理セクション**: 「ブロックしているユーザーはいません」の
  空状態(まだ誰もブロックしていないため)

## 9. 成立済み詳細からブロックする

最後に、第23章23.6の導線を通ります。§3で作った5行のうち**completedの行
(L4)**の詳細を開きます(URLは `http://localhost:4173/#/latches/0a2ed1a9-…`
——§5の期待される出力にあったタップ先と同じidです)。

1. **導線**: 成立済みの詳細画面に「このユーザーをブロックする」があります
   (通報の導線と同じ並び。提案段階の詳細には置かれていない理由は
   第23章23.6)
2. **確認**: 押すと確認モーダル「ブロックすると、このやりとりは利用できなく
   なります。」が出ます
3. **実行**: [ブロックする]を押すと、トースト「ブロックしました」が出て、
   詳細が再取得されます
4. **一覧**: 設定画面(`#/settings`)を開き直すと、ブロック管理に
   **Lab10b / 10月2日 10:15** のような行が並びます(display_nameとブロック日)
5. **解除**: [解除する]を押すと、もう1つ確認モーダル「Lab10bさんのブロックを
   解除しますか?」が挟まり、[解除する]で行が消えて空状態に戻ります

ブラウザの外から、2つ確かめておきます。1つは**二重解除の404**です。

```bash
UID_B=$(docker compose exec -T db psql -U latch -d latch -tA -c "SELECT id FROM users WHERE display_name='Lab10b'")
ACCESS=$(cat /tmp/lab10-access-a.txt)
curl -s -o /dev/null -w "もう1回の解除: %{http_code}\n" -X DELETE http://127.0.0.1:8000/v1/users/$UID_B/block -H "Authorization: Bearer $ACCESS"
```

期待される出力:

```
もう1回の解除: 404
```

解除は冪等にしていないので、消えた行へのDELETEは404です(第21章21.5)。
画面がこの404を通信エラーとして出さず、一覧の再取得で収束させたことは
第23章23.5で読みました。

もう1つは、ブロックが**latchesを閉じた痕跡**です。

```bash
docker compose exec -T db psql -U latch -d latch -c "SELECT id, status FROM latches ORDER BY created_at DESC"
```

期待される出力(idは§3で作った5行。statusを見てください):

```
                  id                  |  status
--------------------------------------+----------
 f104ae49-a643-4694-baae-3e43ceb02ee3 | cancelled
 850ab326-d80f-4737-b4ba-ed4c56af605c | cancelled
 22f40ba8-aa5c-4b7b-aa0a-1777fa6fa13f | cancelled
 38590fa2-5e68-43e5-b522-845306a6ddc2 | cancelled
 0a2ed1a9-fac9-4755-9ade-69fd69033c16 | completed
(5 rows)
```

§3ではproposedが2本・candidateが1本あったのに、全部cancelledになって
います。AさんがBさんをブロックした瞬間、2人の間の「まだ終わっていない」
LATCHが全部閉じられたのです(第21章21.3の3つの効果の1つ目)。completedは
対象外で、そのまま残ります。

**順番の注意**: ブロックは仕込みの天敵です。もしこのLabの
途中でブロックを先に試すと、お知らせのfixture(proposedの行)がcancelledに
閉じられて、§5・§6の文言が全部「この提案は成立しませんでした」に変わって
しまいます(解除しても、閉じた歴史は戻りません)。だからブロックは最後の
§9に置いてあります。順路を守っていれば起きません。

## 10. 片付け

Lab 9 §11と同じ流儀で、lab10プレフィックスのデータをFK順に消します。
blocksは解除済みでも念のため入れておきます。

```bash
docker compose exec -T db psql -U latch -d latch -c "
DELETE FROM match_candidates WHERE intent_a_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab10%')) OR intent_b_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab10%'));
DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab10%');
DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab10%')));
DELETE FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab10%'));
DELETE FROM blocks WHERE blocker_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab10%') OR blocked_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab10%');
DELETE FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab10%');
DELETE FROM users WHERE auth_subject LIKE 'learn-lab10%';
SELECT (SELECT count(*) FROM users WHERE auth_subject LIKE 'learn-lab10%') AS users_left;"
```

期待される出力(最後の1行):

```
 users_left
------------
          0
(1 row)
```

消す順序がFKの依存と逆だと、途中のDELETEが外部キー違反で失敗します。
psqlの `-c "…; …;"` は1引数に並べた文を**単一のトランザクション**で実行
するので、1つでも失敗すると全部元に戻ります(何も消えていないように見える
のはこのため)。参照する側(match_candidates・notifications・events)から
消して、参照される側(intents・users)を最後に。テーブル間の依存を、掃除で
もう1回なぞったことになります。

ブラウザの窓を閉じて、`npm run preview` のターミナルは Ctrl+C で停止
してください。`/tmp/lab10-*.txt` の一時ファイルも不要なら消して構いません。

## 11. このLabで確かめたこと

- 5種の通知fixtureをDBに作れば、実API応答を本物の `notificationLines` に
  通して、第23章23.2の表の文言を1つずつ確かめられる(nodeから直接import)
- 起動時のpreloadでドットが点く。popoverを開くと「開いた=見た」の既読が
  走ってドットが消える。開き直せば全部既読
- 既読の再送は204(冪等)・他人のidは404。ブラウザの規則はこの契約の上にある
- 設定画面は `#/settings`。通知許可は状態の表示と取り直し(D-18の
  フォールバック文言つき)、ブロック管理は一覧・確認モーダル・解除
- ブロックの登録導線は成立済み詳細だけ。実行すると2人間のopenなlatchesが
  cancelledに閉じ、解除404は冪等でない設計の実物

**自分のノートに書くこと**: 「popoverを開くだけで何本のHTTPが飛んだか?
そのうち壊れても握られるのはどれか?」——第23章23.4を読み返しながら、
5行ぶんのPOST readと、ドット再判定を思い出して書いてみてください。
