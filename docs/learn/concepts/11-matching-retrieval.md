# 第11章 マッチング前半: SQLで確実に落とし、意味で上位を取る

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第10章(特に10.1のEmbedding・10.8のembedding_completed)・
  第9章(9.7のversion検査)・第4章4.5(SQLのCASTとbind param)・第6章(6.6の
  match_events・3点組)・第1章1.7(PostGIS・pgvector)
- この章を読み終えるとできるようになること:
  - マッチングが5層の漏斗(ファネル)でできていて、前半の2層が「SQLで落とす」と
    「意味で並べる」の分担であることを説明できる
  - Layer 1 Hard Filterの各条件(自己除外・時間の交差・距離・ペア予算・人数・
    ブロック・カテゴリ・飲酒年齢)が、それぞれSQLのどの式で書かれているか読める
  - cosine類似度が「ベクトルの向きの近さ」であり、pgvectorの `<=>` 演算子で
    上位を取れることを、数値の例で説明できる
  - 「同点はintent_id昇順で決定的に崩す」ために、速いHNSW索引をこの単位では
    使わないというトレードオフを説明できる
  - 通過ペアがmatch_candidatesへどう記録され、UNIQUE(a, b, av, bv)が二重生成を
    防ぐのかを説明できる
- 対応コード: `backend/src/latch/worker/matching/` パッケージ全体
  (origin.py・layer1.py・layer2.py・candidates.py・runner.py・__init__.py)
- 設計の根拠: `docs/plans/M2/ws-3-design.md`(特に§2.1の単一SQL・§2.2の決定性優先・
  §2.3のUPSERT・§2.6の条件確定表)と `docs/plans/M2/ws-3-report.md`。
  本文の「NN §X」は `docs/NN-*.md` の第X節を指します(06 §2=Layer 1の判定規則・
  06 §3=Layer 2・05 §2=match_candidatesの定義)
- 次に読むもの: `concepts/12-cheap-judge-cost-guard.md`(第12章。この章の50件を
  20件へ絞るLayer 3と、embedding_completedからこの章の関数を呼ぶ配線・コスト保護)

## 11.1 数万件の中から相手を選ぶ、2段の漏斗

第10章の終わりでは、Intentがベクトルに変換され、embedding_completedという知らせが
発行されるところまでを見ました。この章は、その知らせを起点に動くマッチングの
前半です。

LATCHのマッチングは、**5層の漏斗**(ふるい分けの工程。上から注いだ水が細く
なっていく形)として設計されています(06 §1・01 §12)。

| 層 | 名前 | 何で選ぶか |
|---|---|---|
| Layer 1 | Hard Filter | SQLとPostGIS。絶対に成立しない相手を機械的に落とす |
| Layer 2 | Candidate Retrieval | pgvector。意味が近い順に上位50件へ絞る |
| Layer 3 | Cheap Judge | 軽い評価(スコア計算) |
| Layer 4 | Jev | 意味の判定。ペアが成立するか(TypeSafe Jev+フォールバックLLM) |
| Layer 5 | Latch Score | 最終スコアと提案(proposal)生成 |

なぜ1つの賢い処理で済ませないのか。答えはコストと確実性の住み分けです。
Layer 3以降は1ペアずつ点数をつける処理なので、数万件の相手全員にかけると
時間もAPI費用も爆発します。だから、**DBのSQLが一瞬で落とせる相手は先に落とし**
(Layer 1)、**意味の近さで「これ以上評価する価値がある」上位だけを残す**(Layer 2)。
後続の評価層に流れる相手を漏斗の幅で制御する、という積み方です。

この章で読むのはLayer 1とLayer 2の2層です。この単位(ws-3)の時点では、
残りの3層も、この2層をembedding_completedから呼び出す配線も、**まだ実装されて
いない**状態でした。Stage1はembedding_completedを受信したら、version検査だけして
processedで閉じていました(第9章9.7)。この章の関数は、試験から直接呼ばれて
動いている状態でした。だからこそ関数の形がきれいです。この章で読む
`run_candidate_retrieval` は、DB接続とClockを引数で受け取るただの関数で、
WorkerもPub/Subも知りません。呼び出し方を後から選べる(ws-3設計 §2.4)。
配線前に実体を作るこの順番は、第9章の「経路を先に開通させる」と対になる
ものです——「関数を先に確定させる」ステップ、と呼べます。
なお、この配線は次の単位(ws-4)で実装され、Layer 1〜3がEventから駆動される
ようになりました。読み方は第12章12.5で扱います。

## 11.2 絶対に成立しない相手は、AIに聞かずにSQLで落とす

Layer 1の名前は **Hard Filter**(ハードフィルタ)です。Hard=硬いは、「ここを通らない
ペアは絶対に成立しない」という、例外のない条件の硬さを指します。自分自身のIntent
とのペアは成立しない。時間帯が重ならない相手は成立しない。こうした条件の判定に
AIは使いません(06 §2)。理由は2つ。SQLで書ける判定をAIに聞くのは費用の無駄ですし、
何よりSQLの判定は**間違えない**。索引も効き、同じ問いには同じ答えが返る。
確実に落とせるものは、確実な道具で落とす——それがLayer 1の立場です。

条件は8つあります(06 §2)。表にしてから、コードを読みます。

| 条件 | 成立の条件 | 落ちる例 |
|---|---|---|
| 自己除外 | 相手は別のユーザー | 自分の2つ目のIntent |
| カテゴリ | category_primaryが完全一致 | drinking と meal |
| 時間の交差 | 期間が少しでも重なる | 20-23時と、23-翌2時 |
| 距離 | 2人の中心が「半径の和」以内 | 喫煙所が半径1km同士で2.5km離れ |
| ペア予算 | min(2人のbudget_max)が500円以上 | 片方499円ならペアとして499円 |
| 人数 | 「2人」が双方の人数範囲に入る | 相手が「1人で」のIntent |
| ブロック | ブロック関係にない(双方向) | 相手が自分をブロック済み |
| 飲酒年齢 | 飲酒を含むなら双方が20歳以上 | 19歳の飲酒Intent |

実装は `backend/src/latch/worker/matching/layer1.py` です。特徴は、8条件を
バラバラの関数にしないで、WHERE句の文字列 `LAYER1_WHERE` に全部集めていることです。
コメント込みの引用は長いので、骨格だけ拾います(全体は `layer1.py:35` から)。

```python
LAYER1_WHERE = f"""
    i.status = 'active'
    AND i.embedding IS NOT NULL
    AND i.user_id <> CAST(:origin_user_id AS uuid)
    AND i.category_primary = :origin_category
    AND i.time_start < :origin_time_end
    AND :origin_time_start < COALESCE(i.time_end, i.time_start + interval '3 hours')
    ...(距離・予算・人数・ブロック・飲酒年齢の条件が続く)...
"""
```

1行目と2行目は、条件というより**対象の資格**です。相手はactiveで、かつembeddingが
入っていること。06 §3は、draftのIntentや、EmbeddingがまだのIntent(第10章の
失敗→バックフィル待ち)は、評価される側にもならないと定めています。`i.user_id <> :origin_...`
が自己除外、`category_primary = :origin_category` がカテゴリの完全一致です
(secondaryは判定に使いません。補助情報だからです)。

条件を1つのWHEREに集める設計には、理由があります(ws-3設計 §2.1)。Layer 2が、
このWHERE文字列を**そのまま使って**「同じ条件+意味で並べ替え」を1本のSQLで行う
からです(次の11.4)。2層で条件の定義を共有すれば、条件の書き忘れ・書き間違い
による溝は構造的に生まれません。「Layer 1の試験を通ったのにLayer 2の本番で
落ちる」という事態が起き得ない、ということです。

## 11.3 時間・距離・予算・飲酒年齢を、SQLの式で読む

8条件のうち、素直に読めるもの(自己除外・カテゴリ・人数・ブロック)は表のとおり
なので、ここではSQLにすると頭の体操になる3条件——時間・距離・予算——を読み込み、
残る飲酒年齢にも触れます。

**時間の交差**。2つの期間「20時〜23時」と「23時〜翌2時」は、23時ちょうどを
境界にして重なっていません。22:59までの相手となら1分だけ重なります。この
「境界で接しているだけなら重ならない」判定を、半開区間(始点を含み終点を含まない
区間)の交差として書いたのが引用の2行です。

```sql
    AND i.time_start < :origin_time_end
    AND :origin_time_start < COALESCE(i.time_end, i.time_start + interval '3 hours')
```

「相手の始まり < 自分の終わり」かつ「自分の始まり < 相手の終わり」。この2つの
不等式だけで、2つの半開区間が重なるかどうかが判定できます(片方でも成り立たなければ
離れています)。`COALESCE(i.time_end, ...)` は、終わり時刻がNULLの行に「始まり+3時間」
を当てる防御です。保存時に補完されるはずの値(第6章)ですが、Layer 1は
保存を信用しすぎない、という姿勢です。

**距離**。PostGISの出番です。

```sql
    AND ST_DWithin(
        i.geo_center,
        ST_SetSRID(
            ST_MakePoint(CAST(:origin_lon AS float8), CAST(:origin_lat AS float8)),
            4326)::geography,
        CAST(
            CAST(:origin_radius_m AS int) + COALESCE(i.geo_radius_m, 1000)
            AS double precision))
```

`ST_DWithin(a, b, r)` は「地点aと地点bの距離がr以内」を判定するPostGISの関数です。
`ST_MakePoint` が経度・緯度から点を作り、`::geography` がその点を「地球の表面の
位置」型へ変換しています。geography同士の距離は**メートル**で測る、というのが
PostGISの約束です(緯度経度のままのgeometry型だと度という単位になり、人間には
読めません)。距離のしきい値が「半径の和」なのは、2人の円が触れ合うかを見る
ためです(06 §2)。どちらか片方の「この範囲なら行く」という円だけを見るのでは
ありません。
`COALESCE(i.geo_radius_m, 1000)` は時間と同じく、相手の半径が入っていなければ
保存時の既定(1,000m)を当てる防御です。

**ペア予算**。1つ頭を捻る条件です。予算は「上限」ですから、ペアとして成立する
かは「2人の上限のうち低いほう」で判断します。デートの予算を2人で出し合うなら、
安くできるのは2人のうち安い側まで、という理屈です(06 §2)。これをSQLのCASE式で
書くと、NULL(予算の指定なし)の扱いと組み合わさって、引用のとおりの長い式に
なります(`layer1.py:25` の `_BUDGET_PAIR`)。

```sql
    AND (ペア予算の式 IS NULL
         OR ペア予算の式 >= 500)
```

`_BUDGET_PAIR` は「両方NULLならNULL・片方だけNULLならもう片方・両方あれば小さい方」
を返すCASE式です。そのうえで、NULL(どちらも予算を気にしない)なら落とさず、
500円未満のときだけ落とす。**無指定を罰しない**設計です。

残りの飲酒年齢は、少しだけ触ります。ペアのどちらかが `alcohol_involved=true` なら
**双方**が20歳以上でなければなりません(06 §2)。20歳の判定はPostgreSQLの
`EXTRACT(YEAR FROM AGE(...))`(満年齢)で、誕生日の当日から1つ繰り上がります。
API側で19歳の飲酒Intentは拒否される(第5章・第6章)のに、なぜここでも検査するの
か。答えは「二重防御」です。APIの検証をすり抜けたデータが
DBに入っていたとき(たとえば開発者が直接SQLで直したデータ)、最後の関所でも
落とせるように。防御は重ねておくほど強い、というより「APIの検証は入り口の利便性、
Layer 1は出口の正しさ」と役割が違うのです(ws-3設計 §2.6)。

## 11.4 意味の近い上位50件を取る: cosine類似度とpgvector

Layer 1を通過した相手のうち、今度は**意味の近さで上位50件**に絞ります
(Layer 2 Candidate Retrieval)。ここで第10章のベクトルが効いてきます。

まず「ベクトルの近さ」の測り方を確認します。LATCHが使うのは **cosine類似度**
(コサインるいじど。cosine similarity)です。2つのベクトルの**向きがどれだけ
同じか**を、1(同じ向き)から−1(逆向き)の範囲で測る値です。長さは無視します。
文章の意味の近さは「どの要素がどのくらい強く含まれるか」という**方向**に現れ
ます。長さの差はノイズ、という考え方です。3次元に縮小した例で確かめると(この章11.8の1
で同じものを動かします)、向きが同じ組は1.0、無関係(直交)は0.0、逆向きは−1.0。

pgvectorは、このcosineの計算と「近い順の並べ替え」をDBがやってくれる拡張です。
距離を測る演算子が `<=>` です(値としては「cosine距離」=1−類似度を返します)。
Layer 2のSQLが `layer2.py:26` にあります。

```python
_SELECT_TOPK = text(f"""
    SELECT i.id,
           i.version,
           1 - (i.embedding <=> CAST(:origin_embedding AS vector)) AS similarity,
           COUNT(*) OVER () AS pass_count,
           i.time_start,
           i.budget_max,
           i.structured_data
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE {LAYER1_WHERE}
    ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC, i.id ASC
    LIMIT {K_VECTORS}
""")
```

読みどころを3つ挙げます。

1つめ、`WHERE {LAYER1_WHERE}`。11.2で予告したとおり、Layer 1の条件文字列が
そのまま埋まっています。**絞り(Layer 1)と並べ替え(Layer 2)が1本のSQL**です。
2つめ、`ORDER BY ... ASC` は距離の昇順(近いものが先頭)。`1 - 距離` が類似度
なので、記録する `similarity` は降順と同じ並びです。3つめ、`LIMIT {K_VECTORS}`
の `K_VECTORS = 50`(`layer2.py:24`)。この50は「その層から次の層へ渡す出力数の
上限」として設計で決まった数です(06 §8 D-24)。Layer 3以降は最大50ペアずつ
評価すればよい、という漏斗の幅の指定でもあります。

`COUNT(*) OVER () AS pass_count` は少し変わった形です。`OVER ()` のついたCOUNTは
**window関数**といって、行ごとに値を作る通常の集計と違い、「結果全体の件数」を
各行に添えます。LIMITで切り詰める**前**の通過件数が、切り詰め後の50行と同じ
SQLで取れる、という仕掛けです。「Layer 1を何件が通ったか」は、Layer 3以降の
混み具合を見る手がかりになるため、戻り値に載せています。

SELECTの末尾3列(`i.time_start, i.budget_max, i.structured_data`)は、ws-4で
次の層(Layer 3)の計算のために追加されたものです(第12章で使います)。この単位
(ws-3)の時点ではid・version・類似度・件数の4列だけでした。「次の層に必要な
データは、同じSELECTでついでに取ってくる」と、後に1クエリで済む形へ育ちました。

起点側のベクトルの渡し方に、実装の苦労が1つあります。asyncpgドライバは
pgvectorの `vector` 型の列を**文字列**(`'[0.1, 0.2, ...]'` の形)で返します。
そこで起点を読むときも文字列のまま受け取り、`CAST(:origin_embedding AS vector)` で
DB側へ型を戻して渡します(`origin.py:81` の `_embedding_text`。想定が外れて
配列で返ってきた場合にも文字列へ組み直す防御つきです)。第9章9.8の5例目
「実DBが返す型はunitのスタブでは見えない」の教訓が、ここでも効いています。

## 11.5 速さより、決して揺らない並び: 同点はintent_id昇順

この単位でいちばん深い設計判断は、ORDER BYの**第2キー**です。

```sql
    ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC, i.id ASC
```

距離が完全に同じ相手が複数いたら、上位50件のラインで誰が入るかは、この第2キー
`i.id ASC`(UUIDの昇順)が決めます。なぜなら**「距離が同点」は現実に起こる**から
です。まったく同じ内容のIntent(同じテキスト→決定的に同じベクトル、第10章)を
2人が登録していたら、起点から見た2人の距離は完全に一致します。同点のまま
「たまたま」50件目に入ったり落ちたりしたら、同じデータで試験を回すたびに結果が
変わることになります。設計はこれを許しませんでした。「同点はintent_id昇順で
決定的に崩す」(06 §8 D-24・10 §4.6)。試験の再現性と、本番の挙動の一貫性、
両方のための指定です。

ところが、この1行には代償があります。pgvectorの**HNSW索引**を使えなくなるのです。

HNSW(エイチエヌエスダブリュー)は、ベクトルの近傍を探すための**近似的な**索引です。
「たぶん近いだろう相手を、全域と比べずに高速に探す」ための道具で、`intents.embedding`
列には0001マイグレーションのときからHNSW(cosine)索引が作られています(05 §3)。
数十万件のIntentを相手にすると、この索引の有無で速度が桁で変わるはずです。
ただしpgvectorの索引を使うには条件があって、ORDER BYが距離演算子**単独**で
なければなりません。`i.id ASC` という第2キーを付けた瞬間、PostgreSQLは索引を
使わず、通過した全行の距離を実際に計算してソートする(逐次ソート)動きになります。

つまり「HNSW索引で速く探す」と「並びが決して揺らない」は、pgvectorでは**両立できない**
要件でした。ws-3が選んだのは決定性です(ws-3設計 §2.2)。速い索引は近似である以上、
どの50件が選ばれるかに実装依存の揺れが入り得る。判定が揺らないことを機能要件と
すると、逐次ソートは「同じ問いに必ず同じ答えを返す、遅いが正確なやり方」です。
ci環境の規模(数千行)ではp95 1秒の予算(06 §1)に余裕で届くことも見込まれています。

将来の話も設計に書かれています。本番規模の負荷試験(10 §4.2)で1秒を割り込む
速度が出ないと分かったときだけ、見直す。そのときの候補(iterative scan と
境界同点の再取得を組み合わせる2段クエリ)まで記録してあります。**最適化は
計測が先、実装は後**——このプロジェクトの流儀どおりの置き方です。速度の心配を
先回りして正確さを削らない、という優先順位です。

## 11.6 選んだ相手は帳簿に記す: match_candidatesとUPSERT

Layer 2を通過した相手は、`match_candidates` テーブルへ1行ずつ記録されます。
評価の途中経過を残す帳簿です(05 §2)。この帳簿の設計は、第6章6.6のoutboxと
同じ匂いがします。**やったことをDBの行として残し、後段はその行を見て進む**。

まず**ペアの向きを正規化**します。Aの視点でもBの視点でも、同じ2人の組み合わせは
同じ1行にしたい。そこでUUIDの大小をPython側で比較し、小さいほうを必ず `intent_a_id`、
大きいほうを `intent_b_id` に置く約束にします(`candidates.py:46` の `normalize_pair`)。
どちらが起点になって評価しても、同じペアは同じ行に落ちます。

バージョンも記録します。`intent_a_version`・`intent_b_version` は「評価した時点の
お互いのversion」です。片方でも内容が更新されたら、組み合わせは「別の世代」として
新しい行で評価し直す——そのためにUNIQUE制約は4列を鍵にしています。

```sql
UNIQUE (intent_a_id, intent_b_id, intent_a_version, intent_b_version)
```

(05 §2。alembic 0001で作成済み)「評価はバージョン組ごとに1レコード」という
規定の実体です。

書き込みはUPSERTです。`candidates.py:22` のSQLを見ます(引用はws-4で
cheap_judge_score列が追加された後の現状です。ws-3の時点では
retrieval_scoreだけでした)。

```python
_UPSERT = text("""
    INSERT INTO match_candidates (
        intent_a_id, intent_b_id, intent_a_version, intent_b_version,
        retrieval_score, cheap_judge_score, status, created_at, updated_at
    ) VALUES (
        :intent_a_id, :intent_b_id, :intent_a_version, :intent_b_version,
        :retrieval_score, :cheap_judge_score, 'pending', :now, :now
    )
    ON CONFLICT (intent_a_id, intent_b_id, intent_a_version, intent_b_version)
    DO UPDATE SET
        retrieval_score = EXCLUDED.retrieval_score,
        cheap_judge_score = EXCLUDED.cheap_judge_score,
        updated_at = EXCLUDED.updated_at
""")
```

UPSERT(アップサート)は「あったらUPDATE、なければINSERT」を1文で行うSQLの型で、
`ON CONFLICT` が鍵(UNIQUE制約との衝突)を指定します。INSERTを試みて、同じ4列の
組が既に存在したら、UPDATE側に回る。2つの疑問に答えておきます。

1つめ、なぜ「同じイベントが2回来ても二重に作らない」のに、UPDATEで**上書き**するのか。
これは2つの別の役割です。二重生成の防止はUNIQUE制約が担当します(衝突したら
INSERTされない=行は常に1行)。上書きの対象は、**同一バージョン内の再評価**です。
将来、30分ごとの再評価(時間Bucket。ws-6で実装)で同じ4列組を再び評価したとき、
DO NOTHING だと古い retrieval_score が残り続けます。最新の評価で塗り替えるため、
DO UPDATE にして、更新するのはスコアの列(retrieval_score・cheap_judge_score)と
`updated_at` だけです(ws-3設計 §2.3。cheap_judge_scoreはws-4で追加)。status は
UPDATE 側で触りません。値の遷移——`pending`(評価待ち)から `evaluated`/
`skipped`(Layer 4のJev。第12章12.6)へ、`closed`(削除処理)へ——は、後続の
層や処理の担当だからです。この帳簿では「候補を見つけた」までが担当、
という線引きです。

2つめ、`EXCLUDED.` という書き方。これは「今INSERTしようとしていた新しい行の値」を
指すPostgreSQLのキーワードです。「新しい評価スコアで塗り替える」を1文で書ける、
UPSERTの定型句です。

## 11.7 起点に不備があれば、何もせずに返る

最後に、全体を束ねる `runner.py` と、起点(評価の出発点)の検証を読みます。
`run_candidate_retrieval(conn, clock, intent_id)` が層の入口で、処理は3歩です。
起点を読む(`origin.load_origin`)→検索する(`layer2.retrieve_topk`)→帳簿に記す
(`candidates.upsert_candidate`)。検索と記録の前に入るのが起点の検証です。

起点の検証は、skip理由の列挙です(`origin.py:24` から)。

```python
SKIP_NOT_FOUND = "origin_not_found"
SKIP_NOT_ACTIVE = "origin_not_active"
SKIP_EMBEDDING_NULL = "origin_embedding_null"
SKIP_GEO_MISSING = "origin_geo_missing"
SKIP_TIME_MISSING = "origin_time_missing"
SKIP_PARTICIPANTS = "origin_participants_range"
```

行がない(draftのまま削除された等)・activeでない・embeddingが入っていない・
位置や時間が入っていない・起点自身が「2人」を人数範囲に含まない。どれかに
当てはまれば、検索は1本も走らず、理由を文字列で載せた結果を返して終わります。
「生成元にも対象にもならない」(06 §3)という規定の起点側の実装です。

面白いのは、判定の多くが「ここに来ないはずのデータ」への**防御**である点です。
activeな行なら時間も位置も補完済みのはず(第6章)で、embeddingもembedding_completed
起点で流れてくる以上は入っているはず。それでも検査する理由は2つあります。
1つは、配線(次の単位)が「embedding IS NULLなら再検査して待つ」などの待ち方を
する可能性を残すため。もう1つは、保存の経路を信用しすぎない姿勢です(11.3の
COALESCEと同じ気質)。起点の人数検査は
少し性格が違います。相手の人数条件(2を含む範囲か)はLayer 1のWHEREが見ますが、
**起点自身**が「2人」を範囲に含まないなら、どの相手とも成立しないのは自明なので、
検索の前に諦めます。SQLを1本も投げない最適化であり、正しさの検査でもあります。

関数群がDB接続とClockを引数に取るだけの形(11.1で見ました)なのは、この検証も
含めた全体を1つのトランザクションで包めるためです。試験は `engine.begin()` で
直接包んで呼びます。Workerへの配線は次の単位が選ぶものでした——実際にws-4は
Stage1の処理への同乗を選びました(第12章12.5)。独立したトランザクションとして
切ることも、同乗させることもできる形が、この選択をあとから自由にしたのです。
例外は握りません。SQLの
失敗は呼び出し側へ伝播して、再試行の経路(第9章9.7)に載せる約束です(ws-3設計 §2.4)。

## 11.8 自分で確かめる

1. cosine類似度の直感を、自分の手で確かめる。`cd backend` して次を動かします。
   3次元に縮小した例で、向きが同じ組・無関係な組・逆向きの組を比べます

   ```bash
   uv run python - <<'EOF'
   import math

   def cos(a, b):
       dot = sum(x * y for x, y in zip(a, b))
       na = math.sqrt(sum(x * x for x in a))
       nb = math.sqrt(sum(x * x for x in b))
       return dot / (na * nb)

   same = [1.0, 0.0, 0.5]
   scaled = [2.0, 0.0, 1.0]       # sameの定数倍(向きが同じ)
   other = [0.0, 1.0, 0.0]        # 直交
   opposite = [-1.0, 0.0, -0.5]   # 逆向き
   print("向きが同じ(定数倍):  ", round(cos(same, scaled), 4))
   print("無関係(直交):        ", round(cos(same, other), 4))
   print("逆向き:              ", round(cos(same, opposite), 4))
   EOF
   ```

   期待される出力(2026-09-29に実行して確認しました):

   ```text
   向きが同じ(定数倍):   1.0
   無関係(直交):         0.0
   逆向き:               -1.0
   ```

   `scaled` のように長さが倍になっても1.0のままである点が「向きだけを見る」の
   実感です。pgvectorの `<=>` はこの cosine の「距離」側(1−類似度)を返します

2. `uv run pytest tests/unit/matching/test_origin.py -v` を実行する(14件。
   ws-4でstructured_data読み取りの試験が2件加わりました)。試験名から、skip理由6種の
   分岐・20歳計算の境界(誕生日当日と前日)・UUIDの正規化がそれぞれ試験に
   なっていることを読み取る

3. `uv run pytest tests/unit/matching/test_layer_sql.py -v` を実行する(12件。
   ws-4でLayer 3列のピンが2件加わりました)。この試験はSQLの文字列そのものを
   検査する型です(第6章6.7のtest_store_sql.pyの流儀)。`CAST(:x AS ...)` 形式が
   保たれていること・ORDER BYに第2キー `i.id ASC` があること・LIMITが50である
   こと・ON CONFLICTの列指定を、試験コードを開いて確認する

4. `uv run pytest tests/unit/matching/test_matching_runner.py -v` を実行する(4件。
   ws-4でLayer 3呼び出しの試験が1件加わりました)。スタブのconnと関数を差し込んで、
   skipのとき検索と記録が1回も呼ばれないことを確かめる試験です。何を差し替えて
   いるかを読むと、11.7の「関数を選べる」設計が試験にも効いているのが分かります

5. `rg -n "HNSW|<=>" backend/src/latch/worker/matching/` を実行する。
   コード上にHNSWという言葉が(コメントを除けば)索引定義として現れないこと、
   `<=>` が ORDER BY と similarity の2箇所だけに現れることを確認する。
   索引自体は `backend/alembic/versions/` の0001マイグレーションで作られた
   もので、この単位が新しく作っていないことを確かめる

## 11.9 この章の再統合

- マッチングは5層の漏斗。Layer 1(SQLで落とす)とLayer 2(意味で並べる)が
  前半。コストと確実性の住み分け: 落とせる相手はSQLで、残りを評価する価値の
  ある順に並べて50件へ絞る
- Layer 1の8条件は1つのWHERE文字列に集約され、Layer 2が同じ文字列を使う。
  試験と本番で条件の定義が1つ=乖離がない。時間は半開区間の交差、距離は
  半径の和でのST_DWithin(メートル)、予算は低いほうの上限が500円未満なら除外
- cosine類似度はベクトルの向きの近さ(1〜−1)。pgvectorの `<=>` で距離を測り、
  Layer 1条件込みの1本のSQLで「絞って並べて上位50件」を一度に行う
- 同点はintent_id昇順で決定的に崩す。この第2キーがあるためHNSW索引は使えず
  逐次ソートになる——決定性を機能要件として優先し、速度の最適化は負荷試験の
  計測が先
- 通過ペアはmatch_candidatesへ。UUIDの大小でペアを正規化し、
  UNIQUE(a, b, av, bv)が二重生成を防ぎ、同一バージョン組の再評価は
  retrieval_scoreだけ塗り替えるUPSERT。statusの遷移は後続の層の担当
- 起点に不備(非active・embedding NULL等)があれば検索前に理由つきで抜ける。
  関数はDB接続とClockだけを引数に取り、Workerのイベント経路に依存しない。
  配線は次の単位が選ぶ(そしてws-4で、Stage1への同乗が選ばれた——第12章)

保存されたIntentがベクトルになり(第10章)、そのベクトルで候補が選ばれ、帳簿に
乗るまでを読みました。帳簿に乗った候補は、まだ「意味が近い順に並んだ50組」に
すぎません。この組をさらに対象を絞り込み点数づけるLayer 3と、embedding_completed
からこの章の関数を呼ぶ配線——読者の手持ちは、もうその全部の前提です。

## 11.10 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| ファネル(漏斗) | 上から入れた対象を層ごとに絞り込んでいく工程のたとえ |
| Hard Filter | SQLとPostGISだけの例外ない除外判定(Layer 1) |
| Candidate Retrieval | 意味の近さで候補を並べて上位を取る層(Layer 2) |
| 半開区間 | 始点を含み終点を含まない区間 [s, e)。時間の交差判定の基礎 |
| ST_DWithin | 「2地点の距離がr以内」を判定するPostGISの関数 |
| geography型 | PostGISで「地球の表面の位置」を表す型。距離がメートルになる |
| CASE式 | SQLで「条件によって返す値を選ぶ」式 |
| cosine類似度 | 2つのベクトルの向きの近さ。1(同じ向き)〜0(無関係)〜−1(逆向き) |
| cosine距離 | 1−類似度。pgvectorの `<=>` が返す値(0=同一) |
| pgvector | PostgreSQLへベクトル検索を足す拡張。`<=>` 演算子とHNSW索引を持つ |
| HNSW | 近似近傍探索の索引。速いが、選ばれる行に実装依存の揺れが入り得る |
| 逐次ソート | 全行の距離を実際に計算して並べ替える、遅いが正確で決定的なやり方 |
| window関数 | 行ごとに集計結果を添えるSQLの機能(COUNT(*) OVER ()) |
| K_v(=50) | Layer 2が次層へ渡す出力数の上限(06 §8 D-24)。本文の `K_VECTORS` |
| UPSERT | なければINSERT、あればUPDATEを1文で行うSQLの型(ON CONFLICT) |
| EXCLUDED | UPSERTで「今INSERTしようとしていた新しい行の値」を指す語 |
| 正規化(a<b) | ペアのUUIDを大小で並べ、どちらの視点でも同じ1行に落とす約束 |
| UNIQUE(a, b, av, bv) | ペア×バージョン組の一致で二重生成を防ぐmatch_candidatesの制約 |

## 11.11 確認問題

1. マッチングを1つの賢い処理で行わず、5層の漏斗に分けた理由を、コストと
   確実性の観点から説明してください
2. 時間の交差判定が「20-23時と23-翌2時」を重ならないと判定する理由を、
   半開区間の2つの不等式で説明してください
3. 距離の判定で「2人の半径の和」をしきい値にする理由と、`::geography` が
   付いている理由を説明してください
4. ペア予算がNULL(無指定)のときに落とさない設計の理由を、CASE式の挙動と
   ともに説明してください
5. ORDER BYに `i.id ASC` の第2キーがあるとHNSW索引が使えなくなる理由と、
   それでも決定性を選んだ理由を説明してください。「最適化は計測が先」という
   このプロジェクトの流儀が、この判断のどこに現れていますか
6. match_candidatesのUNIQUE(a, b, av, bv)の4列がそれぞれ何を守っているか、
   「同一イベントの2回投入」と「内容更新後の再評価」と「同一バージョン内の
   再評価」の3つの場面で説明してください
7. 起点の検証が11.7の6条件をすべて検査する理由を、「保存の経路を信用し
   すぎない」と「検索の前の最適化」の2つの性格に分けて説明してください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった部分が、
読み返すべき節です)
