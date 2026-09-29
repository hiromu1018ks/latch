# 第10章 Intentを意味の数値へ変える: Embedding

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第9章(特に9.1のat-least-onceと冪等性・9.4のdebounce・9.5のversion検査)・
  第5章5.2(LLM Gatewayの3系統とスタブ)・第3章(Clock)・第1章1.7(pgvectorの予告)
- この章を読み終えるとできるようになること:
  - 「意味の近さを比べるために文章を数値の列へ変換する」処理(Embedding)が、
    LATCHのマッチングでなぜ必要なのかを、語彙のすり抜けの例で説明できる
  - 外部のAIサービスへ送るテキストを、ユーザーの生文(raw_text)を使わず
    構造化データから組み立てる設計と、その理由を説明できる
  - 外部API呼び出しの規律(timeout 2秒・再試行なし)が、失敗時の
    「保存は済ませて、あとから拾い直す」設計(バックフィル)と対になっている理由を説明できる
  - 内容更新でベクトルをNULLへ戻す(クリアする)2行が、全置換UPDATEのどこに
    入っているか示せる
- 対応コード: `backend/src/latch/worker/`(embedding_text.py・embedding.py・backfill.py)と
  `backend/src/latch/llm/`(gemini.py・embed_smoke.py・gateway.pyへの追記)が主役。
  `intents/store.py` の `_UPDATE` と `worker/main.py` の配線、`intents/events.py` の
  定数追加もこの章で読みます
- 設計の根拠: `docs/plans/M2/ws-2-design.md`(特に§2.1の実行配置・§2.2の2フェーズ・
  §2.3のembeddingクリア・§2.5のバックフィル)と `docs/plans/M2/ws-2-report.md`。
  本文の「NN §X」は `docs/NN-*.md` の第X節を指します(07 §3=Embedding対象テキストの
  形式定義・06 D-15=バックフィルの規定)
- 次に読むもの: `concepts/11-matching-retrieval.md`(第11章。この章の終わりで発行される
  embedding_completed を起点に、マッチングのLayer 1〜3が動きます)

## 10.1 語彙が違っても「意味が近い」を比べたい

第9章の終わりで、created/updated の処理は「Embedding要求のキック」まで、と読みました。
この章はそのEmbeddingの実体です。まず、この処理が何を解決するのかを確認します。

マッチングの最終目的は、条件の合う2人のIntentを組み合わせることです。カテゴリや場所・
時間のような条件は、第6章で保存した構造化データそのまま比較できます。ところが、
Intentには構造化データに落ちにくい味わいが残ります。「静かなお店で」と書いた人と
「おしゃれなバーで」と書いた人。「焼肉」を食べたい人と「肉系なら何でも」の人。
こうした**文章の言い回しの近さ**を、DBの一致比較(=)では扱えません。「焼肉」と
「肉系なら何でも」は文字列として1文字も共有していませんが、探している相手としては
かなり近いはずです。

そこで、文章を**意味の近さが数値の差として出る形**へ変換します。文章を768個の数字の
列(ベクトル)へ変換しておくと、意味が近い2つの文章は数値の並びが近くなる——
こう変換してくれる仕組みを **Embedding**(エンベディング。埋め込むの意)と呼びます。
変換の結果得られる数値の列が**埋め込みベクトル**です(以下、単にベクトル。
文章を数値空間に埋め込む、という絵です)。変換そのものは学習済みの
AIモデルが行うもので、LATCHが自前で作るものではありません。外部のAIサービスへ
テキストを送り、ベクトルを受け取る、それがこの章のAPI呼び出しの正体です。

モデルはGoogleの **gemini-embedding-001** に決まっています(T1 §2.4。768次元は
パラメータで指定)。768という次元数は、DBの `intents.embedding` 列の型
`vector(768)` と一致します(05 §2)。モデルが変わると、同じ空間の比較はできなく
なります。どのモデルで作ったベクトルか分かるよう、`intents.embedding_model` 列に
モデル名を記録しています(01 §18)。

変換は、Intentが active で保存されたときに始まります。draft では呼ばれません(07 §1)。
作成・active化・内容更新の3つのタイミングで、第9章のイベント経路がこの変換をキック
します。1回あたり約100トークン(トークン=AIモデルが文章を数える単位。日本語は
おおよそ1文字前後で1トークン)、月に5万回の試算で約$0.75/月というのがコストの
見積もりです(T1 §4)。

## 10.2 外部へ送る文は、自分で組み立てたものだけ

外部サービスへ何を送るか——ここにこの単位でいちばん大事な設計があります。

Intentには、ユーザーが入力した生の文章 `raw_text` がそのまま保存されています
(第6章)。Embeddingの入力として一番あからさまな候補は、このraw_textです。
文章をベクトルにするなら、文章を送るのが自然に見えます。LATCHはこれを**送らない**
と決めました。送るのは、構造化データから決まった形式で組み立てた短い一文だけです。

> `{カテゴリ} / {時間帯の表現} / {場所名} / {人数の表現} / {条件を「・」で連結}`(07 §3)

この組み立て結果を **正規化テキスト** と呼びます(正規化=一定の規則で形式を揃える
こと)。実際に動かすと、こんな文ができます(この章10.9の1で同じものを動かします)。

```text
drinking / 平日夜20-23時 / 天文館 / 2-4人 / 静かなお店で・予算は抑えめ
```

送らない理由は2つあります。1つは**送るものを最小にする**規律です。外部サービスへ
ユーザーの文章をそのまま送る経路を持たない、というのが仕様の確定値です(01 §21)。
これは「誰の・どんなデータを・どこへ送ったか」の記録(08 §3)と対になる約束です。
マッチングの材料として必要なのはカテゴリ・時間・場所・人数・条件の5要素で、
生の言い回し全体ではありません。もう1つは**決定性**です。raw_textは同じ意味でも
書き方が無限にあり、「書きぶんのゆらぎ」がそのままベクトルのゆらぎになります。
構造化データから決まった規則で組み立てれば、同じ内容からは必ず同じ文ができ、
同じベクトルが得られます。テストで同じ結果を保証できるのは、この決定性のおかげです
(第3章のテスト思想がここでも効きます)。

実装は `backend/src/latch/worker/embedding_text.py` です。入力を受け取るdataclassが
設計の宣言になっているので、まずそこから読みます。

```python
@dataclass(frozen=True)
class EmbeddingTextInput:
    """正規化テキスト導出の入力(intents列の部分・raw_textなし — 確定値#10)。"""

    category_primary: str
    structured_data: dict
    participants_min: int | None
    participants_max: int | None
    time_start: datetime | None
    time_end: datetime | None
```

(`embedding_text.py:20` から)

注目する点はピンの場所です。この入力に **raw_textの欄が最初から存在しない**。
関数の引数に生文を受け取らない、という規則を、コメントではなく型で強制しています。
もし後から誰かが「生文も送ればもっといいベクトルが」と思ってこの関数を呼び直そう
としても、渡す欄がなくて気づく。規則をコードの形に残す工夫です。このdataclassを
組み立てる側(`embedding.py` のSQL)も、SELECTする列にraw_textを含めていません。
「送らない」を2枚のピンで留めている、と読んでください。1枚は試験でもあります
(`test_worker_embedding_text.py` の `test_input_dataclass_has_no_raw_text_field`。
欄がないことを検査する試験です)。

形式に含めないもののうち、visibility と notification_level も触っておきます。
これらは表示・通知の制御であって、マッチングの判定材料ではないためです(07 §4の
規律——判定材料でないものは送らない)。予算 budget も含まれません。予算の一致は
第11章で読む Hard Filter(SQL)の担当で、意味の近さとは別の軸だからです。

## 10.3 「平日夜20-23時」は、決定的な関数で組み立てる

正規化テキストの中で、もっとも加工が要るのは時間帯の表現です。
`2026-09-28 20:00〜23:00` という生の値から「平日夜20-23時」を作る必要があります。

この変換を、仕様は「算術・日付処理はコード側で行い、モデルに計算させない」(07 §4)
と定めています。LLMに「これは平日?夜?」と聞くのではなく、Pythonの関数で
決定的に導く規則です。実装は純関数(同じ入力から常に同じ出力を返す関数。
DBもClockも呼ばない)として書かれています。

曜日の部分は、月曜〜金曜を「平日」、土日を「週末」へ分けます。時刻の帯は、開始時刻を
5つの枠へ分ける固定の表で決まります(`embedding_text.py:32` の `_band`)。

| 開始時刻 | 帯 |
|---|---|
| 5時以上11時未満 | 朝 |
| 11時以上16時未満 | 昼 |
| 16時以上19時未満 | 夕方 |
| 19時以上23時未満 | 夜 |
| それ以外(23時・0〜4時) | 深夜 |

この帯の切り方はdocsには規定がなく、実装の定義です(07 §3の例示「平日夜20-23時」と
整合することが唯一の拘束)。だからこそ、表の全体をunit テストでピン留めしています。
テストの1つ `test_band_table_boundaries` は、境界の時刻(5時・11時・16時・19時・23時)
がどの帯に落ちるかを全て検査します。境界を含めるかどうかは、仕様書に書かれてい
ません。書かれていないぶん、実装と試験がセットで仕様の実態になります——この箇所は
その良い例です。

時刻の部分は「20-23時」のように「開始時-終了時」で作ります。終了が翌日の日をまたぐ
場合は終了時刻の時だけを使う(23:30〜翌1:00なら「23-1時」)。時間帯の指定が
なければ「20時以降」のように「開始時刻-以降」の形に変わります。

組み立ての全体は `build_embedding_text`(`embedding_text.py:78`)が担当します。
空の要素は飛ばして、残りを「 / 」でつなぐ、という規則です。場所名のないIntentなら
「drinking / 週末深夜23-1時 / 2人」のように、場所の欄ごと抜け、区切りの「 / 」も
残りません。この「抜けた欄の区切りが残らない」ことも試験になっています
(`test_soft_constraints_empty_omits_trailing_separator`)。

## 10.4 外部APIを呼ぶ規律: 2秒で切り上げ、再試行しない

正規化テキストが決まれば、次は外部への送信です。この呼び出しには厳しい規律が
3つ課されています(07 §1・06 §1)。

1. **timeout 2秒**。2秒で応答がなければ諦める
2. **再試行なし**。1回失敗したら、その場では終わり
3. 呼び出しはLLM Gateway経由。送信記録(**SendRecord**。何を・いつ・どこへ
   送ったかの記録)を残す

再試行しないのは、一見手抜きに見えるかもしれません。普通の設計では「3回くらい
やり直す」のが定番です。LATCHが再試行をやめた理由は、失敗の回収を別の仕組みに
任せているからです(次の10.5と10.6で読みます)。この場で何度も待つより、
さっさと諦めて後でまとめてやり直すほうが、Workerの他の処理を滞らせません。

実プロバイダは `backend/src/latch/llm/gemini.py` です。ここにも見るべき工夫が
2つあります。1つはSDKの再試行を**明示的に止めている**こと。LATCHが使う
google-genai SDKは、何も設定しないと内部で自動的に再試行を行います。「再試行なし」
の規律を守るには、既定の再試行を切る設定が必須でした。

```python
def _http_options():
    """SDKのHTTP構成(timeout=2000ms・再試行1回のみ)。"""
    from google.genai import types

    return types.HttpOptions(
        timeout=GEMINI_HTTP_TIMEOUT_MS,
        retry_options=types.HttpRetryOptions(attempts=1),  # 再試行なし(07 §1)
    )
```

(`gemini.py:24` から)

もう1つは、API鍵を**明示的に渡している**ことです。SDKは環境変数
`GEMINI_API_KEY` や `GOOGLE_API_KEY` があれば黙ってそれを使います。鍵がどこから
供給されたか分からなくなるのを防ぐため、設定値(`LATCH_GEMINI_API_KEY`)を
`genai.Client(api_key=...)` へ明示渡ししています。「黙って環境を採用しない」は、
過去にBaseUrl設定の事故(M1)で学んだ規律の再適用です。

次元の検証もここにあります。応答が768次元でなければ `LLMProviderError` を投げ、
失敗として扱います(`gemini.py:53` の `embed`)。768次元はパラメータで指定できる
モデルなので、指定と応答が一致しているかの確認は契約の検査です。

実APIとの契約は、unit テストでは確認しきれません(第9章9.8で読んだ教訓どおり、
スタブは呼ばれ方しか検証できない)。そこで1呼び出しだけ実APIを叩くスモーク
テストが用意されました。`make embed-smoke` です(約$0.000015・`.env` に
`LATCH_LLM_MODE=real` と `LATCH_GEMINI_API_KEY` が必要)。私が実行したときの
出力はこうでした(数値は実行ごとに変わります)。

```text
[embed-smoke] model=gemini-embedding-001 dim=768 latency_ms=490 head=[-0.009, -0.0268, -0.0]
[embed-smoke] OK
```

768次元が返ること・0.5秒弱で応答すること・鍵が正しく渡ることを、この1行で
確認できます。費用を1呼び出し分に抑えた小さな検査ですが、「緑でも本番は落ちる」
シリーズの予防接種として作られています(ws-2設計 §2.7)。

なお、ci環境(compose)のworkerは今も `llm_mode=stub` です。鍵をciに渡さない構成を
保ったまま、Embedding系統だけをrealに切り替える構築関数 `build_embedding_gateway`
が新設されました(gateway.py)。Parser系統のreal化を壊さずに系統単位で差し替える、
第5章で読んだ「差し替え単位=プロバイダ」の考え方そのものです。stubでも
`embedding_model` には本番と同じ `"gemini-embedding-001"` を書き込みます。この列の
値は「本番で再エンベディングが必要な対象を特定する」ためのラベルです。stubで
試験するときも同じラベルで通す約束だからです(ws-2設計 §2.2)。

## 10.5 失敗しても保存は済んでいる: 2フェーズとガード付きUPDATE

処理の本体は `backend/src/latch/worker/embedding.py` にあります。流れを追う前に、
この章の山場——「外部API呼び出しをDBトランザクションの外へ出す」設計——を説明します。

素朴に書くと、こうなります。1つのDBトランザクションの中で「Intentを読む→APIを
呼ぶ→結果を書く→コミット」。原子性が高く、一見正しそうです。ところがAPI呼び出しは
最大2秒かかります。DBから見ると、接続と行の状態を保持したまま2秒間何もしない
トランザクションができる。この間に debounce の解放やバックフィルが同じ行を
触ろうとすると、待たされます。まして失敗したら? トランザクションをロールバック
しても、外部APIを呼んだという事実は消せません(副作用のロールバックはできない)。

LATCHの解は、処理を2つのフェーズに割ることです(ws-2設計 §2.2)。

```text
フェーズ1(短いトランザクション): 対象を読むだけ
フェーズ2: API呼び出し(どのトランザクションにも属さない)
           ↓ 成功したら
ガード付きUPDATE + イベント行INSERT(もう1つの短いトランザクション)
```

フェーズ1では、`embedding.py:37` のSELECTで対象のIntentを読みます。ここでも
SELECTする列にraw_textがありません(10.2のもう1枚のピン)。読んだ結果で分岐します。
versionが知らせと違えば、より新しい知らせが担当するので何もしない。embeddingが
すでに入っていれば(resumeで戻ってきたIntentなど。テキストは変わっていない)、
APIを呼ばずに後段への知らせだけ発行します。

フェーズ2でAPIを呼び、768次元の応答が得られたら、結果を書き込みます。書き込みの
SQLが「ガード付き」であることが、この設計の要です。

```python
_UPDATE_EMBEDDING = text("""
    UPDATE intents
    SET embedding = CAST(:vec AS vector), embedding_model = :model,
        updated_at = :now
    WHERE id = :intent_id AND version = :version AND embedding IS NULL
    RETURNING id
""")
```

(`embedding.py:46` から)

WHERE句の `version = :version` は「フェーズ1で読んだ内容が、まだ現役である」ことの
確認です。API呼び出しの2秒間にユーザーが内容を更新してversionが進んでいたら、
このUPDATEは0行を返し、書き込みは行われません(更新後の知らせが改めて担当します)。
`embedding IS NULL` は二重書き込みの排除です。フェーズ1とUPDATEのあいだに、
再配信でもう1つの処理が同じIntentへベクトルを書き込んでいても、後から来た方は
0行で退出します。**読み取りは参考情報であって、正しさはUPDATEの条件が担保する**。
外部I/Oをトランザクションの外に出しても壊れないのは、このガードがあるからです。

そして、APIが失敗したとき。ここが「再試行なし」の另一半です。

```python
        try:
            vec = await self._gateway.embed_intent(text=text, intent_id=str(intent_id))
        except LLMError as exc:
            # 再試行なし(07 §1)。embeddingはNULLのまま=バックフィル対象(06 D-15)
            logger.warning(
                "embedding failed (backfill target) intent_id=%s version=%s error=%s",
                intent_id,
                version,
                type(exc).__name__,
            )
            return
```

(`embedding.py:122` から)

例外を握って、警告ログを1行残して、returnします。Intentの保存は既に済んでいる
(第6章の経路)。embedding列はNULLのままです。このIntentは「あとでやり直すべき対象」
としてDBに残ります。ユーザーには何も起きません。保存は成功しているので、
画面もエラーになりません。**保存の成功と、ベクトル生成の成功を、別々の成功として
扱う**——これがこの単位の失敗設計の中心です(06 D-15)。

## 10.6 遅れたぶんは拾い直す: バックフィル

embeddingがNULLのまま残ったIntentを回収するのが、`backend/src/latch/worker/backfill.py`
の **バックフィル**(backfill=後から埋める)です。Workerの中で周期的に動くタスクで、
300秒ごとに対象を探します(`settings.py` の `embedding_backfill_interval_sec=300`・
`embedding_backfill_batch_limit=50`)。

対象を探すSQLはこれだけです。

```python
_SELECT_BACKFILL_TARGETS = text("""
    SELECT id, version FROM intents
    WHERE embedding IS NULL AND status = 'active'
    ORDER BY updated_at
    LIMIT :limit
""")
```

(`backfill.py:24` から)

注目する点を3つ挙げます。まず**探すだけで、押し戻さない**。見つけた対象は
EmbeddingWorker.handle に渡すだけです。プロバイダが障害中ならhandleはまた失敗し、
embeddingはNULLのまま。次の周期でまた選ばれて、また試される。APIが回復した瞬間に
自然に全部埋まっていく——「回復後に一括再エンベディング」という規定(06 D-15)を、
再試行の仕組みを1つも追加せずに実現しています。10.4で読んだ「再試行なし」は、
この周期タスクがあるから成り立つ規律でした。

次に、対象が `status = 'active'` に限られていること。paused のIntentは、resumeの
タイミングで通常のキックが走るので、バックフィルの世話にならない設計です
(resumeで埋め込み済みならAPIを呼ばずに済ませる話は10.5で出ました)。古い失敗から
順に埋まるよう `ORDER BY updated_at` で並べ、1周期あたりのAPI消費上限をLIMITが
持っています。

最後に、ループが **sleep-first**(待ってから処理する)なこと。Workerが起動して
も、まず1周期ぶん待ってから最初の処理に入ります。起動直後のバーストを避けるためと、
統合試験の間に周期タスクが動いてfixtureを書き換えてしまう事故の予防です
(ws-2設計 §2.5)。

## 10.7 内容が変わったら、ベクトルは捨てる

ここまで読むと、1つ疑問が残ります。**内容を更新したIntentはどうなるのか**。

古い内容で作ったベクトルが残ったままでは、更新後のIntentは前の内容の意味で検索され
続けます。意味の近さを比べる仕組み(第11章)が崩れるので、これは放置できません。
ところがdocsを探しても、「更新時にembeddingをNULLへ戻す」という一文はどこにも
ありません。設計メモは、確定値の組み合わせからこの結論を導いています(ws-2設計
§2.3)。根拠の1つはコストモデルです。T1 §4はEmbeddingの回数を「Active作成・
active化・active更新」と数えていて、更新時の再変換を織り込み済みです。もう1つは
resumeの規定との整合です。「embeddingが既にある=テキストが不変」という前提は、
内容更新でベクトルを捨てないと保てません。

実装は驚くほど小さいです。全置換UPDATE(`intents/store.py` の `_UPDATE`)のSET句に、
2行が挿入されました。

```python
_UPDATE = text(f"""
    UPDATE intents SET
        category_primary = :category_primary,
        ...(中略: 他の列の設定が続く)...
        status = :status,
        version = :version,
        embedding = NULL,
        embedding_model = NULL,
        time_start = :time_start,
        ...(以下略)
""")
```

(`store.py:112` から。`embedding = NULL, embedding_model = NULL` の2行が ws-2 で追加)

更新がコミットされれば、updated の知らせがdebounceの窓を経て(第9章9.4)キックを
呼び、embeddingがNULLになったIntentは通常の経路で再変換されます。バックフィルの
対象と同じ「embedding IS NULL」なので、たまたま変換が失敗しても同じ回収経路に
乗ります。経路の再利用です。

例外もあります。pause/resume のような**状態だけの変更**は別のSQL(`_UPDATE_STATUS`)
を使うため、ベクトルは消えません(06 §9のresume規定どおり)。内容の全置換だけが
ベクトルを捨てる、という切り分けです。visibilityだけ変えた更新でもベクトルは
作り直されますが、1回ぶんの約$0.000015で、コスト試算の枠内という割り切りです。

## 10.8 第2段への合図: embedding_completedと、第9章のフックのその後

ベクトルの書き込みが決まると、EmbeddingWorkerは最後にイベント行を1つINSERTして
publishします。event_typeは `embedding_completed`。第9章で「6種のevent_type」の
1つとして名前だけ出てきたもので、定数はこの単位で追加されました
(`intents/events.py:23`)。冪等キーの3点組は(embedding_completed, intent_id, version)
で、INSERTは `ON CONFLICT DO NOTHING`(第9章9.3と同じ形式)です。

この知らせを受け取った側のマッチング処理は、後続の単位が担当します。
つまりこの章の完成ラインは「embedding_completedが発行され、次の単位が消費できる
状態」。第1段(created/updated→Embedding)と第2段(embedding_completed→マッチング)を
分けたのは、失敗の性質が違うからです。Embeddingは外部API相手なので失敗が日常的で、
バックフィルという回収が要る。マッチングはDB内の処理で、失敗は再試行5回の
ステージ処理に載せられる(第9章9.7)。**どこで区切るか=どこで回収するか**の設計です。
(2026-09-29追記: この読みのとおり、ws-4でembedding_completedを起点にLayer 1〜3が
動くようになりました。第11章・第12章がその消費側です)

最後に、第9章9.7で予告した「予約だけ置いたフック」のその後を書いておきます。
ws-1はstage1の中に `embedding_hook` という呼び出し位置を予約しました。実際にws-2が
実装したとき、埋め場所はこのフックではなく **Workerの配線** に変わりました
(`worker/main.py:185` の `_kick_embedding`)。理由は、あのフックがDBトランザクションの
**中**で呼ばれる位置だったためです。外部API呼び出し(最大2秒)をトランザクションの
中で行うと10.5で読んだ問題が全部起きます。再試行なしの規律とも矛盾します。そこで、
Stage1の処理がコミットした**直後**、ackを返す**前**にEmbeddingWorkerを呼ぶ位置へ
ずらしました。stage1.pyは1行も変えていません。予約した位置が使えないと分かった
とき、予約を壊さずに呼び出し側で位置を選び直す——土台(ws-1)を変更しない
追加の仕方です。

```python
    async def _kick_embedding(
        self, event_type: str, intent_id: uuid.UUID, version: int
    ) -> None:
        """Stage1処理コミット後・ack前のEmbeddingキック(design §2.1-B)。

        ...docstringの続き...
        """
        if self._embedding is None or event_type not in (EVENT_CREATED, EVENT_UPDATED):
            return
        await self._embedding.handle(intent_id, version)
```

(`worker/main.py:185` から)

ack前にキックを終える意味も第9章の読みが効きます。キックの途中でWorkerが落ちたら、
ackされない知らせは再配信されます。再配信ではstage1はduplicateと判定しますが、
Workerはduplicateでもキックを呼ぶ(`main.py:148` の分岐)ので、未完了の埋め込みは
再配信で回収されます。LLM失敗だけはバックフィルが、それ以外の失敗は再配信が拾う。
at-least-onceの世界の最後の1枚として、ここでも配達の重複を「冪等な処理」で
消化しています。

## 10.9 自分で確かめる

1. 正規化テキストを自分の手で作る。`cd backend` して次のスクリプトを動かします。
   平日夜の飲み・週末朝の食事・場所なし深夜の3例です

   ```bash
   uv run python - <<'EOF'
   from datetime import datetime
   from latch.core.clock import JST
   from latch.worker.embedding_text import EmbeddingTextInput, build_embedding_text

   base = dict(
       category_primary="drinking",
       structured_data={"location_name": "天文館",
                        "soft_constraints": [{"text": "静かなお店で"}, {"text": "予算は抑えめ"}]},
       participants_min=2, participants_max=4,
   )
   print(build_embedding_text(EmbeddingTextInput(
       time_start=datetime(2026, 9, 28, 20, 0, tzinfo=JST),
       time_end=datetime(2026, 9, 28, 23, 0, tzinfo=JST), **base)))
   print(build_embedding_text(EmbeddingTextInput(
       category_primary="meal",
       structured_data={"location_name": "鹿児島中央駅周辺", "soft_constraints": []},
       participants_min=1, participants_max=1,
       time_start=datetime(2026, 10, 3, 9, 0, tzinfo=JST),
       time_end=datetime(2026, 10, 3, 11, 0, tzinfo=JST))))
   print(build_embedding_text(EmbeddingTextInput(
       category_primary="drinking", structured_data={},
       participants_min=2, participants_max=2,
       time_start=datetime(2026, 10, 3, 23, 30, tzinfo=JST),
       time_end=datetime(2026, 10, 4, 1, 0, tzinfo=JST))))
   EOF
   ```

   期待される出力(2026-09-29に実行して確認しました。2026-09-28は月曜、
   2026-10-03は土曜です):

   ```text
   drinking / 平日夜20-23時 / 天文館 / 2-4人 / 静かなお店で・予算は抑えめ
   meal / 週末朝9-11時 / 鹿児島中央駅周辺 / 1人
   drinking / 週末深夜23-1時 / 2人
   ```

   2例目の末尾に「 / 」が残らないこと、3例目の「2人」の前に場所の欄がないことを
   確かめてください。`datetime(2026, 9, 28, 20, 0)` の時刻を変えて、帯の境界
   (10.3の表)が想定どおりか試すのも良い練習です

2. `uv run pytest tests/unit/test_worker_embedding_text.py -v` を実行する。
   10件の試験名を縦に読み、10.2〜10.3のどの規則が試験になっているか対応づける。
   `test_input_dataclass_has_no_raw_text_field` が「欄がないこと」を検査している
   しくみ(dataclassのフィールド名一覧)も、試験のコードを読んで確かめる

3. `uv run pytest tests/unit/test_worker_embedding.py -v` を実行する(16件)。
   試験名から、handleの冪等・失敗経路(LLM失敗で例外が外へ出ないこと)・
   SELECT文にraw_textが含まれないことのピンを探す

4. `rg -n "raw_text" backend/src/latch/worker/embedding.py backend/src/latch/worker/embedding_text.py`
   を実行する。ヒットするのはdocstringとコメントだけで、SQLとdataclassの定義には
   現れないことを確認する(私の実行では5行すべてが説明文でした)

5. `.env` に `LATCH_LLM_MODE=real` と `LATCH_GEMINI_API_KEY` がある状態で
   `make embed-smoke` を実行する(1呼び出し・約$0.000015)。`dim=768` と `OK` が
   出れば、実APIとの契約が今も生きている証拠です。鍵のない環境では
   FAILのメッセージが出て1で終わります(実APIは呼びません)

## 10.10 この章の再統合

- Embeddingは、文章の意味の近さを比べるため、テキストを768個の数値の列へ変換する
  仕組み。モデルはgemini-embedding-001。変換のタイミングはactive作成・active化・
  内容更新の3つ
- 外部へ送るテキストは構造化データから組み立てた正規化テキストだけ。raw_textを
  送る経路は持たない。入力dataclassに欄がなく、SELECTにも列がない——規則を型と
  試験でピン留めしている
- 外部APIは timeout 2秒・再試行なし。SDK既定の再試行は明示的に止め、鍵は明示渡し。
  実APIとの契約は embed-smoke の1呼び出しで検査する
- API呼び出しはDBトランザクションの外へ。正しさはフェーズ2のUPDATEが
  version一致+embedding IS NULL のガードで担保する。読み取りは参考情報
- 失敗しても保存は済んでいる。embeddingはNULLのまま残り、300秒周期のバックフィルが
  「回復後に一括して」回収する。再試行なしは、この回収があるから成り立つ
- 内容の全置換UPDATEはembedding/embedding_modelをNULLへクリアする。updated知らせの
  キックで再変換される。resume(状態変更)ではクリアされない
- 完了はembedding_completedの発行。第1段と第2段を分けたのは、失敗の回収方法が
  違うから。第9章のフックは使われず、Worker配線(コミット後・ack前)へ埋まった

第9章で通した経路に、処理の実体がひとつ載りました。保存→知らせ→Embedding→
知らせ、までは動く状態です。しかしまだ、ベクトル同士を比べる側を読んでいません。
768個の数字が「近い」とはどういうことか、数万人の中からどう絞るのか——次の章が
マッチングの前半、Layer 1とLayer 2です。

## 10.11 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| Embedding | 文章を、意味の近さが数値の差に出る768個の数字の列へ変換すること |
| 埋め込みベクトル | Embeddingの結果得られる数値の列。intents.embedding列に保存 |
| 正規化テキスト | 構造化データから決まった形式で組み立て、外部へ送る短い一文 |
| gemini-embedding-001 | LATCHが使うEmbeddingモデル(Google)。768次元はパラメータ指定 |
| 純関数 | 同じ入力から常に同じ出力を返す関数。DBもClockも呼ばない |
| トークン | AIモデルが文章を数える単位。日本語はおおよそ1文字前後で1トークン |
| バックフィル | 失敗などで空いたままのembeddingを、周期タスクが後から埋め直す仕組み |
| sleep-first | 周期ループがまず1周期待ってから処理を始める構え。起動直後のバーストを避ける |
| ガード付きUPDATE | WHERE句の条件(version一致・NULL確認)で「書いてよい瞬間」を検査するUPDATE |
| SendRecord(送信記録) | 何を・いつ・どの外部サービスへ送ったかの記録。Gatewayが自動で残す |
| embedding_completed | Embedding完了を告げるイベント。第2段(マッチング)の起点 |
| スモークテスト | 実APIへ最小の呼び出しをして契約が生きているか確かめる小さな試験 |

## 10.12 確認問題

1. 「焼肉を食べたい」と「肉系なら何でも」のように語彙が一致しないIntentどうしを、
   文字列の一致比較でマッチさせられない理由を、Embeddingが解決する仕組みと
   ともに説明してください
2. raw_textをEmbeddingへ送らない設計の理由を、送るものを最小にする規律と
   確定性の2つから説明してください。また、この規則を「型と試験」で守っている
   しくみを、EmbeddingTextInput と test_input_dataclass_has_no_raw_text_field に
   即して説明してください
3. EmbeddingのAPI呼び出しをDBトランザクションの外へ出した理由と、外に出しても
   正しさが壊れない理由(WHERE句の2条件)を説明してください
4. 「再試行なし」という規律が成立するのはなぜか。バックフィルの周期タスクとの
   関係で説明してください
5. pause/resume では embedding をクリアせず、内容更新(PATCH全置換)ではクリアする
   理由を、「embeddingが既にある=テキストが不変」という前提の保ち方から説明してください
6. 第9章で予約されたembedding_hookが実際には使われず、Worker配線に埋め直された
   経緯を、フックの呼ばれる位置と timeout 2秒・再試行なしの規律の関係から
   説明してください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった部分が、
読み返すべき節です)
