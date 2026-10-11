# UI v2移行 Phase 7 実施報告 — 統合試験と切替完了(ゲート承認を依頼して停止)

- 日付: 2026-10-12
- 出典: [ui-v2-roadmap.md](../ui-v2-roadmap.md) Phase 7 / [phase0-decisions.md](phase0-decisions.md) /
  各phase-reportの持ち帰り( Phase 1〜6)
- 対象: Phase 6(React載せ替え)後の実装
- 状態: **完了条件は機械検証の範囲で満たした状態で、ゲート承認をユーザーに依頼して停止する。**
  全自動試験緑・実機E2E合格・docs整合・本報告コミット済み。ユーザーのゲート承認は
  STATUS.mdのUI v2欄へ記録する(承認後に11 Phase 0=内部αへ)

## 1. frontend全試験の緑化(残課題の回収)

- 実行: `cd frontend && npx vitest run` → **28 files / 367 tests すべて緑(EXIT=0)**・
  `npm run build` 緑。報告書記載の暫定表示・`skip`/`todo`は試験内に存在しないことを
  grepで確認済み(smoke等の「skipリンク」a11y表記は該当外)
- **回収した残課題=タイムボム試験3件**(2026-10-10時点の報告では緑、本日実行で赤):
  `tests/conditions.test.jsx`(1件)と`tests/latch-detail.test.jsx`(2件)が、fixtureの日付
  (2026-10-10)に対する相対語(「今日」「今夜」)を**実行日の実時計**で判定する実装を
  正として期待値を書いていたため、日付が変わると必ず赤になる構造だった。修正は
  試験側へ`vi.useFakeTimers({ now: <fixture時刻>, toFake: ["Date"] })`で時計を固定する
  形(Dateのみ偽装・タイマーは実のまま=RTLのwaitForや1秒更新は実時間で動く)。
  実装は無変更(相対語が実時計基準なのはv2仕様どおり)。コミット0c0b4a6
- 同型の時計依存は純関数試験(latch-view/format/highlight等)にはない(引数でnowIsoを
  受ける設計のため)。残るDOM試験はタイマー検証を相対表現で書いており時計非依存
- **Phase 6持ち帰り1の清掃**: `src/ui/icons.js`の未使用DOM組立関数
  (`icon`/`logoSvg`/`iconHtml`)を削除(ICONS表と`Icon.jsx`消費は不変・参照残 nada を
  grepで確認)。同じコミットに含む

## 2. 契約試験(両実装)とminiflare probe

前提の遵守: phase1b-reportの注意どおり、**参照実装側はworker停止条件**で実行
(`docker compose stop worker`+検証worker停止・実行後に復帰)。単一コマンドで終了
コードを残す。

```
uv run pytest tests/contracts/test_api_parity.py -q
  → 133 passed, 1 warning in 24.01s (EXIT=0)
uv run pytest tests/contracts/test_cloudflare_parity.py -q
  → 130 passed, 1 warning in 7.52s (EXIT=0)
```

- ケース内訳の実査: `cases.py`はCASES=130+PLANNED_REGRESSION=4。参照側133=
  契約130ケース+「将来回帰は宣言のみで実行しない」検査3件(試験関数)。D1側は
  契約130ケース。**P1B追加13件(defer reasons・undo・leave・participants_includes_owner・
  expiry 3択)込み**の数値と一致(12 roadmap Phase 7・phase1b-reportの計上どおり)
- **miniflare probe**: `make cf-dev`(pywrangler dev・8787)+`make cf-probe` →
  `1 passed, 112 deselected (EXIT=0)`。実miniflare runtimeの実測証拠を
  `docs/testassets/results/cloudflare-runtime-probe-20261011-223444.yaml`へ保存
  (D1 batch/Queues producer-consumer/scheduled handler/RS256検証/datetime・decimal等
  全項目ok・adopted_pathのblockerはpyodide未梱包モジュール2件の既知記録どおり)

## 3. 実機E2E(miniflare・実ブラウザ)

環境: `make cf-dev`相当(pywrangler dev --port 8787・`LATCH_LLM_STUB_JEV_NOUL=0.85`を
`--var`で注入=検証専用。通常workerのstub既定0.5では提案が形成されないため、
phase3〜6と同じ調整)。検証ユーザーはp7-a(ふみ)/p7-b(かい)をAPIで登録+同意。
ユーザー操作はZCodeの実ブラウザ(In-app Browser・1280px/390px)で実施し、相手側の
操作とDB検証はAPI/D1直で行った(画面内の文言・状態はスナップショットで取得)。

| # | 操作(実ブラウザ) | 観測された結果(証拠) |
|---|---|---|
| 1 | DEVトークンパネルにIdPトークンを貼って接続 | パネルが消えて01へ(無効トークン時は「トークンが無効です」status表示も確認) |
| 2 | 01: 例文chips「飲み」→読み取りchips→「読み解く」 | chips表示(スタブ既定値)・CTA有効化→`#/confirm`へ遷移 |
| 3 | 02: 行編集(時間=「今日 20:00〜」・場所=「天文館」)・人数seg「含む」・期限3択(既定=今日 22:00・過ぎた択は選択肢に現れない)→「この条件で預ける」 | **POST /v1/intents 201**→`#/standby?deposited=1`着地 |
| 4 | 03着地姿 | 「預けました。/あとは、待つだけ。」+バッジ「待機中 · 1件」+メインカード(原文引用・タグ・role=timerの1秒更新「22:00 の取り下げまで 14:16:32」)・3カード・ほかの意思空状態 |
| 5 | 「条件を直す」→02編集モード→目的行を「軽く飲みたい」に編集→保存 | GET復元(end+3h補完の表示・seg復元済み)→**PATCH成功**(サーバでversion 2)→03通常姿へ |
| 6 | B側をAPIで同一条件預け(201)→パイプライン待ち | worker(Queue consumer)が自動処理し**提案形成**(GET /v1/latchesでproposed・minimal形・回答期限つき) |
| 7 | 03/一覧: 候補トースト(ロゴ+「LATCH候補があります」+見る)+navバッジ「候補 1件」 | トーストの「見る」→`#/latches/{id}`(04)へ |
| 8 | **04 open状態(最小形)** | stb案内ボックス「条件が合う候補があります —…」+回答期限timer(114:28・1秒更新)+目隠しアバター+回答2択+「この提案を通報する」 |
| 9 | 「参加する」 | **joined**(「参加と回答しました。」+「ほかの1人の回答を待っています。」+whoLine「あなた(参加)+回答待ちの1人」+「回答を取り消す」) |
| 10 | 「回答を取り消す」(undo成功系) | **open姿へ復帰**(2択が戻る・POST undo 200) |
| 11 | 再「参加する」→BをAPIでyes(partial_accept 200)→Aが再「参加する」 | **allIn状態**(「2人全員が参加」+「LATCHが成立しました。…」+「チャットを開く」・whoLine「2人全員が参加」) |
| 12 | 「チャットを開く」 | **05成立姿**へ(Phase 6持ち帰り3=allIn→05到達の実機回収) |
| 13 | **05成立姿** | ヒーロー「成立 · 07:51」(yes直後の実時刻)+「LATCHしました。」+タグ解禁(20:30〜/鹿児島市バー宵待/2人)+aside(成立した内容dl+「カレンダーに追加」+メンバー+安全のために+「この成立を取り消す」)+チャット(「今夜の2人」「20:30 · 鹿児島市バー宵待」・開始行・単行input+円形送信) |
| 14 | チャット送信×2(UI) | ログに即座反映+**B側のGET messagesで2件受信を確認**(sender=A) |
| 15 | 「カレンダーに追加」 | **downloadイベント発火**(クライアント完結の.ics生成=Phase 4持ち帰りの実機回収。内容はlatch-calendar試験で担保) |
| 16 | 「通報・ブロック」→対象ラジオ(相手選択済み)→「通報する」→理由4択→送信 | 理由ステップ(D-09)→**done状態「p7かいさんについての通報を受け付けました。」**(matchedなのにreportee_id明示=案Xどおり) |
| 17 | 「この成立を取り消す」→dialog(影響3行+元に戻せない)→「取り消す」 | **POST leave**→D1で`left_user_ids`にA追加・**システム行(sender_id NULL)「p7みなとが参加を取り消しました」**をチャットに実表示 |
| 18 | 離脱後の再訪(リロード=サーバの真実) | matched姿に戻り(left_user_ids非開示のため)書込→**409 CHAT_READONLY→「このチャットは利用できません」へ収束**(D-23) |
| 19 | completed化(テスト準備としてD1直でstatus更新・3日窓ルール`completed_at+3日`を実装から確認済み)→再訪 | attendanceカード「実際に会いましたか?」表示(aside内・メンバーと安全のためにのあいだ)→「会いました」→**「回答を送りました」done状態**。D1のcalibration_recordsは`actual_attended=1`・二重POSTは**409 ATTENDANCE_ALREADY_SUBMITTED**(APIで確認・参照実装と同意味`cancelled_after=NOT attended`) |
| 20 | 4状態目=見送り: 別ペア(食事・明日昼)を01→02→預ける→提案形成→04で「今回は見送る」 | **declined未送信**(理由chips 4択+「待機中に戻る」+「取り消す」)→chips2件選択→「待機中に戻る」で**defer+reasons送信**→D1のresponsesに`reason_codes:["time","mood"]`を確認 |
| 21 | 送信済みdeclinedの再訪(直接URL) | closed描画のdeclinedカード(「見送りました。」+chips表示のみ)→「取り消す」=真のundo→**rejected→proposedへ復帰**(04 open姿) |
| 22 | A「参加する」(partial_accept)→BをAPIでdefer(no)→**rejected**→A再訪 | 「この提案は成立しませんでした」のv2カード+「ホームへ戻る」(一文統一) |
| 23 | A(yes側)のundoをAPI実行 | **409 LATCH_CLOSED**(他者のdefer残存時は戻せない=phase1b実装どおり)。latch_status_eventsに遷移4段(proposed→rejected→proposed→partial_accept→rejected)を確認。UI側の409収束(トースト+再取得)はunit試験で担保 |
| 24 | 03の「取り下げる」→dialog(条件引用+説明)→「取り下げる」 | **DELETE成功**→取り下げ済みカード(「この条件は取り下げました。…」+「もう一度預ける」)+バッジ「待機中 · 0件」・APIのactive一覧からも消滅 |
| 25 | 05で「通報・ブロック」→「ブロックする」 | **done状態「p7かいさんをブロックしました。…」**+GET /v1/users/me/blocksに相手+**D1のblocksに行**を確認(matchedなLATCHはcancelled化しない=D-23の契約) |
| 26 | モバイル390×844 | tabbar表示(4タブ+バッジ)・`page-main`のpadding-bottom 112px・チャット高さ**664px=calc(100dvh − 180px)**をcomputed styleで確認(スクリーンショット: `docs/testassets/results/p7-mobile-390px.png`) |

- **04の回答4状態(open/joined/allIn/declined)・undo成功・undo 409・attendance・
  .icsダウンロード・allIn→05到達はすべて実機で回収**(Phase 3〜6の持ち帰りの解消)。
  matched詳細の通報は対象明示・提案(1対1)の通報は対象非開示の分岐は統合dialogを
  実機で開いて確認(対象欄の出し分けをスナップショットで確認)
- 実APIで未確認(unit担保の申告): attendance二重回答の**UI再POST経路**(409で切替は
  respond/attendance試験で担保・二重409自体はAPIで実測)・safety 429・お知らせ
  popoverの既読化(Phase 5/6で実API確認済み・今回は未再確認)・設定の解除dialog
  (Phase 5で実API確認済み)

### E2Eで検出・修正した不具合(2件)

1. **04のallIn一度きり表示が消費されない**(コミット9e41fa3): 「チャットを開く」が
   `showAllInOnceRef`をクリアしておらず、成立後の再取得(参加取り消しなど)のたびに
   04のallInカードへ戻り、05の取り消し後姿が出せなかった。旧実装(Phase 4の素の
   DOM版)では開いた時点でフラグを消費していたためReact載せ替え時の移植漏れ。
   修正(openChatでフラグ消費)+回帰試験(allIn→チャット→leave→LEFT_TITLE)
2. **03取り下げ時にchrome側の待機中バッジが減らない**(コミット02ce895): 画面内の
   バッジ(「待機中 · N件」)は減るがchromeのcounts供給が更新されず、ヘッダー/tabbarの
   「待機中」バッジが実件数より多く残る。修正(withdrawで`setCounts({standby})`も
   供給)+回帰試験(試験harnessへchrome検査用Probeを追加)

いずれも全vitest緑(367件)・build緑を保ったまま修正。実機で修正後の挙動も再確認
(預ける→バッジ1件→取り下げ→バッジ消灯)。

### 環境の既存差分(実装欠陥ではない・記録)

- **miniflareのローカルD1がP1B以前のスキーマだった**: 詳細GETが
  `no such column: left_user_ids`で503化(一覧は通る)。phase1bのD1移行(0005)が
  ローカルdev用sqliteへ未適用だったもの。`CLOUDFLARE-MIGRATION-STATUS.md`記載の
  既定手順(devが使う実sqliteへ直接適用・wrangler d1 execute --localは別sqlite参照の
  ため)で0005を適用しcf-devを再起動して解消。**契約試験・probeは各々in-processで
  スキーマ管理するため本 drift の影響外**(緑のままだったのは正常)
- `--var LATCH_LLM_STUB_JEV_NOUL=0.85`は検証専用の注入(永続化なし・wrangler.jsoncは
  無変更)

## 4. Workers Assets配信検証

`make cf-dev`(8787・`run_worker_first=["/v1/*","/health","/probe/*"]`・
`not_found_handling="single-page-application"`)に対してcurlで実測:

| 項目 | 実測 |
|---|---|
| `/`(index.html) | 200・`text/html`・ETag+304(Assets配信) |
| ディープリンク`/latches/{uuid}` | **200 text/html(SPAフォールバック)**・`#root`を含む |
| フォント(woff2 8種) | **同オリジン`/assets/*`で200**・`font/woff2`(Google Fonts等の外部参照なし・D-12 self-hostの配信面の確認)。実ブラウザでも全woff2が304/200で読めており、モバイルスクリーンショットでserif/monoの描画を確認 |
| オリジン分離 | `index.html`の外部参照**0件**(grep)。API・Assets・フォントがすべて同一オリジン |
| worker-first | `/v1/*`はAssetsに飲まれずAPIが応答(E2E全体がそう)・`/health` 200 `{"status":"ok"}`(AssetsのSPAフォールバックに遮られない) |
| CSP | **設定なし**。docs(00〜03・08)にCSP要件の規定が存在しないことを確認したため、P7の裁量で追加はしない(要件ができた時点での改版対象・本reportに事実を記録) |

## 5. docs整合(実装に追随する方向のみ)

| 文書 | ズレ | 修正 |
|---|---|---|
| 00 v1.2(変更点欄) | 「旧prototypeはUI v2移行完了(**Phase 6**)まで参照用に残す」 | 完了はroadmap §3.1の正本=**Phase 7**へ追随 |
| 12 v0.4(変更点欄) | 「UI v2移行**Phase 0〜6**を挿入」(§3.1本体と§6はP0〜P7) | ヘッダを**Phase 0〜7**へ追随 |
| 03 v0.5(§2画面一覧) | 成立一覧のルートが**`#/latched`**(実装=`#/matched`) | **`#/matched`**へ追随 |
| STATUS.md | UI v2欄 | P7行を完了(2026-10-12)+ゲート承認待ちへ・P7実施記録を追加 |

修正は3トークンのみ(natural-japaneseのlintで確認・統計表示のみで違反なし)。03の
画面構成記載と実装(React)の対応はPhase 6持ち帰り2どおり§2表とroutes/コンポーネント
の突合で一致を確認(01=#/・02=#/confirm・03=#/standby・04/05=#/latches/{id}・
候補=#/proposals・成立=#/matched・設定/同意/お知らせpopover)。

## 6. 完了条件の確認(roadmap Phase 7)

- [x] 全自動試験緑: frontend 367緑・build緑・契約133/130緑・probe緑
- [x] 実機E2E合格: miniflare+実ブラウザで01→02→預ける→03(取り下げ/修正)→
      04(回答4状態+undo 2種)→05(チャット・通報/ブロック・取り消し・attendance・
      カレンダー)の往復+モバイル390px(§3の表どおり)
- [x] docs整合: 00・03・12・STATUS.md(§5・実装追随方向のみ)
- [x] phase7-report.mdコミット済み: 本書
- [ ] **ユーザーのゲート承認をSTATUS.mdへ記録** — 承認を待って停止中

## 7. ゲート承認のお願い

UI v2移行(P0〜P7)は完了条件を満たしました。ご確認のうえ、承認をいただければ
STATUS.mdへ記録し、11 Phase 0(内部α)の計画立案へ進みます。本環境では
`make cf-dev`(8787)が起動したままです(検証トークン・検証ユーザーp7-a/p7-b付き)。
実物の確認にご利用ください。

## 持ち帰り(11 Phase 0以降への申し送り)

1. **docs/03改版候補(軽微)**: §10に記載のある「設定・一覧のv2様式の値」のうち
   page-main--settings 720px/page-main--lists 880px・list-title clamp(26〜40px)は
   Phase 5で構成した独自値。次回03改版時に§2表へ追記する価値あり(動作への影響なし)
2. **matchMedia判定の再読タイミング**(phase6持ち帰り3): 05のdetails初期openは描画時
   読み・React再描画でも読む差分はphase6-reportに記録済み。実挙動同等のため対応不要
3. **CSP**: 未設定(docs要件なし)。セキュリティ要件として追加する場合は08の改版を
   経てから実装する
4. **attendance二重回答・safety 429・popover既読化の実機再確認**はunit試験と
   Phase 5/6の実API証拠で担保済み(§3の申告どおり)。内部αの操作手順書に含めると
   自然に再確認できる
