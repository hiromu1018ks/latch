# M2 ws-7(グループマッチ Group Search)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** グループマッチの生成〜提案化まで5部品を実装する。GroupEngine(候補Pool構築〔人数緩和検索・同一30分Bucket・cheap_score降順上位15〕・貪欲法〔種=起点・Hard互換追加・3〜4人・user_id相異〕・group_candidates記録+全ペアmatch_candidates生成)、K_j=8配分拡張(1対1最低4回保証+残り最大4回をグループ構成ペアへ・継続優先→新規)、GroupEngine.finalize(集約 aggregate_score = H × min over ペア(MutualScore) × C・閾値0.80・latches生成〔group_candidate_idつき〕・通知順序D-06上位1集合)、既存部品の共存改修(LatchEngine・layer4のグループペア除外・try_promoteの|S|人対応・stage1削除処理へgroup_candidates無効化)、1対1側I-1改修(_evaluate_pairのtx統合)、マイグレーション0005(group_candidates開いている行の部分UNIQUE索引)。

**Architecture:** 実行位置はdesign §2.1案A — `_kick_jev` / `_run_direct_pipeline` のL1〜3後に `GroupEngine.handle(生成)→ JevWorker.handle(group_ctx) → LatchEngine.handle(1対1のみ) → GroupEngine.finalize(集約)` の共通チェーン(ヘルパー `_run_post_retrieval`)を直列挿入する。Pool構築は人数緩和(min<=4 AND max>=3)専用SQL+Layer 3同一計算(Vector 50 → cheap_score → Pool 15)。互換行列はPool∪{種}(最大16)の1 SQL事前計算で貪欲法(純関数)へ渡す。集約はI-1対策として「失敗しうる読取をすべてtx前・計算(退避つきaggregate UPDATE)と生成物(latches INSERT・status_events・proposed遷移)を同一tx」で書く。外部SDKなし(純計算+DB+既存Gateway経路の再利用。context7確認不要 — design §1冒頭)。

**Tech Stack:** 変更なし(Python 3.13 / SQLAlchemy[asyncio]+asyncpg / redis-py(asyncio) / fakeredis[unit]。依頼追加なし・依存追加なし)。

**Spec:** `docs/plans/M2/ws-7-design.md`(agent1設計メモ。**未解決論点なし** — design §5のsupervisor承認事項4件〔①Pool人数緩和検索②種=起点③マイグレーション0005④I-1の1対1側改修〕は2026-09-29承認済み(STATUS.md)、解釈記録5〜11はdesign §2の確定値どおりに本計画へ反映済み・STATUS「G2時確認事項」へ記載済み。実装時確認事項§5-12〜13はTask 10へ手順として組み込み〔§5-12はintegration試験コードに含めスーパーバイザー検証時に確認・§5-13はdense配置試験の実行時間記録〕)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m2-ws-7`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(依存追加はないためlockは進まない)。
- **共有ci-db運用**(STATUS運用ルール1〜3): 本単位は**マイグレーション0005を追加する単位**である。**agent3は `make test-ci` / `make migrate` / `make up` / `make down` / `docker compose …` / `docker build` / `docker pull` を一切実行しない**。開発はunit試験(`make lint`・`make test`)で完結させ、報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する。integration試験ファイルは作成するが**実行せず**、収集(`--collect-only`)のみ確認する。`make test` はintegrationファイルの収集(import)まで行うため、構文・importの正当性はunit実行で検証される。本単位はwave単独(ws-8は未着手)のため並走とのDB取り合いは計画上発生しない(運用ルール1・2は報告書へ明記)。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-09-29)**: 現状 `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空。本計画の新規3ファイル(`test_group_calc.py`・`test_group_engine.py`・`test_matching_groupengine.py`)は既存96ファイルと衝突しない(既存リストに同名なし)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **alembic 0001〜0004は変更禁止**(STATUS運用ルール1〜3・design §3.3): 本単位が追加するのは0005のみ。`git diff main -- backend/alembic/versions/000[1-4]*` が空であることをTask 10で検証する。
- **並走単位なし**(design §1前提: 実行wave上の後続はws-8で未着手)。ただし既存ファイルへ触れるため、各Taskのコミット前に `git status --short` で意図しないファイルの変更が混入していないことを確認する(§4の一覧以外に差分が出ていたら作業を止めて報告する)。
- **固定値の遵守**: design §2 の採用判断(案A・Pool緩和検索・種=起点・0005・I-1改修)と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| 全組み合わせ探索は行わない。候補Poolを小さくしてからGroup Search。MVPでは最大4人・候補Poolは最大15・同一地域・時間帯・カテゴリ内に限定 | 01 §15・06 §7 |
| Pool内のIntentをcheap_score降順(タイブレークintent_id昇順)に走査して種を選び、種のparticipants.max >= 3ならHard互換(人数範囲の共通包含、時間・距離・年齢(飲酒・§2)・自己除外=作成user_id相異。v0.4で可視性は除外)なIntentをcheap_score順に追加して3〜4人の集合を構成。集合Sの成立条件は \|S\| ≧ max(min_i) かつ \|S\| ≦ min(max_i) | 06 §7 |
| 構成した集合をgroup_candidatesへ記録し、全ペアのmatch_candidatesを評価世代付きで生成。未判定ペアが残る集合はstatus=candidateのまま提案対象から外す | 06 §7 |
| D-24: Kは「その層から次層へ渡す出力数の上限」。Vector K_v=50・Cheap Judge K_c=20・Jev K_j=8(回数ベース)・**グループPool上限15**。切り詰めは各層スコア上位(Pool=cheap_score)・同点はintent_id昇順 | 06 §8 D-24 |
| K_j=8配分: (1)1対1候補をcheap_score降順で上位4件判定(1対1最低枠保証)(2)残り最大4回をグループ集合の構成ペアにcheap_score降順で割当(3)全ペアの判定が揃わない集合は当該イベントで提案化せずstatus=candidate保持・未判定ペアを次の再評価でのJev予算の**最優先**対象とする(4人集合は高々2回の再評価で確定)(4)割当順序は1対1最低4回を確保したうえで残りを「前回未判定ペアの継続(優先)→新規グループ集合のペア」。グループ由来ペアが存在しなければ残り枠を1対1へ繰り上げ(この場合の1対1実効上限はK_j=8) | 06 §5 |
| D-06 集約規則: aggregate_score = H × min over ペア(MutualScore) × C。通知閾値は1対1と同じ0.80(別閾値を設けない) | 06 §8 D-06 |
| D-06 通知順序: 同一Poolから複数の成立可能集合があった場合、aggregate_score降順・同点は集合サイズの小さい順・さらに同点ならintent_id辞書順に順位付け、**最上位の1集合のみを通知**。その提案が閉じた後に次の集合を評価して通知できる | 06 §8 D-06・01 §15 |
| D-06 成立確定条件: 集合全員のYESが必要。回答期限時に必要人数が揃っていなければexpired — **回答系はM3。ws-7は提案化まで** | 06 §8 D-06 |
| グループ集合もD-07同一手順: prev_aggregate_scoreと新aggregate_scoreの差(\|Δ\|≧0.05)でスコア変化を判定。新評価世代はprev値を持たず無条件に変化あり | 06 §10 |
| group_candidates: intent_ids uuid[](3≦長≦4・INSERT前に全Intentのuser_id相異を検査)・member_scores jsonb・aggregate_score・prev_aggregate_score・status(candidate/proposed/closed) | 05 §2 |
| D-06中間表現: メンバー間のペア評価は**既存のmatch_candidatesを再利用**(メンバー全組み合わせでペアレコードを生成)。成立時はlatches.group_candidate_idで紐付ける。match_candidatesは2 Intent固定のまま | 05 §4 |
| latches: intent_ids(2≦長≦4)・group_candidate_id(FK・グループ成立の場合のみ)・proposal・score・status・response_deadline・expires_at=参加Intentのexpires_at最小値 | 05 §2 |
| proposal構造はvisibility分岐: summary_only全員→全フィールド、hidden_until_matchを含むならheadcountとmatch_levelのみ。category_secondaryは「集合の種(Intent)の値」・budget=ペア予算=参加Intentのbudget_maxの最小値(NULLは無視)・headcount=\|S\| | 05 §2 |
| 削除Eventは「当該Intentを含む候補・保留の無効化(Layerを経ない)」 | 06 §1・§9 |
| participantsのParser出力: 「2〜4人くらい」→min=2,max=4。**「3人以上なら」→min=3,max=4** | 07 §2 |
| 受入#18: 最大4人のグループLATCHを成立できる(3〜4人のIntentセット)。検証はM3だがws-7はmin=3のIntentが種・メンバーとして機能する下地を作る | 02 §4 |
| Layer 1の人数条件「2 ∈ [min_i, max_i] が双方」は**1対1専用**。グループ側は§7の共通包含 | 06 §2 |
| 層別予算: Layer 5+通知 ≤2秒。Layer 4 ≤5秒。本単位の追加処理はDB読取+純計算でLayer 5枠に収まる | 06 §1 |
| Layer 1〜5はembedding_completedを起点にのみ走る(12 C6)・Layer 4通過候補のみが閾値0.80と比較される(12 C7)・時刻参照はすべてClock経由(12 C2)・回答API・expiry_sweeper・FCM・0時リセットはM3(ws-7はnotifications行生成まで・design §1.4) | 12 §2・design §1.4 |
| 実行位置(案A: `_run_post_retrieval` 共通チェーン)・Pool緩和検索(案B)・種=起点・互換行列の事前計算・finalizeのtx構成(I-1対策)・例外方針(design §2.9の表)・YAGNI切り捨て(design §2.10) | design §2 |
| supervisor承認事項(2026-09-29・STATUS): ①Pool人数緩和(min<=4 AND max>=3専用検索+Layer 3同一計算)②種=起点・起点max>=3トリガー③マイグレーション0005④1対1I-1改修のws-7実施。解釈記録5〜11(aggregateのHは集合単位・D-06通知順序はメンバー重複集合の上位1近似・グループ候補へnearby適用なし・member_scores=seed_id+versions・Pool同一Bucket=time_startの30分Bucket・Pool検索HNSW上限=50・area_name=全メンバーgeo_center平均点) | design §5・STATUS「G2時確認事項」 |
| 実装時確認事項: §5-12(uuid[]の`@>`・`&&`・部分UNIQUE索引へのON CONFLICT推論が3〜4要素で動くこと)はintegration試験コードに含めスーパーバイザー検証時に確認。§5-13(HNSW検索2回の実行時間)はdense配置試験の実行時間を記録 | design §5-12〜13 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **永続化は `sqlalchemy.text()` 生SQLのみ**(worker/matching配下・jev.pyと同じ形式)。ORMモデル・リソーストリ層を作らない
- **bind param は `CAST(:x AS ...)` 形式**(NULLを渡しうるパラメータ・uuid・timestamptz・numericは必須。test_layer_sql.py流儀のcompile検査が回帰を防ぐ)。**uuid[]を値として運ぶbind paramは文字列リテラル形式 `'{"uuid","uuid"}'` を `CAST(:x AS uuid[])` へ渡す**(§9-14。asyncpgの配列型推論に頼らない — ws-6 §2規律の継承)。**DB列(uuid[])との比較に使う配列は `ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]` の要素明示形式**(2要素固定箇所)または列そのまま(`@>`/`&&` の左辺・`ANY(列)`)
- **時刻はClock経由のみ**。製品コード(`backend/src/latch/`)で実時間参照禁止(arch test `test_arch_no_direct_time.py` が強制)。30分Bucket境界も `latch_calc.bucket_start`(Clock由来値から導出)を使う(C2)
- **例外は握り潰さない**(design §2.9の表): GroupEngineのDB書き込み失敗は伝播(fail-closed→`_kick_jev` 経由なら_dispatchの既存except→ackなし再配信→再実行。Runner経由なら次周期60秒後。各部のガードで冪等)。起点不在・max<3・Pool空・INSERT競合・未判定ペア残は例外にせずno-op/スキップ+構造化ログ。逆転ジオコーディングの地物なし(area_name=None)は例外にしない
- **例外メッセージ・ログにIntent本文を入れない**(08 §2.4)。ID・status等の機械情報のみ
- **定数はモジュール定数**(settings化しない): `POOL_LIMIT=15`・`POOL_SEARCH_LIMIT=50`・`GROUP_MIN=3`・`GROUP_MAX=4`(group_calc)。閾値0.80・C=1.0・D07_DELTA=0.05は `latch_calc` の既存定数をimportして再利用(再定義しない)
- **丸めなし**: aggregate_score・MutualScore・cheap_scoreとも丸めない
- **DBトランザクションは短tx分割**(design §2.9): (1)Pool検索・互換行列(短tx読取) (2)集合生成(1集合1tx: group_candidates INSERT+メンバー間ペアUPSERT+user_id相異検査) (3)集約材料読取(短tx・すべてtx前) (4)集約tx(退避つきaggregate UPDATE+latches INSERT+latch_status_events+group_candidatesのproposed遷移・I-1対策) (5)try_promoteはLatchEngineの既存構成(1tx)。geo逆転は読取のみ・tx外。LLM呼び出しはJevWorker内のみ
- **origin.py・layer2.py・layer3.py・latch_calc.py・runner.py は変更しない**(§5)。importして呼ぶのみ。H再検証(1対1)は `layer4.hard_constraint_holds` をそのまま呼ぶ。cheap_score計算は `layer3.rule_score`・`vocab_overlap`・`cheap_score` をそのまま呼ぶ。D-07判定は `latch_calc.d07_allows`・`d07_history_inputs` をそのまま呼ぶ
- **改行・行長**: ruff(E,F,I,UP,B)と `ruff format`(行長88)を毎コミット通す
- **unit試験は外部プロセス不要・実時間待ちなし**(スタブ注入・FakeClock)。SQL文字列の正当性はcompile検査(unit)+実DB試験(integration・スーパーバイザー検証時)の二段構え
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **duplicate Event・複数メンバー起点の再実行で同一集合が二重生成される** — `_kick_jev` はduplicateでも実行される(at-least-once)。集合内の複数メンバー(いずれも種になり得るmax>=3)の評価処理でも同一集合が再構成され得る。0005部分UNIQUE+`ON CONFLICT DO NOTHING`+行数0スキップが崩れると重複集合・重複latchesになる → Task 1のSQLピン+Task 7のON CONFLICT分岐試験(unit)+Task 10 integration試験9(handle 2回でgroup_candidates・latches二重なし・candidateイベント1回)
2. **participants_min>=3の起点(07 §2「3人以上なら」→min=3)がグループ処理に乗らない** — 既存 `origin.load_origin` は人数ガード「2 ∈ [min,max]」でmin>=3をskipする(SKIP_PARTICIPANTS)。起点読取をload_originへ寄せると承認事項②(種=起点・起点max>=3トリガー)が実現できない → Task 7のGroupEngine専用起点読取(ガードをmax>=3へ差し替え)とunit試験(min=3起点がskipされない・max=2起点がno-op)+Task 10 integration試験5(min=5はPool外・min=3種が機能)
3. **グループ所属ペアがLatchEngineでlatch_score計算され、1対1として二重提案される** — 集合の構成ペア(全員2人組の組み合わせ)はmatch_candidates行を持つため、`_SELECT_TARGETS`(latch_score計算)と`_CLOSE_BROKEN`(H再検証close)がその行を触ると集合評価と1対1評価が混線する → Task 6の_SELECT_TARGETS除外ピン+Task 4の_CLOSE_BROKEN除外ピン(unit)+Task 10 integration試験10(グループペア行のlatch_score NULL維持)
4. **未判定ペアが残る集合が提案化される** — 引用#4「全ペアの判定が揃わない集合は当該イベントで提案化せずstatus=candidate保持」。finalizeがjev_result未揃いの集合を集約するとmin over ペアが未確定のままlatchesを作る → Task 8の全ペア揃い判定試験(unit・未揃い→何もしない)+Task 10 integration試験3(4人集合6ペアで1イベント未完→candidate保持→次評価の継続枠で最優先)
5. **Intent更新(世代変化)後も旧aggregate_scoreで提案化される** — 引用#10「新評価世代はprev値を持たず無条件に変化あり」。versions不一致のまま集約すると古いペア評価と新しい評価が混ざる → Task 8の世代リセット試験(unit・versions不一致→aggregate/prev=NULL・versions更新)+集約txの`aggregate_score IS NULL`ガードピン

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1):

```text
backend/alembic/versions/0005_group_candidates_open_unique.py   (Task 1。uq_group_candidates_intent_ids_open部分UNIQUE索引)
backend/src/latch/worker/matching/group_calc.py                 (Task 2。純関数群・定数)
backend/src/latch/worker/matching/group_engine.py               (Task 7・8。GroupEngine本体)
backend/tests/unit/matching/test_group_calc.py                  (Task 2)
backend/tests/unit/matching/test_group_engine.py                (Task 7で作成・Task 8で追記)
backend/tests/integration/test_matching_groupengine.py          (Task 10。作成のみ・実行しない)
docs/plans/M2/ws-7-report.md                                    (Task 10。報告ファイル)
```

変更(design §3.2):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/worker/matching/layer1.py` | `LAYER1_WHERE` を `LAYER1_WHERE_BASE`(人数除く)+人数行への最小分割(HEAD/TAIL構成で**現行文字列はバイト不変**・§9-13)。docstring更新 | 3 |
| `backend/src/latch/worker/matching/layer4.py` | (1)`GROUP_PAIR_EXISTS` 断片の公開定数化 (2)`select_jev_rows` へis_group列追加+LIMIT撤去(§9-5) (3)`select_jev_targets(rows, new_pair_row_ids)` 配分拡張 (4)`hard_constraint_holds` へ `relaxed` 引数と `_H_RECHECK_GROUP` (5)`_CLOSE_BROKEN` へグループ所属除外 (6)`PAIR_KIND_GROUP` 定数・`JevCandidateRow.pair_kind` 実値化 | 4 |
| `backend/src/latch/worker/jev.py` | `JevWorker.handle(intent_id, group_ctx=None)`(§9-6)。`_evaluate` のH再検証で `row.pair_kind == 'group'` → relaxed=True。既定Noneは下位互換 | 5 |
| `backend/src/latch/worker/matching/candidates.py` | `upsert_pair`(ID・version直指定・a<b正規化・similarity/cheap_score載せ・RETURNING id)を追加(§9-7) | 5 |
| `backend/src/latch/worker/matching/latch_engine.py` | (1)`_SELECT_TARGETS` へグループ所属除外 (2)`_read_participants` のidsリスト化・`_COUNT_DAILY_NOTIFICATIONS` のANY化 (3)`_SELECT_LATCH_FOR_UPDATE` へgroup_candidate_id・score追加+try_promoteへD-06重複上位チェック (4)`try_promote` のpublic化(_try_promote→try_promote) (5)`_evaluate_pair` のtx統合(I-1改修・§9-8-5) (6)docstring更新 | 6 |
| `backend/src/latch/worker/matching/proposal.py` | `build_group_proposal` 追加(§9-9) | 8 |
| `backend/src/latch/worker/stage1.py` | 削除処理へ `_CLOSE_GROUPS`(group_candidates無効化・§9-10)を同箇所追加 | 9 |
| `backend/src/latch/worker/main.py` | (1)`_run_post_retrieval` 共通ヘルパー新設・`_kick_jev`/`_run_direct_pipeline` へGroupEngine.handle/JevWorker group_ctx/LatchEngine/GroupEngine.finalizeのチェーン挿入(§9-11) (2)GroupEngine DI(LatchEngineインスタンスを注入) | 9 |
| `backend/tests/unit/matching/test_layer_sql.py` | layer1分割のピン追記(BASE/HEAD/TAIL合成・数値は機械的追随) | 3 |
| `backend/tests/unit/matching/test_layer4.py` | select_jev_targets配分・is_group列・relaxed・_CLOSE_BROKEN除外の試験追記+既定値試験の機械的追随 | 4 |
| `backend/tests/unit/test_worker_jev.py` | handleのgroup_ctx既定None試験・relaxed呼出試験を追記(既存試験はシグネチャ不変のため無傷) | 5 |
| `backend/tests/unit/matching/test_latch_engine.py` | (1)SQLピン更新(_SELECT_TARGETS除外・_COUNT_DAILY ANY化・_SELECT_LATCH_FOR_UPDATE列追加) (2)I-1改修の試験1件(§4.1: peer入力欠損→latch_score NULL維持→復旧後完走) (3)D-06上位チェックの境界試験 (4)_read_participants ids化・try_promote改名の機械的追随 | 6 |
| `backend/tests/unit/matching/test_proposal.py` | build_group_proposalの試験追記 | 8 |
| `backend/tests/unit/test_worker.py` | (1)_RecordingJev等スタブのhandleシグネチャ追随(group_ctx=None) (2)`_run_post_retrieval` 配線ピン追加(group→jev→latch→finalizeの順・未注入no-op) | 9 |
| `backend/tests/unit/test_worker_stage1.py` | 削除Eventで_CLOSE_GROUPSが呼ばれるピン(SQL実行はスタブ・削除分岐の追跡) | 9 |

`worker/matching/__init__.py` は変更しない(layer4・latch_engineと同様に直接import規律。group_calc・group_engineも `from latch.worker.matching.group_calc import …` の直接import)。

既存integration(test_matching_jev.py・test_matching_latchengine.py等)は `JevWorker.handle` の外部契約が「第2引数に既定値付きgroup_ctx」で不変のため無変更で動く見込み。影響が出た場合は機械的追随を報告書に記録する。

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **docker系コマンド一切**(§0): `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` / `docker pull`。マイグレーション0005の適用とapiイメージ再ビルド・integration実行はすべてスーパーバイザーの検証手順に含まれる
- **design §3.3の禁止**: `backend/src/latch/` 配下の intents / auth / users / ratelimit / geo / g1gate / events / core / llm 各モジュール。`worker/` の既存ファイルのうち変更対象以外(**origin.py・runner.py・layer2.py・layer3.py・latch_calc.py・embedding.py・embedding_text.py・debounce.py・backfill.py・__main__.py・cost/配下・reeval.py は再利用のみ・変更禁止**。`layer3.cheap_score`/`rule_score`/`vocab_overlap`・`latch_calc.*`・`origin.Origin`/`bind_params` はimportして使う。geo/service.pyの`reverse_geocode`も呼ぶのみ)。`backend/alembic/` の既存分(0001〜0004不変・0005追加のみ)。`compose.yaml`・`frontend/`・`prototype/`・`docs/`(01〜12改版不要・05 §2への0005追記は次回docs改版に含める方針 — design §5承認事項3)。`Makefile`・`backend/pyproject.toml`・`backend/uv.lock`(依存追加なし)・`.env`・`.env.example`・`settings.py`(本単位の追加なし)。`docs/plans/STATUS.md`(スーパーバイザー管理)。`docs/plans/M0/`・`M1/`・`M2/` の既存ファイル(design・過去plan・過去report)。`backend/tests/` の§4に列挙した以外の既存試験
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4・§2.10):
  - **回答API・全員YES成立・部分YES・競合クローズ・matched遷移・解散復帰**(M3-1/M3-2): 本単位はlatchesのcandidate/proposed/expired(75分・昇格)まで。responsesは常に'[]'のまま
  - **expiry_sweeper**(M3-3): candidate(集合・保留)のexpires_at経過によるexpiredはsweeper担当。group_candidatesの掃除(放置されたcandidate)も期限切れEvent(stage1)とsweeperが回収
  - **FCM・お知らせUI・通知文テンプレート**(M3-5): notificationsテーブルへのレコード生成まで(グループ提案はtype='proposal'を共用)。payloadは`{"latch_id"}`の最小参照
  - **0時リセットジョブ**(M3-4): drainが評価経路のたびに回収(ws-6と同じ扱い)
  - **circuit breaker・E2Eハーネス・障害注入・K上限裏付け試験の実施**(ws-8・G2): 本単位は試験コードでPool≦15の記録が取れる状態を作る(§4.2試験2)
  - **PoolのDB永続化・中間表現**(design §2.10-1): Poolは評価ごとに構成する一時物。構造化ログ `group pool built` と試験の観察で担保
  - **グループ候補へのnearby_also存在通知**(design §2.10-2・解釈記録7): 閾値未満の集合はlatchesを作らずgroup_candidates=candidateのまま
  - **group_candidatesへの評価世代カラム追加**(design §2.10-3): 世代はmember_scores jsonb内versionsで足りる
  - **Pool構築の1対1Layer 2検索との統合**(design §2.10-4): 人数WHERE違いの2クエリのまま
  - **貪欲法のスコア最適化・局所探索**(design §2.10-5): 決定的な貪欲法のみ
  - **Redisによる集合状態管理**(design §2.10-7): 状態はすべてDB。冪等ガードもDB側(部分UNIQUE・aggregate_score IS NULL・条件付きUPDATE)
  - **match_candidatesへのpair_kind列追加**(design §2.10-8): 所属判定はgroup_candidates側のEXISTSで動的に行う
  - **INSERT前SELECT FOR UPDATEへの切替**(design §5-12): ON CONFLICT推論が外れたと判明した場合の代替案であり、本計画では採用しない(外れた場合はBLOCKEDとして報告しスーパーバイザーへ相談)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 10で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分・既存試験の機械的追随を含む)
   検証: `make lint && make test` — ともにexit 0
2. **integration 1ファイル(10試験)が収集できる** — **ただし実行はしない**(§0。実DB・実Redis・0005適用後のスキーマが必要なためスーパーバイザー検証時に実施)
   検証(実装側): `cd backend && uv run pytest --collect-only tests/integration/test_matching_groupengine.py -q` がexit 0(10件収集)。報告書に「**test-ci=スーパーバイザー検証待ち**」と記録
3. **実時間参照が `core/clock.py` のみ**(group_calc.py・group_engine.py 配下はヒットしない)
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
4. **マイグレーションは0005追加のみ・0001〜0004とdocs(01〜12)に差分なし**
   検証: `git diff --stat main -- 'backend/alembic/versions/0001*' 'backend/alembic/versions/0002*' 'backend/alembic/versions/0003*' 'backend/alembic/versions/0004*' docs` — 出力なし。加えてalembicチェーンの静的確認(Task 1 Step 2のスクリプト・heads=['0005'])
5. **変更ファイルが§4の一覧どおり(作成7+変更15=22ファイル)**
   検証: Task 10の報告コミット後に `git diff --name-only main | sort` が§4の一覧(22ファイル・report込み)と完全一致。`git status --short` が空(未コミット変更なし)
6. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし
7. **既存unit試験がすべてグリーンのまま**(既存試験の期待値変更は§4列挙の機械的追随のみであることの証明として `make test` の全件数を報告書へ記録し、変更が§4列挙以外に及んでいないことを§4との突合で示す)

## 7. 報告形式

**結果ファイル**: `docs/plans/M2/ws-7-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M2 ws-7(グループマッチ Group Search)実行報告

- ブランチ: m2-ws-7 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も)> |
| 2 | integration 10試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | <collect-only出力> |
| 3 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 4 | alembic 0001〜0004・docs無変更+チェーン確認 | PASS/FAIL | <git diff --stat 出力(空なら「空」)+heads=['0005']> |
| 5 | 変更ファイル=§4の22ファイル | PASS/FAIL | <git diff --name-only出力> |
| 6 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 7 | 既存unit試験グリーン維持 | PASS/FAIL | <make test 全件数と§4列挙以外の期待値変更がないことの確認結果> |

**test-ci=スーパーバイザー検証待ち**(STATUS運用ルール1〜3・マイグレーション0005追加のため
migrate/test-ci/docker系は実装側で未実行。wave単独のため並走とのDB取り合いは計画上なし)

## design §5 実装時確認事項の結果
12. uuid[]の @> / && / 部分UNIQUE索引へのON CONFLICT推論: unitのSQLピン・compile検査は
    済み。実DBでの推論確認はスーパーバイザー検証時のintegration試験1・6・9が担う
    (掠んだ場合は推移を記録しINSERT前SELECT FOR UPDATE切替をsupervisorへ相談)
13. HNSW検索2回の実行時間: スーパーバイザー検証時のdense配置試験(§4.2試験2)の実行時間を
    記録( Layer 5+通知 ≤2秒・06 §1 との照合)

## 固定値の変更有無(design.md §2・本計画§9)
- 実行位置=案A(_run_post_retrieval共通チェーン・design §2.1): 変更なし / 変更あり(<前→後+理由>)
- Pool人数緩和検索+Layer 3同一計算(承認事項1・design §2.2): 変更なし / 変更あり
- 種=起点・起点max>=3トリガー(承認事項2・design §2.3): 変更なし / 変更あり
- 0005部分UNIQUE+ON CONFLICT DO NOTHING(承認事項3・design §2.8): 変更なし / 変更あり
- 1対1I-1改修=tx統合(承認事項4・design §2.7-4): 変更なし / 変更あり
- member_scores=seed_id+versions(design §2.3): 変更なし / 変更あり
- uuid[]bind=文字列リテラル+CAST(本計画§9-14): 変更なし / 変更あり
- 本計画§9のIF確定事項(SQL全文・純関数・try_promote手順): 変更なし / 変更あり(<前→後+理由>)

## (ws-8・M3への引継ぎ)
- ws-8: Pool≦15の記録は試験2(dense配置)が供給。構造化ログ `group pool built pool_size=`
  が実行時の観察点。K上限裏付け試験(G2)で再利用
- M3-1〜M3-5: グループlatchesの回答は responses への追記(全員YES成立・部分成立なし・
  期限時未揃い=expired)。latches.group_candidate_id から集合を引ける。
  expiry_sweeper は group_candidates.status=candidate の放置掃除も担当
- G2: design §5の解釈記録5〜11はSTATUS「G2時確認事項」③に記載済み

## スーパーバイザー検証手順(test-ci実行時・design §4.3)
1. `make lint && make test` — unit全件グリーン(報告書と同じ結果になること)
2. `docker compose build api worker` — イメージ再ビルド(STATUS運用ルール4)
3. `make migrate` — 0005適用確認(alembic_version=0005・索引 `\di uq_group_candidates_intent_ids_open` の存在)
4. `uv run pytest --collect-only tests/integration/test_matching_groupengine.py -q`
   (backend/内・収集10件の確認。実行前の静的確認)
5. `make test-ci` — 既存全数(1030)+本単位integration 10件がグリーン。
   design §5-12のON CONFLICT/包含推論は試験1・6・9が実証。
   design §5-13のHNSW 2回の実行時間は試験2の所要から確認(≤2秒予算・06 §1)
6. 時刻参照がclock.pyのみ: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|
   time\.sleep|from time import' backend/src` が core/clock.py のみ
7. alembic無変更確認: `git diff main -- 'backend/alembic/versions/000[1-4]*'` が空
8. 変更ファイル一覧が本計画§4と一致・`git status` 空・
   `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空(運用ルール5)
9. test-ci後、残存確認を1回手動実施: users(subject LIKE 'm2ws7-%'=0件)・Redis(prefix掃除)・
   group_candidates・latches・latch_status_events・notifications(prefix由来=0件)

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件7項目の結果一覧(項目2の実行部分は「test-ci=スーパーバイザー検証待ち」)。

## 9. IF確定事項(実装定義の決定値。docsが例示のみの部分を本計画で確定する。変更時は報告書に記録)

### 9-1. モジュール構成・uuid[]パラメータ方式

- 新規モジュール `group_calc.py`(純関数・§9-2)と `group_engine.py`(GroupEngine本体・§9-3〜9-4)。`worker/matching/__init__.py` へのexportは追加しない(直接import規律 — §4)
- 依存方向: `group_calc` → `latch_calc`(LATCH_C再利用)。`group_engine` → `origin`/`layer1`(LAYER1_WHERE_BASE)/`layer3`/`candidates`/`latch_calc`/`proposal`/`group_calc`/`layer4`(jst_day_start不使用・latch_engine.try_promote)。`latch_engine` → `group_calc`(uuid_array_text・dominates)。`proposal` → `group_calc`(group_target_time)。**循環なし**(group_calcはlatch_calc・uuid以外をimportしない)
- **uuid[]を値として運ぶbind paramは文字列リテラル形式で渡す**(asyncpgの配列型推論に頼らない — ws-6 §2規律の可変長版)。組立は `group_calc.uuid_array_text(ids)`(§9-2)が唯一の供給源:

```text
CAST(:ids AS uuid[])  ←  '{"00000000-0000-4000-8000-000000000001","00000000-…-02"}'
```

- DB列(uuid[])との比較に使う配列は要素明示 `ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]`(2要素固定・layer4._SELECT_JEV_ROWSのis_groupなど**列から組む場合はbind不要の `ARRAY[mc.intent_a_id, mc.intent_b_id]`**)か、文字列リテラル+CAST(3〜4要素・可変長)。`ANY(列)` 形式(左辺がDB列)はbind不要
- この方式を使うSQL: `_POOL_SEARCH` ではない(idsを返す側)・`_PAIR_COMPAT`(:ids)・`_SELECT_GROUP_VERSIONS`(:ids)・`_SELECT_GROUP_PAIRS`(:ids)・`_INSERT_GROUP`(:ids)・`_INSERT_GROUP_LATCH`(:ids)・`_FIND_OPEN_GROUP_LATCH`(:ids)・`_SELECT_GROUP_LATCH_RESPONSES`(:ids)・latch_engine `_COUNT_DAILY_NOTIFICATIONS`(:users)・`_SELECT_HIGHER_GROUP_LATCH`(:my_ids)

### 9-2. group_calc.py 全文(定数・純関数)

```python
"""グループマッチ(Group Search)の純関数群・定数(06 §7〜§8・design §2.2〜2.3・§2.5〜2.6)。

DB・async・SQLを持たない純計算のみ(unit試験は決定的)。Pool構築の
cheap_score計算はlayer3の純関数をGroupEngineが呼ぶ(ここでは再実装しない)。
C(集約Calibration)はlatch_calc.LATCH_Cをimportして再利用(再定義しない)。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from latch.worker.matching.latch_calc import LATCH_C

POOL_SEARCH_LIMIT = 50  # Pool検索のHNSW取得上限(K_vと同値・design §2.2)
POOL_LIMIT = 15  # 06 §8 D-24(グループ候補Pool上限)
GROUP_MIN = 3  # 06 §7(3〜4人の集合)
GROUP_MAX = 4


@dataclass(frozen=True)
class PoolEntry:
    """貪欲法の入力1件(Pool行または種=起点。design §2.3)。"""

    intent_id: uuid.UUID
    user_id: uuid.UUID
    participants_min: int
    participants_max: int


def normalize_ids(ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    """intent_idsのsorted正規化(normalize_pairと同じ規律・design §2.8)。"""
    return sorted(ids)


def group_target_time(time_starts: Iterable[datetime]) -> datetime:
    """対象開始時刻 = max(メンバーtime_start)(pair_target_timeの集合版)。"""
    return max(time_starts)


def uuid_array_text(ids: Iterable[uuid.UUID]) -> str:
    """uuid[]のbind param文字列(§9-14): CAST(:x AS uuid[]) へ渡す。

    asyncpgの配列型推論に頼らない(ws-6 §2規律の可変長版)。
    """
    return "{" + ",".join(f'"{u}"' for u in ids) + "}"


def aggregate_score(mutual_scores: list[float]) -> float:
    """aggregate = H × min over ペア(MutualScore) × C(06 §8 D-06)。

    H は呼び出し前の集合再検証通過=1(不成立ならclosedとし集約しない —
    design §2.5手順3・解釈記録5)。丸めない。
    """
    return LATCH_C * min(mutual_scores)


def dominates(
    self_score: float,
    self_ids: list[uuid.UUID],
    other_score: float,
    other_ids: list[uuid.UUID],
) -> bool:
    """D-06通知順序の上位判定: other が self より上位か(design §2.6)。

    aggregate_score降順 → 同点はサイズ昇順 → さらに同点ならintent_id辞書順。
    両idsはsorted正規化済み前提(呼び出し側で保証)。
    """
    if other_score != self_score:
        return other_score > self_score
    if len(other_ids) != len(self_ids):
        return len(other_ids) < len(self_ids)
    return list(other_ids) < list(self_ids)


def _pair_key(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """互換行列のキー(a < b 正規化)。"""
    return (a, b) if a < b else (b, a)


def _settled(members: list[PoolEntry]) -> bool:
    """引用#3: |S| >= 3 かつ |S| >= max(min_i) かつ |S| <= min(max_i)。"""
    lo = max(m.participants_min for m in members)
    hi = min(m.participants_max for m in members)
    return len(members) >= GROUP_MIN and lo <= len(members) <= hi


def _feasible_size(entries: list[PoolEntry]) -> bool:
    """人数見込み: [max(min_i), min(max_i)] ∩ [3, 4] が空でない(design §2.3手順3-c)。

    lo > hi(区間自体が空 — 例: min=4の種に max=3の候補で [4,3])も空と判定
    する(design §4.1「min(max_i)=3の候補はmin=4種の集合に入れない」)。
    """
    lo = max(e.participants_min for e in entries)
    hi = min(e.participants_max for e in entries)
    return lo <= hi and lo <= GROUP_MAX and hi >= GROUP_MIN


def build_groups(
    pool: list[PoolEntry],
    seed: PoolEntry,
    compat: frozenset[tuple[uuid.UUID, uuid.UUID]],
) -> list[tuple[list[uuid.UUID], uuid.UUID]]:
    """貪欲法で3〜4人の集合を構成(06 §7・design §2.3手順1〜6)。

    pool はcheap_score降順(同点intent_id昇順)ソート済み・起点を含まない。
    seed(起点)は走査順の先頭に立つ(承認事項2・起点max>=3は呼び出し側が
    保证)。compat は互換ペアの集合(要素は (小id, 大id) タプル・design §2.3の
    互換行列)。戻り値は (sorted正規化intent_ids, 種id) のタプル列表
    (確定順・複数集合可。種idはmember_scores.seed_idと
    build_group_proposalの種先頭に使う)。不成立の集合(手順5)は種のみ消費
    して次の種へ(追加しかけた候補は残る)。
    """
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

### 9-3. group_engine.py のSQL全文(text()定数)

import・モジュール属性規律は §9-4。SQLはすべて `text()`・bindは§2グローバル制約形式。

```python
# 起点読取(origin.pyの_SELECT_ORIGINと同一列・人数ガードはload_group_origin側で
# 「max >= 3」へ差し替え — design §2.2のPool緩和の起点版・Review Focus 2)
_SELECT_GROUP_ORIGIN = text("""
    SELECT i.id, i.version, i.user_id, i.category_primary, i.alcohol_involved,
           i.budget_max, i.participants_min, i.participants_max,
           i.geo_radius_m, i.time_start, i.time_end, i.status, i.embedding,
           i.structured_data,
           ST_X(i.geo_center::geometry) AS lon,
           ST_Y(i.geo_center::geometry) AS lat,
           u.birth_date
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE i.id = CAST(:intent_id AS uuid)
""")

# 候補Pool検索(design §2.2案B・承認事項1)。起点との互換はLAYER1_WHERE_BASE
# (人数行を緩和)・Bucketは起点time_startの属する30分Bucket(解釈記録9)
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
    LIMIT {POOL_SEARCH_LIMIT}
""")

# 互換行列(design §2.3)。対称条件のため i1.id < i2.id の一方向だけ取る。
# 人数条件は含まない(最終判定は貪欲法の共通包含とfinalizeのH再検証が担う)。
# soft_texts・rule計算に必要な列も同時取得(メンバー間ペアのcheap_score用)
_PAIR_COMPAT = text("""
    SELECT i1.id AS a_id, i2.id AS b_id,
           1 - (i1.embedding <=> i2.embedding) AS similarity,
           i1.time_start AS a_time_start, i1.budget_max AS a_budget_max,
           i1.structured_data AS a_structured,
           i2.time_start AS b_time_start, i2.budget_max AS b_budget_max,
           i2.structured_data AS b_structured
    FROM intents i1
    JOIN intents i2 ON i1.id < i2.id
    JOIN users u1 ON u1.id = i1.user_id
    JOIN users u2 ON u2.id = i2.user_id
    WHERE i1.id = ANY(CAST(:ids AS uuid[]))
      AND i2.id = ANY(CAST(:ids AS uuid[]))
      AND i1.status = 'active' AND i2.status = 'active'
      AND i1.time_start < COALESCE(i2.time_end, i2.time_start + interval '3 hours')
      AND i2.time_start < COALESCE(i1.time_end, i1.time_start + interval '3 hours')
      AND ST_DWithin(i1.geo_center, i2.geo_center,
                     CAST(i1.geo_radius_m + COALESCE(i2.geo_radius_m, 1000)
                          AS double precision))
      AND i1.user_id <> i2.user_id
      AND NOT EXISTS (
          SELECT 1 FROM blocks b
          WHERE (b.blocker_id = i1.user_id AND b.blocked_id = i2.user_id)
             OR (b.blocker_id = i2.user_id AND b.blocked_id = i1.user_id))
      AND (
          (NOT (i1.alcohol_involved OR i2.alcohol_involved))
          OR (
              EXTRACT(YEAR FROM AGE(
                  (CAST(:now AS timestamptz) AT TIME ZONE 'Asia/Tokyo'),
                  CAST(u1.birth_date AS timestamp))) >= 20
              AND EXTRACT(YEAR FROM AGE(
                  (CAST(:now AS timestamptz) AT TIME ZONE 'Asia/Tokyo'),
                  CAST(u2.birth_date AS timestamp))) >= 20
          )
      )
""")

# 集合の記録(design §2.3・§2.8)。ON CONFLICTは0005部分UNIQUE索引へ推論
_INSERT_GROUP = text("""
    INSERT INTO group_candidates
        (intent_ids, member_scores, status, created_at, updated_at)
    VALUES (CAST(:ids AS uuid[]), CAST(:member_scores AS jsonb),
            'candidate', :now, :now)
    ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed')
    DO NOTHING
    RETURNING id
""")

# finalize対象(design §2.5): 起点が属する開いている集合
_SELECT_PENDING_GROUPS = text("""
    SELECT g.id, g.intent_ids, g.member_scores, g.aggregate_score
    FROM group_candidates g
    WHERE g.status = 'candidate'
      AND CAST(:origin AS uuid) = ANY(g.intent_ids)
""")

# 集合メンバーの現行version・人数・user_id(世代判定・H再検証・INSERT前user相異検査)
_SELECT_GROUP_VERSIONS = text("""
    SELECT id, version, user_id, participants_min, participants_max
    FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

# 集合内ペア行(全ペア揃い判定とMutualScore計算の材料)
_SELECT_GROUP_PAIRS = text("""
    SELECT id, intent_a_id, intent_b_id, intent_a_version, intent_b_version,
           jev_result
    FROM match_candidates
    WHERE intent_a_id = ANY(CAST(:ids AS uuid[]))
      AND intent_b_id = ANY(CAST(:ids AS uuid[]))
""")

# 世代リセット(design §2.5手順1・引用#10: 新評価世代はprevを持たない)
_RESET_GENERATION = text("""
    UPDATE group_candidates
    SET aggregate_score = NULL, prev_aggregate_score = NULL,
        member_scores = CAST(:member_scores AS jsonb), updated_at = :now
    WHERE id = CAST(:gid AS uuid) AND status = 'candidate'
""")

# 退避つき集約更新(06 §10手順1〜2のgroup版・I-1対策の本体前半)
_UPDATE_AGGREGATE = text("""
    UPDATE group_candidates
    SET prev_aggregate_score = aggregate_score,
        aggregate_score = CAST(:score AS numeric), updated_at = :now
    WHERE id = CAST(:gid AS uuid) AND aggregate_score IS NULL
    RETURNING id, prev_aggregate_score
""")

# latches生成(design §2.5。0004部分UNIQUEへON CONFLICT・group_candidate_idつき)
_INSERT_GROUP_LATCH = text("""
    INSERT INTO latches
        (intent_ids, group_candidate_id, proposal, score, status,
         response_deadline, expires_at, created_at)
    VALUES (CAST(:ids AS uuid[]), CAST(:gid AS uuid), CAST(:proposal AS jsonb),
            :score, 'candidate', CAST(:deadline AS timestamptz),
            CAST(:expires AS timestamptz), :now)
    ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed', 'partial_accept')
    DO NOTHING
    RETURNING id
""")

# ON CONFLICTで飛んだ場合の既存開いている行特定(1対1の_FIND_OPEN_LATCHと同型)
_FIND_OPEN_GROUP_LATCH = text("""
    SELECT id, status FROM latches
    WHERE intent_ids = CAST(:ids AS uuid[])
      AND status IN ('candidate', 'proposed', 'partial_accept')
""")

# D-07履歴(集合版・uuid[]等値。3〜4要素でそのまま動く — design §2.5)
_SELECT_GROUP_LATCH_RESPONSES = text("""
    SELECT responses FROM latches
    WHERE intent_ids = CAST(:ids AS uuid[])
      AND responses <> CAST('[]' AS jsonb)
""")

# 集合のproposed遷移(latches生成と同一tx・design §2.5)
_MARK_GROUP_PROPOSED = text("""
    UPDATE group_candidates
    SET status = 'proposed', updated_at = :now
    WHERE id = CAST(:gid AS uuid) AND status = 'candidate'
""")

# H再検証不成立(design §2.5手順3: 集合のみ閉じる・構成ペア行は閉じない)
_CLOSE_GROUP_BY_ID = text("""
    UPDATE group_candidates
    SET status = 'closed', updated_at = :now
    WHERE id = CAST(:gid AS uuid) AND status = 'candidate'
""")
```

補足:

- `_POOL_SEARCH` のbind param は `{**bind_params(org), "origin_embedding": org.embedding, "bucket_start": ..., "bucket_end": ...}`。`bucket_start = latch_calc.bucket_start(org.time_start)`(起点time_startの属するBucket)・`bucket_end = bucket_start + timedelta(minutes=latch_calc.BUCKET_MINUTES)`
- `_PAIR_COMPAT` のbind param は `{"ids": group_calc.uuid_array_text(ids), "now": org.evaluated_at}`。`ids` はPool全員+起点(最大16)
- `_INSERT_GROUP` の `:ids` は `group_calc.uuid_array_text(sorted_ids)`・`:member_scores` は `json.dumps({"seed_id": str(seed_id), "versions": {str(i): v, ...}}, ensure_ascii=False)`
- `_INSERT_GROUP_LATCH` の `:score` はfloat(numeric列へ。1対1の_INSERT_LATCHと同じ形式でCASTなし実績あり)

### 9-4. GroupEngine の構成(クラス・手順)

```python
@dataclass(frozen=True)
class GroupContext:
    """GroupEngine.handleの戻り値(design §2.1)。JevWorkerの配分順序判定に使う。"""

    group_ids: frozenset[uuid.UUID]  # 今回INSERTしたgroup_candidatesのid
    new_pair_row_ids: frozenset[uuid.UUID]  # 今回UPSERTしたメンバー間ペアの行id


class GroupEngine:
    """グループマッチ本体(06 §7〜§8・design §2.2〜2.8)。冪等(0005部分UNIQUE・
    UPSERT・aggregate_score IS NULLガード)。モジュール属性経由で
    origin(型のみ)・layer1(BASE)・layer3・candidates・latch_calc・proposal・
    latch_engine(try_promote)を呼ぶ(runnerと同一規律・unit試験が
    monkeypatchで差し替え可能)。"""

    def __init__(self, *, engine: AsyncEngine, clock: Clock, geo=None,
                 latch: LatchEngine | None = None) -> None: ...
```

**`load_group_origin(conn, clock, intent_id) -> OriginLoad`**(モジュール関数): origin.pyの`load_origin`と同一列・同一skip理由(不在/非active/embedding NULL/geo無し/time無し)だが**人数ガードのみ「participants_max >= 3(種になれる条件)」へ差し替え**(`Origin`型をそのまま返す・skip理由 `SKIP_GROUP_ORIGIN_MAX = "origin_not_group"`)。origin.pyは変更しない(§5)。Originの構築はorigin.pyと同一ロジック(time_end・geo_radius_m補完・`age_years`・`soft_texts`)。

**`handle(intent_id) -> GroupContext | None`** の手順(design §2.2〜2.3):

```text
1. 短tx: loaded = load_group_origin(conn, clock, intent_id)
   skip→no-op(構造化ログ "group origin no-op intent_id=%s reason=%s")→None
2. 短tx: rows = _POOL_SEARCH(params)。0件→no-op(ログ "group pool empty")→None
3. 各行へcheap_score計算(起点とのペア・Layer 3と同一計算):
     rule = layer3.rule_score(origin_time_start=org.time_start,
                              cand_time_start=r.time_start,
                              origin_budget=org.budget_max,
                              cand_budget=r.budget_max)
     vocab = layer3.vocab_overlap(org.soft_texts, layer3.soft_texts(r.structured_data))
     cheap = layer3.cheap_score(r.similarity, rule, vocab)
4. pool = sorted(降順cheap・同点intent_id昇順)[:POOL_LIMIT]
   構造化ログ "group pool built intent_id=%s pool_size=%d"
4b. 短tx(書込・冪等): Pool全メンバー×起点のペアUPSERT(design §2.3「種×
   メンバーはPool構築時に既にUPSERT済み」)。各Pool行 r へ
   upsert_pair(conn, intent_a_id=org.intent_id, intent_b_id=r.intent_id,
   intent_a_version=org.version, intent_b_version=r.version,
   similarity=r.similarity, cheap_score=手順3のcheap, now=org.evaluated_at)
   → 戻り行idを new_pair_row_ids へ加える(集合に入ったメンバーとの行は
   pair_kind=group〔EXISTS〕+ ∈new_pair_row_ids=新規としてK_j配分される。
   集合に入らなかったPoolメンバーとの行はEXISTSしないためpair_kind=
   one_on_one扱いで1対1経路の対象 — min>=3相手はH再検証で自然に閉じる)
5. 短tx: pair_info = _PAIR_COMPAT(ids=[org.intent_id, *pool_ids])
   → dict[(a_id, b_id) sorted] = (similarity: float, cheap: float)
     (cheap は rule_score(a/bのtime・budget)+vocab_overlap(a/bのsoft)+
      cheap_score(similarity, rule, vocab) を行ごとに計算)
6. seed = PoolEntry(org)・entries = [PoolEntry(r) for r in pool(cheap降順)]
   groups = group_calc.build_groups(entries, seed, frozenset(pair_info))
7. group_ids=set()・new_pair_row_ids=set()
   各group(確定順)について 1集合1tx:
   a. ids_sorted = group_calc.normalize_ids(group)
   b. members_now = _SELECT_GROUP_VERSIONS(ids_sorted)  ← INSERT前の現行値
      行が1件でも欠け・user_id重複あり→スキップ(ログ)して次の集合へ
   c. gid = _INSERT_GROUP(ids, member_scores={"seed_id": 種id,
        "versions": {id: 現行version}}, now=org.evaluated_at)
      None(ON CONFLICT・開いている同一集合あり)→スキップ(ログ
        "group duplicate skipped")して次へ(既存集合はfinalizeが回収)
      ※ 種id: build_groupsの戻り値 (ids, seed_id) の第2要素(起点=種・
        第2集合以降は残り走査先頭)
   d. メンバー間ペア(起点を含まない全ペア = idsから起点を除いた組み合わせ):
      各ペア (a, b) について upsert_pair(conn, intent_a_id=a, intent_b_id=b,
        intent_a_version=現行, intent_b_version=現行,
        similarity=pair_info[key][0], cheap_score=pair_info[key][1],
        now=org.evaluated_at) → row_id を new_pair_row_ids へ
8. return GroupContext(frozenset(group_ids), frozenset(new_pair_row_ids))
```

**`finalize(intent_id) -> None`** の手順(design §2.5。I-1対策: 失敗しうる読取をすべて集約txの前に済ませる):

```text
1. 短tx: loaded = load_group_origin(...)  skip→no-op(ログ)してreturn
2. 短tx: groups = _SELECT_PENDING_GROUPS(origin)
3. 各group(gid, intent_ids, member_scores, aggregate_score)について:
   a. ms = member_scores(dict化・{"seed_id": str, "versions": {str: int}})
   b. 短tx: now_versions = _SELECT_GROUP_VERSIONS(intent_ids) → dict[id] = (version, user_id, min, max)
   c. 世代判定(design §2.5手順1): ms["versions"] と現行version組が不一致
      (キー集合または値)→ 短tx: _RESET_GENERATION(gid, 新versions)
      → ms["versions"] を現行組へ更新・aggregate_score=None扱いで続行
   d. 全ペア揃い判定(手順2): 短tx: rows = _SELECT_GROUP_PAIRS(intent_ids)
      現行version組の全ペア(|S|C2)が存在し全て jev_result IS NOT NULL か
      → 揃わなければ continue(status=candidate保持・引用#4)
      mutuals = [min(float(j["would_a_accept_b"]), float(j["would_b_accept_a"]))
                 for 各ペア]
   e. H集合再検証(手順3・読取のみ):
      - 人数包含: |S| >= max(min_i) かつ |S| <= min(max_i)(現行min/max)
      - 互換: _PAIR_COMPAT(intent_ids) の結果ペア集合に全ペア(|S|C2)が含まれるか
      不成立→ 短tx: _CLOSE_GROUP_BY_ID(gid) → continue(構成ペア行は閉じない)
   f. 集約材料読取(すべてtx前・I-1対策):
      inputs = [await _read_member_inputs(engine, iid) for iid in intent_ids]
      (latch_engine._SELECT_INTENT_INPUTS相当の読取をgroup_engine内の
       モジュール関数 _read_member_inputs で行う — 行なし/expires_at NULL
       →None。1人でもNone→continue(aggregate NULLのまま・次の評価処理で
       再選択される))
      target = group_calc.group_target_time(m.time_start ...)
      min_expires = min(m.expires_at ...)
      area = await self._area_name(inputs)(全メンバーgeo_centerの平均点の
        逆転ジオコーディング・解釈記録11。geo未注入ならNone)
      score = group_calc.aggregate_score(mutuals)
      responses = 短tx: _SELECT_GROUP_LATCH_RESPONSES(ids)
      has_no, latest_defer_at = latch_calc.d07_history_inputs(responses)
   g. 集約tx(1集合1tx・I-1対策の本体):
      async with engine.begin() as conn:
        row = _UPDATE_AGGREGATE(gid, score, now=clock.now())
        行数0(aggregate計算済み)→ return(冪等スキップ・§2.9の表)
        prev = row.prev_aggregate_score(float|None)
        if score < latch_calc.LATCH_THRESHOLD: return
          (candidateのまま・aggregate_scoreは入る — design §2.5)
        deadline0 = latch_calc.response_deadline(now, target, min_expires)
        if not latch_calc.d07_allows(has_no_response=has_no,
              latest_defer_at=latest_defer_at, now=now, target_time=target,
              new_score=score, prev_latch_score=prev):
            return(ログ "group d07 denied gid=%s")
        proposal = proposal_mod.build_group_proposal(
            members=種を先頭に並べたinputs, score=score, area_name=area)
        latch_id = _INSERT_GROUP_LATCH(ids, gid, proposal, score,
                                       deadline0, min_expires, now)
        if latch_id is None:  # ON CONFLICT(開いている行あり)
            found = _FIND_OPEN_GROUP_LATCH(ids)
            if found is None or found.status != 'candidate': return(ログ)
            d07_allows を再度判定(prev同値)→ Falseならreturn(ログ)
            _UPDATE_FOR_PROMOTION(latch_engineの既存SQLと同型・group側は
              group_engineへ _UPDATE_GROUP_LATCH_FOR_PROMOTION を定義:
              UPDATE latches SET score=:score, proposal=:proposal,
              response_deadline=:deadline WHERE id=:latch_id
              AND status='candidate')
            latch_id = found.id
        else:
            _INSERT_LATCH_EVENT(latch_id, None, 'candidate', None, now)
              (latch_engine._INSERT_LATCH_EVENTと同一SQL — group_engine内に
               同一定数を置く)
        _MARK_GROUP_PROPOSED(gid, now)
   h. latch_id が決まったら tx後: self._latch がNoneでなければ
      await self._latch.try_promote(latch_id)
      (D-06上位チェックはtry_promote内側・§9-8-4)
```

- `now` は `self._clock.now()`(各txの手前で1回採取・tx内で共有)
- `_read_member_inputs`: `latch_engine._read_intent_inputs` と同一SQL・同一構築(LatchIntentInputsを返す)。latch_engineの関数をそのままimportして使う(private流用を避けるためgroup_engineへ同一定数・同関数を置く — latch_engine.pyは変更するが_read_intent_inputs自体はI-1改修で存続)

### 9-5. layer4.py の変更

(1) 定数の追加(冒頭・`PAIR_KIND_ONE_ON_ONE`の下):

```python
PAIR_KIND_GROUP = "group"  # グループ集合由来ペア(ws-7)
# ペアが開いている集合に属するかのEXISTS断片(列参照のみ・bind不要)。
# select_jev_rows(所属=EXISTS)・_CLOSE_BROKEN と latch_engine._SELECT_TARGETS
# (非所属=NOT EXISTS)が共用(design §2.4・§2.7-1)
GROUP_PAIR_EXISTS = """EXISTS (
        SELECT 1 FROM group_candidates g
        WHERE g.status IN ('candidate', 'proposed')
          AND g.intent_ids @> ARRAY[mc.intent_a_id, mc.intent_b_id]
    )"""
```

(2) `_SELECT_JEV_ROWS`(LIMIT撤去・is_group列追加):

```python
_SELECT_JEV_ROWS = text(f"""
    SELECT mc.id, mc.intent_a_id, mc.intent_b_id, mc.intent_a_version,
           mc.intent_b_version, mc.cheap_judge_score, mc.status, mc.skip_reason,
           {GROUP_PAIR_EXISTS} AS is_group
    FROM match_candidates mc
    WHERE (mc.intent_a_id = CAST(:origin AS uuid)
           OR mc.intent_b_id = CAST(:origin AS uuid))
      AND (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                THEN mc.intent_a_version
                ELSE mc.intent_b_version END) = :origin_version
      AND mc.jev_result IS NULL
      AND (
        mc.status = 'pending'
        OR (mc.status = 'skipped'
            AND mc.skip_reason IN ('llm_failure', 'invalid_output'))
        OR (mc.status = 'skipped'
            AND mc.skip_reason IN ('intent_daily', 'user_daily', 'global_daily')
            AND mc.updated_at < CAST(:jst_day_start AS timestamptz))
        OR (mc.status = 'skipped' AND mc.skip_reason = 'global_monthly'
            AND mc.updated_at < CAST(:jst_month_start AS timestamptz))
      )
    ORDER BY mc.cheap_judge_score DESC,
             CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                  THEN mc.intent_b_id
                  ELSE mc.intent_a_id END ASC
""")
```

(WHERE句・ORDER BYは現行と同一文字列・`LIMIT 8`のみ撤去。LIMIT撤去はdesign §2.4「LIMIT 8は撤去する(配分は純関数側で行うため)」。`select_jev_rows` は戻り値の `JevCandidateRow` 生成時に `pair_kind=(PAIR_KIND_GROUP if r[8] else PAIR_KIND_ONE_ON_ONE)` を設定)

(3) `select_jev_targets` の配分拡張(06 §5規則1〜4・design §2.4):

```python
def select_jev_targets(
    rows: list[JevCandidateRow],
    new_pair_row_ids: frozenset[uuid.UUID] = frozenset(),
) -> list[JevCandidateRow]:
    """K_j配分の純関数(06 §5規則1〜4・design §2.4)。

    入力rowsはcheap_score降順(SQLのORDER BY由来)。規則: (1)1対1上位4件を
    最低枠として選ぶ(2)残り枠(max4)をグループペアへ — 継続(row_id ∉
    new_pair_row_ids)を優先し、次に新規(∈)(3)グループで埋まらない枠は
    1対1の残りを繰り上げ(総数上限8)。各層内の順序は入力順(cheap降順)。
    """
    one_on_one = [r for r in rows if r.pair_kind == PAIR_KIND_ONE_ON_ONE]
    selected = one_on_one[:ONE_ON_ONE_MIN]
    budget = K_J - len(selected)
    if budget > 0:
        continuing = [
            r
            for r in rows
            if r.pair_kind == PAIR_KIND_GROUP
            and r.row_id not in new_pair_row_ids
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

(4) `hard_constraint_holds` へrelaxed引数(design §2.4):

```python
_H_RECHECK_GROUP = text(f"""
    SELECT 1 FROM intents i JOIN users u ON u.id = i.user_id
    WHERE i.id = CAST(:candidate_id AS uuid)
      AND {LAYER1_WHERE_BASE}
      AND i.participants_min <= 4
      AND i.participants_max >= 3
    LIMIT 1
""")


async def hard_constraint_holds(
    conn: AsyncConnection,
    origin: Origin,
    candidate_id: uuid.UUID,
    *,
    relaxed: bool = False,
) -> bool:
    """H再検証。relaxed=Trueはグループペア用(人数をPool条件と同一の緩和へ
    差し替え・design §2.4)。人数の最終判定はfinalizeのH再検証が担う。"""
    params = bind_params(origin)
    params["candidate_id"] = candidate_id
    stmt = _H_RECHECK_GROUP if relaxed else _H_RECHECK
    row = (await conn.execute(stmt, params)).first()
    return row is not None
```

(5) `_CLOSE_BROKEN` へグループ所属除外(WHERE末尾に1行追加・他は不変):

```python
      AND NOT {GROUP_PAIR_EXISTS}
```

### 9-6. jev.py の変更・GroupContext受渡し

- `JevWorker.handle` のシグネチャ: `async def handle(self, intent_id: uuid.UUID, group_ctx=None) -> None:`。docstringへ「group_ctx(GroupEngine.handleの戻り値・None可)はselect_jev_targetsの継続/新規判定に使う」を追記。本文末尾の選択ループ:

```python
        new_pair_row_ids = (
            group_ctx.new_pair_row_ids if group_ctx is not None else frozenset()
        )
        for row in layer4.select_jev_targets(rows, new_pair_row_ids):
            await self._evaluate(org, origin_inp, row)
```

- `_evaluate` 内のH再検証(現行 `holds = await layer4.hard_constraint_holds(conn, org, peer_id)`)を:

```python
            holds = await layer4.hard_constraint_holds(
                conn,
                org,
                peer_id,
                relaxed=(row.pair_kind == layer4.PAIR_KIND_GROUP),
            )
```

- group_ctx=None(既定)は全グループペアを継続扱いにする(new_pair_row_ids=frozenset())— 下位互換(design §2.4)

### 9-7. candidates.py への upsert_pair 追加

```python
_UPSERT_PAIR = text("""
    INSERT INTO match_candidates (
        intent_a_id, intent_b_id, intent_a_version, intent_b_version,
        retrieval_score, cheap_judge_score, status, created_at, updated_at
    ) VALUES (
        :intent_a_id, :intent_b_id, :intent_a_version, :intent_b_version,
        :retrieval_score, :cheap_score, 'pending', :now, :now
    )
    ON CONFLICT (intent_a_id, intent_b_id, intent_a_version, intent_b_version)
    DO UPDATE SET
        retrieval_score = EXCLUDED.retrieval_score,
        cheap_judge_score = EXCLUDED.cheap_judge_score,
        updated_at = EXCLUDED.updated_at
    RETURNING id
""")


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT/RETURNING結果のUUID列復元(asyncpgサブクラス対策・origin.pyと同一)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def upsert_pair(
    conn: AsyncConnection,
    *,
    intent_a_id: uuid.UUID,
    intent_b_id: uuid.UUID,
    intent_a_version: int,
    intent_b_version: int,
    similarity: float,
    cheap_score: float,
    now,
) -> uuid.UUID:
    """ID・version直指定の1ペアUPSERT(design §2.3・グループのメンバー間ペア用)。

    upsert_candidate(Origin起点)と違い起点を取らない。a<b正規化は同一規律。
    RETURNING id(group_ctxのnew_pair_row_idsの供給源)。DO UPDATE句は
    upsert_candidateと同一(statusを壊さない)。丸めない。
    """
    a_id, b_id = sorted((intent_a_id, intent_b_id))
    versions = {intent_a_id: intent_a_version, intent_b_id: intent_b_version}
    res = await conn.execute(
        _UPSERT_PAIR,
        {
            "intent_a_id": a_id,
            "intent_b_id": b_id,
            "intent_a_version": versions[a_id],
            "intent_b_version": versions[b_id],
            "retrieval_score": similarity,
            "cheap_score": cheap_score,
            "now": now,
        },
    )
    return _coerce_uuid(res.first()[0])
```

(`_UPSERT`・`upsert_candidate` は無変更)

### 9-8. latch_engine.py の変更(5箇所)

**(1) `_SELECT_TARGETS` へグループ所属除外**(design §2.7-1)。ORDER BYの直前に1行追加(他は不変)。**`_DRAIN_CANDIDATES` は無変更**(`ANY(l.intent_ids)`・max(time_start)相関サブクエリが既に配列対応済みのため、グループlatchesもscore>=0.80のcandidate行として自然に走査対象になる — design §2.7-2):

```python
      AND NOT {layer4.GROUP_PAIR_EXISTS}
```

**(2) `_COUNT_DAILY_NOTIFICATIONS` のANY化**(design §2.7-2):

```python
_COUNT_DAILY_NOTIFICATIONS = text("""
    SELECT user_id, COUNT(*) FROM notifications
    WHERE user_id = ANY(CAST(:users AS uuid[]))
      AND type IN ('proposal', 'nearby_candidate')
      AND created_at >= CAST(:day_start AS timestamptz)
      AND created_at < CAST(:day_next AS timestamptz)
    GROUP BY user_id
""")
```

`_count_daily_notifications` はシグネチャ不変(`conn, user_ids: list[uuid.UUID], day_start, day_next`)。実装のu0/u1組立を削除し `{"users": group_calc.uuid_array_text(user_ids), ...}` へ。docstringの「0〜2要素・u1=u0」記述を「0〜4要素(2者または3〜4者の参加者)」へ更新。冒頭へ `from latch.worker.matching import group_calc` を追加。

**(3) `_read_participants` のidsリスト化**(design §2.7-2):

```python
async def _read_participants(
    conn, intent_ids: list[uuid.UUID]
) -> list[Participant]:
    """全参加者の_SELECT_INTENT_INPUTS読取(2〜4要素)。

    行なし・expires_at NULLのIntentは除外(呼び出し側が
    len(parts) < len(intent_ids) で対象外化)。順序はintent_idsどおり。
    """
    out: list[Participant] = []
    for intent_id in intent_ids:
        row = (
            (await conn.execute(_SELECT_INTENT_INPUTS, {"intent_id": intent_id}))
            .mappings()
            .first()
        )
        if row is None or row["expires_at"] is None:
            continue
        out.append(Participant(...))  # 現行と同一構築
    return out
```

**(4) try_promoteの|S|人対応・D-06上位チェック・public化**(design §2.6・§2.7-2):

- `_SELECT_LATCH_FOR_UPDATE` へ2列追加(他は不変):

```python
    SELECT id, status, intent_ids, expires_at, group_candidate_id, score
    FROM latches WHERE id = CAST(:latch_id AS uuid)
    FOR UPDATE
```

- `_select_latch_for_update` の戻りタプルへ `row[4]`(group_candidate_id)・`row[5]`(score)を追記
- 新規SQL・ヘルパー:

```python
# D-06通知順序: メンバーが重なる開いている集合(design §2.6)
_SELECT_HIGHER_GROUP_LATCH = text("""
    SELECT l.score, l.intent_ids
    FROM latches l
    WHERE l.status IN ('candidate', 'proposed', 'partial_accept')
      AND l.group_candidate_id IS NOT NULL
      AND l.id <> CAST(:self AS uuid)
      AND l.intent_ids && CAST(:my_ids AS uuid[])
""")


async def _has_higher_group_latch(
    conn, self_id: uuid.UUID, self_ids: list[uuid.UUID], self_score: float
) -> bool:
    """自分より上位(aggregate降順→サイズ昇順→辞書順)の重複集合があるか。"""
    res = await conn.execute(
        _SELECT_HIGHER_GROUP_LATCH,
        {"self": self_id, "my_ids": group_calc.uuid_array_text(self_ids)},
    )
    for score, ids in res.fetchall():
        other_ids = sorted(_coerce_uuid(x) for x in ids)
        if group_calc.dominates(self_score, self_ids, float(score), other_ids):
            return True
    return False
```

- `try_promote`(旧 `_try_promote`・public化。内部呼び出し3箇所〔`_proposal_path`末尾・`_drain`内〕も `self.try_promote` へ)。本文変更点:
  - `parts = await _read_participants(conn, row[2])`(idsリスト渡し)
  - `if len(parts) < 2:` → `if len(parts) < len(row[2]):`(全員揃わなければ対象外)
  - 75分ルール・expires_at切れ判定の**後**・D-08日次カウントの**前**へ挿入:

```python
            if row[4] is not None:  # グループlatchesのみ(design §2.6)
                if await _has_higher_group_latch(conn, latch_id, row[2], float(row[5])):
                    return  # candidateのまま(上位の行が閉じた後のdrainで提示)
```

  - それ以外(対象時刻 `max(p.time_start)`・`min(p.expires_at)`・D-08・notifications)は現行ロジックのまま|S|人で自然に動く
- docstringの手順列挙へ「D-06重複上位チェック(グループのみ)」を追記

**(5) `_evaluate_pair` のtx統合(I-1改修・承認事項4・design §2.7-4)**。書き換え後の全文:

```python
    async def _evaluate_pair(self, org, row: LatchTargetRow) -> None:
        """1ペア: 材料読取(tx前)→tx1(H再検証+退避つきUPDATE+D-07+latches+events)。

        I-1対策(design §2.7-4・承認事項4): 失敗しうる読取をtx前に済ませ、
        計算(latch_score退避UPDATE)と生成物(latches INSERT・status_events)を
        同一txで書く。読取段階の失敗はlatch_score NULLのまま残るため、
        復旧後の再handleで再選択される(ws-6の引継ぎ空白の構造解消)。
        """
        now = self._clock.now()
        peer_id = (
            row.intent_b_id if row.intent_a_id == org.intent_id else row.intent_a_id
        )
        a_id, b_id = sorted((org.intent_id, peer_id))
        # tx前の読取(全材料・短tx内包): peer入力が欠けていれば書かない
        origin_inputs = await _read_intent_inputs(self._engine, org.intent_id)
        peer_inputs = await _read_intent_inputs(self._engine, peer_id)
        if origin_inputs is None or peer_inputs is None:
            logger.info(
                "latch inputs missing row_id=%s peer_id=%s", row.row_id, peer_id
            )
            return
        target = latch_calc.pair_target_time(
            origin_inputs.time_start, peer_inputs.time_start
        )
        min_expires = min(origin_inputs.expires_at, peer_inputs.expires_at)
        area = await self._area_name(origin_inputs, peer_inputs)
        responses = await _read_latch_responses(self._engine, a_id, b_id)
        has_no, latest_defer_at = latch_calc.d07_history_inputs(responses)
        mutual = min(
            float(row.jev_result["would_a_accept_b"]),
            float(row.jev_result["would_b_accept_a"]),
        )
        score = latch_calc.LATCH_C * mutual
        deadline0 = latch_calc.response_deadline(now, target, min_expires)
        # tx1: H再検証(SELECT) + 退避つきlatch_score UPDATE + 生成物
        latch_id: uuid.UUID | None = None
        async with self._engine.begin() as conn:
            if not await layer4.hard_constraint_holds(conn, org, peer_id):
                await _close_h_broken(conn, row.row_id, now)
                return
            rec = await _record_score(conn, row.row_id, score, now)
            if rec is None:
                return
            _, prev_latch_score = rec
            if score >= latch_calc.LATCH_THRESHOLD:
                if not latch_calc.d07_allows(
                    has_no_response=has_no,
                    latest_defer_at=latest_defer_at,
                    now=now,
                    target_time=target,
                    new_score=score,
                    prev_latch_score=prev_latch_score,
                ):
                    logger.info("latch d07 denied a=%s b=%s", a_id, b_id)
                    return
                proposal = proposal_mod.build_proposal(
                    origin=origin_inputs, peer=peer_inputs,
                    score=score, area_name=area,
                )
                latch_id = await _insert_latch(
                    conn, a_id=a_id, b_id=b_id, proposal=proposal, score=score,
                    deadline=deadline0, expires=min_expires, now=now,
                )
                if latch_id is not None:
                    await _insert_latch_event(
                        conn, latch_id, None, "candidate", None, now
                    )
                else:
                    found = await _find_open_latch(conn, a_id, b_id)
                    if found is None:
                        return
                    lid, status = found
                    if status != "candidate":
                        logger.info(
                            "latch open row exists latch_id=%s status=%s",
                            lid,
                            status,
                        )
                        return
                    if not latch_calc.d07_allows(
                        has_no_response=has_no,
                        latest_defer_at=latest_defer_at,
                        now=now,
                        target_time=target,
                        new_score=score,
                        prev_latch_score=prev_latch_score,
                    ):
                        logger.info(
                            "latch d07 denied on promotion a=%s b=%s", a_id, b_id
                        )
                        return
                    if not await _update_for_promotion(
                        conn, lid, score=score, proposal=proposal,
                        deadline=deadline0,
                    ):
                        return
                    latch_id = lid
            else:
                latch_id = await self._nearby_in_tx(
                    conn,
                    a_id=a_id,
                    b_id=b_id,
                    score=score,
                    origin_inputs=origin_inputs,
                    peer_inputs=peer_inputs,
                    deadline0=deadline0,
                    min_expires=min_expires,
                    has_no=has_no,
                    now=now,
                )
        if latch_id is not None:
            await self.try_promote(latch_id)
```

- `_nearby_path` を `_nearby_in_tx(conn, ...)` へ改名・`engine.begin()` を自分で開かず引数のconnを使う形へ(中身の判定・INSERT・カウント・通知は現行 `_nearby_path` と同一。戻り値は「新規INSERT成功ならlatch_id・開いている行あり/対象なしはNone」)。nearbyはtry_promoteしない(現行どおりcandidateのまま)
- `_proposal_path` は削除(tx統合で吸収)。観測結果(latch_score・latches・events・notifications)は不変(§6-7の機械的追随範囲)

### 9-9. proposal.py への build_group_proposal 追加

```python
def build_group_proposal(
    *,
    members: list[LatchIntentInputs],  # 種を先頭(design §2.7-3)
    score: float,
    area_name: str | None,
) -> dict:
    """集合版proposal生成(05 §2・引用#14・design §2.7-3)。

    visibility分岐は1対1と同一: 全員summary_onlyでなければheadcountと
    match_levelのみ。全フィールド側は time_summary=max(time_start)のJST
    書式(ws-6 §2.5と同一)・category_secondaryは種(members[0])の値・
    budget=参加budget_maxの最小値(NULLは無視)・headcount=|S|。
    """
    if any(m.visibility != "summary_only" for m in members):
        return {"headcount": len(members), "match_level": match_level(score)}
    budgets = [m.budget_max for m in members if m.budget_max is not None]
    seed = members[0]
    return {
        "time_summary": group_target_time(m.time_start for m in members)
        .astimezone(JST)
        .strftime("%Y-%m-%d %H:%M"),
        "area_name": area_name,
        "headcount": len(members),
        "category_primary": seed.category_primary,  # Layer 1完全一致で同一
        "category_secondary": seed.category_secondary,  # 種の値(引用#14)
        "budget": {"max": min(budgets)} if budgets else None,
        "match_level": match_level(score),
    }
```

冒頭へ `from latch.worker.matching.group_calc import group_target_time` を追加。docstringのws-7拡張点記述を実装に差し替え。

### 9-10. stage1.py への _CLOSE_GROUPS 追加

```python
_CLOSE_GROUPS = text("""
    UPDATE group_candidates
    SET status = 'closed', updated_at = :now
    WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
      AND status <> 'closed'
""")
```

`_process_once` の削除分岐(`if event_type == EVENT_DELETED:`)へ `_CLOSE_CANDIDATES` の直後に1行追加:

```python
                await conn.execute(
                    _CLOSE_GROUPS, {"intent_id": intent_id, "now": now}
                )
```

### 9-11. worker/main.py の配線

- import追加: `from latch.worker.matching.group_engine import GroupEngine`(`from latch.worker.matching.latch_engine import LatchEngine` の直後)
- `Worker.__init__` へ `group: GroupEngine | None = None` を追加・`self._group = group`
- `run()` 内・LatchEngine構築ブロックの後に:

```python
            # GroupEngine DI(M2 ws-7・design §2.1案A): LatchEngine直後の
            # グループ生成・集約。latchはtry_promote委譲用(Noneなら提案化なし)
            if self._group is None:
                self._group = GroupEngine(
                    engine=engine,
                    clock=self.clock,
                    geo=GeoService(engine),
                    latch=self._latch,
                )
```

- 新規ヘルパー(`_kick_jev` の直前に配置):

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

- `_kick_jev` の本文(`if event_type != EVENT_EMBEDDING_COMPLETED: return` の後)を `await self._run_post_retrieval(intent_id)` へ(旧Jev/Latch直列呼び出しは削除)。docstringへ「GroupEngine.handle→Jev→Latch→GroupEngine.finalizeの共通チェーン(_run_post_retrieval)」を追記
- `_run_direct_pipeline` の後半(`run_candidate_retrieval` のtx後)を `await self._run_post_retrieval(intent_id)` へ

### 9-12. マイグレーション0005 全文

```python
"""group_candidatesへ開いている行の部分UNIQUE索引を追加(M2 ws-7・design §2.8)。

同一intent_idsの開いている(status IN ('candidate','proposed'))集合を1行に
強制する。at-least-once再実行と複数メンバー起点(いずれも種になり得る)の
再構成による同一集合重複生成をDBで防ぐ最小構成(supervisor承認事項3・
2026-09-29)。閉じた集合(closed)の履歴は複数行保持できる(D-07履歴検査・
集計の供給源)。05 §2への追記は次回docs改版に含める(ws-5/ws-6前例)。

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_group_candidates_intent_ids_open"
        " ON group_candidates (intent_ids)"
        " WHERE status IN ('candidate', 'proposed')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_group_candidates_intent_ids_open")
```

### 9-13. layer1.py の分割(バイト不変)

`LAYER1_WHERE` 定義(現行35〜70行)を次の構成へ置き換える。**HEAD+ONE_ON_ONE+TAIL の連結は現行の `LAYER1_WHERE` とバイト同一**(test_layer_sql.pyの全ピンが無修正で通る)。`_SELECT_HARD` は `LAYER1_WHERE` 参照のまま無変更。docstringへ「design §2.2: BASE(人数除く)はグループPool検索・緩和H再検証が使う」を追記。

```python
# design §2.2: 人数行のみを分離した最小分割。HEAD+ONE_ON_ONE+TAIL は
# 従来の LAYER1_WHERE とバイト同一(1対1検索の文字列不変・承認事項1)
LAYER1_WHERE_HEAD = f"""
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
"""
ONE_ON_ONE_PARTICIPANTS = """    AND i.participants_min <= 2
    AND i.participants_max >= 2
"""
LAYER1_WHERE_TAIL = """    AND NOT EXISTS (
        SELECT 1 FROM blocks b
        WHERE (b.blocker_id = CAST(:origin_user_id AS uuid)
               AND b.blocked_id = i.user_id)
           OR (b.blocker_id = i.user_id
               AND b.blocked_id = CAST(:origin_user_id AS uuid)))
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
# 人数行(06 §2「2 ∈ [min_i, max_i] が双方」)を除いたLayer 1条件。
# グループPool検索(group_engine._POOL_SEARCH)と緩和H再検証
# (layer4._H_RECHECK_GROUP)が人数を差し替えて使う(design §2.2)
LAYER1_WHERE_BASE = f"{LAYER1_WHERE_HEAD}{LAYER1_WHERE_TAIL}"
# Layer 1 の全条件(06 §2・design §2.6)。layer2 が同一文字列を使用する
LAYER1_WHERE = f"{LAYER1_WHERE_HEAD}{ONE_ON_ONE_PARTICIPANTS}{LAYER1_WHERE_TAIL}"
```

### 9-14. uuid[]パラメータ方式の背景(ws-6 §2規律の継承)

ws-6計画は「uuid[]は要素明示 `ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]`」を確定した(当時は2要素固定)。本単位は3〜16要素の可変長配列をbindで運ぶため、要素数が固定のSQL(latches履歴照会は要素数可変…)は要素明示を維持し、**可変長は文字列リテラル `uuid_array_text()` + `CAST(:x AS uuid[])`** とする。理由: (1)asyncpgのlist→配列自動変換はtext()経由の型推論に依存し、コードベースに前例がない(ws-6と同一の消極理由)(2)文字列リテラルは単一textパラメータのためcompile検査(`:name` 残留検出)がそのまま効く(3)PostgreSQLの配列リテラル構文は `'{"uuid","uuid"}'`。unit試験はSQLピン+compile検査、実挙動(キャスト・推論)はintegration(スーパーバイザー検証時)が実証する(design §5-12)。

## 8. 実装ステップ(TDD。Task 1〜10の順で実行する)

### Task 1: マイグレーション0005(group_candidates部分UNIQUE索引)

**Files:**
- Create: `backend/alembic/versions/0005_group_candidates_open_unique.py`

**Interfaces:**
- Consumes: なし
- Produces(Task 7・8・integrationが使用): 部分UNIQUE索引 `uq_group_candidates_intent_ids_open`(同一intent_idsの開いている集合を1行に強制・`_INSERT_GROUP` の `ON CONFLICT (intent_ids) WHERE status IN ('candidate','proposed') DO NOTHING` の推論対象)

- [ ] **Step 1: 実装する**

`backend/alembic/versions/0005_group_candidates_open_unique.py` を作成。内容は§9-12のとおり(revision="0005"・down_revision="0004"・`op.execute` のCREATE UNIQUE INDEX … WHERE 1文+downgradeのDROP INDEX)。

- [ ] **Step 2: チェーンの静的確認(`make migrate` は実行禁止・§0)**

Run:
```bash
cd backend && uv run python - <<'EOF'
from alembic.config import Config
from alembic.script import ScriptDirectory
sd = ScriptDirectory.from_config(Config("alembic.ini"))
heads = sd.get_heads()
assert heads == ["0005"], heads
walk = [rev.revision for rev in sd.walk_revisions()]
assert walk == ["0005", "0004", "0003", "0002", "0001"], walk
print("OK heads=", heads)
EOF
```
Expected: `OK heads= ['0005']`(チェーン接続の静的確認)
Run: `cd backend && uv run pytest --collect-only tests/integration/test_schema.py -q`
Expected: 収集成功(実DB検証はtest-ci=スーパーバイザー検証待ち)

- [ ] **Step 3: コミット**

```bash
make lint && make test
git add backend/alembic/versions/0005_group_candidates_open_unique.py
git commit -m "feat: マイグレーション0005(group_candidates開いている行の部分UNIQUE索引)"
```

### Task 2: group_calc.py(純関数群・定数)

**Files:**
- Create: `backend/src/latch/worker/matching/group_calc.py`
- Test: `backend/tests/unit/matching/test_group_calc.py`

**Interfaces:**
- Consumes: `latch_calc.LATCH_C`(importして再利用)
- Produces(Task 4〜9が使用 — §9-2のとおり):
  - 定数: `POOL_SEARCH_LIMIT=50` / `POOL_LIMIT=15` / `GROUP_MIN=3` / `GROUP_MAX=4`
  - `PoolEntry(intent_id, user_id, participants_min, participants_max)`(frozen dataclass)
  - `normalize_ids(ids) -> list[uuid.UUID]`(sorted)
  - `group_target_time(time_starts) -> datetime`(max)
  - `uuid_array_text(ids) -> str`('{"uuid","uuid"}'形式)
  - `aggregate_score(mutual_scores: list[float]) -> float`(= LATCH_C × min)
  - `dominates(self_score, self_ids, other_score, other_ids) -> bool`(D-06上位判定)
  - `build_groups(pool: list[PoolEntry], seed: PoolEntry, compat: frozenset[tuple[uuid, uuid]]) -> list[tuple[list[uuid.UUID], uuid.UUID]]`(sorted ids・種id)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_group_calc.py` を作成(design §4.1-1〜4・すべて決定的)。

```python
"""group_calc純関数のunit試験(design §4.1・06 §7〜§8 D-06/D-24)。"""

import uuid
from datetime import UTC, datetime, timedelta

from latch.worker.matching.group_calc import (
    GROUP_MAX,
    GROUP_MIN,
    POOL_LIMIT,
    POOL_SEARCH_LIMIT,
    PoolEntry,
    aggregate_score,
    build_groups,
    dominates,
    group_target_time,
    normalize_ids,
    uuid_array_text,
)

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


def _entry(
    n: int, *, pmin: int = 2, pmax: int = 4, user: int | None = None
) -> PoolEntry:
    return PoolEntry(
        intent_id=_uid(n),
        user_id=_uid(user if user is not None else 1000 + n),
        participants_min=pmin,
        participants_max=pmax,
    )


def _compat(*pairs: tuple[int, int]) -> frozenset[tuple[uuid.UUID, uuid.UUID]]:
    """全互換にするには全組み合わせを列挙する(ここでは指定ペアのみ互換)。"""
    out: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for a, b in pairs:
        x, y = _uid(a), _uid(b)
        out.add((x, y) if x < y else (y, x))
    return frozenset(out)


def _full_compat(ids: list[int]) -> frozenset[tuple[uuid.UUID, uuid.UUID]]:
    return _compat(
        *[(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]]
    )


def test_constants_pin_docs_values():
    assert POOL_LIMIT == 15  # 06 §8 D-24
    assert POOL_SEARCH_LIMIT == 50  # design §2.2(K_vと同値)
    assert GROUP_MIN == 3 and GROUP_MAX == 4  # 06 §7


def test_seed_max_ge_3_builds_three_member_group():
    """種(max>=3)+互換2件 → 3人集合確定(design §2.3手順1〜4)。"""
    compat = _full_compat([1, 2, 3])
    groups = build_groups([_entry(2), _entry(3)], _entry(1, pmax=4), compat)
    assert groups == [(normalize_ids([_uid(1), _uid(2), _uid(3)]), _uid(1))]


def test_pool_entry_max_lt_3_not_in_group_and_not_seed():
    """max=2のPool要素は人数見込みで弾かれ(手順3-c)・種にも選ばれない。

    種1+{8,9}で第1集合。残り[7(max=2),10]は7が種になれず10単独では
    3人未満 → 戻りは1集合のみ(もし7が集合に入っていれば{1,7,8}になる)。
    """
    compat = _full_compat([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    groups = build_groups(
        [_entry(7, pmax=2), _entry(8), _entry(9), _entry(10)],
        _entry(1),
        compat,
    )
    assert groups == [(normalize_ids([_uid(1), _uid(8), _uid(9)]), _uid(1))]


def test_seed_max_lt_3_returns_empty():
    """防御: max<3の起点は呼び出し側(GroupEngine)が弾く前提・空リスト。"""
    groups = build_groups([_entry(2), _entry(3)], _entry(1, pmax=2), _compat())
    assert groups == []


def test_three_member_group_does_not_extend_to_four():
    """3人で成立したら4人へ拡張しない(引用#8のサイズ昇順と整合)。"""
    compat = _full_compat([1, 2, 3, 4])
    groups = build_groups(
        [_entry(2), _entry(3), _entry(4)], _entry(1), compat
    )
    assert len(groups[0][0]) == 3


def test_min4_seed_collects_four_members():
    """min=4の種は4人まで追加を続ける(|S|>=max(min_i)=4で確定)。"""
    compat = _full_compat([1, 2, 3, 4])
    groups = build_groups(
        [_entry(2), _entry(3), _entry(4)], _entry(1, pmin=4, pmax=4), compat
    )
    assert len(groups[0][0]) == 4


def test_unreachable_group_not_generated():
    """min=4の種に候補2件 → |S|=3で確定できず不成立(手順5)。"""
    compat = _full_compat([1, 2, 3])
    groups = build_groups([_entry(2), _entry(3)], _entry(1, pmin=4), compat)
    assert groups == []


def test_inclusion_rejects_candidate_whose_max_is_too_small():
    """人数包含の逐次判定: min=4種の集合にmax=3の候補は入らない(手順3-c)。"""
    compat = _full_compat([1, 2, 3, 4, 5, 6])
    # 種min=4・候補(2..6)のうち max=3 の候補(2,3)は見込みが弾く
    groups = build_groups(
        [
            _entry(2, pmax=3),
            _entry(3, pmax=3),
            _entry(4),
            _entry(5),
            _entry(6),
        ],
        _entry(1, pmin=4, pmax=4),
        compat,
    )
    assert groups == [(
        normalize_ids([_uid(1), _uid(4), _uid(5), _uid(6)]),
        _uid(1),
    )]


def test_user_conflict_skips_candidate():
    """作成user_id相異違反の候補はスキップ(手順3-b)。"""
    compat = _full_compat([1, 2, 3, 4])
    groups = build_groups(
        [_entry(2, user=1001), _entry(3), _entry(4)],  # 2は種(1)と同user
        _entry(1),
        compat,
    )
    assert groups == [(
        normalize_ids([_uid(1), _uid(3), _uid(4)]),
        _uid(1),
    )]


def test_incompatible_candidate_skipped():
    """互換行列にないペアを含む候補はスキップ(手順3-a・全メンバーと互換)。"""
    compat = _full_compat([1, 3, 4])  # 1×2・2×3・2×4は非互換
    groups = build_groups([_entry(2), _entry(3), _entry(4)], _entry(1), compat)
    assert groups == [(
        normalize_ids([_uid(1), _uid(3), _uid(4)]),
        _uid(1),
    )]


def test_multiple_groups_second_seed_from_remaining_pool():
    """複数集合: 確定集合のメンバーを除き残りから次の種(引用#16の下地)。"""
    compat = _full_compat([1, 2, 3, 4, 5, 6])
    groups = build_groups(
        [_entry(2), _entry(3), _entry(4), _entry(5), _entry(6)],
        _entry(1),
        compat,
    )
    assert [sorted(g[0]) for g in groups] == [
        sorted([_uid(1), _uid(2), _uid(3)]),
        sorted([_uid(4), _uid(5), _uid(6)]),
    ]
    assert groups[1][1] == _uid(4)  # 第2集合の種は残り走査先頭


def test_scan_order_follows_input_pool_order():
    """走査順は入力pool順(cheap降順ソートは呼び出し側の責務)。"""
    compat = _full_compat([1, 3, 4])  # 1×2も非互換(2は仲間に入れない)
    groups = build_groups(
        [_entry(2), _entry(3), _entry(4)], _entry(1), compat
    )
    assert groups == [(normalize_ids([_uid(1), _uid(3), _uid(4)]), _uid(1))]


def test_aggregate_takes_min_over_pairs():
    assert aggregate_score([0.9, 0.7, 0.85]) == 0.7  # C=1.0(LATCH_C)
    assert aggregate_score([0.83]) == 0.83


def test_normalize_ids_sorts():
    a, b = _uid(9), _uid(2)
    assert normalize_ids([a, b]) == [b, a]


def test_group_target_time_takes_max():
    t1 = NOW + timedelta(hours=1)
    t2 = NOW + timedelta(hours=3)
    assert group_target_time([t1, t2, NOW]) == t2


def test_uuid_array_text_format():
    assert uuid_array_text([_uid(1), _uid(2)]) == (
        "{" + f'"{_uid(1)}"' + "," + f'"{_uid(2)}"' + "}"
    )


def test_dominates_ordering():
    a = [_uid(1), _uid(2), _uid(3)]
    b = [_uid(4), _uid(5), _uid(6)]
    small = [_uid(1), _uid(2)]
    assert dominates(0.7, a, 0.9, b)  # スコア降順
    assert not dominates(0.9, a, 0.7, b)
    assert dominates(0.8, a, 0.8, small)  # 同点はサイズ昇順(小さい方が上位)
    assert not dominates(0.8, small, 0.8, a)
    c = [_uid(1), _uid(2), _uid(9)]  # 辞書順で a < c
    assert dominates(0.8, a, 0.8, c) is False
    assert dominates(0.8, c, 0.8, a)  # aの方が上位
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_group_calc.py -v`
Expected: FAIL(ModuleNotFoundError: latch.worker.matching.group_calc)

- [ ] **Step 3: 実装する**

`backend/src/latch/worker/matching/group_calc.py` を§9-2の全文どおり作成。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_group_calc.py -v`
Expected: PASS(全試験)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/group_calc.py backend/tests/unit/matching/test_group_calc.py
git commit -m "feat: group_calc純関数群(貪欲法・集約・D-06上位判定・uuid[]組立)"
```

### Task 3: layer1.py の最小分割(§9-13)

**Files:**
- Modify: `backend/src/latch/worker/matching/layer1.py:34-70`(LAYER1_WHERE定義をHEAD/ONE_ON_ONE/TAIL構成へ)
- Test: `backend/tests/unit/matching/test_layer_sql.py`(追記)

**Interfaces:**
- Consumes: なし(既存 `_BUDGET_PAIR`・`PAIR_BUDGET_MIN_YEN`)
- Produces(Task 4・7が使用): `LAYER1_WHERE_BASE`(人数行を除くLayer 1条件文字列)。`LAYER1_WHERE`(合成後)は**バイト不変**・`ONE_ON_ONE_PARTICIPANTS`・`LAYER1_WHERE_HEAD`・`LAYER1_WHERE_TAIL`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_layer_sql.py` へ追記:

```python
# -- layer1 最小分割(M2 ws-7・design §2.2) --


def test_layer1_split_composes_identical_where():
    """BASE+人数行の分割。LAYER1_WHERE は HEAD+ONE_ON_ONE+TAIL と一致。"""
    assert layer1.LAYER1_WHERE == (
        f"{layer1.LAYER1_WHERE_HEAD}"
        f"{layer1.ONE_ON_ONE_PARTICIPANTS}"
        f"{layer1.LAYER1_WHERE_TAIL}"
    )
    assert layer1.LAYER1_WHERE_BASE == (
        f"{layer1.LAYER1_WHERE_HEAD}{layer1.LAYER1_WHERE_TAIL}"
    )


def test_layer1_base_excludes_participants_conditions():
    """BASEに人数行なし(POOL_SEARCH・_H_RECHECK_GROUPが人数を差し替える)。"""
    assert "participants" not in layer1.LAYER1_WHERE_BASE
    # 人数以外の全条件はBASEにも残る
    where = layer1.LAYER1_WHERE_BASE
    assert "i.status = 'active'" in where
    assert "ST_DWithin" in where
    assert "NOT EXISTS" in where and "blocks" in where
    assert "EXTRACT(YEAR FROM AGE(" in where
    assert "LEAST(" in where


def test_layer1_where_keeps_participants_and_unchanged_pins():
    """合成後のLAYER1_WHEREは人数込み(既存ピンの回帰確認)。"""
    assert "i.participants_min <= 2" in layer1.LAYER1_WHERE
    assert "i.participants_max >= 2" in layer1.LAYER1_WHERE
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer_sql.py -v`
Expected: 新規3件FAIL(AttributeError: LAYER1_WHERE_BASE)。**既存試験は全てPASSのまま**(現行コードが基準)

- [ ] **Step 3: 実装する**

`layer1.py` の `LAYER1_WHERE` 定義(現行35〜70行)を§9-13の構成へ置き換える。モジュール冒頭のdocstringへ1文追記(「design §2.2: BASE(人数除く)はグループPool検索・緩和H再検視が使う」)。**他の行(_SELECT_HARD・hard_filter_candidates・HardCandidate等)は無変更**。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/ -v`
Expected: PASS(test_layer_sql.py新規3件含む全件。**既存の test_layer1_where_contains_all_conditions 等も無修正でPASS** — バイト不変の証明)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/layer1.py backend/tests/unit/matching/test_layer_sql.py
git commit -m "feat: layer1の人数行分離(LAYER1_WHERE_BASE・グループPool検索用・文字列不変)"
```

### Task 4: layer4.py のK_j配分拡張(§9-5)

**Files:**
- Modify: `backend/src/latch/worker/matching/layer4.py`
- Test: `backend/tests/unit/matching/test_layer4.py`(追記+機械的追随)

**Interfaces:**
- Consumes: `layer1.LAYER1_WHERE_BASE`(Task 3)・`group_calc.uuid_array_text`(Task 2。layer4は `_COUNT`…不使用。実際のimportは `LAYER1_WHERE_BASE` のみ追加)
- Produces(Task 5〜7・9が使用):
  - `PAIR_KIND_GROUP = "group"`・`GROUP_PAIR_EXISTS`(EXISTS断片)
  - `select_jev_rows` の戻り `JevCandidateRow.pair_kind` が実値('one_on_one' | 'group')・LIMIT撤去
  - `select_jev_targets(rows, new_pair_row_ids=frozenset()) -> list[JevCandidateRow]`
  - `hard_constraint_holds(conn, origin, candidate_id, *, relaxed=False) -> bool`
  - `_H_RECHECK_GROUP`(緩和H再検証SQL)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_layer4.py` へ追記(design §4.1「test_worker_jev.py系へ追加」のselect_jev_targets配分はここでカバー):

```python
# -- K_j配分とグループ拡張(M2 ws-7・design §2.4) --


def _grp_row(n: int) -> JevCandidateRow:
    return _row(n, kind="group")


def test_select_targets_one_on_one_minimum_four_first():
    """規則1: 1対1上位4件を先に確保(グループが高スコアでも)。"""
    rows = [_row(i) for i in range(6)] + [_grp_row(i) for i in range(20, 24)]
    out = select_jev_targets(rows)
    assert [r.row_id for r in out[:4]] == [r.row_id for r in rows[:4]]
    assert len(out) == 8  # 残り4枠はグループ(新規・rows順)


def test_select_targets_continuing_group_preferred_over_new():
    """規則4: 継続(row_id∉new)→新規(∈)の順。各層内は入力順。"""
    new1, new2 = _grp_row(21), _grp_row(22)
    cont1, cont2 = _grp_row(23), _grp_row(24)
    rows = [_row(1), _row(2), _row(3), _row(4), new1, cont1, new2, cont2]
    out = select_jev_targets(rows, frozenset([new1.row_id, new2.row_id]))
    # 上位4=1対1・残り4枠=継続2件が先・次に新規2件
    assert [r.row_id for r in out] == [
        _row(1).row_id, _row(2).row_id, _row(3).row_id, _row(4).row_id,
        cont1.row_id, cont2.row_id, new1.row_id, new2.row_id,
    ]


def test_select_targets_promotes_one_on_one_when_no_group():
    """規則4: グループ不在は1対1へ繰り上げ(実効上限8)。"""
    rows = [_row(i) for i in range(12)]
    out = select_jev_targets(rows)
    assert [r.row_id for r in out] == [r.row_id for r in rows[:8]]


def test_select_targets_minimum_four_kept_with_continuing():
    """1対1最低4は継続評価があっても確保(引用#6規則4-a)。"""
    cont = [_grp_row(i) for i in range(30, 36)]  # 継続6件(継続優先で食い合う)
    rows = [_row(i) for i in range(6)] + cont
    out = select_jev_targets(rows)  # new_pair_row_ids既定=全グループ継続扱い
    assert sum(1 for r in out if r.pair_kind == "one_on_one") >= 4


def test_select_sql_has_is_group_and_no_limit():
    """is_group列追加・LIMIT撤去(配分は純関数側・design §2.4)。"""
    sql = str(layer4._SELECT_JEV_ROWS)
    assert "AS is_group" in sql
    assert layer4.GROUP_PAIR_EXISTS.strip() in sql
    assert "g.intent_ids @> ARRAY[mc.intent_a_id, mc.intent_b_id]" in sql
    assert "LIMIT" not in sql.split("ORDER BY", 1)[1]


def test_group_pair_exists_fragment_pins():
    frag = layer4.GROUP_PAIR_EXISTS
    assert "group_candidates g" in frag
    assert "g.status IN ('candidate', 'proposed')" in frag
    assert "@>" in frag


def test_h_recheck_group_uses_base_and_relaxed_participants():
    sql = str(layer4._H_RECHECK_GROUP)
    assert layer1.LAYER1_WHERE_BASE.strip()[:40] in sql
    assert "i.participants_min <= 4" in sql
    assert "i.participants_max >= 3" in sql
    assert "i.participants_min <= 2" not in sql  # 人数行は含まない


def test_close_broken_excludes_group_pairs():
    sql = str(layer4._CLOSE_BROKEN)
    assert f"AND NOT {layer4.GROUP_PAIR_EXISTS}" in sql


def test_select_jev_rows_builds_pair_kind():
    """select_jev_rowsがis_group列をpair_kindへ変換(実DBはintegration)。"""
    # unitではSQL文字列ピンのみ。ペア種別変換はgroup_engine試験と
    # integrationで実証する。ここでは定数の存在のみ:
    assert layer4.PAIR_KIND_GROUP == "group"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer4.py -v`
Expected: 新規9件FAIL(AttributeError等)。**既存3件(identity_and_cap・keeps_input_order・constants_pin)は実装後に無修正でPASSすること**(1対1のみの入力では恒等+上限8が維持される — §9-5-3の構成が保証)

- [ ] **Step 3: 実装する**

`layer4.py` を§9-5のとおり変更((1)定数2件追加 (2)`_SELECT_JEV_ROWS` のis_group列+LIMIT撤去 (3)`select_jev_rows` の戻り生成へpair_kind実値化(`pair_kind=(PAIR_KIND_GROUP if r[8] else PAIR_KIND_ONE_ON_ONE)`) (4)`select_jev_targets` の配分拡張 (5)`_H_RECHECK_GROUP`+`hard_constraint_holds` のrelaxed (6)`_CLOSE_BROKEN` へ `AND NOT {GROUP_PAIR_EXISTS}`)。docstringのws-7拡張点記述を実装に差し替え・`from latch.worker.matching.layer1 import LAYER1_WHERE, LAYER1_WHERE_BASE` へimport追加。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer4.py tests/unit/test_worker_jev.py -v`
Expected: PASS。test_worker_jev.pyの既存試験が`select_jev_targets`の呼び出し(jev.pyは現行 `layer4.select_jev_targets(rows)`)をそのまま通すか確認 — **jev.pyの変更はTask 5のため、ここでは `_patch_phase1` のスタブ行がpair_kind既定値のまま通ることを確認する**(現行 `select_jev_targets(rows)` シグネチャ互換・既定引数追加は破壊でない)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/layer4.py backend/tests/unit/matching/test_layer4.py
git commit -m "feat: layer4のK_j配分拡張(is_group列・1対1最低4+継続優先・緩和H再検証・close除外)"
```

### Task 5: candidates.upsert_pair + JevWorkerのgroup_ctx拡張(§9-6・§9-7)

**Files:**
- Modify: `backend/src/latch/worker/matching/candidates.py`(upsert_pair追加)
- Modify: `backend/src/latch/worker/jev.py`(handleシグネチャ・relaxed)
- Test: `backend/tests/unit/matching/test_layer_sql.py`(upsert_pairのSQLピン)・`backend/tests/unit/test_worker_jev.py`(追記)

**Interfaces:**
- Consumes: `layer4.PAIR_KIND_GROUP`・`select_jev_targets(rows, new_pair_row_ids)`(Task 4)
- Produces(Task 7が使用):
  - `candidates.upsert_pair(conn, *, intent_a_id, intent_b_id, intent_a_version, intent_b_version, similarity, cheap_score, now) -> uuid.UUID`
  - `JevWorker.handle(intent_id, group_ctx=None)`(group_ctxは `GroupEngine.GroupContext`・`new_pair_row_ids: frozenset[uuid.UUID]` 属性)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_layer_sql.py` へ追記:

```python
# -- candidates.upsert_pair(M2 ws-7・design §2.3) --

_PAIR_KEYS = (
    "intent_a_id",
    "intent_b_id",
    "intent_a_version",
    "intent_b_version",
    "retrieval_score",
    "cheap_score",
    "now",
)


def test_upsert_pair_pins_on_conflict_and_returning():
    sql = str(candidates._UPSERT_PAIR)
    assert (
        "ON CONFLICT (intent_a_id, intent_b_id,"
        " intent_a_version, intent_b_version)" in sql
    )
    assert "DO UPDATE SET" in sql
    assert "RETURNING id" in sql
    assert "'pending'" in sql.split("DO UPDATE", 1)[0]


def test_upsert_pair_do_update_touches_scores_only():
    sql = str(candidates._UPSERT_PAIR)
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "status" not in update_clause
    assert "cheap_judge_score = EXCLUDED.cheap_judge_score" in update_clause


def test_upsert_pair_all_bind_params_recognized():
    compiled = str(candidates._UPSERT_PAIR.compile(dialect=postgresql.dialect()))
    for key in _PAIR_KEYS:
        assert f":{key}" not in compiled, key
```

`backend/tests/unit/test_worker_jev.py` へ追記:

```python
# -- group_ctx拡張(M2 ws-7・design §2.4) --


async def test_handle_default_group_ctx_is_backward_compatible(redis, monkeypatch):
    """group_ctx省略(既定None)は全グループペアを継続扱い・既存経路不変。"""
    org = _origin()
    rows = [_row(1), _row(2)]
    _patch_phase1(monkeypatch, org=org, rows=rows, origin_row=_jev_row())
    h_calls = _patch_eval(monkeypatch)
    _patch_writes(monkeypatch)
    recorded: list = []

    async def fake_targets(rows_in, new_pair_row_ids=frozenset()):
        recorded.append((tuple(r.row_id for r in rows_in), new_pair_row_ids))
        return list(rows_in)

    monkeypatch.setattr(jev_mod.layer4, "select_jev_targets", fake_targets)
    worker = _make_worker(_FakeGateway(_judgment()), _FakeGuard(), _recording_store())
    await worker.handle(_uid(1))
    assert recorded[0][1] == frozenset()  # None→frozenset


async def test_handle_passes_new_pair_row_ids_to_targets(redis, monkeypatch):
    """group_ctx.new_pair_row_idsがselect_jev_targetsへ流れる(§2.4)。"""

    class _Ctx:
        new_pair_row_ids = frozenset({_uid(50)})

    org = _origin()
    _patch_phase1(monkeypatch, org=org, rows=[_row(1)], origin_row=_jev_row())
    _patch_eval(monkeypatch)
    _patch_writes(monkeypatch)
    recorded: list = []

    async def fake_targets(rows_in, new_pair_row_ids=frozenset()):
        recorded.append(new_pair_row_ids)
        return list(rows_in)

    monkeypatch.setattr(jev_mod.layer4, "select_jev_targets", fake_targets)
    worker = _make_worker(_FakeGateway(_judgment()), _FakeGuard(), _recording_store())
    await worker.handle(_uid(1), _Ctx())
    assert recorded[0] == frozenset({_uid(50)})


async def test_group_pair_uses_relaxed_h_recheck(redis, monkeypatch):
    """pair_kind='group'の行はH再検証へrelaxed=True(§2.4)。"""
    import dataclasses

    org = _origin()
    grp = dataclasses.replace(_row(1), pair_kind="group")
    _patch_phase1(monkeypatch, org=org, rows=[grp], origin_row=_jev_row())
    relaxed_calls: list = []

    async def fake_h(conn, origin, candidate_id, *, relaxed=False):
        relaxed_calls.append(relaxed)
        return True

    async def fake_read_peer(engine, pid):
        return _jev_row()

    monkeypatch.setattr(jev_mod.layer4, "hard_constraint_holds", fake_h)
    monkeypatch.setattr(jev_mod, "_read_peer", fake_read_peer)
    _patch_writes(monkeypatch)
    worker = _make_worker(_FakeGateway(_judgment()), _FakeGuard(), _recording_store())
    await worker.handle(_uid(1))
    assert relaxed_calls == [True]
```

※`_recording_store` は既存fixture/ヘルパーの `JevCostStore(fakeredis)` ラッパ(`_RecordingCostStore`)。既存試験の作り(`test_success_path_writes_jev_result_and_counts`)を参照し同一の組立を使う(fakeredis fixtureはguard/cost_store構築に必要なため残す)。

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer_sql.py tests/unit/test_worker_jev.py -v`
Expected: 新規6件FAIL(upsert_pairなし・group_ctx KwArgumentError等)

- [ ] **Step 3: 実装する**

(1) `candidates.py` へ `_UPSERT_PAIR`・`_coerce_uuid`・`upsert_pair` を§9-7どおり追加。docstringへ「ws-7: グループのメンバー間ペア用」を追記。
(2) `jev.py` を§9-6どおり変更(handleシグネチャ・選択ループ・`_evaluate` のrelaxed)。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer_sql.py tests/unit/test_worker_jev.py tests/unit/matching/test_layer4.py -v`
Expected: PASS(既存のtest_worker_jev.py全試験も無修正で通る)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/candidates.py backend/src/latch/worker/jev.py backend/tests/unit/matching/test_layer_sql.py backend/tests/unit/test_worker_jev.py
git commit -m "feat: upsert_pair(ID直指定UPSERT)とJevWorkerのgroup_ctx受け渡し"
```

### Task 6: latch_engine.py の共存改修・I-1改修(§9-8)

**Files:**
- Modify: `backend/src/latch/worker/matching/latch_engine.py`
- Test: `backend/tests/unit/matching/test_latch_engine.py`(追記+機械的追随)

**Interfaces:**
- Consumes: `layer4.GROUP_PAIR_EXISTS`(Task 4)・`group_calc.uuid_array_text`/`dominates`(Task 2)・`latch_calc`(既存)
- Produces(Task 8が使用):
  - `try_promote(latch_id)`(public・GroupEngine.finalizeから呼ぶ)
  - `_read_participants(conn, intent_ids: list[uuid.UUID])`(idsリスト)
  - `_SELECT_LATCH_FOR_UPDATE` が6列(id, status, intent_ids, expires_at, group_candidate_id, score)
  - `_has_higher_group_latch(conn, self_id, self_ids, self_score) -> bool`
  - `_evaluate_pair` は「tx前読取→tx1統合」構成(観測結果は不変)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_latch_engine.py` へ追記(冒頭へ `from latch.worker.matching import layer4` のimport追加・必要に応じて `from decimal import Decimal` も):

```python
# -- グループ共存改修・I-1改修(M2 ws-7・design §2.6〜2.7) --


def test_select_targets_excludes_group_pairs():
    """グループ所属ペアはlatch_score計算対象外(design §2.7-1)。"""
    sql = str(le._SELECT_TARGETS)
    assert f"AND NOT {layer4.GROUP_PAIR_EXISTS}" in sql


def test_count_daily_notifications_uses_any_uuid_array():
    """|S|人対応: user_id = ANY(:users)(design §2.7-2)。"""
    sql = str(le._COUNT_DAILY_NOTIFICATIONS)
    assert "user_id = ANY(CAST(:users AS uuid[]))" in sql
    assert "IN (CAST(:u0" not in sql


def test_select_latch_for_update_carries_group_columns():
    sql = str(le._SELECT_LATCH_FOR_UPDATE)
    assert "group_candidate_id" in sql
    assert ", score" in sql


def test_higher_group_latch_sql_pins():
    sql = str(le._SELECT_HIGHER_GROUP_LATCH)
    assert "l.group_candidate_id IS NOT NULL" in sql
    assert "l.intent_ids && CAST(:my_ids AS uuid[])" in sql
    assert "l.id <> CAST(:self AS uuid)" in sql


async def test_try_promote_skips_when_higher_group_latch_exists(monkeypatch):
    """D-06: メンバーが重なる上位集合があればproposed化しない(design §2.6)。"""

    class _Conn:  # _has_higher_group_latchがTrueを返すスタブ
        async def execute(self, stmt, params=None):
            class _R:
                def fetchall(self):
                    return [(Decimal("0.9"), [_uid(1), _uid(2), _uid(9)])]

            return _R()

    assert await le._has_higher_group_latch(
        _Conn(), _uid(5), [_uid(1), _uid(2), _uid(3)], 0.85
    ) is True  # 0.9 > 0.85で上位

    # try_promote本体内: 上位あり→_promote_latch呼ばれない
    log = _patch_for_promote(monkeypatch, group_candidate_id=_uid(80),
                             score=Decimal("0.85"), higher=True)
    await _engine().try_promote(NEW_LATCH_ID)
    assert log["promote_latch"] == []
    assert log["events"] == []  # proposed遷移イベントなし(candidateのまま)


async def test_try_promote_promotes_group_when_no_higher(monkeypatch):
    """上位なし(または1対1行)は従来どおりproposed化。"""
    log = _patch_for_promote(monkeypatch, group_candidate_id=_uid(80),
                             score=Decimal("0.85"), higher=False)
    await _engine().try_promote(NEW_LATCH_ID)
    assert log["promote_latch"] == [NEW_LATCH_ID]
    assert ("candidate", "proposed") in [(e[1], e[2]) for e in log["events"]]


async def test_try_promote_one_on_one_skips_d06_check(monkeypatch):
    """group_candidate_id NULL(1対1)はD-06チェックを行わない。"""
    log = _patch_for_promote(monkeypatch, group_candidate_id=None,
                             score=Decimal("0.85"), higher=True)
    await _engine().try_promote(NEW_LATCH_ID)
    assert log["promote_latch"] == [NEW_LATCH_ID]  # higher=Trueでも昇格


async def test_evaluate_pair_missing_peer_inputs_keeps_score_null(monkeypatch):
    """I-1改修: peer入力欠損→_record_scoreを呼ばない(latch_score NULL維持)。"""
    log = _patch(
        monkeypatch,
        org=_origin(),
        rows=[_row(1)],
        inputs={_uid(1): _inputs(1)},  # peer(_uid(101))の入力なし
    )
    await _engine().handle(_uid(1))
    assert log["record"] == []  # 計算だけ成功させて生成物なし、を作らない
    assert log["insert"] == []


async def test_evaluate_pair_completes_after_peer_inputs_restored(monkeypatch):
    """復旧後の再handleでlatches生成まで完走(I-1改修の回収経路)。"""
    org = _origin()
    inputs = {_uid(1): _inputs(1), _uid(101): _inputs(101)}
    # 1回目: peer欠損
    log1 = _patch(monkeypatch, org=org, rows=[_row(1)],
                  inputs={_uid(1): _inputs(1)})
    await _engine().handle(_uid(1))
    assert log1["record"] == []
    # 2回目: 完全な入力(latch_score IS NULLガードで再選択される)
    log2 = _patch(monkeypatch, org=org, rows=[_row(1, wa=0.9, wb=0.85)],
                  inputs=inputs)
    await _engine().handle(_uid(1))
    assert [c[1] for c in log2["record"]] == [0.85]  # LATCH_C×min(0.9,0.85)
    assert log2["insert"]  # latches生成
    assert (None, "candidate") in [(e[0], e[1]) for e in log2["events"]]
```

`_patch_for_promote` ヘルパーを同ファイルへ追加(`_patch` のtry_promote系スタブ拡張版。`_select_latch_for_update` が `(latch_id, "candidate", ids, expires, group_candidate_id, score)` を返すスタブ・`_has_higher_group_latch` が `higher` 引数値を返すスタブ・`_promote_latch`・`_insert_latch_event`・`_insert_notification`・`_count_daily_notifications`・`_count_open_proposed`・`_read_participants` を記録スタブへ差し替える。`_read_participants` の戻りは3要素idsならParticipant 3件)。**既存 `_patch` の `fake_promote`(monkeypatch.setattr(le.LatchEngine, "_try_promote", …))は `try_promote` へ改名**する(機械的追随・既存試験は呼び出し記録のキー名以外影響なし)。

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py -v`
Expected: 新規9件FAIL。既存試験も `_try_promote` 参照スタブ等で一時FAILになる可能性 → Step 3で機械的追随と一緒に直す

- [ ] **Step 3: 実装する**

`latch_engine.py` を§9-8の(1)〜(5)どおり変更。要点: (1)`_SELECT_TARGETS` へ除外1行 (2)`_COUNT_DAILY_NOTIFICATIONS` ANY化+`_count_daily_notifications` のparams組立変更+`group_calc` import (3)`_read_participants` ids化+`try_promote` 内の呼び出しと `len(parts) < len(row[2])` (4)`_SELECT_LATCH_FOR_UPDATE` 2列追加+`_select_latch_for_update` 戻り拡張+`_SELECT_HIGHER_GROUP_LATCH`/`_has_higher_group_latch` 新設+try_promote内チェック挿入+`_try_promote`→`try_promote` 改名(内部3箇所) (5)`_evaluate_pair` のtx統合(§9-8-5の全文)+`_nearby_path`→`_nearby_in_tx`・`_proposal_path` 削除。docstring更新(ws-7拡張点を実装に差し替え)。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/ tests/unit/test_worker_reeval.py -v`
Expected: PASS(既存のtest_latch_engine.py試験は観測結果不変のため、スタブのキー名・シグネチャ追随のみで通る。`_read_intent_inputs` 系の既存試験で「record後にinputsを読む」順序を前提としたものがあれば期待値を機械的追随させる — 変更内容を報告書へ記録)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/latch_engine.py backend/tests/unit/matching/test_latch_engine.py
git commit -m "feat: latch_engine共存改修(グループ除外・|S|人try_promote・D-06上位1)とI-1 tx統合"
```

### Task 7: group_engine.py 前半(GroupEngine.handle・Pool・生成)

**Files:**
- Create: `backend/src/latch/worker/matching/group_engine.py`(このタスクでhandleまで。finalizeはTask 8)
- Test: `backend/tests/unit/matching/test_group_engine.py`(作成)

**Interfaces:**
- Consumes: `group_calc`(Task 2)・`layer1.LAYER1_WHERE_BASE`(Task 3)・`layer3.rule_score`/`vocab_overlap`/`cheap_score`/`soft_texts`・`candidates.upsert_pair`(Task 5)・`latch_calc.bucket_start`・`origin.Origin`/`bind_params`
- Produces(Task 8〜9が使用):
  - `GroupEngine(engine=…, clock=…, geo=None, latch=None)`
  - `GroupContext(group_ids: frozenset[uuid.UUID], new_pair_row_ids: frozenset[uuid.UUID])`
  - `GroupEngine.handle(intent_id) -> GroupContext | None`
  - `load_group_origin(conn, clock, intent_id) -> OriginLoad`(skip理由はorigin.pyと同一+`SKIP_GROUP_ORIGIN_MAX = "origin_not_group"`)
  - SQL定数 `_POOL_SEARCH`/`_PAIR_COMPAT`/`_INSERT_GROUP`/`_SELECT_GROUP_VERSIONS`(§9-3)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_group_engine.py` を作成(test_worker_jev.pyと同型・design §4.1)。骨子:

```python
"""GroupEngine.handle/finalizeのunit試験(design §4.1)。DB操作はgroup_engineの
モジュール関数をmonkeypatchして分岐ロジックを検証(runnerと同一規律)。
SQL文字列はtext()定数への直接ピンで検証する。"""

import uuid
from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.worker.matching import group_engine as ge
from latch.worker.matching.group_engine import GroupEngine
from latch.worker.matching.origin import Origin, OriginLoad

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
START = NOW + timedelta(hours=30)  # 将来窓(75分ルール回避)


def _uid(n): return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


def _origin(n=1, pmin=2, pmax=4):
    return Origin(
        intent_id=_uid(n), version=1, user_id=_uid(n + 1),
        category_primary="drinking", alcohol_involved=False, budget_max=5000,
        participants_min=pmin, participants_max=pmax,
        geo_lon=130.55, geo_lat=31.59, geo_radius_m=2000,
        time_start=START, time_end=START + timedelta(hours=3),
        embedding="[0.1, 0.2]", soft_texts=(), user_ge_20=True,
        evaluated_at=NOW,
    )


def _pool_row(n, *, pmin=2, pmax=4, similarity=0.9, cheap=0.8):
    """_POOL_SEARCH戻り相当(namedtupleかタプル・§9-4手順2のSELECT列順)。"""
    ...


class _FakeEngine:
    def begin(self): return _FakeTx()

class _FakeTx:
    async def __aenter__(self): return object()
    async def __aexit__(self, *exc): return False


from typing import NamedTuple


class _PoolRow(NamedTuple):
    """_POOL_SEARCH戻り相当(§9-4手順2のSELECT列順)。"""

    intent_id: uuid.UUID
    version: int
    user_id: uuid.UUID
    participants_min: int
    participants_max: int
    time_start: datetime
    budget_max: int | None
    structured_data: dict
    similarity: float


def _pool_row(n, *, pmin=2, pmax=4, similarity=0.9, budget=5000):
    return _PoolRow(
        intent_id=_uid(n), version=1, user_id=_uid(1000 + n),
        participants_min=pmin, participants_max=pmax,
        time_start=START, budget_max=budget,
        structured_data={
            "location_name": "天文館",
            "soft_constraints": [{"text": "静かな場所",
                                  "downgraded_from_ng": False}],
        },
        similarity=similarity,
    )
```

試験(design §4.1のGroupEngine分+Review Focus 2):

| 試験 | 検証内容 |
|---|---|
| `test_origin_not_found_noop` | `load_group_origin` がskip(不在)→handleはNone・Pool検索しない |
| `test_origin_max_lt_3_noop` | 起点(max=2)→`SKIP_GROUP_ORIGIN_MAX` でno-op(承認事項2のトリガー) |
| `test_min3_origin_passes_origin_gate` | **min=3/max=4の起点がskipされない**(Review Focus 2・load_group_origin直呼びでOriginが返る) |
| `test_empty_pool_noop` | Pool 0件→None・「group pool empty」経路 |
| `test_pool_search_sql_pins` | `_POOL_SEARCH` に `LAYER1_WHERE_BASE`・`i.participants_min <= 4`・`i.participants_max >= 3`・Bucket条件(`>= CAST(:bucket_start AS timestamptz)` と `< CAST(:bucket_end AS timestamptz)`)・`LIMIT 50`・距離ASC+id ASCのORDER BY・bind全認識(postgresql dialect compile) |
| `test_pair_compat_sql_pins` | `_PAIR_COMPAT` に `i1.id < i2.id`・`ST_DWithin`・`i1.user_id <> i2.user_id`・blocks NOT EXISTS・飲酒年齢(EXTRACTが2つ)・`ANY(CAST(:ids AS uuid[]))` が2箇所 |
| `test_insert_group_sql_pins` | `_INSERT_GROUP` にON CONFLICT (intent_ids) WHERE status IN ('candidate','proposed') DO NOTHING・RETURNING id・status='candidate' |
| `test_pool_top15_cheap_desc_intent_id_asc` | スタブ検索行20件(small差のcheap・同点)→Pool 15件・cheap降順・同点intent_id昇順・`group pool built` のログ(caplog) |
| `test_handle_builds_group_with_seed_origin` | 互換スタブ(全ペア互換)→集合{起点,2,3}・`_INSERT_GROUP` 呼び出しのmember_scoresが `{"seed_id": 起点id, "versions": {id: 1,…}}`・intent_idsはsorted正規化 |
| `test_handle_upserts_pool_pairs_then_member_pairs` | upsert_pair呼び出しは**Pool全員×起点**(手順4b・Pool由来similarity/cheap)の後に**集合のメンバー間ペア**(手順7-d・起点を含まない。3人集合{1,2,3}なら4b=Pool分行+7-d={2,3}の1行) |
| `test_handle_duplicate_group_skipped` | `_INSERT_GROUP` がNone→スキップ・GroupContextは空・「group duplicate skipped」ログ |
| `test_handle_user_conflict_skips_insert` | `_SELECT_GROUP_VERSIONS` が同一user_id×2→INSERT前にスキップ(05 §2) |
| `test_group_context_contents` | GroupContext.group_ids・new_pair_row_ids(upsert_pairの戻りid集合) |

`_patch_group` ヘルパー:`load_group_origin`・`_pool_search`・`_pair_compat`・`candidates.upsert_pair`(ge.candidates経由)・`_insert_group`・`_select_group_versions` を記録スタブへ差し替える(test_worker_jev.pyの`_patch_phase1`と同型)。

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_group_engine.py -v`
Expected: FAIL(ModuleNotFoundError: group_engine)

- [ ] **Step 3: 実装する**

`group_engine.py` を§9-3(SQL全文)+§9-4(handle手順)どおり作成。このタスクの実装範囲: モジュールdocstring・import・SQL定数(§9-3全文)・`load_group_origin`・`GroupContext`・`_PoolRow` 系の内部dataclass・`GroupEngine.__init__`/`handle`(§9-4手順1〜8・手順4bのPool由来ペアUPSERTを含む)・Pool構築のcheap_score計算(手順3)・互換行列の `pair_info` 組立(手順5)。**finalizeはTask 8で追記**(この段階では定義しない — main.py配線はTask 9)。層3・candidates・latch_calcはモジュール属性経由(`layer3`・`candidates`・`latch_calc`)で呼ぶ(unit試験がmonkeypatch可能・runner規律)。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_group_engine.py -v`
Expected: PASS(全試験)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/group_engine.py backend/tests/unit/matching/test_group_engine.py
git commit -m "feat: GroupEngine.handle(人数緩和Pool検索・互換行列・貪欲法による集合生成)"
```

### Task 8: proposal.build_group_proposal + GroupEngine.finalize(§9-9・§9-4)

**Files:**
- Modify: `backend/src/latch/worker/matching/proposal.py`(build_group_proposal追加)
- Modify: `backend/src/latch/worker/matching/group_engine.py`(finalize追記+集約系SQL)
- Test: `backend/tests/unit/matching/test_proposal.py`(追記)・`backend/tests/unit/matching/test_group_engine.py`(追記)

**Interfaces:**
- Consumes: `group_calc.group_target_time`・`latch_calc.LATCH_THRESHOLD`/`d07_allows`/`d07_history_inputs`/`response_deadline`(既存)・`LatchEngine.try_promote`(Task 6)・`proposal.LatchIntentInputs`(既存)
- Produces(Task 9が使用):
  - `proposal.build_group_proposal(*, members: list[LatchIntentInputs], score: float, area_name: str | None) -> dict`
  - `GroupEngine.finalize(intent_id) -> None`
  - SQL定数 `_SELECT_PENDING_GROUPS`/`_SELECT_GROUP_PAIRS`/`_RESET_GENERATION`/`_UPDATE_AGGREGATE`/`_INSERT_GROUP_LATCH`/`_FIND_OPEN_GROUP_LATCH`/`_SELECT_GROUP_LATCH_RESPONSES`/`_MARK_GROUP_PROPOSED`/`_CLOSE_GROUP_BY_ID`/`_UPDATE_GROUP_LATCH_FOR_PROMOTION`(§9-3)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_proposal.py` へ追記:

```python
# -- build_group_proposal(M2 ws-7・05 §2・引用#14) --


def _m(n: int, **over) -> LatchIntentInputs:
    base = dict(
        intent_id=_uid(n), user_id=_uid(n + 50), visibility="summary_only",
        notification_level="proposals_only",
        time_start=NOW + timedelta(hours=30),
        expires_at=NOW + timedelta(days=5), budget_max=5000,
        category_primary="drinking", category_secondary="ビアバー",
        geo_lon=130.558, geo_lat=31.596,
    )
    base.update(over)
    return LatchIntentInputs(**base)


def test_group_proposal_full_fields_when_all_summary_only():
    members = [_m(1), _m(2, budget_max=3000), _m(3, time_start=NOW + timedelta(hours=40))]
    p = build_group_proposal(members=members, score=0.85, area_name="天文館")
    assert p["headcount"] == 3
    assert p["match_level"] == "medium"
    assert p["budget"] == {"max": 3000}  # min(budget_max)・NULL無視
    assert p["category_secondary"] == "ビアバー"  # 種(members[0])の値
    assert p["area_name"] == "天文館"
    # time_summaryはmax(time_start)のJST書式(ws-6 §2.5と同一)


def test_group_proposal_hidden_until_match_minimal():
    members = [_m(1), _m(2, visibility="hidden_until_match")]
    p = build_group_proposal(members=members, score=0.92, area_name=None)
    assert set(p) == {"headcount", "match_level"}
    assert p["headcount"] == 2


def test_group_proposal_null_budgets_yield_none():
    members = [_m(1, budget_max=None), _m(2, budget_max=None), _m(3, budget_max=None)]
    p = build_group_proposal(members=members, score=0.81, area_name=None)
    assert p["budget"] is None
```

`backend/tests/unit/matching/test_group_engine.py` へ追記(design §4.1のfinalize分+Review Focus 4・5):

| 試験 | 検証内容 |
|---|---|
| `test_finalize_origin_noop` | 起点skip→何もしない |
| `test_finalize_generation_reset_when_versions_diverge` | member_scores.versions不一致→`_RESET_GENERATION` 呼び出し(versions=現行組・aggregate/prevはNULLに)→引き続き現行組で判定(Review Focus 5) |
| `test_finalize_incomplete_pairs_do_nothing` | 現行version組の全ペアのうちjev_result無し→INSERTもUPDATEも呼ばれない(status=candidate保持・引用#4) |
| `test_finalize_h_broken_closes_group` | 人数包含不合格(min=5メンバー等)または互換欠落→`_CLOSE_GROUP_BY_ID` のみ・ペア行closeなし(design §2.5手順3) |
| `test_finalize_aggregate_and_latch_in_same_tx` | 全ペア揃い・score>=0.80→`_UPDATE_AGGREGATE`→d07→`_INSERT_GROUP_LATCH`→イベント→`_MARK_GROUP_PROPOSED` が**同一conn(tx)内**でこの順(スタブconnの呼出順リストで検証・design §4.1) |
| `test_finalize_threshold_boundary_080` | mutualsのmin=0.80ちょうど→提案(score >= LATCH_THRESHOLDは等号付き) |
| `test_finalize_below_threshold_keeps_candidate` | score<0.80→`_UPDATE_AGGREGATE` のみ・latches INSERTなし(candidateのまま・aggregateは入る) |
| `test_finalize_d07_denied` | d07_allowsがFalse(stub)→`_UPDATE_AGGREGATE` のみ・INSERTなし・「group d07 denied」ログ |
| `test_finalize_calls_try_promote` | INSERT成功→`latch.try_promote(latch_id)` 呼び出し(latchスタブ) |
| `test_finalize_read_failure_leaves_aggregate_null` | `_read_member_inputs` がNone(expires NULLメンバー)→`_UPDATE_AGGREGATE` 呼ばれず(aggregate NULLのまま・次の評価処理で再選択 — I-1対策・design §2.9の表) |
| `test_finalize_aggregate_guard_skips_computed` | `aggregate_score` 計算済み行→`_UPDATE_AGGREGATE` の戻り0行→何もしない(冪等) |
| `test_insert_group_latch_sql_pins` | ON CONFLICT (intent_ids) WHERE … DO NOTHING・group_candidate_id列・RETURNING id |

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_proposal.py tests/unit/matching/test_group_engine.py -v`
Expected: 新規FAIL(build_group_proposalなし・finalizeなし)

- [ ] **Step 3: 実装する**

(1) `proposal.py` へ§9-9の `build_group_proposal` を追加。
(2) `group_engine.py` へ§9-3の集約系SQL・`_read_member_inputs`・`GroupEngine.finalize`(§9-4手順)・`_area_name`(全メンバーgeo平均点: `sum(lon)/n`, `sum(lat)/n` を `self._geo.reverse_geocode` へ・geo NoneならNone)を追記。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/ -v`
Expected: PASS(全matching系unit)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/proposal.py backend/src/latch/worker/matching/group_engine.py backend/tests/unit/matching/test_proposal.py backend/tests/unit/matching/test_group_engine.py
git commit -m "feat: GroupEngine.finalize(集約tx・I-1対策)とbuild_group_proposal"
```

### Task 9: stage1._CLOSE_GROUPS + main.py 配線(§9-10・§9-11)

**Files:**
- Modify: `backend/src/latch/worker/stage1.py`
- Modify: `backend/src/latch/worker/main.py`
- Test: `backend/tests/unit/test_worker_stage1.py`(追記)・`backend/tests/unit/test_worker.py`(機械的追随+追記)

**Interfaces:**
- Consumes: `GroupEngine`(Task 7・8)・`JevWorker.handle(intent_id, group_ctx)`(Task 5)
- Produces: `Worker._run_post_retrieval(intent_id)`(共通チェーン)・`Worker(group=…)` DI・stage1の削除Eventでgroup_candidates無効化

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_stage1.py` へ追記(既存 `test_process_deleted_closes_candidates` のScriptedConn流儀でSQL文字列を記録):

```python
async def test_process_deleted_closes_group_candidates():
    """deleted → group_candidatesの無効化SQL(status='closed'・06 §1・design §2.7-5)。"""
    # 既存のdeleted試験と同じScriptedEngine構成。実行されたSQLに
    # _CLOSE_GROUPS(group_candidates / ANY(intent_ids) / 'closed')が含まれ
    # _CLOSE_CANDIDATES(match_candidates)の後に実行されることを検証
```

(既存 `test_process_deleted_closes_candidates` をコピーし、記録したSQL列へ group_candidates のUPDATEが含まれること・順序が match_candidates→group_candidates であることを追加検証する形で実装)

`backend/tests/unit/test_worker.py` へ追記・修正:

```python
# -- 配線(M2 ws-7)。_run_post_retrieval共通チェーン(design §2.1案A) --


class _RecordingGroup:
    """GroupEngineスタブ(handle/finalizeの呼び出しを記録・handleは空ctx)。"""

    def __init__(self):
        self.handles: list[uuid.UUID] = []
        self.finalizes: list[uuid.UUID] = []

    async def handle(self, intent_id):
        self.handles.append(intent_id)
        return None

    async def finalize(self, intent_id):
        self.finalizes.append(intent_id)
```

- 既存 `_RecordingJev`・`_Ordered` の `handle` シグネチャへ `group_ctx=None` を追加(機械的追随)
- 新規試験(test_worker.pyへ。`_Ordered` 系スタブと `_started_worker_with_latch` 相当の構成を流用):

```python
async def test_kick_jev_chain_group_jev_latch_finalize(fake_clock):
    """embedding_completed → group.handle→jev→latch→group.finalizeの順(§2.1案A)。"""
    order: list[str] = []

    class _Chain:
        def __init__(self, tag: str):
            self._tag = tag
            self.calls: list[uuid.UUID] = []

        async def handle(self, intent_id, group_ctx=None):
            self.calls.append(intent_id)
            order.append(self._tag)
            return None

        async def finalize(self, intent_id):
            self.calls.append(intent_id)
            order.append(f"{self._tag}!")
            return None

    group = _Chain("group")
    jev = _Chain("jev")
    latch = _Chain("latch")
    stage1 = _RecordingStage1("processed")
    worker = Worker(
        clock=fake_clock, bus=_FakeBus(), stage1=stage1,
        jev=jev, latch=latch, group=group,
    )
    task = asyncio.create_task(worker.run())
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert order == ["group", "jev", "latch", "group!"]
        assert group.calls == [iid, iid]  # handleとfinalize
    finally:
        await _stop(worker, task)


async def test_kick_jev_group_not_injected_noop(fake_clock):
    """group未注入(ws-6資産の試験)はjev→latchのみ・例外なし。"""
    jev = _RecordingJev()
    latch = _RecordingLatch()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_latch(fake_clock, stage1, jev, latch)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert jev.calls == [iid] and latch.calls == [iid]
    finally:
        await _stop(worker, task)


async def test_kick_jev_passes_group_ctx_to_jev(fake_clock):
    """group.handleの戻り値がjev.handleの第2引数へ渡る。"""
    marker = object()

    class _CtxGroup:
        async def handle(self, intent_id):
            return marker

        async def finalize(self, intent_id):
            return None

    seen: list = []

    class _Jev:
        async def handle(self, intent_id, group_ctx=None):
            seen.append(group_ctx)

    stage1 = _RecordingStage1("processed")
    worker = Worker(
        clock=fake_clock, bus=_FakeBus(), stage1=stage1,
        jev=_Jev(), latch=None, group=_CtxGroup(),
    )
    task = asyncio.create_task(worker.run())
    try:
        await worker._dispatch(
            _make_event("embedding_completed", uuid.uuid4(), 1)
        )
        assert seen == [marker]
    finally:
        await _stop(worker, task)


async def test_run_direct_pipeline_uses_post_retrieval(fake_clock, monkeypatch):
    """直接投入: L1〜3tx→共通チェーン(group→jev→latch→finalize)。"""
    from latch.worker import main as main_mod

    order: list[str] = []

    async def fake_retrieval(conn, clock, intent_id):
        order.append("l123")

    monkeypatch.setattr(main_mod, "run_candidate_retrieval", fake_retrieval)

    class _C:
        def __init__(self, tag):
            self._tag = tag

        async def handle(self, intent_id, group_ctx=None):
            order.append(self._tag)
            return None

        async def finalize(self, intent_id):
            order.append(f"{self._tag}!")

    worker = Worker(
        clock=fake_clock, bus=_FakeBus(), stage1=_RecordingStage1("processed"),
        jev=_C("jev"), latch=_C("latch"), group=_C("group"),
    )
    await worker._run_direct_pipeline(_FakePipelineEngine(), uuid.uuid4())
    assert order == ["l123", "group", "jev", "latch", "group!"]
```

(既存 `_RecordingStage1`・`_make_event`・`_stop`・`_FakePipelineEngine` はtest_worker.pyの既存資産をそのまま使う。`Worker.__init__` の `group=` はTask 9 Step 3で追加)

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_stage1.py tests/unit/test_worker.py -v`
Expected: 新規FAIL・test_worker.pyの既存試験はgroup未注入で無傷のはず(通らなければ機械的追随)

- [ ] **Step 3: 実装する**

(1) `stage1.py` へ§9-10どおり `_CLOSE_GROUPS` と削除分岐へ1行追加。
(2) `main.py` へ§9-11どおり(import・`__init__` のgroup引数・run()内DI・`_run_post_retrieval` 新設・`_kick_jev`/`_run_direct_pipeline` の本文置換)。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_stage1.py tests/unit/test_worker.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/stage1.py backend/src/latch/worker/main.py backend/tests/unit/test_worker_stage1.py backend/tests/unit/test_worker.py
git commit -m "feat: 削除Eventのgroup_candidates無効化と_run_post_retrieval共通チェーン配線"
```

### Task 10: integration試験作成・完了条件検証・報告書

**Files:**
- Create: `backend/tests/integration/test_matching_groupengine.py`(作成のみ・**実行は§0により禁止**)
- Create: `docs/plans/M2/ws-7-report.md`

**Interfaces:**
- Consumes: Task 1〜9の全部品・conftest既存fixture(`db_engine`/`redis_client`/`api_client`)・test_matching_latchengine.pyの流儀(fixture `field`・`_user`・`_intent`・`_embed`・`_seed_jev`・FakeClock)

- [ ] **Step 1: integration試験ファイルを作成する**

`backend/tests/integration/test_matching_groupengine.py` を作成。**test_matching_latchengine.py の構成を踏襲**(design §4.2の対抗策: 時間窓分離・teardown完全性・prefix掃除)。docstring冒頭に収束10試験である旨と「実行はスーパーバイザー検証時」を明記。**teardown順は design §4 のとおり latch_status_events→notifications→latches→group_candidates→match_candidates→match_events→intents→users**(group_candidatesがlatchesのFK元のためlatchesの後・match_candidatesの前)。

Fixture・ヘルパー:

- `SUBJECT_PREFIX = "m2ws7-"`・Redis prefix `ws7-`・`CATEGORY = "meal"`・`BASE_HOURS = 120`
- `_structured` へ `participants: tuple[int, int] | None = None` 引数を追加(Noneなら従来どおり省略・指定なら `"participants": {"min": p[0], "max": p[1]}` をstructuredへ追加)
- `_group_engine(db_engine, clock, latch)` = `GroupEngine(engine=db_engine, clock=clock, geo=GeoService(db_engine), latch=LatchEngine(engine=db_engine, clock=clock, geo=GeoService(db_engine)))`
- JevWorker実行は **スタブGateway**(test_matching_jev.pyの `_FakeGateway`/スタブgateway流儀を参照して同一構成)・`JevCostGuard`/`JevCostStore` はredis_clientから構築

10試験(design §4.2・§5-12の推論確認を含む):

```python
async def test_1_three_member_group_e2e(api_client, db_engine, field, redis_sweep):
    """3人集合E2E(引用#18の下地): 生成→K_j配分→除外→集約→提案。"""
    # (1) ユーザー3名・Intent3件: 種A(min2max4)+B(min2max4)+C(min2max4)を同一Bucket
    #     (同一時刻・now+5日窓)・近接geo・同一カテゴリで作成・embedding直入れ(E1同一ベクトル)
    # (2) GroupEngine.handle(A) → group_candidates(candidate・intent_ids=sorted3件
    #     ・member_scores.versions)・match_candidates 3ペア(pending)
    # (3) JevWorker.handle(A, group_ctx) → K_j配分(1対1上位4+グループ残り)で
    #     3ペアすべてjev_result(stub 0.9/0.85等)・GroupEngine.finalize(A)
    # (4) 検証: aggregate_score = min(MutualScore) = 0.85・latches(candidate→
    #     try_promoteでproposed)・group_candidate_id紐付・latch_status_events
    #     (candidate・proposed)・notifications 3行(type='proposal')・
    #     proposal(headcount=3・種Aのsecondary・min budget)・
    #     グループペア行のlatch_score IS NULL(1対1除外)

async def test_2_pool_limit_15_recorded(api_client, db_engine, field):
    """Pool上限15(引用#16): 密集20件→Pool 15・cheap降順・同点intent_id昇順。"""
    # 起点max=4+候補20件を同一Bucket・カテゴリ・近接geoで作成
    # (embeddingは値を散らせてcheap_scoreに差を作る)。
    # GroupEngine.handle(起点)の構造化ログ(caplog "group pool built pool_size=15")
    # とgroup_candidatesの構成メンバーがPool上位15に一致すること。
    # HNSW 2回(1対1Layer2+Pool)の実行時間を記録(design §5-13)

async def test_3_incomplete_group_continues_next_eval(api_client, db_engine, field, redis_sweep):
    """未判定ペアの保留と継続優先(引用#6規則3・4)。"""
    # 4人集合(6ペア)を仕込み・Guardを deny(intent_daily) で制限するか
    # stub Gatewayの評価数を制限して1イベントで6ペア揃えない。
    # group_candidates=candidate保持・latchesなし。
    # 次評価(ReevalRunnerでなくJevWorker.handle再実行で代替)の
    # select_jev_targetsで当該集合の未判定ペアが1対1新規より優先されること
    # (jev_resultが入った行の集合)

async def test_4_kj_mixed_allocation(api_client, db_engine, field, redis_sweep):
    """K_j配分の混在(引用#6): 1対1上位4件が先・残り枠がグループへ。"""
    # 起点に1対1候補6件(min2max2・時間ずらし)+グループPoolを同時に作る。
    # JevWorker.handle後・jev_resultが入った行のpair構成(guardカウンタまたは
    # jev_resultの数)で「1対14件+グループ4件」を検証

async def test_5_participants_bounds(api_client, db_engine, field):
    """4人確定と人数不成立: 種min=4は3人では確定せず・min=5はPoolに入らない。"""
    # (1) 種(min4max4)+候補3件 → 4人集合確定(member 4件)
    # (2) min=5/max=6のIntentはPool検索(min<=4)で除外されること
    #     (group_candidates・match_candidatesのペアに現れない)

async def test_6_d06_top1_notification(api_client, db_engine, field, redis_sweep):
    """D-06上位1集合(引用#8): 2集合成立可能→proposedはaggregate上位1件のみ。"""
    # 起点A+{B,C}(aggregate高)と{D,E}(aggregate低)を両方成立可能にする
    # (jev値を直接UPDATEで差をつける)。finalize後:
    # latches 2行ともcandidate作成されるがproposedは上位1件のみ。
    # 上位を閉じ(status='expired'へ直接UPDATE)た後のdrain
    # (LatchEngine.handleの再実行)で第2集合がproposed化されること

async def test_7_visibility_branch(api_client, db_engine, field, redis_sweep):
    """visibility分岐(引用#14): hidden_until_match混在はheadcount+match_levelのみ。"""
    # メンバー1件をhidden_until_matchで作成・E2Eと同一手順で提案化し
    # proposalのキー集合が {"headcount","match_level"} であること

async def test_8_delete_event_closes_group(api_client, db_engine, field):
    """削除Eventで集合close(引用#15): stage1の_CLOSE_GROUPS。"""
    # 3人集合構成後・メンバー1件を削除Event(intents API DELETE)で処理。
    # group_candidates.status='closed'・構成ペア行はmatch_candidatesの
    # close既存動作(_CLOSE_CANDIDATES)どおりclosedであること

async def test_9_idempotent_double_handle(api_client, db_engine, field, redis_sweep):
    """冪等性: 同一handle 2回でgroup_candidates二重なし・latches二重なし。"""
    # GroupEngine.handle(A)を2回・finalize(A)も2回実行。
    # group_candidates 1行(0005部分UNIQUE・ON CONFLICTで2回目スキップ)・
    # latches 1行(0004部分UNIQUE)・latch_status_eventsのcandidate挿入は1回
    # (design §5-12のON CONFLICT推論の実証)

async def test_10_one_on_one_and_group_parallel(api_client, db_engine, field, redis_sweep):
    """1対1とグループの並走: 同一Fixtureで両提案生成・グループペアのlatch_score NULL。"""
    # 起点A(2人希望min2max2)× 相手B(min2max2)=1対1、B/C/D(max>=3)の
    # グループ集合を混在させFullChain(_run_post_retrieval相当を手順実行)。
    # 1対1提案(latch_score計算済み・2要素latches)とグループ提案
    # (aggregate由来・3要素latches)が両方存在し・グループ構成ペア行の
    # latch_score が NULL のまま
```

各試験の時間窓・jev投入・検証の詳細はtest_matching_latchengine.pyの対応試験と同一の作り方とする(`_seed_jev` を流用する場合はPool生成後のpending行へ直書き)。design §5-12の推論確認(`@>`/`&&`/ON CONFLICTが3〜4要素で動く)は試験1・6・9の成功が実証する。

- [ ] **Step 2: 収集確認(実行禁止)**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_matching_groupengine.py -q`
Expected: `10 tests collected`(exit 0)

- [ ] **Step 3: 完了条件の検証(§6の7項目)**

§6の検証コマンドを順に実行し、結果を記録する:
1. `make lint && make test`
2. `cd backend && uv run pytest --collect-only tests/integration/test_matching_groupengine.py -q`
3. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` + `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v`
4. `git diff --stat main -- 'backend/alembic/versions/0001*' 'backend/alembic/versions/0002*' 'backend/alembic/versions/0003*' 'backend/alembic/versions/0004*' docs` + Task 1のチェーン確認スクリプト
5. `git diff --name-only main | sort` が§4の一覧(22ファイル)と一致・`git status --short` が空
6. `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空

- [ ] **Step 4: 報告書を書く**

`docs/plans/M2/ws-7-report.md` を§7の形式どおり作成(完了条件表・design §5実装時確認事項・固定値変更有無・引継ぎ・検証手順・コミット一覧)。

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/tests/integration/test_matching_groupengine.py docs/plans/M2/ws-7-report.md
git commit -m "test: GroupEngine integration 10試験(収集のみ)とws-7実行報告"
git status --short  # 空であることを確認
```

## Self-Review記録(計画書作成時の確認)

1. **Specカバレッジ**(design §1.1の5部品→Task対応): ①GroupEngine(生成)=Task 7・②K_j配分拡張=Task 4・5・③GroupEngine(集約)=Task 8・④共存改修=Task 6・9(stage1)・⑤未判定ペア継続=Task 4(継続枠)+Task 8(全ペア揃い判定)+integration試験3。マイグレーション0005=Task 1。design §3.1の新規3ファイル・§3.2の変更8ファイルはすべて§4へ反映(design §3.2の「既存試験諸ファイル」は§4変更表の5試験ファイルへ展開)。design §4.3検証手順は§7報告書テンプレートの「スーパーバイザー検証手順」へ反映
2. **プレースホルダスキャン**: TBD/TODO/「後で実装」なし。Task 5の`_recording_store`・Task 7の`_pool_row`等の組立は「既存試験(test_worker_jev.py等)の対応ヘルパーを参照して同一構成」と指示しており・参照先ファイル名と構成要素を明記済み(全文掲載はws-6計画も同様の参照形式を採用)
3. **型一貫性**: `build_groups` の戻り `list[tuple[list[uuid.UUID], uuid.UUID]]` は§9-2全文と§9-4手順7-cで一致(訂正済み)。`select_jev_targets(rows, new_pair_row_ids)` は§9-5と§9-6とTask 4・5試験で一致。`GroupContext(group_ids, new_pair_row_ids)` は§9-4とTask 5試験(_Ctxスタブのnew_pair_row_ids)で一致。`try_promote(latch_id)` public化は§9-8-4と§9-4手順h・Task 6・8で一致
4. **Review Focus**: ①二重生成=Task 1 SQLピン+Task 7分岐試験+integration 9②min>=3起点=Task 7のload_group_origin試験+integration 5③グループペア除外=Task 4・6のSQLピン+integration 10④未判定集合=Task 8試験+integration 3⑤世代変化=Task 8試験(リセット+ガードピン)
