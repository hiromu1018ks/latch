# 第19章 届ける: 通知の文面を型で縛り、表示を構造で渡す

- 種別: 解説(通読して理解を積む章)
- 前提知識: 第18章(通知の先行書き込み・attendance_request・sweeper)・
  第14章(proposal通知とnearby_candidate・D-08上限)・第5章(Protocolと
  スタブ・ドライラン)・第17章(条件付きUPDATE・latchesのcursor改頁)・
  第9章(latch.llm.sendの構造化ログ)
- この章を読み終えるとできるようになること:
  - 通知が「事実の行(notifications)」と「届け方(プッシュ・お知らせ)」の
    2層に分けて作られている理由を説明できる
  - プッシュの文面が「引数にtypeしか取れない関数」からしか生まれない設計が、
    決まり文句の徹底を査閲ではなく構造で担保する仕組みを説明できる
  - StubPushSenderのドライラン記録(latch.push.send)を読み、実送信(FCM)に
    置き換える日のための準備が何か挙げられる
  - 送信がトランザクションのコミット直後に置かれ、失敗しても握られる理由を
    「通知の事実は行、プッシュは配信経路の一つ」という分担で説明できる
  - お知らせ一覧のLEFT JOINが、文言をクライアントに任せたまま表示の規制を
    サーバ側のデータ構造で担保するしくみを追える
- 対応コード: `backend/src/latch/notifications/`(新設パッケージ: templates・
  records・sender・store・service・routes・schemas・types)・
  `worker/matching/latch_engine.py` と `worker/sweeper.py`(コミット直後送信)・
  `worker/main.py`(PushSenderの配線)・`main.py`(router登録)・
  `settings.py`(`push_mode`・`push_stub_delay_ms`)
- 設計の根拠: `docs/plans/M3/ws-3-design.md`(特に§2.1の送信位置・§2.4の文面
  写像・§2.5のLEFT JOIN)と `docs/plans/M3/ws-3-report.md`。本文の「NN §X」は
  `docs/NN-*.md` の第X節を指します(03 §4=通知の文面と頻度・05 §5=お知らせAPI・
  08 §2.6=プライバシー規定FR-21・10 §1=ドライラン検証)
- 次に読むもの: `concepts/20-after-matched.md`(第20章)。届けた先で待つ、
  成立のあとの2つの体験(チャットと実施自己申告)を読みます

## 19.1 送るべき事実は、もうDBにある

第14章と第18章で、notificationsテーブルへの**先行書き込み**を読みました。
提案が出ればproposal・近くに候補がいればnearby_candidate(第14章)、
会が終わればattendance_request(第18章)。いずれもpayloadは
`{"latch_id": "<uuid>"}` だけの、文言を一切持たない行でした。送るべき
「事実」は、遷移と同じトランザクションで先にDBへ書かれている。
この章はその続きです。溜まった行を、どう届けるか。

層を分けて描くと、こうなります。

```text
事実の層   notifications行(第14章・第18章の先行書き込み)
              ↓ 読む
届け方の層 ①プッシュ(スマホのロック画面に着く合図)
           ②アプリ内通知(アプリを開いた人へのお知らせ一覧)
```

届け方が2つあるのは、一方が失敗しても他方が効くようにするためです(03 §4・
D-18)。プッシュの許可をユーザーがOSに拒まれても、お知らせ一覧はアプリの中に
あるので必ず届く。プッシュは「着いた合図」、内容の確認と回答はアプリ内——
この章で読む設計は、すべてこの分担を下敷けにしています。

まず読むのは、より外側の危険を抱えた「プッシュ」です。

## 19.2 文面は、typeしか受け取れない関数からしか生まれない

スマホへのプッシュ通知には、この教科書でいちばん深刻なプライバシーの問題が
あります。**ロック画面は、本人以外の目にも触れる**ということです。

- 電車で居眠りしている人の画面に、通知文が浮かぶ
- 食卓に置いたスマホに、家族が目をやる
- 通知が「今夜20時・天文館・3人・焼肉・予算5,000円」という内容だったら?

仕様はここを明確に規定しています(03 §4・08 §2.6)。プッシュの本文は汎用文
「LATCH候補があります。詳細はアプリでご確認ください。」だけ。人数・時間帯・
地域名・カテゴリ・予算といった**条件サマリ**(条件の要約)を、プッシュには
一切含めない。この規定を **FR-21** と呼びます。飲酒のお付き合い(drinking)
のIntentでも文面は同じです。「焼肉」でも「ワイン」でも、プッシュを見ただけで
カテゴリを推察できないようにするためです。

規定がある。では、これをどう実装に落とすか。ここに2通りの作り方があります。

1. **作れるが紛れ込める**: 文言を組み立てる関数を書き、引数に提案データ
   (proposal)を渡す。規定を守るかどうかは、その関数の中身をレビューする人間
   が確かめる
2. **そもそも作れない**: 関数の引数を**通知のtypeだけ**にする。文面に混ぜたい
   データが、関数に届かない。混ぜようがない

LATCHは2を選びました。`notifications/templates.py` の全体がこれです。

```python
PUSH_TITLE = "LATCH"
PUSH_BODY_LATCH = "LATCH候補があります。\n詳細はアプリでご確認ください。"
PUSH_BODY_NOTICE = "LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"


def build_push(notification_type: str) -> tuple[str, str]:
    if notification_type in (NOTIFICATION_PROPOSAL, NOTIFICATION_NEARBY):
        return PUSH_TITLE, PUSH_BODY_LATCH
    if notification_type == NOTIFICATION_ATTENDANCE_REQUEST:
        return PUSH_TITLE, PUSH_BODY_NOTICE
    raise ValueError(f"unknown notification_type: {notification_type!r}")
```

(`backend/src/latch/notifications/templates.py:16-33`)

読み方が3つあります。

1つめ。`build_push`(文面を作る)に渡せるのは、通知の種類を表すtype文字列
だけです。proposal(提案)なのかnearby_candidate(近くの候補)なのか
attendance_request(実施のお知らせ)なのか。時間も場所も人数も、この関数は
**知りようがありません**。「引数に条件サマリを混ぜてしまう」ような間違いは、
先の1案ではコードレビューで見つけるしかありませんでした。2案では
**型の上で存在しない経路**です。規定を破るには、まず関数の形を変えなければ
ならない。形を変える
変更はdiffで目立ちます。FR-21の担保が、査閲から構造へ格上げされた、という
わけです。

2つめ。提案(proposal)と近くの候補(nearby_candidate)が**同じ文面**なのは
偶然ではありません。2つを区別する文面にしてしまうと、「閾値に達しなかった
候補がいる」という事実までOS経由に漏れてしまいます。区別が必要な情報は
アプリ内にだけ置く、の徹底です。

3つめ。attendance_requestだけ、第二の汎用文「LATCHからのお知らせがあります。」
を使います。会が終わったあとの申告案内に「LATCH候補があります」は不適切です
から、文案だけ別にしました。どちらの文面も、条件サマリを一切含まないことは
同じです(設計判断の経緯はws-3設計§2.4と§5-1にあります)。

なお、この3つの定数はunit試験で**全文がそのまま固定**されています
(`tests/unit/notifications/test_templates.py`)。文言の変更は、定数と試験の
両方を変える作業になります。うっかり「ちょっと詳しい文面に変えてみよう」と
いう変更が、静かに紛れ込まない仕掛けです。

## 19.3 送信者は最初からスタブ: 記録だけが送った証拠

文面が決まったら、次はそれを届ける相手です。実機へのプッシュは、Googleの
**FCM**(Firebase Cloud Messaging。Android・iOS・Webへのプッシュを中継する
サービス)という外部サービスを経由します。ところがLATCHのこの段階では、
FCMのアカウントも鍵もまだ用意されていません(人間が用意すべき資材として、
実装から分離して管理されています)。

ここで第5章の作法が再登場します。LLM呼び出しでやったのと同じです。
**Protocol**(「この形の部品なら、どの実装でも差し替えられる」という約束の型)
で送信者の窓口を決め、当面は**スタブ**(外の世界と話さない、試験用の代替部品)
で埋める。

```python
class PushSender(Protocol):
    name: str

    async def send(
        self, *, user_id, notification_type: str, latch_id
    ) -> None: ...
```

(`backend/src/latch/notifications/sender.py:22-27`)

`send`(送る)は、誰に(user_id)・何の通知か(notification_type)・どのLATCH
についてか(latch_id)だけを受け取ります。戻り値はなし。そして**例外も
出しません**(この理由は19.4で)。トークン(スマホ側で受信を識別するための
文字列)の管理は、この型の外側・実装の中に隠れます。将来FCMへつなぐ日には、
このProtocolを実装する `FirebasePushSender` を1つ作って差し替えるだけで、
呼び出し側は1行も変わりません。

スタブの実体は `StubPushSender`(`sender.py:30-84`)です。ネットワーク呼び出し
は一切なし。やることは、19.2の `build_push` で文面を得て、記録を1行、ログへ
出すことだけです。記録の型がこれです。

```python
class PushSendRecord(BaseModel):
    occurred_at: datetime  # Clock.now()(tz-aware UTC)
    user_id: str           # 宛先ユーザー
    notification_type: str # proposal / nearby_candidate / attendance_request
    latch_id: str          # 参照先
    title: str             # テンプレート定数
    body: str              # テンプレート定数(汎用文)
    status: SendStatus     # ok / timeout / error
    error_code: str | None = None  # 例外IDのみ
```

(`backend/src/latch/notifications/records.py:22-32`)

`latch.llm.send`(第9章)と同じ作りの**構造化ログ**——出すフィールドを型で
固定した、機械が読める1行の記録です。ロガー名は `latch.push.send`。
ログをこの名前で検索すれば、送信の事実が全部出てきます(Lab 7で実際に見ます)。

LLMの送信記録と1つだけ違うのは、**本文を含む**ことです。LLM記録が「内容を
含まない」(機微な入力をログに残さない)方針だったのに対し、プッシュ記録は
本文が要ります。なぜか。FR-21の検証方法そのものが「プッシュ本文が汎用文で
あることを**記録で検証**する」(10 §1)だからです。本文は19.2の固定定数しか
通り得ないため、機微が入り込む経路は構造的にありません。「内容を含めない」
規律と「記録で検証する」規律のどちらを取るかは、何が機微かで判断する——
同じプロジェクトの中に、根拠の違う2つの方針が並んでいる例として覚えて
ください。

設定は `settings.py` に2つ増えました。`push_mode="stub"`(実送信は将来の
`"real"`。**未実装の値を指定するとValueErrorで起動に失敗**します。静かに
スタブへ落ちない規律は、`llm_mode` と同じです)と、`push_stub_delay_ms`
(スタブの遅延の注入。試験でtimeout経路を作るときに使います)。

## 19.4 送る瞬間はコミットの直後、失敗しても握る

層をもう一度思い出してください。notifications行=「通知する」という事実。
プッシュ=その事実を届ける経路の1つ。この分担から、送る**瞬間**が決まります。

送り先を集めて送るコードを、提案の経路で見ます。第14章で読んだ `try_promote`
の末尾です(該当箇所だけ抜き出します)。

```python
    async def try_promote(self, latch_id: uuid.UUID) -> None:
        ...
        pending: list[tuple[uuid.UUID, str, uuid.UUID]] = []
        async with self._engine.begin() as conn:
            ...  # (第14章の手順1〜8: 検査・proposed遷移・通知行の書き込み)
                await _insert_notification(
                    conn, p.user_id, NOTIFICATION_PROPOSAL, latch_id, now
                )
                pending.append((p.user_id, NOTIFICATION_PROPOSAL, latch_id))
        # txコミット後のみ送信(§2.1: コミット前に送るとnotifications行が
        # ないままプッシュが飛び得る)。早期return(candidate保留・競合負け)
        # はpending空のままtxを抜けるため送信されない。
        await self._send_pushes(pending)
```

(`backend/src/latch/worker/matching/latch_engine.py:767-838`)

トランザクションの**中**では、notificationsへのINSERTと並んで、送り先を
`pending`(未送信リスト)へ集めるだけ。トランザクションが閉じた(=コミット
された)あと、初めて送信が走ります。この順序を逆にすると、こうなります。
プッシュを送ったあとでトランザクションが失敗したら、**notifications行が
存在しないのに通知だけが届く**。事実の記録と配送が食い違う状態です。
D-08の日次上限(1日6件)はnotifications行を数えていますから、数えられない
送信は上限の仕組みまで壊します。コミット直後、が正解です。

失敗の扱いも、この分担から導けます。送信メソッドは例外を出しません
(19.3のProtocolどおり)。仮に送信が失敗しても、notifications行=通知の事実は
すでにコミット済みで、アプリ内通知というもう一つの経路が必ず届く。プッシュ
の失敗は `status="error"` の記録1行に収めて、処理を止めない。再送も試みません。
第14章の冪等ガード(latch_score IS NULL)により、同じ評価経路は二度と送信段に
入らないので、再送の仕組みだけが中途半端に残る、という事態にもなりません。

未注入もまた、正しい状態です。`LatchEngine` のコンストラクタは
`push: PushSender | None = None` を受け取ります。Noneなら `_send_pushes` は
何もしない(no-op)。第10章10.8の `_kick_embedding` と同じ作法で、この部品を
知らない古い試験構成がすべてそのまま緑で済むようにしています。

配線は `worker/main.py` に1つ。`build_push_sender(self.clock, self.settings)`
で作った1個の送信者を、LatchEngineとExpirySweeperの両方へ注入します
(`worker/main.py:174-207`)。attendance_requestの送信も同じ形で、sweeperの
`_complete_latch` がtx内で送り先を集め、コミット後に送ります
(`worker/sweeper.py:297-355`。第18章18.6の引用に、`targets.append(...)` の
1行が加わったのがそれです)。

## 19.5 お知らせは構造で渡す: LEFT JOINと、文言を任せる理由

もう一方の届け方、アプリ内通知(お知らせ)に入ります。窓口は2つ。

- `GET /v1/notifications` — 自分宛のお知らせ一覧(新しい順)
- `POST /v1/notifications/{id}/read` — 1件を既読にする

プッシュが「合図」なら、こちらが内容の本体です。では、この一覧は文面を
返すのでしょうか。しません。ここにも意図があります(05 §2)。

お知らせの表示文は、将来A/Bテスト(文面を2種類用意して効果を比べる試験)の
対象になる予定です。表示文をサーバが完成形で持っていたら、文面を変えるたびに
サーバのリリースが要る。そこでLATCHは、**文言の組み立てをクライアント
(フロントエンド)に任せて、サーバは構造(データ)だけを渡す**ことにしました。
通知の種類(type)と、参照先のLATCHの内容。この2つがあれば、画面側は文面を
いくらでも組み立てられます。

参照先の中身を渡すところに、この章でいちばん変わったSQLが登場します。
`notifications/store.py` の一覧SQLです。

```sql
SELECT n.id, n.type, n.payload->>'latch_id', n.read_at, n.created_at,
       l.id, l.status, l.response_deadline, l.expires_at, l.completed_at,
       l.proposal
FROM notifications n
LEFT JOIN latches l
  ON n.payload->>'latch_id'
     ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
 AND l.id = CAST(n.payload->>'latch_id' AS uuid)
WHERE n.user_id = CAST(:me AS uuid)
  AND (CAST(:ct AS timestamptz) IS NULL
       OR n.created_at < CAST(:ct AS timestamptz)
       OR (n.created_at = CAST(:ct AS timestamptz)
           AND n.id < CAST(:lid AS uuid)))
ORDER BY n.created_at DESC, n.id DESC
LIMIT :limit
```

(`backend/src/latch/notifications/store.py:28-44`)

**JOIN**(ジョイン)とは、2つの表をつないで1つの結果にすることです。
「notificationsの各行に、latchesの対応する行を横に並べる」。
**LEFT JOIN**は、つなぐ相手が見つからなかった行も、右側をNULL(空っぽ)に
して残す書き方です。ここでは、notifications.payloadのlatch_idを鍵にlatchesを
横につけています。お知らせは履歴を含むので、もう閉じたexpiredのLATCHへの
通知も一覧に並ぶ必要があります。GET /v1/latches(第17章)が開いている行しか
返さないのに対し、お知らせは過去の行も全部見せる——その差を、この1本のSQLが
埋めています。

JOINのON句に、正規表現でのuuid形式チェックが入っているのも読みどころです。
payloadはjsonb(自由な形のJSON)なので、普通にCASTしようとすると、中身が
uuidの形をしていなかった場合に問い合わせ全体が例外で死にます。正規表現で
「uuidの形をしている行」だけをCASTする。不正な行はlatch=nullで落ちる
(防御)。利便性と耐性の両取りを、1つの条件式で書いています。

応答はこういう形になります(Lab 7で実際に見ます)。

```json
{
  "items": [
    {
      "id": "dc51b182-…",
      "type": "attendance_request",
      "latch_id": "adf10b0b-…",
      "read_at": null,
      "created_at": "2026-10-01T06:48:10.646198Z",
      "latch": {
        "id": "adf10b0b-…",
        "status": "completed",
        "response_deadline": "…",
        "expires_at": "…",
        "completed_at": "2026-10-01T06:44:24.377022Z",
        "proposal": {"headcount": 2, "match_level": "medium"}
      }
    }
  ],
  "next_cursor": null
}
```

ここで、19.2の文面の話とつながります。文言はクライアントに任せた——では、
**プライバシーはどうなる**のでしょう。hidden_until_match(成立するまで条件を
隠す設定)のLATCHの詳細が、お知らせ経由で丸見えになったら意味がありません。

答えは、表示の規制もサーバ側の**構造**で担保されている、です。お知らせが
渡すlatch要素は、latches.proposal(第14章14.4)をそのまま埋めたものです。
hidden_until_matchの行のproposalは `headcount` と `match_level` だけ
(ws-6実装)。nearby_candidateの行も `{"headcount", "match_level": "low"}`
の最小構成。**最初から入っていない情報は、JOINしても出てこない**。文言の
自由はクライアントに、出してよい情報の境界はサーバが持つ。FR-21をプッシュ
では関数の型で、お知らせではデータの中身で、二重に守っている構図です。

一覧が1頁(既定20件)を超えたら、続きを取るしくみが要ります。
**cursor**(カーソル=次に読み始める位置を表す切符)です。しくみはこうです。
頁の最後の行の「どこまで読んだか」を `(created_at, id)` の2つの値で表し、
それを **base64url**(URLに安全に載せられる英数字だけの文字列への書き換え)
に詰めたものを、応答の末尾のnext_cursorとして返す。クライアントは次の要求に
`?cursor=…&limit=20`(limitは1〜100)として付き返します。「63件目から20件」
のような**番号**で数えないのは、頁の間に行が増えると番号がずれるから——
位置そのものを渡す方式で、**キーセット方式**と呼びます(latchesの一覧も同じ
方式です)。鍵が2つの組なのは、同一トランザクションで書かれた複数人分の通知行は
created_atが完全に同一になるから(第18章Lab 6で見ました)。1つめの鍵が同じ
行は、2つめの鍵(id)で順序まで確定します
(`notifications/service.py:59-77` のencode/decode。形式の壊れたcursorは
422 VALIDATION_ERROR)。

## 19.6 既読は、冪等な204

既読APIは短い話です。手順は3行で書けます(`notifications/service.py:146-174`)。

1. `WHERE id = :id AND user_id = :me` で対象行をSELECTする
2. 行がなければ **404**。**他人の通知idも、存在しないidも、同じ404**です
   (どちらで区別する情報も返さない)
3. `read_at` がNULL(未読)なら `UPDATE ... SET read_at = :now`。すでに
   既読なら何もせず、どちらにせよ **204 No Content**

2回目以降も204が返る=**冪等**(何度やっても同じ結果)です。POST /v1/sessions
(第4章の系列)と同じ、POSTなのに新しく作るもののない窓口です。未読かどうかは
一覧の各itemの `read_at` がnullかどうかで分かるので、
未読件数を数える専用のAPIは作られていません(05に規定がなく、一覧で判定
できるため。作らない判断の記録はws-3設計§2.8にあります)。

## 19.7 この章の再統合

- 通知は2層。**事実(notifications行)は先に、確実に書く**。届け方
  (プッシュ+お知らせ)はその後から読む。片方の失敗がもう片方を壊さない
- プッシュの文面は、**引数にtypeしか取れない関数**からしか生まれない。
  FR-21(条件サマリをプッシュに含めない)の担保が、コードレビューの目から
  関数の型へ移った。3つの定数はunit試験で全文固定されている
- 送信者はProtocolで抽象し、現在は**ネットワークを呼ばないスタブ**。
  送信1件ごとに `latch.push.send` へ構造化ログを残す。本文を記録に含むのは
  「汎用文であることを記録で検証する」ため(LLM記録との方針差は、何が機微か
  による)
- 送信は**トランザクションのコミット直後**。送り先はtx内で集めるだけ。
  送信の失敗は例外を出さず記録に収め、再送しない。未注入はno-op
- お知らせ一覧はpayloadのlatch_idでlatchesを**LEFT JOIN**して構造だけ渡し、
  文言はクライアントが組み立てる。hidden_until_matchの行はproposalが最初から
  最小構成なので、JOINしても条件サマリは**構造的に出ない**
- 既読は冪等な204。他人のidは存在しないidと同じ404

第17章・第18章と、latchesの行は提案・返事・成立・期限切れ・完了と進んできま
した。届ける側の仕組みも、この章で出揃いました。残るは、届けた先で待つ体験
です。成立した2人が詳細を決めるチャットと、会が終わったあとの実施の申告。
それが第20章で、LATCHのこの段階で実装済みの最後のAPI群になります。

## 19.8 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| 条件サマリ | 人数・時間帯・地域名・カテゴリ・予算など、Intentの条件の要約 |
| FR-21 | プッシュ本文を汎用文のみとし条件サマリを含めない、プライバシーの規定(03 §4・08 §2.6) |
| 汎用文 | 「LATCH候補があります。詳細はアプリでご確認ください。」等、内容を特定しない固定の文面 |
| アプリ内通知 / お知らせ | notificationsの行をアプリの中の一覧として見せる届け方。プッシュの恒久フォールバック |
| FCM | Firebase Cloud Messaging。Android・iOS・Webへのプッシュを中継するGoogleのサービス |
| ドライラン | 外へ送らない代わりに「送ったならこうなる」を記録だけ残す実装方法 |
| StubPushSender | PushSender Protocolのスタブ実装。latch.push.sendへ記録を出すだけ |
| PushSendRecord | 送信1件の構造化ログの型。本文を含む(検証のため) |
| 構造化ログ | 出力フィールドを型で固定した、機械が読める1行のログ(latch.llm.sendと同型) |
| LEFT JOIN | 2つの表をつなぎ、つなげられなかった左側の行もNULLつきで残すSQLの書き方 |
| キーセット(2キー) | (created_at, id)の組で順序を確定する改頁方式。同一時刻行のタイブレークに必要 |
| no-op | 何もしないこと。PushSender未注入時の送信呼び出しは何も起きない |

## 19.9 確認問題

1. `build_push` の引数がtypeだけであることが「FR-21の構造的担保」と言える
   理由を、レビューで担保する場合と比べて説明してください
2. proposalとnearby_candidateのプッシュ文面が同じである理由を、「閾値未満の
   候補であること」が漏れる経路を想定して説明してください
3. 送信を「txコミット直後」に置く理由を、送信→コミット失敗の順で起きること
   (notifications行とD-08上限の関係)で説明してください
4. LLMの送信記録は本文を含まず、プッシュの記録は本文を含む。この方針の違いを
   「何が機微か」「何を検証するのか」の2点から説明してください
5. お知らせ一覧がlatchesをLEFT JOINする理由を、「お知らせは履歴を含む」ことと
   GET /v1/latchesの対象範囲との違いで説明してください
6. hidden_until_matchのLATCHへの通知が、お知らせ一覧でも条件サマリを出さない
   理由を、proposalの中身とJOINの関係で説明してください
7. 既読APIが2回目も204を返す(冪等である)価値を、クライアント側の実装
   (通知を開く画面を2度開いた場合など)を想定して説明してください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった
部分が、読み返すべき節です)
