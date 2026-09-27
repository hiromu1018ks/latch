# M1 ws-2 実装報告(Intent Parser + POST /v1/intents/parse)

結果: 実装完了。`make lint` 緑・`make test` 268 passed(新規60件含む・失敗は計画書§0.6の既知1件のみ)。test-ci はスーパーバイザー検証待ち。逸脱3件は「計画からの逸脱・判断」に記録。

- 作業単位: ws-2(design: docs/plans/M1/ws-2-design.md / plan: docs/plans/M1/ws-2-plan.md)
- 実装日: 2026-09-27
- ブランチ: m1-ws-2(worktree /home/misty/.herdr/worktrees/latch/m1-ws-2)

## 実装概要

作成・変更ファイルは計画書 §2 の一覧どおり(過不足なし)。

作成(intentsパッケージ・7ファイル):

| ファイル | 内容 |
|---|---|
| `backend/src/latch/intents/__init__.py` | 公開IFの再export(例外・プロンプト・スキーマ・補完・サービス・ルータ) |
| `backend/src/latch/intents/errors.py` | IntentsError基底 + LLMUnavailableError(503)/UnstructurableError(422)/DependencyUnavailableError(503) |
| `backend/src/latch/intents/prompt.py` | PARSER_SYSTEM_PROMPT(07 §2全文・行リスト+join形式)+ format_parser_system_prompt |
| `backend/src/latch/intents/schema.py` | ParserOutputと部分モデル・WARNING_MESSAGE_NG_DOWNGRADED |
| `backend/src/latch/intents/completion.py` | D-19補完の単一規則(DEFAULT_RADIUS_M・DEFAULT_PARTICIPANTS・default_time_end・expires_at_candidates・nearest_expires_at) |
| `backend/src/latch/intents/service.py` | SupportsParseIntent(Protocol)・ParseWarning・ParseResult・IntentParseService・make_intent_parse_service |
| `backend/src/latch/intents/routes.py` | parse_router・ParseRequest/ParseResponse・get_intent_parse_service |

作成(試験・7ファイル):

- `backend/tests/unit/intents/`(__init__.pyなし): test_errors.py・test_prompt.py・test_parser_output.py・test_completion.py・test_service.py・test_parse_routes.py
- `backend/tests/integration/test_intents_parse_api.py`(収集確認まで・実行はスーパーバイザー検証)

変更(§2.2 どおり main.py のみ):

- `backend/src/latch/main.py` — parse_router の include・lifespanのサービスごとの独立スキップ判定(engineはauthと共有取得)・IntentsErrorハンドラ1個・create_app第4引数 intent_parse_service・docstring更新

## 検証結果

- make lint: グリーン(`87 files already formatted` / `All checks passed!`)
- make test: **1 failed, 268 passed(66 deselected)** — 失敗は§0.6の既知1件のみ。新規unit試験60件はすべて緑(内訳: test_errors 3・test_prompt 3・test_parser_output 18・test_completion 8・test_service 15・test_parse_routes 13)
- 既知1件の切り分け: ベースコミット12bb7cc(ws-2差分なし)で `uv run pytest tests/unit/auth/test_tokens.py` を実行し同一失敗(ExpiredSignatureError)を確認 → ws-2起因でないことを証明
- rg 時刻参照検査: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットは `backend/src/latch/core/clock.py:33` のみ(arch test も make test 内で緑)
- git diff --stat(12bb7cc..HEAD): intents/ 7ファイル・main.py・tests/unit/intents/ 6ファイル・test_intents_parse_api.py・ws-2-plan.md(checkbox更新)のみ。§3.1 禁止領域(llm/・auth/・core/・geo/・worker/・users/・alembic/・settings.py・pyproject.toml・uv.lock・compose.yaml・Makefile等)に差分なし
- TODO/FIXME/PLACEHOLDER 検査: `rg -n 'TODO|FIXME|PLACEHOLDER' backend/src/latch/intents backend/tests/unit/intents …` ヒットなし

## test-ci(スーパーバイザー検証待ち)

- test-ci=スーパーバイザー検証待ち(STATUS運用ルール)
- 検証手順: cd <リポジトリルート> && docker compose build api && make test-ci
  (make test-ci はapiイメージを再ビルドしないため、コード変更後は build api が必須)
- 対象: `backend/tests/integration/test_intents_parse_api.py`(5件。design §4.2-1〜5。収集確認済み・全体収集335件でimportエラーなし)

## 完了条件の達成状況(計画書 §4)

1. **make lint グリーン**: 上記のとおり(`All checks passed!`)
2. **make test グリーン(既知1件除く・新規60件)**: 268 passed + 既知1件。新規内訳は検証結果のとおり
3. **D-17切替・D-04 warnings・規則5正規化・user_id帰属・current_date JST が関数名として存在し通過**:
   - D-17(503/422切替): test_llm_unavailable_maps_to_503 / test_unstructurable_maps_to_422(test_parse_routes.py)、test_provider_error_maps_to_503_llm_unavailable / test_timeout_maps_to_503_llm_unavailable / test_missing_required_field_maps_to_422_unstructurable(test_service.py)
   - D-04(warnings生成): test_multiple_ng_unverifiable_produce_warning_per_condition(test_service.py)/ test_warnings_shape_matches_05_section5_example(test_parse_routes.py)
   - 規則5正規化: test_negative_constraints_are_normalized_into_ng_unverifiable(test_service.py)
   - user_id帰属: test_lookup_hit_passes_str_user_id_to_parser / test_lookup_none_passes_none_user_id / test_lookup_failure_maps_to_503_dependency_unavailable(test_service.py)
   - current_date JST暦日付(UTC 15:00境界): test_current_date_uses_jst_calendar_date_across_midnight(test_service.py)
4. **プロンプト全文ピン留め**: test_prompt_is_pinned_to_docs_07_section2(test_prompt.py)がdocs/07-jev-llm-spec.md最初の```textブロックとの一致を検証して緑
5. **時刻参照検査**: ヒットは core/clock.py のみ(上記)
6. **差分範囲**: §2 の一覧どおり・禁止領域に差分なし(上記)
7. **integration試験の存在と§4.2-1〜5カバー**: test_intents_parse_api.py の test_1〜test_5 が対応。実行(test-ci)はスーパーバイザー検証待ち

## 計画からの逸脱・判断

1. **コミット実施(§0.2からの逸脱)**: 計画書§0.2は「git commit はしない(コミットはスーパーバイザーが行う)」と定めていた。これに対しスーパーバイザーから追加指示(2026-09-27「このワークツリーのブランチ(m1-ws-2)にコミットする。mainには触らない」)があったため、追加指示を優先してタスク単位のコミットをm1-ws-2へ行った(6+2コミット・mainへの操作なし)。追加指示が本報告時点で最新の明示指示であることを優先の根拠とする
2. **既知1件の切り分け方法(git stashの代替)**: コミット運用としたためworktreeに差分がなくgit stashでws-2差分を外せない。代わりにベースコミット12bb7ccをdetached HEADでチェックアウトして同一テストを実行し、同一失敗の再現を確認した(検証目的は同等・検証後にm1-ws-2へ復帰)
3. **Task 6コミットでのadd漏れ**: Task 6のコミット(1186aa4)で test_parse_routes.py のaddを忘れ、追い込みコミットで組み込んだ(内容の変更なし・履歴に残る)
