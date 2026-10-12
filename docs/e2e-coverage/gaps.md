# カバレッジギャップ(優先度つき・Phase 2 — 2026-10-12)

前提: 既存367件のvitestは**モックfetchのコンポーネント試験**として強力だが、実ブラウザ×実API結合の証明はゼロ。本リストは ①完全未カバーcell ②partial cellの不足分 ③実API結合にのみ証明できる事柄 をcluster化したもの。Status: `open` → `implemented(file:line)` / `known-failure(findings#N)` / `waived(reason)`。

## P0(横断アクセス・安全)

| ID | Behavior | Dimension | Priority | Status | Notes(テストアプローチ案) |
|---|---|---|---|---|---|
| G-01 | 他ユーザーのlatch URL直叩き(G7) | authz | P0 | implemented(cross-user.spec.js:8) | 実ユーザーA/Bの2セッションE2E。BのブラウザでAのlatch詳細URLを開き404「提案が見つかりません」+home導線。API面の横断遮断は契約試験(outsider)担保済みだがUI結合は実セッションで未証明 |

## P1(主幹フロー・状態遷移・永続化)

| ID | Behavior | Dimension | Priority | Status | Notes |
|---|---|---|---|---|---|
| G-02 | 主幹作成フロー: 入力→parse→確認→預ける→03着地→永続(C1/C2/C5/D5/E1) | happy, validation, state, persist | P1 | implemented(main-flow.spec.js:29・C2:10,15/C4:22/C5:65) | 実APIユーザーで01に「天文館 今夜19時から 2人で 飲み」等(既存7地物の範囲)を入力→debounce parse→02で5条件確認→預ける→`#/standby?deposited=1`着地→**リロードでIntentが残る**。C1のcomposer配線(型入力→chips→CTA有効化)はunit全体で未証明のため本E2Eが最初の証明になる(OBS-03) |
| G-03 | 認証・セッション実フロー(A2/A3/A4/A5/A6) | happy, validation, error, state, persist | P1 | implemented(auth.spec.js:8-66・A5はfindings#1修正後にfixme解除で36件緑) | CLI発行IdPトークン→開発用パネル実操作(有効=接続/無効=「トークンが無効です」/route abort=「接続できませんでした」)→storage実永続→再訪で継続。A5はaccess破棄+refresh残存での再訪で自動refreshを観測。TokenPanelはunitで書けない構造(OBS-02)のためE2Eが唯一の証明場所 |
| G-04 | 同意ゲート実フロー(B1/B2/B3) | error, state, happy, persist | P1 | implemented(consent.spec.js:8,16,31) | 未同意ユーザー(登録のみ・consentなし)で起動→`#/onboarding`誘導→01入力でparse 422 CONSENT_REQUIRED→トースト+誘導→オンボーディングで同意→再訪で誘導なし。App配線(起動時・422横断ラップ)がunit未証明のためE2Eが本丸 |
| G-05 | 回答・undo実フロー(G2/G5/G6・E6込み) | happy, state, error | P1 | implemented(response.spec.js:8,19,33,44,59,71) | latch fixture(契約harness `reference_harness.py`のactor provisioning流用)で04を開く→YES→allIn/05遷移→undo→**open復帰(2択の再表示)**→再undoで409収束。**G5(open復帰)はunitにアサーション自体が存在しない**(FG-07)最重要の新規証明。着地時トースト(E6)もここで観測 |
| G-06 | 取り下げの永続化(E3 persist) | persist | P1 | implemented(intent-lifecycle.spec.js:8) | active Intent作成→03「取り下げる」→dialog確認→リロード/再訪でIntentが消えている(バッジも0)。withdraw後のintents再取得が実装にないため(OBS-05)E2Eでのみ証明可能 |
| G-07 | チャット実送受信(H3/H4) | happy, persist, state | P1 | implemented(chat.spec.js:8) | matched latch fixture→2ブラウザコンテキスト(自分/相手)で送信→相手側一覧に反映(ポーリングではなく再訪で確認)・leave後の読取専用 |
| G-08 | 条件を直す(編集モード)実フロー(D8 persist/D9/E2/E5) | happy, state, persist | P1 | implemented(intent-lifecycle.spec.js:37) | 03「条件を直す」→GET復元→条件変更→PATCH→リロードで変更が維持。下書き行(E5)からの編集も同型。D8のGETパスはunitで検出不能(FG-06) |

## P2(二次フロー・表示品質)

| ID | Behavior | Dimension | Priority | Status | Notes |
|---|---|---|---|---|---|
| G-09 | 01の入力補助とエラー表示(C3/C4/C2後半) | happy, error, validation | P2 | implemented(errors-and-settings.spec.js:8・main-flow.spec.js:10,15,22) | C4=例文chip実クリック→挿入→parse。C3=page.routeでparse 429/503注入→専用文言+retryable。C2=300字境界の実挙動(maxLength実効性) |
| G-10 | 保存系エラー文言と再試行(D7) | error | P2 | implemented(errors-and-settings.spec.js:22) | page.routeでPOST /v1/intentsに429/503注入→errorPlacement文言→再クリックで再送 |
| G-11 | countsバッジの実DOM表示(J1) | happy | P2 | implemented(response.spec.js:71) | データ付きE2E(例: G-02/G-05の過程)でヘッダー+tabbarの非0件pillをassert。FG-01/02で実装証明が欠けていた箇所 |
| G-12 | お知らせpopover実配線と既読永続(J4) | happy, state, persist | P2 | implemented(errors-and-settings.spec.js:58) | 実Header通知ボタン開閉→一覧→既読化→リロードで既読維持。notification fixtureが必要 |
| G-13 | .ics実ダウンロード(H2) | happy | P2 | implemented(chat.spec.js:30) | Playwrightのdownloadイベントでファイル取得→中身(UID/DTSTART/SUMMARY)検証 |
| G-14 | ブロック解除の永続(I3 persist) | persist | P2 | implemented(errors-and-settings.spec.js:79) | 設定でブロック解除→リロードで行が消えている |
| G-15 | leave後の再訪readonly(H6 persist転嫁先) | state | P2 | implemented(chat.spec.js:44) | leave→リロード→CHAT_READONLY収束で「取り消し」が反映。H6/H7/H10のpersistは設計上ここへ転嫁(W-04) |
| G-16 | 不正・存在しないlatch uuid(G8) | error | P2 | implemented(misc.spec.js:8) | 不正uuid→home戻し・整形式存在なし→404表示。最短で書ける回帰保険 |
| G-17 | ≤720pxビューポート実描画(J3) | happy | P2 | implemented(misc.spec.js:20) | 390pxと720px境界でtabbar/topnav切替をviewport変更で実証 |

## waived(実施しない・理由つき)

| ID | Behavior | 理由 |
|---|---|---|
| W-01 | A7 未登録者の挙動(state) | 期待挙動が仕様に未定義(登録UIなし・POST /v1/usersはAPI専用)。assertすべき結果が決まっていないため実装不可。仕様確認事項として findings.md OBS-01 |
| W-02 | F4 一覧追頁のE2E | >20件のlatch作成コストが大きく、unit(latch-lists.test.jsx:148-161)が完全証明済み |
| W-03 | H5 チャット30秒ポーリングのE2E | 実時間30秒待ちが必要で遅い。unit(latch-chat.test.jsx:84-123)が完全証明済み |
| W-04 | H7/H10 persist | 設計上サーバに既答フィールドがなく、再訪時は409収束(H8)/読取専用(H4①)へ転嫁と明記(AttendanceCard.jsx:4-5・DetailScreen.jsx:15-16)。G-15が実証を担う |

## Phase 2 exit checklist

- [x] 全cell(適用91)にステータスと証拠(matrix §2.3)
- [x] false-greenヒット7件を findings.md に記録
- [x] gaps.md 優先度つき・重複除去済み・アプローチ案つき(P0:1 / P1:7 / P2:9 / waived:4)

## Phase 3結果(2026-10-12)

- 全ギャップ実装完了: **G-01〜G-17 すべてimplemented**(上表Status列にfile:line)。追加された成果物は `frontend/tests/e2e/`(helpers.js+9specファイル・35テスト)。2回連続全緑(35 passed・1 skipped・~24秒)
- 唯一の既知失敗だったA5(access切れ再訪の自動refresh)は **findings#1をclient.js単一フライト化で修正**しfixme解除(36件緑・2回連続)
- 実装中に判明した契約の追加事実: 取り下げ(cancelled)後のGET /v1/intents/{id}は本人にも404 / latch proposal jsonbの豊富さ(summary_only全フィールド形)が04画面の全形/最小形を決める
