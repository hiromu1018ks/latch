# M1 ws-5(フロントエンド)設計メモ

- 作業単位: ws-5 — フロントエンド(prototype準拠): parse連携・条件リストの動的連結・有効期限の既定選択計算+disabled化・必須3フィールド催促・判定不能NG条件のNG行・注意表示・保存API接続(active/draft。03 第10節の既知差分解消)(docs/plans/STATUS.md M1表 / 出典 12 M1-7 / 03 §3・§10 / 依存: ws-2〜ws-4)
- 作成: 2026-09-28(agent1 / superpowers:brainstorming使用・prototype実機起動確認済み)
- 次工程: 計画書(ws-5-plan.md)へ変換 → 実装エージェントがworktree内でTDD実装
- 並走: ws-4(レート制限)が実装中。API契約は ws-4-design.md どおり(429 RATE_LIMITED envelope)。本設計は429の表示を含むが、**実装・検証の最終合わせはws-4マージ後**を前提とする(§2.9・§4.2)。本単位の実装はbackend追加(§3.2)を1点含むため、着手はws-4マージ後が安全

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M1スコープ7(12 M1-7)を実装する。プロトタイプ(単一画面「新しいIntent」)を出発点とし、次の5点を実現して、03 §10「既知の差分」のうち入力フローに関わる4点を解消する。実装基準はprototype/側を正とする(00 運用ルール6)。

1. 入力テキストと条件リストの動的連結(POST /v1/intents/parse)
2. 有効期限4選択肢の既定選択計算と過ぎた選択肢のdisabled化
3. 必須3フィールド(category・time.start・location)の催促
4. 判定不能NG条件のNG行・注意表示
5. 保存API接続(このIntentを預ける=active/下書き保存=draft)

画面・導線は既存のまま(単一画面+確認モーダル)。ホーム一覧・提案詳細等の後続画面はM3-10、Active Intent一覧(topbar「Intent N件」から到達)も本単位外(§1.4)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | フロントエンドの実装基準は`prototype/`(Vite + vanilla JS)。本書(docs)とプロトタイプが食い違う場合はプロトタイプ側を正とする。デザインシステム・UI文言は03 §10に固定 | 00 運用ルール6・03 §10・04 §2 |
| 2 | 入力フロー: 自然言語記入(300字上限・文字数カウンタ)→ 同一画面の条件リスト(時間/場所/人数/予算/目的の行・行単位インライン修正・条件の追加)→ 預け方パネル → 下書き保存(draft・トースト)or このIntentを預ける(⌘Enter)→ 確認モーダル → Intentを確認する(POST /v1/intents status: active)→ 同一画面に留まる | 03 §3 |
| 3 | 条件リストの文言はプロトタイプ固定(「時間: 今日 20:00以降」「場所: 天文館」「人数: 2〜4人」「予算: ひとり5,000円まで」「目的: 軽く飲む」)。条件の追加ラベル(その他・曜日・移動・雰囲気)はUI上の入力補助で、格納先はすべてstructured_data.soft_constraints | 03 §3・03 §10・03 D-19補足 |
| 4 | 入力上限300字は入力欄で遮断(maxlength)。カウンタ「41 / 300」形式を常時表示 | 03 §3・05 D-19 raw_text行 |
| 5 | 必須3フィールド(category・time.start・location)がParserで推定できなかった場合は「預ける」をブロックして指定を促す。検証の強制はサーバ側(05 v0.4 §5)。時刻検証の表示もParser連携とともに追加 | 03 §3 |
| 6 | 判定不能NG条件: 条件リストにNG行を付し「この条件は確実には除外できません。参考条件として扱います」の注意表示。同意のうえSoft Constraintとして保存(黙って降格させない)。NG行の行型はプロトタイプにまだなく、Parser連携とともに追加 | 03 §3・02 D-04 |
| 7 | 有効期限は4選択肢(今夜 23:30=当日JST/明日 12:00/明日 23:30/3日後まで=now+72h)。選択肢時刻が現在より過ぎている場合はdisabled。既定の選択は「選択可能な選択肢のうちtime.start+3時間に最も近い値」。クライアントは選択値の絶対時刻をexpires_atとして送る。下書き保存(draft)では検証を適用しない | 03 §3・03 D-19 |
| 8 | parse API: POST /v1/intents/parse。応答structured_intentは07 Parser出力と同形(visibility・notification_levelを含まない=預け方パネルで選ぶ)。warningsは02 D-04注意表示のデータソース。text 300字超過も422(切り詰めしない) | 05 §5 |
| 9 | parseのエラー分岐: 503 LLM_UNAVAILABLE=入力テキストを保持した再試行ボタン/422 VALIDATION_ERROR=構造化フォームへのフォールバック(必須3+予算・人数・soft条件の手動入力)。**フォームはフォールバック専用であり、通常経路の主UIにはしない** | 07 D-17・07 §2・05 §5エラー表 |
| 10 | Parser timeout 10秒・再試行なし(サーバ側)。性能目標は入力確定から構造化プレビュー表示までp95 3秒 | 07 §1・07 D-17 |
| 11 | 保存API: POST /v1/intents。statusはactive(既定)またはdraft。draftはraw_textのみ必須(部分的な中途データでも保存)。structured_intentは変更後の全量を送る(全置換・PATCHと同一契約)。active作成では必須3・時刻検証(過去不可・+7日上限)・ジオコーディング(422 GEOCODING_FAILEDは条件リストへ戻して修正を促す)・年齢検証(422 UNDER_AGE)をサーバが強制 | 05 §5 |
| 12 | expires_atはstructured_intent内フィールド。nullの場合はサーバ側で「time_start+3時間に最も近い選択肢」を補完(クライアントの補完に依存しない)。draftでは補完せずNULLのまま | 05 §5 |
| 13 | 補完規則の単一実装: backend/src/latch/intents/completion.pyがexpires_at_candidates・nearest_expires_atを持つ(二重実装による不整合を防ぐ — 07 §2)。docstringは「消費者は保存経路(ws-3)とUI計算(ws-5)」と記す | 07 §2・completion.py |
| 14 | 認証: 全API認証済みユーザーのみ(Authorization: Bearer JWT)。認証不要はauth/tokenとauth/refreshのみ。POST /v1/auth/tokenはIdPトークンをbodyで受け、access_token(1時間)+refresh_token(回転式30日)を返す | 05 §5・C3 |
| 15 | テスト用認証構成: staging鍵ペア+テストユーザーJWT発行ツール(内部CLI issue-idp-token)。実IdPに依存しない。アクセストークン直接発行ツールは存在しない(常にPOST /v1/auth/token経由) | 10 §1・backend/src/latch/auth/tools.py・backend/README |
| 16 | エラー形式は共通envelope {error: {code, message, details}}。クライアントはcodeで分岐しmessageは参考にしか使わない。429 RATE_LIMITED(作成20件/日・更新6回/時・API 60req/分)・422 ACTIVE_INTENT_LIMIT(Active 5件)はws-4実装中 | 05 §5・ws-4-design |
| 17 | 429の超過はActive Intent数上限のみ422(ACTIVE_INTENT_LIMIT)、その他は429(RATE_LIMITED) | 05 §5 |
| 18 | クライアントはWebから着手。Web段階ではWeb Push経路のみ作動(通知は本単位外)。クライアント側トークン保存の詳細はdocsで未確定(04 D-21は認証方式・JWT契約を確定、クライアント実装の保存場所は規定しない) | 04 §2・04 D-21 |
| 19 | raw_textは非公開・ログ出力禁止(01 §21)。クライアント側でもconsole等へ意図文言を出さない規律を07 §2・08 §2.4と同様に適用する(本設計の適用判断) | 01 §21・08 §2.4 |
| 20 | G1完了条件: 02#1(下書き経路含む)〜#4がci環境でグリーン(10 §3の振り分け。LLMはスタブ) | 12 M1・10 §3 |
| 21 | ci環境: API 1インスタンス(127.0.0.1:8000)・常設。composeのapiイメージはコード変更後に再ビルドが必要。API 60req/分レート制限はws-4適用後、フロントの通信設計もこの上限内に収める | 10 §1・STATUS運用ルール4・08 §5.4 |
| 22 | docsとプロトタイプの整合は、プロトタイプ変更時とdocs改版時に相互確認(T4常時トラック)。仕様の反転を伴う変更は改版手続き(00 運用ルール7) | 00 運用ルール7・12 §4 T4 |

### 1.3 既存実装資産との接続

- **prototype/**(実機起動確認済み・Vite 6.4.2): index.html(204行)・app.js(207行)・styles.css(702行)・public/assets/textures。実装済み: 文字数カウンタ+maxlength・空テキストで「預ける」無効化・行単位インライン編集(鉛筆→input→Enter/✓確定・Escape取消)・条件の追加(select+入力)・テーマ切替(localStorage latch-theme)・popover・トースト(2.4秒)・確認モーダル(⌘Enter/Escape)。未実装=03 §10の既知差分(§1.1の5点)とtopbar周りのプレースホルダ
- **backend/(マージ済みmain)**: POST /v1/intents/parse(warnings含む)・intents CRUD(POST active/draft・GET一覧・PATCH・DELETE・pause/resume)・users・認証(token/refresh/logout)。リクエスト検証はintent_input.py(全フィールドOptionalのStructuredIntentInput・1契約1モデル)。**CORS設定なし(main.pyにCORSMiddlewareなし)**
- **completion.py**: 期限4選択肢・既定計算の単一実装(確定値13)。本設計はこれをAPI経由で消費する(§2.6)
- **compose(latch-ci)**: api 127.0.0.1:8000・DB/Redis常設。フロントの検証はこのapiへvite proxy経由で接続(§2.2)。**ws-4実装中のtest-ciとはDB状態を共有するため、実機結合確認(データを書く操作)はws-4マージ後に行う**(STATUS運用ルール1〜3と同趣旨)

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

- **Active Intent一覧・下書き一覧のUI**(topbar「Intent N件」からの導線・GET /v1/intents一覧の消費・draft再開編集)— 03 §10「topbar周りは各画面の実装で接続する」・M3-10。draftの保存API接続(POST)のみ本単位。draft再開→active化(PATCH)はAPI実装済みだがUI経路は一覧待ち
- **ホーム・提案詳細・成立済み詳細・お知らせ一覧・設定**(03 §2)— M3-10
- **本番IdPログインUI(Google/Apple)** — M1-7の字義・03 §2画面一覧にない。本単位は開発用トークン取得に限る(§2.3)
- **フロントの本番配信形態**(静的配信元・CDN・コンテナ化)— M4/staging構築時。本単位はvite dev(とbuild可否)のみ
- **429 RATE_LIMITEDの実装・実測**(ws-4が実装中)。本単位は表示の設計のみ(§2.9)
- **Playwright等のブラウザE2E自動化** — 導入しない判断と根拠は§4.2・§5-1

## 2. 実装方式の選択肢と推奨

### 2.1 リポジトリと技術選択 — 推奨: `frontend/`を新設し、prototypeの資産を持ち上げる(vanilla JS + Vite継続)

選択肢:

- **A(推奨). `frontend/`を新設**し、prototype/のindex.html・styles.css・app.js・アセット・package.jsonをコピーして本実装の出発点にする。以後のフロント実装はfrontend/で行い、**prototype/は静的モックのまま温存**する
- B. prototype/を直接本実装へ昇格させる(その場でAPI接続等を書き足す)
- C. React等のUIフレームワークで再構築する

推奨の根拠:

- **Bの問題** — 04 §2・12 §1.1・03 §10はprototypeを「Vite + vanilla JSの**静的SPA**(API未接続)」として記述する。ここへ本実装を書き込むとdocs記述と実態が乖離し、「実装基準(00 運用ルール6)」の参照物が自己参照する(基準と実装の区別が消える)。T4トラック(常時・プロトタイプ↔docs相互確認)はプロトタイプが独立に存在することを前提とする
- **Cの問題** — 画面は単一(本単位)で、資産(702行のCSS・DOM構造・207行のJS)はフレームワークなしで成立している。フレームワーク導入は資産の移植コストと、docs/learn(初心者向け教材)との整合(コンポーネント・状態ライブラリ等の追加概念)を払ってまで得るものがM1〜M3の規模にはない。M3-10で5画面追加後も、ES modules+小さな状態モジュールで維持できる規模である
- **Aのトレードオフ** — prototype更新時にfrontend/へ反映する手作業が発生する。T4の相互確認手順(確定値22)に「prototype変更時はfrontend/への反映要否を確認する」を1行加える運用で対処する

技術スタック(確定): Vite(prototypeと同じ6系)+ vanilla ES modules + vitest(§4)。新規依存はvitest・happy-dom(試験用)のみで、ランタイム依存はprototypeと同一(@fontsource/*・@phosphor-icons/web・vite)。

### 2.2 API接続とビルド — 推奨: vite dev proxy(`/v1` → `http://127.0.0.1:8000`)

backendにCORS設定がないため、ブラウザから直接fetchすると異算オリジンで失敗する。選択肢:

- **A(推奨). vite.config.mjsのserver.proxyで`/v1`をcompose常設api(127.0.0.1:8000)へ転送する**。開発・実機確認は同一オリジンになり、backend・composeとも無変更
- B. backendへCORSMiddlewareを追加する — main.py変更+許可オリジン管理が発生する。本単位はbackend追加を期限API(§2.6)1点に絞り、CORSのための変更は加えない(変更面の最小化・ws-4並行中の競合回避)
- C. フロントをcomposeへコンテナ追加しsame-origin配信 — 本番配信形態の確定(M4)を前倒しするもので過剰(§1.4)

本番配信はsame-origin(backendまたはCDN)をM4で確定する。**ビルド**: `vite build`が通ることを確認のみ(配信の組み込みはスコープ外)。

### 2.3 認証の扱い — 推奨: 開発用トークンパネル(トークン未設定時のみ表示)

全APIが認証必須(確定値14)だが、認証UI(本物のIdPフロー)はスコープ外(§1.4)。フロントはアクセストークンを保持・送出する必要がある。選択肢:

- **A(推奨)。アクセストークン未所持のときだけ「開発用トークンパネル」を表示する**: IdPトークン文字列を貼る → フロントが`POST /v1/auth/token`を呼ぶ → access_token・refresh_tokenを保存。IdPトークンの発行はbackendの内部CLI(issue-idp-token・確定値15)で行う(人が1回実行)
- B. 認証画面を先行実装する — スコープ外(§1.4)
- C. Authorizationを付けずにAPIを叩く — C3違反で不可

トークン管理の設計(確定値18が未規定のため本設計で固定):

- access_token: メモリ+sessionStorage(1時間・タブ閉じで消える)
- refresh_token: localStorage(30日回転式。再訪時の継続利用)
- APIクライアントは401 UNAUTHENTICATEDで`POST /v1/auth/refresh`を1回試み、成功なら元のリクエストを再送、失敗(refreshも無効)ならトークンパネルへ戻す
- このパネルは本番IdPログインUIへの差し替え経路として設計する(token APIを叩く部分は本番フローと共通)

### 2.4 parse連携 — 推奨: 入力はdebounce(1秒)で自動確定・テキスト変化時のみ送信

parseのトリガーはdocsが確定していない(03 §3のフロー図は「記入↓構造化」)。07 D-17の「入力確定から構造化プレビュー表示まで」を受けて、選択肢:

- **A(推奨)。テキスト変更後1秒のdebounceで自動送信**。同一テキストの再送はしない(直近成功テキストと比較)。送信中(in-flight)にテキストが変わったら古い応答は破棄し最新で再送(AbortController)。空テキスト・変更なしは送信しない
- B. 「条件を読み取る」ボタンを追加 — プロトタイプにないUI要素を増やすことは実装基準(確定値1)の変更を伴う。不採用
- C. blur(フォーカス外れ)で送信 — 「預ける」を押す前にblurする保証がなく、確定前に保存を試みる経路が残る

推奨の根拠: UI構成・文言を一切変えずに03 §3のフロー(記入→自動で構造化表示)が成立するのはAのみ。送信回数は「テキスト確定ごとに1回」に抑え、60req/分(確定値21)とParserコスト(07 §1「1登録1回」)の趣旨に合わせる。

応答の分岐(確定値9・10):

| 応答 | UI動作 |
|---|---|
| 200 | structured_intentを状態へ格納 → 条件リストを再構成(§2.5) → 有効期限の既定を再計算(§2.6) |
| 422 VALIDATION_ERROR(構造化不能) | 条件リストを「全行が手動入力可能な空状態」で表示(=構造化フォームへのフォールバック。確定値9)。raw_textは保持。必須3行に催促表示(§2.7) |
| 503 LLM_UNAVAILABLE | 条件リスト領域にエラー表示+「もう一度読み取る」ボタン(入力テキスト保持・確定値9)。自動再試行はしない(07 §1「再試行なし」のクライアント側踏襲) |
| 429 RATE_LIMITED | 待機を促す表示(ws-4マージ後の最終確認) |
| 401 | §2.3のrefresh再試行 |

### 2.5 条件リストの動的連結 — 推奨: 表示はフォーマッタ純関数、編集はタイプ別の構造化エディタ(=フォームフォールバックと同一UI)

**表示(structured_intent → 行文言)。** 5行のラベル(時間/場所/人数/予算/目的)はプロトタイプ固定(確定値3)。値はフォーマッタ(src/intent/format.js・純関数)がstructured_intentから組み立てる:

| 行 | 値の組み立て(null時) |
|---|---|
| 時間 | time.startをJST表示(当日=「今日」・翌日=「明日」・それ以外=「M月D日(曜)」)+「20:00以降」。time.endがあれば「20:00〜23:00」(指定なし) |
| 場所 | location.name(指定なし) |
| 人数 | min==maxなら「N人」、違えば「2〜4人」(指定なし) |
| 予算 | budget.maxがあれば「ひとり5,000円まで」(指定なし=制約なし) |
| 目的 | category.secondaryがあればsecondary、なければprimaryの日本語ラベル(meal=食事/drinking=飲み/activity=アクティビティ)(指定なし) |

加えて: soft_constraintsは「その他」ラベルの追加行(ph-flagアイコン・プロトタイプの追加行と同型)、ng_unverifiableはNG行(§2.8)。soft行・NG行・「条件を追加」の行は、送信時にsoft_constraints/ng_unverifiable配列へ集約する。追加ラベル(その他・曜日・移動・雰囲気)の格納先がsoft_constraintsであることはD-19補足の確定値どおり(確定値3)。曜日・移動・雰囲気のラベルはUI内の表示用情報であり、API送信値はtextのみ(05 §5のsoft_constraintsはlist[str>)。

**編集(行単位インライン修正=フォームフォールバック兼用)。** プロトタイプの行編集(鉛筆アイコン→input→Enter/✓で確定・Escapeで取消・確定値2)を踏襲しつつ、入力をタイプ別の構造化エディタにする。選択肢:

- **A(推奨)。タイプ別の構造化入力**: 時間=`<input type="datetime-local">`(JST)・場所=nameテキスト+半径(任意・メートル数値)・人数=min/max数値(1〜4)・予算=max数値(円)・目的=primaryのselect+secondaryテキスト・soft/NG行=テキスト。確定時にISO 8601(tz-aware)・数値へ変換して状態へ書き戻す
- B. フリーテキスト編集→再parse — 行ごとにLLMを呼ぶことになり、コスト(07 §1)と非確定性(精度ゲートの趣旨)の両方で不適
- C. フリーテキスト+クライアント側パターン解釈(「20時」「明日」等) — Parserの解釈規則(07 §2)の部分再実装になり、07の出力と食い違う解釈を作り込む

推奨の根拠: 確定値9は「構造化フォームへのフォールバック(必須3フィールド+予算・人数・soft条件の手動入力)」を規定し、かつ「フォームはフォールバック専用で通常経路の主UIにはしない」とする。条件リストの行編集UIがそのまま構造化入力を担えば、**通常経路の修正とフォールバックの手動入力が同一UIで成立する**(03 §3「行単位のインライン修正」と整合)。これにより「フォーム」という別画面を主UIに置かずに済む。HTML標準入力だけで値変換が確定的に行え、解釈の曖昧さが残らない。

### 2.6 有効期限の既定選択計算+disabled化 — 推奨: 補完規則をbackend APIで提供する(設計論点)

補完規則(4選択肢の絶対時刻・既定選択=time.start+3時間最近・過ぎた選択肢の除外)はcompletion.pyが単一実装として持つ(確定値13)。フロントはUI計算のためにこの規則を必要とするが、参照方法が本単位の設計論点である(スーパーバイザー指示)。選択肢:

- **A(推奨)。backendへ軽量APIを追加する**: `GET /v1/intents/expiry-options`(クエリパラメータ`time_start`は任意)。応答は`{"options": [{"label": "今夜 23:30", "expires_at": "2026-09-28T23:30:00+09:00", "selectable": true}, ...], "default_index": 1}`。実装はcompletion.pyのexpires_at_candidates+nearest_expires_atを呼ぶだけで、**新しい計算ロジックを書かない**。time_startが無ければdefault_indexはnull
- B. フロントでJS再実装する — 規則は数行だが「二重実装による不整合を防ぐ」(completion.pyの明文目的・07 §2)に反し、同期を試験で強制し続ける維持コストが残る
- C. parse応答に期限情報を混載する — parse応答は「07のParser出力スキーマと同形」(05 §5の明文)を崩す。不採用

推奨の根拠: completion.pyのdocstringは「消費者は保存経路(ws-3)とUI計算(ws-5)」と記す。つまりws-5(フロント)がこのPythonモジュールの計算を**何らかの経路で消費する**想定である。フロントから消費する唯一の経路がAPIであり、単一実装原則を構造で守る。トレードオフは2点:

1. backendに1エンドポイント追加する(§3.2)。**ws-5実装はws-4マージ後に行うため、並行実装との競合はない**。60req/分の内訳として、正常フローはparse 1+expiry 2(parse直後・確認モーダル開く時)+保存1=4リクエストで収まる
2. 05 §5「その他のエンドポイント」表へ1行の追記が必要(運用ルール7の改版手続き・§6へ引継ぎ)

**フロント側の挙動**: parse成功(time.start確定)時と確認モーダルを開く時にexpiry-optionsを取得する。時間が経過して選択肢が過ぎる場合の再計算も、この再取得で吸収する(確定値7)。selectはlabelを表示し、value=expires_at(絶対時刻)。selectable=falseの選択肢はdisabled。ユーザーが選択した値の絶対時刻をexpires_atとして送る(確定値7)。未選択のままの場合も既定表示値を送る(選択UIの値が常に存在する)。

### 2.7 必須3フィールドの催促と時刻検証の表示 — 推奨: 「預ける」の有効条件に必須3充足を含め、欠落行に催促表示

- **「このIntentを預ける」の有効条件**(確定値5): テキスト非空 かつ category・time.start・locationがすべて確定していること。parse前・422フォールバック直後(未入力)はdisabled。現行プロトタイプの「テキストが空のとき無効化」を拡張する形で、プロトタイプのUI構成は変えない
- **催促表示**: 必須3のいずれかが欠落した行は値を「指定なし」とし、行に催促スタイル(--coral系)を付す。条件リスト直下に注記「時間・場所・目的を指定すると預けられます」を表示する(文言は本設計で固定。03に確定なし)
- **時刻検証の表示**(確定値5・11): フロント側の事前検証は行わず、保存APIの422 VALIDATION_ERROR(過去時刻・+7日超)を時間行のエラーとして表示する。検証の強制はサーバ側(03 §3の明文)。フロントは表示のみ担う
- **「下書き保存」の有効条件**: テキスト非空のみ(draftはraw_textのみ必須・確定値11)

### 2.8 判定不能NG条件のNG行・注意表示

parse応答のng_unverifiable(またはwarnings code=NG_CONDITION_DOWNGRADED)ごとに、条件リストへ**NG行**を追加する(確定値6):

- 行型: `.condition-row`の構造を踏襲し、ラベル「NG」・アイコンはph-warning・data-label="NG"。styles.cssへNG行と注意表示のスタイルを追記する(プロトタイプに行型がないため03 §3が追加を求める箇所)
- 注意表示: NG行の直下にwarnings.messageの文言(「この条件は確実には除外できません。参考条件として扱います」)をmuted表示する。文言はサーバ応答のmessageを使わず、schema.pyのWARNING_MESSAGE_NG_DOWNGRADEDと同一の固定文字列をフロント定数として持つ(確定値16の「messageは参考にしか使わない」踏襲)
- 編集: 文言のインライン編集を許す(soft行と同一のテキスト編集)。削除UIは本単位では付けない(プロトタイプに行削除UIがないため・基準維持)
- 送信: ng_unverifiable配列へ(05 §5。保存時にdowngraded_from_ngフラグ付きsoft_constraintsへ格納されるのはサーバ側)

### 2.9 保存API接続(active/draft)とエラー表示 — 推奨: 確認モーダルの「Intentを確認する」でPOST・エラーはcodeで分岐して行単位/グローバルに表示

**保存フロー(確定値2・11):**

- 「このIntentを預ける」(⌘Enter)→ 必須3充足なら確認モーダルを開く(サマリ=選択中の有効期限label+公開設定。プロトタイプ実装どおり)→「Intentを確認する」→ `POST /v1/intents` {raw_text, status: "active", structured_intent: 全量} → 201 → モーダルを閉じ同一画面に留まる(入力・条件リストは残す=プロトタイプ挙動の維持)
- 「下書き保存」→ `POST /v1/intents` {raw_text, status: "draft", structured_intent: 現在の部分値} → 201 → トースト「下書きを保存しました」(2.4秒)
- structured_intentの送信内容は行編集結果を反映(§2.5の組立)。alcohol_involvedはparse由来の値をそのまま送る(編集UIなし・保存時にサーバがdrinkingならtrue確定)。negative_constraintsは常に空配列(FR-42)。visibility・notification_level・expires_atは預け方パネルの選択値

**エラー表示マップ(envelopeのcodeで分岐・確定値16・17):**

| code | 表示位置 | 内容 |
|---|---|---|
| VALIDATION_ERROR(時刻系) | 時間行 | 過去時刻・+7日超を修正促進(§2.7) |
| GEOCODING_FAILED | 場所行 | 場所の修正を促す(確定値11「条件リストへ戻して修正を促す」) |
| VALIDATION_ERROR(必須系) | 該当行 | 催促表示へ(通常はフロント側で事前ブロック済み=到達稀) |
| UNDER_AGE | グローバル | 飲酒を含むIntentは20歳未満作成不可の案内 |
| ACTIVE_INTENT_LIMIT | グローバル | Active 5件上限の案内(停止・期限切れの案内) |
| RATE_LIMITED(429) | グローバル | 時間をおいて再操作を促す(ws-4マージ後の最終確認) |
| LLM_UNAVAILABLE・DEPENDENCY_UNAVAILABLE(503) | グローバル | 再試行ボタン(parse)/再操作促進(保存) |

保存リクエスト中はボタンをdisabledにし二重送信を防ぐ。

## 3. ファイル構成

### 3.1 新規に作るもの(frontend/)

```
frontend/
  package.json          # vite・vitest・@fontsource/*・@phosphor-icons/web(prototypeと同一+試験依存)
  vite.config.mjs       # dev proxy: "/v1" → http://127.0.0.1:8000・test環境設定
  index.html            # prototypeからコピー+開発用トークンパネル・NG行テンプレート等の構造追加
  styles.css            # prototypeからコピー+NG行・催促/エラー状態・トークンパネルのスタイル追記
  public/assets/textures/  # prototypeからコピー
  src/
    main.js             # エントリ: DOM配線・モジュール結合(prototype app.jsのui部分を移設)
    ui/chrome.js        # テーマ・popover・モーダル・トースト(prototype app.jsから分離)
    api/client.js       # request(): 認証ヘッダー・envelope解釈・401→refresh→1回再試行
    api/session.js      # トークン管理(access: メモリ+sessionStorage / refresh: localStorage)・トークンパネル
    intent/state.js     # フォーム状態(テキスト・structured_intent編集値・期限選択・検証状態)
    intent/parseFlow.js # debounce 1秒・同一text抑制・in-flight abort・応答分岐(§2.4)
    intent/conditions.js# 条件リストDOM構成・タイプ別エディタ・催促表示(§2.5・§2.7・§2.8)
    intent/format.js    # structured→表示文言・datetime-local→tz-aware ISO・APIボディ組立(純関数)
    intent/expiry.js    # expiry-options取得・select構成(label/disabled/既定)(§2.6)
    intent/save.js      # active/draft保存・エラー表示マップ(§2.9)
  tests/
    format.test.js      # 表示文言・JST変換・ボディ組立
    parseFlow.test.js   # debounce・abort・応答分岐(fake timers・fetchモック)
    expiry.test.js      # モック応答からのselect構成・時間経過の扱い
    save.test.js        # 201/422/429/503分岐・ボタン状態
    state.test.js       # 預ける有効条件・催促状態
```

### 3.2 backend側の追加(ws-5実装時・ws-4マージ後に実施)

| ファイル | 変更内容 |
|---|---|
| intents/routes.py | GET /v1/intents/expiry-optionsを追加(completion.pyの関数を呼ぶだけ・計算ロジックなし) |
| backend/tests/integration/ | expiry-optionsのintegration 1件(認証・time_startあり/なしの応答形式) |
| docs/05-data-model-api.md | 「その他のエンドポイント」表へ1行追記(運用ルール7の改版手続き。実装と同時に行う) |

### 3.3 触らないもの

- **prototype/** — 温存(コピー元・実装基準の参照物のまま)
- **backend/** — §3.2の追加以外は無変更(main.pyへのCORS追加等はしない)。ws-4並行中のため尤其注意
- **compose.yaml / Makefile** — 無変更(frontendの起動手順はfrontend/READMEに記す。Makefileターゲットの追加は計画書で提案するが、repo rootの変更はスーパーバイザー判断)
- **docs/learn/** — agent4管轄(マージ後に同期)
- **docs/ 仕様書群** — §3.2の05追記以外なし

## 4. テスト方針

### 4.1 unit(vitest・happy-dom・fetchモック。毎コミット)

- **format**: structured→文言の境界(当日/翌日/日跨ぎ・min==max・null「指定なし」)・datetime-local→tz-aware ISO(JST)・APIボディ組立(soft/ng配列の集約・visibility等の既定)
- **parseFlow**: debounce発火(1秒後・以降の再入で延長)・同一text再送なし・in-flight中の再入で古い応答破棄・422/503/429/401のUI状態遷移
- **expiry**: モック応答からのselect構成(label・value=絶対時刻・disabled)・default_indexの反映
- **save**: active/draftのボディ・201後のUI(トースト・モーダル)・エラーcode→表示位置のマップ・二重送信防止
- **state**: 「預ける」有効条件(必須3充足)・催促表示のON/OFF
- 規律: テストコード・consoleにサンプル意図文言(raw_text相当)を crews 出力しない(確定値19)。テスト入力はdocsの例文(05 §5・03 §3に掲載済みの天文館の文)を使う

### 4.2 結合確認(実機・手順を定式化して報告書に残す)

compose常設api(vite proxy経由)を対象に、実装エージェントが手動で確認し、手順と結果を報告書に記録する。**ws-4マージ後**に行う(DBへ行を書くため・§1.3):

1. トークン取得: `uv run python -m latch.auth issue-idp-token` → トークンパネルへ貼付(access_token取得)
2. parse連携: テキスト入力→1秒後に条件リストが構造化結果で再構成されること(§2.5の文言)
3. 行編集: 時間をdatetime-localで変更→表示更新→「預ける」が有効
4. 期限: 時刻に応じた既定選択・過ぎた選択肢のdisabled(expiry-options応答)
5. NG行: 会社関係者の除外を含むテキスト→NG行+注意表示
6. 保存: 「預ける」→モーダル→「Intentを確認する」→201・同一画面留保。「下書き保存」→201・トースト
7. エラー: 場所を存在しない地名へ編集→422 GEOCODING_FAILEDの場所行表示。429はws-4マージ後(apiイメージ再ビルド後)
8. 保存結果のDB確認・topbar「Intent 3件」の実数化はスーパーバイザー検証として残す(一覧UIがスコープ外のため)

### 4.3 品質・規律

- フロント試験はDB/Redisを消費しないため、STATUS運用ルール1〜3(test-ci同時実行制約)に抵触しない
- `npm run build`(vite build)の成功を確認する
- アクセシビリティ: プロトタイプの規律(aria-expanded・role=dialog・role=status・:focus-visible・Escape)を維持したまま拡張する(03 §10デザインシステム)

## 5. 未解決の論点(設計判断の告白 — レビューで確認を求める)

すべてdocsの根拠から判断したが、字義に複数の読みがあり得るため、承認時に確認してほしい:

1. **02#1〜#4のci検証はbackend integration試験が本体で、フロントE2E(Playwright等)はM1で導入しない**(§4)。02#3「ユーザーが解析内容を修正できる」の検証方法「プレビューでフィールドを変更して保存する」を「API全置換契約の検験(ws-3のtest_intents_crud_api)+フロントUIロジック試験(vitest)+実機結合確認(§4.2)」の組合せで満たす解釈である。UI経由の自動化が必要という判断であればPlaywright導入(ci時間・ブラウザ環境のコスト)が別途発生する
2. **補完規則をbackend APIで提供する(GET /v1/intents/expiry-options)**(§2.6)。completion.pyの単一実装原則(二重実装防止の明文)を優先し、05 §5へのエンドポイント追記(運用ルール7の改版手続き)を伴う判断とした。JS再実装はdocs改版不要という利益があり、表示初期値のズレが保存値を汚さない設計(null送信則)も理論上可能だが、「二重実装を防ぐ」の明文と正面から衝突するため不採用とした
3. **開発用トークンパネルをfrontendに含める**(§2.3)。認証UIはM1-7・03 §2画面一覧にないため、本番IdPフローの先行実装とはしない。トークン保存場所(access=sessionStorage+メモリ・refresh=localStorage)はdocsが未確定(確定値18)のため本設計で固定した。セキュリティ上の懸念があれば指摘を乞う
4. **parseトリガーをdebounce 1秒(自動)とした**(§2.4)。07 D-17の「入力確定」の解釈であり、UI文言を追加しない方法を選んだ。debounce時間(1秒)にdocs上の根拠はない
5. **目的行の表示値とnull行の「指定なし」等、プロトタイプが静的文字列だった箇所の動的生成ルールを本設計で固定した**(§2.5の表)。ラベル・行構成・レイアウトはプロトタイプ基準を維持しており、文言対応表の水準で03 §10の対応管理に載せられればと考える(要否はT4トラックの運用判断)
6. **frontend/を新設しprototype/を温存する**(§2.1)。prototype変更時にfrontend/へ反映を確認する運用をT4へ1行加える。prototype/を昇格させる選択が望ましいという判断であれば、docs(04 §2・12 §1.1・03 §10)の記述変更を伴う

## 6. 次工程への引継ぎ

- 計画書(ws-5-plan.md)は§2〜§4をタスクへ分解する。実装順の推奨: frontend/雛形(コピー+vitest基盤)→ api/session・client(§2.3・トークンパネル)→ parseFlow→ conditions(表示・編集・催促)→ NG行→ expiry(§2.6)→ save→ 結合確認(§4.2)
- **着手条件: ws-4マージ後**(backend追加§3.2と429最終確認のため)。計画書の§0に明記する
- backend追加(expiry-options)と05 §5追記は実装タスクに含め、reportに「05改版実施済み」を記録する
- frontend/README(起動手順: npm install→npm run dev→トークン発行手順への参照)を雛形タスクに含める
