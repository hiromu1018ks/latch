# M2 ws-2(Embedding Worker) 実行報告

- ブランチ: m2-ws-2 / ベース: 3a3950e
- 日付: 2026-09-29
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `All checks passed!` / `618 passed, 109 deselected in 4.99s`(deselected=integration 102+本単位7) |
| 2 | integration 7試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | `uv run pytest --collect-only tests/integration/test_embedding_pipeline.py -q` → `7 tests collected in 0.01s` |
| 3 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic\|time\.sleep\|from time import' backend/src` → `core/clock.py:33` の1行のみ。`test_arch_no_direct_time.py` → `1 passed` |
| 4 | alembic・docs無変更 | PASS | `git diff --stat main -- backend/alembic docs` → **backend/alembic・docs/01〜12仕様書・docs/learn等は無差分**。差分は§4スコープの報告ファイル `docs/plans/M2/ws-2-report.md`(本ファイル・新規作成)のみ |
| 5 | 触るファイルがスコープどおり | PASS(差異1件・許可済み) | `git diff --name-only main \| sort` は§4の一覧(21ファイル)+ `backend/tests/unit/llm/test_llm_factory.py`(supervisor許可による機械的追従 — 補足欄参照)。`git status --short` は空 |
| 6 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 出力なし(空) |
| 7 | embed-smokeターゲット・起動ガード | 記載確認/exit=1 | `make -n embed-smoke` → `cd backend && uv run --env-file ../.env python -m latch.llm.embed_smoke`。envなし実行 → `[embed-smoke] FAIL: LATCH_LLM_MODE=real が必要(.env・make embed-smoke)` / `exit=1`(実API不呼び出し)。実API実行は embed-smoke=スーパーバイザー検証待ち |

## design §5 実装時確認事項の結果
1. google-genaiの実機挙動(HttpRetryOptions(attempts=1)の実効性・embed_content応答形式・タイムアウト例外型): **SDK実バージョン=2.25.0**(design §5-1想定の下限`>=1.33`を満たすためpyprojectの下限は変更せず)。unit試験の契約ピン(`HttpOptions(timeout=, retry_options=HttpRetryOptions(attempts=1))`・`EmbedContentConfig(output_dimensionality=768)`・応答`response.embeddings[0].values`)はSDK 2.25.0の実型定義でそのまま有効なことをスタブなし実型で確認(test_llm_gemini.py 7件)。実機実効性(401・タイムアウト例外型・レイテンシ)は **make embed-smoke=スーパーバイザー検証待ち**
2. バックフィル初期値(周期300秒・バッチ50): `settings.py` 実装値 `embedding_backfill_interval_sec: int = 300` / `embedding_backfill_batch_limit: int = 50`
3. test_events_pipeline.pyの期待値追従範囲: **追従不要・無変更**。分析根拠 — (a) 全8試験のassertは `_wait_status`/`_fetch_event` のevent_type絞り込み(created/updated/deleted)でありembedding_completed行の追加では壊れない。(b) test_3の `count(*)==0`(draft作成直後)はdraftがEvent発行しないためkickも走らず成立したまま。(c) test_5/6/7のghost intentはhandleフェーズ1で行なしno-op(embedding_completed行を作らない)。test_7の行数assertは `event_type='created'` 絞り込み。(d) test_4はmatch_candidatesのカウント(無関係)。(e) teardown(user_env)は `source_intent_id IN (SELECT id FROM intents WHERE user_id=...)` でembedding_completed行も削除される。(f) Worker未注入時のbackfillタスクはsleep-first 300秒のため試験中にrun_onceしない・shutdown時の `await backfill_task` はstop伝播で即終了
4. docs改版候補(§2.3 内容更新時embeddingクリア): **申告** — 05 §5(または06 §9)へ「内容更新(PATCH全置換)時、intents.embedding/embedding_modelはNULLクリアされ、更新Eventのキックで再エンベディングされる。resume(pause/resume/delete)ではクリアされない」の一言追記を提案(store._UPDATE実装と仕様の整合を明文化・判断はsupervisor)

## 固定値の変更有無(design.md §2・本計画§8)
- 実行配置=Worker配線案B(design §2.1): 変更なし
- 2フェーズ+ガード付きUPDATE(design §2.2): 変更なし
- _UPDATEでのembeddingクリア(design §2.3): 変更なし
- バックフィル=Worker内周期タスク(design §2.5): 変更なし
- google-genai・明示api_key・retry無効化(design §2.6): 変更なし
- build_embedding_gateway新設(design §2.8): 変更なし
- 本計画§8のIF確定事項(EmbeddingWorker/BackfillRunnerのAPI・SQL・正規化テキスト導出規則・設定既定値・Makefile形式): 変更なし

## スーパーバイザー検証手順(test-ci・embed-smoke実行時)
1. `docker compose build api worker` — **api・workerイメージの再ビルドが必須**(make test-ci の compose up は再ビルドしないため・STATUS運用ルール4)
2. `make test-ci` — test_embedding_pipeline.py(#1〜#7)を含む全体グリーン。**test_events_pipeline.py が赤化した場合は期待値の値のみを最小修正する**(試験意図を変えない — design §3.4-3。修正できない赤化はagent3へ差し戻し)
3. `make embed-smoke` — 実APIスモーク1呼び出し(dim=768・レイテンシ・exit 0)。契約ズレ(HttpRetryOptionsの引数名・応答形式等)が検出された場合はその内容を記録
4. 常設worker復帰の確認: `docker compose ps` で worker が running に戻っていること(llm_mode=stubでbuild_embedding_gatewayがStubLLM構築となり起動する)

## コミット一覧
```
7ef5a66 test: EmbeddingパイプラインE2E 7試験(作成のみ・実行はスーパーバイザー)
6b6da00 feat: Embedding実APIスモークharness(make embed-smoke)
5c569f9 feat: WorkerへEmbeddingキックとバックフィル起動を配線(ack前にキック)
018845e feat: BackfillRunner(embedding NULL・active対象の周期再エンベディング)
6ecf0b7 feat: EmbeddingWorker(2フェーズ・ガード付きUPDATE・embedding_completed発行)
8bd8586 feat: build_embedding_gateway(Embedding系統のみreal化・鍵fail-fast)
be0c117 style: gemini.pyのdocstring行長をE501へ適合(88文字内)
95f6b4e feat: GeminiEmbeddingProvider(明示api_key・retry無効化・768検証)
9796c73 feat: 内容更新時のembeddingクリア(store._UPDATE 2行挿入)
efab855 feat: 正規化テキスト導出の純関数(07 §3形式・raw_text非経路)
c70d60f feat: google-genai依存とEmbedding Worker設定を追加(M2 ws-2)
```

## 補足(詰まった点・判断した点があれば)
1. **test_llm_factory.pyの期待値追従(supervisor許可)**: Task 1(Settingsへ`llm_gemini_api_key`追加)の適用で既存試験 `test_llm_settings_are_exactly_six_fields`(ws-6資産・llm_*設定6項目ピン)が赤化し、§5禁止(その他既存テストファイル)と完了条件1が両立しない仕様矛盾としてBLOCKED報告した。**supervisor裁定により機械的追従を許可された**ため、期待値セットへ `llm_gemini_api_key` を1項目追加(6→7項目)し関数名・コメントを実態へ合わせた(`test_llm_settings_are_exactly_seven_fields`・M1 ws-5の契約カウンタ試験と同じ前例扱い)。試験意図(timeout/failフラグのenv経路を作らない構成ピン)は不変。同ファイルの当該期待値に限る許可であり、他の既存試験ファイルは無変更
2. **計画書テストコードの構文修正(Task 4)**: 計画書§8 Task 4 Step 1の `await` を使う3試験(test_embed_calls_with_model_contents_and_768・test_dimension_mismatch_raises_provider_error・test_sdk_exception_passes_through)が `def` のまま記載されSyntaxErrorになるため、`async def` へ修正(asyncio_mode=autoで実行)。試験内容は計画書どおり
3. **Task 6の件数表記**: 計画書Task 6 Step 5のExpected「PASS(11件)」は計画書自身の列挙が10関数のため実際は10件(全列挙を実装・網羅)。Task 8追記後のtest_worker_embedding.pyは16件
4. **ruff(format・E501)への機械適合**: 計画書記載のコード全文のうち数箇所が `ruff format` の折り返し規則・E501(88文字)に抵触したため、整形とdocstring/コメントの行長短縮のみを実施(ロジック・期待値は無変更。gemini.py冒頭docstring・embed_smoke.pyのFAILメッセージ文言・試験docstring等)
5. **google-genai解決バージョン**: 2.25.0(design §5-1想定`>=1.33`に対し上位互換で解決。pyproject下限は計画書どおり`>=1.33`のまま)
