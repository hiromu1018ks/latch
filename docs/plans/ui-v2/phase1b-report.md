# UI v2移行 Phase 1B 実施報告 — API拡張(D-01・D-04・D-06・D-07・D-08)

- 日付: 2026-10-10
- 出典: [ui-v2-roadmap.md](../ui-v2-roadmap.md) Phase 1B / [phase0-decisions.md](phase0-decisions.md) 決定の要約(backend(P1B)欄) / 05 v0.6第5節
- 対象: backend(PostgreSQL参照実装)+ Cloudflare D1/Workers実装の両方
  (frontend/は不変更。呼び出し側はPhase 2〜4)
- 状態: 完了。決定表の「API追加」5件のみを実装し、表にないAPIは作っていない

## 実施内容(5件)

### D-01 有効期限の選択肢3択化(小)

- `intents/completion.py` `expires_at_candidates`: 4値(今夜23:30/明日12:00/
  明日23:30/3日後まで=now+72h)→**3値(今日22:00/今日23:59/明日5:00・JST暦日付)**へ変更。
  既定選択(`nearest_expires_at`=time_start+3hに最も近い選択可能候補)と
  過ぎた選択肢のselectable=false規則は現行のまま3値へ適用
- `intents/routes.py` `EXPIRY_LABELS`をプロトタイプv2のラベル
  (「今日 22:00 / 今日 23:59 / 明日 5:00」)へ変更
- 既定選択・切替の計算はcompletion.pyの単一実装のまま(新規計算ロジックなし・07 §2)

### D-04 participants_includes_owner(小)

- `intent_input.py` `StructuredIntentInput`へ`participants_includes_owner: bool | None`
  を追加(未知キーignoreから正規受付へ)
- `mapping.py` `_structured_data`: structured_dataへ格納。**未指定は既定true**。
  GET応答(`to_response_structured`・`StructuredIntentOut`)にも同キーを追加
  (02へ戻った際のseg復元用)。**キーを持たない既存行は読取時trueへ補完**
- `intent_input.py` `ParticipantsInput`の上限を4→**5**へ拡張。根拠: 決定表D-04
  「『含まない』の場合はクライアントがparticipantsの実効値を+1して
  (あなた+2〜4人=合計3〜5人)送信する」。従来のle=4だと「含まない+4人」が
  422になりD-04が実装できない。LATCHサイズ上限4はlatches.intent_idsのCHECKが
  担うため人数希望の保存値域とは独立(05 §2の該当行へ注記を追記)

### D-07 responses.reason_codes(見送り理由・小)

- `latches/schemas.py` `ResponseRequest`へ`reasons: list[time|place|size|mood] | None`
  を追加。**defer時のみ受理**(yes/noとの同時指定・値域外は422 VALIDATION_ERROR・
  Pydanticのmodel_validatorで検証)
- `latches/service.py` respond: defer+reasonsのとき回答itemへ`reason_codes`を付与。
  reasons無しのdefer・yes/noはキーなし(既存データ形状不変)。
  calibration_records.actual_responsesはresponsesと同形のため自動的に同形保持
- 契約試験3件(受理・yes同時422・値域外422)+統合試験(保存・calibration反映)を追加

### D-06 POST /v1/latches/{id}/response/undo(回答取り消し・中)

- `latches/routes.py`・`service.py`・`store.py`へ実装。応答はLatchEnvelope
  (undo後の`my_response: null`を含む・05 §5のとおり)
- 直列化: 回答APIと同一(FOR UPDATE+条件付きUPDATE・06 §6)。条件は
  status IN (proposed, partial_accept, rejected)・response_deadline/expires_at有効・
  参加Intent活性(NOT EXISTS)・取り消し前responses一致(スナップショットガード)
- **取り消し後の状態は残ったresponsesから再計算**(domain/latch_status.py
  `status_after_undo`を両実装共通の単一実装として新設): 他者のyesが残る=
  partial_accept・誰もyesを持たない=proposed。**他者のno/deferが残る場合は
  rejectedに戻れないため409 LATCH_CLOSED**(05 v0.6の列挙に含まれない運用細目
  として実装確定し、05 §5 undo節へ追記)
- **rejected確定時に作成済みのcalibration_recordsは取り消しと同一トランザクション
  で削除**。「レコードは回答確定時に作成」(05 §2)の規則を維持する。この処理が
  ないとundo→再回答→再確定でUNIQUE(latch_id)違反になり成立が503になる
  (統合試験test_21で検出し修正)
- エラー: 409 UNANSWERED(未回答)/ 409 LATCH_EXPIRED(回答期限・Intent期限経過)/
  409 LATCH_CLOSED(matched済み・競合クローズ後・他者のno/defer残り)/
  **404 NOT_FOUND(参加者以外)**。404は05 v0.6の規定(回答APIの403と扱いを
  分ける・attendanceと同じ開示方針)
- latch_status_eventsへ遷移(from→to)を記録。reason_codesは回答itemごと破棄
- D1版: `adapters/d1/atomic.py` `apply_response_undo`(marker+guard+journal構成は
  apply_responseと同一)・`latch_worker/svc_latches.py` `undo_response`

### D-08 POST /v1/latches/{id}/leave(参加取り消し・中)

- 応答: `{"latch": {"id", "status": "matched", "left": true}}`(新規LeaveEnvelope)
- 1トランザクションで次を確定(05 §5のとおり・statusはmatchedのまま):
  ① `messages`へシステム行挿入(sender_id=NULL・本文「{表示名}が参加を取り消しました」)
  ② `latches.left_user_ids`(新カラム・uuid[] DEFAULT '{}')へ追記 →
  以後そのユーザーの送信は409 CHAT_READONLY(残メンバーは継続して書込可)
  ③ `calibration_records.cancelled_after=true`(行なしは警告のみ・成立を落とさない)
- エラー: 409 ALREADY_LEFT(取り消し済み)/ 409 LATCH_CLOSED(matched以外・
  completed含む)/ 404(参加者以外)
- 解散しない・headcount再判定しない・参加Intentは待機に戻さない・プッシュ通知なし
  (残メンバーへの通知はシステム行のみ)——いずれも決定表D-08の最小構成どおり
- `MessageOut.sender_id`を`uuid.UUID | None`へ(sender_id=nullはシステム行のみ)
- D1版: `apply_leave`(left_user_ids snapshotガード+システム行+cancelled_afterを
  単一batch)+`svc_latches.leave`+`send_message`のleft判定

## スキーマ変更(両実装)

| 対象 | PostgreSQL(alembic 0009) | D1(cloudflare/migrations/0005) |
|---|---|---|
| latches.left_user_ids | `uuid[] NOT NULL DEFAULT '{}'` | `TEXT NOT NULL DEFAULT '[]'`(JSON配列) |
| messages.sender_id | DROP NOT NULL | SQLiteはALTER COLUMN不可のためテーブル再構築 |

- `migration/transform.py` `_ARRAY_COLUMNS.latches`へ`left_user_ids`を追加
  (cutoverリハーサルのdump変換でuuid[]がlistのままD1へ流れるのを防ぐ)
- D1の`chat_sendable`(未使用の判定ヘルパー)にもleft判定を追加し意味整合

## docs/05への追記(v0.6)

- §2: latches表へ`left_user_ids`行・messages行へsender_id NULL可(システム行)・
  intents表participants行へ上限5の根拠(D-04の+1換算)を追記
- §5: エラー列挙表へ**UNANSWERED・ALREADY_LEFT**を追記(undo/leave節に
  エラーとして規定済みだったが列挙表から漏れていた)。undo節へ実装確定細目
  (状態再計算・他者のno/defer残りはLATCH_CLOSED・calibration破棄・参加者404)、
  leave節へleft_user_idsの記述を反映
- §6: LATCH遷移表へundo行(partial_accept→proposed / rejected→proposed・
  partial_accept)を追記
- ヘッダ: v0.6変更点へPhase 1B実装完了(2026-10-10)を記録

## 試験証拠

### 単体(make test・毎コミット規律)

```
1579 passed, 582 deselected in 11.81s   (make test・ruff lintも全緑)
```

追加・更新した主な単体試験:
- intents: test_completion(3択期待値へ全面更新・6件)・test_expiry_options_routes
  (3選択肢・label・default_index)・test_mapping(participants_includes_ownerの
  3件・structured_data形状ピン更新)・test_intent_input(上限5へ追従)
- latches: test_latches_service(undo 4件・leave 2件・新規例外2件のcode/statusピン・
  status_after_undo純計算)・test_latches_routes(undo/leave/reasons 5件)・
  test_chat_service(脱退済み409・他メンバーは書込可)・test_rate_limit_wiring
  (新規2経路の保護ピン)

### 統合(ci常設API・実HTTP)

```
tests/integration/test_latches_api.py+test_expiry_options_api.py: 28 passed
tests/integration/test_intents_crud_api.py: test_24(P1B追加)passed
```

- test_18 defer+reasonsのresponses保存・calibration同形反映
- test_19 reasonsの検証(yes同時422・値域外422・無指定はキーなし)
- test_20 undoでproposedへ戻る・遷移記録・再回答可
- test_21 undoの分類全網羅(理由破棄・UNANSWERED・matched LATCH_CLOSED・
  非参加者404・期限切れLATCH_EXPIRED)
- test_22 leaveの全効果(システム行sender_id=null・自分409 CHAT_READONLY・
  相手201・cancelled_after=true・status matched維持・ALREADY_LEFT)
- test_23 leaveの分類(matched以外409・非参加者404・不在404)
- test_24 システム行本文の表示名検証
- ※ undo→再回答→matchedのUNIQUE衝突(503)をtest_21で検出し、calibration
  破棄の実装で解消(上記D-06節)

### 契約試験(両実装パリティ)

```
tests/contracts/test_api_parity.py     参照実装(PostgreSQL): 132 passed
tests/contracts/test_cloudflare_parity.py  D1実装(LocalD1Database・in-process):
                                        130 passed
```

- cases.pyへ13ケース追加(defer reasons受理/yes同時422/値域外422・undo成功/
  UNANSWERED/matched/非参加者・leave成功/ALREADY_LEFT/matched以外/非参加者・
  脱退後CHAT_READONLY・システム行sender_id=null・participants_includes_owner保存)
- baseline_manifest.yamlを再生成(schema_head 0008→0009)
- 並行注意: 参照実装側をworker稼働中に流すと、workerの評価書込みがharnessの
  teardown(FK削除順)と競合してteardownがFK違反になる。**worker停止が前提**
  (make test-ciと同じ条件・運用上の既知事項として記録)

### 全套(make test-ci・worker停止)

```
2160 passed, 1 skipped(EXIT=0)
```

- (cf-dev起動中はcloudflare_local probeが実行されて2160+1skip・停止時は
  2159+2skip。いずれもEXIT=0)
- tests/integration/test_geo・test_obs_schemaのheadピンを0008→0009へ追従
- tests/cloudflare(cutoverリハーサル含む)緑。0009のleft_user_idsに伴う
  transform.py追随なしではリハーサルが壊れるため前述の配列変換追加が必須だった

### miniflare実測(make cf-dev + cf-probe)

```
tests/cloudflare -m cloudflare_local: 1 passed(実pywrangler dev・8787)
```

- 実miniflare上でundo/leaveを含む契約コードはin-process localと同一の
  Workers実装(routes・svc_latches・atomic)を使用するため、上記
  test_cloudflare_parityのlocal合格が同一コードの検証になる。probeは
  実runtime(Python/datetime/JWT/D1/Queues/Cron)の実測

## 既存破損の修復(P1B外だが全套緑のために必要だった)

1. **backend lintの既存21件違反**(HEAD時点・前Phaseで実行されていなかった):
   ruff format+checkで解消(E501/F401/UP017/I001/F841)
2. **cutoverリハーサルのdump変換**: left_user_ids(uuid[])が_ARRAY_COLUMNS
   対象外でlistのままD1へbindされ4試験が503相当に(P1B起因の追随漏れとして修正)
3. **probe試験の既存ドリフト**(本日早朝のコミット3d05f4f起因):
   - /probe/jwks_fetchは本文検証を意図的に行わない設計(probes.py docstring)に
     変更済みだがtest_runtime_probeが旧挙動(kids検証)のまま→現行設計へ追従
   - wrangler.jsoncのLATCH_ENV="api"に対しtestが"local-probe"を期待→構成値へ追従
   - queue consumerからprobe受信記録(QUEUE_MESSAGES/probe_events)が除去され
     probeのqueue実測が必ず失敗→受信記録を復元(処理本体は変更なし)
4. **ci DBの残留テストデータ**(過去実行のteardown失敗分): FK順の一括削除で清掃。
   cutoverのverify(matched_intents_closed)はDB全体を読むため残留に敏感

## 決定表にないAPIについて

作成していない。実装したのはundo・leaveの2経路と既存経路への拡張
(expiry-options 3値化・structured_dataキー・reasons)のみ。ページネーション・
エラー形式・認可・Clock契約(Clock.now()はFOR UPDATE取得後に採取)の既存規律を
すべて踏襲。依存追加なし(backend/cloudflareともpackage・pyproject不変)

## Phase 2〜4への引き継ぎ

- 呼び出し側: `POST /v1/latches/{id}/response/undo`(body空)・
  `POST /v1/latches/{id}/leave`(body空)・defer時`reasons`は
  `{"response": "defer", "reasons": ["time", ...]}`・02のsegは
  `structured_intent.participants_includes_owner`(省略true)で送信・GETで復元される
- expiry-optionsは`options`配列が3要素になる(labels「今日 22:00/今日 23:59/
  明日 5:00」・default_indexは0〜2)
- undoはdefer(no)→rejectedからも戻せるが、**他者の見送りが残るLATCHは
  409 LATCH_CLOSED**になる(04のdeclined状態のundoボタンは相手未回答の
  一覧Cardでのみ有効)
