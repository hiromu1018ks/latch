# M1 ws-6-g1fix 実装報告書(G1 FN=A-034対応のプロンプト修正)

- ブランチ: `ws-6-g1fix`(supervisor指示書による単発タスク・計画書なし)
- 実施日: 2026-09-28
- **結果: DONE(Parser構造化ゲート合格・alcoholゲート合格 recall/precision 100%・overall_passed=true)**

## 1. 変更内容

2026-09-28のG1全件実測(`g1-result-20260928-203517.yaml`: Parser構造化は合格・alcoholは
recall 23/24=95.8%で不合格、FN=A-034「barでコーラだけ飲む」をfalse判定)に対し、
オーナー承認済みの規則7改訂文面を適用した。原因は「バー等の場所の語→true」と
「アルコールを指さない用法はfalse」の優先関係がプロンプトに明示されていなかったこと。

| ファイル | 変更 |
|---|---|
| `backend/src/latch/intents/prompt.py` | `PARSER_SYSTEM_PROMPT` 規則7へ2項目(場所の語の優先・ノンアルコール明示)を5行挿入。既存行は不変。挿入行のうち「ノンアルコールビール」行は結合後の行幅が東アジア文字幅でちょうど88のためruff formatの指示どおり1行(分割なし)。値はdocs §2全文と1文字・改行位置まで一致 |
| `docs/07-jev-llm-spec.md` | §2プロンプト全文へ同一の5行を挿入(§2の```textブロック目=pin対象)。文書バージョンv0.5→v0.6、冒頭変更点へ「v0.6(2026-09-28): 規則7に場所の語の優先順位とノンアルコール明示を追記(G1実測FN=A-034対応・オーナー承認)」を追記、作成日行へv0.6更新を反映。§2「alcohol_involvedの判定と伝播」の規則7言い換え箇所へ優先関係1文を最小同期 |

改訂後の規則7(挿入部分のみ・全文は07 §2 v0.6):

```text
   - バー・酒場など飲酒の場を表す語があるときは、本人がアルコールを飲まない
     つもりでも(「コーラにする」等)true とする。場所の語を飲み物の意向より
     優先する(08 D-10: 飲酒を伴う場に身を置く機会として扱うため)。
   - 「ノンアルコールビール」のようにアルコールでないことが明示された飲み物は
     true の根拠にしない。
```

## 2. ピン試験の更新

`backend/tests/unit/intents/test_prompt.py` は **docs本文を期待値として動的に読む方式**
(`PARSER_SYSTEM_PROMPT == 07の最初の```textブロック`)であり、ハードコードされた全文ピンは
存在しない。よって試験コードの変更は不要で、上記2ファイルの同一文面適用によりピンは
改訂後の全文へ自動追従する。追従は `make test` の
`test_prompt_is_pinned_to_docs_07_section2` 合格(533 passedに含まれる)で検証した。

## 3. 検証(make lint・make test)

- `make lint` → **All checks passed!**(ruff format --check 132 files formatted・ruff check緑)
- `make test` → **533 passed, 94 deselected**(ベース533件と同一件数・ピン試験含む全緑)

## 4. 両ゲート再実行(実API)

- 実行: `cd backend && uv run --env-file ../.env python -m latch.g1gate`(全71件・exit 0)
- 証拠: `docs/testassets/results/g1-result-20260928-210042.yaml`
  (trial_id: g1-20260928-210042・model: claude-haiku-4-5・llm_mode: real)
- プロンプトSHA256: `5e7e91020d7b25497254241d5f311915f21ccc28d7457e2b4c2b4718519ffa5e`
  (改訂前 `80768d52…e895` から変化=改訂全文が実測に使われた証左)
- incomplete=False(全件完走・E-001〜003は422 VALIDATION_ERRORの期待どおり)

### Parser構造化ゲート(32件・5フィールド率) — **passed: true**

| フィールド | 一致率 | 閾値 | 判定 |
|---|---|---|---|
| category | 100.0%(32/32) | 85% | ✅ |
| time_start | 93.75%(30/32) | 90% | ✅ |
| location | 100.0%(32/32) | 90% | ✅ |
| participants | 84.38%(27/32) | 80% | ✅ |
| budget | 100.0%(32/32) | 90% | ✅ |

### alcohol_involvedゲート(36件) — **passed: true**

- **recall 1.0(24/24)・precision 1.0(TP=24・FP=0・FN=0・TN=12・unclassified 0)**
- 前回不合格のFN=A-034は **TPに是正**(expected=true・actual=true・category.primary=drinking)
- 前回(203517)との対比: recall 95.8%→100%・precision 1.0→1.0(維持)

### 総合判定

- **overall_passed: true**(両ゲート合格・exit code 0)

## 5. コミット一覧

```
e491661 docs: G1両ゲート再実行の証拠レポート(Parser合格・alcohol recall/precision 100%で合格・A-034是正)
ee54bb4 fix: 規則7に場所の語の優先とノンアルコール明示を追記(G1 FN=A-034対応・07 v0.6)
(この報告書のコミットが3件目)
```

## 6. 備考

- 規律どおり触ったのは prompt.py・07・ゲートレポート・本報告書のみ(test_prompt.pyは
  変更不要・STATUS.md他docsは無修正。mainには触らずコミットはすべてws-6-g1fix)
- API鍵の実値はコード・レポート・報告書に含めていない(.envはworktreeルート置き・git管理外)
- 合否の最終判断(G1判定)は人間領域 — 本書は実行の完全性と計測値の記録
