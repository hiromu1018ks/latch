# 第4章 1本の書き込みの経路: POST /v1/users はどうやってDBの行になるか

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第2章(トレース1・DI)・第3章(Clock・FakeClock・テストの2層)・Lab 1(psqlでSQLに触れた)・Lab 2
- この章を読み終えるとできるようになること:
  - 「読み取り」と「書き込み」で、失敗の種類がどう増えるか説明できる
  - POST /v1/users の経路をルータ→サービス→SQLの3層で追い、各層が何を担うか言える
  - 401・400・422・409・503の出どころを、コードの位置と対応させて説明できる
  - 満18歳の判定がなぜJSTの暦日付で行われるのか、FakeClockの境界例で示せる
  - 重複登録がDBのUNIQUE制約で検出され、409へ変換される経路を説明できる
  - 経路が3層に分かれていることが、テストで部品をすり替えることとどうつながるか説明できる
- 次に読むもの: `concepts/05-intents-parse.md`(LLMという外部の協力者を扱う、もう1つの経路)

## 4.1 読み取りと書き込みでは、失敗の種類が違う

第2章で追った `/health` は、失敗らしい失敗がありませんでした。時計を見て文字列を返すだけ。
一方、ユーザーの初回登録 `POST /v1/users` は、データベースに**新しい行を1本書き込む**処理です。
書き込みには、読み取りにはなかった失敗がいくつも同居します。

- 内容の検証: 表示名は空でもいいのか。生年月日の形式は
- 本人確認: 「あなたの」行を作る処理で、あなた自身であることをどう保証するのか
- 重複: 同じ人物が2回登録しようとしたら
- 年齢: 18歳未満は登録不可というルールの判定は誰が・いつやるのか
- 依存の障害: 書き込み先のDBが落ちていたら

この章で追うのは、この5つの失敗にそれぞれ違う場所で答えている経路です。
`docs/plans/M1/ws-1-design.md` が判断の記録、`backend/src/latch/users/` の4ファイルが実体です。
第2章と同じ読み方——入口から入って、1つの流れを最後まで追う——で進めます。

## 4.2 経路の全体図: 受付・判断・実行の3層

まず全体像から。`POST /v1/users` に届いたリクエストは、次の3つの層を通ってDBに届き、
その帰り道で201の応答になります。

```text
POST /v1/users {"display_name": "...", "birth_date": "1990-04-01", ...}
  │
  ├ ① ルータ (users/routes.py)          … 受付: 認証・入力の形チェック
  │     401 UNAUTHENTICATED / 400・422 はここで出る
  ├ ② サービス (users/service.py)       … 判断: 年齢・時刻・失敗の意味づけ
  │     422 UNDER_AGE / 409 USER_EXISTS / 503 はここで意味が決まる
  └ ③ SQL (users/service.py の下部)     … 実行: INSERT 1本・SELECT 1本
        DBのUNIQUE制約が最後の番人
             ↓
  201 {"user": {"id": "<uuid>", "display_name": "..."}}
```

第2章の `/health` は「ハンドラが時計を呼んで返す」の1枚岩でした。それがここでは3層に割れています。
理由は1つではありません。**テストで部品をすり替えられるようにするため**(4.7で実証します)と、
**失敗の種類ごとに担当を明確にするため**です。どの層が何を守っているかを覚えると、
この章の残りは全部その確認になります。

なお「受付・判断・実行」は、世の文献ではルーティング層・サービス層(ユースケース層)・
永続化層(リポジトリ層)と呼ばれることが多い対応関係です。LATCHでは `users/` パッケージの
中に `routes.py`・`service.py` というファイル名ではっきり見える形で置かれています。

## 4.3 門番が2人: 認証(401)と入力検証(400/422)

### 1人目: 認証 — あなたは誰か

`backend/src/latch/users/routes.py` の冒頭で、ルータ全体に1つの飾りが付いています。

```python
users_router = APIRouter(
    prefix="/v1/users",
    tags=["users"],
    dependencies=[Depends(require_authenticated)],  # C3(05 第5節冒頭)
)
```

`dependencies=[...]` は「このルータに属する**すべての**窓口で、リクエスト処理の前にこれを通せ」
というFastAPIへの指定です。通されるのが `require_authenticated`(`auth/deps.py`)で、
`Authorization: Bearer <JWT>` ヘッダーを検証し、中身のclaims(主張。トークンが持つ
「私はこういう者だ」の情報)を取り出します。

ここで **JWT**(ジェイ・ダブリュー・ティー)を初めて定義します。JWT(JSON Web Token)は、
身分証明書のデータに電子署名を付けたものです。署名があるので、受け取った側は
「これは確かにLATCHサーバーが発行したもので、書き換えられていない」と検証できます。
LATCHではログインのときに「GoogleやAppleで本人確認を済ませた、この利用者」というJWTを
発きます(GoogleやAppleのような、本人確認を代行するサービスを **IdP**(アイ・ディー・ピー、
Identity Provider)と呼びます)。ヘッダーがなければここで即座に401 UNAUTHENTICATED。
**窓口ごとに認証を書き忘れる事故を、ルータ単位の一括指定で防ぐ**のがこの形の狙いです。

認証を通ったリクエストからは、こんなコードで本人識別子を取り出します。

```python
@users_router.post("", status_code=201, response_model=UserCreatedResponse)
async def register_users(
    body: UserCreateRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[UserService, Depends(get_users_service)],
) -> UserCreatedResponse:
```

`claims` に入るのが検証済みのJWTの中身です。登録処理はこの `claims.auth_provider` と
`claims.auth_subject`(「どのIdPで・そのIdP上のどの人物か」を示す2つの値)に紐けてUser行を
作ります。第3章で学んだDIと同じ仕組みで、`svc` には次の層のサービスが届きます。

### 2人目: 入力検証 — 中身は正しい形か

`body: UserCreateRequest` の部分が2人目の門番です。`UserCreateRequest` は
**Pydantic**(ピダンティック)という、データの形を宣言すると検証までやってくれるライブラリの
モデルです。定義はこう書かれています。

```python
class UserCreateRequest(BaseModel):
    # 上限なし・文字種検証なし(docsに規定なし — design §2.7)
    display_name: str = Field(min_length=1)  # 空文字=必須欠落相当(422)
    birth_date: date  # YYYY-MM-DD以外は422(既存ハンドラがenvelope化)
    profile: UserProfileInput | None = None
```

`str = Field(min_length=1)` と宣言するだけで、FastAPIはリクエストのJSONがこの形に
合うかを**関数を呼ぶ前に**検証します。display_nameが空文字なら422、birth_dateが
`"1990/04/01"` のように形式を外れていても422。ハンドラの中身は1行も実行されません。

検証に落ちたときの応答は、`main.py` の `validation_error_handler` が共通の形に整えます。

```python
@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # 05 第5節の使い分け: JSON形式不正=400 / 必須欠落・値域外=422
    if any(err.get("type") == "json_invalid" for err in exc.errors()):
        return JSONResponse(
            status_code=400,
            content=_error_body("MALFORMED_REQUEST", "request body is not valid JSON"),
        )
    return JSONResponse(
        status_code=422,
        content=_error_body("VALIDATION_ERROR", "request validation failed"),
    )
```

ここに400と422の使い分けが出てきます。**JSONとしてそもそも壊れている**(カッコの閉じ忘れ等)は
400 MALFORMED_REQUEST、**JSONだが中身が規則に合わない**は422 VALIDATION_ERROR。
どちらも本文は `{"error": {"code", "message", "details"}}` という共通の封筒(envelope)で
返ります。LATCHの全APIでこの封筒の形は統一されています。

ここまでで、2人の門番が `{"display_name": "", ...}`(422)とヘッダーなし(401)を弾きました。
形の整ったリクエストだけが、次の層に進みます。

## 4.4 判断する層: 満18歳はJSTの暦日付で切る

`svc.register(...)` として呼ばれるのが `users/service.py` のUserServiceです。
ルータが受けた材料を、ビジネスのルールに照らして判断する層です。登録の核心部分を抜き出すと
こうなります。

```python
async def _register(
    self, *, claims, display_name, birth_date, profile,
) -> UserCreated:
    if age_years(birth_date, self._clock.jst_date()) < 18:
        raise UnderAgeError("under 18 years old")
    now = self._clock.now()
    new_user = NewUser(
        display_name=display_name,
        birth_date=birth_date,
        profile=profile,
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        created_at=now,
        updated_at=now,
    )
    user_id = await self._create_user(new_user)
    return UserCreated(id=user_id, display_name=display_name)
```

読むべき場所は2つ。まず冒頭の年齢判定、次に `now = self._clock.now()` です。

### 満年齢の計算式

年齢は `age_years` という純粋関数(状態を持たず、入力だけで結果が決まる関数)に切り出してあります。

```python
def age_years(birth_date: date, today: date) -> int:
    """満年齢(design §2.3)。(月, 日)タプル比較 — 誕生日当日に加算。

    2月29日生まれは、その暦日に2月29日を持たない年は3月1日に加算される
    (自己申告値のため1日の差は許容して比較を単純に保つ)。
    """
    return (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )
```

最後の行が肝です。`(9, 28) < (9, 28)` のような**タプル比較**で「まだ誕生日が来ていない」を
判定します。Pythonでは `(9, 27) < (9, 28)` が `True` になる、辞書順の比較を利用した
定番の書き方です。2月29日生まれの扱いまでdocstringに決めてあるのは、この種の計算に
「うるう年は?」という問いが必ず付いてくるからです。

### なぜ `jst_date()` なのか

判定に使う「今日」は `self._clock.jst_date()` です。第3章で見たClock契約の、日本の暦日付に
変換する側のメソッドですね。「18歳未満は登録不可」というルールは誕生日の到達で決まり、
誕生日は暦日で区切るしかありません。**どの時間帯の暦で切るのか**を決めないと、境界の9時間が
宙に浮きます。LATCHは日本のサービスとしてJSTで切ると決めています(`ws-1-design.md` §2.3)。

差が体感できる例をFakeClockで見てみましょう。次を `backend/` で実行すると、
JSTで9月28日生まれの人が、**1秒の間に**17歳から18歳になります。

```bash
cd backend && uv run python - <<'EOF'
from datetime import UTC, date, datetime, timedelta
from latch.core.clock import FakeClock
from latch.users.service import age_years

fake = FakeClock(datetime(2026, 9, 27, 14, 59, 59, tzinfo=UTC))  # JST 23:59:59
print(fake.jst_date(), age_years(date(2008, 9, 28), fake.jst_date()))
fake.advance(timedelta(seconds=1))                                # JST 翌0:00:00
print(fake.jst_date(), age_years(date(2008, 9, 28), fake.jst_date()))
EOF
```

期待される出力:

```
2026-09-27 17
2026-09-28 18
```

UTCの時計は1秒しか進んでいません。それでも日本の暦日付が9月27日から9月28日に変わり、
「18歳の誕生日当日は登録可」というルールの判定がひっくり返ります。Clockが
`jst_date()` という専用の出口を1つ持っているので、時計を1秒進めるだけでこの境界を
試験できる——第3章の「時刻を集約する」設計が、ここで地で働いているのです。

### 時刻列はClockの明示値で書く

`created_at=now, updated_at=now` の `now` はClock由来です。PostgreSQLには
「挿入時刻を自動で付ける」便利機能(DEFAULT now())がありますが、LATCHはあえて使いません。
**DBの機能に任せると、テストで時刻を制御できなくなる**からです。Clock差し替えの設計を
崩さないために、時刻は必ずPython側で作って明示的に渡します(`ws-1-design.md` 確定値11)。

## 4.5 実行する層: SQLは生で書き、idの採番はDBに任せる

判断を通った `NewUser` は、最後の層でSQLに変わります。この層は**生SQL**(ORMのような
変換の仕組みを通さず、SQL文を文字列で直接書くやり方)で書かれていて、`service.py` の
下部にSQL文が定数として置かれています。

その前に、登場する道具を1つ定義しておきます。**SQLAlchemy**(エスキューエル・アルケミー)
は、Pythonのコードからデータベースに接続してSQLを実行するためのライブラリです。
LATCHのDBまわりはすべてこのライブラリ経由です。第1章1.10の地図にあった `core/db.py`
(DB接続)がSQLAlchemyの設定を担い、ここではその `text()` という機能を使います。

```python
_INSERT = text("""
    INSERT INTO users
        (display_name, profile, birth_date, auth_provider, auth_subject,
         created_at, updated_at)
    VALUES
        (:display_name, CAST(:profile AS jsonb), :birth_date, :auth_provider,
         :auth_subject, :created_at, :updated_at)
    RETURNING id
""")
```

読み方はLab 1でpsqlに触れたSQLと同じです。`:display_name` のような `:` 始まりは、
SQLAlchemyの `text()` で使う**名前つきプレースホルダー**で、値は別途辞書で渡します。
SQLの文字列と値が分かれるので、値の中身がSQLの構文を壊す(SQLインジェクション)事故が
原理的に起きません。

2つの設計判断がこの1本に込められています。

**idはDBに任せる**。INSERT文にid列がなく、代わりに `RETURNING id` で「採番された結果を
ちょうだい」と受け取ります。users表のidには `DEFAULT gen_random_uuid()` が付いているので、
DBが新しいuuidを採番してくれます。Python側にもuuidを作って渡す道はありますが、
採番の責務が2か所に分散すると、どちらが真実か分からなくなります。

**ORMを使わない**。SQLAlchemyには「表定義をPythonのクラスに写し、INSERTをメソッド呼び出しで
書ける」ORM(Object Relational Mapping、オブジェクト関係マッピング)という仕組みがあります。
ところがusersの永続化はINSERT 1本・SELECT 1本しかありません。この規模でORMを導入すると、
表の定義が「マイグレーション」と「モデルクラス」の2か所に重複して残ります。表の定義は
マイグレーションだけが持つ、という方針を優先して、usersでは生SQLとしました
(`ws-1-design.md` §2.1)。当時「intents CRUD(ws-3)で参照が増えた時点で再判断する」と
記録されていたこの判断は、ws-3でも**導入しない**に踏襲されました。保存側のSQLも
INSERT 1本・SELECT 3種・UPDATE 2種と1文で書けるものばかりで、ORMの利益が働く場面が
ないためです(判断の記録は `ws-3-design.md` §2.9。次の再判断はM2)。

## 4.6 最後の番人: DBのUNIQUE制約が409を作る経路

では、同じ人物が2回目の登録を試したら何が起きるでしょう。

users表には `UNIQUE(auth_provider, auth_subject)` という制約(`uq_users_auth_provider_subject`)
がalembic 0001から付いています。「確認してからINSERT」をPythonで書くこともできますが、
ws-1の設計はあえて**いきなりINSERTして、制約違反を捕捉する**道を選びました。

理由は競合の安全です。事前のSELECTとINSERTの間に、同じ値での登録が割り込む余地は
どうしても残ります(この種のすき間をTOCTOU——Time Of Check To Time Of Use、
確認時と使用時のずれ——と呼びます)。2つの処理が同時に「未登録だ」を見て、両方がINSERTを
試みたとき、**最後に残れるのはDBの制約だけ**です。真実源に判定を任せる、ということです。

失敗は次の経路で409に変わります。

```python
        except IntegrityError as exc:
            classified = classify_integrity_error(exc)
            if classified is not None:
                raise classified from exc
            raise
```

INSERTが制約に触れるとSQLAlchemyは `IntegrityError` を投げます。`classify_integrity_error` が
その中身を見て、**犯人が `uq_users_auth_provider_subject` のUNIQUE違反(sqlstate 23505)のときだけ**
`UserExistsError`(409 USER_EXISTS)へ変換します。それ以外の整合性エラーは素通りさせて、
UserServiceの外側の `except Exception` が503 DEPENDENCY_UNAVAILABLEへ包みます。
「重複は利用者の再登録、それ以外はシステム側の異常」という**失敗の意味づけ**を、ここで
行っているわけです。

```python
    orig = exc.orig
    sqlstate = getattr(orig, "sqlstate", None)
    constraint_name = getattr(orig, "constraint_name", None)
    if constraint_name is None:
        driver = getattr(orig, "driver_exception", None)
        if driver is not None:
            constraint_name = getattr(driver, "constraint_name", None)
```

この「`driver_exception` の先も見る」書き方は、実は本番DBでの検証で見つかった欠陥の修正跡です。
unit試験は合成した例外で書かれていたため、実DBでは例外が別の形(SQLAlchemyのドライバーラッパー)に
包まれて届くことを見落としていました。結果、重複登録が409でなく503として返る状態が
実環境での試験(test_4)で発覚しました。`ws-1-report.md` の「修正ラウンド」がこの記録です。
**unitが緑でも、本物の部品の組み合わせでは違う挙動がありうる**——だからintegration試験が
存在する、という教訓の実例は、このプロジェクトでこれが2度目です(1度目はM0の認証。
`docs/plans/STATUS.md` の完了記録に経緯があります)。

なお、エラーの優先順も設計で固定されています。「17歳かつ登録済み」のリクエストには
409でなく422 UNDER_AGEが返ります。年齢判定(4.4)がINSERT(4.6)の前にあるからです。
受付可否が先、一意性が後、という自然な順です(`ws-1-design.md` §2.2)。

## 4.7 テスト33件は層ごとに部品をすり替えて守る

`backend/tests/unit/users/` には3つのファイル、合わせて33件の試験があります。
層ごとに1ファイルずつ対応しているのが見るとおりです。

| ファイル | 守る層 | すり替える部品 |
|---|---|---|
| `test_age.py`(8件) | 年齢の純粋関数 | なし(引数だけで決まる) |
| `test_users_service.py`(14件) | サービスの判断 | 永続化関数をスタブに |
| `test_users_routes.py`(11件) | ルータの受付 | UserServiceをスタブに |

たとえばルータの試験は、本物のUserServiceの代わりにこういうスタブ(代理部品)を注入します。

```python
class StubUserService:
    """ルーティング試験用のUserServiceスタブ(design §4.1-4)。"""

    def __init__(self):
        self.registered: list[dict] = []
        self.register_error: Exception | None = None
        self.me: UserMe | None = ME
```

さらに `app.dependency_overrides[require_authenticated] = lambda: CLAIMS` で認証まで
差し替えます。**DBも認証もLLMもいない世界で、ルータの応答の形だけを試す**。
これは第3章で学んだ「差し替え点が1つなら、テストはその1か所だけすり替える」の
応用です。各層の試験がすり替える相手が1種類で済むのは、層が分かれているおかげです。

実DB・実HTTPでの組み合わせ(integration試験6件。初回登録のフロー通しや401・409の実証)は
`make test-ci` 側に置かれ、unitとは別のタイミングで走ります。第3章の2層構成そのものが、
ws-1でもう1周できた形です。

## 4.8 自分で確かめる

1. `uv run pytest tests/unit/users -v` を実行し、試験名を縦に読む。どの試験が4.3〜4.6の
   どの節に対応するか、ノートに書き分けてみる
2. 4.4のFakeClockの例で `birth_date=date(2008, 9, 27)` に変える。出力がどう変わり、
   なぜそうなるかを説明する(JST日付は変わっても年齢判定が変わらない理由)
3. `rg -n "classify_integrity_error" backend/src` を実行し、定義と使用箇所が
   どうつながっているかを見る
4. `git log --oneline -- backend/src/latch/users/` でこのパッケージの生い立ちを一覧する。
   `git show 0895411` が4.6で触れた修正ラウンドの実物

## 4.9 この章の再統合

- 書き込みは、読み取りにない失敗(検証・本人確認・重複・ルール違反・依存障害)を抱える。
  LATCHはそれを受付・判断・実行の3層に分けて、失敗の種類ごとに担当を固定した
- 3層に分けたもう1つの利益はテストにある。各層が「届いたものを信じて、自分の判断を
  返す」だけなので、層の境目で部品をスタブにすり替えれば、DBも認証もいない世界で
  各層を単独に試せる(4.7の33件がその実証)
- 401は認証(require_authenticatedの一括指定)、400/422は入力の形(Pydantic+共通ハンドラ)、
  422 UNDER_AGEと409と503はサービス層の意味づけ。同じDBの失敗でも、
  「重複」と「それ以外」は別のステータスに分かれる
- 年齢判定はJST暦日付で切る。Clockの `jst_date()` があるから、9時間のずれ問題が
  1本の経路で解決され、境界が1秒のadvanceで試せる
- 永続化はINSERT 1本・SELECT 1本の生SQL。「表の定義はマイグレーションだけが持つ」
  「idはDBに任せる」「時刻はClockの明示値」が一貫している
- 重複の検出は事前SELECTでなくUNIQUE制約に任せる(TOCTOUを残さない)。
  unitが緑でも実DBでは違う例外の形が来ることは、2度の実例が出ている

次章では、この3層の型をもう1つの経路に適用します。相手はDBではなくLLM——
「同じ入力に必ず同じ答えを返すとは限らない協力者」です。経路の型は同じでも、
信頼の設計はまったく別物になります。

## 4.10 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| 書き込み / 読み取り | DBの行を作る・変える処理 / 見るだけの処理 |
| ルータ / サービス層 / 永続化層 | 受付 / ルールの判断 / SQLの実行 |
| claims | JWTが運ぶ「私はこういう者だ」の情報 |
| JWT / IdP | 署名付きの身分証データ / 本人確認を代行するサービス(Google・Apple等) |
| Pydantic | データの形を宣言すると検証までやってくれるライブラリ |
| SQLAlchemy | PythonからDBに接続してSQLを実行するためのライブラリ |
| envelope(封筒) | `{"error": {code, message, details}}` の共通エラー形式 |
| プレースホルダー | SQLの `:名前`。値と文を分けて安全に渡す仕組み |
| ORM | 表をPythonのクラスに写して操作する仕組み(_usersでは不採用_) |
| TOCTOU | 確認時と使用時のずれによるすき間。事前SELECTに残る競合 |
| IntegrityError | DBの制約違反を表すSQLAlchemyの例外 |
| スタブ | 試験のために差し込む、都合の良い振る舞いの代理部品 |

## 4.11 確認問題

1. 書き込みの処理が読み取りより難しくなる理由を、増える失敗の種類(この章の冒頭で
   挙げた5つ)を使って説明してください
2. `dependencies=[Depends(require_authenticated)]` をルータに付けることは、
   各ハンドラに個別に書くことと何が違いますか。防げる事故は何ですか
3. `{"birth_date": "1990/04/01"}` のリクエストに返るステータスコードと、
   それを決めているコードの位置を説明してください
4. 満18歳の判定をUTCのまま行うと何が起きますか。JST 23:59:59の例で説明してください
5. 重複登録の検出に事前SELECTでなくINSERT衝突を使う理由を、TOCTOUという言葉を使って
   説明してください
6. `classify_integrity_error` が制約名を2段階(`orig` 直と `driver_exception` の先)で
   探すのはなぜですか。この修正が生まれた経緯と合わせて説明してください
7. usersでORMを導入しなかった理由は何ですか。その後のintents CRUD(ws-3)でも
   導入見送りが踏襲されました。「ORMの利益を使う場面がない」の意味を、
   この2単位のSQLの内容(本数・種類)から説明してください
8. 3層に分かれていることは、テストを書くうえでどんな利益になりますか。
   4.7のスタブの使い方と合わせて説明してください
