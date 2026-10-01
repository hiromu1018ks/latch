# 第21章 相手から身を守る: ブロックと通報

- 種別: 解説(通読して理解を積む章)
- 前提知識: 第20章(チャットの送信経路と409 CHAT_READONLY・blocksの双方向判定)・
  第17章(競合クローズとcancel_latch・latch_status_events)・
  第7章(Redisで数える・レート制限)・第4章(3層の経路: ルータ→サービス→SQL)・
  第19章(通知はtypeしか渡せない)
- この章を読み終えるとできるようになること:
  - ブロックが1行の記録から3つの効果(候補から外す・進行中を閉じる・チャットを
    読み取り専用にする)を即時に出すしくみを、D-23の規定と突き合わせて説明できる
  - blocksの登録APIが冪等な201を返す理由と、登録・D-23のcancelled化・キャッシュ
    削除が「コミットの前後」でどう分担されるか追える
  - チャット送信のブロック判定がRedisのキャッシュを経由するようになった経緯と、
    「真実はDB・覚えはRedis」という役割分工を説明できる
  - キャッシュの内容が壊れたまま残っても最長1時間で直る(TTL)理由と、Redisが
    死んでいても判定が止まらない(フォールバック)理由を説明できる
  - ブロック解除が過去を戻さず未来だけを戻すことと、通報が受付・記録だけで
    何も自動化しないことの、安全性の設計上の意味を説明できる
- 対応コード: `backend/src/latch/safety/`(新設モジュール。cache・store・service・
  routes・errorsの5ファイル)と `backend/src/latch/latches/service.py` への差し替え数行・
  `backend/src/latch/main.py` の組み立て。マイグレーションはありません(blocks・reports
  テーブルは第4章の0001で作成済み)
- 設計の根拠: `docs/plans/M3/ws-5-design.md`(特に§2.2のキャッシュ・§2.3のD-23・
  §2.4の冪等201・§2.5の通報)と `docs/plans/M3/ws-5-report.md`。本文の「NN §X」は
  `docs/NN-*.md` の第X節を指します(08 §5.1・§5.2・D-23=ブロックと通報・
  05 §2・§5=テーブルとAPI・03 §5=不成立の文言)
- 次に読むもの: `labs/lab8-safety.md`(Lab 8)。ブロックの登録から、キャッシュの中身を
  redis-cliでのぞき、解除で復帰するまでを自分の手で歩きます

## 21.1 マッチングの最後に、拒否の権利が要る

ここまでの章で、LATCHは出会いを作る側の話でした。Intentを集め、意味で近い人を
探し、スコアを付け、提案して、成立させる。しかし実際のサービスでは、その逆の
操作が必ず要ります。「この人とは関わりたくない」。マッチングがどれだけ賢くても、
相手を拒む権利がなければ、システムは利用者を危険に晒す道具になります。

LATCHが用意した拒否の手段は2つで、性質が違います。**ブロック**は、自分の世界から
相手を外す操作。自分の候補から外れ、2人の進行中の提案は閉じ、チャットは書けなく
なります。**通報**は、運用する側への申し立て。「なりすましが疑われる」のように、
利用者ひとりの手では対処しきれない事態を、記録として残して人間に渡す窓口です。

この章で読むのは、この2つのAPIと、その裏で動くRedisのキャッシュです。

```
POST   /v1/users/{user_id}/block    ブロックの登録
DELETE /v1/users/{user_id}/block    ブロックの解除
GET    /v1/users/me/blocks          自分のブロック一覧
POST   /v1/reports                  通報
```

4つの窓口は、これまでusersやlatchesに足してきたのと同じ3層(ルータ→サービス→
SQL)で組みますが、入れる場所は新しく `safety/` というモジュールを作りました。
usersは登録と年齢検証、latchesは提案と回答が主題で、ここに「防護」を混ぜると
責務が曖昧になるためです(設計§2.1)。ブロックはlatchesテーブルをcancelledに
閉じるし、通報は独立したreportsテーブルに書く。ドメインとしてもまとまりが
あるので、箱を分けました。

## 21.2 1行のINSERTが、3つの効果を即時に出す

ブロックが及ぼす効果は、仕様のD-23に3つ書かれています。

1. 今後の候補生成から除外する(マッチングの入り口で外す)
2. 進行中の提案(proposed・partial_accept)をcancelledで閉じる
3. 成立済み(matched)のLATCHは、チャットを読み取り専用にする

この3つの中で、とりわけ「即時に」が効いています。ブロックしたのに相手への
提案がもう1回出てきたら、利用者はシステムを信じられなくなります。そこで
登録APIは、1回のリクエストの中でこれらをできるところまで一気にやります。

```python
async def block_user(self, *, auth_provider, auth_subject, target_user_id):
    try:
        async with self._engine.begin() as conn:
            me = await self._me(conn, auth_provider, auth_subject)
            if target_user_id == me:
                raise SafetyValidationError("cannot block yourself")
            if not await store.user_exists(conn, target_user_id):
                raise SafetyNotFoundError("user not found")
            if await store.block_exists(conn, blocker=me, target=target_user_id):
                return target_user_id  # 冪等201: 手順5〜7スキップ(引用#16)
            now = self._clock.now()
            await store.insert_block(
                conn, blocker=me, blocked=target_user_id, now=now
            )
            await self._cancel_open_latches(
                conn, me=me, other=target_user_id, now=now
            )
        await self._invalidate(me, target_user_id)  # コミット後DEL(§2.2)
        return target_user_id
    except SafetyError:
        raise
    except Exception as exc:
        raise _wrap_unexpected(exc) from exc
```

(`backend/src/latch/safety/service.py:97-126`。docstringは省略)

順番に読むと、検査が3つ並んでから書き込みが2つ、トランザクションの外に
もう1つ、という構成です。

自分自身はブロックできません(422 VALIDATION_ERROR)。相手が実在しなければ
404 NOT_FOUND。そして3つ目の検査がこのAPIの特徴で、すでにブロック済みなら
**INSERTもcancelled化もスキップして、同じ201を返します**。blocksテーブルには
UNIQUE制約がなく(0001作成時の判断)、同じペアの行が2本できても一覧が重複する
以外の害はありません。それでも毎回INSERTしていたのと、存在を確認してから
1本だけ書くのとでは、データの信用が違います。APIを2回叩いても同じ結果に
なる性質を冪等(べきとう)と呼びます。クライアントは「すでにブロック済みか?」
を気にせず、ただ登録を頼めばよい。この設計です。

SQLはごく短いものです。

```sql
_INSERT_BLOCK = text("""
    INSERT INTO blocks (blocker_id, blocked_id, created_at)
    VALUES (CAST(:blocker AS uuid), CAST(:blocked AS uuid),
            CAST(:now AS timestamptz))
    RETURNING id
""")
```

(`backend/src/latch/safety/store.py:48-53`)

## 21.3 登録と同じトランザクションで、進行中の提案を閉じる

`_cancel_open_latches` がD-23の2番目の効果、進行中の提案を閉じる処理です。
第20章では、blocks行が「あるもの」として送信側の判定だけを読みました。
ここで、その行を書き込む登録APIの後半を読みます。

対象を探すSQLが、まず1つの論点を含んでいます。

```sql
_SELECT_OPEN_LATCHES_BETWEEN = text("""
    SELECT l.id, l.status FROM latches l
    WHERE l.status IN ('candidate', 'proposed', 'partial_accept')
      AND l.intent_ids && CAST(:my_intents AS uuid[])
      AND l.intent_ids && CAST(:their_intents AS uuid[])
    ORDER BY l.id
    FOR UPDATE
""")
```

(`backend/src/latch/safety/store.py:90-97`)

仕様のD-23は「進行中の提案(proposed / partial_accept)はcancelledで閉じる」と
書いていて、candidateには触れていません。しかし実装はcandidateを含む3状態を
並べています。これは、保留キューの提示判定(第14章のtry_promote)がブロックの
再検査を持たないためです。candidateを放置すれば、次のdrainでブロック済みの
相手へ提案が提示されてしまう。D-23の1番目の効果「今後の候補生成から除外」は
マッチング経路全体の遮断を意図しているので、提示前の行も閉じるのが仕様の
精神に忠実、という判断です(設計§2.3。第17章の競合クローズがすでにcandidateを
閉じる対象にしていたのと同じ解釈です)。

`&&` は「配列同士に共通要素があるか」のPostgreSQLの演算子で、第15章のグループ
マッチングで出ました。2つの条件を並べることで、「自分のIntentと相手のIntentを
**両方**含むLATCH」だけが選ばれます。グループLATCHで第三者が混ざっていても、
自分と相手の双方が参加していれば閉じます。`ORDER BY l.id` を付けた上で
`FOR UPDATE` なのも第17章と同じ規律で、ロックを取る順序を固定してデッドロックを
防ぎます。

1行ごとの処理は、第17章の競合クローズが作った資産をそのまま借りる形です。

```python
for latch_id, from_status in rows:
    if await latches_store.cancel_latch(conn, latch_id):
        await latches_store.insert_latch_event(
            conn, latch_id, from_status, "cancelled", me, now
        )
```

(`backend/src/latch/safety/service.py:140-144`)

`cancel_latch` は「まだ対象ステータスならcancelledへ」の条件付きUPDATEで、
他の処理が先に状態を動かしていたらFalseを返します。Falseは読み飛ばす。
成功した行だけ、latch_status_eventsに1行ずつ書きます。

このイベントの `user_id` に注目してください。第18章で、バッチ(sweeper)が閉じる
遷移はシステム起因なので **user_idはNULL** と読みました。ブロックによるcancelledは
利用者の操作が引き金なので、引き金を引いた人(ブロックした側)のUUIDが入ります。
同じcancelledでも、誰が閉じたかが台帳で区別できるのです。

一方で、閉じたことを知らせる通知は送りません。仕様は不成立の理由を「この提案は
成立しませんでした」の一文に統一し、ブロックされた事実を一切開示しないと定め
ます(03 §5)。cancelledになった提案はホームの一覧から消えるだけで、相手は
「なぜ」を知りません。これも安全の設計で、ブロックした人の意思を、ブロックされた
人に気づかせないように守ります。

最後に、matchedだけはこの処理の対象外です。成立済みのLATCHをcancelledにすると、
会った記録やチャットの履歴の位置づけまで崩れてしまいます。代わりに、20.4で
読んだ送信時の判定が効きます。blocks行が増えれば、次の送信は409 CHAT_READONLY。
**書く道だけを閉じて、読める記録は残す**——第20章の設計が、そのままD-23の
3番目の効果を担います。

## 21.4 覚えと真実: 判定を速くするキャッシュの役割分担

### なぜキャッシュが要るのか

ここからが、この単位で一番の新しい道具です。

第20章のブロック判定は、送信のたびにDBへEXISTS 1本を投げていました。これ自体は
正確ですが、チャットの送信のような高頻度な操作で、毎回DBを見るのは無駄が多い。
しかもブロックしていない人のほうが圧倒的に多いのですから、大半の問い合わせは
「該当なし」を確かめるためだけのDB往復になります。

そこで、ブロック一覧をRedisに置いて、送信時の判定はRedisを見るように変えます。
データベースの内容を、読みが速い別の場所に写して引き合わせる仕組み一般を
**キャッシュ**と呼びます。置く場所には、第1章で「手元の付箋」と紹介したRedisを
使います。真実はあくまでDBにあり、Redisのほうは「覚え」にすぎません。この
主従の関係をこの節では繰り返し確認します。キャッシュで一番むずかしいのは
「覚えが古くなったらどうするか」だからです。

### キーの形とread-through

覚えの形は、ユーザー1人につき1つの鍵です。

```
blk:u:{user_id} = '["<ブロックした相手のuuid>", ...]'   (JSONの配列・3600秒で自動的に消える)
```

`blk:` は `auth:`(第7章以前のセッション失効リスト)や `rl:`(レート制限カウンタ)
と同じ、用途ごとの名前空間です。値は「その人がブロックしている相手の一覧」。
ブロックが1件もなければ `[]` という空の配列を丸ごと覚えます。**空も覚える**のが
ミソで、大多数の「ブロックなし」ユーザーの参照が、1回のDB読み込みで済むように
なります。

読み方は、必要になってから覚える方式です。渡された鍵をまとめてMGETで取りに
行き、無かった鍵だけDBから読んで、その場でSETする。この「読みに出たついでに
覚える」方式を **read-through(リードスルー)** と呼びます。

```python
async def is_blocked_between(self, me: uuid.UUID, others: list[uuid.UUID]) -> bool:
    if not others:
        return False
    try:
        user_ids = [me, *others]
        raw = await self._redis.mget([_key(u) for u in user_ids])
        lists: dict[str, set[str]] = {}
        missing: list[uuid.UUID] = []
        for user_id, value in zip(user_ids, raw, strict=True):
            if value is None:
                missing.append(user_id)
            else:
                lists[str(user_id)] = set(json.loads(value))
        if missing:
            lists.update(await self._load(missing))
        me_key = str(me)
        if lists[me_key] & {str(o) for o in others}:
            return True
        return any(me_key in lists[str(o)] for o in others)
    except RedisError:
        logger.warning("safety.cache.redis_error fallback=db")
        async with self._engine.connect() as conn:
            return await latches_store.select_block_between(conn, me, others)
```

(`backend/src/latch/safety/cache.py:46-73`。docstringは省略)

チャットへ送るときの呼び出しは `is_blocked_between(自分, 相手全員)` です。
自分の一覧と相手の一覧を突き合わせて、どちらかがもう一方を含んでいれば
True。第20章の「単方向の記録・双方向の判定」が、DBのEXISTS 1本から
「一覧を取ってきてPythonで比べる」形に変わりました。Redisとの往復はMGETの
1回で済み、DBは覚えのないユーザーが現れたときだけ訪れます。

### 覚えの更新は、コミット後に消す

登録や解除でブロックが変わったら、覚えは古くなります。ここでの戦略は、
書き換えません。**消します**。

service.pyの `block_user` をもう一度見ると、トランザクション(`async with
self._engine.begin()`)の**外**で `_invalidate` を呼んでいました。この中身は
Redisから2本の鍵をDELするだけです。自分と相手、両方の鍵を消すのは、双方向の
判定が両者の一覧を見るからです。消された鍵は、次の送信時のread-throughで
DBから読み直され、新しい内容で覚え直されます。

なぜ「新しい内容を書き込む」のではなく「消す」なのでしょうか。そしてなぜ
コミットの後なのでしょうか。

DELをコミットの**前**にやると、並行して走っている送信が、まだ古いDBを見て
覚えを再作成してしまう可能性があります。DEL→(コミット前のDBで旧値をSET)→
コミット、という順序が起きると、せっかく消した覚えが古い内容で生き返るのです。
コミット後なら、DELの後に読みに行く人は誰でも、コミット済みの新しいDBを見る
ことになる。書き換えないのは、書き換えのロジック(DBとRedisの値をマージする等)を
書かずに済むからです。消して、読ませる。それだけのほうが、壊れ方が少ない。

### TTLは、消し忘れの保険

鍵には3600秒(1時間)のTTLが付いています。TTL(第1章・生存時間)は通常、
「使い終わったデータを掃除する」ための仕組みですが、ここでの役割は違います。

DELはRedisへの命令であって、成功は保証されません。Redisが一瞬死んでいれば
DELは失敗します。そのとき登録API自体は失敗にしません。DBのコミットは成功
しているのだから、ブロックは事実として成立していて、覚えが古いだけだからです。
`invalidate` は例外を飲んで、warningログを1行残すだけ。

```python
async def invalidate(self, user_ids: list[uuid.UUID]) -> None:
    try:
        await self._redis.delete(*[_key(u) for u in user_ids])
    except RedisError:
        logger.warning("safety.cache.invalidate_failed ttl_converges")
```

(`backend/src/latch/safety/cache.py:86-96`)

では古い覚え(ブロック前の `[]`)はいつ直るのか。それがTTLです。1時間経てば
鍵は自動で消え、次のread-throughが新しいDBで覚え直します。つまりDELの失敗は
**最長1時間の違和感**に変わる。ブロックしたのに1時間だけ相手のメッセージが
届く、というのは気持ちの悪い話ではありますが、判定が永遠に古いままよりは
るかにましです。しかも、ちゃんとした経路(DEL→再読み込み)が働いていれば、
この1時間すら発生しません。TTLは最後の保険です。

### Redisが死んでいても、判定は止まらない

`is_blocked_between` の `except RedisError` に、もうひとつの役割があります。
Redis自体が落ちているとき、MGETは例外を出します。ここで諦めてエラーを返したら、
Redis断のせいでチャットが送れなくなります。ブロック判定は安全に関わる判定です。
覚えが読めないからといって「誰もブロックしていないことにする」のも危険です。

そこで、Redisが使えないときはDBへ聞き直します。フォールバック(退路)です。
呼び先は、第20章で読んだ `select_block_between` がそのまま残っています。
差し替えは「キャッシュがあればキャッシュ、なければDB」という分岐で、

```python
if self._block_cache is not None:
    blocked = await self._block_cache.is_blocked_between(user_id, others)
else:
    blocked = await store.select_block_between(conn, user_id, others)
```

(`backend/src/latch/latches/service.py:266-271`)

キャッシュを注入しない構成(ユニットテストなど)では、昔のとおりDB直読みで
動きます。**性能のための仕組みが、可用性と安全性をどちらも壊さない**。これが
「真実はDB・覚えはRedis」の役割分担の意味です。

なお、キャッシュを使うのはチャットの送信判定だけです。ブロック一覧APIは
created_atでの並び替えと改頁にDBの行がそのまま要るのでDBを読みますし、
マッチングのLayer 1(第11章)も引き続きSQLでDBを見ます。DB直読みは常に
真実を返すので、そちらの経路をキャッシュ化しない判断にも、同じ考え方が
反映されています(設計§5-1)。

## 21.5 解除は、過去を巻き戻さない

解除のAPIは、行を1本消すだけの素朴なものです。

```python
async def unblock_user(self, *, auth_provider, auth_subject, target_user_id):
    try:
        async with self._engine.begin() as conn:
            me = await self._me(conn, auth_provider, auth_subject)
            if not await store.delete_block(
                conn, blocker=me, blocked=target_user_id
            ):
                raise SafetyNotFoundError("block not found")
        await self._invalidate(me, target_user_id)
    ...
```

(`backend/src/latch/safety/service.py:146-159`。後半は省略)

DELETE … RETURNING id の影響行数が0なら、つまり消す行がなければ404 NOT_FOUND
を返します。「もう消えているなら成功(204)にしておけばいいのでは?」という気も
しますが、誤操作で二重に解除を頼んだときは、何も起きなかったと知らせるほうが
親切だと判断されました(設計§2.4)。冪等201との対比も面白いところです。登録は
「同じ状態に着地する操作」なので冪等に、解除は「対象があることが前提の操作」
なので、対象の不在は404と、性質に合わせて使い分けています。

効くのは未来だけ、という点も押さえておきます。解除はblocks行を消しますが、
ブロックの間にcancelledになった提案をmatchedに戻したりはしません。ブロックが
原因で閉じた歴史は、歴史のまま残ります。解除後のチャットは、送信のたびに
blocksの現物(キャッシュが消えて再読み込みされたあとのDB)を見るので、自然に
書けるようにもどります。誤ってブロックしてすぐ解除すれば、チャットは復活する。
過去に起こった閉鎖の記録だけは、そのまま残るのです。

一覧APIは第19章・第20章で何度も読んだcursor改頁の同型で、`created_at` と `id`
の2キー・降順です。応答には相手の表示名(display_name)も並びます。ブロック
管理の画面(実装はこれからの単位)で「誰をブロックしたか」を名前で見せるための
フィールドです。

## 21.6 通報は、受け取って記録するだけ

通報のAPIは、この章でいちばん動きが少ない窓口です。それ自体が設計です。

```json
request:  {"reportee_id": "<uuid>", "latch_id": "<uuid|null>", "reason": "inappropriate_content"}
response: 201 {"report_id": "<uuid>"}
```

reasonは選択式の4つで、英語の識別子を受け付けます。

| コード | 意味 |
|---|---|
| `inappropriate_content` | 不適切な内容 |
| `unpleasant_behavior` | 不快な対応 |
| `suspected_impersonation` | なりすましの疑い |
| `other` | その他 |

日本語の文言をDBに保存しないのは、表側の言葉が変わっても(言い方を直しても)
記録が動かないようにするためです。日本語の見出しは画面を持つ側の仕事で、
APIとDBはコードだけを知ります。latch_idは省略できます。LATCHに絡まない
通報——たとえばプロフィールだけを見ての通報——もあるためです。指定する
場合は、自分も相手もそのLATCHの参加者でなければ422になります。関わって
いない人同士の通報を受け付けても、調べようがないからです。

処理はreportsテーブルへの単一INSERTです。statusは `pending`(受付)を固定で
書き込み、それ以外のことはしません。警告も送らない、アカウントを止めない、
自動検知もしない。通報の先には「受付・記録 → レビュー → 対処(警告・アカウント
停止・ブロック支援)」の流れが想定されていて、レビュー以降は人間の運用の
仕事です(08 §5.2)。MVPではそこを自動化しないと仕様に書いてあります。

同じ通報を2回しても、拒まれずに2行残ります。機械的に重複を間引くより、
運用する人が一覧を見て判断するほうが、誤判断が起きにくいという割り切りです。

## 21.7 この章のまとめ

ブロックの登録は、たった1行のINSERTに見えて、3つの効果を連れて歩く処理
でした。候補から外すのはマッチングのSQLが、進行中を閉じるのは登録と同じ
トランザクション内のcancelled化が、チャットを閉じるのは送信時の判定が、
それぞれ受け持ちます。効果が即時であることは、キャッシュの更新を「コミット
後に消す・読み直させる」という手順で守られています。

通報はその対極の、何も自動化しない窓口でした。ブロックは自分を守る操作で、
通報は共同体を守る申し立て。速さが求められる操作と、注意が求められる操作で、
設計の温度が違うのです。

| 操作 | 仕掛け | 章へのリンク |
|---|---|---|
| 候補から除外 | Layer 1のSQLがblocksを見る | 第11章(実装はSQLに条件が入ったまま) |
| 進行中を閉じる | cancel_latchの再利用+台帳user_id=blocker | 第17章 |
| チャットを閉じる | 送信時409 CHAT_READONLY・判定はキャッシュ経由 | 第20章+この章21.4 |
| 通報 | pendingで記録するだけ | — |

## 用語集

| 用語 | 意味 |
|---|---|
| ブロック(block) | 自分の世界から特定の相手を外す操作。単方向の記録で、判定は双方向 |
| 通報(report) | 運用側への申し立て。受付・記録のみで、対処は人間のレビュー |
| 冪等(べきとう) | 同じ操作を繰り返しても同じ結果に着地する性質。ブロック登録の201が例 |
| キャッシュ | 読みが速い別の場所にデータを写して、DBへの問い合わせを減らす仕組み |
| read-through | キャッシュに無いときだけDBを読み、その場でキャッシュへ覚える方式 |
| TTL | データの生存時間。ここでは掃除でなく「消し忘れの最終保険」として使う |
| フォールバック(退路) | 主の経路(Redis)が使えないときに、副の経路(DB)へ切替えること |
| invalidate | キャッシュの覚えを消すこと。次のread-throughが新しい内容で覚え直す |
| D-23 | ブロックを成立後も即時に適用するという規定。3つの効果を定める |

## 確認問題

1. ブロックの登録APIは、同じ相手への2回目の登録でどんな応答を返しますか?
   また、そのときINSERTとcancelled化はどうなりますか?
2. blocksのキャッシュを更新するとき、実装は「新しい値を書き込む」のでなく
   「鍵をDELする」を選びました。コミットの後にDELする理由とあわせて説明して
   ください。
3. TTL 3600秒の役割は、第7章のレート制限カウンタのTTLと何が違いますか?
4. Redisが落ちている間、チャットの送信はどうなりますか? その判定はどこで
   行われますか?
5. ブロックを解除したあと、ブロック中にcancelledになった提案と、matchedだった
   LATCHのチャットは、それぞれどうなりますか?
6. 通報のreasonに日本語の文言ではなく英語のコードを保存する理由は何ですか?

(解答は用意しません。自分の言葉で答えられなければ、それが読み返すべき節の
地図です)
