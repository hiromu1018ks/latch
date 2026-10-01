# M3 ws-4 設計 — チャット+実施自己申告

作成: 2026-10-01(agent1)。参照仕様: 12 M3-6 / 05 §2・§5〜§6 / 09 §2.2・D-09 / 03 §5〜§6 / 08 §2.4・§2.5・D-23・第5節 / 02 §4(#19)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

matchedで解放されたチャットルームの送受信APIと、completed遷移後の実施自己申告APIを作る。ws-1がlatchesドメイン(回答・一覧・詳細)を、ws-2がmatched→completed遷移と申告通知の先行書き込みを実装済みであり、本単位はその間に残る「成立後の体験」のAPI面を埋める。APIの追加は3エンドポイントだけで、既存テーブルのカラム変更もないため、latchesモジュールへの追記とマイグレーション1本(Indexのみ)で完結する。

1. **チャットAPI**: `POST /v1/latches/{latch_id}/messages`(送信)・`GET /v1/latches/{latch_id}/messages`(取得・cursor改頁)
2. **実施自己申告**: `POST /v1/latches/{latch_id}/attendance`(D-09・3日以内・初回のみ受理)
3. **ブロック時409 CHAT_READONLY分岐**: blocksの双方向判定込みで本単位が先行実装する(§2.2。ws-5との分担)
4. **マイグレーション0006**: messagesのIndexとcalibration_recordsの部分UNIQUE(§2.5)

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | messagesはmatched以降のみ書き込み可。ブロック適用中のLATCH(08 D-23)は読み取り専用で、書き込みは409 CHAT_READONLY | 05 §2 |
| 2 | POST /v1/latches/{id}/messages: 認可は参加者・matched以降。ブロック適用中のLATCHは409 CHAT_READONLY(08 D-23) | 05 §5 |
| 3 | GET /v1/latches/{id}/messages: 参加者。ページネーション共通規定を適用 | 05 §5 |
| 4 | 改頁共通規定: `?cursor=&limit=`(limit 1〜100・既定20・超過は422)。応答は`{"items": [...], "next_cursor"}`。messagesの既定ソートはcreated_at昇順(会話の自然順)。cursorはサーバ生成の不透明文字列 | 05 §5 |
| 5 | 成立済みLATCHは対象時刻の経過でcompletedとなる。完了後も閲覧は可能とするが、チャットの新規送信は閉じる | 03 §6 |
| 6 | matched済みLATCHの参加Intent削除時もLATCHはcancelledで閉じ、チャットは読み取り専用化(新規送信不可。閲覧は可能。D-23と同じ線) | 08 §2.5 |
| 7 | blocksは単方向の記録で、マッチングは (A,B)(B,A) の双方向を確認。成立後の適用はD-23(進行中はcancelled、matchedはチャット読み取り専用化) | 05 §2・08 D-23・08 §5 |
| 8 | blocksテーブル・reports等のEntityは0001で作成済み。ブロックAPI・Redisキャッシュ・D-23成立後即時適用はM3-7(ws-5) | 05 §2・12 M3-7 |
| 9 | attendance: request `{"attended": true}` → 200 `{"latch_id": "…", "actual_attended": true}`。errorsは409 ATTENDANCE_ALREADY_SUBMITTED(二重回答・訂正不可)/ 409 ATTENDANCE_WINDOW_CLOSED(completed遷移から3日経過)/ 404(参加者以外) | 05 §5 |
| 10 | completed遷移後のLATCHに対し参加者が回答する。`attended: true`はcalibration_records.actual_attended=trueへ、`attended: false`はcancelled_after=trueへ反映。対象はcompletedのLATCHのみ・遷移から3日以内(超過は409)。回答は初回のみ受理し訂正不可(Ground Truthの純度のため)。無回答はAPIを叩かないことで表現し欠測として扱う | 05 §5・09 D-09 |
| 11 | 申告通知はcancelled LATCHには送らない。通知はプッシュ+アプリ内通知(お知らせ)でcompleted遷移時点に送る(実装済み・接続#3) | 09 D-09・08 §2.5 |
| 12 | calibration_recordsのactual_attended / cancelled_afterは収集時(D-09)に更新する経路がattendance API。updated_atはこの収集時更新で進む | 05 §2 |
| 13 | 実行率=actual_attended=trueの成立LATCH数 / actual_attendedの回答済み成立LATCH数(無回答は分母から除く)。actual_attended=trueとcancelled_after=trueは排他 | 09 §2.2 |
| 14 | matched→completedは対象時刻(time_start)の経過。cancelled LATCH(解散済み)はcompleted化対象外=申告通知を送らない | 05 §6・09 D-09 |
| 15 | 通知の先行書き込みはnotificationsへtype='attendance_request'・payload={latch_id}最小参照(媒体はws-3)。本単位はnotificationsを読み書きしない | M2 ws-6承認・ws-2実装 |
| 16 | チャット成立LATCH単位の閉じたルーム。matched時点で解放される(表示名・プロフィール・集合情報と同時) | 03 §6 |
| 17 | Intentの参照・回答は参加者のみに許可し、ブロックの判定はサーバ側で強制する | 05 §5(冒頭) |
| 18 | 403 FORBIDDENは認可違反(所有者・参加者以外の操作)・全API。404 NOT_FOUNDはリソース不在 | 05 §5 |
| 19 | 構造化ログの許可リスト: 個別Intentの文言を含めない。メッセージ本文・回答内容もログへ出さない(ws-1「status/codeのみ」と同型) | 08 §2.4 |
| 20 | 02#19「成立後にチャットを利用できる」: 成立LATCHでメッセージを送受信する → 双方向の送受信記録 | 02 §4 |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

| 資産 | 本単位からの使い方 |
|---|---|
| `latches/routes.py`・`service.py`・`store.py`・`schemas.py`・`errors.py`(ws-1) | 同一ドメインへの追記。`fetch_user_id`(未登録404)・`select_participant_intent`(参加者検査)・`select_latch_for_update`(FOR UPDATE読取)・`_wrap_unexpected`(503ラップ)・`decode_cursor`のcursor規約・エラーハンドラ(main.pyのLatchesError変換)をそのまま再利用 |
| `worker/sweeper.py`(ws-2) | matched→completed遷移・attendance通知先行書き込み(type='attendance_request')が実装済み。**無変更**(引用#11・#15の供給源。通知の読み取り・媒体はws-3) |
| `latches/calibration.py`(ws-1) | `build_prediction`等は無変更。本単位はcalibration_recordsの**更新**(actual_attended/cancelled_after)のみで、レコード作成(rejected/matched時)には触れない |
| latch_status_events | 本単位のAPIはLATCHの状態遷移を書かない(messages挿入・calibration更新のみ)。イベント表は既存遷移のまま |
| alembic 0001〜0005 | messages・calibration_records・blocksテーブルは0001で作成済み。本単位は0006を追加(Index・部分UNIQUEのみ・§2.5) |
| `tests/unit/test_rate_limit_wiring.py` | 全v1ルートの`api_rate_limited`ピンへ3行機械的追従(§4.3。M1 ws-5・M3 ws-1前例) |
| `latches_router`の`dependencies=[Depends(api_rate_limited)]` | 新エンドポイントも同一routerに生やし、レート制限を自動適用(60req/分・08 §5.4) |

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

- GET /v1/notifications・read・FCM実送信・attendance通知の媒体・文面 → **ws-3**(本単位はnotificationsテーブルに触れない。sweeperが書いた先行書き込み行の消費はws-3)
- blocks API(POST/DELETE /v1/users/{id}/block・一覧)・Redisキャッシュ・D-23成立後即時適用(進行中提案のcancelled化) → **ws-5**(本単位はmatched LATCHへの送信時の判定のみ・§2.2)
- チャット画面・成立済み詳細画面・「このチャットは利用できません」表示 → **ws-7**(03 第6節・08 D-23)
- 30日定期削除・calibration_records匿名化(D-13) → **ws-6**
- 02#19のstaging E2E → **ws-9**(本単位はci統合試験で本体を担保)

## 2. 実装方式の選択と推奨

### 2.1 チャットAPI — 書込可否は「status='matched'のみ」の単一条件に集約する

書き込み可否を決める規則は出典が3つに分かれる。05 §2は「matched以降のみ書き込み可」、03 §6は「completed後も閲覧可・新規送信は閉じる」、08 §2.5は「cancelled後も閲覧可・読取専用化」。この3つを突き合わせると、書込可能な状態は**status='matched'の間だけ**であり、completed・cancelled・ブロック適用中はいずれも「閲覧はできるが送信は閉じている」同じUX状態になる。

エラー応答の選択肢は2つある。

| 軸 | 案A(推奨): matched以外は全て409 CHAT_READONLY | 案B: 状態ごとにLATCH_CLOSED/CHAT_READONLYを使い分け |
|---|---|---|
| docsのエラー表との整合 | CHAT_READONLYの定義「ブロック適用中」からの拡大になる(§5-2) | 拡大なし。ただしcompleted後・cancelled後の送信に対応する行がエラー表に存在しない |
| クライアント分岐(ws-7) | 409 CHAT_READONLY一種で「入力を閉じる」処理にまとまる | 状態ごとの分岐が必要で、表示はどちらも同じになる |
| UX文言との整合 | 03 §6・08 §2.5の「読み取り専用」がそのままコードに対応する | 「読み取り専用」状態の一部だけがCHAT_READONLYになり対応が割れる |

案Aを推奨する。CHAT_READONLYを「チャットが読み取り専用状態への送信」の単一コードと解釈し、`status != 'matched'`(proposed・partial_accept・completed・cancelled・rejected・expired・candidate)とブロック適用中の両方に使う。05エラー表の発生箇所列はブロックに限記されているため、この拡大は解釈記録として§5へ置き、05次回改版での追記候補とする(§5-2)。

送信の手順は、ws-1のrespondと同じ直列化の型を踏襲する。

1. `fetch_user_id`: 未登録JWTは404(引用#18・接続資産と同型)
2. `select_latch_for_update`: 行ロック取得。なしは404
3. `select_participant_intent`: 参加者でなければ403(引用#17・#18)
4. `status != 'matched'` → 409 CHAT_READONLY(§2.1案A)
5. 参加者(自分以外)とのblocks双方向判定 → 引っかかれば409 CHAT_READONLY(§2.2)
6. `INSERT INTO messages`(id・latch_id・sender_id・body・created_at=Clock.now())
7. 201 `{"message": {"id", "latch_id", "sender_id", "body", "created_at"}}`

Clock.now()はFOR UPDATE取得後に採取する(ws-1のrespondと同一規律)。FOR UPDATEにより、sweeperのcompleted化(FOR UPDATE SKIP LOCKED)や回答APIとの競合では先着が決まり、completed化済みなら手順4が確実に409を返す。messagesテーブル自体にstatusを持たないため、INSERT単体での条件付き書き込みは置けず、行ロック→検査→INSERTの順で担保する。

取得(GET)は参加者ならstatusを問わず200を返す。matched以前の状態では行が存在しないため空リストになり、completed・cancelled後も閲覧が続く(引用#5・#6)。cursorはws-1のキーセット方式と同じ発想で`(created_at, id)`の2キー(base64url・昇順)、ソートは引用#4のcreated_at昇順。limitは共通規定どおり1〜100・既定20。

bodyの制約はdocsに規定がなく、実装定義とする(§5-3)。**1〜1000字(前後の空白を除いて1字以上・1000字以下。空白のみは422)**。Intentのraw_text300字(07 §4)ほど厳しくはなく、チャットの一文として十分な幅を持たせた値である。応答要素に表示名を含めないのは、matched/completedのLATCH詳細がparticipants(display_name入り・ws-1実装)を既に返すためで、フロントはそちらで名前を解決できる。

### 2.2 ブロック409 CHAT_READONLY分岐 — 本単位で先行実装する(推奨)

ブロック判定を本単位に含めるか、ws-5まで空けるか。判断軸は3つで、いずれも先行実装を支持する。

| 軸 | 案A(推奨): ws-4で先行実装 | 案B: ws-5待ち |
|---|---|---|
| 二度手間 | 判定をstore関数1つに隔離すれば、ws-5のキャッシュ差し替えは1箇所で済む | 条件付き検査を後から送信経路へ組み込む手戻りと、送信試験の期待値変更が発生 |
| G3追加試験(D-23成立後ブロック) | 受信側が先に確定し、ws-5はブロック登録側に集中できる | ws-5単位で送信側・登録側の両方を扱い、試験も大きくなる |
| 並行単位の独立性 | blocks行はfixtureで直接INSERTすれば試験可能(ws-1のlatches行fixtureと同型)で、ws-5の実装を待たない | ws-5の完了を待つ必要があり、waveの並列度が下がる |

実装はDB直読みとする。blocksの双方向判定(引用#7)は送信トランザクション内の1つのEXISTS検査で、自分以外の参加者全員との組み合わせを確認する(グループLATCHでは全ペアが対象)。

```sql
SELECT EXISTS (
    SELECT 1 FROM blocks
    WHERE (blocker_id = :me AND blocked_id = ANY(CAST(:others AS uuid[])))
       OR (blocker_id = ANY(CAST(:others AS uuid[])) AND blocked_id = :me)
)
```

Redisキャッシュは持たない(08 §5のキャッシュ参照はws-5のスコープ)。ws-5設計に対しては、本関数(`store.select_block_between`)を差し替え点として明け渡す。なお、D-23の「進行中(proposed/partial_accept)提案のcancelled化」はブロック登録の副作用でありws-5が担うため、本単位はmatched LATCHへの送信判定だけを持つ(引用#7・§1.4)。

### 2.3 実施自己申告 — 回答はLATCH単位で先着1名が確定させる

attendanceの最大の論点は「誰の回答か」である。1対1でもグループでも参加者全員が申告通知を受け取る(引用#11)が、calibration_recordsが持つのは**LATCH単位の**actual_attended / cancelled_afterのboolean 2つだけで(引用#12・05 §2)、参加者別の回答を保持する場所はdocsのどこにもない。05 §5は「回答は初回のみ受理し訂正不可」と書き、09 §2.2の分母も「回答済み**成立LATCH**数」とLATCH単位で数える。

以上から、**最初に回答した参加者の1タップでLATCHの記録が確定し、以降の回答(1対1の相手・グループの他メンバー)は409 ATTENDANCE_ALREADY_SUBMITTED**とする。「実際に会いましたか?」はLATCH単位の事実確認であり、最初の回答で確定しても集計上の矛盾は生じない。この解釈はグループ体験への影響が残るため、G3時確認事項への登録候補として§5-1に置く(参加者ごとの集約規則を将来導入するなら、actual_responses的な別テーブルを伴うdocs改版が必要)。

手順は単一トランザクションで、respondの型に対応させる。

1. `fetch_user_id`: 未登録JWTは404
2. `select_latch`(読取専用・FOR UPDATEなし): なしは404
3. `select_participant_intent`: 参加者でなければ**404**(引用#9の「404(参加者以外)」を明示どおり実装。response APIの403と扱いを分ける — attendanceは通知を受け取っていない可能性のあるユーザーにもAPIが見えるため、関与の有無自体を開示しない)
4. `status != 'completed'` → 409 LATCH_CLOSED(§5-4。matched〔対象時刻前〕・cancelled・それ以外の全状態。引用#10「対象はcompletedのLATCHのみ」)
5. `now > completed_at + 3日` → 409 ATTENDANCE_WINDOW_CLOSED。境界は**3日ちょうどまで受理**(`now <= completed_at + 3日`)。「遷移から3日以内」(引用#10)の以内を閉区間として読む
6. 条件付きUPDATE(原子性が排他の本体):

```sql
UPDATE calibration_records
SET actual_attended = :attended,
    cancelled_after = NOT :attended,
    updated_at = :now
WHERE latch_id = :latch_id AND actual_attended IS NULL
```

7. 影響0行は事前検査で分類する(ws-1のupdate_responseと同型)。事前SELECTで`actual_attended IS NOT NULL`なら409 ATTENDANCE_ALREADY_SUBMITTED、**行そのものがなければ503 DEPENDENCY_UNAVAILABLE+構造化ログ1行**(`latch.attendance.calibration_missing`。§5-5)

actual_attendedとcancelled_afterは常に同時に値が入る。true同士の重なりは入力がboolean 1つのため機械的に発生せず、引用#13の排他要件を構造で満たす。`attended: false`のときactual_attended=false・cancelled_after=true(引用#10・#13の対応そのまま)。応答は引用#9の形式どおり`200 {"latch_id": "…", "actual_attended": <attended>}`とし、cancelled_afterは応答に出さない。

「cancelledには通知しない」(引用#11・#14)はすでに実装済みである。sweeperのcompleted化対象抽出は`status='matched'`行に限られ、cancelled LATCHは通知書き込みから除外されている(接続#2)。API面でも手順4がcancelledを409で受け付けないため、docsの規定は両面で満たされる。本単位でnotificationsに新たな書き込みは行わない(§1.4)。

無回答の扱いに実装は不要である。スキップは「APIを叩かないこと」で表現され(引用#10)、3日窓の超過がそのまま無回答(欠測)になる。期限切れを検知して何かをするジョブは、09 §2.2の無回答率集計(M4-5 Observability)が担い、本単位の範囲ではない。

### 2.4 並行制御とClock

すべての時刻参照はClock経由(arch test C2・M0からの強制)。apiプロセスのClockは試験から差し替え不能のため、期限境界の試験はDB値を過去へ書き換える等価置換で行う(ws-1 §9-2・ws-2の確立済み手法)。タイムボム(M3 ws-1のtest_k_limits教訓)を避けるため、completed_atを固定時刻で置く試験は作らない(now相対値で設定する)。

並行の観点まとめ:

- **送信**: latch行のFOR UPDATEがsweeper(completed化)・回答API・削除Event処理(cancelled化)と直列化する。検査とINSERTの間に状態は動かない
- **attendance**: calibrationの条件付きUPDATEが二重回答の排他の本体。latch行は読取のみ(completedは終端状態で、以降の遷移は遷移表上ない。行ロック不要)。先着2リクエストの競合は影響0行→409で自然に解決
- **GET messages**: 読取専用でロック不要

### 2.5 マイグレーション0006(Index 2本・テーブル変更なし)

| 追加 | 定義 | 根拠 |
|---|---|---|
| idx_messages_latch | `CREATE INDEX ... ON messages (latch_id, created_at)` | GET改頁のキーセット(latch_id絞り+created_at昇順)。05 §3にmessagesのIndex規定がなく設計判断(§5-6) |
| ux_calibration_latch | `CREATE UNIQUE INDEX ... ON calibration_records (latch_id) WHERE latch_id IS NOT NULL` | attendanceの行特定を「latch_id→高々1行」と構造保証する。latches部分UNIQUE(0004)・group_candidates部分UNIQUE(0005)と同型。匿名化(D-13・ws-6)でlatch_idがNULLへ変わると対象外になるため部分UNIQUEとする |

テーブル・カラムの追加はなく、0001のmessages・calibration_recordsをそのまま使う。マイグレーション追加単位である本単位は、STATUS運用ルール1・2(共有ci-dbのtest-ci/migrateはDB消費単位〔ws-3〕と同時実行しない・検証はスーパーバイザーが直列実行)の対象になるため、計画書の報告形式にこの旨を記載する。

### 2.6 ws-3並行とのファイル接触(最小)

ws-3(通知)と触れうるファイルは2つで、いずれも両側追記保持で解消できる(M1 ws-2のsettings.py前例)。

- `tests/unit/test_rate_limit_wiring.py`: ルート別期待値へ3行(messages 2・attendance 1・§4.3)
- `backend/tests/integration/`: 本単位の新規ファイル(§4.2)。conftest.py・既存試験ファイルには触れない

新エンドポイントはlatches_router(prefix・タグ・レート制限依存ごと登録済み)への追記で成立するため、**main.pyは無変更**である(lifespanのservice構築も既存のまま)。

latchesドメイン本体(routes/service/store/schemas/errors)とworker・notifications系はws-3と重ならない。

## 3. ファイル構成

| 対象 | 操作 | 内容 |
|---|---|---|
| `src/latch/latches/routes.py` | 変更 | POST/GET `/{latch_id}/messages`・POST `/{latch_id}/attendance`の3エンドポイント追記(既存latches_router・レート制限依存はそのまま) |
| `src/latch/latches/schemas.py` | 変更 | `MessageRequest`(body: str)・`MessageOut`(id/latch_id/sender_id/body/created_at)・`MessageEnvelope`・`MessageListResponse`(items/next_cursor)・`AttendanceRequest`(attended: bool)・`AttendanceResponse`(latch_id/actual_attended) |
| `src/latch/latches/service.py` | 変更 | `send_message`・`list_messages`・`submit_attendance`の3ユースケース(§2.1・§2.3の手順。`_wrap_unexpected`で503ラップ) |
| `src/latch/latches/store.py` | 変更 | SQL追記: messages挿入・改頁選択・blocks双方向判定・calibration条件付きUPDATE・calibration事前読取。cursorのencode/decodeはserviceの既存パターンに対応する2キー版 |
| `src/latch/latches/errors.py` | 変更 | `ChatReadonlyError`(409 CHAT_READONLY)・`AttendanceAlreadySubmittedError`(409)・`AttendanceWindowClosedError`(409)の3クラス(既存階層に準拠) |
| `alembic/versions/0006_*.py` | 新規 | §2.5のIndex 2本 |
| `tests/unit/latches/test_chat_service.py` | 新規 | 純計算のunit(§4.1) |
| `tests/unit/latches/test_chat_store.py` | 新規 | SQL compile検査・blocks判定SQLのdialect検査(§4.1) |
| `tests/integration/test_chat_attendance_api.py` | 新規 | 統合試験17件(§4.2) |
| `tests/unit/test_rate_limit_wiring.py` | 変更 | 期待値へ3行(機械的追従) |
| `worker/`一式(sweeper・latch_engine・reeval) | 触らない | completed化・通知先行書き込みはws-2実装のまま |
| `src/latch/main.py` | 触らない | latches_routerは登録済み。service構築も既存lifespanのまま(§2.6) |
| `latches/calibration.py` | 触らない | 作成経路は無変更(本単位は更新のみ) |
| `intents/`・`users/`・`geo/`・notifications読み書き経路 | 触らない | ws-3・ws-5・ws-6の領域 |
| `frontend/`・`prototype/` | 触らない | ws-7の領域(チャット画面は未実装・03 §10) |

## 4. テスト方針

### 4.1 unit(スタブで確定できるもの)

- **test_chat_service.py**: 窓判定の境界(`now == completed_at + 3日`は受理・+3日+1秒は409)・status分類(matched/completed/cancelled→各409の種別)・cursor 2キーのencode/decode往復と形式不正422・body検証(0字・空白のみ・1001字→422、1000字・改行込み→受理)
- **test_chat_store.py**: 新規SQLの実dialect compile検査(bind paramのリテラル落ち防止・M1 ws-3の教訓)・blocks判定SQLの構成ピン

api常設・実DBを扱う動作本体はintegrationに置く(ws-1と同じ振り分け)。

### 4.2 integration(test_chat_attendance_api.py・17試験)

実DB(compose常設)・実Redis・api常設(実HTTP)。users/intents/latches行はfixtureで直接INSERTし、パイプラインを走らせない(ws-1のtest_latches_api.pyと同型。SUBJECT_PREFIX=`m3ws4-`)。teardownはFK順(messages→latch_status_events→calibration_records→notifications→latches→intents→users)にLIKE接頭辞で全削除し、blocksも掃除に加える。completed化はDB値の直接UPDATE、3日経過はcompleted_atを過去へUPDATE(§2.4の等価置換)。

| # | 試験 | 期待値 |
|---|---|---|
| 1 | matchedで送信(双方向2件)・GET一覧 | 201が2件・created_at昇順・応答要素はid/latch_id/sender_id/body/created_at |
| 2 | GET改頁(limit=1で2頁+終端null) | next_cursor経由で全件取得・cursorは不透明 |
| 3 | proposed・partial_acceptへの送信 | 409 CHAT_READONLY |
| 4 | completed後の送信 | 409 CHAT_READONLY(引用#5) |
| 5 | cancelled後の送信とGET | 409 CHAT_READONLY・GET閲覧は可(引用#6) |
| 6 | blocksに(A,B)挿入後にA送信・B送信(双方向) | どちらも409 CHAT_READONLY・GET閲覧は可(引用#1・#7) |
| 7 | 参加者以外のPOST/GET messages | 403 FORBIDDEN(引用#17・#18) |
| 8 | 不在latchへのmessages・attendance | 404 NOT_FOUND |
| 9 | body検証(0字・空白のみ・1001字) | 422 VALIDATION_ERROR |
| 10 | attendance true | 200・calibrationのactual_attended=true・cancelled_after=false・updated_atが進む(引用#9・#12) |
| 11 | attendance false | 200・actual_attended=false・cancelled_after=true |
| 12 | 先着確定(1対1で2人目が回答・グループ3人で2人目) | 409 ATTENDANCE_ALREADY_SUBMITTED(§2.3・§5-1) |
| 13 | 3日経過(completed_atを72時間+前にUPDATE)・境界(ちょうど3日はDB値で再現) | 409 ATTENDANCE_WINDOW_CLOSED / 境界は受理(§2.3手順5) |
| 14 | status=matched・cancelledへのattendance | 409 LATCH_CLOSED(§2.3手順4) |
| 15 | 参加者以外のattendance | 404(引用#9・存在秘匿) |
| 16 | calibration行をDELETEしてattendance | 503 DEPENDENCY_UNAVAILABLE(§2.3手順7) |
| 17 | 送信からattendanceまでのE2E(02#19の本体): matched→双方送信→completed化→attendance | 双方向の送受信記録と申告反映(引用#20) |

blocksのfixture直接INSERTはws-5を待たない(§2.2)。attendance通知の先行書き込み自体はws-2試験で担保済みのため、本単位ではnotificationsを検証しない。

### 4.3 機械チェック(計画書レビュー時の確定事項)

- **basename一意**: 新規3ファイル(test_chat_service.py・test_chat_store.py・test_chat_attendance_api.py)が既存114ファイル(2026-10-01時点・重複ゼロ確認済み)と衝突しないことを`find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d`が空であることで確認
- **ピン試験の追随访問**: test_rate_limit_wiring.pyへは`/v1/latches/{latch_id}/messages`: 2(POST+GET)・`/v1/latches/{latch_id}/attendance`: 1の3行追従(機械的・M3 ws-1前例)。既存ピン(test_llm_factory等)への影響なし(settings変更なし・依存追加なし)
- **DB干渉対抗策**: m3ws4-プレフィックス+FK順teardown・completed_at等の時間値はnow相対(タイムボム回避)・messages/blocksをteardownに追加
- **試験数の整合**: 現行test-ci 1301 passedに対する増分(unit+integrationの合計)を計画書に記載

## 5. 未解決の論点(supervisor承認事項・G3時確認候補)

いずれも設計としては§2の推奨で確定させており、実装を保留した箇所はない。承認時に確認を求める論点と、docs改版を要するものを分けて列挙する。

1. **【最重要】attendanceの回答単位はLATCH単位・先着1名で確定**(§2.3)。グループLATCHでは最初に回答した1人の回答がLATCH全体の記録になる。05 §5「初回のみ受理」とcalibration_recordsの構造(参加者別の回答保持場所なし)からの演繹だが、「参加者全員が通知を受け、1人しか回答できない」体験面の影響が残る。参加者ごとの回答集約を将来導入するならschema拡張を伴うdocs改版が必要。**G3時確認事項への登録を推奨**
2. **CHAT_READONLYの適用範囲拡大**(§2.1)。completed後・cancelled後の送信にも409 CHAT_READONLYを使う。05 §5エラー表の発生箇所(「ブロック適用中」)からの拡大であり、**05次回改版でエラー表の発生箇所列を更新する候補**(03 §6・08 §2.5の「読み取り専用」統一が根拠)
3. **messages本文は1〜1000字・空白のみ不可(422)**(§2.1)。docsに規定のない実装定義。05 §5にmessagesのrequest形式が未記載のため、**あわせて次回改版での追記候補**
4. **status != completedへのattendanceは409 LATCH_CLOSED**(§2.3手順4)。LATCH_CLOSEDの語義(05 §5では「競合クローズ後」)を「受付可能状態でない閉状態」へ拡大する読み。matched(対象時刻前)への早すぎる申告もこれに含む
5. **calibration行不在時は503+構造化ログ**(§2.3手順7)。ws-1のCalibration作成スキップ(評価行特定失敗)と同根の稀ケース。代替の「200で受理してログのみ」は05 §2のupdated_at契約と応答の真実性を崩すため採らない。発生時は集計分母から1件落ちるが、無回答と区別可能なログを残す
6. **マイグレーション0006のIndex 2本**(§2.5)。05 §3に規定がないため設計判断。**05 §3への追記候補**
7. **POST messagesの応答は201**(§2.1)。05 §5に応答例がなく、リソース作成の慣例(POST /v1/intentsの201)に準拠した

## 6. 完了条件(本単位の検証対象)

- チャット: matchedでのみ送信でき(201)・双方向の送受信記録がGETで昇順取得できる。proposed/partial_accept/completed/cancelled・ブロック適用中は409 CHAT_READONLY。閲覧は継続できる(02#19の本体)
- attendance: completedかつ3日以内のLATCHに参加者が回答でき、actual_attended/cancelled_afterが排他で反映される。二重回答(2人目含む)は409 ATTENDANCE_ALREADY_SUBMITTED・3日超過は409 ATTENDANCE_WINDOW_CLOSED・参加者以外は404
- test-ciがマージ後mainでグリーン(スーパーバイザー検証・api再ビルド後)。alembic head=0006。残存ゼロ(m3ws4-・全テーブル)。lint緑・basename一意
