# UI v2移行 Phase 1 実施報告 — デザイン基盤と新シェル

- 日付: 2026-10-10
- 出典: [ui-v2-roadmap.md](../ui-v2-roadmap.md) Phase 1 / [ui-v2-gap-analysis.md](../ui-v2-gap-analysis.md) §1〜§3・§7 / [phase0-decisions.md](phase0-decisions.md) D-12
- デザインの正: `prototype/v2/01〜05`の共通`<style>`と各部品のインライン指定
- 状態: 完了。全vitest緑(244件)・`vite build`緑・ブラウザで全ルート確認済み

## 実施内容

### 1. styles.css 全面書換(旧1016行→全廃・新規)

- `:root`トークン一式(面s1〜s3・line/line-strong・文字t1〜t3・acc/stb/dng各派生・
  z-index)は5画面の共通`<style>`をそのまま集約。旧トークン(`--ink/--green/--coral/
  --paper`等)と`html[data-theme]`ブロックは残していない
- 共通部品: `cta/ghost/danger/lift`(hover規定込み)・`dlg`+`::backdrop`・`tab/tabbar`
  (≤720pxで固定表示+safe-area)・`navlink`(aria-current)・`ground`(28pxドットグリッド)・
  `skip/vh`・`hdr`(sticky+blur12px)・spinner・`toast-pos/toast`(03の右上トースト)・
  `:focus-visible`(2px acc)・`prefers-reduced-motion`(全停止)
- フォント読み込み: 後述の@fontsource 3書体
- 「暫定画面」章(ファイル末尾): 旧画面クラスをv2トークンで最小整えした当て込み。
  画面本体はPhase 2〜5で差し替わる(一覧は後述)
- paperテクスチャ(png 3点)は`public/assets/textures/`ごと削除

### 2. フォント(D-12: self-host・@fontsource)

- 追加: `@fontsource/zen-old-mincho`(500,700)・`@fontsource/zen-kaku-gothic-new`
  (400,500,700)・`@fontsource/ibm-plex-mono`(400,500,600・latin)
  — Zen 2書体はjapanese+latin両subset。font-family名はプロトタイプと同一
- 削除: `@fontsource/{m-plus-1p,fraunces,klee-one,shippori-mincho}`・
  `@phosphor-icons/web`(package.jsonからも除去)
- CDN(fonts.googleapis.com)は不使用。`npm run build`で全woff/woff2が
  distへバンドルされることを確認

### 3. src/ui/icons.js(新規)

プロトタイプのSVG pathデータをそのまま写したアイコン一式(bell/lock/clock/pin/
people/yen/glass/briefcase/car/info/plus/check/pencil/close/eyeOff/chevronDown/
calendar/shield/flag/send/spinner/latchMark+logoSvg)。独自アイコンは追加していない。
旧`ph ph-*`クラスの出力個所(conditions.js行アイコン・編集ボタン・report/block/
blocks各modalの閉じるボタン・chrome.js確認モーダルのclock/lock)をSVGへ置換。
index.htmlの静的アイコンもプロトタイプのSVGを直接記載。

### 4. index.html 再構築

- `<head>`: theme-color `#0B0C12`・viewport-fit=cover・テーマ切替スクリプト削除
- 新ヘッダー`.hdr`: ロゴSVG(左右2弧)+wordmark(IBM Plex Mono 17px・字間.34em)+
  topnav4項目(預ける/待機中/候補/成立・件数バッジ付き)+通知ボタン(円形ghost+
  新着dot)+アカウントボタン(円形・イニシャル1字)
- モバイルtabbar(4タブ+バッジ・≤720pxで表示)、右上toast(`#toast`・toast-pos)、
  `<dialog class="dlg" id="appDialog">`の土台
- data-theme・paperテクスチャ参照は削除
- 旧画面のDOM(ID・クラス)は暫定表示として維持(機能等価のため)

### 5. router.js・chrome.js・main.js

- router: `#/confirm`(02)・`#/standby`(03)を追加(厳密一致)。sectionは
  プレースホルダ(空でない最小の見出し+説明1行)
- chrome.js再構成: ①`setCurrentNav`(topnav+tabbarのaria-current)②`setCounts`
  (待機中=stb/候補・成立=accのバッジ。0件は非表示)③`setAccountInitial`
  ④右上toast(自動消滅2400msは現行踏襲)⑤`openAppDialog/closeAppDialog`(土台)⑥
  popover(お知らせ・アカウント)⑦旧確認モーダル(暫定)。テーマ切替・
  「Intent N件」popover機構は削除
- main.js: screensへconfirm/standbyを追加、NAV_BY_ROUTE(home/confirm→預ける・
  standby→待機中・latch→候補〔暫定〕・設定/同意は強調なし)、homeのonCounts→
  chrome.setCounts配線、アカウント初期表示のためensureMe(1回)
- home.js: intentCount/intentList(popover)を削除しonCountsコールバックへ
  (latches=候補/成立数・GET /v1/intents?status=active=待機中数)。下書き件数の
  取得・表示はD-13の導線再設計(Phase 2/3)まで一時的に消失

### 6. 試験

smoke・latch-shellを新シェルへ全面更新(テーマ切替の不在・CDN不使用・tabbar・
dialog・バッジ・SVG化を検証)。router(confirm/standby)・latch-home(onCounts)・
conditions(SVGアイコン)を追随更新。**236件→244件すべて緑**。

## 暫定表示の一覧(Phase 2〜5で差し替え)

| ルート / 部位 | 現状(暫定) | 移管先 |
|---|---|---|
| `#/` 入力画面 | 旧画面のまま(見出し「やりたいことを、一文だけ。」・条件リスト5行+行編集・預け方サイドバー3select・文字数カウンタ・下書き保存・⌘Enter)。v2トークンで最小整えのみ | Phase 2(01 composer+02読み解き確認へ分離) |
| `#/` 確認モーダル | 旧「あとは、置いておくだけ。」モーダル(modal-backdrop方式) | Phase 2(02→03遷移で廃止) |
| `#/` LATCH候補・成立済み一覧 | 旧latch-card(見出し=日時+場所・一致度バッジ・残時間) | Phase 3/4(D-11の簡易一覧画面) |
| `#/latches/{id}` 詳細3姿 | 旧表示(3択回答・一致度・参加者リスト・チャット・attendance・通報/ブロック各modal) | Phase 3(04)・Phase 4(05+チャット) |
| `#/settings`・`#/onboarding` | 旧表示(通知許可・ブロック管理・LLM同意) | Phase 5 |
| お知らせ・アカウントpopover | dlg近傍の値で暫定構成 | Phase 5(D-14) |
| DEVトークンパネル | v2トークンで最小整え | Phase 5 |
| `#/confirm`・`#/standby` | プレースホルダ(見出し+説明1行) | Phase 2・Phase 3 |
| topnav「候補」「成立」のリンク先 | `#/`(一覧がホームにしかないため) | Phase 3/4(一覧画面新設後) |

## 独自に構成した値・判断(プロトタイプに直接の原型がないもの)

- **クラス名・DOM構造**: プロトタイプはインラインstyleのため、`count-pill`/`tb`の
  色修飾(stb/acc)・`icon-round`・`brand`・`toast-msg/toast-close`等のクラス名と
  バッジDOMはこちらの構成。**値**(色・字間・padding)はプロトタイプの該当指定を写した
- **右上toastの内容**: 03のトーストは「LATCH候補があります」固定構造(アイコン箱+
  「見る」リンク)。汎用トースト(下書き保存・通報受付等)のため本文+閉じるボタンの
  単純化した構成とし、自動消滅2400msは現行挙動を踏襲(03は手動閉じのみ)
- **`.page-main`既定値**: 01の値(max-width 1120px・padding clamp(40px,7vw,104px)
  clamp(16px,4vw,48px) 120px・gap clamp(56px,8vw,88px))を全画面の既定に使用。
  02〜05の個別値(1200/1240px等)は各画面実装フェーズで上書きする
- **h1字号**: 01ヒーロー値clamp(38px,5.6vw,76px)は`#page-title`のみに適用。
  設定・同意等の一般h1は01のセクション見出し値clamp(26px,3vw,30px)(Zen Old Mincho 700)
- **旧画面の暫定スタイル**: レイアウト骨格(ワークスペース2カラム等)はv1から
  踏襲。入力部品は02のfield値(radius 12・line-strong・s1)、カード・チップ・ボタンは
  プロトタイプの部品値を流用。ブレークポイントは800/560px→**720px**へ統合
- **回答3択ボタン**: プロトタイプ04は2択(参加する=cta/見送る=ghost)のため直接の
  原型なし。「参加する」(yes)のみcta、残る2つはghostとして組んだ(Phase 3で2択化)
- **旧モーダル群**(確認・通報・ブロック・解除): `<dialog>`化はP2/P4/P5の担当のため
  `modal-backdrop`方式のまま、外観を`.dlg`と同値(min(440px,100%−32px)・padding 28・
  radius 24・line-strong・s1・backdrop rgba(5,6,10,.72))へ揃えた。
  `.confirmation-mark`(52px acc-soft円)は原型なしの組成
- **popover(お知らせ・アカウント)**: プロトタイプにUIがないためs1+line-strong+
  radius 18+影0 24px 60px -20px rgba(0,0,0,.7)(トーストの影値)で暫定構成
- **section-kickerの点**: v1の縦棒に代え7px円・stb(01ビーコンと同径同色)。
  kicker文字(12px/700/ls .12em/t3)は組成
- **appDialog**: 03 drop-dlgの構成を一般化。アクション3様(ghost=やめておく/
  solid=02「保存」のt1地/danger=取り下げる)は各ボタンのプロトタイプ値から。
  backdropクリックで閉じる追加(Escは`<dialog>`標準)
- **nav強調の写像**: 詳細(`#/latches/{id}`)はstatus不問で「候補」扱い(Phase 3/4で
  提案/成立を分岐)。設定・同意は強調なし
- **アカウント初期1字**: 起動時に`GET /v1/users/me`を1回呼ぶ(失敗時は空表示)。
  v1はハードコード「たろう」だった

## 検証

- `cd frontend && npx vitest run`: **24 files / 244 tests すべて緑**
- `npm run build`: 成功(フォントwoff/woff2含む)
- ブラウザ(Chromium)実機確認: `#/`・`#/confirm`・`#/standby`・`#/settings`・
  `#/onboarding`が新ヘッダーで閲覧可・aria-currentが経路に追従。1280px幅で
  topnav4項目・390px幅でtopnav非表示+下部tabbar(4タブ)・page-mainの
  padding-bottom 112pxで最終コンテンツがtabbarに隠れないことを確認。
  テーマ切替ボタン・data-themeは不在

## 後続フェーズへの引き継ぎ

- Phase 2: 01/02実装時に`#page-title`・暫定入力画面・確認モーダルを廃止。
  composerのfocus-withinグロー・beacon/chipIn等の画面固有アニメはstyles.cssに
  未導入(共通部品のみ導入済み)
- Phase 3/4: D-11一覧画面の新設時にtopnav「候補」「成立」のリンク先と
  NAV_BY_ROUTEのlatch分岐を更新。Intent一覧・下書き一覧の復活(D-13)
- Phase 5: popover・DEVパネル・同意・設定の再デザイン。旧モーダルの`<dialog>`化
- 05のチャット入力(単行input+円形送信)はPhase 4まで旧形式
