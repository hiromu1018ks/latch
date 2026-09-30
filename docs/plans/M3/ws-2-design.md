# M3 ws-2 設計 — バッチ群(expiry_sweeper・Intent期限切れ・catch-up統合・リセットジョブ)

作成: 2026-10-01(agent1)。参照仕様: 12 M3-3・M3-4 / 06 §6・§9〜§10 / 04 §5 / 05 §3・§6 / 09 D-09 / 02 §3

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

時間経由で状態を閉じるバッチ群を作り、提示〜回答〜成立〜完了の状態機械の「出口」を完結させる。

1. **latches期限切れバッチ(expiry_sweeper)**: proposed/partial_acceptの回答期限・Intent期限切れ、
   保留(candidate)の期限切れをexpiredへ遷移(FOR UPDATE SKIP LOCKED・60秒周期)
2. **Intent期限切れバッチ**(同一スケジューラ): draft/active/paused×expires_at経過をexpiredへ + expiredイベント発行
3. **catch-upスキャン**: M2 ws-6のReevalRunnerとして実装済み。本単位はexpiry_sweeperとの
   **同一スケジューラ統合**(単一ジョブ管理)のみを担う(§2.1)
4. **リセットジョブ**(M3-4): JST 0時日次・暦月初JST 0時月次。完了時に保留キュー再評価(§2.5〜2.6)
5. **matched→completed遷移**(ws-1設計§1.4の引継ぎ): 対象時刻経過のmatched行をcompletedへ +
   実施自己申告の通知先行書き込み(D-09)。sweeperと同一スケジューラが自然な担い手
6. **G1引継ぎ02#4の実確認**: 期限経過後のIntent expired遷移をci統合試験で実施(G1裁定(a)の履行)

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | latches期限切れバッチの実行者はMatching Worker上の定期ジョブ`latch_expiry_sweeper`。周期60秒・時刻源はアプリ層のClock.now()(DBのclock_timestamp()は使わない) | 06 §6 |
| 2 | 対象と遷移: `status IN ('proposed','partial_accept')`かつ`(response_deadline <= :now OR expires_at <= :now)` → expired。`status='candidate'`(保留キュー)かつ`expires_at <= :now` → expired | 06 §6 |
| 3 | 取得はFOR UPDATE SKIP LOCKEDで行い、回答APIと同一の遷移規則・直列化方式で処理する。バッチと回答APIが同一行で競合しても、UPDATE条件が同一の真実(期限とstatus)を参照するため不整合は生じない。期限切れバッチが遅延してstatusがまだproposedのままでも、期限直後の回答は受理されない(回答APIの条件付きUPDATEが構造的排除) | 06 §6 |
| 4 | Intent側の期限切れバッチ(idx_intents_expiresの対象)と同一のスケジューラで動かし、切替・停止は単一ジョブとして管理する。60秒はresponse_deadlineの表示精度(「あと◯分」)に対して十分な粒度 | 06 §6・03 §5 |
| 5 | Intent遷移: active・paused→expiredはexpires_at経過(時間バッチ・idx_intents_expiresの対象と一致)。draft→expiredは「active化されないまま期限が切れた下書き」の扱い(idx_intents_expiresはdraftを含む) | 05 §6 |
| 6 | idx_intents_expires: `CREATE INDEX ... ON intents (expires_at) WHERE status IN ('draft','active','paused')` — 部分索引。抽出SQLのWHEREを同条件に揃えると索引が効く | 05 §3 |
| 7 | catch-upスキャン(期限前スキャン)はexpiry_sweeperと同一の60秒周期スケジューラで、`status='active'`かつ`expires_atまで2時間以内`かつ`直近の詳細評価の実施から30分以上経過`のIntentをCandidate Retrievalから直接投入(**Eventを発行しない** — 同一versionのidempotencyキー衝突のため)。embedding IS NULLは対象外。60秒周期はexpiry_sweeperと同一スケジューラで動かし、追加のジョブ管理を発生させない | 06 §9 |
| 8 | catch-upの値の根拠: 2時間はIntent実効寿命の大半をカバーし「対象開始時刻まで75分」の通知打ち切り前に最低2回の再評価機会を保障。30分は頻度制限と同一周期で通常経路との二重評価を防ぐ | 06 §9 |
| 9 | リセットジョブ: 日次カウンタはJST 0時・月次カウンタは暦月初JST 0時の**同一ジョブ**でリセット(FR-50)。TTL方式はUTC基準で9時間ずれるため用いない。ジョブはClockインターフェースのJST日付境界を参照し、テスト環境ではClock操作で0時・月初を再現できる | 04 §5 |
| 10 | リセットジョブの失敗時は、最初のJev実行要求でカウンタの日付キーを検査し、前日以前のキーを検知した場合にリセット(自己修復)。ジョブの単一障害点を補う | 04 §5 |
| 11 | 0時リセットは日次カウンタのリセットジョブの**完了時に保留キュー再評価イベントを発行する**。既存提案のクローズの検知は、当該トランザクションのコミット時に保留キュー再評価イベントを発行する方式(イベントの内容はlatch_status_eventsへの挿入と同一トランザクションで確定するため、クローズの取りこぼしがない) | 06 §10 |
| 12 | キュー再評価の差分化(FR-03): ①直近評価以降に参加Intent更新(バージョン組変化)②latch_score/aggregate_scoreが0.05基準で変化③保留登録後に一度も再評価されていない — のいずれかを満たす候補のみ再評価。変化のない候補は提示順の再計算のみ(Jev再実行しない)。大量保留時は提示順(対象時刻昇順・Score降順)に沿って上限内の件数のみ処理し、残りは次のトリガーに委ねる | 06 §10 |
| 13 | latch_status_events: 遷移を書くトランザクション(回答API・競合クローズ・expiry_sweeper・Layer 5のproposed遷移)と同時に挿入。user_idは遷移の引き金となったユーザーで、システム起因(sweeper等)はNULL。保留キュー再評価トリガー(クローズ検知)の**観測点でもある** | 05 §2・06 §10 |
| 14 | 保留中のcandidateはexpiry_sweeperがexpires_at経過でexpiredにする。提示時(candidate→proposed遷移)に対象開始時刻まで75分を切っている候補は通知せず破棄(candidate→expired・ws-6実装済み) | 06 §10・05 §6 |
| 15 | matched→completed: 対象時刻(time_start)の経過。遷移時に実施自己申告の通知を送る。cancelled LATCH(解散済み)には申告通知を送らない | 05 §6 |
| 16 | D-09: completed遷移時点でプッシュ+アプリ内通知により1問自己申告「実際に会いましたか?」(はい/いいえ)・回答期限3日・スキップ可。無回答は欠測。通知の先行書き込みは`payload={latch_id}`最小参照・媒体はM3-5(M2 ws-6の承認構成) | 09 D-09・05 §2・M2 ws-6承認 |
| 17 | 通知上限D-08: 1ユーザー日6件・1Intent同時3件。日次上限の真実はnotifications(created_atの日付条件。0時リセットはday_start/day_nextの切替で成立 — カウンタリセットジョブ不要)。nearby存在通知も日次上限に含む | 03 D-08・06 §10・latch_engine実装 |
| 18 | Jevカウンタの鍵: `jev:daily:{yyyymmdd}`(TTL 48h)・`jev:monthly:{yyyymm}`(45日)ほか。TTLは掃除用に留めリセット表現には使わない(リセット=日付キー切替。JST 0時を跨ぐと新キーで0から始まる=自己修復を内包) | 04 §5・cost/store.py(M2 ws-4承認「リセットジョブ本体はM3-4」) |
| 19 | イベント種: event_typeはcreated/updated/deleted/expired/scheduled/embedding_completedの6値。UNIQUE(event_type, source_intent_id, payload内version)。削除・期限切れ・指定時刻Eventはdebounce統合せず即時処理。`EVENT_EXPIRED="expired"`はM1 ws-3で定義済み(コメント「発行経路はM3-3」) | 05 §2・06 §9・intents/events.py |
| 20 | 期限切れ・削除されたIntentを含むlatchesはsweeper(引用#2)と削除Event処理(ws-1実装)がそれぞれ閉じる。解散復帰(ws-1実装済み)でactiveへ戻ったIntentは次のBucket再評価・catch-upの対象 | 06 §6・05 §6 |
| 21 | 02#4: 「expires_atを指定して保存し、期限経過後の状態を確認する → Intentがexpiredに遷移」。時刻操作は10 §1(Clock)。G1裁定(a): M1は保存時検証まで実施済み・期限経過後expired遷移はM3-3(expiry_sweeper)実装時に確認 | 02 §3・STATUS G1 |
| 22 | expiry_sweeperがlatchesをexpiredにする際の通知: 05 §6のexpired行に通知規定なし。不成立の表示は画面側の一文統一(03 §7)で、API・通知は種別・理由を開示しない | 05 §6・03 §7 |
| 23 | 待機・周期のテスト再現: FakeClockのset()/advance()で期限・バッチ周期を決定的に再現。すべての時刻参照はClock経由(arch test強制) | 10 §1・C2 |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

| 資産 | 本単位からの使い方 |
|---|---|
| `worker/reeval.py` ReevalRunner | catch-up+30分Bucket再評価を実装済み(60秒・Event不発行の直接投入)。docstringに「M3-3がexpiry_sweeperを同一周期へ統合できるよう独立クラスにする」と明記。本単位は§2.1の統合点のみ変更 |
| `worker/backfill.py` BackfillRunner | 周期ジョブの定型(sleep-first・run_onceの例外はrun()が握り次周期回収・stop追従)。ResetJobも同型で書く |
| `worker/matching/latch_engine.py` | `_drain()`(保留キュー全体を提示順にtry_promote)をpublic化して§2.6の再評価実体として再利用。`_INSERT_LATCH_EVENT`・`_DRAIN_CANDIDATES`・try_promoteの直列化(FOR UPDATE+条件付きUPDATE)は参照実装。**評価経路のロジックは無変更** |
| `worker/cost/store.py` JevCostStore | キー体系(引用#18)。§2.5で掃除メソッドを追加(INCR系・scan_reportは無変更) |
| `worker/cost/guard.py` | `_day_bucket`/`_month_bucket`(Clock.jst_date()由来)。リセットジョブも同一導出を使う(guard無変更) |
| `core/clock.py` | `Clock.jst_date()`が既存(JST日付キー・リセットジョブ判定用のコメントつき)。次のJST 0時導出のみ本単位で追加 |
| `intents/events.py` | `EVENT_EXPIRED`定義済み。`insert_match_event(event_type='expired')`を期限切れtx内で呼ぶ(保存と同一トランザクションの慣行どおり) |
| `worker/stage1.py` | expiredイベントは既にEVENT_TYPESに含まれ「処理実体なし(processed)」としている。**無変更**(§2.3) |
| `latches/`(ws-1資産)・`worker/stage1.py`の削除Event処理 | 触らない(回答APIの直列化とクローズ資産。sweeperはworker側資産として作る — M2以降「Workerがlatches状態遷移を持つ」構成(latch_engine・stage1のclose_latches_on_delete)の延長) |
| alembic 0001〜0005 | 変更なし。latches.completed_at・latch_status_events・notificationsは0001で作成済み |

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

- FCM実送信・お知らせAPI・D-05回答期限の通知・D-08の通知面・attendance通知の媒体確定 → ws-3
  (本単位はnotifications先行書き込み〔type・payload={latch_id}〕まで。読み取り・送信はws-3)
- 実施自己申告の回答API(attendance・3日以内の受付・cancelledには通知しないの送信面) → ws-4
- 30日定期削除・calibration匿名化 → ws-6
- 02#16(期限切れ処理)のstaging E2E・#13〜#23 → ws-9。本単位は02#4の本体と#16相当の遷移単体をciで担保
- Workerの水平スケール(複数プロセスでのsweeper分散)。SKIP LOCKEDにより将来安全だが、
  本単位の試験範囲はWorker 1構成(06 §10 design §2.10-8と同一前提)

## 2. 実装方式の選択と推奨

### 2.1 スケジューラ統合形 — 推奨: ReevalRunnerへExpirySweeperを注入(単一ジョブ化)

06 §6(引用#4)はlatches sweeperとIntent期限切れバッチの、06 §9(引用#7)はcatch-upの「同一スケジューラ・単一ジョブ管理」を
要求する。catch-upの実体はReevalRunnerとして稼働済みのため、統合点の選択が最初の設計判断になる。

| 案 | 内容 | 判定 |
|---|---|---|
| **A(推奨)** | ReevalRunnerへオプション引数`sweeper`を注入(既定None)。`run_once()`の**先頭**で`sweeper.run_once()`を実行してからcatch-up/Bucket投入へ。worker/main.pyでExpirySweeperを構築して渡す | 既存試験(注入なし)は無傷。60秒tickの1本化で「切替・停止は単一ジョブ」を満たす。先頭実行により期限切れ確定がパイプラインの重さに後ろ倒しにならない |
| B | 独立taskとして並走(interval同一60秒) | 「同一スケジューラ」の文言から離れ、ジョブ管理が2系統になる。ただしsweeperだけ軽量に保てる利点はある |
| C | ReevalRunnerを改名拡張した新BatchRunnerへ全統合 | 既存のReevalRunner試験資産(test_worker_reeval・test_k_limits_e2e)の期待値を書き換える。統合の利益に対して変更が大きい |

**A案のトレードオフ(明記)**: run_onceは直列なので、catch-up/Bucket投入(最大batch_limit=50件のパイプライン)に
時間がかかると次tickまでの間隔が延び、期限切れ確定と表示の遅延が最大「tick処理時間+60秒」となる。
正しさへの影響はない(回答APIの条件付きUPDATEが期限後受理を構造排除・引用#3)。影響は期限表示の鮮度のみで、
イベント嵐のときはそもそもパイプライン全体が遅延しているため、ci環境・MVP規模では許容する。
阻害される場合は設定値(batch_limit)の調整で対処でき、案Bへの分割は構成変更なしで後から可能(sweeperが独立
クラスであるため)。

### 2.2 latches expiry_sweeper — 推奨: SKIP LOCKED一括抽出→行単位txの条件付きUPDATE

SQL(06 §6の対象条件をそのまま):

```sql
-- 対象抽出(1回のtxでロック取得。期限切れが古い順)
SELECT id, status FROM latches
 WHERE (status IN ('proposed','partial_accept')
        AND (response_deadline <= CAST(:now AS timestamptz)
             OR expires_at <= CAST(:now AS timestamptz)))
    OR (status = 'candidate' AND expires_at <= CAST(:now AS timestamptz))
 ORDER BY response_deadline, expires_at, id
 LIMIT :batch_limit
 FOR UPDATE SKIP LOCKED
```

行ごとに個別txで閉じる(1行の予期しない失敗が他行へ波及しない。失敗行は次tickで再捕獲):

```sql
-- proposed/partial_accept行(回答APIの_UPDATE_RESPONSEと同一の真実を再検査)
UPDATE latches SET status = 'expired'
 WHERE id = CAST(:latch_id AS uuid)
   AND status IN ('proposed','partial_accept')
   AND (response_deadline <= CAST(:now AS timestamptz) OR expires_at <= CAST(:now AS timestamptz))
 RETURNING id
-- candidate行(期限のみ。response_deadlineは暫定値なので判定に使わない・引用#2)
UPDATE latches SET status = 'expired'
 WHERE id = CAST(:latch_id AS uuid)
   AND status = 'candidate'
   AND expires_at <= CAST(:now AS timestamptz)
 RETURNING id
-- 影響行数=1のとき同一txで:
INSERT INTO latch_status_events (latch_id, from_status, to_status, user_id, created_at)
 VALUES (:id, :from_status, 'expired', NULL, :now)   -- システム起因=引用#13
```

- `:now`は抽出・UPDATEとも**同一のClock.now()値**(1tick=1時刻。抽出と実行の間に期限が動かない)
- SKIP LOCKEDの効果: 回答APIのFOR UPDATEと同一行で競合した場合、回答側が先なら行は変わっており
  条件付きUPDATEの影響行数0で無視(次回に該当なし)。sweeper側が先なら回答APIの409分岐が正しく握る。
  いずれも不整合なし(引用#3)
- 対象はnearby候補(score < 閾値のcandidate行)を含む。06 §6の条件にscoreはなく、保留の実体は
  status='candidate'の行すべてのため(期限で閉じるのが正しい)
- **Calibrationレコードは作らない**(回答によらない閉鎖。ws-1設計§2.5案Bと同じ理由)
- **notificationsは作らない**(引用#22)

### 2.3 Intent期限切れバッチ — 推奨: バッチtx内でexpired化+expiredイベント発行(同一tx)

```sql
-- 対象抽出(idx_intents_expiresの部分索引条件と同一・引用#5/#6)
SELECT id, status FROM intents
 WHERE status IN ('draft','active','paused')
   AND expires_at <= CAST(:now AS timestamptz)
 ORDER BY expires_at, id
 LIMIT :batch_limit
 FOR UPDATE SKIP LOCKED
```

行単位tx:

```sql
UPDATE intents SET status = 'expired', updated_at = :now
 WHERE id = CAST(:intent_id AS uuid)
   AND status IN ('draft','active','paused')
   AND expires_at <= CAST(:now AS timestamptz)
 RETURNING version
-- 影響行数=1のとき同一txで expiredイベント発行(引用#19・保存と同一txの慣行)
insert_match_event(conn, event_type='expired', intent_id=..., version=RETURNING値, now=...)
```

- **expiredイベントを発行する**: `EVENT_EXPIRED`が「発行経路はM3-3」と定義済みで、event_type 6値の正規の
  一員(引用#19)。Stage1は既に「処理実体なし(processed)」として受理し、削除Eventのような波及処理は
  前提としていない(stage1無変更)。イベントの意義は監査と将来のscheduled経路との対称性
- **match_candidates/group_candidatesをclosed化しない**: 候補無効化の明示規定は削除Eventのみ(06 §1)。
  期限切れIntentはLayer 1/2の対象外(status≠active)で新しい候補生成に出ず、既存ペアもH再検証で落ちる。
  latches側はsweeperのcandidate行expires_at条件(§2.2)が閉じる(latches.expires_at=参加Intentの
  expires_at最小値・05 §2)
- draftも対象(引用#5)。draft→expiredに通知・イベント以外の波及なし(下書きはEventを1回も発行していないが、
  expiredイベントのidempotencyキー(expired, id, version)は常に空いている)
- paused→expiredも対象(05 §6「active・paused→expired」)。resume Eventと競合してもversion検査で古い方は
  破棄され、status検査(WHERE status IN (...))が二重遷移を防ぐ

### 2.4 matched→completed遷移 — 推奨: 同一sweeperの第3処理+attendance通知先行書き込み

ws-1設計§1.4が「sweeperと同一スケジューラの定期ジョブが自然な担い手」と指定。対象時刻は
`max(参加Intentのtime_start)`(05 §2の一覧SQL・latch_engineの_DRAIN_CANDIDATESと同型の導出)。

```sql
-- 対象抽出(対象時刻昇順)
SELECT l.id FROM latches l
 WHERE l.status = 'matched'
   AND (SELECT max(i.time_start) FROM intents i WHERE i.id = ANY(l.intent_ids)) <= CAST(:now AS timestamptz)
 ORDER BY (SELECT max(i.time_start) FROM intents i WHERE i.id = ANY(l.intent_ids)), l.id
 LIMIT :batch_limit
 FOR UPDATE SKIP LOCKED
```

行単位tx:

```sql
UPDATE latches SET status = 'completed', completed_at = CAST(:now AS timestamptz)
 WHERE id = CAST(:latch_id AS uuid) AND status = 'matched'
 RETURNING intent_ids
-- 影響行数=1のとき同一txで:
-- 1) latch_status_events(from='matched', to='completed', user_id=NULL)
-- 2) 実施自己申告の通知先行書き込み(引用#15/#16): 参加者全員へ
INSERT INTO notifications (user_id, type, payload, created_at)
 VALUES (:user_id, 'attendance_request', CAST(:payload AS jsonb), :now)
 -- payload = {"latch_id": "..."}(最小参照・M2 ws-6承認構成)
```

- `type='attendance_request'`は**実装定義の新規type値**(既存'proposal'/'nearby_candidate'と同列。
  docsにtype値の確定一覧はなく、先行書き込みの慣行に従う。§5承認事項2)
- cancelledへ解散済みの行はstatus='matched'でなく対象外=D-09「cancelled LATCHには送らない」を構造的に満たす
- completed_at列は0001に存在(マイグレーション追加なし)

### 2.5 リセットジョブ(ResetJob)— 推奨: 独立task「次のJST 0時までsleep」+掃除+drain

04 §5(引用#9/#10)の「スケジューラで起動するリセットジョブ(0時JSTのキック。月次は月初0時も判定)」に従い、
60秒系とは別周期の独立taskとしてWorkerへ載せる(backfill・reevalと並ぶ3つ目の定期task)。

```text
run():
  loop:
    next_midnight = 次のJST 0時(Clock.now()から導出・sleepは注入可能)
    await sleep((next_midnight - now).total_seconds())
    now = Clock.now()  ← 起動時刻を再取得(0時丁度でなく数秒遅れで発火してよい)
    jst_today = now.jst_date()
    前日キー掃除(JevCostStore新メソッド):
        DEL jev:daily:{(jst_today-1日).yyyymmdd}
        SCAN+DEL jev:exec:{前日}:* / jev:intent:*:{前日} / jev:user:*:{前日}
        jst_today.day == 1 なら DEL jev:monthly:{前月.yyyymm}   ← 月次判定(引用#9)
    await latch_engine.drain()   ← 完了時に保留キュー再評価(引用#11・§2.6)
    例外時はログ+短い待機(retry_interval_sec 既定300秒)で再試行(翌0時まで放置しない。
        機能的リセットは日付キー切替で常時担保済みのため、失敗の実害はdrain遅延のみ)
```

- **掃除の位置づけ**: 機能的なリセット(日次30,000/月次600,000の上限復帰)は日付キー切替で0時を跨いだ瞬間に
  成立している(INCRが当日キーを作る・引用#18)。D-08日次6件もnotificationsの日付条件切替で同期(引用#17)。
  ジョブのDELは「リセットの実行」の実体として旧キーを即時解放する(TTL 48h/45日を待たない。
  月次キーが45日残留するのを防ぐ意味が最も大きい)
- **起動時の遡及はしない**: Worker起動が0時N分で当日分を取り逃した場合でも、カウンタは自己修復済み・
  drainは次の評価経路(handle末尾)で代替されるため、初回は次の0時を待つ
- Clock操作によるテスト再現(引用#9): FakeClockを0時直前(23:59 JST)にsetして1周期.sleepを短くすることで
  0時跨ぎ・月初(月末日23:59→1日0時)を決定的に起こす

### 2.6 保留キュー再評価イベントの実体 — 推奨: latch_engine.drain()の直接実行(イベント発行しない)

引用#11は「再評価イベント」を2箇所で要求するが、実体は異なる:

- **クローズ検知側**: 「イベントの内容はlatch_status_eventsへの挿入と同一トランザクションで確定する」とあり、
  イベントの実体=**latch_status_events挿入そのもの**(観測点・引用#13)。ws-1により全クローズ経路
  (回答API・競合クローズ・削除Event処理)が同tx挿入を実装済みで、取りこぼしのない観測点は既に存在する
- **0時リセット側**: 対応する実体の規定がない。match_eventsへ新種を発行する案は成立しない
  (event_typeは6値固定・UNIQUEキーがsource_intent_id起点でキュー全体の再評価に合わない)。
  catch-upスキャンと同じ理由(引用#7「Event発行方式だと同一versionのidempotencyキー衝突」)で、
  **直接実行**が仕様の意図に適う

よって0時リセット完了時の再評価は `latch_engine.drain()` のジョブ起点での1回実行とする。
`_drain()`(private)を`drain()`へpublic化するのみで、中身は再利用:

- `_DRAIN_CANDIDATES`: 保留全体を提示順(対象時刻昇順・score降順・同点id昇順)に抽出。
  期限切れ(score条件つき)・nearby行(score < 閾値)は対象外
- 各行`try_promote`: FOR UPDATE→D-08両上限検査(notifications当日カウント=0時後は0から。
  同時3件はクローズ済み分が空く)→D-07抑制→75分切れ破棄→candidate→proposed(D-05再計算)
  →通知先行書き込み→latch_status_events
- 差分化(引用#12)との関係: drainは「変化のない候補は提示順の再計算のみ」に正確に対応する(Jevを呼ばない)。
  Jev再評価が必要な候補(バージョン組変化・スコア0.05変化・保留後未再評価)は既存の評価経路
  (Event・Bucket・catch-up)がmatch_candidatesを更新したうえでhandle末尾の_drainを回すため、
  0時リセット側で改めてパイプライン投入はしない。上限内の件数のみ処理し残りは次トリガーへ、
  もtry_promoteの行単位上限判定が自然に満たす

### 2.7 クローズ検知後のdrain(06 §10クローズ側の消費実装)— 推奨: sweeper tick内の観測

観測点(latch_status_events)は揃ったが、クローズを観測してdrainを回す実行者が現状ない(回答API・
stage1はdrainを呼ばない。評価経路の_drainは次のパイプライン実行まで走らない)。同時3件の枠回復が
次の評価まで遅れると、保留候補の提示が不当に遅れる。

```text
ExpirySweeper.run_once の第4処理(1〜3の書き込みのあと):
  closed = SELECT 1 FROM latch_status_events
            WHERE created_at > CAST(:last_tick AS timestamptz)
              AND to_status IN ('rejected','expired','cancelled','matched')
            LIMIT 1
  if closed is not None: await latch_engine.drain()
  last_tick = now   ← メモリ保持(初回=起動時刻。起動前のクローズは既存評価経路のdrainが回済み)
```

- 期限切れ処理(§2.2)自身が書いたexpiredイベントも観測する=期限切れで空いた枠を同じtickで埋める。
  順序(書き込み→観測→drain)がこれを可能にする
- latch_status_eventsへのIndexは作らない(created_at範囲+to_statusの1行存在検査。latch_status_eventsは
  遷移時のみ書き込まれ、60秒窓の走査対象は常に僅か。MVP規模でseq走査でも十分小さい)
- **これは06 §10のクローズ側トリガーの実装完結**であり、単位表の文言にはない。§5承認事項3として
  supervisorの判断を仰ぐ(外す場合: drainは評価経路・0時リセットのみで、クローズ後の枠回復反映は
  次の評価経路まで最大数分〜遅れる)

### 2.8 採用しないもの(YAGNIによる切り捨て一覧)

| 切り捨て | 理由 |
|---|---|
| match_eventsへの「保留キュー再評価」イベント種の新設 | event_type 6値固定(引用#19)・idempotencyキー構造に合わない。latch_status_eventsが観測点(引用#13) |
| 期限切れIntentのmatch_candidates/group_candidatesをclosed化 | 明示規定は削除Eventのみ(06 §1)。レイヤー対象外とH再検証で自然に無力化(§2.3) |
| sweeper用のlatch_status_events Index | 60秒窓の存在検査は僅か行数。M4の性能試験で必要になったら追加 |
| リセットジョブの遡及実行(起動時に当日0時分を取り戻す) | カウンタは自己修復済み・drainは評価経路で代替(§2.5) |
| DBベースのジョブ実行履歴(前回実行時刻の永続化) | last_tickはメモリで十分(取りこぼしの害が§2.7の初回扱いで消える) |
| completed遷移の際の Intent 側追加遷移 | 05 §6にmatched→completedに伴うIntent遷移の規定なし(Intentはmatchedのまま) |
| 強制的なfulfillment(event发行後のStage1波及) | Stage1はexpiredを処理実体なし(processed)として設計済み。波及はsweeper側が完結 |

## 3. ファイル構成

### 3.1 作るもの(新規ファイル)

| ファイル | 内容 |
|---|---|
| `backend/src/latch/worker/sweeper.py` | ExpirySweeper(latches期限切れ・Intent期限切れ・completed遷移・クローズ検知drain)。engine・clock・latch(LatchEngine)・batch_limitを注入。run_once()はpublic(test・integrationから直接呼ぶ・ReevalRunnerと同型) |
| `backend/src/latch/worker/reset.py` | ResetJob(次JST 0時sleep→前日キー掃除+月初月次→drain→リトライ)。engine不要(cost_store・clock・latch・sleep注入)。次JST 0時導出ヘルパ`next_jst_midnight(now)` |
| `backend/tests/unit/worker/test_sweeper.py` | §4.1 |
| `backend/tests/unit/worker/test_reset_job.py` | §4.1(basename一意性: 既存配下に同一名なしを確認済み) |
| `backend/tests/integration/test_expiry_batches.py` | §4.2(02#4本体を含む) |

※unitはtests/unit/worker/へ置く(既存test_worker_*.pyはtests/unit直下だが、latches/・cost/の前例にならい
ディレクトリを分ける。__init__.pyなしのprepend mode運用のため、tests配下全体でのbasename一意性は
find+uniq -dで計画書段階と検証段階の両方で機械確認する)

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更 |
|---|---|
| `worker/reeval.py` | `__init__`へ`sweeper=None`オプション追加。`run_once()`先頭で`sweeper.run_once()`(Noneなら従動作)。既存試験は注入なしで無傷 |
| `worker/main.py` | ExpirySweeper構築(既存engine・clock・self._latchを渡す)→ReevalRunnerへ注入。ResetJob構築(掃除用のJevCostStoreは既存redis接続から別インスタンスで構築)→定期task追加(graceful shutdownのawait追加) |
| `worker/matching/latch_engine.py` | `_drain()`→`drain()`へpublic化(エイリアス。`_drain`は残さず呼び箇所2箇所を置換 — 試験が参照していないかはピン試験追随访問で確認) |
| `worker/cost/store.py` | 掃除メソッド追加(`delete_daily(day)`・`delete_monthly(month)`・`scan_delete(pattern)`)。INCR系・scan_report無変更 |
| `settings.py` | `sweeper_batch_limit: int = 50`・`reset_retry_sec: int = 300` 追加(既存の機械ピン試験test_settings.py・test_llm_factory.pyへの影響を追随访問で確認) |

### 3.3 触らないもの(明示)

- `latches/`(ws-1の回答API・store・service・calibration)— sweeperはworker側資産
- `worker/stage1.py`(expiredイベントは処理実体なしのまま受理)
- `worker/matching/group_engine.py`・layer1〜4・runner・origin・jev — 評価経路無変更
- `worker/cost/guard.py`(日付キー導出は現状利用)
- `alembic/`(マイグレーション追加なし)
- `intents/events.py`(EVENT_EXPIRED・insert_match_eventをそのまま使用)

## 4. テスト方針

### 4.1 unit(`make test`。スタブで決定的)

既存のworker系unit試験(test_worker_reeval.py等)のパターン(スタブengine/conn・FakeClock・SQL定数の
バイトピンは必要なもののみ)に準拠:

1. **対象抽出SQL・条件付きUPDATEのピン**: §2.2〜2.4のSQL文字列が対象条件(status・期限の組合せ・
   SKIP LOCKED句・LIMIT)を正しく含むか(compile検査・LAYER1_WHEREバイト同一と同型の規律)
2. **遷移の分岐**: proposed×response_deadline切れ/expires_at切れ・candidate×expires_at切れ・
   期限前(対象外)・draft/active/paused×期限切れ・matched×time_start経過/未経過
3. **events・通知の同時挿入**: latch_status_events(from/to/user_id=NULL)・attendance_request通知
   (参加者全員分)・expiredイベント(insert_match_event呼び出しの引数)
4. **next_jst_midnight**: JST 0時直前・直後・月末(翌日0時)・12月→1月の年跨ぎ
5. **ResetJobの日付判定**: 前日キー生成・月初判定(day==1で月次キー)・掃除メソッドの呼び出し
   (JevCostStoreはスタブ)
6. **クローズ検知drain**: 観測SELECTの呼び出し・クローズあり→drain呼出・なし→呼ばない・last_tick更新
7. **例外は握らない**(run_onceから伝播しRunner側で握る — Backfill/Reevalと同一契約)
8. **arch test**: 時刻参照がClock経由であることをtest_arch_no_direct_time.pyが既定で強制(新規コードも従う)

### 4.2 integration(`make test-ci`。compose常設DB・実Redis・in-process実行)

実DB・実Redis・FakeClock(Worker構築へ注入)で、runnerのrun_onceを直接呼ぶ(test_k_limits_e2e.pyの
ReevalRunner扱いと同型)。**期限切れの再現はDB値操作とFakeClock.advanceの併用**(M3 ws-1計画§9-2の
規律 — API経由では保存できない過去時刻をfixtureで直書き):

1. **02#4本体(G1引継ぎ)**: 有効期限つきIntentをAPIで保存 → expires_atを過去へDB直書き
   (または期限1分前を設定してFakeClock.advance) → sweeper実行 → `status='expired'`を検証。
   match_eventsへexpiredイベントが発行されていること
2. **latches期限切れ群**: ①proposed×response_deadline経過→expired ②proposed×expires_at経過→expired
   ③candidate(保留)×expires_at経過→expired(nearby候補のcandidate行も含む) ④期限前は対象外(不変)
   ⑤期限切れ直後の回答APIが409 LATCH_EXPIRED(sweeper遅延時も受理されない・引用#3の実確認)
3. **draft→expired**: draft×期限切れ(下書きも対象・05 §6)
4. **matched→completed**: time_start経過のmatched行→completed+completed_at+latch_status_events+
   attendance_request通知(参加者全員分)。cancelled(解散済み)には通知なし
5. **SKIP LOCKEDの並行性(簡易)**: 2つのrun_onceを同時起動し、同一行が二重に遷移しない
   (latch_status_eventsのexpired遷移が1行だけ)
6. **クローズ検知drain**: D-08同時3件上限で保留(candidate)になった行を仕込み、参加提案の1つを
   回答APIでrejected → sweeper実行 → 保留行がproposedへ昇格(response_deadline再計算・通知)
7. **catch-up統合の回帰**: ReevalRunner+sweeper注入構成でrun_onceが両方を実行(期限切れ処理→catch-up
   抽出の順)。既存test_k_limits_e2eは注入なし構成で無傷であること
8. **リセットジョブ**: FakeClock 23:59 JSTにset→sleep短縮→0時跨ぎ→前日キーDEL(実Redisでキーを仕込み
   消滅確認)・drain呼出。月末23:59→1日0時で月次キーも消滅。月初以外では月次キー残存。
   D-08回復: 前日に通知6件到達のユーザーの保留candidateが0時跨ぎ後のdrainで提示される
9. **teardown**: m3ws2-プレフィックスでuser/intent/latch等をFK逆順完全削除(M3 ws-1の対抗策規律。
   latch_status_events・notificationsのFKを忘れない — M2 ws-8の教訓)

### 4.3 検証手順(報告書への明記用)

1. `docker compose build api worker` → `make test-ci`(api再ビルド必須の運用ルール4)
2. `make test`(unit)・`ruff check`(test-ciにlint非含込のためマージ後lint再実行 — M2 ws-4運用メモ)
3. basename一意: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
4. alembic head=0005不変(マイグレーション追加なし)
5. 残存ゼロ確認(m3ws2-・latch_status_events・notifications)

## 5. 未解決の論点(supervisor承認事項・G3時確認候補)

1. **「保留キュー再評価イベント」の実体=drain直接実行(イベント不発行)**(§2.6)。
   06 §10はイベント発行を文言上要求するが、クローズ側の実体はlatch_status_events挿入(05 §2「観測点」)と
   読める一方、0時側の実体規定がない。catch-upの前例(06 §9 Event不発行)と同一の判断で直接実行とする。
   G3時確認事項へ記録候補
2. **attendance通知のtype値`'attendance_request'`は実装定義**(§2.4)。docsにnotifications.typeの確定値一覧は
   なく、先行書き込み(payload={latch_id})の慣行に従う。媒体・読み取りはws-3
3. **クローズ検知後のdrainをsweeper tickに含める**(§2.7)。06 §10クローズ側トリガーの消費実装だが
   単位表の文言にない範囲。外す場合はdrainは評価経路と0時リセットのみで、同時3件枠の回復反映が
   次の評価経路まで遅れる(最大数分)。**含めることを推奨**(実装は1 SELECT+条件付きdrainで小さい)
4. **統合スケジューラの遅延トレードオフ**(§2.1)。重いcatch-up/Bucket tickで期限切れ確定が最大
   「tick処理時間+60秒」遅延しうる。正しさへの影響なし(引用#3)。許容するか、案B(独立task)へ分割するか
5. **Intent期限切れ時にmatch_candidates/group_candidatesをclosed化しない**(§2.3)。明示規定は削除Eventのみ。
   レイヤー対象外とH再検証で自然無力化のため実害なし、という読み。G3時確認事項へ記録候補

以上。設計判断に保留はない(各論点に推奨を明示済み)。コミットはスーパーバイザーが行う。
