# M1 ws-6(実プロバイダadapter + G1精度ゲートharness)設計メモ

- 作業単位: ws-6 — 実プロバイダadapter(Parser=Anthropic Haiku 4.5・llm_mode=real・鍵はLATCH_ANTHROPIC_API_KEY)+G1精度ゲートharness(Parser入力セット+飲酒判定セットをdocs/testassets/で実行・合格基準は07 D-17/09 §4.3)(docs/plans/STATUS.md M1表 / 出典 12 M1完了条件・G1条件2〜4 / 07 §1〜§2・09 §4.3・T1 v0.2 / 依存: M0 ws-2 Gateway=マージ済み・T1 Parser契約=2026-09-28確定)
- 作成: 2026-09-28(agent1 / superpowers:brainstorming使用)
- 次工程: 計画書(ws-6-plan.md)へ変換 → 実装エージェントがworktree内でTDD実装
- 並走: なし(M1残単位)。**マイグレーション追加なし・DB/Redisに触れない**ためSTATUS運用ルールのtest-ci同時実行制約には無関係
- スコープ外(supervisor補足): gemini・typesafe鍵は未設定 — Embedding/Jev系統の実adapterはM2。API鍵の実値を設計書・コード・ログに書かない(キー名のみ)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M1完了条件(G1)の残る2項目 — Parser構造化精度ゲート(07 D-17)とalcohol_involved精度ゲート(09 §4.3)— を**実測可能にする**。具体的には(1)M0 ws-2のGatewayプロバイダ抽象へT1契約確定済みの実adapter(Anthropic Claude API・Haiku 4.5)を差し込み`llm_mode="real"`を有効化する、(2)docs/testassets/の確定済み入力セット(g1-parser-struct.yaml 32件+エラー付帯3件・g1-alcohol.yaml 36件・status=confirmed)をGateway経由で実行し、フィールド別一致率とalcohol recall/precisionを測定・レポート化するharnessを作る。**ゲートの実測実行とG1合否判定は人間領域**(スーパーバイザー/ユーザー)であり、本単位の受渡しは「harnessが動作し、誰でも1コマンドで再実行できる状態」まで(12 §5「プロンプト変更のたびに両ゲートを再実行できる状態」の実行部の実装)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | Parser系統のプロバイダは**Anthropic Claude API・Haiku 4.5**(T1 v0.2推奨・2026-09-28に契約確定。LATCH_ANTHROPIC_API_KEYを.env〔git管理外〕に設定済み)。精度ゲートを通るかで確定。不合格ならSonnet 5へ統一 | 07 §1表 / T1 v0.2 §3・§5 / STATUS「現在」 |
| 2 | Parser呼び出し特性: **同期(ユーザーが待つ)・timeout 10秒・再試行なし(即フォールバックへ)**。性能目標p95 3秒/p99 8秒(目標値であり実装で強制する値ではない) | 07 §1表・§5 D-17 |
| 3 | LLM呼び出しは単一のLLM Gateway経由。送信記録は送信先・データ種別・時刻を含み**内容を含まない**(どのIntentの何種別をいつ送ったか)。問い合わせ時に開示できる状態を保つ | 04 §2 / 08 §3 / 12 §2 C4 |
| 4 | 構造化ログは許可リスト方式。API鍵・raw_text・条件系フィールドは不許可。例外・エラーはID等の最小情報のみ | 08 §2.4 |
| 5 | システムプロンプト(全文)は07 §2で確定済み。`{current_date}` に現在日付を渡す。**出力は指定のJSONスキーマのみ認める**(説明文を含めない) | 07 §2 |
| 6 | 必須3フィールド抽出不能=422 VALIDATION_ERROR(フォームフォールバック)/ LLM障害(timeout・API障害・レート制限)=503 LLM_UNAVAILABLE(再試行ボタン)。検証はParserOutput.model_validateに一元(スキーマ不適合出力は422へ一元・M1 ws-2 §2.4) | 07 §2・§5 D-17 / M1 ws-2 design §2.4 |
| 7 | Parser構造化精度ゲート: 入力セット30件以上で**category 85% / time.start 90% / location 90%(地域の一致で判定、座標の厳密一致は求めない)/ participants 80% / budget 90%**(丸め規則適用後の値で判定) | 07 §5 D-17 / 09 §4.3 |
| 8 | alcohol_involved精度ゲート: **recall 100%(見逃し0件)・precision下限90%**。基準の再実行タイミングは構造化精度ゲートと同一(Parser変更時の毎回) | 09 §4.3(FR-20) |
| 9 | 検証手順: 「入力セットをParserへ流す → 出力と期待値をフィールド単位で照合 → 完全一致率を集計 → 07 D-17の基準と判定」。リリース前ゲートであり、Parserやプロンプトを変更するたびに再実行する | 09 §4.3 |
| 10 | ゲート入力=docs/testassets/(10 §2の共有資産・バージョン管理)。g1-parser-struct.yaml(測定対象32件+エラー付帯3件)・g1-alcohol.yaml(36件=true24/false12)・**status=confirmed**(2026-09-28確定)。基準日時2026-10-01T11:30 JST({current_date}へ注入)。期待値は**Parser出力段階の値**(アプリ層補完前)。locationは表記揺れ同一地域を一致扱い。エラーケースは422が返ることを確認(一致率分母外) | docs/testassets/README.md・各YAML / 10 §2 |
| 11 | ゲートの合格証拠はdocs/testassets/results/へ保管(試験ID・実施日・環境・入力条件・合否) | 10 §5 / 12 §5 |
| 12 | 精度ゲート不成立の打ち手は07のプロンプト改善のみ。運用パラメータをゲート回避のために変えない | 12 §7 |
| 13 | 実プロバイダは`build_llm_gateway`の分岐に追加(M0設計どおり・Gateway IF不変)。実プロバイダは構成時(ファクトリ)に`PARSER_SYSTEM_PROMPT`定数を注入されて使う | M0 ws-2 design §2.3・§1.4 / M1 ws-2 design §1.3・§2.6 / intents/prompt.py docstring |
| 14 | SDK既定のbackoff retryはtimeout予算を圧迫するため無効化する(Jev/TypeSafe SDKに関する07 §1の先例 — 本設計はanthropic SDKのmax_retries=0で同じ規範を適用) | 07 §1(v0.5の再試行規定) |
| 15 | negative_constraintsは常に空配列。正規化(規則5違反の回復)はparse境界=IntentParseService内(M1 ws-2 §2.5) | 07 §2 / M1 ws-2 design §2.5 |
| 16 | テスト入力文言は生産データではないためログ制限の対象外 | 09 §4.1 |
| 17 | Parserの縮退先はBedrockのClaude(東京)→OpenAI(T1 §5)— **本単位では作らない**(Gatewayの差し替え抽象で将来対応) | T1 v0.2 §5 |
| 18 | 実API呼び出しは通常のtest-ciでは走らない構成であること(コスト・鍵依存の分離。harnessの実行経路は設計で決めてよい) | supervisor補足(ws-6固有) |

### 1.3 現状資産の確認(実装が乗る基盤)

- `LLMGateway.parse_intent(*, text, current_date, user_id=None) -> dict`(llm/gateway.py)— timeout 10秒(asyncio.timeout)・再試行なし・成否毎のSendRecord出力・例外はLLMTimeoutError/LLMProviderErrorへwrap済み。**Gateway本体・records・stub・providers ABC・errorsは本単位で無変更**
- `build_llm_gateway(clock, settings)` は `llm_mode != "stub"` をValueErrorで拒否(実装待ちの分岐)。拡張点はこの関数のみ
- `StubLLM`(llm/stub.py)— 応答注入・遅延・常時失敗フラグ。Embedding/Jevは本単位でも継続利用(real時もスタブのまま)
- `PARSER_SYSTEM_PROMPT` + `format_parser_system_prompt(current_date)`(intents/prompt.py)・`ParserOutput`(intents/schema.py・model_validateが検証関門)・`IntentParseService`(規則5正規化+検証+warnings。parserは`SupportsParseIntent` Protocolで注入)・`make_intent_parse_service(clock, settings, user_lookup)`(llm/へのimportはこのファクトリに限る)
- Settingsは`env_prefix="LATCH_"`・**env_file未指定**(ローカルの.envはdocker composeの変数展開用。プロセスへは環境変数で渡す必要がある)
- compose.yamlのapi/workerへはLATCH_LLM_MODE・LATCH_ANTHROPIC_API_KEYを渡していない → **ci環境は常にstubのまま**(test-ciの実API非依存は構成で保証される)
- Makefile慣行: `python -m latch.geo`(モジュールCLI)+makeターゲット(geo-import/geo-verify)。uv 0.12.19は`--env-file`対応を確認済み

## 2. 実装方式の選択肢と推奨

### 2.1 HTTPクライアント — 推奨: Anthropic公式SDK(`anthropic`)

- **A(推奨). `anthropic` SDKの`AsyncAnthropic`クライアント**(Provider IFはasync)
- B. httpxでMessages APIを直実装

推奨の根拠: T1 v0.2の評価・契約面の前提は公式API仕様。SDKは(a)型付き例外(RateLimitError・APIStatusError・APIConnectionError/APITimeoutError)でエラー分類が確定、(b)`max_retries=0`とクライアント`timeout`が制御点として明示的(確定値14の規範を設定値で実現)、(c)structured outputs(§2.2)の要求形状(`output_config.format`)をSDKが保証。Bはエラー分類(429/5xx/timeoutの区別)とAPI改版への追従を自前で抱え込み、確定値3・4(送信記録・ログ許可リスト)を守る例外経路の実装量が増える。

トレードオフ: 本番依存が1個増える(anthropic 1.x。httpx2ベース — dev依存のhttpx〔テスト用〕と併存するが混ぜない)。Dockerfileの`uv sync --frozen --no-dev`が自動的に拾う(イメージ側の変更は不要)。

### 2.2 出力JSONの強制 — 推奨: structured outputs(`output_config.format` の json_schema)

- **A(推奨). `messages.create(model, system, messages, output_config={"format": {"type": "json_schema", "schema": ...}})` でAPI側に出力JSONを保証させる**
- B. プロンプト指示のみ+応答テキストのJSONパース(コードフェンス等の前後掃除つき)

推奨の根拠: 07 §2は「出力は指定のJSONのみ認める」を**要件**として課し、実装手段は規定しない。Aはこの要件をAPI側で構造的に保証し、(1)出力揺れによる422 VALIDATION_ERROR(構造化不能)経路を構造的にほぼ排除 — ゲートは精度そのものを測ることになる、(2)テキスト掃除(フェンス・前置き文)という推測的コードが不要、(3)T1 v0.2が評価基準3(機能)に structured outputs 対応を挙げた採用理由を活用。応答は「最初のtextブロックがvalid JSON」(SDK保証)なので`json.loads`→dictでProvider契約(`complete_structured -> dict`)を満たす。

**スキーマの供給源は `ParserOutput.model_json_schema()`(intents/schema.py)を単一の真実とし、make_intent_parse_serviceが注入する**(§2.3)。Anthropic側の制約(全プロパティrequired・`additionalProperties: false`等)でpydantic生成形($defs参照・OptionalのanyOf・既定値フィールドのrequired外)がそのまま使えない場合は、llm側に**機械的後加工関数を1つ**(required埋め・additionalProperties付与・$defs解決)を置く — 手書き複製ではなく生成スキーマからの決定的導出であり真実は単一のまま。後加工で対応できない制約が実装時に判明した場合のみ、07 §2対応の手書きスキーマ定数(llm/anthropic.py)へ切替し、単体試験で「スキーマ定数がParserOutputのフィールド・列挙と対応していること」を機械検証する(計画書でどちらに落ちたかを記録)。ParserOutput.model_validate(サービス層)が最終関門として残るため、スキーマとpydanticのずれは422として表面化し、黙って不一致にならない。

トレードオフ: プロンプト内スキーマ表記(07 §2全文に含まれる)とAPIスキーマの二重管理の懸念 — 供給源一元化+ピン留め試験+model_validate関門で「ずれは必ず検出可能」な状態にして抑える。Bはプロンプトだけでは出力保証が確率的である(確定値5の「のみ認める」をコードで強制できない)。

### 2.3 プロンプト・スキーマの注入経路 — 推奨: `build_llm_gateway` へキーワード引数追加(llm/はintents/非依存のまま)

- **A(推奨). `build_llm_gateway(clock, settings, *, parser_system_prompt: str | None = None, parser_output_schema: dict | None = None)`。`make_intent_parse_service` が `PARSER_SYSTEM_PROMPT` と `ParserOutput.model_json_schema()` を渡す**。`AnthropicParserProvider` は`{current_date}`入りの生プロンプトを受け取り、呼び出し毎にISO日付を差し込む(フォーマッタと同じreplace方式)
- B. llm/anthropic.py が intents.prompt を直接import(llm→intents依存)
- C. main.py lifespan が構築に介入

推奨の根拠: M0/M1で確立した依存方向(intents→llmのみ・llm/はドメイン知識を持たない)を維持する。intents/prompt.pyのdocstring「実プロバイダは構成時(main/lifespan)にこの定数を注入されて使う」の実体がこの形(注入者はファクトリ)。llm/は「プロンプト文字列とスキーマdict」だけを受け取り、intents/を知らない。Bはllm/がドメイン層へ依存し、Cはmake_intent_parse_service(clock, settings, user_lookup)という構造を壊す。stubモードでは引数は無視され、既存呼び出し(2引数)は後方互換で動く。

### 2.4 `llm_mode="real"` の系統別の扱い — 推奨: 単一llm_mode・Parserのみreal化

- **A(推奨). `llm_mode="real"` はParser系統のみAnthropicへ。Embedding/JevはStubLLM継続**(gemini・typesafe鍵は未設定・M2実装)
- B. 系統別モード設定(llm_mode_parser等)を今の段階で導入

推奨の根拠: supervisor補足のとおり本単位の対象はParserのみ。Embedding/Jevの実プロバイダとフォールバック制御はM2(12 M2スコープ2・6・10)で、系統別モードの必要性が実際に生じるのはその時。BはYAGNI。real分岐の検証: **`llm_anthropic_api_key` が空、またはプロンプト/スキーマ引数がNoneならValueErrorで起動拒否**(fail-fast。鍵の存在を静かに握りつぶさない)。

トレードオフ: 「real」の語感と実態(Parserのみreal・Embedding/Jevはstub)の差 → settings.pyのコメントと本設計書に明記し、M2で系統別へ拡張する際の引継ぎ事項とする(§6-4)。

### 2.5 timeout・再試行 — 推奨: SDK timeout=10秒・max_retries=0・Gateway執行層は無変更

SDKクライアントは `timeout=10.0`(GatewayのTIMEOUT_PARSER_Sと同値)・`max_retries=0` で構築(確定値2・14)。Gatewayの`asyncio.timeout(10秒)`・LLMTimeoutError/LLMProviderErrorへのwrap・SendRecord出力は既存実装のまま最終関門となり、APITimeoutError等のSDK例外はGatewayの既存の例外処理へそのまま流れる。**Gateway本体・records.py・stub.py・providers.py・errors.pyは差分ゼロ**(build_llm_gatewayの分岐拡張のみ)。SDK timeoutとGateway timeoutを同値にすることで、SDK側で過剰に待つ時間を作らない。

### 2.6 生成パラメータ — 推奨: temperature=0・thinkingなし・max_tokens=1024

07は温度・トークン上限を規定しない。よって実装値として次を固定する: **`temperature=0`**(抽出タスクの決定性 — 09 §4.3が「プロンプト変更のたびに再実行」を要求する以上、測定の再現性を最大化するのが合理。Haiku 4.5はtemperature許可モデル)。**thinkingは指定なし(=無効)** — 抽出タスクに逐次推論は不要で、p95 3秒目標とコストに逆行するため。**`max_tokens=1024`**(T1 v0.2試算の出力想定約0.4k+マージン。打ち切り防御のみで、課金は実出力ベース)。プロンプトキャッシュ(システムプロンプト約1.2kトークン)は付けない — 効果が最小キャッシュ単位(512〜4096トークン)前後で確実性が低く、T1 v0.2も「効かせていない保守値」で試算している(§6-1に記録)。

### 2.7 G1 harnessの実行経路 — 推奨: CLIモジュール(`python -m latch.g1gate`)+ `make g1-gate`(pytest外)

- **A(推奨). `latch/g1gate/` パッケージにCLIを作り、Makefileへ `g1-gate` ターゲット(`cd backend && uv run --env-file ../.env python -m latch.g1gate`)を追加**
- B. pytestに`g1_gate` markerを設け、デフォルト deselect で運用
- C. compose常設apiコンテナ内で実行

推奨の根拠: 確定値18(実APIはtest-ciで走らない)に対し、Aは**実API経路がpytestの通常実行(make test/test-ci)から構造的に存在しない** — marker漏れによる誤発火(Bのリスク)が原理的に起きない。起動時に`llm_mode=real`と鍵の有無を検証し、不足なら即座に拒否(誤実行防止)。geo-import/geo-verifyと同一の慣行(`python -m latch.geo`+makeターゲット)。Cは鍵をコンテナへ渡すcompose変更が必要で分離の逆行。実行の前提は`.env`へ`LATCH_LLM_MODE=real`と`LATCH_ANTHROPIC_API_KEY=<実値>`を書くこと(make経由のみで有効)。ci環境への影響はない — composeは.envを変数補間(${...})に使うが、compose.yamlのapi/workerのenvironmentに`LATCH_LLM_MODE`を書かないためコンテナへは渡らず、ciはllm_mode既定のstubのまま動く。

### 2.8 harnessの測定経路 — 推奨: Gateway+IntentParseServiceを本番ロジックそのまで通す(HTTP層を経ない)

- **A(推奨). harnessは `build_llm_gateway` + `IntentParseService` を直接構築して測定**。`clock=FakeClock(2026-10-01T11:30:00+09:00)`(基準日時・READMEの前提)・`user_lookup` は常にNoneを返す関数(未登録JWT経路 — M1 ws-2 §2.3と同一挙動。DB非依存でharnessを軽量化)
- B. 実HTTPでPOST /v1/intents/parseを叩く(httpx+テスト用JWT発行)
- C. Gatewayのみ呼ぶ(サービス層を通さず生dictを取得)

推奨の根拠: 精度ゲートが測るべきは「Parser(プロンプト+実プロバイダ)の出力」であり、規則5正規化・ParserOutput検証(422相当・エラー付帯ケースの確認に必要)・warnings生成までを含む本番ロジックをそのまま通るのがA。Bは認証・DB・Redisを実測経路に持ち込み、測定対象でないものへ鍵と依存が増える(routes層はprovider非依存でM1 ws-2 integrationがスタブで検証済み)。Cは422正規化・検証経路をharnessが再実装することになり二重実装(07 §2「二重実装による不整合を防ぐ」と同じ原則)。current_dateはFakeClock.jst_date()由来(=2026-10-01)で、YAMLの`meta.current_datetime`と起動時に一致検証(期待値の前提が崩れていないかの防御)。レポートの実行時刻(証拠の実施日)はSystemClockを別途注入して取得(arch testはsrc配下で実時刻参照を禁止するため、必ずClock経由)。

### 2.9 照合・集計 — 推奨: フィールド別完全一致・locationは双方向部分一致・閾値判定まで機械化

純粋関数モジュール(compare.py)として実装し、unit試験で全ロジックをスタブで立証(実API不要)。

- **category.primary**: Literal等値(drinking/meal/activity)
- **time.start**: tz-aware datetimeとして**瞬間等値**(+09:00とZ表記の揺れは吸収。期待値ISO文字列→datetime化)
- **location.name**: 空白正規化のうえ `expected ⊆ actual or actual ⊆ expected` の**双方向部分一致**(地域一致・確定値10。「天文館」vs「天文館界隈」=一致、「天文館」vs「呉服町」=不一致、「中央駅前」⊂「鹿児島中央駅前」=一致)
- **participants**: (min, max)組の等値(None含む)
- **budget.max**: None/intの等値
- 一致率=一致件数/測定対象32件。**フィールド別に集計**し、07 D-17の閾値(定数)と比較してPASS/FAIL
- **alcohol_involved**(g1-alcohol.yaml 36件): TP/FP/FN/TNを数え、**recall=TP/(TP+FN)=100%・precision=TP/(TP+FP)≥90%** を判定(09 §4.3)。g1-parser-struct.yaml側のalcohol_involved期待値はREADMEどおり**参考値として集計するが合否判定には使わない**
- **error_cases(3件)**: IntentParseServiceが`UnstructurableError`(=422 VALIDATION_ERROR相当)を上げることを検証。`LLMUnavailableError`(503相当)が上がったケースが1件でもあれば**実行不完全として全体FAIL**(分母を減らして集計しない — 入力セット全件への応答が得られて初めてD-17の「入力セットに対する一致率」が成立。再実行を促す)。error_casesの`missing`フィールドはペイロード設計(05)の参照情報であり照合対象外
- 判定はレポートへ機械記載するが、**G1合否の承認は人間**(運用: ゲート毎人間確認)

### 2.10 レポート — 推奨: YAMLをdocs/testassets/results/へ・exit codeで合否を返す

- 出力先: `docs/testassets/results/g1-result-YYYYMMDD-HHMMSS.yaml`(SystemClock実行時刻。10 §5の「試験ID・実施日・環境・入力条件・合否」に対応)。git管理(証拠)
- 内容: meta(実行時刻・llm_mode・モデルID・`PARSER_SYSTEM_PROMPT`のSHA256・入力ファイル名+SHA256・基準日時)/ parser_struct(閾値・フィールド別一致率・合否・ケース別詳細)/ alcohol(TP/FP/FN/TN・recall・precision・合否・ケース別詳細)/ error_cases(期待・発生・ok)/ overall_passed
- ケース別詳細には**actual(Parser出力全体=生応答)を含める** — テスト入力は生産データでなく機微制限の対象外(確定値16)。失敗ケースの分析(プロンプト改善の打ち手出し・確定値12)に必要
- CLI exit code: 全合格=0/不合格・実行不完全=1(実行結果の可視化)
- `--limit N` オプション(先頭Nケースのみ実行)を付け、実APIでの部分実行・デバッグを可能にする(集計・レポート形式は同一)

### 2.11 実行の直列化 — 推奨: 71呼び出し(32+36+3)を直列実行

並列化しない。精度測定が目的でレイテンシ計測はM4(12 M4-4)。Haiku 4.5の実測想定(1回あたり数秒)で全体3〜6分・コストはT1 v0.2単価($1/$5 per MToken)で**71回全体でおよそ$0.3未満(1回あたり約$0.004:入力約1.8k+出力0.4kトークン)**〔2026-09-28 supervisor検算により訂正。初稿は「$0.3未満/回」と100倍の誤りだった〕。直列はレート制限(RPM)との余裕も持つ。分布遅延・p50/p95測定はM4 load試験まで作らない。

### 2.12 採用しないもの(YAGNIによる切り捨て一覧)

- Embedding/Jev系統の実adapter・System One IF・フォールバックLLM・circuit breaker(M2。12 M2スコープ6・10・T1 §5)
- Parserの縮退先(Bedrock東京・OpenAI)への切替実装(T1 §5の縮退先はGateway抽象の差し替えで将来対応)
- 系統別llm_mode設定(§2.4-B。M2で必要になった時)
- プロンプトキャッシュ(§2.6。効果限定的・T1試算は非適用の保守値)
- pytest marker・実APIのunit/integration試験(§2.7-A。CLIのみ)
- HTTP層(routes)を通すreal試験(§2.8-B。routesはprovider非依存)
- harnessの並列実行・レイテンシ測定(§2.11。M4)
- トークン数・cost実測のレポート搭載(M4 Observability。usageはSDK応答にあるが本単位では使わない)
- ゲート入力セットの変更・追加(YAMLはconfirmed・読み取り専用。変更は09/10の改版を要する・12 §5)
- G1判定のSTATUS.md記載(スーパーバイザー領域)

## 3. ファイル構成

### 3.1 作るもの

```text
backend/src/latch/llm/anthropic.py    # AnthropicParserProvider(ParserProvider実装)。
                                      #   ANTHROPIC_PARSER_MODEL="claude-haiku-4-5"(バージョンID固定・
                                      #   jev-1.13.0と同じ規範。envに出さない)/ timeout=10.0・max_retries=0・
                                      #   temperature=0・max_tokens=1024 / name="anthropic"(送信記録の送信先)
                                      #   system={current_date}差し替え済みプロンプト・output_config(json_schema)
                                      #   応答: 最初のtextブロック→json.loads→dict(スキーマ検証は
                                      #   サービス層ParserOutputが関門・Providerでは再検証しない)
                                      #   例外: SDK例外を送出しGatewayの既存wrapに委ねる
backend/src/latch/g1gate/
├── __init__.py                       # 公開IFの再export
├── __main__.py                       # python -m latch.g1gate エントリポイント(引数解析・起動検証)
├── cases.py                          # docs/testassets/のYAML読み込み+pydantic検証
│                                     #   (meta.current_datetimeとFakeClock基準日時の一致検証含む)
├── compare.py                        # 照合・集計・閾値判定(すべて純粋関数。§2.9の規則)
├── runner.py                         # Gateway+IntentParseService構築(FakeClock基準日時・user_lookup=None)
│                                     #   ・ケース逐次実行(直列)・例外分類(Unstructurable/LLMUnavailable)
└── report.py                         # レポートYAML書き出し(meta・sha256・ケース詳細・合否)
backend/tests/unit/llm/
└── test_anthropic_provider.py        # §4-1(SDKクライアントをモック・実APIゼロ)
backend/tests/unit/g1gate/
├── test_cases.py                     # §4-3(docs実YAMLの構造回帰+フィクスチャ異常系)
├── test_compare.py                   # §4-4(照合・集計・閾値の全ロジック)
├── test_runner.py                    # §4-5(StubLLM応答注入で全経路・レポート生成)
└── test_report.py                    # §4-6(レポートYAML構造)
docs/testassets/results/              # 実行時に生成(git管理・10 §5の証拠置き場)
```

インターフェースの要旨(実装詳細は計画書・TDDで確定):

```python
# llm/anthropic.py
ANTHROPIC_PARSER_MODEL = "claude-haiku-4-5"  # T1 v0.2 Parser契約(2026-09-28)

class AnthropicParserProvider(ParserProvider):
    """Parser系統の実プロバイダ(07 §1・T1 v0.2)。name="anthropic"(08 §3送信記録)。"""
    def __init__(self, *, api_key: str, system_prompt: str,
                 output_schema: dict) -> None: ...
        # AsyncAnthropic(api_key, timeout=TIMEOUT_PARSER_Sと同値, max_retries=0)
    async def complete_structured(self, text: str, current_date: date) -> dict: ...
        # system=プロンプト.replace("{current_date}", current_date.isoformat())
        # messages=[{"role":"user","content":text}]
        # output_config={"format":{"type":"json_schema","schema":...}}
        # temperature=0, max_tokens=1024 → 最初のtextブロックをjson.loads→dict

# gateway.py(build_llm_gateway拡張 — 既存2引数呼び出しは後方互換)
def build_llm_gateway(clock: Clock, settings: Settings, *,
                      parser_system_prompt: str | None = None,
                      parser_output_schema: dict | None = None) -> LLMGateway:
    # "stub": 現状どおり(引数は無視)
    # "real": 鍵空/プロンプトNone/スキーマNone → ValueError(fail-fast)
    #          parser=AnthropicParserProvider(...)・embedding/jev=StubLLM(§2.4)

# intents/service.py(make_intent_parse_service — プロンプト/スキーマを渡すのみ)
gateway = build_llm_gateway(
    clock, settings,
    parser_system_prompt=PARSER_SYSTEM_PROMPT,
    parser_output_schema=ParserOutput.model_json_schema(),  # §2.2(要後加工ならllm側関数へ)
)

# settings.py(追記分)
llm_anthropic_api_key: str = ""  # T1 Parser契約。実値は.env(git管理外)・キー名のみ扱う

# g1gate は make g1-gate のみから実行(make test/test-ciには含まれない)
# make g1-gate:
#   cd backend && uv run --env-file ../.env python -m latch.g1gate \
#     [--assets ../docs/testassets] [--out ../docs/testassets/results] [--limit N]
```

### 3.2 触るもの(既存ファイルへの変更)

- `backend/src/latch/llm/gateway.py` — `build_llm_gateway` の分岐拡張(§2.3・§2.4)。LLMGateway本体は無変更
- `backend/src/latch/intents/service.py` — `make_intent_parse_service` がプロンプト/スキーマを渡す(数行)
- `backend/src/latch/settings.py` — `llm_anthropic_api_key` 1項目追記
- `backend/pyproject.toml`・`uv.lock` — `anthropic`(本番)・`pyyaml`(本番)追加。**依存追加を伴う唯一の単位(依存追加は本設計のスコープ内)**
- `Makefile` — `g1-gate` ターゲット追加(.PHONY行へも追記)
- `.env.example` — `LATCH_LLM_MODE` 行の追記(harness実行時のみ設定。コメントで「ci環境では設定しない」を明記)。※実値は既存 `.env` 側にスーパーバイザーが設定

### 3.3 触らないもの

- `llm/providers.py`・`records.py`・`stub.py`・`errors.py`(ABC・送信記録・スタブ・例外体系 — 無差分)
- `intents/prompt.py`・`schema.py`・`completion.py`・`routes.py`・`errors.py`(プロンプト全文ピン・ParserOutput・補完規則・ルーティング不変。G1条件4のピン試験も既存のまま)
- `main.py`・`worker/`・`auth/`・`core/`・`geo/`・`ratelimit/`・`users/`(real化はmake_intent_parse_service経由で自動・プロセス統合の変更不要)
- `backend/alembic/`(マイグレーション追加なし — DB書込みゼロ)
- `compose.yaml`・`backend/Dockerfile`・`docker/`(ci環境へ鍵・llm_modeを渡さない=stub継続。イメージは uv sync --frozen が新依存を拾うだけでDockerfile変更不要。test-ci実行時のapi再ビルドは運用ルール4の既存手順)
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/testassets/`の入力YAML2件(confirmed・読み取り専用)
- `.env`(git管理外・スーパーバイザー管理)
- `frontend/`・`prototype/`・`README.md`・`.claude/`

## 4. テスト方針

すべての新規試験はunit(`make test`)。**実APIを叩く試験はpytestに1つも作らない**(§2.7-A。実測はCLI=make g1-gateのみ)。

1. **test_anthropic_provider**(SDKクライアントを注入モック・公式SDKのレスポンス型を合成): (a)リクエスト構成 — model=ANTHROPIC_PARSER_MODEL・system={current_date}差し替え済み・messages=[text]・output_configのjson_schema注入値・temperature=0・max_tokens=1024・クライアントのtimeout/max_retries=0、(b)応答 — 最初のtextブロックのJSON→dict返却、(c)例外 — SDK例外(APIStatusError等)は素通し(Gateway wrapは既存試験が担保)、(d)鍵・プロンプト・スキーマなしの構築拒否
2. **test_llm_factory.pyへ追記**(既存ファイル・basename一意性を維持): `llm_mode="real"`+引数完備→AnthropicParserProvider内包(embedding/jevはStubLLM)・鍵なしValueError・引数欠落ValueError・`llm_mode="stub"`は引数がなくも従来どおり動く(後方互換)
3. **test_cases**: docs実YAMLの読み込み回帰(件数32/36/3・meta検証・id一意・期待値フィールド構造 — 入力セットの予期せぬ変更を検出)+tests/fixturesの小YAMLで異常系(欠落・形式不正)
4. **test_compare**(純粋関数・代表値): category等値・time.start瞬間等値(+09:00/Z表記)・location双方向部分一致(天文館/天文館界隈=一致・天文館/呉服町=不一致・中央駅前⊂鹿児島中央駅前=一致・空白正規化)・participants対(None含む)・budget None/int・フィールド別一致率集計・alcohol TP/FP/FN/TN→recall/precision・閾値境界(ちょうど85%/84.9%等)・alcohol参考値は判定外・error分類
5. **test_runner**(StubLLMへparser_response注入・実Gateway+実IntentParseService): ハッピーパス(全ケース応答→集計・レポート生成tmp_path)・422相当(UnstructurableError→error_cases扱い)・503相当(LLMUnavailableError→実行不完全FAIL)・規則5正規化の通過(negative_constraints非空の応答でもng_unverifiableへ移る=本番ロジック経由の証明)・`--limit`部分実行・FakeClockとmeta.current_datetimeの不一致検証
6. **test_report**: レポートYAMLの構造(metaのsha256・閾値・合否・ケース詳細にactualを含む)・exit code相当のoverall_passed
7. **arch規律**: 既存`test_arch_no_direct_time`がg1gate/・anthropic.pyを自動スキャン(実行時刻はSystemClock注入で、直参照なし)
8. **既存回帰**: `make lint`・`make test` 全緑(llm/intents既存試験は引数追加の後方互換で不変)・`make test-ci` 全緑(**apiイメージ再ビルド後**。ciはstubのため実API非依存の証明そのもの)

### make g1-gate(手動・実API・G1証拠採取)

スーパーバイザーまたは実装エージェントが実行。前提: `.env`に`LATCH_LLM_MODE=real`と`LATCH_ANTHROPIC_API_KEY=<実値>`(スーパーバイザーが設定。設計書・コード・ログに実値を書かない)。出力=docs/testassets/results/へのレポートYAML+exit code。全件実行(71呼び出し・直列)で測定を完結させ、レポートがG1判定(人間)への入力になる。実装単位の受渡し時には`--limit 5`程度のスモーク実行でharness動作(+実adapterの呼び出し経路)を証明し、全件実測はG1判定のタイミングで実施する(コスト・鍵の管理はスーパーバイザー)。

## 5. 完了条件(この単位の受渡し判定。計画書が参照する)

1. `make lint`・`make test` がグリーン(§4-1〜7のunit追加分を含む)
2. `make test-ci` がグリーン(apiイメージ再ビルド後・マイグレーション追加なし)。ci環境が実APIに依存しないこと(llm_mode既定stub・compose無変更)の確認
3. 起動検証がunit試験で立証済み(stubモード指定・鍵なしでの確実な拒否)。かつ実APIでのスモーク実行(`--limit 5`・レポート生成)が成功している
4. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが引き続き `core/clock.py` のみ
5. 依存追加が `anthropic`・`pyyaml` の2つのみ(`git diff` で pyproject.toml・uv.lock の差分がこの2追加に限ること)
6. 触るファイルが §3.2・§3.1 の一覧どおり(llm/はgateway.pyのbuild_llm_gateway拡張のみ・intents/はservice.pyの数行のみ・compose.yaml・Dockerfile無差分)
7. `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空(運用ルール5)

## 6. 未解決の論点(設計は推奨で固定済み。supervisor確認事項)

1. **temperature=0(§2.6)**: 07は温度を規定しない。ゲート再実行(09 §4.3)の測定安定性のためtemperature=0とする設計判断。未指定(API既定)や別値も選べる — 承認を求める
2. **structured outputs採用とスキーマ供給源(§2.2)**: 07 §2「出力は指定のJSONのみ認める」の実現手段としてAPI側強制(output_config.format)を採用。スキーマ供給源は`ParserOutput.model_json_schema()`(単一の真実)を推奨し、Anthropic側制約が原因で使えない箇所は機械的後加工(最悪時のみ手書き定数+対応検証試験)へ切替する**段階的判断**とした。プロンプト指示のみ+パースの構成(B)も技術的に成立する。実装時の切替を許す前提の承認を求める
3. **llm_mode=realの意味(§2.4)**: real=Parser系統のみ実プロバイダ化し、Embedding/Jevはスタブ継続(gemini・typesafe鍵未設定・M2実装)。M2で系統別モードへ拡張する引継ぎ事項として記録
4. **.envの読み込み経路(§2.7)**: Settingsはenv_file未指定のまま、Makefileのg1-gateターゲットが`uv run --env-file ../.env`で読み込む(既存Settingsの挙動を変えない・鍵をmake経路に局所化)。Settingsへenv_file=".env"を追加する構成は全設定の読み込み元が変わるため採らない
5. **alcohol_involvedの参考値の扱い(§2.9)**: g1-parser-struct.yaml側のalcohol_involved期待値は集計値としてレポートに載せるが合否判定には使わない(READMEの位置づけどおり、合否はg1-alcohol.yamlのみ)
6. **error_casesの検証範囲(§2.9)**: 「UnstructurableError(422相当)が上がること」まで。応答ペイロードのmissingフィールド照合は行わない(05の422 envelopeに欠落フィールド情報がなく、missingは参照情報)
7. **レポートの生応答保存(§2.10)**: Parser出力全体をケース別詳細に含める(テスト入力は機微制限対象外・09 §4.1)。失敗分析に必要。懸念があればactualをフィールド単位の照合結果に制限する形にもできる
