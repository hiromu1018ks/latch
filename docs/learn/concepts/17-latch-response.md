# 第17章 提案への返事がシステムを動かす: LATCH応答系とCalibration

- 種別: 解説(通読して理解を積む章)
- 前提知識: 第14章(Layer 5・latchesとproposal・D-05/D-06/D-07・部分UNIQUE索引0004)・
  第15章(グループのlatches・group_candidate_id)・第13章(ガード付きUPDATEとjev_result)。
  第9章(Event駆動のstage1)と第12章(文字bigram)も参照します
- この章を読み終えるとできるようになること:
  - ユーザーの回答(yes/no/defer)がlatchesのstatusをどう動かすかを、遷移表も合わせて説明できる
  - FOR UPDATEと条件付きUPDATEの2段構えが「同時回答・期限直後の回答」をどう排除するか、
    SQLのWHERE句を読んで説明できる
  - 409が3種類(LATCH_EXPIRED / ALREADY_ANSWERED / LATCH_CLOSED)ある理由と、
    影響行数0からエラーを分類する流れを追える
  - 全員のYESで成立(matched)した瞬間に、Intentのmatched化と競合クローズが
    同じトランザクションで走る理由を説明できる
  - 成立後の解放情報(参加者表示名・集合情報)がなぜ成立まで出ないのか、
    APIの応答を見て説明できる
  - Calibrationレコードが「予測と実際」の何を並べて保存するのか、
    predictionの中身とsegment(lexical/semantic)の判定方法も合わせて説明できる
  - Intentが削除されたとき、残りの参加者がactiveへ戻る経路を追える
- 対応コード: `backend/src/latch/latches/`(routes・service・store・errors・schemas・
  calibration)と `worker/stage1.py` の追加分・`worker/matching/group_engine.py` の
  昇格UPDATE追従・`worker/matching/layer3.py` の `bigrams`
- 設計の根拠: `docs/plans/M3/ws-1-design.md`(特に§2.2の直列化手順・§2.5のCalibration)と
  `docs/plans/M3/ws-1-report.md`。本文の「NN §X」は `docs/NN-*.md` の第X節を指します
  (05 §5=回答APIの契約・05 §6=遷移表・06 §6=直列化・07 §6=Calibration・
  03 §5〜§7=画面の表示・09 §2.3=segment・01 §8=競合の不成立)
- 次に読むもの: この章の内容を手で動かすなら `labs/lab5-latch-response.md`(Lab 5)。
  実装の進行に合わせて、期限切れを掃除するexpiry_sweeper(ws-2)と通知(ws-3)が後続の章です

## 17.1 第14章の終わりから始める: 提案は、まだ何も約束していない

第14章と第15章を読み終えた時点のlatchesテーブルを思い出してください。Layer 5が
latch_score 0.60(提案閾値・v0.6)を超えた組にlatches行を作り、statusをcandidate
からproposedへ昇格させ、notificationsに1行書きました。スマホには「提案が届いて
います」という通知が届いている。ここまでがM2で作った世界です。

ところが、その提案の行には**まだ誰の意思も入っていません**。提案は「2人とも
行きそうなら、この組み合わせで会いませんか」という問いかけであって、約束では
ない。ユーザーが `[参加する]` `[今回は見送る]` `[辞退する]` のどれかを押して
はじめて、この行は「成立」か「不成立」へ向けて動き出します(03 §5)。その返事を
受け付け、確定までのすべてを面倒見るのが、この章で読む **LATCH応答系** です。
2026-09-30にマージされたM3 ws-1(latchesドメイン)が担います。

返事の受け付けは、一見すると単純な仕事に見えます。latches行を読んで、
responsesに回答を追記して、statusを更新する。これだけなら数十行のコードで
終わるでしょう。実際のコードは`backend/src/latch/latches/`に6ファイル・約1300行。
差が開く理由は3つです。

1. **同じ行に、同時に返事が来る**。AさんとBさんが同じ提案をほぼ同時に開いて、
   ほぼ同時にボタンを押す。2つの処理が同じ行へ同時に書き込んでも矛盾しない
   仕組みが必要です
2. **期限は抽象的な概念ではなく、比較できる値**。「回答期限を過ぎた返事は受け
   付けない」を、アプリの誠実さでなくSQLのWHERE条件で守る必要がある
3. **成立・不成立は、latchesの1行だけの話ではない**。成立すれば2人のIntentは
   マッチング市場から引退し、他に開いている提案は閉じる。不成立なら別の道がある。
   1回の返事が、いくつものテーブルへ連鎖する

この章は、この3つを順に読み解きます。コードの入口は第14章の読み方と同じです。
APIの窓口(routes.py)から入って、判定の流れ(service.py)を読み、SQL(store.py)で
裏取りする。3つを読み終えたあと、17.6で成立後にだけ見せる情報、17.7で予測の
精度をあとで検証するための記録を読みます。順番に追いましょう。

## 17.2 返事の窓口は3つだけ: APIの契約

応答系のAPIは3本です(05 §5)。`backend/src/latch/latches/routes.py` に並んでいます。

```python
latches_router = APIRouter(
    prefix="/v1/latches",
    tags=["latches"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4と同型)
)
```

(`backend/src/latch/latches/routes.py:28` から)

| メソッドとパス | 役割 |
|---|---|
| `GET /v1/latches` | 自分が関与しているLATCHの一覧(cursorページング) |
| `GET /v1/latches/{id}` | LATCHの詳細。成立後は解放情報を含む |
| `POST /v1/latches/{id}/response` | 回答の送信。`{"response": "yes"}` のような本文 |

`api_rate_limited` が付いているのは第7章のレート制限です。すべての `/v1` ルートが
この守りを通る、というプロジェクト全体の規律を、新しい窓口も踏襲しています
(試験 `test_rate_limit_wiring.py` が「全ルートに制限が付いていること」を機械的に
強制しています。ws-1でこの期待表に3行が加わりました)。

回答の本文は3値だけです(05 §2)。**yes**(参加する)・**no**(辞退する)・
**defer**(今回は見送る)。noとdeferは、どちらも提案を閉じる点では同じ扱いです。
違いはあとで効いてきます。第14章14.6で読んだD-07(再提案制御)が、
「noを受け付けた」のか「deferを受け付けた」のかをresponsesの中の値で区別する
ためです。だからこの章の実装でも、noとdeferを別の値としてそのまま保存します。

回答を送ると、応答はこの形で返ってきます(Lab 5で実際に観察します)。

```json
{
  "latch": {
    "id": "…", "status": "partial_accept", "response_deadline": "…",
    "expires_at": "…", "created_at": "…", "completed_at": null,
    "proposal": {"headcount": 2, "match_level": "medium"},
    "is_group": false, "my_response": "yes", "remaining_responses": 1
  }
}
```

注目すべきは**入っていないもの**です。latchesテーブルにはresponsesというJSONB列が
あって、誰がいつ何と答えたか全員分が入っています。しかしAPIの応答には、
「自分の回答」(`my_response`)と「残り必要人数」(`remaining_responses`)しか出ません。
他の誰がもう答えたかは、インターネット上の見知らぬ相手に知られたくない情報です。
「あと2人の回答が必要」を人数だけで伝え、誰が答えたかは出さない(03 §5)。
この**情報最小化**は、表示の都合ではなくAPIの応答構造そのもので守られています。

## 17.3 「先に読んだ人が正解」を1行のSQLで守る: FOR UPDATEと条件付きUPDATE

この節が応答系の心臓部です。ゆっくり読みます。

### 同時回答という難問

自分と相手が、同じlatches行へ同時にyesを送ったとします。理想的には両方とも
受理され、statusはpartial_accept(1人目)とmatched(2人目)へ順に進みます。しかし
素朴な実装では、こう書きたくなります。

1. latches行をSELECTしてstatusとresponsesを読む
2. Pythonで「追記後のresponses」と「新しいstatus」を計算する
3. UPDATEで書き込む

この3 stepの間に、もう片方の処理が割り込めます。両方が同じ「追記前のresponses」
を読んで、それぞれの回答を追記して書き込んだら、後から書いた方が先の回答を
上書きして消します。片方の返事が歴史から消える——これは許されません。

### 2段構えの直列化

LATCHの解は2段構えです。1段目は**行ロック**、2段目は**ガード付きUPDATE**。
第13章と第14章で「ガード付きUPDATE」として何度も出てきた書き方の、いちばん
厚い版だと考えてください。

**1段目: FOR UPDATE(行ロック)**。

```sql
SELECT id, status, intent_ids, responses, response_deadline, expires_at,
       score, proposal, group_candidate_id, created_at
FROM latches WHERE id = CAST(:latch_id AS uuid)
FOR UPDATE
```

(`backend/src/latch/latches/store.py:93` の `_SELECT_LATCH_FOR_UPDATE` から)

SQLの `FOR UPDATE` は「この行を読みながら、**書くつもりである**ことをDBに宣言する」
構文です。効果はトランザクションをまたいで持ちます。Aの処理がこの行をFOR UPDATE
で読んだら、Aのトランザクションが終わる(コミットする)まで、他のトランザクションは
同じ行をFOR UPDATEで読めず**待たされます**。つまり、同じ行への回答処理は、
たとえ同時に届いても、DBの中で自然に1列に並びます。先に並んだ方が先に処理され、
後から並んだ方は、先の処理の結果がコミットされた**あとの状態**を読んで始めます。

**2段目: 条件付きUPDATE**。読んだ行の状態を材料に、Python側で「新しいstatus」を
計算します(この純計算はこのあと17.5で読みます)。そして書き込みは、ただの
UPDATEではなく、条件付きUPDATEで行います。

```sql
UPDATE latches
SET responses = responses || CAST(:item AS jsonb),
    status = :new_status
WHERE id = CAST(:latch_id AS uuid)
  AND status IN ('proposed', 'partial_accept')
  AND response_deadline > CAST(:now AS timestamptz)
  AND expires_at > CAST(:now AS timestamptz)
  AND NOT EXISTS (
      SELECT 1 FROM intents i
      WHERE i.id = ANY(latches.intent_ids)
        AND i.status NOT IN ('active', 'paused'))
RETURNING id, status
```

(`backend/src/latch/latches/store.py:106` の `_UPDATE_RESPONSE` から)

WHERE句を1行ずつ読むと、このUPDATEが受理するのは「**今まさに回答できる状態にある**」
行だけだと分かります。

- `status IN ('proposed', 'partial_accept')` — まだ開いている(終端状態でない)
- `response_deadline > :now` — 回答期限がまだ来ていない
- `expires_at > :now` — Intent自体の寿命もまだ来ていない
- `NOT EXISTS (…)` — 参加しているIntentがすべて active か paused である
  (削除やキャンセルがされていない)

どれか1つでも崩れていたら、UPDATEは**1行も更新しません**。`RETURNING` が何も
返さない=影響行数0。プログラムはそこで「私の前提は、もう古かった」と知ります。

FOR UPDATEとWHEREの二重がけには意味があります。FOR UPDATEは並びを整える装置で、
前提が古くなる可能性を消すものではありません。たとえば、**掃除バッチの遅れで
statusがまだproposedのままになっている**行を考えてください。回答期限は
もう過ぎています。そこに返事が来たらどうなるか。読み取ったstatusはproposed
なので一見受理できますが、`response_deadline > :now` が偽になるのでUPDATEは
影響0で弾かれます。

逆に、WHERE条件は真でも、読み取りからUPDATEまでの間に他の処理が割り込む
隙間があります。これをFOR UPDATEが封じます。**読むときに列を作り、書くときに
条件で検品する**。この2段によって、「期限が過ぎた返事」も「二重回答」(次節で
扱う分類)も、アプリの礼儀ではなくDBの振る舞いとして防がれるのです(06 §6)。

なお `:now` はDBの現在時刻関数ではなく、アプリ側のClockから採った値を渡します
(第3章のClock)。FOR UPDATEで行を取ったあとに `Clock.now()` を1回呼んで、
検査にもUPDATEにも同じ値を使い回します。テストで時計を差し替えたとき、判定に
使う時刻が場所によってずれないための規律です(`service.py:83` の
`now = self._clock.now()  # FOR UPDATE取得後に採取`)。

### 手順0〜6: service.respond の流れ

以上を前提に、`service.py` の `respond` が全体をどう進めるかを見渡します。
設計メモが「手順0〜6」と番号を振っているので、それに沿って読んでください。

```python
async def respond(self, *, auth_provider, auth_subject, latch_id, response):
    try:
        async with self._engine.begin() as conn:          # トランザクション開始
            user_id = await store.fetch_user_id(...)       # 手順0: 未登録JWT→404
            row = await store.select_latch_for_update(...) # 手順1: FOR UPDATE読取
            now = self._clock.now()
            my_intent = await store.select_participant_intent(...)
            if my_intent is None:                          # 手順2a: 参加者外→403
                raise ForbiddenError("not a participant")
            for r in row.responses:
                if r.get("user_id") == str(user_id):       # 手順2b: 二重回答→409
                    raise AlreadyAnsweredError("already answered")
            if row.status not in _RESPONSE_ACCEPTING:      # 手順2c: 終端→409分類
                self._raise_closed_or_expired(row, now)
            item = {"user_id": …, "intent_id": …,
                    "response": response, "answered_at": now.isoformat()}
            after = [*row.responses, item]
            new_status = compute_new_status(               # 手順3: 純計算
                response, after, len(row.intent_ids))
            updated = await store.update_response(         # 手順4: 条件付きUPDATE
                conn, latch_id=row.id, item=item,
                new_status=new_status, now=now)
            if updated is None:                            # 手順5: 影響0→409
                self._raise_closed_or_expired(row, now)
            await store.insert_latch_event(                # 手順6a: 遷移の記録
                conn, row.id, from_status=row.status,
                to_status=new_status, user_id=user_id, now=now)
            if new_status == "matched":                    # 手順6c: 成立の連鎖
                await self._on_matched(conn, row, now)
            # 手順6b(rejected時のCalibration作成)と、matched/rejected共通の
            # Calibration呼び出しは、このあと17.5と17.7で読みます
            …
```

(`backend/src/latch/latches/service.py:67` から。一部省略)

トランザクション(`engine.begin()`)の中に、判定も書き込みも連鎖も全部入っている点を
見てください。このトランザクションがコミットする瞬間、latchesのstatus・responses・
latch_status_events・(成立なら)intentsと他のlatches行——すべてが一斉に確定します。
どれか1つでも例外が出れば全部が元に戻る。「成立が確定したのに相手のIntentが
マッチされたまま残っている」ような中間状態は、構造的に作れません。

## 17.4 断られた理由を3種類だけ返す: 409の分類

手順2〜5で返事が受理されなかったとき、APIはどう答えるべきでしょうか。
「409 Conflict(競合)」というステータスコードに、理由を示す code を添えて
返します(05 §5)。codeは3種類です。

| code | 意味 | いつ起こるか |
|---|---|---|
| `LATCH_EXPIRED` | 回答期限が切れた | response_deadline か expires_at を過ぎた |
| `ALREADY_ANSWERED` | この人はもう答えた | responsesの中に自分のuser_idがある |
| `LATCH_CLOSED` | 提案がもう閉じている | rejected(断られた)・matched(成立済み)・cancelled(競合クローズか削除)・candidate(まだ提示されていない)のいずれか、または参加Intentの消失 |

3つ目のLATCH_CLOSEDは「いま回答できる状態にない」を全部引き受ける籠です。
rejected・matched・cancelledのような終端状態はもちろん、**candidate(まだ誰にも
提示していない様子見の行)**への回答も「回答できる状態にない」のでLATCH_CLOSED
になります。さらに17.5の末尾で読む「参加Intentが削除・キャンセルされていた」
場合も、ここに分類されます。

分類は、**事前に分かるものは事前に返し、分からなかったものは影響行数0で
判断する**、という分担で行われます。手順2bの「responsesの中に自分がいるか」は
FOR UPDATEで読んだ行からすぐ分かるので、ここで409 ALREADY_ANSWEREDを返します。ところが「期限が
切ったあとの返事」のようなケースは、事前検査の瞬間にはstatusがまだproposed
かもしれない。だから条件付きUPDATEを最後の検品として流し、影響行数0が返って
きたら、あらためて**読み取った値から理由を推定**します。

```python
@staticmethod
def _raise_closed_or_expired(row, now) -> None:
    """手順2c/5の409分類(期限切れが上・design §2.2)。"""
    if row.response_deadline <= now or row.expires_at <= now:
        raise LatchExpiredError("response deadline passed")
    raise LatchClosedError("latch closed")
```

(`backend/src/latch/latches/service.py:222` から)

期限が切れていればLATCH_EXPIRED、そうでなければLATCH_CLOSED。**期限切れの
判定を優先**しているのは、掃除バッチの遅れ(17.3で見た「statusはまだproposed」)
でも、ユーザーには「期限が切れたから」と正しく伝えたいからです。

ついでに言えば、409の仲間ではないエラーも2つ出てきます。`422 VALIDATION_ERROR`
はresponseがyes/no/defer以外("maybe"など)のとき、Pydanticがルータの入口で弾きます。
`403 FORBIDDEN` は参加者以外の操作に対して、存在を隠さず403と返します——
intents系と同じ方針です。**存在秘匿にしない**のは、「その提案は存在するが、
あなたは関係ない」と正直に伝えるほうが、クライアントの分岐を単純にするからです。

## 17.5 全員のYESが揃った瞬間に全部が決まる: 成立と競合クローズ

### 新しいstatusは、数を数えるだけ

手順3の「新しいstatus」の計算は、SQLを1本も叩かない純計算です。

```python
def compute_new_status(response: str, responses: list[dict], total: int) -> str:
    """手順3の純計算(design §2.4)。responsesは追記後の全量。

    yes→全員yesでmatched・未満でpartial_accept(必要人数=|S|・D-06)。
    no/defer→即rejected(グループでも部分成立なし)。
    """
    if response != RESPONSE_YES:
        return "rejected"
    yes = sum(1 for r in responses if r.get("response") == RESPONSE_YES)
    return "matched" if yes == total else "partial_accept"
```

(`backend/src/latch/latches/service.py:45` から)

yesを数えて、必要人数と一致したらmatched。1対1なら2人のYESで成立。3人グループ
なら3人全員——必要人数は「集合の人数そのもの」で、「4人中3人のYESで成立」
という部分成立はしません(第15章15.6で読んだとおり、部分成立なしはグループの
確定値です。06 §8 D-06)。**noかdeferが1人でも入れば
即rejected**です。グループで2人yesのあと3人目がnoなら、そこで閉じます。
「あと1人だったのに」という提案の世界は、途中経過として存在しません。

yesが1人入ってまだ足りない状態は **partial_accept**(部分受諾)と呼ばれ、
latchesのstatusとしてきちんと記録されます。第14章14.3の部分UNIQUE索引が
「開いている状態」にcandidate・proposed・partial_acceptの3つを数えていたのを
思い出してください。回答待ちの途中も「開いている」ので、他の提案が同じ組み合わせ
に作られることはありません。

### 成立の連鎖: Intentを引退させ、他の提案を閉じる

new_statusがmatchedになったとき、同じトランザクションの中で2つの連鎖が走ります
(手順6c、`_on_matched`)。

```python
async def _on_matched(self, conn, row, now) -> None:
    """手順6c: 参加Intent matched化+競合クローズ(design §2.3)。"""
    matched = await store.match_intents(conn, row.intent_ids, now)
    if len(matched) != len(row.intent_ids):
        raise DependencyUnavailableError("intents changed during match")
    conflicts = await store.select_conflicting_latches(conn, row.id, row.intent_ids)
    for latch_id, from_status in conflicts:
        if await store.cancel_latch(conn, latch_id):
            await store.insert_latch_event(
                conn, latch_id, from_status, "cancelled", None, now
            )
```

(`backend/src/latch/latches/service.py:229` から)

**1つ目の連鎖: 参加Intentのmatched化**。intentsテーブルのstatusをmatchedへ
UPDATEします。Intentは「募集している状態」なので、自分を含む提案が成立した
瞬間に市場から引退します(05 §6)。以降の再評価(Layer 5の再評価・第15章の
継続枠)は、matchedなIntentを対象にしません。

**2つ目の連鎖: 競合クローズ**。同じIntentを含む**他の開いているlatches**を
cancelledへ閉じます。AさんのIntentは、時として複数の組み合わせで提案を持てます
(同時に3件まで、第14章D-08)。AさんとCさんの提案が開いているときに、Aさんと
Bさんの提案が成立したら、AさんとCさんの提案はもう成立できません。相手のIntentは
activeのままかもしれませんが、自分のIntentが引退したので、その提案は閉じる
しかない。これを**競合クローズ**と呼びます(01 §8)。

対象の探し方に、このプロジェクトらしいSQLが1つ出てきます。

```sql
SELECT id, status FROM latches
WHERE id <> CAST(:self_id AS uuid)
  AND status IN ('candidate', 'proposed', 'partial_accept')
  AND intent_ids && CAST(:member_ids AS uuid[])
ORDER BY id
FOR UPDATE
```

(`backend/src/latch/latches/store.py:130` の `_SELECT_CONFLICTING_LATCHES` から)

`&&` はPostgreSQLの配列演算子で、「**重なりがあるか**」を意味します。
`intent_ids && :member_ids` は「このlatchの参加Intent一覧と、いま成立した
メンバー一覧に、1つでも共通のIntentがあるか」。LIKEでもJSONBの包含でもなく、
UUIDの配列同士の交差判定で「関係ある提案」を拾えます。participant上限から対象は
多くても十数行なので、FOR UPDATEでまとめて押さえてから1行ずつ閉じます。
閉じながら `latch_status_events` にも(from_status, to_status='cancelled',
user_id=NULL)の行を残します——**この提案は誰の返事ではなくシステムの判断で
閉じた**、という記録です(user_idがNULL=システム起因、05 §2)。

なぜイベント発行ではなく、いまのトランザクションで直接閉じるのでしょうか。
Workerに「成立したので閉じてください」とイベントで知らせる設計も考えられます。
しかし配信の遅れている数秒間、「成立済みの相手への提案」が開いたまま残り、
そこへの回答が受理されてしまう窓ができます。06 §6はこの窓を許しません。
回答APIのトランザクションがコミットする瞬間には、世界の整合が確定している
——直列化と同じ思想が、連鎖にも及んでいます。

### 解散: 成立したけれど、道が消えるとき

実はもう1つ、latchesがcancelledへ向かう道があります。**成立後に、参加者の
Intentが削除される**ケースです。「matchedなのに削除できるの?」——たしかに、
削除APIはdraft・active・pausedだけを対象にしていて、matchedなIntentは通常の
操作では削除できません(第6章6.5)。それでもこの道を作る理由があります。
05 §6の遷移表が「matched→cancelled(参加Intentの削除)」という遷移を定めていて、
仕様として存在する状態遷移は、どんな経路で起こっても正しく処理されなければ
ならないからです。その担い手として、削除の波及をEvent経由で請け負っている
stage1(Worker)の削除処理が、今回のws-1で拡張されました。

```python
async def close_latches_on_delete(conn, intent_id, now):
    """削除Intentを含むlatchesのクローズ+matched解散・残Intent復帰
    (design §2.7・06 §6)。"""
```

(`backend/src/latch/worker/stage1.py:455` から)

処理は2つです。開いているlatches(candidate/proposed/partial_accept)をcancelled
へ閉じる。そしてmatchedのlatchesを**解散**——cancelledへ閉じたうえで、
**残った参加者のIntentを元に戻します**。戻り先は2通りです。Intentのexpires_atが
まだ来ていなければactive(もう一度マッチングの市場へ戻る)、過ぎていればexpired。
「食べる予定が消えたから、メンバーの残りはもう募集できない」ではなく、
「もう一度、条件の合う別の誰かと結ばれるチャンスが残る」——それが解散と
削除の違いです(05 §6の遷移表)。

戻ったIntentは、30分Bucketの再評価とcatch-upスキャン(第14章14.9)で、次の
時間枠の評価対象になります。市場は、いったん外れた人を放り出さない。

なお、この経路は**Workerの処理**なので、削除APIが返った直後にはまだlatchesが
開いている数秒間があります。この隙間への回答を封じるのが、17.3のUPDATEの
WHEREにあった `NOT EXISTS (参加Intentがactive/pausedでない)` の検査です。
Intentがcancelledになった瞬間から、回答UPDATEは影響0で弾かれます。設計メモが
この検査を「06 §6の確定値への厳格化」と呼んだ理由です——仕様のWHEREに、
実装で削除レースの防御を1つ重ねた。裏を返せば、仕様の条件だけでも安全側に
倒れているので、この追加は「狭める方向」であり、緩和ではありません。

## 17.6 成立したら、初めて見せられるものがある: 解放情報

回答APIと並んで、GETの2本も応答系の一部です。`GET /v1/latches` は自分が関与する
LATCHの一覧を、`GET /v1/latches/{id}` は1件の詳細を返します。

一覧のSQLには、どうやって「自分が関与している」を調べているかという、小さくて
効いた工夫があります。

```sql
WHERE l.status <> 'candidate'
  AND EXISTS (SELECT 1 FROM intents i
              WHERE i.id = ANY(l.intent_ids)
                AND i.user_id = CAST(:me AS uuid))
```

(`backend/src/latch/latches/store.py:196` の `_SELECT_LATCHES_PAGE` から)

latchesには「この提案は誰に見せるか」の列がありません。参加者はintentsを経由で
しか分からない。そこで `EXISTS`(存在するか)で「このlatchのintent_idsの中に、
自分のIntentが1つでもあるか」を調べます。`status <> 'candidate'` も大事です。
candidateは「まだ誰にも提示していない、様子見の行」(第14章14.3)。nearby_alsoの
存在通知で相手に見せるのは候補の**存在**であって中身ではない、という03 §2の
約束を、一覧のSQLが守っています。

詳細APIの役割は「**成立後の解放**」です。statusがmatchedか、completed(成立後に
対象時刻が過ぎて完了した状態。この遷移自体はws-2で実装予定)のときだけ、
応答に次の3つが加わります(03 §6)。

- `participants` — 参加者のuser_id・表示名・プロフィール
- `time_summary` — 対象開始時刻をJSTで整形した文字列(例: `2026-10-05 22:24`)
- `area_name` — 参加Intentの位置の中点から引いた地名(例: `鹿児島市クックレモン`)。
  座標から地名を引くことを**逆ジオコード**と呼びます。地名から座標を引く
  ジオコーディング(第1章1.7)の逆方向です

相手と会えることが確定するまで、システムは知っている表示名を出しません。
第14章14.4で読んだvisibility分岐(成立まで隠す提案)と同じ思想です。相手の
表示名・プロフィール・どこでいつ会うのか——**会う約束が成立してはじめて
意味を持つ情報**を、成立の瞬間まで出さない。提案の間はmatch_level(相性の
ざっくり感)だけを見せ、成立後に人物と場所を明かす。人と人をつなぐサービスの
礼儀が、APIの応答構造に刻まれています。

time_summaryとarea_nameの材料は、参加Intentのtime_startとgeo_centerから毎回
計算します。latches.proposalには、hidden_until_matchの提案だと場所も時刻も
入っていません(第14章14.4)。だから成立後のこの瞬間に、intentsから取り直して
作るのです(この計算は`service.py:198` から。座標の中点から地名を引く発想は、
第14章14.4のarea_nameと同じ平均点です)。

## 17.7 予測が外れたかどうかを、あとで裁くための記録: Calibration

ここからは、この単位でいちばん見逃されやすい機能——**Calibration**(較正)の
記録です。ラッチの成立・不成立とは直接関係なく、システムの**学習材料**を
溜める仕事です。

### なぜ「予測」と「実際」を並べて保存するのか

第13章のLayer 4は、相手の組み合わせごとに「AはBを受け入れるか 0.9」のような
確率を吐いていました。第14章はそれを掛け合わせてlatch_scoreを作り、閾値(0.60・
v0.6)を超えた組み合わせだけ提案しました。**つまりシステムは、毎回「この2人は
成立しそうだ」と予測している**わけです。では、その予測は当たっているでしょうか。

これを検証するには、予測時の値と、その後の実際の結果を**同じ行に**並べて
保存しておくしかありません。「0.9と予測した組み合わせは、実際に何%成立
したか」。この照合ができてはじめて、閾値0.60が妥当か、Jevの出力に偏りが
ないかを、数字で議論できる(07 §6)。保存先が `calibration_records` テーブルで、
「回答が確定した瞬間」——matchedかrejectedに遷移した瞬間——に作られます。

predictionの中身は、こう組み立てられます(`calibration.py:137` の
`build_prediction`)。

| キー | 中身 | 由来 |
|---|---|---|
| `would_a_accept_b` / `would_b_accept_a` | Layer 4の評価値 | match_candidatesの評価行 |
| `MutualScore` | 上2つのmin | 第14章14.2と同じ合成 |
| `L` | 提示時のlatch_score | latches.score |
| `jev_5axis` | 5軸の評価とconfidence | 同評価行 |
| `provider` / `model` | どの判定系・どの版か | 同評価行 |
| `segment` | `"lexical"` か `"semantic"` | この節のあとで読む |

actual(実際)側は素直です。`actual_responses` は回答後のresponses全量、
`matched` は真偽値。この2列が「結果」です。

### segment: 言葉が重なった出会いか、意味が近かった出会いか

predictionの最後の `segment` は、09 §2.3の仮説2から来ています。LATCHのマッチング
は語彙の重なり(「焼肉」と「焼肉」)でも、意味の近さ(「焼肉」と「ステーキ」)でも
提案を作れます。**どちらの種類の提案のほうが予測が当たりやすいか**——これを
検証するには、レコードに「どちらだったか」の印が要ります。それがsegmentです。

判定は、第12章で読んだ文字bigramをそのまま使います。

```python
def classify_segment(texts_a: tuple[str, ...], texts_b: tuple[str, ...]) -> str:
    """bigram積集合が非空→lexical・空→semantic(双方空はsemantic)。"""
    if bigrams(texts_a) & bigrams(texts_b):
        return "lexical"
    return "semantic"
```

(`backend/src/latch/latches/calibration.py:49` から)

対象のテキストは、両者の `category_secondary`(サブカテゴリ。「焼肉」「イタリアン」
のような語)を基本とし、どちらかがなければsoft_constraintsの文言を使います。
2文字ずつの切り出しで重なり(積集合)があれば **lexical**(語彙一致)、
なければ **semantic**(意味近接)と分類します。

bigramの実装は、layer3のものをそのまま再利用します
(`from latch.worker.matching.layer3 import bigrams`)。もし第12章の語彙計算と
この章のsegmentでトークン化が2つに割れると、「Layer 3は語彙一致と判定したのに
segmentはsemantic」という不整合が起こりえます。そこで、パイプライン内の
切り方を1つに保ったのです(ws-1設計§2.6)。

グループ提案(第15章)の場合、predictionは全ペアの評価のうち **MutualScoreが
最小のペア**の値を使います。6ペアの評価があっても、記録するのは「いちばん
弱い環」の予測——それが集団の成否を握るからです(09 §2.3の「集約スコアの
minとなるペアのフラグを適用する」を、prediction全体に拡張した読み方。
ws-1設計§2.12)。

### できないときは、記録しないという判断

Calibrationの作成は、評価行の特定から始まります。latchesには「この提案は
match_candidatesのどの行から作られたか」を指す列がありません(05 §2)。そこで
serviceは、1対1なら次の3段階で特定を試みます(`calibration.py:69` の
`pick_eval_row`)。

1. `latch_score` がlatches.scoreと**一致する**評価行(いちばん新しいもの)
2. なければ、latches行の作成時刻より前に更新された評価行のうちいちばん新しいもの
3. それでもなければ、世代を問わずいちばん新しい評価行

スコアの計算とlatchesへの格納が同じトランザクション・同じ時刻で行われる
仕組み上、第1段でほぼ確実に見つかります。

それでも全段失敗したら、どうすると思いますか。エラーにして回答を落とすのでは
なく、**レコードを作らないで、構造化ログを1行残すだけ**にします。

```python
if hit_jev is None or pair_ids is None:
    logger.warning("latch.calibration.missing latch_id=%s", row.id)
    return
```

(`backend/src/latch/latches/service.py:264` から)

理由は損失の大小です。評価用の統計が1行欠ける損失と、ユーザーの回答が
成立しない損失。後者のほうが圧倒的に大きい。統計の穴はあとで埋められますが、
成立の瞬間は取り返せません。**学習の材料集めは、本編を邪魔してはならない**——
Calibrationまわりのコード全体を貫く優先順位です。

## 17.8 自分で確かめる

### 演習1: 409の言い分を予測する

次のそれぞれで、APIが返すHTTPステータスとcodeを予測してから、理由を言葉に
してください(予測が合っているかは、Lab 5で確かめられます)。

1. 期限切れの掃除バッチが遅れていて、statusはまだproposed。response_deadlineは
   30分前に過ぎている。ここにyesを送る
2. 自分がすでにyesで答えたlatchesに、もう一度yesを送る
3. 自分とBさんの提案がmatchedになった直後に、自分とCさんの開いていた別の
   latches(Cさんがまだ未回答)へ、Cさんがyesを送る

### 演習2: 試験を読む

`backend/tests/integration/test_latches_api.py` の冒頭には、17試験の一覧が
docstringに書かれています。試験4(期限切れ)と試験5(競合クローズ)を開いて、
「期限はどう作っているか」「競合する2つのlatchesをどう作っているか」を
読んでください。この「Lab 5もどき」(試験)の中の工夫は、Lab 5であなた自身が
やることと同じです。

### 演習3: SQLのWHEREを声に出して読む

`store.py` の `_UPDATE_RESPONSE` を開いて、WHERE句の4条件を、自分の言葉で
1つずつ言い換えてみてください。「なぜ status と期限の**両方**を検査するのか」に
答えられたら、17.3は読めています。

## 17.9 この章の再統合

第14章で、評価は提案に変わりました。しかし提案は、まだ何も約束していません
でした。この章で、提案への返事がシステムを動かすところまでを読みました。

核は**1行のUPDATEのWHERE句**です。FOR UPDATEが回答を1列に並ばせ、条件付き
UPDATEが「いま回答できる状態か」を検品する。影響行数0は「前提が古かった」の
サインで、読み取った値から期限切れかクローズかを分類して409を返す。この仕組み
だけで、同時回答も、期限直後の返事も、掃除バッチの遅れも、削除との競争も、
矛盾なく裁かれます。アプリケーションの礼儀ではなく、DBの振る舞いとして。
第13章で「ガード付きUPDATE」を学んだときの思想が、ユーザーの返事という
最も競合しやすい場所で、いちばん厚い形になって現れたのです。

成立は一瞬で世界を確定します。全員のYESが揃ったら、同じトランザクションで、
参加Intentは引退し、他の開いている提案は閉じ、競合クローズの記録が残り、
解放情報の材料が整う。コミットした瞬間、2人(以上)の間でだけ見える情報が
生まれる。逆に誰かがnoと言った瞬間、システムは何も取りはからわず、ただ正しく
閉じて、予測と実際の対をCalibrationに残します。次に同じ組み合わせを提案するか
どうかは、第14章のD-07がresponsesを読んで判断します。

この章で、latchesの行は「候補→提案→返事→成立/不成立」の一生を閉じました。
残るのは時間の管理です。返事の期限が本当に切れたらstatusをexpiredへ進める
掃除(expiry_sweeper)、成立した提案の対象時刻が過ぎたらcompletedへ進める
バッチ——これらはws-2が担い、後続の章で読むことになります。もう1つ、
成立・不成立を本人たちに伝える通知がws-3で加わります。その実装は、この章で
見た遷移の台帳(latch_status_events)を読みに行く——観測点は、もう書かれています。

## 17.10 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| 応答系 | 提示された提案への返事を受け付け、成立・不成立を確定する仕組み一式 |
| FOR UPDATE | 行を読みながら「書く予定」を宣言し、他のトランザクションを待たせるSQL構文 |
| 条件付きUPDATE | WHEREに「今まさに書ける状態」の条件を並べ、影響行数0を検品に使うUPDATE |
| 影響行数0 | UPDATEが1行も更新しなかったこと。前提が古くなった合図 |
| partial_accept | 一部がyesを返した状態。まだ開いている |
| matched | 必要人数全員がyesで成立した状態 |
| rejected | noかdeferで閉じた状態 |
| cancelled | 競合クローズや削除・解散で閉じた状態 |
| 競合クローズ | 成立時に、同じIntentを含む他の開いているlatchesをcancelledへ閉じること |
| 解散 | matchedなlatchesが参加Intentの削除でcancelledになり、残ったIntentがactive/expiredへ戻ること |
| 解放情報 | 成立後にだけAPIへ出る、参加者表示名・time_summary・area_name |
| 逆ジオコード | 座標(緯度経度)から地名を引くこと。地名→座標のジオコーディングの逆方向 |
| `&&` (配列演算子) | PostgreSQLで「配列同士に重なりがあるか」を判定する演算子 |
| EXISTS | 相当する行が存在するかだけを調べるSQLの書き方 |
| Calibration | 予測(prediction)と実際(actual)を1行に並べて保存する、判定精度の検証用記録 |
| segment | 提案が語彙一致(lexical)か意味近接(semantic)かの分類タグ |
| latch_status_events | latchesの状態遷移を、誰が起こしたか(user_id・NULL=システム)つきで記録するテーブル |

## 17.11 確認問題

1. FOR UPDATEを省いて「SELECT→計算→UPDATE」だけにすると、具体的にどんな事故が
  起こりえますか。登場人物を2人のユーザーとして、時系列で説明してください
2. 条件付きUPDATEのWHEREに status の条件と期限の比較が**両方**ある理由を、
   「掃除バッチが遅れている」状況を使って説明してください
3. 影響行数0が返ってきたあと、LATCH_EXPIREDとLATCH_CLOSEDをどう切り分けますか。
   期限切れを優先する理由は何でしょう
4. 成立(matched)のトランザクションの中で、latches以外にどのテーブルが変わりますか。
   すべて挙げてみてください
5. 競合クローズをイベント発行にしてWorkerに任せた場合、どんな窓ができますか
6. AさんBさんCさんDさんの4人提案で、A・B・Cがyes、Dがnoと答えました。statusは
   どうなりますか。Dがnoを押す直前はどうでしたか
7. GET /v1/latches/{id} の応答に、成立前はparticipantsが入らない理由を、
   情報最小化の観点から説明してください
8. Calibrationレコードのsegmentがlexicalになるのはどんな場合ですか。判定に
   使う道具は何でしょう
9. 評価行が特定できなかったとき、応答系は回答の受理をやめますか。それとも
   記録の作成をやめますか。その優先順位の根拠は何でしょう

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった
部分が、読み返すべき節です)
