# 第13章 お金を払う判定と、信用しない作法: Layer 4 Jevとフォールバック切替

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第12章(特に12.6のJevCostGuardとINCR先行・12.7の日付キー切替)・
  第10章(10.2の正規化テキストの思想・10.5の2フェーズ構成・10.8の_kick_embedding)・
  第5章(5.2のGatewayと差し替え・5.3のLLM出力の境界検証)・第11章(11.2の
  LAYER1_WHERE・11.5の同点崩し・11.6のmatch_candidates)・第6章(6.4のD-04降格)・
  第9章(9.7の再試行と再配信)
- この章を読み終えるとできるようになること:
  - Layer 4 Jevが「1ペア1回の呼び出しで両方向の受け入れ確率を聞く」層であることを、
    課金と回数上限の関係ごと説明できる
  - System Oneの質問型 noul(確率)と score(段階)の違いと、7つの質問の内訳を
    コードで追える。理由(reason)の自由文を生成しない設計の意味を説明できる
  - 正規化テキストを `build_jev_text` で作り、何を送り何を送らないか説明できる
  - 返ってきた答えの検証(キー同値性・値域・/4正規化)と「再試行しない」判断を
    第5章のParser検証との対応で説明できる
  - フォールバック切替の条件(429・529・timeout・接続障害)と、切替しない条件
    (400系・出力検証失敗)を区別できる。SDK再試行を無効化する理由を説明できる
  - JevWorkerの2フェーズ構成と、_kick_jevの位置(コミット後・ack前)を追える
  - K_j=8の選択・同一評価世代スキップ・H再検証・skip_reasonの再選択規則を、
    第11章・第12章の規律の延長として読める
- 対応コード: `backend/src/latch/llm/jev.py`・`typesafe.py`・`anthropic_jev.py`・
  `gateway.py`・`stub.py`・`jev_smoke.py`・`worker/jev.py`・
  `worker/matching/layer4.py`・`worker/main.py` のM2 ws-5追加分
- 設計の根拠: `docs/plans/M2/ws-5-design.md`(特に§2.2の切替表・§2.5の選択と
  Guard契約・§2.6の世代スキップ・§2.7のskip_reason)と
  `docs/plans/M2/ws-5-report.md`。本文の「NN §X」は `docs/NN-*.md` の第X節を
  指します(07 §4=Jevの入出力と切替・06 §5=Layer 4の規定・05 §2=jev_result)
- 次に読むもの: `concepts/14-latch-layer5.md`(第14章。この章で記録したjev_resultを
  消費してスコアと提案を作るLayer 5 LATCH Engineです)

## 13.1 20件に残った相手へ、お金を払って最後の質問を投げる

第12章の終わりで、候補は「安上がりな審査を通った上位20件」として帳簿
(match_candidates)に並び、statusはpendingのままでした。この章の層は、その
pending行を1件ずつ消費します。

Layer 4 **Jev**(ジェブ)は、AIに「この2人は本当にマッチするか」を聞く層です。
聞き先はTypeSafe AI社の **System One**(システム・ワン)——「質問を送ると、
その答えを数値で返す」ことに特化した判定APIです。文章を書かせる汎用AIとは
違い、確率と段階を答えさせることに作り込まれた専用の判定モデルで、LATCHは
その中の **jev-1.13.0** という版を固定して使います(`llm/jev.py:17` の
`JEV_MODEL`)。

版を固定する理由は、第11章・第12章で積んできた「決して揺らない並び」と同じ
発想です。判定モデルの版が替われば、同じペアに違う答えが返り得る。LATCHは
「モデル更新時はバージョンを上げて日本語評価をやり直してから切り替える」という
約束(07 §1)でこれに備えます。便利な `jev-latest` のような「最新を指す名前」
(エイリアス)は使いません。今日と明日で指す先が変わる名前を固定値にすると、
約束が守れなくなるからです。

呼び出しのかたちもdocsが決めています(06 §5)。**1ペアにつき1回**のAPI呼び出しで、
両方向の質問を同時に聞きます。「AさんはBさんとの成立に応じるか」「BさんはAさんとの
成立に応じるか」——受け入れは往々にして非対称なので(AはいいがBは遠慮したい、は
普通に起こる)、方向を分けて聞きます。この1回の呼び出しが、第12章12.6のカウンタで
数えていた「1実行回数」の正体です。1ペアに1回と決めてあるので、カウンタの設計と
呼び出しの設計が噛み合います。

お金がかかる処理を、信用のおけない相手(外部API)に頼る。この章の読みどころは、
そこを囲む**作法**のすべてが、この教科書で既に登場した規律の再適用であることです。

| 心配ごと | 囲う道具 | 初出 |
|---|---|---|
| 入力に生の文章を送りたくない | 正規化テキストだけを作る | 第10章10.2 |
| 返事が壊れているかもしれない | 境界で機械的に検証する | 第5章5.3 |
| 呼び出しが暴走しないように | 番人(INCR先行のカウンタ) | 第12章12.6 |
| 外部APIをDBトランザクションに入れない | 2フェーズ構成 | 第10章10.5 |
| 失敗したら記録を残して回収する | 再配信と冪等ガード | 第9章9.7 |
| 誰に何を送ったか後で分かるように | 送信記録 | 第9章・第5章 |

新しく登場する工夫は2つ、「断られたら別のAIに同じ質問をする」切替(13.5)と、
「同じペアに二度払わない」評価世代スキップ(13.7)です。この2つも、まず道具を
確認してから順に読んでいきます。

## 13.2 質問は7つに固定する: 確率を聞くnoulと、段階を聞くscore

Jevに送る質問は、`llm/jev.py:19` の `JEV_QUESTIONS` という定数に全部で
7つ、辞書の形で書かれています。まず実物を1つ見ます。AさんがBさんを受け入れる
確率を聞く質問です。

```python
    "would_a_accept_b": {
        "type": "noul",
        "instructions": {
            "a": "`state.intent_a`",
            "b": "`state.intent_b`",
            "question": "`a`の作成者が`b`との成立を提示されたときyesと答える確率。"
            "`a`の[hard]条件を満たすか、`a`の[soft]条件と判定不能NG条件に"
            "触れないことを含めて判定する。`b`が`a`の[hard]条件と意味的に"
            "矛盾する場合(予算感の著しい乖離が食事内容を成立させない等)は"
            "yesから遠ざける",
        },
        "criteria": {
            "true": "成立し得る提案である",
            "false": "成立しない提案である",
        },
    },
```

(`llm/jev.py:20` から)

System Oneの質問には型が2つあります。**確率を聞く型**と**段階を聞く型**です。
System Oneの用語では、確率を返す型を **noul**(ヌール)、段階を返す型を
**score**(スコア)と呼びます。

- **noul** は「〜である確率」を0〜1の数値で返させます。上の質問なら、
  「Aさんがyesと答える確率 0.83」のような答えが返る
- **score** は5段階(0〜4)のどの段階に当たるかを返させます。各段階に意味を
  与える文章の並び(`criteria` のリスト)を質問に添えるのがこの型の作法です

7つの質問の内訳はこうなっています(`llm/jev.py:103` の `NOUL_KEYS` と
`SCORE_KEYS` がまさにこの区分です)。

| 質問キー | 型 | 何を聞くか |
|---|---|---|
| would_a_accept_b | noul | Aの作成者がBとの成立にyesと答える確率 |
| would_b_accept_a | noul | Bの作成者がAとの成立にyesと答える確率 |
| latent_yes | noul | どちらかが明示していないが、記述の範囲内でYESになり得る可能性 |
| purpose_fit | score | 目的・カテゴリの適合度(飲みたい×食べたいのズレ等) |
| mood_fit | score | 雰囲気・軽さの適合度(「軽く」×「がっつり」等) |
| timing_fit | score | 時間帯・所要時間の適合度 |
| social_fit | score | 人数・社会的文脈(立場・関係性)の適合度 |

latent_yes(潜在的なyes)は少し分かりにくいので例で掴みます。「焼肉に行きたい」と
「今日は肉系ならどこでもいい」——後者は焼肉を希望してはいませんが、焼肉を提示され
れば受け入れ得る。明示された希望の重なりだけで測ると零れてしまう「実は成立する」
可能性を聞く質問です(07 §4)。

読みどころを3つ挙げます。

1つめ、`instructions` の中に `` `state.intent_a` `` のような**バッククォートで
囲んだ参照**があります。System Oneへの入力(state)に名前で触る書き方で、質問の
文面とデータとが混ざらないよう、参照を記法で分離します。2つめ、7質問は
**1リクエストにまとめて**送ります。1質問ずつ7往復すると費用も時間も7倍です。
質問名(辞書のキー)はLATCH側で自由に付けられ、応答は同じ名前で返る——だから
「どの質問の答えか」の対応付けが壊れません。3つめ、文言の細部(criteriaの
言い回しなど)はdocs 07 §4の確定値をそのまま定数にしたもので、変更はdocsの
改版を伴う約束です。定数を書き換えても気づけないように、第5章の
`PARSER_SYSTEM_PROMPT` と同じ**全文ピン試験**(定数の全文を試験の期待値に
貼って、1文字でも変わったら赤になる型の試験)で守っています。

最後に、この章でいちばん肩の力を抜いてよい設計を1つ。**理由の文章は生成しない**
と決まっています(07 §4)。質問は「値を返せ」だけ、判定の根拠文は要りません。
保存する `jev_result` にも自由文は入らない。判定器の説明文は読み手を納得させ
ますが、機械の処理には使えず、検証もできず、その分のトークン課金も発生します。
「値だけ。根拠は人間がこの教科書で読む」——割り切りの良い設計です。

## 13.3 外へ出すのは正規化テキストだけ: 生の文章は一度も出ない

Jevへの入力を作るのが `build_jev_text` です(`llm/jev.py:134`)。第10章の
Embeddingと同じ規律がここでも通ります。**ユーザーが打った生の文章(raw_text)は
外のAPIへ一切送らない**。送るのは構造化データから組み直したテキストだけです。
入力のデータクラス `JevTextInput` にraw_textの欄がそもそもないのは、その約束を
型で釘刺す工夫です(第10章の「構造的ピン」と同じ語)。

出力がどんなテキストになるか、実際に作ってみます。飲みのIntentの例です。

```text
Intent A:
[hard] category: drinking
[hard] time: 2026-10-03 20:00–23:00
[hard] location: 天文館周辺 半径2km
[hard] participants: 2–4人
[hard] budget_max: 5000円
[soft] 軽く飲みたい
[soft] 会社関係の人は避けたい(システムで判定不能)
```

行の先頭に付いた `[hard]` と `[soft]` は、この条件が**絶対条件か希望か**の
区別です。Hard ConstraintはLayer 1のSQLで機械的に判定できるもの(第11章11.2)、
softは「できれば」の希望文言(第5章)。判定AIに「この条件は絶対です」と教える
ための目印です。softの2行目にある「(システムで判定不能)」は、第6章6.4で見た
NG条件の降格の印です。「会社関係の人は避けたい」は機械では判定できないので
Layer 1を通しましたが、AIには「判定不能なNG条件」として伝わる必要がある——
そのために付ける注釈です。

行は上から固定順で、**内容が存在する行だけ**を出します。予算の指定なし
(budget NULL)なら `budget_max` の行は丸ごと省略です。「制約なし」を行として
書くと、かえって「0円」と誤読される心配があります。無いことは書かない、が
安全です。送らない情報も決まっています。visibility・notification_level(第6章)・
alcohol_involved・category_secondary・そしてraw_text。判定に不要なもの、
プライバシー的に送る理由のないものは、行として存在しません
(07 §4・`build_jev_text` のdocstring)。

時刻の表示は `2026-10-03 20:00–23:00` のように、日付は開始側にだけ付きます。
タイムゾーンの付き方や終了時刻の補完(始まり+3時間)といった**算術はすべて
コード側**で済ませて、テキストは読むだけにしてあります。判定AIの公式な
主訓練言語は英語で、日本語は同等に扱われる保証がないため(07 §4の日本語リスク)、
計算をAIにさせず、文章も最小構成に保つ——送るものを減らすことが、揺らぎを
減らすことになる、という判断です。

こうして作った2つのテキストは、`state` という1つのJSONに
`{"intent_a": <Aのテキスト>, "intent_b": <Bのテキスト>}` の形で納めて送ります。
どちらのIntentがAになるかは、帳簿の行で既に `intent_a_id < intent_b_id` と
正規化された順(第11章11.6)をそのまま使います。UUIDの大小で機械的に決まるので、
誰が起点になったかでA/Bが入れ替わる心配がありません。

## 13.4 返ってきた答えは、信用する前に機械が検査する

第5章5.3で問うたことを、もう一度問います。LLMは「この形式で答えよ」という指示に
従う保証がない相手です。実際、System Oneの応答は質問と同じ名前の `answers`
という辞書で返りますが、キーが欠けている・値が範囲を超えている・型が違う、
といった壊れ方が現実に起こりえます。DBに例えれば、制約を守らないINSERTを
送ってくる相手。人間の世界なら「報告書は確認してから回す」、システムでも同じで、
**境界で必ず検証を1本通す**のです。

その1本が `validate_and_normalize` です(`llm/jev.py:199`)。検査は4つ。

1. **キーの同値性** — answersのキーが、質問の7キーと完全一致するか。
   欠けていても、余分があっても不正
2. **noulの値域** — would_a_accept_b・would_b_accept_a・latent_yesは
   0〜1の数値か
3. **scoreの値域** — 4つの適合度は0〜4の数値か(System Oneは段階の
   確率加重をfloatで返すので、1.035のような端数が正当な値です)
4. **confidenceの値域** — scoreに付いてくる確信度は0〜1の数値か、
   または無しか(noulには付きません)

数値の検査は `bool` をわざわざ除外しています(`llm/jev.py:184` の
`isinstance(value, bool)` の判定)。Pythonでは `True` が数値として扱われて
しまうため、「1」が欲しい場所に真偽値が来ても素通りする隙間を、先に塞いで
います。防御的な検証の良いお手本です。

検査を通ったscoreは、**4で割って**0〜1へ正規化します。分母が4である理由を
簡単に言うと、段階が「0,1,2,3,4」の5段階だから最大の水準は4であり、4で割って
はじめて値が0〜1に収まるからです(05 §2の「正規化値」と整合します)。5で割ると
上限が0.8で頭打ちになり、docsの文言と合わなくなります。1.035なら
1.035÷4=0.25875。正規化した値は丸めず、floatのまま記録します(第12章12.4の
「丸めない」規律と同じです)。

検査を通らない応答は `JevOutputInvalidError` を送出します。ここが第5章の
Parserと少し様子の違うところで、興味深い判断です。**再試行をしません**。
しかも後述のフォールバックLLMへも切り替えません。出力の形が壊れているのは、
「混雑していて一時的に答えられなかった」のような問い直せば直る失敗ではなく、
**実装の不整合**(質問の定義と応答の解釈が噛み合っていない等)の疑いがある
からです(07 §4)。問い直して偶然を待つより、失敗として記録し、人間が
ログで気づけるようにする。14行で済む防御の代わりに、隠れた不整合を永遠に
抱え込むリスクを選ばなかった、という割り切りです。

検証を通った応答は、帳簿の `jev_result` 列(JSONB)へこの形で保存されます
(05 §2。providerとmodelは13.6で出てくる実行の側が付けます)。

```json
{
  "would_a_accept_b": 0.83,
  "would_b_accept_a": 0.71,
  "jev_5axis": {
    "purpose_fit": {"value": 0.25875, "confidence": 0.8},
    "mood_fit":     {"value": 0.5, "confidence": 0.6},
    "timing_fit":   {"value": 0.5, "confidence": 0.7},
    "social_fit":   {"value": 0.75, "confidence": 0.6},
    "latent_yes":   {"value": 0.4, "confidence": null}
  }
}
```

5軸の適合度は、Layer 5(次の単位)がスコアを計算するときの**判断材料**で、
当人同士の受け入れ確率(would_*)とは別枠で保存されます。latent_yesは確率なので
正規化不要、confidenceは元々付きません(null)。

## 13.5 第一候補が断られたら、別のAIに同じ質問をする

ここまでの読みだと、Jevの呼び出しは「System Oneへ送って検証して終わり」に
見えます。実際の呼び出しは、**耳の聞こえない電話のとり継ぎ**のように組まれて
います。これがこの章の新しい工夫の1つ目、**フォールバック**(fall back=後ろへ
下がる、の意。予備へ切り替えること)です。

第一候補(TypeSafeのSystem One)は、断られ方の種類が分けられます。

| 第一候補の失敗 | 意味 | LATCHの反応 |
|---|---|---|
| 429(レート制限) | 「今は多すぎる。後で」 | **フォールバックへ切替** |
| 529(混雑) | 「サーバが手一杯。後で」 | **フォールバックへ切替** |
| timeout(6秒) | 「間に合わなかった」 | **フォールバックへ切替** |
| 接続障害(DNS・拒否等) | 「電話がつながらない」 | **フォールバックへ切替** |
| 400等のその他HTTPエラー | 「頼み方自体がおかしい」 | 切替せず失敗として伝播 |
| 出力検証失敗(13.4) | 「答えの形が壊れている」 | 切替せず失敗として伝播 |

上の4種に共通するのは、**質問は悪くないが、今この相手は答えられない**という
状況です。答えを待ち続けるかわりに、同じ質問を**別のAI**に持ち込みます。
切替先は汎用のLLM、Anthropic社のClaude(`claude-sonnet-5`)です
(`llm/anthropic_jev.py:24`)。

面白いのは、フォールバック側の作り込みです。汎用LLMに判定を頼むとき、LATCHは
「判定AI」の振る舞いを**外から全部与えます**。第5章でParserにやったのと同じ
構成です。システムプロンプト(7質問の意味と値域、値だけを出す約束)を渡し、
出力は **structured output**(構造化出力。JSONの形をスキーマで指定して、
その形でのみ応答させる仕組み)で受け取ります。スキーマは `FallbackJevAnswers`
というPydanticモデルから起こしていて、7キーすべてが数値です
(`llm/anthropic_jev.py:60`)。

```python
class FallbackJevAnswers(BaseModel):
    """フォールバック応答のスキーマ(7キー全てnumber)。

    min/max等の数値制約を付けない — A3(anthropic.pyの_STRIP_KEYS知見:
    Anthropic structured outputsが数値制約を拒否する)。値域検証は
    validate_and_normalize(検証失敗=切替条件外の縮退)が担う。
    """

    would_a_accept_b: float
    would_b_accept_a: float
    purpose_fit: float
    mood_fit: float
    timing_fit: float
    social_fit: float
    latent_yes: float
```

(`llm/anthropic_jev.py:60` から)

このdocstringが指すのは第5章の知見です。Anthropicのstructured outputは
「0以上1以下」のような数値の範囲指定を受け付けないため、スキーマは「数値で
ある」以上のことを要求しません。では範囲は誰が守るのか——13.4の
`validate_and_normalize` です。**スキーマは緩く、検証は厳しく**。これは
守りの二重がえに見えますが、実際はそれぞれの道具の得手不得手に役割を
合わせた分担です。
応答のJSONは、System Oneと同じ「7キーのanswers」の形に組み立て直されてから
同じ検証関数を通るので、第一候補とフォールバックで検証の質が揃います。

切替の判定はGatewayの中にあります(`gateway.py:117` の `judge_pair`)。

```python
        try:
            ...(中略: 第一候補TypeSafeの呼び出し。検証を通ればJevJudgmentを
            返して終わり)...
        except (
            LLMTimeoutError,
            LLMRateLimitError,
            LLMOverloadedError,
            LLMConnectionError,
            _FirstCandidateSkippedOpen,
        ):
            pass  # 07 §4の切替条件4種+breaker開放中のスキップ(第16章)。
            # LLMProviderError・JevOutputInvalidErrorは伝播
        return await self.call_jev_fallback(
            intent_a=intent_a, intent_b=intent_b, intent_ids=intent_ids
        )
```

(`llm/gateway.py:132` から。中略部分は実際にはcircuit breakerへの確認と
第一候補呼び出し・検証とJevJudgment組み立て)

捕捉する例外が**切替条件の4種だけ**で、400系(`LLMProviderError`)と出力検証失敗
(`JevOutputInvalidError`)は捕捉対象に入っていません。だからそれらはそのまま
呼び出し元へ伝播します。5番目の `_FirstCandidateSkippedOpen` は切替条件ではなく、
breakerが「しばらく第一候補を呼ばない」と判断したときに開放の合図として使う
内部例外です(この節の末尾と第16章で触れます)。else節もif文もなく
「例外の型で分岐する」この書き方は、
「どう失敗したか」の分類を例外クラスに一任する設計です。4種の例外は
`llm/typesafe.py:84` でSDKの例外から翻訳されています(TypeSafeRateLimitError→
LLMRateLimitError、status=529→LLMOverloadedError等)。Gatewayは送信先の
事情を知らず、型だけを見る——第5章のGatewayの思想がここにも貫かれています。

切替が起きたとき、送信記録はどうなるでしょう。1回目(typesafe宛・失敗)と
2回目(anthropic宛・成功)で**2件**の記録が残ります(第9章で読んだ
`latch.llm.send` 構造化ログ。宛先(destination)で区別)。誰に何を送ったかは、
成功・失敗ともに後から追えます。

切り替えない判断と対で、**待たない判断**もあります。TypeSafeのSDKは、429や
529を受けると既定で「少し待って自動で再送」する作りです。LATCHはこれを
`RetryPolicy(max_retries=0)` で無効化しています(`llm/typesafe.py:72`)。
フォールバック側のAnthropic SDKも `max_retries=0`。理由は単純で、**同じ相手を
待つお金と時間は払わない**と決まっているからです。timeoutは双方とも6秒
(`TIMEOUT_JEV_S = 6.0`。`gateway.py:40`)で、最悪でも第一候補6秒+フォールバック
6秒の12秒で処理が終わります。再試行を許すと、6秒待ってさらに数秒待つ
ことになり、遅延とコストの二重払いです。07 §4の「SDK既定のbackoff retryは
無効化・再試行なし」は、この実装の根拠になっている確定値です。

そして、フォールバックまで失敗したら(双方のAPIが同時に調子を悪くしたら)。
この**双障害**は例外としてLayer 4へ伝播し、ペアは「後でやり直す」保留
(status=skipped、理由llm_failure)として記録されます(06 §8 D-15の**縮退**。
13.6で見ます)。単発の失敗への守りがこの節の切替なら、失敗が**続く**ときの
守りは別枠で用意されています。**circuit breaker**(サーキット・ブレーカー。
遮断器)——直近60秒の窓で失敗や遅さが続いたら、しばらく第一候補を呼ぶのを
やめる仕組みです(ws-8で `judge_pair` に組み込まれました。上の引用のexcept節に
`_FirstCandidateSkippedOpen` が紛れていたのがそれ)。開いても判定はフォール
バックで続くので、切替の延長線上にある守りと読めます。仕組みの全部は
第16章で読みます。

## 13.6 API呼び出しはトランザクションの外で: 第10章の2フェーズをもう一度

では、この高価で信頼のおけない呼び出しを、いつどこで実行するのでしょう。
出発点は第10章10.8と同じ場所です。Stage1がembedding_completedのEventを
processed(またはduplicate)にした**直後**、ackを返す**前**。Workerはそこで
EmbeddingWorkerを呼んでいました。今回、その隣に1行増えています。

```python
                    await self._kick_embedding(*result.triple)
                    await self._kick_jev(*result.triple)
```

(`worker/main.py:221` から)

`_kick_jev`(`main.py:268`)は、Eventの種類がembedding_completedのときだけ
JevWorkerを呼ぶ小さな門番です。作りは `_kick_embedding` のコピーのような形で、
これも意図的です。**配線の形を既存の隣に合わせる**ことで、読む側の学習成本を
下げ、将来の変更箇所も「並びのこの辺」と予測しやすくなります。
(この章を書いた時点ではJevWorkerだけを呼んでいましたが、ws-6でLayer 5の
LatchEngineが同じ `_kick_jev` の続きとして直列実行されるようになりました。
続きは第14章です)

第12章12.5を思い出してください。Layer 1〜3は、EventをprocessedにするUPDATEと
**同一のトランザクションに同乗**しました。DB内の処理だから失敗すればロール
バックでき、再試行5回の経路に載る——そういう性質でした。Layer 4は逆の判断です。
**stage1のトランザクションには入れない**。外部API呼び出しが1イベントで最大
8回、最悪で1回あたり12秒。トランザクションをそれだけ握り続けるとDBが詰まり、
さらにstage1の再試行でロールバックされた後にJevを呼び直し、**課金の二重払い**が
構造的に起こります(ws-5設計 §2.1の案Aの不採用理由)。呼び出しの性質(外部APIか、
DB内か)で場所を選ぶ——第10章でEmbeddingが学んだ教訓の、もう一度の適用です。

実行の本体 `JevWorker`(`worker/jev.py:153`)は、第10章のEmbeddingWorkerと
同じ**2フェーズ構成**です。

```text
フェーズ1(短いトランザクション): 起点を読み、閉じるべき行を閉じ、評価対象を選ぶ
フェーズ2(どのトランザクションにも属さない): ペアごとに
  番人へ確認 → 許可されれば judge_pair(API呼び出し)→ 実行回数を計上
フェーズ3(短いトランザクション): 結果をガード付きUPDATEで書く
```

フェーズ1の「評価対象を選ぶ」は13.7で詳しく読みます。フェーズ2の最初の一歩が
第12章の番人です。`guard.request_execution(起点intent_id, 起点user_id)`。
denyならAPIを呼ばず、その行にdeny理由を書いて次のペアへ。許可が出たら
`judge_pair` を呼び、返ってきた判定をフェーズ3で書き込みます。**実行回数の
計上は、実際にAPIを呼んだ経路だけ、1回**。第一候補が成功したらtypesafe_jevを
1回、切替の末フォールバックで成功したらfallback_llmを1回数えます
(`worker/jev.py:301` の `_record`)。切替の過程で2回呼んだから2回計上、とは
しません。カウンタの意味は「いくら払ったか」ではなく「何回の判定が終わったか」
だから、最後に答えが返った経路を1回と数えるのが筋です。

フェーズ3のUPDATEには、第10章と同じ**ガード**が付いています。

```sql
UPDATE match_candidates
SET jev_result = CAST(:jev AS jsonb), status = 'evaluated',
    skip_reason = NULL, updated_at = :now
WHERE id = CAST(:row_id AS uuid) AND jev_result IS NULL
```

(`worker/jev.py:47` の `_COMPLETE`)

`jev_result IS NULL` がガードです。すでに誰かが結果を書いていたら、このUPDATEは
0行に当たって何も変えません。なぜ必要か。WorkerはEventの再配信で同じ処理を
もう一度走らせることがあります(at-least-once。第9章)。1回目で評価が済んだ
行を2回目でもう一度APIへ送ったら、同じ判定に2度お金を払うことになります。
「結果がまだ無い行だけが対象」という条件をUPDATE自体に持たせることで、再配信で
走り直しても、**終わった分は構造的に飛ばされる**のです。

失敗したときの振る舞いは、失敗の種類で綺麗に分かれています(ws-5設計 §2.9)。

| 失敗 | 扱い | 理由 |
|---|---|---|
| 番人のdeny(予算・回数上限) | skipped+理由を書く(例外にしない) | 後で再評価すべき保留 |
| LLM失敗(双障害)・検証失敗 | skipped+理由を書く(例外にしない) | 同上(D-15の縮退) |
| DB書き込み失敗・番人のRedis失敗 | 例外を伝播させる | ackされず再配信→やり直し |

denyであれLLM失敗であれ「この1ペアが今評価できない」ことは、システム全体の
失敗ではありません。スキップとして記録し、次のペアへ進む。一方、DBにもRedisにも
書けない状態は、この先の処理も同じ運命を辿るので、素直に例外で止まって再配信を
待つ。**諦める失敗と、引き返す失敗を区別する**——この線引きが、この章の例外
処理のすべてです。

## 13.7 同じペアに二度払わない: K_j=8・評価世代・スキップの回収

最後に、フェーズ1の「評価対象を選ぶ」を読みます。ここには3つの決まりごとが
1本のSQLに押し込められています。

1つめは**回数の上限**です。1イベントの処理でJevを実行してよいのは
**上位8件**まで(`K_J = 8`。`layer4.py:28`。06 §8 D-24)。20件のpendingの
うち、安上がりな審査(cheap_judge_score)の点数が高い順に8件だけがお金を払う
資格を得て、残りは保留のまま次の機会を待ちます。

2つめは**同じペアに二度払わない**ことです。Intentは更新されるたびにversionが
上がり、評価がやり直されます(第6章・第9章)。しかし、表示名を少し直しただけの
更新で、中身が同じペアにもう一度お金を払うのは無駄です。そこで「同一の評価
世代」——起点と相手の**両方のversionが前回と同じ組み合わせ**——で既に結果が
ある行は、選択から外します。それがWHERE句の `mc.jev_result IS NULL` です。
結果がすでに書いてある行は、何度評価が回ってきても**聞き直さない**。再評価では
Layer 1の条件だけを機械的に再確認(Hの再計算)し、Hard Constraintを満たさなく
なったペアだけstatus=closedへ閉じます。AIには聞き直さずに、です
(`layer4.py:244` の `close_broken_pairs`。このSQLもLayer 1の条件文字列
`LAYER1_WHERE` をそのまま埋め込んであるので、パイプライン本体のLayer 1と
再検証のLayer 1が食い違う隙間が構造的にありません——第11章11.2の資産の再利用)。

3つめは**保留の回収**です。13.6でdenyやLLM失敗の行はskippedとして残ると
書きました。では、それらはいつもう一度選ばれるのでしょう。ここで
**skip_reason**——その行がなぜスキップされたかの理由列——が効いてきます
(マイグレーション0003で追加された列です)。同じ「保留」でも、待てば解ける
ものと、待っても解けないものがあるからです。

| skip_reason | もう一度選ばれるのは |
|---|---|
| llm_failure・invalid_output(API障害系) | **いつでも**(次のイベントで即時) |
| intent_daily・user_daily・global_daily(日次予算系) | **JST 0時を過ぎてから**(予算がリセットされる) |
| global_monthly(月次予算系) | **暦月初のJST 0時を過ぎてから** |

予算のリセットは第12章12.7で読んだとおり、日付の変わった鍵に自動で切り替わる
仕組みでした。「この行は昨日予算切れでスキップされた」ことは、行の
`updated_at` が本日のJST 0時より前かどうかで分かります。日付の境界はClockから
導くので、リセットのジョブが動いていなくても、次のイベントが来れば自然に
「昨日の保留」は選び直される——保留の回収も、リセットと同じ発想で待たずに
担保されているのです。

選択の並び順も第11章・第12章と同じ規律です。cheap_judge_scoreの降順、同点は
相手のintent_id昇順(第12章12.4と同じ同点崩し)。並べるのはDB側の1問
(`ORDER BY`)で、Python側はその結果を上から処理するだけ。この章で読んだ時点の
実装では `LIMIT 8` もSQLが持っていましたが、グループマッチ(第15章)の追加で
8の枠の切り分け方が「1対1最低4件+残りをグループへ」という配分に変わったため、
**打ち切りはPythonの純関数**(`select_jev_targets`・`layer4.py:196`)が担い、
SQLは並び順と資格の判定に専念する形になりました。なお、**denyされても補充しません**。
予算のdenyは次のペアでも同じ理由でdenyされるので、枠を繰り上げて何かを
追加で評価しても、無駄にカウンタを消費するだけです(design §2.5-4)。上限に
当たった日は、潔く翌日へ回す設計です。

ここまで読むと、第12章の番人が誰に課税するのかも具体的になります。課税の
対象は**起点となったIntentと、その作成者**だけです。相手側として評価される
消費は、起点側のイベント処理に属しているので二重に数えません。この約束は
「1Intent 40回/日・1ユーザー120回/日」の予算が破られないための解釈記録として
設計書に残っています(ws-5設計 §5-6)。

## 13.8 自分で確かめる

`cd backend` してから動かします。ここにあるものはすべて**お金がかからない**
演習です(スタブとfakeredisで完結します)。

1. 正規化テキストを自分の手で作る。13.3の出力を再現します

   ```bash
   uv run python - <<'EOF'
   from datetime import datetime

   from latch.core.clock import JST
   from latch.llm.jev import JevTextInput, build_jev_text

   inp = JevTextInput(
       category_primary="drinking",
       structured_data={
           "location_name": "天文館周辺",
           "soft_constraints": [
               {"text": "軽く飲みたい"},
               {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
           ],
       },
       participants_min=2,
       participants_max=4,
       time_start=datetime(2026, 10, 3, 20, 0, tzinfo=JST),
       time_end=datetime(2026, 10, 3, 23, 0, tzinfo=JST),
       budget_max=5000,
       geo_radius_m=2000,
   )
   print(build_jev_text(inp, label="Intent A"))
   EOF
   ```

   期待される出力(2026-09-29に実行して確認しました):

   ```text
   Intent A:
   [hard] category: drinking
   [hard] time: 2026-10-03 20:00–23:00
   [hard] location: 天文館周辺 半径2km
   [hard] participants: 2–4人
   [hard] budget_max: 5000円
   [soft] 軽く飲みたい
   [soft] 会社関係の人は避けたい(システムで判定不能)
   ```

   `budget_max=5000` を `budget_max=None` に変えると `budget_max` の行が
   消えることも確かめてください(省略の規則)。

2. スタブで判定を流してみる。Gatewayは `llm_mode=stub`(既定)なら実APIを
   一切呼びません。返り値の形(JevJudgment)を掴みます

   ```bash
   uv run python - <<'EOF'
   import asyncio

   from latch.core.clock import SystemClock
   from latch.llm.gateway import build_worker_gateway
   from latch.settings import Settings

   gw = build_worker_gateway(SystemClock(), Settings())  # stub


   async def main() -> None:
       j = await gw.judge_pair(
           intent_a="Intent A:\n[hard] category: drinking",
           intent_b="Intent B:\n[hard] category: cafe",
           intent_ids=["a", "b"],
       )
       print("provider:", j.provider)
       print("model   :", j.model)
       print("result  :", j.result)


   asyncio.run(main())
   EOF
   ```

   期待される出力(2026-09-29に実行して確認しました)。

   ```text
   provider: typesafe_jev
   model   : jev-1.13.0
   result  : {'would_a_accept_b': 0.5, 'would_b_accept_a': 0.5, 'jev_5axis': {'purpose_fit': {'value': 0.5, 'confidence': 0.5}, 'mood_fit': {'value': 0.5, 'confidence': 0.5}, 'timing_fit': {'value': 0.5, 'confidence': 0.5}, 'social_fit': {'value': 0.5, 'confidence': 0.5}, 'latent_yes': {'value': 0.5, 'confidence': None}}}
   ```

   noulは0.5、scoreは2.0(=正規化すると0.5)がスタブの固定値です
   (`llm/stub.py` の `DEFAULT_JEV_RESPONSE`)。値が決まっているので、
   この先のLayer 5の試験も決定的に書けます。

3. 検証に壊れた答えをぶつける。13.4の防御を体験します

   ```bash
   uv run python - <<'EOF'
   from latch.llm.errors import JevOutputInvalidError
   from latch.llm.jev import validate_and_normalize

   good = {
       "model": "jev-1.13.0",
       "answers": {
           "would_a_accept_b": {"type": "noul", "noul": 0.83},
           "would_b_accept_a": {"type": "noul", "noul": 0.71},
           "latent_yes": {"type": "noul", "noul": 0.4},
           "purpose_fit": {"type": "score", "score": 1.035, "confidence": 0.8},
           "mood_fit": {"type": "score", "score": 2.0, "confidence": 0.6},
           "timing_fit": {"type": "score", "score": 2.0, "confidence": 0.7},
           "social_fit": {"type": "score", "score": 3.0, "confidence": 0.6},
       },
       "usage": {"input_tokens": 100, "output_tokens": 50},
   }
   print("正常系:", validate_and_normalize(good))

   bad = dict(good)
   bad["answers"] = {**good["answers"], "purpose_fit": {"type": "score", "score": 4.5}}
   try:
       validate_and_normalize(bad)
   except JevOutputInvalidError:
       print("値域超過:", JevOutputInvalidError.__name__)
   EOF
   ```

   期待される出力(2026-09-29に実行して確認しました)。

   ```text
   正常系: {'would_a_accept_b': 0.83, 'would_b_accept_a': 0.71, 'jev_5axis': {'purpose_fit': {'value': 0.25875, 'confidence': 0.8}, 'mood_fit': {'value': 0.5, 'confidence': 0.6}, 'timing_fit': {'value': 0.5, 'confidence': 0.7}, 'social_fit': {'value': 0.75, 'confidence': 0.6}, 'latent_yes': {'value': 0.4, 'confidence': None}}}
   値域超過: JevOutputInvalidError
   ```

   `purpose_fit` の `score` を `4.5` にしたのが値域超過です。1.035が
   0.25875へ正規化されていることも確かめてください(1.035÷4)。

4. 番人がdenyする瞬間を見る。第12章12.6のカウンタを、 fakeredis(Redisの
   内側の動きをメモリ上で再現する試験用の部品)で回します。1Intentあたり
   40回/日の上限に、41回目の要求がどうなるか

   ```bash
   uv run python - <<'EOF'
   import asyncio
   import uuid
   from datetime import UTC, datetime

   import fakeredis.aioredis

   from latch.core.clock import FakeClock
   from latch.worker.cost import JevCostGuard, JevCostStore

   NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-29 21:00
   IID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
   UID = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


   async def main() -> None:
       r = fakeredis.aioredis.FakeRedis(decode_responses=True)
       guard = JevCostGuard(store=JevCostStore(r), clock=FakeClock(NOW))
       decision = None
       for _ in range(41):
           decision = await guard.request_execution(IID, UID)
       print("41回目:", decision.allowed, decision.deny_reason)
       again = await guard.request_execution(IID, UID)
       print("42回目:", again.allowed, again.deny_reason)
       print("カウンタ:", await r.get(f"jev:intent:{IID}:20260929"))
       await r.aclose()


   asyncio.run(main())
   EOF
   ```

   期待される出力(2026-09-29に実行して確認しました)。

   ```text
   41回目: False intent_daily
   42回目: False intent_daily
   カウンタ: 42
   ```

   41回目でdenyされたあと、もう1回要求するとカウンタが42に増えています。
   **denyも消費する**(第12章12.6)の実物です。FakeClockの日付を変えれば
   鍵名の日付も変わり、カウンタが0から始まることも試せます(日付キー切替)。

5. 試験を読む。この章の内容が試験名にそのままなっています

   ```bash
   uv run pytest tests/unit/llm/test_jev_format.py -v          # 28件
   uv run pytest tests/unit/llm/test_typesafe_jev.py -v         # 13件
   uv run pytest tests/unit/llm/test_gateway_jev_switch.py -v   # 21件
   uv run pytest tests/unit/matching/test_layer4.py -v          # 19件
   uv run pytest tests/unit/test_worker_jev.py -v               # 25件
   ```

   (件数は2026-09-30に実行して確認しました)
   test_gateway_jev_switchの試験名を読むと13.5の切替表がそのまま並んでいます。
   test_worker_jevには「deny→skipped」「双障害→llm_failure」「冪等: 再実行で
   API不呼出」のような試験が並び、13.6の分岐表を1行ずつ確かめています

6. `rg -n "_kick_jev|EVENT_EMBEDDING_COMPLETED" backend/src/latch/worker` を
   実行する。main.pyの配線(188行目・235行目)とjev.pyにだけ現れることを確認
   する。Stage1はLayer 4のことを1行も知らない——12.5のmatching_hookと対比
   してください

実APIを1回だけ叩く `make jev-smoke` という検査もあります(1呼び出しで課金。
`.env` に `LATCH_LLM_MODE=real` と3つの鍵が必要。`FALLBACK=1` でフォールバック
側の直接呼び出し)。実行手順の位置づけは第10章のembed-smokeと同じ「緑でも本番は
落ちる」シリーズの予防接種ですが、**お金がかかるので教材の演習には入れません**。
興味が湧いたときに、自分の判断で実行してください。

## 13.9 この章の再統合

- Layer 4 Jevは、20件に絞った候補に**1ペア1回**のお金を払い、両方向の
  受け入れ確率と4軸の適合度を聞く層。判定APIはSystem Oneのjev-1.13.0固定
  (エイリアス不使用・版更新は評価のやり直しを伴う)
- 質問は7つに固定。確率を聞くnoul(would_a_accept_b・would_b_accept_a・
  latent_yes)と、5段階を聞くscore(目的・雰囲気・時間・人数の適合度)。
  **理由の自由文は生成しない**。文言はdocsの確定値を全文ピン試験で守る
- 外へ出すのは `build_jev_text` の正規化テキストだけ。raw_textは入力の型に
  そもそもない。絶対条件には[hard]、希望には[soft]、判定不能なNG条件には
  (システムで判定不能)の注釈。無い条件の行は書かない
- 返り値は境界で機械検査する(キー同値・値域)。scoreは最大水準4で割って
  [0,1]へ正規化。**検証失敗は再試行も切替もしない**(実装不整合の疑い)
- 第一候補が429・529・timeout・接続障害で断られたら、**同じ7質問を**
  フォールバックLLM(Claude)にstructured outputで答え直してもらう。
  400系と検証失敗は切替しない。SDKの自動再試行は無効化(待たない・二重に
  払わない)。送信記録は切替で2件。フォールバックも失敗したら縮退(保留)。
  失敗や遅さが60秒の窓で続くときはcircuit breakerが第一候補を呼ぶのを
  やめる(第16章)
- 実行はコミット後ack前の `_kick_jev` から、第10章と同じ2フェーズ。
  外部APIはトランザクションの外。結果は `jev_result IS NULL` のガード付き
  UPDATEで書くので、再配信で二度払いしない。deny・LLM失敗は記録して次へ、
  DB・Redis失敗は例外で引き返す
- 選択はcheap_judge_score降順・同点は相手id昇順・上限K_j=8。同一評価世代
  (両versionが同じ組)は**聞き直さない**(Hの再計算のみ)。skipped行は
  skip_reasonで回収時期が決まる: API障害系は即時、日次予算系はJST 0時越し、
  月次予算系は暦月初越し。denyされても補充しない

20件のpendingは、こうして「評価済み8件+保留」となり、1行ごとに
jev_resultが埋まっていきます。次章のLayer 5は、このjev_resultを材料に
スコア(L=H×MutualScore×C)を計算し、提案(proposal)を作る層です。お金を払って
得た数値が、ようやくユーザーに見える形になるのはそこからです。

## 13.10 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| Jev | Layer 4で使う判定モデル(System One上のjev-1.13.0) |
| System One | 質問を送ると数値の答えを返す、判定に特化したTypeSafe AIのAPI |
| noul | System Oneの質問型の1つ。0〜1の確率で答えさせる型 |
| score | System Oneの質問型の1つ。criteriaで段階を定義して答えさせる型 |
| criteria | score質問に添える、各段階の意味を定める文章の並び |
| latent_yes | 明示されていないが記述の範囲内でYESになり得る可能性を聞く質問 |
| jev_result | 1ペア分の判定結果を収めるJSONB列(would_*・jev_5axis・provider・model) |
| provider | 判定を実際に返した経路。"typesafe_jev"(第一候補)か"fallback_llm"(予備) |
| フォールバック | 第一候補の失敗時に予備の経路へ切り替えること |
| 双障害 | 第一候補もフォールバックも失敗すること。縮退(保留)になる |
| 縮退(D-15) | 判定を諦めて保留(status=skipped)にし、後の再評価に回すこと |
| structured output | JSONの形をスキーマで指定して、その形でのみ応答させる仕組み |
| 評価世代 | 起点と相手のversionの組。同じ組は再評価してもJevへ聞き直さない |
| K_j(=8) | 1イベント処理あたりのJev実行回数の上限(06 §8 D-24) |
| skip_reason | skipped行がスキップされた理由の列(マイグレーション0003で追加) |
| H再検証 | 再評価時にLayer 1の条件だけ機械的に再確認すること(LAYER1_WHERE再利用) |
| circuit breaker | 失敗や遅さが続いたら、しばらく第一候補を呼ぶのをやめる仕組み(第16章) |
| fakeredis | Redisの動きをメモリ上で再現する試験用の部品。実Redis不要 |

## 13.11 確認問題

1. 1ペアの評価でAPI呼び出しが1回なのに質問が7つある理由を、「往復」と
   「1実行回数」(第12章12.6)の言葉で説明してください
2. jev-1.13.0のような版の固定を、`jev-latest` のようなエイリアスで代替
   できない理由を、決定性(第11章11.5)との関係で説明してください
3. noulとscoreを、返ってくる値の形の違いで説明してください。latent_yesが
   noulである理由も考えてください
4. reason(判定の根拠文)を生成しない設計の利点を、検証・課金・保存の
   3つの面から挙げてください
5. build_jev_text がraw_textを入力に持たないことと、第10章10.2の正規化
   テキストの思想との関係を説明してください
6. scoreの正規化で4を分母にする理由を、5で割った場合に起きる不都合から
   説明してください
7. 出力検証に失敗したとき、再試行もフォールバックもしない理由を、
   「問い直せば直る失敗」と「実装不整合」の区別で説明してください
8. 429は切替するのに400は切替しない違いを、それぞれの失敗の意味から
   説明してください
9. JevWorkerのフェーズ3のUPDATEに `jev_result IS NULL` が付いている理由を、
   Eventの再配信(at-least-once・第9章)とお金の関係で説明してください
10. skip_reasonがllm_failureの行とintent_dailyの行で、再び選ばれる時期が
    違う理由を、D-15の回収経路と第12章12.7の日付キー切替から説明して
    ください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった
部分が、読み返すべき節です)
