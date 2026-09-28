# M1 ws-5(フロントエンド)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M1スコープ7(12 M1-7)をfrontend/新設で実装する — prototypeを本実装へ持ち上げ、parse連携(debounce 1秒)・条件リストの動的連結(タイプ別インライン編集)・有効期限の既定選択計算+disabled化(expiry-options API経由)・必須3フィールド催促・判定不能NG条件のNG行・保存API接続(active/draft)を通し、03 §10の既知差分のうち入力フローに関わる4点を解消する。

**Architecture:** `frontend/`を新設しprototype/の資産(index.html・styles.css・app.js・アセット)をコピーして出発する(prototype/は温存)。JSはES modules・状態は小さな純オブジェクト(state.js)・表示文言とAPIボディ組立は純関数(format.js)に分離し、DOM構成(conditions.js)とAPI通信(client.js)を注入可能な依存として結合する。期限4選択肢の計算はcompletion.pyの単一実装をbackend API(GET /v1/intents/expiry-options)経由で消費する(07 §2の二重実装禁止を構造で守る)。テストはvitest+happy-dom+fetchモックで実資産ゼロ。

**Tech Stack:** Vite 6.4.2(prototypeと同一)/ vanilla ES modules / vitest 3 + happy-dom(試験のみ・新規ランタイム依存ゼロ)/ backend側はPython 3.13 + FastAPI(既存)

**Spec:** docs/plans/M1/ws-5-design.md(本計画はdesign §2〜§4をタスク分解する。design §5の告白6件 — E2E不導入・expiry-options API追加〔05追記は後日ユーザー確認〕・トークン保存場所・debounce 1秒・文言対応表・frontend/新設+prototype温存 — はスーパーバイザー承認済みであり、本計画はそれを前提に組み立てる)

---

## 0. 作業規律(worktree・コミット・環境)

1. **着手条件(ws-4マージ後)**: 本単位のworktreeは**ws-4(レート制限)マージ済みのmain**から切る。着手前に次を確認し、全て成立していることを報告書に記録する。成立していない場合は作業を中断し「BLOCKED: ws-4未マージ」と報告する。
   - `git log --oneline -8` でws-4(ratelimit)のマージコミットが見えること
   - `ls backend/src/latch/ratelimit/` で `__init__.py errors.py store.py limiter.py deps.py` が存在すること(429 RATE_LIMITED・API 60req/分が実装済みの証)
2. **worktree**: 実装はスーパーバイザーが用意したgit worktree内で行う。なければ `superpowers:using-git-worktrees` スキルに従って作成する(ブランチ名 `ws-5-frontend`)。mainには直接触らない。本計画書のパスはすべてリポジトリルートからの相対パスで書いてある(worktree内ではworktreeルートが基準)。
3. **コミット規律(必須)**: タスク単位でworktreeブランチへコミットする(1タスク=1コミットを基本とする)。各Taskの最終ステップにコミットコマンドを用意してあるので必ず実行すること。コミットメッセージの末尾には `Co-Authored-By: Claude Code <noreply@anthropic.com>` を付ける(改行を挟んで追記。`git commit -m "…" -m "Co-Authored-By: …"` 形式でよい)。mainへのマージ・pushはスーパーバイザーが行う(実装者は行わない)。
4. **フロントエンド環境(§0必須・worktree内で完結)**: node/npmは次の手順でfrontend/内に整える。compose常設環境・repo root側には何も追加しない。
   - 要件: Node.js 20.19以降(または22.12以降)とnpm。`node --version` / `npm --version` で確認する。
   - Task 1で `frontend/package.json` を作成後、`cd frontend && npm install` を1回実行する(node_modulesが生成される。`.gitignore` の `node_modules/` 行で既に除外済みのためコミットされない)。
   - 以後の試験実行はすべて `cd frontend && npm test`(vitest run。実api・実DB・実Redisを消費しない)。
5. **vite dev serverと429(レート制限)への注意**: `npm run dev` のvite dev proxyは `/v1` をcompose常設api(127.0.0.1:8000)へ転送する。**ws-4適用後のapiはAPI全体60req/分(ユーザー単位)で429 RATE_LIMITEDを返す**。正常フローの通信は4リクエスト(parse 1+expiry 2+保存1・design §2.6)に収まるが、開発中の頻繁な画面リロード・テキスト連打で429に当たる可能性がある。429が出たら**1分待って再操作**すること(待機以外の回避策は本単位では用意しない)。unit試験はfetchモックのため影響しない。
6. **共有ci-dbとtest-ci**: フロント試験はDB/Redisを消費しない(STATUS運用ルール1〜3に抵触しない)。ただしbackend側の変更(Task 2)があるため、**`make test-ci` と `make migrate` は実行しない**(共有ci-dbの取り合い)。backend開発は `make lint` / `make test`(unitのみ)で進め、報告には「test-ci=スーパーバイザー検証待ち」と記録する。
7. **apiイメージ再ビルド**: Task 12の結合確認で実apiを叩くため `docker compose build api && docker compose up -d --wait` を実行する(STATUS運用ルール4。apiはbuild型・ソースマウントなし)。compose.yaml・Makefile・docker/自体は変更しない。
8. **raw_textの規律(確定値19)**: 意図文言をconsole・ログ・テストコードに新規に出力しない。テスト・手順で使う入力はdocs掲載済みの例文(03 §3・05 §5の「今日20時以降、天文館で…」)に限る。
9. **検証コマンド**: backend変更を含むコミット前に `make lint` と `make test` が緑であること。frontendのコミット前に `cd frontend && npm test` が緑であること。Task 11からは `npm run build` も緑であること。

## 1. 参照仕様節

実装が直接依存する確定値(design §1.2に全22件の詳細がある。ここでは要点と出典のみ)。

| # | 確定値 | 出典 |
|---|---|---|
| 1 | フロントエンドの実装基準は`prototype/`(Vite + vanilla JS)。docsと食い違う場合はプロトタイプ側を正とする | 00 運用ルール6・03 §10 |
| 2 | 入力フロー: 300字上限+カウンタ → 条件リスト(5行+行単位インライン修正+条件の追加) → 預け方パネル → 下書き(draft・トースト)or 預ける(⌘Enter) → 確認モーダル → Intentを確認する(POST active) → 同一画面に留まる | 03 §3 |
| 3 | 条件リストの文言はプロトタイプ固定。追加ラベル(その他・曜日・移動・雰囲気)はUI上の入力補助で格納先はすべてsoft_constraints(API送信値はtextのみ) | 03 §3・03 §10・D-19補足 |
| 4 | 必須3(category・time.start・location)欠落時は「預ける」をブロックして指定を促す。検証の強制はサーバ側。draftはraw_textのみ必須 | 03 §3・05 §5 |
| 5 | 判定不能NG条件: NG行+「この条件は確実には除外できません。参考条件として扱います」の注意表示。同意のうえng_unverifiableとして保存 | 03 §3・02 D-04 |
| 6 | 有効期限4選択肢(今夜23:30/明日12:00/明日23:30/3日後まで=now+72h)。過ぎた選択肢はdisabled。既定=time.start+3時間に最も近い選択可能肢。クライアントは絶対時刻をexpires_atとして送る。draftでは検証しない | 03 §3・03 D-19 |
| 7 | parse: POST /v1/intents/parse。応答structured_intentは07 Parser出力と同形(visibility・notification_levelを含まない)。warningsはD-04注意表示のデータソース。text 300字超過も422 | 05 §5 |
| 8 | parseエラー分岐: 503 LLM_UNAVAILABLE=再試行ボタン / 422 VALIDATION_ERROR=構造化フォームフォールバック(行編集と同一UI)。フォームはフォールバック専用で主UIにしない | 07 D-17・05 §5 |
| 9 | 保存: POST /v1/intents {raw_text, status: active/draft, structured_intent: 全量}。activeは必須3・時刻検証・ジオコーディング・年齢検証をサーバが強制。structured_intentは全置換 | 05 §5 |
| 10 | expires_atはstructured_intent内フィールド。nullならサーバがtime_start+3h最近肢を補完(draftは補完せずNULL) | 05 §5 |
| 11 | 補完規則の単一実装はcompletion.py(expires_at_candidates・nearest_expires_at)。消費者は保存経路(ws-3)とUI計算(ws-5) | 07 §2・completion.py |
| 12 | 認証: 全API認証必須(Bearer JWT)。POST /v1/auth/tokenはIdPトークンをbodyで受けaccess_token(1時間)+refresh_token(回転式30日)を返す。テスト用IdPトークンは内部CLI `python -m latch.auth issue-idp-token` | 05 §5・C3・10 §1 |
| 13 | エラー形式は共通envelope {error: {code, message, details}}。クライアントはcodeで分岐しmessageは参考にしか使わない。429 RATE_LIMITED・422 ACTIVE_INTENT_LIMITはws-4実装済み(前提) | 05 §5・ws-4 |
| 14 | Parser timeout 10秒・再試行なし(サーバ側)。クライアントも自動再試行しない | 07 §1・07 D-17 |
| 15 | ci環境: api 127.0.0.1:8000常設・composeはbuild型(コード変更後に再ビルド必要)。フロントの通信設計は60req/分の上限内に収める | 10 §1・STATUS運用ルール4 |
| 16 | raw_textは非公開・ログ出力禁止。クライアント側でもconsole等へ意図文言を出さない | 01 §21・08 §2.4 |

## 2. スコープ(作成・変更するファイル一覧)

### 2.1 新規作成(frontend/・backend試験)

```
frontend/
  package.json          # Task 1: prototype依存+vitest/happy-dom(試験)
  vite.config.mjs       # Task 1: dev proxy(/v1→127.0.0.1:8000)+vitest設定
  index.html            # Task 1コピー → Task 11で本実装構成へ修正
  styles.css            # Task 1コピー → Task 11でNG行・催促・パネル等を追記
  public/assets/textures/  # Task 1: prototypeからコピー
  README.md             # Task 1: 起動手順・トークン発行手順
  src/main.js           # Task 1(暫定コピー) → Task 11で本配線
  src/ui/chrome.js      # Task 11: テーマ・popover・トースト・モーダル(app.jsから分離)
  src/api/client.js     # Task 5: request()(認証ヘッダー・envelope解釈・401→refresh→再送)
  src/api/session.js    # Task 5: トークン管理(access=sessionStorage+メモリ / refresh=localStorage)
  src/intent/state.js   # Task 4: フォーム状態・canSubmit/canDraft/missingRequired
  src/intent/format.js  # Task 3: 表示文言・JST変換・APIボディ組立(純関数)
  src/intent/parseFlow.js  # Task 6: debounce 1秒・同一text抑制・in-flight abort
  src/intent/conditions.js # Task 7/8: 条件リストDOM・催促・NG行・タイプ別エディタ
  src/intent/expiry.js  # Task 9: expiry-options取得・select構成
  src/intent/save.js    # Task 10: active/draft保存・エラー表示マップ
frontend/tests/
  smoke.test.js         # Task 1(雛形) → Task 11で拡張
  format.test.js        # Task 3
  state.test.js         # Task 4
  session.test.js       # Task 5(session+client・ファイル名はdesign外の追加)
  client.test.js        # Task 5
  parseFlow.test.js     # Task 6
  conditions.test.js    # Task 7/8(design §3.1に対応・表示とエディタで1ファイル)
  expiry.test.js        # Task 9
  save.test.js          # Task 10
backend/tests/unit/intents/test_expiry_options_routes.py  # Task 2(unit・ASGI)
backend/tests/integration/test_expiry_options_api.py      # Task 2(実行はスーパーバイザー)
docs/plans/M1/ws-5-report.md  # Task 12: 報告ファイル
```

### 2.2 変更(既存ファイル)

| ファイル | 変更内容 | タスク |
|---|---|---|
| `backend/src/latch/intents/routes.py` | parse_routerへGET /v1/intents/expiry-optionsを追加(completion.pyの関数を呼ぶだけ・新規計算ロジックなし) | Task 2 |
| `docs/05-data-model-api.md` | §5「その他のエンドポイント」表へexpiry-optionsの1行を追記(運用ルール7。後日ユーザー確認・報告書に記録) | Task 2 |

## 3. 禁止(触れてはいけないもの・スコープ外の判断基準)

**触れてはいけないファイル**(design §3.3):

- `prototype/` — 温存(コピー元・実装基準の参照物のまま)。1行も変更しない
- `backend/src/latch/` のうち routes.py 以外の製品コード(main.pyへのCORS追加はしない・completion.py・service.py・store.py・schema.py・intent_input.py等は読むだけ)。特に `backend/src/latch/ratelimit/`(ws-4資産)・`backend/src/latch/auth/`・`backend/src/latch/core/`・`backend/src/latch/llm/`
- `backend/alembic/`(マイグレーション追加なし)
- `compose.yaml`・`Makefile`・`docker/`(frontend起動手順はfrontend/READMEに記す)
- `docs/` 仕様書群のうち05以外・`docs/learn/`(agent4管轄)
- 既存テストファイル一切(修正禁止・無修正で緑であることを確認するだけ)

**スコープ外と判断する基準**(design §1.4。以下を見つけても作らない・報告に記録のみ):

- Active Intent一覧・下書き一覧のUI(topbar「Intent N件」の実数化・draft再開編集)— M3-10。draftのPOST接続のみ本単位
- ホーム・提案詳細・成立済み詳細・お知らせ一覧・設定 — M3-10
- 本番IdPログインUI(Google/Apple)— 本単位は開発用トークンパネルのみ
- フロントの本番配信形態(静的配信元・CDN・コンテナ化)— M4。vite devとbuild確認のみ
- 429 RATE_LIMITEDの実装・実測 — ws-4実装済み。本単位は表示のみ
- Playwright等のブラウザE2E自動化 — 導入しない(design告白1・承認済み)
- NG行の削除UI — プロトタイプに行削除UIがないため本単位では付けない
- CORS許可オリジンの追加 — vite proxyで解決するためbackendは無変更

## 4. Global Constraints

- UI構成・文言はprototype基準(00 運用ルール6)。本計画が変更を指示する箇所(初期テキストを空に・expiry selectの動的構成等)のみを変え、変更した文言は報告書の文言対応表に載せる
- API通信はすべてcreateClientのcall()経由(Authorizationヘッダー・envelope解釈・401リフレッシュを一元化。fetchの直接呼び出しを書かない)
- クライアントはエラーのcodeで分岐しmessageは参考にしか使わない(確定値13)。唯一の例外はVALIDATION_ERRORの表示位置推定(messageの先頭一致・design §2.9表の実現手段。フォールバックはグローバル表示)
- 期限4選択肢の計算・既定選択・selectable判定をフロントで再実装しない(07 §2)。すべてGET /v1/intents/expiry-optionsの応答から組み立てる
- 正常フローの通信は4リクエスト以内(parse 1+expiry 2+保存1)。同一テキストのparse再送をしない
- 新規ランタイム依存は追加しない(dependenciesはprototypeと同一。試験依存はvitest・happy-domのみ)
- vitestは実api・実DB・実Redis・実LLMを消費しない(fetchモック・インメモリストレージ)
- 意図文言(raw_text)をconsole・ログに出すコードを書かない(確定値16)。テスト入力はdocs掲載の例文のみ
- アクセシビリティはprototypeの規律(aria-expanded・role=dialog・role=status・:focus-visible・Escape)を維持したまま拡張する
- backend製品コードの変更はroutes.pyのexpiry-options追加のみ。時刻参照はget_clock経由(C2。arch testが毎コミットで強制)

## 5. Review Focus(specが暗示するが各タスクの試験だけでは拾い切れない入力クラス)

1. **parse 422フォールバック状態からの下書き保存**(必須3が揃わないままでもdraftは保存できる=raw_textのみ必須)— Task 10のtest_5がピン留め
2. **refreshトークンも失効した401**(access・refresh両方無効で無限リフレッシュループにしない。refreshは1回のみ・失敗ならパネルへ戻す)— Task 5のclient試験test_401_refresh_fail_and_expireがピン留め
3. **期限選択肢の全滅がない**(3日後=now+72hは常に未来のためselectableが全滅しない。深夜0時前後でもUIが選択値を失わない)— Task 2 unitのtest_3・Task 9のtest_default_fallback_to_first_selectableがピン留め
4. **datetime-localを空のまま確定した行編集**(start=nullへ戻し催促状態にする。エラー・例外にしない)— Task 8のtest_time_edit_clearがピン留め
5. **in-flight中のテキスト再入力**(古いparse応答で条件リストが上書きされない・直前の1リクエストのみ有効)— Task 6のtest_inflight_abort_and_discardがピン留め
6. **未知のVALIDATION_ERROR message**(表示位置の判定に失敗してもグローバル表示へ落ちる。行指定ミスで黙って捨てない)— Task 10のtest_error_placement_unknown_validationがピン留め
7. **JST日付境界**(JST 0時を跨ぐtime.startで「今日/明日」判定が正しい・datetime-local変換の往復)— Task 3のtest_format_time_boundaries・test_datetime_local_roundtripがピン留め
8. **保存中の二重送信**(リクエスト中にボタン・⌘Enterを押しても2本目が飛ばない)— Task 10のtest_save_inflight_disableがピン留め

---

## Task 1: frontend/雛形(prototypeコピー・npm環境・smoke試験・README)

**Files:**
- Create: `frontend/package.json`・`frontend/vite.config.mjs`・`frontend/README.md`
- Create: `frontend/index.html`・`frontend/styles.css`・`frontend/public/assets/textures/*`(prototypeからコピー+後述の最小変更)
- Create: `frontend/src/main.js`(prototype app.jsを暫定コピー。Task 11で本配線に置換)
- Test: `frontend/tests/smoke.test.js`

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: `npm test` / `npm run dev` / `npm run build` が動くfrontend/環境。以後のタスクはこの package.json の scripts を使う

- [ ] **Step 1: prototypeの資産をコピーする**

```bash
mkdir -p frontend/public/assets frontend/src frontend/tests
cp prototype/index.html prototype/styles.css frontend/
cp -r prototype/public/assets/textures frontend/public/assets/
cp prototype/app.js frontend/src/main.js
```

- [ ] **Step 2: index.htmlのscript参照をmain.jsへ変える**

`frontend/index.html` の末尾近く `<script type="module" src="/app.js"></script>` を次に変更する:

```html
    <script type="module" src="/src/main.js"></script>
```

- [ ] **Step 3: package.jsonを作成する**

`frontend/package.json`:

```json
{
  "name": "latch-frontend",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@fontsource/fraunces": "^5.3.0",
    "@fontsource/klee-one": "^5.3.0",
    "@fontsource/m-plus-1p": "^5.3.0",
    "@fontsource/shippori-mincho": "^5.3.0",
    "@phosphor-icons/web": "^2.1.2",
    "vite": "6.4.2"
  },
  "devDependencies": {
    "happy-dom": "^17.0.0",
    "vitest": "^3.0.0"
  }
}
```

- [ ] **Step 4: vite.config.mjsを作成する(dev proxy+vitest設定)**

`frontend/vite.config.mjs`(design §2.2。CORSのためのbackend変更はしない):

```js
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist/client",
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
    proxy: {
      "/v1": "http://127.0.0.1:8000",
    },
    warmup: { clientFiles: ["./index.html", "./styles.css", "./src/main.js"] },
  },
  test: {
    environment: "happy-dom",
    include: ["tests/**/*.test.js"],
  },
});
```

- [ ] **Step 5: README.mdを作成する**

`frontend/README.md`:

````markdown
# LATCH frontend

M1 ws-5: `prototype/` を出発点とする本実装(00 運用ルール6・03 §10)。プロトタイプは
`prototype/` に温存されている(実装基準の参照物・変更しない)。

## セットアップ

1. Node.js 20.19+(または22.12+)とnpmが必要(`node --version` で確認)
2. `npm install`
3. repo rootで `make up`(compose常設環境: api 127.0.0.1:8000)
4. `npm run dev` → http://localhost:5173/

## API接続(開発)

- vite dev proxy が `/v1` を `http://127.0.0.1:8000`(compose常設api)へ転送する
  (vite.config.mjs。backendにCORS設定はないため同一オリジンで開発する)
- 全APIが認証必須(05 §5)。初回は画面上の「開発用トークンパネル」へIdPトークンを貼る。
  IdPトークンの発行(repo rootで実行・10 §1の内部CLI):

  ```
  cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject <任意のsubject文字列>
  ```

- apiはbuild型・ソースマウントなし。backendコード変更後は
  `docker compose build api && docker compose up -d api` が必要(STATUS運用ルール4)
- **API全体60req/分のレート制限(ws-4)が適用されている**。画面リロードやテキスト連打で
  429 RATE_LIMITED が出たら1分待って再操作すること

## テスト

`npm test`(vitest run・happy-dom・fetchモック。実api・実DB・実Redisを消費しない)
````

- [ ] **Step 6: smoke試験を書く**

`frontend/tests/smoke.test.js`:

```js
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

const html = () =>
  readFileSync(new URL("../index.html", import.meta.url), "utf8");

describe("frontend雛形", () => {
  it("script type=module で /src/main.js を読む", () => {
    expect(html()).toContain('src="/src/main.js"');
  });

  it("prototypeの主要IDを維持する(実装基準・00 運用ルール6)", () => {
    const ids = [
      "intentText",
      "characterCount",
      "conditionList",
      "addConditionButton",
      "addConditionForm",
      "newConditionLabel",
      "newConditionValue",
      "cancelAdd",
      "expiry",
      "privacy",
      "notification",
      "submitButton",
      "draftButton",
      "toast",
      "confirmationModal",
      "modalClose",
      "returnButton",
      "themeButton",
    ];
    for (const id of ids) {
      expect(html()).toContain(`id="${id}"`);
    }
  });

  it("スタイルとアセットが参照される", () => {
    expect(html()).toContain('href="/styles.css"');
    expect(html()).toContain("paper-milk.png");
  });
});
```

- [ ] **Step 7: npm installして試験を実行する**

```bash
cd frontend && npm install && npm test
```

Expected: 3 passed(smoke 3件)。node_modulesが大きく生成されるが `.gitignore` の `node_modules/` で除外される。

- [ ] **Step 8: 雛形が配信されることを確認する**

```bash
cd frontend && timeout 5 npm run dev &
sleep 3 && curl -sf http://127.0.0.1:5173/ | rg -c "src/main.js"
```

Expected: 1(HTMLが配信されscript参照が変わっている)。確認後、バックグラウンドのdev serverを止める。

- [ ] **Step 9: コミットする**

```bash
git add frontend/
git commit -m "feat(frontend): prototypeをfrontend/へ持ち上げnpm環境とvitest基盤を整える(M1 ws-5 Task 1)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

※ package-lock.jsonはfrontend/配下に生成されるため `git add frontend/` に含まれる。node_modulesは既存の `.gitignore`(`node_modules/`)で除外される。

## Task 2: backend GET /v1/intents/expiry-options(単一実装のAPI提供・05 §5追記)

**Files:**
- Modify: `backend/src/latch/intents/routes.py`(parse_routerへ追加)
- Modify: `docs/05-data-model-api.md`(§5「その他のエンドポイント」表へ1行)
- Test: `backend/tests/unit/intents/test_expiry_options_routes.py`
- Test: `backend/tests/integration/test_expiry_options_api.py`(作成のみ・実行はスーパーバイザー)

**Interfaces:**
- Consumes: `latch.intents.completion.expires_at_candidates(now)`・`nearest_expires_at(time_start, now)`・`latch.core.deps.get_clock`・parse_routerの既存依存(認証・レート制限)
- Produces: `GET /v1/intents/expiry-options?time_start=<ISO 8601 tz-aware>` → `{"options": [{"label": str, "expires_at": ISO str, "selectable": bool} ×4], "default_index": int | None}`。Task 9のfetchExpiryOptionsがこの契約を消費する

- [ ] **Step 1: unit試験を書く(失敗を確認)**

`backend/tests/unit/intents/test_expiry_options_routes.py`:

```python
"""GET /v1/intents/expiry-options(design §2.6・05 §5追記分)。

completion.py の単一実装をAPIが正しく露出するかをFakeClockで決定的に検証する。
依存上書きは test_parse_routes.py と同じ require_authenticated オーバーライド
(ws-4適用後の api_rate_limited は require_authenticated を内包するため、
sub-dependency の上書きで効く。既存unit試験と同一の方法)。
"""

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.main import create_app

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-27 21:00

LABELS = ["今夜 23:30", "明日 12:00", "明日 23:30", "3日後まで"]


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sub-1",
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _client(clock: datetime | None = None) -> AsyncClient:
    app = create_app(clock=FakeClock(NOW if clock is None else clock))
    app.dependency_overrides[require_authenticated] = _claims
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_1_options_without_time_start():
    """time_startなし: 4選択肢・ラベル順・全selectable・default_index=None。"""
    async with _client() as client:
        resp = await client.get("/v1/intents/expiry-options")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [o["label"] for o in body["options"]] == LABELS
    # JST 21:00 実行相当: 今夜23:30はまだ未来
    assert [o["selectable"] for o in body["options"]] == [True, True, True, True]
    assert body["default_index"] is None
    expires = [o["expires_at"] for o in body["options"]]
    assert expires[0] == "2026-09-27T23:30:00+09:00"
    assert expires[1] == "2026-09-28T12:00:00+09:00"
    assert expires[2] == "2026-09-28T23:30:00+09:00"
    assert expires[3] == "2026-09-30T21:00:00+09:00"  # now+72h(JST)


async def test_2_default_index_with_time_start():
    """time_start=JST 23:00 → target=+3h=翌02:00 → 最近は今夜23:30(index=0)。"""
    async with _client() as client:
        resp = await client.get(
            "/v1/intents/expiry-options",
            params={"time_start": "2026-09-27T23:00:00+09:00"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_index"] == 0


async def test_3_passed_option_disabled_and_default_shifts():
    """JST 23:31以降: 今夜23:30はselectable=false・既定は未来のみから選ぶ。"""
    late = datetime(2026, 9, 27, 14, 31, 0, tzinfo=UTC)  # JST 23:31
    async with _client(clock=late) as client:
        resp = await client.get(
            "/v1/intents/expiry-options",
            params={"time_start": "2026-09-28T01:00:00+09:00"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["options"][0]["selectable"] is False  # 今夜23:30は過ぎた
    assert body["default_index"] == 1  # target=04:00 → 明日12:00が最近の未来
    # 選択肢が全滅しない(3日後=now+72hは常に未来 — Review Focus 3)
    assert any(o["selectable"] for o in body["options"])


async def test_4_naive_time_start_is_422():
    """tz-naiveのtime_startは422 VALIDATION_ERROR(envelope)。"""
    async with _client() as client:
        resp = await client.get(
            "/v1/intents/expiry-options",
            params={"time_start": "2026-09-27T23:00:00"},
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
```

- [ ] **Step 2: 試験を実行して失敗を確認する**

Run: `cd backend && uv run pytest tests/unit/intents/test_expiry_options_routes.py -v`
Expected: 4件 FAIL(404 Not Found — ルートが未定義)

- [ ] **Step 3: ルートを実装する**

`backend/src/latch/intents/routes.py` へ次を追記する(import部と本体。**既存コードには触れない**)。

import部(既存importの近くに追記):

```python
from latch.core.clock import Clock
from latch.core.deps import get_clock
from latch.intents.completion import expires_at_candidates, nearest_expires_at
```

parse_intent 関数の直後(WS-3 CRUDセクションコメントの前)へ追記:

```python
# ---------------------------------------------------------------------------
# M1 ws-5: 有効期限選択肢の提供(design §2.6・05 §5追記分)


class ExpiryOptionOut(BaseModel):
    """期限選択肢1件(03 §3 FR-13・design §2.6)。"""

    label: str
    expires_at: datetime
    selectable: bool


class ExpiryOptionsResponse(BaseModel):
    options: list[ExpiryOptionOut]
    default_index: int | None = None  # time_start未指定はnull


EXPIRY_LABELS = ("今夜 23:30", "明日 12:00", "明日 23:30", "3日後まで")


@parse_router.get("/expiry-options", response_model=ExpiryOptionsResponse)
async def expiry_options(
    clock: Annotated[Clock, Depends(get_clock)],
    time_start: datetime | None = Query(
        default=None, description="time.start(ISO 8601・tz-aware)"
    ),
) -> ExpiryOptionsResponse:
    """GET /v1/intents/expiry-options(05 §5追記・design §2.6)。

    completion.py の単一実装を呼ぶだけ(新規計算ロジックなし — 07 §2)。
    UI計算(ws-5)がこのAPI経由で消費する。parse_routerに置くことで
    intents_crud_router の GET /{intent_id} より先にマッチする
    (main.py のinclude順)。
    """
    if time_start is not None and time_start.tzinfo is None:
        raise IntentValidationError("time_start must be tz-aware ISO8601")
    now = clock.now()
    candidates = expires_at_candidates(now)
    default_index: int | None = None
    if time_start is not None:
        nearest = nearest_expires_at(time_start, now)
        default_index = candidates.index(nearest)
    return ExpiryOptionsResponse(
        options=[
            ExpiryOptionOut(label=label, expires_at=at, selectable=at > now)
            for label, at in zip(EXPIRY_LABELS, candidates, strict=True)
        ],
        default_index=default_index,
    )
```

import部の追記(エラー階層。既存の `from latch.intents.intent_input import …` の近くへ):

```python
from latch.intents.errors import IntentValidationError
```

`Query` と `Depends` は routes.py 先頭の fastapi import に既に含まれている(確認のうえ、無ければ追加)。

- [ ] **Step 4: unit試験を実行して通ることを確認する**

Run: `cd backend && uv run pytest tests/unit/intents/test_expiry_options_routes.py -v`
Expected: 4 passed

- [ ] **Step 5: integration試験ファイルを作成する(実行はしない)**

`backend/tests/integration/test_expiry_options_api.py`:

```python
"""GET /v1/intents/expiry-options のci環境実証(design §3.2・§4.2-4)。

実行は make test-ci(スーパーバイザーが実施。STATUS運用ルール1〜3)。
time_startの既定検証は実行時刻非依存にする(応答値から距離最小を再検証)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


async def _access_token(api_client) -> str:
    """内部CLIでIdPトークンを発行し、API発行JWTへ交換する(10 第1節)。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        "google",
        "--subject",
        f"m1ws5-{uuid_mod.uuid4().hex[:12]}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    resp = await api_client.post(
        "/v1/auth/token",
        json={"provider": "google", "idp_token": stdout.decode().strip()},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def test_1_requires_authentication(api_client):
    resp = await api_client.get("/v1/intents/expiry-options")
    assert resp.status_code == 401, resp.text


async def test_2_options_shape_without_time_start(api_client):
    token = await _access_token(api_client)
    resp = await api_client.get(
        "/v1/intents/expiry-options", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [o["label"] for o in body["options"]] == [
        "今夜 23:30",
        "明日 12:00",
        "明日 23:30",
        "3日後まで",
    ]
    assert body["default_index"] is None
    for option in body["options"]:
        assert set(option) == {"label", "expires_at", "selectable"}
    # 選択肢が全滅しない(3日後=now+72hは常に未来)
    assert any(o["selectable"] for o in body["options"])


async def test_3_default_index_is_nearest_to_start_plus_3h(api_client):
    token = await _access_token(api_client)
    time_start = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    resp = await api_client.get(
        "/v1/intents/expiry-options",
        params={"time_start": time_start},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    idx = body["default_index"]
    assert isinstance(idx, int) and 0 <= idx < 4
    assert body["options"][idx]["selectable"] is True
    target = datetime.fromisoformat(time_start) + timedelta(hours=3)
    distances = [
        abs(
            datetime.fromisoformat(o["expires_at"]).timestamp() - target.timestamp()
        )
        for o in body["options"]
    ]
    selectable = [i for i, o in enumerate(body["options"]) if o["selectable"]]
    assert idx == min(selectable, key=lambda i: distances[i])
```

- [ ] **Step 6: 05 §5へエンドポイント行を追記する**

`docs/05-data-model-api.md` の「その他のエンドポイント」表で `GET /v1/intents` 行の直後に1行挿入する:

```markdown
| GET /v1/intents/expiry-options | 有効期限4選択肢と既定選択の取得(03 第3節FR-13。UIの期限計算がcompletion.pyの単一実装〔07 第2節〕を消費するための経路・v0.4) | 認証済みユーザー全員。クエリ`time_start`(任意・ISO 8601 tz-aware・naiveは422 VALIDATION_ERROR)がある場合は`default_index`=`time_start+3時間に最も近い選択可能候補`の位置(なしはnull)。応答は`{"options": [{"label", "expires_at", "selectable"}×4], "default_index": <int\|null>}`。実装はcompletion.pyの呼び出しのみ(新規計算ロジックなし) |
```

- [ ] **Step 7: lint・unit全体・テストファイル名一意性を確認する**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
```

Expected: lint緑・unit全緑(既存含む)・重複なし(出力空)

- [ ] **Step 8: コミットする**

```bash
git add backend/src/latch/intents/routes.py backend/tests/unit/intents/test_expiry_options_routes.py backend/tests/integration/test_expiry_options_api.py docs/05-data-model-api.md
git commit -m "feat(intents): 有効期限選択肢API(expiry-options)を追加し05へ契約を追記(M1 ws-5 Task 2)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## Task 3: intent/format.js(表示文言・JST変換・ボディ組立の純関数)

**Files:**
- Create: `frontend/src/intent/format.js`
- Test: `frontend/tests/format.test.js`

**Interfaces:**
- Consumes: なし(DOM非依存・日付ライブラリなし。Intlのみ)
- Produces(以後のタスクが使う正確なシグネチャ):
  - `jstParts(iso: string) → { year: number, month: number, day: number, hour: string, minute: string, weekday: string, dateKey: string }`(JST暦で分解)
  - `formatTime(startIso: string|null, endIso: string|null, nowIso: string) → string`(`"今日 20:00以降"` 等・nullなら`"指定なし"`)
  - `formatLocation(location: {name: string|null}) → string`
  - `formatParticipants(p: {min: number|null, max: number|null}) → string`(`"2〜4人"` / `"2人"` / `"指定なし"`)
  - `formatBudget(budget: {max: number|null}) → string`(`"ひとり5,000円まで"` / `"指定なし"`)
  - `formatCategory(category: {primary: string|null, secondary: string|null}) → string`
  - `isoToDateTimeLocal(iso: string|null) → string`(`"2026-09-28T20:00"` 形式・JST)
  - `dateTimeLocalToIso(value: string) → string|null`(`"2026-09-28T20:00"` → `"2026-09-28T20:00:00+09:00"`)
  - `PRIVACY_VALUES` / `NOTIFICATION_VALUES`(select表示文言→API値のMap)
  - `buildCreateRequest(state, opts: { status: "active"|"draft", visibility, notificationLevel, expiresAt }) → { raw_text, status, structured_intent }`(state形状はTask 4参照)

- [ ] **Step 1: 試験を書く**

`frontend/tests/format.test.js`(入力はdocs掲載の例文由来の値のみ):

```js
import { describe, expect, it } from "vitest";
import {
  buildCreateRequest,
  dateTimeLocalToIso,
  formatBudget,
  formatCategory,
  formatLocation,
  formatParticipants,
  formatTime,
  isoToDateTimeLocal,
  jstParts,
} from "../src/intent/format.js";

// docs例文の意図(03 §3・05 §5)から想定されるstructured_intent相当
const NOW = "2026-09-27T12:00:00+09:00"; // JST 日曜 12:00

describe("formatTime", () => {
  it("当日のstartは「今日 H:mm以降」", () => {
    expect(formatTime("2026-09-27T20:00:00+09:00", null, NOW)).toBe(
      "今日 20:00以降",
    );
  });

  it("翌日のstartは「明日 H:mm以降」", () => {
    expect(formatTime("2026-09-28T20:00:00+09:00", null, NOW)).toBe(
      "明日 20:00以降",
    );
  });

  it("翌々日以降は「M月D日(曜) H:mm以降」", () => {
    expect(formatTime("2026-09-29T19:30:00+09:00", null, NOW)).toBe(
      "9月29日(火) 19:30以降",
    );
  });

  it("endがあれば「H:mm〜H:mm」", () => {
    expect(
      formatTime("2026-09-27T20:00:00+09:00", "2026-09-27T23:00:00+09:00", NOW),
    ).toBe("今日 20:00〜23:00");
  });

  it("nullは「指定なし」", () => {
    expect(formatTime(null, null, NOW)).toBe("指定なし");
  });

  it("UTC入力でもJST暦で表示する(日付境界・Review Focus 7)", () => {
    // 2026-09-27T16:00Z = JST 28日 01:00 → 「明日 01:00以降」
    expect(formatTime("2026-09-27T16:00:00Z", null, NOW)).toBe(
      "明日 01:00以降",
    );
  });
});

describe("行の表示文言", () => {
  it("場所", () => {
    expect(formatLocation({ name: "天文館" })).toBe("天文館");
    expect(formatLocation({ name: null })).toBe("指定なし");
  });

  it("人数: min==maxは「N人」・違えば「M〜N人」・nullは「指定なし」", () => {
    expect(formatParticipants({ min: 2, max: 2 })).toBe("2人");
    expect(formatParticipants({ min: 2, max: 4 })).toBe("2〜4人");
    expect(formatParticipants({ min: null, max: 3 })).toBe("3人");
    expect(formatParticipants({ min: null, max: null })).toBe("指定なし");
  });

  it("予算: カンマ区切り・nullは「指定なし」", () => {
    expect(formatBudget({ max: 5000 })).toBe("ひとり5,000円まで");
    expect(formatBudget({ max: 10000 })).toBe("ひとり10,000円まで");
    expect(formatBudget({ max: null })).toBe("指定なし");
  });

  it("目的: secondary優先・なければprimaryの日本語ラベル・nullは「指定なし」", () => {
    expect(formatCategory({ primary: "drinking", secondary: "焼肉" })).toBe(
      "焼肉",
    );
    expect(formatCategory({ primary: "drinking", secondary: null })).toBe(
      "飲み",
    );
    expect(formatCategory({ primary: "meal", secondary: null })).toBe("食事");
    expect(formatCategory({ primary: "activity", secondary: null })).toBe(
      "アクティビティ",
    );
    expect(formatCategory({ primary: null, secondary: null })).toBe(
      "指定なし",
    );
  });
});

describe("datetime-local ↔ ISO変換(JST固定)", () => {
  it("ISO→datetime-local(JST)", () => {
    expect(isoToDateTimeLocal("2026-09-28T20:00:00+09:00")).toBe(
      "2026-09-28T20:00",
    );
    expect(isoToDateTimeLocal("2026-09-27T16:00:00Z")).toBe("2026-09-28T01:00");
    expect(isoToDateTimeLocal(null)).toBe("");
  });

  it("datetime-local→tz-aware ISO(+09:00)", () => {
    expect(dateTimeLocalToIso("2026-09-28T20:00")).toBe(
      "2026-09-28T20:00:00+09:00",
    );
    expect(dateTimeLocalToIso("")).toBe(null);
  });

  it("往復変換でJST表記が保たれる(Review Focus 7)", () => {
    const iso = "2026-09-27T20:00:00+09:00";
    expect(dateTimeLocalToIso(isoToDateTimeLocal(iso))).toBe(iso);
  });
});

describe("jstParts", () => {
  it("JST暦で分解する(曜日・dateKey)", () => {
    const parts = jstParts("2026-09-27T16:00:00Z"); // JST 9/28 01:00 月曜
    expect(parts).toMatchObject({
      year: 2026,
      month: 9,
      day: 28,
      hour: "01",
      minute: "00",
      weekday: "Mon",
      dateKey: "2026-09-28",
    });
  });
});

describe("buildCreateRequest", () => {
  const state = {
    rawText: " 今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。 ",
    structured: {
      category: { primary: "drinking", secondary: null },
      alcohol_involved: true,
      time: { start: "2026-09-27T20:00:00+09:00", end: null },
      location: { name: "天文館", radius_m: 1000 },
      budget: { max: 5000, currency: "JPY" },
      participants: { min: 2, max: 4 },
    },
    softRows: [
      { id: "soft-0", text: "軽く飲みたい" },
      { id: "soft-1", text: "静かなお店" },
    ],
    ngRows: [{ id: "ng-0", text: "会社関係の人は避けたい" }],
  };

  it("active保存のボディ(raw_textはtrim・soft/ng配列へ集約)", () => {
    const body = buildCreateRequest(state, {
      status: "active",
      visibility: "hidden_until_match",
      notificationLevel: "proposals_only",
      expiresAt: "2026-09-27T23:30:00+09:00",
    });
    expect(body.raw_text).toBe(
      "今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。",
    );
    expect(body.status).toBe("active");
    expect(body.structured_intent).toEqual({
      category: { primary: "drinking", secondary: null },
      alcohol_involved: true,
      time: { start: "2026-09-27T20:00:00+09:00", end: null, flexibility_minutes: null },
      location: { name: "天文館", radius_m: 1000 },
      budget: { max: 5000, currency: "JPY" },
      participants: { min: 2, max: 4 },
      visibility: "hidden_until_match",
      notification_level: "proposals_only",
      expires_at: "2026-09-27T23:30:00+09:00",
      soft_constraints: ["軽く飲みたい", "静かなお店"],
      ng_unverifiable: ["会社関係の人は避けたい"],
      negative_constraints: [],
    });
  });

  it("draft保存でもvisibility既定値を格納する(05 §5)", () => {
    const body = buildCreateRequest(state, {
      status: "draft",
      visibility: "hidden_until_match",
      notificationLevel: "proposals_only",
      expiresAt: null,
    });
    expect(body.status).toBe("draft");
    expect(body.structured_intent.visibility).toBe("hidden_until_match");
    expect(body.structured_intent.expires_at).toBe(null);
  });
});
```

- [ ] **Step 2: 試験を実行して失敗を確認する**

Run: `cd frontend && npx vitest run tests/format.test.js`
Expected: FAIL(モジュールが見つからない)

- [ ] **Step 3: format.jsを実装する**

`frontend/src/intent/format.js`:

```js
// structured_intent → 表示文言・APIボディ組立の純関数(design §2.5・§2.9)。
// DOM非依存・日付ライブラリ非依存(Intl のみ)。意図文言をログに出さない(01 §21)。

const JST_TIME_ZONE = "Asia/Tokyo";
const WEEKDAY_JA = {
  Sun: "日",
  Mon: "月",
  Tue: "火",
  Wed: "水",
  Thu: "木",
  Fri: "金",
  Sat: "土",
};
const CATEGORY_LABEL = {
  meal: "食事",
  drinking: "飲み",
  activity: "アクティビティ",
};

// 預け方パネルのselect表示文言 → API値(03 §3・05 §5)
export const PRIVACY_VALUES = {
  条件一致までは非公開: "hidden_until_match",
  候補にだけ概要を表示: "summary_only",
};
export const NOTIFICATION_VALUES = {
  一致したときだけ: "proposals_only",
  近い候補も知らせる: "nearby_also",
  通知しない: "muted",
};

const pad = (n) => String(n).padStart(2, "0");

export const jstParts = (iso) => {
  const date = new Date(iso);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: JST_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    weekday: "short",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type)?.value ?? "";
  const year = Number(get("year"));
  const month = Number(get("month"));
  const day = Number(get("day"));
  return {
    year,
    month,
    day,
    hour: get("hour"),
    minute: get("minute"),
    weekday: get("weekday"),
    dateKey: `${get("year")}-${get("month")}-${get("day")}`,
  };
};

const daysDiff = (aIso, bIso) => {
  const a = jstParts(aIso);
  const b = jstParts(bIso);
  const ua = Date.UTC(a.year, a.month - 1, a.day);
  const ub = Date.UTC(b.year, b.month - 1, b.day);
  return Math.round((ua - ub) / 86_400_000);
};

export const formatTime = (startIso, endIso, nowIso) => {
  if (!startIso) return "指定なし";
  const start = jstParts(startIso);
  const diff = daysDiff(startIso, nowIso);
  const day =
    diff === 0
      ? "今日"
      : diff === 1
        ? "明日"
        : `${start.month}月${start.day}日(${WEEKDAY_JA[start.weekday] ?? ""})`;
  const from = `${start.hour}:${start.minute}`;
  const range = endIso
    ? `〜${jstParts(endIso).hour}:${jstParts(endIso).minute}`
    : "以降";
  return `${day} ${from}${range}`;
};

export const formatLocation = (location) =>
  location?.name ? location.name : "指定なし";

export const formatParticipants = (p) => {
  const min = p?.min ?? null;
  const max = p?.max ?? null;
  if (min === null && max === null) return "指定なし";
  if (min !== null && max !== null && min !== max) return `${min}〜${max}人`;
  return `${min ?? max}人`;
};

export const formatBudget = (budget) =>
  budget?.max != null
    ? `ひとり${Number(budget.max).toLocaleString("ja-JP")}円まで`
    : "指定なし";

export const formatCategory = (category) => {
  if (category?.secondary) return category.secondary;
  if (category?.primary) return CATEGORY_LABEL[category.primary] ?? category.primary;
  return "指定なし";
};

export const isoToDateTimeLocal = (iso) => {
  if (!iso) return "";
  const p = jstParts(iso);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${p.hour}:${p.minute}`;
};

// datetime-local の値はJSTと解釈し+09:00付きISOへ(design §2.5・HTML標準入力のみで確定変換)
export const dateTimeLocalToIso = (value) =>
  value ? `${value}:00+09:00` : null;

export const buildCreateRequest = (state, opts) => ({
  raw_text: state.rawText.trim(),
  status: opts.status,
  structured_intent: {
    category: { ...state.structured.category },
    alcohol_involved: state.structured.alcohol_involved,
    time: {
      start: state.structured.time.start,
      end: null, // 編集UIなし・サーバ側でtime.start+3h補完(05 §5・completion.py)
      flexibility_minutes: null,
    },
    location: { ...state.structured.location },
    budget: { ...state.structured.budget },
    participants: { ...state.structured.participants },
    visibility: opts.visibility,
    notification_level: opts.notificationLevel,
    expires_at: opts.expiresAt,
    soft_constraints: state.softRows.map((row) => row.text),
    ng_unverifiable: state.ngRows.map((row) => row.text),
    negative_constraints: [], // FR-42: 常に空配列
  },
});
```

- [ ] **Step 4: 試験を実行して通ることを確認する**

Run: `cd frontend && npx vitest run tests/format.test.js`
Expected: PASS(全件)

- [ ] **Step 5: コミットする**

```bash
git add frontend/src/intent/format.js frontend/tests/format.test.js
git commit -m "feat(frontend): 表示文言とAPIボディ組立の純関数format.js(M1 ws-5 Task 3)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## Task 4: intent/state.js(フォーム状態・預ける有効条件・催促)

**Files:**
- Create: `frontend/src/intent/state.js`
- Test: `frontend/tests/state.test.js`

**Interfaces:**
- Consumes: parse応答の `structured_intent`(07 Parser出力と同形・visibility等を含まない)
- Produces:
  - `createFormState() → state`(state = `{ rawText: string, structured, softRows: [{id, text}], ngRows: [{id, text}], parseStatus: "idle"|"in-flight"|"ok"|"fallback"|"error" }`。structuredは `{ category: {primary, secondary}, alcohol_involved: bool|null, time: {start, end}, location: {name, radius_m}, budget: {max, currency}, participants: {min, max} }`)
  - `applyParseResult(state, structuredIntent) → state`(parse成功時に5行分を格納・soft/ngを行へ分離)
  - `applyParseFallback(state) → state`(422時: 5行を空に・rawText保持)
  - `missingRequired(state) → string[]`(欠落ラベル。`"目的"|"時間"|"場所"`)
  - `canSubmit(state) → boolean`(テキスト非空 かつ 必須3充足)
  - `canDraft(state) → boolean`(テキスト非空のみ)

- [ ] **Step 1: 試験を書く**

`frontend/tests/state.test.js`:

```js
import { describe, expect, it } from "vitest";
import {
  applyParseFallback,
  applyParseResult,
  canDraft,
  canSubmit,
  createFormState,
  missingRequired,
} from "../src/intent/state.js";

const PARSED = {
  category: { primary: "drinking", secondary: null },
  alcohol_involved: true,
  time: { start: "2026-09-27T20:00:00+09:00", end: null },
  location: { name: "天文館", radius_m: null },
  budget: { max: 5000, currency: "JPY" },
  participants: { min: 2, max: 4 },
  soft_constraints: ["軽く飲みたい"],
  negative_constraints: [],
  ng_unverifiable: ["会社関係の人は避けたい"],
};

describe("初期状態", () => {
  it("必須3はすべて欠落・催促3行・預けられない", () => {
    const state = createFormState();
    expect(missingRequired(state)).toEqual(["目的", "時間", "場所"]);
    expect(canSubmit(state)).toBe(false);
  });

  it("テキストが空のとき下書きも不可(raw_textのみ必須)", () => {
    expect(canDraft(createFormState())).toBe(false);
  });
});

describe("applyParseResult", () => {
  it("5行分を格納しsoft/ngを行リストへ分離する", () => {
    const state = applyParseResult(createFormState(), PARSED);
    expect(state.structured.category).toEqual({ primary: "drinking", secondary: null });
    expect(state.structured.time.start).toBe("2026-09-27T20:00:00+09:00");
    expect(state.softRows).toEqual([{ id: "soft-0", text: "軽く飲みたい" }]);
    expect(state.ngRows).toEqual([{ id: "ng-0", text: "会社関係の人は避けたい" }]);
    expect(state.parseStatus).toBe("ok");
  });

  it("必須3が揃い預けられる(テキストあり・Review Focus 1の前提)", () => {
    const withText = { ...createFormState(), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    const state = applyParseResult(withText, PARSED);
    expect(missingRequired(state)).toEqual([]);
    expect(canSubmit(state)).toBe(true);
  });

  it("visibility・notification_level・expires_atを状態に持たない(預け方パネルの選択値)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    expect(state.structured).not.toHaveProperty("visibility");
    expect(state.structured).not.toHaveProperty("notification_level");
    expect(state.structured).not.toHaveProperty("expires_at");
  });
});

describe("applyParseFallback(422構造化不能)", () => {
  it("5行を空に戻し催促状態にする・rawTextは保持", () => {
    const parsed = applyParseResult(
      { ...createFormState(), rawText: "今日20時以降、天文館で軽く飲みたい。" },
      PARSED,
    );
    const fallback = applyParseFallback(parsed);
    expect(fallback.rawText).toBe("今日20時以降、天文館で軽く飲みたい。");
    expect(missingRequired(fallback)).toEqual(["目的", "時間", "場所"]);
    expect(fallback.softRows).toEqual([]);
    expect(fallback.ngRows).toEqual([]);
    expect(fallback.parseStatus).toBe("fallback");
    // 必須3が揃わないままでもdraftは保存できる(Review Focus 1)
    expect(canDraft(fallback)).toBe(true);
  });
});

describe("必須3の個別欠落", () => {
  it("場所だけ欠けても預けられない", () => {
    const state = {
      ...createFormState(),
      rawText: "x",
      structured: {
        ...createFormState().structured,
        category: { primary: "drinking", secondary: null },
        time: { start: "2026-09-27T20:00:00+09:00", end: null },
      },
    };
    expect(missingRequired(state)).toEqual(["場所"]);
    expect(canSubmit(state)).toBe(false);
  });
});
```

- [ ] **Step 2: 試験を実行して失敗を確認する**

Run: `cd frontend && npx vitest run tests/state.test.js`
Expected: FAIL(モジュールなし)

- [ ] **Step 3: state.jsを実装する**

`frontend/src/intent/state.js`:

```js
// フォーム状態(design §2.5〜§2.7)。純オブジェクト+純関数(immerなし・都度新オブジェクト)。
// visibility・notification_level・expires_at は預け方パネル由来で保存時に組み込む
// (parse応答は07 Parser出力と同形でこれらを含まない — 05 §5)。

export const emptyStructured = () => ({
  category: { primary: null, secondary: null },
  alcohol_involved: null,
  time: { start: null, end: null },
  location: { name: null, radius_m: null },
  budget: { max: null, currency: "JPY" },
  participants: { min: null, max: null },
});

export const createFormState = () => ({
  rawText: "",
  structured: emptyStructured(),
  softRows: [], // 「条件を追加」(その他・曜日・移動・雰囲気)とparse由来soft(D-19補足)
  ngRows: [], // 判定不能NG(02 D-04・03 §3)
  parseStatus: "idle", // idle | in-flight | ok | fallback | error
});

export const applyParseResult = (state, structuredIntent) => ({
  ...state,
  structured: {
    category: { ...structuredIntent.category },
    alcohol_involved: structuredIntent.alcohol_involved,
    time: {
      start: structuredIntent.time?.start ?? null,
      end: structuredIntent.time?.end ?? null,
    },
    location: { ...structuredIntent.location },
    budget: {
      max: structuredIntent.budget?.max ?? null,
      currency: structuredIntent.budget?.currency ?? "JPY",
    },
    participants: { ...structuredIntent.participants },
  },
  softRows: (structuredIntent.soft_constraints ?? []).map((text, i) => ({
    id: `soft-${i}`,
    text,
  })),
  ngRows: (structuredIntent.ng_unverifiable ?? []).map((text, i) => ({
    id: `ng-${i}`,
    text,
  })),
  parseStatus: "ok",
});

export const applyParseFallback = (state) => ({
  ...state,
  structured: emptyStructured(),
  softRows: [],
  ngRows: [],
  parseStatus: "fallback",
});

export const missingRequired = (state) => {
  const missing = [];
  if (!state.structured.category?.primary) missing.push("目的");
  if (!state.structured.time?.start) missing.push("時間");
  if (!state.structured.location?.name) missing.push("場所");
  return missing;
};

// 預ける=テキスト非空+必須3充足(03 §3・design §2.7)
export const canSubmit = (state) =>
  state.rawText.trim().length > 0 && missingRequired(state).length === 0;

// 下書き=テキスト非空のみ(draftはraw_textのみ必須・05 §5)
export const canDraft = (state) => state.rawText.trim().length > 0;
```

- [ ] **Step 4: 試験を実行して通ることを確認する**

Run: `cd frontend && npx vitest run tests/state.test.js`
Expected: PASS(全件)

- [ ] **Step 5: コミットする**

```bash
git add frontend/src/intent/state.js frontend/tests/state.test.js
git commit -m "feat(frontend): フォーム状態と必須3フィールドの有効条件(M1 ws-5 Task 4)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## Task 5: api/session.js・api/client.js(トークン管理・APIクライアント)

**Files:**
- Create: `frontend/src/api/session.js`・`frontend/src/api/client.js`
- Test: `frontend/tests/session.test.js`・`frontend/tests/client.test.js`

**Interfaces:**
- Consumes: `POST /v1/auth/token`(IdPトークン交換)・`POST /v1/auth/refresh`(回転式)のHTTP契約(05 §5)
- Produces:
  - `createSession({ storage? }) → { current(): {accessToken, refreshToken}, save({access_token, refresh_token}), clear(), hasTokens() }`(storageは試験注入用・既定で実sessionStorage/localStorage)
  - `exchangeIdpToken(fetchImpl, { provider, idpToken }) → { access_token, refresh_token, … }`(ApiError throw)
  - `class ApiError extends Error { status, code }`
  - `createClient({ session, fetchImpl?, onSessionExpired? }) → { call(method, path, { body, signal }?) → data }`(envelope解釈・401→refresh→1回再送)

- [ ] **Step 1: session試験を書く**

`frontend/tests/session.test.js`:

```js
import { describe, expect, it, vi } from "vitest";
import { createSession, exchangeIdpToken } from "../src/api/session.js";

const memoryStorage = () => {
  const store = new Map();
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
};

const storages = () => ({ storage: { session: memoryStorage(), local: memoryStorage() } });
const ok = (body) => new Response(JSON.stringify(body), { status: 200 });

describe("createSession(トークン保存場所 — design §2.3)", () => {
  it("accessはsessionStorage+メモリ・refreshはlocalStorage", () => {
    const session = createSession(storages());
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    expect(session.current()).toEqual({ accessToken: "at-1", refreshToken: "rt-1" });
    expect(session.hasTokens()).toBe(true);
  });

  it("clearで両方消える", () => {
    const session = createSession(storages());
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    session.clear();
    expect(session.current()).toEqual({ accessToken: null, refreshToken: null });
    expect(session.hasTokens()).toBe(false);
  });

  it("refreshのみ保持でもhasTokens(再訪継続利用)", () => {
    const session = createSession(storages());
    session.save({ access_token: null, refresh_token: "rt-1" });
    expect(session.hasTokens()).toBe(true);
  });
});

describe("exchangeIdpToken(本番フローと共通の交換経路)", () => {
  it("200ならトークン2値を返す(リクエスト形式を検証)", async () => {
    const fetchImpl = vi.fn(async () => ok({ access_token: "at-1", refresh_token: "rt-1" }));
    const tokens = await exchangeIdpToken(fetchImpl, { provider: "google", idpToken: "idp-1" });
    expect(tokens).toEqual({ access_token: "at-1", refresh_token: "rt-1" });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/v1/auth/token");
    expect(JSON.parse(init.body)).toEqual({ provider: "google", idp_token: "idp-1" });
  });

  it("401 INVALID_IDP_TOKENはApiError(呼び出し側でパネルに表示)", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(
        JSON.stringify({ error: { code: "INVALID_IDP_TOKEN", message: "x", details: null } }),
        { status: 401 },
      ),
    );
    await expect(
      exchangeIdpToken(fetchImpl, { provider: "google", idpToken: "bad" }),
    ).rejects.toMatchObject({ status: 401, code: "INVALID_IDP_TOKEN" });
  });
});
```

- [ ] **Step 2: session.jsを実装する**

`frontend/src/api/session.js`:

```js
// トークン管理(design §2.3・告白3承認済み)。
// access: メモリ+sessionStorage(1時間・タブ閉で消える)
// refresh: localStorage(30日回転式・再訪時の継続利用)
export const ACCESS_KEY = "latch-access";
export const REFRESH_KEY = "latch-refresh";

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message ?? code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const parseEnvelope = async (res) => {
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, data?.error?.code ?? `HTTP_${res.status}`, data?.error?.message);
  }
  return data;
};

export const createSession = ({ storage } = {}) => {
  const sessionStore = storage?.session ?? sessionStorage;
  const localStore = storage?.local ?? localStorage;
  const memory = { accessToken: null };

  return {
    current: () => ({
      accessToken: memory.accessToken ?? sessionStore.getItem(ACCESS_KEY),
      refreshToken: localStore.getItem(REFRESH_KEY),
    }),
    save: ({ access_token, refresh_token }) => {
      if (access_token) {
        memory.accessToken = access_token;
        sessionStore.setItem(ACCESS_KEY, access_token);
      }
      if (refresh_token) localStore.setItem(REFRESH_KEY, refresh_token);
    },
    clear: () => {
      memory.accessToken = null;
      sessionStore.removeItem(ACCESS_KEY);
      localStore.removeItem(REFRESH_KEY);
    },
    hasTokens: () =>
      Boolean(memory.accessToken ?? sessionStore.getItem(ACCESS_KEY) ?? localStore.getItem(REFRESH_KEY)),
  };
};

// 開発用トークンパネル→POST /v1/auth/token(本番IdPフローと共通の交換経路・design §2.3)
export const exchangeIdpToken = (fetchImpl, { provider, idpToken }) =>
  fetchImpl("/v1/auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, idp_token: idpToken }),
  }).then(parseEnvelope);
```

- [ ] **Step 3: session試験を実行して通ることを確認する**

Run: `cd frontend && npx vitest run tests/session.test.js`
Expected: PASS

- [ ] **Step 4: client試験を書く**

`frontend/tests/client.test.js`:

```js
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createClient } from "../src/api/client.js";
import { createSession } from "../src/api/session.js";

const memoryStorage = () => {
  const store = new Map();
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
};

const makeSession = () =>
  createSession({ storage: { session: memoryStorage(), local: memoryStorage() } });

const jsonResponse = (status, body) =>
  new Response(JSON.stringify(body), { status });

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("createClient.call", () => {
  it("Authorizationヘッダー付きでリクエストし200のbodyを返す", async () => {
    const session = makeSession();
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { options: [], default_index: null }),
    );
    const client = createClient({ session, fetchImpl });
    const data = await client.call("GET", "/v1/intents/expiry-options");
    expect(data).toEqual({ options: [], default_index: null });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/v1/intents/expiry-options");
    expect(init.headers.Authorization).toBe("Bearer at-1");
  });

  it("bodyはJSON化されContent-Typeが付く", async () => {
    const session = makeSession();
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    const fetchImpl = vi.fn(async () => jsonResponse(200, { ok: true }));
    const client = createClient({ session, fetchImpl });
    await client.call("POST", "/v1/intents/parse", { body: { text: "…" } });
    const [, init] = fetchImpl.mock.calls[0];
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ text: "…" });
  });

  it("エラー時はenvelopeのcodeでApiError(messageは参考・確定値13)", async () => {
    const session = makeSession();
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    const fetchImpl = vi.fn(async () =>
      jsonResponse(422, {
        error: { code: "VALIDATION_ERROR", message: "time.start is in the past", details: null },
      }),
    );
    const client = createClient({ session, fetchImpl });
    await expect(client.call("POST", "/v1/intents")).rejects.toMatchObject({
      status: 422,
      code: "VALIDATION_ERROR",
    });
  });

  it("401→refresh成功→元リクエストを再送する(1回のみ)", async () => {
    const session = makeSession();
    session.save({ access_token: "expired", refresh_token: "rt-1" });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: "UNAUTHENTICATED", message: "x", details: null } }))
      .mockResolvedValueOnce(jsonResponse(200, { access_token: "at-2", refresh_token: "rt-2" }))
      .mockResolvedValueOnce(jsonResponse(200, { options: [], default_index: null }));
    const client = createClient({ session, fetchImpl });
    const data = await client.call("GET", "/v1/intents/expiry-options");
    expect(data).toEqual({ options: [], default_index: null });
    expect(fetchImpl.mock.calls[0][1].headers.Authorization).toBe("Bearer expired");
    expect(fetchImpl.mock.calls[1][0]).toBe("/v1/auth/refresh");
    expect(fetchImpl.mock.calls[2][1].headers.Authorization).toBe("Bearer at-2");
    expect(session.current().refreshToken).toBe("rt-2"); // 回転保存
  });

  it("401→refresh失敗→clear+onSessionExpired+ApiError(Review Focus 2)", async () => {
    const session = makeSession();
    session.save({ access_token: "expired", refresh_token: "rt-dead" });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: "UNAUTHENTICATED", message: "x", details: null } }))
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: "INVALID_REFRESH_TOKEN", message: "x", details: null } }));
    const onSessionExpired = vi.fn();
    const client = createClient({ session, fetchImpl, onSessionExpired });
    await expect(client.call("GET", "/v1/intents/expiry-options")).rejects.toMatchObject({
      status: 401,
      code: "UNAUTHENTICATED",
    });
    expect(onSessionExpired).toHaveBeenCalledTimes(1);
    expect(session.hasTokens()).toBe(false);
    expect(fetchImpl).toHaveBeenCalledTimes(2); // 再送しない
  });

  it("signalをfetchへ透過する(parseのabort用)", async () => {
    const session = makeSession();
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    const fetchImpl = vi.fn(async () => jsonResponse(200, { ok: 1 }));
    const client = createClient({ session, fetchImpl });
    const controller = new AbortController();
    await client.call("POST", "/v1/intents/parse", {
      body: { text: "…" },
      signal: controller.signal,
    });
    expect(fetchImpl.mock.calls[0][1].signal).toBe(controller.signal);
  });
});
```

- [ ] **Step 5: client.jsを実装する**

`frontend/src/api/client.js`:

```js
// APIクライアント(design §2.3)。全API通信はこのcall()を通る:
// - Authorizationヘッダー付与
// - 共通envelope {error:{code,message,details}} の解釈(ApiErrorはcodeで分岐・確定値13)
// - 401 UNAUTHENTICATED → POST /v1/auth/refresh を1回試み、成功なら元リクエストを再送
//   失敗ならセッション破棄してonSessionExpiredへ(トークンパネルへ戻す)
import { ApiError } from "./session.js";

const doFetch = async (fetchImpl, method, path, { body, signal, token } = {}) => {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetchImpl(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });
};

const refreshOnce = async (session, fetchImpl) => {
  const refreshToken = session.current().refreshToken;
  if (!refreshToken) return false;
  try {
    const res = await doFetch(fetchImpl, "POST", "/v1/auth/refresh", {
      body: { refresh_token: refreshToken },
    });
    if (!res.ok) return false;
    const data = await res.json();
    session.save({ access_token: data.access_token, refresh_token: data.refresh_token });
    return true;
  } catch {
    return false;
  }
};

export const createClient = ({ session, fetchImpl, onSessionExpired = () => {} }) => {
  const fetcher = fetchImpl ?? ((...args) => fetch(...args));
  return {
    call: async (method, path, { body, signal } = {}) => {
      let res = await doFetch(fetcher, method, path, {
        body,
        signal,
        token: session.current().accessToken,
      });
      if (res.status === 401 && session.current().refreshToken) {
        const refreshed = await refreshOnce(session, fetcher);
        if (refreshed) {
          res = await doFetch(fetcher, method, path, {
            body,
            signal,
            token: session.current().accessToken,
          });
        } else {
          session.clear();
          onSessionExpired();
        }
      } else if (res.status === 401) {
        session.clear();
        onSessionExpired();
      }
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        throw new ApiError(
          res.status,
          data?.error?.code ?? `HTTP_${res.status}`,
          data?.error?.message,
        );
      }
      return data;
    },
  };
};
```

- [ ] **Step 6: 試験を実行して通ることを確認する**

Run: `cd frontend && npx vitest run tests/session.test.js tests/client.test.js`
Expected: PASS(全件)

- [ ] **Step 7: コミットする**

```bash
git add frontend/src/api/ frontend/tests/session.test.js frontend/tests/client.test.js
git commit -m "feat(frontend): トークン管理とAPIクライアント(401リフレッシュ・envelope解釈)(M1 ws-5 Task 5)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## Task 6: intent/parseFlow.js(debounce 1秒・同一text抑制・in-flight abort・応答分岐)

**Files:**
- Create: `frontend/src/intent/parseFlow.js`
- Test: `frontend/tests/parseFlow.test.js`

**Interfaces:**
- Consumes: `send(text, { signal }) → Promise<parse応答>`(main.jsがclient.callで作って注入。ApiErrorをthrowし得る)
- Produces: `createParseFlow({ debounceMs?, send, onResult, onError }) → { input(text), retry(), dispose() }`
  - `input(text)`: テキスト変更の受付(1秒debounce → send)
  - `retry()`: 503時の「もう一度読み取る」(lastTextをクリアして即時再送)
  - `dispose()`: タイマー停止・in-flight abort

- [ ] **Step 1: 試験を書く**

`frontend/tests/parseFlow.test.js`(fake timers使用):

```js
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createParseFlow } from "../src/intent/parseFlow.js";

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

describe("createParseFlow(design §2.4)", () => {
  it("入力後1秒で送信する(即時ではない)", async () => {
    const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    expect(send).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(send).toHaveBeenCalledTimes(1);
    flow.dispose();
  });

  it("再入力でdebounceが延長される(最後の1秒だけが有効)", async () => {
    const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、");
    await vi.advanceTimersByTimeAsync(700);
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(700);
    expect(send).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);
    expect(send).toHaveBeenCalledTimes(1);
    flow.dispose();
  });

  it("空テキスト・空白のみは送信しない", async () => {
    const send = vi.fn();
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("");
    flow.input("   ");
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).not.toHaveBeenCalled();
  });

  it("同一テキストの再送をしない(60req/分対策・Review Focus 5の前提)", async () => {
    const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(1000);
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("in-flight中の再入力で古いsendはabortされ古い応答は破棄(Review Focus 5)", async () => {
    const first = deferred();
    const second = deferred();
    const send = vi
      .fn()
      .mockImplementationOnce((_text, { signal }) => {
        signal.addEventListener("abort", () => first.reject(new DOMException("aborted", "AbortError")));
        return first.promise;
      })
      .mockImplementationOnce(() => second.promise);
    const onResult = vi.fn();
    const flow = createParseFlow({ send, onResult, onError: vi.fn() });

    flow.input("1本目のテキスト");
    await vi.advanceTimersByTimeAsync(1000);
    flow.input("2本目のテキスト");
    await vi.advanceTimersByTimeAsync(1000);

    // 1本目のsignalはabort済み
    expect(send.mock.calls[0][1].signal.aborted).toBe(true);
    // 古い応答が後からresolveしてもonResultは呼ばれない
    second.resolve({ structured_intent: { second: true }, warnings: [] });
    await vi.advanceTimersByTimeAsync(0);
    first.resolve({ structured_intent: { first: true }, warnings: [] });
    await Promise.resolve();
    expect(onResult).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith({ structured_intent: { second: true }, warnings: [] });
    flow.dispose();
  });

  it("ApiErrorはonErrorへ(codeで分岐・確定値13)", async () => {
    const send = vi.fn(async () => {
      throw Object.assign(new Error("unavailable"), {
        name: "ApiError",
        status: 503,
        code: "LLM_UNAVAILABLE",
      });
    });
    const onError = vi.fn();
    const flow = createParseFlow({ send, onResult: vi.fn(), onError });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(1000);
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "LLM_UNAVAILABLE" }),
    );
  });

  it("retry()は同一テキストでも再送する(503時のもう一度読み取る)", async () => {
    const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(1000);
    expect(send).toHaveBeenCalledTimes(1);
    flow.retry();
    await vi.advanceTimersByTimeAsync(0);
    expect(send).toHaveBeenCalledTimes(2);
    flow.dispose();
  });

  it("dispose()でタイマー停止", async () => {
    const send = vi.fn(async () => ({}));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    flow.dispose();
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 試験を実行して失敗を確認する**

Run: `cd frontend && npx vitest run tests/parseFlow.test.js`
Expected: FAIL(モジュールなし)

- [ ] **Step 3: parseFlow.jsを実装する**

`frontend/src/intent/parseFlow.js`:

```js
// parse連携の送信制御(design §2.4・告白4承認済み: debounce 1秒)。
// - テキスト変更後1秒のdebounceで自動送信(03 §3「記入↓構造化」)
// - 同一テキストの再送なし(直近成功テキストと比較・60req/分と07 §1のコスト配慮)
// - in-flight中の再入力は古いリクエストをabortして最新で再送(AbortController)
// - 空テキスト・変更なしは送信しない・自動再試行なし(07 §1)

export const createParseFlow = ({ debounceMs = 1000, send, onResult, onError }) => {
  let timer = null;
  let controller = null;
  let lastText = null;
  let pending = null;

  const flush = async () => {
    timer = null;
    const text = pending;
    pending = null;
    if (!text || !text.trim() || text === lastText) return;

    controller?.abort();
    const current = new AbortController();
    controller = current;
    try {
      const data = await send(text, { signal: current.signal });
      if (current !== controller || current.signal.aborted) return; // 古い応答は破棄
      lastText = text;
      onResult(data);
    } catch (err) {
      if (current !== controller || current.signal.aborted) return;
      if (err?.name === "AbortError") return;
      onError(err);
    }
  };

  return {
    input: (text) => {
      pending = text;
      if (timer) clearTimeout(timer);
      timer = setTimeout(flush, debounceMs);
    },
    retry: () => {
      lastText = null;
      if (timer) clearTimeout(timer);
      timer = setTimeout(flush, 0);
    },
    dispose: () => {
      if (timer) clearTimeout(timer);
      timer = null;
      controller?.abort();
      controller = null;
      pending = null;
    },
  };
};
```

- [ ] **Step 4: 試験を実行して通ることを確認する**

Run: `cd frontend && npx vitest run tests/parseFlow.test.js`
Expected: PASS(全件)

- [ ] **Step 5: コミットする**

```bash
git add frontend/src/intent/parseFlow.js frontend/tests/parseFlow.test.js
git commit -m "feat(frontend): parse送信制御(debounce・同一text抑制・in-flight abort)(M1 ws-5 Task 6)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## Task 7: intent/conditions.js 表示(条件リストDOM・催促・soft/NG行)

**Files:**
- Create: `frontend/src/intent/conditions.js`(Task 8でエディタを追記)
- Test: `frontend/tests/conditions.test.js`(Task 8で追記)

**Interfaces:**
- Consumes: `state`(Task 4)・`formatTime/formatLocation/formatParticipants/formatBudget/formatCategory`(Task 3)
- Produces:
  - `ROW_DEFS`(5行の定義。`[{key, label, icon}]`)
  - `REQUIRED_NOTE_TEXT`(催促注記の固定文言)
  - `NG_NOTE_TEXT`(D-04注意文言・schema.pyのWARNING_MESSAGE_NG_DOWNGRADEDと同一)
  - `renderConditions({ listEl, state, now }) → void`(listElの中身を5行+soft行+NG行で再構成。催促行は `data-missing="true"` + `.condition-missing`)

- [ ] **Step 1: 試験を書く**

`frontend/tests/conditions.test.js`:

```js
import { beforeEach, describe, expect, it } from "vitest";
import { NG_NOTE_TEXT, REQUIRED_NOTE_TEXT, renderConditions } from "../src/intent/conditions.js";
import { applyParseResult, createFormState } from "../src/intent/state.js";

const NOW = "2026-09-27T12:00:00+09:00";

const PARSED = {
  category: { primary: "drinking", secondary: null },
  alcohol_involved: true,
  time: { start: "2026-09-27T20:00:00+09:00", end: null },
  location: { name: "天文館", radius_m: null },
  budget: { max: 5000, currency: "JPY" },
  participants: { min: 2, max: 4 },
  soft_constraints: ["軽く飲みたい"],
  negative_constraints: [],
  ng_unverifiable: ["会社関係の人は避けたい"],
};

beforeEach(() => {
  document.body.innerHTML = '<div class="condition-list" id="conditionList"></div>';
});

const listEl = () => document.querySelector("#conditionList");

describe("renderConditions(design §2.5・§2.7・§2.8)", () => {
  it("parse結果から5行+soft行+NG行を構成する(文言はプロトタイプ固定)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderConditions({ listEl: listEl(), state, now: NOW });
    const rows = [...listEl().querySelectorAll(".condition-row")];
    expect(rows.map((r) => r.dataset.label)).toEqual([
      "時間",
      "場所",
      "人数",
      "予算",
      "目的",
      "その他",
      "NG",
    ]);
    const text = (label) =>
      rows.find((r) => r.dataset.label === label).querySelector(".condition-value").textContent;
    expect(text("時間")).toBe("今日 20:00以降");
    expect(text("場所")).toBe("天文館");
    expect(text("人数")).toBe("2〜4人");
    expect(text("予算")).toBe("ひとり5,000円まで");
    expect(text("目的")).toBe("飲み");
    expect(text("その他")).toBe("軽く飲みたい");
    expect(text("NG")).toBe("会社関係の人は避けたい");
  });

  it("必須3欠落行は催促状態(data-missing・「指定なし」)", () => {
    renderConditions({ listEl: listEl(), state: createFormState(), now: NOW });
    const missing = [...listEl().querySelectorAll('.condition-row[data-missing="true"]')];
    expect(missing.map((r) => r.dataset.label)).toEqual(["時間", "場所", "目的"]);
    for (const row of missing) {
      expect(row.querySelector(".condition-value").textContent).toBe("指定なし");
    }
  });

  it("soft行はph-flag・追加行と同型", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderConditions({ listEl: listEl(), state, now: NOW });
    const softRow = listEl().querySelector('[data-label="その他"]');
    expect(softRow.querySelector(".condition-icon i").className).toContain("ph-flag");
  });

  it("NG行はph-warning・data-label=NG・直下に注意文言(02 D-04)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderConditions({ listEl: listEl(), state, now: NOW });
    const ngRow = listEl().querySelector('[data-label="NG"]');
    expect(ngRow.querySelector(".condition-icon i").className).toContain("ph-warning");
    const note = ngRow.querySelector(".ng-note");
    expect(note.textContent).toBe(NG_NOTE_TEXT);
    expect(NG_NOTE_TEXT).toBe("この条件は確実には除外できません。参考条件として扱います");
  });

  it("各行に編集ボタン(aria-label)がある", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderConditions({ listEl: listEl(), state, now: NOW });
    for (const row of listEl().querySelectorAll(".condition-row")) {
      const button = row.querySelector(".edit-button");
      expect(button).not.toBeNull();
      expect(button.getAttribute("aria-label")).toBe(`${row.dataset.label}を編集`);
    }
  });

  it("再描画で行が重複しない(replaceChildren)", () => {
    const state = applyParseResult(createFormState(), PARSED);
    renderConditions({ listEl: listEl(), state, now: NOW });
    renderConditions({ listEl: listEl(), state, now: NOW });
    expect(listEl().querySelectorAll(".condition-row").length).toBe(7);
  });
});
```

- [ ] **Step 2: 試験を実行して失敗を確認する**

Run: `cd frontend && npx vitest run tests/conditions.test.js`
Expected: FAIL(モジュールなし)

- [ ] **Step 3: conditions.jsを実装する(表示部)**

`frontend/src/intent/conditions.js`:

```js
// 条件リストのDOM構成(design §2.5・§2.7・§2.8)。
// 行構造・ラベル・アイコンはプロトタイプ固定(00 運用ルール6)。値はformat.jsの
// 純関数が組み立てる。ユーザー入力はすべてtextContentへ設定する(innerHTMLに埋めない)。
import {
  formatBudget,
  formatCategory,
  formatLocation,
  formatParticipants,
  formatTime,
} from "./format.js";
import { missingRequired } from "./state.js";

export const REQUIRED_NOTE_TEXT = "時間・場所・目的を指定すると預けられます";
// 02 D-04・03 §3の注意文言(schema.py WARNING_MESSAGE_NG_DOWNGRADED と同一の固定文字列。
// サーバ応答のmessageは参考にしか使わない — 確定値13)
export const NG_NOTE_TEXT = "この条件は確実には除外できません。参考条件として扱います";

export const ROW_DEFS = [
  { key: "time", label: "時間", icon: "ph-clock" },
  { key: "location", label: "場所", icon: "ph-map-pin" },
  { key: "participants", label: "人数", icon: "ph-users-three" },
  { key: "budget", label: "予算", icon: "ph-currency-jpy" },
  { key: "category", label: "目的", icon: "ph-flag" },
];

const createEditButton = (label) => {
  const button = document.createElement("button");
  button.className = "edit-button";
  button.type = "button";
  button.setAttribute("aria-label", `${label}を編集`);
  button.innerHTML = '<i class="ph ph-pencil-simple" aria-hidden="true"></i>';
  return button;
};

const buildRow = ({ label, icon, value, extraClass = "" }) => {
  const row = document.createElement("div");
  row.className = `condition-row ${extraClass}`.trim();
  row.dataset.label = label;
  const iconBox = document.createElement("div");
  iconBox.className = "condition-icon";
  iconBox.innerHTML = `<i class="ph ${icon}" aria-hidden="true"></i>`;
  const labelEl = document.createElement("span");
  labelEl.className = "condition-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("span");
  valueEl.className = "condition-value";
  valueEl.textContent = value;
  row.append(iconBox, labelEl, valueEl, createEditButton(label));
  return row;
};

export const renderConditions = ({ listEl, state, now }) => {
  const s = state.structured;
  const values = {
    time: formatTime(s.time.start, s.time.end, now),
    location: formatLocation(s.location),
    participants: formatParticipants(s.participants),
    budget: formatBudget(s.budget),
    category: formatCategory(s.category),
  };
  const missing = new Set(missingRequired(state));

  const rows = ROW_DEFS.map((def) =>
    buildRow({
      label: def.label,
      icon: def.icon,
      value: values[def.key],
      extraClass: missing.has(def.label) ? "condition-missing" : "",
    }),
  );
  for (const row of rows) {
    if (row.classList.contains("condition-missing")) row.dataset.missing = "true";
  }

  for (const soft of state.softRows) {
    const row = buildRow({ label: "その他", icon: "ph-flag", value: soft.text });
    row.dataset.rowId = soft.id;
    rows.push(row);
  }
  for (const ng of state.ngRows) {
    const row = buildRow({
      label: "NG",
      icon: "ph-warning",
      value: ng.text,
      extraClass: "ng-row",
    });
    row.dataset.rowId = ng.id;
    const note = document.createElement("p");
    note.className = "ng-note";
    note.textContent = NG_NOTE_TEXT;
    row.querySelector(".condition-value").append(note);
    rows.push(row);
  }

  listEl.replaceChildren(...rows);
};
```

- [ ] **Step 4: 試験を実行して通ることを確認する**

Run: `cd frontend && npx vitest run tests/conditions.test.js`
Expected: PASS(全件)

- [ ] **Step 5: コミットする**

```bash
git add frontend/src/intent/conditions.js frontend/tests/conditions.test.js
git commit -m "feat(frontend): 条件リストの動的構成と催促・soft/NG行(M1 ws-5 Task 7)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## Task 8: intent/conditions.js エディタ(タイプ別構造化入力・条件の追加)

**Files:**
- Modify: `frontend/src/intent/conditions.js`(エディタ部を追記)
- Test: `frontend/tests/conditions.test.js`(追記)

**Interfaces:**
- Consumes: `state`(Task 4)・`isoToDateTimeLocal/dateTimeLocalToIso`(Task 3)・`ROW_DEFS`(Task 7)
- Produces:
  - `beginRowEdit({ rowEl, state, rerender }) → void`(行タイプを判定し構造化エディタを開く。Enter/✓でstateへ書き戻しrerender()・Escapeで取消rerender())
  - `attachAddCondition({ formEl, state, rerender }) → void`(「条件を追加」フォーム。label(その他・曜日・移動・雰囲気)はUI上の補助で、softRowsへtextのみ追加 — D-19補足)

- [ ] **Step 1: 試験を追記する**

`frontend/tests/conditions.test.js` へ追記(import部に `beginRowEdit`・`attachAddCondition`・`applyParseFallback` を追加):

```js
describe("beginRowEdit(タイプ別エディタ・design §2.5)", () => {
  const editState = () => applyParseResult(createFormState(), PARSED);

  // main.jsの委譲リスナーを介さずbeginRowEditを直接呼ぶ(rerenderは再描画のみ)
  const openEditor = (state, label) => {
    renderConditions({ listEl: listEl(), state, now: NOW });
    const row = listEl().querySelector(`[data-label="${label}"]`);
    beginRowEdit({
      rowEl: row,
      state,
      rerender: () => renderConditions({ listEl: listEl(), state, now: NOW }),
    });
    return listEl().querySelector(`[data-label="${label}"]`);
  };

  it("時間: datetime-localで確定するとstartがtz-aware ISOへ書き換わる", () => {
    const state = editState();
    const row = openEditor(state, "時間");
    const input = row.querySelector('input[type="datetime-local"]');
    expect(input.value).toBe("2026-09-27T20:00"); // ISO→datetime-local(JST)
    input.value = "2026-09-28T19:30";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.time.start).toBe("2026-09-28T19:30:00+09:00");
  });

  it("時間: 未入力で確定するとstart=nullへ戻る(催促状態・Review Focus 4)", () => {
    const state = editState();
    const row = openEditor(state, "時間");
    row.querySelector('input[type="datetime-local"]').value = "";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.time.start).toBe(null);
  });

  it("場所: name+半径(任意)を書き戻す", () => {
    const state = editState();
    const row = openEditor(state, "場所");
    row.querySelector('input[name="name"]').value = "天文館周辺";
    row.querySelector('input[name="radius"]').value = "2000";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.location).toEqual({ name: "天文館周辺", radius_m: 2000 });
  });

  it("人数: min/max数値(1〜4)を書き戻す・空はnull", () => {
    const state = editState();
    const row = openEditor(state, "人数");
    row.querySelector('input[name="min"]').value = "3";
    row.querySelector('input[name="max"]').value = "";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.participants).toEqual({ min: 3, max: null });
  });

  it("予算: max数値を書き戻す・空はnull", () => {
    const state = editState();
    const row = openEditor(state, "予算");
    row.querySelector('input[name="max"]').value = "8000";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.budget.max).toBe(8000);
  });

  it("目的: primaryのselect+secondaryテキスト", () => {
    const state = editState();
    const row = openEditor(state, "目的");
    row.querySelector("select").value = "meal";
    row.querySelector('input[name="secondary"]').value = "焼肉";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.structured.category).toEqual({ primary: "meal", secondary: "焼肉" });
  });

  it("soft行: テキスト編集でsoftRowsが書き換わる", () => {
    const state = editState();
    const row = openEditor(state, "その他");
    row.querySelector("input").value = "静かなお店";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.softRows[0].text).toBe("静かなお店");
  });

  it("NG行: テキスト編集でngRowsが書き換わる(削除UIなし・design §2.8)", () => {
    const state = editState();
    const row = openEditor(state, "NG");
    row.querySelector("input").value = "取引先の人は避けたい";
    row.querySelector(".condition-edit").requestSubmit();
    expect(state.ngRows[0].text).toBe("取引先の人は避けたい");
  });

  it("Escapeで取り消し(stateは不変)", () => {
    const state = editState();
    const row = openEditor(state, "場所");
    row.querySelector('input[name="name"]').value = "別の場所";
    row.querySelector(".condition-edit").dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(state.structured.location.name).toBe("天文館");
  });
});

describe("attachAddCondition(条件の追加)", () => {
  it("フォーム送信でsoftRowsへtextのみ追加される(ラベルはUI補助)", () => {
    document.body.innerHTML += `
      <form class="add-row" id="addConditionForm">
        <select id="newConditionLabel"><option>雰囲気</option></select>
        <input id="newConditionValue" aria-label="追加する条件" />
      </form>`;
    const state = applyParseResult(createFormState(), PARSED);
    const form = document.querySelector("#addConditionForm");
    attachAddCondition({ formEl: form, state, rerender: () => {} });
    document.querySelector("#newConditionValue").value = "落ち着いた雰囲気";
    form.requestSubmit();
    expect(state.softRows.map((r) => r.text)).toContain("落ち着いた雰囲気");
    expect(JSON.stringify(state.softRows)).not.toContain("雰囲気"); // ラベルは格納しない
  });
});
```

- [ ] **Step 2: 試験を実行して失敗を確認する**

Run: `cd frontend && npx vitest run tests/conditions.test.js`
Expected: 追加分FAIL(beginRowEdit/attachAddCondition 未export)

- [ ] **Step 3: エディタを実装する(conditions.jsへ追記)**

`frontend/src/intent/conditions.js` へ追記:

```js
// ---------------------------------------------------------------------------
// 行単位インライン編集(タイプ別構造化入力・design §2.5)
// 通常経路の修正と422フォールバックの手動入力が同一UI(確定値9「フォームは
// フォールバック専用で主UIにしない」の実現)。

import { dateTimeLocalToIso, isoToDateTimeLocal } from "./format.js";

const num = (value) => (value === "" || value == null ? null : Number(value));

const buildEditForm = (innerHtml) => {
  const form = document.createElement("form");
  form.className = "condition-edit";
  form.innerHTML = `
    ${innerHtml}
    <button type="submit" aria-label="変更を保存"><i class="ph ph-check" aria-hidden="true"></i></button>
  `;
  return form;
};

// 行タイプ別の入力要素と確定時のstate書き戻し
const editors = {
  time: {
    fields: (state) => `
      <input type="datetime-local" name="start" aria-label="時間を編集"
             value="${isoToDateTimeLocal(state.structured.time.start)}" />`,
    commit: (state, form) => {
      state.structured.time.start = dateTimeLocalToIso(form.elements.start.value);
    },
  },
  location: {
    fields: (state) => `
      <input name="name" aria-label="場所を編集" required
             value="${state.structured.location.name ?? ""}" />
      <input type="number" name="radius" min="1" aria-label="半径(メートル・任意)"
             value="${state.structured.location.radius_m ?? ""}" placeholder="半径m(任意)" />`,
    commit: (state, form) => {
      state.structured.location.name = form.elements.name.value.trim() || null;
      state.structured.location.radius_m = num(form.elements.radius.value);
    },
  },
  participants: {
    fields: (state) => `
      <input type="number" name="min" min="1" max="4" aria-label="最小人数"
             value="${state.structured.participants.min ?? ""}" placeholder="最小" />
      <input type="number" name="max" min="1" max="4" aria-label="最大人数"
             value="${state.structured.participants.max ?? ""}" placeholder="最大" />`,
    commit: (state, form) => {
      state.structured.participants.min = num(form.elements.min.value);
      state.structured.participants.max = num(form.elements.max.value);
    },
  },
  budget: {
    fields: (state) => `
      <input type="number" name="max" min="0" aria-label="予算上限(円)"
             value="${state.structured.budget.max ?? ""}" placeholder="円" />`,
    commit: (state, form) => {
      state.structured.budget.max = num(form.elements.max.value);
    },
  },
  category: {
    fields: (state) => `
      <select name="primary" aria-label="目的の種類">
        <option value="meal"${state.structured.category.primary === "meal" ? " selected" : ""}>食事</option>
        <option value="drinking"${state.structured.category.primary === "drinking" ? " selected" : ""}>飲み</option>
        <option value="activity"${state.structured.category.primary === "activity" ? " selected" : ""}>アクティビティ</option>
      </select>
      <input name="secondary" aria-label="目的の詳細(任意)"
             value="${state.structured.category.secondary ?? ""}" placeholder="詳細(任意)" />`,
    commit: (state, form) => {
      state.structured.category.primary = form.elements.primary.value || null;
      state.structured.category.secondary = form.elements.secondary.value.trim() || null;
    },
  },
};

const textEditor = {
  fields: (current) => `
    <input name="text" aria-label="条件を編集" required value="${current ?? ""}" />`,
  commit: (target, form) => {
    target.text = form.elements.text.value.trim();
  },
};

export const beginRowEdit = ({ rowEl, state, rerender }) => {
  if (rowEl.querySelector(".condition-edit")) return;
  const label = rowEl.dataset.label;
  const def = ROW_DEFS.find((d) => d.label === label);

  let editor;
  let commitTarget = null;
  if (def) {
    editor = editors[def.key];
  } else {
    // soft行・NG行: テキスト編集(soft行と同一UI・design §2.8)
    const list = label === "NG" ? state.ngRows : state.softRows;
    commitTarget = list.find((r) => r.id === rowEl.dataset.rowId) ?? null;
    editor = textEditor;
  }

  const valueEl = rowEl.querySelector(".condition-value");
  const currentText = valueEl.childNodes[0].textContent;
  const form = buildEditForm(
    def ? editor.fields(state) : editor.fields(currentText),
  );

  const finish = (save) => {
    if (save) {
      if (def) {
        editor.commit(state, form);
      } else if (commitTarget) {
        editor.commit(commitTarget, form);
      }
    }
    rerender(); // 取消・確定とも再描画で元の行構成へ戻す
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    finish(true);
  });
  form.addEventListener("keydown", (event) => {
    if (event.key === "Escape") finish(false);
  });

  const editButton = rowEl.querySelector(".edit-button");
  valueEl.remove();
  editButton.replaceWith(form);
  const first = form.querySelector("input, select");
  if (first) {
    first.focus();
    if (first.select) first.select();
  }
};

// 「条件を追加」(その他・曜日・移動・雰囲気 — ラベルはUI上の入力補助で
// 格納先はすべてsoft_constraints・API送信値はtextのみ: D-19補足・確定値3)
export const attachAddCondition = ({ formEl, state, rerender }) => {
  formEl.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = formEl.querySelector("input[aria-label='追加する条件']");
    const text = input.value.trim();
    if (!text) return;
    state.softRows.push({ id: `soft-${Date.now()}-${state.softRows.length}`, text });
    input.value = "";
    formEl.hidden = true; // 追加フォームを閉じ、「条件を追加」ボタンはrerender側(main.js)で再表示
    rerender();
  });
};
```

**実装メモ**: happy-dom の `requestSubmit()` は submit イベントを発火する。`form.elements.<name>` は name 属性でアクセスする。soft/NG行の特定は `data-row-id`(renderConditionsがsoft/ng行のみ設定)で行う。

- [ ] **Step 4: 試験を実行して通ることを確認する**

Run: `cd frontend && npx vitest run tests/conditions.test.js`
Expected: PASS(表示分+エディタ分すべて)

- [ ] **Step 5: コミットする**

```bash
git add frontend/src/intent/conditions.js frontend/tests/conditions.test.js
git commit -m "feat(frontend): タイプ別インラインエディタと条件の追加(M1 ws-5 Task 8)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## Task 9: intent/expiry.js(expiry-options取得・select構成)

**Files:**
- Create: `frontend/src/intent/expiry.js`
- Test: `frontend/tests/expiry.test.js`

**Interfaces:**
- Consumes: `client.call("GET", "/v1/intents/expiry-options?time_start=…")`(Task 5・Task 2の契約)
- Produces:
  - `fetchExpiryOptions(client, timeStartIso | null) → Promise<{options: [{label, expires_at, selectable}], default_index}>`
  - `applyExpiryOptions(selectEl, data) → number`(selectのoptionを再構成し選択したindexを返す)
  - `selectedExpiry(selectEl) → { label, expiresAt }`(選択中の値。value=絶対時刻ISO)

- [ ] **Step 1: 試験を書く**

`frontend/tests/expiry.test.js`:

```js
import { beforeEach, describe, expect, it } from "vitest";
import { applyExpiryOptions, fetchExpiryOptions, selectedExpiry } from "../src/intent/expiry.js";

beforeEach(() => {
  document.body.innerHTML = '<select id="expiry"></select>';
});

const select = () => document.querySelector("#expiry");

const DATA = {
  options: [
    { label: "今夜 23:30", expires_at: "2026-09-27T23:30:00+09:00", selectable: true },
    { label: "明日 12:00", expires_at: "2026-09-28T12:00:00+09:00", selectable: true },
    { label: "明日 23:30", expires_at: "2026-09-28T23:30:00+09:00", selectable: true },
    { label: "3日後まで", expires_at: "2026-09-30T21:00:00+09:00", selectable: true },
  ],
  default_index: 1,
};

describe("fetchExpiryOptions(design §2.6)", () => {
  it("time_startなしでGETする", async () => {
    const calls = [];
    const client = { call: async (method, path) => (calls.push([method, path]), DATA) };
    await fetchExpiryOptions(client, null);
    expect(calls).toEqual([["GET", "/v1/intents/expiry-options"]]);
  });

  it("time_startはURLエンコードしてクエリへ", async () => {
    const calls = [];
    const client = { call: async (method, path) => (calls.push(path), DATA) };
    await fetchExpiryOptions(client, "2026-09-27T20:00:00+09:00");
    expect(calls[0]).toBe(
      `/v1/intents/expiry-options?time_start=${encodeURIComponent("2026-09-27T20:00:00+09:00")}`,
    );
  });
});

describe("applyExpiryOptions", () => {
  it("label表示・value=絶対時刻・default_indexを選択", () => {
    const idx = applyExpiryOptions(select(), DATA);
    expect(idx).toBe(1);
    const options = [...select().options];
    expect(options.map((o) => o.textContent)).toEqual([
      "今夜 23:30",
      "明日 12:00",
      "明日 23:30",
      "3日後まで",
    ]);
    expect(options[0].value).toBe("2026-09-27T23:30:00+09:00");
    expect(select().selectedIndex).toBe(1);
  });

  it("selectable=falseの選択肢はdisabled(過ぎた選択肢・確定値6)", () => {
    const passed = {
      options: DATA.options.map((o, i) => (i === 0 ? { ...o, selectable: false } : o)),
      default_index: 1,
    };
    applyExpiryOptions(select(), passed);
    expect(select().options[0].disabled).toBe(true);
    expect(select().options[1].disabled).toBe(false);
  });

  it("default_index=null(時間未確定)は最初の選択可能肢を選ぶ(Review Focus 3)", () => {
    const noDefault = {
      options: DATA.options.map((o, i) => (i === 0 ? { ...o, selectable: false } : o)),
      default_index: null,
    };
    const idx = applyExpiryOptions(select(), noDefault);
    expect(idx).toBe(1);
    expect(select().value).toBe("2026-09-28T12:00:00+09:00");
  });
});

describe("selectedExpiry", () => {
  it("選択中のlabelと絶対時刻を返す(クライアントは絶対時刻をexpires_atとして送る)", () => {
    applyExpiryOptions(select(), DATA);
    expect(selectedExpiry(select())).toEqual({
      label: "明日 12:00",
      expiresAt: "2026-09-28T12:00:00+09:00",
    });
  });
});
```

- [ ] **Step 2: 試験を実行して失敗を確認する**

Run: `cd frontend && npx vitest run tests/expiry.test.js`
Expected: FAIL(モジュールなし)

- [ ] **Step 3: expiry.jsを実装する**

`frontend/src/intent/expiry.js`:

```js
// 有効期限の選択肢構成(design §2.6)。
// 4選択肢の絶対時刻・既定選択・selectableはすべてGET /v1/intents/expiry-options
// (completion.pyの単一実装)の応答から組み立てる — フロントで計算しない(07 §2)。
export const fetchExpiryOptions = (client, timeStartIso) => {
  const query = timeStartIso
    ? `?time_start=${encodeURIComponent(timeStartIso)}`
    : "";
  return client.call("GET", `/v1/intents/expiry-options${query}`);
};

export const applyExpiryOptions = (selectEl, data) => {
  selectEl.replaceChildren();
  for (const option of data.options) {
    const el = document.createElement("option");
    el.textContent = option.label;
    el.value = option.expires_at; // クライアントは絶対時刻を送る(03 §3)
    el.disabled = !option.selectable; // 過ぎた選択肢(確定値6)
    selectEl.append(el);
  }
  const fallback = data.options.findIndex((o) => o.selectable);
  const idx = data.default_index ?? fallback;
  if (idx >= 0) selectEl.selectedIndex = idx;
  return idx;
};

export const selectedExpiry = (selectEl) => {
  const option = selectEl.selectedOptions[0];
  if (!option) return { label: null, expiresAt: null };
  return { label: option.textContent, expiresAt: option.value };
};
```

- [ ] **Step 4: 試験を実行して通ることを確認する**

Run: `cd frontend && npx vitest run tests/expiry.test.js`
Expected: PASS(全件)

- [ ] **Step 5: コミットする**

```bash
git add frontend/src/intent/expiry.js frontend/tests/expiry.test.js
git commit -m "feat(frontend): 期限選択肢の取得とselect構成(expiry-options経由)(M1 ws-5 Task 9)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## Task 10: intent/save.js(active/draft保存・エラー表示マップ)

**Files:**
- Create: `frontend/src/intent/save.js`
- Test: `frontend/tests/save.test.js`

**Interfaces:**
- Consumes: `client.call`(Task 5)・`buildCreateRequest`(Task 3)・`canSubmit/canDraft`(Task 4)
- Produces:
  - `errorPlacement(err) → { target: "time"|"location"|"global", note: string }`(design §2.9のエラー表示マップ)
  - `createSaveFlow({ client, getState, getOptions, onBusy, onActiveSaved, onDraftSaved, onFormError }) → { saveActive(), saveDraft() }`
    - `getOptions() → { visibility, notificationLevel, expiresAt }`(main.jsが預け方パネルから組み立てる)
    - `onFormError(placement)` は行/グローバルの表示先をmain.jsへ委ねる
    - 二重送信防止: 保存リクエスト中は `onBusy(true)` → 完了で `onBusy(false)`

- [ ] **Step 1: 試験を書く**

`frontend/tests/save.test.js`:

```js
import { describe, expect, it, vi } from "vitest";
import { createSaveFlow, errorPlacement } from "../src/intent/save.js";
import { applyParseFallback, applyParseResult, createFormState } from "../src/intent/state.js";

const NOW = "2026-09-27T12:00:00+09:00";

const PARSED = {
  category: { primary: "drinking", secondary: null },
  alcohol_involved: true,
  time: { start: "2026-09-27T20:00:00+09:00", end: null },
  location: { name: "天文館", radius_m: null },
  budget: { max: 5000, currency: "JPY" },
  participants: { min: 2, max: 4 },
  soft_constraints: ["軽く飲みたい"],
  negative_constraints: [],
  ng_unverifiable: ["会社関係の人は避けたい"],
};

const apiError = (status, code, message) =>
  Object.assign(new Error(message ?? code), { name: "ApiError", status, code });

const makeFlow = (state, callImpl) => {
  const flow = {};
  flow.client = { call: vi.fn(callImpl) };
  flow.getOptions = () => ({
    visibility: "hidden_until_match",
    notificationLevel: "proposals_only",
    expiresAt: "2026-09-27T23:30:00+09:00",
  });
  flow.handlers = {
    onBusy: vi.fn(),
    onActiveSaved: vi.fn(),
    onDraftSaved: vi.fn(),
    onFormError: vi.fn(),
  };
  flow.save = createSaveFlow({
    client: flow.client,
    getState: () => state,
    getOptions: flow.getOptions,
    ...flow.handlers,
  });
  return flow;
};

describe("errorPlacement(design §2.9の表示マップ)", () => {
  it("GEOCODING_FAILED → 場所行", () => {
    expect(errorPlacement(apiError(422, "GEOCODING_FAILED"))).toMatchObject({
      target: "location",
    });
  });

  it("VALIDATION_ERRORのtime.start/expires_at → 時間行", () => {
    expect(
      errorPlacement(apiError(422, "VALIDATION_ERROR", "time.start is in the past")),
    ).toMatchObject({ target: "time" });
    expect(
      errorPlacement(apiError(422, "VALIDATION_ERROR", "expires_at exceeds 7 days")),
    ).toMatchObject({ target: "time" });
  });

  it("未知のVALIDATION_ERROR message → グローバル(Review Focus 6)", () => {
    expect(
      errorPlacement(apiError(422, "VALIDATION_ERROR", "category is required")),
    ).toMatchObject({ target: "global" });
    expect(errorPlacement(apiError(422, "VALIDATION_ERROR", null))).toMatchObject({
      target: "global",
    });
  });

  it("UNDER_AGE / ACTIVE_INTENT_LIMIT / RATE_LIMITED / 503系 → グローバル", () => {
    for (const code of [
      "UNDER_AGE",
      "ACTIVE_INTENT_LIMIT",
      "RATE_LIMITED",
      "LLM_UNAVAILABLE",
      "DEPENDENCY_UNAVAILABLE",
    ]) {
      expect(errorPlacement(apiError(code === "RATE_LIMITED" ? 429 : 422, code))).toMatchObject({
        target: "global",
      });
    }
  });
});

describe("createSaveFlow", () => {
  it("saveActive: 必須3充足ならPOST /v1/intents(status=active)し201でonActiveSaved", async () => {
    const state = { ...applyParseResult(createFormState(), PARSED), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    const calls = [];
    const flow = makeFlow(state, async (method, path, opts) => {
      calls.push([method, path, opts?.body]);
      return { intent: { id: "i-1", status: "active" } };
    });
    await flow.save.saveActive();
    expect(calls[0][0]).toBe("POST");
    expect(calls[0][1]).toBe("/v1/intents");
    expect(calls[0][2].status).toBe("active");
    expect(calls[0][2].structured_intent.expires_at).toBe("2026-09-27T23:30:00+09:00");
    expect(flow.handlers.onActiveSaved).toHaveBeenCalledTimes(1);
    expect(flow.handlers.onFormError).not.toHaveBeenCalled();
  });

  it("saveActive: 必須3欠落なら送信しない(フロント事前ブロック・確定値4)", async () => {
    const state = { ...createFormState(), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    const flow = makeFlow(state, async () => ({}));
    await flow.save.saveActive();
    expect(flow.client.call).not.toHaveBeenCalled();
  });

  it("saveDraft: rawTextのみで保存できる(fallback状態でも・Review Focus 1)", async () => {
    const parsed = { ...applyParseResult(createFormState(), PARSED), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    const state = applyParseFallback(parsed); // 必須3が揃わない状態
    const calls = [];
    const flow = makeFlow(state, async (method, path, opts) => {
      calls.push(opts?.body);
      return { intent: { id: "i-2", status: "draft" } };
    });
    await flow.save.saveDraft();
    expect(calls[0].status).toBe("draft");
    expect(flow.handlers.onDraftSaved).toHaveBeenCalledTimes(1);
  });

  it("saveDraft: テキスト空なら送信しない", async () => {
    const flow = makeFlow(createFormState(), async () => ({}));
    await flow.save.saveDraft();
    expect(flow.client.call).not.toHaveBeenCalled();
  });

  it("エラー時はonFormError(placement)へ", async () => {
    const state = { ...applyParseResult(createFormState(), PARSED), rawText: "x今日20時以降、天文館で軽く飲みたい。" };
    const flow = makeFlow(state, async () => {
      throw apiError(422, "GEOCODING_FAILED");
    });
    await flow.save.saveActive();
    expect(flow.handlers.onFormError).toHaveBeenCalledWith(
      expect.objectContaining({ target: "location" }),
    );
    expect(flow.handlers.onActiveSaved).not.toHaveBeenCalled();
  });

  it("保存中は二重送信しない(onBusy・Review Focus 8)", async () => {
    const state = { ...applyParseResult(createFormState(), PARSED), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    let release;
    const gate = new Promise((res) => (release = res));
    const busyStates = [];
    const flow = makeFlow(state, async () => {
      busyStates.push(flow.handlers.onBusy.mock.calls.at(-1)?.[0] ?? null);
      await gate;
      return { intent: {} };
    });
    const first = flow.save.saveActive();
    await Promise.resolve();
    await flow.save.saveActive(); // in-flight中の再押下
    release();
    await first;
    expect(flow.client.call).toHaveBeenCalledTimes(1);
    expect(busyStates[0]).toBe(true);
    expect(flow.handlers.onBusy).toHaveBeenLastCalledWith(false);
  });
});
```

- [ ] **Step 2: 試験を実行して失敗を確認する**

Run: `cd frontend && npx vitest run tests/save.test.js`
Expected: FAIL(モジュールなし)

- [ ] **Step 3: save.jsを実装する**

`frontend/src/intent/save.js`:

```js
// 保存API接続(active/draft)とエラー表示マップ(design §2.9)。
// 検証の強制はサーバ側(03 §3)− フロントはcanSubmit/canDraftで事前にブロックし、
// サーバ422/429/503はcodeで分岐して表示位置へ振り分ける(確定値13)。
import { buildCreateRequest } from "./format.js";
import { canDraft, canSubmit } from "./state.js";

// design §2.9のエラー表示マップ。VALIDATION_ERRORのみmessageの先頭一致で
// 表示位置を推定する(確定値13「messageは参考にしか使わない」の唯一の例外。
// 未知の形式はグローバル表示へ落ちる — Review Focus 6)
export const errorPlacement = (err) => {
  switch (err?.code) {
    case "GEOCODING_FAILED":
      return { target: "location", note: "場所が見つかりません。条件リストの場所を修正してください。" };
    case "UNDER_AGE":
      return { target: "global", note: "飲酒を含むIntentは20歳以上の方のみ作成できます。" };
    case "ACTIVE_INTENT_LIMIT":
      return {
        target: "global",
        note: "預けられるIntentはActive 5件までです。停止中・期限切れのIntentを確認してください。",
      };
    case "RATE_LIMITED":
      return { target: "global", note: "操作が集中しています。少し時間をおいてもう一度お試しください。" };
    case "LLM_UNAVAILABLE":
    case "DEPENDENCY_UNAVAILABLE":
      return { target: "global", note: "ただいま混み合っています。もう一度お試しください。" };
    case "VALIDATION_ERROR": {
      const message = err.message ?? "";
      if (message.startsWith("time.start") || message.startsWith("expires_at")) {
        return { target: "time", note: "時刻を修正してください(現在〜7日以内)。" };
      }
      return { target: "global", note: "入力内容を確認してください。" };
    }
    default:
      return { target: "global", note: "保存できませんでした。もう一度お試しください。" };
  }
};

export const createSaveFlow = ({
  client,
  getState,
  getOptions,
  onBusy,
  onActiveSaved,
  onDraftSaved,
  onFormError,
}) => {
  let pending = false;

  const post = async (status, onSuccess) => {
    if (pending) return; // 二重送信防止(design §2.9)
    const state = getState();
    if (status === "active" && !canSubmit(state)) return;
    if (status === "draft" && !canDraft(state)) return;

    pending = true;
    onBusy?.(true);
    try {
      const body = buildCreateRequest(state, { status, ...getOptions() });
      await client.call("POST", "/v1/intents", { body });
      onSuccess();
    } catch (err) {
      if (err?.code === "UNAUTHENTICATED") return; // パネル表示はonSessionExpired側で処理済み
      onFormError?.(errorPlacement(err));
    } finally {
      pending = false;
      onBusy?.(false);
    }
  };

  return {
    saveActive: () => post("active", onActiveSaved),
    saveDraft: () => post("draft", onDraftSaved),
  };
};
```

- [ ] **Step 4: 試験を実行して通ることを確認する**

Run: `cd frontend && npx vitest run tests/save.test.js`
Expected: PASS(全件)

- [ ] **Step 5: コミットする**

```bash
git add frontend/src/intent/save.js frontend/tests/save.test.js
git commit -m "feat(frontend): active/draft保存とエラー表示マップ(M1 ws-5 Task 10)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## Task 11: 配線(ui/chrome.js・main.js・index.html・styles.css・build確認)

**Files:**
- Create: `frontend/src/ui/chrome.js`(prototype app.jsのUI部品を分離)
- Modify: `frontend/src/main.js`(Task 1の暫定コピーを本配線に置換・全量書き換え)
- Modify: `frontend/index.html`(後述の7箇所)
- Modify: `frontend/styles.css`(末尾へ追記のみ)
- Test: `frontend/tests/smoke.test.js`(拡張)

**Interfaces:**
- Consumes: Task 3〜10の全モジュール
- Produces: 動作する単一画面「新しいIntent」(03 §3)。Task 12の結合確認対象

- [ ] **Step 1: index.htmlを修正する(7箇所のみ・それ以外は変えない)**

1. `<textarea id="intentText" …>…</textarea>` の中身(サンプル文言)を空にする。`<span class="character-count" id="characterCount">41 / 300</span>` は `0 / 300` にする(API接続実装では空スタート。文言対応表へ記録)
2. `<div class="condition-list" id="conditionList">…</div>` の中身(静的5行)を空にする(`<div class="condition-list" id="conditionList"></div>`)。renderConditionsが構成する
3. 条件リスト直下(`<button class="add-condition" …>` の前)へ催促注記を追加:

```html
            <p class="required-note" id="requiredNote" hidden>時間・場所・目的を指定すると預けられます</p>
```

4. 同じ位置へ parse エラー表示(503/429)を追加:

```html
            <p class="parse-error" id="parseError" role="alert" hidden></p>
```

5. `<main id="main" class="workspace">` の直前に開発用トークンパネルを追加:

```html
      <section class="token-panel" id="tokenPanel" aria-labelledby="token-title" hidden>
        <p class="section-kicker"><span></span>DEV AUTH</p>
        <h2 id="token-title">開発用トークンパネル</h2>
        <p class="token-note">APIを利用するには認証が必要です(05 §5)。backendの内部CLIで発行したIdPトークンを貼り付けてください。手順は frontend/README.md を参照。</p>
        <textarea id="idpToken" rows="3" aria-label="IdPトークン" placeholder="eyJhbGciOiJSUzI1NiIs…"></textarea>
        <button class="token-connect" id="tokenConnect" type="button">接続する</button>
        <p class="token-error" id="tokenError" role="status" hidden></p>
      </section>
```

6. 保存エラーのグローバル表示を actions-panel の直後(`</aside>` の前)へ追加:

```html
            <p class="form-error" id="globalError" role="alert" hidden></p>
```

7. expiry・privacy・notification の `<select>` 3つのうち `id="expiry"` の `<option>` 4つを削除し `disabled` 属性を付け、label を「有効期限(入力後に選べます)」に変える(選択肢はexpiry-options応答から構成。文言対応表へ記録):

```html
            <label for="expiry"><i class="ph ph-clock" aria-hidden="true"></i>有効期限(入力後に選べます)</label>
            <div class="select-wrap">
              <select id="expiry" disabled></select>
              <i class="ph ph-caret-down" aria-hidden="true"></i>
            </div>
```

- [ ] **Step 2: styles.cssへ追記する(ファイル末尾・既存スタイルは変更しない)**

`frontend/styles.css` 末尾へ:

```css
/* ---------------------------------------------------------------------------
   M1 ws-5 追加分: 催促・NG行・エラー・トークンパネル(design §2.3・§2.7〜§2.9)
   既存スタイルはprototype基準のまま変更しない(00 運用ルール6)
--------------------------------------------------------------------------- */

.condition-row.condition-missing .condition-value {
  color: var(--coral);
}

.required-note,
.parse-error,
.form-error,
.ng-note {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--muted);
}

.parse-error,
.form-error {
  color: var(--coral);
}

.condition-row.condition-error .condition-value {
  color: var(--coral);
}

.parse-retry {
  margin-left: 10px;
  padding: 4px 14px;
  border: 1px solid var(--field-border);
  border-radius: 999px;
  background: var(--field-surface-strong);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.ng-row .condition-value { font-size: 16px; }

.ng-note { margin: 4px 0 0; }

.condition-edit {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  grid-column: 3 / 4;
  min-width: 0;
}

.condition-edit input,
.condition-edit select {
  padding: 8px 10px;
  border: 1px solid var(--field-border);
  border-radius: 10px;
  background: var(--field-surface-strong);
  font: inherit;
  font-size: 15px;
  min-width: 0;
}

.condition-edit input[type="number"] { width: 90px; }

.token-panel {
  margin: 24px auto 0;
  max-width: 640px;
  padding: 20px 24px;
  border: 1px solid var(--field-border);
  border-radius: 16px;
  background: var(--field-surface);
  display: grid;
  gap: 10px;
}

.token-panel h2 { margin: 0; font-size: 18px; }

.token-note { margin: 0; font-size: 13px; color: var(--muted); }

.token-panel textarea {
  width: 100%;
  padding: 10px;
  border: 1px solid var(--field-border);
  border-radius: 10px;
  background: var(--field-surface-strong);
  font: inherit;
  font-size: 13px;
  font-family: monospace;
}

.token-connect {
  justify-self: start;
  padding: 10px 22px;
  border: none;
  border-radius: 999px;
  background: var(--green);
  color: var(--paper);
  font-weight: 700;
  cursor: pointer;
}

.token-error { margin: 0; font-size: 13px; color: var(--coral); }
```

- [ ] **Step 3: ui/chrome.jsを作成する(prototype app.jsのUI部品を分離)**

`frontend/src/ui/chrome.js`(テーマ・popover・トースト・モーダル。app.jsからほぼそのまま移設し、トースト文言とモーダルのsummary組み立てを引数化する):

```js
// 画面装飾の初期化(prototype app.jsから分離・design §3.1)。
// 変更点: ①トーストは文言を引数で受ける ②確認モーダルのsummaryは
// 選択値(label)を引数で受ける(expiryのvalueが絶対時刻になったため)。

const root = document.documentElement;

const updateThemeButton = (themeButton) => {
  const dark = root.dataset.theme === "dark";
  themeButton.setAttribute("aria-pressed", String(dark));
  themeButton.setAttribute(
    "aria-label",
    dark ? "ライトモードに切り替える" : "ダークモードに切り替える",
  );
  themeButton.querySelector("i").className = dark ? "ph ph-sun" : "ph ph-moon";
};

export const initChrome = ({ themeButton, popovers, toast, modal }) => {
  const setTheme = (theme, persist = false) => {
    root.dataset.theme = theme;
    if (persist) localStorage.setItem("latch-theme", theme);
    updateThemeButton(themeButton);
  };

  themeButton.addEventListener("click", () => {
    setTheme(root.dataset.theme === "dark" ? "light" : "dark", true);
  });
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", (event) => {
      if (!localStorage.getItem("latch-theme"))
        setTheme(event.matches ? "dark" : "light");
    });
  updateThemeButton(themeButton);

  const closePopovers = (except = null) => {
    for (const { button, panel } of popovers) {
      if (panel !== except) {
        panel.hidden = true;
        button.setAttribute("aria-expanded", "false");
      }
    }
  };
  for (const { button, panel } of popovers) {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const willOpen = panel.hidden;
      closePopovers(panel);
      panel.hidden = !willOpen;
      button.setAttribute("aria-expanded", String(willOpen));
    });
    panel.addEventListener("click", (event) => event.stopPropagation());
  }
  document.addEventListener("click", () => closePopovers());

  let toastTimer;
  const showToast = (message) => {
    window.clearTimeout(toastTimer);
    const icon = toast.querySelector("i"); // プロトタイプのcheckアイコンを維持
    toast.replaceChildren(icon, ` ${message}`);
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 2400);
  };

  const openModal = ({ expiryLabel, privacyLabel }) => {
    const summary = modal.root.querySelector(".confirmation-summary");
    summary.replaceChildren();
    const clock = document.createElement("i");
    clock.className = "ph ph-clock";
    clock.setAttribute("aria-hidden", "true");
    summary.append(clock, ` ${expiryLabel}まで`);
    const lock = document.createElement("i");
    lock.className = "ph ph-lock";
    lock.setAttribute("aria-hidden", "true");
    summary.append(lock, ` ${privacyLabel === "条件一致までは非公開" ? "成立までは非公開" : privacyLabel}`);
    modal.root.hidden = false;
    document.body.classList.add("modal-open");
    modal.closeButton.focus();
  };
  const closeModal = ({ submitButton }) => {
    modal.root.hidden = true;
    document.body.classList.remove("modal-open");
    submitButton?.focus();
  };

  // 「Intentを確認する」(returnButton)のclickでモーダルを閉じない —
  // 保存成功(onActiveSaved)で閉じる(design §2.9)。保存失敗時はモーダルを
  // 開いたまま main.js のonFormErrorが扱う
  modal.closeButton.addEventListener("click", () => closeModal({}));
  modal.root.addEventListener("click", (event) => {
    if (event.target === modal.root) closeModal({});
  });

  return { showToast, openModal, closeModal };
};
```

- [ ] **Step 4: main.jsを全量書き換える(本配線)**

`frontend/src/main.js`:

```js
// エントリポイント(03 §3の入力フロー・design §2.2〜§2.9)。
// DOM配線のみを担い、ロジックは各モジュールへ委ねる。意図文言をログに出さない(01 §21)。
import { initChrome } from "./ui/chrome.js";
import { createClient } from "./api/client.js";
import { createSession, exchangeIdpToken } from "./api/session.js";
import { PRIVACY_VALUES, NOTIFICATION_VALUES } from "./intent/format.js";
import {
  applyParseFallback,
  applyParseResult,
  canDraft,
  canSubmit,
  createFormState,
  missingRequired,
} from "./intent/state.js";
import { createParseFlow } from "./intent/parseFlow.js";
import {
  NG_NOTE_TEXT,
  REQUIRED_NOTE_TEXT,
  attachAddCondition,
  beginRowEdit,
  renderConditions,
} from "./intent/conditions.js";
import { applyExpiryOptions, fetchExpiryOptions, selectedExpiry } from "./intent/expiry.js";
import { createSaveFlow } from "./intent/save.js";

const $ = (selector) => document.querySelector(selector);

const session = createSession();
const client = createClient({
  session,
  onSessionExpired: () => showTokenPanel(),
});

const state = createFormState();

// --- 画面装飾(テーマ・popover・トースト・モーダル) -------------------------
const chrome = initChrome({
  themeButton: $("#themeButton"),
  popovers: [
    { button: $("#noticeButton"), panel: $("#noticePopover") },
    { button: $("#accountButton"), panel: $("#accountPopover") },
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

// --- 条件リスト ------------------------------------------------------------
const conditionList = $("#conditionList");
const requiredNote = $("#requiredNote");
const parseError = $("#parseError");
const globalError = $("#globalError");

const rerender = () => {
  renderConditions({ listEl: conditionList, state, now: new Date().toISOString() });
  const missing = missingRequired(state);
  requiredNote.hidden = missing.length === 0;
};

conditionList.addEventListener("click", (event) => {
  const editButton = event.target.closest(".edit-button");
  if (editButton) {
    beginRowEdit({ rowEl: editButton.closest(".condition-row"), state, rerender });
    refreshActions();
  }
});

const addConditionForm = $("#addConditionForm");
$("#addConditionButton").addEventListener("click", () => {
  addConditionForm.hidden = false;
  $("#addConditionButton").hidden = true;
  $("#newConditionValue").focus();
});
$("#cancelAdd").addEventListener("click", () => {
  addConditionForm.hidden = true;
  $("#addConditionButton").hidden = false;
  $("#newConditionValue").value = "";
});
attachAddCondition({
  formEl: addConditionForm,
  state,
  rerender: () => {
    rerender();
    $("#addConditionButton").hidden = false; // 追加フォームはattachAddCondition内で閉じる
  },
});

// --- parse連携(debounce・design §2.4) ------------------------------------
const showParseError = (message, retryable) => {
  parseError.replaceChildren(document.createTextNode(message));
  if (retryable) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "parse-retry";
    button.textContent = "もう一度読み取る"; // 07 D-17: 入力テキストを保持した再試行
    button.addEventListener("click", () => parseFlow.retry());
    parseError.append(button);
  }
  parseError.hidden = false;
};

const parseFlow = createParseFlow({
  send: (text, { signal }) =>
    client.call("POST", "/v1/intents/parse", { body: { text }, signal }),
  onResult: (data) => {
    parseError.hidden = true;
    Object.assign(state, applyParseResult(state, data.structured_intent));
    rerender();
    refreshExpiry(state.structured.time.start);
    refreshActions();
  },
  onError: (err) => {
    state.parseStatus = "error";
    if (err?.code === "VALIDATION_ERROR") {
      // 構造化フォームへのフォールバック(確定値9): 全行が手動入力可能な空状態
      parseError.hidden = true;
      Object.assign(state, applyParseFallback(state));
      rerender();
      refreshActions();
      return;
    }
    if (err?.code === "RATE_LIMITED") {
      showParseError("操作が集中しています。少し時間をおいてから入力し直してください。", false);
    } else if (err?.code === "LLM_UNAVAILABLE") {
      showParseError("ただいま条件を読み取れません。", true);
    } else if (err?.code === "UNAUTHENTICATED") {
      return; // パネル表示済み(onSessionExpired)
    } else {
      showParseError("通信エラーが発生しました。", true);
    }
  },
});

// --- 入力テキスト(300字上限はmaxlength・カウンタ常時表示) ----------------
const intentText = $("#intentText");
const characterCount = $("#characterCount");
const submitButton = $("#submitButton");
const draftButton = $("#draftButton");

const refreshActions = () => {
  characterCount.textContent = `${intentText.value.length} / 300`;
  state.rawText = intentText.value;
  submitButton.disabled = !canSubmit(state);
  draftButton.disabled = !canDraft(state);
};

intentText.addEventListener("input", () => {
  refreshActions();
  parseFlow.input(intentText.value);
});

// --- 有効期限(design §2.6) ----------------------------------------------
const expirySelect = $("#expiry");

async function refreshExpiry(timeStartIso) {
  try {
    const data = await fetchExpiryOptions(client, timeStartIso ?? null);
    applyExpiryOptions(expirySelect, data);
    expirySelect.disabled = false;
    expirySelect.closest(".setting-group").querySelector("label").textContent =
      "有効期限";
  } catch {
    // 取得失敗時は既存の選択肢のまま続行(保存時は現在の選択値を送る)
  }
}

// --- 保存(active/draft・design §2.9) ------------------------------------
const saveFlow = createSaveFlow({
  client,
  getState: () => state,
  getOptions: () => ({
    visibility:
      PRIVACY_VALUES[$("#privacy").value] ?? "hidden_until_match",
    notificationLevel:
      NOTIFICATION_VALUES[$("#notification").value] ?? "proposals_only",
    expiresAt: selectedExpiry(expirySelect).expiresAt,
  }),
  onBusy: (busy) => {
    submitButton.disabled = busy || !canSubmit(state);
    draftButton.disabled = busy || !canDraft(state);
  },
  onActiveSaved: () => {
    chrome.closeModal({ submitButton }); // モーダルを閉じ同一画面に留まる(03 §3)
    refreshActions();
  },
  onDraftSaved: () => {
    chrome.showToast("下書きを保存しました"); // プロトタイプ文言
    refreshActions();
  },
  onFormError: (placement) => {
    if (placement.target === "location" || placement.target === "time") {
      // 条件リストへ戻して修正を促す(確定値11・design §2.9) — モーダルを閉じる
      chrome.closeModal({});
      const row = conditionList.querySelector(
        `[data-label="${placement.target === "location" ? "場所" : "時間"}"]`,
      );
      row?.classList.add("condition-error");
      row?.setAttribute("title", placement.note);
      globalError.hidden = true;
    } else {
      globalError.textContent = placement.note;
      globalError.hidden = false;
    }
  },
});

const openConfirmation = async () => {
  if (!canSubmit(state)) return;
  // モーダルを開く時に期限を選択肢の最新状態へ再取得(時間経過で過ぎた選択肢の
  // disabled化・既定の再計算 — design §2.6・確定値6)
  await refreshExpiry(state.structured.time.start);
  const { label } = selectedExpiry(expirySelect);
  chrome.openModal({
    expiryLabel: label ?? "—",
    privacyLabel: $("#privacy").value,
  });
};

submitButton.addEventListener("click", openConfirmation);
$("#returnButton").addEventListener("click", () => saveFlow.saveActive());
draftButton.addEventListener("click", () => saveFlow.saveDraft());

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    openConfirmation();
  }
  if (event.key === "Escape") {
    if (!$("#confirmationModal").hidden) chrome.closeModal({});
  }
});

// --- 初期化 ---------------------------------------------------------------
rerender();
refreshActions();
```

- [ ] **Step 5: smoke試験を拡張する**

`frontend/tests/smoke.test.js` へ追記:

```js
describe("本実装の構成(Task 11)", () => {
  it("トークンパネル・催促注記・エラー表示の要素がある", () => {
    for (const id of ["tokenPanel", "idpToken", "tokenConnect", "tokenError", "requiredNote", "parseError", "globalError"]) {
      expect(html()).toContain(`id="${id}"`);
    }
  });

  it("expiry selectは初期disabled(選択肢はAPI応答から構成)", () => {
    expect(html()).toMatch(/<select id="expiry" disabled><\/select>/);
  });

  it("初期テキストは空(サンプル文言を残さない・文言対応表)", () => {
    expect(html()).toContain('id="characterCount">0 / 300');
    expect(html()).not.toContain("天文館");
  });
});
```

- [ ] **Step 6: 全テストとbuildを実行する**

```bash
cd frontend && npm test && npm run build
```

Expected: 全試験 PASS・build成功(dist/client が生成。コミット対象外=既存 .gitignore の `dist/` で除外)

- [ ] **Step 7: dev serverでの配信確認(画面操作はTask 12・ここはHTML配信のみ)**

```bash
cd frontend && timeout 8 npm run dev &
sleep 3 && curl -sf http://127.0.0.1:5173/ | rg -c "tokenPanel" && curl -sf http://127.0.0.1:5173/src/main.js | rg -c "createParseFlow"
```

Expected: 両方 1

- [ ] **Step 8: コミットする**

```bash
git add frontend/index.html frontend/styles.css frontend/src/ frontend/tests/smoke.test.js
git commit -m "feat(frontend): 入力フローの本配線(parse・期限・催促・NG行・保存)(M1 ws-5 Task 11)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## Task 12: 結合確認(実機手順)と報告ファイルの作成

**Files:**
- Create: `docs/plans/M1/ws-5-report.md`

**Interfaces:**
- Consumes: Task 1〜11の成果物・compose常設環境(api・db)・backend内部CLI
- Produces: `docs/plans/M1/ws-5-report.md`(スーパーバイザーがマージ判断する唯一の成果物)

- [ ] **Step 1: apiイメージを再ビルドして起動する**

```bash
docker compose build api && docker compose up -d --wait
```

(STATUS運用ルール4。expiry-optionsを含むコードでapiを起動する)

- [ ] **Step 2: トークン取得(10 §1の内部CLI経由)**

```bash
IDP_TOKEN=$(cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject ws5-manual-1)
ACCESS=$(curl -sf -X POST http://127.0.0.1:8000/v1/auth/token \
  -H "Content-Type: application/json" \
  -d "{\"provider\": \"google\", \"idp_token\": \"$IDP_TOKEN\"}" | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
echo "access-token-length=${#ACCESS}"
```

Expected: access-tokenの文字数が出力される(200・空でない)

- [ ] **Step 3: expiry-optionsの実応答確認**

```bash
curl -sf "http://127.0.0.1:8000/v1/intents/expiry-options" -H "Authorization: Bearer $ACCESS" | python3 -m json.tool
curl -sf "http://127.0.0.1:8000/v1/intents/expiry-options?time_start=2026-09-27T23:00:00%2B09:00" -H "Authorization: Bearer $ACCESS" | python3 -c "import json,sys; print(json.load(sys.stdin)['default_index'])"
```

Expected: 4選択肢のJSON・time_start付きは `0`(今夜23:30最近)。実行時刻によって selectable が変わる点は報告に記録する

- [ ] **Step 4: parse・保存の実応答確認(docs掲載の例文のみ使う)**

```bash
PARSE=$(curl -sf -X POST http://127.0.0.1:8000/v1/intents/parse \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"text": "今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。予算は5000円くらい。会社関係の人は避けたい。"}')
echo "$PARSE" | python3 -m json.tool | head -20
```

Expected: 200・structured_intent(ci環境はスタブ応答: meal/東京駅)。※LLMスタブ応答のためng_unverifiable等は空になり得る — これは既知のci環境の挙動(報告に記録)

保存(active・201) — structured_intent はスタブ応答の値に expiry-options の選択値を上書きして使う:

```bash
EXPIRY=$(curl -sf "http://127.0.0.1:8000/v1/intents/expiry-options" -H "Authorization: Bearer $ACCESS" | python3 -c "import json,sys; print(json.load(sys.stdin)['options'][1]['expires_at'])")
PARSE="$PARSE" EXPIRY="$EXPIRY" python3 <<'PY' > /tmp/ws5-active.json
import json, os
si = json.loads(os.environ["PARSE"])["structured_intent"]
si["visibility"] = "hidden_until_match"
si["notification_level"] = "proposals_only"
si["expires_at"] = os.environ["EXPIRY"]
si["negative_constraints"] = []
print(json.dumps({
  "raw_text": "今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。予算は5000円くらい。",
  "status": "active",
  "structured_intent": si,
}, ensure_ascii=False))
PY
curl -s -X POST http://127.0.0.1:8000/v1/intents -H "Authorization: Bearer $ACCESS" \
  -H "Content-Type: application/json" -d @/tmp/ws5-active.json | python3 -m json.tool
```

Expected: 201・`{"intent": {"id": …, "status": "active", …}}`

下書き(draft・201):

```bash
curl -s -X POST http://127.0.0.1:8000/v1/intents -H "Authorization: Bearer $ACCESS" \
  -H "Content-Type: application/json" \
  -d '{"raw_text": "今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。", "status": "draft"}' | python3 -m json.tool
```

Expected: 201・status=draft・expires_at=null(補完しない・05 §5)

ジオコーディング失敗(422 GEOCODING_FAILED): 上記activeのJSONで location.name を「存在しない地名ああああ」に変えて再送:

Expected: 422・`error.code=GEOCODING_FAILED`

- [ ] **Step 5: DBでの保存結果確認**

```bash
docker compose exec db psql -U latch -d latch -c "SELECT status, expires_at IS NOT NULL AS has_expiry FROM intents ORDER BY created_at DESC LIMIT 3;"
```

Expected: active行(expires_atあり)・draft行(expires_at NULL)が見える

- [ ] **Step 6: vite dev server経由のproxy確認(同一オリジン)**

```bash
cd frontend && timeout 8 npm run dev &
sleep 3 && curl -sf -X POST http://127.0.0.1:5173/v1/intents/parse \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"text": "今日20時以降、天文館で軽く飲みたい。"}' -o /dev/null -w "%{http_code}\n"
```

Expected: 200(proxy経由でcompose apiへ到達。design §2.2)

- [ ] **Step 7: 報告ファイルを作成する**

`docs/plans/M1/ws-5-report.md`(報告形式は本計画書末尾のテンプレート)。UIの画面操作(debounce後の再構成・行編集・催促・NG行・期限選択・モーダル・トースト)は**実行手順書を報告書に残し、実画面確認はスーパーバイザー検証待ち**と記録する(告白1: E2E不導入のため。ロジックはvitestで検証済み)。

- [ ] **Step 8: コミットする**

```bash
git add docs/plans/M1/ws-5-report.md
git commit -m "docs: M1 ws-5実装報告(M1 ws-5 Task 12)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## 完了条件(テストで証明できる形)

1. `cd frontend && npm test` が全件緑(smoke・format・state・session・client・parseFlow・conditions・expiry・save)
2. `make lint` / `make test`(backend)が緑(expiry-options追加を含む。既存試験は無修正で緑)
3. `cd frontend && npm run build` が成功
4. backend unit試験 `tests/unit/intents/test_expiry_options_routes.py` 4件緑(Task 2)
5. `rg -n "console\.(log|info|debug)" frontend/src/` が空(ログ規律・確定値16)
6. `rg -n "天文館" frontend/src/ frontend/tests/` が format.test.js・state.test.js・conditions.test.js・save.test.js のdocs掲載例文由来のみに命中(新規の意図文言をコードに書いていない)
7. Task 12の実機結合手順(curl・psql)の結果が報告書に記録済み
8. integration試験 `test_expiry_options_api.py` はファイル存在のみ(実行=スーパーバイザー検証待ちと報告に記録)

## 報告形式

結果ファイル: `docs/plans/M1/ws-5-report.md`。次の構成で書く:

```markdown
# M1 ws-5(フロントエンド)実装報告

- 作成: YYYY-MM-DD(agent3)
- 計画: docs/plans/M1/ws-5-plan.md / 設計: docs/plans/M1/ws-5-design.md

## コミット一覧
(Task 1〜12のコミットhashとメッセージ)

## 実装サマリ
(frontend/の構成・各モジュールの担当・backend expiry-options追加・05追記)

## 検証結果
- frontend: npm test N passed / npm run build 成功
- backend: make lint 緑 / make test N passed(既存無修正)
- 着手条件確認: ws-4マージ済み(ratelimit/の存在・git log)

## 結合確認(実機・design §4.2)
(Step 2〜6の実行結果: トークン取得・expiry-options・parse・保存201×2・
 GEOCODING_FAILED 422・DB確認・vite proxy。curl応答の要点)

## test-ci=スーパーバイザー検証待ち
(test_expiry_options_api.py 3件・実行手順: make test-ci)

## UI画面確認手順(スーパーバイザー用)
1. make up 後 cd frontend && npm run dev → http://localhost:5173/
2. トークン発行(CLI)→ パネルへ貼付 → 接続する
3. 例文入力 → 1秒後に条件リストが再構成される
4. 行編集(鉛筆→入力→Enter)・催促表示(必須3欠落)
5. 期限の既定選択・disabled・モーダルのsummary
6. 下書き保存(トースト)・預ける(201で同一画面)
7. 場所を存在しない地名へ編集して預ける → 場所行へエラー
8. topbar「Intent 3件」はプレースホルダのまま(M3-10)

## 文言対応表(告白5・design §2.5・§2.7〜§2.9で本実装が固定した文言)
| 箇所 | 文言 | 出典・備考 |
(催促注記・パネル文言・エラー文言一式・初期テキスト空・expiry label 変更)

## 05改版実施済み(後日ユーザー確認待ち)
(expiry-options 1行追記の内容)

## 知見・残余リスク
(実装中に判明したこと・designとの差分・残課題)
```

## 補遺: designからの実装補完(計画がdesignの字義に対して決めた細目)

1. **初期テキストを空にした**(prototypeはサンプル文言入り)。API接続後は意図文言の入力から開始するのが03 §3のフロー。変更は文言対応表へ記録
2. **expiry selectの初期状態**は disabled+空(選択肢はexpiry-options応答から構成・design §2.6の「parse成功時・モーダルを開く時の取得」の前は選べない)
3. **VALIDATION_ERRORの表示位置**はmessage先頭一致(time.start/expires_at→時間行)で推定し、未知の形式はグローバルへ落とす(design §2.9表の実現手段・確定値13の例外はこの1点のみ)
4. **time.endの編集UIは置かず null で送る**(サーバ側でtime.start+3h補完・05 §5)。parse応答のendは表示に使う
5. **refreshExpiryの失敗は握りつぶす**(既存の選択肢のまま保存を続行。期限取得の失敗で入力フローを止めない)
6. **503時の再試行UI**は parseエラー表示内の「もう一度読み取る」ボタン(showParseErrorが生成)で parseFlow.retry() を呼ぶ(入力テキスト保持・07 D-17)。429にはボタンを付けず待機のみ促す
7. **session.jsにApiError类を置いた**(envelope解釈の共通部品としてsession/client双方から使う)
8. **UI画面操作の最終確認はスーパーバイザー検証とする**(E2E不導入・告白1。実行手順を報告書に残す)

