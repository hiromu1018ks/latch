# M3 ws-5(ブロック・通報)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ブロックと通報のAPI面を実装し、D-23(成立後のブロック即時適用)を確定する — ①blocks API: `POST /v1/users/{user_id}/block`(冪等201・D-23 cancelled化を同tx内で同期実行)・`DELETE /v1/users/{user_id}/block`(204・行なし404・遡及効果なし)・`GET /v1/users/me/blocks`(cursor改頁・display_nameつき)②Redisブロックキャッシュ(`blk:u:{user_id}`=JSON配列・read-through・コミット後DEL・Redis断はDBフォールバック)へws-4が明け渡した`store.select_block_between`差し替え点を載せ替える③reports API: `POST /v1/reports`(受付・記録のみ・reason 4値コード・status='pending'固定)。**マイグレーション追加なし(head=0006不変)・依存追加なし**。

**Architecture:** design §2.1〜§2.6 — `safety/`を新設(6ファイル。latches cancelled化・Redisキャッシュ・cursor改頁というusersドメインの外にある依存を3つ持つため)。D-23 cancelled化はブロック登録APIの**単一トランザクション内で同期実行**(案A: ws-1の競合クローズ`cancel_latch`+`insert_latch_event`をそのまま再利用・対象はcandidateを含む3状態・matched除外・参加Intentへは触らない・通知は送らない)。キャッシュは案A(ユーザー単位JSON一覧): MGET一括→欠落はDBから読み`SET EX 3600`(空は`"[]"`)→メモリ双方向判定。反映は**DBコミット後**に両者のキーをDEL(コミット前DELは並行read-throughがコミット前DBで旧値を再キャッシュする窓を残すため)。Redis断(RedisError)は安全性優先でDBの`select_block_between`へフォールバック(キャッシュは性能の最適化であって真実はDBにある)。`LatchesService`へ`block_cache`を注入し、`send_message`は注入時キャッシュ優先・未注入時は従来どおりDB直読み(既存試験互換)。

**Tech Stack:** 変更なし(Python 3.13 / FastAPI / SQLAlchemy[asyncio]+asyncpg text()生SQL / redis.asyncio / pytest)。

**Spec:** `docs/plans/M3/ws-5-design.md`(agent1設計メモ。**未解決論点なし** — design §5の5件は2026-10-01スーパーバイザー承認済み・G3時確認事項への登録済み。①Layer 1(worker側)のblocks参照はSQL直読きのまま②D-23 cancelled化の対象にcandidateを含める③ブロック解除後にチャット送信が可能に戻る(現物参照)④1対1ブロックでセッション失効リストを使わない⑤display_name込み応答・reason英語コード化・status値域pending/reviewed/resolved確定。design.mdが本計画より優先)。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m3-ws-5`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(**依存追加なし**のためlockは進まない)。
- **【最重要】共有ci-dbへの `make test-ci` / `make migrate` は禁止**(STATUS運用ルール1。マイグレーション追加はしないが、ws-6∥ws-7並走時に備えた標準規律として運用する)。開発は**unit試験(`make test`)のみ**で進める。integration試験ファイル(`tests/integration/test_safety_api.py`)は**作成するが実行しない** — インポートエラー検出のため `--collect-only` を実行してよい(conftestのimport時副作用はenv設定のみでDB接続しない)。報告ファイルには「**test-ci=スーパーバイザー検証待ち**」と記録する。`docker compose build api` を含めcompose系コマンド(docker build/pull/up/down/migrate相当)は一切実行しない。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-10-01)**: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空(既存121ファイル)。**design §4.1のファイル名のうち3件(`test_store_sql.py`・`test_service.py`・`test_routes.py`)は既存(intents・auth)とbasename衝突するため、本計画では `test_safety_` 接頭辞へ変更する**(`test_safety_cache.py`・`test_safety_store_sql.py`・`test_safety_service.py`・`test_safety_routes.py` — §9-1)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **マイグレーション追加なし・依存追加なし・設定追加なし**: `backend/alembic/` は一切触らない(head=0006不変)・`backend/pyproject.toml`・`backend/uv.lock`・`Makefile`・`.env`・`.env.example`・`settings.py` に触れない。
- **ws-6・ws-7が並行で実行される可能性**: `tests/unit/test_rate_limit_wiring.py` は並走単位も追記し得る。マージはスーパーバイザーが両側保持で解消するため、**追記位置はルート列挙(アルファベット順)の機械的該当箇所でよい**(Task 5)。worktree内で他単位の追記が見えなくても気にしない。
- **固定値の遵守**: design §2 の採用判断(案A・SQL・手順)と本計画§2のグローバル制約は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `refactor:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| blocksは単方向の記録で、マッチングは (A,B)(B,A) の双方向を確認して候補から除外する。Layer 1の参照は毎回DBを見ずRedisキャッシュから行い、ブロック設定の反映はキャッシュ更新を経由する。解除はDELETEで、遡及効果は持たない(候補除外・読み取り専用化を取り消さない) | 08 §5.1・引用#1 |
| 通報はPOST /v1/reportsで受け付け、運用者の手動対応に回す(受付・記録→レビュー→対処)。MVPでは自動検知・自動停止を行わない。通報理由は選択式(不適切な内容/不快な対応/なりすまし疑い/その他)。通報は提案画面・チャット画面から常に可能 | 08 §5.2・引用#2 |
| D-23: ブロックは成立後も即時に適用。効果は3つ — ①今後の候補生成から除外 ②進行中の提案(proposed/partial_accept)はcancelledで閉じる ③成立済み(matched)のLATCHはチャットを読み取り専用化。completedへの遷移は通常どおり。解放済みの情報の回収は行わない。ブロックされた側には「このチャットは利用できません」のみ表示し、ブロックされた事実を直接通知しない | 08 D-23・引用#3 |
| latches遷移: proposed・partial_accept→cancelledのトリガーに「ブロック」が明記。matched→cancelledは参加Intentの削除のみ(ブロックではmatchedをcancelledにしない) | 05 §6・引用#4 |
| blocksテーブル: id/blocker_id/blocked_id/created_at(0001作成済み)。解除は当該行の削除。UNIQUE(blocker_id, blocked_id)はDB制約に入れずアプリ層で担保 | 05 §2・引用#5・#16 |
| reportsテーブル: id/reporter_id/reportee_id/latch_id/reason/status/created_at(0001作成済み)。latch_idはNULL可。status値域はM3で確定(pending→reviewed→resolved・design §2.5) | 05 §2・引用#6・#16 |
| API表: GET /v1/users/me/blocks(本人のみ)・POST /v1/users/{id}/block(本人操作)・DELETE /v1/users/{id}/block(本人操作・遡及なし)・POST /v1/reports(本人操作) | 05 §5・引用#7 |
| 改頁共通規定: `?cursor=&limit=`(limit 1〜100・既定20・超過は422)。応答は`{"items": [...], "next_cursor"}`。blocksの既定ソートはcreated_at降順 | 05 §5・引用#8 |
| messages: ブロック適用中のLATCHは読み取り専用(書き込みは409 CHAT_READONLY・ws-4実装済み)。取得(閲覧)は参加者ならstatusを問わず可能 | 05 §2・05 §5・引用#9 |
| Redisの用途は4つ(セッション失効リスト・ブロックリスト・友人関係〔MVP外〕・Jev実行カウンタ)。ブロックリストはHard Filterが高頻度参照 | 04 §2・引用#10 |
| ブロックによるcancelledでも参加Intentはactiveのまま(復帰処理不要) | 06 §6・引用#11 |
| latch_status_eventsは遷移を書くトランザクションと同時に挿入。user_idは遷移の引き金となったユーザー(ブロック登録時はblocker) | 05 §2・06 §10・引用#12 |
| 不成立の理由は「この提案は成立しませんでした」の一文に統一し、ブロックされた事実は一切開示しない(cancelled化の通知は送らない) | 03 §5・引用#13 |
| すべてのAPIは認証済みユーザーのみ。ブロックの判定はクライアント入力によらずサーバ側で強制 | 08 §5.3・01 §22・引用#14 |
| エラーcode列挙にblocks/reports専用codeの定義なし(404 NOT_FOUND・422 VALIDATION_ERROR・503 DEPENDENCY_UNAVAILABLE等の共通codeで構成) | 05 §5・引用#15 |
| 競合クローズはcandidate・proposed・partial_acceptのlatchesをcancelledへ(条件付きUPDATE+ORDER BY idのFOR UPDATE。`cancel_latch`・`select_conflicting_latches`を再利用) | 05 §6・引用#17・ws-1実装 |
| sweeperのクローズ検知drainがcancelledイベントを観測して保留キュー再評価(本単位はイベント挿入まで・追加実装不要) | 06 §10・引用#18・ws-2実装 |
| supervisor承認(design §5): ①Layer 1はSQL直読き維持(G3時確認)②candidate含み(G3時確認)③解除後は現物参照で送信可(G3時確認)④セッション失効リスト不使用(G3時確認)⑤display_name/reasonコード/status値域(フロント共有値) | design §5 |

## 2. グローバル制約(全タスクに暗黙に適用・design §2の固定値)

- **モジュール構成(design §2.1)**: `backend/src/latch/safety/`を新設し、`__init__.py`・`errors.py`・`store.py`・`cache.py`・`service.py`・`routes.py`の6ファイル。ルータは `APIRouter(prefix="/v1", tags=["safety"], dependencies=[Depends(api_rate_limited)])` で、パスは `/users/me/blocks`・`/users/{user_id}/block`・`/reports`(`/v1/users/{user_id}/block` はusers_routerに存在せず衝突なし)。DIは `get_safety_service`(app.state経由・既存パターン)。`latches/`への接触は service.py からの store 再利用(`fetch_user_id`・`select_latch`・`fetch_participant_user_ids`・`cancel_latch`・`insert_latch_event`)と main.py 構成・service.py 差し替えのみ。
- **キャッシュの形(design §2.2案A)**: `blk:u:{user_id}` = `'["<blocked_uuid>", ...]'`(そのユーザーがブロックしている相手一覧のJSON配列)・`SET EX 3600`(TTLは掃除用ではなくDEL失敗時の最終収束期間)。接頭辞 `blk:` は `auth:`・`rl:` と名前空間を分ける。判定 `BlockCache.is_blocked_between(me, others)`: ①`MGET`(送信1回)②欠落は `SELECT blocker_id, blocked_id FROM blocks WHERE blocker_id = ANY(:ids)` で一括DB読み→SET(空は`"[]"`)③メモリ判定(自分の一覧にothersの誰か、またはothersのいずれかの一覧に自分=双方向)。others空はFalse(Redisに触れない)。RedisErrorはwarningログ1行+DBの`select_block_between`へフォールバック(BlockCacheが自分のengine接続で実行・送信txと直列しない)。**キャッシュを使うのはチャット送信判定のみ**(一覧APIはcursor改頁にcreated_atが必要なためDB直読み・Layer 1はSQL現状維持)。
- **キャッシュ更新(design §2.2)**: ブロック登録・解除の**DBコミット後に**両者のキーをDEL(`blk:u:{blocker}`と`blk:u:{blocked}`の2本)。DEL失敗(Redis断)はTTL 3600秒で収束・warningログ1行のみ(登録API自体はDBコミット済みなので成功扱い)。
- **D-23 cancelled化(design §2.3案A)**: ブロック登録APIの単一トランザクション内で同期実行。対象は**candidateを含む3状態**(`IN ('candidate','proposed','partial_accept')`・supervisor承認②)。対象検索SQL(§2.3)は両ユーザーのIntent(全status)を共に含むlatchesを `ORDER BY l.id FOR UPDATE`(競合クローズと同型・ロック順序固定)。1行ごとに ①`latches.store.cancel_latch`(条件付きUPDATE・競合負けFalseで読み飛ばし)②成功時のみ `latches.store.insert_latch_event(from_status, 'cancelled', user_id=blocker, now)`(引用#12)。参加Intentはactiveのまま手を触れない(引用#11)。**通知は送らない**(引用#13)。matchedはcancelled化対象外(引用#4)・読取専用化はws-4実装の送信時判定が担う。再評価はws-2のクローズ検知drainが持つ(本単位はイベント挿入まで)。
- **POST /v1/users/{user_id}/block の手順(design §2.4・単一tx・この順)**:
  1. `latches.store.fetch_user_id`(未登録JWTは404 NOT_FOUND)
  2. `user_id == 自分` → 422 VALIDATION_ERROR
  3. 相手ユーザーの実在検査 → 404 NOT_FOUND
  4. 自分→相手のblocks行の存在検査。**あれば冪等201として手順5〜7をスキップ**(応答は同一)
  5. INSERT(blocks行・idはDB DEFAULT→RETURNING)
  6. D-23 cancelled化(上の固定値)
  7. コミット後、キャッシュDEL(両者)
  応答は `201 {"blocked_id": "<uuid>"}`(既存ブロック時も201)。
- **DELETE /v1/users/{user_id}/block の手順(design §2.4)**: ①fetch_user_id→404 ②`DELETE ... RETURNING id`(行なしは**404 NOT_FOUND**・冪等204にしない) ③コミット後キャッシュDEL(両者)。応答204。**遡及効果なし**(cancelledにした提案は戻さない・解除後の未来はblocks現物参照: Layer 1のSQLが再開し、送信判定もキャッシュ再構築後に従う — supervisor承認③)。
- **GET /v1/users/me/blocks(design §2.4)**: cursor改頁。キーセット2キー `(created_at DESC, id DESC)`・SQLは `b.created_at < :ct OR (b.created_at = :ct AND b.id < :bid)`・usersをJOINして `display_name` を含める(05に規定なし・supervisor承認⑤)。cursorは base64url `"ISO|uuid"`(latchesの `encode_message_cursor` と同型・パディング除去)。limit 1〜100・既定20・超過422・形式不正cursorは422。応答 `{"items": [{"blocked_id","display_name","created_at"}], "next_cursor"}`。
- **POST /v1/reports(design §2.5)**: request `{"reportee_id": "<uuid>", "latch_id": "<uuid|null>", "reason": "<code>"}` → `201 {"report_id": "<uuid>"}`。reasonは選択式4値の英語コード: `inappropriate_content` / `unpleasant_behavior` / `suspected_impersonation` / `other`(値域外は422)。latch_idは省略可(既定null・引用#6)。検査順: ①fetch_user_id→404 ②`reportee_id == reporter` → 422 ③reportee実在→404 ④latch_id指定時はlatch実在(404)・自分が参加者・reporteeが参加者(不成立は422)。statusは**'pending'固定**で投入(値域pending→reviewed→resolvedはdesign §2.5確定・遷移操作はM4+)。**重複制限なし**・自動検知/停止なし。単一INSERTのみで他テーブルに触れない。
- **セッション失効リストは使わない(design §2.6・supervisor承認④)**: 1対1ブロック登録で相手のJWTを失効させない(「ブロックされた事実を直接通知しない」との整合。失効リストはlogout・退会ws-6・運用者停止が担う)。
- **エラー(design §2.1)**: `SafetyError`基底+`SafetyNotFoundError`(404 NOT_FOUND)・`SafetyValidationError`(422 VALIDATION_ERROR)・`SafetyDependencyUnavailableError`(503 DEPENDENCY_UNAVAILABLE)のみ(引用#15・専用codeなし)。例外メッセージは固定文言のみ(08 §2.4)。予期しない例外はクラス名のみログへ残して503(intents/latchesと同じラップ方針)。
- **send_message差し替え(design §2.2)**: `LatchesService.__init__` へ `block_cache=None` を追加し、`send_message` は `self._block_cache is not None` なら `is_blocked_between`、Noneなら従来どおり `store.select_block_between`(既存の`test_chat_service.py`はモック注入で無傷)。`make_latches_service(*, clock, engine, block_cache=None)`。
- **永続化はtext()生SQLのみ**・UUID復元は `_coerce_uuid` 規律・uuid[]のbindは `list[uuid.UUID]`+SQL側 `CAST(:x AS uuid[])`。
- **時刻はClock経由のみ**(arch test強制)。`block_user`・`report_user` の now はtx内で1回採取しINSERTとイベント挿入で同一値を使う。
- **ログ**: okログは `safety.block ok` 等のイベント名+結果のみ(usersパターン・低頻度のため可)。display_name・reason・uuid本文は出さない(08 §2.4)。Redis断・DEL失敗のwarningは `safety.*` プレフィックス+IDなし。
- **notifications・worker・alembicに触れない**: cancelled化の通知なし・Layer 1/latch_engine/sweeper無変更・マイグレーション追加なし(head=0006不変)。
- **`make lint`(ruff E,F,I,UP,B・format行長88)と `make test` を毎コミット通す**。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **ブロック登録の二重送信で二重行**(UNIQUE制約なし・引用#16違反) — 存在検査なしでは一覧が同じ相手を重複表示する。reasonable な期待は「二度POSTしても同一状態で201」 → Task 1のSQLピン(`_SELECT_BLOCK_EXISTS`)+Task 3 unit(idempotent)+Task 7試験1(blocks行COUNT=2ユーザー分でちょうど2)
2. **cancelled化の対象過不足**(candidate抜きでブロック済み相手への提示が残る/matched入りで成立済みを潰す・引用#3/#4違反) — 対象SQLのstatus列挙を部分集合で書くとどちらかが壊れる → Task 1のSQLピン(3値・matched不在)+Task 7試験6(4状態でcancelled 3本+matched維持)
3. **コミット前DELによる旧値再キャッシュ**(design §2.2違反) — DELをtx内に置くと並行read-throughがコミット前DBで「ブロックなし」を再キャッシュし、409になるべき送信が通る → Task 3 unit(invalidateはtx後・呼び出し順)+Task 7試験4(温め→登録→DEL確認→送信409)
4. **Redis断でチャットが止まる(fail-closed)** — RedisErrorを上へ伝播させると503で送信不能。安全性の判定はDBを見るほうが正しい → Task 2 unit(RedisError→`select_block_between`フォールバック)
5. **解除済みブロックの二重解除を204で握りつぶす** — 冪等204にすると誤UI操作が無反応になる(design §2.4は404を選択) → Task 3 unit(行なし404)+Task 7試験1(再解除404)

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3。design §4.1のうち3ファイルはbasename衝突のため `test_safety_` 接頭辞へ変更 — §9-1):

```text
backend/src/latch/safety/__init__.py                    (Task 5。router・serviceのexport)
backend/src/latch/safety/errors.py                      (Task 1。SafetyError系3クラス)
backend/src/latch/safety/store.py                       (Task 1。SQL定数9本+関数9本+BlockRow)
backend/src/latch/safety/cache.py                       (Task 2。BlockCache)
backend/src/latch/safety/service.py                     (Task 3〜4。BlockReportService+cursor codec)
backend/src/latch/safety/routes.py                      (Task 5。safety_router・4エンドポイント+スキーマ5クラス)
backend/tests/unit/safety/test_safety_cache.py          (Task 2。7件)
backend/tests/unit/safety/test_safety_store_sql.py      (Task 1。8件)
backend/tests/unit/safety/test_safety_service.py        (Task 3〜4。16件)
backend/tests/unit/safety/test_safety_routes.py         (Task 5。7件)
backend/tests/integration/test_safety_api.py            (Task 7。design §4.2の9試験)
docs/plans/M3/ws-5-report.md                            (Task 8。報告ファイル)
```

変更(design §3):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/main.py` | ①`from latch.safety import ...` import追加+`safety_logger` ②`create_app(safety_service=None)` 引数と `app.state` 設定 ③lifespanへ `build_safety` フラグ・redis_client生成条件へOR追加・`BlockCache` 構築と `make_safety_service`・`make_latches_service` へ `block_cache` 渡し ④`app.include_router(safety_router)` ⑤`SafetyError` 例外ハンドラ | 5・6 |
| `backend/src/latch/latches/service.py` | ①`LatchesService.__init__` へ `block_cache=None` と `self._block_cache` ②`send_message` のblocks判定をキャッシュ優先へ差し替え(数行・design §2.2の差し替え点) ③`make_latches_service(*, clock, engine, block_cache=None)` | 6 |
| `backend/tests/unit/latches/test_chat_service.py` | 末尾へ1件追記(`block_cache`注入時の送信409/201・未注入時の従来経路 — design §4.1) | 6 |
| `backend/tests/unit/test_rate_limit_wiring.py` | 期待値Counterへ3エントリ追記(`/v1/reports`: 1・`/v1/users/me/blocks`: 1・`/v1/users/{user_id}/block`: 2。機械的・§0) | 5 |
| `backend/tests/integration/test_chat_attendance_api.py` | **スコープ外への最小変更**(後述) — `_block` ヘルパーへ `redis_client` 引数と `blk:u:` 手動DEL追加・試験6へfixture引数追加 | 6 |

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`。

**スコープ外だが既存コードに載せる最小変更(ws-4のLatchRow.completed_at precedent)**: `test_chat_attendance_api.py` の `_block` ヘルパーはblocks行をDB直接INSERTするが、ws-5適用後のapi常設は `send_message` をキャッシュ経由で判定する。試験6は「ブロック前」送信でキャッシュを温めた後に `_block` でINSERTするため、キャッシュが無効化されず**409になるべき送信が201で通ってしまい失敗する**。よって `_block` へ「INSERT後に対象2ユーザーの `blk:u:` キーを手動DEL」を追加する(外部からの行追加でキャッシュ更新経由を通らない事象の再現・design §2.2の整合)。unit側は影響なし(DB接続なし)。

**スコープ外と判断する基準(必要になったと感じても作らない — design §1.4)**:

- `「このチャットは利用できません」表示・ブロック管理画面・通報フォーム → ws-8(03 §8)。チャット画面の読取専切替はws-7が409を受けて行う
- 通報の運用レビューUI・status遷移操作(警告・アカウント停止) → M4+の運用面(本単位は受付・記録のみ)
- 退会時の削除・calibration匿名化・Redis失効リストの退会利用 → ws-6
- `GET /v1/latches/{id}` 応答への読取専用手がかり(`chat_readonly`等) → 05に規定なし・送信409でws-7が切替可能なため作らない(design §1.4)
- Layer 1(worker側)のRedisキャッシュ参照化 → 本単位はAPI側のみ(supervisor承認①・08 §5.1との差分はG3時確認事項)
- blocks/reportsテーブルへのIndex・CHECK・UNIQUE追加 → マイグレーションなし(引用#16の0001判断を踏襲)

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- **docker/compose系の全コマンド**: `make up` / `make down` / `make test-ci` / `make migrate` / `docker compose build api` / `docker build` / `docker pull` — §0のとおり共有ci-dbを消費してはならない。検証はスーパーバイザーが実施する
- **実API呼び出し**: `make g1-gate`・`make g2-gate`・`make embed-smoke`・`make jev-smoke`・`make geo-*` は起動しない(本単位は外部SDKなし・LLM Gatewayに触らない)
- **design §3の禁止(無変更ファイル)**:
  - `backend/src/latch/latches/store.py`(**完全無変更** — `select_block_between` は残置しキャッシュのフォールバックと既存試験が使う)
  - `backend/src/latch/worker/` 一式(Layer 1・latch_engine・sweeper・reset等)
  - `backend/src/latch/` 配下の auth / users / intents / notifications / geo / core / events / llm / ratelimit / g1gate / g2gate 各モジュール
  - `backend/alembic/`(0001〜0006とも無変更・追加もしない)
  - `backend/tests/integration/conftest.py`・`backend/tests/unit/` 配下のconftest系(新規ファイルから利用するのみ)
  - `backend/tests/` の§4に列挙した以外の既存試験ファイル。特に `test_latches_api.py`・`test_latches_service.py`・`test_latches_store_sql.py`・`test_latches_routes.py`・`test_settings.py`・`test_llm_factory.py`・`test_app_health.py`・`test_arch_no_direct_time.py` は**一切触らない**(§9-2の追随访問。`test_chat_attendance_api.py` のみ§4に記載の最小変更を許す)
  - `compose.yaml`・`docker/`・`frontend/`・`prototype/`・`backend/pyproject.toml`・`backend/uv.lock`・`Makefile`・`.env`・`.env.example`・`settings.py`
  - `docs/`(01〜12・learn・testassets)・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/` の既存ファイル(M0〜M3のdesign・plan・report)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 8で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(新規unit 39件込み)
   検証: `make lint && make test` — ともにexit 0。**期待件数: 1187 passed**(main 1148+本単位unit 39=cache 7+store_sql 8+service 16+routes 7+chat_service追記 1。test_rate_limit_wiringは追記のみで件数不変。deselectedのintegrationを除く)。全件数を報告書に記録
2. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
3. **マイグレーション無変更(alembic head=0006不変)**
   検証: `cd backend && uv run alembic heads` が `0006 (head)` 単一・`git diff --name-only main -- backend/alembic` が**空**(1行も出ない)
4. **変更ファイルが§4の一覧どおり(作成12+変更5=17ファイル)**
   検証: Task 8の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
5. **実時間参照が `core/clock.py` のみ**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
6. **integration試験ファイルが収集可能(実行はスーパーバイザー検証)**
   検証: `cd backend && uv run pytest tests/integration/test_safety_api.py --collect-only -q` がexit 0で**9件**を収集(migrated_db fixtureは実行されない)
7. **test-ci=スーパーバイザー検証待ち**(§0規律・本単位は実行しない)。スーパーバイザー検証時の期待: `docker compose build api` → `make test-ci` がグリーン・**期待件数: 1426 passed**(main 1378+unit 39+integration 9)・alembic head=0006・m3ws5-残存ゼロ・Redis `blk:u:` 残存ゼロ(§7のSQL)

## 7. 報告形式

**結果ファイル**: `docs/plans/M3/ws-5-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M3 ws-5(ブロック・通報)実行報告

- ブランチ: m3-ws-5 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も・期待1187)> |
| 2 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 3 | alembic head=0006不変 | PASS/FAIL | <alembic heads 出力 + git diff --name-only main -- backend/alembic(空)> |
| 4 | 変更ファイル=§4の17ファイル | PASS/FAIL | <git diff --name-only main出力> |
| 5 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 6 | integration収集9件 | PASS/FAIL | <pytest --collect-only -q 末尾> |
| 7 | test-ci | **スーパーバイザー検証待ち** | <実施せず(§0規律)。期待1426 passed・head=0006・残存ゼロ> |

## design §5 実装時確認事項の結果
- (なし — design §5の5件は2026-10-01承認済み。本計画§2の固定値から
  変更した場合は下に「変更前→変更後+理由」を記録する)

## 固定値の変更有無(design.md §2・本計画§2)
- design §4.1のunit試験ファイル名 → test_safety_接頭辞へ変更(basename衝突・§9-1): 変更あり(計画時点で確定済み)
- キャッシュ blk:u: JSON配列・TTL 3600・コミット後DEL(§2): 変更なし / 変更あり
- D-23対象3状態(candidate含む)・matched除外・通知なし(§2): 変更なし / 変更あり
- POST手順1〜7・冪等201・DELETE 404/204・reports 4値reason・pending固定(§2): 変更なし / 変更あり
- その他: 変更なし / 変更あり(<前→後+理由>)

## (スーパーバイザー・G3・ws-6/ws-7/ws-8への引継ぎ)
- G3時確認候補(design §5): ①Layer 1のblocks参照はSQL直読きのまま(08 §5.1との差分)
  ②candidate含み(05 §6遷移表にcandidate→cancelled行なし) ③解除後は現物参照で
  送信可 ④1対1ブロックで失効リスト不使用(08 §5.3との読み方)
- フロント共有値(design §5-5): blocks一覧はdisplay_nameつき・reports.reasonは
  4値の英語コード・status値域はpending→reviewed→resolved
- ws-6: 退会時のblocks/reports行の扱い(削除範囲)は本単位では扱っていない
- test_chat_attendance_api.py の _block は blk:u: 手動DELつき(§4の最小変更)

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

**検証手順(スーパーバイザー検証用・agent3は実施しない。完了条件7に対応)**:

1. mainに対しws-5をマージ(またはws-5ワークツリーでmainを取り込み)
2. `make lint && make test` — unit全件グリーン(期待1187+並走単位分)
3. `docker compose build api` — **test-ciの前に必ず先行**(STATUS運用ルール4・ws-5はapi常設にsafety_routerとBlockCacheを載せるため必須)
4. `make test-ci` — 期待 **1426 passed + 並走単位分**(main 1378+unit 39+integration 9)
5. basename一意確認(運用ルール5・並走単位との両側保持確認): `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
6. `cd backend && uv run alembic heads` — 0006(head)単一のまま
7. test-ci後の残存確認(subject prefix `m3ws5-` で掃除・teardownの実効性):

```sql
SELECT COUNT(*) FROM users WHERE auth_subject LIKE 'm3ws5-%';
SELECT COUNT(*) FROM intents WHERE user_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws5-%');
SELECT COUNT(*) FROM latches WHERE intent_ids && ARRAY(
  SELECT id FROM intents WHERE user_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws5-%'));
SELECT COUNT(*) FROM latch_status_events WHERE latch_id IN
  (SELECT id FROM latches WHERE intent_ids && ARRAY(
    SELECT id FROM intents WHERE user_id IN
    (SELECT id FROM users WHERE auth_subject LIKE 'm3ws5-%')));
SELECT COUNT(*) FROM messages WHERE latch_id IN
  (SELECT id FROM latches WHERE intent_ids && ARRAY(
    SELECT id FROM intents WHERE user_id IN
    (SELECT id FROM users WHERE auth_subject LIKE 'm3ws5-%')));
SELECT COUNT(*) FROM notifications WHERE user_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws5-%');
SELECT COUNT(*) FROM blocks WHERE blocker_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws5-%')
  OR blocked_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws5-%');
SELECT COUNT(*) FROM reports WHERE reporter_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws5-%')
  OR reportee_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws5-%');
```

すべて0件であること(blocks・reportsを含む・design §4.2)。

8. Redis残存確認(teardownの `blk:u:` 掃除の実効性):

```bash
docker compose exec redis redis-cli --scan --pattern 'blk:u:*'
```

出力が空であること(試験が孤立キーを残していないことの確認。旧TTL分は3600秒で自動消滅するが、teardownが正しく働いていれば掃除直後は空)。

---

## 8. 実装ステップ(TDD。Task 1〜8の順で実行する)

### Task 1: safety/errors.py + safety/store.py(SQL定数9本・関数9本)

**Files:**
- Create: `backend/src/latch/safety/errors.py`
- Create: `backend/src/latch/safety/store.py`
- Test: `backend/tests/unit/safety/test_safety_store_sql.py`(新規・8件)

**Interfaces:**
- Produces: `SafetyError`(基底)・`SafetyNotFoundError`(404 NOT_FOUND)・`SafetyValidationError`(422 VALIDATION_ERROR)・`SafetyDependencyUnavailableError`(503 DEPENDENCY_UNAVAILABLE)(Task 3のservice・Task 5のmain.pyハンドラが消費)/ `BlockRow(id, blocked_id, display_name, created_at)`(frozen dataclass)/ store関数9本: `user_exists(conn, user_id) -> bool`・`block_exists(conn, *, blocker, target) -> bool`・`insert_block(conn, *, blocker, blocked, now) -> uuid.UUID`・`delete_block(conn, *, blocker, blocked) -> bool`・`select_blocks_page(conn, *, me, before: tuple[datetime, uuid.UUID] | None, limit) -> list[BlockRow]`・`select_blocked_ids_map(conn, user_ids) -> dict[uuid.UUID, list[uuid.UUID]]`・`select_user_intent_ids(conn, user_id) -> list[uuid.UUID]`・`select_open_latches_between(conn, my_intents, their_intents) -> list[tuple[uuid.UUID, str]]`・`insert_report(conn, *, reporter, reportee, latch_id, reason, status, now) -> uuid.UUID`(Task 2〜4が消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/safety/` ディレクトリを作成し(`__init__.py` は置かない)、`test_safety_store_sql.py` を新規作成:

```python
"""safety永続化のSQL検査(M3 ws-5 design §4.1)。

intents/latchesのtest_store_sql.pyと同型: 実dialect compile検査(bind paramの
部分認識ゼロ)+新規SQLの句ピン(design §2.3〜§2.5)。実DBでの挙動は
integration(test_safety_api.py)の担い。
"""

from sqlalchemy.dialects import postgresql

from latch.safety import store

_NEW_SQL = (
    "_SELECT_USER_EXISTS",
    "_SELECT_BLOCK_EXISTS",
    "_INSERT_BLOCK",
    "_DELETE_BLOCK",
    "_SELECT_BLOCKS_PAGE",
    "_SELECT_BLOCKED_IDS_MAP",
    "_SELECT_USER_INTENT_IDS",
    "_SELECT_OPEN_LATCHES_BETWEEN",
    "_INSERT_REPORT",
)


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_sql_bind_params_fully_recognized():
    """compiled文字列に未変換の ':name' が残らない(部分認識ゼロ)。"""
    tokens = (
        ":u",
        ":me",
        ":target",
        ":blocker",
        ":blocked",
        ":now",
        ":ct",
        ":bid",
        ":limit",
        ":ids",
        ":my_intents",
        ":their_intents",
        ":reporter",
        ":reportee",
        ":latch_id",
        ":reason",
        ":status",
    )
    for name in _NEW_SQL:
        compiled = _compiled(getattr(store, name))
        for token in tokens:
            assert token not in compiled, (name, token)


def test_block_insert_exists_delete_pinned():
    """blocks書込3種: 存在検査・INSERT・DELETE RETURNING(design §2.4)。

    Review Focus 1: UNIQUEなし(引用#16)のため存在検査が二重行防止の本体。
    """
    raw_ex = str(store._SELECT_BLOCK_EXISTS)
    assert "SELECT 1 FROM blocks" in raw_ex
    assert "blocker_id = CAST(:me AS uuid)" in raw_ex
    assert "blocked_id = CAST(:target AS uuid)" in raw_ex
    raw_in = str(store._INSERT_BLOCK)
    assert "INSERT INTO blocks (blocker_id, blocked_id, created_at)" in raw_in
    assert "RETURNING id" in raw_in
    raw_de = str(store._DELETE_BLOCK)
    assert "DELETE FROM blocks" in raw_de
    assert "blocker_id = CAST(:blocker AS uuid)" in raw_de
    assert "blocked_id = CAST(:blocked AS uuid)" in raw_de
    assert "RETURNING id" in raw_de


def test_blocks_page_keyset_pinned():
    """一覧はusers JOIN・created_at DESC,id DESCの2キーセット(design §2.4)。"""
    raw = str(store._SELECT_BLOCKS_PAGE)
    assert "JOIN users u ON u.id = b.blocked_id" in raw
    assert "blocker_id = CAST(:me AS uuid)" in raw
    assert "CAST(:ct AS timestamptz) IS NULL" in raw
    assert "b.created_at < CAST(:ct AS timestamptz)" in raw
    assert "(b.created_at = CAST(:ct AS timestamptz)" in raw
    assert "b.id < CAST(:bid AS uuid)" in raw
    assert "ORDER BY b.created_at DESC, b.id DESC" in raw
    assert "LIMIT :limit" in raw


def test_blocked_ids_map_pinned():
    """キャッシュミス用の一括読み(blocker_id→blocked_id・design §2.2)。"""
    raw = str(store._SELECT_BLOCKED_IDS_MAP)
    assert "SELECT blocker_id, blocked_id FROM blocks" in raw
    assert "blocker_id = ANY(CAST(:ids AS uuid[]))" in raw


def test_user_intent_ids_pinned():
    """D-23対象検索の入力: ユーザーの全Intent(全status・design §2.3)。"""
    raw = str(store._SELECT_USER_INTENT_IDS)
    assert "SELECT id FROM intents" in raw
    assert "user_id = CAST(:u AS uuid)" in raw


def test_open_latches_between_pinned():
    """cancelled化対象: status 3値・intent_ids &&×2・ORDER BY id FOR UPDATE。

    Review Focus 2: candidate抜きだとブロック済み相手への提示が残り、
    matched入りだと成立済みを潰す。design §2.3のSQLどおり(承認事項②)。
    """
    raw = str(store._SELECT_OPEN_LATCHES_BETWEEN)
    assert "IN ('candidate', 'proposed', 'partial_accept')" in raw
    assert "l.intent_ids && CAST(:my_intents AS uuid[])" in raw
    assert "l.intent_ids && CAST(:their_intents AS uuid[])" in raw
    assert "ORDER BY l.id" in raw
    assert "FOR UPDATE" in raw


def test_insert_report_pinned():
    """reports INSERTは6列(statusはアプリ層値・CHECKなし・design §2.5)。"""
    raw = str(store._INSERT_REPORT)
    assert "INSERT INTO reports" in raw
    for col in (
        "reporter_id",
        "reportee_id",
        "latch_id",
        "reason",
        "status",
        "created_at",
    ):
        assert col in raw
    assert "RETURNING id" in raw


def test_user_exists_pinned():
    """相手実在検査(users 1行・design §2.4手順3)。"""
    raw = str(store._SELECT_USER_EXISTS)
    assert "SELECT 1 FROM users" in raw
    assert "id = CAST(:u AS uuid)" in raw
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_store_sql.py -v`
Expected: **8件とも ERROR/FAIL**(import error: `latch.safety` を import できない)

- [ ] **Step 3: 最小実装**

`backend/src/latch/safety/errors.py` を作成:

```python
"""safetyドメイン例外階層(M3 ws-5 design §2.1・§3)。

ブロック・通報は専用error codeを持たない(引用#15 — 05 §5の共通codeで
構成する)。各例外はhttp_statusとcodeを固定で持ち、main.pyのハンドラが
共通envelopeへ変換する。例外メッセージは固定文言のみ(08 §2.4)。
"""

from __future__ import annotations


class SafetyError(Exception):
    """safetyドメインエラーの基底。http_status/codeを持つ(ハンドラが消費)。"""

    http_status: int
    code: str


class SafetyNotFoundError(SafetyError):
    """対象不在(未登録JWT・相手ユーザー不在・blocks行なし・latch不在)。"""

    http_status = 404
    code = "NOT_FOUND"


class SafetyValidationError(SafetyError):
    """リクエスト検証422(自分自身への操作・cursor形式不正・latch非参加)。"""

    http_status = 422
    code = "VALIDATION_ERROR"


class SafetyDependencyUnavailableError(SafetyError):
    """DB・Redis等の依存障害(05 §5「全API」)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"
```

`backend/src/latch/safety/store.py` を作成:

```python
"""safety永続化(text()生SQL・M3 ws-5 design §2.2〜§2.5)。

SQL定数+connを受け取るasync関数群(latches/store.py流儀。トランザクション
はserviceが統轄)。asyncpgのUUID復元は_coerce_uuid規律・uuid[]のbindは
list[uuid.UUID]+SQL側CAST。blocksのUNIQUEなし(引用#16)は存在検査で
アプリ層担保、reportsのstatus値域(pending→reviewed→resolved)もアプリ層
(design §2.5・DB CHECKなし)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT/RETURNING結果のUUID列復元(asyncpgサブクラス対策・latchesと同型)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


@dataclass(frozen=True)
class BlockRow:
    """blocks一覧の1行(users JOIN・design §2.4)。"""

    id: uuid.UUID
    blocked_id: uuid.UUID
    display_name: str
    created_at: datetime


# -- 相手実在・blocks書込(design §2.4手順3〜5) --

_SELECT_USER_EXISTS = text("""
    SELECT 1 FROM users WHERE id = CAST(:u AS uuid)
""")

_SELECT_BLOCK_EXISTS = text("""
    SELECT 1 FROM blocks
    WHERE blocker_id = CAST(:me AS uuid) AND blocked_id = CAST(:target AS uuid)
""")

_INSERT_BLOCK = text("""
    INSERT INTO blocks (blocker_id, blocked_id, created_at)
    VALUES (CAST(:blocker AS uuid), CAST(:blocked AS uuid),
            CAST(:now AS timestamptz))
    RETURNING id
""")

_DELETE_BLOCK = text("""
    DELETE FROM blocks
    WHERE blocker_id = CAST(:blocker AS uuid) AND blocked_id = CAST(:blocked AS uuid)
    RETURNING id
""")

# -- 一覧改頁(created_at DESC, id DESCの2キーセット・design §2.4) --

_SELECT_BLOCKS_PAGE = text("""
    SELECT b.id, b.blocked_id, u.display_name, b.created_at
    FROM blocks b JOIN users u ON u.id = b.blocked_id
    WHERE b.blocker_id = CAST(:me AS uuid)
      AND (CAST(:ct AS timestamptz) IS NULL
           OR b.created_at < CAST(:ct AS timestamptz)
           OR (b.created_at = CAST(:ct AS timestamptz)
               AND b.id < CAST(:bid AS uuid)))
    ORDER BY b.created_at DESC, b.id DESC
    LIMIT :limit
""")

# -- キャッシュミス用の一括読み(design §2.2) --

_SELECT_BLOCKED_IDS_MAP = text("""
    SELECT blocker_id, blocked_id FROM blocks
    WHERE blocker_id = ANY(CAST(:ids AS uuid[]))
""")

# -- D-23 cancelled化対象(design §2.3。両ユーザーのIntentを共に含む進行中
#    latches。ORDER BY id でロック順序を固定 — 競合クローズ
#    (_SELECT_CONFLICTING_LATCHES)と同型) --

_SELECT_USER_INTENT_IDS = text("""
    SELECT id FROM intents WHERE user_id = CAST(:u AS uuid)
""")

_SELECT_OPEN_LATCHES_BETWEEN = text("""
    SELECT l.id, l.status FROM latches l
    WHERE l.status IN ('candidate', 'proposed', 'partial_accept')
      AND l.intent_ids && CAST(:my_intents AS uuid[])
      AND l.intent_ids && CAST(:their_intents AS uuid[])
    ORDER BY l.id
    FOR UPDATE
""")

# -- reports(design §2.5・単一INSERTのみ) --

_INSERT_REPORT = text("""
    INSERT INTO reports (reporter_id, reportee_id, latch_id, reason, status,
                         created_at)
    VALUES (CAST(:reporter AS uuid), CAST(:reportee AS uuid),
            CAST(:latch_id AS uuid), :reason, :status, CAST(:now AS timestamptz))
    RETURNING id
""")


async def user_exists(conn: AsyncConnection, user_id: uuid.UUID) -> bool:
    """相手ユーザーの実在検査(design §2.4手順3)。"""
    res = await conn.execute(_SELECT_USER_EXISTS, {"u": user_id})
    return res.first() is not None


async def block_exists(
    conn: AsyncConnection, *, blocker: uuid.UUID, target: uuid.UUID
) -> bool:
    """自分→相手のblocks行の存在検査(冪等201の分岐・引用#16)。"""
    res = await conn.execute(
        _SELECT_BLOCK_EXISTS, {"me": blocker, "target": target}
    )
    return res.first() is not None


async def insert_block(
    conn: AsyncConnection,
    *,
    blocker: uuid.UUID,
    blocked: uuid.UUID,
    now: datetime,
) -> uuid.UUID:
    """blocks行挿入(design §2.4手順5)。idはDB DEFAULT→RETURNING。"""
    res = await conn.execute(
        _INSERT_BLOCK, {"blocker": blocker, "blocked": blocked, "now": now}
    )
    row = res.first()
    assert row is not None
    return _coerce_uuid(row[0])


async def delete_block(
    conn: AsyncConnection, *, blocker: uuid.UUID, blocked: uuid.UUID
) -> bool:
    """blocks行削除(design §2.4)。False=行なし(呼び出し側は404)。"""
    res = await conn.execute(
        _DELETE_BLOCK, {"blocker": blocker, "blocked": blocked}
    )
    return res.first() is not None


async def select_blocks_page(
    conn: AsyncConnection,
    *,
    me: uuid.UUID,
    before: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[BlockRow]:
    """blocks一覧(design §2.4)。before=cursor位置(created_at, id)。"""
    res = await conn.execute(
        _SELECT_BLOCKS_PAGE,
        {
            "me": me,
            "ct": before[0] if before else None,
            "bid": before[1] if before else None,
            "limit": limit,
        },
    )
    return [
        BlockRow(
            id=_coerce_uuid(r[0]),
            blocked_id=_coerce_uuid(r[1]),
            display_name=r[2],
            created_at=r[3],
        )
        for r in res.fetchall()
    ]


async def select_blocked_ids_map(
    conn: AsyncConnection, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """blocker_id→blocked_id一覧(キャッシュミス時の一括DB読み・§2.2)。

    ブロックゼロのユーザーはキーなし(呼び出し側が空リスト扱い)。
    """
    res = await conn.execute(_SELECT_BLOCKED_IDS_MAP, {"ids": list(user_ids)})
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for blocker, blocked in res.fetchall():
        out.setdefault(_coerce_uuid(blocker), []).append(_coerce_uuid(blocked))
    return out


async def select_user_intent_ids(
    conn: AsyncConnection, user_id: uuid.UUID
) -> list[uuid.UUID]:
    """ユーザーの全Intent id(全status・design §2.3の対象検索の入力)。"""
    res = await conn.execute(_SELECT_USER_INTENT_IDS, {"u": user_id})
    return [_coerce_uuid(r[0]) for r in res.fetchall()]


async def select_open_latches_between(
    conn: AsyncConnection,
    my_intents: list[uuid.UUID],
    their_intents: list[uuid.UUID],
) -> list[tuple[uuid.UUID, str]]:
    """D-23 cancelled化対象の行ロック取得(ORDER BY id・design §2.3)。"""
    res = await conn.execute(
        _SELECT_OPEN_LATCHES_BETWEEN,
        {"my_intents": list(my_intents), "their_intents": list(their_intents)},
    )
    return [(_coerce_uuid(r[0]), r[1]) for r in res.fetchall()]


async def insert_report(
    conn: AsyncConnection,
    *,
    reporter: uuid.UUID,
    reportee: uuid.UUID,
    latch_id: uuid.UUID | None,
    reason: str,
    status: str,
    now: datetime,
) -> uuid.UUID:
    """reports挿入(design §2.5)。statusはserviceが'pending'固定で渡す。"""
    res = await conn.execute(
        _INSERT_REPORT,
        {
            "reporter": reporter,
            "reportee": reportee,
            "latch_id": latch_id,
            "reason": reason,
            "status": status,
            "now": now,
        },
    )
    row = res.first()
    assert row is not None
    return _coerce_uuid(row[0])
```

**注意**: この時点で `latch.safety` パッケージに `__init__.py` が無いとimportできない。Task 5で本格的な `__init__.py` を書くまでの**暫定**として、空でよいので `backend/src/latch/safety/__init__.py` を作成する(`touch` 相当の空ファイル。Task 5で内容を書く):

```bash
touch backend/src/latch/safety/__init__.py
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_store_sql.py -v`
Expected: PASS(8件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/safety/__init__.py backend/src/latch/safety/errors.py backend/src/latch/safety/store.py backend/tests/unit/safety/test_safety_store_sql.py
git commit -m "feat: add safety domain errors and blocks/reports store with SQL pins (M3 ws-5)"
```

### Task 2: safety/cache.py(BlockCache)

**Files:**
- Create: `backend/src/latch/safety/cache.py`
- Test: `backend/tests/unit/safety/test_safety_cache.py`(新規・7件)

**Interfaces:**
- Consumes: Task 1の `store.select_blocked_ids_map`、latchesの `store.select_block_between`(フォールバック)
- Produces: `BlockCache(*, redis_client: aioredis.Redis, engine: AsyncEngine)`・`await is_blocked_between(me: uuid.UUID, others: list[uuid.UUID]) -> bool`・`await invalidate(user_ids: list[uuid.UUID]) -> None`(Redis失敗を握る)。キー形式 `blk:u:{user_id}`・値はJSON配列文字列・TTL 3600(Task 3〜6・main.pyが消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/safety/test_safety_cache.py` を新規作成:

```python
"""BlockCacheのunit試験(M3 ws-5 design §4.1)。

Redisはスタブ(asyncメソッドを持つ偽オブジェクト・decode_responses=True
相当のstr値を保持)。DB読み込みはsafety.store.select_blocked_ids_mapの
monkeypatch差し替え、フォールバックはlatches.store.select_block_betweenの
差し替え(SQLに依存しない・test_chat_service.pyと同型)。
"""

import json
import uuid

from redis.exceptions import RedisError

from latch.latches import store as latches_store
from latch.safety import store as safety_store
from latch.safety.cache import BlockCache

ME = uuid.uuid4()
P1 = uuid.uuid4()
P2 = uuid.uuid4()


class FakeRedis:
    """decode_responses=True相当の最小スタブ(mget/set/delete)。"""

    def __init__(self, data: dict | None = None, fail: bool = False):
        self.data: dict[str, str] = dict(data or {})
        self.fail = fail
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def mget(self, keys):
        if self.fail:
            raise RedisError("down")
        return [self.data.get(k) for k in keys]

    async def set(self, key, value, ex=None):
        if self.fail:
            raise RedisError("down")
        self.set_calls.append((key, value, ex))
        self.data[key] = value

    async def delete(self, *keys):
        if self.fail:
            raise RedisError("down")
        for k in keys:
            self.data.pop(k, None)


class _NoopEngine:
    """store差し替え用の空エンジン(conn不使用・ws-4と同型)。"""

    def connect(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _ret(value):
    """monkeypatch差し替え用の「常にvalueを返すasync関数」ファクトリ。"""

    async def _inner(*args, **kwargs):
        return value

    return _inner


def _make(data: dict | None = None, fail: bool = False):
    redis = FakeRedis(data, fail=fail)
    cache = BlockCache(redis_client=redis, engine=_NoopEngine())
    return redis, cache


async def test_read_through_miss_loads_and_sets(monkeypatch):
    """ミス→DB一括読み→SET EX 3600のJSON配列(design §2.2)。"""
    redis, cache = _make()  # Redis空=全面ミス
    monkeypatch.setattr(
        safety_store, "select_blocked_ids_map", _ret({ME: [P1], P1: []})
    )
    assert await cache.is_blocked_between(ME, [P1]) is True
    assert redis.set_calls == [
        (f"blk:u:{ME}", json.dumps([str(P1)]), 3600),
        (f"blk:u:{P1}", json.dumps([]), 3600),
    ]


async def test_hit_skips_db(monkeypatch):
    """MGETヒットはDB問い合わせゼロ(§2.2 — 参照は毎回DBを見ない)。"""
    redis, cache = _make(
        {
            f"blk:u:{ME}": json.dumps([str(P1)]),
            f"blk:u:{P1}": json.dumps([]),
        }
    )
    called = []

    async def _db(*args, **kwargs):
        called.append(1)
        return {}

    monkeypatch.setattr(safety_store, "select_blocked_ids_map", _db)
    assert await cache.is_blocked_between(ME, [P1]) is True
    assert called == []


async def test_empty_blocks_cached_as_empty_json(monkeypatch):
    """ブロックゼロの大多数も"[]"でキャッシュ(案A・§2.2)。"""
    redis, cache = _make()
    monkeypatch.setattr(safety_store, "select_blocked_ids_map", _ret({}))
    assert await cache.is_blocked_between(ME, [P1]) is False
    assert redis.data[f"blk:u:{ME}"] == "[]"
    assert redis.data[f"blk:u:{P1}"] == "[]"


async def test_bidirectional_detection():
    """双方向判定: 片方向のみでTrue・逆視点もTrue・無関係False(引用#1)。"""
    # (ME→P1)のみの行: 自分視点True
    _, cache = _make(
        {f"blk:u:{ME}": json.dumps([str(P1)]), f"blk:u:{P1}": json.dumps([])}
    )
    assert await cache.is_blocked_between(ME, [P1]) is True
    # 同じ行を相手視点で: P1の一覧にMEは無いがMEの一覧にP1 → True(双方向)
    _, cache_r = _make(
        {f"blk:u:{ME}": json.dumps([str(P1)]), f"blk:u:{P1}": json.dumps([])}
    )
    assert await cache_r.is_blocked_between(P1, [ME]) is True
    # 相手(P2)が自分(ME)をブロック: 自分の一覧は空でもTrue
    _, cache_p2 = _make(
        {f"blk:u:{ME}": json.dumps([]), f"blk:u:{P2}": json.dumps([str(ME)])}
    )
    assert await cache_p2.is_blocked_between(ME, [P2]) is True
    # グループ複数相手: others同士のブロック(P1→P2)は自分の送信を止めない
    _, cache_g = _make(
        {
            f"blk:u:{ME}": json.dumps([]),
            f"blk:u:{P1}": json.dumps([str(P2)]),
            f"blk:u:{P2}": json.dumps([]),
        }
    )
    assert await cache_g.is_blocked_between(ME, [P1, P2]) is False
    # グループで自分が誰か(P1)をブロック済み → True
    _, cache_g2 = _make(
        {
            f"blk:u:{ME}": json.dumps([str(P1)]),
            f"blk:u:{P1}": json.dumps([]),
            f"blk:u:{P2}": json.dumps([]),
        }
    )
    assert await cache_g2.is_blocked_between(ME, [P1, P2]) is True


async def test_invalidate_deletes_keys():
    """invalidateは指定ユーザーのキーをDEL(§2.2)。無関係キーは残る。"""
    redis, cache = _make(
        {f"blk:u:{ME}": "[]", f"blk:u:{P1}": "[]", f"blk:u:{P2}": "[]"}
    )
    await cache.invalidate([ME, P1])  # 両者のキーをDEL
    assert f"blk:u:{ME}" not in redis.data
    assert f"blk:u:{P1}" not in redis.data
    assert f"blk:u:{P2}" in redis.data


async def test_redis_error_falls_back_to_db(monkeypatch):
    """Redis断は安全性優先でDBのselect_block_betweenへ(§2.2・Review Focus 4)。"""
    redis, cache = _make(fail=True)
    assert redis.fail is True
    monkeypatch.setattr(latches_store, "select_block_between", _ret(True))
    assert await cache.is_blocked_between(ME, [P1]) is True


async def test_no_others_returns_false():
    """others空はFalse・Redisにも触らない(1人LATCH等の防御)。"""
    redis, cache = _make()
    assert await cache.is_blocked_between(ME, []) is False
    assert redis.data == {}
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_cache.py -v`
Expected: **7件とも ERROR/FAIL**(import error: `latch.safety.cache` が存在しない)

- [ ] **Step 3: 最小実装**

`backend/src/latch/safety/cache.py` を作成:

```python
"""Redisブロックキャッシュ(M3 ws-5 design §2.2)。

鍵(接頭辞 blk: — auth:・rl: と名前空間を分ける):
  ユーザー単位JSON一覧:  blk:u:{user_id} = '["<blocked_uuid>", ...]'  SET EX 3600

read-through: MGETで一括取得し、欠落キーはDBから一括読みしてSET(空は"[]"。
ブロックゼロの大多数の参照も1回のDB読みで賄える)。TTL 3600は掃除用では
なくDEL失敗時の最終収束期間(08 §5.1の「反映はキャッシュ更新を経由」は
登録・解除のコミット後に両者のキーをDELする — コミット前DELは並行
read-throughがコミット前DBで旧値を再キャッシュする窓を残すため)。
Redis断(RedisError)は安全性優先でDBのselect_block_betweenへフォール
バックする(キャッシュは性能の最適化であって真実はDBにある・warning 1行)。
DB読み込みは自分のengine接続で行い、送信トランザクション(FOR UPDATE
保持中)と直列しない(blocks行をロックしない単純SELECTのため競合しない)。
"""

from __future__ import annotations

import json
import logging
import uuid

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.latches import store as latches_store
from latch.safety import store

logger = logging.getLogger("latch.safety")

_TTL_S = 3600


def _key(user_id: uuid.UUID) -> str:
    return f"blk:u:{user_id}"


class BlockCache:
    """ブロック一覧のread-throughキャッシュ(decode_responses=TrueのRedisを注入)。"""

    def __init__(
        self, *, redis_client: aioredis.Redis, engine: AsyncEngine
    ) -> None:
        self._redis = redis_client
        self._engine = engine

    async def is_blocked_between(
        self, me: uuid.UUID, others: list[uuid.UUID]
    ) -> bool:
        """自分とothersの間のblocks双方向判定(08 §5.1・引用#1)。

        自分の一覧にothersの誰かが含まれる、またはothersのいずれかの
        一覧に自分が含まれればTrue(双方向)。
        """
        if not others:
            return False
        try:
            user_ids = [me, *others]
            raw = await self._redis.mget([_key(u) for u in user_ids])
            lists: dict[str, set[str]] = {}
            missing: list[uuid.UUID] = []
            for user_id, value in zip(user_ids, raw):
                if value is None:
                    missing.append(user_id)
                else:
                    lists[str(user_id)] = set(json.loads(value))
            if missing:
                lists.update(await self._load(missing))
            me_key = str(me)
            if lists[me_key] & {str(o) for o in others}:
                return True
            return any(me_key in lists[str(o)] for o in others)
        except RedisError:
            logger.warning("safety.cache.redis_error fallback=db")
            async with self._engine.connect() as conn:
                return await latches_store.select_block_between(conn, me, others)

    async def _load(
        self, user_ids: list[uuid.UUID]
    ) -> dict[str, set[str]]:
        """欠落キーをDBから一括読みし、SET EX 3600して値を返す(read-through)。"""
        async with self._engine.connect() as conn:
            blocked_map = await store.select_blocked_ids_map(conn, user_ids)
        out: dict[str, set[str]] = {}
        for user_id in user_ids:
            ids = [str(x) for x in blocked_map.get(user_id, [])]
            await self._redis.set(_key(user_id), json.dumps(ids), ex=_TTL_S)
            out[str(user_id)] = set(ids)
        return out

    async def invalidate(self, user_ids: list[uuid.UUID]) -> None:
        """登録・解除コミット後のキー削除(§2.2)。

        失敗(Redis断)は例外を出さずTTL 3600秒で収束(warning 1行のみ。
        呼び出し元のAPIはDBコミット済みなので成功扱い)。
        """
        try:
            await self._redis.delete(*[_key(u) for u in user_ids])
        except RedisError:
            logger.warning("safety.cache.invalidate_failed ttl_converges")
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_cache.py -v`
Expected: PASS(7件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/safety/cache.py backend/tests/unit/safety/test_safety_cache.py
git commit -m "feat: add BlockCache read-through redis cache with db fallback (M3 ws-5)"
```

### Task 3: safety/service.py block系(登録・D-23・解除・一覧+cursor)

**Files:**
- Create: `backend/src/latch/safety/service.py`
- Test: `backend/tests/unit/safety/test_safety_service.py`(新規・13件)

**Interfaces:**
- Consumes: Task 1のstore関数(`user_exists`・`block_exists`・`insert_block`・`delete_block`・`select_blocks_page`・`select_user_intent_ids`・`select_open_latches_between`)・Task 2の `BlockCache`・latchesの `latches_store.fetch_user_id`/`cancel_latch`/`insert_latch_event`
- Produces: `BlockReportService(*, clock: Clock, engine: AsyncEngine, block_cache: BlockCache | None = None)`・`await block_user(*, auth_provider: str, auth_subject: str, target_user_id: uuid.UUID) -> uuid.UUID`(冪等・blocked_idを返す)・`await unblock_user(*, auth_provider, auth_subject, target_user_id) -> None`・`await list_blocks(*, auth_provider, auth_subject, cursor: str | None = None, limit: int = 20) -> tuple[list[BlockRow], str | None]`・module level の `encode_block_cursor(created_at, block_id) -> str`・`decode_block_cursor(cursor) -> tuple[datetime, uuid.UUID]`(形式不正は `SafetyValidationError`)(Task 4のreport・Task 5のroutesが消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/safety/test_safety_service.py` を新規作成:

```python
"""BlockReportService(block系)のunit試験(M3 ws-5 design §4.1)。

storeはスタブ(monkeypatch差し替え)でSQLに依存しない(test_chat_service.py
と同型)。時刻はFakeClock。latches側資産(cancel_latch・insert_latch_event)
も差し替え。SQL検査はtest_safety_store_sql.py・実HTTPは
test_safety_api.py(integration)の担い。
"""

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.latches import store as latches_store
from latch.safety import store as safety_store
from latch.safety.errors import SafetyValidationError
from latch.safety.service import (
    BlockReportService,
    decode_block_cursor,
    encode_block_cursor,
)
from latch.safety.store import BlockRow

NOW = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
ME = uuid.uuid4()
TARGET = uuid.uuid4()
LATCH1 = uuid.uuid4()
LATCH2 = uuid.uuid4()
LATCH3 = uuid.uuid4()


class _NoopEngine:
    """store全体をmonkeypatch差し替えするための空エンジン(conn不使用)。"""

    def begin(self):
        return self

    def connect(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _ret(value):
    """monkeypatch差し替え用の「常にvalueを返すasync関数」ファクトリ。"""

    async def _inner(*args, **kwargs):
        return value

    return _inner


class _StubCache:
    """BlockCacheのスタブ(invalidate呼び出し記録のみ・§2.2)。"""

    def __init__(self):
        self.invalidated: list[list[uuid.UUID]] = []

    async def invalidate(self, user_ids):
        self.invalidated.append(list(user_ids))


def _svc(cache=None) -> BlockReportService:
    return BlockReportService(
        clock=FakeClock(NOW), engine=_NoopEngine(), block_cache=cache
    )


def _row(**overrides) -> BlockRow:
    base = dict(
        id=uuid.uuid4(),
        blocked_id=uuid.uuid4(),
        display_name="相手",
        created_at=NOW - timedelta(minutes=5),
    )
    base.update(overrides)
    return BlockRow(**base)


def _b64(s: str) -> str:
    """テスト用: 不透明cursor候補の生成(base64url・パディング除去)。"""
    return base64.urlsafe_b64encode(s.encode()).rstrip(b"=").decode()


def test_block_cursor_roundtrip():
    """2キー(created_at,id)のencode/decode往復(design §2.4)。"""
    token = encode_block_cursor(NOW, LATCH1)
    assert decode_block_cursor(token) == (NOW, LATCH1)
    assert "=" not in token  # base64urlのパディング除去(不透明文字列)


def test_block_cursor_invalid_422():
    """形式不正cursorは422 VALIDATION_ERROR(latches/intentsと同型)。"""
    for bad in (
        _b64("no-pipe-here"),  # 区切りなし
        _b64("not-a-datetime|" + str(uuid.uuid4())),  # 日時復元失敗
        "!!!",  # base64urlとして不正
    ):
        with pytest.raises(SafetyValidationError):
            decode_block_cursor(bad)


async def test_block_self_422(monkeypatch):
    """自分自身のブロックは422(引用#15 — 専用codeなし)。"""
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    with pytest.raises(SafetyValidationError):
        await _svc().block_user(
            auth_provider="google", auth_subject="s", target_user_id=ME
        )


async def test_block_unregistered_jwt_404(monkeypatch):
    """未登録JWTは404(design §2.4手順1・チャットAPIと同じ扱い)。"""
    from latch.safety.errors import SafetyNotFoundError

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(None))
    with pytest.raises(SafetyNotFoundError):
        await _svc().block_user(
            auth_provider="google", auth_subject="s", target_user_id=TARGET
        )


async def test_block_target_missing_404(monkeypatch):
    """相手ユーザー不在は404(design §2.4手順3)。"""
    from latch.safety.errors import SafetyNotFoundError

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(False))
    with pytest.raises(SafetyNotFoundError):
        await _svc().block_user(
            auth_provider="google", auth_subject="s", target_user_id=TARGET
        )


async def test_block_inserts_and_cancels_d23(monkeypatch):
    """登録はINSERT+D-23 cancelled化+イベント(user_id=blocker・§2.3)。

    Review Focus 2: 対象3行(candidate/proposed/partial_accept)すべてへ
    cancel_latch→insert_latch_event。イベントのuser_idはblocker(引用#12)。
    """
    inserts: list[dict] = []
    events: list[tuple] = []

    async def _insert_block(conn, *, blocker, blocked, now):
        inserts.append({"blocker": blocker, "blocked": blocked, "now": now})
        return uuid.uuid4()

    async def _event(conn, latch_id, from_status, to_status, user_id, now):
        events.append((latch_id, from_status, to_status, user_id, now))

    cancelled: list[uuid.UUID] = []

    async def _cancel(conn, latch_id):
        cancelled.append(latch_id)
        return True

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))
    monkeypatch.setattr(safety_store, "block_exists", _ret(False))
    monkeypatch.setattr(safety_store, "insert_block", _insert_block)
    monkeypatch.setattr(
        safety_store, "select_user_intent_ids", _ret([uuid.uuid4()])
    )
    monkeypatch.setattr(
        safety_store,
        "select_open_latches_between",
        _ret(
            [
                (LATCH1, "candidate"),
                (LATCH2, "proposed"),
                (LATCH3, "partial_accept"),
            ]
        ),
    )
    monkeypatch.setattr(latches_store, "cancel_latch", _cancel)
    monkeypatch.setattr(latches_store, "insert_latch_event", _event)
    got = await _svc().block_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )
    assert got == TARGET  # 201応答のblocked_id
    assert inserts == [{"blocker": ME, "blocked": TARGET, "now": NOW}]
    assert cancelled == [LATCH1, LATCH2, LATCH3]  # ORDER BY id相当の対象順
    assert events == [
        (LATCH1, "candidate", "cancelled", ME, NOW),
        (LATCH2, "proposed", "cancelled", ME, NOW),
        (LATCH3, "partial_accept", "cancelled", ME, NOW),
    ]


async def test_block_cancel_race_skips_event(monkeypatch):
    """cancel_latch競合負け(False)の行はイベントを挿まない(§2.3)。"""
    events: list[tuple] = []

    async def _event(conn, latch_id, from_status, to_status, user_id, now):
        events.append(latch_id)

    async def _cancel(conn, latch_id):
        return latch_id == LATCH1  # LATCH2は負け

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))
    monkeypatch.setattr(safety_store, "block_exists", _ret(False))
    monkeypatch.setattr(safety_store, "insert_block", _ret(uuid.uuid4()))
    monkeypatch.setattr(safety_store, "select_user_intent_ids", _ret([]))
    monkeypatch.setattr(
        safety_store,
        "select_open_latches_between",
        _ret([(LATCH1, "proposed"), (LATCH2, "proposed")]),
    )
    monkeypatch.setattr(latches_store, "cancel_latch", _cancel)
    monkeypatch.setattr(latches_store, "insert_latch_event", _event)
    await _svc().block_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )
    assert events == [LATCH1]


async def test_block_idempotent_existing_201(monkeypatch):
    """既存ブロック時はINSERTもcancelled化もしない(冪等201・§2.4手順4)。"""
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))
    monkeypatch.setattr(safety_store, "block_exists", _ret(True))

    async def _fail(*args, **kwargs):
        raise AssertionError("呼ばれないはず")

    monkeypatch.setattr(safety_store, "insert_block", _fail)
    monkeypatch.setattr(safety_store, "select_user_intent_ids", _fail)
    monkeypatch.setattr(safety_store, "select_open_latches_between", _fail)
    monkeypatch.setattr(latches_store, "cancel_latch", _fail)
    got = await _svc().block_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )
    assert got == TARGET


async def test_block_invalidates_cache_after_commit(monkeypatch):
    """コミット後に両者のキーをDEL(§2.2・Review Focus 3)。

    invalidateはtx抜け後の呼び出し(insert_blockと同一conn上でない)。
    コード構造で担保し、ここでは「登録成功で両者へ1回だけ呼ばれる」をピン。
    """
    cache = _StubCache()
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))
    monkeypatch.setattr(safety_store, "block_exists", _ret(False))
    monkeypatch.setattr(safety_store, "insert_block", _ret(uuid.uuid4()))
    monkeypatch.setattr(safety_store, "select_user_intent_ids", _ret([]))
    monkeypatch.setattr(safety_store, "select_open_latches_between", _ret([]))
    await _svc(cache).block_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )
    assert cache.invalidated == [[ME, TARGET]]
    # 未注入時はスキップ(例外にならない)
    await _svc().block_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )


async def test_unblock_missing_404(monkeypatch):
    """blocks行なしの解除は404(冪等204にしない・design §2.4)。"""
    from latch.safety.errors import SafetyNotFoundError

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "delete_block", _ret(False))
    with pytest.raises(SafetyNotFoundError):
        await _svc().unblock_user(
            auth_provider="google", auth_subject="s", target_user_id=TARGET
        )


async def test_unblock_deletes_and_invalidates(monkeypatch):
    """解除成功は行削除+コミット後キャッシュDEL(§2.4)。遡及処理なし。"""
    cache = _StubCache()
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "delete_block", _ret(True))
    await _svc(cache).unblock_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )
    assert cache.invalidated == [[ME, TARGET]]


async def test_list_blocks_pagination(monkeypatch):
    """limit+1件取得→溢れたらnext_cursor生成(latches一覧と同型・§2.4)。"""
    r1 = _row()
    r2 = _row()
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "select_blocks_page", _ret([r1, r2]))
    items, next_cursor = await _svc().list_blocks(
        auth_provider="google", auth_subject="s", limit=1
    )
    assert items == [r1]  # limit=1で1件だけ返す
    assert next_cursor == encode_block_cursor(r1.created_at, r1.id)
    # cursor渡しはdecodeしてbeforeへ渡される
    monkeypatch.setattr(safety_store, "select_blocks_page", _ret([r2]))
    items2, next2 = await _svc().list_blocks(
        auth_provider="google",
        auth_subject="s",
        cursor=encode_block_cursor(r1.created_at, r1.id),
        limit=1,
    )
    assert items2 == [r2]
    assert next2 is None  # 次頁なし


async def test_list_blocks_invalid_cursor_422(monkeypatch):
    """形式不正cursorは422(design §2.4)。未登録JWTも404。"""
    from latch.safety.errors import SafetyNotFoundError

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    with pytest.raises(SafetyValidationError):
        await _svc().list_blocks(
            auth_provider="google", auth_subject="s", cursor="!!!"
        )
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(None))
    with pytest.raises(SafetyNotFoundError):
        await _svc().list_blocks(auth_provider="google", auth_subject="s")
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_service.py -v`
Expected: **13件とも ERROR/FAIL**(import error: `latch.safety.service` が存在しない)

- [ ] **Step 3: 最小実装**

`backend/src/latch/safety/service.py` を作成(block系+reportの枠。report_user本体はTask 4で追記するが、**このTaskではblock_user・unblock_user・list_blocks・cursor codec・_me・_invalidate・_wrap_unexpected・_cancel_open_latchesのみ**を書く。定数 `REPORT_REASONS`・`REPORT_STATUS_PENDING` も先に置いてよい):

```python
"""safetyユースケース(M3 ws-5 design §2.3〜§2.5)。

block_userは単一トランザクションで「登録→D-23 cancelled化」までを行い、
コミット後にキャッシュDEL(§2.2 — コミット前DELは並行read-throughが
コミット前DBで旧値を再キャッシュする窓を残す)。cancelled化の通知は
送らない(引用#13)。予期しない例外はintents/latchesと同じラップ方針
(例外クラス名のみログへ残して503)。
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.latches import store as latches_store
from latch.safety import store
from latch.safety.cache import BlockCache
from latch.safety.errors import (
    SafetyDependencyUnavailableError,
    SafetyError,
    SafetyNotFoundError,
    SafetyValidationError,
)

logger = logging.getLogger("latch.safety")

# reports.reasonの選択式4値(08 §5.2・表示文言はフロントが持つ・design §2.5)
REPORT_REASONS = (
    "inappropriate_content",
    "unpleasant_behavior",
    "suspected_impersonation",
    "other",
)
# status値域はpending(受付)→reviewed(レビュー済)→resolved(対処済)。
# 本単位はpending投入のみ・遷移操作は運用面(M4+・design §2.5)
REPORT_STATUS_PENDING = "pending"


def _wrap_unexpected(exc: Exception) -> SafetyDependencyUnavailableError:
    """予期しない例外を503へ包む。例外のクラス名のみログへ残す(08 §2.4)。"""
    logger.warning("safety.unexpected class=%s", type(exc).__name__)
    return SafetyDependencyUnavailableError("safety dependency unavailable")


def encode_block_cursor(created_at: datetime, block_id: uuid.UUID) -> str:
    """blocks一覧のキーセットcursor 2キー: base64url("ISO|uuid")。

    latchesのencode_message_cursorと同型。ソート順
    (created_at DESC, id DESC)をタプル比較で表現する。
    """
    raw = f"{created_at.isoformat()}|{block_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_block_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """cursor復元。形式不正は422 VALIDATION_ERROR(同型)。"""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ct, bid = raw.split("|")
        return datetime.fromisoformat(ct), uuid.UUID(bid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SafetyValidationError("invalid cursor") from exc


class BlockReportService:
    """ブロック登録・解除・一覧・通報のユースケース(design §2.3〜§2.5)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        engine: AsyncEngine,
        block_cache: BlockCache | None = None,
    ) -> None:
        self._clock = clock
        self._engine = engine
        self._cache = block_cache

    async def _me(self, conn, auth_provider: str, auth_subject: str) -> uuid.UUID:
        """claims→users.id。未登録JWTは404(引用#14・latchesと同型)。"""
        user_id = await latches_store.fetch_user_id(conn, auth_provider, auth_subject)
        if user_id is None:
            raise SafetyNotFoundError("user not found")
        return user_id

    async def _invalidate(self, a: uuid.UUID, b: uuid.UUID) -> None:
        """コミット後に両者のキーをDEL(§2.2)。未注入時はスキップ。"""
        if self._cache is not None:
            await self._cache.invalidate([a, b])

    async def block_user(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        target_user_id: uuid.UUID,
    ) -> uuid.UUID:
        """POST /v1/users/{id}/block(design §2.4手順1〜7・単一tx)。冪等201。"""
        try:
            async with self._engine.begin() as conn:
                me = await self._me(conn, auth_provider, auth_subject)
                if target_user_id == me:
                    raise SafetyValidationError("cannot block yourself")
                if not await store.user_exists(conn, target_user_id):
                    raise SafetyNotFoundError("user not found")
                if await store.block_exists(conn, blocker=me, target=target_user_id):
                    return target_user_id  # 冪等201: 手順5〜7スキップ(引用#16)
                now = self._clock.now()
                await store.insert_block(
                    conn, blocker=me, blocked=target_user_id, now=now
                )
                await self._cancel_open_latches(
                    conn, me=me, other=target_user_id, now=now
                )
            await self._invalidate(me, target_user_id)  # コミット後DEL(§2.2)
            return target_user_id
        except SafetyError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    async def _cancel_open_latches(
        self, conn, *, me: uuid.UUID, other: uuid.UUID, now: datetime
    ) -> None:
        """D-23: 両者を共に含む進行中latchesのcancelled化(design §2.3)。

        対象はcandidateを含む3状態(承認事項②)。cancel_latchは競合負け
        Falseで読み飛ばし、成功時のみイベント挿入(user_id=blocker — 引用#12)。
        参加Intentはactiveのまま(引用#11)・通知は送らない(引用#13)。
        """
        my_intents = await store.select_user_intent_ids(conn, me)
        their_intents = await store.select_user_intent_ids(conn, other)
        rows = await store.select_open_latches_between(
            conn, my_intents, their_intents
        )
        for latch_id, from_status in rows:
            if await latches_store.cancel_latch(conn, latch_id):
                await latches_store.insert_latch_event(
                    conn, latch_id, from_status, "cancelled", me, now
                )

    async def unblock_user(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        target_user_id: uuid.UUID,
    ) -> None:
        """DELETE /v1/users/{id}/block(design §2.4)。行なし404・遡及なし。"""
        try:
            async with self._engine.begin() as conn:
                me = await self._me(conn, auth_provider, auth_subject)
                if not await store.delete_block(
                    conn, blocker=me, blocked=target_user_id
                ):
                    raise SafetyNotFoundError("block not found")
            await self._invalidate(me, target_user_id)
        except SafetyError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    async def list_blocks(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list, str | None]:
        """GET /v1/users/me/blocks(design §2.4)。created_at降順cursor改頁。"""
        try:
            async with self._engine.connect() as conn:
                me = await self._me(conn, auth_provider, auth_subject)
                before = decode_block_cursor(cursor) if cursor else None
                rows = await store.select_blocks_page(
                    conn, me=me, before=before, limit=limit + 1
                )
            items = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = encode_block_cursor(last.created_at, last.id)
            return items, next_cursor
        except SafetyError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_service.py -v`
Expected: PASS(13件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/safety/service.py backend/tests/unit/safety/test_safety_service.py
git commit -m "feat: add block/unblock/list use cases with D-23 cancel and cursor codec (M3 ws-5)"
```

### Task 4: service report系(POST /v1/reports)

**Files:**
- Modify: `backend/src/latch/safety/service.py`(BlockReportService へ `report_user` 1メソッド追加)
- Test: `backend/tests/unit/safety/test_safety_service.py`(末尾へ追記・3件)

**Interfaces:**
- Consumes: Task 1の `store.insert_report`・latchesの `latches_store.select_latch`/`fetch_participant_user_ids`
- Produces: `await report_user(*, auth_provider: str, auth_subject: str, reportee_id: uuid.UUID, latch_id: uuid.UUID | None = None, reason: str) -> uuid.UUID`(report_idを返す。Task 5のrouteが消費)

- [ ] **Step 1: 失敗するテストを書く**

`test_safety_service.py` の末尾へ追記(latch行スタブ用に `_row` と同じ要領で `SimpleNamespace` を使うため、import部へ `from types import SimpleNamespace` を1行追加する):

```python
# -- report_user(design §2.5) --


def _latch_row(intent_ids: list) -> SimpleNamespace:
    """latches行スタブ(report_userはintent_idsのみ読む)。"""
    return SimpleNamespace(id=uuid.uuid4(), intent_ids=intent_ids)


async def test_report_self_422_and_reportee_missing_404(monkeypatch):
    """自分自身は422・reportee不在は404(design §2.5)。"""
    from latch.safety.errors import SafetyNotFoundError

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    with pytest.raises(SafetyValidationError):
        await _svc().report_user(
            auth_provider="google",
            auth_subject="s",
            reportee_id=ME,
            reason="other",
        )
    monkeypatch.setattr(safety_store, "user_exists", _ret(False))
    with pytest.raises(SafetyNotFoundError):
        await _svc().report_user(
            auth_provider="google",
            auth_subject="s",
            reportee_id=TARGET,
            reason="other",
        )


async def test_report_latch_participation(monkeypatch):
    """latch_id指定時: 不在404・自分非参加422・reportee非参加422(§2.5)。"""
    from latch.safety.errors import SafetyNotFoundError

    i1, i2, i3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    latch = _latch_row([i1, i2])  # 自分+第三者のlatch(TARGET非参加)
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))

    async def _participants(conn, intent_ids):
        return [ME, i3]  # latchの参加者(TARGETを含まない)

    monkeypatch.setattr(latches_store, "select_latch", _ret(latch))
    monkeypatch.setattr(
        latches_store, "fetch_participant_user_ids", _participants
    )
    # reporteeが非参加 → 422
    with pytest.raises(SafetyValidationError):
        await _svc().report_user(
            auth_provider="google",
            auth_subject="s",
            reportee_id=TARGET,
            latch_id=latch.id,
            reason="other",
        )
    # 自分が非参加(他人のlatch) → 422
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(uuid.uuid4()))
    with pytest.raises(SafetyValidationError):
        await _svc().report_user(
            auth_provider="google",
            auth_subject="s",
            reportee_id=TARGET,
            latch_id=latch.id,
            reason="other",
        )
    # latch不在 → 404
    monkeypatch.setattr(latches_store, "select_latch", _ret(None))
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    with pytest.raises(SafetyNotFoundError):
        await _svc().report_user(
            auth_provider="google",
            auth_subject="s",
            reportee_id=TARGET,
            latch_id=uuid.uuid4(),
            reason="other",
        )


async def test_report_inserts_pending(monkeypatch):
    """挿入はstatus='pending'固定・latch_id省略可(null・§2.5)。"""
    i1, i2 = uuid.uuid4(), uuid.uuid4()
    latch = _latch_row([i1, i2])
    inserts: list[dict] = []

    async def _insert_report(
        conn, *, reporter, reportee, latch_id, reason, status, now
    ):
        inserts.append(
            {
                "reporter": reporter,
                "reportee": reportee,
                "latch_id": latch_id,
                "reason": reason,
                "status": status,
                "now": now,
            }
        )
        return uuid.uuid4()

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))
    monkeypatch.setattr(latches_store, "select_latch", _ret(latch))

    async def _participants(conn, intent_ids):
        return [ME, TARGET]

    monkeypatch.setattr(
        latches_store, "fetch_participant_user_ids", _participants
    )
    monkeypatch.setattr(safety_store, "insert_report", _insert_report)
    await _svc().report_user(
        auth_provider="google",
        auth_subject="s",
        reportee_id=TARGET,
        latch_id=latch.id,
        reason="inappropriate_content",
    )
    await _svc().report_user(
        auth_provider="google", auth_subject="s", reportee_id=TARGET, reason="other"
    )
    assert inserts == [
        {
            "reporter": ME,
            "reportee": TARGET,
            "latch_id": latch.id,
            "reason": "inappropriate_content",
            "status": "pending",
            "now": NOW,
        },
        {
            "reporter": ME,
            "reportee": TARGET,
            "latch_id": None,  # 省略時はnull(引用#6)
            "reason": "other",
            "status": "pending",
            "now": NOW,
        },
    ]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_service.py -v`
Expected: 追加3件が FAIL(`report_user` 属性なし)/ Task 3の13件はPASS

- [ ] **Step 3: 最小実装**

`BlockReportService` へ — `list_blocks` の後(クラス内の末尾)へ1メソッド追加:

```python
    async def report_user(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        reportee_id: uuid.UUID,
        latch_id: uuid.UUID | None = None,
        reason: str,
    ) -> uuid.UUID:
        """POST /v1/reports(design §2.5)。受付・記録のみ・pending固定。"""
        try:
            async with self._engine.begin() as conn:
                me = await self._me(conn, auth_provider, auth_subject)
                if reportee_id == me:
                    raise SafetyValidationError("cannot report yourself")
                if not await store.user_exists(conn, reportee_id):
                    raise SafetyNotFoundError("user not found")
                if latch_id is not None:
                    row = await latches_store.select_latch(conn, latch_id)
                    if row is None:
                        raise SafetyNotFoundError("latch not found")
                    participants = await latches_store.fetch_participant_user_ids(
                        conn, row.intent_ids
                    )
                    if me not in participants or reportee_id not in participants:
                        raise SafetyValidationError("not latch participants")
                now = self._clock.now()
                return await store.insert_report(
                    conn,
                    reporter=me,
                    reportee=reportee_id,
                    latch_id=latch_id,
                    reason=reason,
                    status=REPORT_STATUS_PENDING,
                    now=now,
                )
        except SafetyError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/safety -v`
Expected: PASS(cache 7+store_sql 8+service 16=31件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/safety/service.py backend/tests/unit/safety/test_safety_service.py
git commit -m "feat: add report use case with 4-value reason codes and pending status (M3 ws-5)"
```

### Task 5: safety/routes.py + __init__.py + main.py構成 + レート制限ピン追従

**Files:**
- Create: `backend/src/latch/safety/routes.py`
- Modify: `backend/src/latch/safety/__init__.py`(暫定の空ファイルへ本格記載)
- Modify: `backend/src/latch/main.py`(import・create_app引数・lifespan build_safety・router登録・SafetyErrorハンドラ。**build_latches区画のblock_cache渡しはTask 6**)
- Create: `backend/tests/unit/safety/test_safety_routes.py`(新規・7件)
- Modify: `backend/tests/unit/test_rate_limit_wiring.py`(期待値Counterへ3エントリ追記)

**Interfaces:**
- Consumes: Task 3〜4の `BlockReportService` 4メソッド・Task 1のerrors
- Produces: HTTP契約 `POST /v1/users/{user_id}/block`(201 `{"blocked_id"}`)・`DELETE /v1/users/{user_id}/block`(204)・`GET /v1/users/me/blocks`(200 `{"items", "next_cursor"}`)・`POST /v1/reports`(201 `{"report_id"}`)。`safety_router`(prefix `/v1`・`api_rate_limited` 依存)・`create_app(safety_service=None)` 引数・`make_safety_service`・`BlockCache` のexport

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/safety/test_safety_routes.py` を新規作成:

```python
"""safetyルーティングのunit試験(M3 ws-5・design §4.1)。

スタブサービス注入で応答形状を検証(test_latches_routes.pyと同型・実HTTPは
test_safety_api.py)。reasonの4値検証(limit外422)・cursor/limit検証は
FastAPI+pydanticが担うため、ここで契約ごとピンする。
"""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.main import create_app
from latch.safety.errors import SafetyNotFoundError
from latch.safety.store import BlockRow

NOW = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
TARGET = str(uuid.uuid4())
REPORT_ID = str(uuid.uuid4())


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sub-1",
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _block_row() -> BlockRow:
    return BlockRow(
        id=uuid.uuid4(),
        blocked_id=uuid.UUID(TARGET),
        display_name="相手",
        created_at=NOW - timedelta(minutes=5),
    )


class StubService:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def block_user(self, **kwargs):
        self.calls.append(("block_user", kwargs))
        return uuid.UUID(TARGET)

    async def unblock_user(self, **kwargs):
        self.calls.append(("unblock_user", kwargs))
        return None

    async def list_blocks(self, **kwargs):
        self.calls.append(("list_blocks", kwargs))
        return ([_block_row()], None)

    async def report_user(self, **kwargs):
        self.calls.append(("report_user", kwargs))
        return uuid.UUID(REPORT_ID)


def _app(svc: StubService):
    app = create_app(safety_service=svc)
    app.dependency_overrides[require_authenticated] = lambda: _claims()
    return app


async def test_block_returns_201():
    """POST /v1/users/{id}/block は201 {"blocked_id"}(design §2.4)。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(f"/v1/users/{TARGET}/block")
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"blocked_id": TARGET}
    assert stub.calls[0][1]["target_user_id"] == uuid.UUID(TARGET)


async def test_unblock_returns_204():
    """DELETE /v1/users/{id}/block は204(authのlogoutと同型)。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/v1/users/{TARGET}/block")
    assert resp.status_code == 204
    assert resp.content == b""


async def test_blocks_list_shape():
    """GET /v1/users/me/blocks の応答形状(design §2.4)。

    items要素はblocked_id/display_name/created_atの3要素のみ。
    """
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/users/me/blocks")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    item = body["items"][0]
    assert set(item) == {"blocked_id", "display_name", "created_at"}
    assert item["blocked_id"] == TARGET
    assert item["display_name"] == "相手"
    assert stub.calls[0][1]["cursor"] is None
    assert stub.calls[0][1]["limit"] == 20  # 既定(引用#8)


async def test_report_returns_201():
    """POST /v1/reports は201 {"report_id"}(design §2.5)。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/reports",
            json={
                "reportee_id": TARGET,
                "latch_id": str(uuid.uuid4()),
                "reason": "inappropriate_content",
            },
        )
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"report_id": REPORT_ID}


async def test_report_invalid_reason_422():
    """reason値域外は422 VALIDATION_ERROR(4値コード・design §2.5)。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/reports",
            json={"reportee_id": TARGET, "reason": "unknown_reason"},
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_blocks_limit_over_422():
    """limit 101は422(改頁共通規定・引用#8)。limit=1は受理。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        over = await client.get("/v1/users/me/blocks", params={"limit": 101})
        ok = await client.get("/v1/users/me/blocks", params={"limit": 1})
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "VALIDATION_ERROR"
    assert ok.status_code == 200


async def test_domain_error_maps_to_envelope():
    """SafetyErrorは共通error envelopeへ(引用#15・NOT_FOUND)。"""

    class _RaiseService(StubService):
        async def block_user(self, **kwargs):
            raise SafetyNotFoundError("user not found")

    async with AsyncClient(
        transport=ASGITransport(app=_app(_RaiseService())), base_url="http://test"
    ) as client:
        resp = await client.post(f"/v1/users/{TARGET}/block")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert set(body["error"]) == {"code", "message", "details"}
```

あわせて `backend/tests/unit/test_rate_limit_wiring.py` の `test_all_v1_routes_are_rate_limited` のCounter期待値へ、アルファベット順の該当箇所へ3エントリを挿入(`/v1/notifications/{notification_id}/read` の後・`/v1/users` の前に `/v1/reports`、`/v1/users/me` の後に `/v1/users/me/blocks` と `/v1/users/{user_id}/block`):

```python
        "/v1/notifications": 1,  # M3 ws-3(お知らせ一覧)
        "/v1/notifications/{notification_id}/read": 1,  # M3 ws-3(既読)
        "/v1/reports": 1,  # M3 ws-5(通報)
        "/v1/users": 1,
        "/v1/users/me": 1,
        "/v1/users/me/blocks": 1,  # M3 ws-5(ブロック一覧)
        "/v1/users/{user_id}/block": 2,  # M3 ws-5(登録+解除)
```

(挿入する3行は `/v1/reports`: 1・`/v1/users/me/blocks`: 1・`/v1/users/{user_id}/block`: 2。位置は機械的・並走単位は§0のとおりスーパーバイザーが両側保持でマージする)

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_routes.py tests/unit/test_rate_limit_wiring.py -v`
Expected: routes 7件とも FAIL/ERROR(`create_app() got an unexpected keyword argument 'safety_service'` 等)/ wiring はFAIL(Counter不一致 — 新ルートがまだ `protected` に入らない)

- [ ] **Step 3: 最小実装**

(1) `backend/src/latch/safety/routes.py` を作成:

```python
"""safetyルータ(M3 ws-5 design §2.1・§2.4・§2.5・05 §5)。

/v1/users/me/blocks・/v1/users/{id}/block(登録/解除)・/v1/reportsの
4エンドポイント。claimsはプリミティブ(provider/subject)としてサービスへ
渡す(intents/latchesと同型)。ログはイベント名と結果/コードのみ —
通報理由・表示名は出さない(08 §2.4)。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.ratelimit.deps import api_rate_limited
from latch.safety.service import BlockReportService

logger = logging.getLogger("latch.safety")

safety_router = APIRouter(
    prefix="/v1",
    tags=["safety"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4と同型)
)

# 通報理由は選択式4値の英語コード(08 §5.2・design §2.5。表示文言はフロント)
ReportReason = Literal[
    "inappropriate_content",
    "unpleasant_behavior",
    "suspected_impersonation",
    "other",
]


def get_safety_service(request: Request) -> BlockReportService:
    """app.state.safety_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.safety_service


class BlockResponse(BaseModel):
    """POST /v1/users/{id}/block の201応答(design §2.4)。冪等(既存でも同形)。"""

    blocked_id: uuid.UUID


class BlockOut(BaseModel):
    """blocks一覧の1行(design §2.4・display_nameはsupervisor承認⑤)。"""

    model_config = ConfigDict(from_attributes=True)

    blocked_id: uuid.UUID
    display_name: str
    created_at: datetime


class BlockListResponse(BaseModel):
    """GET /v1/users/me/blocks 応答(05 §5共通規定・cursor改頁)。"""

    items: list[BlockOut]
    next_cursor: str | None = None


class ReportRequest(BaseModel):
    """POST /v1/reports のbody(design §2.5)。latch_idは省略可(引用#6)。"""

    reportee_id: uuid.UUID
    latch_id: uuid.UUID | None = None
    reason: ReportReason


class ReportResponse(BaseModel):
    """POST /v1/reports の201応答。"""

    report_id: uuid.UUID


@safety_router.get("/users/me/blocks", response_model=BlockListResponse)
async def list_blocks(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[BlockReportService, Depends(get_safety_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> BlockListResponse:
    """GET /v1/users/me/blocks(05 §5。created_at降順・本人のみ)。"""
    items, next_cursor = await svc.list_blocks(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        cursor=cursor,
        limit=limit,
    )
    return BlockListResponse(items=items, next_cursor=next_cursor)


@safety_router.post(
    "/users/{user_id}/block", response_model=BlockResponse, status_code=201
)
async def block_user(
    user_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[BlockReportService, Depends(get_safety_service)],
) -> BlockResponse:
    """POST /v1/users/{id}/block(05 §5。冪等201・D-23はserviceが実行)。"""
    blocked_id = await svc.block_user(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        target_user_id=user_id,
    )
    logger.info("safety.block ok")
    return BlockResponse(blocked_id=blocked_id)


@safety_router.delete("/users/{user_id}/block", status_code=204)
async def unblock_user(
    user_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[BlockReportService, Depends(get_safety_service)],
) -> None:
    """DELETE /v1/users/{id}/block(05 §5。204・行なし404・遡及なし)。"""
    await svc.unblock_user(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        target_user_id=user_id,
    )
    logger.info("safety.unblock ok")


@safety_router.post("/reports", response_model=ReportResponse, status_code=201)
async def report_user(
    body: ReportRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[BlockReportService, Depends(get_safety_service)],
) -> ReportResponse:
    """POST /v1/reports(08 §5.2。受付・記録のみ・status=pending)。"""
    report_id = await svc.report_user(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        reportee_id=body.reportee_id,
        latch_id=body.latch_id,
        reason=body.reason,
    )
    logger.info("safety.report ok")
    return ReportResponse(report_id=report_id)
```

(2) `backend/src/latch/safety/__init__.py` を空ファイルから次へ書き換え:

```python
"""safetyドメイン(M3 ws-5)。ブロック・通報(01 §22の「相手からの防護」群)。"""

from latch.safety.cache import BlockCache
from latch.safety.errors import (
    SafetyDependencyUnavailableError,
    SafetyError,
    SafetyNotFoundError,
    SafetyValidationError,
)
from latch.safety.routes import safety_router
from latch.safety.service import BlockReportService, make_safety_service

__all__ = [
    "BlockCache",
    "BlockReportService",
    "SafetyDependencyUnavailableError",
    "SafetyError",
    "SafetyNotFoundError",
    "SafetyValidationError",
    "make_safety_service",
    "safety_router",
]
```

`backend/src/latch/safety/service.py` の末尾へ `make_safety_service` を追加(lifespan用の構築・Task 3で書き漏れた場合に備えここで確定):

```python
def make_safety_service(
    *, clock: Clock, engine: AsyncEngine, block_cache: BlockCache | None = None
) -> BlockReportService:
    """main.py lifespan用の構築(§2.1・block_cacheはlatchesと共有資産)。"""
    return BlockReportService(
        clock=clock, engine=engine, block_cache=block_cache
    )
```

(3) `backend/src/latch/main.py` を変更。**5箇所**:

(a) import部へ追加(`from latch.ratelimit import make_rate_limiter` の後・settings importの前。ruff isort順):

```python
from latch.safety import (
    BlockCache,
    SafetyError,
    make_safety_service,
    safety_router,
)
```

(b) logger行へ追加(ratelimit_loggerの後):

```python
safety_logger = logging.getLogger("latch.safety")
```

(c) `_lifespan` 内 — build判定フラグ(`build_notifications` の後へ1行):

```python
    build_safety = not hasattr(app.state, "safety_service")
```

早期リターン条件へOR追加(`or build_events` の後へ `or build_safety` を追記):

```python
    if not (
        build_auth
        or build_users
        or build_intents
        or build_intents_crud
        or build_latches
        or build_notifications
        or build_rate_limit
        or build_events
        or build_safety
    ):
        yield
        return
```

Redis生成条件へOR追加:

```python
    redis_client = None
    if build_auth or build_rate_limit or build_safety:
        # Redisはauth(失効リスト)・レート制限カウンタ・ブロックキャッシュ(blk:)
        # の共用(design §2.8・ws-5 §2.2)
        redis_client = aioredis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
```

`if build_latches:` 区画の**直前**へsafety区画を追加(block_cacheをlatchesより先に構築するため。main.py内の順序は `elif build_intents_crud:` → `if build_latches:` → `if build_notifications:` なので、build_latchesの直前=build_notificationsより前):

```python
    block_cache = None
    if build_safety:
        # BlockCache は safety(書込み側)と latches(送信判定)の共有資産
        block_cache = BlockCache(redis_client=redis_client, engine=engine)
        app.state.safety_service = make_safety_service(
            clock=app.state.clock, engine=engine, block_cache=block_cache
        )
```

(**build_latches区画のblock_cache渡しはTask 6で行う — このTaskでは `make_latches_service(clock=app.state.clock, engine=engine)` のまま変更しない**)

(d) `create_app` の引数へ `safety_service=None` を追加(`notifications_service=None,` の後)し、関数冒頭のstate設定へ追加:

```python
    notifications_service=None,
    safety_service=None,
```

```python
    if notifications_service is not None:
        app.state.notifications_service = notifications_service
    if safety_service is not None:
        app.state.safety_service = safety_service
```

(e) router登録(`app.include_router(notifications_router)` の後)とハンドラ(`NotificationsError` ハンドラの後):

```python
    app.include_router(notifications_router)
    app.include_router(safety_router)
```

```python
    @app.exception_handler(SafetyError)
    async def safety_error_handler(request: Request, exc: SafetyError) -> JSONResponse:
        safety_logger.warning("safety.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_routes.py tests/unit/test_rate_limit_wiring.py tests/unit/test_app_health.py -v`
Expected: PASS(routes 7+wiring 1+app_health既存。`make lint && make test` もこの時点でグリーンなことを確認 — 既存試験はcreate_appのデフォルト引数追加で無傷のはず)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/safety/routes.py backend/src/latch/safety/__init__.py backend/src/latch/safety/service.py backend/src/latch/main.py backend/tests/unit/safety/test_safety_routes.py backend/tests/unit/test_rate_limit_wiring.py
git commit -m "feat: expose safety router (block/report endpoints) and wire into app (M3 ws-5)"
```

### Task 6: send_messageのキャッシュ差し替え(latches/service.py・design §2.2の差し替え点)

**Files:**
- Modify: `backend/src/latch/latches/service.py`(`LatchesService.__init__` へ `block_cache=None`・`send_message` のblocks判定差し替え・`make_latches_service` へ引数)
- Modify: `backend/src/latch/main.py`(build_latches区画の `make_latches_service` 呼び出しへ `block_cache` 渡し・1行)
- Modify: `backend/tests/unit/latches/test_chat_service.py`(末尾へ1件追記)
- Modify: `backend/tests/integration/test_chat_attendance_api.py`(§4のスコープ外最小変更 — `_block` ヘルパーへの手動DEL追加)

**Interfaces:**
- Consumes: Task 2の `BlockCache.is_blocked_between`
- Produces: `LatchesService(*, clock, engine, geo=None, block_cache=None)`・`make_latches_service(*, clock, engine, block_cache=None)`。`send_message` は注入時キャッシュ優先・未注入時 `store.select_block_between`(DB直読み・既存試験互換)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/latches/test_chat_service.py` の末尾(ファイル最後)へ追記:

```python
async def test_send_message_block_cache_paths(monkeypatch):
    """block_cache注入時はキャッシュ判定・未注入時はDB直読み(ws-5 §2.2)。

    既存試験(select_block_betweenモック)は未注入経路の担保として残る。
    (a)注入+ブロックあり→DBを問わず409 (b)注入+なし→INSERT
    (c)未注入→select_block_between経由。
    """
    from latch.latches import store as store_mod

    class _StubCache:
        def __init__(self, blocked: bool):
            self.blocked = blocked
            self.calls: list[tuple] = []

        async def is_blocked_between(self, me, others):
            self.calls.append((me, tuple(others)))
            return self.blocked

    row = _row(status="matched")
    out_msg = _msg(row, body="キャッシュ経路")
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "fetch_participant_user_ids", _ret([ME, PEER]))
    monkeypatch.setattr(store_mod, "insert_message", _ret(out_msg))
    db_calls: list[tuple] = []

    async def _db_between(conn, me, others):
        db_calls.append((me, tuple(others)))
        return False  # DB側は常に「ブロックなし」

    monkeypatch.setattr(store_mod, "select_block_between", _db_between)
    # (a) 注入+キャッシュTrue: DB(False)よりキャッシュを優先して409
    cache = _StubCache(True)
    with pytest.raises(ChatReadonlyError):
        await LatchesService(
            clock=FakeClock(NOW), engine=_NoopEngine(), block_cache=cache
        ).send_message(
            auth_provider="google", auth_subject="s", latch_id=row.id, body="x"
        )
    assert cache.calls == [(ME, (PEER,))]
    assert db_calls == []  # キャッシュ経路ではDBを問わない
    # (b) 注入+キャッシュFalse: INSERTへ到達
    got = await LatchesService(
        clock=FakeClock(NOW), engine=_NoopEngine(), block_cache=_StubCache(False)
    ).send_message(
        auth_provider="google", auth_subject="s", latch_id=row.id, body="キャッシュ経路"
    )
    assert got is out_msg
    # (c) 未注入: 従来どおりselect_block_between(design §2.2の残置経路)
    svc_plain = LatchesService(clock=FakeClock(NOW), engine=_NoopEngine())
    got2 = await svc_plain.send_message(
        auth_provider="google", auth_subject="s", latch_id=row.id, body="x"
    )
    assert got2 is out_msg
    assert db_calls == [(ME, (PEER,))]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_service.py -v`
Expected: 追加1件が FAIL(`LatchesService.__init__() got an unexpected keyword argument 'block_cache'`)/ 既存13件はPASS

- [ ] **Step 3: 最小実装**

`backend/src/latch/latches/service.py` を3箇所変更:

(1) `LatchesService.__init__`(`latches/service.py:68-71`)へ `block_cache` を追加:

```python
    def __init__(
        self, *, clock: Clock, engine: AsyncEngine, geo=None, block_cache=None
    ) -> None:
        self._clock = clock
        self._engine = engine
        self._geo = geo  # GeoService | None(Noneならarea_name=None)
        self._block_cache = block_cache  # BlockCache | None(NoneならDB直読み)
```

(2) `send_message` のblocks判定(`latches/service.py:261-264`付近)を差し替え:

```python
                user_ids = await store.fetch_participant_user_ids(
                    conn, row.intent_ids
                )
                others = [u for u in user_ids if u != user_id]
                if self._block_cache is not None:
                    blocked = await self._block_cache.is_blocked_between(
                        user_id, others
                    )
                else:
                    blocked = await store.select_block_between(
                        conn, user_id, others
                    )
                if blocked:
                    raise ChatReadonlyError("chat readonly")
```

(置き換え前: `if await store.select_block_between(conn, user_id, others): raise ChatReadonlyError("chat readonly")`。コメント行「ws-5がRedisキャッシュ差し替え点…」がstore側のdocstringにあるためservice側に重複コメントは不要・上の形のまま)

(3) `make_latches_service`(`latches/service.py:576-580`)へ引数追加:

```python
def make_latches_service(
    *, clock: Clock, engine: AsyncEngine, block_cache=None
) -> LatchesService:
    """main.py lifespan用の構築(GeoServiceはengineから作る)。"""
    from latch.geo.service import GeoService

    return LatchesService(
        clock=clock, engine=engine, geo=GeoService(engine), block_cache=block_cache
    )
```

`backend/src/latch/main.py` のbuild_latches区画(Task 5でblock_cache変数を導入済み)を1行変更:

```python
    if build_latches:
        app.state.latches_service = make_latches_service(
            clock=app.state.clock, engine=engine, block_cache=block_cache
        )
```

`backend/tests/integration/test_chat_attendance_api.py` の最小変更(§4)。`_block` ヘルパーを差し替え(引数へ `redis_client=None` を追加しINSERT後に該当2ユーザーの `blk:u:` キーを手動DEL):

```python
async def _block(
    db_engine, blocker_intent: str, blocked_intent: str, redis_client=None
) -> None:
    """blocks行を直接INSERT(ws-5適用後の整合: blk:u:手動DELつき)。

    API経由でない行追加はキャッシュ更新経由を通らないため、該当2
    ユーザーのキーを手動DELして送信判定へ反映させる(design §2.2整合・
    ws-5計画§4のスコープ外最小変更)。
    """
    async with db_engine.begin() as conn:
        a = (
            await conn.execute(
                text("SELECT user_id FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": blocker_intent},
            )
        ).scalar_one()
        b = (
            await conn.execute(
                text("SELECT user_id FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": blocked_intent},
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO blocks (blocker_id, blocked_id, created_at)"
                " VALUES (CAST(:a AS uuid), CAST(:b AS uuid),"
                " CAST(:now AS timestamptz))"
            ),
            {"a": a, "b": b, "now": SystemClock().now()},
        )
    if redis_client is not None:
        await redis_client.delete(f"blk:u:{a}", f"blk:u:{b}")
```

`test_6_block_both_directions_409` のシグネチャと `_block` 呼び出しを変更:

```python
async def test_6_block_both_directions_409(api_client, db_engine, redis_client, field):
```

```python
    await _block(db_engine, i1["id"], i2["id"], redis_client)  # (A,B)のみ挿入
```

(test_6の本体アサーションは無変更。他試験の `_block` 呼び出しは存在しないため影響なし)

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches -v && uv run pytest tests/integration/test_chat_attendance_api.py --collect-only -q`
Expected: unit latches全件PASS(test_chat_service.py 14件=既存13+追記1)/ integration収集17件のまま(変更はcollect結果に影響しない)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/service.py backend/src/latch/main.py backend/tests/unit/latches/test_chat_service.py backend/tests/integration/test_chat_attendance_api.py
git commit -m "feat: route send_message blocks check through BlockCache (M3 ws-5 design 2.2)"
```

### Task 7: 統合試験9件(作成のみ・実行はスーパーバイザー)

**Files:**
- Create: `backend/tests/integration/test_safety_api.py`

**Interfaces:**
- Consumes: 実装済みの4エンドポイント(HTTP)・compose常設DB/Redis/api(スーパーバイザー検証時に稼働)。fixture・ヘルパーはws-4の `test_chat_attendance_api.py` と同型(SUBJECT_PREFIX=`m3ws5-`・teardownへblocks/reports掃除とRedisキー `blk:u:` 掃除を追加)

**重要**: 本Taskの試験は**実行しない**(§0)。`--collect-only` で9件が収集できることのみ検証する。壊れていたらスーパーバイザー検証で全waveが止まる — ヘルパー・SQL・期待値は本計画のコードをそのまま写し、独自の変更をしないこと。**agent3はcollect-onlyまでしか実行しない前提で、書いた後に自分で見直す**(ヘルパーの戻り値unpack・引数形式・比較の型 — ws-3で計画書のテストコード欠陥3系統が検証時発覚した教訓)。

- [ ] **Step 1: 試験ファイルを作成**

`backend/tests/integration/test_safety_api.py`:

```python
"""ブロック・通報のintegration試験(M3 ws-5 design §4.2の9試験)。

実DB(compose常設)・実Redis・api常設(実HTTP)。users/intents/latches行は
fixtureで直接INSERT(ws-4のtest_chat_attendance_api.pyと同型・パイプライン
を走らせない)。ブロック登録・解除・通報は実API(D-23 cancelled化・キャッシュ
DELを含む本体が対象のため)。時間値はnow相対(タイムボム回避)。teardownは
FK順+blocks/reports+Redisキーblk:u:掃除(SUBJECT_PREFIX=m3ws5-)。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120
SUBJECT_PREFIX = "m3ws5-"


@pytest.fixture
async def field(db_engine, redis_client):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除+
    Redisキーblk:u:掃除(users削除前にid一覧を取得)。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT id FROM users WHERE auth_subject LIKE :p"), p
            )
        ).fetchall()
    ids = [str(r[0]) for r in rows]
    if ids:
        await redis_client.delete(*[f"blk:u:{i}" for i in ids])
    async with db_engine.begin() as conn:
        # FK順: reports → messages → latch_status_events →
        # calibration_records → notifications → latches →
        # (match/group/events) → blocks → intents → users
        await conn.execute(
            text(
                "DELETE FROM reports WHERE reporter_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                " OR reportee_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM messages WHERE latch_id IN"
                " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
            ),
            p,
        )
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
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
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
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM blocks WHERE blocker_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                " OR blocked_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
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


async def _user(api_client, prefix: str, name: str = "m3ws5") -> dict:
    """ユーザー登録 → {"headers", "id", "display_name"}(登録応答のuser.id)。"""
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
        json={"display_name": name, "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    user = created.json()["user"]
    return {"headers": headers, "id": user["id"], "display_name": name}


async def _ghost_headers(api_client, prefix: str) -> dict:
    """未登録JWTのヘッダー(トークン発行のみ・/v1/users未登録)。"""
    subject = f"{prefix}{uuid_mod.uuid4().hex[:8]}"
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured(*, secondary: str | None = None, start: str | None = None) -> dict:
    return {
        "category": {"primary": CATEGORY, "secondary": secondary},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }


async def _intent(api_client, headers, *, start: str | None = None) -> dict:
    resp = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={
            "raw_text": "分類用テキスト",
            "status": "active",
            "structured_intent": _structured(start=start),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]


async def _latch(
    db_engine,
    intent_ids: list,
    *,
    status: str = "matched",
    score: float = 0.85,
    gid: str | None = None,
    proposal: dict | None = None,
) -> str:
    """latches行を直接INSERT(fixture・design §4.2)。既定はmatched。"""
    now = SystemClock().now()
    if proposal is None:
        proposal = {"headcount": len(intent_ids), "match_level": "medium"}
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, group_candidate_id, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:gid AS uuid),
                        CAST(:proposal AS jsonb), :score, :status,
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in intent_ids],
                "gid": uuid_mod.UUID(gid) if gid else None,
                "proposal": json.dumps(proposal, ensure_ascii=False),
                "score": score,
                "status": status,
                "deadline": now + timedelta(hours=2.0),
                "expires": now + timedelta(hours=120.0),
                "now": now,
            },
        )
    return str(res.first()[0])


async def _send(api_client, headers, latch_id: str, body: str):
    return await api_client.post(
        f"/v1/latches/{latch_id}/messages", headers=headers, json={"body": body}
    )


def _uuids(values: list) -> list:
    return [uuid_mod.UUID(v) for v in values]


# -- 試験1: 登録→一覧→解除のサイクル(冪等201・display_name・再解除404) --


async def test_1_block_register_list_unblock_cycle(api_client, db_engine, field):
    h1 = await _user(api_client, field, name="登録者")
    h2 = await _user(api_client, field, name="ふたりめ")
    h3 = await _user(api_client, field, name="さんばんめ")
    r1 = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r1.status_code == 201, r1.text
    assert r1.json() == {"blocked_id": h2["id"]}
    r1b = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r1b.status_code == 201  # 冪等201(design §2.4手順4)
    assert r1b.json() == {"blocked_id": h2["id"]}
    r2 = await api_client.post(f"/v1/users/{h3['id']}/block", headers=h1["headers"])
    assert r2.status_code == 201
    # blocks行は相手ごとに1行(存在検査・引用#16)
    async with db_engine.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM blocks"
                    " WHERE blocker_id = CAST(:a AS uuid)"
                ),
                {"a": h1["id"]},
            )
        ).scalar_one()
    assert n == 2  # 二重POSTしても増えない
    listed = await api_client.get("/v1/users/me/blocks", headers=h1["headers"])
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {i["blocked_id"] for i in body["items"]} == {h2["id"], h3["id"]}
    assert {i["display_name"] for i in body["items"]} == {"ふたりめ", "さんばんめ"}
    cts = [
        datetime.fromisoformat(i["created_at"].replace("Z", "+00:00"))
        for i in body["items"]
    ]
    assert cts[0] >= cts[1]  # created_at降順(同一時刻はid DESCで決まる)
    assert body["next_cursor"] is None
    d = await api_client.delete(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert d.status_code == 204
    d2 = await api_client.delete(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert d2.status_code == 404  # 再解除は404(冪等204にしない・§2.4)
    assert d2.json()["error"]["code"] == "NOT_FOUND"
    listed2 = await api_client.get("/v1/users/me/blocks", headers=h1["headers"])
    assert {i["blocked_id"] for i in listed2.json()["items"]} == {h3["id"]}


# -- 試験2: 検証エラー(自分422・相手不在404・未登録JWT 404) --


async def test_2_block_validation_errors(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    r = await api_client.post(f"/v1/users/{h1['id']}/block", headers=h1["headers"])
    assert r.status_code == 422  # 自分自身(引用#15 — 専用codeなし)
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    r2 = await api_client.post(
        f"/v1/users/{uuid_mod.uuid4()}/block", headers=h1["headers"]
    )
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "NOT_FOUND"
    ghost = await _ghost_headers(api_client, field)
    r3 = await api_client.post(f"/v1/users/{h1['id']}/block", headers=ghost)
    assert r3.status_code == 404  # 未登録JWT(design §2.4手順1)
    r4 = await api_client.delete(
        f"/v1/users/{uuid_mod.uuid4()}/block", headers=h1["headers"]
    )
    assert r4.status_code == 404
    assert r4.json()["error"]["code"] == "NOT_FOUND"


# -- 試験3: 一覧のcursor改頁 --


async def test_3_block_list_pagination(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    targets = [(await _user(api_client, field))["id"] for _ in range(3)]
    for t in targets:
        r = await api_client.post(f"/v1/users/{t}/block", headers=h1["headers"])
        assert r.status_code == 201
    p1 = await api_client.get(
        "/v1/users/me/blocks", headers=h1["headers"], params={"limit": 2}
    )
    body1 = p1.json()
    assert len(body1["items"]) == 2
    assert isinstance(body1["next_cursor"], str)  # cursorは不透明(引用#8)
    p2 = await api_client.get(
        "/v1/users/me/blocks",
        headers=h1["headers"],
        params={"limit": 2, "cursor": body1["next_cursor"]},
    )
    body2 = p2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None  # 終端
    got = {i["blocked_id"] for i in body1["items"] + body2["items"]}
    assert got == set(targets)  # 重複・欠落なし
    over = await api_client.get(
        "/v1/users/me/blocks", headers=h1["headers"], params={"limit": 101}
    )
    assert over.status_code == 422
    bad = await api_client.get(
        "/v1/users/me/blocks", headers=h1["headers"], params={"cursor": "!!!"}
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"


# -- 試験4: ブロック後のチャット送信409(キャッシュ経由: 温め→DEL→再構築) --


async def test_4_block_cache_chat_409(
    api_client, db_engine, redis_client, field
):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    # 登録前に一度送信してキャッシュを温める(design §4.2試験2)
    warm = await _send(api_client, h1["headers"], latch, "こんにちは")
    assert warm.status_code == 201, warm.text
    assert await redis_client.exists(f"blk:u:{h1['id']}") == 1
    assert await redis_client.exists(f"blk:u:{h2['id']}") == 1
    # ブロック登録 → コミット後DEL(§2.2)
    r = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r.status_code == 201, r.text
    assert await redis_client.exists(f"blk:u:{h1['id']}") == 0
    assert await redis_client.exists(f"blk:u:{h2['id']}") == 0
    # 再送信は再構築(read-through)でblocks行を見て409
    ra = await _send(api_client, h1["headers"], latch, "x")
    assert ra.status_code == 409
    assert ra.json()["error"]["code"] == "CHAT_READONLY"
    rb = await _send(api_client, h2["headers"], latch, "x")  # 双方向(引用#1)
    assert rb.status_code == 409
    assert rb.json()["error"]["code"] == "CHAT_READONLY"


# -- 試験5: 解除後は現物参照で送信可に戻る(supervisor承認③) --


async def test_5_unblock_restores_chat(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    r = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r.status_code == 201, r.text
    assert (await _send(api_client, h1["headers"], latch, "x")).status_code == 409
    d = await api_client.delete(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert d.status_code == 204
    after = await _send(api_client, h1["headers"], latch, "解除後もよろしく")
    assert after.status_code == 201, after.text  # §2.4 — 遡及なし・未来は従う


# -- 試験6: D-23本体(candidate/proposed/partial_accept閉鎖・matched維持) --


async def test_6_d23_cancels_open_latches(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    made = []  # (status, latch_id, u1側intent_id)
    for status in ("candidate", "proposed", "partial_accept", "matched"):
        ia = await _intent(api_client, h1["headers"])
        ib = await _intent(api_client, h2["headers"])
        latch = await _latch(db_engine, [ia["id"], ib["id"]], status=status)
        made.append((status, latch, ia["id"]))
    r = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r.status_code == 201, r.text
    latch_ids = [l for _, l, _ in made]
    intent_ids = [i for _, _, i in made]
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT status FROM latches"
                    " WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
                ),
                {"ids": _uuids(latch_ids)},
            )
        ).fetchall()
        statuses = {row[0] for row in rows}
        # candidate/proposed/partial_acceptはcancelled・matchedは維持(引用#4)
        assert statuses == {"cancelled", "matched"}
        assert [row[0] for row in rows].count("cancelled") == 3
        ev = (
            await conn.execute(
                text("""
                    SELECT from_status, to_status, user_id
                    FROM latch_status_events
                    WHERE latch_id = ANY(CAST(:ids AS uuid[]))
                      AND to_status = 'cancelled'
                """),
                {"ids": _uuids(latch_ids)},
            )
        ).fetchall()
        assert sorted(e[0] for e in ev) == [
            "candidate",
            "partial_accept",
            "proposed",
        ]
        assert all(e[2] == uuid_mod.UUID(h1["id"]) for e in ev)  # user_id=blocker
        st = (
            await conn.execute(
                text(
                    "SELECT DISTINCT status FROM intents"
                    " WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": _uuids(intent_ids)},
            )
        ).fetchall()
        assert st == [("active",)]  # 参加Intentはactiveのまま(引用#11)
        nn = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM notifications"
                    " WHERE user_id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                ),
                {"a": h1["id"], "b": h2["id"]},
            )
        ).scalar_one()
        assert nn == 0  # 通知なし(引用#13)
    matched_latch = [l for s, l, _ in made if s == "matched"][0]
    rr = await _send(api_client, h1["headers"], matched_latch, "x")
    assert rr.status_code == 409  # matchedは送信だけ409(読取専用化)
    assert rr.json()["error"]["code"] == "CHAT_READONLY"


# -- 試験7: グループLATCH(第三メンバーを含む提案のcancelled) --


async def test_7_d23_group_latch(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    i3 = await _intent(api_client, h3["headers"])
    # u1・u2・u3の3人latch(u1とu2が共に参加)
    latch_g = await _latch(db_engine, [i1["id"], i2["id"], i3["id"]], status="proposed")
    # u2・u3のみのlatch(u1不参加 — ブロック者を含まない提案)
    i2b = await _intent(api_client, h2["headers"])
    i3b = await _intent(api_client, h3["headers"])
    latch_other = await _latch(db_engine, [i2b["id"], i3b["id"]], status="proposed")
    r = await api_client.post(f"/v1/users/{h2['id']}/block", headers=h1["headers"])
    assert r.status_code == 201, r.text
    async with db_engine.connect() as conn:
        sg = (
            await conn.execute(
                text("SELECT status FROM latches WHERE id = CAST(:l AS uuid)"),
                {"l": latch_g},
            )
        ).scalar_one()
        so = (
            await conn.execute(
                text("SELECT status FROM latches WHERE id = CAST(:l AS uuid)"),
                {"l": latch_other},
            )
        ).scalar_one()
    assert sg == "cancelled"  # 第三メンバー(u3)を含んでも閉じる(§2.3)
    assert so == "proposed"  # u1を含まない提案は閉じない


# -- 試験8: reportsの受付サイクル --


async def test_8_reports_cycle(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    # 受付201(latch_idつき・引用#2)
    r = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={
            "reportee_id": h2["id"],
            "latch_id": latch,
            "reason": "inappropriate_content",
        },
    )
    assert r.status_code == 201, r.text
    report_id = r.json()["report_id"]
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT reason, status, latch_id FROM reports"
                    " WHERE id = CAST(:r AS uuid)"
                ),
                {"r": report_id},
            )
        ).first()
    assert row[0] == "inappropriate_content"
    assert row[1] == "pending"  # 固定投入(§2.5)
    assert str(row[2]) == latch
    # latch_idなし受付(引用#6)
    r2 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"reportee_id": h2["id"], "reason": "other"},
    )
    assert r2.status_code == 201
    # 自分自身は422
    r3 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"reportee_id": h1["id"], "reason": "other"},
    )
    assert r3.status_code == 422
    assert r3.json()["error"]["code"] == "VALIDATION_ERROR"
    # reason値域外は422(4値コード・§2.5)
    r4 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"reportee_id": h2["id"], "reason": "unknown"},
    )
    assert r4.status_code == 422
    # reportee不在は404
    r5 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"reportee_id": str(uuid_mod.uuid4()), "reason": "other"},
    )
    assert r5.status_code == 404
    assert r5.json()["error"]["code"] == "NOT_FOUND"
    # 未登録JWTは404
    ghost = await _ghost_headers(api_client, field)
    r6 = await api_client.post(
        "/v1/reports",
        headers=ghost,
        json={"reportee_id": h2["id"], "reason": "other"},
    )
    assert r6.status_code == 404


# -- 試験9: reportsのlatch_id参加者検査 --


async def test_9_reports_latch_participation(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    i3 = await _intent(api_client, h3["headers"])
    # h1+h2のlatch(正常系)とh1+h3のlatch(reportee=h2が非参加)
    latch_ok = await _latch(db_engine, [i1["id"], i2["id"]])
    latch_no_reportee = await _latch(db_engine, [i1["id"], i3["id"]])
    # latch不在は404
    r0 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={
            "reportee_id": h2["id"],
            "latch_id": str(uuid_mod.uuid4()),
            "reason": "other",
        },
    )
    assert r0.status_code == 404
    # reporteeが非参加のlatch → 422(§2.5)
    r1 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={
            "reportee_id": h2["id"],
            "latch_id": latch_no_reportee,
            "reason": "other",
        },
    )
    assert r1.status_code == 422
    assert r1.json()["error"]["code"] == "VALIDATION_ERROR"
    # 自分が非参加のlatch(h2+h3)へ h1 が通報 → 422
    latch_no_me = await _latch(db_engine, [i2["id"], i3["id"]])
    r2 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={
            "reportee_id": h2["id"],
            "latch_id": latch_no_me,
            "reason": "other",
        },
    )
    assert r2.status_code == 422
    # 正常系: 自分+reporteeとも参加 → 201
    r3 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={
            "reportee_id": h2["id"],
            "latch_id": latch_ok,
            "reason": "suspected_impersonation",
        },
    )
    assert r3.status_code == 201, r3.text
```

- [ ] **Step 2: 収集検証(実行しない・§0)**

Run: `cd backend && uv run pytest tests/integration/test_safety_api.py --collect-only -q`
Expected: exit 0・**9 collected**(`test_1_block_register_list_unblock_cycle` 〜 `test_9_reports_latch_participation`。migrated_db fixtureは走らない)

- [ ] **Step 3: 書いたコードの自己見直し(ws-3教訓・collect-onlyしか実行できないため)**

次を確認して壊れていれば直す(直した場合は報告書の補足へ記録):
- ヘルパーの戻り値の型: `_user` はdict(`["id"]` はstr・`["headers"]` はdict)・`_intent` はdict(`["id"]` はstr)・`_latch` はstr
- DB bindのUUID変換: `ANY(CAST(:ids AS uuid[]))` へは `_uuids()` で `uuid.UUID` へ変換して渡す(ws-4の_latchと同型)・単一CASTはstrのまま可
- cursor改頁の比較: `body1["items"] + body2["items"]` はリスト連結・`set(targets)` との比較はstr集合
- redis_client fixtureは `decode_responses=True`(existsはintを返す)
- latch_status_events.user_id はUUID(asyncpg)→ `uuid_mod.UUID(h1["id"])` と比較
- teardownのusers削除前にRedis掃除(id一覧が要るため順序を守る)

- [ ] **Step 4: lint・unit再確認してコミット**

Run: `make lint && make test`
Expected: グリーン(unit 1187件相当。integrationはdeselected)

```bash
git add backend/tests/integration/test_safety_api.py
git commit -m "test: add safety integration suite (9 cases, supervisor-run) (M3 ws-5)"
```

### Task 8: 全体検証・報告ファイル

**Files:**
- Create: `docs/plans/M3/ws-5-report.md`

- [ ] **Step 1: 完了条件1〜6を検証(§6の検証コマンドを順に実行)**

```bash
make lint && make test                       # 期待: exit 0・1187 passed
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d   # 期待: 空
cd backend && uv run alembic heads           # 期待: 0006 (head) 単一(不変)
git diff --name-only main -- backend/alembic # 期待: 空(出力なし)
git diff --name-only main | sort             # 期待: §4の一覧と一致
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src
                                             # 期待: core/clock.py の行のみ
cd backend && uv run pytest tests/integration/test_safety_api.py --collect-only -q
                                             # 期待: 9 collected
cd backend && uv run pytest tests/integration/test_chat_attendance_api.py --collect-only -q
                                             # 期待: 17 collected(Task 6の変更が収集を壊していないこと)
```

§6の完了条件7(test-ci)は**実行しない**。報告書に「スーパーバイザー検証待ち」と記録する(§0規律)。

- [ ] **Step 2: 報告ファイルを作成**

`docs/plans/M3/ws-5-report.md` を§7の形式どおり作成(検証コマンドの出力末尾を証拠として貼る)。

- [ ] **Step 3: コミット**

```bash
git add docs/plans/M3/ws-5-report.md
git commit -m "docs: add M3 ws-5 execution report (M3 ws-5)"
```

- [ ] **Step 4: 最終確認**

```bash
git status --short          # 期待: 空
git log --oneline main..HEAD  # Task 1〜8のコミット一覧
```

---

## 9. 計画書セルフレビュー(機械チェック・スーパーバイザー指示による。2026-10-01実施)

### 9-1. 新規テストファイルのbasename衝突(運用ルール5)

確認コマンド(実行済み・main時点・既存121ファイル):

```bash
cd backend && find tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
# → 空(design §4.3と一致)
```

**design §4.1のファイル名の検証結果**:

| design §4.1の名前 | 既存 | 判定 |
|---|---|---|
| `test_cache.py` | なし | そのまま可 |
| `test_store_sql.py` | `tests/unit/intents/test_store_sql.py` | **衝突** |
| `test_service.py` | `tests/unit/intents/test_service.py` | **衝突** |
| `test_routes.py` | `tests/unit/auth/test_routes.py` | **衝突** |
| `test_safety_api.py` | なし | そのまま可 |

よって本計画は**4ファイルとも `test_safety_` 接頭辞付き**とする(`test_safety_cache.py`・`test_safety_store_sql.py`・`test_safety_service.py`・`test_safety_routes.py`・`test_safety_api.py` — cache/apiは対称性のため統一)。design §4.1の**分割意図は不変**・ファイル名のみ適合措置(報告書に記録)。

### 9-2. ピン試験への追随访問(触る全ピン試験を挙げて影響有無を明記)

| ピン試験 | 触るか | 影響 |
|---|---|---|
| `tests/unit/test_rate_limit_wiring.py` | **追記する**(Task 5) | Counter期待値へ3エントリ(`/v1/reports`: 1・`/v1/users/me/blocks`: 1・`/v1/users/{user_id}/block`: 2=POST+DELETE)。機械的・ルート列挙のアルファベット順該当箇所。ws-6/ws-7も追記し得るが両側保持マージで解消(§0) |
| `tests/unit/latches/test_chat_service.py` | **追記する**(Task 6) | design §4.1の指示どおり`block_cache`注入時の経路分岐1件。既存13件は`LatchesService(clock, engine)`構築のままselect_block_betweenモックで動くため無傷(引数追加はデフォルトNone) |
| `tests/integration/test_chat_attendance_api.py` | **最小変更する**(Task 6・§4) | `_block`ヘルパー(DB直接INSERT)へ`redis_client`引数と`blk:u:`手動DEL追加・試験6へfixture引数追加。**api常設がキャッシュ経由で判定するようになった後も試験6の409期待を保つための整合**(収集17件は不変) |
| `tests/unit/latches/test_latches_service.py` | 触らない | 無傷。`LatchesService.__init__`への`block_cache=None`追加はキーワード引数デフォルトのみ。既存メソッドは参照しない |
| `tests/unit/latches/test_latches_routes.py` | 触らない | 無傷。StubServiceは`send_message`を呼ばない。create_appの引数追加もデフォルトで無影響 |
| `tests/unit/latches/test_latches_store_sql.py` | 触らない | **無傷**。`latches/store.py`は本単位で**完全無変更**(select_block_between残置)のため走査対象も不変 |
| `tests/unit/test_app_health.py`・`tests/unit/test_settings.py`・`tests/unit/llm/test_llm_factory.py` | 触らない | 無傷。settings・依存・LLM Gateway無接触。create_app素呼びはlifespan非実行 |
| `tests/unit/test_arch_no_direct_time.py` | 触らない | 無傷。追加コードはClock経由(FakeClock/`self._clock.now()`)のみ。SystemClockはintegration(test_safety_api.py)のヘルパーのみで既存ws-4資産と同一扱い |
| `tests/integration/test_schema.py` | 触らない | 無傷。マイグレーション追加なし(design §4.3) |
| `tests/integration/test_latches_api.py` | 触らない | 無傷。latches経路に変更なし |

### 9-3. DB残存干渉の対抗策(共有ci-db)

- **subjectプレフィックス**: `m3ws5-`(field fixtureが試験ごとに `m3ws5-{uuid8}-` を生成・ws-4の `m3ws4-` と衝突しない)
- **teardownはFK順+本単位テーブル+Redis**(Task 7のfield fixture): reports → messages → latch_status_events → calibration_records → notifications → latches → match_candidates → group_candidates → match_events → **blocks** → intents → users。**users削除前にid一覧を引いて `blk:u:{id}` をRedisからDELETE**(design §4.2・§7の残存確認SQLと対)
- **reportsはlatchesのFKを持つため最初に削除**(latches→の順序依存を解消)
- **時間値はすべてnow相対**(タイムボム回避・ws-1のtest_k_limits教訓): `_latch` のresponse_deadline/expires_atは `SystemClock().now()` 起点。固定時刻リテラルは置かない(unit側のFakeClock基準NOWはfakeのため対象外)
- **head不変(0006)**のため運用ルール1の「番号取り合い」は発生しないが、ws-6∥ws-7とのtest-ci同時実行は§0規律(実行しない)で回避する

### 9-4. 試験数の整合

| 種別 | main基準(2026-10-01スーパーバイザー指示値) | 本単位増分 | 期待合計 |
|---|---|---|---|
| unit(`make test`) | 1148 passed | **+39**(test_safety_cache.py 7=Task 2・test_safety_store_sql.py 8=Task 1・test_safety_service.py 16=Task 3の13+Task 4の3・test_safety_routes.py 7=Task 5・test_chat_service.py追記 1=Task 6。test_rate_limit_wiringは追記のみで件数不変) | **1187 passed** |
| test-ci(unit+integration) | 1378 passed | **+48**(unit 39+integration 9) | **1426 passed**(スーパーバイザー検証時・並走単位分は別加算) |

### 9-5. スペックカバレッジ・型整合(design §1.2の確定値→タスク対応・書き起こし後の点検)

- 引用#1(双方向判定・キャッシュ参照・反映はキャッシュ経由・解除の遡及なし)→ Task 2 cache+Task 7試験4・5・Task 3 unblock
- 引用#2(通報の受付・記録のみ・選択式4値)→ Task 4 service+Task 7試験8・routesの `ReportReason` Literal(Task 5)
- 引用#3・#4(D-23の3効果・matchedはcancelled化しない)→ Task 3 `_cancel_open_latches`+Task 7試験6・7。①Layer 1除外はworker無変更(承認①)・③読取専用化はws-4の送信判定+キャッシュ
- 引用#5・#6・#16(blocks/reportsテーブル既存・UNIQUEなし・status値域M3)→ Task 1 SQL・`REPORT_STATUS_PENDING`(Task 4)・`REPORT_REASONS`。DB CHECK/UNIQUE追加なし
- 引用#7・#8(API表・改頁共通規定)→ Task 5 routes・Task 1 `_SELECT_BLOCKS_PAGE`・Task 7試験1・3
- 引用#9(409 CHAT_READONLY・閲覧可)→ Task 6差し替え後もws-4実装のまま(Task 7試験4の送信409)
- 引用#10(Redis用途4つの一つ)→ Task 2 `blk:` 接頭辞(auth:/rl:と分離)
- 引用#11(Intentはactiveのまま)→ `_cancel_open_latches` がlatchesのみ触れる+Task 7試験6のDISTINCT statusピン
- 引用#12(イベント同時挿入・user_id=引き金)→ Task 3の `insert_latch_event(conn, latch_id, from_status, "cancelled", me, now)`(同一tx内)+Task 7試験6
- 引用#13(不成立理由の一文統一・事実開示禁止)→ cancelled化の通知なし(notifications無接触)+Task 7試験6のnotifications 0件ピン
- 引用#14(認証済みのみ・サーバ側強制)→ `require_authenticated`+`api_rate_limited`(Task 5)・判定はすべてサーバ側
- 引用#15(専用codeなし)→ Task 1 errors 3クラス(404/422/503のみ)
- 引用#17(競合クローズ資産の再利用)→ `cancel_latch`・`insert_latch_event` をlatches.storeから再利用・`_SELECT_OPEN_LATCHES_BETWEEN` は `_SELECT_CONFLICTING_LATCHES` と同型(ORDER BY id FOR UPDATE)
- 引用#18(クローズ検知drain)→ **本単位はイベント挿入まで**(worker無変更・§5)
- design §2.6(失効リスト不使用)→ 実装なし(承認④)・報告書の引継ぎに記録
- design §2.7(マイグレーションなし)→ §5禁止・§6完了条件3
- 型整合点検: `BlockRow(id, blocked_id, display_name, created_at)`(Task 1)はTask 3 `list_blocks` の戻り要素・Task 5 `BlockOut`(from_attributes)と一致。`is_blocked_between(me: uuid.UUID, others: list[uuid.UUID]) -> bool`(Task 2)はTask 6 send_messageの呼び出し(`user_id, others`)と一致。`block_user(...) -> uuid.UUID`(Task 3)はTask 5 `BlockResponse(blocked_id=blocked_id)` と一致。`insert_report` の `status: str` に `REPORT_STATUS_PENDING`(="pending")を渡す(Task 4)・`reason: str` にroutesのLiteral値が入る。cursor `(datetime, uuid.UUID)` は `_SELECT_BLOCKS_PAGE` のbind(`:ct`・`:bid`)と一致。`_user` 戻りdictの `["id"]` はstr(POST /v1/users応答のUUIDは文字列化される)・DB bind時に必要な箇所は `_uuids()` で変換(Task 7)。プレースホルダ(TBD/「適宜」等)なし — 全ステップに実コードを記載済み

### 9-6. 未解決論点の確認

design §5の5件はすべて2026-10-01スーパーバイザー承認済み・G3時確認事項への登録済み(§Spec)。本計画に落とし込めない未解決論点は**ない**。BLOCKED事項なし。

計画書で確定した適合措置は2件(いずれもdesignの意図を保ったままの機械的調整・報告書に記録):
1. unit試験ファイル名の `test_safety_` 接頭辞化(§9-1・basename一意規律)
2. `test_chat_attendance_api.py` の `_block` ヘルパーへの `blk:u:` 手動DEL追加(§4・キャッシュ差し替え後の既存試験保護)

