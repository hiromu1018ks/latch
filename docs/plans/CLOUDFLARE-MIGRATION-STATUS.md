# Cloudflare移行 実施進行記録(2026-10-08〜)

計画: docs/superpowers/plans/2026-10-08-cloudflare-migration.md
本書は実装セッション横断の作業状態記録。最終報告書の材料とする。

## 完了タスク

### T0: 比較基準と契約の固定 ✅ (commit dd2a59b)
- backend/tests/contracts/: ContractCase 119ケース(データ駆動・全API領域)
  参照実装(compose api)で全合格。runner+reference_harness(actor/seed/cleanup)
- BaselineManifest(prompt/model/limits/schema/fixture hash) drift検出試験
- B0呼出回数基準: parse並行20=LLM20回(集約なし)等を fixtures/b0_baseline_counts.yaml
- 将来回帰ケース4分類定義(隔離引数逆転/再送/公平性/Redis成功後rollback)
- **発見**: 実行環境に LATCH_LLM_MODE=real が漏れており、test-ci実行時は
  `LATCH_LLM_MODE=stub` を明示必須(初回51失敗の原因)。massload4試験は
  load DB(compose.load.yaml db・port 25432)起動が前提(G4確認事項14)。
- frontend vitest 233 passed

### T1: Python Workers互換性probe ✅ (commit 2a2e228)
- cloudflare/api: pywrangler(workers-py 1.17.7)+Pyodide 3.14.2でFastAPI動作
- **重大発見**: cryptography/pyjwt/anthropic/google-genai/typesafe-sdk
  すべてimport可・pyjwt+cryptographyのRS256検証が従来経路のまま動作
  (2026-10-06調査書の「cryptography wheel不在」は新toolchainで解消)
- D1 batch原子性・0行UPDATE・Queues送受+ack・Cron scheduled・並行state分離
  (20並行)・メモリ32MB・既存ParserOutput検証意味同一・JWKS async fetch(self-
  fetch mock)をlocal実測
- RuntimeCompatibilityReport: docs/testassets/results/cloudflare-runtime-probe-*.yaml
- make cf-dev / cf-probe / cf-unit
- ツール環境メモ: UV_CACHE_DIR/UV_PYTHON_INSTALL_DIR/XDG_CONFIG_HOME/
  npm_config_cache をworkspace下へ(読み取り専用home対策)
- 実配備(実Workers実測)は別承認後

### T2: domain/ports分離 ✅ (commit ede54e2)
- domain/time.py(JST・jst_day/month_start・micros変換・age_years)
- domain/types.py(IntentSnapshot/CommandResult/EvaluationKey/ClaimResult/
  ScheduleRequest)
- ports/: atomic(apply_*4種)・evaluations・retrieval・schedule
- 依存検査: tests/unit/test_domain_imports.py(AST準静的+実import)で
  SQLAlchemy/asyncpg/Redis/PubSub/ネイティブ地理への到達を強制遮断
- compute_new_status → domain/latch_status.py(latches.serviceは再export)

### T3: D1 schema + 原子command ✅ (commit 4364310)
- cloudflare/migrations/0001_core.sql: 既存表のD1変換 + commands/journal/
  deletion_tombstones/intent_match_ownership/refresh_tokens/session_families/
  jwt_revocations/usage_counters/outbox/jobs最小
- adapters/d1/: database(D1 API互換・batch=単一transaction)・atomic
  (apply_response/intent/safety/proposal: marker従属方式)・latches/intents/
  safety読取・sessions(refresh回転1行条件付きUPDATE・再利用検出family失効)
- 19試験: 部分保存なし3種・20並行6種(最終YES/YES対NO/期限切れ/共有Intent
  同時成立/通知枠/refresh回転)・session/safety一式
- 契約スイート併走: 140 passed(test_d1_commands + contracts)

### T4: 正確な候補検索 ✅ (commits dd3fb1a, ad3553b, f578238)
- domain/geo_distance.py: WGS84測地線距離(Vincenty・PostGIS geography互換)
- adapters/d1/retrieval.py: hard filter(SQLite)→測地線正確判定→cosine全件
  →snapshot固定(K_v=50は全条件の後)・pass_count=確定通過数
- retrieval_snapshots表・peer削除でsnapshot無効化(atomic側)
- 決定的境界9試験 + **PostgreSQL実測(layer2+pgvector+PostGIS)との一致**:
  同一fixtureで候補ID・版・順位完全一致・similarity 1e-6以内・pass_count一致
  (共有DB汚染回避のため遠未来時間窓+teardownで分離)

### T5: Queues/outbox/処理権/全LLM試行記録 ✅ (commits bd8753e〜344bafb)
- 0002_jobs.sql: jobs完全版(lease/fencing/max_attempts) +
  evaluation_claims + llm_attempts(usage不明はcost NULL)
- adapters/d1/jobs.py: claim_due条件付きUPDATE仲裁・lease回収(上限failed)・
  outbox event_id重複検出・D1UsageGuard(usage_counters原子予約)
- adapters/d1/evaluations.py + attempts.py: claim/complete(fencing破棄)・
  試行記録(prepare→finish・二重finish不採用・SDK内部再試行回数)
- runtime/cloudflare/jobs.py: EvaluationJobRunner
  (claim→prepare→sender→finish→complete・timeout=unknown+job記録)
- 13試験: 同キー20並行=外部呼出1回・別キー全実行・lease奪取fencing・
  送信境界試行残存(usage不明0円なし)・fallback実モデル記録・再配送・
  回収・20並行×上限5→ちょうど5・peer新版は別仕事・系統別費用集計
- 関連既存契約: tests/unit/llm + cost 197 passed・
  integration test_matching_jev 全pass

## テスト現況

- unit全套: 1478+34 passed(make test相当 + cloudflare unit)
- cloudflare suite: 47 passed(d1_commands 19 + retrieval 10 +
  runtime_probe 5 + job_recovery 13)
- contracts: 119+2 passed(参照実装・compose api)
- test-ci全面緑は「最終確認」で再取得(実行時 LATCH_LLM_MODE=stub +
  load DB起動 + worker停止を忘れずに)

## 次のタスク(T6から)

- T6: domain/evaluation_key.py + adapters/d1/evaluation_cache.py +
  test_evaluation_cache.py(I2のhit/miss/削除/calibration・A/B方向)
- T7: domain/parse_key.py + test_parse_reuse.py(I4・フロントの
  lastText解析基準日含む — frontend/src/intent/state.js修正候補)
- T8: 0003_schedule.sql + adapters/d1/schedule.py +
  runtime/cloudflare/scheduled.py + test_schedule_recovery.py(I3)
- T9: Vectorize比較(近似を許さない初期判定 → 不採用見込みなら根拠記録)
- T10: notification_jobs・retention・observability・restore
- T11: migration_eval/compare.py・costs.py + test_end_to_end.py
- T12: migration/export.py・transform.py・verify.py・replay.py +
  test_cutover_rehearsal.py(seedでの変換・復元・切替・巻き戻し)
- 最終報告書: docs/ へ(変更・試験結果・費用見込み・残課題・本番切替手順)

## 環境メモ(次セッション必読)

- compose ci常設: 起動中。test-ci系は `docker compose stop worker` してから
- massloadは `docker compose -f compose.load.yaml up -d db` が前提
- uv: `export UV_CACHE_DIR=/home/misty/Projects/latch/.uv-cache`
- 実測値をCIに残すenv: `LATCH_LLM_MODE=stub LATCH_PUBSUB_EMULATOR_HOST=127.0.0.1:8085`
- wrangler系: `make cf-dev`(pywrangler)→ `make cf-probe`
  (XDG_CONFIG_HOME等はMakefileのCF_ENVが処理)
- 実D1・実Workers配備・実データ移行はユーザー承認待ち(計画§6・目標制約)
