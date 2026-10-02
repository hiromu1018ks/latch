# M3 m3-g3fix 設計 — G3承認前の修正2件(お知らせmy_response+成立通知)

作成: 2026-10-02(agent1)。参照仕様: 03 §4・§6・§7 / 05 §2・§5・§6 / 06 §6・§10 / 08 §2.2・§2.6 / 01 第46行付近。対象はG3レビューで指摘された2件(オーナー裁定2026-10-02「B: 両方直す」)。**マイグレーションなし・notificationsテーブル構造不変・docs本体は触らない**(§5改版候補として整理)。

- **修正A(⑫解消)**: GET /v1/notifications応答のlatch要素へ`my_response`を追加し、お知らせ終了行を自分の操作履歴文言へ分岐させる(03 §7「自分の操作によるものは自身の操作履歴として表示」のフロント実現。現状はws-8解釈⑫により一律CLOSED_TEXT)
- **修正B(③解消)**: 提案成立(partial_accept→matched遷移)時の通知を追加する(05 §6遷移表はこの遷移の通知を規定せず、01導入文「成立時にだけ通知が来る」と矛盾。通知あり側で解消する裁定=ws-3解釈③の解除)

## 1. 目的と前提となるdocsの確定値

| # | 確定値 | 出典 |
|---|---|---|
| 1 | 提案が閉じる表示は2種類に集約 — 「自分の操作によるもの(NO・見送り・Intentの停止削除): 自身の操作履歴として表示」「それ以外: 『この提案は成立しませんでした』の一文で統一」。相手の回答種別・ブロック事実は一切開示しない | 03 §7 |
| 2 | 通知は3層(プッシュ=汎用文/アプリ内=条件サマリ表示)。プッシュ本文は汎用文のみとし条件サマリ(人数・時間帯・地域名・カテゴリ・予算)を含めない。この規制はプッシュ許可の有無と独立 | 03 §4・08 §2.6 |
| 3 | 通知のタイミングは双方(グループでは必要人数全員)への同時送信 | 03 §4 D-20 |
| 4 | 提案通知の頻度上限は1ユーザー日6件(nearby_alsoの存在通知を含む)。**上限の対象は「提案通知」である** | 03 §4 D-08・06 §10 |
| 5 | mutedのIntentは提案通知を送らない。それ以外の挙動は通常どおり(proposed遷移等) | 06 §6 |
| 6 | 遷移表: partial_accept→matched のトリガーは「必要人数全員がyes」(**通知の規定なし=改版候補**)。matched→completed は「遷移時に実施自己申告の通知を送る」 | 05 §6 |
| 7 | 成立後はチャット・表示名・最小限プロフィール・集合情報・次アクション(チャット開始の誘導)を解放する | 03 §6 |
| 8 | 必要人数のYES(承認)が揃った時点で、条件サマリ(未解放の場合)・表示名・プロフィール・チャット・集合情報を解放する | 08 §2.2 |
| 9 | 「条件付きの意思をLATCHに置いて、あとは待つ」「預けておけば、成立時にだけ通知が来る状態を作る」 | 01 第46行付近 |
| 10 | GET /v1/notificationsはcursor方式・既定ソートcreated_at降順(limit既定20) | 05 §5 |
| 11 | latches.responsesは構造化配列(`{user_id, intent_id, response: yes/no/defer, answered_at}`)。回答種別は記録として区別保持 | 05 §2・§7・03 D-07 |
| 12 | タップの遷移先はプッシュ・アプリ内通知のいずれからでも提案詳細画面へ(バナー上の直接回答ボタンなし) | 03 §4 FR-46 |
| 13 | attendance_request通知(completed遷移時)は参加者全員へ先行書き込み・D-08日次上限は消費しない | 05 §6・ws-3解釈④(G3時確認事項4) |

## 2. 実装方式の選択と推奨

### 2.1 修正A: my_responseの計算方法(バックエンド)

**案A(推奨): 一覧SELECTへ`l.responses`を追加し、Pythonで抽出。**
`_SELECT_NOTIFICATIONS_PAGE`(notifications/store.py)のSELECTへ`l.responses`を1列追加する。応答組み立て(notifications/service.py)で`_my_response(l_responses, user_id)`を計算する — latches/service.py:539の同名privateヘルパーと同一ロジックの3行をnotifications/service.pyへ置く(private関数のimportはモジュール規律に反するため複製。両者の同期は試験が担保、詳細は§4)。

- フィールド名・位置: **`NotificationLatchOut.my_response: str | None = None`**(latches APIの`LatchSummaryOut.my_response`と同名・同位置)。フロントの`closedText(latch)`(view.js)が`latch.my_response`を参照するため、**ws-7詳細画面と同じ関数・同じ値でそのまま動き、文言の一貫が構造的に担保される**
- 値域: `"yes" / "no" / "defer" / None`(未回答、またはlatch行不在=latchブロック全体がnull)。latches APIと同一
- LEFT JOIN不成立行(latch=null)は`latch`ブロックごとnullのためmy_responseも出ない(既存の防御構造不変)

トレードオフ: 1ページ20行×参加者4人程度のresponses配列をDBから読むが、latches一覧API(`select_latches_page`)が既に全行responsesを読んで`_my_response`で抽出する同一構造であり、新規の性能懸念はない。

**案B(不採用): SQL側でjsonb配列から相関サブクエリ抽出。** ネットワーク転送は減るが、LEFT JOIN+相関サブクエリでSQLが複雑化し、latches側のPython抽出と判定経路が2系統に分かれる。一貫性を欠く。

**案C(不可): 通知書き込み時にpayloadへmy_responseを保存。** proposal通知の先行書き込みは回答より前に行われるため、書き込み時点で自分の回答は常に存在せず常にnullになる。時間的に成立しない。

### 2.2 修正A: フロント文言分岐

`notificationLines`(view.js)の終了行分岐を`return [CLOSED_TEXT]`から`return [closedText(latch)]`へ変更する。`closedText`はws-7詳細画面(renderClosed・detail.js:229)が使う既存の純関数で、次の分岐を持つ:

| my_response | 文言(ANSWERED_TEXT/texts.js) |
|---|---|
| `"no"` | 「辞退しました」 |
| `"defer"` | 「今回は見送りました」 |
| それ以外(None・yes) | CLOSED_TEXT「この提案は成立しませんでした」 |

- Intent停止削除によるcancelled(03 §7の3つ目の「自分の操作」)はmy_responseからは判定できずCLOSED_TEXTのままになるが、**これはws-7詳細画面と同一の挙動・同一の限界**であり(詳細側もmy_responseのみで分岐)、⑫の修正範囲(my_responseによる分岐)を詳細画面と一貫させる本設計の要請どおり
- matched(自分yes)はそもそも終了行でなく成立系分岐に入るため影響なし
- latch=null防御行・nearby・attendance_request行は不変

### 2.3 修正B: 通知type・文面・上限

- **type: `latch_matched`**(types.py正本へ`NOTIFICATION_MATCHED = "latch_matched"`を追加。既存3値 proposal / nearby_candidate / attendance_request と同じスネークケース)。attendance_requestへの流用はしない(成立連絡と実施依頼は別事象であり、記録・試験・将来の文面A/Bで分離できないため)
- **プッシュ本文: 第三の汎用文を新設**(`PUSH_BODY_MATCHED = "LATCHが成立しました。\nチャットでメッセージを確認しましょう。"`)。build_push(notification_type)は**引数・構造とも不変**(条件サマリを本文へ混ぜる経路が型の上で存在しないという08 §2.6の構造担保を踏襲)。成立事実の連絡は条件サマリではなく、表示規制と両立する。文面は文言案として§6でオーナー確認
- **アプリ内文言(お知らせ行)**: 見出し「LATCHが成立しました。」+チャット導線+statusバッジ(詳細は下記マトリクス)。導線文言は成立後詳細画面(ws-7)の`NEXT_ACTION_TEXT`「チャットで挨拶を交わしましょう」を流用する(成立後体験の文言単一ソース)。**条件サマリは成立通知行に含めない** — hidden_until_matchだった提案ではlatches.proposalが最小構造のまま(生成時の不変値)であり表示可否がlatchごとに分かれる。成立の要点は見出しと導線であり、サマリは元の提案通知行(成立後はバッジ「成立済み」付きで条件サマリを保持するws-8解釈⑬の挙動)と詳細画面が既に担っている
- **D-08日次上限: 消費しない**。上限カウントSQL `_COUNT_DAILY_NOTIFICATIONS` は`type IN ('proposal', 'nearby_candidate')`で対象を絞っており、latch_matchedは対象外=SQL変更なし。03 §4 D-08の上限対象は「提案通知」であり、attendance_request=不消費のws-3解釈④と同型の解釈
- **muted: 適用外(全参加者へ送る)**。06 §6のmuted規定は「提案通知を送らない」に限定的で、attendance_request通知が参加者全員へ送る現行実装と同型。成立通知は提案供給ではなく契約成立の連絡であり、通知レベル設定(提案の受け取り方)の対象外と読む

### 2.4 修正B: 通知生成の挿入箇所とプッシュ送信経路

**挿入箇所: `LatchesService.respond()`のトランザクション内、`_on_matched`(参加Intent matched化+競合クローズ)の直後。** partial_accept→matched遷移を書く経路は回答APIのみ(05 §6遷移表。`update_response`のUPDATEを発行するのはrespondのみ、sweeper・latch_engine・deletionはmatchedを書かない — 実装確認済み)。したがってrespondへの挿入で経路の抜けは構造的にない。

- 宛先は**参加者全員(最後にYESを入れた本人を含む)**。03 §4 D-20の同時送信とattendance_requestの現行実装(全員へ)に合わせる。自分が成立させたLATCHもお知らせに並ぶことは自然な記録であり、グループ成立では一括して全員へ書く
- INSERTはlatches/store.pyへ追加(`INSERT INTO notifications ... payload={"latch_id"}`の最小参照 — latch_engine._INSERT_NOTIFICATION・sweeper._INSERT_ATTENDANCE_NOTIFICATIONと同一SQL形。各モジュールがtext()生SQLを持つ既存慣行)。宛先取得も同一tx内で`SELECT user_id FROM intents WHERE id = ANY(intent_ids)`(sweeperの_SELECT_LATCH_PARTICIPANT_USERSと同一形)
- **プッシュ送信はtxコミット後**(ws-3 design §2.1案A「コミット前に送るとnotifications行がないままプッシュが飛び得る」の慣行)。`LatchesService`へ`push: PushSender | None = None`を注入し、respondのtx抜け後に全員へ`send(notification_type=NOTIFICATION_MATCHED)`。Noneならno-op(sweeper・latch_engineと同型・既存試験構成との互換)
- `make_latches_service`へpush引数を追加し、main.pyのlifespanで`build_push_sender(clock, settings)`を構築して渡す(push_mode設定はAPIプロセスでも有効。stubはネットワーク呼び出しゼロ・例外を握る設計でAPI応答への影響は送信先障害時のtimeout 3秒上限のみ)
- 競合クローズされた側(他LATCH成立でcancelled)へは通知しない — 不成立理由の通知はdocs規定がなく、03 §7の一覧・既存のstatus遷移表示のまま(今回の範囲外)

**対比: 案B'(不採用) sweeper/worker側のイベント観測で送る。** APIプロセスから送信I/Oを排除できるが、(a)「matchedイベント観測→送信」の新規経路と既送信管理が必要になり複雑化、(b)成立〜通知が最大60秒遅れる。成立通知は01導入文が約束する体験の核心であり、sweeperのcompleted通知と同じ「遷移を書くtx内で先行書き込み・コミット後送信」の対称な構造をrespondに置くのが最も一貫する。

### 2.5 フロントマトリクスの拡張(notificationLines)

`notificationLines`(view.js)へ`type === "latch_matched"`分岐を追加する(nearby/attendanceと同じtype分岐の先頭グループへ)。

| 条件 | 表示行 |
|---|---|
| type=latch_matched・latch=null | NOTICE_FALLBACK_TEXT(防御・既存と同型) |
| type=latch_matched・latch.statusがexpired/rejected/cancelled(解散後) | CLOSED_TEXT(一文統一・03 §7) |
| type=latch_matched・matched | [「LATCHが成立しました。」, 「チャットで挨拶を交わしましょう」, バッジ「成立済み」] |
| type=latch_matched・completed | [「LATCHが成立しました。」, 「チャットで挨拶を交わしましょう」, バッジ「完了」] |

- バッジはSTATUS_TEXT流用(ws-8解釈⑬と同型)。completed後はattendance_request行が別に出るため導線文言の重複懸念は薄い(バッジで区別可能)
- **`notificationHref`は変更不要** — 現行実装は「nearby以外・latchがあれば`#/latches/{id}`へリンク」であり、latch_matchedはこの条件に自動的に当てはまる(03 §4 FR-46の詳細画面遷移・成立後詳細にはチャットがある)
- 既存行(type=proposal の matched/completed 後のバッジ表示=ws-8解釈⑬)は不変。成立通知行と提案通知行は別行として並び、提案行が成立後の姿を、成立通知行が「成立したこと+導線」を担う

## 3. ファイル構成

**作るもの・触るもの**

| ファイル | 変更 |
|---|---|
| backend/src/latch/notifications/store.py | `_SELECT_NOTIFICATIONS_PAGE`へ`l.responses`追加・`LatchJoinedRow`へ`responses`フィールド(default_factory=list=既存unit試験のfixture互換) |
| backend/src/latch/notifications/schemas.py | `NotificationLatchOut`へ`my_response: str \| None = None` |
| backend/src/latch/notifications/service.py | `_my_response`ヘルパー・list()の応答組み立てへmy_response |
| backend/src/latch/notifications/types.py | `NOTIFICATION_MATCHED = "latch_matched"` |
| backend/src/latch/notifications/templates.py | `PUSH_BODY_MATCHED`定数・build_pushへlatch_matched分岐 |
| backend/src/latch/latches/store.py | 通知INSERT用SQL・参加者user_id取得SQL(いずれもsweeperと同一形) |
| backend/src/latch/latches/service.py | respond()のmatched分岐へ通知先行書き込み+tx後プッシュ送信・`__init__`へpush注入 |
| backend/src/latch/main.py | lifespanでbuild_push_senderを構築しmake_latches_serviceへ注入 |
| frontend/src/latch/view.js | notificationLinesの終了行をclosedText(latch)へ・latch_matched分岐追加 |
| frontend/src/latch/texts.js | MATCHED_NOTICE_TITLE新設(見出し文言) |
| backend/tests/unit/notifications/test_notifications_service.py | my_response抽出の試験追加・fixture拡張 |
| backend/tests/unit/notifications/test_templates.py | latch_matched文面試験 |
| backend/tests/unit/latches/test_latches_service.py | matched時の通知INSERT呼び出し(store差し替え)・push送信 |
| backend/tests/integration/test_notifications_api.py | 1対1成立E2E(双方yes→latch_matched通知+プッシュ記録) |
| frontend/tests/latch-notice.test.js | 終了行my_response分岐3ケース・latch_matched行の文言 |

**触らないもの**: マイグレーション(テーブル構造不変・payloadは既存`{"latch_id"}`最小参照)/ latch_engine・sweeper(提案・近傍・申告の各通知は不変)/ notice.js(行のHTML組み立てはnotificationLinesのみに依存)/ respond.js・detail.js・home.js(closedTextの一元化により変更不要)/ notifications/routes.py・sender.py・records.py(応答モデル・送信IF・記録は不変)/ docs本体(§5参照)

## 4. テスト方針(既存試験の影響調査を含む)

**新設試験**

1. unit/notifications(test_notifications_service.py): my_response抽出 — responsesへ自分のno/defer/yesが含まれる行・未回答(None)・latch=null行。notifications側の`_my_response`はlatches/service.pyのヘルパーの複製(§2.1)であるため、no/defer/Noneの同一入出力ケースをunit/latches側の応答試験と対応させて論理の一致を担保する
2. unit/notifications(test_templates.py): `build_push(NOTIFICATION_MATCHED)` → (PUSH_TITLE, PUSH_BODY_MATCHED)。既存試験の未知type値は`"unknown_type"`リテラルでありlatch_matchedと衝突しない(確認済み)。あわせて全文ピン試験(test_push_constants_full_text)へPUSH_BODY_MATCHEDを、条件サマリ語彙の禁止試験(test_build_push_output_has_no_condition_summary)のtypeループへlatch_matchedを加える
3. unit/latches(test_latches_service.py): respondのmatched経路で通知INSERTがstore経由で呼ばれること(matched以外の経路では呼ばれないこと)・push注入時のsend呼び出し・push=Noneでエラーにならないこと(storeはmonkeypatch差し替えの既存構成)
4. integration(test_notifications_api.py): 1対1成立E2E — 両者yes→matched→**双方のnotificationsにtype=latch_matched行(payload={latch_id})**・プッシュドライラン記録2件(body=PUSH_BODY_MATCHEDのbyte比較)・GET /v1/notificationsのlatch.my_responseが回答者視点で正しい値
5. frontend(latch-notice.test.js): ①type=proposal×終了3種×my_response(no→「辞退しました」/defer→「今回は見送りました」/未設定→CLOSED_TEXT) ②type=latch_matched(matched→見出し+導線+バッジ「成立済み」/completed→バッジ「完了」/cancelled→CLOSED_TEXT) ③notificationHref(latch_matched→#/latches/{id})

**既存試験への影響調査(実施済み)**

| 試験 | 影響 | 対応 |
|---|---|---|
| unit/notifications/test_notifications_service.py | `LatchJoinedRow`へフィールド追加(default_factory=list)のためfixtureはそのまま成立 | なし |
| unit/notifications/test_templates.py | 全文ピン3定数・写像試験・forbiddenループが既存3type前提。未知type試験は`"unknown_type"`リテラルで衝突なし | 全文ピン・写像・forbiddenループへlatch_matched分を追加(§4新設2) |
| integration/test_notifications_api.py test_3(muted) | 提案時点の確認。muted側bの0行期待は成立前 | なし |
| integration/test_g3_acceptance_e2e.py test_1(muted) | Bの回答yesで**partial_acceptまで**(A未回答)でありmatchedにならない→成立通知は発火しない | なし |
| integration/test_g3_acceptance_e2e.py test_4(グループ成立×競合) | respond×3でmatchedになるため成立通知が3人へ書かれる。**notifications行数の期待はない**(latches/intents/eventsの確認のみ) | なし(必要ならlatch_matched 3行のassert追加は任意) |
| integration/test_expiry_batches.py test_5(completed通知) | latchはDB直接投入(status=matched)でrespond不経由→成立通知なし。COUNTはtype='attendance_request'絞り込み | なし |
| test_latches_api.py・test_chat_attendance_api.py | notifications参照はteardown(DELETE)のみ | なし |
| frontend latch-notice.test.js 53行(終了3種=CLOSED_TEXT) | notificationLatchヘルパーがmy_responseを設定しなければNone→CLOSED_TEXTのまま通る | なし(新ケースを別途追加) |

**試験環境**: backendはtest-ci構成(共有ci-db・purge-match-sub運用)、frontendはvitest。バックエンドの成立通知E2EはFakeClock+StubPushSenderの既存構成を踏襲(caplogでlatch.push.send記録を検証)。

## 5. docs改版候補(設計書に整理のみ・本体は触らない)

| docs | 追記事項 | 位置 |
|---|---|---|
| 05 §6 | LATCH遷移表の`partial_accept→matched`行へ「通知送信(成立通知・type=latch_matched)」を追記 | LATCH遷移表 |
| 05 §5 | GET /v1/notifications応答のlatch要素へmy_response(yes/no/defer/null)を追記 | API仕様 |
| 03 §4 | 成立通知の様式(アプリ内=見出し+チャット導線・プッシュ=第三汎用文)を追記。D-08上限の対象外である旨 | 通知設計 |
| 08 §2.6 | 成立通知のプッシュ本文(成立後の情報であり汎用文構造は同一)の注記 | 表示規制 |
| 01 | 第46行付近の導入文は今回の実装で具体規定と整合(矛盾解消)。追記不要の見込み | — |
| 06 §10 | 成立通知がD-08上限・mutedの対象外である旨(本設計§2.3の解釈) | D-07/D-08実装箇所 |

## 6. 未解決の論点(文言案・オーナー確認事項)→ **2026-10-02 全件確定**

1. **修正A・お知らせ終了行の操作履歴文言** — 詳細画面(ws-7)と同一の文言を流用: no=「辞退しました」/ defer=「今回は見送りました」/ その他=「この提案は成立しませんでした」。文言を増やさずANSWERED_TEXT定数の共有で済ませる方釈の確認
2. **修正B・アプリ内見出しと導線** — 見出し「LATCHが成立しました。」+導線「チャットで挨拶を交わしましょう」(NEXT_ACTION_TEXT流用)+バッジ(成立済み/完了)。条件サマリは成立通知行に含めない(§2.3の理由)。この行構成の確認
3. **修正B・プッシュ本文** — 推奨: 「LATCHが成立しました。\nチャットでメッセージを確認しましょう。」(2行目にチャット導線)。対案: 「LATCHが成立しました。\n詳細はアプリでご確認ください。」(既存2文と対称のアプリ誘導形)。いずれも汎用文(条件サマリなし)
4. **通知type名** — `latch_matched`を推奨(既存3値の命名規律に同じ)。対案: `matched`(短いが文脈が薄い)
5. **成立させた本人にも通知** — 参加者全員へ同時送信(D-20準用・attendance_requestと同型)。自分の操作でお知らせが1件増える体験の確認(03 §7の「自分の操作」文言との関係 — 成立は「閉じ」ではないためCLOSED_TEXT問題は起きない)
6. **matchedへの移行はrespondのみ**(§2.4) — 今後 matched 遷移を書く経路が増えた場合(例: 将来の自動成立)、通知挿入の追随が必要である旨をコードコメントとSTATUSに残すか

**裁定(オーナー「全部推奨で」+supervisor技術判断・2026-10-02)**: 1=承認(詳細画面同一文言の流用)/ 2=承認(見出し+導線+バッジ・条件サマリ不含)/ 3=**推奨案で承認**(「LATCHが成立しました。\nチャットでメッセージを確認しましょう。」)/ 4=承認(type=latch_matched)/ 5=承認(参加者全員へ同時送信)/ 6=残す(コードコメント+STATUS引継ぎへ記録)
