# Lab 9 3画面をブラウザで動かす: 提案からチャットまで

- 種別: チュートリアル(手を動かして必ず成功体験を得る。壊しません)
- 前提知識: 第8章(フロントの構成とトークンパネル)・第17章(回答と成立)・
  第20章(チャットのAPI)・第21章(通報)。Lab 4(トークンパネルと保存までの
  HTTP)と Lab 5/7(評価行とlatches行の直接INSERT)は、同じコマンドを
  使うので思い出せるとスムーズです
- 所要目安: 30〜40分(うち数分は、チャットのポーリング待ちの時間です)
- このLabでできるようになること: この教科書で curl と psql だけで見てきた
  LATCHの世界が、**画面**でどう見えるかを自分の目で確かめられる。具体的には、
  hashルーター(URLで画面が切り替わる仕組み)・ホームの2セクション(候補と
  成立済み)・詳細画面の3つの姿(提案/成立済み/不成立)・visibilityによる
  情報の隠蔽と解放・不成立の二値化(自分の操作履歴/統一文言)・チャットの
  ポーリングでの届き方を、2つのブラウザ窓で2人になりながら歩ける
- 次に読むもの: `concepts/22-deletion.md`(第22章)。このLabで作ったデータを
  消す「退会」の設計を読みます
- 使うもの: ブラウザ2窓(普段使いの窓と、プライベートウィンドウ)。
  ブラウザだけで2人のユーザーを同時に扱うためです

## 0. このLabで何をするか

M3 ws-7で、フロントエンドに3つの画面が生まれました。ホーム(入力画面+候補と
成立済みの2セクション)と、LATCH詳細(1つのURLがstatusによって3つの姿に
変わる画面)です。裏で動くAPIは、第17章・第20章・第21章で curl で叩いた
ものと同一です。

```
GET  /v1/latches          ホームの2セクション(第17章)
GET  /v1/latches/{id}     詳細(1画面3姿の材料・第17章)
POST /v1/latches/{id}/response   回答の3択(第17章)
GET・POST /v1/latches/{id}/messages  チャット(第20章)
POST /v1/reports          通報(第21章)
```

このLabの道のりはこうです。

1. api と画面を用意する(画面は配信用にビルドして `npm run preview` で開く)
2. ブラウザ2窓で2人のユーザーになる(トークンパネルにIdPトークンを貼る)
3. 2人のIntentを、画面の入力フォームから保存する
4. 評価行と提案を psql で用意する(Lab 5/7と同じ手法)
5. ホームに提案が現れ、詳細画面で3択を押し、辞退と統一文言を見る
6. もう1本の提案を両者「参加する」で成立させ、隠れていた情報が解放される
   さまを見る。チャットを2窓で送受信する

curl を叩く場面は「ユーザー登録」と「裏側の確認」だけです。操作の主役は
ブラウザです。壊しません(データは最後に全部消します)。

## 1. api を最新のコードで動かす

Lab 6の冒頭と同じ確認から始めます。コードをgitで更新したあとに `make up`
だけを実行しても、**起動済みのコンテナは古いイメージのまま動き続けます**。
ws-6/ws-7でapiのコードは変わっているので、イメージを作り直します
(すでに新しければ数秒で終わります)。

```bash
cd /home/misty/Projects/latch
docker compose ps
```

`api` が `(healthy)` であれば、削除カスケードのコードが入っているかだけ
確認しましょう(入っていなければビルドからやり直しです)。

```bash
docker compose exec -T api ls /app/src/latch/intents/deletion.py
curl -s http://127.0.0.1:8000/health
```

期待される出力(1行目にファイルパス、2行目にJSON):

```
/app/src/latch/intents/deletion.py
{"status":"ok","server_time":"2026-10-02T07:12:34.567890+00:00"}
```

`ls` が「No such file」になった場合は、Lab 6冒頭の手順でビルドし直します
(`docker compose build api && make up`)。

## 2. 画面をビルドして配信する

第8章では開発サーバー(`npm run dev`・5173番)を使いました。このLabでは、
**配信用の形にビルドした成果物**を配信する `npm run preview` を使います。
本番と同じ配信のされ方を体験するのが目的です。

```bash
cd /home/misty/Projects/latch/frontend
npm install
npm run build
```

期待される出力(最後の2行。ファイル名の英数字部分はビルドごとに変わります):

```
dist/client/assets/index-FNz3EBVH.js    36.26 kB │ gzip:  13.18 kB
✓ built in 271ms
```

`dist/client/` へ配信用のファイル群が出力されました。これを配信します。

```bash
npm run preview
```

期待される出力:

```
  ➜  Local:   http://localhost:4173/
```

この表示のままターミナルが占有されるので、**別のターミナルを開いて**
続きの確認をしてください(占有を解きたければ Ctrl+C です。Lab の途中で
画面を見たくなったら、もう1度 `npm run preview` で起動します)。

2つ確認します。画面が配信されていることと、/v1 の通信がapiへ転送される
こと(第8章8.3の vite proxy と同じ仕組みが preview にも効いています)。

```bash
curl -s -o /dev/null -w 'index: %{http_code}\n' http://localhost:4173/
curl -s -o /dev/null -w 'v1転送: %{http_code}\n' -X POST http://localhost:4173/v1/auth/token \
  -H "Content-Type: application/json" -d '{}'
```

期待される出力:

```
index: 200
v1転送: 422
```

2本目が422(中身が空なので検証にはじかれる)なら、転送は正しく働いています。

## 3. 2人のユーザーを用意する(ブラウザ2窓)

Lab 4と同じ開発用トークンを、2人分発行します。backend ディレクトリで
実行します。

```bash
cd /home/misty/Projects/latch/backend
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab9a > /tmp/lab9-idp-a.txt
uv run python -m latch.auth issue-idp-token --provider google --subject learn-lab9b > /tmp/lab9-idp-b.txt
echo "A: $(head -c 30 /tmp/lab9-idp-a.txt)... / B: $(head -c 30 /tmp/lab9-idp-b.txt)..."
```

期待される出力(冒頭だけ):

```
A: eyJhbGciOiJSUzI1NiIsImtpZCI6InRlc3Q... / B: eyJhbGciOiJSUzI1NiIsImtpZCI6InRlc3Q...
```

ユーザー登録は curl で済ませます(画面にユーザー登録フォームはまだなく、
Lab 4§4と同じ実体です。ここから先の操作は画面で行います)。

```bash
cd /home/misty/Projects/latch
for s in a b; do
  ACCESS=$(curl -s -X POST http://localhost:4173/v1/auth/token \
    -H "Content-Type: application/json" \
    -d "{\"provider\":\"google\",\"idp_token\":\"$(cat /tmp/lab9-idp-$s.txt)\"}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
  echo "$ACCESS" > /tmp/lab9-access-$s.txt
  curl -s -o /dev/null -w "lab9$s users: %{http_code}\n" -X POST http://localhost:4173/v1/users \
    -H "Content-Type: application/json" -H "Authorization: Bearer $ACCESS" \
    -d '{"display_name":"Lab9'$s'","birth_date":"1992-06-15","profile":{"bio":"Lab 9をやっています"}}'
done
```

期待される出力:

```
lab9a users: 201
lab9b users: 201
```

いよいよブラウザです。**2つの窓**を開きます。

- 窓1(普段使いのウィンドウ)= Aさん: `http://localhost:4173/` を開く
- 窓2(プライベート/シークレットウィンドウ)= Bさん: 同じURLを開く

トークンの保管場所がブラウザのStorageなので、窓が別Storageを持てば同じ
画面で2人になれます(第8章8.4のsessionStorage/localStorageの話の実践です)。

初回は「開発用トークンパネル」が出ています(Lab 4と同じ部品)。窓1に
Aさん分のIdPトークンを貼って「接続する」を押します。

```bash
cat /tmp/lab9-idp-a.txt
```

表示された1行をコピーして、窓1のパネルへ貼り付けます。接続が成功すると
パネルが消えて入力画面が使える状態になります。窓2も、`cat /tmp/lab9-idp-b.txt`
の内容で同じことをします。**以後、「Aさん」=窓1、「Bさん」=窓2です**。

接続の裏で走っているのは、先ほど curl で送った `POST /v1/auth/token` と
同じ1本です(第8章8.4)。

## 4. 2人のIntentを、画面から保存する

窓1(Aさん)で、入力画面に次の文を打ちます。

```
今度の土曜、天文館のあたりで焼肉を食べたい
```

入力が止まってしばらくすると(第8章8.5のdebounce)、条件リストが
現れます。Lab 4§5で見た parse の応答が画面になったものです。確認したら
「預ける」を押して保存してください(預け方の選択は Lab 4§7 と同じです)。

保存が成功したら、画面の上のほうにある **「Intent N件」** のボタンを見て
ください。「Intent 1件」になっているはずです。押すと、ActiveなIntentの
一覧(カテゴリと時刻の行)がポップオーバーで開きます。ws-7で、この
カウントはプロトタイプの固定表示から、`GET /v1/intents?status=active` の
実応答による動的表示に変わりました。

窓2(Bさん)でも、同じ文を入れて「預ける」までやってください。2人とも
Intentを預けた状態がスタート地点です。

**裏で何が走ったか**は、Lab 4で全部送ったHTTPと同じです(parse → 保存の
2本。このLabでも最後にcurlで確認する場面があります)。

## 5. 評価行と提案を用意する(psql)

マッチングの6層を本物に動かすと数分かかるので、Lab 5/7と同じく、
Layer 4が書いた「評価行」と、Layer 5が書いた「提案の行」を直接入れます。
リポジトリのルート(`latch/`)で実行します。

まず2人のIntentのidを取ります。

```bash
ACCESS=$(cat /tmp/lab9-access-a.txt)
curl -s "http://localhost:4173/v1/intents?status=active" -H "Authorization: Bearer $ACCESS" \
  | python3 -c "import json,sys; [print(i['id']) for i in json.load(sys.stdin)['items']]"
```

期待される出力(2行。**上=Aさん、下=Bさん**の順で並びます。値は環境ごとに
違います。あとで使うので、それぞれコピーしておきます):

```
0e9f92e8-5b93-41d9-b4be-cd05eb864cfb
871d80f1-5347-42ec-8f92-2c04531bd89a
```

シェル変数に入れておきます(自分の実行結果の値に置き換えてください)。

```bash
INTENT_A=0e9f92e8-5b93-41d9-b4be-cd05eb864cfb
INTENT_B=871d80f1-5347-42ec-8f92-2c04531bd89a
```

評価行は Lab 7§2 と同じSQLです(latch_score=0.85・evaluated)。

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

続いて提案を1本作ります。ここが Lab 7 との違いです。proposalに
**全フィールド版**(time_summaryやarea_nameを含む形・第8章で読んだ
visibilityの「見せる側」)を入れて、条件サマリの見える提案にします。

```bash
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO latches
  (intent_ids, group_candidate_id, proposal, score, status,
   response_deadline, expires_at, created_at)
VALUES
  (ARRAY['$INTENT_A'::uuid, '$INTENT_B'::uuid],
   NULL,
   '{\"time_summary\": \"2026-10-05 19:00\", \"area_name\": \"天文館\",
      \"headcount\": 2, \"category_primary\": \"meal\", \"category_secondary\": \"焼肉\",
      \"budget\": {\"max\": 5000}, \"match_level\": \"medium\"}'::jsonb,
   0.85, 'proposed',
   now() + interval '2 hours',
   now() + interval '120 hours',
   now())
RETURNING id, status;"
```

期待される出力(idは環境ごとに違います。**あとでURL直打ちに使うので、
書き留めてください**):

```
                  id                  | status
--------------------------------------+---------
 55173658-68b0-4a6d-96f8-f996c821d403 | proposed
(1 row)
```

## 6. ホームに提案が現れ、詳細が3択を出す

窓1(Aさん)のブラウザに戻って、**再読み込み**します(F5キーなど)。
入力画面の下に、「LATCH候補」と「成立済みLATCH」の2つのセクションが
並んでいるはずです。候補のセクションに、日時と場所を見出しにした
カードがあり、バッジが「回答まち」「一致度 中」、そして「あと1時間
◯分で締切」と並びます。

何が起きたか。画面は `GET /v1/latches` を1回呼び、応答のitemsを
**statusで仕分け**しました。proposed/partial_acceptは候補セクション、
matched/completedは成立済みセクション、そしてrejected/expired/cancelledは
**どこにも表示しません**(終了済みの提案は一覧から消える——03 §7の規定)。
条件サマリの行(time_summary・area_name・人数・カテゴリ・予算)は、
proposalのフィールドをそのまま並べたものです(第8章のformat系純関数の
親戚・`src/latch/view.js` の conditionSummaryLines)。

カードをクリックしてください。URLが `#/latches/55173658-…` に変わり、
詳細画面に切り替わります。**ページが丸ごと読み込み直されていない**ことに
注目してください。`#/` のついたURL(hash)の変化だけで画面が切り替わるのが、
ws-7で入ったhashルーターです(第8章の画面が1枚だった時代との違いです)。

詳細画面には、条件サマリ・「一致度 中」・「あと1時間◯分で締切」(これは
30秒ごとに自動で減っていきます)・そして3つのボタン——**[参加する]
[今回は見送る] [辞退する]**——が並びます。第17章で curl から叩いた
3択(yes/defer/no)が、ようやくボタンになりました。

**試しに窓2(Bさん)でも**、同じURLを開いてみてください(ホームから
カードをクリックでも、アドレスバーへ `#/latches/(書き留めたid)` を
貼っても構いません。URLで直接開けることも、hashルーターの性質です)。
Bさんにも同じ3択が見えます。まだ誰も答えていないからです。

## 7. 辞退と、統一文言

まず、Aさん(窓1)で**[辞退する]** を押してください。ボタン群が
「辞退しました」の一文に変わり、画面は不成立の姿になります。Aさんにとって
これは「自分の操作の履歴」です(03 §7の二値化の片方)。

次に、窓2(Bさん)の詳細画面を**再読み込み**してください。Bさんには
「**この提案は成立しませんでした**」とだけ表示されます。誰が・なぜ
消したのかは、一切出ません。

同じstatus=rejectedなのに、AさんとBさんで表示が違う。差を作っているのは
`my_response`(自分の回答)だけです。自分がno/deferで終わらせた提案は
自分の操作履歴として、それ以外は統一文言で示す——第17章で読んだ規定が、
`view.js` の closedText という純関数1つで実現されています。

ホームに戻って(Bさんの窓でも、wordmarkをクリックすると `#/` に戻ります)
再読み込みすると、この提案は候補一覧から**消えています**。終了済みは
仕分けで捨てられるのでした(§6)。

## 8. 最小形の提案から成立へ: 隠れていた情報の解放

2本目の提案を作ります。今度はproposalを**最小形**(headcountと
match_levelだけ)にします。visibilityで「相手に見せない」側
(hidden_until_match)の提案がこの形でした(第14章14.4)。

**同じ2人の間に、開いている提案は1本しか置けない**ことに注意してください。
第14章の部分UNIQUE索引(0004)が、二重提案を防いでいます。§7で1本目が
rejectedに閉じたので、いまは置けます。試しに閉じる前にもう1本INSERT
しようとすると、`duplicate key value violates unique constraint
"uq_latches_intent_ids_open"` ではじかれます(実際にこのLabの検証中に
起こったことです。閉じた提案と開いた提案の違いが、索引1つの違いです)。

```bash
docker compose exec -T db psql -U latch -d latch -c "
INSERT INTO latches
  (intent_ids, group_candidate_id, proposal, score, status,
   response_deadline, expires_at, created_at)
VALUES
  (ARRAY['$INTENT_A'::uuid, '$INTENT_B'::uuid],
   NULL,
   '{\"headcount\": 2, \"match_level\": \"high\"}'::jsonb,
   0.91, 'proposed',
   now() + interval '2 hours',
   now() + interval '120 hours',
   now())
RETURNING id, status;"
```

期待される出力:

```
                  id                  | status
--------------------------------------+---------
 4a00c022-479c-43b3-98e1-116fec08fa68 | proposed
(1 row)
```

窓1(Aさん)を再読み込みすると、候補セクションに新しいカードがあります。
見出しは「**条件が合う候補があります**」、バッジは「回答まち」「一致度 高」。
条件サマリがありません——最小形には日時も場所も入っていないからです
(第14章14.4で読んだvisibilityの分岐です)。

カードを開いて、**[参加する]** を押してください。「参加します」の表示に
変わります(1対1の提案なので「あと1人の回答が必要」は出ません)。
窓2(Bさん)でも開いて **[参加する]** を押してください。Bさんの画面では、
押した瞬間に**画面全体が成立済みの姿へ**切り替わります。

- 「集合情報」として日時と場所が表示される(§5で何も入れなかったのに!)
- 参加者の名前(Lab9a・Lab9b)と bio が並ぶ
- 「チャットで挨拶を交わしましょう」の一文
- チャットの入力欄

隠れていた情報が解放されました。実はtime_summaryやparticipantsは、
`GET /v1/latches/{id}` の応答が**status=matchedのときだけ**詰めて返す
情報です(第17章17.6・05 §5)。proposalの最小形は画面の工夫ではなく、
APIが隠している。だからフロントは「time_summaryというキーがあるか」だけを
見て分岐できます(`view.js` の isMinimalProposal。APIの2形と1:1に
対応する巧妙な判定です)。

窓1(Aさん)も、詳細を再読み込みすれば同じ成立済みの姿になります。

## 9. チャットを2窓で送受信する

窓1(Aさん)のチャット入力欄に「19時の天文館の改札で会いましょう!」と
打って送信してみてください。自分の文が右側に並びます。第20章で見た
`POST /v1/latches/{id}/messages`(201)が、送信ボタンの裏で走っています。
送信後に自分の文が並ぶのは、送信の応答をそのまま表示せず、**取得し直して
から描画している**ためです(順序の真実をサーバに預ける設計・第20章20.3)。

では窓2(Bさん)を見てください。**すぐには届いていません**。この画面は
30秒おきに `GET /v1/latches/{id}/messages` を呼ぶポーリング(順次確認)で
新しい文を取りに来ます。30秒待つ(または窓2を再読み込みする)と、Aさんの文が
左側に現れます。自分/相手の左右の振り分けは、メッセージの sender_id と
自分のuser_id の比較で決まります(第20章・`view.js` の messageSide)。

Bさんも返信してみてください(「了解!楽しみにしています。」など)。Aさんの
窓にも、ポーリングで届きます。試した実測では、A→B・B→Aの2往復で、
応答はこの順で並びます(curl で同じ取得を叩くと確かめられます)。

```bash
ACCESS=$(cat /tmp/lab9-access-b.txt)
curl -s "http://localhost:4173/v1/latches/4a00c022-479c-43b3-98e1-116fec08fa68/messages" \
  -H "Authorization: Bearer $ACCESS" \
  | python3 -c "import json,sys; [print(m['body']) for m in json.load(sys.stdin)['items']]"
```

期待される出力(idの部分は自分の環境の値に置き換えてください):

```
19時の天文館の改札で会いましょう!
了解!楽しみにしています。
```

created_atの**昇順**(古い順)で並ぶことも、第20章で読んだとおりです。
なお、このチャットはLATCHが閉じるまでの数時間だけの部屋です。成立前の
提案詳細にチャットはなく、成立すると現れる——**書ける期間はstatusが
matchedの間だけ**という規定(409 CHAT_READONLY)が、画面の構成そのものに
現れています。

## 10. 発展: 通報の導線

成立済み詳細画面の下のほうに「このLATCHを通報する」の導線があります。
押すと対象者を選び(1対1なら相手固定)、理由を4択から選んで送信します。
裏では `POST /v1/reports` が走り、送信後には「通報を受け付けました」の
トーストが出ます(第21章21.6)。

提案詳細(1対1)にも「この提案を通報する」があります。提案段階は参加者が
非開示なので、リクエストには **reportee_id を入れず latch_id だけ**を送り、
サーバが「自分以外の参加者」から相手を解決します(ws-7で追加された経路。
検証した実応答は201 `{"report_id": "..."}` で、DBでは reportee が相手側の
Lab9bに解決されていました)。グループの提案にはこの導線がありません——
誰を通報対象にすべきか、画面が知らないからです。

## 11. 片付け

Lab 5〜7と同じ流儀で、lab9プレフィックスのデータをFK順に消します
(今回はblocksを使っていない代わりに、§10で通報を試した人はreportsも
消します。試していなければ0行のままです)。

```bash
docker compose exec -T db psql -U latch -d latch -c "
DELETE FROM match_candidates WHERE intent_a_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab9%')) OR intent_b_id IN (SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab9%'));
DELETE FROM reports WHERE reporter_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab9%') OR reportee_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab9%');
DELETE FROM messages WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab9%')));
DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab9%')));
DELETE FROM calibration_records WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab9%')));
DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab9%');
DELETE FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab9%'));
DELETE FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'learn-lab9%');
DELETE FROM users WHERE auth_subject LIKE 'learn-lab9%';
SELECT (SELECT count(*) FROM users WHERE auth_subject LIKE 'learn-lab9%') AS users_left;"
```

期待される出力(最後の1行。§10を試した場合、reportsのDELETEが1行に
なります):

```
 users_left
------------
          0
(1 row)
```

ブラウザの両窓も閉じて構いません。`npm run preview` を動かしていた
ターミナルは Ctrl+C で停止します。

## 12. このLabで確かめたこと

- 配信用ビルド(`npm run build`)と preview(4173番)で、開発サーバーと
  同様に /v1 転送が効いて画面が動く
- 1つのindex.htmlのなかで、hash(`#/`・`#/latches/{id}`)だけで画面が
  切り替わる。URLを直接開けることも、通知から着地することを考えると
  大事な性質(第8章の時代からの成長)
- ホームは `GET /v1/latches` のitemsをstatusで仕分けする。終了済みは
  一覧に出ない
- 詳細画面はstatusで3つの姿になる。不成立の表示は my_response だけで
  二値化される(自分の操作履歴/統一文言)
- visibilityの隠蔽はAPIの応答形(最小形/全フィールド版)が担う。フロントは
  キーの有無だけで分岐する。成立すると隠れていた情報が解放される
- チャットは30秒ポーリングと送信後の再取得で届く。左右の振り分けは
  sender_idと自分のidの比較
- 通報は提案段階ではreportee_idを省略し、サーバがlatchの参加者から解決する

**自分のノートに書くこと**を思い出してください。いちばん効く問いはこれです:
「curlで叩いていたAPIと、ボタンの裏で走っているAPIは、何が違いましたか?」
(答えは「何も違わない」。この確信を持てたなら、このLabは成功です)
