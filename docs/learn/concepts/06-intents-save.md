# 第6章 思いを1行に確定する: POST /v1/intents の保存・状態遷移・出来事の記録

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第4章(3層の経路・検証・DB制約)・第5章(parseとParserOutput・Protocol・スタブ)・第3章(Clock・FakeClock)
- この章を読み終えるとできるようになること:
  - parseで作った構造化データがDBの1行になる経路を、検証の5段階つきで説明できる
  - 同じ入力が「下書きでは受理されて、確定では422になる」理由を、検証の切替として説明できる
  - 保存時にサーバ側で確定される値(補完・alcohol_involved・座標)を挙げ、
    それぞれ確定する理由を説明できる
  - 時刻・年齢・地名の検証がなぜこの順で走るか言える
  - Intentの状態遷移図を描き、versionの動き(据え置き・+1)を操作ごとに予測できる
  - 保存が成功した瞬間に「出来事」が同じトランザクションで1行書かれる理由を説明できる
  - unit テストが緑でも実DBで挙動が変わった今回の実例を挙げ、integration試験の存在意義に繋げられる
- 対応コード: `backend/src/latch/intents/`(第5章のparse側7ファイルに、保存側の4ファイルが加わって11ファイル。この章の主役は `service.py` の後半・`mapping.py`・`events.py`・`store.py`)
- 設計の根拠: `docs/plans/M1/ws-3-design.md`(特に§2.2のEvent発行表・§2.3の検証分離・§2.7のversion管理)と `docs/plans/M1/ws-3-report.md` §7(実DBで見つかった欠陥の記録)
- 次に読むもの: ws-4(レート制限)・ws-5(フロントエンド)の実装とともに追加される章

## 6.1 2本の道が合流する: parseは見せて、保存は確定する

第5章の `POST /v1/intents/parse` は、自然文を構造化データに変換して**見せるだけ**の窓口でした。
変換結果にユーザーが OK を出してから、いよいよ保存する。その窓口が今回実装された
`POST /v1/intents` です。リクエストの形を見てください。

```json
{
  "raw_text": "今週末あたりで飲みに行きたい",
  "status": "active",
  "structured_intent": {
    "category": {"primary": "drinking"},
    "time": {"start": "2026-09-27T18:00:00+09:00"},
    "location": {"name": "天文館"}
  }
}
```

`raw_text` はユーザーが打った原文、`structured_intent` はparseが返した構造化データを
ほぼそのまま再送したもの(第5章5.1の出力例と形が同じです)。つまりこのAPIは、
第4章で学んだ**書き込みの3層**(ルータ→サービス→SQL)を骨格にしながら、
その中心に第5章の**構造化データ**が流れる経路です。2章分の道具がここで合流します。

窓口は1つではなく7つ用意されました。`routes.py` の後半に並んでいます
(`backend/src/latch/intents/routes.py:197` から)。

```text
POST   /v1/intents              作成(active または draft)
GET    /v1/intents              一覧(?status=・?cursor=・?limit=)
GET    /v1/intents/{id}         取得
PATCH  /v1/intents/{id}         更新(全置換・draft→active化)
DELETE /v1/intents/{id}         削除(実体は cancelled への遷移。6.5で)
POST   /v1/intents/{id}/pause   一時停止
POST   /v1/intents/{id}/resume  再開
```

登録・参照の2窓口だったusers(第4章)と比べると、一気に賑やかになりました。理由は単純で、
Userは「登録して終わり」の行ですが、Intentは**作られてから状態が変わる**行だからです。
下書きを書き直す。確定する。止める。やめる。そのすべてに窓口が要ります。
一覧と取得の2つは読み取りの窓口で、第4章のGETと同じ経路の型なので深入りしません。
この章の残りは、**書き込みが絡む5つの窓口**(作成・更新・削除・停止・再開)の
背後で何が起きているかの説明になります。

## 6.2 「まだ決めない」下書きと「確定する」active: 検証の切替

保存APIのリクエストにある `"status": "active"` に注目してください。ここには
`active`(確定)か `draft`(下書き)を指定できます。省略時は `active` です。

なぜ入口が2つあるのでしょう。ユーザーの実際を思い浮かべてください。アプリが
「今週末あたりで飲みに行きたい」を構造化して見せてきた。ここで「場所は天文館で
合ってるけど、日付はまだ決めてないな」と思うことは珍しくありません。その中途半端な
状態を捨ててしまうと、また最初から打ち直しです。**形が不完全でも置いておける場所**が
欲しい。それが下書きです。

2つの入口は、受付の厳しさが違います。同じ入力が `draft` では受理されて `active` では
422になる。この**検証の切替**が、ws-3の設計の中心にある考え方です。

| 項目 | active(作成・更新・active化) | draft(作成・再保存) |
|---|---|---|
| raw_text | 必須・1〜300字 | 必須・1〜300字(唯一の検証) |
| structured_intent | 必須(全量) | 任意(部分的でも保存) |
| 必須3フィールド | 検査して422 | 検査しない |
| 時刻(過去・7日超) | 検査して422 | しない(形式だけ検査) |
| 年齢(飲酒) | 検査して422 UNDER_AGE | しない |
| 地名→座標 | 実行して422 GEOCODING_FAILED | しない(座標はNULL) |
| 補完(終了時刻・期限等) | 適用 | しない |
| 出来事の記録(Event) | 発行 | なし |

この表のどこを見ても、**リクエストの形そのものは変わっていません**。
activeでもdraftでも送るJSONは同一で、違うのは「検証をどこまで適用するか」だけ。
だから入力モデルは1つ
(`StructuredIntentInput`、全フィールドOptional)にまとめられました
(`backend/src/latch/intents/intent_input.py:57`)。第5章5.3では、「利用者からの入力」と
「LLMからの出力」が同じPydantic検証で関所を通ると学びました。同じ発想で、
今度は「形式は常に検査・意味はactive時に検査」という線を1つのモデルに収めています。

実際に動きの違いを見ましょう。DBもLLMも使わない、第4章・第5章で練習したスタブの
構成です。`backend/` で実行します。

```bash
cd backend && uv run python - <<'EOF'
import asyncio, uuid
from datetime import UTC, date, datetime
from latch.core.clock import FakeClock
from latch.geo.service import Geofeature
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.service import IntentService
from latch.intents.store import UserRow

NOW = datetime(2026, 9, 27, 3, 0, 0, tzinfo=UTC)   # JST 2026-09-27 12:00
TENMONKAN = Geofeature(source="osm_poi", kind="amenity", name="天文館",
                       city_name=None, pref_name=None, lon=130.5581, lat=31.5965)

class FakeConn:                    # uow が渡してくる「書き込みトランザクション」の記録係
    def __init__(self): self.executes = []
    async def execute(self, stmt, params=None): self.executes.append((stmt, params))

class FakeUow:
    def __init__(self): self.conn = FakeConn()
    def __call__(self): return self
    async def __aenter__(self): return self.conn
    async def __aexit__(self, *exc): return False

class StubStore:                   # DBの代理(保存内容を記録するだけ)
    def __init__(self): self.inserts = []
    async def fetch_user_row(self, provider, subject):
        return UserRow(id=uuid.UUID(int=1), birth_date=date(2000, 1, 1))
    async def insert(self, conn, cols, *, user_id, status, now):
        self.inserts.append(cols); return uuid.uuid4()

class StubGeocoder:                # 「天文館」→固定座標を返すジオコーダの代理
    async def geocode_forward(self, name): return TENMONKAN

INP = StructuredIntentInput.model_validate({
    "category": {"primary": "drinking"},
    "time": {"start": "2026-09-27T18:00:00+09:00"},   # JST 18:00(開始だけ指定)
    "location": {"name": "天文館"},
    "alcohol_involved": False,
})

async def run(status):
    uow = FakeUow(); store = StubStore()
    svc = IntentService(clock=FakeClock(NOW), store=store, uow=uow,
                        reader=uow, geocoder=StubGeocoder())
    row = await svc.create(auth_provider="google", auth_subject="sub-x",
                           raw_text="今週末あたりで飲みに行きたい",
                           status=status, structured_intent=INP)
    tag = f"[{status}]"
    print(f"{tag} status={row.status} version={row.version}")
    print(f"{tag} category={row.category_primary} alcohol_involved={row.alcohol_involved}")
    if row.time_end is not None:
        print(f"{tag} time_end={row.time_end.isoformat()} (start+3時間の補完)")
    else:
        print(f"{tag} time_end=None (draftは補完しない)")
    if row.expires_at is not None:
        print(f"{tag} expires_at={row.expires_at.isoformat()} (選択肢から補完)")
    else:
        print(f"{tag} expires_at=None (draftは補完しない)")
    print(f"{tag} 座標=({store.inserts[0].geo_lon}, {store.inserts[0].geo_lat})")
    ev = uow.conn.executes
    kinds = [p["event_type"] + " " + p["payload"] for _, p in ev]
    print(f"{tag} 同一トランザクション内のEvent INSERT: {len(ev)}本 {kinds or 'なし'}")

async def main():
    await run("active")
    print()
    await run("draft")

asyncio.run(main())
EOF
```

期待される出力(2026-09-28に実行して確認しました):

```
[active] status=active version=1
[active] category=drinking alcohol_involved=True
[active] time_end=2026-09-27T21:00:00+09:00 (start+3時間の補完)
[active] expires_at=2026-09-27T23:30:00+09:00 (選択肢から補完)
[active] 座標=(130.5581, 31.5965)
[active] 同一トランザクション内のEvent INSERT: 1本 ['created {"version": 1}']

[draft] status=draft version=1
[draft] category=drinking alcohol_involved=False
[draft] time_end=None (draftは補完しない)
[draft] expires_at=None (draftは補完しない)
[draft] 座標=(None, None)
[draft] 同一トランザクション内のEvent INSERT: 0本 なし
```

**同じ入力・同じ時計**を渡しているのに、結果がここまで違います。active では
`alcohol_involved` が `False` から `True` に変わり(6.4で理由を説明します)、
終了時刻と有効期限が補完され、「天文館」が座標に解決され、Event が1本記録される。
draft ではそのどれも起きません。保存される列の多くが NULL のまま置かれる。
この1枚の出力が、この章の内容のほとんどを先取りしています。

## 6.3 検証は5つの関所を、安いものから順に通る

active経路の検証は、`service.py` の `create` の中に、決まった順で並んでいます。
`backend/src/latch/intents/service.py:361` からの本体を見てください
(説明のために該当行だけを抜き、間は `...` に省略しています)。

```python
    async def _create(
        self, *, auth_provider, auth_subject, raw_text, status, inp,
    ) -> IntentRow:
        user = await self._require_user(auth_provider, auth_subject)
        now = self._clock.now()
        if status == "active":
            cols = self._resolve_active_or_raise(inp, now=now)
            if cols.alcohol_involved:
                self._require_age_20(user)
            cols = await self._geocode_or_raise(inp.location.name, cols)
            ...
```

読み下すと、active作成は **形式 → 必須3 → 時刻 → 年齢 → ジオコーディング** の順に
関所を通ってから保存に向かいます。順番には理由があります。前の関所ほど安く、
後ろの関所ほど高くつくからです。安い方から追います。

**1番目の関所: 形式(Pydantic・ルータ層)**。raw_textの字数・カテゴリの値が3種か・
時刻がタイムゾーン付きか、といった**入力の形**の検査は、`IntentCreateRequest` と
`StructuredIntentInput`(intent_input.py)が請け負います。ハンドラが呼ばれる前に
FastAPIが検証するので、ここを通れば「形は正しい」が保証済みです
(第4章4.3と同じ仕組み)。

**2番目: 必須3フィールド**。第5章5.1でparseの出力に必須3(category・time.start・
location.name)があったのを覚えていますか。保存でも同じ3つが、マッチング対象として
成立する最低条件です。`service.py:267` に書かれています。

```python
    @staticmethod
    def _validate_required3(inp: StructuredIntentInput) -> None:
        if inp.category is None or inp.category.primary is None:
            raise IntentValidationError("category is required")
        if inp.time is None or inp.time.start is None:
            raise IntentValidationError("time.start is required")
        if inp.location is None or inp.location.name is None:
            raise IntentValidationError("location is required")
```

**3番目: 時刻**。開始時刻が過去なら、もう会えない約束を保存することになります。
逆に7日より遠い未来も、このサービスの対象領域外です。`service.py:276` が検査します。

```python
    @classmethod
    def _validate_times(cls, inp: StructuredIntentInput, *, now: datetime) -> None:
        """過去不可・現在+7日上限(05 §5。境界: nowちょうど/now+7日ちょうどは受理)。"""
        start = inp.time.start if inp.time else None
        if start is not None:
            if start < now:
                raise IntentValidationError("time.start is in the past")
            if start > now + _MAX_AHEAD:
                raise IntentValidationError("time.start exceeds 7 days")
```

`now` との比較に使われているのは、第3章で学んだ **Clock** です。基準時刻が1本に
集約されているおかげで、「nowちょうどは受理・now-1秒は422」という1秒刻みの境界が
`test_time_validation.py` の6件の試験で確かめられています。draftではこの関所を
通らないので、同じ過去の時刻が受理されます(6.2の表のとおり)。

**4番目: 年齢**。お酒が絡むIntentには20歳という壁があります。`service.py:262` です。

```python
    def _require_age_20(self, user: UserRow) -> None:
        if age_years(user.birth_date, self._clock.jst_date()) < 20:
            raise UnderAgeError("under 20 years old")
```

見覚えのある形ではありませんか。`age_years` は第4章4.4で読んだ、(月, 日)タプル比較の
満年齢の純粋関数そのものです。usersの18歳線で使うために作った部品を、intentsは
20歳線に**そのまま再利用**しています。2月29日生まれの扱いまで含めて1つの実装しか
存在しない——第5章5.6の「補完の単一規則」と同じ規律が、年齢計算にも効いています。

**5番目: ジオコーディング**。「天文館」という地名を、DBのgeofeatures表の地物に
照合して座標(経度・緯度)に変換する処理です(地名→座標の方向を**正転**と呼びます。
Lab 1の `make geo-verify` で観察したものです)。該当する地物がなければ、保存する
意味がないので422で返します。`service.py:297` です。

```python
    async def _geocode_or_raise(
        self, name: str, cols: ResolvedColumns
    ) -> ResolvedColumns:
        feature = await self._geocoder.geocode_forward(name)
        if feature is None:
            raise GeocodingFailedError("geocoding failed")
        return replace(cols, geo_lon=feature.lon, geo_lat=feature.lat)
```

`self._geocoder` の型は `SupportsForwardGeocoding` という Protocol です
(`service.py:189`)。第5章5.5の `SupportsParseIntent` と同じ仕組みで、
実際に入るのは `geo/` のGeoServiceですが、IntentServiceはそのことを型として知りません。
「`geocode_forward` という形のメソッドを持つ者なら誰でもここに渡せる」ので、
この章の実行例では固定座標を返すスタブに差し替えられました。LLMで学んだ境界の
作り方が、地名変換でもそのまま使われています。

ここで422の内訳をまとめておきます。ws-3で追加された422系は3種です。
`必須3欠落・過去時刻・7日超過 → 422 VALIDATION_ERROR`、`20歳未満の飲酒 → 422 UNDER_AGE`、
`地名の解決失敗 → 422 GEOCODING_FAILED`。どれも「利用者が内容を直せば通る道が残っている」
失敗で、第5章5.4の分類でいえば**利用者起因**の側です。システム起因の503は、
この後ろ(保存の実行)でしか起きません。

## 6.4 サーバだけが決められる値: 補完・alcohol確定・座標

検証を通った入力は、次に**保存列への変換**を通ります。担当は `mapping.py` です。
ここでの処理は、クライアントが送った値をそのまま保存するのではなく、
**サーバ側の規則で確定してから保存する**点が特徴です。3つあります。

1つめは**補完**です。第5章5.6の終わりに予告がありました。「time.endがnullなら
開始3時間後に読み替える、人数がnullなら2人、という補完のルールは
`completion.py` が唯一の実装。消費するのは保存(ws-3)」——あの回収がここです。
`mapping.py:106` の `resolve_for_active` が消費しています。

```python
def resolve_for_active(inp: StructuredIntentInput, *, now: datetime) -> ResolvedColumns:
    """active正規化。必須3検証済み前提(service)。補完を消費(design §2.3表)。"""
    ...
    return replace(
        cols,
        category_primary=primary,
        alcohol_involved=(
            True if primary == "drinking" else (inp.alcohol_involved or False)
        ),
        time_end=inp.time.end or default_time_end(time_start),
        expires_at=inp.expires_at or nearest_expires_at(time_start, now),
        geo_radius_m=(
            inp.location.radius_m
            if inp.location.radius_m is not None
            else DEFAULT_RADIUS_M
        ),
    )
```

`inp.time.end or default_time_end(time_start)` の読み方は「endが送られていれば
それを、なければ開始+3時間を」。6.2の実行例で `time_end=2026-09-27T21:00:00+09:00`
が補完されたのはこの行の働きです(18:00開始+3時間)。有効期限 `expires_at` も、
「今夜23:30・明日12:00・明日23:30・3日後」の4択から開始+3時間に最も近いものが
選ばれます(実行例では 23:30 が選ばれました)。

2つめは **alcohol_involved の確定**です。上の引用の真ん中、
`True if primary == "drinking" else (inp.alcohol_involved or False)`
に注目してください。カテゴリが飲酒(drinking)なら、クライアントが
`alcohol_involved: false` を送っていても **true に確定**されます。
ユーザーの入力よりサーバの規則が優先される、数少ない箇所です。

なぜこうするのか。「飲みに行きたい」で `alcohol_involved: false` というデータは、
後のマッチングで「飲まない人にも飲みの提案を流す」誤配信の元になります。
お酒の有無は年齢検証(20歳)の発動条件でもあるので、ここで嘘の false が通ると
未成年の飲酒Intentが検査を素通りしてしまう。**整合性に関わる値は、
信頼できる側(自分のカテゴリ判定)で確定する**、という判断です。

確定された値が年齢検証とどう噛むか、動かして確かめられます。19歳の利用者が
`alcohol_involved: false` を明示して送っても、422 UNDER_AGE になります。

```bash
cd backend && uv run python - <<'EOF'
import asyncio, uuid
from datetime import UTC, date, datetime
from latch.core.clock import FakeClock
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.errors import IntentsError
from latch.intents.service import IntentService
from latch.intents.store import UserRow

NOW = datetime(2026, 9, 27, 3, 0, 0, tzinfo=UTC)  # JST 2026-09-27

class FakeUow:
    def __call__(self): return self
    async def __aenter__(self): return None
    async def __aexit__(self, *exc): return False

class StubStore:
    async def fetch_user_row(self, provider, subject):
        return UserRow(id=uuid.UUID(int=1), birth_date=date(2007, 3, 15))  # 19歳

INP = StructuredIntentInput.model_validate({
    "category": {"primary": "drinking"},
    "time": {"start": "2026-09-27T18:00:00+09:00"},
    "location": {"name": "天文館"},
    "alcohol_involved": False,   # クライアントは「飲まない」と送っている
})

async def main():
    svc = IntentService(clock=FakeClock(NOW), store=StubStore(), uow=FakeUow(),
                        reader=FakeUow(), geocoder=None)
    try:
        await svc.create(auth_provider="google", auth_subject="sub-x",
                         raw_text="飲みに行きたい", status="active", structured_intent=INP)
    except IntentsError as e:
        print(type(e).__name__, "/", e.http_status, "/", e.code)

asyncio.run(main())
EOF
```

期待される出力:

```
UnderAgeError / 422 / UNDER_AGE
```

3つめは**座標**です。クライアントは `location.name`(地名)しか送りません。
座標は6.3の5番目の関所でジオコーディングが解決し、保存列 `geo_center` に
PostGISのポイント型で入ります。ここで設計段階の発見を1つ紹介します。
intents表には地名を保存する列が**存在しない**のです(座標系の geo_center・geo_radius_m
と、構造データのJSONにはキーがなかった)。しかし後でIntentを再表示したり、下書きを
再編集したりするには地名が要る。アプリの確認画面では、条件リストに
「場所: 天文館」のように表示することになっています(仕様書03 §3)。座標から逆変換で
地名を拾うと1kmグリッドの丸めで別の地名になりかねません。
設計は `structured_data` へ `location_name` キーを追加して格納することに
しました(`mapping.py:63` の `_structured_data`)。この列は **JSONB**
(JSON形式のデータをそのまま保存できるPostgreSQLの列型)なので、中のキー追加は
マイグレーションを要しません。この判断も含めて、`ws-3-design.md` §6-1 に記録があります。
**仕様書に穴を見つけたら、判断と根拠を記録してから埋める**——このプロジェクトの
作法がまた1つ増えました。

なお、第5章5.6のng_unverifiable(判定不能NG)もここで形を変えて保存されます。
`downgraded_from_ng: true` の印を付けて soft_constraints(柔らかい条件)の一員にする
規則です(`mapping.py:56` の `_soft_constraints`)。応答を返すときは逆向きの変換
(`to_response_structured`)でsoftとngに分けて戻すので、ユーザーには元の形で見えます。

## 6.5 行の一生: 状態遷移とversion

保存されたIntentの行は、status列の値を変えながら生きていきます。
考えられる状態は6つです: `draft`・`active`・`paused`・`matched`・`expired`・`cancelled`。
状態どうしの移り方を図にしたものを**状態遷移図**と呼びます。ws-3が扱う範囲を
移り方の一覧として書くと、次のとおりです(05 §6の遷移表から、期限切れ・成立を
除いた部分)。

```text
  作成(POST)─┬─ active指定 → active
              └─ draft指定  → draft

  active ──pause──→ paused ──resume──→ active
  active・paused ──PATCH(内容更新)──→ 同じstatusのまま version+1
  draft・active・paused ──DELETE──→ cancelled
  draft ──PATCH(status=active)──→ active(後半で説明)
  draft ──PATCH(再保存)──→ draftのまま version+1
```

遷移は「どこからどこへはOK・どこからどこへはNG」という表で仕様に決まっていて、
コードはその表のとおりに実装されています。たとえば draft のまま pause しようとすると
422 VALIDATION_ERROR(「その移り方は決まっていない」)。実装の中心は
`service.py:696` の `_transition` で、全操作がここに集約されています。

```python
    async def _transition(
        self, *, auth_provider, auth_subject, intent_id,
        allowed_from: tuple[str, ...],   # ここからは移してよい(遷移表の「元」)
        new_status: str,                 # 移す先
        version_delta: int,              # version をいくつ動かすか
        event_type: str | None,          # 発行する出来事(None=発行しない)
    ) -> IntentRow:
```

呼び出し側は3行で済みます(`service.py:657` から)。

```python
    async def pause(self, *, auth_provider, auth_subject, intent_id) -> IntentRow:
        return await self._transition(
            ..., allowed_from=("active",), new_status="paused",
            version_delta=0, event_type=None,  # 発行規定なし(pausedはLayer 1対象外)
        )

    async def resume(self, *, auth_provider, auth_subject, intent_id) -> IntentRow:
        return await self._transition(
            ..., allowed_from=("paused",), new_status="active",
            version_delta=1,  # 確定値15: キー衝突回避のため必ず+1
            event_type=EVENT_UPDATED,
        )
```

**version** は「このIntentの内容が何版か」を数える列です。内容を書き換えるたびに
1ずつ増えます。覚えるべき動きは4つだけです。

- 内容の更新(draft再保存・active更新)と resume は **+1**
- pause と DELETE は **不変**(内容が変わらないから)
- **draft→active化は、内容の実質変更がなければ据え置き**(後で詳しく)
- 作成は常に 1

resume が +1 する理由は、すぐ後の6.6で冪等キーとセットで説明します。

**DELETE が削除でない**点も読みどころです。`DELETE /v1/intents/{id}` を叩くと、
行は消えずに `status='cancelled'` へ書き換わります。なぜ物理削除しないのか。
理由は2つ、設計書 §2.8・§6-3 に判断とともに記録されています。1つは、仕様の遷移表が
DELETEをcancelledへの遷移として定義していること(値域にcancelledがある以上、
打ち切った行もstatusで表す設計)。もう1つは、将来の削除連鎖(このIntentを含む
候補の削除等)は別単位(M3)で実装されることです。「削除」ボタンの裏で何が起きるかは、システムによって
実はまちまちです。履歴を消すか、フラグで残すか。LATCHは後者を選びました。

**PATCH** は一番複雑な窓口です。同じ `PATCH /v1/intents/{id}` が、行の今の状態に
応じて3通りに分かれます(`service.py:476` の `_update` の分岐)。

1. **draft行へのPATCH** — 下書きの再保存。検証はraw_textの字数だけ。Eventなし。
   `structured_intent` を省略すれば既存の構造データを保持したまま raw_text だけ更新
2. **draft行 + `"status": "active"`** — **active化**。ここで初めて6.3の全関所
   (必須3・時刻・年齢・ジオコーディング)を通ります。不通なら422で**行はdraftのまま**
3. **active/paused行へのPATCH** — 内容の全置換。POSTと同一の全検証・version+1

2番のactive化で、version の据え置き判定が面白いので少し掘ります。下書きを
保存していたユーザーが、何も変えずに「これで確定」とボタンを押した場合、
内容は変わっていないのだから version を増やすのは不自然です。そこで
「実質変更がなければ据え置く」という規則になりました。ただし比較には注意が要ります。
draft行は**補完前**の値で保存されています(6.2の出力で time_end=None だったこと、
そしてactive化すると補完が入ることを思い出してください)。補完後の値を比較対象に
すると、中身が同じでも常に「変わった」と判定されてしまいます。そこで比較は
「リクエストをdraftと同じ規則で解釈した結果」と「現行のdraft行」で行われます。
`service.py:616` のコメントがそのままこの説明です。

```python
        # version判定はdraft表現での列比較(§2.7): 補完込みで比較すると
        # 同一内容でも常に相違となるため、リクエストをdraft用resolveした結果と比較
        draft_cols = replace(resolve_for_draft(inp), raw_text=raw_text)
        new_version = (
            row.version + 1 if differs_from_row(draft_cols, row) else row.version
        )
```

最後に、書き換えの競合について。2つのリクエストが同時に同じ行を書き換えようとしたら
どうなるでしょう。答えは「行に鍵をかけて直列にする」です。書き込み経路は
`SELECT ... FOR UPDATE`(読みながらその行に鍵をかけるSQL)で行を取得し、
UPDATEのWHERE句には「statusが今の値のままであること」を含めます
(`store.py:129` の `WHERE id = :intent_id AND status = :expected_status`)。
鍵をかけた側が先に書き、遅れた側は影響行数0を見て422で諦める。
「最後に残れるのはDBの制約だけ」(第4章4.6)と同じ発想が、更新の競合にも
使われています。

## 6.6 確定した事実は、保存と同じトランザクションで1行になる: MatchEvent outbox

この章の締めくくりに、一番大きな仕掛けを説明します。LATCHでは、Intentが
確定・更新・削除されるたびに、**その事実を知らせる1行**が別の表に書かれます。
マッチングはこの知らせを起点に動く設計だからです(02・06)。この「知らせ」を
MatchEventと呼びます。

M1時点では、知らせを受け取る相手(M2で作るPub/Sub配送とMatching Worker)が
まだ存在しません。では「発行」の実体は何か。答えが `events.py` です。全文が
50行に満たないファイルで、中心はこのSQL1本です(`backend/src/latch/intents/events.py:24`)。

```python
_INSERT_EVENT = text("""
    INSERT INTO match_events
        (event_type, source_intent_id, payload, status, created_at)
    VALUES
        (:event_type, :source_intent_id, CAST(:payload AS jsonb), 'pending',
         :created_at)
""")
```

つまりM1の「発行」は、`match_events` 表へのINSERT 1本です。statusは `'pending'`
(配送待ち)で置いておき、M2の配送の仕組みがこの行を後から拾いに行く。
先方の都合で配るのを少しだけ遅らせる郵便の「引き受けてから配達する」方式に似ています。
このパターンを **outbox(アウトボックス=送信箱)** と呼びます。名前の「送信箱」は、
溜めたものを後で配達するための箱、という意味です。

なぜ即配達でなく送信箱なのか。最大の理由は**原子性**です。複数の書き込みを
「全て成功するか、1つも実行しなかったことになるか」のどちらかにまとめる性質を
原子性と呼びます。そして、それを実現する仕組みが **トランザクション** です。
銀行振込が定番の例えです。引き落としと入金、この2つの書き込みの間でシステムが
落ちると大変なので、2つを1つのトランザクションにして、どちらかだけが起きる状態を
構造的に防ぎます。

Intent保存とEvent発行は、まさに振込と入金の関係です。「保存されたのにEventがない」
というパターンでは、マッチングが始まらないIntentが生まれます。「Eventだけあって
Intentがない」場合は、Workerが存在しない行を引きに行きます。`create` のコード
(`service.py:378`)は、この2本のINSERTを
**同じ `async with self._uow() as conn:` の中**で呼んでいます。

```python
            async with self._uow() as conn:
                intent_id = await self._store.insert(
                    conn, cols, user_id=user.id, status="active", now=now
                )
                await insert_match_event(
                    conn,
                    event_type=EVENT_CREATED,
                    intent_id=intent_id,
                    version=cols.version,
                    now=now,
                )
```

`uow` は Unit of Work(作業単位)の略で、`engine.begin`(トランザクションを開く)を
束ねた呼び出し可能な部品です。この `with` ブロックを抜けるとき、2本のINSERTが
まとめてコミットされる。片方だけ成功する経路が、はじめから存在しません。
6.2の実行例で「同一トランザクション内のEvent INSERT: 1本」と表示していました。
あれは、スタブのuowが渡してきたのと同じconnに対して、EventのINSERTが走ったことの
記録です。

いつ、どんな種類のEventが発行されるかは、設計書§2.2の表にまとまっています。
操作とEventの対応は次のとおりです。

| 操作 | Event | version |
|---|---|---|
| POST active作成 | created | 1 |
| POST draft作成 | (なし) | 1 |
| PATCH draft再保存 | (なし) | +1 |
| PATCH draft→active | **created**(初回投入は作成種) | 据え置き or +1 |
| PATCH active/paused内容更新 | updated | +1 |
| pause | (なし) | 不変 |
| resume | updated | **+1** |
| DELETE | deleted | 不変 |

draftがEventを発行しないのは当然と言えば当然です。中身が確定していない下書きを
マッチングの対象にするわけにはいきません。一方でdraft→active化がcreated種なのは
少々意外に見えるかもしれません。「更新では?」と思うかもしれませんが、
この行にはまだ1度もcreatedが出ていないので、初めての知らせは「作成」である
方が自然です(06 §9-0)。

`resume` が必ず version+1 する理由も、この表の向こう側にあります。match_events
表には `UNIQUE(event_type, source_intent_id, payload内のversion)` という
**式UNIQUE索引**が付いています(alembic 0001から)。同じ出来事を同じ版に対して
2回書けない、というDBレベルの約束です。この種の「何度実行しても結果が同じに
なる」性質を**冪等性**(べきとうせい)と呼び、それを実現する鍵を**冪等キー**と
呼びます。さて pause→resume を繰り返すとしましょう。resume のEventは
updated・version不変だと決めていたらどうなるか。たとえばversion=5のIntentで
2回目のresumeをすると、(updated, このIntent, version=5) を2行目として書こうとした
時点でUNIQUE違反になります。だから resume は
必ず version を +1 して、毎回別のキーを作る——設計書が「確定値15」として
記録している根拠です。

created/updated/deleted の3種が今回実装されたものです。定数は `events.py:18` に
並んでいます(期限切れexpired・指定時刻scheduledは、発行する側がまだ存在しないため
定数だけ用意されています)。

## 6.7 緑でも本番は落ちる: CAST 1個が503を作った話

この単位には、テスト設計について考える材料になる実話が記録されています。
`ws-3-report.md` §7 から辿れます。話の順に追いましょう。

スーパーバイザーの検証で `make test-ci`(実DB・実サーバー)を走らせたところ、
保存APIの正常系が**全滅**しました。POST /v1/intents がすべて
503 DEPENDENCY_UNAVAILABLE を返したのです。ところがapiのログには原因例外の
情報がなく、切り分けできない。unit テスト(当時416件)は全部緑でした。

原因はSQLの1箇所でした。地名→座標の結果を保存する列 `geo_center` はPostGISの
ポイント型なので、INSERTは「経度・緯度の数値からポイントを作る」式で書かれて
いました。数値の型を合わせるために PostgreSQL の **CAST**(キャスト=型変換)が
必要で、PostgreSQLの流儀では `:geo_lon::float8` と書けます(`::` がCAST)。
ところが SQLAlchemy の `text()` は `:名前` を「ここに値を埋め込む場所
(プレースホルダー)」として認識しますが、`:geo_lon::float8` の2つ目の `:` を
プレースホルダーの区切りと認識できず、`::float8` ごとリテラル文字列として
PostgreSQLへ送っていました。結果、INSERTが構文エラー。それを `except Exception`
が503へ包んだ、という次第です。

unit テストのスタブは「呼ばれたか・何が渡されたか」を記録するだけで、
**SQLとして正しいか解釈しない**ので、この欠陥を検査できませんでした
(第4章4.7の構成そのものが、ここの見えない壁になります)。

修正は2つ行われました(`eeb5263`)。1つはSQLをPostgreSQLの別のCAST構文
`CAST(:geo_lon AS float8)` へ書き換えること(こちらは `:` がパラメータから
離れた位置にあるため、正しく認識される)。もう1つは運用面で、503へ包むときにログへ
**例外のクラス名だけ**残すこと。原因を秘匿する規律(08 §2.4。ログにユーザーの
文章や地名を出さない)は守ったまま、「どの種類の失敗か」だけは追えるようにしました。
`service.py:178` の `_wrap_unexpected` がそれで、1行で書かれています。

```python
    logger.warning("intents.unexpected class=%s", type(exc).__name__)
```

再発防止の試験も作られました。`test_store_sql.py` は、INSERT/UPDATEのSQLを
本物のPostgreSQL用コンパイラで文字列に変換し、「変換結果に `:名前` が
残っていないこと」を検査します。パラメータの集合を調べるだけでは
「同名パラメータの別箇所認識」を見逃すため、文字列そのものを検査する——
今回の欠陥の本質を突いた作りです。

usersの `classify_integrity_error` 修正(第4章4.6で読んだもの。実DBでは例外が別の形に
包まれて届くのをunitの合成例外は見逃した)と、その前のM0認証の例、そして今回。
**unitが緑でも、本物の部品の組み合わせでは違う挙動がありうる**という教訓の
実例が、これで3つになりました。3つの共通点は「スタブは呼び出しを検証できるが、
相手の内部の挙動までは検証できない」です。だからこのプロジェクトは、部品単体の
unit(420件・3秒)と組み合わせのintegration(82件・実DB)の2層を持ち続けます。
どちらかだけで済ませたら、3度とも本番に近い側でしか拾えない欠陥を見逃して
いたことになります。

## 6.8 自分で確かめる

1. 6.2のスクリプトを動かし、`INP` の `"primary": "drinking"` を `"meal"` に
   変えて再実行する。`alcohol_involved` の確定がどう変わるかを予測してから
   確かめる
2. 6.4のスクリプトの `birth_date` を `date(2006, 9, 27)`(2026-09-27時点で
   ちょうど20歳の誕生日)に変える。受理されるはずです。さらに1日前
   (`date(2006, 9, 26)`)はどうなるかも予測しておく(第4章4.4のタプル比較が
   ここでも効いています)
3. `uv run pytest tests/unit/intents/test_intents_service.py -v` を実行し、
   試験名を縦に読む。6.5の遷移表と6.6のEvent表について、それぞれの行に対応する
   試験を見つけてノートに書き分ける
4. `rg -n "allowed_from" backend/src/latch/intents/service.py` で遷移の許可表が
   コードのどこにあるか確認し、`matched` 行へのDELETEがなぜ422なのか遷移表と
   照らして説明する
5. `git log --oneline -- backend/src/latch/intents/events.py` でこの小さなファイルの
   生い立ちを見る。`git show <ハッシュ>` で中身を読むとTDDの順(試験が先)が
   分かる

## 6.9 この章の再統合

- 保存の入口は draft(形式だけ検査して仮置き)と active(全検証を通して確定)の
  2つ。違いは検証の適用範囲に現れ、同じ入力がdraftで受理されて
  activeで422になるのもそのため
- activeの検証は 形式→必須3→時刻→年齢→ジオコーディング の順。安い検証から
  高い検証へ流れる。時刻の基準はClock、年齢はusersのage_yearsの再利用、
  地名はProtocol越しのジオコーディング
- 補完(終了時刻・期限・半径・人数)はcompletion.pyの単一規則の消費として、
  alcohol_involvedはカテゴリが飲酒ならサーバ側でtrueに確定。確定された値が
  年齢検証の発火条件になる
- 行は状態遷移図に沿って生きる。DELETEは行を消さずcancelledへ。versionは
  変更で+1・pauseとDELETEは不変・draft→activeは実質変更なしで据え置き
  (比較は補完前のdraft表現で)
- 確定の事実はmatch_eventsへのINSERTとして、保存と同じトランザクションで
  1行になる(outbox)。配送はM2。UNIQUE式索引の冪等キーが同じ出来事の二重記録を
  DBレベルで防ぎ、resumeのversion+1はそのキー衝突を避けるため
- CASTの書き方1個が実DBだけで503を作った。unitの緑は「呼び出しの正しさ」までで、
  相手の内部挙動までは保証しない——3度目の実例として、2層のテスト構成の意味を
  裏付けている

第4章で経路の型を、第5章で信頼できない入力への関所を学び、この章でその先の
保存・状態・記録を見ました。教材の次の章は、ws-4(レート制限)とws-5(フロント
エンド)の実装とともに追加されます。outboxのpending行が消費されるのは、その先の
M2(Workerと配送)です。そのとき読むことになる行を、あなたはもう知っています。

## 6.10 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| 下書き / 確定(draft / active) | 形式だけ検査して置く状態 / 全検証を通してマッチング対象になる状態 |
| 検証の切替 | 同じ契約で、検証の適用範囲をactive/draftで切り替えること |
| 正転ジオコーディング | 地名→座標の変換(geofeatures表への照合) |
| 状態遷移図 | 行がどんなstatus間をどう移れるかの地図 |
| version | Intentの内容の版。書き換えで+1 |
| 条件付きUPDATE | WHEREに現statusを含め、競合を影響行数0で検出する書き方 |
| FOR UPDATE | 読みながら行に鍵をかけるSQL。書き込みの直列化 |
| MatchEvent | Intentの確定・更新・削除といった「出来事」を知らせる行 |
| outbox | 配送を後で受け持つ仕組みに備え、まず自分のDBに記録するパターン |
| 原子性 | 複数の書き込みが「全て成功するか、1つも実行しない」に決まる性質 |
| トランザクション | 原子性を実現する仕組み。複数の書き込みを全成功か全なしにまとめる |
| 冪等性 / 冪等キー | 何度実行しても同じ結果になる性質 / それを保証する一意の鍵 |
| CAST | SQLでの型変換(`x::float8` または `CAST(x AS float8)`) |
| uow(Unit of Work) | トランザクションの開始を束ねた部品(engine.beginの包み) |

## 6.11 確認問題

1. 必須3の1つが欠けたリクエストが `status: "draft"` で201になり
   `status: "active"` で422になる理由を、「検証の切替」という言葉を使って
   説明してください
2. 検証の5つの関所を順に挙げ、この順になっている理由を「検証のコスト」に
   結び付けて説明してください
3. カテゴリが `drinking` のリクエストで `alcohol_involved: false` が保存前に
   どうなるか、そしてそれが年齢検証とどう噛み合うかを説明してください
4. draft→active化で version が据え置かれる条件を述べ、比較を「補完前の
   draft表現」で行う理由を説明してください
5. Event発行を保存と**別の**トランザクションで行った場合に残る問題を
   2つ挙げてください(片方だけが起きる状態が2種類あります)
6. resume が version を必ず +1 する理由を、冪等キーのUNIQUE索引と結び付けて
   説明してください
7. 今回の503欠陥はなぜunit テストでは見つからなかったのですか。
   第4章4.6のusersの例と共通する点を「スタブに検証できるもの・できないもの」
   の観点で答えてください
