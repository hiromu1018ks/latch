# M3 ws-5(ブロック・通報)実行報告

- ブランチ: m3-ws-5 / ベース: 60b2b14
- 日付: 2026-10-01
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `All checks passed!` / `1187 passed, 239 deselected in 7.25s`(期待1187どおり。main 1148+store_sql 8+cache 7+service 16+routes 7+chat_service追記 1) |
| 2 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 空(出力なし) |
| 3 | alembic head=0006不変 | PASS | `uv run alembic heads` → `0006 (head)` 単一・`git diff --name-only main -- backend/alembic` → 空 |
| 4 | 変更ファイル=§4の17ファイル | PASS | `git diff --name-only main \| sort` 16ファイル+本報告書(report)=17。§4の一覧と完全一致(`git status --short` も空) |
| 5 | 実時間参照がclock.pyのみ | PASS | `rg 'datetime\.now\|utcnow\|time\.time\|time\.monotonic\|time\.sleep\|from time import' backend/src` → `core/clock.py:33: return datetime.now(UTC)` の1行のみ。`pytest tests/unit/test_arch_no_direct_time.py` → `1 passed` |
| 6 | integration収集9件 | PASS | `pytest tests/integration/test_safety_api.py --collect-only -q` → `9 tests collected`(test_1〜test_9)。`test_chat_attendance_api.py --collect-only -q` → `17 tests collected`(Task 6の変更が収集を壊していない) |
| 7 | test-ci | **スーパーバイザー検証待ち** | 実施せず(§0規律)。期待 **1426 passed + 並走単位分**(main 1378+unit 39+integration 9)・alembic head=0006・m3ws5-残存ゼロ・Redis `blk:u:` 残存ゼロ(§7のSQL・scan) |

## design §5 実装時確認事項の結果
- (なし — design §5の5件は2026-10-01承認済み。本計画§2の固定値から
  変更した場合は下に「変更前→変更後+理由」を記録する)

## 固定値の変更有無(design.md §2・本計画§2)
- design §4.1のunit試験ファイル名 → test_safety_接頭辞へ変更(basename衝突・§9-1): 変更あり(計画時点で確定済み)
- キャッシュ blk:u: JSON配列・TTL 3600・コミット後DEL(§2): 変更なし
- D-23対象3状態(candidate含む)・matched除外・通知なし(§2): 変更なし
- POST手順1〜7・冪等201・DELETE 404/204・reports 4値reason・pending固定(§2): 変更なし
- その他: 変更あり — 以下はいずれもコード本文の意味・挙動は不変の機械的対応:
  1. `cache.py` の `zip(user_ids, raw)` → `zip(user_ids, raw, strict=True)`: ruff B905対応。Redis MGETはキー数と同長のリストを返すため strict=True は実挙動に影響しない
  2. `test_safety_api.py` の内包変数名 `l` → `lid`(2箇所): ruff E741(曖昧な変数名)対応
  3. 計画書Task 6 Step 2の期待記載「既存13件はPASS」: main時点の `test_chat_service.py` は12件だった(grep計測)。追記1件で13件・最終期待総数1187は計画書どおりで影響なし

## (スーパーバイザー・G3・ws-6/ws-7/ws-8への引継ぎ)
- G3時確認候補(design §5): ①Layer 1のblocks参照はSQL直読きのまま(08 §5.1との差分)
  ②candidate含み(05 §6遷移表にcandidate→cancelled行なし) ③解除後は現物参照で
  送信可 ④1対1ブロックで失効リスト不使用(08 §5.3との読み方)
- フロント共有値(design §5-5): blocks一覧はdisplay_nameつき・reports.reasonは
  4値の英語コード(inappropriate_content/unpleasant_behavior/suspected_impersonation/other)・status値域はpending→reviewed→resolved
- ws-6: 退会時のblocks/reports行の扱い(削除範囲)は本単位では扱っていない
- test_chat_attendance_api.py の _block は blk:u: 手動DELつき(§4の最小変更)

## コミット一覧
```
3e10c80 test: add safety integration suite (9 cases, supervisor-run) (M3 ws-5)
40ea9b8 feat: route send_message blocks check through BlockCache (M3 ws-5 design 2.2)
187d4df feat: expose safety router (block/report endpoints) and wire into app (M3 ws-5)
c7b4b2b feat: add report use case with 4-value reason codes and pending status (M3 ws-5)
4116bb6 feat: add block/unblock/list use cases with D-23 cancel and cursor codec (M3 ws-5)
1eaaf89 feat: add BlockCache read-through redis cache with db fallback (M3 ws-5)
6be7c2a feat: add safety domain errors and blocks/reports store with SQL pins (M3 ws-5)
```

## 補足(詰まった点・判断した点があれば)
- **TDDは全タスクで実施**: 各Task Step 1のテストを先に書き、RED(import error /
  AttributeError / TypeError / Counter不一致)を確認してから最小実装へ進み、GREEN
  を確認後にコミットした
- **lint整形差分**: 計画書記載のコードは行長88のruff formatで数箇所整形差分が
  出たため、毎コミットの検査(§0)で `ruff format .` を当ててからコミットした
  (Task 3・Task 7はamendで同一コミットへ整形分を取り込み)
- **実装上の判断は一切なし**: SQL・手順・エラー・キャッシュ・イベント・応答
  形状はすべて計画書§2/§8の実コードをそのまま写した。変更は上記「その他」の
  機械的lint対応3件のみ
- Task 6の `test_chat_attendance_api.py` への `_block` 変更(blk:u: 手動DEL)は
  計画§4・§9-2記載のスコープ外最小変更であり、収集17件は不変を確認
