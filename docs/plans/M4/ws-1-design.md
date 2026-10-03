# M4 ws-1(staging・load環境構築)設計メモ

- 作成: 2026-10-03(agent1)
- 前提: M3完了(m3-g3fix込み・マージ後main test-ci 1480 passed・head=0006)。
  M4単位表・設計前提(2026-10-03承認): staging・load・ダッシュボードはローカルdocker composeの
  別プロジェクト・クラウド不採用・LLMはスタブ+レイテンシ注入で実課金ゼロ
- 参照仕様: 10 第1節(テスト環境構成・時刻操作・疑似イベント注入・認証のテスト構成)・
  10 第4節・第5節・12 M4-1・04 第5節(FR-41 Clock適用範囲)・05(認証のテスト鍵)・
  06 第9節(Event処理)・08 §2.4(ログ許可リスト)・11(フェーズ接続)
- 本単位はマイグレーション追加なし。共有ci-dbの運用ルール1〜3(alembic_version取り合い)の
  対象外で、本単位はtest-ciを通常どおり実行できる(staging/loadのDBは独立ボリュームのため
  共有DB問題そのものが発生しない — §2.1)
- **確定**: supervisor回答(2026-10-03・4件+補足)を反映済み。承認事項の一覧は§5。
  未解決の論点なし
- 消費者: ws-3(27項目ハーネス)・ws-6(負荷・総当たり)・ws-7(障害系)・ws-9(FCM実機)。
  要求の先回り一覧を§1.3に置く
- 外部SDKを扱わない(FCM・LLMともstubのみ)。context7確認は不要

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M1〜M3の機能が「ci環境(in-process試験)」でグリーンになった状態から、
**常設の実コンテナ環境(staging)と隔離の負荷試験環境(load)**を立て、後続単位が
27項目のstagingフル実行(ws-3)・性能5目標と総当たり(ws-6)・障害系(ws-7)・FCM実機(ws-9)を
「器の追加実装なし」で実行できる状態にする。作るものは3つ:

1. **環境の器**: staging・loadのcompose(別プロジェクト・別ボリューム・別ポート)
2. **テスト用注入経路**: Clock制御(10 §1「時刻操作」)・疑似イベントパブリッシャー(「疑似イベント注入」)
3. **認証のテスト構成のstaging版**: staging専用鍵ペアの注入+JWT発行(M0 ws-3資産の再利用)

実装コード(製品ロジック)への変更はClock組立の追加がほぼ全てで、API/Worker/パイプライン本体には触らない。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | 環境は3つ。**staging=縮小構成(API 1〜2・Worker 0〜2・単一ゾーン)**で27項目機能検証+非機能スモーク。**load=隔離環境**で負荷試験・総当たり検出(02#5本格検証) | 10 第1節表 |
| 2 | 本番と同じ種類(PostgreSQL+PostGIS+pgvector / Pub/Sub / Redis / コンテナ基盤 / FCM / Cloud Monitoring)で規模を縮小 | 10 第1節・04技術スタック |
| 3 | 時刻参照はすべてClockインターフェース経由で、**staging・loadではテスト用のClock制御を注入できる**。適用範囲6項目=(1)APIの期限判定(response_deadline・expires_at比較・DBのclock_timestamp()不使用)(2)Intent期限バッチ・LATCH期限切れバッチ(60秒周期)(3)30分Bucket再評価のBucket境界(4)debounceの10秒窓(5)JST 0時リセットジョブとRedisカウンタのJST日付キー(6)作成・更新の時刻検証(過去不可・+7日上限)。expires_at・response_deadline・30分Bucketの前進はClock操作で再現し実時間の待ちを排除 | 10 第1節「時刻操作」・04 §5(FR-41適用範囲と同一) |
| 4 | 疑似イベント注入: **Match Event 5種(01第5節)+派生イベントembedding_completedを手動発火できる**。LLM呼び出しはLLM Gatewayのテストモード(記録済み応答スタブorレイテンシ注入)で決定的。FCMはドライラン(送信内容を記録のみ)・通知到達と表示規制(FR-21)は記録で検証 | 10 第1節「疑似イベント注入」 |
| 5 | Match Event 5種=内部由来の**作成・更新・削除・期限切れ・指定時刻の到来**(expired・scheduled)。実装済みのEvent種別6値(created/updated/deleted/expired/scheduled/embedding_completed)と一致 | 01 §5・intents/events.py |
| 6 | 認証のテスト構成(D-21): 本番と同じJWT検証経路(JWKS参照)を通しつつ、**staging専用の鍵ペアを注入し**、テストユーザーのJWTを発行する内部ツールを用意。実IdP(Google/Apple)に依存しない | 10 第1節「認証のテスト構成」 |
| 7 | 性能試験の負荷条件: Intent保存API 50rps・Match Event 100イベント/秒バースト・Jevレイテンシ注入(p50 2秒/p95 5秒=フォールバック切替時上限の検証)・Parserレイテンシ注入(p50 1秒/p95 2.5秒)。測定はレイテンシ分布(本番=Cloud Monitoring) | 10 §4.1 |
| 8 | 可用性: **稼働中のAPIコンテナを1インスタンス強制停止し、残存インスタンスでの処理継続と自動復旧を確認**。合成監視プローブ(1分間隔)で成功率99.5%以上 | 10 §4.3 |
| 9 | 総当たり検出: Active Intent数を1万→5万→10万と段階的に増加させ同一のイベント注入で観測。Vector Retrieval件数がK_v=50以内でIntent数と無関係に安定 | 10 §4.2 |
| 10 | 障害系: バックアップから新しいDBインスタンスへ復元し再エンベディング(cross-checkでactive Intent数一致)・D-16上限到達(**月初境界はClock操作で進める**)・resume/Bucket遅延のClock操作での回収・毒ペイロード注入(5回再試行→quarantined)・Queue lag alert発報 | 10 §4.4・§4.5・§4.7 |
| 11 | 証拠: 試験ごとに試験ID・実施日・環境・入力条件・合否とともにdocs/testassets/results/へ保管。staging・loadのObservabilityダッシュボードからのメトリクスとトレースで代替・補完してよい | 10 第5節 |
| 12 | 時刻の参照はすべてClock経由で、arch test(test_arch_no_direct_time.py)が毎コミットで強制。**実時間の直接参照が許されるのは core/clock.py のみ**(ALLOWED=単一ファイル) | 04 §5・test_arch_no_direct_time.py:27 |
| 13 | リセットジョブはClockのJST日付境界を参照し、テスト環境ではClock操作で0時・月初を再現。ジョブ失敗時は最初のJev実行要求で前日以前の日付キーを検知して自己修復 | 04 §5 |
| 14 | Event処理: 失敗は5回再試行→失敗理由保持でmatch_events.status=quarantinedへ隔離。削除済みIntentへの参照Eventはquarantinedでなくprocessedで破棄。UNIQUE制約で同一Eventの重複処理を排除。idempotency key=(event_type, source_intent_id, version) | 10 §4.7・06 §9 |
| 15 | apiイメージはciと同一(backend/Dockerfile・uv sync --frozen)。dbイメージはdocker/postgres(postgres:17+PostGIS+pgvector) | compose.yaml・docker/postgres/Dockerfile |
| 16 | ClockはDI可能: create_app(clock=…)・Worker(clock=…)とも未注入時にSystemClock。/healthのserver_timeがClock由来(get_clock())で「Clock差し替えが全経路で効くことの生の消費者」 | main.py・worker/main.py |
| 17 | テスト用認証資産は既存: 同梱testkeys(RS256・kid=test-idp-1・ci/試験専用・prodは起動ガードで拒否)・`python -m latch.auth issue-idp-token --provider google --subject <sub> --private-key <pem>`・`gen-keypair --out-dir <dir>`(READMEに「staging専用ペアの注入にも使用」と明記) | auth/testkeys/README.md・auth/tools.py |
| 18 | Event発行の公開IFは`publish_match_event(event_type, intent_id, version)`の3点組(publish形式は実装の契約)。運用CLIは`python -m latch.events`(purge-match-subが実装済み) | events/bus.py・events/__main__.py |
| 19 | stubのレイテンシ注入はsettingsに既存(llm_stub_delay_parser_ms / _embedding_ms / _jev_ms・push_stub_delay_ms・既定0=無効)。コメントに「p50/p95分布はM4でスタブ内で拡張」 | settings.py |
| 20 | push_mode=stubが既定(ドライラン・構造化ログlatch.push.sendへ記録)。real(実FCM)はws-9・未実装値指定はValueError | settings.py |

### 1.3 消費者の要求の先回り(単位表+10 §3〜§5から列挙)

| 消費者 | 要求 | ws-1が提供する器 |
|---|---|---|
| ws-3(27項目・stagingフル実行・G3繰越02#13〜#23再実行) | stagingへのシード投入・テストユーザーのJWT・API疎通・Clock操作(期限系の項目。正確な振り分けはws-3設計で02 §4・10 §3と突合)・イベント注入・通知ドライラン記録の取得・DB/Redis記録の確認 | staging compose(api2+nginx・db・redis・pubsub・worker)・make staging-up/down/migrate/geo-import・staging-token(JWT発行)・staging-clock(set/advance/show)・staging-publish(6種手動発火)・push stub=構造化ログ(docker logsで取得可) |
| ws-6(性能5目標・総当たり) | 50rps/100evt/sを受け止めるapi・Event注入の高速連投(load宛)・stubレイテンシ注入の切替・層別トレースの前提・他環境から隔離されたDB/Redis | load compose(stagingと同種構成・完全隔離)・stub delay系env(composeで明示可)・隔離の保証(独立プロジェクト・ボリューム・ポート) |
| ws-7(可用性・バックアップ復旧・縮退・Queue障害) | apiインスタンスの強制停止(kill)と自動復旧(restart policy)・残存インスタンスでの継続(LB/複数台)・worker全停止(0台)・毒ペイロードの注入・Clock操作で月初/Bucket遅延を再現・バックアップ取得と復元先DBの立てやすさ | staging/loadのapi 2台+nginx(§2.3)・worker台数のstop/start操作・staging-publish --raw-payload(毒)・Clock操作・pg_dump取得手順(復元先DBインスタンスの追加はws-7設計で) |
| ws-9(FCM実機) | stub→real切替の経路・認証情報の設定場所・stagingでの送信確認 | push_modeの切替はsettings既存経路・staging composeのenvに切り替え余地を確保(ws-9設計時に実値を人間領域から設定) |

### 1.4 既知欠陥(compose workerのLATCH_DATABASE_URL不在)の現状 — 重要な前提修正

単位表には「既知欠陥(compose workerのLATCH_DATABASE_URL不在)解消」があるが、
**この欠陥は既に解消済み**(修正コミット9268a73・2026-09-30ユーザー指示。
compose.yaml workerサービスにLATCH_DATABASE_URL/LATCH_REDIS_URL/LATCH_PUBSUB_EMULATOR_HOSTが
定義済み。STATUS.md M2 ws-8記録の「環境の既存欠陥(ユーザー報告済み)」の後対応)。

本単位での扱いは**再発防止と検証**に置き換える: (a)新規staging/load composeの全サービスで
接続envを網羅する(YAMLアンカー共通化)・(b)完了条件に「workerコンテナの実稼働確認
(DB接続・subscription作成までのログ)」を含める(過去のci検証はin-process workerのため
この種の欠陥を検出できなかった — STATUS M2 ws-1の実例)。

## 2. 実装方式の選択と推奨

### 2.1 環境の分離方式 — 別ファイル+別プロジェクト(**2026-10-03 supervisor承認**)

| 案 | 内容 | 評価 |
|---|---|---|
| A. 既存compose.yamlへdocker composeの`profiles:` | 同一ファイルにstaging/loadサービスをprofile分け、`--profile staging`で起動 | ci環境(毎コミットで安定が要る常設)と試験環境定義が同居し、ci側改変リスクが常時ある。運用ルール4(make up/compose up)との相互作用の検証コストも不要にはできない |
| **B. 別ファイル+別プロジェクト名(推奨)** | `compose.staging.yaml`(`-p latch-staging`)・`compose.load.yaml`(`-p latch-load`)を新設。ciのcompose.yamlは一切触らない | 設計前提(ローカルdocker composeの**別プロジェクト**)に直接忠実。プロジェクト名が違えばコンテナ名・ネットワーク・ボリューム名が自動で分離する(docker composeの命名規則)。**DB独立ボリュームのため運用ルール1〜3のalembic_version取り合いが構造的に発生しない** |

単位表の「composeプロファイル分離」は**B案の実現方式の意図と確定**(2026-10-03 supervisor裁定)。
docker composeの`profiles:`機能そのものは使わない。根拠: (a)ciのcompose.yaml(毎コミットで
安定が要る常設環境)を一切触らない・(b)設計前提の「ローカルdocker composeの**別プロジェクト**」
に直接忠実・(c)プロジェクト名が違えばコンテナ名・ネットワーク・ボリュームが自動で分離し、
**DB独立ボリュームのため運用ルール1〜3のalembic_version取り合いが構造的に発生しない**。
イメージはciと同一Dockerfileからプロジェクト別にbuildされ(latch-staging-api等)、
マシン内に共存する。

**ポート割当**(全て127.0.0.1のみ公開・ci現行値との衝突回避。以後の単位はこの表に従う):

| サービス | ci(現行) | staging | load |
|---|---|---|---|
| nginx(入口) | — | 18000 | 28000 |
| api(直) | 8000 | 18001 / 18002 | 28001 / 28002 |
| db(postgres) | 5432 | 15432 | 25432 |
| redis | 6379 | 16379 | 26379 |
| pubsubエミュレータ | 8085 | 18085 | 28085 |
| プロジェクト名 | latch-ci | latch-staging | latch-load |
| pubsub project_id | latch-ci | latch-staging | latch-load |

ホスト側のCLI(make staging-migrate・staging-geo-import・staging-publish・staging-clock)は
`LATCH_DATABASE_URL=postgresql+asyncpg://...@127.0.0.1:15432/latch` 等のenv上書きで
staging/load宛てに向ける(settingsは全部env_prefix上書き可能なため、CLI・APIとも既存コードで
宛先変更が効く — M0以来の規律)。

### 2.2 Clock制御注入 — Redis共有の制御Clock(推奨)

**論点の所在。** 10 §1の「テスト用のClock制御を注入できる」は、ci試験のFakeClock
(プロセス内・テスト専用)とは異なり、**常設のapi/workerコンテナプロセスに対する注入**を要求する。

現状のClock DI(create_app・Workerのclock引数)は「起動時に渡す」形で、compose起動のプロセスには
SystemClockが入る。さらにstagingはapi 2台+worker 1〜2台の**複数プロセス構成**であり、
プロセス内FakeClockでは時刻がプロセスごとに食い違い、期限判定・sweeper・debounce・日付キーの
整合が崩れる。したがって複数プロセスで同一の時刻を見る「共有Clock」が本質要件。

| 案 | 内容 | 評価 |
|---|---|---|
| A. Redis基準の制御Clock(推奨) | Clock実装の第三構成員を追加: Redis 1キーに「基準点(実時刻→仮想時刻の対応)」JSONを置き、now()=仮想基準点+(実時刻経過)。set/advanceは基準点書き換え。全プロセスが同一キー参照 | 追加依存ゼロ(Redisは10 §1の構成に本来在る)・**プロセス再起動不要の操作**・操作はホスト側CLIから1コマンド・api/worker複数台で自動的に一致・キー不在=オフセット0=実時間なので「制御していない状態」がsystemと等価。now()あたりRedis GET 1回のコストはある(§4のテスト方針で対処を規定) |
| B. 管理エンドポイント経由のインプロセス制御 | apiに管理APIを追加し時刻を書き換える | workerプロセスが共有できず要件を満たさない。管理APIは製品契約の汚染でもある |
| C. 環境変数で初期時刻を注入+コンテナ再起動 | LATCH_CLOCK_INITIALをsetのたびに書いてupし直す | 操作のたびに再起動が要り、常設環境の観察・負荷試験中の操作ができない。30分Bucketの逐次前進等の試験が実用にならない |

**設計**(案A):

- `ControlledClock`(clock.py内に実装 — 引用確定値#12のarch test許可はclock.py単一ファイルなので
  実時間参照もこの場所で合法):
  - Redisキー: `latch:clock:control`(ci環境のRedisとは別インスタンスなので名前衝突なし・TTLなし)
  - 値(JSON): `{"real": <基準の実時刻unix秒float>, "virtual": <基準の仮想時刻unix秒float>}`
  - `now()` = `virtual + (datetime.now(UTC) - real)`。**rate(速度倍率)は設けない** —
    sweeper/BackfillRunner/ReevalRunnerの待機はasyncio.sleep(実時間)であり、rateでは速くならない
    (Clock操作で期限を未来へ動かすのが10 §1の規定手段)。YAGNI
  - `set(時刻)`=基準点を書き換え(後退可 — FakeClockのsetと同一契約。「過去不可」境界試験の
    両方向に対応)・`advance(delta)`=仮想基準点を前進
  - **キー不在は実時間扱い**(オフセット0)・**Redis到達不能は例外**でfail-closed
    (「制御下にあるつもりが実時間」の静かな誤りを許さない。stagingのRedisは常設restart: unless-stopped)
  - 計算部分は純関数に分離しunit試験は決定的に行う(now()の薄いラッパ部分のみ実時間に依存)
- `make_clock(settings)`(clock.py): `clock_mode: "system"(既定)/"controlled"` で
  SystemClock/ControlledClockを選ぶ組立ヘルパー。`create_app()`と`worker/__main__.py`の
  既定構築をこれへ差し替える(既存DI試験はclock引数明示なので無傷)
- 操作CLI(`python -m latch.core.clock_cli`新設): `show`(現在の制御状態と仮想時刻)/
  `set <ISO8601>`/`advance <秒>`。宛先Redisは`LATCH_REDIS_URL`のenv上書きでstaging/load宛へ。
  Makefileターゲット`staging-clock`でラップ
- FakeClock資産との差分まとめ: FakeClock=プロセス内・テストコード専用・自由に進め/戻せる。
  ControlledClock=プロセス横断共有・永続・**実時間レートで自然に進行**・DB保存済み行の時刻
  (created_at等もClock由来)との整合は試験作成者の作法に委ねられる(set後は新規データ投入。
  後退後は既存行のcreated_atが未来になる等の矛盾が起きうる — ws-3設計へ引継ぎ)

ci環境には影響しない(clock_mode既定system・ci composeにenv追加なし)。

### 2.3 staging・loadのサービス構成 — api 2台+nginx・worker 1〜2台(**2026-10-03 supervisor承認**)

**論点の所在。** 10 §1はstagingを「API 1〜2・Worker 0〜2」としており台数に幅がある。
10 §4.3の可用性試験(確定値#8)は「**1インスタンス強制停止→残存インスタンスでの処理継続**」を
要求し、これは2台常時が前提の試験である。また04の実行基盤はクラウドのコンテナ基盤
(オートスケール・ヘルスチェック・マルチAZが標準機能)で、本番の入口はHTTPS LBありき
(04・11の構成)。

| 案 | 内容 | 評価 |
|---|---|---|
| **A. api 2台+nginx前段(推奨)** | api1/api2を別サービス定義(YAMLアンカーで共通化)・nginx:alpineを前段入口とする。proxy_next_upstreamで死んだ1台への振りをもう1台へ再送(passiveヘルスチェック) | 10 §4.3の試験が素直に成立(入口1つで1台強制停止→継続)・本番構成(LB+オートスケール)のローカル近似として「本番と同じ種類」に合致・新しいイメージの持ち込み(nginx)が1つ増える |
| B. api 2台・LBなし(クライアント側フェイルオーバー) | プローブやハーネスが2ポートを直叩きし「どちらか応答すれば成功」と定義 | 道具側に振り分け責務が移り試験ごとに車輪の再発明・本番の入口構造と異なり可用性の証拠として弱い |
| C. api 1台固定 | 「1〜2」の1を取る | 10 §4.3の試験が原理的にできない(残存インスタンスが存在しない) |

workerはworker1/worker2の2定義(stopで0〜2台を操作。複数台同時稼働の重複実行は既存の冪等
ガード〔SKIP LOCKED・UNIQUE・reeval 30分・Backfill/embedding済みスキップ・Retention/Resetの
冪等〕で吸収する設計 — staging受け入れ確認で2台同時稼働を実際に確認する)。

load環境はstagingと**同種構成**にする(api2+nginx・db・redis・pubsub・worker)。
根拠: 10 §3の振り分けで非機能(負荷・障害系)はload環境が担い、単位表ws-7はws-6と環境共有
のためws-7の可用性試験がloadで実施される → api 2台+nginxはloadにも必須。「隔離」の実体は
プロジェクト・ボリューム・ポート・テストデータの完全分離と、負荷試験実行中のci停止運用(§2.6)。
10 §1の「Workerは本番同様にオートスケール」はローカルcompose設計前提から**手動scaleへの読み替え**
(台数の段階変更=stop/start)とする — 設計書として明記(機械的なオートスケールの再現は
ローカルにはない・本番同環境はPhase 0が担う)。

nginx設定は`docker/nginx/staging.conf`等1ファイル(upstream=api1:8000 api2:8000・
proxy_next_upstream・/health通過)。ciにはnginxを入れない(10 §1のci=最小構成どおり)。

**04本番LB構成との対応**(何を模倣し何を模倣しないか): 模倣するのは
**入口1つ・upstream分散・死んだインスタンスへの振りの再送(死活除外)**の3点
(10 §4.3の可用性試験が測る対象そのもの)。模倣しないのはTLS終端(ローカルはHTTPのみ・
HTTPSは本番基盤の担い手)・マネージドLBのactiveヘルスチェックとオートスケール連動・
マルチAZ配置(いずれもクラウド基盤の標準機能で、Phase 0の本番同環境が担う領域)。

### 2.4 認証のテスト構成 — staging専用鍵ペアの注入(M0資産再利用)

現状の同梱testkeysは「ci・試験専用で公開前提」(testkeys/README)であり、10 §1の
「staging専用の鍵ペアを注入」を満たすには鍵の読み出し先を差し替えられる必要がある。

- `auth/testkeys/__init__.py`に**`LATCH_AUTH_TESTKEYS_DIR`環境変数**を追加(既定=従来どおり
  同梱ディレクトリ・ciは無影響)。`load_private_key()`/`load_jwks()`の両方が同じディレクトリを
  見るため、発行側(issue-idp-token・gen-keypair)と検証側(JWKS)が自然に揃う
- staging用鍵ペアは**git管理外の固定パス**(`.staging/keys/`)に`make staging-keys`で生成
  (gen-keypair再利用)・staging composeのapi1/api2へマウント+`LATCH_AUTH_TESTKEYS_DIR`設定。
  prodの起動ガード(build_auth_service)は既存のまま変えない
- JWT発行は既存CLIをMakefileラップ(`make staging-token SUBJECT=u1`→issue-idp-token
  --private-key .staging/keys/idp_test_private.pem)。issuer/audienceは既定(テストIdP値)のまま
  — settings既定値=検証側と自動一致が既存設計(引用#17)

代替案(同梱鍵をstagingでも共用)は「staging専用」の文言と鍵衛生(ciとstagingで同一鍵)の
両面で不採用。実装コストはローダ1関数のenv読みとunit 1〜2試験のみ。

### 2.5 疑似イベントパブリッシャー — 既存events CLIへのサブコマンド追加

`python -m latch.events publish`(既存運用CLI `latch.events`(__main__.py)の拡張):

- 引数: `event_type`(6値choices: created/updated/deleted/expired/scheduled/embedding_completed)、
  `--intent-id <uuid>`(必須)・`--version N`(既定1)・`--raw-payload '<json>'`(毒ペイロード注入用)
- 実行は既存の`EventBus.publish_match_event`と同一経路(3点組をそのまま使う — 引用#18・
  **発行形式の二重管理を作らない**)。worker側は同一の消化経路(stage1)を通る
- expired/scheduledも含めて6種すべて手動発火可(10 §1は「Match Event 5種+派生1種を手動発火
  できる」を要求。scheduledの常時発行経路は存在しない〔M3 ws-2設計①: 保留キュー再評価は
  Event不発行・直接投入と確定〕ため、手動発火はこの種別を観察する唯一の経路でもある)
- `--raw-payload`はws-7の毒ペイロード試験(型不正→quarantined)・参照不在Event
  (存在しないintent_id→processed破棄)の両方の器(引用#14)
- 宛先は`LATCH_PUBSUB_EMULATOR_HOST`+`LATCH_PUBSUB_PROJECT_ID`のenv上書きでstaging/load宛へ
  (purge-match-subと同一方式)

### 2.6 その他の運用確定値

- **LLM/FCM**: staging/load composeは`LATCH_LLM_MODE=stub`・`LATCH_PUSH_MODE=stub`を明示
  (実課金ゼロ・実送信ゼロの保証)。stubのレイテンシ注入は既存のdelay系env
  (固定ms・引用#19)をcomposeで設定可とするまで。**p50/p95分布のスタブ拡張はws-6のスコープ**
  (settingsコメントの既定どおり) — 本単位では行わない
- **ダッシュボード(Observability)の器はws-4**(**2026-10-03 supervisor承認**): ws-1は
  ダッシュボードプロジェクト(latch-dashboard)の器を作らない。ws-4が計測実装とともに
  compose改版で追加する。ws-1側が保証するのは**計測系サービスの後からの追加しやすさ**:
  (a)api/workerの共通定義(イメージ・接続env・depends_on)をYAMLアンカーへ切り出し、
  ws-4の追加変更を「新サービスブロックの追記+共通アンカーへのenv/ポート追記」に局所化する
  (b)ネットワークはプロジェクト内デフォルトのため、追加サービスは同プロジェクト内のDNS名
  (api:8000・db:5432等)で既存コンテナへ到達でき、外部公開もportsへの追記だけで足りる
  ws-1のstaging/load composeはメトリクス取得用のポート・サービスを含まない
- **load試験実行中のci停止**(**2026-10-03 supervisor承認**): 負荷試験(50rps・100evt/s・
  Active 10万)の測定精度のための運用。**組み込み位置はws-6/ws-7の試験実行手順の前段**
  (環境立ち上げの前にci停止→`make down`〔ci・ボリューム保持〕→load環境で試験→
  完了後`make load-down`→`make up`でci復帰)。**干渉防止の根拠**: ci環境は同一ホストの
  コンテナとしてCPU・ディスクIO・メモリを常時消費しており、レイテンシ分布(性能5目標は
  絶対値合格: p95≦500ms等)へノイズを乗せる。ローカルには物理分離がないため、測定時の
  リソース確保が「隔離」の実体の一部(§2.3)。この運用はマージ時にSTATUS.md運用ルールへ
  記録する(supervisor側)
- **マイグレーション・geo投入**: `make staging-migrate`(LATCH_DATABASE_URLをstaging宛にして
  alembic upgrade head)・`make staging-geo-import`(同URLでISJ+OSM取り込み)。
  起動時自動実行はしない(既存規律どおり)。stagingは独立DBのため運用ルール1〜3の対象外
- **強制停止と自動復旧**: api/workerは`restart: unless-stopped`(ciと同一)。ws-7の
  「強制停止→自動復旧」はdocker kill→unless-stoppedによる再起動、「残存継続」はnginxの
  死活除外で表現される(§2.3)。明示stopは「停止したまま」が正しい挙動
- **タイムボム回避**: stagingの受け入れ確認はClock操作・シード投入を含むため、実行時刻依存の
  値(今夜23:30等)に固定せずISO明示する規律をMakefile/手順に含める(M3 ws-1の実例)

## 3. ファイル構成

### 3.1 作るもの

| ファイル | 内容 |
|---|---|
| `compose.staging.yaml` | latch-stagingプロジェクト: db・redis・pubsub・api1・api2・nginx・worker1・worker2。YAMLアンカーでapi/worker共通(env網羅・§1.4の再発防止)。ボリュームlatch-staging-db等 |
| `compose.load.yaml` | latch-loadプロジェクト: 同種構成(隔離)・ポートは28000系。api/workerのscale運用余地を残す定義 |
| `docker/nginx/staging.conf` | nginx upstream(api1・api2)・proxy_next_upstream・listen 80(compose内) |
| `backend/src/latch/core/clock.py` | **既存ファイルへの追記**: ControlledClock・make_clock(settings)。計算純関数`_compute_now(base, real_now)`を含む |
| `backend/src/latch/core/clock_cli.py` | 新規: `python -m latch.core.clock_cli`(show/set/advance・同期redisクライアント) |
| `backend/src/latch/main.py` | 1行変更: create_appの既定Clockを`make_clock(settings)`経由に(clock引数明示の呼び出しは無傷) |
| `backend/src/latch/worker/__main__.py` | 1行変更: Worker構築時のClock既定をmake_clock経由に |
| `backend/src/latch/settings.py` | 追記: `clock_mode`(既定system)・`auth_testkeys_dir`(既定空=同梱)。既存項目の変更なし |
| `backend/src/latch/auth/testkeys/__init__.py` | 追記: LATCH_AUTH_TESTKEYS_DIR環境変数で鍵ディレクトリを差し替え(既定=従来) |
| `backend/src/latch/events/__main__.py` | 追記: `publish`サブコマンド(6値・3点組・--raw-payload) |
| `Makefile` | 追記: staging-up/down/ps/logs/migrate/geo-import/keys/token/clock/publish・load-up/down/ps |
| `backend/tests/unit/test_controlled_clock.py` | 新規: 計算純関数・set/advance・キー不在=実時間・Redis不在=例外・make_clock分岐 |
| `backend/tests/unit/test_clock_cli.py` | 新規: パーサ・操作がキーへ書く値・宛先env(fakeredis) |
| `backend/tests/unit/test_events_publish_cli.py` | 新規: 6値choices・3点組・raw-payload・スタブbusで組立検証 |
| `backend/tests/unit/test_settings.py` ほか | 追随: 新settings項目の機械ピン・testkeys env差し替え試験 |
| `docs/plans/M4/ws-1-report.md` | 報告書(受け入れ確認の証拠つき) |

### 3.2 触らないもの

- `compose.yaml`(ci・一切無変更)・ci環境のMakefileターゲット(up/down/test-ci等)
- Clock契約(Clock ABC・SystemClock・FakeClock)と既存DI試験・arch test
- API/Worker/パイプライン本体(ルーター・stage1・各Engine・sweeper等)・DBスキーマ(マイグレーションなし)
- auth本体(service/routes/idp・prod起動ガード)・pubsub_busのpublish形式
- frontend・prototype・docs/docs本文(本単位でdocs改版なし)

## 4. テスト方針

### 4.1 自動試験(unit・毎コミットの既存規律)

- **ControlledClock**: 計算純関数を決定的に(基準点+経過秒→仮想時刻)・set/advanceの基準点
  更新・キー不在=オフセット0・Redis例外の伝播・make_clockの分岐ピン(system/controlled・
  controlledはRedisクライアントを注入して構築)。fakeredis(既存dev依存)で実行
- **clock_cli**: パーサと操作結果がキーへ書くJSON値・ISO8601(JST付き)の受付
- **publish CLI**: 6値choices(範囲外は拒否)・3点組の組立(スタブbusで検証)・--raw-payloadの
  優先・destination設定はSettings由来(purge-match-subと同型のスタブで)
- **settings/testkeys**: 新settings項目のピン(test_settings追随)・LATCH_AUTH_TESTKEYS_DIRで
  生成鍵ペアの読み替え(一時ディレクトリ+gen-keypairで実生成しての回帰)
- **arch test**: clock.py内の実装は許可内(引用#12)。既存のままグリーンであることを完了条件に含む
- integration試験は追加しない(実コンテナの検証は4.2の受け入れ確認が担う。エミュレータ滞留や
  常設workerとの干渉をテストスイートに持ち込まない)

### 4.2 環境の受け入れ確認(手順化して報告書に証拠として記録)

1. `make staging-up`: 全サービスhealthy(wait通過)・worker2台稼働・workerログにDB接続と
   subscription作成を確認(§1.4(b)の検証)
2. 別DBの確認: ci-db(latch-ci-dbボリューム)への書込ゼロ(行数比較)・alembic_version独立
3. nginx越しのAPI: `make staging-token SUBJECT=u1`→users登録→200・api1/api2双方の
   /health server_time応答(引用#16のClock消費点)
4. Clock制御: `make staging-clock set <ISO>`→server_timeが追随・`advance 86400`→追随・
   `show`表示・制御キー未操作時は実時間と一致
5. イベント注入: `make staging-publish created ...`→worker消化(match_events processed)・
   不正参照→processed破棄・`--raw-payload`型不正→retry5回→quarantined(毒経路の最小確認)
6. worker複数台: 2台同時稼働での同一イベント重複処理が冪等(match_candidates二重なし・引用#14)
7. 隔離の確認: staging起動中にci環境のup/test-ciが通常どおりグリーン(ポート・ボリューム無干渉)
8. load環境: `make load-up`で同様にhealthy・stagingと同時起動しても無干渉
9. 既存回帰: `make lint`・`make test`(unit=1219+新規)・`make test-ci`(1480維持)・
   `make staging-down`後もci環境常設が継続稼働

### 4.3 完了条件(計画書への引き取り用ドラフト)

- [ ] compose.staging.yaml・compose.load.yaml・nginx設定が存在し、makeターゲットでup/down/migrate可
- [ ] staging受け入れ確認(4.2の9項目)がすべてグリーン・証拠が報告書に記録(10 §5の形式)
- [ ] ControlledClock・CLI・publish・testkeys差し替えがunit試験でグリーン・lint緑・basename一意
- [ ] make test-ci 1480維持(既存への影響ゼロ)・arch testグリーン
- [ ] worker実稼働確認(§1.4(b))を含むcompose接続envの網羅

## 5. supervisor承認事項(2026-10-03・4件とも裁定済み — 未解決の論点なし)

- **A1(環境分離方式)§2.1**: 承認。別ファイル+別プロジェクト名(compose.staging.yaml /
  compose.load.yaml・-p latch-staging / latch-load)。単位表の「composeプロファイル分離」は
  この実現方式の意図と確定。根拠(ciのcompose.yamlを一切触らない・DB独立ボリュームで
  運用ルール1〜3のalembic_version問題が構造的に発生しない)は§2.1に明記済み
- **A2(nginx持ち込み)§2.3**: 承認。公式nginx:alpine+proxy_next_upstreamの前段を採用
  (ローカルOSS・コストゼロ。10 §4.3可用性試験の「入口1つ+api 2台」構成の再現)。
  04本番LB構成との対応関係(模倣する3点/模倣しない点)を§2.3に明記
- **A3(ダッシュボードの器)§2.6**: 承認。ws-1はlatch-dashboardの器を作らない・ws-4が
  計測実装とともにcompose改版で追加する。ws-1側は計測系サービスを後から追加しやすい構成
  (共通定義の切り出し方)だけ保証する(§2.6の(a)(b))
- **A4(ci一時停止の運用規律)§2.6**: 承認。負荷試験実行中はci常設環境を一時停止
  (make down/up・ボリューム保持)。試験手順への組み込み位置(ws-6/ws-7手順の前段)と
  干渉防止の根拠(同一ホストのCPU/ディスク競合)は§2.6に記載。マージ時にSTATUS.md
  運用ルールへ記録する(supervisor側)
- **A5(既知欠陥の組み替え)§1.4**: 承認。compose.yaml workerのLATCH_DATABASE_URLは
  2026-09-30修正済み(9268a73・supervisor確認)。本単位では「新規composeでの再発防止
  (YAMLアンカー共通化)+完了条件へのworker実稼働確認」として組み替え

### ws-3以降への引継ぎ(本単位内で確定しない)

- staging試験の実行時間と`reeval_runner_interval_sec`(既定60秒)の関係: Clock操作後の
  sweeper/Runner起動は実時間周期の次tickを待つ。stagingで周期を短縮するか(規定値からの
  読み替えが必要)はws-3設計の論点
- Clock後退時のDB行矛盾(created_atが未来等)への作法(set後は新規データ投入)は
  ws-3ハーネス設計で手順化
- worker台数の段階変更(--scale型の運用をloadでどこまで使うか)はws-6設計の論点
