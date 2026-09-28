# M2 ws-3(Layer 1 Hard Filter + Layer 2 Candidate Retrieval)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** embedding_completed 起点の第2段前半 — Layer 1 Hard Filter(SQL+PostGIS・AI不使用)と Layer 2 Candidate Retrieval(cosine上位K_v=50・同点intent_id昇順)の判定関数群、および通過ペアの match_candidates 生成(status='pending'・UNIQUE(a,b,av,bv)へのUPSERT)を実装し、02#9(Hard Filter単体試験)と02#10(意味的近接で候補生成)を単体試験ファイルとして用意する。

**Architecture:** `latch.worker.matching` 新規パッケージに、AsyncConnection を第一引数に取る純関数群を置く(design §2.4): origin(起点読み込み・no-op判定)→ layer1(共通WHERE確定文字列+通過一覧参照)→ layer2(同一WHERE+距離昇順/id昇順LIMIT 50の単一SQL — design §2.1案A)→ candidates(正規化a<b+UPSERT)→ runner(オーケストレータ)。Layer 1 の全条件は layer1.py の確定文字列 `LAYER1_WHERE` に集約し、layer2 が同じ文字列を使うことで試験(02#9)と本番経路(Layer 2)のWHERE乖離を構造的に排除する。HNSW Indexスキャンは同点解消の第2キー(i.id)と排他のため逐次ソートで決定性を優先する(design §2.2)。stage1 への実配線・worker/main.py のDIは後続単位(本単位は関数群の確定まで)。

**Tech Stack:** 既存のみ(Python 3.13 / SQLAlchemy[asyncio]+asyncpg / pytest)。**依存追加なし・設定追加なし(settings.py 不触)・マイグレーション追加なし(alembic 0002がheadのまま — design §2.7)・docs(01〜12)改版なし・既存ファイル変更なし(§4)**。

**Spec:** `docs/plans/M2/ws-3-design.md`(agent1設計メモ。未解決論点なし — design §5は先送り記録・実装時確認・判断記録のみ。実装時確認2件(§5-2・§5-3)はTask 6・Task 5へ組み込み済み)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m2-ws-3`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(本単位は依存追加がないため既存lockのまま)。
- **共有ci-db運用**(STATUS運用ルール1〜3): compose常設環境(latch-ci)のDBとalembic状態はworktree間・スーパーバイザー検証の共有資産。本単位は**マイグレーション追加なし**のため `make migrate` 不要(実行しても冪等)。**開発はunit試験(`make lint`・`make test`)で完結させる** — 実装エージェントは `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` / `docker pull` を一切実行しない(**本単位にdocker系コマンドの許可はない** — design §4.3に単位固有の例外なし)。integration試験ファイルは作成するが**実行せず**、報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する。`make test` はintegrationファイルの収集(import)まで行うため、構文・importの正当性はunit実行で検証される。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。本計画の新規テストファイル(`test_origin.py`・`test_layer_sql.py`・`test_runner.py`・`test_matching_hardfilter.py`・`test_matching_retrieval.py`)がbackend/tests配下全体で一意であることをコミット前に `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることで確認する。
- **並走単位との競回避(design §3.4 — 最重要)**: **ws-2(Embedding Worker)が同時並走中**。交差しうる領域は worker/・intents/・tests の3つであり、本単位は次を厳守する:
  - **既存ファイルは一切変更しない**。作成するのは §4 の新規ファイルのみ(worker/ 直下の既存ファイル stage1.py・main.py・debounce.py・__init__.py・__main__.py、intents/ 全部、settings.py、alembic、compose.yaml、Makefile は不触。本単位は design §3.2 のとおり変更対象が最初からゼロ)
  - worker/ への追加は `worker/matching/` 新規ディレクトリのみ。tests への追加は `tests/unit/matching/` 新規ディレクトリと `tests/integration/test_matching_*.py` のみ(basename は ws-2 側の想定名 embedding 系と衝突しない)
  - git マージでパスが交差しない構造そのものが競合回避(design §3.4)。**各タスクのコミット前に `git status --short` で意図しない既存ファイルの変更が混入していないことを確認する**
- **固定値の遵守**: design §2 の採用判断と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| Layer 1 判定規則(自己除外 / 時間 [time_start, time_end) 交差 / 距離 ST_DWithin(center_a, center_b, r_a + r_b) / ペア予算 min(budget_max_a, budget_max_b)(NULLは無視)が500円未満の場合のみfail / 人数 2 ∈ [min_i, max_i] 双方で成立 / ブロック双方向 / category_primary完全一致 / 飲酒ペアは双方の作成者が20歳以上(users.birth_date・API作成・更新時検証に対する二重防御) / visibility判定対象外) | 06 §2 |
| Layer 2(ANN SearchはpgvectorのHNSW(cosine)で起点embeddingをクエリに上位K_v件 / embedding IS NULL・draftは生成元にも対象にもならない) | 06 §3 |
| K上限 K_v=50・切り詰め順序はスコア上位・同点はintent_id昇順で決定的に崩す | 06 §8 D-24・01 §11 |
| 比較の向き(起点=新規または更新されたIntentから既存Intentインデックスへ関連候補を引きに行く) | 01 §11 |
| ファネル層順(Layer 1が先に絞り、Layer 2が意味的近接上位を取る) | 01 §12 |
| match_candidates(正規化 intent_a_id < intent_b_id / intent_a_version・intent_b_version=評価時点 / UNIQUE(a,b,av,bv)で評価はバージョン組ごとに1レコード / 同一バージョン内の再評価は既存レコードを更新 / statusは pending/evaluated/skipped/closed・Layer 1〜2時点はpending) | 05 §2 |
| Index(0001作成済み): idx_intents_matching・idx_intents_geo GIST・idx_intents_budget・idx_intents_participants・idx_intents_embedding HNSW(vector_cosine_ops) | 05 §3 |
| 第2段トリガーはembedding_completed。作成・更新Eventの処理はEmbedding要求キックまででLayer 1以降は走らない | 06 §1・§9 |
| Layer 1〜3の予算 p95 ≤1秒 | 06 §1 |
| #9: 予算・時間・距離・人数を意図的に外すペアがいずれの候補にも現れない。ciでHard Filterの判定単体試験 | 02 §4 #9・10 §3 |
| #10: 語彙が一致しない意味的近接ペアが候補として生成される | 02 §4 #10 |
| 自己ペア除外(FR-17): 同一ユーザーの2 IntentがいずれのEvent処理でも生成されない | 10 §3 |
| K上限裏付け: Vector出力≦50・切り詰め決定的(同点intent_id昇順)・同一入力2回で同一結果 | 10 §4.6 |
| 冪等性: 同一Event 2回投入でmatch_candidatesが二重生成しない(UNIQUE制約) | 10 §4.7・12 M2完了条件 |
| 時刻操作はClock経由。テスト用パブリッシャーでembedding_completedを手動発火できる(※本単位はWorker不使用の直接関数呼び出しで代用 — design §4.2) | 10 §1 |
| C1〜C13の関連: C6(Layer 1〜5はembedding_completed起点のみ・配線は後続単位)・C7(Layer 1→2→3の逐次依存) | 12 §2 |
| M2-3・M2-4(ws-3単位定義)とG2条件(02#9・K上限・冪等性) | 12 §3 |
| 既存実装資産(stage1のembedding_hook予約・store.pyのCAST規約・_coerce_uuid・completion.py補完・integration conftest) | design §1.3 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **永続化は `sqlalchemy.text()` 生SQLのみ**(intents/store.py・worker/stage1.pyと同じ形式)。ORMモデル・リポジトリ層を作らない
- **bind param は `CAST(:x AS ...)` 形式**で型を明示する(ws-3報告書の教訓: `:name::type` はSQLAlchemyのbind param正規表現が認識せずリテラル落ちして503化。NULLを渡しうるパラメータは必須)。実dialect compileによる回帰検査を test_layer_sql.py が担う(test_store_sql.py と test_pubsub_bus_sdk_calls.py の流儀 — design §4.1)
- **時刻はClock経由のみ**。製品コード(`backend/src/latch/`)で実時間参照禁止(arch test `test_arch_no_direct_time.py` が強制)。起点の評価時刻は `load_origin` 呼び出し時点の `clock.now()` を `Origin.evaluated_at` として保持し、SQLの `:now`・UPSERTの `created_at/updated_at` に使う
- **例外は握り潰さない**: SQL失敗等は呼び出し側(stage1の再試行ループに載る将来経路)へ伝播(design §2.4)。本モジュール内にtry/except・ロギングを置かない(トレース計測はM4・design §1.4-7。ログ出力の追加もしない)
- **20歳判定はJST暦日の満年齢**: Python側は既存 `latch.users.service.age_years(birth_date, today)` を再利用し、SQL側も `AT TIME ZONE 'Asia/Tokyo'` で同じ暦日基準に揃える(§9固定値・design §2.6からの変更点)
- **K_v=50・500円はモジュール定数**(`K_VECTORS`・`PAIR_BUDGET_MIN_YEN`)。settings.py に追加しない(design §2.8-3)
- **改行・行長**: ruff(E,F,I,UP,B)と `ruff format`(行長88)を毎コミット通す
- unit試験は**外部プロセス不要・実時間待ちなし**(スタブconn・FakeClock)。SQL文字列の正当性は compile 検査(unit)+ 実DB試験(integration・スーパーバイザー検証時)の二段構え

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **対象の time_end が NULL(保存時補完への防御COALESCE)** — 補完前の行と交差判定するとき `i.time_end + interval '3 hours'` が効かなければ半開区間の交差が壊れる → Task 5 の `test_11_defense_coalesce_targets_pass`(time_end NULL の対象が通過)
2. **対象の geo_radius_m が NULL(既定1,000mへの防御COALESCE)** — COALESCEが効かなければ距離計算がNULLになり当該対象が黙って全落ちする → Task 5 の `test_11_defense_coalesce_targets_pass`(geo_radius_m NULL の対象が通過)
3. **ペア予算500円ちょうど(>= 500 はfailしない)** — 06 §2「500円未満の場合のみfail」の境界。499だけ見ると `>` と `>=` を区別できない → Task 5 の `test_1_budget_conditions`(O_499×T_500 が通過)
4. **UPSERTのDO UPDATEが status を壊さない** — DO UPDATE SET に status を含めると再評価で ws-4 以降の評価結果をペンディングへ巻き戻す → Task 3 の `test_upsert_do_update_touches_score_only`(SQLピン)+ Task 6 の `test_4_idempotent_upsert`(2回実行後も status='pending')
5. **K_v境界で完全同点が分断されるときの決定的順序** — 同点内の順序が実装依存だと10 §4.6の裏付け試験が再現しない → Task 6 の `test_2_kv_truncation_deterministic`(同点54件→UUID昇順先頭50件・2回実行で同一結果)

## 4. スコープ(作成・変更するファイル一覧)

作成(**すべて新規ファイル・既存ファイルへの変更はゼロ** — design §3.1・§3.2):

```text
backend/src/latch/worker/matching/__init__.py    (Task 1で空作成 → Task 4で公開APIの最終形)
backend/src/latch/worker/matching/origin.py      (Task 1。起点読み込み・no-op判定・bind params組立)
backend/src/latch/worker/matching/layer1.py      (Task 2。LAYER1_WHERE確定文字列+hard_filter_candidates)
backend/src/latch/worker/matching/layer2.py      (Task 2。retrieve_topk・単一SQL・K_VECTORS=50)
backend/src/latch/worker/matching/candidates.py  (Task 3。normalize_pair・UPSERT)
backend/src/latch/worker/matching/runner.py      (Task 4。run_candidate_retrieval・RetrievalOutcome)
backend/tests/unit/matching/test_origin.py       (Task 1作成・Task 3追記)
backend/tests/unit/matching/test_layer_sql.py    (Task 2作成・Task 3追記)
backend/tests/unit/matching/test_runner.py       (Task 4)
backend/tests/integration/test_matching_hardfilter.py  (Task 5。作成のみ・実行しない)
backend/tests/integration/test_matching_retrieval.py   (Task 6。作成のみ・実行しない)
docs/plans/M2/ws-3-report.md                     (Task 7。報告ファイル)
```

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`(gitignore済み)。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **docker系コマンド一切**(§0): `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` / `docker pull`。api/workerイメージ再ビルドとintegration実行はスーパーバイザーの検証手順に含まれる
- **既存ファイルの一切の変更**(design §3.2・§3.4): `backend/src/latch/worker/stage1.py`・`worker/main.py`・`worker/debounce.py`・`worker/__init__.py`・`worker/__main__.py`(フック実配線は後続単位)、`backend/src/latch/intents/` 全体(events.py・store.py・service.py・completion.py 等。embedding書き込みはws-2)、`backend/src/latch/settings.py`(§2.8-3)、`backend/src/latch/`配下の auth / users / ratelimit / geo / llm / g1gate / events / core 各モジュール、`backend/alembic/`(0002がheadのまま)、`backend/pyproject.toml`+`backend/uv.lock`(依存追加なし)
- `backend/tests/conftest.py`・`backend/tests/integration/conftest.py`(db_engine・api_client fixture を参照のみで使う)、`backend/tests/` の既存テストファイルすべて(test_events_pipeline.py の user_env・test_4 は不変)
- `compose.yaml`・`Makefile`・`frontend/`・`prototype/`・`docker/`・`.mise.toml`・`.gitignore`・`README.md`
- `docs/`(01〜12・learn・reviews — design §2.7で改版対象なし)、`docs/plans/STATUS.md`(スーパーバイザー管理)、`docs/plans/M0/`・`docs/plans/M1/`
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4の後続単位スコープ):
  - embedding_completed Event から本処理への配線(stage1へのmatchingフック追加・worker/main.pyへのDI — 後続のパイプライン組み込み単位)
  - Embeddingキックの実体・embedding_completed発行・intents.embedding書き込み(ws-2・並走中)
  - Layer 3以降(cheap_score・jev_result・latch_score・match_candidates.status の evaluated/skipped/closed 遷移 — ws-4〜)
  - 30分Bucket再評価・catch-upスキャンの呼び出し経路(ws-6。本単位はUPSERTの更新側仕様だけ満たす)
  - 時間・場所flexibilityの拡張判定(MVPでは常にnull — design §1.4-5。到達不能コードを書かない)
  - HNSW Index スキャン最適化(iterative scan — design §2.2・§5-1。逐次ソートで確定値の意味を満たす)
  - K_v・500円のsettings化・match_candidates一括INSERT(executemany)・Layer 1通過件数上限・起点・対象のFOR UPDATE(design §2.8のYAGNI切り捨て)
  - トレース計測・Queue lag(M4)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 7で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分を含む)
   検証: `make lint && make test` — ともにexit 0
2. **integration 2ファイル(計17試験)が収集できる** — **ただし実行はしない**(§0。実DB・compose常設apiが必要なためスーパーバイザー検証時に実施)
   検証(実装側): `cd backend && uv run pytest --collect-only tests/integration/test_matching_hardfilter.py tests/integration/test_matching_retrieval.py -q` がexit 0(17件収集)。報告書に「**test-ci=スーパーバイザー検証待ち**」と記録
3. **実時間参照が `core/clock.py` のみ(matching/配下はヒットしない)**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
4. **`backend/alembic/` と `docs/`(01〜12)に差分なし**
   検証: `git diff --stat main -- backend/alembic docs` — 出力なし(reportはTask 7で作成後コミットするため、この検証はreportコミット前に実施)
5. **変更ファイルが§4の一覧どおり(既存ファイル変更ゼロ)**
   検証: Task 7 Step 3の報告コミット後に `git diff --name-only main | sort` が§4の作成一覧(12ファイル・report込み)と完全一致。`git status --short` が空(未コミット変更なし)
6. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし
7. **既存unit試験がすべてグリーンのまま**(本単位は既存コードに触れないため回帰はないはず — 証明として `make test` の全件数を報告書へ記録)

## 7. 報告形式

**結果ファイル**: `docs/plans/M2/ws-3-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M2 ws-3(Layer 1 Hard Filter + Layer 2 Candidate Retrieval)実行報告

- ブランチ: m2-ws-3 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も)> |
| 2 | integration 17試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | <collect-only出力> |
| 3 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 4 | alembic・docs無変更 | PASS/FAIL | <git diff --stat 出力(空なら「空」)> |
| 5 | 変更ファイル=新規12ファイルのみ | PASS/FAIL | <git diff --name-only出力> |
| 6 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 7 | 既存unit試験グリーン維持 | PASS/FAIL | <make test 全件数(mainの677相当から減っていないこと)> |

## design §5 実装時確認事項の結果
1. (先送り記録)Layer 2 Index最適化: 本単位は逐次ソートのまま(変更なし)。再設計は10 §4.2負荷試験で p95 1秒を割った場合のみ
2. asyncpg の vector 列読み取り: <test_1_semantic_pair の isinstance(embedding, str) assert 結果。文字列でなかった場合は _embedding_text の防御組立を通った旨と実測の型>
3. EXTRACT(YEAR FROM AGE(...)) の挙動ピン: <test_8_age_boundary の結果(誕生日当日=20歳通過・前日=19歳fail)。暦どおりであれば追加対応不要と記録>

## 固定値の変更有無(design.md §2・本計画§9)
- 単一SQL(案A・design §2.1): 変更なし / 変更あり(<前→後+理由>)
- 逐次ソート決定性優先(design §2.2): 変更なし / 変更あり(<前→後+理由>)
- UPSERT DO UPDATE=retrieval_scoreのみ(design §2.3): 変更なし / 変更あり(<前→後+理由>)
- 飲酒年齢のJST暦日基準(本計画§9・design §2.6表からの一変更点): 変更なし / 変更あり(<前→後+理由>)
- 本計画§9のIF確定事項(関数シグネチャ・LAYER1_WHERE・K_VECTORS等): 変更なし / 変更あり(<前→後+理由>)

## スーパーバイザー検証手順(test-ci実行時)
1. `docker compose build api` — apiイメージ再ビルド(monitoring対象コードは本単位にないが ws-2 と同時検証になる可能性があるため STATUS運用ルール4どおり。マイグレーション追加なしのため `make migrate` は不要)
2. `make test-ci` — 既存全数+test_matching_hardfilter 13件+test_matching_retrieval 4件がグリーンで完了条件2を検証(本単位はDBスキーマを変えないが、ws-2と検証タイミングを重ねない — STATUS運用ルール1)
3. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空(運用ルール5)

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件7項目の結果一覧(項目2の実行部分は「test-ci=スーパーバイザー検証待ち」)。

---

## 8. 実装ステップ(TDD。Task 1〜7の順で実行する)

### Task 1: matchingパッケージと origin.py(起点読み込み・no-op判定・bind params)

**Files:**
- Create: `backend/src/latch/worker/matching/__init__.py`(この時点ではdocstringのみ)
- Create: `backend/src/latch/worker/matching/origin.py`
- Test: `backend/tests/unit/matching/test_origin.py`

**Interfaces:**
- Consumes: `Clock`/`FakeClock`(core/clock)・`latch.intents.completion.default_time_end`/`DEFAULT_RADIUS_M`・`latch.users.service.age_years`・`AsyncConnection`(SQLAlchemy)
- Produces(以降全タスク・integration試験が使用):
  - スキップ理由定数: `SKIP_NOT_FOUND = "origin_not_found"` / `SKIP_NOT_ACTIVE = "origin_not_active"` / `SKIP_EMBEDDING_NULL = "origin_embedding_null"` / `SKIP_GEO_MISSING = "origin_geo_missing"` / `SKIP_TIME_MISSING = "origin_time_missing"` / `SKIP_PARTICIPANTS = "origin_participants_range"`
  - `Origin`(frozen dataclass): `intent_id: uuid.UUID` / `version: int` / `user_id: uuid.UUID` / `category_primary: str` / `alcohol_involved: bool` / `budget_max: int | None` / `participants_min: int` / `participants_max: int` / `geo_lon: float` / `geo_lat: float` / `geo_radius_m: int`(NULLはDEFAULT_RADIUS_M=1000補完済み)/ `time_start: datetime` / `time_end: datetime`(NULLはtime_start+3h補完済み)/ `embedding: str`('[0.1,...]'文字列表現)/ `user_ge_20: bool`(評価時点JST暦日で満20歳)/ `evaluated_at: datetime`(load_origin時点のclock.now())
  - `OriginLoad`(frozen dataclass): `origin: Origin | None` / `skip_reason: str | None`(origin と skip_reason は排他)
  - `async load_origin(conn: AsyncConnection, clock: Clock, intent_id: uuid.UUID) -> OriginLoad`
  - `bind_params(origin: Origin) -> dict`(LAYER1_WHERE の bind param 一式。`now` キーに `origin.evaluated_at`)
  - `_embedding_text(raw: object) -> str | None`(vector列読み取り値の文字列化・design §5-2の防御)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_origin.py` を作成:

```python
"""origin(起点読み込み・no-op判定・bind params)のunit試験(design §4.1)。

スタブ行で決定的に。20歳計算はusers.age_yearsと同じJST暦日の満年齢
(06 §2二重防御の基準一致)。実DBのSQL正当性はintegration(test-ci)が担う。
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from latch.core.clock import FakeClock
from latch.worker.matching.origin import (
    SKIP_EMBEDDING_NULL,
    SKIP_GEO_MISSING,
    SKIP_NOT_ACTIVE,
    SKIP_NOT_FOUND,
    SKIP_PARTICIPANTS,
    SKIP_TIME_MISSING,
    _embedding_text,
    bind_params,
    load_origin,
)

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-29 21:00
IID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
USER = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
LEAP_NOW = datetime(2026, 2, 28, 3, 0, 0, tzinfo=UTC)  # JST 2026-02-28 12:00


class _FakeMappings:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return _FakeMappings(self._row)


class StubConn:
    """load_originが1回呼ぶexecuteへスタブ行を返す。"""

    def __init__(self, row):
        self.row = row

    async def execute(self, stmt, params=None):
        return _FakeResult(self.row)


def _row(**overrides):
    """active・補完対象ありの標準行(時間はNOW+3h開始・end未補完)。"""
    base = dict(
        id=str(IID),
        version=1,
        user_id=str(USER),
        category_primary="drinking",
        alcohol_involved=False,
        budget_max=None,
        participants_min=2,
        participants_max=4,
        geo_radius_m=None,
        time_start=NOW + timedelta(hours=3),
        time_end=None,
        status="active",
        embedding="[1.0,0.0]",
        lon=130.5581,
        lat=31.5965,
        birth_date=date(1990, 4, 1),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _load(row, clock=None):
    return await load_origin(StubConn(row), clock or FakeClock(NOW), IID)


async def test_active_origin_built_with_completions():
    """active行は補完済みでOrigin化(end未指定→+3h・radius NULL→1000)。"""
    loaded = await _load(_row())
    assert loaded.skip_reason is None
    org = loaded.origin
    assert org is not None
    assert org.intent_id == IID and org.user_id == USER  # str→UUID復元
    assert org.time_end == org.time_start + timedelta(hours=3)  # default_time_end
    assert org.geo_radius_m == 1000  # DEFAULT_RADIUS_M補完
    assert org.embedding == "[1.0,0.0]"
    assert org.evaluated_at == NOW
    assert org.user_ge_20 is True  # 1990年生まれ・2026年で36歳


async def test_skip_not_found():
    loaded = await _load(None)
    assert loaded.origin is None
    assert loaded.skip_reason == SKIP_NOT_FOUND


async def test_skip_not_active_for_draft():
    loaded = await _load(_row(status="draft"))
    assert loaded.skip_reason == SKIP_NOT_ACTIVE


async def test_skip_embedding_null():
    loaded = await _load(_row(embedding=None))
    assert loaded.skip_reason == SKIP_EMBEDDING_NULL


async def test_skip_geo_missing():
    loaded = await _load(_row(lon=None, lat=None))
    assert loaded.skip_reason == SKIP_GEO_MISSING


async def test_skip_time_missing():
    loaded = await _load(_row(time_start=None))
    assert loaded.skip_reason == SKIP_TIME_MISSING


async def test_skip_participants_range():
    """起点が人数条件 2∈[min,max] を満たさない→no-op(06 §2・design §2.5)。"""
    loaded = await _load(_row(participants_min=3, participants_max=4))
    assert loaded.skip_reason == SKIP_PARTICIPANTS


async def test_user_ge_20_at_exact_birthday_and_day_before():
    """誕生日当日=満20歳(True)・誕生日前日=19歳(False)。JST暦日基準。"""
    on_day = await _load(_row(birth_date=date(2006, 9, 29)))  # JST今日が誕生日
    assert on_day.origin is not None and on_day.origin.user_ge_20 is True
    day_before = await _load(_row(birth_date=date(2006, 9, 30)))  # 誕生日は明日
    assert day_before.origin is not None and day_before.origin.user_ge_20 is False


async def test_user_ge_20_leap_day_boundary():
    """2/29生まれ: うるう年でない年の2/28はまだ加算されず21歳(20歳以上)。"""
    loaded = await _load(_row(birth_date=date(2004, 2, 29)), clock=FakeClock(LEAP_NOW))
    assert loaded.origin is not None and loaded.origin.user_ge_20 is True


def test_embedding_text_variants():
    """文字列はそのまま・配列は組立直し・NoneはNone(design §5-2の防御)。"""
    assert _embedding_text("[0.1,0.2]") == "[0.1,0.2]"
    assert _embedding_text(None) is None
    assert _embedding_text([0.1, 0.2]) == "[0.1,0.2]"


async def test_bind_params_keys_and_values():
    """Layer1/2共通bind param一式(§9固定値・CAST対象を含む)。"""
    loaded = await _load(_row())
    assert loaded.origin is not None
    params = bind_params(loaded.origin)
    assert set(params) == {
        "origin_user_id",
        "origin_category",
        "origin_time_start",
        "origin_time_end",
        "origin_lon",
        "origin_lat",
        "origin_radius_m",
        "origin_budget",
        "origin_alcohol",
        "origin_user_ge_20",
        "now",
    }
    assert params["origin_user_id"] == USER
    assert params["origin_budget"] is None
    assert params["now"] == loaded.origin.evaluated_at
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_origin.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.matching'` — importで収集エラー)

- [ ] **Step 3: `__init__.py` と origin.py を実装する**

`backend/src/latch/worker/matching/__init__.py`(この時点。Task 4で最終形へ更新):

```python
"""第2段マッチング前半: Layer 1 Hard Filter + Layer 2 Candidate Retrieval(M2 ws-3)。

公開APIは AsyncConnection を第一引数に取る純関数群(design §2.4)。
stage1(embedding_completed種別)への実配線は後続単位。
"""
```

`backend/src/latch/worker/matching/origin.py`:

```python
"""起点Intentの読み込みとLayer 1/2共通パラメータの組立(design §2.5・§2.6)。

06 §3「生成元にも対象にもならない」条件(非active・embedding NULL等)は
検索の前にここで落とす(no-op)。time_end・geo_radius_m の NULL は
保存時補完(intents/completion.py)への防御としてここでも補完する。
20歳判定はAPI側検証(users.age_years・JST暦日)と同じ基準(06 §2の二重防御)。
SQLはtext()生SQL・CAST(:x AS ...)形式(§2グローバル制約)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.core.clock import Clock
from latch.intents.completion import DEFAULT_RADIUS_M, default_time_end
from latch.users.service import age_years

# no-op理由(design §2.5・RetrievalOutcome.skip_reason の値域)
SKIP_NOT_FOUND = "origin_not_found"
SKIP_NOT_ACTIVE = "origin_not_active"
SKIP_EMBEDDING_NULL = "origin_embedding_null"
SKIP_GEO_MISSING = "origin_geo_missing"
SKIP_TIME_MISSING = "origin_time_missing"
SKIP_PARTICIPANTS = "origin_participants_range"

_SELECT_ORIGIN = text("""
    SELECT i.id, i.version, i.user_id, i.category_primary, i.alcohol_involved,
           i.budget_max, i.participants_min, i.participants_max,
           i.geo_radius_m, i.time_start, i.time_end, i.status, i.embedding,
           ST_X(i.geo_center::geometry) AS lon,
           ST_Y(i.geo_center::geometry) AS lat,
           u.birth_date
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE i.id = CAST(:intent_id AS uuid)
""")


@dataclass(frozen=True)
class Origin:
    """評価の起点(design §2.5)。time_end・geo_radius_m は補完済み。"""

    intent_id: uuid.UUID
    version: int
    user_id: uuid.UUID
    category_primary: str
    alcohol_involved: bool
    budget_max: int | None
    participants_min: int
    participants_max: int
    geo_lon: float
    geo_lat: float
    geo_radius_m: int
    time_start: datetime
    time_end: datetime
    embedding: str  # '[0.1, ...]'(asyncpgはvector列を文字列で返す — design §5-2)
    user_ge_20: bool  # 評価時点のJST暦日で満20歳(users.age_yearsと同基準)
    evaluated_at: datetime  # load_origin時点のclock.now()(bind_paramsのnow)


@dataclass(frozen=True)
class OriginLoad:
    """load_originの戻り。originとskip_reasonは排他。"""

    origin: Origin | None
    skip_reason: str | None


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策 — 3度目の教訓)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _embedding_text(raw: object) -> str | None:
    """vector列の読み取り値をCAST(:x AS vector)へ渡せる文字列へ(design §5-2)。

    asyncpgはvector型を文字列'[...]'で返す想定。想定が外れて配列等で
    返った場合も文字列へ組立直す(設計不変 — design §5-2)。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return "[" + ",".join(repr(float(v)) for v in raw) + "]"


async def load_origin(
    conn: AsyncConnection, clock: Clock, intent_id: uuid.UUID
) -> OriginLoad:
    """起点を読みno-op判定(design §2.5)。行なしはskip理由付きの空結果。"""
    row = (await conn.execute(_SELECT_ORIGIN, {"intent_id": intent_id})).mappings().first()
    if row is None:
        return OriginLoad(origin=None, skip_reason=SKIP_NOT_FOUND)
    if row.status != "active":
        return OriginLoad(origin=None, skip_reason=SKIP_NOT_ACTIVE)
    embedding = _embedding_text(row.embedding)
    if embedding is None:
        return OriginLoad(origin=None, skip_reason=SKIP_EMBEDDING_NULL)
    if row.lon is None or row.lat is None:
        return OriginLoad(origin=None, skip_reason=SKIP_GEO_MISSING)
    if row.time_start is None:
        return OriginLoad(origin=None, skip_reason=SKIP_TIME_MISSING)
    if not (row.participants_min <= 2 <= row.participants_max):
        return OriginLoad(origin=None, skip_reason=SKIP_PARTICIPANTS)
    origin = Origin(
        intent_id=_coerce_uuid(row.id),
        version=row.version,
        user_id=_coerce_uuid(row.user_id),
        category_primary=row.category_primary,
        alcohol_involved=row.alcohol_involved,
        budget_max=row.budget_max,
        participants_min=row.participants_min,
        participants_max=row.participants_max,
        geo_lon=float(row.lon),
        geo_lat=float(row.lat),
        geo_radius_m=(
            row.geo_radius_m if row.geo_radius_m is not None else DEFAULT_RADIUS_M
        ),
        time_start=row.time_start,
        time_end=(
            row.time_end if row.time_end is not None else default_time_end(row.time_start)
        ),
        embedding=embedding,
        user_ge_20=age_years(row.birth_date, clock.jst_date()) >= 20,
        evaluated_at=clock.now(),
    )
    return OriginLoad(origin=origin, skip_reason=None)


def bind_params(origin: Origin) -> dict:
    """Layer 1/2 共通の bind param 一式(design §2.6)。layer2は+embedding。

    NULLを渡しうる origin_budget はSQL側でCAST(:x AS int)する(§2制約)。
    now は飲酒年齢のJST変換(evaluated_at=tz-aware UTC)に使う。
    """
    return {
        "origin_user_id": origin.user_id,
        "origin_category": origin.category_primary,
        "origin_time_start": origin.time_start,
        "origin_time_end": origin.time_end,
        "origin_lon": origin.geo_lon,
        "origin_lat": origin.geo_lat,
        "origin_radius_m": origin.geo_radius_m,
        "origin_budget": origin.budget_max,
        "origin_alcohol": origin.alcohol_involved,
        "origin_user_ge_20": origin.user_ge_20,
        "now": origin.evaluated_at,
    }
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_origin.py -v`
Expected: PASS(12件)

- [ ] **Step 5: 全体確認してCommit**

Run: `make lint && make test`
Expected: ともにexit 0(既存試験は無影響 — 新規ファイルのみのため)

```bash
git add backend/src/latch/worker/matching/__init__.py \
  backend/src/latch/worker/matching/origin.py \
  backend/tests/unit/matching/test_origin.py
git status --short  # 上記3ファイルのみ(mainは触っていないこと)
git commit -m "feat: matching起点読み込み(no-op判定・補完・bind params)"
```

### Task 2: layer1.py(WHERE共通ビルダー)+ layer2.py(単一SQL上位K_v)

**Files:**
- Create: `backend/src/latch/worker/matching/layer1.py`
- Create: `backend/src/latch/worker/matching/layer2.py`
- Test: `backend/tests/unit/matching/test_layer_sql.py`

**Interfaces:**
- Consumes: `Origin`・`bind_params`(Task 1)
- Produces:
  - `layer1.PAIR_BUDGET_MIN_YEN = 500`(06 §2)
  - `layer1.LAYER1_WHERE: str`(Layer 1 全条件の確定文字列。**layer2 がこの文字列をそのまま使う — design §2.1案Aの核心**)
  - `layer1.HardCandidate`(frozen dataclass): `intent_id: uuid.UUID` / `version: int` / `user_id: uuid.UUID`
  - `async layer1.hard_filter_candidates(conn, origin) -> list[HardCandidate]`(ORDER BYなし・LIMITなし。02#9単体試験・将来トレース用)
  - `layer2.K_VECTORS = 50`(D-24)
  - `layer2.RetrievedCandidate`(frozen dataclass): `intent_id: uuid.UUID` / `version: int` / `similarity: float`(cosine類似度=1−距離)
  - `async layer2.retrieve_topk(conn, origin) -> tuple[list[RetrievedCandidate], int]`(上位K_v件とLayer 1通過件数 — §9固定値)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_layer_sql.py` を作成:

```python
"""Layer 1/2/candidates のSQL文字列ピン+実dialect compile検査(design §4.1)。

test_store_sql.py と test_pubsub_bus_sdk_calls.py の流儀(bind param の
部分認識=リテラル落ちを compile 結果で摘発する。M1 ws-3で503化した欠陥
系統の回帰防止)。ここでは実DBに繋がない(実挙動はintegration)。
"""

from sqlalchemy.dialects import postgresql

from latch.worker.matching import layer1, layer2

_WHERE_KEYS = (
    "origin_user_id",
    "origin_category",
    "origin_time_start",
    "origin_time_end",
    "origin_lon",
    "origin_lat",
    "origin_radius_m",
    "origin_budget",
    "origin_alcohol",
    "origin_user_ge_20",
    "now",
)
_LAYER2_KEYS = _WHERE_KEYS + ("origin_embedding",)
_UPSERT_KEYS = (
    "intent_a_id",
    "intent_b_id",
    "intent_a_version",
    "intent_b_version",
    "retrieval_score",
    "now",
)


def test_layer1_where_contains_all_conditions():
    """06 §2・design §2.6確定表の全条件がWHEREに入っている。"""
    where = layer1.LAYER1_WHERE
    assert "i.status = 'active'" in where
    assert "i.embedding IS NOT NULL" in where
    assert "i.user_id <> CAST(:origin_user_id AS uuid)" in where
    assert "i.category_primary = :origin_category" in where
    assert "i.time_start < :origin_time_end" in where
    assert "COALESCE(i.time_end, i.time_start + interval '3 hours')" in where
    assert "ST_DWithin" in where
    assert "LEAST(" in where
    assert ">= 500" in where  # PAIR_BUDGET_MIN_YEN(06 §2の確定値そのまま)
    assert "i.participants_min <= 2" in where
    assert "i.participants_max >= 2" in where
    assert "NOT EXISTS" in where and "blocks" in where
    assert "EXTRACT(YEAR FROM AGE(" in where
    assert "AT TIME ZONE 'Asia/Tokyo'" in where  # JST暦日基準(§9固定値)


def test_layer1_where_excludes_visibility():
    """visibility は判定対象外(06 §2 — Layer 5のproposal分岐が担う)。"""
    assert "visibility" not in layer1.LAYER1_WHERE


def test_layer1_and_layer2_share_the_same_where():
    """design §2.1案A: 02#9単体試験と本番経路(Layer 2)のWHERE乖離なし。"""
    assert layer1.LAYER1_WHERE.strip() in str(layer2._SELECT_TOPK)


def test_hard_filter_sql_has_no_order_no_limit():
    sql = str(layer1._SELECT_HARD)
    assert "ORDER BY" not in sql
    assert "LIMIT" not in sql


def test_layer2_sql_order_limit_and_window():
    """距離ASC・id ASC(同点解消)の複合ORDER BY + LIMIT 50 + 通過件数window。"""
    sql = str(layer2._SELECT_TOPK)
    assert (
        "ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC,"
        " i.id ASC" in sql
    )
    assert "LIMIT 50" in sql  # K_VECTORS(D-24)
    assert "COUNT(*) OVER () AS pass_count" in sql
    assert "AS similarity" in sql


def test_layer1_sql_all_bind_params_recognized():
    """compiled文字列に未変換の ':name' が残らない=部分認識ゼロ。"""
    compiled = str(layer1._SELECT_HARD.compile(dialect=postgresql.dialect()))
    for key in _WHERE_KEYS:
        assert f":{key}" not in compiled, key


def test_layer2_sql_all_bind_params_recognized():
    compiled = str(layer2._SELECT_TOPK.compile(dialect=postgresql.dialect()))
    for key in _LAYER2_KEYS:
        assert f":{key}" not in compiled, key
```

※ Task 3 の Step 1 で import 行を `from latch.worker.matching import candidates, layer1, layer2` へ更新し UPSERT 分の試験を追記する。

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer_sql.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.matching.layer1'`)

- [ ] **Step 3: layer1.py を実装する**

`backend/src/latch/worker/matching/layer1.py`:

```python
"""Layer 1 Hard Filter(06 §2・design §2.1案A・§2.6確定表)。

SQL+PostGISのみ(AI不使用)。全条件を WHERE 句の確定文字列に集約し、
Layer 2 が同一文字列を使う(層順を1クエリで体現・試験と本番のWHERE乖離
なし — design §2.1)。pass/fail判定はDBへ一任しPythonで再判定しない。
visibilityは判定対象外(06 §2)。flexibilityはMVPで常にnullのため実装
しない(design §1.4-5)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.worker.matching.origin import Origin, bind_params

# 06 §2: ペア予算 min(budget_max_a, budget_max_b)(NULLは無視)が
# 500円未満の場合のみfail(500・比較演算は06 §2の確定値そのまま)
PAIR_BUDGET_MIN_YEN = 500

# ペア予算式(NULLは無視=双方制約なしはfailしない — 06 §2)
_BUDGET_PAIR = """(
        CASE
            WHEN CAST(:origin_budget AS int) IS NULL AND i.budget_max IS NULL THEN NULL
            WHEN CAST(:origin_budget AS int) IS NULL THEN i.budget_max
            WHEN i.budget_max IS NULL THEN CAST(:origin_budget AS int)
            ELSE LEAST(CAST(:origin_budget AS int), i.budget_max)
        END
    )"""

# Layer 1 の全条件(06 §2・design §2.6)。layer2 が同一文字列を使用する
LAYER1_WHERE = f"""
    i.status = 'active'
    AND i.embedding IS NOT NULL
    AND i.user_id <> CAST(:origin_user_id AS uuid)
    AND i.category_primary = :origin_category
    AND i.time_start < :origin_time_end
    AND :origin_time_start < COALESCE(i.time_end, i.time_start + interval '3 hours')
    AND i.geo_center IS NOT NULL
    AND ST_DWithin(
        i.geo_center,
        ST_SetSRID(
            ST_MakePoint(CAST(:origin_lon AS float8), CAST(:origin_lat AS float8)),
            4326)::geography,
        CAST(
            CAST(:origin_radius_m AS int) + COALESCE(i.geo_radius_m, 1000)
            AS double precision))
    AND ({_BUDGET_PAIR} IS NULL
         OR {_BUDGET_PAIR} >= {PAIR_BUDGET_MIN_YEN})
    AND i.participants_min <= 2
    AND i.participants_max >= 2
    AND NOT EXISTS (
        SELECT 1 FROM blocks b
        WHERE (b.blocker_id = CAST(:origin_user_id AS uuid) AND b.blocked_id = i.user_id)
           OR (b.blocker_id = i.user_id AND b.blocked_id = CAST(:origin_user_id AS uuid)))
    AND (
        (NOT (:origin_alcohol OR i.alcohol_involved))
        OR (
            :origin_user_ge_20
            AND EXTRACT(YEAR FROM AGE(
                (CAST(:now AS timestamptz) AT TIME ZONE 'Asia/Tokyo'),
                CAST(u.birth_date AS timestamp))) >= 20
        )
    )
"""

_SELECT_HARD = text(f"""
    SELECT i.id, i.version, i.user_id
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE {LAYER1_WHERE}
""")


@dataclass(frozen=True)
class HardCandidate:
    """Layer 1 通過対象(02#9単体試験・将来トレース用 — design §3.1)。"""

    intent_id: uuid.UUID
    version: int
    user_id: uuid.UUID


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策 — 3度目の教訓)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def hard_filter_candidates(
    conn: AsyncConnection, origin: Origin
) -> list[HardCandidate]:
    """Layer 1 通過集合(ORDER BYなし・LIMITなし。design §3.1)。"""
    rows = (await conn.execute(_SELECT_HARD, bind_params(origin))).all()
    return [
        HardCandidate(
            intent_id=_coerce_uuid(r[0]), version=r[1], user_id=_coerce_uuid(r[2])
        )
        for r in rows
    ]
```

- [ ] **Step 4: layer2.py を実装する**

`backend/src/latch/worker/matching/layer2.py`:

```python
"""Layer 2 Candidate Retrieval(06 §3・design §2.1案A・§2.2)。

Layer 1 条件込みの単一SQLで cosine 距離上位 K_v=50 を取得する。ORDER BY
の第2キー i.id(同点はintent_id昇順 — D-24・10 §4.6)を付けたためHNSW
Indexスキャンは使われず逐次ソートになる(決定性優先 — design §2.2。
負荷試験(10 §4.2)でp95 1秒を割った場合のみiterative scanで再設計)。
retrieval_score には cosine類似度(1 − 距離)を記録する(design §2.6)。
起点embeddingは文字列表現をそのまま再キャストして渡す(asyncpgがvector
列を文字列で返すため — design §2.6・§5-2)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.worker.matching.layer1 import LAYER1_WHERE
from latch.worker.matching.origin import Origin, bind_params

# D-24(06 §8・01 §11): Vector検索のK上限=次層へ渡す出力数の上限
K_VECTORS = 50

_SELECT_TOPK = text(f"""
    SELECT i.id,
           i.version,
           1 - (i.embedding <=> CAST(:origin_embedding AS vector)) AS similarity,
           COUNT(*) OVER () AS pass_count
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE {LAYER1_WHERE}
    ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC, i.id ASC
    LIMIT {K_VECTORS}
""")


@dataclass(frozen=True)
class RetrievedCandidate:
    """Layer 2 通過対象(cosine類似度つき)。"""

    intent_id: uuid.UUID
    version: int
    similarity: float


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策 — 3度目の教訓)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def retrieve_topk(
    conn: AsyncConnection, origin: Origin
) -> tuple[list[RetrievedCandidate], int]:
    """Layer 1 通過のうち距離上位K_v件と、通過件数(LIMIT前 — §9固定値)。

    pass_count は COUNT(*) OVER()(window関数はLIMIT前に評価される)で
    同一SQL内で得る(design §2.1の中間表現を持たない方針を崩さない)。
    """
    params = {**bind_params(origin), "origin_embedding": origin.embedding}
    rows = (await conn.execute(_SELECT_TOPK, params)).all()
    candidates = [
        RetrievedCandidate(
            intent_id=_coerce_uuid(r[0]), version=r[1], similarity=float(r[2])
        )
        for r in rows
    ]
    pass_count = int(rows[0][3]) if rows else 0
    return candidates, pass_count
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer_sql.py -v`
Expected: PASS(7件)

- [ ] **Step 6: 全体確認してCommit**

Run: `make lint && make test`
Expected: ともにexit 0

```bash
git add backend/src/latch/worker/matching/layer1.py \
  backend/src/latch/worker/matching/layer2.py \
  backend/tests/unit/matching/test_layer_sql.py
git status --short
git commit -m "feat: Layer 1共通WHERE確定文字列とLayer 2単一SQL(距離ASC/idASC LIMIT 50)"
```

### Task 3: candidates.py(正規化 intent_a < intent_b・UPSERT)

**Files:**
- Create: `backend/src/latch/worker/matching/candidates.py`
- Test: `backend/tests/unit/matching/test_origin.py` へ追記(正規化 — design §4.1の記載どおり)
- Test: `backend/tests/unit/matching/test_layer_sql.py` へ追記(UPSERTのSQLピン)

**Interfaces:**
- Consumes: `Origin`(Task 1)
- Produces(Task 4・integration試験が使用):
  - `CandidatePair`(frozen dataclass): `intent_a_id: uuid.UUID` / `intent_b_id: uuid.UUID` / `retrieval_score: float`
  - `normalize_pair(origin: Origin, candidate_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]`(intent_a < intent_b・UUID比較はPython側 — design §2.3)
  - `async upsert_candidate(conn, *, origin: Origin, candidate_id: uuid.UUID, candidate_version: int, similarity: float) -> CandidatePair`(UPSERT実行・design §2.3)

- [ ] **Step 1: 失敗するテストを書く(両ファイルへ追記)**

`backend/tests/unit/matching/test_origin.py` へファイル末尾に追記(import節へ `from latch.worker.matching.candidates import normalize_pair` を追加):

```python
# -- intent_a < intent_b 正規化(design §2.3・§4.1) --

async def test_normalize_pair_orders_by_uuid():
    """正規化 intent_a_id < intent_b_id(UUID比較はPython側 — 05 §2)。"""
    loaded = await _load(_row())
    assert loaded.origin is not None
    org = loaded.origin
    smaller = uuid.UUID("00000000-0000-4000-8000-000000000001")
    bigger = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    # 起点が大きい側 → a=候補
    assert normalize_pair(org, smaller) == (smaller, org.intent_id)
    # 起点が小さい側 → a=起点
    assert normalize_pair(org, bigger) == (org.intent_id, bigger)
```

`backend/tests/unit/matching/test_layer_sql.py` へファイル末尾に追記(import節を `from latch.worker.matching import candidates, layer1, layer2` へ更新 — Task 2 Step 1の注記どおり):

```python
# -- candidates UPSERT のSQLピン(design §2.3) --

def test_upsert_on_conflict_targets_unique_columns():
    sql = str(candidates._UPSERT)
    assert (
        "ON CONFLICT (intent_a_id, intent_b_id,"
        " intent_a_version, intent_b_version)" in sql
    )
    assert "DO UPDATE SET" in sql
    assert "retrieval_score = EXCLUDED.retrieval_score" in sql
    assert "updated_at = EXCLUDED.updated_at" in sql


def test_upsert_do_update_touches_score_only():
    """DO UPDATE SET は retrieval_score/updated_at のみ(statusを壊さない —
    design §2.3。evaluated/skipped/closedへの遷移はws-4以降/stage1の担当)。"""
    sql = str(candidates._UPSERT)
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "status" not in update_clause
    assert "cheap_judge_score" not in update_clause
    # 新規行は status='pending'(05 §2・Layer 1〜2時点でJev未評価)
    insert_part = sql.split("DO UPDATE", 1)[0]
    assert "'pending'" in insert_part


def test_upsert_sql_all_bind_params_recognized():
    compiled = str(candidates._UPSERT.compile(dialect=postgresql.dialect()))
    for key in _UPSERT_KEYS:
        assert f":{key}" not in compiled, key
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_origin.py tests/unit/matching/test_layer_sql.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.matching.candidates'`)

- [ ] **Step 3: candidates.py を実装する**

`backend/src/latch/worker/matching/candidates.py`:

```python
"""match_candidates の生成・記録(05 §2・design §2.3)。

正規化 intent_a_id < intent_b_id(UUID比較はPython側)のうえ、
UNIQUE(a, b, av, bv) を狙った UPSERT を行う。新規行は status='pending'。
DO UPDATE は retrieval_score・updated_at のみ(同一バージョン内の再評価は
既存レコードを更新 — 05 §2)。statusの遷移(evaluated/skipped はws-4以降・
closed はstage1の削除処理)はここでは扱わない。対象側versionはSELECT時点の
i.version をそのまま記録する(評価世代)。時刻はOrigin.evaluated_at
(Clock明示値・design §2.3)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.worker.matching.origin import Origin

_UPSERT = text("""
    INSERT INTO match_candidates (
        intent_a_id, intent_b_id, intent_a_version, intent_b_version,
        retrieval_score, status, created_at, updated_at
    ) VALUES (
        :intent_a_id, :intent_b_id, :intent_a_version, :intent_b_version,
        :retrieval_score, 'pending', :now, :now
    )
    ON CONFLICT (intent_a_id, intent_b_id, intent_a_version, intent_b_version)
    DO UPDATE SET
        retrieval_score = EXCLUDED.retrieval_score,
        updated_at = EXCLUDED.updated_at
""")


@dataclass(frozen=True)
class CandidatePair:
    """記録した1ペア(design §2.4「(a, b, score) 一覧」の要素)。"""

    intent_a_id: uuid.UUID
    intent_b_id: uuid.UUID
    retrieval_score: float


def normalize_pair(
    origin: Origin, candidate_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """正規化 intent_a_id < intent_b_id(design §2.3)。"""
    if origin.intent_id < candidate_id:
        return origin.intent_id, candidate_id
    return candidate_id, origin.intent_id


async def upsert_candidate(
    conn: AsyncConnection,
    *,
    origin: Origin,
    candidate_id: uuid.UUID,
    candidate_version: int,
    similarity: float,
) -> CandidatePair:
    """1ペアのUPSERT(design §2.3)。retrieval_score には cosine類似度。"""
    a_id, b_id = normalize_pair(origin, candidate_id)
    versions = {origin.intent_id: origin.version, candidate_id: candidate_version}
    await conn.execute(
        _UPSERT,
        {
            "intent_a_id": a_id,
            "intent_b_id": b_id,
            "intent_a_version": versions[a_id],
            "intent_b_version": versions[b_id],
            "retrieval_score": similarity,
            "now": origin.evaluated_at,
        },
    )
    return CandidatePair(
        intent_a_id=a_id, intent_b_id=b_id, retrieval_score=similarity
    )
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/ -v`
Expected: PASS(test_origin 13件+test_layer_sql 10件)

- [ ] **Step 5: 全体確認してCommit**

Run: `make lint && make test`
Expected: ともにexit 0

```bash
git add backend/src/latch/worker/matching/candidates.py \
  backend/tests/unit/matching/test_origin.py \
  backend/tests/unit/matching/test_layer_sql.py
git status --short
git commit -m "feat: match_candidatesへのUPSERT(正規化a<b・retrieval_scoreのみ更新)"
```

### Task 4: runner.py と __init__.py 最終形(run_candidate_retrieval)

**Files:**
- Create: `backend/src/latch/worker/matching/runner.py`
- Modify: `backend/src/latch/worker/matching/__init__.py`(Task 1のdocstringのみの状態から公開APIの最終形へ)
- Test: `backend/tests/unit/matching/test_runner.py`

**Interfaces:**
- Consumes: `load_origin`(Task 1)・`retrieve_topk`(Task 2)・`upsert_candidate`(Task 3)・Clock
- Produces(後続のパイプライン組み込み単位・integration試験が使用 — design §2.4の呼び出し契約):
  - `RetrievalOutcome`(dataclass): `intent_id: uuid.UUID` / `version: int | None`(no-op時None)/ `layer1_pass_count: int` / `pairs: list[CandidatePair]` / `skip_reason: str | None`
  - `async run_candidate_retrieval(conn: AsyncConnection, clock: Clock, intent_id: uuid.UUID) -> RetrievalOutcome`(起点検証→Layer 2検索→UPSERT。例外は握らず伝播)
  - `__init__.py` の公開API: `run_candidate_retrieval`・`RetrievalOutcome`・`CandidatePair`・`RetrievedCandidate`・`HardCandidate`・`Origin`・`OriginLoad`・`load_origin`・`hard_filter_candidates`・`retrieve_topk`・`upsert_candidate`・`bind_params`・`K_VECTORS`・`PAIR_BUDGET_MIN_YEN`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_runner.py` を作成:

```python
"""runnerの配線(no-op判定→検索→記録)のunit試験(design §4.1)。

monkeypatchでorigin/layer2/candidatesを差し替え、no-op時に検索・記録が
走らないこととOutcomeの構成を検証する(スタブconn・決定的)。
runnerはモジュール属性経由で関数を呼ぶため差し替え可能(§9固定値)。
"""

import uuid
from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.worker.matching import runner
from latch.worker.matching.candidates import CandidatePair
from latch.worker.matching.layer2 import RetrievedCandidate
from latch.worker.matching.origin import (
    SKIP_EMBEDDING_NULL,
    Origin,
    OriginLoad,
)

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
IID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
TARGET = uuid.UUID("00000000-0000-4000-8000-0000000000c3")


def _origin() -> Origin:
    return Origin(
        intent_id=IID,
        version=1,
        user_id=uuid.UUID("00000000-0000-4000-8000-0000000000b2"),
        category_primary="drinking",
        alcohol_involved=False,
        budget_max=None,
        participants_min=2,
        participants_max=2,
        geo_lon=130.5581,
        geo_lat=31.5965,
        geo_radius_m=1000,
        time_start=NOW,
        time_end=NOW + timedelta(hours=3),
        embedding="[1.0]",
        user_ge_20=True,
        evaluated_at=NOW,
    )


async def test_noop_skips_layer2_and_candidates(monkeypatch):
    """no-op(design §2.5)では検索も記録も呼ばれない。"""
    calls = {"topk": 0, "upsert": 0}

    async def fake_load(conn, clock, iid):
        assert iid == IID
        return OriginLoad(origin=None, skip_reason=SKIP_EMBEDDING_NULL)

    async def fake_topk(conn, org):
        calls["topk"] += 1
        return [], 0

    async def fake_upsert(conn, **kwargs):
        calls["upsert"] += 1
        raise AssertionError("no-opでは呼ばれない")

    monkeypatch.setattr(runner.origin, "load_origin", fake_load)
    monkeypatch.setattr(runner.layer2, "retrieve_topk", fake_topk)
    monkeypatch.setattr(runner.candidates, "upsert_candidate", fake_upsert)

    outcome = await runner.run_candidate_retrieval(None, FakeClock(NOW), IID)
    assert outcome.skip_reason == SKIP_EMBEDDING_NULL
    assert outcome.version is None
    assert outcome.layer1_pass_count == 0
    assert outcome.pairs == []
    assert calls == {"topk": 0, "upsert": 0}


async def test_normal_path_records_pairs(monkeypatch):
    """検索結果の各対象を記録へ回しOutcomeへ載せる(pass_count含む)。"""
    org = _origin()

    async def fake_load(conn, clock, iid):
        return OriginLoad(origin=org, skip_reason=None)

    async def fake_topk(conn, o):
        assert o is org
        return ([RetrievedCandidate(TARGET, 2, 0.98)], 7)

    upserted = {}

    async def fake_upsert(conn, *, origin, candidate_id, candidate_version, similarity):
        upserted["args"] = (candidate_id, candidate_version, similarity)
        a, b = sorted((origin.intent_id, candidate_id))
        return CandidatePair(a, b, similarity)

    monkeypatch.setattr(runner.origin, "load_origin", fake_load)
    monkeypatch.setattr(runner.layer2, "retrieve_topk", fake_topk)
    monkeypatch.setattr(runner.candidates, "upsert_candidate", fake_upsert)

    outcome = await runner.run_candidate_retrieval(None, FakeClock(NOW), IID)
    assert outcome.skip_reason is None
    assert outcome.intent_id == IID
    assert outcome.version == 1
    assert outcome.layer1_pass_count == 7
    assert len(outcome.pairs) == 1
    assert outcome.pairs[0].retrieval_score == 0.98
    assert upserted["args"] == (TARGET, 2, 0.98)


async def test_empty_result_records_nothing(monkeypatch):
    """Layer 1通過0件(検索結果空)でも正常終了(skip扱いにしない)。"""
    org = _origin()

    async def fake_load(conn, clock, iid):
        return OriginLoad(origin=org, skip_reason=None)

    async def fake_topk(conn, o):
        return [], 0

    async def fake_upsert(conn, **kwargs):
        raise AssertionError("0件では呼ばれない")

    monkeypatch.setattr(runner.origin, "load_origin", fake_load)
    monkeypatch.setattr(runner.layer2, "retrieve_topk", fake_topk)
    monkeypatch.setattr(runner.candidates, "upsert_candidate", fake_upsert)

    outcome = await runner.run_candidate_retrieval(None, FakeClock(NOW), IID)
    assert outcome.skip_reason is None
    assert outcome.version == 1
    assert outcome.pairs == [] and outcome.layer1_pass_count == 0
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_runner.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.matching.runner'`)

- [ ] **Step 3: runner.py を実装する**

`backend/src/latch/worker/matching/runner.py`:

```python
"""embedding_completed 起点の第2段前半のオーケストレータ(design §2.4)。

起点検証→Layer 2検索→match_candidates記録。トランザクションは呼び出し側
(engine.begin() で包む — design §2.3)。例外は握らず呼び出し側(stage1の
再試行ループに載る将来経路)へ伝播させる。モジュール属性経由で
origin/layer2/candidates を呼ぶ(unit試験がmonkeypatchで差し替え可能)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncConnection

from latch.core.clock import Clock
from latch.worker.matching import candidates, layer2, origin
from latch.worker.matching.candidates import CandidatePair


@dataclass(frozen=True)
class RetrievalOutcome:
    """run_candidate_retrieval の結果(design §2.4 — 試験assertと
    将来トレースの供給源)。"""

    intent_id: uuid.UUID
    version: int | None  # no-op時はNone
    layer1_pass_count: int
    pairs: list[CandidatePair] = field(default_factory=list)
    skip_reason: str | None = None


async def run_candidate_retrieval(
    conn: AsyncConnection, clock: Clock, intent_id: uuid.UUID
) -> RetrievalOutcome:
    """起点検証→Layer 2検索→UPSERT(design §2.3・§2.4)。"""
    loaded = await origin.load_origin(conn, clock, intent_id)
    if loaded.skip_reason is not None:
        return RetrievalOutcome(
            intent_id=intent_id,
            version=None,
            layer1_pass_count=0,
            skip_reason=loaded.skip_reason,
        )
    assert loaded.origin is not None  # skip_reason Noneならoriginがある
    org = loaded.origin
    rows, pass_count = await layer2.retrieve_topk(conn, org)
    pairs: list[CandidatePair] = []
    for c in rows:
        pairs.append(
            await candidates.upsert_candidate(
                conn,
                origin=org,
                candidate_id=c.intent_id,
                candidate_version=c.version,
                similarity=c.similarity,
            )
        )
    return RetrievalOutcome(
        intent_id=org.intent_id,
        version=org.version,
        layer1_pass_count=pass_count,
        pairs=pairs,
    )
```

- [ ] **Step 4: `__init__.py` を最終形へ更新する**

`backend/src/latch/worker/matching/__init__.py` を差し替え:

```python
"""第2段マッチング前半: Layer 1 Hard Filter + Layer 2 Candidate Retrieval(M2 ws-3)。

公開APIは AsyncConnection を第一引数に取る純関数群(design §2.4)。
stage1(embedding_completed種別)への実配線は後続単位。K_v・500円は
module定数(design §2.8-3 — settings化しない)。
"""

from latch.worker.matching.candidates import (
    CandidatePair,
    normalize_pair,
    upsert_candidate,
)
from latch.worker.matching.layer1 import (
    PAIR_BUDGET_MIN_YEN,
    HardCandidate,
    LAYER1_WHERE,
    hard_filter_candidates,
)
from latch.worker.matching.layer2 import (
    K_VECTORS,
    RetrievedCandidate,
    retrieve_topk,
)
from latch.worker.matching.origin import (
    Origin,
    OriginLoad,
    bind_params,
    load_origin,
)
from latch.worker.matching.runner import RetrievalOutcome, run_candidate_retrieval

__all__ = [
    "PAIR_BUDGET_MIN_YEN",
    "K_VECTORS",
    "LAYER1_WHERE",
    "CandidatePair",
    "HardCandidate",
    "Origin",
    "OriginLoad",
    "RetrievedCandidate",
    "RetrievalOutcome",
    "bind_params",
    "hard_filter_candidates",
    "load_origin",
    "normalize_pair",
    "retrieve_topk",
    "run_candidate_retrieval",
    "upsert_candidate",
]
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/ -v && make lint && make test`
Expected: PASS(test_origin 13件+test_layer_sql 10件+test_runner 3件)+ lint/testともにexit 0

- [ ] **Step 6: Commit**

```bash
git add backend/src/latch/worker/matching/runner.py \
  backend/src/latch/worker/matching/__init__.py \
  backend/tests/unit/matching/test_runner.py
git status --short
git commit -m "feat: run_candidate_retrieval(起点検証→Layer2→UPSERTのオーケストレータ)"
```

### Task 5: integration test_matching_hardfilter.py(02#9・作成のみ・実行しない)

**Files:**
- Create: `backend/tests/integration/test_matching_hardfilter.py`

**Interfaces:**
- Consumes: compose常設api(127.0.0.1:8000)・`db_engine` fixture(tests/integration/conftest.py・参照のみ)・`load_origin`・`hard_filter_candidates`・`run_candidate_retrieval`・`SKIP_PARTICIPANTS`(Task 1〜4)・FakeClock/SystemClock/JST(core/clock)
- Produces: 02#9 Hard Filter 単体試験13件(design §4.2 hardfilter 1〜5+Review Focus 1〜3+design §5-3の誕生日境界ピン)。**実行はスーパーバイザー検証時の `make test-ci` のみ**(§0)。Workerは起動しない(直接関数呼び出し・design §4.2)

**design §5-3(実装時確認)の組み込み**: `test_8_age_boundary_jst` が PostgreSQL の `EXTRACT(YEAR FROM AGE(...))` の誕生日当日・前日の実DB計算を1ケースずつピンする。想定どおり(当日=20歳通過・前日=19歳fail)なら追加対応不要。

- [ ] **Step 1: 試験ファイルを作成する**

`backend/tests/integration/test_matching_hardfilter.py`:

```python
"""02#9 Hard Filter 単体試験(M2 ws-3 design §4.2)。

実DB(compose常設DB・PostGIS/pgvector実物)。Workerは起動しない
(直接関数呼び出し・FakeClock注入)。Embedding実体(ws-2)に依存しない:
APIでactive Intentを作り、embeddingは768次元ベクトルを直接UPDATEで挿入。
teardownはsubjectプレフィックス単位で match_candidates → match_events →
intents → blocks → users の順に削除(design §4.2・FK順)。
19歳×飲酒ペアはAPIの20歳検証(_require_age_20)をバイパスするため
DB直接UPDATEで作出(06 §2「API作成・更新時検証に対する二重防御」の実試験)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import JST, FakeClock, SystemClock
from latch.worker.matching import (
    hard_filter_candidates,
    load_origin,
    run_candidate_retrieval,
)
from latch.worker.matching.origin import SKIP_PARTICIPANTS

pytestmark = pytest.mark.integration

# 緯度1度≈111,320m(南北方向のみの移動で距離を制御。WGS84楕円体でも
# 誤差は数m — 判定マージン(数百m)に対して無視できる)
METERS_PER_DEG_LAT = 111_320.0


# -- 共通ヘルパ(test_events_pipeline.py と同じ流儀) --


async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        provider,
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def _register(api_client, subject: str, birth_date: str):
    idp_token = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "m2ws3", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["user"]["id"]


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0。直交/平行の制御用)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)  # 起点・対照の標準ベクトル(E2=_vec(0.0,1.0) と直交)


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _birth_jst_years_ago(years: int, *, plus_days: int = 0) -> str:
    """JST今日基準で満 years 歳になる誕生日。plus_days=1 は誕生日前日
    (=まだ満 years-1 歳)。うるう日起点は3/2へ外す(PostgreSQLのAGEは
    2/29生まれの繰り上げを暦減算で行いPythonと最大1ヶ月ずれるため、
    境界試験の対象から外す — 本計画§9)。"""
    today = SystemClock().now().astimezone(JST).date()
    try:
        d = today.replace(year=today.year - years)
    except ValueError:  # 2/29生まれ相当
        d = today.replace(year=today.year - years, day=28)
    if (d.month, d.day) in {(2, 28), (2, 29), (3, 1)}:
        d = date(d.year, 3, 2)
    return (d + timedelta(days=plus_days)).isoformat()


def _structured(
    *,
    category: str = "drinking",
    start: str | None = None,
    budget: int | None = None,
    participants: tuple[int, int] | None = None,
    visibility: str | None = None,
    alcohol: bool = False,
) -> dict:
    d: dict = {
        "category": {"primary": category, "secondary": None},
        "alcohol_involved": alcohol,
        "time": {"start": start or _future(3), "end": None},
        "location": {"name": "天文館"},
    }
    if budget is not None:
        d["budget"] = {"max": budget}
    if participants is not None:
        d["participants"] = {"min": participants[0], "max": participants[1]}
    if visibility is not None:
        d["visibility"] = visibility
    return d


def _payload(structured: dict, *, status: str = "active") -> dict:
    return {
        "raw_text": "今夜 天文館で飲みたい",
        "status": status,
        "structured_intent": structured,
    }


async def _create(api_client, headers, payload) -> dict:
    resp = await api_client.post("/v1/intents", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]


async def _set(db_engine, intent_id: str, sql_set: str) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(f"UPDATE intents SET {sql_set} WHERE id = CAST(:iid AS uuid)"),
            {"iid": intent_id},
        )


async def _intent(
    api_client, db_engine, headers, payload, *, vec: str | None = E1
) -> dict:
    """active Intentを作りembeddingを直接挿入(design §4.2のfixture方針)。"""
    intent = await _create(api_client, headers, payload)
    if vec is not None:
        await _set(
            db_engine,
            intent["id"],
            "embedding = CAST('" + vec + "' AS vector), embedding_model = 'fixture'",
        )
    return intent


async def _geo(db_engine, intent_id: str) -> tuple[float, float]:
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT ST_X(geo_center::geometry), ST_Y(geo_center::geometry)"
                    " FROM intents WHERE id = CAST(:iid AS uuid)"
                ),
                {"iid": intent_id},
            )
        ).first()
    assert row is not None
    return float(row[0]), float(row[1])


async def _hard_ids(db_engine, clock, origin_intent_id: str) -> set[str]:
    """Layer 1 通過集合のintent_id一覧(02#9の判定面)。"""
    async with db_engine.connect() as conn:
        loaded = await load_origin(conn, clock, uuid_mod.UUID(origin_intent_id))
        assert loaded.origin is not None, loaded.skip_reason
        rows = await hard_filter_candidates(conn, loaded.origin)
    return {str(c.intent_id) for c in rows}


async def _run(db_engine, clock, origin_intent_id: str):
    async with db_engine.begin() as conn:
        return await run_candidate_retrieval(
            conn, clock, uuid_mod.UUID(origin_intent_id)
        )


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"m2ws3-{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_events WHERE source_intent_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM blocks WHERE blocker_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                " OR blocked_id IN (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)


async def _user(api_client, prefix: str, birth_date: str = "1990-04-01"):
    subject = f"{prefix}{uuid_mod.uuid4().hex[:8]}"
    return await _register(api_client, subject, birth_date)


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


# -- §4.2 試験1〜5 + Review Focus + design §5-3 --


async def test_1_budget_conditions(api_client, db_engine, field):
    """02#9予算系統: ペア予算499 fail(片方・双方)・NULL系/500はfailしない。"""
    clock = _clock()
    h_on, _ = await _user(api_client, field)
    o_null = await _intent(api_client, db_engine, h_on, _payload(_structured()))
    h_o499, _ = await _user(api_client, field)
    o_499 = await _intent(
        api_client, db_engine, h_o499, _payload(_structured(budget=499))
    )
    h1, _ = await _user(api_client, field)
    t_null = await _intent(api_client, db_engine, h1, _payload(_structured()))
    h2, _ = await _user(api_client, field)
    t_499 = await _intent(
        api_client, db_engine, h2, _payload(_structured(budget=499))
    )
    h3, _ = await _user(api_client, field)
    t_500 = await _intent(
        api_client, db_engine, h3, _payload(_structured(budget=500))
    )

    ids = await _hard_ids(db_engine, clock, o_null["id"])
    assert t_null["id"] in ids  # NULL×NULL → 制約なし → failしない(06 §2)
    assert t_499["id"] not in ids  # NULL×499 = ペア予算499 → fail
    assert t_500["id"] in ids  # NULL×500 → 500未満でない → pass(境界)

    # 起点499はどの対象とも LEAST(499, x) <= 499 になる(500ちょうどの
    # 境界passは o_null 側の検証で担保済み — Review Focus 3)
    ids499 = await _hard_ids(db_engine, clock, o_499["id"])
    assert t_null["id"] not in ids499  # 499×NULL = 499 → fail(片方499の逆側)
    assert t_499["id"] not in ids499  # 499×499 = 499 → fail(双方499)
    assert t_500["id"] not in ids499  # 499×500 = LEAST(499,500) = 499 → fail
```

続けて試験2以降(同じファイルへ追記):

```python
async def test_2_time_intersection(api_client, db_engine, field):
    """02#9時間系統: 半開区間 [s,e) の交差なし・境界接触(e_a=s_b)はfail。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    origin_end = datetime.fromisoformat(o["structured_intent"]["time"]["end"])
    h1, _ = await _user(api_client, field)
    t_pass = await _intent(
        api_client, db_engine, h1, _payload(_structured(start=_future(4)))
    )
    h2, _ = await _user(api_client, field)
    t_touch = await _intent(
        api_client,
        db_engine,
        h2,
        _payload(_structured(start=origin_end.isoformat())),  # e_a = s_b
    )
    h3, _ = await _user(api_client, field)
    t_no = await _intent(
        api_client, db_engine, h3, _payload(_structured(start=_future(7)))
    )

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_pass["id"] in ids  # [3,6) と [4,7) は交差
    assert t_touch["id"] not in ids  # [6,9) と [3,6) の境界接触=交差なし
    assert t_no["id"] not in ids  # [7,10) は交差なし


async def test_3_distance_radius_sum(api_client, db_engine, field):
    """02#9距離系統: r_a + r_b の外れはfail・和の内側(0.9×和)は通過。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    lon, lat = await _geo(db_engine, o["id"])
    # 既定 r_a = r_b = 1000m(completion.DEFAULT_RADIUS_M)→ 和 = 2000m
    h1, _ = await _user(api_client, field)
    t_in = await _intent(api_client, db_engine, h1, _payload(_structured()))
    await _set(
        db_engine,
        t_in["id"],
        "geo_center = ST_SetSRID(ST_MakePoint("
        f"CAST({lon!r} AS float8),"
        f" CAST({lat + 1800 / METERS_PER_DEG_LAT!r} AS float8)"
        "), 4326)::geography",  # 1.8km < 2km → 通過
    )
    h2, _ = await _user(api_client, field)
    t_out = await _intent(api_client, db_engine, h2, _payload(_structured()))
    await _set(
        db_engine,
        t_out["id"],
        "geo_center = ST_SetSRID(ST_MakePoint("
        f"CAST({lon!r} AS float8),"
        f" CAST({lat + 3000 / METERS_PER_DEG_LAT!r} AS float8)"
        "), 4326)::geography",  # 3km > 2km → fail
    )

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_in["id"] in ids
    assert t_out["id"] not in ids


async def test_4_participants(api_client, db_engine, field):
    """02#9人数系統: 対象 max=1 はfail・起点 min=3 はno-op(§2.5)。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_p1 = await _intent(
        api_client, db_engine, h1, _payload(_structured(participants=(1, 1)))
    )
    h2, _ = await _user(api_client, field)
    t_pass = await _intent(api_client, db_engine, h2, _payload(_structured()))
    h3, _ = await _user(api_client, field)
    o_p3 = await _intent(
        api_client, db_engine, h3, _payload(_structured(participants=(3, 4)))
    )

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_p1["id"] not in ids  # 2 ∉ [1,1] → fail
    assert t_pass["id"] in ids  # 2 ∈ [2,2](既定) → 通過
    # 起点側 min=3 → 検索前にno-op(design §2.5)
    async with db_engine.connect() as conn:
        loaded = await load_origin(conn, clock, uuid_mod.UUID(o_p3["id"]))
    assert loaded.origin is None and loaded.skip_reason == SKIP_PARTICIPANTS


async def test_5_blocks_both_directions(api_client, db_engine, field):
    """02#9ブロック系統: (A,B)(B,A)いずれの向きもfail(06 §2)。"""
    clock = _clock()
    h_o, o_user = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h_o, _payload(_structured()))
    h1, ta_user = await _user(api_client, field)
    t_a = await _intent(api_client, db_engine, h1, _payload(_structured()))
    h2, tb_user = await _user(api_client, field)
    t_b = await _intent(api_client, db_engine, h2, _payload(_structured()))
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO blocks (blocker_id, blocked_id, created_at)"
                " VALUES (CAST(:b AS uuid), CAST(:d AS uuid), :now)"
            ),
            {"b": o_user, "d": ta_user, "now": SystemClock().now()},  # O→A
        )
        await conn.execute(
            text(
                "INSERT INTO blocks (blocker_id, blocked_id, created_at)"
                " VALUES (CAST(:b AS uuid), CAST(:d AS uuid), :now)"
            ),
            {"b": tb_user, "d": o_user, "now": SystemClock().now()},  # B→O
        )

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_a["id"] not in ids  # 起点が対象をブロック
    assert t_b["id"] not in ids  # 対象が起点をブロック


async def test_6_category_mismatch(api_client, db_engine, field):
    """02#9カテゴリ系統: category_primary完全一致のみ(secondaryは対象外)。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_cat = await _intent(
        api_client, db_engine, h1, _payload(_structured(category="activity"))
    )
    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_cat["id"] not in ids


async def test_7_alcohol_underage(api_client, db_engine, field):
    """02#9飲酒年齢系統: 19歳×20歳の飲酒ペアは双方向きともfail。

    19歳の alcohol_involved=true はAPIの20歳検証(_require_age_20)を
    通らないためDB直接UPDATEで強制する(06 §2の二重防御の実試験)。
    """
    clock = _clock()
    h20, _ = await _user(api_client, field)
    o20 = await _intent(
        api_client, db_engine, h20, _payload(_structured(alcohol=True))
    )
    h1, _ = await _user(api_client, field)
    t20 = await _intent(
        api_client, db_engine, h1, _payload(_structured(alcohol=True))
    )
    h2, _ = await _user(api_client, field, _birth_jst_years_ago(19))
    o19 = await _intent(api_client, db_engine, h2, _payload(_structured()))
    await _set(db_engine, o19["id"], "alcohol_involved = true")  # API検証バイパス
    h3, _ = await _user(api_client, field, _birth_jst_years_ago(19))
    t19 = await _intent(api_client, db_engine, h3, _payload(_structured()))
    await _set(db_engine, t19["id"], "alcohol_involved = true")

    ids = await _hard_ids(db_engine, clock, o20["id"])
    assert t20["id"] in ids  # 20歳×20歳の飲酒ペアは通過
    assert t19["id"] not in ids  # 対象19歳 → fail
    ids19 = await _hard_ids(db_engine, clock, o19["id"])
    assert t20["id"] not in ids19  # 起点19歳 → fail(起点側の20歳不成立)


async def test_8_age_boundary_jst(api_client, db_engine, field):
    """design §5-3: PostgreSQL AGE の満年齢ピン(誕生日当日=20歳・前日=19歳)。"""
    clock = _clock()
    h20, _ = await _user(api_client, field)
    o20 = await _intent(
        api_client, db_engine, h20, _payload(_structured(alcohol=True))
    )
    # 今日が20歳の誕生日(JST暦日)→ 当日=満20歳 → 通過
    hb, _ = await _user(api_client, field, _birth_jst_years_ago(20))
    t_bday = await _intent(api_client, db_engine, hb, _payload(_structured()))
    await _set(db_engine, t_bday["id"], "alcohol_involved = true")
    # 誕生日は明日(=19歳)→ fail
    hc, _ = await _user(api_client, field, _birth_jst_years_ago(20, plus_days=1))
    t_eve = await _intent(api_client, db_engine, hc, _payload(_structured()))
    await _set(db_engine, t_eve["id"], "alcohol_involved = true")

    ids = await _hard_ids(db_engine, clock, o20["id"])
    assert t_bday["id"] in ids  # EXTRACT(YEAR FROM AGE(...)) = 20
    assert t_eve["id"] not in ids  # = 19


async def test_9_visibility_not_filtered(api_client, db_engine, field):
    """06 §2: visibilityはLayer 1の判定対象外(2値とも通過)。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_hidden = await _intent(
        api_client, db_engine, h1, _payload(_structured(visibility="hidden_until_match"))
    )
    h2, _ = await _user(api_client, field)
    t_summary = await _intent(
        api_client, db_engine, h2, _payload(_structured(visibility="summary_only"))
    )
    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_hidden["id"] in ids and t_summary["id"] in ids


async def test_10_self_exclusion(api_client, db_engine, field):
    """FR-17(引用#12): 同一ユーザーの2 Intent(同一時間帯・地域・カテゴリ・
    embedding同一)がいずれの処理でも候補にならない(hardfilter・run両面)。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    own2 = await _intent(api_client, db_engine, h, _payload(_structured()))

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert own2["id"] not in ids  # 自己除外
    outcome = await _run(db_engine, clock, o["id"])
    assert outcome.pairs == []  # 対象が自己のみ → 1行も生成されない


async def test_11_defense_coalesce_targets_pass(api_client, db_engine, field):
    """Review Focus 1・2: 対象 time_end NULL / geo_radius_m NULL の防御COALESCE。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_noend = await _intent(
        api_client, db_engine, h1, _payload(_structured(start=_future(4)))
    )
    await _set(db_engine, t_noend["id"], "time_end = NULL")
    h2, _ = await _user(api_client, field)
    t_norad = await _intent(api_client, db_engine, h2, _payload(_structured()))
    await _set(db_engine, t_norad["id"], "geo_radius_m = NULL")

    ids = await _hard_ids(db_engine, clock, o["id"])
    # COALESCE(time_end, start+3h)=f(7) と起点 [3,6) が交差 → 通過
    assert t_noend["id"] in ids
    # COALESCE(geo_radius_m, 1000) で距離和2000m・同じ地点 → 通過
    assert t_norad["id"] in ids


async def test_12_draft_target_excluded(api_client, db_engine, field):
    """06 §3: draft対象は他条件が揃っていても対象外(status='active'条件)。

    draft行はジオコーディングが走らないためgeo_centerのみ直接UPDATEで
    他条件を成立させる(時間はresolve_for_draftがstructuredから保存)。
    それでも除外されればstatusによる除外の実証になる。
    """
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_draft = await _intent(
        api_client, db_engine, h1, _payload(_structured(), status="draft")
    )
    lon, lat = await _geo(db_engine, o["id"])
    await _set(
        db_engine,
        t_draft["id"],
        "geo_center = ST_SetSRID(ST_MakePoint("
        f"CAST({lon!r} AS float8), CAST({lat!r} AS float8)), 4326)::geography",
    )
    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_draft["id"] not in ids


async def test_13_run_retrieval_excludes_fail_pairs(api_client, db_engine, field):
    """02#9受け入れ形: run_candidate_retrieval 経由でもfail対象は
    match_candidates に生成されない(pass対照のみ1行・status='pending')。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    own2 = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_pass = await _intent(api_client, db_engine, h1, _payload(_structured()))
    h2, _ = await _user(api_client, field)
    t_cat = await _intent(
        api_client, db_engine, h2, _payload(_structured(category="activity"))
    )
    h3, _ = await _user(api_client, field)
    t_499 = await _intent(
        api_client, db_engine, h3, _payload(_structured(budget=499))
    )

    outcome = await _run(db_engine, clock, o["id"])
    assert outcome.skip_reason is None and outcome.version == 1
    assert outcome.layer1_pass_count == 1  # pass対照のみ
    assert len(outcome.pairs) == 1
    pair = outcome.pairs[0]
    assert str(min(uuid_mod.UUID(o["id"]), uuid_mod.UUID(t_pass["id"]))) == str(
        pair.intent_a_id
    )  # 正規化 a<b
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT intent_a_id, intent_b_id, status, retrieval_score"
                    " FROM match_candidates"
                    " WHERE intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid)"
                ),
                {"o": o["id"]},
            )
        ).all()
    assert len(rows) == 1
    assert rows[0][2] == "pending"  # Layer 1〜2時点(05 §2)
    assert float(rows[0][3]) == pytest.approx(1.0)  # E1×E1 = cosine 1
```

**実装上の注意**:
- `_set` は f-string でSQL断片を組み立てるが、値はテストコード内のリテラル(lon/lat の float repr)のみでユーザー入力は含まない(テストコードのため許容。製品コードではbind param のみ)
- `_intent` の embedding 直接UPDATEは `CAST('...' AS vector)` をSQLへ埋める(ベクトル文字列はテスト生成リテラル)
- pytest.approx を使う箇所の `import pytest` はファイル冒頭にある

- [ ] **Step 2: 収集確認する(実行はしない)**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_matching_hardfilter.py -q`
Expected: `13 tests collected`・exit 0

- [ ] **Step 3: 全体の構文・import検証とCommit**

Run: `make lint && make test`
Expected: ともにexit 0(make test はintegrationファイルの収集(import)まで行うため構文・importが検証される)

```bash
git add backend/tests/integration/test_matching_hardfilter.py
git status --short
git commit -m "test: 02#9 Hard Filter単体試験13件(作成のみ・実行はスーパーバイザー)"
```

### Task 6: integration test_matching_retrieval.py(02#10・K_v・冪等・作成のみ)

**Files:**
- Create: `backend/tests/integration/test_matching_retrieval.py`

**Interfaces:**
- Consumes: compose常設api・`db_engine` fixture・`run_candidate_retrieval`・`load_origin`(design §5-2の実確認)・FakeClock/SystemClock
- Produces: 02#10・K_v切り詰め決定性・対象外・冪等の4試験(design §4.2 retrieval 1〜4)。**実行はスーパーバイザー検証時のみ**(§0)

**design §5-2(実装時確認)の組み込み**: `test_1` の冒頭で `load_origin` が読んだ起点 embedding が `str` であることを assert し、結果を報告書へ記録する。

- [ ] **Step 1: 試験ファイルを作成する**

`backend/tests/integration/test_matching_retrieval.py`:

```python
"""02#10・K_v切り詰め決定性・対象外・冪等(M2 ws-3 design §4.2)。

Worker不使用の直接関数呼び出し・FakeClock注入。ベクトルは基底+摂動で
cosine類似度を制御する(高類似=同一ベクトル・低類似=直交ベクトル)。
55対象の配置は1ユーザー1Intent(レート制限はuser_id/subject単位のため
60req/分の上限には触らない — limiter.py §2.4)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.worker.matching import load_origin, run_candidate_retrieval

pytestmark = pytest.mark.integration


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)
E2 = _vec(0.0, 1.0)  # E1と直交(cosine 0)


async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        provider,
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


def _structured() -> dict:
    now = SystemClock().now()
    return {
        "category": {"primary": "meal", "secondary": None},
        "alcohol_involved": False,
        "time": {"start": (now + timedelta(hours=3)).isoformat(), "end": None},
        "location": {"name": "天文館"},
    }


def _payload(raw_text: str) -> dict:
    return {
        "raw_text": raw_text,
        "status": "active",
        "structured_intent": _structured(),
    }


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"m2ws3r-{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_events WHERE source_intent_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)


async def _user(api_client, prefix: str):
    """1ユーザーを登録してAuthorizationヘッダーを返す。"""
    subject = f"{prefix}{uuid_mod.uuid4().hex[:8]}"
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "m2ws3r", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


async def _intent(api_client, db_engine, headers, raw_text: str, vec: str) -> dict:
    """1Intent作成してembeddingを直接挿入する。"""
    resp = await api_client.post(
        "/v1/intents", headers=headers, json=_payload(raw_text)
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    await _set_embedding(db_engine, intent["id"], vec)
    return intent


async def _set_embedding(db_engine, intent_id: str, vec: str | None) -> None:
    sql = (
        "embedding = CAST('" + vec + "' AS vector), embedding_model = 'fixture'"
        if vec is not None
        else "embedding = NULL"
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            text(f"UPDATE intents SET {sql} WHERE id = CAST(:iid AS uuid)"),
            {"iid": intent_id},
        )


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


def _peer_ids(outcome) -> list[str]:
    """Outcomeのpairsから起点以外の側(候補対象)のidを行順に取り出す。"""
    out = []
    for p in outcome.pairs:
        a, b = str(p.intent_a_id), str(p.intent_b_id)
        out.append(b if str(outcome.intent_id) == a else a)
    return out


async def _run(db_engine, clock, origin_intent_id: str):
    async with db_engine.begin() as conn:
        return await run_candidate_retrieval(
            conn, clock, uuid_mod.UUID(origin_intent_id)
        )


# -- §4.2 試験1〜4 --


async def test_1_semantic_pair_generated(api_client, db_engine, field):
    """02#10: 語彙不一致の意味的近接ペア(焼肉/肉系なら何でも)が生成される
    (status='pending'・score記録・正規化a<b・version組記録)。"""
    clock = _clock()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, "焼肉が食べたい", E1)
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, "肉系なら何でもいい", E1)

    # design §5-2: asyncpg の vector 列読み取り型の実確認
    async with db_engine.connect() as conn:
        loaded = await load_origin(conn, clock, uuid_mod.UUID(a["id"]))
    assert loaded.origin is not None
    assert isinstance(loaded.origin.embedding, str)  # 文字列で読める(想定どおり)

    outcome = await _run(db_engine, clock, a["id"])
    assert outcome.skip_reason is None
    assert outcome.layer1_pass_count == 1
    assert len(outcome.pairs) == 1
    pair = outcome.pairs[0]
    ai, bi = uuid_mod.UUID(a["id"]), uuid_mod.UUID(b["id"])
    assert (pair.intent_a_id, pair.intent_b_id) == (min(ai, bi), max(ai, bi))
    assert pair.retrieval_score == pytest.approx(1.0)  # cosine類似度記録

    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT intent_a_version, intent_b_version, status"
                    " FROM match_candidates WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                ),
                {"a": pair.intent_a_id, "b": pair.intent_b_id},
            )
        ).first()
    assert row is not None
    assert row[0] == 1 and row[1] == 1  # 評価時点のversion組(05 §2)
    assert row[2] == "pending"
```

続けて試験2〜4(同じファイルへ。`soft` は raw_text の語彙差だけで足りるため structured への soft_constraints 指定は省略してよい):

```python
async def test_2_kv_truncation_deterministic(api_client, db_engine, field):
    """K_v=50切り詰めの決定性(10 §4.6): 同点はintent_id昇順・同一入力2回で
    同一結果・embedding NULL対象は除外され件数不変・非同点は距離上位。"""
    clock = _clock()
    ho = await _user(api_client, field)
    origin = await _intent(api_client, db_engine, ho, "金曜の夜 飲みたい", E1)

    creation_order: list[str] = []  # 対象55件(作成順)
    for _ in range(55):
        h = await _user(api_client, field)
        t = await _intent(api_client, db_engine, h, "一緒に飲みましょう", E1)
        creation_order.append(t["id"])

    # フェーズA: 全員同一点(cosine 1.0完全同点)→ UUID昇順先頭50件
    outcome_a = await _run(db_engine, clock, origin["id"])
    assert outcome_a.layer1_pass_count == 55
    assert len(outcome_a.pairs) == 50
    uuid_sorted = sorted(creation_order)
    assert _peer_ids(outcome_a) == uuid_sorted[:50]  # 同点=UUID昇順(D-24)

    # フェーズB: 1件を embedding NULL へ → 除外されて件数不変(design §4.2-3)
    nulled = creation_order[-1]
    await _set_embedding(db_engine, nulled, None)
    valid = [i for i in creation_order if i != nulled]
    outcome_b = await _run(db_engine, clock, origin["id"])
    assert outcome_b.layer1_pass_count == 54
    assert len(outcome_b.pairs) == 50  # 件数不変
    assert _peer_ids(outcome_b) == sorted(valid)[:50]

    # フェーズC: 同一入力2回実行で同一結果(10 §4.6)
    outcome_c = await _run(db_engine, clock, origin["id"])
    assert _peer_ids(outcome_c) == _peer_ids(outcome_b)
    assert outcome_c.pairs == outcome_b.pairs  # 順序・score含む完全一致

    # フェーズD: 類似度差つき(creation順に eps=(i+1)*0.01)→ 距離上位50件
    for i, iid in enumerate(valid):
        await _set_embedding(db_engine, iid, _vec(1.0, (i + 1) * 0.01))
    outcome_d = await _run(db_engine, clock, origin["id"])
    assert len(outcome_d.pairs) == 50
    assert _peer_ids(outcome_d) == valid[:50]  # eps昇順=類似度降順の先頭50
    # 完全同点の崩れ: フェーズDの選択はUUID順と無相関(creation順で決まる)
    assert set(_peer_ids(outcome_d)) != set(_peer_ids(outcome_b))


async def test_3_origin_noop_cases(api_client, db_engine, field):
    """起点 draft / embedding NULL は no-op(match_candidates に1行も増えない)。"""
    clock = _clock()
    ho = await _user(api_client, field)
    origin = await _intent(api_client, db_engine, ho, "起点", E1)
    # 対照: 1件だけ候補になる通常起点
    h1 = await _user(api_client, field)
    await _intent(api_client, db_engine, h1, "対象", E1)
    outcome = await _run(db_engine, clock, origin["id"])
    assert len(outcome.pairs) == 1

    # draft起点(structured込みで作成しembeddingも入れる → statusによる除外)
    h2 = await _user(api_client, field)
    resp = await api_client.post(
        "/v1/intents",
        headers=h2,
        json={
            "raw_text": "下書き",
            "status": "draft",
            "structured_intent": _structured(),
        },
    )
    assert resp.status_code == 201, resp.text
    draft = resp.json()["intent"]
    await _set_embedding(db_engine, draft["id"], E1)
    out_draft = await _run(db_engine, clock, draft["id"])
    assert out_draft.skip_reason == "origin_not_active"
    assert out_draft.pairs == []

    # embedding NULL 起点(active・embedding未投入)
    h3 = await _user(api_client, field)
    resp3 = await api_client.post(
        "/v1/intents", headers=h3, json=_payload("まだ埋め込みなし")
    )
    assert resp3.status_code == 201, resp3.text
    noemb = resp3.json()["intent"]
    out_null = await _run(db_engine, clock, noemb["id"])
    assert out_null.skip_reason == "origin_embedding_null"
    assert out_null.pairs == []

    async with db_engine.connect() as conn:
        cnt = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM match_candidates"
                    " WHERE intent_a_id = CAST(:d AS uuid)"
                    " OR intent_b_id = CAST(:d AS uuid)"
                    " OR intent_a_id = CAST(:n AS uuid)"
                    " OR intent_b_id = CAST(:n AS uuid)"
                ),
                {"d": draft["id"], "n": noemb["id"]},
            )
        ).scalar()
    assert cnt == 0  # no-op起点のペアは1行も増えない


async def test_4_idempotent_upsert(api_client, db_engine, field):
    """冪等性(10 §4.7): 同一 (a,b,av,bv) 2回実行で1行・scoreはDO UPDATEで
    更新される・status='pending' は維持される(Review Focus 4)。"""
    clock = _clock()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, "起点", E1)
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, "対象", E1)

    await _run(db_engine, clock, a["id"])  # 1回目(score ≈ 1.0)
    # 対象のembeddingを直交ベクトルへ → 2回目のscoreは ≈ 0.0 に変わる
    await _set_embedding(db_engine, b["id"], E2)
    await _run(db_engine, clock, a["id"])  # 2回目

    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT retrieval_score, status, created_at, updated_at"
                    " FROM match_candidates"
                    " WHERE (intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid))"
                    " OR (intent_a_id = CAST(:b AS uuid)"
                    " AND intent_b_id = CAST(:a AS uuid))"
                ),
                {"a": a["id"], "b": b["id"]},
            )
        ).all()
    assert len(rows) == 1  # 二重生成なし(UNIQUE+UPSERT — 10 §4.7)
    score, status, created_at, updated_at = rows[0]
    assert float(score) == pytest.approx(0.0, abs=1e-6)  # 更新されている
    assert status == "pending"  # DO UPDATEはstatusを壊さない
    assert created_at < updated_at  # 2回目でupdated_atが進んだ
```

**実装上の注意**:
- `test_2` の55ユーザー作成は直列でおよそ1〜2分かかる(test-ci全体の実行時間に含まれる)。レート制限(user_id単位60req/分・作成20件/日)には各ユーザー3リクエストのため触らない
- フェーズDの `!=` assert は自然に成立する(UUID順とcreation順は無相関)。万一同集合になった場合(確率は天文学的にゼロ)は失敗として扱う(再試行で自然に解消する)

- [ ] **Step 2: 収集確認する(実行はしない)**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_matching_retrieval.py -q`
Expected: `4 tests collected`・exit 0

- [ ] **Step 3: 全体の構文・import検証とCommit**

Run: `make lint && make test`
Expected: ともにexit 0

```bash
git add backend/tests/integration/test_matching_retrieval.py
git status --short
git commit -m "test: 02#10・K_v決定性・冪等の4試験(作成のみ・実行はスーパーバイザー)"
```

### Task 7: 報告ファイル・完了条件検証

**Files:**
- Create: `docs/plans/M2/ws-3-report.md`

**Interfaces:**
- Consumes: 本計画§6の検証コマンド・§7の報告形式
- Produces: 報告ファイル(スーパーバイザーが検証・マージ時に使用)

- [ ] **Step 1: 完了条件§6の検証をすべて実行し結果を控える**

```bash
make lint && make test
cd backend && uv run pytest --collect-only tests/integration/test_matching_hardfilter.py tests/integration/test_matching_retrieval.py -q
cd .. && rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
cd .. && git diff --stat main -- backend/alembic docs
git diff --name-only main | sort
git status --short
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
```

§6の各条件の期待値と突き合わせる(差異があれば理由を報告書に書く)。

- [ ] **Step 2: 報告ファイルを§7の形式で作成する**

`docs/plans/M2/ws-3-report.md` を§7のテンプレートどおりに作成し、Step 1の証拠を貼る。design §5の実装時確認事項2件(asyncpg vector読み取り・AGEの満年齢ピン)の結果を必ず記載する。

- [ ] **Step 3: 全体を検証してコミットする**

```bash
make lint && make test
git add docs/plans/M2/ws-3-report.md
git commit -m "docs: M2 ws-3の実行報告(完了条件7項目の証拠・test-ciは検証待ち)"
git status --short  # 空であること
git diff --name-only main | sort  # §4の12ファイルと完全一致(完了条件5)
```

- [ ] **Step 4: 最終返信**

報告ファイルのパスと完了条件7項目の結果一覧(項目2の実行部分は「test-ci=スーパーバイザー検証待ち」)を返す。

---

## 9. 固定値(IF確定事項。design.md §2の採用判断+本計画が確定した実装詳細)

design §2 の採用判断(変更なし前提):

1. **Layer 1/2 は条件を共有する単一SQL**(design §2.1案A): WHERE共通ビルダー=layer1.py の `LAYER1_WHERE` 確定文字列。案B(2クエリ分割)・案C(HNSW-first)不採用
2. **決定性優先(逐次ソート)**(design §2.2): `ORDER BY 距離 ASC, i.id ASC LIMIT 50`。HNSW Indexスキャン不使用(最適化は負荷試験結果待ち — design §5-1)
3. **UPSERT は DO UPDATE(retrieval_score・updated_atのみ)**(design §2.3): status='pending' は挿入時のみ
4. **例外は握らず伝播**(design §2.4): モジュール内にtry/except・ロギングなし

本計画が確定した実装詳細(design 未規定部分の実装解釈。変更時は報告書へ記録):

5. **`Origin.evaluated_at`**: load_origin 時点の `clock.now()` を Origin へ保持。LAYER1_WHERE の `:now`・UPSERT の `created_at/updated_at` に使う(design §3.1 のシグネチャ `(conn, origin)` を維持しつつ評価時刻を運ぶ)
6. **layer1_pass_count は `COUNT(*) OVER ()`**(同一SQL内・window関数はLIMIT前に評価): design §2.4「Layer 1 通過件数」の取得手段。design §2.1 の「中間表現を持たない」方針を崩さない
7. **`:origin_budget` は `CAST(:origin_budget AS int)`**: design §2.6表は裸だが、NULLを渡すパラメータは型推論に任せられない(asyncpgがunknown型NULLでエラーになりうる)。§2のCAST規約どおり
8. **飲酒年齢の20歳計算はJST暦日基準に統一**(design §2.6表からの一変更点): SQLは `EXTRACT(YEAR FROM AGE((CAST(:now AS timestamptz) AT TIME ZONE 'Asia/Tokyo'), CAST(u.birth_date AS timestamp))) >= 20`・Python側は既存 `users.service.age_years(birth, clock.jst_date())` を再利用。design §2.5「20歳の基準時点は Clock.now() の満年齢(基準タイムゾーン未規定)」を、API作成・更新時検証(`_require_age_20`・age_years=JST暦日)と同じ基準へ確定した(06 §2「API作成・更新時検証に対する二重防御」を同じ物差しで満たす)。`:now` には tz-aware UTC の `evaluated_at` をそのまま渡す
9. **PostgreSQL `AGE` とうるう日**: 2/29生まれは PostgreSQL が暦減算で最大1ヶ月Python(`age_years`の3/1加算)とずれる(どちらも20歳未満側に倒れるため安全側)。integration の誕生日境界ピン(test_8)はうるう日起点を3/2へ外して決定的に保つ
10. **関数シグネチャ一式**: `load_origin(conn, clock, intent_id) -> OriginLoad` / `hard_filter_candidates(conn, origin) -> list[HardCandidate]` / `retrieve_topk(conn, origin) -> tuple[list[RetrievedCandidate], int]` / `upsert_candidate(conn, *, origin, candidate_id, candidate_version, similarity) -> CandidatePair` / `normalize_pair(origin, candidate_id) -> tuple[UUID, UUID]` / `run_candidate_retrieval(conn, clock, intent_id) -> RetrievalOutcome` / `bind_params(origin) -> dict`
11. **runner はモジュール属性経由で origin/layer2/candidates を呼ぶ**(`from latch.worker.matching import candidates, layer2, origin` + `await origin.load_origin(...)`): unit試験がmonkeypatchで差し替え可能にするため(test_runner.py の前提)
12. **定数の所在**: `K_VECTORS=50` は layer2.py・`PAIR_BUDGET_MIN_YEN=500` は layer1.py に定義し `__init__.py` が再export(settings.py 不触 — design §2.8-3)
13. **UUID順の正規化はPythonの uuid.UUID 比較**(int比較=PostgreSQLのuuidバイト順と一致): design §2.3「UUID比較はPython側」
14. **retrieval_score は cosine類似度(1−距離)**、ORDER BY は距離昇順(design §2.6どおり・同義)

---

## Self-Review の記録(計画書作成時点の確認)

1. **Specカバレッジ**: design §1.2確定値17項目→§1参照仕様表へ反映。§2.1(案A→Task 2)・§2.2(逐次ソート→Task 2のlayer2)・§2.3(UPSERT→Task 3)・§2.4(契約→Task 4)・§2.5(no-op→Task 1)・§2.6(確定表→Task 2のLAYER1_WHERE)・§2.7(マイグレーションなし→§5禁止)・§2.8(YAGNI→§5禁止)・§3.1/3.2(ファイル一覧→§4)・§3.3(禁止→§5)・§3.4(競合回避→§0)・§4.1(unit3ファイル→Task 1〜4)・§4.2(integration 2ファイル→Task 5・6)・§4.3(検証手順→§6・Task 7)・§5-2(asyncpg読み取り→Task 6のtest_1)・§5-3(AGE境界ピン→Task 5のtest_8)。design §1.4のスコープ外7項目は§5禁止へ反映
2. **プレースホルダ**: TBD/TODO/「後で決める」記述なし。コードは最終形のみを掲載(執筆中の草案は一本化済み)
3. **型一貫性**: `Origin`(Task 1定義・evaluated_at含む)をTask 2〜6が同型で使用。`RetrievedCandidate`(Task 2)をTask 4・6が使用。`CandidatePair`(Task 3)をTask 4・6が使用。`RetrievalOutcome`(Task 4)をTask 5・6が使用。`bind_params` のキー一式は test_layer_sql の `_WHERE_KEYS` と一致
4. **Review Focus**: §3の5項目が所有タスクの試験に割り当て済み(各項目末尾に明記)
5. **integration試験数の整合**: hardfilter 13件+retrieval 4件=17件(§6完了条件2・§7報告形式の記載と一致)
