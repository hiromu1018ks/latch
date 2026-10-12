# Cloudflare移行 実施進行記録(2026-10-08〜)

計画: docs/superpowers/plans/2026-10-08-cloudflare-migration.md
本書は実装セッション横断の作業状態記録。最終報告書は
docs/reviews/cloudflare-migration-implementation-report.md。

## 現在のフェーズ: 実LLM切替(stub→real)実施 ✅(2026-10-12・V1-P1 A-2)

実配備(https://latch-api.hiromu1018ks.workers.dev)で**Parser=実Anthropic
(claude-haiku-4-5)・Embedding=実Gemini(gemini-embedding-001)・Jev=実TypeSafe
(jev-1.13.0・フォールバックAnthropic Haiku)が稼働**。スモーク証拠:
docs/testassets/results/llm-real-smoke-20261012-060102.yaml
(parse実応答=stub決定出力と相違〔「明日の夜」を当日基準で解決〕・
embedding 2件+jev 1件がllm_attemptsへactual_provider=google/typesafe_jev
で記録・jev_resultへprovider/model入り・実Jevスコア0.95で提案自動生成
〔25.5秒〕→yes×2→matched〔26.2秒〕・実呼び出し計4件・34秒)。

- **機構**: wrangler.jsoncからLATCH_LLM_MODE varを**削除**(現行wranglerは
  varsと同名secretの登録を拒否するため・code 10053を実配備で発見)+wrangler
  secret LATCH_LLM_MODE=real。実装はコード既定値"stub"を読むため、secretを
  設定しないローカルdev/miniflare・CIは**常にstubのまま**(ci/staging/cf-dev
  の決定性と課金保護は不変・cf E2E再実施で確認済み)
- **実装**: cloudflare/api/latch_worker/llm_real.py(fetch直書きtransportの
  3系統+フォールバック・workers.fetch使用・timeout/フォールバック/breaker/
  送信記録の規律は本体LLMGatewayをそのまま再利用)。pipeline.build_pipeline
  とstate.build_stateがLATCH_LLM_MODEで分岐(stub経路はfingerprint・cost・
  llm_attempts記録を含め従来と一字も変わらない=単体試験で固定)。real時のみ
  parser/embeddingのllm_attempts記録を追加(jevはEvaluationJobRunnerが従来どおり)
- 鍵3本(LATCH_ANTHROPIC/GEMINI/TYPESAFE_API_KEY)はwrangler secretへ設定済み
- **ロールバック手順**: `wrangler secret delete LATCH_LLM_MODE` + `make
  cf-deploy`(varsはもう無くコード既定stubへ戻る)。加えて設計上の縮退
  (TypeSafe障害→Haikuフォールバック・双障害→skipped)は組み込み済み
- **発見(実配備トラブル)**: respond(YES回答)直後の_select_latchが503になる
  事象=**実D1へ0005_ui_v2_leave.sql(left_user_ids・messages.sender_id NULL
  許容)が未適用**だったため(ローカルcf-e2eは全マイグレーションを当てるため
  発現しない)。wrangler d1 execute --remote --fileで適用し解決。以後の
  教訓: **実D1へcloudflare/migrationsを新人が追加したら実D1へも適用を忘れない**
- **残るのは別承認待ち項目(実push・本番切替判断・実LLM品質比較)と最終報告書更新**
  (品質自体はG1/G2ゲートで実API検証済みのため再評価不要)

## 過去のフェーズ: フロントエンド配線(Workers Assets配信)完了 ✅(2026-10-10)

実環境(https://latch-api.hiromu1018ks.workers.dev)で**同一オリジンによる
フロント配信+APIが稼働**。ブラウザ実機でログイン→同意→意図作成→提案自動
生成→回答→成立→チャット送信まで通ることを確認。実測:
docs/testassets/results/cloudflare-frontend-e2e-20261010-015344.yaml
(提案は意図保存後49秒以内にホーム/お知らせへ出現・通知配送記録4件・
D1はE2E実施前のbaselineへ復元)。

- wrangler.jsoncへassets(frontend/dist/client・SPA fallback・
  run_worker_first=[/v1/*,/health,/probe/*])を追加。run_worker_first配列形式は
  wrangler>=4.20(実効4.149)でサポート — Sec-Fetch-Mode: navigateな/v1直接
  naviでもworkerへ到達することを実測確認
- make cf-deployがfrontend build(npm ci && npm run build)込みに統合
  (唯一のデプロイ経路を維持)
- 回帰: 契約remote 116/116(328秒・assets配信後のversionで全green)・
  frontend vitest 236・cf-unit 111(+remote gated skip 1)・
  gzip 7925KiB不変(assetsはscript上限外)
- frontend/srcのコード変更ゼロ(ユーザー登録POST /v1/usersはv1フロントにUIが
  ないためAPI直叩きで事前実施 — 記録YAMLに明記)
- LLMはstub(2026-10-12にreal切替実施 — 冒頭節参照)

## 本体実装の成果(2026-10-09)

- **組立**: cloudflare/api/latch_worker/ — app.py(本体routes再利用の
  FastAPI)・state.py(env→services構築ミドルウェア)・svc_*.py(D1読取サービス)
- **認証**: jwt_pure.py(純Python HS256/RS256・pyjwt API互換)を _compat.py
  が sys.modules['jwt'] へ登録 — auth/tokens.py・idp.py が原改変なしで動作
  (実pyjwt発行トークンとの相互運用を実証)。shims: sqlalchemy/redis/
  cryptography/anthropic/typesafe_sdk はimport互換stub(実行時不使用)
- **再利用と差し替えの境界**: routes・schemas・domain計算・errors・
  AuthService・UserService・ConsentService・IntentParseService・RateLimiter
  は本体クラスをそのまま使用(注入関数だけD1)。書込は atomic.apply_*
  (command marker+outbox)。読取は svc_*.py が参照実装と同一SQL意味でD1化
- **バンドル**: latchパッケージは `make cf-sync`(rsync)で
  cloudflare/api/latch/ へコピー(bundlerはsymlinkを辿らない)。
  **デプロイは `make cf-deploy` を使うこと**(cf-sync込み)
- **geofeatures**: 0004_geofeatures_normalized.sql(正規化名列追加)+
  ci実データ7行シード(backend/tests/cloudflare/fixtures/geofeatures_seed.sql)
  を実D1へ適用済み
- **契約試験**: tests/contracts/test_cloudflare_parity.py +
  cloudflare_harness.py(local in-process / miniflare / remoteの3モード)
  - local in-process(ASGI+LocalD1Database): **116/116 passed**
  - miniflare(実Pyodide・実D1 API・shims/jwt_pure実経路): **116/116 passed**
  - remote(実環境 https://latch-api...): **116/116 passed**(532秒)
  - backend全套(LATCH_LLM_MODE=stub): **2104 passed / 0 failed**
  - cf-unit(cloudflare資産試験): 107 passed(既存資産無傷)
- 2026-10-09当時のdeploy version: 68a8e9d9-62a2-4977-b2e7-eaa0346bc585

## パイプライン組込みの成果(2026-10-10)

- **pipeline.py**(cloudflare/api/latch_worker/): outbox掃き(post-commit
  ベストエフォート+cron・at-least-once・envelopeはIDのみ)・評価
  (embedding stub生成→T4検索snapshot→layer3 cheap judge→match_candidates
  UNIQUE(a,b,va,vb)→EvaluationJobRunner+stub jev(I1 claim・llm_attempts
  記録)→latch_score→閾値超えをatomic.apply_proposal(D-08・通知job同時保存))
  ・run_cron(ScheduledEvaluator・jobs lease回収・outbox掃き・notify/
  retention job・run_retention)・handle_queue_message(consumer)
- **svc_intents.py hooks**: 意図確定後(request_evaluation/mark_paused+
  post-commit配送)。**atomic.apply_response**: matched時に評価scheduleを
  一時化(guard従属)。**entry.py**: scheduled/queueを実装へ差し替え
  (cron結果はprobe_eventsへ稼働証跡として記録・observability off維持)
- **stub JEV応答**: 両方向noul 0.9で固定(決定的・score=0.9≥閾値0.60で
  提案成立。実LLMとは無関係・コンストラクタ差替えはstubの正規機能)
- **修正1件**: run_retentionのsnapshot掃きは実D1のFK強制により子行
  (retrieval_snapshot_rows)を親より先に削除するよう修正(初回E2Eの
  cron証跡で発見→修正後のcron証跡で解消確認)
- **新規試験**: tests/cloudflare/test_pipeline_e2e.py(in-process 4試験:
  E2E一貫性・outbox再送at-least-once・再配送/cron重複で提案・Jev試行とも
  1件のまま・cron単独で提案到達)+ test_pipeline_remote_e2e.py(実環境
  ゲート付き・結果YAML自動生成)
- **実測(最終)**: 意図2件作成1.4秒→提案自動生成11.7秒(queue即時経路・
  cronは*/5フォールバック)→matched 12.1秒→cron通知配送記録4件。
  jobs未収束0・outbox未送0・cron全段ok(retention_error解消)
- 最終deploy version: 47b68bbe-3577-44dc-bbb1-aaf2af611c03
  (初回e2ff75c9はretention FK修正前・挙動はretention段以外同一)
- **テスト現況**: backend全套 **2108 passed / 0 failed**(2104+パイプライン4)
  ・cf-unit **111 passed**(107+4)・契約remote 116/116(2026-10-10)

## フロントエンド配線の成果(2026-10-10)

- **assets設定**: cloudflare/api/wrangler.jsoncへ
  `"assets": {"directory": "../../frontend/dist/client",
  "not_found_handling": "single-page-application",
  "run_worker_first": ["/v1/*", "/health", "/probe/*"]}` を追加。
  wranglerは設定ファイル相対のプロジェクト外パスを拒否しないためcf-sync的な
  コピーは不要(ドライランで"Read 34 files"を確認)
- **Makefile**: cf-deployが `cd frontend && npm ci && npm run build` から
  始まる(make cf-deploy = 唯一のデプロイ経路・frontend build込み)
- **routing実測(curl)**: (a) `/` =200 index.html (b) `/v1/intents` 無認証=
  401 JSON(worker到達・Sec-Fetch-Mode: navigate付きでも同一=run_worker_first
  効) (c) `/health`=200 ok・`/probe/report`=200 worker応答 (d) 未知パス=
  200 index.html(SPA fallback) (e) 静的asset=200(正しいcontent-type)
- **実機結合(ブラウザ=ZCode in-app browser)**: alpha=UI全面経由
  (トークンパネル→オンボーディング同意→意図入力+条件編集→提案確認→
  参加する→成立後チャット送信)・beta=API直叩き(相手側として許容)。
  登録(POST /v1/users)のみAPI(v1フロントに登録UIなし・コード変更ゼロ)
- **deploy version**: 7d9e0c78-55ba-43b7-8a67-21ac7d559e70
  (gzip 7925.29KiB・assets 32ファイル)

## 実環境契約試験の再実行手順(回帰確認に使う)

```
export CLOUDFLARE_API_TOKEN=$(grep ^CLOUDFLARE_API_TOKEN= .env | cut -d= -f2-)
cd backend
LATCH_CF_REMOTE=1 LATCH_CF_API_URL=https://latch-api.hiromu1018ks.workers.dev \
  uv run pytest tests/contracts/test_cloudflare_parity.py -q
# seed/cleanupはwrangler d1 execute --remote(-y付き・D1エラー応答検知あり)
# miniflare検証時は LATCH_CF_D1_SQLITE=<devのD1 sqliteパス> を代わりに指定
```

## 実配備環境(2026-10-10時点・稼働中)

- Worker(API本体+probe+フロントassets併設): https://latch-api.hiromu1018ks.workers.dev
  (cloudflare/api — gzip 7925KiB(上限10MB)・cron */5・observability off・
  vars: LATCH_ENV=api〔LATCH_LLM_MODEは2026-10-12にvarsから削除=secret切替へ〕・
  queue consumer稼働。
  Workers Assetsでfrontend/dist/clientを同一オリジン配信・API/probe経路は
  run_worker_firstでworker先行)
  secrets(2026-10-12〜): LATCH_LLM_MODE=real・LATCH_ANTHROPIC_API_KEY・
  LATCH_GEMINI_API_KEY・LATCH_TYPESAFE_API_KEY(鍵値は.env由来・repo外)
- D1: latch-core(id: f40367ac-7631-4506-a083-c52e7ab51787)
  マイグレーション0001〜0006適用済み(0005・0006は2026-10-12に実D1へ適用。
  0006_survey.sql=V1-P3体感アンケートのsurvey_responses表)+
  geofeatures 7行+probe_events表
  (cron稼働証跡として継続記録)
- Queues: latch-jobs-probe(producer=env.JOBS・consumer=パイプライン評価)
- 認証: .envのCLOUDFLARE_API_TOKEN・アカウント67e928741c8e73415fe206699d0f1667
- プラン: Workers Paid(2026-10のみ契約・ユーザー申告)

## 実配備で判明した差異(本体実装で必ず反映)

1. scheduled/queueハンドラのenvはself.envから取得(引数envはNone)
2. D1の.all()はD1Result{success,meta,results}形式(行は.results内)。
   .first()はlocalと同一動作 — 複数行読取は.first()ループかraw処理が確実
3. マルチisolate: メモリ内グローバルは共有されない — 状態はD1永続化
4. workers.devドメインへの自己subrequestは404 — service binding/カスタム
   ドメインが必要(外部URLへのfetchは無関係)
5. JsProxy値はstr()/int()で明示変換(json.loads等の型要求)

## 本体実装の要点(次セッションの仕事)

- 再利用: backend/src/latch/(routes・schemas・domain計算) +
  adapters/d1/(atomic・latches・intents・safety・sessions・retrieval等)
- LLM SDK・pyjwtはバンドル不可(gzip 10MB上限)→
  LLM: fetch直書きtransport(stubで開始)・JWT: probes.pyの純Python
  HS256/RS256実装を認証へ流用
- 契約試験: backend/tests/contracts/(119ケース・transport自由)を
  実環境URLへ流す — PostgreSQL用reference_harnessと同型の
  cloudflare_harnessが必要(actor provisioningはデプロイ済みAPI経由。
  認証鍵はwrangler secret/varで同一テスト鍵を設定)
- 費用: Workers Paid枠内。実LLM呼出しは2026-10-12に実施(冒頭節参照)

## 過去の成果(詳細は各節)

- test-ci全套: **1988 passed, 1 skipped, 0 failed**(クリーンDB)
- frontend: 236 passed
- 実配備probe実測: コールド1.1秒/warm 110-126ms・Queues E2E 8.6秒・
  cron毎分→5分・純Python RS256動作(記録: cloudflare-prod-probe-20261009.yaml)
- 採否: I1/I2/I3/I5=採用・I4=一部採用・I6=別実験・Vectorize=deferred

## 完了タスク詳細

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
- test-ci系実行時は `LATCH_LLM_MODE=stub` 明示必須(実環境にrealが漏れている)
- uv: `export UV_CACHE_DIR=/home/misty/Projects/latch/.uv-cache`
  (wrangler系はMakefileのCF_ENVが一括設定: UV_PYTHON_INSTALL_DIR等)
- ローカル検証: `make cf-unit`(worker不要)・`make cf-dev`(cf-sync込み・
  別ターミナル)+`make cf-probe`
- 実配備: **`make cf-deploy`**(cf-sync+latchコピー込み。直pywrangler deployは
  latchコピーが更新されないため使わない)
- 実D1確認: `npx wrangler d1 execute latch-core --remote --command "..."`
- miniflareのD1は `wrangler d1 execute --local` がdev serverと別sqliteを
  参照する点に注意(devが使う実ファイルへ直接sqlite3でマイグレーションした)
- **実LLM呼出しは2026-10-12に実施**(V1-P1 A-2委任・冒頭節)。**実データ移行はユーザー承認待ちのまま**(実配備・実D1は承認済み)
