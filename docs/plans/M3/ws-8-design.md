# M3 ws-8 設計 — フロントエンド(周辺2画面)+不成立一文統一

作成: 2026-10-02(agent1)。参照仕様: 12 M3-10 / 03 第2節・第4節・第7節・第8節・第10節 / 00 運用ルール6 / 05 §5 / 08 §5.1・D-23。依存単位(ws-3通知・ws-5ブロック・ws-7フロントコア)はすべてマージ済み。**backend変更なし・マイグレーションなし・依存追加なし**(消費するAPIはws-3/ws-5で実装済み)。

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

プロトタイプ6画面(03 §2)のうち未実装の2画面 — お知らせ一覧・設定 — を、ws-7のfrontend資産(vanilla JS・hashルーター・texts.js文言集約・vitest 170件)の上に追加し、不成立表示「この提案は成立しませんでした」を全画面で単一ソース(texts.jsのCLOSED_TEXT)に統一する(T4整合・00 運用ルール6)。具体的には次の4点である。

1. **お知らせ一覧**(03 §2・§4): topbarベルpopoverの中身を空状態から通知一覧へ差し替える。未読ドット・既読管理(POST read)を接続する
2. **設定**(03 §2): `#/settings`画面を新設し、通知許可(D-18)とブロック管理(ws-5 API)を載せる。accountPopoverの「設定」ボタン(プレースホルダ)を接続する
3. **不成立一文統一**(03 §7・12 M3-10): お知らせ一覧の終了済み提案行をCLOSED_TEXTで表示する。詳細・ホームの二値化はws-7実装済みであり、本単位はお知らせ行の追加と全文言の単一ソース確認で完結する
4. **ブロック登録導線**(§5-1承認事項): 成立済み詳細からのブロック登録(ws-7 §5-2引継ぎ。採用を前提に設計し、§5-1に承認を求める)

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | お知らせ画面の必須要素は「アプリ内通知一覧 / 既読管理」。UI名称は「お知らせ」(topbarのベルpopover)。空状態は「まだ新しい候補はありません / 条件が合うと、ここに静かに届きます。」 | 03 §2 |
| 2 | 未読表示はお知らせボタンのドット(notification-dot) | 03 §2 |
| 3 | 設定画面の必須要素は「通知許可 / ブロック管理」(友人管理はv0.4で削除) | 03 §2 |
| 4 | アプリ内通知の様式 — summary_only: 「LATCH候補があります。」+条件サマリ(日付時刻/地名/人数/種目/予算)+「あなたの条件との一致度が高い候補です。」/ hidden_until_match: 「条件が合う候補があります。一致度が高い候補です。今夜中にご回答ください。」(条件サマリなし) | 03 §4 |
| 5 | FR-46: 通知(プッシュ・アプリ内)のタップは提案詳細画面へ遷移し、回答は提案詳細画面の3択からのみ行う。バナー上の直接回答ボタンは設けない | 03 §4 |
| 6 | D-18: プッシュ許可は拒否しても利用可能(アプリ内通知にフォールバック)。許可の再要求は候補が初めて発生した時点など文脈のあるタイミングに限り、**設定画面からいつでも取り直せる** | 03 §8 D-18 |
| 7 | 不成立の表示は2種類に集約 — 自分の操作によるもの(NO・見送り・Intentの停止削除)は自身の操作履歴として表示し、それ以外(相手の回答・回答期限切れ・Intent期限切れ・Hard Constraint変化・人数不足・ブロック・競合)は「この提案は成立しませんでした」の一文で統一。相手の回答種別・ブロック事実は一切開示しない | 03 §7・D-20 |
| 8 | LATCH状態のうちユーザーに見せるのはproposed以降。nearby_alsoが選ばれている場合に限り閾値未満候補も**その発生(存在)**の通知のみを行い、条件サマリ・相手情報は通知に含めない | 03 §2 |
| 9 | 通知手段はプッシュ+アプリ内(topbarの「お知らせ」popover+未読ドット)の併用 | 03 §4・D-18 |
| 10 | フロントエンドの実装基準は`prototype/`。食い違う場合はプロトタイプ側を正とする。後続の画面(設定等)もプロトタイプのデザインシステムを踏襲する | 03 §10・00 運用ルール6 |
| 11 | デザインシステム(プロトタイプ固定)— フォント4種/ライト・ダーク/カラー変数(--green・--coral・--paper・--ink・--line・--muted)+paperテクスチャ/Phosphor Icons/3ブレークポイント(1050・800・560px)/:focus-visible・prefers-reduced-motion・aria属性・Escapeで閉じる | 03 §10 |
| 12 | topbarのpopoverのボタン(プロフィール・設定・ログアウト)はプレースホルダ。**各画面の実装で接続する** | 03 §10 既知の差分 |
| 13 | GET /v1/notifications / POST /v1/notifications/{id}/read — 本人のみ。一覧はページネーション共通規定(`?cursor=&limit=`1〜100既定20・created_at降順) | 05 §5 |
| 14 | GET /v1/users/me/blocks・DELETE /v1/users/{user_id}/block — 一覧はcursor改頁(created_at降順) | 05 §5 |
| 15 | 実施自己申告(D-09)の設問は「実際に会いましたか?」— completed遷移後3日以内・1タップ回答 | 05 §5・09 D-09 |
| 16 | 通知typeは3値: `proposal` / `nearby_candidate` / `attendance_request` | ws-3実装(notifications/types.py) |
| 17 | お知らせitem = `{id, type, latch_id, read_at, created_at, latch: {id, status, response_deadline, expires_at, completed_at, proposal} \| null}`。hidden_until_match行はproposalが`{headcount, match_level}`のみ・nearby行は最小構成(match_level="low")のため条件サマリは構造的に出ない。**my_responseは含まれない** | ws-3実装(schemas.py・設計§2.5) |
| 18 | POST readは204冪等(既読済みも204)。他人のid・存在しないidは404 | ws-3設計§2.6 |
| 19 | blocks一覧item = `{blocked_id, display_name, created_at}`(display_nameはusers JOIN・ブロック管理画面の需要から追加)。DELETEは204・行不在は**404**(冪等にしない) | ws-5設計§2.4・実装 |
| 20 | nearby通知のpayload.latch_idは**candidate状態のlatches行id**(try_promoteしない・candidateのまま)= GET /v1/latches/{id}の対象外(404) | latch_engine._nearby_in_tx実装・05 §5 |
| 21 | attendance_request行のlatch.statusはcompletedで返る。クライアントは設問を表示し、回答操作はws-4のAPIへ | ws-3設計§2.5 |
| 22 | G3完了条件: プロトタイプ6画面が03に従い実装済み(実装基準はprototype側を正とする) | 12 M3完了条件 |
| 23 | ブロック解除は遡及効果なし(候補除外・読取専用化を取り消さない。判定はblocks現物参照) | 08 §5.1・ws-5設計§2.4 |
| 24 | D-23: ブロックされた側の画面には「このチャットは利用できません」のみ表示。cancelled化・読取専用化は登録APIのtx内でサーバ側が自動適用 | 08 D-23・ws-5実装 |

### 1.3 既存実装資産との接続(マージ済みmain・frontendはws-7完了時点)

| 資産 | 位置 | 本単位での扱い |
|---|---|---|
| APIクライアント(認証・envelope・401→refresh) | `src/api/client.js` | **無変更で共用**。全呼び出しも`client.call()`を通す |
| 画面装飾(popover開閉・トースト) | `src/ui/chrome.js` | **無変更**(ws-7と同一方針)。popoverを開いたことの検知は`#noticeButton`への追加リスナで行う(§2.2) |
| hashルーター | `src/router.js` | `parseHash`へ`#/settings`の解析を追加(3行規模) |
| 文言定数(CLOSED_TEXT・CHAT_UNAVAILABLE_TEXT等) | `src/latch/texts.js` | ws-7が確保した定数群を**参照**(一文統一の単一ソース)。新規文言定数を追記 |
| 表示の純関数(条件サマリ組立・残時間書式) | `src/latch/view.js` | `conditionSummaryLines`・`remainingTimeText`を**流用**。お知らせ行の純関数を追記(§2.3) |
| LATCH詳細(3姿・通報導線) | `src/latch/detail.js` | §5-1採用時のみブロック導線を追記(§2.7)。それ以外無変更 |
| ホーム(status仕分け・終了済み非表示) | `src/latch/home.js` | 無変更(引用#7のホーム側実装はws-7完了済み) |
| index.htmlのnoticePopover(静的空状態)・notification-dot(常時表示) | `index.html` | popover中身をコンテナ化・ドットへhidden制御を追加(§2.1〜§2.2)。既存試験(latch-shell)は`#noticePopover`要素の存在のみ参照のため無傷 |
| accountPopoverの設定ボタン(プレースホルダ) | `index.html` | `id`を付与して接続(引用#12)。プロフィール・ログアウトはプレースホルダのまま温存(§1.4) |
| 実装済みAPI(notifications・blocks・read・DELETE block) | backend | **変更しない**。応答形式は§2のとおり型どおり消費 |

### 1.4 スコープ外(後続へ渡すもの。本単位では作らない)

- プッシュのフロント受信(FCM web・Service Worker・PWA化)・デバイストークン登録API → **M4以降**(11。ws-7 §1.4と同一の引継ぎ)。本単位の「通知許可」はブラウザ許可状態の表示・取り直しのみ(§2.5)
- オンボーディング(許可の初回要求)・LLM送信の同意再確認 → M4-6(12。08 D-14の告知・同意フロー)
- プロフィール編集・ログアウト導線 → accountPopoverのボタンはプレースホルダ温存(03 §10の既知差分・実装状況は03が管理)
- 通知の絞り込み(unread_only等)・既読一括API → 05に規定なし。行単位POST readで足りる
- Intent単位のお知らせ設定(notification_level) → 預け方パネル(M1実装済み)のままで設定画面の対象外(03 §3)
- チャット画面内のブロック導線 → 成立済み詳細の導線(§2.7)で担う。チャット画面そのものには置かない(08 §5.1に導線規定なし・導線の一元化)

## 2. 実装方式の選択と推奨

### 2.1 お知らせ一覧の載せ方 — 03 §2の規定どおりtopbar popoverの中身を差し替える

| 案 | 内容 | 評価 |
|---|---|---|
| **A(推奨)** | 既存の`#noticePopover`の中身(静的空状態)を、JSが描画する通知一覧コンテナへ差し替える。取得はpopoverを開いたとき+起動時1回(§2.2) | 03 §2・D-18(引用#1・#9)が「topbarのベルpopover+未読ドット」と規定。プロトタイプのtopbar構成を踏襲し、chrome.jsの開閉機構をそのまま使える |
| B | 独立画面`#/notifications`を新設し、ベルは導線のみにする | 03の規定と不合致。通知1件を確認するのに画面遷移が増え、「待つ」体験の妨げになる |
| C | popoverには直近3件のみ+「すべて見る」で画面Bへ | 2段構えになり実装が倍加する。popover内改頁(案A)で同じ役割を果たせる |

案Aの構成: `#noticePopover`内に`popover-kicker`(既存)＋`div#noticeList`＋`button#noticeMoreButton`(next_cursor残時に表示)を置く。0件のときはJSが空状態文言(引用#1・texts.jsへ定数化)を`#noticeList`へ入れる。popoverの高さは`max-height`+`overflow-y: scroll`(styles.css追記・560px以下も同じ挙動)。

取得の規則: **popoverを開くたびに`GET /v1/notifications?limit=20`を1回**取り直す(開いた時点の最新を見せる。「もっと見る」でnext_cursorを追頁 — homeのmoreButtonと同型)。失敗時は前回表示を維持し、空状態も出さない(再訪で再試行・detailの再取得失敗と同型)。レート制限(60req/分)に対しては、開く頻度は低く、1回の開閉で一覧1回+未読行のread(最大20・§2.2)に収まるため余裕がある。

### 2.2 未読ドットと既読管理 — 起動時1回の取得でドットを判定し、開いた行を既読化する

**未読ドット(引用#2)。** 専用の未読カウントAPIは05に規定がなく(ws-3 §1.4)、一覧itemsの`read_at`で判定可能である。そこで**アプリ起動時(main.js初期化・トークンがある場合)に`GET /v1/notifications?limit=20`を1回**呼び、itemsに`read_at === null`が1件でもあれば`#notification-dot`を表示、全件既読・0件なら非表示とする(ドットは`hidden`属性で制御。現状CSS常時表示のためindex.htmlへ初期`hidden`を追加)。この起動時取得の結果はpopoverの初回描画にも使い、二重取得しない。新着のポーリングは行わない(03に規定なし・プッシュ受信はM4。起動時と開く時の取得でM3は足りる)。

**既読化(引用#1「既読管理」)。** 規則は「**popoverを開いて表示された未読行にPOST readする**(開いた=見た)」の1本に絞る。表示後に未読行それぞれへ`POST /v1/notifications/{id}/read`を発行し(並行・失敗した行は握って次回開いた時の未読対象のまま)、完了後にドットを再判定する。行クリックを既読の条件にしないのは、未読のまま放置されるpopoverがドットを点灯させ続けるためである(ドットの語義を「開いていない新着がある」に保つ)。204冪等(引用#18)のため二重送信の害はない。

popoverを開いたことの検知は、`#noticeButton`へmain.jsで追加のclickリスナを置く(chrome.js無変更)。chromeのリスナが先に登録されているため、追加リスナの実行時には`#noticePopover.hidden`は開閉後の状態であり、`hidden === false`(開いた)のときだけ取得・既読化を走らせる。

### 2.3 お知らせ行の文言 — type×latch.statusのマトリクスで確定し、終了行はCLOSED_TEXTに統一する

行の文言は03 §4の様式(引用#4)を基準に、**JOINで埋め込まれた現在のlatch.status(引用#17)で分岐**する。判定と組立は`view.js`へ追加する純関数(`notificationLines(item)`・`notificationHref(item)`)に集約し、DOM描画(notice.js)と分離する。

| type | latch.status | 行の文言 | タップ先 |
|---|---|---|---|
| proposal | proposed / partial_accept | **様式どおり**(下記) | `#/latches/{id}` |
| proposal | matched / completed | 見出し+条件サマリ+バッジ「成立済み」(STATUS_TEXT流用)。回答を促す一文は出さない | `#/latches/{id}` |
| proposal | expired / rejected / cancelled | **「この提案は成立しませんでした」(CLOSED_TEXT・一文統一)** | `#/latches/{id}`(不成立姿) |
| nearby_candidate | 全status(candidateのまま期限切れでexpiredもあり得る) | 「近い条件の候補があるようです。」(設計判断・§5-2) | **なし**(candidateはGET /v1/latches対象外=404のため・引用#20。行の文言はstatusによらず存在通知のまま — nearbyは提案(proposed以降)ではないためCLOSED_TEXTの対象外) |
| attendance_request | completed | 「実際に会いましたか?」(引用#15) | `#/latches/{id}`(成立済み詳細のattendanceへ) |
| (全type) | latch === null(防御) | 「LATCHからのお知らせがあります。」 | なし |

様式どおり(proposed/partial_accept)の行の詳細:

- 見出し「LATCH候補があります。」+条件サマリ(全フィールド版は`view.js`の`conditionSummaryLines`をそのまま流用)+「あなたの条件との一致度が高い候補です。」
- hidden_until_match(proposalにtime_summaryキーなし・ws-7と同型の判定)は条件サマリを出さず「条件が合う候補があります。」+「一致度が高い候補です。」+残時間(`response_deadline`から`remainingTimeText`流用・「あとN時間で締切」)。引用#4の「今夜中に」は例示文面であり、実際の期限はresponse_deadlineで表示する
- **お知らせ行では自分の操作履歴(no/defer)を表示しない**。notifications応答にmy_responseがなく(引用#17)、フロントで二値化できないため、終了行は自分で閉じた場合も一律CLOSED_TEXTとする。理由の開示禁止(引用#7)の精神を満たし、二値化の主戦場は詳細画面(ws-7実装のclosedText)が担う。この解釈は§5-2に記録する
- 行の文言は日時(`created_at`のJST書式・format.jsの流用)と未読スタイル(`read_at === null`の行に`.unread`クラス)を添える

nearby行(引用#8)は「その発生(存在)」のみを伝える文言とし、条件サマリ・一致度・相手情報・人数を一切出さない(応答のproposalも最小構成のため構造的に出ない・引用#17)。タップ先を持たない行はリンク化しない。

attendance_request行は設問(引用#15)のみを表示し、**行からの直接回答は行わない**(FR-46: 回答は詳細画面からのみ・引用#5)。タップで成立済み詳細へ遷移し、3日窓内ならattendance導線(ws-7実装)が現れる。

### 2.4 設定画面 — `#/settings`を新設しaccountPopoverの「設定」から入る

| 案 | 内容 | 評価 |
|---|---|---|
| **A(推奨)** | `#/settings`画面を新設(routerへ解析追加)。accountPopoverの「設定」ボタン(引用#12の接続対象)のクリックでhashを切り替え、popoverを閉じる | 03 §2が設定を6画面の一つとして扱い、ブロック一覧・追頁を載せるには画面面積が要る。既存ルーター・画面切替のパターン(§1.3)に乗る |
| B | accountPopoverの中身を設定UIにする | popoverは狭く、ブロック一覧の改頁・確認モーダルが載らない。プロトタイプの構成(accountPopover=メニュー)とも不合致 |

`parseHash`は`#/settings`厳密一致を`{name: "settings"}`へ、未知hashは従来どおりホームへ戻す。main.jsの`screens`へ`#settingsScreen`を追加し、onRouteで`settings.show()`を呼ぶ(ホームの`home.load()`と同型)。設定ボタンには`id="settingsButton"`を付与し、クリックで`location.hash = "#/settings"`+popover閉鎖(`hidden`化+aria-expanded更新 — chromeのclosePopoversと同じ操作をmain.js側で実行。chrome.jsは無変更)。

画面構成(見出し「設定」+2セクション。デザインシステム引用#11に準拠):

1. **通知セクション**(§2.5): 通知許可の状態表示と取り直し
2. **ブロック管理セクション**(§2.6): ブロック一覧と解除

規定のない要素(プロフィール編集・ログアウト・テーマ等)は置かない(§1.4)。

### 2.5 通知許可 — ブラウザNotification APIの状態表示+取り直し(D-18のM3での範囲)

D-18(引用#6)の「設定画面からいつでも取り直せる」を履行する。実送信(FCM web)はM4のため、この画面で扱うのは**ブラウザの許可状態のみ**である(トークン登録を伴わないためサーバ通信は発生しない)。

`navigator.notification`相当(`Notification.permission`・`requestPermission()`)は直接参照せず、`createPermissionControl({ notificationApi })`へ**注入**する(happy-domにNotificationがないため試験必須のDI・client注入と同型)。状態と表示は次のとおり。

| 状態 | 表示 | 操作 |
|---|---|---|
| granted | 「通知は許可されています」 | なし |
| default | 「通知は許可されていません」 | [通知の許可を求める]ボタン → `requestPermission()` → 結果を再表示 |
| denied | 「通知はブラウザの設定でブロックされています。ブラウザの設定から変更できます。」 | なし(再要求APIが効かないため案内のみ) |
| API不在(非対応環境) | 「この環境では通知を利用できません。」 | なし |

全状態に補足文「許可しなくても、アプリ内のお知らせで確認できます。」を添える(D-18のフォールバック保証・引用#6)。オンボーディングでの初回要求・「候補が初めて発生した時点」の文脈ある再要求はM4の実装(§1.4)であり、本単位では設定画面のみとする。

### 2.6 ブロック管理 — 一覧+解除(確認モーダル挟み)。登録導線は成立済み詳細(§2.7)

`GET /v1/users/me/blocks?limit=20`で一覧し(引用#19)、行=表示名+ブロック日+「解除する」ボタン。next_cursor残時は「もっと見る」(homeと同型)。空状態は「ブロックしているユーザーはいません」(texts.js)。

解除は**確認モーダルを1つ挟む**(「{表示名}さんのブロックを解除しますか?」+[解除する]/[やめる]。reportModalと同型のbackdrop再利用) — 解除後の再登録導線は成立済み詳細(§2.7)しかなく、誤解除の回復に画面をまたぐ必要があるため、確認で防ぐ。確定で`DELETE /v1/users/{blocked_id}/block`を呼ぶ:

- **204** → 一覧から行を除去(ローカル除去でなく一覧を取り直してもよいが、行単位の除去で十分・カーソル保持)
- **404**(既に解除済み・引用#19) → 通信エラー扱いにせず、一覧を取り直して収束させる(回答系409の再取得収束と同型)
- 429 RATE_LIMITED → 既存のRATE_LIMIT_TEXT

解除の遡及効果なし(引用#23)はUIで説明しない(過去の提案が戻らないことの注記は規定なし。YAGNI)。

### 2.7 ブロック登録導線(§5-1承認事項) — 成立済み詳細に通報と同型の導線を置く

ws-7 §5-2の引継ぎ「チャット画面からのブロックの要否はws-8設計時に判断する(D-23体験の導線として成立済み詳細に置く価値がある)」に対し、**置く**と判断した。ブロックAPI(ws-5)は実装済みだが登録導線が画面に1つもなく、D-23体験(引用#24)の入口が存在しないままになるためである。導線は通報(08 §5.2)と同じく成立済み詳細に置き、提案詳細(proposed)には置かない(参加者非開示。通報の案Xと異なりblockはuser_idパス指定が必須で省略解決の契約がない)。

- **1対1(matched/completed)**: 「このユーザーをブロックする」ボタン(report-entryと同型)→確認モーダル「ブロックすると、このやりとりは利用できなくなります。」→`POST /v1/users/{user_id}/block`(相手固定)
- **グループ**: 対象者選択(participantsから自分以外を選ぶ・reportの選択モーダルと同型)→確認→POST
- 成功(201・冪等)→トースト「ブロックしました」→詳細を再取得。D-23のcancelled化・チャット読取専用化は登録APIのtx内でサーバが自動適用済み(引用#24)のため、表示は再取得応答で自然に収束する(回答成功後の再取得と同型)。ブロックした側のチャットは送信時409 CHAT_READONLY→「このチャットは利用できません」(CHAT_UNAVAILABLE_TEXT・既存ws-7実装)になる

§5-1で不採用となった場合は本節と`blockFlow.js`・index.htmlのblockModal・detail.js追記をファイル構成から除外する(それ以外の設計への影響はない)。

### 2.8 共通文言 — texts.jsの追記で完結させる

ws-7が確保した`texts.js`(CLOSED_TEXT・CHAT_UNAVAILABLE_TEXT等)を参照し、本単位の新規文言(お知らせ空状態・nearby文言・attendance設問・許可状態4種・ブロック空状態・確認モーダル文・トースト)を**同じファイルへ追記**する。不成立の一文統一はCLOSED_TEXTの単一参照で担保され(§2.3・§4)、文言変更はtexts.jsに集約され続ける。

## 3. ファイル構成

作るもの(frontend/配下):

| ファイル | 役割 |
|---|---|
| `src/latch/notice.js` | お知らせpopoverのflow(開く時の取得・行描画・未読行の既読化・ドット判定・追頁・起動時1回のプレロード結果の受け渡し) |
| `src/latch/settings.js` | 設定画面の骨組(2セクションのマウント・#/settings表示) |
| `src/latch/permission.js` | 通知許可コントロール(DI注入・状態→文言写像の純関数+requestPermission呼び出し) |
| `src/latch/blocks.js` | ブロック一覧・解除flow(確認モーダル・404収束・追頁) |
| `src/latch/blockFlow.js` | 成立済み詳細からのブロック登録モーダル(1対1=相手固定/グループ=選択。§5-1採用時) |
| `src/latch/view.js`(追記) | お知らせ行の純関数 — `notificationLines`(type×statusマトリクス§2.3)・`notificationHref`(candidate/nullはnull)・未読判定 |
| `src/latch/texts.js`(追記) | §2.8の新規文言定数 |
| `src/router.js`(変更) | `parseHash`へ`#/settings`解析を追加 |
| `src/main.js`(変更) | notice(プレロード+ボタンリスナ)・settingsの初期化、screensへsettings追加、accountPopover設定ボタンの接続 |
| `index.html`(変更) | `#noticePopover`中身のコンテナ化(noticeList+noticeMoreButton)・`#notification-dot`へ初期hidden・`#settingsScreen`追加・設定ボタンへid・`#blockModal` backdrop追加(§5-1採用時) |
| `styles.css`(追記) | notice-list行・未読スタイル・popoverスクロール・settings画面・ブロック行・確認モーダル(既存クラス無変更) |
| `tests/latch-notice.test.js`・`tests/latch-settings.test.js`・`tests/latch-blocks.test.js`・`tests/latch-block-flow.test.js`・`tests/router.test.js`(追記) | §4の新規試験 |

触らないもの: `src/api/client.js`・`src/api/session.js`・`src/ui/chrome.js`・`src/intent/`配下・`src/latch/home.js`・`src/latch/respond.js`・`src/latch/chat.js`・`src/latch/attendance.js`・`src/latch/report.js`・`prototype/`全体・**backend全体**(src・tests・マイグレーション・依存)。`src/latch/detail.js`は§5-1採用時のみブロック導線の追記(report-entryの並びへのボタン追加とblockFlow呼び出し・表示ロジック無変更)。

## 4. テスト方針

vitest + happy-dom・fetchモックの既存パターン(ws-7と同型)。実API・実DB・実Redisを消費しない。

- **view.js追記分(純関数)**: type×statusマトリクスの文言 — ①proposal×終了3種(expired/rejected/cancelled)がCLOSED_TEXTになる(一文統一・自分の操作由来との区別なし)②proposal×proposedの様式(summary_only=条件サマリ+一致度文/hidden=「条件が合う候補があります。」+残時間)③proposal×matched/completed=バッジ「成立済み」で回答促しなし④nearby=存在文言のみ⑤attendance_request=設問⑥latch=null防御文言。`notificationHref` — candidate行とlatch=null行はnull(proposed/matched/completed/終了行は`#/latches/{id}`)。未読判定(read_at null)
- **notice.js(flow)**: 開くでGET(limit=20)・未読行へPOST read(既読行には呼ばない)・read後にドット再判定(未読あり→表示/なし→非表示)・追頁(next_cursorで「もっと見る」・nullで非表示)・0件で空状態文言・取得失敗で前回表示維持
- **permission.js**: 4状態の文言写像・defaultでrequestPermission呼び出し→granted反映・API不在で非対応文言・requestPermission失敗(拒否)でdefault維持
- **blocks.js**: 一覧描画(display_name・created_at)・解除のDELETEパス(`/v1/users/{blocked_id}/block`)・204で行除去・404で一覧再取得・確認モーダルの[やめる]でDELETEしない・空状態・追頁
- **blockFlow.js(§5-1採用時)**: 1対1=相手固定のPOST body(パスuser_id)・グループ=選択者へPOST・成功トースト+詳細再取得呼び出し・提案詳細(mode=proposal)からは開けない
- **router.js追記**: `#/settings`解析・未知hash→ホーム(既存試験への追記)
- **既存170件**: 変更対象がview.js/texts.jsへの追記・router.jsへの分岐追加・index.htmlのpopover中身(要素自体は残存)のみのため、構成上影響しない(latch-shellの`#noticePopover`存在確認は無傷)

試験数の目安: 新規で概ね40〜55件(view.jsのマトリクス中心)。完了条件は`npm test`全緑(既存170件+新規)・`npm run build`成功。backend変更がないためtest-ciへの影響はない(mainでのtest-ci実行は運用ルールどおりスーパーバイザー検証時・直列実行)。

## 5. 未解決の論点

### 5-1. ブロック登録導線を成立済み詳細に置く — 承認事項(推奨: 置く)

08 §5.1・03 §6のいずれにもブロック登録の画面導線規定がなく、12 M3-10のws-8スコープは「設定(通知許可・ブロック管理)」(=一覧・解除)にとどまる。しかし現状ブロック登録の導線が画面に1つもなく、D-23(引用#24)の体験全体が入口を欠く。ws-7 §5-2が本単位への判断引継ぎを指定していた事項であり、**通報導線(08 §5.2「チャット画面から常に可能」)と同型の導線を成立済み詳細へ置くことを推奨する**(§2.7)。フロントのみの追加(backend・マイグレーション変更なし)。不採用の場合はblockFlow.js・blockModal・detail.js追記を除外する。

### 5-2. 設計判断の記録(docs規定の隙間への解釈)

1. **お知らせ行のnearby文言「近い条件の候補があるようです。」** — 03 §4の様式にnearby行の文面規定なし(「その発生(存在)の通知のみ」という規定のみ・引用#8)。サマリ・相手情報を出さない範囲での設計判断
2. **お知らせ終了行は一律CLOSED_TEXT(自分の操作履歴を表示しない)** — 03 §7の二値化はmy_responseの取得できる詳細画面で完結実装(ws-7)。notifications応答にmy_responseがなく(引用#17)、フロントで二値化できないため、お知らせ行では自分でno/deferで閉じた提案も統一文言とする。理由開示禁止(引用#7)を満たし、backend拡張(my_response追加)は行わない
3. **matched/completed後のproposal行はバッジ「成立済み」表示** — 成立通知を作らない(ws-3解釈②)ため通知行は増えないが、JOINの現在status(引用#17)がmatched/completedに変わった行で「回答ください」類が不当になるのを防ぐ処理。文言はSTATUS_TEXT(ws-7)の流用
4. **ブロック解除に確認モーダルを挟む** — 再登録導線が成立済み詳細(§5-1)しかないため誤解除の回復コストが高い。docs規定なしのUI判断
5. **提案詳細(proposed)にブロック導線を置かない** — 参加者非開示のため対象を指定できない(通報の案Xのような省略解決契約がblockにはない)。08 §5.2が「提案画面から常に可能」と規定するのは通報のみ

### 5-3. ws-9/G3への引継ぎ

- G3完了条件「プロトタイプ6画面が03に従い実装済み」(引用#22)は本単位の完了で6画面が揃う(実装基準はprototype/・引用#10)。実機確認は§6のスモーク項目をws-9のE2E整備へ接続できる形で記録する
- 通知許可の実送信(FCM web)・オンボーディング・D-14同意再確認はM4-6。`permission.js`のDI構造はトークン登録追加時にもそのまま使う
- 設定ボタン以外のaccountPopoverボタン(プロフィール・ログアウト)はプレースホルダ温存(03 §10の既知差分管理対象)

## 6. 完了条件(本単位の検証対象)

1. `npm test`が全緑(既存170件+新規、既存の失敗ゼロ)。`npm run build`が成功
2. 実機スモーク(compose api + `npm run preview`・手順は計画書に記載。通知・ブロックの事前データはbackend内部CLIまたはAPI直叩きで作る):
   - ①通知fixture(proposed 1件+hidden 1件+nearby 1件+attendance_request 1件)で起動時ドット表示→popoverを開く→行が様式どおり(§2.3のマトリクス)・nearby行はリンクなし
   - ②開いた後ドットが消灯し、再取得でread_atが反映されている(既読化)
   - ③終了済みlatch(cancelled等)の通知行が「この提案は成立しませんでした」で、タップで詳細の不成立姿(単一ソースCLOSED_TEXT)
   - ④設定(`#/settings`)がaccountPopoverの「設定」から開く。通知許可の状態が表示され、default状態で[通知の許可を求める]が機能する(ブラウザの許可ダイアログ)
   - ⑤ブロック登録(API直叩きで事前登録)→設定の一覧に表示名が並ぶ→確認モーダル→解除で行が消える
   - ⑥(§5-1採用時)成立済み詳細からブロック登録→トースト→チャットが読取専用(「このチャットは利用できません」)へ収束
3. 03 §7の一文統一がtexts.jsのCLOSED_TEXT単一参照でお知らせ行・詳細不成立姿の両方で担保されていることを試験(§4の①)が証拠する
4. 差分スコープ: frontend/配下のみ(index.html・styles.css・src・tests)。backend・prototype/・ドキュメント本体への差分ゼロ
