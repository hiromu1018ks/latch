# M2 ws-4(Layer 3 Cheap Judge + コスト保護)実行報告

- ブランチ: m2-ws-4 / ベース: 45e2c85
- 日付: 2026-09-29
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `All checks passed!` / `687 passed, 131 deselected in 5.06s`(ともに exit 0。131 deselected=integration 5件含む収集済み) |
| 2 | integration 5試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | `uv run pytest --collect-only tests/integration/test_matching_cheapjudge.py -q` → `5 tests collected`・exit 0(test_1〜test_5) |
| 3 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic\|time\.sleep\|from time import' backend/src` → ヒット `core/clock.py:33` の1行のみ。`pytest tests/unit/test_arch_no_direct_time.py -v` → `1 passed` |
| 4 | alembic・docs無変更 | PASS | `git diff --stat main -- backend/alembic docs` → 空(report作成・コミット前に実施) |
| 5 | 変更ファイル=§4の24ファイル | PASS(25ファイル=§4の24+整形1・下記補足) | `git diff --name-only main \| sort` → §4の24ファイル(report込み)+`backend/tests/integration/test_matching_hardfilter.py`(main由来のruff format差分解消・意味変化なし)。**補足**: main時点でhardfilter.pyが `ruff format --check` に落ちる状態で、§0「make lintグリーン」と§5「同ファイル不変」が両立しなかったため、§0の指示経路(整形適用)を独立choreコミット(0479b77)として分離した |
| 6 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 空 |
| 7 | 既存unit試験グリーン維持 | PASS | `make test` 687 passed / 131 deselected。期待値変更は§4列挙の機械的追随のみ(test_layer_sql.py: _UPSERT_KEYSへcheap_judge_score追加・`test_upsert_do_update_touches_score_only`→`test_upsert_do_update_touches_scores_only`へ差し替え・Layer3列ピン2件追記 / test_origin.py: _row()へstructured_data=None追加・soft_texts試験2件追記 / test_matching_runner.py: _origin()へsoft_texts追加・test_normal_path_records_pairs差し替え・Layer3試験1件追記 / test_worker_stage1.py: _stage1ヘルパーへmatching_hook引数・matchingフック試験4件追記 / test_worker.py: _run_matching試験2件追記)。列挙以外の既存試験変更なし |

## design §5 実装時確認事項の結果
4. jsonb列の読み取り型: unitの `soft_texts`(str返りはjson.loadsで解釈 — test_layer3 `test_soft_texts_excludes_downgraded` の `soft_texts(json.dumps(sd))` が通過)がstr解釈で動作することを確認済み。実DB(structured_dataがstr/dictどちらで返るか)は integration 試験1のスーパーバイザー実行時に確定する。いずれでも設計不変 — design §5-4
5. integrationのtest_matching_retrieval.py回帰: スーパーバイザー検証時のtest-ci結果で追記。
   runner外部契約不変のため無変更で通る見込み(design §5-5)。影響があった場合は理由を記録
6. (ws-5引継ぎ)Guard呼び出し契約: request_execution(intent_id, user_id) をLayer 4実行直前に
   呼ぶこと・deny時のstatus=skipped遷移とrecord_execution(provider)の実API呼び出しベース計上はws-5
7. (ws-6引継ぎ)reevalガードの再利用: Bucket再評価・catch-upスキャンの入口でも同一部品を使用

## 固定値の変更有無(design.md §2・本計画§9)
- Layer 2と同一トランザクション・同一経路(design §2.1案A): 変更なし
- §2.2の実装定義(時間/予算/語彙の正規化・重み): 変更なし
  (ただしTask 1で計画書掲載テストの期待値誤り1件を修正 — 予算近さの飽和例 `(3000,5000)==0.0` は差2000<3000で飽和せず1/3が定義どおり。`(3000,7000)==0.0`(差4000)へ修正。実装定義・本計画§9-2には変更なし)
- INCR先行・denyも消費(design §2.4): 変更なし
- 日付キー切替方式(design §2.5): 変更なし
- 本計画§9のIF確定事項(シグネチャ・定数・キー形式・TTL): 変更なし
  (計画書コード由来の機械的修正2件: scan_reportのSCANパターンをf-string化(dayが展開されず集計空になるバグ — Task 2)・integrationファイルの未使用import/未使用変数除去(F401/F841)。IF・挙動の仕様は不変)

## スーパーバイザー検証手順(test-ci実行時)
1. `docker compose build api` — apiイメージ再ビルド(STATUS運用ルール4。worker監視対象コードを
   含むため必須。マイグレーション追加なしのため `make migrate` は不要・alembic_version不動)
2. `make test-ci` — 既存全数+test_matching_cheapjudge 5件がグリーンで完了条件2を検証
3. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空(運用ルール5)
4. test-ci実行後、試験が残した Redis キー(prefix掃除)と user データ(teardown)が残っていない
   ことを1回手動確認(design §4.3-4・対抗策の実効性検証・初回のみ):
   redis-cli keys 'ws4-*' が空・ci-db側は subject LIKE 'm2ws4-%' のusers行が0件

## コミット一覧
```
5478618 test: Layer3・reeval・配線の5試験(作成のみ・実行はスーパーバイザー)
6c749a3 feat: workerへreevalガードとmatchingフック実体をDI(redis構築)
e43ba5d feat: stage1のembedding_completed種別へmatchingフックDIを追加
e8bcd45 feat: run_candidate_retrievalへLayer 3組込み(全件記録・topkc選定)
9e5b846 feat: Layer 3計算用データのSELECT列追加とUPSERTのcheap_judge_score拡張
e19bf1c feat: ReevalGuard(30分頻度・SET NX EX 1800・fail-closed)とcost公開API
f885cca feat: JevCostGuard(4カウンタINCR先行・80%跨ぎalert・fail-closed)
6b6d21e feat: JevコストカウンタStore(jev:接頭辞・日付キー切替・reeval NX)
9a2e1f2 feat: Layer 3 cheap_score純関数群(決定的・依存ゼロ)
0479b77 chore: main由来のruff format差分をtest_matching_hardfilter.pyへ解消(意味変化なし)
```

## 補足(詰まった点・判断した点)
1. **hardfilter.pyの整形(完了条件5の25ファイル)**: main時点で
   `tests/integration/test_matching_hardfilter.py`(§5禁止ファイル)が `ruff format --check`
   に落ちる状態だった(ws-3由来とみられる)。§0「make lintグリーン」/§0「整形差分を指摘されたら
   ruff format . を当てる」/§5「同ファイル不変」/完了条件5「24ファイル一致」が両立しないため、
   機能変化ゼロ(引数改行のみ)の整形1件を独立choreコミット(0479b77)として分離し、この報告書で
   記録した。取り込み判断はスーパーバイザー(不要なら当該コミットのリバートでmain側の整形課題として
   先送り可能。その場合make lintは当ファイルで落ちる状態が継続)。
2. **計画書テストコードの期待値誤り(Task 1)**: `test_budget_closeness_boundaries` の
   `budget_closeness(3000, 5000) == 0.0`(コメント「差2000で飽和(3000超)」)は実装定義
   1 − min(|Δ|,3000)/3000 と矛盾(2000<3000のため飽和せず1/3)。spec(design §2.2・本計画§9-2)
   を正としテスト側を `(3000, 7000) == 0.0`(差4000で飽和)へ修正した。
3. **計画書store.pyコードのバグ(Task 2)**: `scan_report` のSCANパターンが f-string
   リテラルでなく `{day}` が展開されず集計が常に空になる状態だった。テスト(spec)が正しいため
   f-string化した(80% alert添付レポートの実効性に関わる)。
4. **計画書integrationコードの機械的修正(Task 9)**: 未使用import(UTC・datetime)と
   未使用変数b(F401/F841)を除去。試験の意図・構造は不変。
5. **試験件数表記のずれ**: Task 6 Step 4の期待「test_origin 15件+test_layer_sql 12件+
   test_layer3 11件+test_matching_runner 4件」に対し実測は「14+12+10+4=40件」。追記試験数は
   計画どおり(test_origin +2・test_matching_runner +1・test_layer3 新規10=Task 1 Step 4の
   「PASS(10件)」とSelf-Review §5と一致)。Task 6 Step 4の件数表記(15・11)は計画書内部の
   数え誤り。
6. **Task 3のamend**: 初回コミット時に `make lint \| tail` のパイプ終了コードでlint失敗を見逃し
   test_cost_guard.pyの整形差分が残ったままコミットした。即座に気付き整形+import順fixをamend
   (f885cca)してlintグリーンを再検証した。以後のlint検証は `set -o pipefail` 付きで実行。
7. **Task 5/6のコミット分割**: 計画書Task 5 Step 5のとおり、Origin必須フィールド追加で
   test_matching_runner.pyがredになる期間があるためTask 6実装後に2コミット(9e5b846・e8bcd45)
   として分割コミットした。

## スーパーバイザー検証による修正(2026-09-29追記)

スーパーバイザー検証の `make test-ci` で test_matching_cheapjudge.py の4件
(test_1・2・3・5)が失敗。実行されたことがない計画書由来の試験設計潜伏欠陥
5系統と判明し、supervisor裁定(方針A〜F)に基づき当該ファイルのみ修正した
(unit試験・他ファイルは不変)。

**根拠(スーパーバイザー確認済み・5系統)**:
1. `intent_input.py:25` CategoryInput.primary は `Literal["meal","drinking","activity"]`。
   `ws4cheap` はpydantic検証で422 VALIDATION_ERROR(全 `_intent` 呼び出しが失敗)していた
2. `layer1.py:40-41` の時間交差は狭義不等号
   (`i.time_start < origin_end AND origin_start < COALESCE(i.time_end, i.time_start+3h)`)。
   end無し同士でΔ180分ちょうどは境界接触となりLayer 1落ちしていた
3. `layer1.py` ペア予算は `LEAST(origin, cand) >= 500`(NULL無視)。
   budget=0 の候補はLayer 1落ちしていた
4. layer3.py の合成式 `0.5*sim + 0.3*((時間近さ+予算近さ)/2) + 0.2*語彙` によると
   test_3の期待値に計算誤り: c_t(Δ180・予算同額・語彙同一)は 0.85(記載0.7は誤り)・
   c_b(Δ0・差3000・語彙同一)も 0.85(記載0.8は誤り)。c_v=0.8・c_mid≈0.7167・
   c_max=1.0・c_min=0.5は正しかった
5. 元の c_b/c_v 同点0.8の前提が誤り。修正後は c_t=c_b=0.85 が同点になる

**修正内容(方針A〜F)**:
- A: CATEGORY を `"meal"` へ。カテゴリ分離の代わりに時間窓分離 — 全テストIntentの
  基準時刻を `BASE_HOURS=120`(now+5日)へ統一。他試験・残存データは+1〜+7時間帯か
  過去のためLayer 1の狭義時間交差で構造的に交差しない。モジュールdocstringと
  CATEGORY/BASE_HOURS コメントを時間窓分離の記述へ更新
- B: `_structured` に `end` パラメータを追加。test_3の起点は明示 `end=start+6h`
  (120h+6h<168h で+7日上限内)を与え、Δ180分候補(far_start)が狭義交差を通るようにした
- C: test_3の予算対照を双方500以上へ(差3000飽和は origin=3500 vs 候補=500)。
  c_min(最低値ピン)も Δ180・差3000・語彙0 で budget=500
- D: 期待値を修正(c_t=0.85・c_b=0.85)。同点順序検証は c_t と c_b の同点0.85ペアで
  UUID昇順をassertする形へ書き換え
- E: test_1・test_2・test_5 は基準時刻の BASE_HOURS 化のみ(test_1の期待値
  `0.5*1.0+0.3*0.75+0.2*(1/3)` は正しい・budget3000はLayer 1問題なし)
- F: unit試験・他ファイルは不変

**修正後の検証(実装側)**: `make lint` exit 0 / `make test` 687 passed・131
deselected / `uv run pytest --collect-only tests/integration/test_matching_cheapjudge.py -q`
→ 5 tests collected・exit 0。
**test-ci=スーパーバイザー検証待ち**(再実行で4件のグリーン化を確認)。
