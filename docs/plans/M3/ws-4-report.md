# M3 ws-4(チャット+実施自己申告)実行報告

- ブランチ: m3-ws-4 / ベース: c3dbafa(worktree作成時点の分岐元。実行時のmain先頭は0916af6 — 後述)
- 日付: 2026-10-01
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `241 files already formatted / All checks passed!`・`1119 passed, 218 deselected in 7.26s`(期待1119どおり・§9-4整合) |
| 2 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 空 |
| 3 | alembic head=0006 | PASS | `uv run alembic heads` → `0006 (head)` 単一・`git diff --name-only main -- backend/alembic`(merge-base起点・実作業tree) → `backend/alembic/versions/0006_chat_indexes.py` の1行のみ |
| 4 | 変更ファイル=§4の11ファイル | PASS(注記あり) | `git diff --name-only c3dbafa \| sort`(merge-base起点)→ §4の11ファイル(report込み)と完全一致。`git diff --name-only main` には追加で `docs/plans/M3/ws-3-plan.md`・`docs/plans/STATUS.md` の2件が出るが、これは**mainがworktreeベース(c3dbafa)から2コミット進行しているため**(0481f9d ws-3計画書・0916af6 STATUS更新・スーパーバイザー commits。本単位の変更ではない。§7検証手順1のws-3→ws-4マージ順で自然に解消) |
| 5 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic\|time\.sleep\|from time import' backend/src` → `backend/src/latch/core/clock.py:33: return datetime.now(UTC)` の1行のみ・`pytest tests/unit/test_arch_no_direct_time.py -v` → `1 passed` |
| 6 | integration収集17件 | PASS | `uv run pytest tests/integration/test_chat_attendance_api.py --collect-only -q` → `17 tests collected`・exit 0(migrated_db fixtureは未実行) |
| 7 | test-ci | **スーパーバイザー検証待ち** | 実施せず(§0規律)。期待1337 passed(main 1301+unit 19+integration 17)+ws-3分・alembic head=0006・m3ws4-残存ゼロ(§7のSQL) |

## design §5 実装時確認事項の結果
- (なし — design §5の7件は2026-10-01承認済み。本計画§2の固定値から
  変更した場合は下に「変更前→変更後+理由」を記録する)

## 固定値の変更有無(design.md §2・本計画§2)
- CHAT_READONLY=matched単一条件+blocks(§2): 変更なし
- attendance手順1〜7の順序と404/409/503分類(§2): 変更なし
- cursor 2キー(created_at,id)・窓=閉区間3日・body trim後1〜1000字(§2): 変更なし
- その他: 変更なし(最終コードは計画書§8のコードどおり。ruff format適用による折り返し整形のみ)

## (G3・ws-5・ws-3への引継ぎ)
- ws-5: blocks判定は store.select_block_between(conn, me, others)(DB直読み・
  キャッシュなし)。Redisキャッシュ差し替え点として本関数を明け渡す
- G3時確認候補(design §5-1): attendanceの回答単位はLATCH単位先着1名
- docs改版候補(design §5): ②05エラー表のCHAT_READONLY発生箇所列 ③05 §5へ
  messages request形式 ⑥05 §3へIndex 2本

## コミット一覧
```
2634d50 feat: add chat/attendance error classes and request/response schemas (M3 ws-4)
ff73890 feat: add message cursor codec and attendance window predicate (M3 ws-4)
3e304b7 feat: add chat store functions (insert/page/blocks-exists) with SQL pins (M3 ws-4)
61abaf7 feat: add attendance store (conditional update + preselect) and LatchRow.completed_at (M3 ws-4)
cef2bc8 feat: add send_message and list_messages use cases (M3 ws-4)
7fc9959 feat: add submit_attendance use case with first-writer-wins update (M3 ws-4)
15fe081 feat: expose messages and attendance endpoints on latches router (M3 ws-4)
614208b feat: add migration 0006 chat indexes (messages composite + calibration partial unique) (M3 ws-4)
b0a07cc test: add chat+attendance integration suite (17 cases, supervisor-run) (M3 ws-4)
(下記が本報告のコミット)
docs: add M3 ws-4 execution report (M3 ws-4)
```

## 補足(詰まった点・判断した点があれば)
1. **TDD中間コミットを§0「毎コミットグリーン」へ合わせる機械的調整(最終コードは計画書どおり・機能変更なし)**:
   - 計画書Task 3 Step 1の `_NEW_SQL` はattendance系2定数(`_SELECT_CALIBRATION_ATTENDANCE`・`_UPDATE_ATTENDANCE`)を含むが、それらはTask 4で追加されるため、Task 3コミット時点では `test_ws4_sql_bind_params_fully_recognized` が赤になり§0規律(毎コミットグリーン)と両立しない。Task 3コミット時のみ `_NEW_SQL` をチャット系4定数とし、Task 4 Step 1で6定数へ拡張した(最終状態は計画書と完全一致)。
   - 同様に、Task 1時点で未使用となる `from latch.core.clock import FakeClock`(Task 5で使用)・Task 5時点で未使用となる `AttendanceAlreadySubmittedError`/`AttendanceWindowClosedError` のservice.py import(Task 6で使用)は、F401を避けるため該当Taskコミット時点では外し、使用Taskで追加した(最終状態は計画書§8 Task 5/6 Step 3のimportコードどおり)。
2. **make lint の整形差分**: 各タスクの追記後に `ruff format .` を適用した(§0指示どおり)。折り返し位置の変更のみで意味変更なし。
3. **完了条件4の `git diff --name-only main`**: 上表のとおり、worktreeベース(c3dbafa)以降にmain側へ入った `docs/plans/M3/ws-3-plan.md`(新規)・`docs/plans/STATUS.md`(更新)が差分に混入する。agent3はこれらに触れていない(§5禁止どおり)。merge-base起点のdiffでは§4の11ファイルと完全一致。
