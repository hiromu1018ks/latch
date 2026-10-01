# 第18章 時間が状態を閉じる: 期限切れバッチとリセットジョブ

- 種別: 解説(通読して理解を積む章)
- 前提知識: 第17章(FOR UPDATE・条件付きUPDATE・影響行数0・latch_status_events)・
  第14章(保留キューとdrain・D-08上限)・第13章(H再検証)・
  第12章(Jevカウンタと日付キー切替)・
  第7章(JSTバケット)・第3章(ClockとFakeClock)。第9章(outboxと同一トランザクションの
  慣行)と第6章(遷移表とversion)も参照します
- この章を読み終えるとできるようになること:
  - 「誰も操作しないのに時間で閉じる遷移」を、定期バッチがDBを確かめて担う理由を説明できる
  - FOR UPDATE SKIP LOCKEDが「鍵のかかった行を待たずに諦める」仕組みを、
    第17章のFOR UPDATE(待つ方)との対比で説明できる
  - 対象を抽出してから書くまでの間の競合を、条件付きUPDATEの再検査がどう裁くか追える
  - expiredイベント・latch_status_events・attendance通知がすべて同一トランザクションで
    書かれる理由を説明できる
  - 上位の提案が閉じた瞬間に保留行が昇格する経路(クローズ検知drain)を追える
  - JST 0時のリセットジョブの実体が「掃除とdrain」であり、機能的なリセットは
    日付キー切替がすでに果たしていることを説明できる
- 対応コード: `backend/src/latch/worker/sweeper.py`(新設)・`worker/reset.py`(新設)・
  `worker/reeval.py`(sweeper注入)・`worker/matching/latch_engine.py`(drainのpublic化)・
  `worker/cost/store.py`(掃除メソッド)・`worker/main.py`(配線)・
  `settings.py`(`sweeper_batch_limit`・`reset_retry_sec` の追加)
- 設計の根拠: `docs/plans/M3/ws-2-design.md`(特に§2.1のスケジューラ統合・§2.2〜2.4の
  SQLと直列化・§2.5〜2.7のリセットジョブとdrain)と `docs/plans/M3/ws-2-report.md`。
  本文の「NN §X」は `docs/NN-*.md` の第X節を指します(06 §6=期限切れバッチ・
  06 §9=catch-up・06 §10=保留キュー再評価・04 §5=リセットジョブ・05 §6=遷移表・
  05 §3=idx_intents_expires・09 D-09=実施自己申告)
- 次に読むもの: `labs/lab6-expiry.md`(Lab 6)。この章で読んだバッチを、自分の手で
  起こして観察します

## 18.1 誰も押さないボタンを、時間が押す

第17章の終わりに、こんな予告をして終わりました。返事の期限が本当に切れたら
statusをexpiredへ進める掃除、成立した提案の対象時刻が過ぎたらcompletedへ進める
バッチ——これらは後続の章で読む、と。2026-10-01にマージされたM3 ws-2がそれです。
この章では、その「時間の管理」の中身を読みます。

まず、何が難しいのかを問題として置きます。第17章の応答系は、ユーザーが
`[参加する]` を押すから動きました。リクエストが来て、経路が走って、行が変わる。
APIとはそういうもので、**誰かが何かを要求したときだけ動く**仕組みです。

ところが、閉じるべき状態のすべてが、誰かの要求で閉じられるわけではありません。

- 提案が出たのに、誰も返事をしない。回答期限が過ぎる
- 保留キューに残っていた候補が、期限切れを迎える
- 下書きのまま(activeにされないまま)期限の切れたIntentがある
- 成立(matched)した提案で、いよいよ会の対象時刻が過ぎる

これらに共通するのは、**きっかけとなるユーザーの操作が存在しない**ことです。
期限が切れる瞬間に、DBは何も通知してくれません。時間が過ぎたという出来事は、
こちらから確かめに行かない限り、どこにも現れないのです。

そこでLATCHは、**バッチ(batch=まとめて処理する仕分け作業)** と呼ばれる形の
プログラムでこれに答えます。一定の周期で自分から起きて、DBを自分の目で確かめ、
条件に当てはまる行の状態を進める。ユーザーの要求を待たない、定期の巡回です。
具体的には `backend/src/latch/worker/sweeper.py` の **ExpirySweeper** が、
60秒に1回、次の4つの処理をこの順で実行します。

| # | 処理 | 対象 | 行き先 |
|---|---|---|---|
| 1 | latches期限切れ | 返事待ち(proposed/partial_accept)で回答期限かIntent期限が過ぎた行、保留(candidate)で期限が過ぎた行 | expired |
| 2 | Intent期限切れ | draft/active/pausedで `expires_at` が過ぎた行 | expired |
| 3 | 完了遷移 | matchedで、対象時刻(参加Intentのtime_startの最大値)が過ぎた行 | completed |
| 4 | クローズ検知drain | 直近60秒に閉じ系の遷移があったか | 保留キューの再評価 |

1〜3が「時間が状態を閉じる」本体、4は閉じたことで空いた枠をすぐ詰める追加の
処理です(18.7で読みます)。この表の内容(期限が過ぎたらexpiredへ進める)は、
遷移表(05 §6)をそのまま実装しただけに見えます。実際、SQLを1本書けば済むように
見えるでしょう。この章の本題は、
その単純な更新を、**同時並行で動く他の経路と矛盾なく**行うための設計です。
道具は第17章で使い込んだ2つ、FOR UPDATEと条件付きUPDATEです。バッチは、あの
道具立てを「誰の操作もない場所」へ拡張したものになります。

## 18.2 60秒の定期便: sweeperはReevalRunnerの先頭に乗る

最初の設計判断は、sweeperを「どこで、どの周期で動かすか」です。

LATCHにはすでに60秒周期の定期処理があります。第14章14.9で読んだ
**ReevalRunner**(`worker/reeval.py`)で、catch-upスキャン(expires_atまで2時間以内の
active Intentを再評価へ投入)と30分Bucketの再評価を、60秒ごとの周期で回していました。
06 §6は、latches期限切れバッチとIntent期限切れバッチとcatch-upを「同一の
スケジューラで動かし、切替・停止は単一ジョブとして管理する」と定めています。

周期ジョブが独立に増えると、管理する相手が増えます。起動と停止を別々に呼び、
片方だけ止まっている状態に気づく。LATCHはそれを避けたい。そこでws-2は、
ReevalRunnerの `run_once` の**先頭**にsweeperを差し込む形を取りました。

```python
    async def run_once(self) -> int:
        """1周期分: sweeper(注入時)→catch-up抽出→Bucket処理→直接投入。

        例外は握らない(sweeper内も含む・design §2.1)。
        """
        if self._sweeper is not None:
            await self._sweeper.run_once()  # 先頭(期限切れ確定を先行)
        now = self._clock.now()
```

(`worker/reeval.py:110-117`。`self._sweeper` は省略可能な注入で、渡さなければ
従来どおりの動作です。既存の試験が壊れないようにするための工夫です)

先頭に置く意味は、処理の順序です。期限切れの確定を、重いパイプライン(catch-upは
1回に最大50件の評価を投入できます)より**先**に済ませる。こうすると、期限切れで
閉じたlatches・expiredになったIntentを、同じtickの後段の処理が正しい前提で
扱えます。Workerの組み立て `worker/main.py` では、ExpirySweeperを構築して
ReevalRunnerへ渡し、60秒系はtick 1本で動きます。

ここで、この章を通して効く約束を1つ固定します。**1tick=1時刻**。`run_once` の
冒頭で `Clock.now()` を1回だけ取り、その値を抽出・UPDATE・イベント挿入の
すべてで使います。あるtickの中で「抽出は10:00:00、書き込みは10:00:03」に
なると、抽出時点では切れていなかった期限が、書き込み時点では切れていた——
そんな際どいケースを最初から作る必要がありません。1回のtickは1つの時刻で完結する。
時刻源がClockに1点集中している(第3章)おかげで、この約束は自然に守れます。
実時間を直接参照したら、この章の内容全体が試験で確かめられなくなります。

なお、この統合には代償もあります。tickは直列なので、catch-upが重いと期限切れの
確定が最大「tick処理時間+60秒」遅れうる。設計書はこの点を明記したうえで
許容しました。**遅れても正しさは変わらない**——その理由は、次の2節で見る
SQLの形そのものの中にあります。

## 18.3 鍵のかかった行は待たない: FOR UPDATE SKIP LOCKED

第17章の回答APIは、latches行を読むとき `FOR UPDATE` を付けました。行を読みながら
「書く予定」を宣言して、他のトランザクションを待たせる。同時回答を1列に並べて、
二重回答や期限直後の回答を排除するためには、**待つ価値**がありました。

バッチは逆です。**鍵のかかった行は、待たずに諦めます**。そのためのSQL句が
`SKIP LOCKED` です。対象の抽出はこう書かれています。

```python
_SELECT_EXPIRING_LATCHES = text("""
    SELECT id, status FROM latches
    WHERE (status IN ('proposed', 'partial_accept')
           AND (response_deadline <= CAST(:now AS timestamptz)
                OR expires_at <= CAST(:now AS timestamptz)))
       OR (status = 'candidate' AND expires_at <= CAST(:now AS timestamptz))
    ORDER BY response_deadline, expires_at, id
    LIMIT :batch_limit
    FOR UPDATE SKIP LOCKED
""")
```

(`worker/sweeper.py:35-44`)

WHERE句は06 §6の対象条件そのものです。読み方は次のとおり。

- `proposed` か `partial_accept`(返事待ちの提案)は、**回答期限** (`response_deadline`)
  か**Intentの寿命** (`expires_at`) のどちらかが過ぎていれば期限切れ。第17章で
  見たとおり、返事は回答期限までに限られ、Intent自体の寿命はそれより先に来る
  こともあります
- `candidate`(保留キュー。第14章14.5)は `expires_at` だけを見ます。候補行の
  `response_deadline` は提示時に計算する**暫定値**で、まだ誰にも提示していない
  状態の期限で判定するのは誤りだからです(05 §6)
- 保留キューにはscoreが閾値に届かないnearby候補の行も含まれます。期限で
  閉じるのが正しいので、scoreの条件はありません

末尾の `LIMIT :batch_limit` は、1回の処理で拾う行数の上限です(設定は
`settings.py` の `sweeper_batch_limit`。既定50)。期限切れが1度に大量に積れても、
残りは次の周期が拾うので、区切って処理します。

続いて `FOR UPDATE SKIP LOCKED` の部分を見ます。`FOR UPDATE` は第17章と同じ「行と一緒に
鍵を取る」構文です。違いは `SKIP LOCKED` が付いたことで、**鍵が既に誰かに
持たれている行は、待たずに結果から除く**とPostgreSQLに伝わります。回答APIが
ある行のトランザクションを進行中なら、その行は今回の抽出に入ってこない。
sweeperは待たずに、次の行へ進みます。

なぜ諦めてよいのか。答えは「**また来るから**」です。回答APIの待ちは、その場で
正しい答えを返すために必要な待ちでした。バッチは60秒後にまた同じ確かめを
します。今回鍵が取れなかった行は、次のtickで誰も鍵を持っていなければ普通に
処理される。数分の遅れと引き換えに、バッチが他の処理をブロックしない形が
手に入ります(逆向きのブロック、つまり重い処理のせいでバッチが待たされる
こともありません)。

この性質には、もう1つ先の見方があります。将来Workerを複数プロセスに増やした
とき、同じsweeperが並行に走っても、鍵を取られた行は他のsweeperから見えなくなる
ので、同じ行を二重に処理しません。ws-2の試験範囲はWorker 1構成ですが、
SKIP LOCKEDはその日のために効く保険にもなっています(design §1.4)。

## 18.4 抽出してから書くまでの隙間を、条件付きUPDATEが裁く

18.3の抽出SQLは、実は**抽出専用の短いトランザクション**で走って、すぐコミット
されます(`_select_expiring_latches`。`engine.begin()` のブロックを抜けた時点で
鍵は全部返却されます)。そのあと、sweeperは行の数だけ**別々の**トランザクションを
1つずつ開いて、書き込みを行います。抽出と書き込みの間、鍵を持ち続けません。

なぜこんな回りくどいことをするのか。抽出した瞬間からUPDATEするまでの間に、
**他の経路がその行を変えうる**からです。抽出の時点で「回答期限が切れている」
ように見えた行も、次の瞬間には、期限切れに気づいたユーザーの回答APIが
その行を掴んでいるかもしれません。行単位のトランザクションは、1行の予期しない
失敗が他の行へ波及しないための分割でもあります。失敗した行は、次のtickで
もう一度抽出されるだけです(例外を握らない規律は第14章のReevalRunnerと同じで、
周期ループの側が握って次周期で回収します)。

では、抽出と実行の間に行が変わっていたら、どうなるのでしょう。ここで第17章の
条件付きUPDATEが再登場します。書き込みのSQLはこうです。

```python
_EXPIRE_RESPONSE_LATCH = text("""
    UPDATE latches SET status = 'expired'
    WHERE id = CAST(:latch_id AS uuid)
      AND status IN ('proposed', 'partial_accept')
      AND (response_deadline <= CAST(:now AS timestamptz)
           OR expires_at <= CAST(:now AS timestamptz))
    RETURNING id
""")
```

(`worker/sweeper.py:47-54`)

WHERE句を見てください。**id指定に加えて、statusと期限の条件をもう一度書いて
います**。抽出SQLの条件と同じ内容です。これは、UPDATEを実行するその瞬間に、
「この行はいまも期限切れの返事待ちか」をDB自身に確かめさせる検品です。
回答APIが先に動いて行のstatusを変えていたら、このUPDATEは**影響行数0**を
返します。第17章で読んだ「影響行数0=前提が古かったサイン」です。sweeperは
影響行数が0なら、イベントを書かず、何もなかったように次の行へ進みます。

逆に、sweeperが先で回答APIが後なら、回答APIのFOR UPDATEがsweeperの終わった
行を待ってから読み、期限の条件を自分のWHEREで検品して409 LATCH_EXPIREDを
返します。つまりどちらが先でも、矛盾は起きません(06 §6)。

ここから、18.2で先送りにした問いの答えが出ます。**sweeperが遅れても、期限の
切れた行への回答は受理されません**。回答APIは自分のWHERE句で期限を自分で
確かめるからです。期限の真実は行そのもの(response_deadline・expires_at・status)に
あり、sweeperは行を「期限切れの形」に整える係でしかない。この分担があるから、
sweeperの遅延は期限表示の鮮度の問題であって、正しさの問題にはならないのです。

影響行数が1だったとき——つまり自分がこの行をexpiredに確定させたとき——
同じトランザクションの中で、遷移の台帳へ1行書きます。

```python
            await conn.execute(
                _INSERT_LATCH_EVENT_SQL,
                {
                    "latch_id": latch_id,
                    "from_status": from_status,
                    "to_status": "expired",
                    "user_id": None,
                    "now": now,
                },
            )
```

(`worker/sweeper.py:256-265` の抜粋。`latch_status_events` への挿入は第17章の
回答APIと同じSQLです)

`user_id: None` に注目してください。第17章で読んだlatch_status_eventsは、遷移を
起こした本人を `user_id` に記録する台帳でした。バッチはシステム起因の遷移なので、
**user_idはNULL**。台帳の形はユーザー起因もシステム起因も同じ1行で、起こした
主体だけが違う。この約束が18.7の種になります。

## 18.5 Intentの期限切れ: イベントを同じトランザクションに残す

2番目の処理は、Intentそのものの期限切れです。第6章の遷移表(05 §6)では、
active/pausedなIntentは `expires_at` の経過でexpiredへ、と定められていました。
第7章では、期限の切れた行がこのバッチで掃かれる**までのわずかな間**はDB上で
activeのまま残る、という前提で計上の条件を工夫したのでした。ここに、その掃く
実体が入ります。

抽出のSQLはこうです。

```python
_SELECT_EXPIRING_INTENTS = text("""
    SELECT id, status FROM intents
    WHERE status IN ('draft', 'active', 'paused')
      AND expires_at <= CAST(:now AS timestamptz)
    ORDER BY expires_at, id
    LIMIT :batch_limit
    FOR UPDATE SKIP LOCKED
""")
```

(`worker/sweeper.py:66-73`)

対象が `draft` も含んでいる点を、1度立ち止まって確認します。draft(下書き)は
まだマッチングにも出ていない、Eventを1回も発行していない状態です。それでも
期限切れの対象にするのは、05 §6が「active化されないまま期限が切れた下書き」も
expiredとして扱うと定めるからです。`paused`(一時停止中)も同様に対象です。
resume Eventとの競合が起きても、UPDATEのstatus検査(WHERE句)が二重遷移を防ぎ、
第9章のversion検査が古い方のイベントを破棄します。latchesのときと同じ考え方です。

WHERE句の `status IN ('draft','active','paused') AND expires_at <= ...` という形には、
もう1つ機能があります。**部分索引(partial index)** を効かせるための形です。
`idx_intents_expires` は、intentsの全行ではなく「この3つのstatusの行だけ」を
集めた索引として作られています(05 §3)。索引の対象条件と抽出のWHERE句を同じ形に
揃えると、PostgreSQLはこの索引だけを見て対象を見つけられます。expiredや削除済みの
過去の行がどれほど増えても、期限切れの探査は「まだ生きている行」だけの規模で
済む。バッチは60秒ごとに走るので、この効率は割と真面目に効きます。

書き込み側もlatchesと同じ構造です。条件付きUPDATE(`_EXPIRE_INTENT`。
`sweeper.py:75-81`)がstatusと期限を再検査し、`RETURNING version` で現在のversionを
受け取ります。影響行数1のとき、同じトランザクションでexpiredイベントを1行、
outbox(match_events)へ書きます。

```python
            await insert_match_event(
                conn,
                event_type=EVENT_EXPIRED,
                intent_id=intent_id,
                version=int(row[0]),
                now=now,
            )
```

(`worker/sweeper.py:283-289`)

イベントをoutboxへ書くのは第9章と同じ慣行です。保存と同じトランザクションで
書けば、「状態は変わったのにイベントが残らない」すき間が構造的に生じません。
event_typeの値は6種類と決まっていて、`EVENT_EXPIRED` はその1つとしてM1の頃から
定義されていて、「発行経路はM3-3」というコメント付きで予約されていました。
ws-2がその予約を果たした形です。

おもしろいのは、このイベントを読む側の仕草です。第9章のStage1(workerの第1段
処理)は、expiredイベントを**「処理実体なし(processed)」として受理します**。
削除イベントのときはクローズ処理という波及がありました(第17章17.9)。expiredには
ない。なぜなら、波及のすべてはsweeper自身が済ませているからです。latchesは
18.3の処理が閉じ、候補表(match_candidates)は明示的に閉じない代わりに、次の
Layer 1の抽出条件(`status='active'`)と第13章のH再検証で自然に無力化されます。
イベントの役割は、指令ではなく**記録**です。いつ・どのIntentが・どのversionで
期限切れになったかの監査と、将来の scheduled 経路との対称性のために残ります。

## 18.6 対象時刻が過ぎたら完了: matched→completedと実施自己申告の通知

3番目の処理は、時間が **状態を前へ** 進める例です。第17章で成立(matched)した
提案は、会の約束として実在しています。その会の対象時刻が過ぎたら、行は
**completed(完了)** へ進みます。

対象時刻の定義から。複数のIntentからできたLATCHでは、参加Intentのtime_startの
**最大値**(いちばん遅い開始時刻)が、会の対象時刻です(05 §2)。抽出SQLは
これをサブクエリで計算します。

```python
_SELECT_COMPLETION_TARGETS = text("""
    SELECT l.id FROM latches l
    WHERE l.status = 'matched'
      AND (SELECT max(i.time_start) FROM intents i
           WHERE i.id = ANY(l.intent_ids)) <= CAST(:now AS timestamptz)
    ORDER BY (SELECT max(i.time_start) FROM intents i
              WHERE i.id = ANY(l.intent_ids)), l.id
    LIMIT :batch_limit
    FOR UPDATE SKIP LOCKED
""")
```

(`worker/sweeper.py:84-93`)

`ANY(l.intent_ids)` は「配列の中のどれか」で、参加Intentの集合を指します
(第15章の `&&`(配列の交差)と同じ、PostgreSQLの配列を扱う演算子の仲間です)。
条件付きUPDATE
(`_COMPLETE_LATCH`。`sweeper.py:96-100`)はstatus='matched'を再検査して
`completed_at` に時刻を刻みます。この列は、latchesの初期定義(マイグレーション
0001)の時点から用意されていました。**使う日を待っていた列**です。

cancelled(解散済み・第17章17.9)がこの処理の対象にならないことも、読んで
ください。対象抽出は `status = 'matched'` だけを見るので、解散してcancelledに
なった行は、構造的に外れます。09 D-09は「cancelled LATCHには申告通知を送らない」
と定めていますが、これはif文での除外ではありません。**状態機械の設計として、
はじめから対象に存在しない**のです。条件で除外するコードは、条件を書き忘れた
ときに間違いになります。そもそも対象に存在しないものは、書き忘れようがありません。

completedへの遷移には、もう1つ仕事がついて回ります。**実施自己申告**の通知です。
09 D-09: 会が終わったあと、参加者に「実際に会いましたか?」を1問だけ尋ねる。
回答期限は3日、スキップ可。この回答は集まったとき、将来の提案の精度(第17章の
Calibrationのような検証)に使われます。

sweeperは、completed遷移と同じトランザクションで、参加者全員に通知を1行ずつ
書きます。

```python
            for (user_id,) in users.fetchall():
                await conn.execute(
                    _INSERT_ATTENDANCE_NOTIFICATION,
                    {
                        "user_id": _coerce_uuid(user_id),
                        "type": NOTIFICATION_ATTENDANCE_REQUEST,
                        "payload": json.dumps({"latch_id": str(latch_id)}),
                        "now": now,
                    },
                )
```

(`worker/sweeper.py:320-329`。宛先は参加Intentのuser_id全員)

notificationsテーブルには、`type='attendance_request'` の行が、payloadに
latch_idだけを入れて残ります。第14章の提案通知(proposal)と同じ書き方です。
これは**通知の先行書き込み**と呼ばれる慣行です。通知の「送信」(スマホへの
プッシュやアプリ内お知らせ)は、まだ実装されていません(次の単位ws-3の題材)。
その代わり、送るべき事実(誰に・何を・いつ)を先にDBへ書いておく。送信の実装が
後から来ても、この記録を読めば、取りこぼさず送れます。遷移の瞬間と記録の瞬間を
同じトランザクションに閉じ込めているので、「完了したのに申告が残らない」
すき間もありません。

## 18.7 閉じた瞬間に枠が空く: クローズ検知drain

4番目の処理は、sweeper自身の書いたものを、sweeperが読みに行く話です。

第14章14.5の保留キューを思い出してください。D-08の同時3件上限に当たって
candidateのまま留まった行は、上位の提案が閉じれば(rejected・expired・cancelled・
matched)、枠が空いて昇格できるようになります。その昇格を実行するのがdrain
(保留キューを提示順に回す処理)でした。ただし第14章の時点では、drainは
**評価経路の末尾**でだけ呼ばれていました。評価(Event)が来るたびには回るが、
提案が閉じただけでは誰も回してくれない。閉じた瞬間に空いた枠を、次の評価が
来るまで待つ必要がありました。

そこで、閉じたことを誰が知るかが問題になります。答えは、**もう書かれている**
でした。latch_status_eventsです。第17章で見たとおり、latchesを閉じるすべての
経路(回答API・競合クローズ・削除Event処理・18.3のsweeper)が、遷移を同じ
トランザクションでこの台帳へ記録します。閉じに漏れる経路がそもそもない。
観測点を新設する必要はなく、**台帳の読み手**を1人足すだけで済むのです。

sweeperの `run_once` は、1〜3の書き込みを終えたあと、これを確かめます。

```python
_SELECT_RECENT_CLOSE = text("""
    SELECT 1 FROM latch_status_events
    WHERE created_at > CAST(:last_tick AS timestamptz)
      AND to_status IN ('rejected', 'expired', 'cancelled', 'matched')
    LIMIT 1
""")
```

(`worker/sweeper.py:123-128`)

「前回のtick以降に、閉じ系の遷移(rejected/expired/cancelled/matched)が
1行でもあるか」を調べる、1行存在検査です(第17章のEXISTSと同じ目的の書き方)。
あれば、保留キューをdrainします。`last_tick` はsweeperの起動時刻から始まって、
毎tickの終わりに更新されます。メモリに持つだけでよく、DBに保存しません——
起動前に閉じた分は、既存の評価経路がもう回しているからです。

注目すべきは、**sweeper自身が書いたexpired行も、この検査に引っかかる**ことです。
18.3の期限切れ処理がlatch_status_eventsへexpiredを書き、この節の検査がそれを
観測し、同じtickのdrainが、空いた枠へ保留行を昇格させる。書き込み→観測→drain
という順序を1つのtickの中に並べることで、「期限切れで閉じたために空いた枠」が
次のtickまで待たずに埋まります。第17章の最後に予告した「観測点は、もう書かれて
います」の消費側が、これです。

実はこの仕組みには、実装中に興味深いできごとがありました。このdrainの効きを
確かめる統合試験のひとつ(test_expiry_batches.pyの試験2)が、agent3の手元では
「保留行が同tickで昇格した」と観察されたのに、supervisorの環境では昇格しない——
という不一致を起こしたのです。原因を掘ると、観察は本物ではありませんでした。
手元の環境では常設のworker(本物の時計で動く)が裏でsweeperを回しており、
そちらがクローズを観測してdrainしていた。一方、試験コードの中のFakeClockは
時間を進めない固定の時刻だったので、`created_at > last_tick` が成立せず、
試験内のsweeperのdrainは走らなかった。どちらも正しい動きで、**時計が誰の
ものであるか**だけが違っていた。期待値は「試験内のsweeperは昇格させない」に
修正され、同tick昇格の実証は本物の時計で動く別の試験が担いました(ws-2報告書)。
第3章で「時刻源をClockに1点集中させると、テストで時間を支配できる」と学んだ
はずの内容が、観察する側の時計(本番workerとFakeClock)を混ぜると破綻する、
という実例です。試験を書くとき、環境の時計と試験の時計がどちらで動いているかは、
常に意識する価値があります。

## 18.8 もう一つの時間: JST 0時のリセットジョブ

この章の主人公は「期限」という時間でしたが、LATCHにはもう1つ、扱い方の違う
時間があります。**日付の切り替わり**、つまりJSTの0時です。

第7章と第12章で、LATCHの各種カウンタのリセットを学びました。レート制限の
20件/日も、Jevの費用上限30,000回/日も、**カウンタを消す処理は存在しない**。
鍵の名前に日付を埋め込み(`rl:create:{user_id}:{yyyymmdd}` や
`jev:daily:{yyyymmdd}`)、日が変われば別の鍵をINCRする。0時を跨いだ瞬間に、
新しい日は0から始まる。TTLは掃除用であって、リセットの表現には関与しない。
この方式の強みは、リセットジョブがいなくても正しいことでした。

では、04 §5のいう「リセットジョブ」は何のためにあるのでしょう。ws-2はこれを
`worker/reset.py` の **ResetJob** として実装しました。答えから言うと、ジョブの
仕事は**掃除と、溜まった仕事の回収**であって、リセットではありません。

コードは短いので、本体をそのまま読みます。

```python
    async def run_once(self) -> None:
        """0時発火の本体: 前日キー掃除(月初は前月月次)+drain(design §2.5)。

        DELは冪等(キー不在=0削除)。失敗した場合は例外を握らずrun()へ
        伝播する(runがretry_secで再試行 — 翌0時まで放置しない)。
        """
        jst_today = self._clock.jst_date()
        prev_day = (jst_today - timedelta(days=1)).strftime("%Y%m%d")
        await self._cost_store.delete_daily(prev_day)
        await self._cost_store.scan_delete(f"jev:exec:{prev_day}:*")
        await self._cost_store.scan_delete(f"jev:intent:*:{prev_day}")
        await self._cost_store.scan_delete(f"jev:user:*:{prev_day}")
        if jst_today.day == 1:
            await self._cost_store.delete_monthly(_prev_month_key(jst_today))
        await self._latch.drain()  # 完了時に保留キュー再評価(引用#11・§2.6)
```

(`worker/reset.py:63-77`)

前半が掃除です。前日の日付キーをDELし(`delete_daily`)、日の入った内訳キーを
SCANで探して消します(`scan_delete`。`worker/cost/store.py` に追加された
メソッドです)。月初(`jst_today.day == 1`)なら、前月の月次キーも消します。
掃除の意味は、TTLを待たずに旧キーを即時解放することです。日次キーのTTLは
48時間なので放っておけば2日で消えますが、月次キーのTTLは45日。600,000回の
上限が刻まれた鍵が1か月半もRedisに居座るのは無駄で、これを避ける意味が
いちばん大きい。DELは冪等(キーがなければ0個消す)なので、2回実行しても
安全です。日付は `Clock.jst_date()` から導くので、テストではFakeClockで
月末や月初を自在に再現できます。

後半の `await self._latch.drain()` が、06 §10の「0時リセット完了時に保留キュー
再評価」の実体です。ここにも意味があります。D-08の**日次**上限(1ユーザー
1日6件の通知)は、notificationsの当日分カウントで判定されます。0時を跨げば
カウントは0に戻る。つまり0時の直後は、昨日「今日はもう6件出した」ために保留
されていた候補が、昇格できる状態になっているはずです。それを、次の評価経路の
たまたまのタイミングまで待たせるのはもったいない。日付の切り替わりを知って
いる者(このジョブ)が、直後に1回drainを回すのが合理的です。

06 §10はこれを「再評価イベントの発行」と呼んでいますが、実装は**イベントを
発行しない**直接実行です。理由は、第14章のcatch-upスキャンと同じです。
match_eventsのidempotencyキーは(event_type, source_intent_id, version)なので、
「保留キュー全体を再評価せよ」というキュー全体への指令を、個別Intentの
イベントとして表現できません。同じversionで同じイベント種を発行すると
idempotencyキーが衝突します。だから、drainという関数を直接呼びます。
仕様の意図(溜まった昇格を回収する)は、イベントという道具でなくても達成
できる、という判断です(design §2.6)。

ここでdrainが呼べるようになっていること自体も、この単位の変更です。第14章の
時点では `LatchEngine._drain()` はアンダースコア付きの、クラス内部の処理で
した。ws-2で `drain()` としてpublicな名前に変えました(`latch_engine.py:812`)。
呼び手が増えた(評価経路の末尾・sweeperのクローズ検知・リセットジョブ)ので、
クラスの内側だけの処理から、複数の経路から呼ばれる公開の入口に育ったのです。中身は
無変更で、提示順にtry_promoteを回すだけ。D-08上限の検査はtry_promoteの行単位に
入っているので、枠が空いていない候補は、drainされてもまた保留に戻るだけです。

最後に、このジョブの待ち方を見ておきます。

```python
def next_jst_midnight(now: datetime) -> datetime:
    """now(tz-aware UTC)より後の直近のJST 0時をUTC表現で返す(design §2.5)。

    nowがJST 0時丁度のときは翌日0時(待機0秒を生まない)。
    月末・年跨ぎはdateの+1日演算が処理する。
    """
```

(`worker/reset.py:25-30`。本体はJSTへ変換して+1日の0時を作り、UTCへ戻す)

60秒系のsleep-first周期とは違い、**次のJST 0時まで一括して待ちます**。
月末(31日23:59→1日0時)も年跨ぎ(12月31日→1月1日)も、dateの+1日演算がそのまま
処理します。Workerの中では、backfill・ReevalRunnerに並ぶ3つ目の定期taskとして
起動されます(`worker/main.py`。失敗時は300秒後(`reset_retry_sec`)
に再試行し、翌0時まで放置しません)。ただし失敗の実害は小さい、というのが
このジョブの性格です。カウンタのリセットは日付キー切替がすでに担っていて、
ジョブが止まっても上限は0時できちんと復帰する。drainも、評価経路がいずれ
代替します。掃除と回収の遅れは、衛生と鮮度の問題であって、正しさの問題では
ない。18.2から18.8まで、この章に通っているのは同じ原理です。**正しさは
DBと日付キーとClockが担い、周期ジョブはその結果を整える**。

## 18.9 自分で確かめる

この章の内容は、unit試験・integration試験・自分の手の3層で確かめられます。

### 演習1: unit試験の名前を読む

```bash
cd backend
uv run pytest tests/unit/worker/test_sweeper.py tests/unit/worker/test_reset_job.py -v
```

sweeperが20件・reset_jobが11件、合計31件が数秒で走ります。試験名を読むだけでも
内容が追えます。`test_select_expiring_latches_pins_conditions` のような「SQLピン」
と呼ばれる型の試験(対象条件のSQL文字列を規定値に固定して確かめる・第11章11.9)が、
18.3・18.5のWHERE句の形をそのまま検査しています。
`test_next_jst_midnight_*` の並び(0時直前・0時丁度・月末・年跨ぎ)は18.8の導出の
境界です。

### 演習2: 2つのFOR UPDATEを並べて読む

```bash
rg -n "FOR UPDATE" backend/src/latch/
```

`FOR UPDATE` 自体は、回答API(`latches/store.py`)・削除Event処理(`worker/stage1.py`)・
users行の直列化(`intents/store.py`)など、待つことを選んだ場所のあちこちで
使われています。`SKIP LOCKED` が付くのは `worker/sweeper.py` の3か所だけ。
待つ方と諦める方。同じ句に1語追加するだけで意味が変わるところを、自分の言葉で
説明できるか試してください。

### 演習3: 自分の手で期限切れを起こす

`labs/lab6-expiry.md`(Lab 6)へどうぞ。期限を過去へ書き換えて、60秒のsweeperが
DBを閉じていくのを、ログとpsqlで観察します。この章の1〜3番の処理と、
attendance通知の行が全部見られます。

## 18.10 この章の再統合

- 状態機械の出口のうち、ユーザーの操作で閉じるものはAPIが、**時間の経過で
  閉じるものはバッチが**担う。ExpirySweeperは60秒ごとにlatches期限切れ・
  Intent期限切れ・完了遷移・クローズ検知drainの4処理をこの順で実行する
- バッチの直列化は第17章と同じ2道具。ただしFOR UPDATEにはSKIP LOCKEDが付き
  (鍵のかかった行は待たずに諦める——また60秒後に来るから)、条件付きUPDATEは
  抽出と実行の隙間の再検査になる(影響行数0は「他の経路が先」の正しい結果)
- **期限の真実は行そのもの**。回答APIは自分のWHEREで期限を検品するので、
  sweeperの遅延は正しさに影響しない。1tick=1時刻の約束(Clock)がこの設計を
  試験可能にする
- 遷移イベント・expiredイベント・attendance通知は、すべて遷移と**同一の
  トランザクション**で書かれる。すき間なく、取りこぼさず。latch_status_eventsは
  user_id=NULL=システム起因で記録され、全クローズ経路が書く唯一の観測点として、
  クローズ検知drainに再利用された
- JST 0時のリセットジョブの実体は掃除とdrain。機能的なリセットは第7章・
  第12章の日付キー切替がすでに担っていて、ジョブはTTLを待たない解放と、
  日次上限の復帰直後の回収を1回行う

第17章で、latchesの行は「候補→提案→返事→成立/不成立」まで読みました。この章で
その一生の最後(期限切れ・完了)までが閉じ、閉じたことで空いた枠が次の候補へ
回される循環まで見えました。残る時間の題材は、成果の報告です。成立・不成立・
完了を本人たちに伝える通知の実体(読み取り・送信)が、次の単位ws-3で加わります。
その入口は、この章でnotificationsに先行書き込みした行です。

## 18.11 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| バッチ | ユーザーの要求を待たず、周期で自分から起きてDBを処理するプログラム |
| ExpirySweeper | 期限切れ・完了・クローズ検知の4処理を60秒ごとに実行するクラス(worker/sweeper.py) |
| tick | 周期処理の1回分。「1tick=1時刻」は1回の処理でClock.now()を1回だけ使う約束 |
| SKIP LOCKED | FOR UPDATEのオプション。鍵のかかった行を待たずに結果から除くSQL句 |
| 影響行数0 | UPDATEが1行も更新しなかったこと。抽出と実行の間に行が変わった正しいサイン(第17章から再登場) |
| 部分索引 | テーブルの一部の行(ここではdraft/active/paused)だけを集めた索引。抽出条件と形を揃えると効く |
| expiredイベント | Intentの期限切れをmatch_eventsへ記録するイベント。Stage1は処理実体なしで受理する |
| completed / completed_at | 対象時刻経過でmatchedが進む完了状態と、その時刻を刻む列 |
| 実施自己申告 (D-09) | 完了後の参加者への「実際に会いましたか?」の1問。回答期限3日・スキップ可 |
| attendance_request | 実施自己申告の通知のtype値。payload={latch_id}の最小参照で先行書き込みされる |
| 先行書き込み | 通知の送信実装に先立って、送るべき事実をnotificationsへ書いておく慣行 |
| クローズ検知drain | latch_status_eventsに閉じ系遷移があったら保留キューを回す、sweeperの第4処理 |
| last_tick | クローズ検知の観測窓の起点。sweeperのメモリ上で毎tick更新される |
| ResetJob | 次のJST 0時まで待機し、旧カウンタキーの掃除とdrainを行う独立task(worker/reset.py) |
| next_jst_midnight | 現在時刻より後の直近のJST 0時を計算する関数。月末・年跨ぎはdateの演算が処理する |
| drain(公開) | 保留キューを提示順にtry_promoteで回す処理。ws-2で内部処理から公開入口へ育った |

## 18.12 確認問題

1. 回答APIのFOR UPDATEとsweeperのFOR UPDATE SKIP LOCKEDは、同じ行の鍵を
   取り合ったとき、それぞれどうなりますか。2つの順序(回答が先・sweeperが先)を
   仮定して、最終的な行の状態とそれぞれの応答・結果を説明してください
2. sweeperの1tickが重くて期限切れの確定が2分遅れたとき、その間に来た
   「期限切れ直後の提案への回答」は受理されますか。理由を、回答APIのWHERE句と
   紐けて説明してください
3. candidate(保留キュー)の行の期限切れ判定にresponse_deadlineを使わない理由は
   何でしたか。response_deadlineがその行にとって何であるかを含めて答えてください
4. expiredイベントを発行するのに、Stage1が「処理実体なし」で済む理由を説明
   してください。削除イベントのときにあった波及(第17章)との違いはどこに
   ありますか
5. リセットジョブが0時に一度も起動できなかった週を考えてください。
   Jevの日次上限(30,000回)と、D-08日次上限で保留された候補の昇格は、
   それぞれどうなりますか
6. 統合試験で「期限切れ後に保留行が同tickで昇格した」と観察したら、それは
   どんな可能性を疑うべきでしたか。試験内の時計と環境の時計の区別を使って
   説明してください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった
部分が、読み返すべき節です)
