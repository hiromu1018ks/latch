# M0 ws-3(認証)設計メモ

- 作業単位: ws-3 — 認証: token/refresh/logout・Redis失効リスト・JWKS検証・テスト用認証構成(staging鍵ペア+JWT発行ツール)(docs/plans/STATUS.md M0表 / 出典 M0-4, M0-7 / 依存ハード制約 C3 / 前提: ws-1=マージ済み 28fde85・雛形/ws-2=main a247243)
- 作成: 2026-09-27(agent1 / superpowers:brainstorming使用)
- 次工程: 計画書(ws-3-plan.md)へ変換 → 実装エージェントがworktree内でTDD実装
- 並走: ws-4(地物データ)=マイグレーション追加単位。**ws-3はマイグレーションを追加しない**(§2.6)が、共有ci-dbを消費するため test-ci の同時実行はSTATUS運用ルールどおり禁止(計画書§0に明記すること)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

依存ハード制約**C3(認証基盤が全APIの前提)**を解消する。05 §5・04 D-21 の認証3エンドポイント(token / refresh / logout)と、(1)独自JWT発行・検証、(2)Redis失効リスト、(3)IdPトークンのJWKS検証経路、(4)テスト用認証構成(staging鍵ペア+テストユーザーJWT発行ツール)をM0で作る。**users API(POST /v1/users・GET /v1/users/me)はM1**(12 M1-1)であり、本単位はusers表の**読み取りのみ**を行う(§1.3)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | 認証は `Authorization: Bearer <JWT>`(04 D-21)。**認証を要求しないエンドポイントは POST /v1/auth/token と POST /v1/auth/refresh の2つのみ**。APIが発行するJWTのclaimには `auth_provider` と `auth_subject` を含め、認証済み操作はこの2claimでUser行と紐付ける | 05 第5節冒頭 |
| 2 | POST /v1/auth/token: request `{provider: "google"|"apple", idp_token}` → response 200 `{access_token, token_type:"Bearer", expires_in:3600, refresh_token:<回転式・30日>, user:{id:<uuid>\|null, profile_complete:false}}`。APIはIdPの公開鍵(JWKS)でidp_tokenを検証し、auth_provider+auth_subjectでUserと紐付ける。**紐付くUserがない場合も認証は成功(200)**、User作成は次のPOST /v1/users(初回登録フロー)。`profile_complete:false` が初回登録待ちの合図。errors: 401 INVALID_IDP_TOKEN、429 RATE_LIMITED、503 DEPENDENCY_UNAVAILABLE(JWKS取得失敗等) | 05 第5節 認証・ユーザー系 |
| 3 | POST /v1/auth/refresh: request `{refresh_token}` → 200 `{access_token, token_type, expires_in, refresh_token:<新>}`。**回転式で、応答時に旧トークンを無効化**。無効化済みトークンの再提示は盗難の疑いとして401 INVALID_REFRESH_TOKENを返し、**当該ユーザーの当該IdPセッションのトークン族全体を失効**(安全側に倒す)。errors: 401 INVALID_REFRESH_TOKEN、429 RATE_LIMITED | 05 第5節 |
| 4 | POST /v1/auth/logout: request `{}`(AuthorizationヘッダーのJWTが対象)→ **204**。対象JWTをRedis失効リストへ登録し、対応するリフレッシュトークン族を無効化。以後の同JWTの提示は401 UNAUTHENTICATED | 05 第5節 |
| 5 | D-21確定値: IdPはGoogleとSign in with Appleの2種。アプリがIdP認証を行いIdPトークンをAPIへ提示、APIは**IdPの公開鍵(JWKS)で検証**しIdP種別+sub識別子でUserと紐付け。**APIが独自JWT(有効期限1時間)を発行。リフレッシュトークンは30日・回転式**。JWTはステートレスで検証し、ブロック・退会の即時反映は**Redisの失効リスト**で行う。アカウント連結はMVPに含めない | 04 第4節 D-21 |
| 6 | Redis選定理由: **セッション失効リスト、ブロックリスト、友人関係、Jev実行カウンタの4用途を1つで賄う** | 04 第3節 キャッシュ行 |
| 7 | エラー形式(共通): `{"error": {"code", "message", "details"}}`。401 UNAUTHENTICATED(JWT無効・期限切れ・**失効リスト掲載**)、401 INVALID_IDP_TOKEN(JWKS署名検証等)、401 INVALID_REFRESH_TOKEN、503 DEPENDENCY_UNAVAILABLE(**DB・Redis等の依存障害**。全API) | 05 第5節 エラー形式 |
| 8 | **認証のテスト構成(D-21)**: 本番と同じJWT検証経路(JWKS参照)を通しつつ、**staging専用の鍵ペアを注入**し、**テストユーザーのJWTを発行する内部ツール**を用意する。Google/Appleの実IdPには依存しない | 10 第1節 |
| 9 | ci環境は「最小構成(常設)。API 1インスタンス、Worker 1、DB共用」。redis:8-alpineがcomposeに常設済み(127.0.0.1:6379公開) | 10 第1節 / compose.yaml |
| 10 | M0スコープ4: 「認証: POST /v1/auth/token・refresh(回転式)・logout、Redis失効リスト、JWKS検証」。スコープ7: 「テスト用認証構成: staging専用鍵ペア注入とテストユーザーJWT発行ツール」。G0: 「**認証3エンドポイントとJWKS検証経路がci環境でグリーン**」 | 12 第3節 M0 |
| 11 | 時刻参照はすべてClockインターフェース経由(arch test `test_arch_no_direct_time` が `backend/src` 全体を強制。`datetime.now|utcnow|time.time|time.monotonic` のヒットが core/clock.py のみであることが雛形の完了条件) | 04 第5節(FR-41)・10 第1節 |
| 12 | users表: auth_provider text NOT NULL CHECK IN ('google','apple')・auth_subject text NOT NULL・**UNIQUE(auth_provider, auth_subject)**。**`profile_complete` 列は存在しない**(05 §2・移行0001にない) | 05 第2節 users / ws-1 0001 |
| 13 | レート制限(429 RATE_LIMITED)の実装(Active 5件・作成20件/日・API 60req/分・Redis JST日付キー)は**M1** | 12 第3節 M1-6 |
| 14 | API/WorkerへのDBランタイム統合(lifespanエンジン生成・app.state DI)・composeへの `LATCH_DATABASE_URL` 注入は「最初にDBを消費する単位」が行う(ws-1設計§1.3の引継ぎ。**それが本単位になる** — §1.3) | ws-1-design §1.3 |
| 15 | 実行環境メモ: dockerグループ参加済み・compose常設環境は3healthy+worker running | STATUS.md 完了記録 |

### 1.3 users API(M1)未実装での認証の扱い(supervisor指示による確定)

05 §5(確定値2)のとおり、**tokenエンドポイントはUser紐付けの結果を応答に含める契約**であり、Userがなくても200(初回登録待ち)が正当動作である。よって本単位は次のとおり確定する:

1. **users表への読み取りのみ行う**(SELECT id FROM users WHERE auth_provider=… AND auth_subject=…)。「最初にDBを消費する単位」(ws-1設計§1.3の引継ぎ事項)はM1ではなく**ws-3が引き受ける** — lifespanでのエンジン生成・`app.state` DI・compose apiへの `LATCH_DATABASE_URL` 注入を本単位で行う。なおws-1設計§1.3には「ws-3の認証はRedis+JWKSでDB未消費」という想定が記載されていたが、STATUS依存表(ws-3→ws-1(users))と05 §5の契約(users紐付け)が優先する
2. **SQLAlchemyモデル・リポジトリ層は作らない**(ws-1設計§2.4の「マイグレーションが真実源」方針を維持)。読み取りは `sqlalchemy.text()` の生SQL1本(`uq_users_auth_provider_subject` により高々1行)。モデル導入はM1
3. **`profile_complete` の導出**(確定値12: 列が存在しない): POST /v1/users(M1)はdisplay_name・birth_date必須で完全な行を一括作成するため、部分登録状態は存在しない。よって **`profile_complete` = 「User行が存在する」**(行あり=true・id返却 / 行なし=false・id=null)として導出する
4. **User行の作成経路はM1まで存在しない**。M0の通常運用ではUser行が常に不在のためtoken応答は常に `user:{id:null, profile_complete:false}` となる — 契約上正しい(初回登録フローの前段)。User紐付け経路の試験は、試験が直接INSERTしたUser行(固定リテラル時刻・ws-1試験規約)で検証する(§4.2-2)

### 1.4 スコープ外(後続単位へ渡すもの。ws-3では作らない)

- **users API(POST /v1/users・GET /v1/users/me)** — M1(12 M1-1)。USER_EXISTS・UNDER_AGE等もM1
- **レート制限(429 RATE_LIMITEDの実装)** — M1(12 M1-6。Redis JST日付キー)。本単位はerror codeの列挙型に含めるのみ
- **保護リソース側エンドポイント**(intents等のv1ドメインAPI)— M1〜M3。本単位は認証Dependency(§2.7)を提供するのみ
- **本番IdP実URL設定の実運用**(Google/AppleのJWKS URL・client_id等の確定値はデプロイ時。本単位は設定経路とprodガードのみ用意 §2.5)
- **鍵ローテーション運用・失効リストの容量設計** — kid付き鍵の差し替え経路は用意するが、運用サイクル設計はM4/11
- **Worker側の認証消費** — なし(Workerはユーザー要求を直接処理しない)。Pub/Sub・WorkerのRedis消費はM2
- **多要素・デバイス束ね・アカウント連結** — D-21によりMVP対象外

## 2. 実装方式の選択肢と推奨

### 2.1 ライブラリ選定(supervisor指示: Redis依存の追加判断を含む)— 推奨: PyJWT[crypto] + redis-py(devにfakeredis)

選択肢:

- **A(推奨). `pyjwt[crypto]`(JWT) + `redis>=5`(redis.asyncio)+ dev依存 `fakeredis`**
- B. python-jose + redis-py
- C. フルOSS IdP(Keycloak・Auth0 SDK等)を導入し独自JWT発行をやめる

推奨の根拠: **CはD-21と正面衝突**(APIが独自JWTを発行する確定値。コンポーネント追加でもある)し不採用。**Bのpython-joseは保守停滞とCVE履歴**があり新規採用の理由がない。PyJWTはJWT処理の事実上の標準で、`PyJWKClient`(JWKS取得・kid照合・キャッシュ)が本単位の「本番と同じJWKS検証経路」(確定値8)をそのまま供給する。`[crypto]` 追加で入るcryptographyがRS256(IdP検証)に必要。**Redisクライアントはredis-py公式のasyncサポート**(redis.asyncio)一択で議論の余地がなく、依存追加として `redis` 1本のみ。**fakeredis(devのみ)**はunit試験をプロセス外Redisなしで決定的に走らせるためのもので、redis-pyのコマンド解釈経路(GETDEL・SADD・TTL含む)をそのまま実行する — 独自InMemory抽象を作るより実挙動に近い(ws-2のClockがABC+差し替えなのに対し、RedisはRedisプロトコル自体が境界であるため二重抽象を作らない)。

トレードオフ: fakeredisと実Redisの挙動差は残る( TTLの実際の経過等)。よって**失効・回転の実効はintegrationで実Redisで検証する**(§4.2)。実時間を扱うサードパーティ内部(PyJWTのキャッシュ等)はarch testの走査対象外(`backend/src` のみ)であり、**自前コードの期限判定はClock由来に統一する**(§2.8)。

追加依存の確定値: 実行依存 `pyjwt[crypto]>=2.10`・`redis>=5.2` / dev依存 `fakeredis>=2.26`。

### 2.2 トークン3種の方式 — 推奨: アクセスJWT=HS256・IdPトークン検証=RS256(JWKS)・リフレッシュ=不透明乱数(Redis保持)

docsが固定するのは「独自JWT 1時間・検証はステートレス+Redis失効リスト」「IdPトークンはJWKSで検証」「リフレッシュは30日回転式」(確定値2〜5)。実装方式は本設計で確定する:

| トークン | 方式 | 根拠 |
|---|---|---|
| アクセストークン(独自JWT) | **HS256(対称鍵)**。claim: `iss="latch-api"`, `aud="latch-app"`, `sub="{provider}:{subject}"`(参照用), **`auth_provider`・`auth_subject`**(確定値1の必須claim), `jti`(uuid4), `sid`(セッション=リフレッシュ族id), `iat`/`exp`(Clock由来) | 検証者=発行者=API Layerのみ(04第2節の構成上、他コンポーネントはJWTを検証しない)。対称鍵はAPIプロセスだけが知ればよく、prodの設定が環境変数1つで済む。algは発行・検証とも `HS256` にピン留め(alg confusionの余地を構造的に塞ぐ) |
| IdPトークン検証 | **RS256(JWKS・kid照合)**。`PyJWKClient`(URL指定)または同梱JWKSからの静的鍵(§2.5) | 実IdP(Google/Apple)のJWKSがRS256公開鍵配布モデルであり、「本番と同じJWT検証経路(JWKS参照)」(確定値8)をci/stagingでも通すには公開鍵検証でなければならない |
| リフレッシュトークン | **不透明トークン**(`secrets.token_urlsafe(32)`・JWTではない)。**RedisにSHA-256ハッシュで保存**(生値をRedisに置かない。ダンプ漏洩時の悪用防止)。TTL 30日 | 05第2節にリフレッシュテーブルは存在せず(12テーブルになし)、04第3節のRedis 4用途「セッション失効リスト」がセッション状態の置き場として確定している。回転・再利用検知はサーバ側状態が必須であり、自己完結JWTでは「無効化済みトークンの再提示」(確定値3)を検知できない |

選択肢とトレードオフ: アクセストークンをRS256鍵ペアにする案は、kid付き鍵回転の経路が開く利点がある一方、prodで鍵ペアの注入運用が必須になり、docsが要求しない運用を増やす。HS256で代用できない要件(外部が独自JWTを検証する)はMVPに存在しない。リフレッシュをJWTにする案は状態を減らせるが、回転検知に結局Redisが要り、失効意味論がJWT自己主張とRedis事実の2系統に割れる。**いずれも不採用**。

### 2.3 Redisスキーマと回転・族失効の正当性 — 推奨: 失効リスト+族索引+消費済みマーカーの4鍵型

Redis 8(compose常設・GETDEL可用)上に次の鍵を置く(接頭辞 `auth:`。他用途(Jevカウンタ等M1+)と名前空間を分ける):

```text
失効リスト:   SET  auth:revoked:{jti}   "1"            PX <(exp−now)ms>   … logout・族失効で登録
リフレッシュ: HSET auth:rt:{sha256(token)} provider…, subject…, family…    PX 30d
族索引:       SADD auth:family:{family}  {sha256(token)}                   PX 30d
消費済み:     SET  auth:used:{sha256(token)} {family}                       PX 30d
```

各ユースケースの正当性(すべてアトミック性の要点つき):

- **token発行**: 族id=uuid4で生成。`auth:rt:{sha}` と `auth:family:{fid}`(初期メンバー)を書く。アクセスJWTの `sid` にfidを載せる(logoutが族を特定するため)
- **refresh回転**: `GETDEL auth:rt:{sha}`(アトミック消費)→ 値あり: 新トークンを発行し、`rt:{new_sha}` 追加+`family` へSADD+**旧shaの `auth:used:{sha}` にfidを書く**(残り30日のTTL)。→ 値なし: `EXISTS auth:used:{sha}` があれば**回転済みトークンの再提示=盗難疑い**としてfidの族を全失効(SMEMBERS→各rt鍵DEL→family鍵DEL)し401 INVALID_REFRESH_TOKEN。`used` もなければ不在・期限切れとして401(族失効はしない — 不在トークンから族を特定できないため、また期限切れは盗難と区別できないため安全側に倒す対象外)
- **logout**: 認証Dependency通過後、(1) `auth:revoked:{jti}` をTTL=残り有効期限で登録(expで自動消滅=失効リストが無限に伸びない)、(2) `sid` の族を全失効。204。冪等(既失効jtiの再提示はDependencyが401で拒否するため二重登録経路は存在しない)
- **認証Dependency(毎リクエスト)**: JWT検証(署名・iss・alg)→**expをClock.now()と手動比較**(§2.8)→ `EXISTS auth:revoked:{jti}`(確定値7の「失効リスト掲載」)→ AuthContext(provider, subject, jti, sid)

トレードオフ: 族失効のSMEMBERSは族サイズに比例するが、族サイズ=当該セッションの回転回数(30日内の再発行数)でしかなくMVP規模で問題にならない。シャーディング時の族横断は単一Redis(MVP)の範囲外。

### 2.4 IdP検証の構成 — 推奨: 検証設定のプロバイダレジストリ+JWKS取得2方式(URL/同梱)

`IdPVerifier` はprovider(google/apple)ごとの検証設定(issuer・audience・JWKSソース)を持ち、(1)JWKSからkid照合で署名検証(RS256ピン留め)、(2)iss/aud検証、(3)expをClock.now()と手動比較、(4)sub(=auth_subject)の非空検証、を行う。失敗は `jwt.InvalidTokenError` 系→401 INVALID_IDP_TOKEN、**JWKS取得の接続失敗→503 DEPENDENCY_UNAVAILABLE**(確定値2の「JWKS取得失敗等」)、kid不在(再取得後も不一致)→401。

JWKSソースは2方式を設定で切り替える:

- **URL方式(prod)**: `PyJWKClient` がJWKS URLを取得・キャッシュ。実IdPの運用と同一経路
- **同梱方式(ci/staging既定)**: パッケージ内のJWKS JSON(latch/auth/testkeys/)から静的に鍵を構築。**取得失敗という状態が存在しない**

トレードオフ: 同梱方式は「JWKS参照」という文字通りのHTTP参照ではなくなるが、検証コードの本体(署名・kid・iss/aud・exp)は完全一致であり、G0の「JWKS検証経路がci環境でグリーン」はこの経路で立証する。URL方式の結合試験は `file://` URL(urlopenが対応)でunit試験する(§4.1-6)。

### 2.5 鍵管理(app_env別)とテスト用認証構成 — 推奨: 同梱テスト鍵ペア(ci)+専用ペア注入(staging)+prodガード

10第1節(確定値8)の「staging専用の鍵ペアを注入」「テストユーザーのJWTを発行する内部ツール」を次の構成で実装する:

1. **鍵ペアの実体(RS256)**: `backend/src/latch/auth/testkeys/` に**ci/試験用の鍵ペアをコミット**(秘密鍵PEM・JWKS JSON・固定kid `test-idp-1`)。テスト専用で公開を前提とした値であり、**prodで誤用されないよう起動ガードで拒否する**(下記)。**staging実環境はコミット鍵と別に専用ペアを生成・注入**する(`gen-keypair`で生成→設定で参照)
2. **設定(Settings追記・すべて消費あり)**:
   - `redis_url`(既定 `redis://127.0.0.1:6379/0`)
   - `auth_access_secret`(HS256用。空=同梱テスト鍵。**prodで空/テスト鍵なら起動拒否**)
   - `auth_idp_jwks_url_google` / `auth_idp_jwks_url_apple`(空=同梱テストJWKS。prodは実URL必須)
   - `auth_idp_issuer_google` / `auth_idp_issuer_apple` / `auth_idp_audience_google` / `auth_idp_audience_apple`(既定=テストIdP値 `https://idp.ci.latch.test/{provider}`・`latch-test-app`。prodは実IdPのissuer/audience必須)
3. **prodガード**: `build_auth_service(settings)` が `app_env=="prod"` のときテスト既定値(空secret・同梱JWKS・テストissuer/audience)の残存をValueErrorで拒否(ws-2の `llm_mode` 拒否と同一パターン)。ci/stagingは既定値でそのまま動く
4. **JWT発行ツール(内部ツール)**: `python -m latch.auth` のCLIサブコマンド
   - `issue-idp-token --provider {google,apple} --subject <sub> [--expires-in 3600] [--private-key <pem>] [--issuer …] [--audience …]` — **staging鍵ペア(既定=同梱、stagingでは `--private-key` で注入ペアを指定)で署名したIdPトークン(テストユーザーのJWT)を発行**しstdoutへ。issuer/audienceの既定はSettingsと同一(=API側検証設定と自動一致)
   - `gen-keypair --out-dir <dir>` — staging専用ペアの生成(秘密鍵PEM+JWKS JSON書き出し。鍵ローテーションにも使用)
   - 発行時刻・期限は**Clockインターフェース経由(SystemClock)**で取得し、`--iat <unix秒>` で上書き可能とする(tools.pyも `backend/src` 内のためarch test準拠。`datetime.now` 等の直接参照は書かない)

ツールが発行するのは**IdPトークン**(JWKS検証経路の入り口)とし、アクセストークンの直接鍵造は行う**しない** — アクセストークンは POST /v1/auth/token 経由で取得させる経路を常により本物の経路として残す(§4.2の試験もこの経路)。

トレードオフ: 秘密鍵のリポジトリコミットは通常アンチパターンだが、当該鍵はci/試験専用で価値を持たず、prod誤用は起動ガードで封じる。代替(環境構築時に都度生成)は常設ci環境の再現性を損ねる(鍵が消えるたびに工具・API・試験の整合が壊れる)。

### 2.6 users参照とプロセス統合 — 推奨: 生SQL読み取り1本+lifespanでengine/redis/app.state構築

§1.3のとおり、tokenエンドポイントのみusers表を読む(読み取り専用SELECT 1本・`sqlalchemy.text()`)。**マイグレーションは追加しない**(認証の状態はすべてRedis・JWTにあり、05第2節に対応テーブルが存在しないため。共有ci-dbのalembic_versionを取り合うSTATUS運用リスクにも抵触しない)。

`create_app` はlifespanを得る: 起動時に `redis.asyncio.Redis.from_url(settings.redis_url)` と `create_db_engine(settings)`(ws-1の工場をそのまま使用)を生成し、`build_auth_service` で構築したAuthServiceを `app.state.auth_service` へ、engineを `app.state.db_engine` へ置く。終了時に両方を閉じる。**テスト注入用に `create_app(clock, settings, auth_service=None)` の第3引数を設け、指定があればlifespanでの構築をスキップ**してapp.stateへ直接載せる(§4.1のunit試験がfakeredis+スタブでapp全体を組める)。httpxのASGITransportはlifespanを実行しないため、既存の `/health` unit試験への影響はない。

### 2.7 API構成とC3の強制 — 推奨: public/protectedの2ルータ+`require_authenticated`依存+共通エラーハンドラ

- `latch/auth/routes.py` に (a)**public_router**(token・refresh。認証不要の2エンドポイント=確定値1)と (b)**logoutルータ**(`dependencies=[Depends(require_authenticated)]`)を定義しmain.pyで両方include
- **`require_authenticated`**(`latch/auth/deps.py`): §2.3の検証フローを通りAuthContextを返す。**M1以降のドメインルータはこの依存をルータ単位に付すことがC3の強制方法**(v1契約上の認証不要2エンドポイントのみが依存を持たない)。FastAPIのグローバル依頼で「デフォルト認証+例外2つ」を実装する方法はinclude順で例外を表現できず事故りやすいため採らない
- **共通エラー形式(確定値7)**: `AuthError` 系例外(InvalidIdpToken/Unauthenticated/InvalidRefreshToken/DependencyUnavailable)→ハンドラで `{"error":{code,message,details}}` を返す。**RequestValidationErrorハンドラ**: JSON形式不正は400 MALFORMED_REQUEST、必須欠落・値域外(provider不正値等)は422 VALIDATION_ERRORへ振り分け(05第5節の使い分けをFastAPIの例外タイプから判定)。Redis/DB障害は依存層で `DependencyUnavailableError` へ包み503
- ログ: 認証系のログは**コード/結果のみ**(トークン文字列・claim内容・subjectは出さない。08の許可リスト思想に沿う)

### 2.8 時刻(C2/arch規律) — 推奨: 期限判定はライブラリに委ねずClock手動比較

PyJWTの `decode` はexpをライブラリ内の実時間で検証するため、そのまま使うと**Clock差し替えで期限切れを再現できず**(10第1節の時刻操作)、時刻参照の単一経路というarch規律の実質も満たさない。よって**署名・iss・aud・algのみPyJWTに検証させ(`options={"verify_exp": False}`)、exp・iatの判定は `clock.now()` との手動比較**で行う(自前コードはClock経由のみでありarch testを通過。FakeClockのadvanceで期限切れ401が決定的に試験できる)。アクセスJWT発行の `iat`/`exp`、失効リストTTL算出、IdPトークンexp判定もすべてClock由来。

### 2.9 採用しないもの(YAGNIによる切り捨て一覧)

- python-jose・Keycloak等のIdP導入(§2.1)
- アクセストークンRS256化・自己JWKS公開エンドポイント(検証者がAPIのみ。§2.2)
- リフレッシュトークンのJWT化・DBテーブル化(§2.2)
- SessionStore抽象IF( fakeredisで足りる。§2.1)
- レート制限ミドルウェア(M1。確定値13)
- usersモデル・リポジトリ層(M1。§1.3)
- WorkerへのRedis/DB注入(M2)
- トークンintrospectionエンドポイント・`/.well-known/jwks`(v1契約にない)
- アクセストークン直接発行サブコマンド(§2.5)
- 失効リストの掃除ジョブ(TTLで自動消滅。§2.3)

## 3. ファイル構成

### 3.1 作るもの

```text
backend/src/latch/auth/
├── __init__.py        # 公開IFの再export(AuthService, build_auth_service, 例外, deps)
├── __main__.py        # CLI入口(python -m latch.auth → tools.main())
├── errors.py          # AuthError(基底)+ InvalidIdpToken/Unauthenticated/InvalidRefreshToken/
│                      #   DependencyUnavailable(http_status, code, message)
├── idp.py             # IdP検証: 検証設定(google/apple)・JWKSソース(PyJWKClient/同梱静的)・
│                      #   RS256署名+iss/aud検証+exp手動Clock判定 → AuthSubject(provider, subject)
├── tokens.py          # アクセスJWT発行/検証(HS256・claim固定・iss/aud/algピン・exp手動Clock判定)
│                      #   ACCESS_TTL_S=3600・REFRESH_TTL_S=30日(04 D-21)
├── sessions.py        # Redis操作(§2.3スキーマ): 失効リスト登録/照会・refresh発行/回転(GETDEL)・
│                      #   消費済みマーカー・族全失効・logout一連
├── service.py         # AuthService(token/refresh/logout ユースケース・users読み取りSQL(text()1本))+
│                      #   build_auth_service(clock, settings)(prodガード)
├── routes.py          # public_router(token/refresh)+ logout(router依存でrequire_authenticated)
├── deps.py            # get_auth_service(app.state)・require_authenticated → AuthContext
├── tools.py           # issue-idp-token / gen-keypair(argparse)
└── testkeys/
    ├── __init__.py    # ローダ(load_private_key / load_jwks / TEST_ACCESS_SECRET / DEFAULT_KID)
    ├── idp_test_private.pem    # ci/試験用RS256秘密鍵(コミット。prod使用は起動ガードで拒否)
    ├── idp_test_jwks.json      # 公開鍵JWKS(kid=test-idp-1)
    └── README.md               # 「テスト専用・再生成はgen-keypair」の明記
backend/tests/unit/auth/
├── test_tokens.py         # 発行/検証・期限(FakeClock)・改ざん・algピン・claim構成
├── test_sessions.py       # fakeredis: 回転・再利用検知→族失効・不在401・logout失効TTL
├── test_service_token.py  # tokenユースケース(スタブIdP・インメモリlookup)・user.id応答分岐
├── test_service_refresh.py# 回転応答・族失効連鎖
├── test_routes.py         # 3エンドポイントのAPI挙動・エラーenvelope・400/422振り分け・logout 204
├── test_deps.py           # require_authenticated(401系バリエーション・ダミー保護ルートで検証)
├── test_idp.py            # 同梱JWKS検証ラウンドトリップ・iss/aud/exp/鍵違い・file:// URL方式
├── test_tools.py          # issue-idp-token↔検証・gen-keypair(tmp_path)
├── test_prod_guard.py     # build_auth_serviceのprod拒否・ci/staging既定受理
└── test_auth_settings.py  # 新設定項目の既定値・env上書き(既存tests/unit/test_settings.pyとは別ファイル。
                           #   並走ws-4との共通ファイル衝突を避ける。ws-2設計§3.3と同一判断)
backend/tests/integration/
├── conftest.py            # (既存へ追記)redis client fixture・compose apiへのHTTP client fixture
└── test_auth_api.py       # §4.2の実HTTP・実Redis・実DB試験
```

インターフェースの要旨(実装詳細は計画書・TDDで確定):

```python
# tokens.py
ACCESS_TTL_S = 3600            # 04 D-21(有効期限1時間)
REFRESH_TTL_S = 30 * 24 * 3600 # 04 D-21(30日)

async def issue_access_token(*, clock: Clock, secret: str, provider: str,
                             subject: str, sid: str) -> str: ...
async def verify_access_token(*, clock: Clock, secret: str, token: str)
    -> AccessTokenClaims:  # auth_provider, auth_subject, jti, sid, exp …(失効リスト照会はしない)

# idp.py
class IdPVerifier:
    async def verify(self, *, provider: str, idp_token: str,
                     clock: Clock) -> tuple[str, str]:  # (provider, subject)
    # 401/503相当は InvalidIdpTokenError / DependencyUnavailableError で送出

# sessions.py
class SessionStore:  # redis.asyncio.Redis を注入して構築
    async def create_refresh(self, *, provider, subject) -> RefreshIssued:  # (token, sid)
    async def rotate_refresh(self, *, token: str) -> RotationResult  # 失敗はInvalidRefreshTokenError
    async def revoke_access(self, *, jti: str, exp: datetime, now: datetime) -> None
    async def revoke_family(self, *, sid: str) -> None
    async def is_revoked(self, *, jti: str) -> bool

# service.py
class AuthService:
    def __init__(self, *, clock, settings, sessions: SessionStore,
                 idp: IdPVerifier, user_lookup: Callable[[str, str], Awaitable[uuid | None]]): ...
    async def token(self, *, provider: str, idp_token: str) -> TokenResult: ...
    async def refresh(self, *, refresh_token: str) -> RefreshResult: ...
    async def logout(self, *, claims: AccessTokenClaims) -> None: ...
```

### 3.2 触るもの(既存ファイルへの変更)

- `backend/src/latch/main.py` — lifespan追加(redis・engine・auth_service構築/解体)+ auth 2ルータのinclude + エラーハンドラ。`/health` とcreate_app既存引数は変更しない(第3引数 `auth_service` 追加のみ)
- `backend/src/latch/settings.py` — §2.5-2の設定群を追記(既存項目は変更しない)
- `backend/pyproject.toml` / `uv.lock` — §2.1の依存追加(実行2・dev1)
- `backend/README.md` — 認証ツール使用方法(発行→token交換のcurl例)・鍵管理の追記
- `compose.yaml` — **apiサービスへ環境変数追加**: `LATCH_DATABASE_URL=postgresql+asyncpg://latch:latch@db:5432/latch`・`LATCH_REDIS_URL=redis://redis:6379/0`(コンテナ内は127.0.0.1既定が不通のため必須。workerは依然何も消費しないため触らない)
- `backend/tests/conftest.py` — 変更しない(既存app/client fixtureは /health のみでlifespan非実行のため影響なし。auth用fixtureは tests/unit/auth/ 内に置く)
- `backend/tests/integration/conftest.py` — redis・HTTP client fixtureを追記(既存migrated_db/db_engineは温存)

### 3.3 触らないもの

- `backend/src/latch/core/`(clock.py・db.py・deps.py すべてws-3で再利用・不改変)・`worker/`・`llm/`
- `backend/alembic/`(マイグレーション追加なし。§2.6)
- `docker/postgres/Dockerfile`・`Makefile`(target追加なし。test/test-ci/lintはそのまま)・`.mise.toml`
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/M0/`の他ファイル
- `prototype/` 全体・ルート `README.md`・`.claude/`

## 4. テスト方針

### 4.1 unit(`make test`。外部プロセス不要・FakeClock+fakeredis+スタブで決定的)

1. **アクセスJWT**: 発行→検証ラウンドトリップ(claimにauth_provider/auth_subject/jti/sid/iss/aud/iat/exp)。FakeClock.advance(>1h)で期限切れ401(§2.8手動判定の証明)。署名改ざん・誤secret・alg不一致(`alg:none`・RS256混入)は拒否
2. **セッションStore(fakeredis)**: token→refresh回転で旧トークン無効化(GETDEL)+新トークン格納+族索引更新。**旧トークン再提示→族全失効**(族メンバーのrt鍵・family鍵が消え、回転後の新トークンも401)。未知トークン→401(族失効しない)。logout→revoked鍵のTTLが残り有効期限以下・正・族索引削除
3. **tokenユースケース**: スタブIdPVerifier(subject=固定値)+インメモリuser_lookupで、(a)lookup結果あり→user.id/profile_complete=true、(b)なし→null/false(§1.3-3の導出)。IdP検証失敗→INVALID_IDP_TOKEN、lookupでのDB例外→503
4. **refresh/logoutユースケース**: 応答形式(新access+新refresh・token_type/expires_in=3600)・logoutが失効リスト+族失効を呼ぶ
5. **ルーティング**: 実app(create_appにauth_service注入)へhttpxで、token/refresh/logoutの成功・各401・503・**エラーenvelopeの形状**(`error.code` 等)・不正provider値→422 VALIDATION_ERROR・body破損→400 MALFORMED_REQUEST・logout成功204
6. **require_authenticated**: ダミー保護ルートを試験内appで定義し、Authorization欠落・非Bearer・改ざん・期限切れ・失効リスト掲載→401 UNAUTHENTICATED、正常→AuthContext。IdP検証のURL方式は `file://` JWKSで1試験(取得失敗は到達不能URLで503)
7. **ツール**: issue-idp-tokenの出力がIdPVerifier(同梱)で検証できる(issuer/audience既定一致)。gen-keypair(tmp_path)→生成JWKSで検証できるラウンドトリップ
8. **prodガード・設定**: prod+テスト既定残存でValueError・ci/staging既定で構築成功。新Settings項目のenv上書き(`LATCH_REDIS_URL` 等)
9. **arch規律**: 既存 `test_arch_no_direct_time.py` がauth/配下を自動スキャン(§2.8によりヒットなし)

### 4.2 integration(`make test-ci`。compose常設の実Redis・実DB・実apiプロセス)

**実HTTP(127.0.0.1:8000のcompose api)** — G0「認証3エンドポイントとJWKS検証経路がci環境でグリーン」の主要証拠:

| # | 検証 | 根拠 |
|---|---|---|
| 1 | ツール発行IdPトークン(google/apple各1)でPOST /v1/auth/token→200・token_type=Bearer・expires_in=3600・refresh_tokenあり・`user:{id:null, profile_complete:false}` | 05 §5(確定値2) |
| 2 | db_engine fixtureでUser行を直接INSERT(ユニークなsubject・固定リテラル時刻・試験後DELETE)→token→`user.id=<uuid>`・`profile_complete:true` | 05 §5・§1.3 |
| 3 | 鍵違いIdPトークン(gen-keypairで生成した別鍵)→401 INVALID_IDP_TOKEN(envelope) | 05 §5 |
| 4 | refresh回転: 旧refresh再利用→401 INVALID_REFRESH_TOKEN、**回転後の新refreshも同一族なので401**(族失効) | 05 §5(確定値3) |
| 5 | logout: 204→同JWT再利用→401 UNAUTHENTICATED→当族refresh→401 | 05 §5(確定値4) |
| 6 | 不正なrefresh_token文字列→401 | 05 §5 |
| 7 | 実Redis直接参照: revoked鍵の存在とTTL上限(≤3600s)・族鍵の削除 | §2.3 |

**ASGI+実Redis(FakeClock注入・127.0.0.1:6379)**: FakeClock.advance(>1h)後のアクセスJWT利用→401(期限切れのClock再現。compose api実プロセスではClock固定のためこちらでカバー)。

試験データ: subjectは実行ごとにユニークな値(uuid接尾辞)を用い、User行は試験内でDELETE(共有ci-dbの汚染回避)。Redis鍵はTTL付き(最大30日)で自動消滅するため追加掃除はしない。

### 4.3 既存資産への回帰

`make lint`・`make test`(既存unit 79+llm 47など全件)・`make test-ci`(109件)がグリーン。`/health`・Clock・LLM Gateway・スキーマ試験への影響なし(main.pyのlifespan追加はASGITransport試験で非実行のため)。

## 5. 完了条件(この単位の受渡し判定。計画書が参照する)

1. `make lint`・`make test` がグリーン(unit/auth 追加分を含む)
2. `make test-ci` で §4.2-1〜7 がグリーン(**G0文言「認証3エンドポイントとJWKS検証経路がci環境でグリーン」の証拠**。ただしws-4並走中のtest-ci実行はSTATUS運用ルールに従いスーパーバイザー検証待ちとしてよい)
3. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが引き続き `core/clock.py` のみ(§2.8)
4. 依存追加が `pyjwt[crypto]`・`redis`(devに `fakeredis`)のみ(`git diff` でpyproject/uv.lockの差分が想定どおり)
5. alembic/versions に差分なし(マイグレーション追加なし)
6. 触るファイルが §3.2・§3.1 の一覧どおり(core/・worker/・llm/・Makefile・docs/prototypeに差分なし)
7. `uv run python -m latch.auth issue-idp-token --provider google --subject demo` の出力がcompose apiの POST /v1/auth/token で200交換できる(README記載の手順)

## 6. 未解決の論点(設計は推奨で固定済み。supervisor確認事項)

1. **アクセストークンの署名方式をHS256(対称鍵)とする判断**(§2.2): docsはアルゴリズムを確定しておらず(04 D-21は「独自JWT・1時間」のみ)、検証者がAPI Layerのみである根拠でHS256を選んだ。kid付き鍵ペア(RS256)への変更は、外部検証者または鍵ローテーション運用が確定した時点でtokens.py差し替え+設定追加で対応可能
2. **リフレッシュトークンの保持先をRedisとする判断**(§2.2): 05第2節にリフレッシュテーブルが存在せず、04第3節のRedis用途「セッション失効リスト」が唯一の置き場と解釈した。Redis全喪失時は全セッション再ログインとなる(失効リストも消える=安全側には倒れない)。永続化要件が出た場合はDBテーブル追加として再計画
3. **`profile_complete` の導出**(§1.3-3): users表に列がないため「User行の存在」から導出する解釈を取った。05 v0.5系での列追加・明記の要否は軽微なため本解釈で前進する
4. **refresh/logoutでの503の扱い**(§2.7): 05 §5はrefreshのerrorsに401/429のみ列挙するが、同表の503 DEPENDENCY_UNAVAILABLEは「全API」かつ「DB・Redis等の依存障害」であり、Redisを必須とするrefresh/logoutでも503を返す本体側の解釈とした(errors列挙は主要例示と解釈)
5. **実IdP(JWT URL・client_id等)のprod確定値**: 本単位は設定経路とprodガードのみ用意する(§1.4)。実URL・audienceの確定はデプロイ設計(M4/11)で行う
