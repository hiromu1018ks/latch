# M3 ws-9(G3ハーネス)実行報告

- ブランチ: m3-ws-9 / ベース: cb2eab0
- 日付: 2026-10-02
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `274 files already formatted` / `All checks passed!` / `1215 passed, 260 deselected`(unit 1215不変・期待どおり) |
| 2 | integration収集 | PASS | `--collect-only tests/integration/test_g3_acceptance_e2e.py -q` → `6 tests collected`(test_1〜test_6)・全体 `--collect-only -q` → `1475 tests collected`(=1469+6・期待どおり) |
| 3 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 空 |
| 4 | 製品コード等無変更 | PASS | `git diff main -- backend/src backend/alembic backend/pyproject.toml backend/uv.lock Makefile compose.yaml docker frontend prototype` → 空 |
| 5 | 変更ファイル=§4の4ファイル | PASS | `git diff --name-only main \| sort` → `backend/tests/integration/test_embedding_pipeline.py`・`backend/tests/integration/test_events_pipeline.py`・`backend/tests/integration/test_g3_acceptance_e2e.py`・`docs/plans/M3/ws-9-report.md`(作成2+変更2)・`git status --short` 空 |
| 6 | test-ci未実行 | PASS | test-ci=スーパーバイザー検証待ち(§0規律) |
| 7 | teardown恒久対策反映 | PASS | `rg -n "DELETE FROM users" ...` → test_events_pipeline.py:178・test_embedding_pipeline.py:244(各1ヒット) |

## design §5 承認事項の実装対応
- §5-1(ci環境実施・supervisor前提①): 試験はci構成(実DB・api常設・StubPushSender)で書いた。staging再実行はM4-3
- §5-2(単一ファイル集約): test_g3_acceptance_e2e.pyへ6試験集約
- §5-3(#22の観測レイヤー=API応答+frontend vitest): 試験2はAPI応答のキー集合比較。実機ブラウザE2Eなし
- §5-4(競合クローズ=グループ成立×1対1): 試験4の構成どおり
- §5-5(期限確定=4人組3人YES): 試験3の構成どおり
- §5-6(teardown恒久対策を本単位へ): Task 4で2ファイルへusers削除1行追加
- §5-7(#24〜#26・MLR・非機能はG3資料に含めない): 対応表・報告書に含めていない

## 固定値の変更有無(本計画§9)
- SUBJECT_PREFIX=m3ws9-・期待キー集合(13キー/7キー/2キー): 変更なし
- ヘルパー形式・SQL: 変更なし(§8 Task 1のコードブロックどおり。後述3点のlint起因最小差分のみ)
- その他§9のIF確定事項: 変更なし

lint起因の最小差分(§0「整形差分はruff formatを当てる」手順とruff check対応。挙動への影響なし):
1. ruff format適用 — 試験1の `_structured(start=start, expires=expires, notification_level="proposals_only")` 呼び出しが1行に整形された(折り返し→1行・意味不変)
2. `zip(header_pairs, latch_ids, strict=True)` — B905(zipの明示strict=必須)。両リスト同長(2要素)のため挙動不変
3. GroupEngine importはTask 3コミットで追加 — 計画書Task 1コードブロックに試験6(Task 3)用のimportが先行して含まれTask 1単体ではF401(未使用import)でlint不可のため、Task 1では除外しTask 3(e1cd06f)で追加。最終状態は計画書のコードブロックと同一

## (スーパーバイザー検証時の手順・結果記入欄・design §4.2)
1. `make lint && make test` — unit 1215 passed(不変)
2. `docker compose build api worker` — 運用ルール4(形式どおり)
3. `make test-ci` — **1475 passed**(=1469+6)・purge-match-sub実行をMakefile出力で確認
4. `cd frontend && npm test` — 217 passed(G3条件4の証拠化・再実行)
5. 残存確認: m3ws9-系は従来手順+**m2ws1-%/m2ws2-% users==0件**(<SQL結果。1回目は既存蓄積分の掃除SQLを先行実行>)
   ```sql
   DELETE FROM users WHERE auth_subject LIKE 'm2ws1-%' OR auth_subject LIKE 'm2ws2-%';  -- 既存蓄積分(1回のみ)
   ```
   の後にもう一度test-ciを実行し、`SELECT COUNT(*) FROM users WHERE auth_subject LIKE 'm2ws1-%' OR auth_subject LIKE 'm2ws2-%';` が0件
6. lint再実行(test-ciにlintが含まれないため・ws-4運用メモ)
7. basename一意確認・alembic head=0006不変・時刻参照がclock.pyのみ(arch test)
8. G3判定資料への反映: design §2.1対応表+test-ci/frontendの実行ログをSTATUS「G3判定資料」節へ集約(G2判定資料と同型)

## コミット一覧
```
1c1134a test: delete users in user_env teardown (m2ws1/m2ws2 leak fix)
e1cd06f test: G3 acceptance e2e test 6 (self-duplicate group exclusion)
e1971eb test: G3 acceptance e2e tests 3-5 (deadline expiry, conflict close, defer)
6bbcd9d test: G3 acceptance e2e tests 1-2 (muted answer chain, visibility A/B)
```

## 補足(詰まった点・判断した点があれば)
- 計画書§0の「作成→構文→収集→unit全件」サイクルどおり全Taskを進行。各Taskでpy_compile・collect-only・`make lint && make test`(1215 passed不変)・basename一意(空)を確認してからコミット
- スキップした項目なし(全Step実行)。仕様矛盾は発見せず
- integration試験の実行は§0規律どおり一切行っていない(収集・構文確認のみ)
