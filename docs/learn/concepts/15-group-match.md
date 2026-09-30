# 第15章 3人以上を結ぶ: グループマッチ(Group Search)

- 種別: 解説(通読して理解を積む章)
- 前提知識: 第9章(at-least-onceと冪等性・stage1)・第11章(Layer 1〜3と
  match_candidates・`normalize_pair` の正規化)・第12章(cheap_scoreの3要素)・
  第13章(JevWorker・K_jとjev_result)・第14章(latches・部分UNIQUE索引・
  try_promoteとdrain)。
  第14章を読んだ直後の勢いで読むのがいちばん楽です
- この章を読み終えるとできるようになること:
  - 「3人以上なら」という人数希望のIntentが、1対1のパイプラインを通れない問題と、
    人数を緩和した候補Pool検索での解き方を説明できる
  - 15人のPoolから貪欲法で3〜4人の集合を作る手順を、種・互換行列・人数の共通包含と
    ともに`group_calc.py` で追える
  - K_j=8の枠が「1対1最低4回+残り最大4回」へ分け合われる規則と、
    未判定ペアの継続優先が次の評価で集合を確定させる様子を説明できる
  - 集合のスコアが「一番弱いペアに合わせる」minで決まる理由と、計算と生成を
    同一トランザクションに置くI-1対策を説明できる
  - D-06「メンバーが重なる開いている集合のうち最上位1つだけが通知される」絞りを、
    try_promoteの内側で追える
- 対応コード: `backend/src/latch/worker/matching/group_calc.py`・`group_engine.py`・
  `layer4.py` と `latch_engine.py` のM2 ws-7追加分・
  `backend/alembic/versions/0005_group_candidates_open_unique.py`・`worker/main.py` の
  配線追加分
- 設計の根拠: `docs/plans/M2/ws-7-design.md`(特に§2.2の人数緩和・§2.3の貪欲法・
  §2.4のK_j配分・§2.5の集約トランザクション・§2.8の0005)と
  `docs/plans/M2/ws-7-report.md`。
  本文の「NN §X」は `docs/NN-*.md` の第X節を指します(06 §5・§7〜§8=配分とGroup
  SearchとD-06・01 §15=グループの流れ・05 §2・§4=group_candidatesと中間表現・
  07 §2=Parserの人数出力・02 §4受入#18)
- 次に読むもの: `concepts/16-circuit-breaker.md`(第16章。LLM呼び出しの守りの
  続き。この章が作ったグループのlatchesが返事を受け取る仕組みは、その先の
  第17章で読みます)
  (回答API・expiry_sweeper・G2の検証記録の予定)

## 15.1 「3人以上なら」と入力したIntentは、1対1の門を永久に通れない

「今週末、3人以上でバーベキューがしたい」。こんな入力から話を始めます。

第5章で読んだParserは、この文章から人数の範囲を取り出します。「3人以上なら」は
**min=3・max=4** です(07 §2。「〜以上」の上限はMVPの4人で切ります)。一方
「2〜4人くらい」ならmin=2・max=4。「1人でも」とならmin=1。つまりIntentの人数希望は、
最小と最大の組で表されるのです。

ここで第11章を思い出してください。1対1マッチングのLayer 1には、人数の門がありました。
**どちらのIntentでも「2」が人数範囲に入っていること**(11.2の人数行)。この門は
2人の組み合わせを作るための専門の門なので、当然の設計です。

問題が見えますね。min=3・max=4のIntentは、この門を**決して通れません**。「2」が
範囲に入っていないからです。1人のバーベキュー希望者と組むことも、別の「3人以上」
希望者と2人で組むことも、Layer 1の段階で弾かれます。このままでは「3〜4人の
IntentセットでグループLATCHを成立させる」という受け入れ条件(02 §4の受入#18)が
永遠に達成できません。

そこで、グループ専用の門を別に作ることにしました。仕様書の06 §7は、グループの
候補を集めるPool(プール=待合室)を「同一の時間帯・地域・カテゴリでLayer 3を
通過したIntent群」と定義します。この「Layer 3を通過」を実装するとき、LATCHは
**人数の条件だけを緩和した別の検索SQL**を作る道を選びました(ws-7設計 §2.2の
承認事項1)。既存パイプラインの結果をそのまま流用すると、さっき見たとおり
min=3のIntentが永久にPoolに入らないからです。

実装は小さな分割です。第11章で読んだ `layer1.py` の条件文字列を、人数の行が
真ん中に挟まる形で3つに切り分けます。

```python
LAYER1_WHERE_HEAD = """...(資格・自己除外・カテゴリ・時間の交差・距離・ペア予算)..."""
ONE_ON_ONE_PARTICIPANTS = """    AND i.participants_min <= 2
    AND i.participants_max >= 2
"""
LAYER1_WHERE_TAIL = """...(ブロック・飲酒年齢)..."""
LAYER1_WHERE_BASE = f"{LAYER1_WHERE_HEAD}{LAYER1_WHERE_TAIL}"
LAYER1_WHERE = f"{LAYER1_WHERE_HEAD}{ONE_ON_ONE_PARTICIPANTS}{LAYER1_WHERE_TAIL}"
```

(`worker/matching/layer1.py:37〜80` の骨格。HEADとTAILのあいだに人数行が
挟まります。`LAYER1_WHERE` をつなげた結果は分割前と1バイトも変わらないよう
機械検証されて、ws-7で導入されました)

1対1側は今までどおり `LAYER1_WHERE` を使います。グループ側は `LAYER1_WHERE_BASE` に、
別の人数条件を組み合わせる。既存の検索に一切触れずに、新しい門だけ作る分割です。

「Layer 3を通過」の実質は、類似度とルールと語彙の重なりで点数を付ける
cheap_scoreの計算です(第12章)。この計算は純関数なので、門の形が変わっても
そのまま呼び直せます。通過試験の内容は同じ。この再利用ができるように、
第12章の時点でcheap_scoreがDBと切り離された純関数になっていたのでした。

## 15.2 組み合わせを探す前に、候補を15人に絞る: 候補Pool

次の問題は、計算量です。「3〜4人の組み合わせで一番良い組を作れ」と言われたら、
素朴には全部の組み合わせを試すことになります。15人の候補から4人を選ぶやり方だけで
1,365通り。候補が50人になれば23万通りです。仕様書はここを最初に禁じています
——**全組み合わせ探索は行わない**(01 §15)。その代わりに、第11章〜第13章と同じ
発想を貫きます。**各層で出力を絞る**。

復習すると、1対1の漏斗はこうでした。Vector検索が50件(K_v)→cheap_scoreで
20件(K_c)→Jevが8回(K_j)。グループにも、この鎖の最後にもう1つ、出力の
上限が加わります。**候補Poolの上限15人**。06 §8のD-24が「Kはその層から次層へ
渡す出力数の上限」だという定義を、第13章で読みました。グループのPool上限15も
同じKの仲間です。

鎖の全体はこうなります。

```text
Vector検索(相似度上位50件)→ cheap_score計算 → Pool(降順上位15人)
```

検索SQLは `group_engine.py` の冒頭にあります。

```python
_POOL_SEARCH = text(f"""
    SELECT i.id, i.version, i.user_id, i.participants_min, i.participants_max,
           i.time_start, i.budget_max, i.structured_data,
           1 - (i.embedding <=> CAST(:origin_embedding AS vector)) AS similarity
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE {LAYER1_WHERE_BASE}
      AND i.participants_min <= 4
      AND i.participants_max >= 3
      AND i.time_start >= CAST(:bucket_start AS timestamptz)
      AND i.time_start < CAST(:bucket_end AS timestamptz)
    ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC, i.id ASC
    LIMIT {group_calc.POOL_SEARCH_LIMIT}
""")
```

(`worker/matching/group_engine.py:58` から)

見た目は第11章のLayer 2とほぼ同じ、embedding同士の近さ順の検索です。違うのは3点。

1点め、`WHERE` が `LAYER1_WHERE_BASE`。15.1で分割した、人数行を除いた門です。
2点め、人数の緩和条件 `participants_min <= 4 AND participants_max >= 3`。これは
「**3または4が、このIntentの人数範囲に入っている**」という意味の書き換えです。
落とすのは、MVPの上限4人を超える希望(min=5)と2人固定(max=2)の2種だけです。
3点め、時間の条件が30分Bucket。起点の `time_start` が属する30分区切りの内側に
収まる候補だけを集めます(第14章14.9のBucketと同じ「time_startが属するBucket」
の解釈。時間帯が揃った人を集めるための条件です)。

検索で50件取ったら、Pool行×起点のペアでcheap_scoreを計算し直して、**点数の
高い順に上位15件**をPoolとします。並び順の規律は第12章と同じ、降順・同点は
intent_idの昇順。決定的であることも同じです。なおPoolのWHEREには第11章の
自己除外(起点と相手は別のユーザー)が入っているので、**Poolの15人は起点自身を
含みません**。貪欲法の説明で「起点を足して最大16人」と言うときの16は、この
15+起点の1です。

## 15.3 全部は試さず、良い順に積む: 貪欲法と互換行列

Poolが15人に決まりました。次は3〜4人の組を作ります。ここで出てくるのが
**貪欲法(どんよくほう)**です。名前の前に機能を言うと、こうです。すべての
組み合わせを比べて最善を選ぶ代わりに、**今の局面で一番良い選択を取って、
積み上げていく**方法。残りの候補から順序どおり条件を満たす人を1人足す。
それを繰り返す。厳密な最善は保証されませんが、探索空間を飲み込まずに
済みます。

積み上げの最初の1枚を**種(シード)**と呼びます。ここにPoolの人を足していき、
3〜4人になったら集合(しゅうごう)=グループの候補として確定します。

種は誰にするべきでしょうか。06 §7は「Pool内を走査して種となるIntentを選ぶ」と
しか書いておらず、評価処理の起点(このIntentの処理をしています、という主役)を
種にするとは書いていません。Poolは起点自身を含まない15人なので、素直に読むと
「Poolで一番点数の高い人」が種になり、起点を含まない集合を作ることになります。
同じ集合を他のメンバーの処理と重複して作る無駄があるうえ、起点自身のグループ
成立の機会も失われます。両方で損です。そこでws-7は**種=起点**と決めました
(設計 §2.3の承認事項2)。起点をPool走査順の先頭に置き、起点の
`participants_max >= 3` をグループ処理のトリガーとします。max=2(2人固定)の
起点なら、種になれないのでグループ処理全体を何もせず終えます。

足せるかどうかの判定には、第11章のHard Filterの条件がもう一度登場します。時間が
交差すること・距離が届くこと・年齢(飲酒を含むなら20歳以上)・ブロック関係に
ないこと・作成者が別人であること。ただし判定の相手が「起点×候補」の1対1では
なく、**候補と、今集合にいる全員**です。3人目を足すときは、種とも、2人目とも
仲良くできる人でなければなりません。

ここで素朴に実装すると、判定のたびにSQLが走ります。追加候補1人につき、集合の
人数ぶんの問い合わせ。貪欲法の途中で何度もやっていてはDBが疲れます。そこで
ws-7は、Poolの全員と起点(合わせて最大16人)の全ペア(最大120ペア)について
「2人がHard互換か」を**SQL 1回で事前に計算**してしまいました。返ってくるのは
互換なペアの一覧です。この「誰と誰が互換か」の表を**互換行列**と呼びます。
貪欲法の中では、この一覧の「ある・ない」だけを見ます。DB問い合わせは0回。

本体は `group_calc.py` の `build_groups` です。DBもSQLも持たない、入力から
出力を決める純関数として書かれています(第3章で学んだとおり、純関数は試験が
決定的になるので、この単位の試験の中心になりました)。

```python
def build_groups(
    pool: list[PoolEntry],
    seed: PoolEntry,
    compat: frozenset[tuple[uuid.UUID, uuid.UUID]],
) -> list[tuple[list[uuid.UUID], uuid.UUID]]:
    """貪欲法で3〜4人の集合を構成(06 §7・design §2.3手順1〜6)。
    ..."""
    remaining: list[PoolEntry] = [seed, *pool]
    groups: list[tuple[list[uuid.UUID], uuid.UUID]] = []
    while True:
        # 手順2: 残りのうち先頭の種になれるIntent(max >= 3)
        idx = next(
            (i for i, e in enumerate(remaining) if e.participants_max >= GROUP_MIN),
            None,
        )
        if idx is None:
            break  # 手順6: 種になれるIntentが残っていない
        head = remaining.pop(idx)
        members: list[PoolEntry] = [head]
        for cand in list(remaining):
            if len(members) >= GROUP_MAX:
                break
            if any(cand.user_id == m.user_id for m in members):
                continue  # 作成user_id相異(手順3-b・行列にも同条件がある=二重防御)
            if not all(
                _pair_key(cand.intent_id, m.intent_id) in compat for m in members
            ):
                continue  # 現集合の全メンバーと互換(手順3-a)
            if not _feasible_size([*members, cand]):
                continue  # 人数見込み(手順3-c)
            members.append(cand)
            if _settled(members):
                break  # 手順4: 3人で成立したら4人へ拡張しない(引用#8と整合)
        if _settled(members):
            ids = normalize_ids(m.intent_id for m in members)
            groups.append((ids, head.intent_id))
            won = set(ids)
            remaining = [e for e in remaining if e.intent_id not in won]
        # 不成立(手順5)は種のみ消費(while冒頭で次の種を探す)
    return groups
```

(`worker/matching/group_calc.py:104` から。引用内の「引用#N」は設計メモ
ws-7-design.md §1.2の確定値表の番号です)

外側の `while` が「次の集合」を、内側の `for` が「次のメンバー」を探します。
スキップの条件が3つ並んでいます。作成者の重複(同じ人が2つのIntentを出していたら
1人ぶんとして数えるため、集合には1人しか入れません)。互換行列にないペア(誰か1人と
折り合えなければ、その人は入れません)。そして人数の見込み。この3つ目を説き明かす
ために、人数の条件を一般化しましょう。

1対1のLayer 1では「2がどちらの範囲にも入る」でした。N人の場合はこれが
**「Nが、全員の人数範囲に入る」**になります。たとえばmin=2・max=3の人と
min=3・max=4の人が混ざる3人集合なら、3は [2,3] にも [3,4] にも入るので、
この集合は成立です。
逆に「4人でなければ嫌」(min=4)の人を含む3人集合は、3が [4,4] に入らないので
不成立です。式で書くと
**集合の人数 \|S\| が max(全員のmin) 以上で、min(全員のmax) 以下**。全員の条件を
同時に満たす人数範囲を探すので、これを人数の**共通包含**と呼びます。
`_settled` がこの判定です。

```python
def _settled(members: list[PoolEntry]) -> bool:
    """引用#3: |S| >= 3 かつ |S| >= max(min_i) かつ |S| <= min(max_i)。"""
    lo = max(m.participants_min for m in members)
    hi = min(m.participants_max for m in members)
    return len(members) >= GROUP_MIN and lo <= len(members) <= hi
```

(`worker/matching/group_calc.py:86` から)

`_feasible_size` はその予備検査で、ここから先を足しても**どの人数でも**全員の条件を
満たせなくなったら、それ以上追加しないためのものです。種がmin=4で相手がmax=3の
とき、達成可能な人数の共通部分は空になります([4,4] と [2,3] は交わらない)。
見込みの段階で弾いておけば、無駄な追加をしません。

読み進めた途中の行に、ひとつ不思議なコメントがあります。「3人で成立したら4人へ
拡張しない」。4人の方が賑やかで良さそうなものですが、やらない理由は2つあります。
ひとつはD-06の同点順(15.7で読みます)が「集合サイズの小さい順」を優先すると
決まっているから。もうひとつは、次節で読む集約スコアの性質です——人数を増やしても
スコアは上がる見込みがありません。だから3人で確定できるなら、そこで確定するのが
得、という判断です。

貪欲法から返ってきた集合は、次節のとおり2つのテーブルへ記録されます。

## 15.4 集合の記録と0005: ペアの評価は、1対1とグループの共有資産

`build_groups` が返した集合1つにつき、`GroupEngine.handle` は1つのトランザクションで
次を書きます。

- `group_candidates` へ、集合の1行(3〜4人のintent_idsとstatus='candidate')
- `match_candidates` へ、**メンバー全員のペアの行**(3人なら3ペア、4人なら6ペア)

2つ目が肝です。第13章で読いたとおり、Jevの評価もMutualScoreの計算も、単位は
つねに**2人のペア**です。グループになっても、この単位は変えません。4人集合の
評価とは、6つのペア評価の集まりです。そして同じ2人のペアは、1対1の文脈でも
グループの文脈でも**同じ1行**の評価を使い回す——これが05 §4の決め事で、
「**ペア評価は共有資産**」と設計書は呼びます。{A,B,C}の集合を作ったあと、AとBが
2人だけでマッチする文脈に現れても、Jevに同じ質問を二度払いしません。

集合の行そのものは `group_candidates` テーブルに、latchesと同じ作法で守られます。
第14章14.3の再登場です。「開いている同一メンバーの集合は1行」というルールを、
**部分UNIQUE索引**でDB自身に守らせる。マイグレーション0005がそれです。

```python
def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_group_candidates_intent_ids_open"
        " ON group_candidates (intent_ids)"
        " WHERE status IN ('candidate', 'proposed')"
    )
```

(`backend/alembic/versions/0005_group_candidates_open_unique.py:24` から)

0004(latches)と形が同じであることに注目してください。WHERE句の対象が
`('candidate', 'proposed')` の2状態なのは、group_candidatesの状態遷移が
candidate(評価中)→proposed(提案を作った)→closed(閉じた)の3状態だからです。
閉じた集合は複数行残せます。断られたメンバーがもう一度集まるかもしれないからで、
この履歴はD-07(第14章14.6)の判定の材料にもなります。

重複を防ぎたい場面は、1対1よりひとつ多いのが面白いところです。at-least-onceの
再実行に加えて、**集合のメンバーは誰でも種になれる**ため、別々のメンバーの
評価処理が同じ集合を作り直してくる経路があります。たとえばA起点の処理で
{A,B,C}を作った直後に、C起点の処理が同じ{A,B,C}を作りにくる。INSERTは
`ON CONFLICT DO NOTHING` で跳ねて、先に作られた1行だけが残ります。

`member_scores` 列には、評価の実値ではなく `{"seed_id": ..., "versions": {...}}`
という参照情報だけが入ります。versionsは集合を作った時点の各Intentのversion。
次節の集約で「評価世代が変わっていないか」の照合に使います。ペアの評価値は
match_candidates行が持っているので、写しは持ちません。同じ値を2か所に持つと
必ず食い違う瞬間が来る——第14章14.5でカウンタについて読んだのと同じ規律です。

なお、削除のEventが来たら、削除されたIntentを含む集合はstage1の段階で
status=closedへ閉じます(1対1の候補無効化と同じ箇所に、group_candidatesの
無効化が1つ加わりました)。メンバーが欠けた集合はもう集約できないので、
最も早い段階で閉じるのが正しい扱いです。

## 15.5 お金を払う8回の枠を、1対1と分け合う: K_j配分

集合ができて、ペアの行が揃いました。ここから先は、第13章のJevWorkerが
ペアを評価します。ところが、ここで予算の本質的な問題が立ちふさります。
**1イベントで払えるJevの回数は8回のまま**(06 §5・D-24)。グループのペアが
増えても、予算は増えません。4人集合は6ペアで、残り枠は最大4回。1回の処理では
評価が揃わないのです。

仕様(06 §5)はこの8回の配分規則を4つ決めていて、ws-7はそれを
`select_jev_targets` という純関数に実装しました。規則を先に訳します。

1. 1対1のペアに**最低4回**を保証する
2. 残り(最大4回)をグループのペアへ割り当てる
3. 割当の順は、**前回未判定だったペアの継続を優先し、次に新規**のグループペア
4. グループに使わなかった枠は1対1へ繰り上げる(この場合の1対1の実効上限は8回)

コードは短いので、全体を読みます。

```python
def select_jev_targets(
    rows: list[JevCandidateRow],
    new_pair_row_ids: frozenset[uuid.UUID] = frozenset(),
) -> list[JevCandidateRow]:
    """K_j配分の純関数(06 §5規則1〜4・design §2.4)。
    ..."""
    one_on_one = [r for r in rows if r.pair_kind == PAIR_KIND_ONE_ON_ONE]
    selected = one_on_one[:ONE_ON_ONE_MIN]
    budget = K_J - len(selected)
    if budget > 0:
        continuing = [
            r
            for r in rows
            if r.pair_kind == PAIR_KIND_GROUP and r.row_id not in new_pair_row_ids
        ]
        fresh = [
            r
            for r in rows
            if r.pair_kind == PAIR_KIND_GROUP and r.row_id in new_pair_row_ids
        ]
        selected += (continuing + fresh)[:budget]
    if len(selected) < K_J:  # 規則4の繰上げ(1対1実効上限はK_j=8)
        extra = K_J - len(selected)
        selected += one_on_one[ONE_ON_ONE_MIN : ONE_ON_ONE_MIN + extra]
    return selected
```

(`worker/matching/layer4.py:196` から)

入力の `rows` は、SQLがcheap_score降順に並べた未評価ペアの全行。`pair_kind` は
その行が1対1(`one_on_one`)かグループ由来(`group`)かの札で、SQLの `is_group` 列の
実値です。この札は「開いている集合に、このペアの両方のIDが含まれるか」を
`group_candidates.intent_ids @> ARRAY[...]`(配列の包含)で調べて付けます。
2行目の引数 `new_pair_row_ids` は「今回の処理で作ったばかりのペア行」のidの
集合。継続と新規の区別に使います。

まず1対1の上位4件を取ります。次に残り枠を計算して、継続(=今回作っていない、
つまり前の評価で残ったペア)を優先し、そのあと新規を詰めます。グループのペアが
少なくて枠が余れば、1対1を上から繰り上げて8回ちょうどまで使う。

「継続を優先する」のは、規則3が**未判定ペアを次の評価の最優先にする**と決めて
いることの実装です。これが何を守るかというと、**放置され続ける集合**を出さないこと。
4人集合の6ペアのうち4ペアだけ評価された状態は、いつまでも「評価中」のまま
漂います。継続優先で毎回未判定ペアから埋めていけば、4人集合は高々2回の
再評価で全部のペアが揃う、と仕様は見積もっています。

第13章13.7で読んだ「上位8件」は、この拡張で姿を変えました。SQLから `LIMIT 8` が
消えています。8は**選択する件数**ではなく**実行する回数**の上限です。種類の
混ざった枠の配分は、SQLでなくPythonの純関数で書く方が正確に書ける——そういう
判断です(SQLは並び順と資格の判定に専念します)。第13章の時点では1対1だけが
相手でした。このとき「上位8件を取る」と「8回払う」が同義でしたが、グループが
混ざると同義でなくなった、ということです。

## 15.6 全員ぶんが揃って初めて決まる: 集約のminと、同一トランザクション

ペアの評価が少しずつ進んでいく様子を想像してください。4人集合の6ペアのうち、
今回のイベントで3ペア。次の再評価で2ペア。そのまた次で1ペア——という具合に、
**複数のイベントにまたがって**揃っていきます。全部揃った瞬間、集合のスコアを
計算して、提案にしてよいかを判定します。この締めの処理を、GroupEngineの
`finalize`(ファイナライズ=仕上げ)と呼びます。

スコアの式は、第14章14.2のlatch_scoreの親戚です。

```text
aggregate_score = H × min over ペア(MutualScore) × C
```

HはHard互換の再検証(通過すれば1)、Cは較正係数(現状1.0)。中程の
**min over ペア**が、この章の主人公です。集合内の全ペアについてMutualScore
(お互いの受け入れ確率の小さい方。第14章14.2)を計算し、**その最小値**を
集合のスコアにします。

なぜminなのでしょう。4人で食事に行くとして、AとB・AとC・AとD・BとC・BとDは
快適でも、CとDだけが折り合えないとしたら。その卓の空気は、一番折り合えない
2人に引っ張られます。輪の強さは最も弱い部品で決まる——だから**評価の一番
低いペアがいる集合は、そこで頭打ち**と評価するのです。平均なら1ペアの不仲を
他5ペアの仲良しで薄められますが、それは実際の食卓では起きません。
mutual_scoresのリストを渡してminを掛けるだけの3行の純関数
(`group_calc.py:54` の `aggregate_score`)に、この判断が込まれています。

閾値は1対1と同じ **0.60**(v0.6。当初は0.80でしたが2026-09-30の改版で緩和)。
別の閾値を設けません(06 §8 D-06)。未満ならlatchesは
作らず、集合はcandidateのまま次の評価を待ちます。

`finalize` の本体は `group_engine.py:749` から約200行続き、読むべき構造は
2段階に分かれています。前半の読取フェーズと、後半の書込フェーズです。前半では
起点が属する開いている集合を1つずつ門くぐらせて、通過した集合の材料を集めます。
門は3つあります。

- 評価世代が変わっていないか(member_scoresのversionsと現行の照合。
  変わっていればスコアをリセット)
- 全ペアのjev_resultが揃っているか
- 人数の共通包含と互換行列の再検証

揃っていない集合には**何もしません**。status=candidateのままで、15.5の継続枠が
次の評価で回収します。

後半の書込は、集めた材料からスコアを計算し、**1つのトランザクション**で
aggregateの更新とlatchesのINSERT(とlatch_status_events)を書きます。

ここで、グループのlatches行の形をはじめて具体的に見ておきましょう。第14章の
latches行はintent_idsに2人分のUUIDが入った「2人組の1行」でした。グループでも
**集合1つにつき1行**です。intent_idsに3〜4人分のUUIDが(sorted正規化で)
入り、scoreにはaggregate_score、proposalには集合版の提案カードが入る。
さらに `group_candidate_id` という列が加わって、この行がどのgroup_candidatesの
行から生まれたかを指します(のちにM3 ws-1で、昇格UPDATEがこの列も書き換える
ようになりました。世代が変わって同じ行を再昇格するとき、常に昇格時点の最新の
gidを指すためです)。テーブルはlatchesの1本のまま、行の人数が伸びただけ、
という形です(この可変長の配列をSQLへ渡す工夫は、15.8の終わりでまた出ます)。

トランザクションを1つにまとめることに、設計上の理由が刻まれています。
ws-6から引き継がれた空白の話です。

ws-6の実装では、1対1のlatch_scoreの計算(退避つきUPDATE)と、提案の行を作る
INSERTが別トランザクションでした。途中で読取に失敗すると、「スコアは計算済み」
なのに「提案の行はなし」という中間状態が残る。そして再実行のガードが
「スコア計算済みなら再選択しない」だったため、この行は**二度と選ばれず**
放置される——こんな穴が、ws-7の着手前に指摘されていました(ws-6報告書の
引継ぎI-1)。

ws-7はこの穴を2つの場所で構造的に塞ぎました。グループ側(finalize)では
「失敗しうる読取をすべてトランザクションの前に済ませ、計算と生成物を同一
トランザクションに置く」。同じ構成を、1対1のLatchEngineでも組み直しました
(承認事項4)。読取段階で失敗してもスコアはNULLのままなので、次の評価処理が
自然にもう一度選び直す。中間状態がそもそも作れない形、というのが「冪等ガードで
救う」よりも強い解決です。

## 15.7 1人でも重なる集合があるときは、一番良い集合だけが先に立つ: D-06

最後の関所です。15.3の貪欲法は、1つのPoolから**複数の集合**を作ることがあります
(残りメンバーから第2の種を探す手順6)。たとえば{A,B,C}と{A,D,E}。両方とも
閾値を超えたら、両方通知していいのでしょうか。Aさんのスマホに、2つのグループ
提案が同時に届きます。

仕様はこれを禁じます(06 §8 D-06)。**最上位の1集合のみを通知**し、その提案が
閉じた後に次の集合を評価して通知する。ユーザーに選択の混乱を起こさせないための
規則です。

「最上位」の決め方もD-06が定義します。aggregate_scoreの降順。同点なら集合サイズの
小さい順(3人を4人より先)。さらに同点ならintent_idの辞書順。この順序の判定は
`group_calc.dominates` という純関数に切り出されています。

実装の位置は、第14章14.5で読んだ `try_promote` の内側です。75分ルールの直後に
入ります。いま `try_promote` が昇格させようとしているlatches行(以下「自分」)
がグループの行(`group_candidate_id` が入っている)なら、**自分とメンバーが
1人でも重なる、開いているグループlatches**を探します(intent_ids配列の交差
`&&` で判定)。自分より上位の行があれば、proposed化せずcandidateのまま返ります。
上位の提案が閉じれば(expired等)、drain(第14章14.5の保留キューを回す処理)が
次の評価で2番手を提示します。グループのlatchesもscore >= 0.60のcandidate行として
drainの走査対象に最初から入っているので、回収の経路を新しく作る必要がなかった
のです。

いちばん上の集合が決まる場面には、もう1か所あります。`finalize` の中で、
確定した集合を処理する順番です。読取フェーズで集めた確定対象をD-06順
(スコア降順→サイズ昇順→id辞書順)に**ソートしてから**、集約のトランザクションと
try_promoteを上から実行します。このソートは、最初の実装にはありませんでした。
試験を書く過程で、ある順序依存が見つかったのです。低い集合を先に処理すると、
try_promoteの比較対象になる「作成済みのlatches」がまだ無く、上位チェックが
効きません。そこで高位→低位の処理順へ修正され、下位が正しく抑制されるように
なりました(ws-7報告書の修正パス2)。決定性を得るためのソートで、第12章の
同点順と同じ気質の修正です。

## 15.8 同居の規律: 1対1はグループを覗かない、グループは1対1を壊さない

ここまで読んだ部品を、既存のパイプラインに差し込む配線を確認して、章の本体を
閉じます。

`worker/main.py` の `_run_post_retrieval` が、Layer 3のあとの共通チェーンです。

```python
    async def _run_post_retrieval(self, intent_id: uuid.UUID) -> None:
        """L1〜3後の共通チェーン(design §2.1案A)。
        ...
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

並びが意味を持ちます。GroupEngine.handle(集合を作り、ペア行を用意)→JevWorker
(15.5の配分で評価)→LatchEngine(1対1の提案)→GroupEngine.finalize(15.6の集約)。
集合を作ってから評価し、評価が終わってから1対1を確定し、最後に集約する。
Layer 5+通知の層別予算(2秒以内)を1連の流れで守る、第14章14.1の直列チェーンを
そのまま4段に伸ばした形です。

1対1側のLatchEngineは、グループ所属中のペアを**覗きません**。latch_scoreを
計算する対象の選択SQLに「開いている集合に属さないペアだけ」という除外が入り
ました。集合のaggregateはjev_resultから直接計算するので、latch_scoreは不要。
逆にJevWorkerは1対1もグループも同じペア行を評価します(共有資産)。評価済みの
ペアがあとから集合に含まれても、行はそのまま、評価値が集約で使われます。

同居するうえでの細則がもうひとつ。第13章で読んだ評価直前のH再検証は、あの
SQLの人数の行が1対1のままなので、3人以上希望(min=3)のグループペアを誤って
「不成立」と判定してしまいます。そこでグループペアの再検証では、人数だけPoolと
同じ緩和条件(`min <= 4 AND max >= 3`)に差し替えた版を使います
(`layer4.py` の `_H_RECHECK_GROUP`・`hard_constraint_holds` の
`relaxed=True`)。ここは緩い再検証で構いません。人数の**最終**判定は、15.6の
集約時にもう一度厳密に走るからです。

最後に、実装中に発見された小さな教訓をひとつ。uuidの配列をSQLへ渡すとき、
LATCHはこれまで `ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]` という
「要素ごとに渡してSQLで組み立てる」書き方をしてきました(第14章14.3のINSERT)。
グループでは3〜4人と長さが可変になるため、ws-7はパラメータとして
**リストをそのまま渡す** `uuid_array`(`group_calc.py:43`。`CAST(:ids AS uuid[])`)
を導入しました。最初の実装は文字列リテラルで組み立てていたのですが、test-ciの
検証で「a sized iterable container expected」とasyncpgに拒まれることが判明。
リスト渡しへ統一されました(ws-7報告書・スーパーバイザー検証の記録)。可変長の
配列を扱うときは、SQLで組み立てる前提の書き方を見直す必要があった、という
実測に基づく修正です。

## 15.9 自分で確かめる

`cd backend` してから動かします。ここにあるものはすべて**お金がかからない**
演習です(純関数とスタブで完結します)。

### 演習1: 貪欲法を試験で動かす

`test_group_calc.py` は、15.3で読んだ純関数の試験です。3人で確定したら4人に
拡張しない・min=4の種は4人まで集める・ユーザーの重複はスキップ——といった
規則ごとに試験が並んでいます。

```bash
uv run pytest tests/unit/matching/test_group_calc.py -v
```

17件が通ります。最後の `test_dominates_ordering` は15.7のD-06順序の試験です。

```text
tests/unit/matching/test_group_calc.py::test_constants_pin_docs_values PASSED [  5%]
tests/unit/matching/test_group_calc.py::test_seed_max_ge_3_builds_three_member_group PASSED [ 11%]
tests/unit/matching/test_group_calc.py::test_pool_entry_max_lt_3_not_in_group_and_not_seed PASSED [ 17%]
tests/unit/matching/test_group_calc.py::test_seed_max_lt_3_returns_empty PASSED [ 23%]
tests/unit/matching/test_group_calc.py::test_three_member_group_does_not_extend_to_four PASSED [ 29%]
tests/unit/matching/test_group_calc.py::test_min4_seed_collects_four_members PASSED [ 35%]
tests/unit/matching/test_group_calc.py::test_unreachable_group_not_generated PASSED [ 41%]
(中略)
============================== 17 passed in 0.02s ==============================
```

(2026-09-29に実行して確認しました)

試験を読むだけでも学びがあります。`test_three_member_group_does_not_extend_to_four`
を開いて、期待値が3人であることを確かめてみてください。

### 演習2: K_j配分を境界で動かす

15.5の配分の試験は `-k "select_targets"` で部分的に実行できます。

```bash
uv run pytest tests/unit/matching/test_layer4.py -k "select_targets" -v
```

```text
tests/unit/matching/test_layer4.py::test_select_targets_identity_and_cap PASSED [ 16%]
tests/unit/matching/test_layer4.py::test_select_targets_keeps_input_order PASSED [ 33%]
tests/unit/matching/test_layer4.py::test_select_targets_one_on_one_minimum_four_first PASSED [ 50%]
tests/unit/matching/test_layer4.py::test_select_targets_continuing_group_preferred_over_new PASSED [ 66%]
tests/unit/matching/test_layer4.py::test_select_targets_promotes_one_on_one_when_no_group PASSED [ 83%]
tests/unit/matching/test_layer4.py::test_select_targets_minimum_four_kept_with_continuing PASSED [100%]

======================= 6 passed, 13 deselected in 0.04s =======================
```

(2026-09-29に実行して確認しました)

名前を読むと規則がそのまま並んでいます。`one_on_one_minimum_four_first`
(1対1の最低4件が先)・`continuing_group_preferred_over_new`(継続が新規より優先)・
`promotes_one_on_one_when_no_group`(グループがなければ1対1へ繰り上げ)。
第3章で学んだ「境界の試験」の見本のような一揃いです。

### 演習3: 配線を確認する

`_run_post_retrieval` の4段チェーンは、rgで追えます。

```bash
rg -n "_run_post_retrieval|_kick_jev|GroupEngine" backend/src/latch/worker/main.py
```

`_run_post_retrieval` の定義(280行付近)と、それを呼ぶ `_kick_jev`(298行付近・
embedding_completed起点)と `_run_direct_pipeline`(316行付近・再評価起点)の
3か所が見つかれば正解です。第14章演習4で追った2か所が、共通のヘルパーを
挟む3か所になりました。

## 15.10 この章の再統合

グループマッチは、第11章〜第14章の部品を組み替えた仕組みでした。候補を集める門は
人数を緩和して作り直し(15.1〜15.2)、組み合わせは貪欲法で積み上げ(15.3)、
評価の単位はペアのまま共有資産として使い回し(15.4)、予算は8回を1対1と分け合い
(15.5)、スコアは一番弱いペアに合わせて決め(15.6)、通知は重なる集合の最上位だけが
先に立つ(15.7)。そしてすべては、1対1がグループを覗かない・グループが1対1を
壊さないという同居の規律(15.8)の下で、既存のチェーンに4段目として組み込まれました。

通して見えてくるのは、**新しいテーブルはgroup_candidatesと索引1本だけ**で、
評価も提案も既存の器(match_candidates・latches・notifications)に流し込んだ
ことです。回答API(M3)で3〜4人のYES揃いを扱うときも、latchesの行は1対1と
同じ形をしている——その下地がこの章でできました。

## 15.11 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| 集合 | グループマッチの候補となる、3〜4人のIntentの組 |
| 候補Pool | 集合を作る前に狭く絞った候補の待合室。上限15人(D-24) |
| 人数の緩和 | グループ用の門。min<=4 かつ max>=3(3か4が範囲に入る) |
| 貪欲法 | 全組み合わせを試さず、今の最善を積み上げていく方法 |
| 種(シード) | 貪欲法で最初に置く1枚。LATCHでは評価の起点Intent |
| 互換行列 | Pool全員のペアのHard互換を1 SQLで事前計算した表 |
| 共通包含 | 集合の人数が全員の人数範囲に入っていること(\|S\|>=max(min) かつ \|S\|<=min(max)) |
| group_candidates | 集合の1行を記録するテーブル。candidate→proposed→closed |
| 0005 | group_candidatesの開いている行に部分UNIQUE索引を付けるマイグレーション |
| ペア評価の共有資産 | 同じ2人のペアは1対1でもグループでも同じmatch_candidates行を使う決め |
| K_j配分 | 8回のJev予算を「1対1最低4+残りはグループ(継続優先)」で分ける規則 |
| 継続ペア | 前回の評価で判定が残ったグループのペア。次の評価の最優先 |
| 集約(aggregate_score) | 集合のスコア。H × min over ペア(MutualScore) × C |
| min over ペア | 全ペアのMutualScoreの最小値。評価の一番低いペアが頭打ちを作る |
| finalize | 全ペアの評価が揃った集合を確定させるGroupEngineの仕上げ処理 |
| D-06 | 通知の順序規則。重なる集合のうち最上位1集合のみ通知 |
| uuid_array | uuidのリストをそのままSQLパラメータへ渡すためのヘルパー |

## 15.12 確認問題

1. min=3・max=4のIntentが、1対1のLayer 1を通れない理由を説明してください。
   グループのPool検索はこの問題をどう解決していますか
2. Poolを15人に絞る前に、検索で取る件数は何件ですか。また、絞り込みの順序の
   規律(第1優先・同点の第2優先)を言ってください
3. min=2・max=3の人・min=3・max=4の人・min=4・max=4の人の3人で集合は
   成立しますか。共通包含の式で確かめてください
4. 4人集合の評価に必要なペアは何本ですか。1イベントの残り枠は最大何回なので、
   評価は最低何回のイベントにまたがりますか。継続優先はここで何を防いでいますか
5. aggregate_scoreがminを採る理由を、「平均だと何が起きるか」から説明してください
6. finalizeで計算(aggregateの更新)と生成物(latchesのINSERT)を同一トランザクションに
   置く理由を、ws-6で見つかった空白(I-1)と合わせて説明してください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった部分が、
読み返すべき節です)
