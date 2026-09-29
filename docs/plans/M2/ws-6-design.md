# M2 ws-6(Layer 5 LATCH Engine)設計メモ

- 作成: 2026-09-29(agent1)
- 前提: M2 ws-1〜ws-5 マージ済み(最終 d18efb7+207c59e・マージ後main test-ci 926 passed)。実行wave上の後続はws-7(ws-6依存)で並走なし
- 参照仕様: 06 §6・§9〜§10 / 03 D-05・D-07・D-08 / 05 §2・§6 / 08 §2.2〜§2.3・D-11 / 04 §5 / 02 §4(#8・#12) / 01 §16
- 本単位は外部SDKを扱わない(純計算+DB+Redis)。context7確認は不要(スーパーバイザー追記2)
- ws-5からの引継ぎ(ws-5-report.md引継ぎ節・スーパーバイザー追記1): JevWorker.handle(intent_id) は
  起点非依存IF。30分Bucket再評価・catch-upスキャンから同一部品を呼び、reevalガードは入口で共用。
  latch_score計算・保留キューは本単位の担い

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

6つの部品を実装する。

1. **LatchEngine(Layer 5本体)**: latch_score計算(L = H × MutualScore × C・MutualScore=min)・
   閾値0.80判定・latches行(candidate)生成・proposal生成(visibility分岐)・D-07再提案制御
2. **提示制御**: D-08上限検査(日6件/ユーザー・同時3件/Intent)・candidate→proposed遷移・
   提示時D-05式再計算・75分ルール・latch_status_events記録
3. **nearby_also存在通知・muted通知抑制**: notificationsテーブルへの通知記録
   (FCM・お知らせUIはM3-5。本単位はレコード生成まで)
4. **保留キューdrain**: 提示順(対象時刻昇順・Score降順)での提示
5. **再評価経路**: 30分Bucket再評価+catch-upスキャン(60秒周期・直接投入)。
   ws-1設計§1.4-3の委譲事項(scheduled Event発行主体)への回答を含む(§2.8)
6. **Worker配線**: `_kick_jev` 内 JevWorker→LatchEngine 直列実行・ReevalRunner DI

マイグレーションは **0004を1本追加**(latches部分UNIQUE索引 — §2.3。supervisor承認事項)。
依存追加なし。実行waveはws-6が単独のため共有ci-dbのalembic_version取り合いは計画上なし
(運用ルール1・2は報告書へ明記)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | スコア計算は01 §13の定義をそのまま実装: `L = H × MutualScore × C`・`MutualScore = min(would_a_accept_b, would_b_accept_a)`・HはLayer 1判定時点の再検証(通過=1・不成立=0)・Cは初期値1 | 06 §6 |
| 2 | L >= 0.80(D-01)なら提案候補。提案化の前にD-08上限(1ユーザー日6件・1Intent同時3件)を検査し、上限内なら直ちにproposedへ遷移させて通知。超過分は保留キュー(latches.status=candidate)へ。閾値超過の候補は上限検査の結果にかかわらずlatches行をcandidateとして作成する | 06 §6・§10 |
| 3 | 保留キューの提示順は対象時刻昇順・タイブレークLATCH Score降順。提示時(candidate→proposed遷移)には提示時点でD-05の式を再計算しresponse_deadlineを上書きする。candidate作成時のresponse_deadlineは保留登録時点の暫定値であり提示判定には使わない | 06 §10・03 D-08 |
| 4 | D-05式: `回答期限 = min( max( 通知時刻+15分, min(通知時刻+2時間, 対象開始時刻−60分) ), 参加Intentのexpires_atの最小値 )`。導出した期限が通知時刻を過ぎない場合は通知しない(75分ルールと同一扱い)。対象時刻まで75分を切った提案は通知自体を行わない | 03 D-05・§5 |
| 5 | 提示対象を取り出した時点で対象開始時刻まで75分を切っている候補は通知せず破棄する(candidate→expired)。保留中のcandidateのexpires_at経過によるexpiredはexpiry_sweeper(M3-3)の担当 | 06 §10・06 §6 |
| 6 | D-07: 見送り(defer)後の同一Intent組み合わせへの再提案は (a)見送りから min(24時間, 対象開始時刻までの残時間の半分) の経過 (b)スコア変化(またはどちらかのIntent更新による新候補) の**両方**を満たす場合に限る。no回答が存在する場合はIntent期限まで再提案しない。回答種別(no/defer)はlatches.responsesに区別保持 | 03 D-07 |
| 7 | D-07スコア変化の判定手順: 評価トランザクション内で同一バージョン組の現行match_candidates行をFOR UPDATEで読み、現行latch_score・updated_atをprev_latch_score・prev_evaluated_atへ退避してから新評価値でUPDATE。prev_latch_scoreがNULL(初回評価)なら無条件に変化あり。NULLでなければ \|新−prev\| ≧ 0.05 を変化あり。評価世代(バージョン組)そのものの変化も条件(b)を満たす(新バージョン組はprev値を持たず無条件に変化あり) | 06 §10 |
| 8 | D-08: 1ユーザーあたり提案通知は日6件まで(0時リセット)・1Intentあたり同時進行(proposed)の提案は3件まで。上限超過の候補は破棄せず保留し、既存提案のクローズまたはリセット後に提示順で提示。保留中に候補側の対象時刻が来たものはexpired | 03 D-08 |
| 9 | nearby_alsoが指定されたIntentについては閾値未満の候補の発生時にその存在の通知のみを送る(条件サマリ・一致度・相手情報は含めない)。当該候補はlatches.status=candidateのまま保持し(proposed遷移しない)、閾値超過時または後続の再評価で通常の提案判定に回る。存在通知もD-08の日次上限(日6件/ユーザー)に含める。同時進行上限(同時3件)はproposed数の上限であるためcandidateのままの存在通知には適用しない | 06 §6・03 §3 |
| 10 | mutedのIntentは提案通知を送らない。それ以外の挙動(proposed遷移・保留キュー・回答期限・競合クローズ)は通常どおりで、latches.statusはproposedへ遷移する。muted提案もproposed遷移であるためMutual Latch Rateの分母(proposed遷移カウント)に含まれる | 06 §6・09 §2.1 |
| 11 | proposal(05 §2正式構造)の生成はLayer 5。フィールド: time_summary・area_name・headcount・category_primary・category_secondary・budget・match_level。格納禁止はraw_text・soft/NG条件の文言・座標(08 §2.3・D-11)。文言はクライアント/Layer 5テンプレートが構造から組み立てる | 05 §2・08 §2.3 |
| 12 | visibility生成分岐: summary_onlyでは全フィールド生成。hidden_until_matchを含む候補では **headcountとmatch_levelのみ** 格納(time_summary・area_name・category_primary・category_secondary・budgetは格納しない)。双方のvisibilityが異なる場合はより厳しい方(いずれかがhidden_until_matchならhidden扱い)を優先 | 05 §2 |
| 13 | match_level: high(0.90以上)/ medium(0.80以上0.90未満)/ low(提案閾値以上0.80未満。下端は運用中の提案閾値に連動し未定義区間を生じさせない)。内部スコア生値は格納しない | 05 §2 |
| 14 | area_nameはgeo_centerを約1kmグリッドへ丸めた代表点の地物名(逆転ジオコーディング・D-11)。変換結果の地域名のみproposalへ格納(座標は格納しない)。実装はgeo/service.py `reverse_geocode(lon, lat)`(M0 ws-4実装済み・PostGIS完結) | 08 D-11・04 §3 |
| 15 | latches: intent_ids(uuid[]・2≦長さ≦4)・proposal(jsonb NOT NULL)・score(提示時)・responses(jsonb DEFAULT '[]')・status(candidate/proposed/…・candidateは保留キューの行)・response_deadline(NOT NULL)・expires_at(参加Intentのexpires_at最小値)・created_at/completed_at | 05 §2 |
| 16 | latch_status_events: 遷移を書くトランザクション(回答API・競合クローズ・expiry_sweeper・**Layer 5のproposed遷移**)と同時に挿入。from_statusはcandidate作成時NULL可。user_idは遷移の引き金となったユーザーで、システム起因(Layer 5)はNULL。09 §2.1の集計(proposed=分母・matched=分子)の供給源 | 05 §2・06 §10 |
| 17 | 再評価差分化: キューの再評価時に再評価するのは (1)参加Intentの更新(バージョン組変化)あり (2)latch_scoreが0.05基準で変化 (3)保留登録後に一度も再評価されていない — のいずれかを満たす候補のみ。変化のない候補は提示順の再計算のみ(再評価経路のたびにdrainを試みる)。大量保留時は提示順に沿って上限内の件数のみ処理し残りは次トリガーへ | 06 §10 |
| 18 | 30分Bucket再評価: 到来したBucketに属するIntentのみをCandidate Retrievalから再実行。起点のembedding IS NULLなら当該Bucketをスキップ。Bucket境界はClockインターフェース経由 | 06 §9・04 §5 |
| 19 | catch-upスキャン: expiry_sweeperと同一の60秒周期スケジューラで `status='active'` かつ `expires_atまで2時間以内` かつ `直近の詳細評価の実施から30分以上経過` のIntentを抽出し、Candidate Retrievalからパイプラインを**直接投入**(Eventを発行しない — 同一versionのidempotencyキー衝突のため)。embedding IS NULLは対象外 | 06 §9 |
| 20 | 再評価頻度の頻度制限(同一Intent更新由来)はreeval:{intent_id} TTL 30分のガードで判定。Bucket再評価・catch-upも同一部品を再利用(ws-4資産・引継ぎどおり入口で共用) | 06 §5・ws-4資産 |
| 21 | match_candidates: UNIQUE(intent_a_id, intent_b_id, intent_a_version, intent_b_version)。prev_latch_score・prev_evaluated_at列あり。同一バージョン内の再評価は既存レコードを更新。latch_score列(L=H×MutualScore×C)あり | 05 §2 |
| 22 | 競合制御(FR-08)の条件付きUPDATE・FOR UPDATE方式・Clock.now()基準(:nowはDBのclock_timestamp()を使わない)。回答API本体はM3だが、Layer 5の遷移も同一の直列化方式(WHEREにstatus条件)で書く | 06 §6・10 §1 |
| 23 | 通知手段(FCM・アプリ内通知)・D-08上限の「通知送信」の媒体はM3-5。02#13(通知到達記録)はM3の検証。M2のws-6はlatches遷移と通知記録(notifications行)まで | 12 M3-5・02 §4 |
| 24 | 02#8(時間イベントで再評価できる)= 指定時刻をまたぐ2つのactive Intentを配置し時刻到来による候補生成の記録。Bucket再評価・catch-up経路のE2E検証対象 | 02 §4 |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

- `worker/jev.py`(**JevWorker**): `handle(intent_id)` は起点非依存IF(引継ぎ)。フェーズ1で
  `close_broken_pairs`(H不成立のevaluated行の一括close)→K_j選択→ペア毎評価→
  `_COMPLETE`(jev_result書き込み+status='evaluated')。**latch_scoreは書かない**(ws-5時の約束)。
  本単位はJevWorkerを呼ぶだけで**jev.pyは変更しない**
- `worker/matching/layer4.py`: `hard_constraint_holds`(H再検証・LAYER1_WHERE再利用)を
  LatchEngineのH再検証でも**そのまま呼ぶ**(試験と本番のWHERE乖離なし)。`jst_day_start`も再利用
- `worker/matching/origin.py`: `load_origin`(起点検証・no-op理由)。LatchEngineのフェーズ1で再利用
- `worker/matching/runner.py`: `run_candidate_retrieval`(L1〜3+UPSERT)。Bucket/catch-up経路から
  自前トランザクションで呼ぶ。**runner.pyは変更しない**
- `worker/main.py`: `_kick_jev`(embedding_completed種のみJevWorkerをキック)。本単位はここへ
  LatchEngine呼び出しを追記(§2.1)。ReevalRunnerのDI・task追加
- `worker/cost/reeval.py`: `ReevalGuard.allow(intent_id)`(SET NX EX 1800)。Bucket・catch-upの
  入口で共用(引継ぎ・§2.8)
- `worker/backfill.py`(`BackfillRunner`): 周期ジョブの実例(sleep-firstループ・例外握り・
  stop追従)。ReevalRunnerは同型(§2.8)
- `geo/service.py`: `GeoService.reverse_geocode(lon, lat) -> str | None`(市区町村名+地物名)。
  proposalのarea_name生成に呼ぶ(§2.5)
- `worker/matching/candidates.py`: `normalize_pair`(intent_a_id < intent_b_id・UUID比較は
  Python側)。latches.intent_idsの正規化もPython側sortedで同一規律
- alembic 0003 = head。latches・latch_status_events・notificationsテーブルは0001で作成済み。
  latchesに開いている行の一意性を強制する索引は存在しない(→0004追加・§2.3)
- `settings.py`: `embedding_backfill_interval_sec` 等の周期系設定の並びへReevalRunner分を追加

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

1. **回答API・競合クローズ・matched遷移・calibration_records生成**(M3-1/M3-2・06 §6の回答処理)。
   本単位はlatchesのcandidate/proposed/expired(75分・昇格)遷移のみ。responsesは常に'[]'のまま
2. **expiry_sweeper・Intent期限切れバッチ・catch-up以外のsweeper系**(M3-3)。candidateの
   expires_at経過によるexpired遷移はsweeper担当(本単位のdrainは期限切れ行を対象外にするだけ)
3. **リセットジョブ・0時の保留キュー再評価イベント発行**(M3-4・06 §10)。M2ではdrainを
   評価経路(Bucket・catch-up・新規評価)のたびに試みることで0時直後の提示を自然に回収する
   (初回のBucket/catch-up評価でdrainが走る)。イベント発行接続はM3-4
4. **FCM・お知らせUI・通知文テンプレート**(M3-5)。本単位はnotificationsテーブルへの
   レコード生成まで(§2.7・承認事項2)
5. **グループマッチ**(ws-7): 候補Pool・貪欲法・group_candidates・aggregate_score。本単位は
   1対1のみ。latches生成・proposal生成(headcount=2)・drainは1対1構成で実装し、
   ws-7が集合向けに拡張する(拡張点をdocstringへ記す)
6. **circuit breaker・E2Eハーネス・障害注入**(ws-8)。本単位にLLM呼び出しはない
7. **スキーマ変更のdocs反映**: 0004の部分UNIQUE索引(05 §2へ追記すべき構造)は
   ws-5のskip_reason列と同じ扱い(実装先行・docs追記は次回改版。承認事項1)

## 2. 実装方式の選択と推奨

### 2.1 Layer 5の実行位置 — 推奨: JevWorker直後の同一キック直列(案A)

Layer 5は「Layer 4の評価結果(jev_result入りのevaluated行)」を消費する。実行位置の3案:

| 案 | 内容 | 評価 |
|---|---|---|
| **A(推奨)** | `_kick_jev` 内で `JevWorker.handle(intent_id)` 完了後、引き続き `LatchEngine.handle(intent_id)` を直列実行 | Layer 5+通知の層別予算≤2秒(06 §1)を初回投入の1連の流れで守れる。起点非依存IFをJevWorkerと同型にでき、Bucket/catch-upからも同一チェーンで呼べる(引継ぎとの整合)。冪等ガードも後述の通り単純 |
| B | JevWorkerの`_evaluate`内(ペア毎完了時)にlatch_score計算とlatches生成を組み込む | ペア毎の中間コミット構造(短tx UPDATE)とlatches生成txが絡み、JevWorker(ws-5資産)の変更が必要。「latch_score計算・保留キューは本単位の担い」(引継ぎ)に対しLayer 4コードを触る形になり責務境界面が濁る |
| C | LatchEngineを独立したPub/Sub Consumerにする(embedding_completedを二重購読) | Worker 1構成で消費者を増やさず、Layer 4完了を待たず起動して対象行がないno-opが増えるだけ。at-least-onceの二重経路になり冪等論証が複雑化 |

**採用: 案A。** `_kick_jev` の末尾で `await self._latch.handle(intent_id)`(未注入なら何も
しない — ws-1資産の試験互換。`_kick_embedding` と同一規律)。イベント種は引き続き
embedding_completedのみ。`_dispatch`/`_on_release` 側の変更は不要(`_kick_jev` が呼ばれる
経路はそのまま)。

**LatchEngine.handle(intent_id) のIFは起点非依存**(JevWorkerと同型・§2.8のBucket/catch-up
から同一部品を呼ぶ)。戻り値なし・例外方針は§2.9。

### 2.2 latch_score計算とH再検証

**対象行の選択**(フェーズ1・短tx)。`load_origin` で起点を検証(no-op理由はJevWorkerと同一
分岐・構造化ログ)したうえで、起点に紐づく「計算済みでない評価行」を取得する:

```sql
SELECT mc.id, mc.intent_a_id, mc.intent_b_id, mc.intent_a_version,
       mc.intent_b_version, mc.jev_result, mc.cheap_judge_score
FROM match_candidates mc
WHERE (mc.intent_a_id = CAST(:origin AS uuid)
       OR mc.intent_b_id = CAST(:origin AS uuid))
  AND (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
            THEN mc.intent_a_version
            ELSE mc.intent_b_version END) = :origin_version
  AND mc.status = 'evaluated'
  AND mc.jev_result IS NOT NULL
  AND mc.latch_score IS NULL
  AND EXISTS (
      SELECT 1 FROM intents p
      WHERE p.id = (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                         THEN mc.intent_b_id ELSE mc.intent_a_id END)
        AND p.version = (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                              THEN mc.intent_b_version
                              ELSE mc.intent_a_version END))
ORDER BY mc.cheap_judge_score DESC NULLS LAST,
         (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
               THEN mc.intent_b_id ELSE mc.intent_a_id END) ASC
```

- 起点version一致・`latch_score IS NULL`(冪等ガード。再実行で完了分は飛ぶ)はJevWorkerの
  選択SQLと同じ構成。**相手version=相手現行**のEXISTS条件が本単位特有: 相手が更新済みの
  旧バージョン組行は評価世代として陳腐化しており提案に使わない(06 §10手順5は新バージョン
  組で再提案する建付け。旧世代行はlatch_score NULLのまま残す — JevWorkerの
  「旧世代行はjev_result保持のまま」というws-5設計と同じ扱いで、クローズはしない)
- LIMITなし: 対象は当該起点のJev評価済み行のみ(1イベント高々K_j=8件の蓄積)で全件処理してよい

**ペア毎の処理**(フェーズ2・評価トランザクション):

1. **H再検証**(06 §6「H: Layer 1の判定時点の再検証」): `layer4.hard_constraint_holds(conn,
   origin, peer_id)` を呼ぶ(LAYER1_WHERE再利用)。JevWorkerの`close_broken_pairs`が直近で
   H不成立行をclose済みだが、latches生成時点でのH確定はLayer 5自身の責務として再検査する
   (belt-and-suspenders・1ペア1SELECT)。不成立なら当該行を`status='closed'`へ
   UPDATE(`WHERE id=:row_id AND status='evaluated'`)して次の行へ
2. **MutualScore = min(would_a_accept_b, would_b_accept_a)**(jev_resultのJSONから。jev_result
   の検証はGateway済み・生のJSONB読み取りのみ)
3. **latch_score = H(=1) × MutualScore × C(=1.0)**。Cは定数 `LATCH_C = 1.0`
   (01 §14・将来のCalibration調整点。settingsではなくコード定数 — 運用データでの調整は
   計測を経てdocs改訂後に変更する値で、環境差し替え想定ではない)
4. **退避つきUPDATE**(06 §10手順1〜2の骨格):

```sql
UPDATE match_candidates
SET prev_latch_score = latch_score,
    prev_evaluated_at = updated_at,
    latch_score = :score,
    updated_at = :now
WHERE id = CAST(:row_id AS uuid) AND latch_score IS NULL
RETURNING id
```

初回計算(latch_score IS NULL)ではprev_*はNULLのまま退避される(手順3「prevがNULLなら
無条件に変化あり」の状態を自然に作る)。行数0=他の実行が先に計算済み(競合負け)で
スキップ。将来のC調整による同一バージョン組再評価もこのUPDATEの形でprevを残す

5. **閾値判定**: `latch_score >= LATCH_THRESHOLD(0.80)`(06 §6「L >= 0.80」)→提案経路(§2.3〜
   §2.4)。未満→nearby経路(§2.7)。どちらでもなければ(latches化条件なし)何もしない

### 2.3 latches行の生成と重複防止 — 部分UNIQUE索引(0004・承認事項1)

**課題。** latchesテーブルには同一メンバー集合の重複を防ぐ制約がない。一方で次の3つが要る:

1. **at-least-onceの冪等**: `_kick_jev` はduplicate Eventでも実行される(worker/main.pyの
   `_dispatch` は processed/duplicate ともキックする)。同一ペアのcandidate二重生成を防ぐ
2. **同一ペアへの並立提案の遮断**: 既存提案(proposed)が開いている間に新評価世代で
   2本目のlatchesを作ると、D-08(通知過多の抑制)と03 §7(不成立理由の二値化表示)の趣旨に
   反する重複提案になる。06 §10手順5の「再提案を許す」は既存提案が閉じた後の話
3. **nearby行の昇格**(§2.7): 閾値未満で作ったcandidate行を、後の閾値超過評価で
   「通常の提案判定に回す」(引用#9)には、既存行の更新(昇格)として実装するのが唯一の
   整合方法(2本目を作ると上記2に抵触)

**採用: 開いているlatchesは同一メンバー集合で1行、をDBで強制する部分UNIQUE索引。**

```sql
-- 0004_latches_open_unique.py
CREATE UNIQUE INDEX uq_latches_intent_ids_open
    ON latches (intent_ids)
    WHERE status IN ('candidate', 'proposed', 'partial_accept');
```

- intent_idsは正規化(昇順ソート)して格納 — `normalize_pair`(candidates.py)と同じ規律で
  Python側 `sorted([a, b])`(1対1。ws-7は集合側で同様に正規化)
- INSERTは `ON CONFLICT (intent_ids) WHERE status IN ('candidate','proposed','partial_accept')
  DO NOTHING`。行数0なら「開いている行が既にある」→(a)その行がcandidateなら**昇格判定**へ、
  (b)proposed/partial_acceptならスキップ(構造化ログ `latch open row exists`)
- 閉じた行(expired/rejected/cancelled/matched/completed)は制約対象外なので、
  D-07検査を通れば新規latchesを作れる(「再提案」の正しい経路)
- グループ(ws-7)にも同一制約がそのまま効く(同一メンバー集合の開いているlatchesは1行)

**生成(フェーズ3・単一tx):**

```sql
INSERT INTO latches
    (intent_ids, proposal, score, status, response_deadline, expires_at, created_at)
VALUES (ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[],
        CAST(:proposal AS jsonb), :score, 'candidate', :deadline, :expires, :now)
ON CONFLICT (intent_ids) WHERE status IN ('candidate','proposed','partial_accept')
DO NOTHING
RETURNING id
```

- `score` = latch_score(05「提示時のスコア」。candidate時点の現値を置き、提示時に意味を持つ)
- `response_deadline` = D-05式の**暫定値**(通知時刻=now=登録時刻。引用#3のとおり提示判定には
  使わない。提示時(§2.6)に必ず再計算して上書きする)
- `expires_at` = min(両Intentのexpires_at)(引用#15)
- 成功時のみ `latch_status_events` へ `(from_status=NULL, to_status='candidate',
  user_id=NULL)` を同一txでINSERT(引用#16。システム起因=Layer 5のためuser_id=NULL)
- INSERTスキップ(OPTION CONFLICT)時はlatch_status_eventsも書かない

**昇格判定**(ON CONFLICTで飛んだ場合のうち相手行がcandidateのとき):

- 前提: 今回の評価が**閾値超過**(閾値未満の評価で既存candidate行を更新しない — nearby行が
  閾値未満のまま再評価されてもスコア・存在通知とも再送しない)
- D-07検査(§2.4)を通るなら、既存行を `UPDATE latches SET score=:score,
  proposal=CAST(:p AS jsonb), response_deadline=:deadline WHERE id=:id AND
  status='candidate'` で更新(proposalはvisibility分岐の通常生成に切り替え・deadlineは
  暫定値再計算)し、提示判定(§2.6)へ進む。latchesにupdated_at列はない(05 §2)ため
  更新列はこの3つのみ
- D-07検査に落ちるなら何もしない(構造化ログ)

### 2.4 D-07再提案制御の実装(提案化の直前・引用#6〜#7)

判定は純関数 `d07_allows(...)`(latch_calc.py)として切り出し、**新規INSERT時と昇格時の
両方の直前**に呼ぶ(実装箇所は06 §10「Layer 5の提案化の直前」。昇格=proposed化だから
両方に要る)。入力と判定順:

1. **no履歴**: 同一Intent組み合わせ(正規化ペア)の過去latches(開いている・閉じた全履歴)の
   `responses` に `response='no'` が1つでもあれば **False**(Intent期限まで再提案しない)。
   履歴検索SQL:

```sql
SELECT responses FROM latches
WHERE intent_ids = ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]
  AND responses <> CAST('[]' AS jsonb)
```

(intent_idsの等値は正規化配列の一致。B木のuuid[]等値は部分UNIQUE索引と同型に効く)

2. **defer抑制**: `responses` に `response='defer'` がある場合、直近のdefer回答
   (`answered_at` の最大)から `min(24時間, (対象開始時刻 − now) / 2)` 以内なら、
   **スコア変化判定**(手順3)を満たすかを見る。満たさなければ **False**
3. **スコア変化判定**(引用#7): 今回計算したmatch_candidates行の `prev_latch_score` が
   NULL → 無条件に変化あり(=再提案可)。非NULL → `abs(new_score − prev_latch_score) >= 0.05`
   を変化あり。M2の対象行はlatch_score IS NULL(初回計算)でprevもNULLのため常に変化ありと
   なる — これは仕様どおり(新バージョン組はprev値を持たない。引用#7手順5)。同一バージョン
   組での再計算(C調整時)にのみ|Δ|判定が効く。prev値は§2.2の退避つきUPDATEが供給する
4. defer履歴なし(初回提案、または前回提案が無回答期限切れ等)なら**手順1のみ**で判定
   (03 D-07は見送りとNOのみ規定し、無回答後の再提案を抑制しない — 仕様にない抑制を
   勝手に足さない)

**「対象開始時刻」の解釈(承認事項3)。** 1対1でtime_startが2つある場合の代表値はdocsに
明示がない。**max(参加Intentのtime_start)(=時間窓交差の開始時刻)を採用する** —
Hard Filterが時間窓の交差を条件とする以上、両者が合流できる最早時刻が「対象開始時刻」
であり、minでは「片方の開始時刻が既に過ぎている交差」が常に75分ルールで破棄され
実用にならないため。この値は75分ルール・D-05式・defer抑制の残時間計算・提示順の
ソートキーで一貫して使う(§2.6)。G2時確認候補として記録する

### 2.5 proposal生成(visibility分岐・area_name)

`proposal.py` に純関数 `build_proposal(inputs_a, inputs_b, score) -> dict` を置く。

**入力収集**(フェーズ2で専用SELECT・両者ぶん):

```sql
SELECT id, user_id, visibility, notification_level, time_start, time_end,
       expires_at, budget_max, category_primary, structured_data,
       ST_X(geo_center::geometry) AS lon, ST_Y(geo_center::geometry) AS lat
FROM intents WHERE id = CAST(:intent_id AS uuid)
```

**visibility分岐(引用#12)**: `visibility_a == 'summary_only' and visibility_b ==
'summary_only'` →全フィールド。いずれかがhidden_until_match → `{"headcount": 2,
"match_level": <level>}` のみ。

各フィールドの生成規則(05 §2の表どおり):

| フィールド | 生成 |
|---|---|
| time_summary | 対象時刻(max(time_start))のJST文字列 `YYYY-MM-DD HH:MM`(表示文言「今夜 20:00〜」への整形はM3クライアント/テンプレート。**値の書式は本設計の確定** — 05は「集合の対象時刻」のみ規定) |
| area_name | `GeoService.reverse_geocode((lon_a+lon_b)/2, (lat_a+lat_b)/2)`。**中点を約1kmグリッド代表点へ丸めた地名**(承認事項3。geo/service.pyが丸め込みで地名を返す)。地物なし(None)はnullで格納(M3表示側のフォールバック対象。対象エリアは地物取り込み済みで通常発生しない) |
| headcount | 2(1対1。ws-7が集合サイズへ拡張) |
| category_primary | 共通値(06 Layer 1の完全一致条件により同一) |
| category_secondary | **起点Intent(評価の種)のstructured_data.category_secondary**(05「集合の種(Intent)の値を採る」の1対1適用。双方にsecondaryがある場合も起点側) |
| budget | ペア予算 `{"max": min(budget_max_a, budget_max_b)}`(NULLは無視・06 §2。両方NULLならnull) |
| match_level | 純関数 `match_level(score)`: score>=0.90→'high'・>=0.80→'medium'・未満→'low'(引用#13。下端は運用閾値に連動し未定義区間なし) |

格納禁止(raw_text・soft/NG条件文言・座標)は構造上入らない(08 §2.3)。近傍行(nearby)の
proposalは§2.7のとおり常に最小構成。

### 2.6 D-08上限・提示(candidate→proposed)・D-05式・75分ルール・drain

**提示判定 `try_promote(latch_id)`(フェーズ3/4共通部品・行毎にtx):**

1. 行を `SELECT ... FOR UPDATE`(status='candidate'を確認)
2. **75分ルール**: 対象時刻(max time_start・参加Intentから再計算)− now < 75分 →
   `UPDATE latches SET status='expired' WHERE id=:id AND status='candidate'` +
   latch_status_events(candidate→expired)を同一txで記録して終了(引用#5・「通知せず破棄」)
3. `expires_at <= now` → 何もせず終了(expiry_sweeper=M3-3の担当。ここでは提示対象外にするだけ)
4. **D-08日次上限**(引用#8): 通知を送る参加者(notification_level != 'muted'のuser_id)ごとに
   当日の通知件数を数え、全員 6 未満であること。カウントの真実は **notificationsテーブル**
   (§2.7・承認事項2):

```sql
SELECT user_id, COUNT(*) FROM notifications
WHERE user_id = ANY(CAST(:users AS uuid[]))
  AND type IN ('proposal', 'nearby_candidate')
  AND created_at >= CAST(:day_start AS timestamptz)
  AND created_at <  CAST(:day_next AS timestamptz)
GROUP BY user_id
```

(`day_start`=`jst_day_start(clock.jst_date())`(layer4再利用)・`day_next`=+1日。
0時リセットは日付条件の動的切り替えで自然に成立 — カウンタリセットジョブ不要)
5. **D-08同時進行上限**: 参加Intentそれぞれについて、当該Intentを含む開いている
   proposed/partial_accept件数が 3 未満であること:

```sql
SELECT COUNT(*) FROM latches
WHERE status IN ('proposed', 'partial_accept')
  AND intent_ids @> ARRAY[CAST(:intent_id AS uuid)]
```

(muted参加Intentもproposed数に計上 — 引用#10のとおり遷移自体は行うため)
6. 上限内なら **proposed遷移**: `UPDATE latches SET status='proposed',
   response_deadline=:deadline WHERE id=:id AND status='candidate'`(条件付きUPDATE・引用#22。
   行数0=他経路で既に遷移済み→終了)。`deadline`は **D-05式を提示時点(now)で再計算**
   (引用#3〜#4):

```python
def response_deadline(now, target_time, min_expires_at):
    inner = max(now + timedelta(minutes=15),
                min(now + timedelta(hours=2), target_time - timedelta(minutes=60)))
    return min(inner, min_expires_at)
```

   deadline <= now となる場合は通知しない(引用#4「75分ルールと同一の扱い」— 手順2の
   75分チェックが先に効くため通常はここに到達しない。防御として残す)
7. **latch_status_events(candidate→proposed・user_id=NULL)+ notifications INSERT**
   (通知対象者のみ・§2.7)を同一txで実行

**drain(フェーズ4・引用#17の「提示順の再計算」)。** LatchEngine.handle の末尾で保留キューを
提示順に走査し、`try_promote` を各行に試みる:

```sql
SELECT l.id,
       (SELECT max(i.time_start) FROM intents i
        WHERE i.id = ANY(l.intent_ids)) AS target_time
FROM latches l
WHERE l.status = 'candidate'
  AND l.expires_at > :now
  AND l.score >= :threshold   -- nearby行(閾値未満)はproposed化しない(引用#9)
ORDER BY target_time ASC, l.score DESC, l.id ASC
```

- ソート = 対象時刻昇順・LATCH Score降順(引用#3)。さらに同点はlatch_id昇順で決定的に
  崩す(D-24の同点規律と同型・docsに明示なしのため設計確定)
- 各行独立に判定(上限に当たった行はcandidateのまま次へ。「提示順に沿って上限内の件数のみ
  処理し、残りは次のトリガーへ」=行単位の上限判定で自然に実現。ci規模のキュー長で
  全走査のコストは無視できる)
- nearby行(score < 0.80)はdrain対象外(SELECT条件で除外)。nearby行の昇格は
  §2.3の新規評価経路のみ

### 2.7 nearby_also存在通知・muted抑制・notifications(承認事項2)

**notificationsテーブルをD-08カウントと通知事実の両方の真実として使う**(05 §2既定義の
テーブル。type/user_id/payload/read_at/created_at):

- **提案通知(proposed化時)**: 通知対象者 = `notification_level in
  ('proposals_only','nearby_also')` の参加者(mutedは除外・引用#10)。1対1で最大2行
  `INSERT INTO notifications (user_id, type, payload, created_at) VALUES
  (:uid, 'proposal', CAST(:payload AS jsonb), :now)`。`payload` は `{"latch_id": "<uuid>"}`
  の最小参照(通知文・条件サマリの組み立てはM3-5がlatches.proposalから行う。
  ここに文言を置かないことでA/B文言変更が構造に引かれない — 05 §2のproposalと同精神)
  - hidden_until_matchの提案でもnotificationsは書く(本文はM3-5が汎用文を組み立てる。
    ws-6は送信先記録のみで表示要素を持たないため08 §2.6の規制と独立)
- **存在通知(nearby・閾値未満評価時)**: ペアのうち `notification_level='nearby_also'` の
  参加者に限り、**当日上限(日6件)内なら** `type='nearby_candidate'`・同一payloadでINSERT
  (引用#9「存在通知もD-08の日次上限に含める」)。上限到達時はスキップ+構造化ログ
  (存在通知は保留できないため、超過分は切り捨て。candidate行自体は作る)
  - 同一latches行からの存在通知は行生成時の1回のみ(開いている間の再評価では§2.3の
    ON CONFLICTでINSERTが飛び、通知も再送されない)
  - **no履歴のあるペアには存在通知も出さない**(D-07「NO=このIntent組み合わせへの拒否」の
    一貫適用 — docs明示なし・設計確定)。defer抑制は提案でないため適用しない
- **nearby行のlatches生成**: 閾値未満かつnearby_also参加者がいる(かつno履歴なし)場合のみ、
  latches行をcandidateで作る(引用#9「当該候補はlatches.status=candidateのまま保持」)。
  proposalはvisibilityによらず**常に最小構造 `{"headcount": 2, "match_level": "low"}`**
  (08 §2.2「存在通知には条件サマリ・相手情報を一切含めない」— summary_only相手でも
  存在通知にサマリを出さない以上、格納も最小にする。match_levelはNOT NULL相当の構造
  埋めで'low'。スコアはscore列に実値を保持し昇格時に上書き)。response_deadlineは暫定値
  (提示されない行なので実質未使用・形式上NOT NULLのため格納)。nearby_also参加者が
  いない閾値未満候補はlatches行を作らない
- **muted**: proposed遷移・latch_status_events・drain・D-08同時3件の計上は通常どおり。
  notificationsのみ書かない(=当日日次上限も消費しない — 通知しないものはカウントしない)

**Redisカウンタを使わない理由**: D-16カウンタ(ws-4)は「実行回数のブレーキ」で事前INCRが
本質。D-08は「通知した事実の上限」で、notifications行の存在が事実そのもの。DB一元化により
試験・監査でカウンタと事実の突合が不要になり、0時境界もjst_day_startでClock制御下に置ける。
ci規模の件数(1ユーザー日6件)でCOUNTコストは無視できる。

### 2.8 30分Bucket再評価とcatch-upスキャン — 推奨: 直接投入へ統一(scheduled不使用)

**ws-1設計§1.4-3の委譲事項(「scheduled EventをBucketスケジューラが発行するか直接投入に
揃えるか」)への回答: 両経路ともEventを発行しない直接投入に統一し、scheduled Event種の
発行経路は作らない**(定数・stage1の受信規定〔即時処理〕は将来用にそのまま残置)。

理由: catch-upがEvent発行を避けるのと同一の理由(idempotencyキー
(event_type, source_intent_id, version)のUNIQUEが同一versionの再発行を許さない。06 §9)
がBucket再評価にもそのまま当てはまる。1 Intentは時間の経過とともに複数回の再評価対象に
なり得るが、versionは更新時しか上がらないため、Event方式では2回目のBucket評価が
恒久に発行できない。

**ReevalRunner(worker/reeval.py・BackfillRunnerと同型の周期ジョブ):**

- 周期60秒(06 §9「expiry_sweeperと同一の60秒周期スケジューラ」。M3-3がsweeperを
  同一ジョブへ統合できるよう、Runnerは独立クラスにしてWorker.run()で並列task化)
- sleep-first・stop追従・run_once内の例外は握って次周期で回収(BackfillRunnerと同一)

**run_once の中身:**

1. **catch-up対象の抽出**(引用#19。60秒毎に評価):

```sql
SELECT id FROM intents
WHERE status = 'active'
  AND embedding IS NOT NULL
  AND expires_at IS NOT NULL
  AND expires_at > :now
  AND expires_at <= CAST(:now AS timestamptz) + interval '2 hours'
ORDER BY expires_at
LIMIT :batch_limit
```

「直近の詳細評価から30分以上経過」の抽出条件はSQLに入れない — **reevalガード
(`ReevalGuard.allow`)が入口で同じ意味(30分以内の再評価抑止)をアトミックに判定・消費する**
ため(引継ぎ「reevalガードは入口で共用」。抽出してガードで弾く結果は等しい)
2. **30分Bucket処理**: 現在時刻の属するBucketの開始時刻(30分切り下げ・Clock由来の
   純関数 `bucket_start(now)` = JSTへ変換し分を0/30へ切り下げ)が前回処理分より新しければ:

```sql
SELECT id FROM intents
WHERE status = 'active'
  AND embedding IS NOT NULL
  AND time_start >= CAST(:bucket_start AS timestamptz)
  AND time_start <  CAST(:bucket_end AS timestamptz)
```

「到来したBucketに属するIntent」= **time_startが当該Bucketに属するIntent**(01 §16
「20:00に有効化するIntentを取得」の読み。設計確定・理由: time_startは不変なので
1 IntentのBucket到来は高々1回であり、これを過ぎた後の再評価機会はcatch-up(期限2時間前
から30分周期)が担う。窓交差Bucketごとの再評価案はcatch-upと役割が重複し評価回数を
増やすだけ)。前回処理BucketはRunnerのメモリ保持(再起動でリセット→再起動直後に現在
Bucketを処理→reevalガードが30分以内を弾く→無害)
3. **各Intentへの直接投入**(両経路共通・`_run_direct_pipeline(intent_id)`):

```text
reeval.allow(intent_id) が False → スキップ(30分以内)
engine.begin() のトランザクションで run_candidate_retrieval(conn, clock, intent_id)  ← L1〜3
コミット → JevWorker.handle(intent_id)  ← L4(引継ぎの起点非依存IF)
        → LatchEngine.handle(intent_id)  ← L5(本単位)
```

(stage1の`_run_matching`は「stage1トランザクションに同乗する」構造のため流用しない。
直接投入用にworker/main.pyへ `_run_direct_pipeline` を新設。例外は握らずRunnerへ伝播
させ、Runnerが握って次周期で回収 — 評価の失敗がキューの再配信に載らない代わりに
60秒後の再試行が回収する。冪等は各部のガード(latch_score IS NULL・ON CONFLICT・
条件付きUPDATE)で担保済み)
4. LatchEngine.handle内のdrain(§2.6)が評価のたびに提示を回すため、0時リセット後の
   保留提示もM2ではこの経路で自然に回収される(引用#17。正式な再評価イベント接続はM3-4)

**設定(settings.pyへ追加):** `reeval_runner_interval_sec: int = 60`・
`reeval_runner_batch_limit: int = 50`(backfillと同型の命名)。

### 2.9 例外方針・冪等性・再配信回収(JevWorkerの表と同型)

| 状況 | 扱い |
|---|---|
| 起点が読めない(削除・非active・embedding NULL等) | no-op・構造化ログ(`latch origin no-op` — JevWorkerと同型) |
| H再検証不成立 | 当該match_candidates行をclosedへ(§2.2) |
| latch_score計算済み(再実行) | 選択SQLの `latch_score IS NULL` で自然スキップ |
| latches INSERTの競合(開いている行あり) | ON CONFLICT DO NOTHING → 昇格判定またはスキップ |
| proposed遷移の競合(他が先に) | 条件付きUPDATEの行数0 → 終了(notificationsも書かない) |
| 逆転ジオコーディングで地物なし | proposal.area_name=null で続行(表示はM3側のフォールバック) |
| **DB書き込み失敗** | **伝播**(JevWorkerと同じ方針)。`_kick_jev` 経由なら`_dispatch`/`_on_release`の既存except→ackなし再配信→再実行(上記ガードで冪等)。Runner経路なら次周期60秒後の再試行 |
| Redis(reeval)失敗 | 既存のとおりfail-closed(ReevalGuardがJevCostDependencyErrorへ包む)。embedding_completed起点ではstage1再試行経路へ、Runner経路では握って次周期 |

トランザクション分割: (1)フェーズ1読取(短tx) (2)ペア毎のH再検証+退避つきlatch_score UPDATE
(1 tx) (3)latches INSERT/昇格+latch_status_events(1 tx) (4)提示遷移+status_events+
notifications(1 tx・try_promote) (5)drainは行毎に(4)を再利用。長いトランザクションを
作らない(embedding/JevWorkerの2フェーズ構成の規律継承)。LLM呼び出しがないため
「API呼び出しのtx外化」の論点は生じない(geo逆転は読取のみ・tx外で実行)。

### 2.10 採用しないもの(YAGNIによる切り捨て一覧)

1. **RedisによるD-08日次カウンタ** — notificationsを真実とする(§2.7)。二重管理を避ける
2. **latchesへの「対象時刻」カラム追加** — 参加Intentからmax(time_start)を都度導出
   (§2.4解釈)。提示順はdrain SELECTの相関サブクエリで足りる
3. **latchesへの評価世代(version組)カラム追加** — D-07の世代判定はmatch_candidatesの
   prev_latch_score(NULL=新世代)で足りる(引用#7手順5)
4. **性能索引(latches status部分索引・notifications(user_id, created_at)・GIN(intent_ids))**
   — ci・ベータ規模の件数では全走査が十分速い。部分UNIQUE(0004)は正確性が目的で必要。
   性能索引は件数が増えた段階で追加(05 §3の改定とセット)
5. **窓交差Bucketごとの再評価** — §2.8のとおりtime_start属するBucket+catch-upで足りる
6. **expiry_sweeperの先行実装(candidateのexpires_at切れ遷移)** — M3-3の担い(引用#5)。
   本単位はdrain/selectの対象外化のみ
7. **同一バージョン組でのlatch_score再計算経路(C調整)** — C=1.0固定。再計算が必要になる
   のはCalibration蓄積後(docs改訂を伴う)。退避つきUPDATEとd07純関数はそのときの骨格として
   動くように実装するが、呼び出し経路は作らない
8. **drainのバッチ化・分散ロック** — Worker 1構成(ci・MVP)を前提に行ロック(FOR UPDATE・
   条件付きUPDATE)のみで十分。複数Worker化はM4以降のスケーリング検討(04 §6)

## 3. ファイル構成

### 3.1 作るもの(新規ファイル)

| ファイル | 内容 |
|---|---|
| `backend/src/latch/worker/matching/latch_calc.py` | 純関数群: `response_deadline`(D-05式)・`match_level`・`d07_allows`(no/defer抑制・prev比較)・`target_time`導出(max(time_start))・`bucket_start`(30分切り下げ)。定数 `LATCH_THRESHOLD=0.80`・`LATCH_C=1.0`・`D07_DELTA=0.05`・`D08_DAILY_LIMIT=6`・`D08_CONCURRENT_LIMIT=3`・提示75分/下限15分/上限2時間/締切60分の各timedelta |
| `backend/src/latch/worker/matching/proposal.py` | `build_proposal`(visibility分岐・§2.5の表)・nearby最小構造の定数。`JevTextInput`的な入力dataclass(LatchIntentInputs) |
| `backend/src/latch/worker/matching/latch_engine.py` | `LatchEngine`本体: `handle(intent_id)`(フェーズ1〜4)・選択SQL・退避つきUPDATE・latches INSERT/昇格・`try_promote`(75分/D-08/proposed遷移/notifications)・drain。モジュール属性経由でorigin/layer4/latch_calc/proposalを呼ぶ(runnerと同一規律・unit試験がmonkeypatchで差し替え可能) |
| `backend/src/latch/worker/reeval.py` | `ReevalRunner`(§2.8): catch-up抽出SQL・Bucket抽出SQL・`_pipeline`呼び出し。BackfillRunner同型のsleep-firstループ |
| `backend/alembic/versions/0004_latches_open_unique.py` | 部分UNIQUE索引 `uq_latches_intent_ids_open`(§2.3) |
| `backend/tests/unit/matching/test_latch_calc.py` | 純関数のunit(D-05式境界・match_level境界・d07分岐・bucket_start) |
| `backend/tests/unit/matching/test_proposal.py` | proposal生成のunit(visibility分岐・budget NULL・secondary選択) |
| `backend/tests/unit/matching/test_latch_engine.py` | LatchEngineのunit(test_worker_jev.pyと同型: engine/origin/layer4スタブ+モジュール属性差し替え) |
| `backend/tests/unit/test_worker_reeval.py` | ReevalRunnerのunit(FakeClock・ガードスタブ・sleep差し替え) |
| `backend/tests/integration/test_matching_latchengine.py` | integration(§4.2) |

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更 |
|---|---|
| `backend/src/latch/worker/main.py` | (1)`_kick_jev` の末尾へ `LatchEngine.handle` 呼び出しを追記(docstring更新・未注入ならno-op) (2)`LatchEngine`・`ReevalRunner` のDI(run()内構築・geoは`GeoService(engine)`を注入) (3)`_run_direct_pipeline(intent_id)` の新設(§2.8-3) (4)ReevalRunnerのtask起動・graceful shutdown待ち |
| `backend/src/latch/settings.py` | `reeval_runner_interval_sec=60`・`reeval_runner_batch_limit=50` を追加(backfill並び) |
| `backend/tests/unit/test_worker.py` 等の既存Worker試験 | DI追加に伴う機械的追随(コンストラクタ引数はデルト値で従来互換にするため最小限) |

### 3.3 触らないもの(明示)

- `worker/jev.py`(JevWorker)・`worker/matching/layer1〜4.py`・`runner.py`・`origin.py`・
  `candidates.py` — ws-5資産。LatchEngineから呼ぶのみ
- `worker/stage1.py` — matching_hook構造のまま。直接投入はmain.py側の新ヘルパー
- `worker/embedding.py`・`worker/cost/*` — 再利用のみ
- `geo/service.py` — `reverse_geocode` を呼ぶのみ
- `intents/`・`auth/`・`users/`・`llm/`(Gateway・providers) — 関与なし
- `backend/alembic/versions/0001〜0003` — 変更不可(運用ルールどおり)
- `docs/`(00〜12) — 本設計の解釈記録をG2時確認事項へ残すのはSTATUS運用(supervisor経由)。
  agent1は設計書内に記録するのみ

## 4. テスト方針

対抗策はws-4/ws-5検証で確立したものを踏襲(スーパーバイザー追記3):

- **時間窓分離**: integration Fixtureのtime_start/expires_atは **now+5日系へ統一**
  (専用カテゴリ分離でなく時間窓で既存データと分離 — ws-4裁定)。期限・75分・D-05系の
  試験だけ専用の遠い/近い窓を使い、関数内パート間で窓が重ならないようClock進行を設計
- **active保存時のtime_end補完(start+3h)に注意**: 時間を進めるパートではtime_startの
  再指定が必要かを常に確認(ws-5検証(4)と同種の落とし穴)
- **teardown完全性**: latches・latch_status_events・notifications・match_candidates・
  intents・usersをユーザーprefix単位で完全削除(FK・部分UNIQUEの残存が次回試験の
  「開いている行あり」判定を壊す)。削除順はlatch_status_events→notifications→
  latches→match_candidates→intents→users
- **テストbasename一意**(運用ルール5): `test_latch_calc.py`・`test_proposal.py`・
  `test_latch_engine.py`・`test_worker_reeval.py`・`test_matching_latchengine.py`
  はいずれも既存と衝突なし
- integrationはagent3実行禁止のため**収集のみ**(`--collect-only`で件数証拠)。
  test-ci=スーパーバイザー検証待ち(運用ルール1〜3)

### 4.1 unit(`make test`。スタブで決定的)

`test_latch_calc.py`(純関数・境界中心):

1. D-05式: 15分下限が効くケース(対象時刻まで>2時間)・2時間上限が効くケース・
   60分締切が効くケース・expires_atが最少になるケース・期限<=nowになるケース
2. match_level: 0.90/0.80/0.79/0.70の境界
3. d07_allows: no履歴→False・defer抑制期間内+prev None(新世代)→True(無条件変化あり)・
   抑制期間内+|Δ|<0.05→False・抑制期間内+|Δ|>=0.05→True・抑制期間経過→True・
   defer履歴なし+noなし→True
4. bucket_start: 境界時刻(:00/:30)・JST変換を含む値

`test_proposal.py`: 双方summary_only→全フィールド・片方hidden→最小構造のみ・
budget両方NULL→null・片方NULL→値・secondary起点側採用・area_name=None許容

`test_latch_engine.py`(test_worker_jev.pyと同型): no-op分岐(起点不在)・H再検証不成立で
closed・latch_score計算とprev退避・閾値境界(0.80ちょうど→提案)・ON CONFLICTで
既存candidate→昇格(score/proposal更新)・既存proposed→スキップ・try_promoteの
75分→expired+status_events・D-08日次6件目→保留・同時3件→保留・muted→notifications
なし+proposed・notifications INSERTの型と件数・drainの順序(対象時刻昇順・score降順)・
冪重実行(latch_score NULLガードで二重なし)

`test_worker_reeval.py`: catch-up抽出(expires_at境界・2時間内外)・Bucket処理
(同一Bucketの再処理なし・Bucket境界の切り替わり)・ガードdenyでpipeline呼ばれない・
例外を握って継続・stop追従

### 4.2 integration(`make test-ci`。compose常設DB・実Redis・スタブGateway)

`test_matching_latchengine.py`(ws-5の`test_matching_jev.py`構成を踏襲・FakeClock操作):

1. **E2E提案生成**(02#12・#8下地): ユーザー2名+Intent2件(now+5日窓)→embedding
   fixture直入れ→match_candidatesへjev_result付き行を直接投入(stub応答値由来)→
   `LatchEngine.handle` →latches(candidate/proposed)・latch_status_events(NULL→candidate→
   proposed)・notifications 2行・response_deadline=D-05再計算値の検証
2. **visibility分岐**: summary_only×2とhidden_until_match混在の2ケースでproposal内容比較
   (hiddenはheadcount+match_levelのみ・#22の下地)
3. **D-08日次上限**: 通知6件の状態を作り7件目がcandidateのまま(proposedにならない)
   ・nearby存在通知が上限でスキップされるケース
4. **同時3件/Intent**: 既存proposed 3件→4件目candidate保留
5. **75分ルール**: 対象時刻をnow+70分にしたFixture→handle後にcandidate→expired・
   通知ゼロ
6. **nearby_also**: 閾値未満(stub値0.5等)・nearby_also指定→candidate+存在通知
   1行(nearby_also側のみ)・proposed遷移なし・muted側通知なし
7. **D-07 defer抑制**: latches.responsesへdefer履歴を直接投入→抑制期間内の再評価
   (Clock進行なし)で新latches不作成・Clockを抑制期間経過へ進めると作成
8. **drain順序**: candidate 3件(対象時刻・scoreを組み替え)→提示順の検証
9. **冪等性**: 同一`handle`を2回実行→latches二重なし・status_eventsのcandidate挿入は1回
10. **ReevalRunner経路**(02#8): 時刻をまたぐ2 Intent+FakeClock進行→catch-up抽出→
    直接pipeline→候補生成の記録。reevalガードで30分以内の再抽出がスキップされること

### 4.3 検証手順(報告書への明記用)

1. `make lint` / `make test`(全unit)
2. `uv run pytest --collect-only tests/integration/test_matching_latchengine.py -q`
   (収集件数の記録。実行はスーパーバイザー検証時)
3. 時刻参照がclock.pyのみ: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|
   time\.sleep|from time import' backend/src` が `core/clock.py:33` のみ+
   `test_arch_no_direct_time.py` passed
4. alembic: 0004追加後も0001〜0003無変更(`git diff main -- backend/alembic/versions/000[1-3]*`
   が空)
5. 変更ファイル一覧が§3と一致・`git status` 空・テストbasename一意(運用ルール5のコマンド)
6. スーパーバイザー検証時: `docker compose build api worker` → `make migrate`
   (0004適用・uq_latches_intent_ids_openの存在)→ `make test-ci`(既存926+新規)→
   Redis/users残存確認(prefix掃除・subject LIKE 'm2ws6-%'=0件)

## 5. 未解決の論点(supervisor承認事項・G2時確認候補)

**supervisor承認を求める事項(設計の前提・ws-5のdesign §5運用と同型):**

1. **マイグレーション0004(latches部分UNIQUE索引)の追加**(§2.3)。STATUS前提
   「マイグレーションは原則不要」に対する追加だが、冪等性(at-least-onceのduplicate
   キック)とnearby行昇格(引用#9)の両要件をDBで担保する最小構造。05 §2への追記は
   docs次回改版に含める(ws-5 skip_reason・ws-3 location_nameと同じ扱い)
2. **notificationsテーブルへの先行書き込み**(§2.7)。M3-5の領域(アプリ内通知の実装)に
   先行するが、(a)D-08日次上限のカウント対象は「通知の事実」であり事実の記録なしに
   上限を実装できない (b)nearby_also存在通知・muted抑制の動作証拠(02#13の下地)を
   残す — ため。payloadは`{"latch_id"}`の最小参照とし、通知文・条件サマリの組み立ては
   M3-5に委ねる(type='proposal'/'nearby_candidate'の2値)
3. **スコープ分担の確認**: 12 M3-5に「D-08上限(日6件・同時3件)と保留の提示順制御」の
   記載が残るが、STATUS承認済み単位表ではws-6の内容(12のM2-7にも同一内容あり)。
   **STATUS単位表が正**(ws-6が実装・M3-5はFCM・お知らせUI等の媒体と残務)として進める

**解釈記録(G2時確認候補・STATUS「G2時確認事項」へ追記提案):**

4. **対象開始時刻 = max(参加Intentのtime_start)**(§2.4)。75分ルール・D-05式・defer抑制
   残時間・提示順ソートがすべてこの値を参照。min(time_start)や起点側採用と結果が変わる
   場合がある(片方の開始が既に過ぎた交差ペア等)
5. **area_name = 両者geo_center中点の逆転ジオコーディング**(§2.5)。05 §2は「代表点の
   地物名」のみで中点規定なし。D-11の1kmグリッド丸めはgeo/service.pyが実装済み
6. **nearby存在通知へのno履歴検査適用・match_level='low'埋め**(§2.7)。docs明示なし
   (提案でない存在通知にD-07をどこまで適用するか)
7. **time_summary書式 = JST `YYYY-MM-DD HH:MM`**(§2.5)。表示文言(「今夜 20:00〜」)への
   整形はクライアント側
8. **category_secondaryは起点(種)Intentの値**(§2.5)。05は「集合の種の値を採る」(グループ
   向け規定の1対1適用)

**実装時確認事項(実装エージェントが確認して報告):**

9. `notifications` テーブルのtype列にCHECK制約がないこと(0001定義を確認のうえ、
   'proposal'/'nearby_candidate'の2値をコード側のLiteralで管理)。CHECKが存在する場合は
   BLOCKEDとして supervisorへ報告(docsにtype値域の規定なし・想定はフリーtext)
10. `latches.intent_ids` のuuid[]等値比較・`@>` 演算が部分UNIQUE索引のON CONFLICT推論で
    期待どおり動くこと(初回integrationで確認。推論が外れる場合はINSERT前SELECT FOR UPDATE
    への切替をsupervisorへ相談)
