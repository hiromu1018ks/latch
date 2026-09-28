# M2 ws-1(イベント駆動基盤)設計メモ

- 作成: 2026-09-28(agent1)
- 対象: STATUS.md M2作業単位表 ws-1(出典 M2-1 / 06 §9・01 §16・10 §4.7)
- 前提: M1完了時点のmain(test-ci 627 passed・alembic 0002=head・geo実データ復旧済み)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M1が実装したoutbox(match_eventsへの同一トランザクションINSERT)から、Matching Worker第1段の
処理完了までの経路を開通させる。具体的には (1) ci環境のMessage Queue具象の確定とcomposeへの追加、
(2) outbox→Queue→Workerの連携、(3) Worker第1段(debounce・idempotency・version検査・再試行→隔離・
削除Eventの候補無効化・破棄と隔離の区別)の実装である。Embeddingキックの実体以降(ws-2〜ws-6)は
この経路に乗る前提のフックのみを用意する。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | Eventは5種(作成・更新・削除・期限切れ・指定時刻の内部由来)+派生1種(embedding_completed)。MVPで発生するEventは内部由来5種 | 01 §5 |
| 2 | Match Eventはキュー経由で非同期処理。debounce(作成には適用せず・更新のみ10秒窓で統合し最新versionのみ処理)・idempotency key・version numberで不要な再計算を防ぐ。Eventの冪等化はmatch_eventsのUNIQUE制約がDBレベルで保証 | 01 §16 |
| 3 | 処理失敗は上限回数まで再試行し、再試行しても失敗するEventは失敗理由を保持したうえで隔離 | 01 §16 |
| 4 | Message Queue選定=Google Cloud Pub/Sub。「dead letter topicによる隔離が標準装備…retry回数・最大保留時間はsubscriptionで宣言的に設定でき…exactly-onceでなくてもidempotency keyで冪等化する。MVP規模にKafkaは過剰」 | 04 §3選定表 |
| 5 | match_events: id / event_type(6値)/ source_intent_id / payload(発火時のIntent versionを含む)/ status(pending/processed/quarantined)/ created_at / processed_at。UNIQUE(event_type, source_intent_id, payload内version)がPub/Subのat-least-once配信に対するDBレベルの重複排除。隔離は失敗理由をpayloadに保持。**削除済みIntentへの参照Eventは正当な遅延Eventとしてstatus=processedで破棄(理由をpayloadに記録)、payload不正(構造違反・version欠落等)のみquarantinedへ隔離** | 05 §2 |
| 6 | 第1段の確定値: (0) draft対象外・draft→active化の初回投入は作成種(version+1でも作成種。キーは常に空き)(1) 作成Eventは窓なし即時・更新Eventのみ同一source_intent_idを10秒トレーリング窓で統合し窓解放時の最新versionのみ処理(リードエッジ+差し替えの二重評価はしない)・削除/期限切れ/指定時刻Eventは統合せず即時 (2) idempotency key=(event_type, source_intent_id, version) (3) version検査: =現行のみ処理・<現行は破棄・>現行はFOR UPDATE再読込し乖離が続く限り再試行 (4) 処理失敗は5回まで再試行→失敗理由を保持してquarantined | 06 §9 |
| 7 | 削除Eventは「当該Intentを含む候補・保留の無効化(Layerを経ない)」。期限切れEventは「回答待ち提案のクローズ(Layerを経ない)」 | 06 §1 |
| 8 | 参照先Intent不在のEvent=正当な遅延Eventとしてprocessed破棄(隔離にしない。隔離滞留と誤alertの続発防止)。payload不正(構造違反・version欠落・型不一致等)=5回再試行後にquarantined。**10 §4.7の毒ペイロード試験の対象はこちら(payload不正)** | 06 §9 |
| 9 | resume時はversion+1の再評価Event(update種)を発行(idempotencyキー衝突回避) | 06 §9・05 §6 |
| 10 | debounce 10秒・retry 5回は初期値とし、計測(Queue lag)を見て調整 | 06 §9 |
| 11 | テスト環境は「04の技術スタック(PostgreSQL+PostGIS+pgvector / Pub/Sub / Redis / …)を本番と同じ種類で立てるが、規模を縮小する」。ci=最小構成・常設(API 1/Worker 1/DB共用) | 10 §1 |
| 12 | 時刻操作はClock経由。適用範囲(4)にdebounceの10秒窓を含む。「Pub/Subへのテスト用パブリッシャーでMatch Event 5種と派生イベントembedding_completedを手動発火できる」 | 10 §1 |
| 13 | 初期LATCH判定p95 10秒の層別予算: Embedding区間(debounce込み。作成Eventは窓なし)≤2秒。計測起点は「対象Intentのactive化の保存コミット時点」 | 06 §1 |
| 14 | Queue障害試験: 毒ペイロード→retry上限5回→quarantined・滞留が後続に波及しない。削除済みIntent参照Eventはprocessed破棄。同一Event2回投入でmatch_candidatesが二重生成しない(UNIQUE制約) | 10 §4.7 |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

**発行側(M1実装済み・変更はpublish呼び出しのみ)**

`backend/src/latch/intents/events.py` がoutboxの実体。`insert_match_event` はIntent保存と同一
トランザクションでmatch_eventsへINSERT(pending)。イベント文字列は created / updated / deleted /
expired / scheduled の5種が定数定義済み(embedding_completedはws-2が追加)。

intents CRUDの発行網羅(`backend/src/latch/intents/service.py`)はM1で次のとおり実装済みであり、
本単位の発行側の残作業は「コミット後のpublish呼び出し接続」のみ:

| event_type | 発行箇所(M1実装) | 確定値との整合 |
|---|---|---|
| created | active作成(service.py:415)・draft→active化(:671・初回投入は作成種) | 06 §9-0どおり |
| updated | active更新(:586)・resume(:718・version+1のupdate種) | 06 §9・05 §6どおり |
| deleted | DELETE(:733・cancelled遷移と同一トランザクション) | 06 §1どおり |
| (なし) | draft作成・draft再保存 | 06 §9-0「draft対象外」どおり |
| expired | 発行経路はM3-3(expiry_sweeper)。定数のみ存在 | 1.4へ委譲 |
| scheduled | 発行経路は後続(1.4)。定数のみ存在 | 1.4へ委譲 |

**受信側(雛形のみ)**

`backend/src/latch/worker/main.py` は起動・graceful shutdownとClock/Settingsの明示的構築のみの
雛形(実処理は本単位から)。`backend/tests/unit/test_worker.py` はClock注入と即時shutdownを検証。

**基盤**

- compose.yaml: db / redis / api / worker の4サービス。Queueに相当するサービスは未追加
- `core/db.py`(create_db_engine)・`core/clock.py`(FakeClockはset/advanceを持つ・10 第1節の時刻操作)
- integration conftest: compose常設DBへ `alembic upgrade head` → functionスコープのengine
- 運用ルール: make test-ciはapiイメージを再ビルドしない(コード変更後の検証は `docker compose build api worker` が先行必須。STATUS.md「運用ルール」4)

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

1. **Embeddingキックの実体・embedding_completed発行**(ws-2): 06 §9「作成・更新Eventの処理は
   Embedding要求のキックまで」。本単位はキックを挿入する位置(フック)のみ予約する
2. **Layer 1〜5の実処理**(ws-3〜ws-7): embedding_completedを起点とする第2段。match_candidatesの
   生成はws-3以降
3. **30分Bucket再評価・catch-upスキャンとscheduled Eventの発行主体**(ws-6): 06 §9のcatch-upは
   「Eventを発行しない直接投入」が確定済み。scheduled EventをBucketスケジューラが発行するか
   直接投入に揃えるかはws-6設計で確定する。本単位はscheduledを受信した場合の規定
   (統合せず即時・06 §9-1)のみ実装
4. **expiry_sweeperとexpired Eventの発行**(M3-3): 発行経路のみ後続。受信時の「回答待ち提案の
   クローズ」(06 §1)はlatches行が生成されないため本単位では対象外
5. **latches側の無効化**(削除Event処理のうち保留=latches candidateの無効化・06 §1): latches行は
   ws-6以降にしか生成されないため、本単位はmatch_candidatesの無効化のみ
6. **本番GCP Pub/Subの実環境構成**(M4/リリース段階): 本単位はci(エミュレータ)が対象。実環境は
   設定切替(§2.1)で接続可能な抽象にする
7. **Queue lagメトリックのalert発報**(10 §4.7・M4のObservability): lag計測基盤はCloud Monitoring
   (04 §3)。ciでは計測しない

## 2. 実装方式の選択肢と推奨

### 2.1 ci環境のMessage Queue具象 — 推奨: Google Cloud Pub/Sub エミュレータ

スーパーバイザー指示どおり本単位の主要論点。選択肢と評価は次のとおり。

| 選択肢 | 評価 |
|---|---|
| **A. Pub/Sub エミュレータ(推奨)** | 10 §1「本番と同じ種類で立てる」(確定値#11)をそのまま満たす。SDK(google-cloud-pubsub)・publish/subscribeのコード経路が本番と完全同一で、切替は環境変数のみ。後述の制約は本設計の非依存構成で回避できる |
| B. Redis Streams | 既存Redis(compose常設)を流用できコンテナ数は増えない。しかし**04 §3の選定(本番=Pub/Sub)と10 §1の原則から外れ**、ci/stagingの機能試験(10 §3)が本番構成の保証にならなくなる。SDKも本番と別経路になり2重維持 |
| C. Cloud Tasks / RabbitMQ 等 | Cloud Tasksはciに同一具象がない(エミュレータ不存在)。RabbitMQ等も04選定表で比較済みの候補外で、10 §1原則不適合はBと同じ |

**推奨Aの根拠とトレードオフ(設計書として明記すべき点)**:

- 根拠: 確定値#4(本番=Pub/Sub)と#11(本番と同じ種類で縮小)の組み合わせがci構成を直接指定している。
  疎結合・at-least-once・冪等受領という本単位の検証対象が、本番と同一のミドルウェア semantics で
  試験できる価値をコンテナ1個の追加が上回る
- エミュレータの既知制約と本設計の対応:
  - **dead letter topic等の一部機能は未対応** → 本設計はDLQを**使わない**。隔離の実体は
    match_events.status=quarantined(DB。確定値#5・#6-4)であり、再試行もWorker内カウント(§2.4)で
    実装する。04 §3が挙げるPub/Sub利点のうちDLQとsubscription宣言的retryには依らない構成
  - **インメモリ・非永続(コンテナ再起動でメッセージ消失)** → メッセージ喪失時の補償を
    フォールバックリレー(§2.2)がDBのpending行から回収するため、喪失はレイテンシ増で済み
    正しさを損なわない。この回収経路は本番のメッセージ喪失時にも同一に働く
  - **orderingはバージョン依存** → ordering非依存の設計(§2.3: 正しさはversion検査+UNIQUEが担保)のため影響なし
- コンテナイメージは `gcr.io/google-cloudsdk/cloud-pubsub-emulator`(ポート8085)を想定するが、
  イメージ名・起動引数の正確な動作確認は実装時の初期タスクとする(§5)

### 2.2 発行経路(outbox→Pub/Sub) — 推奨: コミット直後publish + フォールバックリレー

M1のoutbox(match_events INSERT)を残したまま、Queueへの publish をどうつなぐか。

| 選択肢 | 評価 |
|---|---|
| A. ポーリングリレーのみ(pending行を定期publish) | publish済みの印がなく、processedになるまで毎周期で同一Eventを再publishし続ける。updated Eventはdebounce(10秒)の間pendingのままなので再publishが繰り返され、同一versionの再受信が常態化する。実害は冪等で吸収されるが無駄打ちが構造化される |
| **B. コミット直後publish + フォールバックリレー(推奨)** | APIがuowコミット直後にpublish(失敗はログで握る)。加えて「**created_atが一定時間(既定30秒)以上前のpending行のみ**」を定期再publishするフォールバックリレーをAPIプロセス内に置く。通常時はpublish 1回で済み、APIのpublish失敗・Pub/Subメッセージ喪失・Worker長期停止を同一経路で回収する |
| C. Redisにpublish済みマーカー + ポーリングリレー | マイグレーション不要だが状態がDBとRedisの2箇所に分散し、マーカー喪失時の再publish説明など新たな運用論点を増やす。Bならマーカー自体が不要 |
| D. match_eventsへpublished_at列を追加 | outboxの標準形だがマイグレーション0003と05 §2改版が必要。共有ci-db運用とdocs改版の確認コストに見合わない( Bで要件を満たせるため) |

**推奨Bの根拠**:

1. **層別予算(確定値#13)**: 初回投入Event(作成種・窓なし)は「active化の保存コミット時点」から
   計測され、Embedding区間≤2秒にpublish遅延も含めて収まる必要がある。ポーリング周期分
   (例1秒なら平均0.5秒)を消費するA/C/Dより、コミット直後publishが遅延最小
2. **マイグレーション不要・docs改版不要**: スーパーバイザー補足(alembicは0002がhead・追加は原則不要)
   を満たす。状態はmatch_events.statusの3値のみ(確定値#5)に保てる
3. **04 §2の構成図(Intent API → DB保存 → Event発行 → Pub/Sub)に忠実**: 発行者はAPI
4. **補償の一元化**: publish失敗・メッセージ喪失・Worker停止中の滞留のいずれも「30秒超のpending行」
   という単一条件で回収する。フォールバックの周期は5秒・しきい値は30秒を既定とし設定化する
   (debounce 10秒+処理< 通常処理はしきい値に到達しない=通常時の再publishゼロ)

なお、publishはuowトランザクションの**外**(コミット後)で呼ぶ。コミット失敗ならEvent行ごと
存在しないためpublishされず、publish失敗ならEvent行がpendingとして残りフォールバックで回収される。
この順序で「保存が成功した行に必ずEvent行が伴う」(M1 events.pyの原子性)は変わらない。

### 2.3 Worker第1段の配置 — 推奨: Workerプロセス内のsubscribe+debounce、正しさはversion検査とUNIQUEに分離

- **受信**: workerコンテナ(compose常設・10 §1のWorker 1)がPub/Sub subscriptionをストリーミング
  pullで消費する。WorkerのDI構造(既存 `worker/main.py` のclock/settings明示構築)を踏襲し、
  bus・engineを追加注入する。テストではFakeBus/実エミュレータbusを注入(§4)
- **debounce(更新Eventのみ・トレーリング窓10秒)**: Workerプロセス内のasyncio実装とする。解放時刻は
  「未知のversionを持つEventの到着時刻(Clock.now()) + 10秒」とし、判定はすべてClock基準
  (確定値#12: debounceの10秒窓はClock操作で再現)。窓解放時にグループ内の最新versionのみ処理し、
  窓に吸収された古いversionの行は「統合済み」の破棄理由でprocessedへ閉じる(05 §2のstatus 3値の
  範囲内。確定値#6-1の「1回の評価に合流する」を行単位で表現したもの)
- **同一versionの重複受信は窓を延長しない**: at-least-once配信とフォールバックリレーの再publishに
  よる同一Eventの再受信は、06 §9-1の統合対象(「更新という状態変化」)ではないため、トレーリング窓を
  リセットしない。これにより再受信が窓を永久に延長する競合を構造的に排除する
- **debounceは最適化であり、正しさの担保は別置き**: 複数Worker(本番の水平スケール・04 §6)で同一
  IntentのEventが別Workerに散らばった場合、窓統合は最適に働かないが、処理結果の正しさは
  version検査(古いEventは破棄)とUNIQUE制約(二重処理排除)がDBで保証する(確定値#2・#5)。
  本番で統合精度を上げるPub/Sub ordering key(source_intent_id)の有効化は本単位では行わず、
  将来のスケール時の課題とする(YAGNI。§2.7)
- **ackは処理完了後**: メッセージのackは「DBのstatus遷移コミット後」に行う。ack deadlineは
  600秒(最大値)で設定し、debounce 10秒+再試行バックオフ最大31秒+処理が収まる。Workerクラッシュ時は
  未ackメッセージの再配信(at-least-once)が回収する。nack による再試行は使わない(次項)
- **再試行はWorker内ループ**: 処理失敗(db接続エラー等の一時障害・payload不正・version乖離)は
  Worker内でバックオフ(1, 2, 4, 8, 16秒)を挟み5回再試行し(確定値#6-4)、失敗理由をpayloadへ追記して
  status=quarantined+ackで閉じる。カウントはプロセス内(メモリ)とし、Worker再起動でリセットされる
  (再配信から数え直し。quarantined+ackで必ず終端するため無限ループにはならない)。Pub/Sub側の
  宣言的retryへの依存はエミュレータ制約(§2.1)と試験の決定性の両面で避ける

**冪等受領(行の確保)**: Workerは受信メッセージ(3点組 event_type, source_intent_id, version)で
match_events行を `INSERT ... ON CONFLICT DO NOTHING` → 既存ならSELECT で確保する。これにより
(a) API経由(pending行あり)はその行を、(b) テストパブリッシャーからの直接publish(行なし。10 §1)は
受信時に挿入した行を、同一の経路で処理する。確保した行のstatusがpendingでなければ(=処理済みまたは
隔離済み)即ackして終わる(at-least-onceの重複排除。確定値#5)。処理中の競合は行のFOR UPDATEで直列化する。

### 2.4 version検査と破棄・隔離の区別

確定値#6-3・#8を次のフローとして実装する。

```text
メッセージから3点組(event_type, source_intent_id, version)を抽出
  → 抽出不能(version欠落・型不一致) → 受信生payloadのまま行を挿入して構造違反経路
    (再試行5回 → 失敗理由を保持して quarantined+ack。行のUNIQUEキーは組めないため
     payload->>'version' はNULLとなり、UNIQUE制約は複数の毒ペイロードを妨げない — 意図した挙動)
  → 抽出可なら行確保(INSERT ... ON CONFLICT DO NOTHING → 既存はSELECT・FOR UPDATE)
  → event_typeが6値外 → 構造違反経路(同上)
  → 参照先IntentをSELECT:
      行なし(削除済み等の不在)      → 正当な遅延Event。discard_reasonをpayloadへ追記して processed+ack
                                     (quarantinedにしない — 隔離滞留と誤alertの爆発防止。確定値#8)
      payload v < intents.version   → 古いEvent。discard_reason="stale_version"で processed+ack
      payload v > intents.version   → FOR UPDATEで再読込(読み取り遅延を疑う)
                                       → 一致すれば処理へ / 乖離が続けば再試行(5回)→ quarantined
      payload v = intents.version   → 種別処理へ
```

- 破棄理由のpayload追記は、UNIQUEインデックスが参照する `payload->>'version'` を変更しない
  (discard_reason は別キーを追加。確定値#5のUNIQUEは不変)
- 削除(DELETE API)はcancelled遷移のため行が残りversion検査を通る。「行なし」は主に10 §4.7の
  試験注入と異常系で発生するが、処理は上記のとおりprocessed破棄に統一する(06 §9の「不在」の文言どおり)

### 2.5 種別処理(ws-1時点の実装範囲)

| event_type | debounce | ws-1での処理実体 |
|---|---|---|
| created | なし(即時・06 §9-1) | version検査後、**Embedding要求フックを呼ぶ位置のみ予約**してprocessed。フックの実体(embedding IS NULL時のキック)はws-2 |
| updated | あり(10秒トレーリング窓) | 同上。窓解放時の最新versionのみ処理 |
| deleted | なし(即時) | **match_candidatesの無効化**: `UPDATE match_candidates SET status='closed' WHERE (intent_a_id=:id OR intent_b_id=:id) AND status <> 'closed'`(06 §1「候補の無効化・Layerを経ない」)。行が0件でも正常(候補生成はws-3以降)。latches側は1.4-5のとおり後続単位 |
| expired | なし(即時) | version検査を経てprocessed(「回答待ち提案のクローズ」の実体はlatches不在のため後続。受信規定のみ) |
| scheduled | なし(即時・06 §9-1) | 同上(発行主体はws-6設計で確定。1.4-3) |
| embedding_completed | なし(即時) | ws-2が発行する派生イベント。**event_typeの値域には本単位から含める**(6値。05 §2)が、処理実体(第2段)は後続。受信時はversion検査を経てprocessed |
| (6値外) | — | 構造違反として2.4の再試行経路(5回)→ quarantined |

### 2.6 マイグレーション・docs改版 — 追加なし

- alembicは0002のまま。match_events(0001)・UNIQUE索引(0001)・match_candidates(0001)で本単位の
  全機能が成立する。**マイグレーション追加の理由は発生しなかった**(§2.2で案Dを採らなかった理由)
- docs(01〜12)の改版は不要。§2.3〜2.5の実装規定(窓吸収行のprocessed閉包・同一version再受信の
  窓延長なし・行なし受領時のINSERT確保)は、いずれも06 §9・05 §2の確定値の実装解釈の範囲内と
  判断する(確定値が直接定めない細部はstatus 3値・UNIQUEの意味を壊さない運用に収めている)

### 2.7 採用しないもの(YAGNIによる切り捨て一覧)

1. **Pub/Subのdead letter topic**(隔離はDB側quarantinedが実体。エミュレータ未対応機能に依らない)
2. **subscriptionの宣言的retry設定**(Worker内再試行で完結。§2.3)
3. **Pub/Sub ordering key**(Worker 1のciでは不要。正しさはversion検査+UNIQUEが担保)
4. **publish済みマーカーのRedis/DB保持**(§2.2-Bで不要)
5. **テストパブリッシャーCLI**(10 §1の「テスト用パブリッシャー」はpytestからbusを直接呼ぶ
   FakeBus/実busで満たす。製品にCLIは入れない)
6. **debounce状態のRedis外部化**(Worker 1ではプロセス内で十分。外部化は水平スケール時の課題)
7. **exactly-once配信の追求**(確定値#4: idempotency keyで冪等化する設計思想に従う)

## 3. ファイル構成

### 3.1 作るもの

```text
backend/src/latch/events/__init__.py
    # モジュール公開API(make_event_bus等)
backend/src/latch/events/bus.py
    # EventBus Protocol(ポート): publish_match_event(event_type, intent_id, version)
    #                          / subscribe(on_message) → Subscription(stop・ack)
    # IncomingEvent(message_id・3点組・ack()。テストパブリッシャー直投入とAPI経由の
    # 両方を同じ形で扱う)
backend/src/latch/events/pubsub_bus.py
    # google-cloud-pubsub実装。topic/subscriptionのensure(存在なければ作成・
    # ack_deadline=600秒)・publish(json 3点組)・ストリーミングpull。
    # settings.pubsub_emulator_host 非空ならエミュレータへ(SDKはPUBSUB_EMULATOR_HOST)。
    # API・Workerの両プロセスから同じクラスを使う
backend/src/latch/events/relay.py
    # フォールバックリレー(§2.2-B): created_at < now-30秒のpending行を5秒周期で
    # 再publish(publishはEventBusへ。SELECTはLIMIT付き・時刻はClock)
backend/src/latch/worker/debounce.py
    # TrailingDebouncer: source_intent_idごとの窓管理。解放時刻=未知version到着時刻+10秒
    # (Clock基準・tickは短周期のasyncioループでClock.now()と比較=実時間を参照しない)。
    # 同一versionの再提出は延長しない。解放時コールバックへグループ(最新version+吸収行)を渡す
backend/src/latch/worker/stage1.py
    # 第1段処理: 行確保(ON CONFLICT DO NOTHING)・payload検証・version検査(§2.4)・
    # 種別処理(§2.5)・再試行ルール(5回・バックオフ)・quarantined/processed遷移
    # (SQLはintents/events.py・store.pyと同じtext()生SQL形式・時刻はClock明示値)
backend/tests/unit/test_worker_debounce.py
    # FakeClock+短tickで窓統合・即時種別・再受信非延長・窓吸収行のprocessed閉包
backend/tests/unit/test_worker_stage1.py
    # version三分岐・行なし破棄・payload不正→quarantined・再試行5回・deletedの無効化SQL
    # (DB接続不要な範囲はスタブconn・SQL文字列はintegrationで担保)
backend/tests/unit/test_events_relay.py
    # 再publish条件(30秒超のpendingのみ)・publish失敗の握り
backend/tests/integration/test_events_pipeline.py
    # 実エミュレータ+実DBでのE2E(§4.2)
```

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更 |
|---|---|
| `backend/src/latch/intents/service.py` | uowコミット後にpublish呼び出しを追加(EventBusを注入・publish失敗はログで握り例外にしない)。発行箇所・event_typeは現状のまま |
| `backend/src/latch/main.py` | lifespanでEventBus構築とフォールバックリレー起動を追加(既存の「サービスごとの独立スキップ判定」へ1項目追記) |
| `backend/src/latch/worker/main.py` | run()にsubscribe開始・debouncer・stage1配線を追加(既存のclock/settingsシグネチャとgraceful shutdownは維持) |
| `backend/src/latch/settings.py` | pubsub_project_id / pubsub_topic_match_events(既定"match-events") / pubsub_subscription_match_events(既定"match-events-sub") / pubsub_emulator_host(既定""=実GCP) / pubsub_ack_deadline_sec(600)/ event_fallback_relay_after_sec(30)/ event_fallback_poll_sec(5)/ event_debounce_window_sec(10)/ event_retry_max(5) |
| `compose.yaml` | pubsubサービス(エミュレータ・127.0.0.1:8085)追加と api/worker への LATCH_PUBSUB_EMULATOR_HOST 設定 |
| `backend/pyproject.toml` | 依存へgoogle-cloud-pubsubを追加 |
| `Makefile` | test-ciでworkerコンテナ停止を挟み、試験後に復帰させる(§4.2の競合対策。`docker compose up -d --wait && docker compose stop worker && …pytest…; docker compose start worker` の形。pytestの成否にかかわらずworkerは復帰) |
| `backend/tests/unit/test_worker.py` | Worker依存追加に伴う最小追従(既存3試験の意図は維持) |

### 3.3 触らないもの

- `backend/alembic/`(0002がheadのまま・§2.6)
- `backend/src/latch/intents/events.py`(outbox INSERTと5種定数は不変。embedding_completed定数の追加はws-2)
- `backend/src/latch/`配下の auth / users / ratelimit / geo / llm / g1gate 各モジュール
- `frontend/`・`prototype/`
- `docs/`(01〜12・本設計で改版対象なし)

## 4. テスト方針

### 4.1 unit(`make test`。外部プロセス不要・FakeClock+スタブで決定的)

| 試験 | 内容 |
|---|---|
| debounce | 窓内に同一intentのupdated v2→v3→v4を投入しClock.advance(10秒)で解放 → 処理はv4のみ・v2/v3は統合理由でprocessed。created/deletedはdebounceを経ない即時。同一version再受信で解放時刻が延びない。窓解放がClock操作のみで再現(実時間待ちなし) |
| stage1 | version三分岐(<・=・>)と>のFOR UPDATE再読込後の合流。行なし受領→processed+discard_reason。payload不正(version欠落・6値外event_type)→5回再試行→quarantined+理由記録。再試行バックオフの進行はFakeClock |
| relay | 再publish対象が「30秒超のpending」のみであること(未満の行は対象外)。publish例外で中断せず次周期へ |
| 冪等受領 | pending以外のstatusの行は処理せずackのみ(重複受領の排除) |

### 4.2 integration(`make test-ci`。compose常設+実エミュレータ+実DB)

**前提と環境**: composeへpubsub-emulatorを追加。`docker compose build api worker` を先行
(運用ルール4)。**test-ci実行中は `docker compose stop worker` で常設workerを停止する**(Makefileに
組み込む)— 常設worker(SystemClock)とテストプロセス内Worker(FakeClock)が同一subscriptionを
消費してDB更新・debounce時刻の主導権を奪い合う競合を防ぐため。Worker処理の検証はすべてテスト
プロセス内Worker(bus=実エミュレータ・clock=FakeClock・engine=実DB)が担う。workerコンテナが走らせる
コードは同一(worker/main.py)であり、コンテナ起動自体は `make up`/`ps` で確認する。

| 試験 | 内容 | 対応確定値 |
|---|---|---|
| E2E(作成) | api_client(実HTTP)でPOST /v1/intents(active)→APIがpublish→テストWorkerが受信→match_eventsがprocessed。メッセージ内容(3点組)も検証 | #2・#13 |
| debounce統合 | PATCH 2回→窓統合→Clock.advance(10秒)で最新versionのみ処理・全行processed | #6-1 |
| draft→active | draft作成(発行なし)→active化→作成種Eventが即時処理 | #6-0 |
| 削除Event | fixtureでmatch_candidates行を直接INSERT→DELETE API→該当行がclosed | #7 |
| 参照先不在 | テストパブリッシャー(bus直接publish・10 第1節)で存在しないintent_idのEvent→processed+discard_reason(quarantinedにならない) | #8・#14 |
| 毒ペイロード | version欠落・6値外event_type→5回再試行(実ログで試行を確認)→quarantined+理由保持。後続Eventの処理が滞らない | #8・#14 |
| 重複投入 | 同一3点組を2回publish→処理1回(2回目はackのみ)。match_candidates二重生成なしの土台 | #5・#14 |
| resume | pause→resume→version+1のupdate種→debounce経由で処理 | #9 |

**試験間のメッセージ干渉対策**: エミュレータのsubscriptionには前試験の残余メッセージが残り得る
(常設worker停止中のフォールバックリレーによるpublish含む)。functionスコープのfixtureで試験開始時に
subscriptionの滞留をdrain(空引き)してからWorkerを起動する。試験のWorker subscriptionは
topicは共通・subscription名は試験専用にするとdrainが安全になる(実装計画で確定)。

### 4.3 後続単位への接続試験(本単位では実施しない)

- 冪等性のG2試験(同一Event2回投入でmatch_candidates二重生成なし・10 §4.7)はmatch_candidates生成
  (ws-3以降)が前提。本単位は「処理1回」の土台のみ検証(§4.2)
- 縮退・障害注入(ws-8)・Queue lag alert(M4)も本単位の範囲外(1.4)

## 5. 未解決の論点・実装時確認事項

**設計判断として未解決のものはなし**(主要論点のMessage Queue具象は§2.1で確定値#4・#11を根拠に
決定し、トレードオフを明記した)。実装時の確認事項は次のとおり。

1. **エミュレータイメージの動作確認**(§2.1): `gcr.io/google-cloudsdk/cloud-pubsub-emulator` の
   現行タグと起動引数(`gcloud beta emulators pubsub start --host-port=0.0.0.0:8085`)、
   google-cloud-pubsub SDKのエミュレータ接続(PUBSUB_EMULATOR_HOST)の組み合わせを最初に確認する。
   万一イメージ供給が止まっている等の場合はミラー鏡像を探索のうえ supervisorへ報告する
2. **フォールバックしきい値30秒・周期5秒・ack_deadline 600秒の初期値**: 06 §9-10に準じ
   設定値として実装し、調整は計測(将来のQueue lag)で行う
3. **試験subscriptionのdrain実装**(§4.2): subscription破棄/再作成か空引きかは実装計画で確定する
