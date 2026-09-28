# M2 ws-3(Layer 1 Hard Filter + Layer 2 Candidate Retrieval)設計メモ

- 作成: 2026-09-29(agent1)
- 前提: M2 ws-1 マージ済み(0bb6a2d・main test-ci 677 passed)。ws-2(Embedding Worker)と並行wave
- 参照仕様: 06 §2〜§3・05 §3・02 §4(#9・#10)・01 §11〜§12・10 §3・§4.6・§4.7

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

embedding_completed 起点の第2段の前半、すなわち **Layer 1 Hard Filter(SQL+PostGIS・AI不使用)**
と **Layer 2 Candidate Retrieval(pgvector cosine・K_v=50)** の判定関数、および通過ペアの
**match_candidates 生成・記録**を実装する。02#9(Hard Filter 単体試験)と 02#10(意味的近接で
候補生成)の受け入れ条件をこの単位の完了条件に含める。パイプラインへの組み込み
(embedding_completed Event からの呼び出し配線)は後続単位であり、本単位は
stage1 の embedding_hook にならう「フックの呼び出し契約」の確定までをスコープとする。

ws-2 と並行するため、試験は Embedding 実体に依存しない(fixture で intents.embedding へ
直接ベクトルを挿入)。worker/・intents/・tests の交差は「既存行を変更しない追記」で回避する(§3.4)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | Layer 1 の判定規則(表のとおり、SQLとIndexで判定・pass/failのみ・AI不使用): 自己除外=intent_a.user_id ≠ intent_b.user_id / 時間=[time_start, time_end) の交差が存在(flexibilityがあればその分拡張) / 距離=ST_DWithin(center_a, center_b, r_a + r_b) / 予算=ペア予算 min(budget_max_a, budget_max_b)(NULLは無視)が500円未満の場合のみfail / 人数(1対1)=2 ∈ [min_i, max_i] が双方で成立 / ブロック=blocksに(A,B)(B,A)のいずれも存在しない / カテゴリ=category_primary完全一致(secondaryは対象外) / 年齢=ペアのいずれかがalcohol_involved=trueなら双方の作成者が20歳以上(users.birth_date。API作成・更新時検証に対する二重防御) / visibilityは判定対象外(開示範囲の制御でありLayer 5のproposal分岐が担う) / 絶対NGは判定可能なもの(ブロック)のみ | 06 §2 |
| 2 | Layer 2: raw_textは埋め込みに使わず構造化データの正規化テキストがembed対象(実体はws-2)。ANN SearchはpgvectorのHNSW(cosine)で起点Intentのembeddingをクエリに上位K_v件を取得。embedding IS NULLのIntentはLayer 1〜2の生成元にも対象にもならない。draftも生成元にも対象にもならない(draft→active化で作成種Event経由の初回投入) | 06 §3 |
| 3 | K上限: Vector検索 K_v=50。Kは「その層から次層へ渡す出力数の上限」。切り詰め順序は各層のスコア上位(Vector=類似度)とし、同点はintent_id昇順で決定的に崩す | 06 §8 D-24・01 §11 |
| 4 | 比較の向き: 起点は新規または更新されたIntentで、そこから既存Intentインデックスへ関連候補を引きに行く(新規同士だけ比べて済ませない) | 01 §11 |
| 5 | ファネルの層順: 地域・時間・カテゴリ・Hard ConstraintがLayer 1、Vector SearchがLayer 2(Layer 1が先に絞り、Layer 2が意味的近接上位を取る) | 01 §12 |
| 6 | match_candidates: 正規化 intent_a_id < intent_b_id / intent_a_version・intent_b_version=評価時点の各version / UNIQUE(a,b,av,bv)により評価はバージョン組ごとに1レコード・Intent更新後の再評価は新しいバージョン組で新レコード / 同一バージョン内の再評価(時間Bucket等)は既存レコードを更新 / retrieval_score・cheap_judge_score=各層の結果 / status は pending / evaluated / skipped(Jev未判定) / closed | 05 §2 |
| 7 | Index(0001で作成済み): idx_intents_matching(category_primary, time_start, expires_at) WHERE status='active' / idx_intents_geo GIST(geo_center) / idx_intents_budget・idx_intents_participants(部分Index) / idx_intents_embedding HNSW(vector_cosine_ops) | 05 §3 |
| 8 | 第2段トリガーはembedding_completed(version検査=現行のみ処理・embedding IS NULLは再検査して再試行)。作成・更新Eventの処理はEmbedding要求のキックまででLayer 1以降は走らない | 06 §1・§9 |
| 9 | Layer 1〜3(Hard Filter + Candidate Retrieval + Cheap Judge)の予算は p95 ≤1秒(01 §20「Candidate Retrieval p95 1秒」と同じ枠) | 06 §1 |
| 10 | #9(Hard Constraintをコードで除外できる): 予算・時間・距離・人数を意じ的に外すペアを配置 → いずれの候補にも現れない。**ciでHard Filterの判定単体試験**(stagingでE2E確認は後段の振り分け) | 02 §4 #9・10 §3 |
| 11 | #10(Vector Searchで候補を絞れる): 語彙が一致しない意味的近接ペア(焼肉/肉系なら何でも)を配置 → 候補として生成される | 02 §4 #10 |
| 12 | 自己ペアの除外(FR-17): 同一ユーザーの2 Intent(同一時間帯・地域・カテゴリ、embedding類似も高い)を配置し、いずれのEvent処理でも当該ペアがmatch_candidatesに生成されないことを確認 | 10 §3 |
| 13 | K上限の裏付け: Vector出力 ≦50が記録で守られ、切り詰めが決定的(同点intent_id昇順)。同一入力での2回実行が同一結果になることで確認 | 10 §4.6 |
| 14 | 冪等性: 同一Event 2回投入でmatch_candidatesが二重生成しない(UNIQUE制約) | 10 §4.7・12 M2完了条件 |
| 15 | 時刻操作はClock経由(適用範囲に時間条件の境界操作を含む)。テスト用パブリッシャーでembedding_completedを手動発火できる | 10 §1 |
| 16 | time_flexibility_minutes / location_flexibility はstructured_data内でMVPでは常にnull(将来の抽出有効化に備えた予約) | 05 §2 |
| 17 | ペア予算(参加Intentのbudget_maxの最小値)はproposalのbudget生成元としても使う(本単位では記録のみでproposal生成はws-6) | 05 §2・06 §2 |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

- `worker/stage1.py`: 第1段。行確保・version検査・種別処理・processed遷移まで実装済み。
  `embedding_hook: Callable[[str, uuid.UUID, int], Awaitable[None]]`(event_type, intent_id, version)
  の予約があり、created/updated で呼ばれる(実体はws-2)。**embedding_completed は 6値の
  event_type として受信し version 検査を経て processed になる(第2段の実体は後続単位)**。
  削除Eventの `_CLOSE_CANDIDATES`(match_candidates を closed へ)が match_candidates への
  唯一の既存書き込み
- `intents/events.py`: outbox INSERT と 5種定数(embedding_completed 定数の追加はws-2)
- `intents/completion.py`: time_end 未指定は time_start+3時間を保存時補完
  (`default_time_end`)。active 行は time_start/time_end/expires_at が補完済み
- `intents/store.py`: text() 生SQL・`CAST(:x AS ...)` 規約(bind param リテラル落ち対策・
  ws-3報告書の教訓)・`_coerce_uuid`(asyncpg UUID 対策・3度目の教訓で stage1 にも導入済み)
- `alembic 0002`(geofeatures)= head。intents.embedding vector(768)・HNSW Index・
  match_candidates(UNIQUE(a,b,av,bv))・blocks・users.birth_date はすべて 0001 で作成済み
- integration conftest: `migrated_db`(session スコープ upgrade head)→ `db_engine`。
  ws-3 の試験はこの fixture をそのまま使う(worker 起動不要の直接関数呼び出し)
- `tests/integration/test_events_pipeline.py`: user 登録〜active Intent 作成の流儀
  (`_register`・`_active_payload`・`_future`)と teardown での match_candidates 先行削除の
  実例(test_4 が status='candidate' で直接 INSERT している — CHECK制約がないため可能。
  本単位はこの試験に触れない)

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

1. **embedding_completed Event から本処理への配線**(後続のパイプライン組み込み単位):
   stage1 への matching フック追加・worker/main.py への DI。本単位は呼び出し契約(§2.4)のみ
2. **Embedding キックの実体・embedding_completed 発行**(ws-2・並走中)
3. **Layer 3 Cheap Judge 以降**(ws-4〜): cheap_score 計算・jev_result・latch_score・
   match_candidates.status の evaluated/skipped 遷移
4. **30分Bucket再評価・catch-upスキャン**(ws-6): 再評価による既存レコード更新の呼び出し
   経路。UPSERT の更新側仕様(§2.3)だけは本単位で満たしておく
5. **時間・場所flexibilityの拡張判定**: MVPでは常にnull(引用#16)のため実装しない(§2.6)
6. **K上限裏付け試験の本実施**(ws-8): 10 §4.6 の密集E2E。本単位は Layer 2 部分の
   単体裏付け(§4.2)まで
7. **トレース計測・Queue lag**(M4のObservability)

## 2. 実装方式の選択と推奨

### 2.1 Layer 1 / Layer 2 のクエリ構成 — 推奨: 条件を共有する単一SQL(複合ORDER BY + LIMIT)

**案A(推奨)**: Hard Filter の全条件を WHERE 句に並べ、`ORDER BY i.embedding <=> CAST(:query AS vector) ASC, i.id ASC LIMIT K_v` を付けた**単一SQL**。WHERE 句の組立は共通ビルダー関数として、Layer 1 単体参照用(ORDER BY / LIMIT なし。§4.2 の 02#9 単体試験と将来のトレース用)と Layer 2 用(ORDER BY + LIMIT)の両方から使う。

- 利点: 層順(引用#5: Layer 1 が絞り Layer 2 が並べる)を1クエリで正確に体現する。
  「Layer 1 通過集合」の中間表現を持たず、通過集合が大きくても2往復しない。
  単体試験(02#9)と本番経路(Layer 2)が同一の WHERE で検証できる(試験と本番の乖離がない)
- トレードオフ: 後述(§2.2)のとおり HNSW Index が使われず逐次計算になる。ci/staging 規模
  (数千行)では p95 1秒(引用#9)に余裕を持って届く。本番負荷試験(10 §4.2・M4)で
  問題が出た場合の最適化余地は §2.2 に記録する

**案B(不採用)**: 2クエリ分割 — Layer 1 で通過 ID 一覧を取得し、Layer 2 で
`WHERE id = ANY(:ids) ORDER BY ...` を投げる。層の独立性は明示的だが、通過集合の ID 配列が
大きくなりうる・同一WHEREの二重管理になりうる・2往復する。案A の共通ビルダーで
Layer 1 単体関数も作れるため、案Bが勝る点がない

**案C(不採用)**: HNSW-first — はじめに `ORDER BY embedding <=> :q LIMIT 50`(Index 使用)で
意味的近接上位を取り、Hard Filter をアプリ層で後段フィルタ。06 §1 の層順(L1→L2)と逆で
あり、Hard Filter で落ちた分だけ出力が K_v=50 を割る(D-24 の「出力数上限」の意味が壊れる)。
Index を使うことだけが目的の逆転はできない

### 2.2 HNSW Index と同点 intent_id 昇順の両立 — 推奨: 決定性優先(逐次ソート)。Index は付随

06 §3 は「ANN SearchはpgvectorのHNSW(cosine)で」上位 K_v 件を取得すると述べ、D-24(引用#3)
と 10 §4.6(引用#13)は「同点はintent_id昇順で決定的に崩す」ことを完了条件にしている。
pgvector の Index KNN スキャンは ORDER BY 句が距離演算子単独(`ORDER BY embedding <=> :q`)で
あることを条件とするため、**第2キーの i.id を付けた瞬間に Index は使えず逐次計算+ソートに
なる**。この2つの要件は技術的に排他であり、次のとおり解釈して解決する。

- **決定性(同点 intent_id 昇順)を機能要件とし優先する**。cosine 距離の float 完全同点は
  「同一ベクトル」(同一テキスト→決定的なEmbedding)の重複配置で実際に起こり、K_v 境界での
  切り詰めが実装依存で揺れることは許されない(10 §4.6 の裏付け試験・試験の再現性の両方が
  この順序を要求する)
- 「HNSW(cosine)で取得する」は、HNSW cosine Index(05 §3・0001作成済み)が定義された
  テーブル上で cosine 距離演算子 `<=>` により上位を取るという意味と解釈する。近似探索
  (Index スキャン)は性能のための実装詳細であり、MVP 実装は正確な逐次ソートで同じ問いに
  答える(ws-1設計§2.6と同様、「確定値が直接定めない細部は確定値の意味を壊さない運用に
  収める」実装解釈)。シーケンシャルスキャンでもクエリの意味(意味的近接上位 K_v)は同一
- 将来の最適化余地(採用しないが記録): pgvector の iterative scan + 境界距離と同点の行を
  再取得する2段クエリ、または ef_search 調整。10 §4.2 の負荷試験で Vector Retrieval 件数・
  レイテンシが要件を割ったときだけ検討する

### 2.3 match_candidates の記録 — 推奨: INSERT ... ON CONFLICT DO UPDATE(retrieval_score のみ)

Layer 2 通過の各対象について、正規化(intent_a_id < intent_b_id、UUID 比較はPython側)のうえ
UNIQUE(a, b, av, bv) を狙った UPSERT を行う。

```sql
INSERT INTO match_candidates (
    intent_a_id, intent_b_id, intent_a_version, intent_b_version,
    retrieval_score, status, created_at, updated_at
) VALUES (...)
ON CONFLICT (intent_a_id, intent_b_id, intent_a_version, intent_b_version)
DO UPDATE SET retrieval_score = EXCLUDED.retrieval_score,
              updated_at = EXCLUDED.updated_at
```

- 新規行は `status='pending'`(05 §2 の値域。Layer 1〜2 時点では Jev 未評価)。**status は
  更新側で触らない**(evaluated/skipped への遷移と jev_result・cheap_judge_score は ws-4 以降、
  closed は stage1 の削除処理が持つ)
- DO UPDATE とする根拠は 05 §2「同一バージョン内の再評価(時間Bucket等)は既存レコードを
  更新する」。DO NOTHING だと再評価で古い retrieval_score が残る。DO UPDATE でも冪等性
  (引用#14: 2回投入で二重生成しない)は保たれる(行は常に1行。UNIQUE が担保)
- 対象側の version は SELECT 時点の `i.version` をそのまま記録する(評価世代)。起点側は
  Event の version(version 検査済みの現行)
- トランザクション: 起点読み込み→検索→UPSERT までを呼び出し側の1トランザクションで
  実行する(関数群は AsyncConnection を受け取る純関数。§2.4)

### 2.4 フックの呼び出し契約(stage1 の embedding_hook と対になるもの)

stage1 への実配線は後続単位(1.4-1)。本単位が確定する契約は次のとおり。

- **matching モジュールの公開APIは `AsyncConnection` を第一引数に取る純関数群**とする。
  これにより (a) integration 試験は `engine.begin()` で直接包んで呼べる(worker 不要)、
  (b) 後続単位は stage1 の `_process_once` のトランザクションに同乗させることも独立
  トランザクションに切ることも選べる
- 呼ばれるタイミング: embedding_completed Event の version 検査通過後・processed マーク前に
  equivalent の位置(stage1 の種別処理で embedding_completed がフックを呼ぶ形を想定)
- エントリポイント: `run_candidate_retrieval(conn, clock, intent_id) -> RetrievalOutcome`。
  戻り値は dataclass(起点version・Layer 1 通過件数・生成・更新した match_candidates の
  (a, b, score) 一覧・skip理由)。試験の assert と将来のトレースの供給源
- 例外: SQL失敗等は呼び出し側(stage1 の再試行ループに載る将来経路)に伝播させる。
  本モジュール内での握り潰しはしない

### 2.5 起点の検証とショートサーキット(no-op 条件)

起点 Intent を読み、次のいずれかなら検索を実行せず空の結果を返す(06 §3「生成元にもならない」)。

| 条件 | 根拠 |
|---|---|
| status ≠ 'active'(draft ほか) | 06 §3(draft対象外)・05 §2(activeのみマッチング対象) |
| embedding IS NULL | 06 §3(embedding未完了・失敗は対象外。embedding_completed 到着待ちの再検査は組み込み単位の責務) |
| geo_center IS NULL / time_start IS NULL | active行では補完済みのはずだがアプリ層保証のための防御(05 §2) |
| 起点が人数条件 2 ∈ [min, max] を満たさない | 06 §2(双方で成立が条件。起点側不成立なら候補は成立し得ない) |

起点の作成者情報(birth_date)は起点の20歳判定(飲酒ペアの二重防御)に使うため users から
読む。20歳の基準時点は docs に明記がないため **Clock.now() の満年齢**とする(birth_date は
不変であり、実質差は20歳の誕生日前後1日に限られる。判定は評価のたびに行う)。

### 2.6 Layer 1 各条件の SQL 実装(確定表)

対象側は `intents i`(WHERE status='active' AND embedding IS NOT NULL — 06 §3)。
起点の値はすべて bind param(時刻は Clock 明示値・C2/sql 規約どおり `CAST(:x AS ...)` 形式)。

| 条件(06 §2) | SQL 表現 | 備考 |
|---|---|---|
| 対象の基本 | `i.status = 'active' AND i.embedding IS NOT NULL` | draft・embedding NULL は対象外 |
| 自己除外 | `i.user_id <> CAST(:origin_user_id AS uuid)` | FR-17 |
| カテゴリ | `i.category_primary = :origin_category` | 完全一致 |
| 時間交差 | `i.time_start < :origin_time_end AND :origin_time_start < COALESCE(i.time_end, i.time_start + interval '3 hours')` | 半開区間 [s, e) の交差(s_a < e_b AND s_b < e_a)。time_end の COALESCE は保存時補完(completion.py)への防御。flexibility は MVP で常に null(05 §2)のため実装しない(1.4-5) |
| 距離 | `i.geo_center IS NOT NULL AND ST_DWithin(i.geo_center, ST_SetSRID(ST_MakePoint(CAST(:origin_lon AS float8), CAST(:origin_lat AS float8)), 4326)::geography, CAST(CAST(:origin_radius_m AS int) + COALESCE(i.geo_radius_m, 1000) AS double precision))` | geography 同士の ST_DWithin はメートル。r_a + r_b(06 §2)。geo_radius_m の COALESCE は既定1,000m(completion.py DEFAULT_RADIUS_M)への防御 |
| 予算 | `(CASE WHEN :origin_budget IS NULL AND i.budget_max IS NULL THEN NULL WHEN :origin_budget IS NULL THEN i.budget_max WHEN i.budget_max IS NULL THEN :origin_budget ELSE LEAST(:origin_budget, i.budget_max) END) IS NULL OR (同式) >= 500` | ペア予算=min(NULLは無視)。NULL(双方制約なし)は fail しない。500未満のみ fail(06 §2)。500・比較演算は06 §2の確定値そのまま |
| 人数 | `i.participants_min <= 2 AND i.participants_max >= 2` | 起点側は Python で事前検査(§2.5) |
| ブロック | `NOT EXISTS (SELECT 1 FROM blocks b WHERE (b.blocker_id = CAST(:origin_user_id AS uuid) AND b.blocked_id = i.user_id) OR (b.blocker_id = i.user_id AND b.blocked_id = CAST(:origin_user_id AS uuid)))` | 双方向(06 §2) |
| 飲酒年齢 | `(NOT (:origin_alcohol OR i.alcohol_involved)) OR (:origin_user_ge_20 AND EXTRACT(YEAR FROM AGE(CAST(:now AS timestamp), CAST(u.birth_date AS timestamp))) >= 20)` | ペアのいずれかが true なら双方20歳以上。u は `JOIN users u ON u.id = i.user_id`。起点側の20歳は Python 計算(bool param)。EXTRACT(YEAR FROM AGE(...)) は満年齢(誕生日基準) |
| (visibility) | **条件に入れない** | 06 §2(v0.4で判定対象外) |

Layer 2 の SELECT はこれに `i.id, i.version, 1 - (i.embedding <=> CAST(:origin_embedding AS vector)) AS similarity` を追加し、`ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC, i.id ASC LIMIT 50`。**retrieval_score には cosine 類似度(1 − cosine距離)を記録**する(Layer 3 の類似度要素と同一の量。ORDER BY は距離昇順=類似度降順と同義)。起点 embedding は文字列表現(`'[0.1, ...]'` — asyncpg が vector 列を文字列で返す)をそのまま再キャストして渡す。

### 2.7 マイグレーション・docs改版 — 追加なし

- alembic は 0002 のまま。intents.embedding(0001)・HNSW Index(0001)・match_candidates と
  UNIQUE(0001)・blocks(0001)・users.birth_date(0001)で本単位の全機能が成立する。
  **マイグレーションを追加する理由は発生しなかった**
- match_candidates.status への CHECK 制約追加はしない(既存 test_events_pipeline test_4 が
  status='candidate' で直接 INSERT しており、05 §2 の値域と試験資産の両立には DB 変更より
  試験資産側の将来の整理が適切。本単位は触らない)
- docs(01〜12)の改版不要。§2.2・§2.5 の解釈(逐次ソート・20歳基準時点)は確定値の
  実装解釈の範囲囲内と判断し、本メモに記録する

### 2.8 採用しないもの(YAGNIによる切り捨て一覧)

1. **Layer 2 の HNSW Index スキャン**(§2.2: 決定性優先。最適化は負荷試験結果待ち)
2. **flexibility 拡張の実装**(05 §2: MVPでは常にnull。到達不能コードを書かない)
3. **K_v・500円の settings 化**(D-24・06 §2 の確定値そのものなので module 定数。調整余地はdocs改版を伴う)
4. **match_candidates の一括INSERT(executemany化)**(K_v=50 と小さく、UPSERT の結果を
   RetrievalOutcome へ載せる都合から順次実行で十分)
5. **Layer 1 通過件数の上限**(Layer 1 はフィルタであり出力上限は Layer 2 の K_v が持つ。
   通過件数の過大検知はトレース計測(M4)の担当)
6. **pgvector の Python 型サポート导入**(text() の `CAST(:x AS vector)` で足りる。
   依存追加なし)
7. **起点・対象の FOR UPDATE**(version 検査を通過した Event 経路での一貫性は stage1 の
   既存の検査と UPSERT の version 組で担保される。Bucket 再評価の直列化は ws-6 の課題)

## 3. ファイル構成

### 3.1 作るもの(すべて新規ファイル)

```text
backend/src/latch/worker/matching/__init__.py
    # 公開API(run_candidate_retrieval・RetrievalOutcome)と
    # フックの呼び出し契約(§2.4)の TypeAlias・K_VECTORS=50 など定数
backend/src/latch/worker/matching/origin.py
    # 起点Intent読み込み(id, version, user_id, category_primary, alcohol_involved,
    # budget_max, participants_min/max, geo(lon/lat), geo_radius_m, time_start/end,
    # embedding文字列)+ 起点users.birth_date + no-op判定(§2.5)+ SQLパラメータ組立
backend/src/latch/worker/matching/layer1.py
    # Hard Filter の WHERE 共通ビルダー + hard_filter_candidates(conn, origin)
    #   → 通過対象の一覧(id, version, user_id)(ORDER BY なし。02#9単体試験・将来トレース用)
backend/src/latch/worker/matching/layer2.py
    # retrieve_topk(conn, origin) → Layer 1 条件込み単一SQL
    #   (id, version, similarity) 上位K_v=50・ORDER BY 距離ASC, id ASC
backend/src/latch/worker/matching/candidates.py
    # match_candidates への UPSERT(§2.3)・intent_a/b 正規化・retrieval_score記録
backend/src/latch/worker/matching/runner.py
    # run_candidate_retrieval(conn, clock, intent_id) → RetrievalOutcome
    #   (起点検証→layer2→candidates記録。no-op理由もOutcomeに載せる)
backend/tests/unit/matching/test_origin.py
    # パラメータ組立・no-op判定(人数2不成立・embedding NULL・draft)・
    # 起点20歳計算の境界(誕生日当日/前日)・UUID正規化 — スタブrowで決定的
backend/tests/unit/matching/test_layer_sql.py
    # layer1/layer2/candidates のSQL文字列ピン+実dialect compile検査
    #   (CAST形式・ORDER BY 構成・LIMIT 50・ON CONFLICT 列指定。test_store_sql.py流儀)
backend/tests/unit/matching/test_runner.py
    # no-op で layer2/candidates が呼ばれないこと(スタブconnで検証)
backend/tests/integration/test_matching_hardfilter.py
    # 02#9 単体試験(§4.2)
backend/tests/integration/test_matching_retrieval.py
    # 02#10・K_v・冪等・対象外(§4.2)
```

### 3.2 触るもの(既存ファイルへの変更)

**なし**(設定追加なし・依存追加なし・compose/Makefile 変更なし)。

ws-2 が settings.py に Embedding 系設定を追記する可能性に対し、本単位は K_v=50(D-24)・
500円(06 §2)を module 定数とするため settings.py を触らない(§2.8-3)。マイグレーションも
追加しない(§2.7)ので、共有ci-dbの alembic_version を進めず運用ルール1・2(STATUS.md
「運用ルール」)のDB取り合いも発生しない。

### 3.3 触らないもの

- `backend/src/latch/worker/stage1.py`・`worker/main.py`・`worker/debounce.py`(フック実配線は
  後続単位。stage1 の embedding_completed 種別は processed のまま)
- `backend/src/latch/intents/`(events.py・store.py・service.py すべて。embedding 書き込みは ws-2)
- `backend/src/latch/`配下の auth / users / ratelimit / geo / llm / g1gate / events 各モジュール
- `backend/alembic/`(0002 が head のまま)
- `backend/tests/`の既存ファイル(test_events_pipeline.py の user_env・test_4 は不変。
  integration conftest の fixture は参照のみ)
- `compose.yaml`・`Makefile`・`backend/pyproject.toml`・`frontend/`・`prototype/`
- `docs/`(01〜12。改版対象なし)

### 3.4 ws-2 並走との競合回避方針(明記指示への対応)

- **交差しうる領域は worker/・intents/・tests の3つ**。このうち intents/ は本単位が一切触らず
  (3.3)、worker/ は両単位とも**新規ファイル・新規ディレクトリの追加のみ**(ws-2 は
  embedding 系ファイル、ws-3 は matching/ パッケージ)で、既存ファイル(stage1.py・main.py・
  debounce.py・__init__.py)は双方変更しない。git マージでパスが交差しない
- tests も同様に新規ファイル追加のみ。**basename 一意性**(運用ルール5)は
  `test_matching_*.py` vs ws-2 側の想定名(embedding 系)で衝突しない。マージ時は
  `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空である
  ことを確認する(本設計のテスト方針§4.3に検証手順として明記)
- settings.py は両単位とも「既存行を変更しない追記」を前提とするが、本単位は追記自体を
  不要にした(§2.8-3)。ws-2 が追記する場合のマージは両側保持で解消(ws-1の実績と同じ)
- stage1 への matching フック追加(embedding_completed 種別の処理実体)は**後続単位で
  単独の変更**として行う。ws-2 の embedding_hook 実装と同じ stage1.py を後から一度だけ
  触る設計にすることで、並走中の同一ファイル二重変更を構造的に避ける

## 4. テスト方針

### 4.1 unit(`make test`。外部プロセス不要・スタブで決定的)

- **test_origin.py**: スタブ行からのパラメータ組立(geo の ST_X/ST_Y 読み取り想定・
  embedding 文字列の受け渡し)・no-op 判定の全分岐(draft・非active・embedding NULL・
  geo/time 欠落・人数2不成立)・起点20歳計算(20歳誕生日当日=満20歳・前日=19歳・
  うるう年2/29境界を1ケース)・`intent_a < intent_b` 正規化(UUID 比較)
- **test_layer_sql.py**: SQL 文字列ピン(WHERE 構成要素がすべて含まれること・visibility を
  条件に含まないこと)+ 実dialect compile による bind param 検査(`CAST(:x AS ...)` 形式・
  パラメータ一式・リテラル落ちなし。test_store_sql.py と test_pubsub_bus_sdk_calls.py の
  流儀)・`ORDER BY ... ASC, id ASC LIMIT 50` ピン・ON CONFLICT の列指定ピン
- **test_runner.py**: no-op 条件で layer2・candidates が呼ばれないこと(スタブ injected)。
  RetrievalOutcome の構成(skip理由・生成件数)

### 4.2 integration(`make test-ci`。compose常設DB・pgvector 実物)

fixture 方針(Embedding 実体に依存しない): API でユーザー登録(18-19歳・20歳以上を
birth_date で作出)→ API で active Intent 作成(geo・時間は保存時補完の実物)→
`UPDATE intents SET embedding = CAST(:vec AS vector), embedding_model = 'fixture' WHERE id = ...`
で**768次元ベクトルを直接挿入**。ベクトルは基底ベクトル+摂動で cosine 類似度を制御
(高類似=平行摂動・低類似=直交)。Worker は起動しない(直接関数呼び出し・FakeClock 注入)。
teardown は user 単位で match_candidates → match_events → intents → users の順に削除
(test_events_pipeline.py user_env の FK 順に同じ)。

**test_matching_hardfilter.py(02#9 — 単位の完了条件その1)**

1. pass 対照(全条件成立ペア)が Layer 1 通過集合に現れる
2. 各 fail 条件を1つずつ外したペア(計8系統)が通過集合に現れない:
   予算(双方499円・片方499円→ペア予算499)・時間(交差なし・境界接触 [s,e) の e_a=s_b)・
   距離(r_a+r_b 外れ・境界内外の1ケース)・人数(対象 max=1・起点 min=3)・ブロック
   (A→B・B→A の双方向)・カテゴリ(不一致)・飲酒年齢(19歳×20歳の飲酒ペア・
   **DB直接 UPDATE で alcohol_involved=true を強制した19歳起点**=API 検証をバイパスした
   二重防御の実試験)・自己除外(同一ユーザー2 Intent・FR-17/引用#12)
3. 予算 NULL 系(双方NULL・片方NULL)は fail しない(NULLは無視)
4. visibility が異なる2値(hidden_until_match / summary_only)のどちらも通過すること
   (判定対象外の確認)
5. 02#9 の受け入れ形「いずれの候補にも現れない」を run_candidate_retrieval 経由でも
   確認(match_candidates に当該ペアが生成されない)

**test_matching_retrieval.py(02#10・K_v・冪等・対象外 — 完了条件その2〜その4)**

1. **02#10**: 語彙が一致しない意味的近接ペア(カテゴリ=meal で文言「焼肉」/「肉系なら
   何でも」のように soft_constraints を変える)・Hard条件成立・embedding が高 cosine →
   match_candidates に生成される(status='pending'・retrieval_score 記録・正規化 a<b・
   version組記録)。低 cosine の対照は上位から落ちる(または K_v 内に来ない)
2. **K_v=50 切り詰めの決定性**: Hard条件成立な対象55件を配置(同一 embedding=距離完全同点)、
   出力50件・**同点は intent_id 昇順**(=UUID 昇順の先頭50件が選ばれる)。さらに非同点の
   通常ケース(類似度差つき55件)でも距離上位50件。同一入力2回実行で同一結果(10 §4.6)
3. **embedding NULL / draft 対象外**: 対象55件のうち embedding NULL を混ぜても除外され
   件数不変。起点を draft・embedding NULL にした場合 no-op(match_candidates に1行も増えない)
4. **冪等性**: 同一 (a, b, av, bv) で run_candidate_retrieval を2回実行 → match_candidates
   は1行のまま(retrieval_score は更新・行数不変。10 §4.7 の Layer 1〜2 部分)
5. 既存(test-ci 677)の全数グリーン維持

### 4.3 検証手順(報告書への明記用)

1. `make test`(unit)→ lint
2. `make test-ci`(api イメージ再ビルド後・運用ルール4。**マイグレーション追加なしのため
   DBのalembic_versionは動かない**)。実施タイミングは ws-2 の test-ci 実行と同時にしない
   (運用ルール1。本単位はDBスキーマを変えないが、 ws-2 側の都合に合わせる)
3. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d`
   が空(basename 一意性・運用ルール5)

## 5. 未解決の論点・実装時確認事項

1. **(先送り記録)Layer 2 の Index 最適化**: 本単位は逐次ソートで決定性を取る(§2.2)。
   10 §4.2 の負荷試験(1万〜10万 Intent)で Candidate Retrieval p95 1秒(引用#9)が割れる
   場合のみ、iterative scan + 境界同点再取得の2段クエリを再設計する(M4・ws-8 の負荷試験
   で観測。G2のK上限裏付け試験は現方式のままで実施可能)
2. **(実装時確認)asyncpg の vector 列読み取り**: 起点の embedding を SELECT した際の
   戻り型(文字列想定)を最初の integration 実装で実確認する。文字列でない場合も
   `CAST(:x AS vector)` へ渡せる表現へ変換する(数値配列→文字列組立)だけで設計不変
3. **(実装時確認)EXTRACT(YEAR FROM AGE(...)) の挙動ピン**: unit ではなく integration 側で
   誕生日当日・前日の実DB計算を1ケースずつ確認する(PostgreSQL の AGE が暦どおりの満年齢
   を返すことの実証。設計の想定どおりであれば追加対応不要)
4. **(後続単位への引継ぎ)match_candidates.status='pending' の意味確定**: 本単位は
   pending で INSERT するのみ。evaluated/skipped への遷移主体(ws-4〜)・closed(stage1 の
   削除処理)と値域の運用は 05 §2 どおり。既存 test_events_pipeline test_4 の
   status='candidate' は 05 §2 の値域外だが試験資産のため本単位では触らない(§2.7)。
   ws-4 以降で match_candidates の status を本格利用する単位が整理する
5. **(質問不要の判断記録)20歳判定の基準時点**: docs に明記がないため Clock.now() の満年齢
   とした(§2.5)。birth_date は不変で、代替候補(対象時刻 time_start 基準)との差は20歳の
   誕生日をまたぐ日夜限りであり、安全性側(評価時点で満20歳)を選んだ。異論あれば
   実装単位での変更は Python 計算1箇所に閉じる
