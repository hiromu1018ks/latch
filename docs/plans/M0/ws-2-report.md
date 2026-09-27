# M0 ws-2(LLM Gateway) 実行報告

- ブランチ: m0-ws-2 / ベース: e21138c
- 日付: 2026-09-27
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | lint: `30 files already formatted` + `All checks passed!`(exit 0)/ test: `77 passed, 3 deselected in 0.35s` |
| 2 | 3系統の送信記録(G0文言) | PASS | `uv run pytest tests/unit/llm/test_gateway_parse.py tests/unit/llm/test_gateway_embed.py tests/unit/llm/test_gateway_jev.py -v` → `20 passed in 0.20s`(parse 10・embed 5・jev 5。ok/timeout/error/キャンセル記録試験を含む) |
| 3 | 決定性・注入・許可リスト・ファクトリ | PASS | `uv run pytest tests/unit/llm -v` → `47 passed in 0.32s`(test_send_record 5・test_stub 15・test_gateway_* 20・test_llm_factory 7) |
| 4 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットは `backend/src/latch/core/clock.py:33: return datetime.now(UTC)` の1行のみ。arch test `test_arch_no_direct_time.py` → `1 passed` |
| 5 | 依存追加なし | PASS | `git diff --stat main -- backend/pyproject.toml backend/uv.lock` → 出力なし(空) |
| 6 | 触るファイルがスコープどおり | PASS | `git diff --name-only main \| sort` → llm/6ファイル・tests/unit/llm/6ファイル・settings.py・本報告ファイルの計14ファイル(§4一覧と一致)。settings.py差分は追記7行のみ(既存行の変更・削除なし) |

## 固定値の変更有無(design.md §6・本計画§2)
- 送信記録=構造化ログ(latch.llm.send・SendRecord 7フィールド): 変更なし
- 系統別ABC×3 + StubLLM単一クラス: 変更なし
- timeout 10/2/6秒のGateway内強制(Timeouts上書き・envなし): 変更なし
- スタブ応答=コンストラクタ上書き+遅延/エラー注入: 変更なし

## コミット一覧
```
7a3b7d5 fix: キャンセル時の送信記録とスタブ応答の防御的コピー(レビュー指摘)
a8978a8 feat: latch.llm公開IFの再export(M1/M2呼び出し側の窓口)
1fd5b47 feat: LLM設定4項目とbuild_llm_gatewayファクトリ(設定→StubLLM)
2e7704a feat: Embedding/Jev系統メソッド(768次元・intent_ids記録・timeout)
58e965f feat: LLMGateway共通経路とParser系統(timeout・送信記録・C4)
9413ee5 feat: スタブのレイテンシ注入とエラー注入(10 第4.5節障害再現)
c01a593 feat: 系統別プロバイダABCと決定的スタブ(10 第1節テストモード)
3ed458c feat: LLM Gateway例外階層とSendRecord送信記録(許可リスト・08 第2.4節)
```

## 補足(詰まった点・判断した点があれば)
- 計画書記載のコードがそのままではruffで赤になる箇所が3件あり、意味を変えない最小修正をした(毎コミットのlint規律§0に従うため):
  - `stub.py`: `.encode("utf-8")` → `.encode()`(UP012。デフォルトUTF-8と同一動作)
  - `test_stub.py`: 89字コメントを2行へ折返し(E501)
  - `gateway.py`: 計画書の未使用import `datetime` を削除(F401)・`parse_intent` のdocstring 92字を複数行化(E501)。`test_gateway_parse.py` の `_gateway` ヘルパ行は `ruff format` の標準折返しで解消
- Task 3の `test_stub_zero_delay_by_default` は実装前(RED段階)からPASSする性質のテスト(既定動作=遅延なしと「機能なし」が一致するため)。遅延・fail系の他4件は `TypeError` でREDを確認した後GREENとした。
- 仕様矛盾は発見されず。design.md §6の4論点はすべて推奨どおり固定値で実装し、変更していない。
- **最終レビュー(fresh reviewer)での指摘2件をfix passで修正**(TDD: 追加テストRED→GREEN→スイート全体グリーン):
  - `gateway.py`: 呼び出し開始後の外部キャンセル(CancelledError)でも送信記録(status="error"/error_code="CancelledError")を出してから伝播させる(design §3.1「成否にかかわらず」・08 第3節の開示要件をキャンセルまで拡張する解釈)。テスト `test_parse_cancellation_records_then_reraises`
  - `stub.py`: 応答を格納時・返却時にdeepcopy(消費者が応答を書き換えてもデフォルト応答定数・他インスタンス・同一インスタンスの再呼び出しが汚染されない。決定性の前提)。テスト `test_stub_responses_are_not_shared_mutable`
  - 見送り(minor・M1/M2時に検討): プロバイダが自前で送出したLLMTimeoutErrorはstatus="error"で記録される(系統timeoutとの区別はerror_codeで可能)/ `latch.llm` 再exportへのSystemName・SendStatus・LOGGER_NAME追加(計画Task 7の一覧どおりのため追加せず)
- **M1への引継ぎ事項**(レビューのRecommendation): parse API統合時、`latch.llm.send` のINFO出力がappのログ設定で確実に出るようにすること(専用ハンドラ or ログ設定。現状main.pyはロギング設定なし)
