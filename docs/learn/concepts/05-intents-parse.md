# 第5章 信頼できない協力者を迎える: POST /v1/intents/parse の境界設計

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第4章(3層の経路・エラーの意味づけ)・第3章(Clock・FakeClock)
- この章を読み終えるとできるようになること:
  - 「今週末あたりで飲みに行きたい」のような自然文が構造化データに変わる流れを、
    実際の入出力で説明できる
  - LLMの代役となるスタブが、なぜ・どのように開発を支えるか説明できる
  - LLMの出力を検証してからシステムに入れる理由と、その実装(ParserOutput)を追える
  - 失敗を422(利用者起因)と503(システム起因)に切り分ける理由を説明できる
  - Protocolという「形だけ合わせれば呼べる」仕組みの意味が分かる
  - 規則を破ったLLM出力を捨てずに正規化し、注意表示を付ける設計を説明できる
- 対応コード: `backend/src/latch/intents/`(7ファイル・小さいので通読可能です)
- 設計の根拠: `docs/plans/M1/ws-2-design.md`(特に§2.2〜§2.5の判断記録)
- 次に読むもの: ws-3(intents CRUD)の実装とともに追加される章(parseと保存が合流する経路です)

## 5.1 LATCHの心臓部: 自然文を構造化するとは何をするか

第1章で「ユーザーの『やりたいこと』をAIが構造化されたデータに変換する」と書きました。
M1 ws-2でその経路が実装され、`POST /v1/intents/parse` として動いています。
ここで主役の名前を正式に定義します。**LLM**(エル・エル・エム、Large Language
Model=大規模言語モデル)とは、文章をやり取りするAIの一種です。人間の書いた文章を
読んで、指示に従う文章を返せます。第1章で単に「AI」と書いていた部分は、
このLLMのことです。

まず、入力と出力を実際の形で見ましょう。

入力はこれだけです。ユーザーがアプリに入力した自然文そのもの(上限300字)。

```json
{"text": "今週末あたりで飲みに行きたい"}
```

出力は、マッチングに使えるよう項目に分解されたデータです(StubLLMの既定応答の例)。

```json
{
  "structured_intent": {
    "category": {"primary": "meal", "secondary": null},
    "alcohol_involved": false,
    "time": {"start": "2026-09-27T19:00:00+09:00", "end": null,
             "flexibility_minutes": null},
    "location": {"name": "東京駅", "radius_m": null, "flexibility": null},
    "budget": {"max": null, "currency": "JPY"},
    "participants": {"min": null, "max": null},
    "soft_constraints": [],
    "negative_constraints": [],
    "ng_unverifiable": []
  },
  "warnings": []
}
```

「飲みに行きたい」が `category.primary` に入り、日時・場所・予算・人数がそれぞれの
項目に落ちます。このうち **category・time.start・location.name の3つは必須**です。
この3つが取れないと、マッチングの対象として成立しません。

この経路の構成は第4章と同じ3層です。ルータ(`routes.py`)が受付、サービス
(`service.py`)が判断、その先にSQLの代わりに**LLMへの呼び出し**が来ます。
しかし、DBとLLMは性質がまるで違います。DBは「同じ問いに同じ答えを返し、
壊れたデータは制約で拒否する」律儀な相手です。LLMはどうでしょう。
返事の形が崩れることもあれば、都合の悪い入力に創作で答えることもある。
**この章の主題は、律儀でない相手とどう付き合うか**という、経路の型とは別の
設計の軸です。

なお、このAPIは**保存をしません**。parseは「プレビュー」で、変換結果を見せて
ユーザーに確認させるための窓口です。確定してDBに書くのはws-3(未実装)の仕事です。
だから経路にINSERTは1本もありません。

## 5.2 賢い嘘: スタブが開発を支える

今のLATCHは、実は本物のLLMに接続していません。プロバイダ契約(T1)がまだなので、
`llm_mode="stub"` という設定で動きます。LLMの役を務めるのが `llm/stub.py` の
**StubLLM**です。

スタブ(stub=切り株)とは「本物の代わりに置く、形は同じで中身は簡単な部品」のことです。
StubLLMは、初期化メソッド(第3章3.3の `__init__`)で受け取った固定の応答を返すだけの
LLMです。既定値が `DEFAULT_PARSER_RESPONSE` として同じファイルに定義されていて、
5.1の出力例はこれです。

面白いのは、スタブであることを隠さない点です。StubLLMはこう作られています。

```python
class StubLLM(ParserProvider, EmbeddingProvider, JevProvider):
    """テストモード(10 第1節)。name="stub"。"""

    def __init__(
        self,
        *,
        parser_response: dict | None = None,
        ...
        fail_parser: bool = False,
        ...
```

応答を差し替えられる(`parser_response`)、**失敗を演じられる**(`fail_parser=True`)、
遅延も注入できる。つまり「ハッピーなLLM」だけでなく「落ちるLLM」「遅いLLM」まで
演じ分けられる役者です。4章でDBをスタブに差し替えたのと同じ発想が、LLMでは
**本番側がまだ存在しない段階から**使えているわけです。

これが何を可能にするかというと、**LLMがなくてもシステム全体の設計と試験を進められる**
ことです。今週あなたが `make test` で回している60件のintents試験は、ネットワークも
API鍵も使いません。そして将来T1でプロバイダが決まったら、`build_llm_gateway` の分岐に
本物を足すだけ。切り株を本物の木に植え替えるとき、周りの庭はいじりません。

## 5.3 信頼できない協力者: 出力は境界で検証してから中へ

さて核心です。LLM(将来の本物)の返事を、そのまま信用していいでしょうか。

LLMは「JSONスキーマに従って出力せよ」という指示(プロンプト)を受けても、
従わない出力を返しえます。カテゴリのスペルミス、必須項目の欠落、そもそも
JSONとして壊れている返事。DBの例でいえば、制約を守らないINSERTを送ってくるような
相手です。人間の世界なら「この人の報告書は一度確認してから回します」となるでしょう。
システムでも同じです。

LATCHの答えは、**LLMの境界で必ず検証を1本通す**ことです。その役目を持つのが
`intents/schema.py` のParserOutputです。第4章のUserCreateRequestと同じPydanticモデルですが、
向きが逆です。UserCreateRequestは**利用者からの入力**を検証し、ParserOutputは
**LLMからの出力**を検証します。信頼できない入力の検証に、入力元が人間かAIかは
関係ない。検証という関所の価値は同じです。

```python
class ParserOutput(_IgnoreExtraModel):
    """07 §2出力JSONスキーマ(この形式のみ認める)。"""

    category: ParserCategory
    alcohol_involved: bool  # 常にtrue/false(規則7)・アプリ層補完なし
    time: ParserTime
    location: ParserLocation
    budget: ParserBudget = ParserBudget()
    participants: ParserParticipants = ParserParticipants()
    soft_constraints: list[str] = []
    negative_constraints: list[str] = []  # 常に空が正常系(FR-42・規則5)
    ng_unverifiable: list[str] = []
```

検証の様子を実際に見てみましょう。`backend/` で実行します。

```bash
cd backend && uv run python - <<'EOF'
from latch.intents.schema import ParserOutput

bad = {
    "category": {"primary": "meal"},
    "alcohol_involved": False,
    "time": {"start": "2026-09-27T19:00:00+09:00"},
}
try:
    ParserOutput.model_validate(bad)
except Exception as exc:
    for e in exc.errors()[:3]:
        print(e["type"], "→", list(e["loc"]))
EOF
```

期待される出力:

```
missing → ['location']
```

必須3フィールドの1つ `location` が欠けていることが、検証で機械的に捕捉されます。
この検査に落ちた出力は、システムの内側に一切入らず、次節の422になります。

細かい姿勢も読んでおくと面白い。クラスの継承元 `_IgnoreExtraModel` は
**余分なキーは無視する**設定です。失敗とするのは欠損・値域外・パース不能のみ。
LLMが余計な情報を足してきたくらいで利用者をフォームへ追いやるのは過剰、
という割り切りです(`schema.py` のdocstringに07 §4の失敗分類に揃える旨が書かれています)。

## 5.4 2つの失敗、2つの道: 422と503の切替

ここで、このAPIの失敗を全部挙げてみます。

1. textが300字を超えている → 422(ルータのPydantic検証)
2. 必須3フィールドが抽出できなかった/出力がスキーマに合わない → 422
3. LLMがタイムアウトした・障害で応えなかった → 503
4. DB(Redis等の依存)が落ちていた → 503

同じ「parseが失敗した」でも、2は**利用者に別の入力方法を案内すべき失敗**、
3と4は**システム側が直すべき失敗**です。LATCHの仕様(D-17)はこの2つを
厳密に区別させます。クライアントの画面では、422なら「フォームで手入力してください」
に分岐し、503なら「入力内容を保持したまま再試行」に分岐します。
**ステータスコードが、利用者の次の一手を決める**のです。

実装は `service.py` のこの順序にそのまま表れています。

```python
        try:
            raw = await self._parser.parse_intent(
                text=text,
                current_date=self._clock.jst_date(),
                user_id=str(user_id) if user_id is not None else None,
            )
        except Exception as exc:
            # Gateway契約上ここで飛ぶのはLLMError系(timeout・API障害)のみ
            # (design §2.7)。検証(ValidationError)は後段なので含まれない。
            raise LLMUnavailableError("intent parser unavailable") from exc
        if not isinstance(raw, dict):
            raise UnstructurableError("structured intent is not extractable")
        try:
            structured = ParserOutput.model_validate(_normalize_rule5(raw))
        except ValidationError as exc:
            raise UnstructurableError("structured intent is not extractable") from exc
```

LLM呼び出しで例外が飛んだら503(`LLMUnavailableError`)、戻ってきた値の検証に
落ちたら422(`UnstructurableError`)。**例外が上がったか、検証を通ったか**の
2点だけで判定が決まる、単純で強い構造です。

例外そのものは `intents/errors.py` に3つだけ定義されています。

```python
class LLMUnavailableError(IntentsError):
    """Parser系LLM障害(timeout・API障害・レート制限。07 §2・05 §5)。

    クライアントは入力テキストを保持した再試行ボタンへ分岐する(D-17)。
    """

    http_status = 503
    code = "LLM_UNAVAILABLE"
```

第4章のusersの例外(UserExistsError等)と同じ形——`http_status` と `code` を属性に
持ち、main.pyのハンドラが共通envelopeに変換する——ですね。ドメインごとに例外の
階層を作り、窓口の整形は1か所に置く。このパターンがM1で3回目(auth・users・intents)に
なり、もうプロジェクトの型といえます。

設計判断として1つ知っておくべきことがあります。「LLMがJSONとして壊れた出力を
返した場合」は、timeoutでも必須欠落でもありません。設計書(ws-2-design.md §2.4)は
これを**422に一元**しました。理由は3つ挙げられて、どれも契約を単純に保つ方向です。

- 中途半端な出力からは、必須3フィールドを信頼して拾い直せない
- 503の再試行で戻ってきても、同じ壊れた出力が返る可能性がある
- 422の逃し道(構造化フォーム)はどんなときでも機能する

契約にない第3の区分を作らずに済む利点もあります。

## 5.5 形だけ合わせれば呼べる: Protocolという仕組み

`IntentParseService` の初期化メソッド(第3章3.3の `__init__`)を見てください。

```python
class IntentParseService:
    """POST /v1/intents/parse のユースケース(同期・再試行なしはGateway側)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        parser: SupportsParseIntent,
        user_lookup: UserLookup,
    ) -> None:
```

`parser: SupportsParseIntent` という型に注目します。実行時にはここに本物のLLMGateway
が入ります。ところが `IntentParseService` のクラス定義は、LLMGatewayのことを
**型としても名前としても知りません**。代わりに、同じファイルにこう書かれています。

```python
class SupportsParseIntent(Protocol):
    """Parser系統の構造的Protocol(design §2.2)。LLMGateway.parse_intent と適合。"""

    async def parse_intent(
        self, *, text: str, current_date: date, user_id: str | None = None
    ) -> dict: ...
```

**Protocol**(プロトコル)とは、「この形のメソッドを持っている者なら、誰でもこの型と
みなせる」という仕組みです(ダックタイピング——アヒルのように鳴けばアヒル——を
型として書けるようにしたもの、と説明されることもあります)。継承や登録は不要で、
**メソッドの形が一致していれば自動的に適合**します。LLMGatewayはこのProtocolを
意識していないのに、`parse_intent(text=..., current_date=...)` という同名同形の
メソッドを持っているので、そのまま `parser` として渡せます。

これの何が嬉しいか。**「サービス本体はLLMに依存しない」という規律が、型とimportの
形で機械的に守られる**のです。もし `IntentParseService` がLLMGatewayを直接の型に
していたら、llm/の変更の影響がintents/まで波及する経路が残ります。Protocolを挟むと、
サービスが知るのは「parse_intentという形」だけ。LLMを丸ごとスタブと差し替える試験も、
この境界があるから自然に書けます(5.2のスタブの話と、ここで1本につながります)。

llm/へのimportは、ファイル単位で見ると `make_intent_parse_service` という構築用の
関数のための1行だけです。`rg -n "from latch.llm" backend/src/latch/intents` を実行すると
`service.py:27` の1行だけが返り、これが `build_llm_gateway` を呼ぶファクトリの中で
使われています。クラス本体は純粋なまま、LLMとの結びつきはこの1か所に閉じている、
という構図です。

## 5.6 保険としての正規化: 黙って降格させない

LLMに与える指示書(システムプロンプト)は、バージョン管理されたたった1つの定数として
`intents/prompt.py` に置かれています。その中に、こういう規則があります。

> 「会社関係の人は避けたい」のように、システムが判定データを持たない除外
> 条件は ng_unverifiable 配列に入れる。negative_constraints には入れない
> (negative_constraints はMVPでは常に空配列である。理由は次節)。

条件は2種類に分けられます。**negative_constraints** は「システムが判定できる除外条件」、
**ng_unverifiable**(判定不能NG)は「判定データを持たないので、確実には除外できない条件」です。
「会社関係の人は避けたい」が後者なのは、誰が会社関係かをシステムが知らないからです。
後者の条件は、除外を約束できないぶん、ユーザーに「この条件は確実には除外できません」と
注意表示で伝えることになっています。この注意表示をwarningsとしてAPI応答に載せる
連携が、仕様書でD-04と呼ばれているものです。

ところが、LLMはこの規則を破って `negative_constraints` に値を入れてくることが
ありえます(規則は指示であって制約ではない)。それを黙って通すと、判定不能な条件が
**注意表示なしで**マッチングに流れ込む。LATCHの選択は、境界での正規化という保険です。

```python
def _normalize_rule5(raw: dict) -> dict:
    """規則5違反の回復(design §2.5)。

    negative_constraints 非空(LLM違反)の要素を ng_unverifiable へ結合し、
    negative_constraints は空配列で応答する(FR-42の不変式回復。「常に空」の
    回復できる唯一の場所=parse境界)。結合由来の条件は warnings 生成対象に
    なるため D-04 の注意表示も出る(黙って降格させない)。
    """
    normalized = dict(raw)
    negative = normalized.get("negative_constraints") or []
    ng = list(normalized.get("ng_unverifiable") or [])
    normalized["negative_constraints"] = []
    normalized["ng_unverifiable"] = [*ng, *negative]
    return normalized
```

規則を破った値を捨てず、**「判定できない」側へ移して注意表示を付ける**。データは
保存し、意味の曖昧さは警告として見える化する。この設計態度は、エラーを握りつぶす
よりずっと健全で、第1章の「判定不能NGは黙って降格させない」というサービスの姿勢が
コードになったものです。

動くところを見ましょう。`backend/` で、規則5をわざと破るスタブLLMを渡して
サービスを回します(DBも本物LLMも使いません)。

```bash
cd backend && uv run python - <<'EOF'
import asyncio
from datetime import UTC, datetime
from latch.core.clock import FakeClock
from latch.intents.service import IntentParseService
from latch.llm.stub import DEFAULT_PARSER_RESPONSE

class RuleBreakingParser:
    """規則5を破るLLMの役者(negative_constraints に値を入れる)"""
    async def parse_intent(self, *, text, current_date, user_id=None):
        resp = dict(DEFAULT_PARSER_RESPONSE)
        resp["negative_constraints"] = ["会社関係の人は避けたい"]
        return resp

async def lookup(provider, subject):
    return None  # 未登録の利用者(登録前にparseしてもよい経路)

svc = IntentParseService(
    clock=FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)),
    parser=RuleBreakingParser(),
    user_lookup=lookup,
)

async def main():
    r = await svc.parse(text="今週末あたりで飲みに行きたい",
                        auth_provider="google", auth_subject="sub-x")
    print("negative_constraints:", r.structured_intent.negative_constraints)
    print("ng_unverifiable:", r.structured_intent.ng_unverifiable)
    for w in r.warnings:
        print("warning:", w.code, "/", w.condition)

asyncio.run(main())
EOF
```

期待される出力:

```
negative_constraints: []
ng_unverifiable: ['会社関係の人は避けたい']
warning: NG_CONDITION_DOWNGRADED / 会社関係の人は避けたい
```

LLMの違反出力が、境界を通った瞬間に「常に空」という約束を回復し、条件は
ng_unverifiableへ、注意表示はwarningsへ、それぞれ移っているのが見えます。

同じファイルには、**補完の単一規則**(`completion.py`)もあります。time.endがnullなら
開始3時間後に読み替える、人数がnullなら2人、という、Parserがやらない補完の
ルールです。パフォーマー(LLM)と演出側(アプリ)で二重実装すると必ずずれるので、
「補完はこの1ファイルが唯一の実装」と定めてあります。消費するのは保存(ws-3)と
UI(ws-5)で、parse応答には適用しません(出力例のend・radius_mがnullのままなのはそのため)。

## 5.7 もう1本の照会: なぜparseがDBを1回だけ読むのか

最後に、少し変わっている設計を1つ。parseは「保存しないプレビュー」なのでDBは
使いません。ところが実は、リクエストのたびにSELECTを1本だけ投げています。

これは、**送信記録に利用者を紐付けるため**です。LATCHはLLMへ送った事実を記録する
約束になっています。記録するのは「いつ・どの種類を・誰が依頼したか」の3点で、
送った中身(ユーザーの文章)は含みません(08 §3。問い合わせが来たら開示できる
状態を保つための記録です)。その「誰が」を埋めるため、JWTのclaimsからUser行を引いて
idを記録に渡します。未登録の利用者ならNoneのまま(5.6の例の `lookup` がまさに
その経路です)。

設計書(§2.3)には、この1本のために「DBが落ちたらparseも503で止まる」という
代償が明記されています。fail-closed(怪しいときは閉じる)側に倒した判断です。
**得られるものと失うものを設計書に書いて選ぶ**という作法は、なんとなく便利だから
SELECTを足すのとは違う、という見本になります。

## 5.8 テスト60件とプロンプトのピン留め

`backend/tests/unit/intents/` の60件も、4章と同じく層ごとに分かれています。

| ファイル | 件数 | 守る層・対象 |
|---|---|---|
| `test_completion.py` | 8件 | 補完の純粋関数 |
| `test_parser_output.py` | 18件 | スキーマの受入と拒否 |
| `test_service.py` | 15件 | サービスの判定(422/503切替・正規化・user_id帰属・JST日付) |
| `test_parse_routes.py` | 13件 | ルータの応答形状 |
| `test_prompt.py` | 3件 | プロンプトの全文ピン留め |
| `test_errors.py` | 3件 | 例外の属性(ステータスとcode) |

中でも一風変わっているのが `test_prompt.py` の**全文ピン留め**です。
`PARSER_SYSTEM_PROMPT`(prompt.pyの1つの定数)が、仕様書docs/07のプロンプト全文と
1文字・改行位置まで一致することを検証します。プロンプトの変更はAIの挙動を変える
仕様変更なので、「変更したければ設計書とこの試験の両方を意識して直す」ようにする
番人です。第3章の「規律はテストで守る」と同じ発想が、文章の定数にも使われています。

## 5.9 この章の再統合

- LLMは律儀でない協力者。LATCHは境界で必ずPydantic検証を通し、検証に落ちた
  出力はシステムの内側に入れない
- 失敗は「利用者起因(422・フォームへ)」と「システム起因(503・再試行へ)」に
  切り分けられる。判定材料は「例外が上がったか、検証を通ったか」の2点だけ
- StubLLMは「応答・失敗・遅延を演じ分けられる役者」。実プロバイダ確定(T1)まで
  開発が止まらないのは、この切り株のおかげ
- Protocolで「LLM非依存」をimportの形で強制する。形が同じなら呼べる、は
  差し替え可能性を型にしたもの
- 規則違反の出力は捨てずに正規化し、注意表示を付ける(黙って降格させない)。
  補完の規則も単一実装に一元する。ずれる余地を最初から作らない
- 「なぜDBを1本だけ読むのか」は設計書に得失つきで記録されている。便利さより
  判断の根拠が先

第4章・第5章で、LATCHの3層の型と「信頼できない入力への関所」という2つの軸を
見ました。次の実装(ws-3 intents CRUD)は、この2章の経路が合流する場所です。
parseで作った構造化データを、今度は検証して保存する。読み進める準備はできています。

## 5.10 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| LLM(大規模言語モデル) | 文章をやり取りするAI。指示に従う文章を返す |
| 自然文 / 構造化データ | 人が話すままの文章 / 項目に分解された機械処理向きのデータ |
| 必須3フィールド | category・time.start・location.name。欠けたら422 |
| スタブ / StubLLM | 本物の代役の部品 / 応答・失敗・遅延を演じるLLMの代役 |
| システムプロンプト | LLMに与える役割と規則の指示書(バージョン管理された定数) |
| Protocol | メソッドの形が一致すれば適合とみなす型の仕組み |
| 正規化 | 出力を約束の形へ整え直すこと(規則5違反の回復) |
| ng_unverifiable / warnings | 判定不能な除外条件 / それを利用者に伝える注意表示 |
| fail-closed | 怪しいときは停止する側に倒す方針(開き続けるfail-openの対) |
| ピン留め試験 | 期待値を実体と1文字まで一致させることで変更を検出する試験 |
| 補完の単一規則 | time.end=+3時間・人数2人等のデフォルトを決める唯一の実装 |

## 5.11 確認問題

1. parseの失敗が422になるときと503になるときの違いを、利用者の画面での分岐
   (フォーム/再試行)と結び付けて説明してください
2. StubLLMが「失敗を演じられる」ことは、どんな試験を可能にしますか。
   5.8の表のどの試験がそれを使っているか挙げてください
3. ParserOutputが「余分なキーは無視する」理由を、失敗の分類(欠損・値域外・
   パース不能のみ)と合わせて説明してください
4. `IntentParseService` のクラス本体がLLMGatewayを型として知らないことには、
   どんな意味がありますか。Protocolが果たす役割と合わせて答えてください
5. LLMが `negative_constraints` に値を入れて返してきたとき、LATCHはどう扱いますか。
   その挙動が「黙って降格させない」とどう対応するか説明してください
6. parseが保存しない設計である理由と、それでもDBを1本読む理由を
   それぞれ説明してください
