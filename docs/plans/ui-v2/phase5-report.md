# UI v2移行 Phase 5 実施報告 — 横断機能の載せ替え+a11y総点検+モバイル確認

- 日付: 2026-10-10
- 出典: [ui-v2-roadmap.md](../ui-v2-roadmap.md) Phase 5 / [ui-v2-gap-analysis.md](../ui-v2-gap-analysis.md) §5 / [phase0-decisions.md](phase0-decisions.md) D-11・D-13・D-14
- デザインの正: `prototype/v2/`の共通`<style>`(部品の正源)。画面構成はD-14どおり
  v2部品(token・cta/ghost/danger・dlg・card)の踏襲で独自に構成
- 状態: 完了。全vitest緑(358件)・`vite build`緑・実API(compose local+実ブラウザ)で
  同意・設定・お知らせ・下書きactive化・一覧・終了状態の稼働を確認

## 実施内容

### 1. LLM同意(オンボーディング+設定内)のv2再実装 — `src/latch/consent.js`

- **文言は無変更**(texts.jsのCONSENT_*定数・2026-10-03ユーザー承認済みの単一ソース)。
  変更したのは構成のみ: リード+3点の`ul.consent-points`(accドットつきリスト・
  02のprivacy-list風)+`consent-condition`(#0F1119の枠つき注記ボックス)+状態線
  (`consent-state`・同意済みはstbドット)+チェックラベル+**「同意する」=v2のcta**
  (旧`.submit-button`から切替・チェックで有効化のロジックは無変更)
- カード内の題(CONSENT_TITLE)は出さない。実ブラウザで画面h1とカード題の二重表示が
  見えたためで、題はオンボーディングのh1・設定のセクションh2が担う
- 誘導経路2本(ensureGuidance・422 CONSENT_REQUIREDのnotifyRequired)と
  POST/GET /v1/users/me/consentの呼び出しは無変更

### 2. 設定画面のv2再実装 — `index.html`・`settings.js`(無変更)・`permission.js`・`blocks.js`

- **画面構成**: `page-main--settings`(720px)+ヒーロー(kicker SETTINGS+serif題)+3枚の
  v2カード(card・22px角)。旧`.settings-section`(暫定当て込み)は廃止
- **通知許可**(permission.js): 02の`privacy-ic`アイコン枠(bell)+状態文言+ghost pillの
  「通知の許可を求める」(旧solid accボタンから切替)。permissionTextの4状態写像と
  DI(Notification注入)は無変更
- **ブロック管理**(blocks.js): 一覧は03のsb-row系の様式(表示名700+「M月D日 HH:MM に
  ブロック」meta+解除する=dng縁ghost・`aria-haspopup="dialog"`・表示名入りaria-label)。
  0件は`latch-empty`(破線カード)
- **解除の確認を`<dialog class="dlg" id="unblockDialog">`へ統一**(旧
  `.modal-backdrop` div方式を廃止・Phase 4のsafetyDialogと同型)。題=serif 22px・
  確認文は表示名をstrongで強調・ボタンはsafety系部品(やめる=ghost/解除する=dng
  solid)・backdropクリックとEscで閉じる・404収束と429のdialog維持は既存どおり
- 旧モーダル専用だった`body.modal-open`(overflow:hidden)は使用箇所ゼロのため
  CSSごと削除(`<dialog>`標準のモーダル挙動で代替)

### 3. お知らせの再構成(D-14) — `notice.js`・`chrome.js`・`index.html`

- **統合の判断**: 03のアプリ内トースト(「LATCH候補があります」=即時の着信導線・
  プロトタイプ03が正源)とヘッダー通知ボタンのpopover(履歴一覧・既読化)は**併存**と
  した。両者は同じlatch詳細に着地するが役割が違う(押すタイミング=今/後から見返す)。
  トーストは既存のまま(chrome.showToastの候補カード形)。プロトタイプにpopover中身の
  規定がないため、**popover側をv2部品で再構成**した
- **popover行のv2様式**: 幅320→360px・行=radius 12・未読はacc縁+右上にaccドット+
  「未読」の視覚隠し補読(vh)+未読の先頭行700・既読はline縁・時刻はt3・hoverで
  枠を上げる。空状態は破線カード(NOTICE_EMPTY_*)
- **a11y**: 通知・アカウントボタンに`aria-haspopup="true"`+`aria-controls`を追加・
  **Escでpopoverを閉じてボタンへフォーカスを戻す**処理をchrome.jsへ追加
  (aria-expandedの上下は既存どおり)。popover表示にtoastInアニメ(0.3s)

### 4. 下書き(D-13) — `standby.js`・`confirm.js`・`save.js`・`format.js`

- **03に下書きセクションを追加**: 「ほかに待機中の意思」の下に`sb-others-sec`で
  「下書き」見出し+行(sb-row系)。行=pill「下書き」(gone様式=s2/t3)+原文引用+meta
  (種別 · 「N分前に保存」=新規純関数draftAgoText・下書きに期限行は出さない)+
  「直して預ける」(→`#/confirm?intent={id}`)+「取り下げる」(danger・既存の取り下げ
  dialogとDELETEを共有)。**1件もないときはセクションごと隠す**(空状態の規定なし・
  01の待機セクションと同じ方針)
- **active化の経路**: 02編集モードでGETした`status`を覚え、**下書き編集時はCTA保存の
  PATCHに`status:"active"`を添える**(buildPatchRequestにstatusオプション追加・
  05 §5のdraft→active遷移どおり。検証不通は422でサーバがdraftのまま据え置き)。
  activeなIntentの「条件を直す」は従前どおりstatus省略(現状維持)
- 取り下げ対象の探索をactive+下書きの両リストへ拡張(下書きの取り下げでは
  取り下げ済みカード(goneMain)には入らない)

### 5. 終了状態・空状態のv2様式 — `detail.js`・`styles.css`

- **不成立(closed)・未発見(notfound)をv2カードへ**: 旧の暫定一文表示(p.section-kicker+
  serif 22px裸)から`closed-card`(s1・line縁・24px角・kicker+serif clamp(22〜30px)の
  一文+「ホームへ戻る」ghost)へ。文言は既存の一文統一(CLOSED_TEXT・
  closedText(latch)の二値化・LATCH_NOT_FOUND_TEXT)を**無変更**で流用
- 空状態(ブロック0件・お知らせ0件・一覧0件)は共通の`latch-empty`(破線カード・
  strong=見出し)に統一
- 詳細のnav強調を実態に合わせる(renderProposal→候補・renderMatched→成立・
  renderClosed→候補に`chrome.setCurrentNav`。Phase 1からの暫定(詳細=候補固定)を解消)

### 6. D-11簡易一覧(候補・成立タブ) — `lists.js`(新設)・`router.js`・`main.js`・`index.html`

- phase4-report持ち帰り4の回収。phase0-decisions D-11の最小版どおり、**候補・成立
  それぞれに簡易一覧1画面**(`#/proposals`・`#/matched`)を新設した。様式は03の
  リスト部品の踏襲(44pxカテゴリアイコン+原文/隠蔽文言+meta+状態pill)で、行の選択で
  04/05詳細へ遷移する
- 見出しは承認済みの通知文言(PROPOSAL_NOTICE_TITLE「LATCH候補があります。」・
  MATCHED_NOTICE_TITLE「LATCHが成立しました。」)を流用し、新規の見出し文案は
  作らない。バッジはpv-badge(acc・「LATCH候補 · N件」「成立 · N件」)・空状態は
  既存定数(NO_LATCH_*/NO_MATCHED_*)
- 行のpill=回答まち/成立済みはacc(navバッジと同じ強調)・完了はgone(s2)。metaは
  候補=人数+残時間締切(最小形も残時間は出す=03 §4の様式)・成立=「チャットで
  挨拶を交わしましょう」。データ源はGET /v1/latches(追頁は累積)・バッジ件数を供給
- topnav/tabbarの「候補」「成立」のhrefを`#/`から各一覧へ付け替え(Phase 1からの
  暫定直結を解消)

### 7. a11y総点検

| 項目 | 結果 |
|---|---|
| skip link | `.skip`(本文へスキップ・focusでtop:12px)を全画面で維持 |
| focus-visible | `:focus-visible{outline:2px solid var(--acc);outline-offset:3px}`が全体規定。実ブラウザでEsc後の通知ボタンへ見えるリングを確認 |
| aria-current | topnav/tabbarのdata-nav要素に反映(lists追加で候補・成立も正しく強調) |
| aria-expanded/controls/haspopup | popover 2ボタンにexpanded+controls+haspopup=true。dialogを開くボタン(取り下げ・通報・ブロック・解除・取り消し)にhaspopup=dialog |
| Esc+フォーカス復帰 | popoverをEscで閉じて起動ボタンへfocus(chrome.js) |
| role=timer/log | 03の残時間(timer)・チャット(log)はPhase 3/4のまま維持 |
| aria-live | toast・noticePopover(role=status)・01の読み取りchips(polite) |
| vh(視覚隠し) | 「件」「未読」「（現在の手順）」等の補読を維持・お知らせ未読に追加 |
| reduced-motion | `prefers-reduced-motion: reduce`で全animation/transition停止の全体規定を維持 |
| コントラスト | 下表のとおり全トークンが基準超え |

コントラスト実測(WCAG 2.x比・プロトタイプのトークン値のまま):

| 組み合わせ | 比 | 基準 |
|---|---|---|
| --line-strong #646A8A / --s1 #12141D(入力縁・枠) | **3.47** | 3:1(非テキスト)✓ |
| --line-strong / --s2・--bg | 3.20〜3.69 | 3:1 ✓ |
| --t2 #A9ACBF / --s1・--s2・--bg(補助文字) | 7.54〜8.68 | 4.5:1 ✓ |
| --t3 #8A8DA0 / --s1・--s2(注記) | 5.16〜5.60 | 4.5:1 ✓ |
| --acc/--stb/--dng / --s1(強調色文字) | 7.26〜10.71 | 4.5:1 ✓ |
| --t1 #EDEBF3 / --s1(本文) | 15.55 | 4.5:1 ✓ |

### 8. モバイル(≤720px)確認 — devtools(実ブラウザのviewport 390×844)

- tabbar: fixed表示・4タブ・`aria-current`連動・バッジ(待機中stb・候補/成立acc)を確認
- safe-area: tabbarの`padding:6px 8px calc(6px + env(safe-area-inset-bottom))`・
  `page-main`のpadding-bottom 112px(+inset)を算出値で確認(この環境ではinset=0)
- チャット高さ: 05の`.lt-chat`が`calc(100dvh - 180px)`(实测664px=844-180)で
  入力欄まで1画面に収まることを確認
- 設定・一覧・popoverは390pxで縦積み・折返し(ブロック行はname/date/解除が
  wrap)を確認。popover幅は`min(360px, calc(100vw - 32px))`で収まる

## 実API検証(compose local・2026-10-10)

環境: compose常設api/db/worker+`npm run dev`(vite proxy)+実ブラウザ。
検証ユーザーはverify-phase5-a(consent済み・active1+下書き2)・verify-phase5-b
(未同意・phase5-aにブロックされる側)とPhase 4検証で作ったverify-phase4-aの
実データ(成立1・不成立3・未読4)。

1. **未同意誘導**: phase5-bで接続→`ensureGuidance`が**#/onboardingへ遷移**・
   v2カード(リード+3点+条件+「同意していません」+チェック+無効cta)を表示
2. **同意**: チェックでcta有効化→押下で**POST consent 200**・トースト「同意を
   記録しました」+`#/`へ戻る(スクリーンショット済み)
3. **設定**: #/settingsで3カード(通知=許可済み・ブロック行=phase5-b・同意カード=
   「同意済み: 10月10日 21:38」)。「解除する」→**dlg表示**→「解除する」→**DELETE
   204**で行が消え空状態へ(スクリーンショット済み)
4. **お知らせ**: 未読4件のpopover(未読ドット+「未読」vh+700先頭行+時刻)を表示・
   `aria-expanded=true`・**Escで閉じて通知ボタンへフォーカス復帰**を確認。開いた
   ことで既読化(POST read)が走る
5. **一覧**: #/proposalsで**候補3件**(実データ・隠蔽提案の行+残時間締切+回答まち
   pill+詳細リンク)・#/matchedで成立1件+「チャットで挨拶を交わしましょう」。
   nav強調が候補/成立に追従。成立タブバッジが実件数で点灯
6. **下書きactive化(D-13)**: 03の下書き行「直して預ける」→02編集モード(値復元・
   確認済み)→「この条件で預ける」→**PATCH /v1/intents/{id}(status=active)**→
   `#/standby`で「待機中 · 2件」・**サーバのGET intentsでstatus=activeを確認**・
   残りのraw_textのみの下書きは下書きセクションに残る
7. **終了状態**: phase4-aのcancelled LATCHを開くと**「この提案は成立しませんでした」
   のv2カード+ホームへ戻る**(スクリーンショット済み)
8. **モバイル390px**: 05(チャット664px=calc(100dvh-180px)・tabbar・成立強調)・
   成立一覧・設定の3画面を撮影済み
9. dockerログ(api/worker)にraw_text・原文の漏出なし(01 §21)

## 独自に構成した画面・値(プロトタイプに直接の原型がないもの)

プロトタイプは5画面のみで、本フェーズの画面はD-14どおり**共通部品の踏襲で独自構成**
した(値は全て既存トークン・部品の再利用):

1. **設定画面**(ヒーロー+3カード構成・通知のアイコン枠+ghost pill・ブロック行の様式)
   — 部品源は02のprivacy-card/privacy-ic・03のsb-row・dlg。720pxの
   `page-main--settings`は最大幅の独自設定
2. **オンボーディング**(settingsと同構成のヒーロー+consentカード) — pointsの
   accドットつきリストは02のprivacy-list風の独自構成。ctaの全幅化(align-self:stretch)
   も独自
3. **お知らせpopover**(幅360px・行の未読/既読2姿・右上ドット) — 行の様式は03の
   doing-notify・toastの配色を借用。popover表示アニメにtoastInを転用
4. **D-11簡易一覧2画面**(リスト部品は03踏襲・ヒーローはbadge+serif題のみ) —
   `list-title`のclamp(26px,3.2vw,40px)はsb-title(64px)より小型の独自値。
   `page-main--lists`の880px幅も独自。pill=accはnavバッジの色規約の適用
5. **終了状態カード**(closed-card) — 構成はkicker+serif一文+ghost導線。
   serifサイズclamp(22〜30px)はpv-state-title(22px)の拡張
6. **下書き行** — 03のsb-rowに「下書き」pill(gone様式流用)を添えたのみ。
   「直して預ける」「N分前に保存」「下書き」の文案は新設(D-13の裁量域)
7. **ブロック解除dialog** — safety系部品(dlg+dlg-title/dlg-desc+safety-actions)の
   再利用。題「ブロックの解除」は旧モーダルから維持
8. **consent-cardの状態線**(`consent-state::before`のドット) — stb/t3の2色切替は独自

## 持ち帰り(Phase 6以降への申し送り)

1. **アカウントボタンのイニシャル更新**: 接続直後(me取得前の描画)は空のまま(既存
   挙動・Phase 2から)。トークン接続後のsetAccountInitial呼び出しは未接続
2. **Phase 6(React化)の対象に本フェーズの全DOM配線が加わる**: consent・settings・
   permission・blocks・notice・lists・standby下書き・detail closedの各配線を
   コンポーネント化する(試験は本フェーズで更新済みの意図を踏襲)
3. **一覧の追頁**: 「もっと見る」の累積追頁は実装したが、20件超の大量データでの
   体験(フィルタ・既読反映)は未設計(現行APIの範囲では問題なし)
4. **modal-open機構の削除**: 使用箇所ゼロのため`body.modal-open`のCSS規定を削除した。
   Phase 6でdialog制御を再設計する際は`<dialog>`標準のfocus trapのみで足りる
5. **attendanceの実機確認**: phase4-report持ち帰り3の継続(completed遷移は翌日以降。
   P7の実機E2Eで確認)
