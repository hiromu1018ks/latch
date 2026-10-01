# M3 ws-7 設計 — フロントエンド(コア3画面)

作成: 2026-10-01(agent1)。2026-10-01 supervisor裁定(案X: 通報のreportee_id省略可)を§2.7・§3・§4・§5-1・§6へ反映済み。参照仕様: 12 M3-10 / 03 第2節・第4〜7節・第10節 / 01 第10節 / 05 §5〜§6 / 08 §5.2・D-23 / 00 運用ル則6

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

ホーム・提案詳細・成立済み詳細(チャット)のコア3画面を、M1 ws-5のfrontend/資産(vanilla JS・Vite・vitest 81件)の上に追加する。バックエンドは実装済み(ws-1回答系・ws-4チャット/attendance・ws-5通報・ws-3通知API)であり、本単位はフロントだけで完結する。具体的には次の5点である。

1. **ホーム**(03 §2): 既存のIntent入力画面を起点に、LATCH候補・成立済みLATCHの2セクションと、topbar「Intent N件」からのActive Intent一覧を加える
2. **提案詳細**(03 §5): visibility分岐・一致度区分・残時間表示・グループ必要人数・回答操作3択([参加する]/[今回は見送る]/[辞退する])。通報導線を含む(08 §5.2・§2.7)
3. **成立済み詳細**(03 §6): チャット・参加者の表示名とプロフィール・集合情報・次アクション・実施自己申告(D-09)。通報導線を含む(08 §5.2)
4. **不成立表示の分岐ロジック**(03 §7): 自分の操作履歴と統一文言の二値化。文言の定数化は本単位で行い、ws-8が参照する
5. **安全導線**(08 §5.2): 提案詳細・成立済み詳細の両画面からの通報(理由選択式4値)。提案段階の1対1通報を可能にするため、`POST /v1/reports`の**backend付帯変更**(reportee_id省略可+latch_id解決・supervisor裁定案X・§2.7)を本単位のブランチで実施する

お知らせ一覧・設定(通知許可・ブロック管理)と不成立一文統一の全画面適用はws-8であり、本単位はホームからの導線と文言の置き場所だけを確保する。backend変更は通報契約の付帯変更(§2.7)のみで、マイグレーションは追加しない(reportsテーブルは0001作成済み・列変更なし)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | ホームの必須要素は5つ — Intent入力導線 / Active Intent一覧 / LATCH候補(proposed) / 成立済みLATCH / 未読表示。Active Intent一覧はtopbarの「Intent N件」カウントから到達する一覧領域(プロトタイプのtopbar構成を踏襲)。未読表示はお知らせボタンのドット(notification-dot) | 03 §2 |
| 2 | LATCH状態のうちユーザーに見せるのはproposed以降であり、candidateの詳細(条件サマリ)はシステム内部のデータとして画面に出さない | 03 §2・01 §10 |
| 3 | Intent入力画面を起点とし、残りの要素はtopbarの「Intent N件」カウントと「お知らせ」popoverから到達する領域として実装する。5要素(Intent入力・Active Intent一覧・LATCH候補・成立済みLATCH・お知らせ)が最低限の構成 | 01 §10 |
| 4 | 提案詳細の必須構成 — ①条件サマリ(アプリ内通知と同形式。相手のraw_text・NG条件は表示しない)②一致度(内部スコアの生値でなく「高」等の表示。区分はlatches.proposal.match_level)③回答操作3択 ④回答期限の残時間表示(「あと1時間23分で締切」。表示値はresponse_deadline)⑤グループ提案では必要人数と現況(「あと2人の回答が必要」)を人数のみ表示。誰が回答済みかの特定は表示しない | 03 §5 |
| 5 | visibility=hidden_until_matchの提案では条件サマリを表示しない。表示内容は「条件が合う候補があります」の本文と、一致度・回答期限(+グループなら必要人数)のみ。条件サマリ・表示名・プロフィールは成立した時点で解放する | 03 §5・08 §2.2 |
| 6 | 一致度の区分はmatch_level(high / medium / low)。high≥0.90 / medium≥0.80 / low=提案閾値0.60以上0.80未満(G2改版で提案閾値と分離) | 05 §2・latch_calc.py実装 |
| 7 | 通知(プッシュ・アプリ内)のタップは提案詳細画面へ遷移し、回答は提案詳細画面の3択からのみ行う。バナー上の直接回答ボタンは設けない(FR-46) | 03 §4・01 §10 |
| 8 | 成立(matched)で解放するもの — チャット(成立LATCH単位の閉じたルーム)/ 相手の表示名・最小限のプロフィール / 集合情報(対象日時・場所の要約)/ 次アクションの提示(チャット開始の誘導・対象時刻のリマインド)。成立済みLATCHは対象時刻の経過でcompletedとなり、完了後も閲覧は可能だがチャットの新規送信は閉じる | 03 §6 |
| 9 | 不成立の表示は2種類に集約 — 自分の操作によるもの(NO・見送り・Intentの停止削除)は自身の操作履歴として表示し、それ以外(相手の回答・回答期限切れ・Intent期限切れ・Hard Constraint変化・人数不足・ブロック・競合)は「この提案は成立しませんでした」の一文で統一。相手の回答種別・ブロック事実は一切開示しない。ホームのLATCH候補一覧から当該提案は終了済みとして扱われる | 03 §7・D-20 |
| 10 | 通報理由は選択式(不適切な内容 / 不快な対応 / なりすまし疑い / その他)とし、通報自体は提案画面・チャット画面から常に可能である。受付・記録のみで運用者の手動対応に回す | 08 §5.2 |
| 11 | D-23: ブロックされた側の画面には「このチャットは利用できません」のみ表示し、ブロックされた事実を直接通知しない。チャットの読取専用は送信時409 CHAT_READONLYで伝わる | 08 D-23・ws-5実装 |
| 12 | 実施自己申告(D-09): completed遷移後のLATCHに対し参加者が「実際に会いましたか?」に1タップで回答。対象はcompletedのみ・遷移から3日以内(超過は409 ATTENDANCE_WINDOW_CLOSED)。回答は初回のみ受理(二重は409 ATTENDANCE_ALREADY_SUBMITTED)。無回答はAPIを叩かないことで表現 | 05 §5・09 D-09 |
| 13 | チャットの書き込みはmatchedのみ。matched以外の全状態とブロック適用中は409 CHAT_READONLY。取得(閲覧)は参加者ならstatusを問わず可能。本文はtrim後1〜1000字 | 05 §5・ws-4実装 |
| 14 | 改頁共通規定: `?cursor=&limit=`(limit 1〜100・既定20)。応答は`{"items": [...], "next_cursor"}`(次がなければnull)。latchesは対象時刻昇順・同点created_at降順、messagesはcreated_at昇順(会話の自然順)。cursorは不透明文字列でクライアントは解釈しない | 05 §5 |
| 15 | 回答のエラー分岐 — 409 LATCH_EXPIRED(回答期限切れ)/ 409 ALREADY_ANSWERED(二重回答)/ 409 LATCH_CLOSED(競合クローズ後)/ 422 VALIDATION_ERROR(response値が不正)。クライアントはcodeで分岐し、messageは参考にしか使わない | 05 §5 |
| 16 | GET /v1/latchesは本人関与かつcandidate除外。GET /v1/latches/{id}は成立後に解放情報を含む。閲覧・回答は参加者のみ(404/403) | 05 §5・§6 |
| 17 | フロントエンドの実装基準は`prototype/`に合わせる。本書とプロトタイプが食い違う場合はプロトタイプ側を正とする。後続の画面(提案詳細・成立済み詳細・設定・ホーム一覧)もプロトタイプのデザインシステムを踏襲する | 03 §10・00 運用ル則6 |
| 18 | デザインシステム(プロトタイプ固定)— フォント4種(@fontsourceセルフホスト)/ ライト・ダーク(data-theme)/ カラー変数(--green・--coral・--paper・--ink・--line・--muted)+ paperテクスチャ / Phosphor Icons / 3ブレークポイント(1050・800・560px)/ :focus-visible 3pxアウトライン・prefers-reduced-motion・aria属性 | 03 §10 |
| 19 | GET /v1/intentsは自Intent一覧(statusフィルタ可。下書き一覧とActive Intent一覧の表示に使う)。GET /v1/users/meはid・display_name・profile・birth_date・profile_completeを返す | 05 §5・users実装 |
| 20 | G3関連の受け入れ — #13ホームのLATCH候補から回答可能/#14提案への両ユーザー回答操作/#15成立後の解放情報/#17成立前後の可視範囲/#19チャット送受信/#21提案画面からの通報/#22公開設定2値の表示分岐(条件サマリの有無・raw_text・NG条件・名前・プロフィールの非表示)/#23座標精度の値を表示しない | 02 §4 |

### 1.3 既存実装資産との接続(マージ済みmain・frontendはM1 ws-5のまま)

| 資産 | 位置 | 本単位での扱い |
|---|---|---|
| APIクライアント(Authorization付与・envelope解釈・401→refresh 1回→再送) | `src/api/client.js` | **無変更で共用**。3画面の全API呼び出しも`client.call()`を通す |
| トークン管理(access=sessionStorage / refresh=localStorage)・開発用トークンパネル | `src/api/session.js`・`index.html` | 無変更 |
| Intent入力フロー(parse連携・条件リスト・預け方・保存) | `src/intent/*`・`src/main.js` | **ロジック無変更**。main.jsの配線を入力画面セクションの初期化へ切り出す(§3) |
| 画面装飾(テーマ・popover・トースト・モーダル・Escape) | `src/ui/chrome.js` | popovers配列にActive Intent一覧を追加して共用。関数は無変更 |
| JST書式変換の純関数(jstParts・formatTime等) | `src/intent/format.js` | 参照のみ(流用)。新画面の書式関数は`src/latch/view.js`へ(§2.4) |
| デザイン(カラー・フォント・topbar・popover・モーダル) | `styles.css`・`index.html` | 既存クラス無変更。新画面分を追記(§3) |
| vitest + happy-dom・fetchモック | `tests/*.test.js` 9ファイル81件 | 既存81件は無傷(既存モジュール無変更により構造的に保証)。新規は同パターンで追加 |
| 実装済みAPI応答(latches・messages・attendance・intents・users/me・reports) | backend各所 | **変更しない**。応答形式は§2のとおり型どおり消費 |

API応答の型(ws-1/ws-4実装・フロントが消費する形):

- `GET /v1/latches` → `{"items": [LatchSummary], "next_cursor"}`。LatchSummary = `{id, status, response_deadline, expires_at, created_at, completed_at, proposal, is_group, my_response, remaining_responses}`。`my_response`は自分の回答のみ(他者の回答種別は出ない)、`remaining_responses`は人数のみ
- `GET /v1/latches/{id}` → `{"latch": LatchDetail}`。LatchDetailは上記+`participants[{user_id, display_name, profile}]`・`time_summary`・`area_name`(この3点はmatched/completedのみ非null)
- `proposal`は2形 — 全フィールド版(summary_only同士): `{time_summary("YYYY-MM-DD HH:MM"), area_name, headcount, category_primary, category_secondary, budget{max}|null, match_level}` / 最小版(hidden_until_matchを含む): `{headcount, match_level}`
- `POST /v1/latches/{id}/response` → 200 `{"latch": LatchSummary}` / `GET・POST messages` → `{items, next_cursor}`・201 `{"message"}` / `POST attendance` → 200 `{latch_id, actual_attended}` / `POST /v1/reports` → 201 `{report_id}`(bodyは`{reportee_id, latch_id|null, reason}`・reasonは4値コード)

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

- お知らせ一覧(popoverの中身)・未読ドットの接続・設定画面(通知許可・ブロック管理) → ws-8。本単位は既存の空状態popoverとドット要素を温存し、ホームからの導線(置き場所)だけ維持する
- 「この提案は成立しませんでした」の全画面への一文統一(T4整合) → ws-8。本単位は文言を`texts.js`へ定数化してws-8が参照する(§2.8)
- Intent一覧からの個別Intent編集・下書き再開UI・pause/resume操作 → 本単位は表示のみ(05 §5が一覧の用途を「表示」と規定)。編集は現行入力画面(新規作成)が担う
- チャットの既読・タイピング表示・Push通知の連動 → 規定なし。ポーリング表示のみ(§2.6)
- PWA化・プッシュのフロント受信(FCM web) → M4以降(11)

## 2. 実装方式の選択と推奨

### 2.1 画面ルーティング — 50行規模のhashルーターを自作する

3画面を1つのindex.htmlに載せるための画面切替方式として、次の3案を比較した。

- **A案(推奨): hashルーター自作。** `location.hash`を解析し(`#/` = ホーム、`#/latches/{uuid}` = LATCH詳細)、`hashchange`で画面sectionの表示切替を行う`src/router.js`を新設する。規定はこの2種類で、未知のhashはホームへ戻す
- B案: Viteのmulti-page build(home.html / latch.html)。topbar・テーマ初期化・セッション管理が各ページで重複し、画面遷移がフルロードになる。テーマの先行適用スクリプト(index.html先頭)も複製が必要
- C案: フレームワーク(React等)導入。M1資産(client・session・intent各モジュール・81件の試験)の全面再構成を要求され、03 §10が固定したvanilla構成と衝突する

A案を推奨する。理由は2つ。第一にFR-46(引用#7)の「通知のタップは提案詳細画面へ遷移する」をURLで表現でき、履歴の戻る・再訪・通知からの直接遷移(将来のプッシュ受信)がすべて`#/latches/{id}`一本で扱える。第二に依存が`hashchange`だけで、既存のvanilla構成への侵入が最小(B案の重複・C案の再構成を避けられる)。ルーターはURL解析と画面切替のみを担い、各画面の初期化・データ取得は画面モジュール側に置く(単体試験可能な境界)。

提案詳細と成立済み詳細を別URLにせず**1画面(`#/latches/{id}`)で扱う**。理由は§2.3のとおり、両者は同じリソースのstatus差であり、回答成立の瞬間にproposed→matchedが同じURLで起きるためである。

### 2.2 ホーム — 既存のIntent入力画面を起点に、下段へ2セクションを追加する

ホームの形について、次の2案を比較した。

- **A案(推奨): 入力画面=ホーム。** 既存のworkspace(intent-editor + deposit-settingsの2カラム)をそのままホームとし、その下に「LATCH候補」セクションと「成立済みLATCH」セクションを全幅で追加する
- B案: ホームを独立画面とし、入力画面は「新しいIntent」導線の先に分離する。01 §10「Intent入力画面を起点とし」の文言と、03 §2がホームの第1要素を「Intent入力導線」とする理由で妥当性はあるが、現行資産の配置転換(入力フォームの縮小・移動)が必要になる

A案を推奨する。01 §10(引用#3)が「Intent入力画面を起点として残りの要素を実装する」と述べており、docs上も自然であることに加え、M1資産の配置を1ピクセルも動かさずに5要素(引用#1)が揃う。LATCHの体験が「置いて、待って、通知が来たらYESかNOだけ」(03 §1)である以上、提案がない期間のホームの主役は入力画面のままでよく、提案が生まれると下段に現れる構成はこの体験の順序そのものである。

ホームのデータ取得と表示は次のとおり。

1. `GET /v1/latches`(limit=20)を1回。受け取ったitemsをクライアント側でstatusで仕分ける — `proposed`/`partial_accept` → LATCH候補セクション、`matched`/`completed` → 成立済みセクション、`rejected`/`expired`/`cancelled` → **表示しない**(引用#9「ホームのLATCH候補一覧から当該提案は終了済みとして扱われる」の直接実装。終了済みセクションは設けない)
2. `next_cursor`が残っていれば「もっと見る」ボタンで次頁を追加分だけ追加取得する(cursorは不透明文字列として扱う・引用#14)
3. topbar「Intent N件」: `GET /v1/intents?status=active`を1回取得し、件数をボタンに反映する(「Intent N件」。プロトタイプの静的「3件」を動的化する)。押下で開くpopoverにactive一覧(カテゴリ・時刻・有効期限の行)と、下書きがあればその件数のみを添える(表示のみ・引用#19。カウントはactive数とする)
4. 各カード行は`#/latches/{id}`へのリンクで、表示要素は一覧・詳細で共通の純関数(§2.4)から組む。クリックで提案詳細へ遷移する(引用#7・FR-46)
5. 空状態はプロトタイプのお知らせpopoverの文体(「まだ新しい候補はありません / 条件が合うと、ここに静かに届きます。」)に合わせる設計判断として「まだ提案はありません / 条件が重なると、ここに届きます。」を置く

### 2.3 LATCH詳細は1画面3姿 — statusで提案/成立済み/不成立に分岐する

`#/latches/{id}`を開いたら`GET /v1/latches/{id}`を呼び、応答のstatusで表示を3つに分岐させる。

| status | 姿 | 主な内容 |
|---|---|---|
| proposed / partial_accept | 提案詳細(03 §5) | 条件サマリ(visibility分岐)・一致度・残時間・グループ人数・回答3択(my_responseなし)or 回答済み表示(my_responseあり) |
| matched / completed | 成立済み詳細(03 §6) | 参加者(表示名・プロフィール)・集合情報(time_summary・area_name)・次アクション・チャット(matchedは送信可・completedは閲覧のみ)。completedかつ3日以内ならattendance |
| rejected / expired / cancelled | 不成立(03 §7) | my_responseがno/deferなら自身の操作履歴(「辞退しました」/「今回は見送りました」)、それ以外は統一文言(引用#9・§2.8) |

分岐は純関数(`view.js`のmode判定)として切り出し、URLとAPI応答の組だけで結果が決まるようにする。ページ内で状態が変わる場面(回答送信・成立・締切)はすべてAPI応答で再描画するため、分岐の入力は常にサーバのstatusである。404 NOT_FOUND(存在しないlatch_id)は「提案が見つかりません」+ホームへ戻る導線、403 FORBIDDEN(参加者以外)は同じく「提案が見つかりません」で扱う(参加者でない事実の開示を避ける・引用#16)。

### 2.4 表示の組立 — API応答の既存フィールドだけから組み、フロントで計算しない

一致度区分・残時間・グループ必要人数はAPI応答の既存フィールドから供給され、フロント側で新たな計算をしない(03の表示規定に従う)。表示変換はすべて`src/latch/view.js`の純関数(DOM非依存)とし、単体試験の対象にする。

- **visibility分岐**: proposalは2形(§1.3)で、visibilityの値自体は応答に含まれない。よって**time_summaryキーの有無**で分岐する(推測ではなく、生成側の2形と1:1に対応する)。最小版(hidden_until_matchを含む)では条件サマリ欄を出さず「条件が合う候補があります」の本文を置き(引用#5・03 §4のアプリ内通知様式)、一致度・残時間(+グループなら人数)のみを表示する。全フィールド版は条件サマリを組む(§2.4末尾の書式)
- **一致度**: match_level `high`/`medium`/`low` → 「高」/「中」/「低」(03 §5が「『高』等の表示」と例示。生スコア値はどこにも出さない・引用#4)
- **残時間**: `response_deadline − 現在時刻` の表示書式変換のみ — 24時間超は「あと1日と3時間で締切」、1〜24時間は「あと3時間20分で締切」、1時間未満は「あと40分で締切」、経過後は「締切」(03 §5「あと1時間23分で締切」の例の拡張・設計判断)。秒は表示しない。提案詳細の表示中は30秒間隔のタイマーで書式を更新し、残時間0で回答ボタンをdisabledにする(送信の可否判定そのものはサーバ側のまま・409 LATCH_EXPIREDで確定)
- **グループ必要人数**: `is_group`かつ`remaining_responses > 0`のとき「あと{remaining_responses}人の回答が必要」(引用#4・03 §5)。必要人数としては{headcount}人と併記する。誰が回答済みかの個人の特定は行わない。1対1(is_group=false)では人数の現況表示を出さない
- **回答済み表示**: my_responseありのとき回答ボタン群を差し替える — yes: 「参加します」+partial_acceptなら「あとN人の回答が必要」、defer: 「今回は見送りました」、no: 「辞退しました」
- **成立済みの集合情報**: 詳細応答の`time_summary`(YYYY-MM-DD HH:MM)・`area_name`・participantsをそのまま表示する(ともにmatched/completedのみ・§1.3)。次アクションは「チャットで挨拶を交わしましょう」+集合情報の再掲(対象時刻のリマインド・引用#8)とする(文言は設計判断)
- **条件サマリの書式**(全フィールド版・03 §4のアプリ内通知様式に準拠): 日付・時刻(time_summary) / area_name / {headcount}人 / category_secondary(なければcategory_primary) / 予算上限はbudget.maxがあるとき「ひとり{N}円まで」。raw_text・NG条件・座標・距離は表示しない(引用#20の#22・#23)

### 2.5 回答操作 — 3択の文言と値を固定し、409系は再取得で解決する

回答操作はUI文言→API値を次のとおり固定する(STATUS単位表・03 §5): [参加する] = `yes` / [今回は見送る] = `defer` / [辞退する] = `no`。送信は`POST /v1/latches/{id}/response`(`{"response": "..."}`)で、ボタン押下から応答まで二重送信を防ぐ(M1 saveFlowと同型のpendingフラグ)。

成功(200)時は応答のlatchで画面を更新する。statusが`matched`になっていれば成立済み姿(チャット含む)へ、`partial_accept`なら回答済み+残人数表示へ、`rejected`(no/defer)なら操作履歴表示へ、それぞれ遷移する。participantsの解放が必要なため、回答成功後に`GET /v1/latches/{id}`を1回取り直して描画し直す(表示の唯一の真実は詳細応答とする)。

409系(引用#15)はすべて「詳細を再取得して現在の姿へ収束させる」の一本で処理する — `LATCH_EXPIRED`/`LATCH_CLOSED` → 再取得の結果(不成立姿・統一文言)をそのまま表示、`ALREADY_ANSWERED` → 再取得して回答済み表示。いずれも追加の文言を重ねない。429 RATE_LIMITEDは既存の文言(「操作が集中しています。少し時間をおいてもう一度お試しください。」)を使う。VALIDATION_ERRORは発生しない経路(3択の値はフロントで固定)だが、既定のグローバル表示へ落とす。

### 2.6 チャット — 30秒ポーリング+送信後即時再取得で足りる

メッセージの受信方式として、ポーリング(A案)とリアルタイム(B案: WebSocket/SSE)を比較した。05 §5にリアルタイム経路の規定がなく、対象が「対象時刻まで数時間の閉じたルーム」(03 §1)で即時性の要求が緩いため、**A案: 30秒間隔のポーリング+送信後の即時再取得**を採用する。タブの復帰(`visibilitychange`のvisible)でも1回取り直す。レート制限(60req/分・08 §5.4)に対しては、チャット画面のポーリングが2req/分に収まり、ホームの初回取得(2〜3req)と併せても余裕を持つ。

初回取得の組み立ては、実装の改頁方向に注意を要する点である。`GET /v1/latches/{id}/messages`はcreated_at**昇順**のキーセットで、cursorなしの呼び出しは**最古の20件**を返す(ws-4実装・引用#14)。会話の末尾(最新)を表示するには、`next_cursor`がnullになるまで頁を進める必要がある。よって初回は`limit=100`で取得し、`next_cursor`が残る限り続ける(上限5頁=500件で打ち切り、超過時は先頭を省略して表示する)。以降のポーリングは、保持している最終頁のcursorから差分を追記する。通常の運用(数時間のルーム)では1頁で尽きる。

表示の規則は次のとおり。

- 自分/相手の判定は`sender_id === 自分のuser_id`で行う。自分のuser_idはセッション確立後に`GET /v1/users/me`(応答のid・引用#19)を1回呼んで保持する(メモリのみ・再ログインで再取得)
- 左右の振り分け(自分=右・相手=左)と、送信者名の解決は詳細応答のparticipantsから行う(messages応答は表示名を含まない・ws-4設計どおり)。グループ(3〜4人)は全員の表示名を示す
- 送信はtextarea(1000字上限)+[送信]。201応答でローカルに追記せず、再取得して描画する(順序の真実をサーバに保つ)。本文はフロントではtrimしない(サーバ検証に委ねる・空白のみは422を表示)
- `409 CHAT_READONLY`を受けたら入力欄をdisabledにし「このチャットは利用できません」を表示する(引用#11・D-23の文言をそのまま使う)。ブロックされた事実の推察を避けるため、これ以上の理由は表示しない
- status=completedでは最初から入力欄をdisabledにし「対象時刻を過ぎたため、このチャットは閲覧のみできます」を表示する(03 §6・文言は設計判断)

### 2.7 attendanceと通報 — completed詳細の2導線

**attendance(D-09)。** 成立済み詳細のうち`status=completed`かつ`completed_at`から3日以内のとき、「実際に会いましたか?」の2択([会いました]=`true` / [会えていません]=`false`)を表示する(引用#12)。3日経過後は`completed_at`と現在時刻の比較で非表示にする(表示の条件判断であり、フロントで新たな状態を作らない)。未回答かどうかはAPI応答に判別フィールドがないため、**質問を表示してPOSTし、409 ATTENDANCE_ALREADY_SUBMITTEDで「回答済み」表示に切り替える**(05 §5「初回のみ受理」と応答スキーマからの演繹・設計判断)。409 ATTENDANCE_WINDOW_CLOSEDは再取得で非表示化する。

**通報(08 §5.2・案X確定)。** 提案詳細と成立済み詳細の両画面に通報導線を置く(引用#10の「提案画面・チャット画面から常に可能」)。理由のUI文言は「不適切な内容 / 不快な対応 / なりすまし疑い / その他」(引用#10)で、API値は`inappropriate_content` / `unpleasant_behavior` / `suspected_impersonation` / `other`の4値コード(ws-5実装)。201で「通報を受け付けました」をトースト表示する。運用側の対応状況の表示は行わない(受付・記録のみ・引用#10)。

送信形態は画面の状態で2通りに分かれる。**成立済み詳細**(participantsが取れる): 1対1は相手固定、グループは対象者選択→理由選択の2段モーダルとし、bodyは`{reportee_id, latch_id, reason}`(reportee_id明示・現行契約どおり)。**提案詳細**(proposed/partial_accept・participants非開示): 1対1(is_group=false)に限り「この提案を通報する」導線を置き、bodyは`{latch_id, reason}`(reportee_id省略・§2.7末尾のbackend付帯変更でサーバが解決)。グループ提案は参加者非開示の構造上どこを通報対象にも特定できないため、導線を置かない(is_group=trueで非表示。成立後にparticipantsから選択する形で対応)。

**backend付帯変更(reportee_id省略可・supervisor裁定案X)。** `POST /v1/reports`を次のとおり拡張する(ws-7ブランチで実施・マイグレーションなし)。

1. `ReportRequest.reportee_id`を`uuid.UUID | None = None`へ(省略可)。**省略時はlatch_id必須**とし、両方の省略は422 VALIDATION_ERROR
2. `report_user`(safety/service.py)でreportee_id省略時、latchの参加者(`fetch_participant_user_ids`)から通報者以外の集合を解決する。**1人(1対1)ならその者がreportee**。2人以上(グループ)は対象を特定できないため422 VALIDATION_ERROR
3. 既存のws-5検査(自分自身の通報拒否・reportee実在・latch存在・両者参加者検査)は省略経路でも等価に適用する — 解決経路では「自分以外の集合から取る」「latch参加者から解決する」により構造的に充足され、明示経路(reportee_id指定)は現行どおり
4. エラーは既存のSafetyValidationError(422)・SafetyNotFoundError(404)のマッピングをそのまま使い、新規のerror codeは追加しない
5. 変更範囲は`safety/routes.py`(ReportRequest)と`safety/service.py`(report_userの解決分岐)の2ファイル+試験。ws-6が並走中だがsafety/への競合はなく、マージ時の両側保持はスーパーバイザーが対処する(裁定条件⑤)

**ブロックは本単位に置かない。** 08 §5.1・03 §6に成立済み詳細画面へのブロック導線の規定がなく、ブロックUIはws-8の設定(ブロック管理)が担うためである。D-23成立後ブロックの追加試験(G3)はAPI経由で実施可能であり、ws-8への引継ぎとして「成立済み詳細からのブロック導線の要否」を§5-2に記録する。

### 2.8 共通文言 — src/latch/texts.jsに集約しws-8が参照する

「この提案は成立しませんでした」(引用#9)はcancelled提案の表示で頻出するため、ws-8の一文統一(T4整合)に先立ち、置き場所を本単位で確保する。新設する`src/latch/texts.js`に画面文言の定数を集める — `CLOSED_TEXT`(「この提案は成立しませんでした」)・`CHAT_UNAVAILABLE_TEXT`(「このチャットは利用できません」・D-23)・match_levelの文言マップ・完了チャットの文言・各種空状態。ws-8はこの定数を参照して設定画面・お知らせ一覧を組み、03 §7の一文統一を単一ソースで実現する。文言の変更はこの1ファイルに集約される。

## 3. ファイル構成

作るもの(frontend/配下):

| ファイル | 役割 |
|---|---|
| `src/router.js` | hash解析(`#/`・`#/latches/{uuid}`)・hashchangeでの画面section切替・未知hashはホームへ |
| `src/appState.js` | 自分のuser_id・表示中latch等の画面横断状態の保持(getUsersMeの呼び出しを含む) |
| `src/latch/texts.js` | 画面文言の定数集約(§2.8) |
| `src/latch/view.js` | 純関数 — status→3姿のmode判定・visibility分岐(proposalキー有無)・match_level→文言・残時間書式・条件サマリ組立・回答済み表示・メッセージの左右判定 |
| `src/latch/home.js` | ホームの2セクション(候補・成立済み)の取得と描画・next_cursorの追頁・Intent N件の動的化とpopover一覧 |
| `src/latch/detail.js` | LATCH詳細(3姿)の取得と描画・404/403の扱い・残時間タイマー |
| `src/latch/respond.js` | 回答flow(3択→POST response・pending制御・409系の再取得・429表示) |
| `src/latch/chat.js` | チャット(初回の複数頁取得・30秒ポーリング・送信・CHAT_READONLY/completedの切替) |
| `src/latch/attendance.js` | attendance flow(2択→POST・409の回答済み化) |
| `src/latch/report.js` | 通報flow(成立後=対象者選択+理由4択/提案段階1対1=理由4択のみ→POST・完了トースト・§2.7) |
| `src/intent/screen.js` | main.jsから切り出した入力画面の配線(ロジック無変更・§1.3) |
| `index.html` | 画面section構造(home・detail)の追加・Active Intent popover・wordmarkのhref=`#/`化 |
| `styles.css` | 新画面のスタイル追記(カード・バッジ・チャット・モーダル・空状態。既存クラス無変更・§1.2引用#18のデザインシステムに準拠) |
| `tests/latch-view.test.js`他 | §4の新規試験ファイル(frontend) |

backend側(案Xの付帯変更のみ・§2.7):

| ファイル | 変更 |
|---|---|
| `src/latch/safety/routes.py` | `ReportRequest.reportee_id`を省略可へ |
| `src/latch/safety/service.py` | `report_user`へreportee_id省略時のlatch参加者解決分岐(1対1解決・グループ422)を追加 |
| `tests/unit/safety/test_safety_service.py`・`test_safety_routes.py`・`tests/integration/test_safety_api.py` | 省略経路の試験追記(§4)。ファイル新設なし(basename一意は維持) |

触らないもの: `src/api/client.js`・`src/api/session.js`・`src/intent/`配下の既存6モジュール・`src/ui/chrome.js`(popovers配列への追記のみ・関数無変更)・`prototype/`全体・backendのsafety/以外(safety内部でも`store.py`・`cache.py`・blocks系・D-23系は無変更)。main.jsは入力画面の初期化をscreen.jsへ委譲し、共通初期化(chrome・session・トークンパネル)+ルーター起動に再構成する(importパスの調整のみでロジックは動かさない)。

## 4. テスト方針

vitest + happy-dom・fetchモックの既存パターン(M1 ws-5)に準拠する。実API・実DB・実Redisは消費しない。

- **view.js(純関数)**: visibility分岐(最小版で条件サマリ非表示・「条件が合う候補があります」の本文・一致度と残時間は表示/全フィールド版で条件サマリ組立)・match_level 3値の文言・残時間書式の4区切り(24時間超・1〜24時間・1時間未満・経過後)・status 3姿の判定・不成立の二値化(my_response=no/deferの操作履歴/それ以外は統一文言)・グループ「あとN人の回答が必要」と1対1の非表示・回答済み表示の3種・メッセージの左右判定(sender_id比較)
- **flows(respond・chat・attendance・report)**: 3択のbody値(yes/no/defer)と二重送信防止・409 LATCH_EXPIRED/LATCH_CLOSED/ALREADY_ANSWEREDでの再取得呼び出し・429の表示・chatの初回複数頁取得(next_cursor尽きまで・5頁上限)・ポーリングの開始/停止・CHAT_READONLYでの入力disabledと文言・completedの閲覧専用・attendanceの2択body・409 ALREADY_SUBMITTEDの回答済み化・reportの送信形態2通り(成立後=reportee_id明示/提案段階1対1=latch_idのみ)と理由4値のbody・グループ提案で通報導線が非表示になること
- **home.js**: itemsのstatus仕分け(候補/成立済み/終了は非表示)・next_cursorの追頁・Intent N件のカウント(active数)とpopover行
- **router.js**: hashの解析・未知hash→ホーム・hashchangeでのsection切替
- **既存81件**: 既存モジュール(src/intent/*・api/*)を無変更とするため、構成上影響しない。main.jsの再構成はindex.html側の要素を変えない限り既存試験の対象外

試験数の目安: frontendは新規で概ね60〜80件(view.js中心)。backendは案Xの省略経路をunit(safety/service・routes: 1対1解決・グループ422・両方省略422・latch_id不在404・非参加者のlatch指定422・解決経路で自分自身がreporteeにならないこと)+integration(test_safety_api: 実DBで省略bodyの201とreportee解決・明示経路の現行試験が無傷)へ追記する。frontendの`npm test`全緑・`npm run build`成功・backendのunit/test-ci全緑を単位の完了条件に含める(test-ciの実行は運用ルールどおりスーパーバイザー検証時・ws-6との同時実行禁止)。

## 5. 未解決の論点と裁定記録

### 5-1. 提案詳細(proposed)からの通報 — 解決済み(案X・2026-10-01 supervisor裁定)

08 §5.2(引用#10)が「提案画面から常に可能」と要求する一方、`POST /v1/reports`のreportee_id必須(ws-5実装)と`GET /v1/latches/{id}`のparticipants=matched/completedのみ(05 §5・ws-1実装)の組合せでは提案段階の通報が実装できない契約の空白があった。**supervisor裁定で案X(reportee_id省略可+latch_id解決のbackend付帯変更)が確定**し、§2.7・§3・§4に反映済み。裁定の条件は次のとおり。

1. 省略時はlatch_id必須・サーバが当該latchの参加者から通報者以外を解決(§2.7の確定条件1〜3に同じ)
2. 既存のws-5検査(reportee実在・latch参加者検査)は省略経路でも等価に適用
3. **docs改版候補(人間領域・実施は次回改版時)**: 05 §5へreportee_id省略可の追記、08 §5.2へグループ提案の制約(参加者非開示のため対象選択不能→成立後限定)の明記
4. 本backend変更はws-7のブランチで実施。ws-6並走中のsafety/競合はなし(裁定条件⑤・マージ時の両側保持はスーパーバイザーが対処)

### 5-2. ws-8への引継ぎ(確定事項)

- `texts.js`の定数群をws-8が参照する(§2.8・一文統一の単一ソース)
- 成立済み詳細からのブロック導線は本単位で置かない(§2.7)。ws-8の設定(ブロック管理)と分担するが、「チャット画面からのブロック」の要否はws-8設計時に判断する(D-23体験の導線として成立済み詳細に置く価値がある)
- お知らせpopoverの中身・未読ドットの接続(GET /v1/notifications)はws-8。本単位は空状態popoverとドット要素を温存する
- ホームの「Intent N件」popoverは表示のみ(§1.4)。個別Intentの編集・下書き再開のUIはws-8以降で判断する

## 6. 完了条件(本単位の検証対象)

1. `npm test`が全緑(既存81件+新規、既存の失敗ゼロ)。backendのunit(safety追記込み)が全緑
2. `npm run build`が成功し、`npm run preview`で3画面が開ける
3. 実機スモーク(compose api + preview・手順は計画書に記載): ①ホームにLATCH候補・成立済みが表示される(ない場合は空状態)②提案詳細でvisibility両形(hiddenは条件サマリなし・summary_onlyは条件サマリあり)・一致度・残時間・グループ人数が表示され3択で回答できる③双方yesで成立済み詳細(チャット送受信・参加者・集合情報)へ遷移する④completedでattendanceに回答できる⑤成立済み詳細から通報できる(reportee_id明示)⑥**提案詳細(1対1)から通報できる(reportee_id省略・latch_idのみ)**⑦409系(期限切れ回答・二重回答・閲覧専用チャットへの送信)がそれぞれ規定の表示に収束する
4. 03 §7の二値化(自分の操作履歴/統一文言)がdetailの不成立姿で確認できる
5. backendの省略経路試験: reportee_id省略+1対1latchで201(解決先が相手)・グループlatchで422・latch_id不在で404・両方省略で422・明示経路(reportee_id指定)の現行挙動が無傷(test-ciはスーパーバイザー検証時・運用ルール1のws-6との同時実行禁止)
