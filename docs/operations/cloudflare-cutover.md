# Cloudflare切替運用手順(T12・実装承認後の実施用)

作成: 2026-10-08移行計画 §6 T12。状態: **リハーサル済み(seed)・本番実施は未承認**。

前提: T0〜T12の実装・ローカル検証完了(進行記録: docs/plans/CLOUDFLARE-MIGRATION-STATUS.md)。
本手順の実データ作業・デプロイは、**ユーザーの別の明示的承認後にのみ**実施する。

## 1. 前提確認(実施前チェックリスト)

- [ ] 実承認の取得(対象・時刻・巻き戻し基準の明記)
- [ ] Cloudflareアカウント: Workers Paid・D1・Queues・R2有効化
- [ ] 実Workersの小規模配備と実測probe(T1の別承認項目)が合格
- [ ] 旧系(GCP/ローカル)が稼働中で書込み可能
- [ ] 秘密情報(API鍵・JWT鍵)はCloudflare secretsへ設定(DB移行対象外)

## 2. 切替手順(単一書込み先への切り替え)

1. **受付停止(旧系)**: 機能フラグ/環境操作で新規Intent受付と通知を停止
   (旧ロールバック手順 docs/plans/M4/ws-8-rollback.md の3段階遮断を参考)
2. **処理中jobの整理**: 旧worker停止→match_events pending=0を確認
3. **export**: `latch.migration.export.export_source` で旧DBを取得
   (MigrationManifest: 件数・整合性hash・削除watermark・writer世代)
4. **session/counter取得**: Redisから refresh_tokens・family・jti失効・
   counter(D-16/Jev予算)を snapshot化し `transform_session_snapshot` へ
5. **変換・import**: `transform_dump` → D1へimport(wrangler d1 execute またはadapter)
6. **照合**: `verify_migration` — 件数・FK・状態・履歴・公開範囲・認証状態
   **1つでも不一致なら以降へ進まない**
7. **writer世代の更新**: `latch.migration.replay.CURRENT_WRITER_GENERATION` を進め
   旧API・旧jobの遅い書込みが拒否されることを確認(outbox.writer_generation)
8. **関門**: `CutoverGate` — source_drained・verify_ok・journal_ready・
   replay_proven が全て真であることを確認 → D1を唯一の書込み先として開放
9. **DNS切替**: Cloudflare側をユーザーへ公開
   (DNS変更だけで切替完了としない — 7・8の世代と関門が本体)

## 3. 巻き戻し手順

### 3a. 書込み開始前(D1へ未書込み)
- 旧系をそのまま再公開(手順2.1を逆操作)。D1側は破棄。

### 3b. 書込み開始後(コードのみ戻す — 第一案)
- Cloudflareのコードを前バージョンへ戻し、D1はそのまま参照
  (D1 schemaは後方互換の範囲で戻す)

### 3c. DBまで戻す場合
1. 受付停止(旧系と同じ3段階遮断)
2. D1の追加command journalを取得: `export_new_journal`
3. 旧DBへreplay: `replay_to_postgres`(状態のみ・LLM/通知の再送なし)
4. 照合: 件数・状態・認証(§2.6と同じ検査)
5. 旧系を再開(writer世代を戻す)
6. **通知・LLM仕事の重複処理防止**: outbox event_id・notification_deliveries・
   llm_attempts の処理済み印がreplay経由で失われないことを確認

### 停止基準(切替直後の観察)
- 遅延・失敗率が停止閾値を超えたら受付停止(観測時間・判断担当は承認時に確定)

## 4. リハーサル結果(2026-10-08・seed)

- 記録: docs/testassets/results/cloudflare-cutover-rehearsal-*.yaml
- 合格: export→変換→import→照合(件数・FK・状態・履歴)・
  認証状態移行(回転/消費済み401相当/失効/counter)・
  writer世代拒否・巻き戻しreplay(冪等・重複処理なし)・関門
- 本番データ: 未配備のため無し(seedで検証 — 計画T12-1の定めるとおり)

## 5. 実施時の記録義務

- 各手順の実測時間・件数・hashを切替記録として保存
- 切替後24時間は §5.6 の監視項目を通常の2倍頻度で観察
