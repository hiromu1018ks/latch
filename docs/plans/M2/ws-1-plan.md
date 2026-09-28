# M2 ws-1(イベント駆動基盤)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M1が実装したoutbox(match_eventsへの同一トランザクションINSERT)からPub/Sub経由のMatching Worker第1段処理完了までの経路を開通させる — (1) ci環境のPub/Subエミュレータをcomposeへ追加、(2) コミット直後publish+フォールバックリレー、(3) Worker第1段(debounce・idempotency・version検査・5回再試行→quarantined・削除Eventの候補無効化・破棄と隔離の区別)。Embeddingキックの実体(ws-2)はフックの位置のみ予約する。

**Architecture:** 新規 `latch.events` パッケージにポート(EventBus Protocol: `IncomingEvent`・`publish_match_event`/`subscribe`/`ensure`/`close`)とPub/Subエミュレータ対応実装(PubsubEventBus)・フォールバックリレー(FallbackRelay)を置く。`latch.worker` にdebounce(TrailingDebouncer: updatedのみ10秒トレーリング窓)と第1段処理(Stage1: 行確保・version検査・種別処理・再試行)を置く。発行側は `IntentService` へEventBus注入を追加しuowコミット直後にpublish(失敗はログで握り)。正しさはversion検査+UNIQUE制約がDBで担保し、debounceは最適化に徹する(at-least-once前提)。隔離の実体はPub/SubのDLQでなく `match_events.status='quarantined'`(エミュレータ未対応機能に依らない構成 — design §2.1・§2.7)。

**Tech Stack:** 既存(Python 3.13 / FastAPI / SQLAlchemy[asyncio]+asyncpg / pytest)+ **google-cloud-pubsub(本単位が新規追加・唯一の依存追加)**。**マイグレーション追加なし**(alembic 0002がheadのまま — design §2.6)。**docs(01〜12)改版なし**(design §2.6)。

**Spec:** `docs/plans/M2/ws-1-design.md`(agent1設計メモ。設計判断として未解決の論点はなし — design §5。実装時確認事項3件は本計画のTask 1・Task 5・Task 10へ組み込み済み)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m2-ws-1`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(本単位は依存追加があるためlock更新込みで導入される)。
- **共有ci-db運用**(STATUS運用ルール1〜3): compose常設環境(latch-ci)のDBとalembic状態はworktree間・スーパーバイザー検証の共有資産。本単位は**マイグレーション追加なし**のため `make migrate` は不要(実行しても冪等)。**開発はunit試験(`make lint`・`make test`)で完結させる** — 実装エージェントは `make test-ci` / `make up` / `make down` / `docker compose …` / `docker build` を一切実行しない。integration試験ファイルは作成するが**実行せず**、報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する。`make test` はintegrationファイルの収集(import)まで行うため、構文・importの正当性はunit実行で検証される。
- **例外として許可されるDockerコマンド**: `docker pull gcr.io/google-cloudsdk/cloud-pubsub-emulator:latest` のみ(Task 1のイメージ供給確認 — design §5-1)。**コンテナの作成・起動・停止は一切しない**。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。本計画の新規テストファイル(`test_worker_debounce.py`・`test_worker_stage1.py`・`test_events_relay.py`・`test_event_publish.py`・`test_events_pipeline.py`)はbackend/tests配下全体で一意であることをコミット前に `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることで確認する。
- **並走単位なし**: M2実行waveは ws-1 → (ws-2 ∥ ws-3)。本単位は単独waveであり、交点ファイルの競合は想定しない(main.py・compose.yaml・Makefile も本単位が唯一触る)。
- **設計判断の固定値**: design §2の採用判断(エミュレータ・コミット直後publish+リレー・Worker内subscribe+debounce・再試行はWorker内ループ・DLQ不使用・マイグレーション追加なし)と本計画§8のIF確定事項(IncomingEvent契約・Stage1のAPIとSQL・debouncerのAPI・relayのSQL・設定既定値・Makefile/composeの形)は固定値として落としてある。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする(12 第8節 運用ルール5)。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由(システムPythonと衝突させないため)。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| Eventは5種(作成・更新・削除・期限切れ・指定時刻)+派生1種(embedding_completed)。MVPで発生するのは内部由来5種 | 01 §5 |
| Match Eventはキュー経由で非同期処理。debounce(作成には適用せず・更新のみ10秒窓で統合し最新versionのみ処理)・idempotency key・version numberで不要な再計算を防ぐ。冪等化はmatch_eventsのUNIQUE制約がDBレベルで保証 | 01 §16 |
| 処理失敗は上限回数まで再試行し、再試行しても失敗するEventは失敗理由を保持したうえで隔離 | 01 §16 |
| Message Queue選定=Google Cloud Pub/Sub(idempotency keyで冪等化。MVP規模にKafkaは過剰) | 04 §3選定表 |
| match_events: id / event_type(6値)/ source_intent_id / payload(発火時のIntent versionを含む)/ status(pending/processed/quarantined)/ created_at / processed_at。UNIQUE(event_type, source_intent_id, payload内version)がat-least-once配信へのDBレベル重複排除。隔離は失敗理由をpayloadに保持。**削除済みIntentへの参照Eventは正当な遅延Eventとしてstatus=processedで破棄(理由をpayloadに記録)、payload不正のみquarantinedへ隔離** | 05 §2 |
| 第1段の確定値: (0) draft対象外・draft→active化の初回投入は作成種(version+1でも作成種) (1) 作成Eventは窓なし即時・更新Eventのみ同一source_intent_idを10秒トレーリング窓で統合し窓解放時の最新versionのみ処理・削除/期限切れ/指定時刻Eventは統合せず即時 (2) idempotency key=(event_type, source_intent_id, version) (3) version検査: =現行のみ処理・<現行は破棄・>現行はFOR UPDATE再読込し乖離が続く限り再試行 (4) 処理失敗は5回まで再試行→失敗理由を保持してquarantined | 06 §9 |
| 削除Eventは「当該Intentを含む候補・保留の無効化(Layerを経ない)」 | 06 §1 |
| 参照先Intent不在のEvent=正当な遅延Eventとしてprocessed破棄(隔離にしない)。payload不正(構造違反・version欠落・型不一致等)=5回再試行後にquarantined。10 §4.7の毒ペイロード試験の対象はpayload不正 | 06 §9 |
| resume時はversion+1の再評価Event(update種)を発行(idempotencyキー衝突回避) | 06 §9・05 §6 |
| debounce 10秒・retry 5回は初期値とし、計測(Queue lag)を見て調整 | 06 §9 |
| テスト環境は本番と同じ種類で規模を縮小。ci=最小構成・常設(API 1/Worker 1/DB共用)。時刻操作はClock経由(適用範囲にdebounceの10秒窓を含む)。「Pub/Subへのテスト用パブリッシャーでMatch Event 5種と派生イベントembedding_completedを手動発火できる」 | 10 §1 |
| 初期LATCH判定p95 10秒の層別予算: Embedding区間(debounce込み。作成Eventは窓なし)≤2秒。計測起点は「対象Intentのactive化の保存コミット時点」 | 06 §1 |
| Queue障害試験: 毒ペイロード→retry上限5回→quarantined・滞留が後続に波及しない。削除済みIntent参照Eventはprocessed破棄。同一Event2回投入でmatch_candidatesが二重生成しない(UNIQUE制約) | 10 §4.7 |
| make test-ci(compose up)はapiイメージを再ビルドしない。コード変更後の検証では `docker compose build api worker` が先行必須 | STATUS運用ルール4 / compose.yaml |
| M2スコープ1(Event発行とPub/Sub連携・第1段)・G2冪等性条件 | 12 第3節 M2-1・G2 |
| 発行側の既存実装(insert_match_event・発行網羅表)・受信側雛形・compose現状 | design §1.3 / intents/events.py・service.py・worker/main.py・compose.yaml |

## 2. グローバル制約(全タスクに暗黙に適用)

- **永続化は `sqlalchemy.text()` 生SQLのみ**(intents/events.py・store.pyと同じ形式)。ORMモデル・リポジトリ層を作らない。**時刻は `clock.now()` の明示値**(DB時刻関数DEFAULTに頼らない)
- **製品コード(`backend/src/latch/`)で実時間への直接参照を禁止**(arch test `test_arch_no_direct_time.py` が強制: `datetime.now` / `utcnow` / `date.today` / `time.time` / `time.monotonic` / `time.sleep` / `from time import`)。debounceのtick・リレー周期・再試行バックオフの待機は **`asyncio.sleep`(待機であり時刻参照ではない)**、時刻判定は **すべてClock基準**。バックオフの待機時間はテストで即時化できるよう `sleep: Callable[[float], Awaitable[None]]` を注入可能にする(既定 `asyncio.sleep`)
- **publishはuowトランザクションの外(コミット後)**。publish失敗は例外にせずログで握る(フォールバックリレーが回収)。コミット失敗ならEvent行ごと存在しないためpublishされない(design §2.2-B)
- **alembicは0002のまま**(design §2.6)。`backend/src/latch/intents/events.py` のoutbox INSERTと5種定数は不変(embedding_completed定数の追加はws-2 — design §3.3)
- stage1のSQLでmatch_eventsのstatus遷移はすべて **`WHERE ... AND status = 'pending'` 条件UPDATE**。payloadへの理由追記は **`payload || CAST(:extra AS jsonb)` で別キー(`discard_reason` / `failure_reason` / `raw`)を追加** し、UNIQUE索引が参照する `payload->>'version'` を変更しない(design §2.4)
- ログ(ロガー `latch.events.*` / `latch.worker.*`)は message_id・3点組(event_type/intent_id/version)・status遷移・理由のみ。**毒ペイロードの生データをログへ出さない**(生データはDBのpayloadへ保存 — design §2.4)。raw_text・表示名等も出さない(08 §2.4)
- unit試験は**外部プロセス不要・実時間待ちなし**(FakeClock+注入sleep+スタブconn)。SQL文字列そのものの正当性はintegration(test-ci)で担保する(design §4.1)
- 依存追加は `google-cloud-pubsub` のみ(`backend/pyproject.toml` + `backend/uv.lock` はTask 1で変更・以後触らない)。テスト用ライブラリ追加なし
- ruff対象(`E`,`F`,`I`,`UP`,`B`)と `ruff format` を毎コミット通す

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **同一Eventの二重受領(at-least-once配信+フォールバックリレーの再publish)** — 再受信で処理を二度走らせると、候補無効化などの副作用が重複し得る。行確保のstatus検査と条件UPDATEで弾く → Task 5の `test_intake_duplicate_skips_processing`・`test_process_locked_row_not_pending_returns_without_side_effects`、Task 10の `test_7_duplicate_delivery_processed_once` が所有
2. **同一version再受信によるdebounce窓の永久延長** — フォールバックリレーがupdated Eventを再publishし続けると、素朴な実装では窓が延長され続け処理が永久に始まらない(design §2.3明記の競合) → Task 4の `test_same_version_resubmit_does_not_extend_window`・`test_resubmit_after_release_starts_new_window` が所有
3. **理由追記がUNIQUE索引列(payload->>'version')を壊す** — `payload` を丸ごと置き換える実装だと `payload->>'version'` が消え、idempotencyキーが崩壊する → Task 5の `test_mark_processed_extra_preserves_version_key`(SQLパラメータの `extra` が `version` キーを含まない検証)、Task 10の `test_5_missing_intent_processed_discard`(実DBで追記後も3点組SELECTで行が見つかる検証)が所有
4. **source_intent_id UUID不正・JSONバイト不正の毒ペイロードで行挿入自体が失敗する** — 挿入失敗のままack不能になるとack_deadline(600秒)毎の再配信が無限に続く → Task 5の `test_intake_raw_poison_inserts_quarantined_row`(nil UUIDで確実に行が作られる検証)、Task 2の `test_from_payload_invalid_json` が所有
5. **status遷移コミット前にackが出る(クラッシュでメッセージ喪失)** — ackが先だと、DB遷移前にWorkerが死んだ場合に再配信が起こらずEvent行がpendingのまま滞留する(design §2.3「ackは処理完了後」) → Task 8の `test_dispatch_ack_after_intake_completion`(ackがintake完了後・debounce投入後は解放処理後に呼ばれる順序検証)、Task 4の `test_run_releases_and_acks_through_callback` が所有

## 4. スコープ(作成・変更するファイル一覧)

作成(design.md §3.1どおり+テスト2ファイル追加・理由注記):

```text
backend/src/latch/events/__init__.py           (Task 3。モジュール公開API: EventBus・IncomingEvent・Subscription・make_event_bus・FallbackRelay)
backend/src/latch/events/bus.py                (Task 2。EventBus Protocol・IncomingEvent・Subscription)
backend/src/latch/events/pubsub_bus.py         (Task 3。google-cloud-pubsub実装・ensure/publish/subscribe/publish_raw/close)
backend/src/latch/events/relay.py              (Task 6。フォールバックリレー)
backend/src/latch/worker/debounce.py           (Task 4。TrailingDebouncer・DebounceEntry・DebounceGroup)
backend/src/latch/worker/stage1.py             (Task 5。Stage1・IntakeResult・PayloadInvalid/Retryable・EVENT_TYPES)
backend/tests/unit/test_worker_stage1.py       (Task 2でIncomingEvent契約から作成開始 → Task 5でStage1本体を追記)
backend/tests/unit/test_worker_debounce.py     (Task 4)
backend/tests/unit/test_events_relay.py        (Task 6)
backend/tests/unit/intents/test_event_publish.py (Task 7。design §3.1一覧への追加 — publish接続のunit保証のため)
backend/tests/integration/test_events_pipeline.py (Task 10。作成のみ・実行しない)
docs/plans/M2/ws-1-report.md                   (Task 11。報告ファイル)
```

変更(design.md §3.2どおり):

- `backend/pyproject.toml`(+ `backend/uv.lock`)— 依存へgoogle-cloud-pubsubを追加(Task 1)
- `backend/src/latch/settings.py` — イベント駆動9設定を追加(Task 1)
- `backend/src/latch/intents/service.py` — IntentServiceへ `event_bus` 注入と `_publish` ヘルパー(uow後publish・失敗握り)を追加(Task 7)。発行箇所・event_typeは現状のまま
- `backend/src/latch/main.py` — lifespanでEventBus構築とフォールバックリレー起動を追加(独立スキップ判定へ1項目)(Task 7)
- `backend/src/latch/worker/main.py` — run()にsubscribe開始・debouncer・stage1配線を追加(既存clock/settingsシグネチャとgraceful shutdownは維持)(Task 8)
- `backend/tests/unit/test_worker.py` — Worker依存追加に伴う最小追従(既存3試験の意図は維持)+配線試験4件(Task 8)
- `compose.yaml` — pubsubサービス(エミュレータ)追加と api/worker への `LATCH_PUBSUB_EMULATOR_HOST` 設定(Task 9)
- `Makefile` — test-ciでworker停止→pytest→worker復帰(Task 9)

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`(gitignore済み)。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **compose常設環境の操作**: `make test-ci` / `make up` / `make down` / `docker compose …` / `docker build` / コンテナ起動停止(§0。許可は `docker pull gcr.io/google-cloudsdk/cloud-pubsub-emulator:latest` のみ)。api/workerイメージの再ビルドはスーパーバイザーの検証手順に含まれる
- `backend/alembic/` 全体(0002がheadのまま・design §2.6)、`backend/src/latch/intents/events.py`(outbox INSERTと5種定数は不変 — design §3.3)、`backend/src/latch/auth/`・`users/`・`ratelimit/`・`geo/`・`llm/`・`g1gate/`・`core/`
- `frontend/`・`prototype/`・`docs/`(01〜12・learn・reviews — design §2.6で改版対象なし)、`docs/plans/STATUS.md`(スーパーバイザー管理)、`docs/plans/M0/`・`docs/plans/M1/`
- `backend/tests/conftest.py`・`backend/tests/integration/conftest.py`(db_engine・api_clientで足りる — design §3.2)、`backend/tests/unit/` と `backend/tests/integration/` の既存テストファイル(test_worker.pyのみ§4どおり追従可)
- `docker/`・`.mise.toml`・`.gitignore`・ルート `README.md`・`backend/README.md`・`.claude/`・`.agents/`・`.hermes/`
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4に列挙された後続単位のスコープ):
  - Embeddingキックの実体・embedding_completedの発行と定数追加(ws-2。本単位は `embedding_hook` の呼び出し位置のみ)
  - Layer 1〜5の実処理・match_candidatesの生成(ws-3〜ws-7。本単位はdeleted Eventでの無効化SQLのみ)
  - 30分Bucket再評価・catch-upスキャン・scheduled Eventの発行主体(ws-6。本単位はscheduled受信時の規定=即時処理のみ)
  - expiry_sweeper・expired Eventの発行(M3-3。「回答待ち提案のクローズ」の実体はlatches不在のため対象外)
  - latches側の無効化(latches行はws-6以降にしか生成されない)
  - 本番GCP Pub/Subの実環境構成(M4/リリース。本単位は環境変数切替で接続可能な抽象にする)
  - Queue lag計測・alert発報(M4のObservability)
  - Pub/Subのdead letter topic・subscription宣言的retry・ordering key・publish済みマーカーのRedis/DB保持・テストパブリッシャーCLI・debounce状態のRedis外部化・exactly-once配信の追求(design §2.7のYAGNI切り捨て一覧)
  - `match_events`への列追加(published_at等 — design §2.2で棄却)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 11で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分を含む)
   検証: `make lint && make test` — ともにexit 0
2. **integration 8試験(test_events_pipeline.py)が収集できる** — **ただし実行はしない**(§0。api/workerイメージ再ビルド+エミュレータ起動が必要なためスーパーバイザー検証時に実施)
   検証(実装側): `cd backend && uv run pytest --collect-only tests/integration/test_events_pipeline.py -q` がexit 0(8件収集)。報告書に「**test-ci=スーパーバイザー検証待ち**」と記録
3. **実時間参照が `core/clock.py` のみ(events/・worker/配下はヒットしない)**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
4. **`backend/alembic/` と `docs/`(01〜12)に差分なし**
   検証: `git diff --stat main -- backend/alembic docs` — 出力なし
5. **触るファイルが §4 の一覧どおり**
   検証: `git diff --name-only main | sort` が§4の一覧と完全一致。`git status --short` が空(未コミット変更なし)
6. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし
7. **compose.yamlにpubsubサービス・Makefile test-ciのworker停止/復帰が規定どおり**(実行は検証待ち)
   検証: `make -n test-ci` のドライラン出力が `docker compose up -d --wait` → `docker compose stop worker` → pytest → `docker compose start worker` 復帰の順に展開されること(pytest成否にかかわらずworker復帰)。compose.yamlへpubsubサービスとapi/workerの `LATCH_PUBSUB_EMULATOR_HOST` が記録されていること

## 7. 報告形式

**結果ファイル**: `docs/plans/M2/ws-1-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M2 ws-1(イベント駆動基盤) 実行報告

- ブランチ: m2-ws-1 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る> |
| 2 | integration 8試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | <collect-only出力> |
| 3 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 4 | alembic・docs無変更 | PASS/FAIL | <git diff --stat 出力(空なら「空」)> |
| 5 | 触るファイルがスコープどおり | PASS/FAIL | <git diff --name-only出力> |
| 6 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 7 | compose.yaml・Makefileの規定 | 記載確認/make -n test-ci出力 | <ドライラン出力> |

## design §5 実装時確認事項の結果
1. エミュレータイメージ: <docker pull の成否と確認した公式の起動引数。ミラー鏡像に替えた場合はその記録>
2. 初期値(debounce 10秒・retry 5回・relay 30秒/5秒・ack_deadline 600秒): <settings.py の実装値>
3. 試験subscriptionのdrain実装: <採用した方式(試験専用subscriptionの作成/削除)の確認結果>

## 固定値の変更有無(design.md §2・本計画§8)
- ci Queue具象=Pub/Subエミュレータ(design §2.1): 変更なし / 変更あり(<前→後+理由>)
- コミット直後publish+フォールバックリレー(design §2.2): 変更なし / 変更あり(<前→後+理由>)
- Worker内subscribe+debounce・再試行はWorker内ループ(design §2.3): 変更なし / 変更あり(<前→後+理由>)
- 本計画§8のIF確定事項(IncomingEvent契約・Stage1 API/SQL・debouncer API・relay SQL・設定既定値・Makefile/compose形式): 変更なし / 変更あり(<前→後+理由>)

## スーパーバイザー検証手順(test-ci実行時)
1. `docker compose build api worker` — **api・workerイメージの再ビルドが必須**(make test-ci の compose up は再ビルドしないため・STATUS運用ルール4)
2. `make test-ci` — Makefileがworkerを停止してからpytestを実行し、成否にかかわらずworkerを復帰する。test_events_pipeline.py(#1〜#8)を含む全体グリーンで完了条件2を検証
   (マイグレーション追加なしのため `make migrate` は不要。実行しても冪等)
3. 常設worker復帰の確認: `docker compose ps` で worker が running に戻っていること(コンテナ起動自体がworker/main.py配線の起動確認 — design §4.2)

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件7項目の結果一覧(項目2・7の実行部分は「test-ci=スーパーバイザー検証待ち」)。

---

## 8. 実装ステップ(TDD。Task 1〜11の順で実行する)

### Task 1: エミュレータイメージ確認・依存追加・設定

**Files:**
- Modify: `backend/pyproject.toml`(+ `backend/uv.lock` は `uv sync` が更新)
- Modify: `backend/src/latch/settings.py`

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: Settingsの新フィールド9件(下記コードのとおり)。以降の全タスクが参照: `pubsub_project_id` / `pubsub_topic_match_events` / `pubsub_subscription_match_events` / `pubsub_emulator_host` / `pubsub_ack_deadline_sec` / `event_fallback_relay_after_sec` / `event_fallback_poll_sec` / `event_debounce_window_sec` / `event_retry_max`

- [ ] **Step 1: エミュレータイメージの供給と起動引数を確認する(design §5-1)**

WebSearch/WebFetchでGoogle Cloud公式ドキュメント(Pub/Sub エミュレータ)を確認し、イメージ名 `gcr.io/google-cloudsdk/cloud-pubsub-emulator` の現行タグと起動引数(`gcloud beta emulators pubsub start --host-port=0.0.0.0:8085`)が現行も有効であることを確かめる。次にイメージ供給の確認:

```bash
docker pull gcr.io/google-cloudsdk/cloud-pubsub-emulator:latest
```

※§0で許可した例外コマンド(コンテナは作成しない)。pullが失敗する場合(イメージ供給停止等)は、ミラー鏡像を探索してcompose.yaml(Task 9)の `image:` を当該鏡像に替え、報告ファイル「design §5 実装時確認事項の結果」に記録する。起動時の実際の動作確認(コンテナ起動・SDK接続)はスーパーバイザー検証時のtest-ciで行う。確認結果(イメージ名・タグ・起動引数の出典URL)を報告ファイル用に控える。

- [ ] **Step 2: 依存を追加する**

`backend/pyproject.toml` の `dependencies` へ1行追加(alphabetical順で `fastapi` と `pyjwt` の間):

```toml
    "google-cloud-pubsub>=2.21",
```

- [ ] **Step 3: 依存を導入する**

```bash
make setup
```

`backend/uv.lock` が更新される(google-cloud-pubsubとその依存: google-api-core・grpcio等)。`cd backend && uv run python -c "import google.cloud.pubsub_v1; print(google.cloud.pubsub_v1.__version__)"` でimportできることを確認。

- [ ] **Step 4: 設定を追加する**

`backend/src/latch/settings.py` の末尾(rate_limit系の後)へ追加:

```python
    # --- イベント駆動(M2 ws-1。design §3.2)---
    # Pub/Sub(04 §3選定)。ci=エミュレータ(composeのpubsubサービス)。
    # pubsub_emulator_host が空なら実GCP(本番)。非空ならSDKの
    # PUBSUB_EMULATOR_HOST 経由でエミュレータへ接続する
    pubsub_project_id: str = "latch-ci"
    pubsub_topic_match_events: str = "match-events"
    pubsub_subscription_match_events: str = "match-events-sub"
    pubsub_emulator_host: str = ""
    pubsub_ack_deadline_sec: int = 600  # 最大値(debounce10s+backoff31s+処理が収まる — design §2.3)
    # フォールバックリレー(design §2.2-B): 通常処理はしきい値に到達しない
    # (=通常時の再publishゼロ)。debounce 10秒+処理 < 30秒
    event_fallback_relay_after_sec: int = 30
    event_fallback_poll_sec: int = 5
    event_debounce_window_sec: int = 10  # 06 §9-1(初期値。調整はQueue lag計測で)
    event_retry_max: int = 5  # 06 §9-4(初回+再試行5回。バックオフ1,2,4,8,16秒)
```

- [ ] **Step 5: 既存試験が壊れないことを確認する**

Run: `make lint && make test`
Expected: ともにexit 0(既存 `tests/unit/test_settings.py` は app_env/log_level/database_url のみ検査しており新フィールドの影響を受けない)

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/latch/settings.py
git commit -m "feat: イベント駆動の設定とgoogle-cloud-pubsub依存を追加(M2 ws-1)"
```

### Task 2: eventsパッケージのポート(bus.py・IncomingEvent契約)

**Files:**
- Create: `backend/src/latch/events/bus.py`・`backend/src/latch/events/__init__.py`(この時点では bus.py のみ再export)
- Test: `backend/tests/unit/test_worker_stage1.py`(この時点ではIncomingEvent契約のみ。Task 5でStage1本体を追記)

**Interfaces:**
- Consumes: なし(bus.pyはSDKに依存しない純Python)
- Produces(以降全タスクの核):
  - `IncomingEvent`(dataclass): `message_id: str` / `data: dict`(デコード済み生JSON)/ `event_type: str | None` / `intent_id: uuid.UUID | None` / `version: int | None` / `ack: Callable[[], None]`。classmethod `from_payload(*, message_id: str, payload: bytes, ack: Callable[[], None]) -> IncomingEvent`。メソッド `triple(self) -> tuple[str, uuid.UUID, int] | None`(3点組。一部でも欠ければNone)
  - `EventBus`(Protocol): `async ensure(self) -> None` / `async publish_match_event(self, *, event_type: str, intent_id: uuid.UUID, version: int) -> None` / `async subscribe(self, on_message: Callable[[IncomingEvent], Awaitable[None]]) -> Subscription` / `async close(self) -> None`
  - `Subscription`(Protocol): `stop(self) -> None`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_stage1.py` を作成:

```python
"""Stage1(第1段処理)とIncomingEvent入力契約のunit試験(design §4.1)。

IncomingEvent.from_payload はテストパブリッシャー・API経由の両方の受信で
最初に通る境界(10 §1「テスト用パブリッシャー」の入力面)。Stage1本体は
Task 5でこのファイルへ追記する(スタブconn・注入sleepで決定的に)。
"""

import uuid

from latch.events import IncomingEvent


def _ack() -> None:  # 記録用のack
    pass


def test_from_payload_full_triple():
    iid = uuid.uuid4()
    payload = (
        b'{"event_type": "updated", "source_intent_id": "'
        + str(iid).encode()
        + b'", "version": 3}'
    )
    event = IncomingEvent.from_payload(message_id="m1", payload=payload, ack=_ack)
    assert event.triple() == ("updated", iid, 3)


def test_from_payload_missing_version_yields_none_triple():
    payload = b'{"event_type": "updated", "source_intent_id": "%s"}' % str(
        uuid.uuid4()
    ).encode()
    event = IncomingEvent.from_payload(message_id="m2", payload=payload, ack=_ack)
    assert event.triple() is None
    assert event.version is None


def test_from_payload_wrong_types_yield_none_triple():
    iid = uuid.uuid4()
    # versionがfloat / str / bool、source_intent_idがUUIDでない → いずれもNone
    for raw in (
        b'{"event_type": "updated", "source_intent_id": "%s", "version": 3.5}'
        % str(iid).encode(),
        b'{"event_type": "updated", "source_intent_id": "%s", "version": "3"}'
        % str(iid).encode(),
        b'{"event_type": "updated", "source_intent_id": "%s", "version": true}'
        % str(iid).encode(),
        b'{"event_type": "updated", "source_intent_id": "not-a-uuid", "version": 3}',
    ):
        event = IncomingEvent.from_payload(message_id="m3", payload=raw, ack=_ack)
        assert event.triple() is None, raw


def test_from_payload_invalid_json_keeps_raw():
    event = IncomingEvent.from_payload(
        message_id="m4", payload=b"not-json{", ack=_ack
    )
    assert event.triple() is None
    assert event.data == {"_raw": "not-json{"}  # 生データをDB保存経路へ残す
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_stage1.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.events'` — importで収集エラー)

- [ ] **Step 3: bus.py を実装する**

`backend/src/latch/events/bus.py`:

```python
"""EventBusポート(アダプタはpubsub_bus。ヘキサゴナルの端口)。

IncomingEventはAPI経由(pending行あり)とテストパブリッシャー直投入(行なし)
の両方を同じ形で扱う(design §2.3の冪等受領)。3点組(event_type,
source_intent_id, version)がidempotency key(06 §9-2)。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class IncomingEvent:
    """受信メッセージ。3点組の抽出不能(欠落・型不一致)は各要素None。"""

    message_id: str
    data: dict  # デコード済み生JSON(JSON不正時は {"_raw": "<文字列>"})
    event_type: str | None
    intent_id: uuid.UUID | None
    version: int | None
    ack: Callable[[], None] = field(repr=False)

    @classmethod
    def from_payload(
        cls, *, message_id: str, payload: bytes, ack: Callable[[], None]
    ) -> IncomingEvent:
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            data = {"_raw": payload.decode(errors="replace")}
        if not isinstance(data, dict):
            data = {"_raw": json.dumps(data)}
        et = data.get("event_type")
        iid = data.get("source_intent_id")
        ver = data.get("version")
        return cls(
            message_id=message_id,
            data=data,
            event_type=et if isinstance(et, str) else None,
            intent_id=_as_uuid(iid),
            version=ver if isinstance(ver, int) and not isinstance(ver, bool) else None,
            ack=ack,
        )

    def triple(self) -> tuple[str, uuid.UUID, int] | None:
        """3点組。一部でも欠ければNone(構造違反経路へ — design §2.4)。"""
        if self.event_type is None or self.intent_id is None or self.version is None:
            return None
        return (self.event_type, self.intent_id, self.version)


def _as_uuid(raw: object) -> uuid.UUID | None:
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


class Subscription(Protocol):
    """subscribeの戻り。stop()でストリーミングpullを終了する(未ackは再配信)。"""

    def stop(self) -> None: ...


class EventBus(Protocol):
    """Match Eventトランスポートのポート(design §3.1)。"""

    async def ensure(self) -> None:
        """topic/subscriptionを存在なければ作成する(冪等)。"""
        ...

    async def publish_match_event(
        self, *, event_type: str, intent_id: uuid.UUID, version: int
    ) -> None:
        """3点組をJSONでpublishする(publish形式は実装の契約)。"""
        ...

    async def subscribe(
        self, on_message: Callable[[IncomingEvent], Awaitable[None]]
    ) -> Subscription:
        """ストリーディングpullを開始する。ackはIncomingEvent.ack経由。"""
        ...

    async def close(self) -> None:
        """クライアントの後始末(プロセス終了時)。"""
        ...
```

`backend/src/latch/events/__init__.py`(この時点):

```python
"""イベント駆動基盤(M2 ws-1)。EventBusポートとPub/Sub実装・フォールバックリレー。"""

from latch.events.bus import EventBus, IncomingEvent, Subscription

__all__ = ["EventBus", "IncomingEvent", "Subscription"]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_stage1.py -v`
Expected: PASS(4件)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/events/__init__.py backend/src/latch/events/bus.py backend/tests/unit/test_worker_stage1.py
git commit -m "feat: EventBusポートとIncomingEvent契約(eventsパッケージ)"
```

### Task 3: PubsubEventBus(pubsub_bus.py)

**Files:**
- Create: `backend/src/latch/events/pubsub_bus.py`
- Modify: `backend/src/latch/events/__init__.py`(make_event_bus等をexport)

**Interfaces:**
- Consumes: `EventBus`・`IncomingEvent`(Task 2)・`Settings.pubsub_*`(Task 1)
- Produces: `PubsubEventBus(settings: Settings, *, subscription: str | None = None)`(subscriptionは試験専用subscription名の上書き用 — design §4.2)。`make_event_bus(settings: Settings) -> EventBus`(`events/__init__.py` 経由でmain.py・worker/main.pyが使用)。テスト・デバッグ用の `publish_raw(data: bytes) -> None`(10 §1テストパブリッシャーの毒ペイロード投入用)

**unit試験を書かない理由(design §4.1・§3.1の試験一覧にpubsub_bus単体の記載がなく、SDKの実RPCをunitで代替できないため。publish JSON形式・ack_deadline・ストリーミングpullの実挙動はintegration `test_events_pipeline.py`(Task 10)の実エミュレータ試験が担保する。本タスクは `make test` の収集(import)で構文・import正当性を検証する)**

- [ ] **Step 1: pubsub_bus.py を実装する**

```python
"""google-cloud-pubsub実装のEventBus(04 §3選定。ci=エミュレータ — design §2.1)。

SDKは環境変数 PUBSUB_EMULATOR_HOST が設定されていると自動でエミュレータへ
接続する(公式の切替経路。本番=実GCPは settings.pubsub_emulator_host が空)。
隔離の実体はDB側(match_events.status)のためdead letter topic・subscription
宣言的retryには依らない(design §2.7)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable

from latch.events.bus import EventBus, IncomingEvent, Subscription
from latch.settings import Settings

logger = logging.getLogger(__name__)


class PubsubEventBus(EventBus):
    """API・Workerの両プロセスから使う(トピック・subscriptionは共有)。"""

    def __init__(self, settings: Settings, *, subscription: str | None = None) -> None:
        from google.cloud import pubsub_v1  # 遅延import(unit収集の軽量化)

        self._settings = settings
        if settings.pubsub_emulator_host:
            # SDK公式のエミュレータ切替(認証もバイパスされる)
            os.environ["PUBSUB_EMULATOR_HOST"] = settings.pubsub_emulator_host
        self._publisher = pubsub_v1.PublisherClient()
        self._subscriber = pubsub_v1.SubscriberClient()
        self._topic_path = self._publisher.topic_path(
            settings.pubsub_project_id, settings.pubsub_topic_match_events
        )
        self._subscription_path = self._subscriber.subscription_path(
            settings.pubsub_project_id,
            subscription or settings.pubsub_subscription_match_events,
        )

    async def ensure(self) -> None:
        """topic/subscriptionを作成(冪等)。AlreadyExistsは握る。"""
        from google.api_core.exceptions import AlreadyExists

        def _ensure() -> None:
            try:
                self._publisher.create_topic(self._topic_path)
                logger.info("pubsub topic created: %s", self._topic_path)
            except AlreadyExists:
                pass
            try:
                self._subscriber.create_subscription(
                    name=self._subscription_path,
                    topic=self._topic_path,
                    ack_deadline_seconds=self._settings.pubsub_ack_deadline_sec,
                )
                logger.info("pubsub subscription created: %s", self._subscription_path)
            except AlreadyExists:
                pass

        await asyncio.to_thread(_ensure)

    async def publish_match_event(
        self, *, event_type: str, intent_id: uuid.UUID, version: int
    ) -> None:
        """3点組JSON({event_type, source_intent_id, version})をpublishする。"""
        data = json.dumps(
            {
                "event_type": event_type,
                "source_intent_id": str(intent_id),
                "version": version,
            }
        ).encode()
        await self.publish_raw(data)

    async def publish_raw(self, data: bytes) -> None:
        """生bytesをpublish(テストパブリッシャーの毒ペイロード投入用 — 10 §1)。"""
        future = self._publisher.publish(self._topic_path, data)
        await asyncio.wrap_future(future)

    async def subscribe(
        self, on_message: Callable[[IncomingEvent], Awaitable[None]]
    ) -> Subscription:
        """バックグラウンドスレッドのストリーミングpull(asyncio公式パターン)。

        callbackはgRPCスレッドで呼ばれるため、asyncioループへ
        run_coroutine_threadsafe で渡す。ackはSDKのmessage.ackをそのまま
        呼ぶ(スレッドセーフ)。
        """
        loop = asyncio.get_running_loop()

        def _callback(message) -> None:
            event = IncomingEvent.from_payload(
                message_id=message.message_id,
                payload=message.data,
                ack=message.ack,
            )
            asyncio.run_coroutine_threadsafe(on_message(event), loop)

        future = self._subscriber.subscribe(
            self._subscription_path, callback=_callback
        )
        return _PubsubSubscription(future)

    async def close(self) -> None:
        def _close() -> None:
            try:
                self._publisher.api_client.close()
            except Exception:  # noqa: BLE001 — 後始末の失敗は握る
                pass
            try:
                self._subscriber.close()
            except Exception:  # noqa: BLE001
                pass

        await asyncio.to_thread(_close)

    def delete_subscription(self) -> None:
        """試験専用subscriptionの削除(integration試験のteardown専用)。"""
        from google.api_core.exceptions import NotFound

        try:
            self._subscriber.delete_subscription(self._subscription_path)
        except NotFound:
            pass


class _PubsubSubscription(Subscription):
    def __init__(self, future) -> None:
        self._future = future

    def stop(self) -> None:
        # streaming pullのcancel(未ackメッセージは再配信 — at-least-once)
        self._future.cancel()


def make_event_bus(settings: Settings) -> EventBus:
    """プロセス構築用の工場(main.py・worker/main.pyが使用)。"""
    return PubsubEventBus(settings)
```

- [ ] **Step 2: __init__.py を更新する**

`backend/src/latch/events/__init__.py` を差し替え:

```python
"""イベント駆動基盤(M2 ws-1)。EventBusポートとPub/Sub実装・フォールバックリレー。"""

from latch.events.bus import EventBus, IncomingEvent, Subscription
from latch.events.pubsub_bus import PubsubEventBus, make_event_bus
from latch.events.relay import FallbackRelay

__all__ = [
    "EventBus",
    "FallbackRelay",
    "IncomingEvent",
    "PubsubEventBus",
    "Subscription",
    "make_event_bus",
]
```

※ `relay` のimportはTask 6で実装するまで通らない。**Task 3の時点では relay の行と `FallbackRelay` を除いた内容でコミットし、Task 6でこの形へ更新する**(段階的に合わせる。Task 3で書くのは `EventBus`・`IncomingEvent`・`PubsubEventBus`・`Subscription`・`make_event_bus` のみ)。

- [ ] **Step 3: 収集・既存試験でimportの正当性を確認する**

Run: `make lint && make test`
Expected: ともにexit 0(google-cloud-pubsubが `uv sync` 済みのためimport成功)

- [ ] **Step 4: Commit**

```bash
git add backend/src/latch/events/__init__.py backend/src/latch/events/pubsub_bus.py
git commit -m "feat: PubsubEventBus(エミュレータ切替・ensure・ストリーミングpull)"
```

### Task 4: TrailingDebouncer(debounce.py)

**Files:**
- Create: `backend/src/latch/worker/debounce.py`
- Test: `backend/tests/unit/test_worker_debounce.py`

**Interfaces:**
- Consumes: `Clock`(core/clock)・`IncomingEvent`(Task 2)
- Produces(Task 8のWorker配線が使用):
  - `DebounceEntry`(dataclass): `event: IncomingEvent` / `triple: tuple[str, uuid.UUID, int]` / `row_id: uuid.UUID`
  - `DebounceGroup`(dataclass): `intent_id: uuid.UUID` / `latest: DebounceEntry`(version最大)/ `absorbed: list[DebounceEntry]`(version昇順・latest以外)
  - `TrailingDebouncer(*, clock: Clock, window_sec: float, tick_sec: float, on_release: Callable[[DebounceGroup], Awaitable[None]])`。`submit(entry) -> bool`(True=新規作成/未知versionによる延長、False=既知versionで何もしない)。`poll() -> list[DebounceGroup]`(解放時刻 ≤ clock.now() のグループを取り出して削除)。`async run(*, stop: asyncio.Event | None = None) -> None`(tick周期でpollしon_releaseを待つ)
  - 解放時刻 = **未知versionを持つEventの到着時刻(Clock.now())+ window_sec**(Clock基準。同一versionの再提出は延長しない — design §2.3)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_debounce.py` を作成:

```python
"""TrailingDebouncerの窓管理(design §4.1)。

FakeClock+poll()/短tickのrun()で、窓統合・即時判定・再受信非延長・
窓吸収行の閉包をClock操作のみで再現する(実時間待ちなし — 10 §1)。
"""

import asyncio
import uuid
from datetime import timedelta

from latch.core.clock import FakeClock
from latch.events import IncomingEvent
from latch.worker.debounce import DebounceEntry, DebounceGroup, TrailingDebouncer


def _entry(event_type: str, iid: uuid.UUID, version: int) -> DebounceEntry:
    def _ack() -> None:
        pass

    payload = (
        b'{"event_type": "%s", "source_intent_id": "%s", "version": %d}'
        % (event_type.encode(), str(iid).encode(), version)
    )
    event = IncomingEvent.from_payload(
        message_id=f"{event_type}-{version}", payload=payload, ack=_ack
    )
    triple = event.triple()
    assert triple is not None
    return DebounceEntry(event=event, triple=triple, row_id=uuid.uuid4())


def _debouncer(clock: FakeClock, released: list[DebounceGroup]) -> TrailingDebouncer:
    async def _on_release(group: DebounceGroup) -> None:
        released.append(group)

    return TrailingDebouncer(
        clock=clock, window_sec=10.0, tick_sec=0.001, on_release=_on_release
    )


def test_window_release_merges_updates(fake_clock):
    """窓内の v2→v3→v4 は v4 のみ処理対象(latest)・v2/v3 は吸収(absorbed)。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    iid = uuid.uuid4()
    assert d.submit(_entry("updated", iid, 2)) is True
    fake_clock.advance(timedelta(seconds=3))
    assert d.submit(_entry("updated", iid, 3)) is True  # 未知version→延長
    fake_clock.advance(timedelta(seconds=3))
    assert d.submit(_entry("updated", iid, 4)) is True
    assert d.poll() == []  # 最後の到着から10秒未満
    fake_clock.advance(timedelta(seconds=4))  # v4到着から10秒
    groups = d.poll()
    assert len(groups) == 1
    assert groups[0].latest.triple[2] == 4
    assert [e.triple[2] for e in groups[0].absorbed] == [2, 3]


def test_release_at_is_arrival_plus_window(fake_clock):
    """解放時刻は未知version到着時刻+10秒(Clock基準・design §2.3)。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    iid = uuid.uuid4()
    d.submit(_entry("updated", iid, 2))
    fake_clock.advance(timedelta(seconds=9, milliseconds=900))
    assert d.poll() == []
    fake_clock.advance(timedelta(milliseconds=100))
    assert len(d.poll()) == 1


def test_same_version_resubmit_does_not_extend_window(fake_clock):
    """同一version再受信で解放時刻が延びない(窓の永久延長競合の排除 — design §2.3)。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    iid = uuid.uuid4()
    d.submit(_entry("updated", iid, 2))
    fake_clock.advance(timedelta(seconds=9))
    assert d.submit(_entry("updated", iid, 2)) is False  # 既知→非延長
    fake_clock.advance(timedelta(seconds=1))  # 到着から10秒(再受信から1秒)
    assert len(d.poll()) == 1  # 延長されていればここで解放されない


def test_resubmit_after_release_starts_new_window(fake_clock):
    """解放済み後の再受信は新規窓(version検査でstale破棄される経路 — design §2.3)。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    iid = uuid.uuid4()
    d.submit(_entry("updated", iid, 2))
    fake_clock.advance(timedelta(seconds=10))
    assert len(d.poll()) == 1
    assert d.submit(_entry("updated", iid, 2)) is True  # グループ削除後は新規


def test_groups_are_per_intent(fake_clock):
    """source_intent_idごとに独立した窓。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    a, b = uuid.uuid4(), uuid.uuid4()
    d.submit(_entry("updated", a, 1))
    d.submit(_entry("updated", b, 5))
    fake_clock.advance(timedelta(seconds=10))
    groups = d.poll()
    assert {g.intent_id for g in groups} == {a, b}


async def test_run_releases_and_acks_through_callback(fake_clock):
    """run()のtickループがClock操作だけで解放し、on_releaseへ渡す。"""
    released: list[DebounceGroup] = []
    d = _debouncer(fake_clock, released)
    iid = uuid.uuid4()
    d.submit(_entry("updated", iid, 2))
    stop = asyncio.Event()
    task = asyncio.create_task(d.run(stop=stop))
    await asyncio.sleep(0)  # runが待機に入るのを許す
    fake_clock.advance(timedelta(seconds=10))
    await asyncio.wait_for(_wait_len(released, 1), timeout=1.0)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


async def _wait_len(lst: list, n: int) -> None:
    while len(lst) < n:
        await asyncio.sleep(0.001)
```

`fake_clock` fixtureは `backend/tests/conftest.py` の既定初期時刻(2026-09-27 12:00 UTC)を使う(窓の判定はClock相対なので初期時刻の値は問わない)。

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_debounce.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.debounce'`)

- [ ] **Step 3: debounce.py を実装する**

`backend/src/latch/worker/debounce.py`:

```python
"""更新Eventのみのトレーリングdebounce(06 §9-1・design §2.3)。

窓は最適化であり正しさはversion検査+UNIQUE制約がDBで担保する(設計判断)。
解放時刻=未知version到着時刻(Clock.now())+window_sec。同一versionの
再提出は窓を延長しない(at-least-once再受信・フォールバックリレーの
再publishが窓を永久に延長する競合の構造的排除 — design §2.3)。
tickはasyncioの短周期ループでClock.now()と比較する(実時間を参照しない)。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from latch.core.clock import Clock
from latch.events import IncomingEvent

logger = logging.getLogger(__name__)


@dataclass
class DebounceEntry:
    """窓へ投入される1メッセージ(行確保済み — design §2.3の冪等受領)。"""

    event: IncomingEvent
    triple: tuple[str, uuid.UUID, int]
    row_id: uuid.UUID


@dataclass
class DebounceGroup:
    """窓解放時にon_releaseへ渡るグループ(最新version+吸収行)。"""

    intent_id: uuid.UUID
    latest: DebounceEntry
    absorbed: list[DebounceEntry] = field(default_factory=list)


@dataclass
class _Group:
    release_at: datetime
    entries: list[DebounceEntry] = field(default_factory=list)
    known_versions: set[int] = field(default_factory=set)


class TrailingDebouncer:
    def __init__(
        self,
        *,
        clock: Clock,
        window_sec: float,
        tick_sec: float,
        on_release: Callable[[DebounceGroup], Awaitable[None]],
    ) -> None:
        self._clock = clock
        self._window = timedelta(seconds=window_sec)
        self._tick_sec = tick_sec
        self._on_release = on_release
        self._groups: dict[uuid.UUID, _Group] = {}

    def submit(self, entry: DebounceEntry) -> bool:
        """True=新規作成/未知versionによる延長。False=既知version(何もしない)。"""
        _, intent_id, version = entry.triple
        group = self._groups.get(intent_id)
        if group is None:
            self._groups[intent_id] = _Group(
                release_at=self._clock.now() + self._window,
                entries=[entry],
                known_versions={version},
            )
            return True
        if version in group.known_versions:
            return False
        group.known_versions.add(version)
        group.entries.append(entry)
        group.release_at = self._clock.now() + self._window  # 未知version→延長
        return True

    def poll(self) -> list[DebounceGroup]:
        """解放時刻に達したグループを取り出す(削除して返す)。"""
        now = self._clock.now()
        released: list[DebounceGroup] = []
        due = [iid for iid, g in self._groups.items() if now >= g.release_at]
        for intent_id in due:
            group = self._groups.pop(intent_id)
            entries = sorted(group.entries, key=lambda e: e.triple[2])
            released.append(
                DebounceGroup(
                    intent_id=intent_id, latest=entries[-1], absorbed=entries[:-1]
                )
            )
        return released

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """tick周期でpollし、on_releaseを待つ(shutdownで窓内は破棄=未ack再配信)。"""
        while stop is None or not stop.is_set():
            await asyncio.sleep(self._tick_sec)
            for group in self.poll():
                try:
                    await self._on_release(group)
                except Exception:
                    logger.exception(
                        "debounce release failed intent_id=%s version=%s",
                        group.intent_id,
                        group.latest.triple[2],
                    )
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_debounce.py -v`
Expected: PASS(7件)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/worker/debounce.py backend/tests/unit/test_worker_debounce.py
git commit -m "feat: TrailingDebouncer(更新Eventの10秒トレーリング窓・再受信非延長)"
```

### Task 5: Stage1(第1段処理・stage1.py)

**Files:**
- Create: `backend/src/latch/worker/stage1.py`
- Test: `backend/tests/unit/test_worker_stage1.py`(Task 2のIncomingEvent試験へ追記)

**Interfaces:**
- Consumes: `IncomingEvent`(Task 2)・`Settings.event_retry_max`(Task 1)・`EVENT_*` 定数(intents/events.py・**変更しない**)・Clock・AsyncEngine
- Produces(Task 8のWorker配線が使用):
  - `EVENT_TYPES: frozenset[str]` = {created, updated, deleted, expired, scheduled, embedding_completed}(6値 — 05 §2。embedding_completedの定数追加はws-2のためstage1内ローカル文字列)
  - `BACKOFF_SEC: tuple[float, ...]` = (1.0, 2.0, 4.0, 8.0, 16.0)
  - `PayloadInvalid(Exception)` / `Retryable(Exception)`(Stage1Error基底)
  - `IntakeResult`(dataclass): `kind: str`("debounce" | "processed" | "duplicate" | "quarantined")/ `triple: tuple[str, uuid.UUID, int] | None` / `row_id: uuid.UUID | None` / `reason: str | None`
  - `Stage1(*, engine: AsyncEngine, clock: Clock, settings: Settings, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, embedding_hook: Callable[[str, uuid.UUID, int], Awaitable[None]] | None = None)`
    - `async intake(event: IncomingEvent) -> IntakeResult` — 検証(構造違反は再試行5回→quarantine直行)→ 行確保(ON CONFLICT DO NOTHING→SELECT FOR UPDATE)→ status≠pendingはduplicate → updatedは"debounce" → それ以外はprocessして完了
    - `async process(event: IncomingEvent, triple, row_id) -> str` — version検査・種別処理を1トランザクションで実行。失敗(Retryable・SQLAlchemyError)はバックオフ挟み再試行、上限で `_quarantine` し"quarantined"を返す。成功は"processed"
    - `async discard(row_id: uuid.UUID, *, reason: str) -> None` — debounce吸収行を processed+discard_reason で閉じる
    - `embedding_hook` は作成・更新Eventの処理位置に予約するフック(本単位ではNone可。実体はws-2 — design §2.5)

- [ ] **Step 1: 失敗するテストを書く(Stage1分を追記)**

`backend/tests/unit/test_worker_stage1.py` へ追記(ファイル末尾。import節へ追記):

```python
# -- Stage1本体(追記分。スタブconnで決定的に — design §4.1)--

import json
from datetime import UTC, datetime

from latch.core.clock import FakeClock
from latch.settings import Settings
from latch.worker.stage1 import (
    BACKOFF_SEC,
    PayloadInvalid,
    Stage1,
)

S1_NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


class FakeResult:
    """SQLAlchemy CursorResult風(タプル行・first()・rowcount)。"""

    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row


class ScriptedConn:
    """executeを順に仕込んだ結果で応え、呼び出し(SQL文字列・params)を記録する。"""

    def __init__(self, results: list):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = list(results)

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return FakeResult(None, 0)


class ScriptedEngine:
    """engine.begin() と同じ形(begin→conn)のスタブ。"""

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


class RecordingSleep:
    """即時返るsleep(バックオフの待機秒数を記録 — §2グローバル制約)。"""

    def __init__(self):
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def _event(event_type: str, iid, version, *, raw: bytes | None = None) -> IncomingEvent:
    if raw is not None:
        payload = raw
    elif version is None:
        payload = b'{"event_type": "%s", "source_intent_id": "%s"}' % (
            event_type.encode(),
            str(iid).encode(),
        )
    else:
        payload = b'{"event_type": "%s", "source_intent_id": "%s", "version": %d}' % (
            event_type.encode(),
            str(iid).encode(),
            version,
        )
    return IncomingEvent.from_payload(message_id=f"{event_type}-m", payload=payload, ack=_noop_ack)


def _noop_ack() -> None:
    pass


def _stage1(engine, clock=None, sleep=None, hook=None) -> Stage1:
    return Stage1(
        engine=engine,
        clock=clock if clock is not None else FakeClock(S1_NOW),
        settings=Settings(),
        sleep=sleep if sleep is not None else (lambda s: _await_none()),
        embedding_hook=hook,
    )


async def _await_none() -> None:
    return None


IID = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
ROW_ID = uuid.UUID("00000000-0000-4000-8000-0000000000bb")


def _claim_inserted() -> list:
    """claim挿入成功(RETURNING id が返る)→ intents version=1(=現行)。"""
    return [
        FakeResult((str(ROW_ID),)),      # INSERT ON CONFLICT DO NOTHING RETURNING id
        FakeResult(("pending",)),        # SELECT ... FOR UPDATE (行status)
        FakeResult((1,)),                # SELECT version FROM intents
        FakeResult(None, 1),             # UPDATE match_events → processed
    ]


def _sql(conn: ScriptedConn, i: int) -> str:
    return conn.calls[i][0]


async def test_intake_created_processes():
    """createdはdebounceを経ず即時処理(06 §9-1)。processed遷移SQLまで到達。"""
    engine = ScriptedEngine(_claim_inserted())
    stage1 = _stage1(engine)
    result = await stage1.intake(_event("created", IID, 1))
    assert result.kind == "processed"
    assert result.row_id == ROW_ID
    assert engine.begins == 2  # claim(1) + process(1)
    assert _sql(engine.conn, 1).startswith("SELECT status FROM match_events")  # FOR UPDATE


async def test_intake_updated_returns_debounce():
    """updatedは窓へ(処理しない)。claimのみ行う。"""
    engine = ScriptedEngine([FakeResult((str(ROW_ID),))])
    result = await _stage1(engine).intake(_event("updated", IID, 3))
    assert result.kind == "debounce"
    assert engine.begins == 1  # processしていない


async def test_intake_duplicate_skips_processing():
    """既存行のstatus≠pending→処理せずduplicate(重複受領の排除 — design §2.3)。"""
    engine = ScriptedEngine(
        [
            FakeResult(None, 0),           # INSERT ON CONFLICT DO NOTHING(競合)
            FakeResult((str(ROW_ID), "processed")),  # SELECT id, status FOR UPDATE
        ]
    )
    result = await _stage1(engine).intake(_event("created", IID, 1))
    assert result.kind == "duplicate"
    assert engine.begins == 1


async def test_intake_raw_poison_inserts_quarantined_row():
    """3点組抽出不能→5回再試行(バックオフ進行)→nil UUIDでquarantined行を作る。"""
    engine = ScriptedEngine([FakeResult((str(ROW_ID),))])
    sleep = RecordingSleep()
    raw = b'{"weird": true}'
    result = await _stage1(engine, sleep=sleep).intake(
        _event("updated", IID, None, raw=raw)
    )
    assert result.kind == "quarantined"
    assert sleep.seconds == list(BACKOFF_SEC)  # 1,2,4,8,16 — 06 §9-4
    params = engine.conn.calls[0][1]
    assert params["source_intent_id"] == uuid.UUID(int=0)  # nil UUIDで確実に行を作る
    assert params["status"] == "quarantined"
    assert "failure_reason" in params["payload"]  # json文字列に理由


async def test_intake_unknown_event_type_quarantined():
    """3点組可だが6値外のevent_type→構造違反経路(quarantine直行・design §2.4)。"""
    engine = ScriptedEngine([FakeResult((str(ROW_ID),))])
    sleep = RecordingSleep()
    result = await _stage1(engine, sleep=sleep).intake(_event("bogus", IID, 1))
    assert result.kind == "quarantined"
    assert sleep.seconds == list(BACKOFF_SEC)
    params = engine.conn.calls[0][1]
    assert params["event_type"] == "bogus"  # 受信値をそのまま保存
    assert params["status"] == "quarantined"


async def test_process_stale_version_discarded():
    """payload v < intents.version → processed + discard_reason=stale_version。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),   # 行status(FOR UPDATE)
            FakeResult((5,)),           # intents.version=5(受信は3)
            FakeResult(None, 1),        # UPDATE → processed
        ]
    )
    stage1 = _stage1(engine)
    outcome = await stage1.process(_event("updated", IID, 3), ("updated", IID, 3), ROW_ID)
    assert outcome == "processed"
    params = engine.conn.calls[2][1]
    assert json.loads(params["extra"]) == {"discard_reason": "stale_version"}
    assert "version" not in json.loads(params["extra"])  # UNIQUE索引列を壊さない


async def test_process_intent_missing_discarded():
    """参照先Intent不在→正当な遅延Eventとしてprocessed破棄(06 §9・design §2.4)。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult(None),           # intents行なし
            FakeResult(None, 1),
        ]
    )
    await _stage1(engine).process(_event("created", IID, 1), ("created", IID, 1), ROW_ID)
    params = engine.conn.calls[2][1]
    assert json.loads(params["extra"])["discard_reason"] == "intent_not_found"


async def test_process_version_ahead_reread_then_merge():
    """payload v > intents.version → FOR UPDATE再読込で一致すれば処理へ合流。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),           # 通常SELECTは v=1(受信は2)
            FakeResult((2,)),           # FOR UPDATE再読込で v=2(読み取り遅延)
            FakeResult(None, 1),        # processed
        ]
    )
    outcome = await _stage1(engine).process(_event("created", IID, 2), ("created", IID, 2), ROW_ID)
    assert outcome == "processed"
    assert "FOR UPDATE" in _sql(engine.conn, 2)  # 3本目のSQLが再読込


async def test_process_version_divergence_retries_then_quarantined():
    """乖離が続く限り再試行(5回)→quarantined+理由保持(06 §9-3・#6-4)。"""
    results = []
    for _ in range(6):  # 初回+再試行5回 = 6トランザクション × 3 SQL
        results += [
            FakeResult(("pending",)),
            FakeResult((1,)),           # intents.version=1(受信は9・乖離継続)
            FakeResult((1,)),           # FOR UPDATE再読込でも1
        ]
    results += [FakeResult(None, 1)]    # _quarantine_row の UPDATE(match_events)
    engine = ScriptedEngine(results)
    sleep = RecordingSleep()
    outcome = await _stage1(engine, sleep=sleep).process(
        _event("created", IID, 9), ("created", IID, 9), ROW_ID
    )
    assert outcome == "quarantined"
    assert sleep.seconds == list(BACKOFF_SEC)
    assert engine.begins == 7  # 6処理 + 隔離遷移1
    extra = json.loads(engine.conn.calls[-1][1]["extra"])
    assert "failure_reason" in extra  # 失敗理由をpayloadへ保持(05 §2)
    assert "version" not in extra  # UNIQUE索引列(payload->>'version')を壊さない


async def test_process_locked_row_not_pending_returns_without_side_effects():
    """process時の行status≠pending(二重受領)→副作用なく完了。"""
    engine = ScriptedEngine([FakeResult(("processed",))])
    outcome = await _stage1(engine).process(_event("created", IID, 1), ("created", IID, 1), ROW_ID)
    assert outcome == "processed"
    assert len(engine.conn.calls) == 1  # intents照会もUPDATEもしていない


async def test_process_deleted_closes_candidates():
    """deleted → match_candidatesの無効化SQL(status='closed'・06 §1)。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 0),        # 候補UPDATE(0件でも正常 — design §2.5)
            FakeResult(None, 1),        # processed
        ]
    )
    await _stage1(engine).process(_event("deleted", IID, 1), ("deleted", IID, 1), ROW_ID)
    close_sql = _sql(engine.conn, 2)
    assert "match_candidates" in close_sql and "closed" in close_sql
    assert engine.conn.calls[2][1]["intent_id"] == IID


async def test_embedding_hook_called_for_created_and_updated_only():
    """作成・更新EventでEmbeddingフックが呼ばれる位置を予約(ws-2 — design §2.5)。"""
    calls: list[tuple[str, uuid.UUID, int]] = []

    async def hook(event_type: str, intent_id: uuid.UUID, version: int) -> None:
        calls.append((event_type, intent_id, version))

    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    await _stage1(engine, hook=hook).process(
        _event("created", IID, 1), ("created", IID, 1), ROW_ID
    )
    assert calls == [("created", IID, 1)]

    engine2 = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 1),
        ]
    )
    await _stage1(engine2, hook=hook).process(
        _event("expired", IID, 1), ("expired", IID, 1), ROW_ID
    )
    assert len(calls) == 1  # expiredでは呼ばない


async def test_discard_marks_absorbed_row_processed():
    """debounce吸収行の閉包: processed + discard_reason(design §2.3)。"""
    engine = ScriptedEngine([FakeResult(None, 1)])
    await _stage1(engine).discard(ROW_ID, reason="debounced_superceded")
    params = engine.conn.calls[0][1]
    assert json.loads(params["extra"]) == {"discard_reason": "debounced_superceded"}


def test_mark_processed_extra_preserves_version_key():
    """理由追記のextraは version キーを含まない(payload->>'version'は不変 — design §2.4)。"""
    # stale/intent不在/discardの各試験の params 検証が本試験(重複定義せず
    # test_process_stale_version_discarded 等の assert で担保済み)。
    # ここでは BACKOFF_SEC と PayloadInvalid の公開値を固定する:
    assert BACKOFF_SEC == (1.0, 2.0, 4.0, 8.0, 16.0)
    assert issubclass(PayloadInvalid, Exception)
```

**追記するimportは実際に使うものだけ**(ruff F401対策。`json`・`datetime`・`pytest`・`FakeClock`・`Settings`・`BACKOFF_SEC`・`PayloadInvalid`・`Stage1`)。

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_stage1.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.worker.stage1'`)

- [ ] **Step 3: stage1.py を実装する**

`backend/src/latch/worker/stage1.py`:

```python
"""Matching Worker第1段(06 §9・design §2.3〜§2.5)。

行確保(ON CONFLICT DO NOTHING→SELECT FOR UPDATE)・version検査
(<現行=破棄 /=現行=処理 />現行=FOR UPDATE再読込)・種別処理・再試行
(5回・バックオフ)→quarantined。削除済みIntentへの参照Eventは正当な
遅延Eventとしてprocessed破棄、payload不正のみ隔離(06 §9・design §2.4)。
SQLはtext()生SQL・時刻はClock明示値(§2グローバル制約)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from latch.core.clock import Clock
from latch.events import IncomingEvent
from latch.intents.events import (
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_UPDATED,
)
from latch.settings import Settings

logger = logging.getLogger(__name__)

# 6値(05 §2)。embedding_completedの定数追加はws-2のためここでは文字列
_EVENT_EMBEDDING_COMPLETED = "embedding_completed"
EVENT_TYPES = frozenset(
    {
        EVENT_CREATED,
        EVENT_UPDATED,
        EVENT_DELETED,
        "expired",
        "scheduled",
        _EVENT_EMBEDDING_COMPLETED,
    }
)
BACKOFF_SEC: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
_NIL_UUID = uuid.UUID(int=0)

_INSERT_EVENT = text("""
    INSERT INTO match_events
        (event_type, source_intent_id, payload, status, created_at)
    VALUES
        (:event_type, :source_intent_id, CAST(:payload AS jsonb), :status,
         :created_at)
    ON CONFLICT DO NOTHING
    RETURNING id
""")
_SELECT_CLAIM = text("""
    SELECT id, status FROM match_events
    WHERE event_type = :event_type AND source_intent_id = :source_intent_id
      AND payload->>'version' = :version_text
    FOR UPDATE
""")
_LOCK_ROW = text("SELECT status FROM match_events WHERE id = :row_id FOR UPDATE")
_SELECT_INTENT = text("SELECT version FROM intents WHERE id = :intent_id")
_SELECT_INTENT_FOR_UPDATE = text(
    "SELECT version FROM intents WHERE id = :intent_id FOR UPDATE"
)
# 理由追記は payload || :extra(別キー追加)。payload->>'version' は不変(design §2.4)
_MARK_PROCESSED = text("""
    UPDATE match_events
    SET status = 'processed', processed_at = :now,
        payload = payload || CAST(:extra AS jsonb)
    WHERE id = :row_id AND status = 'pending'
""")
_MARK_QUARANTINED = text("""
    UPDATE match_events
    SET status = 'quarantined', processed_at = :now,
        payload = payload || CAST(:extra AS jsonb)
    WHERE id = :row_id AND status = 'pending'
""")
_CLOSE_CANDIDATES = text("""
    UPDATE match_candidates
    SET status = 'closed', updated_at = :now
    WHERE (intent_a_id = :intent_id OR intent_b_id = :intent_id)
      AND status <> 'closed'
""")


class Stage1Error(Exception):
    """Stage1内の処理失敗(再試行ループの対象)。"""


class PayloadInvalid(Stage1Error):
    """構造違反(version欠落・型不一致・6値外event_type — 永続的失敗)。"""


class Retryable(Stage1Error):
    """一時的失敗(version乖離の継続・読み取り遅延)。"""


@dataclass
class IntakeResult:
    kind: str  # "debounce" | "processed" | "duplicate" | "quarantined"
    triple: tuple[str, uuid.UUID, int] | None = None
    row_id: uuid.UUID | None = None
    reason: str | None = None


class Stage1:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        embedding_hook: Callable[[str, uuid.UUID, int], Awaitable[None]]
        | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._retry_max = settings.event_retry_max
        self._sleep = sleep
        self._embedding_hook = embedding_hook  # ws-2が実体を置く(design §2.5)

    # -- 受信入口 --

    async def intake(self, event: IncomingEvent) -> IntakeResult:
        """検証→行確保→分岐。構造違反は再試行5回→quarantine直行(10 §4.7)。"""
        triple: tuple[str, uuid.UUID, int] | None = None
        reason: str | None = None
        for attempt in range(self._retry_max + 1):
            try:
                triple = self._validate(event)
                break
            except PayloadInvalid as exc:
                reason = str(exc)
                logger.warning(
                    "stage1 invalid event message_id=%s attempt=%d reason=%s",
                    event.message_id,
                    attempt + 1,
                    reason,
                )
                if attempt < self._retry_max:
                    await self._sleep(BACKOFF_SEC[attempt])
        if triple is None:
            await self._quarantine_direct(event, reason=reason or "invalid payload")
            return IntakeResult(kind="quarantined", reason=reason)
        row_id, status = await self._claim(triple)
        if status != "pending":
            return IntakeResult(kind="duplicate", triple=triple, row_id=row_id)
        if triple[0] == EVENT_UPDATED:
            return IntakeResult(kind="debounce", triple=triple, row_id=row_id)
        outcome = await self.process(event, triple, row_id)
        return IntakeResult(kind=outcome, triple=triple, row_id=row_id)

    # -- 処理(debounce解放後・即時種別共通)--

    async def process(
        self, event: IncomingEvent, triple: tuple[str, uuid.UUID, int], row_id: uuid.UUID
    ) -> str:
        """1トランザクション処理を再試行ループで。成功=processed・上限=quarantined。"""
        reason: str | None = None
        for attempt in range(self._retry_max + 1):
            try:
                await self._process_once(triple, row_id)
                return "processed"
            except (Retryable, SQLAlchemyError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "stage1 process event_type=%s intent_id=%s attempt=%d failed: %s",
                    triple[0],
                    triple[1],
                    attempt + 1,
                    reason,
                )
                if attempt < self._retry_max:
                    await self._sleep(BACKOFF_SEC[attempt])
        await self._quarantine_row(row_id, triple, reason=reason or "retry exhausted")
        return "quarantined"

    async def discard(self, row_id: uuid.UUID, *, reason: str) -> None:
        """debounce吸収行のprocessed閉包(design §2.3)。"""
        async with self._engine.begin() as conn:
            await conn.execute(
                _MARK_PROCESSED,
                {
                    "row_id": row_id,
                    "now": self._clock.now(),
                    "extra": json.dumps({"discard_reason": reason}),
                },
            )

    # -- 内部 --

    def _validate(self, event: IncomingEvent) -> tuple[str, uuid.UUID, int]:
        triple = event.triple()
        if triple is None:
            raise PayloadInvalid(
                "unextractable triple (missing or invalid event_type/"
                "source_intent_id/version)"
            )
        if triple[0] not in EVENT_TYPES:
            raise PayloadInvalid(f"unknown event_type {triple[0]!r}")
        return triple

    async def _claim(
        self, triple: tuple[str, uuid.UUID, int]
    ) -> tuple[uuid.UUID, str]:
        """行確保(冪等受領 — design §2.3)。API経由は既存行・直投入はここでINSERT。"""
        event_type, intent_id, version = triple
        params = {
            "event_type": event_type,
            "source_intent_id": intent_id,
            "payload": json.dumps({"version": version}),
            "status": "pending",
            "created_at": self._clock.now(),
        }
        async with self._engine.begin() as conn:
            res = await conn.execute(_INSERT_EVENT, params)
            row = res.first()
            if row is not None:
                return uuid.UUID(row[0]), "pending"
            res = await conn.execute(
                _SELECT_CLAIM,
                {
                    "event_type": event_type,
                    "source_intent_id": intent_id,
                    "version_text": str(version),
                },
            )
            row = res.first()
            if row is None:
                raise Retryable("claim conflicted but row not found")
            return uuid.UUID(row[0]), row[1]

    async def _process_once(
        self, triple: tuple[str, uuid.UUID, int], row_id: uuid.UUID
    ) -> None:
        event_type, intent_id, version = triple
        now = self._clock.now()
        async with self._engine.begin() as conn:
            status = await self._lock_row(conn, row_id)
            if status != "pending":
                return  # 二重受領(条件UPDATE・FOR UPDATEで直列化)
            current = await self._intent_version(conn, intent_id, for_update=False)
            if current is None:
                # 削除済み等の不在=正当な遅延Event → processed破棄(06 §9)
                await self._mark(conn, _MARK_PROCESSED, row_id, "intent_not_found", now)
                return
            if version < current:
                await self._mark(conn, _MARK_PROCESSED, row_id, "stale_version", now)
                return
            if version > current:
                reread = await self._intent_version(conn, intent_id, for_update=True)
                if reread is None:
                    raise Retryable("intent vanished on reread")
                if reread != version:
                    raise Retryable(f"version divergence: event={version} current={reread}")
            # version == intents.version → 種別処理(§2.5)
            if event_type == EVENT_DELETED:
                await conn.execute(_CLOSE_CANDIDATES, {"intent_id": intent_id, "now": now})
            elif event_type in (EVENT_CREATED, EVENT_UPDATED):
                if self._embedding_hook is not None:
                    await self._embedding_hook(event_type, intent_id, version)
            # expired / scheduled / embedding_completed は処理実体なし(processed)
            await conn.execute(_MARK_PROCESSED, {"row_id": row_id, "now": now, "extra": "{}"})

    async def _lock_row(self, conn: AsyncConnection, row_id: uuid.UUID) -> str | None:
        res = await conn.execute(_LOCK_ROW, {"row_id": row_id})
        row = res.first()
        if row is None:
            raise Retryable("match_events row vanished")
        return row[0]

    async def _intent_version(
        self, conn: AsyncConnection, intent_id: uuid.UUID, *, for_update: bool
    ) -> int | None:
        stmt = _SELECT_INTENT_FOR_UPDATE if for_update else _SELECT_INTENT
        res = await conn.execute(stmt, {"intent_id": intent_id})
        row = res.first()
        return None if row is None else row[0]

    async def _mark(
        self, conn: AsyncConnection, stmt, row_id: uuid.UUID, reason: str, now: datetime
    ) -> None:
        await conn.execute(
            stmt,
            {
                "row_id": row_id,
                "now": now,
                "extra": json.dumps({"discard_reason": reason}),
            },
        )

    async def _quarantine_row(
        self, triple: tuple[str, uuid.UUID, int], row_id: uuid.UUID, *, reason: str
    ) -> None:
        """claim済み行の隔離遷移(失敗理由をpayloadへ保持 — 05 §2)。"""
        async with self._engine.begin() as conn:
            await conn.execute(
                _MARK_QUARANTINED,
                {
                    "row_id": row_id,
                    "now": self._clock.now(),
                    "extra": json.dumps({"failure_reason": reason}),
                },
            )

    async def _quarantine_direct(self, event: IncomingEvent, *, reason: str) -> None:
        """行なし受領(毒ペイロード)の隔離行INSERT(design §2.4)。

        3点組が組める場合はその3点組・組めない場合は受信生データを保存する
        (source_intent_id は nil UUID — 行挿入を確実にしUNIQUEのNULL重複許容
        は「複数の毒ペイロードを妨げない」意図どおり — design §2.4)。
        """
        triple = event.triple()
        now = self._clock.now()
        async with self._engine.begin() as conn:
            if triple is not None:
                event_type, intent_id, version = triple
                res = await conn.execute(
                    _INSERT_EVENT,
                    {
                        "event_type": event_type,
                        "source_intent_id": intent_id,
                        "payload": json.dumps(
                            {"version": version, "failure_reason": reason}
                        ),
                        "status": "quarantined",
                        "created_at": now,
                    },
                )
                if res.first() is not None:
                    return  # quarantined直行の挿入が成功
                # UNIQUE競合(過去の同一3点組)→ 既存行をquarantined遷移
                res = await conn.execute(
                    _SELECT_CLAIM,
                    {
                        "event_type": event_type,
                        "source_intent_id": intent_id,
                        "version_text": str(version),
                    },
                )
                existing = res.first()
                if existing is not None:
                    await conn.execute(
                        _MARK_QUARANTINED,
                        {
                            "row_id": uuid.UUID(existing[0]),
                            "now": now,
                            "extra": json.dumps({"failure_reason": reason}),
                        },
                    )
                return
            await conn.execute(
                _INSERT_EVENT,
                {
                    "event_type": (
                        event.data.get("event_type")
                        if isinstance(event.data.get("event_type"), str)
                        else "invalid"
                    ),
                    "source_intent_id": event.intent_id or _NIL_UUID,
                    "payload": json.dumps(
                        {"raw": event.data, "failure_reason": reason}
                    ),
                    "status": "quarantined",
                    "created_at": now,
                },
            )
```

**実装上の注意**:
- `_INSERT_EVENT` のstatusはパラメータ(claim=`'pending'`・quarantine直行=`'quarantined'`)。3点組可の毒(6値外event_type)がUNIQUE競合した場合は `_SELECT_CLAIM`→`_MARK_QUARANTINED`(status='pending'条件付き)で既存行を隔離へ
- `test_intake_raw_poison_inserts_quarantined_row` の `_event(..., None, raw=...)` はversionなしpayloadになる(3点組None経路)

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker_stage1.py -v`
Expected: PASS(4+14件。結果仕込みとSQL呼び出し順がずれる場合はStep 3の注意に従い試験の仕込みを結果順へ合わせる)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/worker/stage1.py backend/tests/unit/test_worker_stage1.py
git commit -m "feat: Stage1第1段処理(行確保・version検査・種別処理・再試行→quarantined)"
```

### Task 6: FallbackRelay(relay.py)

**Files:**
- Create: `backend/src/latch/events/relay.py`
- Modify: `backend/src/latch/events/__init__.py`(Task 3で予約した `FallbackRelay` のexportへ更新)
- Test: `backend/tests/unit/test_events_relay.py`

**Interfaces:**
- Consumes: `EventBus`(Task 2)・Clock・AsyncEngine・`Settings.event_fallback_*`(Task 1)
- Produces: `FallbackRelay(*, engine: AsyncEngine, clock: Clock, bus: EventBus, settings: Settings)`。`async run_once() -> int`(30秒超のpending行を再publishした件数を返す)。`async run(*, stop: asyncio.Event | None = None) -> None`(poll_sec周期でrun_once。例外は握って次周期へ)。main.pyのlifespan(Task 7)が起動する

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_events_relay.py`:

```python
"""フォールバックリレー(design §2.2-B)。

再publish対象が「created_atが一定時間(既定30秒)以上前のpending行」のみ
であること・publish例外で中断せず次行/次周期へ続くことを検証する。
"""

import uuid
from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.events.relay import FallbackRelay
from latch.settings import Settings

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
IID = uuid.UUID("00000000-0000-4000-8000-0000000000cc")


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


class StubBus:
    def __init__(self, fail: bool = False):
        self.published: list[tuple[str, uuid.UUID, int]] = []
        self.fail = fail

    async def publish_match_event(self, *, event_type, intent_id, version):
        if self.fail:
            raise RuntimeError("pubsub down")
        self.published.append((event_type, intent_id, version))


def _relay(engine, clock=None, bus=None) -> tuple[FallbackRelay, ScriptedConn]:
    relay = FallbackRelay(
        engine=engine,
        clock=clock if clock is not None else FakeClock(NOW),
        bus=bus if bus is not None else StubBus(),
        settings=Settings(),
    )
    return relay, engine.conn


async def test_republish_only_stale_pending_rows():
    """SELECTはstatus=pendingかつcreated_at < now-30秒。該当行をpublishする。"""
    engine = ScriptedEngine(
        FakeResult([("updated", IID, "3"), ("updated", IID, "4")])
    )
    bus = StubBus()
    relay, conn = _relay(engine, bus=bus)
    assert await relay.run_once() == 2
    sql, params = conn.calls[0]
    assert "pending" in sql and "created_at" in sql
    assert params["threshold"] == NOW - timedelta(seconds=30)  # しきい値=30秒
    assert bus.published == [("updated", IID, 3), ("updated", IID, 4)]


async def test_no_rows_means_no_publish():
    engine = ScriptedEngine(FakeResult([]))
    bus = StubBus()
    relay, _ = _relay(engine, bus=bus)
    assert await relay.run_once() == 0
    assert bus.published == []


async def test_publish_failure_is_swallowed():
    """publish例外で中断せず次行も試み、run_onceは正常終了(design §2.2-B)。"""
    engine = ScriptedEngine(FakeResult([("updated", IID, 3), ("updated", IID, 4)]))
    bus = StubBus(fail=True)
    relay, _ = _relay(engine, bus=bus)
    assert await relay.run_once() == 0  # 例外を外へ出さない
    assert len(bus.published) == 0
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_events_relay.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.events.relay'`)

- [ ] **Step 3: relay.py を実装する**

`backend/src/latch/events/relay.py`:

```python
"""フォールバックリレー(design §2.2-B)。

APIプロセス内で5秒周期に回し、created_at が既定30秒以上前のpending行のみ
を再publishする(publishはEventBusへ・SELECTはLIMIT付き・時刻はClock)。
通常処理(debounce 10秒+処理)はしきい値に到達しないため再publishゼロ。
APIのpublish失敗・Pub/Subメッセージ喪失・Worker長期停止を同一条件で回収。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.events.bus import EventBus
from latch.settings import Settings

logger = logging.getLogger(__name__)

_SELECT_STALE_PENDING = text("""
    SELECT event_type, source_intent_id, payload->>'version'
    FROM match_events
    WHERE status = 'pending' AND created_at < :threshold
    ORDER BY created_at
    LIMIT :limit
""")
_BATCH_LIMIT = 100


class FallbackRelay:
    def __init__(
        self, *, engine: AsyncEngine, clock: Clock, bus: EventBus, settings: Settings
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._bus = bus
        self._settings = settings

    async def run_once(self) -> int:
        threshold = self._clock.now() - timedelta(
            seconds=self._settings.event_fallback_relay_after_sec
        )
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _SELECT_STALE_PENDING, {"threshold": threshold, "limit": _BATCH_LIMIT}
            )
            rows = res.fetchall()
        published = 0
        for event_type, intent_id, version_text in rows:
            try:
                await self._bus.publish_match_event(
                    event_type=event_type,
                    intent_id=intent_id,
                    version=int(version_text),
                )
                published += 1
            except Exception:
                # publish失敗は握る(行はpendingのまま次周期で再対象 — design §2.2-B)
                logger.warning(
                    "relay publish failed event_type=%s intent_id=%s",
                    event_type,
                    intent_id,
                )
        return published

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        while stop is None or not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                # SELECT失敗(db一時障害)も握って次周期へ
                logger.warning("relay run_once failed", exc_info=True)
            await asyncio.sleep(self._settings.event_fallback_poll_sec)
```

- [ ] **Step 4: `events/__init__.py` をTask 3で予約した最終形へ更新する**

Task 3のStep 2に記載した最終形(relay importと `FallbackRelay` を含む)へ差し替える。

- [ ] **Step 5: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_events_relay.py -v && make lint && make test`
Expected: PASS(3件)+ lint/testともにexit 0

- [ ] **Step 6: Commit**

```bash
git add backend/src/latch/events/relay.py backend/src/latch/events/__init__.py backend/tests/unit/test_events_relay.py
git commit -m "feat: フォールバックリレー(30秒超のpending行を5秒周期で再publish)"
```

### Task 7: 発行側接続(service.py publish・main.py lifespan)

**Files:**
- Modify: `backend/src/latch/intents/service.py`
- Modify: `backend/src/latch/main.py`
- Test: `backend/tests/unit/intents/test_event_publish.py`(design §3.1一覧への追加 — uow外publishと握りのunit保証。既存 `tests/unit/intents/test_events.py`(outbox INSERT試験)は触らない)

**Interfaces:**
- Consumes: `EventBus`(Task 2)・`make_event_bus`(Task 3)・`FallbackRelay`(Task 6)
- Produces:
  - `IntentService.__init__` へのキーワード引数 `event_bus: EventBus | None = None`(既定None=従来どおり発行しない。既存unit試験は無影響)
  - `make_intent_service(*, clock, engine, limiter=None, event_bus=None)` への引数追加
  - `create_app` のapp.stateへ `event_bus`(注入済みならlifespanでの構築をスキップ — 既存のサービスごとの独立スキップ判定へ1項目)
  - lifespanが `app.state.event_relay_stop: asyncio.Event` と `app.state.event_relay_task` を設定(relay起動)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/intents/test_event_publish.py`(既存 `test_intents_service.py` と同じ流儀: StubStore/FakeCtx・`StructuredIntentInput.model_validate`・全フィールド明示のIntentRow):

```python
"""uowコミット後のpublish接続(design §2.2-B)。

publishはトランザクション外・失敗は握る・発行箇所とevent_typeは
M1実装のまま(insert_match_eventと同一条件)であることを検証する。
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from latch.core.clock import FakeClock
from latch.geo.service import Geofeature
from latch.intents import EVENT_CREATED, EVENT_DELETED, EVENT_UPDATED
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.service import IntentService
from latch.intents.store import IntentRow, UserRow

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
START = datetime(2026, 9, 28, 21, 0, 0, tzinfo=UTC)  # NOW+9h(active妥当値)
TENMONKAN = Geofeature(
    source="osm_poi", kind="amenity", name="天文館",
    city_name=None, pref_name=None, lon=130.5581, lat=31.5965,
)


class FakeConn:
    def __init__(self):
        self.executes = 0


class FakeCtx:
    """uow/reader用(engine.begin / engine.connect と同じ形のCallable)。"""

    def __init__(self, conn: FakeConn):
        self.conn = conn
        self.exited = 0  # __aexit__(=コミット)回数

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        self.exited += 1
        return False


class StubStore:
    """状態持ちスタブ(pause→resume→deleteの遷移を通す)。"""

    def __init__(self):
        self.next_id = uuid.uuid4()
        self.status = "active"
        self.version = 1

    async def fetch_user_row(self, provider, subject):
        return UserRow(id=USER_ID, birth_date=date(2000, 1, 1))

    async def fetch(self, conn, intent_id):
        return self._row()

    async def fetch_for_update(self, conn, intent_id):
        return self._row()

    async def insert(self, conn, cols, *, user_id, status, now):
        return self.next_id

    async def update(self, conn, intent_id, cols, *, status, version, now, expected_status):
        self.version = version
        return 1

    async def update_status(self, conn, intent_id, *, status, version, now, expected_status):
        self.status = status
        self.version = version
        return 1

    async def list_page(self, conn, user_id, *, status, before, limit):
        return []

    async def lock_user_row(self, conn, user_id):
        pass

    async def count_active(self, conn, user_id, *, now):
        return 0

    def _row(self) -> IntentRow:
        return IntentRow(
            id=self.next_id,
            user_id=USER_ID,
            category_primary="drinking",
            alcohol_involved=False,
            raw_text="原文",
            structured_data={"location_name": "天文館"},
            geo_radius_m=2000,
            budget_max=5000,
            participants_min=2,
            participants_max=4,
            visibility="hidden_until_match",
            notification_level="proposals_only",
            status=self.status,
            version=self.version,
            time_start=START,
            time_end=None,
            expires_at=None,
            created_at=NOW,
            updated_at=NOW,
        )


class StubGeocoder:
    async def geocode_forward(self, name: str):
        return TENMONKAN


class StubBus:
    def __init__(self):
        self.published: list[dict] = []

    async def publish_match_event(self, *, event_type, intent_id, version):
        self.published.append(
            {"event_type": event_type, "intent_id": intent_id, "version": version}
        )


class FailingBus(StubBus):
    async def publish_match_event(self, *, event_type, intent_id, version):
        raise RuntimeError("pubsub down")


def _service(bus, uow: FakeCtx) -> IntentService:
    return IntentService(
        clock=FakeClock(NOW),
        store=StubStore(),
        uow=uow,
        reader=FakeCtx(FakeConn()),
        geocoder=StubGeocoder(),
        event_bus=bus,
    )


def _active_input() -> StructuredIntentInput:
    return StructuredIntentInput.model_validate(
        {
            "category": {"primary": "drinking", "secondary": None},
            "alcohol_involved": False,
            "time": {"start": START.isoformat()},
            "location": {"name": "天文館"},
            "expires_at": (START + timedelta(hours=3)).isoformat(),
        }
    )


async def test_publish_after_commit_on_active_create():
    """active作成 → uowコミット後(event=created, version=1)を1回publish。"""
    bus, uow = StubBus(), FakeCtx(FakeConn())
    svc = _service(bus, uow)
    row = await svc.create(
        auth_provider="google", auth_subject="s1",
        raw_text="x", status="active", structured_intent=_active_input(),
    )
    assert bus.published == [
        {"event_type": EVENT_CREATED, "intent_id": row.id, "version": 1}
    ]
    assert uow.exited >= 1  # uow(コミット)の外でpublish


async def test_no_publish_for_draft_create():
    """draft作成は発行しない(06 §9-0)。"""
    bus, uow = StubBus(), FakeCtx(FakeConn())
    svc = _service(bus, uow)
    await svc.create(
        auth_provider="google", auth_subject="s1",
        raw_text="x", status="draft", structured_intent=None,
    )
    assert bus.published == []


async def test_publish_failure_is_swallowed():
    """publish失敗でもAPI応答は正常(例外にしない — design §2.2-B)。"""
    bus, uow = FailingBus(), FakeCtx(FakeConn())
    svc = _service(bus, uow)
    row = await svc.create(
        auth_provider="google", auth_subject="s1",
        raw_text="x", status="active", structured_intent=_active_input(),
    )
    assert row.status == "active"


async def test_publish_on_update_resume_delete_but_not_pause():
    """update/resumeはupdated・deleteはdeleted・pauseは発行なし(M1発行表)。"""
    bus, uow = StubBus(), FakeCtx(FakeConn())
    svc = _service(bus, uow)
    intent_id = uuid.uuid4()
    await svc.update(
        auth_provider="google", auth_subject="s1", intent_id=intent_id,
        raw_text="y", status=None, structured_intent=_active_input(),
    )
    await svc.pause(auth_provider="google", auth_subject="s1", intent_id=intent_id)
    await svc.resume(auth_provider="google", auth_subject="s1", intent_id=intent_id)
    await svc.delete(auth_provider="google", auth_subject="s1", intent_id=intent_id)
    types = [p["event_type"] for p in bus.published]
    assert types == [EVENT_UPDATED, EVENT_UPDATED, EVENT_DELETED]


async def test_make_intent_service_accepts_event_bus():
    import inspect

    from latch.intents.service import make_intent_service

    sig = inspect.signature(make_intent_service)
    assert "event_bus" in sig.parameters
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/intents/test_event_publish.py -v`
Expected: FAIL(`TypeError: IntentService.__init__() got an unexpected keyword argument 'event_bus'` 系)

- [ ] **Step 3: service.py へpublishを接続する**

`backend/src/latch/intents/service.py` へ次の変更を加える:

(1) import節へ追加(`from latch.intents.events import (...)` の直後あたり):

```python
from latch.events import EventBus
```

(2) モジュールにloggerが無ければ追加(import `logging` は既存):

```python
logger = logging.getLogger("latch.intents")
```

(3) `IntentService.__init__` の引数と代入へ1件ずつ追加(既存引数の末尾に):

```python
        event_bus: EventBus | None = None,
```

```python
        self._event_bus = event_bus
```

(4) クラスへ `_publish` ヘルパーを追加(`_row_from_cols` の直前あたり):

```python
    async def _publish(
        self, *, event_type: str, intent_id: uuid.UUID, version: int
    ) -> None:
        """uowコミット後のpublish(design §2.2-B)。失敗は握り、フォールバック
        リレー(30秒超のpending行)が回収する。publishはuowの外でのみ呼ぶ。"""
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish_match_event(
                event_type=event_type, intent_id=intent_id, version=version
            )
        except Exception:
            logger.warning(
                "event publish failed event_type=%s intent_id=%s version=%s",
                event_type,
                intent_id,
                version,
            )
```

(5) `_create` のactive分岐 — `async with self._uow() as conn:` ブロックの後(`return self._row_from_cols(...)` の前)へ挿入:

```python
            await self._publish(
                event_type=EVENT_CREATED, intent_id=intent_id, version=cols.version
            )
```

(draft分岐・`_row_from_cols` の引数はそのまま。`intent_id` はinsertの戻り値の変数名)

(6) `_update` — `async with self._uow() as conn:` ブロック内の分岐を次の形へ(下位メソッド `_update_draft` / `_update_active` の中身は無変更。戻り値の受け取りとpublishだけをuowの外で):

```python
        async with self._uow() as conn:
            row = await self._store.fetch_for_update(conn, intent_id)
            if row is None:
                raise IntentNotFoundError("intent not found")
            if row.user_id != user.id:
                raise ForbiddenError("not owner")
            target = status if status is not None else row.status
            if row.status == "draft":
                updated = await self._update_draft(
                    conn, row=row, target=target, raw_text=raw_text,
                    inp=structured_intent, user=user, now=now,
                )
                publish_type = EVENT_CREATED if target == "active" else None
            elif row.status in ("active", "paused"):
                updated = await self._update_active(
                    conn, row=row, target=target, raw_text=raw_text,
                    inp=structured_intent, user=user, now=now,
                )
                publish_type = EVENT_UPDATED
            else:
                raise InvalidTransitionError("intent is not editable")
        # uowコミット後にpublish(draft→active化のversion据え置きでも発行 —
        # insert_match_eventと同一条件・06 §9-0)
        if publish_type is not None:
            await self._publish(
                event_type=publish_type, intent_id=intent_id, version=updated.version
            )
        return updated
```

(7) `_transition` — `if event_type is not None:` のinsert_match_eventと `return replace(...)` の間で結果を受け、uowブロックを抜けた後にpublish:

```python
                if event_type is not None:
                    await insert_match_event(
                        conn,
                        event_type=event_type,
                        intent_id=intent_id,
                        version=new_version,
                        now=now,
                    )
                result = replace(
                    row, status=new_status, version=new_version, updated_at=now
                )
            # uowコミット後にpublish(pauseはevent_type=Noneのため対象外)
            if event_type is not None:
                await self._publish(
                    event_type=event_type, intent_id=intent_id, version=new_version
                )
            return result
```

(8) `make_intent_service` へ引数と渡しを追加:

```python
def make_intent_service(
    *,
    clock: Clock,
    engine: AsyncEngine,
    limiter: RateLimiter | None = None,
    event_bus: EventBus | None = None,
) -> IntentService:
```

```python
    return IntentService(
        clock=clock,
        store=IntentStore(engine),
        uow=engine.begin,
        reader=engine.connect,
        geocoder=GeoService(engine),
        limiter=limiter,
        event_bus=event_bus,
    )
```

- [ ] **Step 4: main.py のlifespanへEventBusとrelayを追加する**

`backend/src/latch/main.py` へ:

(1) importへ追加:

```python
import asyncio
from latch.events import FallbackRelay, make_event_bus
```

(2) `_lifespan` の冒頭のスキップ判定へ1項目:

```python
    build_events = not hasattr(app.state, "event_bus")
```

早退き条件の `if not (build_auth or ... or build_rate_limit):` へ `or build_events` を追加。

(3) engine構築の後(user_lookupより前でよい)へ:

```python
    event_relay_task = None
    event_relay_stop = None
    if build_events:
        # ci=エミュレータ・本番=実GCP(settings切替のみ — design §2.1)。
        # ensure(topic作成)は呼ばない: unit試験のlifespan(app fixtureがbus未注入)
        # で実GCPへのRPC接続待ちが発生するため。topicはWorker起動時のensureが
        # 作り、APIはpublish失敗(NotFound)を握ってフォールバックリレーが回収する
        event_bus = make_event_bus(settings)
        app.state.event_bus = event_bus
        app.state.intent_service = make_intent_service(
            clock=app.state.clock,
            engine=engine,
            limiter=app.state.rate_limiter if build_rate_limit else None,
            event_bus=event_bus,
        )
        relay = FallbackRelay(
            engine=engine, clock=app.state.clock, bus=event_bus, settings=settings
        )
        event_relay_stop = asyncio.Event()
        app.state.event_relay_stop = event_relay_stop
        event_relay_task = asyncio.create_task(relay.run(stop=event_relay_stop))
        app.state.event_relay_task = event_relay_task
    elif build_intents_crud:
        # event_bus注入済み(テスト)でもintent_service未構築なら構築する
        app.state.intent_service = make_intent_service(
            clock=app.state.clock,
            engine=engine,
            limiter=app.state.rate_limiter if build_rate_limit else None,
            event_bus=getattr(app.state, "event_bus", None),
        )
```

**注意**: 既存の `if build_intents_crud:` ブロック(旧来の `make_intent_service` 呼び出し)は、上の `build_events` 分岐で構築済みの場合に二重構築しないよう `if build_intents_crud and not build_events:` へ条件を変える。`elif build_intents_crud:` はevent_bus注入済みテスト(intent_service未注入)の互換のため残す。

(4) finally節へrelayの後始末を追加(engine.dispose()の前):

```python
        if event_relay_stop is not None:
            event_relay_stop.set()
        if event_relay_task is not None:
            event_relay_task.cancel()
            try:
                await event_relay_task
            except asyncio.CancelledError:
                pass
        if build_events:
            await app.state.event_bus.close()
```

- [ ] **Step 5: テストが通ること、既存試験が壊れないことを確認する**

Run: `make lint && make test`
Expected: ともにexit 0(既存intents試験は `event_bus=None` 既定で無影響。`test_app_health.py` 等のlifespan系unit試験もevent_bus未注入時はbuild_events=TrueでPubsubEventBus構築になる — **クライアント生成のみで接続しないため失敗しない**。もし既存試験がPubsubEventBus構築で失敗する場合は、当該試験のcreate_app呼び出しへ `event_bus=StubBus()` 系の注入を1行追加する対応を認める(報告ファイルへ記録)。relayの初回run_onceは即座に走るがDB接続失敗(Exception)は握られて次周期へ進む)

- [ ] **Step 6: Commit**

```bash
git add backend/src/latch/intents/service.py backend/src/latch/main.py backend/tests/unit/intents/test_event_publish.py
git commit -m "feat: コミット直後publishとフォールバックリレー起動をAPIへ接続"
```

### Task 8: Worker配線(worker/main.py)

**Files:**
- Modify: `backend/src/latch/worker/main.py`
- Modify: `backend/tests/unit/test_worker.py`(最小追従+配線試験)

**Interfaces:**
- Consumes: `Stage1`・`TrailingDebouncer`(Task 4・5)・`make_event_bus`・`create_db_engine`・Settings(clock/settingsシグネチャとgraceful shutdownは維持 — design §3.2)
- Produces: `Worker(clock=None, settings=None, *, bus=None, engine=None, stage1=None, debouncer=None, sleep=asyncio.sleep)`。`run()` は bus/engine/stage1/debouncer の未注入分を構築し、subscribe→dispatch(intake→debounce or process)→graceful shutdown(subscription.stop・debouncer停止・engine/busの後始末)。`_dispatch(event)` と `_on_release(group)` の規約:
  - intake結果 `processed` / `duplicate` / `quarantined` → `event.ack()`
  - intake結果 `debounce` → `debouncer.submit(entry)`:Trueはack保留(解放時にack)、False(既知version)は即ack(design §2.3)
  - 解放時: latestを `stage1.process` → ack、absorbedを `stage1.discard(reason="debounced_superceded")` → ack

- [ ] **Step 1: 失敗するテストを書く(test_worker.pyへ追記)**

`backend/tests/unit/test_worker.py` へ追記。まず既存3試験へ `bus=` を渡す最小追従(意図は不変): 各 `Worker(clock=...)` / `Worker()` を `Worker(clock=..., bus=_FakeBus())` / `Worker(bus=_FakeBus())` へ(`_FakeBus` は下記)。追記分:

```python
# -- 配線(M2 ws-1)。FakeBus+記録Stage1+実debouncer(run()内構成)で検証 --

import uuid
from datetime import timedelta

from latch.events import IncomingEvent
from latch.worker.stage1 import IntakeResult


class _FakeBus:
    """EventBusスタブ(subscribeを記録。ensure/closeも数える)。"""

    def __init__(self):
        self.subscribers: list = []
        self.ensured = 0
        self.closed = 0

    async def ensure(self):
        self.ensured += 1

    async def publish_match_event(self, *, event_type, intent_id, version):
        raise AssertionError("worker配線ではpublishしない")

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


class _RecordingStage1:
    """Stage1スタブ(intake/process/discardの呼び出しを記録)。"""

    def __init__(self, kind: str):
        self.kind = kind
        self.intakes: list[IncomingEvent] = []
        self.processed: list[tuple] = []
        self.discarded: list[uuid.UUID] = []

    async def intake(self, event):
        self.intakes.append(event)
        return IntakeResult(
            kind=self.kind, triple=event.triple(), row_id=uuid.uuid4()
        )

    async def process(self, event, triple, row_id):
        self.processed.append((triple, row_id))
        return "processed"

    async def discard(self, row_id, *, reason):
        self.discarded.append(row_id)


async def _started_worker(fake_clock, stage1):
    """run()を起動し(bus=FakeBus・stage1=スタブ)、subscribe・debouncer構築を待つ。"""
    from latch.worker.main import Worker

    worker = Worker(clock=fake_clock, bus=_FakeBus(), stage1=stage1)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.01)
    return worker, task


async def _stop(worker, task) -> None:
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=1.0)


async def test_dispatch_immediate_event_acks_after_intake(fake_clock):
    """created等の即時種別: intake完了後にack(順序含む — Review Focus 5)。"""
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker(fake_clock, stage1)
    try:
        event = _make_event("created", uuid.uuid4(), 1)
        await worker._dispatch(event)
        assert stage1.intakes == [event]
        assert event.ack_count == [1]  # intake完了後にack
    finally:
        await _stop(worker, task)


async def test_dispatch_duplicate_and_quarantined_ack_only(fake_clock):
    """duplicate/quarantinedはackのみ(処理副作用なし)。"""
    for kind in ("duplicate", "quarantined"):
        stage1 = _RecordingStage1(kind)
        worker, task = await _started_worker(fake_clock, stage1)
        try:
            event = _make_event("created", uuid.uuid4(), 1)
            await worker._dispatch(event)
            assert event.ack_count == [1], kind
            assert stage1.processed == [], kind
        finally:
            await _stop(worker, task)


async def test_dispatch_updated_debounce_release_acks_all(fake_clock):
    """updated: 受信時ack保留→窓解放でlatest処理+absorbed閉包→両方ack。"""
    stage1 = _RecordingStage1("debounce")
    worker, task = await _started_worker(fake_clock, stage1)
    try:
        iid = uuid.uuid4()
        e2 = _make_event("updated", iid, 2)
        e3 = _make_event("updated", iid, 3)
        await worker._dispatch(e2)
        await worker._dispatch(e3)
        assert e2.ack_count == [] and e3.ack_count == []  # 解放前はackしない
        fake_clock.advance(timedelta(seconds=10))
        await asyncio.sleep(0.2)  # debouncer tick(0.05秒)が解放を実行
        assert stage1.processed, "latest未処理"
        assert stage1.processed[0][0][2] == 3  # latest=v3(design §2.3)
        assert len(stage1.discarded) == 1  # v2は統合理由でprocessed閉包
        assert e2.ack_count == [1] and e3.ack_count == [1]
    finally:
        await _stop(worker, task)


async def test_run_starts_subscription_and_stops_gracefully(fake_clock):
    """run()がensure+subscribeを開始し、shutdownでsubscription.stopへ至る。"""
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker(fake_clock, stage1)
    assert worker.bus.ensured >= 1
    assert worker.bus.subscribers  # subscribe済み
    await _stop(worker, task)
    assert worker._subscription.stopped == 1
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker.py -v`
Expected: FAIL(`TypeError: Worker.__init__() got an unexpected keyword argument 'bus'` 系)

- [ ] **Step 3: worker/main.py を実装する**

`backend/src/latch/worker/main.py` を差し替え:

```python
"""Worker本体(design §3.1)。ci環境では api と同じイメージ・別プロセス(Worker 1)。

M2 ws-1: Pub/Sub subscriptionをストリーミングpullで消費し、第1段
(Stage1)+更新Eventのdebounce(TrailingDebouncer)へ配線する。ackは
DBのstatus遷移コミット後(design §2.3)。bus/engine/stage1/debouncerは
注入可能(unit試験)、未注入分はrun()で構築する。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from latch.core.clock import Clock, SystemClock
from latch.core.db import create_db_engine
from latch.events import EventBus, IncomingEvent, make_event_bus
from latch.settings import Settings
from latch.worker.debounce import DebounceEntry, DebounceGroup, TrailingDebouncer
from latch.worker.stage1 import Stage1

logger = logging.getLogger(__name__)

DEBOUNCE_TICK_SEC = 0.05  # 窓判定のtick(実時間の待機間隔。判定はClock基準)


class Worker:
    """継続実行の土台。Clock は起動時の1回の明示的構築(design §2.4 DI方式)。"""

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
    ) -> None:
        self.clock: Clock = clock if clock is not None else SystemClock()
        self.settings: Settings = settings if settings is not None else Settings()
        self._bus = bus
        self._engine = engine
        self._stage1 = stage1
        self._debouncer = debouncer
        self._sleep = sleep
        self._stop = asyncio.Event()

    def request_shutdown(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        owns_bus = self._bus is None
        owns_engine = self._engine is None
        if owns_bus:
            self._bus = make_event_bus(self.settings)
        bus = self._bus
        engine = (
            self._engine
            if self._engine is not None
            else create_db_engine(self.settings)
        )
        try:
            stage1 = (
                self._stage1
                if self._stage1 is not None
                else Stage1(
                    engine=engine,
                    clock=self.clock,
                    settings=self.settings,
                    sleep=self._sleep,
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
            await bus.ensure()
            self._subscription = await bus.subscribe(self._dispatch)
            debouncer_task = asyncio.create_task(debouncer.run(stop=self._stop))
            logger.info(
                "worker started (app_env=%s)", self.settings.app_env
            )
            await self._stop.wait()
            # graceful shutdown: 窓内entryは解放せず未ack再配信へ(design §2.3)
            if self._subscription is not None:
                self._subscription.stop()
            await debouncer_task
            logger.info("worker stopped")
        finally:
            if owns_engine and engine is not None:
                await engine.dispose()
            if owns_bus:
                await bus.close()

    # -- 配線(bus.callback→ここ。asyncioループ内で実行される)--

    async def _dispatch(self, event: IncomingEvent) -> None:
        try:
            result = await self._stage1.intake(event)
            if result.kind in ("processed", "duplicate", "quarantined"):
                event.ack()
                return
            if result.kind == "debounce":
                assert result.triple is not None and result.row_id is not None
                accepted = self._debouncer.submit(
                    DebounceEntry(
                        event=event, triple=result.triple, row_id=result.row_id
                    )
                )
                if not accepted:
                    # 既知versionの再受信: 窓を延長せず即ack(代表は最初のメッセージ)
                    event.ack()
                return
        except Exception:
            # ackせず終了 → ack_deadline後に再配信(at-least-onceの回収)
            logger.exception(
                "event dispatch failed message_id=%s", event.message_id
            )

    async def _on_release(self, group: DebounceGroup) -> None:
        """窓解放: latestを処理し、吸収行は統合理由でprocessed閉包(design §2.3)。"""
        try:
            await self._stage1.process(
                group.latest.event, group.latest.triple, group.latest.row_id
            )
            group.latest.event.ack()
            for entry in group.absorbed:
                await self._stage1.discard(
                    entry.row_id, reason="debounced_superceded"
                )
                entry.event.ack()
        except Exception:
            logger.exception(
                "debounce release failed intent_id=%s", group.intent_id
            )
```

`Worker` は `bus` プロパティ(注入/構築済みのbusを返す)と `_subscription`(subscribeの戻り)を持つ。run()内でbusを自前構築した場合は `self._bus = bus` を代入してから使用する(テストが `worker.bus` で参照できるようにするため):

```python
    @property
    def bus(self) -> EventBus:
        """注入/構築済みのbus(テスト・配線確認用)。"""
        assert self._bus is not None, "bus未構築(run()未実施・未注入)"
        return self._bus
```

`__init__` では `self._subscription = None` も初期化する。run()内のbus構築部は次の形にする:

```python
        owns_bus = self._bus is None
        if owns_bus:
            self._bus = make_event_bus(self.settings)
        bus = self._bus
```

**既存3試験への対応**: 既存試験は busを渡す形へ最小追従する(Step 1の冒頭に記載のとおり `Worker(clock=fake_clock, bus=_FakeBus())` 等)。bus未注入のままrun()すると `bus.ensure()` がエミュレータ不存在で失敗し得るため(クライアント生成自体は失敗しないがensureはRPCする)。「SystemClock既定」「注入Clockをそのまま使う」の意図はbus注入と直交するため維持される(design §3.2「最小追従・既存3試験の意図は維持」)。

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/test_worker.py -v && make lint && make test`
Expected: PASS(既存3件+追記4件)+ lint/testともにexit 0

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/worker/main.py backend/tests/unit/test_worker.py
git commit -m "feat: Workerへsubscribe・debounce・Stage1配線(ackはstatus遷移後)"
```

### Task 9: compose.yaml・Makefile

**Files:**
- Modify: `compose.yaml`
- Modify: `Makefile`

**Interfaces:**
- Consumes: Task 1で確認したエミュレータイメージ名・起動引数・Task 1の設定名(`LATCH_PUBSUB_EMULATOR_HOST`)
- Produces: composeの `pubsub` サービス(127.0.0.1:8085)・api/workerへのエミュレータ設定・`make test-ci` の「worker停止→pytest→worker復帰」(design §3.2・§4.2)。**実行検証はしない**(§0。`make -n test-ci` のドライランとYAML記載のみ)

- [ ] **Step 1: compose.yaml へpubsubサービスを追加する**

`redis:` サービスの直後に挿入(`services:` の直後・`api:` の前でもよい):

```yaml
  pubsub:
    # ci環境のMessage Queue具象(10 §1「本番と同じ種類で規模縮小」・04 §3選定=Pub/Sub)。
    # Pub/Subエミュレータ。10 §1原則によりRedis Streams等の代替は採らない
    # (design §2.1-A)。healthcheckはgRPCのみのため付けない(--waitはrunningで通る)。
    # 接続の確立は各プロセス側で再試行(publish失敗→リレー・ensure失敗→コンテナ再起動)
    image: gcr.io/google-cloudsdk/cloud-pubsub-emulator:latest
    command: ["gcloud", "beta", "emulators", "pubsub", "start", "--host-port=0.0.0.0:8085"]
    ports:
      - "127.0.0.1:8085:8085"
    restart: unless-stopped
```

※Task 1でミラー鏡像に替えた場合は `image:` をその鏡像にする(報告済みのもの)。起動引数はTask 1の公式ドキュメント確認結果に従い、異なっていれば修正する。

- [ ] **Step 2: api/worker へエミュレータ設定を追加する**

`api:` と `worker:` の `environment:`(workerは現状environmentがないため新設)へ追加。apiのenvironment:

```yaml
      LATCH_PUBSUB_EMULATOR_HOST: pubsub:8085
```

workerは `command:` の前に:

```yaml
    environment:
      LATCH_PUBSUB_EMULATOR_HOST: pubsub:8085
```

またapi・workerの `depends_on:` へ起動順としてpubsubを追加(healthcheckなしのためstarted順のみ):

```yaml
      pubsub:
        condition: service_started
```

- [ ] **Step 3: Makefile のtest-ciへworker停止/復帰を組み込む**

`test-ci:` のレシピを差し替え(design §3.2・§4.2 — 常設worker(SystemClock)とテストプロセス内Worker(FakeClock)が同一subscriptionを消費する競合の防止。pytestの成否にかかわらずworkerを復帰):

```make
test-ci: ## ci環境試験(worker停止→unit+integration→worker復帰。--group geoでosmium込み)
	docker compose up -d --wait
	docker compose stop worker
	cd backend && uv run --group geo pytest; rc=$$?; docker compose start worker; exit $$rc
```

`.PHONY` 行に変更なし(test-ciは既存掲載)。

- [ ] **Step 4: ドライランで確認する(composeは実行しない)**

```bash
make -n test-ci
```

Expected: 出力が `docker compose up -d --wait` → `docker compose stop worker` → `cd backend && uv run --group geo pytest; rc=$?; docker compose start worker; exit $rc` の順に展開されること。`make lint && make test` もexit 0(本タスクはコード変更なし)。

- [ ] **Step 5: Commit**

```bash
git add compose.yaml Makefile
git commit -m "feat: ci環境へPub/Subエミュレータ追加とtest-ciのworker停止/復帰"
```

### Task 10: integration試験(test_events_pipeline.py・作成のみ)

**Files:**
- Create: `backend/tests/integration/test_events_pipeline.py`

**Interfaces:**
- Consumes: compose常設api(127.0.0.1:8000)・db_engine fixture(tests/integration/conftest.py)・PubsubEventBus(Task 3・`subscription` 上書き引数と `publish_raw`)・Worker(Task 8)・Stage1/TrailingDebouncer・FakeClock
- Produces: design §4.2の8試験(スーパーバイザー検証時に `make test-ci` で実行)。**試験専用subscriptionの作成/削除**により前試験の残余メッセージ干渉を構造的に排除する(design §5-3のdrain実装の確定 — 新規subscriptionは作成以降のメッセージのみ受けるため空引き不要)

**本タスクは作成のみ**(§0。実行と証拠採取はスーパーバイザー検証時)。収集確認(`--collect-only`)までを実装側検証とする。

- [ ] **Step 1: 試験ファイルを作成する**

`backend/tests/integration/test_events_pipeline.py`:

```python
"""イベント駆動パイプラインのci環境実証(M2 ws-1 design §4.2)。

実HTTP(compose api)・実DB・実Pub/Subエミュレータ(127.0.0.1:8085)。
Worker処理はテストプロセス内Worker(bus=実エミュレータ・clock=FakeClock・
engine=実DB)が担う(design §4.2 — 常設workerはMakefileのtest-ciが停止済み)。
**実行はapi/workerイメージ再ビルド後の make test-ci のみ**(スーパーバイザー
検証時)。試験専用subscriptionを作成/削除し試験間の残余メッセージ干渉を
構造的に排除する(design §5-3: 新規subscriptionは作成以降の配信のみ受ける)。
"""

import asyncio
import os
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock

pytestmark = pytest.mark.integration

# テストプロセスからはport映射経由(コンテナ内はpubsub:8085 — compose.yaml)
os.environ.setdefault("LATCH_PUBSUB_EMULATOR_HOST", "127.0.0.1:8085")

from latch.events import PubsubEventBus  # noqa: E402(env設定後にimport)
from latch.settings import Settings  # noqa: E402
from latch.worker.main import Worker  # noqa: E402


async def _instant(_seconds: float) -> None:
    """バックオフ待機の即時化(再試行回数はログで担保 — §2グローバル制約)。"""
    return None


# -- 共通ヘルパ(test_intents_crud_api.py と同じ流儀)--

async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "latch.auth", "issue-idp-token",
        "--provider", provider, "--subject", subject,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
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
        "/v1/users", headers=headers,
        json={"display_name": "m2ws1", "birth_date": birth_date, "profile": {}},
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


# -- fixture: 試験専用subscription + テストプロセス内Worker --

class _WorkerEnv:
    def __init__(self, bus, clock, worker, task):
        self.bus = bus
        self.clock = clock
        self.worker = worker
        self.task = task


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
    worker = Worker(
        clock=clock, settings=settings, bus=bus, engine=db_engine, sleep=_instant
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)  # subscribe開始を待つ
    try:
        yield _WorkerEnv(bus=bus, clock=clock, worker=worker, task=task)
    finally:
        worker.request_shutdown()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
        await bus.close()
        bus.delete_subscription()  # 残余メッセージを次試験へ残さない


@pytest.fixture
async def user_env(api_client, db_engine):
    """subject実行ごと一意のuser。後始末でmatch_events・intent行を消す。"""
    subject = f"m2ws1-{uuid_mod.uuid4().hex[:12]}"
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
    return resp.json()


# -- §4.2の8試験 --

async def test_1_e2e_create_event_processed(api_client, db_engine, worker_env, user_env):
    """E2E(作成): POST→API publish→Worker受信→processed(3点組も検証)。#2・#13"""
    intent = await _create_active(api_client, user_env["headers"])
    row = await _wait_status(db_engine, "created", intent["id"], intent["version"])
    assert row[0] == "processed"
    assert row[1]["version"] == intent["version"]  # メッセージ内容(3点組)


async def test_2_debounce_merges_updates(api_client, db_engine, worker_env, user_env):
    """PATCH 2回→窓統合→最新versionのみ処理・全行processed。#6-1"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    headers = user_env["headers"]
    v2 = dict(_active_payload())
    v2["raw_text"] = "明日の夜 天文館でしっかり飲みたい"
    r2 = await api_client.patch(f"/v1/intents/{intent['id']}", headers=headers, json=v2)
    assert r2.status_code == 200, r2.text
    worker_env.clock.advance(timedelta(seconds=3))
    v3 = dict(_active_payload())
    v3["raw_text"] = "明後日の夜 天文館で軽く飲みたい"
    r3 = await api_client.patch(f"/v1/intents/{intent['id']}", headers=headers, json=v3)
    assert r3.status_code == 200, r3.text
    worker_env.clock.advance(timedelta(seconds=10))  # 窓解放
    row3 = await _wait_status(db_engine, "updated", intent["id"], 3)
    assert row3[0] == "processed" and "discard_reason" not in row3[1]
    row2 = await _fetch_event(db_engine, "updated", intent["id"], 2)
    assert row2[0] == "processed"  # 窓吸収行のprocessed閉包
    assert row2[1].get("discard_reason") == "debounced_superceded"


async def test_3_draft_to_active_emits_created(api_client, db_engine, worker_env, user_env):
    """draft作成(発行なし)→active化→作成種Eventが即時処理。#6-0"""
    headers = user_env["headers"]
    draft = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={"raw_text": "下書き", "status": "draft", "structured_intent": None},
    )
    assert draft.status_code == 201, draft.text
    intent_id = draft.json()["id"]
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text("SELECT count(*) FROM match_events WHERE source_intent_id = :i"),
            {"i": intent_id},
        )
        assert res.scalar() == 0  # draftは発行しない
    act = await api_client.patch(
        f"/v1/intents/{intent_id}",
        headers=headers,
        json=dict(_active_payload()),
    )
    assert act.status_code == 200, act.text
    row = await _wait_status(db_engine, "created", intent_id, act.json()["version"])
    assert row[0] == "processed"


async def test_4_delete_event_closes_candidates(api_client, db_engine, worker_env, user_env):
    """fixtureでmatch_candidates行を直接INSERT→DELETE→該当行がclosed。#7"""
    i1 = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", i1["id"], i1["version"])
    # 同一userの2つ目(pending回避のためactive上限内)
    i2 = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", i2["id"], i2["version"])
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO match_candidates"
                " (intent_a_id, intent_b_id, intent_a_version, intent_b_version,"
                "  status, created_at, updated_at)"
                " VALUES (:a, :b, :av, :bv, 'candidate', :now, :now)"
            ),
            {
                "a": i1["id"], "b": i2["id"],
                "av": i1["version"], "bv": i2["version"],
                "now": SystemClock().now(),
            },
        )
    resp = await api_client.delete(
        f"/v1/intents/{i1['id']}", headers=user_env["headers"]
    )
    assert resp.status_code == 204, resp.text
    row = await _wait_status(db_engine, "deleted", i1["id"], i1["version"])
    assert row[0] == "processed"
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT count(*) FROM match_candidates"
                " WHERE (intent_a_id = :i OR intent_b_id = :i) AND status <> 'closed'"
            ),
            {"i": i1["id"]},
        )
        assert res.scalar() == 0  # 候補はclosed(06 §1)


async def test_5_missing_intent_processed_discard(db_engine, worker_env):
    """存在しないintent_idのEvent→processed+discard_reason(隔離しない)。#8・#14"""
    ghost = uuid_mod.uuid4()
    await worker_env.bus.publish_match_event(
        event_type="created", intent_id=ghost, version=1
    )
    row = await _wait_status(db_engine, "created", ghost, 1)
    assert row[0] == "processed"
    assert row[1].get("discard_reason") == "intent_not_found"
    async with db_engine.begin() as conn:  # 後始末(直指定削除)
        await conn.execute(
            text("DELETE FROM match_events WHERE source_intent_id = :i"), {"i": ghost}
        )


async def test_6_poison_payload_quarantined(db_engine, worker_env, caplog):
    """毒ペイロード(version欠落)→再試行5回→quarantined+理由保持。後続滞らず。#8・#14"""
    await worker_env.bus.publish_raw(b'{"event_type": "updated"}')  # version欠落
    async with db_engine.connect() as conn:
        deadline = asyncio.get_event_loop().time() + 20.0
        while asyncio.get_event_loop().time() < deadline:
            res = await conn.execute(
                text(
                    "SELECT count(*) FROM match_events"
                    " WHERE status = 'quarantined' AND payload ? 'failure_reason'"
                )
            )
            if res.scalar() >= 1:
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail("poison payload not quarantined")
    # 再試行5回の確認(実ログ — 10 §4.7)
    assert sum(
        1
        for r in caplog.records
        if "stage1 invalid event" in r.getMessage() and "attempt=" in r.getMessage()
    ) >= 6  # 初回+再試行5回の警告ログ
    # 後続が滞らない: 正常Eventを続けて投入しprocessedになる
    normal = uuid_mod.uuid4()
    await worker_env.bus.publish_match_event(
        event_type="created", intent_id=normal, version=1
    )
    row = await _wait_status(db_engine, "created", normal, 1)
    assert row[0] == "processed"
    async with db_engine.begin() as conn:  # 後始末
        await conn.execute(
            text(
                "DELETE FROM match_events"
                " WHERE source_intent_id = :normal OR payload ? 'failure_reason'"
            ),
            {"normal": normal},
        )


async def test_7_duplicate_delivery_processed_once(db_engine, worker_env):
    """同一3点組2回publish→行1行・処理1回(2回目はackのみ)。#5・#14"""
    iid = uuid_mod.uuid4()
    await worker_env.bus.publish_match_event(
        event_type="created", intent_id=iid, version=1
    )
    await _wait_status(db_engine, "created", iid, 1)
    await worker_env.bus.publish_match_event(
        event_type="created", intent_id=iid, version=1
    )
    await asyncio.sleep(2.0)  # 2回目の受領を待つ
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT count(*) FROM match_events"
                " WHERE event_type='created' AND source_intent_id = :i"
                " AND payload->>'version' = '1'"
            ),
            {"i": iid},
        )
        assert res.scalar() == 1  # 二重生成なし(UNIQUE+行確保)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM match_events WHERE source_intent_id = :i"), {"i": iid}
        )


async def test_8_resume_update_event(api_client, db_engine, worker_env, user_env):
    """pause→resume→version+1のupdate種→debounce経由で処理。#9"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    headers = user_env["headers"]
    resp = await api_client.post(f"/v1/intents/{intent['id']}/pause", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await api_client.post(f"/v1/intents/{intent['id']}/resume", headers=headers)
    assert resp.status_code == 200, resp.text
    new_version = resp.json()["version"]
    assert new_version == intent["version"] + 1  # 05 §6・06 §9
    worker_env.clock.advance(timedelta(seconds=10))  # debounce窓解放
    row = await _wait_status(db_engine, "updated", intent["id"], new_version)
    assert row[0] == "processed"
```

**実装時の注意**:
- pause/resumeのルーティングパスは実装(resp)に合わせる。`POST /v1/intents/{id}/pause`・`/resume` でなければ `git grep -n "pause" backend/src/latch/intents/routes.py` で実際のpathを確認して合わせる
- `test_6` の後始末は `normal` の行と `payload ? 'failure_reason'` の毒ペイロード行を削除する(共有ci-db汚染回避 — 既存integration試験と同じ規約)
- caplogはpytest標準。workerプロセス(非コンテナ)のlogger `latch.worker.stage1` の警告を拾う。伝播設定に依存するため、検証時に拾えなければ `_wait_status` 同様のDBポーリングで quarantined 行を待つ検証を主とし、ログ確認は報告の補足とする(判断は報告ファイルへ記録)

- [ ] **Step 2: 収集確認する(実行はしない)**

```bash
cd backend && uv run pytest --collect-only tests/integration/test_events_pipeline.py -q
```

Expected: `8 tests collected`・exit 0

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_events_pipeline.py
git commit -m "test: イベントパイプラインE2E 8試験(作成のみ・実行はスーパーバイザー)"
```

### Task 11: 報告ファイル・完了条件検証

**Files:**
- Create: `docs/plans/M2/ws-1-report.md`

**Interfaces:**
- Consumes: 本計画§6の検証コマンド・§7の報告形式
- Produces: 報告ファイル(スーパーバイザーが検証・マージ時に使用)

- [ ] **Step 1: 完了条件§6の検証をすべて実行し結果を控える**

```bash
make lint && make test
cd backend && uv run pytest --collect-only tests/integration/test_events_pipeline.py -q
cd .. && rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
cd .. && git diff --stat main -- backend/alembic docs
git diff --name-only main | sort
git status --short
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
make -n test-ci
```

§6の各条件の期待値と突き合わせる(§4の一覧と差異があれば、ファイルを追加するか理由を報告書に書く)。

- [ ] **Step 2: 報告ファイルを§7の形式で作成する**

`docs/plans/M2/ws-1-report.md` を§7のテンプレートどおりに作成し、Step 1の証拠を貼る。design §5の実装時確認事項3件(エミュレータイメージ・初期値・drain実装)の結果を必ず記載する。

- [ ] **Step 3: 全体を検証してコミットする**

```bash
make lint && make test
git add docs/plans/M2/ws-1-report.md
git commit -m "docs: M2 ws-1の実行報告(完了条件7項目の証拠・test-ciは検証待ち)"
git status --short  # 空であること
```

- [ ] **Step 4: 最終返信**

報告ファイルのパスと完了条件7項目の結果一覧(項目2・7の実行部分は「test-ci=スーパーバイザー検証待ち」)を返す。

---

## Self-Review の記録(計画書作成時点の確認)

1. **Specカバレッジ**: design §2.1(エミュレータ→Task 1・3・9)・§2.2(publish+リレー→Task 6・7)・§2.3〜2.5(debounce・version検査・種別処理→Task 4・5・8)・§3.1/3.2(ファイル一覧→§4)・§4.1(unit表→Task 4〜8)・§4.2(integration 8試験→Task 10)・§5-1〜3(→Task 1・Task 1(設定値)・Task 10(試験専用subscription))に対応タスクを確認済み。design §1.4のスコープ外7項目は§5禁止へ反映
2. **プレースホルダ**: TBD/TODO/「後で決める」記述なし。Task 3(pubsub_busのunit試験なし)はdesign §4.1・§3.1の試験一覧に掲載がないための例外で、根拠と代替担保(integration)を明記済み
3. **型一貫性**: `IncomingEvent.triple()`(Task 2定義)をTask 4・5・8・10が同型で使用。`IntakeResult.kind` の4値と `Worker._dispatch` の分岐(Task 8)・`Stage1.process` の戻り "processed"/"quarantined"(Task 5)が整合。`DebounceEntry/DebounceGroup`(Task 4)をTask 8・10が使用
4. **Review Focus**: §3の5項目が所有タスクの試験に割り当て済み(各項目末尾に明記)
