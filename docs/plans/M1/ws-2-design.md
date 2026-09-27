# M1 ws-2(Intent Parser + POST /v1/intents/parse)設計メモ

- 作業単位: ws-2 — Intent Parser(07 §2: 確定済みシステムプロンプト実装・アプリ層補完の単一規則・D-04連携ng_unverifiable→warnings)+ POST /v1/intents/parse(同期LLM・timeout 10秒・再試行なし・503 LLM_UNAVAILABLE/422 VALIDATION_ERROR切替 D-17)(docs/plans/STATUS.md M1表 / 出典 12 M1-2・M1-3 / 07 §2・05 §5 / 依存: M0 LLM Gateway=マージ済み)
- 作成: 2026-09-27(agent1 / superpowers:brainstorming使用)
- 次工程: 計画書(ws-2-plan.md)へ変換 → 実装エージェントがworktree内でTDD実装
- 並走: ws-1(users API)=users/配下。**本単位はマイグレーションを追加せず・llm/にも無差分**(§1.3・§3.3)のため、STATUS運用ルールのtest-ci同時実行制約には抵触しない。交点はmain.pyのみ(§2.7・マージ時両側追記保持)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M1スコープ2〜3(12 M1-2・M1-3)を実装する。LLM Gateway(M0 ws-2・スタブ込み)の上に、(1)07 §2の確定済みシステムプロンプトの実装、(2)アプリ層補完の単一規則(03 D-19)、(3)D-04連携(ng_unverifiable→warnings)、(4)POST /v1/intents/parse(同期LLM・timeout 10秒・再試行なし・D-17の503/422切替)を作る。実プロバイダ接続は作らない(T1未確定。llm_mode="stub"で動作)。Parser本体(プロンプト・出力スキーマ検証・補完規則・warnings生成)は**LLM非依存・DB非依存の純粋モジュール**として切り出し、単体テスト可能にする(スーパーバイザー指示)。02#1〜#4の下流(保存=ws-3・フロント=ws-5)は本単位のparse応答と補完規則を前提にする。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | Intent Parserの呼び出し特性: **同期(ユーザーが待つ)・timeout 10秒・再試行なし(即フォールバックへ)**。タイムアウト時は再試行せず構造化フォームへフォールバックする。性能目標はp95 3秒/p99 8秒(目標値であり実装で強制する値ではない) | 07 §1表・§5 D-17 |
| 2 | システムプロンプト(全文)が07 §2で確定済み。入力は日本語の自然文(最大300字・**アプリ層で事前検証**)、現在日付は `{current_date}` としてプロンプトへ渡す。出力は指定のJSONスキーマのみ(説明文を含めない) | 07 §2 |
| 3 | parseリクエストのtextはraw_textの上限300字と同一値で検証し、超過は422 VALIDATION_ERROR。**切り詰めは行わない** | 07 §2解釈規則(v0.4) |
| 4 | **必須3フィールド(category / time.start / location.name)が抽出できない場合と、LLM障害(timeout・API障害・レート制限)の場合とでは応答を区別する(FR-43)**。前者は422 VALIDATION_ERROR(フォームフォールバック)、後者は503 LLM_UNAVAILABLE(入力テキストを保持した再試行ボタン) | 07 §2・§5 D-17 |
| 5 | Parserはフィールドの抽出のみを行い、**デフォルトの補完はParser後のアプリ層で単一の規則として適用する(二重実装による不整合を防ぐ)**。補完表: time.end null→time.start+3時間 / participants null→min=2,max=2 / location.radius_m null→1,000(既定半径) / budget.max null→nullのまま(制約なし) / expires_at→time.start+3時間に最も近い4選択肢 / visibility→hidden_until_match / notification_level→proposals_only | 07 §2解釈規則(03 D-19) |
| 6 | 有効期限の4選択肢: **今夜23:30(当日23:30 JST)/ 明日12:00(翌日12:00 JST)/ 明日23:30(翌日23:30 JST)/ 3日後まで(現在時刻+72時間)**。選択肢の時刻が現在より過ぎていれば選択不可。既定は選択可能な選択肢のうちtime.start+3時間に最も近い値 | 03 §3(FR-13) |
| 7 | **negative_constraintsはMVPでは常に空配列(FR-42)**。判定不能NGはng_unverifiableへ入れ、negative_constraintsには入れない(プロンプト規則5)。Layer 1での照合を二重に実装しない | 07 §2規則5・解釈規則 |
| 8 | alcohol_involvedは常にtrue/falseを出力し、**アプリ層での補完はない**。category.primary=drinkingは常にtrue。保存時にサーバ側で確定するのは保存経路(ws-3)の責務 | 07 §2規則7・解釈規則・05 §5 |
| 9 | **D-04連携**: ng_unverifiableが空でない場合、応答のwarningsに `{code: "NG_CONDITION_DOWNGRADED", condition: ..., message: "この条件は確実には除外できません。参考条件として扱います"}` を載せる。クライアントは条件リストに注意表示を出す(02 D-04・03 §3)。Hard Filterの対象にはならない | 07 §2 D-04連携・05 §5応答例 |
| 10 | POST /v1/intents/parse契約: request `{"text": "..."}` → response **200** `{"structured_intent": {...07のParser出力スキーマと同形...}, "warnings": [...]}`。**保存しない**(01レビューSP-4)。structured_intentはvisibility・notification_levelを含まず、座標を返さずlocation.nameのまま(ジオコーディングは保存前・ws-3)。応答例ではtime.end=null・radius_m=nullのまま(=**parse応答に補完規則5を適用しない**) | 05 §5 |
| 11 | エラー形式(共通): `{"error": {"code", "message", "details"}}`。parse関連のcode: 400 MALFORMED_REQUEST(JSON形式不正)・401 UNAUTHENTICATED・422 VALIDATION_ERROR(必須3フィールドの抽出不能・textの300字超過)・503 LLM_UNAVAILABLE(LLM障害)・503 DEPENDENCY_UNAVAILABLE(DB・Redis等の依存障害・全API) | 05 §5エラー形式表 |
| 12 | 全API認証済みユーザーのみ(parseは認証不要2エンドポイントに含まれない)。M1以降のドメインルータは `require_authenticated` 依存をルータ単位に付す(C3の強制方法) | 05 §5冒頭 / auth/deps.py |
| 13 | 時刻参照はすべてClock経由(arch test `test_arch_no_direct_time` が backend/src 全体を強制)。**JST暦日付は `clock.jst_date()`** が正規経路。プロンプトの{current_date}もこれで与える | 04 §5(FR-41)・10 §1 / core/clock.py |
| 14 | 送信記録は送信先・データ種別・時刻を含み**内容を含まない**。ユーザーからの問い合わせ時に開示できる状態を保つ(→Parser系統のuser_id帰属の用途)。ログ・例外にIntent本文を混ぜない | 08 §3・§2.4 |
| 15 | Parser構造化精度ゲート(G1): category 85% / time.start 90% / location 90% / participants 80% / budget 90%。**プロンプト変更のたびにゲートを再実行できる状態**にする(→プロンプトはバージョン管理された単一の定数であること) | 07 §5 D-17・09 §4.3・12 M1完了条件 |
| 16 | 時刻検証(過去不可・現在+7日上限)は**activeの作成・更新時**(保存経路)に適用するものであり、parse(保存しないプレビュー)では適用しない | 05 §5 POST /v1/intentsの時刻検証 |
| 17 | Gateway資産(M0 ws-2・マージ済み): `LLMGateway.parse_intent(*, text, current_date, user_id=None) -> dict`・timeout 10秒・再試行なしはGateway内で実装済み。失敗は `LLMTimeoutError` / `LLMProviderError`。送信記録(SendRecord)は成否毎に出力。StubLLMは応答注入(parser_response)・常時失敗フラグ・遅延注入可能。`DEFAULT_PARSER_RESPONSE` は必須3フィールドを含むスキーマ適合の固定応答 | backend/src/latch/llm/(gateway.py・stub.py) |
| 18 | SendRecord.user_id引数はM0 ws-2設計§6-2が**M1のparse API実装へ値を渡す判断を委託**した予約(08 §3の開示目的) | M0 ws-2設計 §6-2 / llm/records.py |

### 1.3 Gateway資産との接続(llm/は無変更)

M0 ws-2が作ったGatewayはparse APIの要求をすべて満たしており、**本単位はllm/配下に一切差分を作らない**。parser系統のtimeout 10秒・再試行なし・送信記録・スタブ応答注入はすべてGateway・StubLLMの既存実装をそのまま使う。M0 ws-2設計§1.4が「Parser/Jev応答のスキーマ検証(07 §2必須3フィールド判定)=M1」と後回しにした部分と、§6-2が委託したuser_id渡しを実装するのが本単位の仕事である。実プロバイダ(llm_mode="real")はT1確定後の別単位で、`build_llm_gateway` の分岐に追加する(M0設計どおり・Gateway IF不変)。

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

- **POST /v1/intents(CRUD・draft/active)・ジオコーディング・alcohol_involvedのサーバ側確定・時刻検証** — ws-3(12 M1-4・M1-5)。補完規則(確定値5・§3.1 completion.py)の**消費**はws-3が行う(本単位は規則の単一実装のみ)
- **レート制限(429 RATE_LIMITED)** — ws-4(12 M1-6)
- **フロントエンドのparse連携・期限既定選択計算** — ws-5(12 M1-7)
- **実プロバイダadapter・llm_mode="real"・API鍵管理** — T1確定後の別単位
- **精度ゲートharness・入力セット・期待値表** — T3・09 §4.3(本単位はプロンプト定数とParserOutputスキーマを再利用可能な形で提供)
- **p95/p99の計測・レイテンシモニタリング** — M4 Observability(12 M4-5)
- **Jev・Embeddingの呼び出し側** — M2(12 M2スコープ2・6)

## 2. 実装方式の選択肢と推奨

### 2.1 Parser本体の受け皿 — 推奨: `latch/intents/` パッケージを新設

選択肢:

- **A(推奨). `backend/src/latch/intents/` を新設し、Parser本体(プロンプト定数・出力スキーマ・補完規則・サービス)とparseルータを置く**
- B. `latch/llm/` 配下にparserサブモジュールとして置く
- C. ルータとサービスのみ作り、スキーマ・補完はmain.py近傍のapi層に置く

推奨の根拠: Parser本体の構成要素(出力スキーマ検証・D-19補完規則・D-04 warnings・規則5正規化)は**ドメイン知識**であり、LLM接続の共通経路(C4・送信記録とtimeout)に徹するというM0 ws-2のllm/設計と異質である。Bはこの境目を曖昧にする。またSTATUS依存表でws-3(intents CRUD)はws-2に依存しており、ws-3が同じintentsドメイン(errors・スキーマ)を拡張することを考えれば、パッケージの土台を本単位で作るのが自然である。users(ws-1)と対称な構成(service・routes・errorsをドメインパッケージへ)になり、M1以降のドメイン構成が揃う。

トレードオフ: 新パッケージが増える。ただしファイル構成はws-1(users/)と同型であり、学習の観点でも重複がない。

### 2.2 Gateway呼び出しの結合 — 推奨: 構造的Protocol(`SupportsParseIntent`)

選択肢:

- **A(推奨). intents/service.py 内に `parse_intent` メソッドを持つProtocolを定義し、`IntentParseService` はそれへ依存。LLMGatewayはシグネチャが一致するため構造的適合(アダプタ不要)**
- B. 具体クラス `LLMGateway` を型として注入する(intents→llmのimportが生じる)
- C. 純粋なCallableを注入する

推奨の根拠: 「Parser本体はLLM非依存」(スーパーバイザー指示)を**import関係も含めて構造で強制**できる — intents/配下はllm/を1行もimportせず、Protocolへの差し替えだけでunit試験が成立する。実行時は本物のGateway(とStubLLM)をそのまま渡せば、timeout・送信記録を含む経路が試験される。Bは「型のためにだけ」llm/へ依存し、llm/変更時の影響がintentsへ波及する。Cはキーワード引数(text・current_date・user_id)の意味が型から消える。

トレードオフ: Protocolの静的検査は型検査頼りである。ただしGateway側シグネチャはM0で固定済み(確定値17)であり、ずれはunit試験(実Gatewayを渡す試験を含む・§4.1-4)が検出する。

### 2.3 送信記録user_idの帰属(M0 ws-2 §6-2の引継ぎ)— 推奨: User.idをlookupして渡す

M0 ws-2設計§6-2が本設計へ委託した判断(確定値18)。選択肢:

- **A(推奨). authの `make_user_lookup`(M0 ws-3でマージ済み・UNIQUE索引へのSELECT 1本)を注入してUser.idを引き、`str(id)` をGatewayへ渡す。User行がない(未登録JWT)場合はNoneのまま呼ぶ。lookupの失敗は503 DEPENDENCY_UNAVAILABLE**
- B. 常にNoneにする(開示の実務が現れるM4まで保留)
- C. JWT claimsのauth_subjectをuser_id欄へ入れる

推奨の根拠: SendRecord.user_idの用途は08 §3の「ユーザーからの問い合わせ時に開示」であり、per-userの絞り込みに一意なユーザー識別子が要る。Bは予約を宙吊りにし、ci/stagingの全parse送信記録から帰属が消える。CはIdP識別子をログへ出す(プライバシー下行)うえ、provider横断で衝突し得る文字列であり「user_id」の語義にも合わない。AのUser.id(uuid)は正規の識別子で、lookup関数は**マージ済み資産**であるためws-1(未マージ)への依存も生じない。

トレードオフ: parseがDB依存になる(1本の索引済みSELECT・p95 3秒に対しては無視できるコスト)。DB障害時はparseも503 DEPENDENCY_UNAVAILABLEで失敗する(fail-closed。依存障害の契約上自然だが、LLM自体は健常でもDB落ちだとparse不能になる点は§6-1に明記)。

### 2.4 LLM出力の検証失敗(パース不能・型違反)の応答区分 — 推奨: すべて422 VALIDATION_ERRORへ一元

確定値4は「必須3フィールド抽出不能=422 / LLM障害=503」の2区分のみを規定し、**LLMがスキーマに適合しない出力を返した場合**(JSONとして不正・型違い・必須キー欠落)の区分は明記していない。選択肢:

- **A(推奨). 出力の検証は `ParserOutput.model_validate` に一元化し、ValidationErrorはすべて422 VALIDATION_ERROR(構造化不能)として応答する**
- B. JSONパース不能・型違反は503 LLM_UNAVAILABLE(LLM障害)に分ける

推奨の根拠: (1)契約上の応答区分は2つであり、第三の区分を勝手に増やす余地を残さない。(2)スキーマに適合しない出力からは必須3フィールドを**信頼して**取り出せない。「抽出できない」のと実務上同じである。(3)422の逃し道は構造化フォームであり常に機能する。503の再試行はgarbageが再発する可能性を抱えた往復になる。timeout・API障害(確定値4の列挙)はGatewayの例外(LLMTimeoutError/LLMProviderError)として届くため、この時点で確実に503へ振り分けられる — 2区分の判定材料は「例外が上がったか・検証を通ったか」で決定的になる。

トレードオフ: 一過性の出力劣化(打ち切られたJSON等)でもフォームへ誘導される。ただしD-17の再試行ボタンはテキスト保持付きであり、ユーザーはフォームと再入力のどちらでも復帰できる。

### 2.5 プロンプト規則5違反(negative_constraints非空)の扱い — 推奨: ng_unverifiableへマージする正規化

プロンプト規則5と確定値7はnegative_constraintsを「MVPでは常に空配列」と規定するが、LLMが違反して値を入れた場合の扱いは明記されていない。選択肢:

- **A(推奨). サービス内でdict段階の正規化を行う: negative_constraintsの各要素をng_unverifiableへ結合し、negative_constraintsは空配列として応答する(結合結果はwarningsの生成対象になるためD-04の注意表示も出る)**
- B. LLM出力をそのまま返す

推奨の根拠: Bだと「常に空配列」の不変式(FR-42)が破れたまま下流へ流れ、Layer 1で判定されないNG条件が**注意表示なしで**保存され得る(判定不能NGは黙って降格させない、がD-04の原則)。Aはこの不変式を回復できる唯一の場所(parse境界)であり、正規化された条件はng_unverifiable由来としてwarningsを伴うため、ユーザーへの見え方はD-04と同一になる。プロンプト規則5により通常は空であり、正規化は保険である(数行+試験1件のコスト)。

トレードオフ: LLM出力の書き換えを行う。09 §4.3の期待値表にnegative_constraintsの正例を設けない規定とも整合する(正規化後は常に空)。

### 2.6 プロンプトの所在と固定 — 推奨: `intents/prompt.py` の定数+全文ピン試験

選択肢:

- **A(推奨). 07 §2の全文(改行・規則番号まで含む)を `PARSER_SYSTEM_PROMPT` 定数とし、`{current_date}` だけを差し替えるフォーマッタを置く。unit試験で全文をピン留めする**
- B. llm/配下(実プロバイダ実装の隣)に置く
- C. 設定(env・DB)から与える

推奨の根拠: 確定値15は「プロンプト変更のたびにゲートを再実行できる状態」を要求しており、変更が即コード差分(とピン留め試験の失敗)として現れる単一の定数が最も追跡性が高い。Cは変更が無音に起こり得るため不採用。Bも動作するが、確定済み仕様(07 §2)の実体がLLM接続層に埋まり、G1のゲートharness(T3)や補完規則との対応が見えにくくなる。Aにおいて実プロバイダはT1確定後、構成時(main/lifespan)にこの定数を注入されて使う(llm/からintents/へのimportも不要・§2.2と同じ依存方向)。

トレードオフ: 07 §2が改版されるとピン留め試験が落ちる。これは意図した挙動である — プロンプト変更は仕様変更であり、設計書の更新とG1両ゲートの再実行(09 §4.3)を要する事象として扱う。M1のスタブ実行時にはランタイム消費者を持たない(消費者=将来の実プロバイダとゲートharness)が、「確定済みシステムプロンプト実装」(12 M1-2)の成果物そのものである。

### 2.7 プロセス統合・エラー処理 — 推奨: ws-1と対称(service+app.state+ハンドラ1個+独立スキップ判定)

- **`IntentParseService`**: ユースケース `parse(text, auth_provider, auth_subject) -> ParseResult`。コンストラクタは `clock`・`parser`(§2.2のProtocol)・`user_lookup`(§2.3のCallable)を注入。フロー: (1)user_lookup(失敗→503 / None可)→ (2)`parser.parse_intent(text, current_date=clock.jst_date(), user_id=...)`(LLMError系→503 LLM_UNAVAILABLE)→ (3)規則5正規化(§2.5)→ (4)`ParserOutput.model_validate`(ValidationError→422)→ (5)warnings構築。claimはルータから**プリミティブ(provider/subject)で受ける**(サービスはauthの型をimportしない)
- **`make_intent_parse_service(*, clock, settings, user_lookup)`**: `build_llm_gateway(clock, settings)` を呼ぶ構築関数(llm/のimportはここに限る・サービス本体はLLM非依存)
- **app.state.intent_parse_service** へ載せ、`create_app` にテスト注入用引数を追加。lifespanは**サービスごとの独立スキップ判定**へ変更する(ws-1設計§2.5と同一方向。engineの構築はauthと共有ヘルパで取得する)。**このlifespan改変がws-1との唯一の衝突点**であり、マージ時は両側追記保持(M0 settings.pyと同一の解消方法)
- **`intents/errors.py`**: `IntentsError` 基底(http_status・code属性)+ `LLMUnavailableError`(503 LLM_UNAVAILABLE)+ `UnstructurableError`(422 VALIDATION_ERROR)+ `DependencyUnavailableError`(503)。main.pyにハンドラを1個追加(auth・usersと同型・数行)。ws-3はこの基底へGEOCODING_FAILED等を追加して拡張する
- **ルータ**: `APIRouter(prefix="/v1/intents", dependencies=[Depends(require_authenticated)])`(確定値12・C3)。textは `Field(min_length=1, max_length=300)`(空文字=必須欠落相当。ws-1のdisplay_nameと同型・切り詰めなし)

### 2.8 採用しないもの(YAGNIによる切り捨て一覧)

- 実プロバイダadapter・httpx・API鍵設定・llm_mode="real"(T1後。§1.4)
- parse応答への補完適用(time.end・radius_m等の埋め戻し)— 05 §5応答例がnullのまま返すことを確定しており、補完の消費はws-3(保存)とws-5(UI表示)である(確定値5・10)
- parse結果のキャッシュ・同一textの再parse省略(docsに規定なし。レート制限はws-4)
- 時刻検証(過去不可・+7日)・人数の値域検証・budgetの負数検証・location.name以外の地理検証(保存時・ws-3。確定値16。docsにparse側の規定なし)
- 構造化フォームAPI(フォールバックフォームはクライアント側機能・03 §3)
- レート制限ミドルウェア(ws-4)・p95計測(M4)
- ORM・リポジトリ層(parseはDB書込みゼロ・lookup 1本は関数注入。ws-3で再判断)
- llm/・auth/・core/・geo/・worker/への変更(§1.3・§3.3)
- 新設定項目(llm_mode等で足りる)・依存追加(pydantic・既存で足りる)

## 3. ファイル構成

### 3.1 作るもの

```text
backend/src/latch/intents/
├── __init__.py        # 公開IFの再export(IntentParseService, make_intent_parse_service, 例外)
├── prompt.py          # PARSER_SYSTEM_PROMPT(07 §2全文・{current_date}プレースホルダ1箇所)
│                      #   + format_parser_system_prompt(current_date)->str(ISO日付で差し込み)
├── schema.py          # ParserOutput(pydantic・07 §2出力JSONスキーマ)・部分モデル
│                      #   (ParserCategory/ParserTime/ParserLocation/ParserBudget/ParserParticipants)
│                      #   + WARNING_MESSAGE_NG_DOWNGRADED 定数(03 §3の文言)
├── completion.py      # D-19アプリ層補完の単一規則(§2.6・確定値5・6。すべて純粋関数)
├── errors.py          # IntentsError(基底)+ LLMUnavailableError(503)/
│                      #   UnstructurableError(422 VALIDATION_ERROR)/ DependencyUnavailableError(503)
├── service.py         # SupportsParseIntent(Protocol)・IntentParseService(parse)・
│                      #   make_intent_parse_service(clock, settings, user_lookup)
│                      #   (build_llm_gatewayの呼び出しはこのファクトリに限る)
└── routes.py          # parse_router(prefix="/v1/intents"・require_authenticated)・
                       #   ParseRequest(text: 1〜300字)/ParseResponse・get_parse_service依存
backend/tests/unit/intents/
├── test_prompt.py     # §4.1-1 全文ピン留め・フォーマッタ
├── test_schema.py     # §4.1-2 受入・拒否・既定値
├── test_completion.py # §4.1-3 補完規則(JST境界含む・FakeClock)
├── test_service.py    # §4.1-4 ユースケース(スタブProtocol+実Gateway+StubLLM)
└── test_routes.py     # §4.1-5 ルーティング(httpx・スタブサービス注入・
                       #   require_authenticatedはdependency_overrides)
backend/tests/integration/
└── test_intents_parse_api.py  # §4.2 実HTTP・compose常設api(イメージ再ビルド後)
```

インターフェースの要旨(実装詳細は計画書・TDDで確定):

```python
# prompt.py
PARSER_SYSTEM_PROMPT = "...07 §2の全文(規則1〜7・出力JSONスキーマを含む)..."
def format_parser_system_prompt(current_date: date) -> str: ...

# schema.py — 07 §2出力JSONスキーマ(「この形式のみ認める」)
# 必須3フィールド(category/time.start/location.name)は検証必須・既定なし。
# budget/participants/3配列はLLMが省略しても既定で受ける(規則1「抽出できる
# フィールドのみ埋める」への寛容な解釈。欠けても422にしない)。
# 余分なキーは無視する(失敗とするのは欠損・値域外・パース不能のみ — 07 §4の
# 失敗分類に揃える。余分キーは失敗に挙げられていない)。time.startはtz-aware必須
# (naive拒否。オフセットはJSTに限定しない)。
# flexibility_minutes/location.flexibilityはnullのみ受容(03 D-19の固定扱い)。
class ParserOutput(BaseModel):
    category: ParserCategory            # primary: Literal["meal","drinking","activity"]
    alcohol_involved: bool              # 常に出力(07 §2規則7)・補完なし
    time: ParserTime                    # start: datetime(tz-aware) / end: datetime|None
    location: ParserLocation            # name: str(min_length=1) / radius_m: int|None
    budget: ParserBudget = ...          # max: int|None / currency: Literal["JPY"]
    participants: ParserParticipants = ...  # min/max: int|None
    soft_constraints: list[str] = []
    negative_constraints: list[str] = []
    ng_unverifiable: list[str] = []

# completion.py — D-19補完の単一実装(消費はws-3保存経路・ws-5 UI計算)
DEFAULT_RADIUS_M = 1000
DEFAULT_PARTICIPANTS = (2, 2)
def default_time_end(time_start: datetime) -> datetime: ...      # +3時間
def expires_at_candidates(now: datetime) -> list[datetime]: ...  # 4値(JST基準3つ+now+72h)
def nearest_expires_at(time_start: datetime, now: datetime) -> datetime: ...
    # nowより未来の候補から|候補-(time_start+3h)|が最小の値(同点は最早)

# service.py
class SupportsParseIntent(Protocol):
    async def parse_intent(self, *, text: str, current_date: date,
                           user_id: str | None = None) -> dict: ...

@dataclass(frozen=True)
class ParseWarning:
    code: str          # "NG_CONDITION_DOWNGRADED"
    condition: str
    message: str       # WARNING_MESSAGE_NG_DOWNGRADED

@dataclass(frozen=True)
class ParseResult:
    structured_intent: ParserOutput
    warnings: list[ParseWarning]

class IntentParseService:
    def __init__(self, *, clock: Clock, parser: SupportsParseIntent,
                 user_lookup: Callable[[str, str], Awaitable[uuid.UUID | None]]) -> None: ...
    async def parse(self, *, text: str, auth_provider: str,
                    auth_subject: str) -> ParseResult: ...
        # user_lookup失敗→DependencyUnavailableError(§2.3)
        # LLMTimeoutError/LLMProviderError→LLMUnavailableError(503)
        # 規則5正規化(§2.5)→ValidationError→UnstructurableError(422)
        # warnings: ng_unverifiableの要素ごとにNG_CONDITION_DOWNGRADEDを1件

def make_intent_parse_service(*, clock: Clock, settings: Settings,
                              user_lookup) -> IntentParseService: ...

# routes.py
parse_router = APIRouter(
    prefix="/v1/intents",
    tags=["intents"],
    dependencies=[Depends(require_authenticated)],  # C3(確定値12)
)

@parse_router.post("/parse")  # POST /v1/intents/parse(05 §5)
async def parse_intent(body: ParseRequest, ...) -> ParseResponse: ...
```

### 3.2 触るもの(既存ファイルへの変更)

- `backend/src/latch/main.py` — (1) parse_routerのinclude、(2) lifespanでintent_parse_serviceを構築しapp.stateへ載せる(**サービスごとの独立スキップ判定へ変更**。engineはauthと共有で取得)、(3) `IntentsError` ハンドラを1個追加、(4) `create_app` に `intent_parse_service` テスト注入引数を追加。**これらが本単位の既存コードへの全変更**であり、ws-1との交点もここだけ(ws-1設計§3.2と同じ。マージ時は両側追記保持)

### 3.3 触らないもの

- `backend/src/latch/llm/`(Gateway・StubLLM・送信記録 — **無差分**。§1.3)
- `backend/src/latch/auth/`(require_authenticated・make_user_lookup・tokens すべて不変・参照のみ)・`core/`(clock・db・deps)・`geo/`・`worker/`
- `backend/src/latch/users/`(ws-1の並走成果物)
- `backend/alembic/`(マイグレーション追加なし — parseはDB書込みゼロ)・`backend/src/latch/settings.py`(新設定なし)・`pyproject.toml`/`uv.lock`(依存追加なし)
- `compose.yaml`・`Makefile`・`Dockerfile`・`.mise.toml`
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/M0/`・`docs/plans/M1/ws-1-design.md`(並走成果物)・`prototype/`・`README.md`・`.claude/`

## 4. テスト方針

### 4.1 unit(`make test`。外部プロセス不要・FakeClock+スタブで決定的)

1. **プロンプト**: `PARSER_SYSTEM_PROMPT` が07 §2の全文と一致することをピン留め(規則1〜7・出力スキーマ・プレースホルダ{current_date}を含む)。フォーマッタがISO日付(YYYY-MM-DD)を差し込み、`{` が残らないこと
2. **ParserOutputスキーマ**: (a)受理 — スタブ既定応答(llm/stub.py DEFAULT_PARSER_RESPONSE)・フルフィールド・最小値(budget/participants/配列の省略は既定で受理)・余分キーの無視、(b)拒否 — category欠落/primaryが3値外、time欠落/startがnaive、location欠落/name=""、alcohol_involved欠落、flexibility_minutesへの非null、currency="USD"。拒否はすべてValidationError(→サービスで422へ)
3. **補完規則(completion.py)**: default_time_end=+3時間・DEFAULT_RADIUS_M=1000・DEFAULT_PARTICIPANTS=(2,2)。expires_at_candidates: JST基準の3値(今夜23:30=当日JST 23:30等)とnow+72hの4値・順序。nearest_expires_at: time_start+3h前後の最近傍・**過ぎた候補の除外**(JST 23:45で「今夜23:30」が除外される境界をFakeClockで再現)・同点は最早・time_startが過去の場合(最早の未来候補)。すべてFakeClock由来の時刻で決定的
4. **IntentParseService**: (a)ハッピー — Protocolスタブが既定応答→ParseResult・warnings=[]、ng_unverifiable複数→**要素ごとに1件のwarning**(code/condition/message検証)。(b)規則5正規化 — negative_constraints非空の応答→同要素がng_unverifiableへ移りnegative_constraints=[]・warningsも出る。(c)503切替 — 実Gateway+StubLLM(fail_parser=True→LLMProviderError・遅延注入>短縮timeout→LLMTimeoutError)でLLMUnavailableError(503)。(d)422切替 — スタブ応答から必須3を1つずつ欠いた応答・JSON文字列化した応答でUnstructurableError(422)。(e)user_lookup — User行あり→str(id)がparserへ渡ること(スタブが受け取った引数で検証)、None→user_id=Noneで呼ばれる、例外→DependencyUnavailableError(503)。(f)current_date — FakeClockをJST/UTC境界(UTC 15:00/16:00)へsetし、渡されるcurrent_dateがJST暦日付であることを検証(確定値13)
5. **ルーティング**(create_app+スタブサービス注入+`app.dependency_overrides[require_authenticated]`): 200応答形状(トップレベル `{structured_intent, warnings}`・structured_intentが07スキーマ同形・warningsの入れ子)・text欠落/空文字/301字→422 VALIDATION_ERROR(envelope)・JSON破損→400 MALFORMED_REQUEST(既存ハンドラ)・スタブがLLMUnavailableError→503 `code=LLM_UNAVAILABLE` envelope・UnstructurableError→422 envelope・`require_authenticated` を401を発生させる依存へ上書きした場合のUNAUTHENTICATED envelope
6. **arch規律**: 既存 `test_arch_no_direct_time.py` がintents/配下を自動スキャン(ヒットなし)

### 4.2 integration(`make test-ci`。compose常設api(127.0.0.1:8000)。**apiイメージ再ビルド後**に実行)

| # | 検証 | 根拠 |
|---|---|---|
| 1 | **ハッピーパス**: ツール発行IdPトークン→POST /v1/auth/token→POST /v1/intents/parse `{"text": "..."}`→**200**。structured_intent=スタブ既定応答と同形(category.primary・tz-aware ISO8601のtime.start・location.name・alcohol_involved含む)・warnings=`[]` | 05 §5(確定値10・17) |
| 2 | text=301字→422 envelope `code=VALIDATION_ERROR`(切り詰めなし・300字ちょうどは200) | 07 §2(確定値3) |
| 3 | text欠落・JSON破損→422 VALIDATION_ERROR / 400 MALFORMED_REQUEST | 05 §5(確定値11) |
| 4 | Authorization欠落・改ざんJWT→401 `code=UNAUTHENTICATED` | 05 §5(確定値11・12) |
| 5 | **未登録subject**(User行のないIdPトークン→token→parse)→200(User行をparseの前提にしない・§2.3のuser_id=None経路) | §2.3・05 §5 |
| 6 | 事後処理不要(parseはDB書込みゼロ・行が残らない) | 05 §5 SP-4 |

503/422の構造化不能経路はスタブの設定経路がないためunit(§4.1-4/5)で証明する(M0 ws-3と同じ振り分け)。送信記録(latch.llm.send)は別プロセスの出力のためunitで証明済み(M0試験)でありintegrationでは検証しない。

### 4.3 既存資産への回帰

`make lint`・`make test`(既存unit+auth/llm/geo全件)・`make test-ci`(既存270+新規)がグリーン。llm/・auth/・geo/への差分がないためGateway・認証・geo試験への影響なし(main.pyのlifespan変更は、ASGITransportがlifespanを実行しないため既存unit試験に影響しない — ws-1設計§4.3と同一の根拠)。

## 5. 完了条件(この単位の受渡し判定。計画書が参照する)

1. `make lint`・`make test` がグリーン(unit/intents 追加分を含む)
2. `make test-ci` で §4.2-1〜6 がグリーン(実行前にapiイメージを再ビルドしていること。**マイグレーション追加なしのためSTATUS運用ルールのtest-ci同時実行制約には無関係**)
3. D-17の応答切替(503 LLM_UNAVAILABLE / 422 VALIDATION_ERROR)・D-04 warnings生成・規則5正規化・user_id帰属がunit試験で立証済み(§4.1-4/5)
4. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが引き続き `core/clock.py` のみ
5. `llm/`・`auth/`・`core/`・`geo/`・`worker/`・`alembic/`・`settings.py`・`pyproject.toml`・`uv.lock`・`compose.yaml`・`Makefile` に差分なし(§1.3・§2.8)
6. 触るファイルが §3.2・§3.1 の一覧どおり(新規は intents/ とテスト・既存はmain.pyのみ)

## 6. 未解決の論点(設計は推奨で固定済み。supervisor確認事項)

1. **user_id帰属のためのDB参照**(§2.3): parse経路に索引済みSELECT 1本を入れ、DB障害時は503 DEPENDENCY_UNAVAILABLEでfail-closedとした。LLM自体は健在でもDB落ちだとparse不能になる。帰属を諦めてNoneにする構成(B)も可能であり、M0 ws-2 §6-2の委託事項の決定として承認を求める
2. **LLM出力の検証失敗を422へ一元**(§2.4): docsは2区分のみ規定し、スキーマ不適合出力の区分は未規定。422(フォーム誘導)に一元したが、一過性の出力劣化を503(再試行)へ振る構成もあり得る
3. **規則5違反の正規化**(§2.5): negative_constraints非空をng_unverifiableへマージする書き換えを行う。LLM出力の改変を嫌う場合はパススルー+B案の検討になるが、その場合FR-42不変式の破れが下流へ流れる
4. **未登録JWTでのparse許可**(§2.3・§4.2-5): 05 §5は「全API認証済みユーザーのみ」を要求するだけで、User行の存在をparseの前提とはしていない。profile_complete:falseのトークンでもparse可と解釈した(user_id=Noneで記録)
5. **time.startのタイムゾーン**(§3.1): tz-awareを必須とするがJST(+09:00)限定にはしない(オフセット違いは保存時のws-3側で扱う)。prompt例はJSTだが、LLMの揺れで422にする根拠がないためである
6. **main.py lifespan改変のws-1との衝突**(§2.7): 両単位が同一箇所(lifespanのスキップ判定)を「サービスごとの独立判定」へ改変する。マージ時は両側追記保持で解消する想定(M0 settings.py・ws-1設計§3.2と同一)。競合の解消はスーパーバイザーが実施する
