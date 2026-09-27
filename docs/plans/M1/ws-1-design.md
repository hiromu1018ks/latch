# M1 ws-1(users API)設計メモ

- 作業単位: ws-1 — users API: POST /v1/users(初回登録・birth_date必須・18歳未満422 UNDER_AGE)・GET /v1/users/me(docs/plans/STATUS.md M1表 / 出典 12 M1-1・05 §5 / 依存: M0 usersスキーマ・認証=マージ済み)
- 作成: 2026-09-27(agent1 / superpowers:brainstorming使用)
- 次工程: 計画書(ws-1-plan.md)へ変換 → 実装エージェントがworktree内でTDD実装
- 並走: ws-2(Intent Parser)=llm/配下+parseルータ。**本単位はマイグレーションを追加しない**(§1.3)ため、STATUS運用ルールのtest-ci同時実行制約(マイグレーション追加単位×DB消費単位)には抵触しない。交点はmain.pyのみ(§3.2・マージ時両側追記保持)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M1スコープ1(12 M1-1)「users: POST /v1/users(初回登録・birth_date必須・18歳未満422 UNDER_AGE)、GET /v1/users/me(05 §5)」を実装する。M0の認証(ws-3)はUser行の**読み取りのみ**を行い、User作成経路をM1へ先送りした(ws-3設計§1.3「User行の作成経路はM1まで存在しない」)。本単位がその作成経路を作り、初回登録フロー(token→`profile_complete:false`→POST /v1/users→me)を通す。02#1〜#4(G1)の下流(intents・フロントエンド)はUser行の存在を前提とするため、ws-1はM1の最初の土台になる。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | POST /v1/users: request `{"display_name": "...", "birth_date": "1990-04-01", "profile": {"bio": "..."}}` → response **201** `{"user": {"id": "<uuid>", "display_name": "..."}}`。errors: 422 VALIDATION_ERROR(display_name / birth_dateの必須欠落・形式不正)、422 UNDER_AGE(18歳未満)、409 USER_EXISTS | 05 §5 認証・ユーザー系 POST /v1/users |
| 2 | Authorizationヘッダー必須(認証不要2エンドポイントに含まれない)。サーバは認証済みJWTの `auth_provider`・`auth_subject` claimを取り出し、この2値に紐付くUser行を本APIで生成する(**User.idは新規採番**)。`POST /v1/auth/token` で `profile_complete: false` を受けたクライアントが直後に呼ぶ経路 | 05 §5 POST /v1/users解説 |
| 3 | birth_dateは必須。**呼び出し時点のClock.now()から18歳未満と判定された場合は登録を拒否**(422 UNDER_AGE)。表示名・プロフィールの内容審査はMVPに含めない | 05 §5 / 02 D-12 |
| 4 | 18歳以上の登録・生年月日は**自己申告**(users.birth_dateへ保持)・**IdPからは取得しない**(IdPが生年月日の提供を標準化していないため) | 08 第4節 D-10 |
| 5 | GET /v1/users/me: response 200 `{"id", "display_name", "profile": {"bio": "..."}, "birth_date": "1990-04-01", "profile_complete": true}`。**本人のみ**。birth_dateは本人自身にのみ返す(相手・提案画面には一切出さない) | 05 §5 GET /v1/users/me |
| 6 | users表: id uuid PK / display_name text NOT NULL / profile jsonb NOT NULL DEFAULT '{}' / **birth_date date NOT NULL** / auth_provider text NOT NULL CHECK IN ('google','apple') / auth_subject text NOT NULL / **UNIQUE(auth_provider, auth_subject)** / location_preferences・visibility_preferences・trust_score NULL可 / created_at・updated_at timestamptz NOT NULL。**マイグレーション0001にこの定義が実装済み**(birth_date込み) | 05 §2 users / alembic 0001 |
| 7 | エラー形式(共通): `{"error": {"code", "message", "details"}}`。409 USER_EXISTS(登録済みauth_subjectでの初回登録・POST /v1/users)、422 VALIDATION_ERROR(必須欠落・値域外等・**intents系・users系**)、422 UNDER_AGE、**404 NOT_FOUND(リソース不在・全API)**、503 DEPENDENCY_UNAVAILABLE(DB・Redis等の依存障害・**全API**)、401 UNAUTHENTICATED(JWT無効・期限切れ・失効リスト掲載・全API) | 05 §5 エラー形式表 |
| 8 | 認証は `Authorization: Bearer <JWT>`。認証を要求しないエンドポイントはauth/tokenとauth/refreshの2つのみ。APIが発行するJWTのclaimには `auth_provider` と `auth_subject` を含め、**認証済み操作はこの2claimでUser行と紐付ける**。M1以降のドメインルータは `require_authenticated` 依存をルータ単位に付すことがC3の強制方法 | 05 §5 冒頭 / auth/deps.py |
| 9 | `profile_complete` 列はusers表に存在しない。導出は「**User行が存在する**」=true(POST /v1/usersはdisplay_name・birth_date必須で完全な行を一括作成するため部分登録状態は存在しない) | ws-3設計 §1.3-3(05 §2に列なしによる確定) |
| 10 | 時刻参照はすべてClock経由(arch test `test_arch_no_direct_time` が backend/src 全体を強制)。Clockはtz-aware UTCを返し、**JST暦日付は `clock.jst_date()`** が正規経路(レート制限のJST日付キーと同じ基準) | 04 §5 FR-41 / 10 §1 / core/clock.py |
| 11 | 時刻列にDB時刻関数のDEFAULTを付けない(created_at/updated_atは**Clock由来の明示値**を挿入)。uuid PKにはDEFAULT gen_random_uuid()が付く(INSERT側の明示値を妨げない) | ws-1(M0)設計 §2.8 / alembic 0001 |
| 12 | 実HTTPのintegration試験はcompose常設api(127.0.0.1:8000)へ。IdPトークン発行はws-3の内部ツール(`python -m latch.auth issue-idp-token`)+同梱テスト鍵で本物のJWKS検証経路を通す | 10 §1 / auth/tools.py / tests/integration/conftest.py |
| 13 | `make test-ci` は `docker compose up -d --wait` のみで**イメージを再ビルドしない**。実HTTP試験はコード変更後にapiイメージの再ビルドが前提 | Makefile / compose.yaml |
| 14 | 429 RATE_LIMITED(レート制限)の実装はM1 ws-4。本単位はerror codeを列挙しない(me応答・登録応答の契約に含まれない) | 12 M1-6 |

### 1.3 マイグレーション追加の要否 — 判断: **追加しない**

users表はalembic 0001に確定値6の定義どおり実装済みであり、本単位が要求する列・制約(birth_date NOT NULL・UNIQUE制約・profile DEFAULT '{}')に過不足がない。よって**マイグレーションは追加しない**。これはSTATUS運用ルール(マイグレーション追加単位とDB消費単位のtest-ci同時実行禁止)のリスクを排除し、並走ws-2(llm/・DB不消費・マイグレーション追加なし)との実行制約を発生させない。

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

- **レート制限(429 RATE_LIMITED)** — M1 ws-4(12 M1-6・Redis JST日付キー)
- **intents系API・Parser** — ws-2(ws-3 CRUD)。users APIの完了を依存とする(STATUS M1表)
- **users行の更新・削除・退会API** — 05 §5のAPI一覧に存在しない(ブロック・通報はM3以降)。birth_date変更・年齢再検証の経路もdocsに契約なし
- **表示名・プロフィールの内容審査** — 02 D-12によりMVP外(確定値3)
- **ORMモデル・リポジトリ層の本格導入** — §2.1でusers単位では不要と判断。intents(ws-3)で再判断
- **フロントエンドの登録画面接続** — M1 ws-5

## 2. 実装方式の選択肢と推奨

### 2.1 永続層の方式 — 推奨: 生SQL(`sqlalchemy.text()`)継続・関数注入

選択肢:

- **A(推奨). `text()` 生SQLのモジュール関数(INSERT 1本+SELECT 1本)をUserServiceへ Callable で注入** — auth/service.py の `user_lookup` 注入パターンと同型
- B. SQLAlchemy ORMモデル(`class User(Base)`)を導入し、M1以続のドメインの土台にする
- C. SQLCore(Tableメタデータのみ)を定義し、クエリはtext()/Core構文で書く

推奨の根拠: users APIの永続化は**INSERT 1本・SELECT 1本**(いずれも単表・WHEREはUNIQUE制約の2列)であり、ORMの利益(リレーション横断・unit-of-work・マッピングによる型保証)が働く規模ではない。M0-ws-1設計「マイグレーションが真実源」方針を維持でき、モデルとDDLの二重管理を生まない。M1 ws-3(intents CRUD)はGeo(vector・`ST_DWithin`・`<=>`)とJSONB中心でSQL方言が強く、ORM化の判断はその単位の設計で行うべきであり、usersだけ先行してORM化するとws-3が生SQLを選んだ場合に二重構造が残る。

「**モデル導入はM1**」(ws-3 M0設計§1.3-2)の予告との整合は「M0では作らない」の限定的な意味と解釈し、M1 ws-1では不要と判断する(この解釈は§6-2に確認事項として記載)。

トレードオフ: 列追加・型変更の際にSQL文字列を手で直す必要があり、実行時まで誤りが検出されない。ただしusers表の列は05 §2で確定しており、変更はdocs改版を伴う。将来の列追加もマイグレーションと同時にSQLを直す運用はtext()パターン(make_user_lookup・test_auth_api.py)で既に確立している。

### 2.2 409 USER_EXISTSの検出 — 推奨: 事前SELECTなし・INSERT衝突をDB制約で検出

選択肢:

- **A(推奨). いきなりINSERTし、UNIQUE制約 `uq_users_auth_provider_subject` 違反(IntegrityError)を409 USER_EXISTSへ変換**
- B. 事前SELECT(auth/service.py のlookupと同じ問い合わせ)で存在確認してからINSERT

推奨の根拠: BにはTOCTOU競合が残る(同一subjectの並行登録で事前チェックをすり抜けたINSERTが制約違反になり、409ではなく503として応答されうる)。DBのUNIQUE制約こそが「登録済みauth_subject」の真実源(確定値6)であり、INSERT衝突を捕捉する方が1本のSELECTを省いて競合安全になる。IntegrityErrorは**制約名を検査**し、`uq_users_auth_provider_subject` 由来なら409、それ以外の整合性違反は503 DEPENDENCY_UNAVAILABLEへ送る(制約名の取り出し方の実装詳細は計画書・TDDで確定)。

検証の順序(どのエラーが優先するか)は次のとおり固定する:

1. Pydantic検証(display_name/birth_dateの必須・形式)→ 422 VALIDATION_ERROR(既存のRequestValidationErrorハンドラがenvelope化。新コード不要)
2. 年齢検証(Clock・§2.3)→ 422 UNDER_AGE
3. INSERT → 409 USER_EXISTS / 503

つまり「17歳かつ登録済みsubject」ならUNDER_AGEを返す。登録の受け入れ可否が先に確定してから一意性を見る自然な順であり、将来の試験もこの順で固定する。

### 2.3 18歳判定の計算規則 — 推奨: `clock.jst_date()` 基準・(月, 日)タプル比較

docsは「呼び出し時点のClock.now()から18歳未満と判定」(確定値3)のみを規定し、暦日・タイムゾーンの細則は確定していない。本設計で次のとおり固定する:

- **基準日付は `clock.jst_date()`**(Clock契約のJST暦日付の正規経路・確定値10)。サービス対象が日本(初期リリース地域、02 D-22)であり、誕生日の到達判定は暦日で行うものとしてJSTを用いる。UTCのまま比較すると日本時間の深夜0時前後で最大9時間の判定ずれが生じる
- 年齢は `age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))` で計算する(純粋関数 `age_years(birth_date, today)` として切り出し、FakeClockで決定的に試験)。**age < 18 なら422 UNDER_AGE**(18歳の誕生日当日は登録可)
- **2月29日生まれ**: タプル比較により、その年の3月1日に年齢が加算される(2月29日を暦日に持たない年の解釈)。年齢計算に関する法律の前日主義(2月28日)と1日ずれるが、生年月日は自己申告(確定値4)であり、1日の差は許容して実装を単純な比較に保つ
- **未来のbirth_date**(誤入力): 年齢が負=18歳未満としてUNDER_AGEで拒否されるため、特別な検証を追加しない

### 2.4 GET /v1/users/me・User行不在時の応答 — 推奨: 404 NOT_FOUND

初回登録待ち(profile_complete: false)のJWTでGET /v1/users/meを呼んだ場合の応答はdocsに明示がない。エラー表の**404 NOT_FOUND(リソース不在・全API)**(確定値7)を適用し、User行がなければ404を返す。200を返すのは行が存在するときだけなので、me応答の `profile_complete` は**常にtrue**となる(確定値9の導出と整合。falseが現れるのはtoken応答の側)。

### 2.5 プロセス統合 — 推奨: UserService + app.state + lifespanの独立構築化

auth(ws-3)と対称な構成を採る:

- **`UserService`**: ユースケース `register`(claims・display_name・birth_date・profile → 201応答データ)と `get_me`(claims → me応答データ)。コンストラクタは `clock` と**永続化関数をCallableで注入**(`create_user: (engine, NewUser) -> uuid`・`fetch_by_auth: (engine, provider, subject) -> UserRow | None`)。unit試験で関数スタブに差し替えられる(authの `user_lookup` 注入と同型)
- **`make_user_service(engine)`**: 実SQL関数(text())を束ねた構築関数。lifespanで使用
- **app.state.users_service** へ載せ、`create_app` にテスト注入用の引数を追加する。lifespanの構築スキップ判定を「auth_service注入済みなら全部スキップ」から**サービスごとの独立判定**(users_service注入済みならusers側は構築しない)へ変更する。users単体のunit試験でauthスタックを構築せずに済む
- ルータは `APIRouter(prefix="/v1/users", dependencies=[Depends(require_authenticated)])` — C3の強制方法(確定値8)どおり、ルータ単位で認証を付ける。claimの取り出しは各エンドポイントで `AccessTokenClaims` を受け取る

INSERTの内容: display_name・profile(§2.7)・birth_date・auth_provider・auth_subject・**created_at/updated_at=Clock.now()の明示値**(確定値11)。**idはDEFAULT gen_random_uuid()に任せ `RETURNING id` で受け取る**(確定値2の「新規採番」。Python側でuuid4を振る二重管理をしない)。

main.pyへの差分はこの方式で最小になる(include 1行+lifespan数行+ハンドラ1関数)。**ws-2もmain.pyに触る見込み**のため、マージ時は両側追記保持(M0のsettings.py競合と同一の解消方法)を想定する。

### 2.6 エラー階層とハンドラ — 推奨: users/errors.py に独立階層+ハンドラ1個追加

`UsersError` 基底(http_status・code属性)と、`UnderAgeError`(422 UNDER_AGE)・`UserExistsError`(409 USER_EXISTS)・`UserNotFoundError`(404 NOT_FOUND)・`DependencyUnavailableError`(503)を `latch/users/errors.py` に置く。auth/errors.py の `AuthError` と共通基底を作ればハンドラを1つに集約できるが、そのためだけにauth/errors.py(動作済み)へ変更を入れるのは並走リスクに対して利益が小さい。**authは不変**とし、main.py に `users_error_handler` を1個追加する(AuthErrorハンドラと同型・数行)。503への包み込みはUserService内でrepoの例外を捕捉して行う(auth/service.py の `except Exception` パターンと同様)。

### 2.7 入出力の検証と形式 — 推奨: 最小限のPydantic検証(docsに規定のある範囲のみ)

- `display_name: str`(必須・**min_length=1**=空文字は「必須欠落」相当の422 VALIDATION_ERROR)。**最大長・文字種の制限を付けない**(docsに規定がなく、内容審査はMVP外・確定値3。付けると仕様追加になる)
- `birth_date: date`(YYYY-MM-DD以外は422 VALIDATION_ERROR・既存ハンドラがenvelope化)。過去日付の下限(高齢の上限値等)の検証も追加しない(docsに規定なし)
- `profile`: 任意(省略可)。入力モデルは `bio: str | None = None` のみを受け付け、**DBには `{"bio": ...}`(bioがNoneなら `{}`)を格納**する。応答はDBのprofile jsonbをそのまま返す(格納経路が本APIのみのため内容は実質保証)
- 応答形式は確定値1・5のとおり: POSTは201 `{"user": {"id", "display_name"}}`(入れ子)、GET meは200 `{id, display_name, profile, birth_date(ISO文字列), profile_complete: true}`
- meの応答にjti/sid等のセッション情報を含めない(05 §5の応答例にない)

### 2.8 採用しないもの(YAGNIによる切り捨て一覧)

- ORMモデル・リポジトリクラス(§2.1。関数注入で足りる)
- 事前SELECTによる409判定(§2.2)
- birth_dateの未来日・上限齢の特別検証(§2.3・§2.7)
- display_nameの長さ上限・文字種検証・profanity審査(§2.7・02 D-12)
- Python側でのid採番(uuid4)(§2.5・RETURNINGで足りる)
- auth/service.py の `make_user_lookup` の本モジュールへの統合・置き換え(§3.3。auth不変。SELECTのWHERE句が重複するが、users側は全列取得で用途が異なり、統合の判断はws-3(intents)でusers参照が増えた時点で行う — §6-4)
- auth/errors.py・core/・Makefile・compose.yaml・settings.py・pyproject.tomlへの変更(依存追加なし・新設定なし)
- users行の更新・削除・退会API・birth_date変更経路(05 §5に契約なし)
- レート制限ミドルウェア(ws-4)

## 3. ファイル構成

### 3.1 作るもの

```text
backend/src/latch/users/
├── __init__.py        # 公開IFの再export(UserService, make_user_service, 例外)
├── errors.py          # UsersError(基底)+ UnderAgeError(422 UNDER_AGE)/
│                      #   UserExistsError(409 USER_EXISTS)/ UserNotFoundError(404)/
│                      #   DependencyUnavailableError(503)
├── service.py         # age_years(birth_date, today)->int(純粋関数)・
│                      #   UserService(register/get_me)・make_user_service(engine)
│                      #   (実SQL: INSERT...RETURNING id / SELECT全列WHERE provider+subject。
│                      #    IntegrityErrorの制約名検査→409もここ)
└── routes.py          # users_router(prefix="/v1/users", require_authenticated依存)・
│                      #   入出力モデル(UserCreateRequest/UserProfileInput/
│                      #   UserCreatedResponse/UserMeResponse)
backend/tests/unit/users/
├── test_age.py        # §4.1-1 年齢純粋関数(全パターン)
├── test_service.py    # §4.1-2 ユースケース(スタブ永続化関数)
└── test_routes.py     # §4.1-3 ルーティング(httpx・スタブUserService注入・
                       #   require_authenticatedはdependency_overrides)
backend/tests/integration/
└── test_users_api.py  # §4.2 実HTTP・実DB(api_client + db_engine fixture)
```

インターフェースの要旨(実装詳細は計画書・TDDで確定):

```python
# service.py
def age_years(birth_date: date, today: date) -> int: ...  # §2.3の式

@dataclass(frozen=True)
class NewUser:        # registerの中間表現(INSERT列・時刻はClock由来の明示値 §2.5)
    display_name: str
    birth_date: date
    profile: dict     # {"bio": ...} or {}
    auth_provider: str
    auth_subject: str
    created_at: datetime
    updated_at: datetime

@dataclass(frozen=True)
class UserCreated:    # 201応答本体 {"user": {...}}
    id: uuid.UUID
    display_name: str

@dataclass(frozen=True)
class UserMe:         # 200 me応答本体
    id: uuid.UUID
    display_name: str
    profile: dict
    birth_date: date
    profile_complete: bool  # 常にTrue(§2.4)

class UserService:
    def __init__(self, *, clock: Clock,
                 create_user: CreateUserFn,     # (NewUser) -> uuid.UUID
                 fetch_by_auth: FetchByAuthFn) -> None: ...
    # 永続化関数のengineはmake_user_serviceがクロージャで束縛する
    # (§2.5と同一シグネチャ。UserService自身はengineを知らない)
    async def register(self, *, claims: AccessTokenClaims, display_name: str,
                       birth_date: date, profile: dict) -> UserCreated: ...
        # 18歳未満→UnderAgeError / 一意性違反→UserExistsError / 依存障害→503
    async def get_me(self, *, claims: AccessTokenClaims) -> UserMe: ...
        # 行なし→UserNotFoundError

def make_user_service(*, clock: Clock, engine: AsyncEngine) -> UserService: ...
    # 実SQL関数を束ねる。INSERTは created_at/updated_at=clock.now()・RETURNING id(§2.5)

# routes.py
users_router = APIRouter(
    prefix="/v1/users",
    tags=["users"],
    dependencies=[Depends(require_authenticated)],  # C3(確定値8)
)

@users_router.post("", status_code=201)  # POST /v1/users
async def register_users(body: UserCreateRequest, ...) -> UserCreatedResponse: ...

@users_router.get("/me")                 # GET /v1/users/me
async def get_users_me(...) -> UserMeResponse: ...
```

### 3.2 触るもの(既存ファイルへの変更)

- `backend/src/latch/main.py` — (1) usersルータのinclude、(2) lifespanでusers_serviceを構築しapp.stateへ載せる(auth_serviceと独立したスキップ判定に変更)、(3) `UsersError` ハンドラを追加、(4) `create_app` にテスト注入引数を追加。**これらが本単位の既存コードへの全変更**であり、ws-2との交点もここだけ(マージ時は両側追記保持)
- `backend/tests/integration/conftest.py` — 追加不要(api_client・db_engineで足りる。IdPトークン発行は latch.auth.tools の資産をtest_users_api.py内で利用)

### 3.3 触らないもの

- `backend/src/latch/auth/`(make_user_lookup・errors・service すべて不変。§2.6・§2.8)・`core/`(clock・db・deps)・`llm/`・`geo/`・`worker/`(ws-2の領域と明確に分離)
- `backend/alembic/`(マイグレーション追加なし・§1.3)・`backend/pyproject.toml`・`uv.lock`(依存追加なし)
- `backend/src/latch/settings.py`(新設定なし)・`compose.yaml`・`Makefile`・`.mise.toml`
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/M0/`・`prototype/`・ルート `README.md`

## 4. テスト方針

### 4.1 unit(`make test`。外部プロセス不要・FakeClock+スタブで決定的)

1. **年齢純粋関数 `age_years`**: 18歳の誕生日当日=18(登録可)・前日=17(UNDER_AGE)・2月29日生まれ(うるう年・平年の3月1日境界)・未来日付(負の年齢)・年またぎ(12/31生まれの1/1)。判定は `age_years(birth, clock.jst_date()) < 18` の組合せでservice経由でも1試験
2. **UserService.register**: スタブ永続化関数で、(a)18歳未満→UnderAgeError(422)・create_userが呼ばれない、(b)成人→UserCreated(id=スタブ値)・NewUserのcreated_at/updated_atがClock由来の現在時刻(§2.5)であることを検査、(c)スタブがUserExistsErrorを上げ→そのまま透過、(d)スタブが一般例外→503に包む。**JST境界**: FakeClockをJST深夜0時直前/直後に相当するUTC時刻へsetし、誕生日当日の判定がJST暦日で変わることを確認(§2.3のUTCずれ排除の証明)
3. **UserService.get_me**: スタブで行あり→UserMe(profile_complete=True)・行なし→UserNotFoundError(404)
4. **ルーティング**(create_app+スタブUserService注入+`app.dependency_overrides[require_authenticated]`): POST 201の応答形状(`{"user": {"id", "display_name"}}`・idはUUID文字列)・display_nameの欠落・空文字、birth_dateの形式不正→422 VALIDATION_ERROR(envelope)・UNDER_AGEのenvelope・me 200の応答形状(birth_date="YYYY-MM-DD"・profile_complete=true)・me 404 envelope・ボディ破損→400 MALFORMED_REQUEST(既存ハンドラ)
5. **arch規律**: 既存 `test_arch_no_direct_time.py` がusers/配下を自動スキャン(ヒットなし)

### 4.2 integration(`make test-ci`。compose常設の実DB・実apiプロセス。**apiイメージ再ビルド後**に実行・§1.2確定値13)

| # | 検証 | 根拠 |
|---|---|---|
| 1 | **初回登録フロー通し**: ツール発行IdPトークン→POST /v1/auth/token→`user:{id:null, profile_complete:false}`→POST /v1/users→201 `{"user":{"id:<uuid>, display_name}}`→GET /v1/users/me→200(id一致・birth_date=申告どおりのISO文字列・profile_complete:true)→**再token→`profile_complete:true`・id一致** | 05 §5(確定値1・2・5・9) |
| 2 | 17歳相当のbirth_date(実行日のjst_date基準で年齢17になる値)→422 envelope `code=UNDER_AGE`・DBに行がないことを確認 | 05 §5・08 D-10 |
| 3 | display_name欠落・birth_date="1990/04/01"(形式不正)→422 `code=VALIDATION_ERROR` | 05 §5(確定値1) |
| 4 | 同一subjectでの再登録(#1と同じIdPトークンで新JWT)→409 `code=USER_EXISTS` | 05 §5(確定値1) |
| 5 | 未登録subject(別subjectのIdPトークン)でGET /v1/users/me→404 `code=NOT_FOUND` | §2.4・05 §5(確定値7) |
| 6 | Authorization欠落・改ざんJWT→401 `code=UNAUTHENTICATED`(両エンドポイント) | 05 §5(確定値7・8) |
| 7 | 事後: #1・#4のusers行をDELETE(subjectは実行毎ユニークなuuid接尾辞で共有ci-db汚染回避。M0 ws-3試験と同じ規約) | STATUS運用 |

18歳の当日境界(FakeClockが必要)はunit(§4.1-2)で証明する。compose api実プロセスはClock固定のため、integrationでは代表値(17歳・成人)のみ。

### 4.3 既存資産への回帰

`make lint`・`make test`(既存unit+auth/llm/geo全件)・`make test-ci`(既存270+新規)がグリーン。auth 3エンドポイント・/health・Clock・Gateway・geo試験への影響なし(main.pyのlifespan変更は、auth_service注入済みパターンの既存unit試験ではASGITransportがlifespanを実行しないため影響しない)。

## 5. 完了条件(この単位の受渡し判定。計画書が参照する)

1. `make lint`・`make test` がグリーン(unit/users 追加分を含む)
2. `make test-ci` で §4.2-1〜7 がグリーン(実行前にapiイメージを再ビルドしていること・§1.2確定値13。並走ws-2はマイグレーション追加なしのためtest-ci同時実行制約なし)
3. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが引き続き `core/clock.py` のみ(§2.3・§4.1-5)
4. `alembic/` に差分なし・`pyproject.toml`/`uv.lock`/`settings.py`/`compose.yaml`/`Makefile` に差分なし(§2.8)
5. 触るファイルが §3.2・§3.1 の一覧どおり(auth/・core/・llm/・geo/・worker/に差分なし)
6. §4.2-1の初回登録フローが1本の試験として通る(me応答のbirth_date・profile_completeと、再token時の `profile_complete:true` まで含む)

## 6. 未解決の論点(設計は推奨で固定済み。supervisor確認事項)

1. **GET /v1/users/me・User行不在時の404解釈**(§2.4): docsに明示がないため「NOT_FOUND(リソース不在・全API)」の適用と解釈した。200+`profile_complete:false` 等の代替契約を採る場合は05 §5の改版になる
2. **「モデル導入はM1」予告の解釈**(§2.1): ws-3(M0)設計§1.3-2の「モデル導入はM1」を「M0では作らない」の意と解釈し、本単位では生SQL継続とした。ORM導入の判断はintents CRUD(ws-3 M1)へ委ねる。もし「M1で必ずORM土台を作る」意図であれば本設計の§2.1・§3は作り直し
3. **エラー優先順序の固定**(§2.2): 17歳かつ登録済みsubjectではUNDER_AGE(422)をUSER_EXISTS(409)より優先する順に固定した。逆順の契約要件はdocsに見当たらない
4. **auth側lookupとのSELECT重複の許容**(§2.8): auth/service.py の `make_user_lookup`(idのみ)とusers側のfetch(全列)はWHERE句が同一だが用途が異なるため、あえて統合せずauth不変を優先した。統合はws-3(intents)でusers参照経路が増えた時点で検討
5. **display_nameの上限なし**(§2.7): docsに規定がないため最大長を付けていない。text列に無制限の文字列が入る運用上の懸念がある場合は05への規定追加とともに制限を入れる
6. **`make test-ci` の再ビルド問題**(§1.2確定値13): compose upはイメージを再ビルドしないため、本単位からは検証手順に「api再ビルド」を明記する対応とした。Makefileのtest-ciへ `--build` 追加は複数単位の並走と衝突するため本単位では行わない(改善するかはスーパーバイザー判断)
