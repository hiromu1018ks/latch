# M3 ws-7(フロントエンド コア3画面)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M1 ws-5のfrontend資産(vanilla JS・Vite・vitest 81件)の上に、ホーム(LATCH候補・成立済みLATCHの2セクション+Active Intent一覧)・LATCH詳細(1画面3姿 = 提案/成立済み/不成立)・チャット(30秒ポーリング)・回答3択・attendance・通報のコア3画面を実装する。あわせて提案段階の1対1通報を可能にするbackend付帯変更(supervisor裁定案X: `POST /v1/reports`のreportee_id省略可+latch参加者解決・design §2.7)を本単位のブランチで実施する。**マイグレーション追加なし(head=0006不変)・依存追加なし(npm・uvともに)**。

**Architecture:** design §2 — 画面切替は**hashルーター自作**(`src/router.js`・`#/`=ホーム・`#/latches/{uuid}`=詳細・未知はホーム)。ホームは**入力画面=ホーム**(既存workspaceを1ピクセルも動かさず下段へ2セクション追加)。詳細は**1画面3姿**(statusでproposal/matched/closedに分岐・分岐入力は常にサーバのstatus)。表示変換はすべて`src/latch/view.js`の**純関数**(DOM非依存)で組み、フロントで計算しない(一致度はmatch_level・残時間はresponse_deadlineの書式変換のみ・visibility分岐はproposalのtime_summaryキー有無)。チャットは**30秒ポーリング+送信後即時再取得**(初回はlimit=100でnext_cursorが尽きるまで・上限5頁)。回答の409系はすべて「詳細再取得で現在の姿へ収束」の一本。文言は`src/latch/texts.js`へ集約(ws-8の一文統一の単一ソース)。backend案Xは`safety/routes.py`(ReportRequest.reportee_id省略可)+`safety/service.py`(省略時latch参加者から通報者以外を解決・1対1なら解決・グループは422)の2ファイル+試験3ファイル追記のみ。

**Tech Stack:** 変更なし(frontend: Vite 6.4.2 + vanilla JS + vitest 3 + happy-dom / backend: Python 3.13 / FastAPI / SQLAlchemy[asyncio]+asyncpg / pytest)。

**Spec:** `docs/plans/M3/ws-7-design.md`(agent1設計メモ。**未解決論点なし** — design §5-1の案Xは2026-10-01 supervisor裁定で確定済み)。design.mdが本計画より優先。本計画の適合措置2件(§9-6)はdesignの意図を保った実装調整で、いずれも報告書に記録する。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m3-ws-7`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に次の2つを実行する: ①backend `make setup`(`uv sync` — **依存追加なし**のためlockは進まない) ②`cd frontend && npm install`(**依存追加なし**のためpackage-lock.jsonは不変。worktreeにnode_modulesは持ち込まれないため初回必須)
- **【最重要】共有ci-dbへの `make test-ci` / `make migrate` は禁止**(STATUS運用ルール1・ws-6並走中)。開発は**backend unit試験(`make test`)+frontend(`npm test`・`npm run build`)のみ**で進める。integration試験ファイル(`tests/integration/test_safety_api.py`)は**追記するが実行しない** — インポートエラー検出のため `--collect-only` を実行してよい。報告ファイルには「**test-ci=スーパーバイザー検証待ち**」と記録する。`docker compose` 系コマンド(build/pull/up/down/migrate相当)は一切実行しない。**previewの実機スモーク(compose api + `npm run preview`による3画面確認)も実施しない** — 手順を報告書へ書くのみ(§7)
- **マイグレーション追加なし・依存追加なし・設定追加なし**: `backend/alembic/` は一切触らない(head=0006不変)・`backend/pyproject.toml`・`backend/uv.lock`・`frontend/package.json`・`frontend/package-lock.json`・`Makefile`・`.env`・`.env.example`・`settings.py`・`vite.config.mjs`・`vitest.config.mjs` に触れない
- **frontendの既存9テストファイル(81件)は1行も触らない**: `tests/client.test.js`・`tests/conditions.test.js`・`tests/expiry.test.js`・`tests/format.test.js`・`tests/parseFlow.test.js`・`tests/save.test.js`・`tests/session.test.js`・`tests/smoke.test.js`・`tests/state.test.js`。既存モジュール(`src/api/*`・`src/intent/*`の6モジュール・`src/ui/chrome.js`)も無変更(§5)により、**既存81件は構造的に無傷**。新規試験はすべて新規ファイルへ(§9-1の衝突確認済み)
- **ws-6(削除・退会)が並走中**: backend側で本単位が触るのは `safety/routes.py`・`safety/service.py`・試験3ファイル(`test_safety_service.py`・`test_safety_routes.py`・`test_safety_api.py`)の追記のみ。**ファイル新規作成はfrontend系のみ**。`tests/unit/test_rate_limit_wiring.py` は**変更しない**(ルート追加なし・ws-5で `/v1/reports` は登録済み)。マージ時の両側保持はスーパーバイザーが対処する
- **固定値の遵守**: design §2 の採用判断(案A・関数名・文言)と本計画§2のグローバル制約は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない
- **frontend試験コードを書いた後の自己見直し**(ws-3の教訓・supervisor指示): ヘルパーの戻り値unpack・引数形式・比較の型・モックの応答形式を見直す。**特にfetch/clientモックの応答形式は§2のAPI応答型と突き合わせること**(`{"items", "next_cursor"}`・`{"latch"}`・`{"message"}`・`{report_id}`・users/meはフラット)
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` 等)
- **毎コミットの検査**: backendは `make lint` と `make test` がグリーン。frontendは `cd frontend && npm test` がグリーン(両方に触るTaskでは両方)。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| ホームの必須要素5つ(Intent入力導線/Active Intent一覧/LATCH候補/成立済みLATCH/未読表示。Active Intent一覧はtopbar「Intent N件」から。未読はnotification-dot) | 03 §2・01 §10 |
| candidateの詳細(条件サマリ)は画面に出さない。ユーザーに見せるのはproposed以降 | 03 §2・01 §10 |
| 提案詳細の必須構成 — 条件サマリ(アプリ内通知と同形式・相手のraw_text・NG条件は非表示)/一致度(生スコアでなく区分)/回答3択/回答期限の残時間/グループは必要人数と現況のみ(誰が回答済みか特定しない) | 03 §5 |
| visibility=hidden_until_matchの提案は条件サマリを表示しない。「条件が合う候補があります」+一致度・回答期限(+グループなら人数)。条件サマリ・表示名・プロフィールは成立時に解放 | 03 §5・08 §2.2 |
| 一致度区分はmatch_level(high/medium/low)。「高」等の表示。生スコア値は出さない | 03 §5・05 §2 |
| 通知のタップは提案詳細画面へ遷移し、回答は3択からのみ(FR-46) | 03 §4・01 §10 |
| 成立(matched)で解放 — チャット(閉じたルーム)/相手の表示名・最小限プロフィール/集合情報/次アクション。completed後も閲覧可・新規送信は閉じる | 03 §6 |
| 不成立は2種類に集約 — 自分の操作(NO・見送り)は操作履歴、それ以外は「この提案は成立しませんでした」の一文で統一。相手の回答種別・ブロック事実は開示しない | 03 §7・D-20 |
| 通報理由は選択式4値(不適切な内容/不快な対応/なりすまし疑い/その他)。提案画面・チャット画面から常に可能。受付・記録のみ | 08 §5.2 |
| D-23: ブロックされた側には「このチャットは利用できません」のみ表示。読取専用は送信時409 CHAT_READONLYで伝わる | 08 D-23 |
| 実施自己申告(D-09): completedのみ・3日以内(超過は409 ATTENDANCE_WINDOW_CLOSED)・初回のみ(二重は409 ATTENDANCE_ALREADY_SUBMITTED)・無回答はAPIを叩かないことで表現 | 05 §5・09 D-09 |
| チャット書込はmatchedのみ(それ以外とブロック中は409 CHAT_READONLY)。閲覧はstatusを問わず参加者可。本文はtrim後1〜1000字 | 05 §5・ws-4実装 |
| 改頁共通規定: `?cursor=&limit=`(1〜100・既定20)。応答は`{"items", "next_cursor"}`。latchesは対象時刻昇順・messagesはcreated_at昇順。cursorは不透明文字列 | 05 §5 |
| 回答のエラー分岐 — 409 LATCH_EXPIRED / 409 ALREADY_ANSWERED / 409 LATCH_CLOSED / 422 VALIDATION_ERROR。クライアントはcodeで分岐 | 05 §5 |
| GET /v1/latchesは本人関与かつcandidate除外。GET /v1/latches/{id}は成立後に解放情報。閲覧・回答は参加者のみ(404/403) | 05 §5・§6 |
| フロントの実装基準は`prototype/`。食い違う場合はプロトタイプ側を正とする。デザインシステム(フォント4種・data-theme・カラー変数・Phosphor Icons・3ブレークポイント・focus-visible・reduced-motion)は固定 | 03 §10・00 運用ル則6 |
| GET /v1/intentsはstatusフィルタ可(下書き一覧とActive Intent一覧の表示に使う)。GET /v1/users/meはid・display_name・profile・birth_date・profile_complete | 05 §5 |
| G3関連受け入れ — #13ホームから回答可/#14両ユーザー回答/#15成立後解放/#17成立前後の可視範囲/#19チャット送受信/#21提案画面からの通報/#22公開設定2値の表示分岐/#23座標精度を表示しない | 02 §4 |
| 案X(2026-10-01 supervisor裁定・design §5-1): reportee_id省略可+省略時はlatch_id必須・参加者から通報者以外を解決(1対1は解決・グループ422)・既存ws-5検査は等価適用・docs改版候補は人間領域 | design §2.7・§5-1 |
| backend変更はsafetyのroutes/service+試験3ファイルのみ・マイグレーションなし(reportsテーブルは0001作成済み・列変更なし) | design §2.7・§3 |

## 2. グローバル制約(全タスクに暗黙に適用・design §2の固定値)

### 2-1. API応答の型(フロントが消費する形・実装済み。**これとモック応答を突き合わせる**)

- `GET /v1/latches?limit=20&cursor=` → `{"items": [LatchSummary], "next_cursor": string|null}`。**LatchSummary** = `{id, status, response_deadline, expires_at, created_at, completed_at, proposal, is_group, my_response, remaining_responses}`(my_responseは自分の回答のみ・remaining_responsesは人数)
- `GET /v1/latches/{id}` → `{"latch": LatchDetail}`。**LatchDetailはLatchSummary+`participants[{user_id, display_name, profile}]`・`time_summary`・`area_name`**(この3点はmatched/completedのみ非null)。**注意: 詳細応答の`completed_at`はws-1実装の都合で常にnull**(`_page_view_of`がNoneを渡すため — §9-6の適合措置①)
- **proposalは2形** — 全フィールド版(summary_only同士): `{time_summary("YYYY-MM-DD HH:MM"), area_name, headcount, category_primary, category_secondary, budget{max}|null, match_level}` / 最小版(hidden_until_matchを含む): `{headcount, match_level}`(**time_summaryキー自体が無い** — visibility分岐はこのキー有無で判定)
- `POST /v1/latches/{id}/response` body `{"response": "yes"|"no"|"defer"}` → 200 `{"latch": LatchSummary}`
- `GET /v1/latches/{id}/messages?limit=&cursor=` → `{items: [{id, latch_id, sender_id, body, created_at}], next_cursor}`(created_at**昇順**・cursorなしは最古頁・next_cursorが次の新しい頁へ)
- `POST /v1/latches/{id}/messages` body `{"body"}` → 201 `{"message": {...}}`
- `POST /v1/latches/{id}/attendance` body `{"attended": bool}` → 200 `{latch_id, actual_attended}` / 409 `ATTENDANCE_ALREADY_SUBMITTED`・`ATTENDANCE_WINDOW_CLOSED`
- `POST /v1/reports` body `{reportee_id?, latch_id?, reason}`(案X後: reportee_id省略可・両方省略は422)→ 201 `{report_id}`。reasonは `inappropriate_content` / `unpleasant_behavior` / `suspected_impersonation` / `other`
- `GET /v1/intents?status=active|draft` → `{items: [{id, status, structured_intent{category, time, location, ...}, expires_at, ...}], next_cursor}`
- `GET /v1/users/me` → `{id, display_name, profile, birth_date, profile_complete}`(**フラット・envelopeなし**)

### 2-2. 画面・文言の固定値(design §2.2〜§2.8)

- **ルートは2種**: `#/` = ホーム、`#/latches/{uuid}` = LATCH詳細。未知のhash・uuid形式不正はホームへ。ルーターはURL解析とsection切替のみ(画面の初期化・取得は画面モジュール側)
- **ホーム**: `GET /v1/latches`(limit=20)1回→クライアント側でstatus仕分け — `proposed`/`partial_accept`→候補セクション、`matched`/`completed`→成立済みセクション、`rejected`/`expired`/`cancelled`→**表示しない**(終了済みセクションは設けない)。next_cursorが残れば「もっと見る」で追頁。topbar「Intent N件」は `GET /v1/intents?status=active` の件数(active数。下書きは件数のみ添える)。カード行は `#/latches/{id}` リンク。候補の空状態は「まだ提案はありません / 条件が重なると、ここに届きます。」
- **詳細3姿**: proposed/partial_accept→提案詳細(条件サマリ[visibility分岐]・一致度・残時間・グループ人数・回答3択 or 回答済み表示)/ matched・completed→成立済み詳細(参加者・集合情報・次アクション・チャット・completedならattendance)/ rejected・expired・cancelled→不成立(my_responseがno/deferなら「辞退しました」/「今回は見送りました」、それ以外は統一文言)。404・403は「提案が見つかりません」+ホームへ戻る導線(参加者でない事実を開示しない)
- **残時間書式**: `response_deadline − 現在時刻` — 24時間超「あと{日}日と{時間}時間で締切」(時間0なら「あと{日}日で締切」)/ 1〜24時間「あと{時}時間{分}分で締切」(分0なら「あと{時}時間で締切」)/ 1時間未満「あと{分}分で締切」/ 1分未満「まもなく締切」/ 経過後「締切」。秒は表示しない。提案詳細の表示中は30秒タイマーで更新し、残時間≤0で回答ボタンをdisabled(送信可否の真実はサーバの409 LATCH_EXPIRED)
- **一致度**: `match_level` → high=「高」/ medium=「中」/ low=「低」。生スコア値はどこにも出さない
- **グループ**: `is_group && remaining_responses > 0` のとき「あと{remaining_responses}人の回答が必要」。1対1(is_group=false)では現況表示なし。誰が回答済みかの特定はしない
- **条件サマリ(全フィールド版)**: 日付・時刻(time_summary)/ area_name / {headcount}人 / category_secondary(なければcategory_primary→日本語ラベル)/ budget.maxがあるとき「ひとり{N}円まで」(toLocaleString区切り)。raw_text・NG条件・座標・距離は表示しない
- **回答3択**: [参加する]=`yes` / [今回は見送る]=`defer` / [辞退する]=`no`。POST bodyは`{"response": ...}`。pendingフラグで二重送信防止。成功(200)したら`GET /v1/latches/{id}`を1回取り直して再描画(表示の唯一の真実は詳細応答)。**409系(LATCH_EXPIRED/ALREADY_ANSWERED/LATCH_CLOSED)はすべて再取得で現在の姿へ収束**・追加文言なし。429は既存文言「操作が集中しています。少し時間をおいてもう一度お試しください。」。VALIDATION_ERRORはグローバル表示へ
- **チャット**: 30秒ポーリング+送信後即時再取得+`visibilitychange` visibleで1回。初回はlimit=100でnext_cursorが尽きるまで頁を進める(**上限5頁=500件・超過時は先頭を省略して表示**)。以降のポーリングは保持cursorから差分追記。自分/相手は `sender_id === 自分のuser_id`(meは `GET /v1/users/me` を1回・メモリのみ)。自分=右・相手=左、送信者名は詳細応答participantsから解決。送信はtextarea(maxlength=1000)+[送信]、201でローカル追記せず再取得。本文はフロントではtrimしない。`409 CHAT_READONLY`→入力disabled+「このチャットは利用できません」(理由はこれ以上出さない)。status=completed→最初から入力disabled+「対象時刻を過ぎたため、このチャットは閲覧のみできます」
- **attendance**: status=completedのとき質問「実際に会いましたか?」2択([会いました]=true / [会えていません]=false)を表示。未回答判別フィールドがないため**質問を表示してPOSTし、409 ATTENDANCE_ALREADY_SUBMITTEDで「回答済み」表示へ切替**・409 ATTENDANCE_WINDOW_CLOSEDで非表示化(**§9-6適合措置①**: 詳細応答のcompleted_atが常にnullのためdesign §2.7のローカル3日比較は実装不能・窓判定はサーバ409に一本化)
- **通報**: 提案詳細と成立済み詳細の両画面に導線。理由UI文言→API値: 不適切な内容=`inappropriate_content` / 不快な対応=`unpleasant_behavior` / なりすまし疑い=`suspected_impersonation` / その他=`other`。成立済み詳細: 1対1は相手固定、グループは対象者選択→理由選択、body `{reportee_id, latch_id, reason}`。提案詳細(proposed/partial_accept): **1対1(is_group=false)のみ**「この提案を通報する」導線、body `{latch_id, reason}`(reportee_id省略・案X)。**グループ提案は導線を置かない**(参加者非開示のため対象選択不能)。201でトースト「通報を受け付けました」
- **texts.js集約**: 「この提案は成立しませんでした」(CLOSED_TEXT)・「このチャットは利用できません」(CHAT_UNAVAILABLE_TEXT)・match_levelマップ・各種空状態。ws-8が参照する
- **XSS対策**: API応答由来の文字列(表示名・メッセージ本文・area_name・カテゴリ)をinnerHTMLへ入れる箇所はすべて `escapeHtml` を通す(view.jsの純関数)

### 2-3. backend案Xの固定値(design §2.7)

- `ReportRequest.reportee_id` を `uuid.UUID | None = None` へ(省略可)。routesのエンドポイント本体は無変更(`reportee_id=body.reportee_id` がNoneを運ぶ)
- `report_user` の検査順(単一tx): ①`_me`(未登録JWTは404) ②**reportee_idとlatch_idの両方省略は422** ③latch_id指定時: latch実在(404)→参加者取得→`me not in participants`は422→**reportee_id省略時は「通報者以外の集合」を解決(1人ならそれがreportee・2人以上は422)**・reportee_id明示時は `reportee_id not in participants` が422 ④`reportee_id == me` は422(解決経路では構造的に充足・明示経路の防御) ⑤reportee実在(404) ⑥INSERT(status='pending'固定)
- 既存ws-5検査(自分自身拒否・reportee実在・latch存在・両者参加者検査)は省略経路でも等価に適用(§9-5)
- エラーは既存のSafetyValidationError(422)・SafetyNotFoundError(404)のマッピングのまま。**新規error codeなし**・マイグレーションなし・store.py無変更

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **messages初回取得の改頁方向取り違え**(cursorなし=最古頁・next_cursorが「新しい方」への誘導 — 取得方向を新旧逆にすると会話の末尾が表示されない) → Task 8(chatの複数頁連結テスト・呼び出しURLのcursor引数ピン)
2. **hidden_until_match提案で条件サマリ・相手情報を描画してしまう**(03 §5・引用#22違反) → Task 4(最小形はconditionSummaryLinesがnull)+Task 12(詳細の最小形でHIDDEN_PROPOSAL_TEXT・サマリ行なし)
3. **終了済み(rejected/expired/cancelled)がホームに残る**(引用#9違反 — 「ホームの一覧から終了済みとして扱われる」) → Task 11(splitStatusesの非表示ピン)
4. **API応答由来文字列のXSS**(メッセージ本文・表示名は任意入力) → Task 4(escapeHtml)+Task 8(レンダリング後の本文ピン)
5. **グループ提案から通報対象を選ばせてしまう/省略bodyでグループlatchを叩いてしまう**(参加者非開示の構造違反・案Xの422と食い合わせ) → Task 10(グループ提案は導線なし)+Task 1/3(グループlatch省略bodyは422)
6. **回答の409をエラー文言で上書きして現在の姿に収束しない**(design §2.5違反) → Task 7(3codeすべて再取得呼び出しピン)
7. **残時間0で回答ボタンが押せる**(期限切れ後に409を踏む前の見た目) → Task 12(残時間≤0で3択disabled)

---

## 4. スコープ(作成・変更するファイル一覧)

作成(frontend・§9-1のbasename衝突確認済み):

```text
frontend/src/latch/texts.js            (Task 4。画面文言の定数集約)
frontend/src/latch/view.js             (Task 4。純関数 — mode判定・visibility分岐・書式)
frontend/src/router.js                 (Task 5。hash解析・hashchange切替)
frontend/src/appState.js               (Task 6。自分のuser_id保持・getUsersMe)
frontend/src/latch/respond.js          (Task 7。回答flow)
frontend/src/latch/chat.js             (Task 8。チャット)
frontend/src/latch/attendance.js       (Task 9。attendance flow)
frontend/src/latch/report.js           (Task 10。通報flow)
frontend/src/latch/home.js             (Task 11。ホーム2セクション+Intent N件)
frontend/src/latch/detail.js           (Task 12。詳細3姿)
frontend/src/intent/screen.js          (Task 13。main.jsから切り出した入力画面配線)
frontend/tests/latch-view.test.js      (Task 4。20件)
frontend/tests/router.test.js          (Task 5。6件)
frontend/tests/app-state.test.js       (Task 6。3件)
frontend/tests/latch-respond.test.js   (Task 7。8件)
frontend/tests/latch-chat.test.js      (Task 8。9件)
frontend/tests/latch-attendance.test.js(Task 9。6件)
frontend/tests/latch-report.test.js    (Task 10。7件)
frontend/tests/latch-home.test.js      (Task 11。10件)
frontend/tests/latch-detail.test.js    (Task 12。11件)
frontend/tests/latch-shell.test.js     (Task 13。6件)
docs/plans/M3/ws-7-report.md           (Task 15。報告ファイル)
```

変更(backend・案Xのみ・design §3):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/safety/service.py` | `report_user` のreportee_id引数を省略可へ+解決分岐(§2-3の検査順へ組み替え) | 1 |
| `backend/src/latch/safety/routes.py` | `ReportRequest.reportee_id` を `uuid.UUID \| None = None` へ(1行) | 2 |
| `backend/tests/unit/safety/test_safety_service.py` | 末尾へ6件追記(§8 Task 1) | 1 |
| `backend/tests/unit/safety/test_safety_routes.py` | 末尾へ2件追記(§8 Task 2) | 2 |
| `backend/tests/integration/test_safety_api.py` | 末尾へ1試験追記(作成のみ・実行はスーパーバイザー) | 3 |

変更(frontend・構造の配線。design §3):

| ファイル | 変更内容 | Task |
|---|---|---|
| `frontend/index.html` | ①wordmarkのhrefを`#/`へ ②intent-countボタンのid付与+popover化 ③mainをhomeScreen(既存workspace+2セクション)/detailScreen構造へ ④reportModal追加(§8 Task 13の完全指定) | 13 |
| `frontend/src/main.js` | 入力画面の初期化をscreen.jsへ委譲し、共通初期化(chrome・session・トークンパネル)+appState+home+detail+reportFlow+router起動へ再構成(**ロジックは動かさない・importパスの調整のみ**) | 13 |
| `frontend/styles.css` | 新画面分を末尾へ追記(カード・バッジ・詳細・チャット・attendance・通報モーダル・空状態。**既存クラス無変更**・§8 Task 13の完全指定) | 13 |

生成されるがコミットしないもの: `frontend/dist/`(build成果物)・`frontend/node_modules/`・`backend/.venv/`・`__pycache__/`。

**スコープ外と判断する基準(必要になったと感じても作らない — design §1.4)**:

- お知らせ一覧(popoverの中身)・未読ドットの接続(GET /v1/notifications)・設定画面(通知許可・ブロック管理) → ws-8。本単位は既存の空状態popoverとドット要素を温存
- 「この提案は成立しませんでした」の全画面への一文統一(T4整合) → ws-8。texts.jsの定数化まで
- Intent一覧からの個別Intent編集・下書き再開UI・pause/resume操作 → 表示のみ(05 §5)
- チャットの既読・タイピング表示・Push通知連動・WebSocket/SSE → 規定なし・ポーリングのみ
- 成立済み詳細からのブロック導線 → 08 §5.1・03 §6に規定なし・ws-8の設定が担う(design §5-2)
- PWA化・プッシュのフロント受信(FCM web) → M4以降(11)
- backendのlatches/users/intents/notifications系・`GET /v1/latches/{id}`応答のcompleted_at問題の修正 → backend変更はsafetyの2ファイルのみ(supervisor指示・§9-6①)

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- **docker/compose系の全コマンド**: `make up` / `make down` / `make test-ci` / `make migrate` / `make g1-gate` / `make g2-gate` / `make embed-smoke` / `make jev-smoke` / `make geo-*` / `docker compose *` / `docker build` / `docker pull` — §0のとおり共有ci-dbを消費してはならない。**preview実機スモークも実施しない**
- **frontendの無変更ファイル(design §3「触らないもの」)**:
  - `frontend/src/api/client.js`・`frontend/src/api/session.js`(**完全無変更** — 全API呼び出しはclient.call()を通す)
  - `frontend/src/intent/` 配下の**既存6モジュール**(`state.js`・`parseFlow.js`・`conditions.js`・`expiry.js`・`save.js`・`format.js` — 参照のみ。screen.jsは**新規ファイル**でformat.js等をimportするのは可)
  - `frontend/src/ui/chrome.js`(**完全無変更** — popovers配列への追記はmain.js側で行う)
  - `frontend/tests/` の既存9ファイル(§0・§9-2)
  - `frontend/vite.config.mjs`・`frontend/vitest.config.mjs`・`frontend/package.json`・`frontend/package-lock.json`・`frontend/public/`
  - `prototype/` 全体(実装基準の参照物)
- **backendの無変更ファイル**:
  - `backend/src/latch/safety/` のうち `store.py`・`cache.py`・`errors.py`・`__init__.py`・blocks系・D-23系
  - `backend/src/latch/` 配下の auth / users / intents / latches / notifications / geo / core / events / llm / ratelimit / worker / g1gate / g2gate 各モジュール・`main.py`
  - `backend/tests/unit/test_rate_limit_wiring.py`(**変更なし** — ルート追加なし)
  - `backend/tests/unit/safety/` のうち `test_safety_cache.py`・`test_safety_store_sql.py`・`backend/tests/integration/` の §4に列挙した以外のファイル(conftest系含む)
  - `backend/alembic/`・`compose.yaml`・`docker/`・`backend/pyproject.toml`・`backend/uv.lock`・`Makefile`・`.env`・`.env.example`・`settings.py`
- `docs/`(01〜12・learn・testassets)・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/` の既存ファイル(M0〜M3のdesign・plan・report。**ws-7-report.mdの新規作成のみ可**)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 14〜15で全て実行し報告ファイルに証拠を残す)

1. **backendの `make lint`・`make test` がグリーン**(案Xのunit追記8件込み)
   検証: `make lint && make test` — ともにexit 0。**期待件数: 1196 passed**(main 1188+本単位unit 8=service 6+routes 2。ws-6並走分は別加算)
2. **frontendの `npm test` が全緑**(既存81件+新規86件)
   検証: `cd frontend && npm test` — **期待件数: 167 passed**(81+86・§9-4の内訳)。既存9ファイルの失敗ゼロ
3. **frontendの `npm run build` が成功**
   検証: `cd frontend && npm run build` — exit 0・dist生成
4. **テストファイルbasenameがfrontend/tests・backend/testsとも既存と衝突しない**
   検証: `ls frontend/tests/*.test.js | awk -F/ '{print $NF}' | sort | uniq -d` が空・`cd backend && find tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
5. **マイグレーション無変更(alembic head=0006不変)**
   検証: `cd backend && uv run alembic heads` が `0006 (head)` 単一・`git diff --name-only main -- backend/alembic` が**空**
6. **変更ファイルが§4の一覧どおり(frontend新規21+変更3・backend変更5=計29ファイル)**
   検証: Task 15の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
7. **integration試験ファイルが収集可能(実行はスーパーバイザー検証)**
   検証: `cd backend && uv run pytest tests/integration/test_safety_api.py --collect-only -q` がexit 0で**10件**(既存9+追記1)を収集
8. **test-ci=スーパーバイザー検証待ち**(§0規律・本単位は実行しない)。スーパーバイザー検証時の期待: `docker compose build api` → `make test-ci` がグリーン・**期待件数: 1436 passed**(main 1427+unit 8+integration 1・ws-6分は別加算)・alembic head=0006・m3ws7-残存ゼロ(§7のSQL)

## 7. 報告形式

**結果ファイル**: `docs/plans/M3/ws-7-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M3 ws-7(フロントエンド コア3画面)実行報告

- ブランチ: m3-ws-7 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も・期待1195)> |
| 2 | npm test | PASS/FAIL | <出力末尾(期待167 files/167 passed・既存81件含む)> |
| 3 | npm run build | PASS/FAIL | <出力末尾> |
| 4 | テストbasename衝突なし | PASS/FAIL | <uniq -d 出力(双方とも空)> |
| 5 | alembic head=0006不変 | PASS/FAIL | <alembic heads + git diff(空)> |
| 6 | 変更ファイル=§4の29ファイル | PASS/FAIL | <git diff --name-only main出力> |
| 7 | integration収集10件 | PASS/FAIL | <pytest --collect-only -q 末尾> |
| 8 | test-ci | **スーパーバイザー検証待ち** | <実施せず(§0規律)。期待1436 passed・head=0006・m3ws7-残存ゼロ> |

## 固定値の変更有無(design.md §2・本計画§2)
- 残時間書式の4区切り+「まもなく締切」(§2-2): 変更なし / 変更あり
- チャット30秒ポーリング・5頁上限・ローカル追記なし(§2-2): 変更なし / 変更あり
- attendance表示条件=status=completedのみ・窓判定は409へ一本化(§9-6①): 変更なし / 変更あり
- 案Xの検査順(§2-3): 変更なし / 変更あり
- その他: 変更なし / 変更あり(<前→後+理由>)

## (スーパーバイザー・G3・ws-8への引継ぎ)
- 詳細応答のcompleted_atは常にnull(ws-1実装の_page_view_of)。G3/ws-9での修正候補
- texts.jsの定数群をws-8が参照(CLOSED_TEXT等・一文統一の単一ソース)
- 成立済み詳細からのブロック導線の要否はws-8設計時に判断(design §5-2)
- お知らせpopover中身・未読ドット接続・Intent個別編集はws-8

## 実機スモーク手順(スーパーバイザー検証用・agent3は実施しない)
1. mainへマージ後: `docker compose build api && make up`(api再ビルド必須・STATUS運用ル則4)
2. `cd frontend && npm install && npm run build && npm run preview`(dist配信・localhost:4173)
3. トークンパネルへIdPトークン(`cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject <subject>`)を貼る(/v1/users登録済みのsubject)
4. ①ホームにLATCH候補・成立済みが表示(ない場合空状態) ②提案詳細でvisibility両形(hiddenは条件サマリなし)・一致度・残時間・グループ人数が表示され3択で回答 ③双方yesで成立済み詳細(チャット送受信・参加者・集合情報)へ遷移 ④completedでattendance回答 ⑤成立済み詳細から通報(reportee_id明示) ⑥提案詳細(1対1)から通報(latch_idのみ) ⑦409系(期限切れ回答・二重回答・閲覧専用チャット送信)が規定表示へ収束
5. test-ci後の残存確認(§7末尾のSQL・m3ws7-)

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

**test-ci後の残存確認SQL(スーパーバイザー用・`m3ws7-`プレフィックスが残っていないこと)**:

```sql
SELECT COUNT(*) FROM users WHERE auth_subject LIKE 'm3ws7-%';
SELECT COUNT(*) FROM intents WHERE user_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws7-%');
SELECT COUNT(*) FROM latches WHERE intent_ids && ARRAY(
  SELECT id FROM intents WHERE user_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws7-%'));
SELECT COUNT(*) FROM reports WHERE reporter_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws7-%')
  OR reportee_id IN
  (SELECT id FROM users WHERE auth_subject LIKE 'm3ws7-%');
```

すべて0件であること(§9-3)。

---

## 8. 実装ステップ(TDD。Task 1〜15の順で実行する)

### Task 1: backend案X — service.py のreportee_id省略可+latch参加者解決

**Files:**
- Modify: `backend/src/latch/safety/service.py`(`report_user` のシグネチャと検査順)
- Test: `backend/tests/unit/safety/test_safety_service.py`(末尾へ6件追記)

**Interfaces:**
- Consumes: 既存の `latches_store.select_latch` / `fetch_participant_user_ids` / `safety_store.insert_report` / `SafetyValidationError` / `SafetyNotFoundError`
- Produces: `await report_user(*, auth_provider: str, auth_subject: str, reportee_id: uuid.UUID | None = None, latch_id: uuid.UUID | None = None, reason: str) -> uuid.UUID`(**reportee_idが省略可になった**。None+latch_id指定時はサーバが参加者から通報者以外を解決。Task 2のroutesが消費。**既存呼び出し(reportee_id明示)は引数名互換で無傷**)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/safety/test_safety_service.py` の末尾(ファイル最後の `test_report_inserts_pending` の後)へ追記:

```python
# -- report_user 案X: reportee_id省略+latch解決(ws-7 design §2.7) --


async def test_report_resolve_one_on_one_latch(monkeypatch):
    """reportee_id省略+1対1latch: 通報者以外の1人を解決して挿入(§2.7)。

    解決経路では自分自身がreporteeにならない(othersはmeを除く集合)。
    """
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
    monkeypatch.setattr(latches_store, "select_latch", _ret(latch))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))

    async def _participants(conn, intent_ids):
        return [ME, TARGET]  # 1対1: 通報者+相手

    monkeypatch.setattr(latches_store, "fetch_participant_user_ids", _participants)
    monkeypatch.setattr(safety_store, "insert_report", _insert_report)
    await _svc().report_user(
        auth_provider="google",
        auth_subject="s",
        latch_id=latch.id,
        reason="unpleasant_behavior",
    )
    assert inserts == [
        {
            "reporter": ME,
            "reportee": TARGET,  # 解決先は通報者以外の1人(自分ではない)
            "latch_id": latch.id,
            "reason": "unpleasant_behavior",
            "status": "pending",
            "now": NOW,
        }
    ]


async def test_report_resolve_group_latch_422(monkeypatch):
    """グループlatch(通報者以外が2人以上)は対象特定不能で422(§2.7条件2)。"""
    latch = _latch_row([uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(latches_store, "select_latch", _ret(latch))

    async def _participants(conn, intent_ids):
        return [ME, TARGET, uuid.uuid4()]  # 通報者以外が2人

    monkeypatch.setattr(latches_store, "fetch_participant_user_ids", _participants)
    with pytest.raises(SafetyValidationError):
        await _svc().report_user(
            auth_provider="google",
            auth_subject="s",
            latch_id=latch.id,
            reason="other",
        )


async def test_report_both_omitted_422(monkeypatch):
    """reportee_id・latch_idの両方省略は422(§2.7条件1)。"""
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    with pytest.raises(SafetyValidationError):
        await _svc().report_user(
            auth_provider="google", auth_subject="s", reason="other"
        )


async def test_report_resolve_latch_missing_404(monkeypatch):
    """省略経路でもlatch不在は404(既存検査の等価適用・§2.7条件3)。"""
    from latch.safety.errors import SafetyNotFoundError

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(latches_store, "select_latch", _ret(None))
    with pytest.raises(SafetyNotFoundError):
        await _svc().report_user(
            auth_provider="google",
            auth_subject="s",
            latch_id=uuid.uuid4(),
            reason="other",
        )


async def test_report_resolve_reporter_not_participant_422(monkeypatch):
    """省略経路で自分が非参加のlatchは422(§2.7条件3)。"""
    latch = _latch_row([uuid.uuid4(), uuid.uuid4()])
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(latches_store, "select_latch", _ret(latch))
    monkeypatch.setattr(
        latches_store, "fetch_participant_user_ids", _ret([TARGET, uuid.uuid4()])
    )
    with pytest.raises(SafetyValidationError):
        await _svc().report_user(
            auth_provider="google",
            auth_subject="s",
            latch_id=latch.id,
            reason="other",
        )


async def test_report_explicit_path_unchanged(monkeypatch):
    """明示経路(reportee_id指定)は現行どおり201相当で挿入(§2.7条件3)。

    reporteeが非参加のlatchへの明示指定は引き続き422。
    """
    latch = _latch_row([uuid.uuid4(), uuid.uuid4()])
    inserts: list[dict] = []

    async def _insert_report(
        conn, *, reporter, reportee, latch_id, reason, status, now
    ):
        inserts.append({"reporter": reporter, "reportee": reportee})
        return uuid.uuid4()

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(latches_store, "select_latch", _ret(latch))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))

    async def _participants(conn, intent_ids):
        return [ME, TARGET]

    monkeypatch.setattr(latches_store, "fetch_participant_user_ids", _participants)
    monkeypatch.setattr(safety_store, "insert_report", _insert_report)
    # 正常: reportee明示+参加 → 挿入
    await _svc().report_user(
        auth_provider="google",
        auth_subject="s",
        reportee_id=TARGET,
        latch_id=latch.id,
        reason="other",
    )
    assert inserts == [{"reporter": ME, "reportee": TARGET}]
    # reportee非参加のlatch → 422(現行挙動)
    monkeypatch.setattr(
        latches_store, "fetch_participant_user_ids", _ret([ME, uuid.uuid4()])
    )
    with pytest.raises(SafetyValidationError):
        await _svc().report_user(
            auth_provider="google",
            auth_subject="s",
            reportee_id=TARGET,
            latch_id=latch.id,
            reason="other",
        )
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_service.py -v`
Expected: 追加6件がすべて ERROR/FAIL(現行の `report_user` は `reportee_id` が必須キーワード引数のため、省略呼び出しは `TypeError: ... missing 1 required keyword-only argument: 'reportee_id'` になる)/ 既存16件はPASS

- [ ] **Step 3: 最小実装**

`backend/src/latch/safety/service.py` の `report_user` メソッド全体を、次のとおり**置き換える**(メソッドのdocstring・検査順・引数既定値が変わる。クラス内の他メソッド・`make_safety_service` は無変更):

```python
    async def report_user(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        reportee_id: uuid.UUID | None = None,
        latch_id: uuid.UUID | None = None,
        reason: str,
    ) -> uuid.UUID:
        """POST /v1/reports(design §2.5・ws-7案X§2.7)。受付・記録のみ。

        reportee_id省略時はlatch_id必須で、当該latchの参加者から通報者
        以外を解決する(1人ならその者がreportee・2人以上は422)。既存の
        ws-5検査(自分自身拒否・reportee実在・latch存在・両者参加者)は
        省略経路でも等価に適用する。
        """
        try:
            async with self._engine.begin() as conn:
                me = await self._me(conn, auth_provider, auth_subject)
                if reportee_id is None and latch_id is None:
                    raise SafetyValidationError(
                        "reportee_id or latch_id required"
                    )
                if latch_id is not None:
                    row = await latches_store.select_latch(conn, latch_id)
                    if row is None:
                        raise SafetyNotFoundError("latch not found")
                    participants = await latches_store.fetch_participant_user_ids(
                        conn, row.intent_ids
                    )
                    if me not in participants:
                        raise SafetyValidationError("not latch participants")
                    if reportee_id is None:
                        others = [u for u in participants if u != me]
                        if len(others) != 1:
                            raise SafetyValidationError(
                                "cannot resolve reportee from latch"
                            )
                        reportee_id = others[0]
                    elif reportee_id not in participants:
                        raise SafetyValidationError("not latch participants")
                if reportee_id == me:
                    raise SafetyValidationError("cannot report yourself")
                if not await store.user_exists(conn, reportee_id):
                    raise SafetyNotFoundError("user not found")
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

(置き換え前との差分: ①`reportee_id: uuid.UUID` → `uuid.UUID | None = None` ②両方省略検査を先頭へ追加 ③latch検査ブロックを「me参加検査→(省略時)解決/(明示時)reportee参加検査」の順へ組替え ④「自分自身422→reportee実在404」をlatch検査の後へ。**既存unit試験(test_report_self_422_and_reportee_missing_404・test_report_latch_participation・test_report_inserts_pending)はこの検査順でも同じ結果になる** — reportee_id=ME+latch_id=Noneはlatchブロックスキップ後 `reportee_id == me` で422、reportee不在+latch_id=Noneはuser_exists Falseで404)

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/safety -v`
Expected: PASS(cache 7+store_sql 8+service **22**=既存16+追記6+routes 7=44件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/safety/service.py backend/tests/unit/safety/test_safety_service.py
git commit -m "feat: allow reportee_id omission with latch participant resolution in report_user (M3 ws-7)"
```

### Task 2: backend案X — routes.py のReportRequest省略可

**Files:**
- Modify: `backend/src/latch/safety/routes.py`(`ReportRequest.reportee_id` の1行)
- Test: `backend/tests/unit/safety/test_safety_routes.py`(末尾へ2件追記)

**Interfaces:**
- Consumes: Task 1の `report_user(reportee_id=None可)`
- Produces: HTTP契約 — `POST /v1/reports` のbody `{latch_id, reason}`(reportee_idなし)が201。両方省略 `{reason}` は422 VALIDATION_ERROR envelope(service検査)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/safety/test_safety_routes.py` の末尾へ追記(import部に `from latch.safety.errors import SafetyValidationError` を1行追加すること):

```python
async def test_report_without_reportee_id_201():
    """reportee_id省略bodyは201・serviceへreportee_id=Noneが渡る(案X)。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/reports",
            json={"latch_id": str(uuid.uuid4()), "reason": "other"},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"report_id": REPORT_ID}
    assert stub.calls[0][1]["reportee_id"] is None  # 省略がそのまま伝播
    assert stub.calls[0][1]["latch_id"] is not None


async def test_report_both_omitted_422():
    """reportee_id・latch_idとも省略は422 VALIDATION_ERROR(§2.7条件1)。

    検査はserviceが担うため、ここではenvelope変換までをピンする
    (ServiceValidationError→422の契約)。
    """

    class _RaiseService(StubService):
        async def report_user(self, **kwargs):
            raise SafetyValidationError("reportee_id or latch_id required")

    async with AsyncClient(
        transport=ASGITransport(app=_app(_RaiseService())), base_url="http://test"
    ) as client:
        resp = await client.post("/v1/reports", json={"reason": "other"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/safety/test_safety_routes.py -v`
Expected: 追加2件がFAIL(1件目: 現行の必須`reportee_id`によりpydanticが422を返すため201にならない)/ 既存7件はPASS

- [ ] **Step 3: 最小実装**

`backend/src/latch/safety/routes.py` の `ReportRequest` を次へ置き換え(**1行変更+docstring**。他は無変更):

```python
class ReportRequest(BaseModel):
    """POST /v1/reports のbody(design §2.5・ws-7案X§2.7)。

    reportee_idは省略可(latch_idからサーバが通報者以外を解決 — 1対1
    解決・グループは422)。latch_idも省略可だが両方の省略はserviceが
    422へ。提案段階(1対1)の通報が省略形。
    """

    reportee_id: uuid.UUID | None = None
    latch_id: uuid.UUID | None = None
    reason: ReportReason
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/safety -v && make lint`
Expected: PASS(service 22+routes 9+cache 7+store_sql 8=46件)・lintグリーン

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/safety/routes.py backend/tests/unit/safety/test_safety_routes.py
git commit -m "feat: make reports reportee_id optional in request schema (M3 ws-7)"
```

### Task 3: backend案X — integration試験追記(作成のみ・実行はスーパーバイザー)

**Files:**
- Modify: `backend/tests/integration/test_safety_api.py`(末尾へ1試験追記)

**重要**: 本Taskの試験は**実行しない**(§0)。`--collect-only` で10件が収集できることのみ検証する。**追記後に自分で見直す**(ヘルパーの戻り値unpack・引数形式・比較の型 — ws-3の教訓)。

- [ ] **Step 1: 試験を追記**

まずファイル冒頭のプレフィックス定数を変更する(§9-3の対抗策 — 追記試験を含む全行の残存確認が§7のSQL〔`m3ws7-`〕と1:1になる):

```python
SUBJECT_PREFIX = "m3ws7-"  # 変更前: "m3ws5-"(docstringの m3ws5- 記載も併せて直す)
```

次に `backend/tests/integration/test_safety_api.py` の末尾( `test_9_reports_latch_participation` の後)へ追記:

```python
# -- 試験10: 案X reportee_id省略+解決(ws-7 design §6-5) --


async def test_10_reports_reportee_resolution(api_client, db_engine, field):
    """案X: 省略+1対1latchで相手に解決201・グループ422・両方省略422・
    非参加422・latch不在404・明示経路無傷(ws-7 design §2.7)。"""
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    i1 = await _intent(api_client, h1["headers"])
    i2 = await _intent(api_client, h2["headers"])
    i3 = await _intent(api_client, h3["headers"])
    pair = await _latch(db_engine, [i1["id"], i2["id"]])
    group = await _latch(
        db_engine, [i1["id"], i2["id"], i3["id"]], gid=str(uuid_mod.uuid4())
    )
    # 省略+1対1 → 201・reportee_idはh2へ解決される
    r1 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"latch_id": pair, "reason": "other"},
    )
    assert r1.status_code == 201, r1.text
    async with db_engine.connect() as conn:
        reportee = (
            await conn.execute(
                text(
                    "SELECT reportee_id FROM reports WHERE id = CAST(:r AS uuid)"
                ),
                {"r": r1.json()["report_id"]},
            )
        ).scalar_one()
    assert str(reportee) == h2["id"]  # 通報者(h1)以外の1人
    # 省略+グループlatch → 422(対象特定不能)
    r2 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"latch_id": group, "reason": "other"},
    )
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "VALIDATION_ERROR"
    # 両方省略 → 422
    r3 = await api_client.post(
        "/v1/reports", headers=h1["headers"], json={"reason": "other"}
    )
    assert r3.status_code == 422
    # 省略+自分非参加のlatch → 422
    other = await _latch(db_engine, [i2["id"], i3["id"]])
    r4 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"latch_id": other, "reason": "other"},
    )
    assert r4.status_code == 422
    # 省略+latch不在 → 404
    r5 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"latch_id": str(uuid_mod.uuid4()), "reason": "other"},
    )
    assert r5.status_code == 404
    # 明示経路(reportee_id指定)は現行どおり201
    r6 = await api_client.post(
        "/v1/reports",
        headers=h1["headers"],
        json={"reportee_id": h2["id"], "latch_id": pair, "reason": "other"},
    )
    assert r6.status_code == 201, r6.text
```

- [ ] **Step 2: 収集検証(実行しない・§0)**

Run: `cd backend && uv run pytest tests/integration/test_safety_api.py --collect-only -q`
Expected: exit 0・**10 collected**(`test_1` 〜 `test_10`)

- [ ] **Step 3: 書いたコードの自己見直し(ws-3教訓・collect-onlyしか実行できないため)**

次を確認して壊れていれば直す(直した場合は報告書の補足へ記録):
- `_user` はdict(`["id"]` はstr・`["headers"]` はdict)・`_intent` はdict(`["id"]` はstr)・`_latch` はstr(そのままJSON bodyへ入れて可)
- `_latch` のgid引数はstr(`uuid_mod.UUID(gid)`へ変換されるため `str(uuid_mod.uuid4())` を渡す)
- reports.reportee_idはUUID(asyncpg)→ `str(reportee)` で文字列比較
- 既存9試験は無変更(追記のみ)

- [ ] **Step 4: lint・unit再確認してコミット**

Run: `make lint && make test`
Expected: グリーン(unit 1195件相当・integrationはdeselected)

```bash
git add backend/tests/integration/test_safety_api.py
git commit -m "test: add reportee resolution integration case (supervisor-run) (M3 ws-7)"
```

### Task 4: frontend — texts.js(文言集約)+ view.js(純関数)

**Files:**
- Create: `frontend/src/latch/texts.js`
- Create: `frontend/src/latch/view.js`
- Test: `frontend/tests/latch-view.test.js`(新規・20件)

**Interfaces:**
- Consumes: 既存 `src/intent/format.js` の `formatCategory({primary, secondary})`(カテゴリ→日本語ラベル・参照のみ)
- Produces(Task 5〜13が消費):
  - texts.js: `CLOSED_TEXT`・`CHAT_UNAVAILABLE_TEXT`・`CHAT_COMPLETED_TEXT`・`HIDDEN_PROPOSAL_TEXT`・`NO_LATCH_TITLE`・`NO_LATCH_NOTE`・`NO_MATCHED_TITLE`・`NO_MATCHED_NOTE`・`MATCH_LEVEL_TEXT`(map)・`STATUS_TEXT`(map)・`RESPONSE_BUTTONS`(`[["yes","参加する"],["defer","今回は見送る"],["no","辞退する"]]`)・`ANSWERED_TEXT`(map)・`REPORT_REASON_OPTIONS`・`RATE_LIMIT_TEXT`・`LATCH_NOT_FOUND_TEXT`・`HOME_LINK_TEXT`・`DEADLINE_CLOSED_TEXT`・`DEADLINE_SOON_TEXT`・`NEXT_ACTION_TEXT`・`REPORT_TOAST_TEXT`・`ATTENDANCE_QUESTION`・`ATTENDANCE_DONE_TEXT`・`ATTENDANCE_ALREADY_TEXT`・`ATTENDANCE_ERROR_TEXT`
  - view.js: `latchMode(latch) -> "proposal"|"matched"|"closed"`・`isMinimalProposal(latch) -> bool`・`matchLevelText(latch) -> string`・`remainingMs(deadlineIso, nowIso) -> number`・`remainingTimeText(deadlineIso, nowIso) -> string`・`groupNeedText(latch) -> string|null`・`conditionSummaryLines(latch) -> string[]|null`・`closedText(latch) -> string`・`messageSide(message, meId) -> "mine"|"theirs"`・`senderName(senderId, participants) -> string`・`escapeHtml(value) -> string`

- [ ] **Step 1: 失敗するテストを書く**

`frontend/src/latch/` ディレクトリを作成し、`frontend/tests/latch-view.test.js` を新規作成:

```js
import { describe, expect, it } from "vitest";
import {
  ANSWERED_TEXT,
  CHAT_UNAVAILABLE_TEXT,
  CLOSED_TEXT,
  RATE_LIMIT_TEXT,
  REPORT_REASON_OPTIONS,
  RESPONSE_BUTTONS,
} from "../src/latch/texts.js";
import {
  closedText,
  conditionSummaryLines,
  escapeHtml,
  groupNeedText,
  isMinimalProposal,
  latchMode,
  matchLevelText,
  messageSide,
  remainingMs,
  remainingTimeText,
  senderName,
} from "../src/latch/view.js";

// API応答のfixture(§2-1のLatchDetail型・proposalは全フィールド版)
const fullProposal = {
  time_summary: "2026-10-01 20:00",
  area_name: "天文館周辺",
  headcount: 2,
  category_primary: "drinking",
  category_secondary: "軽く飲めるお店",
  budget: { max: 5000 },
  match_level: "high",
};
const minimalProposal = { headcount: 2, match_level: "low" }; // hidden_until_match形
const base = {
  id: "11111111-1111-4111-8111-111111111111",
  status: "proposed",
  response_deadline: "2026-10-01T12:00:00+09:00",
  expires_at: "2026-10-04T12:00:00+09:00",
  created_at: "2026-10-01T09:00:00+09:00",
  completed_at: null,
  proposal: fullProposal,
  is_group: false,
  my_response: null,
  remaining_responses: 1,
};
const latch = (overrides = {}, proposal = fullProposal) => ({
  ...base,
  proposal,
  ...overrides,
});
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const participants = [
  { user_id: ME, display_name: "自分", profile: {} },
  { user_id: PEER, display_name: "相手", profile: {} },
];

describe("latchMode(status→3姿・design §2.3)", () => {
  it("proposed/partial_accept→proposal・matched/completed→matched・終了3種→closed", () => {
    expect(latchMode(latch({ status: "proposed" }))).toBe("proposal");
    expect(latchMode(latch({ status: "partial_accept" }))).toBe("proposal");
    expect(latchMode(latch({ status: "matched" }))).toBe("matched");
    expect(latchMode(latch({ status: "completed" }))).toBe("matched");
    expect(latchMode(latch({ status: "rejected" }))).toBe("closed");
    expect(latchMode(latch({ status: "expired" }))).toBe("closed");
    expect(latchMode(latch({ status: "cancelled" }))).toBe("closed");
  });
});

describe("visibility分岐(design §2.4・引用#5)", () => {
  it("proposalのtime_summaryキー有無で最小形を判定する", () => {
    expect(isMinimalProposal(latch({}, minimalProposal))).toBe(true);
    expect(isMinimalProposal(latch({}, fullProposal))).toBe(false);
  });

  it("全フィールド版の条件サマリ行(日時/場所/人数/カテゴリ/予算)", () => {
    expect(conditionSummaryLines(latch())).toEqual([
      "2026-10-01 20:00",
      "天文館周辺",
      "2人",
      "軽く飲めるお店", // category_secondary優先
      "ひとり5,000円まで",
    ]);
  });

  it("budget nullは予算行なし・secondary nullはprimaryの日本語ラベル", () => {
    expect(
      conditionSummaryLines(
        latch({}, {
          ...fullProposal,
          category_secondary: null,
          budget: null,
        }),
      ),
    ).toEqual(["2026-10-01 20:00", "天文館周辺", "2人", "飲み"]);
  });

  it("最小形はnull(条件サマリを組まない)", () => {
    expect(conditionSummaryLines(latch({}, minimalProposal))).toBeNull();
  });
});

describe("一致度(design §2.4・生スコアは出さない)", () => {
  it("match_level 3値の文言", () => {
    expect(matchLevelText(latch({}, { ...minimalProposal, match_level: "high" }))).toBe("高");
    expect(matchLevelText(latch({}, { ...minimalProposal, match_level: "medium" }))).toBe("中");
    expect(matchLevelText(latch({}, minimalProposal))).toBe("低");
  });
});

describe("残時間書式(design §2.4・deadline=2026-10-01T12:00+09:00基準)", () => {
  it("24時間超は「あとN日とM時間で締切」", () => {
    expect(remainingTimeText(base.response_deadline, "2026-09-28T09:00:00+09:00"))
      .toBe("あと3日と3時間で締切");
  });

  it("24時間超・時間0は「あとN日で締切」", () => {
    expect(remainingTimeText(base.response_deadline, "2026-09-29T12:00:00+09:00"))
      .toBe("あと2日で締切");
  });

  it("1〜24時間は「あとN時間M分で締切」(designの例と同形)", () => {
    expect(remainingTimeText(base.response_deadline, "2026-10-01T10:37:00+09:00"))
      .toBe("あと1時間23分で締切");
  });

  it("1〜24時間・分0は「あとN時間で締切」", () => {
    expect(remainingTimeText(base.response_deadline, "2026-10-01T10:00:00+09:00"))
      .toBe("あと2時間で締切");
  });

  it("1時間未満は「あとN分で締切」", () => {
    expect(remainingTimeText(base.response_deadline, "2026-10-01T11:20:00+09:00"))
      .toBe("あと40分で締切");
  });

  it("1分未満は「まもなく締切」(秒は表示しない)", () => {
    expect(remainingTimeText(base.response_deadline, "2026-10-01T11:59:30+09:00"))
      .toBe("まもなく締切");
  });

  it("経過後・ちょうど0は「締切」・remainingMsは負", () => {
    expect(remainingTimeText(base.response_deadline, "2026-10-01T13:00:00+09:00"))
      .toBe("締切");
    expect(remainingTimeText(base.response_deadline, "2026-10-01T12:00:00+09:00"))
      .toBe("締切");
    expect(remainingMs(base.response_deadline, "2026-10-01T13:00:00+09:00"))
      .toBeLessThanOrEqual(0);
  });
});

describe("グループ必要人数(design §2.4・引用#4)", () => {
  it("is_group+残回答>0のときのみ「あとN人の回答が必要」", () => {
    expect(groupNeedText(latch({ is_group: true, remaining_responses: 2 })))
      .toBe("あと2人の回答が必要");
    expect(groupNeedText(latch({ is_group: false, remaining_responses: 1 })))
      .toBeNull(); // 1対1は現況表示なし
    expect(groupNeedText(latch({ is_group: true, remaining_responses: 0 })))
      .toBeNull(); // 成立済み
  });
});

describe("不成立の二値化(design §2.3・引用#9)", () => {
  it("my_response=no/deferは自身の操作履歴", () => {
    expect(closedText(latch({ status: "cancelled", my_response: "no" })))
      .toBe("辞退しました");
    expect(closedText(latch({ status: "rejected", my_response: "defer" })))
      .toBe("今回は見送りました");
  });

  it("それ以外(相手の回答・期限切れ・ブロック等)は統一文言", () => {
    expect(closedText(latch({ status: "cancelled", my_response: null })))
      .toBe(CLOSED_TEXT);
    expect(closedText(latch({ status: "expired", my_response: "yes" })))
      .toBe(CLOSED_TEXT); // 自分は参加しても不成立は統一文言
  });
});

describe("メッセージの左右判定と送信者名(design §2.6)", () => {
  it("sender_id===meはmine・それ以外はtheirs・名前はparticipantsから解決", () => {
    const mine = { sender_id: ME, body: "こんにちは" };
    const theirs = { sender_id: PEER, body: "はじめまして" };
    expect(messageSide(mine, ME)).toBe("mine");
    expect(messageSide(theirs, ME)).toBe("theirs");
    expect(senderName(PEER, participants)).toBe("相手");
    expect(senderName("44444444-4444-4444-8444-444444444444", participants))
      .toBe(""); // 不在(グループ外・防御)
  });
});

describe("escapeHtml(XSS対策・API応答由来文字列)", () => {
  it("HTML特殊文字をエスケープする", () => {
    expect(escapeHtml('<script>alert("x")</script>'))
      .toBe("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;");
    expect(escapeHtml("a&b<c>d'e")).toBe("a&amp;b&lt;c&gt;d&#39;e");
  });
});

describe("texts.jsの定数ピン(design §2.8・ws-8参照)", () => {
  it("統一文言・D-23文言・429文言", () => {
    expect(CLOSED_TEXT).toBe("この提案は成立しませんでした");
    expect(CHAT_UNAVAILABLE_TEXT).toBe("このチャットは利用できません");
    expect(RATE_LIMIT_TEXT)
      .toBe("操作が集中しています。少し時間をおいてもう一度お試しください。");
  });

  it("回答3択と回答済み文言・通報理由4値コード", () => {
    expect(RESPONSE_BUTTONS).toEqual([
      ["yes", "参加する"],
      ["defer", "今回は見送る"],
      ["no", "辞退する"],
    ]);
    expect(ANSWERED_TEXT).toEqual({
      yes: "参加します",
      defer: "今回は見送りました",
      no: "辞退しました",
    });
    expect(REPORT_REASON_OPTIONS).toEqual([
      ["inappropriate_content", "不適切な内容"],
      ["unpleasant_behavior", "不快な対応"],
      ["suspected_impersonation", "なりすまし疑い"],
      ["other", "その他"],
    ]);
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npm test`
Expected: 新規 `tests/latch-view.test.js` の20件がすべて FAIL/ERROR(モジュールが存在しない)/ 既存9ファイル81件はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/latch/texts.js` を作成:

```js
// 画面文言の定数集約(M3 ws-7 design §2.8)。
// 「この提案は成立しませんでした」等の一文統一はws-8がこの定数を
// 参照して実現する(単一ソース)。文言変更はこの1ファイルに集約される。

export const CLOSED_TEXT = "この提案は成立しませんでした";
export const CHAT_UNAVAILABLE_TEXT = "このチャットは利用できません"; // D-23
export const CHAT_COMPLETED_TEXT =
  "対象時刻を過ぎたため、このチャットは閲覧のみできます";
export const HIDDEN_PROPOSAL_TEXT = "条件が合う候補があります"; // 03 §4の様式
export const NO_LATCH_TITLE = "まだ提案はありません";
export const NO_LATCH_NOTE = "条件が重なると、ここに届きます。";
export const NO_MATCHED_TITLE = "成立したLATCHはまだありません";
export const NO_MATCHED_NOTE = "成立すると、ここに並びます。";
export const LATCH_NOT_FOUND_TEXT = "提案が見つかりません"; // 404/403同一扱い
export const HOME_LINK_TEXT = "ホームへ戻る";
export const NEXT_ACTION_TEXT = "チャットで挨拶を交わしましょう";
export const DEADLINE_CLOSED_TEXT = "締切";
export const DEADLINE_SOON_TEXT = "まもなく締切";
export const RATE_LIMIT_TEXT =
  "操作が集中しています。少し時間をおいてもう一度お試しください。";
export const REPORT_TOAST_TEXT = "通報を受け付けました";
export const ATTENDANCE_QUESTION = "実際に会いましたか?"; // D-09
export const ATTENDANCE_DONE_TEXT = "回答を送りました";
export const ATTENDANCE_ALREADY_TEXT = "回答済みです";
export const ATTENDANCE_ERROR_TEXT = "送信できませんでした。";

// 一致度区分(03 §5「高」等の表示・生スコアは出さない)
export const MATCH_LEVEL_TEXT = { high: "高", medium: "中", low: "低" };

// status→バッジ文言(candidateは一覧に出ないため含めない)
export const STATUS_TEXT = {
  proposed: "回答まち",
  partial_accept: "回答まち",
  matched: "成立済み",
  completed: "完了",
};

// 回答操作3択(03 §5のUI文言→API値・design §2.5)
export const RESPONSE_BUTTONS = [
  ["yes", "参加する"],
  ["defer", "今回は見送る"],
  ["no", "辞退する"],
];

// 回答済み表示(design §2.4)
export const ANSWERED_TEXT = {
  yes: "参加します",
  defer: "今回は見送りました",
  no: "辞退しました",
};

// 通報理由の選択式4値(08 §5.2・API値はws-5実装の英語コード)
export const REPORT_REASON_OPTIONS = [
  ["inappropriate_content", "不適切な内容"],
  ["unpleasant_behavior", "不快な対応"],
  ["suspected_impersonation", "なりすまし疑い"],
  ["other", "その他"],
];
```

`frontend/src/latch/view.js` を作成:

```js
// API応答→表示への純関数群(M3 ws-7 design §2.3〜§2.4)。DOM非依存。
// フロントで新たな計算をしない — 一致度はmatch_level・残時間は
// response_deadlineの書式変換のみ・visibility分岐はproposalの
// time_summaryキー有無(生成側の2形と1:1)。
import { formatCategory } from "../intent/format.js";
import {
  ANSWERED_TEXT,
  CLOSED_TEXT,
  DEADLINE_CLOSED_TEXT,
  DEADLINE_SOON_TEXT,
  MATCH_LEVEL_TEXT,
} from "./texts.js";

const PROPOSAL_STATUSES = new Set(["proposed", "partial_accept"]);
const MATCHED_STATUSES = new Set(["matched", "completed"]);

export const latchMode = (latch) =>
  PROPOSAL_STATUSES.has(latch.status)
    ? "proposal"
    : MATCHED_STATUSES.has(latch.status)
      ? "matched"
      : "closed";

export const isMinimalProposal = (latch) =>
  !("time_summary" in latch.proposal); // hidden_until_matchを含む形

export const matchLevelText = (latch) =>
  MATCH_LEVEL_TEXT[latch.proposal.match_level] ?? "";

export const remainingMs = (deadlineIso, nowIso) =>
  new Date(deadlineIso).getTime() - new Date(nowIso).getTime();

export const remainingTimeText = (deadlineIso, nowIso) => {
  const ms = remainingMs(deadlineIso, nowIso);
  if (ms <= 0) return DEADLINE_CLOSED_TEXT;
  const totalMin = Math.floor(ms / 60000); // 秒は表示しない
  if (totalMin < 1) return DEADLINE_SOON_TEXT;
  if (totalMin < 60) return `あと${totalMin}分で締切`;
  if (totalMin < 60 * 24) {
    const hours = Math.floor(totalMin / 60);
    const minutes = totalMin % 60;
    return minutes === 0
      ? `あと${hours}時間で締切`
      : `あと${hours}時間${minutes}分で締切`;
  }
  const days = Math.floor(totalMin / (60 * 24));
  const hours = Math.floor((totalMin % (60 * 24)) / 60);
  return hours === 0
    ? `あと${days}日で締切`
    : `あと${days}日と${hours}時間で締切`;
};

export const groupNeedText = (latch) =>
  latch.is_group && latch.remaining_responses > 0
    ? `あと${latch.remaining_responses}人の回答が必要`
    : null;

// 条件サマリ(全フィールド版のみ・design §2.4末尾の書式)。
// raw_text・NG条件・座標・距離は組まない(引用#20の#22・#23)。
export const conditionSummaryLines = (latch) => {
  if (isMinimalProposal(latch)) return null;
  const p = latch.proposal;
  const lines = [
    p.time_summary,
    p.area_name,
    `${p.headcount}人`,
    formatCategory({
      primary: p.category_primary,
      secondary: p.category_secondary,
    }),
  ];
  if (p.budget && p.budget.max != null) {
    lines.push(`ひとり${Number(p.budget.max).toLocaleString("ja-JP")}円まで`);
  }
  return lines;
};

// 不成立の二値化(03 §7): 自分の操作履歴か統一文言か
export const closedText = (latch) =>
  latch.my_response === "no"
    ? ANSWERED_TEXT.no
    : latch.my_response === "defer"
      ? ANSWERED_TEXT.defer
      : CLOSED_TEXT;

export const messageSide = (message, meId) =>
  message.sender_id === meId ? "mine" : "theirs";

export const senderName = (senderId, participants) =>
  participants.find((p) => p.user_id === senderId)?.display_name ?? "";

// API応答由来の文字列をinnerHTMLへ入れる前のエスケープ(XSS対策)
export const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-view 20件+既存81件=**101 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/texts.js frontend/src/latch/view.js frontend/tests/latch-view.test.js
git commit -m "feat: add latch display text constants and pure view functions (M3 ws-7)"
```

### Task 5: frontend — router.js(hashルーター自作・design §2.1案A)

**Files:**
- Create: `frontend/src/router.js`
- Test: `frontend/tests/router.test.js`(新規・6件)

**Interfaces:**
- Produces: `parseHash(hash) -> {name: "home"|"latch", id: string|null}`(未知hash・uuid形式不正はhome)・`createRouter({screens: {home: HTMLElement, latch: HTMLElement}, onRoute: (route) => void}) -> {start(), stop()}`(startはhashchange登録+即時適用。sectionのhidden切替はrouterが担い、データ取得はonRoute側 — Task 13のmain.jsが消費)

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/router.test.js` を新規作成:

```js
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRouter, parseHash } from "../src/router.js";

const UUID = "11111111-1111-4111-8111-111111111111";

const mountScreens = () => ({
  home: document.createElement("div"),
  latch: document.createElement("div"),
});

afterEach(() => {
  window.location.hash = "";
});

describe("parseHash(design §2.1)", () => {
  it("#/ と空と未知のhashはホームへ", () => {
    expect(parseHash("#/")).toEqual({ name: "home", id: null });
    expect(parseHash("")).toEqual({ name: "home", id: null });
    expect(parseHash("#/unknown/path")).toEqual({ name: "home", id: null });
    expect(parseHash("#/latches/")).toEqual({ name: "home", id: null });
  });

  it("#/latches/{uuid} を解析する", () => {
    expect(parseHash(`#/latches/${UUID}`)).toEqual({
      name: "latch",
      id: UUID,
    });
  });

  it("uuid形式でなければホームへ戻す", () => {
    expect(parseHash("#/latches/not-a-uuid").name).toBe("home");
    expect(parseHash("#/latches/11111111-1111-1111-1111-111111111111").name)
      .toBe("home"); // 4が無くuuid形式として不正
  });
});

describe("createRouter(hashchangeで画面section切替)", () => {
  it("startは現在hashで解決しsectionのhiddenを切替+onRouteを呼ぶ", () => {
    const screens = mountScreens();
    screens.home.hidden = true; // 初期状態を裏返しておく
    screens.latch.hidden = false;
    const onRoute = vi.fn();
    const router = createRouter({ screens, onRoute });
    window.location.hash = "#/";
    router.start();
    expect(screens.home.hidden).toBe(false);
    expect(screens.latch.hidden).toBe(true);
    expect(onRoute).toHaveBeenCalledWith({ name: "home", id: null });
    router.stop();
  });

  it("hashchangeでlatch画面へ切替(戻るも可)", () => {
    const screens = mountScreens();
    const onRoute = vi.fn();
    const router = createRouter({ screens, onRoute });
    window.location.hash = "#/";
    router.start();
    window.location.hash = `#/latches/${UUID}`;
    window.dispatchEvent(new Event("hashchange"));
    expect(screens.home.hidden).toBe(true);
    expect(screens.latch.hidden).toBe(false);
    expect(onRoute).toHaveBeenLastCalledWith({ name: "latch", id: UUID });
    window.location.hash = "#/";
    window.dispatchEvent(new Event("hashchange"));
    expect(screens.home.hidden).toBe(false);
    router.stop();
  });

  it("stopでhashchangeをlistenしない", () => {
    const screens = mountScreens();
    const onRoute = vi.fn();
    const router = createRouter({ screens, onRoute });
    window.location.hash = "#/";
    router.start();
    router.stop();
    onRoute.mockClear();
    window.location.hash = `#/latches/${UUID}`;
    window.dispatchEvent(new Event("hashchange"));
    expect(onRoute).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npm test`
Expected: `tests/router.test.js` の6件がFAIL/ERROR(import error)/ 他はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/router.js` を作成:

```js
// hashルーター(M3 ws-7 design §2.1案A)。`#/`=ホーム・`#/latches/{uuid}`=
// LATCH詳細・未知のhashはホームへ戻す。ルーターはURL解析と画面sectionの
// 切替のみを担い、各画面の初期化・データ取得はonRoute側(単体試験可能な境界)。
const LATCH_HASH = /^#\/latches\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$/;

export const parseHash = (hash) => {
  const match = LATCH_HASH.exec(hash ?? "");
  if (match) return { name: "latch", id: match[1] };
  return { name: "home", id: null };
};

export const createRouter = ({ screens, onRoute }) => {
  const apply = () => {
    const route = parseHash(window.location.hash);
    for (const [name, el] of Object.entries(screens)) {
      el.hidden = route.name !== name;
    }
    onRoute?.(route);
  };
  return {
    start() {
      window.addEventListener("hashchange", apply);
      apply();
    },
    stop() {
      window.removeEventListener("hashchange", apply);
    },
  };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(router 6+latch-view 20+既存81=**107 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/router.js frontend/tests/router.test.js
git commit -m "feat: add hash router with home/latch screen switching (M3 ws-7)"
```

### Task 6: frontend — appState.js(自分のuser_id保持)

**Files:**
- Create: `frontend/src/appState.js`
- Test: `frontend/tests/app-state.test.js`(新規・3件)

**Interfaces:**
- Produces: `createAppState({ client }) -> { get me(), ensureMe(): Promise<{id, display_name, profile}>, reset() }`(`GET /v1/users/me` を1回だけ呼びメモリ保持・再ログインでreset。Task 12のdetail.jsが消費)

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/app-state.test.js` を新規作成:

```js
import { describe, expect, it, vi } from "vitest";
import { createAppState } from "../src/appState.js";

// GET /v1/users/me の応答(§2-1: フラット・envelopeなし)
const meResponse = {
  id: "22222222-2222-4222-8222-222222222222",
  display_name: "たろう",
  profile: { bio: "よろしく" },
  birth_date: "1990-04-01",
  profile_complete: true,
};

describe("createAppState(design §2.6)", () => {
  it("ensureMeはGET /v1/users/meを1回だけ呼びメモリ保持する", async () => {
    const client = { call: vi.fn(async () => meResponse) };
    const state = createAppState({ client });
    const first = await state.ensureMe();
    const second = await state.ensureMe();
    expect(client.call).toHaveBeenCalledTimes(1);
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/users/me");
    expect(first).toEqual({
      id: meResponse.id,
      display_name: "たろう",
      profile: { bio: "よろしく" },
    });
    expect(second).toBe(first); // 同一オブジェクト(キャッシュ)
    expect(state.me.id).toBe(meResponse.id);
  });

  it("初期状態のmeはnull", () => {
    const state = createAppState({ client: { call: vi.fn() } });
    expect(state.me).toBeNull();
  });

  it("resetすると次回で再取得する(再ログイン)", async () => {
    const client = { call: vi.fn(async () => meResponse) };
    const state = createAppState({ client });
    await state.ensureMe();
    state.reset();
    expect(state.me).toBeNull();
    await state.ensureMe();
    expect(client.call).toHaveBeenCalledTimes(2);
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npm test`
Expected: `tests/app-state.test.js` の3件がFAIL/ERROR / 他はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/appState.js` を作成:

```js
// 画面横断状態(M3 ws-7 design §3)。自分のuser_idはGET /v1/users/meで
// セッション確立後に1回だけ取得しメモリ保持する(再ログインで再取得)。

export const createAppState = ({ client }) => {
  let me = null;
  return {
    get me() {
      return me;
    },
    async ensureMe() {
      if (me) return me;
      const data = await client.call("GET", "/v1/users/me");
      me = { id: data.id, display_name: data.display_name, profile: data.profile };
      return me;
    },
    reset() {
      me = null;
    },
  };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(app-state 3+router 6+latch-view 20+既存81=**110 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/appState.js frontend/tests/app-state.test.js
git commit -m "feat: add app state holding current user id (M3 ws-7)"
```

### Task 7: frontend — respond.js(回答flow・design §2.5)

**Files:**
- Create: `frontend/src/latch/respond.js`
- Test: `frontend/tests/latch-respond.test.js`(新規・8件)

**Interfaces:**
- Consumes: Task 4の `RATE_LIMIT_TEXT`・`src/api/session.js` の `ApiError`(テストのみ)
- Produces: `createRespondFlow({ client, latchId, refresh, showError }) -> { submit(value: "yes"|"defer"|"no", buttons: HTMLButtonElement[]) }`(POST `/v1/latches/{id}/response` body `{"response": value}`・pending制御・成功と409系はrefresh()で詳細再取得・429と未知はshowErrorへ。Task 12のdetail.jsが消費)

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-respond.test.js` を新規作成:

```js
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/session.js";
import { RATE_LIMIT_TEXT } from "../src/latch/texts.js";
import { createRespondFlow } from "../src/latch/respond.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";

const apiError = (status, code) => new ApiError(status, code, "msg");

const makeFlow = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const refresh = vi.fn(async () => {});
  const showError = vi.fn();
  const flow = createRespondFlow({ client, latchId: LATCH_ID, refresh, showError });
  const buttons = ["yes", "defer", "no"].map((value) => {
    const button = document.createElement("button");
    button.dataset.value = value;
    return button;
  });
  return { client, refresh, showError, flow, buttons };
};

describe("回答flow(design §2.5)", () => {
  it("3択のUI値→API値をbodyへ送る(yes/defer/no)", async () => {
    const { client, flow, buttons } = makeFlow(async () => ({ latch: {} }));
    await flow.submit("yes", buttons);
    await flow.submit("defer", buttons);
    await flow.submit("no", buttons);
    expect(client.mock.calls[0]).toEqual([
      "POST",
      `/v1/latches/${LATCH_ID}/response`,
      { body: { response: "yes" } },
    ]);
    expect(client.mock.calls[1][2]).toEqual({ body: { response: "defer" } });
    expect(client.mock.calls[2][2]).toEqual({ body: { response: "no" } });
  });

  it("pending中の二重送信を防ぐ(ボタンはdisabled)", async () => {
    let resolveCall;
    const client = {
      call: vi.fn(
        () => new Promise((resolve) => { resolveCall = resolve; }),
      ),
    };
    const flow = createRespondFlow({
      client, latchId: LATCH_ID, refresh: vi.fn(async () => {}), showError: vi.fn(),
    });
    const buttons = [document.createElement("button")];
    const first = flow.submit("yes", buttons);
    expect(buttons[0].disabled).toBe(true); // 送信中はdisabled
    await flow.submit("yes", buttons); // pending中の2回目は無視
    expect(client.call).toHaveBeenCalledTimes(1);
    resolveCall({});
    await first;
  });

  it("成功(200)時は詳細を取り直す(refresh)", async () => {
    const { flow, refresh, showError, buttons } = makeFlow(async () => ({ latch: {} }));
    await flow.submit("yes", buttons);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(showError).not.toHaveBeenCalled();
  });

  it("409 LATCH_EXPIREDは再取得で収束(文言を重ねない)", async () => {
    const { flow, refresh, showError, buttons } = makeFlow(() => {
      throw apiError(409, "LATCH_EXPIRED");
    });
    await flow.submit("yes", buttons);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(showError).not.toHaveBeenCalled();
  });

  it("409 ALREADY_ANSWEREDは再取得で収束", async () => {
    const { flow, refresh, buttons } = makeFlow(() => {
      throw apiError(409, "ALREADY_ANSWERED");
    });
    await flow.submit("yes", buttons);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("409 LATCH_CLOSEDは再取得で収束", async () => {
    const { flow, refresh, buttons } = makeFlow(() => {
      throw apiError(409, "LATCH_CLOSED");
    });
    await flow.submit("yes", buttons);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("429 RATE_LIMITEDは既存文言を表示", async () => {
    const { flow, refresh, showError, buttons } = makeFlow(() => {
      throw apiError(429, "RATE_LIMITED");
    });
    await flow.submit("yes", buttons);
    expect(showError).toHaveBeenCalledWith(RATE_LIMIT_TEXT);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("未知のエラーはメッセージを表示(VALIDATION_ERROR相当の既定表示)", async () => {
    const { flow, showError, buttons } = makeFlow(() => {
      throw apiError(422, "VALIDATION_ERROR");
    });
    await flow.submit("yes", buttons);
    expect(showError).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npm test`
Expected: `tests/latch-respond.test.js` の8件がFAIL/ERROR / 他はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/latch/respond.js` を作成:

```js
// 回答flow(M3 ws-7 design §2.5)。3択→POST response・pending制御・
// 成功も409系も「詳細再取得で現在の姿へ収束」の一本(表示の唯一の真実は
// 詳細応答)。追加の文言を重ねない。
import { RATE_LIMIT_TEXT } from "./texts.js";

export const createRespondFlow = ({ client, latchId, refresh, showError }) => {
  let pending = false;
  return {
    submit: async (value, buttons = []) => {
      if (pending) return;
      pending = true;
      for (const button of buttons) button.disabled = true;
      try {
        await client.call("POST", `/v1/latches/${latchId}/response`, {
          body: { response: value },
        });
        await refresh();
      } catch (err) {
        // 409 LATCH_EXPIRED / ALREADY_ANSWERED / LATCH_CLOSED はすべて
        // 再取得の結果(不成立姿・回答済み表示)をそのまま表示する
        if (err?.status === 409) {
          await refresh();
          return;
        }
        if (err?.code === "RATE_LIMITED") {
          showError(RATE_LIMIT_TEXT);
          return;
        }
        showError(err?.message ?? "通信エラーが発生しました。");
      } finally {
        pending = false;
        for (const button of buttons) button.disabled = false;
      }
    },
  };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-respond 8+app-state 3+router 6+latch-view 20+既存81=**118 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/respond.js frontend/tests/latch-respond.test.js
git commit -m "feat: add response flow with pending guard and 409 refetch (M3 ws-7)"
```

### Task 8: frontend — chat.js(チャット・design §2.6)

**Files:**
- Create: `frontend/src/latch/chat.js`
- Test: `frontend/tests/latch-chat.test.js`(新規・9件)

**Interfaces:**
- Consumes: Task 4の `CHAT_COMPLETED_TEXT`・`CHAT_UNAVAILABLE_TEXT`・`escapeHtml`・`senderName`
- Produces: `createChat({ client, latch, meId, mount }) -> { start(): Promise, stop(), send(): Promise }`。latchはLatchDetail・mountは描画先HTMLElement。30秒ポーリング+`visibilitychange`・初回はlimit=100でnext_cursorが尽きるまで(上限5頁=500件・超過時は先頭省略)・送信はPOST後に再取得(ローカル追記なし)・`409 CHAT_READONLY`で入力disabled+D-23文言・status!==matchedは最初から読取専用。Task 12のdetail.jsが消費

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-chat.test.js` を新規作成:

```js
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/session.js";
import {
  CHAT_COMPLETED_TEXT,
  CHAT_UNAVAILABLE_TEXT,
} from "../src/latch/texts.js";
import { createChat } from "../src/latch/chat.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";

const matchedLatch = {
  id: LATCH_ID, status: "matched", proposal: {}, is_group: false,
  my_response: null, remaining_responses: 0,
  participants: [
    { user_id: ME, display_name: "自分", profile: {} },
    { user_id: PEER, display_name: "相手", profile: {} },
  ],
};
const message = (id, senderId, body) => ({
  id, latch_id: LATCH_ID, sender_id: senderId, body,
  created_at: "2026-10-01T20:00:00+09:00",
});

const mountChat = (latch = matchedLatch, callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const mount = document.createElement("div");
  const chat = createChat({ client, latch, meId: ME, mount });
  return { client, mount, chat };
};

afterEach(() => {
  vi.useRealTimers();
});

describe("chat(デザイン§2.6)", () => {
  it("初回取得はnext_cursorが尽きるまで頁を進め昇順連結する", async () => {
    const pages = [
      { items: [message("m1", PEER, "1通目")], next_cursor: "c1" },
      { items: [message("m2", ME, "2通目"), message("m3", PEER, "3通目")], next_cursor: null },
    ];
    const { client, mount, chat } = mountChat(
      matchedLatch,
      async (method, path) => pages.shift(),
    );
    await chat.start();
    expect(client.mock.calls[0]).toEqual([
      "GET",
      `/v1/latches/${LATCH_ID}/messages?limit=100`,
    ]);
    expect(client.mock.calls[1]).toEqual([
      "GET",
      `/v1/latches/${LATCH_ID}/messages?limit=100&cursor=${encodeURIComponent("c1")}`,
    ]);
    const bodies = [...mount.querySelectorAll(".chat-body")].map((el) => el.textContent);
    expect(bodies).toEqual(["1通目", "2通目", "3通目"]); // 昇順
    chat.stop();
  });

  it("初回取得は5頁で打ち切り(6頁目を要求しない)", async () => {
    let calls = 0;
    const { client, chat } = mountChat(matchedLatch, async () => {
      calls += 1;
      return { items: [message(`m${calls}`, PEER, `msg${calls}`)], next_cursor: `c${calls}` };
    });
    await chat.start();
    expect(client.call).toHaveBeenCalledTimes(5); // 上限5頁
    chat.stop();
  });

  it("30秒間隔でポーリングしstopで停止する", async () => {
    vi.useFakeTimers();
    let calls = 0;
    const { client, chat } = mountChat(matchedLatch, async () => {
      calls += 1;
      return { items: [], next_cursor: null };
    });
    const started = chat.start();
    await vi.advanceTimersByTimeAsync(0); // start内の初回syncを完了させる
    await started;
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(calls).toBe(3);
    chat.stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toBe(3); // stop後は増えない
  });

  it("送信はPOST後に再取得しローカル追記しない", async () => {
    let page = { items: [message("m1", PEER, "既存")], next_cursor: null };
    const posts = [];
    const { client, mount, chat } = mountChat(matchedLatch, async (method, path) => {
      if (method === "POST") {
        posts.push(path);
        return { message: message("m2", ME, "新着") };
      }
      page = { items: [...page.items, message("m2", ME, "新着")], next_cursor: null };
      return page;
    });
    await chat.start();
    mount.querySelector(".chat-input").value = "新着";
    await chat.send();
    expect(posts).toEqual([`/v1/latches/${LATCH_ID}/messages`]);
    const bodies = [...mount.querySelectorAll(".chat-body")].map((el) => el.textContent);
    expect(bodies).toEqual(["既存", "新着"]); // サーバ再取得の順序どおり
    chat.stop();
  });

  it("409 CHAT_READONLYで入力disabled+「このチャットは利用できません」", async () => {
    const { mount, chat } = mountChat(matchedLatch, async (method) => {
      if (method === "POST") throw new ApiError(409, "CHAT_READONLY", "readonly");
      return { items: [], next_cursor: null };
    });
    await chat.start();
    await chat.send();
    const input = mount.querySelector(".chat-input");
    const notice = mount.querySelector(".chat-notice");
    expect(input.disabled).toBe(true);
    expect(notice.textContent).toBe(CHAT_UNAVAILABLE_TEXT);
    expect(notice.hidden).toBe(false);
    chat.stop();
  });

  it("status=completedは最初から読取専用(閲覧のみ文言)", async () => {
    const completed = { ...matchedLatch, status: "completed" };
    const { mount, chat } = mountChat(completed, async () => ({
      items: [], next_cursor: null,
    }));
    await chat.start();
    expect(mount.querySelector(".chat-input").disabled).toBe(true);
    expect(mount.querySelector(".chat-notice").textContent).toBe(CHAT_COMPLETED_TEXT);
    chat.stop();
  });

  it("自分=右・相手=左・相手の送信者名はparticipantsから解決する", async () => {
    const { mount, chat } = mountChat(matchedLatch, async () => ({
      items: [
        message("m1", ME, "自分の発言"),
        message("m2", PEER, "相手の発言"),
      ],
      next_cursor: null,
    }));
    await chat.start();
    const rows = [...mount.querySelectorAll(".chat-message")];
    expect(rows[0].className).toContain("chat-mine");
    expect(rows[1].className).toContain("chat-theirs");
    expect(rows[1].querySelector(".chat-sender").textContent).toBe("相手");
    expect(rows[0].querySelector(".chat-sender")).toBeNull(); // 自分は名前なし
    chat.stop();
  });

  it("visibilitychangeのvisibleで1回取り直す", async () => {
    let calls = 0;
    const { client, chat } = mountChat(matchedLatch, async () => {
      calls += 1;
      return { items: [], next_cursor: null };
    });
    await chat.start();
    expect(calls).toBe(1);
    Object.defineProperty(document, "visibilityState", {
      configurable: true, value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    await Promise.resolve();
    await Promise.resolve();
    expect(calls).toBe(2);
    Object.defineProperty(document, "visibilityState", {
      configurable: true, value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    await Promise.resolve();
    await Promise.resolve();
    expect(calls).toBe(2); // hiddenでは再取得しない
    chat.stop(); // listener掃除(以降のテストへ影響させない)
  });

  it("メッセージ本文はHTMLとして解釈されない(escapeHtml)", async () => {
    const { mount, chat } = mountChat(matchedLatch, async () => ({
      items: [message("m1", PEER, '<img src=x onerror="alert(1)">')],
      next_cursor: null,
    }));
    await chat.start();
    expect(mount.querySelector(".chat-body").innerHTML)
      .not.toContain("<img");
    expect(mount.querySelector(".chat-body").textContent)
      .toBe('<img src=x onerror="alert(1)">');
    chat.stop();
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npm test`
Expected: `tests/latch-chat.test.js` の9件がFAIL/ERROR / 他はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/latch/chat.js` を作成:

```js
// チャット(M3 ws-7 design §2.6)。30秒ポーリング+送信後即時再取得+
// visibilitychange。初回はlimit=100でnext_cursorが尽きるまで(上限5頁=500件・
// 超過時は先頭を省略)。順序の真実はサーバ(ローカル追記しない)。
import { CHAT_COMPLETED_TEXT, CHAT_UNAVAILABLE_TEXT } from "./texts.js";
import { escapeHtml, senderName } from "./view.js";

const POLL_MS = 30_000;
const PAGE_LIMIT = 100;
const MAX_PAGES = 5;

export const createChat = ({ client, latch, meId, mount }) => {
  let messages = [];
  let cursor = null;
  let timer = null;
  // matchedのみ書込可(それ以外は閲覧のみ・03 §6)
  const readonlyReason =
    latch.status === "matched" ? null : CHAT_COMPLETED_TEXT;

  const listEl = document.createElement("div");
  listEl.className = "chat-messages";
  listEl.setAttribute("aria-live", "polite");
  const noticeEl = document.createElement("p");
  noticeEl.className = "chat-notice";
  noticeEl.hidden = true;
  const formEl = document.createElement("form");
  formEl.className = "chat-form";
  const inputEl = document.createElement("textarea");
  inputEl.className = "chat-input";
  inputEl.maxLength = 1000; // trim後1〜1000字の上限(空白のみはサーバ422)
  inputEl.rows = 2;
  inputEl.setAttribute("aria-label", "メッセージ");
  const sendButton = document.createElement("button");
  sendButton.type = "submit";
  sendButton.className = "chat-send";
  sendButton.textContent = "送信";
  formEl.append(inputEl, sendButton);
  mount.replaceChildren(listEl, noticeEl, formEl);

  const setReadonly = (text) => {
    inputEl.disabled = true;
    sendButton.disabled = true;
    noticeEl.textContent = text;
    noticeEl.hidden = false;
  };

  const render = () => {
    listEl.replaceChildren(
      ...messages.map((m) => {
        const row = document.createElement("div");
        const mine = m.sender_id === meId;
        row.className = `chat-message ${mine ? "chat-mine" : "chat-theirs"}`;
        // 自分=右(名前なし)・相手=左(送信者名は詳細応答participantsから)
        const name = mine
          ? ""
          : `<span class="chat-sender">${escapeHtml(
              senderName(m.sender_id, latch.participants ?? []),
            )}</span>`;
        row.innerHTML = `${name}<span class="chat-body">${escapeHtml(m.body)}</span>`;
        return row;
      }),
    );
    listEl.scrollTop = listEl.scrollHeight;
  };

  const sync = async () => {
    let pages = 0;
    // cursorなし=最古頁・next_cursorは「より新しい頁」への不透明文字列。
    // 尽きるまで進めて会話の末尾(最新)まで取得する(design §2.6)
    do {
      const query = cursor
        ? `?limit=${PAGE_LIMIT}&cursor=${encodeURIComponent(cursor)}`
        : `?limit=${PAGE_LIMIT}`;
      const data = await client.call(
        "GET",
        `/v1/latches/${latch.id}/messages${query}`,
      );
      messages = messages.concat(data.items);
      cursor = data.next_cursor;
      pages += 1;
    } while (cursor && pages < MAX_PAGES);
    if (messages.length > MAX_PAGES * PAGE_LIMIT) {
      messages = messages.slice(messages.length - MAX_PAGES * PAGE_LIMIT);
    }
    render();
  };

  const send = async () => {
    if (inputEl.disabled) return;
    try {
      await client.call("POST", `/v1/latches/${latch.id}/messages`, {
        body: { body: inputEl.value }, // フロントではtrimしない(§2.6)
      });
      inputEl.value = "";
      await sync(); // 201でローカルに追記せず再取得
    } catch (err) {
      if (err?.code === "CHAT_READONLY") {
        // D-23: 理由はこれ以上表示しない(ブロック事実の推察を避ける)
        setReadonly(CHAT_UNAVAILABLE_TEXT);
        return;
      }
      noticeEl.textContent =
        err?.code === "VALIDATION_ERROR"
          ? "メッセージを入力してください。" // 空白のみ等(§2.6)
          : "送信できませんでした。";
      noticeEl.hidden = false;
    }
  };

  formEl.addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });

  const onVisible = () => {
    if (document.visibilityState === "visible") sync().catch(() => {});
  };

  const start = async () => {
    await sync();
    if (readonlyReason) setReadonly(readonlyReason);
    timer = setInterval(() => sync().catch(() => {}), POLL_MS);
    document.addEventListener("visibilitychange", onVisible);
  };

  const stop = () => {
    if (timer) clearInterval(timer);
    timer = null;
    document.removeEventListener("visibilitychange", onVisible);
  };

  return { start, stop, send };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-chat 9+latch-respond 8+app-state 3+router 6+latch-view 20+既存81=**127 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/chat.js frontend/tests/latch-chat.test.js
git commit -m "feat: add chat with paged initial load and 30s polling (M3 ws-7)"
```

### Task 9: frontend — attendance.js(実施自己申告・design §2.7)

**Files:**
- Create: `frontend/src/latch/attendance.js`
- Test: `frontend/tests/latch-attendance.test.js`(新規・6件)

**Interfaces:**
- Consumes: Task 4の `ATTENDANCE_*`・`RATE_LIMIT_TEXT`
- Produces: `createAttendanceFlow({ client, latchId, mount }) -> { renderQuestion(), submit(attended: boolean): Promise }`(POST `/v1/latches/{id}/attendance` body `{"attended": bool}`・200で完了表示・409 ALREADY_SUBMITTEDで回答済み表示・409 WINDOW_CLOSEDで非表示化。Task 12のdetail.jsが消費)

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-attendance.test.js` を新規作成:

```js
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/session.js";
import {
  ATTENDANCE_ALREADY_TEXT,
  ATTENDANCE_DONE_TEXT,
  ATTENDANCE_QUESTION,
} from "../src/latch/texts.js";
import { createAttendanceFlow } from "../src/latch/attendance.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const apiError = (status, code) => new ApiError(status, code, "msg");

const mountFlow = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const mount = document.createElement("div");
  const flow = createAttendanceFlow({ client, latchId: LATCH_ID, mount });
  return { client, mount, flow };
};

describe("attendance(D-09・design §2.7)", () => {
  it("質問と2択([会いました]/[会えていません])を描画する", () => {
    const { mount, flow } = mountFlow(async () => ({}));
    flow.renderQuestion();
    expect(mount.textContent).toContain(ATTENDANCE_QUESTION);
    const labels = [...mount.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["会いました", "会えていません"]);
  });

  it("[会いました]は{attended:true}を送る(ボタンclick経由)", async () => {
    const { client, mount, flow } = mountFlow(async () => ({
      latch_id: LATCH_ID, actual_attended: true,
    }));
    flow.renderQuestion();
    mount.querySelectorAll("button")[0].click(); // 会いました
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(client.call).toHaveBeenCalledWith(
      "POST",
      `/v1/latches/${LATCH_ID}/attendance`,
      { body: { attended: true } },
    );
  });

  it("[会えていません]は{attended:false}を送る", async () => {
    const client = { call: vi.fn(async () => ({ latch_id: LATCH_ID, actual_attended: false })) };
    const mount = document.createElement("div");
    const flow = createAttendanceFlow({ client, latchId: LATCH_ID, mount });
    flow.renderQuestion();
    mount.querySelectorAll("button")[1].click(); // 会えていません
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(client.call).toHaveBeenCalledWith(
      "POST",
      `/v1/latches/${LATCH_ID}/attendance`,
      { body: { attended: false } },
    );
  });

  it("200で「回答を送りました」表示(2択は消す)", async () => {
    const { mount, flow } = mountFlow(async () => ({
      latch_id: LATCH_ID, actual_attended: true,
    }));
    flow.renderQuestion();
    await flow.submit(true);
    expect(mount.textContent).toContain(ATTENDANCE_DONE_TEXT);
    expect(mount.querySelectorAll("button").length).toBe(0);
  });

  it("409 ATTENDANCE_ALREADY_SUBMITTEDで回答済み表示に切替", async () => {
    const { mount, flow } = mountFlow(() => {
      throw apiError(409, "ATTENDANCE_ALREADY_SUBMITTED");
    });
    flow.renderQuestion();
    await flow.submit(true);
    expect(mount.textContent).toContain(ATTENDANCE_ALREADY_TEXT);
    expect(mount.querySelectorAll("button").length).toBe(0);
  });

  it("409 ATTENDANCE_WINDOW_CLOSEDで非表示化(3日窓はサーバ判定)", async () => {
    const { mount, flow } = mountFlow(() => {
      throw apiError(409, "ATTENDANCE_WINDOW_CLOSED");
    });
    flow.renderQuestion();
    await flow.submit(true);
    expect(mount.children.length).toBe(0); // 完全に非表示(§9-6①)
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npm test`
Expected: `tests/latch-attendance.test.js` の6件がFAIL/ERROR / 他はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/latch/attendance.js` を作成:

```js
// 実施自己申告(D-09・M3 ws-7 design §2.7)。completed詳細の2択。
// 未回答かどうかの判別フィールドがないため、質問を表示してPOSTし、
// 409で表示を切替える(窓判定・二重回答ともサーバが真実)。
import {
  ATTENDANCE_ALREADY_TEXT,
  ATTENDANCE_DONE_TEXT,
  ATTENDANCE_ERROR_TEXT,
  ATTENDANCE_QUESTION,
  RATE_LIMIT_TEXT,
} from "./texts.js";

export const createAttendanceFlow = ({ client, latchId, mount }) => {
  const noteEl = document.createElement("p");
  noteEl.className = "attendance-note";
  noteEl.hidden = true;

  const showNote = (text) => {
    noteEl.textContent = text;
    noteEl.hidden = false;
  };

  const submit = async (attended) => {
    mount.querySelector(".attendance-field")?.remove(); // 2択を外す
    try {
      await client.call("POST", `/v1/latches/${latchId}/attendance`, {
        body: { attended },
      });
      showNote(ATTENDANCE_DONE_TEXT);
    } catch (err) {
      if (err?.code === "ATTENDANCE_ALREADY_SUBMITTED") {
        showNote(ATTENDANCE_ALREADY_TEXT); // 回答済み表示へ切替
        return;
      }
      if (err?.code === "ATTENDANCE_WINDOW_CLOSED") {
        mount.replaceChildren(); // 3日窓超過は非表示化
        return;
      }
      showNote(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : ATTENDANCE_ERROR_TEXT);
    }
  };

  const renderQuestion = () => {
    const field = document.createElement("fieldset");
    field.className = "attendance-field";
    const legend = document.createElement("legend");
    legend.textContent = ATTENDANCE_QUESTION;
    field.append(legend);
    for (const [value, label] of [["true", "会いました"], ["false", "会えていません"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.value = value;
      button.textContent = label;
      button.addEventListener("click", () => submit(value === "true"));
      field.append(button);
    }
    mount.replaceChildren(field, noteEl);
  };

  return { renderQuestion, submit };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-attendance 6+latch-chat 9+latch-respond 8+app-state 3+router 6+latch-view 20+既存81=**133 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/attendance.js frontend/tests/latch-attendance.test.js
git commit -m "feat: add attendance flow switching display on 409 codes (M3 ws-7)"
```

### Task 10: frontend — report.js(通報flow・design §2.7)

**Files:**
- Create: `frontend/src/latch/report.js`
- Test: `frontend/tests/latch-report.test.js`(新規・7件)

**Interfaces:**
- Consumes: Task 4の `REPORT_REASON_OPTIONS`・`REPORT_TOAST_TEXT`・`RATE_LIMIT_TEXT`・`escapeHtml`
- Produces: `createReportFlow({ client, chrome, modal }) -> { open({ latchId, participants: array|null, meId }): void, submit(): Promise, close(): void }`。openの3形 — ①`participants=null`(提案1対1): 対象選択なし・bodyは`{latch_id, reason}`(reportee_id省略・案X) ②participantsあり+通報者以外が1人(成立済み1対1): 相手固定・body `{reportee_id, latch_id, reason}` ③participantsあり+2人以上(成立済みグループ): 対象者ラジオ選択→bodyのreportee_idは選択値。201でモーダルを閉じトースト表示。Task 12のdetail.jsとTask 13のmain.jsが消費

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-report.test.js` を新規作成:

```js
import { describe, expect, it, vi } from "vitest";
import { REPORT_TOAST_TEXT } from "../src/latch/texts.js";
import { createReportFlow } from "../src/latch/report.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const THIRD = "44444444-4444-4444-8444-444444444444";

const makeFlow = (callImpl) => {
  const client = { call: vi.fn(callImpl ?? (async () => ({ report_id: "r1" }))) };
  const chrome = { showToast: vi.fn() };
  const modal = document.createElement("div");
  const flow = createReportFlow({ client, chrome, modal });
  return { client, chrome, modal, flow };
};

const selectReason = (modal, value) => {
  const radio = modal.querySelector(`input[name="reason"][value="${value}"]`);
  radio.checked = true;
};

describe("通報flow(design §2.7)", () => {
  it("成立済み1対1: 相手固定でreportee_id明示のbodyを送る", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({
      latchId: LATCH_ID,
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手", profile: {} },
      ],
      meId: ME,
    });
    expect(modal.hidden).toBe(false);
    selectReason(modal, "inappropriate_content");
    await flow.submit();
    expect(client.call).toHaveBeenCalledWith("POST", "/v1/reports", {
      body: {
        reportee_id: PEER,
        latch_id: LATCH_ID,
        reason: "inappropriate_content",
      },
    });
  });

  it("成立済みグループ: 対象者を選択してreportee_id=選択値", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({
      latchId: LATCH_ID,
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手A", profile: {} },
        { user_id: THIRD, display_name: "相手B", profile: {} },
      ],
      meId: ME,
    });
    modal.querySelector(`input[name="reportee"][value="${THIRD}"]`).checked = true;
    selectReason(modal, "other");
    await flow.submit();
    expect(client.call).toHaveBeenCalledWith("POST", "/v1/reports", {
      body: { reportee_id: THIRD, latch_id: LATCH_ID, reason: "other" },
    });
  });

  it("提案1対1(participants=null): reportee_idを省略したbody{latch_id, reason}(案X)", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    selectReason(modal, "unpleasant_behavior");
    await flow.submit();
    expect(client.call).toHaveBeenCalledWith("POST", "/v1/reports", {
      body: { latch_id: LATCH_ID, reason: "unpleasant_behavior" },
    });
  });

  it("理由4値のコードがそのままAPI値になる", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    for (const code of [
      "inappropriate_content",
      "unpleasant_behavior",
      "suspected_impersonation",
      "other",
    ]) {
      selectReason(modal, code);
      await flow.submit();
      expect(client.mock.calls.at(-1)[2].body.reason).toBe(code);
    }
  });

  it("201でモーダルを閉じトースト表示", async () => {
    const { chrome, modal, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    selectReason(modal, "other");
    await flow.submit();
    expect(modal.hidden).toBe(true);
    expect(chrome.showToast).toHaveBeenCalledWith(REPORT_TOAST_TEXT);
  });

  it("理由未選択は送信せず案内文言(422を出さない)", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    await flow.submit(); // 何も選んでいない
    expect(client.call).not.toHaveBeenCalled();
    expect(modal.hidden).toBe(false); // 閉じない
    expect(modal.querySelector("[data-role=report-error]").hidden).toBe(false);
  });

  it("失敗時はモーダルを閉じずエラー表示(RATE_LIMITEDは既存文言)", async () => {
    const apiError = Object.assign(new Error("x"), {
      name: "ApiError", status: 429, code: "RATE_LIMITED",
    });
    const { client, modal, flow } = makeFlow(() => {
      throw apiError;
    });
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    selectReason(modal, "other");
    await flow.submit();
    expect(modal.hidden).toBe(false);
    expect(client.call).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npm test`
Expected: `tests/latch-report.test.js` の7件がFAIL/ERROR / 他はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/latch/report.js` を作成:

```js
// 通報flow(M3 ws-7 design §2.7・08 §5.2)。提案詳細と成立済み詳細の両画面
// から開く。成立済み=reportee_id明示(1対1は相手固定・グループは選択)、
// 提案1対1=reportee_id省略body(案X: サーバがlatch参加者から解決)。
import {
  RATE_LIMIT_TEXT,
  REPORT_REASON_OPTIONS,
  REPORT_TOAST_TEXT,
} from "./texts.js";
import { escapeHtml } from "./view.js";

export const createReportFlow = ({ client, chrome, modal }) => {
  let latchId = null;
  let fixedReporteeId = null;
  let fixedName = "";
  let candidates = []; // [{id, name}] — グループ選択肢
  let omitReportee = false;

  const render = () => {
    const targetHtml = fixedReporteeId
      ? `<p class="report-target">通報対象: <strong>${escapeHtml(fixedName)}</strong></p>`
      : candidates.length > 0
        ? `<fieldset class="report-target-select"><legend>通報する相手</legend>${candidates
            .map(
              (c) =>
                `<label><input type="radio" name="reportee" value="${c.id}" /> ${escapeHtml(c.name)}</label>`,
            )
            .join("")}</fieldset>`
        : ""; // 提案1対1: 対象表示なし(非開示のため)
    modal.innerHTML = `
      <section class="confirmation report-dialog" role="dialog" aria-modal="true" aria-labelledby="reportTitle">
        <button type="button" class="modal-close" data-role="report-close" aria-label="閉じる"><i class="ph ph-x"></i></button>
        <p class="section-kicker centered"><span></span>REPORT</p>
        <h2 id="reportTitle">通報</h2>
        ${targetHtml}
        <fieldset class="report-reasons"><legend>理由</legend>${REPORT_REASON_OPTIONS.map(
          ([code, label]) =>
            `<label><input type="radio" name="reason" value="${code}" /> ${label}</label>`,
        ).join("")}</fieldset>
        <p class="report-error" data-role="report-error" role="alert" hidden></p>
        <button type="button" class="report-submit" data-role="report-submit">通報する</button>
      </section>
    `;
    modal.querySelector("[data-role=report-close]").addEventListener("click", close);
    modal.querySelector("[data-role=report-submit]").addEventListener("click", submit);
  };

  const showError = (text) => {
    const errorEl = modal.querySelector("[data-role=report-error]");
    errorEl.textContent = text;
    errorEl.hidden = false;
  };

  const open = ({ latchId: id, participants = null, meId = null }) => {
    latchId = id;
    fixedReporteeId = null;
    fixedName = "";
    candidates = [];
    omitReportee = participants == null;
    if (participants) {
      const others = participants.filter((p) => p.user_id !== meId);
      if (others.length === 1) {
        fixedReporteeId = others[0].user_id; // 1対1: 相手固定
        fixedName = others[0].display_name;
      } else {
        candidates = others.map((p) => ({ id: p.user_id, name: p.display_name }));
      }
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
    const reason = modal.querySelector('input[name="reason"]:checked')?.value;
    if (!reason) {
      showError("理由を選んでください。");
      return;
    }
    const selected = modal.querySelector('input[name="reportee"]:checked')?.value;
    const body = { latch_id: latchId, reason };
    if (fixedReporteeId) body.reportee_id = fixedReporteeId;
    else if (selected) body.reportee_id = selected;
    // omitReportee(提案1対1)はreportee_idなしのまま送る(案X)
    try {
      await client.call("POST", "/v1/reports", { body });
      close();
      chrome.showToast(REPORT_TOAST_TEXT); // 受付・記録のみ(08 §5.2)
    } catch (err) {
      showError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : "送信できませんでした。");
    }
  };

  // backdropクリックで閉じる(confirmationModalと同型・一度だけwire)
  modal.addEventListener("click", (event) => {
    if (event.target === modal && !modal.hidden) close();
  });

  return { open, submit, close };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-report 7+latch-attendance 6+latch-chat 9+latch-respond 8+app-state 3+router 6+latch-view 20+既存81=**140 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/report.js frontend/tests/latch-report.test.js
git commit -m "feat: add report flow with fixed/selected/omitted reportee forms (M3 ws-7)"
```

### Task 11: frontend — home.js(ホーム2セクション+Intent N件・design §2.2)

**Files:**
- Create: `frontend/src/latch/home.js`
- Test: `frontend/tests/latch-home.test.js`(新規・10件)

**Interfaces:**
- Consumes: Task 4のview関数・texts定数・既存 `format.js` の `formatCategory` / `formatTime`
- Produces: `splitStatuses(items) -> {candidates, matched}`(終了3statusは捨てる)・`latchCardHtml(latch, nowIso) -> string`(§2-2の卡片)・`createHome({ client, el }) -> { load(), loadMore(), loadIntents() }`。el = `{candidateList, matchedList, moreButton, intentCount, intentList}`(HTMLElement)。Task 13のmain.jsが消費

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-home.test.js` を新規作成:

```js
import { describe, expect, it, vi } from "vitest";
import { NO_LATCH_TITLE } from "../src/latch/texts.js";
import {
  createHome,
  latchCardHtml,
  splitStatuses,
} from "../src/latch/home.js";

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
const latch = (status, overrides = {}) => ({
  id: LATCH_ID,
  status,
  response_deadline: "2026-12-01T12:00:00+09:00",
  expires_at: "2026-12-04T12:00:00+09:00",
  created_at: "2026-10-01T09:00:00+09:00",
  completed_at: null,
  proposal: fullProposal,
  is_group: false,
  my_response: null,
  remaining_responses: 1,
  ...overrides,
});

const mountHome = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const el = {
    candidateList: document.createElement("div"),
    matchedList: document.createElement("div"),
    moreButton: document.createElement("button"),
    intentCount: document.createElement("button"),
    intentList: document.createElement("div"),
  };
  el.moreButton.hidden = true;
  const home = createHome({ client, el });
  return { client, el, home };
};

describe("splitStatuses(design §2.2・引用#9)", () => {
  it("proposed/partial_accept→候補・matched/completed→成立済み・終了3種は捨てる", () => {
    const { candidates, matched } = splitStatuses([
      latch("proposed"),
      latch("partial_accept", { id: "99999999-9999-4999-8999-999999999999" }),
      latch("matched", { id: "88888888-8888-4888-8888-888888888888" }),
      latch("completed", { id: "77777777-7777-4777-8777-777777777777" }),
      latch("rejected", { id: "66666666-6666-4666-8666-666666666666" }),
      latch("expired", { id: "55555555-5555-4555-8555-555555555555" }),
      latch("cancelled", { id: "44444444-4444-4444-8444-444444444444" }),
    ]);
    expect(candidates.map((l) => l.status)).toEqual(["proposed", "partial_accept"]);
    expect(matched.map((l) => l.status)).toEqual(["matched", "completed"]);
  });
});

describe("latchCardHtml", () => {
  it("カード行は#/latches/{id}へのリンク", () => {
    const html = latchCardHtml(latch("proposed"), "2026-10-01T09:00:00+09:00");
    expect(html).toContain(`href="#/latches/${LATCH_ID}"`);
    expect(html).toContain("2026-10-01 20:00 天文館周辺"); // 見出し=日時+場所
    expect(html).toContain("一致度 高");
  });
});

describe("createHome(design §2.2)", () => {
  it("loadはGET /v1/latches?limit=20を1回・itemsをstatusで仕分け描画", async () => {
    const { client, el, home } = mountHome(async () => ({
      items: [latch("proposed"), latch("matched", { id: "88888888-8888-4888-8888-888888888888" })],
      next_cursor: null,
    }));
    await home.load();
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/latches?limit=20");
    expect(el.candidateList.querySelectorAll(".latch-card").length).toBe(1);
    expect(el.matchedList.querySelectorAll(".latch-card").length).toBe(1);
  });

  it("終了済み(rejected/expired/cancelled)はどちらのセクションにも出ない", async () => {
    const { el, home } = mountHome(async () => ({
      items: [latch("rejected"), latch("expired", { id: "55555555-5555-4555-8555-555555555555" }), latch("cancelled", { id: "44444444-4444-4444-8444-444444444444" })],
      next_cursor: null,
    }));
    await home.load();
    expect(el.candidateList.querySelectorAll(".latch-card").length).toBe(0);
    expect(el.matchedList.querySelectorAll(".latch-card").length).toBe(0);
  });

  it("next_cursorが残れば「もっと見る」を表示し追頁でcursorを渡す", async () => {
    const pages = [
      { items: [latch("proposed")], next_cursor: "c1" },
      { items: [latch("proposed", { id: "99999999-9999-4999-8999-999999999999" })], next_cursor: null },
    ];
    const { client, el, home } = mountHome(async () => pages.shift());
    await home.load();
    expect(el.moreButton.hidden).toBe(false);
    await home.loadMore();
    expect(client.mock.calls[1]).toEqual([
      "GET",
      "/v1/latches?limit=20&cursor=c1",
    ]);
    expect(el.candidateList.querySelectorAll(".latch-card").length).toBe(2);
    expect(el.moreButton.hidden).toBe(true); // 終端
  });

  it("next_cursor=nullなら「もっと見る」は非表示のまま", async () => {
    const { el, home } = mountHome(async () => ({ items: [], next_cursor: null }));
    await home.load();
    expect(el.moreButton.hidden).toBe(true);
  });

  it("Intent N件: active数を反映しpopoverにactive一覧の行(カテゴリ・時刻)", async () => {
    const intent = (primary) => ({
      id: "aaaaaaa1-1111-4111-8111-111111111111",
      status: "active",
      structured_intent: {
        category: { primary, secondary: null },
        time: { start: "2026-10-01T20:00:00+09:00", end: null },
      },
      expires_at: null,
    });
    const { client, el, home } = mountHome(async (method, path) => {
      if (path.includes("status=active")) {
        return { items: [intent("drinking"), intent("meal")], next_cursor: null };
      }
      return { items: [], next_cursor: null }; // draft
    });
    await home.loadIntents();
    expect(client.mock.calls[0]).toEqual([
      "GET",
      "/v1/intents?status=active",
    ]);
    expect(el.intentCount.textContent).toContain("Intent");
    expect(el.intentCount.textContent).toContain("2件");
    expect(el.intentList.querySelectorAll(".intent-row").length).toBe(2);
    expect(el.intentList.textContent).toContain("飲み"); // カテゴリラベル
  });

  it("下書きがあれば件数のみ添える(表示のみ)", async () => {
    const { el, home } = mountHome(async (method, path) => {
      if (path.includes("status=active")) {
        return { items: [], next_cursor: null };
      }
      return {
        items: [
          { id: "d1", status: "draft", structured_intent: {} },
          { id: "d2", status: "draft", structured_intent: {} },
        ],
        next_cursor: null,
      };
    });
    await home.loadIntents();
    expect(el.intentCount.textContent).toContain("0件"); // カウントはactive数
    expect(el.intentList.textContent).toContain("下書き 2件");
  });

  it("候補セクションの空状態は「まだ提案はありません」", async () => {
    const { el, home } = mountHome(async () => ({ items: [], next_cursor: null }));
    await home.load();
    expect(el.candidateList.textContent).toContain(NO_LATCH_TITLE);
  });

  it("hidden_until_match形の候補カードは「条件が合う候補があります」", async () => {
    const { el, home } = mountHome(async () => ({
      items: [latch("proposed", { proposal: { headcount: 2, match_level: "medium" } })],
      next_cursor: null,
    }));
    await home.load();
    expect(el.candidateList.textContent).toContain("条件が合う候補があります");
    expect(el.candidateList.textContent).not.toContain("天文館");
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npm test`
Expected: `tests/latch-home.test.js` の10件がFAIL/ERROR / 他はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/latch/home.js` を作成:

```js
// ホーム(M3 ws-7 design §2.2)。入力画面=ホーム — workspaceの下段へ
// LATCH候補・成立済みLATCHの2セクション。GET /v1/latches(1回)をクライアント
// 側でstatus仕分け(終了済みは表示しない・引用#9)。topbar「Intent N件」は
// active数を動的化しpopoverに一覧(表示のみ)。
import { formatCategory, formatTime } from "../intent/format.js";
import {
  HIDDEN_PROPOSAL_TEXT,
  NO_LATCH_NOTE,
  NO_LATCH_TITLE,
  NO_MATCHED_NOTE,
  NO_MATCHED_TITLE,
  STATUS_TEXT,
} from "./texts.js";
import {
  conditionSummaryLines,
  escapeHtml,
  matchLevelText,
  remainingTimeText,
} from "./view.js";

const CANDIDATE_STATUSES = new Set(["proposed", "partial_accept"]);
const MATCHED_STATUSES = new Set(["matched", "completed"]);

export const splitStatuses = (items) => ({
  candidates: items.filter((item) => CANDIDATE_STATUSES.has(item.status)),
  matched: items.filter((item) => MATCHED_STATUSES.has(item.status)),
  // rejected/expired/cancelled は捨てる(ホームの一覧から終了済みとして
  // 扱われる — 引用#9。終了済みセクションは設けない)
});

export const latchCardHtml = (latchItem, nowIso) => {
  const lines = conditionSummaryLines(latchItem);
  // 最小形(hidden_until_match)は条件サマリを出さない(引用#5)
  const title = lines ? `${lines[0]} ${lines[1] ?? ""}`.trim() : HIDDEN_PROPOSAL_TEXT;
  const deadline = CANDIDATE_STATUSES.has(latchItem.status)
    ? `<span class="latch-card-deadline">${escapeHtml(
        remainingTimeText(latchItem.response_deadline, nowIso),
      )}</span>`
    : "";
  return `<a class="latch-card" href="#/latches/${latchItem.id}">
    <span class="latch-card-title">${escapeHtml(title)}</span>
    <span class="latch-card-meta">
      <span class="badge">${STATUS_TEXT[latchItem.status] ?? latchItem.status}</span>
      <span class="badge">一致度 ${matchLevelText(latchItem)}</span>
      ${deadline}
    </span>
  </a>`;
};

const emptyHtml = (title, note) =>
  `<p class="latch-empty"><strong>${title}</strong><span>${note}</span></p>`;

const intentRow = (intent) => {
  const row = document.createElement("div");
  row.className = "intent-row";
  const structured = intent.structured_intent ?? {};
  row.innerHTML = `<span class="intent-row-category">${escapeHtml(
    formatCategory(structured.category),
  )}</span>
  <span class="intent-row-time">${escapeHtml(
    formatTime(
      structured.time?.start,
      structured.time?.end,
      new Date().toISOString(),
    ),
  )}</span>`;
  return row;
};

export const createHome = ({ client, el }) => {
  let items = [];
  let cursor = null;

  const render = () => {
    const { candidates, matched } = splitStatuses(items);
    const nowIso = new Date().toISOString();
    el.candidateList.innerHTML = candidates.length
      ? candidates.map((item) => latchCardHtml(item, nowIso)).join("")
      : emptyHtml(NO_LATCH_TITLE, NO_LATCH_NOTE);
    el.matchedList.innerHTML = matched.length
      ? matched.map((item) => latchCardHtml(item, nowIso)).join("")
      : emptyHtml(NO_MATCHED_TITLE, NO_MATCHED_NOTE);
    el.moreButton.hidden = cursor == null; // cursorは不透明文字列(引用#14)
  };

  const load = async () => {
    const data = await client.call("GET", "/v1/latches?limit=20");
    items = data.items;
    cursor = data.next_cursor;
    render();
  };

  const loadMore = async () => {
    if (cursor == null) return;
    const data = await client.call(
      "GET",
      `/v1/latches?limit=20&cursor=${encodeURIComponent(cursor)}`,
    );
    items = items.concat(data.items); // 追加分だけ追加
    cursor = data.next_cursor;
    render();
  };

  const loadIntents = async () => {
    // active一覧(表示)+下書き件数(表示のみ・カウントはactive数)
    const [active, drafts] = await Promise.all([
      client.call("GET", "/v1/intents?status=active"),
      client.call("GET", "/v1/intents?status=draft"),
    ]);
    el.intentCount.innerHTML = `Intent <span>${active.items.length}件</span>`;
    el.intentList.replaceChildren(...active.items.map(intentRow));
    if (drafts.items.length > 0) {
      const note = document.createElement("p");
      note.className = "intent-drafts";
      note.textContent = `下書き ${drafts.items.length}件`;
      el.intentList.append(note);
    }
  };

  return { load, loadMore, loadIntents };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-home 10+latch-report 7+latch-attendance 6+latch-chat 9+latch-respond 8+app-state 3+router 6+latch-view 20+既存81=**150 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/home.js frontend/tests/latch-home.test.js
git commit -m "feat: add home sections with status split and intent count (M3 ws-7)"
```

### Task 12: frontend — detail.js(LATCH詳細・1画面3姿・design §2.3)

**Files:**
- Create: `frontend/src/latch/detail.js`
- Test: `frontend/tests/latch-detail.test.js`(新規・11件)

**Interfaces:**
- Consumes: Task 4のview関数・texts・Task 7の `createRespondFlow`・Task 8の `createChat`・Task 9の `createAttendanceFlow`・Task 6のappState
- Produces: `createDetail({ client, appState, chrome, root, reportFlow }) -> { show(latchId): Promise, refresh(): Promise }`。rootは `#detailScreen` 要素・reportFlowはTask 10のflow(main.jsが構築して注入)。404/403は「提案が見つかりません」+ホーム導線。Task 13のmain.jsが消費

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-detail.test.js` を新規作成:

```js
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/session.js";
import { CLOSED_TEXT, LATCH_NOT_FOUND_TEXT } from "../src/latch/texts.js";
import { createDetail } from "../src/latch/detail.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const DEADLINE = "2026-12-01T12:00:00+09:00"; // 遠い未来(残時間テストを安定化)

const fullProposal = {
  time_summary: "2026-10-01 20:00",
  area_name: "天文館周辺",
  headcount: 2,
  category_primary: "drinking",
  category_secondary: null,
  budget: { max: 5000 },
  match_level: "high",
};
const minimalProposal = { headcount: 2, match_level: "medium" };

const baseLatch = (overrides = {}, proposal = fullProposal) => ({
  id: LATCH_ID,
  status: "proposed",
  response_deadline: DEADLINE,
  expires_at: "2026-12-04T12:00:00+09:00",
  created_at: "2026-10-01T09:00:00+09:00",
  completed_at: null,
  proposal,
  is_group: false,
  my_response: null,
  remaining_responses: 1,
  ...overrides,
});

const participants = [
  { user_id: ME, display_name: "自分", profile: { bio: "自分紹介" } },
  { user_id: PEER, display_name: "相手", profile: { bio: "よろしく" } },
];

const makeDetail = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const appState = {
    me: { id: ME, display_name: "自分", profile: {} },
    ensureMe: vi.fn(async () => ({ id: ME, display_name: "自分", profile: {} })),
  };
  const chrome = { showToast: vi.fn() };
  const root = document.createElement("section");
  const reportFlow = { open: vi.fn() };
  const detail = createDetail({ client, appState, chrome, root, reportFlow });
  return { client, appState, chrome, root, reportFlow, detail };
};

// GET詳細とGET messagesの両方に応答する既定モック
const apiFor = (latch) => async (method, path) => {
  if (path === `/v1/latches/${latch.id}/messages?limit=100`) {
    return { items: [], next_cursor: null };
  }
  return { latch };
};

describe("detail 3姿(design §2.3)", () => {
  it("提案姿(全形): 条件サマリ・一致度・残時間・回答3択", async () => {
    const { root, detail } = makeDetail(apiFor(baseLatch()));
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("2026-10-01 20:00");
    expect(root.textContent).toContain("天文館周辺");
    expect(root.textContent).toContain("ひとり5,000円まで");
    expect(root.textContent).toContain("一致度 高");
    expect(root.textContent).toContain("で締切"); // 残時間表示
    const buttons = [...root.querySelectorAll(".respond-button")];
    expect(buttons.map((b) => b.dataset.value)).toEqual(["yes", "defer", "no"]);
    expect(buttons.map((b) => b.textContent))
      .toEqual(["参加する", "今回は見送る", "辞退する"]);
  });

  it("提案姿(最小形): 条件サマリなし・「条件が合う候補があります」+一致度・残時間", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({}, minimalProposal)),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("条件が合う候補があります");
    expect(root.textContent).not.toContain("天文館周辺");
    expect(root.textContent).toContain("一致度 中");
    expect(root.textContent).toContain("で締切");
    expect(root.querySelectorAll(".respond-button").length).toBe(3);
  });

  it("回答済み(my_response=yes): 3択を差し替え「参加します」", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({ status: "partial_accept", my_response: "yes", remaining_responses: 1 })),
    );
    await detail.show(LATCH_ID);
    expect(root.querySelectorAll(".respond-button").length).toBe(0);
    expect(root.textContent).toContain("参加します");
    expect(root.textContent).toContain("あと1人の回答が必要"); // partial_acceptの現況
  });

  it("グループ提案: 必要人数の現況を表示(誰が回答済みかは出さない)", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({ is_group: true, remaining_responses: 2 })),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("あと2人の回答が必要");
  });

  it("成立済み姿: 参加者(表示名)・集合情報・次アクション・チャット領域", async () => {
    const matched = baseLatch(
      { status: "matched", remaining_responses: 0 },
      minimalProposal,
    );
    matched.participants = participants;
    matched.time_summary = "2026-10-01 20:00";
    matched.area_name = "天文館周辺";
    const { root, detail } = makeDetail(apiFor(matched));
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("相手"); // 参加者表示名
    expect(root.textContent).toContain("よろしく"); // プロフィールbio
    expect(root.textContent).toContain("2026-10-01 20:00");
    expect(root.textContent).toContain("チャットで挨拶を交わしましょう");
    expect(root.querySelector(".chat-messages")).not.toBeNull();
    expect(root.querySelectorAll(".respond-button").length).toBe(0); // 回答操作なし
  });

  it("completed姿: チャットは読取専用文言・attendance質問を表示", async () => {
    const completed = baseLatch(
      { status: "completed", remaining_responses: 0 },
      minimalProposal,
    );
    completed.participants = participants;
    completed.time_summary = "2026-10-01 20:00";
    completed.area_name = null;
    const { root, detail } = makeDetail(apiFor(completed));
    await detail.show(LATCH_ID);
    expect(root.textContent)
      .toContain("対象時刻を過ぎたため、このチャットは閲覧のみできます");
    expect(root.textContent).toContain("実際に会いましたか?");
    expect(root.querySelectorAll(".attendance-field button").length).toBe(2);
  });

  it("不成立(my_response=no): 自身の操作履歴「辞退しました」", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({ status: "rejected", my_response: "no" })),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("辞退しました");
    expect(root.textContent).not.toContain(CLOSED_TEXT);
  });

  it("不成立(それ以外): 統一文言のみ(相手の回答種別は開示しない)", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({ status: "cancelled", my_response: null })),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain(CLOSED_TEXT);
  });

  it("404は「提案が見つかりません」+ホームへ戻る導線", async () => {
    const { root, detail } = makeDetail(() => {
      throw new ApiError(404, "NOT_FOUND", "not found");
    });
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain(LATCH_NOT_FOUND_TEXT);
    expect(root.querySelector('a[href="#/"]')).not.toBeNull();
  });

  it("403も404と同じ扱い(参加者でない事実を開示しない)", async () => {
    const { root, detail } = makeDetail(() => {
      throw new ApiError(403, "FORBIDDEN", "forbidden");
    });
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain(LATCH_NOT_FOUND_TEXT);
  });

  it("通報導線: 提案1対1=あり/グループ提案=なし/成立済み=あり", async () => {
    // 提案1対1
    const oneOnOne = makeDetail(apiFor(baseLatch()));
    await oneOnOne.detail.show(LATCH_ID);
    expect(oneOnOne.root.textContent).toContain("この提案を通報する");
    // グループ提案(参加者非開示のため導線なし)
    const group = makeDetail(
      apiFor(baseLatch({ is_group: true, remaining_responses: 2 })),
    );
    await group.detail.show(LATCH_ID);
    expect(group.root.textContent).not.toContain("通報");
    // 成立済み
    const matched = baseLatch({ status: "matched" }, minimalProposal);
    matched.participants = participants;
    matched.time_summary = "2026-10-01 20:00";
    matched.area_name = null;
    const done = makeDetail(apiFor(matched));
    await done.detail.show(LATCH_ID);
    expect(done.root.textContent).toContain("このLATCHを通報する");
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npm test`
Expected: `tests/latch-detail.test.js` の11件がFAIL/ERROR / 他はPASS

- [ ] **Step 3: 最小実装**

`frontend/src/latch/detail.js` を作成:

```js
// LATCH詳細(M3 ws-7 design §2.3)。1画面3姿 — statusでproposal/matched/
// closedへ分岐(分岐の入力は常にサーバのstatus・API応答で再描画)。
// ページ内で状態が変わる場面(回答・成立・締切)はすべて再取得で収束する。
import { createAttendanceFlow } from "./attendance.js";
import { createChat } from "./chat.js";
import { createRespondFlow } from "./respond.js";
import {
  ANSWERED_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  HOME_LINK_TEXT,
  LATCH_NOT_FOUND_TEXT,
  NEXT_ACTION_TEXT,
  RESPONSE_BUTTONS,
} from "./texts.js";
import {
  closedText,
  conditionSummaryLines,
  escapeHtml,
  groupNeedText,
  latchMode,
  matchLevelText,
  remainingMs,
  remainingTimeText,
} from "./view.js";

const DEADLINE_TICK_MS = 30_000;

export const createDetail = ({ client, appState, chrome, root, reportFlow }) => {
  let latchId = null;
  let timer = null;
  let chat = null;

  const stopTimers = () => {
    if (timer) clearInterval(timer);
    timer = null;
    chat?.stop();
    chat = null;
  };

  const fetchLatch = () => client.call("GET", `/v1/latches/${latchId}`);

  const refresh = async () => {
    // 回答成功・409系の収束先 — 表示の唯一の真実は詳細応答(design §2.5)
    try {
      const data = await fetchLatch();
      await render(data.latch);
    } catch {
      // 再取得失敗時は現在の表示を維持(次の操作で再試行)
    }
  };

  const showError = (message) => {
    let errorEl = root.querySelector(".detail-error");
    if (!errorEl) {
      errorEl = document.createElement("p");
      errorEl.className = "detail-error";
      errorEl.setAttribute("role", "alert");
      root.append(errorEl);
    }
    errorEl.textContent = message;
    errorEl.hidden = false;
  };

  // -- 通報導線(design §2.7): 成立済み=両画面・提案は1対1のみ --
  const reportEntryHtml = (mode, isGroup) => {
    if (mode === "matched") return "このLATCHを通報する";
    if (mode === "proposal" && !isGroup) return "この提案を通報する";
    return null; // グループ提案: 参加者非開示のため導線なし
  };

  const wireReportEntry = (section, latch, participants) => {
    const entry = section.querySelector("[data-role=report-entry]");
    if (!entry) return;
    entry.addEventListener("click", () => {
      // participants=null(提案1対1)はreportee_id省略body(案X)
      reportFlow.open({
        latchId: latch.id,
        participants: participants ?? null,
        meId: appState.me?.id ?? null,
      });
    });
  };

  const startDeadlineTimer = (latch) => {
    const deadlineEl = root.querySelector("[data-role=deadline]");
    if (!deadlineEl) return;
    const update = () => {
      const nowIso = new Date().toISOString();
      deadlineEl.textContent = remainingTimeText(latch.response_deadline, nowIso);
      if (remainingMs(latch.response_deadline, nowIso) <= 0) {
        // 残時間0で回答ボタンをdisabled(送信可否の真実はサーバの409)
        for (const button of root.querySelectorAll(".respond-button")) {
          button.disabled = true;
        }
      }
    };
    update();
    timer = setInterval(update, DEADLINE_TICK_MS);
  };

  const renderProposal = (latch) => {
    const section = document.createElement("section");
    section.className = "latch-detail";
    const summaryLines = conditionSummaryLines(latch);
    // visibility分岐: 最小形は条件サマリ欄を出さず本文を置く(引用#5)
    const summaryHtml = summaryLines
      ? `<p class="latch-summary">${summaryLines.map(escapeHtml).join("<br>")}</p>`
      : `<p class="latch-summary latch-summary-hidden">${HIDDEN_PROPOSAL_TEXT}</p>`;
    const need = groupNeedText(latch);
    const metaHtml = [
      `<span class="badge">一致度 ${matchLevelText(latch)}</span>`,
      `<span class="latch-deadline" data-role="deadline"></span>`,
      need ? `<span class="badge">${escapeHtml(need)}</span>` : "",
    ].join("");
    // 回答済みなら3択を差し替え(design §2.4)
    const actionsHtml = latch.my_response
      ? `<p class="answered-text">${ANSWERED_TEXT[latch.my_response]}</p>${
          latch.my_response === "yes" && need
            ? `<p class="group-note">${escapeHtml(need)}</p>`
            : ""
        }`
      : `<div class="respond-actions">${RESPONSE_BUTTONS.map(
          ([value, label]) =>
            `<button type="button" class="respond-button" data-value="${value}">${label}</button>`,
        ).join("")}</div>`;
    const entryLabel = reportEntryHtml("proposal", latch.is_group);
    section.innerHTML = `
      <p class="section-kicker"><span></span>LATCH</p>
      ${summaryHtml}
      <p class="latch-meta">${metaHtml}</p>
      ${actionsHtml}
      ${entryLabel ? `<button type="button" class="report-entry" data-role="report-entry">${entryLabel}</button>` : ""}
    `;
    root.replaceChildren(section);
    const respondFlow = createRespondFlow({
      client,
      latchId: latch.id,
      refresh,
      showError,
    });
    const buttons = [...section.querySelectorAll(".respond-button")];
    for (const button of buttons) {
      button.addEventListener("click", () =>
        respondFlow.submit(button.dataset.value, buttons),
      );
    }
    startDeadlineTimer(latch);
    wireReportEntry(section, latch, null);
  };

  const renderMatched = async (latch) => {
    const me = await appState.ensureMe();
    const section = document.createElement("section");
    section.className = "latch-detail";
    const participants = latch.participants ?? [];
    const participantsHtml = participants
      .map(
        (p) =>
          `<li class="participant"><strong>${escapeHtml(p.display_name)}</strong>${
            p.profile?.bio ? `<span>${escapeHtml(p.profile.bio)}</span>` : ""
          }</li>`,
      )
      .join("");
    section.innerHTML = `
      <p class="section-kicker"><span></span>LATCH</p>
      <h2 class="latch-heading">集合情報</h2>
      <p class="latch-summary">${escapeHtml(latch.time_summary ?? "")}${
        latch.area_name ? `<br>${escapeHtml(latch.area_name)}` : ""
      }</p>
      <ul class="participants">${participantsHtml}</ul>
      <p class="next-action">${NEXT_ACTION_TEXT}</p>
      <div class="chat-area" data-role="chat"></div>
      ${
        latch.status === "completed"
          ? `<div class="attendance" data-role="attendance"></div>`
          : ""
      }
      <button type="button" class="report-entry" data-role="report-entry">${reportEntryHtml("matched", latch.is_group)}</button>
    `;
    root.replaceChildren(section);
    chat = createChat({
      client,
      latch,
      meId: me.id,
      mount: section.querySelector('[data-role="chat"]'),
    });
    await chat.start();
    if (latch.status === "completed") {
      // 表示条件はstatusのみ(詳細応答のcompleted_atは常にnull・§9-6①。
      // 3日窓・二重回答はサーバの409で切替える)
      const attendanceFlow = createAttendanceFlow({
        client,
        latchId: latch.id,
        mount: section.querySelector('[data-role="attendance"]'),
      });
      attendanceFlow.renderQuestion();
    }
    wireReportEntry(section, latch, participants);
  };

  const renderClosed = (latch) => {
    const section = document.createElement("section");
    section.className = "latch-detail latch-closed";
    // 二値化はview.jsの純関数に一元化(自分の操作履歴/統一文言・03 §7)
    section.innerHTML = `
      <p class="section-kicker"><span></span>LATCH</p>
      <p class="closed-text">${escapeHtml(closedText(latch))}</p>
    `;
    root.replaceChildren(section);
  };

  const renderNotFound = () => {
    stopTimers();
    const section = document.createElement("section");
    section.className = "latch-detail latch-notfound";
    section.innerHTML = `<p class="notfound-text">${LATCH_NOT_FOUND_TEXT}</p>
      <a class="home-link" href="#/">${HOME_LINK_TEXT}</a>`;
    root.replaceChildren(section);
  };

  const render = async (latch) => {
    stopTimers();
    const mode = latchMode(latch);
    if (mode === "proposal") renderProposal(latch);
    else if (mode === "matched") await renderMatched(latch);
    else renderClosed(latch);
  };

  const show = async (id) => {
    latchId = id;
    try {
      const data = await fetchLatch();
      await render(data.latch);
    } catch (err) {
      // 404/403は同一扱い(参加者でない事実の開示を避ける・引用#16)
      if (err?.status === 404 || err?.status === 403) {
        renderNotFound();
        return;
      }
      renderNotFound(); // 通信エラーも導線つきの案内へ(再訪で再試行)
    }
  };

  return { show, refresh };
};
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-detail 11+latch-home 10+latch-report 7+latch-attendance 6+latch-chat 9+latch-respond 8+app-state 3+router 6+latch-view 20+既存81=**161 passed**)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/detail.js frontend/tests/latch-detail.test.js
git commit -m "feat: add latch detail with three status modes and report entry (M3 ws-7)"
```

### Task 13: frontend — 構造一式(index.html・screen.js・main.js・styles.css)

**Files:**
- Create: `frontend/src/intent/screen.js`(main.jsから入力画面配線を切り出し・**ロジック無変更**)
- Create: `frontend/tests/latch-shell.test.js`(新規・6件)
- Modify: `frontend/index.html`(§2の構造変更)
- Modify: `frontend/src/main.js`(再構成・importパス調整のみでロジックは動かさない)
- Modify: `frontend/styles.css`(末尾へ新画面分を追記・**既存クラス無変更**)

**Interfaces:**
- Consumes: Task 4〜12の全モジュール・既存 `initChrome`・`createClient`・`createSession`・`exchangeIdpToken`
- Produces: `initIntentScreen({ client, chrome }) -> void`(main.jsの条件リスト〜保存〜初期化の配線一式を担う。既存の入力ロジックと同一動作)

**既存81件無傷の構造保証(§9-2)**: 本Taskは `index.html`・`main.js`・`styles.css` と新規2ファイルのみに触れ、既存モジュール(`src/api/*`・`src/intent/*`・`src/ui/chrome.js`)と既存9テストファイルは1行も変更しない。既存試験が読むのはモジュールとindex.htmlのID群(smoke.test.js)のみ — IDは全て維持する(下の変更指定どおり)。

- [ ] **Step 1: 失敗するテストを書く**

`frontend/tests/latch-shell.test.js` を新規作成:

```js
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// happy-dom環境は import.meta.url を http スキームへ書き換えるため
// process.cwd()(= npm test 実行時の frontend/)基準で解決する(smoke.test.jsと同型)
const html = () => readFileSync(resolve(process.cwd(), "index.html"), "utf8");
const mainJs = () => readFileSync(resolve(process.cwd(), "src/main.js"), "utf8");
const css = () => readFileSync(resolve(process.cwd(), "styles.css"), "utf8");

describe("M3 ws-7 画面構造(design §2.1〜§2.2・§3)", () => {
  it("画面section構造: homeScreen(workspace+2セクション)とdetailScreen", () => {
    expect(html()).toContain('id="homeScreen"');
    expect(html()).toContain('id="detailScreen"');
    expect(html()).toContain('id="candidateList"');
    expect(html()).toContain('id="matchedList"');
    expect(html()).toContain('id="moreButton"');
    // 既存の入力画面要素はworkspace内に維持(smoke.test.jsのID群)
    expect(html()).toContain('class="workspace"');
  });

  it("Active Intent一覧: intentCountButtonとintentPopover・intentList", () => {
    expect(html()).toContain('id="intentCountButton"');
    expect(html()).toContain('id="intentPopover"');
    expect(html()).toContain('id="intentList"');
  });

  it("wordmarkのhrefは#/ ・reportModalがある", () => {
    expect(html()).toContain('href="#/"');
    expect(html()).toContain('id="reportModal"');
  });

  it("main.jsはscreen.jsとrouter.jsをimportして両画面を配線する", () => {
    expect(mainJs()).toContain('from "./intent/screen.js"');
    expect(mainJs()).toContain('from "./router.js"');
    expect(mainJs()).toContain('from "./latch/home.js"');
    expect(mainJs()).toContain('from "./latch/detail.js"');
    expect(mainJs()).toContain('from "./latch/report.js"');
    expect(mainJs()).toContain('from "./appState.js"');
  });

  it("styles.cssに新画面クラスがある(既存クラス無変更・design §3)", () => {
    for (const className of [
      ".latch-card", ".latch-section", ".chat-message", ".chat-mine",
      ".chat-theirs", ".respond-button", ".attendance-field", ".report-entry",
      ".latch-empty",
    ]) {
      expect(css()).toContain(className);
    }
  });

  it("tokenPanel等の既存構造は温存される", () => {
    for (const id of ["tokenPanel", "idpToken", "tokenConnect", "toast", "confirmationModal", "noticePopover"]) {
      expect(html()).toContain(`id="${id}"`);
    }
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd frontend && npm test`
Expected: `tests/latch-shell.test.js` の6件がFAIL(構造がまだ無い)/ 他はPASS

- [ ] **Step 3: 最小実装**

**(1) `frontend/src/intent/screen.js` を作成** — `frontend/src/main.js` の現在の「条件リスト」コメント行(86行目付近)からファイル末尾の初期化(273行目 `refreshActions();`)までを**そのまま切り出す**。変更点は次の3点のみ(ロジックは1行も動かさない):

```js
// 入力画面の配線(M3 ws-7 design §3。main.jsから切り出し・ロジック無変更)。
// DOM配線のみを担い、ロジックは各モジュールへ委ねる(元main.jsと同一)。
// 意図文言をログに出さない(01 §21)。
import { PRIVACY_VALUES, NOTIFICATION_VALUES } from "./format.js";
import {
  applyParseFallback,
  applyParseResult,
  canDraft,
  canSubmit,
  createFormState,
  missingRequired,
} from "./state.js";
import { createParseFlow } from "./parseFlow.js";
import {
  NG_NOTE_TEXT,
  REQUIRED_NOTE_TEXT,
  attachAddCondition,
  beginRowEdit,
  renderConditions,
} from "./conditions.js";
import { applyExpiryOptions, fetchExpiryOptions, selectedExpiry } from "./expiry.js";
import { createSaveFlow } from "./save.js";

const $ = (selector) => document.querySelector(selector);

export const initIntentScreen = ({ client, chrome }) => {
  const state = createFormState();

  // --- 条件リスト(main.jsから切り出し・ここから下は元コードそのまま) ---
  const conditionList = $("#conditionList");
  // …(main.jsの「条件リスト」〜「初期化」末尾の rerender(); refreshActions(); まで
  //    をそのまま貼り付ける。修正は次の2点のみ:
  //    ① 参照する showTokenPanel は使っていない(入力画面は参照しない)ため無いまま
  //    ② Escape/Cmd+Enter の keydown ハンドラもそのまま移す
};
```

**切り出しの正確な手順**: `main.js` の86行目(`// --- 条件リスト ---`)から273行目(`refreshActions();`)までを切り取り、`initIntentScreen` の関数本体へ貼り付ける。貼り付け後、`main.js` 側で使っていた識別子のうち `session`・`client`・`chrome`・`showTokenPanel` への参照を確認する — この区間が参照するのは `client`(parseFlow・expiry・saveFlow)と `chrome`(closeModal・openModal・showToast)のみのため、引数の2つで足りる。`$` はscreen.js内で定義し直す(上のとおり)。**state構築(`createFormState()`)もこの区間の先頭へ移す**(main.jsの34行目 `const state = createFormState();` をscreen.jsへ移動)。

**(2) `frontend/index.html` を変更** — 4箇所(既存行は一切消さない・IDは維持):

(a) wordmark(22行目):

```html
        <a class="wordmark" href="#/" aria-label="LATCH ホーム">LATCH</a>
```

(置換前: `<a class="wordmark" href="#main" aria-label="LATCH ホーム">LATCH</a>`)

(b) intent-countボタン(24行目)をpopover化:

```html
        <div class="popover-wrap">
          <button class="intent-count" id="intentCountButton" type="button" aria-expanded="false">Intent <span>0件</span></button>
          <div class="popover intent-popover" id="intentPopover" hidden>
            <p class="popover-kicker">ACTIVE INTENT</p>
            <div class="intent-list" id="intentList"></div>
          </div>
        </div>
```

(置換前: `<button class="intent-count" type="button">Intent <span>3件</span></button>`。**静的「3件」は動的化のため0件へ** — smoke.test.jsはこのボタンの文言を検査しない)

(c) `</main>` 全体の構造 — 65行目 `<main id="main" class="workspace">` を次へ置き換え(workspaceクラスは内側のsectionへ移し、既存の `.intent-editor` と `.deposit-settings` の2セクションは**1行も変更せず** `class="workspace"` のsectionで囲む。その下へ2セクションとdetailScreenを追加):

```html
      <main id="main">
        <div id="homeScreen">
          <section class="workspace" aria-label="新しいIntent">
            <!-- 既存の <section class="intent-editor">〜</aside>(deposit-settings)
                 をこの中へ・1行も変更しない -->
          </section>

          <section class="latch-section" id="candidateSection" aria-labelledby="candidateTitle">
            <div class="section-heading"><span aria-hidden="true"></span><h2 id="candidateTitle"><b>LATCH</b>候補</h2></div>
            <div class="latch-list" id="candidateList"></div>
            <button class="more-button" id="moreButton" type="button" hidden>もっと見る</button>
          </section>

          <section class="latch-section" id="matchedSection" aria-labelledby="matchedTitle">
            <div class="section-heading"><span aria-hidden="true"></span><h2 id="matchedTitle">成立済み<b>LATCH</b></h2></div>
            <div class="latch-list" id="matchedList"></div>
          </section>
        </div>
        <section class="detail-view" id="detailScreen" hidden></section>
      </main>
```

(d) confirmationModalの閉じタグ `</div>`(178行目)の後・`</div>`(app-shell閉じ・179行目)の前にreportModalを追加:

```html
      <div class="modal-backdrop" id="reportModal" role="presentation" hidden></div>
```

(中身はreport.jsのopenが組み立てる。backdrop構造はconfirmationModalと同型)

**(3) `frontend/src/main.js` を再構成** — ファイル全体を次へ置き換える:

```js
// エントリポイント(M3 ws-7 design §3)。共通初期化(テーマ・セッション・
// トークンパネル)+ルーター起動のみを担う。入力画面の配線は screen.js、
// ホーム・詳細は latch/* へ委譲。意図文言をログに出さない(01 §21)。
import { initChrome } from "./ui/chrome.js";
import { createClient } from "./api/client.js";
import { createSession, exchangeIdpToken } from "./api/session.js";
import { initIntentScreen } from "./intent/screen.js";
import { createAppState } from "./appState.js";
import { createRouter } from "./router.js";
import { createHome } from "./latch/home.js";
import { createDetail } from "./latch/detail.js";
import { createReportFlow } from "./latch/report.js";

const $ = (selector) => document.querySelector(selector);

const session = createSession();
const client = createClient({
  session,
  onSessionExpired: () => showTokenPanel(),
});

// --- 画面装飾(テーマ・popover・トースト・モーダル) -------------------------
// Active Intent一覧をpopoversへ追加(ws-7 §2.2・関数は無変更)
const chrome = initChrome({
  themeButton: $("#themeButton"),
  popovers: [
    { button: $("#noticeButton"), panel: $("#noticePopover") },
    { button: $("#accountButton"), panel: $("#accountPopover") },
    { button: $("#intentCountButton"), panel: $("#intentPopover") },
  ],
  toast: $("#toast"),
  modal: {
    root: $("#confirmationModal"),
    closeButton: $("#modalClose"),
    returnButton: $("#returnButton"),
  },
});

// --- 開発用トークンパネル(design §2.3・トークン未設定時のみ表示) ----------
const tokenPanel = $("#tokenPanel");
const tokenError = $("#tokenError");

function showTokenPanel() {
  tokenPanel.hidden = false;
  tokenError.hidden = true;
}

if (!session.hasTokens()) showTokenPanel();

$("#tokenConnect").addEventListener("click", async () => {
  const idpToken = $("#idpToken").value.trim();
  if (!idpToken) return;
  const button = $("#tokenConnect");
  button.disabled = true;
  try {
    const tokens = await exchangeIdpToken((...args) => fetch(...args), {
      provider: "google",
      idpToken,
    });
    session.save(tokens);
    tokenPanel.hidden = true;
    $("#idpToken").value = "";
  } catch (err) {
    tokenError.textContent =
      err?.status === 401
        ? "トークンが無効です。再発行して貼り直してください。"
        : "接続できませんでした。APIの起動を確認してください。";
    tokenError.hidden = false;
  } finally {
    button.disabled = false;
  }
});

// --- 入力画面(既存ロジック・screen.jsへ切り出し) ---------------------------
initIntentScreen({ client, chrome });

// --- ホーム・詳細・通報・ルーター(ws-7 design §2.1〜§2.2) ------------------
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

const reportFlow = createReportFlow({ client, chrome, modal: $("#reportModal") });
const detail = createDetail({
  client,
  appState,
  chrome,
  root: $("#detailScreen"),
  reportFlow,
});

const router = createRouter({
  screens: { home: $("#homeScreen"), latch: $("#detailScreen") },
  onRoute: (route) => {
    if (route.name === "home") {
      home.load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
      home.loadIntents().catch(() => {});
    } else {
      detail.show(route.id).catch(() => {});
    }
  },
});
router.start();
```

**(4) `frontend/styles.css` の末尾へ追記**(既存クラスは無変更。デザインシステムの変数 `--ink` `--green` `--green-dark` `--coral` `--paper` `--line` `--muted` `--field-surface` `--field-border` と `:focus-visible` 3px規定〔600行目付近の既存規定に乗る〕を利用):

```css
/* ===== M3 ws-7 追加: ホーム2セクション・LATCH詳細・チャット・通報 ===== */

.detail-view { max-width: 720px; margin: 0 auto; padding: 40px 24px 80px; }

.latch-section { max-width: 1100px; margin: 0 auto; padding: 24px 35px 48px; }
.latch-list { display: grid; gap: 12px; margin-top: 20px; }
.latch-card {
  display: flex; flex-direction: column; gap: 10px;
  padding: 18px 22px; border: 1px solid var(--line); border-radius: 14px;
  background: var(--paper); text-decoration: none; color: var(--ink);
  transition: border-color 0.15s ease;
}
.latch-card:hover, .latch-card:focus-visible { border-color: var(--green); }
.latch-card-title { font-size: 18px; font-weight: 700; letter-spacing: 0.01em; }
.latch-card-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.badge {
  font-size: 12px; font-weight: 700; letter-spacing: 0.06em;
  padding: 4px 10px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--muted);
}
.latch-card-deadline { font-size: 13px; font-weight: 700; color: var(--coral); }
.latch-empty {
  display: grid; gap: 6px; padding: 28px 22px;
  border: 1px dashed var(--line); border-radius: 14px; color: var(--muted);
}
.more-button {
  margin-top: 16px; padding: 10px 28px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--paper);
  font-weight: 700; cursor: pointer;
}
.intent-popover { min-width: 280px; }
.intent-list { display: grid; gap: 10px; }
.intent-row { display: flex; justify-content: space-between; gap: 12px; font-size: 14px; }
.intent-row-category { font-weight: 700; }
.intent-row-time { color: var(--muted); }
.intent-drafts { font-size: 13px; color: var(--muted); border-top: 1px solid var(--line); padding-top: 10px; }

.latch-detail { display: grid; gap: 20px; }
.latch-summary { font-size: 18px; line-height: 1.7; }
.latch-summary-hidden { color: var(--muted); }
.latch-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.latch-deadline { font-size: 14px; font-weight: 700; color: var(--coral); }
.respond-actions { display: grid; gap: 10px; }
.respond-button {
  padding: 14px 20px; border-radius: 12px; border: 1px solid var(--green);
  background: var(--green); color: #fff; font-size: 16px; font-weight: 700;
  cursor: pointer;
}
.respond-button:disabled { opacity: 0.5; cursor: default; }
.answered-text { font-size: 18px; font-weight: 700; }
.group-note { color: var(--muted); font-size: 14px; }
.latch-heading { font-size: 14px; letter-spacing: 0.08em; color: var(--muted); }
.participants { display: grid; gap: 10px; list-style: none; }
.participant { display: grid; gap: 4px; padding: 12px 16px; border: 1px solid var(--line); border-radius: 12px; }
.participant span { font-size: 14px; color: var(--muted); }
.next-action { font-weight: 700; }
.closed-text { font-size: 18px; }
.notfound-text { font-size: 18px; }
.home-link { color: var(--green); font-weight: 700; }
.detail-error { color: var(--coral); font-weight: 700; }

.chat-area { display: grid; gap: 12px; }
.chat-messages {
  display: grid; gap: 10px; max-height: 360px; overflow-y: auto;
  padding: 16px; border: 1px solid var(--line); border-radius: 14px;
  background: var(--field-surface);
}
.chat-message { max-width: 80%; padding: 10px 14px; border-radius: 14px; font-size: 15px; }
.chat-mine {
  justify-self: end; background: var(--green); color: #fff;
  border-bottom-right-radius: 4px;
}
.chat-theirs {
  justify-self: start; background: var(--paper);
  border: 1px solid var(--line); border-bottom-left-radius: 4px;
}
.chat-sender { display: block; font-size: 12px; color: var(--muted); margin-bottom: 2px; }
.chat-theirs .chat-sender { color: var(--muted); }
.chat-notice { color: var(--muted); font-size: 14px; }
.chat-form { display: flex; gap: 8px; }
.chat-input {
  flex: 1; padding: 10px 14px; border-radius: 10px;
  border: 1px solid var(--field-border); background: var(--field-surface-strong);
  font-family: inherit; font-size: 15px; resize: vertical;
}
.chat-input:disabled { opacity: 0.6; }
.chat-send {
  padding: 10px 22px; border-radius: 999px; border: none;
  background: var(--green); color: #fff; font-weight: 700; cursor: pointer;
}
.chat-send:disabled { opacity: 0.5; cursor: default; }

.attendance-field {
  display: grid; gap: 10px; padding: 16px 20px;
  border: 1px solid var(--line); border-radius: 14px;
}
.attendance-field legend { font-weight: 700; padding: 0 8px; }
.attendance-field button {
  padding: 12px 20px; border-radius: 12px; border: 1px solid var(--line);
  background: var(--paper); font-weight: 700; cursor: pointer;
}
.attendance-note { color: var(--muted); font-weight: 700; }

.report-entry {
  justify-self: start; padding: 8px 16px; font-size: 13px;
  border: none; background: none; color: var(--muted);
  text-decoration: underline; cursor: pointer;
}
.report-dialog { display: grid; gap: 16px; }
.report-target { font-size: 15px; }
.report-target-select, .report-reasons { display: grid; gap: 10px; border: 1px solid var(--line); border-radius: 12px; padding: 14px 18px; }
.report-target-select legend, .report-reasons legend { font-weight: 700; padding: 0 8px; }
.report-target-select label, .report-reasons label { display: block; font-size: 15px; }
.report-error { color: var(--coral); font-weight: 700; }
.report-submit {
  padding: 12px 24px; border-radius: 12px; border: none;
  background: var(--coral); color: #fff; font-weight: 700; cursor: pointer;
}

@media (max-width: 800px) {
  .latch-section { padding: 16px 20px 40px; }
  .chat-message { max-width: 92%; }
}
@media (max-width: 560px) {
  .detail-view { padding: 24px 16px 64px; }
  .chat-form { flex-direction: column; }
  .chat-send { width: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .latch-card { transition: none; }
}
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd frontend && npm test`
Expected: PASS(latch-shell 6+latch-detail 11+latch-home 10+latch-report 7+latch-attendance 6+latch-chat 9+latch-respond 8+app-state 3+router 6+latch-view 20+既存81=**167 passed**。**既存9ファイルが全てPASSしていることを特に確認する — smoke.test.jsのID群ピンが全て緑であること**)

- [ ] **Step 5: コミット**

```bash
git add frontend/index.html frontend/src/main.js frontend/src/intent/screen.js frontend/styles.css frontend/tests/latch-shell.test.js
git commit -m "feat: wire home/detail screens with hash router and shell structure (M3 ws-7)"
```

### Task 14: 全体検証(build・完了条件1〜7)

**Files:**(変更なし)

- [ ] **Step 1: frontend build**

Run: `cd frontend && npm run build`
Expected: exit 0(vite buildが成功・dist/生成)

- [ ] **Step 2: 完了条件1〜7を検証(§6の検証コマンドを順に実行)**

```bash
make lint && make test                       # 期待: exit 0・1196 passed
cd frontend && npm test                      # 期待: 167 passed(81+86)
cd frontend && npm run build                 # 期待: exit 0
ls frontend/tests/*.test.js | awk -F/ '{print $NF}' | sort | uniq -d   # 期待: 空
cd backend && find tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d  # 期待: 空
cd backend && uv run alembic heads           # 期待: 0006 (head) 単一(不変)
git diff --name-only main -- backend/alembic # 期待: 空(出力なし)
cd backend && uv run pytest tests/integration/test_safety_api.py --collect-only -q    # 期待: 10 collected
```

§6の完了条件8(test-ci)は**実行しない**。報告書に「スーパーバイザー検証待ち」と記録する(§0規律)。

### Task 15: 報告ファイル

**Files:**
- Create: `docs/plans/M3/ws-7-report.md`

- [ ] **Step 1: 報告ファイルを作成**

`docs/plans/M3/ws-7-report.md` を§7の形式どおり作成(検証コマンドの出力末尾を証拠として貼る)。

- [ ] **Step 2: コミット**

```bash
git add docs/plans/M3/ws-7-report.md
git commit -m "docs: add M3 ws-7 execution report (M3 ws-7)"
```

- [ ] **Step 3: 最終確認**

```bash
git status --short          # 期待: 空
git log --oneline main..HEAD  # Task 1〜15のコミット一覧
```

---

## 9. 計画書セルフレビュー(機械チェック・スーパーバイザー指示による。2026-10-01実施)

### 9-1. frontend新規テストファイル名のbasename衝突(運用ル則5・指示(1))

確認コマンド(2026-10-01実施・main時点の既存9ファイル):

```bash
ls frontend/tests/*.test.js | awk -F/ '{print $NF}' | sort
# → client / conditions / expiry / format / parseFlow / save / session / smoke / state(9ファイル)
```

本計画の新規10ファイル — `latch-view` / `router` / `app-state` / `latch-respond` / `latch-chat` / `latch-attendance` / `latch-report` / `latch-home` / `latch-detail` / `latch-shell`(.test.js) — は**すべて既存9ファイルと衝突しない**(上記コマンドで機械確認済み・`router.test.js` も `app-state.test.js` も既存に存在しない)。frontendのvitest includeは `tests/**/*.test.js` のためサブディレクトリ分割不要・フラットに新規追加する。backend側はファイル新規作成なし(§4)のため backend/tests のbasename一意は不変。

### 9-2. ピン試験への追随访問(触る全試験を挙げて影響有無を明記・指示(2))

| 試験ファイル | 触るか | 影響 |
|---|---|---|
| `backend/tests/unit/safety/test_safety_service.py` | **追記する**(Task 1・6件) | 末尾へ案Xの省略経路6件。既存16件は `report_user` の引数名互換(reportee_id明示呼び出しはキーワード引数のためデフォルト値追加で無傷)・検査順組替え後も同一結果(§8 Task 1 Step 3の検証どおり) |
| `backend/tests/unit/safety/test_safety_routes.py` | **追記する**(Task 2・2件) | 末尾へ2件。既存7件は `ReportRequest` のreportee_id必須→省略可変更の影響を受けない(既存試験はreportee_idを明示的に送る) |
| `backend/tests/integration/test_safety_api.py` | **追記する**(Task 3・1試験+SUBJECT_PREFIXの1行変更) | 末尾へ試験10。`SUBJECT_PREFIX` を `m3ws5-`→`m3ws7-` へ変更(§9-3)。既存9試験のロジックは無傷(prefixが変わるのみ・teardownのSQLはprefixパラメータ化済み) |
| `backend/tests/unit/test_rate_limit_wiring.py` | 触らない | **無傷**。ルート追加なし(案Xは既存 `/v1/reports` のbody拡張のみ・ルート数は不変) |
| `backend/tests/unit/safety/test_safety_cache.py`・`test_safety_store_sql.py` | 触らない | 無傷。cache・storeは本単位で完全無変更 |
| `backend/tests/unit/latches/*`・`tests/integration/test_chat_attendance_api.py` 他 | 触らない | 無傷。latches/users/intents/notifications系は一切変更しない |
| `frontend/tests/` の既存9ファイル(81件) | 触らない | **無傷(指示(4)の根拠)**: 既存9ファイルは ①既存モジュール(`src/api/client.js`・`session.js`・`src/intent/`6モジュール・`src/ui/chrome.js` — いずれも§5で完全無変更)と ②`index.html` のID群(smoke.test.js・IDはTask 13の変更指定で全て維持) のみを読む。新規モジュールと新規テストは既存のimport graphに加わらないため、構造的に既存81件へ影響する経路が存在しない |

### 9-3. DB残存干渉の対抗策(共有ci-db・指示(3))

- **subjectプレフィックスは `m3ws7-` へ変更する**(Task 3・1行)。design §4・§6-5は残存確認のprefixを規定していなかったため、本計画が次のとおり確定する: ①integration追記(test_10)は既存の `field` fixtureをそのまま使う(重複実装しない)②fixtureの `SUBJECT_PREFIX` 定数を `m3ws7-` へ変更することで、**追記試験を含むファイル全体の行**の残存確認が§7のSQL(`m3ws7-`)と1:1で対応する ③teardownは既存のFK順削除(reports→messages→…→users)がtest_10の行も掃除する(test_10が使うテーブルはusers/intents/latches/reportsのみで既存カバー内)④Redisキー(`blk:u:`)の掃除も既存fixtureのまま有効
- ws-5検証時の `m3ws5-` 残存はゼロ確認済み(2026-10-01・STATUS)のためprefix変更による旧データ混乱なし
- **時間値はすべてnow相対**(既存 `_latch` ヘルパーが `SystemClock().now()` 起点・タイムボム回避)。frontend試験は実DBを消費しない(vitest+happy-dom+モック)
- **head不変(0006)**のため運用ル則1の「番号取り合い」は発生しない。ws-6とのtest-ci同時実行は§0規律(実行しない)で回避する

### 9-4. 試験数の整合(指示・現行基準値との突合)

| 種別 | main基準(2026-10-01スーパーバイザー指示値) | 本単位増分 | 期待合計 |
|---|---|---|---|
| backend unit(`make test`) | 1188 passed | **+8**(test_safety_service.py 6=Task 1・test_safety_routes.py 2=Task 2) | **1196 passed** |
| backend test-ci(unit+integration) | 1427 passed | **+9**(unit 8+integration 1=Task 3) | **1436 passed**(スーパーバイザー検証時・ws-6並走分は別加算) |
| frontend `npm test` | 81 passed(9ファイル) | **+86**(10ファイル: latch-view 20=Task 4・router 6=Task 5・app-state 3=Task 6・latch-respond 8=Task 7・latch-chat 9=Task 8・latch-attendance 6=Task 9・latch-report 7=Task 10・latch-home 10=Task 11・latch-detail 11=Task 12・latch-shell 6=Task 13) | **167 passed** |

各TaskのStep 4に通過時点の累積期待値を記載済み(Task 4後101→Task 13後167)。design §4の目安「frontend新規60〜80件」に対し86件はやや上回るが、view.js純関数の境界ケース(残時間7区切り・不成立二値化)を含むための増分であり、designの試験方針(§4)の全項目をピン留めするために必要な件数である。

### 9-5. スペックカバレッジ・型整合(design §1.2の確定値→タスク対応・書き起こし後の点検)

- 引用#1(ホーム5要素)→ Task 13(index.htmlのtopbar popover・ドット温存)+Task 11(2セクション・Intent N件)
- 引用#2(candidateを出さない)→ backend実装済み(`_SELECT_LATCHES_PAGE` の `status <> 'candidate'`)・フロントは追加対応不要(消費しない)
- 引用#3(入力画面起点・5要素最低構成)→ Task 13(workspace無変更+下段2セクション)
- 引用#4(提案詳細の必須構成5点)→ Task 4(conditionSummaryLines・matchLevelText・remainingTimeText・groupNeedText)+Task 12(renderProposal)
- 引用#5(hidden_until_match分岐)→ Task 4(isMinimalProposal)+Task 12(最小形)+Task 11(カードの最小形)
- 引用#6(match_level区分)→ Task 4(MATCH_LEVEL_TEXT・生スコアは表示しない)
- 引用#7(FR-46・タップは詳細画面へ)→ Task 5(`#/latches/{uuid}`)+Task 11(カードはリンク)
- 引用#8(matchedで解放するもの)→ Task 12(renderMatched: participants・集合情報・次アクション・チャット・completedの閲覧専用=Task 8)
- 引用#9(不成立二値化・終了済み扱い)→ Task 4(closedText)+Task 12(renderClosed)+Task 11(splitStatusesの非表示)
- 引用#10(通報4値・両画面から常に可能)→ Task 10+Task 12(reportEntryHtml)+backend案X Task 1〜3
- 引用#11(D-23文言)→ Task 8(CHAT_READONLY→CHAT_UNAVAILABLE_TEXT)+Task 4(定数ピン)
- 引用#12(D-09)→ Task 9+Task 12(completedのみ表示)※窓判定は§9-6①
- 引用#13(チャット書込matchedのみ・本文1〜1000字)→ Task 8(maxLength=1000・trimしない・422表示)
- 引用#14(改頁共通規定・cursor不透明)→ Task 8(encodeURIComponent+不透明扱い)+Task 11(loadMore)
- 引用#15(回答の409分岐)→ Task 7(3codeすべて再取得)+Task 12(refresh)
- 引用#16(404/403)→ Task 12(renderNotFound)
- 引用#17(プロトタイプ準拠)→ Task 13(既存クラス無変更・デザインシステム変数のみ新規CSSで使用)
- 引用#18(デザインシステム)→ Task 13(styles.css追記・3ブレークポイント・reduced-motion)
- 引用#19(intents一覧・users/me)→ Task 11(loadIntents)+Task 6(ensureMe)
- 引用#20(G3関連#13〜#23)→ #13=Task 11+12・#14=Task 7・#15/#17=Task 12・#19=Task 8・#21=Task 10+12・#22=Task 4+12(条件サマリ分岐・raw_text・NG条件・座標は組まない)・#23=表示項目に座標精度なし(conditionSummaryLinesが座標を含まない)
- design §2.7(案X)→ Task 1〜3(backend)+Task 10(省略body)+Task 12(導線条件)
- design §2.8(texts集約)→ Task 4
- 型整合点検: `createRespondFlow({client, latchId, refresh, showError})`(Task 7)はTask 12の呼び出しと一致・`createChat({client, latch, meId, mount})`(Task 8)はTask 12の呼び出しと一致(latch=LatchDetail・meId=appState.ensureMeのid)・`createAttendanceFlow({client, latchId, mount})`(Task 9)はTask 12と一致・`reportFlow.open({latchId, participants, meId})`(Task 10)はTask 12の呼び出しと一致・`createHome({client, el})` のel 5要素(Task 11)はTask 13のmain.jsのel指定と一致・`createRouter({screens, onRoute})`(Task 5)はTask 13と一致・`splitStatuses`/`latchCardHtml`(Task 11)の戻り値はTask 11のテストと一致。モック応答形式は§2-1のAPI応答型どおり(`{"items","next_cursor"}`・`{"latch"}`・`{"message"}`・`{report_id}`・users/meフラット・IntentOutのstructured_intent)。プレースホルダなし — 全コードステップに実コード記載(Task 13のscreen.jsのみ「main.jsからの切り出し」として切り出し元の行範囲と調整点を正確に指定・対象コードはrepo内に実在)

### 9-6. 未解決論点の確認と適合措置

design §5-1(案X)は2026-10-01 supervisor裁定で確定済み・§5-2はws-8への引継ぎ(確定事項)。**本計画に落とし込めない未解決論点はなく、BLOCKED事項なし。**

計画書で確定した適合措置は2件(いずれもdesignの意図を保った実装調整・報告書の「固定値の変更有無」に記録):

1. **attendanceの表示条件(§2-2)**: design §2.7は「completed_atから3日以内のとき」表示とするが、**詳細応答の`completed_at`はws-1実装(`_page_view_of`がNoneを渡す)により常にnull**のため日時比較が実装不能。backendのlatches/service.pyは本単位の変更禁止(supervisor指示: safetyのみ)であるため、表示条件は**status=completedのみ**とし、3日窓(409 ATTENDANCE_WINDOW_CLOSEDで非表示化)と二重回答(409 ALREADY_SUBMITTEDで回答済み表示)は**design自身が規定する409経路**へ一本化する。機能は等価(窓切れ後に質問が見え、押下で非表示化する差のみ)。latches/service.pyのcompleted_at修正はG3/ws-9への引継ぎとして報告書に記録する
2. **integrationのsubjectプレフィックス(§9-3)**: `m3ws5-`→`m3ws7-`への1行変更(残存確認の1:1対応のため・design §4はprefixを規定していなかったため本計画が確定)

追加の設計判断(designが規定なく本計画が確定した文言・§2-2に集約・designの範囲内): 成立済みセクションの空状態文言「成立したLATCHはまだありません / 成立すると、ここに並びます。」・残時間1分未満「まもなく締切」・分0/時間0の省略書式・通報導線の文言(「この提案を通報する」「このLATCHを通報する」)・完了チャット文言(design §2.6に規定済み)。
