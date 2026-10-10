# Cloudflare移行 実装・検証・リハーサル報告書

- 日付: 2026-10-09(2026-10-10更新: 本体実装・パイプライン組込み実測を追加)
- 対象計画: docs/superpowers/plans/2026-10-08-cloudflare-migration.md
- 範囲: T0〜T12の実装・ローカル検証・seedリハーサルに加え、本体LATCH APIの
  実Workers配備とマッチングパイプライン組込みまで実施(§8)。
  **実データ移行・実LLM比較・実push・本番切替は未実施(承認待ち)**
- 検証環境: ローカル(compose ci PostgreSQL + LocalD1Database[D1 API互換] +
  pywrangler[Pyodide 3.14.2] + stub LLM)+ 実環境(Workers/D1/Queues/Cron)。
  実測値の出所は各節に明記

## 1. 変更内容(全タスク実装完了)

| タスク | 主な成果物 | コミット |
|---|---|---|
| T0 契約固定 | tests/contracts/(ContractCase 119ケース・BaselineManifest・B0呼出回数・回帰ケース4分類) | dd2a59b |
| T1 Workers互換性 | cloudflare/api(probe worker)・互換性レポート・make cf-dev/probe | 2a2e228 |
| T2 domain分離 | domain/(time・types・latch_status・geo_distance・parse_key・evaluation_key)+ports/4種+依存検査 | ede54e2 |
| T3 D1原子command | 0001_core.sql(23表+D1新規10表)・adapters/d1(database/atomic/latches/intents/safety/sessions) | 4364310 |
| T4 正確検索 | adapters/d1/retrieval(snapshot方式)・PostgreSQL実測一致 | dd3fb1a |
| T5 jobs/I1 | 0002_jobs.sql・jobs/evaluations/attempts・runtime/cloudflare/jobs | bd8753e |
| T6 評価cache/I2 | evaluation_cache表・adapter(厳密fingerprint) | 444a03e |
| T7 解析再利用/I4 | domain/parse_key(集約・手動修正保護)・frontend基準日対応 | b8d67fe |
| T8 schedule/I3 | 0003_schedule.sql・adapters/d1/schedule・runtime/cloudflare/scheduled | 99af1ee |
| T9 Vectorize | 比較harness・採否記録(見送り・再判断条件付き) | 3f64230 |
| T10 通知/保持/復元 | notification_jobs・retention/restore・observability | 4d1eea5 |
| T11 E2E/費用 | migration_eval/(compare・costs)・比較・費用記録 | 1ec7c85 |
| T12 切替リハーサル | migration/(export・transform・verify・replay)・運用手順書 | 81e92f5 |

### 6つの改善の採否と削減効果

| 施策 | 採否 | 削減効果(実測) | 品質への影響 | 根拠 |
|---|---|---|---|---|
| I1 処理前の確保 | **採用** | 同キー20並行: 外部呼出20回→1回(19回削減/評価) | なし(結果・候補・通知はB0一致・20並行試験) | T5 test_job_recovery 13試験 |
| I2 評価cache | **採用** | 同fingerprint再評価: 追加呼出1→0回(I1との差・別計上) | なし(数値結果のみ保存・hard条件はhit時再確認) | T6 test_evaluation_cache 14試験 |
| I3 due時刻・世代 | **採用** | 旧Redis抑制で失われていた新版の評価漏れを解消(品質維持が主効果) | 正(抑制中の更新が消えない) | T8 test_schedule_recovery 14試験 |
| I4 解析再利用 | **一部採用** | 同一実行内同文並行: 20→1呼出。**永続cacheは保存契約承認まで導入せず** | 手動修正保護(manual優先)・JST日跨ぎ再解析 | T7 backend 9+frontend 3試験 |
| I5 保持と監視 | **採用** | 候補30日・cache/snapshot期限掃除。**履歴・通報・calibrationは維持**(料金だけで消さない) | 監視は永続データから再計算(process counterを正にしない) | T10 test_retention/restore 10試験 |
| I6 枠と閾値 | **実施せず(別実験)** | — | 枠8・閾値0.60を維持 | 計画§5.7「移行の必須条件にしない」・T11記録のとおり別実験として分離 |

Vectorize(T9): **見送り**(ANNは決定的一致を保証できない+実index比較は
実配備承認待ち+現行は正確検索で費用要件内)。再判断条件は
docs/testassets/results/cloudflare-vectorize-decision.yaml に記録。

## 2. 試験結果

### ローカル決定的試験(全緑)

| スイート | 件数 | 内容 |
|---|---|---|
| unit全套 | 1564 passed | 既存試験無傷(計算結果同一・T2分離の回帰なし) |
| cloudflare suite | 107 passed | T3 19+T4 10+T5 13+T6 14+T7 9+T8 14+T9 5+T10 10+T11 2+T12 6+probe 5 |
| contracts(参照実装) | 121 passed | 119ケース+manifest+B0記録 |
| frontend vitest | 236 passed | 233+3(JST日跨ぎ) |

test-ci全套(worker停止+load DB起動+`LATCH_LLM_MODE=stub`):
最終実行結果は §6 の最終確認に記載(本報告書と同時に実施)。

### §7受入条件に対する実施試験の対応

| 項目 | 受入条件 | 実施した試験と結果 |
|---|---|---|
| API契約・状態遷移 | 同じ入力・Clockで同じ応答 | contracts 119ケース緑(PostgreSQL参照実装)。D1版はT3コマンド試験で同一業務結果 |
| 認可・非公開性・同意・block・削除 | 違反0件(cache/索引/再配送にも) | T3 20並行6種+T6 cache失効+T10復元の公開範囲検査 |
| 決定的な正確検索 | 候補集合・順位・提案が旧版と一致 | T4 PostgreSQL実測一致(ID・版・順位完全一致・similarity 1e-6以内・pass_count一致) |
| 保存 | p95 500ms以下 | **ローカル実測のみ**(T3 apply_*はms台)。実環境は承認後 |
| Parser | p95 3秒/p99 8秒・G1基準維持 | 解析経路不変(BaselineManifestでprompt/model hash同一を検証)。実測は承認後 |
| 候補検索 | p95 1秒・候補削減なし | T4は切り詰め前全件計算(55→50は全条件後)。実測は承認後 |
| 初期判定 | p95 10秒 | T8即時due+T5 claim。実測は承認後 |
| 通知 | 判定後5秒 | T10配送(stub)。実測は承認後 |
| 再評価 | 時間条件漏れなし・due遅延p95 60秒 | T8 14試験(bucket/2時間前/期限/抑制解除/回収/公平) |
| 障害 | 並行重複防止・unknown追跡 | T5 13試験(claim 1回・fencing・prepared残存・unknown記録) |
| 可用性・復元 | 実データ規模で測定し許容値を承認 | T10 export→restore整合(回答・履歴・削除・認証)。実環境数値は承認後 |
| 費用 | 全試行・全サービス込み | T11費用記録($9.51/月@3,000評価・LLM概算込み) |

**§7のうち実環境実測を要する項目(保存/Parser/検索/初期判定/通知の絶対値・
実LLM品質)は未実施** — 実配備probe(T1)と実Workers試験(T11)の承認待ち。
これら以外は実施試験で満たしている。

### リハーサル(完了条件3)

- 変換: seed→export→変換→D1 import→照合(件数・FK・状態・履歴) ✅
- 認証状態: 旧refresh回転✅・消費済み再利用→family失効✅・失効JWT拒否✅・
  counter残存✅
- 切替: writer世代で旧書込み拒否✅
- 巻き戻し: D1 journal→旧DB replay(matched再適用✅・再replay冪等✅・
  LLM/通知の再送なし✅)
- **回答・履歴・削除・認証状態の整合性**: T12 6試験+T10復元試験で確認 ✅
- 記録: docs/testassets/results/cloudflare-cutover-rehearsal-*.yaml

## 3. 費用見込み(実測ベース・USD)

出典: docs/testassets/results/cloudflare-e2e-comparison-20261008-120000.yaml

| 項目 | 月額(USD) | 根拠 |
|---|---|---|
| Workers Paid | $5.00 | 枠内(3000評価×6req倍率・CPU) |
| D1 | $0.00 | 枠内(読取25B行/書込50M行・3000評価×430行) |
| Queues | $0.00 | 枠内(ops 10M・3000×4) |
| R2 | $0.01 | 暗号化export 0.5GB |
| LLM | $4.50 | 3,000試行×$0.0015(概算 — llm_attempts実測で置換) |
| **合計** | **$9.51/月** | ≒¥1,400〜1,600(為替想定150円) |

- 月額1,000〜3,000円の目安内(§1のとおり厳格上限ではない)
- 旧GCP構成試算(Phase 1 ≈$262/月)に対し大幅低減。ただしLLM費は
  構成非依存で同額 — 差分はインフラ
- 増加時は候補削減・モデル変更をせず報告して予算再判断(計画§1)

## 4. 残る課題(承認待ち・未実施)

1. ~~**実配備probe(T1別承認)**: 実Workersでの性能・制限・コールドスタート~~
   → 2026-10-09実施済み(cloudflare-prod-probe-20261009.yaml・承認済み)
2. ~~**実D1確認(T3別承認)**: LocalD1DatabaseはD1 API互換だが、実D1での
   並行・batch意味の最終確認~~ → 2026-10-09実施済み(契約116ケースが
   実D1・実Workersで全green・§8)
3. **実環境UX・費用実測(T11 unresolved)**: 日本からのcold/warm・burst・
   障害後回収・実LLM品質比較(G1/G2資産再利用)。stub E2Eは§8で実施
4. **実push(ws-9資材待ち)**: FCM実装はstubのまま(既存課題の引継ぎ)
5. **履歴匿名化(D-13二段階目)**: 未実装のまま別受入項目(計画§5.6)
6. **D1所在地・委託先告知**: APAC配置hintは日本国内保存の保証ではない —
   実配備時に確認(計画T10-2)
7. **G1再実施**: prompt/modelはBaselineManifest hash同一のため不要と判断。
   解析経路を変える場合は再実施

## 5. 本番切替手順(要約)

詳細: docs/operations/cloudflare-cutover.md(リハーサル済み・実施は承認後)

1. 承認取得 → 2. 旧系受付停止・job整理 → 3. export+session snapshot →
4. 変換・D1 import → 5. verify_migration(不一致なら中止) →
6. writer世代更新(旧書込み拒否) → 7. CutoverGate確認 → 8. D1書込み開放 →
9. DNS切替。巻き戻し: 開始前=旧系復帰/開始後=コードのみ戻す第一・
DBまで戻す場合はjournal replay→照合→再開。

## 6. 最終確認

test-ci全套(クリーンDB・worker停止・load DB起動・`LATCH_LLM_MODE=stub`):

**1988 passed, 1 skipped, 0 failed**(2026-10-09・所要6分44秒)

内訳: unit全套 + integration全套 + contracts 121 + cloudflare suite 107
(1 skipped = cloudflare_local probe〔make cf-dev起動時に実行〕)
frontend vitest: 236 passed(24 files)

備考: 共有ci DBは実行中の並行pytestで候補等が蓄積するため、
異常終行後はTRUNCATE(geofeatures以外)してから再実行すること
(本報告の最終実行はクリーン状態で実施)。

## 7. 結論

- 完了条件1(全必須タスク+採否記録): **達成** — T0〜T12実装・§1表のとおり
- 完了条件2(§7を受行した試験結果で満たす): **ローカル検証可能な全項目で達成**。
  実環境実測項目(性能絶対値・実LLM品質)は承認待ちとして明示(§4)
- 完了条件3(リハーサル+回答・履歴・削除・認証整合): **達成**(seed)
- 完了条件4(報告書): **本書**
- 本番切替: **ユーザーの明示的承認を待って停止**(§5手順書あり)

## 8. 本体配備とマッチングパイプライン組込み(2026-10-09〜10・実環境)

初版報告(§1〜§7)はローカル検証まで。以降の実施分を追記する。
進行記録: docs/plans/CLOUDFLARE-MIGRATION-STATUS.md

### 8.1 本体LATCH APIの実Workers配備(2026-10-09)

- 組立: cloudflare/api/latch_worker/(本体routes/schemas/domain計算を再利用・
  読取はsvc_*.pyのD1化・書込はatomic.apply_*・認証は純Python JWT shim)
- 実測: 契約116ケースをlocal in-process・miniflare・実環境の3経路で全green
  (docs/testassets/results/cloudflare-api-parity-20261009.yaml)
- backend全套 2104 passed / cf-unit 107 passed(既存資産無傷)

### 8.2 バックグラウンド処理(マッチングパイプライン)の組込み(2026-10-10)

同期APIだけだった Workers へ、意図保存からLATCH成立・通知記録までの
非同期一連を接続した(LLMはstubのまま・実LLM呼出しは承認待ち・外部送信なし)。

配線(計画§4配送・§5設計規律どおり):

| 経路 | 実装 | 保証 |
|---|---|---|
| 意図確定時 | svc_intents hooks(schedule登録+post-commitベストエフォート配送) | 失敗してもcron掃き+schedule dueが評価を保証 |
| outbox→Queues | pipeline.dispatch_outbox(cron掃き・envelopeはIDのみ) | at-least-once・重複実行は評価冪等性で吸収 |
| queue consumer | handle_queue_message→evaluate_intent | embedding stub生成→T4検索snapshot→layer3→match_candidates(UNIQUE)→EvaluationJobRunner+stub jev(I1 claim・llm_attempts記録)→apply_proposal(D-08・通知job同時保存) |
| cron(*/5) | run_cron | ScheduledEvaluator(T8)・jobs lease回収(T5)・outbox掃き・notify/retention job(T10)・run_retention |
| 回答成立 | atomic.apply_response(matched時にschedule一時化を従属追加) | 成立Intentは再評価機会から外れる |

- stub JEV応答は両方向noul 0.9に固定(決定的・score=0.9≥閾値0.60。
  stubのコンストラクタ応答差替えは正規機能 — 実LLM品質とは無関係)
- 修正1件: run_retentionのsnapshot掃きは実D1のFK強制のため子行→親の順に
  削除するよう修正(初回E2Eのcron証跡で発見・修正後証跡で解消確認)

### 8.3 実環境実測(最終・version 47b68bbe)

docs/testassets/results/cloudflare-pipeline-e2e-20261010-010604.yaml

- 意図2件(matching)作成 1.4秒 → **LATCH提案自動生成 11.7秒**(queue即時経路。
  cron */5はフォールバック) → 回答yes×2 → matched 12.1秒
- 通知: API面で両者にproposal+latch_matched・**cron配送記録4件**
  (notification_deliveries・安定ID冪等)
- D1状態: jobs未収束0・outbox未送信0・llm_attempts(jev)記録あり
  ・cron全段ok(schedule回収52件含む・retention正常)
- 契約116ケースは組込み後も実環境で全green(336秒)

### 8.4 試験(2026-10-10時点)

| スイート | 件数 | 内容 |
|---|---|---|
| backend全套 | **2108 passed / 0 failed** | 2104+新規パイプライン4(worker停止・LATCH_LLM_MODE=stub) |
| cf-unit | **111 passed** | 107+新規4(in-process E2E一貫性・outbox再送at-least-once・再配送/cron重複で提案・Jev試行とも1件・cron単独で提案到達) |
| 契約(remote) | **116/116 passed** | 実環境・組込み後回帰 |
| 実環境E2E | 1 passed(約3分) | test_pipeline_remote_e2e.py(LATCH_CF_REMOTE=1・結果YAML自動生成) |

バンドル: gzip 7925KiB(上限10MB内・本体実装から+7KiB)・cron */5維持・
observability off維持(cron結果はprobe_eventsへ稼働証跡として記録)。
費用: 実測範囲(契約×2+デプロイ×2+E2E×2+cron数百回)はWorkers Paid $5枠内。
