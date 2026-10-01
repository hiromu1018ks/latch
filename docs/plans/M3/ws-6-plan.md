# M3 ws-6(削除・退会)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FR-19/FR-22の削除範囲を物理削除で実装し、D-13第一段(calibration匿名化のID系NULL化)を置く — ①削除カスケード実体 `intents/deletion.py`(1対1候補DELETE→group FK解消→group候補DELETE→latchesクローズ〔FR-19〕→calibration匿名化→Intent行DELETE)②単発削除はEvent経由を維持したままstage1の処理を物理削除化+DELETE APIの受理statusを全statusへ拡張(matched受理=FR-19のAPI経路開通)③退会API `DELETE /v1/users/me`(全Intent処理+messages/notifications削除+表示名置換+auth_subject切替を1トランザクションで同期実行)④30日定期削除 `worker/retention.py`(RetentionJob・ResetJob同型のJST 0時発火)⑤blocks/reports/match_eventsは残置。**マイグレーション追加なし(head=0006不変)・依存追加なし・設定追加なし**。

**Architecture:** design §2.1〜§2.7 — 削除の実体は `cascade_delete_intent` 1本に一元化(intents配下・intents.serviceへのimportは持たない)。呼び出し側はトランザクションを開いて渡す(関数自身はtxを開かない・C9と同じ寿命)。単発削除はws-1 §2.7の承認済み構成(APIはcancelled遷移+EVENT_DELETED発行→stage1が処理)を維持し、stage1のEVENT_DELETED処理だけclosed化からcascade呼び出しへ差し替える。退会は同期tx内で全カスケード+ユーザー単位処理まで実行(応答204の時点で生データが消える・Worker稼働状態に非依存)、コミット後に `revoke_access`+`revoke_family`(Redis失効リスト)。退会後のusers行は残置し `auth_subject='deleted:'||id` へ書き換える(FK的に物理削除不能・authの照合SQL無変更で再ログインは新規扱い)。表示名置換はusers行のUPDATEで達成(latches詳細APIは `users.display_name` をjoin参照〔`latches/store.py:255`〕のため全latchesへ自動反映)。

**Tech Stack:** 変更なし(Python 3.13 / FastAPI / SQLAlchemy[asyncio]+asyncpg text()生SQL / redis.asyncio / pytest)。

**Spec:** `docs/plans/M3/ws-6-design.md`(agent1設計メモ。**未解決論点なし** — design §5の7件は実装時確認事項・docs改版候補として整理済みで、実装の確定値はdesign §2にある。design.mdが本計画より優先)。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m3-ws-6`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(**依存追加なし**のためlockは進まない)。
- **【最重要】共有ci-dbへの `make test-ci` / `make migrate` は禁止**(STATUS運用ルール1。マイグレーション追加はしないが、**ws-6∥ws-7並走時に備えた標準規律**として運用する)。開発は**unit試験(`make test`)のみ**で進める。integration試験ファイル(§4の3ファイル)は**作成するが実行しない** — インポートエラー検出のため `--collect-only` を実行してよい(conftestのimport時副作用はenv設定のみでDB接続しない)。報告ファイルには「**test-ci=スーパーバイザー検証待ち**」と記録する。`docker compose build api` を含めcompose系コマンド(docker build/pull/up/down/migrate相当)は一切実行しない(**docker系コマンド不要**)。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-10-01)**: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空。design §3.1の新規6テストファイル(test_deletion・test_worker_retention・test_account_delete・test_deletion_api・test_account_api・test_retention_job)は**いずれも既存と衝突しない**(§9-1の表)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **マイグレーション追加なし・依存追加なし・設定追加なし**: `backend/alembic/` は一切触らない(head=0006不変)・`backend/pyproject.toml`・`backend/uv.lock`・`Makefile`・`.env`・`.env.example`・`settings.py` に触れない。RetentionJobの保持期間30日・batch_limit 500・retry_sec 300は**モジュール定数・デフォルト引数**とする(設定追加の代替)。
- **ws-7(フロントエンド+通報契約の付帯変更)が並行で実行される**: ws-7は `safety/routes.py`・`safety/service.py` への小変更(reportee_id省略可)。本単位は**safety/へ一切触れない**(design §3.3)ため競合なし。`tests/unit/test_rate_limit_wiring.py` は本単位が `/v1/users/me` の期待値を 1→2 へ書き換える(機械的・ws-7はルート追加なしのため干渉なし)。マージはスーパーバイザーが行うため、worktree内で他単位の変更が見えなくても気にしない。
- **integration試験コードを書いた後は「ヘルパーの戻り値unpack・引数形式・比較の型」を自分で見直すこと**(ws-3の教訓・スーパーバイザー指示)。特に: `_user` 戻り値はdict(`["id"]` はstr)・DB直接SELECTのUUID列は `str(r[0])` で比較・jsonb列は `json.loads` 相当のdictとして返る場合とstrで返る場合がある(`fetch_by_auth` の前例 — isinstance検査を入れる)・`rowcount` はFakeResultスタブと実DBの両方にある。
- **固定値の遵守**: design §2 の採用判断(案A・SQL・手順)と本計画§2のグローバル制約は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `refactor:` 等)。コミットメッセージの末尾に `Co-Authored-By: Claude Code <noreply@anthropic.com>` 行を付ける。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| ユーザーによるIntent削除・退会時の削除範囲: ①該当Intent行(raw_text・structured_data・embeddingを含む) ②当該Intentを含むすべてのmatch_candidates・group_candidates(**処理済みを含む**。evaluated・skipped・closedを問わず削除) ③当該Intentを含む回答待ちのlatches(cancelledで閉じる) ④当該ユーザーのmessages・notifications ⑤calibration_records該当行の匿名化 ⑥退会時は上記全てに加え、関与したlatches内の表示名を「退会したユーザー」へ置換(相手側の履歴は残すが個人との紐付きを切る) | 08 §2.5・design引用#1 |
| FR-19: matched済みLATCHの参加Intentが削除された場合も当該LATCHをcancelledで閉じる。残った参加者への表示は「この提案は成立しませんでした」の一文のみ。D-09の通知はcancelled LATCHには送らない。退会と単発のIntent削除で扱いは一致。解散時に残る参加Intentはexpires_at経過済みならexpired・経過前ならactiveへ復帰し再提案可能 | 08 §2.5・design引用#2 |
| FR-22: match_candidates・group_candidatesのレコードは**評価確定から30日で定期削除**(削除要求がない場合も)。削除されたユーザーの条件由来の判定値が相手側レコードに恒久残存する経路を断つ | 08 §2.5・design引用#3 |
| D-13第一段: 匿名化は月次バッチ(第二段)とし、退会・削除時点でID系(user_id・latch_id・intent_ids)をNULL化する(第一段)。anonymized_atはバッチ実行時に立てる(CHECK「anonymized_at NOT NULLならID系NULL」と矛盾しない=第一段ではNULLのまま) | 08 D-13・design引用#4・#6 |
| calibration_records: actual_responsesは「匿名化でuser_idを除去し、回答種別と時刻は残す」。latch_id・intent_idsはNULLへ。prediction・proposal_snapshot・matched等は触れない(粒度低下は第二段) | 05 §2・design §2.6 |
| latches・responses・messagesはユーザーの履歴として維持(退会時の表示名置換・ID除去は適用する) | 08 §2.5・design引用#7 |
| LATCH遷移: matched→cancelled(参加Intentの削除)。Intent遷移: matched→active/matched→expired(参加Intentを含むLATCHが解散した場合。expires_at経過済みならexpired・経過前ならactive) | 05 §6・design引用#8・#9 |
| 削除済みIntentへの参照Eventは正当な遅延Eventとしてstatus=processedで破棄し(理由をpayloadに記録)、quarantinedにはしない。match_events.source_intent_idはFKなし | 05 §2・06 §9・design引用#10 |
| D-09実施自己申告の通知はcancelled LATCHには送らない(構造担保: sweeperのcompleted化はstatus='matched'行のみ) — **確認のみ・実装変更なし** | 09 §2.2・design引用#11・`worker/sweeper.py:297` |
| セッションの失効(退会の即時反映)はRedisの失効リストで行う | 08 §5.3・design引用#13 |
| 単発削除はEvent経由を維持(ws-1 §2.7承認済み構成)。受理statusは全status(draft/active/paused/matched/expired/cancelled)へ拡張。cancelled遷移は「Event処理までの一時標識」として残す(version不変) | design §2.2・引用#14 |
| 退会API `DELETE /v1/users/me`: request {}・response 204・認証はAuthorizationヘッダーのJWT(05 §5への規定追加はdocs改版候補) | design §2.3・#15 |
| 退会後のusers行: 残置+`auth_subject='deleted:'||id` 切替(マイグレーションなし)。退会済みJWTは失効リストで401・同一IdP再ログインは新規ユーザー扱い | design §2.4 |
| 30日定期削除: 対象は両候補テーブルの全status一律で `updated_at < now-30日`(pendingも含む — 起点Intentは確実に失効)。latchesは削除しない(履歴維持)。latches.group_candidate_idのNULL化はgroup_candidates削除より前(FK RESTRICT) | design §2.5・引用#3・#18 |
| blocks・reports・match_events・users残存列(birth_date等)・latches.responses内user_id: **すべて残置**(design §2.7の根拠どおり) | design §2.7・引用#16 |
| FKの連鎖(いずれもON DELETE句なし=RESTRICT): match_candidates→intents・latches→group_candidates・calibration_records→latches。usersを参照するFK(blocks・reports・notifications・messages・intents.user_id) — 削除順序の制約になる | 0001・design引用#17・#18 |
| C8(Intent保存APIは同期LLM非依存)・C9(同一の直列化方式)・C2(Clock経由) | 12 §2 |

## 2. グローバル制約(全タスクに暗黙に適用・design §2の固定値)

- **`intents/deletion.py` の構成(design §2.1)**: SQL定数+`cascade_delete_intent(conn, intent_id: uuid.UUID, now)`+`close_latches_on_delete`(stage1から**中身不変**で移設)。**txを開かない**(呼び出し元のtxに乗る)。intents.serviceへのimportは持たない(users.serviceからの依存がunit试験で差し替え可能な理由)。SQLはdesign §2.1の全文どおり(`CAST(:intent_id AS uuid)` 形式)。削除順序はFK依存の固定順: ①`DELETE FROM match_candidates`(intent_a_id/intent_b_id一致・status条件なし=処理済み含む) ②`UPDATE latches SET group_candidate_id = NULL`(FK解消) ③`DELETE FROM group_candidates`(intent_ids ANY一致) ④`close_latches_on_delete`(FR-19) ⑤`UPDATE calibration_records`(匿名化・`latch_id IS NOT NULL`の冪等ガード) ⑥`DELETE FROM intents`。
- **`_ANONYMIZE_CALIBRATION` の変換(design §2.6)**: `latch_id=NULL`・`intent_ids=NULL`・actual_responses各要素から `user_id` キーのみ除去(`e - 'user_id'`・WITH ORDINALITYで配列順保存・COALESCEで空配列化)・`updated_at=now`。anonymized_atはNULLのまま・prediction等は触らない。
- **stage1の差し替え(design §2.2)**: `_process_once` のEVENT_DELETED分岐は `await cascade_delete_intent(conn, intent_id, now)` の1行へ。`_CLOSE_CANDIDATES`/`_CLOSE_GROUPS` は削除。`close_latches_on_delete` とSQL定数6本(_SELECT_OPEN_LATCHES_ON_DELETE・_CANCEL_LATCH_ON_DELETE・_SELECT_MATCHED_LATCHES_ON_DELETE・_CANCEL_MATCHED_LATCH_ON_DELETE・_RESTORE_INTENTS_ON_DISSOLVE・_INSERT_LATCH_EVENT_SQL)はdeletion.pyへ移設(中身不変・`_coerce_uuid` は両モジュールに同じ定義を置く — auth/users/intents各所に重複ヘルパーがある既存規律)。
- **`intents/service.py` delete()の拡張(design §2.2)**: `allowed_from=("draft","active","paused","matched","expired","cancelled")`(全6status)。`new_status="cancelled"`・`version_delta=0`・`event_type=EVENT_DELETED` は不変。コメントを「Event処理(stage1)で物理削除(M3 ws-6)」へ更新。
- **`insert_match_event` へON CONFLICT DO NOTHING追加(design §2.2前提の適合・§9-6)**: 現行 `intents/events.py` の `_INSERT_EVENT` にはON CONFLICTがないため、cancelled済み行への再DELETE(冪等204)がUNIQUE索引 `ux_match_events_idempotency`(0001)違反→503になる。design §2.2「Event発行はinsert_match_eventのON CONFLICT DO NOTHINGで同一3点組の重複を挿入しない」の前提どおりへ変更する(1行追加)。
- **退会APIの手順(design §2.3・単一tx・この順)**: ①`fetch_by_auth`(行なし=UserNotFoundError 404 — get_meと同型) ②`SELECT id FROM intents WHERE user_id=... ORDER BY id`(全status) ③各行へ `cascade_delete_intent` ③'`DELETE FROM messages WHERE sender_id=...`(**sender分のみ**・他者分は履歴維持) ④`DELETE FROM notifications WHERE user_id=...` ⑤`UPDATE users SET display_name='退会したユーザー', profile='{}'::jsonb, auth_subject='deleted:'||id::text, updated_at=now` ⑥txコミット後(begood — uowのaexit後)に `sessions.revoke_access(jti, exp, now)` + `sessions.revoke_family(sid)`。Eventは発行しない。
- **UserServiceへのDI(design §2.3)**: `__init__` へ `uow: UnitOfWork | None = None`(engine.beginと同一署名)・`sessions=None`(revoke_access/revoke_familyを持つオブジェクト)・`cascade: CascadeFn | None = None`(Noneなら本物 `cascade_delete_intent` を使用)を**デフォルト付きキーワード引数**で追加(既存の直構築unit試験 `tests/unit/users/test_users_service.py:85` は無傷)。uow/sessionsがNoneのまま `delete_account` が呼ばれた場合は DependencyUnavailableError(503 — 失効しない退会は安全側でないため)。`make_user_service(*, clock, engine, sessions=None)` がuow=engine.begin・cascade=本物を束ねる。
- **users行のusers错误は既存のものだけ**: 404 UserNotFoundError(未登録JWT)・503 DependencyUnavailableError・401は認証Dependency。専用の新例外・新codeは作らない。
- **RetentionJob(design §2.5案A)**: `worker/retention.py` に新設。runループ・graceful shutdown・注入sleepは**ResetJobと同一契約**(`next_jst_midnight` は `worker/reset.py` からimport再利用)。run_onceは `cutoff=clock.now()-timedelta(days=30)`・batch_limit=500の窓で空になるまで(group削除は各周でlatches.group_candidate_id NULL化→group DELETE。matchは単独ループ)。戻り値は `(group削除件数, match削除件数)` のtuple・件数をINFOログへ。**engineのみで動く**(redis不要)ためworker/main.pyでは無条件構築(ResetJobのredis_client条件の外)。
- **worker/main.py**: RetentionJob構築+`asyncio.create_task(retention_job.run(stop=self._stop))`+終了待ちをResetJobと並んで追加。
- **main.py lifespan**: redis_client生成条件へ `or build_users` を追加し、`make_user_service(clock=..., engine=engine, sessions=SessionStore(redis_client))` を渡す(1行更新+import行)。users_service注入済みテスト(build_users=False)は無影響。
- **永続化はtext()生SQLのみ**・UUID復元は既存の `_coerce_*` 規律・uuid[]のbindは `list[uuid.UUID]`+SQL側 `CAST(:x AS uuid[])`。
- **時刻はClock経由のみ**(arch test強制)。RetentionJobのcutoffは `Clock.now()` の実時間(UTC)。integration試験の時間値はSystemClock相対(タイムボム回避)。
- **ログ**: okログは `users.delete_me ok` 等のイベント名+結果のみ。display_name・subject・uuid本文は出さない(08 §2.4)。RetentionJobは件数のみ。
- **latches/・worker/sweeper.py・worker/reset.py・worker/reeval.py・safety/・notifications/・worker/matching/・auth/service.py・auth/deps.py に触れない**(design §3.3・§5)。
- **`make lint`(ruff E,F,I,UP,B・format行長88)と `make test` を毎コミット通す**。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **処理済み候補の残存**(evaluated/skipped/closed行だけ閉じて残す実装 — FR-22違反) — 削除SQLのWHEREにstatus条件を書かなければ起きないが、旧 `_CLOSE_CANDIDATES` の `status <> 'closed'` を写してしまうと起きる → Task 1のSQLピン(WHERE句にstatusなし)+Task 6試験1(4status行の消滅)
2. **group FK解消忘れによるRESTRICT違反**(group_candidatesを先に消すと latches→group_candidates FKでエラー) → Task 1の順序ピン(calls[1]のlatches UPDATEがcalls[2]のgroup DELETEより前)+Task 5の順序ピン+Task 6試験1
3. **退会後の旧JWTが1時間有効**(署名検証はuser存在を見ないため失効させないと通る) — 合理な期待は「退会応答後すぐ旧JWTは401」 → Task 3のunit(revoke_access+revoke_familyの呼び出し・txコミット後)+Task 6 test_account_api試験4
4. **matched IntentのDELETEが422のまま**(FR-19のAPI経路不通) → Task 2のunit(matched/expired/cancelled受理)+Task 6試験2
5. **calibration匿名化の漏れ・過剰**(anonymized_atを立ててしまう/回答種別・時刻まで消す/配列順が崩れる) → Task 1のSQLピン(`latch_id IS NOT NULL`・`e - 'user_id'`・WITH ORDINALITY・anonymized_at不在)+Task 6試験1(actual_responsesのuser_id除去・順序保存・answered_at保存)

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1):

```text
backend/src/latch/intents/deletion.py               (Task 1。カスケード実体+close_latches_on_delete移設)
backend/src/latch/worker/retention.py               (Task 5。RetentionJob)
backend/tests/unit/intents/test_deletion.py         (Task 1。6件)
backend/tests/unit/test_worker_retention.py         (Task 5。5件)
backend/tests/unit/users/test_account_delete.py     (Task 3。5件)
backend/tests/integration/test_deletion_api.py      (Task 6。7件)
backend/tests/integration/test_account_api.py       (Task 6。6件)
backend/tests/integration/test_retention_job.py     (Task 6。2件)
docs/plans/M3/ws-6-report.md                        (Task 7。報告ファイル)
```

変更(design §3.2):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/worker/stage1.py` | EVENT_DELETED分岐を `cascade_delete_intent` 呼び出しへ差し替え。`_CLOSE_CANDIDATES`/`_CLOSE_GROUPS` 削除。`close_latches_on_delete` とSQL定数6本をdeletion.pyへ移設(削除)。モジュールdocstringの該当行を更新 | 1 |
| `backend/tests/unit/test_worker_stage1.py` | 削除Event 2試験(336行・362行)を物理削除期待へ書き換え(機械的追従・件数不変) | 1 |
| `backend/src/latch/intents/service.py` | `delete()` のallowed_fromを全statusへ拡張+コメント更新 | 2 |
| `backend/src/latch/intents/events.py` | `_INSERT_EVENT` へ `ON CONFLICT DO NOTHING` 追加(design §2.2前提適合・§9-6) | 2 |
| `backend/tests/unit/intents/test_intents_service.py` | 2試験(matched拒否・cancelled再削除拒否)を受理期待へ反転+expired受理の追試験1件 | 2 |
| `backend/tests/unit/intents/test_events.py` | ON CONFLICTピン1件追記 | 2 |
| `backend/src/latch/users/service.py` | `delete_account` 追記・DI(uow/sessions/cascade)・SQL定数4本・型エイリアス | 3 |
| `backend/src/latch/users/routes.py` | `DELETE /v1/users/me` エンドポイント追記 | 4 |
| `backend/src/latch/main.py` | lifespanのredis_client条件へbuild_users追加・`make_user_service` へSessionStore渡し・import | 4 |
| `backend/tests/unit/test_rate_limit_wiring.py` | 期待Counter `/v1/users/me`: 1→2(機械的・§0) | 4 |
| `backend/tests/unit/users/test_users_routes.py` | StubUserServiceへdelete_account追記+204試験1件 | 4 |
| `backend/src/latch/worker/main.py` | RetentionJobの構築+task起動+終了待ち+import | 5 |
| `backend/tests/integration/test_matching_groupengine.py` | test_8(901行)を物理削除期待へ書き換え(機械的追従・件数不変) | 6 |

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`。

**スコープ外と判断する基準(必要になったと感じても作らない — design §1.4・§2.7・§2.8)**:

- D-13第二段(月次バッチ: 時刻の日単位丸め・proposal_snapshot粒度低下・anonymized_at設定) → 運用設計(08 §6)
- 「この提案は成立しませんでした」の一文表示・「退会したユーザー」表示のフロント側 → ws-7/ws-8
- match_eventsの定期削除・容量管理・updated_at部分Index → 将来課題(design §5-6/7)
- 通報への運用対応(警告・アカウント停止)・退会時のblocks行削除・BlockCacheの明示DEL → M4+(§2.7残置)
- latches・messages(他人分)・latch_status_events・users残存列(birth_date等)の削除・ダミー値化 → 履歴維持・design §5-3
- 退会の非同期化(進捗API)・削除完了通知・deleted_at列(マイグレーション0007)・ friendships → design §2.8

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- **docker/compose系の全コマンド**: `make up` / `make down` / `make test-ci` / `make migrate` / `make geo-*` / `make g1-gate` / `make g2-gate` / `make embed-smoke` / `make jev-smoke` / `docker compose build api` / `docker build` / `docker pull` — §0のとおり共有ci-dbを消費してはならない。検証はスーパーバイザーが実施する
- **design §3.3の禁止(無変更ファイル)**:
  - `backend/src/latch/safety/` 一式(**ws-7が並行で変更する・競合回避**)・`backend/tests/integration/test_safety_api.py`
  - `backend/src/latch/latches/`(store・service・routes・schemas・calibration — 表示名置換はusers行で自動・D-09除外はsweeper構造担保済み)
  - `backend/src/latch/worker/sweeper.py`・`worker/reset.py`(next_jst_midnightの**importのみ可**)・`worker/reeval.py`・`worker/debounce.py`・`worker/backfill.py`・`worker/cost.py`・`worker/jev.py`・`worker/matching/` 一式
  - `backend/src/latch/auth/service.py`・`auth/deps.py`・`auth/routes.py`(auth_subject切替は照合変更不要)・**`auth/sessions.py` は読み取りのみ・変更禁止**
  - `backend/src/latch/notifications/`・`geo/`・`core/`・`events/`・`llm/`・`ratelimit/` 各モジュール
  - `backend/alembic/`(0001〜0006とも無変更・追加もしない)
  - `backend/tests/integration/conftest.py`・`backend/tests/conftest.py`(新規ファイルから利用するのみ)
  - `backend/tests/` の§4に列挙した以外の既存試験ファイル。特に `test_users_service.py`(直構築のまま無傷)・`test_intents_crud_api.py`・`test_latches_api.py`・`test_expiry_batches.py`・`test_app_health.py`・`test_arch_no_direct_time.py`・`test_settings.py` は**一切触らない**(§9-2の追随访問)
  - `compose.yaml`・`docker/`・`frontend/`・`prototype/`・`backend/pyproject.toml`・`backend/uv.lock`・`Makefile`・`.env`・`.env.example`・`src/latch/settings.py`
  - `docs/`(01〜12・learn・testassets)・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/` の既存ファイル(M0〜M3のdesign・plan・report)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 7で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(新規/追記unit 19件込み)
   検証: `make lint && make test` — ともにexit 0。**期待件数: 1207 passed**(main 1188 + 本単位unit 19 = test_deletion.py 6〔Task 1〕+test_intents_service.py 1〔Task 2・2件は書き換えで件数不変〕+test_events.py 1〔Task 2〕+test_account_delete.py 5〔Task 3〕+test_users_routes.py 1〔Task 4〕+test_worker_retention.py 5〔Task 5〕。test_worker_stage1.py 2件・test_rate_limit_wiring.py は書き換えのみで件数不変)。全件数を報告書に記録
2. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `cd backend && find tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
3. **マイグレーション無変更(alembic head=0006不変)**
   検証: `cd backend && uv run alembic heads` が単一head・`git diff --name-only main -- backend/alembic` が**空**(1行も出ない)
4. **変更ファイルが§4の一覧どおり(作成9=実装2+テスト6+報告1・変更13=合計22ファイル)**
   検証: Task 7の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
5. **実時間参照が `core/clock.py` のみ**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
6. **integration試験ファイルが収集可能(実行はスーパーバイザー検証)**
   検証: `cd backend && uv run pytest tests/integration/test_deletion_api.py tests/integration/test_account_api.py tests/integration/test_retention_job.py --collect-only -q` がexit 0で**15件**を収集。`uv run pytest tests/integration/test_matching_groupengine.py --collect-only -q` が変更前と同一件数(test_8の書き換えで件数不変であること)
7. **test-ci=スーパーバイザー検証待ち**(§0規律・本単位は実行しない)。スーパーバイザー検証時の期待: `docker compose build api` → `make test-ci` がグリーン・**期待件数: 1461 passed**(main 1427 + unit 19 + integration 15)・alembic head=0006・m3ws6-残存ゼロ(§7のSQL)

## 7. 報告形式

**結果ファイル**: `docs/plans/M3/ws-6-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M3 ws-6(削除・退会)実行報告

- ブランチ: m3-ws-6 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も・期待1207)> |
| 2 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 3 | alembic head=0006不変 | PASS/FAIL | <alembic heads 出力 + git diff --name-only main -- backend/alembic(空)> |
| 4 | 変更ファイル=§4の21ファイル | PASS/FAIL | <git diff --name-only main出力> |
| 5 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 6 | integration収集15件+groupengine件数不変 | PASS/FAIL | <pytest --collect-only -q 末尾×2> |
| 7 | test-ci | **スーパーバイザー検証待ち** | <実施せず(§0規律)。期待1461 passed・head=0006・残存ゼロ> |

## design §5 実装時確認事項の結果
- (design §5の7件はG3時確認・docs改版候補として登録済み。下の変更有無へ実差分を記録)

## 固定値の変更有無(design.md §2・本計画§2)
- insert_match_eventへON CONFLICT DO NOTHING追加(design §2.2の前提適合・§9-6): 変更あり(計画時点で確定済み)
- 退会2回目呼び出しの応答は404 NOT_FOUND(未登録JWTと同型・design §2.3の冪等記述は削除操作の冪等性): 変更なし/変更あり
- カスケードSQL・順序・退会手順・RetentionJob構成(§2): 変更なし / 変更あり(<前→後+理由>)
- その他: 変更なし / 変更あり(<前→後+理由>)

## (スーパーバイザー・G3・ws-8/ws-9への引継ぎ)
- docs改版候補(design §5): ①05 §6への削除受理status明記 ②05 §5へのDELETE /v1/users/me規定追加
  (404 NOT_FOUND〔未登録JWT〕も明記) ③users残存列(birth_date)のオーナー確認 ④auth_subject='deleted:'暗黙契約
  ⑤単発削除の完了がEvent処理依存(通常数秒) ⑥match_events恒久蓄積 ⑦updated_at部分Index
- ws-8: 不成立表示「この提案は成立しませんでした」・ブロック管理画面の退会者表示はusers行置換で自動参照
- test-ci検証時の残存確認SQL(§9-3): SELECT count(*) FROM users WHERE auth_subject LIKE 'm3ws6-%' OR auth_subject LIKE 'deleted:%'
  → 期待0(teardownが退会済み行もid追跡で掃除)
```

---

## 8. 実装ステップ(TDD: 各タスクはテスト→実装→全緑→コミットの順)

### Task 1: 削除カスケード実体 intents/deletion.py + stage1差し替え

**Files:**
- Create: `backend/src/latch/intents/deletion.py`
- Modify: `backend/src/latch/worker/stage1.py`(EVENT_DELETED分岐・定数削除・docstring)
- Test: `backend/tests/unit/intents/test_deletion.py`(新規6件)
- Modify: `backend/tests/unit/test_worker_stage1.py`(2試験の書き換え・件数不変)

**Interfaces:**
- Consumes: なし(stage1の既存SQL定数を移設)
- Produces: `cascade_delete_intent(conn: AsyncConnection, intent_id: uuid.UUID, now: datetime) -> None`(Task 2〜3・6が使う。**txを開かない**)・`close_latches_on_delete(conn, intent_id, now) -> None`(deletion.py内でcascadeから呼ぶ。stage1のEVENT_DELETED処理はcascade経由となり直接は呼ばない)

- [ ] **Step 1: 失敗するunit試験を書く(test_deletion.py新規)**

`backend/tests/unit/intents/test_deletion.py` を作成(test_worker_stage1.pyのScriptedEngine流儀・ファイル内スタブ定義):

```python
"""削除カスケード(intents/deletion.py)のunit試験(M3 ws-6 design §2.1・§4.1)。

スタブconnでSQL実行順序(FK依存の固定順)とbindパラメータをピンする。
実DBでの削除結果(①〜⑥の検証)は integration/test_deletion_api.py が担う
(§9-6の適合措置 — unitはDBレス規律)。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from latch.intents import deletion

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
IID = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
OTHER = uuid.UUID("00000000-0000-4000-8000-0000000000cc")
LID = uuid.UUID("00000000-0000-4000-8000-0000000000dd")


class FakeResult:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class ScriptedConn:
    def __init__(self, results: list):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = list(results)

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return FakeResult(None, 0)


def _sql(conn, i: int) -> str:
    return conn.calls[i][0]


def _no_row_results() -> list:
    """cascade全体の応答列(候補・latches・calibrationすべて0行/0件)。"""
    return [
        FakeResult(None, 0),  # ① match_candidates DELETE
        FakeResult(None, 0),  # ② latches group_candidate_id NULL
        FakeResult(None, 0),  # ③ group_candidates DELETE
        FakeResult((), 0),    # ④ 開いているlatches SELECT(0行)
        FakeResult((), 0),    # ④ matched latches SELECT(0行)
        FakeResult(None, 0),  # ⑤ calibration匿名化
        FakeResult(None, 0),  # ⑥ intents DELETE
    ]


async def test_cascade_sql_order_and_params():
    """FK依存の固定順: ①候補→②latches FK解消→③group→④latches→⑤匿名化→⑥Intent。"""
    conn = ScriptedConn(_no_row_results())
    await deletion.cascade_delete_intent(conn, IID, NOW)
    # ① 処理済み含む全status(WHEREにstatus条件なし)
    sql1 = _sql(conn, 0)
    assert "DELETE FROM match_candidates" in sql1
    assert "intent_a_id = CAST(:intent_id AS uuid)" in sql1
    assert "intent_b_id = CAST(:intent_id AS uuid)" in sql1
    assert "status" not in sql1
    assert conn.calls[0][1] == {"intent_id": IID}
    # ② FK解消が③より前(RESTRICT回避)
    sql2 = _sql(conn, 1)
    assert "UPDATE latches" in sql2 and "group_candidate_id = NULL" in sql2
    assert "ANY(intent_ids)" in sql2
    # ③ group削除
    assert "DELETE FROM group_candidates" in _sql(conn, 2)
    # ⑤ 匿名化・⑥ Intent行(raw_text・embeddingごと)
    assert "UPDATE calibration_records" in _sql(conn, 5)
    assert "DELETE FROM intents" in _sql(conn, 6)
    assert conn.calls[6][1] == {"intent_id": IID}


async def test_cascade_matched_latch_dissolve_and_restore():
    """matched行→cancelled+イベント(user_id=NULL)+残存Intent復帰(削除対象を除く)。"""
    conn = ScriptedConn(
        [
            FakeResult(None, 0),  # match DELETE
            FakeResult(None, 0),  # detach
            FakeResult(None, 0),  # group DELETE
            FakeResult((), 0),    # open SELECT 0行
            FakeResult(((LID, (IID, OTHER)),), 0),  # matched SELECT 1行
            FakeResult((LID,), 1),  # cancel matched(RETURNING)
            FakeResult(None, 0),    # latch_status_events INSERT
            FakeResult((OTHER,), 1),  # 復帰 UPDATE RETURNING
            FakeResult(None, 0),  # calibration
            FakeResult(None, 0),  # intents DELETE
        ]
    )
    await deletion.cascade_delete_intent(conn, IID, NOW)
    assert "status = 'matched'" in _sql(conn, 4)  # 対象はmatched行のみ
    assert "'cancelled'" in _sql(conn, 5)
    ev = conn.calls[6][1]
    assert ev["from_status"] == "matched"
    assert ev["to_status"] == "cancelled"
    assert ev["user_id"] is None  # システム起因
    restore_sql = _sql(conn, 7)
    assert "CASE WHEN expires_at <= CAST(:now AS timestamptz)" in restore_sql
    assert conn.calls[7][1]["ids"] == [OTHER]  # 削除対象IIDは除外


async def test_cascade_open_latches_cancel_with_events():
    """開いているlatches(candidate/proposed/partial_accept)→cancelled+イベント。"""
    conn = ScriptedConn(
        [
            FakeResult(None, 0),
            FakeResult(None, 0),
            FakeResult(None, 0),
            FakeResult(((LID, "proposed"),), 0),  # open SELECT
            FakeResult((LID,), 1),                # cancel(RETURNING)
            FakeResult(None, 0),                  # latch_status_events INSERT
            FakeResult((), 0),                    # matched SELECT 0行
            FakeResult(None, 0),
            FakeResult(None, 0),
        ]
    )
    await deletion.cascade_delete_intent(conn, IID, NOW)
    assert "IN ('candidate', 'proposed', 'partial_accept')" in _sql(conn, 3)
    ev = conn.calls[5][1]
    assert ev["from_status"] == "proposed"
    assert ev["to_status"] == "cancelled"
    assert ev["user_id"] is None


async def test_cascade_idempotent_second_run_no_rows():
    """二重実行: 全操作が0行/空でも例外なく完了(design §2.2の冪等)。"""
    conn = ScriptedConn(_no_row_results() + _no_row_results())
    await deletion.cascade_delete_intent(conn, IID, NOW)
    await deletion.cascade_delete_intent(conn, IID, NOW)  # 例外なし
    assert len(conn.calls) == 14  # 2周×7本


async def test_anonymize_sql_pins():
    """D-13第一段のSQLピン: user_id除去・順序保存・冪等ガード・anonymized_at不変。"""
    sql = str(deletion._ANONYMIZE_CALIBRATION)
    assert "latch_id = NULL" in sql
    assert "intent_ids = NULL" in sql
    assert "e - 'user_id'" in sql
    assert "WITH ORDINALITY" in sql  # 配列順保存
    assert "jsonb_agg" in sql and "COALESCE" in sql
    assert "latch_id IS NOT NULL" in sql  # 匿名化済み行スキップ
    assert "= ANY(intent_ids)" in sql
    assert "anonymized_at" not in sql  # 第二段まで立てない


def test_all_sql_bind_params_recognized():
    """実dialectでcompileし未認識 ':name' が残らない(test_store_sql流儀)。"""
    names = (
        "_DELETE_MATCH_CANDIDATES",
        "_DETACH_GROUP_CANDIDATES",
        "_DELETE_GROUP_CANDIDATES",
        "_ANONYMIZE_CALIBRATION",
        "_DELETE_INTENT",
        "_SELECT_OPEN_LATCHES_ON_DELETE",
        "_CANCEL_LATCH_ON_DELETE",
        "_SELECT_MATCHED_LATCHES_ON_DELETE",
        "_CANCEL_MATCHED_LATCH_ON_DELETE",
        "_RESTORE_INTENTS_ON_DISSOLVE",
        "_INSERT_LATCH_EVENT_SQL",
    )
    keys = ("intent_id", "now", "latch_id", "from_status", "to_status", "user_id", "ids")
    for name in names:
        compiled = str(getattr(deletion, name).compile(dialect=postgresql.dialect()))
        for key in keys:
            assert f":{key}" not in compiled, (name, key)
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_deletion.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.intents.deletion'` 相当の収集エラー)

- [ ] **Step 3: deletion.pyを実装**

`backend/src/latch/intents/deletion.py` を作成(design §2.1のSQL全文。`close_latches_on_delete` 本体は `worker/stage1.py:455` から**中身不変**で移設):

```python
"""削除カスケードの実体(M3 ws-6 design §2.1・08 §2.5)。

単発Intent削除(stage1のEVENT_DELETED処理)と退会(users service)の両経路から
呼ばれる1Intent分の物理削除。intents配下だが intents.service へのimportは
持たない(users→worker依存を生まない)。関数自身はtxを開かず、呼び出し元の
トランザクションに乗る(C9の直列化方式と同じ寿命)。
削除順序はFK依存の固定順: ①1対1候補(処理済み含む全status・FR-22)
②latches.group_candidate_id NULL化(RESTRICT解消) ③group候補 ④latches
クローズ(FR-19・matched解散と残存Intent復帰) ⑤calibration匿名化
(D-13第一段) ⑥Intent行(raw_text・structured_data・embeddingごと)。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT/RETURNING結果のUUID列復元(pgproto.UUID対策 — stage1と同じ)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


_DELETE_MATCH_CANDIDATES = text("""
    DELETE FROM match_candidates
     WHERE intent_a_id = CAST(:intent_id AS uuid)
        OR intent_b_id = CAST(:intent_id AS uuid)
""")  # 全status・処理済み含む(FR-22・design引用#1-②)

_DETACH_GROUP_CANDIDATES = text("""
    UPDATE latches SET group_candidate_id = NULL
     WHERE group_candidate_id IN (
         SELECT id FROM group_candidates
          WHERE CAST(:intent_id AS uuid) = ANY(intent_ids))
""")  # latches→group_candidates FK(RESTRICT)の解消。
      # 閉じたlatchesのgroup_candidate_idは再評価で使わないためNULL化してよい

_DELETE_GROUP_CANDIDATES = text("""
    DELETE FROM group_candidates
     WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
""")

# D-13第一段(design §2.6): ID系NULL化+actual_responsesからuser_id除去。
# latch_id IS NOT NULL=未匿名化行のみ(冪等ガード)。anonymized_atは第二段。
_ANONYMIZE_CALIBRATION = text("""
    UPDATE calibration_records
       SET latch_id = NULL,
           intent_ids = NULL,
           actual_responses = COALESCE((
               SELECT jsonb_agg(e - 'user_id' ORDER BY ord)
                 FROM jsonb_array_elements(actual_responses)
                      WITH ORDINALITY AS t(e, ord)
           ), '[]'::jsonb),
           updated_at = CAST(:now AS timestamptz)
     WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
       AND latch_id IS NOT NULL
""")

_DELETE_INTENT = text("""
    DELETE FROM intents WHERE id = CAST(:intent_id AS uuid)
""")  # raw_text・structured_data・embedding(pgvector)ごと消える(引用#1-①)


async def cascade_delete_intent(
    conn: AsyncConnection, intent_id: uuid.UUID, now: datetime
) -> None:
    """1Intent分の削除カスケード。呼び出し元のtxに乗る(txは開かない)。"""
    await conn.execute(_DELETE_MATCH_CANDIDATES, {"intent_id": intent_id})
    await conn.execute(_DETACH_GROUP_CANDIDATES, {"intent_id": intent_id})
    await conn.execute(_DELETE_GROUP_CANDIDATES, {"intent_id": intent_id})
    await close_latches_on_delete(conn, intent_id, now)
    await conn.execute(
        _ANONYMIZE_CALIBRATION, {"intent_id": intent_id, "now": now}
    )
    await conn.execute(_DELETE_INTENT, {"intent_id": intent_id})


# -- 以下、worker/stage1.py から中身不変で移設(M3 ws-1 design §2.7・引用#19・#20) --

_SELECT_OPEN_LATCHES_ON_DELETE = text("""
    SELECT id, status FROM latches
    WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
      AND status IN ('candidate', 'proposed', 'partial_accept')
    ORDER BY id
    FOR UPDATE
""")
_CANCEL_LATCH_ON_DELETE = text("""
    UPDATE latches SET status = 'cancelled'
    WHERE id = CAST(:latch_id AS uuid)
      AND status IN ('candidate', 'proposed', 'partial_accept')
    RETURNING id
""")
_SELECT_MATCHED_LATCHES_ON_DELETE = text("""
    SELECT id, intent_ids FROM latches
    WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
      AND status = 'matched'
    ORDER BY id
    FOR UPDATE
""")
_CANCEL_MATCHED_LATCH_ON_DELETE = text("""
    UPDATE latches SET status = 'cancelled'
    WHERE id = CAST(:latch_id AS uuid) AND status = 'matched'
    RETURNING id
""")
# 解散時の残る参加Intent復帰(引用#9: expires_at経過→expired・それ以外→active)
_RESTORE_INTENTS_ON_DISSOLVE = text("""
    UPDATE intents
    SET status = CASE WHEN expires_at <= CAST(:now AS timestamptz)
                      THEN 'expired' ELSE 'active' END,
        updated_at = CAST(:now AS timestamptz)
    WHERE id = ANY(CAST(:ids AS uuid[])) AND status = 'matched'
    RETURNING id, status
""")
# latch_status_events挿入(latch_engine._INSERT_LATCH_EVENTと同一SQL)
_INSERT_LATCH_EVENT_SQL = text("""
    INSERT INTO latch_status_events
        (latch_id, from_status, to_status, user_id, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:from_status AS text),
            :to_status, CAST(:user_id AS uuid), CAST(:now AS timestamptz))
""")


async def close_latches_on_delete(
    conn: AsyncConnection, intent_id: uuid.UUID, now
) -> None:
    """削除Event処理のlatches波及(M3 ws-1 design §2.7・FR-19)。

    1) 開いているlatches(candidate/proposed/partial_accept)→cancelled+events
    2) matched行→cancelled(解散)+events+残る参加Intentの復帰
       (削除されたIntent自身はcascadeの⑥で消えるため対象外)。
    イベントのuser_idはNULL(システム起因)。
    """
    open_rows = (
        await conn.execute(_SELECT_OPEN_LATCHES_ON_DELETE, {"intent_id": intent_id})
    ).fetchall()
    for latch_id, from_status in open_rows:
        res = await conn.execute(
            _CANCEL_LATCH_ON_DELETE, {"latch_id": _coerce_uuid(latch_id)}
        )
        if res.first() is None:
            continue  # 同一tx内で他経路が閉じた(通常ない防御)
        await conn.execute(
            _INSERT_LATCH_EVENT_SQL,
            {
                "latch_id": latch_id,
                "from_status": from_status,
                "to_status": "cancelled",
                "user_id": None,
                "now": now,
            },
        )
    matched_rows = (
        await conn.execute(_SELECT_MATCHED_LATCHES_ON_DELETE, {"intent_id": intent_id})
    ).fetchall()
    for latch_id, intent_ids in matched_rows:
        res = await conn.execute(
            _CANCEL_MATCHED_LATCH_ON_DELETE, {"latch_id": _coerce_uuid(latch_id)}
        )
        if res.first() is None:
            continue
        await conn.execute(
            _INSERT_LATCH_EVENT_SQL,
            {
                "latch_id": latch_id,
                "from_status": "matched",
                "to_status": "cancelled",
                "user_id": None,
                "now": now,
            },
        )
        rest = [i for i in (_coerce_uuid(x) for x in intent_ids) if i != intent_id]
        if rest:
            await conn.execute(_RESTORE_INTENTS_ON_DISSOLVE, {"ids": rest, "now": now})
```

- [ ] **Step 4: test_deletion.py が通ることを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_deletion.py -v`
Expected: PASS 6件(この時点でstage1は未差し替え・`make test` 全体は既存どおり緑)

- [ ] **Step 5: test_worker_stage1.py の2試験を物理削除期待へ書き換え**

`backend/tests/unit/test_worker_stage1.py` の336行・362行の2試験を次の内容へ置き換える(応答列がcascadeの7本+processedへ増える):

```python
async def test_process_deleted_deletes_match_candidates():
    """deleted → match_candidates物理削除(08 §2.5・M3 ws-6・処理済み含む)。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 0),  # match_candidates DELETE(0件でも正常)
            FakeResult(None, 0),  # latches group_candidate_id NULL化
            FakeResult(None, 0),  # group_candidates DELETE
            FakeResult((), 0),    # 開いているlatches SELECT(0行)
            FakeResult((), 0),    # matched latches SELECT(0行)
            FakeResult(None, 0),  # calibration匿名化
            FakeResult(None, 0),  # intents DELETE
            FakeResult(None, 1),  # processed
        ]
    )
    await _stage1(engine).process(
        _event("deleted", IID, 1), ("deleted", IID, 1), ROW_ID
    )
    delete_sql = _sql(engine.conn, 2)
    assert "DELETE FROM match_candidates" in delete_sql
    assert "status" not in delete_sql  # 処理済みstatus問わず全行
    assert engine.conn.calls[2][1]["intent_id"] == IID
    assert "DELETE FROM intents" in _sql(engine.conn, 8)  # Intent行ごと


async def test_process_deleted_detaches_groups_before_delete():
    """deleted → group FK解消(latches NULL化)→group_candidates物理削除の順序。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 0),  # match_candidates DELETE
            FakeResult(None, 0),  # latches NULL化(本試験の主対象)
            FakeResult(None, 0),  # group_candidates DELETE
            FakeResult((), 0),    # 開いているlatches SELECT
            FakeResult((), 0),    # matched latches SELECT
            FakeResult(None, 0),  # calibration匿名化
            FakeResult(None, 0),  # intents DELETE
            FakeResult(None, 1),  # processed
        ]
    )
    await _stage1(engine).process(
        _event("deleted", IID, 1), ("deleted", IID, 1), ROW_ID
    )
    assert engine.begins == 1  # 同一トランザクション
    detach_sql = _sql(engine.conn, 3)
    assert "UPDATE latches" in detach_sql
    assert "group_candidate_id = NULL" in detach_sql
    assert "ANY(intent_ids)" in detach_sql
    delete_sql = _sql(engine.conn, 4)
    assert "DELETE FROM group_candidates" in delete_sql
    # 実行順序: calls[3]=latches NULL化 → calls[4]=group削除(RESTRICT回避)
    assert "DELETE FROM intents" in _sql(engine.conn, 8)
```

(旧2試験 `test_process_deleted_closes_candidates`・`test_process_deleted_closes_group_candidates` は削除し、上の2つへ置き換える。**件数不変**)

- [ ] **Step 6: stage1.pyを差し替え**

`backend/src/latch/worker/stage1.py`:
1. import追加: `from latch.intents.deletion import cascade_delete_intent`
2. `_CLOSE_CANDIDATES`・`_CLOSE_GROUPS` 定数(95〜106行)を削除
3. `_process_once` のEVENT_DELETED分岐(331〜336行)を次へ置き換え:

```python
            if event_type == EVENT_DELETED:
                # 物理削除カスケード(M3 ws-6・08 §2.5。移設済みの
                # close_latches_on_delete含む・cascade内で呼ばれる)
                await cascade_delete_intent(conn, intent_id, now)
```

4. `_SELECT_OPEN_LATCHES_ON_DELETE`〜`_INSERT_LATCH_EVENT_SQL` の6定数(108〜151行)と `close_latches_on_delete` 関数(455行〜末尾)を削除(移設済み)
5. モジュールdocstringの1行目説明にある「削除Event処理」関連の記述でclosed化に言及する部分があれば「物理削除カスケード」へ更新(docstring冒頭のdesign参照はM3 ws-6を追記)

- [ ] **Step 7: 全unit実行**

Run: `cd backend && uv run pytest -m "not integration" -q`
Expected: PASS 全件(1194 passed = 1188 + test_deletion.py 6。test_worker_stage1.py は件数不変)

- [ ] **Step 8: コミット**

```bash
git add backend/src/latch/intents/deletion.py backend/src/latch/worker/stage1.py \
  backend/tests/unit/intents/test_deletion.py backend/tests/unit/test_worker_stage1.py
git commit -m "feat: 削除カスケード実体intents/deletion.py新設+stage1のEVENT_DELETED処理を物理削除化(M3 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 2: DELETE API受理status全拡張 + insert_match_eventの冪等化

**Files:**
- Modify: `backend/src/latch/intents/service.py`(delete()のallowed_from・コメント)
- Modify: `backend/src/latch/intents/events.py`(_INSERT_EVENTへON CONFLICT)
- Modify: `backend/tests/unit/intents/test_intents_service.py`(2試験反転+1件追加)
- Modify: `backend/tests/unit/intents/test_events.py`(1件追加)

**Interfaces:**
- Consumes: なし
- Produces: `delete()` が全status(draft/active/paused/matched/expired/cancelled)からcancelled遷移+EVENT_DELETED発行を受理(Task 6のintegrationが使う)

- [ ] **Step 1: 失敗する試験へ書き換え・追記(test_intents_service.py)**

`backend/tests/unit/intents/test_intents_service.py` の896行 `test_delete_matched_rejected` と906行 `test_delete_twice_second_rejected` を次の3試験へ置き換え・追記する(既存ヘルパー `_row`/`_service`/`StubStore` はそのまま利用。**2件置き換え+1件追加で+1件**):

```python
async def test_delete_matched_accepted_to_cancelled():
    """matched受理(FR-19のAPI経路開通・M3 ws-6 design §2.2)。"""
    row = _row(status="matched")
    svc, store, _, uow_conn, _ = _service(store=StubStore(rows={row.id: row}))
    result = await svc.delete(
        auth_provider="google", auth_subject="s", intent_id=row.id
    )
    assert result is None
    upd = store.status_updates[0]
    assert upd["status"] == "cancelled"
    assert upd["version"] == 1  # 不変
    assert upd["expected_status"] == "matched"
    etype, params = _event_calls(uow_conn)[0]
    assert etype == "deleted"
    assert json.loads(params["payload"]) == {"version": 1}


async def test_delete_cancelled_is_idempotent():
    """cancelled済みへの再DELETEも受理(応答204のまま冪等・design §2.2)。
    Eventの同一3点組重複はinsert_match_eventのON CONFLICTが挿入しない。"""
    row = _row(status="cancelled")
    svc, store, _, uow_conn, _ = _service(store=StubStore(rows={row.id: row}))
    await svc.delete(auth_provider="google", auth_subject="s", intent_id=row.id)
    assert store.status_updates[0]["status"] == "cancelled"
    assert store.status_updates[0]["expected_status"] == "cancelled"
    etype, _ = _event_calls(uow_conn)[0]
    assert etype == "deleted"


async def test_delete_expired_accepted():
    """expired受理(期限切れIntentのraw_textも「預けた意思」のため消す)。"""
    row = _row(status="expired")
    svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    await svc.delete(auth_provider="google", auth_subject="s", intent_id=row.id)
    assert store.status_updates[0]["status"] == "cancelled"
```

※ `_service` の戻り値の順序・`_event_calls` の形は既存の `test_delete_active_transitions_cancelled_with_deleted_event`(877行)と完全に同じ形に合わせること(ws-3教訓: 戻り値unpackは既存試験を写す)。

- [ ] **Step 2: test_events.pyへON CONFLICTピンを追記**

`backend/tests/unit/intents/test_events.py` の末尾へ:

```python
def test_insert_event_sql_has_on_conflict_do_nothing():
    """同一3点組の再発行(cancelled再DELETE等)はUNIQUE索引で挿入しない
    (M3 ws-6 design §2.2・ux_match_events_idempotency)。"""
    compiled = str(events._INSERT_EVENT)
    assert "ON CONFLICT DO NOTHING" in compiled
```

(`events` モジュールのimportは既存の `from latch.intents import events` 等の形に合わせる — ファイル冒頭の既存importを確認して使う)

- [ ] **Step 3: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intents_service.py tests/unit/intents/test_events.py -v`
Expected: FAIL(matched受理2試験がInvalidTransitionError・ON CONFLICTピンが文字列不一致)

- [ ] **Step 4: 実装**

`backend/src/latch/intents/service.py` のdelete(758行)のallowed_fromを拡張しコメントを更新:

```python
    async def delete(
        self, *, auth_provider: str, auth_subject: str, intent_id: uuid.UUID
    ) -> None:
        await self._transition(
            auth_provider=auth_provider,
            auth_subject=auth_subject,
            intent_id=intent_id,
            # 全status受理(matched=FR-19経路・expired=raw_text残存回避・
            # cancelled=冪等再削除。M3 ws-6 design §2.2)
            allowed_from=(
                "draft", "active", "paused", "matched", "expired", "cancelled",
            ),
            new_status="cancelled",  # Event処理(stage1)で物理削除(M3 ws-6)
            version_delta=0,
            event_type=EVENT_DELETED,
        )
```

`backend/src/latch/intents/events.py` の `_INSERT_EVENT` へON CONFLICTを追加:

```python
_INSERT_EVENT = text("""
    INSERT INTO match_events
        (event_type, source_intent_id, payload, status, created_at)
    VALUES
        (:event_type, :source_intent_id, CAST(:payload AS jsonb), 'pending',
         :created_at)
    ON CONFLICT DO NOTHING
""")
```

(モジュールdocstringへ1行追記: 「M3 ws-6: 同一3点組の再発行はON CONFLICT DO NOTHINGで挿入しない(cancelled再削除の冪等・design §2.2)」)

- [ ] **Step 5: 全unit実行**

Run: `cd backend && uv run pytest -m "not integration" -q`
Expected: PASS 全件(1196 passed = 1194 + 2)

- [ ] **Step 6: コミット**

```bash
git add backend/src/latch/intents/service.py backend/src/latch/intents/events.py \
  backend/tests/unit/intents/test_intents_service.py backend/tests/unit/intents/test_events.py
git commit -m "feat: DELETE受理statusを全statusへ拡張+insert_match_event冪等化(M3 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 3: 退会 UserService.delete_account

**Files:**
- Modify: `backend/src/latch/users/service.py`(delete_account・DI・SQL定数4本)
- Test: `backend/tests/unit/users/test_account_delete.py`(新規5件)

**Interfaces:**
- Consumes: `cascade_delete_intent`(Task 1・`from latch.intents.deletion import cascade_delete_intent`)・`AccessTokenClaims`(jti/sid/expを保持)・SessionStore(`revoke_access(*, jti, exp, now)`/`revoke_family(*, sid)` — `auth/sessions.py:102,107`。変更禁止・読み取りのみ)
- Produces: `UserService.delete_account(*, claims: AccessTokenClaims) -> None`(Task 4のroutesが呼ぶ・raise UserNotFoundError/DependencyUnavailableError)・`make_user_service(*, clock, engine, sessions=None)`(Task 4のmain.pyが呼ぶ)

- [ ] **Step 1: 失敗するunit試験を書く(test_account_delete.py新規)**

`backend/tests/unit/users/test_account_delete.py` を作成:

```python
"""退会 delete_account のunit試験(M3 ws-6 design §2.3・§4.1)。

スタブuow(ScriptedConn)+記録cascade+記録SessionStoreで、SQL列・bind・
失効呼び出しの順序(txコミット後)を検証する。実DBでの削除結果は
integration/test_account_api.py が担う(§9-6の適合措置)。
"""

import uuid
from datetime import UTC, date, datetime

import pytest

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.users.errors import (
    DependencyUnavailableError,
    UserNotFoundError,
)
from latch.users.service import UserService, UserRow

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000011")
IID1 = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
IID2 = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
CLAIMS = AccessTokenClaims(
    auth_provider="google",
    auth_subject="sub-1",
    jti="jti-1",
    sid="sid-1",
    iat=NOW,
    exp=NOW.replace(hour=13),
)


class RowsResult:
    """複数行を返すSELECT応酬(FakeResultは1行しか返せないため)。"""

    def __init__(self, rows: list):
        self._rows = rows
        self.rowcount = len(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class ScriptedConn:
    def __init__(self, results: list):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = list(results)

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return RowsResult([])


class ScriptedUow:
    """engine.begin() と同じ形。aexitの抜けを記録(失効はコミット後の検証用)。"""

    def __init__(self, results: list):
        self.conn = ScriptedConn(results)
        self.begins = 0
        self.exits = 0

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        self.exits += 1
        return False


class RecordingSessions:
    def __init__(self):
        self.calls: list = []

    async def revoke_access(self, *, jti, exp, now):
        self.calls.append(("access", jti))

    async def revoke_family(self, *, sid):
        self.calls.append(("family", sid))


def _recording_cascade(log: list):
    async def _cascade(conn, intent_id, now):
        log.append(intent_id)

    return _cascade


async def _noop_create(_):
    raise AssertionError("create_user is not used here")


async def _fetch(_p, _s):
    return UserRow(
        id=USER_ID, display_name="退会者", profile={}, birth_date=date(1990, 4, 1),
    )


def _svc(uow=None, sessions=None, cascade=None):
    return UserService(
        clock=FakeClock(NOW),
        create_user=_noop_create,
        fetch_by_auth=_fetch,
        uow=uow,
        sessions=sessions,
        cascade=cascade,
    )


async def test_delete_account_cascades_all_intents_and_user_updates():
    """全Intent(取得順)へcascade→messages/notifications削除→users置換。"""
    cascades: list = []
    uow = ScriptedUow(
        [
            RowsResult([(IID1,), (IID2,)]),  # _SELECT_ALL_INTENT_IDS
            FakeResultNone(), FakeResultNone(), FakeResultNone(),
        ]
    )
    sessions = RecordingSessions()
    svc = _svc(
        uow=uow, sessions=sessions, cascade=_recording_cascade(cascades)
    )
    await svc.delete_account(claims=CLAIMS)
    assert cascades == [IID1, IID2]  # 取得順(id昇順)に全Intent分
    calls = uow.conn.calls
    assert "SELECT id FROM intents" in calls[0][0]
    assert calls[0][1]["user_id"] == USER_ID
    assert "DELETE FROM messages" in calls[1][0]
    assert calls[1][1]["user_id"] == USER_ID  # sender_id一致(design §2.3)
    assert "DELETE FROM notifications" in calls[2][0]
    anonymize = calls[3][0]
    assert "UPDATE users" in anonymize
    assert "退会したユーザー" in anonymize
    assert "'{}'::jsonb" in anonymize
    assert "'deleted:' || id::text" in anonymize
    assert calls[3][1] == {"user_id": USER_ID, "now": NOW}


class FakeResultNone:
    def first(self):
        return None

    def fetchall(self):
        return []

    rowcount = 0


async def test_delete_account_revokes_sessions_after_tx_exit():
    """失効(revoke_access+revoke_family)はuowのaexit後・両方呼ぶ。"""
    cascades: list = []
    uow = ScriptedUow([RowsResult([]), FakeResultNone(), FakeResultNone(), FakeResultNone()])
    sessions = RecordingSessions()
    svc = _svc(
        uow=uow, sessions=sessions, cascade=_recording_cascade(cascades)
    )
    exited_before = 0
    await svc.delete_account(claims=CLAIMS)
    # aexit(=コミット相当)が1回・その後sessions呼び出し2回
    assert uow.exits == 1
    assert sessions.calls == [("access", "jti-1"), ("family", "sid-1")]


async def test_delete_account_unknown_user_404():
    """未登録JWT: fetch_by_authがNone → UserNotFoundError・SQL/失効ゼロ。"""

    async def _none(_p, _s):
        return None

    uow = ScriptedUow([])
    sessions = RecordingSessions()
    svc = UserService(
        clock=FakeClock(NOW),
        create_user=_noop_create,
        fetch_by_auth=_none,
        uow=uow,
        sessions=sessions,
    )
    with pytest.raises(UserNotFoundError):
        await svc.delete_account(claims=CLAIMS)
    assert uow.begins == 0
    assert sessions.calls == []


async def test_delete_account_with_no_intents_is_idempotent_shape():
    """Intent 0件でもユーザー単位処理は走る(冪等再実行の形状)。"""
    cascades: list = []
    uow = ScriptedUow(
        [RowsResult([]), FakeResultNone(), FakeResultNone(), FakeResultNone()]
    )
    svc = _svc(
        uow=uow, sessions=RecordingSessions(),
        cascade=_recording_cascade(cascades),
    )
    await svc.delete_account(claims=CLAIMS)  # 例外なし
    assert cascades == []
    assert len(uow.conn.calls) == 4  # SELECT+messages+notifications+users


async def test_delete_account_without_uow_or_sessions_503():
    """uow/sessions未注入の呼び出しは503(失効しない退会は安全側でない)。"""
    svc = UserService(
        clock=FakeClock(NOW),
        create_user=_noop_create,
        fetch_by_auth=_fetch,
    )
    with pytest.raises(DependencyUnavailableError):
        await svc.delete_account(claims=CLAIMS)
```

注意: `FakeResultNone` はクラス定義の並び順でNameErrorにならないよう、**ファイル先頭のスタブ定義群へまとめて配置**すること(上のコード例では可読性のため途中に出たが、実装時はスタブ域へ移動する)。

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/users/test_account_delete.py -v`
Expected: FAIL(`UserService.__init__() got an unexpected keyword argument 'uow'` 相当)

- [ ] **Step 3: users/service.pyへ実装**

`backend/src/latch/users/service.py`:

1. import追加: `from contextlib import AbstractAsyncContextManager`・`from sqlalchemy.ext.asyncio import AsyncConnection`・`from latch.intents.deletion import cascade_delete_intent`(既存のimport群へ追加)。型エイリアスを追記:

```python
UnitOfWork = Callable[[], AbstractAsyncContextManager[AsyncConnection]]
CascadeFn = Callable[[AsyncConnection, uuid.UUID, datetime], Awaitable[None]]

(importは `from contextlib import AbstractAsyncContextManager` — `intents/service.py:211` と同一表記)
```

2. SQL定数を追記(design §2.3の確定値そのまま):

```python
# 退会(M3 ws-6 design §2.3)。users行は残置し認証紐付けを切替(§2.4案A)
_SELECT_ALL_INTENT_IDS = text("""
    SELECT id FROM intents
    WHERE user_id = CAST(:user_id AS uuid)
    ORDER BY id
""")
_DELETE_MESSAGES = text("""
    DELETE FROM messages WHERE sender_id = CAST(:user_id AS uuid)
""")
_DELETE_NOTIFICATIONS = text("""
    DELETE FROM notifications WHERE user_id = CAST(:user_id AS uuid)
""")
_ANONYMIZE_USER = text("""
    UPDATE users
       SET display_name = '退会したユーザー',
           profile = '{}'::jsonb,
           auth_subject = 'deleted:' || id::text,
           updated_at = CAST(:now AS timestamptz)
     WHERE id = CAST(:user_id AS uuid)
""")
```

3. `UserService.__init__` へデフォルト付き引数を追加・`delete_account` を追記:

```python
    def __init__(
        self,
        *,
        clock: Clock,
        create_user: CreateUserFn,
        fetch_by_auth: FetchByAuthFn,
        uow: UnitOfWork | None = None,
        sessions=None,
        cascade: CascadeFn | None = None,
    ) -> None:
        self._clock = clock
        self._create_user = create_user
        self._fetch_by_auth = fetch_by_auth
        self._uow = uow
        self._sessions = sessions
        self._cascade = cascade
```

```python
    async def delete_account(self, *, claims: AccessTokenClaims) -> None:
        """退会: 全Intentのカスケード+ユーザー単位処理を1txで(設計 §2.3案A)。

        応答204の時点で生データが消える(Worker稼働状態に非依存)。
        コミット後にセッション失効(Redis失効リスト・引用#13)。
        """
        try:
            await self._delete_account(claims=claims)
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users dependency unavailable"
            ) from exc

    async def _delete_account(self, *, claims: AccessTokenClaims) -> None:
        if self._uow is None or self._sessions is None:
            # 失効しない退会は安全側でない(旧JWTが1時間有効のまま残る)
            raise DependencyUnavailableError("delete_account not wired")
        try:
            row = await self._fetch_by_auth(
                claims.auth_provider, claims.auth_subject
            )
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users dependency unavailable"
            ) from exc
        if row is None:
            raise UserNotFoundError("user not found")
        now = self._clock.now()
        cascade = self._cascade or cascade_delete_intent
        async with self._uow() as conn:
            rows = (
                await conn.execute(
                    _SELECT_ALL_INTENT_IDS, {"user_id": row.id}
                )
            ).fetchall()
            for (iid,) in rows:
                await cascade(conn, _coerce_user_id(iid), now)
            await conn.execute(_DELETE_MESSAGES, {"user_id": row.id})
            await conn.execute(_DELETE_NOTIFICATIONS, {"user_id": row.id})
            await conn.execute(
                _ANONYMIZE_USER, {"user_id": row.id, "now": now}
            )
        # uowコミット後に失効(引用#13。失効が先でも無害・logoutと同じ順序)
        await self._sessions.revoke_access(
            jti=claims.jti, exp=claims.exp, now=now
        )
        await self._sessions.revoke_family(sid=claims.sid)
```

4. `make_user_service` へ引数と束ねを追加:

```python
def make_user_service(
    *, clock: Clock, engine: AsyncEngine, sessions=None
) -> UserService:
    """...既存docstringへ1行: M3 ws-6でuow/cascade/sessionsを束ねる..."""
    ...(create_user/fetch_by_authは既存のまま)...
    return UserService(
        clock=clock,
        create_user=create_user,
        fetch_by_auth=fetch_by_auth,
        uow=engine.begin,
        sessions=sessions,
        cascade=cascade_delete_intent,
    )
```

- [ ] **Step 4: test_account_delete.py が通ることを確認**

Run: `cd backend && uv run pytest tests/unit/users/test_account_delete.py tests/unit/users/test_users_service.py -v`
Expected: PASS(test_account_delete.py 5件 + 既存test_users_service.py 無傷)

- [ ] **Step 5: 全unit実行**

Run: `cd backend && uv run pytest -m "not integration" -q`
Expected: PASS 全件(1200 passed = 1195 + 5)

- [ ] **Step 6: コミット**

```bash
git add backend/src/latch/users/service.py backend/tests/unit/users/test_account_delete.py
git commit -m "feat: 退会delete_accountをUserServiceへ追加(同期tx+コミット後セッション失効)(M3 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 4: DELETE /v1/users/me エンドポイント + main.py組替え + wiring追従

**Files:**
- Modify: `backend/src/latch/users/routes.py`(エンドポイント1つ)
- Modify: `backend/src/latch/main.py`(lifespanのredis条件・SessionStore渡し・import)
- Modify: `backend/tests/unit/test_rate_limit_wiring.py`(期待値1箇所)
- Modify: `backend/tests/unit/users/test_users_routes.py`(Stub追記+1件)

**Interfaces:**
- Consumes: `UserService.delete_account`(Task 3)・`require_authenticated`・`api_rate_limited`(既存)・`SessionStore`(`auth/sessions.py`・変更禁止)
- Produces: `DELETE /v1/users/me`(204・Task 6のintegrationが使う)・`make_user_service(clock=, engine=, sessions=)` シグネチャ(main.py lifespan用)

- [ ] **Step 1: 失敗するroutes試験を書く(test_users_routes.py追記)**

`backend/tests/unit/users/test_users_routes.py`:

1. `StubUserService`(55行)へメソッドを追記:

```python
    async def delete_account(self, *, claims):
        self.deleted.append(claims)
```

(`__init__` へ `self.deleted: list = []` を1行追加)

2. ファイル末尾へ試験1件を追記:

```python
async def test_delete_users_me_returns_204(client, stub):
    resp = await client.delete("/v1/users/me")
    assert resp.status_code == 204
    assert resp.content == b""
    assert stub.deleted == [CLAIMS]
```

(`CLAIMS` は既存のfixture依存変数 — ファイル冒頭の定義・`dependency_overrides` の使い方は既存試験と同じ)

3. `backend/tests/unit/test_rate_limit_wiring.py` の期待Counterにある `"/v1/users/me": 1,` を次へ書き換え:

```python
        "/v1/users/me": 2,  # GET + DELETE(M3 ws-6・退会)
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/users/test_users_routes.py tests/unit/test_rate_limit_wiring.py -v`
Expected: FAIL(DELETE /v1/users/me が405/404・wiringが「差し替え漏れ」またはCounter不一致)

- [ ] **Step 3: 実装**

`backend/src/latch/users/routes.py` の末尾へエンドポイントを追記:

```python
@users_router.delete("/me", status_code=204)
async def delete_users_me(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[UserService, Depends(get_users_service)],
) -> None:
    """退会(M3 ws-6・design §2.3)。全Intentカスケード+ユーザー単位処理を
    1トランザクションで同期実行し、コミット後にセッションを失効する。"""
    await svc.delete_account(claims=claims)
    logger.info("users.delete_me ok")
```

(モジュールdocstringへ1行追記: 「DELETE /v1/users/me(退会・M3 ws-6)」)

`backend/src/latch/main.py`:

1. import追加: `from latch.auth.sessions import SessionStore`
2. `_lifespan` 内のredis_client生成条件(88行)を次へ更新:

```python
    if build_auth or build_rate_limit or build_safety or build_users:
        # Redisはauth(失効リスト)・レート制限カウンタ・ブロックキャッシュ(blk:)
        # の共用(design §2.8・ws-5 §2.2)。M3 ws-6: 退会のセッション失効
        # (SessionStore)もusers構築時に必要なため条件へ追加
```

3. users_service構築(117〜120行)を次へ更新:

```python
    if build_users:
        app.state.users_service = make_user_service(
            clock=app.state.clock,
            engine=engine,
            sessions=SessionStore(redis_client)
            if redis_client is not None
            else None,
        )
```

(条件へbuild_usersを足したためbuild_users=Trueならredis_clientは必ず生成済み。三項は防御)

- [ ] **Step 4: 全unit実行**

Run: `cd backend && uv run pytest -m "not integration" -q`
Expected: PASS 全件(1201 passed = 1200 + 1)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/users/routes.py backend/src/latch/main.py \
  backend/tests/unit/test_rate_limit_wiring.py backend/tests/unit/users/test_users_routes.py
git commit -m "feat: 退会API DELETE /v1/users/me新設+lifespanのSessionStore組込み(M3 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 5: RetentionJob(30日定期削除)

**Files:**
- Create: `backend/src/latch/worker/retention.py`
- Modify: `backend/src/latch/worker/main.py`(構築+task起動)
- Test: `backend/tests/unit/test_worker_retention.py`(新規5件)

**Interfaces:**
- Consumes: `next_jst_midnight`(`worker/reset.py` からimport・reset.pyは無変更)・`AsyncEngine`・`Clock`
- Produces: `RetentionJob(*, engine, clock, retry_sec=300, batch_limit=500, sleep=asyncio.sleep)`・`run_once() -> tuple[int, int]`(group削除件数, match削除件数)・`run(*, stop=None)`(worker/main.py・Task 6のintegrationが使う)

- [ ] **Step 1: 失敗するunit試験を書く(test_worker_retention.py新規)**

`backend/tests/unit/test_worker_retention.py` を作成(ScriptedEngine流儀+ResetJob試験流儀):

```python
"""RetentionJob(30日定期削除)のunit試験(M3 ws-6 design §2.5・§4.1)。

スタブconnでSQL・cutoff・バッチ窓を検証。runループはClockを前進させる
sleep差し替え(_advancing_sleep — test_reset_job.py:128 と同一流儀)で
決定的に回す。実DBでの削除結果は integration/test_retention_job.py。
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from latch.core.clock import FakeClock
from latch.worker.retention import BATCH_LIMIT, RETENTION_DAYS, RetentionJob

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


class FakeResult:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


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


def _sql(conn, i: int) -> str:
    return conn.calls[i][0]


async def test_run_once_sqls_cutoff_and_returns_counts():
    """detach→group削除→match削除の順・cutoff=now-30日・件数を返す。"""
    engine = ScriptedEngine(
        [
            FakeResult(None, 0),   # ① latches NULL化
            FakeResult(None, 3),   # ② group DELETE(3<500で終了)
            FakeResult(None, 2),   # ③ match DELETE
        ]
    )
    job = RetentionJob(engine=engine, clock=FakeClock(NOW))
    groups, matches = await job.run_once()
    assert (groups, matches) == (3, 2)
    assert engine.begins == 1  # 単一tx
    detach_sql = _sql(engine.conn, 0)
    assert "UPDATE latches" in detach_sql
    assert "group_candidate_id = NULL" in detach_sql
    gdel = _sql(engine.conn, 1)
    assert "DELETE FROM group_candidates" in gdel
    assert "updated_at < CAST(:cutoff AS timestamptz)" in gdel
    assert "LIMIT :batch_limit" in gdel
    cutoff = engine.conn.calls[1][1]["cutoff"]
    assert (NOW - cutoff).total_seconds() == RETENTION_DAYS * 86400
    assert engine.conn.calls[1][1]["batch_limit"] == BATCH_LIMIT
    assert "DELETE FROM match_candidates" in _sql(engine.conn, 2)
    assert "status" not in gdel  # 全status一律(pending含む・design §2.5)
    assert "status" not in _sql(engine.conn, 2)


async def test_run_once_batches_until_window_under_limit():
    """group削除がbatch_limit到達なら再周(各周でdetach→DELETE)。"""
    engine = ScriptedEngine(
        [
            FakeResult(None, 0),        # detach 1周目
            FakeResult(None, BATCH_LIMIT),  # group 1周目(=limit)
            FakeResult(None, 0),        # detach 2周目
            FakeResult(None, 120),      # group 2周目(<limitで終了)
            FakeResult(None, 7),        # match
        ]
    )
    job = RetentionJob(engine=engine, clock=FakeClock(NOW))
    groups, matches = await job.run_once()
    assert (groups, matches) == (BATCH_LIMIT + 120, 7)
    # 各周の順序: detachがgroup DELETEの直前(2周目もdetachから)
    assert "UPDATE latches" in _sql(engine.conn, 2)
    assert "DELETE FROM group_candidates" in _sql(engine.conn, 3)


def _advancing_sleep(clock: FakeClock, stop: asyncio.Event, stop_at: int):
    """待機秒だけClockを前進させるsleep差し替え(0時跨ぎの決定的再現)。
    test_reset_job.py と同一流儀 — stop_at回目の呼び出しでstopを立てrunを終了。"""
    calls: list[float] = []

    async def _sleep(seconds: float) -> None:
        calls.append(seconds)
        clock.set(clock.now() + timedelta(seconds=seconds))
        if len(calls) >= stop_at:
            stop.set()

    return _sleep, calls


async def test_run_waits_until_midnight_then_runs_once():
    """0時待機(秒数=next_jst_midnightとの差)→run_once→次周期待機で終了。"""
    clock = FakeClock(NOW)  # JST 2026-10-01 21:00 → 次0時まで3時間
    stop = asyncio.Event()
    sleep, calls = _advancing_sleep(clock, stop, stop_at=2)
    engine = ScriptedEngine(
        [FakeResult(None, 0), FakeResult(None, 0), FakeResult(None, 0)]
    )
    job = RetentionJob(engine=engine, clock=clock, sleep=sleep)
    await job.run(stop=stop)
    assert calls[0] == 3 * 3600.0  # 21:00→24:00(JST)
    assert engine.begins == 1  # 0時到達でrun_once実行
    assert calls[1] == 24 * 3600.0  # 次周期の待機(翌0時まで丸1日)


class FlakyConn:
    """1回目のexecuteでSQLAlchemyError・2回目以降は正常応答。"""

    def __init__(self):
        self.calls = 0

    async def execute(self, stmt, params=None):
        self.calls += 1
        if self.calls == 1:
            raise SQLAlchemyError("boom")
        return FakeResult(None, 0)


class FlakyEngine:
    def __init__(self):
        self.conn = FlakyConn()

    def begin(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


async def test_run_retries_after_failure_with_retry_sec():
    """run_once失敗(例外)→retry_sec待機→再試行で成功(翌0時まで放置しない)。"""
    clock = FakeClock(NOW)
    stop = asyncio.Event()
    sleep, calls = _advancing_sleep(clock, stop, stop_at=3)
    job = RetentionJob(
        engine=FlakyEngine(), clock=clock, retry_sec=300, sleep=sleep
    )
    await job.run(stop=stop)
    assert calls[0] == 3 * 3600.0  # 0時まで
    assert calls[1] == 300.0  # retry_secでの待機
    assert job._engine.conn.calls >= 4  # 1回目boom(1本)+2回目成功(3本)


def test_constants_pinned():
    """FR-22の保持期間と窓の固定値(design §2.5)。"""
    assert RETENTION_DAYS == 30
    assert BATCH_LIMIT == 500
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_retention.py -v`
Expected: FAIL(`No module named 'latch.worker.retention'` 相当)

- [ ] **Step 3: retention.pyを実装**

`backend/src/latch/worker/retention.py` を作成:

```python
"""30日定期削除ジョブ(M3 ws-6・design §2.5・08 §2.5 FR-22)。

match_candidates・group_candidatesの評価確定から30日経過行を日次削除する
(削除要求がない場合も。削除されたユーザーの条件由来の判定値が相手側
レコードに恒久残存する経路を断つ)。対象は全status一律でupdated_at < cutoff
(pendingも含む — 起点Intentは確実に失効しているため安全側)。
latchesは削除しない(履歴維持)。group削除の各周でlatches.group_candidate_id
をNULL化してから削除する(FK RESTRICTの解消・proposal表示は残る)。
runループ・graceful shutdown・注入sleepはResetJobと同一契約(0時発火・
失敗時retry_sec再試行)。30日判定はClock.now()(UTC)の実時間。
Indexは追加しない(日次1回・ベータ規模ではフルスキャンで十分 — design §2.5)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.worker.reset import next_jst_midnight

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
BATCH_LIMIT = 500

_DETACH_GROUP_LATCHES = text("""
    UPDATE latches SET group_candidate_id = NULL
     WHERE group_candidate_id IN (
         SELECT id FROM group_candidates
          WHERE updated_at < CAST(:cutoff AS timestamptz)
          ORDER BY id LIMIT :batch_limit)
""")
_DELETE_GROUP_BATCH = text("""
    DELETE FROM group_candidates
     WHERE id IN (SELECT id FROM group_candidates
                   WHERE updated_at < CAST(:cutoff AS timestamptz)
                   ORDER BY id LIMIT :batch_limit)
""")
_DELETE_MATCH_BATCH = text("""
    DELETE FROM match_candidates
     WHERE id IN (SELECT id FROM match_candidates
                   WHERE updated_at < CAST(:cutoff AS timestamptz)
                   ORDER BY id LIMIT :batch_limit)
""")


class RetentionJob:
    """次のJST 0時まで待機→run_once(30日経過候補の削除)。独立task。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        retry_sec: int = 300,
        batch_limit: int = BATCH_LIMIT,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._retry_sec = retry_sec
        self._batch_limit = batch_limit
        self._sleep = sleep

    async def run_once(self) -> tuple[int, int]:
        """0時発火の本体: batch_limitの窓で空になるまで削除。戻り=(group, match)件数。"""
        cutoff = self._clock.now() - _timedelta(days=RETENTION_DAYS)
        groups = matches = 0
        async with self._engine.begin() as conn:
            while True:
                params = {"cutoff": cutoff, "batch_limit": self._batch_limit}
                await conn.execute(_DETACH_GROUP_LATCHES, params)
                n = (await conn.execute(_DELETE_GROUP_BATCH, params)).rowcount
                groups += n
                if n < self._batch_limit:
                    break
            while True:
                params = {"cutoff": cutoff, "batch_limit": self._batch_limit}
                n = (await conn.execute(_DELETE_MATCH_BATCH, params)).rowcount
                matches += n
                if n < self._batch_limit:
                    break
        logger.info(
            "retention.run_once deleted group_candidates=%d match_candidates=%d",
            groups,
            matches,
        )
        return groups, matches

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """次のJST 0時まで待機→run_once(失敗時はretry_secで再試行)。"""
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
                    logger.warning("retention run_once failed", exc_info=True)
                    await self._wait(self._retry_sec, stop)
                    if stop is not None and stop.is_set():
                        return

    async def _wait(self, seconds: float, stop: asyncio.Event | None) -> None:
        """待機(ResetJob._waitと同一契約 — 注入sleepでunit試験が決定的に回る)。"""
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
            task.result()


def _timedelta(*, days: int):
    # datetime.timedelta(clock非依存の静的計算のため直接importせず遅延束縛に
    # しない — arch試験は datetime.now/utcnow の直接呼び出しを禁じるもので
    # timedelta演算は対象外。通常のfrom datetime import timedeltaでよい。
    from datetime import timedelta

    return timedelta(days=days)
```

実装注記: 上の `_timedelta` は説明のための注記つきで、実際にはモジュール先頭の `from datetime import timedelta` 1本でよい(arch試験 `test_arch_no_direct_time.py` が禁じるのは now()/utcnow()/time.time 等の**現在時刻参照**であり、timedelta演算は無関係)。実装時は `cutoff = self._clock.now() - timedelta(days=RETENTION_DAYS)` として `_timedelta` ヘルパーは作らないこと。

- [ ] **Step 4: worker/main.pyへ組み込み**

`backend/src/latch/worker/main.py`:

1. import追加: `from latch.worker.retention import RetentionJob`
2. run()内、ResetJob構築(221〜229行)の直後に追記:

```python
            # RetentionJob DI(M3 ws-6・design §2.5案A): 30日定期削除。
            # engineのみで動く(redis不要)のためResetJobと違い無条件構築
            retention_job = RetentionJob(engine=engine, clock=self.clock)
```

3. task起動(ResetJobの後・`logger.info("worker started...")` の前)へ:

```python
            retention_task = asyncio.create_task(
                retention_job.run(stop=self._stop)
            )
```

4. 終了待ち(`if reset_job is not None:` の後)へ:

```python
            await retention_task
```

- [ ] **Step 5: 全unit実行**

Run: `cd backend && uv run pytest -m "not integration" -q`
Expected: PASS 全件(1207 passed = 1201 + 5。`tests/unit/test_worker.py` のWorker試験は即stopのためRetentionJob構築の影響を受けない)

- [ ] **Step 6: コミット**

```bash
git add backend/src/latch/worker/retention.py backend/src/latch/worker/main.py \
  backend/tests/unit/test_worker_retention.py
git commit -m "feat: 30日定期削除RetentionJob新設+worker本体への組込み(M3 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 6: integration 3ファイル + groupengine test_8追従

**Files:**
- Create: `backend/tests/integration/test_deletion_api.py`(7件)
- Create: `backend/tests/integration/test_account_api.py`(6件)
- Create: `backend/tests/integration/test_retention_job.py`(2件)
- Modify: `backend/tests/integration/test_matching_groupengine.py`(test_8書き換え・件数不変)

**Interfaces:**
- Consumes: `cascade_delete_intent`・`delete()` 全status受理(Task 1〜2)・`delete_account`・`DELETE /v1/users/me`(Task 3〜4)・`RetentionJob`(Task 5)・`Stage1`(直接構築 — groupengine test_8流儀)
- Produces: なし(検証のみ)

**共通対抗策(3ファイルとも・§9-3)**: `SUBJECT_PREFIX = "m3ws6-"`(field fixtureが試験ごとに `m3ws6-{uuid8}-` を生成)・teardownはFK順完全削除・時間値はSystemClock().now()相対(タイムボム回避)。**退会済みusers行はauth_subjectが `'deleted:<uuid>'` へ置換されprefix照会から消えるため、test_account_api.pyのfield fixtureは「試験中に登録したuser_idをリストへappend」し、teardownのusers特定は `auth_subject LIKE :p OR id = ANY(:ids)` の双方で行う**(§9-3)。

- [ ] **Step 1: test_deletion_api.pyを作成**

`backend/tests/integration/test_deletion_api.py`(実DB〔db_engine〕・api常設〔api_client〕・Stage1直接構築〔Workerプロセスなし — groupengine test_8流儀〕。**design §4.1①〜⑥の実DB検証をここで担う**):

```python
"""単発Intent削除API+物理削除カスケードのintegration試験(M3 ws-6 design §4.2)。

実DB(compose常設)・api常設(実HTTP)。物理削除はstage1のEvent処理経由のため、
DELETE APIを叩いた後、Stage1を直接構築して削除Eventを投入(groupengine
test_8流儀)。候補・latches・calibrationはfixtureで直接INSERT(パイプラインを
走らせない — test_safety_api.py流儀)。subjectプレフィックス m3ws6- ・
teardownはFK順+本単位テーブル。時間値はnow相対。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock
from latch.settings import Settings
from latch.worker.stage1 import Stage1

pytestmark = pytest.mark.integration

CATEGORY = "meal"
SUBJECT_PREFIX = "m3ws6-"


@pytest.fixture
async def field(db_engine):
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        # FK順: messages → latch_status_events → calibration_records →
        # notifications → latches → group_candidates → match_candidates →
        # match_events → blocks → intents → users
        await conn.execute(text(
            "DELETE FROM messages WHERE latch_id IN (SELECT id FROM latches"
            " WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)))"), p)
        await conn.execute(text(
            "DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM"
            " latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE"
            " user_id IN (SELECT id FROM users WHERE auth_subject LIKE :p)))"), p)
        await conn.execute(text(
            "DELETE FROM calibration_records WHERE intent_ids && ARRAY("
            "SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"), p)
        await conn.execute(text(
            "DELETE FROM notifications WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)"), p)
        await conn.execute(text(
            "DELETE FROM latches WHERE intent_ids && ARRAY("
            "SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"), p)
        await conn.execute(text(
            "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
            "SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"), p)
        await conn.execute(text(
            "DELETE FROM match_candidates WHERE intent_a_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            " OR intent_b_id IN (SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"), p)
        await conn.execute(text(
            "DELETE FROM match_events WHERE source_intent_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"), p)
        await conn.execute(text(
            "DELETE FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)"), p)
        await conn.execute(text(
            "DELETE FROM users WHERE auth_subject LIKE :p"), p)
```

続けてヘルパー群(`_cli_idp_token`・`_user`・`_future`・`_structured`・`_intent` は test_safety_api.py と同一内容のコピー — ファイル内定義。`_intent` は `start` キーワードを受け付ける形のまま。加えて本単位固有のヘルパー):

```python
async def _candidate(db_engine, a: str, b: str, status: str,
                     a_version: int = 1) -> None:
    """match_candidates行を直接INSERT(全status仕込み用)。

    UNIQUE(intent_a_id, intent_b_id, intent_a_version, intent_b_version)
    があるため、同一ペアへ複数行仕込むときはa_versionを変える
    (再評価世代違い=処理済み行の実態)。
    """
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO match_candidates
                    (intent_a_id, intent_b_id, intent_a_version,
                     intent_b_version, status, created_at, updated_at)
                VALUES (CAST(:a AS uuid), CAST(:b AS uuid), :av, 1, :status,
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
            """),
            {"a": a if a < b else b, "b": b if a < b else a,
             "av": a_version, "status": status, "now": now},
        )


async def _group(db_engine, intent_ids: list) -> str:
    """group_candidates行を直接INSERT(3〜4 Intent)。"""
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO group_candidates (intent_ids, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), 'candidate',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {"ids": [uuid_mod.UUID(i) for i in sorted(intent_ids, key=str)],
             "now": now},
        )
    return str(res.first()[0])


async def _latch(db_engine, intent_ids: list, *, status: str,
                 gid: str | None = None) -> str:
    """latches行を直接INSERT(test_safety_api.pyの_latchと同型)。"""
    now = SystemClock().now()
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
            {"ids": [uuid_mod.UUID(i) for i in intent_ids],
             "gid": uuid_mod.UUID(gid) if gid else None,
             "proposal": json.dumps(
                 {"headcount": len(intent_ids), "match_level": "medium"},
                 ensure_ascii=False),
             "score": 0.85, "status": status,
             "deadline": now + timedelta(hours=2.0),
             "expires": now + timedelta(hours=120.0), "now": now},
        )
    return str(res.first()[0])


async def _calibration(db_engine, latch_id: str, intent_ids: list,
                       user_ids: list) -> None:
    """calibration_records行を直接INSERT(actual_responsesにuser_idを含む)。"""
    now = SystemClock().now()
    responses = [
        {"user_id": u, "response": "yes",
         "answered_at": (now - timedelta(minutes=5)).isoformat()}
        for u in user_ids
    ]
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO calibration_records
                    (latch_id, intent_ids, prediction, proposal_snapshot,
                     actual_responses, matched, created_at, updated_at)
                VALUES (CAST(:lid AS uuid), CAST(:ids AS uuid[]),
                        CAST(:prediction AS jsonb),
                        CAST(:snapshot AS jsonb),
                        CAST(:responses AS jsonb), true,
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
            """),
            {"lid": uuid_mod.UUID(latch_id),
             "ids": [uuid_mod.UUID(i) for i in intent_ids],
             "prediction": json.dumps({"latch_score": 0.8}),
             "snapshot": json.dumps({"headcount": 2}),
             "responses": json.dumps(responses, ensure_ascii=False),
             "now": now},
        )


async def _run_deleted_event(db_engine, intent_id: str, version: int) -> str:
    """APIが発行した削除Eventと同一3点組をStage1へ直接投入(test_8流儀)。"""
    from latch.core.clock import FakeClock
    from latch.events import IncomingEvent

    payload = json.dumps(
        {"event_type": "deleted", "source_intent_id": intent_id,
         "version": version}
    ).encode()
    event = IncomingEvent.from_payload(
        message_id=f"deleted-{intent_id}", payload=payload, ack=lambda: None
    )
    stage1 = Stage1(engine=db_engine, clock=FakeClock(SystemClock().now()),
                    settings=Settings())
    result = await stage1.intake(event)
    assert result.kind == "processed"
    return "processed"


async def _scalar(db_engine, sql: str, params: dict):
    async with db_engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()
```

試験本体(7件):

```python
async def test_1_delete_api_cascade_via_stage1(api_client, db_engine, field):
    """design §4.1①〜⑥: 処理済み含む全候補削除・latchesクローズ・復帰・
    calibration匿名化・Intent行消滅・他ユーザー無傷。"""
    u1 = await _user(api_client, field, "退会者")
    u2 = await _user(api_client, field, "相手")
    u3 = await _user(api_client, field, "第三者")
    i1 = (await _intent(api_client, u1["headers"]))["id"]  # 削除対象
    i2 = (await _intent(api_client, u2["headers"], start=_future(130)))["id"]
    i3 = (await _intent(api_client, u3["headers"]))["id"]
    i2b = (await _intent(api_client, u2["headers"]))["id"]  # 他ユーザー専用
    # ① match_candidates 4status(処理済み含む — UNIQUE回避のためa_version違い)
    for idx, st in enumerate(("pending", "evaluated", "skipped", "closed")):
        await _candidate(db_engine, i1, i2, st, a_version=idx + 1)
    await _candidate(db_engine, i2, i2b, "evaluated")  # 無傷検証用
    # ②③ group+latches(開いている)
    gid = await _group(db_engine, [i1, i2, i3])
    open_latch = await _latch(db_engine, [i1, i2, i3], status="proposed", gid=gid)
    # ④ matched LATCH 2本(復帰2分岐: i2=expires未来→active・期限切れi4→expired)
    m1 = await _latch(db_engine, [i1, i2], status="matched")
    i4 = (await _intent(api_client, u2["headers"]))["id"]
    async with db_engine.begin() as conn:  # i4を期限切れmatchedへ直UPDATE
        await conn.execute(text(
            "UPDATE intents SET status='matched',"
            " expires_at = now() - interval '1 hour' WHERE id = CAST(:i AS uuid)"
        ), {"i": i4})
    m2 = await _latch(db_engine, [i1, i4], status="matched")
    # ⑤ calibration(actual_responsesにu1/u2のuser_id)
    await _calibration(db_engine, m1, [i1, i2], [u1["id"], u2["id"]])

    resp = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert resp.status_code == 204, resp.text
    await _run_deleted_event(db_engine, i1, 1)

    # ① 候補全行消滅(処理済み含む)・他ユーザー行は無傷
    assert await _scalar(db_engine,
        "SELECT count(*) FROM match_candidates WHERE"
        " intent_a_id = CAST(:i AS uuid) OR intent_b_id = CAST(:i AS uuid)",
        {"i": i1}) == 0
    assert await _scalar(db_engine,
        "SELECT count(*) FROM match_candidates WHERE"
        " intent_a_id = CAST(:a AS uuid) AND intent_b_id = CAST(:b AS uuid)",
        {"a": min(i2, i2b), "b": max(i2, i2b)}) == 1
    # ②③ group消滅・latchesのFK NULL化+cancelled
    assert await _scalar(db_engine,
        "SELECT count(*) FROM group_candidates WHERE"
        " intent_ids && ARRAY[CAST(:i AS uuid)]", {"i": i1}) == 0
    assert await _scalar(db_engine,
        "SELECT status FROM latches WHERE id = CAST(:l AS uuid)",
        {"l": open_latch}) == "cancelled"
    assert await _scalar(db_engine,
        "SELECT group_candidate_id FROM latches WHERE id = CAST(:l AS uuid)",
        {"l": open_latch}) is None
    # ④ matched解散+イベント(user_id NULL)+復帰2分岐
    assert await _scalar(db_engine,
        "SELECT status FROM latches WHERE id = CAST(:l AS uuid)",
        {"l": m1}) == "cancelled"
    ev = await _scalar(db_engine,
        "SELECT user_id FROM latch_status_events WHERE latch_id = CAST(:l AS uuid)"
        " AND to_status = 'cancelled'", {"l": m1})
    assert ev is None
    assert await _scalar(db_engine,
        "SELECT status FROM intents WHERE id = CAST(:i AS uuid)",
        {"i": i2}) == "active"   # expires_at未来→active復帰
    assert await _scalar(db_engine,
        "SELECT status FROM intents WHERE id = CAST(:i AS uuid)",
        {"i": i4}) == "expired"  # expires_at経過→expired復帰
    # ⑤ calibration匿名化(D-13第一段)
    row = await _scalar(db_engine,
        "SELECT latch_id, intent_ids, actual_responses FROM"
        " calibration_records WHERE latch_id = CAST(:l AS uuid)", {"l": m1})
    # ↑ scalarは1列のため、実際は execute().first() で3列受けするヘルパーに
    #   替える(§0のws-3教訓: 戻り値unpackは実装時に見直す)
    # ⑥ Intent行消滅
    assert await _scalar(db_engine,
        "SELECT count(*) FROM intents WHERE id = CAST(:i AS uuid)",
        {"i": i1}) == 0
    # 他ユーザー無傷
    assert await _scalar(db_engine,
        "SELECT display_name FROM users WHERE id = CAST(:u AS uuid)",
        {"u": u2["id"]}) == "相手"


async def test_2_delete_matched_intent_204(api_client, db_engine, field):
    """FR-19: matched IntentのDELETEは204(従来422・M3 ws-6拡張)。"""
    u1 = await _user(api_client, field)
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    async with db_engine.begin() as conn:
        await conn.execute(text(
            "UPDATE intents SET status='matched' WHERE id = CAST(:i AS uuid)"),
            {"i": i1})
    resp = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert resp.status_code == 204, resp.text


async def test_3_delete_expired_intent_204(api_client, db_engine, field):
    """expired IntentのDELETEも204(raw_text残存回避)。"""
    u1 = await _user(api_client, field)
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    async with db_engine.begin() as conn:
        await conn.execute(text(
            "UPDATE intents SET status='expired' WHERE id = CAST(:i AS uuid)"),
            {"i": i1})
    resp = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert resp.status_code == 204, resp.text


async def test_4_delete_cancelled_twice_204_idempotent(
    api_client, db_engine, field
):
    """cancelled済みへの再DELETEも204・Event行は重複しない(ON CONFLICT)。"""
    u1 = await _user(api_client, field)
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    first = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert first.status_code == 204
    second = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert second.status_code == 204
    assert await _scalar(db_engine,
        "SELECT count(*) FROM match_events WHERE source_intent_id = CAST(:i AS uuid)"
        " AND event_type = 'deleted'", {"i": i1}) == 1


async def test_5_delete_other_users_intent_403(api_client, field):
    u1 = await _user(api_client, field)
    u2 = await _user(api_client, field)
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    resp = await api_client.delete(f"/v1/intents/{i1}", headers=u2["headers"])
    assert resp.status_code == 403, resp.text


async def test_6_delete_unknown_intent_404(api_client, field):
    u1 = await _user(api_client, field)
    resp = await api_client.delete(
        f"/v1/intents/{uuid_mod.uuid4()}", headers=u1["headers"]
    )
    assert resp.status_code == 404, resp.text


async def test_7_late_event_after_delete_discarded(api_client, db_engine, field):
    """削除後の遅延Eventはintent_not_foundでprocessed破棄(引用#10)。"""
    u1 = await _user(api_client, field)
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    resp = await api_client.delete(f"/v1/intents/{i1}", headers=u1["headers"])
    assert resp.status_code == 204
    await _run_deleted_event(db_engine, i1, 1)  # 削除実行
    # 到着が遅れた created Event(v=1)
    from latch.core.clock import FakeClock
    from latch.events import IncomingEvent

    payload = json.dumps(
        {"event_type": "created", "source_intent_id": i1, "version": 1}
    ).encode()
    event = IncomingEvent.from_payload(
        message_id=f"late-{i1}", payload=payload, ack=lambda: None
    )
    stage1 = Stage1(engine=db_engine, clock=FakeClock(SystemClock().now()),
                    settings=Settings())
    assert (await stage1.intake(event)).kind == "processed"
    assert await _scalar(db_engine,
        "SELECT payload->>'discard_reason' FROM match_events WHERE"
        " event_type = 'created' AND source_intent_id = CAST(:i AS uuid)",
        {"i": i1}) == "intent_not_found"
```

実装注記: `test_1` の⑤の検証は3列受けが必要なため、`_first(db_engine, sql, params)` ヘルパー(`execute().first()` を返す)を追加して `latch_id, intent_ids, actual_responses = await _first(...)` と受け、`latch_id is None`・`intent_ids is None`・`[r for r in responses if "user_id" in r] == []`・`responses[0]["answered_at"]` が保存・`responses[0]["response"] == "yes"`・順序(最初の要素がu1分=仕込み順)を検証する形へ実装すること(§0のws-3教訓)。

- [ ] **Step 2: test_account_api.pyを作成**

`backend/tests/integration/test_account_api.py`(実HTTP・実DB・実Redis〔失効リスト〕。**退会済みusers行のid追跡teardown**):

```python
"""退会API DELETE /v1/users/me のintegration試験(M3 ws-6 design §4.2)。

実DB(compose常設)・api常設(実HTTP)・Redis失効リスト(compose常設)。
退会でusers.auth_subjectが'deleted:<id>'へ置換されるため、teardownは
prefix照会に加えて試験中に登録したuser_idを直接指定して掃く(§9-3)。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration

CATEGORY = "meal"
SUBJECT_PREFIX = "m3ws6-"


@pytest.fixture
async def field(db_engine):
    """subjectプレフィックス+user_id追跡リスト。teardownは双方で削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    user_ids: list[str] = []
    yield prefix, user_ids
    p = {"p": prefix + "%", "ids": [uuid_mod.UUID(i) for i in user_ids]}
    async with db_engine.begin() as conn:
        # users特定は prefix OR id(退会済み行はauth_subject置換でprefix不一致)
        who = ("(SELECT id FROM users WHERE auth_subject LIKE :p"
               " OR id = ANY(CAST(:ids AS uuid[])))")
        await conn.execute(text(
            "DELETE FROM messages WHERE latch_id IN (SELECT id FROM latches"
            f" WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN {who}))"
            " OR sender_id IN " + who), p)
        await conn.execute(text(
            "DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM"
            f" latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE user_id IN {who}))"), p)
        await conn.execute(text(
            "DELETE FROM calibration_records WHERE intent_ids && ARRAY("
            f"SELECT id FROM intents WHERE user_id IN {who})"), p)
        await conn.execute(text(
            f"DELETE FROM notifications WHERE user_id IN {who}"), p)
        await conn.execute(text(
            "DELETE FROM latches WHERE intent_ids && ARRAY("
            f"SELECT id FROM intents WHERE user_id IN {who})"), p)
        await conn.execute(text(
            "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
            f"SELECT id FROM intents WHERE user_id IN {who})"), p)
        await conn.execute(text(
            "DELETE FROM match_candidates WHERE intent_a_id IN"
            f"(SELECT id FROM intents WHERE user_id IN {who})"
            " OR intent_b_id IN"
            f"(SELECT id FROM intents WHERE user_id IN {who})"), p)
        await conn.execute(text(
            "DELETE FROM match_events WHERE source_intent_id IN"
            f"(SELECT id FROM intents WHERE user_id IN {who})"), p)
        await conn.execute(text(
            f"DELETE FROM reports WHERE reporter_id IN {who}"
            f" OR reportee_id IN {who}"), p)
        await conn.execute(text(
            f"DELETE FROM blocks WHERE blocker_id IN {who}"
            f" OR blocked_id IN {who}"), p)
        await conn.execute(text(
            f"DELETE FROM intents WHERE user_id IN {who}"), p)
        await conn.execute(text(
            "DELETE FROM users WHERE auth_subject LIKE :p"
            " OR id = ANY(CAST(:ids AS uuid[]))"), p)
```

(ヘルパー `_cli_idp_token`・`_user`・`_future`・`_structured`・`_intent`・`_candidate`〔a_version既定値のまま〕・`_latch`・`_scalar` は test_deletion_api.py と同一内容のコピー。`_user` のみ、戻り値へ `subject` キーを1つ追加する〔実装注記1〕)

```python
async def _user_tracked(api_client, field_tuple, name="退会者"):
    prefix, user_ids = field_tuple
    u = await _user(api_client, prefix, name)
    user_ids.append(u["id"])
    return u
```

試験本体(6件):

```python
async def test_1_delete_users_me_204_full_cleanup(api_client, db_engine, field):
    """design §4.2: 全Intent消滅・候補消滅・messages(sender分)・notifications
    消滅・blocks/reports残置・users行は置換(残置)。"""
    field_tuple = field
    u1 = await _user_tracked(api_client, field_tuple, "退会する人")
    u2 = await _user_tracked(api_client, field_tuple, "残る人")
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    i2 = (await _intent(api_client, u2["headers"]))["id"]
    await _candidate(db_engine, i1, i2, "evaluated")
    lid = await _latch(db_engine, [i1, i2], status="matched")
    async with db_engine.begin() as conn:  # messages(u1分+u2分)・notifications・blocks・reports
        await conn.execute(text(
            "INSERT INTO messages (latch_id, sender_id, body, created_at)"
            " VALUES (CAST(:l AS uuid), CAST(:s AS uuid), 'hello',"
            " CAST(:n AS timestamptz))"),
            {"l": uuid_mod.UUID(lid), "s": uuid_mod.UUID(u1["id"]),
             "n": SystemClock().now()})
        await conn.execute(text(
            "INSERT INTO messages (latch_id, sender_id, body, created_at)"
            " VALUES (CAST(:l AS uuid), CAST(:s AS uuid), 'stay',"
            " CAST(:n AS timestamptz))"),
            {"l": uuid_mod.UUID(lid), "s": uuid_mod.UUID(u2["id"]),
             "n": SystemClock().now()})
        await conn.execute(text(
            "INSERT INTO notifications (user_id, type, payload, created_at)"
            " VALUES (CAST(:u AS uuid), 'latch_proposed', '{}',"
            " CAST(:n AS timestamptz))"),
            {"u": uuid_mod.UUID(u1["id"]), "n": SystemClock().now()})
        await conn.execute(text(
            "INSERT INTO blocks (blocker_id, blocked_id, created_at)"
            " VALUES (CAST(:a AS uuid), CAST(:b AS uuid),"
            " CAST(:n AS timestamptz))"),
            {"a": uuid_mod.UUID(u1["id"]), "b": uuid_mod.UUID(u2["id"]),
             "n": SystemClock().now()})
        await conn.execute(text(
            "INSERT INTO reports (reporter_id, reportee_id, reason, status,"
            " created_at) VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 'other',"
            " 'pending', CAST(:n AS timestamptz))"),
            {"a": uuid_mod.UUID(u1["id"]), "b": uuid_mod.UUID(u2["id"]),
             "n": SystemClock().now()})

    resp = await api_client.delete("/v1/users/me", headers=u1["headers"])
    assert resp.status_code == 204, resp.text

    assert await _scalar(db_engine,
        "SELECT count(*) FROM intents WHERE user_id = CAST(:u AS uuid)",
        {"u": u1["id"]}) == 0  # 全Intent消滅
    assert await _scalar(db_engine,
        "SELECT count(*) FROM match_candidates WHERE"
        " intent_a_id = CAST(:i AS uuid) OR intent_b_id = CAST(:i AS uuid)",
        {"i": i1}) == 0
    assert await _scalar(db_engine,
        "SELECT count(*) FROM messages WHERE sender_id = CAST(:u AS uuid)",
        {"u": u1["id"]}) == 0  # 送信分のみ削除
    assert await _scalar(db_engine,
        "SELECT count(*) FROM messages WHERE sender_id = CAST(:u AS uuid)",
        {"u": u2["id"]}) == 1  # 相手分は履歴維持
    assert await _scalar(db_engine,
        "SELECT count(*) FROM notifications WHERE user_id = CAST(:u AS uuid)",
        {"u": u1["id"]}) == 0
    assert await _scalar(db_engine,
        "SELECT count(*) FROM blocks WHERE blocker_id = CAST(:u AS uuid)",
        {"u": u1["id"]}) == 1  # 残置(design §2.7)
    assert await _scalar(db_engine,
        "SELECT count(*) FROM reports WHERE reporter_id = CAST(:u AS uuid)",
        {"u": u1["id"]}) == 1  # 残置
    row = await _scalar(db_engine,
        "SELECT display_name FROM users WHERE id = CAST(:u AS uuid)",
        {"u": u1["id"]})
    assert row == "退会したユーザー"  # users行は残置+置換
    subj = await _scalar(db_engine,
        "SELECT auth_subject FROM users WHERE id = CAST(:u AS uuid)",
        {"u": u1["id"]})
    assert subj == f"deleted:{u1['id']}"
    assert await _scalar(db_engine,
        "SELECT status FROM latches WHERE id = CAST(:l AS uuid)",
        {"l": lid}) == "cancelled"  # FR-19(退会でも一致)


async def test_2_latch_display_name_after_delete(api_client, db_engine, field):
    """latches詳細APIのparticipants表示が「退会したユーザー」へ置換。"""
    field_tuple = field
    u1 = await _user_tracked(api_client, field_tuple, "消える人")
    u2 = await _user_tracked(api_client, field_tuple, "見る人")
    i1 = (await _intent(api_client, u1["headers"]))["id"]
    i2 = (await _intent(api_client, u2["headers"]))["id"]
    lid = await _latch(db_engine, [i1, i2], status="matched")
    resp = await api_client.delete("/v1/users/me", headers=u1["headers"])
    assert resp.status_code == 204, resp.text
    detail = await api_client.get(f"/v1/latches/{lid}", headers=u2["headers"])
    assert detail.status_code == 200, detail.text
    names = [p["display_name"] for p in detail.json()["participants"]]
    assert "退会したユーザー" in names  # users joinの自動反映(latches無変更)


async def test_3_token_after_delete_is_new_user(api_client, field):
    """同一IdP subjectでの再ログインは旧Userに紐付かない(新規フロー)。"""
    field_tuple = field
    prefix, _ = field_tuple
    u1 = await _user_tracked(api_client, field_tuple, "再ログイン")
    subject = f"{prefix}same"  # 退会後に同subjectで引き直す
    # u1のsubjectを特定するため_userの引数からは取れない → _userが返す
    # subjectを使う(下の実装注記)
    idp = await _cli_idp_token("google", u1["subject"])
    resp = await api_client.delete("/v1/users/me", headers=u1["headers"])
    assert resp.status_code == 204
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    assert tok.json()["user"] == {"id": None, "profile_complete": False}


async def test_4_old_jwt_revoked_401(api_client, field):
    """退会前JWT(同jti)は失効リストにより401(即時反映・引用#13)。"""
    field_tuple = field
    u1 = await _user_tracked(api_client, field_tuple, "失効確認")
    resp = await api_client.delete("/v1/users/me", headers=u1["headers"])
    assert resp.status_code == 204
    me = await api_client.get("/v1/users/me", headers=u1["headers"])
    assert me.status_code == 401, me.text


async def test_5_delete_users_me_unregistered_404(api_client, field):
    """退会済みsubjectでの再発行JWT(未登録)の退会要求は404(get_meと同型)。"""
    field_tuple = field
    prefix, _ = field_tuple
    u1 = await _user_tracked(api_client, field_tuple, "二回目")
    idp = await _cli_idp_token("google", u1["subject"])
    resp = await api_client.delete("/v1/users/me", headers=u1["headers"])
    assert resp.status_code == 204
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200
    headers2 = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    second = await api_client.delete("/v1/users/me", headers=headers2)
    assert second.status_code == 404, second.text  # 未登録JWTと同型


async def test_6_delete_users_me_requires_auth_401(api_client, field):
    resp = await api_client.delete("/v1/users/me")
    assert resp.status_code == 401, resp.text
```

実装注記2件(§0のws-3教訓):
1. `_user` ヘルパーは test_safety_api.pyのコピーに `subject` キーを戻り値へ1つ追加する(`return {"headers": ..., "id": ..., "display_name": ..., "subject": subject}`)。試験3・5はそれを使う。
2. `test_3` の `u1["subject"]` は退会APIを叩く**前に**IdPトークンを発行しておく(退会後でもIdPトークン発行は可能だが、試験の意図は「退会前に取得した同一IdPクレデンシャルでの再ログイン」。`_cli_idp_token` は内部ツールでいつでも発行できるため、順序は上記コードどおり「退会前にidp取得→退会→token交換」で固定する)。

- [ ] **Step 3: test_retention_job.pyを作成**

`backend/tests/integration/test_retention_job.py`(実DB・RetentionJob直接構築):

```python
"""RetentionJobのintegration試験(M3 ws-6 design §4.2)。

実DB(compose常設)。RetentionJobを直接構築しrun_onceを呼ぶ(worker常設は
test-ciで停止中)。時間値はSystemClock相対(30日境界のタイムボム回避)。
"""

import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock
from latch.worker.retention import RetentionJob

pytestmark = pytest.mark.integration

SUBJECT_PREFIX = "m3ws6-"


@pytest.fixture
async def field(db_engine):
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        # FK順: latch_status_events(latches FK) → latches(group FK元) →
        # group_candidates → match_candidates(intents FK) → intents → users
        await conn.execute(text(
            "DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM"
            " latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE"
            " user_id IN (SELECT id FROM users WHERE auth_subject LIKE :p)))"), p)
        await conn.execute(text(
            "DELETE FROM latches WHERE intent_ids && ARRAY("
            "SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"), p)
        await conn.execute(text(
            "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
            "SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"), p)
        await conn.execute(text(
            "DELETE FROM match_candidates WHERE intent_a_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            " OR intent_b_id IN (SELECT id FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p))"), p)
        await conn.execute(text(
            "DELETE FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)"), p)
        await conn.execute(text(
            "DELETE FROM users WHERE auth_subject LIKE :p"), p)


async def _user_row(db_engine, prefix: str, name: str) -> str:
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        res = await conn.execute(text(
            "INSERT INTO users (display_name, profile, birth_date,"
            " auth_provider, auth_subject, created_at, updated_at)"
            " VALUES (:n, '{}', DATE '1990-04-01', 'google', :s,"
            " CAST(:now AS timestamptz), CAST(:now AS timestamptz))"
            " RETURNING id"),
            {"n": name, "s": f"{prefix}{uuid_mod.uuid4().hex[:8]}", "now": now})
    return str(res.first()[0])


async def _intent_row(db_engine, user_id: str) -> str:
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        res = await conn.execute(text(
            "INSERT INTO intents (user_id, category_primary,"
            " alcohol_involved, raw_text, structured_data, status, version,"
            " time_start, expires_at, created_at, updated_at)"
            " VALUES (CAST(:u AS uuid), 'meal', false, 'retention', '{}',"
            " 'expired', 1, CAST(:t AS timestamptz),"
            " CAST(:e AS timestamptz), CAST(:now AS timestamptz),"
            " CAST(:now AS timestamptz)) RETURNING id"),
            {"u": uuid_mod.UUID(user_id),
             "t": now + timedelta(hours=2), "e": now + timedelta(hours=5),
             "now": now})
    return str(res.first()[0])


async def _candidate(db_engine, a: str, b: str, *, aged: float) -> None:
    """match_candidates行(updated_atを日数指定で経過させる)。"""
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO match_candidates (intent_a_id, intent_b_id,"
            " intent_a_version, intent_b_version, status, created_at,"
            " updated_at) VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 1, 1,"
            " :status, CAST(:c AS timestamptz), CAST(:u AS timestamptz))"),
            {"a": uuid_mod.UUID(min(a, b)), "b": uuid_mod.UUID(max(a, b)),
             "status": "pending",  # pendingも削除対象であることの検証
             "c": now - timedelta(days=aged),
             "u": now - timedelta(days=aged)})


async def test_1_retention_deletes_aged_all_statuses_keeps_recent(db_engine, field):
    """30日+1秒=削除・29日=残る・pendingも削除(design §2.5)。"""
    uid = await _user_row(db_engine, field, "retention1")
    i1, i2, i3 = (await _intent_row(db_engine, uid) for _ in range(3))
    await _candidate(db_engine, i1, i2, aged=30 + 1 / 86400)  # 30日+1秒
    await _candidate(db_engine, i2, i3, aged=29.0)            # 29日
    job = RetentionJob(engine=db_engine, clock=SystemClock())
    groups, matches = await job.run_once()
    assert matches >= 1
    async with db_engine.connect() as conn:
        n_aged = (await conn.execute(text(
            "SELECT count(*) FROM match_candidates WHERE"
            " (intent_a_id = CAST(:a AS uuid) AND intent_b_id = CAST(:b AS uuid))"
            " OR (intent_a_id = CAST(:b AS uuid) AND intent_b_id = CAST(:a AS uuid))"),
            {"a": uuid_mod.UUID(i1), "b": uuid_mod.UUID(i2)})).scalar()
        n_recent = (await conn.execute(text(
            "SELECT count(*) FROM match_candidates WHERE"
            " (intent_a_id = CAST(:a AS uuid) AND intent_b_id = CAST(:b AS uuid))"
            " OR (intent_a_id = CAST(:b AS uuid) AND intent_b_id = CAST(:a AS uuid))"),
            {"a": uuid_mod.UUID(i2), "b": uuid_mod.UUID(i3)})).scalar()
    assert n_aged == 0   # 30日+1秒は削除
    assert n_recent == 1  # 29日は残る


async def test_2_retention_detaches_latches_fk(db_engine, field):
    """group削除前にlatches.group_candidate_idをNULL化・latches行は残る。"""
    uid = await _user_row(db_engine, field, "retention2")
    ids = [await _intent_row(db_engine, uid) for _ in range(3)]
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        gid = str((await conn.execute(text(
            "INSERT INTO group_candidates (intent_ids, status, created_at,"
            " updated_at) VALUES (CAST(:ids AS uuid[]), 'candidate',"
            " CAST(:c AS timestamptz), CAST(:u AS timestamptz)) RETURNING id"),
            {"ids": [uuid_mod.UUID(i) for i in sorted(ids, key=str)],
             "c": now - timedelta(days=31), "u": now - timedelta(days=31)}
        )).first()[0])
        lid = str((await conn.execute(text(
            "INSERT INTO latches (intent_ids, group_candidate_id, proposal,"
            " score, status, response_deadline, expires_at, created_at)"
            " VALUES (CAST(:ids AS uuid[]), CAST(:gid AS uuid), '{}', 0.5,"
            " 'cancelled', CAST(:d AS timestamptz), CAST(:e AS timestamptz),"
            " CAST(:n AS timestamptz)) RETURNING id"),
            {"ids": [uuid_mod.UUID(i) for i in ids],
             "gid": uuid_mod.UUID(gid),
             "d": now + timedelta(hours=1), "e": now + timedelta(hours=2),
             "n": now})).first()[0])
    job = RetentionJob(engine=db_engine, clock=SystemClock())
    await job.run_once()
    async with db_engine.connect() as conn:
        g_count = (await conn.execute(text(
            "SELECT count(*) FROM group_candidates WHERE"
            " id = CAST(:g AS uuid)"), {"g": uuid_mod.UUID(gid)})).scalar()
        latch = (await conn.execute(text(
            "SELECT status, group_candidate_id FROM latches WHERE"
            " id = CAST(:l AS uuid)"), {"l": uuid_mod.UUID(lid)})).first()
    assert g_count == 0
    assert latch is not None and latch[0] == "cancelled"  # latches行は残る
    assert latch[1] is None  # FK解消
```

実装注記: latches直接INSERTの `response_deadline`/`expires_at` はNOT NULLのため必須。`test_1` の検証はrun_once後の2クエリ(30日+1秒行=0・29日行=1)で完結する。

- [ ] **Step 4: groupengine test_8を物理削除期待へ書き換え**

`backend/tests/integration/test_matching_groupengine.py` の901行 `test_8_delete_event_closes_group` を次へ書き換え(関数名・docstring・期待値のみ。仕込みは不変):

```python
async def test_8_delete_event_deletes_group(api_client, db_engine, field):
    """削除Eventで集合・ペア行の物理削除(M3 ws-6・08 §2.5・処理済み含む)。"""
```

(関数本体のうち変更するのは末尾の検証3行+コメント)

```python
    gc2 = await _group_of(db_engine, ids)
    assert gc2 is None  # group_candidates行ごと消える(物理削除)
    # 構成ペア行も削除(処理済み含む全削除 — design §2.1)
    row = await _pair_row(db_engine, a, b)
    assert row is None
```

(それ以外の行 — 仕込み・stage1投入 — は不変。`_group_of`/`_pair_row` は「行なし→None」を返す既存実装のため期待値変更のみで動く)

- [ ] **Step 5: 収集確認(実行はしない)**

Run: `cd backend && uv run pytest tests/integration/test_deletion_api.py tests/integration/test_account_api.py tests/integration/test_retention_job.py --collect-only -q && uv run pytest tests/integration/test_matching_groupengine.py --collect-only -q`
Expected: exit 0・**15 collected**(7+6+2)・groupengineは変更前と同一件数(test_8は関数名変更のみのため)

- [ ] **Step 6: 全unit実行+コミット**

Run: `cd backend && uv run pytest -m "not integration" -q`
Expected: PASS 全件(1207 passed — integrationは新規のためunit件数不変)

```bash
git add backend/tests/integration/test_deletion_api.py \
  backend/tests/integration/test_account_api.py \
  backend/tests/integration/test_retention_job.py \
  backend/tests/integration/test_matching_groupengine.py
git commit -m "test: 削除・退会・30日定期削除のintegration試験+groupengine期待追従(M3 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 7: 完了条件の検証と報告

- [ ] **Step 1: 完了条件1〜6を検証(§6の検証コマンドを順に実行)**

```bash
make lint && make test                       # 期待: exit 0・1207 passed
cd backend && find tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
                                             # 期待: 空
cd backend && uv run alembic heads           # 期待: 単一head(0006)
git diff --name-only main -- backend/alembic # 期待: 空(出力なし)
git diff --name-only main | sort             # 期待: §4の一覧と一致
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src
                                             # 期待: core/clock.py の行のみ
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
                                             # 期待: exit 0
cd backend && uv run pytest tests/integration/test_deletion_api.py tests/integration/test_account_api.py tests/integration/test_retention_job.py --collect-only -q
                                             # 期待: 15 collected
cd backend && uv run pytest tests/integration/test_matching_groupengine.py --collect-only -q
                                             # 期待: 変更前と同一件数
```

§6の完了条件7(test-ci)は**実行しない**。報告書に「スーパーバイザー検証待ち」と記録する(§0規律)。

- [ ] **Step 2: 報告ファイルを作成**

`docs/plans/M3/ws-6-report.md` を§7の形式どおり作成(検証コマンドの出力末尾を証拠として貼る)。

- [ ] **Step 3: コミット**

```bash
git add docs/plans/M3/ws-6-report.md
git commit -m "docs: add M3 ws-6 execution report (M3 ws-6)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

- [ ] **Step 4: 最終確認**

```bash
git status --short          # 期待: 空
git log --oneline main..HEAD  # Task 1〜7のコミット一覧
```

---

## 9. 計画書セルフレビュー(機械チェック・スーパーバイザー指示による。2026-10-01実施)

### 9-1. 新規テストファイルのbasename衝突(運用ルール5)

確認コマンド(実行済み・main時点):

```bash
cd backend && find tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
# → 空(design §3.1と一致)
```

**design §3.1の新規テスト6ファイルの検証結果**:

| design §3.1の名前 | 既存同名 | 判定 |
|---|---|---|
| `test_deletion.py`(unit/intents/) | なし | そのまま可 |
| `test_worker_retention.py`(unit直下) | なし(worker配下にtest_reset_job.py・test_sweeper.pyは別パス) | そのまま可 |
| `test_account_delete.py`(unit/users/) | なし | そのまま可 |
| `test_deletion_api.py`(integration/) | なし | そのまま可 |
| `test_account_api.py`(integration/) | なし | そのまま可 |
| `test_retention_job.py`(integration/) | なし(unit直下test_worker_retention.pyはbasename不同) | そのまま可 |

**衝突なし・designのファイル名をそのまま採用**。新規実装2ファイル(deletion.py・retention.py)も既存モジュールと衝突なし。

### 9-2. ピン試験への追随访問(触る全ピン試験を挙げて影響有無を明記)

| ピン試験 | 触るか | 影響 |
|---|---|---|
| `tests/unit/test_worker_stage1.py` | **2試験を書き換える**(Task 1・design §4.3) | 336行・362行の削除Event 2試験がclosed化を期待しているため物理削除期待(DELETE SQL・順序)へ書き換え。**件数不変**。応答列がcascade 7本+processedへ増える(closed化2本→削除カスケード7本) |
| `tests/integration/test_matching_groupengine.py` | **test_8を書き換える**(Task 6・design §4.3) | 940〜943行の期待(gc2=='closed'・row=='closed')が物理削除期待(gc2 is None・row is None)へ。**件数不変**。`rg 'EVENT_DELETED|closed' tests/` の削除Event絡みの該当はこの2ファイルのみ(2026-10-01確認: 他のclosed言及はgroup候補statusの直接INSERT等で本単位の影響外) |
| `tests/unit/intents/test_intents_service.py` | **2試験反転+1件追加**(Task 2) | `test_delete_matched_rejected`(896行)・`test_delete_twice_second_rejected`(906行)が422期待→受理期待へ反転。+1件(expired受理)。404/403系は既存のまま |
| `tests/unit/intents/test_events.py` | **1件追記**(Task 2) | ON CONFLICT DO NOTHINGのSQLピン。既存の定数値試験(EVENT_DELETED=="deleted"等)は無傷 |
| `tests/unit/test_rate_limit_wiring.py` | **期待値1箇所書き換え**(Task 4) | `/v1/users/me`: 1→2(GET+DELETE)。機械的。ws-7はルート追加なしのため干渉なし(§0) |
| `tests/unit/users/test_users_routes.py` | **1件追記+Stubへメソッド追記**(Task 4) | 既存試験はcreate_app(users_service=stub)でlifespan非実行のためuow/sessions無注入の影響なし |
| `tests/unit/users/test_users_service.py` | 触らない | **無傷**。UserService直構築(85行)はデフォルト付き引数追加のためそのまま通る |
| `tests/integration/test_users_api.py`・その他users系integration | 触らない | 無傷。lifespanはusers_service注入済み(build_users=False)でredis条件変更の影響を受けない |
| `tests/unit/test_worker.py`・`tests/unit/worker/test_reset_job.py` | 触らない | 無傷。Worker.runのRetentionJob構築はengineのみで注入試験(即stop)に影響しない。reset.pyは無変更 |
| `tests/unit/test_app_health.py`・`test_settings.py`・`test_arch_no_direct_time.py` | 触らない | 無傷。settings・依存無接触。追加コードはClock経由のみ |
| `tests/integration/test_safety_api.py`・`test_latches_api.py`・`test_expiry_batches.py` | 触らない | 無傷。safety/・latches/・sweeper経路に変更なし(`rg 'EVENT_DELETED|deleted'` の該当なしを確認済み) |
| `tests/integration/test_intents_crud_api.py` | 触らない | 無傷。DELETEの204期待は受理status拡張で変わらない(draft/active/pausedの既存経路)。matched/expired受理の新経路はtest_deletion_api.pyが担う |

### 9-3. DB残存干渉の対抗策(共有ci-db)

- **subjectプレフィックス**: `m3ws6-`(3ファイルのfield fixtureが試験ごとに `m3ws6-{uuid8}-` を生成・ws-5の `m3ws5-` と衝突しない)
- **teardownはFK順+本単位テーブル**(Task 6のfield fixture): messages → latch_status_events → calibration_records → notifications → latches → group_candidates → match_candidates → match_events → blocks → intents → users
- **退会済みusers行のid追跡**(test_account_api.py固有): 退会でauth_subjectが`'deleted:<uuid>'`へ置換されprefix照会から外れるため、fixtureは`(prefix, user_ids)`をyieldし `_user_tracked` がidをappend。teardownのusers特定は `auth_subject LIKE :p OR id = ANY(CAST(:ids AS uuid[]))` の双方(全削除SQLのusers部分サブクエリを同条件へ)
- **時間値はすべてSystemClock().now()相対**(タイムボム回避・ws-1のtest_k_limits教訓): retentionの30日境界仕込み(30日+1秒/29日)・latchesのdeadline/expires・calibrationのresponses時刻。unit側のFakeClock基準NOWはfakeのため対象外
- **RetentionJob.run_onceはテストプロセス内で直接構築・呼び出し**(worker常設では動かない — test-ciはworker停止→試験→復帰の順)。他単位の残存行はteardown完全削除規律により存在しない想定だが、万一30日超の行が残っていても削除対象は両候補テーブルのみ(latches・usersは触れない)で他試験の期待値を壊さない
- **head不変(0006)**のため運用ルール1の「番号取り合い」は発生しない。ws-6∥ws-7とのtest-ci同時実行は§0規律(実行しない)で回避する

### 9-4. 試験数の整合

| 種別 | main基準(2026-10-01スーパーバイザー指示値) | 本単位増分 | 期待合計 |
|---|---|---|---|
| unit(`make test`) | 1188 passed | **+19**(test_deletion.py 6=Task 1・test_intents_service.py +1=Task 2〔2件は書き換えで件数不変〕・test_events.py +1=Task 2・test_account_delete.py 5=Task 3・test_users_routes.py +1=Task 4・test_worker_retention.py 5=Task 5。test_worker_stage1.py 2件・test_rate_limit_wiring.py は書き換えのみで件数不変) | **1207 passed** |
| test-ci(unit+integration) | 1427 passed | **+34**(unit 19+integration 15=deletion_api 7+account_api 6+retention_job 2。groupengine test_8は件数不変) | **1461 passed**(スーパーバイザー検証時・ws-7分は別加算) |

### 9-5. スペックカバレッジ・型整合(design §1.2の確定値→タスク対応・書き起こし後の点検)

- 引用#1(削除範囲①〜⑥)→ Task 1のcascade(①=⑥のSQL・②=WHERE statusなし+Task 6試験1の4status仕込み・③=close_latches_on_delete移設・④=Task 3の_DELETE_MESSAGES/_DELETE_NOTIFICATIONS+Task 6 account試験1・⑤=_ANONYMIZE_CALIBRATION・⑥=_ANONYMIZE_USER+Task 6試験2)
- 引用#2(FR-19・matched解散・残存Intent復帰・退会/単発で一致)→ Task 1のmatched分岐+Task 2(matched受理)+Task 6試験1・account試験1
- 引用#3(FR-22の30日削除)→ Task 5 RetentionJob+Task 6 test_retention_job
- 引用#4・#6(D-13第一段: ID系NULL化・anonymized_at据え置き・actual_responsesはuser_id除去で回答種別・時刻は残す)→ Task 1の_ANONYMIZE_CALIBRATION+SQLピン(anonymized_at不在・e-'user_id'・WITH ORDINALITY)+Task 6試験1⑤
- 引用#7(latches・responses・messages履歴維持)→ messagesはsender分のみ削除(Task 3)+Task 6 account試験1(相手分残存)・latchesはcascadeでcancelled化のみ
- 引用#8・#9(遷移表)→ Task 1移設分(中身不変)+Task 6試験1の復帰2分岐
- 引用#10(遅延Event破棄)→ Task 6試験7・stage1のintent_not_found経路は無変更
- 引用#11(D-09通知除外・構造担保)→ 触らない(sweeperのstatus='matched'条件)。Task 7の報告書に確認記録を残す
- 引用#13(失効リスト)→ Task 3のrevoke_access+revoke_family+Task 6 account試験4
- 引用#14(単発=Event経由維持)→ Task 1のstage1差し替え(API・Event発行系は無変更)
- 引用#15・M3-8スコープ文言 → 全体
- 引用#16(blocks/reports引継ぎ)→ 残置(Task 6 account試験1の残存ピン)。BlockCache・Layer 1は無接触
- 引用#17・#18(FK・削除順序)→ cascadeの固定順+Task 5のdetach→delete順+teardownのFK順
- design §2.2(受理status拡張・ON CONFLICT冪等)→ Task 2+Task 6試験2〜4
- design §2.3〜2.4(退会API・auth_subject切替)→ Task 3〜4+Task 6 account試験1・3・5
- design §2.5(RetentionJob)→ Task 5+Task 6 test_retention_job 2件
- design §2.7(blocks・reports・match_events・users残存列残置)→ 実装なし(§5禁止)+Task 6 account試験1の残存ピン
- design §2.8(マイグレーションなし等YAGNI)→ §5禁止・§6完了条件3
- 型整合点検: `cascade_delete_intent(conn, intent_id: uuid.UUID, now: datetime) -> None`(Task 1)はTask 3のCascadeFn型(`Callable[[AsyncConnection, uuid.UUID, datetime], Awaitable[None]]`)・stage1の呼び出し(`cascade_delete_intent(conn, intent_id, now)`)と一致。`delete_account(*, claims: AccessTokenClaims) -> None`(Task 3)はTask 4 routesの`await svc.delete_account(claims=claims)`と一致。`make_user_service(*, clock, engine, sessions=None)`(Task 3)はTask 4 main.pyの呼び出しと一致。`RetentionJob(*, engine, clock, retry_sec=300, batch_limit=500, sleep)`(Task 5)はworker/main.pyの構築(`engine=engine, clock=self.clock`のみ)・Task 6 integrationの構築と一致。`run_once() -> tuple[int, int]`はunit試験の`(groups, matches)`受けと一致。ScriptedConn系スタブはFakeResult/RowsResultのfetchall/first/rowcountを持ち実DBとの境界差なし。プレースホルダ(TBD/「適宜」等)なし — 全ステップに実コードを記載済み(integrationの実装注記は期待の明確化であって省略指示ではない)

### 9-6. 未解決論点の確認・適合措置

design §5の7件はG3時確認・docs改版候補として登録済み(§Spec)。**本計画に落とし込めない未解決論点はなく、BLOCKED事項なし**。計画で確定した適合措置は3件(いずれもdesignの意図を保った機械的調整・報告書に記録):

1. **test_deletion.py(unit)はSQLピン方式**(design §4.1「DB fixtureに仕込み…」の実DB検証は integration/test_deletion_api.py 試験1が担う) — unitはDBレス規律(`make test` がcompose常設DBに接続しない・既存unit構成)のため。design §4.1①〜⑥はintegration側で全て検証される
2. **`insert_match_event` へON CONFLICT DO NOTHING追加**(design §2.2が「insert_match_eventのON CONFLICT DO NOTHING」を既存前提として記述しているが、現行 `intents/events.py` の_INSERT_EVENTにはない。designの前提どおりへ1行追加 — これなしにはcancelled再削除の冪等204がUNIQUE索引違反で503になる)
3. **退会APIの2回目呼び出しは404 NOT_FOUND**(design §2.3「冪等性: 2回目の呼び出しは…204を返す」は削除操作の冪等性(再実行が安全)の記述であり、API応答としてはusers行のauth_subject置換後にuser特定不能のためget_meと同型の404になる。05 §5への規定追加(docs改版候補②)時に404を明記する — 報告書の引継ぎへ記録)

