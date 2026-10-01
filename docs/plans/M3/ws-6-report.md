# M3 ws-6(削除・退会)実行報告

- ブランチ: m3-ws-6 / ベース: c3541d3(worktree分岐点=af67547系・main側の後追いSTATUSコミットを含む最新main)
- 日付: 2026-10-01
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | lint exit 0(273 files formatted・All checks passed)/ `1207 passed, 253 deselected in 7.13s`(期待1207どおり) |
| 2 | テストbasename一意 | PASS | `find tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → **空** |
| 3 | alembic head=0006不変 | PASS | `uv run alembic heads` → `0006 (head)`(単一)/ `git diff --name-only main -- backend/alembic` → 空(出力なし) |
| 4 | 変更ファイル=§4の21ファイル(+本報告書) | PASS | `git diff --name-only main...HEAD`(merge-base起点・worktree分の変更) → 21ファイル=§4一覧どおり(reportコミット後22)。`git status --short` → 空。※ `git diff --name-only main`(2点)には `docs/plans/STATUS.md` が混入するが、これは**main側の後追いコミットc3541d3(スーパーバイザー記録)がworktreeに無いだけ**でworktree側は STATUS.md に一切触れていない(§5禁止遵守・merge-base起点diffでは出現しない) |
| 5 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic\|time\.sleep\|from time import' backend/src` → `core/clock.py:33: return datetime.now(UTC)` の1行のみ/ `pytest tests/unit/test_arch_no_direct_time.py` → 1 passed |
| 6 | integration収集15件+groupengine件数不変 | **PASS(14件・計画から-1)** | 3ファイル収集 → `14 tests collected`(計画15件のうち test_account_api.py test_2をスキップしたため。下記「スキップ記録」参照)/ groupengine → `12 tests collected`(変更前12件と同一・件数不変) |
| 7 | test-ci | **スーパーバイザー検証待ち** | 実施せず(§0規律)。期待 **1460 passed**(main 1427 + unit 19 + integration 14)・alembic head=0006・残存ゼロ(下記SQL) |

### スキップ記録(§0規律「計画書にない判断は勝手に決めずスキップして報告」)

**test_account_api.py test_2(latches詳細participantsの「退会したユーザー」表示)をスキップした理由 — 仕様矛盾の発見**:

計画書§8 Task 6 Step 2のtest_2は「退会後、`GET /v1/latches/{lid}` のparticipantsに『退会したユーザー』が表示される」ことを期待するが、現行契約(design §2.1・§2.8・不変ファイルlatches/)と両立しない:

1. 退会cascadeは参加Intentを**物理削除**し、当該LATCHを**cancelled化**する(FR-19・design §2.1・本計画§2)
2. `latches/service.py` の詳細取得は `status not in ("matched", "completed")` のとき **participants=None** で返す(design §2.8・`latches/schemas.py:60`「participants等はmatched/completedのみ非null」・latches/は§5禁止で無変更)
3. 仮にparticipants解決が走っても `_SELECT_PARTICIPANTS`(`latches/store.py:255`)は **intents起点のJOIN**(`FROM intents i JOIN users u ON u.id = i.user_id`)であり、退会cascadeでintents行が消えた時点で当該ユーザーのエントリは現れない

つまり「cancelled LATCHのparticipantsに退会者表示」は物理削除設計と構造的に矛盾する。design §2.7「表示名置換はusers行のUPDATEで達成」は正しく、その自動反映の実対象は**users.display_nameを直接参照する残存物**(blocks管理画面〔引継ぎ欄どおりws-8〕・messages履歴等)であり、cancelled LATCHのparticipantsはFR-19のとおり「この提案は成立しませんでした」表示(ws-7フロント)に置き換わる経路である。**latches/は§5禁止のため本単位では修正不能**であり、design.mdが本計画より優先(§1)する規定に照らしてもtest_2の期待はdesign §2.8と冲突するため、test_2のみ作成を見送った(test_1・3〜6の5件は作成・収集確認済み)。

**影響**: 収集件数15→14・test-ci期待件数1461→1460。docs改版候補①(05 §6)への追記時に「cancelled LATCH詳細のparticipantsはnull(退会者表示は不成立文言で代替)」を明記することを提案する。

## design §5 実装時確認事項の結果
- (design §5の7件はG3時確認・docs改版候補として登録済み。下の変更有無へ実差分を記録)

## 固定値の変更有無(design.md §2・本計画§2)
- insert_match_eventへON CONFLICT DO NOTHING追加(design §2.2の前提適合・§9-6): 変更あり(計画時点で確定済みどおり適用)
- 退会2回目呼び出しの応答は404 NOT_FOUND(未登録JWTと同型・design §2.3の冪等記述は削除操作の冪等性): 変更なし(§9-6の適合措置どおり・test_account_api test_5で404を検証)
- カスケードSQL・順序・退会手順・RetentionJob構成(§2): 変更なし
- その他: 変更あり 3件(いずれも機械的調整・仕様の変更なし)
  1. **Task 1**: 計画書§8 Task 1 Step 1のtest_deletion.pyスタブ応答に括弧のタイポ(`FakeResult(((LID, (IID, OTHER)),), 0)` は1列1行になり2値unpackでValueError)。既存test_worker_stage1.py流儀の `FakeResult((LID, (IID, OTHER)), 0)`(row=1行2列)へ修正した。検証意図は不変
  2. **Task 1**: test_worker_stage1.py 末尾2試験(`test_close_latches_on_delete_*`)が `stage1_mod.close_latches_on_delete` を直接参照しており、§8 Task 1 Step 6の「close_latches_on_deleteをstage1.pyから削除(移設)」と衝突する(計画書はこの2試験に未言及)。deletion.pyへの移設に伴う機械的参照替え(`deletion_mod.close_latches_on_delete`)を行った。件数不変・試験内容は無傷。あわせて未使用になった `from latch.worker import stage1 as stage1_mod` importを削除
  3. **Task 6**: integration試験のSQL文字列に行長88超過(E501)があったため分割(report/retention/deletionの各テスト内。SQL意味は不変)。また test_deletion_api test_1 の `_calibration` ヘルパーは answered_at に要素ごとの1分差を付けて匿名化後の**配列順保存検証を可能**にした(計画書実装注記「順序(最初の要素がu1分=仕込み順)を検証する形へ実装すること」の実現手段・仕込みの全要素同時刻では順序検証が不可能なため)

## (スーパーバイザー・G3・ws-8/ws-9への引継ぎ)
- docs改版候補(design §5): ①05 §6への削除受理status明記 ②05 §5へのDELETE /v1/users/me規定追加
  (404 NOT_FOUND〔未登録JWT〕も明記) ③users残存列(birth_date)のオーナー確認
  ④auth_subject='deleted:'暗黙契約 ⑤単発削除の完了がEvent処理依存(通常数秒)
  ⑥match_events恒久蓄積 ⑦updated_at部分Index
- **新規**(test_2スキップに由来): ⑧cancelled LATCH詳細APIのparticipantsはnull
  (design §2.8)。「退会したユーザー」表示はusers.display_name参照箇所(blocks管理画面等)に
  自動反映され、cancelled LATCHは「この提案は成立しませんでした」表示(ws-7フロント)へ
  置き換わる。05 §6追記時に明記を推奨
- ws-8: 不成立表示「この提案は成立しませんでした」・ブロック管理画面の退会者表示はusers行置換で自動参照
- test-ci検証時の残存確認SQL(§9-3): SELECT count(*) FROM users WHERE auth_subject LIKE 'm3ws6-%' OR auth_subject LIKE 'deleted:%'
  → 期待0(teardownが退会済み行もid追跡で掃除)
- test-ci期待値の内訳補足: unit 1207(main 1188+19)/ integration +14(deletion 7+account 5+retention 2・groupengine不変)
- 作業コミット(6件): c176fdd(Task1)→619015a(Task2)→00344db(Task3)→12b4dca(Task4)→d973ba9(Task5)→d1ad1e9(Task6)
- 計画書Task 3〜4の中間期待件数(1200/1201)は±1の記載ズレがあったが、§6最終期待1207(1188+19)と§9-4内訳(+19)は実測1207と整合(実測推移: 1188→1194→1196→1201→1202→1207)
