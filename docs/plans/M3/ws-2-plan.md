# M3 ws-2(バッチ群)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 時間経由で状態を閉じるバッチ群を実装し、提示〜回答〜成立〜完了の状態機械の「出口」を完結させる — ①`worker/sweeper.py`新設: latches期限切れバッチ(expiry_sweeper・proposed/partial_accept×回答期限/Intent期限・candidate×期限切れ→expired・FOR UPDATE SKIP LOCKED+条件付きUPDATE・latch_status_events同時挿入)②Intent期限切れバッチ(draft/active/paused×expires_at経過→expired+expiredイベント発行・G1引継ぎ02#4本体)③matched→completed遷移(対象時刻経過・completed_at+実施自己申告通知の先行書き込みtype='attendance_request')④catch-upとの同一スケジューラ統合(ReevalRunnerへExpirySweeper注入・run_once先頭実行)⑤`worker/reset.py`新設: リセットジョブ(次JST 0時待機→前日キー掃除+月初月次→drain)⑥クローズ検知drain(latch_status_events観測→保留キュー再評価)。**マイグレーション追加なし・依存追加なし**。

**Architecture:** design §2.1案A — ExpirySweeperはReevalRunnerのrun_once先頭へ注入される独立クラス(60秒tickの1本化・切替停止は単一ジョブ)。各バッチは「SKIP LOCKED一括抽出(抽出txは即コミット)→行単位txの条件付きUPDATE→影響行数1のときlatch_status_events・expiredイベント・attendance通知を同一tx挿入」(design §2.2〜2.4・06 §6引用#3の直列化)。リセットジョブは60秒系とは別周期の独立task(次JST 0時まで待機・BackfillRunner同型のstop追従・例外時300秒再試行)。保留キュー再評価の実体は`latch_engine.drain()`の直接実行(`_drain`のpublic化のみ・評価経路のロジック無変更・design §2.6)。クローズ検知はsweeper tick第4処理としてlatch_status_eventsを観測(§2.7・supervisor承認事項③)。

**Tech Stack:** 変更なし(Python 3.13 / SQLAlchemy[asyncio]+asyncpg / Redis / pytest+fakeredis)。

**Spec:** `docs/plans/M3/ws-2-design.md`(agent1設計メモ。**未解決論点なし** — §5のsupervisor承認事項5件は2026-10-01承認済み・承認記録はSTATUS.md 297行目。①drain直接実行〔G3時確認候補〕②type='attendance_request'実装定義③クローズ検知drainのsweeper tick包含④統合スケジューラ遅延許容⑤Intent期限切れ時の候補closed化なし〔G3時確認候補〕。design.mdが本計画より優先)。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m3-ws-2`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(**依存追加・マイグレーション追加ともになし**のためlockもalembicも進まない)。
- **共有ci-db運用**(STATUS運用ルール1〜5): 本単位は**マイグレーションを追加しない**ため(design §4.3・alembic head=0005不変)、**agent3は `make test-ci` を実行してよい**(ws-1と同一条件・2026-09-30スーパーバイザー指示)。ただし**必ず `docker compose build api` を先行させる**(運用ルール4。compose.yamlのapiはbuild型・ソースマウントなし)。順序は `make lint && make test` → `docker compose build api` → `make test-ci`。`make up` / `make down` / `make migrate` / `docker build` / `docker pull` / `docker compose build worker` は実行しない(`make test-ci` 内部の `docker compose up -d --wait` と先行の `docker compose build api` は可。workerイメージはtest-ci内部で `docker compose stop worker` されるだけで本単位の検証に不要 — 試験はWorkerプロセスを立てず部品を直接構成する)。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-10-01)**: 現状 `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空(既存111ファイル)。本計画の新規3ファイル(`tests/unit/worker/test_sweeper.py`・`tests/unit/worker/test_reset_job.py`・`tests/integration/test_expiry_batches.py`)は既存と衝突しない(既存リストに同名なし・`test_expiry_options_api.py` はbasenameが異なる)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **`tests/unit/worker/` ディレクトリは新規作成するが `__init__.py` は置かない**(design §3.1注・prepend mode運用)。
- **alembic 0001〜0005は変更禁止**(design §3.3・マイグレーション追加なし): Task 10で `git diff main -- backend/alembic` が空であることを検証する。head=0005不変。
- **依存追加なし**: `backend/pyproject.toml`・`backend/uv.lock`・`Makefile`・`.env`・`.env.example` に新規依存・設定キーを追加しない。`settings.py` への追加は `sweeper_batch_limit`・`reset_retry_sec` の2キーのみ(design §3.2)。
- **並走単位に注意**(本単位はws-2 ∥ ws-3 ∥ ws-4 wave): 他単位のworktreeと共有ci-dbを取り合うため、`make test-ci` の実行は他単位のtest-ci実行と同時にしない(運用ルール1。マイグレーション追加単位のtest-ciと重なると "Can't locate revision" 系で失敗し得る。失敗した場合は報告書に「test-ci=再実行待ち」と記録してよい)。
- **固定値の遵守**: design §2 の採用判断(案A・SQL・drain直接実行)と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `refactor:` `chore:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| latches期限切れバッチの実行者はMatching Worker上の定期ジョブ`latch_expiry_sweeper`。周期60秒・時刻源はアプリ層のClock.now()(DBのclock_timestamp()は使わない) | 06 §6・引用#1 |
| 対象と遷移: `status IN ('proposed','partial_accept')`かつ`(response_deadline <= :now OR expires_at <= :now)` → expired。`status='candidate'`(保留キュー)かつ`expires_at <= :now` → expired | 06 §6・引用#2 |
| 取得はFOR UPDATE SKIP LOCKED。バッチと回答APIが同一行で競合してもUPDATE条件が同一の真実(期限とstatus)を参照するため不整合なし。期限切れバッチ遅延でstatusがproposedのままでも期限直後の回答は受理されない(回答APIの条件付きUPDATEが構造的排除) | 06 §6・引用#3・C9 |
| Intent側の期限切れバッチと同一のスケジューラで動かし、切替・停止は単一ジョブとして管理 | 06 §6・引用#4 |
| Intent遷移: active・paused→expiredはexpires_at経過。draft→expiredは「active化されないまま期限が切れた下書き」 | 05 §6・引用#5 |
| idx_intents_expiresの部分索引条件と抽出SQLのWHEREを同一にする(`status IN ('draft','active','paused') AND expires_at <= :now`) | 05 §3・引用#6 |
| catch-upはexpiry_sweeperと同一の60秒周期スケジューラ・追加のジョブ管理を発生させない | 06 §9・引用#7 |
| リセットジョブ: 日次カウンタはJST 0時・月次カウンタは暦月初JST 0時の**同一ジョブ**でリセット(FR-50)。TTL方式はUTC基準で9時間ずれるため用いない。ClockのJST日付境界を参照しテスト環境ではClock操作で0時・月初を再現 | 04 §5・引用#9 |
| リセットジョブ失敗時は、最初のJev実行要求でカウンタの日付キーを検査し前日以前のキーを検知した場合にリセット(自己修復)。ジョブの単一障害点を補う | 04 §5・引用#10 |
| 0時リセット完了時に保留キュー再評価イベントを発行。クローズ検知はlatch_status_eventsへの挿入と同一トランザクションで確定(取りこぼしない観測点) | 06 §10・引用#11・C10 |
| キュー再評価の差分化(FR-03): 変化のない候補は提示順の再計算のみ(Jev再実行しない)。大量保留時は提示順に沿って上限内の件数のみ処理し残りは次のトリガーへ | 06 §10・引用#12 |
| latch_status_events: 遷移を書くtxと同時に挿入。user_idはシステム起因(sweeper等)はNULL | 05 §2・06 §10・引用#13・C10 |
| 保留中のcandidateはexpiry_sweeperがexpires_at経過でexpiredにする | 06 §10・引用#14 |
| matched→completed: 対象時刻(time_start)の経過。遷移時に実施自己申告の通知を送る。cancelled LATCH(解散済み)には申告通知を送らない | 05 §6・引用#15 |
| D-09: completed遷移時点でプッシュ+アプリ内通知により1問自己申告。回答期限3日・スキップ可。通知の先行書き込みは`payload={latch_id}`最小参照・媒体はM3-5(M2 ws-6承認構成) | 09 D-09・05 §2・引用#16 |
| Jevカウンタの鍵: `jev:daily:{yyyymmdd}`(TTL 48h)・`jev:monthly:{yyyymm}`(45日)ほか。TTLは掃除用に留めリセット表現には使わない(リセット=日付キー切替) | 04 §5・引用#18・C12 |
| イベント種: expiredはevent_type 6値の正規の一員。`EVENT_EXPIRED`はM1 ws-3で定義済み(「発行経路はM3-3」)・Stage1は処理実体なし(processed)として受理済み→**stage1無変更** | 05 §2・06 §9・引用#19 |
| 期限切れ・削除されたIntentを含むlatchesはsweeperと削除Event処理(ws-1実装)がそれぞれ閉じる | 06 §6・引用#20 |
| expiry_sweeperがlatchesをexpiredにする際の通知規定なし(不成立の表示は画面側の一文統一・API・通知は種別理由を開示しない)→**notificationsを作らない・Calibrationも作らない** | 05 §6・03 §7・引用#22・design §2.2 |
| 02#4: 「expires_atを指定して保存し、期限経過後の状態を確認する → Intentがexpiredに遷移」。時刻操作はClock(10 §1)。G1裁定(a): 期限経過後expired遷移はM3-3実装時に確認 | 02 §3・STATUS G1・引用#21 |
| 待機・周期のテスト再現: FakeClockのset()/advance()。すべての時刻参照はClock経由(arch test強制) | 10 §1・C2・引用#23 |
| 保留キュー再評価イベントの実体=drain直接実行(イベント不発行)。クローズ検知drainはsweeper tickへ包含 | design §2.6・§2.7・supervisor承認①③ |
| attendance通知のtype値`'attendance_request'`は実装定義 | design §2.4・supervisor承認② |
| 統合スケジューラの遅延(tick処理時間+60秒)は許容。正しさへの影響なし | design §2.1・supervisor承認④ |
| Intent期限切れ時にmatch_candidates/group_candidatesをclosed化しない(Layer 1のstatus='active'条件とH再検証で自然無力化) | design §2.3・supervisor承認⑤ |
| 既存資産の再利用(ReevalRunner・BackfillRunner定型・latch_engineのdrain/try_promote・JevCostStore・Clock.jst_date・insert_match_event) | design §1.3 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **時刻はClock経由のみ**。製品コード(`backend/src/latch/`)で実時間参照禁止(arch test `test_arch_no_direct_time.py` が強制)。sweeperのrun_onceは**1tick=1時刻**: 冒頭で `now = self._clock.now()` を1回採取し、抽出・UPDATE・イベント挿入のすべてで同一の値を使い回す(design §2.2)。ResetJobのrun_onceは `self._clock.jst_date()` でJST暦日付を得る。
- **永続化はtext()生SQLのみ**。SQLAlchemy ORMは導入しない。asyncpgのUUID復元は `_coerce_uuid` 規律、uuid[]のbindは `list[uuid.UUID]` を渡してSQL側で `CAST(:x AS uuid[])`(文字列リテラルはasyncpgが拒否・ws-7検証済み)。
- **1tick=1時刻の抽出とUPDATEの同値性**: 抽出と行単位UPDATEは同じ `now` をbindする(抽出と実行の間に期限が動かない・design §2.2)。
- **latch_status_events・match_events・notificationsは遷移と同一txで挿入**(C10・引用#13)。システム起因(sweeper・ResetJob)のlatch_status_events.user_idはNULL。
- **candidate行の期限判定にresponse_deadlineを使わない**(引用#2・暫定値のため)。candidateはexpires_atのみ。
- **latchesの期限切れ処理でnotifications・Calibrationを作らない**(引用#22・design §2.2)。notificationsの新規書き込みはcompleted遷移時のattendance_requestのみ。
- **評価経路のロジックは無変更**(design §1.3): `latch_engine.py` の変更は `_drain()`→`drain()` のpublic化(改名)のみ。try_promote・_DRAIN_CANDIDATES・_COUNT_* などには触れない。
- **`worker/stage1.py`・`latches/`・`worker/matching/group_engine.py`・layer1〜4・runner・origin・jev・`worker/cost/guard.py` は無変更**(design §3.3)。
- **例外・ログに機微を入れない**(08 §2.4)。sweeperのログは `sweeper.expired latch_id=%s` 形式のIDログのみ(§9-13)。
- **例外は握らない**: ExpirySweeper.run_once・ResetJob.run_onceから伝播し、呼び出し側(ReevalRunner.run・ResetJob.run)が握る(Backfill/Reevalと同一契約)。
- **`make lint`(ruff E,F,I,UP,B・format行長88)と `make test` を毎コミット通す**。unit試験は外部プロセス不要・実時間待ちなし(スタブ注入・FakeClock・fakeredis)。
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **SKIP LOCKED抽出後に回答API・並行sweeperが先行して行を変えた**(引用#3) — 抽出と行単位UPDATEの間の競合。条件付きUPDATEの影響行数0ならイベントを挿入せず無視(次回に該当なし)。影響0時にイベント挿入すると架空の遷移記録が残る → Task 6 unit(`test_expire_latch_no_row_no_event`)+Task 9試験6(並行run_onceでlatch_status_eventsのexpired遷移が行ごとに1行のみ)
2. **candidate行の暫定response_deadline(過去)を期限判定に使う**(引用#2違反) — candidate作成時のdeadlineは昇格時への暫定値。これを判定に入れると期限内の保留行を誤って破棄する → Task 5のSQLピン(`_EXPIRE_CANDIDATE_LATCH` に `response_deadline` が現れないこと)+Task 9試験2⑤(candidate×deadline暫定過去・expires未来は不変)
3. **completed遷移がcancelled(解散済み)へ通知する/時間前のmatchedをcompletedにする**(引用#15/#16違反) — WHERE `status='matched'` の書き漏らし・time_start経過判定の誤り → Task 5のSQLピン(`AND status = 'matched'`・`max(i.time_start) <= :now`)+Task 9試験5(cancelled行に通知なし・time_start経過前はTask 9試験1の対象外確認で担保)
4. **last_tickの更新順序の誤りでクローズを取りこぼす/再帰drainする**(§2.7) — run_once例外時にlast_tickを進めると観測窓に隙間ができる(取りこぼし)。逆にtick内で「drain→観測」の順にすると自分のdrainのイベントを拾って毎tick無条件drain化する → Task 6 unit(例外時last_tick不変・処理順序「書き込み→観測→drain」の呼び出し順ピン)
5. **Intent期限切れだけ行われて参加candidateのlatchが閉じ残る**(引用#20) — Intent期限切れバッチとlatches期限切れバッチは別処理。latches.expires_at(参加Intent期限の最小値)も同tickで切れていればlatches側も閉じることを全体で確認しないと「片方だけ閉じる」状態が見過ごされる → Task 9試験1(参加candidate行を仕込み、同一run_onceでintentもlatchもexpired)

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1):

```text
backend/src/latch/worker/sweeper.py                       (Task 5〜6。ExpirySweeper)
backend/src/latch/worker/reset.py                         (Task 3〜4。ResetJob+next_jst_midnight)
backend/tests/unit/worker/test_sweeper.py                 (Task 5〜6)
backend/tests/unit/worker/test_reset_job.py               (Task 3〜4)
backend/tests/integration/test_expiry_batches.py          (Task 9。design §4.2の試験群)
docs/plans/M3/ws-2-report.md                              (Task 10。報告ファイル)
```

変更(design §3.2):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/worker/matching/latch_engine.py` | `_drain()` を `drain()` へpublic化(812行の定義改名+568行の呼び出し置換+docstring追記。ロジック無変更) | 1 |
| `backend/tests/unit/matching/test_latch_engine.py` | 952行・969行の `._drain()` を `.drain()` へ機械的置換(期待値変更ではなく呼び出し先改名) | 1 |
| `backend/src/latch/worker/cost/store.py` | 掃除メソッド3つ追加(`delete_daily`・`delete_monthly`・`scan_delete`)。INCR系・scan_report無変更 | 2 |
| `backend/tests/unit/cost/test_cost_store.py` | 掃除メソッドの試験4件を末尾へ追記 | 2 |
| `backend/src/latch/worker/reeval.py` | `__init__` へ `sweeper=None` オプション追加・`run_once()` 先頭で `sweeper.run_once()`(Noneなら従動作)・docstringの統合前提注記を現状説明へ更新 | 7 |
| `backend/tests/unit/test_worker_reeval.py` | 注入あり(先行実行の順序)・注入なし(従動作)の2件を追記 | 7 |
| `backend/src/latch/settings.py` | `sweeper_batch_limit: int = 50`・`reset_retry_sec: int = 300` 追加 | 8 |
| `backend/tests/unit/test_settings.py` | `test_settings_defaults` へ2キーのassertを追記 | 8 |
| `backend/src/latch/worker/main.py` | ExpirySweeper構築→ReevalRunnerへ注入・ResetJob構築→定期task追加+graceful shutdownのawait追加 | 8 |

`backend/tests/unit/worker/` ディレクトリは新規作成するが `__init__.py` は置かない(§0のとおり)。

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`。

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- **docker系のうち `make up` / `make down` / `make migrate` / `docker build` / `docker pull` / `docker compose build worker`**: §0のとおり。api再ビルド(`docker compose build api`)と `make test-ci`(内部の `docker compose up -d --wait` を含む)は実行可・報告書に結果を記録
- **実API呼び出し**: `make g1-gate`・`make g2-gate`・`make embed-smoke`・`make jev-smoke` は起動しない(本単位は外部SDKなし・LLM Gatewayに触らない)
- **design §3.3の禁止**: `backend/src/latch/` 配下の auth / users / intents / ratelimit / geo / core / events / llm / g1gate / g2gate / latches 各モジュール。`worker/` の既存ファイルのうち変更対象以外(`worker/__main__.py`・`worker/main.py`(Task 8の配線分を除く)・`embedding.py`・`embedding_text.py`・`debounce.py`・`backfill.py`・`jev.py`・`stage1.py`・`cost/guard.py`・`cost/reeval.py`・`cost/errors.py`・`cost/__init__.py`)。`worker/matching/` のうち変更対象以外(`candidates.py`・`group_calc.py`・`latch_calc.py`・`latch_engine.py`(Task 1のpublic化を除く)・`group_engine.py`・`layer1.py`〜`layer4.py`・`origin.py`・`proposal.py`・`runner.py`)。`backend/alembic/`(0001〜0005不変・追加もしない)。`compose.yaml`・`docker/`・`frontend/`・`prototype/`。`backend/pyproject.toml`・`backend/uv.lock`(依存追加なし)。`.env`・`.env.example`。`docs/`(01〜12・learn・testassets)。`docs/plans/STATUS.md`(スーパーバイザー管理)。`docs/plans/` の既存ファイル(M0/M1/M2/M3のdesign・plan・report)。`backend/tests/` の§4に列挙した以外の既存試験
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4・§2.8):
  - **FCM実送信・お知らせAPI・D-05回答期限の通知・D-08の通知面・attendance通知の媒体確定・読み取り** → ws-3(本単位はnotifications先行書き込み〔type・payload={latch_id}〕まで)
  - **実施自己申告の回答API(attendance・3日以内の受付)** → ws-4(actual_attendedはNULLのまま・cancelledには通知しないの送信面は構造的に本単位が担保)
  - **30日定期削除・calibration匿名化** → ws-6
  - **02#16(期限切れ処理)のstaging E2E・#13〜#23** → ws-9(本単位は02#4の本体と#16相当の遷移単体をciで担保)
  - **Workerの水平スケール(複数プロセスでのsweeper分散)** → SKIP LOCKEDにより将来安全だが試験範囲はWorker 1構成(design §1.4)
  - **match_candidates/group_candidatesのclosed化・match_eventsへの「保留キュー再評価」イベント種の新設・sweeper用latch_status_events Index・リセットジョブの遡及実行・DBベースのジョブ実行履歴・completed時のIntent側追加遷移・Stage1のexpired波及** → design §2.8の切り捨て一覧どおり
  - **統合スケジューラの案B(独立task)への分割** → 構成変更なしで後から可能なため本単位では作らない(supervisor承認④)
  - **catch-up・Bucket再評価・30分ガードのロジック** → ReevalRunner既存実装のまま(本単位は注入点のみ)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 10で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分・既存試験の追従を含む)
   検証: `make lint && make test` — ともにexit 0。全件数を報告書に記録(2026-10-01時点のmainは unit 1062件)
2. **`docker compose build api` 後の `make test-ci` がグリーン**(既存1253+新規integrationを含む)
   検証: `docker compose build api` exit 0 → `make test-ci` exit 0。報告書に全件数を記録
3. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
4. **マイグレーション・依存に差分なし**
   検証: `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock Makefile` — 出力なし(空)
5. **変更ファイルが§4の一覧どおり(作成6+変更9=15ファイル)**
   検証: Task 10の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
6. **実時間参照が `core/clock.py` のみ**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
7. **既存unit試験がすべてグリーンのまま**(test_latch_engine.py 952/969の `_drain`→`drain` 置換が呼び出し先改名のみであることの証明として `make test` の全件数を報告書へ記録し、§4列挙以外の期待値変更がないことを§4との突合で示す)

## 7. 報告形式

**結果ファイル**: `docs/plans/M3/ws-2-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M3 ws-2(バッチ群)実行報告

- ブランチ: m3-ws-2 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も)> |
| 2 | docker compose build api → make test-ci | PASS/FAIL | <build完了とtest-ci全件数> |
| 3 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 4 | alembic・依存・Makefile無変更 | PASS/FAIL | <git diff出力(空なら「空」)> |
| 5 | 変更ファイル=§4の15ファイル | PASS/FAIL | <git diff --name-only出力> |
| 6 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 7 | 既存unit試験グリーン維持 | PASS/FAIL | <make test 全件数と§4列挙以外の期待値変更がないことの確認結果> |

## design §5 実装時確認事項の結果
- (なし — design §5の5件はすべて2026-10-01承認済み。本計画§9のIF確定事項から
  変更した場合は下に「変更前→変更後+理由」を記録する)

## 固定値の変更有無(design.md §2・本計画§9)
- SQL定数と抽出・行単位tx構成(§9-2/7): 変更なし / 変更あり
- run_once戻り値=遷移行数(§9-1): 変更なし / 変更あり
- ResetJob run_once直呼びによる0時再現(§9-4/5): 変更なし / 変更あり
- その他§9のIF確定事項: 変更なし / 変更あり(<前→後+理由>)

## (G3・ws-3〜ws-4への引継ぎ)
- ws-3: attendance_request通知は notifications.type='attendance_request'・
  payload={"latch_id"} で先行書き込み済み(読み取り・送信はws-3)
- ws-4: attendance回答APIはcancelledには通知しない点をcompleted側通知で担保済み
- G3時確認候補(design §5): ①drain直接実行 ⑤候補closed化なし

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

**検証手順(design §4.3。報告書の完了条件2に対応)**:

1. `make lint && make test` — unit全件グリーン
2. `docker compose build api` — **test-ciの前に必ず先行**(STATUS運用ルール4)
3. `make test-ci` — 既存1253+新規integrationがグリーン
4. basename一意確認(運用ルール5): `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることを報告書に記録
5. lint再実行(test-ciにlintが含まれないため・M2 ws-4運用メモ): `make lint`
6. test-ci後の残存確認: users/intents/latches系の残行が0件(subject prefix `m3ws2-` で掃除・teardownの実効性)
   ```sql
   SELECT COUNT(*) FROM users WHERE auth_subject LIKE 'm3ws2-%';
   SELECT COUNT(*) FROM intents WHERE user_id IN
     (SELECT id FROM users WHERE auth_subject LIKE 'm3ws2-%');
   SELECT COUNT(*) FROM latches WHERE intent_ids && ARRAY(
     SELECT id FROM intents WHERE user_id IN
     (SELECT id FROM users WHERE auth_subject LIKE 'm3ws2-%'));
   SELECT COUNT(*) FROM latch_status_events WHERE latch_id IN
     (SELECT id FROM latches WHERE intent_ids && ARRAY(
       SELECT id FROM intents WHERE user_id IN
       (SELECT id FROM users WHERE auth_subject LIKE 'm3ws2-%')));
   SELECT COUNT(*) FROM notifications WHERE user_id IN
     (SELECT id FROM users WHERE auth_subject LIKE 'm3ws2-%');
   ```
   すべて0件であること。Redisはテストの掃除prefix(`m3ws2r-`・`ws2r-`)でSCANして0件

## 8. 実装ステップ(TDD。Task 1〜10の順で実行する)

### Task 1: latch_engine `_drain()` → `drain()` public化(sweeper・ResetJobが依存する土台)

**Files:**
- Modify: `backend/src/latch/worker/matching/latch_engine.py:568`・`backend/src/latch/worker/matching/latch_engine.py:812`
- Test: `backend/tests/unit/matching/test_latch_engine.py:952`・`backend/tests/unit/matching/test_latch_engine.py:969`

**Interfaces:**
- Produces: `LatchEngine.drain(self) -> None`(Task 6のExpirySweeper・Task 3のResetJobが呼ぶ。design §2.6・§3.2)

- [ ] **Step 1: 失敗するテストを書く(既存試験の機械的置換)**

`backend/tests/unit/matching/test_latch_engine.py` の952行目と969行目の `._drain()` を `.drain()` へ書き換える(2箇所。それぞれ `await _engine()._drain()` → `await _engine().drain()`、`await _engine(clock=clock)._drain()` → `await _engine(clock=clock).drain()`)。

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py -v`
Expected: 952/969行を呼ぶ2試験が FAIL with "AttributeError: 'LatchEngine' object has no attribute 'drain'"(それ以外の既存試験はPASS)

- [ ] **Step 3: 最小実装(public化・ロジック無変更)**

`backend/src/latch/worker/matching/latch_engine.py` の2箇所を変更する。

568行目(handle内の呼び出し):

```python
        await self.drain()
```

812行目の定義を改名し、docstringの1行目を public化の用途に更新する:

```python
    async def drain(self) -> None:
        """保留キューを提示順に走査し各行へtry_promote(評価経路のたび・引用#17)。

        M3 ws-2(design §2.6): 0時リセット完了時・クローズ検知(sweeper §2.7)からも
        呼ばれるpublic IF。差分化(引用#12)に正確に対応する — Jevを呼ばず
        提示順の再計算のみ(try_promoteの行単位上限判定が上限内の件数のみ処理し、
        残りは次トリガーへ委ねる)。

        大量保留時は行単位の上限判定で自然に上限内のみ処理され、残りは
        次トリガーへ(design §2.10-8: Worker 1構成を前提に行ロックのみ)。
        """
        now = self._clock.now()
        ids = await _drain_candidates(self._engine, now, latch_calc.LATCH_THRESHOLD)
        for latch_id in ids:
            await self.try_promote(latch_id)
```

(docstring本体は既存の文言を保持し、冒頭へM3 ws-2の段落を挿入する形。関数本体のロジックは無変更)

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py -v`
Expected: PASS(全件・`_drain` の参照は tests ソース内から消滅)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/matching/latch_engine.py backend/tests/unit/matching/test_latch_engine.py
git commit -m "refactor: make LatchEngine._drain public as drain for batch callers"
```

### Task 2: JevCostStore 掃除メソッド(リセットジョブの土台)

**Files:**
- Modify: `backend/src/latch/worker/cost/store.py`(class JevCostStore へ3メソッド追加)
- Test: `backend/tests/unit/cost/test_cost_store.py`(末尾へ追記)

**Interfaces:**
- Produces: `JevCostStore.delete_daily(day: str) -> int`・`JevCostStore.delete_monthly(month: str) -> int`・`JevCostStore.scan_delete(pattern: str) -> int`(いずれも戻り値=削除キー数。Task 3のResetJobが呼ぶ。key_prefix考慮)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/cost/test_cost_store.py` の末尾(既存 `test_key_prefix_isolates_namespace` 等の後)へ追記:

```python
# -- 掃除メソッド(M3 ws-2・リセットジョブ用・design §2.5) --


async def test_delete_daily_removes_key_and_is_idempotent(redis):
    """旧日次キーの即時解放(TTL 48hを待たない)。削除数を返す・冪等。"""
    store = JevCostStore(redis)
    await store.incr_daily("20260930")
    assert await store.delete_daily("20260930") == 1
    assert await redis.get("jev:daily:20260930") is None
    assert await store.delete_daily("20260930") == 0  # 既にない(冪等)


async def test_delete_monthly_removes_key(redis):
    """旧月次キーの即時解放(TTL 45日を待たない・月初0時)。"""
    store = JevCostStore(redis)
    await store.incr_monthly("202609")
    assert await store.delete_monthly("202609") == 1
    assert await redis.get("jev:monthly:202609") is None


async def test_scan_delete_removes_matching_keys_only(redis):
    """SCAN一致キーの一括削除(jev:exec:{day}:* 等)。対象外は保持。"""
    store = JevCostStore(redis)
    await store.record_execution("typesafe_jev", "20260930")
    await store.record_execution("typesafe_jev", "20260930")
    await store.record_execution("fallback_llm", "20260930")
    await store.incr_daily("20260930")  # パターン外(掃除対象は日次DELが担う)
    assert await store.scan_delete("jev:exec:20260930:*") == 2
    assert await redis.get("jev:exec:20260930:typesafe_jev") is None
    assert await redis.get("jev:exec:20260930:fallback_llm") is None
    assert await redis.get("jev:daily:20260930") == "2"  # 対象外は保持


async def test_cleanup_methods_respect_key_prefix(redis):
    """key_prefix付きでも同一prefix空間を掃除(integration共用Redis対策)。"""
    store = JevCostStore(redis, key_prefix="rj-")
    await store.incr_daily("20260930")
    await store.record_execution("typesafe_jev", "20260930")
    await store.incr_monthly("202609")
    assert await store.delete_daily("20260930") == 1
    assert await store.scan_delete("jev:exec:20260930:*") == 1
    assert await store.delete_monthly("202609") == 1
    assert await redis.get("rj-jev:daily:20260930") is None
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/cost/test_cost_store.py -v -k "delete or cleanup"`
Expected: FAIL with "AttributeError: 'JevCostStore' object has no attribute 'delete_daily'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/worker/cost/store.py` の class JevCostStore 内(`scan_report` メソッドの前あたり・INCR系の後)へ追記:

```python
    async def delete_daily(self, day: str) -> int:
        """リセットジョブの旧日次キー解放(M3-4・design §2.5)。

        機能的リセット(上限復帰)は日付キー切替で0時跨ぎの瞬間に成立済み。
        これは旧キーの即時解放(TTL 48hを待たない)。戻り値=削除キー数。
        """
        return int(await self._redis.delete(f"{self._prefix}jev:daily:{day}"))

    async def delete_monthly(self, month: str) -> int:
        """旧月次キー解放(月初0時・TTL 45日の残留回避)。"""
        return int(await self._redis.delete(f"{self._prefix}jev:monthly:{month}"))

    async def scan_delete(self, pattern: str) -> int:
        """SCAN一致キーの一括削除(jev:exec:{day}:* 等・prefix考慮)。

        対象行数は1日分の実行内訳・Intent/ユーザ別カウンタのみで
        常時小さい( _scan_counts と同規模)。戻り値=削除キー数。
        """
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match=f"{self._prefix}{pattern}", count=100
            )
            if keys:
                deleted += int(await self._redis.delete(*keys))
            if cursor == 0:
                return deleted
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/cost/test_cost_store.py -v`
Expected: PASS(既存含む全件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/cost/store.py backend/tests/unit/cost/test_cost_store.py
git commit -m "feat: add JevCostStore cleanup methods for reset job"
```

### Task 3: `worker/reset.py` 新設 — next_jst_midnight + ResetJob.run_once

**Files:**
- Create: `backend/src/latch/worker/reset.py`
- Create: `backend/tests/unit/worker/test_reset_job.py`(§0のとおり `tests/unit/worker/` ディレクトリを新設・`__init__.py` は置かない)

**Interfaces:**
- Consumes: Task 2の `JevCostStore.delete_daily(day)`・`delete_monthly(month)`・`scan_delete(pattern)`、Task 1の `LatchEngine.drain()`
- Produces: `latch.worker.reset.next_jst_midnight(now: datetime) -> datetime`(module関数・tz-aware UTC受取/戻し)、`latch.worker.reset._prev_month_key(today: date) -> str`、`ResetJob(*, cost_store, clock: Clock, latch, retry_sec: int = 300, sleep=asyncio.sleep)`・`ResetJob.run_once() -> None`・`ResetJob.run(*, stop: asyncio.Event | None = None) -> None`(Task 8のmain.pyが構築)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/worker/test_reset_job.py` を新規作成:

```python
"""ResetJobのunit試験(M3 ws-2 design §2.5〜2.6・§4.1-4/5)。

スタブcost_store・スタブlatch(記録)・FakeClockで決定的に検証する。
0時跨ぎ・月末・年跨ぎはFakeClock.setとnext_jst_midnightの純関数性で再現。
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from latch.core.clock import JST, FakeClock
from latch.worker.reset import ResetJob, _prev_month_key, next_jst_midnight

# JST 2026-10-01 21:00(月末・月初判定用に日付を都度setする)
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


class RecordingStore:
    """JevCostStoreスタブ(掃除メソッドの呼び出し記録)。"""

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self._error = error

    async def delete_daily(self, day: str) -> int:
        self.calls.append(("daily", day))
        if self._error is not None:
            raise self._error
        return 1

    async def delete_monthly(self, month: str) -> int:
        self.calls.append(("monthly", month))
        return 1

    async def scan_delete(self, pattern: str) -> int:
        self.calls.append(("scan", pattern))
        return 1


class RecordingLatch:
    """LatchEngineスタブ(drain呼び出し記録・初回失敗差し替え可)。"""

    def __init__(self, fail_first: bool = False):
        self.drains = 0
        self._fail_first = fail_first

    async def drain(self) -> None:
        self.drains += 1
        if self._fail_first and self.drains == 1:
            raise RuntimeError("drain boom")


def _job(*, clock, store=None, latch=None, retry_sec=300, sleep=None):
    return ResetJob(
        cost_store=store or RecordingStore(),
        clock=clock,
        latch=latch or RecordingLatch(),
        retry_sec=retry_sec,
        **({} if sleep is None else {"sleep": sleep}),
    )


# --- 1. next_jst_midnight(§4.1-4) ---


def test_next_jst_midnight_typical_evening():
    """JST 21:00 → 翌日JST 0時(= 当日UTC 15:00)。"""
    now = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)  # JST 21:00
    assert next_jst_midnight(now) == datetime(2026, 10, 1, 15, 0, 0, tzinfo=UTC)


def test_next_jst_midnight_exactly_midnight_returns_next_day():
    """JST 0時丁度 → 翌日0時(24時間後)。待機0秒を生まない。"""
    now = datetime(2026, 10, 1, 15, 0, 0, tzinfo=UTC)  # JST 10-02 00:00
    assert next_jst_midnight(now) == datetime(2026, 10, 2, 15, 0, 0, tzinfo=UTC)


def test_next_jst_midnight_month_end():
    """JST 10-31 23:59 → 11-01 00:00(月末跨ぎ)。"""
    now = datetime(2026, 10, 31, 14, 59, 0, tzinfo=UTC)  # JST 23:59
    assert next_jst_midnight(now) == datetime(2026, 10, 31, 15, 0, 0, tzinfo=UTC)


def test_next_jst_midnight_year_boundary():
    """JST 12-31 → 翌日=2027-01-01 00:00 JST(年跨ぎ)。"""
    now = datetime(2026, 12, 31, 14, 0, 0, tzinfo=UTC)  # JST 12-31 23:00
    assert next_jst_midnight(now) == datetime(2026, 12, 31, 15, 0, 0, tzinfo=UTC)


# --- 2. _prev_month_key ---


def test_prev_month_key_regular_and_year_boundary():
    assert _prev_month_key(date(2026, 11, 1)) == "202610"
    assert _prev_month_key(date(2027, 1, 1)) == "202612"  # 年跨ぎ
    assert _prev_month_key(date(2026, 2, 1)) == "202601"


# --- 3. run_once: 前日キー掃除 + 月初判定 + drain(§4.1-5) ---


async def test_run_once_cleans_prev_day_keys_and_drains():
    """月初(11-01)0時発火: 前日(10-31)の4キー+前月(10月)月次を削除しdrain。"""
    clock = FakeClock(datetime(2026, 11, 1, 0, 0, 5, tzinfo=JST))
    store, latch = RecordingStore(), RecordingLatch()
    await _job(clock=clock, store=store, latch=latch).run_once()
    kinds = [k for k, _ in store.calls]
    assert ("daily", "20261031") in store.calls
    assert ("scan", "jev:exec:20261031:*") in store.calls
    assert ("scan", "jev:intent:*:20261031") in store.calls
    assert ("scan", "jev:user:*:20261031") in store.calls
    assert ("monthly", "202610") in store.calls  # 月初(day==1)
    assert latch.drains == 1  # 完了時に保留キュー再評価(引用#11・§2.6)
    assert len(store.calls) == 5


async def test_run_once_keeps_monthly_on_non_first_day():
    """月初以外(10-15): 月次キーの削除を呼ばない(当月キー残存)。"""
    clock = FakeClock(datetime(2026, 10, 15, 0, 0, 5, tzinfo=JST))
    store, latch = RecordingStore(), RecordingLatch()
    await _job(clock=clock, store=store, latch=latch).run_once()
    assert ("daily", "20261014") in store.calls
    assert all(kind != "monthly" for kind, _ in store.calls)
    assert latch.drains == 1
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/worker/test_reset_job.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'latch.worker.reset'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/worker/reset.py` を新規作成(runループはTask 4で追加。この時点では `run` を含まない):

```python
"""リセットジョブ(M3-4・04 §5・design §2.5〜2.6)。

日次カウンタ(JST 0時)・月次カウンタ(暦月初JST 0時)の**同一ジョブ**での
リセット(FR-50)。機能的なリセット(日次30,000/月次600,000の上限復帰)は
日付キー切替で0時を跨いだ瞬間にINCRが成立させる(自己修復・引用#18)。
本ジョブの実体は「リセットの実行」の儀礼として旧キーを即時解放する
(TTL 48h/45日を待たない。月次キーの45日残留回避の意味が最も大きい)と、
完了時に保留キュー再評価として latch_engine.drain() を1回実行すること
(引用#11。イベントは発行しない — design §2.6・supervisor承認①)。
起動時の遡及はしない(カウンタは自己修復済み・drainは評価経路が代替)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta

from latch.core.clock import JST, Clock

logger = logging.getLogger(__name__)


def next_jst_midnight(now: datetime) -> datetime:
    """now(tz-aware UTC)より後の直近のJST 0時をUTC表現で返す(design §2.5)。

    nowがJST 0時丁度のときは翌日0時(待機0秒を生まない)。
    月末・年跨ぎはdateの+1日演算が処理する。
    """
    jst_now = now.astimezone(JST)
    next_date = jst_now.date() + timedelta(days=1)
    return datetime(
        next_date.year, next_date.month, next_date.day, 0, 0, 0, tzinfo=JST
    ).astimezone(UTC)


def _prev_month_key(today: date) -> str:
    """月初0時の掃除対象=前月の月次キー(yyyymm)。"""
    year = today.year - (1 if today.month == 1 else 0)
    month = 12 if today.month == 1 else today.month - 1
    return f"{year:04d}{month:02d}"


class ResetJob:
    """次のJST 0時まで待機→run_once(掃除+drain)。60秒系とは別周期の独立task。"""

    def __init__(
        self,
        *,
        cost_store,  # JevCostStore(掃除)
        clock: Clock,
        latch,  # LatchEngine(drain呼び出し・design §2.6)
        retry_sec: int = 300,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._cost_store = cost_store
        self._clock = clock
        self._latch = latch
        self._retry_sec = retry_sec
        self._sleep = sleep

    async def run_once(self) -> None:
        """0時発火の本体: 前日キー掃除(月初は前月月次)+drain(design §2.5)。

        DELは冪等(キー不在=0削除)。失敗した場合は例外を握らずrun()へ
        伝播する(runがretry_secで再試行 — 翌0時まで放置しない)。
        """
        jst_today = self._clock.jst_date()
        prev_day = (jst_today - timedelta(days=1)).strftime("%Y%m%d")
        await self._cost_store.delete_daily(prev_day)
        await self._cost_store.scan_delete(f"jev:exec:{prev_day}:*")
        await self._cost_store.scan_delete(f"jev:intent:*:{prev_day}")
        await self._cost_store.scan_delete(f"jev:user:*:{prev_day}")
        if jst_today.day == 1:
            await self._cost_store.delete_monthly(_prev_month_key(jst_today))
        await self._latch.drain()  # 完了時に保留キュー再評価(引用#11・§2.6)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/worker/test_reset_job.py -v`
Expected: PASS(10件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/reset.py backend/tests/unit/worker/test_reset_job.py
git commit -m "feat: add ResetJob run_once with JST midnight key cleanup"
```

### Task 4: ResetJob.run(待機・例外再試行・stop追従)

**Files:**
- Modify: `backend/src/latch/worker/reset.py`(class ResetJob へrun/_wait追加)
- Test: `backend/tests/unit/worker/test_reset_job.py`(追記)

**Interfaces:**
- Consumes: Task 3の `next_jst_midnight`・`ResetJob.run_once`
- Produces: `ResetJob.run(*, stop: asyncio.Event | None = None) -> None`(Task 8のmain.pyがcreate_taskで起動)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/worker/test_reset_job.py` の末尾へ追記:

```python
# --- 4. run: 次JST 0時待機→run_once・stop追従・例外retry(§4.1-5) ---


def _advancing_sleep(clock: FakeClock, stop: asyncio.Event, stop_at: int):
    """待機秒だけClockを前進させるsleep差し替え(0時跨ぎの決定的再現)。
    stop_at回目の呼び出しでstopを立て、runループを終了させる。"""
    calls: list[float] = []

    async def _sleep(seconds: float) -> None:
        calls.append(seconds)
        clock.set(clock.now() + timedelta(seconds=seconds))
        if len(calls) >= stop_at:
            stop.set()

    return _sleep, calls


async def test_run_waits_until_midnight_then_runs_once():
    """0時まで待機(待機秒=next_jst_midnightとの差)→run_once→次0時待機で終了。"""
    clock = FakeClock(NOW)  # JST 10-01 21:00 → 次0時まで3時間
    stop = asyncio.Event()
    sleep, calls = _advancing_sleep(clock, stop, stop_at=2)
    latch = RecordingLatch()
    job = _job(clock=clock, latch=latch, sleep=sleep)
    await job.run(stop=stop)
    assert calls[0] == 3 * 3600.0  # 21:00→24:00
    assert latch.drains == 1  # 0時到達でrun_once実行
    assert calls[1] == 24 * 3600.0  # 次周期の待機(翌0時まで丸1日)


async def test_run_retries_after_failure_with_retry_sec():
    """run_once失敗(例外)→retry_sec待機→再試行で成功(翌0時まで放置しない)。"""
    clock = FakeClock(NOW)
    stop = asyncio.Event()
    sleep, calls = _advancing_sleep(clock, stop, stop_at=3)
    latch = RecordingLatch(fail_first=True)  # 1回目のdrainで例外
    job = _job(clock=clock, latch=latch, retry_sec=300, sleep=sleep)
    await job.run(stop=stop)
    assert calls[1] == 300.0  # retry_secでの待機
    assert latch.drains == 2  # 再試行で成功


async def test_run_stops_immediately_when_stop_already_set():
    """stopセット済みで起動→待機もrun_onceもしない(graceful shutdown)。"""
    clock = FakeClock(NOW)
    stop = asyncio.Event()
    stop.set()
    latch = RecordingLatch()
    sleep_calls: list[float] = []

    async def sleep(seconds: float) -> None:  # pragma: no cover - 呼ばれない
        sleep_calls.append(seconds)

    await _job(clock=clock, latch=latch, sleep=sleep).run(stop=stop)
    assert latch.drains == 0
    assert sleep_calls == []


async def test_wait_without_stop_uses_injected_sleep():
    """stop=Noneの待機は注入sleepをそのまま使う(unit専用経路)。"""
    clock = FakeClock(NOW)
    sleep_calls: list[float] = []

    async def sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    job = _job(clock=clock, sleep=sleep)
    await job._wait(5.0, None)
    assert sleep_calls == [5.0]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/worker/test_reset_job.py -v -k "run_ or _wait"`
Expected: FAIL with "AttributeError: 'ResetJob' object has no attribute 'run'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/worker/reset.py` の class ResetJob へ追記:

```python
    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """次のJST 0時まで待機→run_once(失敗時はretry_secで再試行)。

        待機中のstopで発火を挟まず終了(graceful shutdown・BackfillRunner
        と同一契約)。0時丁度でなく数秒遅れの発火を許容する(run_onceが
        Clock.jst_date()を再取得するため・design §2.5)。
        """
        while stop is None or not stop.is_set():
            now = self._clock.now()
            await self._wait(
                (next_jst_midnight(now) - now).total_seconds(), stop
            )
            if stop is not None and stop.is_set():
                break
            while True:  # 失敗時はretry_secで再試行(翌0時まで放置しない)
                try:
                    await self.run_once()
                    break
                except Exception:
                    logger.warning("reset run_once failed", exc_info=True)
                    await self._wait(self._retry_sec, stop)
                    if stop is not None and stop.is_set():
                        return

    async def _wait(self, seconds: float, stop: asyncio.Event | None) -> None:
        """待機。注入sleepとstop待ちを並行させ、先に完了した方で返る。

        stopが来れば待機秒の残りを無視して即返る(shautdown応答性)。
        注入sleep(run(stop=event)でも使う)でunit試験が決定的に回せる。
        """
        if stop is None:
            await self._sleep(seconds)
            return
        sleep_task = asyncio.create_task(self._sleep(seconds))
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()  # 例外があれば再送出(待機自体の失敗は握らない)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/worker/test_reset_job.py -v`
Expected: PASS(全14件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/reset.py backend/tests/unit/worker/test_reset_job.py
git commit -m "feat: add ResetJob run loop with midnight wait and retry"
```

### Task 5: `worker/sweeper.py` 新設 — SQL定数・抽出関数(SQLピン試験)

**Files:**
- Create: `backend/src/latch/worker/sweeper.py`(この時点ではSQL定数+module関数のみ。ExpirySweeperクラスはTask 6)
- Create: `backend/tests/unit/worker/test_sweeper.py`

**Interfaces:**
- Consumes: `latch.intents.events.insert_match_event`・`EVENT_EXPIRED`(既存・無変更)
- Produces: SQL定数 `_SELECT_EXPIRING_LATCHES`・`_EXPIRE_RESPONSE_LATCH`・`_EXPIRE_CANDIDATE_LATCH`・`_SELECT_EXPIRING_INTENTS`・`_EXPIRE_INTENT`・`_SELECT_COMPLETION_TARGETS`・`_COMPLETE_LATCH`・`_SELECT_LATCH_PARTICIPANT_USERS`・`_INSERT_LATCH_EVENT_SQL`・`_INSERT_ATTENDANCE_NOTIFICATION`・`_SELECT_RECENT_CLOSE`(全て `text()` 定数)。module関数 `_select_expiring_latches(engine, now, batch_limit) -> list[tuple[uuid.UUID, str]]`・`_select_expiring_intents(...) -> list[tuple[uuid.UUID, str]]`・`_select_completion_targets(...) -> list[uuid.UUID]`・`_has_recent_close(engine, last_tick) -> bool`・`_coerce_uuid(value) -> uuid.UUID`(Task 6のExpirySweeperとunit試験が使う)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/worker/test_sweeper.py` を新規作成:

```python
"""ExpirySweeperのunit試験(M3 ws-2 design §2.2〜2.4・§2.7・§4.1)。

SQLピン(対象条件・SKIP LOCKED・LIMIT・部分索引条件との一致)とcompile検査は
SQL定数へ直接(reeval流儀)。run_onceの分岐は抽出関数をmonkeypatchで差し替え、
行単位処理はFakeEngine/FakeConnスタブで検証する。
"""

import uuid
from datetime import UTC, datetime

from latch.worker import sweeper as sweeper_mod
from latch.worker.sweeper import (
    _COMPLETE_LATCH,
    _EXPIRE_CANDIDATE_LATCH,
    _EXPIRE_INTENT,
    _EXPIRE_RESPONSE_LATCH,
    _INSERT_ATTENDANCE_NOTIFICATION,
    _INSERT_LATCH_EVENT_SQL,
    _SELECT_COMPLETION_TARGETS,
    _SELECT_EXPIRING_INTENTS,
    _SELECT_EXPIRING_LATCHES,
    _SELECT_LATCH_PARTICIPANT_USERS,
    _SELECT_RECENT_CLOSE,
)

LATCH1 = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
LATCH2 = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
INTENT1 = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
INTENT2 = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
USER1 = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
USER2 = uuid.UUID("00000000-0000-4000-8000-0000000000c2")

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


# --- 1. latches期限切れ抽出SQLピン(引用#2・design §2.2) ---


def test_select_expiring_latches_pins_conditions():
    sql = str(_SELECT_EXPIRING_LATCHES)
    assert "status IN ('proposed', 'partial_accept')" in sql
    assert "response_deadline <= CAST(:now AS timestamptz)" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    # candidateは保留キュー(期限のみ)
    assert "status = 'candidate' AND expires_at <= CAST(:now AS timestamptz)" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT :batch_limit" in sql


def test_expire_response_latch_pins_same_truth_as_response_api():
    """proposed/partial_accept行のUPDATEは回答API(_UPDATE_RESPONSE)と同一の
    真実(期限とstatus)を再検査する(引用#3・C9)。"""
    sql = str(_EXPIRE_RESPONSE_LATCH)
    assert "SET status = 'expired'" in sql
    assert "status IN ('proposed', 'partial_accept')" in sql
    assert "response_deadline <= CAST(:now AS timestamptz)" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "RETURNING id" in sql


def test_expire_candidate_ignores_response_deadline():
    """candidate行はresponse_deadline(昇格時への暫定値)を判定に使わない
    (引用#2・Review Focus 2)。"""
    sql = str(_EXPIRE_CANDIDATE_LATCH)
    assert "SET status = 'expired'" in sql
    assert "status = 'candidate'" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "response_deadline" not in sql
    assert "RETURNING id" in sql


# --- 2. Intent期限切れSQLピン(引用#5/#6・design §2.3) ---


def test_select_expiring_intents_matches_partial_index():
    """抽出WHEREはidx_intents_expiresの部分索引条件と同一(design引用#6)。"""
    sql = str(_SELECT_EXPIRING_INTENTS)
    assert "status IN ('draft', 'active', 'paused')" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT :batch_limit" in sql


def test_expire_intent_pins_update_and_version_return():
    sql = str(_EXPIRE_INTENT)
    assert "SET status = 'expired', updated_at = CAST(:now AS timestamptz)" in sql
    assert "status IN ('draft', 'active', 'paused')" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "RETURNING version" in sql


# --- 3. matched→completed SQLピン(引用#15・design §2.4) ---


def test_select_completion_targets_pins_max_time_start():
    sql = str(_SELECT_COMPLETION_TARGETS)
    assert "l.status = 'matched'" in sql
    assert "(SELECT max(i.time_start) FROM intents i" in sql
    assert "<= CAST(:now AS timestamptz)" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


def test_complete_latch_pins_matched_guard_and_completed_at():
    """cancelled(解散済み)はstatus='matched'でなく対象外=通知を送らない
    ことを構造的に担保(引用#15・Review Focus 3)。"""
    sql = str(_COMPLETE_LATCH)
    assert "SET status = 'completed', completed_at = CAST(:now AS timestamptz)" in sql
    assert "AND status = 'matched'" in sql
    assert "RETURNING intent_ids" in sql


# --- 4. クローズ検知観測SQLピン(design §2.7) ---


def test_select_recent_close_pins_window_and_statuses():
    sql = str(_SELECT_RECENT_CLOSE)
    assert "created_at > CAST(:last_tick AS timestamptz)" in sql
    assert "to_status IN ('rejected', 'expired', 'cancelled', 'matched')" in sql
    assert "LIMIT 1" in sql


# --- 5. compile検査(postgresql dialectで未変換の ':name' が残らない) ---


def test_sql_bind_params_compile():
    from sqlalchemy.dialects import postgresql

    targets = {
        _SELECT_EXPIRING_LATCHES: {"now", "batch_limit"},
        _EXPIRE_RESPONSE_LATCH: {"latch_id", "now"},
        _EXPIRE_CANDIDATE_LATCH: {"latch_id", "now"},
        _SELECT_EXPIRING_INTENTS: {"now", "batch_limit"},
        _EXPIRE_INTENT: {"intent_id", "now"},
        _SELECT_COMPLETION_TARGETS: {"now", "batch_limit"},
        _COMPLETE_LATCH: {"latch_id", "now"},
        _SELECT_LATCH_PARTICIPANT_USERS: {"ids"},
        _INSERT_LATCH_EVENT_SQL: {
            "latch_id",
            "from_status",
            "to_status",
            "user_id",
            "now",
        },
        _INSERT_ATTENDANCE_NOTIFICATION: {"user_id", "type", "payload", "now"},
        _SELECT_RECENT_CLOSE: {"last_tick"},
    }
    for stmt, keys in targets.items():
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        for key in keys:
            assert f":{key}" not in compiled, (key, compiled)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/worker/test_sweeper.py -v`
Expected: FAIL with "ImportError: cannot import name '_SELECT_EXPIRING_LATCHES'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/worker/sweeper.py` を新規作成(ExpirySweeperクラスはTask 6で追加):

```python
"""期限切れバッチExpirySweeper(M3-3・06 §6・design §2.2〜2.4・§2.7)。

latches期限切れ(proposed/partial_accept×回答期限/Intent期限・candidate×
期限切れ)・Intent期限切れ(draft/active/paused×expires_at)・matched→completed
遷移・クローズ検知drain(latch_status_events観測)の4処理を1つのrun_onceで
実行する。ReevalRunnerのrun_once先頭から呼ばれる(同一スケジューラ・単一
ジョブ管理・design §2.1案A)。

直列化(06 §6引用#3): 抽出はFOR UPDATE SKIP LOCKED(抽出txは即コミット)、
行ごとに個別txの条件付きUPDATE(抽出と実行の間に他経路が行を変えても影響
行数0で無視)。影響行数1のとき遷移イベント(latch_status_events・
match_events expired)・attendance通知を**同一tx**で挿入する(C10)。
1tick=1時刻: run_once冒頭のClock.now()を抽出・UPDATE・挿入すべてで使う。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# 実施自己申告の通知type(実装定義・supervisor承認②。媒体・読み取りはws-3)
NOTIFICATION_ATTENDANCE_REQUEST = "attendance_request"

# latches期限切れ対象抽出(06 §6の対象条件・design §2.2)。
# ORDER BYは期限切れが古い順(response_deadline・expires_at・id)
_SELECT_EXPIRING_LATCHES = text("""
    SELECT id, status FROM latches
    WHERE (status IN ('proposed', 'partial_accept')
           AND (response_deadline <= CAST(:now AS timestamptz)
                OR expires_at <= CAST(:now AS timestamptz)))
       OR (status = 'candidate' AND expires_at <= CAST(:now AS timestamptz))
    ORDER BY response_deadline, expires_at, id
    LIMIT :batch_limit
    FOR UPDATE SKIP LOCKED
""")

# proposed/partial_accept行(回答API _UPDATE_RESPONSEと同一の真実を再検査)
_EXPIRE_RESPONSE_LATCH = text("""
    UPDATE latches SET status = 'expired'
    WHERE id = CAST(:latch_id AS uuid)
      AND status IN ('proposed', 'partial_accept')
      AND (response_deadline <= CAST(:now AS timestamptz)
           OR expires_at <= CAST(:now AS timestamptz))
    RETURNING id
""")

# candidate行(保留キュー。response_deadlineは暫定値なので判定に使わない・引用#2)
_EXPIRE_CANDIDATE_LATCH = text("""
    UPDATE latches SET status = 'expired'
    WHERE id = CAST(:latch_id AS uuid)
      AND status = 'candidate'
      AND expires_at <= CAST(:now AS timestamptz)
    RETURNING id
""")

# Intent期限切れ対象抽出(WHEREはidx_intents_expiresの部分索引条件と同一・引用#6)
_SELECT_EXPIRING_INTENTS = text("""
    SELECT id, status FROM intents
    WHERE status IN ('draft', 'active', 'paused')
      AND expires_at <= CAST(:now AS timestamptz)
    ORDER BY expires_at, id
    LIMIT :batch_limit
    FOR UPDATE SKIP LOCKED
""")

_EXPIRE_INTENT = text("""
    UPDATE intents SET status = 'expired', updated_at = CAST(:now AS timestamptz)
    WHERE id = CAST(:intent_id AS uuid)
      AND status IN ('draft', 'active', 'paused')
      AND expires_at <= CAST(:now AS timestamptz)
    RETURNING version
""")

# matched→completed対象抽出(対象時刻=max(参加Intentのtime_start)経過・design §2.4)
_SELECT_COMPLETION_TARGETS = text("""
    SELECT l.id FROM latches l
    WHERE l.status = 'matched'
      AND (SELECT max(i.time_start) FROM intents i
           WHERE i.id = ANY(l.intent_ids)) <= CAST(:now AS timestamptz)
    ORDER BY (SELECT max(i.time_start) FROM intents i
              WHERE i.id = ANY(l.intent_ids)), l.id
    LIMIT :batch_limit
    FOR UPDATE SKIP LOCKED
""")

# cancelled(解散済み)はstatus='matched'でなく対象外=申告通知を送らない(引用#15)
_COMPLETE_LATCH = text("""
    UPDATE latches SET status = 'completed', completed_at = CAST(:now AS timestamptz)
    WHERE id = CAST(:latch_id AS uuid) AND status = 'matched'
    RETURNING intent_ids
""")

# completed遷移時の実施自己申告通知の宛先(D-09・参加者全員)
_SELECT_LATCH_PARTICIPANT_USERS = text("""
    SELECT user_id FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

# latch_status_events挿入(latch_engine._INSERT_LATCH_EVENTと同一SQL。
# システム起因=本バッチはuser_id=NULL・引用#13)
_INSERT_LATCH_EVENT_SQL = text("""
    INSERT INTO latch_status_events
        (latch_id, from_status, to_status, user_id, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:from_status AS text),
            :to_status, CAST(:user_id AS uuid), :now)
""")

# attendance通知先行書き込み(payload={latch_id}最小参照・引用#16)
_INSERT_ATTENDANCE_NOTIFICATION = text("""
    INSERT INTO notifications (user_id, type, payload, created_at)
    VALUES (CAST(:user_id AS uuid), :type, CAST(:payload AS jsonb), :now)
""")

# クローズ検知(design §2.7): last_tick以降のクローズ系遷移の1行存在検査
_SELECT_RECENT_CLOSE = text("""
    SELECT 1 FROM latch_status_events
    WHERE created_at > CAST(:last_tick AS timestamptz)
      AND to_status IN ('rejected', 'expired', 'cancelled', 'matched')
    LIMIT 1
""")


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策・origin.pyと同一規律)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def _select_expiring_latches(
    engine: AsyncEngine, now: datetime, batch_limit: int
) -> list[tuple[uuid.UUID, str]]:
    """latches期限切れ対象抽出(engine.begin()内包・抽出txは即コミット)。"""
    async with engine.begin() as conn:
        res = await conn.execute(
            _SELECT_EXPIRING_LATCHES, {"now": now, "batch_limit": batch_limit}
        )
        rows = res.fetchall()
    return [(_coerce_uuid(r[0]), r[1]) for r in rows]


async def _select_expiring_intents(
    engine: AsyncEngine, now: datetime, batch_limit: int
) -> list[tuple[uuid.UUID, str]]:
    """Intent期限切れ対象抽出(idx_intents_expires部分索引と同一条件)。"""
    async with engine.begin() as conn:
        res = await conn.execute(
            _SELECT_EXPIRING_INTENTS, {"now": now, "batch_limit": batch_limit}
        )
        rows = res.fetchall()
    return [(_coerce_uuid(r[0]), r[1]) for r in rows]


async def _select_completion_targets(
    engine: AsyncEngine, now: datetime, batch_limit: int
) -> list[uuid.UUID]:
    """matched→completed対象抽出(対象時刻昇順)。"""
    async with engine.begin() as conn:
        res = await conn.execute(
            _SELECT_COMPLETION_TARGETS, {"now": now, "batch_limit": batch_limit}
        )
        rows = res.fetchall()
    return [_coerce_uuid(r[0]) for r in rows]


async def _has_recent_close(engine: AsyncEngine, last_tick: datetime) -> bool:
    """§2.7観測: last_tick以降にクローズ系イベントがあるか(1行存在検査)。

    60秒窓の走査対象は遷移時のみ書かれるlatch_status_eventsの僅か行数
    (Index不要・design §2.7)。期限切れ処理自身が書いたexpired行も観測する
    (呼び出し順「書き込み→観測→drain」が期限切れで空いた枠の同tick昇格を許す)。
    """
    async with engine.begin() as conn:
        res = await conn.execute(_SELECT_RECENT_CLOSE, {"last_tick": last_tick})
        return res.first() is not None
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/worker/test_sweeper.py -v`
Expected: PASS(10件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/sweeper.py backend/tests/unit/worker/test_sweeper.py
git commit -m "feat: add expiry sweeper SQL constants and selectors"
```

### Task 6: `worker/sweeper.py` — ExpirySweeper.run_once(4処理)

**Files:**
- Modify: `backend/src/latch/worker/sweeper.py`(class ExpirySweeper を追記)
- Test: `backend/tests/unit/worker/test_sweeper.py`(追記)

**Interfaces:**
- Consumes: Task 5のSQL定数・抽出関数、Task 1の `LatchEngine.drain()`、`insert_match_event`・`EVENT_EXPIRED`
- Produces: `ExpirySweeper(*, engine: AsyncEngine, clock: Clock, latch, batch_limit: int)`・`ExpirySweeper.run_once() -> int`(戻り値=遷移行数・§9-1。Task 7のReevalRunner注入とTask 9のintegrationが使う)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/worker/test_sweeper.py` の末尾へ追記(import文はファイル先頭のimportブロックへ `import json`・`import pytest`・`from datetime import timedelta`・`from latch.core.clock import FakeClock`・`from latch.worker.sweeper import ExpirySweeper` を追記):

```python
# --- 6. 行単位txスタブ(FakeEngine/FakeConn) ---


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """SQL定数→戻り値の辞書。実行記録(stmt, params)を保持。"""

    def __init__(self, results=None):
        self.calls: list[tuple[object, dict]] = []
        self._results = results or {}

    async def execute(self, stmt, params=None):
        self.calls.append((stmt, dict(params or {})))
        return FakeResult(self._results.get(stmt))


class _BeginCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakeEngine:
    """engine.begin()のスタブ(単一connを返す・begin回数を記録)。"""

    def __init__(self, conn):
        self._conn = conn
        self.begins = 0

    def begin(self):
        self.begins += 1
        return _BeginCtx(self._conn)


class RecordingLatch:
    """LatchEngineスタブ(drain呼び出し記録)。"""

    def __init__(self):
        self.drains = 0

    async def drain(self):
        self.drains += 1


def _sweeper(engine=None, clock=None, latch=None, batch_limit=50):
    return ExpirySweeper(
        engine=engine or object(),  # 抽出関数はmonkeypatchで差し替え
        clock=clock or FakeClock(NOW),
        latch=latch or RecordingLatch(),
        batch_limit=batch_limit,
    )


# --- 7. _expire_latch: 条件付きUPDATE+イベント同一tx(§4.1-2/3) ---


async def test_expire_latch_proposed_writes_event_same_tx():
    """proposed行: 影響1→expired遷移+イベントを同一tx(user_id=NULL)。"""
    conn = FakeConn({_EXPIRE_RESPONSE_LATCH: [("ok",)]})
    engine = FakeEngine(conn)
    sw = _sweeper(engine=engine)
    assert await sw._expire_latch(LATCH1, "proposed", NOW) is True
    assert engine.begins == 1  # 単一tx
    stmts = [s for s, _ in conn.calls]
    assert stmts == [_EXPIRE_RESPONSE_LATCH, _INSERT_LATCH_EVENT_SQL]
    params = conn.calls[1][1]
    assert params["from_status"] == "proposed"
    assert params["to_status"] == "expired"
    assert params["user_id"] is None  # システム起因(引用#13)
    assert params["now"] == NOW  # 1tick=1時刻


async def test_expire_latch_candidate_uses_candidate_sql():
    """candidate行: candidate用UPDATE(期限のみ)を使う。"""
    conn = FakeConn({_EXPIRE_CANDIDATE_LATCH: [("ok",)]})
    engine = FakeEngine(conn)
    sw = _sweeper(engine=engine)
    assert await sw._expire_latch(LATCH1, "candidate", NOW) is True
    assert conn.calls[0][0] is _EXPIRE_CANDIDATE_LATCH
    assert conn.calls[1][1]["from_status"] == "candidate"


async def test_expire_latch_no_row_no_event():
    """影響0(他経路で遷移済み)→イベント挿入しない・False(引用#3・
    Review Focus 1)。"""
    conn = FakeConn({_EXPIRE_RESPONSE_LATCH: []})
    sw = _sweeper(engine=FakeEngine(conn))
    assert await sw._expire_latch(LATCH1, "proposed", NOW) is False
    assert len(conn.calls) == 1  # UPDATEのみ・イベントなし


# --- 8. _expire_intent: expired化+expiredイベント同一tx(§4.1-3) ---


async def test_expire_intent_publishes_expired_event_same_tx(monkeypatch):
    """影響1→UPDATE RETURNING versionでexpiredイベントを同一tx発行(引用#19)。"""
    events = []

    async def fake_insert(conn, *, event_type, intent_id, version, now):
        events.append((event_type, intent_id, version, now))

    monkeypatch.setattr(sweeper_mod, "insert_match_event", fake_insert)
    conn = FakeConn({_EXPIRE_INTENT: [(3,)]})  # RETURNING version=3
    engine = FakeEngine(conn)
    sw = _sweeper(engine=engine)
    assert await sw._expire_intent(INTENT1, NOW) is True
    assert engine.begins == 1  # 同一tx
    assert events == [("expired", INTENT1, 3, NOW)]


async def test_expire_intent_no_row_no_event(monkeypatch):
    async def fake_insert(conn, **kwargs):  # pragma: no cover - 呼ばれない
        raise AssertionError("影響0ではイベント発行しない")

    monkeypatch.setattr(sweeper_mod, "insert_match_event", fake_insert)
    conn = FakeConn({_EXPIRE_INTENT: []})
    sw = _sweeper(engine=FakeEngine(conn))
    assert await sw._expire_intent(INTENT1, NOW) is False


# --- 9. _complete_latch: completed化+イベント+attendance通知(§4.1-3) ---


async def test_complete_latch_writes_event_then_notifications():
    """影響1→イベント(matched→completed・user_id=NULL)→参加者全員へ
    attendance_request通知(payload={latch_id}最小参照)。同一tx。"""
    conn = FakeConn({
        _COMPLETE_LATCH: [([INTENT1, INTENT2],)],  # RETURNING intent_ids
        _SELECT_LATCH_PARTICIPANT_USERS: [(USER1,), (USER2,)],
    })
    engine = FakeEngine(conn)
    sw = _sweeper(engine=engine)
    assert await sw._complete_latch(LATCH1, NOW) is True
    assert engine.begins == 1  # 単一tx
    stmts = [s for s, _ in conn.calls]
    assert stmts == [
        _COMPLETE_LATCH,
        _INSERT_LATCH_EVENT_SQL,
        _SELECT_LATCH_PARTICIPANT_USERS,
        _INSERT_ATTENDANCE_NOTIFICATION,
        _INSERT_ATTENDANCE_NOTIFICATION,
    ]
    ev = conn.calls[1][1]
    assert ev["from_status"] == "matched"
    assert ev["to_status"] == "completed"
    assert ev["user_id"] is None
    notified = [conn.calls[3][1]["user_id"], conn.calls[4][1]["user_id"]]
    assert notified == [USER1, USER2]
    n1 = conn.calls[3][1]
    assert n1["type"] == "attendance_request"
    assert json.loads(n1["payload"]) == {"latch_id": str(LATCH1)}
    assert n1["now"] == NOW


async def test_complete_latch_no_row_no_side_effects():
    conn = FakeConn({_COMPLETE_LATCH: []})
    sw = _sweeper(engine=FakeEngine(conn))
    assert await sw._complete_latch(LATCH1, NOW) is False
    assert len(conn.calls) == 1


# --- 10. run_once: 処理順序(書き込み→観測→drain)・クローズ分岐・last_tick ---


async def test_run_once_order_and_drain_on_close(monkeypatch):
    """処理順序はlatches→intents→completed→観測→drain(§2.7・Review Focus 4)。
    クローズあり→drain呼出・last_tick更新。"""
    calls = []

    async def sel_latches(engine, now, limit):
        calls.append("select_latches")
        return [(LATCH1, "proposed")]

    async def sel_intents(engine, now, limit):
        calls.append("select_intents")
        return [(INTENT1, "active")]

    async def sel_completed(engine, now, limit):
        calls.append("select_completed")
        return [LATCH2]

    monkeypatch.setattr(sweeper_mod, "_select_expiring_latches", sel_latches)
    monkeypatch.setattr(sweeper_mod, "_select_expiring_intents", sel_intents)
    monkeypatch.setattr(sweeper_mod, "_select_completion_targets", sel_completed)

    async def fake_close(engine, last_tick):
        calls.append(f"observe:{last_tick == NOW}")  # 初回last_tick=起動時刻
        return True

    monkeypatch.setattr(sweeper_mod, "_has_recent_close", fake_close)
    latch = RecordingLatch()
    sw = _sweeper(latch=latch)

    async def fake_expire_l(latch_id, from_status, now):
        calls.append(f"expire_latch:{latch_id}:{from_status}")
        return True

    async def fake_expire_i(intent_id, now):
        calls.append(f"expire_intent:{intent_id}")
        return True

    async def fake_complete(latch_id, now):
        calls.append(f"complete:{latch_id}")
        return True

    monkeypatch.setattr(sw, "_expire_latch", fake_expire_l)
    monkeypatch.setattr(sw, "_expire_intent", fake_expire_i)
    monkeypatch.setattr(sw, "_complete_latch", fake_complete)

    done = await sw.run_once()
    assert done == 3  # 遷移行数(§9-1)
    assert calls == [
        "select_latches",
        f"expire_latch:{LATCH1}:proposed",
        "select_intents",
        f"expire_intent:{INTENT1}",
        "select_completed",
        f"complete:{LATCH2}",
        "observe:True",
    ]
    assert latch.drains == 1  # クローズ観測→drain(順序: 観測がdrainの前)
    assert sw._last_tick == NOW  # 成功時に更新


async def test_run_once_no_close_no_drain(monkeypatch):
    """観測なし→drain呼ばない・last_tickは更新する。"""

    async def sel_latches(engine, now, limit):
        return []

    async def sel_intents(engine, now, limit):
        return []

    async def sel_completed(engine, now, limit):
        return []

    async def fake_close(engine, last_tick):
        return False

    monkeypatch.setattr(sweeper_mod, "_select_expiring_latches", sel_latches)
    monkeypatch.setattr(sweeper_mod, "_select_expiring_intents", sel_intents)
    monkeypatch.setattr(sweeper_mod, "_select_completion_targets", sel_completed)
    monkeypatch.setattr(sweeper_mod, "_has_recent_close", fake_close)
    latch = RecordingLatch()
    sw = _sweeper(latch=latch)
    assert await sw.run_once() == 0
    assert latch.drains == 0
    assert sw._last_tick == NOW


async def test_run_once_propagates_exception_and_keeps_last_tick(monkeypatch):
    """行処理の例外は握らない(ReevalRunner側で握る)・last_tickは更新しない
    (観測窓の取りこぼし防止・Review Focus 4)。"""
    clock = FakeClock(NOW)

    async def sel_latches(engine, now, limit):
        return [(LATCH1, "proposed")]

    async def sel_intents(engine, now, limit):
        return []

    async def sel_completed(engine, now, limit):
        return []

    async def raising(latch_id, from_status, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(sweeper_mod, "_select_expiring_latches", sel_latches)
    monkeypatch.setattr(sweeper_mod, "_select_expiring_intents", sel_intents)
    monkeypatch.setattr(sweeper_mod, "_select_completion_targets", sel_completed)
    sw = _sweeper(clock=clock)
    monkeypatch.setattr(sw, "_expire_latch", raising)
    clock.advance(timedelta(seconds=60))  # run_onceのnow=NOW+60s
    with pytest.raises(RuntimeError):
        await sw.run_once()
    assert sw._last_tick == NOW  # 例外時は更新しない(旧値のまま)


async def test_drain_error_propagates_from_run_once(monkeypatch):
    """drain(第4処理)の例外も握らない(run_once全体の契約)。"""

    async def sel_latches(engine, now, limit):
        return []

    async def sel_intents(engine, now, limit):
        return []

    async def sel_completed(engine, now, limit):
        return []

    async def fake_close(engine, last_tick):
        return True

    monkeypatch.setattr(sweeper_mod, "_select_expiring_latches", sel_latches)
    monkeypatch.setattr(sweeper_mod, "_select_expiring_intents", sel_intents)
    monkeypatch.setattr(sweeper_mod, "_select_completion_targets", sel_completed)
    monkeypatch.setattr(sweeper_mod, "_has_recent_close", fake_close)

    class BrokenLatch:
        async def drain(self):
            raise RuntimeError("drain boom")

    sw = _sweeper(latch=BrokenLatch())
    with pytest.raises(RuntimeError):
        await sw.run_once()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/worker/test_sweeper.py -v -k "expire_latch or expire_intent or complete_latch or run_once or drain_error"`
Expected: FAIL with "ImportError: cannot import name 'ExpirySweeper'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/worker/sweeper.py` へ import を追加し(classの前):

```python
import json
```

(既存のimportブロックへ追記。`from latch.intents.events import EVENT_EXPIRED, insert_match_event` も追記)

class ExpirySweeper を `_has_recent_close` の後に追記:

```python
class ExpirySweeper:
    """期限切れバッチ本体(06 §6・design §2.2〜2.4・§2.7)。

    ReevalRunner.run_onceの先頭から呼ばれる(60秒tick・design §2.1案A)。
    run_onceはpublic(unit/integrationから直接呼ぶ・ReevalRunnerと同型)。
    独立した周期ループは持たない(切替・停止はReevalRunnerの単一ジョブ)。
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock,  # Clock(既存のClock型・core.clock)
        latch,  # LatchEngine(drain呼び出し・design §2.7)
        batch_limit: int,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._latch = latch
        self._batch_limit = batch_limit
        self._last_tick: datetime = clock.now()  # 起動時刻(§2.7)

    async def run_once(self) -> int:
        """1周期分: latches期限切れ→Intent期限切れ→completed→クローズ検知drain。

        戻り値=遷移させた行数(latches+intents+completedの合計・drain呼出を
        含まない)。例外は握らない(ReevalRunner.runが握って次周期で回収)。
        1tick=1時刻: 抽出・UPDATE・挿入すべてで同一のnowを使う(design §2.2)。
        """
        now = self._clock.now()
        done = 0
        for latch_id, status in await _select_expiring_latches(
            self._engine, now, self._batch_limit
        ):
            if await self._expire_latch(latch_id, status, now):
                done += 1
        for intent_id, _status in await _select_expiring_intents(
            self._engine, now, self._batch_limit
        ):
            if await self._expire_intent(intent_id, now):
                done += 1
        for latch_id in await _select_completion_targets(
            self._engine, now, self._batch_limit
        ):
            if await self._complete_latch(latch_id, now):
                done += 1
        # 観測→drainの順(自分の書いたexpiredも観測=枠回復を同じtickで。
        # drain後の観測はしない=再帰なし・Review Focus 4)
        if await _has_recent_close(self._engine, self._last_tick):
            await self._latch.drain()
        self._last_tick = now
        return done

    async def _expire_latch(
        self, latch_id: uuid.UUID, from_status: str, now: datetime
    ) -> bool:
        """行単位tx: 条件付きUPDATE→影響1ならlatch_status_events同一tx挿入。

        抽出時のstatusでUPDATE文を使い分ける(candidateは期限のみ判定)。
        影響0(回答API等が先行)はイベントなしで無視(引用#3)。
        """
        async with self._engine.begin() as conn:
            stmt = (
                _EXPIRE_CANDIDATE_LATCH
                if from_status == "candidate"
                else _EXPIRE_RESPONSE_LATCH
            )
            res = await conn.execute(stmt, {"latch_id": latch_id, "now": now})
            if res.first() is None:
                return False
            await conn.execute(
                _INSERT_LATCH_EVENT_SQL,
                {
                    "latch_id": latch_id,
                    "from_status": from_status,
                    "to_status": "expired",
                    "user_id": None,
                    "now": now,
                },
            )
            logger.info("sweeper.expired latch_id=%s from=%s", latch_id, from_status)
            return True

    async def _expire_intent(self, intent_id: uuid.UUID, now: datetime) -> bool:
        """行単位tx: expired化+expiredイベント同一tx発行(引用#19・保存と同一慣行)。

        draft(下書き)も対象(引用#5)。paused→expiredも対象(resume Event競合は
        version検査とstatus検査で二重遷移なし)。match_candidates等は閉じない
        (supervisor承認⑤・Layer 1のstatus='active'条件とH再検証で自然無力化)。
        """
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _EXPIRE_INTENT, {"intent_id": intent_id, "now": now}
            )
            row = res.first()
            if row is None:
                return False
            await insert_match_event(
                conn,
                event_type=EVENT_EXPIRED,
                intent_id=intent_id,
                version=int(row[0]),
                now=now,
            )
            logger.info("sweeper.intent_expired intent_id=%s", intent_id)
            return True

    async def _complete_latch(self, latch_id: uuid.UUID, now: datetime) -> bool:
        """行単位tx: completed化+イベント+実施自己申告の通知先行書き込み(D-09)。

        cancelled(解散済み)はstatus='matched'でなく対象外=通知を送らない
        (引用#15・構造的担保)。参加者はintents.user_idを行順に全員へ(§9-9)。
        """
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _COMPLETE_LATCH, {"latch_id": latch_id, "now": now}
            )
            row = res.first()
            if row is None:
                return False
            await conn.execute(
                _INSERT_LATCH_EVENT_SQL,
                {
                    "latch_id": latch_id,
                    "from_status": "matched",
                    "to_status": "completed",
                    "user_id": None,
                    "now": now,
                },
            )
            users = await conn.execute(
                _SELECT_LATCH_PARTICIPANT_USERS,
                {"ids": [_coerce_uuid(x) for x in row[0]]},
            )
            for (user_id,) in users.fetchall():
                await conn.execute(
                    _INSERT_ATTENDANCE_NOTIFICATION,
                    {
                        "user_id": _coerce_uuid(user_id),
                        "type": NOTIFICATION_ATTENDANCE_REQUEST,
                        "payload": json.dumps({"latch_id": str(latch_id)}),
                        "now": now,
                    },
                )
            logger.info("sweeper.completed latch_id=%s", latch_id)
            return True
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/worker/test_sweeper.py -v`
Expected: PASS(全件・Task 5分を含む)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/sweeper.py backend/tests/unit/worker/test_sweeper.py
git commit -m "feat: add ExpirySweeper run_once with same-tx events"
```

### Task 7: `worker/reeval.py` — ExpirySweeper注入(同一スケジューラ統合)

**Files:**
- Modify: `backend/src/latch/worker/reeval.py`(`__init__` へ `sweeper=None` オプション追加・`run_once()` 先頭へ実行・docstring更新)
- Test: `backend/tests/unit/test_worker_reeval.py`(末尾へ追記)

**Interfaces:**
- Consumes: Task 6の `ExpirySweeper.run_once()`
- Produces: `ReevalRunner(..., sweeper=None)`(既定None=従動作。Task 8のmain.pyが渡す)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_reeval.py` の末尾へ追記:

```python
# --- 13. ExpirySweeper注入(M3 ws-2・design §2.1案A・§4.1統合回帰のunit側) ---


class RecordingSweeper:
    """ExpirySweeperスタブ(run_onceの呼び出し記録)。"""

    def __init__(self, error: Exception | None = None):
        self.calls = 0
        self._error = error

    async def run_once(self) -> int:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return 0


async def test_sweeper_runs_first_when_injected(monkeypatch):
    """注入あり→sweeper.run_onceがcatch-up/Bucket投入の前に実行される
    (design §2.1「先頭実行により期限切れ確定がパイプラインの重さに
    後ろ倒しにならない」)。"""
    _patch_select(monkeypatch, catchup=[IID1], bucket=())
    order: list[str] = []
    sweeper = RecordingSweeper()

    class OrderedSweeper(RecordingSweeper):
        async def run_once(self) -> int:
            order.append("sweeper")
            return await super().run_once()

    pipeline = RecordingPipeline()

    async def ordered_pipeline(intent_id):
        order.append("pipeline")
        await pipeline(intent_id)

    runner = ReevalRunner(
        engine=object(),
        clock=FakeClock(NOW),
        guard=None,
        pipeline=ordered_pipeline,
        interval_sec=60.0,
        batch_limit=50,
        sweeper=OrderedSweeper(),
    )
    done = await runner.run_once()
    assert done == 1
    assert order == ["sweeper", "pipeline"]  # sweeper先行


async def test_sweeper_none_keeps_current_behavior(monkeypatch):
    """注入なし(既定None)→従動作。既存構成(test_k_limits_e2e等)は無傷。"""
    _patch_select(monkeypatch, catchup=[IID1], bucket=())
    pipeline = RecordingPipeline()
    runner = _runner(pipeline=pipeline)  # 既存ヘルパ(sweeper渡さず)
    assert await runner.run_once() == 1
    assert pipeline.calls == [IID1]


async def test_sweeper_error_propagates_from_run_once(monkeypatch):
    """sweeper.run_onceの例外は握らず伝播(run()が握って次周期で回収)。"""
    import pytest

    _patch_select(monkeypatch, catchup=[IID1], bucket=())
    runner = ReevalRunner(
        engine=object(),
        clock=FakeClock(NOW),
        guard=None,
        pipeline=RecordingPipeline(),
        interval_sec=60.0,
        batch_limit=50,
        sweeper=RecordingSweeper(error=RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        await runner.run_once()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_reeval.py -v -k "sweeper"`
Expected: FAIL with "TypeError: ReevalRunner.__init__() got an unexpected keyword argument 'sweeper'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/worker/reeval.py` を3箇所変更する。

(1) module docstring の末尾2行(「M3-3がexpiry_sweeperを同一周期へ統合できるよう独立クラスにする。」)を現状説明へ更新:

```python
"""再評価Runner(06 §9・design §2.8)。BackfillRunnerと同型の周期ジョブ。

catch-upスキャン(expires_atまで2時間以内のactive)と30分Bucket再評価
(time_startが当該Bucketに属するIntent・design §2.8-2の読み)を抽出し、
reevalガード(ws-4資産・入口で共用)を通してpipelineを直接投入する
(Eventは発行しない — 同一versionのidempotencyキー衝突のため・06 §9)。
sleep-first・stop追従・run_once内の例外は握らずrun()が握って次周期で回収。

M3 ws-2(design §2.1案A): ExpirySweeperをオプション注入(sweeper=Noneで
従動作)。run_onceの**先頭**でsweeper.run_once()を実行してからcatch-up/
Bucket投入へ(期限切れ確定がパイプラインの重さに後ろ倒しにならない)。
60秒tickの1本化でlatches・Intent期限切れ・catch-upの切替・停止は単一ジョブ。
"""
```

(2) `__init__` へ `sweeper=None` 引数と保持を追加(既存引数の末尾・sleepの後):

```python
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        guard,  # ReevalGuard | None(Noneならガード判定をスキップ)
        pipeline,  # Callable[[uuid.UUID], Awaitable[None]]
        interval_sec: float,
        batch_limit: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        sweeper=None,  # ExpirySweeper | None(M3 ws-2・design §2.1案A)
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._guard = guard
        self._pipeline = pipeline
        self._interval_sec = interval_sec
        self._batch_limit = batch_limit
        self._sleep = sleep
        self._sweeper = sweeper
        self._last_bucket: datetime | None = None  # 前回処理Bucket(メモリ保持)
```

(3) `run_once` の先頭(docstringの直後・`now = self._clock.now()` の前)へ挿入:

```python
    async def run_once(self) -> int:
        """1周期分: sweeper(注入時)→catch-up抽出→Bucket処理→直接投入。

        例外は握らない(sweeper内も含む・design §2.1)。
        """
        if self._sweeper is not None:
            await self._sweeper.run_once()  # 先頭(期限切れ確定を先行)
        now = self._clock.now()
        # …(既存の実装をそのまま継続・変更なし)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_reeval.py -v`
Expected: PASS(既存12件+新規3件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/reeval.py backend/tests/unit/test_worker_reeval.py
git commit -m "feat: integrate ExpirySweeper into ReevalRunner tick"
```

### Task 8: settings.py 2キー + worker/main.py 配線

**Files:**
- Modify: `backend/src/latch/settings.py`(末尾へ2キー追加)
- Modify: `backend/tests/unit/test_settings.py`(`test_settings_defaults` へ2行追記)
- Modify: `backend/src/latch/worker/main.py`(ExpirySweeper構築+注入・ResetJob構築+task追加)

**Interfaces:**
- Consumes: Task 6の `ExpirySweeper`、Task 3〜4の `ResetJob`、Task 7の `ReevalRunner(sweeper=...)`、Task 2の `JevCostStore`
- Produces: `Settings.sweeper_batch_limit: int`(既定50)・`Settings.reset_retry_sec: int`(既定300)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_settings.py` の `test_settings_defaults` へ、既存の `reeval_runner_batch_limit` のassertの後へ2行追記:

```python
    assert s.reeval_runner_interval_sec == 60  # M2 ws-6(design §2.8)
    assert s.reeval_runner_batch_limit == 50
    assert s.sweeper_batch_limit == 50  # M3 ws-2(06 §6・期限切れバッチ1tick上限)
    assert s.reset_retry_sec == 300  # M3 ws-2(04 §5・リセット失敗時の再試行間隔)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_settings.py::test_settings_defaults -v`
Expected: FAIL with "AttributeError: 'Settings' object has no attribute 'sweeper_batch_limit'"

- [ ] **Step 3: 最小実装**

(1) `backend/src/latch/settings.py` の末尾(reeval_runner_batch_limitの後)へ追記:

```python
    # --- 期限切れバッチ・リセットジョブ(M3 ws-2・06 §6・04 §5)---
    # expiry_sweeper(latches/Intent期限切れ・completed遷移)の1tickあたり処理
    # 上限。周期はreeval_runner_interval_secと同一スケジューラ(60秒・06 §6)
    sweeper_batch_limit: int = 50
    # リセットジョブ失敗時の再試行待機秒(04 §5「翌0時まで放置しない」)
    reset_retry_sec: int = 300
```

(2) `backend/src/latch/worker/main.py` を変更する。

import部(`from latch.worker.reeval import ReevalRunner` の前後へ2行追加):

```python
from latch.worker.reset import ResetJob
from latch.worker.reeval import ReevalRunner
from latch.worker.sweeper import ExpirySweeper
```

ReevalRunner構築ブロック(`if self._reeval_runner is None and redis_client is not None:` 内。コメントを更新しsweeper構築を追加):

```python
            # ReevalRunner DI(design §2.8): catch-up・Bucket再評価の周期task。
            # pipelineはengineを閉包した直接投入(_run_direct_pipeline)。
            # M3 ws-2(design §2.1案A): ExpirySweeperを注入し60秒tickを1本化
            # (latches・Intent期限切れ・catch-upの切替・停止は単一ジョブ)
            if self._reeval_runner is None and redis_client is not None:

                async def _pipeline(intent_id: uuid.UUID) -> None:
                    await self._run_direct_pipeline(engine, intent_id)

                sweeper = ExpirySweeper(
                    engine=engine,
                    clock=self.clock,
                    latch=self._latch,
                    batch_limit=self.settings.sweeper_batch_limit,
                )
                self._reeval_runner = ReevalRunner(
                    engine=engine,
                    clock=self.clock,
                    guard=self._reeval,  # この時点でrun()内構築済み(ReevalGuard)
                    pipeline=_pipeline,
                    interval_sec=self.settings.reeval_runner_interval_sec,
                    batch_limit=self.settings.reeval_runner_batch_limit,
                    sweeper=sweeper,
                )
            reeval_runner = self._reeval_runner
            # ResetJob DI(M3-4・design §2.5): 次JST 0時の掃除+drain。60秒系
            # とは別周期の独立task。cost_storeはJevWorker用と別インスタンス
            # (掃除専用・redis接続は共用)。sleepは渡さない(asyncio.sleep=本番待機)
            reset_job = None
            if redis_client is not None:
                reset_job = ResetJob(
                    cost_store=JevCostStore(redis_client),
                    clock=self.clock,
                    latch=self._latch,
                    retry_sec=self.settings.reset_retry_sec,
                )
```

task起動部(`reeval_task` 起動の後に追加。現行は `if reeval_runner is not None: reeval_task = asyncio.create_task(...)`)。`reeval_task` を先に `None` 初期化する形へ書き換え:

```python
            reeval_task = None
            if reeval_runner is not None:
                reeval_task = asyncio.create_task(reeval_runner.run(stop=self._stop))
            reset_task = None
            if reset_job is not None:
                reset_task = asyncio.create_task(reset_job.run(stop=self._stop))
```

graceful shutdown部(debouncer_task・backfill_task・reeval_taskのawaitの並びの最後に追加):

```python
            await debouncer_task
            await backfill_task
            if reeval_runner is not None:
                await reeval_task
            if reset_job is not None:
                await reset_task
```

(現行コードは `if reeval_runner is not None: reeval_task = asyncio.create_task(...)` の1文でtask変数を作っている。上の書き換えで `reeval_task = None` 初期化+条件代入へ変えること)

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_settings.py tests/unit/test_worker.py -v`
Expected: PASS(test_settings_defaults に新規2assert含む・test_worker.pyの3試験は無傷: Worker.run()の即shutdown構成ではResetJob/ReevalRunnerのrun_onceは呼ばれず、`reset_job.run` はwhile条件のstopチェックで即returnする)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/settings.py backend/tests/unit/test_settings.py backend/src/latch/worker/main.py
git commit -m "feat: wire ExpirySweeper and ResetJob into worker"
```

### Task 9: integration試験 `test_expiry_batches.py`(design §4.2)

**Files:**
- Create: `backend/tests/integration/test_expiry_batches.py`

**Interfaces:**
- Consumes: Task 6の `ExpirySweeper`・Task 3〜4の `ResetJob`・Task 7の `ReevalRunner(sweeper=...)`・Task 1の `LatchEngine.drain()`・Task 2の `JevCostStore`・既存の `POST /v1/intents`・`POST /v1/latches/{id}/response`(回答API)

**前提**: 実DB・実Redis・api常設(実HTTP)。sweeper/resetはFakeClock注入で直接構成(test_k_limits_e2e.pyのReevalRunner扱いと同型・Workerプロセスは立てない)。**期限切れの再現はFakeClock.advance**(apiプロセスのClockは差し替え不能な試験〔試験3〕のみDB値操作 — ws-1計画§9-2の規律)。FakeClockは `FakeClock(SystemClock().now())` で開始し、fixtureの期限列は**その時刻基準のオフセット**で書く(時刻源の1本化)。

- [ ] **Step 1: 試験ファイルを作成(共通部+試験1〜2)**

`backend/tests/integration/test_expiry_batches.py` を新規作成:

```python
"""期限切れバッチ群のintegration試験(M3 ws-2 design §4.2・02#4本体)。

実DB(compose常設)・実Redis・api常設(実HTTP)。sweeper/resetはFakeClock注入で
直接構成しrun_onceを呼ぶ(Workerプロセスは立てない — test_k_limits_e2eの
ReevalRunner扱いと同型)。期限切れの再現はFakeClock.advance(時刻源を1本化。
試験3のみapiプロセスClock(SystemClock)基準のためDB値操作 — ws-1計画§9-2規律)。
latches行はfixtureで直接INSERT(回答APIはlatches行しか読まない・ws-1流儀)。
"""

import asyncio
import json
import math
import random
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import JST, FakeClock, SystemClock
from latch.worker.cost import JevCostStore
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.reeval import ReevalRunner
from latch.worker.reset import ResetJob
from latch.worker.sweeper import ExpirySweeper

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120  # 時間窓はnow+5日系と分離(ws-1・M2 ws-8の対抗策規律)
SUBJECT_PREFIX = "m3ws2-"


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM latch_status_events WHERE latch_id IN"
                " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM calibration_records WHERE latch_id IN"
                " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM notifications WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
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
                "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
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


@pytest.fixture
async def redis_sweep(redis_client):
    """Redis試験ごとのkey_prefix(teardownで掃除・test_k_limits流儀)。"""
    prefix = f"m3ws2r-{uuid_mod.uuid4().hex[:8]}-"
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


async def _user(api_client, prefix: str):
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
        json={"display_name": "m3ws2", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured(*, start: str | None = None, expires: str | None = None) -> dict:
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": None},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }
    if expires is not None:
        d["expires_at"] = expires  # 明示期限(補完のスナップ回避・k_limits流儀)
    return d


async def _intent(
    api_client,
    headers,
    *,
    status: str = "active",
    start: str | None = None,
    expires: str | None = None,
) -> dict:
    resp = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={
            "raw_text": "分類用テキスト",
            "status": status,
            "structured_intent": _structured(start=start, expires=expires),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]


async def _latch(
    db_engine,
    intent_ids: list[str],
    *,
    now: datetime,
    status: str = "proposed",
    score: float = 0.85,
    deadline_offset: timedelta = timedelta(hours=2),
    expires_offset: timedelta = timedelta(hours=120),
) -> str:
    """latches行を直接INSERT(fixture)。期限列はnow基準のオフセット指定。"""
    proposal = {"headcount": len(intent_ids), "match_level": "medium"}
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (CAST(:ids AS uuid[]),
                        CAST(:proposal AS jsonb), :score, :status,
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in intent_ids],
                "proposal": json.dumps(proposal, ensure_ascii=False),
                "score": score,
                "status": status,
                "deadline": now + deadline_offset,
                "expires": now + expires_offset,
                "now": now,
            },
        )
    return str(res.first()[0])


def _unique_vec() -> str:
    """テスト毎に一意な768次元ランダム単位ベクトル(k_limits流儀)。"""
    components = [random.random() for _ in range(768)]
    norm = math.sqrt(sum(c * c for c in components)) or 1.0
    return "[" + ",".join(repr(c / norm) for c in components) + "]"


async def _set_embedding(db_engine, intent_id: str) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:vec AS vector),"
                " embedding_model = 'test' WHERE id = CAST(:id AS uuid)"
            ),
            {"vec": _unique_vec(), "id": intent_id},
        )


def _sweeper(db_engine, clock):
    """実LatchEngineをdrain対象に持つExpirySweeper(試験ごとに構築)。"""
    latch_engine = LatchEngine(engine=db_engine, clock=clock)
    return ExpirySweeper(
        engine=db_engine, clock=clock, latch=latch_engine, batch_limit=50
    )


async def _status(db_engine, table: str, row_id: str) -> str:
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(f"SELECT status FROM {table} WHERE id = CAST(:id AS uuid)"),
            {"id": row_id},
        )
        return res.scalar_one()


# --- 試験1: 02#4本体(G1引継ぎ・引用#20のlatch同時閉鎖・Review Focus 5) ---


async def test_1_intent_expiry_transition_and_event(api_client, db_engine, field):
    """API保存(expires明示)→期限内は対象外→FakeClock進行→expired遷移+
    match_eventsのexpiredイベント1回。参加candidateのlatchesも同tickで閉じる
    (latches.expires_at=参加Intent期限の最小値・引用#20)。"""
    clock = FakeClock(SystemClock().now())
    headers = await _user(api_client, field)
    intent = await _intent(
        api_client,
        headers,
        expires=(clock.now() + timedelta(hours=1)).isoformat(),
    )
    # 参加candidate行(期限はIntentと同時刻に切れるよう固定で直書き)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (ARRAY[CAST(:iid AS uuid)] || CAST(:other AS uuid),
                        CAST(:proposal AS jsonb), 0.85, 'candidate',
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
            """),
            {
                "iid": intent["id"],
                "other": uuid_mod.uuid4(),  # 単独Intent扱いの埋め草(評価は走らない)
                "proposal": json.dumps({"headcount": 2, "match_level": "medium"}),
                "deadline": clock.now() + timedelta(hours=2),
                "expires": clock.now() + timedelta(hours=1),  # Intent期限と同時
                "now": clock.now(),
            },
        )
    sw = _sweeper(db_engine, clock)
    assert await sw.run_once() == 0  # 期限内→対象なし(遷移なし)

    clock.advance(timedelta(minutes=61))  # 期限(expires=+1h)を経過
    assert await sw.run_once() == 2  # intent expired + latch candidate expired

    assert await _status(db_engine, "intents", intent["id"]) == "expired"
    async with db_engine.begin() as conn:
        ev = await conn.execute(
            text(
                "SELECT payload->>'version' FROM match_events"
                " WHERE source_intent_id = CAST(:iid AS uuid)"
                "   AND event_type = 'expired'"
            ),
            {"iid": intent["id"]},
        )
        rows = ev.fetchall()
    assert rows == [(str(intent["version"]),)]  # イベント1回・version一致
    # 参加candidate行もexpired(引用#20・Review Focus 5)
    async with db_engine.begin() as conn:
        latch_status = await conn.execute(
            text(
                "SELECT l.status FROM latches l"
                " WHERE CAST(:iid AS uuid) = ANY(l.intent_ids)"
                "   AND l.status <> 'candidate'"
            ),
            {"iid": intent["id"]},
        )
        assert latch_status.scalar_one() == "expired"

    # 再実行で変化なし(冪等・イベントは1回のまま)
    clock.advance(timedelta(minutes=1))
    assert await sw.run_once() == 0
    async with db_engine.begin() as conn:
        cnt = await conn.execute(
            text(
                "SELECT COUNT(*) FROM match_events"
                " WHERE source_intent_id = CAST(:iid AS uuid)"
                "   AND event_type = 'expired'"
            ),
            {"iid": intent["id"]},
        )
        assert cnt.scalar_one() == 1


# --- 試験2: latches期限切れmatrix(引用#2・Review Focus 2) ---


async def test_2_latches_expiry_matrix(api_client, db_engine, field):
    """①proposed×deadline切れ②proposed×expires切れ③candidate(nearby含む)×
    expires切れ④期限前proposed不変⑤candidate×暫定deadline過去・expires未来は
    不変(candidateはresponse_deadlineを判定に使わない・引用#2)。"""
    clock = FakeClock(SystemClock().now())
    base = clock.now()
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    i1 = await _intent(api_client, ha)
    i2 = await _intent(api_client, hb)
    i3 = await _intent(api_client, hb)
    i4 = await _intent(api_client, hb)
    i5 = await _intent(api_client, hb)
    l1 = await _latch(  # ① proposed×response_deadline切れ
        db_engine, [i1["id"], i2["id"]], now=base,
        status="proposed",
        deadline_offset=timedelta(hours=-1), expires_offset=timedelta(hours=120),
    )
    l2 = await _latch(  # ② proposed×expires_at切れ
        db_engine, [i1["id"], i3["id"]], now=base,
        status="proposed",
        deadline_offset=timedelta(hours=2), expires_offset=timedelta(hours=-1),
    )
    l3 = await _latch(  # ③ candidate(nearby・score<0.60)×expires切れ
        db_engine, [i1["id"], i4["id"]], now=base,
        status="candidate", score=0.30,
        deadline_offset=timedelta(hours=-1), expires_offset=timedelta(minutes=-1),
    )
    l4 = await _latch(  # ④ 期限前proposed(対象外)
        db_engine, [i1["id"], i5["id"]], now=base,
        status="proposed",
        deadline_offset=timedelta(hours=2), expires_offset=timedelta(hours=120),
    )
    l5 = await _latch(  # ⑤ candidate×暫定deadline過去・expires未来(不変)
        db_engine, [i2["id"], i3["id"]], now=base,
        status="candidate", score=0.85,
        deadline_offset=timedelta(hours=-1), expires_offset=timedelta(hours=120),
    )
    sw = _sweeper(db_engine, clock)
    assert await sw.run_once() == 3  # ①②③のみ
    got = {
        l1: "expired", l2: "expired", l3: "expired",
        l4: "proposed", l5: "candidate",
    }
    for latch_id, expected in got.items():
        assert await _status(db_engine, "latches", latch_id) == expected, latch_id
    # latch_status_events: expired遷移3行(システム起因=user_id NULL)
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT latch_id, from_status, to_status, user_id"
                " FROM latch_status_events WHERE to_status = 'expired'"
                "   AND latch_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [uuid_mod.UUID(x) for x in (l1, l2, l3)]},
        )
        rows = res.fetchall()
    assert len(rows) == 3
    assert all(r[2] == "expired" and r[3] is None for r in rows)
    from_statuses = {str(r[0]): r[1] for r in rows}
    assert from_statuses[l1] == "proposed"
    assert from_statuses[l3] == "candidate"
    # 不変行(④⑤)へのイベント書き込みなし
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT COUNT(*) FROM latch_status_events"
                " WHERE latch_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [uuid_mod.UUID(x) for x in (l4, l5)]},
        )
        assert res.scalar_one() == 0
```

- [ ] **Step 2: 試験1〜2がDB接続環境で通ることを確認**

Run: `cd backend && uv run pytest tests/integration/test_expiry_batches.py -v -k "test_1 or test_2"`
Expected: PASS(2件。※compose環境が必要 — §0の順序で `docker compose build api` → `make test-ci` の前段で個別実行する場合は `docker compose up -d --wait` 済みの状態で)

- [ ] **Step 3: 試験3〜5を追記(期限後回答409・draft→expired・matched→completed)**

`backend/tests/integration/test_expiry_batches.py` の末尾へ追記:

```python
# --- 試験3: 期限切れ直後の回答は409(api Clock=SystemClock基準・引用#3) ---


async def test_3_response_after_deadline_is_409(api_client, db_engine, field):
    """sweeper未実行のまま期限切れ(status=proposed)でも回答は受理されない
    (409 LATCH_EXPIRED)→sweeperでexpired化→expired後も409。
    apiプロセスのClockは差し替え不能のためdeadlineをDB直書きで過去へ
    (ws-1計画§9-2の等価置換と同一規律)。"""
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    i1 = await _intent(api_client, ha)
    i2 = await _intent(api_client, hb)
    now = SystemClock().now()
    latch_id = await _latch(
        db_engine, [i1["id"], i2["id"]], now=now,
        status="proposed",
        deadline_offset=timedelta(hours=2), expires_offset=timedelta(hours=120),
    )
    # deadlineを過去へ(sweeper遅延でstatusがproposedのままの状態を再現)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET response_deadline ="
                " CAST(:past AS timestamptz) WHERE id = CAST(:id AS uuid)"
            ),
            {"past": SystemClock().now() - timedelta(hours=1), "id": latch_id},
        )
    resp = await api_client.post(
        f"/v1/latches/{latch_id}/response",
        headers=ha,
        json={"response": "yes"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "LATCH_EXPIRED"
    assert await _status(db_engine, "latches", latch_id) == "proposed"  # 不変

    clock = FakeClock(SystemClock().now())
    assert await _sweeper(db_engine, clock).run_once() == 1
    assert await _status(db_engine, "latches", latch_id) == "expired"

    resp2 = await api_client.post(
        f"/v1/latches/{latch_id}/response",
        headers=ha,
        json={"response": "yes"},
    )
    assert resp2.status_code == 409, resp2.text
    assert resp2.json()["error"]["code"] == "LATCH_EXPIRED"  # expired後も同分類


# --- 試験4: draft→expired(05 §6・引用#5) ---


async def test_4_draft_intent_expires(api_client, db_engine, field):
    """active化されないまま期限が切れた下書きも対象(05 §6)。"""
    clock = FakeClock(SystemClock().now())
    headers = await _user(api_client, field)
    draft = await _intent(
        api_client,
        headers,
        status="draft",
        expires=(clock.now() + timedelta(hours=1)).isoformat(),
    )
    clock.advance(timedelta(minutes=61))
    assert await _sweeper(db_engine, clock).run_once() == 1
    assert await _status(db_engine, "intents", draft["id"]) == "expired"
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT COUNT(*) FROM match_events"
                " WHERE source_intent_id = CAST(:iid AS uuid)"
                "   AND event_type = 'expired'"
            ),
            {"iid": draft["id"]},
        )
        assert res.scalar_one() == 1  # draftもexpiredイベントを発行


# --- 試験5: matched→completed + attendance通知先行書き込み(引用#15/#16・
#     Review Focus 3) ---


async def test_5_matched_to_completed_attendance_notifications(
    api_client, db_engine, field
):
    """対象時刻(max time_start)経過のmatched→completed+completed_at+
    イベント(matched→completed・user_id NULL)+attendance_request通知
    (参加者全員・payload={latch_id})。cancelled(解散済み)には通知なし。"""
    clock = FakeClock(SystemClock().now())
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    i1 = await _intent(api_client, ha)  # time_start=+120h
    i2 = await _intent(api_client, hb)
    i3 = await _intent(api_client, ha)
    i4 = await _intent(api_client, hb)
    base = clock.now()
    l_matched = await _latch(
        db_engine, [i1["id"], i2["id"]], now=base, status="matched",
        deadline_offset=timedelta(hours=240), expires_offset=timedelta(hours=240),
    )
    l_cancelled = await _latch(
        db_engine, [i3["id"], i4["id"]], now=base, status="matched",
        deadline_offset=timedelta(hours=240), expires_offset=timedelta(hours=240),
    )
    # 解散済み(cancelled)を再現: 対象時刻は経過させるがstatusがmatchedでない
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET status = 'cancelled'"
                " WHERE id = CAST(:id AS uuid)"
            ),
            {"id": l_cancelled},
        )

    clock.advance(timedelta(hours=BASE_HOURS + 1))  # 対象時刻(+120h)を経過
    assert await _sweeper(db_engine, clock).run_once() == 1  # matchedのみ

    async with db_engine.begin() as conn:
        row = await conn.execute(
            text(
                "SELECT status, completed_at FROM latches"
                " WHERE id = CAST(:id AS uuid)"
            ),
            {"id": l_matched},
        )
        status, completed_at = row.one()
    assert status == "completed"
    assert completed_at is not None
    # イベント(matched→completed・user_id NULL)
    async with db_engine.begin() as conn:
        ev = await conn.execute(
            text(
                "SELECT from_status, to_status, user_id FROM latch_status_events"
                " WHERE latch_id = CAST(:id AS uuid)"
            ),
            {"id": l_matched},
        )
        assert ev.one() == ("matched", "completed", None)
        # attendance_request通知: matched分は参加者2人・cancelled分は0件
        n_matched = await conn.execute(
            text(
                "SELECT COUNT(*) FROM notifications"
                " WHERE type = 'attendance_request'"
                "   AND payload->>'latch_id' = :lid",
            ),
            {"lid": l_matched},
        )
        assert n_matched.scalar_one() == 2
        n_cancelled = await conn.execute(
            text(
                "SELECT COUNT(*) FROM notifications"
                " WHERE type = 'attendance_request'"
                "   AND payload->>'latch_id' = :lid",
            ),
            {"lid": l_cancelled},
        )
        assert n_cancelled.scalar_one() == 0
    # cancelled行はcompletedにしない(対象外のまま)
    assert await _status(db_engine, "latches", l_cancelled) == "cancelled"
```

- [ ] **Step 4: 試験3〜5が通ることを確認**

Run: `cd backend && uv run pytest tests/integration/test_expiry_batches.py -v -k "test_3 or test_4 or test_5"`
Expected: PASS(3件)

- [ ] **Step 5: 試験6〜8を追記(並行・クローズ検知drain・統合回帰)**

`backend/tests/integration/test_expiry_batches.py` の末尾へ追記:

```python
# --- 試験6: SKIP LOCKED簡易並行性(引用#3・Review Focus 1) ---


async def test_6_concurrent_run_once_single_transition(api_client, db_engine, field):
    """同一sweeperのrun_onceを並行2重実行→同一行が二重に遷移しない
    (latch_status_eventsのexpired遷移は行ごとに1行のみ)。"""
    clock = FakeClock(SystemClock().now())
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    i1 = await _intent(api_client, ha)
    i2 = await _intent(api_client, hb)
    i3 = await _intent(api_client, ha)
    i4 = await _intent(api_client, hb)
    i5 = await _intent(api_client, ha)
    i6 = await _intent(api_client, hb)
    base = clock.now()
    ids = []
    for a, b in ((i1, i2), (i3, i4), (i5, i6)):
        ids.append(
            await _latch(
                db_engine, [a["id"], b["id"]], now=base,
                status="proposed",
                deadline_offset=timedelta(hours=-1),
                expires_offset=timedelta(hours=120),
            )
        )
    sw = _sweeper(db_engine, clock)
    await asyncio.gather(sw.run_once(), sw.run_once())
    for latch_id in ids:
        assert await _status(db_engine, "latches", latch_id) == "expired"
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT latch_id, COUNT(*) FROM latch_status_events"
                " WHERE to_status = 'expired'"
                "   AND latch_id = ANY(CAST(:ids AS uuid[]))"
                " GROUP BY latch_id HAVING COUNT(*) > 1"
            ),
            {"ids": [uuid_mod.UUID(x) for x in ids]},
        )
        assert res.fetchall() == []  # 二重遷移なし


# --- 試験7: クローズ検知drain(§2.7・06 §10クローズ側トリガーの消費) ---


async def test_7_close_detection_drain_promotes_candidate(
    api_client, db_engine, field
):
    """D-08同時3件上限で保留(candidate)の行が、参加提案の1つのrejectedを
    sweeperが観測(latch_status_events)してdrain→proposedへ昇格する。
    response_deadlineはD-05再計算・proposal通知は参加者2人分。"""
    clock = FakeClock(SystemClock().now())
    base = clock.now()
    ha = await _user(api_client, field)  # a1〜a4の所有者
    hb = await _user(api_client, field)  # w1〜w4の所有者
    a1 = await _intent(api_client, ha)
    a2 = await _intent(api_client, ha)
    a3 = await _intent(api_client, ha)
    a4 = await _intent(api_client, ha)
    w1 = await _intent(api_client, hb)
    w2 = await _intent(api_client, hb)
    w3 = await _intent(api_client, hb)
    # a4のD-08同時3件を消費済みの既存proposed×3(期限は未来・sweeper対象外)
    l_a1 = await _latch(db_engine, [a1["id"], w2["id"]], now=base, status="proposed")
    l_a2 = await _latch(db_engine, [a2["id"], w3["id"]], now=base, status="proposed")
    l_a3 = await _latch(db_engine, [a3["id"], w1["id"]], now=base, status="proposed")
    # 対象の保留行(a4・w1: 上限が空けば昇格できる)
    target = await _latch(
        db_engine, [a4["id"], w1["id"]], now=base,
        status="candidate", score=0.85,
    )

    latch_engine = LatchEngine(engine=db_engine, clock=clock)
    sweeper = ExpirySweeper(
        engine=db_engine, clock=clock, latch=latch_engine, batch_limit=50
    )
    # 事前: drainを直接呼んでもa4はproposed 3件で上限→candidateのまま
    await latch_engine.drain()
    assert await _status(db_engine, "latches", target) == "candidate"

    # a1参加のlatchを回答APIでno→rejected(latch_status_events挿入)
    resp = await api_client.post(
        f"/v1/latches/{l_a1}/response", headers=ha, json={"response": "no"}
    )
    assert resp.status_code == 200, resp.text

    # sweeper: 期限切れ対象なし・観測でrejectedを検知→drain→保留行が昇格
    assert await sweeper.run_once() == 0
    assert await _status(db_engine, "latches", target) == "proposed"
    async with db_engine.begin() as conn:
        row = await conn.execute(
            text(
                "SELECT response_deadline FROM latches WHERE id = CAST(:id AS uuid)"
            ),
            {"id": target},
        )
        deadline = row.scalar_one()
    assert deadline > clock.now()  # D-05再計算(未来)
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT COUNT(*) FROM notifications WHERE type = 'proposal'"
                "   AND payload->>'latch_id' = :lid"
            ),
            {"lid": target},
        )
        assert res.scalar_one() == 2  # a4・w1の参加者2人分(mutedなし)


# --- 試験8: catch-up統合の回帰(design §2.1・§4.2-7) ---


async def test_8_reeval_runner_integrates_sweeper(
    api_client, db_engine, field
):
    """ReevalRunner+sweeper注入構成: run_onceが「期限切れ処理→catch-up抽出」
    の順に両方実行する。期限切れintentはexpired化・catch-up対象(期限2時間
    以内・embeddingあり)はpipelineへ投入。既存注入なし構成はunit側で担保。"""
    clock = FakeClock(SystemClock().now())
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    # a: catch-up対象(期限2時間以内・embedding直書き)
    a = await _intent(
        api_client, ha, expires=(clock.now() + timedelta(minutes=90)).isoformat()
    )
    await _set_embedding(db_engine, a["id"])
    # b: 期限切れ対象(+60分 → advance 61分で経過)
    b = await _intent(
        api_client, hb, expires=(clock.now() + timedelta(minutes=60)).isoformat()
    )

    pipelined: list[str] = []
    latch_engine = LatchEngine(engine=db_engine, clock=clock)
    sweeper = ExpirySweeper(
        engine=db_engine, clock=clock, latch=latch_engine, batch_limit=50
    )

    async def pipeline(intent_id):
        pipelined.append(str(intent_id))

    runner = ReevalRunner(
        engine=db_engine,
        clock=clock,
        guard=None,  # ガード判定不要(投入の記録のみ・実L1〜3はk_limitsで担保)
        pipeline=pipeline,
        interval_sec=0.01,
        batch_limit=50,
        sweeper=sweeper,
    )
    clock.advance(timedelta(minutes=61))  # b期限切れ・aは残29分(2時間以内)
    done = await runner.run_once()
    assert done == 1  # catch-upはaのみ(bはsweeperがexpired化済みで対象外)
    assert pipelined == [a["id"]]
    assert await _status(db_engine, "intents", b["id"]) == "expired"  # sweeper先行
```

- [ ] **Step 6: 試験6〜8が通ることを確認**

Run: `cd backend && uv run pytest tests/integration/test_expiry_batches.py -v -k "test_6 or test_7 or test_8"`
Expected: PASS(3件)

- [ ] **Step 7: 試験9a〜9bを追記(リセットジョブ)**

`backend/tests/integration/test_expiry_batches.py` の末尾へ追記:

```python
# --- 試験9a: リセットジョブのキー掃除(design §2.5・§4.2-8) ---


async def test_9a_reset_job_key_cleanup(db_engine, redis_client, redis_sweep):
    """run_once(0時発火の本体): 前日キー4種を消滅・当日キーは保持。
    月初(11-01)は前月(10月)月次も消滅・月初以外(10-15)は当月月次が残存。"""
    store = JevCostStore(redis_client, key_prefix=redis_sweep)
    # 月初0時5秒(2026-11-01 JST)を再現
    clock = FakeClock(datetime(2026, 11, 1, 0, 0, 5, tzinfo=JST))
    await store.incr_daily("20261031")
    await store.record_execution("typesafe_jev", "20261031")
    await store.incr_intent("i-1", "20261031")
    await store.incr_user("u-1", "20261031")
    await store.incr_daily("20261101")  # 当日分(消さない)
    await store.incr_monthly("202610")  # 前月(月初→掃除対象)
    job = ResetJob(
        cost_store=store,
        clock=clock,
        latch=LatchEngine(engine=db_engine, clock=clock),
        retry_sec=300,
    )
    await job.run_once()
    p = redis_sweep
    assert await redis_client.get(f"{p}jev:daily:20261031") is None
    assert await redis_client.get(f"{p}jev:exec:20261031:typesafe_jev") is None
    assert await redis_client.get(f"{p}jev:intent:i-1:20261031") is None
    assert await redis_client.get(f"{p}jev:user:u-1:20261031") is None
    assert await redis_client.get(f"{p}jev:daily:20261101") == "1"  # 当日保持
    assert await redis_client.get(f"{p}jev:monthly:202610") is None  # 月初掃除

    # 月初以外(2026-10-15): 当月月次は残存
    clock.set(datetime(2026, 10, 15, 0, 0, 5, tzinfo=JST))
    await store.incr_monthly("202610")
    await job.run_once()
    assert await redis_client.get(f"{p}jev:monthly:202610") == "1"  # 残存


# --- 試験9b: 0時跨ぎdrainでD-08日次上限が回復(引用#11/#17) ---


async def test_9b_reset_job_drain_recovers_d08_daily_quota(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """前日に通知6件(D-08日次上限到達)のユーザーの保留candidateが、
    0時跨ぎ後のdrainで提示される(カウント真実=notificationsの日付条件切替)。"""
    clock = FakeClock(SystemClock().now())
    ha = await _user(api_client, field)
    hb = await _user(api_client, field)
    a = await _intent(api_client, ha)
    w = await _intent(api_client, hb)
    target = await _latch(
        db_engine, [a["id"], w["id"]], now=clock.now(),
        status="candidate", score=0.85,
    )
    # haの当日分通知6件(type='proposal'・上限到達の再現)
    async with db_engine.begin() as conn:
        for _ in range(6):
            await conn.execute(
                text(
                    "INSERT INTO notifications (user_id, type, payload, created_at)"
                    " VALUES (CAST(:uid AS uuid), 'proposal',"
                    " CAST(:payload AS jsonb), CAST(:now AS timestamptz))"
                ),
                {
                    "uid": uuid_mod.UUID(a["user_id"]),
                    "payload": json.dumps({"latch_id": str(uuid_mod.uuid4())}),
                    "now": clock.now(),
                },
            )
    latch_engine = LatchEngine(engine=db_engine, clock=clock)
    # 事前(当日): 6件上限で昇格しない
    await latch_engine.drain()
    assert await _status(db_engine, "latches", target) == "candidate"

    # 翌日JST 0時5秒へ跨ぐ(リセットジョブの発火時刻)
    now_jst = clock.now().astimezone(JST)
    next_day = now_jst + timedelta(days=1)
    clock.set(
        datetime(next_day.year, next_day.month, next_day.day, 0, 0, 5, tzinfo=JST)
    )
    # cost_storeは実JevCostStore(掃除対象キーがなくても无害・teardownで掃除)
    job = ResetJob(
        cost_store=JevCostStore(redis_client, key_prefix=redis_sweep),
        clock=clock,
        latch=latch_engine,
    )
    await job.run_once()
    assert await _status(db_engine, "latches", target) == "proposed"
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT COUNT(*) FROM notifications WHERE type = 'proposal'"
                "   AND payload->>'latch_id' = :lid"
                "   AND created_at >= CAST(:day AS timestamptz)"
            ),
            {"lid": target, "day": clock.now()},
        )
        assert res.scalar_one() == 2  # 翌日分として参加者2人へ提示
```

- [ ] **Step 8: 試験9a〜9bが通ることを確認**

Run: `cd backend && uv run pytest tests/integration/test_expiry_batches.py -v -k "test_9"`
Expected: PASS(2件)

- [ ] **Step 9: 全integration試験が通ることを確認**

Run: `cd backend && uv run pytest tests/integration/test_expiry_batches.py -v`
Expected: PASS(10件: 試験1〜8+9a/9b)

- [ ] **Step 10: コミット**

```bash
git add backend/tests/integration/test_expiry_batches.py
git commit -m "test: add expiry batch integration tests (02#4)"
```

### Task 10: 報告書作成・完了条件の検証

**Files:**
- Create: `docs/plans/M3/ws-2-report.md`

- [ ] **Step 1: 完了条件1〜7を§6のコマンドで検証し、結果を§7の形式で報告ファイルへ書く**

検証コマンド一式(§6の7条件・順に実行して出力を記録):

```bash
make lint && make test
docker compose build api
make test-ci
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock Makefile
git diff --name-only main | sort
git status --short
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
```

- [ ] **Step 2: 残存確認(§7検証手順6のSQL)を実行し報告書へ記録**

```bash
docker compose exec -T db psql -U latch -d latch -c \
  "SELECT COUNT(*) FROM users WHERE auth_subject LIKE 'm3ws2-%';"
docker compose exec -T db psql -U latch -d latch -c \
  "SELECT COUNT(*) FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'm3ws2-%');"
docker compose exec -T db psql -U latch -d latch -c \
  "SELECT COUNT(*) FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'm3ws2-%'));"
docker compose exec -T db psql -U latch -d latch -c \
  "SELECT COUNT(*) FROM latch_status_events WHERE latch_id IN (SELECT id FROM latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'm3ws2-%')));"
docker compose exec -T db psql -U latch -d latch -c \
  "SELECT COUNT(*) FROM notifications WHERE user_id IN (SELECT id FROM users WHERE auth_subject LIKE 'm3ws2-%');"
```

すべて0件であること。Redisは `m3ws2r-` prefixでSCANして0件:

```bash
docker compose exec -T redis redis-cli --scan --pattern 'm3ws2r-*'
```

- [ ] **Step 3: コミット**

```bash
git add docs/plans/M3/ws-2-report.md
git commit -m "docs: ws-2 implementation report"
```

## 9. IF確定事項(実装定義の決定値。docs・design.mdが例示のみの部分を本計画で確定する。変更時は報告書に記録)

1. **`ExpirySweeper.run_once() -> int` の戻り値は遷移させた行数**(latches期限切れ+Intent期限切れ+completedの合計。drain呼び出しは含まない。drainで昇格・破棄された行はカウントしない — ReevalRunnerの投入件数との混同を防ぐ)。drain自体の結果検証はDB状態で行う
2. **SQL定数は `worker/sweeper.py` 内へ自前定義する**(`_INSERT_LATCH_EVENT_SQL` は latch_engine `_INSERT_LATCH_EVENT` と同一SQL・`_INSERT_ATTENDANCE_NOTIFICATION` は latch_engine `_INSERT_NOTIFICATION` と同一SQL)。stage1が `_INSERT_LATCH_EVENT_SQL` を自前定義している前例と同じ(design §1.3「参照実装」の位置づけ・import結合を増やさない)。唯一の例外は `insert_match_event`/`EVENT_EXPIRED`(関数・定数をimportして使う — 保存と同一慣行)
3. **attendance_request通知はD-08日次上限カウントに含めない**(latch_engine `_COUNT_DAILY_NOTIFICATIONS` の `type IN ('proposal', 'nearby_candidate')` は無変更。D-08の上限は提示系通知の抑制が目的で、申告依頼は1回限りの完了通知のため。本判断は実装定義 — 変更要望があればws-3で再検討)
4. **ResetJob.run_onceをpublicとし、integration試験9a/9bはrun_once直呼びで0時跨ぎを再現する**(Clockを0時過ぎへsetしてから呼ぶ — design §2.5「0時丁度でなく数秒遅れで発火してよい」と等価。run()ループの待機・再採取はunit試験の注入sleep+FakeClockで検証済みであり、design §4.2-8の「sleep短縮→0時跨ぎ」に相当する再現をunit/integrationで分担する)
5. **ResetJob._waitは「注入sleepとstop待ちの並行待機」**(`asyncio.wait(FIRST_COMPLETED)` — 両方がtaskを1つ作る)。本番(main.py)ではsleepを渡さない(asyncio.sleep既定)。worker_envの `_instant` 注入の影響を受けない(0時までの長期待機がbusy loop化するのを防ぐ)
6. **掃除対象は `jev:daily:{前日}` DEL・`jev:exec:{前日}:*`/`jev:intent:*:{前日}`/`jev:user:*:{前日}` のSCAN+DEL・月初のみ `jev:monthly:{前月}` DEL**(design §2.5の擬似コードどおり)。`reeval:{intent_id}` キーは掃除対象外(TTL 30分で自然消滅・引用#18の趣旨)。バケット文字列の書式はguard `_day_bucket`/`_month_bucket` と同一(`%Y%m%d`・`%Y%m`)
7. **抽出のFOR UPDATE SKIP LOCKEDは抽出tx内のみ(即コミット)**。行単位txの条件付きUPDATEが第2防御となる(design §2.2「行ごとに個別txで閉じる」の読み)。SKIP LOCKEDの効果は「他txがロック中の行を抽出から除外」であり、抽出〜UPDATEの間の競合は影響行数0で吸収する
8. **`next_jst_midnight(now)` と `_prev_month_key(today)` は `worker/reset.py` 内のmodule関数**(`core/clock.py` は無変更・design §1.3)。nowがJST 0時丁度のときは翌日0時を返す(待機0秒のbusy loopを生まない)
9. **completed遷移時のattendance通知の宛先は `_SELECT_LATCH_PARTICIPANT_USERS`(intents.user_id)を行順に全員**。参加Intent欠損時は取得できた分のみ(matched行は削除経路でcancelledへ遷移するため欠損は前提にならない。payloadは `{"latch_id": str(latch_id)}` の最小参照・引用#16)
10. **integration試験のFakeClockは `FakeClock(SystemClock().now())` で開始**し、fixtureの期限列はその時刻基準のオフセットで書く(worker_env流儀・時刻源1本化)。apiプロセスのClockが関与する試験3(409)のみDB値操作で期限切れを再現する(ws-1計画§9-2の規律)
11. **ExpirySweeperの `_last_tick` 初期値は `__init__` での `clock.now()`(起動時刻)**。run_onceは**成功時のみ**末尾で `_last_tick = now` へ更新する(例外時は更新しない=観測窓の取りこぼしなし・Review Focus 4)
12. **sweeperの固定文言ログ**: `sweeper.expired latch_id=%s from=%s`・`sweeper.intent_expired intent_id=%s`・`sweeper.completed latch_id=%s`・`reset run_once failed`(warning・exc_info付き)のみ。機微(Proposal本文・soft_constraints)は出さない(08 §2.4)
13. **`worker/main.py` のReevalRunner構築条件(`redis_client is not None`)はsweeperも同一条件に含める**(統合スケジューラのため、スケジューラが構築されない構成ではsweeperも構築しない)。ResetJobも同一条件(cost_storeがRedis必須)。`_latch`(LatchEngine)はこの時点で構築済み
14. **試験9bの「前日6件」は「当日(FakeClock開始日のJST暦日)6件」として `created_at=clock.now()` で仕込む**(実行時刻が当日のどこででも成立するように。0時跨ぎを `clock.set(翌日0時5秒JST)` で再現し、day_start切替でカウント0になることを検証する)

## Self-Review記録(計画書作成時の確認 — 2026-10-01)

1. **design §1.1の6項目の対応**: ①latches期限切れバッチ→Task 5(SQL)+Task 6(_expire_latch)+試験2 ②Intent期限切れバッチ+expiredイベント→Task 5+Task 6(_expire_intent)+試験1/4 ③catch-up統合→Task 7+Task 8+試験8 ④リセットジョブ→Task 2+3+4+試験9a/9b ⑤matched→completed→Task 5+Task 6(_complete_latch)+試験5 ⑥G1引継ぎ02#4→試験1。design §2.8の切り捨て一覧は§5へ一覧化。design §2.6(drain直接実行)→Task 1+試験7/9b。design §2.7(クローズ検知)→Task 6+試験7
2. **placeholder scan**: 「TBD/TODO/適切に/同様に」なし。全SQL・関数シグネチャ・テストコードを本文に記載。Task 9 Step 7の試験9bは確定形へ統一済み(書きかけ形式は除去)
3. **型整合**: `ExpirySweeper(*, engine, clock, latch, batch_limit)` とTask 7/8の呼び出し整合。`ResetJob(*, cost_store, clock, latch, retry_sec, sleep)` とTask 8の構築・Task 9a/9bの直構築整合。`JevCostStore.delete_daily(day: str)`/`scan_delete(pattern: str)` とResetJob.run_onceの呼び出し(`strftime("%Y%m%d")` の文字列)整合。`drain()`(Task 1)とsweeper/resetからの呼び出し整合。`ReevalRunner(..., sweeper=None)`(Task 7)とmain.pyの `sweeper=sweeper`(Task 8)整合
4. **Review Focus**: 1(競合影響0)→Task 6 unit+試験6。2(candidate暫定deadline)→Task 5 SQLピン+試験2⑤。3(cancelled通知/時間前completed)→Task 5 SQLピン+試験5。4(last_tick更新順序・再帰なし)→Task 6 unit(例外時不変+順序ピン)。5(Intent/latch同時閉鎖)→試験1。すべて所有タスクの試験でピン留め
5. **機械チェック(M2/M3 ws-1踏襲)**: basename一意=§0に機械確認結果(既存111ファイル・新規3ファイルとも衝突なし。2026-10-01実施)。ピン試験への影響訪問=latch_engineの_drain→drain改名はtest_latch_engine.py 952/969の機械的置換のみ(期待値変更なし・Task 1)/reeval.pyのsweeperオプションは既定Noneで既存12試験無傷(Task 7で従動作試験を明示)/settings.py追記はtest_settings_defaultsへ2行追記のみ(test_llm_factory.pyはLLM系統設定を参照し本2キーに触らないため無傷)/main.py配線はtest_worker.pyの即shutdown構成でrun_once未実行のため無傷(Task 8 Step 4で明示)。DB/Redis残存干渉対抗策=subject prefix `m3ws2-`+FK順teardown(latch_status_events・notificationsを含む・Task 9 Step 1)+時間窓BASE_HOURS=120分離+Redis prefix `m3ws2r-`(Task 9 redis_sweep)
6. **design §4.2の9項目とTask 9の対応**: 1(02#4)→試験1。2(latches期限切れ群①〜④)→試験2。2⑤(期限切れ直後の回答409)→試験3として分離実装(回答API試験であるため・apiプロセスのSystemClock基準のDB値操作が必要)。試験2の⑤(candidate×暫定deadline過去・expires未来の不変)は引用#2のピンとして追加。3(draft)→試験4。4(matched→completed)→試験5。5(SKIP LOCKED)→試験6。6(クローズ検知drain)→試験7。7(catch-up統合)→試験8+Task 7 unit。8(リセットジョブ)→試験9a/9b+Task 3/4 unit(runループの0時跨ぎ)。9(teardown)→field fixture
7. **報告形式の検証手順**: design §4.3の5項目(①build+test-ci ②lint再実行 ③basename ④alembic head不変 ⑤残存ゼロ)を§7検証手順1〜6へ全対応
