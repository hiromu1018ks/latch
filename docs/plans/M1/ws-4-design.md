# M1 ws-4(レート制限)設計メモ

- 作業単位: ws-4 — レート制限: Active 5件・作成20件/日・更新6回/時・API 60req/分(Redis・JST日付キー)(docs/plans/STATUS.md M1表 / 出典 12 M1-6 / 08 §5.4・04 §5 / 依存: ws-3・M0(Redis)=マージ済み)
- 作成: 2026-09-28(agent1 / superpowers:brainstorming使用)
- 次工程: 計画書(ws-4-plan.md)へ変換 → 実装エージェントがworktree内でTDD実装
- 並走: なし(実行waveは ws-3 → ws-4 → ws-5 の直列)。agent4のdocs/learn/更新には触れない。**マイグレーションを追加しない**(§3)ため、STATUS運用ルール1〜3(test-ci同時実行制約)には抵触しない。apiイメージの再ビルドが検証時に必要(運用ルール4)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M1スコープ6(12 M1-6)を実装する。08 §5.4が確定した上限値4種 — Active Intent数5件・Intent作成20件/日・Intent更新6回/時・API全体60req/分 — をRedisカウンタとDB計上でAPI Layerに強制する。狙いはコスト保護の第1層「源流の抑制」であり(08 §5.4)、反復更新によるMatch Eventの大量発生(01レビューF-32)でJevコスト上限(04 D-16)を食い潰す攻撃経路を、Eventが発生する前のAPI受付段階で塞ぐ。通過後の消費抑制は06 v0.3 §5のJev予算が担う(二層の保護)。

Active数上限のみ422(既存Intentの停止・期限切れを促す)、他の3種は429(05 §5エラー形式表)。DBスキーマ変更・マイグレーションなし。Redisはcompose常設(latch-ci-redis)をそのまま使う。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | 上限値4種: Active Intent数 5件/ユーザー(超過時422・既存Intentの停止・期限切れを促す)/ Intent作成 20件/日/ユーザー(429)/ Intent更新 6回/時/Intent(429)/ API全体 60リクエスト/分/ユーザー(429) | 08 §5.4 |
| 2 | 根拠: 正常な利用実態(Active数件・修正は数回)を損なわない下限と、反復更新によるMatch Event大量発生(01レビューF-32)でJevコスト上限(04 D-16)を食い潰す攻撃経路を源流で塞ぐ上限の間に置いた。Intent更新の上限は06のdebounce(10秒・更新のみ窓統合)と直交して働く | 08 §5.4 |
| 3 | カウンタはRedisで持ち、API Layerで強制する。この上限は源流の抑制であり、通過後のJev消費の抑制は06 v0.3 §5のJev予算(1Intent日次40回・1ユーザー日次120回)と頻度制限(30分)が担う(二層の保護) | 08 §5.4・04 §5 |
| 4 | error code: 422 ACTIVE_INTENT_LIMIT(適用範囲=Intents作成)/ 429 RATE_LIMITED(適用範囲=全API)。レート制限の超過はActive Intent数上限のみ422、作成・更新・API全体の上限は429 | 05 §5エラー形式表・05 §5冒頭 |
| 5 | POST /v1/auth/token・POST /v1/auth/refresh のerrorsに429 RATE_LIMITEDが列挙される(認証不要2エンドポイントにも適用の意思が契約上明示) | 05 §5認証・ユーザー系 |
| 6 | 認証は全API認証済みユーザーのみ。認証不要はauth/tokenとauth/refreshの2つのみ。JWT claimはauth_provider+auth_subjectでUser行と紐付く | 05 §5冒頭・C3 |
| 7 | Redisはセッション失効リスト・ブロックリスト・友人関係・Jev実行カウンタの4用途を1つで賄う(実装ではレート制限カウンタが5用途目) | 04 §3 |
| 8 | すべての時刻参照はClockインターフェース経由(FR-41)。適用範囲に「RedisカウンタのJST日付キー」を含む。テスト環境ではClock操作でJST 0時を再現できる | 04 §5 |
| 9 | カウンタのリセット手段: スケジューラのリセットジョブ(JST 0時キック)+初回要求での自己修復。**TTL方式はUTC基準となりJST 0時と9時間ずれるため用いない**。ジョブはClockのJST日付境界を参照する | 04 §5 |
| 10 | resumeはversion+1の再評価Event(update種)を発行する(idempotencyキー衝突回避。pause→resumeの繰り返しで必ず衝突するため) | 05 §6・06 §9起点補償(ws-3実装済み) |
| 11 | Intent遷移表: active→paused・paused→active(resume)。draft→active。expiry_sweeper(active→expired)はM3実装(12 M3-3)でありM1時点では期限切れ行のstatusは残留する | 05 §6 |
| 12 | DB・Redis等の依存障害は503 DEPENDENCY_UNAVAILABLE(全API) | 05 §5エラー形式表・intents/errors.py既存 |
| 13 | ログ・計測・例外にユーザー由来の内容を含めない。許可リスト方式(例外メッセージはIDとコードで表現) | 08 §2.4 |
| 14 | Intent作成数・更新頻度の上限は実装するがMVP受け入れ条件外(コスト保護の一部) | 02 D-12 |
| 15 | 429 RATE_LIMITED(作成20件/日・更新6回/時・API 60req/分)と422 ACTIVE_INTENT_LIMIT(Active 5件)はws-4=本単位が実装する(ws-3は未実装のまま引継ぎ) | ws-3-design確定値24・12 M1-6 |
| 16 | /health は運用プローブ用でv1 API契約の外(C3の対象外) | M0設計・main.py |
| 17 | Intent保存 p95 500ms(性能目標。レート制限チェックの追加はRedis INCR 1〜2本) | 04 §7 |
| 18 | ci環境はAPI 1インスタンス・Redis常設。make test-ci はapiイメージを再ビルドしない(コード変更後の検証では docker compose build api が先行必須) | 10 §1・STATUS運用ルール4 |
| 19 | テストファイルのbasenameはtests配下全体で一意(並走マージ時の確認含む) | STATUS運用ルール5 |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

- **auth/**: Redis実運用の先行例。SessionStore(decode_responses=True の redis.asyncio.Redis を注入するStoreパターン・鍵接頭辞 `auth:` でJevカウンタ等と名前空間分離・例外はserviceが503へ包む)。本単位はtoken/refreshへlimiterフックを入れるためservice.pyを最小変更する(§2.5)
- **intents/(ws-2・ws-3)**: CRUD完成。Active数の計上対象はこのAPI(POST /v1/intents・PATCH・resume)。IntentService はclock・store・uow・reader・geocoderの注入構成 — limiterも同じ形で追加(§2.4)。検証順序(形式→必須3→時刻→年齢→ジオコーディング→保存)はws-3-design §2.5確定
- **core/clock.py**: `jst_date()` がJST暦日付の正規経路。FakeClock.set/advanceでJST 0時・時間境界を試験再現できる。arch test(test_arch_no_direct_time)が毎コミットでClock経由を強制
- **main.py**: lifespanがredis_clientを生成(現在はbuild_auth時のみ — §2.6で常時生成へ)。app.state経由のサービス注入パターン(create_app引数)がテスト差し替えの既定手段
- **settings.py**: LATCH_環境変数プレフィックス。上限値の設定可能性(§2.8)はこの既定パターンに乗る
- **compose(latch-ci)**: Redis常設・api 1インスタンス。環境変数はLATCH_DATABASE_URL/LATCH_REDIS_URLのみ(上限値の上書き口を追加可能)

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

- **Jev予算(1Intent日次40回・1ユーザー日次120回・頻度制限30分)とD-16回数上限・リセットジョブ・80% alert** — M2-9(04 §5・06 §5)。本単位のカウンタはレート制限4種のみで、Jev実行カウンタを扱わない
- **ブロック・通報** — M3(08 §5.1〜§5.2)
- **expiry_sweeper(期限切れIntentのexpired遷移)** — M3-3。本単位は期限切れ残留行を計上から除外するだけで遷移させない(§2.3)
- **Retry-Afterヘッダ等のクライアント再試行契約** — 05 §5に規定なし。作らない
- **フロントエンドの429/422表示対応** — ws-5(12 M1-7)

## 2. 実装方式の選択肢と推奨

### 2.1 パッケージ構成 — 推奨: 新規 `ratelimit/` パッケージ(errors・store・limiterの3ファイル)

選択肢:

- **A(推奨). `backend/src/latch/ratelimit/` を新設する**(errors.py・store.py・limiter.py)。429 RATE_LIMITEDの適用範囲は全APIでありintentsドメインの外なので、横断関心事として独立パッケージに置く。422 ACTIVE_INTENT_LIMITは「Intents作成」適用のステータス検証なので、例外的に intents/errors.py へ IntentsError の派生で追加する
- **B. core/ へ置く**: coreはclock・db・depsの基盤モジュール。Redisを使うビジネスロジック(上限値判定)を基盤に置くと責務が混ざる
- **C. intents/ 内蔵**: 60req/分とauth系への適用がintents外のため不適

推奨の根拠: B・Cはそれぞれ基盤・ドメインの境界を壊す。Aはauth/(Redis実運用の先行例)と対称な「StoreにRedis操作を閉じ込め・service/limiterが判断する」構成で、ユニット試験はスタブRedisで置換できる。

### 2.2 カウンタ方式 — 推奨: Redis INCR + JSTバケット日付キー(固定窓)

選択肢:

- **A(推奨). INCR+JSTバケットキー(固定窓)**: キーにJST暦のバケット(日付・日付+時・日付+時+分)を埋め込む。バケットが進むと自動的に新しいキーへ切り替わる=リセット。TTLは掃除用にしか使わない
- **B. sliding window(ZSET+ZREMRANGEBYSCORE)**: 厳密な窓だが実装と試験が複雑で、Redisメモリも増える
- **C. DBでカウント**: 08 §5.4「カウンタはRedis」に反し、書き込みも増える
- **D. TTLのみでリセット(24時間TTL等)**: 04 §5が明示的に否定(UTC基準になりJST 0時と9時間ずれる)

推奨の根拠: 12 M1-6の「Redis、JST日付キー」の字義はAそのもの。Aはリセットジョブ不要(04 §5のジョブはJev日次/月次カウンタ向けの規定で、キー埋め込み方式ならジョブの単一障害点と自己修復の複雑さを最初から回避できる)。固定窓の弱点は境界での瞬間2倍(分窓で2分間に119req・時窓で2時間に11回更新)だが、08 §5.4の上限は源流抑制が目的で通過後のJev予算(二層目)があり、境界2倍は許容できる。Clockのjst_date()/now()からバケット文字列を導出するため、FakeClockでJST 0時・時間境界の試験が決定的に再現できる(確定値8)。

### 2.3 Active 5件(422 ACTIVE_INTENT_LIMIT)— 推奨: DB COUNT計上 + users行ロックで直列化

**計上の単位と条件**: `SELECT COUNT(*) FROM intents WHERE user_id = :uid AND status = 'active' AND (expires_at IS NULL OR expires_at > :now)`。期限切れ残留行(sweeperがM3未実装のためstatus='active'のまま、確定値11)は、遷移表上すでにexpired相当なので計上から除外する。除外しない場合、期限の切れたIntentがActive枠を占有し続け、08 §5.4が促す「期限切れ」による枠解放が機能しなくなる。

**適用タイミング**: (1) POST status=active、(2) PATCH draft→active化、(3) resume(paused→active)の3箇所。いずれもstatus='active'が増える操作ですべて直前に検証する。PATCH(active/paused内容更新)はActive数を増やさないため対象外。

**競合制御**: COUNT→INSERTの間に同一ユーザーの並行リクエストが COMMITすると6件目が通る(read committedの視界)。users行を `SELECT ... FOR UPDATE` してからCOUNT→書き込みまで同一トランザクションで行い、同一ユーザーのActive化操作を直列化する。users行ロックは1行・短時間で、60req/分の入口制限と併せて実害はない。

選択肢(計上手段):

- **A(推奨). DB COUNT**: Active数はpause・resume・delete・期限で変動するDB状態そのもの。DBが真実の源
- **B. Redisカウンタ(INCR/DECR)**: 変動要因(pause・delete・期限切れ)ごとにDECRを配線する必要があり、ずれれば恒久的な不整合(枠の枯渇・解放し放題)になる

推奨の根拠: 08 §5.4「カウンタはRedis(04)で持ち」は作成・更新・API全体の3種(アクセス頻度=Redis向き)を指すと解釈する。Active数のみステータスコードも422で「リソース状態の検証」であり、真実の源はDB。スキーマ変更も不要になる。

エラー: `ActiveIntentLimitError(IntentsError)`(http_status=422・code=ACTIVE_INTENT_LIMIT)をintents/errors.pyへ追加。既存のIntentsErrorハンドラがそのままenvelope化する(main.py変更不要)。

### 2.4 適用位置と検証順序 — 推奨: API全体は依存関数・作成/更新はIntentServiceへlimiter注入(401→429→422の順)

**全体原則**: 401(認証)→ 429(レート制限)→ 422(ドメイン検証)の順で拒否する。未認証リクエストにユーザー状態を晒さず、攻撃的な流量にはドメイン検証(ジオコーディングSQL等)を消費させる前に429で返す。

**API全体 60req/分(429)**: FastAPIの依存関数 `api_rate_limited` を新設し、`require_authenticated` を内包する(claimsからuser_idを得てINCR)。users・intents(parse・crud)・auth(logout)の各ルータの `dependencies=[Depends(require_authenticated)]` を `dependencies=[Depends(api_rate_limited)]` へ差し替える。差し替え忘れは試験(§4)で網羅確認する。/health は依存を付けない(v1契約外・確定値16)。

- ミドルウェア方式は不採用: user_idのためのJWT再パースが二重実装になり、/health除外の分岐も生じる。依存方式は「v1 API契約にのみ適用」を構造で表現できる

**作成 20件/日・更新 6回/時(429)**: IntentServiceへlimiterを注入(Optional。None=無効)し、ユースケース内で判定する。routesの依存とservice内の判定に分かれると検証順序の真実が割れるため、ws-3-design §2.5が確定した「検証順序はserviceが統制」を踏襲する。挿入位置は検証順序の先頭:

- create: user解決 → **作成20/日 INCR(429)** → **Active数検証(active時のみ・422)** → 必須3 → 時刻 → 年齢 → ジオコーディング → 保存(既存§2.5の順序は無変更)
- update: user解決・行ロック → **更新6回/時 INCR(429)** → 既存分岐(draft再保存・active更新・draft→active化)。draft→active化では作成種Eventを発行するが行は既存なので作成カウントは消費せず、更新カウントで数える
- resume: **更新6回/時 INCR(429)** → **Active数検証(422)** → 遷移

**更新6回/時の適用対象**: PATCHの全分岐(内容更新・draft再保存・draft→active化)+resume。resumeを含める根拠は、resumeがupdate種のMatch Eventを発行する(確定値10)ため、pause/resume連打でEventを大量発生させる経路が残るから(08 §5.4の攻撃経路堵塞の趣旨)。pause(Event不発行)とdelete(1回しか成功しない遷移)は対象外。

**INCRは判定より先行させる**(429を返したリクエストもカウント済み): INCR→判定がRedis上アトミックに近く、検査とカウントの間の並行すり抜けがない。429後の連打はすべて429になり、窓が進めば自動回復する。失敗作成(422等)もカウントを消費するが、20/日の上限は正常利用(数件)との間に十分余裕がある。

### 2.5 auth/token・refresh への適用 — 推奨: IdP検証後に provider+subject 単位(401優先を保持)

05 §5はtoken/refreshのerrorsに429 RATE_LIMITEDを列挙する(確定値5)。適用する。単位は08 §5.4「/ユーザー」に従い、認証前のため provider+subject(IdP検証で確定する値。User行の有無によらない)を単位とする。

- **IP単位は不採用**: docsのどこにもIP単位の規定がなく、04 §6のマネージドコンテナ基盤ではX-Forwarded-For系の信頼できるプロキシ設定が前提になっていない(信頼できないIPは偽装で回避される)
- **実装位置**: AuthServiceへlimiterを注入し、IdP検証の直後にINCRする(token・refreshとも)。IdP検証より前に置くと401 INVALID_IDP_TOKENより429が先に出て、401優先の原則(§2.4)に反するため
- キーのsubjectはメールアドレス等のPIIになり得るためsha256でハッシュ化して埋める(SessionStoreがrefresh tokenをSHA-256で保持するのと同じ規律)

auth/はM0完成資産だが、変更はコンストラクタと_token/_refreshへのフック2箇所に限定する(limiter引数はOptionalで既存の直接構築を壊さない)。

### 2.6 Redis断絶時の挙動 — 推奨: fail-closed(503 DEPENDENCY_UNAVAILABLE)

Redisへ接続できない場合、レート制限チェックは503 DEPENDENCY_UNAVAILABLEで失敗する(確定値12)。「制限なしで通す(fail-open)」はコスト保護の穴を開け、しかも既存の認証経路は失効リスト照会にRedisが必須で同じ障害時にすでに503になる — つまりfail-openにしてもAPI全体の可用性は戻らず、開放する実利がない。挙動を一貫させる。

ratelimit/errors.py に `RateLimitError` 基底と `RateLimitedError`(429 RATE_LIMITED)・`RateLimitDependencyError`(503 DEPENDENCY_UNAVAILABLE)を置き、main.pyへハンドラを1つ追加する。StoreのRedis例外はlimiterが503へ包む(auth・intentsのserviceと同じ形式)。

### 2.7 Redisキー設計とTTL

```
rl:api:{user_id}:{yyyymmddHHMM}                  INCR  上限60  TTL 120秒
rl:create:{user_id}:{yyyymmdd}                    INCR  上限20  TTL 48時間
rl:update:{intent_id}:{yyyymmddHH}                INCR  上限6   TTL 13時間
rl:auth:{provider}:{sha256(subject)}:{yyyymmddHHMM}  INCR  上限60  TTL 120秒
```

- 接頭辞 `rl:` は `auth:` と名前空間を分離(SessionStore冒頭の規定どおり。JevカウンタはM2で別接頭辞)
- yyyymmdd/HH/MM はJST暦。日付は `clock.jst_date()`、時・分は `clock.now().astimezone(JST)` から導出する(時刻参照はすべてClock経由 — 確定値8・arch test強制)
- INCRとEXPIREはpipelineで毎回併発する。バケットキーは時間の進行とともに新キーへ切替わるため、EXPIREの毎回上書き(TTL延長)は無害。INCR後の値で判定する(§2.4のINCR先行)
- TTLは掃除用に留め、リセット表現には使わない(確定値9のTTL不採用を遵守。リセット=キー切替)
- 上限値はSettingsフィールド(rate_limit_api_per_min=60・rate_limit_create_per_day=20・rate_limit_update_per_hour=6・rate_limit_active_intents=5)とし、環境変数(LATCH_)で上書き可能にする(§2.8)

### 2.8 既存実装・試験との整合(ws-3 CRUD試験の保全)

- **lifespanのRedis生成を常時化**: 現在redis_clientはbuild_auth時のみ生成。RateLimiterのためbuild_auth/user/intentsの判定と独立に生成し、`app.state.rate_limiter` へ載せる。create_app引数での差し替え(rate_limiter=Noneで無効)も既存パターンに追加する
- **IntentService・AuthServiceのlimiter引数はOptional(既定None=無効)**: 既存unit試験はサービスを直接構築しているため、引数なしの構築がそのまま動く(引数追加のみで壊さない)
- **既存integration試験はデフォルト上限のまま通る見込み**: 試験はsubject(uuid接尾辞)とintentを毎回ユニークに作るため、ユーザー単位・Intent単位のカウンタは試験間で分散する。1テスト内の同一ユーザーリクエスト数(10前後)・同一IntentへのPATCH回数(2回まで)・同時Active数(2〜3件)はいずれも上限未満。この見込みを§4の回帰試験で検証する
- **上限の上書き口**: compose apiの環境変数(LATCH_RATE_LIMIT_*)で引き上げ可能。将来の負荷試験・試験追加で必要になった時に使う(本単位ではcompose.yamlの変更は不要)
- **検証時の手順**: apiイメージ再ビルドが必須(確定値18)

## 3. ファイル構成

### 3.1 新規に作るもの

```
backend/src/latch/ratelimit/
  __init__.py    # RateLimiterファクトリ・export
  errors.py      # RateLimitError基底・RateLimitedError(429)・RateLimitDependencyError(503)
  store.py       # RateLimitStore(Redis INCR/EXPIRE・キー組立を閉じ込める。decode_responses=TrueのRedisを注入)
  limiter.py     # RateLimiter(check_api/check_create/check_update・上限判定・バケット文字列はclockから導出)
backend/tests/unit/ratelimit/
  test_store.py      # スタブRedis(dict)でINCR・TTL・キー形式
  test_limiter.py    # 上限境界(60件目OK・61件目429)・Redis例外→503・カウント種別の分離
  test_jst_boundary.py  # FakeClockでJST 0時/時/分バケットの切替(リセット再現)
backend/tests/integration/
  test_ratelimit_api.py  # 実api・実Redis・実DBでの429/422のE2E(§4.2)
```

### 3.2 修正するもの(既存ファイル)

| ファイル | 変更内容 |
|---|---|
| settings.py | 上限4種のフィールド追加(§2.7。既定=仕様値) |
| main.py | lifespanでredis_client常時生成・app.state.rate_limiter構築・RateLimitErrorハンドラ追加 |
| intents/errors.py | ActiveIntentLimitError(422 ACTIVE_INTENT_LIMIT)追加 |
| intents/store.py | count_active(COUNT句は§2.3の条件)・lock_user_row(users行FOR UPDATE)追加 |
| intents/service.py | limiter注入(Optional)・create/update/resumeの3箇所へフック(§2.4の順序) |
| intents/routes.py | parse_router・intents_crud_routerの依存をapi_rate_limitedへ差し替え |
| users/routes.py | users_routerの依存をapi_rate_limitedへ差し替え |
| auth/routes.py | logout_routerの依存をapi_rate_limitedへ差し替え |
| auth/service.py | limiter注入(Optional)・_token/_refreshへIdP検証後のフック |

### 3.3 触らないもの

- core/(clock.py・db.py・deps.py — jst_date・JST定数はimportのみ)・users/service.py・geo/・llm/・worker/・alembic/(マイグレーション追加なし)・compose.yaml・prototype/・docs/learn/(agent4管轄)・docs/(仕様書群)
- intents/のparse系資産(schema.py・completion.py・prompt.py・mapping.py・events.py・IntentParseService)・store.pyの既存メソッド
- auth/のtokens.py・idp.py・sessions.py・deps.py

## 4. テスト方針

### 4.1 unit(FakeClock・スタブRedis)

- **store**: スタブRedis(dict実装)でINCRの積算・EXPIRE設定・キー文字列の形式(接頭辞・バケット・ハッシュ化)を検証
- **limiter**: 上限境界(60件目は受理・61件目でRateLimitedError)・上限値のSettings差し替え・Redis例外の503への包み・429時もカウントが進むこと(INCR先行)
- **JSTバケット境界**: FakeClockで23:59JST→00:00JST・時境界・分境界をまたぐと新しいキーへ切り替わること(=日次/時間/分のリセットがClock操作で再現できる — 確定値8)。日付の導出がjst_date()経由であること
- **Active数検証(service・スタブstore)**: count_active=5で6件目のactive作成・draft→active化・resumeがActiveIntentLimitError(422)。期限切れ行(expires_at<=now)が計上から除外されること。PATCH(active内容更新)は422にならないこと
- **検証順序**: Active上限と必須3欠落が両立するリクエストで422 ACTIVE_INTENT_LIMITが先(§2.4の順序どおり)
- **既存試験の回帰**: limiter=None構築でws-3のunit試験が無修正でグリーン

### 4.2 integration(実api・実Redis・実DB。apiイメージ要再ビルド)

- **作成20件/日**: 1ユーザーでdraft作成を20件(201)→21件目で429 RATE_LIMITED。翌日の窓は実時間で再現しないため日次リセットはunit(4.1)で担保する
- **更新6回/時**: 同一IntentへPATCH 6回(200)→7回目で429。別Intentは影響を受けないこと(単位=Intent)
- **API 60req/分**: 1ユーザーでGET /v1/intentsを60回(200)→61回目で429。別ユーザーは同時刻に60件まで受理されること(単位=ユーザー)
- **Active 5件**: 5件active化後に6件目のactive作成・draft→active化・resumeが422 ACTIVE_INTENT_LIMIT。1件pauseすると6件目が受理されること(枠解放)。expires_atをDB直接UPDATEで過去へ倒した行が計上から除外されること
- **resumeの更新カウント**: resume連打で6回目以降429
- **401優先**: レート制限超過状態のユーザーでも、無効JWTでは401 UNAUTHENTICATEDが返ること
- **auth系429**: 同一provider+subjectでtoken発行を60回→61回目429(IdP検証後のため無効idp_tokenの連打は401のまま)
- **/health**: レート制限の対象外(無認証で打ち続けても429にならない)
- **既存試験の回帰**: test_intents_crud_api・test_auth_api・test_users_api・test_intents_parse_apiがデフォルト上限のままグリーン(§2.8の見込みの検証)

### 4.3 品質ゲート

- arch test(直接時刻参照禁止)はratelimitモジュールにも自動的に効く(clock注入のみのため違反しない)。新規arch testは追加しない
- テストファイル名の一意性(確定値19): ratelimit配下とtest_ratelimit_api.pyで重複なし
- ログ検証: 429/422のログにcodeと上限種のみを出し、text・subject等を含めない(確定値13)

## 5. 未解決の論点(設計判断の告白 — レビューで確認を求める)

すべてdocsの根拠から判断したが、字義に複数の読みがあり得るため、承認時に確認してほしい:

1. **更新6回/時の対象にresumeを含めた**(§2.4)。08 §5.4の「Intent更新」はPATCHとだけ読むこともできるが、resumeがupdate種Eventを発行する(05 §6・06 §9)以上、pause/resume連打によるEvent大量発生の経路を残すのは攻撃経路堵塞の趣旨(08 §5.4根拠文)に反すると判断した
2. **期限切れactive行(expires_at<=now・sweeper未実施)をActive数計上から除外した**(§2.3)。計上に含めるとM3までの間、期限の切れたIntentが枠を占有し続ける。05 §6遷移表は期限切れactiveをexpired(=Activeでない)と定めるため、除外が遷移表の意思に沿うと判断した
3. **auth/token・refreshの429の単位をprovider+subjectとした**(§2.5)。08 §5.4は「/ユーザー」単位で、認証前のリクエストにuser_idが無いため、IdP検証で確定するprovider+subjectを単位にした。IP単位はdocsに根拠がなく不採用とした
4. **INCR先行(429を返したリクエストもカウント済み・422で失敗した作成も消費)**(§2.4)。判定とカウントの間のすり抜けをRedis上で潰すための判断。失敗作成の消費はユーザー体験の軽い割引(1日20の余裕内)と評価した
5. **固定バケット窓の境界で瞬間2倍を許容した**(§2.2)。分窓で2分間に119reqまで通る。源流抑制+Jev予算の二層構成(08 §5.4)を踏まえ、sliding windowの複雑さに見合わないと判断した

## 6. 次工程への引継ぎ

- 計画書(ws-4-plan.md)は§2〜§4をタスクへ分解する。実装順の推奨: ratelimit/(unit完結)→ intentsフック(Active数・作成/更新)→ 依存差し替え(API全体)→ authフック → integration
- 報告形式に「docker compose build api 実行後のtest-ci」を検証手順として明記する(確定値18)
