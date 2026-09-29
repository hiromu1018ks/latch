# M2 ws-6(Layer 5 LATCH Engine)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Layer 5 LATCH Engine本体(LatchEngine: latch_score計算 `L = H × MutualScore × C`・閾値0.80判定・latches行生成/昇格・D-07再提案制御・D-08上限検査・75分ルール・D-05回答期限式・提示順drain・nearby_also存在通知・muted抑制)、再評価経路(30分Bucket再評価+catch-upスキャン60秒周期の直接投入)、Worker配線(`_kick_jev` 内JevWorker直列実行・ReevalRunner周期task)、マイグレーション0004(latches開いている行の部分UNIQUE索引)を実装する。回答API・競合クローズ・expiry_sweeper・FCM/お知らせUI・グループマッチ・circuit breakerは後続単位(M3-1〜M3-5・ws-7・ws-8)。

**Architecture:** Layer 5は `_kick_jev` 内で `JevWorker.handle` 完了後に `LatchEngine.handle(intent_id)` を直列実行(design §2.1案A・起点非依存IF)。LatchEngineはJevWorkerと同一の短tx分割(選択読取→ペア毎[H再検証+退避つきlatch_score UPDATE]→latches INSERT/昇格+status_events→try_promote[FOR UPDATE+条件付きUPDATE+notifications]→drain)。D-08日次上限のカウントの真実はnotificationsテーブル(Redisカウンタ不使用)。再評価はReevalRunner(BackfillRunner同型・sleep-first周期60秒)がcatch-up(期限2時間以内)と30分Bucket(time_start属するBucket)を抽出し、reevalガード(ws-4資産・入口共用)を通して `_run_direct_pipeline`(L1〜3トランザクション→JevWorker→LatchEngine)を直接投入(Eventを発行しない — idempotencyキー衝突のため)。外部SDKなし(純計算+DB+Redis。context7確認不要)。

**Tech Stack:** 変更なし(Python 3.13 / SQLAlchemy[asyncio]+asyncpg / redis-py(asyncio) / fakeredis[unit]。依存追加なし)。

**Spec:** `docs/plans/M2/ws-6-design.md`(agent1設計メモ。**未解決論点なし** — design §5のsupervisor承認事項3件〔マイグレーション0004・notifications先行書き込み・スコープ分担はSTATUS単位表が正〕と解釈記録5件〔対象開始時刻=max(time_start)・area_name=geo中点の逆転ジオコーディング・nearby通知のno履歴検査とmatch_level='low'埋め・time_summary書式=JST YYYY-MM-DD HH:MM・category_secondaryは種Intentの値〕は2026-09-29承認/了解済みの確定事項として本計画へ反映。解釈記録はSTATUS「G2時確認事項」へ記載済み。実装時確認事項§5-9〜10はTask 4・Task 8へ手順として組み込み)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m2-ws-6`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(依存追加はないためlockは進まない)。
- **共有ci-db運用**(STATUS運用ルール1〜3): 本単位は**マイグレーション0004を追加する単位**である。**agent3は `make test-ci` / `make migrate` / `make up` / `make down` / `docker compose …` / `docker build` / `docker pull` を一切実行しない**。開発はunit試験(`make lint`・`make test`)で完結させ、報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する。integration試験ファイルは作成するが**実行せず**、収集(`--collect-only`)のみ確認する。`make test` はintegrationファイルの収集(import)まで行うため、構文・importの正当性はunit実行で検証される。本単位はwave単独のため並走とのDB取り合いは計画上発生しない(運用ルール1・2は報告書へ明記)。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-09-29)**: 現状 `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空。本計画の新規5ファイル(`test_latch_calc.py`・`test_proposal.py`・`test_latch_engine.py`・`test_worker_reeval.py`・`test_matching_latchengine.py`)は既存96ファイルと衝突しない(既存リストに同名なし・`test_worker_reeval.py` と既存 `cost/test_cost_reeval.py` はbasename不同でOK)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **ピン試験の干渉確認済み**(supervisor追記4): `rg "model_fields" backend/tests` は4ファイル(`test_llm_factory.py`〔llm_ prefix 9項目〕・`test_geo_settings.py`〔geo_ prefix 3項目〕・`test_auth_settings.py`〔auth_ prefix 8項目〕・`test_send_record.py`〔SendRecord許可リスト・Settingsと無関係〕)。本計画でsettings.pyへ追加する `reeval_runner_interval_sec`・`reeval_runner_batch_limit` はいずれのprefixにも一致しないため**干渉なし・期待値更新不要**。`test_settings.py` に件数ピンなし。Task 7実装後に `make test` 全件グリーンで再確認する。
- **並走単位なし**(design §1前提: 実行waveはws-6単独)。ただし既存ファイルへ触れるため、各Taskのコミット前に `git status --short` で意図しないファイルの変更が混入していないことを確認する(§4の一覧以外に差分が出ていたら作業を止めて報告する)。
- **固定値の遵守**: design §2 の採用判断と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| スコア計算は01 §13の定義をそのまま実装: `L = H × MutualScore × C`・`MutualScore = min(would_a_accept_b, would_b_accept_a)`・HはLayer 1判定時点の再検証(通過=1・不成立=0)・Cは初期値1 | 06 §6 |
| L >= 0.80(D-01)なら提案候補。提案化の前にD-08上限(1ユーザー日6件・1Intent同時3件)を検査し、上限内なら直ちにproposedへ遷移させて通知。超過分は保留キュー(latches.status=candidate)へ。閾値超過の候補は上限検査の結果にかかわらずlatches行をcandidateとして作成する | 06 §6・§10 |
| 保留キューの提示順は対象時刻昇順・タイブレークLATCH Score降順。提示時(candidate→proposed遷移)には提示時点でD-05の式を再計算しresponse_deadlineを上書きする。candidate作成時のresponse_deadlineは暫定値 | 06 §10・03 D-08 |
| D-05式: `回答期限 = min( max( 通知時刻+15分, min(通知時刻+2時間, 対象開始時刻−60分) ), 参加Intentのexpires_atの最小値 )`。導出期限が通知時刻を過ぎない場合は通知しない(75分ルールと同一扱い)。対象時刻まで75分を切った提案は通知自体を行わない | 03 D-05・§5 |
| 提示対象を取り出した時点で対象開始時刻まで75分を切っている候補は通知せず破棄(candidate→expired)。保留中のcandidateのexpires_at経過によるexpiredはexpiry_sweeper(M3-3)の担当 | 06 §10・§6 |
| D-07: 見送り(defer)後の同一Intent組み合わせへの再提案は (a)見送りから min(24時間, 対象開始時刻までの残時間の半分) の経過 (b)スコア変化(またはどちらかのIntent更新による新候補)の両方を満たす場合に限る。no回答が存在する場合はIntent期限まで再提案しない。回答種別(no/defer)はlatches.responsesに区別保持 | 03 D-07 |
| D-07スコア変化の判定: 評価トランザクション内で現行match_candidates行をFOR UPDATEで読み、現行latch_score・updated_atをprev_latch_score・prev_evaluated_atへ退避してから新評価値でUPDATE。prevがNULL(初回評価)なら無条件に変化あり。NULLでなければ \|新−prev\| ≧ 0.05 を変化あり。評価世代(バージョン組)そのものの変化も条件(b)を満たす | 06 §10 |
| D-08: 1ユーザーあたり提案通知は日6件まで(0時リセット)・1Intentあたり同時進行(proposed)の提案は3件まで。上限超過の候補は破棄せず保留し、既存提案のクローズまたはリセット後に提示順で提示 | 03 D-08 |
| nearby_alsoが指定されたIntentは閾値未満の候補の発生時にその存在の通知のみ(条件サマリ・一致度・相手情報を含めない)。当該候補はlatches.status=candidateのまま保持し(proposed遷移しない)、閾値超過時または後続の再評価で通常の提案判定に回る。存在通知もD-08の日次上限に含める。同時進行上限はcandidateのままの存在通知には適用しない | 06 §6・03 §3 |
| mutedのIntentは提案通知を送らない。それ以外の挙動(proposed遷移・保留キュー・回答期限・競合クローズ)は通常どおり。muted提案もproposed遷移であるためMutual Latch Rateの分母(proposed遷移カウント)に含まれる | 06 §6・09 §2.1 |
| proposal(05 §2正式構造)の生成はLayer 5。visibility生成分岐: summary_only双方は全フィールド・hidden_until_matchを含む候補ではheadcountとmatch_levelのみ。双方のvisibilityが異なる場合はより厳しい方を優先。格納禁止はraw_text・soft/NG条件の文言・座標 | 05 §2・08 §2.3・D-11 |
| match_level: high(0.90以上)/ medium(0.80以上0.90未満)/ low(提案閾値以上0.80未満。下端は運用中の提案閾値に連動)。内部スコア生値は格納しない | 05 §2 |
| area_nameはgeo_centerを約1kmグリッドへ丸めた代表点の地物名(逆転ジオコーディング・D-11)。変換結果の地域名のみproposalへ格納 | 08 D-11・04 §3 |
| latches: intent_ids(uuid[]・2≦長さ≦4)・proposal(jsonb NOT NULL)・score(提示時)・status(candidate/proposed/…)・response_deadline(NOT NULL)・expires_at(参加Intentのexpires_at最小値)。latch_status_eventsは遷移トランザクションと同時挿入・from_statusはcandidate作成時NULL可・user_idはシステム起因(Layer 5)はNULL | 05 §2・06 §10 |
| 再評価差分化: 再評価経路のたびにdrainを試みる。大量保留時は提示順に沿って上限内の件数のみ処理し残りは次トリガーへ | 06 §10 |
| 30分Bucket再評価: 到来したBucketに属するIntentのみCandidate Retrievalから再実行。起点のembedding IS NULLなら当該Bucketをスキップ。Bucket境界はClockインターフェース経由 | 06 §9・04 §5 |
| catch-upスキャン: 60秒周期で `status='active'` かつ `expires_atまで2時間以内` のIntentを抽出しCandidate Retrievalからパイプラインを直接投入(Eventを発行しない — 同一versionのidempotencyキー衝突のため)。embedding IS NULLは対象外 | 06 §9 |
| 再評価頻度制限(同一Intent更新由来)はreeval:{intent_id} TTL 30分のガードで判定。Bucket再評価・catch-upも同一部品を入口で共用 | 06 §5・ws-4資産 |
| 競合制御(FR-08)の条件付きUPDATE・FOR UPDATE方式・Clock.now()基準。Layer 5の遷移も同一の直列化方式(WHEREにstatus条件)で書く | 06 §6・§10 |
| 通知手段(FCM・アプリ内通知)はM3-5。M2のws-6はlatches遷移と通知記録(notifications行)まで | 12 M3-5・02 §4 |
| 02#8(時間イベントで再評価できる)= 指定時刻をまたぐ2つのactive Intentを配置し時刻到来による候補生成の記録。Bucket再評価・catch-up経路のE2E検証対象 | 02 §4 |
| Layer 1〜5はembedding_completedを起点にのみ走る(12 C6)・Layer 4通過候補のみが閾値0.80と比較される(12 C7)・visibility分岐・nearby_also・mutedはLayer 5のproposal/通知生成に組み込みLayer 1には含めない(12 C13)・時刻参照はすべてClock経由(12 C2) | 12 §2 |
| 実行位置(案A: `_kick_jev` 内JevWorker直列)・トランザクション分割・例外方針(design §2.9の表)・Redisカウンタ不採用(design §2.7)・性能索引不採用(design §2.10) | design §2 |
| supervisor承認事項(2026-09-29): マイグレーション0004・notifications先行書き込み(payloadは`{"latch_id"}`の最小参照・type='proposal'/'nearby_candidate')・スコープ分担はSTATUS単位表が正。解釈記録5件(対象開始時刻=max(time_start)・area_name=geo中点・nearbyのno履歴検査とmatch_level='low'・time_summary書式・category_secondaryは種Intent) | design §5・STATUS「G2時確認事項」 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **永続化は `sqlalchemy.text()` 生SQLのみ**(worker/matching配下・jev.pyと同じ形式)。ORMモデル・リソーストリ層を作らない
- **bind param は `CAST(:x AS ...)` 形式**(NULLを渡しうるパラメータ・uuid・timestamptzは必須。test_layer_sql.py流儀のcompile検査が回帰を防ぐ)。**uuid[] は `ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]` の要素明示形式**(コードベースにANY/list渡しの前例がないため・asyncpgの型推論に頼らない)
- **時刻はClock経由のみ**。製品コード(`backend/src/latch/`)で実時間参照禁止(arch test `test_arch_no_direct_time.py` が強制)。JST日付境界(D-08の0時リセット・bucket_start)もClockから導出する(C2)
- **例外は握り潰さない**(design §2.9の表): LatchEngineのDB書き込み失敗・ReevalGuardのRedis失敗(JevCostDependencyError)は伝播(fail-closed→`_kick_jev` 経由なら_dispatchの既存except→ackなし再配信→duplicate経由で再実行。ガードで冪等)。逆転ジオコーディングの地物なし(area_name=None)は例外にしない。ReevalRunnerのrun_once内の例外はrun()ループで握って次周期で回収(BackfillRunnerと同一)
- **例外メッセージ・ログにIntent本文を入れない**(08 §2.4)。ID・status等の機械情報のみ
- **LATCH_THRESHOLD=0.80・LATCH_C=1.0・D07_DELTA=0.05・D08_DAILY_LIMIT=6・D08_CONCURRENT_LIMIT=3・75分/15分/2時間/60分・MATCH_LEVEL_HIGH_MIN=0.90はモジュール定数**(settings化しない — design §2.2・ws-4/ws-5規律。Cは01 §14のCalibration調整点だが環境差し替え想定ではないためコード定数)。docs確定値の調整はdocs改版を伴う
- **丸めなし**: latch_score・score列・MutualScoreとも丸めない
- **DBトランザクションは短tx分割**(design §2.9): (1)フェーズ1読取 (2)ペア毎H再検証+退避つきUPDATE (3)latches INSERT/昇格+events (4)try_promote(遷移+events+notifications) (5)drainは行毎に(4)を再利用。geo逆転は読取のみ・tx外で実行
- **jev.py・layer1〜4.py・runner.py・origin.py・candidates.py・geo/service.py は変更しない**(§5)。importして呼ぶのみ。H再検証は `layer4.hard_constraint_holds` をそのまま呼ぶ(LAYER1_WHERE再利用・試験と本番のWHERE乖離なし)
- **改行・行長**: ruff(E,F,I,UP,B)と `ruff format`(行長88)を毎コミット通す
- **unit試験は外部プロセス不要・実時間待ちなし**(スタブ注入・FakeClock)。SQL文字列の正当性はcompile検査(unit)+実DB試験(integration・スーパーバイザー検証時)の二段構え
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **duplicate Event再実行で同一ペアのlatchesが二重生成される** — `_kick_jev` はduplicateでも実行される(at-least-once)。0004部分UNIQUE+`ON CONFLICT DO NOTHING`+行数0分岐(昇格/スキップ)が崩れると重複提案になる → Task 4のSQLピン(`ON CONFLICT (intent_ids) WHERE status IN …`・RETURNING)と分岐試験(既存proposed→スキップ)+Task 8 integration試験9(handle 2回でlatches二重なし・candidateイベント1回)
2. **nearby行(score < 0.80のcandidate)がdrainでproposed化される** — 引用#9「当該候補はcandidateのまま保持し(proposed遷移しない)」。drain SELECTに `l.score >= :threshold` が落ちると存在通知済み行が提案化される → Task 5のdrain SQLピン+unit試験(閾値未満行はtry_promoteへ渡らない)
3. **muted参加者へnotificationsが書かれる(書いてはいけない)・日次上限を消費する** — 引用#10「通知のみしない」=通知しないものはカウントもしない。通知対象者の抽出を1箇所(notification_level != 'muted')に寄せないと分母計上(proposed遷移)と通知除外が矛盾する → Task 5試験(muted→proposed遷移+notifications 0行+上限消費なし)
4. **75分切れ・D-05導出期限<=nowの候補がproposed化され通知される** — 引用#4・#5「通知せず破棄(candidate→expired)」。75分チェックがD-08カウントの後やproposed遷移の後に来ると通知事実が先に作れる → Task 5試験(75分→expired+status_events+通知ゼロ・手順順序のピン)
5. **D-08日次上限がJST 0時を跨いで残る(0時リセットされない)** — 引用#8「日6件まで(0時リセット)」。day_start/day_nextが固定値や実時間由来だと境界がずれる → Task 5のSQLピン(`created_at >= CAST(:day_start AS timestamptz)`)+day_start=`jst_day_start(clock.jst_date())`由来の試験+Task 2のjst_day_start再利用(layer4からimport・再実装しない)

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1):

```text
backend/alembic/versions/0004_latches_open_unique.py      (Task 1。uq_latches_intent_ids_open部分UNIQUE索引)
backend/src/latch/worker/matching/latch_calc.py           (Task 2。純関数群・定数)
backend/src/latch/worker/matching/proposal.py             (Task 3。build_proposal・LatchIntentInputs・nearby最小構造)
backend/src/latch/worker/matching/latch_engine.py         (Task 4・5。LatchEngine本体)
backend/src/latch/worker/reeval.py                        (Task 6。ReevalRunner)
backend/tests/unit/matching/test_latch_calc.py            (Task 2)
backend/tests/unit/matching/test_proposal.py              (Task 3)
backend/tests/unit/matching/test_latch_engine.py          (Task 4で作成・Task 5で追記)
backend/tests/unit/test_worker_reeval.py                  (Task 6)
backend/tests/integration/test_matching_latchengine.py    (Task 8。作成のみ・実行しない)
docs/plans/M2/ws-6-report.md                              (Task 8。報告ファイル)
```

変更(design §3.2):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/worker/main.py` | (1)`_kick_jev` 内へLatchEngine.handle直列追記(イベント種フィルタの構造変更を含む・§9-7) (2)`LatchEngine`・`ReevalRunner` のDI(run()内構築) (3)`_run_direct_pipeline(engine, intent_id)` の新設 (4)ReevalRunnerのtask起動・graceful shutdown待ち | 7 |
| `backend/src/latch/settings.py` | `reeval_runner_interval_sec=60`・`reeval_runner_batch_limit=50` を追加(embedding_backfill並び) | 7 |
| `backend/tests/unit/test_worker.py` | `_kick_jev` 直列配線のピン追加(jev後にlatch・未注入no-op・createdでは呼ばれない)・`_run_direct_pipeline` 配線ピン | 7 |
| `backend/tests/unit/test_settings.py` | `test_settings_defaults` へ新規2項目の既定値ピン追記(機械的追随) | 7 |

`worker/matching/__init__.py` は変更しない(layer4・jevもexportされておらず直接import規律。latch_calc・proposal・latch_engineも`from latch.worker.matching.latch_calc import …`の直接import)。

既存integration(test_matching_jev.py等)は `JevWorker`・`run_candidate_retrieval` の外部契約が不変のため無変更で動く見込み。影響が出た場合は機械的追随を報告書に記録する。

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **docker系コマンド一切**(§0): `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` / `docker pull`。マイグレーション0004の適用とapiイメージ再ビルド・integration実行はすべてスーパーバイザーの検証手順に含まれる
- **design §3.3の禁止**: `backend/src/latch/` 配下の intents / auth / users / ratelimit / geo / g1gate / events / core / llm 各モジュール。`worker/` の既存ファイルのうち main.py 以外(jev.py〔JevWorker・呼ぶのみ〕・stage1.py・embedding.py・embedding_text.py・debounce.py・backfill.py・__main__.py・cost/配下・`matching/` 配下の既存7ファイル{origin,layer1,layer2,layer3,layer4,candidates,runner}.py — **layer4.hard_constraint_holds・jst_day_start はimportして再利用するのみ・変更しない**。geo/service.pyの`reverse_geocode`も呼ぶのみ)。`backend/alembic/` の既存分(0001〜0003不変。0004追加のみ)。`compose.yaml`・`frontend/`・`prototype/`・`docs/`(01〜12改版不要・05 §2への部分UNIQUE索引追記は次回docs改版に含める方針 — design §1.4-7)・`Makefile`・`backend/pyproject.toml`・`backend/uv.lock`(依存追加なし)・`.env`・`.env.example`。`docs/plans/STATUS.md`(スーパーバイザー管理)。`docs/plans/M0/`・`M1/`・`M2/` の既存ファイル(design・過去plan・過去report)。`backend/tests/` の§4に列挙した以外の既存試験
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4・§2.10):
  - **回答API・競合クローズ・matched遷移・calibration_records生成**(M3-1/M3-2): 本単位はlatchesのcandidate/proposed/expired(75分・昇格)遷移のみ。responsesは常に'[]'のまま(試験が直接投入するdefer履歴を除く)
  - **expiry_sweeper・Intent期限切れバッチ**(M3-3): candidateのexpires_at経過によるexpired遷移はsweeper担当(本単位のdrain/try_promoteは期限切れ行を対象外にするだけ)
  - **リセットジョブ・0時の保留キュー再評価イベント発行**(M3-4): M2ではdrainを評価経路のたびに試みることで0時直後の提示を自然に回収する。イベント発行接続はM3-4
  - **FCM・お知らせUI・通知文テンプレート**(M3-5): notificationsテーブルへのレコード生成まで。payloadは`{"latch_id"}`の最小参照
  - **グループマッチ**(ws-7): 候補Pool・貪欲法・group_candidates・aggregate_score。本単位は1対1のみ(latches生成・proposal生成headcount=2・drainを1対1構成で実装)。拡張点はdocstringへ記す
  - **circuit breaker・E2Eハーネス・障害注入**(ws-8)。本単位にLLM呼び出しはない
  - **RedisによるD-08日次カウンタ**(design §2.10-1): notificationsを真実とする。二重管理を作らない
  - **latchesへの「対象時刻」・評価世代カラム追加**(design §2.10-2〜3): max(time_start)を都度導出(相関サブクエリ)。世代判定はprev_latch_scoreで足りる
  - **性能索引(latches status部分索引・notifications(user_id, created_at)・GIN(intent_ids))**(design §2.10-4): ci・ベータ規模では全走査が十分速い。部分UNIQUE(0004)は正確性が目的で必要
  - **同一バージョン組でのlatch_score再計算経路(C調整)**(design §2.10-7): C=1.0固定。退避つきUPDATEとd07純関数はそのときの骨格として動くように実装するが、呼び出し経路は作らない
  - **drainのバッチ化・分散ロック**(design §2.10-8): Worker 1構成を前提に行ロックのみ
  - **scheduled Event種の発行経路**(design §2.8): 両経路ともEventを発行しない直接投入に統一。stage1の受信規定は将来用にそのまま残置(stage1.pyは触らない)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 8で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分・既存試験の機械的追随を含む)
   検証: `make lint && make test` — ともにexit 0
2. **integration 1ファイル(10試験)が収集できる** — **ただし実行はしない**(§0。実DB・実Redis・0004適用後のスキーマが必要なためスーパーバイザー検証時に実施)
   検証(実装側): `cd backend && uv run pytest --collect-only tests/integration/test_matching_latchengine.py -q` がexit 0(10件収集)。報告書に「**test-ci=スーパーバイザー検証待ち**」と記録
3. **実時間参照が `core/clock.py` のみ**(latch_calc.py・proposal.py・latch_engine.py・reeval.py 配下はヒットしない)
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
4. **マイグレーションは0004追加のみ・0001〜0003とdocs(01〜12)に差分なし**
   検証: `git diff --stat main -- backend/alembic/versions/0001_initial_schema.py backend/alembic/versions/0002_geofeatures.py backend/alembic/versions/0003_match_candidates_skip_reason.py docs` — 出力なし(0004は新規ファイルとして表示される・reportはTask 8コミット後に再確認)。加えてalembicチェーンの静的確認(Task 1 Step 2のスクリプト・heads=['0004'])
5. **変更ファイルが§4の一覧どおり(作成11+変更4=15ファイル)**
   検証: Task 8 Step 3の報告コミット後に `git diff --name-only main | sort` が§4の一覧(15ファイル・report込み)と完全一致。`git status --short` が空(未コミット変更なし)
6. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし
7. **既存unit試験がすべてグリーンのまま**(既存試験の期待値変更はtest_worker.pyへのピン追加のみであることの証明として `make test` の全件数を報告書へ記録し、変更が§4列挙以外に及んでいないことを§4との突合で示す)

## 7. 報告形式

**結果ファイル**: `docs/plans/M2/ws-6-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M2 ws-6(Layer 5 LATCH Engine)実行報告

- ブランチ: m2-ws-6 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も)> |
| 2 | integration 10試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | <collect-only出力> |
| 3 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 4 | alembic 0001〜0003・docs無変更+チェーン確認 | PASS/FAIL | <git diff --stat 出力(空なら「空」)+heads=['0004']> |
| 5 | 変更ファイル=§4の14ファイル | PASS/FAIL | <git diff --name-only出力> |
| 6 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 7 | 既存unit試験グリーン維持 | PASS/FAIL | <make test 全件数と§4列挙以外の期待値変更がないことの確認結果> |

**test-ci=スーパーバイザー検証待ち**(STATUS運用ルール1〜3・マイグレーション0004追加のため
migrate/test-ci/docker系は実装側で未実行。wave単独のため並走とのDB取り合いは計画上なし)

## design §5 実装時確認事項の結果
9. notifications.type列のCHECK制約有無: <0001定義の再確認結果(計画時点でCHECKなし確認済み・
   実装時にrgで再確認した結果)。CHECKが存在していた場合はBLOCKEDとして記録>
10. uuid[]部分UNIQUE索引のON CONFLICT推論: <unitのSQLピンは済み。実DBでの推論確認は
    スーパーバイザー検証時のintegration試験1・9が担う(掠んだ場合は推移を記録)>

## 固定値の変更有無(design.md §2・本計画§9)
- 実行位置=案A(_kick_jev内JevWorker直列・design §2.1): 変更なし / 変更あり(<前→後+理由>)
- 0004部分UNIQUE索引+ON CONFLICT DO NOTHING(design §2.3): 変更なし / 変更あり(<前→後+理由>)
- D-07判定のd07_allows純関数化・両直前呼出(design §2.4): 変更なし / 変更あり(<前→後+理由>)
- 対象開始時刻=max(time_start)(承認済み解釈): 変更なし / 変更あり(<前→後+理由>)
- notifications先行書き込み・payload={"latch_id"}最小参照(承認事項2): 変更なし / 変更あり
- drain直接投入統一・scheduled Event不使用(design §2.8): 変更なし / 変更あり(<前→後+理由>)
- 本計画§9のIF確定事項(SQL全文・純関数・try_promote手順): 変更なし / 変更あり(<前→後+理由>)

## (ws-7・ws-8・M3への引継ぎ)
- ws-7: latches生成・proposal生成(headcount=2)・drain・try_promoteは1対1構成。
  intent_ids正規化はsorted・集合側は同様に正規化。拡張点は各docstringに記載
- ws-8: LatchEngineにLLM呼び出し・外部APIなし(geo逆転はPostGIS完結)。障害注入対象外
- M3-1〜M3-5: responsesは常に'[]'(試験投入分を除く)。回答APIはtry_promoteと同一の
  条件付きUPDATE規律で書く。expiry_sweeperはcandidateのexpires_at切れを担当。
  FCM/お知らせUIはnotifications行から組み立て(payload={"latch_id"})

## スーパーバイザー検証手順(test-ci実行時)
1. `docker compose build api worker` — イメージ再ビルド(STATUS運用ルール4)
2. `make migrate` — 0004適用確認(alembic_version=0004・`\di uq_latches_intent_ids_open` の存在)
3. `make test-ci` — 既存全数(926)+本単位integration 10件がグリーン。design §5-10の
   ON CONFLICT推論は試験1・9が実証(失敗する場合はINSERT前SELECT FOR UPDATE切替を協議)
4. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
   (運用ルール5)
5. test-ci後、Redis残存(prefix掃除)とuser残存(subject LIKE 'm2ws6-%'=0件)・
   latches/latch_status_events/notifications残存(prefix由来=0件)を1回手動確認

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件7項目の結果一覧(項目2の実行部分は「test-ci=スーパーバイザー検証待ち」)。

## 9. IF確定事項(実装定義の決定値。docsが例示のみの部分を本計画で確定する。変更時は報告書に記録)

### 9-1. モジュール構成・通知種別

- `latch_calc.py` — 純関数・定数のみ(DB・asyncなし・SQLなし)
- `proposal.py` — `LatchIntentInputs`(frozen dataclass)・`build_proposal`(純関数)・`nearby_proposal`。latch_calcをimport(match_level)
- `latch_engine.py` — `LatchEngine`本体・SQL text()定数・conn受取りのモジュール関数群(unit試験がmonkeypatchで差し替え可能・runnerと同一規律)
- `worker/reeval.py` — `ReevalRunner`(BackfillRunner同型)
- 通知種別定数(latch_engine.py): `NOTIFICATION_PROPOSAL = "proposal"`・`NOTIFICATION_NEARBY = "nearby_candidate"`。**コード側のLiteralで管理**(notifications.typeにCHECK制約なし — 0001実検証済み・Task 4で再確認)
- latches.statusの開いている値: `('candidate', 'proposed', 'partial_accept')`(0004のWHERE句と同一・定数 `OPEN_LATCH_STATUSES` は持たずSQLリテラルで統一)

### 9-2. latch_calc.py 全文(定数・純関数)

```python
"""Layer 5の純関数群・定数(06 §6・§10・03 D-05/D-07/D-08・design §2.4〜2.6)。

DB・async・SQLを持たない純計算のみ(unit試験は決定的)。jst_day_start は
layer4からimportして再利用(再実装しない — C2のJST導出を一元化)。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from latch.core.clock import JST
from latch.worker.matching.layer4 import jst_day_start  # noqa: F401(再export・試験が同一規律を検証)

LATCH_THRESHOLD = 0.80   # 06 §6「L >= 0.80」(D-01)
LATCH_C = 1.0            # 01 §14・C初期値(Calibration調整点・コード定数)
D07_DELTA = 0.05         # 06 §10(スコア変化判定)
D08_DAILY_LIMIT = 6      # 03 D-08(1ユーザー日次)
D08_CONCURRENT_LIMIT = 3  # 03 D-08(1Intent同時進行)
MATCH_LEVEL_HIGH_MIN = 0.90  # 05 §2(high/medium境界)

PROMPT_LEAD_MIN = timedelta(minutes=75)     # 06 §10(75分ルール)
DEADLINE_MIN_AFTER_NOTIFY = timedelta(minutes=15)   # 03 D-05
DEADLINE_MAX_AFTER_NOTIFY = timedelta(hours=2)      # 03 D-05
DEADLINE_BEFORE_START = timedelta(minutes=60)       # 03 D-05
DEFER_SUPPRESS_MAX = timedelta(hours=24)            # 03 D-07(min(24時間, 残時間/2)の上限)
BUCKET_MINUTES = 30                                  # 06 §9(30分Bucket)

RESPONSE_NO = "no"
RESPONSE_DEFER = "defer"


def response_deadline(
    now: datetime, target_time: datetime, min_expires_at: datetime
) -> datetime:
    """D-05回答期限式(03 D-05・design §2.6)。

    回答期限 = min( max(通知時刻+15分, min(通知時刻+2時間, 対象開始時刻−60分)),
    参加Intentのexpires_atの最小値 )。丸めなし。
    """
    inner = max(
        now + DEADLINE_MIN_AFTER_NOTIFY,
        min(now + DEADLINE_MAX_AFTER_NOTIFY, target_time - DEADLINE_BEFORE_START),
    )
    return min(inner, min_expires_at)


def match_level(score: float) -> str:
    """一致度区分(05 §2)。high/medium/low — 下端は運用閾値に連動し未定義区間なし。"""
    if score >= MATCH_LEVEL_HIGH_MIN:
        return "high"
    if score >= LATCH_THRESHOLD:
        return "medium"
    return "low"


def pair_target_time(time_start_a: datetime, time_start_b: datetime) -> datetime:
    """対象開始時刻 = max(参加Intentのtime_start)(承認済み解釈・design §2.4)。

    75分ルール・D-05式・defer抑制の残時間・提示順ソートがすべてこの値を参照。
    """
    return max(time_start_a, time_start_b)


def d07_history_inputs(responses: list[dict]) -> tuple[bool, datetime | None]:
    """latches.responses(JSONB復元済みlist)からD-07判定入力を抽出(純関数)。

    has_no: response='no' が1つでも存在。latest_defer_at: response='defer' の
    answered_at(ISO文字列)の最大(None=defer履歴なし)。answered_at欠損の
    defer要素は無視する(防御・M3-1が必ず書く)。
    """
    has_no = any(r.get("response") == RESPONSE_NO for r in responses)
    defers = [
        datetime.fromisoformat(r["answered_at"])
        for r in responses
        if r.get("response") == RESPONSE_DEFER and r.get("answered_at")
    ]
    return has_no, (max(defers) if defers else None)


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


def bucket_start(now: datetime) -> datetime:
    """現在時刻の属する30分Bucketの開始時刻(06 §9・design §2.8-2)。

    JSTへ変換し分を0/30へ切り下げ。戻り値はJST tz-aware(Bucket境界はClock由来)。
    """
    jst_now = now.astimezone(JST)
    minute = 0 if jst_now.minute < BUCKET_MINUTES else BUCKET_MINUTES
    return jst_now.replace(minute=minute, second=0, microsecond=0)
```

### 9-3. proposal.py 全文

```python
"""proposal生成(05 §2・08 §2.3・D-11・design §2.5)。

visibility生成分岐(引用#12)と各フィールドの生成規則。格納禁止
(raw_text・soft/NG条件の文言・座標)は構造上入らない。area_nameは
geo中点の逆転ジオコーディング結果を呼び出し側(tx外)が渡す
(承認済み解釈 — 純関数はDBを持たない)。
ws-7拡張点: headcount=2固定とorigin/peer 2者構成を集合側へ拡張する
(category_secondaryは「種Intentの値」規則を集合の種へ適用)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from latch.core.clock import JST
from latch.worker.matching.latch_calc import match_level, pair_target_time

NEARBY_HEADCOUNT = 2


def nearby_proposal() -> dict:
    """nearby存在通知用の最小構造(08 §2.2・design §2.7)。常に新規dict。"""
    return {"headcount": NEARBY_HEADCOUNT, "match_level": "low"}


@dataclass(frozen=True)
class LatchIntentInputs:
    """build_proposal入力の1Intent分(_SELECT_INTENT_INPUTS由来)。

    格納禁止フィールド(raw_text・soft/NG文言・座標文字列)を保持しない
    構造ピン(unit試験がdataclasses.fieldsで検証)。geo_lon/geo_latは
    area_name生成のための読取専用値(proposalへは格納しない)。
    """

    intent_id: uuid.UUID
    user_id: uuid.UUID
    visibility: str              # 'summary_only' | 'hidden_until_match'
    notification_level: str      # 'proposals_only' | 'nearby_also' | 'muted'
    time_start: datetime
    expires_at: datetime
    budget_max: int | None
    category_primary: str
    category_secondary: str | None  # structured_data.category.secondary
    geo_lon: float
    geo_lat: float


def build_proposal(
    *,
    origin: LatchIntentInputs,
    peer: LatchIntentInputs,
    score: float,
    area_name: str | None,
) -> dict:
    """proposal生成(05 §2・design §2.5の表)。origin=評価の種(起点Intent)。

    visibility分岐: 双方summary_only→全フィールド。いずれかが
    hidden_until_match→headcountとmatch_levelのみ(より厳しい方を優先)。
    """
    if origin.visibility != "summary_only" or peer.visibility != "summary_only":
        return {"headcount": 2, "match_level": match_level(score)}
    budgets = [b for b in (origin.budget_max, peer.budget_max) if b is not None]
    return {
        "time_summary": pair_target_time(origin.time_start, peer.time_start)
        .astimezone(JST)
        .strftime("%Y-%m-%d %H:%M"),
        "area_name": area_name,
        "headcount": 2,
        "category_primary": origin.category_primary,  # Layer 1完全一致で同一
        "category_secondary": origin.category_secondary,  # 起点側(引用#8)
        "budget": {"max": min(budgets)} if budgets else None,
        "match_level": match_level(score),
    }
```

### 9-4. latch_engine.py のSQL全文(text()定数)

補足: design §2.5の入力収集SQLから `time_end`(使用箇所なし)を除いた。design §2.6の `user_id = ANY(CAST(:users AS uuid[]))` は2要素明示の `IN` 形式へ置き換えた(意味は等価・asyncpgのlist型推論に頼らない — §2グローバル制約)。

```python
# フェーズ1: 起点に紐づく「計算済みでない評価行」(design §2.2。
# 起点version一致・latch_score IS NULL(冪等ガード)・相手version=相手現行のEXISTS)
_SELECT_TARGETS = text("""
    SELECT mc.id, mc.intent_a_id, mc.intent_b_id, mc.intent_a_version,
           mc.intent_b_version, mc.jev_result, mc.cheap_judge_score
    FROM match_candidates mc
    WHERE (mc.intent_a_id = CAST(:origin AS uuid)
           OR mc.intent_b_id = CAST(:origin AS uuid))
      AND (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                THEN mc.intent_a_version
                ELSE mc.intent_b_version END) = :origin_version
      AND mc.status = 'evaluated'
      AND mc.jev_result IS NOT NULL
      AND mc.latch_score IS NULL
      AND EXISTS (
          SELECT 1 FROM intents p
          WHERE p.id = (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                             THEN mc.intent_b_id ELSE mc.intent_a_id END)
            AND p.version = (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                                  THEN mc.intent_b_version
                                  ELSE mc.intent_a_version END))
    ORDER BY mc.cheap_judge_score DESC NULLS LAST,
             (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                   THEN mc.intent_b_id ELSE mc.intent_a_id END) ASC
""")

# H再検証不成立行のclose(WHEREにstatus条件 — design §2.2-1)
_CLOSE_H_BROKEN = text("""
    UPDATE match_candidates
    SET status = 'closed', updated_at = :now
    WHERE id = CAST(:row_id AS uuid) AND status = 'evaluated'
    RETURNING id
""")

# 退避つきlatch_score UPDATE(design §2.2-4・06 §10手順1〜2。RETURNINGの
# prev_latch_scoreは退避された旧値(初回計算はNULL=手順3「prevがNULLなら
# 無条件に変化あり」を自然に作る)。行数0=他の実行が先に計算済み)
_RECORD_SCORE = text("""
    UPDATE match_candidates
    SET prev_latch_score = latch_score,
        prev_evaluated_at = updated_at,
        latch_score = :score,
        updated_at = :now
    WHERE id = CAST(:row_id AS uuid) AND latch_score IS NULL
    RETURNING id, prev_latch_score
""")

# proposal入力・try_promote参加者読取(design §2.5。visibility・
# notification_levelは0001のCHECKつき列・category_secondaryはstructured_data内)
_SELECT_INTENT_INPUTS = text("""
    SELECT id, user_id, visibility, notification_level, time_start,
           expires_at, budget_max, category_primary, structured_data,
           ST_X(geo_center::geometry) AS lon, ST_Y(geo_center::geometry) AS lat
    FROM intents WHERE id = CAST(:intent_id AS uuid)
""")

# D-07履歴(同一組み合わせの全latches・responses空は除外。design §2.4-1)
_SELECT_LATCH_RESPONSES = text("""
    SELECT responses FROM latches
    WHERE intent_ids = ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]
      AND responses <> CAST('[]' AS jsonb)
""")

# latches生成(0004部分UNIQUE索引へON CONFLICT・design §2.3)
_INSERT_LATCH = text("""
    INSERT INTO latches
        (intent_ids, proposal, score, status, response_deadline, expires_at, created_at)
    VALUES (ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[],
            CAST(:proposal AS jsonb), :score, 'candidate', :deadline, :expires, :now)
    ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed', 'partial_accept')
    DO NOTHING
    RETURNING id
""")

# ON CONFLICTで飛んだ場合の既存開いている行特定(design §2.3)
_FIND_OPEN_LATCH = text("""
    SELECT id, status FROM latches
    WHERE intent_ids = ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]
      AND status IN ('candidate', 'proposed', 'partial_accept')
""")

# 昇格時のcandidate行更新(score/proposal/deadlineの3列のみ — latchesに
# updated_at列なし。design §2.3)
_UPDATE_FOR_PROMOTION = text("""
    UPDATE latches
    SET score = :score, proposal = CAST(:proposal AS jsonb),
        response_deadline = :deadline
    WHERE id = CAST(:latch_id AS uuid) AND status = 'candidate'
    RETURNING id
""")

# try_promote手順1: 行ロック(design §2.6・06 §6の直列化方式)
_SELECT_LATCH_FOR_UPDATE = text("""
    SELECT id, status, intent_ids, expires_at
    FROM latches WHERE id = CAST(:latch_id AS uuid)
    FOR UPDATE
""")

# latch_status_events挿入(from_status・user_idはNULL可 — 05 §2・引用#16)
_INSERT_LATCH_EVENT = text("""
    INSERT INTO latch_status_events
        (latch_id, from_status, to_status, user_id, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:from_status AS text),
            :to_status, CAST(:user_id AS uuid), :now)
""")

# D-08日次上限カウント(真実はnotifications・design §2.6。0時リセットは
# day_start/day_nextの日付条件の動的切り替えで成立 — カウンタリセットジョブ不要)
_COUNT_DAILY_NOTIFICATIONS = text("""
    SELECT user_id, COUNT(*) FROM notifications
    WHERE user_id IN (CAST(:u0 AS uuid), CAST(:u1 AS uuid))
      AND type IN ('proposal', 'nearby_candidate')
      AND created_at >= CAST(:day_start AS timestamptz)
      AND created_at < CAST(:day_next AS timestamptz)
    GROUP BY user_id
""")

# D-08同時進行上限(muted参加Intentもproposed数に計上 — 引用#10)
_COUNT_OPEN_PROPOSED = text("""
    SELECT COUNT(*) FROM latches
    WHERE status IN ('proposed', 'partial_accept')
      AND intent_ids @> ARRAY[CAST(:intent_id AS uuid)]
""")

# 75分/deadline<=nowの破棄(条件付きUPDATE — design §2.6手順2)
_EXPIRE_LATCH = text("""
    UPDATE latches SET status = 'expired'
    WHERE id = CAST(:latch_id AS uuid) AND status = 'candidate'
    RETURNING id
""")

# proposed遷移(提示時D-05再計算・条件付きUPDATE — design §2.6手順6)
_PROMOTE_LATCH = text("""
    UPDATE latches
    SET status = 'proposed', response_deadline = CAST(:deadline AS timestamptz)
    WHERE id = CAST(:latch_id AS uuid) AND status = 'candidate'
    RETURNING id
""")

# notifications INSERT(payloadは{"latch_id"}の最小参照 — 承認事項2)
_INSERT_NOTIFICATION = text("""
    INSERT INTO notifications (user_id, type, payload, created_at)
    VALUES (CAST(:user_id AS uuid), :type, CAST(:payload AS jsonb), :now)
""")

# drain: 提示順(対象時刻昇順・score降順・同点latch_id昇順)。nearby行
# (score < 閾値)は対象外・期限切れ行は対象外(design §2.6)
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

(`_DRAIN_CANDIDATES` の `ANY(l.intent_ids)` は**列側**の演算(bind paramではない)ため§2の要素明示形式の対象外。`_COUNT_DAILY_NOTIFICATIONS` の `type IN ('proposal', 'nearby_candidate')` はSQLリテラル — 定数と同一値をunit試験がピンで突合する)

### 9-5. LatchEngine の構成(クラス・モジュール関数・手順)

```python
NOTIFICATION_PROPOSAL = "proposal"
NOTIFICATION_NEARBY = "nearby_candidate"


@dataclass(frozen=True)
class LatchTargetRow:
    """_SELECT_TARGETSの1行。"""

    row_id: uuid.UUID
    intent_a_id: uuid.UUID
    intent_b_id: uuid.UUID
    jev_result: dict  # _parse_structured済み(Gateway検証済みの生JSONB読取のみ)


@dataclass(frozen=True)
class Participant:
    """try_promote用の参加Intent情報(同一tx内で再読取)。"""

    intent_id: uuid.UUID
    user_id: uuid.UUID
    notification_level: str
    time_start: datetime
    expires_at: datetime
```

**conn受取りのモジュール関数**(unit試験のmonkeypatch対象。asyncpgのUUID対策は `_coerce_uuid`(origin.pyと同一規律でlatch_engine内に置く・origin.pyからimportしない〔変更禁止〕)):

- `_select_target_rows(conn, origin_id, origin_version) -> list[LatchTargetRow]`
- `_read_intent_inputs(conn, intent_id) -> LatchIntentInputs | None`(structured_dataがstrならjson.loads〔embedding.pyと同一規律〕・`category.secondary` からcategory_secondaryを取り出す・expires_atがNULLの行はNoneを返す〔latches.expires_at NOT NULLのため対象外〕)
- `_close_h_broken(conn, row_id, now) -> bool`(RETURNINGなければFalse)
- `_record_score(conn, row_id, score, now) -> tuple[uuid.UUID, float | None] | None`(None=競合負け)
- `_read_latch_responses(conn, a_id, b_id) -> list[dict]`(responsesがstrならjson.loads)
- `_insert_latch(conn, *, a_id, b_id, proposal, score, deadline, expires, now) -> uuid.UUID | None`(None=ON CONFLICTで飛んだ)
- `_find_open_latch(conn, a_id, b_id) -> tuple[uuid.UUID, str] | None`
- `_update_for_promotion(conn, latch_id, *, score, proposal, deadline) -> bool`
- `_select_latch_for_update(conn, latch_id) -> tuple | None`(id, status, intent_ids(listへ復元), expires_at)
- `_read_participants(conn, a_id, b_id) -> list[Participant]`(両者の `_SELECT_INTENT_INPUTS`・expiry/level/time_startを返す)
- `_insert_latch_event(conn, latch_id, from_status, to_status, user_id, now) -> None`(user_id=Noneでシステム起因)
- `_count_daily_notifications(conn, user_ids, day_start, day_next) -> dict[uuid.UUID, int]`(user_idsは2要素固定・u0/u1へ展開)
- `_count_open_proposed(conn, intent_id) -> int`
- `_expire_latch(conn, latch_id, now) -> bool`
- `_promote_latch(conn, latch_id, deadline, now) -> bool`
- `_insert_notification(conn, user_id, ntype, latch_id, now) -> None`(payloadは `json.dumps({"latch_id": str(latch_id)})`)
- `_drain_candidates(engine, now, threshold) -> list[uuid.UUID]`(engine.begin()内包の短tx)

**LatchEngineクラス**:

```python
class LatchEngine:
    """Layer 5本体(06 §6・§10・design §2.1案A・§2.9の表)。

    handle(intent_id) は起点非依存IF(JevWorkerと同型・Bucket/catch-upから
    同一部品を呼ぶ)。冪等: 選択SQLのlatch_score IS NULL・ON CONFLICT・
    条件付きUPDATE。モジュール属性経由で origin/layer4/latch_calc/proposal
    を呼ぶ(runnerと同一規律・unit試験がmonkeypatchで差し替え可能)。
    ws-7拡張点: latches生成・proposal・drain・try_promoteは1対1構成。
    """

    def __init__(self, *, engine, clock, geo=None) -> None:
        self._engine = engine
        self._clock = clock
        self._geo = geo  # GeoService | None(Noneならarea_name=None)

    async def handle(self, intent_id: uuid.UUID) -> None: ...
    async def _evaluate_pair(self, org: Origin, row: LatchTargetRow) -> None: ...
    async def _try_promote(self, latch_id: uuid.UUID) -> None: ...
    async def _drain(self) -> None: ...
    async def _area_name(self, a: LatchIntentInputs, b: LatchIntentInputs) -> str | None: ...
```

**handle の手順**(design §2.9のtx分割):

1. フェーズ1(短tx): `origin_mod.load_origin(conn, clock, intent_id)` → skip_reason/origin Noneなら構造化ログ(`latch origin no-op intent_id=%s reason=%s`)を出してreturn。`_select_target_rows(conn, org.intent_id, org.version)`
2. 各行へ `_evaluate_pair(org, row)`(直列)
3. 末尾で `_drain()`(評価のたびに提示を回す — 引用#17)

**_evaluate_pair の手順**:

1. `now = clock.now()`・`origin_is_a = row.intent_a_id == org.intent_id`・`peer_id` を解決・`a_id, b_id = sorted([org.intent_id, peer_id])`(intent_ids正規化 — Python側sorted・design §2.3)
2. tx1(`engine.begin()`): `layer4.hard_constraint_holds(conn, org, peer_id)` → Falseなら `_close_h_broken(conn, row.row_id, now)` してreturn(belt-and-suspenders・1ペア1SELECT)。`_mutual_score(row.jev_result)`(=min(would_a_accept_b, would_b_accept_a)・jev_result生JSONB読取のみ)・`score = LATCH_C * mutual`・`rec = _record_score(conn, row.row_id, score, now)` → Noneならreturn(競合負け)。`(_, prev_latch_score) = rec`
3. tx外: `origin_inputs = await 短txで_read_intent_inputs(engine, org.intent_id)`・`peer_inputs = 同(engine, peer_id)`(どちらかNoneならreturn — 相手削除・期限NULL)。`target = pair_target_time(origin_inputs.time_start, peer_inputs.time_start)`・`min_expires = min(origin_inputs.expires_at, peer_inputs.expires_at)`・`area = await self._area_name(origin_inputs, peer_inputs)`(geoがNoneならNone・tx外読取)
4. 履歴読取(短tx): `responses = _read_latch_responses(engine, a_id, b_id)`・`has_no, latest_defer_at = latch_calc.d07_history_inputs(responses)`
5. **閾値判定** `score >= LATCH_THRESHOLD`:
   - **提案経路**: `d07_allows(has_no_response=has_no, latest_defer_at=latest_defer_at, now=now, target_time=target, new_score=score, prev_latch_score=prev_latch_score)` → Falseなら構造化ログ(`latch d07 denied a=%s b=%s`)でreturn。`proposal = proposal_mod.build_proposal(origin=origin_inputs, peer=peer_inputs, score=score, area_name=area)`・`deadline0 = latch_calc.response_deadline(now, target, min_expires)`(暫定値)。tx2: `latch_id = _insert_latch(...)`:
     - latch_id非None: `_insert_latch_event(latch_id, None, 'candidate', None, now)`(システム起因=Layer 5のためuser_id=NULL)→ `_try_promote(latch_id)`
     - latch_id None(ON CONFLICT): `_find_open_latch(conn, a_id, b_id)` → Noneならreturn・status != 'candidate'なら構造化ログ(`latch open row exists latch_id=%s status=%s`)でreturn・candidateなら **昇格判定**: `d07_allows(...)`(同一入力)を再度呼び通るなら `_update_for_promotion(conn, lid, score=score, proposal=proposal, deadline=deadline0)`(成功時のみ)→ `_try_promote(lid)`。通らないなら何もしない(ログ)
   - **nearby経路**(score < LATCH_THRESHOLD): 通知対象 = `[inp for inp in (origin_inputs, peer_inputs) if inp.notification_level == 'nearby_also']`。対象なし→return(latches行を作らない)。**has_no=True→return**(存在通知も出さない — 承認済み解釈)。tx2: `latch_id = _insert_latch(conn, a_id, b_id, proposal=proposal_mod.nearby_proposal(), score=score, deadline=deadline0, expires=min_expires, now=now)` → Noneならreturn(開いている行あり・閾値未満評価では既存行を更新しない)。`_insert_latch_event(latch_id, None, 'candidate', None, now)`。存在通知: `day_start = layer4.jst_day_start(clock.jst_date())`・`day_next = day_start + timedelta(days=1)`・`counts = _count_daily_notifications(conn, [u.user_id for u in 通知対象], day_start, day_next)`・各対象者 `counts.get(uid, 0) < D08_DAILY_LIMIT` なら `_insert_notification(conn, uid, NOTIFICATION_NEARBY, latch_id, now)`・超過なら構造化ログ(`latch nearby daily limit uid=%s`・切り捨て)。**nearbyはtry_promoteしない**(proposed遷移しない・引用#9)

**_try_promote(latch_id) の手順**(1tx・design §2.6。**Review Focus 4の手順順序**):

1. tx(`engine.begin()`): `row = _select_latch_for_update(conn, latch_id)` → None または `row.status != 'candidate'` ならreturn
2. `now = clock.now()`(FOR UPDATE取得後に採取)・`parts = _read_participants(conn, a, b)`(row.intent_idsの2要素)・`target = max(p.time_start for p in parts)`・`min_expires = min(p.expires_at for p in parts)`
3. **75分ルール**: `target - now < PROMPT_LEAD_MIN` → `_expire_latch(conn, latch_id, now)`+`_insert_latch_event(conn, latch_id, 'candidate', 'expired', None, now)` してreturn(通知ゼロ)
4. `row.expires_at <= now` → 何もせずreturn(expiry_sweeper=M3-3の担当・ここでは対象外化のみ)
5. `deadline = response_deadline(now, target, min_expires)`・**`deadline <= now` なら手順3と同一扱い**(75分ルールと同一 — 引用#4。防御)
6. 通知対象 `notify = [p for p in parts if p.notification_level != 'muted']`。**D-08日次**: `day_start = jst_day_start(clock.jst_date())`・`day_next = day_start + timedelta(days=1)`・`counts = _count_daily_notifications(conn, [p.user_id for p in notify], day_start, day_next)`・`any(counts.get(p.user_id, 0) >= D08_DAILY_LIMIT for p in notify)` ならreturn(**candidateのまま**・破棄しない)
7. **D-08同時**: 各p(muted含む全参加者 — proposed数に計上)で `_count_open_proposed(conn, p.intent_id) >= D08_CONCURRENT_LIMIT` ならreturn(candidateのまま)
8. `_promote_latch(conn, latch_id, deadline, now)` → Falseならreturn(他経路で遷移済み)
9. `_insert_latch_event(conn, latch_id, 'candidate', 'proposed', None, now)`・notify全員へ `_insert_notification(conn, p.user_id, NOTIFICATION_PROPOSAL, latch_id, now)`(mutedは書かない=上限も消費しない)

**_drain の手順**: `now = clock.now()`・`ids = _drain_candidates(engine, now, LATCH_THRESHOLD)`・各行へ `_try_promote(latch_id)`(行毎に独立tx・上限に当たった行はcandidateのまま次へ)

**_area_name**: `self._geo is None` ならNone。`await self._geo.reverse_geocode((a.geo_lon + b.geo_lon) / 2, (a.geo_lat + b.geo_lat) / 2)`(中点・承認済み解釈)

**例外方針**(design §2.9の表・関数からは握らない): DB書き込み失敗・JevCostDependencyError(ReevalRunner経由のreevalガード)は伝播。`_kick_jev` 経由なら_dispatch/_on_releaseの既存except→ackなし再配信→duplicate経由で再実行(ガードで冪等)。地物なしはarea_name=Noneで続行

### 9-6. ReevalRunner(worker/reeval.py)

```python
"""再評価Runner(06 §9・design §2.8)。BackfillRunnerと同型の周期ジョブ。

catch-upスキャン(expires_atまで2時間以内のactive)と30分Bucket再評価
(time_startが当該Bucketに属するIntent・design §2.8-2の読み)を抽出し、
reevalガード(ws-4資産・入口で共用)を通してpipelineを直接投入する
(Eventは発行しない — 同一versionのidempotencyキー衝突のため・06 §9)。
sleep-first・stop追従・run_once内の例外は握らずrun()が握って次周期で回収。
M3-3がexpiry_sweeperを同一周期へ統合できるよう独立クラスにする。
"""

_SELECT_CATCHUP_TARGETS = text("""
    SELECT id FROM intents
    WHERE status = 'active'
      AND embedding IS NOT NULL
      AND expires_at IS NOT NULL
      AND expires_at > CAST(:now AS timestamptz)
      AND expires_at <= CAST(:now AS timestamptz) + interval '2 hours'
    ORDER BY expires_at, id
    LIMIT :batch_limit
""")

_SELECT_BUCKET_TARGETS = text("""
    SELECT id FROM intents
    WHERE status = 'active'
      AND embedding IS NOT NULL
      AND time_start >= CAST(:bucket_start AS timestamptz)
      AND time_start < CAST(:bucket_end AS timestamptz)
    ORDER BY id
""")


class ReevalRunner:
    """周期ごとにcatch-up+Bucket対象をpipelineへ渡す(design §2.8)。"""

    def __init__(
        self,
        *,
        engine,
        clock,
        guard,  # ReevalGuard | None(Noneならガード判定をスキップ)
        pipeline,  # Callable[[uuid.UUID], Awaitable[None]]
        interval_sec: float,
        batch_limit: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._guard = guard
        self._pipeline = pipeline
        self._interval_sec = interval_sec
        self._batch_limit = batch_limit
        self._sleep = sleep
        self._last_bucket: datetime | None = None  # 前回処理Bucket(メモリ保持)

    async def run_once(self) -> int:
        """1周期分: catch-up抽出→Bucket処理→直接投入。例外は握らない。"""
        now = self._clock.now()
        ids = await _select_catchup_targets(self._engine, now, self._batch_limit)
        b_start = latch_calc.bucket_start(now)
        if self._last_bucket is None or b_start > self._last_bucket:
            b_end = b_start + timedelta(minutes=latch_calc.BUCKET_MINUTES)
            ids = ids + await _select_bucket_targets(self._engine, b_start, b_end)
            self._last_bucket = b_start
        done = 0
        for intent_id in ids:
            if self._guard is not None and not await self._guard.allow(intent_id):
                continue  # 30分以内の再評価(06 §5(c)・抽出条件とガードで結果は等しい)
            await self._pipeline(intent_id)
            done += 1
        return done

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """sleep-first周期ループ(BackfillRunnerと同一)。"""
        while stop is None or not stop.is_set():
            await self._wait_interval(stop)
            if stop is not None and stop.is_set():
                break
            try:
                await self.run_once()
            except Exception:
                logger.warning("reeval run_once failed", exc_info=True)

    async def _wait_interval(self, stop: asyncio.Event | None) -> None:
        if stop is None:
            await self._sleep(self._interval_sec)
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=self._interval_sec)
        except TimeoutError:
            pass
```

- 「直近の詳細評価から30分以上経過」の抽出条件はSQLに入れない — reevalガードが入口で同じ意味をアトミックに判定・消費する(引継ぎ・design §2.8-1)
- 重複(catch-upとBucketの両方に同一Intent): pipeline側のガード(latch_score IS NULL・ON CONFLICT・reevalガード)で冪等。ただし同一run_once内で2回pipelineを呼ばないよう、Bucket対象のうちcatch-up済みIDを除く(単純に `seen` setで重複排除を1行入れる)
- `_select_catchup_targets`/`_select_bucket_targets` はengine.begin()を内包するモジュール関数(unit試験のmonkeypatch対象・`_coerce_uuid`でUUID復元)

### 9-7. Worker配線(worker/main.py)

`Worker.__init__` へ引数追加(既定Noneで従来互換 — design §3.2):

```python
latch: LatchEngine | None = None,
reeval_runner: ReevalRunner | None = None,
```

import追加: `from latch.geo.service import GeoService`・`from latch.worker.matching.latch_engine import LatchEngine`・`from latch.worker.reeval import ReevalRunner`

`run()` 内のDI(JevWorker構築の後に・gateway構築は既存のまま):

```python
# LatchEngine DI(M2 ws-6・design §2.1案A): JevWorker直後のLayer 5。
# redis非依存のため常に構築(注入済み資産は再構築しない)
if self._latch is None:
    self._latch = LatchEngine(engine=engine, clock=self.clock, geo=GeoService(engine))
# ReevalRunner DI(design §2.8): catch-up・Bucket再評価の周期task。
# pipelineはengineを閉包した直接投入(_run_direct_pipeline)
if self._reeval_runner is None and redis_client is not None:
    async def _pipeline(intent_id: uuid.UUID) -> None:
        await self._run_direct_pipeline(engine, intent_id)

    self._reeval_runner = ReevalRunner(
        engine=engine,
        clock=self.clock,
        guard=self._reeval,  # この時点でrun()内構築済み(ReevalGuard)
        pipeline=_pipeline,
        interval_sec=self.settings.reeval_runner_interval_sec,
        batch_limit=self.settings.reeval_runner_batch_limit,
    )
```

task起動・shutdown待ち(backfill_taskと同型に追加):

```python
reeval_task = asyncio.create_task(reeval_runner.run(stop=self._stop))
...
await reeval_task
```

(reeval_runnerは `self._reeval_runner if self._latch is not None` 系の分岐でなく、run()内でローカル変数 `reeval_runner = self._reeval_runner` を受け、taskは `reeval_runner is not None` のときのみ起動・awaitする。redis不使用構成〔unit試験〕ではNoneで起動しない)

`_kick_jev` の変更(design §2.1案A・docstring更新):

```python
async def _kick_jev(
    self, event_type: str, intent_id: uuid.UUID, version: int
) -> None:
    """Stage1処理コミット後・ack前のLayer 4→Layer 5キック(design §2.1案A)。

    embedding_completedのみ(06 §1)。JevWorker完了後にLatchEngineを直列実行
    (Layer 5+通知の層別予算≤2秒を1連の流れで守る)。DB失敗はここから伝播
    して_dispatch/_on_releaseの既存exceptが受け、ackなし再配信が回収する
    (冪等ガード latch_score IS NULL・ON CONFLICT・条件付きUPDATE)。
    未注入(ws-1/ws-5資産の試験)はそれぞれ何もしない。version引数はhandleが
    起点読取で再検証するため使わない(IFは起点非依存)。
    """
    if event_type != EVENT_EMBEDDING_COMPLETED:
        return
    if self._jev is not None:
        await self._jev.handle(intent_id)
    if self._latch is not None:
        await self._latch.handle(intent_id)
```

`_run_direct_pipeline` の新設(design §2.8-3・ReevalRunnerのpipeline本体):

```python
async def _run_direct_pipeline(self, engine, intent_id: uuid.UUID) -> None:
    """Bucket/catch-up起点の直接投入(design §2.8-3・Eventを発行しない)。

    L1〜3を自前トランザクションで実行し、コミット後にLayer 4・Layer 5を
    直列キック(stage1の_run_matchingはstage1トランザクションに同乗する
    構造のため流用しない)。例外は握らずRunnerへ伝播(Runnerが握って次周期
    で回収)。冪等は各部のガードで担保済み。
    """
    async with engine.begin() as conn:
        await run_candidate_retrieval(conn, self.clock, intent_id)
    if self._jev is not None:
        await self._jev.handle(intent_id)
    if self._latch is not None:
        await self._latch.handle(intent_id)
```

### 9-8. settings.py・マイグレーション0004

settings.py(embedding_backfill並びの後に追加):

```python
    # --- 再評価Runner(M2 ws-6・06 §9・design §2.8)---
    # catch-upスキャン・30分Bucket再評価の周期と1周期あたりの投入上限
    # (60秒はexpiry_sweeperと同一周期・06 §9。初期値。計測後に調整)
    reeval_runner_interval_sec: int = 60
    reeval_runner_batch_limit: int = 50
```

マイグレーション0004(0001〜0003と同じ `op.execute` 形式・DB時刻関数のDEFAULTなし):

```python
"""latchesへ開いている行の部分UNIQUE索引を追加(M2 ws-6・design §2.3)。

同一メンバー集合(intent_ids)の開いている(status IN ('candidate',
'proposed','partial_accept'))latchesを1行に強制する。at-least-once再実行の
冪等(ON CONFLICT DO NOTHING)とnearby行の昇格(既存candidate行の更新として
実装)の両要件をDBで担保する最小構成(supervisor承認事項1・2026-09-29)。
閉じた行(expired/rejected/cancelled/matched/completed)は制約対象外のため
D-07検査を通れば新規latchesを作れる。05 §2への追記は次回docs改版に含める
(ws-5 skip_reason前例)。

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_latches_intent_ids_open ON latches (intent_ids)"
        " WHERE status IN ('candidate', 'proposed', 'partial_accept')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_latches_intent_ids_open")
```

### 9-9. ピン試験の「ちょうどN項目」対処(§0で機械確認済み)

- settings.pyへ追加する2項目は `llm_` / `geo_` / `auth_` いずれのprefixにも一致しないため、`test_llm_settings_are_exactly_nine_fields`・`test_geo_settings_are_exactly_three_fields`・`test_auth_settings_are_exactly_eight_fields` のいずれにも**干渉しない(期待値更新不要)**。`test_send_record_fields_are_exactly_the_allowlist` はSendRecordのフィールド許可リストでSettingsと無関係
- Task 7実装後に `make test` 全件グリーンで確認し、報告書§7の完了条件7へ件数を記録する

### 9-10. design §5 実装時確認事項2件の確認手順

- **§5-9(notifications.typeのCHECK制約)**: 計画時点で0001定義を直接確認済み(`type text NOT NULL` のみ・CHECKなし)。Task 4 Step 1で `rg -n "CHECK" backend/alembic/versions/0001_initial_schema.py` を実行しnotificationsテーブル部(209〜221行目付近)にCHECKがないことを再確認する。**CHECKが存在していた場合は作業を停止してBLOCKEDとして報告する**(docsにtype値域の規定なし・想定はフリーtext)。結果は報告書「design §5 実装時確認事項の結果」へ記録
- **§5-10(uuid[]部分UNIQUE索引のON CONFLICT推論)**: unit試験では `_INSERT_LATCH` のSQL文字列ピン(`ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed', 'partial_accept') DO NOTHING`)で構文を担保する。実DBでの推論確認はスーパーバイザー検証時のintegration試験1・9が担う(handle 2回でlatches二重なし=推論とON CONFLICTが実効している証拠)。**推論が外れてintegrationが失敗する場合はINSERT前SELECT FOR UPDATEへの切替をsupervisorへ相談(BLOCKED報告)**

## 8. 実装ステップ(TDD。Task 1〜8の順で実行する)

### Task 1: マイグレーション0004(latches部分UNIQUE索引)

**Files:**
- Create: `backend/alembic/versions/0004_latches_open_unique.py`

**Interfaces:**
- Consumes: なし
- Produces(Task 4・integrationが使用): 部分UNIQUE索引 `uq_latches_intent_ids_open`(同一intent_idsの開いているlatchesを1行に強制・`ON CONFLICT (intent_ids) WHERE status IN ('candidate','proposed','partial_accept') DO NOTHING` の推論対象)

- [ ] **Step 1: 実装する**

`backend/alembic/versions/0004_latches_open_unique.py` を作成。内容は§9-8のとおり(revision="0004"・down_revision="0003"・`op.execute` のCREATE UNIQUE INDEX … WHERE 1文+downgradeのDROP INDEX)。

- [ ] **Step 2: チェーンの静的確認(`make migrate` は実行禁止・§0)**

Run:
```bash
cd backend && uv run python - <<'EOF'
from alembic.config import Config
from alembic.script import ScriptDirectory
sd = ScriptDirectory.from_config(Config("alembic.ini"))
heads = sd.get_heads()
assert heads == ["0004"], heads
walk = [rev.revision for rev in sd.walk_revisions()]
assert walk == ["0004", "0003", "0002", "0001"], walk
print("OK heads=", heads)
EOF
```
Expected: `OK heads= ['0004']`(チェーン接続の静的確認)
Run: `cd backend && uv run pytest --collect-only tests/integration/test_schema.py -q`
Expected: 収集成功(実DB検証はtest-ci=スーパーバイザー検証待ち)

- [ ] **Step 3: コミット**

```bash
make lint && make test
git add backend/alembic/versions/0004_latches_open_unique.py
git commit -m "feat: マイグレーション0004(latches開いている行の部分UNIQUE索引)"
```

### Task 2: latch_calc.py(純関数群・定数)

**Files:**
- Create: `backend/src/latch/worker/matching/latch_calc.py`
- Test: `backend/tests/unit/matching/test_latch_calc.py`

**Interfaces:**
- Consumes: `layer4.jst_day_start`(importして再export — layer4.pyは変更しない)・`core.clock.JST`
- Produces(Task 3〜7が使用 — §9-2のとおり):
  - 定数: `LATCH_THRESHOLD=0.80` / `LATCH_C=1.0` / `D07_DELTA=0.05` / `D08_DAILY_LIMIT=6` / `D08_CONCURRENT_LIMIT=3` / `MATCH_LEVEL_HIGH_MIN=0.90` / `PROMPT_LEAD_MIN=timedelta(minutes=75)` / `DEADLINE_MIN_AFTER_NOTIFY=timedelta(minutes=15)` / `DEADLINE_MAX_AFTER_NOTIFY=timedelta(hours=2)` / `DEADLINE_BEFORE_START=timedelta(minutes=60)` / `DEFER_SUPPRESS_MAX=timedelta(hours=24)` / `BUCKET_MINUTES=30` / `RESPONSE_NO="no"` / `RESPONSE_DEFER="defer"`
  - `response_deadline(now, target_time, min_expires_at) -> datetime`
  - `match_level(score) -> str`
  - `pair_target_time(time_start_a, time_start_b) -> datetime`
  - `d07_history_inputs(responses: list[dict]) -> tuple[bool, datetime | None]`
  - `d07_allows(*, has_no_response, latest_defer_at, now, target_time, new_score, prev_latch_score) -> bool`
  - `bucket_start(now) -> datetime`(JST tz-awareを返す)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_latch_calc.py` を作成(design §4.1-1〜4・すべて決定的)。基準時刻は `NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)`(JST 21:00)を共用:

```python
"""latch_calc純関数のunit試験(design §4.1・06 §6/§10・03 D-05/D-07)。"""

from datetime import UTC, datetime, timedelta

from latch.core.clock import JST
from latch.worker.matching.latch_calc import (
    BUCKET_MINUTES,
    D07_DELTA,
    D08_CONCURRENT_LIMIT,
    D08_DAILY_LIMIT,
    LATCH_C,
    LATCH_THRESHOLD,
    MATCH_LEVEL_HIGH_MIN,
    PROMPT_LEAD_MIN,
    bucket_start,
    d07_allows,
    d07_history_inputs,
    match_level,
    pair_target_time,
    response_deadline,
)

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)  # JST 2026-10-01 21:00
TARGET = NOW + timedelta(hours=30)
EXPIRES = NOW + timedelta(days=5)
DEFER_AT = NOW - timedelta(minutes=10)


def test_constants_pin_docs_values():
    assert LATCH_THRESHOLD == 0.80  # 06 §6(D-01)
    assert LATCH_C == 1.0  # 01 §14・C初期値
    assert D07_DELTA == 0.05  # 06 §10
    assert D08_DAILY_LIMIT == 6  # 03 D-08
    assert D08_CONCURRENT_LIMIT == 3  # 03 D-08
    assert MATCH_LEVEL_HIGH_MIN == 0.90  # 05 §2
    assert PROMPT_LEAD_MIN == timedelta(minutes=75)  # 06 §10
    assert BUCKET_MINUTES == 30  # 06 §9


def test_deadline_two_hour_cap_applies():
    # 対象まで4時間 → min(now+2h, target-60m)=now+2h・max(now+15m, now+2h)=now+2h
    d = response_deadline(NOW, NOW + timedelta(hours=4), EXPIRES)
    assert d == NOW + timedelta(hours=2)


def test_deadline_inner_min_applies():
    # 対象まで1時間20分 → min(now+2h, now+20m)=now+20m・max(now+15m, now+20m)=now+20m
    d = response_deadline(NOW, NOW + timedelta(minutes=80), EXPIRES)
    assert d == NOW + timedelta(minutes=20)


def test_deadline_15min_floor_when_target_very_close():
    # 対象まで70分 → min(now+2h, now+10m)=now+10m・max(now+15m, now+10m)=15分下限
    d = response_deadline(NOW, NOW + timedelta(minutes=70), EXPIRES)
    assert d == NOW + timedelta(minutes=15)


def test_deadline_expires_at_is_minimum():
    d = response_deadline(NOW, NOW + timedelta(hours=4), NOW + timedelta(minutes=30))
    assert d == NOW + timedelta(minutes=30)  # expires_atが最少


def test_deadline_can_be_past_notify_time():
    # expiresがnow+5分 → 期限が通知時刻を過ぎる(75分ルールと同一扱いの対象)
    d = response_deadline(NOW, NOW + timedelta(hours=4), NOW + timedelta(minutes=5))
    assert d <= NOW


def test_match_level_boundaries():
    assert match_level(0.90) == "high"
    assert match_level(0.95) == "high"
    assert match_level(0.80) == "medium"  # 閾値ちょうどは提案(medium)
    assert match_level(0.899) == "medium"
    assert match_level(0.79) == "low"
    assert match_level(0.70) == "low"


def test_pair_target_time_takes_max():
    a = NOW + timedelta(hours=1)
    b = NOW + timedelta(hours=2)
    assert pair_target_time(a, b) == b  # max(参加Intentのtime_start)
    assert pair_target_time(b, a) == b


def test_d07_no_history_denies():
    assert d07_allows(
        has_no_response=True, latest_defer_at=None, now=NOW,
        target_time=TARGET, new_score=0.9, prev_latch_score=None,
    ) is False  # no=Intent期限まで再提案しない


def test_d07_no_defer_allows():
    assert d07_allows(
        has_no_response=False, latest_defer_at=None, now=NOW,
        target_time=TARGET, new_score=0.9, prev_latch_score=0.9,
    ) is True  # 無回答期限切れ等は抑制しない(仕様にない抑制を足さない)


def test_d07_defer_new_generation_allows_within_suppression():
    # 抑制期間内でも新評価世代(prev=None)は無条件変化あり(引用#7手順5)
    assert d07_allows(
        has_no_response=False, latest_defer_at=DEFER_AT, now=NOW,
        target_time=TARGET, new_score=0.85, prev_latch_score=None,
    ) is True


def test_d07_defer_small_delta_denies_within_suppression():
    assert d07_allows(
        has_no_response=False, latest_defer_at=DEFER_AT, now=NOW,
        target_time=TARGET, new_score=0.85, prev_latch_score=0.83,
    ) is False  # |Δ|=0.02 < 0.05


def test_d07_defer_delta_threshold_allows_within_suppression():
    assert d07_allows(
        has_no_response=False, latest_defer_at=DEFER_AT, now=NOW,
        target_time=TARGET, new_score=0.90, prev_latch_score=0.85,
    ) is True  # |Δ|=0.05 ≧ 0.05(境界)


def test_d07_defer_suppression_elapsed_allows():
    # 対象まで3時間 → min(24h, 1.5h)=1.5h。deferから2時間経過=抑制期間経過
    assert d07_allows(
        has_no_response=False, latest_defer_at=NOW - timedelta(hours=2), now=NOW,
        target_time=NOW + timedelta(hours=3), new_score=0.85,
        prev_latch_score=0.85,
    ) is True


def test_d07_defer_24h_cap():
    # 対象まで3日 → min(24h, 36h)=24h。deferから30時間経過=経過
    assert d07_allows(
        has_no_response=False, latest_defer_at=NOW - timedelta(hours=30), now=NOW,
        target_time=NOW + timedelta(hours=72), new_score=0.85,
        prev_latch_score=0.85,
    ) is True


def test_d07_history_inputs_extracts_no_and_latest_defer():
    responses = [
        {"user_id": "u1", "response": "defer", "answered_at": "2026-10-01T11:40:00+00:00"},
        {"user_id": "u2", "response": "defer", "answered_at": "2026-10-01T11:50:00+00:00"},
        {"user_id": "u1", "response": "yes", "answered_at": "2026-10-01T11:55:00+00:00"},
    ]
    has_no, latest = d07_history_inputs(responses)
    assert has_no is False
    assert latest == datetime(2026, 10, 1, 11, 50, 0, tzinfo=UTC)  # answered_at最大のdefer


def test_d07_history_inputs_no_and_empty():
    has_no, latest = d07_history_inputs(
        [{"user_id": "u1", "response": "no", "answered_at": "2026-10-01T11:40:00+00:00"}]
    )
    assert has_no is True and latest is None
    assert d07_history_inputs([]) == (False, None)


def test_bucket_start_boundaries():
    # JST 21:00→分0(00分台は切り下げ0)・JST 21:29:59→21:00・JST 21:30ちょうど→21:30
    assert bucket_start(datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)) == datetime(
        2026, 10, 1, 21, 0, 0, tzinfo=JST
    )
    assert bucket_start(datetime(2026, 10, 1, 12, 29, 59, tzinfo=UTC)) == datetime(
        2026, 10, 1, 21, 0, 0, tzinfo=JST
    )
    assert bucket_start(datetime(2026, 10, 1, 12, 30, 0, tzinfo=UTC)) == datetime(
        2026, 10, 1, 21, 30, 0, tzinfo=JST
    )
    # UTC 15:45 = JST 00:45(日付跨ぎ)→ JST 00:30
    assert bucket_start(datetime(2026, 10, 1, 15, 45, 0, tzinfo=UTC)) == datetime(
        2026, 10, 2, 0, 30, 0, tzinfo=JST
    )


def test_jst_day_start_reexported_from_layer4():
    from latch.worker.matching import layer4
    from latch.worker.matching.latch_calc import jst_day_start

    assert jst_day_start is layer4.jst_day_start  # 再実装しない(Review Focus 5)
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_calc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.worker.matching.latch_calc'`

- [ ] **Step 3: 実装する**

`backend/src/latch/worker/matching/latch_calc.py` を作成。内容は§9-2全文のとおり。

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_calc.py -v`
Expected: PASS(全項目)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/latch_calc.py backend/tests/unit/matching/test_latch_calc.py
git commit -m "feat: latch_calc純関数群(D-05式・match_level・D-07判定・bucket_start)"
```

### Task 3: proposal.py(visibility分岐・nearby最小構造)

**Files:**
- Create: `backend/src/latch/worker/matching/proposal.py`
- Test: `backend/tests/unit/matching/test_proposal.py`

**Interfaces:**
- Consumes: `latch_calc.match_level`・`latch_calc.pair_target_time`・`core.clock.JST`(Task 2)
- Produces(Task 4・integrationが使用):
  - `LatchIntentInputs`(frozen dataclass — §9-3のフィールド一式)
  - `build_proposal(*, origin, peer, score, area_name) -> dict`
  - `nearby_proposal() -> dict`・`NEARBY_HEADCOUNT = 2`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_proposal.py` を作成(design §4.1・確定値の全文一致。importへ `from datetime import UTC, datetime, timedelta` を含める):

```python
"""proposal生成のunit試験(05 §2・08 §2.3・design §2.5)。"""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

from latch.worker.matching.proposal import (
    LatchIntentInputs,
    build_proposal,
    nearby_proposal,
)

T0 = datetime(2026, 10, 2, 11, 0, 0, tzinfo=UTC)  # JST 2026-10-02 20:00


def _inp(n: int = 1, **over) -> LatchIntentInputs:
    base = dict(
        intent_id=uuid.UUID(f"00000000-0000-4000-8000-{n:012d}"),
        user_id=uuid.UUID(f"00000000-0000-4000-8000-{n + 50:012d}"),
        visibility="summary_only",
        notification_level="proposals_only",
        time_start=T0,
        expires_at=T0 + timedelta(days=5),
        budget_max=5000,
        category_primary="drinking",
        category_secondary="ビール",
        geo_lon=130.558,
        geo_lat=31.596,
    )
    base.update(over)
    return LatchIntentInputs(**base)


def test_both_summary_only_builds_all_fields():
    p = build_proposal(
        origin=_inp(1, category_secondary="ビール"),
        peer=_inp(2, budget_max=4000, category_secondary="ワイン"),
        score=0.85,
        area_name="鹿児島市天文館",
    )
    assert p == {
        "time_summary": "2026-10-02 20:00",  # JST YYYY-MM-DD HH:MM(承認済み解釈)
        "area_name": "鹿児島市天文館",
        "headcount": 2,
        "category_primary": "drinking",
        "category_secondary": "ビール",  # 起点側(引用#8)
        "budget": {"max": 4000},  # min(5000, 4000)・NULL無視
        "match_level": "medium",  # 0.85
    }


def test_hidden_until_match_minimizes_fields():
    p = build_proposal(
        origin=_inp(1, visibility="hidden_until_match"),
        peer=_inp(2),
        score=0.95,
        area_name="x",
    )
    assert p == {"headcount": 2, "match_level": "high"}  # headcountとmatch_levelのみ


def test_hidden_on_either_side_minimizes():
    # 双方のvisibilityが異なる場合はより厳しい方(いずれかがhiddenならhidden扱い)
    p = build_proposal(origin=_inp(1), peer=_inp(2, visibility="hidden_until_match"),
                       score=0.85, area_name="x")
    assert p == {"headcount": 2, "match_level": "medium"}


def test_budget_null_handling():
    p = build_proposal(origin=_inp(1, budget_max=None), peer=_inp(2, budget_max=4000),
                       score=0.85, area_name=None)
    assert p["budget"] == {"max": 4000}  # 片方NULLは無視
    p2 = build_proposal(origin=_inp(1, budget_max=None), peer=_inp(2, budget_max=None),
                        score=0.85, area_name=None)
    assert p2["budget"] is None  # 両方NULLならnull


def test_area_name_none_is_allowed():
    p = build_proposal(origin=_inp(1), peer=_inp(2), score=0.85, area_name=None)
    assert p["area_name"] is None  # 地物なし(M3表示側フォールバック対象)


def test_time_summary_uses_max_time_start():
    p = build_proposal(origin=_inp(1, time_start=T0),
                       peer=_inp(2, time_start=T0 + timedelta(minutes=90)),
                       score=0.85, area_name=None)
    assert p["time_summary"] == "2026-10-02 21:30"  # max(time_start)のJST


def test_nearby_proposal_is_minimal_and_fresh():
    assert nearby_proposal() == {"headcount": 2, "match_level": "low"}
    d = nearby_proposal()
    d["match_level"] = "changed"
    assert nearby_proposal() == {"headcount": 2, "match_level": "low"}  # 新規dict


def test_latch_intent_inputs_holds_no_forbidden_fields():
    names = {f.name for f in dataclasses.fields(LatchIntentInputs)}
    assert names == {
        "intent_id", "user_id", "visibility", "notification_level",
        "time_start", "expires_at", "budget_max", "category_primary",
        "category_secondary", "geo_lon", "geo_lat",
    }
    # 格納禁止の生データを保持しない構造ピン
    assert not names & {"raw_text", "soft_constraints", "structured_data"}
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_proposal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.worker.matching.proposal'`

- [ ] **Step 3: 実装する**

`backend/src/latch/worker/matching/proposal.py` を作成。内容は§9-3全文のとおり。

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_proposal.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/proposal.py backend/tests/unit/matching/test_proposal.py
git commit -m "feat: proposal生成(visibility分岐・budget/secondary規則・nearby最小構造)"
```

### Task 4: latch_engine.py 前半(選択・latch_score計算・latches生成・昇格・nearby)

**Files:**
- Create: `backend/src/latch/worker/matching/latch_engine.py`
- Create: `backend/tests/unit/matching/test_latch_engine.py`(前半分の試験)

**Interfaces:**
- Consumes: `origin.load_origin`・`origin.Origin`(変更禁止・importのみ)・`layer4.hard_constraint_holds`・`layer4.jst_day_start`・`latch_calc`・`proposal`・Task 1の0004索引
- Produces(Task 5〜8が使用):
  - `NOTIFICATION_PROPOSAL = "proposal"` / `NOTIFICATION_NEARBY = "nearby_candidate"`
  - `LatchTargetRow`(frozen dataclass: row_id / intent_a_id / intent_b_id / jev_result)
  - `LatchEngine(*, engine, clock, geo=None)`・`handle(intent_id) -> None`
  - SQL text()定数(§9-4のうち `_SELECT_TARGETS`・`_CLOSE_H_BROKEN`・`_RECORD_SCORE`・`_SELECT_INTENT_INPUTS`・`_SELECT_LATCH_RESPONSES`・`_INSERT_LATCH`・`_FIND_OPEN_LATCH`・`_UPDATE_FOR_PROMOTION`・`_INSERT_LATCH_EVENT`・`_INSERT_NOTIFICATION`)
  - conn受取りモジュール関数群(§9-5のうち前半で使う分。Task 5の `_try_promote`/`_drain` はシグネチャのみ定義し本体はpass)

- [ ] **Step 1: design §5-9の実装時確認(notifications.type CHECK制約)**

Run: `rg -n "CHECK" backend/alembic/versions/0001_initial_schema.py`
確認: ヒットのうち notifications テーブル部(`CREATE TABLE notifications` 〜閉じ括弧・209行目付近)にCHECKがないこと(計画時点で確認済み・intentsのvisibility/notification_level等のCHECKのみのはず)。**CHECKが存在していた場合は作業を停止してBLOCKEDとして報告**。結果は報告書へ記録。

- [ ] **Step 2: 失敗するテストを書く**

`backend/tests/unit/matching/test_latch_engine.py` を作成。構成はtest_worker_jev.pyと同型(FakeEngine/FakeTx+モジュール関数monkeypatch・SQL文字列ピンはtest_layer_sql.py流儀)。ファイル冒頭の共通部:

```python
"""LatchEngine.handle(選択・latch_score・latches生成・昇格・nearby・提示)のunit試験。

design §2.2〜2.7・§4.1。DB操作はlatch_engineのモジュール関数をmonkeypatch
して分岐ロジックを検証する(runnerと同一規律)。SQL文字列はtext()定数への
直接ピンで検証する。
"""

import uuid
from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.worker.matching import latch_engine as le
from latch.worker.matching.latch_engine import LatchEngine, LatchTargetRow
from latch.worker.matching.origin import Origin, OriginLoad
from latch.worker.matching.proposal import LatchIntentInputs

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


def _origin(n: int = 1) -> Origin:
    """起点(time_startは将来窓・test_worker_jev.pyの_originと同一構成)。"""
    return Origin(
        intent_id=_uid(n), version=1, user_id=_uid(n + 1),
        category_primary="drinking", alcohol_involved=False, budget_max=5000,
        participants_min=2, participants_max=4, geo_lon=130.55, geo_lat=31.59,
        geo_radius_m=2000, time_start=NOW + timedelta(hours=30),
        time_end=NOW + timedelta(hours=33), embedding="[0.1, 0.2]",
        soft_texts=(), user_ge_20=True, evaluated_at=NOW,
    )


def _row(n: int = 1, wa: float = 0.9, wb: float = 0.85) -> LatchTargetRow:
    return LatchTargetRow(
        row_id=_uid(n), intent_a_id=_uid(n), intent_b_id=_uid(n + 100),
        jev_result={"would_a_accept_b": wa, "would_b_accept_a": wb,
                    "provider": "typesafe_jev", "model": "jev-1.13.0"},
    )


def _inputs(n: int, **over) -> LatchIntentInputs:
    base = dict(
        intent_id=_uid(n), user_id=_uid(n + 50),
        visibility="summary_only", notification_level="proposals_only",
        time_start=NOW + timedelta(hours=30), expires_at=NOW + timedelta(days=5),
        budget_max=5000, category_primary="drinking", category_secondary=None,
        geo_lon=130.558, geo_lat=31.596,
    )
    base.update(over)
    return LatchIntentInputs(**base)


class _FakeEngine:
    def begin(self):
        return _FakeTx()


class _FakeTx:
    async def __aenter__(self):
        return object()  # conn本体はmonkeypatchした関数が受けない

    async def __aexit__(self, *exc):
        return False


class _RecordingGeo:
    def __init__(self, name="鹿児島市天文館"):
        self._name = name
        self.calls: list[tuple[float, float]] = []

    async def reverse_geocode(self, lon, lat):
        self.calls.append((lon, lat))
        return self._name
```

試験項目(monkeypatch対象: `le.origin_mod.load_origin`・`le.layer4.hard_constraint_holds`・`le._select_target_rows`・`le._read_intent_inputs`・`le._close_h_broken`・`le._record_score`・`le._read_latch_responses`・`le._insert_latch`・`le._find_open_latch`・`le._update_for_promotion`・`le._insert_latch_event`・`le._insert_notification`・`le._count_daily_notifications`・`le._try_promote`〔Task 4では記録スタブ〕・`le._drain_candidates`):

1. **no-op分岐**: `load_origin` がskip_reason付きを返す → `_select_target_rows` 不呼出・例外なし
2. **H再検証不成立**: `hard_constraint_holds` がFalse → `_close_h_broken` がrow_idで呼ばれ・`_record_score` 不呼出
3. **latch_score計算**: wa=0.9/wb=0.85 → `_record_score` が `score=0.85`(=min×LATCH_C)で呼ばれる
4. **競合負け**: `_record_score` がNone → `_read_intent_inputs` 以降不呼出
5. **閾値境界(0.80ちょうど)**: wa=wb=0.80 → 提案経路(`_insert_latch` 呼出)
6. **nearby経路**: wa=wb=0.5・片方 `notification_level='nearby_also'` → `_insert_latch` が `proposal=nearby_proposal()` と同一構造・`score=0.5` で呼ばれ・`_insert_latch_event(None, 'candidate', user_id=None)`・`_insert_notification` がnearby_also側のみ1件(type=NOTIFICATION_NEARBY)・`_try_promote` 不呼出
7. **nearby日次上限**: `_count_daily_notifications` がnearby_also側6を返す → 存在通知0件・`_insert_latch` は呼ばれる(candidate行自体は作る)
8. **閾値未満+nearby_alsoなし**: 両方 `proposals_only` → `_insert_latch` 不呼出
9. **nearby+no履歴**: `_read_latch_responses` が `[{"response": "no", ...}]` → `_insert_latch`・`_insert_notification` 不呼出
10. **提案経路のD-07 no**: 閾値超過+no履歴 → `_insert_latch` 不呼出
11. **提案経路のD-07 defer抑制内+Δ小**: `_record_score` がprev=0.83を返し・履歴deferが直近 → `_insert_latch` 不呼出。prev=Noneなら呼ばれる(新世代)
12. **INSERT成功**: `_insert_latch` がUUIDを返す → `_insert_latch_event(latch_id, None, 'candidate', None)`+`_try_promote(latch_id)`
13. **ON CONFLICT→既存candidate**: `_insert_latch` None・`_find_open_latch` が(lid, 'candidate')・D-07通過 → `_update_for_promotion`(score/proposal/deadline)+`_try_promote(lid)`
14. **ON CONFLICT→既存proposed**: `_find_open_latch` が(lid, 'proposed') → `_update_for_promotion`・`_try_promote` 不呼出
15. **昇格時D-07拒否**: (lid, 'candidate')+defer抑制内+Δ小 → `_update_for_promotion` 不呼出
16. **閾値未満評価での既存行不更新**: nearby経路で `_insert_latch` None → `_find_open_latch` 不呼出
17. **_read_intent_inputsの片方None**: 相手削除 → `_insert_latch` 不呼出・例外なし
18. **intent_ids正規化**: `_insert_latch` が受け取るa_id < b_id(sorted)
19. **area_name中点**: geo注入で `_RecordingGeo.calls == [((lon_a+lon_b)/2, (lat_a+lat_b)/2)]`・geo=Noneなら `_insert_latch` のproposalのarea_name=None
20. **SQLピン(test_layer_sql.py流儀・Review Focus 1)**:
    - `_SELECT_TARGETS`: `mc.latch_score IS NULL`・`mc.status = 'evaluated'`・`mc.jev_result IS NOT NULL`・EXISTS句(`p.version =` の相手現行一致)・`ORDER BY mc.cheap_judge_score DESC NULLS LAST`
    - `_RECORD_SCORE`: `prev_latch_score = latch_score`・`prev_evaluated_at = updated_at`・`WHERE id = CAST(:row_id AS uuid) AND latch_score IS NULL`・`RETURNING id, prev_latch_score`
    - `_INSERT_LATCH`: `ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed', 'partial_accept')`・`DO NOTHING`・`RETURNING id`・status初期値 `'candidate'`
    - `_INSERT_LATCH_EVENT`: 5列INSERT・CAST形式
    - `_SELECT_LATCH_RESPONSES`: `responses <> CAST('[]' AS jsonb)`
    - 上記SQLのcompile検査(postgresql dialect・bind paramが展開される)
21. **notifications種別ピン**: `NOTIFICATION_PROPOSAL == "proposal"`・`NOTIFICATION_NEARBY == "nearby_candidate"`(§9-10のCHECKなし確認とセット)

- [ ] **Step 3: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.worker.matching.latch_engine'`

- [ ] **Step 4: 実装する**

`backend/src/latch/worker/matching/latch_engine.py` を作成。内容は§9-1・§9-4(前半のSQL)・§9-5(クラス・handle・_evaluate_pair・conn受取りモジュール関数)のとおり。ただし:
- `_try_promote`・`_drain` はシグネチャのみ定義し本体はpass(Task 5で実装。handle末尾の `await self._drain()` 呼び出しは含める)
- `_evaluate_pair` の提案経路・昇格経路で `_try_promote(latch_id)` を呼ぶ(このTaskのテストはmonkeypatchで記録スタブへ差し替える)
- docstringに出典(06 §6・§10・design §2.1案A・§2.3〜2.5・§2.9の表)とws-7拡張点(1対1構成)を書く

- [ ] **Step 5: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py -v`
Expected: PASS

- [ ] **Step 6: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/latch_engine.py backend/tests/unit/matching/test_latch_engine.py
git commit -m "feat: LatchEngine前半(latch_score計算・latches生成/昇格・nearby存在通知)"
```

### Task 5: latch_engine.py 後半(try_promote・drain)

**Files:**
- Modify: `backend/src/latch/worker/matching/latch_engine.py`(SQL定数の追記: `_SELECT_LATCH_FOR_UPDATE`・`_COUNT_DAILY_NOTIFICATIONS`・`_COUNT_OPEN_PROPOSED`・`_EXPIRE_LATCH`・`_PROMOTE_LATCH`・`_DRAIN_CANDIDATES`・`Participant`・try_promote/drain本体)
- Modify: `backend/tests/unit/matching/test_latch_engine.py`(後半分の試験を追記)

**Interfaces:**
- Consumes: Task 4のLatchEngine前半・`latch_calc.response_deadline`・`layer4.jst_day_start`・`timedelta`
- Produces(Task 7・8が使用):
  - `Participant`(frozen dataclass: intent_id / user_id / notification_level / time_start / expires_at)
  - `_try_promote` 本体・`_drain` 本体・§9-4残りのSQL定数・`_select_latch_for_update`・`_read_participants`・`_count_daily_notifications`・`_count_open_proposed`・`_expire_latch`・`_promote_latch`・`_drain_candidates`

- [ ] **Step 1: 失敗するテストを書く(追記)**

`test_latch_engine.py` へ後半分を追記。`_try_promote`/`_drain` はmonkeypatch対象を `le._select_latch_for_update`・`le._read_participants`・`le._count_daily_notifications`・`le._count_open_proposed`・`le._expire_latch`・`le._promote_latch`・`le._insert_latch_event`・`le._insert_notification`・`le._drain_candidates` として直接 `_try_promote`/`_drain` を呼ぶ形で検証する(共通の `_latch_row`/`_participants` ヘルパをファイルへ追加):

1. **75分ルール**: `_read_participants` のtime_startがnow+70分 → `_expire_latch` 呼出+`_insert_latch_event(lid, 'candidate', 'expired', None)`・`_promote_latch`・`_insert_notification` 不呼出(Review Focus 4・通知ゼロ)
2. **status≠candidate**: `_select_latch_for_update` がstatus='proposed'を返す → 何も呼ばれない
3. **行なし**: None → 何も呼ばれない
4. **expires_at<=now**: row.expires_atがnow → 何も呼ばれない(expiry_sweeper=M3-3担当・対象外化のみ)
5. **deadline<=now**: time_start=now+80分・expires_at=now+10分(min_expiresが先に切れる)→ `_expire_latch`+expiredイベント(75分と同一扱い・防御)
6. **D-08日次上限**: 通知対象1名のcountsが6 → `_promote_latch` 不呼出(candidateのまま)・events/notificationsなし
7. **D-08同時3件**: `_count_open_proposed` が3を返す → `_promote_latch` 不呼出
8. **muted(Review Focus 3)**: 両参加者muted → `_promote_latch` 呼出+`_insert_latch_event(candidate→proposed)`・`_insert_notification` 0件・`_count_daily_notifications` の引数user_idsが空(上限を消費しない)
9. **正常プロモート**: 通知対象2名・上限内 → `_promote_latch(lid, deadline=D-05再計算値, now)`・イベント(candidate→proposed・user_id=None)・`_insert_notification` 2件(type=NOTIFICATION_PROPOSAL・payload=`{"latch_id": "<uuid>"}`・muted除外)
10. **提示時deadline再計算**: deadline引数が `response_deadline(now, target, min_expires)` と一致(nowはFOR UPDATE後に採取 — FakeClockで検証)
11. **promote競合負け**: `_promote_latch` がFalse → イベント・notifications不呼出
12. **drain順序**: `_drain_candidates` が[id1, id2, id3]を返す → `_try_promote` がid1→id2→id3の順(判定は呼出順リスト)
13. **drainのnearby除外(Review Focus 2)**: `_DRAIN_CANDIDATES` のSQL文字列に `l.score >= CAST(:threshold AS numeric)` が含まれる・`_drain` が `LATCH_THRESHOLD` をthresholdへ渡す
14. **drain SQLピン**: `l.status = 'candidate'`・`l.expires_at > CAST(:now AS timestamptz)`・`ORDER BY target_time ASC, l.score DESC, l.id ASC`(同点はid昇順で決定的)
15. **D-08カウントSQLピン(Review Focus 5)**: `_COUNT_DAILY_NOTIFICATIONS` に `type IN ('proposal', 'nearby_candidate')`・`created_at >= CAST(:day_start AS timestamptz)`・`created_at < CAST(:day_next AS timestamptz)` が含まれる。day_start/day_nextが `jst_day_start(clock.jst_date())`/`+1日` であることを `_count_daily_notifications` スタブの引数で検証(FakeClockの日付から導出)
16. **_COUNT_OPEN_PROPOSEDピン**: `status IN ('proposed', 'partial_accept')`・`intent_ids @> ARRAY[CAST(:intent_id AS uuid)]`
17. **_SELECT_LATCH_FOR_UPDATEピン**: `FOR UPDATE`・`WHERE id = CAST(:latch_id AS uuid)`
18. **_PROMOTE_LATCHピン**: `SET status = 'proposed', response_deadline = CAST(:deadline AS timestamptz)`・`WHERE … AND status = 'candidate'`・`RETURNING id`(条件付きUPDATE)
19. **_EXPIRE_LATCHピン**: `SET status = 'expired'`・`WHERE … AND status = 'candidate'`

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py -v`
Expected: 後半追加分がFAIL(`_try_promote` がpassのため `_expire_latch` 等が呼ばれない)。前半分(Task 4)は引き続きPASS

- [ ] **Step 3: 実装する**

`latch_engine.py` へ §9-4残りのSQL定数・`Participant`・`_read_participants` 等のconn受取り関数・`_try_promote` 本体(§9-5の手順1〜9・**手順順序がReview Focus 4**)・`_drain` 本体を実装する。`_try_promote` は1tx(`engine.begin()`)でまとめる(design §2.9のtx分割(4))。

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py -v`
Expected: PASS(全項目)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/latch_engine.py backend/tests/unit/matching/test_latch_engine.py
git commit -m "feat: LatchEngine後半(try_promote・drain・D-08上限・75分ルール・notifications)"
```

### Task 6: worker/reeval.py(ReevalRunner)

**Files:**
- Create: `backend/src/latch/worker/reeval.py`
- Test: `backend/tests/unit/test_worker_reeval.py`

**Interfaces:**
- Consumes: `latch_calc.bucket_start`・`latch_calc.BUCKET_MINUTES`(Task 2)・`ReevalGuard.allow`(ws-4・変更禁止)
- Produces(Task 7が使用):
  - `ReevalRunner(*, engine, clock, guard, pipeline, interval_sec, batch_limit, sleep=asyncio.sleep)` — guard: `ReevalGuard | None`・pipeline: `Callable[[uuid.UUID], Awaitable[None]]`
  - `run_once() -> int`・`run(*, stop=None)`(sleep-first・例外はrun()で握る)
  - `_select_catchup_targets(engine, now, batch_limit) -> list[uuid.UUID]`・`_select_bucket_targets(engine, bucket_start, bucket_end) -> list[uuid.UUID]`(モジュール関数・unit試験のmonkeypatch対象)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_reeval.py` を作成(design §4.1・test_worker_backfill.pyと同型のFakeResult/ScriptedConn/ScriptedEngineパターン):

```python
"""ReevalRunnerのunit試験(design §2.8・§4.1)。

catch-up抽出・Bucket処理(同一Bucket再処理なし・境界切替)・ガードdenyで
pipeline不呼出・例外握り継続・stop追従を検証する。SQL抽出関数は
monkeypatchで差し替え(runner流儀)、SQL文字列ピンはtext()定数へ直接。
"""

import asyncio
import uuid

from latch.core.clock import FakeClock
from latch.worker import reeval as reeval_mod
from latch.worker.reeval import ReevalRunner

IID1 = uuid.UUID("00000000-0000-4000-8000-0000000000f1")
IID2 = uuid.UUID("00000000-0000-4000-8000-0000000000f2")
```

試験項目:

1. **catch-up抽出SQLピン**: `_SELECT_CATCHUP_TARGETS` に `status = 'active'`・`embedding IS NOT NULL`・`expires_at IS NOT NULL`・`expires_at > CAST(:now AS timestamptz)`・`expires_at <= CAST(:now AS timestamptz) + interval '2 hours'`・`LIMIT :batch_limit` が含まれる(引用#19・「直近評価から30分」条件はSQLに入れない — ガード担い)
2. **Bucket抽出SQLピン**: `_SELECT_BUCKET_TARGETS` に `time_start >= CAST(:bucket_start AS timestamptz)`・`time_start < CAST(:bucket_end AS timestamptz)`・`embedding IS NOT NULL`
3. **run_onceがcatch-up+Bucket対象をpipelineへ**: 両方の抽出スタブがIDsを返す → pipeline呼出は全IDs(catch-up+Bucket・`seen`で重複排除: 両方に同一IDがある場合は1回のみ)
4. **同一Bucketの再処理なし**: run_onceを2回(クロック不進行) → 2回目はBucket抽出スタブ不呼出(`_last_bucket` 保持)・catch-upのみpipeline
5. **Bucket境界の切り替わり**: FakeClockを31分進めてrun_once → 新しいbucket_startでBucket抽出が再度呼ばれる
6. **初回は現在Bucketを処理**: `_last_bucket=None` → 1回目からBucket抽出が呼ばれる(再起動直後に現在Bucket→ガードが30分以内を弾く・design §2.8-2)
7. **ガードdeny**: guardスタブがFalse → 当該IDのpipeline不呼出・他IDは呼ばれる
8. **guard=None**: ガード判定をスキップして全IDsへpipeline
9. **pipeline例外は握らず伝播**: pipelineがRuntimeErrorを投げる → run_onceはそのままRuntimeErrorを送出(pytest.raises)。`run()` のループは握って継続(stopセットで終了・runnerのrunを短時間動かして検証)
10. **run_onceの戻り値**: pipeline成功件数
11. **stop追従**: `run(stop=event)` はsleep-first(即pipelineしない)でevent.set()後に終了(`_wait_interval` のwait_forタイムアウト代わりにsleepスタブで即復帰させる)
12. **compile検査**: 2つのSQLのbind paramがpostgresql dialectで展開される

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_reeval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.worker.reeval'`

- [ ] **Step 3: 実装する**

`backend/src/latch/worker/reeval.py` を作成。内容は§9-6全文のとおり(`seen` setによる重複排除1行を含む)。docstringに出典(06 §9・design §2.8・M3-3統合予定)を書く。

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_reeval.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/reeval.py backend/tests/unit/test_worker_reeval.py
git commit -m "feat: ReevalRunner(catch-up・30分Bucket・直接投入・sleep-first周期)"
```

### Task 7: Worker配線(main.py)・settings追加・test_worker.py追従

**Files:**
- Modify: `backend/src/latch/worker/main.py`(_kick_jev直列化・LatchEngine/ReevalRunner DI・_run_direct_pipeline・task起動)
- Modify: `backend/src/latch/settings.py`(reeval_runner 2項目)
- Modify: `backend/tests/unit/test_worker.py`(配線ピン追加)

**Interfaces:**
- Consumes: `LatchEngine`(Task 4・5)・`ReevalRunner`(Task 6)・`GeoService`(変更禁止・importのみ)・`run_candidate_retrieval`(変更禁止)
- Produces: `Worker.__init__(…, latch=None, reeval_runner=None)`・`Worker._run_direct_pipeline(engine, intent_id)`・`Settings.reeval_runner_interval_sec=60`・`Settings.reeval_runner_batch_limit=50`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker.py` へ追記(§9-10の設定追加はピン試験と干渉しない — §0機械確認済み):

1. **settings追加ピン**(`tests/unit/test_settings.py` の `test_settings_defaults` へ2行追加 — §4変更一覧の機械的追随):
   `assert s.reeval_runner_interval_sec == 60`・`assert s.reeval_runner_batch_limit == 50`
2. **_kick_jev直列配線**(test_worker.py・`_RecordingJev` と対称の `_RecordingLatch` スタブを追加):

```python
class _RecordingLatch:
    """LatchEngineスタブ(handleの呼び出しを記録)。"""

    def __init__(self):
        self.calls: list[uuid.UUID] = []

    async def handle(self, intent_id):
        self.calls.append(intent_id)


async def test_kick_jev_runs_latch_after_jev(fake_clock):
    """embedding_completed → JevWorker完了後にLatchEngineを直列実行(design §2.1案A)。"""
    jev = _RecordingJev()
    latch = _RecordingLatch()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_latch(fake_clock, stage1, jev, latch)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert jev.calls == [iid]
        assert latch.calls == [iid]  # JevWorkerの後にLayer 5
    finally:
        await _stop(worker, task)


async def test_kick_jev_latch_not_injected_is_noop(fake_clock):
    """Latch未注入(ws-1/ws-5資産の試験)は何もしない。"""
    jev = _RecordingJev()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_latch(fake_clock, stage1, jev, None)
    try:
        await worker._dispatch(_make_event("embedding_completed", uuid.uuid4(), 1))
        assert jev.calls  # jevのみ
    finally:
        await _stop(worker, task)


async def test_kick_jev_jev_not_injected_latch_runs(fake_clock):
    """Jev未注入でもlatch注入ならlatchを実行(構成上の独立性)。"""
    latch = _RecordingLatch()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_latch(fake_clock, stage1, None, latch)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert latch.calls == [iid]
    finally:
        await _stop(worker, task)


async def test_created_does_not_kick_latch(fake_clock):
    """created/updatedではLayer 5も起動しない(06 §1)。"""
    latch = _RecordingLatch()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_latch(fake_clock, stage1, None, latch)
    try:
        await worker._dispatch(_make_event("created", uuid.uuid4(), 1))
        assert latch.calls == []
    finally:
        await _stop(worker, task)
```

3. **_run_direct_pipeline配線**: `_RecordingJev`/`_RecordingLatch` 注入のWorkerで `worker._run_direct_pipeline(engine, iid)` を直接呼び(engineは_FakeEngine的スタブでrun_candidate_retrievalをmonkeypatch)、L1〜3→jev→latchの順の呼出を記録スタブで検証

(`_started_worker_with_latch` は既存 `_started_worker_with_jev` の隣へ同型ヘルパとして追加)

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/test_worker.py tests/unit/test_settings.py -v`
Expected: FAIL(Workerにlatch引数がない・設定がない)

- [ ] **Step 3: 実装する**

1. `settings.py`: §9-8の2フィールドをembedding_backfill並びの後に追加
2. `worker/main.py`: §9-7のとおり変更(import追加・`__init__` 引数2件・run()内DI〔LatchEngine常時構築・ReevalRunnerはredis構成時〕・task起動/await・`_kick_jev` の書き換え〔docstring更新込み〕・`_run_direct_pipeline` 新設)
3. 既存の `_kick_jev` 呼び出し経路(`_dispatch` 内)は変更不要(メソッド内部の構造変更のみ)

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit -v`
Expected: PASS(unit全体・既存のjev配線試験にも影響なし)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/main.py backend/src/latch/settings.py backend/tests/unit/test_worker.py backend/tests/unit/test_settings.py
git commit -m "feat: Worker配線(_kick_jev直列LatchEngine・ReevalRunner周期task)・settings追加"
```

### Task 8: integration試験作成・完了条件検証・報告書

**Files:**
- Create: `backend/tests/integration/test_matching_latchengine.py`(作成のみ・**実行しない**)
- Create: `docs/plans/M2/ws-6-report.md`

**Interfaces:**
- Consumes: 全Taskの成果物
- Produces: 報告書(§7の形式)

- [ ] **Step 1: integration試験を書く**

`backend/tests/integration/test_matching_latchengine.py` を作成。**test_matching_jev.py流儀を踏襲**(対抗策はdesign §4): `pytestmark = pytest.mark.integration`・時間窓は **now+5日系(BASE_HOURS=120)へ統一**(専用カテゴリ分離でなく時間窓分離 — ws-4裁定)・期限・75分・D-05系の試験だけ専用の遠い/近い窓を使い同一関数内パート間で窓が重ならないようClock進行を設計・active保存時のtime_end補完(start+3h)に注意(ws-5検証(4)と同種)・subjectプレフィックス `m2ws6-`・teardownは**latch_status_events→notifications→latches→match_candidates→intents→users**の順で完全削除(FK・部分UNIQUEの残存が次回試験の「開いている行あり」判定を壊すため)・Redisは `ws6-` prefixのSCAN+DELETE・FakeClock注入・Workerプロセス起動なし(LatchEngine/ReevalRunner直接構築)・jev_resultはmatch_candidatesへ直接UPDATE(stub由来の値・提案系はwould_*=0.9等を直書き)。

ファイル冒頭のdocstringに対抗策を明記する。試験内容(10試験・design §4.2):

1. **test_1_e2e_proposal_generation**(02#12下地): ユーザー2名+Intent 2件(now+5日窓)→embedding直接UPDATE→`run_candidate_retrieval`→match_candidatesへjev_result付きevaluated行を直接投入(would_*=0.9/0.85→latch_score=0.85)→`LatchEngine.handle` →latches(status=proposed)・latch_status_events(NULL→candidate→proposedの2行・user_id NULL)・notifications 2行(type='proposal'・payload={"latch_id"})・response_deadline=D-05再計算値(min(max(now+15m, min(now+2h, target-60m)), min_expires)の期待値計算)・score=0.85・proposal全フィールド(match_level='medium'・area_nameは実地物〔天文館周辺fixture〕またはnullのどちらでもよい — 地物取り込み状態に依存しないassert: `in ("鹿児島市…", None)` は不可避なら `proposal["area_name"] is None or isinstance(proposal["area_name"], str)`)
2. **test_2_visibility_branch**(引用#12): summary_only×2とhidden_until_match混在(片方hidden)の2ケースでlatches.proposalの内容比較(hiddenは `{"headcount": 2, "match_level": …}` のみ・time_summary等なし)
3. **test_3_d08_daily_limit**: 通知対象ユーザーへnotifications 6件(type='proposal'・当日)を直接INSERT → handle→latches 7件目はcandidateのまま(proposedにならない)・notifications増えない・**nearby存在通知の上限スキップ**: nearby_also側に当日6件済み→閾値未満評価で存在通知ゼロ(candidate行は作られる)
4. **test_4_d08_concurrent_limit**: 参加Intentの一方を含む開いているproposed latches 3件を直接INSERT(intent_idsに当該Intent+他Intentの組み合わせ・expiresは将来)→4件目の評価はcandidate保留
5. **test_5_75min_rule**: 対象時刻をnow+70分にしたFixture(時間窓分離: 当試験専用の近い窓・他試験と関数内で重ならない)→handle後latches candidate→expired・latch_status_events(candidate→expired)・notifications 0件
6. **test_6_nearby_also**: 閾値未満(would_*=0.5)・片方 `notification_level='nearby_also'`(PATCHまたは作成時payloadで指定)・もう片方muted → latches candidateのまま・存在通知はnearby_also側のみ1行(type='nearby_candidate')・muted側0行・proposed遷移なし・proposal=nearby最小構造・score=0.5(実値)
7. **test_7_d07_defer_suppression**: latches.responsesへdefer履歴(`{"user_id": …, "response": "defer", "answered_at": <now-10分のISO>}`)を直接投入(開いているcandidate行として前提を作り、閾値超過の新評価世代を別ペア… ではなく同一ペアで閉じた行+開いている行の両方を試験: 開いているcandidate行のresponsesへdeferを直接UPDATEで注入→閾値超過の再評価(handle再実行)で `_update_for_promotion` が抑制内+prev Noneで**新世代は変化ありとなり昇格する**ケースと、閉じた過去行(expired)へdefer履歴を入れて新規INSERT経路で抑制されるケースを分けて検証(前者=手順3の新世代無条件、後者=手順2のdefer抑制)。Clockを抑制期間経過へ進めた場合は昇格する)
8. **test_8_drain_order**: candidate 3件を直接INSERT(対象時刻=参加Intentのtime_startの差・scoreの差を組み替え: ①対象時刻早い+score低い ②対象時刻遅い+score高い ③対象時刻早い+score高い 等)→handle(起点no-opでもよい — drainを回すため存在するIntent起点)→proposed化された順序=対象時刻昇順・score降順の検証(notificationsのcreated_at順またはlatch_status_events順で)
9. **test_9_idempotency**(Review Focus 1・design §5-10の実証): 同一handleを2回実行→latches二重なし(同一intent_idsで1行)・latch_status_eventsのcandidate挿入は1回・notificationsも再送なし・match_candidatesのprev退避は初回のみ(2回目はlatch_score IS NULLガードで対象外)
10. **test_10_reeval_runner_path**(02#8): 時刻をまたぐ2 Intent(now+5日窓の2件・embedding直入れ)を配置→FakeClock進行でcatch-up抽出(expires_atまで2時間以内へ到る時刻へ進める・期限も近い窓へ調整)→`ReevalRunner.run_once`(guard=実ReevalGuard+fakeredisでなく実redis_sweep prefix・pipeline=Worker._run_direct_pipeline相当の直接構築)→match_candidates生成の記録・**reevalガードで30分以内の再実行がスキップされること**(run_once 2回目でpipeline不呼出)

実装詳細: `LatchEngine(engine=db_engine, clock=fake_clock, geo=GeoService(db_engine))`(実geo・PostGIS完結)。jev_result直接投入は `UPDATE match_candidates SET jev_result = CAST(:jev AS jsonb), status='evaluated' WHERE id=…`。latches直接投入はINSERT(intent_ids=ARRAY[…2要素…]::uuid[]・proposal='{}'::jsonb・score・status・response_deadline・expires_at・created_at)。

- [ ] **Step 2: 収集確認(実行はしない)**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_matching_latchengine.py -q`
Expected: 10件収集・exit 0
Run: `make lint && make test`
Expected: PASS(unitは外部プロセス不要のためintegrationのimport検査のみで完了)

- [ ] **Step 3: 完了条件7項目を検証する**

1. `make lint && make test` — exit 0(全件数を記録)
2. `cd backend && uv run pytest --collect-only tests/integration/test_matching_latchengine.py -q` — 10件・exit 0
3. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` — ヒットが `core/clock.py` のみ + `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` — exit 0
4. `git diff --stat main -- backend/alembic/versions/0001_initial_schema.py backend/alembic/versions/0002_geofeatures.py backend/alembic/versions/0003_match_candidates_skip_reason.py docs` — 出力なし + Task 1のチェーン確認スクリプトでheads=['0004']
5. (報告書コミット後に再確認)`git diff --name-only main | sort` — §4の一覧(15ファイル・report込み・test_settings.py追加込み)と完全一致 + `git status --short` — 空
6. `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし
7. `make test` の全件数を報告書へ記録(既存unit全数+本単位追加。926はtest-ci〔unit+integration合計〕の値なのでunit側は実測値をそのまま記録する。§4列挙以外の期待値変更がないことの突合)

- [ ] **Step 4: 報告書を作成する**

§7の形式で `docs/plans/M2/ws-6-report.md` を作成・記入する(design §5実装時確認事項2件〔§5-9 CHECK再確認結果・§5-10 ON CONFLICT推論のunit担保状況〕の結果・固定値の変更有無・引継ぎ事項・スーパーバイザー検証手順を含む)。

- [ ] **Step 5: コミット**

```bash
git add backend/tests/integration/test_matching_latchengine.py docs/plans/M2/ws-6-report.md
git commit -m "docs: ws-6報告書とintegration試験(test-ci=スーパーバイザー検証待ち)"
git status --short  # 空であることを最終確認
```

完了後の最終返信は「報告ファイルのパス+完了条件7項目の結果一覧」。工作はここまで — スーパーバイザー検証(`docker compose build api worker` → `make migrate` → `make test-ci` → 残存確認)は実装側では実行しない。

---

## Self-Review記録(計画書作成時の確認)

1. **Specカバレッジ**: design §1.2の引用#1〜#24はすべてタスクへ配置済み(#1〜3→Task 4・5、#4〜5→Task 2・5、#6〜7→Task 2・4、#8→Task 5、#9〜10→Task 4・5・8、#11〜14→Task 3、#15〜16→Task 4・5・0004、#17〜20→Task 5・6、#21〜22→Task 4・5、#23〜24→Task 8)。design §1.1の6部品: LatchEngine(Task 4・5)・提示制御(Task 5)・nearby/muted(Task 4・5)・drain(Task 5)・再評価(Task 6・7)・Worker配線(Task 7)。§1.4のスコープ外7項目は§5へ明記
2. **プレースホルダスキャン**: 「TBD」「適切に」「後で」なし。SQL全文・純関数全文・試験コードは§9とTask本文に実物を記載
3. **型整合**: `LatchEngine(*, engine, clock, geo=None)`・`ReevalRunner(*, engine, clock, guard, pipeline, interval_sec, batch_limit, sleep)`・`_record_score -> tuple[uuid.UUID, float | None] | None`・`d07_allows` のキーワード引数名はTask間で一致
4. **Review Focus 5件**: 各行末に所有タスクの試験を明記済み(上記§3)
