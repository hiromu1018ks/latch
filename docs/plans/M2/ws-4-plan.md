# M2 ws-4(Layer 3 Cheap Judge + コスト保護)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Layer 3 Cheap Judge(cheap_score=0.5×類似度+0.3×ルールスコア+0.2×語彙重なりをLayer 2通過全件へ計算・全件記録・上位K_c=20を次層出力)と、コスト保護の未実装2層(Jev予算カウンタStore・判定Guard・再評価ガードreeval)、および embedding_completed 起点の配線(stage1 matchingフック・worker/main.py のDI)を実装する。Jev実行本体はws-5であり、本単位は「実行直前に呼ばれる部品」+「reevalのパイプライン組み込み」のみ。

**Architecture:** Layer 3 は Layer 2 と同一トランザクション・同一実行経路で組む(design §2.1案A): `run_candidate_retrieval` の内部で列拡張した `retrieve_topk` の結果を受け取り、`layer3.py` の純関数(依存ゼロ・決定的)で cheap_score を計算、既存UPSERTに cheap_judge_score を乗せて全件記録、`select_top_kc` で上位K_c=20を `RetrievalOutcome.topkc` へ載せる。コスト保護は `worker/cost/` 新規パッケージに Redis操作(store)・判定(guard・INCR先行)・再評価ガード(reeval)を分け、`ratelimit/` と対称の構成にする。カウンタのJSTリセットは日付キー切替方式(ジョブ不在でも正確・design §2.5)。reevalガードのみ本単位でパイプライン(matchingフック実体の先頭)に組み込む。

**Tech Stack:** 既存のみ(Python 3.13 / SQLAlchemy[asyncio]+asyncpg / redis-py(asyncio) / fakeredis[unit試験])。**依存追加なし・設定追加なし(settings.py 不触)・マイグレーション追加なし(alembic 0002がheadのまま・cheap_judge_scoreカラムは0001作成済み — design §1.1・§2.8)・docs(01〜12)改版なし**。

**Spec:** `docs/plans/M2/ws-4-design.md`(agent1設計メモ。**未解決論点なし** — design §5-1〜3(alert媒体=構造化ログ・リセットジョブ本体はM3-4・既存試験期待値の機械的追随)は2026-09-29 supervisor承認済みの確定事項として本計画へ反映。§5-4〜5は実装時確認として各Taskへ組み込み、§5-6〜9は引継ぎ・先送り記録として§5・§7へ反映)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m2-ws-4`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(本単位は依存追加がないため既存lockのまま)。
- **共有ci-db運用**(STATUS運用ルール1〜3): 本単位は**マイグレーション追加なし**のため `make migrate` 不要(実行しても冪等)。**開発はunit試験(`make lint`・`make test`)で完結させる** — 実装エージェントは `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` / `docker pull` を一切実行しない。integration試験ファイルは作成するが**実行せず**、報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する。`make test` はintegrationファイルの収集(import)まで行うため、構文・importの正当性はunit実行で検証される。
- **テストファイルのbasename一意**(STATUS運用ルール5・supervisor指示): tests配下は `__init__.py` なしのためbasenameがimport名になる。**design §3.1の `tests/unit/cost/test_store.py` は既存 `tests/unit/ratelimit/test_store.py` とbasename衝突するため、本計画は `test_cost_store.py`・`test_cost_guard.py`・`test_cost_reeval.py` に改名して落とし込む**(M2 ws-3で同名衝突により実装がBLOCKEDした実績への対処・design §4.3-3の機械確認を計画時点で実施済み)。本計画の新規テストファイル(`test_layer3.py`・`test_cost_store.py`・`test_cost_guard.py`・`test_cost_reeval.py`・`test_matching_cheapjudge.py`)がbackend/tests配下全体で一意であることを、コミット前に `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることで確認する。
- **既存テストファイル名の実在確認(supervisor指示の機械確認)**: design §3.2の `tests/unit/matching/test_runner.py` は実在しない。実ファイルは `tests/unit/matching/test_matching_runner.py`(ws-3実装時にbasename一意化のためリネーム済み)。本計画は実ファイル名で指定する。
- **並走単位なし**(design §1前提: 実行waveはws-4単独)。ただし既存ファイルへ触れるため、各タスクのコミット前に `git status --short` で意図しないファイルの変更が混入していないことを確認する(§4の一覧以外に差分が出ていたら作業を止めて報告する)。
- **固定値の遵守**: design §2 の採用判断と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| Layer 3(cheap_score = 0.5×埋め込み類似度(cosine そのもの) + 0.3×ルールスコア(時間帯・ペア予算の近さを0〜1に正規化) + 0.2×soft constraintsの語彙重なり。D-04降格のNG条件は語彙重もりの計算対象から明示的に除外(Layer 3を素通りしLayer 4入力へ渡る)。決定的(追加APIコストゼロ)。重みの初期値は運用データで調整) | 06 §4・06 §2 |
| K上限 K_c=20(その層から次層へ渡す出力数の上限)。切り詰めはcheap_score上位・同点はintent_id昇順で決定的に崩す | 06 §8 D-24・01 §11 |
| Layer 1〜3の予算 p95 ≤1秒。Layer 3はルール計算のみ | 06 §1 |
| Jev予算3層((a)1Intent 40回/日・JST 0時リセット (b)1ユーザー120回/日・Active上限5×40の理論値と一致する共有プール (c)同一Intentの更新による再評価は直近の詳細評価から30分空ける・Event自体は処理済みとし再評価しない) | 06 §5(FR-03表) |
| 予算カウンタはRedisで user_id・intent_id・日付キーで保持し、実行要求側(Matching Worker)でINCR。頻度制限はTTL 30分のキー(reeval:{intent_id})で判定 | 06 §5 |
| D-16(日次30,000回・80%の24,000回でalert / 月次600,000回・80%でalert / 月次リセットは暦月初JST 0時・到達時は復帰予定を明示 / 上限到達時はJev実行をスキップし対象候補を「Jev未判定」として記録 / 実際のAPI呼び出しをすべて1実行回数に計上・フォールバックLLMへの切替呼び出しも同一の1実行回数) | 04 §4 D-16 |
| 80% alertにはEvent源流上位ユーザーのレポートを添付(Intent別・ユーザー別のJev消費上位リストと第一候補・フォールバック別の実行回数内訳) | 04 §4 D-16・06 §5 |
| コスト保護の経路(Jev実行要求→Redisの日次/月次カウンタINCR→80%到達でalert→上限到達でスキップ。TTL方式はUTC基準となりJST 0時と9時間ずれるため用いない。リセットジョブ失敗時は最初のJev実行要求で前日以前のキーを検知しリセット=自己修復。カウンタは実行要求側で増やす=並行実行でも超過幅を1要求分) | 04 §5 |
| alertの宛先とエスカレーションは運用設計に委ねる(本書は検知と制御の経路のみ確定) | 04 §5 |
| 縮退(D-15): 回数上限到達・Jev予算/頻度制限由来のスキップは「提案見送り・候補保留」。保留は match_candidates.status=skipped で表現 | 06 §8 D-15 |
| Layer 1〜5はembedding_completedを起点にのみ走る(作成・更新Eventの処理はEmbedding要求のキックまで) | 06 §1・§9・12 C6 |
| match_candidates(retrieval_score / cheap_judge_score = 各層の結果。status値域は pending/evaluated/skipped/closed。同一バージョン内の再評価は既存レコードを更新) | 05 §2 |
| soft_constraints は `{text, downgraded_from_ng}` の配列(downgraded_from_ng=trueはD-04降格条件) | 05 §2 |
| K上限の裏付け(Cheap Judge判定 ≦20 が記録で守られ・切り詰め決定的(同点intent_id昇順)・同一入力2回実行で同一結果) | 10 §4.6・02 §4 #11 |
| 時刻参照はすべてClock経由(JST 0時リセットとRedisカウンタのJST日付キーを含む) | 04 §5・10 §1・12 C2 |
| Jevカウンタは実行要求側でINCR・TTL方式不採用 | 12 C12 |
| 既存実装資産(runner・layer2・origin・candidates・stage1のembedding_hook DIパターン・ratelimitのINCR先行規律・embedding_text・integration conftest) | design §1.3 |
| supervisor承認3件(2026-09-29): alert媒体=構造化ログで開始 / リセットジョブ本体(保留キュー再評価イベント発行)はM3-4 / 既存試験の期待値更新は機械的追随として正当 | design §5-1〜3 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **永続化は `sqlalchemy.text()` 生SQLのみ**(intents/store.py・worker/matching と同じ形式)。ORMモデル・リポジトリ層を作らない
- **bind param は `CAST(:x AS ...)` 形式**(NULLを渡しうるパラメータは必須。test_layer_sql.py の compile 検査が回帰を防ぐ)
- **時刻はClock経由のみ**。製品コード(`backend/src/latch/`)で実時間参照禁止(arch test `test_arch_no_direct_time.py` が強制)。RedisカウンタのJST日付キー(yyyymmdd/yyyymm)も `clock.jst_date()` から導出する(C2・引用#15)
- **Redis操作の規律(ratelimit/store.py・limiter.py の踏襲)**: INCRとEXPIREはpipelineで併発・TTLは掃除用でリセット表現に使わない(リセット=日付キー切替・design §2.5)・Redis例外は専用例外でfail-closed(コスト保護の失敗を実行の通り抜けにしない)・バケット文字列はClockから導出しStoreは受け取る
- **例外は握り潰さない**(matching/ 配下): SQL失敗等は呼び出し側(stage1の再試行5回→quarantined)へ伝播。matching/ 配下のモジュールにtry/except・ロギングを置かない。例外: worker/cost/(Redis例外のfail-closedラップ)と worker/main.py の `_run_matching`(reevalスキップの構造化ログのみ)
- **K_c=20・各上限値・重み・飽和値はモジュール定数**(settings.py に追加しない — design §2.8-4)。調整はdocs改版を伴う
- **ログは構造化ログのみ**: alert=`latch.cost.alert` logger(design §2.4-5・supervisor承認)。reevalスキップ理由=`worker/main.py` のlogger(design §2.6)。新規loggerはこの2つのみ
- **改行・行長**: ruff(E,F,I,UP,B)と `ruff format`(行長88)を毎コミット通す
- **unit試験は外部プロセス不要・実時間待ちなし**(fakeredis・スタブconn・FakeClock)。SQL文字列の正当性は compile 検査(unit)+ 実DB試験(integration・スーパーバイザー検証時)の二段構え
- **丸めなし**: cheap_score は計算・DB書き込みとも丸めない(float→numeric へそのまま。design §2.2)

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **soft_constraints の downgraded_from_ng=true 文言が語彙計算に混入する** — 降格条件はLayer 3を素通りする決まり(06 §4)であり、混入すると語彙一致ペアのスコアが汚染される → Task 1 の `test_soft_texts_excludes_downgraded`(除外ピン)+ Task 5 の `test_origin_soft_texts_extraction`(Origin構築経由の実証)+ Task 9 の試験3(ng_unverifiable がスコアに影響しない対照)
2. **30分以内の同一Intent再評価Eventが評価を実行してしまう** — 高頻度更新でJev実行(将来ws-5)とマッチングが無限に増える。06 §5(c)「Event自体は処理済みとし再評価しない」 → Task 4 の `test_first_call_allows_second_denies`(SET NX)+ Task 8 の `test_run_matching_suppressed_when_reeval_denies`(フック実体)+ Task 9 の試験5(配線経由で行数不変)
3. **Redis障害時にマッチング・コスト保護が「通り抜けて」しまう** — D-16「上限はコストの保証」・fail-closed違反 → Task 3 の `test_redis_exception_wrapped_fail_closed`・Task 4 の `test_redis_exception_fail_closed`(専用例外で再試行経路へ)
4. **K_c同点境界で順序が非決定になる** — 同点解消が実装依存だと10 §4.6の裏付け試験が再現しない → Task 1 の `test_select_top_kc_tie_break_and_truncation`・`test_select_top_kc_truncates_at_20`(同点=UUID昇順・2回実行で完全同一)
5. **UPSERTのDO UPDATEが status を pending へ巻き戻す** — 再評価で ws-5 の評価結果(evaluated/skipped)が消える → Task 5 の `test_upsert_do_update_touches_scores_only`(DO UPDATE句にstatusを含まないSQLピン)

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1 + 本計画の変更点):

```text
backend/src/latch/worker/matching/layer3.py          (Task 1。cheap_score純関数群)
backend/src/latch/worker/cost/__init__.py            (Task 2で仮作成 → Task 4で公開APIの最終形)
backend/src/latch/worker/cost/errors.py              (Task 2。design §3.1への追加 — §9-3)
backend/src/latch/worker/cost/store.py               (Task 2。Redis操作のStore)
backend/src/latch/worker/cost/guard.py               (Task 3。4カウンタINCR先行判定・alert)
backend/src/latch/worker/cost/reeval.py              (Task 4。再評価頻度ガード)
backend/tests/unit/matching/test_layer3.py           (Task 1)
backend/tests/unit/cost/test_cost_store.py           (Task 2。designのtest_store.pyから改名 — §0)
backend/tests/unit/cost/test_cost_guard.py           (Task 3。designのtest_guard.pyから改名 — §0)
backend/tests/unit/cost/test_cost_reeval.py          (Task 4。designのtest_reeval.pyから改名 — §0)
backend/tests/integration/test_matching_cheapjudge.py (Task 9。作成のみ・実行しない)
docs/plans/M2/ws-4-report.md                         (Task 10。報告ファイル)
```

変更(design §3.2。test_runner.py は実ファイル名 test_matching_runner.py へ読み替え):

| ファイル | 変更内容 | Task |
|---|---|---|
| `worker/matching/layer2.py` | `_SELECT_TOPK` へ `i.time_start, i.budget_max, i.structured_data` 列追加。`RetrievedCandidate` に `time_start`・`budget_max`・`soft_texts` 追加 | 5 |
| `worker/matching/origin.py` | `_SELECT_ORIGIN` へ `i.structured_data` 追加。`Origin` に `soft_texts: tuple[str, ...]` 追加 | 5 |
| `worker/matching/candidates.py` | `_UPSERT` の INSERT 列と DO UPDATE 句へ cheap_judge_score 追加。`upsert_candidate` に `cheap_score` 引数 | 5 |
| `worker/matching/runner.py` | Layer 3 組込み(全件計算→全件UPSERT→topkc選定)。`RetrievalOutcome` に `topkc` 追加 | 6 |
| `worker/matching/__init__.py` | layer3 の公開API追記 | 6 |
| `worker/stage1.py` | `matching_hook` パラメータ追加と `_process_once` の embedding_completed 種別での呼び出し | 7 |
| `worker/main.py` | redis クライアント構築・`ReevalGuard`・matching フック実体 `_run_matching` と stage1 への DI | 8 |
| `tests/unit/matching/test_layer_sql.py` | SELECT列・UPSERT句ピンの期待値更新(機械的追随・supervisor承認) | 5 |
| `tests/unit/matching/test_origin.py` | structured_data 読み取り・soft_texts 抽出の追記 | 5 |
| `tests/unit/matching/test_matching_runner.py` | RetrievedCandidate 拡張への追随 + Layer 3 呼び出しの追加検証(スタブ) | 6 |
| `tests/unit/test_worker_stage1.py` | matching フック試験の追記(embedding_hook試験パターンを踏襲) | 7 |
| `tests/unit/test_worker.py` | `_run_matching` DI 配線のピン(最小) | 8 |

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`(gitignore済み)。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **docker系コマンド一切**(§0): `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` / `docker pull`。api/workerイメージ再ビルドとintegration実行はスーパーバイザーの検証手順に含まれる
- **design §3.3の禁止**: `backend/src/latch/` 配下の intents / auth / users / ratelimit / geo / llm / g1gate / events / core 各モジュール(ratelimit は規律の参照元。コードは変更しない)。`worker/` の既存ファイルのうち stage1.py・main.py 以外(embedding.py・embedding_text.py・debounce.py・backfill.py・__main__.py・__init__.py)。`backend/alembic/`(0002がheadのまま)。`backend/pyproject.toml`+`backend/uv.lock`(依存追加なし)。`backend/tests/` の既存ファイルのうち §4 に列挙した以外(特に test_events_pipeline.py・test_matching_hardfilter.py・test_matching_retrieval.py・test_pubsub_bus_sdk_calls.py)。`compose.yaml`・`Makefile`・`frontend/`・`prototype/`・`docker/`・`.mise.toml`・`.gitignore`・`README.md`・`docs/`(01〜12・learn・reviews)。`docs/plans/STATUS.md`(スーパーバイザー管理)。`docs/plans/M0/`・`docs/plans/M1/`・`docs/plans/M2/` の既存ファイル(design・過去plan・過去report)
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4の後続単位スコープ):
  - **Layer 4 Jev 本体**(ws-5): System One IF・フォールバック切替・K_j=8配分・jev_result記録・`request_execution` の呼び出し・deny時の match_candidates.status=skipped 遷移の実行(遷移先の意味はdesign §1.4-1が確定済み・書き込むのはws-5)・`record_execution` の呼び出し
  - **30分Bucket再評価・catch-upスキャン**(ws-6)。入口での reeval ガード再利用もws-6(本単位はフック経由の呼び出しのみ)
  - **circuit breaker・障害注入・G2ハーネス**(ws-8・10 §4.5)
  - **リセットジョブ本体とスケジューラ**(M3-4・supervisor承認済み): 0時キック・保留キュー再評価イベント発行。カウンタのJSTリセット正確性は本単位の日付キー方式が常時担保する(design §2.5)
  - **alertの外部通知媒体**(FCM等・supervisor承認済み: ci段階は構造化ログ)。宛先・エスカレーションは運用設計(引用#9)
  - **トレース計測・Intent別/ユーザー別ダッシュボード**(M4)。本単位はalert用の集計関数(scan_report)のみ
  - **cheap_score重み・正規化パラメータの運用調整**(06 §4のとおり初期値・評価は09)
  - **Jev予算GuardのLayer 3パイプラインへの組み込み**(Guardの呼び出しは「Jev実行の直前」=ws-5。本単位は部品とunit試験のみ — design §2.9-6)
  - **reevalスキップ理由の match_events payload への記録**(design §2.9-8。構造化ログで足りる)
  - **cheap_judge_scoreの小数丸め**(design §2.9-9)
  - **K_c=20・各上限値のsettings化**(design §2.9-4)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 10で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分・既存試験の機械的追随を含む)
   検証: `make lint && make test` — ともにexit 0
2. **integration 1ファイル(5試験)が収集できる** — **ただし実行はしない**(§0。実DB・実Redis・compose常設apiが必要なためスーパーバイザー検証時に実施)
   検証(実装側): `cd backend && uv run pytest --collect-only tests/integration/test_matching_cheapjudge.py -q` がexit 0(5件収集)。報告書に「**test-ci=スーパーバイザー検証待ち**」と記録
3. **実時間参照が `core/clock.py` のみ(cost/・matching/・stage1.py・main.py 配下はヒットしない)**
   検証: `rg -n 'datetime\\.now|utcnow|time\\.time|time\\.monotonic|time\\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
4. **`backend/alembic/` と `docs/`(01〜12)に差分なし**
   検証: `git diff --stat main -- backend/alembic docs` — 出力なし(reportはTask 10で作成後コミットするため、この検証はreportコミット前に実施)
5. **変更ファイルが§4の一覧どおり(作成12+変更12=24ファイル)**
   検証: Task 10 Step 3の報告コミット後に `git diff --name-only main | sort` が§4の一覧(24ファイル・report込み)と完全一致。`git status --short` が空(未コミット変更なし)
6. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし
7. **既存unit試験がすべてグリーンのまま**(既存試験の期待値変更は§4に列挙した機械的追随のみであることの証明として `make test` の全件数を報告書へ記録し、変更が列挙対象以外に及んでいないことを§4との突合で示す)

## 7. 報告形式

**結果ファイル**: `docs/plans/M2/ws-4-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M2 ws-4(Layer 3 Cheap Judge + コスト保護)実行報告

- ブランチ: m2-ws-4 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も)> |
| 2 | integration 5試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | <collect-only出力> |
| 3 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 4 | alembic・docs無変更 | PASS/FAIL | <git diff --stat 出力(空なら「空」)> |
| 5 | 変更ファイル=§4の24ファイル | PASS/FAIL | <git diff --name-only出力> |
| 6 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 7 | 既存unit試験グリーン維持 | PASS/FAIL | <make test 全件数と§4列挙以外の期待値変更がないことの確認結果> |

## design §5 実装時確認事項の結果
4. jsonb列の読み取り型: <integration試験1の実測(structured_dataがstr/dictどちらで返ったか。
   unitのsoft_texts(str解釈)が通ったことと併記。いずれでも設計不変 — design §5-4>
5. integrationのtest_matching_retrieval.py回帰: <スーパーバイザー検証時のtest-ci結果で追記。
   runner外部契約不変のため無変更で通る見込み(design §5-5)。影響があった場合は理由を記録>
6. (ws-5引継ぎ)Guard呼び出し契約: request_execution(intent_id, user_id) をLayer 4実行直前に
   呼ぶこと・deny時のstatus=skipped遷移とrecord_execution(provider)の実API呼び出しベース計上はws-5
7. (ws-6引継ぎ)reevalガードの再利用: Bucket再評価・catch-upスキャンの入口でも同一部品を使用

## 固定値の変更有無(design.md §2・本計画§9)
- Layer 2と同一トランザクション・同一経路(design §2.1案A): 変更なし / 変更あり(<前→後+理由>)
- §2.2の実装定義(時間/予算/語彙の正規化・重み): 変更なし / 変更あり(<前→後+理由>)
- INCR先行・denyも消費(design §2.4): 変更なし / 変更あり(<前→後+理由>)
- 日付キー切替方式(design §2.5): 変更なし / 変更あり(<前→後+理由>)
- 本計画§9のIF確定事項(シグネチャ・定数・キー形式・TTL): 変更なし / 変更あり(<前→後+理由>)

## スーパーバイザー検証手順(test-ci実行時)
1. `docker compose build api` — apiイメージ再ビルド(STATUS運用ルール4。worker監視対象コードを
   含むため必須。マイグレーション追加なしのため `make migrate` は不要・alembic_version不動)
2. `make test-ci` — 既存全数+test_matching_cheapjudge 5件がグリーンで完了条件2を検証
3. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空(運用ルール5)
4. test-ci実行後、試験が残した Redis キー(prefix掃除)と user データ(teardown)が残っていない
   ことを1回手動確認(design §4.3-4・対抗策の実効性検証・初回のみ):
   redis-cli keys 'ws4-*' が空・ci-db側は subject LIKE 'm2ws4-%' のusers行が0件

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件7項目の結果一覧(項目2の実行部分は「test-ci=スーパーバイザー検証待ち」)。

---

## 8. 実装ステップ(TDD。Task 1〜10の順で実行する)

### Task 1: layer3.py(cheap_score純関数群)

**Files:**
- Create: `backend/src/latch/worker/matching/layer3.py`
- Test: `backend/tests/unit/matching/test_layer3.py`

**Interfaces:**
- Consumes: なし(標準ライブラリのみ・依存ゼロ。Origin/RetrievedCandidate を import しない — §9-4)
- Produces(Task 5・6・integration試験が使用):
  - 定数: `K_CHEAP = 20`・`TIME_SATURATION_MIN = 180`・`BUDGET_SATURATION_YEN = 3000`・`NEUTRAL = 0.5`・`WEIGHT_SIMILARITY = 0.5`・`WEIGHT_RULE = 0.3`・`WEIGHT_VOCAB = 0.2`
  - `ScoredCandidate`(frozen dataclass): `intent_id: uuid.UUID` / `version: int` / `similarity: float` / `cheap_score: float`
  - `soft_texts(structured_data: object) -> tuple[str, ...]`(downgraded_from_ng != true の text のみ抽出。jsonb列のstr返りはjson.loadsで解釈)
  - `time_closeness(start_a: datetime, start_b: datetime) -> float`(1 − min(Δmin,180)/180)
  - `budget_closeness(budget_a: int | None, budget_b: int | None) -> float`(1 − min(|Δ|,3000)/3000・NULLは0.5)
  - `rule_score(*, origin_time_start, cand_time_start, origin_budget, cand_budget) -> float`((時間近さ+予算近さ)/2)
  - `vocab_overlap(texts_a: tuple[str, ...], texts_b: tuple[str, ...]) -> float`(bigram集合のJaccard・双方空は0.5)
  - `_bigrams(texts: tuple[str, ...]) -> frozenset[str]`(NFKC正規化・文字bigram・2文字未満は空・text間をまたがない)
  - `cheap_score(similarity: float, rule: float, vocab: float) -> float`(0.5/0.3/0.2合成・丸めなし)
  - `select_top_kc(scored: list[ScoredCandidate]) -> list[ScoredCandidate]`(cheap_score降順・同点intent_id昇順の上位K_CHEAP)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_layer3.py` を作成:

```python
"""layer3(cheap_score純関数群)のunit試験(design §4.1)。

すべて境界値を手計算と照合する決定的検査(06 §4の決定性要件・
同一入力→同一出力)。実DBとの組み合わせはintegration(test-ci)が担う。
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from latch.worker.matching.layer3 import (
    BUDGET_SATURATION_YEN,
    K_CHEAP,
    NEUTRAL,
    TIME_SATURATION_MIN,
    ScoredCandidate,
    _bigrams,
    budget_closeness,
    cheap_score,
    rule_score,
    select_top_kc,
    soft_texts,
    time_closeness,
    vocab_overlap,
)

T0 = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


def test_constants_pin_docs_values():
    """docs確定値のピン(§9固定値)。"""
    assert K_CHEAP == 20  # 06 §8 D-24
    assert TIME_SATURATION_MIN == 180  # design §2.2(Intent実効寿命3時間=03 D-19)
    assert BUDGET_SATURATION_YEN == 3000  # design §2.2初期値
    assert NEUTRAL == 0.5


def test_time_closeness_boundaries():
    """Δ0=1.0・Δ180=0.0・Δ90=0.5・Δ240=0.0(飽和)・対称性。"""
    assert time_closeness(T0, T0) == 1.0
    assert time_closeness(T0, T0 + timedelta(minutes=180)) == 0.0
    assert time_closeness(T0, T0 + timedelta(minutes=90)) == 0.5
    assert time_closeness(T0, T0 + timedelta(minutes=240)) == 0.0
    assert time_closeness(T0 + timedelta(minutes=240), T0) == 0.0


def test_budget_closeness_boundaries():
    """差0=1.0・差3000=0.0・差1500=0.5・NULL系=中立0.5(design §2.2)。"""
    assert budget_closeness(3000, 3000) == 1.0
    assert budget_closeness(3000, 0) == 0.0
    assert budget_closeness(3000, 1500) == 0.5
    assert budget_closeness(3000, 5000) == 0.0  # 差2000で飽和(3000超)
    assert budget_closeness(None, 1000) == NEUTRAL
    assert budget_closeness(1000, None) == NEUTRAL
    assert budget_closeness(None, None) == NEUTRAL


def test_rule_score_is_mean_of_two():
    """ルールスコア=(時間近さ+予算近さ)/2(design §2.2)。"""
    rule = rule_score(
        origin_time_start=T0,
        cand_time_start=T0 + timedelta(minutes=90),  # 0.5
        origin_budget=3000,
        cand_budget=1500,  # 0.5
    )
    assert rule == 0.5
    rule2 = rule_score(
        origin_time_start=T0,
        cand_time_start=T0,  # 1.0
        origin_budget=None,
        cand_budget=None,  # 0.5(中立)
    )
    assert rule2 == 0.75


def test_bigrams_nfkc_short_and_no_cross_text():
    """NFKC正規化・2文字未満は空・textをまたいだbigramを作らない。"""
    assert _bigrams(("焼肉",)) == frozenset({"焼肉"})
    assert _bigrams(("あ",)) == frozenset()  # 2文字未満
    assert _bigrams(()) == frozenset()
    assert _bigrams(("ＡＢ",)) == _bigrams(("AB",))  # NFKC: 全角→半角
    assert _bigrams(("AB", "CD")) == frozenset({"AB", "CD"})
    assert "BC" not in _bigrams(("AB", "CD"))  # またがない


def test_vocab_overlap_jaccard():
    """同一文言=1.0・共通なし=0.0・部分一致=手計算値・空の扱い。"""
    assert vocab_overlap(("焼肉",), ("焼肉",)) == 1.0
    assert vocab_overlap(("焼肉",), ("寿司",)) == 0.0
    # design §4.1の手計算例: {焼肉} vs {焼肉,肉好,好き} = 1/3
    assert vocab_overlap(("焼肉",), ("焼肉好き",)) == pytest.approx(1 / 3)
    assert vocab_overlap((), ()) == NEUTRAL  # 双方空=中立(design §2.2)
    assert vocab_overlap(("焼肉",), ()) == 0.0  # 片方空=共通なし


def test_soft_texts_excludes_downgraded():
    """downgraded_from_ng=trueの文言は抽出しない(06 §4・引用#1)。"""
    sd = {
        "soft_constraints": [
            {"text": "焼肉", "downgraded_from_ng": False},
            {"text": "個室", "downgraded_from_ng": True},
            {"text": "静か"},  # キー欠落=降格ではない
        ]
    }
    assert soft_texts(sd) == ("焼肉", "静か")
    assert soft_texts(json.dumps(sd)) == ("焼肉", "静か")  # jsonbのstr返り
    assert soft_texts(None) == ()
    assert soft_texts("{}") == ()
    assert soft_texts('{"soft_constraints": []}') == ()
    assert soft_texts('{"soft_constraints": "not-a-list"}') == ()


def test_cheap_score_weights_pin():
    """0.5/0.3/0.2 の手計算照合(06 §4)・丸めなし。"""
    assert cheap_score(1.0, 1.0, 1.0) == 1.0
    assert cheap_score(0.0, 0.0, 0.0) == 0.0
    expected = 0.5 * 0.8 + 0.3 * 0.5 + 0.2 * (1 / 3)
    assert cheap_score(0.8, 0.5, 1 / 3) == expected  # 同一式=完全同一float


def test_select_top_kc_tie_break():
    """降順・同点はintent_id昇順(D-24)。"""
    scored = [
        ScoredCandidate(_uid(30), 1, 1.0, 0.9),
        ScoredCandidate(_uid(10), 1, 1.0, 0.5),  # 同点グループ
        ScoredCandidate(_uid(2), 1, 1.0, 0.5),
        ScoredCandidate(_uid(20), 1, 1.0, 0.5),
        ScoredCandidate(_uid(1), 1, 1.0, 0.1),
    ]
    top = select_top_kc(scored)
    assert [s.intent_id for s in top] == [
        _uid(30),
        _uid(2),
        _uid(10),
        _uid(20),
        _uid(1),
    ]


def test_select_top_kc_truncates_at_20_deterministic():
    """21件以上で20件・同一入力2回で完全同一(10 §4.6・丸めなし)。"""
    scored = [
        ScoredCandidate(_uid(i), 1, 1.0, 1.0 - i * 0.001) for i in range(25)
    ]
    top = select_top_kc(scored)
    assert len(top) == 20
    assert top[0].intent_id == _uid(0)
    assert top[-1].intent_id == _uid(19)
    again = select_top_kc(scored)
    assert again == top  # dataclass完全一致(順序・score含む)
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer3.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.matching.layer3'` — importで収集エラー)

- [ ] **Step 3: layer3.py を実装する**

`backend/src/latch/worker/matching/layer3.py`:

```python
"""Layer 3 Cheap Judge(06 §4・design §2.2・§2.3)。

cheap_score = 0.5×類似度 + 0.3×ルールスコア + 0.2×語彙重なり の線形合成。
すべて決定的(依存ゼロ・追加APIコストゼロ)。06 §4 は要素と重みのみを確定し
正規化の詳細を規定しないため、実装定義は design §2.2(重みと同じく運用
データで調整対象)。語彙抽出に形態素解析器を使わない(依存追加と辞書版依存
の非決定性 — design §2.2)。D-04降格(downgraded_from_ng=true)は語彙重なり
の計算対象から明示的に除外しLayer 4入力へ渡る(06 §4)。丸めない(同点判定と
決定性を桁依存にしない — design §2.2)。
"""

from __future__ import annotations

import json
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

# 06 §8 D-24: Cheap Judge から Layer 4 へ渡す出力数の上限
K_CHEAP = 20
# design §2.2 実装定義の初期値(調整はこの定数1箇所に閉じる — design §5-8)
TIME_SATURATION_MIN = 180  # Intent実効寿命3時間(03 D-19)を飽和点に
BUDGET_SATURATION_YEN = 3000  # Layer 1のペア予算fail線500円(06 §2)の6倍
NEUTRAL = 0.5  # NULL・空語彙の中立値(満点にも零点にもしない)
# 06 §4 の重み(初期値・運用データで調整 — 引用#1)
WEIGHT_SIMILARITY = 0.5
WEIGHT_RULE = 0.3
WEIGHT_VOCAB = 0.2


@dataclass(frozen=True)
class ScoredCandidate:
    """Layer 3 通過候補(Layer 2 結果+cheap_score。design §2.3)。"""

    intent_id: uuid.UUID
    version: int
    similarity: float
    cheap_score: float


def soft_texts(structured_data: object) -> tuple[str, ...]:
    """soft_constraints から語彙抽出(降格除外 — 06 §4・引用#13)。

    jsonb列はstrで返りうるためjson.loadsで解釈する(worker/embedding.pyと
    同規律 — design §5-4)。構造が想定外の場合は空(安全側)。
    """
    if isinstance(structured_data, str):
        try:
            structured_data = json.loads(structured_data)
        except json.JSONDecodeError:
            return ()
    if not isinstance(structured_data, dict):
        return ()
    raw = structured_data.get("soft_constraints")
    if not isinstance(raw, list):
        return ()
    return tuple(
        item["text"]
        for item in raw
        if isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and bool(item["text"])
        and item.get("downgraded_from_ng") is not True
    )


def time_closeness(start_a: datetime, start_b: datetime) -> float:
    """時間近さ: 1 − min(Δmin, 180)/180(design §2.2)。

    開始時刻差のみ(交差判定はLayer 1が持つ。time_endとの組合せ評価はしない)。
    """
    delta_min = abs((start_a - start_b).total_seconds()) / 60.0
    return 1.0 - min(delta_min, TIME_SATURATION_MIN) / TIME_SATURATION_MIN


def budget_closeness(budget_a: int | None, budget_b: int | None) -> float:
    """予算近さ: 1 − min(|Δ|, 3000)/3000(design §2.2)。

    NULL=制約なし(05 §2)のため近さが定義できない→中立0.5。
    """
    if budget_a is None or budget_b is None:
        return NEUTRAL
    delta = abs(budget_a - budget_b)
    return 1.0 - min(delta, BUDGET_SATURATION_YEN) / BUDGET_SATURATION_YEN


def rule_score(
    *,
    origin_time_start: datetime,
    cand_time_start: datetime,
    origin_budget: int | None,
    cand_budget: int | None,
) -> float:
    """ルールスコア: (時間近さ + 予算近さ) / 2(design §2.2の等分)。"""
    return (
        time_closeness(origin_time_start, cand_time_start)
        + budget_closeness(origin_budget, cand_budget)
    ) / 2.0


def _bigrams(texts: tuple[str, ...]) -> frozenset[str]:
    """文字bigram集合(NFKC正規化・2文字未満は空・text間をまたがない)。"""
    grams: set[str] = set()
    for text in texts:
        normalized = unicodedata.normalize("NFKC", text)
        if len(normalized) < 2:
            continue
        grams.update(normalized[i : i + 2] for i in range(len(normalized) - 1))
    return frozenset(grams)


def vocab_overlap(texts_a: tuple[str, ...], texts_b: tuple[str, ...]) -> float:
    """語彙重なり: bigram集合のJaccard係数(design §2.2)。

    双方の語彙集合が空(soft constraintsなし)は中立0.5。
    """
    a = _bigrams(texts_a)
    b = _bigrams(texts_b)
    union = a | b
    if not union:
        return NEUTRAL
    return len(a & b) / len(union)


def cheap_score(similarity: float, rule: float, vocab: float) -> float:
    """0.5/0.3/0.2 の線形合成(06 §4)。丸めない。"""
    return WEIGHT_SIMILARITY * similarity + WEIGHT_RULE * rule + WEIGHT_VOCAB * vocab


def select_top_kc(scored: list[ScoredCandidate]) -> list[ScoredCandidate]:
    """上位 K_c=20(cheap_score降順・同点はintent_id昇順 — D-24・design §2.3)。"""
    return sorted(scored, key=lambda s: (-s.cheap_score, s.intent_id))[:K_CHEAP]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer3.py -v`
Expected: PASS(10件)

- [ ] **Step 5: 全体確認してCommit**

Run: `make lint && make test`
Expected: ともにexit 0(既存試験は無影響 — 新規ファイルのみのため)

```bash
git add backend/src/latch/worker/matching/layer3.py \
  backend/tests/unit/matching/test_layer3.py
git status --short  # 上記2ファイルのみ
git commit -m "feat: Layer 3 cheap_score純関数群(決定的・依存ゼロ)"
```

### Task 2: cost store・errors(Redis操作を閉じ込める)

**Files:**
- Create: `backend/src/latch/worker/cost/__init__.py`(この時点ではdocstringのみ)
- Create: `backend/src/latch/worker/cost/errors.py`
- Create: `backend/src/latch/worker/cost/store.py`
- Test: `backend/tests/unit/cost/test_cost_store.py`

**Interfaces:**
- Consumes: `redis.asyncio.Redis`(decode_responses=True を注入)・`Clock`(guard側がバケットを導出 — storeは受け取るのみ)
- Produces(Task 3・4・8・integration試験が使用):
  - `JevCostDependencyError(Exception)`(errors.py。Redis例外のfail-closedラップ — ratelimitのRateLimitDependencyErrorと同型)
  - `JevCostStore(redis, *, key_prefix: str = "")`:
    - `async incr_daily(day: str) -> int`(key `{prefix}jev:daily:{yyyymmdd}`・TTL 48h)
    - `async incr_monthly(month: str) -> int`(key `{prefix}jev:monthly:{yyyymm}`・TTL 45日)
    - `async incr_intent(intent_id: str, day: str) -> int`(key `{prefix}jev:intent:{intent_id}:{yyyymmdd}`・TTL 48h)
    - `async incr_user(user_id: str, day: str) -> int`(key `{prefix}jev:user:{user_id}:{yyyymmdd}`・TTL 48h)
    - `async record_execution(provider: str, day: str) -> int`(key `{prefix}jev:exec:{yyyymmdd}:{provider}`・TTL 48h。provider="typesafe_jev" / "fallback_llm")
    - `async set_reeval_nx(intent_id: str) -> bool`(key `{prefix}reeval:{intent_id}`・SET NX EX 1800。True=確保成功)
    - `async scan_report(day: str) -> dict`(キー形式は§9-5。`{"intent_top": [...], "user_top": [...], "exec_breakdown": {...}}`。上位10件)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/cost/test_cost_store.py` を作成:

```python
"""JevCostStore: キー形式・TTL・INCR+EXPIRE・key_prefix(design §2.4・§4.1)。

fakeredis(redis-pyのコマンド解釈経路をそのまま実行)。ratelimit/test_store.py流儀。
"""

import fakeredis.aioredis
import pytest

from latch.worker.cost.store import JevCostStore


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


async def test_incr_daily_key_and_ttl(redis):
    store = JevCostStore(redis)
    assert await store.incr_daily("20260929") == 1
    assert await store.incr_daily("20260929") == 2  # 積算
    assert await redis.get("jev:daily:20260929") == "2"
    assert 0 < await redis.ttl("jev:daily:20260929") <= 48 * 3600


async def test_incr_monthly_key_and_ttl(redis):
    store = JevCostStore(redis)
    await store.incr_monthly("202609")
    assert await redis.get("jev:monthly:202609") == "1"
    assert 0 < await redis.ttl("jev:monthly:202609") <= 45 * 24 * 3600


async def test_incr_intent_and_user_keys(redis):
    store = JevCostStore(redis)
    await store.incr_intent("i-1", "20260929")
    await store.incr_user("u-1", "20260929")
    assert await redis.get("jev:intent:i-1:20260929") == "1"
    assert await redis.get("jev:user:u-1:20260929") == "1"
    assert 0 < await redis.ttl("jev:intent:i-1:20260929") <= 48 * 3600


async def test_record_execution_provider_keys(redis):
    """第一候補/フォールバックの経路別キー(D-16内訳・design §2.4)。"""
    store = JevCostStore(redis)
    await store.record_execution("typesafe_jev", "20260929")
    await store.record_execution("typesafe_jev", "20260929")
    await store.record_execution("fallback_llm", "20260929")
    assert await redis.get("jev:exec:20260929:typesafe_jev") == "2"
    assert await redis.get("jev:exec:20260929:fallback_llm") == "1"


async def test_day_key_switch_resets_counter(redis):
    """日付キー切替=リセット(JST 0時を跨ぐと新キーで0から — design §2.5)。"""
    store = JevCostStore(redis)
    assert await store.incr_daily("20260929") == 1
    assert await store.incr_daily("20260930") == 1  # 新キー=別カウンタ
    assert await store.incr_intent("i-1", "20260930") == 1


async def test_set_reeval_nx_and_ttl(redis):
    store = JevCostStore(redis)
    assert await store.set_reeval_nx("i-1") is True
    assert await store.set_reeval_nx("i-1") is False  # 30分以内
    assert 0 < await redis.ttl("reeval:i-1") <= 1800  # 06 §5の30分


async def test_key_prefix_isolates_namespace(redis):
    """key_prefix=integration試験での共用Redis干渉防止(design §3.1)。"""
    store = JevCostStore(redis, key_prefix="it-")
    await store.incr_daily("20260929")
    await store.set_reeval_nx("i-1")
    assert await redis.get("it-jev:daily:20260929") == "1"
    assert await redis.exists("it-reeval:i-1") == 1
    assert await redis.keys("jev:*") == []
    assert await redis.keys("reeval:*") == []


async def test_scan_report_aggregates_top_and_breakdown(redis):
    """80% alert添付レポートの集計(design §2.4-6)。別日は入らない。"""
    store = JevCostStore(redis)
    for _ in range(3):
        await store.incr_intent("i-a", "20260929")
    await store.incr_intent("i-b", "20260929")
    for _ in range(2):
        await store.incr_user("u-x", "20260929")
    await store.record_execution("typesafe_jev", "20260929")
    await store.record_execution("fallback_llm", "20260929")
    await store.incr_intent("i-a", "20260928")  # 別日=対象外

    report = await store.scan_report("20260929")
    assert report["intent_top"][0] == ("jev:intent:i-a:20260929", 3)
    assert ("jev:intent:i-b:20260929", 1) in report["intent_top"]
    assert report["user_top"][0] == ("jev:user:u-x:20260929", 2)
    assert report["exec_breakdown"] == {
        "jev:exec:20260929:fallback_llm": 1,
        "jev:exec:20260929:typesafe_jev": 1,
    }
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/cost/test_cost_store.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.cost'`)

- [ ] **Step 3: cost パッケージを実装する**

`backend/src/latch/worker/cost/__init__.py`(この時点。Task 4で最終形へ更新):

```python
"""Jevコスト保護(D-16・06 §5)と再評価頻度ガード(M2 ws-4・design §2.4)。

Guard呼び出し(Layer 4実行直前)と record_execution(実際のAPI呼び出し後)は
ws-5。本単位のパイプライン組み込みは ReevalGuard のみ(design §2.6)。
"""
```

`backend/src/latch/worker/cost/errors.py`:

```python
"""コスト保護系例外(design §2.4-7。ratelimit/errors.pyと同型)。"""


class JevCostDependencyError(Exception):
    """Redis接続障害(fail-closed — コスト保護の失敗を実行の通り抜けにしない)。

    呼び出し側(ws-5のLayer 4直前・ws-4のreeval)は再試行経路へ載せる。
    """
```

`backend/src/latch/worker/cost/store.py`:

```python
"""Redis上のJevコスト保護カウンタ(design §2.4)。

鍵(接頭辞 jev: — rl:/auth: と名前空間を分ける。ratelimit/store.pyのコメント
「JevカウンタはM2で別接頭辞」どおり):
  日次:   INCR jev:daily:{yyyymmdd}                TTL 48時間
  月次:   INCR jev:monthly:{yyyymm}                 TTL 45日
  Intent: INCR jev:intent:{intent_id}:{yyyymmdd}    TTL 48時間
  ユーザ: INCR jev:user:{user_id}:{yyyymmdd}        TTL 48時間
  内訳:   INCR jev:exec:{yyyymmdd}:{provider}       TTL 48時間
  再評価: SET reeval:{intent_id} "1" NX EX 1800(06 §5のキー名そのまま)

INCRとEXPIREはpipelineで毎回併発(ratelimitと同一規律)。TTLは掃除用に留め、
リセット表現には使わない(04 §5 — リセット=日付キー切替。JST 0時を跨ぐと
新キーで0から始まる=自己修復を内包 — design §2.5)。バケット文字列
(yyyymmdd等)は呼び出し側(guard)がClockから導出する(C2)。key_prefixは
integration試験での共用Redis干渉防止用(既定"")。
"""

from __future__ import annotations

import redis.asyncio as aioredis

_TTL_DAILY_S = 48 * 3600
_TTL_MONTHLY_S = 45 * 24 * 3600
_REEVAL_TTL_S = 1800  # 06 §5: 再評価頻度30分
_REPORT_TOP_N = 10  # alert添付レポートの上位件数(実装定義 — §9-6)


class JevCostStore:
    """Redis操作を閉じ込めるStore(decode_responses=True のRedisを注入)。"""

    def __init__(self, redis: aioredis.Redis, *, key_prefix: str = "") -> None:
        self._redis = redis
        self._prefix = key_prefix

    async def _incr(self, key: str, ttl_s: int) -> int:
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_s)
        result = await pipe.execute()
        return int(result[0])

    async def incr_daily(self, day: str) -> int:
        """D-16 日次グローバルカウンタ(30,000)。"""
        return await self._incr(f"{self._prefix}jev:daily:{day}", _TTL_DAILY_S)

    async def incr_monthly(self, month: str) -> int:
        """D-16 月次グローバルカウンタ(600,000)。"""
        return await self._incr(f"{self._prefix}jev:monthly:{month}", _TTL_MONTHLY_S)

    async def incr_intent(self, intent_id: str, day: str) -> int:
        """1Intent 40回/日(06 §5)。"""
        return await self._incr(
            f"{self._prefix}jev:intent:{intent_id}:{day}", _TTL_DAILY_S
        )

    async def incr_user(self, user_id: str, day: str) -> int:
        """1ユーザー120回/日(06 §5・Active上限5×40の共有プール)。"""
        return await self._incr(f"{self._prefix}jev:user:{user_id}:{day}", _TTL_DAILY_S)

    async def record_execution(self, provider: str, day: str) -> int:
        """第一候補/フォールバック内訳(D-16「実際のAPI呼び出しを計上」)。

        実行経路確定後に+1する(ws-5が呼ぶ — design §2.4-3)。
        """
        return await self._incr(
            f"{self._prefix}jev:exec:{day}:{provider}", _TTL_DAILY_S
        )

    async def set_reeval_nx(self, intent_id: str) -> bool:
        """reeval:{intent_id} の SET NX EX 1800(06 §5・design §2.6)。

        True=確保成功(評価してよい)・False=30分以内の再評価。
        """
        return bool(
            await self._redis.set(
                f"{self._prefix}reeval:{intent_id}", "1", nx=True, ex=_REEVAL_TTL_S
            )
        )

    async def scan_report(self, day: str) -> dict:
        """80% alert添付の消費レポート(design §2.4-6・引用#7)。

        Intent別・ユーザー別の消費上位リストと経路別内訳。alertは80%跨ぎ時
        のみ(1日/月に高々数回)のためSCANのコストは許容する(design §2.4-6)。
        """
        intent_counts = await self._scan_counts(f"jev:intent:*:{day}")
        user_counts = await self._scan_counts(f"jev:user:*:{day}")
        exec_counts = await self._scan_counts(f"jev:exec:{day}:*")
        return {
            "intent_top": self._top(intent_counts),
            "user_top": self._top(user_counts),
            "exec_breakdown": dict(sorted(exec_counts.items())),
        }

    async def _scan_counts(self, pattern: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match=f"{self._prefix}{pattern}", count=100
            )
            for key in keys:
                value = await self._redis.get(key)
                if value is not None:
                    counts[key] = int(value)
            if cursor == 0:
                break
        return counts

    @staticmethod
    def _top(counts: dict[str, int]) -> list[tuple[str, int]]:
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_REPORT_TOP_N]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/cost/test_cost_store.py -v`
Expected: PASS(8件)

- [ ] **Step 5: 全体確認してCommit**

Run: `make lint && make test`
Expected: ともにexit 0

```bash
git add backend/src/latch/worker/cost/__init__.py \
  backend/src/latch/worker/cost/errors.py \
  backend/src/latch/worker/cost/store.py \
  backend/tests/unit/cost/test_cost_store.py
git status --short
git commit -m "feat: JevコストカウンタStore(jev:接頭辞・日付キー切替・reeval NX)"
```

### Task 3: cost guard(4カウンタINCR先行判定・alert)

**Files:**
- Create: `backend/src/latch/worker/cost/guard.py`
- Test: `backend/tests/unit/cost/test_cost_guard.py`

**Interfaces:**
- Consumes: `JevCostStore`(Task 2)・`Clock`/`FakeClock`・`JevCostDependencyError`(Task 2)
- Produces(Task 4・8・ws-5・integration試験が使用):
  - 定数: `DAILY_LIMIT = 30_000`・`DAILY_ALERT = 24_000`・`MONTHLY_LIMIT = 600_000`・`MONTHLY_ALERT = 480_000`・`INTENT_DAILY_LIMIT = 40`・`USER_DAILY_LIMIT = 120`
  - deny理由定数: `DENY_INTENT_DAILY = "intent_daily"`・`DENY_USER_DAILY = "user_daily"`・`DENY_GLOBAL_DAILY = "global_daily"`・`DENY_GLOBAL_MONTHLY = "global_monthly"`
  - `JevDecision`(frozen dataclass): `allowed: bool` / `deny_reason: str | None`
  - `structured_log_alert(event: dict) -> None`(alert媒体のci段階実体・`latch.cost.alert` logger・supervisor承認)
  - `JevCostGuard(*, store, clock, alert_emitter=structured_log_alert)`:
    - `async request_execution(intent_id: uuid.UUID, user_id: uuid.UUID) -> JevDecision`(4カウンタINCR先行・denyも消費・80%跨ぎalert1回・月次上限到達ログ(復帰予定明示)1回・Redis例外はJevCostDependencyError)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/cost/test_cost_guard.py` を作成:

```python
"""JevCostGuard: INCR先行判定・deny理由・80%跨ぎalert・fail-closed(design §4.1)。

上限定数は実際の値(30,000等)のまま扱う。実際に30,000回INCRするのは重いため
Storeをスタブ化して任意のカウンタ値を返させ、境界(24000/24001・30001・480000・
600001・41・121)を検証する(Store自体のINCR正確性はtest_cost_storeが担保)。
"""

import logging
import uuid
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.worker.cost import errors as cost_errors
from latch.worker.cost.guard import (
    DAILY_ALERT,
    DAILY_LIMIT,
    INTENT_DAILY_LIMIT,
    MONTHLY_ALERT,
    MONTHLY_LIMIT,
    USER_DAILY_LIMIT,
    DENY_GLOBAL_DAILY,
    DENY_GLOBAL_MONTHLY,
    DENY_INTENT_DAILY,
    DENY_USER_DAILY,
    JevCostGuard,
    structured_log_alert,
)

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-29 21:00
IID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
UID = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


class StubStore:
    """初期値から+1ずつ返すStore差し替え(design §4.1)。scan_reportは空。"""

    def __init__(self, *, intent=0, user=0, daily=0, monthly=0) -> None:
        self.counts = {
            "intent": intent,
            "user": user,
            "daily": daily,
            "monthly": monthly,
        }

    async def incr_intent(self, intent_id: str, day: str) -> int:
        self.counts["intent"] += 1
        return self.counts["intent"]

    async def incr_user(self, user_id: str, day: str) -> int:
        self.counts["user"] += 1
        return self.counts["user"]

    async def incr_daily(self, day: str) -> int:
        self.counts["daily"] += 1
        return self.counts["daily"]

    async def incr_monthly(self, month: str) -> int:
        self.counts["monthly"] += 1
        return self.counts["monthly"]

    async def scan_report(self, day: str) -> dict:
        return {"intent_top": [], "user_top": [], "exec_breakdown": {}}


def _guard(store, emitter=None) -> JevCostGuard:
    return JevCostGuard(
        store=store,
        clock=FakeClock(NOW),
        alert_emitter=emitter if emitter is not None else (lambda event: None),
    )


def test_limit_constants_pin_docs_values():
    """D-16・06 §5 の確定値ピン(§9固定値)。"""
    assert DAILY_LIMIT == 30_000
    assert DAILY_ALERT == 24_000  # 80%
    assert MONTHLY_LIMIT == 600_000
    assert MONTHLY_ALERT == 480_000  # 80%
    assert INTENT_DAILY_LIMIT == 40
    assert USER_DAILY_LIMIT == 120


async def test_allow_within_all_limits():
    """全上限ちょうど(+1要求で境界値に達するが超えない)は許可。"""
    store = StubStore(intent=39, user=119, daily=23_999, monthly=479_999)
    decision = await _guard(store).request_execution(IID, UID)
    assert decision.allowed is True
    assert decision.deny_reason is None
    # 要求後のカウンタは境界値ちょうど(41/121/24000/480000ではない=超過なし)
    assert store.counts == {
        "intent": 40,
        "user": 120,
        "daily": 24_000,
        "monthly": 480_000,
    }


async def test_deny_reasons_at_each_boundary():
    """deny理由4種の判定(design §4.1の境界)。"""
    cases = [
        (dict(intent=40), DENY_INTENT_DAILY),  # 41回目
        (dict(user=120), DENY_USER_DAILY),  # 121回目
        (dict(daily=30_000), DENY_GLOBAL_DAILY),  # 30001回目
        (dict(monthly=600_000), DENY_GLOBAL_MONTHLY),  # 600001回目
    ]
    for initial, expected in cases:
        store = StubStore(**initial)
        decision = await _guard(store).request_execution(IID, UID)
        assert decision.allowed is False, initial
        assert decision.deny_reason == expected, initial


async def test_deny_priority_follows_design_enumeration():
    """複数上限同時超過は design §2.4-2 の列挙順(intent→user→daily→monthly)。"""
    store = StubStore(intent=40, user=120, daily=30_000, monthly=600_000)
    decision = await _guard(store).request_execution(IID, UID)
    assert decision.deny_reason == DENY_INTENT_DAILY


async def test_denied_requests_still_consume_counter():
    """INCR先行: deny後の再要求でもカウンタは増える(design §2.4-2・引用#8)。"""
    store = StubStore()
    guard = _guard(store)
    for _ in range(41):
        await guard.request_execution(IID, UID)
    assert store.counts["intent"] == 41  # 41回目でdeny済み
    await guard.request_execution(IID, UID)
    assert store.counts["intent"] == 42  # deny後も消費


async def test_daily_80pct_alert_fires_once_at_threshold():
    """戻り値==24000のとき1回だけ(23999・24001では発報しない — design §2.4-4)。"""
    events: list[dict] = []
    store = StubStore(daily=23_998)  # →23999: 発報しない
    await _guard(store, emitter=events.append).request_execution(IID, UID)
    assert events == []
    store2 = StubStore(daily=23_999)  # →24000: 発報
    await _guard(store2, emitter=events.append).request_execution(IID, UID)
    assert len(events) == 1
    assert events[0]["kind"] == "daily_80pct"
    assert events[0]["day"] == "20260929"
    assert events[0]["count"] == DAILY_ALERT
    assert events[0]["report"] == {"intent_top": [], "user_top": [], "exec_breakdown": {}}
    store3 = StubStore(daily=24_000)  # →24001: 発報しない
    await _guard(store3, emitter=events.append).request_execution(IID, UID)
    assert len(events) == 1


async def test_monthly_80pct_and_limit_alerts_with_resume_at():
    """月次480000跨ぎalert・600001到達で復帰予定(暦月初JST 0時)明示ログ1回。"""
    events: list[dict] = []
    store = StubStore(monthly=479_999)  # →480000: 80%発報
    decision = await _guard(store, emitter=events.append).request_execution(IID, UID)
    assert decision.allowed is True  # 480000 ≤ 上限
    assert [e["kind"] for e in events] == ["monthly_80pct"]

    events2: list[dict] = []
    store2 = StubStore(monthly=599_999)  # →600000: 上限内・発報なし
    decision2 = await _guard(store2, emitter=events2.append).request_execution(IID, UID)
    assert decision2.allowed is True
    assert events2 == []

    events3: list[dict] = []
    store3 = StubStore(monthly=600_000)  # →600001: deny+到達ログ
    decision3 = await _guard(store3, emitter=events3.append).request_execution(IID, UID)
    assert decision3.allowed is False
    assert decision3.deny_reason == DENY_GLOBAL_MONTHLY
    assert [e["kind"] for e in events3] == ["monthly_limit"]
    assert events3[0]["resume_at"] == "2026-10-01T00:00:00+09:00"  # 暦月初JST 0時


async def test_redis_exception_wrapped_fail_closed():
    """Redis例外は専用例外へ(fail-closed — design §2.4-7)。"""

    class BrokenStore(StubStore):
        async def incr_intent(self, intent_id: str, day: str) -> int:
            raise ConnectionError("redis down")

    with pytest.raises(cost_errors.JevCostDependencyError):
        await _guard(BrokenStore()).request_execution(IID, UID)


def test_structured_log_alert_emits_to_latch_cost_alert(caplog):
    """alert媒体=構造化ログ(supervisor承認・design §2.4-5)。"""
    with caplog.at_level(logging.WARNING, logger="latch.cost.alert"):
        structured_log_alert({"kind": "daily_80pct", "day": "20260929"})
    assert caplog.records[0].name == "latch.cost.alert"
    assert "daily_80pct" in caplog.records[0].getMessage()
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/cost/test_cost_guard.py -v`
Expected: FAIL(`ImportError: cannot import name 'JevCostGuard'`)

- [ ] **Step 3: guard.py を実装する**

`backend/src/latch/worker/cost/guard.py`:

```python
"""Jev実行のコスト保護判定(04 §4 D-16・06 §5・design §2.4)。

INCR先行: 4カウンタ(intent別40/日・user別120/日・日次30,000・月次600,000)を
INCRしてから上限と比較する。denyされた要求もカウントを消費する(判定と
カウントの間の並行すり抜けをRedis上で潰す — 引用#8「超過幅を1要求分」)。
80% alertと月次上限到達ログはINCR戻り値が丁度しきい値に等しいとき1回だけ
発報する(prev < threshold ≤ curr の跨ぎ検出。Redis INCRの原子性により跨ぎは
1回 — design §2.4-4)。alert媒体は構造化ログで開始(媒体差し替え可能な
emitter注入 — supervisor承認 2026-09-29)。Redis例外は専用例外でfail-closed
(ratelimitのRateLimitDependencyErrorと同型)。バケット文字列はClockから導出
(C2・引用#15)。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from latch.core.clock import Clock
from latch.worker.cost.errors import JevCostDependencyError
from latch.worker.cost.store import JevCostStore

logger = logging.getLogger(__name__)

# D-16(04 §4)・06 §5 の確定値(§9固定値・settings化しない — design §2.8-4)
DAILY_LIMIT = 30_000
DAILY_ALERT = 24_000  # 80%
MONTHLY_LIMIT = 600_000
MONTHLY_ALERT = 480_000  # 80%
INTENT_DAILY_LIMIT = 40
USER_DAILY_LIMIT = 120

# deny理由(JevDecision.deny_reason の値域・design §2.4-2の列挙順が優先順位)
DENY_INTENT_DAILY = "intent_daily"
DENY_USER_DAILY = "user_daily"
DENY_GLOBAL_DAILY = "global_daily"
DENY_GLOBAL_MONTHLY = "global_monthly"


def structured_log_alert(event: dict) -> None:
    """alert媒体のci段階実体(構造化ログ — supervisor承認・design §2.4-5)。

    宛先・エスカレーションは運用設計に委ねられているため(引用#9)、媒体は
    この関数の差し替えで切り替える。
    """
    logging.getLogger("latch.cost.alert").warning(
        "jev cost alert %s", json.dumps(event, ensure_ascii=False)
    )


@dataclass(frozen=True)
class JevDecision:
    """request_execution の結果(deny時は理由付き — 縮退記録はws-5)。"""

    allowed: bool
    deny_reason: str | None = None


class JevCostGuard:
    """Jev実行の直前に呼ぶ判定(design §2.4-1。呼び出し元はws-5)。"""

    def __init__(
        self,
        *,
        store: JevCostStore,
        clock: Clock,
        alert_emitter: Callable[[dict], None] = structured_log_alert,
    ) -> None:
        self._store = store
        self._clock = clock
        self._alert_emitter = alert_emitter

    def _day_bucket(self) -> str:
        return self._clock.jst_date().strftime("%Y%m%d")

    def _month_bucket(self) -> str:
        return self._clock.jst_date().strftime("%Y%m")

    def _next_month_start_jst(self) -> str:
        """復帰予定=暦月初のJST 0時(D-16・design §2.4-4)。"""
        today = self._clock.jst_date()
        year = today.year + (1 if today.month == 12 else 0)
        month = 1 if today.month == 12 else today.month + 1
        return f"{year:04d}-{month:02d}-01T00:00:00+09:00"

    async def request_execution(
        self, intent_id: uuid.UUID, user_id: uuid.UUID
    ) -> JevDecision:
        """4カウンタINCR先行判定(design §2.4-2)。denyもカウントを消費する。"""
        day = self._day_bucket()
        month = self._month_bucket()
        try:
            intent_count = await self._store.incr_intent(str(intent_id), day)
            user_count = await self._store.incr_user(str(user_id), day)
            daily_count = await self._store.incr_daily(day)
            monthly_count = await self._store.incr_monthly(month)
        except Exception as exc:
            raise JevCostDependencyError("jev cost store unavailable") from exc
        if daily_count == DAILY_ALERT:
            await self._emit(
                {"kind": "daily_80pct", "day": day, "count": daily_count}, day
            )
        if monthly_count == MONTHLY_ALERT:
            await self._emit(
                {"kind": "monthly_80pct", "month": month, "count": monthly_count},
                day,
            )
        if monthly_count == MONTHLY_LIMIT + 1:
            await self._emit(
                {
                    "kind": "monthly_limit",
                    "month": month,
                    "count": monthly_count,
                    "resume_at": self._next_month_start_jst(),
                },
                day,
            )
        if intent_count > INTENT_DAILY_LIMIT:
            return JevDecision(allowed=False, deny_reason=DENY_INTENT_DAILY)
        if user_count > USER_DAILY_LIMIT:
            return JevDecision(allowed=False, deny_reason=DENY_USER_DAILY)
        if daily_count > DAILY_LIMIT:
            return JevDecision(allowed=False, deny_reason=DENY_GLOBAL_DAILY)
        if monthly_count > MONTHLY_LIMIT:
            return JevDecision(allowed=False, deny_reason=DENY_GLOBAL_MONTHLY)
        return JevDecision(allowed=True, deny_reason=None)

    async def _emit(self, event: dict, day: str) -> None:
        """alert発報(引用#7: レポート本文を添付)。scan失敗でalert自体は落とさない。"""
        try:
            event["report"] = await self._store.scan_report(day)
        except Exception:
            logger.warning("jev alert report scan failed", exc_info=True)
        self._alert_emitter(event)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/cost/test_cost_guard.py -v`
Expected: PASS(9件)

- [ ] **Step 5: 全体確認してCommit**

Run: `make lint && make test`
Expected: ともにexit 0

```bash
git add backend/src/latch/worker/cost/guard.py \
  backend/tests/unit/cost/test_cost_guard.py
git status --short
git commit -m "feat: JevCostGuard(4カウンタINCR先行・80%跨ぎalert・fail-closed)"
```

### Task 4: cost reval・cost/__init__ 最終形(再評価頻度ガード)

**Files:**
- Create: `backend/src/latch/worker/cost/reeval.py`
- Modify: `backend/src/latch/worker/cost/__init__.py`(Task 2のdocstringのみの状態から公開APIの最終形へ)
- Test: `backend/tests/unit/cost/test_cost_reeval.py`

**Interfaces:**
- Consumes: `JevCostStore.set_reeval_nx`(Task 2)・`JevCostDependencyError`(Task 2)
- Produces(Task 8・integration試験・ws-6が使用):
  - `ReevalGuard(redis, *, key_prefix: str = "")`:
    - `async allow(intent_id: uuid.UUID) -> bool`(True=評価してよい(初回or30分経過)・False=30分以内の再評価。Redis例外はJevCostDependencyErrorでfail-closed)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/cost/test_cost_reeval.py` を作成:

```python
"""ReevalGuard: 初回allow・窓内deny・TTL・キー分離・fail-closed(design §4.1)。

Redis側TTLは実時間で減るため、TTL切れの再許可はキーDELETEで「切れた状態」を
再現して検証する(§9-7。FakeClockを進めても実Redis/fakeredisのTTLは変化しない)。
"""

import uuid

import fakeredis.aioredis
import pytest

from latch.worker.cost.errors import JevCostDependencyError
from latch.worker.cost.reeval import ReevalGuard

IID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
OTHER = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


async def test_first_call_allows_second_denies(redis):
    guard = ReevalGuard(redis)
    assert await guard.allow(IID) is True
    assert await guard.allow(IID) is False  # 30分以内(06 §5(c))


async def test_key_ttl_is_1800(redis):
    guard = ReevalGuard(redis)
    await guard.allow(IID)
    assert 0 < await redis.ttl(f"reeval:{IID}") <= 1800


async def test_allows_again_after_key_expiry_equivalent(redis):
    """TTL切れ相当(キー消失)で再びallow(30分経過後の次のEventで再評価)。"""
    guard = ReevalGuard(redis)
    assert await guard.allow(IID) is True
    assert await guard.allow(IID) is False
    await redis.delete(f"reeval:{IID}")  # TTL切れの相当(§9-7)
    assert await guard.allow(IID) is True


async def test_distinct_intents_are_independent(redis):
    guard = ReevalGuard(redis)
    await guard.allow(IID)
    assert await guard.allow(OTHER) is True  # Intent単位の鍵


async def test_key_prefix_isolation(redis):
    guard = ReevalGuard(redis, key_prefix="it-")
    await guard.allow(IID)
    assert await redis.exists(f"it-reeval:{IID}") == 1
    assert await redis.exists(f"reeval:{IID}") == 0


async def test_redis_exception_fail_closed(redis, monkeypatch):
    async def broken(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis, "set", broken)
    guard = ReevalGuard(redis)
    with pytest.raises(JevCostDependencyError):
        await guard.allow(IID)
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/cost/test_cost_reeval.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.cost.reeval'`)

- [ ] **Step 3: reval.py を実装して `__init__.py` を最終形へ更新する**

`backend/src/latch/worker/cost/reeval.py`:

```python
"""再評価頻度ガード(06 §5・design §2.6)。

同一Intentの更新による再評価は直近の詳細評価(パイプライン投入)から30分
空ける(06 §5 FR-03(c))。キー reeval:{intent_id}(06 §5のキー名そのまま)の
SET NX EX 1800 で判定する。Redis例外はfail-closed(design §2.4-7 —
フックが例外を出せばstage1の再試行5回→quarantinedの既存経路に載る)。
Bucket再評価・catch-upスキャン(ws-6)も同一部品を再利用する(design §5-7)。
"""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis

from latch.worker.cost.errors import JevCostDependencyError
from latch.worker.cost.store import JevCostStore


class ReevalGuard:
    """「直近の詳細評価から30分」の統一判定(起点を問わず — design §2.6)。"""

    def __init__(
        self, redis: aioredis.Redis, *, key_prefix: str = ""
    ) -> None:
        self._store = JevCostStore(redis, key_prefix=key_prefix)

    async def allow(self, intent_id: uuid.UUID) -> bool:
        """True=評価してよい(初回or30分経過)・False=30分以内の再評価。"""
        try:
            return await self._store.set_reeval_nx(str(intent_id))
        except Exception as exc:
            raise JevCostDependencyError("jev cost store unavailable") from exc
```

`backend/src/latch/worker/cost/__init__.py` を差し替え:

```python
"""Jevコスト保護(D-16・06 §5)と再評価頻度ガード(M2 ws-4・design §2.4)。

Guard呼び出し(Layer 4実行直前)と record_execution(実際のAPI呼び出し後)は
ws-5。本単位のパイプライン組み込みは ReevalGuard のみ(design §2.6)。
"""

from latch.worker.cost.errors import JevCostDependencyError
from latch.worker.cost.guard import (
    DAILY_ALERT,
    DAILY_LIMIT,
    DENY_GLOBAL_DAILY,
    DENY_GLOBAL_MONTHLY,
    DENY_INTENT_DAILY,
    DENY_USER_DAILY,
    INTENT_DAILY_LIMIT,
    MONTHLY_ALERT,
    MONTHLY_LIMIT,
    USER_DAILY_LIMIT,
    JevCostGuard,
    JevDecision,
    structured_log_alert,
)
from latch.worker.cost.reeval import ReevalGuard
from latch.worker.cost.store import JevCostStore

__all__ = [
    "DAILY_ALERT",
    "DAILY_LIMIT",
    "DENY_GLOBAL_DAILY",
    "DENY_GLOBAL_MONTHLY",
    "DENY_INTENT_DAILY",
    "DENY_USER_DAILY",
    "INTENT_DAILY_LIMIT",
    "MONTHLY_ALERT",
    "MONTHLY_LIMIT",
    "USER_DAILY_LIMIT",
    "JevCostDependencyError",
    "JevCostGuard",
    "JevCostStore",
    "JevDecision",
    "ReevalGuard",
    "structured_log_alert",
]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/cost/ -v`
Expected: PASS(test_cost_store 8件+test_cost_guard 9件+test_cost_reeval 6件)

- [ ] **Step 5: 全体確認してCommit**

Run: `make lint && make test`
Expected: ともにexit 0

```bash
git add backend/src/latch/worker/cost/reeval.py \
  backend/src/latch/worker/cost/__init__.py \
  backend/tests/unit/cost/test_cost_reeval.py
git status --short
git commit -m "feat: ReevalGuard(30分頻度・SET NX EX 1800・fail-closed)とcost公開API"
```

### Task 5: layer2・origin・candidates 拡張(Layer 3計算に必要なデータと記録列)

**Files:**
- Modify: `backend/src/latch/worker/matching/layer2.py`(`_SELECT_TOPK` 列追加・`RetrievedCandidate` 拡張)
- Modify: `backend/src/latch/worker/matching/origin.py`(`_SELECT_ORIGIN` 列追加・`Origin.soft_texts`)
- Modify: `backend/src/latch/worker/matching/candidates.py`(UPSERTへcheap_judge_score)
- Test: `backend/tests/unit/matching/test_layer_sql.py`(期待値の機械的追随 — supervisor承認)
- Test: `backend/tests/unit/matching/test_origin.py`(soft_texts抽出の追記)

**Interfaces:**
- Consumes: `layer3.soft_texts`(Task 1)
- Produces(Task 6・integration試験・ws-5が使用):
  - `RetrievedCandidate`(frozen dataclass)の最終形: `intent_id: uuid.UUID` / `version: int` / `similarity: float` / `time_start: datetime` / `budget_max: int | None` / `soft_texts: tuple[str, ...]`
  - `Origin`(frozen dataclass)へ `soft_texts: tuple[str, ...]` 追加(降格除外済み)
  - `upsert_candidate(conn, *, origin, candidate_id, candidate_version, similarity, cheap_score) -> CandidatePair`(cheap_score引数追加。CandidatePairはretrieval_scoreのみのまま — topkcが別にあるため)

- [ ] **Step 1: 失敗するテストを書く(両ファイルへ追記・期待値更新)**

`backend/tests/unit/matching/test_layer_sql.py` を更新:

(a) `_UPSERT_KEYS` へ `"cheap_judge_score"` を追加(compile検査の対象増):

```python
_UPSERT_KEYS = (
    "intent_a_id",
    "intent_b_id",
    "intent_a_version",
    "intent_b_version",
    "retrieval_score",
    "cheap_judge_score",
    "now",
)
```

(b) ファイル末尾へ追記:

```python
# -- Layer 3 組込みのSQLピン(M2 ws-4・design §3.2 — 機械的追随) --


def test_layer2_sql_selects_layer3_columns():
    """Layer 3計算に必要な対象側データの列追加(design §3.2)。"""
    sql = str(layer2._SELECT_TOPK)
    assert "i.time_start" in sql
    assert "i.budget_max" in sql
    assert "i.structured_data" in sql


def test_upsert_writes_cheap_judge_score():
    """INSERT列とDO UPDATE句の両方に cheap_judge_score(design §2.3全件記録)。"""
    sql = str(candidates._UPSERT)
    insert_part = sql.split("DO UPDATE", 1)[0]
    assert "cheap_judge_score" in insert_part
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "cheap_judge_score = EXCLUDED.cheap_judge_score" in update_clause
```

(c) 既存の `test_upsert_do_update_touches_score_only` を次のとおり差し替え(cheap_judge_scoreを書くようになったため。**statusのみ壊さない表明へ更新**):

```python
def test_upsert_do_update_touches_scores_only():
    """DO UPDATE SET は retrieval_score/cheap_judge_score/updated_at のみ
    (statusを壊さない — design §2.3。evaluated/skipped/closedへの遷移は
    ws-5/stage1の担当)。"""
    sql = str(candidates._UPSERT)
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "status" not in update_clause
    assert "cheap_judge_score = EXCLUDED.cheap_judge_score" in update_clause
    # 新規行は status='pending'(05 §2・Layer 1〜3時点でJev未評価)
    insert_part = sql.split("DO UPDATE", 1)[0]
    assert "'pending'" in insert_part
```

`backend/tests/unit/matching/test_origin.py` を更新:

(a) import節へ `import json` を追加。

(b) `_row()` の base dict へ `structured_data=None,` を追加(time_end=None の近く)。

(c) ファイル末尾へ追記:

```python
# -- soft_texts抽出(M2 ws-4・design §3.2) --


async def test_origin_soft_texts_extraction():
    """structured_data(str返り含む)からsoft_texts抽出・降格除外。"""
    sd = json.dumps(
        {
            "soft_constraints": [
                {"text": "焼肉", "downgraded_from_ng": False},
                {"text": "個室", "downgraded_from_ng": True},
                {"text": "静か"},
            ]
        }
    )
    loaded = await _load(_row(structured_data=sd))
    assert loaded.origin is not None
    assert loaded.origin.soft_texts == ("焼肉", "静か")


async def test_origin_soft_texts_empty_variants():
    loaded = await _load(_row(structured_data=None))
    assert loaded.origin is not None and loaded.origin.soft_texts == ()
    loaded2 = await _load(_row(structured_data='{"soft_constraints": []}'))
    assert loaded2.origin is not None and loaded2.origin.soft_texts == ()
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer_sql.py tests/unit/matching/test_origin.py -v`
Expected: FAIL(新規追加分が `AttributeError: 'Origin' object has no attribute 'soft_texts'` 等・UPSERT列ピンがred)

- [ ] **Step 3: layer2.py・origin.py・candidates.py を実装する**

`backend/src/latch/worker/matching/layer2.py` — 差し替え(変更点: `_SELECT_TOPK` の3列追加・`RetrievedCandidate` の3フィールド追加・import追加・構築部):

```python
"""Layer 2 Candidate Retrieval(06 §3・design §2.1案A・§2.2)。

Layer 1 条件込みの単一SQLで cosine 距離上位 K_v=50 を取得する。ORDER BY
の第2キー i.id(同点はintent_id昇順 — D-24・10 §4.6)を付けたためHNSW
Indexスキャンは使われず逐次ソートになる(決定性優先 — design §2.2。
負荷試験(10 §4.2)でp95 1秒を割った場合のみiterative scanで再設計)。
retrieval_score には cosine類似度(1 − 距離)を記録する(design §2.6)。
起点embeddingは文字列表現をそのまま再キャストして渡す(asyncpgがvector
列を文字列で返すため — design §2.6・§5-2)。M2 ws-4: Layer 3 計算に必要な
対象側データ(time_start・budget_max・structured_data)を同じSELECTで返す
(design §3.2 — 往復増は不利のため分割しない)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.worker.matching.layer1 import LAYER1_WHERE
from latch.worker.matching.layer3 import soft_texts
from latch.worker.matching.origin import Origin, bind_params

# D-24(06 §8・01 §11): Vector検索のK上限=次層へ渡す出力数の上限
K_VECTORS = 50

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


@dataclass(frozen=True)
class RetrievedCandidate:
    """Layer 2 通過対象(cosine類似度+Layer 3計算に必要な対象側データ)。"""

    intent_id: uuid.UUID
    version: int
    similarity: float
    time_start: datetime
    budget_max: int | None
    soft_texts: tuple[str, ...]


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
    structured_data は jsonb(asyncpgはstrで返りうる — design §5-4)のため
    layer3.soft_texts 内で解釈する。
    """
    params = {**bind_params(origin), "origin_embedding": origin.embedding}
    rows = (await conn.execute(_SELECT_TOPK, params)).all()
    candidates = [
        RetrievedCandidate(
            intent_id=_coerce_uuid(r[0]),
            version=r[1],
            similarity=float(r[2]),
            time_start=r[4],
            budget_max=r[5],
            soft_texts=soft_texts(r[6]),
        )
        for r in rows
    ]
    pass_count = int(rows[0][3]) if rows else 0
    return candidates, pass_count
```

`backend/src/latch/worker/matching/origin.py` — 変更3点(他は不変):

(a) docstring末尾へ1行追記: 「M2 ws-4: structured_data を読み Layer 3 語彙計算用の soft_texts(降格除外済み)を Origin へ持たせる(design §3.2)。」

(b) import節へ追加:

```python
from latch.worker.matching.layer3 import soft_texts
```

(c) `_SELECT_ORIGIN` の SELECT 列へ `i.structured_data,` を追加(embedding の後・ST_X の前):

```python
_SELECT_ORIGIN = text("""
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
```

(d) `Origin` dataclass へ `soft_texts: tuple[str, ...]` を追加(user_ge_20 の前・コメント付き):

```python
    soft_texts: tuple[str, ...]  # 降格除外済みのsoft_constraints文言(06 §4語彙計算用)
```

(e) `load_origin` の `origin = Origin(...)` 構築へ追加(user_ge_20 の前):

```python
        soft_texts=soft_texts(row.structured_data),
```

`backend/src/latch/worker/matching/candidates.py` — 変更2点(他は不変):

(a) docstringの「DO UPDATE は retrieval_score・updated_at のみ」を「DO UPDATE は retrieval_score・cheap_judge_score・updated_at のみ(M2 ws-4でcheap_judge_score追加)」へ更新。

(b) `_UPSERT` を差し替え:

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

(c) `upsert_candidate` を差し替え(cheap_score 引数追加・params へ設定):

```python
async def upsert_candidate(
    conn: AsyncConnection,
    *,
    origin: Origin,
    candidate_id: uuid.UUID,
    candidate_version: int,
    similarity: float,
    cheap_score: float,
) -> CandidatePair:
    """1ペアのUPSERT(design §2.3)。

    retrieval_score には cosine類似度・cheap_judge_score には Layer 3 スコア
    (Layer 2通過全件を記録 — design §2.3)。丸めない(design §2.2)。
    """
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
            "cheap_judge_score": cheap_score,
            "now": origin.evaluated_at,
        },
    )
    return CandidatePair(
        intent_a_id=a_id, intent_b_id=b_id, retrieval_score=similarity
    )
```

- [ ] **Step 4: テストが通ることを確認する(この時点では test_matching_runner.py はredのまま=次Taskで追随)**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer_sql.py tests/unit/matching/test_origin.py -v`
Expected: PASS(test_layer_sql 12件+test_origin 15件)。ただし `tests/unit/matching/test_matching_runner.py` は `Origin` の必須フィールド追加により**この時点でFAILする(機械的追随はTask 6)**。`make test` を通すのはTask 6完了後。コミットはTask単位で行うが、`make test` がredのままコミットできないため、**Task 5とTask 6を続けて実行し、Task 6のStep 4で両方のコミット前検査を行う**。したがってTask 5のコミットはTask 6の実装後にまとめて行う(次Step参照)

- [ ] **Step 5: (コミットはTask 6 Step 5でまとめて実行 — Origin拡張とrunner組込みが一体の機械的追随のため)**

### Task 6: runner Layer 3組込み・matching/__init__ 最終形

**Files:**
- Modify: `backend/src/latch/worker/matching/runner.py`
- Modify: `backend/src/latch/worker/matching/__init__.py`
- Test: `backend/tests/unit/matching/test_matching_runner.py`(機械的追随+Layer 3追加検証)

**Interfaces:**
- Consumes: `layer3.rule_score`・`layer3.vocab_overlap`・`layer3.cheap_score`・`layer3.select_top_kc`・`layer3.ScoredCandidate`(Task 1)・Task 5の拡張インターフェース
- Produces(integration試験・ws-5が使用):
  - `RetrievalOutcome`(dataclass)の最終形: `intent_id: uuid.UUID` / `version: int | None` / `layer1_pass_count: int` / `pairs: list[CandidatePair]` / `topkc: list[ScoredCandidate]`(cheap_score降順・同点intent_id昇順の上位K_c=20)/ `skip_reason: str | None`
  - `run_candidate_retrieval(conn, clock, intent_id) -> RetrievalOutcome`(外部契約不変・Layer 3を内部に組む)

- [ ] **Step 1: 失敗するテストを書く(test_matching_runner.py の更新)**

`backend/tests/unit/matching/test_matching_runner.py` を更新:

(a) import節へ `import pytest` を追加。

(b) `_origin()` へ `soft_texts=("焼肉",),` を追加(user_ge_20=True の前。起点: budget_max=None のまま):

```python
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
        soft_texts=("焼肉",),
        user_ge_20=True,
        evaluated_at=NOW,
    )
```

(c) `test_normal_path_records_pairs` を差し替え(fake_topk の候補をキーワード構築へ・fake_upsert へ cheap_score 受け・手計算照合):

```python
async def test_normal_path_records_pairs(monkeypatch):
    """検索結果の各対象を記録へ回しOutcomeへ載せる(pass_count含む)。"""
    org = _origin()

    async def fake_load(conn, clock, iid):
        return OriginLoad(origin=org, skip_reason=None)

    target = RetrievedCandidate(
        intent_id=TARGET,
        version=2,
        similarity=0.98,
        time_start=NOW,  # Δ0→時間近さ1.0
        budget_max=None,  # 片方NULL→予算近さ0.5
        soft_texts=("焼肉",),  # 同一文言→語彙1.0
    )

    async def fake_topk(conn, o):
        assert o is org
        return ([target], 7)

    upserted = {}

    async def fake_upsert(
        conn, *, origin, candidate_id, candidate_version, similarity, cheap_score
    ):
        upserted["args"] = (candidate_id, candidate_version, similarity, cheap_score)
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
    # Layer 3: 0.5*0.98 + 0.3*((1.0+0.5)/2) + 0.2*1.0 = 0.815(手計算)
    expected = 0.5 * 0.98 + 0.3 * 0.75 + 0.2 * 1.0
    assert upserted["args"][:3] == (TARGET, 2, 0.98)
    assert upserted["args"][3] == pytest.approx(expected)
    assert len(outcome.topkc) == 1
    assert outcome.topkc[0].intent_id == TARGET
    assert outcome.topkc[0].cheap_score == pytest.approx(expected)
```

(d) ファイル末尾へ追記(Layer 3パイプラインの追加検証):

```python
# -- Layer 3 組込みの追加検証(M2 ws-4・design §2.3) --


async def test_layer3_pipeline_orders_and_scores(monkeypatch):
    """全候補へcheap_score計算→全件UPSERT→topkc選定(design §2.3)。"""
    org = _origin()  # time_start=NOW・budget_max=None・soft_texts=("焼肉",)

    async def fake_load(conn, clock, iid):
        return OriginLoad(origin=org, skip_reason=None)

    c1 = RetrievedCandidate(
        intent_id=uuid.UUID("00000000-0000-4000-8000-0000000000c1"),
        version=1,
        similarity=1.0,
        time_start=NOW,  # 時間近さ1.0
        budget_max=None,  # 予算近さ0.5(片方NULL)
        soft_texts=("焼肉好き",),  # 語彙1/3(design §4.1の手計算例)
    )
    c2 = RetrievedCandidate(
        intent_id=uuid.UUID("00000000-0000-4000-8000-0000000000c2"),
        version=1,
        similarity=0.5,
        time_start=NOW + timedelta(minutes=180),  # 時間近さ0.0(飽和)
        budget_max=None,  # 予算近さ0.5
        soft_texts=(),  # 片方空→語彙0.0
    )

    async def fake_topk(conn, o):
        return ([c1, c2], 2)

    upserts: list[tuple[uuid.UUID, float]] = []

    async def fake_upsert(
        conn, *, origin, candidate_id, candidate_version, similarity, cheap_score
    ):
        upserts.append((candidate_id, cheap_score))
        a, b = sorted((origin.intent_id, candidate_id))
        return CandidatePair(a, b, similarity)

    monkeypatch.setattr(runner.origin, "load_origin", fake_load)
    monkeypatch.setattr(runner.layer2, "retrieve_topk", fake_topk)
    monkeypatch.setattr(runner.candidates, "upsert_candidate", fake_upsert)

    outcome = await runner.run_candidate_retrieval(None, FakeClock(NOW), IID)
    # 全件(Layer 2通過2件とも)UPSERTへ回る(K_c内外の全候補が記録に残る)
    assert [u[0] for u in upserts] == [c1.intent_id, c2.intent_id]
    expected_c1 = 0.5 * 1.0 + 0.3 * 0.75 + 0.2 * (1 / 3)
    expected_c2 = 0.5 * 0.5 + 0.3 * 0.25 + 0.2 * 0.0
    assert upserts[0][1] == pytest.approx(expected_c1)
    assert upserts[1][1] == pytest.approx(expected_c2)
    # topkcはcheap_score降順
    assert [s.intent_id for s in outcome.topkc] == [c1.intent_id, c2.intent_id]
    assert outcome.topkc[0].cheap_score == pytest.approx(expected_c1)
    assert outcome.topkc[0].similarity == 1.0
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/test_matching_runner.py -v`
Expected: FAIL(`TypeError: upsert_candidate() got an unexpected keyword argument 'cheap_score'` またはtopkc属性なし)

- [ ] **Step 3: runner.py を実装して `__init__.py` を最終形へ更新する**

`backend/src/latch/worker/matching/runner.py` を差し替え:

```python
"""embedding_completed 起点の第2段前半のオーケストレータ(design §2.4・§2.1案A)。

起点検証→Layer 2検索→Layer 3計算→match_candidates記録。Layer 3 は Layer 2 と
同一トランザクション・同一実行経路で組む(design §2.1案A): Layer 2 通過全件
(≤K_v=50)へ cheap_score を計算し全件の cheap_judge_score をUPSERTし、上位
K_c=20(降順・同点intent_id昇順)を topkc として次層(Layer 4・ws-5)へ返す。
トランザクションは呼び出し側(engine.begin() で包む — design §2.3)。例外は
握らず呼び出し側(stage1の再試行ループに載る経路)へ伝播させる。モジュール
属性経由で origin/layer2/candidates を呼ぶ(unit試験がmonkeypatchで差し替え
可能)。layer3 は純関数のため実物を呼ぶ(決定的)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncConnection

from latch.core.clock import Clock
from latch.worker.matching import candidates, layer2, layer3, origin
from latch.worker.matching.candidates import CandidatePair
from latch.worker.matching.layer3 import ScoredCandidate


@dataclass(frozen=True)
class RetrievalOutcome:
    """run_candidate_retrieval の結果(design §2.4 — 試験assertと
    将来トレースの供給源)。topkc は Layer 4(ws-5)への出力。"""

    intent_id: uuid.UUID
    version: int | None  # no-op時はNone
    layer1_pass_count: int
    pairs: list[CandidatePair] = field(default_factory=list)
    topkc: list[ScoredCandidate] = field(default_factory=list)
    skip_reason: str | None = None


async def run_candidate_retrieval(
    conn: AsyncConnection, clock: Clock, intent_id: uuid.UUID
) -> RetrievalOutcome:
    """起点検証→Layer 2検索→Layer 3計算→UPSERT(design §2.1案A・§2.4)。"""
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
    scored: list[ScoredCandidate] = []
    for c in rows:
        rule = layer3.rule_score(
            origin_time_start=org.time_start,
            cand_time_start=c.time_start,
            origin_budget=org.budget_max,
            cand_budget=c.budget_max,
        )
        vocab = layer3.vocab_overlap(org.soft_texts, c.soft_texts)
        cheap = layer3.cheap_score(c.similarity, rule, vocab)
        scored.append(
            ScoredCandidate(
                intent_id=c.intent_id,
                version=c.version,
                similarity=c.similarity,
                cheap_score=cheap,
            )
        )
        pairs.append(
            await candidates.upsert_candidate(
                conn,
                origin=org,
                candidate_id=c.intent_id,
                candidate_version=c.version,
                similarity=c.similarity,
                cheap_score=cheap,
            )
        )
    return RetrievalOutcome(
        intent_id=org.intent_id,
        version=org.version,
        layer1_pass_count=pass_count,
        pairs=pairs,
        topkc=layer3.select_top_kc(scored),
    )
```

`backend/src/latch/worker/matching/__init__.py` を差し替え(layer3の追記+既存の再export維持):

```python
"""第2段マッチング前半: Layer 1〜3(M2 ws-3 + ws-4)。

公開APIは AsyncConnection を第一引数に取る純関数群(design §2.4)。
Layer 3 まで実配線済み(stage1 matching_hook・M2 ws-4)。K_v・K_c・500円は
module定数(design §2.8-3 — settings化しない)。
"""

from latch.worker.matching.candidates import (
    CandidatePair,
    normalize_pair,
    upsert_candidate,
)
from latch.worker.matching.layer1 import (
    LAYER1_WHERE,
    PAIR_BUDGET_MIN_YEN,
    HardCandidate,
    hard_filter_candidates,
)
from latch.worker.matching.layer2 import (
    K_VECTORS,
    RetrievedCandidate,
    retrieve_topk,
)
from latch.worker.matching.layer3 import (
    K_CHEAP,
    ScoredCandidate,
    cheap_score,
    rule_score,
    select_top_kc,
    soft_texts,
    vocab_overlap,
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
    "K_CHEAP",
    "K_VECTORS",
    "LAYER1_WHERE",
    "CandidatePair",
    "HardCandidate",
    "Origin",
    "OriginLoad",
    "RetrievedCandidate",
    "RetrievalOutcome",
    "ScoredCandidate",
    "bind_params",
    "cheap_score",
    "hard_filter_candidates",
    "load_origin",
    "normalize_pair",
    "retrieve_topk",
    "rule_score",
    "run_candidate_retrieval",
    "select_top_kc",
    "soft_texts",
    "upsert_candidate",
    "vocab_overlap",
]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/matching/ -v`
Expected: PASS(test_origin 15件+test_layer_sql 12件+test_layer3 11件+test_matching_runner 4件)

- [ ] **Step 5: 全体確認してCommit(Task 5分と合わせて2コミット)**

Run: `make lint && make test`
Expected: ともにexit 0(既存試験の機械的追随を含む。`tests/unit/matching/test_layer_sql.py` の既存試験がfallingした場合は§4列挙の期待値更新範囲か確認し、範囲外なら作業を止めて報告)

```bash
# Task 5分(layer2・origin・candidatesとテスト期待値)
git add backend/src/latch/worker/matching/layer2.py \
  backend/src/latch/worker/matching/origin.py \
  backend/src/latch/worker/matching/candidates.py \
  backend/tests/unit/matching/test_layer_sql.py \
  backend/tests/unit/matching/test_origin.py
git commit -m "feat: Layer 3計算用データのSELECT列追加とUPSERTのcheap_judge_score拡張"

# Task 6分(runner・__init__・test追随)
git add backend/src/latch/worker/matching/runner.py \
  backend/src/latch/worker/matching/__init__.py \
  backend/tests/unit/matching/test_matching_runner.py
git status --short  # 空(Task 5+6の計8ファイルをコミット済み)
git commit -m "feat: run_candidate_retrievalへLayer 3組込み(全件記録・topkc選定)"
```

### Task 7: stage1 matching フック(embedding_completed種別)

**Files:**
- Modify: `backend/src/latch/worker/stage1.py`
- Test: `backend/tests/unit/test_worker_stage1.py`(追記)

**Interfaces:**
- Consumes: なし(embedding_hook と同型のDIパターン — design §2.7)
- Produces(Task 8・integration試験が使用):
  - `Stage1.__init__` の新パラメータ: `matching_hook: Callable[[AsyncConnection, uuid.UUID], Awaitable[None]] | None = None`(conn を渡すのは `run_candidate_retrieval` の契約(AsyncConnectionを第一引数に取る純関数)への同乗を可能にするため — design §2.7)
  - `_process_once` で `event_type == "embedding_completed"` のとき `await self._matching_hook(conn, intent_id)`(Event行のprocessedマークと同一トランザクション)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_stage1.py` へ追記:

(a) `_stage1` ヘルパーへ `matching_hook=None` パラメータを追加し、`Stage1(...)` へ渡す:

```python
def _stage1(engine, clock=None, sleep=None, hook=None, matching_hook=None) -> Stage1:
    return Stage1(
        engine=engine,
        clock=clock if clock is not None else FakeClock(S1_NOW),
        settings=Settings(),
        sleep=sleep if sleep is not None else (lambda s: _await_none()),
        embedding_hook=hook,
        matching_hook=matching_hook,
    )
```

(b) ファイル末尾(`test_intake_claim_returning_uuid_instance` の後)へ追記:

```python
# -- matching フック(M2 ws-4・design §2.7) --


async def test_matching_hook_called_for_embedding_completed():
    """embedding_completedでmatchingフックがconnとintent_idで呼ばれる。"""
    calls: list[tuple[object, uuid.UUID]] = []

    async def mhook(conn, intent_id):
        calls.append((conn, intent_id))

    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    await _stage1(engine, matching_hook=mhook).process(
        _event("embedding_completed", IID, 1),
        ("embedding_completed", IID, 1),
        ROW_ID,
    )
    assert calls == [(engine.conn, IID)]  # 同一トランザクションのconn


async def test_matching_hook_absent_keeps_processed():
    """hookなしでも embedding_completed は従来どおり processed(処理実体なし)。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    result = await _stage1(engine).process(
        _event("embedding_completed", IID, 1),
        ("embedding_completed", IID, 1),
        ROW_ID,
    )
    assert result == "processed"


async def test_matching_hook_failure_blocks_processed():
    """hook例外は握られず伝播=processedにならない(design §2.7 — Stage1の
    再試行(Retryable/SQLAlchemyError)で捕まらない例外は呼び出し側へ)。"""

    async def mhook(conn, intent_id):
        raise RuntimeError("matching failed")

    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
        ]
    )
    with pytest.raises(RuntimeError):
        await _stage1(engine, matching_hook=mhook).process(
            _event("embedding_completed", IID, 1),
            ("embedding_completed", IID, 1),
            ROW_ID,
        )
    # processed遷移SQL(_MARK_PROCESSED)には到達していない
    assert all(
        "processed" not in sql or "quarantined" in sql
        for sql, _ in engine.conn.calls
    ) or not any(
        "SET status = 'processed'" in sql for sql, _ in engine.conn.calls
    )


async def test_matching_hook_not_called_for_other_types():
    """created/updatedではmatchingフックは呼ばれない(Embeddingキックは別)。"""
    calls: list[uuid.UUID] = []

    async def mhook(conn, intent_id):
        calls.append(intent_id)

    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    await _stage1(engine, matching_hook=mhook).process(
        _event("created", IID, 1), ("created", IID, 1), ROW_ID
    )
    assert calls == []
```

※ `pytest` のimportはファイル冒頭に既にあることを確認(なければ追加)。

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_stage1.py -v -k matching_hook`
Expected: FAIL(`TypeError: _stage1() got an unexpected keyword argument 'matching_hook'`)

- [ ] **Step 3: stage1.py を実装する**

`backend/src/latch/worker/stage1.py` — 変更3点(他は不変):

(a) モジュールdocstringの「削除済みIntentへの参照Eventは正当な」の前にある行「行確保(ON CONFLICT DO NOTHING→SELECT FOR UPDATE)・version検査」は不変のまま、docstring末尾へ1行追記:

```
M2 ws-4: embedding_completed 種別で matching_hook(Layer 1〜3)を同一トランザクションで呼ぶ。
```

(b) `Stage1.__init__` へ `matching_hook` パラメータと代入を追加(embedding_hook の直後):

```python
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        embedding_hook: Callable[[str, uuid.UUID, int], Awaitable[None]] | None = None,
        matching_hook: Callable[
            [AsyncConnection, uuid.UUID], Awaitable[None]
        ] | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._retry_max = settings.event_retry_max
        self._sleep = sleep
        self._embedding_hook = embedding_hook  # ws-2が実体を置く(design §2.5)
        self._matching_hook = matching_hook  # ws-4が実体を置く(design §2.7)
```

(c) `_process_once` の種別処理を差し替え:

```python
            # version == intents.version → 種別処理(§2.5)
            if event_type == EVENT_DELETED:
                await conn.execute(
                    _CLOSE_CANDIDATES, {"intent_id": intent_id, "now": now}
                )
            elif event_type in (EVENT_CREATED, EVENT_UPDATED):
                if self._embedding_hook is not None:
                    await self._embedding_hook(event_type, intent_id, version)
            elif event_type == _EVENT_EMBEDDING_COMPLETED:
                # Layer 1〜3をこのトランザクションへ同乗(design §2.7)。
                # 失敗→ロールバック→再試行5回→quarantinedの既存経路に載る
                if self._matching_hook is not None:
                    await self._matching_hook(conn, intent_id)
            # expired / scheduled は処理実体なし(processed)
            await conn.execute(
                _MARK_PROCESSED, {"row_id": row_id, "now": now, "extra": "{}"}
            )
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_stage1.py -v`
Expected: PASS(既存全件+追記4件)

- [ ] **Step 5: 全体確認してCommit**

Run: `make lint && make test`
Expected: ともにexit 0

```bash
git add backend/src/latch/worker/stage1.py \
  backend/tests/unit/test_worker_stage1.py
git status --short
git commit -m "feat: stage1のembedding_completed種別へmatchingフックDIを追加"
```

### Task 8: worker/main.py のDI(redis・ReevalGuard・_run_matching)

**Files:**
- Modify: `backend/src/latch/worker/main.py`
- Test: `backend/tests/unit/test_worker.py`(追記)

**Interfaces:**
- Consumes: `ReevalGuard`(Task 4)・`run_candidate_retrieval`(Task 6)・`settings.redis_url`(既存・auth/ratelimit が使用中の値)
- Produces(integration試験・本番workerが使用):
  - `Worker.__init__` の新パラメータ: `reeval: ReevalGuard | None = None`
  - `async Worker._run_matching(conn: AsyncConnection, intent_id: uuid.UUID) -> None`(reevalガード→`run_candidate_retrieval(conn, self.clock, intent_id)`)。ガード拒否時は構造化ログ(`latch.worker.main` logger)でスキップ理由を記録してreturn
  - `run()` 内: 未注入時は `redis.asyncio.Redis.from_url(settings.redis_url, decode_responses=True)` を構築し `ReevalGuard` を作る(owns_redis フラグで finally aclose)。未注入時に構築する `Stage1` へ `matching_hook=self._run_matching` を渡す(注入済みstage1は触らない — 既存DIの流儀)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker.py` へ追記(ファイル末尾。`uuid` は冒頭import済み):

```python
# -- _run_matching DI配線(M2 ws-4・design §2.6・§2.7)。ReevalGuardスタブで検証 --

IID = uuid.UUID("00000000-0000-4000-8000-0000000000aa")


class _FakeReeval:
    """ReevalGuardスタブ(allowの呼び出しを記録)。"""

    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[uuid.UUID] = []

    async def allow(self, intent_id):
        self.calls.append(intent_id)
        return self.allowed


async def test_run_matching_runs_retrieval_when_allowed(fake_clock, monkeypatch):
    """ガード許可時: run_candidate_retrieval を conn・clock・intent_id で呼ぶ。"""
    called: list[tuple[object, object, uuid.UUID]] = []

    async def fake_retrieval(conn, clock, intent_id):
        called.append((conn, clock, intent_id))

    monkeypatch.setattr(
        "latch.worker.main.run_candidate_retrieval", fake_retrieval
    )
    guard = _FakeReeval(True)
    worker = Worker(clock=fake_clock, bus=_FakeBus(), reeval=guard)
    await worker._run_matching(None, IID)
    assert called == [(None, fake_clock, IID)]
    assert guard.calls == [IID]


async def test_run_matching_suppressed_when_reeval_denies(fake_clock, monkeypatch):
    """ガード拒否時: run_candidate_retrieval は呼ばれない(design §2.6)。"""
    async def fake_retrieval(conn, clock, intent_id):
        raise AssertionError("ガード拒否では呼ばれない")

    monkeypatch.setattr(
        "latch.worker.main.run_candidate_retrieval", fake_retrieval
    )
    guard = _FakeReeval(False)
    worker = Worker(clock=fake_clock, bus=_FakeBus(), reeval=guard)
    await worker._run_matching(None, IID)  # 例外なくreturn(スキップ)
    assert guard.calls == [IID]
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker.py -v -k run_matching`
Expected: FAIL(`TypeError: Worker.__init__() got an unexpected keyword argument 'reeval'`)

- [ ] **Step 3: worker/main.py を実装する**

`backend/src/latch/worker/main.py` — 変更5点(他は不変):

(a) import節へ追加(logging も追加 — ファイルにまだ無い場合):

```python
import logging

import redis.asyncio as redis_async

from latch.worker.cost import ReevalGuard
from latch.worker.matching import run_candidate_retrieval
```

(b) `logger = logging.getLogger(__name__)` を `DEBOUNCE_TICK_SEC` の前に追加(既にあれば不要)。

(c) `Worker.__init__` へ `reeval: ReevalGuard | None = None` を追加し `self._reeval = reeval`(`self._backfill = backfill` の後):

```python
    def __init__(
        self,
        clock: Clock | None = None,
        settings: Settings | None = None,
        *,
        bus: EventBus | None = None,
        engine=None,
        stage1: Stage1 | None = None,
        debouncer: TrailingDebouncer | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        embedding: EmbeddingWorker | None = None,
        backfill: BackfillRunner | None = None,
        reeval: ReevalGuard | None = None,
    ) -> None:
        self.clock: Clock = clock if clock is not None else SystemClock()
        self.settings: Settings = settings if settings is not None else Settings()
        self._bus = bus
        self._engine = engine
        self._stage1 = stage1
        self._debouncer = debouncer
        self._sleep = sleep
        self._embedding = embedding
        self._backfill = backfill
        self._reeval = reeval
        self._stop = asyncio.Event()
        self._subscription = None
```

(d) `run()` を差し替え(redis構築・Stage1へのmatching_hook・finallyのaclose):

```python
    async def run(self) -> None:
        owns_bus = self._bus is None
        owns_engine = self._engine is None
        owns_redis = self._reeval is None
        if owns_bus:
            self._bus = make_event_bus(self.settings)
        bus = self._bus
        engine = (
            self._engine
            if self._engine is not None
            else create_db_engine(self.settings)
        )
        redis_client = (
            redis_async.Redis.from_url(
                self.settings.redis_url, decode_responses=True
            )
            if owns_redis
            else None
        )
        if self._reeval is None and redis_client is not None:
            self._reeval = ReevalGuard(redis_client)
        try:
            stage1 = (
                self._stage1
                if self._stage1 is not None
                else Stage1(
                    engine=engine,
                    clock=self.clock,
                    settings=self.settings,
                    sleep=self._sleep,
                    matching_hook=self._run_matching,
                )
            )
            debouncer = (
                self._debouncer
                if self._debouncer is not None
                else TrailingDebouncer(
                    clock=self.clock,
                    window_sec=self.settings.event_debounce_window_sec,
                    tick_sec=DEBOUNCE_TICK_SEC,
                    on_release=self._on_release,
                )
            )
            self._stage1 = stage1
            self._debouncer = debouncer
            embedding = (
                self._embedding
                if self._embedding is not None
                else EmbeddingWorker(
                    engine=engine,
                    clock=self.clock,
                    gateway=build_embedding_gateway(self.clock, self.settings),
                    bus=bus,
                )
            )
            backfill = (
                self._backfill
                if self._backfill is not None
                else BackfillRunner(
                    engine=engine,
                    embedding=embedding,
                    interval_sec=self.settings.embedding_backfill_interval_sec,
                    batch_limit=self.settings.embedding_backfill_batch_limit,
                )
            )
            self._embedding = embedding
            self._backfill = backfill
            await bus.ensure()
            self._subscription = await bus.subscribe(self._dispatch)
            debouncer_task = asyncio.create_task(debouncer.run(stop=self._stop))
            backfill_task = asyncio.create_task(backfill.run(stop=self._stop))
            logger.info("worker started (app_env=%s)", self.settings.app_env)
            await self._stop.wait()
            # graceful shutdown: 窓内entryは解放せず未ack再配信へ(design §2.3)
            if self._subscription is not None:
                self._subscription.stop()
            await debouncer_task
            await backfill_task
            logger.info("worker stopped")
        finally:
            if owns_engine and engine is not None:
                await engine.dispose()
            if owns_redis and redis_client is not None:
                await redis_client.aclose()
            if owns_bus:
                await bus.close()
```

(e) `_kick_embedding` の後に `_run_matching` を追加:

```python
    async def _run_matching(self, conn, intent_id: uuid.UUID) -> None:
        """embedding_completed 起点の Layer 1〜3 実行(design §2.6・§2.7)。

        reevalガードで30分以内の再評価をスキップする(06 §5(c)「Event自体は
        処理済みとし、再評価は行わない」 — スキップ理由は構造化ログ)。
        run_candidate_retrieval は stage1 のトランザクションに同乗する
        (失敗→ロールバック→再試行5回→quarantinedの既存経路)。reevalの
        Redis呼び出しがDBトランザクション内に入るが、1回のSET NX(低レイテン
        シ・compose内ネットワーク)で接続の長期保持を生まないため許容
        (design §2.7)。Redis例外はfail-closed(ReevalGuardが専用例外へ包む)。
        """
        if self._reeval is not None and not await self._reeval.allow(intent_id):
            logger.info(
                "matching reeval suppressed intent_id=%s (within 30min window)",
                intent_id,
            )
            return
        await run_candidate_retrieval(conn, self.clock, intent_id)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker.py tests/unit/test_worker_stage1.py -v`
Expected: PASS(既存全件+追記2件)

- [ ] **Step 5: 全体確認してCommit**

Run: `make lint && make test`
Expected: ともにexit 0

```bash
git add backend/src/latch/worker/main.py \
  backend/tests/unit/test_worker.py
git status --short
git commit -m "feat: workerへreevalガードとmatchingフック実体をDI(redis構築)"
```

### Task 9: integration test_matching_cheapjudge.py(作成のみ・実行はスーパーバイザー)

**Files:**
- Create: `backend/tests/integration/test_matching_cheapjudge.py`

**Interfaces:**
- Consumes: compose常設api(127.0.0.1:8000)・`db_engine`・`redis_client` fixture(tests/integration/conftest.py・参照のみ)・`run_candidate_retrieval`(Task 6)・`ReevalGuard`(Task 4)・`Stage1`(Task 7)・`IncomingEvent`(latch.events)・FakeClock/SystemClock・fakeredis
- Produces: design §4.2の5試験(実DB・実Redis)。**実行はスーパーバイザー検証時の `make test-ci` のみ**(§0)。Workerプロセスは起動しない(直接呼び出し・ws-3と同じ流儀)

**design §4.2の対抗策(DB・Redis残存)の組み込み**: カテゴリ分離(`ws4cheap`)・teardown完全性(FK順・try/finally構造のfixture)・件数assertの限定(自己完結配置のみ)・Redisキー分離(key_prefix=uuid+teardown掃除)。

- [ ] **Step 1: 試験ファイルを作成する**

`backend/tests/integration/test_matching_cheapjudge.py`:

```python
"""Layer 3 Cheap Judge・reevalガード・配線のintegration試験(M2 ws-4 design §4.2)。

実DB(compose常設・pgvector)・実Redis(compose常設)。Workerプロセスは起動
しない(直接関数呼び出し・FakeClock注入・Stage1直構築 — ws-3流儀)。
対抗策(design §4.2): category_primary=ws4cheap で他試験由来の残存Intentと
構造的に交差しない(Layer 1完全一致条件)。teardownはsubjectプレフィックス
単位のFK順削除(match_candidates→match_events→intents→users)+Redis prefix掃除。
soft_constraints/ng_unverifiable はAPI入力から投入し、mapping.pyの
{text, downgraded_from_ng} 形式で保存されたものをLayer 3が読む通しを検証。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.events import IncomingEvent
from latch.settings import Settings
from latch.worker.cost.reeval import ReevalGuard
from latch.worker.matching import run_candidate_retrieval
from latch.worker.stage1 import Stage1

pytestmark = pytest.mark.integration

# テスト専用カテゴリ(design §4.2対抗策1・試験ファイル内定数)
CATEGORY = "ws4cheap"


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)  # 全候補同一(sim=1.0固定でLayer 3要素を分離)


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


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除(対抗策2)。"""
    prefix = f"m2ws4-{uuid_mod.uuid4().hex[:8]}-"
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
            text("DELETE FROM users WHERE auth_subject LIKE :p"), p
        )


@pytest.fixture
async def redis_sweep(redis_client):
    """試験ごとのRedis接頭辞。teardownでSCAN+DELETE(対抗策4)。"""
    prefix = f"ws4-{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    cursor = 0
    keys: list[str] = []
    while True:
        cursor, batch = await redis_client.scan(
            cursor=cursor, match=f"{prefix}*", count=100
        )
        keys.extend(batch)
        if cursor == 0:
            break
    if keys:
        await redis_client.delete(*keys)


async def _user(api_client, prefix: str, birth_date: str = "1990-04-01"):
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
        json={
            "display_name": "m2ws4",
            "birth_date": birth_date,
            "profile": {},
        },
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured(
    *,
    start: str | None = None,
    budget: int | None = None,
    soft: list[str] | None = None,
    ng: list[str] | None = None,
) -> dict:
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": None},
        "alcohol_involved": False,
        "time": {"start": start or _future(3), "end": None},
        "location": {"name": "天文館"},
    }
    if budget is not None:
        d["budget"] = {"max": budget}
    if soft is not None:
        d["soft_constraints"] = soft
    if ng is not None:
        d["ng_unverifiable"] = ng
    return d


def _payload(structured: dict) -> dict:
    return {
        "raw_text": "分類用テキスト",
        "status": "active",
        "structured_intent": structured,
    }


async def _intent(api_client, db_engine, headers, structured) -> dict:
    """active Intentを作りembeddingを直接挿入(design §4.2のfixture方針)。"""
    resp = await api_client.post(
        "/v1/intents", headers=headers, json=_payload(structured)
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:vec AS vector),"
                " embedding_model = 'fixture'"
                " WHERE id = CAST(:iid AS uuid)"
            ),
            {"vec": E1, "iid": intent["id"]},
        )
    return intent


async def _run(db_engine, clock, origin_intent_id: str):
    async with db_engine.begin() as conn:
        return await run_candidate_retrieval(
            conn, clock, uuid_mod.UUID(origin_intent_id)
        )


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


def _peer_scores(outcome) -> dict[str, float]:
    """Outcomeのtopkcから(候補id → cheap_score)の対応。"""
    origin_str = str(outcome.intent_id)
    out: dict[str, float] = {}
    for s in outcome.topkc:
        out[str(s.intent_id)] = s.cheap_score
    return out


# -- §4.2 試験1〜5 --


async def test_1_cheap_judge_score_recorded(api_client, db_engine, field):
    """cheap_judge_scoreの実DB記録(design §4.2-1)。

    sim=1.0(同一ベクトル)・時間Δ0・起点budget=3000×対象NULL(中立0.5)・
    語彙「焼肉」vs「焼肉好き」(1/3)→ rule=(1.0+0.5)/2=0.75。
    ng_unverifiable(降格)は語彙に入らない(Review Focus 1)。
    """
    clock = _clock()
    same_start = _future(3)  # 起点と対象で同一文字列=Δ0ピン
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=same_start, budget=3000,
                                               soft=["焼肉"], ng=["個室"])
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb,
        _structured(start=same_start, soft=["焼肉好き"], ng=["静か"]),
    )

    outcome = await _run(db_engine, clock, a["id"])
    assert outcome.skip_reason is None and len(outcome.pairs) == 1
    expected = 0.5 * 1.0 + 0.3 * 0.75 + 0.2 * (1 / 3)
    assert outcome.topkc[0].cheap_score == pytest.approx(expected, abs=1e-9)

    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT retrieval_score, cheap_judge_score, status,"
                    " intent_a_version, intent_b_version"
                    " FROM match_candidates"
                    " WHERE intent_a_id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                    " AND intent_b_id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                ),
                {"a": a["id"], "b": b["id"]},
            )
        ).first()
    assert row is not None
    assert float(row[0]) == pytest.approx(1.0)  # retrieval_score=cosine
    assert float(row[1]) == pytest.approx(expected, abs=1e-9)
    assert row[2] == "pending"  # Layer 1〜3時点(05 §2)
    assert row[3] == 1 and row[4] == 1  # バージョン組はSELECT時点


async def test_2_kc_truncation_deterministic(api_client, db_engine, field):
    """K_c=20切り詰めの決定性(design §4.2-2・10 §4.6)。

    対象25件を完全同点(同一embedding・同一start・同予算・同語彙)で配置。
    全25件にcheap_judge_score記録・topkcは20件・同点はintent_id昇順・
    同一入力2回実行で同一結果。
    """
    clock = _clock()
    same_start = _future(3)
    ho = await _user(api_client, field)
    origin = await _intent(
        api_client, db_engine, ho,
        _structured(start=same_start, soft=["静かな店"]),
    )
    creation_ids: list[str] = []
    for _ in range(25):
        h = await _user(api_client, field)
        t = await _intent(
            api_client, db_engine, h,
            _structured(start=same_start, soft=["静かな店"]),
        )
        creation_ids.append(t["id"])

    outcome = await _run(db_engine, clock, origin["id"])
    assert outcome.layer1_pass_count == 25
    assert len(outcome.pairs) == 25  # Layer 2通過全件を記録(design §2.3)
    assert len(outcome.topkc) == 20  # K_c=20(D-24)
    uuid_sorted = sorted(creation_ids)
    assert [str(s.intent_id) for s in outcome.topkc] == uuid_sorted[:20]

    # DB上も全25件にcheap_judge_scoreが入っている(全件記録)
    async with db_engine.connect() as conn:
        cnt = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM match_candidates"
                    " WHERE cheap_judge_score IS NOT NULL"
                    " AND (intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid))"
                ),
                {"o": origin["id"]},
            )
        ).scalar()
    assert cnt == 25

    # 同一入力2回実行で同一結果(10 §4.6)
    outcome2 = await _run(db_engine, clock, origin["id"])
    assert outcome2.topkc == outcome.topkc  # 順序・score含む完全一致


async def test_3_score_components_contrast(api_client, db_engine, field):
    """スコア構成の実挙動対照(design §4.2-3・§2.2の表の値を実測と照合)。

    sim=1.0固定(E1)で時間・予算・語彙だけを動かす。各対照の期待値:
      max:  時間Δ0(1.0)・予算同額(1.0)・語彙同一(1.0) → 1.0
      t_only(時間のみ遠い):  Δ180(0.0)・1.0・1.0 → 0.7
      b_only(予算のみ遠い):  1.0・差3000(0.0)・1.0 → 0.8
      v_only(語彙のみ不一致): 1.0・1.0・0.0 → 0.8
      mid:  Δ90(≈0.5)・NULL(0.5)・1/3 → ≈0.7167
      min:  Δ180(0.0)・差3000(0.0)・0.0 → 0.5
    時刻はAPI呼び出ス時刻基準のため数秒のずれが入り、Δ90系は approx で検証。
    """
    clock = _clock()
    same_start = _future(3)
    far_start = _future(6)  # Δ180分
    mid_start = _future(4.5)  # Δ90分
    ho = await _user(api_client, field)
    origin = await _intent(
        api_client, db_engine, ho,
        _structured(start=same_start, budget=3000, soft=["焼肉"]),
    )

    async def _cand(start: str, budget: int | None, soft: list[str]) -> str:
        h = await _user(api_client, field)
        it = await _intent(
            api_client, db_engine, h,
            _structured(start=start, budget=budget, soft=soft),
        )
        return it["id"]

    c_max = await _cand(same_start, 3000, ["焼肉"])
    c_t = await _cand(far_start, 3000, ["焼肉"])
    c_b = await _cand(same_start, 0, ["焼肉"])  # 差3000
    c_v = await _cand(same_start, 3000, ["寿司"])
    c_mid = await _cand(mid_start, None, ["焼肉好き"])
    c_min = await _cand(far_start, 0, ["寿司"])

    outcome = await _run(db_engine, clock, origin["id"])
    scores = _peer_scores(outcome)
    assert len(outcome.topkc) == 6
    assert scores[c_max] == pytest.approx(1.0, abs=1e-9)
    assert scores[c_t] == pytest.approx(0.7, abs=0.01)
    assert scores[c_b] == pytest.approx(0.8, abs=1e-9)
    assert scores[c_v] == pytest.approx(0.8, abs=1e-9)
    assert scores[c_mid] == pytest.approx(0.5 + 0.15 + 0.2 / 3, abs=0.01)
    assert scores[c_min] == pytest.approx(0.5, abs=0.01)
    # 大小関係(定義どおり)
    assert scores[c_max] > scores[c_mid] > scores[c_min]
    # 降順(同点はintent_id昇順 — c_bとc_vは同点0.8)
    top_ids = [str(s.intent_id) for s in outcome.topkc]
    i_b, i_v = top_ids.index(c_b), top_ids.index(c_v)
    if c_b > c_v:
        assert i_b > i_v  # UUID昇順で c_v が先
    else:
        assert i_b < i_v


async def test_4_reeval_guard_real_redis(redis_client, redis_sweep):
    """reevalガード(実Redis・design §4.2-4)。TTL切れはキー消失で再現(§9-7)。"""
    guard = ReevalGuard(redis_client, key_prefix=redis_sweep)
    iid = uuid_mod.uuid4()
    assert await guard.allow(iid) is True  # 初回
    assert await guard.allow(iid) is False  # 30分以内
    ttl = await redis_client.ttl(f"{redis_sweep}reeval:{iid}")
    assert 0 < ttl <= 1800
    await redis_client.delete(f"{redis_sweep}reeval:{iid}")  # TTL切れ相当
    assert await guard.allow(iid) is True


async def test_5_stage1_matching_hook_wiring(api_client, db_engine, field):
    """配線: embedding_completed Event→stage1→matchingフック(design §4.2-5)。

    fakeredisのReevalGuard・実DB・実runnerで match_candidates 行が増えること・
    Event行がprocessedに遷移すること。2回目(version=2)はガードにより行数不変
    のままprocessedで閉じる(06 §5(c))。
    """
    clock = _clock()
    same_start = _future(3)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=same_start, soft=["焼肉"])
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=same_start, soft=["焼肉"])
    )

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        guard = ReevalGuard(fake_redis)

        async def hook(conn, intent_id):
            if not await guard.allow(intent_id):
                return
            await run_candidate_retrieval(conn, clock, intent_id)

        stage1 = Stage1(
            engine=db_engine, clock=clock, settings=Settings(), matching_hook=hook
        )

        def _event(version: int) -> IncomingEvent:
            payload = json.dumps(
                {
                    "event_type": "embedding_completed",
                    "source_intent_id": a["id"],
                    "version": version,
                }
            ).encode()
            return IncomingEvent.from_payload(
                message_id=f"m2ws4-{version}", payload=payload, ack=lambda: None
            )

        # 1回目: 評価が走り1行生成・Event行はprocessed
        result1 = await stage1.intake(_event(1))
        assert result1.kind == "processed"
        async with db_engine.connect() as conn:
            cnt1 = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM match_candidates"
                        " WHERE intent_a_id = CAST(:i AS uuid)"
                        " OR intent_b_id = CAST(:i AS uuid)"
                    ),
                    {"i": a["id"]},
                )
            ).scalar()
            ev1 = (
                await conn.execute(
                    text(
                        "SELECT status FROM match_events"
                        " WHERE event_type = 'embedding_completed'"
                        " AND source_intent_id = CAST(:i AS uuid)"
                        " AND payload->>'version' = '1'"
                    ),
                    {"i": a["id"]},
                )
            ).first()
        assert cnt1 == 1
        assert ev1 is not None and ev1[0] == "processed"

        # 2回目: intents.versionを2へ進めて再投入 → ガードでスキップ・行数不変
        async with db_engine.begin() as conn:
            await conn.execute(
                text("UPDATE intents SET version = 2 WHERE id = CAST(:i AS uuid)"),
                {"i": a["id"]},
            )
        result2 = await stage1.intake(_event(2))
        assert result2.kind == "processed"  # Eventは処理済みで閉じる(06 §5(c))
        async with db_engine.connect() as conn:
            cnt2 = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM match_candidates"
                        " WHERE intent_a_id = CAST(:i AS uuid)"
                        " OR intent_b_id = CAST(:i AS uuid)"
                    ),
                    {"i": a["id"]},
                )
            ).scalar()
        assert cnt2 == 1  # 30分以内の再評価は行われない
    finally:
        await fake_redis.aclose()
```

**実装上の注意**:
- `_intent` の embedding 直接UPDATEはbind paramで渡す(ベクトル文字列はテスト生成とはいえ ws-3 の `_set` がf-string組立だった箇所もbind param化できる — ここでは安全側のbind param形式を採用)
- `test_3` の `c_b`/`c_v` 同点(0.8)の順序検証はUUID大小で条件分岐(作成ごとにUUIDが変わるため両方向をカバー)
- `test_5` のEvent payload `version` はjson number。`payload->>'version' = '1'`(text比較)で検索
- `IncomingEvent.from_payload` のシグネチャ(message_id・payload(bytes)・ack)は test_worker_stage1.py の `_event` ヘルパーと同じ形

- [ ] **Step 2: 収集確認する(実行はしない)**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_matching_cheapjudge.py -q`
Expected: `5 tests collected`・exit 0

- [ ] **Step 3: 全体の構文・import検証とCommit**

Run: `make lint && make test`
Expected: ともにexit 0(make test はintegrationファイルの収集(import)まで行うため構文・importが検証される)

```bash
git add backend/tests/integration/test_matching_cheapjudge.py
git status --short
git commit -m "test: Layer3・reeval・配線の5試験(作成のみ・実行はスーパーバイザー)"
```

### Task 10: 報告ファイル・完了条件検証

**Files:**
- Create: `docs/plans/M2/ws-4-report.md`

**Interfaces:**
- Consumes: 本計画§6の検証コマンド・§7の報告形式
- Produces: 報告ファイル(スーパーバイザーが検証・マージ時に使用)

- [ ] **Step 1: 完了条件§6の検証をすべて実行し結果を控える**

```bash
make lint && make test
cd backend && uv run pytest --collect-only tests/integration/test_matching_cheapjudge.py -q
cd .. && rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
cd .. && git diff --stat main -- backend/alembic docs
git diff --name-only main | sort
git status --short
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
```

§6の各条件の期待値と突き合わせる(差異があれば理由を報告書に書く)。

- [ ] **Step 2: 報告ファイルを§7の形式で作成する**

`docs/plans/M2/ws-4-report.md` を§7のテンプレートどおりに作成し、Step 1の証拠を貼る。design §5-4(jsonb列の読み取り型)の結果を必ず記載する(§5-5の回帰はスーパーバイザー検証時に追記する旨を記録)。

- [ ] **Step 3: 全体を検証してコミットする**

```bash
make lint && make test
git add docs/plans/M2/ws-4-report.md
git commit -m "docs: M2 ws-4の実行報告(完了条件7項目の証拠・test-ciは検証待ち)"
git status --short  # 空であること
git diff --name-only main | sort  # §4の24ファイルと完全一致(完了条件5)
```

- [ ] **Step 4: 最終返信**

報告ファイルのパスと完了条件7項目の結果一覧(項目2の実行部分は「test-ci=スーパーバイザー検証待ち」)を返す。

---

## 9. 固定値(IF確定事項。design.md §2 の採用判断+本計画が確定した実装詳細)

design §2 の採用判断(変更なし前提):

1. **Layer 3 は Layer 2 と同一トランザクション・同一実行経路**(design §2.1案A): `run_candidate_retrieval` 内で計算し既存UPSERTに乗せる。案B(別トランザクション)・案C(SQL一括計算)不採用
2. **§2.2の実装定義**(design §2.2の表どおり): 類似度=retrieval_scoreと同一の値を再計算せず用いる(cosine負値も正規化しない)・時間近さ=開始時刻差のみ・予算近さ=双方非NULLのみ定義・語彙=文字bigramのJaccard・NULL/空は中立0.5・丸めなし
3. **全件計算・全件記録・上位K_c=20を次層出力**(design §2.3): K_c外の行はstatus='pending'のまま(statusに触れない)
4. **INCR先行・denyも消費**(design §2.4-2): 判定は4カウンタINCR後。deny理由の優先順位は design §2.4-2 の列挙順(intent_daily→user_daily→global_daily→global_monthly)
5. **日付キー切替方式**(design §2.5): キー名にJST日付(yyyymmdd/yyyymm)を含め、TTLは掃除のみ。リセットジョブ本体(保留キュー再評価イベント発行)はM3-4(supervisor承認)
6. **reevalガードはフック実体の先頭**(design §2.6): ガード拒否時はEventをprocessedで閉じ、スキップ理由は構造化ログ(payloadへ記録しない — design §2.9-8)
7. **例外は握らず伝播**(matching/ 配下・design §2.9と同じ規律): Redis例外のみcost/内でfail-closedラップ

本計画が確定した実装詳細(design 未規定部分の実装解釈・designからの変更点。変更時は報告書へ記録):

8. **テストファイル名のリネーム(design §3.1からの変更点)**: `tests/unit/cost/test_store.py`→`test_cost_store.py`(既存 `ratelimit/test_store.py` とbasename衝突するため・運用ルール5)。`test_guard.py`→`test_cost_guard.py`・`test_reeval.py`→`test_cost_reeval.py` は一意だが命名を統一。design §3.2の `test_runner.py` は実ファイル名 `test_matching_runner.py` へ読み替え(ws-3実装時リネーム済み)
9. **`worker/cost/errors.py` を追加**(design §3.1への追加): `JevCostDependencyError` の置き場所。ratelimit/errors.py と対称(design §2.4-7「専用例外」の実体)
10. **layer3 の関数シグネチャ(design §3.1のコメントからの確定)**: `rule_score` は素の値を keyword-only で受ける(`rule_score(*, origin_time_start, cand_time_start, origin_budget, cand_budget)`)。`_soft_texts` は公開 `soft_texts(structured_data)`(origin.py・layer2.py が呼ぶため。layer3 は Origin/RetrievedCandidate に依存しない純関数モジュールとし循環importを構造的に排除)。時間・予算の個別関数 `time_closeness`・`budget_closeness` を公開(design §4.1が境界値を個別検証するため)
11. **月次上限到達ログは「INCR戻り値 == MONTHLY_LIMIT + 1(600,001)」のとき1回**(design §2.4-4の「30万+1到達時」は上限超過跨ぎ(denyと同時)の意と解釈 — 80% alertと同じ跨ぎ検出パターンで原子性により1回)。`resume_at` は `次暦月01日T00:00:00+09:00` の文字列
12. **alert_emitter は guard のコンストラクタ注入**(既定 `structured_log_alert`・`latch.cost.alert` logger で WARNING、JSON本文添え、レポートは `report` フィールド)。scan_report失敗時もalert自体は発報(reportフィールド欠落)
13. **scan_report の上位件数は10件**(実装定義・`_REPORT_TOP_N = 10`)。集計対象は `{prefix}jev:intent:*:{day}`・`{prefix}jev:user:*:{day}`・`{prefix}jev:exec:{day}:*` のSCAN+GET
14. **Redisキー形式**(design §2.4の表どおり・prefix付き): `jev:daily:{yyyymmdd}`(TTL 48h)・`jev:monthly:{yyyymm}`(TTL 45日)・`jev:intent:{intent_id}:{yyyymmdd}`(48h)・`jev:user:{user_id}:{yyyymmdd}`(48h)・`jev:exec:{yyyymmdd}:{provider}`(48h)・`reeval:{intent_id}`(SET NX EX 1800)。バケット文字列は guard が `clock.jst_date()` から導出し store へ渡す(ratelimitと対称)。key_prefix はキー全体への前置(`{prefix}jev:...`)
15. **`RetrievedCandidate` は6フィールド**(intent_id・version・similarity・time_start・budget_max・soft_texts)。`Origin` への `soft_texts` 追加に伴い既存unit試験2ファイル(test_matching_runner.py の直構築・test_origin.py の `_row()`)へ機械的追随が発生(§4列挙どおり・supervisor承認)
16. **TTL切れ再許可の検証方法(design §4.2-4からの実装解釈変更)**: 実Redis/fakeredisのTTLは実時間で減るためFakeClockを進めても切れない。unit・integrationとも「キーDELETEでTTL切れ相当(キー消失→SET NX成功)」で検証し、TTL設定値の正しさはキーのTTLピン(0 < ttl ≤ 1800)で担保する
17. **`Worker.__init__` の新パラメータは `reeval` のみ**(redisクライアントはrun()内で構築・owns_redis フラグでfinally aclose)。注入済みstage1へは手を付けない(既存DIの流儀)。`_run_matching` は `_reeval` が None の場合ガードなしで実行(run()を経れば常に構築済み。防御的なNone許容)
18. **stage1 の種別処理の最終形**: `EVENT_DELETED`→CLOSE_CANDIDATES / `EVENT_CREATED・EVENT_UPDATED`→embedding_hook / `embedding_completed`→matching_hook(この順。expired・scheduled は処理実体なし(processed)のまま)

---

## Self-Review の記録(計画書作成時点の確認)

1. **Specカバレッジ**: design §1.2確定値16項目→§1参照仕様表へ反映(引用#1〜#16すべて出典付き)。§2.1(案A→Task 6)・§2.2(実装定義→Task 1)・§2.3(全件記録・topkc→Task 6・9)・§2.4(store/guard/キー/INCR規律→Task 2〜3)・§2.5(日付キー切替→Task 2の`test_day_key_switch_resets_counter`)・§2.6(reeval→Task 4・8)・§2.7(配線→Task 7〜8)・§2.8(マイグレーションなし・docs改版なし→§5禁止)・§2.9(YAGNI切り捨て10項→§5禁止へ反映)・§3.1/3.2(ファイル一覧→§4)・§3.3(禁止→§5)・§4.1(unit試験→Task 1〜4・5〜8)・§4.2(integration 5試験+対抗策→Task 9)・§4.3(検証手順→§6・§7)・§5-1〜3(supervisor承認→structured_log_alert・M3-4への先送り・機械的追随)・§5-4(jsonb読み取り型→soft_textsのstr解釈+報告書記録)・§5-5(回帰→報告書)・§5-6〜7(ws-5/ws-6引継ぎ→§5禁止・§7報告形式)・§5-8〜9(先送り→§5禁止)。design §1.4のスコープ外7項目は§5禁止へ反映
2. **プレースホルダ**: TBD/TODO/「後で決める」記述なし。コードは最終形のみを掲載(Task 1執筆中に紛れ込んだ中間案は本体内で最終形に統一済み)
3. **型一貫性**: `ScoredCandidate`(Task 1定義)をTask 6・9が同型で使用。`RetrievedCandidate` 6フィールド(Task 5)をTask 6・test_matching_runner・Task 9が同型で使用。`Origin.soft_texts`(Task 5)をTask 6(runner)・Task 9が使用。`upsert_candidate` の `cheap_score` 引数(Task 5)をTask 6が呼ぶ形と一致。`JevCostStore` のメソッド名(incr_daily/incr_monthly/incr_intent/incr_user/record_execution/set_reeval_nx/scan_report)がTask 2定義・Task 3(StubStore)・Task 4(ReevalGuard)で一致。`ReevalGuard.allow`(Task 4)をTask 8(_FakeReeval)・Task 9が同呼び名で使用。`JevDecision.allowed/deny_reason`(Task 3)をtest_cost_guardのassertが使用
4. **Review Focus**: §3の5項目が所有タスクの試験に割り当て済み(各項目末尾に明記)
5. **試験数の整合**: unit新規4ファイル(test_layer3 10件+test_cost_store 8件+test_cost_guard 9件+test_cost_reeval 6件=33件)+既存4ファイルへの追記・更新(test_layer_sql 12件=10+2・test_origin 15件=13+2・test_matching_runner 4件=3+1・test_worker_stage1 +4件・test_worker +2件)+integration 5件(§6完了条件2・Task 9 Step 2の収集期待値と一致)
6. **basename一意の機械確認(§0)**: 新規5ファイル名(test_layer3・test_cost_store・test_cost_guard・test_cost_reeval・test_matching_cheapjudge)は2026-09-29時点のbackend/tests既存90ファイルと衝突しないことを作成前に確認済み(`find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空)。design §3.1指定のtest_store.pyはratelimit既存と衝突するため§9-8のとおりリネーム
