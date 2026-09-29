# M2 ws-6(Layer 5 LATCH Engine)実行報告

- ブランチ: m2-ws-6 / ベース: 57e2e3e
- 日付: 2026-09-29
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `All checks passed!`(ruff format --check+check)・`878 passed, 149 deselected in 6.10s`(unit全件。既存787+本単位91: latch_calc 19・proposal 8・latch_engine 46・reeval 13・worker 5〔追加〕・settingsピン追記は既存関数内) |
| 2 | integration 10試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | `uv run pytest --collect-only tests/integration/test_matching_latchengine.py -q` → `10 tests collected`・exit 0 |
| 3 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic\|time\.sleep\|from time import' backend/src` のヒットは `backend/src/latch/core/clock.py:33` の1行のみ。`test_arch_no_direct_time.py` → `1 passed` |
| 4 | alembic 0001〜0003・docs無変更+チェーン確認 | PASS | `git diff --stat main -- …0001…0003 docs` → 出力なし(空)。チェーン確認スクリプト → `OK heads= ['0004']`(walk=0004→0003→0002→0001)。`pytest --collect-only tests/integration/test_schema.py -q` → 25 collected |
| 5 | 変更ファイル=§4の一覧(15ファイル) | PASS | `git diff --name-only main \| sort` が作成11+変更4+report=15ファイルと完全一致(下記一覧)・`git status --short` 空 |
| 6 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 出力なし(空) |
| 7 | 既存unit試験グリーン維持 | PASS | `make test` 878 passed(既存787は不変・期待値変更は§4列挙のtest_settings.py〔test_settings_defaultsへ2行追加〕とtest_worker.py〔配線ピン追記〕のみ)。§4列挙以外の既存試験への変更なし |

**test-ci=スーパーバイザー検証待ち**(STATUS運用ルール1〜3・マイグレーション0004追加のため
migrate/test-ci/docker系は実装側で未実行。wave単独のため並走とのDB取り合いは計画上なし)

変更ファイル一覧(§4どおり・15ファイル):
```text
backend/alembic/versions/0004_latches_open_unique.py      (作成)
backend/src/latch/worker/matching/latch_calc.py           (作成)
backend/src/latch/worker/matching/proposal.py             (作成)
backend/src/latch/worker/matching/latch_engine.py         (作成)
backend/src/latch/worker/reeval.py                        (作成)
backend/tests/unit/matching/test_latch_calc.py            (作成)
backend/tests/unit/matching/test_proposal.py              (作成)
backend/tests/unit/matching/test_latch_engine.py          (作成)
backend/tests/unit/test_worker_reeval.py                  (作成)
backend/tests/integration/test_matching_latchengine.py    (作成)
docs/plans/M2/ws-6-report.md                              (作成・本ファイル)
backend/src/latch/worker/main.py                          (変更)
backend/src/latch/settings.py                              (変更)
backend/tests/unit/test_worker.py                         (変更)
backend/tests/unit/test_settings.py                       (変更)
```

## design §5 実装時確認事項の結果
9. notifications.type列のCHECK制約有無: **CHECKなしを再確認**(Task 4 Step 1で
   `rg -n "CHECK" backend/alembic/versions/0001_initial_schema.py` を実行。
   ヒットは行6(コメント)・41(users.auth_provider)・68/70(intents.visibility/
   notification_level)・117/133(group_latches/latches.cardinality)・
   243/257(calibration_records)。notificationsテーブル部(211〜220行)は
   `type text NOT NULL` のみ・CHECKなし)。計画時点の確認と一致。よって
   type値域はコード側のLiteral(`NOTIFICATION_PROPOSAL="proposal"`・
   `NOTIFICATION_NEARBY="nearby_candidate"`)で管理(unit試験
   `test_notification_type_pins` がピン)。BLOCKED事象なし
10. uuid[]部分UNIQUE索引のON CONFLICT推論: unitのSQLピンは済み
    (`_INSERT_LATCH` の `ON CONFLICT (intent_ids) WHERE status IN
    ('candidate','proposed','partial_accept') DO NOTHING` を
    `test_insert_latch_pins_on_conflict_partial_unique` が検証・
    postgresql dialectのcompile検査も通過)。実DBでの推論確認は
    スーパーバイザー検証時のintegration試験1・9が担う
    (試験9: handle 2回でlatches二重なし=推論とON CONFLICTが実効している証拠。
    掠んだ場合はINSERT前SELECT FOR UPDATE切替を協議)

## 固定値の変更有無(design.md §2・本計画§9)
- 実行位置=案A(_kick_jev内JevWorker直列・design §2.1): 変更なし
- 0004部分UNIQUE索引+ON CONFLICT DO NOTHING(design §2.3): 変更なし
- D-07判定のd07_allows純関数化・両直前呼出(design §2.4): 変更なし
- 対象開始時刻=max(time_start)(承認済み解釈): 変更なし
- notifications先行書き込み・payload={"latch_id"}最小参照(承認事項2): 変更なし
- drain直接投入統一・scheduled Event不使用(design §2.8): 変更なし
- 本計画§9のIF確定事項(SQL全文・純関数・try_promote手順): 変更なし
  (§9の配列のみ後述のとおりTask 4/5間で前倒し配置。中身は§9-4/§9-5の全文どおり)

## (ws-7・ws-8・M3への引継ぎ)
- ws-7: latches生成・proposal生成(headcount=2)・drain・try_promoteは1対1構成。
  intent_ids正規化はsorted・集合側は同様に正規化。拡張点は各docstringに記載
- ws-8: LatchEngineにLLM呼び出し・外部APIなし(geo逆転はPostGIS完結)。障害注入対象外
- M3-1〜M3-5: responsesは常に'[]'(試験投入分を除く)。回答APIはtry_promoteと同一の
  条件付きUPDATE規律で書く。expiry_sweeperはcandidateのexpires_at切れを担当。
  FCM/お知らせUIはnotifications行から組み立て(payload={"latch_id"})

## スーパーバイザー検証手順(test-ci実行時)
1. `docker compose build api worker` — イメージ再ビルド(STATUS運用ルール4)
2. `make migrate` — 0004適用確認(alembic_version=0004・`\di uq_latches_intent_ids_open` の存在)
3. `make test-ci` — 既存全数(926)+本単位unit 91+integration 10がグリーン。
   design §5-10のON CONFLICT推論は試験1・9が実証(失敗する場合はINSERT前
   SELECT FOR UPDATE切替を協議)
4. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
   (運用ルール5)
5. test-ci後、Redis残存(prefix掃除)とuser残存(subject LIKE 'm2ws6-%'=0件)・
   latches/latch_status_events/notifications残存(prefix由来=0件)を1回手動確認

## コミット一覧
```text
ddbd8a5 feat: マイグレーション0004(latches開いている行の部分UNIQUE索引)
67892b3 feat: latch_calc純関数群(D-05式・match_level・D-07判定・bucket_start)
30496ff feat: proposal生成(visibility分岐・budget/secondary規則・nearby最小構造)
6bbf234 feat: LatchEngine前半(latch_score計算・latches生成/昇格・nearby存在通知)
f56e41f feat: LatchEngine後半(try_promote・drain・D-08上限・75分ルール・notifications)
3e50d34 feat: ReevalRunner(catch-up・30分Bucket・直接投入・sleep-first周期)
cc31e0c feat: Worker配線(_kick_jev直列LatchEngine・ReevalRunner周期task)・settings追加
a2618e4 docs: ws-6報告書とintegration試験(test-ci=スーパーバイザー検証待ち)
```

## 補足(詰まった点・判断した点)
1. **`_COUNT_DAILY_NOTIFICATIONS` と `_drain_candidates` シグネチャの前倒し**:
   計画書ではTask 5の追記リストに列挙されていたが、Task 4の `_evaluate_pair`
   nearby経路(試験項目7)と `_patch` のmonkeypatch対象(`le._drain_candidates`
   属性)がTask 4時点で必要とするためTask 4で実装した。中身は§9-4/§9-5の
   設計どおり(変更なし・配置時期のみ前倒し)
2. **latch_calc試験 `test_deadline_can_be_past_notify_time` の入力修正**:
   計画書掲載のテストコードは入力とアサーションが不一致だった(expires=
   now+5分ならD-05式は min(now+2h, now+5m)=now+5分を返し `d <= NOW` は不成立)。
   design §2.6の式(=03 D-05)が権威のため、入力を min_expires=now-5分(過去)
   へ変更して「期限が通知時刻を過去に突き抜ける」ケースを実効化した
   (実装不変)。latch_engine側の同系試験(後半5)も同一理由で設定を
   「min_expires=now-5分・latches.expires_at=now+1h(将来)」としている
3. **integration試験7(d07)の構成**: 計画書の後者ケース「閉じた過去行+deferで
   新規INSERT経路が抑制される」は、`_RECORD_SCORE` の退避構造上
   latch_score NULL行の評価は常に prev=NULL(=新評価世代=無条件変化あり・
   06 §10手順5)となるためM2の現行経路では発生しない(design §2.10-7どおり
   呼び出し経路を作らない)。prev非NULL+|Δ|<0.05の拒否骨格はunit試験
   (test_latch_engine.py がprevスタブで2件)が検証済み。本ファイルは
   「開いているcandidate行+defer注入→新世代で昇格」と「抑制期間経過
   (deferから25h)で昇格」の2ケースを検証する
4. **integration試験8(drain順序)の観測方法**: FakeClock固定では
   latch_status_events/notificationsのcreated_atが同一時刻になるため
   「proposed化の順序」は直接観測できない。提示順の検証はD-08日次上限を
   seedして「提示順の先頭行のみproposed化・残り2行はcandidate」で
   先頭特定する方式にした(ORDER BY句自体はunit試験のSQLピンが担保)
5. **integration `_seed_jev` は `status='pending'` の行のみ更新**: 試験7パートA
   のようにPATCHでversionを上げると同一ペアに新旧2行が存在するため、
   未評価行のみを対象とする絞り込みを入れた(対象行は常に1行)
6. lint指摘への機械的対応: `# noqa: F401(説明)` はruffに無効directive扱い
   されるため `# noqa: F401 — 説明` 形式へ修正(latch_calc.py)。ruff format・
   E501(B)の整形は毎コミット適用
