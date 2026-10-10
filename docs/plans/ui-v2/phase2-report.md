# UI v2移行 Phase 2 実施報告 — 01ホーム+02読み解き確認

- 日付: 2026-10-10
- 出典: [ui-v2-roadmap.md](../ui-v2-roadmap.md) Phase 2 / [ui-v2-gap-analysis.md](../ui-v2-gap-analysis.md) §4-01・§4-02 / [phase0-decisions.md](phase0-decisions.md) D-01〜D-04・D-13
- デザインの正: `prototype/v2/01(Main.dc.html)`・`02(Confirm.dc.html)`のinline style
- 状態: 完了。全vitest緑(280件)・`vite build`緑・実API(compose local)で
  01→02→保存→03(プレースホルダ)往復を確認

## 実施内容

### 1. 01 預ける(ホーム)— `index.html #homeScreen`・`src/intent/screen.js`

- ヒーロー: 「N件の意思が待機中 — いまは何もしなくて大丈夫」行(stbビーコン・
  `@keyframes beacon`)→「どんな条件なら、動きますか。」(Zen Old Mincho・
  clamp(38px,5.6vw,76px))+リード文。NはGET /v1/intents?status=activeの件数
- **composerカード**: radius 28px・line-strong枠・`box-shadow:0 40px 120px -50px
  var(--acc-glow)`・`:focus-within`で`acc-line`+3px acc-soft+glow。ラベル
  「条件をひとことで」+textarea(rows=3・min-height 128px・Zen Old Mincho
  clamp(22px,2.4vw,30px)・placeholder=プロトタイプの例文)。maxlength=300は維持・
  文字数カウンタは廃止(プロトタイプにない)
- **読み取りchips**: parse結果(debounce 1秒)を行値のある分だけchips(label=acc
  12.5px 700+値15px)で`aria-live="polite"`の`#readStatus`内へ即時表示
  (`@keyframes chipIn`)。0件+空テキストはガイド文、入力済み0件はstb情報ボックス
  (「まだ条件を読み取れません。…」)。parseエラーは`#parseError`(role=alert)+再試行ボタン
- **例文chips**: 飲み/転職/売買/集まりの4値(プロトタイプ01固定)。タップで
  textareaへ+inputイベント発火(parse再送)
- **CTA「読み解く」**: 読み取り1件以上で有効。押下で`parseFlow.commit()`
  (debounce打ち切り即時確定)を待ってから`#/confirm`へ遷移 — 入力直後の
  クリックで古いparse結果を使わない。busy時スピナー+「読み解いています…」
  +ヘルプ4状態はプロトタイプの分岐どおり。⌘Enterは維持(表示ヒントなし)
- **下書き保存(D-13)**: composer下段左にghost小ボタン。canDraft(テキスト非空)
  で有効・保存で右上toast「下書きを保存しました」。pause/resumeはUIから外した
  ままでAPI温存(決定どおり)
- **待機中の意思**: GET /v1/intents?status=activeからカード一覧
  (`intentCardHtml`)。カード=カテゴリSVG+「飲み · 12分前に預けた」+状態pill
  (候補N件=acc強調/待機中=stb+点)+原文引用(Zen Old Mincho 19px・escapeHtml)+
  条件タグ(s2チップ: 「20:00〜」「天文館」「2〜4人」「〜¥5,000」)+期限行
  (「今日 23:59 まで有効」「11/30 まで有効」「期限なし」)。
  grid `repeat(auto-fit,minmax(min(300px,100%),1fr))`。
  0件はヒーロー行とセクションごと非表示(プロトタイプに空状態の規定なし)

### 2. 02 読み解き確認(新規)— `index.html #confirmScreen`・`src/intent/confirm.js`・`src/intent/conditions.js`

- **3ステップインジケータ**(書く✓→02=acc発光→03)・見出し「こう読み解きました。」
- **あなたの言葉(D-03: 値マッチング)**: `src/intent/highlight.js`(新規純関数)。
  原文から各条件行に対応する部分文字列を探し`<mark>`(acc-soft地+inset下線acc)
  +行番号`<sup>`(mono・acc・vh読み上げ補助)を付す。時刻は「今日/今夜/明日/明後日」
  隣接表記を優先し「以降/から/まで/頃」まで含む、予算は「予算」「まで」等の
  前後語と万換算(1,000万円)まで含む、目的は正規化差(「軽く飲む」↔「軽く飲みたい」)
  を先頭共通接頭辞で吸収。マッチしない行はハイライトなし(決定どおり画面は破綻しない)
- **読み取った条件5行**(番号01〜05 mono/acc+40pxアイコン枠+ラベル+値21px 700+
  サブ)。`@keyframes rowIn`。**行編集はテキスト1行input+保存/キャンセル**
  (プロトタイプ02どおり)で、確定時にformat.jsのパーサで構造値へ書き戻す
  (時間:「明日 20:30」「10/12（月）20:00〜」→JST ISO。人数:「2〜4人」。
  予算:「1人 5,000円まで」「1,000万円」。目的: 種別の語or自由文)。空入力はnull
  (催促へ戻す)。読み取れない入力は現在値を保持
- **人数確認seg(D-04)**: 人数行内fieldset・「含む/含まない」radio(vh)。
  未回答=「要確認」(acc・acc-soft枠)でCTA無効・回答=「確認済み」(stb)。
  「含む」=値「2〜4人（あなたを含む）」・sub「相手は1〜3人」、「含まない」=
  「あなた＋2〜4人」・「合計3〜5人」(汎化式)。人数行を編集して範囲が変わったら
  segは要確認へ戻す。再読み取り(parse)でも要確認へ戻す
- **参考条件箱**: 判定不能NG(02 D-04)+parse由来soft(D-19補足)を5行の下の
  stb情報箱に列出(いずれも送信内容のため表示から取り零さない。プロトタイプに
  相当行なし=本実装の補完)
- **足せる条件(任意)**: 固定3トグル(禁煙の店/2時間くらいで解散/全員はじめまして・
  `aria-pressed`)→保存時`soft_constraints`へ追記
- **公開範囲と通知(D-02)**: 説明3項目(目隠しeyeOff・ベル・時計の40px枠)+
  **期限select 3択(D-01)** =GET /v1/intents/expiry-options(今日22:00/23:59/明日5:00)
  を`applyExpiryOptions`で構成(label表示・value=絶対時刻・過ぎた選択肢disabled・
  default_index)。3項目目の「{ラベル} に自動で取り下げ」は選択に追従。
  公開設定・通知設定のselectは廃止(常時hidden_until_match+proposals_only)
- **CTA「この条件で預ける」**: 人数確認必須+必須3(時間/場所/目的)で有効。
  busyスピナー「預けています…」。ヘルプは未回答/必須欠落/OKの3状態+busy。
  概要行「5項目 · 確認が必要なもの N件」(N=seg未回答+必須欠落)。
  保存エラーはGEOCODING_FAILED等で該当行をdng強調+`#confirmError`(role=alert)
- 書き直し(リンク2箇所)→`#/`でcomposerへフォーカス。01を経由しない直接訪問は
  `#/`へ戻す

### 3. 保存フロー(02で確定保存)・状態共有

- 01と02で`createFormState()`の状態を1つ共有(main.jsが生成・両screenへ注入)。
  01=parseとcomposer、02=行編集・seg・期限・保存を担う
- 保存は02のCTAから`createSaveFlow.saveActive()`(POST /v1/intents・status=active)。
  成功で`#/standby`(03・Phase 3実装)へ遷移。旧確認モーダル
  (confirmationModal/modalClose/returnButton)とchrome.jsのopenModal/closeModalは廃止
- `buildCreateRequest`拡張: **participants_includes_owner**(D-04)を送信
  (未回答は既定true)。「含まない」は`effectiveParticipants`で+1換算
  (2〜4人→3〜5人)。`softExtras`(トグルon分)をsoft_constraintsへ連結
- 下書き(D-13)は01のcomposer直下から(上記)。draft→active化導線はPhase 3

### 4. format.jsの表示書式をv2様式へ

- 時間: 「今日 20:00以降」→**「今日 10/10（土）20:00〜」**(当日/翌日の語+
  M/D（曜）・endは「20:00〜23:00」・遠い日は日付のみ)
- 予算: 「ひとり5,000円まで」→**「1人 5,000円まで」**
- 追加: `conditionTags`(カードタグ: 「20:00〜」「天文館」「〜¥5,000」)・
  `expiryLine`(「今日 23:59 まで有効」「11/30 まで有効」「期限なし」)・
  `depositedAgoText`(「12分前に預けた」「38日前に預けた」「たった今預けた」)・
  `formatCategoryPrimary`(カード見出しの種別語)・行編集パーサ4種
  (parseTimeText/parseParticipantsText/parseBudgetText/parseCategoryText)
- 削除: PRIVACY_VALUES/NOTIFICATION_VALUES(D-02でselect廃止)・
  isoToDateTimeLocal/dateTimeLocalToIso(行編集のテキスト化に伴い)

### 5. シェル(hosting)の再構成

- `#main`は横paddingのみの容器とし、各画面sectionが`page-main`を持つ方式へ。
  01=既定値(1120px・gap clamp(56px,8vw,88px))、02=`page-main--confirm`
  (1200px・padding-top clamp(32px,6vw,80px)・gap 44 — プロトタイプ02の値)
- 翡翠haze: 01=上部中央(1200×760・top -260px)、02=右上(900×900・top 120px)
  (各プロトタイプのinline値を`.haze-home/.haze-confirm`へ)
- `expiry.js`の`selectedExpiry`を`options[selectedIndex]`経由へ(一部DOM実装で
  selectedOptionsがselectedIndex代入後も更新が遅れるため)
- `parseFlow`へ`commit()`追加(debounce打ち切り・同一テキストは送信せず即完了)
- 旧workspace(2カラム+預け方サイドバー)・候補/成立セクション(旧latch-card)は
  ホームから削除。**GET /v1/latchesは候補・成立バッジの供給源として維持**
  (一覧表示はPhase 3/4のD-11簡易一覧へ)

### 6. 試験(244件→280件・全緑)

- 更新: **format**(v2書式・パーサ往復・+1換算・タグ/期限行/相対時間)・
  **conditions**(readSlots・rowModels・renderRows・seg3状態・テキスト行編集9種・
  extrasトグル)・**state**(participantsIncludesOwner)・**parseFlow**(commit 2件)・
  **expiry**(3択データへ)・**save**(既存10件は不変で緑)・**latch-home**
  (intentCardHtml・loadIntents・0件時非表示・バッジ供給)・**smoke/latch-shell**
  (新DOM・廃止IDの不在・01/02クラス)
- 新規: **highlight**(6件・プロトタイプ02原文で5条件マーク・数字/今夜/万表記/
  不一致時ハイライトなし・連結復元)・**confirm**(8件・enter描画・直接訪問回避・
  seg含む/含まないの送信ボディ・extras→soft追記・期限3択とラベル反映・
  GEOCODING_FAILEDの行強調・XSS)

## 実API検証(compose local・2026-10-10)

環境: `make up`のcompose常設api(127.0.0.1:8000・llm_mode=stub)+`npm run dev`
+vite proxy。内部CLIでIdPトークン発行→画面のトークンパネルで接続
(POST /v1/usersでの初回登録とLLM同意(#/onboarding)はAPI/画面の既存経路)。

1. 01: 例文chipsで入力→parse(chips表示)→「読み解く」→02へ遷移
2. 02: 行編集で時間=「10/12（月）20:00〜」・場所=「天文館」へ修正
   (スタブparseの既定値9/27東京駅から)・seg「含む」・足せる条件「禁煙の店」on・
   期限「明日 5:00」→「この条件で預ける」→ **#/standby(03プレースホルダ)へ遷移**
3. API側(get /v1/intents?status=active): active・expires_at=10/11 05:00 JST
   (選択どおり)・time.start=10/12 20:00 JST(行編集どおり・endは+3h補完)・
   location=天文館(radius 1000既定)・participants=(2,2)はサーバ補完+
   **participants_includes_owner=true**・soft_constraints=["禁煙の店"]・
   visibility=hidden_until_match・notification_level=proposals_only
4. 01へ戻る: 待機中バッジ1件・ヒーロー行「1件の意思が待機中」・カード
   (「食事 · たった今預けた」+原文引用+タグ+「10/11 まで有効」)表示
5. 下書き: 01の「下書きに保存」→toast+GET /v1/intents?status=draftで1件
6. dockerログにraw_textの漏出なし(01 §21)

検証中に発見・修正した不具合: 02で行編集後に01へ戻るとchips・CTAが編集前の
描画のまま残る(01の再描画とbusy解除描画が抜けていた)→`intentScreen.refresh()`
をルートhomeで呼ぶ+`goConfirm`のbusy解除後にrefreshActionsする修正を入れ、
往復でCTA「読み解く」(idle)・chipsが最新stateになることを確認した。

## 独自に構成した値・判断(プロトタイプに直接の原型がないもの)

- **DOM・クラス名**: プロトタイプはinline styleのため`composer/read-section/
  chip/example-chip/intent-card/intent-pill/steps/words-card/privacy-card/
  slots-card/slot-row/people-box/seg/extra-chip/deposit-button`等のクラス名と
  構造化はこちらの構成(値・配色・字号・余白・角丸・影はプロトタイプの該当指定を写す)
- **待機中カードのカテゴリアイコン**: schemaのカテゴリはmeal/drinking/activityの
  3値のため、プロトタイプの転職(briefcase)・売買(car)は対象外。
  drinking/meal=グラス(プロトタイプの飲み)、activity=people(集まり系の意図に
  合わせた割り当て)
- **期限行の先日付**: プロトタイプ01は「今日 23:59 まで有効」「11/30 まで有効」の
  2例のみ。当日=「今日 H:mm まで有効」・それ以外(明日含む)=「M/D まで有効」
  (時刻を出さない)と解釈した
- **depositedAgoText**: 「12分前に預けた」「38日前に預けた」はプロトタイプ例。
  1分未満「たった今預けた」・時間単位「N時間前に預けた」は補完
- **目的行のサブ**: drinking=プロトタイプ02の「カジュアルな一杯 · 長居は前提に
  しない」。meal「食事をともにする時間」・activity「一緒に楽しむ時間」は補完
- **時間行サブ**: endなし=プロトタイプの「終わりの指定なし · 有効期限まで受け付け」。
  endあり=「終わりは H:mm」は補完。場所サブ「歩いて集まれる範囲」は固定
  (radius_mの表示はなし)
- **参考条件箱(soft+NG)**: プロトタイプ02に相当行がないが、いずれもAPIへ送信
  されるため表示に加えた(NG注意文言は既存の固定文字列)
- **行編集のパーサ**: テキストinput(プロトタイプ)とAPIの構造化送信の橋渡しとして
  format.js出力の往復+代表的表記(「明日 20:00」「19時半」「1,000万円」)を受理。
  読み取れない入力は現在値を保持(再描画で戻る=入力が効かなかったことが見た目で分かる)
- **summary行「5項目 · 確認が必要なもの N件」**: プロトタイプはseg未回答時の
  「1件」固定。N=seg未回答+必須欠落数へ汎化
- **人数欠落時のseg**: participantsが未読み取りでもsegは出す(「「人数」に、
  あなたは含まれますか？」)。回答はparticipants_includes_ownerとして送信し
  人数自体はサーバ補完(2,2)に委ねる
- **下書きボタン(D-13)**: 配置はcomposer下段の左列(鍵行の下)・ghost小ボタン。
  サイズ36pxは補完(プロトタイプに原型なし・決定が裁量を委ねた箇所)
- **01の0件表示**: 待機中0件はヒーロー行と「待機中の意思」セクションを非表示
  (空状態文言は作らなかった)
- **02の520px対応**: プロトタイプ02の`@media (max-width:520px)`(card-pad 20px・
  行1fr+44px・slot-ic非表示・seg label 16px)をクラス構成に置き換えて適用

## 持ち帰り(Phase 3への申し送り)

1. **01カードの「候補N件」acc強調は未接続**: `intentCardHtml`は
   `proposalCount>0`でacc強調カード(候補を見る)を組むが、GET /v1/latches一覧
   応答にIntentとの対応づけ(intent_ids等)がなく、どのIntentに候補が紐づくか
   現行契約からは分からない。Phase 2では全カード待機中(stb)表示。Phase 3の
   「01カード→03/04遷移接続」で対応が要る(API側に intent_ids 開示か通知側からの
   導線のいずれか。契約変更になる場合は05の改訂として持ち帰る)
2. **候補・成立の一覧表示がホームから消えた**: バッジ件数は維持するが一覧DOMは
   D-11の簡易一覧画面(Phase 3/4)を待つ。topnav「候補」「成立」のリンク先も
   従来どおり`#/`のまま
3. **03はプレースホルダ**: 保存後の着地状態「預けました。」・残時間タイマー・
   取り下げ・条件を直す(PATCH)はPhase 3。02→03の遷移のみ実装済み
4. **`#/standby`直訪問**: プレースホルダ表示のみ(01を経由しない02は`#/`へ戻す
   対応済み)
