# 読む順路: コードを「1本の流れ」で追う

- 種別: 解説(コードリーディングの案内)
- 前提: `00-environment.md` 読了・`labs/lab1-observe.md` で起動を体験済み推奨
- 次に読むもの: `concepts/clock-and-testing.md`

## 大原則: ファイル順に読まない

コードは辞書ではなく物語です。ディレクトリの上から読むと、関連のない知識を大量に
覚えようとして脳が拒否します。熟練者はこう読みます:

1. **入口(entry point)から入る** — 「リクエストがどこから来て、どこで処理されるか」
2. **1つのリクエストを端から端まで追う**(縦断トレース) — 1回のトレースで、ルーティング・
   エラー処理・データアクセスの約束事を一度ずつ学べる
3. **テストを先に読む** — テストは「このコードが何をすべきか」の仕様書。実装より読みやすい
4. **「なぜ」は設計記録に聞く** — `docs/plans/` に判断の根拠が残っている。git履歴(`git log -p`)も有効

エディタの「定義へ移動」(VS CodeならF12)と「参照を探す」(Shift+F12)を積極的に使ってください。

## トレース1: `curl /health` が返るまで(約30分)

「ヘルスチェック」= サーバーが生きているか確かめる最も単純なリクエストを追います。
小さいですが、**容器(docker)→サーバー(uvicorn)→アプリ(FastAPI)→部品(Clock)** という
全体の流れがそのまま縮図になっています。

### ステップ0: 外側から観察する

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","server_time":"2026-09-27T07:00:00.123456+00:00"}
```

この応答がどこで作られるかを、逆向きではなく**配管の上流から**追います。

### ステップ1: 容器 — compose.yaml の api サービス

`compose.yaml` を開き、`api:` の項目を見てください。

- `build: backend/` — apiコンテナは `backend/` のDockerfileから作られる
- `command: uvicorn latch.main:app --host 0.0.0.0 --port 8000` — コンテナ内で
  **uvicorn が `latch.main` というモジュールの `app` という変数**をサーブする
- `ports: "127.0.0.1:8000:8000"` — コンテナの8000番をホスト(あなたのPC)の8000番に中継

ここで学べること: 「curlで叩いた先には、実は容器の中のuvicornがいる」

### ステップ2: サーバーが掴む入口 — backend/src/latch/main.py の末尾

`main.py` の最終行(105〜106行目付近):

```python
# composeのapiサービスが参照するエントリポイント(uvicorn latch.main:app)
app = create_app()
```

uvicorn はこの `app` を見つけて起動します。**アプリの実体は `create_app()` という
「組み立て工場」の戻り値**です。なぜ直接組み立てず工場関数にするのか — それは
テストで中身(部品)を差し替えられるようにするためです(トレースの最後で効いてきます)。

### ステップ3: 工場の中身 — create_app()(main.py 57行目〜)

```python
def create_app(
    clock: Clock | None = None,
    settings: Settings | None = None,
    auth_service=None,
) -> FastAPI:
    app = FastAPI(title="LATCH API", lifespan=_lifespan)
    app.state.clock = clock if clock is not None else SystemClock()
    ...
```

読みどころ:

- `clock: Clock | None = None` — **時計を外から渡せる**。渡されなければ `SystemClock()`
  (実時間を返す普通の時計)。これが「依存の注入(DI)」の実例
- `app.state.clock = ...` — 渡された時計をアプリの共有置き場(`app.state`)に載せる

### ステップ4: URLと関数の結びつき — /health ハンドラ(main.py 68〜72行目)

```python
@app.get("/health")
async def health(
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, str]:
    return {"status": "ok", "server_time": clock.now().isoformat()}
```

- `@app.get("/health")` — 「GET /health が来たらこの関数を呼ぶ」という登録(デコレータ)
- `Depends(get_clock)` — FastAPIの依存解決。呼ばれるたびに `get_clock()` の結果を
  `clock` 引数に入れてくれる。`get_clock` の中身は `core/deps.py` にあり、たった1行:

```python
def get_clock(request: Request) -> Clock:
    return request.app.state.clock
```

つまり「アプリの共有置き場から時計を取ってくる」だけ。この1段かませることで、
テスト時に「別の時計」を差し込める道が保証されます。
- 返した dict は自動的にJSONに変換されて応答になります

### ステップ5: 時計の本体 — backend/src/latch/core/clock.py

`clock.now()` の中身(33行目):

```python
class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)
```

プロジェクト全体で「今何時?」と聞いてよいのは基本的にこの1行だけ、という規律があります
(詳細は `concepts/clock-and-testing.md`)。

### ステップ6: テストで確かめる — backend/tests/unit/test_app_health.py

実装を読んだら、次は**その試験**を読みます。20行目から:

```python
async def test_health_returns_ok_and_clock_time(app, fake_clock):
    body = await _get_body(app)
    assert body["status"] == "ok"
    assert body["server_time"] == fake_clock.now().isoformat()
```

- `app` と `fake_clock` は `tests/conftest.py` が用意する「共通の道具(フィクスチャ)」:

```python
@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))

@pytest.fixture
def app(fake_clock):
    return create_app(clock=fake_clock)   # ← 工場に「偽の時計」を渡している!
```

- テストは本物のサーバーを起動せず、アプリを直接呼び出します(`httpx.ASGITransport`)。
  だから約2秒で270試験回せます
- ここまでの全体像: **本番では SystemClock、試験では FakeClock に差し替えて同じ経路を検証する**

### 自分で確かめる

```bash
docker compose logs api --tail 5        # curl した瞬間にアクセスログが増える
make test                               # 全部のunit試験が走る
cd backend && uv run pytest tests/unit/test_app_health.py -v
```

## 今後追加予定のトレース

実装の進行に合わせて増えていきます(未着手):

- トレース2: 認証 — IdPトークン発行→`/v1/auth/token`交換→refresh回転→logout失効
- トレース3: ジオコーディング — 「天文館」→座標→「鹿児島市泉町」のSQL
- トレース4: DBマイグレーション — 0001/0002が適用される仕組み
- トレース5: テスト1件の一生 — pytestが収集から実行まで
