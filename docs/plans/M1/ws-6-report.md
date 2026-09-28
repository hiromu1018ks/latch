# M1 ws-6 実装報告書(実プロバイダadapter + G1精度ゲートharness)

- ブランチ: `ws-6-g1gate`(main f6626faからworktree作成)
- 実施日: 2026-09-28
- **結果: BLOCKED(実APIスモークがAPI鍵の401で未成立。harness実装・全unit・test-ciは完了)**

## 1. サマリ(完了条件7項目・§6)

| # | 完了条件 | 結果 | 証拠 |
|---|---|---|---|
| 1 | `make lint`・`make test` 緑 | ✅ | `make lint` → All checks passed(132 files formatted)・`make test` → **529 passed, 94 deselected**(623件収集) |
| 2 | `make test-ci` 緑(`docker compose build api` 再ビルド後) | ✅ | `docker compose build api` 成功 → `make test-ci` → **621 passed**(exit 0)。マイグレーション追加なし。compose.yaml・Dockerfile・docker/は無差分・Settings既定stubのため**ci環境は実APIに依存しない**(test-ci緑がその証明) |
| 3 | 起動検証のunit試験立証+実APIスモーク成功 | ⚠️ 前半✅/後半**BLOCKED** | 起動検証: `test_main_rejects_stub_mode`・`test_main_rejects_missing_key`・`test_factory_real_requires_api_key`等で立証(exit 2)。スモーク: 後述§5(API鍵401) |
| 4 | 時刻直参照が`core/clock.py`のみ | ✅ | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic' backend/src` → `core/clock.py:33` の1件のみ |
| 5 | 依存追加がanthropic・pyyamlのみ | ✅ | `git diff main -- backend/pyproject.toml` → 追加2行(`anthropic>=1.8.0`・`pyyaml>=6.0.3`)のみ |
| 6 | 触ったファイルが§2.1・§2.2どおり | ✅ | `git diff main --name-only` → 22ファイルすべて一覧内(llm/はgateway.py+新規anthropic.pyのみ・intents/はservice.pyのみ・compose/Dockerfile/docker無差分) |
| 7 | テストbasename一意 | ✅ | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 空 |

## 2. コミット一覧

```
b45c3fe fix: structured outputs非対応の制約キーワードをスキーマ後加工で除去(レビューImportant)
9c190a2 feat: make g1-gateターゲットと.env.exampleへLATCH_LLM_MODE追記
727a403 fix: SDK 1.xが廃止したtemperature引数を除去(決定性はstructured outputsが保証)
5e90cc1 fix: LATCH_ANTHROPIC_API_KEYのenv名写像をaliasへ修正(env_prefix自動写像の不整合)
405428c feat: G1ゲートharness CLI(runner+__main__)を実装
59b83d1 feat: G1ゲートレポートYAML書き出し(report.py)を実装
7d1def9 feat: G1ゲート照合・集計・閾値判定(compare.py)を実装
8a84276 feat: G1ゲート入力セット読み込み(cases.py)を実装
9adbc01 feat: build_llm_gatewayへllm_mode=real分岐を追加しParserへプロンプト/スキーマ注入
ad09507 feat: AnthropicParserProvider(structured outputs+スキーマ後加工)を実装
f33fbc3 feat: anthropic・pyyaml依存とLATCH_ANTHROPIC_API_KEY設定を追加
```

(11コミット。`git log --oneline main..HEAD` の出力)

## 3. テスト構成

新規5ファイル(56試験)+追記1ファイル(+5試験)。`make test` 収集件数は **561 → 623(529 passed + 94 deselected)**(+61件増・deselectedはintegration markerのまま増減なし)。

| ファイル | 件数 | 主な試験 |
|---|---|---|
| `tests/unit/llm/test_anthropic_provider.py`(新規) | 10 | timeout同値ピン・鍵空拒否・clientオプション・request_shape({current_date}差し替え・output_config・後加工スキーマ)・SDK例外素通り・adapt_schema構造ピン・決定性・**非対応キーワード不在の負ピン** |
| `tests/unit/g1gate/test_cases.py`(新規) | 10 | docs実YAML回帰(32+3件・id一意・meta一致)・期待値構造・fixtures・異常系6件(基準日時・draft・件数不一致・id重複・必須欠損・true数不一致) |
| `tests/unit/g1gate/test_compare.py`(新規) | 22 | 5フィールド照合(category/time.start/location双方向部分一致+空不一致/participants/budget)・分母固定・閾値境界(17/20=0.85)・alcohol TP/FP/FN/TN・recall/precision床・unclassified不合格・error_case 422確認・THRESHOLDSピン |
| `tests/unit/g1gate/test_report.py`(新規) | 5 | sha256既知ベクトル・レポート構造(meta・actual全体)・incomplete反映・YAML往復 |
| `tests/unit/g1gate/test_runner.py`(新規) | 10 | StubLLM注入で実Gateway+実IntentParseService全経路(happy path・422・503 incomplete・規則5正規化・limit・echo)・build_gate_service・CLI起動拒否(stub/鍵なし=exit 2) |
| `tests/unit/llm/test_llm_factory.py`(追記) | +5 | defaults 5項目化・env経由鍵読み込み・real構成(ParserのみAnthropic・Embedding/Jev stub)・real要否fail-fast・make_intent_parse_service注入・unknown mode修正 |

## 4. 検証結果

| コマンド | 結果 |
|---|---|
| `make lint` | ✅ All checks passed |
| `make test` | ✅ 529 passed, 94 deselected |
| `docker compose build api` | ✅ 成功(uv syncがanthropic・pyyamlを拾いapi起動OK) |
| `make test-ci` | ✅ 621 passed |

## 5. 実APIスモーク — **BLOCKED(前提待ち)**

- 事前確認: `rg '^LATCH_LLM_MODE=real' .env` ✅・`rg -q '^LATCH_ANTHROPIC_API_KEY=..+' .env` ✅(設定は存在・実値未参照)
- 実行コマンド: `cd backend && uv run --env-file ../.env python -m latch.g1gate --limit 5`
- 結果: **exit 2(初回: env名写像バグ→修正)→ exit 1(全ケース llm_unavailable)**。単発実測で例外を確定: `anthropic.AuthenticationError: Error code: 401 - {'error': {'message': 'token expired or incorrect', 'type': '401'}}`
- **原因: .env の LATCH_ANTHROPIC_API_KEY 実値がAPIに拒否されている(期限切れまたは誤り)**。実値はスーパーバイザー管理(§0.6・§3)のため実装側では対処不可
- レポート: 401による失敗レポート2件が生成されたが不合格(incomplete=true)であり10 §5の合格証拠に不適切なためコミットせず削除
- **再実行手順(鍵が有効になり次第)**: 上記コマンド1本のみ(実装側の残作業なし)。`--limit 5` で meta.limit=5 のレポートが `docs/testassets/results/g1-result-*.yaml` へ生成される

## 6. design §2.2 スキーマ供給源の決着

**機械的後加工(`adapt_schema_for_anthropic`)で実装**。手書きスキーマ定数への切替は不使用。ただし401により実APIでの受け入れ確認は未到達(後加工自体の構造はunit試験で立証済み:$defs完全解決・全object required埋め+additionalProperties=false・enum保持・**レビュー指摘を受けstructured outputs非対応キーワード(minLength等)の除去を追加**(b45c3fe)・非対応キーワード不在の負ピン試験)。鍵解消後のスモークで400が出た場合は計画書Step 8.5のフォールバック手順(手書き切替)を別途実施する。

## 7. 注記(実装中の判断・スコープ外)

### 7.1 仕様・SDK実態との不一致への対応(計画書コードからの逸脱・すべてledger記録済み)

1. **env名写像バグ(計画書Settingsコード)**: `env_prefix="LATCH_"`の自動写像は`LATCH_LLM_ANTHROPIC_API_KEY`を探すが、仕様env名は`LATCH_ANTHROPIC_API_KEY`(T1・.env・Makefile・テストのLLM_ENV_VARSが一致)。`Field(validation_alias=AliasChoices("LATCH_ANTHROPIC_API_KEY", "llm_anthropic_api_key"))`で修正(5e90cc1)。env読み込み試験を追加し再発防止
2. **temperature引数のSDK 1.x廃止**: SDK 1.8.0の`messages.create`に`temperature`引数は存在せず(渡すとTypeError=全呼び出しllm_unavailable)、第一級引数での指定が不可能。design §2.6のtemperature=0からは逸脱するが、出力の決定性はstructured outputs(output_config.format)がAPI側で保証するため**temperature省略**とした(727a403)。test_request_shapeのピンも更新
3. **location空白正規化(計画書内矛盾)**: 計画書テストの`location_match("天文 館","天文館")==True`は計画書実装の正規化(空白を1スペースへ圧縮)では通らない(語間空白が残る)。確定値10「表記の揺れは一致扱い」の意図を優先し空白除去(`re.sub(r"\s+","",s.strip())`)へ変更。normalize_textはlocation_match専用で影響は閉じている
4. **load_alcoholのエラーメッセージ文言**: 計画書テストの`match="true_count"`が計画書実装メッセージに含まれず。テスト側(エラー種別判別可能性)を優先しメッセージへ`true_count/false_count`語を追加
5. **SDKのtimeout保持実態**: SDK 1.8.0は`AsyncAnthropic(timeout=10.0)`のスカラー値をそのまま保持しTimeoutオブジェクトへ正規化しないため、テストの検証を`client.timeout == 10.0`(float等値)へ変更(計画書注記のテスト側SDK追随規定を適用・実装は不変)
6. **lint(ruff format/check)対応**: 計画書コードの折り返し・docstring長・未使用import(Path)をruffが指摘したため整形・文言短縮で対応(機能不変)

### 7.2 レビュー(最終全体レビュー・subagent実施)

Critical 0件・Important 1件・Minor 3件。**Important(minLength等の非対応キーワードがスキーマ後加工を透過し実APIで400拒否の恐れ)は修正済み**(b45c3fe・TDD RED→GREEN)。Minor 3件は以下の通り対応見送り(次単位以降の判断に委ねる):

- `__main__`のYAML読み込み例外が未捕獲でトレースバック+exit 1(exit 2の起動拒否と区別不能)
- D-17「30件以上」前提をharnessが検証しない(`--limit 5`で5/5一致だとoverall_passed=trueのレポートが出る。計画書が部分実行は証拠外と明言済みのため運用で防止可能)
- レポートファイル名が秒単位のため同一JST秒内の再実行で前回レポートを無警告上書き

### 7.3 スコープ外と判断し作らなかったもの(design §2.12どおり)

- Embedding/Jev系統の実adapter・フォールバックLLM・circuit breaker(M2)
- Parserの縮退先(Bedrock東京・OpenAI)切替実装(T1 §5)
- 系統別llm_mode設定(§2.4-B)・プロンプトキャッシュ・cost実測レポート(M4)
- pytest marker・実API試験・HTTP層real試験・並列実行・レイテンシ測定
- ゲート入力セットの変更・G1判定のSTATUS.md記載(スーパーバイザー領域)

### 7.4 §0.1着手条件の記録

すべて成立(b9769ff=HEAD祖先・`backend/src/latch/llm/`6ファイル存在・`docs/testassets/`3ファイル+`status: confirmed`ヒット)。ただし`git log --oneline -8`ではb9769ffは範囲外(計画書作成後のdocsコミット5件が積まれたため・merge-baseで祖先であることを確認)。
