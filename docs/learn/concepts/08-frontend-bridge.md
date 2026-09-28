# 第8章 フロントエンドとbackendの合流: ここまでのAPIが画面の言葉になる

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第1章(HTTP・Docker)・第5章(parse・422/503の切替)・第6章(保存の経路)・
  第7章(レート制限)。第4章(users)は「保存には登録済みユーザーが要る」の根拠です
- この章を読み終えるとできるようになること:
  - `frontend/` と `prototype/` が別々に存在する理由を、実装基準という観点から説明できる
  - 「同じPCの別ポートなのにfetchが失敗する」現象を、オリジンという言葉で説明し、
    vite proxyがどう解くか述べられる
  - JavaScriptの import・async/await・fetch を、Pythonとの対応で読める
  - トークンの置き場所(access と refresh)の使い分けと、401で自動で回復する流れを追える
  - サーバーから返るエラーenvelopeが「どの行に・何を表示するか」へ振り分けられる設計を説明できる
  - 有効期限の選択肢を計算する箇所がAPIの1エンドポイントに集約されている理由(単一実装原則)を語れる
  - 81件のフロント試験がブラウザなしで走る理由を、コードの構造から説明できる
- 対応コード: `frontend/src/`(この章の対象は10ファイル。試験は `frontend/tests/` の9ファイル)
- 設計の根拠: `docs/plans/M1/ws-5-design.md`(特に§2.1〜§2.9の判断記録)
- 次に読むもの: `labs/lab4-frontend.md`(この章で読んだ部品を、ターミナルとブラウザで動かします)

## 8.1 7つの窓口を叩く8つ目の住人: frontend/とprototype/を分けた理由

第7章の終わりで、429や422を「画面の言葉」でどう伝えるかが、フロントエンドの課題として
示されました。この章はその答えを読む章です。

LATCHには、実はずっと前から「画面」がありました。`prototype/` がそれで、第1章の
リポジトリの地図にも「フロントエンドの試作」として載っています。見た目を確かめるための
**静的なモック(動かない見本)**で、APIを一切呼びません。テキストを入力しても、
条件リストは最初から書き込まれた固定文字列が表示されるだけです。

ws-5では、この試作を出発点にして、APIに接続する本実装を `frontend/` として新設しました。
そして `prototype/` は1行も変えずにそのまま温存しています。なぜ分けたのでしょう。
答えは `docs/plans/M1/ws-5-design.md` §2.1に、選択肢ごとの根拠つきで記録されています。
要点は2つです。

1つ目は、**prototypeは「実装基準」という役割を与えられている**からです。プロジェクトの
運用ルール(00 運用ルール6)は「フロントエンドの実装基準はprototype/側を正とする」と
定めています。ここへ本実装を直接書き込むと、「基準」の中身が「実装」に書き換わって
しまい、基準と実装の区別が消えます。教科書に例えるなら、模範解答を試験用紙に直接
書き込んでしまうようなものです。別々のファイルにしておけば、「画面はこうあるべき」の
参照物が、実装がどう変わっても残り続けます。

2つ目は、**コードの規模に対してフレームワークが不要**という判断です。このプロジェクトの
画面は単一で、ReactのようなUIフレームワークを導入してまで得るものがありません。
実際 `frontend/` の依存宣言(`package.json`)を見ると、画面を動かすための依存は
フォント4種・アイコン1種・Viteの6つだけで、試験用にvitestとhappy-domを足した
2つを含めても8つです。prototypeと依存の種類は変えていません。

```text
frontend/
├── index.html            画面の骨格(プロトタイプから持ち上げ+開発用トークンパネル等)
├── styles.css            見た目(プロトタイプの702行を引き継ぎ+NG行・催促表示を追記)
├── vite.config.mjs       開発サーバーの設定(APIへの転送はここに書く。8.2)
├── package.json          依存の宣言(backendのpyproject.tomlに相当)
└── src/
    ├── main.js           エントリ。DOM配線(どのボタンにどの部品を結ぶか)だけを担う
    ├── ui/chrome.js      画面装飾(テーマ切替・popover・トースト・確認モーダル)
    ├── api/session.js    トークンの保管と交換(8.4)
    ├── api/client.js     API呼び出しの共通部品(8.4・8.5)
    ├── intent/state.js   フォームの中身を表すデータ(8.7)
    ├── intent/parseFlow.js   parse送信の制御(8.5)
    ├── intent/conditions.js  条件リストの描画と行編集(8.7)
    ├── intent/format.js  構造化データ→表示文言・APIボディの純関数(8.7)
    ├── intent/expiry.js  期限選択肢の取得とselectの構成(8.6)
    └── intent/save.js    保存とエラー表示の振り分け(8.5)
```

**Vite(ヴァイト)**という道具が初めて出てきました。Viteはフロントエンドの開発サーバーと
ビルド(複数のファイルを配信に向いた形へまとめる作業)を担う道具で、`package.json` の
依存宣言から `npm install` というコマンドで導入します。npmはPythonのuvに相当する
「依存とその版の固定を管理する道具」で、`package-lock.json` がuv.lockに相当する
固定ファイルです。第1章1.5でuvについて学んだ「仮想環境とロックファイル」の話が、
フロントエンドの世界でもそのまま成り立っています。

backendとの共通点は2つあります。1つはここまでの話——**環境の再現を、宣言と固定
ファイルで行う**こと。もう1つは、テストの2層構造です(8.7で出ます)。

## 8.2 同じPCなのに届かない: オリジンとvite proxy

フロントエンドをAPIにつなげる、というのは要するに「ブラウザからHTTPのリクエストを
送る」ことです。curlがURLに向かってリクエストを送るように、ブラウザには **fetch**
(フェッチ=取ってくる)という機能が組み込まれています。しかし、この世界には
curlにはない作法が1つあります。

ブラウザは「自分が開いているページと、違う出身地のサーバーへのリクエスト」を
無原則には許しません。出身地のことを **オリジン**(origin=出身・原点)と呼び、
`スキーム+ホスト+ポート番号` の3つ組で決まります。`http://localhost:5173` と
`http://127.0.0.1:8000` は、あなたのPCの目から見れば同一のマシンですが、
ポート番号が違うため**別オリジン**です。ページを配信する側が、別オリジンへの
リクエストを許可するには、サーバー側に **CORS**(コルス、Cross-Origin Resource
Sharing=別オリジンからの資源共有)という許可設定を書いておく必要があります。

LATCHのbackendには、このCORS設定がありません。そこで選ばれたのが、CORSを足す
代わりに **vite proxy**(プロキシ=代理)で1本にまとめる方式です。
`frontend/vite.config.mjs` の中身はこれだけです。

```javascript
export default defineConfig({
  build: {
    outDir: "dist/client",
  },
  server: {
    host: "0.0.0.0",
    proxy: {
      "/v1": "http://127.0.0.1:8000",
    },
  },
});
```

`proxy: { "/v1": ... }` が本体です。vite devサーバー(ポート5173)は、`/v1` で
始まるパスへのリクエストを受け取ると、その内容を `http://127.0.0.1:8000` へ
**そのまま転送**します。転送結果もそのまま返す。だからブラウザから見れば、
APIも画面も `localhost:5173` に住んでいる(同一オリジン)ので、CORSの問題が
起きる前に消えます。

この選択には、設計書に明確な根拠が書いてあります(design §2.2)。CORSをbackendに
足す方式は、main.pyの変更と「どのオリジンを許すか」という管理が生まれます。
一方proxy方式なら、**backendとcomposeの双方を1行も変えない**。本単位でbackendに
加える変更は、8.6の期限APIの1つだけに絞れる——変更面を最小にする判断は、
第5章の「fail-closed」や第7章の「数える場所の住み分け」と同じく、設計書に
選択肢と根拠を書いて決める、このプロジェクトの型の1つです。

## 8.3 JavaScriptを読むための4つの約束事

これ以降はJavaScript(JS)のコードを読みます。Pythonしか読んでいない読者のために、
読むうえで必要な約束事だけを先に渡しておきます。JSの全体像を学ぶ章ではないので、
ここに挙げた4つを押さえれば、この章のコードは読めるようになっています。

1つ目は **import / export**。Pythonの `from clock import Clock` に対応する仕組みで、
ファイルの頭に `import { createClient } from "./api/client.js";` と書くと、
`api/client.js` から `createClient` という名前を持ってきます。export側に
`export const createClient = ...` と書いてある名前だけが持ち出せます。
パスはドットで始まる相対パスで、Pythonのドット記法(`latch.core.clock`)とは
違う書き方な点に注意です。

2つ目は **アロー関数**という関数の書き方。`const add = (a, b) => a + b;` は、
Pythonでいえば `def add(a, b): return a + b` にほぼ相当します。「材料を受け取り、
何かを返す」道具を変数に束ねる書き方で、LATCHのフロントコードはすべてこの形です。
`: Clock` のような型注釈はなく、返り値の形も宣言しません。

3つ目は **async / await**。第2章でPythonの同名の言葉を学びましたね。JSでも全く同じ
語彙で、`await` は「処理が終わるのを待つ」の印です。1点だけ違いがあり、
JSの非同期処理は **Promise**(プロミス=約束)という「まだ届いていない結果の受け取り
伝票」を基礎に動きます。`await fetch(...)` は「fetchが返す伝票の内容が届くまで待つ」
の意味です。

4つ目は **fetch**。ブラウザ版のcurlです。`fetch(path, { method: "POST", headers, body })`
でHTTPのリクエストを送り、応答オブジェクト(ステータスコードと本文を持つ)を返します。
本文をJSONとして読むには `await res.json()` と1段かませます。Pythonの
`response.json()` と同じ感覚です。

この4つが分かれば、backendの3層(ルータ→サービス→SQL)と同じ読み方で、フロントの
コードも追えます。entryの `main.js` が受付で、`api/client.js` が送信の共通部品、
`intent/` 配下の各モジュールが個々の処理を担います。

## 8.4 トークンはどこに置くか: sessionStorageとlocalStorageの使い分け

全APIが認証必須(05 §5)であることは、何度も出てきました。curlなら
`-H "Authorization: Bearer $ACCESS"` と書けば済みますが、画面から使う場合、
**トークンをどこに保管して、毎回どう付けるか**をフロントが設計しなければなりません。
docsではこの保管場所が未規定だったため、ws-5の設計書で決めています(design §2.3・告白3)。

保管場所には2種類の **Webストレージ**が使えます。どちらもブラウザが提供する
「キーと値のペアを文字列で保存する小さな保管庫」です。違いは寿命です。

| 名前 | 寿命 | 向いているもの |
|---|---|---|
| sessionStorage(セッションストレージ) | タブを閉じると消える | 期限の短いもの。有効期限1時間のaccess_token |
| localStorage(ローカルストレージ) | 消さない限り残る | 寿命の長いもの。有効期限30日のrefresh_token |

LATCHは **access_tokenをメモリ+sessionStorageに、refresh_tokenをlocalStorageに**
分けて置きます。`api/session.js` の中身を読んでみましょう。

```javascript
export const createSession = ({ storage } = {}) => {
  const sessionStore = storage?.session ?? sessionStorage;
  const localStore = storage?.local ?? localStorage;
  const memory = { accessToken: null };

  return {
    current: () => ({
      accessToken: memory.accessToken ?? sessionStore.getItem(ACCESS_KEY),
      refreshToken: localStore.getItem(REFRESH_KEY),
    }),
    save: ({ access_token, refresh_token }) => {
      if (access_token) {
        memory.accessToken = access_token;
        sessionStore.setItem(ACCESS_KEY, access_token);
      }
      if (refresh_token) localStore.setItem(REFRESH_KEY, refresh_token);
    },
    clear: () => {
      memory.accessToken = null;
      sessionStore.removeItem(ACCESS_KEY);
      localStore.removeItem(REFRESH_KEY);
    },
    hasTokens: () =>
      Boolean(memory.accessToken ?? sessionStore.getItem(ACCESS_KEY) ?? localStore.getItem(REFRESH_KEY)),
  };
};
```

読みどころは3つです。1つ目、`save` はaccess_tokenを2箇所(メモリとsessionStorage)に
置きます。メモリにあればsessionStorageを見に行かない。2つ目、`current` は
**「メモリが空なら、sessionStorageから読む」**というフォールバック(落ちる場所が
あるならそちらへ)です。タブ内の遷移ではメモリが使え、タブを閉じればaccess_tokenは
消えます。3つ目、`storage` を引数で受け替えられる作りは、第3章のClockと同じ
**差し替え用の受け口**です。試験では偽の保管庫を渡すことで、実際のブラウザなしで
`session.js` の挙動を検証しています。

では、期限切れのaccess_tokenでAPIを叩いたらどうなるでしょう。サーバーは
`401 UNAUTHENTICATED` を返します。そのときの回復手順が `api/client.js` の
`call()` に書かれています。

```javascript
    call: async (method, path, { body, signal } = {}) => {
      let res = await doFetch(fetcher, method, path, {
        body,
        signal,
        token: session.current().accessToken,
      });
      if (res.status === 401 && session.current().refreshToken) {
        const refreshed = await refreshOnce(session, fetcher);
        if (refreshed) {
          res = await doFetch(fetcher, method, path, {
            body,
            signal,
            token: session.current().accessToken,
          });
        } else {
          session.clear();
          onSessionExpired();
        }
      }
      ...
```

流れを言葉にすると、こうです。

1. まずaccess_tokenを付けてリクエストする
2. 401が返り、refresh_tokenを持っていれば、`POST /v1/auth/refresh` で新しい
   access_tokenを発行してもらう(refreshは回転式で、このときrefresh_token自体も
   新しくなる——第1章1.7の「失効リスト」とあわせて読むと理解が深まります)
3. 交換に成功したら、**元のリクエストをもう1回送り直す**。ユーザーは何も気づかない
4. 交換に失敗したら(refresh_tokenも無効)、トークンを全部捨てて、開発用トークン
   パネルへ戻す(`onSessionExpired`)

「自動で1回だけ救う。救えなければ素直に最初の画面へ戻す」——この回数を1回に
決めているのも設計判断で(design §2.3)、無限にリトライを続けると失敗が
失敗を呼ぶ状態になるからです。backend側の「再試行なし」(第5章)と同じ、
**断固として回数を決める**作法です。

なお、このトークンを画面で受け取る窓口が **開発用トークンパネル**です。本物の
IdP(Google/Apple)でのログインUIはまだ作られていません(M3-10以降)。代わりに、
backendの内部CLIで発行したIdPトークンを画面に貼り付けると、フロントが
`POST /v1/auth/token` を呼んでaccess_token・refresh_tokenを得て保管します。
この交換経路は本番のログインフローと同じものです。つまりパネルは「本番UIへの
差し替え」が可能な形で作られており、開発用の仮組みではなく、認証設計の一部です。
Lab 4で実際に使います。

## 8.5 エラーenvelopeは画面の言葉になる: codeで分岐する設計

第5章で「ステータスコードが、利用者の次の一手を決める」と書きました。422なら
手入力へ、503なら再試行へ。では、その「次の一手」は画面ではどうなりますか。
この節はその答えです。

まず、エラーの受け取り方。`client.js` の `call()` の末尾で、失敗応答は
**ApiError**という例外に変換されます。

```javascript
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        throw new ApiError(
          res.status,
          data?.error?.code ?? `HTTP_${res.status}`,
          data?.error?.message,
        );
      }
```

共通envelopeの `{error: {code, message, details}}`(第4章4.3)から、
**codeだけを確実な手がかりとして**取り出しています。仕様書の言葉で言えば
「クライアントはcodeで分岐しmessageは参考にしか使わない」(05 §5)です。
`message` は人間が読むための文章で、フロントが挙動を分岐させる根拠にはしません。
ここで1つだけ例外があります。後で見ます。

分岐の本体が `intent/save.js` の `errorPlacement` です。サーバーのcodeごとに、
**表示する場所と文章**を対応付けています。

```javascript
export const errorPlacement = (err) => {
  switch (err?.code) {
    case "GEOCODING_FAILED":
      return { target: "location", note: "場所が見つかりません。条件リストの場所を修正してください。" };
    case "UNDER_AGE":
      return { target: "global", note: "飲酒を含むIntentは20歳以上の方のみ作成できます。" };
    case "ACTIVE_INTENT_LIMIT":
      return {
        target: "global",
        note: "預けられるIntentはActive 5件までです。停止中・期限切れのIntentを確認してください。",
      };
    case "RATE_LIMITED":
      return { target: "global", note: "操作が集中しています。少し時間をおいてもう一度お試しください。" };
    ...
    case "VALIDATION_ERROR": {
      const message = err.message ?? "";
      if (message.startsWith("time.start") || message.startsWith("expires_at")) {
        return { target: "time", note: "時刻を修正してください(現在〜7日以内)。" };
      }
      return { target: "global", note: "入力内容を確認してください。" };
    }
```

表に整理すると、こうなります。

| サーバーのcode | 表示される場所 | 利用者の次の一手 |
|---|---|---|
| GEOCODING_FAILED | 場所行のエラー表示 | 条件リストで場所を修正する |
| VALIDATION_ERROR(時刻系) | 時間行のエラー表示 | 条件リストで時刻を修正する |
| UNDER_AGE・ACTIVE_INTENT_LIMIT・RATE_LIMITED | 画面全体へのメッセージ | 内容を読んで待つ・整理する |
| それ以外の未知のcode | 画面全体へのメッセージ | 再試行 |

場所行・時間行という「行単位」の表示は、第6章で読んだ検証(GEOCODING_FAILEDや
過去時刻)が、**修正すべき場所を1つに特定できる失敗**であることに由来します。
それ以外は、修正箇所が特定できないので画面全体へ出します。サーバー側の検証の
性質が、そのまま表示の設計に写っています。

なお、`message.startsWith("time.start")` の行が前述の例外です。時刻検証の
失敗には `GEOCODING_FAILED` のような専用codeがなく、VALIDATION_ERRORに含まれて
届くため、messageの先頭から「時刻の失敗か」を推定しています。未知の形式は
グローバル表示へ落ちる(=間違った行を赤くしない)ので、推定が外れても
利用者を誤導しません。この例外は設計書に明記してあります(design §2.9・
save.jsのコメント)。

parse側の分岐も見ておきます。`main.js` の `onError` が対応します。

```javascript
  onError: (err) => {
    state.parseStatus = "error";
    if (err?.code === "VALIDATION_ERROR") {
      // 構造化フォームへのフォールバック(確定値9): 全行が手動入力可能な空状態
      parseError.hidden = true;
      Object.assign(state, applyParseFallback(state));
      ...
      return;
    }
    if (err?.code === "RATE_LIMITED") {
      showParseError("操作が集中しています。…", false);
    } else if (err?.code === "LLM_UNAVAILABLE") {
      showParseError("ただいま条件を読み取れません。", true);
```

第5章の422/503の切替が、ここで「画面の言葉」になります。**422(構造化できなかった)
では、全行が手動入力可能な空の条件リストを表示する**——メッセージを添えるより先に、
入力の場そのものを切り替えます。手入力で構造化データを
完成できるので、利用者は行き止まりに立たされません。**503(LLMが落ちた)では、
「もう一度読み取る」ボタン付きのメッセージ**で、入力テキストを保持したまま
再試行できます。`RATE_LIMITED`(429)は自動再試行をせず待機を促します(第7章の
「1分待つ」仕様がそのまま画面になっている)。ステータスコードが「利用者の次の
一手」を決める、という第5章の主張が、この分岐で完結します。

もう1つ、parseの送信側にも工夫があります。`intent/parseFlow.js` は、テキストの
入力が止まってから1秒待って(この手の「最後の入力からしばらく待ってから実行する」
仕組みを **debounce**(デバウンス)と呼びます)parseを送ります。さらに、**直前に
送ったテキストと同じなら送り直さない**、送信中に新しい入力が来たら**古いリクエストを
取り消して(AbortController)最新を送る**、という3つの制御をしています。
なぜこうするか。第7章の「API 60req/分」と、LLM呼び出しのコスト(07 §1「1登録1回」)
が背景です。文字を1つ打つたびにparseを送っていたら、60件の上限はあっという間に
消費されます。制御の全容はコード(55行)が短いので、`frontend/src/intent/parseFlow.js`
を通しで読むと、試験(`tests/parseFlow.test.js`・131行)と対応が取れてよく分かります。

## 8.6 同じ計算を2箇所に書かない: expiry-optionsという1行の窓口

有効期限の選択肢には、3つの計算が紐づいています(03 §3)。

- 4つの選択肢の**絶対時刻**(今夜23:30=当日JST/明日12:00/明日23:30/3日後まで)
- **既定の選択**——「time.start+3時間に最も近い選択肢」を選ぶ
- 過ぎた選択肢の **disabled**(選べなくする)

この計算をフロントは必要とします。では、JavaScriptで書き直せばよいか。
ws-5の設計書はこの問いを「本単位最大の設計論点」(design §2.6)として扱い、
**NOと答えました**。理由は、第5章5.6の末尾で学んだ **completion.py** の
存在です。あの節で「補完はこの1ファイルが唯一の実装」と定めてあり、
消費者は「保存経路とUI」と書かれていました。フロントがJSで同じ計算を書けば、
**同じ規則が2つの言語に2重実装される**ことになります。片方だけが仕様変更に
追従する事故は、二重実装が存在する限り必ず起こります。

選ばれた解は、backendに軽量なAPIを1つ足すことでした。

```python
@parse_router.get("/expiry-options", response_model=ExpiryOptionsResponse)
async def expiry_options(
    clock: Annotated[Clock, Depends(get_clock)],
    time_start: TimeStartParam = None,
) -> ExpiryOptionsResponse:
    """GET /v1/intents/expiry-options(05 §5追記・design §2.6)。

    completion.py の単一実装を呼ぶだけ(新規計算ロジックなし — 07 §2)。
    UI計算(ws-5)がこのAPI経由で消費する。
    """
    now = clock.now()
    candidates = expires_at_candidates(now)
    default_index: int | None = None
    if time_start is not None:
        nearest = nearest_expires_at(time_start, now)
        default_index = candidates.index(nearest)
```

`expires_at_candidates` と `nearest_expires_at` はcompletion.pyの既存関数です。
**このエンドポイントには計算ロジックが1行もありません**。既存の単一実装を
呼んで、応答の形に包んでいるだけです。フロント側は、その応答からselectを
組み立てます(`intent/expiry.js`。valueに入れるのはラベルでなく絶対時刻
`expires_at` なので、利用者が選んだ値をそのまま保存APIへ送れます)。

これが「同じ計算を2箇所に書かない」を**約束ではなく構造で**守る仕組みです。
規律をコメントや合意に頼らず、実装の形そのものに焼き込む。この章で扱った
構造の話には、これまでの章が繰り返してきた流れが集まっています。

- 第3章: 時刻の単一経路(Clock)
- 第5章: 補完の単一規則(completion.py)
- この章: 期限計算をAPIで消費する(新規実装ゼロ)

なお、このエンドポイントの追加には、仕様書(docs/05 §5)への1行追記が伴いました。
コードの変更が仕様書の変更を必要とするとき、両方を同時に変える——運用ルール7の
改版手続きです。1エンドポイントの追加ですら、docsと実装の対になる更新が
行われている点も、このプロジェクトの作法の見本です。

## 8.7 画面から計算を取り出す: 純関数と81件の試験

最後に、構成の話をもう1つ。`format.js` は、構造化データを「画面に表示する文言」へ
変える関数の集まりです。

```javascript
export const formatParticipants = (p) => {
  const min = p?.min ?? null;
  const max = p?.max ?? null;
  if (min === null && max === null) return "指定なし";
  if (min !== null && max !== null && min !== max) return `${min}〜${max}人`;
  return `${min ?? max}人`;
};
```

入力(データ)を受け取り、文言を返す。ブラウザも、サーバーも、画面も知らない。
**外の世界を一切読み書きしない関数**を、第6章6.4で学んだ言葉で **純関数**
(じゅんかんすう)と呼びます。parse応答が条件リストの5行に変わる過程は、
この純関数だけで完結します。この設計の実利は、試験の書きやすさです。
外の世界がない試験対象は、データを渡して返り値を比べるだけで検証できます。
`npm test` の81件が、ブラウザもネットワークも使わず0.6秒ほどで終わるのは、
**検証したい部分が外の世界から切り離されて置いてある**からです。

81件の内訳(`npm test` のファイル別)を、第5章5.8の表と同じ視点で並べます。

| ファイル | 守る対象 |
|---|---|
| smoke.test.js | index.htmlとstyles.cssの構造(prototypeの主要IDが残るか・初期テキストは空か) |
| format.test.js | 表示文言・JST変換・APIボディ組立(純関数) |
| state.test.js | 「預ける/下書き」の有効条件と必須欠落の検出 |
| session.test.js | トークンの保管・破棄・401(偽の保管庫で検証) |
| client.test.js | 401→refresh→1回再送(偽のfetchで検証) |
| parseFlow.test.js | debounce・同一テキスト抑制・応答分岐(偽の時計とfetch) |
| conditions.test.js | 条件リストの描画・催促・NG行(happy-domの擬似DOM) |
| expiry.test.js | 応答からselectを組み立てる(disabled・既定選択) |
| save.test.js | 201/422/429/503の分岐と二重送信防止 |

試験の実行環境は **happy-dom**(ハッピードム)です。DOMとは、ブラウザが
「今画面に何が置いてあるか」を表現する内部のデータ構造(8.1のindex.htmlを
読み込んだ結果)で、happy-domはそのDOMをメモリ上で真似るライブラリです。
第5章のStubLLM、第7章のfakeredisと同じ発想——**本物に代わる、形は同じで
軽い部品**で、ブラウザを立ち上げずに描画まで含めた試験が走ります。

backendとの2つ目の共通点はここにあります。第6章6.7の「unitとintegrationの2層」
は、フロントでも同じ形で再現されています。unit相当がこの81件(実環境を一切
使わない)で、integration相当がcompose常設apiと結合する実機確認です
(手順と結果は `docs/plans/M1/ws-5-report.md` に記録されています)。
さらに自動化されたブラウザ試験(E2E試験)は、**あえて導入していません**。
理由はdesign §5に記録されており、backend integration試験+フロントunit試験+
実機確認の組み合わせで02#3の完了条件は検証できる、という判断です。
「何を導入しないか」まで設計書に書く。これも第7章7.9の「得失つきで記録」の
延長線上にある作法です。

`main.js`(273行)がこの章の終着地です。この章で読んできた部品——session、
client、parseFlow、expiry、save、format——を、index.htmlの各要素へ結びつけている
だけのファイルです。ロジックは1行もありません。だから「画面のどこを直したいか」
が決まれば、触るべきファイルはほぼ自動的に決まります。文言の修正はindex.html、
エラー表示の振り分けはsave.js、表示文言の規則はformat.js——**ファイル名が
責任を表している**状態は、backendの「ルータは受付・サービスは判断」と同じく、
3層に分けたことの利益です。

## 8.8 この章の再統合

- prototype/は「実装基準」の参照物として温存され、本実装はfrontend/に新設された。
  基準と実装を分けることで、双方の役割が消えない
- CORS設定を持たないbackendと画面を同じオリジンにまとめるのがvite proxy。
  「backendを変えずに問題を消す」選択は、変更面の最小化という設計判断の例
- トークンは寿命で分けて置く(access=sessionStorage・refresh=localStorage)。
  401ではrefreshを1回試み、成功なら元のリクエストを再送する。回数は必ず決める
- サーバーのエラーはenvelopeのcodeで分岐し、「行単位に直すべき失敗」と
  「画面全体に出す失敗」へ振り分けられる。第5章の422/503の切替がここで
  画面の言葉になる
- 期限の計算はexpiry-options APIが担う。単一実装をAPIで消費する構造が
  「2箇所に同じ計算を書かない」を約束でなく形で保証する
- format.jsのような純関数に計算を寄せたことが、81件の試験を0.6秒へ押し下げた。
  unit/integrationの2層構成は、フロントでもそのまま成り立つ

これで、LATCHの構成要素は第1章の地図から1つ増えました。backend(第4〜7章)と
frontend(この章)が揃い、ユーザーが文を書いてから保存するまでの全経路を、
コードで追える状態です。Lab 4では、この章の部品をターミナルとブラウザの両方で
動かして確かめます。

## 8.9 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| prototype / frontend | 実装基準の見本(静的モック) / APIに接続する本実装 |
| Vite / npm | フロントの開発サーバーとビルド道具 / 依存管理の道具(uvの相当) |
| package.json / package-lock.json | 依存の宣言 / 依存の完全固定(uv.lockの相当) |
| オリジン | スキーム+ホスト+ポートの3つ組。「出身地」のこと |
| CORS / vite proxy | 別オリジンを許可するサーバー側の設定 / 別オリジンを同一に見せる転送 |
| fetch / Promise | ブラウザ版のcurl / 「まだ届いていない結果」の受け取り伝票 |
| import / export / アロー関数 | 別ファイルから名前を借りる / 名前を貸す / 関数の書き方の1つ |
| sessionStorage / localStorage | タブを閉じると消える保管庫 / 残り続ける保管庫 |
| 開発用トークンパネル | IdPトークンを貼ってトークンを得る画面。本番ログインUIと交換経路は共通 |
| ApiError / envelope | エラー応答をJSの例外にしたもの / エラーの共通形 |
| debounce | 入力が止まってからしばらく待って実行する制御 |
| AbortController | 送信中のリクエストを取り消す仕組み |
| 純関数 | 外の世界を一切読み書きしない関数 |
| happy-dom | ブラウザのDOMをメモリ上で真似る試験用ライブラリ |

## 8.10 確認問題

1. `prototype/` をそのまま昇格させて本実装を書き足さず、`frontend/` を新設した
   理由を、「実装基準」という言葉を使って説明してください
2. `http://localhost:5173` と `http://127.0.0.1:8000` は別オリジンです。ポート番号が
   違うだけでも別オリジンになる理由を、オリジンの定義から説明してください
3. access_tokenとrefresh_tokenで保管場所が違う理由を、それぞれの寿命と結び付けて
   説明してください
4. 401が返ってきたとき、`client.js` はどう動きますか。refreshに成功する場合と
   失敗する場合の両方を述べてください
5. `VALIDATION_ERROR` のときだけmessageの先頭を見ている理由と、推定が外れたときに
   起きることを説明してください
6. 有効期限の計算をJavaScriptで再実装しない判断について、二重実装の問題と、
   それを構造で防ぐ仕組みを説明してください
7. `npm test` が81件を0.6秒ほどで終えられる理由を、format.jsが純関数であることと
   happy-domの役割から説明してください
