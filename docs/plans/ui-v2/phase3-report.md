# UI v2移行 Phase 3 実施報告 — 03待機中+04候補が届く

- 日付: 2026-10-10
- 出典: [ui-v2-roadmap.md](../ui-v2-roadmap.md) Phase 3 / [ui-v2-gap-analysis.md](../ui-v2-gap-analysis.md) §4-03・§4-04 / [phase0-decisions.md](phase0-decisions.md) D-05〜D-07・D-11
- デザインの正: `prototype/v2/03(Standby.dc.html)`・`04(Proposal.dc.html)`のinline style
- 状態: 完了。全vitest緑(318件)・`vite build`緑・実API(compose local+実ブラウザ)で
  待機一覧・取り下げ・条件修正・回答4状態(open/joined/allIn/declined)・undo・
  理由chips・チャット送信まで往復を確認

## 実施内容

### 1. 03 待機中(新規)— `index.html #standbyScreen`・`src/latch/standby.js`

- **2姿**: 着地状態(02からの保存直後・`#/standby?deposited=1`)は見出し
  「預けました。/あとは、待つだけ。」+リード「LATCHがこの条件を預かっています。…」。
  通常状態は「条件が揃うのを、/待っています。」。badge「待機中 · N件」(stb・beacon)
- **メイン条件カード**: pill待機中(stb+点)・カテゴリSVG+「飲み · 12分前に預けた」・
  原文引用(Zen Old Mincho clamp(19〜21px)・escapeHtml)・条件タグ(s2チップ)・
  **残時間タイマー**(`role="timer"`・HH:MM:SS・IBM Plex Mono 30px・1秒更新・
  tabular-nums。期限なしは「期限なし」表示でタイマーを出さない)・
  「条件を直す」(pencil・`#/confirm?intent={id}`)・「取り下げる」(ghost danger・
  `aria-haspopup="dialog"`)
- **取り下げ済み状態への切替**: メインのDELETE成功後、同じ位置にpill=取り下げ済み
  (s2/t3)+本文t3+「この条件は取り下げました。もう誰とも照らし合わされません。」+
  「もう一度預ける」(→`#/`)を出す(プロトタイプのmainGone)。削除済みIntentは
  一覧に戻らないため表示をクライアント側で保持(画面再訪時はサーバの真実に揃う)
- **取り下げdialog**: `<dialog class="dlg">`は既存の`#appDialog`(chrome.openAppDialog)
  を利用。題「この条件を取り下げますか？」+条件引用(.dlg-quote)+説明
  (.dlg-desc)+「やめておく」(ghost)/「取り下げる」(danger solid)
- **取り下げ=DELETE /v1/intents/{id}**(05 §5・現行フロントで未接続だった経路)。
  成功で右上toast「取り下げました」+ほかの意思の行は消える
- **オーブ**: 円環3層+ripple×3(4.2s)+2アークbreatheL/R+stb drop-shadow+
  条件タグ4種の浮遊チップ(time/予算はIBM Plex Mono・位置はプロトタイプの絶対指定)
- **「いま、LATCHがしていること」3カード**: 01照らし合わせ続ける/02成立し得るか
  だけを見る/03揃ったら、ひとことだけ+通知プレビュー(LATCHロゴ+「LATCH候補が
  あります」+今)
- **ほかに待機中の意思**: 1行=44pxカテゴリアイコン+原文(serif 18px)+meta
  (種別 · 相対時間 · 期限行)+待機中pill+「直す」(`#/confirm?intent={id}`・
  aria-label=原文つき)+「取り下げる」。空状態「ほかに待機中の意思はありません。」。
  「新しく預ける」リンク(→`#/`)
- **右上トースト(候補)**: `#/standby`入部時にGET /v1/latchesを取り、候補があれば
  プロトタイプ03の候補トースト(ロゴボックス+「LATCH · いま」+「LATCH候補が
  あります」+見る→`#/latches/{id}`+閉じる)を出す。同一latchでセッション内1回。
  自動消滅しない(従来の文言toastは2400ms自動消滅のまま)
- データ源はGET /v1/intents?status=active(created_at降順・先頭=メイン・残り=ほか)。
  入退部で1秒タイマーを開始/停止(main.jsのonRoute)

### 2. 条件を直す=PATCH /v1/intents/{id} — `src/intent/confirm.js`・`save.js`・`state.js`・`router.js`

- ルート`#/confirm?intent={uuid}`を新設(parseHashがqueryを返す形に拡張。
  `#/standby?deposited=1`も同様)。router試験に2ケース追加
- 02編集モード: GET /v1/intents/{id}で`applyIntentSnapshot`(state.js新規)により
  raw_text・structured(category/time/location/budget/participants)・
  participants_includes_owner(D-04のsegは「確認済み」で復元)・soft/ngを復元。
  02の固定トグル分(EXTRAS)はsoftRowsからextrasPickedへ回して保存時の重複を防ぐ。
  保存済みexpires_atが3択にあればselectへ事前選択
- 保存は`createSaveFlow`に`getIntentId`を追加 — ありはPATCH /v1/intents/{id}
  (raw_text+structured_intentの全置換・status省略=現状維持・`buildPatchRequest`)。
  なしは従来どおりPOST。検証エラーの表示位置マップ(errorPlacement)は共通
- 保存成功後の遷移: 新規=`#/standby?deposited=1`(着地)・編集=`#/standby`(通常)。
  復元失敗(消済み・他者)は`#/standby`へ戻す(文言は出さない)

### 3. 04 候補が届く(提案姿の全面再構成)— `src/latch/detail.js`・`view.js`・`respond.js`

- **レイアウト**: `page-main--proposal`(1200px)+`pv-grid`(モバイル1カラム=見出し→
  判断→照合→図/≥980px=図｜見出し／照合｜判断)+翡翠haze(左上)+riseアニメ
- **ヒーロー**: badge「LATCH候補 · {HH:MM} に届きました」(acc)+「条件が、
  揃いそうです。」(serif clamp(36〜68px))+リード文
- **オーブ(acc色版)**: ripple 3.2s(scale .6→1.45)+アークnearL/R(2.6s)+
  acc drop-shadow+チップ(time/エリア/人数/予算・t1色)。03とはkeyframesを別命名
  (ripplePv/nearL/nearR — 03の4.2s系と共存させるため)
- **4情報カード**: 開始(mono+「今夜 · 10/11（日）」等のsub)/エリア(+「お店は成立後に
  相談」)/人数(+「あなた＋N人」)/予算(¥+3桁区切りのmono+「1人あたり · 前後」)。
  time_summaryは「YYYY-MM-DD HH:MM」(JST)をパース
- **「あなたの条件との照合」(新規UI)**: 5行=ラベル/あなたの条件→矢印→候補(700)+
  ✓(28px円)。見出し横に「5項目すべて条件内」+下部注記(プロトタイプどおり)。
  **自分の条件は値マッチングで特定**(D-03と同じ発想・`pickMyIntent`): 詳細応答に
  intent_idsがないため、active Intentをカテゴリ一致+場所の包含+予算の収まり+
  人数の収まりで採点(弱いシグナルのみはnull・人数既定2のみの一致は採用しない)。
  特定できない行は「—」+見出しを「候補は、あなたの条件の範囲で選ばれています」へ
- **回答期限タイマー**: `role="timer"`分:秒・1秒更新・**残5分以下で`.timer-low`
  (dng色)**・0で回答ボタンdisabled+「{HH:MM} を過ぎると、この候補は静かに消えます」
- **目隠しアバター**: 自分=イニシャル1字(s3・2px枠・joined/allInでaccリング)+
  相人分=目隠しアイコン(点線円・eyeOff)のスタック+whoLine(open=「あなた ＋ まだ
  見えないN人」/joined=「あなた（参加）＋ 回答待ちのN人」/allIn=「N人全員が参加」)
- **回答2択(D-05)**: 「参加する」(64px cta+latchMark)/「今回は見送る」(52px ghost)。
  「辞退する」(no)のUI導線は廃止(API値は温存)
- **4状態(open/joined/allIn/declined)**: `proposalStateOf`(view.js純関数・
  my_response×remaining_responsesで判定)
  - joined=「参加と回答しました。」(acc-softカード)+「ほかのN人の回答を待っています
    （M人が回答済み）」(M=total-remaining-1・0は括弧なし)+注記+「待機中に戻る」+
    **「回答を取り消す」**(D-06)
  - allIn=「N人全員が参加しました。」(2人時は人数語なし)+「LATCHが成立しました。
    ここから、お互いの姿が見えます。」+**「チャットを開く」**→同一詳細のチャット
    (05)へ切替。yes回答で成立した直後だけこの状態を一度出し、直接訪問・再読み込み
    は05(チャット)へ(フラグ`showAllInOnce`)
  - declined=「見送りました。」+「条件はそのまま、待機を続けます。…」+
    **理由chips**(D-07・time/place/size/mood・任意複数・`aria-pressed`)+
    「待機中に戻る」+「取り消す」
- **見送りの送信タイミング(D-07制約)**: 理由はdefer回答のbodyでのみ受理される
  (05 §5・P1B実装)ため、**「今回は見送る」では即座に送らず、declined状態の離脱時
  (「待機中に戻る」または別ルートへの遷移)に選択した理由といっしょに送る**
  (`reasons`をbodyへ同梱)。送信前の「取り消す」は画面操作のみ(API呼び出しなし)。
  ルート離脱時は`detail.flush()`(main.jsのonRoute先頭)が未送信の見送りを送る。
  送信済み(my_response=defer・status=rejected)で詳細を開いた場合はclosed描画で
  同じdeclinedカードを出し、「取り消す」が真のundo(POST response/undo)になる
- **undo(D-06)**: POST /v1/latches/{id}/response(空body)。成功は再取得でopen姿へ。
  409(成立済み・期限経過等)はtoast「この回答は取り消せませんでした」(docs/03 §5)
- **respond.js**: 2択+undoに拡張し、戻り値を`{ok, kind}`へ(pending二重送信抑止・
  409系は再取得で収束・429は既存文言)。「表示の唯一の真実は詳細応答」の原則は維持
- **最小形(hidden_until_match)への追随**: D-02によりUIから預けたIntentは提案が
  最小形(`{headcount, match_level}`のみ)になるため、最小形では4カード・照合表・
  オーブを出さず、stb案内ボックス(「条件が合う候補があります — くわしい内容は、
  全員が「参加する」と答えたときにわかります」)+判断カード(タイマー・アバター・
  2択・4状態)で構成。回答フローは全形と同一
- 通報導線は維持(提案1対1=「この提案を通報する」・グループ提案=なし・成立=あり)。
  統合dialogへの再構成はPhase 4(D-09)

### 4. 01の「待機中の意思」カード→03/04遷移 — `src/latch/home.js`

- `pairCandidateLatch`: active Intentが1件×候補があるときだけ対応を確定し、
  カードをacc強調(「候補 N件」+「候補を見る」)にして`#/latches/{id}`へ直結。
  複数件時は対応づけ不能のため全カード待機中(stb)で`#/standby`へ
  (契約持ち帰り・下記)。latches一覧はバッジ供給に加えてカード描画にも使う

### 5. シェル・その他 — `src/ui/chrome.js`・`texts.js`・`styles.css`

- chrome.showToastを2形に(文言toast=従来どおり自動消滅/`{message, href, kicker}`=
  候補トースト・自動消滅なし・`.toast-link/.toast-logo/.toast-body/.toast-see`)
- texts.jsへPhase 3分の文言を集約(4状態見出し・理由chips・undo・03の文言一式)。
  RESPONSE_BUTTONSを2択へ(D-05)
- styles.cssへ03・04の章を追加(値はプロトタイプ03・04のinline styleから写す)。
  `.beacon`を01ヒーロー行と03バッジで共通化。520pxの照合表折返しも移植

## 実API検証(compose local・2026-10-10)

環境: `make up`のcompose常設api/db/worker(127.0.0.1:8000・llm_mode=stub)+
`npm run dev`+vite proxy。内部CLIでIdPトークン発行→画面トークンパネルで接続。
検証ユーザーはphase3-a〜g(7人・POST /v1/users+consentは既存経路)。

候補形成の前提: 提案は対象開始75分前まで(PROMPT_LEAD_MIN)・スタブJevの既定値では
latch_scoreが0.60閾値未満のため、検証では時刻を翌日へ、workerのみ
`LATCH_LLM_STUB_JEV_NOUL=0.85`の1-offコンテナで起動(検証後、通常workerへ戻した。
compose.yaml・backendは無変更)。

1. **04提案姿(全形)**: F/G(明日19:00・天文館・食事)で提案形成→01カードが
   「候補 1件」acc強調で04直結→04でヒーロー・オーブチップ・4情報カード
   (19:00/鹿児島市天文館/2人/¥4,000)・照合表(あなたの条件→候補・5項目すべて条件内)・
   タイマー118:29・目隠しアバター・2択を確認(実データ・スクリーンショット済み)
2. **declined(未送信→送信)**: 「今回は見送る」→即座には送られずdeclinedカード
   (理由chips)→「時間が合わない」「今日は気分じゃない」を選択→「待機中に戻る」で
   POST defer → **DBのresponses.reason_codes=["time","mood"]を確認**・候補バッジ消滅・
   `#/standby`へ遷移
3. **declined(送信済み)+undo**: 詳細再訪でclosed描画のdeclinedカード→
   「取り消す」→POST undo→open姿へ復帰(2択が戻る)
4. **joined+allIn**: undo後、Gが先にyes(API)→Fが「参加する」→**allInカード**
   (「全員が参加しました。」+「チャットを開く」・whoLine「2人全員が参加」)→
   「チャットを開く」で05(チャット)へ→UIからメッセージ送信→一覧に表示
   (joinedの現況文はAPI経由でも確認: D/EペアでD yes→partial_accept・remaining 1)
5. **1対1の4状態をAPI直で全通し**: D/Eペアで open→defer+reasons(rejected)→
   undo(proposed)→yes(partial_accept=joined相当)→E yes(matched)→チャット送受信2件
6. **03待機中**: 01から例文→02(人数確認・行編集・GEOCODING_FAILEDの行強調・
   時刻422の解消)→「この条件で預ける」→**着地状態**(「預けました。/あとは、
   待つだけ。」・タイマー01:49:19を1秒更新で確認)
7. **条件を直す(PATCH)**: 「条件を直す」→02編集モード(保存済み値の復元・seg
   「確認済み」・end+3h補完の表示)→予算行を「1人 5,000円まで」に編集→保存→
   **PATCH成功**(GET /v1/intentsでversion 2・budget.max=5000を確認)→
   `#/standby`通常状態・カードとオーブのチップに〜¥5,000が反映
8. **取り下げ(DELETE)**: 「取り下げる」→dialog(条件引用+説明)→「取り下げる」→
   **取り下げ済みカード**(pill・案内文・もう一度預ける)+ほかの意思は空状態。
   GET /v1/intentsから当該Intentが消滅
9. **チャット**: 成立LATCHでUI送信1件+API送信1件が両者の一覧に表示
10. dockerログ(api/worker)にraw_textの漏出なし(01 §21)

検証中に発見・修正した不具合: 見送り送信後に詳細を再描画したとき、選択済みの
理由chipsが押下状態のまま表示される(declineReasonsを描画開始時にクリアしてい
なかった)→`render()`でクリアする修正(コミット3f2886d)。

## 独自に構成した値・判断(プロトタイプに直接の原型がないもの)

- **DOM・クラス名**: プロトタイプはinline styleのため`sb-*/doing-*/orb-*/pv-*/
  fit-*/reason-chip`等のクラス名と構造化はこちらの構成(値・配色・字号・余白・
  角丸・影はプロトタイプの該当指定を写す)。オーブのアニメは03(ripple 4.2s・
  breatheL/R)と04(ripplePv 3.2s・nearL/R)でkeyframesを別命名して共存
- **理由chipsの送信タイミング**: プロトタイプはdeclined状態でchipsを押すのみ。
  APIがreasonsをdefer回答時にしか受け付けない(05 §5・P1B)ため、送信を離脱時へ
  繰り延べる構成とした(上記3)。プロトタイプの見た目・文言は変えていない
- **allInの到達**: プロトタイプ04のallInカードは「チャットを開く」→05。SPAでは
  成立済み詳細がそのまま05(チャット)のため、yes回答で成立した直後だけallInを
  一度出し(フラグ)、直接訪問・再読み込みは05へ
- **照合表の自分の条件**: 詳細応答にintent_idsがないため値マッチングで特定
  (D-03と同方式)。不一致時は「—」+見出し文言の代替(FIT_UNAVAILABLE_TEXT)。
  予算の候補側は「{max}円前後」・自分側は「{max}円まで」(プロトタイプの対比様式)
- **人数whoLineの汎化**: プロトタイプは3人例のみ。total=2は「2人全員が参加」を
  「全員が参加しました。」(人数語なし)で出す
- **取り下げ済みカードの保持**: DELETE後の表示はクライアント保持(再訪ではサーバ
  の真実=存在しない)。プロトタイプのmainGone相当
- ** joined現況の括弧**: 「（M人が回答済み）」はM>0のときのみ(M=total-remaining-1)。
  1対1のjoined(M=0)は「ほかの1人の回答を待っています。」のみ
- **メイン意図の選択**: 一覧の先頭(created_at降順=最新)をメインカードに、残りを
  「ほかに待機中の意思」へ。プロトタイプは固定2件の例示のみで選択規則が沈黙
- **候補トーストの表示契機**: 03入部時のlatches取得時(新着検知のポーリングは
  しない。常時監視は既存のお知らせ(dot表示)とPhase 5のD-14統合で判断)

## 持ち帰り(Phase 4以降への申し送り)

1. **候補↔Intentの対応づけ契約(継続)**: latches応答にintent_idsがないため、
   01カードの「候補N件」acc強調と04直結は「active 1件×候補あり」のときしか
   確定できない(複数件時は全カードstb・03経由)。04の照合表の自分の条件も
   値マッチングの推定であり、正契約としてはintent_ids(または自分のintent)の
   開示を05改訂として持ち帰りたい(Phase 2からの継続課題)
2. **見送り後のreasons更新経路なし**: undo→再defer以外でreasonsを後から変える
   APIがない。送信済みdeclinedカードのchipsは表示のみ(選択は保持されない)。
   回答時以外の理由収集を狙うなら05改訂(REASON更新API)か回答前選択UIの再構成
3. **グループ提案の形成がstub既定では到達しない**: latch_scoreがスタブ既定
   (MutualScore 0.556換算)で0.60閾値未満。検証時はworker env
   `LATCH_LLM_STUB_JEV_NOUL`の引き上げが必要(実ブラウザのjoined/allIn確認は
   1対1+API直で実施)。02#13等の機能試験と同様、結合試験でのenv調整を憶えておく
4. **D-11簡易一覧(候補・成立タブ)**: 本フェーズでは未実装(topnav「候補」「成立」
   は`#/`のまま)。成立一覧はPhase 4・候補一覧の再確認をPhase 4/5で
5. **matched姿(05)・通報/ブロックの統合dialog・attendanceは現行のまま**:
   Phase 4の対象。04から「チャットを開く」で到達する05は旧UI(暫定)
