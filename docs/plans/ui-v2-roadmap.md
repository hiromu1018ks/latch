# UI v2移行ロードマップ — 8フェーズで「夜と翡翠」へ全画面を切替える

日付: 2026-10-10 / 出典: [ui-v2-gap-analysis.md](ui-v2-gap-analysis.md)(変更点の全量・コミット8c358be)
デザインの正: `prototype/v2/01〜05`(各`.dc.html`のinline styleと`<helmet><style>`が値の正源)

この文書はUI v2移行の実行計画。本体7フェーズ+オプション1フェーズで進め、各フェーズを
**貼り付けプロンプト1本のセッション1本**で完結させる粒度に揃えてある。判断事項を先に
固定するPhase 0が全体の要で、ここが済むまで画面実装に着手しない。

## 進め方の前提(全フェーズ共通)

- **1フェーズ=1セッション。** 各フェーズ末尾のプロンプトを新しいセッション
  (Claude Code / ZCode)にそのまま貼る。セッションを作り替えてもプロンプトが同じなら
  同じ仕事が再現できるよう、プロンプトは自己完結に書いてある
- **進行の記録。** 実施報告は`docs/plans/ui-v2/phaseN-report.md`へ残し、
  `docs/plans/STATUS.md`の「UI v2」欄(Phase 0が新設)へ1行追記する
- **緑維持。** フロント側のコミットごとに`cd frontend && npx vitest run`が緑のまま。
  画面のDOMに依存する試験は、その画面を担うフェーズが同じコミットで更新する
- **supervisor / herdrフローで回す場合。** 各フェーズのプロンプトを`{TASK}`として
  agent2(計画)→agent3(実装)へ渡してよい。agent1(設計)は省略可 — 本ロードマップと
  ギャップ分析が設計の役を兼ねる
- Phase 0の決定(`docs/plans/ui-v2/phase0-decisions.md`)はPhase 1以降のすべての
  セッションが読む。決定変更が必要になったら実装を止めてユーザーに持ち帰る

## フェーズ一覧

| Phase | 主スコープ | 依存 | 規模 | 完了条件(要約) |
|---|---|---|---|---|
| **0** | 判断14項目の裁断+仕様基準をprototype/v2へ切替 | なし | S | 決定文書へのユーザー承認+docs改版 |
| **1** | デザイン基盤(トークン・フォント・SVGアイコン)と新シェル | P0 | L | 全ルートが新シェルで稼働・vitest緑 |
| **1B** | (オプション・P1と並行)決定分のAPI拡張をbackendへ | P0 | M | backend全套+契約試験緑 |
| **2** | 預けるフロー: 01ホーム+02読み解き確認+保存 | P1(P1B一部) | L | 書く→読み解く→預けるが実APIで通る |
| **3** | 待機中03+候補04(回答状態・取り下げ) | P2 | L | 待機・取り下げ・回答が実APIで通る |
| **4** | 成立05+チャット+通報/ブロック+取消 | P3 | M | 成立〜チャット往復が実APIで通る |
| **5** | 横断機能: 同意・設定・お知らせ・終了状態+a11y | P1〜P4 | M | 全機能がv2デザインで稼働 |
| **6** | 統合試験・実機E2E・配信検証・基準切替完了 | P1〜P5 | M | 全自動試験緑+実機合格+承認 |

Phase間の依存は一直線(P0→P1→P2→P3→P4→P5→P6)。P1Bだけ並行トラックで、
必要なのはP2(02の人数確認・期限)とP3(回答undo・理由)とP4(成立取消)の一部だけ。

---

## Phase 0 — 判断を先に固定し、仕様の基準をprototype/v2へ切替える

**スコープ**: ギャップ分析§5・§6の未決14項目について推奨付きで決定表を起こし、
ユーザー承認を得たうえでdocsの基準を`prototype/`(v1)から`prototype/v2/`へ切り替える。
画面実装は一切しない。

**決定すべき14項目**(詳細な対比はギャップ分析§6の表):

| # | 項目 | 選択肢 |
|---|---|---|
| D-01 | 有効期限の選択肢 | プロトタイプ固定3択 / サーバ供給4択を維持 |
| D-02 | 公開設定・通知設定の選択UI | 02に選択UIを復活 / 仕様側で既定値に固定 |
| D-03 | 原文ハイライト(02) | Parser応答へ文字位置を拡張 / 値マッチングで組む / 見送る |
| D-04 | 「あなたを含むか」人数確認 | structured_dataへ項目追加 / 常時「含む」 |
| D-05 | 回答2択化(「辞退する」廃止) | 認める(03 §5改版) / 3択維持 |
| D-06 | 回答取り消し(undo) | API追加 / UIから外す |
| D-07 | 見送り理由chips | API追加 / UIから外す |
| D-08 | 成立の取り消し | API追加 / UIから外す |
| D-09 | 通報理由4択 | dialog内に維持(API必須のため推奨) / 削除に伴うAPI変更 |
| D-10 | カレンダーに追加 | 実装する(クライアント完結) / 外す |
| D-11 | 「候補」「成立」タブの一覧 | 一覧画面を新設 / 詳細直結(01・03から導線) |
| D-12 | フォント配信 | Google Fonts CDN / 同名フォントself-host |
| D-13 | 下書き・pause/resume | 導線を再設計 / UIから外す(APIは温存) |
| D-14 | プロトタイプに画面のない機能の当て込み | 同意・設定・お知らし・attendanceをv2部品で再デザイン(裁量委託)/ 一部機能をMVP外へ |

**docs改版**(承認後): 00(運用ル則6の基準をv2へ)・03(画面構成の全面改版)・
02・05(UI固定値の変更分)・01(語彙)・12(UI v2フェーズの登録と11 Phase 0との順序)・
STATUS.mdへ「UI v2」欄新設。

**完了条件**: 決定表への承認記録・上記docsの改版コミット・STATUS.mdにUI v2欄。

````text
LATCH UI v2移行 Phase 0(裁断と仕様基準切替)を実施する。
作業ディレクトリ: /home/misty/Projects/latch

# 最初に読むもの(この順)
1. docs/plans/ui-v2-roadmap.md — 本フェーズの定義(決定14項目D-01〜D-14)
2. docs/plans/ui-v2-gap-analysis.md — 変更点の全量(§5・§6が判断事項の根拠)
3. prototype/v2/ の5画面 — 各.dc.html。判断の根拠となるUIはここで確認する
4. docs/00(運用ル則)・03(UX仕様)・02・05・01・12 — 改版対象の現行条文

# 作業
1. docs/plans/ui-v2/phase0-decisions.md を新規作成する。D-01〜D-14の
   1項目ずつに「選択肢・推奨・根拠(プロトタイプ該当箇所とdocs条文)・
   API/docsへの影響」をコンパクトにまとめる
2. 推奨と逆説的な影響(契約試験の増減・backend工数)があれば明記する
3. 「QUESTIONS:」としてユーザーへの決定依頼(14項目の一覧+推奨)を出して止まる。
   ユーザーの回答を決定表へ記録する(未回答の項目は推奨仮決定と明示する)
4. 承認が得られたらdocs改版に着手する: 00の運用ル則6をprototype/v2基準へ、
   03を5画面構成へ全面改版(文言・画面・状態遷移をプロトタイプに合わせる)、
   02・05・01・12へ決定の反映、STATUS.mdへ「UI v2」欄の新設
5. docs/plans/ui-v2/phase0-report.md に経過(決定と改版内容)を書く

# 規律
- 本フェーズでfrontend/・backend/の実装ファイルには触れない
- docs改版はユーザー承認の後。承認前に改版コミットを作らない
- docs/01〜11の日本語を書く・直すときは natural-japanese スキルを使う
- プロトタイプの観点から決定を曲げない。プロトタイプに根拠がない項目
  (D-11・D-12・D-14など)は現行仕様と実装コストで推奨を組む

# 完了条件
- phase0-decisions.md に14項目すべての決定が記録されている
- 上記docs改版がコミット済み。STATUS.mdにUI v2欄がある
- phase0-report.md がある
````

## Phase 1 — デザイン基盤と新シェルに土台を置き換える

**スコープ**: `styles.css`全面書換(カラートークン「夜と翡翠」・共通部品cta/ghost/
danger/lift・dlg・ground・skip・focus-visible)+フォント読み込み(D-12)+SVGアイコン
一式(Phosphor廃止)+`index.html`再構築(新ヘッダー・topnav4タブ+件数バッジ・
モバイルtabbar・右上toast・`<dialog>`)+`router.js`へ新ルート(`#/confirm`・
`#/standby`)のプレースホルダ+`chrome.js`再構成+テーマ切替機構の削除。
**旧画面の内容(入力・詳細・設定・同意)は新シェルの下で暫定表示**とし、機能は
現行のまま等価に保つ(本格移設はP2〜P5。暫定である旨をreportに列挙)。

**スコープ外**: 01〜05の画面本体・API呼び出しの変更・backend。

**完了条件**: 全vitest緑・全ルートが新シェルで閲覧可・720px以下でtabbar表示・
テーマ切替ボタン消失・`prefers-reduced-motion`とskip linkの動作。

````text
LATCH UI v2移行 Phase 1(デザイン基盤と新シェル)を実施する。
作業ディレクトリ: /home/misty/Projects/latch

# 最初に読むもの(この順)
1. docs/plans/ui-v2-roadmap.md — 本フェーズの定義
2. docs/plans/ui-v2-gap-analysis.md §1〜§3 — デザインシステム・シェル・ルーティング
3. docs/plans/ui-v2/phase0-decisions.md — D-12(フォント配信)ほか
4. prototype/v2/01〜05の各.dc.html — 共通<style>ブロック(:root・hdr・tabbar・dlgほか)
   が値の正源。画面固有の値はまだ使わない
5. frontend/ の index.html・styles.css・src/ui/chrome.js・src/router.js

# 作業
1. styles.cssを全面書換する: :rootトークン一式(全5画面の共通<style>を集約)、
   cta/ghost/danger/lift/dlg/tab/navlink/ground/skip/vh/spinner等の共通部品、
   focus-visible、prefers-reduced-motion。旧トークン(--ink/--green/--coral等)は残さない
2. フォントをD-12の決定どおり組む(Google Fonts+preconnect またはself-host)。
   package.jsonから@phosphor-icons/webと不要になった@fontsource/*を外す
3. src/ui/icons.js(または同等)にSVGアイコンを集約する。pathデータは
   プロトタイプのSVGをそのまま写す(独自のアイコンを足さない)
4. index.htmlを再構築する: <head>(theme-color・viewport-fit=cover・フォント)、
   新ヘッダー(ロゴSVG+wordmark+topnav4項目+通知/アカウントボタン)、
   モバイルtabbar、各画面のpage-main骨組み、右上toast、<dialog>の土台。
   テーマ切替スクリプト・data-theme・paperテクスチャを削除する
5. router.jsに#/confirm・#/standbyを追加する(空sectionのプレースホルダ)。
   chrome.jsを再構成する(ヘッダーバッジ更新・aria-current・右上toast・dialog制御)
6. 旧画面の内容を新シェルの下で暫定表示に載せ替える(機能等価を保つ最小の当て込み。
   詳細なデザインは後続フェーズの担当)
7. シェル系の試験(smoke・latch-shell等)を新DOMへ更新し、全vitestを緑にする

# 規律
- デザイン値(色・字号・余白・角丸・影・z-index)はプロトタイプの共通<style>から
  そのまま写す。独自の値を入れた箇所はreportに列挙する
- API呼び出し・backend・docs仕様には触れない
- 毎コミット `cd frontend && npx vitest run` が緑
- 生のIntent文言(raw_text)をログへ出さない(docs/01 §21)
- 10分詰まったら推測で仕様を変えず、状況を書いて停止する

# 完了条件
- 全vitest緑。npm run dev で全ルート(#/、#/latches/{id}、#/settings、#/onboarding、
  プレースホルダ2件)が新ヘッダー/タブバーで閲覧できる
- 720px以下で下部tabbar・safe-area。テーマ切替が存在しない
- docs/plans/ui-v2/phase1-report.md(暫定表示の一覧含む)をコミットし、
  STATUS.mdのUI v2欄へ1行追記する
````

## Phase 1B — (オプション・Phase 1と並行)決定分のAPI拡張をbackendへ通す

**スコープ**: P0で「API追加」と決まった項目のみ(例: 回答undo・見送り理由・
成立取り消し・「あなたを含むか」・ハイライト用の文字位置・期限3択)をbackendに
実装し、契約試験まで通す。追加がゼロなら本フェーズはスキップ。

**完了条件**: `make test`緑・契約試験(local/miniflare)緑・docs/05へのAPI追記。

````text
LATCH UI v2移行 Phase 1B(API拡張)を実施する。Phase 1と並行トラック。
作業ディレクトリ: /home/misty/Projects/latch

# 最初に読むもの(この順)
1. docs/plans/ui-v2/phase0-decisions.md — 追加が決まったAPIの確定仕様
2. docs/plans/ui-v2-roadmap.md 本フェーズの定義
3. backend/src/latch/ — intents・latches・safetyのroutes/schemasと試験の構成
4. docs/05 — API仕様の記法

# 作業
1. 決定表で「API追加」となった項目だけを実装する(スキーマ・routes・service・
   契約・単体・E2E)。決定表にないAPIは作らない
2. docs/05へ追記する
3. docs/plans/ui-v2/phase1b-report.md に実施内容と試験証拠を書く

# 規律
- backendの既存規律(Clock契約・依存追加ゼロ・毎コミットmake lint && make test緑)
  に従う
- frontend/には触れない(呼び出しはP2〜P4が行う)
- 詰まったら推測で仕様を変えず、報告書に書いて停止する

# 完了条件
- backend全套緑・契約試験(local/miniflare)緑・docs/05更新・報告書コミット
````

## Phase 2 — 預ける→読み解く→預け込む流れを通す(01ホーム+02読み解き確認)

**スコープ**: 01(ヒーロー「どんな条件なら、動きますか。」・composer・読み取り
chips・例文4種・CTA「読み解く」・「待機中の意思」カード一覧)と02(3ステップ・
「あなたの言葉」ハイライト・読み取った条件5行+行編集・人数の「含む/含まない」・
足せる条件・公開範囲と通知カード・期限select・CTA「この条件で預ける」)を
実装し、parse→保存(active/draft=D-13決定)を再接続する。`format.js`の表示書式を
v2様式へ。01から02・02から03への遷移(03はP3までプレースホルダで可)。

**完了条件**: 書く→読み解く→預けるが実APIで通る・関係試験緑
(conditions・format・state・parseFlow・saveの更新+02の新規試験)。

````text
LATCH UI v2移行 Phase 2(01ホーム+02読み解き確認)を実施する。
作業ディレクトリ: /home/misty/Projects/latch

# 最初に読むもの(この順)
1. docs/plans/ui-v2-roadmap.md — 本フェーズの定義
2. docs/plans/ui-v2-gap-analysis.md §4(01・02の行単位差分)
3. docs/plans/ui-v2/phase0-decisions.md — D-01〜D-04・D-13
4. prototype/v2/01(Main.dc.html)と02(Confirm.dc.html)— 画面の正。inline styleを写す
5. frontend/src/intent/(screen・conditions・format・state・save・parseFlow・expiry)

# 作業
1. 01を実装する: composerカード(フォーカス時の枠+glow・Zen Old Minchoの入力欄)、
   読み取りchips(aria-live=polite・未読み取りの2状態)、例文chips、CTA「読み解く」
   (読み取り1件以上で有効・busyスピナー)→#/confirmへ。待機中の意思カード一覧
   (GET /v1/intents。候補N件=acc強調/待機中=stb・原文引用・条件タグ・期限行)
2. 02を実装する: 3ステップインジケータ、あなたの言葉(D-03の決定どおり)、
   読み取った条件5行(番号+アイコン+値+サブ+行編集)、人数確認seg(D-04)、
   足せる条件トグル、公開範囲と通知カード+期限select(D-01)、
   CTA「この条件で預ける」(人数確認必須)→保存→#/standbyへ遷移
3. 保存フローを再接続する(02で確定保存。下書きはD-13の決定どおり)
4. format.jsの表示書式をv2様式へ(「今日 10/10(土) 20:00〜」「1人 5,000円まで」等)
5. 試験を更新する: conditions・format・state・parseFlow・save+02の新規試験

# 規律
- デザイン値はプロトタイプ01・02のinline styleを正とする。独自の値はreportに列挙
- API呼び出しは既存(+/P1B追加分)のみ。新たな契約変更は持ち帰る
- 毎コミット `cd frontend && npx vitest run` が緑
- 生のIntent文言(raw_text)をログへ出さない
- 10分詰まったら推測で仕様を変えず、状況を書いて停止する

# 完了条件
- 01→02→保存→03(プレースホルダ可)が実API(miniflare/local)で通る
- 全vitest緑
- docs/plans/ui-v2/phase2-report.md をコミットし、STATUS.mdのUI v2欄へ1行追記
````

## Phase 3 — 待機中と候補を行き来させる(03待機中+04候補)

**スコープ**: 03(着地状態「預けました。」・メイン条件カード・残時間タイマー
`role="timer"` HH:MM:SS・オーブ・「いま、LATCHがしていること」3カード・
ほかに待機中の意思リスト・取り下げdialog=DELETE /v1/intents/{id}・
「条件を直す」→02=PATCH /v1/intents/{id}・右上トースト)と04(ヒーロー・4情報
カード・照合表「あなたの条件との照合」・回答期限タイマー・回答2択(D-05)・
4状態open/joined/allIn/declined・undo・理由chips=D-06/D-07決定分・
目隠しアバタースタック)。01カード群からの遷移接続。

**完了条件**: 待機一覧・取り下げ・条件修正・回答(全状態)が実APIで通る・
関係試験緑(home・detail提案姿・respond・view・texts)。

````text
LATCH UI v2移行 Phase 3(03待機中+04候補)を実施する。
作業ディレクトリ: /home/misty/Projects/latch

# 最初に読むもの(この順)
1. docs/plans/ui-v2-roadmap.md — 本フェーズの定義
2. docs/plans/ui-v2-gap-analysis.md §4(03・04の行単位差分)
3. docs/plans/ui-v2/phase0-decisions.md — D-05〜D-07・D-11
4. prototype/v2/03(Standby.dc.html)と04(Proposal.dc.html)
5. frontend/src/latch/(home・detail・respond・view・texts)とfrontend/src/intent/

# 作業
1. 03を実装する: #/standby。着地状態と通常状態の2姿、メイン条件カード
   (pill・原文引用・条件タグ・残時間タイマー1秒更新・条件を直す・取り下げる)、
   取り下げdialog(<dialog class="dlg">・取り下げ済み状態への切替)、オーブと
   3カード、ほかに待機中の意思リスト(直す/取り下げる)、右上トースト
2. 取り下げ=DELETE /v1/intents/{id}、条件を直す=PATCH /v1/intents/{id}を接続する
3. 04を実装する: 詳細の提案姿を全面再構成。ヒーロー・4情報カード・照合表
   (自分の条件×候補の対比較・「5項目すべて条件内」)、回答期限タイマー
   (残5分でdng色)、回答2択と4状態(open/joined/allIn/declined)、
   undo・理由chipsはD-06/D-07の決定どおり、目隠しアバター
4. 01の「待機中の意思」カード→03/04への遷移を接続する
5. 試験を更新する: home・detail(提案姿)・respond・view・texts

# 規律
(Phase 2と同一: デザイン値はプロトタイプ正・契約変更は持ち帰り・
毎コミットvitest緑・raw_textをログへ出さない・10分詰まったら停止)

# 完了条件
- 待機一覧・取り下げ・条件修正・回答(4状態)が実APIで通る・全vitest緑
- docs/plans/ui-v2/phase3-report.md をコミットし、STATUS.mdへ1行追記
````

## Phase 4 — 成立後の体験を組み上げる(05成立・チャット)

**スコープ**: 05(成立ヒーローsnapアニメ+条件タグ・asideの「成立した内容」
details+メンバー(色アバター)+「安全のために」+通報・ブロック統合dialog=D-09・
「この成立を取り消す」=D-08・カレンダーに追加=D-10)とチャット再構成
(ヘッダー+アバタースタック・`role="log"`・開始システム行・相手=アバター+名前+
時間・単行input+円形送信ボタン・読み取り専用表示)。attendanceはD-14決定どおり。

**完了条件**: 成立〜チャット送受信・通報/ブロックが実APIで通る・関係試験緑
(detail成立姿・chat・report・blockFlow・attendance)。

````text
LATCH UI v2移行 Phase 4(05成立・チャット)を実施する。
作業ディレクトリ: /home/misty/Projects/latch

# 最初に読むもの(この順)
1. docs/plans/ui-v2-roadmap.md — 本フェーズの定義
2. docs/plans/ui-v2-gap-analysis.md §4(05の差分)
3. docs/plans/ui-v2/phase0-decisions.md — D-08〜D-10・D-14
4. prototype/v2/05(Latched.dc.html)
5. frontend/src/latch/(detail・chat・report・blockFlow・attendance)

# 作業
1. 05を実装する: 成立ヒーロー(snap/burstアニメ・成立時刻・条件タグ)、
   aside(成立した内容のdetails+dl・カレンダーに追加=D-10・メンバー(色付き
   イニシャル+参加)・安全のために+通報・ブロック)、取り消しdialog(D-08)
2. 通報・ブロックを統合dialogへ再構成する(理由の扱いはD-09の決定どおり)
3. チャットを再構成する: ヘッダー・role=log・システム行・相手/自分のbubble・
   単行input+円形送信・取消・完了後の読み取り専用表示。
   30秒ポーリング・同期ロジック(src/latch/chat.jsのsync)は現行踏襲
4. attendance(D-09「実際に会いましたか?」)はD-14の決定どおり(維持ならv2様式へ)
5. 試験を更新する: detail成立姿・chat・report・blockFlow・attendance

# 規律
(Phase 2と同一: デザイン値はプロトタイプ正・契約変更は持ち帰り・
毎コミットvitest緑・raw_textをログへ出さない・10分詰まったら停止)

# 完了条件
- 成立〜チャット送受信・通報/ブロック(・取消=決定分)が実APIで通る・全vitest緑
- docs/plans/ui-v2/phase4-report.md をコミットし、STATUS.mdへ1行追記
````

## Phase 5 — 横断機能をv2デザインへ載せ替える

**スコープ**: プロトタイプに画面のない現行機能をv2のデザインシステムで再実装
する — LLM同意オンボーディング・設定(通知許可・ブロック管理・同意)・お知らせ
(notifications+トースト統合=D-14)・下書き(D-13)・終了状態(closed・空状態の
v2様式)。加えて全画面のa11y総点検(skip link・focus-visible・aria一式・
`prefers-reduced-motion`・コントラスト)とモバイル実機確認(safe-area・tabbar)。

**完了条件**: 全機能がv2デザインで稼働・全vitest緑
(consent・settings・permission・blocks・notice・a11y系)。

````text
LATCH UI v2移行 Phase 5(横断機能の載せ替え)を実施する。
作業ディレクトリ: /home/misty/Projects/latch

# 最初に読むもの(この順)
1. docs/plans/ui-v2-roadmap.md — 本フェーズの定義
2. docs/plans/ui-v2-gap-analysis.md §5(プロトタイプにない現行機能)
3. docs/plans/ui-v2/phase0-decisions.md — D-13・D-14
4. prototype/v2/の共通<style>(部品の正源。画面構成はここからの裁量)
5. frontend/src/latch/(consent・settings・permission・blocks・notice)

# 作業
1. LLM同意(オンボーディング+設定内)をv2部品で再実装する(文言は現行の
   承認済み文言を変えない)
2. 設定画面(通知許可・ブロック管理+解除dialog・同意状態)をv2部品で再実装する
3. お知らせを再構成する(通知ボタンのpopoverまたは一覧+右上トーストの統合は
   D-14の決定どおり)
4. 下書き(D-13)・終了状態(他者不成立・期限切れの一文表示)・空状態をv2様式へ
5. a11y総点検: skip link・focus-visible・aria-current/expanded/haspopup・
   role=timer/log・aria-live・vh(視覚隠し)・reduced-motion・コントラスト
   (line-strong/t2の3:1)
6. モバイル(≤720px)の実機またはdevtools確認: tabbar・safe-area・チャット高さ
7. 試験を更新する: consent・settings・permission・blocks・notice

# 規律
(Phase 2と同一。ただし画面構成はプロトタイプに原文がないため、共通部品
(token・cta/ghost/danger・dlg・card)の踏襲を正とし、独自に構成してよい。
独自に構成した画面の一覧と判断理由をreportに残す)

# 完了条件
- 全機能がv2デザインで稼働・全vitest緑
- docs/plans/ui-v2/phase5-report.md をコミットし、STATUS.mdへ1行追記
````

## Phase 6 — 試験と実機で全体を締め、基準切替を完了する

**スコープ**: フロント全試験の緑化(残欠落・スキップの回収)・実機E2E
(実環境: 書く→読み解く→預ける→待機→候補→回答→成立→チャットの往復)・
契約116(P1B変更分の追随)・Workers Assets配信検証(フォント読み込み・
オリジン)・docs最終確認(00・03・12・STATUSの状態が実装と一致)・
ゲート承認の依頼。

**完了条件**: 全自動試験緑・実機E2E合格・docs/STATUS整合・ユーザー承認記録。

````text
LATCH UI v2移行 Phase 6(統合試験と切替完了)を実施する。
作業ディレクトリ: /home/misty/Projects/latch

# 最初に読むもの(この順)
1. docs/plans/ui-v2/ の各phase-report — 残課題と意図した差分の一覧
2. docs/plans/STATUS.md — UI v2欄の全体状況
3. docs/plans/ui-v2-gap-analysis.md §8〜§9 — テストと周辺影響の想定

# 作業
1. frontend全試験を緑化する(report記載の暫定・スキップを回収)
2. 実機E2Eを実施する(実環境・miniflare。フロー: 01入力→02確認→預ける→
   03待機(取り下げ/修正)→04候補(回答4状態)→05成立(チャット・通報/ブロック)。
   証拠はコマンド出力で残す)
3. 契約試験(116+P1B追加分)が緑であることを確認する
4. Workers Assets配信を検証する(フォントの読み込み・CSP・オリジン)
5. docs/00・03・12・STATUS.mdが実装と一致しているか確認し、ズレを直す
6. docs/plans/ui-v2/phase6-report.md に全体の証拠をまとめ、
   ゲート承認をユーザーへ依頼して止まる

# 規律
- 検証は単一コマンドで出力し、パイプで終了コードを隠さない(件数は実査する)
- ズレの修正はdocs/STATUSの記録が「実装に追随」する方向のみ
- 日本語の文書修正は natural-japanese スキルを使う

# 完了条件
- 全自動試験緑・実機E2E合格・docs整合・phase6-report.mdコミット済み
- ユーザーのゲート承認をSTATUS.mdへ記録
````

---

## 補記: フェーズを回すうえでの人力ポイント

- **Phase 0の承認だけは必ず人間が行う**(D-01〜D-14とdocs改版)。以降のフェーズは
  完了条件が機械検証可能なため、supervisorフローなら確認マージで進められる
- 各フェーズのプロンプトはセッションを作り替えても同じ仕事を再現できる前提で
  書いてある。実行中に前提が崩れた(プロンプトの指示が現状と合わない)場合は、
  プロンプトを場当たり的に読み替えず、roadmap側を更新してから進める
- ギャップ分析§6の表と本ロードマップのD番号は1:1。判断の根拠をたどるときは
  ギャップ分析側を参照する
