# UI v2移行 Phase 6 実施報告 — 実装のReact載せ替え(見た目不変)

- 日付: 2026-10-10
- 出典: [ui-v2-roadmap.md](../ui-v2-roadmap.md) Phase 6 / [phase0-decisions.md](phase0-decisions.md)
  (載せ替え方針の経緯はロードマップPhase 6節。追加の経緯=2026-10-10ユーザー指示
  「今の画面をreactで再構築するフェーズも追加しようよ」)
- デザインの正: 現行frontend実装(Phase 2〜5の成果物)+prototype/v2。**本フェーズは
  リファクタリングであり、画面の見た目・文言・API呼び出し・a11y属性・デザイン値は
  変えていない**(styles.cssは1行も変更していない)
- 状態: 完了。全vitest緑(365件)・`vite build`緑・実API(compose local+実ブラウザ)で
  同意→01→02(行編集)→保存→03着地→一覧→04→回答→05チャット送信→PATCH
  (条件を直す)まで往復を確認

## 実施内容

### 1. 依存とマウント基盤(作業1・コミットa557ef0)

- `react@19.3`・`react-dom@19.3`をdependenciesへ、`@vitejs/plugin-react@4`(vite 6
  対応線)・`@testing-library/react@16`・`@testing-library/dom@10`をdevDependenciesへ
  追加(規律どおりこの4系統のみ・CDN不使用・セルフホスト維持)
- `vite.config.mjs`と`vitest.config.mjs`へ`react()`プラグインを追加。
  vitestのincludeを`tests/**/*.test.{js,jsx}`へ拡張(JSX試験の拡張子変更に対応)
- `main.jsx`(旧main.js)は`createRoot(#root)`で`<App session>`をマウントするだけに。
  index.htmlはhead+skipの替わりの`#root`+scriptのみ(シェル以降は全部Reactが描く)

### 2. シェル(chrome)・ルーティング・toast・dialog(作業2・同コミット)

- `src/ui/chrome.jsx`: 旧chrome.js(initChrome)を`ChromeProvider`+`useChrome`へ
  載せ替え。APIの形(showToast/setCounts/setCurrentNav/setAccountInitial/
  openAppDialog/dismissDialog)は旧実装と同じで、呼び出し側ロジックを変えずに済む。
  toast(文言=自動消滅2400ms/候補カード=自動消滅なし)とappDialog
  (`<dialog class="dlg">`+showModal/close・backdropクリック・Esc)は状態駆動。
  openAppDialogのbodyはReactNode+移行期の互換として生のNodeも受理した
- `src/ui/Header.jsx`: ヘッダー(ロゴ・topnav4タブ+件数バッジ・お知らせ/アカウント
  popover)をコンポーネント化。popoverは旧chrome.jsと同じ挙動(外側クリックで閉じる・
  Escで閉じて起動ボタンへフォーカスを戻す・1つを開くとほかを閉じる)
- `src/ui/Tabbar.jsx`(4タブ+tbバッジ)・`src/ui/TokenPanel.jsx`(接続→session.save→
  未同意誘導)・`src/ui/Icon.jsx`(icons.jsのICONS表から同形のSVGを組むReact版。
  pathデータ・stroke幅はそのまま)
- ルーティング: Appが`hashchange`を購読し`parseHash(router.js)の契約`をそのまま
  消費して画面を条件マウントする(react-router等は導入しない・router.js無変更)。
  nav強調(aria-current)はNAV_BY_ROUTE+詳細画面からの差し替え(旧NAV_BY_ROUTEと同じ)

### 3. 01ホーム+02読み解き確認(作業3・コミットa0d8cb6)

- **01↔02のstate共有をReact流へ**: `createFormState()`の1オブジェクト共有をやめ、
  Appがフォームstateを1つ持ち、ミラー付きsetter(`updateIntentState`)で両画面へ渡す。
  純関数(applyParseResult・applyIntentSnapshot・editors.commit等)はそのまま使い、
  可変な箇所(editors.commit・parseFlowのコールバック)はミラー参照で同期待ちをする
- `src/intent/HomeScreen.jsx`: composer(制御textarea)・読み取りchips(readSlotsから
  派生描画)・例文chips・CTA「読み解く」(parseFlow.commit待ち→`#/confirm`)・下書き
  保存(D-13)・待機中の意思カード(IntentCard=旧intentCardHtmlのReact版・候補対応
  づけpairCandidateLatchで04直結)・⌘Enter
- `src/intent/ConfirmScreen.jsx`: 3ステップ・あなたの言葉(buildWordsSegments→
  mark+sup)・5行(`SlotRows.jsx`=旧renderRows/beginRowEdit/peopleFieldset/
  renderExtras/ngNoteBoxのReact版)・行編集(editors.commitをstructuredCloneした
  コピーstateへ適用)・seg(D-04)・extrasトグル・期限select(expiry.jsの
  applyExpiryOptions/selectedExpiryをref経由でそのまま使用・expiry.js無変更)・
  CTA保存(saveFlow=createSaveFlowをrefで構築・新規POST/編集PATCH/下書きactive化
  D-13)・エラー行強調(errorPlacement)
- screen.js/confirm.jsを削除。home.jsは純関数のみに(trim: splitStatuses/
  pairCandidateLatch/categoryIconName。intentCardHtml/createHomeは削除)

### 4. 03待機中・一覧・04候補・05成立+チャット(作業4・コミットb374b08)

- `src/latch/StandbyScreen.jsx`(03): 着地/通常の2姿・メインカード(旧mainCardHtml/
  withdrawnCardHtml)+RemainTimer(role=timer・1秒更新)・取り下げdialog(
  chrome.openAppDialog=JSX body)・DELETE・取り下げ済み保持(goneMain)・オーブ
  (旧orbHtmlのチップ絶対位置をstyleオブジェクト化)・ほかの意思/下書き行(D-13)・
  候補トースト(セッション内1回はモジュールスコープSetで旧クロージャと同じ寿命)
- `src/latch/ListsScreen.jsx`(D-11): 候補/成立の2モード・バッジ供給・空状態・追頁
- `src/latch/DetailScreen.jsx`(04/05): 旧detail.jsの3姿を移植。
  **見送り繰り延べ送信**(declinePending/declineReasonsはrefミラーにして、unmount
  cleanupでflush=旧main.jsのonRoute detail.flush()と等価)・**allIn一回表示**
  (showAllInOnceRef)・undo(D-06)・回答期限タイマー(残5分timer-low・0で回答無効)・
  照合表(pickMyIntent+fitRows)・成立姿(ヒーローsnap/burst・details+dl・カレンダーに
  追加=D-10・メンバー・安全のために・成立取り消しD-08=chrome.openAppDialog→
  POST leave・leftLatchはセッション内フラグ)・nav強調(proposal/closed=候補・
  matched=成立)
- `src/latch/Chat.jsx`: 旧chat.jsの同期ロジックをそのまま移植(30秒ポーリング・
  visibilitychange再取得・初回cursor paging上限5頁・差分同期は最終頁cursorから・
  表示は末尾500件・ローカル追記しない)。同期はmessagesRefミラーで行う
- `src/latch/SafetyDialog.jsx`(D-09): 2ステップ(対象ラジオ→理由4択)・1対1は対象
  固定/提案1対1は対象非開示(reportee_id省略=案X)・done状態はrole=status。
  DetailScreenが常時`<dialog>`を描きopenで制御(旧index.htmlの空dialog常在と同じ)
- `src/latch/AttendanceCard.jsx`(D-14): 質問→POST→409で切替・3日窓超過は非表示
- respond.js/calendar.jsは無変更のまま利用(ボタン配列は渡さずbusyはReact側で管理。
  calendar.jsへは判定用の`hasCalendarTime`exportのみ追加)。standby.js/lists.js/
  detail.js/chat.js/safety.js/attendance.jsを削除

### 5. 横断画面(作業5・コミットf584884)

- `src/latch/ConsentCard.jsx`: 旧consent.jsの同意カード(文言はtexts.jsの
  CONSENT_*=2026-10-03承認済みのまま無変更)。オンボーディング(interactive)と設定
  (静的)の両方で使用
- `src/latch/consentGate.js`: 誘導経路2本(ensureGuidance/notifyRequired)を
  フレームワーク非依存の関数へ切り出し(Appのclientラップ=422 CONSENT_REQUIRED
  横断検出から呼ぶ)
- `src/latch/PermissionControl.jsx`(D-18・Notification注入維持)・
  `src/latch/BlocksSection.jsx`(一覧+解除確認dialog=`<dialog class="dlg"
  id="unblockDialog">`・404収束・429表示)・`src/latch/SettingsScreen.jsx`(3カード
  +オンボーディング画面)・`src/latch/NoticePopover.jsx`(useNotice hook+NoticeList。
  preload1回・初回openは再取得しない・未読へだけPOST read・失敗行は握る・
  Esc/外側クリックの閉じはHeaderのpopover機構)。consent.js/settings.js/
  permission.js/blocks.js/notice.js/skeletons.jsxを削除

### 6. 試験の移行(作業6・各コミットで実施)

- **純関数系の試験は無傷**: app-state/client/format/highlight/latch-view/
  parseFlow/save/session/stateに加え、対象モジュールを触らないexpiry/router/
  latch-calendar/latch-respondも旧来のままとした(respond.js・calendar.js・
  router.js・expiry.js自体を無変更で残したため)
- DOM系はRTLへ書き換え(意図=文言・状態遷移・a11yは踏襲し、DOMの組立方法だけ
  置き換え): smoke(構成+シェル)・latch-shell(シェル構造+popover/toast/dialog
  挙動+a11y)・confirm(8→11件・編集モードPATCH/下書きactive化を追加)・conditions
  (readSlots/rowModels等の純関数部は無傷・描画部をSlotRowsで)・latch-home
  (splitStatuses/pairは無傷・カードとデータ取得をHomeScreenで)・latch-standby(14)・
  latch-lists(9)・latch-detail(28・旧20件+成立取り消し/通報dialogを実dialogで)・
  latch-chat(15)・latch-safety(10)・latch-attendance(6)・latch-notice(28・純関数部
  無傷+useNotice/NoticeList)・latch-consent(11)・latch-blocks(7)・latch-settings(9)
- `tests/helpers.jsx`: App描画用のスタブsession/client・chrome contextの
  取得ヘルパー。RTL cleanupはglobals無しのため明示
- 実装した old→new の対応: screen→HomeScreen・confirm→ConfirmScreen+SlotRows・
  home(描画)→HomeScreen/IntentCard・standby→StandbyScreen・lists→ListsScreen・
  detail→DetailScreen(+Chat/SafetyDialog/AttendanceCard)・chat→Chat・respond→
  respond.js(温存)・notice→useNotice/NoticeList(Header)・settings→SettingsScreen・
  consent→ConsentCard+consentGate・blocks→BlocksSection・permission→
  PermissionControl・chrome→ui/chrome.jsx・main→main.jsx+App.jsx

## 実API検証(compose local・2026-10-10)

環境: compose常設api/db(127.0.0.1:8000・llm_mode=stub)+`npm run dev`(vite proxy)+
実ブラウザ(In-app Browser・415px級)。ユーザーはphase6-react-a(ふみ)/
phase6-react-b(カイ)。提案形成のため既存の検証用worker
(`latch-verify-worker`・`LATCH_LLM_STUB_JEV_NOUL=0.85`)を使用し、通常worker
(`latch-ci-worker-1`)を検証中のみ停止→検証後restartして元に戻した。

1. **トークン接続+同意**: トークンパネル(React)にIdPトークンを貼って接続→
   ensureConsentGuidanceが**#/onboardingへ遷移**・React版同意カード
   (チェック→cta有効化→POST consent 200・「同意を記録しました」トースト→`#/`へ)
2. **01**: 例文chips「飲み」→parse(_stub)で読み取りchips(時間/場所/目的)表示・
   CTA有効化→「読み解く」→**#/confirmへ遷移**
3. **02**: 行編集で時間=「明日 20:00」・場所=「天文館」→値がv2書式
   (「明日 10/11（日）20:00〜」)で反映・seg「含む」→「5項目 · すべて確認済み」で
   CTA有効→「この条件で預ける」→**POST成功→#/standby?deposited=1へ**
4. **03着地状態**: 「預けました。/あとは、待つだけ。」・badge「待機中 · 1件」+
   navバッジ1・メインカード(食事・たった今預けた・原文引用・タグ4種・
   role=timer 00:32:17の1秒更新・条件を直す・取り下げる)・オーブチップ・3カード・
   ほかの意思空状態を確認(GET /v1/intentsでexpires_at/time/location/
   participants_includes_owner=trueも確認)
5. **提案形成**: BをAPIで登録+同意+同一条件で預ける→検証workerで**proposed形成**
   (df990fe7・締切23:59)
6. **D-11一覧**: #/proposalsで「LATCH候補 · 1件」+navバッジ1・行=最小形の隠蔽文言
   +残時間「あと31分で締切」+回答まちpill→行クリックで04へ
7. **04**: badge「LATCH候補 · 23:27 に届きました」・案内ボックス(最小形)・
   role=timer 31:09・目隠しアバター(ふ+1人)・回答2択・通報導線。**BがAPIでyes**
   (partial_accept)→AがUIで「参加する」→**allIn状態**(「2人全員が参加」+
   「チャットを開く」)→クリックで**05へ**
8. **05成立・チャット**: ヒーロー「LATCHしました。」+「成立 · 23:28」(yes直後の
   実時刻)+タグ・チャット(「明日の2人」「20:00 · 鹿児島市天文館」・アバタースタック
   カ/ふ・role=log+開始行・input+円形送信)・aside(成立した内容dl・カレンダーに
   追加・メンバー(カイ/あなた・参加)・安全のために・この成立を取り消す)。
   **送信ボタンは空でdisabled→入力で有効化→UI送信→serverに1件**(B側の
   GET messagesで本文を確認)
9. **お知らせ**: 未読(latch_matched)でpopoverを開く→1回目はpreload表示(旧実装と
   同じ=初回openは再取得しない)・開き直しで2行取得+**既読化でドット消灯**
10. **設定**: アカウントpopover→「設定」→**popoverが閉じて**#/settingsへ。3カード
    (通知=許可済み・ブロック管理=空状態・LLM送信の同意=「同意済み: 10月10日 23:24」)
11. **PATCH(条件を直す)**: 新しいIntentを預けて「条件を直す」→02編集モード
    (GET復元・seg「確認済み」・end+3h補完の表示)→予算行「1人 5,000円まで」→
    保存→**PATCH成功(サーバでversion 2・budget.max=5000)**→#/standbyへ
12. **成立一覧**: #/matchedで「成立 · 1件」+行(「チャットで挨拶を交わしましょう」+
    成立済みpill)・nav強調が実態(成立)に追従
13. **raw_text漏出**: api/workerの直近ログに原文なし(01 §21)

検証中に発見・修正した不具合: アカウントpopoverの「設定」クリックで`setOpenName`
がスコープに無く(未デストラクチャ)ReferenceError→popoverが開いたまま残る。
Header.jsxの修正+回帰試験(「アカウントpopoverの[設定]はpopoverを閉じて設定画面へ
遷移する」)を追加(コミットc771660)。

## 避けられない差分(見た目・文言・a11yは不変。DOMの組立方法のみの差分)

1. **画面sectionの常在廃止**: 旧index.htmlは8画面のsectionを常在させてrouterが
   hidden切替していたが、React版はアクティブルートの画面のみをマウントする
   (非表示用のhidden属性の概念がなくなる・見た目は同一)
2. **safetyDialog/unblockDialogの置き場所**: 旧はbody直下の常在dialog。新は
   DetailScreen(統合dialog)/BlocksSection(解除dialog)が所有する(要素は開いて
   いなくても描かれるためDOM存在のタイミングが画面単位に)
3. **matchMedia判定のタイミング**: 05のdetails初期openは旧と同じく描画時1回の
   `window.matchMedia("(min-width: 960px)")`だが、Reactの再描画でも値を読む
   (旧はrenderMatched毎。実挙動は同等)
4. **回答ボタンのpending表現**: 旧はrespondFlowへボタン配列を渡してdisabledを
   直接切替えていたが、React版はrespondBusy状態で制御(respond.js自体は無変更・
   ボタン配列は渡していない)
5. **03/一覧の取得中の描画**: 旧はshow()完了まで描画しなかったため、React版も
   loadedまで骨組みだけを返す(空状態の誤表示を避ける。見た目への影響なし)
6. **Route離脱のflush**: 旧main.jsのonRoute先頭呼び出しを、DetailScreenの
   unmount cleanupへ移設(同一タイミング=画面取替時)
7. **icons.jsのDOM関数(icon/logoSvg/iconHtml)**は残置(未使用・試験と互換のため)。
   ReactはICONS表を消費するIcon.jsxを使う
8. **hosting詳細**: ホストポートの`page-main`骨組み(翡翠haze等)は各画面
   コンポーネント自身が描く(旧index.html静的からの移設・値は同じ)

## 独自に構成した値・判断

- **tests/helpers.jsx**(App描画のスタブ): RTL化のための試験インフラ。本番コード
  に影響しない
- **コンポーネントのprops設計**(client/state/getState/setStateを明示受渡し+
  chromeはcontext): 試験からスタブを注入できる境界。旧モジュールの生成時引数と
  同型を意識した
- 旧モジュールのHTML組立関数(mainCardHtml等)はReact JSXへ写した際、属性の
  有無(data-border/data-mono/aria-label)と文言を1行単位で一致させた(試験で
  検証)。style文字列(top:9%;left:0等)はstyleオブジェクトへの変換helper
  (posStyle)で吸収

## 持ち帰り(Phase 7への申し送り)

1. **icons.jsの未使用DOM関数**: icon/logoSvg/iconHtmlは本フェーズで未使用に
   なった(試験互換のため残置)。P7の清掃で削除可
2. **settings.jsは削除済み・latch-settings試験の骨組describe**: createSettingsの
   単体試験はSettingsScreenの描画試験へ置き換わった。P7でdocs/03の画面構成記載と
   実装(React)の対応を最終確認すること
3. **attendance・.icsダウンロード・allIn→05到達・undo409**: 実APIでは未確認
   (unit試験で担保・phase4-report持ち帰り3の継続)。P7の実機E2Eの対象
4. **モバイル実機確認**: 本フェーズの実ブラウザ検証はデスクトップ幅。tabbar/
   safe-area/チャット高さはP7の実機確認対象(CSS無変更のためリグレッション
   リスクは低いが、条件付きで確認)
5. **契約試験130ケース・Workers Assets配信検証(フォント・CSP・オリジン)**:
   P7の対象。vite buildの出力(dist/client)は本フェーズで緑を確認済み
