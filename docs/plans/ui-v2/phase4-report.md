# UI v2移行 Phase 4 実施報告 — 05成立・チャット+通報/ブロック統合+参加取り消し

- 日付: 2026-10-10
- 出典: [ui-v2-roadmap.md](../ui-v2-roadmap.md) Phase 4 / [ui-v2-gap-analysis.md](../ui-v2-gap-analysis.md) §4-05 / [phase0-decisions.md](phase0-decisions.md) D-08〜D-10・D-14
- デザインの正: `prototype/v2/05(Latched.dc.html)`のinline style
- 状態: 完了。全vitest緑(337件)・`vite build`緑・実API(compose local+実ブラウザ)で
  成立〜チャット送受信・通報/ブロック・参加取り消し(D-08)まで往復を確認

## 実施内容

### 1. 05 成立姿(詳細のmatched姿の全面再構成)— `src/latch/detail.js`・`view.js`

- **レイアウト**: `page-main--latched`(1240px)+`lt-grid`。並び順はスマホ=チャット→
  詳細・≥960px=詳細(300〜360px)|チャット(プロトタイプ05の.lt-gridどおり)。
  チャット高さはPC=`min(720px, 100vh-140px)`・モバイル=`calc(100dvh - 180px)`
- **成立ヒーロー**: snapアニメ(2弧が閉じるsnapL/R・.9s)+burst(1.5s・遅延.9s)+
  kicker「成立 · {HH:MM}」+「LATCHしました。」(serif clamp(28〜46px))+リード
  「{N}人全員が「参加する」と答えました。…」+条件タグ4種(「20:30〜」mono/エリア/
  「N人」/「¥4,000前後」mono・値のない行は組まない)。モバイル(≤720px)でタグ非表示
- **aside「成立した内容」**: `<details>`(≥960pxで初期open・matchMediaを描画時に1回)+
  dl 4行(時間「今夜 20:30〜」=日付語は先頭語のみ・場所+「集合場所はチャットで」sub・
  人数・予算「1人 5,000円前後」)+**「カレンダーに追加」**(D-10・下記3)
- **aside「メンバー」**: 色付きイニシャルアバター(他者=参加順でパレット色40px・
  自分=末尾にs3+2px枠の「あなた」行)+「参加」✓+注記「成立したので、お互いの表示名が
  見えるようになりました。」。取り消し後は自分行が「取り消し済み」(dng)へ切替わる
- **aside「安全のために」**: shieldアイコン見出し+安全ガイド`<details>`(3箇条・
  プロトタイプの文言どおり)+「通報・ブロック」ボタン(ghost danger・
  `aria-haspopup="dialog"`)→統合dialog(下記2)
- **「この成立を取り消す」**(danger文字ボタン・下線・`aria-haspopup="dialog"`)→
  取り消しdialog(下記4)。取り消し後は導線ごと消える
- **attendance**(D-14): completedの詳細ではaside内(メンバーと安全のためにのあいだ)に
  「実際に会いましたか?」カード(v2部品構成・詳細は下記6)

### 2. 通報・ブロックの統合dialog — `src/latch/safety.js`(新設)・`report.js`/`blockFlow.js`削除

- 旧2flow(report.js・blockFlow.js)を廃止し、1つの`createSafetyFlow`へ統合。
  専用の`<dialog class="dlg" id="safetyDialog">`(index.html・旧modal-backdrop 2基を撤去)
- **2ステップ(D-09)**: ①対象メンバーのラジオ(1対1は選択済みで出す・グループは未選択で
  両ボタンdisabled)+説明文+「キャンセル」/「ブロックする」(ghost dng縁)/「通報する」
  (dng solid)→②「通報する」押下で**理由4択のステップをdialog内に挿入**(reports APIの
  reason必須のため・D-09決定どおり。「もどる」で対象ステップへ戻る)
- 完了はdialog内の`role="status"`表示(プロトタイプのdone状態。「{名前}さんをブロック
  しました。…」「{名前}さんについての通報を受け付けました。…」)。トーストは使わない
- **ブロック**: POST /v1/users/{id}/block(パス指定・冪等201)→done状態+
  onBlocked(詳細再取得・D-23収束)。**通報**: POST /v1/reports。提案1対1
  (participants=null)は対象ラジオもブロックボタンも出さず、reportee_id省略body(案X)+
  reasonのみ。429はRATE_LIMIT_TEXT・その他はdialog内エラー表示で開いたまま
- 提案側(04)の通報導線「この提案を通報する」もこのdialogを開く(見た目はプロトタイプ
  05の統合dialog・対象非開示の提案では対象欄が出ない)

### 3. カレンダーに追加(D-10) — `src/latch/calendar.js`(新規)

- クライアント完結の.ics生成+ダウンロード(Blob+objectURL・APIなし)。
  DTSTART=proposal.time_summary(JST「YYYY-MM-DD HH:MM」)をUTCへ変換・
  **DTEND=開始+2時間の固定値**(終了時刻はAPIにない)・SUMMARY=「LATCH（{種別}」
  (formatCategory・不明は「LATCH」)・LOCATION=area_name・UID={latch_id}@latch・
  DTSTAMP=現在時刻。RFC 5545のTEXTエスケープ(\\ , ;)を実装
- time_summaryが組めない(最小形の名残)場合はボタンをdisabled

### 4. 参加取り消し(D-08) — `detail.js`(confirmLeave・openCancelDialog)

- 取り消しdialogは既存の`#appDialog`(chrome.openAppDialog)を使う: 題「この成立を
  取り消しますか？」+影響3行(「チャットに「あなたが参加を取り消しました」と表示され、
  {名前}さんに伝わります」「あなたはこのチャットに書き込めなくなります」「残りの{N}人で
  会うかどうかは、{N}人が決めます」)+「この操作は元に戻せません。」+「やめておく」
  (ghost)/「取り消す」(danger solid)
- 確定で**POST /v1/latches/{id}/leave(空body)**→成功は`leftLatch`フラグを立てて
  再取得で取り消し後姿へ(ヒーロー「参加を取り消しました。」+リード・メンバーの自分行=
  取り消し済み・取り消し導線消滅・**チャット入力disabled+placeholder「取り消したため、
  書き込めません」**)。チャットは再同期されサーバが挿入したシステム行(sender_id NULL・
  「{表示名}が参加を取り消しました」)をdng pillで出す
- 409 ALREADY_LEFTでも同様に取り消し後姿へ収束。その他の失敗はトースト
- **詳細応答にleft_user_idsは出ない**ため、取り消し後の姿はセッション内のフラグで保持
  (再訪時はサーバの真実=matchedに戻り、書込は409 CHAT_READONLY→D-23の既存文言で
  収束。この挙動は持ち帰り2に記載)

### 5. チャット再構成 — `src/latch/chat.js`(UI全面・同期ロジックは現行踏襲)

- **ヘッダー**: 「今夜の3人」(日付語はproposalDateTextの先頭語を借用・時刻不明は
  「N人のチャット」)+「20:30 · 天文館周辺」+アバタースタック(32px・-8px重ね・
  自分=s3+枠で末尾)
- **log**: `role="log"`+aria-label+tabindex=0。先頭に開始行(acc pill・LATCHロゴ+
  「LATCHが成立し、チャットが開きました」)。**相手**=36pxアバター+「名前 · HH:MM」+
  bubble(s2・radius 18 18 18 6)/**自分**=右寄せ「あなた · HH:MM」+bubble(#262B42・
  radius 18 18 6 18)/**システム行**(sender_id NULL)=中央dng pill。msgInアニメ(.3s)
- **入力**: 単行text input(enterkeyhint=send・maxlength=1000)+50px円形送信ボタン
  (紙飛行機SVG・空でdisabled=s3/t3)。送信はPOST後に再取得(ローカル追記しない・
  trimしないで送る)は従来どおり。CHAT_READONLYはD-23の文言で読取専用化・
  completedは閲覧のみ文言・空白のみ等は「メッセージを入力してください。」
- **同期ロジックは無変更**: 30秒ポーリング・visibilitychange再取得・初回limit=100で
  5頁まで(cursor差分同期・表示は末尾500件)。30秒pollで実APIの新着も確認した

### 6. attendanceのv2様式化(D-14) — `src/latch/attendance.js`

- 05のasideに置くv2カード構成(#10121A・line縁・22px角・見出し=「実際に会いましたか?」
  (ATTENDANCE_QUESTION・09 §2.2 D-09)・「会いました」(cta)/「会えていません」(ghost)
  の2択)。ロジックは無変更(質問を表示してPOSTし、二重回答・3日窓超過はサーバの409で
  切替える)。aside置きのため`hidden`運用を追加(renderQuestionで可視化)

### 7. その他 — `texts.js`・`view.js`・`styles.css`・`main.js`・`index.html`

- texts.jsへPhase 4分の文言を集約(LATCHED/LEFT・メンバー・安全ガイド・取消dialog・
  チャットplaceholder等)。旧のREPORT_TOAST_TEXT・BLOCK_ENTRY_TEXT等の統合dialogで
  不要になった定数を削除(お知らせ・設定系は温存)
- view.jsへ純関数追加(chatMembers・chatHeadText・latchedTags・latchedRows・
  cancelDialogLines・AVATAR_COLORS)
- styles.cssへ05の章を追加(値はプロトタイプ05のinline styleから写す)+safety dialog・
  取り消しdialog部品(dlg-list/dlg-note)。旧チャット・旧通報/ブロックdialog・
  旧participants表示のCSSを削除(unblockモーダル分は設定画面で継続利用のため温存)
- main.jsはreportFlow/blockFlowの代わりにsafetyFlowを1つ生成しdetailへ注入

## 実API検証(compose local・2026-10-10)

環境: compose常設api/db(workerのみ検証用1-offコンテナ`LATCH_LLM_STUB_JEV_NOUL=0.85`、
検証後に通常workerへ復元)+`npm run dev`+vite proxy。検証ユーザーはverify-phase4-a/b/c
(POST /v1/users+consent+POST /v1/intents・明日19:00・天文館・D-02により最小形提案)。

1. **04提案姿(最小形)+通報**: aでホーム→「候補 3件」accカード→04(案内ボックス+
   判断カード・タイマー116分・目隠しアバター)→「この提案を通報する」→**統合dialog**
   (対象ラジオなし・ブロックボタンなし)→「通報する」→**理由ステップ(D-09・4択+
   もどる+送信disabled)**→「その他」→送信→done状態「通報を受け付けました。…」。
   **DBのreportsにreason=other・reportee_idはサーバが参加者から解決済み(案X)を確認**
2. **成立**: bがAPIでyes→aが「参加する」(joined状態「ほかの1人の回答を待っています」+
   回答を取り消すを確認)→status=matched→リロードで**05へ直行**
3. **05の構成**: ヒーロー「LATCHしました。」+「成立」kicker+条件タグ(19:00〜/鹿児島市
   天文館/2人)+aside(成立した内容dl・カレンダーに追加・メンバー(色付きイニシャル・
   あなた=枠つき)・安全のために)・チャット(「明日の2人」+「19:00 · 鹿児島市天文館」+
   開始行+単行input+円形送信)。スクリーンショット済み(実データ)
4. **チャット往復**: aがUI送信(送信ボタンは空でdisabled→入力で有効)→bがAPI送信(201)→
   **aの30秒ポーリングで受信**を確認(「あなた · 21:02」「phase4-b · 21:02」)
5. **matched通報**: 「通報・ブロック」→対象ラジオ(phase4-b・選択済み)→通報する→
   理由ステップ→「不快な対応」→送信→done「phase4-bさんについての通報を受け付けました。
   …」・**DBのreportsにreportee_id明示を確認**
6. **参加取り消し(D-08)**: 「この成立を取り消す」→dialog(影響3行・「phase4-bさんに
   伝わります」「残りの1人で…」「元に戻せません」)→「取り消す」→**POST leave**→
   取り消し後姿(「参加を取り消しました。」+メンバー「取り消し済み」(dng)+input
   disabled+placeholder「取り消したため、書き込めません」+取り消し導線消滅)+
   **チャットにシステム行「phase4-aが参加を取り消しました」(dng pill)を表示**
7. **サーバ契約の確認**: b(残存メンバー)の送信は**201**(会話継続)・aの再取り消しは
   **409 ALREADY_LEFT**・aの送信は**409 CHAT_READONLY**・latches.statusは**matchedの
   まま**・left_user_idsにaが記録・システム行は**messagesのsender_id NULL**で挿入
8. **ブロック(D-23)**: 「通報・ブロック」→「ブロックする」→done状態「phase4-bさんを
   ブロックしました。…」+onBlocked(詳細再取得)。blocksへ記録・**matchedのLATCHは
   cancelled化されない(safety serviceの対象はcandidate/proposed/partial_acceptのみ)**
   ため05のまま・双方の送信が**409 CHAT_READONLY**(D-23の読み取り専用化)を確認
9. **モバイル(390px)**: チャット→asideの順・システム行dng pill・タブバー表示を確認
   (スクリーンショット済み)
10. dockerログ(api/worker)にraw_text・原文の漏出なし(01 §21)

実APIで未確認(unit試験で担保): attendanceのPOST(completedはイベント時刻経過後の
statusのため今回到達不可)・.icsの実ダウンロード(クリックは確認・生成内容は
latch-calendar試験)・ALREADY_LEFT後のUI収束(latch-detail試験)・allIn経由の
05到達(Phase 3で実API確認済み・unit試験あり)

## 独自に構成した値・判断(プロトタイプに直接の原型がないもの)

- **DOM・クラス名**: プロトタイプはinline styleのため`lt-*/safety-*`等のクラス名と
  構造化はこちらの構成(値・配色・字号・余白・角丸・影はプロトタイプ05の該当指定を写す)
- **成立時刻(kicker「成立 · 19:16」)**: 詳細応答にmatched_atがなく、YES回答直後だけ
  実時刻を使い(latch内部で保持)、再訪・再読み込みでは「成立」のみ表示(時刻を出すと
  誤りになるため)。チャットの開始行も同様に時刻なし。あわせて持ち帰り1へ
- **開始行・dlの予算行が出ないことがある**: 隠蔽提案(hidden_until_match)はmatched後も
  proposalが最小形のままで、予算・目的のタグとdl行は「値の取れない行は組まない」方針
  に従い省略される(時間・場所・人数はlatch直下の解禁済みフィールドから出る)
- **アバターカラーの拡張**: プロトタイプは2色(#F2B880/#F09AC0)の固定例のため、
  3人目以降は`--stb`/`--acc`の値(#9DB2DB/#5FD4AE)で拡張(4色サイクル・AVATAR_COLORS)
- **チャットタイトルの汎化**: プロトタイプは「今夜の3人」固定のため、日付語を
  proposalDateTextの先頭語(今夜/明日/M/D（dow）)から組む。時刻不明は「N人のチャット」
- **取消dialogの実装先**: プロトタイプは専用`<dialog id="cancel-dlg">`だが、既存の
  `#appDialog`(chrome.openAppDialog・03の取り下げと同型)を再利用した
- **ブロック後の表示**: matchedのLATCHはブロックでもcancelled化されない(D-23実装の
  契約)ため、ブロック後も05のまま(次の書込でCHAT_READONLY→「このチャットは
  利用できません」で収束)。旧blockFlowコメントの「cancelled化」は進行中latchのみ
- **attendanceの配置**: completed詳細のaside内(メンバーと安全のためにのあいだ)。
  プロトタイプに画面がないためD-14どおり部品の踏襲で構成(本格的な画面裁量はPhase 5の
  a11y総点検で見直し可)

## 持ち帰り(Phase 5以降への申し送り)

1. **matched_at(成立時刻)の開示**(05改訂候補): 詳細応答に成立時刻がなく、ヒーローの
   「成立 · {HH:MM}」とチャット開始行の時刻が出せない(現在は省略・YES直後のみ実時刻)。
   latch_status_eventsのmatched遷移時刻の開示を05改訂として持ち帰りたい
2. **left_user_idsの開示**(05改訂候補): 参加持取り消し済みかは詳細応答から分からず、
   セッション内フラグでのみ取り消し後姿を保持する。再訪時はmatched姿に戻り、書込
   (409 CHAT_READONLY)で初めて分かる。自分の脱退状態の開示(またはCHAT_READONLYの
   理由区分)があると「取り消し済み」姿を恒常的に出せる
3. **attendanceの実機確認**: completed遷移(イベント時刻経過)が検証環境では翌日以降に
   なるため、実APIでの往復は未確認(unit試験で担保)。P7の実機E2Eで対象時刻を過ぎた
   成立LATCHを用意して確認する
4. **D-11簡易一覧(候補・成立タブ)**: 引き続き未実装(topnav「候補」「成立」は`#/`の
   まま)。Phase 5で横断機能とあわせて実装する
5. **理由chipsの送信済み表示**: Phase 3からの継続課題(undo→再defer以外でreasonsを
   変える経路なし)。影響は04側のみで本フェーズの変更なし
