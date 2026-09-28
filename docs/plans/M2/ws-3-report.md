# M2 ws-3(Layer 1 Hard Filter + Layer 2 Candidate Retrieval)実行報告

- ブランチ: m2-ws-3 / ベース: 3a3950e
- 日付: 2026-09-29
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `make lint` → `All checks passed!`(exit 0)。`make test` → `600 passed, 119 deselected in 4.18s`(exit 0) |
| 2 | integration 17試験の収集 | 収集確認済み/**test-ci=スーパーバイザー検証待ち** | `uv run pytest --collect-only tests/integration/test_matching_hardfilter.py tests/integration/test_matching_retrieval.py -q` → `17 tests collected`(13+4・exit 0)。実行は§0どおり実施していない |
| 3 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic\|time\.sleep\|from time import' backend/src` → `backend/src/latch/core/clock.py:33: return datetime.now(UTC)` の1行のみ。`uv run pytest tests/unit/test_arch_no_direct_time.py -v` → `1 passed` |
| 4 | alembic・docs無変更 | PASS | `git diff --stat main -- backend/alembic docs` → 出力なし(空。reportコミット前に実施) |
| 5 | 変更ファイル=新規12ファイルのみ | PASS | `git diff --name-only main \| sort` → §4の一覧(12ファイル・後述)と完全一致。`git status --short` → 空 |
| 6 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 出力なし(空) |
| 7 | 既存unit試験グリーン維持 | PASS | `make test` 600 passed(main 575相当+本単位unit 25件=600・既存分の減少なし。119 deselected=integration 115+本単位17相当のdeselect) |

## design §5 実装時確認事項の結果
1. (先送り記録)Layer 2 Index最適化: 本単位は逐次ソートのまま(変更なし)。再設計は10 §4.2負荷試験で p95 1秒を割った場合のみ
2. asyncpg の vector 列読み取り: **test-ci=スーパーバイザー検証待ち** — `test_1_semantic_pair_generated` 冒頭に `assert isinstance(loaded.origin.embedding, str)` を実装済み(文字列でなければ本assertがFAILで検出される。文字列でなかった場合も `_embedding_text` の防御組立が効く実装)。`make test-ci` 実行時に結果を本欄へ追記のこと
3. EXTRACT(YEAR FROM AGE(...)) の挙動ピン: **test-ci=スーパーバイザー検証待ち** — `test_8_age_boundary_jst` が誕生日当日=20歳通過・前日=19歳failをピン(想定どおりなら追加対応不要)。`make test-ci` 実行時に結果を本欄へ追記のこと

## 固定値の変更有無(design.md §2・本計画§9)
- 単一SQL(案A・design §2.1): 変更なし
- 逐次ソート決定性優先(design §2.2): 変更なし
- UPSERT DO UPDATE=retrieval_scoreのみ(design §2.3): 変更なし
- 飲酒年齢のJST暦日基準(本計画§9・design §2.6表からの一変更点): 変更なし
- 本計画§9のIF確定事項(関数シグネチャ・LAYER1_WHERE・K_VECTORS等): 変更なし

### 変更あり(1件・supervisor許可)
- **テストファイル名**: `backend/tests/unit/matching/test_runner.py` → **`backend/tests/unit/matching/test_matching_runner.py`**
  - **supervisor許可によるリネーム(理由: basename衝突・運用ルール5)**。計画書§4作成一覧の `test_runner.py` が既存 `tests/unit/g1gate/test_runner.py` とbasename衝突し `make test` が収集エラー(import file mismatch)となる計画書内部の自己矛盾(§4 vs §0運用ルール5/§6完了条件6)を発見、BLOCKED報告のうえ裁定を得てリネーム。**試験の意図・内容は不変**(§8 Task 4 Step 1掲載コードそのまま・参照はすべて新名で統一・importはファイル内にパス依存なし)

### 機械整形のみ(動作・SQL意味不変)
- `layer1.py` blocks句のE501折り返し(行長89/90>88)、`candidates.py` return文の1行化、`__init__.py` import順のisort、`test_matching_hardfilter.py` のformat適用 — いずれも計画書掲載コードがruffを行長88/規約で通らない箇所への機械的整形
- `test_matching_hardfilter.py` test_13の `own2`/`t_cat`/`t_499` のF841(未使用変数)解消のため代入を `await _intent(...)` 直接呼び出しへ変更(assertは元から変数非参照・行の作成という試験意図不変)

## スーパーバイザー検証手順(test-ci実行時)
1. `docker compose build api` — apiイメージ再ビルド(monitoring対象コードは本単位にないが ws-2 と同時検証になる可能性があるため STATUS運用ルール4どおり。マイグレーション追加なしのため `make migrate` は不要)
2. `make test-ci` — 既存全数+test_matching_hardfilter 13件+test_matching_retrieval 4件がグリーンで完了条件2を検証(本単位はDBスキーマを変えないが、ws-2と検証タイミングを重ねない — STATUS運用ルール1)
3. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空(運用ルール5)

## コミット一覧
```
7d33acc docs: M2 ws-3の実行報告(完了条件7項目の証拠・test-ciは検証待ち)
adcaaa3 test: 02#10・K_v決定性・冪等の4試験(作成のみ・実行はスーパーバイザー)
e2b0915 test: 02#9 Hard Filter単体試験13件(作成のみ・実行はスーパーバイザー)
4e1b009 feat: run_candidate_retrieval(起点検証→Layer2→UPSERTのオーケストレータ)
022286e feat: match_candidatesへのUPSERT(正規化a<b・retrieval_scoreのみ更新)
c28a43d feat: Layer 1共通WHERE確定文字列とLayer 2単一SQL(距離ASC/idASC LIMIT 50)
c907a78 feat: matching起点読み込み(no-op判定・補完・bind params)
```

## 補足(詰まった点・判断した点)
1. **supervisor許可リネーム**(上記「変更あり」) — Task 4で発見・BLOCKED報告→再開指示により解決。Task 1〜3はブロック前にコミット済みで、Task 4分は未コミット残置からそのまま活かした
2. **unit試験件数の計画書表記ズレ**: 計画書Task 1 Step 4 Expected「12件」/Task 3 Step 4「test_origin 13件」は掲載テスト関数の実数(11件/12件)とズレる。テストコードは計画書どおり(実数11+追記1=12で整合)
3. **`make test` 全件数の内訳**: 600 = main 575 + 本単位unit 25(test_origin 12・test_layer_sql 10・test_matching_runner 3)
4. integration 2ファイルの実行は§0(docker系禁止)どおり行っていない。`make test` が収集(import)まで行うため構文・importの正当性はunit実行で検証済み(収集17件・exit 0)
5. 計画書Task 5/Task 6のコミット前 `git status --short` 確認・§0の每コミット検査(lint+testグリーン)を全コミットで実施。Task 2で一時的にlint失敗のままコミットしたがE501整形をamendで解消済み(履歴上は単一コミット)
