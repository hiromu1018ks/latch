# UI v2(プロトタイプ「夜と翡翠」)移行 — 変更点の完全洗い出し

日付: 2026-10-10 / 対象: `frontend/`(クライアントサイド) / 参照物: `prototype/v2/`(同日コミット 4709026)

プロトタイプv2(`prototype/v2/01〜05`)にクライアントサイドを**完全一致**させる場合に
変更が必要なすべての事項を洗い出した。プロトタイプはデザインリファレンスであり、
複製の正は各 `.dc.html` のインライン `style` と `<helmet><style>`(各フォルダREADME参照)。

**結論の要約**: デザインシステム(色・フォント・アイコン・部品)の全置換に加え、
画面構成そのものが「単一ホーム中心」から「5画面フロー(預ける→読み解き確認→待機中→
候補→成立)」へ変わるため、`frontend/`は事実上の全画面作り直し(スタイルは全面書換、
JSはロジック層の純関数のみ流用可)。加えてプロトタイプが要求するが現行APIにない機能
と、逆にプロトタイプに画面が無い現行必須機能(同意・設定ほか)の裁断がそれぞれ必要。

---

## 1. デザインシステムの全置換(styles.css・index.html head・package.json)

| 項目 | 現行 | プロトタイプv2 | 変更 |
|---|---|---|---|
| テーマ | ライト(和紙)基調+ダーク切替(`data-theme`・localStorage・`?theme=`) | ダーク「案A改 夜と翡翠」固定(`color-scheme:dark`) | テーマ切替機構ごと廃止 |
| 色トークン | `--ink/--green/--coral/--paper/--line/--muted` 等+テクスチャpng | `--bg #0B0C12 / --s1 #12141D / --s2 #191C29 / --s3 #20243A / --line #23273A / --line-strong #646A8A / --t1 #EDEBF3 / --t2 #A9ACBF / --t3 #8A8DA0 / --acc #5FD4AE(+hover #7FDEBF・soft・line .4・glow .45・haze .1・on-acc #06180F) / --stb #9DB2DB(+soft .12・line .3) / --dng #F2837A(+soft .12・line .5) / --z-header:30 --z-tabbar:30 --z-toast:40 --z-skip:60` | トークン名ごと全面入れ替え |
| フォント | M PLUS 1p / Fraunces / Klee One / Shippori Mincho(@fontsource self-host) | Zen Old Mincho 500,700(見出し・原文引用)/ Zen Kaku Gothic New 400,500,700(本文)/ IBM Plex Mono 400,500,600(数字・時刻・ロゴ・バッジ)、Google Fonts CDN+preconnect | @fontsource依存全除去。**判断**: CDNを許容するか同名フォントをself-hostするか(オフライン・プライバシー・Workers Assets配信方針) |
| アイコン | Phosphor Icons Webフォント(`ph ph-*`) | すべてインラインSVG(stroke 1.6〜2.4・currentColor) | `ph-*`クラス全廃・SVGをコンポーネント化 |
| 背景 | paper texture PNG(620pxタイル) | `.ground` ドットグリッド(radial-gradient 28pxタイル)+画面ごとの翡翠haze放射(位置・大きさは画面毎に固定値) | テクスチャpng廃止・`public/assets/textures/`削除可 |
| 角丸・ボタン部品 | 角丸3〜16px・submit-button用テクスチャ画像 | pill(999px)主体、カード22〜28px、アイコン枠12px、ボタン高44/52/54/58/64px、`cta`(acc・glow)/`ghost`(line-strong枠)/`danger`(dng)の3系統+hover規定 | 既存ボタン系クラス全廃 |
| フォーカス | `outline: 3px solid rgba(coral,.62)` | `:focus-visible{outline:2px solid var(--acc);outline-offset:3px}`+`main:focus{outline:none}` | 変更 |
| アクセシビリティ | ― | スキップリンク「本文へスキップ」(`.skip`)、視覚隠し`.vh`(「件」等の補読)、`aria-current="page"`、`role="timer"`、`role="log"`、`aria-live`、`aria-busy`、`aria-haspopup="dialog"` | 新規実装 |
| モーション | `rise` など少数 | `beacon`(待機パルス)/`chipIn`/`rowIn`/`spin`/`ripple`(オーブ)/`breatheL・R`(アーク)/`toastIn`/`rise`(候補)/`snapL・R`+`burst`(成立)/`msgIn`(チャット) — `prefers-reduced-motion`で全停止は踏襲 | アニメーション新規一式 |
| meta | `theme-color #f7f4ec`・title「新しいIntent \| LATCH」 | `theme-color`→`#0B0C12`・`viewport-fit=cover`(safe-area対応)・titleは画面毎「LATCH — 預ける」等 | 変更(safe-areaはtabbar・chat高さ計算に必須) |

## 2. アプリシェルの変更(index.html本体・src/ui/chrome.js)

現行: 固定topbar(Fraunces wordmark +「Intent N件」popover + テーマ/通知/アカウントpopover)。
新(全画面共通):

1. **ヘッダー**(`.hdr`・sticky・blur(12px)・border-bottom): LATCHロゴSVG(左右2弧=現行ロゴと同型)+`IBM Plex Mono` wordmark(17px・letter-spacing .34em)
2. **topnav 4リンク**: 「預ける / 待機中(N件・stbバッジ) / 候補(N件・accバッジ) / 成立」— pill型navlink・`aria-current="page"`で当該強調(s2背景)。**バッジ件数は動的値**
3. **右側**: 通知ボタン(円形ghost+新着ドット)・アカウントボタン(イニシャル1字・円形)
4. **モバイル(≤720px)**: topnav非表示→**下部固定tabbar**(4タブ+バッジ`.tb`・safe-area-inset-bottom・blur)+`.page-main`のpadding-bottom追加(112px+)。ブレークポイントが現行の800/560pxから**720pxへ変更**
5. **廃止**: テーマ切替ボタン、「Intent N件」popover(Intent一覧は「待機中」画面へ移管)
6. **残す方の裁断**: 通知popover(お知らせ一覧)・アカウントpopover(設定への導線)はプロトタイプに詳細が無い → §6参照

## 3. 画面構成・ルーティング(src/router.js・src/main.js)

現行ルート: `#/`(=入力+候補一覧+成立済み一覧の単一ホーム)、`#/latches/{uuid}`(3姿詳細)、
`#/settings`、`#/onboarding`。

プロトタイプv2は**5画面フロー**で、新規画面が2つ増える:

| v2画面 | 相当ルート(案) | 現行との対応 |
|---|---|---|
| 01 預ける(ホーム) | `#/` | 現行ホームの入力側を再構成+「待機中の意思」一覧(現行のlatches一覧ではなく**Intent一覧**がデータ源) |
| 02 読み解き確認 | 新規(例: `#/confirm`) | **現行に存在しない画面**。現行の条件リスト行編集がここへ分離 |
| 03 待機中 | 新規(例: `#/standby`) | **現行に存在しない画面**。Intent一覧popover・下書き一覧の代替 |
| 04 候補が届く | `#/latches/{id}`(proposal姿) | 現行詳細の提案姿を全面再構成 |
| 05 成立・チャット | `#/latches/{id}`(matched姿) | 現行詳細の成立姿を全面再構成 |

- 遷移: 01「読み解く」→02→「この条件で預ける」→03(`?deposited=1`相当の着地状態)。
  **現行の確認モーダル(「あとは、置いておくだけ。」)は廃止**し02→03遷移で代替
- `#/settings`・`#/onboarding`は維持(§6)。routerのテスト(`router.test.js`)に新ルート追加
- **判断**: ナビ「候補」「成立」の一覧画面がプロトタイプに無い(04・05は個別詳細)。
  複数件時の一覧を設けるか、01「待機中の意思」カード→候補直結・成立タブは最新1件直結とするか

## 4. 画面別の変更点

### 01 預ける(ホーム) — `index.html` #homeScreen・`src/intent/screen.js`

現行(2カラムworkspace: 左=入力+条件リスト、右=預け方サイドバー)→ 新(単一カラム):

- 見出し: 「やりたいことを、一文だけ。」→**「どんな条件なら、動きますか。」**(Zen Old Mincho・clamp(38〜76px))+リード文+「N件の意思が待機中」行(stbビーコン)
- **composerカード**(radius 28px・line-strong枠・acc glow・focus-withinで枠+glow強化): ラベル「条件をひとことで」+textarea(rows=3・min-height 128px・**Zen Old Mincho clamp(22〜30px)**・placeholderは例文)
- **読み取り表示の形式変更**: 現行=条件リスト5行(アイコン/ラベル/値/行編集)+「条件を追加」フォーム → 新=**chips**(label=acc色小字+値)をcomposer内に`aria-live="polite"`で即時表示。0件時はガイド文、入力済みだが読み取れない時はstb情報ボックス(「まだ条件を読み取れません。…」)
- 例文chips(飲み/転職/売買/集まり=ghost pill・タップでtextareaへ) — **新規**
- 鍵アイコン+「成立するまで、この条件は誰にも見えません」行
- CTA: 「このIntentを預ける」+下書き保存+⌘Enter → **「読み解く」**(読み取り≥1件で有効・busy時スピナー+「読み解いています…」+ヘルプ文言cta-help)→02へ遷移
- **廃止・移動**: 右サイドバー「預け方」全廃(有効期限・公開設定・通知の3select→§4-02の「公開範囲と通知」カードへ集約)、文字数カウンタ(0/300)はプロトタイプに無し、条件リスト行編集は02へ
- **「待機中の意思」セクション**(見出し+「すべて見る」リンク→03): カード=カテゴリSVG+「飲み · 12分前に預けた」+状態pill(**候補N件=acc**強調カード or **待機中=stb**)+原文引用(Zen Old Mincho 19px)+条件タグ(s2チップ)+期限行+「候補を見る」。
  現行`latch-card`(title=日時+場所・badge=回答まち/一致度高・coral残時間)と**構造もデータ源も違う**(GET /v1/intents 中心)。grid `repeat(auto-fit,minmax(min(300px,100%),1fr))`

### 02 読み解き確認 — 新規画面(`src/intent/conditions.js`の移設・再構成)

- 3ステップインジケータ(書く✓→「読み解きを確認」02=acc発光→「預ける」03)
- 見出し「こう読み解きました。」+「あなたの言葉」カード: **原文を`<mark>`+条件番号`<sup>`でハイライト**・「書き直す」(→01)。※番号↔条件行の対応
  **判断**: parse API応答に文字位置(span)がないため、現行応答からハイライト位置は組み立て不可。LLM側拡張または値マッチングのどちらにするか
- 「公開範囲と通知」カード(3項目・アイコン枠付き): ①「成立するまで、誰にも表示されません」②「通知は『LATCH候補があります』の一文だけ」③「{期限} に自動で取り下げ」+**期限select 3択(今日22:00/23:59/明日5:00)**
  **判断**: 期限は現行サーバ供給4択(GET /v1/intents/expiry-options)。3択固定にするかサーバ側追従か。また**公開設定(2値)・通知設定(3値)のselectがプロトタイプから消えている**(visibility=summary_only・notification_level=muted等の選択導線の要否)
- 「読み取った条件」カード(glow枠): 5行(時間/場所/人数/予算/目的)=40pxアイコン枠+`01`番号(mono・acc)+ラベル+**値21px 700**+サブ説明(例:「鹿児島市 · 歩いて集まれる範囲」)+行末編集ボタン。行編集は**テキスト1行input+保存/キャンセル**に単純化
  **判断**: 現行はタイプ別構造化編集(datetime-local/数値/目的select)。APIへ構造化送信する以上、単純テキスト入力のままPUT/PATCHするかは要調整(PATCH /v1/intents/{id}は存在)
- **人数の確認UI(新規)**: 「『2〜4人』に、あなたは含まれますか?」含む/含まないのsegトグル。未回答=「要確認」(acc)でCTA無効・回答=「確認済み」(stb)。「あなたを含む」情報はstructured_data participantsに**現行スキーマにない**(要確認)
- 「足せる条件(任意)」トグルchips(禁煙の店/2時間くらいで解散/全員はじめまして)— soft_constraintsへ格納(自由ラベルか固定ラベルか要判断)
- CTA「この条件で預ける」(人数確認必須)+「書き直す」(ghost)

### 03 待機中 — 新規画面(Intent一覧・取り下げ)

- 着地状態(`deposited`): 見出し「預けました。/あとは、待つだけ。」/通常:「条件が揃うのを、/待っています。」+badge「待機中 · N件」(stb)
- **メイン条件カード**: pill待機中(stb)・原文引用・条件タグ・**残時間タイマー**(`role="timer"`・HH:MM:SS・IBM Plex Mono 30px・1秒更新)・「条件を直す」(→02:PATCH /v1/intents/{id}で対応可)・「取り下げる」(danger→dialog)。取り下げ後は「取り下げました」状態+「もう一度預ける」
  ※取り下げAPI=**DELETE /v1/intents/{id}(既存)**。現行フロントは未実装
- **オーブ**(円環+2アークbreatheアニメ+ripple×3+条件チップ浮遊) — 装飾・新規実装
- 「いま、LATCHがしていること」3カード(01照らし合わせ続ける/02成立し得るかだけを見る/03揃ったらひとこと+通知プレビュー)
- 「ほかに待機中の意思」リスト(1行=44pxカテゴリアイコン+原文+meta「転職 · 38日前に預けた · 期限なし」+待機中pill+直す/取り下げる)。空状態「ほかに待機中の意思はありません。」
- **取り下げdialog**(`<dialog class="dlg">`+::backdrop): 条件引用+「やめておく/取り下げる」。現行のdiv.modal-backdrop方式から`<dialog>`要素へ(全dialog共通)
- **通知トースト(右上・toast-pos)**: 「LATCH候補があります」+見る(→04)+閉じる。現行toast(下中央・自動消滅)と位置・構造が違う
- **判断**: 現行APIの**pause/resume**(POST /intents/{id}/pause|resume)をUIに出すか(proトタイプに無い)

### 04 候補が届く — `src/latch/detail.js` proposal姿の全面再構成

- ヒーロー: badge「LATCH候補 · 19:14 に届きました」(acc)+**「条件が、揃いそうです。」**+リード+**4情報カード**(開始20:30(mono)/エリア天文館/人数3人「あなた＋2人」/予算¥4,000(mono))
- **オーブ**(acc色版・ripple)
- **「あなたの条件との照合」(新規UI)**: 5行=ラベル/あなたの条件→矢印→候補(700)+✓、見出し横「5項目すべて条件内」。
  **判断**: 現行APIのproposal(time_summary等)だけでは「自分の条件 vs 候補」の対比較行が組めない(intent側データと突合せ、またはAPI拡張)。現行の「一致度 高/中/低」バッジ(MATCH_LEVEL_TEXT)はこの形式に置き換わる
- **回答期限タイマー**: 分:秒・1秒更新・**残5分以下でdng色**(timer-low)。現行は30秒毎の「あとN時間で締切」テキスト→書式・更新頻度とも変更
- 参加者表示: 自分=イニシャル、相手=**目隠しアイコン(点線円)**のアバタースタック+「あなた ＋ まだ見えない2人」
- **回答UI**: 現行3択(参加する/今回は見送る/**辞退する**)→**2択(参加する64px cta/今回は見送る52px ghost)**+注記。
  **判断**: APIはyes/no/deferの3値。「辞退する(no)」の導線を切ってよいか(仕様03 §5と要整合)
- **4状態表示(新規)**: ①open(未回答) ②joined=「参加と回答しました。」+「ほかの2人の回答を待っています(1人が回答済み)」+**「回答を取り消す」** ③allIn=「3人全員が参加しました。」+「チャットを開く」 ④declined=「見送りました。」+**理由chips(任意・複数: 時間/場所/人数/気分)**+「待機中に戻る」+「取り消す」
  **判断**: 「回答を取り消す」(回答済み→openへ戻す)APIなし。**見送り理由の送信先APIなし**。担保するならbackend改修、しないならUIから外す
- 現行の「あとN人の回答が必要」(partial_accept行)は②の文脈へ統合

### 05 成立・チャット — `src/latch/detail.js` matched姿・`src/latch/chat.js`の全面再構成

- **成立ヒーロー**: snapアニメ(2弧が閉じる)+burst+「成立 · 19:16」+**「LATCHしました。」**+条件タグ4つ。取消後は「参加を取り消しました。」
- 左aside(≥960pxで2カラム/モバイルで縦積み):
  - 「成立した内容」`<details>`: dl(時間/場所「集合場所はチャットで」/人数/予算)+**「カレンダーに追加」ボタン(新規・.ics生成はクライアント完結可)**
  - 「メンバー」: **色付きイニシャルアバター**(Y=#F2B880/M=#F09AC0/K)+名前+「参加」✓+自分行+「成立したので、お互いの表示名が見えるようになりました。」
  - 「安全のために」: 安全ガイド`<details>`(3箇条)+**「通報・ブロック」ボタン→統合dialog**
  - **「この成立を取り消す」**(danger文字ボタン)→dialog(影響3条+「元に戻せません」)
- **通報・ブロックdialog(統合・新規フォーム)**: 対象メンバーラジオ+説明+「ブロックする」(ghost dng)/「通報する」。
  **判断**: 現行は通報(対象+**理由4択**)/ブロックが別modal。プロトタイプは理由選択なし・1dialog統合。理由(reports API必須項目)の扱い
  **判断**: **「成立の取り消し」APIなし**(chat読み取り専用化のみ)。仕様追加かUI削除か
- **チャット(現行から構造変更)**: ヘッダー(「今夜の3人」+条件+アバタースタック)/`role="log"`/開始システム行「19:16 · LATCHが成立し、チャットが開きました」(acc pill)/相手=アバター+名前+時間+bubble(s2・radius 18 18 18 6)/自分=右(bubble #262B42・radius 18 18 6 18)/システム=中央dng pill/入力=**単行text input**(enterkeyhint=send)+**50px円形送信ボタン**(紙飛行機SVG・空でdisabled)/取消後placeholder「取り消したため、書き込めません」/高さ指定`calc(100dvh-180px)`(モバイル)
  機能面(30秒poll・500件上限・trimしない送信)は維持可。1000字上限を単行inputで運ぶかは微調整
- **現行の参加確認(D-09「実際に会いましたか?」=POST attendance)はプロトタイプに無し** → 維持するならv2デザインを当てる(判断)

## 5. プロトタイプに画面が存在しない現行必須機能(デザイン適用の裁断が必要)

これらは機能として残す前提(仕様・APIは現存)だが、v2プロトタイプに該当画面が無いため
**v2のデザインシステム(色・フォント・部品・dialog)を踏襲した独自の当て込み**が必要:

1. **LLM送信の同意(オンボーディング+設定)** — 422 CONSENT_REQUIRED誘導・マッチング利用条件(M4 ws-5)。`#/onboarding`・consent.js
2. **設定画面**(`#/settings`): 通知許可(DI)・ブロック管理(一覧+解除dialog)・同意状態
3. **お知らせ一覧popover**(notifications API・既読化): プロトタイプの通知ボタンは中身未定義。03のトースト「LATCH候補があります」との統合も含め判断
4. **下書き保存・下書き一覧**(POST intents status=draft・GET ?status=draft): 01から下書きボタンが消え、03に下書き欄なし。機能維持なら導線の再設計(判断)
5. **DEV認証トークンパネル**: 開発用のためv2デザインで最小整える
6. **不成立(closed)表示**: 04 declined(見送り)は自分操作時のみ。他者の見送り・期限切れ(「この提案は成立しませんでした」)のv2様式が無い
7. **Intent pause/resume**: APIのみ存在(UI両方とも無し・継続保留で可)

## 6. プロトタイプが要求するが現行に無いもの(機能追加・仕様変更の裁断)

| 要件 | 出典 | 現状 | 対応候補 |
|---|---|---|---|
| 原文ハイライト(mark+条件番号) | 02 | parse応答に文字位置なし | LLMスキーマ拡張 or 値マッチング |
| 期限3択固定(22:00/23:59/翌5:00) | 02 | サーバ4択供給(expiry-options) | どちらかへ統一 |
| 公開設定・通知設定の選択UI | 01→(消滅) | 2値/3値select実装済み | UI復活or仕様側で固定値化 |
| 「あなたを含むか」人数確認 | 02 | structured_dataに項目なし | スキーマ追加 or 常時「含む」 |
| 回答取り消し(undo) | 04 | APIなし(回答は確定) | API追加 or UI削除 |
| 見送り理由chips(時間/場所/人数/気分) | 04 | APIなし | API追加 or UI削除 |
| 回答2択化(noの廃止) | 04 | yes/no/deferの3値 | 仕様03 §5と整合確認 |
| 成立の取り消し(書込不可化+システム行) | 05 | APIなし | API追加 or UI削除 |
| 通報理由の廃止(通報するのみ) | 05 | reports APIは理由必須4択 | 理由選択をdialog内に残す等 |
| カレンダーに追加(.ics) | 05 | なし | クライアント完結で追加可 |
| 「候補」「成立」タブの一覧 | nav | 一覧はlatches APIに存在 | 一覧画面を足すか直結にするか |
| 通知トースト「LATCH候補があります」(アプリ内) | 03 | お知らせpopoverのみ | トースト実装+既存popover整理 |
| ヘッダー/Navバッジの件数(待機中・候補・成立) | 全画面 | Intent数のみ(topbar) | 3系統のカウント供給(GET集約) |

## 7. ファイル影響マップ(frontend/)

| ファイル | 影響 |
|---|---|
| `index.html` | **全面書換**(head・シェル・5画面分のDOM・dialog・tabbar) |
| `styles.css` | **全面書換**(§1〜4の全スタイル。現行1016行は全廃) |
| `package.json` | `@fontsource/*`・`@phosphor-icons/web`除去(フォント方針確定後) |
| `src/ui/chrome.js` | 全面再構成(navバッジ・aria-current・tabbar・右上トースト・`<dialog>`制御)。popover/theme機構は削除 |
| `src/router.js` | ルート追加(confirm・standby等)+既存テスト更新 |
| `src/main.js` | 配線全面変更(画面初期化の再編) |
| `src/appState.js` | 概ね無傷(me取得) |
| `src/api/client.js` / `session.js` | 無傷(通信・認証は不変) |
| `src/intent/screen.js` | 01 composer化(chips・例文・CTA→confirm)。右欄・カウンタ廃止 |
| `src/intent/conditions.js` | 02の行構造(番号+sub+単行編集+人数seg)へ全面再構成 |
| `src/intent/format.js` | 表示書式をv2様式へ(「今日 10/10(土) 20:00〜」「1人 5,000円まで」「¥4,000」等) |
| `src/intent/state.js` / `parseFlow.js` / `save.js` / `expiry.js` | ロジック維持・連結先変更(saveは02経由の確定保存へ) |
| `src/latch/home.js` | 「待機中の意思」カード(Intents API)へ書換。latches一覧は04/05直結と一覧判断で再編 |
| `src/latch/detail.js` | proposal→04・matched→05へ全面書換 |
| `src/latch/chat.js` | UI構造変更(§4-05)。poll等ロジック維持 |
| `src/latch/respond.js` | 2択+状態4分岐+undo(裁断後) |
| `src/latch/texts.js` | **文言全面更新**(v2語彙: 預ける/読み解く/待機中/候補/成立/取り下げる。「Intent」語の駆逐) |
| `src/latch/view.js` | 純関数の書式・分岐更新(残時間HH:MM:SS化・照合行組立・closed二値化の維持) |
| `src/latch/notice.js` / `blocks.js` / `report.js` / `blockFlow.js` / `permission.js` / `consent.js` / `settings.js` / `attendance.js` | §5の裁断後、v2デザインへ再実装 |
| `public/assets/textures/` | 削除(テクスチャ廃止) |

## 8. テスト影響(frontend/tests・現行236件)

- **DOM・文言・クラス名依存は全面更新**: `smoke`・`latch-shell`・`latch-home/view/detail/chat/respond/notice/settings/blocks/report/consent/attendance/block-flow`(innerHTML比較・クラス・文言)
- **純関数系は部分更新**: `conditions`・`format`(書式)・`expiry`・`state`・`save`・`parseFlow`・`router`(新ルート)・`app-state`・`client`・`session`はロジック維持なら修正小
- E2E(実機結合・契約116)は画面構成変更に追随する形で見直し(フロント側の操作フローが変わるため)

## 9. 周辺への影響(client-side外だが決定が必要)

1. **docs基準の切り替え**: 00運用ル則6・03第10節は「実装基準=`prototype/`(v1)」が前提。v2を新基準にするプロダクトオーナー決定と、03(画面構成)・02・05(UI固定値=期限4択・visibility2値・通知3値・300字上限)・01の改版
2. **Workers Assets配信**: Google Fonts CDN利用の可否(オリジン・プライバシー・オフライン)。不可なら同名フォントのself-host(@fontsourceにZen Old Mincho等があるか要確認)
3. **契約116ケース**: API変更(undo・理由・取り下げ既存・取消等)を足す場合のbackend改修と契約試験の拡張

---

## 実装の進め方(案)

1. §6の裁断を確定(仕様・APIの追加有無) — 未決のまま進めると手戻り最大の箇所
2. デザイントークン+シェル(§1〜3)を先に敷き、全画面の土台を切替
3. 画面フローの骨格(01→02→03と04/05詳細)をAPI既存範囲で実装
4. §5の残機能(同意・設定・お知らせほか)をv2デザインで当て込み
5. テスト・E2E・docs基準の切り替え
