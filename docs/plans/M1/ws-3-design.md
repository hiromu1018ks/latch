# M1 ws-3(intents CRUD・draft→active・保存時検証)設計メモ

- 作業単位: ws-3 — intents CRUD: POST(active/draft)・GET・PATCH・DELETE・pause/resume・draft→active遷移(全検証通過後に受理・初回MatchEvent発行)。ジオコーディング正転の保存組み込み・alcohol_involvedのサーバ側確定・時刻検証(過去不可・+7日上限・active時のみ)(docs/plans/STATUS.md M1表 / 出典 12 M1-4・M1-5 / 05 §5〜§6 / 依存: ws-1・ws-2=マージ済み)
- 作成: 2026-09-27(agent1 / superpowers:brainstorming使用)
- 次工程: 計画書(ws-3-plan.md)へ変換 → 実装エージェントがworktree内でTDD実装
- 並走: なし(実行waveは ws-3 → ws-4 → ws-5 の直列)。agent4のdocs/learn/更新には触れない。**マイグレーションを追加しない**(§2.9)のため、STATUS運用ルール1〜3(test-ci同時実行制約)には抵触しない

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M1スコープ4〜5(12 M1-4・M1-5)を実装する。parse(ws-2)が返した構造化データと補完規則(ws-2 completion.py)を**消費**してIntentを行として保存し、CRUD・pause/resume・draft→active遷移を通す。保存経路に特有の3処理 — ジオコーディング正転の保存組み込み(geo_center確定)、alcohol_involvedのサーバ側確定、時刻検証(過去不可・現在+7日上限・active時のみ)— を実装する。active作成確定・active化・active更新・resumeの各タイミングでmatch_eventsへEventを発行する(§2.2。M2のPub/Sub本格実装と重複を作らない)。

保存APIは同期LLM非依存(C8: DB書き込み+Event発行に絞る)。Embeddingはイベント駆動(M2のEmbedding Worker)であり、本単位ではembedding列はNULLのままにする。02#1(下書き経路含む)〜#4のうち保存側(入力→保存・下書き→再開→active化・修正の反映・有効期限の保存)が本単位の対象で、G1判定はws-5まで揃った時点で行う(§6-2)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | POST /v1/intents契約: request `{"raw_text", "status", "structured_intent"}` → 201 `{"intent": {"id", "status", "version", "expires_at", …}}`。statusは`active`(既定・省略可)または`draft` | 05 §5 |
| 2 | **activeで作成する場合**: 確認フロー(03 §3)を経たデータのみを受け付ける。作成確定の直後にMatch Eventを発行する | 05 §5・06 §9 |
| 3 | **draftで作成する場合**: raw_textのみ必須(最大300字)。structured_intentの内容は任意(部分的な中途データでも保存する)。検証は「raw_textの必須(最大300字)」に限定し、必須3フィールド・時刻検証・ジオコーディング・年齢検証はactive化時(PATCH)まで適用しない。**指定値の形式不正のみ400 MALFORMED_REQUESTで返す**。draftはEmbeddingを行わずLayer 1〜5の対象外・Match Eventを発行しない。visibilityの既定値(hidden_until_match)はdraftでも格納する | 05 §5・06 §9-0・07 §1 |
| 4 | `expires_at`はstructured_intent内のフィールド。クライアントは選択値の絶対時刻を送る。nullの場合はサーバ側で「time_start+3時間に最も近い有効期限選択肢」(03 §3の4値・過ぎた選択肢を除く)と同時刻を補完する。draftでは補完を行わずNULLのまま保存する | 05 §5・03 §3 |
| 5 | **locationの受け方**: リクエストのlocationは`{"name", "radius_m"}`のみで座標は受けない。API保存処理内でlocation.nameをジオコーディング(04 §3の正転・セルフホスト地物データ)し、intents.geo_centerを確定する。該当地物が存在しない場合は422 GEOCODING_FAILED。PATCHでlocationを含む変更の場合も同様。**この検証はstatus=activeの作成・更新に適用する(draftでは適用しない。draftのgeo_centerはNULL可)** | 05 §5・04 §3 |
| 6 | **structured_intentは変更後の全量を送る(全置換)。raw_textも同時に送り直す。PATCHとPOSTは同一契約**であり、Intentはversion単位の条件スナップショットとして扱う | 05 §5 |
| 7 | **時刻検証**: time_startが過去(呼び出し時点より前)の場合、および現在+7日を超える場合は422 VALIDATION_ERROR。expires_atも同様に過去・現在+7日超は422。**draftでは指定値が存在する場合のみ時刻の形式検証(ISO 8601)を行い、過去時刻・上限超過の検証はactive化時まで適用しない** | 05 §5 |
| 8 | **その他の422**: 必須欠落(category / time.start / location、03 D-19。activeのみ)、alcohol_involved=trueかつ作成者が20歳未満(birth_dateから判定、422 UNDER_AGE)。visibilityは欠落時に既定値hidden_until_matchを格納し422の対象としない | 05 §5 |
| 9 | **alcohol_involvedの確定**: 保存時にcategory_primary=drinkingであればサーバ側でalcohol_involved=trueを確定する(クライアント修正値より優先する。07規則7)。drinking以外のカテゴリではリクエスト値をそのまま格納する | 05 §5・07 §2規則7・08 D-10 |
| 10 | **保存時に07出力由来の値を05 §2の構造へ格納する**(ng_unverifiableの各要素は`downgraded_from_ng: true`のsoft_constraintsへ、他は独立カラムへ)。structured_dataが保持するキーはcategory_secondary / soft_constraints / negative_constraints / time_flexibility_minutes / location_flexibilityの5つ。negative_constraintsはMVPで常に空配列(FR-42) | 05 §5・§2・07 §2 |
| 11 | draft→active遷移(PATCH): statusをactiveに変更するPATCHは**active作成と同一の全検証(必須3フィールド・時刻検証・ジオコーディング・年齢検証)を通過して初めて受理**し、検証不通なら422でstatusはdraftのまま据え置く。受理時にgeo_center・embeddingを確定し、初回のMatch Eventを発行する。**条件内容の実質変更を伴わないactive化はversionを据え置く(全置換後のstructured_dataが同一の場合)**。active→draftへの逆遷移は不可(打切りはcancel) | 05 §5・§6 |
| 12 | draft中のPATCH(下書き内容の更新・再保存)では、raw_textの必須(最大300字)以外の検証・ジオコーディング・Embedding・Match Event発行を行わない(下書き保存と同一の扱い) | 05 §5・06 §9-0 |
| 13 | PATCH(全置換): version+1・Event発行。検証はPOSTと同一(時刻検証・ジオコーディング・年齢検証を含む)。構造データの変更でalcohol_involved=trueとなる場合(category変更を含む)は作成者の年齢検証を行い、20歳未満なら422 UNDER_AGE | 05 §5 |
| 14 | **draft→activeの初回投入Eventは作成種(create)を用いる**(active化時に条件内容の実質変更を伴いversionが+1となる場合でも作成種のまま。draft保存時にEventを発行していないためidempotencyキー(create, source_intent_id, version)は常に空いている。debounceの窓統合の影響も受けない) | 06 §9-0 |
| 15 | **resumeはversion+1の再評価Event(update種)を発行する**(idempotencyキーが同一versionの再発行を許さないため。pause→resumeの繰り返しで必ず衝突する)。Embeddingはテキスト不変のため再実行しない | 05 §6・06 §9起点補償 |
| 16 | match_events: event_typeは5種(作成・更新・削除・期限切れ・指定時刻)+派生1種(embedding_completed)。UNIQUE(event_type, source_intent_id, payload内version)=冪等キーをDBレベル保証。statusはpending/processed/quarantined。削除済みIntent参照Eventは正当な遅延Eventとしてprocessedで破棄 | 05 §2・01 §5・06 §9 |
| 17 | Intent遷移表: (作成)→active/draft・draft→active/expired/cancelled・active→paused・paused→active(resume)・active・paused→cancelled/expired・active→matched・matched→active/expired(復帰)。expiry_sweeperはM3実装(12 M3-3)であり本単位では作らない | 05 §6 |
| 18 | GET /v1/intents: 自Intent一覧。statusフィルタ可(`?status=draft`等。フィルタなしは全status)。ページネーション共通規定を適用。既定ソートはcreated_at降順。下書き一覧とActive Intent一覧の表示に使う | 05 §5 |
| 19 | GET /v1/intents/{id}: Intent取得。所有者のみ | 05 §5 |
| 20 | DELETE /v1/intents/{id}: 削除(01 §21の削除範囲を適用)。所有者のみ。遷移表上はdraft→cancelled・active・paused→cancelled(DELETE・ユーザーの打切り) | 05 §5・§6・01 §21 |
| 21 | pause/resume: 停止・再開。所有者のみ | 05 §5 |
| 22 | ページネーション共通規定: `?cursor=<opaque cursor>&limit=<1〜100>`(limit既定20・超過は422)。応答は`{"items": [...], "next_cursor": "..."}`(次ページがなければnull)。cursorはサーバ生成の不透明文字列 | 05 §5 |
| 23 | エラーcode(本単位に関係): 400 MALFORMED_REQUEST・401 UNAUTHENTICATED・403 FORBIDDEN(認可違反: 所有者以外の操作)・404 NOT_FOUND・422 VALIDATION_ERROR(必須欠落・値域外・過去時刻・7日超過等)・422 UNDER_AGE(20歳未満の飲酒Intent作成)・422 GEOCODING_FAILED・503 DEPENDENCY_UNAVAILABLE | 05 §5エラー形式表 |
| 24 | **422 ACTIVE_INTENT_LIMIT(Active 5件)と429 RATE_LIMITED(作成20件/日・更新6回/時・API 60req/分)はレート制限単位(ws-4)が実装する**。本単位では実装しない | 12 M1-6・08 §5.4・STATUS M1表 |
| 25 | 時刻参照はすべてClock経由(arch test `test_arch_no_direct_time` が毎コミットで強制)。期限判定・時刻検証の比較基準はClock.now()(tz-aware UTC)。JST暦日付はclock.jst_date() | 12 C2・04 §5(FR-41) |
| 26 | ログ・計測・例外にIntent原文(raw_text)・正確な位置・NG条件を含めない。許可リスト方式。例外メッセージはIDとエラーコードで表現する | 08 §2.4・01 §21 |
| 27 | 満年齢の判定: (月, 日)タプル比較で誕生日当日に加算(実装済み users/service.age_years。本単位は20歳線に再利用) | ws-1実装・08 D-10 |
| 28 | 正転ジオコーディング資産: `GeoService.geocode_forward(name) -> Geofeature | None`(normalize→完全一致・osm_poi優先→id昇順の決定的順位で1件)。該当なし=None。外部送信経路なし | geo/service.py(M0 ws-4) |
| 29 | D-19補完規則の単一実装(実装済み): default_time_end(+3h)・DEFAULT_RADIUS_M=1000・DEFAULT_PARTICIPANTS=(2,2)・nearest_expires_at(time_start, now)。**消費は本単位とws-5** | intents/completion.py・07 §2 |
| 30 | **保存APIは同期LLM非依存(DB書き込み+Event発行に絞る)**。同期LLMはparse APIのみ | 12 C8・04 §7 |
| 31 | Intent保存 p95 500ms(性能目標。保存はDB書き込み+ジオコーディングSQLのみで構成される) | 04 §7・01 §20 |
| 32 | スキーマ資産: intents表(全列)・match_events表・Index一式・idempotencyの式UNIQUE索引 `ux_match_events_idempotency(event_type, source_intent_id, (payload->>'version'))` はalembic 0001で作成済み。**欠けている列・表・索引はない** | alembic 0001・05 §2〜§3 |
| 33 | 全API認証済みユーザーのみ。Intentの参照・更新・削除は所有者本人のみに許可し、サーバ側で強制する。認証は `require_authenticated`(C3の強制点。claimsはauth_provider/auth_subject) | 05 §5冒頭・01 §22・auth/deps.py |
| 34 | **(設計発見)location.nameの格納先が05 §2に存在しない** — intents表の独立カラムはgeo_center/geo_radius_m(座標系)のみで、structured_dataの保持キー5つにも含まれない。一方でEmbedding正規化テキスト(07 §3)と条件リスト表示(03 §3「場所: 天文館」)はlocation.nameを必要とする。本設計はstructured_dataへ`location_name`キーを追加して格納する(JSONBのためマイグレーション不要。§6-1) | 05 §2・07 §3・03 §3 |
| 35 | 有効期限4選択肢の絶対時刻: 今夜23:30=当日JST 23:30 / 明日12:00・明日23:30=翌日JST / 3日後まで=now+72h(実装済み completion.py) | 03 §3 |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

- **intents/(ws-2)**: completion.py(補完規則。本単位が消費・無変更)・schema.py(ParserOutput。parse専用のまま触らない)・prompt.py・service.py(IntentParseService)・routes.py(parse_router。本単位は同ファイルへCRUDエンドポイントを追記)・errors.py(IntentsError階層。本単位が例外を追加)
- **geo/(M0 ws-4)**: GeoService.geocode_forwardを§2.5のProtocol経由で注入(無変更)
- **auth/**: require_authenticated・make_user_lookup(無変更。本単位のユーザー行参照は§2.6の独自SELECT)
- **users/(ws-1)**: age_years(満年齢。importして再利用)・text()生SQL+Callable注入の流儀
- **core/**: Clock(core/clock.py)・create_db_engine・get_clock(無変更)
- **alembic 0001**: スキーマ完全(確定値32)。マイグレーション追加なし

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

- **レート制限(422 ACTIVE_INTENT_LIMIT・429 RATE_LIMITED)** — ws-4(12 M1-6)。Active 5件の検査も含む
- **expiry_sweeper・Intent期限切れバッチ(draft→expired・active→expired)** — M3-3(12 M3-3)。idx_intents_expires(0001)は既存
- **Pub/Subパブリッシュ・Worker第1段(debounce・version検査・再試行→quarantined)・Embedding Worker・Layer 1〜5** — M2(12 M2-1〜8)。本単位のEvent発行はmatch_eventsへのINSERTまで(§2.2)
- **削除範囲の完全実装(match_candidates・group_candidates削除・30日定期削除・latchesのcancelledクローズ)** — M3-8(08 §2.5)。本単位のDELETEはcancelled遷移+deleted Event(§2.8・§6-3)
- **フロントエンド(条件リスト・下書き一覧・保存API接続)** — ws-5(12 M1-7)
- **ORM・リポジトリ層の本格導入** — §2.9で生SQL継続を判断(将来の再判断はM2以降の状態で)

## 2. 実装方式の選択肢と推奨

### 2.1 パッケージ構成 — 推奨: intents/パッケージを責務別ファイルで拡張

選択肢:

- **A(推奨). intents/パッケージへ新規4ファイル(intent_input.py・mapping.py・events.py・store.py)を追加し、service.pyへIntentService・routes.pyへCRUDエンドポイントを追記する**
- B. service.pyへCRUDユースケースを全て追記する(1ファイルへ集中)
- C. 新パッケージ(latch/intents_crud/等)を切り、parse(ws-2)と分離する

推奨の根拠: 本単位の責務は(1)リクエスト契約の検証、(2)入力→保存列への変換(alcohol確定・補完消費・structured_data構築)、(3)Event発行、(4)永続化(SQL)、(5)ユースケース(検証順序・遷移判断)に分かれ、Bではservice.pyが600行超に肥大して責務境界面が消える(ファイルが大きくなるのはやりすぎのサイン)。Cは同じドメイン(intents)を2パッケージに割り、ws-2設計§2.1が置いた「ws-3は同じintentsドメインを拡張する」という構成判断とSTATUS依存表(ws-3はws-2に依存)に反する。Aはusers(ws-1)と対称な「ドメインパッケージ内の責務別ファイル」であり、parse資産(schema.py・completion.py)との重複なし・境界明確。

トレードオフ: ファイル数が増える(intents/は9ファイル)。ただし各ファイルは単一責務で200行前後に収まり、unit試験もファイル対応になる。

### 2.2 MatchEvent発行の機構 — 推奨: match_eventsをoutboxとする同一トランザクションINSERT(M2との境界)

06 §9は「Intent API → DB保存 → Match Event発行(payloadにIntent version) → Pub/Sub → Matching Worker」という経路を規定し、M2-1が「Event発行とPub/Sub連携」を実装する。05 §5は「作成確定の直後にMatch Eventを発行する」。M1時点ではPub/SubもWorkerも存在しないため、発行の実体をどこまで作るかが本設計の主要判断である。選択肢:

- **A(推奨). match_eventsテーブルをoutboxとして使う。Intent保存とEvent INSERTを同一トランザクションでコミットする(status='pending', payload={"version": N})。Pub/SubパブリッシュはM2-1がpending行をリレーする形で追加する**
- B. 保存コミット後に別トランザクションでEvent INSERTする
- C. インプロセスのイベントディスパッチャ(メモリキュー・コールバック)を用意し、M2でPub/Subへ差し替える

推奨の根拠: Aは(1)原子性 — 保存が成功した行に必ずEvent行が伴い、Eventだけが残って参照先不在となる経路を構造的に作らない(06 §9の「削除済みIntent参照Event」は正当な遅延Eventとして破棄すると05 §2が規定するため、残っても事故にはならないが、設計として漏れを閉じる)。(2)M2との重複ゼロ — M2-1の「Event発行とPub/Sub連携」はoutboxリレー(またはAPIプロセス内パブリッシュ)をこのpending行の上に追加するだけであり、本単位が作るものと役割が重ならない。match_eventsのUNIQUE式索引(0001)・status列(pending/processed/quarantined)・payload冪等キーは、まさにこのoutbox消費を前提にM0 ws-1が作った資産である。(3)C8「DB書き込み+Event発行に絞る」の字義への最短の合致。Cのメモリキューはワーカー不在のM1では受取先がなく、M2で全体が廃棄される投資になる。Bは保存成功・Event欠落の穴を残し、その回収制御をM1で作ることになる(過剰)。

Eventの発行タイミング一式(確定値2・11・13・14・15からの導出):

| 操作 | Event | version | 備考 |
|---|---|---|---|
| POST active作成 | created | 1 | INSERTと同一txn |
| POST draft作成 | (なし) | 1 | 06 §9-0 |
| PATCH draft再保存 | (なし) | +1 | 05 §2「更新ごとに+1」。Eventなしなのでキー衝突の懸念なし(§2.7) |
| PATCH draft→active | **created** | 据え置き or +1(§2.7) | 初回投入は作成種(06 §9-0) |
| PATCH active/paused内容更新 | updated | +1 | |
| pause | (なし) | 不変 | 遷移表に発行規定なし(pausedはLayer 1対象外のため評価から外れる) |
| resume | **updated** | +1 | 06 §9起点補償 |
| DELETE | deleted | 不変 | cancelled遷移と同一txn。1回しか起こらないためキー衝突なし |

event_typeの文字列値はdocsに確定値がない(01 §5は「作成・更新・削除・期限切れ・指定時刻」、06 §9は「作成種(create)」と説明するのみ)。本設計は**created / updated / deleted / expired / scheduled**(ws-3は先頭3種を実装・後続2種は定数のみ定義)+ embedding_completed(05 §2に唯一文字列が確定している値)に固定する。根拠は、確定値が存在するembedding_completedが過去分詞形であるため、語形を揃えると列内で一貫する。文字列はDBに永続するため、この確定はM2設計が引き継ぐ(§6-4)。

payloadは`{"version": N}`のみ(M1の最小。WorkerはIntentを再読込して使う設計であり、06 §9もpayloadに求めるのはversion)。created_atはClock由来の明示値(時刻列にDB時刻関数を使わない規律)。

### 2.3 リクエスト検証とドメイン検証の分離 — 推奨: Pydantic(形状・値域)+サービス層(Clock/DB依存検証)

選択肢:

- **A(推奨). intent_input.pyのPydanticモデルが形状・値域(300字・Literal・tz-aware・participants 1〜4)を検証し、サービス層が必須3フィールド・時刻検証(過去/7日)・年齢検証・ジオコーディング(422化)を行う**
- B. 全検証をPydanticバリデータへ集約する
- C. 全検証をサービス層の手検証にする

推奨の根拠: 時刻検証はClock.now()との比較・年齢検証はbirth_date(JST暦日付)参照・ジオコーディングは地物テーブル参照であり、いずれも検証時に外部依存を呼ぶ。この3つをB(全検証をPydanticへ集約)でやるとモデルが状態を持ちはじめ、unit試験がファクトリ・注入だらけになる。Cは値域検証(422 VALIDATION_ERRORのenvelope出力も含む)を二重実装する。Aは「静的に決まるものは宣言的に、時刻・データ依存のものはユースケースで」の線で、422の発生元が一意に定まる。draftの「形式検証のみ」(確定値3)はAの構成だとPydantic通過=形式検証完了となり、サービス層の検証をactive経路だけで呼ぶ切り替えが1箇所で済む。

active/draftの契約の違い(必須・検証・補完・ジオコーディング・Event)の対応表:

| 項目 | active作成・更新・active化 | draft作成・再保存 |
|---|---|---|
| raw_text | 必須・1〜300字 | 必須・1〜300字(唯一の検証) |
| structured_intent | 必須(全量) | 任意(部分可) |
| 必須3フィールド | 検証する(422) | 検証しない |
| 時刻(過去・+7日) | 検証する(422) | しない(形式のみPydantic) |
| 年齢(alcohol) | 検証する(422 UNDER_AGE) | しない |
| ジオコーディング | 実行(422 GEOCODING_FAILED) | しない(geo_center=NULL) |
| 補完(time_end・participants・radius・expires_at) | 適用 | しない(NOT NULL列のみ既定値) |
| Embedding | NULLのまま(M2 Worker) | NULL(そもそも対象外) |
| MatchEvent | 発行(表中の種別) | なし |

### 2.4 draft部分保存の表現 — 推奨: 全フィールドOptionalの1モデル(1契約1モデル)

選択肢:

- **A(推奨). StructuredIntentInput(全フィールドOptional)を1つだけ定義し、active/draft両経路で使う。必須3はactive経路のサービス層検証とする**
- B. active用(必須込み)とdraft用(全Optional)の2モデルを定義する

推奨の根拠: 05 §5はリクエスト契約を1つ(全置換・同一契約)と規定しており、違いは「検証の適用有無」であって「スキーマの違い」ではない。Bは同一JSON形状に2つのモデルが並び、クライアント・学習資産の両方で混乱の元になる。Aは「形式は常に検証・意味はactive時に検証」という§2.3の線がそのままモデル構成に現れる。

NOT NULL列(draftで未指定になり得るもの)の扱い — 比較可能性も含めて次のとおり固定する:

| 列 | draft未指定時の格納値 | 根拠 |
|---|---|---|
| category_primary | `''`(空文字) | NOT NULL(0001)。draftはcategory未指定可(確定値3)。active化時はLiteral検査で必ず弾かれる |
| alcohol_involved | false | NOT NULL。active化時に07規則7で確定し直す |
| participants_min/max | (2, 2) | NOT NULL DEFAULT 2。アプリ層の明示格納(ws-1の「DB DEFAULTに頼らない」規律と同型) |
| visibility | hidden_until_match | 既定値はdraftでも格納(確定値3) |
| notification_level | proposals_only | NOT NULL。対称的に既定格納 |
| time_start / time_end / expires_at / geo_center / geo_radius_m / budget_max | NULL可 | 05 §2(draftではNULL可) |

### 2.5 ジオコーディング統合 — 推奨: 構造的Protocol注入(GeoServiceは無変更)

選択肢:

- **A(推奨). intents/service.pyに`SupportsForwardGeocoding` Protocol(`async def geocode_forward(name: str) -> Geofeature | None`)を定義し、GeoServiceを構造的適合で注入する(型参照のための`latch.geo` importはservice.pyとファクトリに限る)**
- B. GeoServiceの具象クラスを注入する
- C. 正転SQLをintents内に複製する

推奨の根拠: ws-2設計§2.2のSupportsParseIntentと同型であり、unit試験でスタブジオコーダ(固定座標/None)を差し替えるだけで422 GEOCODING_FAILED系が決定的に試験できる。Cは06 §9で守るべき単一実装の崩壊・Bはgeo/の変更がintentsへ型として波及する。intents→geoのimportは許容する(geoは外部送信経路を持たない内部基盤であり、ws-2が避けたのはllm/への依存だけである。なおusers/service.pyもauth.tokensを型としてimportする先例がある)。

実行順序: ジオコーディングは保存トランザクションの**前**に実行する(検証の最後。形式→必須→時刻→年齢→ジオコーディングの順)。地物参照はread-onlyなのでトランザクション外でよく、失敗(該当なし)なら422 GEOCODING_FAILEDで保存まで到達しない(確定値5)。結果の座標(lon/lat)を保存列へ渡し、INSERTで`ST_SetSRID(ST_MakePoint(:lon,:lat),4326)`のgeographyへ入れる。同一location.nameの再ジオコーディングは決定的(順位固定)のため冪等で、PATCH全置換で毎回実行してよい。

### 2.6 alcohol_involvedの確定・年齢検証・ユーザー行参照 — 推奨: intents内のユーザー行SELECT(id+birth_date)

- **alcohol確定**: mapping.pyの変換内で実装(category_primary=drinkingならリクエスト値に優先してtrue・他はリクエスト値。未指定ならdrinking→true・他→false。確定値9)
- **年齢検証**: alcohol_involved=true(確定後の値)のとき、作成者のbirth_dateから`age_years(birth_date, clock.jst_date()) < 20`で422 UNDER_AGE(08 D-10)。PATCHでalcohol=trueとなる場合も同一検証(確定値13)。age_yearsはusers/service.pyからimportして再利用(満年齢の単一実装。2月29日の扱いも含めて重複実装しない)
- **ユーザー行参照**: authの`make_user_lookup`はidのみを返す契約(authが使用中)のため拡張しない。`IntentStore.fetch_user_row(provider, subject) -> UserRow(id, birth_date) | None`(uq_users_auth_provider_subject索引へのSELECT 1本)をintents/store.pyへ置き、IntentServiceはstore経由で呼ぶ(§2.10のIF)。User行なし(未登録JWT)=404 NOT_FOUND(GET /v1/users/meの先例と同一挙動。初回登録へ誘導する合図)

選択肢(B: users/service.pyへbirth_date付きlookupを追加する)はusersパッケージへの差分を生む。スーパーバイザー指示「intentsパッケージを拡張する構成を基本とせよ」に従い、users表の参照SQL 1本をintents側に置くAを採る(参照先テーブルが別ドメインでもSELECTは所有パッケージで完結できる — geoもgeofeaturesを直接参照する)。

### 2.7 version管理とdraft→activeの実質変更判定 — 推奨: draft表現(補完なし)での列比較

- **active作成**: version=1
- **active/paused内容更新(PATCH)**: version+1・updated Event(確定値13)
- **draft再保存**: version+1(05 §2「更新ごとに+1」)。Eventなしなのでキー衝突の懸念なし
- **draft→active**: 「条件内容の実質変更を伴わないactive化はversionを据え置く(全置換後のstructured_dataが同一の場合)」(確定値11)。**比較はdraft表現(§2.4の格納規則で正規化した保存列)で行う** — draft行は補完なしで保存されており、active化は補完込み(time_end=start+3h・expires_at補完値)の列を生成するため、補完後の列をそのまま比較すると同一内容でも常に相違と判定される。そこで「リクエストをdraft用resolveした結果」と「現行draft行の保存列」を比較し、同一ならversion据え置き・相違なら+1とする。比較対象はraw_text・category_primary・alcohol_involved・structured_data・budget_max・participants_min/max・visibility・notification_level・time_start・time_end・expires_at・geo_radius_m(geo_centerは導出値なので比較外)
- **pause**: version不変(発行規定なし)。**resume**: version+1・updated Event(確定値15)

idempotencyキー(created/updated, id, version)との整合: 据え置きactive化のcreatedは初回発行のため常に空き(06 §9-0)。内容更新のupdatedはversion+1ごとなので衝突しない。resumeのupdatedも+1ごと(確定値15の根拠)。

### 2.8 pause/resume・DELETE — 推奨: 条件付きUPDATE・DELETEはcancelled遷移+deleted Event

- **pause**(POST /v1/intents/{id}/pause): active行のみ受理。`UPDATE ... SET status='paused' WHERE id=:id AND status='active'`。影響行数0=対象行のstatusが異なる→422 VALIDATION_ERROR(遷移表にない遷移。409系codeは05上LATCH回答系に限る)。version不変・Eventなし(pausedはidx_intents_matchingの対象外=Layer 1から外れるため。既存候補の無効化はM2の削除/更新経路の設計に委ねる)
- **resume**(POST /v1/intents/{id}/resume): paused行のみ受理。version+1・updated Event(§2.2表)
- **DELETE**(DELETE /v1/intents/{id}): draft・active・paused行を受理しstatus=cancelledへ遷移(確定値20・05 §6遷移表)。**行の物理削除は行わない**。deleted Eventをcancelled遷移と同一トランザクションで発行(§2.2表)。matched・expired・cancelled行へのDELETE・pause・resumeは422(遷移表に遷移が存在しない)。応答は204
- **競合制御**: 全操作を`SELECT ... FOR UPDATE`→検証→`UPDATE`(WHEREにidと現statusを含む)の同一トランザクションで直列化する(06 §6の回答APIと同一の方式・C9の先行適用)。FOR UPDATEは主キー参照のため競合窓は行単位

物理削除しない根拠: 05 §6遷移表がDELETEをcancelledへの遷移として規定する(値域にcancelledが存在する以上、削除済み行の表現はstatusで持つ設計)。01 §21・08 §2.5の完全な削除範囲(本文・構造化データ・embedding・当該Intentを含む候補の全削除・回答待ちlatchesのcancelledクローズ・30日定期削除)は12 M3-8が別単位で実装する。M1時点ではmatch_candidates等は空(M2以降に生成)であり、削除連鎖の対象がまだ存在しない。この解釈の緊張(08 §2.5「該当Intent行…を削除する」は物理削除とも読める)は§6-3に記録した。

### 2.9 永続化とマイグレーション — 推奨: text()生SQL継続(ws-1委託判断の回答)・マイグレーション追加なし

ws-1設計§2.1は「ORMモデル・リポジトリ層の導入判断はintents CRUD(ws-3 M1)に委ねる」と残した。回答: **導入しない。text()生SQLを継続する**。選択肢:

- **A(推奨). ws-1流儀のtext()生SQLを継続し、SQL一式をstore.pyへ集約する**
- B. SQLAlchemy ORM(Declarativeモデル+AsyncSession)を本単位で導入する

推奨の根拠: (1)整合 — users・auth・geo・llmすべてがtext()/関数注入で構成されており、ここだけORMを入れると学習資産(docs/learn)と実装の対応が二重になる。(2)操作の性質 — 本単位のSQLはINSERT 1・SELECT(FOR UPDATE含む)3種・UPDATE 2種であり、いずれも1文で書ける。ORMの利益(リレーション横断・ユニットオブワーク管理)を使う場面がない。(3)試験 — 実SQLはintegration(test-ci)で、ドメインロジックはunit(スタブstore)で、という分離が§2.10の構成で既に得られている。Bを採る契機(クエリの複雑化・トランザクション管理の重量化)はM2のLayer 1〜3で再評価する。

**マイグレーション追加の要否: なし。** intents表の全列・match_events表・Index一式・idempotency式UNIQUE索引がalembic 0001で既存であり(確定値32)、本単位が必要とする列・表・索引に欠落はない(§6-1のlocation_nameはstructured_data(JSONB)内のキー追加のためスキーマ変更不要)。よってSTATUS運用ルール1〜3(共有ci-dbのtest-ci同時実行制約)への影響なし。STATUS運用ルール4(apiイメージ再ビルド)とルール5(テストファイルbasename一意 — 既存test_service.py(unit/intents)と衝突しないよう新規テストは§3.1の命名に従う)は本単位にも適用する。

### 2.10 一覧とcursor・依存注入・プロセス統合 — 推奨: キーセットcursor・store+uow注入

**cursor実装(確定値22の「サーバ生成の不透明文字列」)**: キーセット方式を採る。cursor = `base64url("{created_at_isoformat}|{intent_id}")`。クエリは`WHERE user_id=:uid [AND status=:status] AND (created_at, id) < (:ts, :id) ORDER BY created_at DESC, id DESC LIMIT :n+1`。n+1件取得し、n件を返して余りがあれば最終行からnext_cursorを組み立てる(なければnull)。decode失敗・形式不正のcursorは422 VALIDATION_ERROR。OFFSET方式(ページ揺れ・コスト)・暗号化cursor(中身が日付とUUIDで秘匿価値がない)は採らない。既定ソートはcreated_at降順(確定値18)。user_id+statusにはidx_intents_userが効く。created_at順のソートはユーザーあたり行数がレート制限により高々数百(ws-4で上限)であるため実用上十分で、Index追加は不要(§2.11)。statusクエリパラメータは6値(draft/active/paused/matched/expired/cancelled)のLiteral検証とし、不正値は422(FastAPIのRequestValidationError→既存ハンドラ→envelope)。

**依存注入(空のapp.state経由・ws-1/ws-2と同型)**:

```python
# store.py — 永続化(SQL一式。connを受け取る純粋な関数群)
class IntentStore:
    def __init__(self, engine: AsyncEngine) -> None: ...
    async def fetch_user_row(self, provider, subject) -> UserRow | None            # 索引済みSELECT 1本
    async def fetch(self, conn, intent_id) -> IntentRow | None                     # 参照(get)
    async def fetch_for_update(self, conn, intent_id) -> IntentRow | None          # SELECT ... FOR UPDATE
    async def insert(self, conn, cols: ResolvedColumns, *, user_id, status, now) -> uuid.UUID
    async def update(self, conn, intent_id, cols: ResolvedColumns, *, status, version, now) -> None
    async def update_status(self, conn, intent_id, *, status, version, now) -> None  # pause/resume/delete
    async def list_page(self, conn, user_id, *, status, before, limit) -> list[IntentRow]

# service.py — ユースケース(検証順序・遷移判断・Event発行)
UnitOfWork = Callable[[], AbstractAsyncContextManager[AsyncConnection]]  # engine.begin を束ねる

class IntentService:
    def __init__(self, *, clock, store: IntentStore, uow: UnitOfWork,
                 geocoder: SupportsForwardGeocoding) -> None: ...
    async def create(self, *, claims, raw_text, status, structured_intent) -> IntentDetail
    async def list(self, *, claims, status, cursor, limit) -> PageResult
    async def get(self, *, claims, intent_id) -> IntentDetail
    async def update(self, *, claims, intent_id, raw_text, status, structured_intent) -> IntentDetail
    async def pause(self, *, claims, intent_id) -> IntentDetail
    async def resume(self, *, claims, intent_id) -> IntentDetail
    async def delete(self, *, claims, intent_id) -> None

def make_intent_service(*, clock, settings, engine) -> IntentService:
    # GeoService(engine)・IntentStore(engine)・uow=engine.begin を束ねる
    # latch.geo への import はこのファクトリとProtocol戻り値型のみ(§2.5)
```

トランザクション境界はサービスが持つ(uowで開き、storeメソッドがconnを受け取る)。保存+Event INSERTを同一トランザクションにする§2.2の要件を、storeがトランザクションを知らないまま実現する構成である。unit試験はuow(ダミーconnをyield)・store(結果を仕込むスタブ)・geocoder・clock(FakeClock)を差し替えて決定的に動かす。

**PATCHリクエストの契約詳細(設計固定)**: `{"raw_text": 1〜300字(必須), "status": "active"|"draft"|null, "structured_intent": {...}|null}`。

- draft行+status未指定(またはdraft): 下書き再保存。structured_intentは任意(省略時は既存の構造データを保持しraw_textのみ更新。送った場合は全置換・§2.4規則で正規化)
- draft行+status=active: active化。raw_text+structured_intent(全量)必須。全検証(§2.3表)
- active/paused行+status未指定: 内容更新(全置換・全検証・version+1・updated Event)。structured_intent必須(省略は422 VALIDATION_ERROR — 全置換契約、確定値6)
- status=active指定で現行active: 内容更新と同一(statusは変わらない)
- その他のstatus変更(active→draft等): 422 VALIDATION_ERROR(確定値11の逆遷移不可を含む)
- paused行への内容更新: active行と同一の扱い(全検証・version+1・updated Event。statusはpausedのまま)。05 §5はdraft経路だけ特記するが「検証はPOSTと同一」はdraft外のPATCHに等しく適用されるため

**応答形状(設計固定)**: 全CRUD応答でintentオブジェクトは同形とする(POSTの応答例(確定値1)のスーパーセットであり下位互換)。所有者本人のみが受け取るためraw_text・structured_dataを含む。geo_center座標・embeddingは返さない(クライアントの使用途がなく、出力最小化の原則(08 §2.4)に沿う)。structured_intentは再編集・再表示用にリクエストと同形で再構成して返す(mapping.pyの逆方向関数 — structured_data.soft_constraintsをdowngraded_from_ngでsoft_constraints/ng_unverifiableへ分け、category・time・location(nameはstructured_data.location_nameから)・budget・participants・visibility・notification_level・expires_atを戻す)。

**main.py統合**: intents_crud_router(include・lifespanで`build_intents_crud = not hasattr(app.state, "intent_service"`判定をbuild_intents(parse)と並べて追加・`create_app`へ`intent_service`テスト注入引数を追加)。IntentsErrorハンドラはws-2が既に置いた1個で足りる(例外を追加するのみ)。新設定項目なし・依存追加なし。

### 2.11 採用しないもの(YAGNIによる切り捨て一覧)

- Active 5件上限(422 ACTIVE_INTENT_LIMIT)・作成/更新/APIレート制限(429)— ws-4(確定値24)
- expiry_sweeper・期限切れバッチ・catch-upスキャン — M3-3
- Pub/Subパブリッシャー・Worker・debounce・version検査・再試行/quarantined処理 — M2-1
- Embedding呼び出し・embedding_completed発行・正規化テキスト生成 — M2-2(07 §3)
- 削除範囲の完全実装(候補削除・latchesクローズ・30日定期削除)・退会処理 — M3-8
- ORM・リポジトリ層(§2.9で判断)・created_at順一覧用の追加Index(§2.10で不要と判断)
- PATCHの部分更新(フィールド単位)・ETag/If-Match・楽観排他の409応答(docsに規定なし。FOR UPDATE+条件付きUPDATEで足りる)
- GET応答でのgeo_center座標・embeddingの返却・条件ベースの監査ログ・変更履歴テーブル(08 §2.4の許可リスト外)
- match_eventsのpolling消化・pending行の掃除(M2の消費者実装と一体)
- 指定時刻Event(scheduled)・期限切れEvent(expired)の発行経路(定数のみ定義。発行者はM2/M3)

## 3. ファイル構成

### 3.1 作るもの

```text
backend/src/latch/intents/
├── intent_input.py    # 05 §5保存API契約の検証モデル(parseのParserOutputとは別契約)
│                      #   CategoryInput/TimeInput/LocationInput/BudgetInput/ParticipantsInput/
│                      #   StructuredIntentInput(全フィールドOptional・tz-aware検証・値域)/
│                      #   IntentCreateRequest/IntentPatchRequest(raw_text 1〜300字)
├── mapping.py         # 入力→保存列への変換(§2.3〜§2.4)
│                      #   ResolvedColumns(dataclass)・resolve_for_draft(inp)・
│                      #   resolve_for_active(inp)(補完消費・alcohol確定)/
│                      #   to_response_structured(row)(保存行→応答structured_intent再構成)
├── events.py          # MatchEvent発行(outbox INSERT・§2.2)
│                      #   EVENT_CREATED/UPDATED/DELETED(+EXPIRED/SCHEDULEDは定数のみ)/
│                      #   insert_match_event(conn, *, event_type, intent_id, version, now)
└── store.py           # 永続化(text()生SQL一式・§2.10)
                       #   UserRow(id, birth_date)・IntentRow(保存行列挙)・IntentStore
backend/src/latch/intents/service.py   # 追記: SupportsForwardGeocoding(Protocol)・
                                       #   IntentService(create/list/get/update/pause/resume/delete)・
                                       #   make_intent_service(clock, settings, engine)
backend/src/latch/intents/routes.py    # 追記: intents_crud_router(§3.1末尾の7エンドポイント)・
                                       #   リクエスト/応答pydanticモデル・get_intent_service依存
backend/src/latch/intents/errors.py    # 追記: GeocodingFailedError(422 GEOCODING_FAILED)・
                                       #   IntentNotFoundError(404)・ForbiddenError(403)・
                                       #   UnderAgeError(422)・InvalidTransitionError(422 VALIDATION_ERROR)
backend/tests/unit/intents/
├── test_intent_input.py   # §4.1-1 モデル受入・拒否(値域・tz-naive・300字・Literal)
├── test_mapping.py        # §4.1-2 変換(alcohol確定・ng降格・補完消費・draft正規化・再構成)
├── test_events.py         # §4.1-3 event_type定数・insert SQLパラメータ(storeスタブでなく
│                          #   関数契約の試験。実INSERTはintegration)
├── test_intents_service.py # §4.1-4 ユースケース(§2.2表の全操作・検証切替・遷移・version)
│                           #   ※basename一意のためこの命名(STATUS運用ルール5。unit/intents/test_service.pyはparse専用)
├── test_crud_routes.py    # §4.1-5 ルーティング(7エンドポイント・応答形状・envelope・cursor)
└── test_time_validation.py # §4.1-6 時刻検証(FakeClock・過去/now+7日境界・expires_at・draft不問)
backend/tests/integration/
└── test_intents_crud_api.py  # §4.2 実HTTP・実DB・実ジオコーディング
```

エンドポイント一覧(routes.py。全て`dependencies=[Depends(require_authenticated)]`):

```text
POST   /v1/intents                → 201 {intent}              (active/draft作成)
GET    /v1/intents                → 200 {items, next_cursor}  (?status=・?cursor=・?limit=)
GET    /v1/intents/{id}           → 200 {intent}
PATCH  /v1/intents/{id}           → 200 {intent}              (全置換・active化・draft再保存)
DELETE /v1/intents/{id}           → 204                       (cancelled遷移)
POST   /v1/intents/{id}/pause     → 200 {intent}
POST   /v1/intents/{id}/resume    → 200 {intent}
```

インターフェースの要旨(実装詳細は計画書・TDDで確定):

```python
# intent_input.py — 全フィールドOptional(§2.4)。tz-aware検証はParserTimeと同型
class StructuredIntentInput(_IgnoreExtraModel):
    category: CategoryInput | None = None          # primary: Literal 3値 | None
    alcohol_involved: bool | None = None
    time: TimeInput | None = None                  # start/end: datetime | None(tz-aware必須)
    location: LocationInput | None = None          # name: str(min_length=1)|None・radius_m: int(ge=1)|None
    budget: BudgetInput | None = None              # max: int(ge=0)|None・currency: Literal["JPY"]
    participants: ParticipantsInput | None = None  # min/max: int(ge=1, le=4)|None
    visibility: Literal["hidden_until_match","summary_only"] | None = None
    notification_level: Literal["proposals_only","nearby_also","muted"] | None = None
    expires_at: datetime | None = None             # tz-aware必須
    soft_constraints: list[str] | None = None
    negative_constraints: list[str] | None = None  # 受け取るが保存は常に空配列(FR-42)
    ng_unverifiable: list[str] | None = None

class IntentCreateRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=300)
    status: Literal["active", "draft"] = "active"
    structured_intent: StructuredIntentInput | None = None

class IntentPatchRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=300)
    status: Literal["active", "draft"] | None = None
    structured_intent: StructuredIntentInput | None = None

# mapping.py — alcohol確定(§2.6)・補完消費(§2.3表)・§2.4のdraft正規化を1箇所に
@dataclass(frozen=True)
class ResolvedColumns:
    category_primary: str            # active: primary / draft: 指定値 or ''
    alcohol_involved: bool           # active: drinking→True確定 / draft: 指定値 or False
    structured_data: dict            # 5キー+location_name(§1.2確定値34)
    budget_max: int | None
    participants_min: int            # 未指定→2
    participants_max: int            # 未指定→2
    visibility: str                  # 未指定→hidden_until_match
    notification_level: str          # 未指定→proposals_only
    time_start: datetime | None
    time_end: datetime | None        # active: 未指定→start+3h / draft: 指定値のみ
    expires_at: datetime | None      # active: 未指定→nearest_expires_at / draft: NULL
    geo_radius_m: int | None         # active: 未指定→1000 / draft: 指定値のみ
    geo_lon: float | None            # active: ジオコーディング結果 / draft: None
    geo_lat: float | None            #   (INSERT時 ST_SetSRID(ST_MakePoint)でgeography化)
    version: int = 1                 # サービスが上書き(§2.7)

def resolve_for_draft(inp: StructuredIntentInput) -> ResolvedColumns: ...
def resolve_for_active(inp: StructuredIntentInput, *, now: datetime) -> ResolvedColumns: ...
def differs_from_row(cols: ResolvedColumns, row: IntentRow) -> bool: ...  # draft表現比較(§2.7)
def to_response_structured(row: IntentRow) -> dict: ...

# events.py
EVENT_CREATED = "created"; EVENT_UPDATED = "updated"; EVENT_DELETED = "deleted"
EVENT_EXPIRED = "expired"; EVENT_SCHEDULED = "scheduled"  # 発行経路はM2/M3(§2.11)
async def insert_match_event(conn, *, event_type: str, intent_id: uuid.UUID,
                             version: int, now: datetime) -> None:
    # INSERT INTO match_events(event_type, source_intent_id, payload, status, created_at)
    #   VALUES(:type, :id, jsonb '{"version": N}', 'pending', :now)

# service.py — 検証順序は§2.5(形式→必須→時刻→年齢→ジオコーディング→保存)
class IntentService:
    # create: user_lookup(404)→[active: 必須3(422)→時刻(422)→年齢(422)→ジオコーディング(422)]
    #         →resolve→uow内 INSERT+Event(activeのみ)→IntentDetail
    # update: uow開く→fetch_for_update(404/403)→§2.10のPATCH分岐→検証・resolve→
    #         UPDATE+Event(§2.2表)→IntentDetail(versionは§2.7)
    # pause/resume/delete: uow開く→fetch_for_update(404/403)→条件付きUPDATE
    #         (遷移不可422)→resumeのみEvent→deleteはdeleted Event
    # list/get: 読み取りのみ(uow不要・store.connect相当)
```

### 3.2 触るもの(既存ファイルへの変更)

- `backend/src/latch/main.py` — (1) intents_crud_routerのinclude、(2) lifespanへ`intent_service`構築追加(parse用build_intents判定と並ぶ独立スキップ判定)、(3) `create_app`へ`intent_service`テスト注入引数を追加。IntentsErrorハンドラは既存1個のまま(例外追加のみ)。**これらが本単位の既存コードへの全変更**

### 3.3 触らないもの

- `backend/src/latch/intents/schema.py`(ParserOutput・parse専用)・`prompt.py`・`completion.py`(消費のみ・§1.3)
- `backend/src/latch/geo/`(GeoService無変更・§2.5)・`auth/`・`core/`・`llm/`・`worker/`・`users/`(age_yearsのimportのみ・無変更)
- `backend/alembic/`(マイグレーション追加なし・§2.9)・`backend/src/latch/settings.py`(新設定なし)・`pyproject.toml`/`uv.lock`(依頼追加なし)
- `compose.yaml`・`Makefile`・`Dockerfile`・`.mise.toml`
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/learn/`(agent4運用中)・`docs/plans/M0/`・`prototype/`・`README.md`・`.claude/`

## 4. テスト方針

### 4.1 unit(`make test`。外部プロセス不要・FakeClock+スタブで決定的)

1. **intent_inputモデル**: 受理(active/draft両リクエストの正例・部分structured_intent・4値フィールド)・拒否(raw_text 0字/301字・primaryが3値外・participants min=0/max=5・budget.max=-1・radius_m=0・naiveなtime.start/expires_at・status値外)。拒否はRequestValidationError→既存422ハンドラ経路
2. **mapping変換**: (a)resolve_for_active — drinking+alcohol=false→**true確定**(クライアント値より優先)・meal+alcohol=true→trueのまま・alcohol未指定→false、time.end未指定→start+3h(completion消費)・participants未指定→(2,2)・radius未指定→1000・expires_at未指定→nearest_expires_at呼び出し(引数検証)・ng_unverifiable→`downgraded_from_ng: true`のsoft_constraints・soft/ng両方→soft由来が先の連結・negative_constraints非空→**保存は空配列**・structured_dataにlocation_name。(b)resolve_for_draft — 補完なし(time_end・expires_at・geo_radiusはNULL)・category未指定→''・alcohol未指定→false・未指定配列→[]。(c)to_response_structured — downgradedフラグでsoft/ngへ分割・location_name→location.name・draft部分行の再構成
3. **events**: 定数ピン留め(created/updated/deleted/expired/scheduled)・insert_match_eventが渡すパラメータ(event_type・payloadのversion・status='pending'・Clock由来created_at)を、connスタブの呼び出し記録で検証
4. **IntentService**(スタブstore・スタブgeocoder・FakeClock・FakeUow): §2.2表の全操作を網羅 — (a)POST activeハッピー(INSERT+created Event version=1が同一connで呼ばれる)、(b)POST draft(Eventなし・geo_center NULL)、(c)検証切替(activeで必須3欠落→422 VALIDATION_ERROR・draftで同じ欠落→受理、時刻: 過去・now+7日ちょうど/1秒超・expires_at同様、alcohol=true+19歳→422 UNDER_AGE・20歳誕生日当日→受理、ジオコーダNone→422 GEOCODING_FAILED)、(d)PATCH分岐全部(draft再保存=Eventなし+version+1・draft→active同一内容=version据え置き+created Event・内容変更=+1+created・active内容更新=+1+updated Event・structured_intent省略のactive更新→422・status=active→draft指定→422・paused内容更新=paused維持+updated Event)、(e)pause(active→paused・Eventなし・version不変・draftのpause→422)、(f)resume(paused→active・version+1・updated Event・activeのresume→422)、(g)DELETE(draft/active/paused→cancelled・deleted Event・version不変・204・matched模擬行→422)、(h)認可(他人の行→403 FORBIDDEN・不在→404・未登録JWT(user_lookup None)→404)、(i)list(cursorデコード→storeへbeforeが渡る・不正cursor→422・limit検証・next_cursor組み立て)
5. **ルーティング**(create_app+スタブサービス注入+`app.dependency_overrides[require_authenticated]`): 7エンドポイントの応答形状(intent envelope・items/next_cursor・204)・422/422 UNDER_AGE/422 GEOCODING_FAILED/403/404の各envelope・`require_authenticated`を401に上書きした場合のUNAUTHENTICATED・limit=101→422・status=drafty等不正値→422
6. **時刻検証の境界詳細**(test_time_validation.py・§4.1-4cから独立したファイル): FakeClockでtime_start=now(境界・受理)・now-1秒(422)・now+7日ちょうど(受理)・now+7日+1秒(422)・expires_atの同一境界・draftでは同値が受理される対照
7. **arch規律・ログ規律**: 既存`test_arch_no_direct_time.py`が新規ファイルを自動スキャン(ヒットなし)。ログ出力にraw_text・location.name・条件文言が含まれないことをcaplogで検証(08 §2.4)

### 4.2 integration(`make test-ci`。compose常設api(127.0.0.1:8000)・実DB・実geofeatures。**apiイメージ再ビルド後**に実行)

前提: users表に実Userを作成(test_users_api.pyと同一のtool発行IdPトークン→POST /v1/usersフロー。subjectは実行ごとにユニーク)。行は試験内で後始末(共有ci-db汚染回避)。正転ジオコーディングの地名は**「天文館」**に固定する — geo試験fixture(osm_sample.xml)と実取り込みデータ(M0 ws-4のgeo-verify実績)の双方に存在し、geo試験によるgeofeaturesのリロード前後(実データ↔fixture)のいずれでも解決するため、試験順序に依存しない。GEOCODING_FAILED側の検証には存在しない地名(「存在しない地名テスト」等)を使う。

| # | 検証 | 根拠 |
|---|---|---|
| 1 | **active作成フルフロー**: 登録→POST /v1/intents(active・全フィールド)→201(intent.id・status=active・version=1・expires_at)→GET /v1/intents/{id}で内容一致(raw_text・structured_data・category・補完後time_end・expires_at)→DB直接検証: geo_center IS NOT NULL・structured_data.location_name・**match_eventsに(created, id, version=1, status=pending)行がちょうど1件** | 05 §5・06 §9(確定値1・2・5) |
| 2 | **draft作成**: POST(raw_textのみ+status=draft)→201・DB検証(geo_center IS NULL・expires_at IS NULL・visibility=hidden_until_match・match_events行なし)→GET /v1/intents?status=draftに現れる | 05 §5(確定値3) |
| 3 | **draft→active**: PATCH status=active(全量)→200・status=active・DB検証(geo_center確定・expires_at補完・created Event発行・同一内容ならversion=1据え置き)→内容変更を伴う再active化パターンでversion=2+created Event | 05 §5・§6・06 §9-0(確定値11・14) |
| 4 | **検証422一式(実HTTP)**: 必須3欠落(該当フィールドをnullで送信)・time_start過去・now+7日超・expires_at過去・19歳Userでalcohol=true(422 UNDER_AGE)・存在しない地名(422 GEOCODING_FAILED)。**draftでは同内容が受理される**ことの対照 | 05 §5(確定値7・8・9) |
| 5 | **PATCH active更新**: 全置換(location変更含む)→200・version=2・updated Event(version=2)・geo_centerが再ジオコーディング | 05 §5(確定値6・13) |
| 6 | **pause/resume**: pause→200(paused・version不変・Event増えない)→resume→200(active・version+1・updated Event) | 05 §5・§6(確定値15) |
| 7 | **DELETE**: active行→204・status=cancelled・deleted Event。draft行も同一 | 05 §5・§6(確定値20) |
| 8 | **認可**: 別UserのIntentへのGET/PATCH/DELETE/pause→403 FORBIDDEN。不在ID→404。Authorization欠落→401 | 05 §5(確定値23・33) |
| 9 | **cursor**: 3件作成→limit=2で1ページ目(items 2件・next_cursor非null)→cursorで2ページ目(残り1件・next_cursor null)。?status=フィルタの絞り込み | 05 §5(確定値18・22) |
| 10 | **後始末**: 作成User・Intent・match_eventsを行ごとDELETE(match_eventsはsource_intent_id対象。 Intent行は本試験で物理削除してよい — 試験データの後始末であり仕様の削除経路ではない) | 共有ci-db規約 |

draft→expired(期限切れ)・active→expiredはexpiry_sweeper(M3-3)のため試験しない。Eventの種別・version・pendingはDB直接検証(実HTTP応答には現れない)。

### 4.3 既存資産への回帰

`make lint`・`make test`(既存unit+intents parse追加分が同居するtest_completion等への影響なし・service.py/routes.pyへの追記はparse試験のimport先を壊さない)・`make test-ci`(374+新規)。main.pyのlifespan改変はASGITransportがlifespanを実行しないため既存unit試験に影響しない(ws-1・ws-2設計§4.3と同一根拠)。マイグレーション追加がないため、alembic head=0002のまま・運用ルール1〜3に無関係(§2.9)。

## 5. 完了条件(この単位の受渡し判定。計画書が参照する)

1. `make lint`・`make test` がグリーン(unit/intents 追加分を含む)
2. `make test-ci` で §4.2-1〜10 がグリーン(実行前にapiイメージを再ビルドしていること — STATUS運用ルール4。マイグレーション追加なしのためalembic操作は不要)
3. §2.2表のEvent発行(created/updated/deleted・version・pending)が§4.1-3/4・§4.2-1/3/5/6/7で立証済み
4. 検証のactive/draft切替(必須3・時刻・年齢・ジオコーディング)がunit+integrationの対照試験で立証済み(§4.1-4c・§4.2-4)
5. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが引き続き `core/clock.py` のみ
6. ログ・例外メッセージにraw_text・地名・条件文言が混入しないことをunit試験(caplog)で検証済み(08 §2.4)
7. `alembic/`・`geo/`・`auth/`・`core/`・`llm/`・`worker/`・`users/`(importのみ)・`settings.py`・`pyproject.toml`・`uv.lock`・`compose.yaml`・`Makefile` に差分なし(§3.2・§3.3)
8. 触るファイルが §3.1・§3.2 の一覧どおり。テストbasenameがtests配下全体で一意(`find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空 — STATUS運用ルール5)

## 6. 未解決の論点(設計は推奨で固定済み。supervisor確認事項)

1. **location.nameの格納先(05 §2の想定漏れ)**: intents表の独立カラムはgeo_center/geo_radius_m(座標系)のみで、structured_dataの保持キー5つ(05 §2)にもlocation.nameが存在しない。一方、Embedding正規化テキスト(07 §3)・条件リストの再表示(03 §3・ws-5)・draft再開の編集にはlocation.name(入力表現)が必須であり、geo_centerからの逆転ジオコーディングでは1kmグリッド丸めにより別地名になり得る(精度低下)上にdraftはgeo_centerがNULLで逆転不能。本設計は**structured_dataへlocation_nameキーを追加**して格納した(JSONBのためマイグレーション不要・05 §5の「structured_intent全置換」契約との整合も保たれる)。05 §2「structured_dataが保持するのは次のキーのみとする」への追記(第6種キー)として05の次回改版に含めるべきと判断する。**05の改版要否と追記内容の承認を求める**
2. **G1完了条件02#4とM1スコープの緊張**: 02#4(有効期限を設定できる)の検証方法は「expires_atを指定して保存し、期限経過後の状態を確認する。Intentがexpiredに遷移」であり、G1は#1〜#4がci環境グリーンを要求する(12 M1完了条件)。しかし期限切れを実行するexpiry_sweeperは12 M3-3に置かれ、M1スコープ4〜7に含まれない。本単位は保存時検証(過去不可・+7日)までを実装し、期限経過後のexpired遷移はM3に委ねた。**G1判定時に#4をどう扱うか(期限経過の確認を Clock 操作による期限切れ値の保存だけで代替するか・期限切れバッチをM1へ前倒すか)はスーパーバイザーの判断を求める**
3. **DELETEの実体(cancelled遷移+deleted Eventで固定)**: 05 §6遷移表はDELETEをcancelledへの遷移として書き、08 §2.5「該当Intent行…を削除する」は物理削除とも読める。本設計は字義(05 §6)に従いcancelled遷移とし、物理削除・候補削除はM3-8(削除・退会単位)が担う構成を取った。M1では候補が生成されないため実害はないが、**この解釈の承認を求める**
4. **event_typeの文字列値(created/updated/deleted/expired/scheduled)**: docsに文字列の確定値がない(01 §5は日本語名称・06 §9は「作成種(create)」と説明するのみ)ため、embedding_completed(05 §2で唯一確定している文字列)の過去分詞形に揃えて固定した。**M2設計がこの値を引き継ぐことの確認を求める**(DB永続値のため後からの変更はデータ移行を要する)
5. **PATCH契約の未規定部分の固定**: (a)status未指定=現状維持、(b)draft再保存でのstructured_intent省略=構造データ保持(送れば全置換)、(c)active/paused更新でのstructured_intent必須(全置換契約)、(d)paused行への内容更新=activeと同一検証、(e)遷移表外の操作(draftのpause・matched行のDELETE等)=422 VALIDATION_ERROR、(f)他人のIntentへの操作=403 FORBIDDEN(05エラー表の字義。存在秘匿の404ではない)、(g)未登録JWT(user_lookup None)=404(users/meと同一)。いずれもdocsの明示がないため設計で固定した
6. **応答intentオブジェクトの形状**: 05 §5のPOST応答例はid/status/version/expires_atの4フィールドだが、draft再開・ws-5の再表示のため全CRUD応答で同形の詳細(raw_text・structured_data・再構成structured_intentを含む)を返すことにした。応答例のスーパーセットであり契約違反ではない解釈だが、**応答形状の確定値を05へ反映する際の前提として承認を求める**
