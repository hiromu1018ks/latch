# E2Eカバレッジマトリクス(LATCH)

- 生成: 2026-10-12 — e2e-coverage-architect Phase 0(インベントリ)
- 対象: リポジトリルート `/home/misty/Projects/latch`(commit aaa23c6 時点・clean tree)
- 本書の役割: インベントリ(§1)→ 振る舞い行列(§2・Phase 1)→ ギャップ(§3・Phase 2)の正本。優先度つきギャップは `gaps.md`、発見事項は `findings.md` が持つ
- 前提ルール: 本分析はアプリケーションソースを変更しない。触れるのはテストコード・テスト設定・`docs/e2e-coverage/` 配下のみ

---

## 1. インベントリ(Phase 0)

### 1.1 スタックとサービス構成

Monorepo(3実装領域)。**同一API契約の2実装が存在**する点が本分析の前提条件:

| 領域 | スタック | 役割 |
|---|---|---|
| `frontend/` | React 19 + Vite 6.4.2 SPA(自作hashルーター・`frontend/src/router.js`)・vitest 3 + RTL + happy-dom | UI。全API同一オリジン相対 `/v1/...`。CORSなし |
| `backend/` | Python 3.13 + FastAPI + PostgreSQL 17(PostGIS+pgvector) + Redis + Pub/Sub(エミュレータ) + worker(uvicorn) | **参照実装**。compose常設ci環境(`make up`)で api=127.0.0.1:8000 |
| `cloudflare/api/` | Cloudflare Workers(python_workers)+ D1 + Queues + Workers Assets(`frontend/dist/client` を同オリジン配信) | **Cloudflare実装**。`make cf-dev` で miniflare=127.0.0.1:8787(単一オリジン)。実配備の経路 |

- 2実装のAPIパリティは `backend/tests/contracts/`(参照133 / Cloudflare 130ケース)が保証済み → **UI E2Eは片方のオリジンで実施すればAPI契約面はもう片方も担保されている**構図
- マッチング(候補→提案形成)はworkerがPub/Sub経由で非同期処理(debounce 10s)。同期APIだけではlatchは生成されない

### 1.2 起動方法(2026-10-12 実機検証済み)

**主目標(compose + vite dev — PlaywrightのwebServerに接続済み)**

| 手順 | コマンド | 検証結果 |
|---|---|---|
| 1. API常設環境 | `make up`(repo root) | 全5コンテナhealthy(db/redis/pubsub/api/worker)✅ |
| 2. マイグレーション | `make migrate` | alembic head適用 ✅(起動時自動適用はない・明示実行の規律) |
| 3. API到達 | `GET http://127.0.0.1:8000/health` | `{"status":"ok","server_time":...}` ✅ / 未認証APIは401 `UNAUTHENTICATED` ✅ |
| 4. フロント | `cd frontend && npm run dev`(=Playwright webServerが起動) | `http://127.0.0.1:5173/` 200 ✅(`/v1` を8000へproxy・`frontend/vite.config.mjs:12-14`) |

**代替目標(Cloudflare実装・単一オリジン)**

`make cf-dev`(別ターロングラン)→ `http://127.0.0.1:8787`。Workers Assets配信のbuild済みフロント+API+ローカルD1。提案形成を動かすには `--var LATCH_LLM_STUB_JEV_NOUL=0.85` が必要。ローカルD1へのmigration手動適用が別途必要(docs/plans/CLOUDFLARE-MIGRATION-STATUS.md)。**Phase 3で第二projectとして追加可能。現時点では主目標のみ接続**

### 1.3 Playwright(ブートストラップ記録)

**導入前の状態**: Playwright/Cypress/Puppeteer なし。**UI自動E2Eはゼロ**。コミットログの「実機E2E」は手動の実ブラウザ検証である(`docs/plans/ui-v2/phase7-report.md` §3・26操作表。「chrome検査用Probe」はvitest内のReactコンテキストcapture部品でブラウザは無関係)

**今回追加したファイル(スキルPhase 0ブートストラップ)**:

| ファイル | 内容 |
|---|---|
| `frontend/package.json` / `package-lock.json` | `@playwright/test` をdevDependenciesへ追加 |
| `frontend/playwright.config.mjs` | testDir=`tests/e2e`・baseURL=5173・webServer=`npm run dev`(reuseExistingServer)・chromium単一・trace=retain-on-failure |
| `frontend/tests/e2e/` | スペック置き場(空・Phase 3から投入) |
| `.gitignore`(root) | `frontend/test-results/` `frontend/playwright-report/` の2行 |
| `~/.cache/ms-playwright/` | chromium + chromium-headless-shell を`npx playwright install chromium`で導入 |

vitestのincludeは `tests/**/*.test.{js,jsx}`(`frontend/vitest.config.mjs`)で、Playwrightの `*.spec.js` と衝突しない。

**Baseline(2026-10-12)**: `npx playwright test --pass-with-no-tests` → exit 0。スペック0件・前提失敗なし。既存E2E suiteが存在しないため記録すべき既存赤はなし

### 1.4 画面ルート(frontend SPA・hashルーター)

`parseHash()`(`frontend/src/router.js:28-50`)。未知のhash・uuid形式不正はすべてhomeへfall-through。

| ルート | 画面 | 目的 |
|---|---|---|
| `#/` | 01 預ける(HomeScreen) | composer入力→parse結果の下書き・待機中の意思カード・下書き保存 |
| `#/confirm[?intent={uuid}]` | 02 読み解き確認(ConfirmScreen) | parse結果の確認・5条件行編集・人数seg・期限3択・保存。`?intent=`は03「条件を直す」編集モード(GET復元→PATCH) |
| `#/standby[?deposited=1]` | 03 待機中(StandbyScreen) | 残時間タイマー・条件を直す・取り下げdialog・下書き行。`deposited=1`は02からの着地 |
| `#/proposals` | 簡易一覧・候補タブ(ListsScreen) | proposed/partial_acceptなlatch一覧(D-11) |
| `#/matched` | 簡易一覧・成立タブ(ListsScreen) | matched/completedなlatch一覧 |
| `#/latches/{uuid}` | 04候補/05成立(DetailScreen) | 1画面3姿(提案・成立・チャット)。回答2択・undo・.ics・leave・attendance・通報・ブロック |
| `#/settings` | 設定(SettingsScreen) | 通知許可・ブロック管理・LLM同意の3セクション |
| `#/onboarding` | LLM同意オンボーディング | 未同意時に誘導(起動時+422 CONSENT_REQUIRED検出時の2経路) |

navは「預ける/待機中/候補/成立」4項目+バッジ(counts)。720px以下は下部tabbar。主な画面側遷移: 02でstate空→01へ戻す・`#/latches/{id}`の404/403→「提案が見つかりません」+home導線・未同意→`#/onboarding`・編集復元失敗→03へ。

### 1.5 APIエンドポイント(参照実装・`backend/src/latch/main.py:290-298`登録)

**ops(認証不要・レート制限なし)**: `GET /health`(server_time付き)・`GET /metrics`(参照実装のみ)

**公開(認証不要・auth系独自レート制限=provider+subject単位60/分)**:

| メソッド+パス | 目的 |
|---|---|
| POST /v1/auth/token | IdPトークン(google/apple)→アクセスJWT+リフレッシュ交換 |
| POST /v1/auth/refresh | リフレッシュ回転(30日) |

**認証必須(全てBearer JWT・API全体60req/分/user・401→429の順で構造保証)**:

| メソッド+パス | 目的 |
|---|---|
| POST /v1/auth/logout | 失効リスト+族失効・204冪等 |
| POST /v1/users | 初回登録(201)。18歳未満422 UNDER_AGE・重複409 |
| GET /v1/users/me | 本人プロファイル |
| DELETE /v1/users/me | 退会(204)・全Intentカスケード |
| POST /v1/users/me/consent | LLM同意記録(冪等・後勝ち) |
| GET /v1/users/me/consent | 同意状態(未同意は200 null) |
| POST /v1/intents/parse | 自然文→構造化プレビュー(保存しない・1-300字) |
| GET /v1/intents/expiry-options | 期限3択+default_index(未認証401を実測確認済み) |
| POST /v1/intents | Intent作成(201・draft/active)。active上限5・作成20/日 |
| GET /v1/intents | 自Intent一覧(status filter・cursor/limit) |
| GET /v1/intents/{intent_id} | 取得(他人は404) |
| PATCH /v1/intents/{intent_id} | 全置換更新(更新6/時) |
| DELETE /v1/intents/{intent_id} | 取り下げ(cancelled遷移・204) |
| POST /v1/intents/{intent_id}/pause | 一時停止 |
| POST /v1/intents/{intent_id}/resume | 再開 |
| GET /v1/latches | 本人関与latch一覧(candidate除外) |
| GET /v1/latches/{latch_id} | 詳細(04/05切替の供給源) |
| POST /v1/latches/{latch_id}/response | 回答(yes/decline+reasons) |
| POST /v1/latches/{latch_id}/response/undo | 回答取り消し(D-06) |
| POST /v1/latches/{latch_id}/leave | 参加のみ取り消し(D-08) |
| POST /v1/latches/{latch_id}/messages | チャット投稿(matchedのみ・201) |
| GET /v1/latches/{latch_id}/messages | チャット一覧(閲覧は状態不問) |
| POST /v1/latches/{latch_id}/attendance | 実施自己申告(D-09・3日以内・初回のみ) |
| GET /v1/notifications | お知らせ一覧 |
| POST /v1/notifications/{notification_id}/read | 既読(204冪等) |
| GET /v1/users/me/blocks | ブロック一覧 |
| POST /v1/users/{user_id}/block | ブロック登録(201冪等) |
| DELETE /v1/users/{user_id}/block | 解除(204・行なし404) |
| POST /v1/reports | 通報(201・受付のみ) |
| POST /v1/sessions | アクセス計測(204。**参照実装のみ**) |

Cloudflare実装の差分: `/health`がstatusのみ・`/v1/sessions`と`/metrics`なし・`/probe/*`検証ルート群あり(非本体API)。

### 1.6 認証モデル

- **仕組み**: IdPトークン(RS256・JWKS検証)→ `POST /v1/auth/token` で独自JWT(HS256・1時間)と回転式リフレッシュ(30日)へ交換。**自動登録なし** — users表に無ければ `profile_complete=false` で通り、明示 `POST /v1/users` で登録
- **同意ゲート**: 未同意ユーザーのparse/active Intent作成は422 `CONSENT_REQUIRED`(フロントは横断検出で`#/onboarding`へ誘導)
- **未認証応答**: 401 + `{"error":{"code":"UNAUTHENTICATED",...}}`(欠落/無効/期限切れ/失効すべて同形)。フロントはrefresh 1回試行→失敗でsession破棄+トークンパネル再表示
- **ロール/権限**: **存在しない**(全ユーザー同権)。所有権は本人のprovider+subject一致のみ(他人は404 NOT_FOUND/403 FORBIDDEN)
- **開発用トークン**: `cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject <任意>`(同梱staging鍵で署名・API検証設定と自動一致)。**E2Eでの認証はこのCLI→UIトークンパネル入力、またはsessionStorage `latch-access` / localStorage `latch-refresh` 直置き**(access=sessionStorage・refresh=localStorage・`frontend/src/api/session.js:4-5`)
- 正規ユーザー作成のフルチェーン: CLI発行→auth/token→POST /v1/users→consent

### 1.7 データ・永続化・シード

- **ストレージ**: PostgreSQL(PostGIS+pgvector必須)。マイグレーション0001〜0009(users/intents/match_candidates/group_candidates/latches/match_events/messages/blocks/reports/notifications/calibration_records/geofeatures/sessions/kpi_*等)。Redis=セッション・レート制限・Clock。Pub/Sub+worker=マッチングパイプライン
- **起動時シード: なし**。地物(geofeatures)も `make geo-import` が明示必要
- **ci DBの現在の実態(2026-10-12確認)**: users=53行(残存試験データ・subjectプレフィックスm2ws6-/t0c-等)。geofeatures=7行(天文館・伊敷町・大字草牟田・バー宵待・城山ホテル等・鹿児島市周辺=integration/contracts試験のseed由来)。**→ Intent作成E2Eの地名はこの7地物の範囲で書く必要がある**(範囲外は422 GEOCODING_FAILED)
- **リセット戦略: なし**(TRUNCATEしない運用)。既存試験は一意subjectプレフィックスで分離+teardown削除。**E2Eも同様に「自己データ作成・一意プレフィックス・共有状態をassertしない」設計が必須**
- **レート制限(設計制約)**: API全体60req/分/user・Intent作成20/日・Intent更新6/時・active Intent上限5件。並列workerで同一ユーザーを叩くと429になる → **E2Eはテストごとに別ユーザー(別subject)を払い出す**
- **worker非同期性**: Intent保存→publish→debounce 10s→パイプライン→latch生成。UI E2Eで04/05画面を開くにはlatchをAPI/DB側で直接作る(契約harness `reference_harness.py` のactor provisioningが参照実装)か、残存データのuuidを利用する。**Phase 3でlatch fixture戦略を決定**
- **時刻**: 全時刻Clock経由(FR-41)。JST基準の表示・期限が多く、フロント試験はfake timersで固定済み。E2Eでは実時間で動く(apiのserver_timeはUTC)

### 1.8 既存テストカタログ

**frontend(vitest・367件・実APIに触れないfetchモック全)** — 28ファイル:

| ファイル | カバー内容(1行) |
|---|---|
| router.test.js | parseHash全ルート解析(未知hash→home・uuid検証)・旧createRouter |
| client.test.js | APIクライアント: Bearer・envelope→ApiError・401→refresh再送/clear |
| session.test.js | トークン保存(access=sessionStorage/refresh=localStorage)・exchange |
| app-state.test.js | ensureMeキャッシュ・reset |
| smoke.test.js | index.html/styles静的検査+App描画スモーク・全ルートsection存在 |
| state.test.js | フォーム状態: 必須欠落・applyParseResult・人数確認D-04 |
| parseFlow.test.js | parse送信制御: 1秒debounce・同一テキスト不送・abort |
| save.test.js | 保存flow: 事前ブロック・errorPlacement(422/429/503)・二重送信防止 |
| format.test.js | 表示書式純関数: 時刻/場所/人数/予算・buildCreateRequest |
| highlight.test.js | 02原文ハイライト: 5条件マッチ・重なり優先 |
| expiry.test.js | 期限select: fetchExpiryOptions・選択肢構成 |
| conditions.test.jsx | 02の5行(SlotRows)構成・行編集書き戻し |
| confirm.test.jsx | 02画面: 保存POST/PATCH(編集)・D-04・GEOCODING_FAILED・空state戻し |
| latch-view.test.js | view.js純関数: latchMode 3姿・残時間書式・照合表 |
| latch-respond.test.js | 回答flow: yes/defer・undo・409収束・429文言 |
| latch-calendar.test.js | .ics生成(RFC5545) |
| latch-home.test.jsx | 01待機中カード・splitStatuses・バッジ |
| latch-standby.test.jsx | 03画面: タイマー・取り下げdialog・着地・下書きD-13 |
| latch-lists.test.jsx | D-11一覧: 行様式・空状態・追頁 |
| latch-detail.test.jsx | 04提案姿(4状態・undo・defer)と05成立姿(ヒーロー・カレンダー・leave・attendance・チャット)・404/403(640行・最大) |
| latch-chat.test.jsx | 05チャット: ページング・30秒ポーリング・読取専用・XSS |
| latch-notice.test.jsx | お知らせ: type×status行列・既読化・追頁 |
| latch-safety.test.jsx | 通報・ブロック統合dialog: 対象ラジオ・理由ステップ |
| latch-attendance.test.jsx | 実施自己申告: 2択・409二種 |
| latch-blocks.test.jsx | ブロック管理: 一覧・解除・404収束 |
| latch-consent.test.jsx | LLM同意: ConsentCard・consentGate誘導2経路 |
| latch-settings.test.jsx | 設定画面: PermissionControl 4状態 |
| latch-shell.test.jsx | シェル: 条件マウント・popover・トースト・a11y |

**backend(pytest)**:

| 種別 | 内容 |
|---|---|
| tests/unit(毎コミット・DB不要) | auth/intents/latches/llm/matching/ratelimit/safety/users/worker/geo/cost/obs等・約150ファイル。ASGI in-process+FakeClock+スタブ |
| tests/integration(compose常設前提・marker=integration) | API実証: 認証3endpoint・CRUD・latches応答系17・チャット+attendance・safety 9・レート制限4種・マッチング各層・G3受け入れE2E・縮退E2E等 |
| tests/contracts | **HTTP契約の正本**: 参照実装133ケース(test_api_parity)+ Cloudflare130(test_cloudflare_parity)。13領域のroute・status・error code・応答形状を固定 |
| tests/cloudflare | Workers/D1/Queues資産の単体+probe(cf-unit / cf-probe=実測8787) |
| staging_tests*(testpaths外・明示実行) | M4受け入れハーネス(S0〜S5機能・obs・NFR)。nginx:18000経由の実API全試験 |

**手動実機E2E(証跡のみ・自動化コードなし)**: `docs/plans/ui-v2/phase7-report.md` §3の26操作(01入力→02→03→04の4状態→05チャット/leave/attendance/通報→390px)。持ち帰りとして「attendance二重回答のUI再POST経路・safety 429・popover既読化」が実機未再確認(phase7-report L93-96)= **E2Eギャップ候補**

### 1.9 E2E設計への制約(要約)

1. レート制限60req/分/user → テストごとに別subjectのユーザー払い出し。429が観測されたらテスト側の設計ミス
2. ci DBはリセットされない(残存users 53行)→ 一意プレフィックス+自己データのみassert
3. Intent作成の地名は既存7地物(天文館等)の範囲で
4. latch(04/05画面)は同期APIで作れない → fixture戦略が必要(API直作成 or 残存利用)
5. 未同意ユーザーはparse/保存が422 → ユーザー払い出し時にconsentまで実施
6. 時刻はJST依存表示(「今日 22:00」等)→ アサーションは時刻非依存で書くか許容幅を持たせる
7. アクセストークン1時間・E2E実行中の切れは実質なし(1suite<1分)

---

## Phase 0 完了チェックリスト

- [x] 起動コマンド既知・実機検証済み(make up+migrate+health 200+vite 200・2026-10-12)
- [x] Playwright存在(ブートストラップ実施)・baseline記録(スペック0件・exit 0)
- [x] 全画面(8ルート)と全エンドポイント(v1 32+ops 2)を認証要件付きで列挙
- [x] 認証モデルとロール(なし=全ユーザー同権)を文書化
- [x] データ・永続化・シード(なし・ci DB実態・リセット戦略なし)を文書化
- [x] 既存テストのカタログ(frontend 29+backend 5種+手動E2E証跡)

## 2. 振る舞い行列(Phase 1 — 2026-10-12)

### 2.1 Actor定義(ロールは存在しないため状態別)

| Actor | 意味 |
|---|---|
| 訪問者 | トークンなしで開いた未認証ブラウザ(開発用トークンパネルが出る) |
| 未登録者 | IdPトークン交換済みだが users 行なし(**登録UIは存在せずAPI専用**のため通常到達しない) |
| 未同意ユーザー | 登録済み・LLM同意なし(#/onboarding誘導対象) |
| ユーザー | 登録済み・同意済みの標準actor(全ユーザー同権) |
| 他ユーザー | 横断アクセス検証用のもう一人のユーザー |

### 2.2 振る舞い一覧(actor + action + 適用次元 + リスク)

次元は happy / validation / authz / error / state / persist の6種。`n/a` 判断は行列(§2.3)に反映済み。

**A. 認証・セッション**

| # | 振る舞い | 適用次元 | リスク注記 |
|---|---|---|---|
| A1 | 訪問者が任意画面を開く → シェルは描画・APIは401で開発用トークンパネル表示 | state | 低(開発導線。本番IdP導線の代替入口) |
| A2 | 訪問者が有効IdPトークンをパネルに入力し接続 → 交換成功・パネル閉鎖・データ表示 | happy | 中(入口が壊れると全機能到達不能) |
| A3 | 無効IdPトークンで接続 → 「トークンが無効です…」再入力促し | validation | 低 |
| A4 | API停止中に接続 → 「接続できませんでした…」 | error | 低 |
| A5 | ユーザーが再訪(access切れ・refresh保持) → 自動refreshでAPI継続 | happy, persist | 中(継続性。access期限1時間をE2Eで切らせる工夫が必要) |
| A6 | refresh無効/期限切れ → session破棄・パネル再表示 | error, state | 中 |
| A7 | 未登録者が画面を開く → 期待挙動未定義(登録UIなし・内部α段階) | state | 低(通常到達しない。要仕様確認→waive候補) |

**B. LLM同意**

| # | 振る舞い | 適用次元 | リスク注記 |
|---|---|---|---|
| B1 | 未同意ユーザーがアプリを開く → #/onboardingへ誘導 | state | 高(コンプライアンスゲート) |
| B2 | 未同意ユーザーがparse/保存を試みる → 422 CONSENT_REQUIRED横断検出→トースト+誘導 | error, state | 高 |
| B3 | 未同意ユーザーがオンボーディングで同意する → POST同意→通常画面へ | happy, persist | 高 |

**C. 01 預ける(ホーム)**

| # | 振る舞い | 適用次元 | リスク注記 |
|---|---|---|---|
| C1 | ユーザーが条件文を入力 → 1秒debounceでparse→読み取りchips・「読み解く」CTA有効化 | happy | 最高(サービス主幹の入口) |
| C2 | 空テキスト/300字超 → CTA無効・送信なし | validation | 低 |
| C3 | parse失敗(429/503) → 専用文言・retryable表示 | error | 中 |
| C4 | 例文chip選択 → textareaへ挿入 | happy | 低 |
| C5 | 下書き保存 → POST draft→トースト・二重送信防止 | happy, persist | 中 |
| C6 | 待機中の意思カード表示(active) | happy | 中(読取) |
| C7 | 候補対応づけ確定時の強調カード+04直結導線 | happy, state | 中 |

**D. 02 読み解き確認**

| # | 振る舞い | 適用次元 | リスク注記 |
|---|---|---|---|
| D1 | parse結果表示(原文ハイライト・5条件行) | happy | 高(誤表示=誤条件で公開) |
| D2 | 5条件の行編集 → 行テキスト解析で書き戻し | happy, validation | 高 |
| D3 | 人数seg変更(D-04 +1換算) | happy | 中 |
| D4 | 期限3択取得・選択(expiry-options) | happy | 高(期限誤り=誤マッチ) |
| D5 | 「預ける」保存 → POST active→03着地(deposited=1) | happy, state, persist | 最高 |
| D6 | 保存422 GEOCODING_FAILED → 場所行エラー表示 | validation | 高 |
| D7 | 保存429/503 → 文言・再試行 | error | 中 |
| D8 | 編集モード(#/confirm?intent=) → GET復元→PATCH | happy, state, persist | 高 |
| D9 | 編集復元404 → 03へ戻す | error, state | 低 |
| D10 | 02直接訪問(state空) → 01へ戻す | state | 低 |

**E. 03 待機中**

| # | 振る舞い | 適用次元 | リスク注記 |
|---|---|---|---|
| E1 | メイン条件カード表示・残時間タイマー1秒更新 | happy | 中(読取) |
| E2 | 「条件を直す」→ #/confirm?intent=へ | state | 中 |
| E3 | 「取り下げる」→dialog確認→DELETE → カード消滅・バッジ更新 | happy, state, persist | 高(意思の撤回) |
| E4 | 取り下げdialogキャンセル → 状態保持 | validation | 低(破壊操作の誤発動防止) |
| E5 | 下書き行からの編集導線(draft→confirm) | state | 低 |
| E6 | 候補トースト表示(右上) | happy | 低 |

**F. 簡易一覧(D-11)**

| # | 振る舞い | 適用次元 | リスク注記 |
|---|---|---|---|
| F1 | #/proposals 候補一覧・空状態 | happy | 中 |
| F2 | #/matched 成立一覧・空状態 | happy | 中 |
| F3 | 行クリック → #/latches/{id} 導線 | state | 中 |
| F4 | 追頁(cursor) | happy | 低 |

**G. 04 候補(提案姿)**

| # | 振る舞い | 適用次元 | リスク注記 |
|---|---|---|---|
| G1 | 提案表示(4情報カード・照合表・回答期限timer) | happy | 最高(提案内容の正しい可視化) |
| G2 | YES回答 → joined/allIn状態遷移 | happy, state, persist | 最高(相互YES=サービス核心) |
| G3 | my_response=no(辞退)の受信表示(現行の回答2択はYES/見送りのみ・D-05で辞退導線は廃止) | happy | 中(Phase 2で行定義を現行仕様へ修正) |
| G4 | 見送り(defer+理由chips) → 見送り表示 | happy, state, persist | 中 |
| G5 | undo → open復帰 | happy, state | 中 |
| G6 | undo 409(期限切れ) → 収束表示 | error | 低 |
| G7 | 他ユーザーのlatch URL直叩き → 404「提案が見つかりません」+home導線 | authz | 最高(横断アクセス防止) |
| G8 | 不正uuid→home戻し/整形式だが存在しないlatch→404表示 | error | 低 |

**H. 05 成立(成立姿)**

| # | 振る舞い | 適用次元 | リスク注記 |
|---|---|---|---|
| H1 | 成立ヒーロー・メンバー・内容表示 | happy | 高 |
| H2 | .icsカレンダーダウンロード | happy | 低 |
| H3 | チャット送信 → 投稿反映 | happy, persist | 高(成立後の唯一の対話手段) |
| H4 | チャット読取専用(3形態) | state | 中 |
| H5 | チャット30秒ポーリング・追頁 | happy | 低 |
| H6 | leave(参加取り消し) → 確認→POST→状態変化 | happy, state, persist | 高 |
| H7 | attendance自己申告 → 済み表示 | happy, persist | 中 |
| H8 | attendance 409(二重) → 収束 | error | 低 |
| H9 | 通報 dialog(対象選択→理由)→POST | happy | 高(安全機能) |
| H10 | ブロック dialog → POST | happy, persist | 高(安全機能。一覧での確認はI3) |

**I. 設定**

| # | 振る舞い | 適用次元 | リスク注記 |
|---|---|---|---|
| I1 | 3セクション表示(通知許可/ブロック/LLM同意) | happy | 低 |
| I2 | 通知許可トグル(権限4状態) | state | 低 |
| I3 | ブロック一覧・解除(204→行除去・404収束) | happy, error, persist | 中 |
| I4 | LLM同意状態の表示 | happy | 低 |

**J. シェル共通**

| # | 振る舞い | 適用次元 | リスク注記 |
|---|---|---|---|
| J1 | nav4項目・aria-current・countsバッジ | happy | 低 |
| J2 | 未知ルート → homeへ | state | 低 |
| J3 | ≤720pxでtabbar表示(実装は720pxブレークポイント・「390px」は実装に存在せず検証ビューポート名) | happy | 低 |
| J4 | お知らせpopover(一覧・既読化・導線) | happy, persist, state | 中 |

**意図的な重複・統合の記録**: B4(設定での同意表示)はI4へ統合。429共通文言は各画面行(C3/D7/G6/H8等)で観測するため独立行なし。pause/resume・logout・登録(POST /v1/users)は**UIが存在しない**ため行なし(API契約は契約試験が担保)。

### 2.3 Behavior × Dimension matrix — BEFORE(Phase 2で証拠マッピング済み・2026-10-12)

セル: `✅(file:line)`=アサーションが結果を証明 / `◐(file:line)`=partial(不足点はgaps.md) / `—`=未カバー / `n/a`=不適用。証拠は frontend/tests/ 基準の相対パス。

| # | 振る舞い(短縮名) | happy | validation | authz | error | state | persist |
|---|---|---|---|---|---|---|---|
| A1 | 訪問者→トークンパネル | n/a | n/a | n/a | n/a | ✅ smoke.test.js:159-165, latch-shell.test.jsx:122-131 | n/a |
| A2 | 有効トークン接続 | ◐ session.test.js:40-47 | n/a | n/a | n/a | n/a | n/a(A5) |
| A3 | 無効トークン接続 | n/a | ◐ session.test.js:49-59 | n/a | n/a | n/a | n/a |
| A4 | API停止中接続 | n/a | n/a | n/a | — | n/a | n/a |
| A5 | 再訪・自動refresh | ◐ client.test.js:65-80 | n/a | n/a | n/a | n/a | ◐ client.test.js:65-80 |
| A6 | refresh失効→パネル | n/a | n/a | n/a | ◐ client.test.js:82-98 | ◐ client.test.js:82-98 | n/a |
| A7 | 未登録者の挙動 | n/a | n/a | n/a | n/a | —(期待挙動未定義) | n/a |
| B1 | 未同意→onboarding誘導 | n/a | n/a | n/a | n/a | ◐ latch-consent.test.jsx:146-157 | n/a |
| B2 | 422 CONSENT_REQUIRED検出 | n/a | n/a | n/a | ◐ latch-consent.test.jsx:159-164 | ◐ latch-consent.test.jsx:159-164 | n/a |
| B3 | オンボーディング同意 | ◐ latch-consent.test.jsx:102-114 | n/a | n/a | n/a | n/a | ◐ latch-consent.test.jsx:95-100 |
| C1 | 入力→parseプレビュー | ◐ parseFlow.test.js:22-43 | n/a | n/a | n/a | n/a | n/a(読取) |
| C2 | 空/300字超→CTA無効 | n/a | ◐ state.test.js:26-27,101-102 | n/a | n/a | n/a | n/a |
| C3 | parse失敗文言 | n/a | n/a | n/a | ◐ parseFlow.test.js:106-119 | n/a | n/a |
| C4 | 例文chip挿入 | ◐ smoke.test.js:129-132 | n/a | n/a | n/a | n/a | n/a(読取) |
| C5 | 下書き保存 | ◐ save.test.js:116-123 | n/a | n/a | n/a | n/a | ◐ save.test.js:116-123 |
| C6 | 待機中カード表示 | ✅ latch-home.test.jsx:170-191 | n/a | n/a | n/a | n/a | n/a(読取) |
| C7 | 候補強調+04直結 | ✅ latch-home.test.jsx:206-218 | n/a | n/a | n/a | ✅ latch-home.test.jsx:142-149 | n/a(読取) |
| D1 | parse結果表示 | ✅ highlight.test.js:29-41, confirm.test.jsx:81-93 | n/a | n/a | n/a | n/a | n/a(読取) |
| D2 | 5条件行編集 | ✅ conditions.test.jsx:211-273 | ✅ conditions.test.jsx:211-273 | n/a | n/a | n/a | n/a(読取) |
| D3 | 人数seg | ✅ conditions.test.jsx:150-169 | n/a | n/a | n/a | n/a | n/a |
| D4 | 期限3択 | ✅ expiry.test.js:22-80 | n/a | n/a | n/a | n/a | n/a(読取) |
| D5 | 預ける→03着地 | ✅ confirm.test.jsx:119-135 | n/a | n/a | n/a | ✅ confirm.test.jsx:122-135 | ◐ save.test.js:92-97 |
| D6 | GEOCODING_FAILED | n/a | ✅ save.test.js:45-48, confirm.test.jsx:219-225 | n/a | n/a | n/a | n/a |
| D7 | 保存429/503 | n/a | n/a | n/a | ◐ save.test.js:68-80 | n/a | n/a |
| D8 | 編集モード復元→PATCH | ✅ confirm.test.jsx:265-311 | n/a | n/a | n/a | ✅ confirm.test.jsx:265-276 | ◐ confirm.test.jsx:308-311 |
| D9 | 復元404→03戻し | n/a | n/a | n/a | ✅ confirm.test.jsx:315-318 | ✅ confirm.test.jsx:315-318 | n/a |
| D10 | 02直接訪問→01戻し | n/a | n/a | n/a | n/a | ✅ confirm.test.jsx:100-103 | n/a |
| E1 | メインカード+タイマー | ✅ latch-standby.test.jsx:85-101, 340-342 | n/a | n/a | n/a | n/a | n/a(読取) |
| E2 | 条件を直す導線 | n/a | n/a | n/a | n/a | ✅ latch-standby.test.jsx:97-99 | n/a |
| E3 | 取り下げ→バッジ更新 | ✅ latch-standby.test.jsx:148-170 | n/a | n/a | n/a | ✅ latch-standby.test.jsx:184-191 | ◐ latch-standby.test.jsx:371-378 |
| E4 | 取り下げキャンセル | n/a | ✅ latch-standby.test.jsx:366-369 | n/a | n/a | n/a | n/a |
| E5 | 下書き行編集導線 | n/a | n/a | n/a | n/a | ✅ latch-standby.test.jsx:269-274 | n/a |
| E6 | 候補トースト | ✅ latch-standby.test.jsx:387-401 | n/a | n/a | n/a | n/a | n/a |
| F1 | 候補一覧・空状態 | ✅ latch-lists.test.jsx:94-129 | n/a | n/a | n/a | n/a | n/a(読取) |
| F2 | 成立一覧・空状態 | ✅ latch-lists.test.jsx:111-140 | n/a | n/a | n/a | n/a | n/a(読取) |
| F3 | 行→詳細導線 | n/a | n/a | n/a | n/a | ✅ latch-lists.test.jsx:46-47 | n/a |
| F4 | 追頁 | ✅ latch-lists.test.jsx:148-161 | n/a | n/a | n/a | n/a | n/a |
| G1 | 提案表示 | ✅ latch-detail.test.jsx:118-167 | n/a | n/a | n/a | n/a | n/a(読取) |
| G2 | YES回答→allIn/joined | ✅ latch-detail.test.jsx:177-184 | n/a | n/a | n/a | ✅ latch-detail.test.jsx:302-313 | ✅ latch-detail.test.jsx:609-613 |
| G3 | 辞退(no)の受信表示 | ✅ latch-detail.test.jsx:381-385 | n/a | n/a | n/a | n/a(受信表示のみ) | n/a(読取) |
| G4 | 見送り+理由 | ✅ latch-detail.test.jsx:228-267 | n/a | n/a | n/a | ✅ latch-detail.test.jsx:228-231 | ✅ latch-detail.test.jsx:269-283 |
| G5 | undo→open復帰 | ◐ latch-respond.test.js:80-86 | n/a | n/a | n/a | ◐ latch-detail.test.jsx:199-202 | n/a |
| G6 | undo 409収束 | n/a | n/a | n/a | ✅ latch-detail.test.jsx:400-404 | n/a | n/a |
| G7 | 他ユーザーlatch URL→404 | n/a | n/a | ✅ latch-detail.test.jsx:411-420 | n/a | n/a | n/a |
| G8 | 不正uuid/存在なし | n/a | n/a | n/a | ✅ router.test.js:32-34, latch-detail.test.jsx:408-412 | n/a | n/a |
| H1 | 成立ヒーロー表示 | ✅ latch-detail.test.jsx:464-502 | n/a | n/a | n/a | n/a | n/a(読取) |
| H2 | .icsダウンロード | ✅ latch-detail.test.jsx:526-527, latch-calendar.test.js:20-54 | n/a | n/a | n/a | n/a | n/a |
| H3 | チャット送信 | ✅ latch-chat.test.jsx:141-148 | n/a | n/a | n/a | n/a | ✅ latch-chat.test.jsx:141-148 |
| H4 | チャット読取専用3形 | n/a | n/a | n/a | n/a | ✅ latch-chat.test.jsx:196-226 | n/a |
| H5 | ポーリング・追頁 | ✅ latch-chat.test.jsx:84-123 | n/a | n/a | n/a | n/a | n/a |
| H6 | leave | ✅ latch-detail.test.jsx:541-573 | n/a | n/a | n/a | ✅ latch-detail.test.jsx:563-573 | ◐(設計上H4①へ転嫁) |
| H7 | attendance申告 | ✅ latch-attendance.test.jsx:48-80 | n/a | n/a | n/a | n/a | ◐(設計上H8へ転嫁) |
| H8 | attendance 409 | n/a | n/a | n/a | ✅ latch-attendance.test.jsx:87-101 | n/a | n/a |
| H9 | 通報dialog→POST | ✅ latch-safety.test.jsx:96-111 | n/a | n/a | n/a | n/a | n/a(受付のみ) |
| H10 | ブロックdialog→POST | ✅ latch-safety.test.jsx:60-67 | n/a | n/a | n/a | n/a | ◐ latch-safety.test.jsx:72-73 |
| I1 | 設定3セクション | ✅ latch-settings.test.jsx:139-159 | n/a | n/a | n/a | n/a | n/a(読取) |
| I2 | 通知許可トグル4状態 | n/a | n/a | n/a | n/a | ✅ latch-settings.test.jsx:31-122 | n/a |
| I3 | ブロック一覧・解除 | ✅ latch-blocks.test.jsx:38-98 | n/a | n/a | ✅ latch-blocks.test.jsx:129-157 | n/a | ◐ latch-blocks.test.jsx:93-95 |
| I4 | 同意状態表示 | ✅ latch-consent.test.jsx:131-142 | n/a | n/a | n/a | n/a | n/a(読取) |
| J1 | nav・バッジ | ◐ latch-shell.test.jsx:49-66, 188-193 | n/a | n/a | n/a | n/a | n/a(読取) |
| J2 | 未知ルート→home | n/a | n/a | n/a | n/a | ✅ router.test.js:17-73 | n/a |
| J3 | ≤720px tabbar | ◐ smoke.test.js:208-213 | n/a | n/a | n/a | n/a | n/a |
| J4 | お知らせpopover | ◐ latch-notice.test.jsx:195-274 | n/a | n/a | n/a | ◐ latch-notice.test.jsx:239-256 | ◐ latch-notice.test.jsx:258-274 |

**集計(Phase 2時点)**: 適用セル91のうち ✅57 / ◐32 / —2(A4・A7)。ただし✅・◐はすべて**モックfetchのコンポーネント試験**としての証明であり、実ブラウザ×実API結合のE2E証明はゼロ(Playwright spec 0件)。E2Eレベルで価値のある結合ギャップは gaps.md にcluster化済み。

※ authz列が実質G7のみなのはロールが存在しないため(全ユーザー同権・横断は404で隠蔽)。未認証応答(401)はA1のstateで観測する。

### 2.4 Behavior × Dimension matrix — AFTER(Phase 3完了・2026-10-12)

BEFORE(§2.3)から変化したセルのみ記載(`implemented(spec:line)`=実E2Eで証明 / `known-failure(findings#N)`)。記載なきセルはBEFOREのまま(vitest証拠✅/n/a)。specは `frontend/tests/e2e/` 基準。

| # | 振る舞い(短縮名) | E2Eで終端化したセル |
|---|---|---|
| A1 | 訪問者→トークンパネル | state implemented(auth.spec.js:8) |
| A2 | 有効トークン接続 | happy implemented(auth.spec.js:14) |
| A3 | 無効トークン接続 | validation implemented(auth.spec.js:26) |
| A4 | API停止中接続 | error implemented(auth.spec.js:33) |
| A5 | 再訪・自動refresh | happy/persist implemented(auth.spec.js:43・findings#1修正済み) |
| A6 | refresh失効→パネル | error/state implemented(auth.spec.js:52) |
| B1 | 未同意→onboarding誘導 | state implemented(consent.spec.js:8) |
| B2 | 422 CONSENT_REQUIRED検出 | error/state implemented(consent.spec.js:16) |
| B3 | オンボーディング同意 | happy/persist implemented(consent.spec.js:31) |
| C1 | 入力→parseプレビュー | happy implemented(main-flow.spec.js:29) |
| C2 | 空/300字超→CTA無効 | validation implemented(main-flow.spec.js:10,15) |
| C3 | parse失敗文言 | error implemented(errors-and-settings.spec.js:8) |
| C4 | 例文chip挿入 | happy implemented(main-flow.spec.js:22) |
| C5 | 下書き保存 | happy/persist implemented(main-flow.spec.js:65) |
| D5 | 預ける→03着地 | persist implemented(main-flow.spec.js:29・リロード再取得で証明) |
| D7 | 保存429/503 | error implemented(errors-and-settings.spec.js:22・再試行込み) |
| D8 | 編集モード復元→PATCH | persist implemented(intent-lifecycle.spec.js:37) |
| E3 | 取り下げ→バッジ更新 | persist implemented(intent-lifecycle.spec.js:8) |
| G1 | 提案表示 | happy implemented(cross-user.spec.js:19・response.spec.js:8) |
| G2 | YES回答→allIn/joined | happy/state/persist implemented(response.spec.js:8,33) |
| G4 | 見送り+理由 | happy/state/persist implemented(response.spec.js:44) |
| G5 | undo→open復帰 | happy/state implemented(response.spec.js:19・FG-07の新規証明) |
| G7 | 他ユーザーlatch URL→404 | authz implemented(cross-user.spec.js:8・**P0**) |
| H2 | .icsダウンロード | happy implemented(chat.spec.js:30・実ダウンロード検証) |
| H3 | チャット送信 | happy/persist implemented(chat.spec.js:8・2コンテキスト) |
| H6 | leave | persist implemented(chat.spec.js:44・再訪readonly) |
| I3 | ブロック一覧・解除 | persist implemented(errors-and-settings.spec.js:79) |
| J1 | nav・バッジ | happy implemented(response.spec.js:71・実DOM非0件) |
| J3 | ≤720px tabbar | happy implemented(misc.spec.js:20) |
| J4 | お知らせpopover | happy/persist/state implemented(errors-and-settings.spec.js:58) |

**AFTER時点で open なセルはゼロ**(適用91= vitest✅ + E2E implemented + n/a。findings#1は2026-10-12修正済み・A5もimplemented)。

### Phase 1 完了チェックリスト

- [x] 全振る舞いがactor+actionの行を持つ(63行・§2.2)
- [x] 振る舞いごとにリスク注記(§2.2)
- [x] 近重複行の意図的な統合を記録(B4→I4・429文言の画面行分散・UIなしAPIの除外)
- [x] セルがPhase 2評価可能な状態(n/a判断済み・残りは—)

## 3. ギャップ分析(Phase 2 — 2026-10-12完了)

- マッピング方法: 4並列エージェントでvitest全29ファイルの**テスト本文**を読み、アサーション単位でセルへ証拠づけ(§2.3)。タイトルは証拠に使っていない
- 結果: 適用セル91のうち ✅57 / ◐32 / —2。ただし**すべてモックfetchコンポーネント試験**としての証明で、実ブラウザ×実APIのE2E証明はゼロ
- False-green 7件(FG-01〜07)・重複クラスタ10件・観察6件 → `findings.md`
- ギャップ: P0×1 / P1×7 / P2×9 / waived×4 → `gaps.md`(各行にテストアプローチ案つき)
- 行定義の修正: G3は現行仕様(D-05・回答2択=YES/見送り)へ・G8/J3表記を原本実装に合わせ修正

### Phase 2 exit checklist

- [x] 全cellにステータスと証拠(covered/partialはfile:line)
- [x] false-greenヒットを findings.md へ記録
- [x] gaps.md 優先度つき・重複除去・アプローチ案つき

## 4. 実装と再分析ループ(Phase 3/4 — 2026-10-12完了)

**Phase 3(実装)**: gaps.md のP0→P1→P2順に `frontend/tests/e2e/` へ実装(helpers.js+9spec・35テスト)。設計:

- ユーザー払い出しは契約harnessと同じ正規経路(IdP JWTをnode:cryptoでRS256署名→auth/token→users→consent)。1テスト=1ユーザー(レート制限60req/分/user対策)
- latch fixtureは契約harnessと同じ直接SQL INSERT(IntentはAPI作成・proposalはbuild_proposalのsummary_only全フィールド形)。workerパイプライン非依存=決定的
- 429系はpage.routeで応答注入(実APIをレート限界まで叩かない)
- afterAllでベストエフォート後片付け(latch系行SQL削除→ユーザー退会API)

**トリアージで修正したテスト側要因**: psqlのRETURNING出力汚染(コマンドタグ混入)・vh(視覚隠蔽)ラジオへのcheck()・複数要素への文言マッチ・起動時同意ゲートとクリックの競合待ち。

**Phase 4(再分析・パス1/3)**: 実装を通じて新たに観察された振る舞いを評価:

1. 取り下げ後のGET /v1/intents/{id}は本人にも404(契約挙動)→ E3のアサーションに反映済み。新規ギャップなし
2. 04画面の全形/最小形はlatch proposal jsonbの豊富さで決まる → E2Eでは全形を検証(fixtureがsummary_only形)。最小形はunit(latch-detail:138-147)で証明済み。新規ギャップなし
3. 並行refresh競合 → **findings#1(A5 known-failure)**として記録。P1の新ギャップではなく既知A5セルの根因確定

**判定: 新規P0/P1ギャップゼロ → 終了条件充足(パス1で終了・ハードキャップ3未満)**。

## 5. 最終報告(Phase 5 — 2026-10-12)

### Before → After

| 指標 | Before(Phase 2時点) | After(Phase 3完了) |
|---|---|---|
| 実ブラウザ×実APIのE2Eテスト | **0件** | **35件**(2回連続全緑・~24秒・chromium) |
| 振る舞い×次元セル(適用91) | ✅57 / ◐32 / —2(すべてモック証明) | openゼロ(vitest✅+E2E implemented+known-failure 2+waived) |
| P0ギャップ(横断アクセス) | 1件open | implemented(cross-user.spec.js:8) |
| P1ギャップ | 7件open | 7件implemented(findings#1は当日修正・A5のfixme解除) |
| P2ギャップ | 9件open | 9件implemented |
| 発見したアプリバグ | — | **findings#1(並行refresh競合でfamily失効)→ 当日修正済み**(client.js単一フライト化) |

### 主要な新規証明(既存367件のvitestでは証明されていなかったもの)

1. **G7横断アクセス**(P0): 実ユーザー2名+第三者の3セッションで404+ホーム導線を確認
2. **G5 undo→open復帰**(FG-07): 全テスト資産で最初のアサーション
3. **主幹フローの実API結合**(OBS-03): composer配線(型入力→parse→chips→CTA)と保存→リロード永続
4. **TokenPanel実挙動**(OBS-02): 接続・無効トークン・API不能の3分岐
5. **同意ゲートのApp配線**(B1/B2): 起動時誘導と422横断検出
6. persist系14セルの実再取得/リロード証明

### 正直な完了声明

- **完了**: すべてのギャップ(G-01〜G-17)が終端状態(implemented / known-failure / waived)。再分析パス1で新規P0/P1なし。vitest 367件は無影響(緑維持)
- **既知の未解決**: なし(findings#1は2026-10-12にclient.jsへ単一フライト化で修正済み。単体client.test.js+実E2E A5の両方で解消を確認)
- **対象外**: Cloudflare実装(8787)向けE2E project(契約試験130件がAPIパリティを担保するため本パスでは対象外とした。必要時にwebServer追加で拡張可能)・W-01〜W-04のwaived(理由はgaps.md)
- **E2Eの実行前提**: compose常設環境(`make up`+`make migrate`)・chromium導入済み・`make e2e`(=frontendの `npm run test:e2e:report`)
- **録画アーカイブ**: 全テストを常時録画し `frontend/e2e-recordings/<実行日時>/index.html`(file://で開ける純静的ギャラリー・機能群セクション+注目点つき)で閲覧する。過去runは最新3件を保持。対話トリアッチは `npx playwright show-report`。ゆっくり再生は `LATCH_E2E_SLOWMO=300`

## 6. Phase 4パス2 — 総洗い出し(2026-10-12・ユーザー指示)

ユーザー指示「残り得るじゃなくてすべて洗い出せ」により、UI振る舞い行列より広い検証空間(受け入れ#1〜#27全行・FR・パイプライン・プライバシー・評価・NFR・リリース)を総当たり走査した。結果は **`backlog.md`(88項目・P0:4/P1:16/P2:約35/P3:約33)** が正本。

- パス2は新規P0/P1ギャップを多数産出したため、**本来の終了条件(直近パスで新規P0/P1ゼロ)は不成立**。以前の§5報告は「UI振る舞い行列のスコープでは完了・検証空間全体では不完全」と訂正する
- UI振る舞い行列への新規行(グループ04/05・通報dialog・attendance・hidden最小形・UNDER_AGE・下書き再開等)は backlog.md §5(U-01〜U-23)に未採番のまま列挙(実装時に行列へ採番・§2.3/§2.4へ反映)
- 実装欠落のため検証不能な8項目(A/B基盤・アンケート・D-13第二段・Web Push登録・nearby UI導線・D-02警報線等)も洗い出し済み(backlog §3/§6/§10)

## 7. 環境基盤+P0実装パス(2026-10-12・backlog §12①②)

backlog §12の推奨着手順①(E-01+E-03)と②(P0全4項目)を実施した。**Playwrightはci 1projectから3projectへ**(ci=compose+vite・staging=nginx 18000+vite proxy・cloudflare=cf-dev 8787+build済みassets)。project選択は `LATCH_E2E_PROJECTS`(既定=ciのみ・`make e2e`の従来挙動は不変)。

### 7.1 実装したテスト(45テスト稼働・全緑)

| project | spec | テスト | 対応backlog項目 |
|---|---|---|---|
| ci(chromium) | tests/e2e/safety.spec.js | 3(1対1成立=対象固定→理由4択→POST/グループ成立=対象ラジオ2人/1対1提案=対象非開示の案X) | **U-01(P0)** |
| ci(chromium) | tests/e2e/proposal-minimal.spec.js | 1(最小形04=照合表・4カードなし/期限/必要人数/YES可) | **U-06(P0・SQL fixture版)** |
| ci(chromium) | 既存9spec | 40(36従来+新規4) | — |
| staging | tests/e2e-staging/pipeline-ui.spec.js | 2(UI預け入れ×2→実worker提案→04最小形→YES×2→成立チャット双方向/お知らせ2種到達) | **E-03・P-01(P0)・U-06(実パイプライン版)** |
| staging | tests/e2e-staging/ground-truth.spec.js | 1(適合2対=提案到達+形状・不適合5対=両者latch 0件・UI行列観測) | **A-01(P0)** |
| cloudflare | tests/e2e-cloudflare/cf-smoke.spec.js | 2(未同意→onboarding同意→主幹作成/2ユーザーUI預け入れ→Queues評価→04最小形→YES×2→成立) | **E-01・P-01 cf版** |

### 7.2 整備した実行基盤(テスト資産・アプリソース無変更)

| 資産 | 内容 |
|---|---|
| `frontend/playwright.config.mjs` | 3project+project別webServer(ci=5173/staging=5174(proxy→18000)/cf=scripts/cf-e2e-server.mjs)。`LATCH_E2E_PROJECTS`で選択(既定ci) |
| `frontend/vite.config.mjs` | proxy向き先を`LATCH_VITE_PROXY_TARGET`でenv化(未指定=8000で従来不変) |
| `frontend/tests/e2e/helpers.js` | `LATCH_E2E_TARGET`でci/staging/cloudflareを切替(鍵=.staging/keys・DB=latch-staging-db-1・API base)。insertGroupLatch/group fixture/proposal指定対応/reportsCount追加 |
| `frontend/tests/e2e-staging/helpers.js` | ensureStagingClockReal(ControlledClock=実時間保証)・waitForLatch(debounce+tick込み)・depositViaUi(UI預け入れ操作)・createGroundTruthIntent(境界値fixture) |
| `frontend/scripts/cf-e2e-server.mjs` | cf版webServer(build→cf-sync→D1 migration+地物seed→pywrangler dev 8787)。persistは`.cf-e2e-d1`(リポジトリ直下・wranglerのファイル監視リロード問題の回避) |
| `frontend/package.json` | `test:e2e:staging`/`test:e2e:cf`(+:report)スクリプト |

### 7.3 実機で確認された実装挙動(ドキュメント化価値のある発見)

1. **UI作成Intentの提案は自動的に最小形**: UIはvisibility=hidden_until_match確定送信のためbuild_proposalは{headcount,match_level}のみ生成(04=照合表なし)。staging・cf両実装でtoEqual証明
2. **D-05回答期限式の実挙動**: 対象時刻が近いスロット(+1〜2h)では期限=通知+15分(target-60min<通知+15分となるため)。E2Eのスロット設計は+2h以上が安定
3. **D1退会はlatchをcancelled化するがIntentは残る**(cf実装): 退会済みユーザーのIntentが残存し同一時間帯の再実行と交差マッチしうる→cf specはスロット分離(+7h)で対処
4. **findings#2**: お知らせpopover初回オープンは起動時スナップショットを使用(通知到達が見えない)。staging E2Eが発見・2回目オープンで再取得することを確認済み

### 7.4 残る主要項目(backlog正本・次の推奨=③P1 UI群)

P1残14項目(E-01/E-03消化済み)。U-05(両者YES 2ブラウザ連鎖)・T-01/T-02(期限切れUI・staging Clock活用)・U-19/P-08(expired 04)は今回のstaging基盤の直接活用形で着手容易。CI載せ(O-01)は45テスト3project体制をGitHub Actionsへ乗せる段。

## 8. V1-P1「回答と期限」クラスタ実装パス(2026-10-12・backlog §12③+findings#2)

backlog §12③のP1 UI群5項目(U-05・U-08・U-09・U-19+T-01/P-08統合・T-02)+findings#2修正を実施。

### 8.1 実装テスト(計5新規+2改修・全緑2回連続以上)

| project | spec | テスト | 対応backlog項目 |
|---|---|---|---|
| ci | e2e/response.spec.js:19 | 両者YESの2ブラウザ連鎖→双方05 | U-05(受け入れ#15) |
| ci | e2e/errors-and-settings.spec.js:99 | 18〜19歳の飲酒Intent作成422 UNDER_AGE全体note | U-08(受け入れ#27 UI面) |
| ci | e2e/main-flow.spec.js:78 | 下書き→直して預ける→active化→03待機中へ | U-09(受け入れ#1経路) |
| staging | e2e-staging/expiry-ui.spec.js:56 | 期限経過04タイマー0:00→実sweeper expired→04 closed・03/お知らせから消失 | U-19+T-01+P-08(受け入れ#16・#4後半) |
| staging | e2e-staging/expiry-ui.spec.js:116 | Intent expires_at経過→実sweeper expired→03待機中から消失 | T-02 |
| staging | pipeline-ui.spec.js:84(改修) | お知らせ初回オープンで実データ(popover 3回クリックworkaround撤去) | findings#2修正のE2E証明 |
| unit | latch-notice.test.jsx(改修) | preload後の初回openも必ずGET(起動後到着2件を初回描画) | findings#2 |

稼働実績(最終状態): **vitest 368(2回)・ci 43(3回)・staging 5(3回)・cf 2(3回)**=E2E 50テスト。

### 8.2 整備した基盤

- helpersの`provisionUser`へ`birthDate`パラメータ(既定1990-06-15不変・U-08用)
- staging expiry系のSQL過去化パターン: `UPDATE latches SET response_deadline = now() - interval '1 minute' WHERE id=...`(per-ID限定・staging postgresはtimestamptz列のためinterval式。D1/SQLiteのbigint microsと異なる)

### 8.3 実機で確認された実装挙動(ドキュメント化価値のある発見)

1. **04の期限切れ表示はブラウザ実時間由来**: DeadlineTimerはクライアント時刻の残時間計算のため、staging Clockを進めても04は「締切」にならない。期限切け切れUIのE2E正規手段は**SQLでresponse_deadlineを過去化**(backlog U-19記載どおり)。表示形は「締切」文字ではなく`[data-role=deadline]`の`0:00`+回答2択disabled(countdownTextは0未満を表示しない)
2. **期限切れlatch/intentのexpired化は実sweeper(60秒tick)の成果として観測可能**: SQL過去化→最長~70秒でstatus=expired(P-08「sweeperの成果」の実測)
3. **時間マッチングは3時間硬判定ではない**: layer3.time_closenessは飽和型(1−min(Δmin,180)/180)のためΔ≥3hでもrule寄与は0になるのみ。similarity・vocabが高いと遠隔スロットでも交差latchが生じうる(stub Jevは常に0.9で提案するため顕著)。**E2Eの候補一覧assertは自分のlatch(href)にスコープすべき**(`#proposalsScreen a.sb-row-link[href="#/latches/{id}"]`形式)
4. **expiry系fixtureのスロット設計**: +11.5h(U-19)/+12.5h(T-02)でpipeline-ui(+2h/+6h)・ground-truth(D(2..5)=+39h以降)と分離。かつ**各テストはfinallyで自分のIntentをDELETE**(失敗残存Intentが次run・並走specと交差マッチしてD-08上限を食う実被害を確認)
5. **draft→active化の成功遷移は#/standby(裸)**: ?deposited=1は新規作成経路のみ(PATCH経路は付かない)、「預けました。」トーストも出ない(遷移と一覧登場で判定)
6. **ciのstub Parserはalcohol_involved=false固定**: U-08(UNDER_AGE)のUI実機はparse応答のroute書き換え(実応答のalcoholをtrueに・G1 alcoholゲートが実Parserの当該挙動を実証済み)で駆動するのが正規手段。02の「含む/含まない」segはD-04人数確認でありalcoholのUIスイッチではない

### 8.4 残る主要項目(backlog正本)

P1残7項目(P-02・A-02・U-02・U-04・U-07・O-01・O-02)。次の推奨=④O-01 CI(GitHub Actions)載せ+残りP1 UI群(U-02ブロックdialog・U-04 attendance・U-07 visibility 2値比較)。findingsは#1・#2ともに修正済みで未解消ゼロ。

## 9. V1-P2 残P1 UI群+CI構築実装パス(2026-10-12・backlog §12④+残りUI群)

backlog §12④(O-01 CI)と残りP1 UI群4項目(U-02・U-04・U-07・P-02)を実施。**P1の開発側実装は出揃い**(残るP1=16中2=A-02オーナー承認待ち・O-02 load環境実施)。

### 9.1 実装テスト(計4新規spec・helpers拡張2)

| project | spec | テスト | 対応backlog項目 |
|---|---|---|---|
| ci | e2e/block.spec.js:10 | 05成立起点→「通報・ブロック」dialog→対象固定→「ブロックする」→完了表示→blocks表+設定ブロック一覧へ反映 | **U-02(受け入れ#20 UI面・H10)** |
| ci | e2e/attendance.spec.js:13 | completed fixture→05出席申告2択→「会いました」→済み表示+calibration_records.actual_attended=true | **U-04(原則6・D-09)** |
| ci | e2e/visibility.spec.js:38 | 同一条件候補を2値配置→04サマリ差分+raw_text/表示名/NG条件/座標の否定的assert(両形)+一覧サマリ行差分 | **U-07(受け入れ#22/#23)** |
| ci | e2e/group.spec.js:12 | 3人グループ04必要人数→YES×3のpartial_accept残回答→allIn→05複数人+matched/Intent/通知3行のDB証明 | **P-02(受け入れ#18)** |
| (CI) | .github/workflows/ci.yml | ci E2E+vitest+backend全套+lintを1jobで(実pushは承認待ち) | **O-01(S-4解消)** |

helpers拡張: `createIntent`へrawText/softConstraints(非開示assert用固有値の注入)・`insertCompletedLatch`(status=completed+completed_at+calibration_records直投入=attendanceの書込先と3日窓判定を満たす)。

稼働実績(最終状態): **vitest 368・ci 47(43+4)・staging 5・cf 2**=E2E 54テスト・2回連続緑。

### 9.2 実機で確認された実装挙動(ドキュメント化価値のある発見)

1. **同一Intent対へのopen状態latchは二重挿入できない**: uq_latches_intent_ids_open(M2 0004の部分UNIQUE索引・D-08提案例)がcandidate/proposed/partial_accept間でintent_ids一意を強制する。受け入れ#22「同一候補を双方で配置」のUI実機は**同一条件の別ペア(A+B/A+C)**で行うのが正規手段(初回実装がunique違反で失敗し発見)
2. **attendanceのUI表示条件はstatusのみ・POST成立条件はcalibration_records行+completed_at**: completed fixtureはlatch行だけでは不十分で、回答確定時にサーバが作るcalibration_records(actual_attended NULL)とcompleted_at(3日窓)の投入がセットで必要(§9-6①の「completed_atは常にnull」は実運用のsweeper経路の話=fixtureでは明示投入)
3. **グループ04の必要人数/残回答表示はwho行+joinedWaitTextで構成される**: 専用のgroupNeedText(view.js)は定義のみで未使用。partial_acceptの現況(「1人が回答済み」)は再訪で見える(answered=total−remaining−1の計算)
4. **make lintに既存drift 2件(test_llm_real.py・test_llm_real_smoke.py=V1-P1 A-2のコミット残)**: O-01のworkflow設計中にlint step投入で発見し回収(ruff format+import並び・意味変化なし)。test-ciにlintが含まれない運用の隙間が実害となった形で、CI載せの価値を実証
5. **test_expiry_batchesの計数assertはDB残存に敏感 — E2E→test-ciの順で実行すると失敗する**: sweeperのrun_once件数assert(==1など)が、同DBに残存する非終端latch・active intent(過去E2Eセッション由来・FakeClockの+121hで一括期限到達扱い)で崩れる。ローカルドライランで実証(3 failed/2174 passed・`assert 34 == 1`)。**CIの実行順序は backend全套(クリーンDB)→E2E** が正しく、逆順は不可。E2E残存の掃除(cleanupAllはbest-effort)だけでは足りない=runnerの毎回クリーンDBがCIの構造的価値
6. **geofeatures空のDBでは契約試験・intent系統合試験が大量失敗する**: 地名解決(天文館等)が422 GEOCODING_FAILEDになるため(ドライランで実証: 契約30件超+attendance/consent/deletion/degraded系が赤)。ローカルのci DBは普段geofeaturesが入っているため気づかない前提。**CIではgeofeaturesのfixture投入(latch.geo CLI)をtest-ciの前後の2回**に行う(前=契約・統合試験の地名解決・後=test-ci内のgeo試験が入れ替え/消去するためE2E用の再投入)

### 9.3 CI workflowの設計判断(O-01)

- **対象=第一段階**: backend全套(make test-ci・LATCH_LLM_MODE=stub明示)+backend lint+frontend vitest+build+ci E2E(Playwright ci project)。staging/cf projectは対象外(実環境・鍵・build資産依存のため契機実行・手動維持)
- **実行順序は backend全套→E2E**(§9.2-5): 計数assertのDB残存感度のためbackend全套をクリーンDBで最初に・E2Eは最後(runnerのDBはjob毎に消えるためE2E残存は無害)
- **geofeaturesのfixture投入はtest-ciの前後2回**(§9.2-6): 前=契約・intent系統合試験の地名解決(空だと422で大量失敗)・後=E2E用(geo試験が入れ替え/消去するため)。実データ取込み(ISJ zip+OSM pbf大容量DL・手動配置)はCI不向きなため、`python -m latch.geo import-isj/import-osm`がpbf/xml自動判別で読める既存fixture(isj_sample+osm_sample・天文館を含む)の冪等投入とする(ローカル実測=7行)
- **load DB(massload 4試験の前提)もworkflow内で起動**: `docker compose -f compose.load.yaml up -d --wait db`+`make load-migrate`(dbサービスのみ)
- **失敗時の見え方**: listレポート(ログで落ちた試験が見える)+Playwright HTMLレポート・trace・録画ギャラリーをartifactへ(`if: failure()`)
- **静的確認とドライラン**: YAML構文+actionlint 1.7.12で警告ゼロ+ローカルでコマンド列ドライラン(migrate・lint・load-migrate・make test-ci全套・geo投入・build・E2E)。ドライランで順序の欠陥2件(§9.2-5/6)を発見し3回目の実行で確定シーケンス(クリーンDB+geo投入→test-ci→geo再投入)を検証。**実pushと初回実行確認はユーザー指示待ち(人間領域の別承認)**

### 9.4 残る主要項目(backlog正本)

P1残2項目(A-02=オーナー承認待ち・O-02=load環境実施)。P2以下はbacklog §12⑤⑥どおり。

## 10. V1-P3 体感アンケート実装パス(2026-10-12・backlog §12 A-06)

backlog A-06(実装欠落→実装+検証)を05 v0.7改版→backend(参照+Cloudflare)→frontend→E2Eの3層で実施。13 §3表2のPhase 1開始前要件を開発側で充足。

### 10.1 実装テスト(仕様=05 v0.7・docsコミット6905c77→実装コミット)

| 層 | 場所 | テスト | 対応 |
|---|---|---|---|
| 仕様 | docs/05-data-model-api.md v0.7 | survey_responses表・GET/POST /v1/users/me/survey・error 2件(SURVEY_ALREADY_ANSWERED/SURVEY_NOT_DUE)・配信契機4項・集計CLI | **A-06設計** |
| backend参照 | alembic 0010+latch/users/survey.py+routes・obs/survey.py | unit 21(test_survey_service 16+test_survey_summary 5)・integration test_survey_api.py 12 | **API+集計** |
| Cloudflare | migrations/0006_survey.sql+svc_users.py build_survey_service+state.py配線 | 契約local(in-process D1)survey 10ケース緑・cf-unit 129緑 | **D1側(E-01体制)** |
| 契約 | tests/contracts(両harnessへactor sv1〜sv3+seed survey_onboarding_answered) | survey 10ケース=参照・D1両側緑(test_api_parity領域"survey"追加・baseline manifestのschema_head=0010へ再生成) | **パリティ** |
| frontend | SurveyCard.jsx(03待機中の受動カード)+texts.js+styles.css(sv-*) | vitest latch-survey.test.jsx 8件(376緑) | **UI** |
| E2E | e2e/survey.spec.js:19,52,66 | onboarding往復(active化→1問→送信→済み→再訪で出ない+DB証明)/未配信(Intentなし+draftのみユーザーに非表示)/week4(created_at過去化29日→3問→answers+free_commentのDB証明) | **A-06実機** |

稼働実績(最終状態): **vitest 376・ci 50・staging 5・cf 2**=E2E 57テスト2回連続緑・backend全套2229/3 skipped(クリーンDB・load DB前提)。

### 10.2 実機・実装で確認された観察(ドキュメント化価値のある発見)

1. **契約ハーネストはケース毎にactorが新規provisionされる**(harness fixtureがfunctionスコープ): due状態を前提する連鎖ケース(onboarding回答→status変化→二重409等)はactorの状態を前提できず、各ケースのsetupで「Intent作成→回答」まで完結させるのが正規の書き方(初回実装の連鎖前提はSURVEY_NOT_DUEで失敗し発見)。consentケースが一見連鎖に見えたのは各ケース内でPOSTまで完結していたため
2. **week4ラウンドのE2E駆動はsurvey_responses.created_atのSQL過去化(29日前)が正規手段**: 期限系E2E(U-19/T-02)と同型。Clock不使用=ブラウザ実時間由来の描画がない(ステータス取得はGET時のサーバ判定のため、過去化した瞬間にdueになる)
3. **draftは「保存」でありactive化でないため配信契機1(status != 'draft'のIntent存在)の対象外**(E2E未配信ケースで実証)。active化後に全Intent削除したユーザーはdueでなくなる=削除が痕跡を消す限界として05 v0.7に記録済み
4. **headピン試験(test_geo/test_obs_schemaのassert version=="0009")はマイグレーション追加のたび機械追随が必要**: 0010で2 failed→ピン更新で解消(0005/0009と同型の既知運用)
5. **baseline manifestのschema_headがマイグレーション追加で変化**: `PYTHONPATH=tests uv run python -m contracts.regen_manifest`で更新しないとtest_baseline_manifest_currentが失敗(意図した変更なのでregenで正しい)
6. **asyncpgはjsonb SELECT結果を自動でdict化**: 試験側でjson.loadsするとTypeError(answers列のassertはdict比較で直接)
7. **D1側0006の適用経路は2つ自動化済み**: 契約localハーネスト(LocalD1Database.migrateがmigrations/配下を走査)とcf-e2e-server(build+migrate)。手動適用は不要

### 10.3 CI(O-01)への影響

本実装でCIが実行するテストは増えるのみ(順序・前提の変化なし): ci E2E 47→50・test-ci 2177→2229・vitest 368→376。alembic head=0010・D1 0006は各migrateステップが自動適用。**workflowの実pushと初回実行確認は引き続きユーザー指示待ち(人間領域の別承認のまま)**
