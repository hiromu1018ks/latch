# Lab 4 画面の裏側を全部見る: フロントエンドの結合をターミナルでなぞり、ブラウザで確かめる

- 種別: チュートリアル(手を動かして必ず成功体験を得る。壊しません)
- 前提知識: 第8章(特に8.2 proxy・8.4 トークン・8.5 エラー分岐・8.6 expiry-options)。
  Lab 1(4サービスの生存確認)も済んでいること
- 所要目安: 40〜50分
- このLabでできるようになること: 画面で1文を入力してから保存されるまでの間に、
  裏で走るHTTPを全部自分の手で送れる。トークンパネルや条件リストが、
  「どのリクエストの結果として表示されているか」を説明できる
- 次に読むもの: なし(ここまでが、いま実装済みの範囲。実装が進めば章とLabが増えます)

## 0. このLabで何をするか

フロントエンドは、いままで学んできたAPIの**見える面**です。例文を入力すると条件リストが
組み替わり、「預ける」を押すと確認モーダルが開く。しかし画面の操作は1つずつが、
裏では **HTTPリクエスト1本**に対応しています。このLabでは、そのリクエストを順番に
curlで送って(=画面の裏側を全部なぞって)から、最後にブラウザで同じ流れを確かめます。

ターミナルで先にやる利点は2つです。**応答がステータスコードと本文で見える**こと。
そして**自分が送ったものが正確に分かる**ことです。ブラウザは便利な分、何を送ったのか
を隠します。先にターミナルで裸の応答を見ておくと、ブラウザで見える挙動のすべてが
「あの応答の結果だった」と読み解けるようになります。

以下は2026-09-28に実行して確認した手順です。compose常設環境(api・db・redis)が
動いている前提です。動いていなければ `make up` してから始めてください(Lab 1の§1)。

## 1. 起動する: npm installとvite dev

frontend/ はbackendとは別の道具の世界です(Node.js・npm。第8章8.1)。初回だけ、
依存を導入します。

```bash
cd frontend
npm install
```

導入の進行が表示され、終わるとプロンプトに戻ります。続いて開発サーバーを起動します。

```bash
npm run dev
```

期待される出力(Network行は環境ごとに異なります):

```
  VITE v6.4.2  ready in 102 ms

  ➜  Local:   http://localhost:5173/
```

このターミナルは**開いたままにしてください**(Ctrl+Cで止まります。停止は最後にします)。
`http://localhost:5173/` が画面の住所です。ここにcurlでアクセスしてみましょう。
別のターミナルを1つ開いて、以降のコマンドはそちらで実行します。

```bash
curl -s http://localhost:5173/ | head -6
```

期待される出力:

```html
<!doctype html>
<html lang="ja">
  <head>
    <script type="module" src="/@vite/client"></script>

    <meta charset="UTF-8" />
```

`/@vite/client` の1行が**開発サーバーで配信している**印です。本番用にビルド
(`npm run build`)した成果物にはこの行は入りません。第8章8.2のproxy設定
(`/v1` → `http://127.0.0.1:8000`)により、画面とAPIが同じオリジンに見えています。

## 2. まずは401: 認証のない世界の応答

認証なしでparseを叩きます。これは「失敗の手本」を最初に見る手順です。

```bash
curl -s -X POST http://localhost:5173/v1/intents/parse \
  -H "Content-Type: application/json" \
  -d '{"text":"テスト"}'
```

期待される出力:

```json
{"error":{"code":"UNAUTHENTICATED","message":"missing bearer token","details":null}}
```

**何を見ているか**。第4章4.3の共通envelopeの実物です。3つの項目(code・message・
details)がそろっていて、`code` が機械的な種類名、`message` が人間向けの説明、
`details` が追加情報(ここでは無し=null)です。画面はこの `code` だけを頼りに
挙動を決めます(第8章8.5)。ステータスコードも確認しておきましょう。

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5173/v1/intents/parse \
  -H "Content-Type: application/json" -d '{"text":"テスト"}'
```

`401` と1行だけ返ります。`-o /dev/null -w "%{http_code}"` は「本文は捨てて、
ステータスコードだけ出す」定番の書き方です。

## 3. トークンを発行して交換する: トークンパネルの裏側

本物のIdPログインはまだ実装されていないので、開発用トークンを発行します
(10 §1の内部CLI。backend/ で実行します)。

```bash
cd /home/misty/Projects/latch/backend
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab4
```

期待される出力(数百文字の1行。冒頭だけを示します):

```
eyJhbGciOiJSUzI1NiIsImtpZCI6InRlc3QtaWRwLTEiLCJ0eXAiOiJKV1QifQ.eyJpc3Mi...
```

`subject` は「この開発トークンが誰になるか」の名前です。読者が自由に決めてよい
(他の学習用データと区別しやすくするため、固有の名前にしましょう)。
トークンは長いので、ファイルへ保存してから使います。

```bash
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab4 > /tmp/idp-token.txt
cat /tmp/idp-token.txt | head -c 40; echo "...(省略)"
```

画面では、この文字列を**開発用トークンパネル**に貼って「接続する」を押します。
その裏で走るのが `POST /v1/auth/token` です(第8章8.4)。curlで再現しましょう。

```bash
ACCESS=$(curl -s -X POST http://localhost:5173/v1/auth/token \
  -H "Content-Type: application/json" \
  -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/idp-token.txt)\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
echo "access_tokenの文字数: ${#ACCESS}"
```

期待される出力:

```
access_tokenの文字数: 396
```

**何を見ているか**。応答JSON全体には `access_token`・`expires_in: 3600`・
`refresh_token`・`user` が入っています(第8章8.4)。ここではaccess_tokenだけを
取り出して変数 `ACCESS` に入れました。**このトークンは1時間(3600秒)で切れます**。
Labの途中で401が出たら、発行からやり直してください(続く節は、うえの発行と交換を
1回にまとめて実行しても構いません)。

ここまでに送ったHTTPは2本です。IdPトークンの発行(内部CLI)と交換
(POST /v1/auth/token)。画面の「トークンパネルへ貼って接続する」1つの操作が、
この2本に対応します。それが8.4で読んだ開発用トークンパネルの実体です。

## 4. 404から学ぶ: 保存の前にユーザー登録

さっそくIntentを保存したくなりますが、その前に1つ、見ておくべき失敗があります。

```bash
curl -s -X POST http://localhost:5173/v1/intents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS" \
  -d '{"raw_text":"Lab 4の練習文。今日20時以降、天文館で軽く飲みたい。","status":"draft"}'
```

期待される出力:

```json
{"error":{"code":"NOT_FOUND","message":"user not found","details":null}}
```

**何を見ているか**。トークン(認証)は有効でも、このユーザーは**まだLATCHに登録
していません**。保存は利用者の行を必要とするので、404で断られます。トークンを
「身分証」とするなら、これは「身分証はあるが、まだ来訪者名簿に名前がない」状態です。
この状態は、第4章で学んだ `POST /v1/users`(初回登録)で解決します。

```bash
curl -s -X POST http://localhost:5173/v1/users \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS" \
  -d '{"display_name":"Lab 4学習者","birth_date":"2000-01-01"}'
```

期待される出力:

```json
{"user":{"id":"0adb2a41-934b-44df-a38a-6a9b597f065f","display_name":"Lab 4学習者"}}
```

`id` の値は環境ごとに違います(UUID=重複しない名前として使う、ランダムに発行される
識別子)。ステータスコードは**201**(作成成功)です。なお画面には「ユーザー登録」の
操作はまだありません(後続の単位で作る予定です)。だからフロント経由で学ぶ場合も、
この手順のように `curl` での登録が先になる、というのがいまの実態です。

## 5. parseを叩く: スタブが返す固定値

準備が整いました。例文でparseを叩きます。

```bash
curl -s -X POST http://localhost:5173/v1/intents/parse \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS" \
  -d '{"text":"今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。予算は5000円くらい。会社関係の人は避けたい。"}' \
  | python3 -m json.tool
```

期待される出力:

```json
{
    "structured_intent": {
        "category": {"primary": "meal", "secondary": null},
        "alcohol_involved": false,
        "time": {"start": "2026-09-27T19:00:00+09:00", "end": null,
                 "flexibility_minutes": null},
        "location": {"name": "東京駅", "radius_m": null, "flexibility": null},
        "budget": {"max": null, "currency": "JPY"},
        "participants": {"min": null, "max": null},
        "soft_constraints": [],
        "negative_constraints": [],
        "ng_unverifiable": []
    },
    "warnings": []
}
```

入れた文と、返ってきた値を見比べてください。**「天文館」も「5000円」も返ってきません**。
場所は「東京駅」、日時は「2026-09-27T19:00」。これは第5章5.2で学んだ **StubLLM**が
どんな文にも返す固定値です(開発環境のapiコンテナへは `llm_mode` を渡さない規約に
なっているため、この画面の経路は `llm_mode="stub"` のまま動きます。実APIを呼ぶ
経路は、Parserの精度を測るG1ゲートだけと独立しています——`howtos/g1-gate.md`)。

ここから大事な観察が1つ得られます。**画面は、parse応答の中身が正しいかを判断しない**。
parseが200を返したら、フロントは応答の形(structured_intent)を受け取って、それをそのまま
条件リストに反映します。「東京駅」と表示されても、それは**スタブがそう言ったから**
表示されているだけです。apiコンテナの設定を本物のLLM(real)へ切り替える日が来ても、
同じ画面が同じ手順で「天文館」と表示するようになるだけです。フロントコードに変更は
ありません。**応答の形が同じなら、中身が変わっても画面はそのまま動く**——これが5.3の
「検証を境界で1本通す」設計が、フロントにまで及んでいる姿です。

## 6. 期限の選択肢を取得する: 既定選択はどこで決まるか

parseのあと、画面の「有効期限」のselectが選べるようになります。その裏で走るのが
`GET /v1/intents/expiry-options` です(第8章8.6)。

```bash
curl -s "http://localhost:5173/v1/intents/expiry-options" \
  -H "Authorization: Bearer $ACCESS" | python3 -m json.tool
```

期待される出力(時刻は実行した日の日付に読み替えてください):

```json
{
    "options": [
        {"label": "今夜 23:30", "expires_at": "2026-09-28T23:30:00+09:00", "selectable": true},
        {"label": "明日 12:00", "expires_at": "2026-09-29T12:00:00+09:00", "selectable": true},
        {"label": "明日 23:30", "expires_at": "2026-09-29T23:30:00+09:00", "selectable": true},
        {"label": "3日後まで", "expires_at": "2026-10-01T12:47:15.214244+09:00", "selectable": true}
    ],
    "default_index": null
}
```

`time_start` を付けると、既定の選択(`default_index`)が計算されて返ります。

```bash
curl -s "http://localhost:5173/v1/intents/expiry-options?time_start=2026-09-28T23:00:00%2B09:00" \
  -H "Authorization: Bearer $ACCESS" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['default_index'])"
```

期待される出力:

```
0
```

**なぜ0か**。time.start=23:00の3時間後は翌日2:00。4つの選択肢のうち、それに最も近い
絶対時刻は「今夜 23:30」(0番目)です。「time.start+3時間に最も近い選択肢」という
仕様(03 §3)が、completion.pyの一元実装で計算され、API経由で届いています
(第8章8.6)。URLの `%2B` は `+` を、URLの中で壊れずに送るための書き方
(URLエンコード)です。

## 7. 保存の3つの道: 201と、2種類の422

保存は `POST /v1/intents` です。ここがLabの山場で、**同じ窓口が3つの結果を返す**
のを観察します。

### 道1: 下書き保存(draft)→ 201

draftは `raw_text` だけで保存できます(第6章6.2の検証の切替)。

```bash
curl -s -o /tmp/draft-resp.json -w "HTTP %{http_code}\n" \
  -X POST http://localhost:5173/v1/intents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS" \
  -d '{"raw_text":"Lab 4の練習文。今日20時以降、天文館で軽く飲みたい。","status":"draft"}'
```

期待される出力: `HTTP 201`

```bash
python3 -c "import json; i=json.load(open('/tmp/draft-resp.json'))['intent']; print(i['id'], i['status'], 'version:', i['version'], 'expires_at:', i['expires_at'])"
```

期待される出力(idの値は環境ごとに違います):

```
da88a27a-bf19-43b9-beed-1aa7990ee33d draft version: 1 expires_at: None
```

`expires_at: None` に注目。draftでは期限を**補完しない**仕様(05 §5)の実測です。
条件が決まっていない下書きに期限を勝手に付けてしまわない、という6.4の話が
ここで裏取りできます。

### 道2: 過去の時刻で預ける(active)→ 422

道3のために、parse応答を使ったactive保存のボディを作ります。いま手元のparse応答の
`time.start` は「2026-09-27T19:00」——**過去**です(スタブの応答は日付が固定の
2026-09-27を指すので、実行日が2026-09-28以降なら常に過去になります)。これをそのまま
送ると、どうなるでしょう。予測を書いてから実行してください(第6章のとおり、検証は
サーバー側が強制します)。

```bash
curl -s -X POST http://localhost:5173/v1/intents/parse \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS" \
  -d '{"text":"テスト"}' > /tmp/parse-resp.json
python3 -c "
import json
parse = json.load(open('/tmp/parse-resp.json'))
body = {'raw_text': 'Lab 4のactive保存テスト。今日20時以降、天文館で軽く飲みたい。',
        'status': 'active',
        'structured_intent': parse['structured_intent']}
json.dump(body, open('/tmp/active-body.json', 'w'), ensure_ascii=False)
print('time.start:', parse['structured_intent']['time']['start'])
"
curl -s -X POST http://localhost:5173/v1/intents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS" \
  -d @/tmp/active-body.json
```

期待される出力:

```json
{"error":{"code":"VALIDATION_ERROR","message":"time.start is in the past","details":null}}
```

ステータスは**422**。ブラウザなら、このcodeとmessageから、フロントが
時間行に「時刻を修正してください(現在〜7日以内)」を表示します
(第8章8.5のerrorPlacement)。ここまでが画面の裏側です。

### 道3: 時刻と場所を直して預ける(active)→ 422がもう1回、そして201

時刻を未来へ直します。`/tmp/active-body.json` の `time.start` を書き換えて送ると、
今度は**別の422**が返ります。

```bash
python3 -c "
import json
body = json.load(open('/tmp/active-body.json'))
body['structured_intent']['time']['start'] = '2026-09-28T20:00:00+09:00'
json.dump(body, open('/tmp/active-body.json', 'w'), ensure_ascii=False)
print('time.start を 2026-09-28T20:00+09:00 へ修正')
"
curl -s -X POST http://localhost:5173/v1/intents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS" \
  -d @/tmp/active-body.json
```

期待される出力:

```json
{"error":{"code":"GEOCODING_FAILED","message":"geocoding failed","details":null}}
```

**なぜこうなるか**。parse応答の `location.name` は「東京駅」です(§5)。東京駅は
鹿児島市周辺の地物データ(geofeatures表)に存在しないので、保存時のジオコーディングが
見つけられず422になります(第6章6.5)。エラーの連鎖は、ここでも**検証の順序**
(形式→必須3→時刻→年齢→ジオコーディング)に従って現れています。

場所を「天文館」へ直して、もう1回送ります。

```bash
python3 -c "
import json
body = json.load(open('/tmp/active-body.json'))
body['structured_intent']['location']['name'] = '天文館'
json.dump(body, open('/tmp/active-body.json', 'w'), ensure_ascii=False)
print('location.name を 天文館 へ修正')
"
curl -s -o /tmp/active-resp.json -w "HTTP %{http_code}\n" \
  -X POST http://localhost:5173/v1/intents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS" \
  -d @/tmp/active-body.json
python3 -c "import json; i=json.load(open('/tmp/active-resp.json'))['intent']; print(i['id'], i['status'], 'expires_at:', i['expires_at'])"
```

期待される出力:

```
HTTP 201
88450bcc-20c5-4285-add1-c639af5f7ebc active expires_at: 2026-09-28T23:30:00+09:00
```

`expires_at` に注目。フロントからは期限を送っていませんが、**サーバーが
「time.start+3時間に最も近い選択肢」で補完**しました(05 §5・completion.py)。
time.start=20:00+3時間=23:00に最も近いのは「今夜 23:30」です。§6で見た
default_index=0の計算と、同じ関数(completion.py)で行われているのが分かります。

この節を通しで振り返ると、同じ `POST /v1/intents` が **201・422(時刻)・
422(場所)・201** を返しました。窓口は1つでも、検証をどこで通過したかで
応答が変わる。第8章8.5のエラー表示マップが、この4つの応答に対応して
作られていることまで確認できれば、backendとfrontendが1本に繋がっています。

**注意**: 保存(作成)には「20件/日・Active 5件」の上限が効きます(第7章)。
Labを何度も回すと上限に当たります。そのときの応答は422 `ACTIVE_INTENT_LIMIT` です。
解消は2つあります。curl で `DELETE /v1/intents/<id>` でIntentを消す(ボタンはまだ
画面にありません)か、1日待ってJST 0時で日次のカウンタをリセットするか。学習用途では
draftを主に使えば、Active上限には当たりません。

## 8. ブラウザで裏取りを確かめる

最後に、このLabでターミナルから送ってきたリクエストを、画面から出してみます。
`http://localhost:5173/` をブラウザで開いてください。

画面操作の期待値は、ws-5のunit試験81件(バックグラウンドの通信部分は偽のfetchで、
描画部分はhappy-domで)と、マージ時の実機結合確認(`docs/plans/M1/ws-5-report.md` の
「UI画面確認手順」)で検証済みの挙動です。このLabで何が起きるかを、
**裏で走るリクエスト**との対応つきで書きます。

1. **トークンパネル**: トークンを持っていない初回表示では「開発用トークンパネル」
   が表示されます(裏: リクエストなし。保管庫にトークンがないので表示される)。
   §3で発行したIdPトークンを貼って「接続する」を押すと、パネルが閉じて入力が
   できるようになります(裏: `POST /v1/auth/token`。§3と同じ1本)
2. **例文の入力**: 例文(§5の一文)を入力し、**入力が止まってから1秒待つ**と
   条件リストが組み替わります(裏: debounce 1秒後の `POST /v1/intents/parse`。§5と
   同じ1本)。表示されるのはスタブの固定値——「東京駅」等——です。§5の実測と
   見比べてください
3. **条件リスト**: 5行(時間・場所・人数・予算・目的)が、parse応答から組み立てられます。
   必須3が1つでも欠けている行は催促表示(赤字)になり、リストの下に
   「時間・場所・目的を指定すると預けられます」の注記が出ます
4. **行編集**: 行の鉛筆アイコンから編集できます。時間行は `datetime-local` の
   入力欄(カレンダー/時刻ピッカー)で、確定するとISO形式へ変換されます
5. **有効期限**: parseが成功したあとに選べるようになり、§6の4選択肢が入ります。
   既定の選択はdefault_indexの計算結果です
6. **下書き保存**: 「下書き保存」で画面の隅に「下書きを保存しました」の通知(トースト=
   数秒だけ現れて消える小さな通知)が出ます(裏: `POST /v1/intents`
   status=draft。§7道1と同じ)
7. **預ける**: 「この Intentを預ける」(または ⌘Enter)で必須3が揃っていれば確認
   モーダルが開き、「Intentを確認する」で保存されます(裏: `POST /v1/intents`
   status=active。§7道3と同じ)。うえの1〜7で手が触れていないボタン・メニュー
   (お知らせ・アカウント・topbarの「Intent 3件」)は、まだ接続されていない
   装飾・プレースホルダです(後続の単位で実装)
8. **エラーの行単位表示**: 場所を「東京駅」に戻して預けると、モーダルが閉じ、
   場所行にエラーが表示されます(§7道2〜3の422が画面の言葉になったもの)

1つだけ注意があります。API全体には **60リクエスト/分**の上限があります(第7章)。
入力を連打したり、画面を頻繁にリロードすると、429「操作が集中しています」が
出ることがあります。1分待てば回復します(この応答も、第8章8.5の分岐で
画面に出る実物です)。

## 9. 振り返り

自分のノートに答えを書いてみましょう。

1. 画面で1文を入力してIntentを預けるまでに、裏で走ったリクエストを順に並べる。
   何本だったか(npm経由の取得等は除く)
2. §4の404 `user not found` は、401とどう違うか。「認証」と「登録」の2つの概念で
   説明する
3. parse応答の「東京駅」が画面に表示されている理由。本物のLLMに切り替わったとき、
   画面の挙動はどうなるか
4. active保存が2種類の422を返した順序と、検証の順序(必須3→時刻→ジオコーディング)
   の対応
5. 下書き保存で `expires_at` がnullだった理由と、active保存で補完された理由。
   どちらの計算もcompletion.pyが担っていることを、第8章8.6と結び付けて説明する

ターミナルで観察したHTTPと、ブラウザで見えた画面の変化が全部対応したはずです。
**画面は、このLabで見たリクエストと応答の見た目にすぎない**——これが、
backend→frontendの順に学んできたこの教科書の到達点です。
