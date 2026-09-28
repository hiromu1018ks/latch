# M1 ws-6(実プロバイダadapter + G1精度ゲートharness)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M1完了条件(G1)の残る2項目 — Parser構造化精度ゲート(07 D-17)とalcohol_involved精度ゲート(09 §4.3)— を実測可能にする。(1)M0 ws-2のGatewayプロバイダ抽象へ、T1契約確定済みの実adapter(Anthropic Claude API・Haiku 4.5)を差し込み`llm_mode="real"`を有効化する(Parser系統のみ。Embedding/JevはStubLLM継続)、(2)docs/testassets/の確定済み入力セット(g1-parser-struct.yaml 32件+エラー付帯3件・g1-alcohol.yaml 36件・status=confirmed)をGateway+IntentParseServiceの本番ロジックで実行し、フィールド別一致率とalcohol recall/precisionを測定・レポート化するharness(`python -m latch.g1gate`+`make g1-gate`)を作る。**ゲートの全件実測とG1合否判定は人間領域**(スーパーバイザー/ユーザー)。本単位の受渡しは「harnessが動作し、誰でも1コマンドで再実行できる状態」まで(12 §5)。

**Architecture:** 実adapterは`llm/anthropic.py`の`AnthropicParserProvider`(公式SDK・structured outputsで出力JSONをAPI側保証・スキーマは`ParserOutput.model_json_schema()`を単一の真実として機械的後加工)。`build_llm_gateway`へキーワード引数`parser_system_prompt`/`parser_output_schema`を追加し(`llm/`はintents/非依存のまま)、`make_intent_parse_service`が`PARSER_SYSTEM_PROMPT`と生成スキーマを注入する。harnessは`latch/g1gate/`パッケージのCLI(cases=YAML読み込み+pydantic検証 / compare=純粋関数の照合・集計・閾値判定 / runner=Gateway+IntentParseService実行(直列) / report=YAML証拠書き出し)。実API経路はCLIのみに存在し、pytest(`make test`/`make test-ci`)は構造的に実API非依存(ci環境はllm_mode既定stubのまま)。

**Tech Stack:** Python 3.13 / FastAPI既存資産(LLMGateway・IntentParseService・FakeClock)/ anthropic SDK 1.x(httpx2ベース・新規本番依存)/ pyyaml(新規本番依存・harnessの入出力)/ pytest(既存・unitのみ)

**Spec:** `docs/plans/M1/ws-6-design.md`(本計画はdesign §2〜§5をタスク分解する。design §6の7件は2026-09-28 supervisor承認済み=未解決論点なし。§2.11のコスト数値はsupervisor検算済み訂正版)

---

## 0. 作業規律(worktree・コミット・環境)

1. **着手条件**: 本単位のworktreeは**ws-5(フロントエンド)マージ済みのmain**から切る。着手前に次を確認し、全て成立していることを報告書に記録する。成立していない場合は作業を中断し「BLOCKED: 前提不成立(<内容>)」と報告する。
   - `git log --oneline -8` でws-5マージコミット(b9769ff)が見えること
   - `ls backend/src/latch/llm/` で `__init__.py errors.py gateway.py providers.py records.py stub.py` が存在すること(無変更前提のws-2資産)
   - `ls docs/testassets/` で `README.md g1-alcohol.yaml g1-parser-struct.yaml` が存在し、`rg -n "status: confirmed" docs/testassets/g1-parser-struct.yaml` がmeta部でヒットすること(confirmed確認)
2. **worktree**: 実装はスーパーバイザーが用意したgit worktree内で行う。なければ `superpowers:using-git-worktrees` スキルに従って作成する(ブランチ名 `ws-6-g1gate`)。mainには直接触らない。本計画書のパスはすべてリポジトリルートからの相対パス(worktree内ではworktreeルートが基準)。
3. **コミット規律(必須)**: タスク単位でworktreeブランチへコミットする(1タスク=1コミットを基本とする)。各Taskの最終ステップにコミットコマンドを用意してあるので必ず実行すること。コミットメッセージの末尾には `Co-Authored-By: Claude Code <noreply@anthropic.com>` を付ける(改行を挟んで追記。`git commit -m "…" -m "Co-Authored-By: …"` 形式でよい)。mainへのマージ・pushはスーパーバイザーが行う(実装者は行わない)。
4. **DB/Redisに触れない単位**: 本単位はマイグレーション追加なし・DB/Redisを消費するコード変更なし(harnessはuser_lookup常にNone・DB非依存)。STATUS運用ルール1〜3(test-ci同時実行制約)には無関係。unit開発は `make lint` / `make test` で進める。
5. **test-ciとapiイメージ再ビルド(運用ルール4)**: 最終検証で `make test-ci` を実行する。composeのapiはbuild型・ソースマウントなしのため、**`docker compose build api` を先行必須**とすること(依存追加によりapiイメージのuv syncがanthropic・pyyamlを拾わないと、api起動時のimport連鎖(main→intents→make_intent_parse_service→build_llm_gateway→llm.anthropic→anthropic SDK)で起動失敗する)。workerはllm非依存(core.clock・settingsのみimport)のため再ビルド不要だが、問題があれば `docker compose build worker` も実行してよい。
6. **API鍵の規律(最重要)**: LATCH_ANTHROPIC_API_KEYの実値をコード・テスト・ログ・報告書・コミットメッセージに**一切書かない**(キー名のみ扱う)。`.env` はgit管理外・スーパーバイザー管理のため本単位では編集しない。実APIスモーク実行(Task 8)は `.env` 経由の値を使い、出力には鍵を含まない(レポートmetaは鍵のSHA256等も載せない)。
7. **テストファイルbasenameの一意性(運用ルール5)**: 新規テストは `backend/tests/unit/g1gate/` と `backend/tests/unit/llm/test_anthropic_provider.py` に作る。すべてのコミット前に `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることを確認する。
8. **検証コマンド**: backend変更を含むコミット前に `make lint` と `make test` が緑であること。
9. **G1判定への言及**: 本単位はharnessの実装まで。ゲートの全件実測(71呼び出し)とG1合否判定はスーパーバイザー/ユーザー領域であり、実装者がG1判定を下す記述を報告書に書かない。

## 1. 参照仕様節

実装が直接依存する確定値(design §1.2に全18件の詳細がある。ここでは要点と出典のみ)。

| # | 確定値 | 出典 |
|---|---|---|
| 1 | Parser系統プロバイダ=Anthropic Claude API・Haiku 4.5(モデルID `claude-haiku-4-5`・T1 v0.2契約2026-09-28確定) | 07 §1表 / T1 v0.2 §3・§5 |
| 2 | Parser呼び出し特性: 同期・timeout 10秒・再試行なし(即フォールバック) | 07 §1表・§5 D-17 |
| 3 | LLM呼び出しは単一Gateway経由。送信記録は送信先・データ種別・時刻のみ(内容不含) | 04 §2 / 08 §3 / 12 §2 C4 |
| 4 | 構造化ログ許可リスト。API鍵・raw_text・条件系フィールド不許可 | 08 §2.4 |
| 5 | システムプロンプト全文は07 §2で確定済み・`{current_date}`へ現在日付・出力は指定JSONスキーマのみ認める | 07 §2 |
| 6 | 必須3抽出不能=422 VALIDATION_ERROR / LLM障害=503 LLM_UNAVAILABLE。検証はParserOutput.model_validateに一元 | 07 §2・§5 D-17 / M1 ws-2 §2.4 |
| 7 | Parser構造化精度ゲート: 30件以上で category 85% / time.start 90% / location 90%(地域一致) / participants 80% / budget 90% | 07 §5 D-17 / 09 §4.3 |
| 8 | alcohol_involvedゲート: recall 100%(見逃し0件)・precision下限90% | 09 §4.3(FR-20) |
| 9 | 検証手順: 入力セット→Parser→フィールド単位照合→一致率集計→D-17基準と判定。Parser/プロンプト変更のたび再実行 | 09 §4.3 |
| 10 | ゲート入力=docs/testassets/(confirmed)。基準日時2026-10-01T11:30 JST。期待値はParser出力段階の値。エラーケースは422確認(分母外) | docs/testassets/README.md / 10 §2 |
| 11 | 合格証拠はdocs/testassets/results/へ保管(試験ID・実施日・環境・入力条件・合否) | 10 §5 / 12 §5 |
| 12 | 精度ゲート不成立の打ちはプロンプト改善のみ | 12 §7 |
| 13 | 実プロバイダはbuild_llm_gatewayの分岐に追加(Gateway IF不変)。プロンプトはファクトリ注入 | M0 ws-2 design §2.3 / intents/prompt.py docstring |
| 14 | SDK既定backoff retryは無効化(max_retries=0) | 07 §1再試行規定 |
| 15 | negative_constraints常に空配列。正規化はparse境界=IntentParseService内 | 07 §2 / M1 ws-2 design §2.5 |
| 16 | テスト入力文言は生産データでなくログ制限対象外(レポートのactual記録を許す) | 09 §4.1 |
| 17 | Parser縮退先(Bedrock東京→OpenAI)は本単位では作らない | T1 v0.2 §5 |
| 18 | 実API呼び出しは通常のtest-ciでは走らない構成(CLI経路のみ) | supervisor補足(ws-6固有) |

設計確定値(design §2・supervisor承認済み§6の7件):
- §2.1-A: 公式SDK `anthropic` の `AsyncAnthropic` を使用
- §2.2-A: structured outputs(`output_config={"format": {"type": "json_schema", "schema": …}}`)で出力JSONをAPI側保証。スキーマ供給源は`ParserOutput.model_json_schema()`+llm側の機械的後加工関数1つ($defs解決・required埋め・additionalProperties付与)。後加工で対応不能な制約が実装時に判明した場合のみ手書きスキーマ定数へ切替(切替時は対応検証試験を付け、報告書に記録)
- §2.3-A: `build_llm_gateway`へキーワード引数追加。`make_intent_parse_service`が`PARSER_SYSTEM_PROMPT`と`ParserOutput.model_json_schema()`を渡す
- §2.4-A: `llm_mode="real"`はParser系統のみreal化。Embedding/JevはStubLLM継続。realで鍵空/引数NoneはValueError(fail-fast)
- §2.5: SDK timeout=10.0(Gateway TIMEOUT_PARSER_Sと同値)・max_retries=0。Gateway本体・records・stub・providers・errorsは差分ゼロ
- §2.6: temperature=0・thinking指定なし(無効)・max_tokens=1024・プロンプトキャッシュなし
- §2.7-A: CLI `python -m latch.g1gate`+makeターゲット。pytestにmarkerを作らない。起動時にllm_mode=realと鍵を検証し不足なら拒否
- §2.8-A: harnessはGateway+IntentParseService直接構成(FakeClock基準日時・user_lookup常にNone)。HTTP層を経ない
- §2.9: 照合規則(category等値 / time.start瞬間等値 / location双方向部分一致・空白正規化 / participants対等値 / budget等値)。フィールド別集計・閾値は定数。alcoholはTP/FP/FN/TN→recall/precision。error_casesは422発生確認のみ(missing照合外)。parser-struct側のalcohol期待値は参考値(判定外)
- §2.10: レポートYAMLをdocs/testassets/results/g1-result-YYYYMMDD-HHMMSS.yamlへ。meta=実行時刻・llm_mode・モデルID・プロンプトSHA256・入力ファイル名+SHA256・基準日時。ケース別詳細にactual(Parser出力全体)を含む。exit code: 全合格=0/それ以外=1。`--limit N`で部分実行
- §2.11: 71呼び出し(32+36+3)を直列実行。全体コストおよそ$0.3未満(1回あたり約$0.004)
- §6-4: Settingsはenv_file未指定のまま。Makefileのg1-gateターゲットが`uv run --env-file ../.env`で読み込む

## 2. スコープ(作成・変更するファイル一覧)

### 2.1 新規作成

```
backend/src/latch/llm/anthropic.py          # Task 2: AnthropicParserProvider+adapt_schema_for_anthropic
backend/src/latch/g1gate/
├── __init__.py                             # Task 7: 公開IFの再export
├── __main__.py                             # Task 7: CLI(引数解析・起動検証・exit code)
├── cases.py                                # Task 4: 入力YAML読み込み+pydantic検証+基準日時一致検証
├── compare.py                              # Task 5: 照合・集計・閾値判定(純粋関数)
├── runner.py                               # Task 7: 実行経路(Gateway+IntentParseService・直列)
└── report.py                               # Task 6: レポートYAML書き出し・sha256
backend/tests/unit/llm/
└── test_anthropic_provider.py              # Task 2(SDKクライアント注入モック・実APIゼロ)
backend/tests/unit/g1gate/
├── test_cases.py                           # Task 4(docs実YAML回帰+フィクスチャ異常系)
├── test_compare.py                         # Task 5(照合・集計・閾値の全ロジック)
├── test_runner.py                          # Task 7(StubLLM注入で全経路)
└── test_report.py                          # Task 6(レポートYAML構造)
backend/tests/fixtures/g1gate/
├── struct-minimal.yaml                     # Task 4(正常系最小・2ケース+1エラー)
└── alcohol-minimal.yaml                    # Task 4(正常系最小・3ケース)
docs/testassets/results/                    # Task 8: スモーク実行時に生成(git管理・10 §5証拠置き場)
docs/plans/M1/ws-6-report.md                # Task 9: 報告ファイル
```

### 2.2 変更(既存ファイル)

| ファイル | 変更内容 | タスク |
|---|---|---|
| `backend/pyproject.toml`・`backend/uv.lock` | `anthropic`(本番)・`pyyaml`(本番)追加。`uv add` が生成する差分のみ | Task 1 |
| `backend/src/latch/settings.py` | `llm_anthropic_api_key: str = ""` 1項目追記+`llm_mode`行コメント更新 | Task 1 |
| `backend/tests/unit/llm/test_llm_factory.py` | LLM_ENV_VARS追記・4項目試験の5項目化(機械的追随)・real系追記・`test_factory_rejects_unknown_mode`修正 | Task 1・3 |
| `backend/src/latch/llm/gateway.py` | `build_llm_gateway` の分岐拡張(real)+import 1行。LLMGateway本体は無変更 | Task 3 |
| `backend/src/latch/intents/service.py` | `make_intent_parse_service` がプロンプト/スキーマを渡す(数行)+import 1行 | Task 3 |
| `Makefile` | `g1-gate` ターゲット追加(.PHONY行へも追記) | Task 8 |
| `.env.example` | `LATCH_LLM_MODE` 行の追記(コメント付き) | Task 8 |

## 3. 禁止(触れてはいけないもの・スコープ外の判断基準)

**触れてはいけないファイル**(design §3.3):

- `backend/src/latch/llm/` のうち **gateway.py 以外**(`providers.py`・`records.py`・`stub.py`・`errors.py`・`__init__.py`)— 無差分。Gateway本体・送信記録・ABC・例外体系は拡張済みの完成物
- `backend/src/latch/intents/` のうち **service.py 以外**(`prompt.py`・`schema.py`・`completion.py`・`routes.py`・`errors.py`・`store.py`・`mapping.py`・`intent_input.py`・`events.py`)— 読むだけ。G1条件4のプロンプトピン試験(test_prompt.py)も既存のまま
- `backend/src/latch/main.py`・`worker/`・`auth/`・`core/`・`geo/`・`ratelimit/`・`users/` — real化はmake_intent_parse_service経由で自動、プロセス統合の変更不要
- `backend/alembic/` — マイグレーション追加なし(DB書込みゼロ)
- `compose.yaml`・`backend/Dockerfile`・`docker/` — ci環境へ鍵・llm_modeを渡さない=stub継続(確定値18)。イメージは`uv sync --frozen`が新依存を拾うだけでDockerfile変更不要
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/learn/`(agent4管轄)
- `docs/testassets/` の入力YAML 2件とREADME(confirmed・読み取り専用。変更は09/10の改版を要する・12 §5)。**`docs/testassets/results/` は本単位の追記先として例外**(スモーク実行のレポートのみ)
- `.env`(git管理外・スーパーバイザー管理。実装者は編集しない)
- `frontend/`・`prototype/`・`README.md`・`.claude/`
- 既存テストファイルのうち `test_llm_factory.py` 以外(修正禁止・無修正で緑であることを確認するだけ)

**スコープ外と判断する基準**(design §2.12。以下を見つけても作らない・報告に記録のみ):

- Embedding/Jev系統の実adapter・System One IF・フォールバックLLM・circuit breaker(M2。12 M2スコープ6・10)
- Parserの縮退先(Bedrock東京・OpenAI)への切替実装(T1 §5。Gateway抽象の差し替えで将来対応)
- 系統別llm_mode設定(§2.4-B。M2で必要になった時)
- プロンプトキャッシュ・トークン数・cost実測のレポート搭載(M4 Observability)
- pytest marker・実APIのunit/integration試験(§2.7-A。CLIのみ)
- HTTP層(routes)を通すreal試験(§2.8-B。routesはprovider非依存)
- harnessの並列実行・レイテンシ測定(§2.11。M4 load試験)
- ゲート入力セットの変更・追加(YAMLはconfirmed)
- G1判定のSTATUS.md記載(スーパーバイザー領域)

## 4. Global Constraints

- Gateway IF・送信記録・例外体系は無変更(確定値3・13)。実プロバイダは`build_llm_gateway`の分岐に閉じる
- `llm/` はintents/非依存のまま(§2.3-A)。プロンプト文字列とスキーマdictだけを受け取る。逆方向の intents→llm import は`make_intent_parse_service`ファクトリに限る(既存規律)
- 依存追加は `anthropic`・`pyyaml` の2つのみ(design完了条件5。`git diff` でpyproject.toml・uv.lockの差分がこの2追加に限ること)
- 実APIを叩く試験をpytestに1つも作らない(§2.7-A)。実API経路はCLI(`make g1-gate`)のみ
- API鍵の実値をコード・テスト・ログ・レポート・報告書に書かない(§0-6)。テストのapi_keyは固定ダミー文字列のみ
- 時刻参照はすべてClock経由(arch test `test_arch_no_direct_time.py` が自動強制。g1gate/・anthropic.pyもスキャン対象。レポートの実行時刻はSystemClock注入)
- テスト入力文言はログ制限対象外(確定値16)だが、テストコードの入力はdocs/testassets掲載文・fixtures定義文に限る(新規に生産データらしき文言を作らない)
- pytest新規ファイルのbasenameはtests配下全体で一意(§0-7)
- Settingsはenv_file未指定のまま(§6-4)。`.env`読み込みはMakefileの`uv run --env-file ../.env`経路のみ

## 5. Review Focus

実装・試験が見落としやすい入力クラスと失敗モード(specが暗示するが個別タスクの試験だけでは踏まないもの。各行の検証は括弧内のタスクが持つ):

1. **実APIが後加工スキーマを400で拒否する**(structured outputs制約とpydantic生成形の不一致がunit試験の合成モックでは検出不能)→ Task 8の実APIスモーク(`--limit 5`)が検出。400が出た場合の手順をTask 8に明記
2. **測定ケースへのエラー混入時に分母を減らして合格に見せる**(design §2.9「入力セット全件への応答が得られて初めて一致率が成立」)→ Task 5(summarize_structの分母固定)・Task 7(incomplete検出)の試験が固定
3. **ci環境への鍵/mode漏れ**(compose経由でreal化しtest-ciが実APIを叩く)→ compose.yaml無差分(§3)+Task 8のtest-ci緑で証明。Settings既定stub
4. **location部分一致の偽陽性**(空文字列は任意文字列の部分文字列になる)→ Task 5の `location_match` 空・空白ケース試験が固定(正規化後空は不一致)
5. **入力セット改変の検出漏れ**(レポートsha256が実際にファイル内容を追跡しない)→ Task 6のsha256既知ベクトル試験が固定

## 6. 完了条件(受渡し判定。design §5の7項目)

1. `make lint`・`make test` がグリーン(Task 2・3・4・5・6・7のunit追加分を含む)
2. `make test-ci` がグリーン(**`docker compose build api` 再ビルド後**に実行・運用ルール4)。マイグレーション追加なし。ci環境が実APIに依存しないこと(llm_mode既定stub・compose無変更)を報告書に明記
3. 起動検証がunit試験で立証済み(stubモード指定・鍵なしでの確実な拒否)。かつ実APIでのスモーク実行(`--limit 5`・レポート生成)が成功している
4. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが引き続き `core/clock.py` のみ
5. 依存追加が `anthropic`・`pyyaml` の2つのみ(`git diff main -- backend/pyproject.toml backend/uv.lock` で確認)
6. 触るファイルが §2.1・§2.2 の一覧どおり(llm/はgateway.pyのみ・intents/はservice.pyの数行のみ・compose.yaml・Dockerfile・docker/無差分)
7. `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空(運用ルール5)

## 7. 報告形式

**結果ファイル**: `docs/plans/M1/ws-6-report.md`(worktreeブランチへコミット)。以下を記載する。

1. **サマリ**: 完了条件7項目(§6)ごとの証拠(コマンドと出力要点)
2. **コミット一覧**: `git log --oneline main..HEAD` の出力
3. **テスト構成**: 新規テストファイル5件+追記1件の試験名一覧と、`make test` の件数(実装前後の差)
4. **検証結果**: `make lint`・`make test`・`docker compose build api`・`make test-ci` の結果(件数)
5. **実APIスモーク**: 実行コマンド・レポートファイルパス(docs/testassets/results/g1-result-*.yaml)・overall_passed・limit値・exit code。`--limit 5` で実施
6. **design §2.2のスキーマ供給源の決着**: 機械的後加工で通ったか・手書き切替したか(切替時は対応検証試験の説明)
7. **注記**: 実装中の判断・フォールバック手順の発火(SDK型調整等)・スコープ外と判断して作らなかったもの

---

### Task 1: 依存追加(anthropic・pyyaml)とSettings鍵項目

**Files:**
- Modify: `backend/pyproject.toml`・`backend/uv.lock`(uv add が更新)
- Modify: `backend/src/latch/settings.py`
- Modify: `backend/tests/unit/llm/test_llm_factory.py`

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: `Settings.llm_anthropic_api_key: str`(既定"")。`anthropic` SDKと`yaml`(pyyaml)がimport可能に。以降の全タスクが依存

- [ ] **Step 1.1: 依存を追加する**

```bash
cd backend && uv add anthropic pyyaml
```

実行後、`backend/pyproject.toml` の `dependencies` に `"anthropic>=…"` と `"pyyaml>=…"` の2行が追加され、`backend/uv.lock` が更新される。**この2つ以外の依追加がないこと**を確認する(`git diff -- backend/pyproject.toml` で追加行が2行のみ)。

- [ ] **Step 1.2: Settingsへ鍵項目を追記する**

`backend/src/latch/settings.py` のLLM Gateway節を次の形へ変更する(`llm_mode`のコメント更新+1項目追記):

```python
    # --- LLM Gateway(ws-2。design §3.2)---
    # "stub": 3系統すべてスタブ / "real": Parser系統のみAnthropic実API
    # (Embedding/JevはM2までスタブ継続 — design §2.4)
    llm_mode: str = "stub"
    # T1 Parser契約(2026-09-28・Anthropic Haiku 4.5)のAPI鍵。実値は.env
    # (git管理外)へ書き、make g1-gate(uv run --env-file ../.env)経由で
    # のみプロセスへ渡す。ci環境(compose)へは渡さない
    llm_anthropic_api_key: str = ""
    # 10 第1節レイテンシ注入(既定は無効)。p50/p95分布はM4でスタブ内で拡張
    llm_stub_delay_parser_ms: int = 0
    llm_stub_delay_embedding_ms: int = 0
    llm_stub_delay_jev_ms: int = 0
```

- [ ] **Step 1.3: 既存試験を追随させる(失敗を先に見る)**

`backend/tests/unit/llm/test_llm_factory.py` の冒頭 `LLM_ENV_VARS` と、`test_llm_settings_defaults`・`test_llm_settings_are_exactly_four_fields` を次へ変更する:

```python
LLM_ENV_VARS = (
    "LATCH_LLM_MODE",
    "LATCH_LLM_STUB_DELAY_PARSER_MS",
    "LATCH_LLM_STUB_DELAY_EMBEDDING_MS",
    "LATCH_LLM_STUB_DELAY_JEV_MS",
    "LATCH_ANTHROPIC_API_KEY",
)
```

```python
def test_llm_settings_defaults(monkeypatch):
    s = _clean_settings(monkeypatch)
    assert s.llm_mode == "stub"
    assert s.llm_anthropic_api_key == ""
    assert s.llm_stub_delay_parser_ms == 0
    assert s.llm_stub_delay_embedding_ms == 0
    assert s.llm_stub_delay_jev_ms == 0
```

```python
def test_llm_settings_are_exactly_five_fields(monkeypatch):
    # Review Focus #5: timeout・failフラグのenv経路を作らない(design §2.4・§2.6)。
    # LLM系設定はこの5項目のみであることを機械検査する。
    _clean_settings(monkeypatch)
    llm_fields = {f for f in Settings.model_fields if f.startswith("llm_")}
    assert llm_fields == {
        "llm_mode",
        "llm_anthropic_api_key",
        "llm_stub_delay_parser_ms",
        "llm_stub_delay_embedding_ms",
        "llm_stub_delay_jev_ms",
    }
```

(`test_llm_settings_are_exactly_four_fields` は削除して上の5項目版へ置き換える。改名を忘れると重複定義になるので注意)

- [ ] **Step 1.4: 試験を実行して緑を確認する**

```bash
cd backend && uv run pytest tests/unit/llm/test_llm_factory.py -v
```

Expected: 全件 PASS(Settings追記と期待値が揃っている)。`_clean_settings` が `LATCH_ANTHROPIC_API_KEY` を消すため、開発環境の実鍵が試験へ影響しない。

- [ ] **Step 1.5: 全unitとlintが緑であることを確認してコミット**

```bash
make lint && make test
```

Expected: lintクリーン・unit全緑(561件からの増減なし。この時点では試験の改名のみ)

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/latch/settings.py backend/tests/unit/llm/test_llm_factory.py
git commit -m "feat: anthropic・pyyaml依存とLATCH_ANTHROPIC_API_KEY設定を追加" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 2: AnthropicParserProvider(llm/anthropic.py)

**Files:**
- Create: `backend/src/latch/llm/anthropic.py`
- Test: `backend/tests/unit/llm/test_anthropic_provider.py`

**Interfaces:**
- Consumes: `ParserProvider`(llm/providers.py — `name`属性+`async complete_structured(text: str, current_date: date) -> dict`)。anthropic SDK(`AsyncAnthropic`)
- Produces: `ANTHROPIC_PARSER_MODEL = "claude-haiku-4-5"`・`ANTHROPIC_PARSER_TIMEOUT_S = 10.0`・`ANTHROPIC_PARSER_MAX_TOKENS = 1024`・`adapt_schema_for_anthropic(schema: dict) -> dict`・`AnthropicParserProvider(*, api_key: str, system_prompt: str, output_schema: dict, client: AsyncAnthropic | None = None)`(Task 3のbuild_llm_gatewayが消費)

注意: `anthropic.py` から `latch.llm.gateway` をimportすると循環する(gateway.pyがTask 3でanthropic.pyをimportするため)。**gateway.pyをimportしない**構成にすること(timeout同値性はピン試験で強制)。

- [ ] **Step 2.1: 失敗テストを書く**

`backend/tests/unit/llm/test_anthropic_provider.py` を新規作成:

```python
"""AnthropicParserProviderのunit(design §4-1)。SDKクライアントは注入モック・
実API呼び出しゼロ。公式SDKのレスポンス型を合成して応答経路を検証する。"""

import json
from datetime import date
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from latch.intents.schema import ParserOutput
from latch.llm.anthropic import (
    ANTHROPIC_PARSER_MAX_TOKENS,
    ANTHROPIC_PARSER_MODEL,
    ANTHROPIC_PARSER_TIMEOUT_S,
    AnthropicParserProvider,
    adapt_schema_for_anthropic,
)
from latch.llm.gateway import TIMEOUT_PARSER_S
from latch.llm.providers import ParserProvider

SYSTEM_PROMPT = "現在日付は {current_date} とする。"  # {current_date}入り簡易プロンプト

# structured outputs用の最小スキーマ(後加工の入力。properties値は任意)
MIN_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


class _RecordingMessages:
    """AsyncAnthropic.messages のテストダブル(呼び出し記録+応答注入)。"""

    def __init__(self, responses=None, error=None):
        self.calls: list[dict] = []
        self._responses = list(responses or [])
        self._error = error

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, messages):
        self.messages = messages


def _sdk_message(text: str) -> anthropic.types.Message:
    """公式SDKのMessage型を合成(最初のtextブロック=有効JSON)。"""
    return anthropic.types.Message(
        id="msg_test",
        type="message",
        role="assistant",
        model=ANTHROPIC_PARSER_MODEL,
        content=[anthropic.types.TextBlock(type="text", text=text)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=anthropic.types.Usage(input_tokens=1, output_tokens=1),
    )


def _sdk_status_error() -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(500, request=request)
    return anthropic.APIStatusError("test error", response=response, body=None)


def test_timeout_constant_matches_gateway():
    # design §2.5: SDK timeoutとGateway timeoutを同値にする(循環import回避のため
    # 定数は自前定義+この試験で同値を強制)
    assert ANTHROPIC_PARSER_TIMEOUT_S == TIMEOUT_PARSER_S


def test_rejects_empty_api_key():
    with pytest.raises(ValueError, match="api_key"):
        AnthropicParserProvider(
            api_key="", system_prompt=SYSTEM_PROMPT, output_schema=MIN_SCHEMA
        )


def test_default_client_options():
    provider = AnthropicParserProvider(
        api_key="test-key", system_prompt=SYSTEM_PROMPT, output_schema=MIN_SCHEMA
    )
    client = provider._client  # AsyncAnthropic実体(この試験のみ構築)
    assert type(client).__name__ == "AsyncAnthropic"
    assert client.max_retries == 0
    timeout = client.timeout
    assert {timeout.connect, timeout.read, timeout.write, timeout.pool} == {
        ANTHROPIC_PARSER_TIMEOUT_S
    }


def test_provider_is_parser_provider():
    provider = AnthropicParserProvider(
        api_key="test-key", system_prompt=SYSTEM_PROMPT, output_schema=MIN_SCHEMA
    )
    assert isinstance(provider, ParserProvider)
    assert provider.name == "anthropic"


async def test_request_shape():
    msgs = _RecordingMessages(responses=[_sdk_message('{"ok": true}')])
    provider = AnthropicParserProvider(
        api_key="test-key",
        system_prompt=SYSTEM_PROMPT,
        output_schema=MIN_SCHEMA,
        client=_FakeClient(msgs),
    )
    result = await provider.complete_structured("テキスト", date(2026, 10, 1))
    assert result == {"ok": True}
    (call,) = msgs.calls
    assert call["model"] == ANTHROPIC_PARSER_MODEL
    assert call["system"] == "現在日付は 2026-10-01 とする。"  # {current_date}差し替え
    assert call["messages"] == [{"role": "user", "content": "テキスト"}]
    assert call["temperature"] == 0
    assert call["max_tokens"] == ANTHROPIC_PARSER_MAX_TOKENS
    assert call["output_config"]["format"]["type"] == "json_schema"
    schema = call["output_config"]["format"]["schema"]
    # 生スキーマではなく後加工済みが渡る証明(required埋め+additionalProperties)
    assert schema["required"] == ["x"]
    assert schema["additionalProperties"] is False


async def test_returns_first_text_block_as_dict():
    # thinking風ブロック(type!="text")が先行しても最初のtextブロックを採る
    thinking = SimpleNamespace(type="thinking", thinking="...")
    text = SimpleNamespace(type="text", text='{"a": 1}')
    msgs = _RecordingMessages(responses=[SimpleNamespace(content=[thinking, text])])
    provider = AnthropicParserProvider(
        api_key="test-key",
        system_prompt=SYSTEM_PROMPT,
        output_schema=MIN_SCHEMA,
        client=_FakeClient(msgs),
    )
    result = await provider.complete_structured("t", date(2026, 10, 1))
    assert result == {"a": 1}


async def test_sdk_exception_passthrough():
    # SDK例外は素通り(design §3.1: Gatewayの既存wrapと送信記録が最終関門)
    msgs = _RecordingMessages(error=_sdk_status_error())
    provider = AnthropicParserProvider(
        api_key="test-key",
        system_prompt=SYSTEM_PROMPT,
        output_schema=MIN_SCHEMA,
        client=_FakeClient(msgs),
    )
    with pytest.raises(anthropic.APIStatusError):
        await provider.complete_structured("t", date(2026, 10, 1))


def _assert_all_objects_strict(node) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            assert node["required"] == sorted(node["properties"])
            assert node["additionalProperties"] is False
        for value in node.values():
            _assert_all_objects_strict(value)
    elif isinstance(node, list):
        for item in node:
            _assert_all_objects_strict(item)


def test_adapt_schema_structure_pins():
    # Review Focus #1(構造面): ParserOutput.model_json_schema()から決定的導出され、
    # Anthropic制約(全required+additionalProperties=false+$defs解決済み)を満たす
    adapted = adapt_schema_for_anthropic(ParserOutput.model_json_schema())
    assert "$defs" not in adapted
    assert "$ref" not in json.dumps(adapted)
    _assert_all_objects_strict(adapted)
    assert set(adapted["properties"]) == {
        "category",
        "alcohol_involved",
        "time",
        "location",
        "budget",
        "participants",
        "soft_constraints",
        "negative_constraints",
        "ng_unverifiable",
    }
    primary = adapted["properties"]["category"]["properties"]["primary"]
    assert set(primary["enum"]) == {"meal", "drinking", "activity"}
    assert adapted["properties"]["alcohol_involved"]["type"] == "boolean"
    start = adapted["properties"]["time"]["properties"]["start"]
    assert start["type"] == "string" and start.get("format") == "date-time"


def test_adapt_schema_is_deterministic():
    a = adapt_schema_for_anthropic(ParserOutput.model_json_schema())
    b = adapt_schema_for_anthropic(ParserOutput.model_json_schema())
    assert a == b
```

注記: `_sdk_message` で `anthropic.types.Message` の構築がSDKバージョンの必須フィールド違いでTypeErrorになる場合は、`anthropic.types` の現在の必須フィールドに合わせて引数を調整してよい(Provider実装はduckタイピングのため修正はテスト側に限る。調整内容を報告書へ記録)。`import httpx2` はanthropic SDK 1.xの依存(httpx2ベース)であり、dev依存のhttpxとは別物。

- [ ] **Step 2.2: テストを実行して失敗を確認する**

```bash
cd backend && uv run pytest tests/unit/llm/test_anthropic_provider.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'latch.llm.anthropic'`

- [ ] **Step 2.3: 実装する**

`backend/src/latch/llm/anthropic.py` を新規作成:

```python
"""Parser系統の実プロバイダ(Anthropic Claude API・Haiku 4.5。T1 v0.2・07 §1)。

design §2.1-A(公式SDK)・§2.2-A(structured outputs)・§2.5(SDK timeout=10秒・
max_retries=0)・§2.6(temperature=0・thinkingなし・max_tokens=1024)。
SDK例外はこの層で握らず素通り — Gatewayの既存wrap(LLMTimeoutError/
LLMProviderError)と送信記録が最終関門(design §2.5)。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from anthropic import AsyncAnthropic

from latch.llm.providers import ParserProvider

ANTHROPIC_PARSER_MODEL = "claude-haiku-4-5"  # T1 v0.2 Parser契約(2026-09-28確定)
# Gateway TIMEOUT_PARSER_Sと同値(design §2.5: SDK側で過剰に待つ時間を作らない)。
# 循環import回避のため値を自前定義し、同値性はunit試験が強制する
ANTHROPIC_PARSER_TIMEOUT_S = 10.0
ANTHROPIC_PARSER_MAX_TOKENS = 1024  # §2.6: 出力想定0.4k+マージンの打ち切り防御

# 後加工で保持しないpydanticメタデータ(出力保証に不要・スキーマを小さく保つ)
_STRIP_KEYS = ("title", "description", "default", "$defs", "examples")


def _resolve_refs(node: Any, defs: dict) -> Any:
    """$ref(#/$defs/X)を定義内容へ再帰的にインライン展開する(design §2.2)。"""
    if isinstance(node, dict):
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            return _resolve_refs(defs[name], defs)
        return {
            key: _resolve_refs(value, defs)
            for key, value in node.items()
            if key not in _STRIP_KEYS
        }
    if isinstance(node, list):
        return [_resolve_refs(item, defs) for item in node]
    return node


def _normalize_objects(node: Any) -> None:
    """すべてのobject型ノードで required=全プロパティ・additionalProperties=false へ。"""
    if isinstance(node, dict):
        properties = node.get("properties")
        if node.get("type") == "object" and isinstance(properties, dict):
            node["required"] = sorted(properties)
            node["additionalProperties"] = False
        for value in node.values():
            _normalize_objects(value)
    elif isinstance(node, list):
        for item in node:
            _normalize_objects(item)


def adapt_schema_for_anthropic(schema: dict) -> dict:
    """pydantic生成JSON Schemaをstructured outputs用へ機械的に後加工(design §2.2)。

    ParserOutput.model_json_schema()を単一の真実とし続けるため、手書き複製
    ではなく決定的導出のみを行う: $defs/$ref解決・メタデータ除去・required
    埋め・additionalProperties=false(Anthropic側の制約)。最終検証関門は
    サービス層のParserOutput.model_validateのまま(ずれは422として表面化)。
    """
    adapted = _resolve_refs(schema, schema.get("$defs", {}))
    _normalize_objects(adapted)
    return adapted


class AnthropicParserProvider(ParserProvider):
    """Parser系統の実プロバイダ(07 §1・T1 v0.2)。name="anthropic"(08 §3送信記録)。"""

    def __init__(
        self,
        *,
        api_key: str,
        system_prompt: str,
        output_schema: dict,
        client: AsyncAnthropic | None = None,
    ) -> None:
        if not api_key:
            # fail-fast: 鍵の不在を静かに握りつぶさない(design §2.4)
            raise ValueError("AnthropicParserProvider requires api_key")
        self.name = "anthropic"
        self._system_prompt = system_prompt
        self._schema = adapt_schema_for_anthropic(output_schema)
        self._client = (
            client
            if client is not None
            else AsyncAnthropic(
                api_key=api_key,
                timeout=ANTHROPIC_PARSER_TIMEOUT_S,
                max_retries=0,
            )
        )

    async def complete_structured(self, text: str, current_date: date) -> dict:
        """07 §2。出力JSONはAPI側が保証(§2.2) — 最初のtextブロックがvalid JSON。"""
        response = await self._client.messages.create(
            model=ANTHROPIC_PARSER_MODEL,
            system=self._system_prompt.replace(
                "{current_date}", current_date.isoformat()
            ),
            messages=[{"role": "user", "content": text}],
            output_config={
                "format": {"type": "json_schema", "schema": self._schema}
            },
            temperature=0,
            max_tokens=ANTHROPIC_PARSER_MAX_TOKENS,
        )
        # structured outputsの保証: 最初のtextブロックはvalid JSON(design §2.2)。
        # この層では再検証しない — 検証関門はサービス層ParserOutput.model_validate
        first_text = next(
            block.text for block in response.content if block.type == "text"
        )
        return json.loads(first_text)
```

- [ ] **Step 2.4: テストを実行して緑を確認する**

```bash
cd backend && uv run pytest tests/unit/llm/test_anthropic_provider.py -v
```

Expected: 全件 PASS

- [ ] **Step 2.5: lint・全unit・basename一意性を確認してコミット**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
```

Expected: lintクリーン・unit全緑(test_anthropic_provider.py 追加分の増加)・重複なし

```bash
git add backend/src/latch/llm/anthropic.py backend/tests/unit/llm/test_anthropic_provider.py
git commit -m "feat: AnthropicParserProvider(structured outputs+スキーマ後加工)を実装" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 3: build_llm_gatewayのreal分岐とmake_intent_parse_serviceの注入

**Files:**
- Modify: `backend/src/latch/llm/gateway.py`(`build_llm_gateway` のみ)
- Modify: `backend/src/latch/intents/service.py`(`make_intent_parse_service` のみ)
- Test: `backend/tests/unit/llm/test_llm_factory.py` へ追記

**Interfaces:**
- Consumes: Task 2の `AnthropicParserProvider(*, api_key, system_prompt, output_schema)`
- Produces: `build_llm_gateway(clock, settings, *, parser_system_prompt: str | None = None, parser_output_schema: dict | None = None) -> LLMGateway`(Task 7のrunnerが`make_intent_parse_service`経由で消費)。既存の2引数呼び出しは後方互換で動く

- [ ] **Step 3.1: 失敗テストを書く(test_llm_factory.pyへ追記)**

`backend/tests/unit/llm/test_llm_factory.py` の末尾へ追記(import部へ `from latch.llm.anthropic import AnthropicParserProvider` と `from latch.llm.stub import StubLLM` を追加):

```python
def test_factory_real_builds_anthropic_parser(clock, monkeypatch):
    # design §2.4: real=Parser系統のみ実装プロバイダ。Embedding/JevはStubLLM継続
    gw = build_llm_gateway(
        clock,
        _clean_settings(
            monkeypatch, llm_mode="real", llm_anthropic_api_key="test-key"
        ),
        parser_system_prompt="p {current_date}",
        parser_output_schema={"type": "object", "properties": {}},
    )
    assert isinstance(gw._parser, AnthropicParserProvider)
    assert gw._parser.name == "anthropic"
    assert isinstance(gw._embedding, StubLLM)
    assert isinstance(gw._jev, StubLLM)


def test_factory_real_requires_api_key(clock, monkeypatch):
    s = _clean_settings(monkeypatch, llm_mode="real")  # 鍵空=既定
    with pytest.raises(ValueError, match="api_key"):
        build_llm_gateway(
            clock, s, parser_system_prompt="p", parser_output_schema={}
        )


def test_factory_real_requires_prompt_and_schema(clock, monkeypatch):
    s = _clean_settings(
        monkeypatch, llm_mode="real", llm_anthropic_api_key="test-key"
    )
    with pytest.raises(ValueError, match="parser_system_prompt"):
        build_llm_gateway(clock, s)
    with pytest.raises(ValueError, match="parser_system_prompt"):
        build_llm_gateway(clock, s, parser_system_prompt="p")
    with pytest.raises(ValueError, match="parser_system_prompt"):
        build_llm_gateway(clock, s, parser_output_schema={"type": "object"})


async def test_make_intent_parse_service_passes_prompt_and_schema(monkeypatch):
    # design §2.3: ファクトリがPARSER_SYSTEM_PROMPTとParserOutputスキーマを渡す。
    # real設定で構築できれば注入は機能している(欠落ならValueError)
    from latch.intents.prompt import PARSER_SYSTEM_PROMPT
    from latch.intents.schema import ParserOutput
    from latch.intents.service import make_intent_parse_service

    monkeypatch.delenv("LATCH_ANTHROPIC_API_KEY", raising=False)
    settings = Settings(llm_mode="real", llm_anthropic_api_key="test-key")
    service = make_intent_parse_service(
        clock=FakeClock(NOW), settings=settings, user_lookup=None
    )
    # stubでもserviceは構築できる(llm/のimportはファクトリに限る規律どおり)
    service_stub = make_intent_parse_service(
        clock=FakeClock(NOW), settings=Settings(), user_lookup=None
    )
    assert service is not None and service_stub is not None
    assert PARSER_SYSTEM_PROMPT  # (docstring参照の実在確認)
    assert ParserOutput.model_json_schema()
```

同じファイルの `test_factory_rejects_unknown_mode` を次へ修正する(realは有効になったため):

```python
def test_factory_rejects_unknown_mode(clock, monkeypatch):
    # stub/real以外は拒否(realの引数完備は別試験が担保)
    s = _clean_settings(monkeypatch, llm_mode="production")
    with pytest.raises(ValueError, match="llm_mode"):
        build_llm_gateway(clock, s)
```

- [ ] **Step 3.2: テストを実行して失敗を確認する**

```bash
cd backend && uv run pytest tests/unit/llm/test_llm_factory.py -v
```

Expected: 追加3件がFAIL(`llm_mode='real'`がValueErrorになる等)・`test_make_intent_parse_service_passes_prompt_and_schema` はFAILしない可能性がある(現状実装でもservice構築は成功するため — この試験はTask 3実装後も通る回帰ピンとして機能)

- [ ] **Step 3.3: gateway.pyのbuild_llm_gatewayを実装する**

`backend/src/latch/llm/gateway.py` の `build_llm_gateway` を次へ置き換える(importへ `from latch.llm.anthropic import AnthropicParserProvider` を追加):

```python
def build_llm_gateway(
    clock: Clock,
    settings: Settings,
    *,
    parser_system_prompt: str | None = None,
    parser_output_schema: dict | None = None,
) -> LLMGateway:
    """設定からGatewayを構築する(design §2.7・ws-6 design §2.3/§2.4)。

    llm_mode="stub": 3系統すべてStubLLM(引数は無視 — 2引数呼び出し後方互換)。
    llm_mode="real": Parser系統のみAnthropicParserProvider(Embedding/Jevは
    StubLLM継続 — gemini・typesafe鍵は未設定・実装はM2)。鍵・プロンプト・
    スキーマの欠落はfail-fast(静かにスタブへ落ちない)。
    """
    stub = StubLLM(
        delay_parser_ms=settings.llm_stub_delay_parser_ms,
        delay_embedding_ms=settings.llm_stub_delay_embedding_ms,
        delay_jev_ms=settings.llm_stub_delay_jev_ms,
    )
    if settings.llm_mode == "stub":
        return LLMGateway(clock=clock, parser=stub, embedding=stub, jev=stub)
    if settings.llm_mode == "real":
        if not settings.llm_anthropic_api_key:
            raise ValueError(
                "llm_mode='real' requires llm_anthropic_api_key"
            )
        if parser_system_prompt is None or parser_output_schema is None:
            raise ValueError(
                "llm_mode='real' requires parser_system_prompt and"
                " parser_output_schema"
            )
        parser = AnthropicParserProvider(
            api_key=settings.llm_anthropic_api_key,
            system_prompt=parser_system_prompt,
            output_schema=parser_output_schema,
        )
        return LLMGateway(clock=clock, parser=parser, embedding=stub, jev=stub)
    raise ValueError(
        f"unknown llm_mode: {settings.llm_mode!r} ('stub' or 'real')"
    )
```

- [ ] **Step 3.4: service.pyのmake_intent_parse_serviceを実装する**

`backend/src/latch/intents/service.py` の `make_intent_parse_service` を次へ置き換える(importへ `from latch.intents.prompt import PARSER_SYSTEM_PROMPT` を追加 — 既存のschema.py importと並ぶ位置):

```python
def make_intent_parse_service(
    *, clock: Clock, settings: Settings, user_lookup: UserLookup
) -> IntentParseService:
    """設定からIntentParseServiceを構築する(design §2.7・ws-6 design §2.3)。

    llm/へのimport(build_llm_gateway)はこのファクトリに限る — サービス本体は
    LLM非依存。プロンプト全文(PARSER_SYSTEM_PROMPT)と出力スキーマ
    (ParserOutput.model_json_schema)をここで注入する。llm_modeの検証は
    build_llm_gatewayが持つ("real"では鍵・引数欠落をfail-fast)。
    """
    gateway = build_llm_gateway(
        clock,
        settings,
        parser_system_prompt=PARSER_SYSTEM_PROMPT,
        parser_output_schema=ParserOutput.model_json_schema(),
    )
    return IntentParseService(clock=clock, parser=gateway, user_lookup=user_lookup)
```

- [ ] **Step 3.5: テストを実行して緑を確認する**

```bash
cd backend && uv run pytest tests/unit/llm/test_llm_factory.py tests/unit/intents/ -v
```

Expected: 全件 PASS(llm/intentsの既存試験は引数追加の後方互換で不変・緑のまま)

- [ ] **Step 3.6: lint・全unitを確認してコミット**

```bash
make lint && make test
```

Expected: lintクリーン・unit全緑

```bash
git add backend/src/latch/llm/gateway.py backend/src/latch/intents/service.py backend/tests/unit/llm/test_llm_factory.py
git commit -m "feat: build_llm_gatewayへllm_mode=real分岐を追加しParserへプロンプト/スキーマ注入" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 4: G1ゲート入力セット読み込み(g1gate/cases.py)

**Files:**
- Create: `backend/src/latch/g1gate/cases.py`
- Create: `backend/tests/fixtures/g1gate/struct-minimal.yaml`・`backend/tests/fixtures/g1gate/alcohol-minimal.yaml`
- Test: `backend/tests/unit/g1gate/test_cases.py`

**Interfaces:**
- Consumes: `latch.core.clock.JST`・pydantic・pyyaml
- Produces: `BASE_CURRENT_DATETIME: datetime`(2026-10-01T11:30 JST)・`ParserStructSet`・`AlcoholSet`(pydanticモデル)・`load_parser_struct(path: Path) -> ParserStructSet`・`load_alcohol(path: Path) -> AlcoholSet`(Task 5・7が消費)

- [ ] **Step 4.1: フィクスチャYAMLを2件作成する**

`backend/tests/fixtures/g1gate/struct-minimal.yaml`:

```yaml
# test_cases.py用の正常系最小セット(docs実YAMLと同じ構造・ fixtures専用の短id)
meta:
  status: confirmed
  created: "2026-09-28"
  current_datetime: "2026-10-01T11:30:00+09:00"
  case_count: 2
  error_case_count: 1

cases:
  - id: P-901
    text: "今夜20時から天文館で飲みたい。ひとり3000円までで"
    author_age: 26
    expected:
      category: drinking
      alcohol_involved: true
      time: {start: "2026-10-01T20:00:00+09:00", end: null}
      location: {name: 天文館}
      participants: {min: null, max: null}
      budget: {max: 3000}
    needs_review: false
    note: "fixtures用"

  - id: P-902
    text: "明日19時から呉服町で軽く一杯。予算4000円くらい"
    author_age: 30
    expected:
      category: drinking
      alcohol_involved: true
      time: {start: "2026-10-02T19:00:00+09:00", end: null}
      location: {name: 呉服町}
      participants: {min: null, max: null}
      budget: {max: 4000}
    needs_review: false
    note: "fixtures用"

error_cases:
  - id: E-901
    text: "今日誰かと飲みたい"
    author_age: null
    expected:
      error: "422 VALIDATION_ERROR"
      missing: [time.start, location.name]
    needs_review: false
    note: "fixtures用"
```

`backend/tests/fixtures/g1gate/alcohol-minimal.yaml`:

```yaml
# test_cases.py・test_runner.py用の正常系最小セット(3件: true 2 / false 1)
meta:
  status: confirmed
  created: "2026-09-28"
  current_datetime: "2026-10-01T11:30:00+09:00"
  case_count: 3
  true_count: 2
  false_count: 1

cases:
  - id: A-901
    text: "今夜20時から天文館で飲みたい。ひとり3000円くらい"
    author_age: 26
    expected:
      alcohol_involved: true
      category_ref: drinking
    needs_review: false
    note: "fixtures用"

  - id: A-902
    text: "明日19時、天文館でビール飲みに行きたい人いますか"
    author_age: 27
    expected:
      alcohol_involved: true
      category_ref: drinking
    needs_review: false
    note: "fixtures用"

  - id: A-903
    text: "昼12時から天文館のカフェでコーヒーを飲みましょう"
    author_age: 35
    expected:
      alcohol_involved: false
      category_ref: activity
    needs_review: false
    note: "fixtures用(アルコールを指さない用法)"
```

- [ ] **Step 4.2: 失敗テストを書く**

`backend/tests/unit/g1gate/test_cases.py` を新規作成:

```python
"""g1gate/cases.pyのunit(design §4-3)。docs実YAMLの構造回帰+異常系。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from latch.g1gate.cases import (
    BASE_CURRENT_DATETIME,
    load_alcohol,
    load_parser_struct,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DOCS_ASSETS = REPO_ROOT / "docs" / "testassets"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "g1gate"


def test_loads_real_parser_struct_yaml():
    # docs実YAMLの構造回帰: 予期せぬ変更をここで検出する(10 §2 confirmed資産)
    parsed = load_parser_struct(DOCS_ASSETS / "g1-parser-struct.yaml")
    assert parsed.meta.status == "confirmed"
    assert len(parsed.cases) == 32
    assert len(parsed.error_cases) == 3
    ids = [c.id for c in parsed.cases]
    assert len(set(ids)) == 32  # id一意
    assert ids[0] == "P-001" and ids[-1] == "P-032"
    assert [c.id for c in parsed.error_cases] == ["E-001", "E-002", "E-003"]
    assert parsed.meta.current_datetime == BASE_CURRENT_DATETIME


def test_loads_real_parser_struct_expectation_shapes():
    parsed = load_parser_struct(DOCS_ASSETS / "g1-parser-struct.yaml")
    first = parsed.cases[0]
    assert first.expected.category == "drinking"
    assert first.expected.alcohol_involved is True
    assert first.expected.time.start == "2026-10-01T20:00:00+09:00"
    assert first.expected.location.name == "天文館"
    assert (first.expected.participants.min, first.expected.participants.max) == (
        None,
        None,
    )
    assert first.expected.budget.max == 3000


def test_loads_real_alcohol_yaml():
    parsed = load_alcohol(DOCS_ASSETS / "g1-alcohol.yaml")
    assert parsed.meta.status == "confirmed"
    assert len(parsed.cases) == 36
    true_count = sum(1 for c in parsed.cases if c.expected.alcohol_involved)
    assert true_count == 24  # true 24 / false 12(README)
    assert [c.id for c in parsed.cases][0] == "A-001"
    assert parsed.meta.current_datetime == BASE_CURRENT_DATETIME


def test_loads_fixture_minimal_sets():
    struct = load_parser_struct(FIXTURES / "struct-minimal.yaml")
    assert len(struct.cases) == 2 and len(struct.error_cases) == 1
    alcohol = load_alcohol(FIXTURES / "alcohol-minimal.yaml")
    assert len(alcohol.cases) == 3


def _rewrite(tmp_path, source: Path, old: str, new: str, name: str) -> Path:
    text = source.read_text(encoding="utf-8")
    assert old in text  # 置換対象が実在することの保証
    target = tmp_path / name
    target.write_text(text.replace(old, new), encoding="utf-8")
    return target


def test_rejects_wrong_current_datetime(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "struct-minimal.yaml",
        "2026-10-01T11:30:00+09:00",
        "2026-11-01T11:30:00+09:00",
        "wrong-datetime.yaml",
    )
    with pytest.raises(ValueError, match="current_datetime"):
        load_parser_struct(path)


def test_rejects_status_not_confirmed(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "struct-minimal.yaml",
        "status: confirmed",
        "status: draft",
        "draft.yaml",
    )
    with pytest.raises(ValueError, match="confirmed"):
        load_parser_struct(path)


def test_rejects_case_count_mismatch(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "struct-minimal.yaml",
        "case_count: 2",
        "case_count: 3",
        "count-mismatch.yaml",
    )
    with pytest.raises(ValueError, match="case_count"):
        load_parser_struct(path)


def test_rejects_duplicate_ids(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "struct-minimal.yaml",
        "- id: P-902",
        "- id: P-901",
        "dup-ids.yaml",
    )
    with pytest.raises(ValueError, match="id"):
        load_parser_struct(path)


def test_rejects_missing_expectation_field(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "struct-minimal.yaml",
        "      category: drinking",
        "",
        "no-category.yaml",
    )
    with pytest.raises(ValidationError):
        load_parser_struct(path)


def test_rejects_alcohol_true_count_mismatch(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "alcohol-minimal.yaml",
        "true_count: 2",
        "true_count: 1",
        "alcohol-mismatch.yaml",
    )
    with pytest.raises(ValueError, match="true_count"):
        load_alcohol(path)
```

- [ ] **Step 4.3: テストを実行して失敗を確認する**

```bash
cd backend && uv run pytest tests/unit/g1gate/test_cases.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'latch.g1gate'`

- [ ] **Step 4.4: 実装する**

`backend/src/latch/g1gate/cases.py` を新規作成:

```python
"""G1ゲート入力セット(docs/testassets/)の読み込みと検証(design §3.1・§2.8)。

YAMLはconfirmed・読み取り専用(10 §2)。ここで行うのは構造検証(pydantic)と
meta整合(件数・true/false・基準日時の一致)。期待値の中身の照合はcompare.py。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from latch.core.clock import JST

# docs/testassets/README.md「共通の前提」: 相対表現の解決基準(2026-10-01 11:30 JST)。
# {current_date}へ注入する値の前提 — meta.current_datetimeと一致しない入力セットは
# 期待値の前提が崩れているため読み込み段階で拒否する(design §2.8)
BASE_CURRENT_DATETIME = datetime(2026, 10, 1, 11, 30, tzinfo=JST)


class _IgnoreExtra(BaseModel):
    """余分キー(note・category_ref等)は無視(構造検証に必要な键のみ持つ)。"""

    model_config = ConfigDict(extra="ignore")


class ExpectedTime(_IgnoreExtra):
    start: str  # ISO8601(+09:00)。照合はcompare.pyがdatetime化
    end: str | None = None


class ExpectedLocation(_IgnoreExtra):
    name: str


class ExpectedParticipants(_IgnoreExtra):
    min: int | None = None
    max: int | None = None


class ExpectedBudget(_IgnoreExtra):
    max: int | None = None


class StructExpectation(_IgnoreExtra):
    """D-17測定対象5フィールド+alcohol参考値(README「測定対象外のフィールド」)。"""

    category: str
    alcohol_involved: bool
    time: ExpectedTime
    location: ExpectedLocation
    participants: ExpectedParticipants
    budget: ExpectedBudget


class ParserStructCase(_IgnoreExtra):
    id: str
    text: str
    author_age: int | None = None
    expected: StructExpectation
    needs_review: bool


class ErrorExpectation(_IgnoreExtra):
    error: str  # "422 VALIDATION_ERROR"
    # missing等は参照情報(05ペイロード設計・design §6-6)であり照合対象外


class ErrorCase(_IgnoreExtra):
    id: str
    text: str
    author_age: int | None = None
    expected: ErrorExpectation
    needs_review: bool


class AlcoholExpectation(_IgnoreExtra):
    alcohol_involved: bool
    # category_refは判定の一助(README)で照合対象外


class AlcoholCase(_IgnoreExtra):
    id: str
    text: str
    author_age: int | None = None
    expected: AlcoholExpectation
    needs_review: bool


class _MetaBase(_IgnoreExtra):
    status: str
    created: str
    current_datetime: datetime  # pydanticがISO8601文字列をパース(tz-aware)


class ParserStructMeta(_MetaBase):
    case_count: int
    error_case_count: int


class AlcoholMeta(_MetaBase):
    case_count: int
    true_count: int
    false_count: int


class ParserStructSet(_IgnoreExtra):
    meta: ParserStructMeta
    cases: list[ParserStructCase]
    error_cases: list[ErrorCase]


class AlcoholSet(_IgnoreExtra):
    meta: AlcoholMeta
    cases: list[AlcoholCase]


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: トップレベルはマッピングである必要があります")
    return data


def _verify_meta_current(meta: _MetaBase, *, kind: str) -> None:
    if meta.status != "confirmed":
        raise ValueError(f"{kind}: statusがconfirmedではありません: {meta.status!r}")
    if meta.current_datetime != BASE_CURRENT_DATETIME:
        raise ValueError(
            f"{kind}: meta.current_datetime({meta.current_datetime.isoformat()})"
            f"が基準日時({BASE_CURRENT_DATETIME.isoformat()})と一致しません"
            f" — 期待値の前提が崩れています(design §2.8)"
        )


def _verify_unique_ids(ids: list[str], *, kind: str) -> None:
    if len(set(ids)) != len(ids):
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"{kind}: ケースidが重複しています: {duplicated}")


def load_parser_struct(path: Path) -> ParserStructSet:
    """g1-parser-struct.yamlを読み、構造・meta整合・id一意を検証する。"""
    parsed = ParserStructSet.model_validate(_load_yaml(path))
    _verify_meta_current(parsed.meta, kind=path.name)
    _verify_unique_ids(
        [c.id for c in parsed.cases] + [c.id for c in parsed.error_cases],
        kind=path.name,
    )
    if len(parsed.cases) != parsed.meta.case_count:
        raise ValueError(
            f"{path.name}: cases {len(parsed.cases)}件がmeta.case_count"
            f" {parsed.meta.case_count}と一致しません"
        )
    if len(parsed.error_cases) != parsed.meta.error_case_count:
        raise ValueError(
            f"{path.name}: error_cases {len(parsed.error_cases)}件が"
            f"meta.error_case_count {parsed.meta.error_case_count}と一致しません"
        )
    return parsed


def load_alcohol(path: Path) -> AlcoholSet:
    """g1-alcohol.yamlを読み、構造・meta整合(true/false数)・id一意を検証する。"""
    parsed = AlcoholSet.model_validate(_load_yaml(path))
    _verify_meta_current(parsed.meta, kind=path.name)
    _verify_unique_ids([c.id for c in parsed.cases], kind=path.name)
    if len(parsed.cases) != parsed.meta.case_count:
        raise ValueError(
            f"{path.name}: cases {len(parsed.cases)}件がmeta.case_count"
            f" {parsed.meta.case_count}と一致しません"
        )
    true_count = sum(1 for c in parsed.cases if c.expected.alcohol_involved)
    false_count = len(parsed.cases) - true_count
    if true_count != parsed.meta.true_count or false_count != parsed.meta.false_count:
        raise ValueError(
            f"{path.name}: true/false実測({true_count}/{false_count})が"
            f"meta({parsed.meta.true_count}/{parsed.meta.false_count})と"
            f"一致しません"
        )
    return parsed
```

- [ ] **Step 4.5: テストを実行して緑を確認する**

```bash
cd backend && uv run pytest tests/unit/g1gate/test_cases.py -v
```

Expected: 全件 PASS(docs実YAMLの回帰・fixtures・異常系6件)

- [ ] **Step 4.6: lint・basename一意性を確認してコミット**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
```

Expected: 緑・重複なし

```bash
git add backend/src/latch/g1gate/cases.py backend/tests/unit/g1gate/test_cases.py backend/tests/fixtures/g1gate/
git commit -m "feat: G1ゲート入力セット読み込み(cases.py)を実装" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 5: 照合・集計・閾値判定(g1gate/compare.py)

**Files:**
- Create: `backend/src/latch/g1gate/compare.py`
- Test: `backend/tests/unit/g1gate/test_compare.py`

**Interfaces:**
- Consumes: Task 4の `ParserStructCase`・`AlcoholCase`・`ErrorCase`(期待値型)。`ParserOutput`(intents/schema.py — 出力側の型)
- Produces: 照合関数群(`location_match`・`time_start_match`等)・`THRESHOLDS`・`compare_struct_case(case, structured, error) -> StructCaseOutcome`・`summarize_struct(outcomes) -> StructSummary`・`compare_alcohol_case`・`summarize_alcohol(outcomes) -> AlcoholSummary`・`alcohol_reference_counts(outcomes) -> dict`・`error_outcome(case, actual_code) -> ErrorCaseOutcome`(Task 6のreport・Task 7のrunnerが消費)

- [ ] **Step 5.1: 失敗テストを書く**

`backend/tests/unit/g1gate/test_compare.py` を新規作成:

```python
"""g1gate/compare.pyのunit(design §4-4・§2.9)。照合・集計・閾値の全ロジック。
すべて純粋関数のため実API不要。"""

from datetime import datetime

from latch.core.clock import JST
from latch.g1gate.cases import AlcoholCase, ErrorCase, ParserStructCase
from latch.g1gate.compare import (
    ALCOHOL_PRECISION_MIN,
    ALCOHOL_RECALL_MIN,
    THRESHOLDS,
    alcohol_reference_counts,
    budget_match,
    category_match,
    classify_alcohol,
    compare_alcohol_case,
    compare_struct_case,
    error_outcome,
    location_match,
    participants_match,
    summarize_alcohol,
    summarize_struct,
    time_start_match,
)
from latch.intents.schema import ParserOutput


def _dt(*args) -> datetime:
    return datetime(*args, tzinfo=JST)


def _output(**overrides) -> ParserOutput:
    """YAML期待値P-901(struct-minimal)に一致するParserOutput。"""
    base = {
        "category": {"primary": "drinking", "secondary": None},
        "alcohol_involved": True,
        "time": {
            "start": "2026-10-01T20:00:00+09:00",
            "end": None,
            "flexibility_minutes": None,
        },
        "location": {"name": "天文館", "radius_m": None, "flexibility": None},
        "budget": {"max": 3000, "currency": "JPY"},
        "participants": {"min": None, "max": None},
        "soft_constraints": [],
        "negative_constraints": [],
        "ng_unverifiable": [],
    }
    base.update(overrides)
    return ParserOutput.model_validate(base)


def _struct_case(**overrides) -> ParserStructCase:
    data = {
        "id": "P-901",
        "text": "今夜20時から天文館で飲みたい。ひとり3000円までで",
        "author_age": 26,
        "expected": {
            "category": "drinking",
            "alcohol_involved": True,
            "time": {"start": "2026-10-01T20:00:00+09:00", "end": None},
            "location": {"name": "天文館"},
            "participants": {"min": None, "max": None},
            "budget": {"max": 3000},
        },
        "needs_review": False,
    }
    for key, value in overrides.items():
        if key == "expected":
            data["expected"].update(value)
        else:
            data[key] = value
    return ParserStructCase.model_validate(data)


def _alcohol_case(expected: bool, case_id: str = "A-901") -> AlcoholCase:
    return AlcoholCase.model_validate(
        {
            "id": case_id,
            "text": "テスト",
            "author_age": 30,
            "expected": {"alcohol_involved": expected},
            "needs_review": False,
        }
    )


# --- フィールド照合(design §2.9) ---


def test_category_match_is_equality():
    assert category_match("drinking", "drinking")
    assert not category_match("meal", "drinking")


def test_time_start_match_is_instant_equality():
    assert time_start_match("2026-10-01T20:00:00+09:00", _dt(2026, 10, 1, 20, 0))
    # Z表記と+09:00表記の揺れは同一瞬間として一致(09 §4.3: 表記は問わない)
    assert time_start_match("2026-10-01T11:00:00Z", _dt(2026, 10, 1, 20, 0))
    assert not time_start_match(
        "2026-10-01T20:00:00+09:00", _dt(2026, 10, 1, 20, 30)
    )


def test_location_match_is_bidirectional_substring():
    # 確定値10: 地域の一致で判定(座標・表記の厳密一致は求めない)
    assert location_match("天文館", "天文館界隈")
    assert location_match("中央駅前", "鹿児島中央駅前")  # expected ⊂ actual
    assert location_match("鹿児島中央駅前", "中央駅前")  # actual ⊂ expected
    assert not location_match("天文館", "呉服町")


def test_location_match_normalizes_whitespace():
    assert location_match("天文 館", "天文館")
    assert location_match(" 天文館あたり ", "天文館あたり")


def test_location_match_empty_never_matches():
    # Review Focus #4: 空文字列は任意文字列の部分文字列になる — 正規化後空は不一致
    assert not location_match("", "天文館")
    assert not location_match("天文館", "")
    assert not location_match("", "")


def test_participants_match_is_pair_equality():
    assert participants_match((None, None), None, None)
    assert participants_match((2, 2), 2, 2)
    assert participants_match((2, 4), 2, 4)
    assert not participants_match((2, None), 2, 2)
    assert not participants_match((2, 4), 2, 2)


def test_budget_match_is_equality():
    assert budget_match(None, None)
    assert budget_match(3000, 3000)
    assert not budget_match(None, 3000)
    assert not budget_match(3000, 4000)


# --- structケース照合と集計 ---


def test_compare_struct_case_all_match():
    outcome = compare_struct_case(_struct_case(), _output(), error=None)
    assert outcome.error is None
    assert all(f.match for f in outcome.fields.values())
    assert set(outcome.fields) == {
        "category",
        "time_start",
        "location",
        "participants",
        "budget",
    }
    assert outcome.actual["category"]["primary"] == "drinking"
    assert outcome.actual["negative_constraints"] == []


def test_compare_struct_case_partial_mismatch():
    outcome = compare_struct_case(
        _struct_case(expected={"budget": {"max": 9999}}), _output(), error=None
    )
    assert outcome.fields["budget"].match is False
    assert outcome.fields["category"].match is True


def test_compare_struct_case_error_counts_all_mismatch():
    # Review Focus #2: 出力なし(422/503系)は全フィールド不一致。分母は減らさない
    outcome = compare_struct_case(_struct_case(), None, error="unstructurable")
    assert outcome.actual is None
    assert outcome.error == "unstructurable"
    assert all(not f.match for f in outcome.fields.values())


def test_summarize_struct_threshold_boundary():
    # 閾値は「>=」で判定(07 D-17)。17/20=0.85: category(0.85)は境界ちょうどで
    # 合格・time_start(0.90)は同率でも不合格 — 全フィールドが17/20になる合成で検証
    outcomes = []
    for i in range(20):
        case = _struct_case(id=f"P-{i:03d}")
        structured = _output() if i < 17 else None
        outcome = compare_struct_case(
            case, structured, error=None if i < 17 else "unstructurable"
        )
        outcomes.append(outcome)
    summary = summarize_struct(outcomes)
    assert summary.fields["category"].match == 17
    assert summary.fields["category"].total == 20
    assert summary.fields["category"].rate == 0.85
    assert summary.fields["category"].passed is True
    # time_start(0.90)は17/20=0.85のため不合格
    assert summary.fields["time_start"].passed is False
    assert summary.passed is False


def test_summarize_struct_passes_when_all_fields_meet():
    outcomes = [compare_struct_case(_struct_case(id=f"P-{i:03d}"), _output(), None) for i in range(5)]
    summary = summarize_struct(outcomes)
    assert all(f.rate == 1.0 for f in summary.fields.values())
    assert summary.passed is True


def test_summarize_struct_total_is_fixed_at_input_size():
    # 分母は入力セット件数から減らない(design §2.9: 全件への応答が前提)
    outcomes = [compare_struct_case(_struct_case(id=f"P-{i:03d}"), None, "llm_unavailable") for i in range(3)]
    summary = summarize_struct(outcomes)
    assert summary.fields["category"].total == 3


# --- alcohol照合と集計 ---


def test_classify_alcohol_four_cells():
    assert classify_alcohol(True, True) == "tp"
    assert classify_alcohol(True, False) == "fn"
    assert classify_alcohol(False, True) == "fp"
    assert classify_alcohol(False, False) == "tn"


def test_classify_alcohol_missing_output():
    # 出力なし: 正例はFN(見逃し)。負例は判定不能(unclassified)
    assert classify_alcohol(True, None) == "fn"
    assert classify_alcohol(False, None) == "unclassified"


def test_compare_alcohol_case_records_actual():
    ok = compare_alcohol_case(_alcohol_case(True), _output(), error=None)
    assert ok.classification == "tp"
    assert ok.actual is True
    failed = compare_alcohol_case(_alcohol_case(True), None, error="llm_unavailable")
    assert failed.classification == "fn"
    assert failed.actual is None


def test_summarize_alcohol_passes_at_precision_floor():
    # 09 §4.3: recall 100%・precision下限90%。tp=18/fp=2 → precision=0.90で合格
    outcomes = (
        [compare_alcohol_case(_alcohol_case(True, f"A-{i}"), _output(), None) for i in range(18)]
        + [compare_alcohol_case(_alcohol_case(False, f"B-{i}"), _output(alcohol_involved=True), None) for i in range(2)]
        + [compare_alcohol_case(_alcohol_case(False, f"C-{i}"), _output(alcohol_involved=False), None) for i in range(12)]
    )
    summary = summarize_alcohol(outcomes)
    assert (summary.tp, summary.fp, summary.fn, summary.tn) == (18, 2, 0, 12)
    assert summary.recall == 1.0
    assert summary.precision == 0.9
    assert summary.passed is True


def test_summarize_alcohol_fails_on_any_miss():
    outcomes = (
        [compare_alcohol_case(_alcohol_case(True, f"A-{i}"), _output(), None) for i in range(23)]
        + [compare_alcohol_case(_alcohol_case(True, "A-x"), _output(alcohol_involved=False), None)]
        + [compare_alcohol_case(_alcohol_case(False, f"C-{i}"), _output(alcohol_involved=False), None) for i in range(12)]
    )
    summary = summarize_alcohol(outcomes)
    assert summary.fn == 1
    assert summary.recall < ALCOHOL_RECALL_MIN
    assert summary.passed is False


def test_summarize_alcohol_fails_on_unclassified():
    # 出力なしの負例が1件でもあれば不合格(全ケースへの応答が前提)
    outcomes = (
        [compare_alcohol_case(_alcohol_case(True, "A-1"), _output(), None)]
        + [compare_alcohol_case(_alcohol_case(False, "B-1"), None, error="unstructurable")]
    )
    summary = summarize_alcohol(outcomes)
    assert summary.unclassified == 1
    assert summary.passed is False


def test_alcohol_reference_counts_are_not_a_verdict():
    # design §6-5: parser-struct側のalcohol期待値は参考値(合否判定に使わない)
    outcomes = [compare_struct_case(_struct_case(id=f"P-{i:03d}"), _output(alcohol_involved=False), None) for i in range(3)]
    reference = alcohol_reference_counts(outcomes)
    assert reference == {"tp": 0, "fp": 0, "fn": 3, "tn": 0}


# --- error_cases判定 ---


def test_error_outcome_ok_on_422():
    case = ErrorCase.model_validate(
        {
            "id": "E-901",
            "text": "今日誰かと飲みたい",
            "author_age": None,
            "expected": {"error": "422 VALIDATION_ERROR", "missing": ["time.start"]},
            "needs_review": False,
        }
    )
    ok = error_outcome(case, actual_code="422 VALIDATION_ERROR")
    assert ok.ok is True
    ng = error_outcome(case, actual_code="503 LLM_UNAVAILABLE")
    assert ng.ok is False


def test_thresholds_match_d17():
    assert THRESHOLDS == {
        "category": 0.85,
        "time_start": 0.90,
        "location": 0.90,
        "participants": 0.80,
        "budget": 0.90,
    }
    assert ALCOHOL_RECALL_MIN == 1.0
    assert ALCOHOL_PRECISION_MIN == 0.90
```

注記: `_output(alcohol_involved=True)` のような引数は `_output` の `base.update(overrides)` でトップレベルキーを差し替える(ネスト更新はしない — テスト内ではトップレベルの `alcohol_involved` のみ差し替えに使う)。

- [ ] **Step 5.2: テストを実行して失敗を確認する**

```bash
cd backend && uv run pytest tests/unit/g1gate/test_compare.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'latch.g1gate.compare'`

- [ ] **Step 5.3: 実装する**

`backend/src/latch/g1gate/compare.py` を新規作成:

```python
"""G1ゲートの照合・集計・閾値判定(design §2.9)。すべて純粋関数(実API不要)。

07 §5 D-17: category 85% / time.start 90% / location 90% / participants 80% / budget 90%
09 §4.3(FR-20): alcohol_involved recall 100%・precision下限90%
一致率の分母は入力セット件数で固定 — 出力の得られなかったケースで分母を
減らして合格に見せない(design §2.9「全件への応答が得られて初めて成立」)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from latch.g1gate.cases import AlcoholCase, ErrorCase, ParserStructCase
from latch.intents.schema import ParserOutput

# 07 §5 D-17(入力セット30件以上での基準)。判定は rate >= threshold
THRESHOLDS = {
    "category": 0.85,
    "time_start": 0.90,
    "location": 0.90,
    "participants": 0.80,
    "budget": 0.90,
}
ALCOHOL_RECALL_MIN = 1.0  # 09 §4.3: 正例の見逃し0件
ALCOHOL_PRECISION_MIN = 0.90  # 09 §4.3: 偽陽性2件まで許容(36件セット)

STRUCT_FIELDS = ("category", "time_start", "location", "participants", "budget")


def normalize_text(s: str) -> str:
    """空白正規化: 前後の除去+連続する空白類を1スペースへ(location一致の前処理)。"""
    return re.sub(r"\s+", " ", s.strip())


def category_match(expected: str, actual: str) -> bool:
    """category.primaryのLiteral等値(design §2.9)。"""
    return expected == actual


def time_start_match(expected_iso: str, actual: datetime) -> bool:
    """tz-awareの瞬間等値(+09:00/Z表記の揺れは吸収)。"""
    return datetime.fromisoformat(expected_iso) == actual


def location_match(expected: str, actual: str) -> bool:
    """地域一致(09 §4.3・確定値10)。空白正規化のうえ双方向部分一致。

    空文字列は任意文字列の部分文字列となるため、正規化後空は不一致とする
    (偽陽性防止。actual空はParserLocationのmin_length=1で通常起きない)。
    """
    e = normalize_text(expected)
    a = normalize_text(actual)
    if not e or not a:
        return False
    return e in a or a in e


def participants_match(
    expected: tuple[int | None, int | None], actual_min: int | None, actual_max: int | None
) -> bool:
    """(min, max)組の等値(None含む)。"""
    return expected == (actual_min, actual_max)


def budget_match(expected: int | None, actual: int | None) -> bool:
    return expected == actual


@dataclass(frozen=True)
class FieldResult:
    """1フィールドの照合結果(expected/actualはJSON安全値)。"""

    expected: object
    actual: object
    match: bool


@dataclass(frozen=True)
class StructCaseOutcome:
    """struct 1ケースの結果。actualはParserOutputのdict化(生応答記録・§2.10)。"""

    id: str
    fields: dict[str, FieldResult]
    alcohol_involved: FieldResult  # 参考値(design §6-5: 合否判定外)
    actual: dict | None  # 出力なし(422/503系)はNone
    error: str | None  # "unstructurable"|"llm_unavailable"|"unexpected:<Class>"|None


def compare_struct_case(
    case: ParserStructCase,
    structured: ParserOutput | None,
    error: str | None,
) -> StructCaseOutcome:
    """1ケースの照合。structured=None(出力なし)は全フィールド不一致扱い。"""
    if structured is None:
        fields = {
            name: FieldResult(
                expected=_expected_snapshot(case, name), actual=None, match=False
            )
            for name in STRUCT_FIELDS
        }
        alcohol = FieldResult(
            expected=case.expected.alcohol_involved, actual=None, match=False
        )
        return StructCaseOutcome(
            id=case.id, fields=fields, alcohol_involved=alcohol,
            actual=None, error=error,
        )
    dump = structured.model_dump(mode="json")
    fields = {
        "category": FieldResult(
            case.expected.category, structured.category.primary,
            category_match(case.expected.category, structured.category.primary),
        ),
        "time_start": FieldResult(
            case.expected.time.start, dump["time"]["start"],
            time_start_match(case.expected.time.start, structured.time.start),
        ),
        "location": FieldResult(
            case.expected.location.name, structured.location.name,
            location_match(case.expected.location.name, structured.location.name),
        ),
        "participants": FieldResult(
            [case.expected.participants.min, case.expected.participants.max],
            dump["participants"],
            participants_match(
                (case.expected.participants.min, case.expected.participants.max),
                structured.participants.min,
                structured.participants.max,
            ),
        ),
        "budget": FieldResult(
            case.expected.budget.max, structured.budget.max,
            budget_match(case.expected.budget.max, structured.budget.max),
        ),
    }
    alcohol = FieldResult(
        case.expected.alcohol_involved, structured.alcohol_involved,
        case.expected.alcohol_involved == structured.alcohol_involved,
    )
    return StructCaseOutcome(
        id=case.id, fields=fields, alcohol_involved=alcohol,
        actual=dump, error=error,
    )


def _expected_snapshot(case: ParserStructCase, name: str) -> object:
    """出力なし時のexpected記録(レポート用のJSON安全値)。"""
    if name == "category":
        return case.expected.category
    if name == "time_start":
        return case.expected.time.start
    if name == "location":
        return case.expected.location.name
    if name == "participants":
        return [case.expected.participants.min, case.expected.participants.max]
    return case.expected.budget.max


@dataclass(frozen=True)
class FieldSummary:
    match: int
    total: int
    rate: float
    threshold: float
    passed: bool


@dataclass(frozen=True)
class StructSummary:
    fields: dict[str, FieldSummary]
    passed: bool


def summarize_struct(outcomes: list[StructCaseOutcome]) -> StructSummary:
    """フィールド別一致率の集計とD-17閾値判定。分母=len(outcomes)で固定。"""
    total = len(outcomes)
    fields: dict[str, FieldSummary] = {}
    for name in STRUCT_FIELDS:
        match = sum(1 for o in outcomes if o.fields[name].match)
        rate = match / total if total else 0.0
        threshold = THRESHOLDS[name]
        fields[name] = FieldSummary(
            match=match, total=total, rate=rate,
            threshold=threshold, passed=rate >= threshold,
        )
    return StructSummary(fields=fields, passed=all(f.passed for f in fields.values()))


def alcohol_reference_counts(outcomes: list[StructCaseOutcome]) -> dict:
    """parser-struct側alcohol期待値の参考集計(design §6-5: 判定に使わない)。"""
    return {
        "tp": sum(1 for o in outcomes if o.alcohol_involved.expected and o.alcohol_involved.actual is True),
        "fp": sum(1 for o in outcomes if not o.alcohol_involved.expected and o.alcohol_involved.actual is True),
        "fn": sum(1 for o in outcomes if o.alcohol_involved.expected and o.alcohol_involved.actual is not True),
        "tn": sum(1 for o in outcomes if not o.alcohol_involved.expected and o.alcohol_involved.actual is False),
    }


def classify_alcohol(expected: bool, actual: bool | None) -> str:
    """TP/FP/FN/TN分類。出力なしは正例=FN(見逃し)・負例=unclassified。"""
    if actual is None:
        return "fn" if expected else "unclassified"
    if expected and actual:
        return "tp"
    if expected and not actual:
        return "fn"
    if not expected and actual:
        return "fp"
    return "tn"


@dataclass(frozen=True)
class AlcoholCaseOutcome:
    id: str
    expected: bool
    classification: str  # tp/fp/fn/tn/unclassified
    actual: bool | None
    actual_raw: dict | None
    error: str | None


def compare_alcohol_case(
    case: AlcoholCase, structured: ParserOutput | None, error: str | None
) -> AlcoholCaseOutcome:
    expected = case.expected.alcohol_involved
    if structured is None:
        classification = classify_alcohol(expected, None)
        return AlcoholCaseOutcome(
            id=case.id, expected=expected, classification=classification,
            actual=None, actual_raw=None, error=error,
        )
    actual = structured.alcohol_involved
    return AlcoholCaseOutcome(
        id=case.id, expected=expected,
        classification=classify_alcohol(expected, actual),
        actual=actual, actual_raw=structured.model_dump(mode="json"), error=error,
    )


@dataclass(frozen=True)
class AlcoholSummary:
    tp: int
    fp: int
    fn: int
    tn: int
    unclassified: int
    recall: float
    precision: float
    passed: bool


def summarize_alcohol(outcomes: list[AlcoholCaseOutcome]) -> AlcoholSummary:
    def count(kind: str) -> int:
        return sum(1 for o in outcomes if o.classification == kind)

    tp, fp, fn, tn = count("tp"), count("fp"), count("fn"), count("tn")
    unclassified = count("unclassified")
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    passed = (
        recall >= ALCOHOL_RECALL_MIN
        and precision >= ALCOHOL_PRECISION_MIN
        and unclassified == 0
    )
    return AlcoholSummary(
        tp=tp, fp=fp, fn=fn, tn=tn, unclassified=unclassified,
        recall=recall, precision=precision, passed=passed,
    )


@dataclass(frozen=True)
class ErrorCaseOutcome:
    id: str
    expected_error: str
    actual: str
    ok: bool


def error_outcome(case: ErrorCase, actual_code: str) -> ErrorCaseOutcome:
    """エラー付帯ケースは422 VALIDATION_ERRORが返ることのみ確認(§2.9)。"""
    return ErrorCaseOutcome(
        id=case.id,
        expected_error=case.expected.error,
        actual=actual_code,
        ok=actual_code == "422 VALIDATION_ERROR",
    )
```

- [ ] **Step 5.4: テストを実行して緑を確認する**

```bash
cd backend && uv run pytest tests/unit/g1gate/test_compare.py -v
```

Expected: 全件 PASS

- [ ] **Step 5.5: lint・全unitを確認してコミット**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
```

```bash
git add backend/src/latch/g1gate/compare.py backend/tests/unit/g1gate/test_compare.py
git commit -m "feat: G1ゲート照合・集計・閾値判定(compare.py)を実装" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 6: レポートYAML書き出し(g1gate/report.py)

**Files:**
- Create: `backend/src/latch/g1gate/report.py`
- Test: `backend/tests/unit/g1gate/test_report.py`

**Interfaces:**
- Consumes: Task 5の `StructSummary`・`AlcoholSummary`・`StructCaseOutcome`・`AlcoholCaseOutcome`・`ErrorCaseOutcome`。`latch.core.clock.JST`
- Produces: `sha256_text(text: str) -> str`・`sha256_file(path: Path) -> str`・`build_report(**kwargs) -> dict`・`write_report(report: dict, *, out_dir: Path, executed_at: datetime) -> Path`(Task 7の__main__が消費)

- [ ] **Step 6.1: 失敗テストを書く**

`backend/tests/unit/g1gate/test_report.py` を新規作成:

```python
"""g1gate/report.pyのunit(design §4-6)。レポートYAMLの構造とsha256。"""

from datetime import datetime
from pathlib import Path

import yaml

from latch.core.clock import JST
from latch.g1gate.cases import AlcoholCase, ParserStructCase
from latch.g1gate.compare import (
    compare_alcohol_case,
    compare_struct_case,
    error_outcome,
    summarize_alcohol,
    summarize_struct,
)
from latch.g1gate.report import build_report, sha256_file, sha256_text, write_report
from latch.intents.schema import ParserOutput

EXECUTED_AT = datetime(2026, 9, 28, 12, 34, 56, tzinfo=JST)


def _output(**overrides) -> ParserOutput:
    base = {
        "category": {"primary": "drinking", "secondary": None},
        "alcohol_involved": True,
        "time": {"start": "2026-10-01T20:00:00+09:00"},
        "location": {"name": "天文館"},
        "budget": {"max": 3000},
        "participants": {"min": None, "max": None},
    }
    base.update(overrides)
    return ParserOutput.model_validate(base)


def _struct_case() -> ParserStructCase:
    return ParserStructCase.model_validate(
        {
            "id": "P-901",
            "text": "t",
            "author_age": 26,
            "expected": {
                "category": "drinking",
                "alcohol_involved": True,
                "time": {"start": "2026-10-01T20:00:00+09:00"},
                "location": {"name": "天文館"},
                "participants": {"min": None, "max": None},
                "budget": {"max": 3000},
            },
            "needs_review": False,
        }
    )


def _build() -> dict:
    struct_outcomes = [compare_struct_case(_struct_case(), _output(), None)]
    alcohol_outcomes = [
        compare_alcohol_case(
            AlcoholCase.model_validate(
                {"id": "A-901", "text": "t", "expected": {"alcohol_involved": True}, "needs_review": False}
            ),
            _output(),
            None,
        )
    ]
    error_outcomes = [_error_outcome()]
    return build_report(
        executed_at=EXECUTED_AT,
        model="claude-haiku-4-5",
        llm_mode="real",
        parser_prompt_sha256=sha256_text("prompt"),
        input_sets={
            "parser_struct": {"file": "g1-parser-struct.yaml", "sha256": "a" * 64},
            "alcohol": {"file": "g1-alcohol.yaml", "sha256": "b" * 64},
        },
        base_current_datetime="2026-10-01T11:30:00+09:00",
        limit=None,
        struct=summarize_struct(struct_outcomes),
        struct_cases=struct_outcomes,
        alcohol_reference={"tp": 1, "fp": 0, "fn": 0, "tn": 0},
        alcohol=summarize_alcohol(alcohol_outcomes),
        alcohol_cases=alcohol_outcomes,
        error_results=error_outcomes,
        incomplete=False,
    )


def _error_outcome():
    from latch.g1gate.cases import ErrorCase

    return error_outcome(
        ErrorCase.model_validate(
            {"id": "E-901", "text": "t", "expected": {"error": "422 VALIDATION_ERROR"}, "needs_review": False}
        ),
        actual_code="422 VALIDATION_ERROR",
    )


def test_sha256_text_known_vector():
    # Review Focus #5: sha256が実際に内容を追跡する(既知ベクトルで検証)
    assert (
        sha256_text("abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_sha256_file_matches_text(tmp_path):
    path = tmp_path / "input.yaml"
    path.write_text("abc", encoding="utf-8")
    assert sha256_file(path) == sha256_text("abc")


def test_build_report_structure():
    report = _build()
    assert report["overall_passed"] is True
    assert report["incomplete"] is False
    meta = report["meta"]
    assert meta["trial_id"] == "g1-20260928-123456"
    assert meta["executed_at"] == EXECUTED_AT.isoformat()
    assert meta["llm_mode"] == "real"
    assert meta["model"] == "claude-haiku-4-5"
    assert meta["parser_prompt_sha256"] == sha256_text("prompt")
    assert meta["input_sets"]["parser_struct"]["sha256"] == "a" * 64
    assert meta["base_current_datetime"] == "2026-10-01T11:30:00+09:00"
    assert meta["limit"] is None
    # parser_struct: 閾値とフィールド別一致率・ケース詳細(actualを含む)
    ps = report["parser_struct"]
    assert ps["fields"]["category"]["rate"] == 1.0
    assert ps["passed"] is True
    (case_detail,) = ps["cases"]
    assert case_detail["id"] == "P-901"
    assert case_detail["actual"]["location"]["name"] == "天文館"
    assert case_detail["error"] is None
    # alcohol: TP/FP/FN/TN・recall/precision
    al = report["alcohol"]
    assert (al["tp"], al["fp"], al["fn"], al["tn"]) == (1, 0, 0, 0)
    assert al["passed"] is True
    assert report["alcohol_reference"] == {"tp": 1, "fp": 0, "fn": 0, "tn": 0}
    (error_detail,) = report["error_cases"]
    assert error_detail["ok"] is True


def test_build_report_fails_when_incomplete():
    # build_reportはincompleteをoverall_passedへ反映する
    report = _build_incomplete()
    assert report["incomplete"] is True
    assert report["overall_passed"] is False


def _build_incomplete() -> dict:
    struct_outcomes = [compare_struct_case(_struct_case(), None, "llm_unavailable")]
    alcohol_outcomes = []
    return build_report(
        executed_at=EXECUTED_AT,
        model="claude-haiku-4-5",
        llm_mode="real",
        parser_prompt_sha256="x",
        input_sets={},
        base_current_datetime="2026-10-01T11:30:00+09:00",
        limit=None,
        struct=summarize_struct(struct_outcomes),
        struct_cases=struct_outcomes,
        alcohol_reference={},
        alcohol=summarize_alcohol(alcohol_outcomes),
        alcohol_cases=alcohol_outcomes,
        error_results=[],
        incomplete=True,
    )


def test_write_report_roundtrip(tmp_path):
    report = _build()
    path = write_report(report, out_dir=tmp_path, executed_at=EXECUTED_AT)
    assert path.name == "g1-result-20260928-123456.yaml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded == report  # YAML往復で同一構造
```

注記: `_error_outcome` ヘルパーは `_build` の後ろに定義してある(実行時解決のため動作する)。整理して `_build` の前に移動してもよい。

- [ ] **Step 6.2: テストを実行して失敗を確認する**

```bash
cd backend && uv run pytest tests/unit/g1gate/test_report.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'latch.g1gate.report'`

- [ ] **Step 6.3: 実装する**

`backend/src/latch/g1gate/report.py` を新規作成:

```python
"""G1ゲート実行結果のレポートYAML書き出し(design §2.10・10 §5の証拠)。

meta(試験ID・実施日・環境・入力条件・合否)とケース別詳細(actual=Parser出力
全体を含む — テスト入力は生産データでなく機微制限対象外・09 §4.1・design §6-7)。
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from latch.core.clock import JST
from latch.g1gate.compare import (
    AlcoholCaseOutcome,
    AlcoholSummary,
    ErrorCaseOutcome,
    StructCaseOutcome,
    StructSummary,
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(
    *,
    executed_at: datetime,
    model: str,
    llm_mode: str,
    parser_prompt_sha256: str,
    input_sets: dict[str, dict],
    base_current_datetime: str,
    limit: int | None,
    struct: StructSummary,
    struct_cases: list[StructCaseOutcome],
    alcohol_reference: dict,
    alcohol: AlcoholSummary,
    alcohol_cases: list[AlcoholCaseOutcome],
    error_results: list[ErrorCaseOutcome],
    incomplete: bool,
) -> dict:
    """証拠レポートのdict構築(10 §5: 試験ID・実施日・環境・入力条件・合否)。"""
    overall = (
        struct.passed
        and alcohol.passed
        and not incomplete
        and all(e.ok for e in error_results)
    )
    return {
        "meta": {
            "trial_id": f"g1-{executed_at.astimezone(JST):%Y%m%d-%H%M%S}",
            "executed_at": executed_at.isoformat(),
            "llm_mode": llm_mode,
            "model": model,
            "parser_prompt_sha256": parser_prompt_sha256,
            "input_sets": input_sets,
            "base_current_datetime": base_current_datetime,
            "limit": limit,
        },
        "parser_struct": {
            "fields": {k: asdict(v) for k, v in struct.fields.items()},
            "passed": struct.passed,
            "cases": [
                {
                    "id": o.id,
                    "fields": {k: asdict(f) for k, f in o.fields.items()},
                    "alcohol_involved": asdict(o.alcohol_involved),
                    "actual": o.actual,
                    "error": o.error,
                }
                for o in struct_cases
            ],
        },
        "alcohol_reference": alcohol_reference,
        "alcohol": {
            **asdict(alcohol),
            "cases": [
                {
                    "id": o.id,
                    "expected": o.expected,
                    "classification": o.classification,
                    "actual": o.actual,
                    "actual_raw": o.actual_raw,
                    "error": o.error,
                }
                for o in alcohol_cases
            ],
        },
        "error_cases": [asdict(e) for e in error_results],
        "incomplete": incomplete,
        "overall_passed": overall,
    }


def write_report(report: dict, *, out_dir: Path, executed_at: datetime) -> Path:
    """g1-result-YYYYMMDD-HHMMSS.yaml(JST実行時刻)をout_dirへ書き出す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = executed_at.astimezone(JST).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"g1-result-{stamp}.yaml"
    path.write_text(
        yaml.safe_dump(report, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
```

- [ ] **Step 6.4: テストを実行して緑を確認する**

```bash
cd backend && uv run pytest tests/unit/g1gate/test_report.py -v
```

Expected: 全件 PASS。`asdict(alcohol)` が `passed` を含むため `test_build_report_structure` の `al["passed"]` はここから満たされる

- [ ] **Step 6.5: lint・全unitを確認してコミット**

```bash
make lint && make test
```

```bash
git add backend/src/latch/g1gate/report.py backend/tests/unit/g1gate/test_report.py
git commit -m "feat: G1ゲートレポートYAML書き出し(report.py)を実装" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 7: 実行経路とCLI(g1gate/runner.py・__main__.py・__init__.py)

**Files:**
- Create: `backend/src/latch/g1gate/runner.py`
- Create: `backend/src/latch/g1gate/__main__.py`
- Create: `backend/src/latch/g1gate/__init__.py`
- Test: `backend/tests/unit/g1gate/test_runner.py`

**Interfaces:**
- Consumes: Task 4の`load_parser_struct`/`load_alcohol`(とその戻り型)・Task 5の`compare_struct_case`/`compare_alcohol_case`/`error_outcome`/`summarize_*`・Task 6の`build_report`/`write_report`・`make_intent_parse_service`・`LLMGateway`+`StubLLM`(テスト)・`Settings`
- Produces: `build_gate_service(settings: Settings) -> IntentParseService`・`run_all(service, struct_set, alcohol_set, *, limit: int | None = None, echo: Callable[[str], None] | None = None) -> GateRunResult`・`main(argv) -> int`(exit 0=全合格/1=不合格・不完全/2=起動拒否)・`latch.g1gate` パッケージ公開IF

- [ ] **Step 7.1: 失敗テストを書く**

`backend/tests/unit/g1gate/test_runner.py` を新規作成:

```python
"""g1gate/runner.py+__main__.pyのunit(design §4-5)。StubLLM応答注入で実Gateway
+実IntentParseService(規則5正規化・ParserOutput検証込み)の全経路を検証。実APIゼロ。"""

from pathlib import Path

import pytest

from latch.core.clock import FakeClock
from latch.g1gate.cases import BASE_CURRENT_DATETIME, load_alcohol, load_parser_struct
from latch.g1gate.compare import summarize_alcohol, summarize_struct
from latch.g1gate.report import build_report, write_report
from latch.g1gate.runner import build_gate_service, run_all
from latch.intents.service import IntentParseService
from latch.llm.gateway import LLMGateway
from latch.llm.stub import StubLLM
from latch.settings import Settings

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "g1gate"

# fixturesのP-901期待値に完全一致する応答(他ケースは一部不一致になる)
STUB_OK_RESPONSE = {
    "category": {"primary": "drinking", "secondary": None},
    "alcohol_involved": True,
    "time": {
        "start": "2026-10-01T20:00:00+09:00",
        "end": None,
        "flexibility_minutes": None,
    },
    "location": {"name": "天文館", "radius_m": None, "flexibility": None},
    "budget": {"max": 3000, "currency": "JPY"},
    "participants": {"min": None, "max": None},
    "soft_constraints": [],
    "negative_constraints": [],
    "ng_unverifiable": [],
}

# 必須3のうち time / location を欠く応答 → ParserOutput検証で422相当
STUB_UNSTRUCTURABLE = {
    "category": {"primary": "drinking", "secondary": None},
    "alcohol_involved": True,
    "budget": {"max": 3000, "currency": "JPY"},
    "participants": {"min": None, "max": None},
    "soft_constraints": [],
    "negative_constraints": [],
    "ng_unverifiable": [],
}


def _service(response: dict | None = None, fail: bool = False) -> IntentParseService:
    """実Gateway(timeout・送信記録込み)+実IntentParseService(本番ロジック)。"""
    stub = StubLLM(parser_response=response, fail_parser=fail)
    gateway = LLMGateway(
        clock=FakeClock(BASE_CURRENT_DATETIME), parser=stub, embedding=stub, jev=stub
    )

    async def _none_lookup(provider: str, subject: str):
        return None  # 未登録JWT経路(design §2.8-A)

    return IntentParseService(
        clock=FakeClock(BASE_CURRENT_DATETIME), parser=gateway, user_lookup=_none_lookup
    )


@pytest.fixture
def struct_set():
    return load_parser_struct(FIXTURES / "struct-minimal.yaml")


@pytest.fixture
def alcohol_set():
    return load_alcohol(FIXTURES / "alcohol-minimal.yaml")


async def test_happy_path_runs_all_three_sets(tmp_path, struct_set, alcohol_set):
    from datetime import datetime

    from latch.core.clock import JST

    service = _service(response=STUB_OK_RESPONSE)
    run = await run_all(service, struct_set, alcohol_set)
    assert not run.incomplete
    assert len(run.struct_outcomes) == 2
    assert len(run.alcohol_outcomes) == 3
    assert len(run.error_outcomes) == 1
    # P-901は完全一致・P-902はtime/location/budgetが不一致(応答はP-901用)
    summary = summarize_struct(run.struct_outcomes)
    assert summary.fields["category"].match == 2  # 両ケースとも期待drinking
    assert summary.fields["time_start"].match == 1
    assert summary.fields["location"].match == 1
    # error_case E-901: STUB_OK応答は422にならない → ok=False(実測では422が出る想定)
    (e,) = run.error_outcomes
    assert e.ok is False
    # alcohol: A-901/A-902(true→true=tp×2)・A-903(false→true=fp×1)
    al = summarize_alcohol(run.alcohol_outcomes)
    assert (al.tp, al.fp, al.fn, al.tn) == (2, 1, 0, 0)
    # レポート生成から書き出しまで(tmp_path)
    executed_at = datetime(2026, 9, 28, 12, 0, tzinfo=JST)
    report = build_report(
        executed_at=executed_at,
        model="claude-haiku-4-5",
        llm_mode="stub",
        parser_prompt_sha256="x",
        input_sets={},
        base_current_datetime=BASE_CURRENT_DATETIME.isoformat(),
        limit=None,
        struct=summary,
        struct_cases=run.struct_outcomes,
        alcohol_reference={"tp": 2, "fp": 0, "fn": 0, "tn": 0},
        alcohol=al,
        alcohol_cases=run.alcohol_outcomes,
        error_results=run.error_outcomes,
        incomplete=run.incomplete,
    )
    path = write_report(report, out_dir=tmp_path, executed_at=executed_at)
    assert path.exists()


async def test_unstructurable_response_yields_422_outcomes(struct_set, alcohol_set):
    service = _service(response=STUB_UNSTRUCTURABLE)
    run = await run_all(service, struct_set, alcohol_set)
    assert not run.incomplete  # 422は正当な応答(実行不完全ではない)
    assert all(o.actual is None and o.error == "unstructurable" for o in run.struct_outcomes)
    (e,) = run.error_outcomes
    assert e.ok is True  # E-901は422期待どおり
    assert e.actual == "422 VALIDATION_ERROR"
    # alcohol側: 出力なし → 正例はFN・負例はunclassified
    al = summarize_alcohol(run.alcohol_outcomes)
    assert al.unclassified == 1
    assert al.passed is False


async def test_llm_unavailable_makes_run_incomplete(struct_set, alcohol_set):
    # Review Focus #2: 503系が1件でもあれば実行不完全(分母を減らして集計しない)
    service = _service(fail=True)
    run = await run_all(service, struct_set, alcohol_set)
    assert run.incomplete
    assert all(
        o.error == "llm_unavailable" for o in run.struct_outcomes
    )
    (e,) = run.error_outcomes
    assert e.actual == "503 LLM_UNAVAILABLE" and e.ok is False


async def test_rule5_normalization_applies_via_real_service(struct_set, alcohol_set):
    # design §4-5: negative_constraints非空の応答でも本番ロジック経由で
    # ng_unverifiableへ移る(規則5正規化の通過証明)
    response = {**STUB_OK_RESPONSE, "negative_constraints": ["会社関係の人は避けたい"]}
    service = _service(response=response)
    run = await run_all(service, struct_set, alcohol_set, limit=1)
    (o,) = run.struct_outcomes
    assert o.actual["negative_constraints"] == []
    assert o.actual["ng_unverifiable"] == ["会社関係の人は避けたい"]


async def test_limit_runs_subset_per_set(struct_set, alcohol_set):
    service = _service(response=STUB_OK_RESPONSE)
    run = await run_all(service, struct_set, alcohol_set, limit=1)
    assert len(run.struct_outcomes) == 1
    assert len(run.alcohol_outcomes) == 1
    assert len(run.error_outcomes) == 1


async def test_echo_reports_progress(struct_set, alcohol_set):
    lines: list[str] = []
    service = _service(response=STUB_OK_RESPONSE)
    await run_all(service, struct_set, alcohol_set, limit=1, echo=lines.append)
    assert lines == ["P-901: ok", "A-901: ok", "E-901: ok"]


def test_build_gate_service_with_stub_settings(monkeypatch):
    monkeypatch.delenv("LATCH_LLM_MODE", raising=False)
    monkeypatch.delenv("LATCH_ANTHROPIC_API_KEY", raising=False)
    service = build_gate_service(Settings())
    assert isinstance(service, IntentParseService)


def test_build_gate_service_real_requires_key(monkeypatch):
    # 鍵なしrealはfail-fast(make_intent_parse_service→build_llm_gateway経由)
    monkeypatch.delenv("LATCH_LLM_MODE", raising=False)
    monkeypatch.delenv("LATCH_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError):
        build_gate_service(Settings(llm_mode="real"))


def test_main_rejects_stub_mode(monkeypatch, capsys):
    # design §2.7: 起動検証 — stubのまま実測したと錯覚させない
    monkeypatch.delenv("LATCH_LLM_MODE", raising=False)
    monkeypatch.delenv("LATCH_ANTHROPIC_API_KEY", raising=False)
    from latch.g1gate.__main__ import main

    assert main([]) == 2
    assert "LATCH_LLM_MODE" in capsys.readouterr().err


def test_main_rejects_missing_key(monkeypatch, capsys):
    monkeypatch.setenv("LATCH_LLM_MODE", "real")
    monkeypatch.delenv("LATCH_ANTHROPIC_API_KEY", raising=False)
    from latch.g1gate.__main__ import main

    assert main([]) == 2
    assert "LATCH_ANTHROPIC_API_KEY" in capsys.readouterr().err
```

- [ ] **Step 7.2: テストを実行して失敗を確認する**

```bash
cd backend && uv run pytest tests/unit/g1gate/test_runner.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'latch.g1gate.runner'`

- [ ] **Step 7.3: runner.pyを実装する**

`backend/src/latch/g1gate/runner.py` を新規作成:

```python
"""G1ゲートの実行経路(design §2.8-A・§2.11)。

Gateway+IntentParseServiceを本番ロジックそのまで通す(HTTP層を経ない)。
- current_date用ClockはFakeClock(基準日時 — README「共通の前提」)
- user_lookupは常にNone(未登録JWT経路・M1 ws-2 §2.3と同一挙動・DB非依存)
- 実行時刻(証拠の実施日)は__main__がSystemClockで別途取得(arch test対応)
- 呼び出しは直列(§2.11 — レート制限との余裕・精度測定が目的でレイテンシ計測はM4)
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from latch.core.clock import FakeClock
from latch.g1gate.cases import BASE_CURRENT_DATETIME, AlcoholSet, ParserStructSet
from latch.g1gate.compare import (
    AlcoholCaseOutcome,
    ErrorCaseOutcome,
    StructCaseOutcome,
    compare_alcohol_case,
    compare_struct_case,
    error_outcome,
)
from latch.intents.errors import IntentsError, LLMUnavailableError, UnstructurableError
from latch.intents.service import IntentParseService, ParseResult, make_intent_parse_service
from latch.settings import Settings

GATE_AUTH_PROVIDER = "g1gate"
GATE_AUTH_SUBJECT = "g1gate"

ERROR_CODE_UNSTRUCTURABLE = "422 VALIDATION_ERROR"
ERROR_CODE_LLM_UNAVAILABLE = "503 LLM_UNAVAILABLE"


async def _none_user_lookup(provider: str, subject: str) -> uuid.UUID | None:
    return None


def build_gate_service(settings: Settings) -> IntentParseService:
    """基準日時のFakeClock+未登録JWT経路でIntentParseServiceを構成(§2.8-A)。

    llm_mode/鍵/プロンプト/スキーマの検証はmake_intent_parse_service→
    build_llm_gatewayが持つ(fail-fast)。
    """
    clock = FakeClock(BASE_CURRENT_DATETIME)
    return make_intent_parse_service(
        clock=clock, settings=settings, user_lookup=_none_user_lookup
    )


async def _parse_classified(
    service: IntentParseService, text: str
) -> tuple[ParseResult | None, str | None]:
    """1ケース実行。errorは "unstructurable"|"llm_unavailable"|"unexpected:<Class>"。"""
    try:
        result = await service.parse(
            text=text,
            auth_provider=GATE_AUTH_PROVIDER,
            auth_subject=GATE_AUTH_SUBJECT,
        )
        return result, None
    except UnstructurableError:
        return None, "unstructurable"
    except LLMUnavailableError:
        return None, "llm_unavailable"
    except IntentsError as exc:
        return None, f"unexpected:{type(exc).__name__}"


def _http_code(error: str | None) -> str:
    """error種を05 §5のcode表記へ(error_casesのレポート用)。"""
    if error == "unstructurable":
        return ERROR_CODE_UNSTRUCTURABLE
    if error == "llm_unavailable":
        return ERROR_CODE_LLM_UNAVAILABLE
    return error or ""


def _is_incomplete(error: str | None) -> bool:
    """503系(LLMUnavailable・予期しないIntentsError)は実行不完全(design §2.9)。"""
    return error is not None and (
        error == "llm_unavailable" or error.startswith("unexpected")
    )


@dataclass(frozen=True)
class GateRunResult:
    struct_outcomes: list[StructCaseOutcome]
    alcohol_outcomes: list[AlcoholCaseOutcome]
    error_outcomes: list[ErrorCaseOutcome]
    incomplete: bool


async def run_all(
    service: IntentParseService,
    struct_set: ParserStructSet,
    alcohol_set: AlcoholSet,
    *,
    limit: int | None = None,
    echo: Callable[[str], None] | None = None,
) -> GateRunResult:
    """struct 32件+alcohol 36件+error 3件を直列実行し、照合結果を返す。

    limitは各リストの先頭N件へ適用(部分実行・デバッグ用。集計形式は同一)。
    """
    struct_cases = struct_set.cases[:limit] if limit is not None else struct_set.cases
    alcohol_cases = alcohol_set.cases[:limit] if limit is not None else alcohol_set.cases
    error_cases = (
        struct_set.error_cases[:limit] if limit is not None else struct_set.error_cases
    )
    incomplete = False

    struct_outcomes: list[StructCaseOutcome] = []
    for case in struct_cases:
        result, error = await _parse_classified(service, case.text)
        structured = result.structured_intent if result is not None else None
        struct_outcomes.append(compare_struct_case(case, structured, error))
        incomplete = incomplete or _is_incomplete(error)
        if echo is not None:
            echo(f"{case.id}: {'ok' if error is None else error}")

    alcohol_outcomes: list[AlcoholCaseOutcome] = []
    for case in alcohol_cases:
        result, error = await _parse_classified(service, case.text)
        structured = result.structured_intent if result is not None else None
        alcohol_outcomes.append(compare_alcohol_case(case, structured, error))
        incomplete = incomplete or _is_incomplete(error)
        if echo is not None:
            echo(f"{case.id}: {'ok' if error is None else error}")

    error_outcomes: list[ErrorCaseOutcome] = []
    for case in error_cases:
        _, error = await _parse_classified(service, case.text)
        error_outcomes.append(error_outcome(case, _http_code(error)))
        incomplete = incomplete or _is_incomplete(error)
        if echo is not None:
            echo(f"{case.id}: {_http_code(error) or 'ok'}")

    return GateRunResult(
        struct_outcomes=struct_outcomes,
        alcohol_outcomes=alcohol_outcomes,
        error_outcomes=error_outcomes,
        incomplete=incomplete,
    )
```

- [ ] **Step 7.4: __main__.pyを実装する**

`backend/src/latch/g1gate/__main__.py` を新規作成:

```python
"""python -m latch.g1gate(design §2.7-A)。G1精度ゲートharnessのCLI。

実API経路はこのCLIのみ(make g1-gate)。pytest(make test/test-ci)には
実API呼び出しが構造的に存在しない(marker漏れの誤発火が原理的に起きない)。
起動時にllm_mode=realと鍵を検証し、不足なら即座に拒否する(誤実行防止)。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from latch.core.clock import SystemClock
from latch.g1gate.cases import BASE_CURRENT_DATETIME, load_alcohol, load_parser_struct
from latch.g1gate.compare import (
    alcohol_reference_counts,
    summarize_alcohol,
    summarize_struct,
)
from latch.g1gate.report import build_report, sha256_file, sha256_text, write_report
from latch.g1gate.runner import build_gate_service, run_all
from latch.intents.prompt import PARSER_SYSTEM_PROMPT
from latch.llm.anthropic import ANTHROPIC_PARSER_MODEL
from latch.settings import Settings

DEFAULT_ASSETS = Path("../docs/testassets")  # make g1-gateはbackend/をcwdとして起動


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.g1gate",
        description=(
            "G1精度ゲートharness(Parser構造化精度+alcohol_involved・09 §4.3)"
        ),
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=DEFAULT_ASSETS,
        help="入力セットディレクトリ(既定 %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="レポート出力ディレクトリ(既定 <assets>/results)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="各セット先頭N件のみ実行(部分実行・デバッグ用)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = Settings()
    # 起動検証(design §2.7): stubのまま実測したと錯覚させない
    if settings.llm_mode != "real":
        print(
            "ERROR: LATCH_LLM_MODE=real が必要です"
            "(make g1-gate は .env を --env-file で読みます)",
            file=sys.stderr,
        )
        return 2
    if not settings.llm_anthropic_api_key:
        print(
            "ERROR: LATCH_ANTHROPIC_API_KEY が未設定です(.env へ設定してください)",
            file=sys.stderr,
        )
        return 2

    struct_set = load_parser_struct(args.assets / "g1-parser-struct.yaml")
    alcohol_set = load_alcohol(args.assets / "g1-alcohol.yaml")
    out_dir = args.out if args.out is not None else args.assets / "results"

    service = build_gate_service(settings)
    executed_at = SystemClock().now()  # 証拠の実施日(Clock経由)
    run = asyncio.run(
        run_all(
            service,
            struct_set,
            alcohol_set,
            limit=args.limit,
            echo=lambda message: print(message, file=sys.stderr),
        )
    )
    report = build_report(
        executed_at=executed_at,
        model=ANTHROPIC_PARSER_MODEL,
        llm_mode=settings.llm_mode,
        parser_prompt_sha256=sha256_text(PARSER_SYSTEM_PROMPT),
        input_sets={
            "parser_struct": {
                "file": "g1-parser-struct.yaml",
                "sha256": sha256_file(args.assets / "g1-parser-struct.yaml"),
            },
            "alcohol": {
                "file": "g1-alcohol.yaml",
                "sha256": sha256_file(args.assets / "g1-alcohol.yaml"),
            },
        },
        base_current_datetime=BASE_CURRENT_DATETIME.isoformat(),
        limit=args.limit,
        struct=summarize_struct(run.struct_outcomes),
        struct_cases=run.struct_outcomes,
        alcohol_reference=alcohol_reference_counts(run.struct_outcomes),
        alcohol=summarize_alcohol(run.alcohol_outcomes),
        alcohol_cases=run.alcohol_outcomes,
        error_results=run.error_outcomes,
        incomplete=run.incomplete,
    )
    path = write_report(report, out_dir=out_dir, executed_at=executed_at)
    print(f"report: {path}")
    print(f"overall_passed: {report['overall_passed']} (incomplete={report['incomplete']})")
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7.5: __init__.pyを実装する**

`backend/src/latch/g1gate/__init__.py` を新規作成:

```python
"""G1精度ゲートharness(M1 ws-6)。CLI(make g1-gate)専用パッケージ。

pytest実行経路に実APIは存在しない(design §2.7-A)。公開IFの再exportのみ。
"""

from latch.g1gate.cases import (
    BASE_CURRENT_DATETIME,
    AlcoholSet,
    ParserStructSet,
    load_alcohol,
    load_parser_struct,
)
from latch.g1gate.compare import (
    ALCOHOL_PRECISION_MIN,
    ALCOHOL_RECALL_MIN,
    THRESHOLDS,
    summarize_alcohol,
    summarize_struct,
)
from latch.g1gate.report import build_report, write_report
from latch.g1gate.runner import GateRunResult, build_gate_service, run_all

__all__ = [
    "ALCOHOL_PRECISION_MIN",
    "ALCOHOL_RECALL_MIN",
    "BASE_CURRENT_DATETIME",
    "AlcoholSet",
    "GateRunResult",
    "ParserStructSet",
    "THRESHOLDS",
    "build_gate_service",
    "build_report",
    "load_alcohol",
    "load_parser_struct",
    "run_all",
    "summarize_alcohol",
    "summarize_struct",
    "write_report",
]
```

- [ ] **Step 7.6: テストを実行して緑を確認する**

```bash
cd backend && uv run pytest tests/unit/g1gate/ -v
```

Expected: 全件 PASS(cases・compare・report・runnerの4ファイル)

- [ ] **Step 7.7: lint・全unit・arch testを確認してコミット**

```bash
make lint && make test
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
```

Expected: 緑(arch testはg1gate/・anthropic.pyを自動スキャンし、直参照なしで通る)

```bash
git add backend/src/latch/g1gate/ backend/tests/unit/g1gate/test_runner.py
git commit -m "feat: G1ゲートharness CLI(runner+__main__)を実装" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 8: Makefile・.env.example・全体検証・実APIスモーク

**Files:**
- Modify: `Makefile`
- Modify: `.env.example`
- Create(実行時生成): `docs/testassets/results/g1-result-*.yaml`(スモークの証拠)

**Interfaces:**
- Consumes: Task 7のCLI(`python -m latch.g1gate`)
- Produces: `make g1-gate` ターゲット。検証結果(報告書の材料)

- [ ] **Step 8.1: Makefileへg1-gateターゲットを追加する**

`.PHONY` 行へ `g1-gate` を追記し、ターゲットを追加(geo-verifyの後ろに置く):

```make
.PHONY: setup up down ps logs lint test test-ci migrate geo-download geo-import geo-verify g1-gate
```

```make
g1-gate: ## G1精度ゲートharness(実API・.envにLATCH_LLM_MODE=real+LATCH_ANTHROPIC_API_KEY必須)
	cd backend && uv run --env-file ../.env python -m latch.g1gate
```

(`--limit` を付ける部分実行は make を経由せず `cd backend && uv run --env-file ../.env python -m latch.g1gate --limit 5` を直接実行する)

- [ ] **Step 8.2: .env.exampleへLATCH_LLM_MODEを追記する**

`.env.example` のLLMプロバイダ節へ追記:

```
# LLMモード(make g1-gate実行時のみreal。ci環境(compose api/worker)へは渡さない=stub維持)
# LATCH_LLM_MODE=real
```

- [ ] **Step 8.3: 全体検証(実APIなし)**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src
git diff main --stat -- backend/pyproject.toml backend/uv.lock
git diff main --name-only
```

Expected: lintクリーン・unit全緑・basename重複なし・時刻参照はcore/clock.pyのみ・依存差分はanthropicとpyyamlのみ・変更ファイルが§2.1・§2.2どおり

- [ ] **Step 8.4: test-ci(apiイメージ再ビルド後)**

```bash
docker compose build api
make test-ci
```

Expected: 全緑(マイグレーション追加なし・ciはllm_mode既定stubのため実API非依存の証明そのもの)

- [ ] **Step 8.5: 実APIスモーク実行(--limit 5)**

前提確認(実値は表示しない・§0-6):

```bash
rg -n '^LATCH_LLM_MODE=real' .env
rg -c '^LATCH_ANTHROPIC_API_KEY=..+' .env
```

両方成立していなければ **BLOCKED: .envのLATCH_LLM_MODE=real/LATCH_ANTHROPIC_API_KEY設定待ち(make g1-gateの前提・design §2.7)** として報告し、Step 8.6以降は実施しない(鍵の値への言及は禁止)。

成立している場合:

```bash
cd backend && uv run --env-file ../.env python -m latch.g1gate --limit 5; echo "exit=$?"
```

Expected: レポート `docs/testassets/results/g1-result-*.yaml` が生成され、meta.limit=5・overall_passed・incomplete=false が記録される。exit codeは0(全合格)か1(不合格 — 部分実行の合否はG1判定の材料ではなく、harness動作の証明が目的)。

**APIがスキーマを400で拒否した場合**(Review Focus #1)のフォールバック手順(design §2.2承認済みの段階的判断):
1. エラーメッセージ(invalid_request_error の schema に関する文言)を報告書へ転記する
2. `llm/anthropic.py` へ手書きスキーマ定数 `ANTHROPIC_PARSER_SCHEMA` を定義し、`AnthropicParserProvider.__init__` の `adapt_schema_for_anthropic(output_schema)` をこの定数へ切替える(呼び出し側のIFは不変)
3. `test_anthropic_provider.py` へ「定数がParserOutputのフィールド・列挙と対応していること」を機械検証する試験を追加する(ルート9フィールド・category.primaryの3値・alcohol_involved boolean・time.startのdate-time)
4. `make lint && make test` を緑にしてコミットし、報告書§6へ「手書き切替」を記録する

- [ ] **Step 8.6: スモークの証拠をコミットする**

```bash
cd /home/misty/Projects/latch  # worktreeルート(相対パス基準)
git add Makefile .env.example docs/testassets/results/
git commit -m "feat: make g1-gateターゲットと.env.exampleへLATCH_LLM_MODE追記" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

(.env.exampleの変更とMakefileとレポート証拠を1コミットに含める。レポートYAMLにAPI鍵が含まれていないことを確認してからaddする — レポートmetaは鍵のSHA等も載せない設計のため通常は問題ない)

### Task 9: 報告書

**Files:**
- Create: `docs/plans/M1/ws-6-report.md`

- [ ] **Step 9.1: 報告書を書く**

`docs/plans/M1/ws-6-report.md` を §7「報告形式」の7項目構成で作成する。スモークがBLOCKEDになった場合も「スモーク=前提待ち(理由)」として記録し、他項目は完了分をそのまま報告する(BLOCKED報告の体裁は維持)。

- [ ] **Step 9.2: コミットする**

```bash
git add docs/plans/M1/ws-6-report.md
git commit -m "docs: ws-6報告書" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## 付録A: 実行時の例外分類と集計規則の確定値(タスク間共有)

runnerの`_parse_classified`が返すerror種と、各セットでの扱い(design §2.9の実装規則):

| error種 | 発生条件 | struct 32件での扱い | alcohol 36件での扱い | error_cases 3件での扱い | incomplete |
|---|---|---|---|---|---|
| `None` | 正常応答 | フィールド照合 | TP/FP/TN分類 | ok=False(422でない) | 影響なし |
| `unstructurable` | UnstructurableError(422相当) | actual=None・全5フィールド不一致(分母32のまま) | expected=true→FN / expected=false→unclassified | ok=True(422期待どおり) | なし(正当な応答) |
| `llm_unavailable` | LLMUnavailableError(503相当) | actual=None・全フィールド不一致 | actual=None(FN/unclassified) | ok=False・actual="503 LLM_UNAVAILABLE" | **あり(全体FAIL)** |
| `unexpected:<Class>` | 上記以外のIntentsError | 同上 | 同上 | ok=False | **あり(全体FAIL)** |

unclassified>0の場合 alcohol.passed=False(全ケースへの応答が前提・design §2.9)。alcohol_reference(parser-struct側参考値)は合否判定に使わない(design §6-5)。

## 付録B: 用語・出典の対応表(実装者が迷ったときの参照)

| コード内の名前 | docs上の概念 | 出典 |
|---|---|---|
| `ANTHROPIC_PARSER_MODEL` | Parser系統プロバイダ(第一候補) | 07 §1表・T1 v0.2 §3 |
| `adapt_schema_for_anthropic` | structured outputs用スキーマの機械的後加工 | design §2.2(supervisor承認) |
| `BASE_CURRENT_DATETIME` | ゲート入力の基準日時(2026-10-01T11:30 JST) | docs/testassets/README.md「共通の前提」 |
| `THRESHOLDS` | D-17のフィールド別基準 | 07 §5 D-17 / 09 §4.3 |
| `ALCOHOL_RECALL_MIN`/`ALCOHOL_PRECISION_MIN` | recall 100%・precision下限90% | 09 §4.3(FR-20) |
| `location_match` | 「地域の一致で判定」(双方向部分一致) | 09 §4.3・確定値10 |
| `error_outcome` | エラー付帯ケースの422確認(分母外) | docs/testassets/README.md測定手順4 |
| `write_report` | 証拠保管(試験ID・実施日・環境・入力条件・合否) | 10 §5 / 12 §5 |
| `alcohol_reference_counts` | parser-struct側alcohol参考値 | design §6-5 |
| `--limit` | 部分実行・デバッグ | design §2.10 |
