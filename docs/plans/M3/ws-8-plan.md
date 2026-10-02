# M3 ws-8(フロントエンド 周辺2画面)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ws-7のfrontend資産(vanilla JS・hashルーター・texts.js文言集約・vitest 170件)の上に、残る2画面 — ①お知らせ一覧(topbarベルpopoverの中身を差し替え・未読ドット・既読管理) ②設定(`#/settings`新設=通知許可DI注入+ブロック管理一覧/解除) — を追加し、不成立表示「この提案は成立しませんでした」を全画面でtexts.jsのCLOSED_TEXT単一ソースに統一する。あわせて§5-1承認済みのブロック登録導線(成立済み詳細・通報と同型)を実装する。**backend変更ゼロ・マイグレーションなし・依存追加なし(npm・uvともに)**。消費するAPI(notifications・read・blocks・DELETE/POST block)はws-3/ws-5で実装済み。

**Architecture:** design §2 — お知らせは**案A**(既存`#noticePopover`の中身をJS描画のコンテナへ差し替え。popoverを開くたび`GET /v1/notifications?limit=20`を1回取り直し+「もっと見る」追頁。未読ドットは起動時1回のpreloadで判定し、開いた行にPOST readで既読化=開いた=見た)。行の文言は**type×latch.statusのマトリクス**(design §2.3)で`view.js`の純関数(`notificationLines`・`notificationHref`)に集約し、終了行はCLOSED_TEXT・nearby行は存在文言のみ(リンクなし・candidateはGET /v1/latchesの対象外のため)。設定は**案A**(`#/settings`画面を新設しaccountPopoverの「設定」ボタンから接続)。通知許可はブラウザNotification APIを**注入**(DI・happy-domにNotificationがないため試験必須)で状態表示+取り直し(実送信FCMはM4)。ブロック管理は一覧+解除(確認モーダル挟み・404は一覧再取得で収束)。ブロック登録導線は成立済み詳細に通報(report-entry)と同型の導線(report.jsのモーダル構造を複製したblockFlow.js・1対1=相手固定/グループ=選択)。文言はすべてtexts.jsへ追記し既存定数(CLOSED_TEXT・STATUS_TEXT・ATTENDANCE_QUESTION・HIDDEN_PROPOSAL_TEXT)を参照。

**Tech Stack:** 変更なし(frontend: Vite 6.4.2 + vanilla JS + vitest 3 + happy-dom)。

**Spec:** `docs/plans/M3/ws-8-design.md`(agent1設計メモ)。**未解決論点なし** — design §5-1(ブロック登録導線)は2026-10-02 supervisor承認済み=採用(STATUS 09983e2)。design.mdが本計画より優先。本計画の適合措置2件(§9-6)はdesignの意図を保った実装補完で、報告書に記録する。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m3-ws-8`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeでは最初に `cd frontend && npm install` を実行する(**依存追加なし**のためpackage-lock.jsonは不変。worktreeにnode_modulesは持ち込まれないため初回必須)。backendは触らないため `make setup` 不要
- **本単位はfrontendのみ**(supervisor指示): backend・マイグレーション・依存追加なし。**`backend/` 配下に一切差分を出さない**(検証もbackend試験を実行する必要がない)。共有ci-dbへの `make test-ci` / `make migrate` は禁止(STATUS運用ルール1)。`docker compose` 系コマンドも一切実行しない。**previewの実機スモークも実施しない** — 手順を報告書へ書くのみ(§7)
- **マイグレーションなし・依存追加なし・設定追加なし**: `backend/` 全体・`frontend/package.json`・`frontend/package-lock.json`・`frontend/vite.config.mjs`・`frontend/vitest.config.mjs`・`Makefile`・`.env`系に触れない
- **frontendの既存18テストファイルは1行も触らない** — 例外は `tests/router.test.js` のみ(design §3が追記を明示。**describeブロックの末尾への追記のみで既存6件の変更はしない**)。既存19ファイルの内訳: app-state / client / conditions / expiry / format / latch-attendance / latch-chat / latch-detail / latch-home / latch-report / latch-respond / latch-shell / latch-view / parseFlow / router / save / session / smoke / state(170件)
- **既存モジュールはdesign §3「触らないもの」どおり無変更**: `src/api/client.js`・`src/api/session.js`・`src/ui/chrome.js`・`src/intent/`配下・`src/latch/home.js`・`respond.js`・`chat.js`・`attendance.js`・`report.js`・`src/appState.js`・`src/intent/screen.js`。**変更する既存ファイルは5つだけ**: `src/router.js`(分岐1行追加)・`src/latch/view.js`(追記)・`src/latch/texts.js`(追記)・`src/latch/detail.js`(ブロック導線の追記のみ)・`index.html`/`src/main.js`/`styles.css`(構成)
- **index.htmlのpopover差し替えの規律**: `#noticePopover` というidと `popover-kicker` は維持する(latch-shell.test.jsの `id="noticePopover"` ピン・smoke.test.jsのID群が無傷であるため)。`#noticePopover` の中身(静的なstrong/span 2行)は削除してコンテナへ差し替える
- **frontend試験コードを書いた後の自己見直し**(ws-3の教訓・supervisor指示): ヘルパーの引数形式・戻り値unpack・モックの応答形式・比較の型を見直す。**特にモック応答は§2-1のAPI応答型と突き合わせること**(`{"items","next_cursor"}`・notifications itemの`latch`はnull可・204系は`null`返し・blocks itemは`{blocked_id, display_name, created_at}`フラット)
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` 等)
- **毎コミットの検証**: `cd frontend && npm test` がグリーン(本単位はfrontendのみのためbackend検証は不要)
- **`python` / `pip` / uvコマンドを使わない**(backendに触らない)
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| お知らせ画面の必須要素は「アプリ内通知一覧 / 既読管理」。UI名称は「お知らせ」(topbarベルpopover)。空状態は「まだ新しい候補はありません / 条件が合うと、ここに静かに届きます。」 | 03 §2(design引用#1) |
| 未読表示はお知らせボタンのドット(notification-dot) | 03 §2(引用#2) |
| 設定画面の必須要素は「通知許可 / ブロック管理」(友人管理はv0.4で削除) | 03 §2(引用#3) |
| アプリ内通知の様式 — summary_only: 「LATCH候補があります。」+条件サマリ+「あなたの条件との一致度が高い候補です。」/ hidden_until_match: 「条件が合う候補があります。」+一致度+回答期限(引用#4の「今夜中に」は例示文面・実際はresponse_deadline) | 03 §4(引用#4) |
| FR-46: 通知のタップは提案詳細画面へ遷移し、回答は詳細画面の3択からのみ。バナー上の直接回答ボタンは設けない | 03 §4(引用#5) |
| D-18: プッシュ許可は拒否しても利用可能(アプリ内通知にフォールバック)。許可の再要求は文脈のあるタイミングに限り、**設定画面からいつでも取り直せる** | 03 §8 D-18(引用#6) |
| 不成立の表示は2種類に集約 — 自分の操作によるものは操作履歴、それ以外(相手の回答・期限切れ・ブロック等)は「この提案は成立しませんでした」の一文で統一。相手の回答種別・ブロック事実は一切開示しない | 03 §7・D-20(引用#7) |
| nearby_alsoが選ばれている場合に限り閾値未満候補も**その発生(存在)**の通知のみを行い、条件サマリ・相手情報は通知に含めない | 03 §2(引用#8) |
| 通知手段はプッシュ+アプリ内(topbarの「お知らせ」popover+未読ドット)の併用 | 03 §4・D-18(引用#9) |
| フロントの実装基準は`prototype/`。後続の画面(設定等)もプロトタイプのデザインシステムを踏襲 | 03 §10・00 運用ルール6(引用#10・#11) |
| topbarのpopoverのボタン(設定)はプレースホルダ。**各画面の実装で接続する**(プロフィール・ログアウトは温存) | 03 §10 既知の差分(引用#12) |
| GET /v1/notifications / POST /v1/notifications/{id}/read — 本人のみ。ページネーション共通規定(`?cursor=&limit=`1〜100既定20・created_at降順) | 05 §5(引用#13) |
| GET /v1/users/me/blocks・DELETE /v1/users/{user_id}/block — 一覧はcursor改頁(created_at降順) | 05 §5(引用#14) |
| 実施自己申告(D-09)の設問は「実際に会いましたか?」(attendance_request行で流用) | 05 §5・09 D-09(引用#15) |
| POST readは204冪等(既読済みも204)。他人のid・存在しないidは404 | ws-3設計§2.6(引用#18) |
| blocks一覧itemは`{blocked_id, display_name, created_at}`。DELETEは204・行不在は404(冪等にしない) | ws-5設計§2.4(引用#19) |
| nearby通知のpayload.latch_idは**candidate状態のlatches行id**=GET /v1/latches/{id}の対象外(404)。行の文言はstatusによらず存在通知のまま(nearbyは提案ではないためCLOSED_TEXTの対象外) | latch_engine実装・05 §5(引用#20) |
| attendance_request行のlatch.statusはcompleted。クライアントは設問を表示し、回答操作はws-4のAPIへ(行からの直接回答はしない・FR-46) | ws-3設計§2.5(引用#21) |
| ブロック解除は遡及効果なし(候補除外・読取専用化を取り消さない) | 08 §5.1(引用#23) |
| D-23: ブロックされた側の画面には「このチャットは利用できません」のみ表示。cancelled化・読取専用化は登録APIのtx内でサーバ側が自動適用(フロントは再取得で収束) | 08 D-23(引用#24) |
| §5-1承認事項(2026-10-02 supervisor承認・STATUS 09983e2): 成立済み詳細に通報と同型のブロック登録導線を置く。提案詳細(proposed)には置かない(参加者非開示・blockはuser_idパス指定必須で省略解決の契約がない) | design §2.7・§5-1 |
| お知らせ終了行は一律CLOSED_TEXT(notifications応答にmy_responseがなくフロントで二値化できないため。理由開示禁止の精神を満たす・backend拡張なし) | design §2.3・§5-2② |
| matched/completed後のproposal行はバッジ「成立済み」(STATUS_TEXT流用)表示・回答を促す一文は出さない | design §2.3・§5-2③ |
| M3-10: フロントエンド(prototype準拠)— ホーム・提案詳細・成立済み詳細・**お知らせ一覧・設定(通知許可・ブロック管理)**。不成立表示は「この提案は成立しませんでした」の一文に統一 | 12 M3スコープ10 |
| G3完了条件: プロトタイプ6画面が03に従い実装済み(実装基準はprototype側を正とする)— 本単位の完了で6画面が揃う | 12 M3完了条件(引用#22) |
| C13: nearby_alsoは候補をcandidateのまま存在通知のみ(通知表示の前提) | 12 依存制約C13 |
| プッシュのフロント受信(FCM web・Service Worker・PWA化)・デバイストークン登録・オンボーディング・D-14同意再確認 → M4-6(本単位の「通知許可」はブラウザ許可状態の表示・取り直しのみ) | design §1.4・11 |

## 2. グローバル制約(全タスクに暗黙に適用・design §2の固定値)

### 2-1. API応答の型(フロントが消費する形・実装済み。**これとモック応答を突き合わせる**)

- `GET /v1/notifications?limit=20&cursor=` → `{"items": [NotificationItem], "next_cursor": string|null}`(created_at降順)
- **NotificationItem** = `{id, type, latch_id, read_at, created_at, latch}`。typeは3値: `proposal` / `nearby_candidate` / `attendance_request`。`read_at` はstring|null(未読=null)。`latch` は `NotificationLatch | null`(LEFT JOIN・不在や不正値はnull)
- **NotificationLatch** = `{id, status, response_deadline, expires_at, completed_at, proposal}`(**my_responseを含まない** — お知らせ行の終了表示は二値化できず一律CLOSED_TEXT・design §5-2②)。statusは全値あり得る(nearby行はcandidateのまま期限切れでexpiredもあり得る)。proposalは2形 — 全フィールド版 `{time_summary, area_name, headcount, category_primary, category_secondary, budget{max}|null, match_level}` / 最小版 `{headcount, match_level}`(time_summaryキー自体が無い — 既存`isMinimalProposal`と同一判定)
- `POST /v1/notifications/{id}/read` → **204**(bodyなし・冪等。client.callは `null` を返す)
- `GET /v1/users/me/blocks?limit=20&cursor=` → `{"items": [{blocked_id, display_name, created_at}], "next_cursor": string|null}`(created_at降順)
- `DELETE /v1/users/{blocked_id}/block` → **204**・行不在は**404**(冪等にしない — 404は通信エラー扱いにせず一覧を取り直して収束)
- `POST /v1/users/{user_id}/block` → **201** `{blocked_id}`(**bodyなし・パス指定**・冪等)
- nearby行の `latch.id` はcandidate状態の行 = `GET /v1/latches/{id}` の対象外(404) — **行をリンク化しない**

### 2-2. 画面・文言の固定値(design §2.1〜§2.8)

- **お知らせpopover(design §2.1案A)**: `#noticePopover`内= `popover-kicker`「お知らせ」(既存維持)+ `div#noticeList` + `button#noticeMoreButton`(next_cursor残時に表示)。popoverの高さは `max-height` + `overflow-y: scroll`(560px以下も同じ挙動)。**popoverを開くたびに`GET /v1/notifications?limit=20`を1回**取り直す(開いた時点の最新)。「もっと見る」で追頁(homeのmoreButtonと同型)。**取得失敗時は前回表示を維持し、空状態も出さない**(再訪で再試行)
- **未読ドット・既読化(design §2.2)**: アプリ起動時(main.js初期化・`session.hasTokens()`がtrueの場合)にpreloadとして`GET /v1/notifications?limit=20`を1回呼び、itemsに`read_at === null`が1件でもあれば`#notificationDot`を表示・全件既読/0件なら非表示(`hidden`属性で制御・index.htmlへ初期`hidden`を追加)。preload結果はpopover初回描画にも使い**二重取得しない**。新着ポーリングはしない。既読化の規則は「**popoverを開いて表示された未読行にPOST readする**(開いた=見た)」の1本 — 未読行それぞれへ並行でPOST(失敗した行は握り、次回開いた時の未読対象のまま)、完了後にドットを再判定。行クリックを既読の条件にしない。popoverを開いたことの検知は`#noticeButton`へのmain.jsの追加clickリスナ(chrome.js無変更 — chromeのリスナが先に登録されているため、追加リスナの実行時には`#noticePopover.hidden`は開閉後の状態であり、`hidden === false`のときだけ取得・既読化)
- **お知らせ行の文言マトリクス(design §2.3 — `notificationLines(item, nowIso)`)**:

| type | latch.status | 行の文言(lines配列) | タップ先href |
|---|---|---|---|
| proposal | proposed / partial_accept(全形) | 「LATCH候補があります。」+条件サマリ(既存`conditionSummaryLines`流用)+「一致度が高い候補です。」 | `#/latches/{id}` |
| proposal | proposed / partial_accept(最小形hidden) | 「条件が合う候補があります。」(既存HIDDEN_PROPOSAL_TEXT)+「一致度が高い候補です。」+残時間(既存`remainingTimeText`流用) | `#/latches/{id}` |
| proposal | matched / completed | 見出し(全形=time_summary+area_name/最小形=HIDDEN_PROPOSAL_TEXT)+条件サマリ+STATUS_TEXT[status]バッジ(「成立済み」/「完了」)。**回答を促す一文は出さない** | `#/latches/{id}` |
| proposal | expired / rejected / cancelled | **CLOSED_TEXT「この提案は成立しませんでした」の1行のみ**(一文統一・my_responseなしのため自分で閉じた場合も一律) | `#/latches/{id}`(不成立姿) |
| nearby_candidate | 全status | 「近い条件の候補があるようです。」のみ(§5-2①・条件サマリ・一致度・人数を出さない) | **null(リンク化しない)** |
| attendance_request | completed | 「実際に会いましたか?」(既存ATTENDANCE_QUESTION)のみ。**行からの直接回答はしない**(FR-46) | `#/latches/{id}` |
| 全type | latch === null(防御) | 「LATCHからのお知らせがあります。」 | **null** |

- **行の付属要素**: 日時(`created_at`のJST書式「M月D日 HH:MM」・`noticeTimeText`純関数)+ 未読スタイル(`read_at === null`の行に`unread`クラス)。タップ先を持たない行(nearby・latch=null)は`<a>`でなく`<div>`で描画
- **設定画面(design §2.4案A)**: `#/settings`を新設(routerへ解析追加・未知hashは従来どおりホーム)。accountPopoverの「設定」ボタン(`id="settingsButton"`)のクリックで `location.hash = "#/settings"` + popover閉鎖(`hidden`化+aria-expanded更新 — chromeのclosePopoversと同じ操作をmain.js側で実行・chrome.js無変更)。main.jsの`screens`へ`settings: $("#settingsScreen")`を追加し、onRouteで`settings.show()`を呼ぶ(home.loadと同型)。画面構成=見出し「設定」+2セクション(通知/ブロック管理)。規定のない要素(プロフィール編集・ログアウト・テーマ)は置かない
- **通知許可(design §2.5)**: ブラウザの許可状態のみ扱う(トークン登録なし・サーバ通信なし)。`Notification.permission`・`requestPermission()`は直接参照せず`createPermissionControl({ notificationApi, mount })`へ注入(DI・client注入と同型)。4状態 — granted「通知は許可されています」(操作なし)/ default「通知は許可されていません」+[通知の許可を求める]ボタン→`requestPermission()`→結果を再表示 / denied「通知はブラウザの設定でブロックされています。ブラウザの設定から変更できます。」(操作なし)/ API不在「この環境では通知を利用できません。」(操作なし)。**全状態に補足文「許可しなくても、アプリ内のお知らせで確認できます。」**を添える(D-18フォールバック保証)
- **ブロック管理(design §2.6)**: `GET /v1/users/me/blocks?limit=20`で一覧。行=表示名+ブロック日+「解除する」ボタン。next_cursor残時は「もっと見る」。空状態「ブロックしているユーザーはいません」。解除は**確認モーダルを1つ挟む**(「{表示名}さんのブロックを解除しますか?」+[解除する]/[やめる]。reportModalと同型backdrop)。確定で`DELETE /v1/users/{blocked_id}/block` — 204で一覧から行を除去(カーソル保持)・404(既に解除済み)は**通信エラー扱いにせず一覧を取り直して収束**・429 RATE_LIMITEDは既存RATE_LIMIT_TEXT。遡及効果なしはUIで説明しない(YAGNI)
- **ブロック登録導線(design §2.7・§5-1承認)**: 成立済み詳細(matched/completed)にreport-entryと同型の導線「このユーザーをブロックする」。**提案詳細(proposed)には置かない**(参加者非開示)。1対1=相手固定/グループ=対象者選択(participantsから自分以外)。確認モーダル「ブロックすると、このやりとりは利用できなくなります。」→`POST /v1/users/{user_id}/block`(パス指定)。成功(201冪等)→トースト「ブロックしました」→詳細を再取得(D-23のcancelled化・読取専用化はサーバ側適用済みのため再取得応答で自然に収束)
- **texts.js追記の新規文言**(§8 Task 2の完全指定。既存CLOSED_TEXT・HIDDEN_PROPOSAL_TEXT・ATTENDANCE_QUESTION・STATUS_TEXT・RATE_LIMIT_TEXTは参照のみ):
  - NOTICE_EMPTY_TITLE「まだ新しい候補はありません」/ NOTICE_EMPTY_NOTE「条件が合うと、ここに静かに届きます。」(03 §2)
  - PROPOSAL_NOTICE_TITLE「LATCH候補があります。」/ MATCH_NOTICE_TEXT「一致度が高い候補です。」(03 §4様式)
  - NEARBY_NOTICE_TEXT「近い条件の候補があるようです。」(design §5-2①)/ NOTICE_FALLBACK_TEXT「LATCHからのお知らせがあります。」(防御)
  - PERMISSION_GRANTED_TEXT / PERMISSION_DEFAULT_TEXT / PERMISSION_DENIED_TEXT / PERMISSION_UNSUPPORTED_TEXT / PERMISSION_NOTE_TEXT / PERMISSION_REQUEST_LABEL「通知の許可を求める」(design §2.5の表)
  - BLOCKS_EMPTY_TEXT「ブロックしているユーザーはいません」/ BLOCK_UNBLOCK_LABEL「解除する」/ BLOCK_CANCEL_LABEL「やめる」/ BLOCK_CONFIRM_SUFFIX「さんのブロックを解除しますか?」(前に表示名を結合)
  - BLOCK_ENTRY_TEXT「このユーザーをブロックする」/ BLOCK_CONFIRM_TEXT「ブロックすると、このやりとりは利用できなくなります。」/ BLOCK_SUBMIT_LABEL「ブロックする」/ BLOCK_SELECT_LABEL「ブロックする相手」/ BLOCK_SELECT_ERROR_TEXT「相手を選んでください。」/ BLOCK_TOAST_TEXT「ブロックしました」/ BLOCK_ERROR_TEXT「送信できませんでした。」
- **XSS対策**: API応答由来の文字列(表示名・通知文言)をinnerHTMLへ入れる箇所はすべて既存`escapeHtml`を通す

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **nearby行から条件サマリ・リンクを出してしまう**(C13・引用#8/#20 — candidateはGET /v1/latchesの対象外=404のためリンクは踏み出しになる) → Task 2(nearby行の文言1行のみ+`notificationHref`がnull)+Task 3(行DOMが`<div>`で`<a>`にならない)
2. **hidden_until_match通知行で条件サマリを出してしまう**(03 §5・表示規制) → Task 2(最小形はHIDDEN_PROPOSAL_TEXT+一致度+残時間のみ)
3. **終了済み通知行に理由や操作履歴を混ぜる**(03 §7・引用#7 — お知らせ行は一律CLOSED_TEXT) → Task 2(expired/rejected/cancelledの3statusがCLOSED_TEXT 1行)
4. **matched/completed後のproposal行に「回答ください」類を残す**(成立済みに回答促しは不当) → Task 2(MATCH_NOTICE_TEXTを含まないピン)
5. **ブロック系の失敗形の取り違え**(解除404を通信エラー扱い/提案詳細にブロック導線=参加者非開示違反) → Task 5(404で一覧再取得)+Task 7(提案詳細に導線なし)

---

## 4. スコープ(作成・変更するファイル一覧)

作成(frontend・§9-1のbasename衝突確認済み):

```text
frontend/src/latch/notice.js           (Task 3。お知らせpopover flow: preload・open・既読化・ドット・追頁)
frontend/src/latch/permission.js       (Task 4。通知許可コントロール・DI注入)
frontend/src/latch/blocks.js           (Task 5。ブロック一覧・解除flow・確認モーダル・404収束)
frontend/src/latch/blockFlow.js        (Task 6。ブロック登録モーダル・1対1固定/グループ選択)
frontend/src/latch/settings.js         (Task 7。設定画面骨組・2セクションのマウント)
frontend/tests/latch-notice.test.js    (Task 2+3。21件=純関数12+flow 9)
frontend/tests/latch-settings.test.js  (Task 4+7。10件=permission 7+settings 3)
frontend/tests/latch-blocks.test.js    (Task 5。7件)
frontend/tests/latch-block-flow.test.js(Task 6+7。6件=flow 5+detail統合1)
docs/plans/M3/ws-8-report.md           (Task 9。報告ファイル)
```

変更(design §3):

| ファイル | 変更内容 | Task |
|---|---|---|
| `frontend/src/router.js` | `parseHash`へ`#/settings`厳密一致の分岐を1本追加(既存ロジック無変更) | 1 |
| `frontend/tests/router.test.js` | describeブロック末尾へ3件追記(既存6件は変更しない) | 1 |
| `frontend/src/latch/texts.js` | §2-2の新規文言定数を末尾へ追記(既存定数無変更) | 2 |
| `frontend/src/latch/view.js` | `noticeTimeText`・`notificationLines`・`notificationHref`を末尾へ追記(既存関数無変更) | 2 |
| `frontend/index.html` | ①`#notificationDot`へid+初期hidden ②`#noticePopover`中身をコンテナ化(noticeList+noticeMoreButton・idとpopover-kickerは維持) ③設定ボタンへ`id="settingsButton"` ④`#settingsScreen`追加 ⑤`#unblockModal`+`#blockModal` backdrop追加 | 7 |
| `frontend/src/main.js` | notice(preload+ボタンリスナ+moreButton)・permission・blocks・settings・blockFlowの初期化、routerのscreens/onRouteへsettings追加、設定ボタン接続(§8 Task 7の完全指定) | 7 |
| `frontend/src/latch/detail.js` | `createDetail`引数へ`blockFlow = null`追加・renderMatchedへblock-entryボタン1行+wireBlockEntry(§5-1承認分・表示ロジック無変更) | 7 |
| `frontend/styles.css` | 新画面分を末尾へ追記(notice-list・settings・block行・モーダル。**既存クラス無変更**・§8 Task 7の完全指定) | 7 |

生成されるがコミットしないもの: `frontend/dist/`(build成果物)・`frontend/node_modules/`。

**スコープ外と判断する基準(必要になったと感じても作らない — design §1.4)**:

- プッシュのフロント受信(FCM web・Service Worker・PWA化)・デバイストークン登録API → M4以降(11)。本単位の「通知許可」はブラウザ許可状態の表示・取り直しのみ
- オンボーディング(許可の初回要求)・LLM送信の同意再確認 → M4-6(08 D-14)
- プロフィール編集・ログアウト導線 → accountPopoverのボタンはプレースホルダ温存(03 §10の既知差分)
- 通知の絞り込み(unread_only等)・既読一括API → 05に規定なし。行単位POST readで足りる
- Intent単位のお知らせ設定(notification_level) → 預け方パネル(M1実装済み)のまま・設定画面の対象外(03 §3)
- チャット画面内のブロック導線 → 成立済み詳細の導線(§2.7)で担う(08 §5.1に導線規定なし・導線の一元化)
- 新着のポーリング・WebSocket → 03に規定なし(起動時と開く時の取得でM3は足りる・design §2.2)
- tokenConnect成功時のnotice.preload追加呼び出し → designは「アプリ起動時(トークンがある場合)」のみ規定。トークンパネル接続直後はドットが popoverオープン時の取得で判定される(報告書の補足へ記録)

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- **backend全体**(src・tests・alembic・pyproject.toml・uv.lock・main.py等すべて — supervisor指示「frontendのみ」)。`make lint` / `make test` / `make test-ci` / `make migrate` 等、backend/Makefile系タスクは実行しない
- **docker/compose系の全コマンド**: `docker compose *` / `docker build` / `docker pull` — 共有ci-dbを消費してはならない。**preview実機スモークも実施しない**
- **frontendの無変更ファイル(design §3「触らないもの」)**:
  - `frontend/src/api/client.js`・`frontend/src/api/session.js`(**完全無変更** — 全API呼び出しはclient.call()を通す)
  - `frontend/src/ui/chrome.js`(**完全無変更** — popover開閉検知はmain.jsの追加リスナで行う)
  - `frontend/src/intent/` 配下の既存7モジュール(state・parseFlow・conditions・expiry・save・format・screen — format.jsの`jstParts`をimportするのは可)
  - `frontend/src/appState.js`・`frontend/src/latch/home.js`・`respond.js`・`chat.js`・`attendance.js`・`report.js`
  - `frontend/tests/` の既存19ファイルのうち18ファイル(§0。**router.test.jsのみ末尾追記可**)
  - `frontend/vite.config.mjs`・`frontend/vitest.config.mjs`・`frontend/package.json`・`frontend/package-lock.json`・`frontend/public/`
  - `prototype/` 全体(実装基準の参照物)
- **`frontend/src/latch/detail.js`は§5-1承認分の追記のみ**(blockFlow引数+block-entryボタン+wireBlockEntry。3姿の表示ロジック・report導線・chat/attendanceへの変更はしない)
- `docs/`(01〜12・learn・testassets)・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/` の既存ファイル(M0〜M3のdesign・plan・report。**ws-8-report.mdの新規作成のみ可**)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 8〜9で全て実行し報告ファイルに証拠を残す)

1. **frontendの `npm test` が全緑**(既存170件+新規47件)
   検証: `cd frontend && npm test` — **期待件数: 217 passed**(23ファイル=19+4。§9-3の内訳)。既存19ファイルの失敗ゼロ(router.test.jsは既存6件+追記3件=9件)
2. **frontendの `npm run build` が成功**
   検証: `cd frontend && npm run build` — exit 0・dist生成
3. **テストファイルbasenameがfrontend/tests配下で一意**
   検証: `ls frontend/tests/*.test.js | awk -F/ '{print $NF}' | sort | uniq -d` が空(§9-1)
4. **backend・prototype・ドキュメント本体への差分ゼロ**
   検証: `git diff --name-only main -- backend prototype docs` が空(§4のとおりfrontend/配下とdocs/plans/M3/ws-8-report.mdのみ)
5. **変更ファイルが§4の一覧どおり(frontend新規9+変更8+report.md=計18ファイル)**
   検証: Task 9の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
6. **03 §7の一文統一がCLOSED_TEXT単一参照で担保されていることを試験が証拠する**
   検証: latch-notice.test.jsの終了行試験(Task 2①)が `CLOSED_TEXT` 定数を期待値として参照していること(importして比較 — design §6-3)

## 7. 報告形式

**結果ファイル**: `docs/plans/M3/ws-8-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M3 ws-8(フロントエンド 周辺2画面)実行報告

- ブランチ: m3-ws-8 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | npm test | PASS/FAIL | <出力末尾(期待23 files / 217 passed・既存170件含む)> |
| 2 | npm run build | PASS/FAIL | <出力末尾> |
| 3 | テストbasename衝突なし | PASS/FAIL | <uniq -d 出力(空)> |
| 4 | backend/prototype/docs差分ゼロ | PASS/FAIL | <git diff --name-only main -- backend prototype docs(空)> |
| 5 | 変更ファイル=§4の17ファイル | PASS/FAIL | <git diff --name-only main出力> |

## 固定値の変更有無(design.md §2・本計画§2)
- お知らせ行マトリクス(§2-2): 変更なし / 変更あり(<前→後+理由>)
- 既読化の規則「開いた=見た」+POST read(§2-2): 変更なし / 変更あり
- 通知許可4状態(§2-5): 変更なし / 変更あり
- ブロック解除確認モーダル・404収束(§2-6): 変更なし / 変更あり
- ブロック登録導線=成立済み詳細のみ(§2-7・§5-1承認): 変更なし / 変更あり
- その他: 変更なし / 変更あり(<前→後+理由>)

## (スーパーバイザー・ws-9/G3への引継ぎ)
- §9-6の適合措置2件(#unblockModalのindex.html追補・noticeTimeText書式)の記録
- tokenConnect成功直後はpreloadを呼ばない(design規定外 — 次のpopoverオープンで取得)
- G3完了条件「プロトタイプ6画面」が本単位の完了で揃った旨(実装基準はprototype/・実機確認は下記スモーク)

## 実機スモーク手順(スーパーバイザー検証用・agent3は実施しない — design §6-2)
1. mainへマージ後: `docker compose build api && make up`(api再ビルド必須・STATUS運用ル則4)
2. `cd frontend && npm install && npm run build && npm run preview`(dist配信・localhost:4173)
3. トークンパネルへIdPトークン(`cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject <subject>`)を貼る
4. 事前データ(backend内部CLIまたはAPI直叩きで作成 — 例: 2ユーザー×Intent2件からlatchを直接INSERTしnotifications行を生成):
   - ①通知fixture(proposed 1件+hidden 1件+nearby 1件+attendance_request 1件)で起動時ドット表示→popoverを開く→行が§2.3マトリクスどおり・nearby行はリンクなし
   - ②開いた後ドットが消灯し、再取得でread_atが反映(既読化)
   - ③終了済みlatch(cancelled等)の通知行が「この提案は成立しませんでした」で、タップで詳細の不成立姿
   - ④設定(`#/settings`)がaccountPopoverの「設定」から開く。通知許可の状態が表示され、default状態で[通知の許可を求める]が機能する(ブラウザの許可ダイアログ)
   - ⑤ブロック登録(API直叩き: `POST /v1/users/{id}/block`)→設定の一覧に表示名が並ぶ→確認モーダル→解除で行が消える
   - ⑥成立済み詳細からブロック登録→トースト「ブロックしました」→チャットが読取専用(「このチャットは利用できません」)へ収束

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

---

## 8. 実装ステップ(TDD。Task 1〜9の順で実行する)

### Task 1: router.js — `#/settings`解析追加

**Files:**
- Modify: `frontend/src/router.js`(`parseHash`へ分岐1本)
- Test: `frontend/tests/router.test.js`(describeブロック末尾へ3件追記 — 既存6件は変更しない)

**Interfaces:**
- Produces: `parseHash("#/settings")` が `{name: "settings", id: null}` を返す(それ以外は従来どおり。Task 7のmain.jsがscreens/onRouteで消費)

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/router.test.js` の `describe("parseHash(design §2.1)", () => {...})` ブロックの末尾(閉じ `});` の前)へ2件追記:

```js
  it("#/settings をsettingsへ解析する(厳密一致・ws-8 design §2.4)", () => {
    expect(parseHash("#/settings")).toEqual({ name: "settings", id: null });
  });

  it("#/settings/ など末尾に追加がある場合は未知hashとしてホームへ", () => {
    expect(parseHash("#/settings/")).toEqual({ name: "home", id: null });
    expect(parseHash("#/settings/x")).toEqual({ name: "home", id: null });
  });
```

同じく `describe("createRouter(hashchangeで画面section切替)", () => {...})` ブロックの末尾へ1件追記:

```js
  it("hashchangeでsettings画面へ切替(home・latchは隠す)", () => {
    const screens = {
      home: document.createElement("div"),
      latch: document.createElement("div"),
      settings: document.createElement("div"),
    };
    const onRoute = vi.fn();
    const router = createRouter({ screens, onRoute });
    window.location.hash = "#/";
    router.start();
    window.location.hash = "#/settings";
    window.dispatchEvent(new Event("hashchange"));
    expect(screens.settings.hidden).toBe(false);
    expect(screens.home.hidden).toBe(true);
    expect(screens.latch.hidden).toBe(true);
    expect(onRoute).toHaveBeenLastCalledWith({ name: "settings", id: null });
    router.stop();
  });
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npx vitest run tests/router.test.js`
Expected: 追加3件がFAIL(`parseHash("#/settings")` が `{name:"home"}` を返すため) / 既存6件はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/router.js` の `parseHash` を次へ置き換える(冒頭コメントの画面列挙へsettingsを追記。`createRouter` は無変更):

```js
// hashルーター(M3 ws-7 design §2.1案A・ws-8 §2.4)。`#/`=ホーム・
// `#/latches/{uuid}`=LATCH詳細・`#/settings`=設定・未知のhashはホームへ
// 戻す。ルーターはURL解析と画面sectionの切替のみを担い、各画面の初期化・
// データ取得はonRoute側(単体試験可能な境界)。
// uuidはbackendの発行形式(uuid4・バージョン桁4+バリアント桁89ab)に合わせる。
const LATCH_HASH = /^#\/latches\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$/;

export const parseHash = (hash) => {
  if (hash === "#/settings") return { name: "settings", id: null };
  const match = LATCH_HASH.exec(hash ?? "");
  if (match) return { name: "latch", id: match[1] };
  return { name: "home", id: null };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(router 9=既存6+追記3・全体 **173 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/router.js frontend/tests/router.test.js
git commit -m "feat: add #/settings route to hash router (M3 ws-8)"
```

### Task 2: texts.js追記(新規文言)+ view.js追記(お知らせ行の純関数)

**Files:**
- Modify: `frontend/src/latch/texts.js`(末尾へ§2-2の新規文言を追記)
- Modify: `frontend/src/latch/view.js`(末尾へ `noticeTimeText`・`notificationLines`・`notificationHref` を追記)
- Test: `frontend/tests/latch-notice.test.js`(新規・純関数分10件。Task 3でflow分を追記)

**Interfaces:**
- Consumes: 既存 `conditionSummaryLines`・`isMinimalProposal`・`remainingTimeText`(view.js)・既存 `format.js` の `jstParts`(import追加)・既存定数 `CLOSED_TEXT`・`HIDDEN_PROPOSAL_TEXT`・`ATTENDANCE_QUESTION`・`STATUS_TEXT`
- Produces(Task 3〜7が消費):
  - texts.js: §2-2列挙の新規定数(NOTICE_EMPTY_TITLE・NOTICE_EMPTY_NOTE・PROPOSAL_NOTICE_TITLE・MATCH_NOTICE_TEXT・NEARBY_NOTICE_TEXT・NOTICE_FALLBACK_TEXT・PERMISSION_GRANTED_TEXT・PERMISSION_DEFAULT_TEXT・PERMISSION_DENIED_TEXT・PERMISSION_UNSUPPORTED_TEXT・PERMISSION_NOTE_TEXT・PERMISSION_REQUEST_LABEL・BLOCKS_EMPTY_TEXT・BLOCK_UNBLOCK_LABEL・BLOCK_CANCEL_LABEL・BLOCK_CONFIRM_SUFFIX・BLOCK_ENTRY_TEXT・BLOCK_CONFIRM_TEXT・BLOCK_SUBMIT_LABEL・BLOCK_SELECT_LABEL・BLOCK_SELECT_ERROR_TEXT・BLOCK_TOAST_TEXT・BLOCK_ERROR_TEXT)
  - view.js: `noticeTimeText(iso) -> string`(JST「M月D日 HH:MM」)・`notificationLines(item, nowIso) -> string[]`(§2-2マトリクス)・`notificationHref(item) -> string|null`(nearbyとlatch=nullはnull)

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-notice.test.js` を新規作成:

```js
import { describe, expect, it } from "vitest";
import {
  ATTENDANCE_QUESTION,
  CLOSED_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  MATCH_NOTICE_TEXT,
  NEARBY_NOTICE_TEXT,
  NOTICE_EMPTY_NOTE,
  NOTICE_EMPTY_TITLE,
  NOTICE_FALLBACK_TEXT,
  PROPOSAL_NOTICE_TITLE,
} from "../src/latch/texts.js";
import {
  notificationHref,
  notificationLines,
  noticeTimeText,
} from "../src/latch/view.js";

// API応答のfixture(§2-1のNotificationItem型・proposalは全フィールド版)
const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const fullProposal = {
  time_summary: "2026-10-01 20:00",
  area_name: "天文館周辺",
  headcount: 2,
  category_primary: "drinking",
  category_secondary: null,
  budget: null,
  match_level: "high",
};
const minimalProposal = { headcount: 2, match_level: "low" }; // hidden_until_match形
const notificationLatch = (status, overrides = {}, proposal = fullProposal) => ({
  id: LATCH_ID,
  status,
  response_deadline: "2026-10-02T12:00:00+09:00",
  expires_at: "2026-10-05T12:00:00+09:00",
  completed_at: null,
  proposal,
  ...overrides,
});
const NOW = "2026-10-01T09:00:00+09:00"; // 残時間27時間→「あと1日と3時間で締切」
const item = (type, latch, overrides = {}) => ({
  id: "n1",
  type,
  latch_id: latch?.id ?? null,
  read_at: null,
  created_at: "2026-10-01T09:00:00+09:00",
  latch: latch ?? null,
  ...overrides,
});

describe("notificationLines(design §2.3のマトリクス)", () => {
  it("proposal×終了3種(expired/rejected/cancelled)はCLOSED_TEXTの1行のみ(一文統一)", () => {
    for (const status of ["expired", "rejected", "cancelled"]) {
      expect(notificationLines(item("proposal", notificationLatch(status)), NOW))
        .toEqual([CLOSED_TEXT]);
    }
  });

  it("proposal×proposed全形: 見出し+条件サマリ+一致度文(03 §4様式)", () => {
    expect(notificationLines(item("proposal", notificationLatch("proposed")), NOW))
      .toEqual([
        PROPOSAL_NOTICE_TITLE,
        "2026-10-01 20:00",
        "天文館周辺",
        "2人",
        "飲み",
        MATCH_NOTICE_TEXT,
      ]);
  });

  it("proposal×proposed最小形(hidden): 条件サマリなし+残時間(期限はresponse_deadline)", () => {
    expect(
      notificationLines(
        item("proposal", notificationLatch("proposed", {}, minimalProposal)),
        NOW,
      ),
    ).toEqual([
      HIDDEN_PROPOSAL_TEXT,
      MATCH_NOTICE_TEXT,
      "あと1日と3時間で締切", // 2026-10-01 09:00→10-02 12:00
    ]);
  });

  it("proposal×matched/completed: 見出し+サマリ+STATUS_TEXTバッジ・回答を促す一文は出さない", () => {
    const matched = notificationLines(
      item("proposal", notificationLatch("matched")), NOW);
    expect(matched.at(-1)).toBe("成立済み"); // STATUS_TEXT.matched
    expect(matched).not.toContain(MATCH_NOTICE_TEXT);
    expect(matched).not.toContain(CLOSED_TEXT);
    const completed = notificationLines(
      item("proposal", notificationLatch("completed")), NOW);
    expect(completed.at(-1)).toBe("完了"); // STATUS_TEXT.completed
  });

  it("proposal×matched最小形: 見出しはHIDDEN_PROPOSAL_TEXT", () => {
    const lines = notificationLines(
      item("proposal", notificationLatch("matched", {}, minimalProposal)), NOW);
    expect(lines[0]).toBe(HIDDEN_PROPOSAL_TEXT);
    expect(lines.at(-1)).toBe("成立済み");
  });

  it("nearby_candidate: 存在文言のみ(statusによらず・サマリ・一致度・人数を出さない)", () => {
    for (const status of ["candidate", "expired"]) {
      expect(notificationLines(item("nearby_candidate", notificationLatch(status)), NOW))
        .toEqual([NEARBY_NOTICE_TEXT]);
    }
  });

  it("attendance_request: 設問のみ(回答導線は詳細画面・FR-46)", () => {
    expect(notificationLines(item("attendance_request", notificationLatch("completed")), NOW))
      .toEqual([ATTENDANCE_QUESTION]);
  });

  it("latch=null(防御): フォールバック文言", () => {
    expect(notificationLines(item("proposal", null), NOW))
      .toEqual([NOTICE_FALLBACK_TEXT]);
  });
});

describe("notificationHref(design §2.3・引用#20)", () => {
  it("nearby行とlatch=null行はnull(candidateはGET /v1/latches対象外のため)", () => {
    expect(notificationHref(item("nearby_candidate", notificationLatch("candidate")))).toBeNull();
    expect(notificationHref(item("proposal", null))).toBeNull();
  });

  it("proposal行(終了含む)とattendance行は#/latches/{id}へ", () => {
    expect(notificationHref(item("proposal", notificationLatch("cancelled"))))
      .toBe(`#/latches/${LATCH_ID}`);
    expect(notificationHref(item("proposal", notificationLatch("proposed"))))
      .toBe(`#/latches/${LATCH_ID}`);
    expect(notificationHref(item("attendance_request", notificationLatch("completed"))))
      .toBe(`#/latches/${LATCH_ID}`);
  });
});

describe("noticeTimeText(created_atのJST書式)", () => {
  it("+09:00付きISOをM月D日 HH:MMへ(zero埋め)", () => {
    expect(noticeTimeText("2026-10-01T09:05:00+09:00")).toBe("10月1日 09:05");
  });

  it("UTC入力はJSTへ変換(00:05Z=09:05+09:00)", () => {
    expect(noticeTimeText("2026-10-01T00:05:00Z")).toBe("10月1日 09:05");
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npx vitest run tests/latch-notice.test.js`
Expected: 12件すべてFAIL/ERROR(view.jsに `notificationLines` 等が無いため) / 他はPASS

- [ ] **Step 3: 最小実装**

**(1) `frontend/src/latch/texts.js` の末尾へ追記**(既存定数は1行も変更しない):

```js
// --- M3 ws-8追記: お知らせ・設定(design §2.8) -----------------------------

// お知らせpopover(03 §2・§4の様式・design §2.3)
export const NOTICE_EMPTY_TITLE = "まだ新しい候補はありません";
export const NOTICE_EMPTY_NOTE = "条件が合うと、ここに静かに届きます。";
export const PROPOSAL_NOTICE_TITLE = "LATCH候補があります。"; // 03 §4の様式
export const MATCH_NOTICE_TEXT = "一致度が高い候補です。"; // 03 §4の様式
export const NEARBY_NOTICE_TEXT = "近い条件の候補があるようです。"; // §5-2①(設計判断)
export const NOTICE_FALLBACK_TEXT = "LATCHからのお知らせがあります。"; // latch=null防御

// 通知許可(design §2.5・D-18のM3での範囲=ブラウザ許可状態のみ)
export const PERMISSION_GRANTED_TEXT = "通知は許可されています";
export const PERMISSION_DEFAULT_TEXT = "通知は許可されていません";
export const PERMISSION_DENIED_TEXT =
  "通知はブラウザの設定でブロックされています。ブラウザの設定から変更できます。";
export const PERMISSION_UNSUPPORTED_TEXT = "この環境では通知を利用できません。";
export const PERMISSION_NOTE_TEXT = "許可しなくても、アプリ内のお知らせで確認できます。";
export const PERMISSION_REQUEST_LABEL = "通知の許可を求める";

// ブロック管理・登録(design §2.6〜§2.7)
export const BLOCKS_EMPTY_TEXT = "ブロックしているユーザーはいません";
export const BLOCK_UNBLOCK_LABEL = "解除する";
export const BLOCK_CANCEL_LABEL = "やめる";
export const BLOCK_CONFIRM_SUFFIX = "さんのブロックを解除しますか?"; // 前に表示名を結合
export const BLOCK_ENTRY_TEXT = "このユーザーをブロックする"; // 成立済み詳細の導線
export const BLOCK_CONFIRM_TEXT = "ブロックすると、このやりとりは利用できなくなります。";
export const BLOCK_SUBMIT_LABEL = "ブロックする";
export const BLOCK_SELECT_LABEL = "ブロックする相手";
export const BLOCK_SELECT_ERROR_TEXT = "相手を選んでください。";
export const BLOCK_TOAST_TEXT = "ブロックしました";
export const BLOCK_ERROR_TEXT = "送信できませんでした。";
```

**(2) `frontend/src/latch/view.js`** — 冒頭のimportを次へ変更し(`jstParts` を追加・既存importに `ATTENDANCE_QUESTION`・`HIDDEN_PROPOSAL_TEXT`・`NEARBY_NOTICE_TEXT`・`NOTICE_FALLBACK_TEXT`・`PROPOSAL_NOTICE_TITLE`・`MATCH_NOTICE_TEXT`・`STATUS_TEXT` を追加):

```js
import { formatCategory, jstParts } from "../intent/format.js";
import {
  ANSWERED_TEXT,
  ATTENDANCE_QUESTION,
  CLOSED_TEXT,
  DEADLINE_CLOSED_TEXT,
  DEADLINE_SOON_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  MATCH_NOTICE_TEXT,
  MATCH_LEVEL_TEXT,
  NEARBY_NOTICE_TEXT,
  NOTICE_FALLBACK_TEXT,
  PROPOSAL_NOTICE_TITLE,
  STATUS_TEXT,
} from "./texts.js";
```

ファイル末尾(既存 `escapeHtml` の後)へ追記:

```js
// --- M3 ws-8追記: お知らせ行の純関数(design §2.3) ---------------------------

// created_atのJST書式(お知らせ行・ブロック日の日付表示。年は出さない)
export const noticeTimeText = (iso) => {
  const p = jstParts(iso);
  return `${p.month}月${p.day}日 ${p.hour}:${p.minute}`;
};

const CLOSED_STATUSES = new Set(["expired", "rejected", "cancelled"]);
const MATCHED_NOTICE_STATUSES = new Set(["matched", "completed"]);

// お知らせ行の文言(type×latch.statusのマトリクス・design §2.3)。
// notifications応答にmy_responseがないため終了行は一律CLOSED_TEXT
// (自分の操作履歴を表示しない・§5-2②)。nearbyは存在通知のみ。
export const notificationLines = (item, nowIso) => {
  if (item.type === "nearby_candidate") return [NEARBY_NOTICE_TEXT];
  if (item.type === "attendance_request") return [ATTENDANCE_QUESTION];
  const latch = item.latch;
  if (!latch) return [NOTICE_FALLBACK_TEXT]; // 防御(全type)
  if (CLOSED_STATUSES.has(latch.status)) return [CLOSED_TEXT]; // 一文統一
  if (MATCHED_NOTICE_STATUSES.has(latch.status)) {
    const title = isMinimalProposal(latch)
      ? HIDDEN_PROPOSAL_TEXT
      : [latch.proposal.time_summary, latch.proposal.area_name]
          .filter(Boolean)
          .join(" ");
    return [
      title,
      ...(conditionSummaryLines(latch) ?? []),
      STATUS_TEXT[latch.status] ?? latch.status, // バッジ(回答促しは出さない)
    ];
  }
  // proposed / partial_accept
  if (isMinimalProposal(latch)) {
    return [
      HIDDEN_PROPOSAL_TEXT,
      MATCH_NOTICE_TEXT,
      remainingTimeText(latch.response_deadline, nowIso),
    ];
  }
  return [
    PROPOSAL_NOTICE_TITLE,
    ...(conditionSummaryLines(latch) ?? []),
    MATCH_NOTICE_TEXT,
  ];
};

// タップ先(nearbyとlatch=nullはリンクなし・引用#20)
export const notificationHref = (item) => {
  if (item.type === "nearby_candidate") return null;
  if (!item.latch) return null;
  return `#/latches/${item.latch.id}`;
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-notice 12+router 9+既存=**185 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/texts.js frontend/src/latch/view.js frontend/tests/latch-notice.test.js
git commit -m "feat: add notification row text matrix and new screen constants (M3 ws-8)"
```

### Task 3: notice.js — お知らせpopover flow

**Files:**
- Create: `frontend/src/latch/notice.js`
- Test: `frontend/tests/latch-notice.test.js`(末尾へflow分9件追記)

**Interfaces:**
- Consumes: Task 2の `notificationLines`・`notificationHref`・`noticeTimeText`・`NOTICE_EMPTY_TITLE`・`NOTICE_EMPTY_NOTE`・既存 `escapeHtml`
- Produces: `createNotice({ client, el }) -> { preload(): Promise, open(): Promise, loadMore(): Promise }`。el = `{ list: HTMLElement(#noticeList), moreButton: HTMLButtonElement(#noticeMoreButton), dot: HTMLElement(#notificationDot) }`。preload=起動時1回の取得+ドット判定+初回描画データ保持・open=開いた時の取り直し(初回はpreload結果を使い二重取得しない)+未読行のPOST read+ドット再判定・loadMore=追頁。Task 7のmain.jsが消費

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-notice.test.js` の末尾へ追記(import部へ `createNotice` を追加 — `import { createNotice } from "../src/latch/notice.js";`)。fixture(`item`・`notificationLatch`)はTask 2と共用:

```js
const mountNotice = (callImpl) => {
  const client = { call: vi.fn(callImpl ?? (async () => ({ items: [], next_cursor: null }))) };
  const el = {
    list: document.createElement("div"),
    moreButton: document.createElement("button"),
    dot: document.createElement("span"),
  };
  el.moreButton.hidden = true;
  el.dot.hidden = true;
  const notice = createNotice({ client, el });
  return { client, el, notice };
};

describe("createNotice(design §2.1〜§2.2)", () => {
  it("preloadはGET /v1/notifications?limit=20を1回・未読があればドット表示+行描画", async () => {
    const { client, el, notice } = mountNotice(async () => ({
      items: [item("proposal", notificationLatch("proposed"))],
      next_cursor: null,
    }));
    await notice.preload();
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/notifications?limit=20");
    expect(el.dot.hidden).toBe(false);
    expect(el.list.querySelectorAll(".notice-item").length).toBe(1);
  });

  it("全件既読・0件ならドットは非表示のまま", async () => {
    const { el, notice } = mountNotice(async () => ({
      items: [
        item("proposal", notificationLatch("proposed"), {
          read_at: "2026-10-01T08:00:00+09:00",
        }),
      ],
      next_cursor: null,
    }));
    await notice.preload();
    expect(el.dot.hidden).toBe(true);
  });

  it("openは未読行へだけPOST readする(既読行には呼ばない)", async () => {
    const { client, notice } = mountNotice(async (method) => {
      if (method === "POST") return null; // 204
      return {
        items: [
          item("proposal", notificationLatch("proposed")), // 未読
          item("proposal", notificationLatch("proposed"), {
            id: "n2",
            read_at: "2026-10-01T08:00:00+09:00",
          }),
        ],
        next_cursor: null,
      };
    });
    await notice.open();
    const posts = client.call.mock.calls.filter(([method]) => method === "POST");
    expect(posts).toEqual([["POST", "/v1/notifications/n1/read"]]);
  });

  it("read完了後にドットを再判定する(全未読→既読化で消灯)", async () => {
    const { el, notice } = mountNotice(async (method) => {
      if (method === "POST") return null;
      return { items: [item("proposal", notificationLatch("proposed"))], next_cursor: null };
    });
    await notice.open();
    expect(el.dot.hidden).toBe(true); // 開いた=見た
  });

  it("POST readに失敗した行は握る(ドット点灯維持・次回開いた時の未読対象)", async () => {
    const { el, notice } = mountNotice(async (method) => {
      if (method === "POST") throw new Error("x");
      return { items: [item("proposal", notificationLatch("proposed"))], next_cursor: null };
    });
    await notice.open(); // throwされてもopen自体は正常終了
    expect(el.dot.hidden).toBe(false);
  });

  it("next_cursorが残れば「もっと見る」を表示し追頁でcursorを渡す", async () => {
    const pages = [
      { items: [item("proposal", notificationLatch("proposed"))], next_cursor: "c1" },
      {
        items: [
          item("proposal", notificationLatch("proposed"), {
            id: "n2",
            read_at: "2026-10-01T08:00:00+09:00",
          }),
        ],
        next_cursor: null,
      },
    ];
    const { client, el, notice } = mountNotice(async (method) => {
      if (method === "POST") return null;
      return pages.shift();
    });
    await notice.open();
    expect(el.moreButton.hidden).toBe(false);
    await notice.loadMore();
    expect(client.call.mock.calls.at(-1)).toEqual([
      "GET",
      "/v1/notifications?limit=20&cursor=c1",
    ]);
    expect(el.list.querySelectorAll(".notice-item").length).toBe(2);
    expect(el.moreButton.hidden).toBe(true);
  });

  it("0件で空状態文言(まだ新しい候補はありません)", async () => {
    const { el, notice } = mountNotice();
    await notice.open();
    expect(el.list.textContent).toContain(NOTICE_EMPTY_TITLE);
    expect(el.list.textContent).toContain(NOTICE_EMPTY_NOTE);
  });

  it("取得失敗は前回表示を維持(空状態も出さない)", async () => {
    let fail = false;
    const { el, notice } = mountNotice(async () => {
      if (fail) throw new Error("x");
      return { items: [item("proposal", notificationLatch("proposed"))], next_cursor: null };
    });
    await notice.preload();
    const before = el.list.innerHTML;
    fail = true;
    await notice.open();
    expect(el.list.innerHTML).toBe(before);
  });

  it("preload後の初回openはGETしない(二重取得なし)・nearby行はリンク化しない", async () => {
    const { client, el, notice } = mountNotice(async (method) => {
      if (method === "POST") return null;
      return {
        items: [
          item("nearby_candidate", notificationLatch("candidate"), {
            read_at: "2026-10-01T08:00:00+09:00",
          }),
        ],
        next_cursor: null,
      };
    });
    await notice.preload();
    await notice.open();
    expect(client.call).toHaveBeenCalledTimes(1); // preloadのGETのみ
    expect(el.list.querySelectorAll("a.notice-item").length).toBe(0); // nearbyは<div>
    expect(el.list.querySelectorAll(".notice-item").length).toBe(1);
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npx vitest run tests/latch-notice.test.js`
Expected: 追加9件がFAIL/ERROR(notice.jsが存在しない)/ Task 2の12件はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/latch/notice.js` を作成:

```js
// お知らせ(M3 ws-8 design §2.1〜§2.2)。topbarベルpopoverの中身。
// popoverを開くたびGET /v1/notifications?limit=20を1回取り直し(開いた時点の
// 最新)・表示された未読行へPOST read(開いた=見た)・起動時1回のpreload結果を
// 初回描画に使い二重取得しない。取得失敗時は前回表示を維持・空状態も出さない
// (再訪で再試行・detailの再取得失敗と同型)。
import { NOTICE_EMPTY_NOTE, NOTICE_EMPTY_TITLE } from "./texts.js";
import {
  escapeHtml,
  notificationHref,
  notificationLines,
  noticeTimeText,
} from "./view.js";

const noticeRowHtml = (item, nowIso) => {
  const lines = notificationLines(item, nowIso)
    .map((line) => `<span class="notice-line">${escapeHtml(line)}</span>`)
    .join("");
  const time = `<span class="notice-time">${escapeHtml(
    noticeTimeText(item.created_at),
  )}</span>`;
  const cls = `notice-item${item.read_at == null ? " unread" : ""}`;
  const href = notificationHref(item);
  return href
    ? `<a class="${cls}" href="${href}">${lines}${time}</a>`
    : `<div class="${cls}">${lines}${time}</div>`; // タップ先なし(nearby等)
};

export const createNotice = ({ client, el }) => {
  let items = [];
  let cursor = null;
  let preloaded = false;

  const hasUnread = () => items.some((item) => item.read_at == null);
  const updateDot = () => {
    el.dot.hidden = !hasUnread();
  };

  const render = () => {
    const nowIso = new Date().toISOString();
    el.list.innerHTML = items.length
      ? items.map((item) => noticeRowHtml(item, nowIso)).join("")
      : `<p class="notice-empty"><strong>${NOTICE_EMPTY_TITLE}</strong><span>${NOTICE_EMPTY_NOTE}</span></p>`;
    el.moreButton.hidden = cursor == null; // cursorは不透明文字列
  };

  const markRead = async () => {
    const unread = items.filter((item) => item.read_at == null);
    await Promise.allSettled(
      unread.map((item) =>
        client
          .call("POST", `/v1/notifications/${item.id}/read`)
          .then(() => {
            item.read_at = new Date().toISOString();
          }),
      ),
    );
    updateDot(); // 失敗した行は次回開いた時の未読対象のまま
  };

  const preload = async () => {
    const data = await client.call("GET", "/v1/notifications?limit=20");
    items = data.items;
    cursor = data.next_cursor;
    preloaded = true;
    render();
    updateDot();
  };

  const open = async () => {
    if (preloaded) {
      preloaded = false; // 起動時取得の結果を初回描画に使い二重取得しない
      render();
    } else {
      try {
        const data = await client.call("GET", "/v1/notifications?limit=20");
        items = data.items;
        cursor = data.next_cursor;
        render();
      } catch {
        return; // 前回表示を維持(空状態も出さない)
      }
    }
    await markRead();
  };

  const loadMore = async () => {
    if (cursor == null) return;
    const data = await client.call(
      "GET",
      `/v1/notifications?limit=20&cursor=${encodeURIComponent(cursor)}`,
    );
    items = items.concat(data.items);
    cursor = data.next_cursor;
    render();
  };

  return { preload, open, loadMore };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-notice 21=12+9・全体 **194 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/notice.js frontend/tests/latch-notice.test.js
git commit -m "feat: add notice popover flow with unread dot and mark-read (M3 ws-8)"
```

### Task 4: permission.js — 通知許可コントロール(DI注入)

**Files:**
- Create: `frontend/src/latch/permission.js`
- Test: `frontend/tests/latch-settings.test.js`(新規・7件。Task 7でsettings分を追記)

**Interfaces:**
- Consumes: §2-2のPERMISSION_*定数(Task 2)・既存 `escapeHtml`
- Produces: `permissionText(permission) -> string`(状態→文言写像の純関数・undefined/未知は非対応文言)・`createPermissionControl({ notificationApi, mount }) -> { render(): void }`。notificationApiは `{ permission: "granted"|"default"|"denied", requestPermission(): Promise<string> }` を期待し**undefinedも可**(API不在=非対応環境。happy-domにNotificationがないため試験は必須DI)。renderは状態文言+補足文を描画し、defaultのときのみ[通知の許可を求める]ボタンを置く(クリックでrequestPermission→結果を内部状態へ反映して再描画)。Task 7のmain.js・settings.jsが消費

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-settings.test.js` を新規作成:

```js
import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  PERMISSION_DEFAULT_TEXT,
  PERMISSION_DENIED_TEXT,
  PERMISSION_GRANTED_TEXT,
  PERMISSION_NOTE_TEXT,
  PERMISSION_REQUEST_LABEL,
  PERMISSION_UNSUPPORTED_TEXT,
} from "../src/latch/texts.js";
import {
  createPermissionControl,
  permissionText,
} from "../src/latch/permission.js";

const mountPermission = (notificationApi) => {
  const mount = document.createElement("div");
  const control = createPermissionControl({ notificationApi, mount });
  return { mount, control };
};

describe("permissionText(状態→文言写像・design §2.5)", () => {
  it("4状態+API不在の文言", () => {
    expect(permissionText("granted")).toBe(PERMISSION_GRANTED_TEXT);
    expect(permissionText("default")).toBe(PERMISSION_DEFAULT_TEXT);
    expect(permissionText("denied")).toBe(PERMISSION_DENIED_TEXT);
    expect(permissionText(undefined)).toBe(PERMISSION_UNSUPPORTED_TEXT);
  });
});

describe("createPermissionControl(design §2.5・D-18)", () => {
  it("granted: 状態文言+補足文のみ(ボタンなし)", () => {
    const { mount, control } = mountPermission({ permission: "granted" });
    control.render();
    expect(mount.textContent).toContain(PERMISSION_GRANTED_TEXT);
    expect(mount.textContent).toContain(PERMISSION_NOTE_TEXT); // D-18フォールバック保証
    expect(mount.querySelector("button")).toBeNull();
  });

  it("default: [通知の許可を求める]ボタンを置く", () => {
    const { mount, control } = mountPermission({
      permission: "default",
      requestPermission: vi.fn(async () => "granted"),
    });
    control.render();
    expect(mount.textContent).toContain(PERMISSION_DEFAULT_TEXT);
    expect(mount.querySelector("[data-role=permission-request]").textContent)
      .toBe(PERMISSION_REQUEST_LABEL);
  });

  it("denied: 案内文言のみ(再要求APIが効かないため操作なし)", () => {
    const { mount, control } = mountPermission({ permission: "denied" });
    control.render();
    expect(mount.textContent).toContain(PERMISSION_DENIED_TEXT);
    expect(mount.querySelector("button")).toBeNull();
  });

  it("API不在(非対応環境): 非対応文言(操作なし)", () => {
    const { mount, control } = mountPermission(undefined);
    control.render();
    expect(mount.textContent).toContain(PERMISSION_UNSUPPORTED_TEXT);
    expect(mount.querySelector("button")).toBeNull();
  });

  it("default→requestPermissionがgrantedを返せば再描画でgranted文言へ", async () => {
    const api = {
      permission: "default",
      requestPermission: vi.fn(async () => "granted"),
    };
    const { mount, control } = mountPermission(api);
    control.render();
    mount.querySelector("[data-role=permission-request]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(api.requestPermission).toHaveBeenCalledTimes(1);
    expect(mount.textContent).toContain(PERMISSION_GRANTED_TEXT);
  });

  it("requestPermissionの拒否(default戻り)と例外はdefault維持(再表示)", async () => {
    // 拒否: 戻り値"default"
    const denied = {
      permission: "default",
      requestPermission: vi.fn(async () => "default"),
    };
    const a = mountPermission(denied);
    a.control.render();
    a.mount.querySelector("[data-role=permission-request]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(a.mount.textContent).toContain(PERMISSION_DEFAULT_TEXT);
    // 例外
    const throwing = {
      permission: "default",
      requestPermission: vi.fn(() => Promise.reject(new Error("x"))),
    };
    const b = mountPermission(throwing);
    b.control.render();
    b.mount.querySelector("[data-role=permission-request]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(b.mount.textContent).toContain(PERMISSION_DEFAULT_TEXT);
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npx vitest run tests/latch-settings.test.js`
Expected: 7件すべてFAIL/ERROR(permission.jsが存在しない)

- [ ] **Step 3: 最小実装**

`frontend/src/latch/permission.js` を作成:

```js
// 通知許可コントロール(M3 ws-8 design §2.5・D-18)。ブラウザNotification API
// は直接参照せず注入する(happy-domにNotificationがないため試験必須のDI・
// client注入と同型)。実送信(FCM web)はM4 — この画面はブラウザの許可状態の
// 表示と取り直しのみ(トークン登録を伴わないためサーバ通信は発生しない)。
import {
  PERMISSION_DEFAULT_TEXT,
  PERMISSION_DENIED_TEXT,
  PERMISSION_GRANTED_TEXT,
  PERMISSION_NOTE_TEXT,
  PERMISSION_REQUEST_LABEL,
  PERMISSION_UNSUPPORTED_TEXT,
} from "./texts.js";
import { escapeHtml } from "./view.js";

export const permissionText = (permission) =>
  permission === "granted"
    ? PERMISSION_GRANTED_TEXT
    : permission === "default"
      ? PERMISSION_DEFAULT_TEXT
      : permission === "denied"
        ? PERMISSION_DENIED_TEXT
        : PERMISSION_UNSUPPORTED_TEXT; // API不在(非対応環境)・未知値

export const createPermissionControl = ({ notificationApi, mount }) => {
  let permission = notificationApi?.permission; // 初期スナップショット

  const render = () => {
    // granted・denied・API不在は操作なし(再要求が効くのはdefaultのみ)
    const buttonHtml =
      permission === "default"
        ? `<button type="button" class="permission-request" data-role="permission-request">${PERMISSION_REQUEST_LABEL}</button>`
        : "";
    mount.innerHTML = `
      <p class="permission-status" data-role="permission-status">${escapeHtml(
        permissionText(permission),
      )}</p>
      ${buttonHtml}
      <p class="permission-note">${escapeHtml(PERMISSION_NOTE_TEXT)}</p>
    `;
    const button = mount.querySelector("[data-role=permission-request]");
    if (button) {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          permission = await notificationApi.requestPermission();
        } catch {
          // 拒否・失敗は現在の状態を維持(再表示)
        }
        render();
      });
    }
  };

  return { render };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-settings 7+ latch-notice 21+router 9+既存=**201 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/permission.js frontend/tests/latch-settings.test.js
git commit -m "feat: add notification permission control with DI (M3 ws-8)"
```

### Task 5: blocks.js — ブロック一覧・解除flow

**Files:**
- Create: `frontend/src/latch/blocks.js`
- Test: `frontend/tests/latch-blocks.test.js`(新規・7件)

**Interfaces:**
- Consumes: Task 2のBLOCK_*定数・既存 `RATE_LIMIT_TEXT`・Task 2の `noticeTimeText`(ブロック日表示)・既存 `escapeHtml`
- Produces: `createBlocksSection({ client, el, modal }) -> { load(): Promise, loadMore(): Promise }`。el = `{ list: HTMLElement(#blockList), moreButton: HTMLButtonElement(#blockMoreButton) }`・modalは `#unblockModal` のHTMLElement(reportModalと同型backdrop・中身はrenderModalが組み立てる)。解除は確認モーダル→DELETE→204で行除去(カーソル保持)・404で一覧再取得収束。Task 7のmain.js・settings.jsが消費

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-blocks.test.js` を新規作成:

```js
import { describe, expect, it, vi } from "vitest";
import {
  BLOCKS_EMPTY_TEXT,
  BLOCK_CANCEL_LABEL,
  BLOCK_CONFIRM_SUFFIX,
  BLOCK_UNBLOCK_LABEL,
  RATE_LIMIT_TEXT,
} from "../src/latch/texts.js";
import { createBlocksSection } from "../src/latch/blocks.js";

const PEER = "33333333-3333-4333-8333-333333333333";
const OTHER = "44444444-4444-4444-8444-444444444444";
const block = (id, name) => ({
  blocked_id: id,
  display_name: name,
  created_at: "2026-10-01T09:00:00+09:00",
});
const apiError = (status, code) =>
  Object.assign(new Error("x"), { name: "ApiError", status, code });

const mountBlocks = (callImpl) => {
  const client = { call: vi.fn(callImpl ?? (async () => ({ items: [], next_cursor: null }))) };
  const el = {
    list: document.createElement("div"),
    moreButton: document.createElement("button"),
  };
  el.moreButton.hidden = true;
  const modal = document.createElement("div");
  modal.hidden = true;
  const blocks = createBlocksSection({ client, el, modal });
  return { client, el, modal, blocks };
};

describe("ブロック管理(design §2.6)", () => {
  it("loadはGET /v1/users/me/blocks?limit=20・行=表示名+ブロック日+解除ボタン(表示名はエスケープ)", async () => {
    const { client, el, blocks } = mountBlocks(async () => ({
      items: [block(PEER, '<script>alert(1)</script>')],
      next_cursor: null,
    }));
    await blocks.load();
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/users/me/blocks?limit=20");
    expect(el.list.querySelectorAll(".block-row").length).toBe(1);
    expect(el.list.querySelector(".block-name").innerHTML)
      .not.toContain("<script>");
    expect(el.list.querySelector(".block-name").textContent)
      .toBe('<script>alert(1)</script>');
    expect(el.list.querySelector(".block-date").textContent).toBe("10月1日 09:00");
    expect(el.list.querySelector("[data-role=unblock-entry]").textContent)
      .toBe(BLOCK_UNBLOCK_LABEL);
  });

  it("0件は空状態文言", async () => {
    const { el, blocks } = mountBlocks();
    await blocks.load();
    expect(el.list.textContent).toContain(BLOCKS_EMPTY_TEXT);
  });

  it("next_cursorが残れば「もっと見る」・追頁でcursorを渡す", async () => {
    const pages = [
      { items: [block(PEER, "相手")], next_cursor: "c1" },
      { items: [block(OTHER, "他者")], next_cursor: null },
    ];
    const { client, el, blocks } = mountBlocks(async () => pages.shift());
    await blocks.load();
    expect(el.moreButton.hidden).toBe(false);
    await blocks.loadMore();
    expect(client.call.mock.calls.at(-1)).toEqual([
      "GET",
      "/v1/users/me/blocks?limit=20&cursor=c1",
    ]);
    expect(el.list.querySelectorAll(".block-row").length).toBe(2);
    expect(el.moreButton.hidden).toBe(true);
  });

  it("解除は確認モーダル(表示名入り文言)を挟み[解除する]でDELETE→204で行除去", async () => {
    const { client, el, modal, blocks } = mountBlocks(async (method) => {
      if (method === "DELETE") return null; // 204
      return { items: [block(PEER, "相手")], next_cursor: null };
    });
    await blocks.load();
    el.list.querySelector("[data-role=unblock-entry]").click();
    expect(modal.hidden).toBe(false);
    expect(modal.textContent).toContain(`相手${BLOCK_CONFIRM_SUFFIX}`);
    modal.querySelector("[data-role=unblock-submit]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(client.call).toHaveBeenCalledWith("DELETE", `/v1/users/${PEER}/block`);
    expect(modal.hidden).toBe(true);
    expect(el.list.querySelectorAll(".block-row").length).toBe(0); // 行除去
    expect(el.list.textContent).toContain(BLOCKS_EMPTY_TEXT);
  });

  it("確認モーダルの[やめる]ではDELETEしない", async () => {
    const { client, el, modal, blocks } = mountBlocks(async () => ({
      items: [block(PEER, "相手")],
      next_cursor: null,
    }));
    await blocks.load();
    el.list.querySelector("[data-role=unblock-entry]").click();
    modal.querySelector("[data-role=unblock-cancel]").click();
    expect(client.call).toHaveBeenCalledTimes(1); // GET のみ
    expect(modal.hidden).toBe(true);
  });

  it("404(既に解除済み)は通信エラー扱いにせず一覧を取り直して収束", async () => {
    let failDelete = false;
    let listCalls = 0;
    const { el, modal, blocks } = mountBlocks(async (method) => {
      if (method === "DELETE") {
        if (failDelete) throw apiError(404, "NOT_FOUND");
        return null;
      }
      listCalls += 1;
      return listCalls === 1
        ? { items: [block(PEER, "相手")], next_cursor: null }
        : { items: [], next_cursor: null };
    });
    await blocks.load();
    failDelete = true;
    el.list.querySelector("[data-role=unblock-entry]").click();
    modal.querySelector("[data-role=unblock-submit]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(listCalls).toBe(2); // 一覧を取り直した(収束)
    expect(el.list.textContent).toContain(BLOCKS_EMPTY_TEXT);
  });

  it("429は既存RATE_LIMIT_TEXT・モーダルは閉じない", async () => {
    const { client, el, modal, blocks } = mountBlocks(async (method) => {
      if (method === "DELETE") throw apiError(429, "RATE_LIMITED");
      return { items: [block(PEER, "相手")], next_cursor: null };
    });
    await blocks.load();
    el.list.querySelector("[data-role=unblock-entry]").click();
    modal.querySelector("[data-role=unblock-submit]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(client.call).toHaveBeenCalledTimes(2); // GET + DELETE
    expect(modal.hidden).toBe(false);
    expect(modal.textContent).toContain(RATE_LIMIT_TEXT);
    expect(modal.textContent).toContain(BLOCK_CANCEL_LABEL);
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npx vitest run tests/latch-blocks.test.js`
Expected: 7件すべてFAIL/ERROR(blocks.jsが存在しない)

- [ ] **Step 3: 最小実装**

`frontend/src/latch/blocks.js` を作成:

```js
// ブロック管理(M3 ws-8 design §2.6)。設定画面の2セクションの一つ。一覧+
// 解除(確認モーダル挟み — 再登録導線が成立済み詳細しかないため誤解除の回復
// コストが高い)。解除の204で行除去(カーソル保持)・404(既に解除済み)は通信
// エラー扱いにせず一覧を取り直して収束させる。遡及効果なしはUIで説明しない。
import {
  BLOCK_CANCEL_LABEL,
  BLOCK_CONFIRM_SUFFIX,
  BLOCK_ERROR_TEXT,
  BLOCK_UNBLOCK_LABEL,
  BLOCKS_EMPTY_TEXT,
  RATE_LIMIT_TEXT,
} from "./texts.js";
import { escapeHtml, noticeTimeText } from "./view.js";

export const createBlocksSection = ({ client, el, modal }) => {
  let items = [];
  let cursor = null;
  let pending = null; // 解除確認中の行

  const render = () => {
    el.list.innerHTML = items.length
      ? items
          .map(
            (item) => `<div class="block-row">
              <span class="block-name">${escapeHtml(item.display_name)}</span>
              <span class="block-date">${escapeHtml(noticeTimeText(item.created_at))}</span>
              <button type="button" class="block-unblock" data-role="unblock-entry" data-id="${item.blocked_id}">${BLOCK_UNBLOCK_LABEL}</button>
            </div>`,
          )
          .join("")
      : `<p class="latch-empty"><strong>${BLOCKS_EMPTY_TEXT}</strong></p>`;
    el.moreButton.hidden = cursor == null;
    wireRows();
  };

  const wireRows = () => {
    for (const button of el.list.querySelectorAll("[data-role=unblock-entry]")) {
      button.addEventListener("click", () => {
        pending = items.find((item) => item.blocked_id === button.dataset.id) ?? null;
        renderModal();
      });
    }
  };

  const showError = (text) => {
    const errorEl = modal.querySelector("[data-role=unblock-error]");
    errorEl.textContent = text;
    errorEl.hidden = false;
  };

  const renderModal = () => {
    if (!pending) return;
    modal.innerHTML = `
      <section class="confirmation block-dialog" role="dialog" aria-modal="true" aria-labelledby="unblockTitle">
        <button type="button" class="modal-close" data-role="unblock-close" aria-label="閉じる"><i class="ph ph-x"></i></button>
        <p class="section-kicker centered"><span></span>BLOCK</p>
        <h2 id="unblockTitle">ブロックの解除</h2>
        <p class="block-confirm">${escapeHtml(pending.display_name)}${BLOCK_CONFIRM_SUFFIX}</p>
        <p class="block-error" data-role="unblock-error" role="alert" hidden></p>
        <div class="block-actions">
          <button type="button" class="block-submit" data-role="unblock-submit">${BLOCK_UNBLOCK_LABEL}</button>
          <button type="button" class="block-cancel" data-role="unblock-cancel">${BLOCK_CANCEL_LABEL}</button>
        </div>
      </section>
    `;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    modal.querySelector("[data-role=unblock-close]").addEventListener("click", close);
    modal.querySelector("[data-role=unblock-cancel]").addEventListener("click", close);
    modal.querySelector("[data-role=unblock-submit]").addEventListener("click", submit);
  };

  const close = () => {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    pending = null;
  };

  const submit = async () => {
    const target = pending;
    if (!target) return;
    try {
      await client.call("DELETE", `/v1/users/${target.blocked_id}/block`);
      items = items.filter((item) => item.blocked_id !== target.blocked_id);
      close();
      render(); // 行単位の除去(カーソル保持)
    } catch (err) {
      if (err?.status === 404) {
        close(); // 既に解除済み — 一覧を取り直して収束
        load().catch(() => {});
        return;
      }
      showError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : BLOCK_ERROR_TEXT);
    }
  };

  // backdropクリックで閉じる(reportModalと同型・一度だけwire)
  modal.addEventListener("click", (event) => {
    if (event.target === modal && !modal.hidden) close();
  });

  const load = async () => {
    const data = await client.call("GET", "/v1/users/me/blocks?limit=20");
    items = data.items;
    cursor = data.next_cursor;
    render();
  };

  const loadMore = async () => {
    if (cursor == null) return;
    const data = await client.call(
      "GET",
      `/v1/users/me/blocks?limit=20&cursor=${encodeURIComponent(cursor)}`,
    );
    items = items.concat(data.items);
    cursor = data.next_cursor;
    render();
  };

  return { load, loadMore };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-blocks 7+latch-settings 7+latch-notice 21+router 9+既存=**208 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/blocks.js frontend/tests/latch-blocks.test.js
git commit -m "feat: add block management section with confirm modal and 404 convergence (M3 ws-8)"
```

### Task 6: blockFlow.js — ブロック登録モーダル(§5-1承認分)

**Files:**
- Create: `frontend/src/latch/blockFlow.js`
- Test: `frontend/tests/latch-block-flow.test.js`(新規・5件。Task 7でdetail統合1件を追記)

**Interfaces:**
- Consumes: Task 2のBLOCK_*定数・既存 `RATE_LIMIT_TEXT`・既存 `escapeHtml`
- Produces: `createBlockFlow({ client, chrome, modal, onBlocked }) -> { open({ participants, meId }): void, submit(): Promise, close(): void }`。open — participants(詳細応答の`participants`配列)から自分以外を抽出し、1人なら相手固定(対象表示)・2人以上なら選択ラジオ。submit — `POST /v1/users/{user_id}/block`(**パス指定・bodyなし**)→201でモーダル閉鎖+トースト「ブロックしました」+`onBlocked()`呼び出し(詳細再取得・D-23の表示収束)。onBlockedは省略可(テスト・未接続時)。Task 7のmain.js(注入)とdetail.js(呼び出し)が消費

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-block-flow.test.js` を新規作成:

```js
import { describe, expect, it, vi } from "vitest";
import {
  BLOCK_CONFIRM_TEXT,
  BLOCK_SELECT_ERROR_TEXT,
  BLOCK_TOAST_TEXT,
  RATE_LIMIT_TEXT,
} from "../src/latch/texts.js";
import { createBlockFlow } from "../src/latch/blockFlow.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const THIRD = "44444444-4444-4444-8444-444444444444";
const apiError = (status, code) =>
  Object.assign(new Error("x"), { name: "ApiError", status, code });

const makeFlow = (callImpl) => {
  const client = { call: vi.fn(callImpl ?? (async () => ({ blocked_id: PEER }))) };
  const chrome = { showToast: vi.fn() };
  const onBlocked = vi.fn(async () => {});
  const modal = document.createElement("div");
  modal.hidden = true;
  const flow = createBlockFlow({ client, chrome, modal, onBlocked });
  return { client, chrome, onBlocked, modal, flow };
};

// happy-domはラジオの排他(uncheck)を実装しないため全解除→選択(latch-reportと同型)
const selectBlockee = (modal, value) => {
  for (const radio of modal.querySelectorAll('input[name="blockee"]')) {
    radio.checked = radio.value === value;
  }
};

describe("ブロック登録flow(design §2.7・§5-1承認)", () => {
  it("1対1: 相手固定で確認文言を出しPOST /v1/users/{相手}/block(パス指定・bodyなし)", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手", profile: {} },
      ],
      meId: ME,
    });
    expect(modal.hidden).toBe(false);
    expect(modal.textContent).toContain("相手"); // 対象表示
    expect(modal.textContent).toContain(BLOCK_CONFIRM_TEXT);
    await flow.submit();
    expect(client.call).toHaveBeenCalledWith("POST", `/v1/users/${PEER}/block`);
  });

  it("グループ: 対象者を選択して選択値のパスへPOST", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手A", profile: {} },
        { user_id: THIRD, display_name: "相手B", profile: {} },
      ],
      meId: ME,
    });
    selectBlockee(modal, THIRD);
    await flow.submit();
    expect(client.call).toHaveBeenCalledWith("POST", `/v1/users/${THIRD}/block`);
  });

  it("成功(201冪等)でモーダル閉鎖+トースト+onBlocked(詳細再取得)", async () => {
    const { chrome, onBlocked, modal, flow } = makeFlow();
    flow.open({
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手", profile: {} },
      ],
      meId: ME,
    });
    await flow.submit();
    expect(modal.hidden).toBe(true);
    expect(chrome.showToast).toHaveBeenCalledWith(BLOCK_TOAST_TEXT);
    expect(onBlocked).toHaveBeenCalledTimes(1);
  });

  it("グループで未選択は送信せず案内(422を出さない)", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手A", profile: {} },
        { user_id: THIRD, display_name: "相手B", profile: {} },
      ],
      meId: ME,
    });
    await flow.submit(); // 何も選んでいない
    expect(client.call).not.toHaveBeenCalled();
    expect(modal.hidden).toBe(false); // 閉じない
    expect(modal.textContent).toContain(BLOCK_SELECT_ERROR_TEXT);
  });

  it("失敗(429)はモーダルを閉じずRATE_LIMIT_TEXT", async () => {
    const { client, modal, flow } = makeFlow(() => {
      throw apiError(429, "RATE_LIMITED");
    });
    flow.open({
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手", profile: {} },
      ],
      meId: ME,
    });
    await flow.submit();
    expect(modal.hidden).toBe(false);
    expect(modal.textContent).toContain(RATE_LIMIT_TEXT);
    expect(client.call).toHaveBeenCalledTimes(1);
  });
});
```

(このファイルの冒頭で `BLOCK_ENTRY_TEXT` をimportしていないが、Task 7の追記試験(detail統合)で使うため、**Task 7でimportへ `BLOCK_ENTRY_TEXT` を1行追加する**。)

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npx vitest run tests/latch-block-flow.test.js`
Expected: 5件すべてFAIL/ERROR(blockFlow.jsが存在しない)

- [ ] **Step 3: 最小実装**

`frontend/src/latch/blockFlow.js` を作成:

```js
// ブロック登録flow(M3 ws-8 design §2.7・§5-1承認)。成立済み詳細の導線
// (通報と同型)。1対1=相手固定・グループ=対象者選択。POST /v1/users/{id}/block
// はパス指定(bodyなし・冪等201)。成功でトースト+onBlocked(詳細再取得 —
// D-23のcancelled化・チャット読取専用化は登録APIのtx内でサーバ側が自動適用
// 済みのため、再取得応答で自然に収束する)。
import {
  BLOCK_CANCEL_LABEL,
  BLOCK_CONFIRM_TEXT,
  BLOCK_ERROR_TEXT,
  BLOCK_SELECT_ERROR_TEXT,
  BLOCK_SELECT_LABEL,
  BLOCK_SUBMIT_LABEL,
  BLOCK_TOAST_TEXT,
  RATE_LIMIT_TEXT,
} from "./texts.js";
import { escapeHtml } from "./view.js";

export const createBlockFlow = ({ client, chrome, modal, onBlocked }) => {
  let fixedTargetId = null;
  let fixedName = "";
  let candidates = []; // [{id, name}] — グループ選択肢

  const showError = (text) => {
    const errorEl = modal.querySelector("[data-role=block-error]");
    errorEl.textContent = text;
    errorEl.hidden = false;
  };

  const render = () => {
    const targetHtml = fixedTargetId
      ? `<p class="block-target">ブロック対象: <strong>${escapeHtml(fixedName)}</strong></p>`
      : `<fieldset class="block-target-select"><legend>${BLOCK_SELECT_LABEL}</legend>${candidates
          .map(
            (c) =>
              `<label><input type="radio" name="blockee" value="${c.id}" /> ${escapeHtml(c.name)}</label>`,
          )
          .join("")}</fieldset>`;
    modal.innerHTML = `
      <section class="confirmation block-dialog" role="dialog" aria-modal="true" aria-labelledby="blockTitle">
        <button type="button" class="modal-close" data-role="block-close" aria-label="閉じる"><i class="ph ph-x"></i></button>
        <p class="section-kicker centered"><span></span>BLOCK</p>
        <h2 id="blockTitle">ブロック</h2>
        ${targetHtml}
        <p class="block-confirm">${BLOCK_CONFIRM_TEXT}</p>
        <p class="block-error" data-role="block-error" role="alert" hidden></p>
        <div class="block-actions">
          <button type="button" class="block-submit" data-role="block-submit">${BLOCK_SUBMIT_LABEL}</button>
          <button type="button" class="block-cancel" data-role="block-cancel">${BLOCK_CANCEL_LABEL}</button>
        </div>
      </section>
    `;
    modal.querySelector("[data-role=block-close]").addEventListener("click", close);
    modal.querySelector("[data-role=block-cancel]").addEventListener("click", close);
    modal.querySelector("[data-role=block-submit]").addEventListener("click", submit);
  };

  const open = ({ participants, meId }) => {
    fixedTargetId = null;
    fixedName = "";
    candidates = [];
    const others = (participants ?? []).filter((p) => p.user_id !== meId);
    if (others.length === 1) {
      fixedTargetId = others[0].user_id; // 1対1: 相手固定
      fixedName = others[0].display_name;
    } else {
      candidates = others.map((p) => ({ id: p.user_id, name: p.display_name }));
    }
    render();
    modal.hidden = false;
    document.body.classList.add("modal-open");
  };

  const close = () => {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  };

  const submit = async () => {
    const selected = modal.querySelector('input[name="blockee"]:checked')?.value;
    const targetId = fixedTargetId ?? selected;
    if (!targetId) {
      showError(BLOCK_SELECT_ERROR_TEXT);
      return;
    }
    try {
      await client.call("POST", `/v1/users/${targetId}/block`);
      close();
      chrome.showToast(BLOCK_TOAST_TEXT);
      await onBlocked?.(); // 詳細再取得(D-23の表示収束)
    } catch (err) {
      showError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : BLOCK_ERROR_TEXT);
    }
  };

  // backdropクリックで閉じる(reportModalと同型・一度だけwire)
  modal.addEventListener("click", (event) => {
    if (event.target === modal && !modal.hidden) close();
  });

  return { open, submit, close };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-block-flow 5+latch-blocks 7+latch-settings 7+latch-notice 21+router 9+既存=**213 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/blockFlow.js frontend/tests/latch-block-flow.test.js
git commit -m "feat: add block registration modal flow for matched detail (M3 ws-8)"
```

### Task 7: settings.js + 構成一式(index.html・main.js・styles.css・detail.js追記)

**Files:**
- Create: `frontend/src/latch/settings.js`
- Modify: `frontend/index.html`(§2の構造変更5箇所)
- Modify: `frontend/src/main.js`(notice・permission・blocks・settings・blockFlowの配線)
- Modify: `frontend/src/latch/detail.js`(ブロック導線の追記のみ)
- Modify: `frontend/styles.css`(末尾へ追記・既存クラス無変更)
- Test: `frontend/tests/latch-settings.test.js`(末尾へ3件追記)・`frontend/tests/latch-block-flow.test.js`(末尾へ1件追記+importへBLOCK_ENTRY_TEXT追加)

**Interfaces:**
- Consumes: Task 3の `createNotice`・Task 4の `createPermissionControl`・Task 5の `createBlocksSection`・Task 6の `createBlockFlow`・Task 1のsettingsルート・既存 `initChrome`(無変更)・`session.hasTokens()`
- Produces: `createSettings({ permissionControl, blocks }) -> { show(): void }`(#/settings表示時にpermissionのrender+blocksのload)

**既存170件無傷の構造保証(§9-2)**: 本Taskのうち既存試験に関わる変更は ①`index.html` — `#noticePopover` のidと `popover-kicker` は維持(latch-shell.test.jsの `id="noticePopover"` ピン・smoke.test.jsのID群はすべて維持) ②`src/main.js` — 試験から読まれない(ファイル読み取りピンはlatch-settings.test.jsの新規試験のみ) ③`src/latch/detail.js` — `createDetail` の引数へ `blockFlow = null` を追加(既存latch-detail.test.jsのmakeDetailはblockFlowを渡さないためnullで動作・導線ボタンのclickは `blockFlow?.open` で無音)。renderMatchedへ「このユーザーをブロックする」ボタンが1つ増えるが、既存11件のピンは `toContain` 系と `.attendance-field button`/`.respond-button` のスコープ限定ピンのみ(全文言一致・ボタン全数ピンなし)のため無傷

- [ ] **Step 1: 失敗するテストを書く**

**(1) `frontend/tests/latch-settings.test.js` の末尾へ追記**(import部へ `createSettings` を追加 — `import { createSettings } from "../src/latch/settings.js";`):

```js
describe("settings画面骨組(design §2.4)", () => {
  it("showはpermissionControl.renderとblocks.loadを呼ぶ", () => {
    const permissionControl = { render: vi.fn() };
    const blocks = { load: vi.fn(async () => {}) };
    const settings = createSettings({ permissionControl, blocks });
    settings.show();
    expect(permissionControl.render).toHaveBeenCalledTimes(1);
    expect(blocks.load).toHaveBeenCalledTimes(1);
  });

  it("index.html構造: settingsScreen・2セクション・モーダル2種・noticeList・notificationDot初期hidden", () => {
    const html = readFileSync(resolve(process.cwd(), "index.html"), "utf8");
    for (const id of [
      "settingsScreen", "permissionControl", "blockList", "blockMoreButton",
      "settingsButton", "noticeList", "noticeMoreButton",
      "notificationDot", "unblockModal", "blockModal",
    ]) {
      expect(html).toContain(`id="${id}"`);
    }
    expect(html).toContain('class="notification-dot" id="notificationDot" hidden');
  });

  it("main.js配線: 5モジュールのimport・screensへsettings・noticeButton追加リスナ・preload", () => {
    const mainJs = readFileSync(resolve(process.cwd(), "src/main.js"), "utf8");
    expect(mainJs).toContain('from "./latch/notice.js"');
    expect(mainJs).toContain('from "./latch/permission.js"');
    expect(mainJs).toContain('from "./latch/blocks.js"');
    expect(mainJs).toContain('from "./latch/settings.js"');
    expect(mainJs).toContain('from "./latch/blockFlow.js"');
    expect(mainJs).toContain('settings: $("#settingsScreen")');
    expect(mainJs).toContain('notice.preload()');
    expect(mainJs).toContain('if (!$("#noticePopover").hidden) notice.open()');
  });
});
```

**(2) `frontend/tests/latch-block-flow.test.js` の末尾へ追記**(import部へ `BLOCK_ENTRY_TEXT` と `createDetail` を追加):

```js
import { BLOCK_ENTRY_TEXT } from "../src/latch/texts.js"; // import部へ追記
import { createDetail } from "../src/latch/detail.js"; // import部へ追記
```

```js
describe("detail統合(design §2.7・§5-2⑤)", () => {
  it("成立済み詳細にブロック導線(クリックでblockFlow.open)・提案詳細には置かない", async () => {
    const fullProposal = {
      time_summary: "2026-10-01 20:00",
      area_name: "天文館周辺",
      headcount: 2,
      category_primary: "drinking",
      category_secondary: null,
      budget: null,
      match_level: "high",
    };
    const participants = [
      { user_id: ME, display_name: "自分", profile: {} },
      { user_id: PEER, display_name: "相手", profile: {} },
    ];
    const matchedLatch = {
      id: LATCH_ID, status: "matched",
      response_deadline: "2026-12-01T12:00:00+09:00",
      expires_at: "2026-12-04T12:00:00+09:00", created_at: "2026-10-01T09:00:00+09:00",
      completed_at: null, proposal: fullProposal, is_group: false,
      my_response: null, remaining_responses: 0,
      participants, time_summary: "2026-10-01 20:00", area_name: "天文館周辺",
    };
    const apiFor = (latch) => async (method, path) => {
      if (path === `/v1/latches/${latch.id}/messages?limit=100`) {
        return { items: [], next_cursor: null };
      }
      return { latch };
    };
    const mountDetail = (latch) => {
      const client = { call: vi.fn(apiFor(latch)) };
      const blockFlow = { open: vi.fn() };
      const root = document.createElement("section");
      const detail = createDetail({
        client,
        appState: {
          me: { id: ME },
          ensureMe: vi.fn(async () => ({ id: ME })),
        },
        chrome: { showToast: vi.fn() },
        root,
        reportFlow: { open: vi.fn() },
        blockFlow,
      });
      return { client, blockFlow, root, detail };
    };
    // 成立済み: 導線あり
    const matched = mountDetail(matchedLatch);
    await matched.detail.show(LATCH_ID);
    expect(matched.root.textContent).toContain(BLOCK_ENTRY_TEXT);
    matched.root.querySelector("[data-role=block-entry]").click();
    expect(matched.blockFlow.open).toHaveBeenCalledWith({
      participants,
      meId: ME,
    });
    // 提案詳細(proposed): 導線なし(参加者非開示)
    const proposed = mountDetail({ ...matchedLatch, status: "proposed" });
    await proposed.detail.show(LATCH_ID);
    expect(proposed.root.textContent).not.toContain(BLOCK_ENTRY_TEXT);
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npx vitest run tests/latch-settings.test.js tests/latch-block-flow.test.js`
Expected: 追加4件がFAIL(settings.jsが存在しない・index.html/main.js/detail.jsが未変更)/ 既存分はPASS

- [ ] **Step 3: 最小実装**

**(1) `frontend/src/latch/settings.js` を作成**:

```js
// 設定画面(M3 ws-8 design §2.4)。2セクション(通知許可・ブロック管理)の
// マウントと#/settings表示。規定のない要素(プロフィール編集・ログアウト・
// テーマ等)は置かない(design §1.4)。
export const createSettings = ({ permissionControl, blocks }) => ({
  show() {
    permissionControl.render();
    blocks.load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
  },
});
```

**(2) `frontend/index.html` を変更** — 5箇所(既存のidは一切消さない):

(a) notification-dot(39行目)へid+初期hidden:

```html
              <span class="notification-dot" id="notificationDot" hidden></span>
```

(置換前: `<span class="notification-dot"></span>`。CSSはdisplayを指定していないため `[hidden]` がそのまま効く)

(b) `#noticePopover` の中身(42〜45行目の `<p class="popover-kicker">…</span>` の4行)をコンテナへ差し替え:

```html
            <div class="popover notification-popover" id="noticePopover" role="status" hidden>
              <p class="popover-kicker">お知らせ</p>
              <div class="notice-list" id="noticeList"></div>
              <button class="more-button notice-more" id="noticeMoreButton" type="button" hidden>もっと見る</button>
            </div>
```

(c) accountPopoverの設定ボタン(55行目)へid:

```html
              <button type="button" id="settingsButton">設定</button>
```

(プロフィール・ログアウトはプレースホルダ温存・変更しない)

(d) `<section class="detail-view" id="detailScreen" hidden></section>`(180行目)の後にsettingsScreenを追加:

```html
        <section class="settings-view" id="settingsScreen" hidden>
          <div class="settings-inner">
            <p class="section-kicker"><span></span>SETTINGS</p>
            <h1>設定</h1>
            <section class="settings-section" aria-labelledby="notificationSettingsTitle">
              <div class="section-heading"><span aria-hidden="true"></span><h2 id="notificationSettingsTitle">通知</h2></div>
              <div id="permissionControl"></div>
            </section>
            <section class="settings-section" aria-labelledby="blockSettingsTitle">
              <div class="section-heading"><span aria-hidden="true"></span><h2 id="blockSettingsTitle">ブロック管理</h2></div>
              <div class="block-list" id="blockList"></div>
              <button class="more-button" id="blockMoreButton" type="button" hidden>もっと見る</button>
            </section>
          </div>
        </section>
```

(e) `<div class="modal-backdrop" id="reportModal" role="presentation" hidden></div>`(201行目)の後に2つのbackdropを追加:

```html
      <div class="modal-backdrop" id="unblockModal" role="presentation" hidden></div>
      <div class="modal-backdrop" id="blockModal" role="presentation" hidden></div>
```

(中身はblocks.js・blockFlow.jsのrenderが組み立てる。backdrop構造はreportModalと同型)

**(3) `frontend/src/latch/detail.js` を変更** — 3箇所(表示ロジックは無変更):

①import部のtexts.js importへ `BLOCK_ENTRY_TEXT` を追加(既存importブロック内の任意の行):

```js
import {
  ANSWERED_TEXT,
  BLOCK_ENTRY_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  HOME_LINK_TEXT,
  LATCH_NOT_FOUND_TEXT,
  NEXT_ACTION_TEXT,
  RESPONSE_BUTTONS,
} from "./texts.js";
```

②`createDetail` のシグネチャへblockFlowを追加(引数既定値付き — 既存のlatch-detail.test.jsはblockFlow未渡しでnull):

```js
export const createDetail = ({ client, appState, chrome, root, reportFlow, blockFlow = null }) => {
```

③`wireReportEntry` の定義の後に `wireBlockEntry` を追加し、renderMatchedのinnerHTMLのreport-entryボタンの次行へblock-entryボタンを置き、renderMatched末尾の `wireReportEntry(section, latch, participants);` の後に `wireBlockEntry(section, participants);` を置く:

```js
  // -- ブロック登録導線(ws-8 design §2.7・§5-1承認): 成立済み詳細のみ --
  const wireBlockEntry = (section, participants) => {
    const entry = section.querySelector("[data-role=block-entry]");
    if (!entry) return;
    entry.addEventListener("click", () => {
      blockFlow?.open({ participants, meId: appState.me?.id ?? null });
    });
  };
```

renderMatchedのinnerHTML内(report-entryボタンの直後へ1行追加):

```js
      <button type="button" class="block-entry" data-role="block-entry">${BLOCK_ENTRY_TEXT}</button>
```

renderMatched末尾(wireReportEntryの呼び出しの後へ1行追加):

```js
    wireBlockEntry(section, participants);
```

**(4) `frontend/src/main.js` を変更** — 3箇所:

①import部へ5行追加(既存importの後):

```js
import { createNotice } from "./latch/notice.js";
import { createPermissionControl } from "./latch/permission.js";
import { createBlocksSection } from "./latch/blocks.js";
import { createSettings } from "./latch/settings.js";
import { createBlockFlow } from "./latch/blockFlow.js";
```

②`initIntentScreen({ client, chrome });` の後の「ホーム・詳細・通報・ルーター」区画を次へ置き換え(blockFlowをdetailの前に生成 — `onBlocked` のarrowは呼び出し時評価のためTDZなし):

```js
// --- ホーム・詳細・通報・お知らせ・設定(ws-7 §2.1〜§2.2・ws-8 §2.1〜§2.7) --
const appState = createAppState({ client });
const home = createHome({
  client,
  el: {
    candidateList: $("#candidateList"),
    matchedList: $("#matchedList"),
    moreButton: $("#moreButton"),
    intentCount: $("#intentCountButton"),
    intentList: $("#intentList"),
  },
});
$("#moreButton").addEventListener("click", () => home.loadMore().catch(() => {}));

// お知らせ(ws-8 design §2.1〜§2.2): 起動時1回のpreloadでドット判定
const notice = createNotice({
  client,
  el: {
    list: $("#noticeList"),
    moreButton: $("#noticeMoreButton"),
    dot: $("#notificationDot"),
  },
});
if (session.hasTokens()) notice.preload().catch(() => {});
$("#noticeMoreButton").addEventListener("click", () => notice.loadMore().catch(() => {}));
// popoverを開いたことの検知は追加リスナ(chromeのリスナが先に登録済みのため
// このリスナの実行時にはhiddenは開閉後 — 開いたときだけ取得・既読化)
$("#noticeButton").addEventListener("click", () => {
  if (!$("#noticePopover").hidden) notice.open().catch(() => {});
});

const reportFlow = createReportFlow({ client, chrome, modal: $("#reportModal") });
const blockFlow = createBlockFlow({
  client,
  chrome,
  modal: $("#blockModal"),
  onBlocked: () => detail.refresh(), // ブロック成功で詳細再取得(D-23収束)
});
const detail = createDetail({
  client,
  appState,
  chrome,
  root: $("#detailScreen"),
  reportFlow,
  blockFlow,
});

// 設定画面(ws-8 design §2.4〜§2.6): 通知許可(DI)+ブロック管理
const permissionControl = createPermissionControl({
  notificationApi: typeof Notification !== "undefined" ? Notification : undefined,
  mount: $("#permissionControl"),
});
const blocks = createBlocksSection({
  client,
  el: { list: $("#blockList"), moreButton: $("#blockMoreButton") },
  modal: $("#unblockModal"),
});
$("#blockMoreButton").addEventListener("click", () => blocks.loadMore().catch(() => {}));
const settings = createSettings({ permissionControl, blocks });
$("#settingsButton").addEventListener("click", () => {
  // popoverを閉じて設定画面へ(chromeのclosePopoversと同じ操作をmain.js側で実行)
  location.hash = "#/settings";
  $("#accountPopover").hidden = true;
  $("#accountButton").setAttribute("aria-expanded", "false");
});

const router = createRouter({
  screens: {
    home: $("#homeScreen"),
    latch: $("#detailScreen"),
    settings: $("#settingsScreen"),
  },
  onRoute: (route) => {
    if (route.name === "home") {
      home.load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
      home.loadIntents().catch(() => {});
    } else if (route.name === "settings") {
      settings.show();
    } else {
      detail.show(route.id).catch(() => {});
    }
  },
});
router.start();
```

**(5) `frontend/styles.css` の末尾へ追記**(既存クラスは無変更。デザインシステムの変数と`@media`規定に乗る):

```css
/* ===== M3 ws-8 追加: お知らせ・設定・ブロック ===== */

.notification-popover {
  min-width: 320px;
  max-height: 420px;
  overflow-y: scroll; /* popoverの高さ上限・560px以下も同じ挙動 */
}
.notice-list { display: grid; gap: 10px; }
.notice-item {
  display: grid; gap: 4px; padding: 12px 14px;
  border: 1px solid var(--line); border-radius: 12px;
  background: var(--paper); text-decoration: none; color: var(--ink);
}
.notice-item:hover, .notice-item:focus-visible { border-color: var(--green); }
.notice-item.unread { border-color: var(--green); }
.notice-line { font-size: 14px; }
.notice-item.unread .notice-line:first-child { font-weight: 700; }
.notice-time { font-size: 12px; color: var(--muted); }
.notice-empty {
  display: grid; gap: 6px; padding: 20px 16px;
  border: 1px dashed var(--line); border-radius: 12px; color: var(--muted);
}
.notice-more { margin-top: 8px; }

.settings-view { max-width: 720px; margin: 0 auto; padding: 40px 24px 80px; }
.settings-inner { display: grid; gap: 24px; }
.settings-inner h1 { font-size: 24px; }
.settings-section {
  display: grid; gap: 14px; padding: 24px;
  border: 1px solid var(--line); border-radius: 14px; background: var(--paper);
}
.permission-status { font-weight: 700; }
.permission-note { font-size: 13px; color: var(--muted); }
.permission-request {
  justify-self: start; padding: 10px 24px; border-radius: 999px;
  border: none; background: var(--green); color: #fff;
  font-weight: 700; cursor: pointer;
}
.permission-request:disabled { opacity: 0.5; cursor: default; }

.block-list { display: grid; gap: 10px; }
.block-row {
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  padding: 12px 16px; border: 1px solid var(--line); border-radius: 12px;
}
.block-name { font-weight: 700; }
.block-date { font-size: 12px; color: var(--muted); margin-right: auto; }
.block-unblock {
  padding: 8px 18px; border-radius: 999px; border: 1px solid var(--line);
  background: var(--paper); font-weight: 700; cursor: pointer;
}

.block-entry {
  justify-self: start; padding: 8px 16px; font-size: 13px;
  border: none; background: none; color: var(--muted);
  text-decoration: underline; cursor: pointer;
}
.block-dialog { display: grid; gap: 16px; }
.block-target { font-size: 15px; }
.block-target-select { display: grid; gap: 10px; border: 1px solid var(--line); border-radius: 12px; padding: 14px 18px; }
.block-target-select legend { font-weight: 700; padding: 0 8px; }
.block-confirm { font-size: 15px; }
.block-actions { display: flex; gap: 10px; }
.block-submit {
  padding: 12px 24px; border-radius: 12px; border: none;
  background: var(--coral); color: #fff; font-weight: 700; cursor: pointer;
}
.block-cancel {
  padding: 12px 24px; border-radius: 12px; border: 1px solid var(--line);
  background: var(--paper); font-weight: 700; cursor: pointer;
}
.block-error { color: var(--coral); font-weight: 700; }

@media (max-width: 560px) {
  .settings-view { padding: 24px 16px 64px; }
  .notification-popover { min-width: 0; max-height: 60vh; }
}
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-settings 10=7+3・latch-block-flow 6=5+1・全体 **217 passed**)。**既存19ファイルが全てPASSしていることを特に確認する — latch-shell.test.jsの `id="noticePopover"` ピン・smoke.test.jsのID群・latch-detail.test.jsの11件がすべて緑であること**

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/settings.js frontend/index.html frontend/src/main.js frontend/src/latch/detail.js frontend/styles.css frontend/tests/latch-settings.test.js frontend/tests/latch-block-flow.test.js
git commit -m "feat: add settings screen, notice container, and block entry wiring (M3 ws-8)"
```

### Task 8: 全体検証(build・完了条件1〜5)

**Files:**(変更なし)

- [ ] **Step 1: frontend build**

Run: `cd frontend && npm run build`
Expected: exit 0(vite buildが成功・dist/生成)

- [ ] **Step 2: 完了条件1〜5を検証(§6の検証コマンドを順に実行)**

```bash
cd frontend && npm test                          # 期待: 217 passed(170+47・23ファイル)
cd frontend && npm run build                     # 期待: exit 0
ls frontend/tests/*.test.js | awk -F/ '{print $NF}' | sort | uniq -d   # 期待: 空
git diff --name-only main -- backend prototype docs   # 期待: 空(出力なし)
git diff --name-only main | sort                # 期待: §4の一覧(report込み17ファイル)
```

(作業ディレクトリがfrontendの場合は `cd ..` でリポジトリルートへ戻ってからgit系コマンドを実行すること)

### Task 9: 報告ファイル

**Files:**
- Create: `docs/plans/M3/ws-8-report.md`

- [ ] **Step 1: 報告ファイルを作成**

`docs/plans/M3/ws-8-report.md` を§7の形式どおり作成(検証コマンドの出力末尾を証拠として貼る)。

- [ ] **Step 2: コミット**

```bash
git add docs/plans/M3/ws-8-report.md
git commit -m "docs: add M3 ws-8 execution report (M3 ws-8)"
```

- [ ] **Step 3: 最終確認**

```bash
git status --short           # 期待: 空
git log --oneline main..HEAD # Task 1〜9のコミット一覧
```

---

## 9. 計画書セルフレビュー(機械チェック・スーパーバイザー指示による。2026-10-02実施)

### 9-1. frontend新規テストファイル名のbasename衝突(運用ルール5・supervisor指示)

確認コマンド(2026-10-02実施・main時点の既存19ファイル):

```bash
ls frontend/tests/*.test.js | awk -F/ '{print $NF}' | sort
# → app-state / client / conditions / expiry / format / latch-attendance /
#    latch-chat / latch-detail / latch-home / latch-report / latch-respond /
#    latch-shell / latch-view / parseFlow / router / save / session / smoke /
#    state(19ファイル・170件)
```

本計画の新規4ファイル — `latch-notice` / `latch-settings` / `latch-blocks` / `latch-block-flow`(.test.js)— は**すべて既存19ファイルと衝突しない**(機械確認済み)。frontendのvitest includeは `tests/**/*.test.js` のためフラットに追加する。既存ファイルへの追記は `tests/router.test.js` のみ(design §3が明示・describeブロック末尾への追記で既存6件は変更しない)。

### 9-2. 既存170件への影響事前特定(supervisor指示・触る全試験を挙げて影響有無を明記)

変更が既存試験に到達する経路は「既存モジュールの変更」「index.htmlの構造」「main.js」の3つのみ。ファイルごとの影響判断:

| 既存試験ファイル | 読んでいる対象 | 影響 |
|---|---|---|
| `latch-shell.test.js` | index.htmlのID群+main.js/styles.cssの文字列 | **無傷**。`id="noticePopover"` はTask 7(b)で維持・削除するのは中身のstrong/spanのみ(文言ピンなし)。main.jsのimportピン(`from "./intent/screen.js"` 等)はすべて残存・styles.cssのクラスピンは既存クラス無変更のため不変 |
| `smoke.test.js` | index.htmlのID群(tokenPanel等) | **無傷**。変更は(a)〜(e)の5箇所で既存idは一切消さない(notification-dotへidを**追加**) |
| `latch-detail.test.js` | `createDetail({client, appState, chrome, root, reportFlow})` | **無傷**。blockFlowは引数既定値 `= null` 追加のみ(未渡しでnull・`blockFlow?.open` は無音)。renderMatchedへblock-entryボタン1行が増えるが、既存11件のピンは `toContain` 系と `.attendance-field button`/`.respond-button`/`a[href="#/"]` のスコープ限定ピンのみ(ボタン全数・全文言一致ピンなし)。グループ提案の `not.toContain("通報")` ピンにも「ブロック」文言は含まれない(導線はmatchedのみのため提案姿にブロック文言は出ない) |
| `latch-view.test.js` | view.jsの既存純関数 | **無傷**。view.jsは末尾追記のみ(既存関数・importの動作不変。import部への追記は新規定数のみ) |
| `latch-report.test.js`・`latch-chat.test.js`・`latch-attendance.test.js`・`latch-respond.test.js`・`latch-home.test.js` | report/chat/attendance/respond/home.js | **無傷**。いずれも無変更ファイル(§5) |
| `router.test.js` | parseHash・createRouter | **追記のみ**(Task 1・3件)。既存6件が参照するhash(`#/`・空・`#/unknown/path`・`#/latches/…`)の解析結果は不変(`#/settings` 厳密一致の分岐追加のみ・既存describe内の既存itは1行も変更しない) |
| `app-state.test.js`・`client.test.js`・`session.test.js`・`format.test.js`・`conditions.test.js`・`expiry.test.js`・`parseFlow.test.js`・`save.test.js`・`state.test.js` | api/intent/appStateモジュール | **無傷**。すべて無変更ファイル(§5) |

### 9-3. 試験数の整合(現行基準値との突合・2026-10-02に `npx vitest run` で170 passedを確認済み)

| Task | ファイル | 追加件数 | 累積期待値 |
|---|---|---|---|
| — | (main時点) | — | 170 passed(19ファイル) |
| 1 | router.test.js追記 | 3 | 173 |
| 2 | latch-notice.test.js(純関数) | 12 | 185 |
| 3 | latch-notice.test.js追記(flow) | 9 | 194 |
| 4 | latch-settings.test.js(permission) | 7 | 201 |
| 5 | latch-blocks.test.js | 7 | 208 |
| 6 | latch-block-flow.test.js | 5 | 213 |
| 7 | latch-settings.test.js追記3+ latch-block-flow.test.js追記1 | 4 | **217 passed(23ファイル)** |

新規47件はdesign §4の目安(40〜55件)の範囲内。view.jsマトリクスの境界(終了3種・matched最小形・nearbyのstatus2種)とflowの失敗形(read失敗・取得失敗・404収束・429)を含む。

### 9-4. スペックカバレッジ(design §1.2の確定値→タスク対応)

- 引用#1(お知らせ一覧・既読管理・空状態)→ Task 2(NOTICE_EMPTY_*)+Task 3(open・markRead・空状態)
- 引用#2(未読ドット)→ Task 3(preload判定・read後再判定)+Task 7(index.htmlの初期hidden)
- 引用#3(設定=通知許可+ブロック管理)→ Task 4+Task 5+Task 7
- 引用#4(アプリ内通知の様式2形)→ Task 2(全形=見出し+サマリ+一致度文/最小形=HIDDEN+一致度文+残時間)
- 引用#5(FR-46・行からの直接回答なし)→ Task 2(attendance行は設問のみ+hrefは詳細画面)
- 引用#6(D-18・設定からいつでも取り直せる)→ Task 4(defaultでrequestPermission)+PERMISSION_NOTE_TEXT
- 引用#7(不成立一文統一)→ Task 2(終了3種=CLOSED_TEXT・importして期待値参照=完了条件6)
- 引用#8(nearbyは存在通知のみ)→ Task 2(文言1行・サマリなし)+notificationHref=null
- 引用#9(popover+未読ドット併用)→ Task 3+Task 7
- 引用#10〜#11(prototype準拠・デザインシステム)→ Task 7(styles.cssは変数のみ・既存クラス無変更・focus-visible/reduced-motionに乗る)
- 引用#12(popover設定ボタンの接続)→ Task 7(c)・main.js(settingsButton)
- 引用#13(GET notifications・POST read・共通改頁)→ Task 3(limit=20・cursor追頁)
- 引用#14(blocks一覧・DELETE)→ Task 5
- 引用#15(attendance設問)→ Task 2(ATTENDANCE_QUESTION流用)
- 引用#18(204冪等)→ Task 3(read失敗を握る根拠)
- 引用#19(blocks itemの型・404)→ Task 5(404収束)
- 引用#20(nearbyのlatch_idはcandidate=404)→ Task 2(href null)+Task 3(行はdiv)
- 引用#21(attendance_request行)→ Task 2
- 引用#22(G3・6画面)→ 本単位の完了で揃う(報告書へ記録)
- 引用#23(解除の遡及なし)→ UIで説明しない(Task 5・YAGNI)
- 引用#24(D-23)→ Task 6(onBlockedで詳細再取得・表示はCHAT_UNAVAILABLE_TEXTへ収束=ws-7実装)
- design §2.7(§5-1承認分)→ Task 6(blockFlow)+Task 7(detail.js追記・統合試験)
- 型整合点検: `createNotice({client, el:{list, moreButton, dot}})`(Task 3)はTask 7のmain.jsのel指定と一致・`createPermissionControl({notificationApi, mount})`(Task 4)はTask 7と一致・`createBlocksSection({client, el:{list, moreButton}, modal})`(Task 5)はTask 7と一致・`createBlockFlow({client, chrome, modal, onBlocked})`(Task 6)はTask 7のmain.jsと一致・`createDetail({...reportFlow, blockFlow})`(Task 7)は既存シグネチャ+既定値付き拡張で既存試験無傷・`createSettings({permissionControl, blocks})`(Task 7)はmain.jsと一致。モック応答形式は§2-1のAPI応答型どおり(notificationsは`{items, next_cursor}`・itemのlatchはnull可・204系は`null`・blocksは`{blocked_id, display_name, created_at}`フラット・201は`{blocked_id}`)。プレースホルダなし — 全コードステップに実コード記載

### 9-5. 試験コードの自己見直し記録(ws-3の教訓・supervisor指示)

計画書作成中に実施した突合(実装ステップのコードは以下の確認を通した形で確定済み):

- **ヘルパーの戻り値unpack**: `mountNotice`/`mountPermission`/`mountBlocks`/`makeFlow` はすべて `{client, el(またはmount), modal, flow系}` の分割代入で受け、各試験の参照と一致させる
- **happy-domのラジオ排他なし**: グループ選択の試験は `selectBlockee`(全解除→選択・latch-report.test.jsの `selectReason` と同型)を使う。直接 `.checked = true` は1ファイル1選択の箇所のみ
- **click→awaitのフラッシュ**: ボタンclickで起動するasyncハンドラは `await Promise.resolve()` を4〜5回(モーダルsubmit→DELETE/POST→catch/then→再描画の深さ2)。呼び出し回数ピンはフラッシュ後に検査
- **204応答のモック**: client.callは204で `null` を返すため、モックは `return null`(オブジェクトではない)
- **残時間の期待値**: 最小形試験のfixture(期限2026-10-02T12:00+09:00・now 2026-10-01T09:00+09:00)は27時間=1620分→「あと1日と3時間で締切」(既存remainingTimeTextの書式分岐と突合)
- **jstPartsのhour/minute**: `hourCycle: "h23"` の2-digitのため「09:05」とzero埋めになる(noticeTimeText期待値)
- **`at(-1)`**: Node 18+/vitest 3で可用(latch-home.test.jsの `.mock.calls.at(-1)` と同型)

**機械検証(2026-10-02・agent2実施)**: 本計画書の§8のコード一式(Task 1〜7の全テストコード・全実装コード・index.html/main.js/detail.js/styles.cssの変更)をmainのfrontendコピーへ適用し `npx vitest run` → **217 passed(23ファイル・既存170件含めて失敗ゼロ)**・`npx vite build` → **成功**を確認済み。計画書のコードはそのまま写せばこの結果を再現する

### 9-6. 適合措置と設計判断(designの意図を保った実装補完・報告書の「固定値の変更有無」へ記録)

1. **`#unblockModal` のindex.html追補**: design §3のindex.html変更一覧には `#blockModal`(§5-1用)しか列挙がないが、design §2.6が必須とするブロック解除の確認モーダル(reportModalと同型backdrop・中身はJSが組み立てる)には静的なbackdrop要素が別途必要。**`#unblockModal` を追加する**(構造はreportModalと同一・追加変更はindex.html 1行のみ)。design §2.6の「確認モーダルを1つ挟む」を実装するための必然的な追補
2. **`noticeTimeText` の書式確定**: design §2.3は行の日時を「created_atのJST書式・format.jsの流用」とのみ規定。format.jsの `formatTime` は時間帯の相対書式(「今日 20:05以降」)で通知行の時刻表示に不合致のため、**同ファイルのexport済み `jstParts` を使った「M月D日 HH:MM」(年なし・zero埋め)** をview.jsの純関数として確定した(ブロック日の表示にも流用)。書式の変更はtexts.jsではなくこの1関数に集約される
3. **notification-dotへのid付与**: design §1.3は「ドットへhidden制御を追加」とのみ規定。JSから確実に参照するため `id="notificationDot"` を付与する(classのみの参照より明示的・CSSの `.notification-dot` 規定はdisplayを指定していないため `[hidden]` 属性がそのまま効く)

design §5-1(ブロック登録導線)は2026-10-02 supervisor承認済み=採用のため、blockFlow.js・blockModal・detail.js追記は本計画に含む(design §2.7の不採用時除外規定は不使用)。design §5-2の設計判断5件(nearby文言・終了行一律CLOSED_TEXT・matchedバッジ・解除確認モーダル・提案詳細に導線なし)はすべて§2-2へ落とし込み済み。**本計画に落とし込めない未解決論点はなく、BLOCKED事項なし。**
