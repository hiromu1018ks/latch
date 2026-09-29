# M2 ws-7(グループマッチ Group Search)設計メモ

- 作成: 2026-09-29(agent1)
- 前提: M2 ws-1〜ws-6 マージ済み(最終 8804a13+19150b4・マージ後main test-ci 1030 passed)。
  実行wave上の後続はws-8(ws-1〜ws-7依存)で並走なし
- 参照仕様: 06 §5・§7〜§8(D-06・D-24)・§1・§9 / 05 §2・§4(D-06中間表現)・§6 / 01 §15〜§16 /
  07 §2 / 02 §4(#18) / 03 §5・D-05・D-07・D-08 / 10 §4.6
- 本単位は外部SDKを扱わない(純計算+DB+Redis+既存Layer 4経路の再利用)。context7確認は不要
- ws-6からの引継ぎ(I-1・ws-6-report引継ぎ節): Layer 5で「peer読取失敗行がlatch_score計算済みの
  まま再選択されない」仕様の空白。ws-7のPool選択・集合構成で同種の穴を生まないよう、
  §2.5で構造的に防ぐ(計算と生成物の同一トランザクション化)+ §2.7-4で1対1側の改修を提案

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

5つの部品を実装する。スコープは「グループ候補生成〜集合の提案化(通知)まで」で、
回答系(POST response・全員YES成立・競合クローズ)はM3(スーパーバイザー追記5)。

1. **GroupEngine(生成)**: 候補Pool構築(同一Bucket・地域・カテゴリ・Layer 3通過・上限15・
   cheap_score降順)→ 貪欲法(種max>=3+Hard互換追加・3〜4人・作成user_id相異)→
   group_candidates記録 + 全ペアmatch_candidates生成
2. **K_j=8配分の拡張**: 1対1最低4回保証+残り最大4回をグループ構成ペアへ
   (未判定ペア継続優先→新規)。layer4.select_jev_targets の拡張(ws-5が用意した拡張点)
3. **GroupEngine(集約)**: aggregate_score = H × min over ペア(MutualScore) × C の計算・
   閾値0.80判定・latches生成(集合・group_candidate_idつき)・通知(aggregate降順1集合のみ)
4. **既存部品の共存改修**: LatchEngine・layer4の1対1処理からグループ所属ペアを除外・
   try_promote/notificationの|S|人対応・stage1の削除処理へgroup_candidates無効化を追加
5. **未判定ペアの継続**: 集合はgroup_candidates.status=candidateで保持し、未判定ペアを
   次の再評価(30分Bucket・catch-up)でのJev予算の最優先対象にする

マイグレーションは **0005を1本追加**(group_candidates部分UNIQUE索引 — §2.8・承認事項)。
依存追加なし。ws-7はマイグレーション追加単位のため共有ci-dbの運用ルール1・2が適用され、
unit試験で開発を進めtest-ciはスーパーバイザー検証時(計画書へ明記)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | 全組み合わせ探索は行わない。候補Poolを小さくしてからGroup Searchする。MVPでは最大4人・候補Poolは最大15人(06 D-24)・同一地域・時間帯・カテゴリ内に限定 | 01 §15・06 §7 |
| 2 | 候補Poolは「同一時間Bucket・地域・カテゴリでLayer 3を通過したIntent群」。Pool内のIntentをcheap_score降順(タイブレークintent_id昇順)に走査して種を選び、種のparticipants.max >= 3ならHard互換(人数範囲の共通包含、時間・距離・年齢(飲酒・§2)・自己除外=集合内の作成user_idが互いに相異。v0.4で可視性は除外)なIntentをcheap_score順に追加して3〜4人の集合を構成する | 06 §7 |
| 3 | 集合Sの成立条件は \|S\| ≧ max(min_i) かつ \|S\| ≦ min(max_i)(全員の人数条件を満たす) | 06 §7 |
| 4 | 構成した集合をgroup_candidates(05)へ記録し、全ペアのmatch_candidatesを評価世代付きで生成。集合のJev判定は§5配分規則に従い、未判定ペアが残る集合はstatus=candidateのまま提案対象から外す | 06 §7 |
| 5 | D-24: Kは「その層から次層へ渡す出力数の上限」。Vector K_v=50・Cheap Judge K_c=20・Jev K_j=8(回数ベース)・**グループPool上限15**。切り詰めは各層スコア上位(Vector=類似度・Cheap Judge=cheap_score・Pool=cheap_score)・同点はintent_id昇順 | 06 §8 D-24 |
| 6 | K_j=8配分: (1)1対1候補(K_c=20)をcheap_score降順で上位4件判定(1対1最低枠保証) (2)残り最大4回をグループ集合の構成ペアにcheap_score降順で割当 (3)全ペアの判定が揃わない集合は当該イベントで提案化せずstatus=candidate保持・未判定ペアを次の再評価でのJev予算の**最優先**対象とする(4人集合は高々2回の再評価で確定) (4)割当順序は1対1最低4回を確保したうえで、残りを「前回未判定ペアの継続(優先)→新規グループ集合のペア」。グループ由来ペアが存在しなければ残り枠を1対1へ繰り上げ(この場合の1対1実効上限はK_j=8) | 06 §5 |
| 7 | D-06 集約規則: aggregate_score = H × min over ペア(MutualScore) × C。集合内全ペアのMutualScoreの最小値を採る。通知閾値は1対1と同じ0.80(別閾値を設けない) | 06 §8 D-06 |
| 8 | D-06 通知順序: 同一Poolから複数の成立可能集合があった場合、aggregate_score降順・同点は集合サイズの小さい順・さらに同点ならintent_id辞書順に順位付け、**最上位の1集合のみを通知**。その提案が閉じた後に次の集合を評価して通知できる | 06 §8 D-06・01 §15 |
| 9 | D-06 成立確定条件: 集合全員のYESが必要(必要人数は\|S\|と一致・部分成立なし)。**回答期限時に必要人数が揃っていなければexpired** — 回答系はM3。ws-7は提案化まで | 06 §8 D-06 |
| 10 | グループ集合もD-07同一手順: group_candidates.prev_aggregate_score(05)と新aggregate_scoreの差(\|Δ\|≧0.05)でスコア変化を判定。新評価世代(バージョン組変化)はprev値を持たず無条件に変化あり | 06 §10 |
| 11 | group_candidates: intent_ids uuid[](3≦長≦4・INSERT前に全Intentのuser_id相異を検査)・member_scores jsonb(メンバー間・集合評価の結果参照。再評価で上書く)・aggregate_score・prev_aggregate_score・status(candidate/proposed/closed) | 05 §2 |
| 12 | D-06中間表現(05 §4): メンバー間のペア評価は**既存のmatch_candidatesを再利用**する(メンバー全組み合わせでペアレコードを生成し、group_candidatesから参照)。成立時はlatches.group_candidate_idで紐付ける。match_candidatesは2 Intent固定のままペア評価・Calibrationの単位を保存 | 05 §4 |
| 13 | latches: intent_ids(2≦長≦4)・group_candidate_id(FK→group_candidates・グループ成立の場合のみ)・proposal・score・status・response_deadline・expires_at=参加Intentのexpires_at最小値 | 05 §2 |
| 14 | proposal構造はvisibility分岐(05 §2): summary_only全員→全フィールド、hidden_until_matchを含むならheadcountとmatch_levelのみ。category_secondaryは「集合の種(Intent)の値を採る」・budget=ペア予算=参加Intentのbudget_maxの最小値(NULLは無視)・headcount=集合サイズ\|S\| | 05 §2 |
| 15 | 削除Eventは「当該Intentを含む候補・保留の無効化(Layerを経ない)」 | 06 §1・§9 |
| 16 | K上限裏付け試験(G2): 密集配置で Vector ≦50 / Cheap Judge ≦20 / Jev ≦8回 / **Pool ≦15** が記録で守られ、切り詰めが決定的(同点intent_id昇順)。同一Poolから複数集合を発生させ、proposedになった集合がaggregate_score降順の1つだけであることをgroup_candidatesの記録で確認 | 10 §4.6 |
| 17 | participantsのParser出力: 「2〜4人くらい」→min=2,max=4。**「3人以上なら」→min=3,max=4**。[hard] participants: 2〜4人 | 07 §2 |
| 18 | 受入#18: 最大4人のグループLATCHを成立できる(3〜4人のIntentセットで必要人数のYESを揃える)。検証はM3(回答系)だが、ws-7はmin=3のIntentが種・メンバーとして機能する下地を作る | 02 §4 |
| 19 | Layer 1の人数条件は**1対1専用**: 「2 ∈ [min_i, max_i] が双方」。グループ側の人数条件は§7の共通包含 | 06 §2 |
| 20 | 層別予算: Layer 5+通知送信 ≤2秒。Layer 4 ≤5秒。本単位の追加処理(Pool検索・貪欲法・集約)はDB読取+純計算でLayer 5枠に収まる | 06 §1 |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

- `worker/matching/layer4.py`: `JevCandidateRow.pair_kind`(コメント明記のws-7拡張点)と
  `select_jev_targets`(ws-5時点は恒等+上限再適用)を本単位が拡張。
  `hard_constraint_holds`・`close_broken_pairs`・`jst_day_start` は再利用
- `worker/jev.py`(**JevWorker**): `handle(intent_id)` は起点非依存IF。
  `select_jev_targets(rows)` でK_j件に確定→ペア毎評価。本単位はシグネチャへ
  グループ文脈を渡す拡張のみ(§2.4)
- `worker/matching/runner.py`: `run_candidate_retrieval`(L1〜3+UPSERT)。
  **1対1の検索としては変更しない**。グループPool検索は別SQL(§2.2)
- `worker/matching/layer1.py`: `LAYER1_WHERE`(人数行込み)。本単位は人数行を
  分離した `LAYER1_WHERE_BASE` への最小分割のみ(既存参照は文字列不変・§2.2)
- `worker/matching/layer3.py`: `cheap_score`・`rule_score`・`vocab_overlap` は純関数。
  Pool構築・メンバー間ペアのcheap_score計算にそのまま呼ぶ
- `worker/matching/candidates.py`: `upsert_candidate`(Origin型)。本単位は
  ID・version直指定の `upsert_pair` を同ファイルへ追加(§2.3)
- `worker/matching/latch_engine.py`(**LatchEngine**): docstring明記のws-7拡張点
  (latches生成・proposal・drain・try_promoteは1対1構成・intent_ids正規化)。
  `try_promote` の|S|人対応・選択SQLのグループ除外(§2.7)
- `worker/matching/latch_calc.py`: `d07_allows`・`response_deadline`・`match_level`・
  `bucket_start`・定数群。集合側も同一関数を使う
- `worker/matching/proposal.py`: `build_proposal`(origin/peer 2者)。本単位は
  `build_group_proposal` を追加(§2.7)
- `worker/main.py`: `_kick_jev`(JevWorker→LatchEngine直列)・`_run_direct_pipeline`。
  両方のチェーンへGroupEngineを挿入(§2.1)
- `worker/stage1.py`: `_CLOSE_CANDIDATES`(削除Eventの候補無効化)。
  group_candidates無効化を追加(§2.7)
- alembic 0004 = head。group_candidatesテーブルは0001で作成済み
  (CHECK(cardinality BETWEEN 3 AND 4)・member_scores jsonb・UNIQUE索引なし)
- 0004の部分UNIQUE索引 `uq_latches_intent_ids_open` はintent_ids配列に効くため、
  **グループlatches(3〜4要素)の同一メンバー集合重複防止にもそのまま効く**(ws-6設計§2.3
  「グループ(ws-7)にも同一制約がそのまま効く」どおり)

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

1. **回答API・全員YES成立・部分YESの扱い・競合クローズ・matched遷移・解散復帰**
   (M3-1/M3-2・D-06成立確定条件・10 §4〔staging〕)。本単位はlatchesの
   candidate/proposed/expired(75分・昇格)まで。responsesは常に'[]'のまま
2. **expiry_sweeper**(M3-3)。candidate(集合・保留)のexpires_at経過によるexpiredは
   sweeper担当。group_candidatesの掃除(放置されたcandidate)も期限切れEvent
   (stage1)とsweeperが回収する(§2.9)
3. **FCM・お知らせUI・通知文テンプレート**(M3-5)。notificationsテーブルへの
   レコード生成まで(ws-6と同じ扱い。グループ提案は type='proposal' を共用)
4. **circuit breaker・E2Eハーネス・障害注入・K上限裏付け試験の実施**(ws-8・G2)。
   本単位は試験コードでPool≦15の記録が取れる状態を作る(§4.2)
5. **0時リセットジョブ**(M3-4)。ws-6と同じ扱い(drainが評価経路のたびに回収)

## 2. 実装方式の選択と推奨

### 2.1 GroupEngineの実行位置 — 推奨: キックチェーンへの直列挿入(案A)

グループ処理は「Pool構築・集合生成(Layer 3の直後が自然)」と「集約・latches生成
(評価完了後)」の2段に分かれる。実行位置の3案:

| 案 | 内容 | 評価 |
|---|---|---|
| **A(推奨)** | `_kick_jev` 内のチェーンを **GroupEngine.handle(生成)→ JevWorker → LatchEngine → GroupEngine.finalize(集約)** とする。`_run_direct_pipeline` も同一チェーン(ヘルパー `_run_post_retrieval` を共通化) | 生成したペアが**同一イベントのK_j配分**(引用#6)に乗り、初回投入の1連の流れで集合の一部まで評価できる。層別予算(引用#20)を1キック内に閉じる。LatchEngine(1対1)→GroupEngine(集合)の順で、1対1優先の提案が先に確定する |
| B | GroupEngineをLatchEngineの後に集約し、生成も評価後に回す | 生成ペアが当該イベントのK_jに乗らず、Jev評価が次の評価(最長30分後)まで遅れる。初回提案レイテンシの要件(06 §1)に不利 |
| C | GroupEngineを独立したPub/Sub Consumerにする | Worker 1構成で消費者を増やない(ws-6案Cと同理由)。embedding_completedの二重購読はat-least-onceの二重経路で冪等論証が複雑化 |

**採用: 案A。** チェーンの骨格:

```text
_kick_jev(embedding_completed) / _run_direct_pipeline(直接投入):
    run_candidate_retrieval(L1〜3・1対1)           ← 既存
    group_ctx = GroupEngine.handle(intent_id)        ← 本単位: Pool構築・集合生成・全ペアUPSERT
    JevWorker.handle(intent_id, group_ctx)           ← 拡張: K_j配分(§2.4)
    LatchEngine.handle(intent_id)                    ← 拡張: 1対1のみ(グループペア除外・§2.7)
    GroupEngine.finalize(intent_id)                  ← 本単位: 集約・latches生成(§2.5)
```

各部品は起点非依存IF(JevWorker・LatchEngineと同型)。未注入なら何もしない
(ws-1資産の試験互換・`_kick_embedding` と同一規律)。group_ctxは生成物
(新規group_candidatesのid・新規ペア行idの集合)で、JevWorkerの配分順序
(継続→新規)の判定に使う(§2.4)。

### 2.2 候補Pool構築 — 人数緩和検索(承認事項1)

**論点。** 引用#2のPoolは「Layer 3を通過したIntent群」。ところが既存Layer 1〜3は
人数条件「2 ∈ [min_i, max_i] が双方」(引用#19・1対1専用)を含むため、
**participants_min >= 3 のIntent(「3人以上なら」→min=3・引用#17)は1対1パイプラインを
一切通過できず、そのままではPoolに永久に入らない**。引用#18(受入#18: 3〜4人の
Intentセットで成立)が達成できなくなる。

選択肢:

| 案 | 内容 | 評価 |
|---|---|---|
| A | Pool=1対1Layer 3通過行(match_candidatesのcheap_judge_score付き行)から選ぶ | 実装最小・既存行の再利用のみ。ただしmin>=3のIntentが種にもメンバーにもなれない。受入#18・07 §2のParser出力と不整合 |
| **B(採用)** | グループ専用のPool検索SQL(人数条件を緩和)を新設し、Layer 3のcheap_score計算(純関数)をPool構築時に適用する | min=3/max=4の典型グループ希望Intent(引用#17)がPoolに入る。「Layer 3を通過」=「Pool構築時にLayer 3と同一の計算と切り詰め(cheap_score降順・上限)を通過」と読む |

**採用: 案B。** 前提として `layer1.py` を最小分割する(既存の `LAYER1_WHERE` を
参照する全コードで文字列は不変・機械的追随のみ):

```python
# layer1.py(分割後・LAYER1_WHEREの値は変更なし)
LAYER1_WHERE_BASE = "…(自己除外・時間交差・距離・ペア予算・ブロック・カテゴリ・年齢)…"
ONE_ON_ONE_PARTICIPANTS = "AND i.participants_min <= 2 AND i.participants_max >= 2"
LAYER1_WHERE = f"{LAYER1_WHERE_BASE} {ONE_ON_ONE_PARTICIPANTS}"
```

Pool検索SQL(group_engine.py。起点との互換はBASEで保証・人数とBucketがPool条件):

```sql
SELECT i.id, i.version, i.user_id, i.time_start, i.budget_max, i.structured_data,
       1 - (i.embedding <=> CAST(:origin_embedding AS vector)) AS similarity
FROM intents i JOIN users u ON u.id = i.user_id
WHERE {LAYER1_WHERE_BASE}
  AND i.participants_min <= 4
  AND i.participants_max >= 3          -- 3人集合か4人集合のメンバーになり得る
  AND i.time_start >= CAST(:bucket_start AS timestamptz)
  AND i.time_start <  CAST(:bucket_end AS timestamptz)   -- 起点と同一30分Bucket
ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC, i.id ASC
LIMIT 50
```

- 人数緩和条件 `min <= 4 AND max >= 3` は「3または4が [min, max] に含まれる」と同値。
  min=5(MVP上限超過)とmax=2(2人固定)をPool段階で落とす。人数の**最終**判定は
  貪欲法(共通包含・引用#3)と集約時のH再検証(§2.5)が担う
- Bucketは `latch_calc.bucket_start(origin.time_start)`(ws-6 §2.8と同じ
  「time_startが属するBucket」解釈)。時間交差条件はBASE内で並行して効く
  (交差しない候補は追加時に弾かれるだけなので、Pool枠を浪費しないよう両方入れる)
- 取得上限50はK_vと同値。「Vector検索 K_v=50 → cheap_score → Pool 15」という
  D-24(引用#5)の出力上限の鎖と同じ形にする
- 取得後、Pythonで `layer3.cheap_score`(類似度×ルール×語彙)を起点とのペアで計算し、
  **cheap_score降順・同点intent_id昇順で上位15件**をPoolとする(引用#5)
- 1イベントでHNSW検索が2回(1対1Layer 2+Pool検索)走るが、両方とも索引を使う
  純DB検索でコスト増は無視できる(引用#20の予算内・実装時確認事項 §5-12)

### 2.3 貪欲法と集合生成 — 種=起点・Pool走査は起点を先頭に(承認事項2)

**種の選び方の論点。** 引用#2は「Pool内のIntentを走査して種を選ぶ」と書き、起点の
役割に触れない。素直に読むと種はPool内のcheap_score最大者になり、起点を含まない
集合を起点の評価処理が作ることになる。これは(1)他メンバーの評価処理と同じ集合を
重複生成する(K_jとDBを浪費)(2)起点自身のグループ成立機会を失う、の両方で不合理。

**採用: 種=起点。** 起点(評価処理の主)がPool走査順の先頭に立つ(起点の順位は
Pool最大と同値扱い)。起点のparticipants_max >= 3 がGroup Searchのトリガー条件で、
起点がmax<3(2人固定)ならPoolに参加できない(引用#3で|S|>=3を含まない)ため、
Group Search全体をno-opにする(構造化ログ)。イベント駆動の原則
「起点の評価処理は起点に紐づく候補を評価する」(01 §11・§16)とも整合する。

**Hard互換行列(事前計算)。** 追加候補のHard互換(時間・距離・年齢・ブロック・
user相異)は「現集合の**全メンバー**との」成立が要る(引用#2)。ペア毎にSQLを発行
すると貪欲法の途中で多数回になるため、Pool∪{起点}(最大16 Intent・120ペア)の
互換を**1 SQLで事前計算**してから純関数の貪欲法へ渡す:

```sql
SELECT i1.id AS a_id, i2.id AS b_id,
       1 - (i1.embedding <=> i2.embedding) AS similarity
FROM intents i1 JOIN intents i2 ON i1.id < i2.id
WHERE i1.id = ANY(CAST(:ids AS uuid[])) AND i2.id = ANY(CAST(:ids AS uuid[]))
  AND i1.status = 'active' AND i2.status = 'active'
  AND i1.time_start < COALESCE(i2.time_end, i2.time_start + interval '3 hours')
  AND i2.time_start < COALESCE(i1.time_end, i1.time_start + interval '3 hours')
  AND ST_DWithin(i1.geo_center, i2.geo_center,
                 CAST(i1.geo_radius_m + COALESCE(i2.geo_radius_m, 1000)
                      AS double precision))
  AND i1.user_id <> i2.user_id
  AND NOT EXISTS (blocks の双方向検査)
  AND (飲酒年齢: (i1.alcohol_involved OR i2.alcohol_involved) なら双方20歳以上)
```

返った行=互換ペア(対称条件なのでi1.id < i2.idの一方向だけ取る)。
similarityも同時に取り、メンバー間ペアのcheap_score
(rule_score・vocab_overlap は各行の列から純関数で計算)に使う(§2.3末尾)。

**貪欲法(純関数 group_calc.build_groups)。** 入力: Pool(cheap_score降順)・種(起点)・
互換行列・各Intentのparticipants_min/max・user_id。手順:

1. 未使用集合 R = Pool ∪ {種}(種は先頭)
2. Rを走査し、最初の「種になれる」Intent(max >= 3)を種 s とする。
   現集合 S = {s}
3. Sの残り候補をcheap_score順に走査し、追加候補cが
   (a)現集合の**全メンバー**と互換行列で互換(b)user_idが既メンバーと相異
   (行列に含む)(c)人数の見込み: c込みで [max(min_i), min(max_i)] と [3, 4] の
   共通部分が空でない — を満たせばSへ追加
4. \|S\| >= 3 かつ \|S\| >= max(min_i) かつ \|S\| <= min(max_i)(引用#3)を満たしたら
   集合として**確定**(3人で成立したら4人へ拡張しない。D-06の同点順「集合サイズの
   小さい順」(引用#8)と整合し、aggregate=minの性質上、人数増はスコアを下げる方向)
5. \|S\|=4 でも引用#3を満たさなければこの集合は不成立(生成しない)
6. 確定した集合のメンバーをRから除き、Rに種になれるIntent(max>=3)が残っていて
   3人以上集まり得れば手順2へ戻る(**複数集合**。引用#16「同一Poolから複数集合を
   発生させる」G2試験の下地)

**集合の記録(1集合1tx)。** group_candidatesへINSERT
(intent_idsは**sorted正規化**・member_scoresは後述・status='candidate'・
ON CONFLICT は§2.8)+ 全ペアのmatch_candidatesをUPSERT
(種×メンバーはPool構築時に既にUPSERT済みのため、**メンバー間ペアのみ追加**。
`candidates.upsert_pair`(新規。ID・version直指定・a<b正規化・評価世代=現行version)に
similarity(行列由来)とcheap_score(純関数計算)を載せる)。
user_id相異(引用#11)は、INSERT前にPythonで全メンバーのuser_idの重複を検査して
強制する(互換行列のuser相異条件と二重防御・05 §2の規定どおり)。

**member_scores jsonb の内容(設計確定)。** 最低限の参照情報として
`{"seed_id": "<uuid>", "versions": {"<intent_id>": <version>, …}}` を格納する。
versionsは集合構成時点の各Intent version(=構成ペアの評価世代)。集約時の世代判定
(§2.5)とD-07の世代変化検出(引用#10)に使う。ペア評価の実値はmatch_candidates行が
持つため、member_scoresに評価値を複製しない(二重管理を避ける・05 §2
「結果参照」の字義どおり参照情報のみ)。

### 2.4 K_j=8配分の拡張(1対1最低4回+残り4回・継続優先)

`layer4.select_jev_rows` と `select_jev_targets` を拡張する(ws-5がコメントで
用意した拡張点)。

**select_jev_rows の拡張。** 起点に紐づく未評価ペアの選択SQLに、グループ所属判定の
列を追加する。LIMIT 8は撤去する(配分は純関数側で行うため。K_j=8は実行回数上限で
あり選択件数ではない・引用#6。起点紐づき未評価ペアは高々K_v×α件でコスト無視できる):

```sql
SELECT mc.id, mc.intent_a_id, mc.intent_b_id, mc.intent_a_version,
       mc.intent_b_version, mc.cheap_judge_score, mc.status, mc.skip_reason,
       EXISTS (
           SELECT 1 FROM group_candidates g
           WHERE g.status IN ('candidate', 'proposed')
             AND g.intent_ids @> ARRAY[mc.intent_a_id, mc.intent_b_id]
       ) AS is_group
FROM match_candidates mc
WHERE (…既存の起点version一致・jev_result IS NULL・status条件は不変…)
ORDER BY mc.cheap_judge_score DESC NULLS LAST,
         (…相手intent_id昇順・既存どおり…)
```

`intent_ids @> ARRAY[a, b]`(uuid[]包含)で「両方のIDを含む集合」を判定する。
1ペアが複数の開いている集合に属す理論上のケース({A,B,C}と{A,B,D}が両方開く)は
EXISTSで「いずれかに属す=グループ由来」と扱う(引用#12: ペア評価は共有資産)。

**JevCandidateRow** は `pair_kind` を実値で持つようにする
('one_on_one' | 'group'。dataclass既存フィールドを既定値から実値へ)。

**select_jev_targets の配分(純関数・06 §5引用#6の規則1〜4)。**

```text
入力: rows(cheap_score降順・pair_kind付き)・new_pair_row_ids(今回生成ペアの行id集合)
1. one_on_one の上位4件を選ぶ(最低枠保証・規則1)
2. 残り枠 = 8 − 選択済み件数
3. 残り枠を group ペアへ割り当てる。順序は
   (a) 継続: pair_kind=group かつ row_id ∉ new_pair_row_ids(既存集合の未判定ペア・規則4-b優先)
   (b) 新規: pair_kind=group かつ row_id ∈ new_pair_row_ids(今回生成した集合のペア)
   各グループ内はcheap_score降順・同点相手intent_id昇順
4. group ペアで残り枠が埋まらない場合、one_on_one の残りをcheap_score順で繰り上げる
   (上限8・規則4の繰上げ)
```

1対1の最低4回は継続評価があっても確保する(引用#6規則4-a)。

**JevWorker への文脈渡し。** `JevWorker.handle(intent_id, group_ctx=None)` とし、
group_ctx(新規ペア行idの集合)をselect_jev_targetsへ流す。main.pyのチェーンが
GroupEngine.handleの戻り値を渡す(§2.1)。既定Noneなら全グループペアを継続扱いにする
(下位互換・既存試験の機械的追随)。

**グループ所属ペアのH再検証。** `_H_RECHECK` はLAYER1_WHERE(人数込み)を使うため、
min>=3を含むグループペアを誤って不成立にする。`hard_constraint_holds` に
緩和版を追加する: `hard_constraint_holds(conn, origin, candidate_id, *, relaxed=False)`。
relaxed=Trueのとき人数条件を `min <= 4 AND max >= 3`(Pool条件と同一)へ差し替えた
`_H_RECHECK_GROUP` を使う。人数の最終判定は集約時のH再検証(§2.5)が担うため、
ここは「Jev予算を人数不成立ペアに浪費しない」ための緩い検査である。
JevWorker._evaluate は `row.pair_kind == 'group'` でrelaxed=Trueを渡す。

**close_broken_pairs の除外。** `_CLOSE_BROKEN`(jev_result済みevaluated行を
Layer 1人数込みで再検証してclose)がグループペアを閉じないよう、
WHEREにグループ所属除外(`NOT EXISTS` …select_jev_rowsと同一のEXISTSの否定)を追加する。
集合が閉じた(status=closed)後のペアは除外から外れ、1対1の通常扱いに戻る
(min>=3のペアは1対1としては不成立のためcloseされる・正しい扱い)。

### 2.5 集約とlatches生成(finalize)— I-1対策として計算と生成を同一txへ

`GroupEngine.finalize(intent_id)` は、起点が属する未完集合の集約を試みる。
JevWorkerの評価は複数イベントにまたがって進む(1イベントの残り枠は最大4回で
4人集合の6ペアは揃わない・引用#6規則3)ため、**各評価処理の末尾で**「全ペアの
jev_resultが揃った集合」を確定させていく。

**対象の検索(短tx読取)。**

```sql
SELECT g.id, g.intent_ids, g.member_scores, g.aggregate_score
FROM group_candidates g
WHERE g.status = 'candidate'
  AND CAST(:origin AS uuid) = ANY(g.intent_ids)
```

各集合について次を判定する(Python+SQL):

1. **世代判定。** member_scores.versions と各Intentの現行versionを照合し、
   どちらかが更新されていれば(世代変化・引用#10)集約値をリセットする
   (`aggregate_score = NULL, prev_aggregate_score = NULL, member_scores.versions = 現行組`。
   1対1の「新バージョン組はprevを持たない」(引用#10)と同じ状態を作る)
2. **全ペアの評価揃い。** 現行version組の全ペア行
   (\|S\| C 2 = 3人で3ペア・4人で6ペア)がすべて `jev_result IS NOT NULL` か。
   揃わなければ**何もしない**(status=candidate保持・引用#4。未判定ペアは次評価の
   Jev予算最優先=§2.4の継続枠で回収)
3. **H集合の再検証。** 人数の共通包含(引用#3: \|S\| >= max(min_i) かつ
   \|S\| <= min(max_i)、現行のmin/maxで再計算)と、互換行列(人数除き・§2.3と
   同一SQL)の全ペア成立。不成立なら **group_candidates.status='closed'**
   (集合のみ閉じる。構成ペアのmatch_candidates行は閉じない — ペア評価は共有資産
   で1対1または他の集合で使い得るため・引用#12)

**集約tx(1集合1tx・I-1対策の本体)。** 検査を通過した集合の
退避つきaggregate更新とlatches生成を**同一トランザクション**で書く:

```sql
-- (a) 退避つき集約更新(06 §10手順1〜2のgroup版)
UPDATE group_candidates
SET prev_aggregate_score = aggregate_score,
    aggregate_score = :score, updated_at = :now
WHERE id = CAST(:gid AS uuid) AND aggregate_score IS NULL
RETURNING id, prev_aggregate_score
```

```sql
-- (b) latches生成(0004部分UNIQUEへON CONFLICT・1対1の_INSERT_LATCHと同型)
INSERT INTO latches
    (intent_ids, group_candidate_id, proposal, score, status,
     response_deadline, expires_at, created_at)
VALUES (CAST(:ids AS uuid[]), CAST(:gid AS uuid), CAST(:proposal AS jsonb),
        :score, 'candidate', :deadline, :expires, :now)
ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed', 'partial_accept')
DO NOTHING
RETURNING id
```

- `score` = aggregate_score = H(=1) × min over ペア(MutualScore) × C(引用#7)。
  MutualScoreは各ペア行のjev_resultから `min(would_a_accept_b, would_b_accept_a)` を
  Pythonで計算(1対1と同じ式)
- latches生成時、group_candidates.status を 'proposed' へ更新する(集合の評価完結と
  提案化を記録・§2.8の遷移。latchesの個別status遷移はlatches.statusが管理)
- 閾値 `aggregate_score >= 0.80`(引用#7)未満の集合はlatchesを作らず
  group_candidatesはcandidateのまま(次評価でペア評価が変われば再計算対象。
  aggregate_score IS NOT NULLのため再計算しない点は§2.9の冪等表どおり)
- INSERT成功時のみ `latch_status_events(from_status=NULL, to_status='candidate',
  user_id=NULL)` を同一txでINSERT(1対1と同型)。ON CONFLICTで飛んだ場合は
  1対1の昇格判定と同様、既存開いている行がcandidateなら昇格
  (score/proposal/response_deadlineの3列更新)へ、proposed/partial_acceptなら
  スキップ(構造化ログ)
- D-07(引用#10): 新規INSERTと昇格の直前に `latch_calc.d07_allows` を集合の
  intent_ids(正規化配列)のlatches履歴で判定する。`_SELECT_LATCH_RESPONSES` の
  配列比較はuuid[]等値のためそのまま3〜4要素で動く

**I-1(引継ぎ)への回答。** ws-6の空白は「計算(latch_score退避UPDATE)だけ成功し、
tx外の読取失敗で生成物(latches)が作られないまま、`latch_score IS NULL` ガードにより
再選択されない」だった。ws-7は(1)失敗しうる読取(集合・ペア・Intent入力・geo・
D-07履歴)をすべて集約txの**前**に済ませる(2)計算(退避UPDATE)と生成物(latches INSERT・
status_events・group_candidatesのproposed遷移)を**同一tx**で書く — ことで、
「計算だけ成功して生成物なし」の中間状態を構造的に作らない。読取段階の失敗は
aggregate_score NULLのまま残るため、次の評価処理で自然に再選択される。

### 2.6 D-06通知順序 — メンバーが重なる開いている集合の最上位のみproposed化

引用#8「同一Poolから複数の成立可能集合があった場合、最上位の1集合のみを通知」。
Poolは評価ごとの一時物でDBに姿を持たないため、**「メンバーが1人でも重なる開いている
グループlatches群のうちaggregate降順・同点はサイズ小→intent_id辞書順の最上位のみ
proposed化する」**として実装する(設計確定・§5解釈記録)。

- 判定は `LatchEngine.try_promote` の内側(75分ルールの後・D-08の前)に入れる。
  `_SELECT_LATCH_FOR_UPDATE` に `group_candidate_id` の取得を追加し、
  `group_candidate_id IS NOT NULL`(=グループlatches)のときだけ重複上位チェックを
  行う:

```sql
SELECT l.id, l.score, l.intent_ids FROM latches l
WHERE l.status IN ('candidate', 'proposed', 'partial_accept')
  AND l.group_candidate_id IS NOT NULL
  AND l.id <> CAST(:self AS uuid)
  AND l.intent_ids && CAST(:my_ids AS uuid[])   -- 配列交差: メンバー重複
```

  自分より上位(比較順序: score降順→intent_ids配列長昇順→intent_ids辞書順)の行が
  あればproposed化せずcandidateのまま戻る。上位の行が閉じた後の再評価
  (drain・保留キュー)で次の集合が提示される(引用#8「閉じた後に次を評価」の回収経路)
- 過剰抑制の余地(別Pool由来で偶然メンバーが重なる集合)はあるが、重複提案による
  D-08上限の消費と選択の混乱を避けるというD-06の趣旨(06 §8根拠)の保守側で動く

### 2.7 既存部品への共存改修

1. **LatchEngine の選択SQL(グループペア除外)。** `_SELECT_TARGETS`(latch_score計算
   対象)に「開いている集合に属さない」条件(`NOT EXISTS` …§2.4と同一EXISTSの否定)を
   追加する。グループ所属ペアのlatch_scoreは書かない
   (aggregateはjev_resultのMutualScoreから直接計算するため不要。
   1対1評価済み(latch_scoreあり)のペアが後から集合に含まれるケースは自然に動く
   — 行は更新されず評価値が集約で使われる)
2. **try_promote・notifications の\|S\|人対応。** `_read_participants(a, b)` を
   idsリストへ一般化し、`_COUNT_DAILY_NOTIFICATIONS` の `user_id IN (u0, u1)` を
   `user_id = ANY(CAST(:users AS uuid[]))` へ、`_COUNT_OPEN_PROPOSED` は
   `@>` で既に配列対応済み。drainの `_DRAIN_CANDIDATES` は
   `ANY(l.intent_ids)` と `max(time_start)` 相関サブクエリが既に配列対応のため
   **無変更でグループlatchesも走査対象になる**(score>=0.80のcandidate行)。
   対象時刻はmax(time_start)・期限はmin(expires_at)が全員分に効く
3. **proposal の集合版。** `proposal.py` へ `build_group_proposal(members, score,
   area_name)` を追加する。membersは種を先頭に格納(dataclass `LatchIntentInputs`
   を共用)。visibility分岐は全員summary_onlyでなければ最小構成
   (headcount=\|S\|+match_levelのみ)。全フィールド側は
   time_summary=max(time_start)のJST `YYYY-MM-DD HH:MM`(ws-6 §2.5と同書式)・
   area_name=**全メンバーgeo_centerの平均点**の逆転ジオコーディング(ws-6「中点」
   規則の集合版)・category_secondary=種の値(引用#14)・budget=min(budget_max)
   (NULL無視)・match_level=match_level(aggregate)。格納禁止フィールドは
   構造上入らない(1対1と同一)
4. **1対1のI-1改修(承認事項4)。** ws-6の空白(§2.5冒頭)を本単位で解消する。
   `LatchEngine._evaluate_pair` のtx構成を「tx1(H再検証+退避つきlatch_score
   UPDATE)→tx外読取→tx2(latches)」から「**tx外読取(全材料)→tx1(H再検証+退避つき
   UPDATE+D-07判定+latches INSERT+status_events)**」へ変更する。H再検証は
   SELECT(読取)のため同一tx内で成立する。変更は小さく(読取の前倒しとtx境界の
   統合)、既存試験の期待する観測結果(latch_score・latches・events・notifications)
   は不変。試験1件を追加(§4.1: 読取失敗時にlatch_scoreがNULLのまま残り、復旧後の
   再handleでlatches生成まで完走する)
5. **stage1 の削除処理へgroup_candidates無効化を追加。** 削除Eventの
   `_CLOSE_CANDIDATES`(引用#15)と同じ箇所で、削除Intentを含むgroup_candidatesを
   closedへする:

```sql
UPDATE group_candidates
SET status = 'closed', updated_at = :now
WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
  AND status <> 'closed'
```

   削除Intentを含む集合は集約不能(全ペアの現行version組が揃わない)なため、
   stage1の時点で閉じるのが最も早い。latches側(開いているcandidate/proposed)は
   ws-6実装済みの削除処理・expiry経路とM3-3が回収する

### 2.8 group_candidatesの重複防止と0005(承認事項3)

**課題。** 集合内の複数メンバーの評価処理(いずれも種になり得るmax>=3)で同一集合が
再構成され得る。at-least-onceのduplicate EventでもGroupEngine.handleは再実行される。
group_candidatesはバージョン組カラムを持たず「同一メンバー集合は常に1行を更新する」
設計(§2.3)のため、INSERTの重複をDBで防ぐ必要がある。

**採用: 開いているgroup_candidatesは同一intent_idsで1行、を強制する部分UNIQUE索引。**

```sql
-- 0005_group_candidates_open_unique.py
CREATE UNIQUE INDEX uq_group_candidates_intent_ids_open
    ON group_candidates (intent_ids)
    WHERE status IN ('candidate', 'proposed');
```

- INSERTは `ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed')
  DO NOTHING`。行数0なら「開いている同一集合が既にある」ため生成をスキップ
  (既存集合は§2.5のfinalizeが回収する。構造化ログ)
- group_candidatesのstatus遷移(設計確定): **candidate**(生成・評価中・aggregate未確定)
  → **proposed**(集約が閾値を超えlatchesを生成した時点)→ **closed**(H再検証不成立・
  削除Event・人数不成立)。閾値未満でlatchesを作らなかった集合はcandidateのまま
  (aggregate_scoreは入る)。closed後の再構成(再評価で同一メンバーが再度最良集合に
  なった場合)は新行として作れる
- intent_idsはsorted正規化(`normalize_pair` と同じ規律のPython側ソート)。
  latches(0004)と同型の部分UNIQUEのため、閉じた集合の履歴は複数行保持できる
  (D-07の履歴検査・集計の供給源)

### 2.9 例外方針・冪等性・トランザクション分割(JevWorker/LatchEngineの表と同型)

| 状況 | 扱い |
|---|---|
| 起点が読めない(削除・非active・embedding NULL等) | no-op・構造化ログ(`group origin no-op` — JevWorker/LatchEngineと同型) |
| 起点のparticipants_max < 3 | no-op(グループ非対象・構造化ログ) |
| Poolが空(緩和検索0件) | no-op |
| 集合INSERTの競合(開いている同一集合あり) | ON CONFLICT DO NOTHING → スキップ+構造化ログ。既存集合はfinalizeが回収 |
| ペアUPSERT失敗 | 伝播(再配信/次周期で回収。UPSERTは冪等) |
| 未判定ペアが残る集合(全ペアのjev_result未揃い) | 何もしない(status=candidate保持・引用#4。次評価のK_j継続枠が最優先で回収) |
| H集合再検証不成立(人数・互換) | group_candidates.status=closed(構成ペア行は閉じない) |
| 集約材料の読取失敗(expires_at NULLのメンバー等) | finalize中止(aggregate_score NULLのまま)。次の評価処理で再選択される(I-1対策・§2.5) |
| latches INSERTの競合(開いている行あり) | 1対1と同型: candidateなら昇格判定(D-07を通ればscore/proposal/deadline更新)・proposed/partial_acceptならスキップ |
| aggregate計算済み集合の再finalize | `aggregate_score IS NULL` ガードで自然スキップ(世代変化時のみリセットされて再計算) |
| D-06上位チェックで上位の重複集合あり | proposed化せずcandidateのまま(§2.6。上位の行が閉じた後のdrainで提示) |
| 逆転ジオコーディングで地物なし | proposal.area_name=null で続行(ws-6と同型) |
| **DB書き込み失敗** | **伝播**。`_kick_jev` 経由ならackなし再配信→再実行、Runner経由なら次周期60秒後(各部のガードで冪等) |

トランザクション分割: (1)Pool検索・互換行列(短tx読取) (2)集合生成(1集合1tx:
group_candidates INSERT+メンバー間ペアUPSERT+user_id相異検査) (3)集約材料読取
(短tx) (4)集約tx(退避つきaggregate UPDATE+latches INSERT+latch_status_events+
group_candidatesのproposed遷移・§2.5) (5)try_promoteはLatchEngineの既存構成
(1tx)。長いトランザクションを作らず、計算と生成物を同一txに置く(I-1対策)。
LLM呼び出しはJevWorker内にしかなく、本単位の追加処理はDB読取と純計算のみ
(geo逆転は読取のみ・tx外)。

### 2.10 採用しないもの(YAGNIによる切り捨て一覧)

1. **PoolのDB永続化(中間表現)** — Poolは評価ごとに構成する一時物。group_candidates
   (集合)とmatch_candidates(ペア)だけを永続化する(06 §1「中間表現を持たない方針」
   の継承)。D-24のPool≦15記録(引用#16)はgroup_candidates生成時の構成ログ
   (構造化ログ `group pool built pool_size=…`)と試験の観察で担保
2. **グループ候補のnearby_also存在通知** — 06 §6のnearby規定は1対1の記載で、
   集合への適用はdocsに明示がない。閾値未満の集合はlatchesを作らずcandidateのまま
   とする(1対1nearbyの analogue を作らない・§5解釈記録)
3. **group_candidatesへの評価世代(バージョン組)カラム追加** — 世代はmember_scores
   jsonb内のversionsで足りる(引用#10の世代変化判定に必要なのは比較元のみ)。
   カラムを増やすと05 §2の構造変更が発生する
4. **Pool構築の1対1Layer 2検索との統合(1クエリ化)** — 人数WHERE違いの2クエリは
   純DB検索でコスト小。統合はLayer 1/2の契約(1対1専用)を崩すためやらない
5. **貪欲法のスコア最適化(全組み合わせ近似・局所探索)** — 全組み合わせ探索禁止
   (引用#1)。決定的な貪欲法のみ
6. **第2集合以降の種を起点以外から選ぶ場合のPool上限管理の複雑化** — 未使用
   メンバーの走査継続(§2.3手順6)で足りる。Pool上限15とK_j制約が実行回数を
   天井に押さえる
7. **Redisによる集合状態管理** — 状態はすべてDB(group_candidates・latches)。
   冪等ガードもDB側(部分UNIQUE・aggregate_score IS NULL・条件付きUPDATE)
8. **マイグレーションでのmatch_candidatesへpair_kind列追加** — ペアの所属判定は
   group_candidates側のEXISTSで動的に行う(§2.4)。列を持つと「1ペアが複数集合に
   属する」ケースの整合管理が発生する。05 §2のスキーマ変更も不要

## 3. ファイル構成

### 3.1 作るもの(新規ファイル)

| ファイル | 内容 |
|---|---|
| `backend/src/latch/worker/matching/group_calc.py` | 純関数群: 貪欲法 `build_groups`(種選択・人数包含の逐次判定・複数集合・user相異)・`aggregate_score`(H×min(MutualScore)×C)・`group_target_time`(max(time_start))・集合正規化 `normalize_ids`(sorted)。定数 `POOL_LIMIT=15`・`GROUP_MIN=3`・`GROUP_MAX=4`・`POOL_SEARCH_LIMIT=50`・`ONE_ON_ONE_JEV_MIN=4`(layer4再利用) |
| `backend/src/latch/worker/matching/group_engine.py` | `GroupEngine`本体: `handle(intent_id)`(Pool検索・互換行列・集合生成tx群・group_ctx返却)・`finalize(intent_id)`(§2.5: 世代判定・全ペア揃い・H再検証・集約tx・try_promote呼び出し)。`_POOL_SEARCH`(§2.2)・`_PAIR_COMPAT`(§2.3)・`_INSERT_GROUP`・`_SELECT_PENDING_GROUPS`・世代リセット/退避つき集約UPDATE/latches INSERTの各SQL。モジュール属性経由でorigin/layer1(BASE)/layer3/candidates/latch_calc/proposal/latch_engineを呼ぶ(既存規律) |
| `backend/alembic/versions/0005_group_candidates_open_unique.py` | 部分UNIQUE索引 `uq_group_candidates_intent_ids_open`(§2.8) |
| `backend/tests/unit/matching/test_group_calc.py` | 純関数のunit(§4.1) |
| `backend/tests/unit/matching/test_group_engine.py` | GroupEngineのunit(test_worker_jev.pyと同型: engine/origin/layer系スタブ+モジュール属性差し替え) |
| `backend/tests/integration/test_matching_groupengine.py` | integration(§4.2) |

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更 |
|---|---|
| `backend/src/latch/worker/matching/layer1.py` | `LAYER1_WHERE` を `LAYER1_WHERE_BASE` + 人数行へ分割(値は不変・§2.2)。docstring更新 |
| `backend/src/latch/worker/matching/layer4.py` | (1)`select_jev_rows` へis_group列追加+LIMIT撤去(§2.4) (2)`select_jev_targets` 配分拡張(§2.4) (3)`hard_constraint_holds` へrelaxed引数と `_H_RECHECK_GROUP`(§2.4) (4)`_CLOSE_BROKEN` へグループ所属除外 (5)`JevCandidateRow.pair_kind` を実値化 |
| `backend/src/latch/worker/jev.py` | `JevWorker.handle(intent_id, group_ctx=None)`(§2.4)。`_evaluate` のH再検証で `row.pair_kind=='group'` → relaxed=True。既定Noneは下位互換 |
| `backend/src/latch/worker/matching/candidates.py` | `upsert_pair`(ID・version直指定・a<b正規化・similarity/cheap_score載せ)を追加 |
| `backend/src/latch/worker/matching/latch_engine.py` | (1)`_SELECT_TARGETS` へグループ所属除外(§2.7-1) (2)`_read_participants` のidsリスト化・`_COUNT_DAILY_NOTIFICATIONS` のANY化(§2.7-2) (3)`_SELECT_LATCH_FOR_UPDATE` へgroup_candidate_id追加+try_promoteへD-06重複上位チェック(§2.6) (4)`_evaluate_pair` のtx統合(I-1改修・承認事項4) (5)`try_promote` のpublic化(GroupEngineから呼ぶ)・docstring更新 |
| `backend/src/latch/worker/matching/proposal.py` | `build_group_proposal` 追加(§2.7-3) |
| `backend/src/latch/worker/stage1.py` | 削除処理へ `_CLOSE_GROUPS`(group_candidates無効化・§2.7-5)を同箇所追加 |
| `backend/src/latch/worker/main.py` | (1)`_kick_jev`・`_run_direct_pipeline` へGroupEngine.handle/JevWorkerへgroup_ctx/GroupEngine.finalizeのチェーン挿入(§2.1。共通ヘルパー `_run_post_retrieval`) (2)GroupEngine DI(LatchEngineインスタンスを注入) |
| 既存試験諸ファイル | 上記シグネチャ変更への機械的追随(handleの引数・SQL文字列ピン試験・期待値) |

### 3.3 触らないもの(明示)

- `worker/matching/runner.py`・`origin.py`・`layer2.py`・`layer3.py`・`latch_calc.py` —
  純関数・1対1検索は無変更で再利用のみ(layer1の分割を除く)
- `worker/reeval.py`・`worker/cost/*`・`worker/embedding.py`・`worker/debounce.py` —
  再利用のみ
- `llm/`(Gateway・typesafe・anthropic・jev) — グループペアのJev呼び出しは
  既存IF(`judge_pair`)のまま(pair_kindで区別しない・引用#12)
- `intents/`・`auth/`・`users/`・`geo/service.py` — 関与なし(reverse_geocode呼び出しのみ)
- `backend/alembic/versions/0001〜0004` — 変更不可(運用ルールどおり)
- `docs/`(00〜12) — 本設計の解釈記録はSTATUS運用(supervisor経由)。agent1は設計書内のみ

## 4. テスト方針

対抗策はws-4/5/6で確立したものを踏襲:

- **時間窓分離**: integration Fixtureのtime_start/expires_atはnow+5日系へ統一。
  30分Bucket条件(§2.2)を持つPool構築は、Fixtureのtime_startを同一時刻(または
  同一Bucket内)へ揃えた専用窓で組む(時間を進めるパートとの分離に注意)
- **teardown完全性**: 削除順は latch_status_events→notifications→latches→
  group_candidates→match_candidates→intents→users(部分UNIQUE・FKの残存が
  「開いている行あり」判定を壊す・user prefix掃除)
- **テストbasename一意**(運用ルール5): `test_group_calc.py`・`test_group_engine.py`・
  `test_matching_groupengine.py` は既存と衝突なし
- integrationはagent3実行禁止のため収集のみ(`--collect-only`)。
  test-ci=スーパーバイザー検証待ち(運用ルール1〜3。0005追加単位のため特に)

### 4.1 unit(`make test`。スタブで決定的)

`test_group_calc.py`(純関数):

1. 貪欲法: 種max>=3で3人集合確定・max<3種は走査スキップ・3人成立時は4人へ
   拡張しない・min=4の種は4人まで追加を続ける・4人でも不成立なら生成なし・
   人数包含の逐次判定(min(max_i)=3の候補はmin=4種の集合に入れない)・
   user相異違反で追加スキップ・互換行列の非互換ペアで追加スキップ・
   複数集合(第2集合の種は残りPoolから)・Pool順の同点はintent_id昇順
2. aggregate: min over ペアの採用・C=1.0・H=1(検証済み前提の計算式)
3. normalize_ids: sorted正規化
4. 対象時刻: max(time_start)

`test_group_engine.py`(スタブ経由・test_worker_jev.pyと同型):

- no-op分岐(起点不在・起点max<3・Pool空)
- Pool構築: 上位15切り詰め・cheap_score降順・同点intent_id昇順(スタブ行列表で)
- 集合生成: group_candidates INSERT列(member_scoresのseed_id/versions)・
  メンバー間ペアUPSERT呼び出し・ON CONFLICT時スキップ・user相異検査
- finalize: 世代リセット(versions不一致→aggregate/prev=NULL)・未判定ペアで
  何もしない・H不成立でclosed・全ペア揃いでaggregate計算とlatches INSERTが
  同一tx内(スタブのconn呼び出し順で検証)・閾値境界(0.80ちょうど→提案)・
  D-06上位チェック(gate関数を純関数化して境界: score同点はサイズ小・
  さらにintent_id辞書順)
- 読取失敗(expires NULLメンバー)でaggregate_score NULLのまま(再選択可能状態)

`test_latch_engine.py` へ追加(1対1I-1改修の試験):

- peer入力を欠損させた状態でhandle→latch_score NULLのまま・復旧後に再handleで
  latches生成まで完走(§2.7-4)

`test_worker_jev.py` 系へ追加: select_jev_targets配分
(1対1上位4・継続優先・新規が残り・繰上げで上限8・1対1最低4は継続があっても確保)。

### 4.2 integration(`make test-ci`。compose常設DB・実Redis・スタブGateway)

`test_matching_groupengine.py`(FakeClock操作・ws-6のtest_matching_latchengine.py構成を踏襲):

1. **3人集合E2E**(引用#18の下地): ユーザー3名・Intent3件(種max>=3+2件はmin2max4・
   同一Bucket・now+5日窓)→embedding fixture直入れ→`GroupEngine.handle` →
   group_candidates(candidate)・全3ペアのmatch_candidates生成 → `JevWorker.handle`
   (K_j配分で1対1上位4+グループ残り)→`LatchEngine.handle`(グループペア除外の
   記録)→`GroupEngine.finalize` → aggregate=min(MutualScore)・latches(candidate/
   proposed)・latch_status_events・notifications 3行・proposal(headcount=3・
   種のsecondary・min budget)
2. **Pool上限15の記録**(引用#16): 同一Bucket・カテゴリに20件の候補を密集配置→
   Pool構造化ログとgroup_candidatesの構成が15件以内・cheap_score降順で
   切り詰められていること(同点はintent_id昇順)
3. **未判定ペアの保留と継続優先**(引用#6規則3・4): 4人集合(6ペア)を仕込み
   Jev実行を制限(stub値・guard deny等)→1イベントで全ペア揃わず
   group_candidates=candidate保持→次評価の`JevWorker`選択で当該集合の未判定
   ペアが1対1新規より優先されること
4. **K_j配分の混在**: 1対1候補とグループペアが両方ある起点で、1対1上位4件が
   先に評価され、残り枠がグループへ渡ること(guardカウンタ・jev_resultの記録で検証)
5. **4人集合と人数不成立**: 種min=4は3人では確定せず4人で確定・
   min=5(max>=5)のIntentはPoolに入らない
6. **D-06上位1集合**: 同一起点Poolから2集合を成立可能にするFixture→
   両方latches candidateになるがproposedはaggregate上位1件のみ・
   上位を閉じ(expired操作)た後のdrainで第2集合がproposed化される
7. **visibility分岐**: hidden_until_match混在集合はheadcount+match_levelのみ
   (1対1の02#22系と同型の下地)
8. **削除Eventで集合close**: 集合構成後にメンバー1件を削除Event→
   stage1の_CLOSE_GROUPSでgroup_candidates=closed・構成ペア行はmatch_candidates
   のclose既存動作どおり
9. **冪等性**: 同一`handle`を2回実行→group_candidates二重なし(部分UNIQUE)・
   latches二重なし・status_eventsのcandidate挿入は1回
10. **1対1とグループの並走**: 同一Fixtureで1対1提案とグループ提案が両方生成
    され、1対1のlatch_score計算がグループ所属ペアを対象外にしていること
    (グループペア行のlatch_score NULL維持)

### 4.3 検証手順(報告書への明記用)

1. `make lint` / `make test`(全unit)
2. `uv run pytest --collect-only tests/integration/test_matching_groupengine.py -q`
   (収集件数の記録。実行はスーパーバイザー検証時)
3. 時刻参照がclock.pyのみ: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|
   time\.sleep|from time import' backend/src` が `core/clock.py:33` のみ+
   arch test passed
4. alembic: 0005追加後も0001〜0004無変更(`git diff main -- backend/alembic/versions/000[1-4]*`
   が空)
5. 変更ファイル一覧が§3と一致・`git status` 空・basename一意(運用ルール5のコマンド)
6. スーパーバイザー検証時: `docker compose build api worker` → `make migrate`
   (0005適用・索引の存在)→ `make test-ci`(1030+新規)→ 残存確認
   (users prefix・Redis・group_candidates・latches)

## 5. 未解決の論点(supervisor承認事項・G2時確認候補)

**supervisor承認を求める事項(設計の前提・ws-5/ws-6のdesign §5運用と同型):**

1. **Pool構築の人数緩和解釈**(§2.2)。「Layer 3を通過したIntent群」(06 §7)を
   「人数条件を `min<=4 AND max>=3` に緩和したPool検索+Layer 3と同一のcheap_score
   計算・切り詰め(降順15・同点intent_id昇順)を通過」と読む。1対1のLayer 1〜3結果を
   そのまま使う読みでは、participants_min>=3のIntent(07 §2「3人以上なら」→min=3)
   が永久にPoolに入らず、受入#18(3〜4人のIntentセットで成立・02 §4)が達成できない。
   実装はlayer1.pyの最小分割(LAYER1_WHERE_BASE)で既存の1対1検索への影響を文字列
   不変に保つ
2. **種=起点とPool走査の起点先頭扱い**(§2.3)。06 §7「Pool内のIntentを走査し種と
   なるIntentを選ぶ」に対し、起点をPool走査順の先頭(順位最大扱い)に置き、
   起点max>=3をGroup Searchのトリガーとする。起点を含まない集合の生成は他メンバー
   の評価と重複し無駄なため行わない(起点max<3はno-op)。第2集合以降の種は残り
   Poolから走査で選ぶ(引用#16の複数集合試験に必要)
3. **マイグレーション0005(group_candidates部分UNIQUE索引)の追加**(§2.8)。
   at-least-onceと複数メンバー起点の再構成による同一集合重複生成をDBで防ぐ
   最小構造。05 §2への追記はdocs次回改版に含める(ws-5 skip_reason・ws-6の0004と
   同じ扱い)
4. **1対1I-1の改修をws-7で実施するか**(§2.7-4)。ws-6の引継ぎ空白
   「peer読取失敗行がlatch_score計算済みのまま再選択されない」を、
   `LatchEngine._evaluate_pair` のtx統合(読取前倒し+計算と生成の同一tx)で
   構造解消する。変更は小さく観測結果は不変だがws-6資産への変更のため承認を求める。
   承認されない場合、1対1側はws-6どおり放置(通常経路=M1削除Event closeと
   M3-8全削除が回収)とし、ws-7の集合側だけ§2.5の構成で防ぐ

**解釈記録(G2時確認候補・STATUS「G2時確認事項」へ追記提案):**

5. **aggregate の H は集合単位**(§2.5)。D-06の集約式 `H × min over ペア(MutualScore)
   × C`(06 §8)のHは単数形であり、集合のHard互換(人数共通包含+人数除き全ペア互換)
   の再検証結果と解釈。ペア行のlatch_score(1対1式)はグループ所属中は書かない
   (§2.7-1)。集約のMutualScoreはペア行のjev_resultから直接計算
6. **D-06通知順序の実装は「メンバー重複集合の上位1」**(§2.6)。「同一Pool」のDB表現
   がないため、開いているグループlatches群のうちメンバーが1人でも重なる集合間で
   aggregate降順(同点=サイズ小→intent_id辞書順)の最上位のみproposed化する近似。
   別Pool由来の偶然の重複は保守側(抑制)に働く
7. **グループ候補へのnearby_also存在通知は行わない**(§2.10-2)。06 §6のnearby規定は
   1対1文脈で集合への適用はdocsに明示なし。閾値未満の集合はlatchesを作らず
   group_candidates=candidateのまま
8. **member_scores の内容は seed_id + versions**(§2.3)。05 §2は「メンバー間・集合評価
   の結果参照」のみで内容規定なし。評価実値の複製は行わない(二重管理回避)
9. **Poolの「同一時間Bucket」は time_start が起点と同一30分Bucketに属する条件**
   (§2.2)。ws-6 §2.8のBucket解釈と同一。時間交差条件(BASE内)と併用する
10. **Pool構築のHNSW検索上限はK_vと同じ50**(§2.2)。D-24はPool上限15のみ規定で
    検索段階の上限記載なし。「Vector 50 → cheap_score → Pool 15」の鎖として設定
11. **area_name は全メンバーgeo_centerの平均点の逆転ジオコーディング**(§2.7-3)。
    ws-6「中点」規則(2者)の集合版。05 §2は「代表点の地物名」のみ

**実装時確認事項(実装エージェントが確認して報告):**

12. `uuid[]` の `@> ARRAY[a, b]`(両方含む)・`&&`(交差)・部分UNIQUE索引への
    ON CONFLICT 推論が3〜4要素配列で期待どおり動くこと(初回integrationで確認。
    推論が外れる場合はINSERT前SELECT FOR UPDATEへの切替をsupervisorへ相談 —
    ws-6設計§5-10と同型)
13. HNSW検索2回(1対1Layer 2+Pool検索)の実行時間がLayer 5+通知の予算≤2秒
    (06 §1)に収まること。ci規模の件数で問題にならないはずだが、
    dense配置試験(§4.2-2)の実行時間を記録する
