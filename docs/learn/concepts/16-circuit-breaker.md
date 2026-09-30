# 第16章 呼ぶのをやめる判断: circuit breakerと続く障害への備え

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第13章(特に13.5のフォールバック切替・13.6の縮退と失敗の分岐表・13.7の
  skip_reasonの再選択)・第3章(ClockとFakeClock)・第6章6.5(状態遷移図)・
  第9章(9.4のdebounceの窓・9.7の再配信)
- この章を読み終えるとできるようになること:
  - 単発の失敗への切替(13.5)と「続く失敗」への対処が別の仕組みである理由を、
    時間と料金の両面から説明できる
  - circuit breaker(遮断器)の3状態——閉(closed)・開(open)・半開(half_open)——の
    移り方を、第6章の状態遷移図と同じ読み方で追える
  - 「開く」2つの条件(エラー率50%超・p95が6秒超)と「1回の失敗では開かない」
    しきい値の意味を説明できる。timeout呼び出しを6.0秒として記録する解釈も
  - `llm/breaker.py` の allow と record の契約を、Clock注入・deque・窓の
    追い出しとともに読める
  - 開放中に第一候補を呼ばず切替へ合流する実装(`_FirstCandidateSkippedOpen`)と、
    計上の範囲(検証失敗は成功扱い・フォールバックは計上しない)を説明できる
  - 単発→切替、継続→breaker、双障害→縮退という3層の守りの分担を、復旧後の
    再評価(13.7)まで通して説明できる
- 対応コード: `backend/src/latch/llm/breaker.py`・`gateway.py`・`stub.py` の
  M2 ws-8追加分
- 設計の根拠: `docs/plans/M2/ws-8-design.md`(特に§2.1の状態を持つ場所の判断・
  §2.2の状態機と判定規則・§2.11の「いまは作らない」一覧)と
  `docs/plans/M2/ws-8-report.md`。確定値の出典は `docs/06 §8 D-15 FR-10`・
  `docs/07 §1`・`docs/07 §4`
- 次に読むもの: `concepts/17-latch-response.md`(第17章。この章は第13章の直接の
  続きで、第14章・第15章を飛ばしてここに来ても大丈夫でした。第17章は
  第14章・第15章の内容を前提にするので、飛ばして来た人は先にそちらへ)

## 16.1 失敗が「続く」とき、切替だけでは足りない

第13章13.5で、第一候補(System One)が429・529・timeout・接続障害を返したときは
フォールバックLLMへ切り替えて判定を続けると学びました。あの仕組みは「1回の
呼び出しが失敗した」ことへの対応でした。では、失敗が**続いたら**どうなるでしょう。

想像してみてください。第一候補が1時間にわたってずっとtimeoutを返す状態です。
Workerは1イベントで最大8件のペアを評価します(13.7のK_j=8)。各ペアで、
第一候補を呼んで6秒待って失敗し、フォールバックへ切り替える。次のペアでも
また第一候補を呼びます。さっき失敗したばかりなのに、です。

この繰り返しは2つの無駄を生みます。

1. **時間**: timeoutが続くと、1ペアあたり最大6秒を「待ってだめ」に使う。
   8件なら最悪48秒の丸損です
2. **料金**: 第一候補よりもフォールバックの方が(汎用LLMなので)コストが高い。
   調子の悪い安い経路を試しては高い経路に流れ続けるので、障害中の判定費が
   ふくらみます

人間ならどうするか。明らかに相手がDownしているときは、「しばらくこの相手には
用を言いつけない。しばらくしたら1回だけ試してみて、直っていれば元に戻そう」
と判断するはずです。この判断を部品として実装したものが、この章の主役です。

## 16.2 ヒューズの考え方: 3つの状態で「呼ぶ・呼ばない・試す」を切り替える

まず現象を平易に言います。**しばらく第一候補を呼ぶのをやめて、時間が経ったら
少しだけ呼び直してみる仕組み**。ソフトウェアの世界ではこれを
**circuit breaker**(サーキット・ブレーカー。circuit=電気回路、breaker=開くもの。
日本語では**遮断器**やブレーカー)と呼びます。

名前の由来は家庭の配電盤です。電気を使いすぎるとブレーカーが落ちて回路が
「開」になり、電気の流れが止まります。これで家の配線が焼けるのを防ぐ。
ソフトウェアのcircuit breakerは同じ発想を外部APIへの呼び出しに適用します。
失敗が続けば呼び出しの回路を「開」にして、これ以上時間とお金を溶かすのを
止める。

breakerの動きは、3つの状態の移り方として書けます。第6章6.5でIntentの
draft/activeの**状態遷移図**を読みました。あれと同じ読み方です。

```text
              開く条件(16.3)                60秒経過後の
              エラー率50%超 or p95が6秒超     次の呼び出し
 ┌─────────┐ ─────────────────────▶ ┌─────────┐ ──────────▶ ┌─────────────────┐
 │ closed  │                         │   open  │             │ half_open       │
 │ (閉)     │ ◀─────────────────────  │ (開)     │ ◀────────── │ (半開)           │
 └─────────┘        試験が成功         └─────────┘  試験が失敗  └─────────────────┘
                    (窓もリセット)                  (また60秒待つ)   1回だけ呼ぶ
```

図の読み方を言葉でも書いておきます。閉じた(closed)状態で開く条件を満たすと
開いた(open)状態へ。開いたまま60秒が経過したあとの**次の呼び出し**で半開
(half_open)へ移り、その呼び出しが試験になります。試験が成功すれば閉じたへ
戻り(窓の記録も空にリセット)、失敗すれば開いたへ戻って、また60秒待ちます。

各状態の意味は次のとおりです。

| 状態 | 意味 | 第一候補を呼ぶか |
|---|---|---|
| **closed**(閉) | 普段の状態。何も起きていない | 呼ぶ |
| **open**(開) | 障害と判断した状態。呼ぶのをやめている | 呼ばない(判定はフォールバックで継続) |
| **half_open**(半開) | 様子見の状態。復旧したか確かめている | **1回だけ**呼ぶ |

注目してほしいのは、openの間も判定が止まらないことです。第一候補の代わりに
フォールバックLLMが答えを返すので、ユーザーへの提案は滞りません。止まるのは
「第一候補への無駄な呼び出し」だけです。

そして、openから自動でhalf_openへ戻る工夫があります。開いてから**60秒**経った
次の呼び出しを、試験として第一候補に向けるのです。試験が成功すればclosedへ
戻り、失敗すればまたopenへ。この往復は「障害の続く限り何度でも」許されます
(06 §8 D-15)。つまりbreakerは、障害を見つけて止めるだけでなく、**復旧を
自分で確かめて戻ってくる**仕組みでもあるのです。

## 16.3 開く条件: 1分の窓で「続いている」を測る

「失敗が続いたら開く」と言いましたが、「続いている」をどう測るのでしょう。
いつからいつまでの失敗を数えますか。

breakerが見るのは**直近60秒だけ**です。この範囲を**測定窓**(単に窓とも)と
呼びます。第9章9.4のdebounceにも「窓」が出ました。あちらは「窓の間に届いた
知らせをまとめる」使い方でした。今回は「窓の中の出来事だけを数えて、窓の外の
古い失敗は水に流す」使い方です。5分前の失敗にいつまでもビクつかないために、
窓は短くしてあるのです。

窓の中で、開く条件は2つあります(06 §8 D-15 FR-10)。

**条件(a)エラー率が50%を超える**。窓内の呼び出しのうち、失敗(13.5の切替条件
4種)の割合が半分より多くなったら、それは「たまたま」ではなく「続いています」。

ここに大事な但し書きが1つあります。**窓内の呼び出しが2回に満たないときは、この
条件で開きません**。なぜか。呼び出し1回・失敗1回でも失敗率は100%になって
しまうからです。それで開くと、「単発のtimeoutやレート制限のスパイクでは発動
せず」(06 D-15)という仕様の文言に反します。1回ぐらいの失敗は切替(13.5)が
吸収します。breakerが担うのは、複数回呼んでも失敗が重なる状態の検知です。

| 窓内の記録 | エラー率 | 開くか |
|---|---|---|
| 失敗1回・成功0回 | 100%(1/1) | 開かない(呼び出し数が2未満) |
| 失敗2回・成功0回 | 100%(2/2) | **開く** |
| 失敗3回・成功2回 | 60%(3/5) | **開く**(50%超) |
| 失敗2回・成功3回 | 40%(2/5) | 開かない(50%以下) |

**条件(b)p95のレイテンシが6秒以上**。失敗ではなく「遅さ」だけで開く条件です。
p95を初めてちゃんと説明します。**パーセンタイル**(百分位)とは、値を小さい順に
並べたとき「下から○%の位置にある値」のことです。p95なら、速い順に並べて
95%の位置の値——言い換えると「上位5%の呼び出しはこれより遅い」という境界線です。
平均と違うところは、一部の極端な値に引きずられないことです
(第9章でも「p95 10秒」という予算の形で出てきました)。

timeoutは6秒(13.5)なので、成功した呼び出しは必ず6秒未満で終わります。という
ことは、窓内のp95が6秒以上になるのは、遅くて打ち切られた呼び出し(timeout)が
窓に5%を超えて混ざっているときだけです。「5%を超える呼び出しが6秒かかって
いる」状態が続けば、エラー率が50%に届かなくても、遅延の面で開く価値があります。

ここで1つ、素朴な疑問を拾っておきます。timeoutで打ち切られた呼び出しの
「実際のレイテンシ」は、打ち切られた時点で測れません。6秒以上かかっていた
はずですが、何秒だったかは神のみぞ知る、です。そこで実装では、timeout呼び出し
を「レイテンシ6.0秒」として窓に記録することに決めています(ws-8設計 §2.2の
解釈記録)。下限の6.0秒で数えるので、実態より悪く数えることはありません。
timeout呼び出しは同時に**失敗**でもあるので、条件(a)の分子にも入ります。
遅い呼び出しは失敗としてもレイテンシとしても数えられる——どちらか先にしきい値を
超えれば、それで開きます。

数値で確かめると、2つの条件の分工が見えます。窓内20回の呼び出しのうち
timeoutが2回・成功が18回なら、エラー率は10%で条件(a)には遠く及ばない。しかし
p95の位置(速い順に並べて19番目)は2回のtimeout側に落ちるので、条件(b)で開きます。
逆に、全20回が失敗ならエラー率100%で条件(a)が即座に発火します。**「失敗が
多い」か「遅いのが混ざっている」か、どちらも継続障害のサイン**として扱うのが
この2条件です。

なお、この窓やしきい値はdocsの確定値です。測定窓60秒はJev予算5秒に対して
十分短く、障害の検知から切替・提案見送りへの移行が1〜2分で終わるよう設計
されています(06 D-15)。50%というしきい値も、単発のスパイクでは発動せず
継続障害だけを拾う値として選ばれました。

## 16.4 実装を読む: Clock注入の純部品とdequeの窓

実装は `backend/src/latch/llm/breaker.py` です。部品の性質から先に言うと、
これは**純部品**です。DBにもRedisにも依存せず、時刻はClockから注入される
だけ(第3章の規律。実時間参照はarch testが機械的に禁止します)。状態は
メモリ上の変数に持ちます。

まず決まりごとをまとめた部分です。

```python
WINDOW_S = 60.0
ERROR_RATE_THRESHOLD = 0.5
HALF_OPEN_AFTER_S = 60.0
MIN_SAMPLES = 2
LATENCY_THRESHOLD_S = 6.0  # 既定はTimeouts.jev_sと同値(07 §1・引用#2)

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass(frozen=True)
class BreakerParams:
    """測定窓・しきい値(design §2.2)。unit試験で短縮注入するための上書き経路。"""

    window_s: float = WINDOW_S
    error_rate_threshold: float = ERROR_RATE_THRESHOLD
    half_open_after_s: float = HALF_OPEN_AFTER_S
    min_samples: int = MIN_SAMPLES
```

(`llm/breaker.py:34` から)

16.3で読んだ確定値がそのまま並んでいます。この値はenv変数では変えられません。
試験で窓を縮めたいときは、`BreakerParams` のインスタンスを差し替えます
(コンストラクタ上書き式)。第13章のTimeoutsと同じ作法で、「docs確定値をenvで
黙って変えられる経路」を最初から作らないための規律です。

窓の本体は、呼び出し1回ぶんの記録 `(時刻, 失敗か, レイテンシ)` を並べた
**deque**(デック。両端キュー。両端から出し入れできる待ち行列)です。

```python
        self._samples: deque[tuple[datetime, bool, float]] = deque()
```

(`llm/breaker.py:71` から)

dequeにした理由は、窓の性質と合うからです。新しい記録は右端に追加し、
60秒より古い記録は左端から捨てる。両端の操作だけなので速く、コードも短い。

契約は2つのメソッドです。呼び出し側(Gateway)は「**allow(今)で聞いてから呼び、
結果をrecord(失敗か, レイテンシ)で報告する**」という順で使います。

**allow** —— 第一候補を呼んでよいかの問い合わせ。

```python
    def allow(self, now: datetime) -> bool:
        """第一候補呼び出しの可否。openは期限到達でhalf_openへ遷移する。"""
        if self._state == CLOSED:
            return True
        if self._state == OPEN:
            assert self._opened_at is not None
            elapsed = now - self._opened_at
            if elapsed >= timedelta(seconds=self._params.half_open_after_s):
                self._transition(HALF_OPEN, reason="half_open_after")
                self._half_open_probe_pending = True
                return True  # この呼び出しが半開の試験リクエスト
            return False
        # half_open: 前回の試験リクエストが未recordなら防御的に拒否
        # (WorkerはEvent直列処理のため通常発生しない — design §2.2)
        return not self._half_open_probe_pending
```

(`llm/breaker.py:82` から)

closedは常に許可。openは60秒未満なら拒否、60秒を過ぎていたら**この瞬間に**
half_openへ遷移して許可します。つまり半開への移行は、時間だけが経過していても
起きません。**次の呼び出しが来たときに、その呼び出しを試験として使う**形です。
試験の結果がまだ報告されていない(half_open中にまた許可を求められた)場合は
断ります。試験は1回だけ、という16.2の絵どおりの防御です。

**record** —— 呼び出し1回の報告。窓の掃除と、状態の遷移判定の両方をします。

```python
    def record(self, *, error: bool, latency_s: float) -> None:
        """呼び出し1回の記録。窓外サンプルを除去して追記し、遷移を判定。"""
        now = self._clock.now()
        if self._state == HALF_OPEN:
            # 試験リクエストの結果: 成功ならclosed+窓リセット・失敗ならopen戻し
            self._half_open_probe_pending = False
            if error:
                self._transition(OPEN, reason="half_open_probe_failed")
            else:
                self._transition(CLOSED, reason="half_open_probe_succeeded")
            self._opened_at = now if error else None
            self._samples.clear()
            return
        cutoff = now - timedelta(seconds=self._params.window_s)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        self._samples.append((now, error, latency_s))
        if self._state == CLOSED:
            reason = self._open_reason()
            if reason is not None:
                self._transition(OPEN, reason=reason)
                self._opened_at = now
```

(`llm/breaker.py:98` から)

前半はhalf_open中の報告です。試験が成功ならclosedへ戻り、**窓を空にリセット**
します。試験前の失敗たちはもう過去の話だからです。失敗ならopenへ戻り、
「開いた時刻」を更新します。つまり次の半開のチャンスは、そこからさらに60秒後。
失敗し続ける限り、60秒ごとに1回だけ試すペースが続きます。

後半がclosed中の通常の記録です。窓の外(60秒より前)のサンプルを左端から捨てて
から追記し、閉じた状態のままなら開放判定 `_open_reason` を呼びます。

```python
    def _open_reason(self) -> str | None:
        """closed中のrecord直後の開放判定。どちらか先で開放(§9-3)。"""
        n = len(self._samples)
        if n >= self._params.min_samples:
            errors = sum(1 for _, error, _ in self._samples if error)
            if errors / n > self._params.error_rate_threshold:
                return "error_rate"
        latencies = sorted(latency for _, _, latency in self._samples)
        p95_index = math.ceil(0.95 * n) - 1
        if latencies[p95_index] >= self._latency_threshold_s:
            return "p95_latency"
        return None
```

(`llm/breaker.py:121` から)

16.3の条件がそのままコードになった長さです。p95位置の計算
`math.ceil(0.95 * n) - 1` は「速い順に並べたとき、下から95%の位置」の
インデックス(0から数えるので−1)。エラー率の判定にだけ `min_samples` の
条件が付いていて、p95の判定には付いていないことも読み取ってください。
遅さは1回のサンプルでも母集団に載るからです(timeoutは6.0秒と記録されるので、
それがp95位置に来れば条件は成立する)。

状態が変わるときは、毎回構造化ログ(latch.breaker)が出ます
(`_transition` の `logger.info`。`breaker.py:137`)。DBには永化しません。
「いつ開いたか」を後から調べたくなったらログを見る、という付け方は、まだ
観測の要件が固まっていない段階では十分な判断です(第4の章で出た「必要になって
から作る」の再適用)。

## 16.5 Gatewayへの組み込み: 開放中は「呼ばない」を切替の形に合流させる

breakerだけあっても意味がありません。呼び出しの現場で効いてこそです。
組み込み先は、13.5で読んだ切替の判定そのもの—— `LLMGateway.judge_pair` です。

```python
        try:
            if self._breaker is None:
                return await self.call_jev_first(
                    intent_a=intent_a, intent_b=intent_b, intent_ids=intent_ids
                )
            if not self._breaker.allow(self._clock.now()):
                raise _FirstCandidateSkippedOpen()
            t0 = self._clock.now()
            try:
                judgment = await self.call_jev_first(
                    intent_a=intent_a, intent_b=intent_b, intent_ids=intent_ids
                )
            except LLMError as exc:
                # timeout(asyncio.timeout打ち切り)は実レイテンシ計測不能のため
                # 打ち切り時点のtimeout_sを記録(design §2.2・承認事項1)。
                # JevOutputInvalidErrorは応答が得られている呼び出し成功扱い。
                invalid_output = isinstance(exc, JevOutputInvalidError)
                if isinstance(exc, LLMTimeoutError):
                    latency_s = self._timeouts.jev_s
                else:
                    latency_s = (self._clock.now() - t0).total_seconds()
                self._breaker.record(error=not invalid_output, latency_s=latency_s)
                raise
            self._breaker.record(
                error=False,
                latency_s=(self._clock.now() - t0).total_seconds(),
            )
            return judgment
        except (
            LLMTimeoutError,
            LLMRateLimitError,
            LLMOverloadedError,
            LLMConnectionError,
            _FirstCandidateSkippedOpen,
        ):
            pass  # 07 §4の切替条件4種+開放中スキップ。LLMProviderError・
            # JevOutputInvalidErrorは伝播
        return await self.call_jev_fallback(
            intent_a=intent_a, intent_b=intent_b, intent_ids=intent_ids
        )
```

(`llm/gateway.py:132` から。13.5で読んだ形にbreakerの確認と記録が加わった
現行の姿です)

読みどころを3つ挙げます。

1つめ、**開放中のスキップを、切替と同じ形で処理している**ことです。
allowがFalseを返したとき、Gatewayは `_FirstCandidateSkippedOpen` という
内部例外をraiseします。この例外は、13.5の切替条件4種を捕捉するexcept節の
**捕まえる側に追加**されています。だから開放中の処理は「第一候補を呼ぶ→失敗する→
フォールバックへ」の既存の道を、最初の一歩だけ短路した形で流れます。切替の
try/exceptという13.5の構造を壊さずに済む、最小の差分です。呼んでもいないのに
「失敗した」という例外に見えるのは変な話ですが、コメントにあるとおり
**呼んでいないので送信記録もbreaker計上も発生しません**——これが正しい振る舞いです。

2つめ、**何を「失敗」として数えるか**の線引きです。`except LLMError` の中で、
`JevOutputInvalidError`(出力検証失敗)だけは `error=not invalid_output` と
反転して、成功扱いで記録されます。出力の形が壊れていたのは、APIは応答したのに
**LATCH側の検証が弾いた**場合だからです(13.4)。呼び出しとしては成功、失敗は
実装不整合の疑い。breakerは「相手のAPIが生きているか」の番人なので、検証失敗で
開くべきではありません。timeoutはレイテンシを6.0秒として記録する、という16.3の
解釈も、この `latency_s = self._timeouts.jev_s` の1行が実体です。

3つめ、**フォールバック側の失敗はbreakerに記録されない**ことです。recordを
呼ぶのは第一候補側のtry節だけ。フォールバックが失敗する(双障害)ときの対処は
13.6の縮退——skippedで保留——が既に担っています。第一候補の健康をbreakerが
見て、フォールバックの失敗は縮退が受ける。**1つの部品が1つの相手の面倒だけを
見る**、この教科書で何度も出てきた分割の原則がここにも貫かれています。

breakerを渡すのはWorker側の構成だけです。`build_worker_gateway`
(`gateway.py:344`)がstub/real両構成で `CircuitBreaker(clock=clock)` を
生成して渡します。APIプロセスが使う `build_llm_gateway` は渡しません。APIは
Jev系統を呼ばない(第9章〜第13章の分業)ので、見張るべき呼び出しが最初から
ないからです。

## 16.6 3層の守り: 切替・breaker・縮退の分担

ここまでで、第一候補の失敗への対処が3つの仕組みに分かれていることが見えました。
分担を表に整理します。

| 状況 | 仕組み | 働き | 初出 |
|---|---|---|---|
| 1回の呼び出しが429・529・timeout・接続障害 | **切替** | その場でフォールバックLLMへ。送信記録2件 | 13.5 |
| 失敗(または遅さ)が1分の窓で続く | **circuit breaker** | 第一候補を呼ぶのをやめる。60秒後に1回だけ試す | この章 |
| 第一候補もフォールバックも失敗(双障害) | **縮退** | ペアをskippedで保留。提案しない | 13.6 |

3つの層は上から順に「だんだん重い判断」になっています。切替はその場限りの
回避。breakerは「しばらくやめる」という時間帯つきの判断。縮退は「今回は諦めて
保留にする」判断です。重い判断ほど発動の条件が厳しい——単発では開かない、
breakerが開いてもフォールバックが生きていれば縮退しない、という順です。

では、縮退で保留されたペアは、その後どうなるのでしたか。13.7で読んだとおり、
skip_reasonがllm_failureの行は**次のイベントで即時、再評価の対象に**選び直され
ます(`RESELECT_ALWAYS`)。つまり障害から復旧した後の最初のMatch Eventで、保留
だったペアがもう一度評価されて、今度は提案まで進める。この回収があるから、
縮退は「諦め」ではなく「先送り」であり、breakerが開いていた60秒間に流れ損ねた
ペアも、閉じた後にちゃんと拾い直されるのです。

復旧の確認も含めて、全体の流れを時系列で並べるとこうなります。

1. 第一候補が失敗し始める → 各呼び出しは切替で吸収される
2. 窓内のエラー率が50%を超える(またはp95が6秒超) → breakerがopenへ。
   以降は呼ばずにフォールバックで判定を続ける
3. フォールバックまで失敗するペアはskippedで保留(縮退)
4. 開いて60秒 → 次の呼び出しが半開の試験になる
5. 試験が成功 → closedに戻り、窓はリセット。第一候補が復活
   試験が失敗 → openに戻り、また60秒後に試す(往復は何度でも)
6. 復旧後のMatch Eventで、保留だったペアが再評価され、提案まで進む

## 16.7 状態はプロセスの頭の中: 再起動したら閉から出直す

最後に、breakerの状態を**どこに**持つかという判断の記録を紹介します。
答えは「Workerプロセスのメモリの中」です。DBにもRedisにも書きません。

この判断の裏には議論がありました(ws-8設計 §2.1の案A〜C)。状態を複数の
Workerで共有するならRedisに置くのが自然です。しかし、いまのLATCHのWorkerは
1プロセス(10 §1のci構成と同じ)。共有する相手がいないのに共有のための
仕組みを作るのは、第15章で見たYAGNIの「まだ必要でないものを作らない」と
同じ判断です。Redisに置いた場合の欠点(ネットワーク往復・障害面の増加)を
先に払う価値は、まだありません。

メモリに持つことの帰結も、正直に書いてあります。**Workerを再起動すると、
breakerの状態は消えて、閉(closed)から出直します**。窓の記録も失われます。
これは許容、と設計は判断しました。測定窓は60秒です。再起動は60秒よりずっと
稀なできごとで、再起動直後は第一候補を試してみるのが(半開の試験と同じ理屈で)
むしろ合理的だからです。

Workerを複数台に構成するのはM4(非機能)で確定する予定で、そのときは状態の
共有をRedisへ移します。ただしそのときも、allowとrecordという契約(16.4)は
変わりません。中身だけ入れ替える設計です。この「いまは作らない」判断の
一覧は、ws-8設計 §2.11に他の判断と並べて記録されています。

## 16.8 自分で確かめる

`cd backend` してから動かします。ここにあるものはすべて**お金がかからない**
演習です(breaker素部品とスタブだけで完結します)。

1. 素部品で状態遷移を追う。16.2の絵どおりの動きを、FakeClockの時計を
   進めながら観察します

   ```bash
   uv run python - <<'EOF'
   from datetime import UTC, datetime, timedelta

   from latch.core.clock import FakeClock
   from latch.llm.breaker import CircuitBreaker

   NOW = datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)
   clock = FakeClock(NOW)
   b = CircuitBreaker(clock=clock)

   b.record(error=True, latency_s=0.1)
   print("失敗1回:", b.state)
   b.record(error=True, latency_s=0.1)
   print("失敗2回:", b.state)
   print("開放中のallow:", b.allow(clock.now()))
   clock.advance(timedelta(seconds=60))
   print("60秒後のallow:", b.allow(clock.now()), "/", b.state)
   b.record(error=False, latency_s=0.1)
   print("試験成功後:", b.state)
   EOF
   ```

   期待される出力(2026-09-30に実行して確認しました):

   ```text
   失敗1回: closed
   失敗2回: open
   開放中のallow: False
   60秒後のallow: True / half_open
   試験成功後: closed
   ```

   見どころは4つです。失敗1回では開かない(min_samples)。2回目でopenに
   なる(2/2=100%>50%)。openのまま聞いても断られる。時計を60秒進めると
   **allowを呼んだ瞬間に**half_openへ移って試験を許可する。最後の成功で
   closedに戻る。`b.record(error=True, latency_s=0.1)` を3回に増やすと
   「3/3=100%」でももちろん開きます。逆に成功3回→失敗2回(2/5=40%)では
   開かないことも確かめてください。

2. Gateway込みで「開放中は第一候補を呼ばない」を見る。第一候補=毎回429の
   スタブ、フォールバック=正常のスタブを差して、judge_pairを3回呼びます。
   呼び出し回数を数えるため、スタブを薄く包んだ小さなクラスを使います

   ```bash
   uv run python - <<'EOF'
   import asyncio
   from datetime import UTC, datetime

   from latch.core.clock import FakeClock
   from latch.llm.breaker import CircuitBreaker
   from latch.llm.gateway import LLMGateway
   from latch.llm.stub import StubLLM


   class CountingStub(StubLLM):
       """judge呼び出し回数を数えるだけのスタブ。"""

       def __init__(self, **kwargs):
           super().__init__(**kwargs)
           self.judge_calls = 0

       async def judge(self, intent_a: str, intent_b: str) -> dict:
           self.judge_calls += 1
           return await super().judge(intent_a, intent_b)


   NOW = datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)  # JST 12:00
   clock = FakeClock(NOW)
   first = CountingStub(fail_jev_exc="ratelimit")  # 第一候補=毎回429
   fallback = CountingStub()                        # フォールバック=正常
   breaker = CircuitBreaker(clock=clock)
   gw = LLMGateway(clock=clock, parser=first, embedding=first, jev=first,
                   jev_fallback=fallback, breaker=breaker)


   async def main() -> None:
       for i in range(1, 4):
           j = await gw.judge_pair(
               intent_a="Intent A:\n[hard] category: drinking",
               intent_b="Intent B:\n[hard] category: cafe",
               intent_ids=["a", "b"],
           )
           print(f"{i}回目: provider={j.provider} state={breaker.state} "
                 f"first_calls={first.judge_calls}")


   asyncio.run(main())
   EOF
   ```

   期待される出力(2026-09-30に実行して確認しました):

   ```text
   1回目: provider=fallback_llm state=closed first_calls=1
   2回目: provider=fallback_llm state=open first_calls=2
   3回目: provider=fallback_llm state=open first_calls=2
   ```

   3回目の行がこの章の核心です。`first_calls` が2のまま増えていない——
   開放中は第一候補を**呼んでいない**のに、判定は `fallback_llm` で
   返っています。1・2回目の失敗(429)が窓に記録され、2回目の記録直後に
   エラー率2/2=100%でopenへ遷移した様子も `state` の列から読めます。
   `fail_jev_exc` はws-8でスタブに増えた引数で、429相当・529相当・timeout
   相当・接続障害相当の例外を選んで起こせます(`llm/stub.py:110`)。
   `"timeout"` に変えて同じことを試すと、timeoutも切替と開放の両方に
   効くことが確認できます。

3. 試験を読む。この章の内容がそのまま試験になっています

   ```bash
   uv run pytest tests/unit/llm/test_breaker.py -v           # 12件
   uv run pytest tests/unit/llm/test_gateway_jev_switch.py -v  # 21件
   ```

   (件数は2026-09-30に実行して確認しました)
   test_breaker.pyの試験名を縦に読むと、16.3〜16.4の表と規則がそのまま
   並んでいます。`test_2_single_error_does_not_open`(1回では開かない)・
   `test_5_error_rate_2_of_5_stays_closed`(40%では開かない)・
   `test_7_p95_boundary_n20`(20回中timeout 2件で開く)・
   `test_9_half_open_success_closes_and_resets_window`(半開成功で窓リセット)。
   gateway側の試験には「検証失敗は成功扱いで計上」
   (test_invalid_output_counts_as_success_in_breaker)や「フォールバックの
   失敗はbreakerに触らない」(test_fallback_failure_does_not_touch_breaker)
   という、16.5の線引きを1つずつ確かめる試験もあります。

   さらに、integration試験(tests/integration/test_degraded_e2e.py)に、
   実DB・実Redis・スタブGatewayで切替・開放・半開・復旧後の再評価までを
   通しで確かめる8本の試験が加わりました。こちらは `make test-ci` で
   動く(ci環境が必要な)試験なので、ここでは名前だけ覚えておいてください。

4. `rg -l "breaker" src/latch` を実行する。breakerを名指ししているのが
   breaker.pyとgateway.pyの2ファイルだけであることを確認する。呼び出し側は
   judge_pairだけ——16.5の「組み込み先は1箇所」が、検索でも確かめられます

## 16.9 この章の再統合

- 単発の失敗は切替(13.5)が吸収する。**失敗が続く**ときは、待ち時間と
  フォールバックの料金が積み上がる。そこで「しばらく呼ぶのをやめる」判断が要る
- その判断を実装したのがcircuit breaker。**closed(呼ぶ)・open(呼ばない)・
  half_open(1回だけ試す)** の3状態で、openから60秒後に半開の試験、成功で
  closedに戻り、失敗ならopenへ。往復は障害の続く限り何度でも許す
- 「続いている」の測り方は**直近60秒の測定窓**。開く条件は (a)窓内の
  エラー率が50%超(ただし呼び出し2回未満では判定しない)と (b)p95レイテンシが
  6秒以上。timeout呼び出しは「6.0秒」として窓に記録し、失敗としても数える
- 実装はClock注入の純部品(`llm/breaker.py`)。契約は **allowで聞く→呼ぶ→
  recordで報告** の3ステップ。窓はdequeで、古い端から捨てる。しきい値は
  BreakerParamsに固定し、envでは変えられない(Timeoutsと同型)
- Gatewayへの組み込みは `_FirstCandidateSkippedOpen` を切替のexcept節へ
  合流させる最小差分。検証失敗(JevOutputInvalidError)は呼び出し成功扱い、
  フォールバック側の失敗は計上しない。breakerは第一候補の健康だけを見る
- 3層の守り: 単発→**切替**、継続→**breaker**、双障害→**縮退**(skipped保留)。
  保留はskip_reason=llm_failureで即時再選択なので、復旧後のイベントで回収される。
  判定は先送りにされるだけで、行き先が消えるわけではない
- 状態はWorkerプロセス内のメモリ。再起動でclosedから出直す(窓60秒に比べ
  再起動は稀で、出直しが不合理でないため)。Worker複数構成が決まるM4で
  Redis共有へ移る予定だが、契約は不変

第一候補を信用しない作法は、これで3層になりました。Layer 4の呼び出しは、
「1回の失敗に切り替え、続く失敗から離れ、両方死んだら保留する」——どの状態でも
ユーザーへの提案の流れは可能な限り止まらないようになっています。この章で
学んだ状態機と窓の考え方は、外部APIを呼ぶあらゆるシステムで使い回せる型です。

## 16.10 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| circuit breaker(遮断器) | 失敗が続いたらしばらく呼ぶのをやめて、時間を置いて試し直す仕組み |
| closed / open / half_open | 普段どおり呼ぶ / 呼ばない / 1回だけ試す、の3状態 |
| 状態遷移図 | 状態どうしの移り方を描いた図(第6章6.5で初登場) |
| 測定窓 | 「続いている」を測る直近の範囲。breakerは60秒。窓の外の失敗は水に流す |
| min_samples | エラー率判定に必要な最小呼び出し数(=2)。1回の失敗で開かないための条件 |
| パーセンタイル / p95 | 値を小さい順に並べたとき下から○%の位置の値 / 95%の位置の値 |
| レイテンシ | 1回の呼び出しにかかった時間 |
| deque(両端キュー) | 両端から出し入れできる待ち行列。窓の「古い方を捨て・新しい方へ追加」に対応 |
| 窓のリセット | 半開の試験が成功したときに窓の記録を空にすること。過去の失敗を持ち越さない |
| 双障害 | 第一候補もフォールバックも失敗すること。縮退(skipped保留)になる |
| プロセス内メモリ | 状態をDBにもRedisにも置かず、動いているプログラムの変数に持つこと |
| fail_jev_exc | スタブにws-8で増えた引数。429・529・timeout・接続障害の例外を選んで起こす |

## 16.11 確認問題

1. 単発の429への対処(切替)と、429が1時間続いたときの対処(breaker)が
   別の仕組みである理由を、時間と料金の2面から説明してください
2. 呼び出し1回・失敗1回(エラー率100%)でbreakerが開かない理由を、
   06 D-15の「単発のスパイクでは発動せず」という文言との関係で説明してください
3. 成功した呼び出しが必ず6秒未満で終わる(13.5のtimeout)とき、窓内のp95が
   6秒以上になるのはどんな場合か。timeout呼び出しを「6.0秒として記録する」
   解釈がなぜ必要かも説明してください
4. openからhalf_openへの移行が「60秒経っただけ」では起きず、「60秒経った後の
   次の呼び出し」で起きる理由を、allowの実装から説明してください
5. 開放中の第一候補スキップを `_FirstCandidateSkippedOpen` という例外で
   切替のexcept節へ合流させた設計の利点を、13.5の構造を壊さないという点と、
   送信記録が発生しないという点から説明してください
6. 出力検証失敗(JevOutputInvalidError)を「呼び出し成功」としてbreakerに
   計上する理由と、フォールバック側の失敗を計上しない理由を、breakerが
   見張るべきものの定義から説明してください
7. 縮退(13.6)でskippedに保留されたペアが、障害回復後にどうやって提案まで
   進むかを、skip_reasonの再選択規則(13.7)と半開の試験を結び付けて説明
   してください
8. breakerの状態をプロセス内メモリに持つ判断の利点と、再起動で状態が消える
   ことを許容した理由を説明してください。Workerが複数台になったときに何が
   問題になり、どうする予定かも述べてください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった
部分が、読み返すべき節です)
