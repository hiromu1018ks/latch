# M2 ws-2(Embedding Worker)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ws-1が開通させたイベント経路(created/updated Event → Stage1処理)にEmbeddingの実体を載せる — (1) 正規化テキスト生成(純関数・raw_text不使用)、(2) LLM Gateway Embedding系統のreal化(gemini-embedding-001・timeout 2秒・再試行なし)、(3) intents.embedding/embedding_model書き込み、(4) 派生イベントembedding_completedの発行、(5) 失敗Intent(embedding NULL)のバックフィル。完成ラインは「embedding_completedが発行され、ws-3が消費できる状態」(受信時の第2段実行はws-3スコープ)。

**Architecture:** Embedding実行はStage1コミット後・ack前にWorker配線(`Worker._dispatch`/`_on_release`)から`EmbeddingWorker.handle`をawaitする(design §2.1-B。Stage1フックは不使用・stage1.py無変更)。handleは2フェーズ(短トランザクションread → API呼び出しはトランザクション外 → version+embedding IS NULLガード付きUPDATEとembedding_completed行INSERTの1トランザクション)で、正しさはガードが担保しat-least-onceの重複実行を許容する。LLM失敗は握ってembedding NULLのままreturn(バックフィル対象・06 D-15)、DB失敗は例外伝播でackなし再配信が回収。バックフィルはWorker内周期タスク(sleep-first 300秒・バッチ50・対象=embedding IS NULL AND status='active'のみ)。real化は`build_embedding_gateway`新設(Embedding系統のみGemini実・parser/jevはstub継続)、SDKはgoogle-genai・明示api_key・HttpRetryOptions(attempts=1)でSDK既定の再試行を無効化。内容更新時の再エンベディングは`store._UPDATE`への`embedding = NULL, embedding_model = NULL`2行挿入で実現(resume経路のupdate_statusはクリアしない)。

**Tech Stack:** 既存(Python 3.13 / FastAPI / SQLAlchemy[asyncio]+asyncpg / pytest)+ **google-genai(本単位が新規追加・唯一の依存追加)**。**マイグレーション追加なし**(alembic 0002がheadのまま — design §2.9)。**docs(01〜12)改版なし**(§2.3の判断は改版候補としてsupervisorへ申告のみ — design §2.9)。

**Spec:** `docs/plans/M2/ws-2-design.md`(agent1設計メモ。設計判断として未解決の論点はなし — design §5。実装時確認事項4件は本計画のTask 1・4・10・11へ組み込み済み)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m2-ws-2`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(本単位は依存追加があるためlock更新込みで導入される)。
- **共有ci-db運用**(STATUS運用ルール1〜3): compose常設環境(latch-ci)のDBとalembic状態はworktree間・スーパーバイザー検証の共有資産。本単位は**マイグレーション追加なし**のため `make migrate` は不要。**開発はunit試験(`make lint`・`make test`)で完結させる** — 実装エージェントは `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` / `docker pull` を一切実行しない(ws-1と違い**docker系の例外許可はゼロ** — design.mdにdocker関連の許可記載なし)。integration試験ファイルは作成するが**実行せず**、報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する。`make embed-smoke`(実APIスモーク)も実行しない(実値・契約の確認はスーパーバイザー領域 — design §4.3)。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。本計画の新規テストファイル(`test_llm_gemini.py`・`test_worker_embedding_text.py`・`test_worker_embedding.py`・`test_worker_backfill.py`・`test_embedding_clear_on_update.py`・`test_embedding_pipeline.py`)はbackend/tests配下全体で一意であることをコミット前に `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることで確認する。
- **並走単位あり(競合回避 — design §3.4・supervisor指示)**: M2実行waveは ws-1 → (ws-2 ∥ ws-3)。本単位はws-3(Layer 1+2)と並走する。交差ファイルへの変更は次の規律を厳守する:
  1. **worker/main.py**: (a)`__init__`への引数追加は引数並びの**末尾**に限る(default付き・既存引数 `sleep` の後ろへ)。(b)run()内の構築・タスク起動は既存行の**後ろへの追記**。(c)`_dispatch`/`_on_release`へのEmbeddingキック呼び出しは**既存行の間への純挿入**(既存行の変更・削除はしない)。ws-3のstage2配線も同一規約で行うため、マージ時の競合は「両側追記保持」で解消できる
  2. **intents/service.py は無変更**(内容更新時クリアはstore._UPDATEの2行挿入で完結 — design §2.3)
  3. **stage1.py は無変更**(design §3.3。ローカル定数 `_EVENT_EMBEDDING_COMPLETED`・未使用の `embedding_hook` はそのまま残す)
  4. **tests**: 新規ファイルのbasename一意(前述)。`test_worker.py`(ws-1資産)は**無変更**(Workerの配線試験は本単位新設の `test_worker_embedding.py` へ置く)。`test_events_pipeline.py` の期待値追従は後述の規定(Task 10)に限定する
  5. **マイグレーション追加なし**(共有ci-dbのalembic_version取り合いは発生しない)
- **設計判断の固定値**: design §2の採用判断(Worker配線案B・2フェーズトランザクション・_UPDATEでのembeddingクリア・Worker内周期バックフィル・google-genai SDK+retry無効化・build_embedding_gateway新設・マイグレーションなし・docs無改版)と本計画§8のIF確定事項(EmbeddingWorker/BackfillRunnerのAPIとSQL・正規化テキストの導出規則・設定既定値・Makefileの形)は固定値として落としてある。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする(12 第8節 運用ルール5)。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由(システムPythonと衝突させないため)。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| Embedding対象テキストは `{category.primary} / {時間帯の表現(例: 平日夜20-23時)} / {location.name} / {人数の表現} / {soft_constraintsを「・」で連結}`。**raw_textは使わない** | 07 §3 |
| モデルは04 D-14基準を満たす多言語対応の埋め込みモデル。次元は768(05のvector(768))。モデル識別子と版をintents.embedding_modelに記録(01 §18) | 07 §3・05 §2・01 §18 |
| Embedding呼び出しはtimeout 2秒・**再試行なし**。失敗時は「保存は行い、回復後にバックフィル、完了時にembedding_completedで復帰」 | 07 §1・07 §3・06 §1(層別予算Embedding区間≤2秒) |
| 第2段トリガー: Embedding WorkerがLLM Gateway経由でEmbedding APIを呼び、intents.embeddingとembedding_modelを書き込んだうえで、派生イベント`embedding_completed`(match_eventsに記録)を発行する | 06 §9・06 §1 |
| embedding_completedのidempotency keyは(embedding_completed, source_intent_id, version) | 06 §9 |
| Embedding要求のキックはintents.embedding IS NULLのときのみ。既に存在する場合(resume由来 — テキストは不変)はスキップし、第2段相当のパイプライン投入へ直接進む | 06 §9 |
| 呼び出しタイミングはIntentのactive作成・active化(draft→active)・active更新後(イベント駆動)。draft状態では呼ばない | 07 §1 |
| Embedding失敗のIntentはembedding=NULLで保存し、回復後に一括再エンベディングのバックフィル。それまで候補の生成元にも対象にもしない。バックフィル完了時はembedding_completedを発行してパイプラインへ復帰 | 06 D-15・06 §3 |
| draftはEmbeddingしない。draft→active化の初回投入は作成種Eventと同一の経路 | 06 §3・06 §9-0・05 §5 |
| 送信データは正規化テキストに限り、raw_textを外部へ送る経路を持たない。LLM Gateway経由で呼び出し、送信先・データ種別・時刻を記録 | 01 §21・07 §1・08 §3 |
| 作成Eventは窓なし即時・更新Eventのみ10秒トレーリング窓で統合(ws-1実装済み) | 06 §9・06 §1 |
| Embeddingプロバイダ=Google Gemini API有料tier・gemini-embedding-001。768はパラメータ指定・入力上限2,048トークン | T1 v0.2 §2.4・§3 |
| T1コストモデルのEmbedding回数の内訳は「Active作成・active化・active更新」(内容更新の再エンベディングを織り込み済み) | T1 v0.2 §4 |
| intents.embedding=vector(768) NULL=未完了または失敗 / embedding_model=text。HNSW cosine索引あり | 05 §2・05 §3 |
| テスト環境は本番と同じ種類で規模縮小。時刻操作はClock経由 | 10 §1 |
| outbox(match_events)→publish→フォールバックリレー・Worker内subscribe/debounce・冪等受領(ON CONFLICT DO NOTHING)はws-1実装済みの経路をそのまま使う | M2 ws-1(06 §9の実装) |
| Embeddingモデル確定がvector次元固定の前提。Gateway抽象化によりプロバイダ差し替えは実装進行可能 | 12 §2 C11 |
| M2スコープ2(Embedding Worker)・依存(ws-1) | 12 §3 M2-2 |
| 既存実装資産(worker/main.py配線点・gateway.embed_intent・StubLLM・store._UPDATE・Clock) | design §1.3 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **永続化は `sqlalchemy.text()` 生SQLのみ**(intents/events.py・store.py・worker/stage1.pyと同じ形式)。ORMモデル・リポジトリ層を作らない。**時刻は `clock.now()` の明示値**(DB時刻関数DEFAULTに頼らない)
- **製品コード(`backend/src/latch/`)で実時間への直接参照を禁止**(arch test `test_arch_no_direct_time.py` が強制: `datetime.now` / `utcnow` / `date.today` / `time.time` / `time.monotonic` / `time.sleep` / `from time import`)。バックフィルの周期待ちは **`asyncio.wait_for(stop.wait(), timeout=...)`**(design §2.5)・debounceと同じく待機はasyncio。embed_smoke.pyのレイテンシ計測は `asyncio.get_running_loop().time()`(禁止トークン外・時刻参照ではなく処理時間の測定)
- **SELECT・導出・ログの3経路すべてでraw_textを扱わない**(確定値#10): EmbeddingWorkerのSELECT列にraw_textを含めない・`EmbeddingTextInput` はraw_textフィールドを持たない・ログへ正規化テキスト・raw_text・表示名を出さない(08 §2.4。送信記録はGatewayのSendRecordが担う)
- **vector列への書き込みは `CAST(:vec AS vector)` の明示CAST**(bind param直後の `::` 短縮CASTはSQLAlchemyが認識しない — M1 ws-3事故の回帰予防。test_store_sql.pyと同じ流儀)
- **理由追記・イベント行INSERTのpayloadは `{"version": N}` のみ**(UNIQUE索引が参照する `payload->>'version'` を壊さない — ws-1と同じ形式)
- **publishはDBトランザクションの外(コミット後)**。publish失敗は例外にせずログで握る(フォールバックリレーが30秒後に回収 — design §2.2)
- **unit試験は外部プロセス不要・実時間待ちなし**(FakeClock+スタブconn+注入部品)。SQL文字列の正当性はintegration(test-ci)で担保する(design §4.1)
- **依存追加は `google-genai` のみ**(`backend/pyproject.toml` + `backend/uv.lock` はTask 1で変更・以後触らない)。テスト用ライブラリ追加なし
- ruff対象(`E`,`F`,`I`,`UP`,`B`)と `ruff format` を毎コミット通す
- LLM呼び出しの失敗(LLMTimeoutError/LLMProviderError・次元不一致)は**handle内で握ってログのみ**(再試行なし・バックフィル対象)。DB失敗(SQLAlchemyError)は**例外を伝播**(呼び出し側のackなし再配信が回収 — design §2.1-B)

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **raw_textの外部送信(確定値#10 — 最重大)** — SELECT列・入力dataclass・ログのいずれかにraw_textが混入すると、送信記録の対象を超えた個人情報がLLMプロバイダへ流出する。3経路すべて構造的に排除する → Task 2の `test_input_dataclass_has_no_raw_text_field`・Task 6の `test_select_does_not_read_raw_text`・Task 10の `test_1_e2e_create_embeds_and_emits`(実経路のtext検証)が所有
2. **SDK既定のtenacity再試行の復活(確定値#3「再試行なし」違反)** — google-genaiは既定で再試行が入り得るため、HttpRetryOptions(attempts=1)の明示指定がないとtimeout 2秒が実効2秒×N回に膨らむ → Task 4の `test_http_options_pin_no_retry`・`test_timeout_pin_matches_gateway` が所有(実機実効性はスモーク=スーパーバイザー検証)
3. **内容更新後の旧ベクトル残留(06 §3の意味崩壊)** — クリアなしでPATCHすると更新後のIntentが常に旧テキストのベクトルで検索され、Layer 2の意味が崩れる → Task 3の `test_update_sql_clears_embedding_columns`・`test_update_status_sql_does_not_clear_embedding`(resumeはクリアしないことの両面ピン)・Task 10の `test_2_update_clears_and_reembeds` が所有
4. **埋め込み実行中クラッシュの回収喪失** — Stage1コミット〜handle完了の間(最大2秒)のクラッシュで再配信がduplicateになるため、duplicate経路でhandleを呼ばない実装だとバックフィル300秒を待たない即時回収が失われる(design §2.1-B) → Task 8の `test_dispatch_duplicate_created_kicks_embedding` が所有
5. **バックフィルのws-3 fixture誤爆** — ws-3は試験fixtureでembeddingを直入れ(embedding_model NULL)するため、対象抽出が「embedding IS NULL」以外の条件(例: embedding_model IS NULL)だと並走単位のfixtureを再エンベディングして破壊する → Task 7の `test_run_once_selects_targets_sql_pin`(`embedding IS NULL AND status = 'active'` のSQLピン)が所有

並行handleによるAPI二重呼び出し(at-least-onceの帰結・design §2.2のトレードオフとして受容)は、Task 6の `test_handle_update_zero_rows_no_event`(ガード付きUPDATE 0行→イベント行もpublishもなし)が二重書き込み排除を担保する。

## 4. スコープ(作成・変更するファイル一覧)

作成(design §3.1どおり):

```text
backend/src/latch/llm/gemini.py                    (Task 4。GeminiEmbeddingProvider・_make_client・_http_options・定数)
backend/src/latch/llm/embed_smoke.py               (Task 9。実APIスモーク: python -m latch.llm.embed_smoke)
backend/src/latch/worker/embedding_text.py         (Task 2。EmbeddingTextInput・build_embedding_text・帯区分表)
backend/src/latch/worker/embedding.py              (Task 6。EmbeddingWorker.handle・2フェーズSQL)
backend/src/latch/worker/backfill.py               (Task 7。BackfillRunner.run_once/run)
backend/tests/unit/test_llm_gemini.py              (Task 4。Task 5でbuild_embedding_gateway分を追記)
backend/tests/unit/test_worker_embedding_text.py   (Task 2)
backend/tests/unit/test_worker_embedding.py        (Task 6。Task 8でWorker配線試験を追記)
backend/tests/unit/test_worker_backfill.py         (Task 7)
backend/tests/unit/test_embedding_clear_on_update.py (Task 3)
backend/tests/integration/test_embedding_pipeline.py  (Task 10。作成のみ・実行しない)
docs/plans/M2/ws-2-report.md                       (Task 11。報告ファイル)
```

変更(design §3.2どおり):

- `backend/pyproject.toml`(+ `backend/uv.lock` は `uv sync` が更新)— 依存へgoogle-genaiを追加(Task 1)
- `backend/src/latch/settings.py` — `llm_gemini_api_key`・`embedding_backfill_interval_sec=300`・`embedding_backfill_batch_limit=50` を末尾へ追記(Task 1)
- `backend/src/latch/intents/events.py` — `EVENT_EMBEDDING_COMPLETED = "embedding_completed"` 定数の追記のみ(Task 6)
- `backend/src/latch/intents/store.py` — `_UPDATE` のSET句へ `embedding = NULL, embedding_model = NULL` の2行を純挿入(Task 3)
- `backend/src/latch/llm/gateway.py` — `build_embedding_gateway` の追記(ファイル末尾)。**既存の `build_llm_gateway`・各系統メソッド・Timeoutsは無変更**(Task 5)
- `backend/src/latch/llm/__init__.py` — `GeminiEmbeddingProvider`・`build_embedding_gateway` のexport追記(Task 5)
- `backend/src/latch/worker/main.py` — Workerへ `embedding`/`backfill` のオプション注入(引数末尾)+run()での未注入時構築+`_dispatch`/`_on_release`へのキック純挿入+backfillタスク起動/停止。**§0競合回避規律に従う**(Task 8)
- `Makefile`(リポジトリルート)— `embed-smoke` ターゲット追記(Task 9)
- `backend/tests/integration/test_events_pipeline.py` — **期待値の機械的追従のみ**(Task 10。分析の結果「追従不要」の場合は無変更のまま報告書に根拠を記録。詳細はTask 10 Step 2)

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`(gitignore済み)。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **docker系コマンド・実環境操作**: `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` / `docker pull`(本単位はws-1のようなdocker例外なし)。**`make embed-smoke` も実行しない**(実API呼び出し=スーパーバイザー領域 — design §4.3。実行ガードの確認のみTask 9で実施)
- `backend/src/latch/worker/stage1.py` — **無変更**(design §2.1-B・§3.3。`_EVENT_EMBEDDING_COMPLETED` ローカル定数・未使用の `embedding_hook` はそのまま)
- `backend/src/latch/intents/service.py`・`routes.py`・`schema.py`・`mapping.py`・`completion.py`・`prompt.py`(§2.3のクリアはstore._UPDATEの2行挿入で完結)
- `backend/src/latch/events/`(bus.py・pubsub_bus.py・relay.py — ws-1の経路をそのまま使用)
- `backend/src/latch/llm/` の既存行(stub.py・records.py・anthropic.py・providers.py・errors.py・gateway.pyの既存メソッド・`build_llm_gateway` — design §2.8-Aの判断)
- `backend/alembic/`(0002がheadのまま・design §2.9)、`compose.yaml`(ci workerはstubのため鍵渡し不要・design §2.7)
- `backend/tests/unit/test_worker.py`(ws-1資産・§0競合回避4)、`backend/tests/conftest.py`・`backend/tests/integration/conftest.py`、その他既存テストファイル(`test_events_pipeline.py` は§4の期待値追従のみ)
- auth / users / ratelimit / geo / g1gate / frontend / prototype / docs 01〜12 / `docs/plans/STATUS.md`(スーパーバイザー管理)/ `docs/plans/M0/`・`docs/plans/M1/` / `docker/`・`.mise.toml`・`.gitignore`・`README.md`・`.claude/`・`.agents/`・`.hermes/`
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4に列挙された後続単位のスコープ):
  - **embedding_completed受信時の第2段実行**(Layer 1〜5・embedding IS NULL再検査5回→隔離) — ws-3。stage1のembedding_completed受信時処理は現状(processed)のまま
  - **モデル変更時のバックフィル**(embedding_model不一致対象の再エンベディング)— 本単位のバックフィル対象は「embedding IS NULL」のみ(07 §3はモデル切替時の規定)
  - **実GCP Pub/Sub・staging構成** — ws-1の抽象(設定切替)のまま
  - **Embedding精度ゲート** — docs規定なし(埋め込み品質はLayer 3/4とG2日本語評価が間接担保)
  - **バックフィルの動的レート調整・per-intentリトライ状態管理・指数バックオフ**(design §2.10-3・06 §9-10)
  - Embedding呼び出しの同時実行セマフォ(design §2.10-2)・テキスト要素の差分比較によるクリア条件付け(§2.10-4)・embedding_completed手動発行CLI(§2.10-5)・新マイグレーション(§2.10-7)
  - Jev系統のreal化(ws-5)・`build_llm_gateway` のreal定義変更(§2.8-A)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 11で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分を含む)
   検証: `make lint && make test` — ともにexit 0
2. **integration 7試験(test_embedding_pipeline.py)が収集できる** — **ただし実行はしない**(§0。api/workerイメージ再ビルド+エミュレータ起動が必要なためスーパーバイザー検証時に実施)
   検証(実装側): `cd backend && uv run pytest --collect-only tests/integration/test_embedding_pipeline.py -q` がexit 0(7件収集)。報告書に「**test-ci=スーパーバイザー検証待ち**」と記録
3. **実時間参照が `core/clock.py` のみ(worker/・llm/配下はヒットしない)**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
4. **`backend/alembic/` と `docs/`(01〜12)に差分なし**
   検証: `git diff --stat main -- backend/alembic docs` — 出力なし
5. **触るファイルが §4 の一覧どおり**
   検証: `git diff --name-only main | sort` が§4の一覧と完全一致(test_events_pipeline.pyは§4の規定により「無変更」または「期待値のみ」)。`git status --short` が空(未コミット変更なし)
6. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし
7. **embed-smokeターゲットが規定どおり**(実API実行はしない)
   検証: `make -n embed-smoke` のドライラン出力が `cd backend && uv run --env-file ../.env python -m latch.llm.embed_smoke` に展開されること。かつ `cd backend && uv run python -m latch.llm.embed_smoke; echo "exit=$?"`(envなし=llm_mode=stub)が実APIを呼ばず **exit=1**(起動ガード)を返すこと

## 7. 報告形式

**結果ファイル**: `docs/plans/M2/ws-2-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M2 ws-2(Embedding Worker) 実行報告

- ブランチ: m2-ws-2 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る> |
| 2 | integration 7試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | <collect-only出力> |
| 3 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 4 | alembic・docs無変更 | PASS/FAIL | <git diff --stat 出力(空なら「空」)> |
| 5 | 触るファイルがスコープどおり | PASS/FAIL | <git diff --name-only出力> |
| 6 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 7 | embed-smokeターゲット・起動ガード | 記載確認/exit=1 | <make -n 出力 + exit=1の出力> |

## design §5 実装時確認事項の結果
1. google-genaiの実機挙動(HttpRetryOptions(attempts=1)の実効性・embed_content応答形式・タイムアウト例外型): <unit試験の契約ピン結果+SDKバージョン。実機確認は make embed-smoke(スーパーバイザー)待ちと記録>
2. バックフィル初期値(周期300秒・バッチ50): <settings.py の実装値>
3. test_events_pipeline.pyの期待値追従範囲: <追従した箇所/「追従不要」の分析根拠>
4. docs改版候補(§2.3 内容更新時embeddingクリア): <05 §5または06 §9への一文言追記案をsupervisorへ申告>

## 固定値の変更有無(design.md §2・本計画§8)
- 実行配置=Worker配線案B(design §2.1): 変更なし / 変更あり(<前→後+理由>)
- 2フェーズ+ガード付きUPDATE(design §2.2): 変更なし / 変更あり(<前→後+理由>)
- _UPDATEでのembeddingクリア(design §2.3): 変更なし / 変更あり(<前→後+理由>)
- バックフィル=Worker内周期タスク(design §2.5): 変更なし / 変更あり(<前→後+理由>)
- google-genai・明示api_key・retry無効化(design §2.6): 変更なし / 変更あり(<前→後+理由>)
- build_embedding_gateway新設(design §2.8): 変更なし / 変更あり(<前→後+理由>)
- 本計画§8のIF確定事項(EmbeddingWorker/BackfillRunnerのAPI・SQL・正規化テキスト導出規則・設定既定値・Makefile形式): 変更なし / 変更あり(<前→後+理由>)

## スーパーバイザー検証手順(test-ci・embed-smoke実行時)
1. `docker compose build api worker` — **api・workerイメージの再ビルドが必須**(make test-ci の compose up は再ビルドしないため・STATUS運用ルール4)
2. `make test-ci` — test_embedding_pipeline.py(#1〜#7)を含む全体グリーン。**test_events_pipeline.py が赤化した場合は期待値の値のみを最小修正する**(試験意図を変えない — design §3.4-3。修正できない赤化はagent3へ差し戻し)
3. `make embed-smoke` — 実APIスモーク1呼び出し(dim=768・レイテンシ・exit 0)。契約ズレ(HttpRetryOptionsの引数名・応答形式等)が検出された場合はその内容を記録
4. 常設worker復帰の確認: `docker compose ps` で worker が running に戻っていること(llm_mode=stubでbuild_embedding_gatewayがStubLLM構築となり起動する)

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件7項目の結果一覧(項目2の実行部分は「test-ci=スーパーバイザー検証待ち」・項目7の実API部分は「embed-smoke=スーパーバイザー検証待ち」)。

---

## 8. 実装ステップ(TDD。Task 1〜11の順で実行する)

### Task 1: 依存追加(google-genai)・設定3件

**Files:**
- Modify: `backend/pyproject.toml`(+ `backend/uv.lock` は `uv sync` が更新)
- Modify: `backend/src/latch/settings.py`

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: Settingsの新フィールド3件(以降の全タスクが参照): `llm_gemini_api_key`(alias LATCH_GEMINI_API_KEY)/ `embedding_backfill_interval_sec: int = 300` / `embedding_backfill_batch_limit: int = 50`。依存 `google-genai>=1.33`(Task 4がimport)

- [ ] **Step 1: 依存を追加する**

`backend/pyproject.toml` の `dependencies` へ1行追加(`google-cloud-pubsub` の直後・google系を隣接させる):

```toml
    "google-genai>=1.33",
```

※ `>=1.33` はdesign §5-1の想定。`uv sync` 解決の実バージョンを控えて報告ファイルdesign §5-1へ記録する(乖離があってもpyprojectの下限はこのままでよい)。

- [ ] **Step 2: 依存を導入する**

```bash
make setup
```

`backend/uv.lock` が更新される。`cd backend && uv run python -c "from google import genai; print(genai.__version__)"` でimportできることを確認し、バージョンを報告ファイル用に控える。

- [ ] **Step 3: 設定を追加する**

`backend/src/latch/settings.py` の末尾(`event_retry_max` の後)へ追記:

```python
    # --- Embedding Worker(M2 ws-2。design §3.2)---
    # Gemini API鍵(Embedding系統real化・T1 v0.2 §2.4)。実値は.env(git管理外)へ
    # 書き、make embed-smoke(uv run --env-file ../.env)経由でのみプロセスへ渡す
    # (g1-gateと同じ規律)。ci環境(compose)へは渡さない(workerはstubのため)。
    # SDKは環境変数GEMINI_API_KEY/GOOGLE_API_KEYを自動採用するため、aliasで
    # 明示渡し経路のみとする(llm_anthropic_api_keyと同じAliasChoices形式)
    llm_gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LATCH_GEMINI_API_KEY", "llm_gemini_api_key"),
    )
    # バックフィル周期タスク(06 D-15・design §2.5)。初期値(計測後に調整 — 06 §9-10)
    embedding_backfill_interval_sec: int = 300
    embedding_backfill_batch_limit: int = 50
```

- [ ] **Step 4: 既存試験が壊れないことを確認する**

Run: `make lint && make test`
Expected: ともにexit 0(既存 `tests/unit/test_settings.py` は app_env/log_level/database_url のみ検査しており新フィールドの影響を受けない)

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/latch/settings.py
git commit -m "feat: google-genai依存とEmbedding Worker設定を追加(M2 ws-2)"
```

### Task 2: 正規化テキスト導出(embedding_text.py・純関数)

**Files:**
- Create: `backend/src/latch/worker/embedding_text.py`
- Test: `backend/tests/unit/test_worker_embedding_text.py`

**Interfaces:**
- Consumes: `latch.core.clock.JST`(固定+9)
- Produces(Task 6のEmbeddingWorkerが使用):
  - `EmbeddingTextInput`(frozen dataclass): `category_primary: str` / `structured_data: dict` / `participants_min: int | None` / `participants_max: int | None` / `time_start: datetime | None` / `time_end: datetime | None`。**raw_textフィールドを持たない**(確定値#10の構造ピン)
  - `build_embedding_text(inp: EmbeddingTextInput) -> str` — 空でない要素を `" / "` で連結。要素導出規則はStep 3の実装コメントの表どおり(07 §3)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_embedding_text.py` を作成:

```python
"""正規化テキスト導出の純関数試験(design §2.4・§4.1)。

07 §3の形式仕様と例示(月曜20:00-23:00 → 平日夜20-23時)を全文ピンで
拘束する。入力dataclassがraw_textを保持しないことの構造ピン(確定値#10)。
帯区分表はdocs規定がなく実装定義のため、ここで全文を固定する。
"""

import dataclasses
from datetime import datetime

from latch.core.clock import JST
from latch.worker.embedding_text import EmbeddingTextInput, build_embedding_text

# 2026-09-28は月曜(平日)。JST 20:00-23:00 = 07 §3の例と同一時刻
MON_20_23_START = datetime(2026, 9, 28, 20, 0, tzinfo=JST)
MON_20_23_END = datetime(2026, 9, 28, 23, 0, tzinfo=JST)


def _inp(**overrides) -> EmbeddingTextInput:
    base = dict(
        category_primary="drinking",
        structured_data={
            "location_name": "天文館",
            "soft_constraints": [
                {"text": "静かなお店で", "downgraded_from_ng": False},
                {"text": "予算は抑えめ", "downgraded_from_ng": True},
            ],
        },
        participants_min=2,
        participants_max=4,
        time_start=MON_20_23_START,
        time_end=MON_20_23_END,
    )
    base.update(overrides)
    return EmbeddingTextInput(**base)


def test_reference_example_from_07_section3():
    """07 §3の例どおり: 月曜20:00-23:00 → 『平日夜20-23時』を含む全文。"""
    text = build_embedding_text(_inp())
    assert text == (
        "drinking / 平日夜20-23時 / 天文館 / 2-4人 / 静かなお店で・予算は抑えめ"
    )


def test_soft_constraints_empty_omits_trailing_separator():
    """soft_constraints空 → 末尾の「 / 」が残らない(空要素の省略)。"""
    inp = _inp(structured_data={"location_name": "天文館", "soft_constraints": []})
    assert build_embedding_text(inp) == "drinking / 平日夜20-23時 / 天文館 / 2-4人"


def test_band_table_boundaries():
    """帯区分表: 5≤h<11朝/11≤h<16昼/16≤h<19夕方/19≤h<23夜/その他(23・0〜4時)深夜。"""
    cases = {  # 開始時刻(JST) → 帯(全て月曜=平日)
        4: "深夜", 5: "朝", 10: "朝", 11: "昼", 15: "昼",
        16: "夕方", 18: "夕方", 19: "夜", 22: "夜", 23: "深夜", 0: "深夜",
    }
    for hour, band in cases.items():
        inp = _inp(
            time_start=datetime(2026, 9, 28, hour, 0, tzinfo=JST),
            time_end=datetime(2026, 9, 28, hour + 1, 0, tzinfo=JST) if hour < 23 else None,
        )
        text = build_embedding_text(inp)
        assert f"平日{band}" in text, (hour, text)


def test_weekend_day_of_week():
    """土曜19:00-22:00 → 週末夜(design §2.4の曜日規則)。"""
    inp = _inp(
        time_start=datetime(2026, 9, 26, 19, 0, tzinfo=JST),  # 2026-09-26は土曜
        time_end=datetime(2026, 9, 26, 22, 0, tzinfo=JST),
    )
    assert "週末夜19-22時" in build_embedding_text(inp)


def test_overnight_end_next_day():
    """日跨ぎ: 23:00〜翌2:00 → 深夜23-2時(endのJST時刻を使う)。"""
    inp = _inp(
        time_start=datetime(2026, 9, 28, 23, 0, tzinfo=JST),
        time_end=datetime(2026, 9, 29, 2, 0, tzinfo=JST),
    )
    assert "深夜23-2時" in build_embedding_text(inp)


def test_time_end_null_uses_以降():
    """time_end NULL → 『{start.hour}時以降』。"""
    inp = _inp(time_end=None)
    assert "平日夜20時以降" in build_embedding_text(inp)


def test_time_start_null_omits_time_element():
    """time_start NULL → 時間帯要素全体を省略(05 §2想定外値の防御)。"""
    inp = _inp(time_start=None, time_end=None)
    assert build_embedding_text(inp) == (
        "drinking / 天文館 / 2-4人 / 静かなお店で・予算は抑えめ"
    )


def test_headcount_equal_min_max():
    """min == max → 『{N}人』。"""
    inp = _inp(participants_min=3, participants_max=3)
    assert " / 3人 / " in build_embedding_text(inp)


def test_location_missing_omits_element():
    """location_name欠損 → 要素を省略(区切りが連続しない)。"""
    inp = _inp(structured_data={"location_name": None, "soft_constraints": []})
    assert build_embedding_text(inp) == "drinking / 平日夜20-23時 / 2-4人"


def test_input_dataclass_has_no_raw_text_field():
    """入力dataclassはraw_textを保持しない(確定値#10の構造ピン)。"""
    field_names = {f.name for f in dataclasses.fields(EmbeddingTextInput)}
    assert "raw_text" not in field_names
    assert field_names == {
        "category_primary",
        "structured_data",
        "participants_min",
        "participants_max",
        "time_start",
        "time_end",
    }
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_embedding_text.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.embedding_text'`)

- [ ] **Step 3: embedding_text.py を実装する**

`backend/src/latch/worker/embedding_text.py`:

```python
"""正規化テキスト導出(07 §3・design §2.4)。

07 §4の規律(算術・日付処理はコード側で行う)に準じて決定的な純関数で
導出する。帳区分表はdocsに規定がなく実装定義(07 §3の例示「平日夜20-23時」
との整合のみが拘束)。入力dataclassはraw_textを保持しない — 構造的に
raw_textがEmbedding経路に入らないことのピン(確定値#10・08 §3)。
形式に含めないもの: raw_text・visibility・notification_level・budget・
alcohol_involved・category_secondary(07 §4と同じ位置づけ: 表示・通知の
制御であり判定材料でない)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from latch.core.clock import JST


@dataclass(frozen=True)
class EmbeddingTextInput:
    """正規化テキスト導出の入力(intents列の部分・raw_textなし — 確定値#10)。"""

    category_primary: str
    structured_data: dict
    participants_min: int | None
    participants_max: int | None
    time_start: datetime | None
    time_end: datetime | None


def _band(hour: int) -> str:
    """開始時刻→帯(design §2.4の固定表・全文はunit試験がピン)。"""
    if 5 <= hour < 11:
        return "朝"
    if 11 <= hour < 16:
        return "昼"
    if 16 <= hour < 19:
        return "夕方"
    if 19 <= hour < 23:
        return "夜"
    return "深夜"  # 23時・0〜4時


def _time_phrase(start: datetime | None, end: datetime | None) -> str:
    """時間帯の表現。time_start NULLは要素全体を省略(空文字を返す)。"""
    if start is None:
        return ""
    s = start.astimezone(JST)
    dow = "平日" if s.weekday() < 5 else "週末"  # 月〜金/土日
    if end is None:
        clock = f"{s.hour}時以降"
    else:
        clock = f"{s.hour}-{end.astimezone(JST).hour}時"
    return f"{dow}{_band(s.hour)}{clock}"


def _headcount_phrase(pmin: int | None, pmax: int | None) -> str:
    if pmin is None or pmax is None:
        return ""  # DB列はNOT NULL(補完済み)。片方のみの指定は起きない想定の防御
    if pmin == pmax:
        return f"{pmin}人"
    return f"{pmin}-{pmax}人"


def _soft_constraints_phrase(raw: object) -> str:
    """structured_data.soft_constraints[].textを「・」で連結(降格込み・06 §3)。"""
    if not isinstance(raw, list):
        return ""
    texts = [
        item["text"]
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"]
    ]
    return "・".join(texts)


def build_embedding_text(inp: EmbeddingTextInput) -> str:
    """空でない要素を「 / 」で連結(空要素で区切りが残らない — 07 §3)。"""
    parts: list[str] = []
    if inp.category_primary:
        parts.append(inp.category_primary)
    time_part = _time_phrase(inp.time_start, inp.time_end)
    if time_part:
        parts.append(time_part)
    location = inp.structured_data.get("location_name")
    if isinstance(location, str) and location:
        parts.append(location)
    headcount = _headcount_phrase(inp.participants_min, inp.participants_max)
    if headcount:
        parts.append(headcount)
    soft = _soft_constraints_phrase(inp.structured_data.get("soft_constraints"))
    if soft:
        parts.append(soft)
    return " / ".join(parts)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_embedding_text.py -v`
Expected: PASS(10件)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/worker/embedding_text.py backend/tests/unit/test_worker_embedding_text.py
git commit -m "feat: 正規化テキスト導出の純関数(07 §3形式・raw_text非経路)"
```

### Task 3: 内容更新時のembeddingクリア(store._UPDATE 2行挿入)

**Files:**
- Modify: `backend/src/latch/intents/store.py`(`_UPDATE` のSET句へ2行の純挿入のみ)
- Test: `backend/tests/unit/test_embedding_clear_on_update.py`

**Interfaces:**
- Consumes: `IntentStore.update`(既存・シグネチャ無変更)
- Produces: `_UPDATE` が全呼び出し経路で `embedding = NULL, embedding_model = NULL` を設定(§2.3の表: active/paused内容更新=クリア→再エンベディング / draft再保存・draft→active化=no-op(draftはembedding NULL) / resume(`_UPDATE_STATUS`)=クリアされない・06 §9のresume規定どおり)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_embedding_clear_on_update.py` を作成:

```python
"""内容更新時のembeddingクリア(design §2.3・§4.1)。

_UPDATEは全置換UPDATEのため、SET句のNULL化が全呼び出し経路で一貫して
作用すること(active更新→クリア/draft→no-op)と、resume経路
(_UPDATE_STATUS)ではクリアされないこと(06 §9のresume規定)を検証する。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from latch.intents import store
from latch.intents.mapping import ResolvedColumns


def test_update_sql_clears_embedding_columns():
    """_UPDATEのSET句がembedding/embedding_modelをNULL化する(SQL文字列ピン)。"""
    compiled = str(store._UPDATE.compile(dialect=postgresql.dialect()))
    assert "embedding = NULL" in compiled
    assert "embedding_model = NULL" in compiled


def test_update_status_sql_does_not_clear_embedding():
    """resume(pause/resume/delete)経路の_UPDATE_STATUSはクリアしない。"""
    compiled = str(store._UPDATE_STATUS.compile(dialect=postgresql.dialect()))
    assert "embedding" not in compiled


class _RecordingConn:
    """IntentStore.updateが実行するSQLを記録するスタブ。"""

    def __init__(self):
        self.executed: list[tuple[str, dict | None]] = []

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        class _R:
            rowcount = 1
        return _R()


async def test_store_update_executes_clearing_sql():
    """IntentStore.update の実行SQLにもNULL化句が含まれる(スタブconn検証)。"""
    conn = _RecordingConn()
    st = store.IntentStore(engine=None)
    await st.update(
        conn,
        uuid.uuid4(),
        ResolvedColumns(),
        status="active",
        version=2,
        now=datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC),
        expected_status="active",
    )
    sql = conn.executed[0][0]
    assert "embedding = NULL" in sql
    assert "embedding_model = NULL" in sql
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_embedding_clear_on_update.py -v`
Expected: FAIL(`test_update_sql_clears_embedding_columns` のassert — `_UPDATE` にNULL化句がない)

- [ ] **Step 3: store.py の _UPDATE へ2行を挿入する**

`backend/src/latch/intents/store.py` の `_UPDATE` において、`version = :version,` の行と `time_start = :time_start,` の行の**間**へ2行を純挿入する(既存行は一切変更しない):

```python
_UPDATE = text(f"""
    UPDATE intents SET
        category_primary = :category_primary,
        alcohol_involved = :alcohol_involved,
        raw_text = :raw_text,
        structured_data = CAST(:structured_data AS jsonb),
        geo_center = {_GEO_CENTER_EXPR},
        geo_radius_m = :geo_radius_m,
        budget_max = :budget_max,
        participants_min = :participants_min,
        participants_max = :participants_max,
        visibility = :visibility,
        notification_level = :notification_level,
        status = :status,
        version = :version,
        embedding = NULL,
        embedding_model = NULL,
        time_start = :time_start,
        time_end = :time_end,
        expires_at = :expires_at,
        updated_at = :updated_at
    WHERE id = :intent_id AND status = :expected_status
""")
```

挿入理由のコメントとして、`_UPDATE` 定義の直前に次を1行追記してよい(追記は新規行・既存行無変更):

```python
# 内容更新(全置換)でembeddingをクリアし更新Eventのキックで再エンベディング
# (design §2.3。resume系は_UPDATE_STATUSのため対象外 — 06 §9のresume規定)
```

- [ ] **Step 4: テストが通ること・既存試験が壊れないことを確認する**

Run: `cd backend && uv run pytest tests/unit/test_embedding_clear_on_update.py tests/unit/intents -v && make lint && make test`
Expected: PASS + ともにexit 0(既存 `test_store_sql.py` のbind param検査はNULLリテラル追加の影響を受けない)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/intents/store.py backend/tests/unit/test_embedding_clear_on_update.py
git commit -m "feat: 内容更新時のembeddingクリア(store._UPDATE 2行挿入)"
```

### Task 4: GeminiEmbeddingProvider(gemini.py)

**Files:**
- Create: `backend/src/latch/llm/gemini.py`
- Test: `backend/tests/unit/test_llm_gemini.py`

**Interfaces:**
- Consumes: `EmbeddingProvider` ABC・`EMBEDDING_DIMENSIONS`(llm/providers.py・**無変更**)・`LLMProviderError`(llm/errors.py・無変更)・google-genai(Task 1)
- Produces(Task 5のbuild_embedding_gatewayが使用):
  - `GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"`(Task 6のEmbeddingWorkerも既定model_idとして使用)
  - `GEMINI_EMBEDDING_TIMEOUT_S = 2.0`・`GEMINI_HTTP_TIMEOUT_MS = 2000`(TIMEOUT_EMBEDDING_Sと同値・unit試験がピン)
  - `GeminiEmbeddingProvider(*, api_key: str, client: object | None = None)` — `name = "google"`(08 §3送信記録)。鍵空はValueError(fail-fast)。`async embed(text: str) -> list[float]` — 768検証(不一致はLLMProviderError)。SDK例外は握らず素通り(anthropic.pyと同じ規律)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_llm_gemini.py` を作成:

```python
"""GeminiEmbeddingProviderの契約ピン(design §2.6・§4.1)。

client注入スタブで実APIなしに検証する。実機挙動(HttpRetryOptionsの実効性・
応答形式・タイムアウト例外型)はmake embed-smoke(スーパーバイザー実行)が
初回検証する — design §5-1(ws-6の401事故と同じ位置づけ)。
"""

from types import SimpleNamespace

import pytest

from latch.llm.errors import LLMProviderError
from latch.llm.gateway import TIMEOUT_EMBEDDING_S
from latch.llm.gemini import (
    GEMINI_EMBEDDING_MODEL,
    GEMINI_EMBEDDING_TIMEOUT_S,
    GEMINI_HTTP_TIMEOUT_MS,
    GeminiEmbeddingProvider,
)
from latch.llm.providers import EMBEDDING_DIMENSIONS


class FakeAioModels:
    """client.aio.models.embed_contentのスタブ(呼び出し引数を記録)。"""

    def __init__(self, values, error=None):
        self.values = values
        self.error = error
        self.calls: list[dict] = []

    async def embed_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(embeddings=[SimpleNamespace(values=list(self.values))])


def _provider(values=None, error=None):
    aio = FakeAioModels(values or [0.25] * EMBEDDING_DIMENSIONS, error)
    client = SimpleNamespace(aio=SimpleNamespace(models=aio))
    return GeminiEmbeddingProvider(api_key="test-key", client=client), aio


def test_timeout_pin_matches_gateway():
    """SDK側timeoutはGateway TIMEOUT_EMBEDDING_Sと同値(design §2.6)。"""
    assert GEMINI_EMBEDDING_TIMEOUT_S == TIMEOUT_EMBEDDING_S
    assert GEMINI_HTTP_TIMEOUT_MS == 2000


def test_http_options_pin_no_retry():
    """HttpOptions: timeout=2000ms・retry attempts=1(SDK既定再試行の無効化)。"""
    from latch.llm import gemini

    opts = gemini._http_options()
    assert opts.timeout == GEMINI_HTTP_TIMEOUT_MS
    assert opts.retry_options.attempts == 1


def test_embed_calls_with_model_contents_and_768():
    """embed_contentの引数: model=gemini-embedding-001・contents=text・768指定。"""
    provider, aio = _provider()
    vec = await provider.embed("drinking / 平日夜20-23時 / 天文館")
    assert len(vec) == EMBEDDING_DIMENSIONS
    call = aio.calls[0]
    assert call["model"] == GEMINI_EMBEDDING_MODEL
    assert call["contents"] == "drinking / 平日夜20-23時 / 天文館"
    assert call["config"].output_dimensionality == EMBEDDING_DIMENSIONS


def test_dimension_mismatch_raises_provider_error():
    """768以外の応答は失敗扱い(LLMProviderError — design §2.6)。"""
    provider, _ = _provider(values=[0.1] * 767)
    with pytest.raises(LLMProviderError):
        await provider.embed("text")


def test_sdk_exception_passes_through():
    """SDK例外はこの層で握らない(Gatewayのwrapと送信記録が最終関門)。"""
    provider, _ = _provider(error=RuntimeError("sdk boom"))
    with pytest.raises(RuntimeError):
        await provider.embed("text")


def test_empty_api_key_fails_fast():
    with pytest.raises(ValueError):
        GeminiEmbeddingProvider(api_key="")


def test_name_is_google_for_send_record():
    """name='google'(08 §3送信記録の送信先・design §2.6)。"""
    provider, _ = _provider()
    assert provider.name == "google"
```

※ `_http_options()` の属性名(`timeout`/`retry_options.attempts`)はgoogle-genaiの現行型定義どおり。Task 1で導入したSDKバージョンで属性名が異なる場合は、公式ドキュメント(GenerateContent用HttpOptionsと共通)を確認して実装を合わせ、**報告ファイルdesign §5-1へ乖離を記録する**。

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_llm_gemini.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.llm.gemini'`)

- [ ] **Step 3: gemini.py を実装する**

`backend/src/latch/llm/gemini.py`:

```python
"""Embedding系統の実プロバイダ(Google Gemini API・gemini-embedding-001。T1 v0.2・07 §3)。

design §2.6: google-genai(Python公式統一SDK)・明示api_key(SDKが環境変数
GEMINI_API_KEY/GOOGLE_API_KEYを自動採用するのを排除 — llm_anthropic_base_url
と同じ規律)・HttpRetryOptions(attempts=1)でSDK既定の再試行を無効化
(「再試行なし」07 §1の必須条件)・output_dimensionality=768(パラメータ指定)。
次元不一致はLLMProviderError(=失敗扱い・D-15経路)。SDK例外はこの層で
握らず素通り — Gatewayの既存wrap(LLMTimeoutError/LLMProviderError)と
送信記録が最終関門(anthropic.pyと同じ)。
"""

from __future__ import annotations

from latch.llm.errors import LLMProviderError
from latch.llm.providers import EMBEDDING_DIMENSIONS, EmbeddingProvider

GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"  # T1 v0.2 §2.4(768はパラメータ指定)
# Gateway TIMEOUT_EMBEDDING_Sと同値(design §2.6: SDK側で過剰に待つ時間を作らない)。
# 循環import回避のため値を自前定義し、同値性はunit試験が強制する
GEMINI_EMBEDDING_TIMEOUT_S = 2.0
GEMINI_HTTP_TIMEOUT_MS = int(GEMINI_EMBEDDING_TIMEOUT_S * 1000)


def _http_options():
    """SDKのHTTP構成(timeout=2000ms・再試行1回のみ)。"""
    from google.genai import types

    return types.HttpOptions(
        timeout=GEMINI_HTTP_TIMEOUT_MS,
        retry_options=types.HttpRetryOptions(attempts=1),  # 再試行なし(07 §1)
    )


def _embed_config():
    from google.genai import types

    return types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS)


class GeminiEmbeddingProvider(EmbeddingProvider):
    """Embedding系統の実プロバイダ(07 §3・T1 v0.2)。name="google"(08 §3)。"""

    def __init__(self, *, api_key: str, client: object | None = None) -> None:
        if not api_key:
            # fail-fast: 鍵の不在を静かに握りつぶさない(design §2.8-B)
            raise ValueError("GeminiEmbeddingProvider requires api_key")
        self.name = "google"
        if client is not None:
            self._client = client
        else:
            self._client = _make_client(api_key)  # モジュール末尾定義(実行時解決)

    async def embed(self, text: str) -> list[float]:
        """07 §3。768次元(パラメータ指定)。次元不一致はLLMProviderError。"""
        response = await self._client.aio.models.embed_content(
            model=GEMINI_EMBEDDING_MODEL,
            contents=text,
            config=_embed_config(),
        )
        values = list(response.embeddings[0].values)
        if len(values) != EMBEDDING_DIMENSIONS:
            raise LLMProviderError(
                f"embedding dimension mismatch: {len(values)} != {EMBEDDING_DIMENSIONS}"
            )
        return values


def _make_client(api_key: str):
    """genai.Client構築(明示api_key — 環境変数の暗黙採用を排除・design §2.6)。"""
    from google import genai

    return genai.Client(api_key=api_key, http_options=_http_options())
```

※ `_make_client` は `_http_options` を使うため `_http_options` より後に定義する(上の形)。`__init__` 内の局所importは単に前方参照回避。モジュール末尾定義で問題ない(Pythonは実行時解決)。

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_llm_gemini.py -v`
Expected: PASS(7件)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/llm/gemini.py backend/tests/unit/test_llm_gemini.py
git commit -m "feat: GeminiEmbeddingProvider(明示api_key・retry無効化・768検証)"
```

### Task 5: build_embedding_gateway(gateway.py 追記・export)

**Files:**
- Modify: `backend/src/latch/llm/gateway.py`(ファイル末尾へ追記のみ)
- Modify: `backend/src/latch/llm/__init__.py`(export追記)
- Test: `backend/tests/unit/test_llm_gemini.py`(追記)

**Interfaces:**
- Consumes: `GeminiEmbeddingProvider`・`GEMINI_EMBEDDING_MODEL`(Task 4)・`LLMGateway`・`StubLLM`・`Settings.llm_mode`/`llm_gemini_api_key`/`llm_stub_delay_*`(既存)
- Produces(Task 8のWorker構築・Task 9のスモークが使用): `build_embedding_gateway(clock: Clock, settings: Settings) -> LLMGateway` — llm_mode="stub"→3系統stub / llm_mode="real"→**embeddingのみGemini実**(parser・jevはstub)/ real+鍵なし→ValueError(fail-fast)。**`build_llm_gateway` は無変更**(design §2.8-B)

- [ ] **Step 1: 失敗するテストを書く(追記)**

`backend/tests/unit/test_llm_gemini.py` へ追記。**import文はファイル先頭のimport節へ追記する**(E402回避・実行時未使用importはruff F401で落ちるため、`pytest` のみStep 1の時点で使用):

import節へ追記:

```python
from datetime import UTC, datetime

from latch.core.clock import FakeClock
from latch.settings import Settings
```

ファイル末尾へ追記:

```python
# -- build_embedding_gateway(design §2.8-B・§4.1 Gateway構成)--


def _clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC))


def test_build_embedding_gateway_stub_mode_all_stub():
    from latch.llm import StubLLM, build_embedding_gateway

    gw = build_embedding_gateway(_clock(), Settings())
    assert isinstance(gw._embedding, StubLLM)
    assert isinstance(gw._parser, StubLLM)
    assert isinstance(gw._jev, StubLLM)


def test_build_embedding_gateway_real_embeds_only():
    from latch.llm import StubLLM, build_embedding_gateway
    from latch.llm.gemini import GeminiEmbeddingProvider

    settings = Settings(llm_mode="real", llm_gemini_api_key="gk-test")
    gw = build_embedding_gateway(_clock(), settings)
    assert isinstance(gw._embedding, GeminiEmbeddingProvider)
    assert isinstance(gw._parser, StubLLM)  # parser/jevはstub継続(design §2.8-B)
    assert isinstance(gw._jev, StubLLM)


def test_build_embedding_gateway_real_without_key_fails_fast():
    from latch.llm import build_embedding_gateway

    settings = Settings(llm_mode="real", llm_gemini_api_key="")
    with pytest.raises(ValueError):
        build_embedding_gateway(_clock(), settings)
```

※ private参照(`gw._embedding` 等)は構成ピン目的の実装詳細検査(ruffの選択ルールにSLなし。M0以降のgateway試験と同じ手法でよい)。

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_llm_gemini.py -v`
Expected: FAIL(`ImportError: cannot import name 'build_embedding_gateway'`)

- [ ] **Step 3: gateway.py へ追記する**

`backend/src/latch/llm/gateway.py` へ:(1) import節へ1行追加:

```python
from latch.llm.gemini import GeminiEmbeddingProvider
```

(2) ファイル末尾(`build_llm_gateway` の後)へ追記:

```python
def build_embedding_gateway(clock: Clock, settings: Settings) -> LLMGateway:
    """Worker・スモーク用のGateway構築(M2 ws-2・design §2.8-B)。

    llm_mode="stub": 3系統すべてStubLLM(ci環境・unit/integration試験)。
    llm_mode="real": Embedding系統のみGeminiEmbeddingProvider実API
    (parser・jevはstub継続 — APIプロセスのbuild_llm_gateway契約は無変更)。
    gemini鍵の欠落はfail-fast(静かにスタブへ落ちない)。Jev系統のreal化は
    ws-5が同じ形で追加する。
    """
    stub = StubLLM(
        delay_parser_ms=settings.llm_stub_delay_parser_ms,
        delay_embedding_ms=settings.llm_stub_delay_embedding_ms,
        delay_jev_ms=settings.llm_stub_delay_jev_ms,
    )
    if settings.llm_mode == "stub":
        return LLMGateway(clock=clock, parser=stub, embedding=stub, jev=stub)
    if settings.llm_mode == "real":
        if not settings.llm_gemini_api_key:
            raise ValueError(
                "llm_mode='real' requires llm_gemini_api_key (embedding gateway)"
            )
        embedding = GeminiEmbeddingProvider(api_key=settings.llm_gemini_api_key)
        return LLMGateway(clock=clock, parser=stub, embedding=embedding, jev=stub)
    raise ValueError(f"unknown llm_mode: {settings.llm_mode!r} ('stub' or 'real')")
```

- [ ] **Step 4: `llm/__init__.py` へexport追記**

`backend/src/latch/llm/__init__.py` の `from latch.llm.gateway import (...)` へ `build_embedding_gateway` を追加・`from latch.llm.gemini import GeminiEmbeddingProvider` を追加・`__all__` へ両者を追加(アルファベット順の位置へ):

```python
from latch.llm.gemini import GeminiEmbeddingProvider
```

`__all__` 追加項目: `"GeminiEmbeddingProvider"`(StubLLMの前・大文字小文字を含めソート順の位置へ)と `"build_embedding_gateway"`(`build_llm_gateway` の前)。

- [ ] **Step 5: テストが通ること・既存試験が壊れないことを確認する**

Run: `cd backend && uv run pytest tests/unit/test_llm_gemini.py -v && make lint && make test`
Expected: PASS(7+3件)+ ともにexit 0(`build_llm_gateway` は無変更のため既存gateway試験に影響なし)

- [ ] **Step 6: Commit**

```bash
git add backend/src/latch/llm/gateway.py backend/src/latch/llm/__init__.py backend/tests/unit/test_llm_gemini.py
git commit -m "feat: build_embedding_gateway(Embedding系統のみreal化・鍵fail-fast)"
```

### Task 6: EmbeddingWorker.handle(embedding.py・イベント定数)

**Files:**
- Create: `backend/src/latch/worker/embedding.py`
- Modify: `backend/src/latch/intents/events.py`(定数1行追記のみ)
- Test: `backend/tests/unit/test_worker_embedding.py`

**Interfaces:**
- Consumes: `build_embedding_text`/`EmbeddingTextInput`(Task 2)・`LLMGateway.embed_intent` 签名(`async embed_intent(*, text: str, intent_id: str) -> list[float]`・duck typingでスタブ差し替え可)・`EVENT_*` 定数(intents/events.py)・`LLMError`(llm/errors)・`Clock`・`EventBus.publish_match_event`・`EMBEDDING_DIMENSIONS`
- Produces(Task 7のBackfillRunner・Task 8のWorker配線・Task 10のintegrationが使用):
  - `intents/events.py`: `EVENT_EMBEDDING_COMPLETED = "embedding_completed"`(ws-1報告書どおり本単位スコープの追記)
  - `EmbeddingWorker(*, engine: AsyncEngine, clock: Clock, gateway, bus: EventBus, model_id: str = GEMINI_EMBEDDING_MODEL)`
  - `async handle(intent_id: uuid.UUID, version: int) -> None` — 2フェーズ(design §2.2):
    - フェーズ1(SELECT 1本・トランザクション): 行なし / version不一致 / status ∉ {active, paused} → no-op return。has_embedding=true → embedding_completed行INSERT(ON CONFLICT DO NOTHING)のみ+コミット後publish(直接投入・確定値#6)
    - フェーズ2(トランザクション外でAPI): text生成 → `gateway.embed_intent` → LLMErrorは握ってログWARNING+return(バックフィル対象)・次元≠768はERRORログ+return → ガード付きUPDATE(`WHERE id AND version=:version AND embedding IS NULL`)+embedding_completed行INSERTの1トランザクション → UPDATE 0行はno-op → コミット後publish(失敗は握る・リレー回収)
    - SQLAlchemyErrorは握らず伝播(呼び出し側のackなし再配信が回収 — design §2.1-B)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_embedding.py` を作成:

```python
"""EmbeddingWorker.handleのunit試験(design §2.2・§4.1)。

スタブconn+記録Gateway+スタブbusで決定的に検証する: 2フェーズの順序・
ガード付きUPDATE・冪等(resume直接投入)・失敗経路の握り(D-15)・
SELECTがraw_textを含まないこと(確定値#10)。Worker配線はTask 8で追記。
"""

import json
import uuid
from datetime import UTC, datetime

from latch.core.clock import FakeClock
from latch.intents.events import EVENT_EMBEDDING_COMPLETED
from latch.llm.errors import LLMProviderError, LLMTimeoutError
from latch.llm.providers import EMBEDDING_DIMENSIONS
from latch.worker.embedding import EmbeddingWorker

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
IID = uuid.UUID("00000000-0000-4000-8000-0000000000ee")
# JST 2026-09-28 20:00-23:00(月曜)= 07 §3の例 → 期待テキストは全文ピン
T_START = datetime(2026, 9, 28, 11, 0, 0, tzinfo=UTC)
T_END = datetime(2026, 9, 28, 14, 0, 0, tzinfo=UTC)
EXPECTED_TEXT = "drinking / 平日夜20-23時 / 天文館 / 2-4人 / 静かなお店で"


class FakeResult:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row


class ScriptedConn:
    def __init__(self, results: list):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = list(results)

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return FakeResult(None, 0)


class ScriptedEngine:
    def __init__(self, results: list):
        self.conn = ScriptedConn(results)
        self.begins = 0

    def begin(self):
        self.begins += 1
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class StubGateway:
    """embed_intentの記録スタブ(失敗・次元不正を注入可)。"""

    def __init__(self, *, vec=None, error=None):
        self.calls: list[dict] = []
        self._vec = vec if vec is not None else [0.5] * EMBEDDING_DIMENSIONS
        self._error = error

    async def embed_intent(self, *, text: str, intent_id: str) -> list[float]:
        self.calls.append({"text": text, "intent_id": intent_id})
        if self._error is not None:
            raise self._error
        return list(self._vec)


class StubBus:
    def __init__(self, fail=False):
        self.published: list[tuple] = []
        self.fail = fail

    async def publish_match_event(self, *, event_type, intent_id, version):
        if self.fail:
            raise RuntimeError("pubsub down")
        self.published.append((event_type, intent_id, version))


def _phase1_row(*, version=1, status="active", has_embedding=False):
    """フェーズ1SELECTの結果行(列順は実装のSELECTどおり)。"""
    structured = {
        "location_name": "天文館",
        "soft_constraints": [{"text": "静かなお店で", "downgraded_from_ng": False}],
    }
    return (
        version,
        status,
        has_embedding,
        "drinking",
        json.dumps(structured),  # asyncpgのjsonbはstrで返る場合がある(store.py規律)
        2,
        4,
        T_START,
        T_END,
    )


def _worker(engine, gateway=None, bus=None) -> EmbeddingWorker:
    return EmbeddingWorker(
        engine=engine,
        clock=FakeClock(NOW),
        gateway=gateway if gateway is not None else StubGateway(),
        bus=bus if bus is not None else StubBus(),
    )


def _sql(conn: ScriptedConn, i: int) -> str:
    return conn.calls[i][0]


async def test_handle_embeds_and_emits_in_order():
    """NULL→フェーズ2: SELECT→embed(正規化text)→ガード付きUPDATE→行INSERT→publish。"""
    engine = ScriptedEngine(
        [
            FakeResult(_phase1_row()),   # フェーズ1 SELECT
            FakeResult((str(IID),)),     # UPDATE RETURNING id(1行)
            FakeResult(None, 0),         # embedding_completed INSERT
        ]
    )
    gateway = StubGateway()
    bus = StubBus()
    await _worker(engine, gateway, bus).handle(IID, 1)
    assert gateway.calls == [{"text": EXPECTED_TEXT, "intent_id": str(IID)}]
    assert "raw_text" not in _sql(engine.conn, 0)  # SELECT列のピン(確定値#10)
    upd_sql, upd_params = engine.conn.calls[1]
    assert "embedding IS NULL" in upd_sql and "version = :version" in upd_sql
    assert "CAST(:vec AS vector)" in upd_sql  # 明示CAST(M1 ws-3事故の回帰予防)
    assert upd_params["model"] == "gemini-embedding-001"
    ins_sql, ins_params = engine.conn.calls[2]
    assert "ON CONFLICT DO NOTHING" in ins_sql
    assert ins_params["event_type"] == EVENT_EMBEDDING_COMPLETED
    assert json.loads(ins_params["payload"]) == {"version": 1}
    assert bus.published == [(EVENT_EMBEDDING_COMPLETED, IID, 1)]


async def test_handle_existing_embedding_directly_emits():
    """embedding既存(resume等)→Gateway不呼び出し+行INSERT+publish(確定値#6)。"""
    engine = ScriptedEngine(
        [
            FakeResult(_phase1_row(has_embedding=True)),
            FakeResult(None, 0),  # INSERTのみ(UPDATEなし)
        ]
    )
    gateway = StubGateway()
    bus = StubBus()
    await _worker(engine, gateway, bus).handle(IID, 1)
    assert gateway.calls == []  # 再エンベディングなし(テキスト不変)
    assert len(engine.conn.calls) == 2
    assert "ON CONFLICT DO NOTHING" in _sql(engine.conn, 1)
    assert bus.published == [(EVENT_EMBEDDING_COMPLETED, IID, 1)]


async def test_handle_missing_intent_is_noop():
    """行なし→no-op(参照先不在は正当な遅延Event — 06 §9と同じ扱い)。"""
    engine = ScriptedEngine([FakeResult(None)])
    gateway = StubGateway()
    bus = StubBus()
    await _worker(engine, gateway, bus).handle(IID, 1)
    assert len(engine.conn.calls) == 1  # SELECTのみ
    assert gateway.calls == [] and bus.published == []


async def test_handle_version_mismatch_is_noop():
    """DB version ≠ 引数version → no-op(新versionのEventが担当)。"""
    engine = ScriptedEngine([FakeResult(_phase1_row(version=2))])
    gateway = StubGateway()
    await _worker(engine, gateway, bus=StubBus()).handle(IID, 1)
    assert len(engine.conn.calls) == 1
    assert gateway.calls == []


async def test_handle_status_out_of_target_is_noop():
    """status ∉ {active, paused}(expired等)→ no-op(Layer 1〜2の対象外)。"""
    engine = ScriptedEngine([FakeResult(_phase1_row(status="cancelled"))])
    gateway = StubGateway()
    await _worker(engine, gateway, bus=StubBus()).handle(IID, 1)
    assert len(engine.conn.calls) == 1
    assert gateway.calls == []


async def test_handle_llm_errors_swallowed_no_write():
    """LLMTimeoutError/LLMProviderError → 例外なし・書き込みなし・イベント行なし(D-15)。"""
    for error in (LLMTimeoutError("t"), LLMProviderError("p")):
        engine = ScriptedEngine([FakeResult(_phase1_row())])
        gateway = StubGateway(error=error)
        bus = StubBus()
        await _worker(engine, gateway, bus).handle(IID, 1)  # 例外が出ないこと
        assert len(engine.conn.calls) == 1, error
        assert bus.published == [], error


async def test_handle_wrong_dimension_treated_as_failure():
    """768以外の応答 → 失敗扱い(書き込みなし・publishなし・design §2.2)。"""
    engine = ScriptedEngine([FakeResult(_phase1_row())])
    gateway = StubGateway(vec=[0.1] * 767)
    bus = StubBus()
    await _worker(engine, gateway, bus).handle(IID, 1)
    assert len(engine.conn.calls) == 1
    assert bus.published == []


async def test_handle_update_zero_rows_no_event():
    """ガード付きUPDATE 0行(並行handleが先に書いた)→イベント行もpublishもなし。"""
    engine = ScriptedEngine(
        [
            FakeResult(_phase1_row()),
            FakeResult(None, 0),  # UPDATE 0行
        ]
    )
    gateway = StubGateway()
    bus = StubBus()
    await _worker(engine, gateway, bus).handle(IID, 1)
    assert len(engine.conn.calls) == 2  # INSERTしていない
    assert bus.published == []


async def test_handle_publish_failure_swallowed():
    """publish失敗は握る(行はpending=フォールバックリレーが30秒後に回収)。"""
    engine = ScriptedEngine(
        [
            FakeResult(_phase1_row(has_embedding=True)),
            FakeResult(None, 0),
        ]
    )
    await _worker(engine, bus=StubBus(fail=True)).handle(IID, 1)  # 例外が出ないこと


def test_select_does_not_read_raw_text():
    """フェーズ1のSELECT定数そのものにraw_textが含まれない(確定値#10のSQLピン)。"""
    from latch.worker.embedding import _SELECT_INTENT_FOR_EMBEDDING

    sql = str(_SELECT_INTENT_FOR_EMBEDDING)
    assert "raw_text" not in sql
    assert "embedding IS NOT NULL" in sql
    assert "time_end" in sql  # 正規化テキスト導出に必要な列は選択している
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_embedding.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.embedding'`)

- [ ] **Step 3: intents/events.py へ定数を追記する**

`backend/src/latch/intents/events.py` の定数群(`EVENT_SCHEDULED` の後)へ1行追記:

```python
EVENT_EMBEDDING_COMPLETED = "embedding_completed"  # 発行経路はM2 ws-2(Embedding Worker)
```

- [ ] **Step 4: embedding.py を実装する**

`backend/src/latch/worker/embedding.py`:

```python
"""Embedding Worker本体(M2 ws-2・design §2.2)。

Stage1コミット後・ack前にWorkerから呼ばれる(配置はdesign §2.1-B・フック不使用)。
2フェーズ(短トランザクションread → API呼び出しはトランザクション外 →
ガード付きUPDATE+embedding_completed行INSERTの1トランザクション)で外部API
呼び出し(最大2秒)をDBトランザクションの外へ出す。正しさはUPDATEのガード
(version一致+embedding IS NULL)が担保し、at-least-onceの重複API呼び出しは
許容する(design §2.2のトレードオフ・コスト影響は無視できる)。
LLM失敗は握ってembedding NULLのままreturn=バックフィル対象(06 D-15)。
DB失敗は例外を伝播し、呼び出し側(Worker._dispatch)のackなし再配信が回収する。
SQLはtext()生SQL・時刻はClock明示値(ws-1と同じ規律)。SELECTにraw_textは
含めない(確定値#10)。
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.events.bus import EventBus
from latch.intents.events import EVENT_EMBEDDING_COMPLETED
from latch.llm.errors import LLMError
from latch.llm.gemini import GEMINI_EMBEDDING_MODEL
from latch.llm.providers import EMBEDDING_DIMENSIONS
from latch.worker.embedding_text import EmbeddingTextInput, build_embedding_text

logger = logging.getLogger(__name__)

# Layer 1〜2の対象はactive(06 §3)。pausedはresume時のキック(IS NULL判定)で回収
_STATUS_TARGETS = frozenset({"active", "paused"})

_SELECT_INTENT_FOR_EMBEDDING = text("""
    SELECT version, status, (embedding IS NOT NULL) AS has_embedding,
           category_primary, structured_data, participants_min, participants_max,
           time_start, time_end
    FROM intents WHERE id = :intent_id
""")
# ガード: フェーズ1で読んだ内容が現行のままであること(version) + 二重書き込み排除
# (embedding IS NULL)。CAST(:vec AS vector)の明示CASTはbind param直後の'::'短縮
# CASTがSQLAlchemyに認識されない事故(M1 ws-3)の回帰予防
_UPDATE_EMBEDDING = text("""
    UPDATE intents
    SET embedding = CAST(:vec AS vector), embedding_model = :model,
        updated_at = :now
    WHERE id = :intent_id AND version = :version AND embedding IS NULL
    RETURNING id
""")
# payloadは{"version": N}のみ(UNIQUE索引が参照するpayload->>'version'と同じ形式)
_INSERT_EMBEDDING_COMPLETED = text("""
    INSERT INTO match_events
        (event_type, source_intent_id, payload, status, created_at)
    VALUES (:event_type, :source_intent_id, CAST(:payload AS jsonb), 'pending',
            :created_at)
    ON CONFLICT DO NOTHING
""")


class EmbeddingWorker:
    """Embedding実行とembedding_completed発行(design §2.2)。冪等(handle再実行可)。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        gateway,
        bus: EventBus,
        model_id: str = GEMINI_EMBEDDING_MODEL,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._gateway = gateway
        self._bus = bus
        self._model_id = model_id

    async def handle(self, intent_id: uuid.UUID, version: int) -> None:
        row = await self._read_target(intent_id)
        if row is None:
            return
        db_version, status, has_embedding = row[0], row[1], row[2]
        if db_version != version:
            return  # 旧Eventの焼き直し(新versionのEventが担当)
        if status not in _STATUS_TARGETS:
            return  # 削除済み等(Layer 1〜2の対象外)
        if has_embedding:
            # 既存(resume由来 — テキスト不変)は再エンベディングせず直接投入(06 §9)
            await self._emit_completed(intent_id, version)
            return
        await self._embed_and_emit(intent_id, version, row)

    # -- フェーズ1(短トランザクション・readのみ)--

    async def _read_target(self, intent_id: uuid.UUID) -> tuple | None:
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _SELECT_INTENT_FOR_EMBEDDING, {"intent_id": intent_id}
            )
            return res.first()

    # -- フェーズ2(API呼び出しはトランザクション外)--

    async def _embed_and_emit(
        self, intent_id: uuid.UUID, version: int, row: tuple
    ) -> None:
        _, _, _, category_primary, structured, pmin, pmax, t_start, t_end = row
        if isinstance(structured, str):  # asyncpgのjsonbがstrで返る場合(store.py規律)
            structured = json.loads(structured)
        inp = EmbeddingTextInput(
            category_primary=category_primary,
            structured_data=structured,
            participants_min=pmin,
            participants_max=pmax,
            time_start=t_start,
            time_end=t_end,
        )
        text = build_embedding_text(inp)
        try:
            vec = await self._gateway.embed_intent(
                text=text, intent_id=str(intent_id)
            )
        except LLMError as exc:
            # 再試行なし(07 §1)。embeddingはNULLのまま=バックフィル対象(06 D-15)
            logger.warning(
                "embedding failed (backfill target) intent_id=%s version=%s error=%s",
                intent_id,
                version,
                type(exc).__name__,
            )
            return
        if len(vec) != EMBEDDING_DIMENSIONS:
            logger.error(
                "embedding dimension mismatch intent_id=%s version=%s dim=%d",
                intent_id,
                version,
                len(vec),
            )
            return
        vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
        now = self._clock.now()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _UPDATE_EMBEDDING,
                {
                    "intent_id": intent_id,
                    "version": version,
                    "vec": vec_literal,
                    "model": self._model_id,
                    "now": now,
                },
            )
            if res.first() is None:
                return  # 並行handleが先に書いた/更新が入った(ガードが排除)
            await self._insert_completed(conn, intent_id, version, now)
        await self._publish(intent_id, version)

    async def _emit_completed(
        self, intent_id: uuid.UUID, version: int
    ) -> None:
        now = self._clock.now()
        async with self._engine.begin() as conn:
            await self._insert_completed(conn, intent_id, version, now)
        await self._publish(intent_id, version)

    async def _insert_completed(
        self, conn, intent_id: uuid.UUID, version: int, now
    ) -> None:
        await conn.execute(
            _INSERT_EMBEDDING_COMPLETED,
            {
                "event_type": EVENT_EMBEDDING_COMPLETED,
                "source_intent_id": intent_id,
                "payload": json.dumps({"version": version}),
                "created_at": now,
            },
        )

    async def _publish(self, intent_id: uuid.UUID, version: int) -> None:
        """コミット後publish。失敗は握る(行はpending=リレーが30秒後に回収)。"""
        try:
            await self._bus.publish_match_event(
                event_type=EVENT_EMBEDDING_COMPLETED,
                intent_id=intent_id,
                version=version,
            )
        except Exception:
            logger.warning(
                "embedding_completed publish failed intent_id=%s version=%s",
                intent_id,
                version,
            )
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_embedding.py -v`
Expected: PASS(11件)

- [ ] **Step 6: Commit**

```bash
git add backend/src/latch/intents/events.py backend/src/latch/worker/embedding.py backend/tests/unit/test_worker_embedding.py
git commit -m "feat: EmbeddingWorker(2フェーズ・ガード付きUPDATE・embedding_completed発行)"
```

### Task 7: BackfillRunner(backfill.py)

**Files:**
- Create: `backend/src/latch/worker/backfill.py`
- Test: `backend/tests/unit/test_worker_backfill.py`

**Interfaces:**
- Consumes: `EmbeddingWorker.handle`(Task 6)・`AsyncEngine`・`Settings.embedding_backfill_*`(Task 1)
- Produces(Task 8のWorker構築・Task 10のintegrationが使用):
  - `BackfillRunner(*, engine: AsyncEngine, embedding: EmbeddingWorker, interval_sec: float, batch_limit: int, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep)`
  - `async run_once() -> int` — 対象抽出 `WHERE embedding IS NULL AND status = 'active' ORDER BY updated_at LIMIT :batch_limit` → 各行を `embedding.handle(id, version)` へ委譲(項目単位の例外は握って継続)。戻り=例外なく終了した件数
  - `async run(*, stop: asyncio.Event | None = None) -> None` — **sleep-first周期ループ**(初回は周期待ちから・design §2.5)。待機は `asyncio.wait_for(stop.wait(), timeout=interval)` でshutdown即応(300秒の待機中でも)。run_once全体の失敗も握って次周期へ

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_backfill.py` を作成:

```python
"""BackfillRunnerのunit試験(design §2.5・§4.1)。

対象抽出SQL(embedding IS NULL AND status='active'のみ)・handleへの委譲・
項目単位の例外握りと継続・sleep-firstのstop応答ループを検証する。
ws-3がfixture直入れするembedding(embedding_model NULL)を誤って
再エンベディングしないための防線(design §1.4-2)。
"""

import asyncio
import uuid

from latch.worker.backfill import BackfillRunner

IID1 = uuid.UUID("00000000-0000-4000-8000-0000000000f1")
IID2 = uuid.UUID("00000000-0000-4000-8000-0000000000f2")


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class ScriptedConn:
    def __init__(self, result):
        self.result = result
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        return self.result


class ScriptedEngine:
    def __init__(self, result):
        self.conn = ScriptedConn(result)

    def begin(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class RecordingEmbedding:
    """handleの記録スタブ(指定intentで例外を注入可)。"""

    def __init__(self, fail_on=None):
        self.calls: list[tuple[uuid.UUID, int]] = []
        self.fail_on = fail_on or set()

    async def handle(self, intent_id, version):
        self.calls.append((intent_id, version))
        if intent_id in self.fail_on:
            raise RuntimeError("db down")


def _runner(engine, embedding, *, interval_sec=300.0, batch_limit=50):
    return BackfillRunner(
        engine=engine, embedding=embedding,
        interval_sec=interval_sec, batch_limit=batch_limit,
    )


async def test_run_once_selects_targets_sql_pin():
    """対象抽出はembedding IS NULL AND status='active'のみ・ORDER BY updated_at・LIMIT。"""
    engine = ScriptedEngine(FakeResult([(IID1, 3), (IID2, 5)]))
    embedding = RecordingEmbedding()
    done = await _runner(engine, embedding, batch_limit=50).run_once()
    sql, params = engine.conn.calls[0]
    assert "embedding IS NULL" in sql
    assert "status = 'active'" in sql
    assert "ORDER BY updated_at" in sql
    assert params == {"limit": 50}
    assert embedding.calls == [(IID1, 3), (IID2, 5)]
    assert done == 2


async def test_run_once_swallows_item_failure_and_continues():
    """項目単位の例外は握って次の対象へ(1件失敗しても残りを処理)。"""
    engine = ScriptedEngine(FakeResult([(IID1, 1), (IID2, 2)]))
    embedding = RecordingEmbedding(fail_on={IID1})
    done = await _runner(engine, embedding).run_once()  # 例外が外へ出ないこと
    assert embedding.calls == [(IID1, 1), (IID2, 2)]
    assert done == 1


async def test_run_sleep_first_immediate_stop_runs_nothing():
    """stop済みなら待機もrun_onceもしない(sleep-first・実時間待ちなし)。"""
    engine = ScriptedEngine(FakeResult([]))
    embedding = RecordingEmbedding()
    runner = _runner(engine, embedding, interval_sec=300.0)
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(runner.run(stop=stop), timeout=1.0)
    assert embedding.calls == []
    assert engine.conn.calls == []


async def test_run_waits_interval_before_first_cycle():
    """初回は周期待ちから(起動直後のバースト回避・design §2.5)。"""
    engine = ScriptedEngine(FakeResult([(IID1, 1)]))
    embedding = RecordingEmbedding()
    runner = _runner(engine, embedding, interval_sec=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop=stop))
    # 即座にはrun_onceしていないことを確認(短intervalでも待機が先)
    await asyncio.sleep(0)
    assert embedding.calls == []
    for _ in range(200):  # 周期(0.01秒)の到来を待つ(実時間・軽微)
        if embedding.calls:
            break
        await asyncio.sleep(0.01)
    assert embedding.calls == [(IID1, 1)]
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_backfill.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.backfill'`)

- [ ] **Step 3: backfill.py を実装する**

`backend/src/latch/worker/backfill.py`:

```python
"""バックフィル周期タスク(06 D-15・design §2.5)。

Worker内の周期タスク: embedding IS NULL かつ status='active' のIntentを
抽出してEmbeddingWorker.handleへ委譲する。プロバイダ障害中は失敗がNULLの
まま残り、次周期で自然に再試行される=「回復後の一括再エンベディング」の
自動達成。対象をembedding IS NULLのみに絞るのは、ws-3がfixture直入れする
embedding(embedding_model NULL)を誤って再エンベディングしないための防線
でもある(design §1.4-2)。初回は周期待ちから開始(起動直後のバースト回避・
ws-3統合試験への干渉防止)。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_SELECT_BACKFILL_TARGETS = text("""
    SELECT id, version FROM intents
    WHERE embedding IS NULL AND status = 'active'
    ORDER BY updated_at
    LIMIT :limit
""")


def _coerce_uuid(value: object) -> uuid.UUID:
    """asyncpgはuuid列をUUIDインスタンスで返す(M0 ws-3と同じ対策)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


class BackfillRunner:
    """周期ごとに失敗Intent(embedding NULL・active)をhandleへ渡す(design §2.5)。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        embedding,
        interval_sec: float,
        batch_limit: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._embedding = embedding
        self._interval_sec = interval_sec
        self._batch_limit = batch_limit
        self._sleep = sleep

    async def run_once(self) -> int:
        """1周期分: 対象抽出→handle委譲(項目単位の例外は握って継続)。"""
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _SELECT_BACKFILL_TARGETS, {"limit": self._batch_limit}
            )
            rows = res.fetchall()
        done = 0
        for row_id, version in rows:
            intent_id = _coerce_uuid(row_id)
            try:
                await self._embedding.handle(intent_id, version)
                done += 1
            except Exception:
                logger.warning(
                    "backfill handle failed intent_id=%s version=%s",
                    intent_id,
                    version,
                )
        return done

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """sleep-first周期ループ。待機中のstopで周期処理を挟まず終了(design §2.5)。"""
        while stop is None or not stop.is_set():
            await self._wait_interval(stop)
            if stop is not None and stop.is_set():
                break
            try:
                await self.run_once()
            except Exception:
                logger.warning("backfill run_once failed", exc_info=True)

    async def _wait_interval(self, stop: asyncio.Event | None) -> None:
        if stop is None:
            await self._sleep(self._interval_sec)
            return
        try:
            # 300秒の待機中でもshutdownに追従(design §2.5)
            await asyncio.wait_for(stop.wait(), timeout=self._interval_sec)
        except TimeoutError:
            pass
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_backfill.py -v`
Expected: PASS(4件)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/worker/backfill.py backend/tests/unit/test_worker_backfill.py
git commit -m "feat: BackfillRunner(embedding NULL・active対象の周期再エンベディング)"
```

### Task 8: Worker配線(worker/main.py・競合回避規律厳守)

**Files:**
- Modify: `backend/src/latch/worker/main.py`(§0競合回避1の規律: 引数末尾追記・run()後ろ追記・_dispatch/_on_releaseへの純挿入)
- Test: `backend/tests/unit/test_worker_embedding.py`(配線試験を追記)

**Interfaces:**
- Consumes: `EmbeddingWorker`・`BackfillRunner`(Task 6・7)・`build_embedding_gateway`(Task 5)・`EVENT_CREATED`/`EVENT_UPDATED`(intents/events.py)・Worker既存の `bus`/`engine`/`stage1`/`debouncer`/`sleep` 引数(無変更)
- Produces:
  - `Worker(..., sleep=asyncio.sleep, embedding: EmbeddingWorker | None = None, backfill: BackfillRunner | None = None)` — 引数並びの**末尾に追加**(sleepの後ろ・default付き)
  - run()内: embedding未注入なら `EmbeddingWorker(engine=engine, clock=self.clock, gateway=build_embedding_gateway(self.clock, self.settings), bus=bus)` を構築 → backfill未注入なら `BackfillRunner(engine=engine, embedding=embedding, interval_sec=settings.embedding_backfill_interval_sec, batch_limit=settings.embedding_backfill_batch_limit)` を構築 → `backfill_task = asyncio.create_task(backfill.run(stop=self._stop))`(debouncer_task起動の後ろ)→ shutdown時 `await backfill_task`(`await debouncer_task` の後ろ)
  - キック規約(`_kick_embedding`): event_type が created/updated のみ呼ぶ。`_embedding is None` なら何もしない(ws-1のtest_worker.py互換)。handleの例外(DB失敗)は素通りし、_dispatch/_on_releaseの既存exceptが受け、ackなし再配信となる
  - 挿入位置: `_dispatch` の `if result.kind in ("processed", "duplicate", "quarantined"):` ブロック内・`event.ack()` の前へ2行 / `_on_release` の `await self._stage1.process(...)` の後・`group.latest.event.ack()` の前に1行

- [ ] **Step 1: 失敗するテストを書く(追記)**

`backend/tests/unit/test_worker_embedding.py` へ追記。**import文はファイル先頭のimport節へ追記する**(E402回避):

```python
import asyncio
from datetime import timedelta

from latch.events import IncomingEvent
from latch.worker.main import Worker
from latch.worker.stage1 import IntakeResult
```

ファイル末尾へ追記する試験コード本体(test_worker.py(ws-1資産)は触らない — §0競合回避4。embedding注入で配線を検証):

```python
# -- Worker配線(M2 ws-2・design §2.1-B・§4.1)--
# test_worker.py(ws-1資産)は触らない(§0競合回避4)。embedding注入で配線を検証。


class _FakeBus:
    def __init__(self):
        self.subscribers: list = []
        self.ensured = 0
        self.closed = 0

    async def ensure(self):
        self.ensured += 1

    async def publish_match_event(self, *, event_type, intent_id, version):
        raise AssertionError("worker配線試験ではpublishしない")

    async def subscribe(self, on_message):
        self.subscribers.append(on_message)
        return _FakeSubscription()

    async def close(self):
        self.closed += 1


class _FakeSubscription:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class _RecordingStage1:
    def __init__(self, kind: str):
        self.kind = kind
        self.processed: list[tuple] = []
        self.discarded: list[uuid.UUID] = []

    async def intake(self, event):
        return IntakeResult(kind=self.kind, triple=event.triple(), row_id=uuid.uuid4())

    async def process(self, event, triple, row_id):
        self.processed.append((triple, row_id))
        return "processed"

    async def discard(self, row_id, *, reason):
        self.discarded.append(row_id)


class _RecordingEmbedding:
    def __init__(self, *, error: Exception | None = None):
        self.calls: list[tuple[uuid.UUID, int]] = []
        self.error = error

    async def handle(self, intent_id, version):
        self.calls.append((intent_id, version))
        if self.error is not None:
            raise self.error


def _make_event(event_type: str, iid, version) -> IncomingEvent:
    acks: list[int] = []

    def _ack() -> None:
        acks.append(1)

    payload = (
        b'{"event_type": "%s", "source_intent_id": "%s", "version": %d}'
        % (event_type.encode(), str(iid).encode(), version)
    )
    event = IncomingEvent.from_payload(
        message_id=f"{event_type}-{version}", payload=payload, ack=_ack
    )
    event.ack_count = acks  # type: ignore[attr-defined]
    return event


async def _started_worker(fake_clock, stage1, embedding):
    worker = Worker(
        clock=fake_clock, bus=_FakeBus(), stage1=stage1, embedding=embedding
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.01)
    return worker, task


async def _stop(worker, task) -> None:
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=1.0)


async def test_dispatch_processed_created_kicks_embedding(fake_clock):
    """processed × created → handle(intent_id, version)をackの前に呼ぶ。"""
    embedding = _RecordingEmbedding()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker(fake_clock, stage1, embedding)
    try:
        iid = uuid.uuid4()
        event = _make_event("created", iid, 1)
        await worker._dispatch(event)
        assert embedding.calls == [(iid, 1)]
        assert event.ack_count == [1]
    finally:
        await _stop(worker, task)


async def test_dispatch_duplicate_created_kicks_embedding(fake_clock):
    """duplicate × created → handleを呼ぶ(実行中クラッシュの即時回収 — §2.1-B)。"""
    embedding = _RecordingEmbedding()
    stage1 = _RecordingStage1("duplicate")
    worker, task = await _started_worker(fake_clock, stage1, embedding)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("created", iid, 1))
        assert embedding.calls == [(iid, 1)]
    finally:
        await _stop(worker, task)


async def test_dispatch_quarantined_no_kick(fake_clock):
    """quarantined(毒ペイロード等)ではキックしない(§4.1配線表)。"""
    embedding = _RecordingEmbedding()
    stage1 = _RecordingStage1("quarantined")
    worker, task = await _started_worker(fake_clock, stage1, embedding)
    try:
        await worker._dispatch(_make_event("created", uuid.uuid4(), 1))
        assert embedding.calls == []
    finally:
        await _stop(worker, task)


async def test_dispatch_non_create_update_types_no_kick(fake_clock):
    """deleted/expired/scheduled/embedding_completedではキックしない。"""
    for event_type in ("deleted", "expired", "scheduled", "embedding_completed"):
        embedding = _RecordingEmbedding()
        stage1 = _RecordingStage1("processed")
        worker, task = await _started_worker(fake_clock, stage1, embedding)
        try:
            await worker._dispatch(_make_event(event_type, uuid.uuid4(), 1))
            assert embedding.calls == [], event_type
        finally:
            await _stop(worker, task)


async def test_on_release_kicks_latest_only(fake_clock):
    """debounce窓解放: latestの処理後にhandle 1回(absorbedでは呼ばない)。"""
    embedding = _RecordingEmbedding()
    stage1 = _RecordingStage1("debounce")
    worker, task = await _started_worker(fake_clock, stage1, embedding)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("updated", iid, 2))
        await worker._dispatch(_make_event("updated", iid, 3))
        fake_clock.advance(timedelta(seconds=10))
        await asyncio.sleep(0.2)  # debouncer tick(0.05秒)が解放を実行
        assert embedding.calls == [(iid, 3)]  # latestのみ
    finally:
        await _stop(worker, task)


async def test_kick_failure_skips_ack(fake_clock):
    """handleの例外(DB失敗)→ackしない=ack_deadline後に再配信が回収(§2.1-B)。"""
    embedding = _RecordingEmbedding(error=RuntimeError("db down"))
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker(fake_clock, stage1, embedding)
    try:
        event = _make_event("created", uuid.uuid4(), 1)
        await worker._dispatch(event)  # 既存exceptが握るため例外は外へ出ない
        assert event.ack_count == []  # ackされない
    finally:
        await _stop(worker, task)
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_embedding.py -v`
Expected: FAIL(追記6件が `TypeError: Worker.__init__() got an unexpected keyword argument 'embedding'` で失敗)

- [ ] **Step 3: worker/main.py へ追記する(§0競合回避1・既存行の変更・削除は一切しない)**

(1) import節へ追記(ws-1のimport群の後ろへ):

```python
from latch.intents.events import EVENT_CREATED, EVENT_UPDATED
from latch.llm.gateway import build_embedding_gateway
from latch.worker.backfill import BackfillRunner
from latch.worker.embedding import EmbeddingWorker
```

(2) `Worker.__init__` の引数並び**末尾**(`sleep: ... = asyncio.sleep,` の後)へ2引数を追記:

```python
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        embedding: EmbeddingWorker | None = None,
        backfill: BackfillRunner | None = None,
    ) -> None:
```

(3) `__init__` 内の代入部の末尾(`self._stop = asyncio.Event()` の前でよい・既存代入の後ろ)へ2行追記:

```python
        self._embedding = embedding
        self._backfill = backfill
```

(4) `run()` 内: `self._stage1 = stage1` / `self._debouncer = debouncer` の**後ろ**へ構築ブロックを追記:

```python
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
```

(5) `run()` 内: `debouncer_task = asyncio.create_task(...)` の**後ろ**へ1行追記:

```python
            backfill_task = asyncio.create_task(backfill.run(stop=self._stop))
```

(6) `run()` 内: `await debouncer_task` の**後ろ**へ1行追記:

```python
            await backfill_task
```

(7) `_dispatch` メソッド: `if result.kind in ("processed", "duplicate", "quarantined"):` ブロック内・`event.ack()` の**前**へ2行を純挿入(design §2.1-B。quarantinedはキックしない):

```python
            if result.kind in ("processed", "duplicate", "quarantined"):
                if (
                    result.kind in ("processed", "duplicate")
                    and result.triple is not None
                ):
                    await self._kick_embedding(*result.triple)
                event.ack()
                return
```

(8) `_on_release` メソッド: `await self._stage1.process(...)` 呼び出しの**後**・`group.latest.event.ack()` の**前**へ1行を純挿入:

```python
            await self._stage1.process(
                group.latest.event, group.latest.triple, group.latest.row_id
            )
            await self._kick_embedding(*group.latest.triple)
            group.latest.event.ack()
```

(9) クラス末尾(`_on_release` の後)へヘルパーメソッドを新規追記:

```python
    async def _kick_embedding(
        self, event_type: str, intent_id: uuid.UUID, version: int
    ) -> None:
        """Stage1処理コミット後・ack前のEmbeddingキック(design §2.1-B)。

        created/updatedのみ(06 §9「処理はEmbedding要求のキックまで」)。
        LLM失敗はhandle内で握られ(embedding NULL=バックフィル対象)、DB失敗は
        ここから伝播して_dispatch/_on_releaseの既存exceptが受け、ackなし
        再配信が回収する。embedding未注入(ws-1資産の試験)は何もしない。
        """
        if self._embedding is None or event_type not in (EVENT_CREATED, EVENT_UPDATED):
            return
        await self._embedding.handle(intent_id, version)
```

(10) `uuid` のimport確認: worker/main.py は現状 `uuid` をimportしていないため、型注釈用に `import uuid` をimport節へ追記する。

- [ ] **Step 4: テストが通ること・既存試験が壊れないことを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_embedding.py tests/unit/test_worker.py -v && make lint && make test`
Expected: PASS(11+6件 + ws-1のtest_worker.py 7件が**無変更で**緑 — embedding未注入のWorkerはrun()内でstub gateway構築となり、`_kick_embedding` のNoneガードで既存_dispatch挙動は不変)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/worker/main.py backend/tests/unit/test_worker_embedding.py
git commit -m "feat: WorkerへEmbeddingキックとバックフィル起動を配線(ack前にキック)"
```

### Task 9: embed_smoke.py・Makefile embed-smoke

**Files:**
- Create: `backend/src/latch/llm/embed_smoke.py`
- Modify: `Makefile`(ルート)

**Interfaces:**
- Consumes: `build_embedding_gateway`(Task 5)・`Settings`(Task 1の `llm_gemini_api_key`)
- Produces: `make embed-smoke` — `cd backend && uv run --env-file ../.env python -m latch.llm.embed_smoke`(g1-gateと同じ.env経路)。llm_mode≠realまたは鍵なしで**exit 1**(起動ガード)。real時は固定サンプルテキスト1件をエンベディングし768次元・レイテンシ・モデル名を出力。**agent3は実APIを叩く実行はしない**(design §4.3・§0。実行はスーパーバイザー)

- [ ] **Step 1: embed_smoke.py を実装する**

`backend/src/latch/llm/embed_smoke.py`:

```python
"""Embedding実APIスモーク(make embed-smoke・design §2.7)。

ws-6の401事故の教訓: 実APIを叩くまで分からない契約ズレの検出。1呼び出し
(約$0.000015・約100トークン)。make embed-smoke は uv run --env-file ../.env
経由で LATCH_LLM_MODE=real と LATCH_GEMINI_API_KEY を渡す(g1-gateと同じ経路)。
SendRecordも通常どおり出力される(Gateway経由 — 08 §3)。レイテンシ計測は
asyncioのloop.time(処理時間の測定であり時刻参照ではない — arch test対象外)。
"""

from __future__ import annotations

import asyncio

from latch.core.clock import SystemClock
from latch.llm.gateway import build_embedding_gateway
from latch.llm.gemini import GEMINI_EMBEDDING_MODEL
from latch.llm.providers import EMBEDDING_DIMENSIONS
from latch.settings import Settings

# 07 §3形式の固定サンプル(設計§2.4の例と同じ要素)
SAMPLE_TEXT = "drinking / 平日夜20-23時 / 天文館 / 2-4人 / 静かなお店で・予算は抑えめ"


async def main() -> int:
    settings = Settings()
    if settings.llm_mode != "real":
        print("[embed-smoke] FAIL: LATCH_LLM_MODE=real が必要(.env — make embed-smoke経由)")
        return 1
    if not settings.llm_gemini_api_key:
        print("[embed-smoke] FAIL: LATCH_GEMINI_API_KEY が未設定(.env)")
        return 1
    gateway = build_embedding_gateway(SystemClock(), settings)  # 鍵欠落はfail-fast
    loop = asyncio.get_running_loop()
    started = loop.time()
    vec = await gateway.embed_intent(text=SAMPLE_TEXT, intent_id="embed-smoke")
    elapsed_ms = (loop.time() - started) * 1000
    print(
        f"[embed-smoke] model={GEMINI_EMBEDDING_MODEL}"
        f" dim={len(vec)} latency_ms={elapsed_ms:.0f} head={[round(v, 4) for v in vec[:3]]}"
    )
    if len(vec) != EMBEDDING_DIMENSIONS:
        print(f"[embed-smoke] FAIL: dimensions != {EMBEDDING_DIMENSIONS}")
        return 1
    print("[embed-smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Makefile へembed-smokeターゲットを追記する**

ルート `Makefile` の `g1-gate:` ターゲットの後ろへ追記し、`.PHONY` 行へ `embed-smoke` を追加:

```make
embed-smoke: ## Embedding実APIスモーク(1呼び出し・.envにLATCH_LLM_MODE=real+LATCH_GEMINI_API_KEY必須)
	cd backend && uv run --env-file ../.env python -m latch.llm.embed_smoke
```

`.PHONY` 行(1行目)の末尾へ ` embed-smoke` を追記(既存行の変更はカンマなしの追記のみ)。

- [ ] **Step 3: ドライランと起動ガードを確認する(実APIは叩かない)**

```bash
make -n embed-smoke
cd backend && uv run python -m latch.llm.embed_smoke; echo "exit=$?"
```

Expected: ドライラン出力が `cd backend && uv run --env-file ../.env python -m latch.llm.embed_smoke` に展開されること。2番目は環境変数なし(llm_mode=stub)のため `[embed-smoke] FAIL: LATCH_LLM_MODE=real が必要` を出力して **exit=1**(実APIを呼ばない)。出力を報告ファイル§7の証拠用に控える。

- [ ] **Step 4: 全体の検査**

Run: `make lint && make test`
Expected: ともにexit 0(embed_smoke.pyは製品コードとしてarch testの対象 — 実時間参照トークンなし)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/llm/embed_smoke.py Makefile
git commit -m "feat: Embedding実APIスモークharness(make embed-smoke)"
```

### Task 10: integration試験(test_embedding_pipeline.py・作成のみ)

**Files:**
- Create: `backend/tests/integration/test_embedding_pipeline.py`
- Modify(条件付き): `backend/tests/integration/test_events_pipeline.py`(期待値の機械的追従のみ — Step 2の規定による)

**Interfaces:**
- Consumes: compose常設api(127.0.0.1:8000)・`db_engine` fixture(tests/integration/conftest.py)・`PubsubEventBus`(`subscription` 上書き引数)・`Worker`(Task 8の `embedding` 注入)・`EmbeddingWorker`(Task 6)・`BackfillRunner`(Task 7)・`StubLLM`(llm/stub)・FakeClock
- Produces: design §4.2の7試験(E2E作成・内容更新・resume・失敗→バックフィル・draft・削除・重複投入)。**実行はapi/workerイメージ再ビルド後の `make test-ci` のみ**(スーパーバイザー検証時)。収集確認(`--collect-only`)までを実装側検証とする

- [ ] **Step 1: 試験ファイルを作成する**

`backend/tests/integration/test_embedding_pipeline.py`:

```python
"""Embeddingパイプラインのci環境実証(M2 ws-2 design §4.2)。

実HTTP(compose api)・実DB・実Pub/Subエミュレータ(127.0.0.1:8085)。
GatewayはStubLLMラップの記録スタブ(呼び出し回数・渡されたtextを記録 —
design §4.2)。Worker処理はテストプロセス内Worker(bus=実エミュレータ・
clock=FakeClock・engine=実DB・embedding=記録スタブ注入)が担う。
**実行はapi/workerイメージ再ビルド後の make test-ci のみ**(スーパーバイザー
検証時)。試験専用subscriptionを作成/削除し試験間の残余メッセージ干渉を
構造的に排除する(ws-1のtest_events_pipeline.pyと同じ構成)。
"""

import asyncio
import os
import re
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.llm.errors import LLMProviderError
from latch.llm.stub import StubLLM

pytestmark = pytest.mark.integration

# テストプロセスからはport映射経由(コンテナ内はpubsub:8085 — compose.yaml)
os.environ.setdefault("LATCH_PUBSUB_EMULATOR_HOST", "127.0.0.1:8085")

# env設定後にimport(PubsubEventBus構築がPUBSUB_EMULATOR_HOSTを読むため)
from latch.events import PubsubEventBus  # noqa: E402
from latch.settings import Settings  # noqa: E402
from latch.worker.backfill import BackfillRunner  # noqa: E402
from latch.worker.embedding import EmbeddingWorker  # noqa: E402
from latch.worker.main import Worker  # noqa: E402


async def _instant(_seconds: float) -> None:
    """バックオフ待機の即時化(ws-1と同じ規律)。"""
    return None


# -- 共通ヘルパ(test_events_pipeline.py と同じ流儀)--


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


async def _register(api_client, subject: str, birth_date: str = "1990-04-01"):
    idp_token = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "m2ws2", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["user"]["id"]


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured() -> dict:
    return {
        "category": {"primary": "drinking", "secondary": None},
        "alcohol_involved": False,
        "time": {"start": _future(3), "end": None},
        "location": {"name": "天文館"},
    }


def _active_payload() -> dict:
    return {
        "raw_text": "明日の夜 天文館で軽く飲みたい",
        "status": "active",
        "structured_intent": _structured(),
    }


async def _fetch_event(db_engine, event_type: str, intent_id, version: int):
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT status, payload FROM match_events"
                " WHERE event_type = :et AND source_intent_id = :iid"
                " AND payload->>'version' = :v"
            ),
            {"et": event_type, "iid": intent_id, "v": str(version)},
        )
        return res.first()


async def _wait_status(
    db_engine, event_type: str, intent_id, version: int, timeout: float = 20.0
):
    """実時間ポーリング(テストコードは実時間参照可 — arch test対象外)。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        row = await _fetch_event(db_engine, event_type, intent_id, version)
        if row is not None and row[0] in ("processed", "quarantined"):
            return row
        await asyncio.sleep(0.2)
    pytest.fail(
        f"timeout: match_events({event_type}, {intent_id}, v{version}) not finalized"
    )


async def _fetch_embedding(db_engine, intent_id):
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT embedding IS NOT NULL, embedding_model"
                " FROM intents WHERE id = :i"
            ),
            {"i": intent_id},
        )
        return res.first()


# -- 記録Gateway(design §4.2: 呼び出し回数・渡されたtextを記録)--


class _RecordingGateway:
    """EmbeddingWorker注入用Gatewayスタブ(失敗注入はfailフラグで実行時切替)。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail = False
        self._stub = StubLLM()

    async def embed_intent(self, *, text: str, intent_id: str) -> list[float]:
        self.calls.append({"text": text, "intent_id": intent_id})
        if self.fail:
            raise LLMProviderError("recording gateway: injected failure")
        return await self._stub.embed(text)


# -- fixture: 試験専用subscription + テストプロセス内Worker --


class _WorkerEnv:
    def __init__(self, bus, clock, worker, task, gateway, embedding):
        self.bus = bus
        self.clock = clock
        self.worker = worker
        self.task = task
        self.gateway = gateway
        self.embedding = embedding


@pytest.fixture
async def worker_env(db_engine):
    sub_name = f"match-events-test-{uuid_mod.uuid4().hex[:8]}"
    settings = Settings()
    bus = PubsubEventBus(settings, subscription=sub_name)
    for _ in range(40):  # エミュレータ起動待ち(最大20秒)
        try:
            await bus.ensure()
            break
        except Exception:
            await asyncio.sleep(0.5)
    else:
        pytest.fail("pubsub emulator not reachable at 127.0.0.1:8085")
    clock = FakeClock(SystemClock().now())
    gateway = _RecordingGateway()
    embedding = EmbeddingWorker(
        engine=db_engine, clock=clock, gateway=gateway, bus=bus
    )
    worker = Worker(
        clock=clock,
        settings=settings,
        bus=bus,
        engine=db_engine,
        sleep=_instant,
        embedding=embedding,
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)  # subscribe開始を待つ
    try:
        yield _WorkerEnv(
            bus=bus, clock=clock, worker=worker, task=task,
            gateway=gateway, embedding=embedding,
        )
    finally:
        worker.request_shutdown()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except TimeoutError:
            task.cancel()
        # 残余メッセージを次試験へ残さない(closeより前に削除 — ws-1と同じ)
        bus.delete_subscription()
        await bus.close()


@pytest.fixture
async def user_env(api_client, db_engine):
    """subject実行ごと一意のuser。後始末でmatch_events・intent行を消す。"""
    subject = f"m2ws2-{uuid_mod.uuid4().hex[:12]}"
    headers, user_id = await _register(api_client, subject)
    st = {"headers": headers, "user_id": user_id}
    try:
        yield st
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM match_events WHERE source_intent_id IN"
                    " (SELECT id FROM intents WHERE user_id = :uid)"
                ),
                {"uid": user_id},
            )
            await conn.execute(
                text("DELETE FROM intents WHERE user_id = :uid"), {"uid": user_id}
            )


async def _create_active(api_client, headers) -> dict:
    resp = await api_client.post(
        "/v1/intents", headers=headers, json=_active_payload()
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]  # IntentEnvelopeラップ(ws-1と同じ)


# -- §4.2の7試験 --

# 時間帯要素の形式(実行時刻相対の_timeで完全ピンできないため構造検証 — 07 §3)
_TIME_PART_RE = re.compile(r"^(平日|週末)(朝|昼|夕方|夜|深夜)\d+時以降$|^(平日|週末)(朝|昼|夕方|夜|深夜)\d+-\d+時$")


async def test_1_e2e_create_embeds_and_emits(
    api_client, db_engine, worker_env, user_env
):
    """E2E(作成): embedding NOT NULL・model記録・embedding_completed行processed。
    Gatewayへ渡されたtextが正規化形式でraw_textを含まない(実経路の#10)。"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await _wait_status(
        db_engine, "embedding_completed", intent["id"], intent["version"]
    )
    row = await _fetch_embedding(db_engine, intent["id"])
    assert row[0] is True
    assert row[1] == "gemini-embedding-001"  # 01 §18(モデル識別子+版の記録)
    assert len(worker_env.gateway.calls) == 1
    got = worker_env.gateway.calls[0]
    assert got["intent_id"] == intent["id"]
    parts = got["text"].split(" / ")
    assert parts[0] == "drinking"  # category.primary
    assert _TIME_PART_RE.match(parts[1]), parts[1]  # 時間帯の形式
    assert parts[2] == "天文館"  # location.name
    assert re.fullmatch(r"\d+-\d+人|\d+人", parts[3])  # 人数の表現
    # raw_text(原文)を含まない — 確定値#10
    assert "明日の夜" not in got["text"]
    assert "軽く飲みたい" not in got["text"]


async def test_2_update_clears_and_reembeds(
    api_client, db_engine, worker_env, user_env
):
    """内容更新: PATCH→embedding NULL化→debounce 10秒→再エンベディング→v2完了。"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await _wait_status(
        db_engine, "embedding_completed", intent["id"], intent["version"]
    )
    headers = user_env["headers"]
    v2 = dict(_active_payload())
    v2["raw_text"] = "明日の夜 天文館でしっかり飲みたい"
    r2 = await api_client.patch(f"/v1/intents/{intent['id']}", headers=headers, json=v2)
    assert r2.status_code == 200, r2.text
    assert r2.json()["intent"]["version"] == 2
    cleared = await _fetch_embedding(db_engine, intent["id"])
    assert cleared[0] is False  # §2.3: 内容更新でNULLクリア
    # Worker受信(submit)完了後にClockを進める(ws-1 test_2と同じsettle)
    await asyncio.sleep(1.0)
    worker_env.clock.advance(timedelta(seconds=10))  # debounce窓解放
    await _wait_status(db_engine, "updated", intent["id"], 2)
    await _wait_status(db_engine, "embedding_completed", intent["id"], 2)
    reembedded = await _fetch_embedding(db_engine, intent["id"])
    assert reembedded[0] is True
    assert len(worker_env.gateway.calls) == 2  # 作成+更新(T1 v0.2 §4)


async def test_3_resume_skips_reembed(api_client, db_engine, worker_env, user_env):
    """resume: version+1のupdated→埋め込み再実行なしでembedding_completed(新version)。"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await _wait_status(
        db_engine, "embedding_completed", intent["id"], intent["version"]
    )
    headers = user_env["headers"]
    resp = await api_client.post(f"/v1/intents/{intent['id']}/pause", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await api_client.post(f"/v1/intents/{intent['id']}/resume", headers=headers)
    assert resp.status_code == 200, resp.text
    new_version = resp.json()["intent"]["version"]
    assert new_version == intent["version"] + 1  # 05 §6・06 §9
    await asyncio.sleep(1.0)  # Worker受信settle(ws-1 test_8と同じ)
    worker_env.clock.advance(timedelta(seconds=10))
    await _wait_status(db_engine, "updated", intent["id"], new_version)
    await _wait_status(db_engine, "embedding_completed", intent["id"], new_version)
    assert len(worker_env.gateway.calls) == 1  # resumeではGateway不呼(確定値#6)
    row = await _fetch_embedding(db_engine, intent["id"])
    assert row[0] is True  # 埋め込みは保持(クリアされない)


async def test_4_failure_then_backfill(api_client, db_engine, worker_env, user_env):
    """失敗→バックフィル: fail注入→NULLのまま→backfill.run_once→埋め込み+復帰。"""
    worker_env.gateway.fail = True  # 失敗スタブへ(design §4.2)
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await asyncio.sleep(1.0)  # handle(失敗)の完了を待つ
    row = await _fetch_embedding(db_engine, intent["id"])
    assert row[0] is False  # 06 D-15: NULLのまま保存
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT count(*) FROM match_events"
                " WHERE event_type = 'embedding_completed'"
                " AND source_intent_id = :i"
            ),
            {"i": intent["id"]},
        )
        assert res.scalar() == 0  # embedding_completed行なし
    worker_env.gateway.fail = False  # 成功スタブへ差し替え
    backfill = BackfillRunner(
        engine=db_engine,
        embedding=worker_env.embedding,
        interval_sec=1.0,
        batch_limit=50,
    )
    await backfill.run_once()  # 周期を待たず直接1周期実行
    row = await _fetch_embedding(db_engine, intent["id"])
    assert row[0] is True
    assert row[1] == "gemini-embedding-001"
    await _wait_status(db_engine, "embedding_completed", intent["id"], 1)


async def test_5_draft_not_embedded_until_active(
    api_client, db_engine, worker_env, user_env
):
    """draft: 埋め込みなし・Eventなし→active化→created経路で埋め込み+完了。"""
    headers = user_env["headers"]
    draft = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={"raw_text": "下書き", "status": "draft", "structured_intent": None},
    )
    assert draft.status_code == 201, draft.text
    await asyncio.sleep(1.0)
    assert worker_env.gateway.calls == []  # draftはEmbeddingしない(06 §3)
    act = await api_client.patch(
        f"/v1/intents/{draft.json()['intent']['id']}",
        headers=headers,
        json=dict(_active_payload()),
    )
    assert act.status_code == 200, act.text
    intent_id = draft.json()["intent"]["id"]
    version = act.json()["intent"]["version"]
    await _wait_status(db_engine, "created", intent_id, version)  # 作成種と同一経路
    await _wait_status(db_engine, "embedding_completed", intent_id, version)
    assert len(worker_env.gateway.calls) == 1


async def test_6_delete_no_embedding_kick(
    api_client, db_engine, worker_env, user_env
):
    """削除: DELETE→deleted処理はembeddingキックしない(06 §1)。"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await _wait_status(
        db_engine, "embedding_completed", intent["id"], intent["version"]
    )
    resp = await api_client.delete(
        f"/v1/intents/{intent['id']}", headers=user_env["headers"]
    )
    assert resp.status_code == 204, resp.text
    await _wait_status(db_engine, "deleted", intent["id"], intent["version"])
    await asyncio.sleep(1.0)
    assert len(worker_env.gateway.calls) == 1  # deletedでは増えない


async def test_7_duplicate_delivery_embeds_once(
    api_client, db_engine, worker_env, user_env
):
    """重複投入: 同一created Event 2回publish→埋め込み1回(duplicate→handle冪等)。"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await _wait_status(
        db_engine, "embedding_completed", intent["id"], intent["version"]
    )
    await worker_env.bus.publish_match_event(  # 同一3点組を手動再publish(テストパブリッシャー — 10 §1)
        event_type="created", intent_id=intent["id"], version=intent["version"]
    )
    await asyncio.sleep(2.0)  # 2回目の受領(duplicate)を待つ
    assert len(worker_env.gateway.calls) == 1  # API不呼び出し(has_embedding→直接投入)
    row = await _fetch_embedding(db_engine, intent["id"])
    assert row[0] is True
```

**実装時の注意**:
- `test_1` の `_TIME_PART_RE`: `_structured()` の time.start は `_future(3)`(実行時刻+3時間)で time.end なし → 正規化テキストの時間帯要素は `{平日|週末}{帯}{h}時以降` の形になる。開始時刻が帯の境界ちょうどでも `_TIME_PART_RE` の選択肢でカバーされる
- Workerへ `backfill` を渡していない(未注入 → run()内でembedding注入済みのものを使いBackfillRunner構成・sleep-first 300秒のためテスト中にrun_onceは走らない)
- `test_4` で `BackfillRunner` に渡す `embedding` は `worker_env.embedding`(Workerが使う同一インスタンス・busも同じ試験専用subscriptionへpublishする)

- [ ] **Step 2: test_events_pipeline.py の期待値追従を確認する(design §3.4-3・§5-3)**

ws-1の8試験はembedding未注入の `Worker(clock, settings, bus, engine, sleep)` で構成される。ws-2マージ後の挙動: run()内で `build_embedding_gateway(clock, settings)` が構築される(llm_mode=stub → StubLLM・外部接続なし)・created/updated処理後にhandleが走り **embedding_completed行が追加発生**する。既有8試験のassertへの影響を分析する:

- `test_1`/`test_2`/`test_3`/`test_4`/`test_8` のassertはすべて `_wait_status`/`_fetch_event` の **event_type絞り込み**(created/updated/deleted)であり、embedding_completed行の追加では壊れない
- `test_3` の `SELECT count(*) FROM match_events WHERE source_intent_id = :i == 0` は**draft作成直後**(active化前)の検査でありembedding_completed行はまだ存在しない
- `test_5`/`test_6`/`test_7` のghost intentはhandleのフェーズ1で行なしno-op(embedding_completed行は作られない)。test_7の行数assertはevent_type='created'絞り込み
- teardown(user_env)は `source_intent_id IN (SELECT id FROM intents ...)` でembedding_completed行も削除される

**判断**: unit(`make test`)ではこの変更は実行されないため実装側で赤化を確認できない。上記分析のとおり**追従不要と判断し、test_events_pipeline.py は無変更**とする。報告書design §5-3へこの分析根拠を記録する。**スーパーバイザー検証時のtest-ciで赤化した場合は、期待値の値のみを最小修正する**(試験意図を変えない)。修正不能な赤化が発生した場合は報告書へ記録し判断を仰ぐ(agent3はagent4の学習資産同期もns対象外 — スーパーバイザー領域)。

- [ ] **Step 3: 収集確認する(実行はしない)**

```bash
cd backend && uv run pytest --collect-only tests/integration/test_embedding_pipeline.py -q
```

Expected: `7 tests collected`・exit 0。あわせて `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることを確認(basename一意)。

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_embedding_pipeline.py
git commit -m "test: EmbeddingパイプラインE2E 7試験(作成のみ・実行はスーパーバイザー)"
```

### Task 11: 報告ファイル・完了条件検証

**Files:**
- Create: `docs/plans/M2/ws-2-report.md`

**Interfaces:**
- Consumes: 本計画§6の検証コマンド・§7の報告形式
- Produces: 報告ファイル(スーパーバイザーが検証・マージ時に使用)

- [ ] **Step 1: 完了条件§6の検証をすべて実行し結果を控える**

```bash
make lint && make test
cd backend && uv run pytest --collect-only tests/integration/test_embedding_pipeline.py -q
cd .. && rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
cd .. && git diff --stat main -- backend/alembic docs
git diff --name-only main | sort
git status --short
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
make -n embed-smoke
cd backend && uv run python -m latch.llm.embed_smoke; echo "exit=$?"
```

§6の各条件の期待値と突き合わせる(§4の一覧と差異があれば、理由を報告書に書く)。

- [ ] **Step 2: 報告ファイルを§7の形式で作成する**

`docs/plans/M2/ws-2-report.md` を§7のテンプレートどおりに作成し、Step 1の証拠を貼る。design §5の実装時確認事項4件の結果を必ず記載する:
1. google-genaiの実機挙動 — unit試験の契約ピン結果とSDK実バージョン(Task 1で控えたもの)。実機確認は「make embed-smoke=スーパーバイザー検証待ち」と記録
2. バックフィル初期値 — settings.pyの実装値(300秒・50)
3. test_events_pipeline.pyの期待値追従範囲 — Task 10 Step 2の分析根拠(追従不要の判断・あるいは実施した修正箇所)
4. docs改版候補の申告 — 「内容更新時のembeddingクリア」を05 §5または06 §9へ一文言として明記するかの候補(design §2.3。判断はsupervisor)

- [ ] **Step 3: 全体を検証してコミットする**

```bash
make lint && make test
git add docs/plans/M2/ws-2-report.md
git commit -m "docs: M2 ws-2の実行報告(完了条件7項目の証拠・test-ci/embed-smokeは検証待ち)"
git status --short  # 空であること
```

- [ ] **Step 4: 最終返信**

報告ファイルのパスと完了条件7項目の結果一覧(項目2の実行部分は「test-ci=スーパーバイザー検証待ち」・項目7の実API部分は「embed-smoke=スーパーバイザー検証待ち」)を返す。

---

## Self-Review の記録(計画書作成時点の確認)

1. **Specカバレッジ**: design §2.1(Worker配線案B→Task 8)・§2.2(2フェーズ+ガード付きUPDATE→Task 6)・§2.3(_UPDATEクリア→Task 3)・§2.4(正規化テキスト導出規則→Task 2)・§2.5(バックフィル→Task 7)・§2.6(Gemini実プロバイダ→Task 4)・§2.7(スモーク→Task 9)・§2.8(build_embedding_gateway→Task 5)・§2.9(マイグレーション・docs改版なし→§4・§5)・§3.1/§3.2(ファイル一覧→§4)・§4.1(unit表→Task 2〜8)・§4.2(integration 7試験→Task 10)・§5-1〜4(実装時確認事項→Task 1・4・10・11)に対応タスクを確認済み。design §1.4のスコープ外7項目・§2.10のYAGNI切り捨て7項目は§5禁止へ反映
2. **プレースホルダ**: TBD/TODO/「後で決める」記述なし。実装コード・試験コード・SQL・コミットメッセージを全タスクに全文記載
3. **型一貫性**: `EmbeddingWorker.handle(intent_id: uuid.UUID, version: int)`(Task 6定義)をTask 7(BackfillRunnerの委譲)・Task 8(_kick_embedding)・Task 10(integration)が同型で使用。`build_embedding_text(EmbeddingTextInput)`(Task 2)をTask 6が使用。`BackfillRunner(*, engine, embedding, interval_sec, batch_limit)`(Task 7)をTask 8のrun()内構築・Task 10のtest_4が同型で使用。`Worker(..., embedding=, backfill=)`(Task 8)をTask 10のworker_env fixtureが使用。`EVENT_EMBEDDING_COMPLETED`(Task 6でevents.pyへ追記)をembedding.py・試験が使用
4. **Review Focus**: §3の5項目が所有タスクの試験に割り当て済み(各項目末尾に明記)。並行handleの二重書き込みはTask 6の `test_handle_update_zero_rows_no_event` が担保
