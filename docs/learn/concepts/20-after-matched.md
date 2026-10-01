# 第20章 成立のあと: チャットと、実際に会ったかの記録

- 種別: 解説(通読して理解を積む章)
- 前提知識: 第17章(FOR UPDATE・条件付きUPDATE・影響行数0・Calibration)・
  第18章(matched→completed・completed_at・attendance_request通知)・
  第19章(お知らせ一覧)・第6章(バリデーションと422)・
  第4章(3層の経路: ルータ→サービス→SQL)
- この章を読み終えるとできるようになること:
  - チャットの書込可否が「status='matched' だけ」というたった1つの条件に集約
    されている理由を、仕様の3か所を突き合わせて説明できる
  - 送信APIが行ロック(FOR UPDATE)を取ってから検査してINSERTする順序が、
    sweeperのcompleted化との競合をどう裁くか追える
  - ブロックの双方向判定がEXISTS 1本で書ける理由と、閲覧が残る設計を説明できる
  - 実施自己申告の回答が「LATCH単位・先着1名」で確定するしくみを、条件付き
    UPDATEとactual_attended/cancelled_afterの排他で説明できる
  - 申告の回答が第17章のCalibrationの「実際」を埋める回収口であると説明できる
  - 0006の2本の索引(複合索引と部分UNIQUE)がそれぞれ何を守るか言える
- 対応コード: `backend/src/latch/latches/` への追記(routes・service・store・
  schemas・errorsの5ファイル)・`backend/alembic/versions/0006_chat_indexes.py`。
  notifications・workerは無変更(申告通知は第18章・媒体は第19章の実装のまま)
- 設計の根拠: `docs/plans/M3/ws-4-design.md`(特に§2.1のmatched単一条件・
  §2.2のblocks先行実装・§2.3の先着1名・§2.5の索引)と
  `docs/plans/M3/ws-4-report.md`。本文の「NN §X」は `docs/NN-*.md` の第X節を
  指します(05 §2・§5=APIの規定・03 §6=完了後の閲覧・08 §2.5・D-23=ブロック・
  09 §2.2・D-09=実施自己申告)
- 次に読むもの: `labs/lab7-after-matched.md`(Lab 7)。この章のAPIを、成立から
  申告まで一筆書きで自分の手で叩きます

## 20.1 成立のあと、システムは何を残すか

第17章の終わりで、成立(matched)したLATCHは初めて相手の情報を解放すると
読みました。表示名・プロフィール・集合情報。では、解放されたあと、2人は何で
詳細を決めるのでしょう。チャットです。成立LATCHごとに閉じた、参加者だけの
チャットルーム(03 §6)。

そして会が終わったら? 第18章でcompletedへ進み、attendance_requestの通知が
届く(第19章)ところまで読みました。通知には「実際に会いましたか?」の1問が
載っています。この回答を集める理由は第17章17.7にありました。Calibrationは
「予測」と「実際」を同じ行に並べて保存する仕組みでしたが、「実際」のうち
**会が本当に開かれたか**は、当時まだ埋める経路がなかった。この章でついに
埋めます。予測の精度検証(07 §6)の、最後のピースです。

つまりこの章は、2つのAPIの話です。

| API | すること | いつ |
|---|---|---|
| POST / GET `/v1/latches/{latch_id}/messages` | チャットの送信・取得 | matchedの間だけ送信可。閲覧はその後も |
| POST `/v1/latches/{latch_id}/attendance` | 「実際に会いましたか?」へ回答 | completedから3日以内・初回のみ |

ルータ・サービス・ストアの3層は、第17章の回答API(response)と同じものを
そのまま使い回します。新しいのは中身の判断だけです。順に読みます。

## 20.2 チャットが開く条件は、たった1つ

チャットは、いつ書けて、いつ書けなくなるのか。仕様書を読むと、答えが
3か所に分かれて書いてあります。

- 「matched以降のみ書き込み可」(05 §2)
- 「completed後も閲覧は可能とするが、チャットの新規送信は閉じる」(03 §6)
- 「matched済みLATCHの参加Intent削除時も、チャットは読み取り専用化
  (閲覧は可能)」(08 §2.5・D-23)

突き合わせてみましょう。書けるのはmatched。completedは閉じる。cancelledも
閉じる(読み取り専用)。proposed(まだ成立していない)はそもそも開いていない。
仕様のこの3つの文を1枚に重ねると、**書ける状態はstatus='matched'の間だけ**で、
それ以外の全状態は「閲覧はできるが、送信は閉じている」という同じ扱いに
整理できます。

ここで設計の選択が1つありました。閉じた理由を、エラーコードで細かく区別
するか? たとえば「完了したからCLOSED」「解散したからCANCELLED」「ブロック
だからREADONLY」……LATCHは区別しない方を選びました。**どの状態でも同じ1つの
コード、409 CHAT_READONLY** です(08 D-23の「読み取り専用」を、完了後・解散後
にも使う拡大)。理由はシンプルで、クライアントのすることはどの理由でも同じ
——入力欄を閉じて「このチャットは利用できません」と出す——だからです。
表示を変えたい理由が生まれたら、そのとき初めてコードを分ければよく、
先に分けておくと使われずに腐ります(ws-4設計§2.1の比較表がこの判断の
記録です。05のエラー表を広げる解釈として、次回の仕様改版候補にも挙がって
います)。

エラーの型は、第17章のLatchesErrorの家族に3つ増えました。

```python
class ChatReadonlyError(LatchesError):
    """チャット読取専用状態への送信(05 §2・08 D-23・design §2.1案A)。

    matched以外の全状態(completed/cancelled後の送信关闭を含む)と
    blocks適用中の両方に使う単一コード(03 §6・08 §2.5の統一・承認事項②)。
    """

    http_status = 409
    code = "CHAT_READONLY"
```

(`backend/src/latch/latches/errors.py:67-75`。このあと同じ形で
`AttendanceAlreadySubmittedError` と `AttendanceWindowClosedError` が続きます)

## 20.3 送信は、行ロックを取ってから

送信APIの本体は、第17章のrespondが積み上げた手順を、ほとんどそのまま
なぞります。読んでみると、見覚えのある並びのはずです。

```python
async def send_message(self, *, auth_provider, auth_subject, latch_id, body):
    try:
        async with self._engine.begin() as conn:
            user_id = await store.fetch_user_id(conn, auth_provider, auth_subject)
            if user_id is None:
                raise LatchNotFoundError("user not found")
            row = await store.select_latch_for_update(conn, latch_id)
            if row is None:
                raise LatchNotFoundError("latch not found")
            if (
                await store.select_participant_intent(conn, row.intent_ids, user_id)
                is None
            ):
                raise ForbiddenError("not a participant")
            now = self._clock.now()  # FOR UPDATE取得後に採取
            if row.status != "matched":
                raise ChatReadonlyError("chat readonly")
            user_ids = await store.fetch_participant_user_ids(conn, row.intent_ids)
            others = [u for u in user_ids if u != user_id]
            if self._block_cache is not None:
                blocked = await self._block_cache.is_blocked_between(
                    user_id, others
                )
            else:
                blocked = await store.select_block_between(conn, user_id, others)
            if blocked:
                raise ChatReadonlyError("chat readonly")
            return await store.insert_message(
                conn, latch_id=row.id, sender_id=user_id, body=body, now=now
            )
    except LatchesError:
        raise
    except Exception as exc:
        raise _wrap_unexpected(exc) from exc
```

(`backend/src/latch/latches/service.py:235-280`。docstringは省略。
`if self._block_cache is not None:` の分岐は第21章のマージで加わりました)

手順を番号で言えば、次の6段階です。①本人の解決(未登録は404)。②**FOR
UPDATE**でlatches行を行ロックして読む(不在は404)。③参加者かどうか
(違えば403)。④matchedかどうか(違えば409 CHAT_READONLY)。⑤ブロックされて
いないか(引っかかれば409)。⑥INSERT。①〜③は第17章17.3と同じ3段、⑤は次の
節、⑥はただのINSERT。新しい議論は②と④の組み合わせにあります。

なぜFOR UPDATE(行が他の処理と重ならないよう、行に鍵をかけるSQL句。第17章)
が必要なのでしょう。messagesテーブルには、実は状態を書く列がありません。
「このメッセージはmatchedの間に書かれた」と保証する列がないのです。だと
すると、検査(④)とINSERT(⑥)の**隙間**に、行がmatchedから別の状態へ動く
余地がないといけません。それを埋めるのが行ロックです。送信APIがlatches行の
鍵を取っている間、同じ行の鍵が必要な処理は待つか諦めるしかありません。
sweeperのcompleted化(FOR UPDATE SKIP LOCKED・第18章)も回答APIも、その
相手です。鍵を
取ってから確かめたstatusは、INSERTを終えて鍵を返すまで動きません。

`now = self._clock.now()` を鍵の**後**に採取しているのも、第17章と同じ規律
です。時刻を採ってから鍵を待つと、待っている間に時刻が古くなる。メッセージの
created_atは、書いた瞬間の時刻であるべきです。

本文(body)の制約は、仕様に規定がなかったため実装で定めました(参考として
仕様改版候補に記録済み)。**前後の空白を除いて1〜1000字。空白だけなら422**。

```python
    @field_validator("body")
    @classmethod
    def _body_length(cls, v: str) -> str:
        if not 1 <= len(v.strip()) <= 1000:
            raise ValueError(
                "body must be 1..1000 chars (excluding surrounding whitespace)"
            )
        return v
```

(`backend/src/latch/latches/schemas.py:90-95`)

Intentのraw_textが300字(第5章)よりゆるいのは、会話の一文としての余白です。
422 VALIDATION_ERRORに変換される流れは、第6章のバリデーションと同じです。

取得(GET)は送信より緩やかです。参加者なら**statusを問わず200**。matchedに
なる前はメッセージ行がそもそも存在しないので空のリストが返り、completed・
cancelledのあとも閲覧は続きます(03 §6・08 §2.5)。読み取りは行を変えない
ので、FOR UPDATEも不要、ただのSELECTです。改頁は第19章のお知らせ一覧と同じ
2キーのキーセット `(created_at, id)`——ただしこちらは会話の自然順なので
**昇順**(古いものが先)です(`service.py:494-512` のencode/decode)。

## 20.4 ブロックされたら、書く道だけが閉じる

⑤のブロック判定です。blocksテーブル(第4章でusersと一緒に作られた、
「この人とは関わりたくない」という記録)は**単方向**です。AがBをブロック
しても、逆向きの記録(BがAをブロックした行)は生じません。だから判定は
**双方向**、どちら向きのブロック行が1本でもあれば送信を閉じます
(05 §2・08 D-23)。

```python
_SELECT_BLOCK_BETWEEN = text("""
    SELECT EXISTS (
        SELECT 1 FROM blocks
        WHERE (blocker_id = CAST(:me AS uuid)
               AND blocked_id = ANY(CAST(:others AS uuid[])))
           OR (blocker_id = ANY(CAST(:others AS uuid[]))
               AND blocked_id = CAST(:me AS uuid))
    )
""")
```

(`backend/src/latch/latches/store.py:288-297`)

「私→誰か」と「誰か→私」のOR 2本。相手は `:others`(自分以外の参加者全員。
グループLATCHなら全ペアが対象)で、`ANY(CAST(:others AS uuid[]))` は
「配列のうちの誰か」(第18章18.6で出たPostgreSQLの配列の書き方です)。
**EXISTS**は「1行でも該当するか」だけを返す存在検査で、該当行を数えて
持ち帰る無駄を省きます(第17章の競合クローズ判定と同じ目的の書き方)。

「ブロックされたらチャットを読み取り専用にする」(D-23)という規定は、
書く道だけを閉じます。読む道は残る(20.3のGETはstatusしか見ない)。過去の
やり取りを証拠として見られなくなるのは別の害になる、という判断です。
つまり、matchedであってもblocks行があれば送信は409 CHAT_READONLY——
20.2の「matched単一条件」に、blocks判定だけが並列に加わる形です。

ブロックを登録するAPI(POST /v1/users/{id}/block)と、登録と同時に進行中の
提案を閉じる処理は、第21章で読みます。この章のLab(Lab 7 §4)では、その
登録APIをそのまま叩いてblocks行の効果を確かめます。判定は、第21章のマージで
Redisのキャッシュ(BlockCache)を経由するよう差し替わりました。20.3の
`if self._block_cache is not None:` の分岐がそれで、else側の
`store.select_block_between`(DB直読み)はキャッシュが使えないときの退路と
して今も残っています。キャッシュの中身と「消して読み直す」更新のしくみは、
第21章21.4で扱います。

## 20.5 申告は、最初の1人がLATCHの記録を確定する

後半です。POST /v1/latches/{latch_id}/attendance。リクエストは
`{"attended": true}` の1つ、応答は `{"latch_id": "…", "actual_attended": true}`
(05 §5)。シンプルな窓口ですが、「誰の回答か」という1つの論点を抱えています。

参加者**全員**が申告通知を受け取ります(第18章)。1対1なら2人、グループなら
3〜4人。ところが、回答をしまう場所——calibration_records——が持っているのは
**LATCH単位の** `actual_attended` / `cancelled_after` の2つのbooleanだけ
(05 §2)。参加者ごとの回答を保持する列は、どこにもありません。仕様はさらに
「回答は初回のみ受理し訂正不可」(05 §5)と書きます。

すべてをつなぐと、答えは1つに決まります。**最初に回答した参加者の1タップが
LATCHの記録を確定し、それ以降の回答は409 ATTENDANCE_ALREADY_SUBMITTED**。
「実際に会いましたか?」はLATCH単位の事実確認なので、最初の1人で確定しても
集計の矛盾は生じません(09 §2.2の実行率は「回答済み成立LATCH数」を分母に
数えます)。グループで「1人しか回答できない」体験面の影響は残ります。
将来参加者ごとの集約を導入するならテーブル追加を伴う仕様改版が必要、という
但し書きを設計メモが残しています(ws-4設計§5-1)。

本体のコードは、第17章以来の条件付きUPDATEが主役です。

```python
_UPDATE_ATTENDANCE = text("""
    UPDATE calibration_records
    SET actual_attended = :attended,
        cancelled_after = NOT :attended,
        updated_at = CAST(:now AS timestamptz)
    WHERE latch_id = CAST(:latch_id AS uuid)
      AND actual_attended IS NULL
    RETURNING id
""")
```

(`backend/src/latch/latches/store.py:306-315`)

`WHERE ... AND actual_attended IS NULL` が肝です。まだ誰も答えていない
(NULL)行だけが更新される。2人の参加者がほぼ同時に答えたら? UPDATEは
片方だけが成功し、もう片方は**影響0行**を返します。影響0行を見たserviceは
409 ATTENDANCE_ALREADY_SUBMITTED を出す(`service.py:354-357`)。鍵の取り合い
をDB自身に裁かせる作法は、第17章・第18章で見たとおりです。FOR UPDATEが
要らない理由も前2章から変わりません。completedは終端状態(遷移表の上で次がない。
第18章)なので、latches行は読むだけで守るべきものがありません。

`actual_attended` と `cancelled_after` が**同じUPDATEで同時に**値が入る点も
見てください。「会えた=true」なら「キャンセル後=false」、「会えない=false」
なら「キャンセル後=true」。入力がboolean 1つなので、両方trueという矛盾した
組み合わせは**機械的に発生しません**。「actual_attended=trueと
cancelled_after=trueは排他」(09 §2.2)という要件を、if文の徹底ではなく
1つの式の形で満たしています。第19章19.2の「引数がtypeだけ」と同じ発想で、
構造による担保です。

応答に `cancelled_after` を出さないのも意図的です。クライアントが
`attended: false` で答えたなら、その情報はもう持っています。書き換え不能な
導出値を繰り返し返す必要はありません。

参加者でない人がこのAPIを叩いたら? messagesの403 FORBIDDEN(20.3)と扱いが
**違って**、404 NOT_FOUND です。申告通知は参加者だけに届くのにAPIの存在は
誰にも見える。だから「このLATCHにあなたは関与していませんか」まで開示しない
——関与の有無を伝えない404で応える、という判断です(05 §5の明文。
第17章の「他人のIntent詳細は404」と同じ作法)。

## 20.6 3日と、答えない自由

回答期限はcompletedになってから**3日**(D-09)。期限の検査はたった1関数です。

```python
def is_attendance_window_open(completed_at: datetime | None, now: datetime) -> bool:
    if completed_at is None:
        return False
    return now <= completed_at + timedelta(days=3)
```

(`backend/src/latch/latches/service.py:515-523`)

`now <= completed_at + 3日`。「3日**以内**」の「以内」を**閉区間**(ちょうど
3日の瞬間まで含む)として読んだ、という記録が設計メモにあります(§2.3手順5)。
境界を含めるかどうかは一字の解釈ですが、試験でちょうど3日を再現するときに
結果が決定的になるので、この1行のためだけに設計メモが1節ある、と思って
ください。期限を過ぎていたら409 ATTENDANCE_WINDOW_CLOSED。

そして、**答えないという選択には実装がいりません**。スキップは「APIを叩か
ないこと」で表され(05 §5)、3日窓の超過がそのまま無回答になります。無回答は
集計から除かれる「欠測」(09 §2.2)として扱われ、良くも悪くも数えません。
期限切れを検知して催促する仕組みは、この章の外(将来の監視・集計の実装)です。

申告APIの応答は、ここまでで4種に出そろいました。

| 状況 | 応答 |
|---|---|
| completed・3日以内・未回答 | 200(JSONでactual_attended) |
| 2人目以降・並行で先着あり | 409 ATTENDANCE_ALREADY_SUBMITTED |
| 3日経過 | 409 ATTENDANCE_WINDOW_CLOSED |
| completed以外への申告 | 409 LATCH_CLOSED |

(このほか参加者以外は404、calibration_records行が丸ごとない場合は503+
構造化ログ1行。後者は第17章17.7で見た「材料特定失敗」と同じ稀ケースです)

## 20.7 テーブルは増やさず、目次だけ足す: 0006の索引2本

この単位のデータベース変更は、テーブルも列も1つも増やさず、**索引**(Index。
検索を速くするための目次)2本だけです(`alembic/versions/0006_chat_indexes.py`)。

```python
def upgrade() -> None:
    op.execute("CREATE INDEX idx_messages_latch ON messages (latch_id, created_at)")
    op.execute(
        "CREATE UNIQUE INDEX ux_calibration_latch"
        " ON calibration_records (latch_id)"
        " WHERE latch_id IS NOT NULL"
    )
```

(`backend/alembic/versions/0006_chat_indexes.py:26-33`)

1本め `idx_messages_latch` は**複合索引**(複数列を組み合わせた目次)。
(latch_id, created_at)の組で並べておけば、20.3のGET改頁「このLATCHの
メッセージを時刻順にN件」が目次をたどるだけで済みます。キーセット改頁の
WHERE句と索引の並びを一致させる、というのが、索引設計の定石の1つです。

2本め `ux_calibration_latch` は**部分UNIQUE索引**(テーブルの一部の行だけに
一意制約をかける目次)。latch_idがNULLでない行に限って「latch_idの重複禁止」
を保証します。20.5のUPDATEは `WHERE latch_id = :latch_id AND actual_attended
IS NULL` で行を特定しましたが、この索引があることで「あるlatch_idを指定
すれば該当行は高々1行」がDB自身の保証になります。見覚えがありませんか。
第14章のlatches(0004)・第15章のgroup_candidates(0005)と同じ形の3本目です。
`WHERE latch_id IS NOT NULL` と一部だけにするのは、将来の匿名化(D-13・
30日経過で個人情報を外す運用)でlatch_idをNULLへ書き換えると、自動的に
一意制約の対象から外れるようにするためです。

マイグレーションのheadは0006になりました。第1章のコマンド `make migrate`
を叩けば、0001から順に0006までが適用済みであることが確認できます。

## 20.8 この章の再統合

- チャットの書込可否は「status='matched'」の**たった1つの条件**。completed・
  cancelled・ブロック適用中は、全部同じ409 CHAT_READONLYで「閉じている」。
  理由の区別はクライアントの役に立たないから
- 送信は**FOR UPDATE→検査→INSERT**の順。messagesに行ロックに相当する列が
  ないため、latches行の鍵で検査とINSERTの隙間を埋める。sweeperのcompleted化
  との競合は鍵の取り合いで自然に裁かれる。閲覧はいつでも、ただのSELECT
- ブロックは**双方向**をEXISTS 1本で検査。書く道だけが閉じ、読む道は残る
- 申告の回答単位は**LATCH単位・先着1名**。条件付きUPDATE(`actual_attended
  IS NULL`の再検査)が排他の本体で、影響0行は409。actual_attendedと
  cancelled_afterは同時に値が入り、排他要件を構造で満たす
- 申告は第17章のCalibrationの**「実際」を埋める回収口**。3日の窓(閉区間)を
  過ぎたら409、答えないのは「叩かないこと」で表される欠測
- 0006の索引2本。改頁のための複合索引と、「1つのLATCHに記録は高々1行」を
  保証する部分UNIQUE(0004・0005の3本目)

ここまでで、Intentを登録してから、提案・返事・成立・チャット・完了・申告
まで、LATCHの一本道がすべて実装されました。第14章〜この章を、1本のlatches行
の一生(candidate→proposed→matched→completed)として読み直すと、どの章も
同じ2つの道具——行の状態で書けることを閉じ、条件付きUPDATEで競合を裁く——
の運用であることが見えるはずです。

## 20.9 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| CHAT_READONLY | チャットが読み取り専用状態への送信に対する409の単一コード |
| 読み取り専用 | 閲覧はできるが新規送信が閉じている状態(completed・cancelled・ブロック適用中) |
| messages | 成立LATCHごとのチャットの発言を書くテーブル(0001から存在。列変更なし) |
| 単方向/双方向 | blocks行は blocker→blocked の片方向の記録。判定は両方向を見る |
| EXISTS | 該当行が1行でもあるかだけを返すSQLの存在検査 |
| attendance(実施自己申告) | 「実際に会いましたか?」の1問への回答API(D-09) |
| ATTENDANCE_ALREADY_SUBMITTED | 申告の二重回答(2人目以降・並行先着負け)への409 |
| ATTENDANCE_WINDOW_CLOSED | completedから3日経過への409 |
| 先着1名 | 申告の回答単位がLATCH単位であること。最初の1回答で記録が確定 |
| 欠測 | 申告の無回答。APIを叩かないことで表現され、集計の分母から除かれる |
| 複合索引 | 複数列の組で並べた索引。改頁のWHEREと並びを揃えると効く |
| 部分UNIQUE索引 | 一部の行だけに一意制約をかける索引(0004・0005・0006の3兄弟) |
| 0006 | idx_messages_latchとux_calibration_latchを追加するマイグレーション |

## 20.10 確認問題

1. チャットの書込可否を「matched単一条件」に集約した理由を、仕様の3か所
   (05 §2・03 §6・08 §2.5)を突き合わせて説明してください
2. completedした直後のLATCHに、参加者がメッセージを送ろうとしました。
   sweeperのcompleted化と送信APIが同時に動いても結果が狂わない理由を、
   FOR UPDATEとmessagesテーブルの構造(状態の列がないこと)から説明して
   ください
3. ブロック適用中のLATCHで、GET /messagesは200になるのにPOST /messagesは
   409になる理由を、「書く道と読む道」で説明してください
4. 申告で2人の参加者が同時に回答を送ったとき、1人だけが200になる仕組みを、
   UPDATEのWHERE句と影響行数で追ってください
5. `attended: false` と答えたとき、calibration_recordsの2列
   (actual_attended・cancelled_after)がどう入るか、そして両方trueがあり
   得ない理由を説明してください
6. 参加者以外への申告が404でmessagesが403なのはなぜか、通知の届き範囲と
   関与の開示の観点から説明してください
7. ux_calibration_latchが「部分」UNIQUEである理由を、将来の匿名化(D-13)と
   結びつけて説明してください
8. 申告をせずに3日が過ぎたLATCHは、集計の中でどう扱われますか。
   「答えない」ことをAPIで表現しない設計と結びつけて説明してください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった
部分が、読み返すべき節です)
