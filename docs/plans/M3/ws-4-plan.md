# M3 ws-4(チャット+実施自己申告)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 成立後の体験のAPI面を埋める — ①チャットAPI: `POST /v1/latches/{latch_id}/messages`(matchedのみ書込可・201)・`GET /v1/latches/{latch_id}/messages`(cursor改頁・閲覧は状態を問わず200)②実施自己申告: `POST /v1/latches/{latch_id}/attendance`(D-09・completedかつ3日以内・LATCH単位先着1名・calibration_recordsのactual_attended/cancelled_after排他反映)③ブロック409 CHAT_READONLY分岐の先行実装(blocks双方向判定・DB直読み・ws-5へ`store.select_block_between`を差し替え点として明け渡し)④マイグレーション0006(idx_messages_latch・ux_calibration_latch部分UNIQUEのIndex 2本)。**テーブル・カラム追加なし・依存追加なし・main.py無変更**。

**Architecture:** design §2.1案A — 書込可否は`status='matched'`単一条件+blocks判定に集約し、matched以外の全状態(proposed/partial_accept/completed/cancelled/rejected/expired/candidate)とブロック適用中は同じ409 CHAT_READONLY(03 §6・08 §2.5の「読み取り専用」統一)。送信はws-1 respondと同一の直列化(latch行FOR UPDATE→参加者検査→status検査→blocks検査→INSERT。Clock.now()はFOR UPDATE取得後に採取)。attendanceはcalibrationの条件付きUPDATE(`WHERE actual_attended IS NULL`)が二重回答排他の本体で、latch行は読取のみ(completedは終端状態・行ロック不要)。cursorはlatches一覧(3キー)のパターンを縮めた2キー`(created_at, id)`キーセット・昇順。APIは状態遷移を書かないためlatch_status_events・notificationsには**触れない**(completed化・attendance通知先行書き込みはws-2実装のまま)。

**Tech Stack:** 変更なし(Python 3.13 / FastAPI / SQLAlchemy[asyncio]+asyncpg text()生SQL / alembic / pytest)。

**Spec:** `docs/plans/M3/ws-4-design.md`(agent1設計メモ。**未解決論点なし** — design §5の7件は2026-10-01スーパーバイザー承認済み・承認記録はSTATUS.md 311行目。①attendanceはLATCH単位先着1名確定〔G3時確認候補〕②CHAT_READONLYをcompleted/cancelled後へ拡大③messages本文1〜1000字・空白のみ422④未completedへのattendanceは409 LATCH_CLOSED⑤calibration行不在は503+構造化ログ⑥マイグレーション0006=Index 2本⑦POST messagesは201。design.mdが本計画より優先)。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m3-ws-4`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(**依存追加なし**のためlockは進まない)。
- **【最重要】共有ci-dbへの `make test-ci` / `make migrate` は禁止**(STATUS運用ルール1・本単位はマイグレーション0006を追加する単位。ws-3とDBを取り合う)。開発は**unit試験(`make test`)のみ**で進める。integration試験ファイル(`tests/integration/test_chat_attendance_api.py`)は**作成するが実行しない** — インポートエラー検出のため `--collect-only` を実行してよい(conftestのimport時副作用はenv設定のみでDB接続しない)。報告ファイルには「**test-ci=スーパーバイザー検証待ち**」と記録する(検証はスーパーバイザーがws-3→ws-4の順に直列実行)。`docker compose build api` も含めcompose系コマンド(docker build/pull/up/down/migrate相当)は一切実行しない。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-10-01)**: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空(既存114ファイル)。本計画の新規3ファイル(`tests/unit/latches/test_chat_service.py`・`tests/unit/latches/test_chat_store.py`・`tests/integration/test_chat_attendance_api.py`)は既存と衝突しない(`test_chat*`は既存ゼロを確認済み)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **alembic 0001〜0005は変更禁止**(design §3)。本単位は`backend/alembic/versions/0006_chat_indexes.py`の**新規追加のみ**。Task 8で `uv run alembic heads` がDBに接続せず単一head `0006` を示すことを確認する。
- **ws-3(通知)が並行で実装されている**: `tests/unit/test_rate_limit_wiring.py` は両単位が追記する(本単位は3エントリ・ws-3は2エントリ)。マージはスーパーバイザーが片方ずつ行い両側保持で解消するため、**追記位置はルート列挙(アルファベット順)の機械的該当箇所でよい**(Task 7)。worktree内でws-3側の追記が見えなくても気にしない。**ws-3はマイグレーションを追加しないため番号の取り合いは発生しない**(head競合なし)。
- **依存追加なし・設定追加なし**: `backend/pyproject.toml`・`backend/uv.lock`・`Makefile`・`.env`・`.env.example`・`settings.py` に触れない(settings無変更ゆえ `test_settings.py`・`test_llm_factory.py` は無傷のはず・§9-2)。
- **固定値の遵守**: design §2 の採用判断(案A・SQL・手順)と本計画§2のグローバル制約は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `refactor:` `chore:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| messagesはmatched以降のみ書き込み可。ブロック適用中のLATCH(08 D-23)は読み取り専用で、書き込みは409 CHAT_READONLY | 05 §2・引用#1 |
| POST /v1/latches/{id}/messages: 認可は参加者・matched以降。GET同・参加者。改頁共通規定 `?cursor=&limit=`(limit 1〜100・既定20・超過は422)・応答は`{"items": [...], "next_cursor"}`・messagesの既定ソートはcreated_at昇順・cursorはサーバ生成の不透明文字列 | 05 §5・引用#2〜#4 |
| 成立済みLATCHは対象時刻の経過でcompleted。完了後も閲覧は可能だが、チャットの新規送信は閉じる | 03 §6・引用#5 |
| matched済みLATCHの参加Intent削除時もLATCHはcancelledで閉じ、チャットは読み取り専用化(閲覧は可能) | 08 §2.5・引用#6 |
| blocksは単方向の記録で、判定は(A,B)(B,A)の双方向。成立後の適用はD-23(matchedはチャット読み取り専用化)。blocksテーブルは0001作成済み・ブロックAPI/Redisキャッシュ/即時適用はws-5 | 05 §2・08 D-23・§5・引用#7〜#8 |
| attendance: request `{"attended": true}` → 200 `{"latch_id": "…", "actual_attended": true}`。409 ATTENDANCE_ALREADY_SUBMITTED(二重回答・訂正不可)/ 409 ATTENDANCE_WINDOW_CLOSED(completed遷移から3日経過)/ 404(参加者以外) | 05 §5・引用#9 |
| completed遷移後のLATCHに参加者が回答。`attended: true`→calibration_records.actual_attended=trueへ、`false`→cancelled_after=trueへ。対象はcompletedのみ・3日以内(超過は409)・初回のみ受理し訂正不可(Ground Truthの純度)。無回答はAPIを叩かないことで表現し欠測扱い | 05 §5・09 D-09・引用#10 |
| actual_attended / cancelled_afterはattendance APIが収集時に更新する経路。updated_atはこの収集時更新で進む。実行率の分母は「actual_attendedの回答済み成立LATCH数」でactual_attended=trueとcancelled_after=trueは排他 | 05 §2・09 §2.2・引用#12〜#13 |
| チャットは成立LATCH単位の閉じたルーム・matched時点で解放。Intentの参照・回答は参加者のみに許可し、ブロックの判定はサーバ側で強制 | 03 §6・05 §5冒頭・引用#16〜#17 |
| 403 FORBIDDENは認可違反(参加者以外)・全API。404 NOT_FOUNDはリソース不在(未登録JWT含む) | 05 §5・引用#18 |
| 構造化ログの許可リスト: 個別Intentの文言・メッセージ本文・回答内容をログへ出さない | 08 §2.4・引用#19 |
| 02#19「成立後にチャットを利用できる」: 成立LATCHでメッセージを送受信する→双方向の送受信記録 | 02 §4・引用#20 |
| 時刻参照はすべてClock経由(arch test強制)。apiプロセスのClockは試験から差し替え不能のため期限境界の試験はDB値を過去/未来へ書き換える等価置換。時間値はnow相対で固定時刻を置かない(タイムボム回避) | C2・design §2.4 |
| attendance通知はsweeperがcompleted遷移時にnotificationsへ先行書き込み済み(type='attendance_request'・payload={latch_id})。本単位はnotificationsを読み書きしない | design §1.3・引用#11・#15 |
| supervisor承認(design §5): ①LATCH単位先着1名②CHAT_READONLY拡大③本文1〜1000字・trim後判定④409 LATCH_CLOSED⑤503+構造化ログ⑥Index 2本⑦POST messagesは201 | STATUS.md 311行 |

## 2. グローバル制約(全タスクに暗黙に適用・design §2の固定値)

- **CHAT_READONLYの適用範囲(案A)**: `status != 'matched'` の全7状態(proposed・partial_accept・completed・cancelled・rejected・expired・candidate)とblocks適用中の**両方**で409 CHAT_READONLY。状態ごとのコード使い分けはしない。
- **送信の直列化手順(design §2.1手順1〜7・この順で実装する)**:
  1. `fetch_user_id`: 未登録JWTは404
  2. `select_latch_for_update`: 行ロック取得。なしは404
  3. `select_participant_intent`: 参加者でなければ403(存在秘匿しない・引用#17)
  4. `status != 'matched'` → 409 CHAT_READONLY
  5. 参加者(自分以外)とのblocks双方向判定 → 引っかかれば409 CHAT_READONLY
  6. `INSERT INTO messages`(created_at=Clock.now()。**FOR UPDATE取得後に採取**)
  7. 201 `{"message": {id, latch_id, sender_id, body, created_at}}`
- **GET messages**: 参加者ならstatusを問わず200(matched以前は空リスト・completed/cancelled後も閲覧継続)。cursorは2キー`(created_at, id)`キーセット(base64url・`"ISO|uuid"`・昇順・パディング除去)。limitは1〜100・既定20。next_cursorは「limit+1件取得して溢れたら」生成(latches一覧と同型)。形式不正cursorは422 VALIDATION_ERROR。
- **body検証(実装定義・design §5-3)**: **trim後(strip)の長さが1〜1000字**。0字・空白のみ・trim後1001字は422 VALIDATION_ERROR(pydantic validator→RequestValidationError→main.pyハンドラ)。保存は元の文字列のまま(trimしない)。改行込みは受理。
- **attendanceの手順(design §2.3手順1〜7・単一トランザクション・この順)**:
  1. `fetch_user_id`: 未登録JWTは404
  2. `select_latch`(読取専用・FOR UPDATEなし): なしは404
  3. `select_participant_intent`: 参加者でなければ**404**(引用#9の存在秘匿。messagesの403と扱いを分ける)
  4. `status != 'completed'` → 409 LATCH_CLOSED(matched〔対象時刻前〕・cancelled・それ以外の全状態)
  5. `now > completed_at + 3日` → 409 ATTENDANCE_WINDOW_CLOSED。**境界は3日ちょうどまで受理**(`now <= completed_at + 3日` の閉区間)
  6. 条件付きUPDATE(`WHERE latch_id = :latch_id AND actual_attended IS NULL`・`SET actual_attended = :attended, cancelled_after = NOT :attended, updated_at = :now`)が排他の本体
  7. 影響0行の事前検査分類: 事前SELECTで`actual_attended IS NOT NULL`なら409 ATTENDANCE_ALREADY_SUBMITTED・**行そのものがなければ503 DEPENDENCY_UNAVAILABLE+構造化ログ1行**(`latch.attendance.calibration_missing latch_id=%s`・本文なし)
- **attendance回答はLATCH単位先着1名**(supervisor承認①)。1人目の回答でLATCHの記録が確定し、2人目以降(1対1の相手・グループの他メンバー)は409 ATTENDANCE_ALREADY_SUBMITTED。actual_attendedとcancelled_afterは常に同時代入で排他を構造担保(`cancelled_after = NOT :attended`)。応答は`200 {"latch_id", "actual_attended"}`のみ(cancelled_afterは出さない)。
- **blocks判定はDB直読み・Redisキャッシュなし**(design §2.2)。`store.select_block_between` がws-5のキャッシュ差し替え点。ブロック登録側API・D-23成立後即時適用(進行中提案のcancelled化)は作らない(ws-5)。
- **notifications・latch_status_eventsに触れない**: 本単位のAPIはLATCHの状態遷移を書かない(messages挿入・calibration更新のみ)。イベント表は既存遷移のまま。
- **時刻はClock経由のみ**(arch test `test_arch_no_direct_time.py` 強制)。`Clock.now()` は送信ではFOR UPDATE取得後に、attendanceでは手順4の直前に1回採取し検査と書き込みで同一値を使う。
- **永続化はtext()生SQLのみ**。SQLAlchemy ORMは導入しない。UUID復元は `_coerce_uuid` 規律、uuid[]のbindは `list[uuid.UUID]` を渡してSQL側で `CAST(:x AS uuid[])`。
- **例外・ログに機微を入れない**(08 §2.4)。例外メッセージは固定文言のみ。**新規3ルートにokログは追加しない**(チャット送信は高頻度・申告はboolean1つ。エラー時のみmain.pyハンドラのwarning=codeのみが出る。respond/getの既存okログは変更しない)。
- **`make lint`(ruff E,F,I,UP,B・format行長88)と `make test` を毎コミット通す**。unit試験は外部プロセス不要・実時間待ちなし(FakeClock・スタブ注入)。
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **completed/cancelled後の送信が201で通る**(引用#5/#6違反) — status検査を`status != 'proposed'`等の部分条件で書くと成立後も書けてしまい「完了後もチャットが閉じない」。 reasonable な期待は「閲覧はできるが送信は409」 → Task 5 unit(matched以外の**全7状態**をループで409)+Task 9試験4・5
2. **ブロック判定の単方向忘れ**((A,B)だけ検査して(B,A)を見逃す・引用#7違反) — 送信者と相手の向きはリクエストごとに変わる。片方向EXISTSだとB→Aの送信が通ってしまう → Task 4のSQLピン(ORで両向き・`ANY(CAST(:others AS uuid[]))` が2回出現)+Task 9試験6(A送信・B送信の**両方**409)
3. **attendanceの窓境界オフバイワン**(3日ちょうどを拒否する/3日+1秒を受理する・引用#10違反) — 「以内」を開区間に読むとちょうど3日目の正当な申告を409で捨てる → Task 2 unit(`is_attendance_window_open` の閉区間ピン: ちょうど3日=True・+1秒=False)+Task 9試験13
4. **二重回答の先着確定漏れ**(条件付きUPDATEの `actual_attended IS NULL` 忘れで2人目が1人目を上書き・引用#9/#10違反) — 訂正不可のGround Truthが壊れ集計が汚染される → Task 4のSQLピン(WHERE句)+Task 9試験12(1対1の2人目・グループ3人の2人目)
5. **GET messages改頁で同時刻メッセージの取りこぼし/重複**(created_atのみのソート・idタブルース無し) — 同一秒に着た2件はcreated_atだけでは順序が定まらず頁境界で落ちる → Task 3のSQLピン(`(created_at = :ct AND id > :mid)` のタプルブレーカー)+Task 5 unit(cursor生成)+Task 9試験2(limit=1で2頁+終端null)

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3):

```text
backend/alembic/versions/0006_chat_indexes.py               (Task 8。Index 2本)
backend/tests/unit/latches/test_chat_service.py             (Task 1〜2・5〜6)
backend/tests/unit/latches/test_chat_store.py               (Task 3〜4)
backend/tests/integration/test_chat_attendance_api.py       (Task 9。design §4.2の17試験)
docs/plans/M3/ws-4-report.md                                (Task 10。報告ファイル)
```

変更(design §3):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/latches/errors.py` | `ChatReadonlyError`(409 CHAT_READONLY)・`AttendanceAlreadySubmittedError`(409)・`AttendanceWindowClosedError`(409)の3クラスを既存階層へ追加 | 1 |
| `backend/src/latch/latches/schemas.py` | `MessageRequest`(body検証付き)・`MessageOut`・`MessageEnvelope`・`MessageListResponse`・`AttendanceRequest`・`AttendanceResponse`の6クラス追加 | 1 |
| `backend/src/latch/latches/service.py` | 純計算 `encode_message_cursor`/`decode_message_cursor`/`is_attendance_window_open` と、`LatchesService.send_message`/`list_messages`/`submit_attendance` の3ユースケース追加 | 2・5・6 |
| `backend/src/latch/latches/store.py` | チャット系(`MessageRow`・`_INSERT_MESSAGE`・`_SELECT_MESSAGES_PAGE`・`_SELECT_PARTICIPANT_USER_IDS`・`_SELECT_BLOCK_BETWEEN` と関数4本)+attendance系(`CalibrationAttendanceRow`・`_SELECT_CALIBRATION_ATTENDANCE`・`_UPDATE_ATTENDANCE` と関数2本)+`LatchRow.completed_at` フィールド追加(`_SELECT_LATCH_FOR_UPDATE`/`_SELECT_LATCH` へcompleted_at取得列追加・`_latch_row` 復元) | 3〜4 |
| `backend/src/latch/latches/routes.py` | POST/GET `/{latch_id}/messages`・POST `/{latch_id}/attendance` の3エンドポイント追記(既存latches_routerへ。okログ追加なし・§2) | 7 |
| `backend/tests/unit/test_rate_limit_wiring.py` | 期待値Counterへ3エントリ追記(`/v1/latches/{latch_id}/attendance`: 1・`/v1/latches/{latch_id}/messages`: 2。機械的・ws-3並走は§0) | 7 |

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`。

**スコープ外のため既存コードに載せる最小変更**: `LatchRow` への `completed_at` フィールド追加と2つのSELECT列追加(attendance手順5がcompleted_atを読むため。respond/list/getの既存経路はこのフィールドを参照しないため挙動不変。`test_latches_service.py` のスタブは `_latch_row` を経由しないmonkeypatch差し替えのため無傷)。

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- **docker/compose系の全コマンド**: `make up` / `make down` / `make migrate` / `make test-ci` / `docker compose build api` / `docker build` / `docker pull` — §0のとおり本単位はマイグレーション追加単位で共有ci-dbを進めてはならない。検証はスーパーバイザーが直列実行する
- **実API呼び出し**: `make g1-gate`・`make g2-gate`・`make embed-smoke`・`make jev-smoke`・`make geo-*` は起動しない(本単位は外部SDKなし・LLM Gatewayに触らない)
- **design §3の禁止(無変更ファイル)**:
  - `backend/src/latch/main.py`(latches_routerは登録済み・lifespanのservice構築も既存のまま。エラーハンドラもLatchesError共通のまま新クラスに自動対応)
  - `backend/src/latch/latches/calibration.py`(作成経路は無変更。本単位はcalibration_recordsの**更新**のみ)
  - `backend/src/latch/worker/` 一式(sweeper.py・latch_engine.py・reset.py・reeval.py・main.py等・completed化・通知先行書き込みはws-2実装のまま)
  - `backend/src/latch/` 配下の auth / users / intents / ratelimit / geo / core / events / llm / g1gate / g2gate 各モジュール(notifications読み書き経路を含む)
  - `backend/alembic/versions/0001〜0005`(変更禁止。0006新規のみ)
  - `backend/tests/integration/conftest.py`・`backend/tests/unit/` 配下のconftest系(新規ファイルから利用するのみ)
  - `backend/tests/` の§4に列挙した以外の既存試験ファイル。特に `test_latches_api.py`・`test_latches_service.py`・`test_latches_store_sql.py`・`test_latches_routes.py`・`test_expiry_batches.py`・`test_settings.py`・`test_llm_factory.py` は**一切触らない**(§9-2の追随访問)
  - `compose.yaml`・`docker/`・`frontend/`・`prototype/`・`backend/pyproject.toml`・`backend/uv.lock`・`Makefile`・`.env`・`.env.example`・`settings.py`
  - `docs/`(01〜12・learn・testassets)・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/` の既存ファイル(M0〜M3のdesign・plan・report)
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4・§2):
  - **GET /v1/notifications・既読・FCM実送信・attendance通知の媒体・文面・お知らせ一覧** → ws-3(本単位はnotificationsテーブルに触れない)
  - **blocks API(POST/DELETE /v1/users/{id}/block・一覧)・Redisキャッシュ・D-23成立後即時適用(進行中提案のcancelled化)** → ws-5(本単位はmatched LATCHへの送信時の判定のみ)
  - **チャット画面・成立済み詳細画面・「このチャットは利用できません」表示** → ws-7(03 第6節)
  - **30日定期削除・calibration_records匿名化(D-13)** → ws-6(匿名化でlatch_idがNULL化されても部分UNIQUEの対象外になるよう0006は設計済み)
  - **02#19のstaging E2E** → ws-9(本単位はci統合試験17件で本体を担保)
  - **期限切れ検知ジョブ・無回答率集計** → M4-5 Observability(09 §2.2)
  - **参加者ごとの回答集約(actual_responses的別テーブル)** → docs改版を要する将来機能(supervisor承認①・G3時確認候補)
  - **messages本文の中間トリム・表示名の応答入り・既読管理** → design §2.1の切り捨て(LATCH詳細のparticipantsで名前解決可)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 10で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(新規unit 19件+test_rate_limit_wiring追記込み)
   検証: `make lint && make test` — ともにexit 0。**期待件数: 1119 passed**(main直近1100+本単位unit 19=service 12+store 7。deselectedのintegrationを除く)。全件数を報告書に記録
2. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
3. **alembic headが0006(DB接続なしの静的確認)**
   検証: `cd backend && uv run alembic heads` が `0006 (head)` 単一を示す・`git diff --name-only main -- backend/alembic` が `backend/alembic/versions/0006_chat_indexes.py` の1行のみ
4. **変更ファイルが§4の一覧どおり(作成5+変更6=11ファイル)**
   検証: Task 10の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
5. **実時間参照が `core/clock.py` のみ**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
6. **integration試験ファイルが収集可能(実行はスーパーバイザー検証)**
   検証: `cd backend && uv run pytest tests/integration/test_chat_attendance_api.py --collect-only -q` がexit 0で**17件**を収集(migrated_db fixtureは実行されない)
7. **test-ci=スーパーバイザー検証待ち**(§0規律・本単位は実行しない)。スーパーバイザー検証時の期待: `docker compose build api` → `make test-ci` がグリーン・**期待件数: 1337 passed**(main 1301+本単位36=unit 19+integration 17)・alembic head=0006・m3ws4-残存ゼロ(§7のSQL)

## 7. 報告形式

**結果ファイル**: `docs/plans/M3/ws-4-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M3 ws-4(チャット+実施自己申告)実行報告

- ブランチ: m3-ws-4 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も・期待1119)> |
| 2 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 3 | alembic head=0006 | PASS/FAIL | <alembic heads 出力 + git diff --name-only main -- backend/alembic> |
| 4 | 変更ファイル=§4の11ファイル | PASS/FAIL | <git diff --name-only main出力> |
| 5 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 6 | integration収集17件 | PASS/FAIL | <pytest --collect-only -q 末尾> |
| 7 | test-ci | **スーパーバイザー検証待ち** | <実施せず(§0規律)。期待1337 passed・head=0006・残存ゼロ> |

## design §5 実装時確認事項の結果
- (なし — design §5の7件は2026-10-01承認済み。本計画§2の固定値から
  変更した場合は下に「変更前→変更後+理由」を記録する)

## 固定値の変更有無(design.md §2・本計画§2)
- CHAT_READONLY=matched単一条件+blocks(§2): 変更なし / 変更あり
- attendance手順1〜7の順序と404/409/503分類(§2): 変更なし / 変更あり
- cursor 2キー(created_at,id)・窓=閉区間3日・body trim後1〜1000字(§2): 変更なし / 変更あり
- その他: 変更なし / 変更あり(<前→後+理由>)

## (G3・ws-5・ws-3への引継ぎ)
- ws-5: blocks判定は store.select_block_between(conn, me, others)(DB直読み・
  キャッシュなし)。Redisキャッシュ差し替え点として本関数を明け渡す
- G3時確認候補(design §5-1): attendanceの回答単位はLATCH単位先着1名
- docs改版候補(design §5): ②05エラー表のCHAT_READONLY発生箇所列 ③05 §5へ
  messages request形式 ⑥05 §3へIndex 2本

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

**検証手順(スーパーバイザー検証用・agent3は実施しない。完了条件7に対応)**:

1. ws-3マージ後のmainに対しws-4をマージ(またはws-4ワークツリーでmainを取り込み)
2. `make lint && make test` — unit全件グリーン(期待1119+ws-3分。§9-4と整合・1118は計画書タイポをsupervisor修正 2026-10-01)
3. `docker compose build api` — **test-ciの前に必ず先行**(STATUS運用ルール4)
4. `make test-ci` — 期待 **1337 passed + ws-3分**(main 1301+unit 19+integration 17)。conftestの `upgrade head` が0006を適用
5. basename一意確認(運用ルール5・ws-3との両側保持確認): `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
6. `cd backend && uv run alembic heads` — 0006(head)
7. test-ci後の残存確認(subject prefix `m3ws4-` で掃除・teardownの実効性):

```sql
SELECT COUNT(*) FROM users WHERE auth_subject LIKE 'm3ws4-%';
SELECT COUNT(*) FROM intents WHERE user_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws4-%');
SELECT COUNT(*) FROM latches WHERE intent_ids && ARRAY(
  SELECT id FROM intents WHERE user_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws4-%'));
SELECT COUNT(*) FROM messages WHERE latch_id IN
  (SELECT id FROM latches WHERE intent_ids && ARRAY(
    SELECT id FROM intents WHERE user_id IN
    (SELECT id FROM users WHERE auth_subject LIKE 'm3ws4-%')));
SELECT COUNT(*) FROM latch_status_events WHERE latch_id IN
  (SELECT id FROM latches WHERE intent_ids && ARRAY(
    SELECT id FROM intents WHERE user_id IN
    (SELECT id FROM users WHERE auth_subject LIKE 'm3ws4-%')));
SELECT COUNT(*) FROM calibration_records WHERE latch_id IN
  (SELECT id FROM latches WHERE intent_ids && ARRAY(
    SELECT id FROM intents WHERE user_id IN
    (SELECT id FROM users WHERE auth_subject LIKE 'm3ws4-%')));
SELECT COUNT(*) FROM notifications WHERE user_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws4-%');
SELECT COUNT(*) FROM blocks WHERE blocker_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws4-%')
  OR blocked_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws4-%');
```

すべて0件であること(messages・calibration_records・blocksを含む・design §4.2)。

---

## 8. 実装ステップ(TDD。Task 1〜10の順で実行する)

### Task 1: 例外3クラス+スキーマ6クラス(契約面)

**Files:**
- Modify: `backend/src/latch/latches/errors.py`(ファイル末尾へ3クラス追加)
- Modify: `backend/src/latch/latches/schemas.py`(ファイル末尾へ6クラス追加)
- Test: `backend/tests/unit/latches/test_chat_service.py`(新規)

**Interfaces:**
- Produces: `ChatReadonlyError`・`AttendanceAlreadySubmittedError`・`AttendanceWindowClosedError`(いずれも `LatchesError` 継承・`http_status`/`code` 固定。Task 5〜6のserviceとmain.pyハンドラが消費)/ `MessageRequest(body: str)`・`MessageOut(id, latch_id, sender_id, body, created_at)`・`MessageEnvelope(message)`・`MessageListResponse(items, next_cursor)`・`AttendanceRequest(attended: bool)`・`AttendanceResponse(latch_id, actual_attended)`(Task 5〜7が消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/latches/test_chat_service.py` を新規作成:

```python
"""チャット+実施自己申告のunit試験(M3 ws-4 design §4.1)。

storeはスタブ(monkeypatch差し替え)でSQLに依存しない(test_latches_service.py
と同型)。時刻はFakeClock。SQL検査はtest_chat_store.py・実HTTPは
test_chat_attendance_api.py(integration)の担い。
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from latch.core.clock import FakeClock
from latch.latches.errors import (
    AttendanceAlreadySubmittedError,
    AttendanceWindowClosedError,
    ChatReadonlyError,
    LatchesError,
)
from latch.latches.schemas import (
    AttendanceRequest,
    AttendanceResponse,
    MessageEnvelope,
    MessageListResponse,
    MessageOut,
    MessageRequest,
)

NOW = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
ME = uuid.uuid4()
PEER = uuid.uuid4()
I1 = uuid.uuid4()


def test_ws4_error_codes_and_statuses():
    """新規3例外はhttp_status+code固定(design §2.1・§2.3・05 §5エラー形式)。"""
    cases = [
        (ChatReadonlyError, 409, "CHAT_READONLY"),
        (AttendanceAlreadySubmittedError, 409, "ATTENDANCE_ALREADY_SUBMITTED"),
        (AttendanceWindowClosedError, 409, "ATTENDANCE_WINDOW_CLOSED"),
    ]
    for exc_cls, status, code in cases:
        assert exc_cls.http_status == status, exc_cls
        assert exc_cls.code == code, exc_cls
        assert issubclass(exc_cls, LatchesError)


def test_message_request_body_rules():
    """bodyはtrim後1〜1000字・空白のみ不可(design §2.1・承認事項③)。

    保存は元文字列のまま(trimしない)。trim後長さで判定する。
    """
    assert MessageRequest(body="こんにちは").body == "こんにちは"
    # 前後の空白は保存される(検査のみtrim)
    assert MessageRequest(body=" こんにちは ").body == " こんにちは "
    assert MessageRequest(body="あ" * 1000).body == "あ" * 1000  # ちょうど1000:受理
    assert MessageRequest(body="1行目\n2行目").body == "1行目\n2行目"  # 改行込み:受理
    for invalid in ("", " ", "\n\t 　", "あ" * 1001):
        with pytest.raises(ValidationError):
            MessageRequest(body=invalid)


def test_message_and_attendance_response_shapes():
    """MessageOutは5要素のみ(表示名を含まない・design §2.1)。

    フロントはLATCH詳細のparticipantsで名前を解決するため、
    messages応答に表示名を載せない構造ピン(Review Focus参照)。
    """
    assert set(MessageOut.model_fields) == {
        "id",
        "latch_id",
        "sender_id",
        "body",
        "created_at",
    }
    assert set(MessageListResponse.model_fields) == {"items", "next_cursor"}
    assert set(MessageEnvelope.model_fields) == {"message"}
    assert set(AttendanceResponse.model_fields) == {
        "latch_id",
        "actual_attended",
    }  # cancelled_afterは応答に出さない(design §2.3)
    assert AttendanceRequest(attended=True).attended is True
    assert AttendanceRequest(attended=False).attended is False
    with pytest.raises(ValidationError):
        AttendanceRequest(attended="maybe")  # boolへパース不能な文字列は422
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_service.py -v`
Expected: **3件とも ERROR/FAIL**(import error: `AttendanceAlreadySubmittedError` 等を import できない)

- [ ] **Step 3: 最小実装(errors.py へ3クラス追加)**

`backend/src/latch/latches/errors.py` の `DependencyUnavailableError` の後(ファイル末尾)へ追加:

```python
class ChatReadonlyError(LatchesError):
    """チャット読取専用状態への送信(05 §2・08 D-23・design §2.1案A)。

    matched以外の全状態(completed/cancelled後の送信关闭を含む)と
    blocks適用中の両方に使う単一コード(03 §6・08 §2.5の統一・承認事項②)。
    """

    http_status = 409
    code = "CHAT_READONLY"


class AttendanceAlreadySubmittedError(LatchesError):
    """実施自己申告の二重回答(初回のみ受理・訂正不可・05 §5)。

    LATCH単位先着1名のため2人目以降の回答もこれに含む(承認事項①)。
    """

    http_status = 409
    code = "ATTENDANCE_ALREADY_SUBMITTED"


class AttendanceWindowClosedError(LatchesError):
    """申告窓閉鎖(completed遷移から3日経過・D-09・design §2.3手順5)。"""

    http_status = 409
    code = "ATTENDANCE_WINDOW_CLOSED"
```

`backend/src/latch/latches/schemas.py` の `LatchListResponse` の後(ファイル末尾)へ追加。importは既存のままで足りる(`uuid`・`datetime`・`BaseModel, ConfigDict` — `field_validator` のみ追加):

```python
from pydantic import BaseModel, ConfigDict, field_validator
```

(既存のimport行を上書き)

```python
class MessageRequest(BaseModel):
    """POST /v1/latches/{id}/messages のbody(05 §5・実装定義・承認事項③)。

    bodyはtrim後1〜1000字(空白のみ不可)。保存はtrimしない。
    値域違反はValueError→RequestValidationError→422 VALIDATION_ERROR。
    """

    body: str

    @field_validator("body")
    @classmethod
    def _body_length(cls, v: str) -> str:
        if not 1 <= len(v.strip()) <= 1000:
            raise ValueError("body must be 1..1000 chars (excluding surrounding whitespace)")
        return v


class MessageOut(BaseModel):
    """messages応答の1件(design §2.1手順7)。表示名は含めない
    (matched/completedのLATCH詳細participantsで解決・design §2.1)。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    latch_id: uuid.UUID
    sender_id: uuid.UUID
    body: str
    created_at: datetime


class MessageEnvelope(BaseModel):
    """POST /v1/latches/{id}/messages の201応答(承認事項⑦)。"""

    message: MessageOut


class MessageListResponse(BaseModel):
    """GET /v1/latches/{id}/messages 応答(05 §5共通規定・cursor改頁)。"""

    items: list[MessageOut]
    next_cursor: str | None = None


class AttendanceRequest(BaseModel):
    """POST /v1/latches/{id}/attendance のbody(05 §5)。"""

    attended: bool


class AttendanceResponse(BaseModel):
    """attendanceの200応答(05 §5)。cancelled_afterは出さない(design §2.3)。"""

    latch_id: uuid.UUID
    actual_attended: bool
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_service.py -v`
Expected: PASS(3件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/errors.py backend/src/latch/latches/schemas.py backend/tests/unit/latches/test_chat_service.py
git commit -m "feat: add chat/attendance error classes and request/response schemas (M3 ws-4)"
```

### Task 2: service純計算(cursor 2キー・3日窓)

**Files:**
- Modify: `backend/src/latch/latches/service.py`(関数3本を module level へ追加)
- Test: `backend/tests/unit/latches/test_chat_service.py`(末尾へ追記)

**Interfaces:**
- Produces: `encode_message_cursor(created_at: datetime, message_id: uuid.UUID) -> str`・`decode_message_cursor(cursor: str) -> tuple[datetime, uuid.UUID]`(形式不正は `LatchValidationError` を raise)・`is_attendance_window_open(completed_at: datetime | None, now: datetime) -> bool`(Task 5〜6が消費)

- [ ] **Step 1: 失敗するテストを書く**

`test_chat_service.py` の末尾へ追記。import部へは**既存クラスのみ**追加する(`import base64`・`from datetime import timedelta`〔既存の `from datetime import UTC, datetime` を拡張〕・`from latch.latches.errors import LatchValidationError` へ1行追加)。**`encode_message_cursor` 等のservice関数のimportはStep 3で実装後に追加する**(未実装のままmodule先頭でimportするとTask 1の試験ごとImportErrorで全滅するため):

```python
def _b64(s: str) -> str:
    """テスト用: 不透明cursor候補の生成(base64url・パディング除去)。"""
    return base64.urlsafe_b64encode(s.encode()).rstrip(b"=").decode()


def test_message_cursor_roundtrip():
    """2キー(created_at,id)のencode/decode往復(design §2.1)。"""
    created = NOW - timedelta(minutes=5)
    mid = uuid.uuid4()
    token = encode_message_cursor(created, mid)
    assert decode_message_cursor(token) == (created, mid)
    assert "=" not in token  # base64urlのパディング除去(不透明文字列)


def test_message_cursor_invalid_422():
    """形式不正cursorは422 VALIDATION_ERROR(latches/intentsと同型)。"""
    for bad in (
        _b64("no-pipe-here"),  # 区切りなし
        _b64("not-a-datetime|" + str(uuid.uuid4())),  # 日時復元失敗
        "!!!",  # base64urlとして不正
    ):
        with pytest.raises(LatchValidationError):
            decode_message_cursor(bad)


def test_is_attendance_window_open_boundaries():
    """3日窓は閉区間: ちょうど3日まで受理・+1秒で閉じ(design §2.3手順5)。

    Review Focus 3: 「以内」を開区間に読むとちょうど3日目の正当な
    申告を409で捨てる。境界を明示ピンする。
    """
    assert is_attendance_window_open(NOW - timedelta(days=2), NOW) is True
    assert is_attendance_window_open(NOW - timedelta(days=3), NOW) is True  # ちょうど3日
    assert (
        is_attendance_window_open(
            NOW - timedelta(days=3) - timedelta(seconds=1), NOW
        )
        is False
    )
    assert is_attendance_window_open(None, NOW) is False  # completed_at未設定は閉
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_service.py -v`
Expected: 追加3件が FAIL with NameError(`encode_message_cursor` is not defined 等)/ Task 1の3件はPASS

- [ ] **Step 3: 最小実装**

`backend/src/latch/latches/service.py` — import部の `from datetime import datetime` を `from datetime import datetime, timedelta` へ変更し、module level の関数群(`encode_cursor` の後ろが自然)へ3本追加:

```python
def encode_message_cursor(created_at: datetime, message_id: uuid.UUID) -> str:
    """messagesのキーセットcursor 2キー(design §2.1): base64url("ISO|uuid")。

    latches一覧のencode_cursor(3キー)の縮小版。ソート順
    (created_at ASC, id ASC)をタプル比較で表現する。
    """
    raw = f"{created_at.isoformat()}|{message_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_message_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """messages cursor復元。形式不正は422 VALIDATION_ERROR(同型)。"""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ct, mid = raw.split("|")
        return datetime.fromisoformat(ct), uuid.UUID(mid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LatchValidationError("invalid cursor") from exc


def is_attendance_window_open(completed_at: datetime | None, now: datetime) -> bool:
    """D-09の3日窓(閉区間: now <= completed_at + 3日・design §2.3手順5)。

    「3日以内」の以内を閉区間として読む(supervisor承認・design §2.3)。
    completed_at未設定(None)は窓閉と扱う(通常起きない防御)。
    """
    if completed_at is None:
        return False
    return now <= completed_at + timedelta(days=3)
```

加えて `test_chat_service.py` のimport部へ実装済み関数を追加(`from latch.latches.service import ...` 行を新設。Task 5でさらに拡張する):

```python
from latch.latches.service import (
    decode_message_cursor,
    encode_message_cursor,
    is_attendance_window_open,
)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_service.py -v`
Expected: PASS(6件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/service.py backend/tests/unit/latches/test_chat_service.py
git commit -m "feat: add message cursor codec and attendance window predicate (M3 ws-4)"
```

### Task 3: store チャット系(messages挿入・改頁・blocks判定)

**Files:**
- Modify: `backend/src/latch/latches/store.py`(dataclass 1本+SQL定数4本+関数4本)
- Test: `backend/tests/unit/latches/test_chat_store.py`(新規)

**Interfaces:**
- Produces: `MessageRow(id, latch_id, sender_id, body, created_at)`・`fetch_participant_user_ids(conn, intent_ids: list[uuid.UUID]) -> list[uuid.UUID]`・`select_block_between(conn, me: uuid.UUID, others: list[uuid.UUID]) -> bool`・`insert_message(conn, *, latch_id, sender_id, body, now) -> MessageRow`・`select_messages_page(conn, *, latch_id, before: tuple[datetime, uuid.UUID] | None, limit: int) -> list[MessageRow]`(Task 5のserviceが消費。`select_block_between` はws-5のキャッシュ差し替え点)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/latches/test_chat_store.py` を新規作成:

```python
"""チャット+attendanceのstore SQL検査(M3 ws-4 design §4.1)。

test_latches_store_sql.pyと同型: 実dialect compile検査(bind paramの
部分認識ゼロ・':name::type' 落穴の再現)+新規SQLの句ピン(design §2.1・
§2.2・§2.3)。実DBでの挙動はintegration(test_chat_attendance_api.py)。
"""

from sqlalchemy.dialects import postgresql

from latch.latches import store

_NEW_SQL = (
    "_INSERT_MESSAGE",
    "_SELECT_MESSAGES_PAGE",
    "_SELECT_PARTICIPANT_USER_IDS",
    "_SELECT_BLOCK_BETWEEN",
    "_SELECT_CALIBRATION_ATTENDANCE",
    "_UPDATE_ATTENDANCE",
)


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_ws4_sql_bind_params_fully_recognized():
    """compiled文字列に未変換の ':name' が残らない(部分認識ゼロ)。"""
    for name in _NEW_SQL:
        stmt = getattr(store, name)
        compiled = _compiled(stmt)
        for token in (
            ":latch_id",
            ":sender_id",
            ":body",
            ":now",
            ":others",
            ":me",
            ":ids",
            ":ct",
            ":mid",
            ":limit",
            ":attended",
        ):
            assert token not in compiled, (name, token)


def test_insert_message_columns_pinned():
    """messages挿入は4列・idはDB DEFAULT→RETURNING(design §2.1手順6)。"""
    raw = str(store._INSERT_MESSAGE)
    assert "INSERT INTO messages" in raw
    for col in ("latch_id", "sender_id", "body", "created_at"):
        assert col in raw
    assert "RETURNING id, latch_id, sender_id, body, created_at" in raw


def test_messages_page_keyset_pinned():
    """改頁はlatch_id絞り+(created_at,id)2キーセット昇順(design §2.1)。

    Review Focus 5: created_at同値のタプルブレーカー(id > :mid)必須。
    """
    raw = str(store._SELECT_MESSAGES_PAGE)
    assert "latch_id = CAST(:latch_id AS uuid)" in raw
    assert "CAST(:ct AS timestamptz) IS NULL" in raw
    assert "created_at > CAST(:ct AS timestamptz)" in raw
    assert "(created_at = CAST(:ct AS timestamptz)" in raw
    assert "id > CAST(:mid AS uuid)" in raw
    assert "ORDER BY created_at ASC, id ASC" in raw
    assert "LIMIT :limit" in raw


def test_block_between_bidirectional_pinned():
    """blocks判定は(me→others) OR (others→me)の双方向EXISTS(引用#7)。

    Review Focus 2: 片方向だとB→A送信が通る。ANY(:others) が2回出現。
    """
    raw = str(store._SELECT_BLOCK_BETWEEN)
    assert "SELECT EXISTS" in raw
    assert "blocker_id = CAST(:me AS uuid)" in raw
    assert "blocked_id = ANY(CAST(:others AS uuid[]))" in raw
    assert "blocked_id = CAST(:me AS uuid)" in raw
    assert raw.count("ANY(CAST(:others AS uuid[]))") == 2
```

(末尾2件のattendanceピンはTask 4で追記する)

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_store.py -v`
Expected: **4件とも FAIL/ERROR**(`store._INSERT_MESSAGE` が存在しない)

- [ ] **Step 3: 最小実装**

`backend/src/latch/latches/store.py` へ追加。

(1) dataclass — `ParticipantRow` の定義の後へ:

```python
@dataclass(frozen=True)
class MessageRow:
    """messages 1行(挿入RETURNING・改頁選択の読取結果・design §2.1)。"""

    id: uuid.UUID
    latch_id: uuid.UUID
    sender_id: uuid.UUID
    body: str
    created_at: datetime
```

(2) SQL定数 — 「読取系」コメントブロックの後(ファイル末尾の関数定義より前の定数地帯)へ:

```python
# -- チャット(M3 ws-4 design §2.1・§2.2) --

_INSERT_MESSAGE = text("""
    INSERT INTO messages (latch_id, sender_id, body, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:sender_id AS uuid), :body,
            CAST(:now AS timestamptz))
    RETURNING id, latch_id, sender_id, body, created_at
""")

# GET改頁のキーセット(latch_id絞り+(created_at,id)昇順・design §2.1)
_SELECT_MESSAGES_PAGE = text("""
    SELECT id, latch_id, sender_id, body, created_at
    FROM messages
    WHERE latch_id = CAST(:latch_id AS uuid)
      AND (CAST(:ct AS timestamptz) IS NULL
           OR created_at > CAST(:ct AS timestamptz)
           OR (created_at = CAST(:ct AS timestamptz)
               AND id > CAST(:mid AS uuid)))
    ORDER BY created_at ASC, id ASC
    LIMIT :limit
""")

# 送信時のblocks判定対象(自分以外の参加者・design §2.2)
_SELECT_PARTICIPANT_USER_IDS = text("""
    SELECT user_id FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

# blocks双方向判定(引用#7・design §2.2。ws-5のRedisキャッシュ差し替え点)
_SELECT_BLOCK_BETWEEN = text("""
    SELECT EXISTS (
        SELECT 1 FROM blocks
        WHERE (blocker_id = CAST(:me AS uuid)
               AND blocked_id = ANY(CAST(:others AS uuid[])))
           OR (blocker_id = ANY(CAST(:others AS uuid[]))
               AND blocked_id = CAST(:me AS uuid))
    )
""")
```

(3) 関数 — ファイル末尾へ:

```python
def _message_row(mapping) -> MessageRow:
    """messages行→MessageRow(UUID復元)。"""
    return MessageRow(
        id=_coerce_uuid(mapping["id"]),
        latch_id=_coerce_uuid(mapping["latch_id"]),
        sender_id=_coerce_uuid(mapping["sender_id"]),
        body=mapping["body"],
        created_at=mapping["created_at"],
    )


async def fetch_participant_user_ids(
    conn: AsyncConnection, intent_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """参加Intent→user_id一覧(送信時のblocks判定対象・design §2.2)。"""
    res = await conn.execute(
        _SELECT_PARTICIPANT_USER_IDS, {"ids": list(intent_ids)}
    )
    return [_coerce_uuid(r[0]) for r in res.fetchall()]


async def select_block_between(
    conn: AsyncConnection, me: uuid.UUID, others: list[uuid.UUID]
) -> bool:
    """自分と参加相手の間のblocks双方向判定(引用#7・design §2.2)。

    ws-5がRedisキャッシュ差し替え点として使う(本実装はDB直読み)。
    """
    res = await conn.execute(
        _SELECT_BLOCK_BETWEEN, {"me": me, "others": list(others)}
    )
    return bool(res.scalar())


async def insert_message(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    sender_id: uuid.UUID,
    body: str,
    now: datetime,
) -> MessageRow:
    """messages挿入(design §2.1手順6)。idはDB DEFAULT→RETURNINGで受け取る。"""
    res = await conn.execute(
        _INSERT_MESSAGE,
        {"latch_id": latch_id, "sender_id": sender_id, "body": body, "now": now},
    )
    row = res.mappings().first()
    assert row is not None
    return _message_row(row)


async def select_messages_page(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    before: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[MessageRow]:
    """messages改頁選択(design §2.1)。before=(created_at, id)・昇順。"""
    res = await conn.execute(
        _SELECT_MESSAGES_PAGE,
        {
            "latch_id": latch_id,
            "ct": before[0] if before else None,
            "mid": before[1] if before else None,
            "limit": limit,
        },
    )
    return [_message_row(r) for r in res.mappings().all()]
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_store.py -v`
Expected: PASS(4件)。あわせて `uv run pytest tests/unit/latches -v` で既存(test_latches_store_sql.py等)も全件PASSのまま(§9-2)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/store.py backend/tests/unit/latches/test_chat_store.py
git commit -m "feat: add chat store functions (insert/page/blocks-exists) with SQL pins (M3 ws-4)"
```

### Task 4: store attendance系+LatchRow.completed_at拡張

**Files:**
- Modify: `backend/src/latch/latches/store.py`(`LatchRow` へcompleted_at追加・`_SELECT_LATCH_FOR_UPDATE`/`_SELECT_LATCH` へ取得列追加・`_latch_row` 復元・SQL定数2本+関数2本+dataclass 1本)
- Test: `backend/tests/unit/latches/test_chat_store.py`(末尾へ追記)

**Interfaces:**
- Produces: `LatchRow.completed_at: datetime | None`(Task 6のsubmit_attendanceが読む)・`CalibrationAttendanceRow(actual_attended: bool | None)`・`select_calibration_attendance(conn, latch_id) -> CalibrationAttendanceRow | None`(None=行なし)・`update_attendance(conn, *, latch_id, attended: bool, now) -> bool`(False=影響0行)

- [ ] **Step 1: 失敗するテストを書く**

`test_chat_store.py` の末尾へ追記:

```python
def test_attendance_update_conditional_pinned():
    """条件付きUPDATE: 排他の本体(design §2.3手順6)。

    Review Focus 4: WHERE actual_attended IS NULL 忘れで2人目が上書き得る。
    actual_attended/cancelled_afterは同時代入で排他を構造担保。
    """
    raw = str(store._UPDATE_ATTENDANCE)
    assert "actual_attended = :attended" in raw
    assert "cancelled_after = NOT :attended" in raw
    assert "updated_at = CAST(:now AS timestamptz)" in raw
    assert "latch_id = CAST(:latch_id AS uuid)" in raw
    assert "actual_attended IS NULL" in raw
    assert "RETURNING id" in raw


def test_select_calibration_attendance_pinned():
    """事前読取はactual_attendedのみ(design §2.3手順7の分類用)。"""
    raw = str(store._SELECT_CALIBRATION_ATTENDANCE)
    assert "SELECT actual_attended FROM calibration_records" in raw
    assert "latch_id = CAST(:latch_id AS uuid)" in raw
    # LatchRowはcompleted_atを持つ(attendance手順5・§4のスコープ注記)
    assert "completed_at" in store.LatchRow.__dataclass_fields__


def test_latch_selects_now_read_completed_at():
    """_SELECT_LATCH(_FOR_UPDATE)はcompleted_atを取得する(手順5の入力)。"""
    assert "completed_at" in str(store._SELECT_LATCH)
    assert "completed_at" in str(store._SELECT_LATCH_FOR_UPDATE)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_store.py -v`
Expected: 追加3件が FAIL(属性なし)/ Task 3の4件はPASS

- [ ] **Step 3: 最小実装**

`backend/src/latch/latches/store.py`:

(1) `LatchRow` へフィールド追加(末尾・デフォルト値付きなので既存の位置呼び出し互換):

```python
    created_at: datetime
    completed_at: datetime | None = None
```

(既存の `created_at: datetime` 行の直後に `completed_at: datetime | None = None` を挿入)

(2) 2つのSELECTの列リストへ `completed_at` を追加:

```python
_SELECT_LATCH_FOR_UPDATE = text("""
    SELECT id, status, intent_ids, responses, response_deadline, expires_at,
           score, proposal, group_candidate_id, created_at, completed_at
    FROM latches WHERE id = CAST(:latch_id AS uuid)
    FOR UPDATE
""")
```

```python
_SELECT_LATCH = text("""
    SELECT id, status, intent_ids, responses, response_deadline, expires_at,
           score, proposal, group_candidate_id, created_at, completed_at
    FROM latches WHERE id = CAST(:latch_id AS uuid)
""")
```

(3) `_latch_row()` の末尾へ復元を追加:

```python
        created_at=mapping["created_at"],
        completed_at=mapping["completed_at"],
```

(既存の `created_at=mapping["created_at"],` の行の直後に `completed_at=mapping["completed_at"],` を挿入)

(4) dataclass — `MessageRow` の後へ:

```python
@dataclass(frozen=True)
class CalibrationAttendanceRow:
    """attendance事前読取の1行(行の有無と回答済みの区別用・design §2.3手順7)。"""

    actual_attended: bool | None
```

(5) SQL定数 — チャット系定数の後へ:

```python
# -- 実施自己申告(M3 ws-4 design §2.3) --

_SELECT_CALIBRATION_ATTENDANCE = text("""
    SELECT actual_attended FROM calibration_records
    WHERE latch_id = CAST(:latch_id AS uuid)
""")

# 手順6: 条件付きUPDATE(二重回答の排他の本体・design §2.3)
_UPDATE_ATTENDANCE = text("""
    UPDATE calibration_records
    SET actual_attended = :attended,
        cancelled_after = NOT :attended,
        updated_at = CAST(:now AS timestamptz)
    WHERE latch_id = CAST(:latch_id AS uuid)
      AND actual_attended IS NULL
    RETURNING id
""")
```

(6) 関数 — ファイル末尾へ:

```python
async def select_calibration_attendance(
    conn: AsyncConnection, latch_id: uuid.UUID
) -> CalibrationAttendanceRow | None:
    """attendance事前読取(design §2.3手順7)。None=行そのものがない。"""
    row = (
        await conn.execute(_SELECT_CALIBRATION_ATTENDANCE, {"latch_id": latch_id})
    ).first()
    if row is None:
        return None
    return CalibrationAttendanceRow(actual_attended=row[0])


async def update_attendance(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    attended: bool,
    now: datetime,
) -> bool:
    """手順6: 条件付きUPDATE。False=影響0行(呼び出し側はAlreadySubmitted)。"""
    res = await conn.execute(
        _UPDATE_ATTENDANCE,
        {"latch_id": latch_id, "attended": attended, "now": now},
    )
    return res.first() is not None
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches tests/unit/matching -v`
Expected: PASS(store 7件+latches/matching既存全件。LatchRow拡張が既存試験を壊していないことの確認を含む)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/store.py backend/tests/unit/latches/test_chat_store.py
git commit -m "feat: add attendance store (conditional update + preselect) and LatchRow.completed_at (M3 ws-4)"
```

### Task 5: service.send_message・list_messages

**Files:**
- Modify: `backend/src/latch/latches/service.py`(LatchesService へ2メソッド追加)
- Test: `backend/tests/unit/latches/test_chat_service.py`(末尾へ追記)

**Interfaces:**
- Consumes: Task 3の `fetch_participant_user_ids`・`select_block_between`・`insert_message`・`select_messages_page`、Task 2の cursor codec
- Produces: `LatchesService.send_message(*, auth_provider: str, auth_subject: str, latch_id: uuid.UUID, body: str) -> MessageRow`(Task 7のrouteが消費)・`LatchesService.list_messages(*, auth_provider, auth_subject, latch_id, cursor: str | None = None, limit: int = 20) -> tuple[list[MessageRow], str | None]`

- [ ] **Step 1: 失敗するテストを書く**

`test_chat_service.py` の末尾へ追記。import部は2箇所: (1) Task 2で追加した `from latch.latches.service import (...)` ブロックへ `LatchesService` を追記する。(2) `MessageRow` は `_msg` ヘルパー内の関数内importのまま(module先頭へは置かない):

```python
# -- send_message / list_messages(design §2.1手順1〜7) --


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


def _row(**overrides) -> SimpleNamespace:
    """LatchRow相当のスタブ(serviceは属性アクセスのみ)。"""
    base = dict(
        id=uuid.uuid4(),
        status="matched",
        intent_ids=[I1, uuid.uuid4()],
        completed_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _ret(value):
    """monkeypatch差し替え用の「常にvalueを返すasync関数」ファクトリ。"""

    async def _inner(*args, **kwargs):
        return value

    return _inner


def _msg(row, sender=ME, body="こんにちは", minutes_ago=0) -> object:
    from latch.latches.store import MessageRow

    return MessageRow(
        id=uuid.uuid4(),
        latch_id=row.id,
        sender_id=sender,
        body=body,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )


async def test_send_message_authorization_404_403(monkeypatch):
    """未登録JWTは404・参加者以外は403(引用#17・#18・design §2.1手順1〜3)。"""
    from latch.latches import store as store_mod
    from latch.latches.errors import ForbiddenError, LatchNotFoundError

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(None))
    with pytest.raises(LatchNotFoundError):
        await LatchesService(clock=FakeClock(NOW), engine=_NoopEngine()).send_message(
            auth_provider="google", auth_subject="s", latch_id=uuid.uuid4(), body="x"
        )
    row = _row()
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(None))
    with pytest.raises(ForbiddenError):
        await LatchesService(clock=FakeClock(NOW), engine=_NoopEngine()).send_message(
            auth_provider="google", auth_subject="s", latch_id=row.id, body="x"
        )


async def test_send_message_non_matched_statuses_409(monkeypatch):
    """matched以外の全7状態は409 CHAT_READONLY(design §2.1案A・引用#1/#5/#6)。

    Review Focus 1: 部分条件(proposedだけ等)ではcompleted後の送信が
    通ってしまう。全状態を網羅ピンする。
    """
    from latch.latches import store as store_mod

    for status in (
        "proposed",
        "partial_accept",
        "completed",
        "cancelled",
        "rejected",
        "expired",
        "candidate",
    ):
        row = _row(status=status)
        monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
        monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
        monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
        with pytest.raises(ChatReadonlyError):
            await LatchesService(
                clock=FakeClock(NOW), engine=_NoopEngine()
            ).send_message(
                auth_provider="google", auth_subject="s", latch_id=row.id, body="x"
            )


async def test_send_message_matched_inserts_and_blocks(monkeypatch):
    """matched+blocksなしならINSERT→MessageRow(design §2.1手順4〜7)。"""
    from latch.latches import store as store_mod

    row = _row(status="matched")
    out_msg = _msg(row, body="はじめまして")
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "fetch_participant_user_ids", _ret([ME, PEER]))
    monkeypatch.setattr(store_mod, "select_block_between", _ret(False))
    monkeypatch.setattr(store_mod, "insert_message", _ret(out_msg))
    got = await LatchesService(clock=FakeClock(NOW), engine=_NoopEngine()).send_message(
        auth_provider="google", auth_subject="s", latch_id=row.id, body="はじめまして"
    )
    assert got is out_msg
    # blocks引っかかりは同コード(手順5・design §2.2)
    monkeypatch.setattr(store_mod, "select_block_between", _ret(True))
    with pytest.raises(ChatReadonlyError):
        await LatchesService(clock=FakeClock(NOW), engine=_NoopEngine()).send_message(
            auth_provider="google", auth_subject="s", latch_id=row.id, body="x"
        )


async def test_list_messages_builds_next_cursor(monkeypatch):
    """limit+1件取得→溢れたらnext_cursor生成(latches一覧と同型・design §2.1)。"""
    from latch.latches import store as store_mod

    row = _row(status="completed")  # completedでも閲覧可(引用#5)
    m1 = _msg(row, body="1通目", minutes_ago=2)
    m2 = _msg(row, sender=PEER, body="2通目", minutes_ago=1)
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch", _ret(row))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "select_messages_page", _ret([m1, m2]))
    items, next_cursor = await LatchesService(
        clock=FakeClock(NOW), engine=_NoopEngine()
    ).list_messages(auth_provider="google", auth_subject="s", latch_id=row.id, limit=1)
    assert items == [m1]  # limit=1で1件だけ返す
    assert next_cursor == encode_message_cursor(m1.created_at, m1.id)
    # cursor渡しはdecodeしてbeforeへ渡される(形式不正は422)
    monkeypatch.setattr(
        store_mod, "select_messages_page", _ret([m2])
    )
    items2, next2 = await LatchesService(
        clock=FakeClock(NOW), engine=_NoopEngine()
    ).list_messages(
        auth_provider="google",
        auth_subject="s",
        latch_id=row.id,
        cursor=encode_message_cursor(m1.created_at, m1.id),
        limit=1,
    )
    assert items2 == [m2]
    assert next2 is None  # 次頁なし
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_service.py -v`
Expected: 追加4件が FAIL(`LatchesService` に `send_message` 属性なし)/ Task 1〜2の6件はPASS

- [ ] **Step 3: 最小実装**

`backend/src/latch/latches/service.py` — import部へ追加:

```python
from latch.latches.errors import (
    AlreadyAnsweredError,
    AttendanceAlreadySubmittedError,
    AttendanceWindowClosedError,
    ChatReadonlyError,
    DependencyUnavailableError,
    ForbiddenError,
    LatchClosedError,
    LatchesError,
    LatchExpiredError,
    LatchNotFoundError,
    LatchValidationError,
)
```

(既存のimportブロックへ3クラスを追記)

`LatchesService` へ — `get` メソッドの後(クラス内の「一覧・詳細」の後)へ「チャット・実施自己申告」ブロックとして2メソッド追加:

```python
    # -- チャット(M3 ws-4 design §2.1手順1〜7) --

    async def send_message(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id: uuid.UUID,
        body: str,
    ):
        """matched LATCHへのメッセージ送信(design §2.1)。

        matched以外の全状態とblocks適用中は409 CHAT_READONLY(案A)。
        FOR UPDATEでsweeperのcompleted化・回答APIと直列化する。
        """
        try:
            async with self._engine.begin() as conn:
                user_id = await store.fetch_user_id(conn, auth_provider, auth_subject)
                if user_id is None:
                    raise LatchNotFoundError("user not found")
                row = await store.select_latch_for_update(conn, latch_id)
                if row is None:
                    raise LatchNotFoundError("latch not found")
                if (
                    await store.select_participant_intent(
                        conn, row.intent_ids, user_id
                    )
                    is None
                ):
                    raise ForbiddenError("not a participant")
                now = self._clock.now()  # FOR UPDATE取得後に採取
                if row.status != "matched":
                    raise ChatReadonlyError("chat readonly")
                user_ids = await store.fetch_participant_user_ids(
                    conn, row.intent_ids
                )
                others = [u for u in user_ids if u != user_id]
                if await store.select_block_between(conn, user_id, others):
                    raise ChatReadonlyError("chat readonly")
                return await store.insert_message(
                    conn, latch_id=row.id, sender_id=user_id, body=body, now=now
                )
        except LatchesError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    async def list_messages(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id: uuid.UUID,
        cursor: str | None = None,
        limit: int = 20,
    ):
        """messages取得(design §2.1)。参加者ならstatusを問わず閲覧可。"""
        try:
            async with self._engine.connect() as conn:
                user_id = await store.fetch_user_id(conn, auth_provider, auth_subject)
                if user_id is None:
                    raise LatchNotFoundError("user not found")
                row = await store.select_latch(conn, latch_id)
                if row is None:
                    raise LatchNotFoundError("latch not found")
                if (
                    await store.select_participant_intent(
                        conn, row.intent_ids, user_id
                    )
                    is None
                ):
                    raise ForbiddenError("not a participant")
                before = decode_message_cursor(cursor) if cursor else None
                rows = await store.select_messages_page(
                    conn, latch_id=latch_id, before=before, limit=limit + 1
                )
            items = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = encode_message_cursor(last.created_at, last.id)
            return items, next_cursor
        except LatchesError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_service.py -v`
Expected: PASS(10件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/service.py backend/tests/unit/latches/test_chat_service.py
git commit -m "feat: add send_message and list_messages use cases (M3 ws-4)"
```

### Task 6: service.submit_attendance

**Files:**
- Modify: `backend/src/latch/latches/service.py`(LatchesService へ1メソッド追加)
- Test: `backend/tests/unit/latches/test_chat_service.py`(末尾へ追記)

**Interfaces:**
- Consumes: Task 4の `select_calibration_attendance`・`update_attendance`・`LatchRow.completed_at`、Task 2の `is_attendance_window_open`
- Produces: `LatchesService.submit_attendance(*, auth_provider: str, auth_subject: str, latch_id: uuid.UUID, attended: bool) -> AttendanceResponse`(Task 7のrouteが消費)

- [ ] **Step 1: 失敗するテストを書く**

`test_chat_service.py` の末尾へ追記。import部は2箇所: (1) Task 1で追加した `from latch.latches.errors import (...)` ブロックへ `DependencyUnavailableError`・`LatchClosedError`・`LatchNotFoundError` を追記する。(2) `from latch.latches.store import CalibrationAttendanceRow` を1行追加する(`AttendanceResponse`・`AttendanceAlreadySubmittedError`・`AttendanceWindowClosedError` はTask 1でimport済み):

```python
# -- submit_attendance(design §2.3手順1〜7) --


async def test_submit_attendance_records_and_returns(monkeypatch):
    """attendedのboolがそのままactual_attendedへ(cancelled_afterは排反)。

    UPDATEは同一tx・now=Clock(design §2.3手順6)。
    """
    from latch.latches import store as store_mod

    row = _row(status="completed", completed_at=NOW - timedelta(hours=1))
    row2 = _row(status="completed", completed_at=NOW - timedelta(hours=2))
    calls: list[dict] = []

    async def _update(conn, *, latch_id, attended, now):
        calls.append({"latch_id": latch_id, "attended": attended, "now": now})
        return True

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch", _ret(row))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(
        store_mod, "select_calibration_attendance",
        _ret(CalibrationAttendanceRow(actual_attended=None)),
    )
    monkeypatch.setattr(store_mod, "update_attendance", _update)
    svc = LatchesService(clock=FakeClock(NOW), engine=_NoopEngine())
    out = await svc.submit_attendance(
        auth_provider="google", auth_subject="s", latch_id=row.id, attended=True
    )
    assert out == AttendanceResponse(latch_id=row.id, actual_attended=True)
    # attended=Falseの対: actual_attended=Falseへ(引用#10・#13)
    monkeypatch.setattr(store_mod, "select_latch", _ret(row2))
    out2 = await svc.submit_attendance(
        auth_provider="google", auth_subject="s", latch_id=row2.id, attended=False
    )
    assert out2 == AttendanceResponse(latch_id=row2.id, actual_attended=False)
    assert calls == [
        {"latch_id": row.id, "attended": True, "now": NOW},
        {"latch_id": row2.id, "attended": False, "now": NOW},
    ]


async def test_submit_attendance_classifications(monkeypatch):
    """手順3〜7の分類: 404/409×2種/503(design §2.3・引用#9/#10)。"""
    from latch.latches import store as store_mod

    svc = LatchesService(clock=FakeClock(NOW), engine=_NoopEngine())
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    done = _row(status="completed", completed_at=NOW - timedelta(hours=1))
    monkeypatch.setattr(store_mod, "select_latch", _ret(done))
    monkeypatch.setattr(
        store_mod, "select_calibration_attendance",
        _ret(CalibrationAttendanceRow(actual_attended=None)),
    )
    # (a) 参加者以外は404(messagesの403と扱いを分ける・引用#9)
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(None))
    with pytest.raises(LatchNotFoundError):
        await svc.submit_attendance(
            auth_provider="google", auth_subject="s", latch_id=done.id, attended=True
        )
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    # (b) matched(対象時刻前)・cancelled等は409 LATCH_CLOSED(手順4)
    for status in ("matched", "cancelled", "proposed"):
        monkeypatch.setattr(
            store_mod, "select_latch", _ret(_row(status=status))
        )
        with pytest.raises(LatchClosedError):
            await svc.submit_attendance(
                auth_provider="google", auth_subject="s",
                latch_id=uuid.uuid4(), attended=True,
            )
    # (c) 3日+1秒経過は409 ATTENDANCE_WINDOW_CLOSED(手順5)
    monkeypatch.setattr(
        store_mod,
        "select_latch",
        _ret(
            _row(
                status="completed",
                completed_at=NOW - timedelta(days=3) - timedelta(seconds=1),
            )
        ),
    )
    with pytest.raises(AttendanceWindowClosedError):
        await svc.submit_attendance(
            auth_provider="google", auth_subject="s", latch_id=uuid.uuid4(),
            attended=True,
        )
    # (d) 回答済みは409 ATTENDANCE_ALREADY_SUBMITTED(手順7事前検査)
    monkeypatch.setattr(store_mod, "select_latch", _ret(done))
    monkeypatch.setattr(
        store_mod, "select_calibration_attendance",
        _ret(CalibrationAttendanceRow(actual_attended=True)),
    )
    with pytest.raises(AttendanceAlreadySubmittedError):
        await svc.submit_attendance(
            auth_provider="google", auth_subject="s", latch_id=done.id, attended=True
        )
    # (e) calibration行不在は503(手順7・承認事項⑤)
    monkeypatch.setattr(
        store_mod, "select_calibration_attendance", _ret(None)
    )
    with pytest.raises(DependencyUnavailableError):
        await svc.submit_attendance(
            auth_provider="google", auth_subject="s", latch_id=done.id, attended=True
        )
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_chat_service.py -v`
Expected: 追加2件が FAIL(`submit_attendance` 属性なし)/ 既存10件はPASS

- [ ] **Step 3: 最小実装**

`LatchesService` へ — `list_messages` の後に1メソッド追加。あわせてservice.py冒頭の既存 `from latch.latches.schemas import LatchDetailOut, LatchSummaryOut, ParticipantOut` 行へ `AttendanceResponse` を追記する:

```python
from latch.latches.schemas import (
    AttendanceResponse,
    LatchDetailOut,
    LatchSummaryOut,
    ParticipantOut,
)
```

メソッド本体:

```python
    # -- 実施自己申告(M3 ws-4 design §2.3手順1〜7) --

    async def submit_attendance(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id: uuid.UUID,
        attended: bool,
    ) -> AttendanceResponse:
        """completed LATCHへの実施自己申告(D-09・design §2.3)。

        回答はLATCH単位で先着1名が確定(承認事項①)。条件付きUPDATEが
        排他の本体(latch行は読取のみ — completedは終端状態)。
        """
        try:
            async with self._engine.begin() as conn:
                user_id = await store.fetch_user_id(conn, auth_provider, auth_subject)
                if user_id is None:
                    raise LatchNotFoundError("user not found")
                row = await store.select_latch(conn, latch_id)
                if row is None:
                    raise LatchNotFoundError("latch not found")
                if (
                    await store.select_participant_intent(
                        conn, row.intent_ids, user_id
                    )
                    is None
                ):
                    # messagesの403と扱いを分ける: 通知を受け取っていない
                    # 可能性のあるユーザーに関与の有無を開示しない(引用#9)
                    raise LatchNotFoundError("not a participant")
                now = self._clock.now()
                if row.status != "completed":
                    raise LatchClosedError("latch closed")
                if not is_attendance_window_open(row.completed_at, now):
                    raise AttendanceWindowClosedError("attendance window closed")
                cal = await store.select_calibration_attendance(conn, latch_id)
                if cal is None:
                    logger.warning(
                        "latch.attendance.calibration_missing latch_id=%s", latch_id
                    )
                    raise DependencyUnavailableError("calibration record missing")
                if cal.actual_attended is not None:
                    raise AttendanceAlreadySubmittedError("already submitted")
                updated = await store.update_attendance(
                    conn, latch_id=latch_id, attended=attended, now=now
                )
                if not updated:  # 影響0行=並行で先着済み(手順7)
                    raise AttendanceAlreadySubmittedError("already submitted")
                return AttendanceResponse(latch_id=row.id, actual_attended=attended)
        except LatchesError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches -v`
Expected: PASS(test_chat_service.py 12件+test_chat_store.py 7件+既存latches試験全件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/service.py backend/tests/unit/latches/test_chat_service.py
git commit -m "feat: add submit_attendance use case with first-writer-wins update (M3 ws-4)"
```

### Task 7: routes 3エンドポイント+レート制限ピン追従

**Files:**
- Modify: `backend/src/latch/latches/routes.py`(3エンドポイント追加)
- Modify: `backend/tests/unit/test_rate_limit_wiring.py`(期待値Counterへ3エントリ追記)

**Interfaces:**
- Consumes: Task 5〜6の `LatchesService.send_message`/`list_messages`/`submit_attendance`、Task 1の6スキーマ
- Produces: HTTP契約 `POST /v1/latches/{latch_id}/messages`(201)・`GET /v1/latches/{latch_id}/messages`(200)・`POST /v1/latches/{latch_id}/attendance`(200)。既存 `latches_router`(prefix・タグ・`api_rate_limited` 依存)への追記なのでレート制限は自動適用

**注記**: routesのunit試験は作らない(design §4.1のunit対象はservice・storeのみ。ルートは薄い変換層で、実HTTPでの応答契約はTask 9の統合試験17件が担保する。新ルートにokログも追加しない — §2グローバル制約)。

- [ ] **Step 1: 失敗するテストを書く(既存ピン試験への機械的追記)**

`backend/tests/unit/test_rate_limit_wiring.py` の `test_all_v1_routes_are_rate_limited` のCounter期待値へ、アルファベット順の該当箇所(`/v1/latches/{latch_id}` と `/v1/latches/{latch_id}/response` の間)へ3エントリを挿入:

```python
        "/v1/latches": 1,  # M3 ws-1(一覧)
        "/v1/latches/{latch_id}": 1,  # M3 ws-1(詳細)
        "/v1/latches/{latch_id}/attendance": 1,  # M3 ws-4(実施自己申告)
        "/v1/latches/{latch_id}/messages": 2,  # M3 ws-4(送信+取得)
        "/v1/latches/{latch_id}/response": 1,  # M3 ws-1(回答)
```

(`/v1/latches/{latch_id}/attendance` と `/v1/latches/{latch_id}/messages` の2行が追加。位置は機械的・ws-3並走は§0のとおりスーパーバイザーが両側保持でマージする)

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_rate_limit_wiring.py -v`
Expected: FAIL(末尾の `assert Counter(protected) == {...}` で期待と実際の不一致 — attendance/messagesのルートがまだ `protected` に入らないため)

- [ ] **Step 3: 最小実装(routes.py へ3エンドポイント)**

`backend/src/latch/latches/routes.py` — import部のschemas importへ6クラスを追記:

```python
from latch.latches.schemas import (
    AttendanceRequest,
    AttendanceResponse,
    LatchDetailEnvelope,
    LatchEnvelope,
    LatchListResponse,
    MessageEnvelope,
    MessageListResponse,
    MessageRequest,
    ResponseRequest,
)
```

`respond_to_latch` の後(ファイル末尾)へ3エンドポイント追加:

```python
@latches_router.post(
    "/{latch_id}/messages", response_model=MessageEnvelope, status_code=201
)
async def send_message(
    latch_id: uuid.UUID,
    body: MessageRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
) -> MessageEnvelope:
    """POST /v1/latches/{id}/messages(05 §5・matchedのみ書込可・承認事項⑦の201)。"""
    message = await svc.send_message(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        latch_id=latch_id,
        body=body.body,
    )
    return MessageEnvelope(message=message)


@latches_router.get("/{latch_id}/messages", response_model=MessageListResponse)
async def list_messages(
    latch_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MessageListResponse:
    """GET /v1/latches/{id}/messages(05 §5改頁共通規定・閲覧は状態を問わず)。"""
    items, next_cursor = await svc.list_messages(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        latch_id=latch_id,
        cursor=cursor,
        limit=limit,
    )
    return MessageListResponse(items=items, next_cursor=next_cursor)


@latches_router.post("/{latch_id}/attendance", response_model=AttendanceResponse)
async def submit_attendance(
    latch_id: uuid.UUID,
    body: AttendanceRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
) -> AttendanceResponse:
    """POST /v1/latches/{id}/attendance(D-09・3日以内・初回のみ受理)。"""
    return await svc.submit_attendance(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        latch_id=latch_id,
        attended=body.attended,
    )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_rate_limit_wiring.py tests/unit/latches -v`
Expected: PASS(レート制限ピン+latches全件。`make lint && make test` もこの時点でグリーンなことを確認)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/routes.py backend/tests/unit/test_rate_limit_wiring.py
git commit -m "feat: expose messages and attendance endpoints on latches router (M3 ws-4)"
```

### Task 8: マイグレーション0006(Index 2本)

**Files:**
- Create: `backend/alembic/versions/0006_chat_indexes.py`

**Interfaces:**
- Produces: DB Index `idx_messages_latch`(messages(latch_id, created_at))・`ux_calibration_latch`(calibration_records(latch_id) WHERE latch_id IS NOT NULL)。テーブル・カラム変更なし。**適用(upgrade)はスーパーバイザーのtest-ci時に行われる**(§0 — agent3は `alembic upgrade` を実行しない)

- [ ] **Step 1: マイグレーションファイルを作成**

`backend/alembic/versions/0006_chat_indexes.py`(0005と同型):

```python
"""messagesのlatch/created_at複合Indexとcalibration_recordsの部分UNIQUEを追加
(M3 ws-4・design §2.5)。

idx_messages_latch: GET /messages改頁のキーセット(latch_id絞り+created_at
昇順)。05 §3にmessagesのIndex規定がなく設計判断(supervisor承認事項⑥)。
ux_calibration_latch: attendanceの行特定を「latch_id→高々1行」と構造保証
(latches部分UNIQUE(0004)・group_candidates部分UNIQUE(0005)と同型)。
匿名化(D-13・ws-6)でlatch_idがNULLへ変わると対象外になるため部分UNIQUE。
05 §3への追記は次回docs改版候補(design §5-6)。

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX idx_messages_latch ON messages (latch_id, created_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_calibration_latch"
        " ON calibration_records (latch_id)"
        " WHERE latch_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_calibration_latch")
    op.execute("DROP INDEX IF EXISTS idx_messages_latch")
```

- [ ] **Step 2: 静的検証(DB接続なし)**

Run: `cd backend && uv run alembic heads && uv run alembic history | head -3`
Expected: heads が `0006 (head)` 単一・historyの先頭が `0006 -> 0005`。**`alembic upgrade` は実行しない**(§0)

Run: `make lint && make test`
Expected: グリーン(マイグレーションファイルはruff対象・unit試験への影響なし)

- [ ] **Step 3: コミット**

```bash
git add backend/alembic/versions/0006_chat_indexes.py
git commit -m "feat: add migration 0006 chat indexes (messages composite + calibration partial unique) (M3 ws-4)"
```

### Task 9: 統合試験17件(作成のみ・実行はスーパーバイザー)

**Files:**
- Create: `backend/tests/integration/test_chat_attendance_api.py`

**Interfaces:**
- Consumes: 実装済みの3エンドポイント(HTTP)・compose常設DB/Redis/api(スーパーバイザー検証時に稼働)。fixture・ヘルパーはws-1の `test_latches_api.py` と同型(SUBJECT_PREFIX=`m3ws4-`・teardownへmessages/blocks掃除を追加)

**重要**: 本Taskの試験は**実行しない**(§0 — compose常設環境への接続を伴うため)。`--collect-only` で17件が収集できることのみ検証する。壊れていたらスーパーバイザー検証で全waveが止まる — ヘルパー・SQL・期待値は本計画のコードをそのまま写し、独自の変更をしないこと。

- [ ] **Step 1: 試験ファイルを作成**

`backend/tests/integration/test_chat_attendance_api.py`:

```python
"""チャット+実施自己申告のintegration試験(M3 ws-4 design §4.2の17試験)。

実DB(compose常設)・実Redis・api常設(実HTTP)。users/intents/latches行は
fixtureで直接INSERT(パイプラインを走らせない・ws-1のtest_latches_api.py
と同型)。completed化・3日経過はDB値の直接UPDATE(api常設のClockは差し替え
不能なための等価置換・design §2.4)。時間値はすべてnow相対(タイムボム回避)。
teardownはFK順+blocks掃除(SUBJECT_PREFIX=m3ws4-)。
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
BASE_HOURS = 120
SUBJECT_PREFIX = "m3ws4-"


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        # FK順: messages → latch_status_events → calibration_records →
        # notifications → latches → (match/group/events) → blocks → intents → users
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
        json={"display_name": "m3ws4", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured(*, secondary: str | None = None, start: str | None = None) -> dict:
    return {
        "category": {"primary": CATEGORY, "secondary": secondary},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }


async def _intent(
    api_client, headers, *, secondary: str | None = None, start: str | None = None
) -> dict:
    resp = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={
            "raw_text": "分類用テキスト",
            "status": "active",
            "structured_intent": _structured(secondary=secondary, start=start),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]


async def _latch(
    db_engine,
    intent_ids: list[str],
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


async def _complete(db_engine, latch_id: str, *, hours_ago: float = 0.0) -> None:
    """matched→completed相当へDB直接UPDATE(completed_at=now相対・§2.4等価置換)。"""
    completed_at = SystemClock().now() - timedelta(hours=hours_ago)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET status = 'completed',"
                " completed_at = CAST(:c AS timestamptz)"
                " WHERE id = CAST(:l AS uuid)"
            ),
            {"c": completed_at, "l": latch_id},
        )


async def _calibration_row(db_engine, latch_id: str, intent_ids: list[str]) -> None:
    """calibration_records行を直接INSERT(actual_attended=NULL・design §4.2)。

    created_at/updated_atはnow-1h(attendance後のupdated_at進行検証用)。
    """
    now = SystemClock().now() - timedelta(hours=1)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO calibration_records
                    (latch_id, intent_ids, prediction, proposal_snapshot,
                     actual_responses, matched, created_at, updated_at)
                VALUES (CAST(:l AS uuid), CAST(:ids AS uuid[]),
                        CAST(:pred AS jsonb), CAST(:prop AS jsonb),
                        CAST(:resp AS jsonb), true,
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
            """),
            {
                "l": latch_id,
                "ids": [uuid_mod.UUID(i) for i in intent_ids],
                "pred": json.dumps({"placeholder": True}),
                "prop": json.dumps({"headcount": len(intent_ids)}),
                "resp": json.dumps([]),
                "now": now,
            },
        )


async def _block(db_engine, blocker_intent: str, blocked_intent: str) -> None:
    """blocks行を直接INSERT(ws-5を待たない・design §2.2/§4.2)。"""
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


async def _pair_eval(
    db_engine, a_id: str, b_id: str, *, wa: float, wb: float
) -> None:
    """match_candidatesへjev_result付きevaluatedを直接INSERT(試験17用)。"""
    jev = {
        "would_a_accept_b": wa,
        "would_b_accept_a": wb,
        "jev_5axis": {
            "purpose_fit": {"value": 0.5, "confidence": 0.9},
            "mood_fit": {"value": 0.5, "confidence": 0.8},
            "timing_fit": {"value": 0.5, "confidence": 0.7},
            "social_fit": {"value": 0.5, "confidence": 0.6},
            "latent_yes": {"value": 0.5, "confidence": None},
        },
        "provider": "typesafe_jev",
        "model": "jev-1.13.0",
    }
    now = SystemClock().now()
    lo_id, hi_id = sorted((a_id, b_id))
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO match_candidates
                    (intent_a_id, intent_b_id, intent_a_version,
                     intent_b_version, retrieval_score, cheap_judge_score,
                     jev_result, latch_score, status, created_at, updated_at)
                VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 1, 1,
                        0.9, 0.9, CAST(:jev AS jsonb), 0.85, 'evaluated',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                ON CONFLICT (intent_a_id, intent_b_id, intent_a_version,
                             intent_b_version) DO UPDATE
                SET jev_result = EXCLUDED.jev_result,
                    latch_score = EXCLUDED.latch_score,
                    status = 'evaluated', updated_at = EXCLUDED.updated_at
            """),
            {
                "a": uuid_mod.UUID(lo_id),
                "b": uuid_mod.UUID(hi_id),
                "jev": json.dumps(jev, ensure_ascii=False),
                "now": now,
            },
        )


async def _send(api_client, headers, latch_id: str, body: str):
    return await api_client.post(
        f"/v1/latches/{latch_id}/messages", headers=headers, json={"body": body}
    )


async def _msgs(api_client, headers, latch_id: str, **params):
    return await api_client.get(
        f"/v1/latches/{latch_id}/messages", headers=headers, params=params or None
    )


async def _attendance(api_client, headers, latch_id: str, attended: bool):
    return await api_client.post(
        f"/v1/latches/{latch_id}/attendance",
        headers=headers,
        json={"attended": attended},
    )


async def _cal(db_engine, latch_id: str) -> dict | None:
    async with db_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT actual_attended, cancelled_after, created_at,"
                        " updated_at FROM calibration_records"
                        " WHERE latch_id = CAST(:l AS uuid)"
                    ),
                    {"l": latch_id},
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


# -- 試験1: matchedで双方向送信・GET一覧 --


async def test_1_pair_chat_roundtrip(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    r1 = await _send(api_client, h1, latch, "はじめまして、よろしくお願いします")
    assert r1.status_code == 201, r1.text  # 承認事項⑦: POSTは201
    m1 = r1.json()["message"]
    assert set(m1) == {"id", "latch_id", "sender_id", "body", "created_at"}
    assert m1["latch_id"] == latch
    assert m1["body"] == "はじめまして、よろしくお願いします"
    r2 = await _send(api_client, h2, latch, "こちらこそ!")
    assert r2.status_code == 201
    listed = await _msgs(api_client, h1, latch)
    assert listed.status_code == 200
    body = listed.json()
    assert [m["body"] for m in body["items"]] == [
        "はじめまして、よろしくお願いします",
        "こちらこそ!",
    ]  # created_at昇順(引用#4)
    assert body["next_cursor"] is None


# -- 試験2: GET改頁 --


async def test_2_messages_pagination(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)  # 同一ユーザー2Intent(1対1作成用)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _send(api_client, h1, latch, "1通目")
    await _send(api_client, h1, latch, "2通目")
    p1 = await _msgs(api_client, h1, latch, limit=1)
    body1 = p1.json()
    assert len(body1["items"]) == 1
    assert body1["items"][0]["body"] == "1通目"
    assert isinstance(body1["next_cursor"], str)  # cursorは不透明(引用#4)
    p2 = await _msgs(api_client, h1, latch, limit=1, cursor=body1["next_cursor"])
    body2 = p2.json()
    assert body2["items"][0]["body"] == "2通目"
    assert body2["next_cursor"] is None  # 終端
    # limit超過は422(共通規定)
    over = await _msgs(api_client, h1, latch, limit=101)
    assert over.status_code == 422


# -- 試験3: matched以前への送信 --


async def test_3_send_before_match_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch_p = await _latch(db_engine, [i1["id"], i2["id"]], status="proposed")
    r = await _send(api_client, h1, latch_p, "x")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CHAT_READONLY"
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 12))
    i4 = await _intent(api_client, h1, start=_future(BASE_HOURS + 12))
    latch_pa = await _latch(db_engine, [i3["id"], i4["id"]], status="partial_accept")
    r2 = await _send(api_client, h1, latch_pa, "x")
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "CHAT_READONLY"


# -- 試験4: completed後の送信 --


async def test_4_send_after_completed_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    r = await _send(api_client, h1, latch, "x")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CHAT_READONLY"  # 引用#5


# -- 試験5: cancelled後の送信とGET --


async def test_5_send_after_cancelled_409_get_ok(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _send(api_client, h1, latch, "残る1通")
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE latches SET status = 'cancelled' WHERE id = CAST(:l AS uuid)"),
            {"l": latch},
        )
    r = await _send(api_client, h1, latch, "x")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CHAT_READONLY"
    listed = await _msgs(api_client, h1, latch)  # 閲覧は継続(引用#6)
    assert listed.status_code == 200
    assert [m["body"] for m in listed.json()["items"]] == ["残る1通"]


# -- 試験6: blocks双方向 --


async def test_6_block_both_directions_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _send(api_client, h1, latch, "ブロック前")
    await _block(db_engine, i1["id"], i2["id"])  # (A,B)のみ挿入
    ra = await _send(api_client, h1, latch, "x")
    assert ra.status_code == 409
    assert ra.json()["error"]["code"] == "CHAT_READONLY"
    rb = await _send(api_client, h2, latch, "x")  # (B,A)行は無い(引用#7)
    assert rb.status_code == 409
    assert rb.json()["error"]["code"] == "CHAT_READONLY"
    listed = await _msgs(api_client, h1, latch)  # 閲覧は可(引用#1)
    assert listed.status_code == 200
    assert [m["body"] for m in listed.json()["items"]] == ["ブロック前"]


# -- 試験7: 参加者以外 --


async def test_7_outsider_messages_403(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    outsider = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    r = await _send(api_client, outsider, latch, "x")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    listed = await _msgs(api_client, outsider, latch)
    assert listed.status_code == 403
    assert listed.json()["error"]["code"] == "FORBIDDEN"


# -- 試験8: 不在latch --


async def test_8_not_found(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    missing = str(uuid_mod.uuid4())
    r = await _send(api_client, h1, missing, "x")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    listed = await _msgs(api_client, h1, missing)
    assert listed.status_code == 404
    r3 = await _attendance(api_client, h1, missing, True)
    assert r3.status_code == 404


# -- 試験9: body検証 --


async def test_9_body_validation_422(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    for bad in ("", "   ", "\n\t", "あ" * 1001):
        r = await _send(api_client, h1, latch, bad)
        assert r.status_code == 422, bad
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    ok = await _send(api_client, h1, latch, " こんにちは ")
    assert ok.status_code == 201  # 前後空白入りもtrim後1字以上で受理(design §2.1)


# -- 試験10: attendance true --


async def test_10_attendance_true(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    await _calibration_row(db_engine, latch, [i1["id"], i2["id"]])
    r = await _attendance(api_client, h1, latch, True)
    assert r.status_code == 200, r.text
    assert r.json() == {"latch_id": latch, "actual_attended": True}  # 引用#9
    cal = await _cal(db_engine, latch)
    assert cal["actual_attended"] is True
    assert cal["cancelled_after"] is False  # 排他(引用#12・#13)
    assert cal["updated_at"] > cal["created_at"]  # 収集時更新で進む(引用#12)


# -- 試験11: attendance false --


async def test_11_attendance_false(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    await _calibration_row(db_engine, latch, [i1["id"], i2["id"]])
    r = await _attendance(api_client, h1, latch, False)
    assert r.status_code == 200
    assert r.json() == {"latch_id": latch, "actual_attended": False}
    cal = await _cal(db_engine, latch)
    assert cal["actual_attended"] is False
    assert cal["cancelled_after"] is True  # 引用#10・#13の対応


# -- 試験12: 先着確定(1対1の2人目・グループ3人の2人目) --


async def test_12_first_answer_wins(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    await _calibration_row(db_engine, latch, [i1["id"], i2["id"]])
    r1 = await _attendance(api_client, h1, latch, True)
    assert r1.status_code == 200
    r2 = await _attendance(api_client, h2, latch, False)  # 相手は上書けない
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "ATTENDANCE_ALREADY_SUBMITTED"
    cal = await _cal(db_engine, latch)
    assert cal["actual_attended"] is True  # 先着のまま(訂正不可)
    # グループ3人: 2人目も同じ
    hs = [await _user(api_client, field) for _ in range(3)]
    intents = [await _intent(api_client, api_h) for api_h in hs]
    ids = [x["id"] for x in intents]
    latch_g = await _latch(db_engine, ids)
    await _complete(db_engine, latch_g, hours_ago=1.0)
    await _calibration_row(db_engine, latch_g, ids)
    assert (await _attendance(api_client, hs[0], latch_g, False)).status_code == 200
    r3 = await _attendance(api_client, hs[1], latch_g, True)
    assert r3.status_code == 409
    assert r3.json()["error"]["code"] == "ATTENDANCE_ALREADY_SUBMITTED"


# -- 試験13: 3日窓(超過と境界) --


async def test_13_attendance_window(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=73.0)  # 3日+1時間: 閉じ
    await _calibration_row(db_engine, latch, [i1["id"], i2["id"]])
    r = await _attendance(api_client, h1, latch, True)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ATTENDANCE_WINDOW_CLOSED"
    # 境界(ちょうど3日)は受理: completed_atを now+10秒-72h へ置き、API実行時点
    # まで窓内を保つ等価置換(§2.4・api常設Clockは差し替え不能)
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 24))
    i4 = await _intent(api_client, h1, start=_future(BASE_HOURS + 24))
    latch2 = await _latch(db_engine, [i3["id"], i4["id"]])
    await _calibration_row(db_engine, latch2, [i3["id"], i4["id"]])
    edge = SystemClock().now() - timedelta(hours=72) + timedelta(seconds=10)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET status = 'completed',"
                " completed_at = CAST(:c AS timestamptz)"
                " WHERE id = CAST(:l AS uuid)"
            ),
            {"c": edge, "l": latch2},
        )
    r2 = await _attendance(api_client, h1, latch2, True)
    assert r2.status_code == 200, r2.text


# -- 試験14: 未completedへのattendance --


async def test_14_attendance_not_completed_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch_m = await _latch(db_engine, [i1["id"], i2["id"]])  # matchedのまま
    r = await _attendance(api_client, h1, latch_m, True)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "LATCH_CLOSED"
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 36))
    i4 = await _intent(api_client, h2, start=_future(BASE_HOURS + 36))
    latch_c = await _latch(db_engine, [i3["id"], i4["id"]], status="cancelled")
    r2 = await _attendance(api_client, h1, latch_c, True)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "LATCH_CLOSED"


# -- 試験15: 参加者以外のattendance(存在秘匿404) --


async def test_15_attendance_outsider_404(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    outsider = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    await _calibration_row(db_engine, latch, [i1["id"], i2["id"]])
    r = await _attendance(api_client, outsider, latch, True)
    assert r.status_code == 404  # messagesの403と扱いを分ける(引用#9)
    assert r.json()["error"]["code"] == "NOT_FOUND"


# -- 試験16: calibration行不在 --


async def test_16_calibration_missing_503(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    await _complete(db_engine, latch, hours_ago=1.0)
    # calibration_records行は作らない(ws-1の作成スキップと同根・承認事項⑤)
    r = await _attendance(api_client, h1, latch, True)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


# -- 試験17: E2E(02#19の本体): matched→双方向送信→completed→attendance --


async def test_17_chat_to_attendance_e2e(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    await _pair_eval(db_engine, i1["id"], i2["id"], wa=0.9, wb=0.9)
    latch = await _latch(db_engine, [i1["id"], i2["id"]], status="proposed")
    # 回答APIの実流れでmatched化+Calibration作成(ws-1実装)
    for h in (h1, h2):
        resp = await api_client.post(
            f"/v1/latches/{latch}/response", headers=h, json={"response": "yes"}
        )
        assert resp.status_code == 200, resp.text
    assert resp.json()["latch"]["status"] == "matched"
    # 双方向の送受信記録(引用#20)
    assert (await _send(api_client, h1, latch, "当日はよろしくお願いします")).status_code == 201
    assert (await _send(api_client, h2, latch, "こちらこそ!")).status_code == 201
    listed = await _msgs(api_client, h2, latch)
    assert len(listed.json()["items"]) == 2
    # 対象時刻経過→completed(sweeper相当をDB値で・§2.4)
    await _complete(db_engine, latch, hours_ago=0.5)
    assert (await _send(api_client, h1, latch, "x")).status_code == 409  # 送信关闭
    # 申告→respond経由で作られた実Calibration行へ反映
    r = await _attendance(api_client, h1, latch, True)
    assert r.status_code == 200, r.text
    cal = await _cal(db_engine, latch)
    assert cal["actual_attended"] is True
    assert cal["cancelled_after"] is False
```

- [ ] **Step 2: 収集検証(実行しない・§0)**

Run: `cd backend && uv run pytest tests/integration/test_chat_attendance_api.py --collect-only -q`
Expected: exit 0・**17 collected**(`test_1_pair_chat_roundtrip` 〜 `test_17_chat_to_attendance_e2e`。migrated_db fixtureは走らない)

- [ ] **Step 3: lint・unit再確認してコミット**

Run: `make lint && make test`
Expected: グリーン(unit 1119件。integrationはdeselected)

```bash
git add backend/tests/integration/test_chat_attendance_api.py
git commit -m "test: add chat+attendance integration suite (17 cases, supervisor-run) (M3 ws-4)"
```

### Task 10: 全体検証・報告ファイル

**Files:**
- Create: `docs/plans/M3/ws-4-report.md`

- [ ] **Step 1: 完了条件1〜6を検証(§6の検証コマンドを順に実行)**

```bash
make lint && make test                       # 期待: exit 0・1119 passed
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d   # 期待: 空
cd backend && uv run alembic heads           # 期待: 0006 (head) 単一
git diff --name-only main | sort             # 期待: §4の11ファイルと一致
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src
                                             # 期待: core/clock.py の行のみ
cd backend && uv run pytest tests/integration/test_chat_attendance_api.py --collect-only -q
                                             # 期待: 17 collected
```

§6の完了条件7(test-ci)は**実行しない**。報告書に「スーパーバイザー検証待ち」と記録する(§0規律)。

- [ ] **Step 2: 報告ファイルを作成**

`docs/plans/M3/ws-4-report.md` を§7の形式どおり作成(検証コマンドの出力末尾を証拠として貼る)。

- [ ] **Step 3: コミット**

```bash
git add docs/plans/M3/ws-4-report.md
git commit -m "docs: add M3 ws-4 execution report (M3 ws-4)"
```

- [ ] **Step 4: 最終確認**

```bash
git status --short          # 期待: 空
git log --oneline main..HEAD  # Task 1〜10のコミット一覧
```

---

## 9. 計画書セルフレビュー(機械チェック・スーパーバイザー指示による。2026-10-01実施)

### 9-1. 新規テストファイルのbasename衝突(運用ルール5)

確認コマンド(実行済み・main時点):

```bash
cd backend && find tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
# → 空(既存114ファイル・design §4.3と一致)
find tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | rg -c "test_chat"
# → 0(test_chat*は既存ゼロ)
```

本計画の新規3ファイル `test_chat_service.py`・`test_chat_store.py`・`test_chat_attendance_api.py` は既存と衝突しない。agent3はTask 10で再確認する(§6完了条件2)。

### 9-2. ピン試験への追随访問(触る全ピン試験の影響有無)

| ピン試験 | 触るか | 影響 |
|---|---|---|
| `tests/unit/test_rate_limit_wiring.py` | **追記する**(Task 7) | Counter期待値へ3エントリ(attendance 1・messages 2)。機械的・ルート列挙のアルファベット順該当箇所。ws-3も同ファイルへ追記するが両側保持マージで解消(§0) |
| `tests/unit/test_settings.py` | 触らない | 無傷。settings.pyへの変更なし(依存・設定キー追加なし・§4) |
| `tests/unit/llm/test_llm_factory.py` | 触らない | 無傷。LLM Gateway・外部SDKに無接触(§5) |
| `tests/unit/test_arch_no_direct_time.py` | 触らない | 無傷。追加コードはClock経由(`FakeClock`/`self._clock.now()`)のみで実時間参照なし。Task 10の§6-5でrg+arch testの再実行を検証に含む |
| `tests/unit/latches/test_latches_store_sql.py` | 触らない | **無傷だが走査対象が増える**: `test_all_sql_bind_params_fully_recognized` は `dir(store)` の大文字定数を走査するため、本単位の新規6定数が自動的にcompile検査対象へ入る。新SQLは既存bind param名(`:latch_id`・`:now`・`:limit`・`:ids`・`:me`)を再利用するため、既存トークン検査の保護がそのまま効く。新規トークン(`:others`・`:mid`・`:ct`・`:attended`・`:body`・`:sender_id`)の部分認識検査は `test_chat_store.py` の `test_ws4_sql_bind_params_fully_recognized` が担う |
| `tests/unit/latches/test_latches_service.py` | 触らない | 無傷。`LatchRow.completed_at` 追加は `_latch_row` を経由しないmonkeypatchスタブに影響しない(属性追加のみ・既存メソッドは参照しない) |
| `tests/unit/latches/test_latches_routes.py` | 触らない | 無傷。StubServiceは既存3メソッドのみ呼ぶ。新3ルートはルータ登録が増えるだけ |
| `tests/integration/test_latches_api.py` | 触らない | 無傷。`_SELECT_LATCH(_FOR_UPDATE)` へのcompleted_at列追加はSELECT列の追加のみで既存の応答・検証に触れない |

### 9-3. DB残存干渉の対抗策(ws-3/ws-4並走・共有ci-db)

- **subjectプレフィックス**: `m3ws4-`(fixtureが試験ごとに `m3ws4-{uuid8}-` を生成・ws-1の `m3ws1-` と同型)。ws-3と衝突しない
- **teardownはFK順+本単位テーブル**: messages → latch_status_events → calibration_records → notifications → latches → match_candidates → group_candidates → match_events → **blocks** → intents → users(Task 9のfield fixture。messages・blocksを掃除に追加 — design §4.2)
- **時間値はすべてnow相対**(タイムボム回避・M3 ws-1のtest_k_limits教訓): `_latch` のresponse_deadline/expires_at・`_complete(hours_ago=)`・`_calibration_row` のcreated_at/updated_at・試験13の境界completed_at(`now+10秒-72h`)はすべて `SystemClock().now()` 起点。固定時刻のリテラルは置かない(unit側のFakeClock基準NOWはfakeなので対象外)
- **残存確認SQL**(messages・calibration_records・blocks込み)は§7の検証手順に記載済み

### 9-4. 試験数の整合

| 種別 | main基準(2026-10-01スーパーバイザー指示値) | 本単位増分 | 期待合計 |
|---|---|---|---|
| unit(`make test`) | 1100 passed | **+19**(test_chat_service.py 12=Task 1の3+Task 2の3+Task 5の4+Task 6の2・test_chat_store.py 7=Task 3の4+Task 4の3。test_rate_limit_wiringは追記のみで件数不変) | **1119 passed** |
| test-ci(unit+integration) | 1301 passed | **+36**(unit 19+integration 17) | **1337 passed**(スーパーバイザー検証時・ws-3分は別加算) |

### 9-5. スペックカバレッジ・型整合(design §1.2の確定値→タスク対応・書き起こし後の点検)

- 引用#1〜#4(チャット書込可否・改頁規定)→ Task 3・5・7・9(試験1〜3)。cursorは2キー・昇順・不透明(引用#4)
- 引用#5・#6(completed/cancelled後の閲覧可・送信关闭)→ Task 5 unit全7状態+試験4・5
- 引用#7・#8(blocks双方向・テーブル既存・APIはws-5)→ Task 3 SQLピン+試験6。`select_block_between` をws-5差し替え点として明け渡し(§7引継ぎ)
- 引用#9〜#13(attendance応答形式・409×2・404・排他・updated_at契約)→ Task 4・6・9(試験10〜16)
- 引用#11・#14・#15(通知はws-2実装のまま・cancelledは対象外)→ **本計画はnotificationsを読み書きしない**(§2・§5)。sweeper無変更
- 引用#16〜#18(閉じたルーム・参加者認可・403/404)→ Task 5・6・9(試験7・8・15)
- 引用#19(ログ許可リスト)→ §2(okログなし・例外は固定文言・`latch.attendance.calibration_missing` はIDのみ)
- 引用#20(02#19双方向送受信)→ 試験17 E2E
- design §2.5(Index 2本)→ Task 8。design §2.6(ws-3接触はtest_rate_limit_wiringのみ・main.py無変更)→ §4・§5どおり
- 型整合点検: `MessageRow`(Task 3定義)はTask 5のsend_message戻り値・list_messages要素として同一形状。`CalibrationAttendanceRow.actual_attended: bool | None`(Task 4)と `is_attendance_window_open(completed_at: datetime | None, now)`(Task 2)のNone許容はTask 6の呼び出しと一致。`LatchRow.completed_at`(Task 4)はTask 6のみ消費。プレースホルダ(TBD/「適宜」等)なし — 全ステップに実コードを記載済み

### 9-6. 未解決論点の確認

design §5の7件はすべて2026-10-01スーパーバイザー承認済み(STATUS.md 311行目・§Spec)。本計画に落とし込めない未解決論点は**ない**。BLOCKED事項なし。



