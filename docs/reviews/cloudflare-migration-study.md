# Cloudflare構成でLATCH本番を運用できるか — 移行調査書

- 作成日: 2026-10-06
- ステータス: Draft(スーパーバイザー検証待ち)
- 目的: 04 §3〜§7の本番確定値(GCP・別紙 docs/reviews/phase0-cloud-cost-estimate.md)に対する代替構成として「Cloudflare Workers実行基盤+外部マネージドPostgreSQL」を評価する。オーナーがGCP案と比較してGo/No-Goを決めるための資料であり、本書自体は構成の採用を提案しない
- 前提文書: 04 v0.6 §2〜§7 / docs/reviews/phase0-cloud-cost-estimate.md(以下「GCP試算書」) / docs/plans/STATUS.md(M2作業単位表・G4時確認事項)
- 調査方法: Cloudflare・Neon・Supabase・Upstash・Firebaseの公式ドキュメントと価格ページを2026-10-06にWebから取得(exa/web_reader経由)。パッケージの実在確認はPyPIの公開JSON API(2026-10-06取得)。単価・制限値には出典URLと確認日を付し、取得できなかったものは推測で埋めず「未確認」とした(第7節)
- LATCH側の実態: backend/src/latch/ の実コードを2026-10-06に確認(db.py・events/pubsub_bus.py・notifications/sender.py・worker/main.py・各store・pyproject.toml)

## 1. 結論 — 技術的には成立する見込み。ただしGCP案を変える理由には弱い

- **費用: GCP案より月$20前後安いが、決定的な差ではない。** 04 §7のPITR(任意時点への復元)要件を満たす比較では、Phase 0(2週間)はCloudflare案が約$35〜47・GCP案が約$56。Phase 1(500人・1ヶ月)はCloudflare案の最安組合せが約$242・GCP案が約$262。LLM費用($140/月)はどちらの構成でも変わらないため、差はインフラの$122→$102(Neon組)に現れるだけである。PITRをSupabase($100/月)で満たすと約$289になり、GCP案を上回る(第6節)
- **技術: APIはFastAPIのまま動く。DB接続と認証署名の2面で大改修が要る。** Python Workersは2026-09-21にGAとなり、FastAPIがビルトインASGIコネクタで動作する。Postgres接続はHyperdrive経由でasyncpg(現行ドライバ)が推奨ドライバとして動く。しかし「SQLAlchemyの非同期ORMは非対応」と公式に明記があり、実際にgreenlet(SQLAlchemy asyncの依存)のPyEmscripten wheelがPyPIに存在しないことを確認した。同様にpyjwt[crypto](JWT署名)の依存cryptographyにもwheelがなく、認証署名はWebCrypto APIへの置換が必要である(第4節)
- **工期: 移行工事は10単位・うち規模感「大」が3。** DB接続層・Worker常駐モデルの転換・試験の再整備が大きい。テスト1754件(test-ci常設緑)はローカルで動くため書き直し不要だが、ドライバ差し替えを経て全件再実行し、G4の非機能試験7種(性能・可用性・バックアップ復旧・縮退・Queue障害)をCloudflare構成でやり直す必要がある(第5節)
- **最大のリスクは成熟度とレイテンシの構造問題。** Python WorkersはGA後2週間(本調査日時点)であり、主要なC拡張パッケージが未対応であることが移行の阻害要因そのものになっている。また最安の外部Postgres(Neon)には東京リージョンがなく、日本のユーザーとDBの間にアプリが挟まる二重の遅延を避けられない(第4節・第7節)

判定の3軸に落とすと「費用: 微優位・工期: 大幅不利・リスク: 大幅不利」である。差し替えの根拠は、現時点で見当たらない。**GCP確定案の維持が現実解であり、Cloudflare構成は将来の再選定候補としての知見になる**。ただしCloudflare構成に明確な利点が1つある — 使っていない時間に課金が発生しない構造である(Phase 0の環境削除忘れリスクの排除。第8節)。

## 2. 調査の前提と母集団

比較対象の定義を先に固める。

- **Cloudflare案の範囲**: 実行基盤(API・Worker・バッチ)をCloudflare Workersへ、イベントキューをCloudflare Queuesへ、定期実行をCron Triggersへ置く。PostgreSQLとRedisはCloudflare内に置けないため外部サービスと組み合わせる。DBのPostGIS(地理判定用拡張)+pgvector(ベクトル検索用拡張)はD1(Cloudflareの「SQLiteのSQL semantics」を持つマネージドDB・<https://developers.cloudflare.com/d1/>・2026-10-06取得)では使えない前提であり(未確認事項#1)、外部マネージドPostgresが必須である
- **比較基準**: GCP試算書と同じ04 §6〜§7の本番確定値(API常時2インスタンス・マネージドPostgreSQL+PITR・Cloud Monitoring+OTLP)を「守る場合」と「緩める場合」を分けて示す。04 §6の単一クラウド前提はCloudflare案では崩れるため、これ自体がdocs改版(オーナー領域)の対象になる
- **金額の通貨・為替**: 円換算していない(GCP試算書と同じ)。LLM費用はGCP試算書§7の値をそのまま引用する(T1 v0.3の前提。インフラの選択に依存しない)

## 3. 対応表 — LATCHの構成要素の置き先

04 §2の構成図の要素ごとに、現行・GCP案・Cloudflare案を並べる。

| 構成要素 | 現行(staging・実コード) | GCP案(04 §3確定値) | Cloudflare案 | 構成差の要点 |
|---|---|---|---|---|
| API Layer(FastAPI) | uvicorn+nginx前段 | Cloud Run・最小2インスタンス | **Workers+ビルトインASGIコネクタ**(workers.asgi) | uvicornが不要になる。常時2インスタンスという概念自体が消える(エッジ常時稼働) |
| Matching Worker(常駐) | Python常駐プロセス(worker/main.py・run()ループ) | Cloud Run(キュー駆動・最小0〜1) | **Queue consumer+複数のCron Triggersへの分解** | 常駐モデルが存在しない。debounce 10秒窓など「秒単位の待機」を別仕掛けで再設計(§4.7) |
| バッチ(60秒sweeper・JST 0時リセット) | 同一Worker内のtick | Cloud Scheduler(追加) | **Cron Triggers(最小間隔1分)** | 60秒周期は1分=60秒でピッタリ対応。JST 0時はUTC表記で15時に設定 |
| PostgreSQL(PostGIS+pgvector) | PostGIS同梱イメージ | Cloud SQL(拡張対応確認済み) | **外部マネージドPostgres(Neon/Supabase等)** | Cloudflare内に置けない。単一クラウド前提の崩壊。接続はHyperdrive経由 |
| Redis(4用途) | redis-py(asyncio)+fakeredis(試験) | Memorystore | **Upstash Redis等(RESP互換)** | 使用コマンドはINCR/EXPIRE/SCAN/SETNX等の標準のみで、コード変更なしの見込み(§4.5) |
| イベントキュー | pubsub_bus(実GCP・エミュレータ切替) | Pub/Sub | **Queues(at-least-once)** | 端口(EventBus)はそのまま・アダプタを新規追加。隔離はDB側(match_events.status)なのでQueuesのDLQに依存しない |
| 計測(3群+HTTPメトリクス) | prometheus-client(pull型)・obs/metrics.py局所化 | Cloud Monitoring+OTLP | **Analytics Engine(書き込み型)or OTLP push** | pull型をWorkersが受けられないためpush型へ。差し替え面はG4確認事項2で既に局所化済み |
| プッシュ通知(FCM) | StubPushSender(PushSender Protocol) | FCM(無料) | FCM HTTP v1(WebCryptoでJWT署名) | 実FCMは未着手(ws-9)のため「移行」ではなく実装先の選択が変わるだけ |
| LLM(TypeSafe Jev・Anthropic・Gemini) | LLM Gateway(gateway.py) | 変更なし | 変更なし | 外部API直呼びでCloud依存なし。Gateway抽象のまま |

この表から読める要点は2つある。第一に、**LLM系統とイベントのビジネスロジック(冪等キー・隔離・縮退運転)はクラウド非依存の設計が効いており、書き換え対象は実行基盤の周縁に集まっている**。第二に、その周縁のうち「常駐プロセスに宿っていた柔らかい部分(debounce・秒間隔のtick・プロセス内キャッシュ)」が転換対象になる。Workersでは、こうした部分はすべて明示的なサービス(DO・Queues遅延・Cron)へ置き換わる。転換量を第4節で1つずつ確認する。

## 4. 技術検証 — 9論点の一次確認結果

### 4.1 Python Workers GA版の制約 — FastAPIは動く。C拡張は構造的に苦しい

GAの状況と制約(いずれも一次情報)。

- **GA: 2026-09-21**(公式blog <https://blog.cloudflare.com/python-workers-ga/>・2026-10-06取得)。「Python is now a first-class, fully supported language」。FastAPI・Flaskは`workers.asgi`、Djangoは`workers.wsgi`のビルトインコネクタで動作し、uvicorn等のWebサーバは不要。LATCHのAPI(fastapi+uvicorn・pyproject.toml確認)のうちuvicornの役割をWorkersプラットフォームが担う
- **パッケージ形式**(<https://developers.cloudflare.com/workers/languages/python/packages/>・2026-10-06取得): 依存は (1) PyPIのpure Pythonパッケージ (2) PyEmscripten形式(PEP 783・Wasm向けPythonパッケージの配布規格)のwheelがあるパッケージ (3) Pyodide同梱パッケージ の3種に限定される。C拡張を含むパッケージはPyEmscripten wheelが必要だが「Wasm support for Python packages is still in early stages」
- **制限値**(<https://developers.cloudflare.com/workers/platform/limits/>・2026-10-06取得): CPU時間はFree 10ms/リクエスト・**Paid 30秒(設定で最大5分)**。Cron Trigger実行はPaid 30秒(1時間未満の間隔)/15分(1時間以上)。メモリ128MB・スクリプトサイズ64MiB(非圧縮)・同時接続6/リクエスト・起動時間1秒。Queue consumerは実行15分(壁時計)
- **実コードへの当てはめ**: LATCHの依存(pyproject.toml)のうちfastapi・pydantic・sqlalchemy・alembic・pyjwt(本体)・redis・prometheus-clientはpure Pythonで動く見込み。**動かない確定分はgreenlet(SQLAlchemy asyncの依存)とcryptography(pyjwt[crypto]の依存)** — PyPI最新版(greenlet 3.5.6・cryptography 50.0.2)にPyEmscripten wheelが存在しないことをPyPI JSON APIで確認(2026-10-06)。google-genai・typesafe-sdkの実行環境要件は未確認(第7節#6)

考察を添えると、この制約は「がんばれば回避できる」類ではない。greenletもcryptographyもC/Rust拡張だ。エコシステムがPEP 783のwheelを整備するまで、Workers上に載ることはない。移行計画はこの2点を前提に置いて組むことになる(§4.3・§4.9)。

### 4.2 Hyperdrive+Python — asyncpgが推奨ドライバ。ただし非同期ORMは非対応

Hyperdrive(CloudflareのPostgres/MySQL接続プーリング+キャッシュ)のPython対応。

- **対応時期**: 2026-09-16に「Hyperdrive support for Python Workers」(<https://developers.cloudflare.com/changelog/post/2026-09-16-hyperdrive-python-workers>・2026-10-06取得)。compatibility date 2026-09-08以降が必要(<https://developers.cloudflare.com/hyperdrive/examples/python-workers/>・同日取得)
- **ドライバ**: 「TCP接続で動く任意のPythonドライバ」が対象で、検証済みはPostgreSQL向けに**asyncpg(推奨)**・pg8000・psycopg。接続は`connect` API(Workersのカスタムsocket syscall)経由。GA blogに「以前はTCPソケット非対応でasyncpg等が動かなかったが、カスタム実装を追加した」との記述がある
- **SQLAlchemyは同期ORMのみ**(`<https://developers.cloudflare.com/hyperdrive/examples/python-workers/>`・2026-10-06取得): 「Currently, only synchronous SQLAlchemy ORMs are supported。非同期版はgreenlet未対応のため不可」と明記。同期DB操作の直列化にはasyncio.Lockの使用が推奨されている
- **接続モデル**: 接続プールはHyperdrive側が保持し、アプリはリクエストごとにクライアント生成が可能。クエリキャッシュは既定で有効。料金はWorkers Paidに含まれクエリ課金なし(<https://developers.cloudflare.com/hyperdrive/platform/pricing/>・2026-10-06取得。Freeプランのみ10万クエリ/日の上限)
- **対応DB**: 「any Postgres or MySQL database」(Neon等の外部マネージド含む)<https://developers.cloudflare.com/hyperdrive/>

結果を左右するのは、この2点の組合せだ — asyncpg単体なら動く。SQLAlchemyのasync面は動かない。次節で実コードの被害範囲を詰める。

### 4.3 asyncpg→差し替えの影響 — 変えるのはエンジンの供給方法。SQL自体は残せる可能性が高い

LATCHのDB層の現状を先に書く(実コード確認・2026-10-06)。

- **SQLはtext()生SQLのみ**。ORMモデル(DeclarativeBase等)はプロジェクト内に存在しない(intents/store.py冒頭の注記「SQL一式をこのモジュールへ」・ws-1流儀)。SQLのパラメータは`:name`形式(SQLAlchemy text()のバインド)
- **エンジンはSQLAlchemy 2.0 AsyncEngine+asyncpg**(core/db.py)。`connect_args={"server_settings": {"timezone": "UTC"}}`(asyncpg固有の設定)を1箇所で付与
- asyncpgの参照は23ファイルに及ぶが、大半はAsyncEngine経由の実行パスと、M2 ws-1以降に確立した**uuid[]バインド規律・_coerce_uuid系のUUID復元**(intents/store.py:61ほか)である

この構造から、移行の実態は「SQLの書き換え」ではなく「**エンジンの供給方法の選択**」になる。選択肢は3つある。

| 選択肢 | 内容 | 影響範囲 | 評価 |
|---|---|---|---|
| (a) SQLAlchemy同期エンジン+psycopg(同期モード)+asyncio.Lock直列化 | Hyperdrive公式の推奨に沿う形。`:name`バインドはSQLAlchemyのpsycopg dialectがそのまま変換するため**8本のalembicと23ファイルのSQLは無修正で済む可能性が高い** | core/db.pyの工場+各storeの実行パス(await conn.execute→同期的に実行しLockで直列化) | SQL無修正の可能性が最も高い。ただし直列化はスループットを落とす(G4確認事項6のRSA署名直列化27req/sの実績と同種の懸念) |
| (b) asyncpg直叩き | Hyperdrive推奨ドライバを生で使う | **全SQLを`:name`→`$n`形式に書き換え**(select系・insert系・alembic含む)+_coerce_uuid・バインド規律の全面再検証 | パフォーマンスの余地はあるが書き換え量が最大 |
| (c) SQLAlchemy asyncのまま待つ(greenletのPyEmscripten wheel待ち) | コード無変更 | なし | wheelの整備時期は未確認であり計画の前提にできない |

試験への影響はこうなる。テスト1754件はローカル環境(asyncpg実DB+fakeredis)で動くため、(a)を採るならドライバ差し替え後の全件再実行で合否を取り直す。ここで問題になるのがドライバ差の潜在欠陥である — asyncpgとpsycopg3は型の暗黙変換の挙動が異なる(例: uuid配列・JSONB・timestampの復元)。M2 ws-1以降に「uuid[]バインド規律・UUID復元_coerce_uuid系の実績」として積んだ知見は、ドライバの実装に紐づく。再検証の範囲は**store層23ファイル全体+フィクスチャ**に及ぶ。alembicはWorkers上では動かさない(ローカル/CIから外部Postgresへ直接実行)ため、8本のマイグレーション自体の変更は不要である。

考察: (a)が最有力だが、直列化の性能影響は未知数であり、これが第5節の移行検証(cf-1)を先行させる理由である。直列化でAPIの保存p95 500ms(01 §20)が守れるかは、GCP側の実績と同じく負荷試験を回して初めて分かる。

### 4.4 PostGIS+pgvectorが動く外部マネージドPostgres — 拡張は2社とも対応。ただし東京とPITRで分かれる

| 項目 | Neon | Supabase |
|---|---|---|
| PostGIS対応 | PG14〜18・3.3.10〜3.6.4(<https://neon.com/docs/reference/pg-extensions>・2026-10-06) | 拡張エコシステムに含む(postgis_tiger_geocoder言及)・詳細バージョン未確認 |
| pgvector対応 | 0.8.0〜0.8.6(同上) | 公式docsで対応確認(vector拡張・HNSW/IVFFlat言及)<https://supabase.com/docs/guides/database/extensions/pgvector>・2026-10-06取得 |
| 東京リージョン | **なし**(AWSシンガポール・シドニーのみ・<https://neon.com/docs/introduction/regions>・2026-10-06取得) | あり(dashboardのリージョン選択にTokyo・第三者情報とdashboardベースの記述のみで一次docsでは未確認) |
| 無料枠 | プロジェクトあたり100 CU-hours/月・5分無通信でスケールトゥゼロ・ストレージ1GB(<https://neon.com/pricing>・2026-10-06取得) | 500MB DB・**1週間非アクティブで一時停止**(再開は手動) |
| 有料段階 | Launch(Scale比で安い側): compute $0.106/CU-h・ストレージ $0.35/GB月・Instant Restore(PITR相当) $0.20/GB月 | Pro $25/月(8GB DB・7日バックアップ込み)+compute(Micro $10/月〜・1GB RAM・直接接続60) |
| PITR(04 §7要件) | Instant Restore・保持最大7日(Launch) | **Pro以上で$100/月**(7日保持) |
| コネクション | プーリング全プラン込み(直接接続上限の数値は未確認) | Micro直接60・プーラー200(pgbouncer経由) |

読み取れることを数字の意味まで落としておく。PITR要件を満たす条件で比べると、**Neonは月額数ドルの追加($4前後)でPITR相当が付くのに対し、SupabaseはPITRで+$100と桁が違う**。04 §7の「マネージドPostgreSQLの日次バックアップ+PITR」を守るならNeon一択に近い。一方で東京リージョンがない問題はNeon側にある。ユーザー(日本・単一エリア)↔Workers↔Neon(シンガポール)の距離の問題は、Smart Placement(§4.10)でWorkerをDB近傍に置くとユーザー側の遅延が伸び、ユーザー近傍に置くとDB側が伸びる。**どこに置いても片方が伸びる構造**であり、保存p95 500ms(01 §20)への圧力になる。Supabase Pro(Micro・東京)ならこの構造問題は消えるが、PITR $100か「PITRなしで7日バックアップのみ(04 §7からの緩和)」の二択を迫られる。

### 4.5 Redis代替(Upstash) — 用途4種はいずれも標準コマンドの範囲。コード変更なしの見込み

Upstash Redis(サーバーレスRedis・コマンド課金)の要件適合を、LATCHの4用途ごとに確認する。単価は<https://upstash.com/pricing/redis>・2026-10-06取得 — $0.20/10万コマンド・ストレージ$0.25/GB(1GB無料)・帯域200GB/月まで無料。Free tierは50万コマンド/月・256MB。

| 用途(04 §3) | 実コードの使用面 | Upstash適合 |
|---|---|---|
| レート制限カウンタ(ratelimit/store.py) | INCR+EXPIRE | 標準コマンド・対応 |
| Jev予算・D-16カウンタ(worker/cost/store.py) | INCR/DELETE/SCAN/SET NX+TTL | SCANは時間課金に響く頻度ではない(日次レポート・リセット時のみ)・対応 |
| ブロックキャッシュ(safety/cache.py) | SET/GET+無効化 | 対応 |
| セッション失効リスト(auth) | SETEX/EXISTS系 | 対応 |

接続はredis-py(現行のまま)から`rediss://`(TLS)スキームで接続する形が一般的(第三者情報+公式SDKドキュメントベース・一次ページは2026-10-06時点で取得できず)。東京リージョン(ap-northeast-1)は第三者レビューで言及があるものの公式リージョン一覧を取得できていないため**未確認**とする(第7節#3)。考察: コード面の移行コストは実質ゼロ設定変更だが、Workers環境からUpstashへのTCP+TLS接続が実環境で安定するか(§4.3のsocket syscall経由)は検証項目に入れるべきである。

### 4.6 イベントキュー(Cloudflare Queues) — at-least-onceとLATCHの冪等設計は噛み合う

- **配送保証**: at-least-once(重複配信があり得る)・公式の製品比較ページに「offers at-least once delivery」の明記(<https://developers.cloudflare.com/workers/platform/storage-options/>・2026-10-06取得)。**LATCHは同一Event2回投入で二重生成しない設計(M2 ws-8の冪等性試験・G2完了条件)を既に持ち、3点組(event_type, source_intent_id, version)を冪等キーとして持つ**ため、at-least-onceは要件上問題にならない
- **失敗処理**: max_retries(デフォルト3・最大100)を超えたメッセージはdead_letter_queueに転送(設定しなければ破棄)<https://developers.cloudflare.com/queues/configuration/configure-queues/>・2026-10-06取得。ただし**LATCHの隔離の実体はDB側(match_events.status=quarantined)であり、pubsub_bus.pyのコメントに「dead letter topic・subscriptionは使用していない」とある**。つまりQueuesのDLQは必須ではなく、DB側の規律がそのまま効く
- **制限**(<https://developers.cloudflare.com/queues/platform/limits/>・2026-10-06取得): スループット5,000メッセージ/秒/キュー・メッセージ128KB・consumer同時250・再試行最大100回・リテンションFree 24時間固定/Paid最大14日・バックログ25GB/キュー
- **料金**(<https://developers.cloudflare.com/queues/platform/pricing/>・同日取得): $0.40/100万操作。1メッセージ≈3操作(write/read/delete)。Workers Paidは月100万操作無料・Workers Freeは1日1万操作

考察: 容量・保証の両面でPub/Subの置き換えとして機能する。確認が必要なのはエンドツーエンドの遅延の実測値(docsに数値がない)であり、初期LATCH判定p95 10秒(01 §20)の配分に入る遅延がどの程度かは負荷試験で確かめることになる(第7節#5)。

### 4.7 定期実行と常駐モデル — ここが構造的な転換点。最小1分と「秒単位の待機」の欠如

Workersには常駐プロセスが存在しない。LATCHのworker/main.py(実コード・2026-10-06確認)は常駐ループ(run()・asyncio.sleepのtick)であり、ここに宿っていた機能の置き先を1つずつ決める必要がある。

- **Cron Triggersの最小間隔は1分**(`* * * * *`・秒フィールドなし)<https://developers.cloudflare.com/workers/configuration/cron-triggers/>・2026-10-06取得。実行は「余剰キャパシティのマシン」で行われ、設定変更の伝播に最大15分・時刻の正確さに幅がある旨が明記されている。LATCHの60秒周期expiry_sweeper(M3 ws-2)は**周期としては60秒=1分でそのまま対応**できる。JST 0時のリセットジョブはUTC表記で15時に設定する
- **debounce 10秒トレーリング窓**(M2 ws-1・更新の統合待ち)は分単位のCronでは表現できない。現実的な置き先は (1) **Durable Objects**(アラーム機能で10秒後に自分自身を起こす。Workers Paidに含まれる・単一インスタンス保証でカウンタにも向く) (2) **QueuesのdelaySeconds**(送信時遅延・最大24時間) の2つである。DO利用時の正確な料金は本調査では取得できていない(第7節#7)
- **30分Bucket再評価**(M2 ws-6)はCron(30分間隔)に置換できる。**catch-upスキャン・KpiJob(JST 0時集計)**も同様にCron群へ置く。Cron Trigger数はPaid 250/アカウント(limits一次)であり、LATCHの必要数(推定5〜6本)で問題ない
- **1リクエストのCPU時間上限**: Paid 30秒(設定で最大5分)・Queue consumerは壁時計15分。1イベントのパイプライン(Hard Filter→Vector→Judge→Jev→Engine)が単一consumer呼び出しに収まるかは、現行でプロセス内で逐次実行している処理をconsumer内でそのまま並べる形になるため、壁時計では問題ないがCPU消費の実測が要る(第6節の課金積算の仮定にも直結)

考察: この節が本書で最も重要な結論を含む。**「常駐プロセスの内側に自由に書いた非同期処理」を「サービスの束」へ分解する作業は、コードの移行というより設計のやり直しである**。debounce一つを取っても、DOのアラームによる再実行は「失敗したらどうなるか・再送の責務は誰か・冪等はどう保つか」を新たに決める必要があり、現行の同期待機(DebounceGroup)とは障害時の挙動が変わる。移行工事の規模感を「大」とする最大の根拠がここにある(第5節cf-5)。

### 4.8 計測 — pull型は使えない。obs/metrics.pyの差し替え面で吸収する

- **現状**: LATCHの計測はprometheus-client直+pull型(G4確認事項2)。メトリクス生成はobs/metrics.py 1モジュールへ局所化済みで、本番移行時の差し替え面として設計されている
- **Workers側の事情**: Prometheusのpull型(外部が取りに来る方式)はWorkersの実行モデルと合わない。Workers側で提供されるのは (1) Workers Logs(月2,000万イベント込み・超過$0.60/100万・保持7日)<https://developers.cloudflare.com/workers/platform/pricing/>・2026-10-06取得 (2) **Workers Analytics Engine**(writeDataPoint APIで書き込み・SQL APIで照会。月1,000万データポイント込み・超過$0.25/100万。<https://developers.cloudflare.com/analytics/analytics-engine/pricing/>・同日取得。なお「現時点では課金されない」の記載あり) (3) Workers Logpush(R2/S3/ロギングプロバイダへの送出) (4) OTelエクスポート(**ログとトレースのみ**が明記・メトリクスのOTLP送信は明記なし)<https://developers.cloudflare.com/workers/observability/>・2026-10-06取得
- **対応案**: 04 §3の「Cloud Monitoring+OTLP」をCloudflare構成では読み替えることになる。候補は (a) Analytics Engineへ書き込み、ダッシュボード・alertをSQL API+Workers Cronで組む (b) OTLP/Grafana Cloud等の外部観測基盤へpush(メトリクスのOTLP対応が明記されていないため可否の実検証が前提) (c) メトリクス自体をDBの集計テーブルへ書き、SQLで集計する(最も粗いが確実)。いずれにせよ**呼び出し面(middleware・各所の計測呼び出し)はobs/metrics.pyに集約済みのため、変更は1モジュールに収まる見込み**である。D-16の80%alert・Queue lag alert(G4⑥で動作確認済み)の実現手段を新基盤で組み直す作業は残る

### 4.9 FCM — HTTP v1+WebCrypto署名で送れる。実装は未着手なので「実装先の選択が変わるだけ」

- **FCM側**: HTTP v1 APIはOAuth 2.0アクセストークン(Bearer)を要求し、スコープは`https://www.googleapis.com/auth/firebase.messaging`・サービスアカウントの鍵からJWT経由でトークンを取得する(<https://firebase.google.com/docs/cloud-messaging/auth-server>・2026-10-06取得)
- **Workers側**: WebCryptoの`subtle.sign`は**RS256(RSASSA-PKCS1-v1_5)の署名・検証に完全対応**(<https://developers.cloudflare.com/workers/runtime-apis/web-crypto/>・2026-10-06取得)。JWT→トークンエンドポイントへの交換は通常のfetchで可能
- **現行コードとの関係**: 実FCMは未着手であり(ws-9はG4の7項目外・Firebase資材確定後に独立挿入)、notifications/sender.pyはStubPushSender+PushSender Protocol。**つまり移行コストではなく、real実装の技術選択が変わるだけ**である。Admin SDK(Python版・C拡張依存の可能性が高い)を使わずHTTP v1直叩き+WebCrypto署名の実装になる見込み

### 4.10 可用性とSLA — 「常時2インスタンス」は構造的に不要になる。SLAはEnterpriseのみ明確

- **冗長性**: Workersはリクエストごとにエッジで実行され、インスタンス管理がアプリ側に存在しない。04 §6の「API Layerは常時2インスタンス以上」は、**Workers構成ではそもそも成り立たない概念であり、守る必要も生じない**(コールドスタート対策の最小インスタンス設定も不要)。ただしこれは「要件を満たす」ではなく「要件が不要になる」であり、docs/04 §6の改版対象である
- **Smart Placement**(<https://developers.cloudflare.com/workers/configuration/smart-placement/>・2026-10-06取得): 全プランで利用可・DB等のバックエンドに近いデータセンターで実行する機能。fetchハンドラのみ対象・Python Workers対応の明記なし(第7節#4)。§4.4の二重レイテンシ問題に対する唯一の緩和手段
- **SLA**: Cloudflareの明確な稼働率コミットはEnterprise契約に付く(第三者集計を含む・一次SLA文書からWorkers単体のFree/Paid向け数値は2026-10-06時点で確認できず)。99.5%(01 §20)の担保は「構成上の冗長性+実測」で示すことになり、SLA文書からの裏付けは取れない。外部Postgres(Neon/Supabase)のSLAも本調査では未確認(第7節#8)

## 5. 移行工事の見取り図 — 10単位・うち大が3

GCP案との差分を単位表の形式で示す(M2・M3の表と同じ体裁)。オーナー領域(docs改版)を含む。各単位の規模感は小(1ワークユニット程度)/中(設計確認を伴う)/大(設計のやり直し+試験再整備)の3段。

| 単位 | 内容 | 規模 | 根拠 |
|---|---|---|---|
| cf-1 DB接続の差し替え | core/db.pyの工場をSQLAlchemy同期+psycopgへ(asyncio.Lock直列化)。SQL(text()・:name)は無修正を狙う。server_settings(timezone UTC)の読み替え・Hyperdrive接続の実検証。uuid[]バインド規律・_coerce_uuid系の再検証(§4.3) | **大** | greenlet欠如でasync面が動かない(PyPI 2026-10-06確認)・エンジン共有の23ファイル・試験1754件の再実行を伴う |
| cf-2 認証署名のWebCrypto化 | pyjwt[crypto](cryptography依存)のRSA署名・検証・JWKS取得をWebCryptoへ。自前JWT実装かpyjwtのアルゴリズム差し替え | **中〜大** | cryptographyのPyEmscripten wheelなし(2026-10-06)・auth/users・nfrtools配下に影響 |
| cf-3 イベントバスのQueuesアダプタ | events/bus.pyの端口を維持・queues_bus.pyを新規(pubsub_busと同列)。冪等・隔離はDB側既存規律のまま | 中 | 端口がヘキサゴナル・pubsub_bus.pyが実装の見本。at-least-once=LATCH設計と合致(§4.6) |
| cf-4 実行基盤のASGI移行 | uvicorn除去・workers.asgiへ・compatibility date/python_workers flag・**全依存パッケージのwheel確認**(pyproject.tomlの棚卸し) | 中 | GA公式対応・ただし依存純度の確認(asyncpg・redis・google-genai・typesafe-sdk)が前提 |
| cf-5 常駐Workerの分解 | run()ループをQueue consumer+Cron群へ・**debounce 10秒窓をDO/Queues遅延で再設計**(障害時挙動の設計を含む)・sweeper/リセット/30分Bucket/catch-upのCron割り当て | **大** | 常駐モデル自体が存在しない・「秒単位の待機」をサービスで組む(§4.7)・設計のやり直しを含む |
| cf-6 Redis接続の設定差し替え | redis_url→Upstash(rediss://)。コード変更なし見込み・WorkersからのTCP接続の実検証 | 小 | 標準コマンドのみ使用(§4.5) |
| cf-7 計測の差し替え | obs/metrics.pyへAnalytics Engine or OTLP push実装・D-16 alert/Queue lag alertの新基盤で再実装 | 中 | 差し替え面はG4確認事項2で局所化済み・pull型からpush型への転換(§4.8) |
| cf-8 FCMのreal実装 | PushSenderの実装をHTTP v1+WebCrypto署名で新規(ws-9相当・移行ではなく新規実装先の選択) | 小(移行分として) | 実装未着手(§4.9) |
| cf-9 試験の再整備 | ドライバ差し替え後の全件再実行(1754件)+Workers環境(pywrangler dev・実環境)での統合試験新設+**非機能試験7種のやり直し**(性能5目標・可用性・バックアップ復旧・縮退・Queue障害) | **大** | G4の証拠はGCP/staging構成のもの・外部Postgresのバックアップ復旧手順(n2相当)を別途確立する必要 |
| cf-10 docs改版(オーナー領域) | 04 §3技術選定・§6デプロイ(単一クラウド前提の崩壊・常時2インスタンスの概念変更)・§7非機能(計測基盤・PITR)・11 Phase 0構成 | 中 | 構成思想の変更を含むためオーナー判断 |

依存関係は「cf-1・cf-2の技術検証が最初の関門」であり、ここが通らない場合、以降の単位に着手する意味がなくなる(スパイク=短時間の技術実験で可否を先に確かめる工程を最初に置くべき)。推定の実行waveは: **(cf-1∥cf-2)スパイク → cf-4 → (cf-3∥cf-5∥cf-6∥cf-7) → cf-8・cf-9 → cf-10**。GCP案と比べた工期の劣位は、このスパイク段階でgreenlet/cryptographyを含む依存棚卸しの結果次第というより、cf-5の設計やり直しとcf-9の試験再整備に由来する。

## 6. 費用試算 — PITR要件を守る比較ではPhase 1で月$20の差

GCP試算書と同じ方法(単価×量=額の式を付す・1ヶ月=730時間・LLMはGCP試算書§7の値を共用)で積算する。Cloudflare側の固有の性質を2つ先に断っておく。

1. **Workers Paid($5/月)は最低月額であり日割りされない**ため、2週間の利用でも1ヶ月分がかかる
2. **使っていない時間への課金が存在しない**(常時稼働インスタンスがない)。GCP試算書§5が警告する「削除し忘れ」の時間課金リスクが構造的に存在しない

### 6.1 単価の出典(すべて2026-10-06取得)

| 項目 | 単価 | 出典 |
|---|---|---|
| Workers Paid | $5/月(最低)・リクエスト1,000万/月込み(超過$0.30/100万)・CPU時間3,000万CPUミリ秒/月込み(超過$0.02/100万CPU ms) | <https://developers.cloudflare.com/workers/platform/pricing/> |
| Hyperdrive | Workers Paidに含まれる・クエリ課金なし(Freeプランのみ10万クエリ/日) | <https://developers.cloudflare.com/hyperdrive/platform/pricing/> |
| Queues | $0.40/100万操作・月100万操作無料(1msg≈3操作) | <https://developers.cloudflare.com/queues/platform/pricing/> |
| Workers Analytics Engine | 月1,000万データポイント込み(超過$0.25/100万)・現時点課金なし | <https://developers.cloudflare.com/analytics/analytics-engine/pricing/> |
| Workers Logs | 月2,000万イベント込み(超過$0.60/100万) | 同上pricing |
| Neon Launch | compute $0.106/CU-h・ストレージ $0.35/GB月・Instant Restore $0.20/GB月 | <https://neon.com/pricing> |
| Supabase Pro+Micro | $25+$10/月(7日バックアップ込み)・PITR +$100/月 | <https://supabase.com/pricing> |
| Upstash | $0.20/10万コマンド・ストレージ$0.25/GB(1GB無料) | <https://upstash.com/pricing/redis> |

### 6.2 シナリオ1 — Phase 0(20〜50人・2週間)

| 項目 | 計算式 | 金額 |
|---|---|---|
| Workers Paid | 最低月額 | $5.00 |
| リクエスト・CPU・Queues・Logs・Cron | リクエスト2.8万(40人×50req/日×14日)・CPU数百万ミリ秒・Queues約1.5万操作 → いずれも無料枠内 | $0 |
| Neon compute | autoscaling 0.25〜1CU・平均0.5CU想定: 0.5CU×336時×$0.106 = $17.81 | |
| Neon ストレージ | 10GB×$0.35 | $3.50 |
| Neon Instant Restore(PITR相当) | 10GB×$0.20 | $2.00 |
| Upstash | 1.5万コマンド/日(仮定)×14日=21万×$0.20/10万 | $0.42 |
| LLM(GCP試算書§7按分) | 同値(インフラ非依存) | $6.54 |
| **合計(Neon組)** | 5.00+17.81+3.50+2.00+0.42+6.54 | **約$35.3** |
| 参考: Supabase組 | 5.00+35.00($25+$10・最低1ヶ月分)+0.42+6.54 | **約$47.0**(PITR追加なら$147.0) |

GCP案(シナリオ1: 約$56)との差は**−$21(Neon組)**。ただしNeonのcompute単価はautoscalingの実際の使用次第で動く仮定(1CU常時なら+$18)であり、幅を持って見るべきである。削除忘れの構造的リスクがない点は金額に出ない利点である(GCP試算書感度#10の対極)。

### 6.3 シナリオ2 — Phase 1(500人・1ヶ月)

アクセス仮定はGCP試算書と共通(500人×50req/日=75万req/月・1万イベント/日=30万msg/月)。

| 項目 | 計算式 | 金額 |
|---|---|---|
| Workers Paid | 最低月額 | $5.00 |
| CPU時間超過 | API 75万req×50ms CPU=37.5M + イベント30万×500ms CPU=150M → 計187.5M−無料枠30M=157.5M×$0.02/100万 | $3.15 |
| リクエスト超過 | 75万<1,000万 | $0 |
| Queues | 30万msg×3操作=90万+リトライ分 → 100万操作無料枠内(リトライ率10%超なら超過・+$0.40/100万) | $0 |
| Logs・Analytics Engine | 250万イベント/月(仮定)・データポイント月数百万 → 枠内 | $0 |
| Neon compute | 1CU常時: 1×730時×$0.106 = $77.38(0.5CU平均なら$38.69) | |
| Neon ストレージ | 20GB×$0.35 = $7.00 | |
| Neon Instant Restore | 20GB×$0.20 = $4.00 | **計 $88.38** |
| Upstash | 10万コマンド/日(仮定)×30日=300万×$0.20/10万 | $6.00 |
| LLM(T1 v0.3・同値) | | $139.95 |
| **合計(Neon組)** | 5.00+3.15+0+0+0+88.38+6.00+139.95 | **約$242/月** |
| 参考: Supabase組(PITR込み) | 5.00+3.15+0+0+0+135.00($25+$10+$100)+6.00+139.95 | **約$289/月** |
| 参考: Supabase組(PITRなし) | 同上から−$100 | **約$189/月**(04 §7違反の参考値) |

GCP案(約$262)との差は**−$20(Neon組)**。LLMを除くインフラだけで見ると$122→$102(Neon組)の17%減である。CPU時間課金の仮定(CPU 50ms/req・500ms/イベント)は保守側の置きで、実際の実測で月$1.7〜$3.2の幅に収まる見込み。仮定が2倍に外れても$6/月程度であり、金額の支配要因は外部Postgresにある。

### 6.4 金額から読めること

PITR(04 §7)を守るという同じ条件で比べると、差は小さい。Cloudflare案の費用優位は**月$20前後(LLM込みの8%・インフラ単体の17%)**に留まる。逆にPITRを7日バックアップへ緩和する判断ができるなら(Supabase Pro・月$189)、GCP案より月$73安くなる。つまり**Cloudflare案の費用評価は「04 §7のPITR保持期間をどこまで必要とするか」という要件判断に従属する**。加えてNeon組の安さには東京リージョンなし(§4.4)という質的条件が付随しており、「安いが遠い」である。

## 7. リスクと未確認事項 — 推測で埋めなかった点

### リスク

| # | リスク | 内容と現時点の評価 |
|---|---|---|
| 1 | **GA後2週間の成熟度** | Python Workers GA(2026-09-21)から本調査日(2026-10-06)まで2週間未満。主要なC拡張(greenlet・cryptography)が未対応であることが移行障害そのものになっており、エコシステムの成熟待ちが計画に組み込めない(時期不明の待ち時間) |
| 2 | **ドライバ差の潜在欠陥** | asyncpg→psycopg差し替えで、M2 ws-1以降に実績を積んだuuid[]バインド・UUID復元・UTC固定の規律が全面的に再検証となる。1754件が通っても、型変換の差(暗黙/明示)が実データ経路で顔を出す型のリスク。直列化(cf-1案(a))による性能劣化は未実測 |
| 3 | **レイテンシの構造問題(Neon組)** | 東京リージョンなし。ユーザー(日本)↔Workers↔Neon(シンガポール)のどこに置いても片方の遅延が伸びる。保存p95 500msへの圧力。Supabase Pro(東京)なら消えるがPITR $100か要件緩和と引き換え |
| 4 | **常駐モデル転換の設計リスク** | debounce 10秒窓のDO/Queues遅延化は、障害時挙動・再送責務・冪等の取り決めを含む設計のやり直し。現行コードの逐次実行との等価性を試験で示す必要がある(cf-5) |
| 5 | **単一Postgresの可用性** | 04 §6のマルチAZ・99.5%(01 §20)に対し、Neon/Supabaseの安プランの実冗長性・SLAは未確認。Cloud SQLと同じ「単一ゾーン」位置づけに落ち着く可能性が高い |
| 6 | **ベンダー数の増加** | 実行(Cloudflare)+DB(Neon/Supabase)+Redis(Upstash)+観測(Cloudflare内か外部)+LLM(3社)=管理対象がGCP案より増える。障害時の切り分け対象も増える |

### 未確認事項(一次情報が取れなかった点・推測はしていない)

1. **D1のPostGIS/pgvector非対応の公式明記**: 「D1=SQLite互換」の製品分類(一次docs)から非対応と判断したが、明示的な非対応リストは確認できていない
2. **Supabaseの東京リージョン・PostGISバージョン**: dashboard選択情報と第三者記述のみ。一次docsでリージョン一覧を取得できなかった
3. **Upstashの東京リージョン**: 第三者レビューにap-northeast-1の言及あり・公式リージョン一覧は2026-10-06時点で取得できず
4. **Smart PlacementのPython Workers対応**: docsに明記なし(§4.10)
5. **Queuesのエンドツーエンド遅延の実測値**: docsに数値なし。初期LATCH判定p95 10秒の配分に入るため実測が要る
6. **依存パッケージの残りの実行環境要件**: google-genai・typesafe-sdk・alembicのWasm環境での動作可否(純度の棚卸しはcf-4で行う)
7. **Durable Objectsの料金詳細**(debounceに使う場合の額)と、軽量DOのWorkers Paid内の扱い
8. **Neon/SupabaseのSLAと実冗長性**(99.5%要件との突合)
9. **Workers Logsの課金体系**: 2026-12-01以降にCloudflare Observabilityへ移行予定の記載あり(pricing一次)・移行後の額は未確認
10. **psycopg(同期)のHyperdrive経由での実動作**: 公式docsは「同期ORM対応」としか書かず、SQLAlchemy sync+psycopgの組合せの実検証(cf-1スパイク)が前提

## 8. 判定のための要点 — どんな条件なら勝つか・負けるか

費用・工期・リスクの3軸で、オーナーが判断に使える形に整理する。

### 8.1 Cloudflare案が勝つ条件

1. **Phase 0のような短期検証を繰り返す運用で、環境の常時課金をゼロ近くにしたい場合。** 削除し忘れの時間課金リスク(感度#10)が構造的に存在しない。インフラ合計はWorkers $5+Postgres(使った分)+Redis(使った分)で収まり、GCP案の「削除忘れ約$110/月」の恐れが原理的にない
2. **サーバーレスの運用特性(パッチ適用不要・インスタンス管理不要)を本番要件の上位に置く場合。** 04 §6の「マネージドサービスでステートフル運用ゼロ」の精神をさらに先まで推し進めた形になる
3. **04 §7のPITRを「7日間の保持」で十分と緩和できる場合(Supabase Pro・月$189)。** 費用はGCP案より月$73安い。ただしdocs改版(オーナー領域)が前提になる

### 8.2 Cloudflare案が負ける条件

1. **04 §3〜§7の本番確定値を本番でもそのまま守る場合。** 単一クラウド・Cloud Monitoring+OTLP・マルチAZの3点が構成の前提と合わず、「本番同構成の検証」(11 §1のPhase 0の趣旨)として不適格になる
2. **PITR要件を保持し、かつ東京リージョンを要求する場合。** Neon(最安・PITR込み)は東京なし・Supabase(東京あり)はPITR $100でGCP超え。要件の組合せによっては費用優位が消える
3. **Phase 0までに既存資産(テスト1754件・非機能試験7種の証拠)をそのまま使いたい場合。** cf-1〜cf-5の大改修+cf-9の試験再整備は、GCP案(nginx→Cloud Runの読み替え中心・DBドライバ無変更)と比べて工期・検証量が明確に大きい

### 8.3 総合と次の一手

3軸を重ねると「費用: 微優位(条件次第で逆転)・工期: 大幅不利・リスク: 大幅不利」である。**現時点でGCP確定案をCloudflare案に差し替える判断につながる要素は見つからなかった。** 一方で、本調査が無駄にならない値は3つある。

1. **04 §3は「スケール後の再選定を妨げない」との原則(§1冒頭)で書かれており、Cloudflare構成はIntentプールが10万規模に達した段階の再選定候補として知見留めする価値がある**(本書自体がその際の下調べになる)
2. **DBのクラウド非依存は既に高い**(text()生SQL・ORMなし)。仮に将来の選択肢を広げたい場合、最初に評価するのはHyperdriveではなく「Neon等の外部Postgresへの接続先変更」単体であり、Workers移行と切り離して検討できる。GCP案のCloud SQLとNeonの比較(費用・東京・PITR)は本書§4.4をそのまま使える
3. **cf-1スパイク(SQLAlchemy sync+psycopgのHyperdrive動作)だけは、Phase 0開始前の余裕がある時に2〜3日で実験する価値がある。** 結果が「SQL無修正で動く」であれば移行コストの見積りが1桁下がる可能性があり、「動かない」であれば本書の結論が確定する。着手はオーナーの判断に委ねる
