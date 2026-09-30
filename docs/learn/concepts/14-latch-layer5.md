# 第14章 評価を提案に変える関所: Layer 5 LATCH Engineと提案を守る枠

- 種別: 解説(通読して理解を積む章)
- 前提知識: 第9章(Pub/Subとat-least-once・冪等性)・第11章(match_candidatesと
  ペアの正規化)・第12章(reevalガード)・第13章(JevWorker・jev_result・ガード付きUPDATE)。
  あわせて第7章(7.4の日付キー)を思い出せると完璧です
- この章を読み終えるとできるようになること:
  - Layer 4の評価結果がユーザーへの「提案」になるまでの道のりを、latches・notifications
    の2つのテーブルを軸に追える
  - latch_score = H × MutualScore × C の3つの要素がそれぞれ何を守っているか説明できる
  - 部分UNIQUE索引(0004)が「開いている提案は1組につき1行」をどう強制するか、
    ON CONFLICT DO NOTHING と昇格とともに説明できる
  - D-08(日6件・同時3件)・D-07(no/defer)・D-05回答期限式・75分ルールという
    4つの枠が、誰をどういう順で守るのか追える
  - nearby_alsoの存在通知とmutedの沈黙が、proposalの中身と通知の書き込みで
    どう使い分けられるか説明できる
  - 30分Bucketとcatch-upという再評価の2経路が、なぜEventを発行せず直接投入なのか説明できる
- 対応コード: `backend/src/latch/worker/matching/latch_calc.py`・`proposal.py`・
  `latch_engine.py`・`worker/reeval.py`・`backend/alembic/versions/0004_latches_open_unique.py`・
  `worker/main.py` のM2 ws-6追加分
- 設計の根拠: `docs/plans/M2/ws-6-design.md`(特に§2.1の配線選択・§2.3の部分UNIQUE索引・
  §2.7のnotifications一元化・§2.8の直接投入)と `docs/plans/M2/ws-6-report.md`。
  本文の「NN §X」は `docs/NN-*.md` の第X節を指します(06 §6・§9〜§10=Layer 5と再評価・
  03 D-05/D-07/D-08=期限と再提案と上限・05 §2=データモデル・08 §2.2〜§2.3=通知の表示)
- 次に読むもの: `concepts/15-group-match.md`(第15章。この章が作ったlatchesと
  try_promoteの枠を、3〜4人のグループがどう共用するかを読みます)

## 14.1 評価が終わっても、まだ誰にも何も届いていない

第13章の終わりの時点で、データベースには何が入っていましたか。match_candidatesの行に
jev_resultというJSONが入り、statusが'evaluated'になりました。7つの質問への答え——
「AはBを受け入れるか 0.9」「BはAを受け入れるか 0.85」のような確率のかたまりです。

ところが、この時点でユーザーのスマホには何も届いていません。AさんもBさんも、
自分の近くに条件の合いそうな人がいることを、まだ何も知りません。**確率が保存され
ただけで、何も起きていない**のです。保存された確率を「誰かに見せる・見送る・
保留する・期限で切る」という行為に変えるのが、この章で読むLayer 5です。

コード上の位置から入ります。Layer 4とLayer 5は、同じ場所から次々に呼ばれます。
第13章13.6で見た `_kick_jev` です。ws-7でグループ処理が前後に加わったとき、
このキックの中身は共通ヘルパー `_run_post_retrieval` へまとめられました
(4段のチェーンのうち、この章で読むのは中央の2段です。前後の2段は第15章)。

```python
    async def _run_post_retrieval(self, intent_id: uuid.UUID) -> None:
        """L1〜3後の共通チェーン(design §2.1案A)。

        GroupEngine.handle(生成)→ JevWorker(group_ctx付き)→ LatchEngine(1対1)
        → GroupEngine.finalize(集約)。各部品は未注入なら何もしない
        (ws-1/ws-5/ws-6資産の試験互換)。DB失敗は伝播し_dispatch/_on_release
        の既存except・Runnerの握りへ載る(各部のガードで冪等)。
        """
        group_ctx = None
        if self._group is not None:
            group_ctx = await self._group.handle(intent_id)
        if self._jev is not None:
            await self._jev.handle(intent_id, group_ctx)
        if self._latch is not None:
            await self._latch.handle(intent_id)
        if self._group is not None:
            await self._group.finalize(intent_id)
```

(`worker/main.py:280` から)

JevWorkerの `handle(intent_id)` が帰ってきたら、すぐさま `LatchEngine.handle(intent_id)`
を呼ぶ。この「直列」の選択には理由があります。Embedding→Jev→Latchの処理を
またぐ総予算が2秒以内と決まっていて(06 §1)、イベントを1回受けてから一連の流れで
完走させたほうが予算を守りやすい。もう1つ、`handle(intent_id)` という引数の形が
JevWorkerと同じ「起点非依存」(何がきっかけでこのIntentが来たかを問わない作り)に
なっている点も重要です。この章の後半(14.9)で、Embedding完了とは別のきっかけから
同じ `handle` を呼びます。そのための準備です。

この章の本体 `LatchEngine` は `worker/matching/latch_engine.py` に約800行で書かれて
います。柱は4本です。スコアを計算し、latches行を作り、提示の枠を検査し、通知を
書く。順に読み進めます。

## 14.2 3つの数を掛け合わせて、1つの確信にする

Layer 5の最初の仕事は、Jevの答えを1つの数へまとめることです。式は仕様(01 §13)
で決まっていて、そのまま実装されています。

```text
latch_score = H × MutualScore × C
```

3つの要素を順に見ます。どれも「片方だけ良好なら提案しない」という同じ思想の
違う面です。

**H(Hard Filterの再検証)**。第11章のHard Filter——期間・場所・年齢などの前提条件が
交差するか——を、Layer 1と同じ `hard_constraint_holds` で、いま一度だけ確認します。
JevWorkerのフェーズ1が直近で同じ検査をして不成立行を閉じているのに、なぜもう一度?
Layer 5がlatches行を作る瞬間のHの確定は、Layer 5自身の責務だからです。評価してから
提案するまでのわずかな間に、相手Intentが更新されて条件が外れることもありえます。
予算を2回のSELECTで買う保険で、設計書では「belt-and-suspenders」(ベルトとサスペ
ンダー——ズボンが落ちないように2つの道具で留める)と呼ばれています。不成立なら
その評価行は `status='closed'` にして、次のペアへ進みます。

**MutualScore(相互スコア)**。jev_resultから2つの確率を取り出して、小さい方を採ります。

```python
            mutual = min(
                float(row.jev_result["would_a_accept_b"]),
                float(row.jev_result["would_b_accept_a"]),
            )
            score = latch_calc.LATCH_C * mutual
```

(`worker/matching/latch_engine.py:542` から)

「AはBを0.9で受け入れるが、BはAを0.3でしか受け入れない」ペアのMutualScoreは0.3です。
片方だけ乗り気な提案をしたら、届いた側は断るだけ。確率の低い側に全体の当落を
委ねるのがこのminの意味です。

**C(較正係数)**。現時点では1.0の定数です。`latch_calc.py` の先頭に
`LATCH_C = 1.0` と書かれています。将来的に、実際の成立率とスコアのズレを運用データで
測って調整するための係数です。設定ファイル(settings.py)ではなくコード定数です。計測を経て仕様変更として
扱うべき値で、環境ごとの差し替えも想定していないためです(ws-6設計 §2.2-3)。

計算したスコアは、match_candidates行へ「退避つき」で書き戻されます。ここが
この章でいちばん凝っているSQLです。

```python
_RECORD_SCORE = text("""
    UPDATE match_candidates
    SET prev_latch_score = latch_score,
        prev_evaluated_at = updated_at,
        latch_score = :score,
        updated_at = :now
    WHERE id = CAST(:row_id AS uuid) AND latch_score IS NULL
    RETURNING id, prev_latch_score
""")
```

(`worker/matching/latch_engine.py:69` から)

注目点が3つあります。第1に `WHERE ... AND latch_score IS NULL`。すでにスコアが
計算済みの行は更新されません。第9章で学んだat-least-once配信では同じEventが
二回来ることがあり、`_kick_jev` はduplicateでも実行されるのでした。二回目の実行で
同じペアを二度計算しない冪等ガード(何度実行しても結果が同じであることの保証)です。
第2に、`SET prev_latch_score = latch_score` という代入の順序。**右辺は代入前の値**を
指すので、古いスコアがprev_latch_scoreへ退避されてから、新しいスコアが書かれます。
初回計算ならlatch_scoreはNULLのまま退避される、つまりprev_latch_scoreはNULLに
なります。このNULLには意味があって、14.6で「prevがNULLなら無条件に変化あり」と
いう判定に使われます。ここで仕込んで、あとで回収する布石です。第3に、行数が
0だったら(UPDATEが1行も返さなかったら)他の実行が先に計算済みということで、
そのペアは静かにスキップします。

スコアが決まったら、閾値 `LATCH_THRESHOLD = 0.60`(D-01・v0.6)との比較で道が
分かれます。0.60以上なら提案の道(14.3〜14.7)。未満でも `nearby_also` を選んだ
ユーザーがいれば存在通知の道(14.8)。それ以外は、何も書かずに終わります。
(閾値は当初0.80でしたが、2026-09-30のv0.6改版で0.60へ緩和されました。
この章の他の節で「0.80を超えた」と書いてある場面は、当時の閾値での話です)

## 14.3 「開いている提案は1組につき1行」を、DB自身に守らせる

スコアが0.80を超えた。さあlatchesテーブルへ提案の行を作る……その前に、頭痛の
種を片付けます。**同じ2人への提案が2行できることを、どうやって防ぐか。**

原因は2つあります。ひとつはat-least-onceの再実行です。もうひとつは、片方の
Intentが更新されて新しい評価世代(バージョン組)で再評価されたとき、古い提案が
まだ開いているのに2本目を作ってしまう経路です。どちらも「開いている提案が
あれば、2本目は作らない」という同じルールで防げます。

方法は2通りあります。アプリで「SELECTして存在を確認してからINSERT」するか、
DBの制約に任せるか。第4章4.6で同じ選択をしました。確認してからINSERTには、
確認とINSERTの間に別の実行が割り込む隙間が残ります。複数のプロセスが同時に
動く世界では、この隙間を消す書き方はDB側にありません。そこでLATCHは、
**制約で守る**方を選びました。マイグレーション0004がそれです。

```python
def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_latches_intent_ids_open ON latches (intent_ids)"
        " WHERE status IN ('candidate', 'proposed', 'partial_accept')"
    )
```

(`backend/alembic/versions/0004_latches_open_unique.py:26` から)

UNIQUE索引なのにWHERE句がついている。これを**部分UNIQUE索引**(partial unique
index)と呼びます。「全体で一意」ではなく「この条件を満たす行の中で一意」を
強制します。条件は `status IN ('candidate', 'proposed', 'partial_accept')`——
つまり**まだ開いている提案に限って、同じintent_idsの行は1行しか許さない**。
閉じた行(expired・rejected・cancelled・matched・completed)には効きません。だから、
一度断られた組み合わせでも、条件を満たせば(14.6のD-07)新しく提案を作れます。
「開いている間は1行」だけが約束で、「一生に1回」ではないのです。

intent_idsに入れるUUIDは、第11章の `normalize_pair` と同じ規律で昇順に並べて
格納します(a < b)。[A, B] と [B, A] が別の行として扱われては索引が機能しない
ので、Python側で `sorted` してから配列を作ります。

索引があると、INSERTはこう書けます。

```python
_INSERT_LATCH = text("""
    INSERT INTO latches
        (intent_ids, proposal, score, status, response_deadline, expires_at, created_at)
    VALUES (ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[],
            CAST(:proposal AS jsonb), :score, 'candidate', :deadline, :expires, :now)
    ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed', 'partial_accept')
    DO NOTHING
    RETURNING id
""")
```

(`worker/matching/latch_engine.py:96` から)

`ON CONFLICT ... DO NOTHING` は「同じ値が既にあったら、何もせず黙って成功する」
というSQLの書き方です。INSERTが跳ねられたかどうかは、RETURNINGの有無(行が
返ってくるか)で分かります。跳ねられたときの処理は2分岐です。

- 既存の開いている行が **candidate** なら→**昇格**の判定へ。閾値未満の評価で
  作った「様子見の行」は、後の再評価で閾値を超えたとき、同じ行のscoreと
  proposalを更新して提案に格上げされます(14.8でnearby_alsoと一緒に
  また出てきます)
- 既存の行が **proposed / partial_accept**(片側が参加を答えた状態。返事の受付は
  第17章の応答系が受け持ちます)なら→何もしない。開いている提案がある
  のに2本目は作らないというルールどおりで、ログだけ残します

行が作れたら(跳ねられなかったら)、latchesの行と同じトランザクションで
latch_status_eventsへ「NULL → candidate」の1行を書きます。このテーブルは提案の
状態遷移の履歴を記録する台帳で、user_idは遷移の引き金となったユーザーですが、
今回はシステム(Layer 5)が動かしたのでNULLです。誰がいつどの提案を動かしたかは、
ここに全部残る設計になっています(05 §2)。

## 14.4 届ける中身は、見せてもよい最小限だけ

latches行のproposal列には、提案カードの中身がJSONで入ります。第13章の7つの質問が
確率の塊だったのに対して、こちらはユーザーに「見せてもよい」と判断された情報だけの
塊です。フィールドは7つに決まっています(05 §2)。

| フィールド | 中身 | 作り方 |
|---|---|---|
| time_summary | 対象時刻 | 2人のtime_startのうち遅い方(max)をJSTの `YYYY-MM-DD HH:MM` へ |
| area_name | 地域名 | 2人のgeo_centerの中点を約1kmグリッドの代表点へ丸め、地物名へ逆変換 |
| headcount | 人数 | 2(1対1のため。グループでは集合の人数3〜4。第15章15.6) |
| category_primary | 大分類 | Layer 1の完全一致条件により両者で同じ値 |
| category_secondary | 詳分類 | 起点Intent(評価の「種」)側の値 |
| budget | 予算 | 2人のbudget_maxの最小値。NULLは無視、両方NULLならnull |
| match_level | 一致度 | スコアから3段階(high/medium/low)へ変換 |

ここには入らないものが明示されています。raw_text(ユーザーが書いた生の文章)・
soft条件やNG条件の文言・座標です(08 §2.3)。特に座標は、「2人の居場所の中点」が
そのまま分かってしまう情報なので、proposalには絶対に入れません。area_nameは
geo/service.pyの `reverse_geocode` が約1kmグリッドへ丸めたうえでの地物名を
返すので、座標を経由せず地名だけが残ります。

「通知の文章」自体もここには入りません。notificationsテーブルへ書くのは
`{"latch_id": "..."}` という行の参照だけ(14.5)。文章はM3で別の単位が
latches.proposalから組み立てます。なぜこうするのか。通知文の文言は運用しながら
変えたくなるものです。文言をDBに保存してしまうと、A/Bテストで文言を変えるたびに
構造が引きずられます。構造(proposal)と文言(組み立て)を分けることで、文言変更が
DBに影響しない——保存するのは中身の構造だけで、読む側がそのときどきの文言を
組み立てる、という分担です。

重要な分岐がもう1つ。visibility(Intent保存時に選ぶ公開設定)です。通常の値
`summary_only` はサマリ(時刻・場所・カテゴリの要約)までを相手に見せます。もう
1つの値 `hidden_until_match` を選んだユーザーは、マッチが成立するまで自分の詳細を
相手に見せません。この設定がproposalに反映されます。

```python
    if origin.visibility != "summary_only" or peer.visibility != "summary_only":
        return {"headcount": 2, "match_level": match_level(score)}
```

(`worker/matching/proposal.py:62` から)

2人のどちらかでもhidden_until_matchなら、proposalは `headcount` と `match_level`
の2フィールドだけになります。time_summaryもarea_nameもカテゴリも予算も入りません。
「より厳しい方に合わせる」ルールで、片方が公開でもう片方が非公開なら非公開側の
尺度で作られます。「いつ・どこで・何を」が全部消えた提案は、それでも意味を持ち
ます。開いている提案の行があることで、どちらかが「参加する」と答えれば
マッチが成立する。詳細は成立してから、という順序です。

match_levelは `latch_calc.py` の純関数で3段階に分けます。0.90以上がhigh、
0.80以上0.90未満がmedium、提案閾値(0.60)以上0.80未満がlow。mediumの下限0.80は
**表示用の区切り**で、提案になるかどうかの閾値0.60とは別の物差しです。閾値が
0.80だった当時は偶然ぴったり重なっていたため、1つの線に見えていました(v0.6で
分離)。**スコアの生値(0.87のような数)は
どこにも出さず、3段階の区分だけを外に出す**約束です(05 §2)。ユーザーに「87%の
確率で合いそうです」と言ってしまうと、その数字の精度まで約束したことになる。
区分ならその責任を負わない、という割り切りです。

## 14.5 お知らせには1日6件・同時3件の上限がある

提案を通知してよいかどうかの検査は、`try_promote`(試験的に提示へ進める)という
部品にまとめられています。読む前に、守るべき枠を仕様から確認します。D-08
(03 D-08)の2つの上限です。

1. **日次上限**: 1ユーザーあたり、通知は1日6件まで。0時(JST)に数え直す
2. **同時進行上限**: 1つのIntentについて、同時に開いている提案(proposed)は3件まで

なぜ上限が要るのか。マッチングの精度が上がって候補がどんどん見つかると、
ユーザーには通知の洪水が届きます。1日20通の「組み合わせが見つかりました」は、
うれしさより疲れを先に届けてしまう。6件に絞って、残りは順番を待たせる——
それがD-08の狙いです。上限を超えた候補は捨てません。latches行はcandidateの
まま残して、枠が空くのを待ちます。この「溜まっているcandidate行」が保留キューです。

この「1日6件」を数える場所はどこでしょうか。第7章のレート制限と第12章の
JevカウンタはRedisのINCRでした。ところが今回は違います。**notificationsテーブル
の行を数えます**。当日の日付範囲で `type IN ('proposal', 'nearby_candidate')` の
行をCOUNTするだけです(`latch_engine.py:144` の `_COUNT_DAILY_NOTIFICATIONS`)。

カウンタを使わない理由は、ws-6設計 §2.7にはっきり書かれています。第12章のD-16
カウンタは「実行回数のブレーキ」で、呼び出す前にINCRして境界を問うことが本質
でした。一方D-08は「通知した事実の上限」で、**事実そのものはnotifications行の
存在**です。カウンタを別に持つと、カウンタの数と事実の行数が食い違う瞬間が
必ずできて、試験と監査で毎回突合しなければならない。行の存在をそのまま数える
なら、数える対象と守る対象が同一で、突合そのものが不要になります。

0時リセットも自然にできています。JSTの「今日の0時」を求める共通部品
`jst_day_start`(Layer 4が導入した関数で、ここでも再利用します)で当日の範囲を
作り、`created_at >= 今日0時 AND created_at < 明日0時`
で数えます。第7章7.4のレート制限と同じ発想です。日付が変われば範囲の指定が
変わるだけなので、「0時にカウンタを
ゼロに戻すジョブ」は不要です。

この検査を含む `try_promote` の手順を、順に追います(`latch_engine.py:746` から)。

```text
1. latches行を SELECT ... FOR UPDATE で行ロックして読む(candidateであることを確認)
2. 対象時刻まで75分を切っていたら → 通知せず破棄(candidate→expired)(14.7)
3. latches.expires_at を過ぎていたら → 対象外にする(期限切れの掃除はM3の担当)
4. 回答期限をこの瞬間の時刻で再計算する(14.7のD-05式)
5. 日次上限: 通知する側の全員が当日6件未満であること
6. 同時進行上限: 両Intentとも開いているproposedが3件未満であること
7. 条件つきUPDATEで status='proposed' へ遷移(期限も上書き)
8. latch_status_events(candidate→proposed)とnotifications(通知する人だけ)を
   同じトランザクションで書く
```

手順7の「条件つきUPDATE」は第13章13.6の再登場です。`UPDATE ... WHERE id = ... AND
status = 'candidate'` と書いておけば、行数が0のとき=他の経路が先に遷移させた
とき、何もせず終わります。誰が先に着いても結果が同じ——競合の数を数えず、
条件で負かす作法です。手順5で「通知する側」と言ったのは、muted(通知オフ)の
参加者がいるためです。この人への通知行は書きません(14.8)。
なおws-7で、グループのlatches(`group_candidate_id` が入っている行)には、手順2の
直後に「メンバーが重なる開いている集合のうち、自分より上位はないか」という
D-06の関所がひとつ加わりました(第15章15.7)。

最後に、保留キューを回す仕組み。**drain(ドレイン=溜まったものを抜く)**と呼ばれる
処理が、LatchEngine.handleの末尾で毎回走ります。

```python
_DRAIN_CANDIDATES = text("""
    SELECT l.id,
           (SELECT max(i.time_start) FROM intents i
            WHERE i.id = ANY(l.intent_ids)) AS target_time
    FROM latches l
    WHERE l.status = 'candidate'
      AND l.expires_at > CAST(:now AS timestamptz)
      AND l.score >= CAST(:threshold AS numeric)
    ORDER BY target_time ASC, l.score DESC, l.id ASC
""")
```

(`worker/matching/latch_engine.py:195` から)

並び順が提示の優先順位です。**対象時刻が近いものから先に**(明日の約束は今夜の
約束より後回し)。時刻が同じなら**スコアが高い方を先に**。この順で並べたcandidate
全行に `try_promote` を1行ずつ試します。上限に当たった行はcandidateのまま次の
機会へ、上限内に収まった行だけがproposedになります。「上限内の件数だけ処理し、残りは
次のトリガーへ」という仕様(06 §10)が、行ごとの判定という単純な形で実現されています。

なお、このWHERE句から `score >= 閾値` が外れることはありません。閾値未満の
candidate(nearby行)はdrainの対象外です。あれは「提案の待機」ではなく「存在だけ
知らせた記録」なので、昇格の道は14.3で見たとおり新しい評価経路だけです。

## 14.6 「見送り」のあとは、条件が揃うまで沈黙する

ここまでの提案は、ユーザーがまだ何も答えていない前提で話をしてきました。
この章が書かれたM2の時点では回答のAPIがまだなく(2026-09-30にM3 ws-1で実装・
第17章)、responsesは常に空でした。ですが再提案の判定(D-07・03 D-07)は、
この章で入れてしまいました。理由は簡単で、
「断られたのに何度も届く提案」は、提案システムとして一番信用を落とす失敗だから
です。枠を先に作ってしまう。

ユーザーの答えは2種類あり得ます。扱いが対称ではありません。

**no(断り)** は、そのIntent組み合わせへの拒否です。お互いのIntentの期限が切れる
まで、この2人の組み合わせは二度と提案されません。条件が変わっても、スコアが
上がっても、です。判定はlatches.responses(JSONの履歴)に `response='no'` が
1つでもあるかどうかだけ。履歴は閉じた行も含めて全件見ます。

**defer(見送り)** は、「いまは少し待ってほしい」です。断りと違って、条件が揃えば
もう一度届きます。ただし黙って再送はしません。2つの条件の**両方**を満たす必要が
あります。

1. 見送りから `min(24時間, 対象開始時刻までの残時間の半分)` が経過している
2. スコアが0.05以上変化している(または評価世代そのものが新しい)

条件1の「残時間の半分」は、対象時刻が迫るほど待ち時間を短くする工夫です。明日の
昼の約束なら最大12時間待てますが、3時間後の約束なら90分だけ。期限が近いものは
もう一度の機会を早く回す。条件2の0.05は「見せても変わらないものを見せない」
ための敷居です。そして「評価世代そのものが新しい」とは、どちらかのIntentが
更新されて新しいバージョン組で評価されたという意味。14.2の退避つきUPDATEを
覚えていますか。初回計算ではprev_latch_scoreはNULLのまま退避されるのでした。
**prevがNULLなら無条件に変化あり**——この判定のために、あのSQLはNULLを残す
作りになっていたのです。

判定は `latch_calc.py` の `d07_allows` という純関数に切り出されています。

```python
def d07_allows(
    *,
    has_no_response: bool,
    latest_defer_at: datetime | None,
    now: datetime,
    target_time: datetime,
    new_score: float,
    prev_latch_score: float | None,
) -> bool:
    """D-07再提案判定(03 D-07・06 §10・design §2.4)。True=提案してよい。

    判定順: (1)no履歴→False(2)defer履歴なし→True(3)抑制期間
    min(24時間,(対象開始時刻−now)/2)経過→True(4)抑制期間内はスコア変化判定
    (prevがNone=新評価世代なら無条件変化あり・非Noneなら|新−prev|≧0.05)。
    """
    if has_no_response:
        return False
    if latest_defer_at is None:
        return True
    suppress = min(DEFER_SUPPRESS_MAX, (target_time - now) / 2)
    if now - latest_defer_at >= suppress:
        return True
    if prev_latch_score is None:
        return True
    return abs(new_score - prev_latch_score) >= D07_DELTA
```

(`worker/matching/latch_calc.py:82` から)

判定の順番に読み味があります。no履歴があれば即False。defer履歴がなければ
(初回提案か、前回が無回答の期限切れか)即True。仕様には「無回答で期限切れた
提案は再送しない」という規定がなく、書かれていない抑制を勝手に足しません
(ws-6設計 §2.4-4)。
defer履歴があるときだけ、抑制期間とスコア変化の2条件を見る。4行のif文に、
ここまでの設計判断が全部詰まっています。

呼ばれるのは2か所です。新しいlatches行を作る直前と、14.3で見た昇格の直前です。
昇格=proposed化ですから、どちらも「提案として出す直前」に同じ門を通るのが正しい。

## 14.7 回答期限は、いちばん厳しい条件が勝つ式で決まる

提案には必ず「いつまでに答えてほしいか」の期限が付きます。response_deadline
列です。この期限は、決められた式(D-05・03 D-05)から計算します。

```text
回答期限 = min( max( 通知時刻 + 15分, min(通知時刻 + 2時間, 対象開始時刻 − 60分) ),
               参加Intentのexpires_atの最小値 )
```

一見複雑ですが、4つの定数はどれも生活の直感に対応しています。

- **+15分**: 通知が届いてから考える時間の下限。届いたそばから期限では不親切
- **+2時間**: 通知を送ってから返事を待つ上限。いつまでも宙に浮かせない
- **−60分**: 対象の開始1時間前には締め切る。開始5分前の「参加します」は
  準備が間に合わない
- **expires_at**: Intent自体の有効期限。提案だけ長生きしても仕方がない

式そのものは `latch_calc.py:34` の `response_deadline` 関数で、この定義通りの
minとmaxの入れ子として書かれています。数式を読むより、値を入れて動かす方が
理解が早い。14.10の演習1でやります。ここでは採取した実行結果だけ先に見せます。
通知時刻を10:00として、3つのパターンです。

| 対象開始時刻 | 2人のexpires_atの最小値 | 計算結果 | 勝った条件 |
|---|---|---|---|
| 16:00(6時間後) | 5日後 | 12:00 | 通知+2時間の上限 |
| 11:30(90分後) | 5日後 | 10:30 | 開始−60分の締切 |
| 11:30(90分後) | 10:20(20分後) | 10:20 | Intentの期限 |

同じ90分後の対象でも、Intent自体の期限が20分後に迫っていれば、期限が式全体を
引き下げます。minとmaxの入れ子は「いちばん厳しい条件が勝つ」構造です。

もう1つ、期限に絡む窓直前のルールがあります。**75分ルール**です。提示しようと
取り出した時点で、対象開始時刻まで75分を切っていたら、通知そのものをしません。
行はcandidateからexpiredへ遷移して、静かに終わります(`try_promote` の手順2)。
今夜20:00の飲み会の提案が19:00に届いても、準備も返事も間に合いません。「出る/
出ないを決めるための最低限の時間」が75分という設定です。期限まで2時間以内の
Intentを特別扱いするcatch-up(14.9で出てきます)と、この75分が
組み合わさって、期限間際の提案は丁寧に避けられています。

ここまで読むと気になることがあるかもしれません。latches行を作る時点(14.3)でも
response_deadlineを書いていました。あれは**暫定値**です。通知時刻を「いま」と
して計算しているので、保留キューで何時間も待ってから提示された行では、
式の前提(通知時刻)が変わってしまいます。だから `try_promote` は、実際に提示する
瞬間(手順4)に**必ず式を再計算して上書きする**のです。candidate作成時の値を
提示判定に使わない、と仕様(06 §10)に明記されているのはこのためです。

最後に「対象開始時刻」の定義です。1対1の提案にはtime_startが2つあります。
どちらを使うのか。LATCHはmax(遅い方)を採用しました。Hard Filterが「時間窓の
交差」を条件にしている以上、2人が合流できるのは遅い方の開始時刻から。早い方を
使うと、「片方の窓はもう開いている」ケースが常に75分ルールに引っかかって
実用になりません。この「遅い方」の値(`pair_target_time`)は、75分ルール・D-05式・
defer抑制の残時間・drainの提示順のどこでも一貫して使われます。docsに明示がなく
設計で確定した解釈で、ws-6設計 §2.4に理由が書かれています。

## 14.8 まだ提案しない相手にも、礼儀正しく声をかける

閾値に届かなかったペアは、いままでの流れだと何も起きません。ところがユーザー側に
は「近くにいる人だけでも知りたい」という設定があります。通知の受け取り方を決める
notification_level の3つの値のうち、**nearby_also** を選んだユーザーです。この
設定のユーザーには、閾値未満の候補でも
「条件に近い人がいる」ことだけを知らせます。

知らせるのは「いる」だけです。相手が誰で・いつ・どこで・どのくらい合いそうかは
一切出しません(08 §2.2)。privacy設計(08)の軸は「マッチが成立して初めて相手が
見える」ことです。閾値未満の緩い通知でもこの軸を崩さないために、ここまで削ります。
だからproposalも、visibilityの設定にかかわらず常に最小構造
`{"headcount": 2, "match_level": "low"}` を入れるだけです
(`proposal.py:23` の `nearby_proposal`)。summary_only同士のペアであっても、
存在通知にサマリを出さない以上、格納する情報も最小に揃えます。

nearbyの行はlatchesにcandidateとして作られますが、drain(14.5)の対象からは
外されています。スコアが閾値未満だからです。この行が提案になる道は1つだけで、
**再評価で閾値を超えて、14.3の昇格を通る**ことです。昇格のときproposalは
visibility分岐の通常生成へ、scoreは新しい値へ、書き換わります。存在通知で
「様子見の行」を作っておき、条件が揃ったら同じ行を格上げする——2行目を作らない
部分UNIQUE索引の設計が、ここで活きます。

存在通知もD-08の日次上限(1日6件)には数えます。上限に達していたら、その通知は
スキップされます。提案と違って保留はできません(「条件が近い人がいました」を
後から届けても意味が薄い)ので、超過分は切り捨てです。一方、同時進行上限
(3件)には数えません。あちらはproposedの件数の上限で、candidateのままのnearby行
は対象外だからです(06 §6)。

逆の設定が **muted**(通知オフ)です。このユーザーには通知行を書きません。
しかし提案自体は作られ、proposedへ遷移します。一見奇妙に見えますが、latchesの
statusは「システムがこの2人を提案として扱っているか」の記録で、notificationsは
「それを誰に届けたか」の記録。2つのテーブルは役割が違い、mutedは後者にだけ
効きます。proposed遷移の数はMutual Latch Rate(提案のうち成立した割合)の分母に
使う指標です。通知オフのユーザーを分母から外すと指標が歪む——この理由も
設計(09 §2.1)に書かれています。なお、通知しない以上、日次上限も消費しません。

## 14.9 時間が経てば、組み合わせは見直される

ここまで読んできた流れは、すべてEmbedding完了のEventが起点でした。だが待って
ください。マッチングの相手は静止していません。新しいIntentが登録され、
既存のIntentが更新され、期限が近づいていく。昨日0.75だったペアが、今夜の
新規登録で0.83になっているかもしれない。**評価は、最初の1回で終わらない**のです。

再評価のきっかけとして仕様(06 §9)が決めているのは2つです。

1. **30分Bucket**: time_startが「今の30分区切り」に属するIntentを、そのBucketの
   来到時に1回だけ再評価する。20:00開始のIntentは、20:00を含むBucketの処理
   (20:00を過ぎて最初の周期、最大60秒後)で再評価される
2. **catch-up(キャッチアップ=追いつき)**: 期限(expires_at)まで2時間以内になった
   activeなIntentを、60秒周期のスキャンで拾って再評価する

1つ目は「始まる直前に最後の相手探し」、2つ目は「もうすぐ期限のIntentを諦める前に
もう一度」です。どちらにも「30分以内に再評価したばかりなら抑える」という第12章の
reevalガードが入口で効きます(`ReevalGuard.allow`・`SET reeval:{intent_id} NX EX 1800`)。
ガードそのものは第12章で `_run_matching` の前で会いましたが、ws-6からは
この再評価経路でも同じ門番が共用されます。門番を2人置くと基準がぶれるので、
1人を2か所で使う——部品の再利用の好例です。

実行のされ方のほうが、この章の山場の1つです。**Eventを発行しません。**
第9章のEventは「何が起きたか」を知らせる配達物で、Pub/Subがworkerへ運びました。
再評価もEventで流せば済むはずです。なぜ直接呼ぶのか。

答えは第9章9.3の冪等キーにあります。Eventには
`(event_type, source_intent_id, version)` のUNIQUE制約が付いていて、同じ内容の
Eventは1回しか登録できないのでした。これが効いてきます。1つのIntentは時間の
経過とともに何度も再評価の対象になります——自分のBucketの来到で1回、期限2時間前の
catch-upで1回、その次の60秒でまた……。ところがversionはIntentを更新しない限り
上がりません。つまりEvent経路では、**2回目以降の再評価が恒久に発行できない**
のです(ws-6設計 §2.8)。配達の仕組みが「同じ内容は1回」を保証する一方で、
再評価は「同じ内容で何回も」やりたい。この矛盾を避けるため、再評価は配達を
使わず、自分のプロセスの中で直接パイプラインを呼びます。

呼び出し先は `worker/main.py:288` の `_run_direct_pipeline` です。

```python
    async def _run_direct_pipeline(self, engine, intent_id: uuid.UUID) -> None:
        """Bucket/catch-up起点の直接投入(design §2.8-3・Eventを発行しない)。

        ...docstringの続き...
        """
        async with engine.begin() as conn:
            await run_candidate_retrieval(conn, self.clock, intent_id)
        if self._jev is not None:
            await self._jev.handle(intent_id)
        if self._latch is not None:
            await self._latch.handle(intent_id)
```

見覚えのある並びです。Layer 1〜3(run_candidate_retrieval)を自分のトランザクションで
実行し、コミットしたらLayer 4(JevWorker)を、そのあとLayer 5(LatchEngine)を直列で
呼ぶ。`_kick_jev` の続きと同じ形です。Stage1の中にある `_run_matching` が
stage1トランザクションに「同乗する」構造だったのに対し(第12章12.5)、こちらは
独立したトランザクション。だから流用せず、この専用の入口を別に作りました。

この周期処理を回し続けるのが `worker/reeval.py` の **ReevalRunner** です。第10章の
バックフィル周期タスクと同じ型のsleep-first周期ジョブで、60秒ごとに`run_once`を呼びます。
`run_once` の中身は、catch-up対象と当該Bucket対象のSQL2本でIntentを抽出し、
reevalガードを通したものへ `_run_direct_pipeline` を1つずつ。例外は握らずに
Runnerへ伝播し、Runnerが握って次の周期で回収する——失敗がキューの再配信に
載らない代わりに、60秒後の再試行が回収する、という割り切りです。

最後に、この経路と14.5のdrainが手をつなぎます。LatchEngine.handleの末尾は毎回
drainでした。再評価経路でLatchEngineが呼ばれるたびに、保留キューが提示順で
回されます。だから「0時を過ぎて日次上限がリセットされた保留分」も、0時を過ぎて
最初の再評価(1分以内のcatch-upか、最初のBucket)で自然に提示されます。正式には
M3で0時の再評価イベントが接続される予定ですが、それまでの間、枠の仕組みが
回り続けるのです(06 §10)。

## 14.10 自分で確かめる

この章の内容は、純関数・SQL・実DBの3層で確かめられます。全部今日動作を
確認しました(2026-09-29)。

### 演習1: 純関数を境界値で動かす

`latch_calc.py` の関数はDBもClockも持たない純関数なので、`uv run python -c` で
直接呼べます。まずは回答期限の式(14.7)から。backend ディレクトリで実行します。

```bash
cd backend
uv run python -c "
from datetime import datetime, timedelta
from latch.core.clock import JST
from latch.worker.matching import latch_calc as lc

notify = datetime(2026, 10, 3, 10, 0, tzinfo=JST)
far = notify + timedelta(hours=6)
near = notify + timedelta(minutes=90)
print(lc.response_deadline(notify, far, notify + timedelta(days=5)))
print(lc.response_deadline(notify, near, notify + timedelta(days=5)))
print(lc.response_deadline(notify, near, notify + timedelta(minutes=20)))
"
```

期待される出力は次のとおりです。1行目が「2時間の上限」、2行目が「開始60分前の
締切」、3行目が「Intentの期限」がそれぞれ勝った結果です(14.7の表と対応します)。

```text
2026-10-03 12:00:00+09:00
2026-10-03 10:30:00+09:00
2026-10-03 10:20:00+09:00
```

次に、再提案の判定(14.6)です。対象時刻は通知の6時間後(抑制期間は残り時間の
半分=3時間)、見送りは30分前と25時間前の2パターン、スコアはprevが0.88に対し
0.9(変化0.02)で試します。

```bash
uv run python -c "
from datetime import datetime, timedelta
from latch.core.clock import JST
from latch.worker.matching import latch_calc as lc

notify = datetime(2026, 10, 3, 10, 0, tzinfo=JST)
far = notify + timedelta(hours=6)
print(lc.d07_allows(has_no_response=True, latest_defer_at=None, now=notify, target_time=far, new_score=0.9, prev_latch_score=None))
print(lc.d07_allows(has_no_response=False, latest_defer_at=None, now=notify, target_time=far, new_score=0.9, prev_latch_score=None))
print(lc.d07_allows(has_no_response=False, latest_defer_at=notify - timedelta(minutes=30), now=notify, target_time=far, new_score=0.9, prev_latch_score=None))
print(lc.d07_allows(has_no_response=False, latest_defer_at=notify - timedelta(minutes=30), now=notify, target_time=far, new_score=0.9, prev_latch_score=0.88))
print(lc.d07_allows(has_no_response=False, latest_defer_at=notify - timedelta(hours=25), now=notify, target_time=far, new_score=0.9, prev_latch_score=0.88))
"
```

期待される出力です。順に、noで拒否・初回は許可・見送り30分後だが新評価世代(prev
なし)で許可・見送り30分後かつスコア変化なしで拒否・見送り25時間経過で許可、
という5分岐です。

```text
False
True
True
False
True
```

### 演習2: 試験を部分的に実行する

この章の部品のunit試験は、意図的に境界値を並べたものです。19件が0.02秒で
回ります。

```bash
cd backend
uv run pytest tests/unit/matching/test_latch_calc.py -q
```

```text
...................                                                   [100%]
19 passed in 0.02s
```

時間がある日は `make test`(unit全体・881件・約6秒)と `make test-ci`
(integration込み・1030件・約3分)も回してみてください。この単位の追加で
unitは787件から881件に、全体は926件から1030件に増えました。

### 演習3: 部分UNIQUE索引を実DBで見る

14.3の索引は、psqlで実物が見られます(Lab 1と同じ構文です)。

```bash
docker compose exec -T db psql -U latch -d latch \
  -c "SELECT indexdef FROM pg_indexes WHERE indexname='uq_latches_intent_ids_open'"
```

期待される出力(見やすさのため整形しています)です。WHERE句に「開いている3状態
だけ」が入っていることを、目で確かめてください。

```text
                                             indexdef
-------------------------------------------------------------------------------------------------
 CREATE UNIQUE INDEX uq_latches_intent_ids_open ON public.latches USING btree (intent_ids)
   WHERE (status = ANY (ARRAY['candidate'::text, 'proposed'::text, 'partial_accept'::text]))
(1 row)
```

### 演習4: 配線を確認する

Layer 4とLayer 5が直列になっていることは、rgで追えます。

```bash
rg -n "_run_post_retrieval|_kick_jev|_run_direct_pipeline" backend/src/latch/worker/main.py
```

`_run_post_retrieval` の定義(280行付近)の中で `self._jev.handle` の次に
`self._latch.handle` が並んでいて、それを `_kick_jev`(298行付近・Embedding完了起点)
と `_run_direct_pipeline`(316行付近・再評価起点)の2か所が呼んでいれば正解です
(ws-7でチェーンの前後にGroupEngineが加わった経緯は第15章15.8)。

## 14.11 この章の再統合

Layer 5は、確率(jev_result)を提案(latches行+通知)という行為に変える関所でした。
そして行為には、全部で枠が付いていました。同じ2人に二度出さない(部分UNIQUE索引)。
1日に6件・同時に3件まで(D-08)。断られた組み合わせは沈黙し、見送りには条件が
要る(D-07)。間に合わない提案は出さない(75分ルール)。返事の期限は式で決める
(D-05)。見せる中身は設定に応じて削る(visibility)。

脚注的に添えると、この「行為の管理」の大半はM2の時点ではシステム側だけの話でした。
responsesは常に空で、ユーザーが提案に答えるAPIはまだなかったのです。ですが第13章の
「信用しない作法」がここでも一貫しているのは見てとれます。アプリ側の確認でなく
DBの制約に守らせる。数えるカウンタでなく事実の行を数える。遷移は条件つきUPDATE
で負かす。この規律は、のちにユーザーの回答を扱うことになったM3 ws-1(2026-09-30
マージ)にも、同じSQLの形で引き継がれました。返事の受理をWHERE句が検品するさまは、
第17章で読みます。

## 14.12 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| latch_score | 提案の良さのスコア。H × MutualScore × C で計算 |
| H(の再検証) | 提案の直前にHard Filterの条件をもう一度確認すること |
| MutualScore | 2人の「受け入れ確率」の小さい方。片方だけ乗り気なら低い方に合わせる |
| C(較正係数) | スコアの調整値。現状は1.0で、運用データで調整する定数 |
| latches | 提案の1行。誰と誰の・どんな内容の・どの状態の提案か |
| latch_status_events | 提案の状態遷移の履歴を記録する台帳。誰がいつ提案を動かしたかが残る |
| notifications | 通知の事実の記録。D-08日次上限はこの行を数えて判定する |
| 部分UNIQUE索引 | WHERE句つきの一意制約。「開いている行だけ一意」を強制する(0004) |
| ON CONFLICT DO NOTHING | INSERTの重複をエラーにせず黙って無視するSQLの書き方 |
| 昇格 | 閾値未満で作ったcandidate行を、再評価で提案用に更新すること |
| proposal | 提案カードの中身(JSON)。見せてもよいフィールドだけが入る |
| match_level | 一致度の3段階(high/medium/low)。スコアの生値は外に出さない |
| D-08 | 通知の上限。1ユーザー1日6件・1 Intentにつき同時3件 |
| 保留キュー / drain | 上限超過で留まったcandidate行と、それを提示順に回す処理 |
| D-07 | 再提案の制御。noは沈黙、deferは期間とスコア変化の両方を要求 |
| D-05式 | 回答期限の計算式。15分・2時間・60分前・Intent期限のminとmax |
| 75分ルール | 対象時刻まで75分を切った提案は通知せず破棄する規則 |
| nearby_also | 閾値未満でも「近くにいる」ことだけ通知する設定 |
| muted | 通知だけ届けない設定。提案の状態遷移は通常どおり行う |
| 30分Bucket | time_startの属する30分区切りが来たときに再評価するしくみ |
| catch-up | 期限2時間以内のIntentを60秒周期で拾って再評価するしくみ |
| 直接投入 | Eventを発行せず、パイプラインを自分のプロセスから直接呼ぶこと |
| ReevalRunner | catch-upとBucketを周期処理するジョブ(sleep-firstの60秒ループ) |

## 14.13 確認問題

1. AがBを0.9で受け入れ、BがAを0.3で受け入れるとき、このペアのMutualScoreは
   いくつですか。また、minではなく平均を使うとしたら、どんな失敗が起きそうですか
2. 部分UNIQUE索引のWHERE句が `status IN ('candidate','proposed','partial_accept')`
   に限定されている理由を、「断られた2人への再提案」の観点から説明してください
3. D-08の日次上限を、第12章のJevカウンタと同じRedisのINCRで実装しなかった理由は
   何でしたか。カウンタと「事実の行」の関係で説明してください
4. 退避つきUPDATE(`_RECORD_SCORE`)が prev_latch_score をNULLのまま退避すると、
   どの判定で「無条件に変化あり」として扱われますか。その判定は誰(どの仕様)の
   ためのものですか
5. 再評価でEventを発行せず `_run_direct_pipeline` を直接呼ぶ理由は、第9章で学んだ
   どの仕組みと衝突するからですか
6. mutedのユーザーを含む提案で、「notificationsに書かない」のに「proposedへは
   遷移させる」理由を、Mutual Latch Rateの分母としての扱いと合わせて説明してください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった部分が、
読み返すべき節です)
