# 第2章 コードの読み方: /health 一本を端から端まで追う

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第1章(特にHTTP・FastAPI・uvicorn・Dockerの節)
- この章を読み終えるとできるようになること:
  - 「どこから読み始めるか」で迷わなくなる(ファイル順でなく流れで読む理由が説明できる)
  - `curl /health` の応答が返るまでの経路を、ファイルと行を指しながら説明できる
  - デコレータ、依存性注入(DI)、フィクスチャという言葉の実体を掴む
- 次に読むもの: `concepts/clock-and-testing.md`(この章で「おまじない」だった部分の理屈を掘ります)

## 2.1 なぜ「上から順に読む」は失敗するか

初心者の多くは、コードを本のように読もうとします。ディレクトリの上から、ファイルを1つずつ。
しかしコードは本と違って**ページ順がありません**。`main.py` の1行目は `import logging` から始まり、
実行の本当の出発点はファイルの一番下にあります。ファイル名の五十音順と、処理の順番の間には
何の関係もないのです。

上から読もうとすると、関連のない知識をどんどん頭に積むことになります。30個目のファイルを
開くころには5個目の内容を忘れています。これは記憶力の問題ではなく、読み方の問題です。

熟練者が代わりにやっているのは、次の3つです。

1. **入口から入る**: 「リクエストはどこから来て、どこで処理されるか」の入口(mainやルート定義)から読む
2. **1つの流れを最後まで追う**: 関係するファイルだけを、呼ばれる順に読む。1本読めば、命名規則・
   エラー処理・データの流れという「そのプロジェクトの型」が一度に分かる
3. **テストと履歴で補完する**: 動きが分からない部品は、そのテスト(=こう動くはず、という説明書)を読む。
   意図が分からない変更は、gitのコミットメッセージと設計メモに理由が書いてある

この章では、この3番目までを、一番小さい流れで実演します。

## 2.2 読むための3つの道具

**定義へ移動と参照を探す**(VS CodeならF12 / Shift+F12)。「この関数の中身は?」はF12で一発で飛べます。
「この関数を誰が使っている?」はShift+F12。マウスでスクロールして探す必要はありません。

**検索(ripgrep)**。コマンドラインからは `rg` が使えます。速くて賢い検索コマンドで、
このプロジェクトのCLAUDE.mdでも `rg` を使えと指定されているほどです。

```bash
rg -n "health" backend/src          # healthという語が現れる行をファイル名+行番号つきで表示
```

**git履歴**。行単位で「いつ・なぜ」を調べられます。

```bash
git log --oneline -- backend/src/latch/core/clock.py   # このファイルの変更履歴だけを一覧
git log -p backend/src/latch/core/clock.py             # 差分つきで全部読む(長い)
git blame backend/src/latch/main.py                    #各行がどのコミット由来か
```

## 2.3 トレース1: `curl /health` が返るまで

題材は、第1章でも叩いたこの1行です。

```bash
curl http://127.0.0.1:8000/health
```

**{"status":"ok","server_time":"..."}** というJSONが返るまでの道のりを、外側の容器から
コアの部品へと、7ステップで追います。各ステップに「ここで初めて出てくる概念」の説明を挟みました。
手を動かしながら読むなら、エディタで各ファイルを開きながら進めてください。

### ステップ1: 容器の定義 — compose.yaml の api サービス

リポジトリ直下の `compose.yaml` を開き、`services:` の中の `api:` を見つけてください。

```yaml
  api:
    build: backend/
    command: uvicorn latch.main:app --host 0.0.0.0 --port 8000
    ports:
      - "127.0.0.1:8000:8000"
```

読み方を1行ずつ分解します。

- `build: backend/` — この容器は `backend/` ディレクトリにある `Dockerfile` の定義から作る
- `command: uvicorn latch.main:app` — 容器の中でこのコマンドを実行する。
  `latch.main` は「`latch` パッケージの `main.py`」、`:` の後の `app` は「その中の `app` という変数」。
  つまり「`backend/src/latch/main.py` の中にある `app` をサーブせよ」という意味に分解できます
- `ports: "127.0.0.1:8000:8000"` — 左があなたのPC側、右が容器の中側。**PCの8000番室と容器の8000番室を
  内線でつなぐ**設定です。だから `curl 127.0.0.1:8000` が容器の中のuvicornに届きます

ここまでで分かったこと: **リクエストは、あなたのPCの8000番ポート → api容器 → その中のuvicorn** という
経路で入っていく。

### ステップ2: サーブされる変数 — main.py の一番下

`backend/src/latch/main.py` を開いて、**一番下の行**を見てください。

```python
app = create_app()
```

たった1行ですが、2つの教えてくれることがあります。

1. uvicornが掴む `app` は、`create_app()` という**関数を呼んだ結果**です。main.pyの読み出発点は
   ファイルの先頭ではなく、この最終行だと分かります(Pythonは上から順に実行されるので、
   実は `create_app` の定義を全部読み込んだ上で、最後にこの1行が走ります)
2. Pythonの **import**(インポート)の話もここで出てきます。ファイル上部の
   `from latch.core.clock import Clock, SystemClock` のような行は「他のファイルから部品を持ってくる」
   宣言です。`latch.core.clock` は `backend/src/latch/core/clock.py` のこと。
   ドットとスラッシュの対応(`latch.core.clock` ↔ `latch/core/clock.py`)を覚えると、
   どんなimport文でも実ファイルに翻訳できます

### ステップ3: 組み立て工場 create_app

`main.py` の中段、`def create_app(...)` から始まる関数が本体です。最初の数行だけ読みます。

```python
def create_app(
    clock: Clock | None = None,
    settings: Settings | None = None,
    auth_service=None,
    users_service=None,
    intent_parse_service=None,
) -> FastAPI:
    app = FastAPI(title="LATCH API", lifespan=_lifespan)
    app.state.clock = clock if clock is not None else SystemClock()
```

`clock: Clock | None = None` は「時計(`clock`)を引数として渡せる。渡されなかったら `None`」という
意味の型注釈つき引数です。そして次の行で、`None` のときは `SystemClock()`(実時間を返す普通の時計)を
作って代わりに入れています。引数はこのあと `auth_service`・`users_service`・`intent_parse_service`
と続きますが、どれも同じ形の「差し替え用の受け口」です(第4章・第5章で実際に使います)。

ここで疑問を持つべきです。**なぜ `app = FastAPI()` して `clock` を直接作らず、わざわざ関数にして
外から渡せるようにしているのか?** 答えは「差し替え可能にするため」です。本番では本物の時計、
テストでは偽の時計(FakeClock)を渡して、同じ組み立て処理を使い回します。このパターンを
**ファクトリ関数**(工場関数)と呼びます。「作る処理を関数に閉じ、材料は外から渡す」だけの話です。

なお `app.state.clock = ...` の `app.state` は、アプリ全体で共有できる**置き場**です。
FastAPIの決まりごととして「何でも置ける棚」と覚えてください。ここに置いたものは、後述の
`get_clock` 経由でどの処理からでも取り出せます。

### ステップ4: 窓口の登録 — /health ハンドラ

少し下に進むと、窓口の定義があります。

```python
    @app.get("/health")
    async def health(
        clock: Annotated[Clock, Depends(get_clock)],
    ) -> dict[str, str]:
        return {"status": "ok", "server_time": clock.now().isoformat()}
```

上から順に、3つの新しい概念が出ています。

**デコレータ**(`@app.get("/health")` の行)。関数定義の真上に `@` つきで書く飾りで、
「この関数に、何か機能を付け足す」記法です。ここでは「**GET /health という窓口が来たら、
下の関数を呼ぶ**」という登録を、FastAPIに対して行っています。`@` を剥がすと、`health` は
ただの関数です。デコレータはすべてこうして「関数を受け取り、少し加工して返す」仕組みです。

**async / await**(関数定義の `async def`)。「並行処理ができる関数」の印です。1件の処理を
待っている間に、他のリクエストの処理を進められるようにするための宣言で、中身の読み方自体は
普通の関数と同じです(LATCHのサーバーコードはほぼ `async def` です)。

**依存性注入(DI)**(`Annotated[Clock, Depends(get_clock)]` の部分)。小さく分解すると、
`Depends(get_clock)` は「この引数が欲しいときは、`get_clock` という関数を呼んだ結果を入れろ」
というFastAPIへの指定です。`Annotated[型, 指定]` はその指定を型とセットで書く現代の書き方、
とだけ覚えれば十分です。つまり**FastAPIが、関数を呼ぶたびに `get_clock()` を実行して、
その戻り値を `clock` 引数に入れてくれる**。`health` の中身は時計を気にせず `clock.now()` と
書けるわけです。この「使うものを自分で作らず、外から渡してもらう」仕組み全体を依存性注入(DI)と
呼びます。第3章で、なぜこれがテストを楽にするかを掘ります。

### ステップ5: 棚から取り出す — core/deps.py

では `get_clock` の中身は何か。`backend/src/latch/core/deps.py` は9行しかないファイルです。

```python
def get_clock(request: Request) -> Clock:
    return request.app.state.clock
```

「このリクエストが属するアプリ(`request.app`)の共有置き場(`state`)から、`clock` を返す」。
ステップ3で `app.state.clock = ...` として棚に置いたものを、ここで取り出しています。
入れる場所と取り出す場所が1対1に対応しているのを確認してください。この1段かまえただけの
間接化が、「テストで別の時計に差し替える」ことを可能にする重要な工夫です。

### ステップ6: 時計の本体 — core/clock.py(ここでは軽く)

`clock.now()` の中身、`SystemClock` は `backend/src/latch/core/clock.py` にあります。

```python
class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)
```

クラス(`class`)という言葉が初出なら、第3章でじっくり扱います。ここでは「`SystemClock` は
『今の日時を聞かれたら答える』部品で、`now()` と呼ぶと現在時刻を返す」とだけ読んでください。
`isoformat()` は日時を `"2026-09-27T09:41:23+00:00"` のような文字列にする標準的な書式化です。
末尾の `+00:00` は「この時刻はUTC(世界標準時)ですよ」という印(タイムゾーン付き)です。

### ステップ7: 期待を書いた紙 — テストを読む

実装を読み終えたら、そのテストを読みます。`backend/tests/unit/test_app_health.py` の
冒頭の試験です。

```python
async def test_health_returns_ok_and_clock_time(app, fake_clock):
    body = await _get_body(app)
    assert body["status"] == "ok"
    assert body["server_time"] == fake_clock.now().isoformat()
```

`assert`(アサート)は「これが成り立っていれば合格、違ったら即座に赤」という宣言です。
この試験は「応答の `server_time` は、**注入した偽の時計**が返す時刻と一致するはず」と
書いています。つまり「/healthの時刻はClock経由で取られている」ことを、この1行が証明しています。

`app` と `fake_clock` はどこから来るのか。同じ `tests/` の `conftest.py` に定義があります。

```python
@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))

@pytest.fixture
def app(fake_clock):
    return create_app(clock=fake_clock)
```

`@pytest.fixture` は「**テスト関数に、材料を渡してくれる準備用の関数**」を作る飾りです。
テスト関数の引数に `fake_clock` と書くだけで、pytestがこの準備関数を呼び、戻り値を渡してくれます。
`app` の準備関数は、その `fake_clock` をさらに `create_app` へ渡しています。
ステップ3で見た「外から渡せる設計」が、ここで効いているわけです。

もう一つ、`_get_body` の中にあるこの行に注目してください。

```python
transport = ASGITransport(app=app)
```

このテストは**本物のサーバーを起動していません**。uvicornもポートも使わず、
アプリの関数を直接呼び出してHTTPのやり取りを模擬しています。だから575件の試験が3秒台で
終わるのです。テスト用の窓口を用意し直す必要がなく、本番と同じアプリを検査できる点も利点です。

### 全体を一枚にまとめると

```text
あなたのPC(127.0.0.1:8000)
  └→ api容器(compose.yaml の ports で中継)
       └→ uvicorn(latch.main:app をサーブ)
            └→ main.py の app = create_app()
                 ├ 組み立て時: app.state.clock = SystemClock()(またはFakeClock)
                 └ リクエスト時: GET /health
                      └→ health() ← Depends(get_clock) ← app.state.clock
                           └→ clock.now().isoformat()
                                └→ {"status":"ok","server_time":"..."}
```

本番と試験で**変わるのは「棚に置く時計」だけ**で、経路はすべて同じ。この性質が、
第3章の主題です。

## 2.4 自分で確かめる

読むだけで終わらないよう、小さな課題を出します(Lab 1・2と重なる部分は復習です)。

1. `make ps` で api が `(healthy)` であることを確認し、`curl 127.0.0.1:8000/health` を2回叩いて
   `server_time` が進むことを観察する
2. `docker compose logs api --tail 5` で、curl のたびにアクセスログが1行増えることを確認する
3. `rg -n "Depends" backend/src` を実行し、DIが使われている場所を数える(認証のファイルにもあります)
4. `git log --oneline -- backend/src/latch/main.py` で、このファイルが雛形→認証→users・
   parse の統合とどう変化したかを一覧する。気になるコミットを `git show <ハッシュ>` で読む

## 2.5 次のトレース(継続追加)

実装の進行に合わせて追加されます。**書き込みの経路(POST /v1/users)とparseの経路は、
第4章・第5章として、レート制限は第7章として、フロントエンドとAPIの合流は第8章として
実現しました**(concepts/ 配下。この章のトレース1と同じ読み方です)。

- トレース2 認証: テスト用トークン発行 → `/v1/auth/token` 交換 → refresh回転 → logout失効
- トレース3 ジオコーディング: 「天文館」→座標→「鹿児島市泉町」のSQLの中身
- トレース4 マイグレーション: 0001/0002がどう適用されるか
- トレース5 テスト1件の一生: pytestがファイルを集めて実行するまで

## 2.6 確認問題

1. 「ファイルの上から順に読む」のが失敗する理由を、自分の言葉で2つ挙げてください
2. `uvicorn latch.main:app` を完全に日本語訳してください(ヒント: ステップ1)
3. `@app.get("/health")` は何をしている飾りですか。デコレータという言葉を使って説明してください
4. `get_clock` を経由せず、`health` 関数の中で直接 `SystemClock()` を作ると、何が起きるでしょうか
5. テストが uvicorn を起動しない理由と、それによって得られる2つの利益を述べてください
