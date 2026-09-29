# 第9章 知らせを運ぶ仕組み: outboxからWorkerまでのイベント駆動

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第6章(特に6.6のoutbox・match_events・UNIQUE式索引の冪等キー・6.5のFOR UPDATE)・
  第3章(Clock・FakeClock)・第5章5.5(Protocol)・第1章1.4(Dockerとcompose)
- この章を読み終えるとできるようになること:
  - 保存とマッチングのあいだにメッセージキューを挟む理由を、「APIを待たせない」と
    「Workerが止まっても保存できる」の2つで説明できる
  - outboxのpending行がprocessedに変わるまでの経路(publish→Pub/Sub→受信→行確保→
    debounce→version検査)を、ファイルと行を指しながら追える
  - 「必ず届くが、同じものが2回来ることもある」という配達の性質(at-least-once)と、
    それでも結果が壊れない理由(冪等性)の関係を説明できる
  - 破棄と隔離の扱いの違いと、それぞれをどんな異常に割り当てたかを言える
  - 更新Eventだけ10秒待つdebounceの窓が、時計(FakeClock)だけで再現できる理由を説明できる
- 対応コード: `backend/src/latch/events/`(bus.py・pubsub_bus.py・relay.py)と
  `backend/src/latch/worker/`(main.py・debounce.py・stage1.py)が主役。
  発行側の接続は `intents/service.py`、設定は `settings.py`、容器の追加は `compose.yaml`
- 設計の根拠: `docs/plans/M2/ws-1-design.md`(特に§2.1のQueue選定・§2.2の発行経路・
  §2.3のWorker配置・§2.4のversion検査)と `docs/plans/M2/ws-1-report.md`
  (実機検証で見つかった欠陥の記録。9.8で読みます)。
  なお本文中の「NN §X」は `docs/NN-*.md` の第X節を指します(例: 「06 §9」=
  `docs/06-matching-pipeline.md` 第9節)
- 次に読むもの: `concepts/10-embedding.md`(第10章。9.7で予約したEmbeddingの
  実体——そしてその埋め場所がフックから変わった経緯——を読みます)

## 9.1 保存と処理のあいだに郵便局を置く

第6章の最後で、こんな予告をして終わりました。「outboxのpending行が消費されるのは、
その先のM2です。そのとき読むことになる行を、あなたはもう知っています」。
そのM2の一部が今回実装されました。この章は、あのpending行が誰にどう運ばれ、
どこでprocessedになるかを、最初から最後まで追う章です。

まず、解くべき問題を確認します。Intentが保存されると、マッチングという時間のかかる
処理をどこかが始めなければなりません。一番単純な設計は、保存APIの中でマッチングを
直接呼ぶことです。しかしこれには2つの困りがあります。

1つめ。マッチングが終わるまで、ユーザーは保存ボタンの応答を待たされます。
2つめ。マッチング処理が落ちていると、保存まで失敗します。「飲みに行きたい」と
登録したいだけのユーザーが、裏方の不調のとばっちりを受ける。

LATCHが選んだ形は、**保存と処理を別々のプロセスに分けて、そのあいだに「郵便局」を
置く**ことです。APIは保存が決まったら「created・このIntent・version 1」という
知らせを郵便局へ投函して、すぐユーザーに応答を返します。裏方のWorkerが自分のペースで
郵便局から知らせを受け取って処理します。Workerが一時停止していても、知らせは
郵便局に溜まるだけで、保存の邪魔にはなりません。この「投函されたメッセージを預かって、
受取人に渡す係」のソフトウェアを、一般に **メッセージキュー**(メッセージ=知らせ、
キュー=待ち行列)と呼びます。そして、出来事の知らせを起点にして処理が動くような
システムの組み方の名前が **イベント駆動**(イベント=出来事)です。第6章の
MatchEventが「知らせ」の実体で、この章はその配達の全容です。

LATCHが使う具象は **Google Cloud Pub/Sub**(パブサブと読みます)です。GCPが提供する
メッセージキューサービスで、単語の意味は「出版(publish)と購読(subscribe)」です。
投函することをpublish、受け取る側が受信枠を登録することをsubscribeと呼ぶ、という
語源です。Pub/Subの中に2つの道具があります。**topic**(トピック)は投函箱で、
publishは特定のtopicに対して行います。**subscription**(サブスクリプション)は
受信枠で、「このtopicに入ったメッセージは私に配って」と登録したものです。
LATCHではtopicが `match-events`、subscriptionが `match-events-sub` と、
設定ファイルに書かれています(`backend/src/latch/settings.py:78`)。

ここで、初心者の多くがぶつかる疑問に先に答えておきます。**同じ知らせが
2回来ることがある**のはなぜか。ネットワークの世界では「届いた」という返事が
配送側へ戻る前に配送側が落ちると、配送側は「届かなかったかも」と思って送り直します。
すると受信側には同じメッセージが2回届く。Pub/Subはこの設計を正面から採っていて、
「**必ず1回以上届ける。ただし2回来ることもある**」という配達の性質を持ちます。
これを **at-least-once**(アット・リースト・ワンス=1回以上)配信と呼びます。
逆に「ちょうど1回だけ」を保証する **exactly-once** を追うこともできますが、
分散システムではそれがとても高くつくことが知られています。LATCHは割り切りました。
**2回来ても困らない仕組みを自分の側に用意すればいい**——それは第6章で既に作って
あります。match_eventsのUNIQUE式索引と、3点組(event_type, source_intent_id,
version)からなる冪等キーです。同じ知らせが2回届いても、DBは2行目を拒否します。
この「配達側の重複を、受け側で吸収する」構図が、この章全体を貫く主題です。

最後に、開発環境の話をします。本番で使うPub/SubはGCP上のサービスですが、
学習・試験環境でGCP課金に依存したくはありません。この環境ではLATCHは
**Pub/Subエミュレータ**を使います。エミュレータとは、本物と同じ作法で動く練習用の
複製です。Pub/Subの
SDK(公式のライブラリ)は、環境変数 `PUBSUB_EMULATOR_HOST` が設定されていると
自動的にエミュレータへ接続しに行きます。つまりAPIもWorkerも**本番と同じコード経路**
で動き、接続先だけが環境変数で切り替わります。この「本番と同じ種類の道具を、
規模だけ縮小して用意する」は、試験環境の設計原則です(10 §1)。
compose.yamlに5つ目のサービスとして加わっています(`compose.yaml:35` から)。
コメント行と `restart` の行を省略した引用です。

```yaml
  pubsub:
    # ci環境のMessage Queue具象(10 §1「本番と同じ種類で規模縮小」・04 §3選定=Pub/Sub)。
    image: gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators
    command: ["gcloud", "beta", "emulators", "pubsub", "start", "--host-port=0.0.0.0:8085"]
    ports:
      - "127.0.0.1:8085:8085"
```

composeが動かす容器はこれで5つになりました(db・redis・api・worker・pubsub)。
apiとworkerには `LATCH_PUBSUB_EMULATOR_HOST: pubsub:8085` が渡されていて、
両者がこの郵便局を見つけられるようになっています。

## 9.2 発行側は二段構え: コミット直後のpublishと、遅れた行を拾うリレー

発行側の出発点は、第6章で読んだoutboxです。保存のトランザクションの中で
match_eventsへpending行がINSERTされる。今回実装されたのは、その直後の運び出しです。

設計上いちばん大事な判断は、**publishをトランザクションの外(コミットの後)で呼ぶ**
ことでした。理由を考えてみましょう。逆にトランザクションの中でpublishしたとします。
まず「まだコミットされていない行の知らせ」が郵便局へ先に飛び出します。受信した
WorkerがDBを見に行っても、行はまだ無い(または見えない)。さらに、publishが
失敗したらどうしますか。トランザクションをロールバックして保存まで取り消すのは
過剰です。ユーザーの保存は成功しているのに「システムの都合で失敗しました」と
言うことになります。コミット後にpublishすれば、この2つの混乱は起きません。
コミットに失敗したら知らせ行ごと存在しないのでpublishも走らない。
publishに失敗したら知らせ行はpendingのまま残る。**保存が成功した行には必ず
知らせ行が伴う**という第6章の原子性は変わらず、publishは「最善を尽くすが、
失敗しても保存を汚さない」役割に分離されました。

コードを読みます。`IntentService` に `_publish` という小さなメソッドが
加わりました(`backend/src/latch/intents/service.py:332`)。

```python
    async def _publish(
        self, *, event_type: str, intent_id: uuid.UUID, version: int
    ) -> None:
        """uowコミット後のpublish(design §2.2-B)。失敗は握り、フォールバック
        リレー(30秒超のpending行)が回収する。publishはuowの外でのみ呼ぶ。"""
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish_match_event(
                event_type=event_type, intent_id=intent_id, version=version
            )
        except Exception:
            logger.warning(
                "event publish failed event_type=%s intent_id=%s version=%s",
                event_type,
                intent_id,
                version,
            )
```

注目する点は2つです。`self._event_bus` が `None` なら何もしない(unit試験や
発行を無効にした構成では、EventBusが注入されない)。そして publish の失敗は
例外を投げず、警告ログを1行残して握り潰します。ここで「失敗を握っていいのか」と
思うのが正しい反応です。握ってよい理由は、**失敗してもpending行がDBに残っている**
から。次の仕組みが必ず拾い直します。

その拾い直し役が **フォールバックリレー**(fallback=縋り綱、relay=継走)です。
APIプロセスの中で5秒ごとに回るループで、`relay.py` のSQLが条件を1つだけ持ちます
(`backend/src/latch/events/relay.py:24`)。

```python
_SELECT_STALE_PENDING = text("""
    SELECT event_type, source_intent_id, payload->>'version'
    FROM match_events
    WHERE status = 'pending' AND created_at < :threshold
    ORDER BY created_at
    LIMIT :limit
""")
```

`threshold` は「現在時刻マイナス30秒」です。つまり**30秒を過ぎてもpendingのままの
行だけ**を再publishする。作成Eventは窓待ちなしで即処理され、更新Eventも
debounceという待ち(10秒。9.4)＋処理で30秒には収まる。だから正常に動いている間、
このSQLは1行も返しません。再publishが走るのは、publishが失敗した・郵便局が
メッセージを失った・Workerが長く止まっていた、という異常のときだけです。
この3つの異常を「30秒超のpending行」という単一の条件で回収する——
補償を一元化する設計です。

なお、通常運転でpublishされる中身は3点組のJSONです(`pubsub_bus.py:69` の
`publish_match_event`)。`{"event_type": ..., "source_intent_id": ..., "version": ...}`
という3つのキーだけの小さなJSONで、知らせの本体データは運びません。行の詳細は
WorkerがDBから読むので、郵便局には「どこを見ればいいか」の鍵だけを運ばせます。

## 9.3 Workerの受信は「まず行を確保」から始まる

受け側のWorkerは、`compose.yaml` のworkerサービスが走らせる
`backend/src/latch/worker/main.py` です。起動すると、 Pub/Subのsubscriptionへ
**ストリーミングpull**(届いたそばから順次受け取る受信形態)を登録します。
新しいメッセージが届くたびに `_dispatch` が呼ばれます(`worker/main.py:112`)。

```python
    async def _dispatch(self, event: IncomingEvent) -> None:
        try:
            result = await self._stage1.intake(event)
            if result.kind in ("processed", "duplicate", "quarantined"):
                event.ack()
                return
            if result.kind == "debounce":
                ...
```

ここで **ack**(アク=確認応答)という言葉が初出です。「受け取って、処理を
完了した」と郵便局に返す返事のことです。LATCHのWorkerは、DBのstatus遷移が
コミットされた後にackを返すと決めています。ackを返すと郵便局はそのメッセージの
配達責任を解きます。ackを返さずにいると、決められた時間(この時間を
**ack deadline**といい、LATCHでは600秒。設定は `settings.py:81`)が過ぎたところで
メッセージが自動的に送り直されます。つまり「ackしない」は「もう一度届けて」という
意思表示になります。Workerがクラッシュした場合を思い浮かべてください。
処理の途中で死ぬと、当然ackは返っていません。すると600秒後にメッセージが再配信され、
別の(または再起動後の)Workerが同じ知らせをもう一度処理しようとします。
at-least-once配信の「送り直し」は、こうしてWorkerの故障の補償にもなっているのです。

`_dispatch` の最初の仕事は、受信したバイト列を `IncomingEvent` という形に変換することです
(`backend/src/latch/events/bus.py:29` の `from_payload`)。JSONとして読めない、
読めても `event_type` が文字列でない、`version` が整数でない——そういうメッセージでは
3点組の要素が `None` になります。この「読める部分だけを拾い、読めなければNone」の
変換が、あとで「毒ペイロード」(処理できない壊れた知らせ。9.6)の仕分けの入り口に
なります。

変換が済んだら、第1段処理 `Stage1.intake`(stage1.py:139)の最初の関門、
**行の確保**です。ここは、この単位で最も読み応えのある工夫なのでゆっくり読みます。

思い出してください。API経由でpublishされた知らせには、outboxがINSERTしたpending行が
既にDBにあります。しかし、10 §1が定める「テスト用パブリッシャー」という別の投入経路では、
テストが郵便局へ直接publishするので、DBには行がありません。同じ知らせなのに、
行がある場合とない場合がある。この違いを1つの経路にまとめるため、Workerは
受信したら**まず自分で行を用意しに行く**ことにしました
(`stage1.py:60` の `_INSERT_EVENT`)。

```python
_INSERT_EVENT = text("""
    INSERT INTO match_events
        (event_type, source_intent_id, payload, status, created_at)
    VALUES
        (:event_type, :source_intent_id, CAST(:payload AS jsonb), :status,
         :created_at)
    ON CONFLICT DO NOTHING
    RETURNING id
""")
```

見覚えのあるINSERTに、2つの飾りが付いています。`ON CONFLICT DO NOTHING` は
「同じ行(UNIQUE違反になる行)が既にあったら、何もせず挿入を諦める」。`RETURNING id`
は「挿入したらその行のidを返す」。この2つの組で、次の場合分けが1回のSQLでできます。

- 挿入に成功し、idが返ってきた → 行はなかった(テスト直投入)。この行を処理する
- 挿入が諦められた(idが返らない)→ 既に行がある。UNIQUE索引と同じ条件で
  `SELECT ... FOR UPDATE` して既存の行を確保する(`stage1.py:69` の `_SELECT_CLAIM`)

FOR UPDATE(第6章6.5で「読みながら行に鍵をかける」と学んだSQL)がここでも出てくる
理由は、複数の受信が同じ知らせを同時に処理しようとするからです。鍵をかけられた側は
先に待たされ、処理の直列化が保証されます。そして確保した行のstatusを見て、
pendingではじめて処理に進む。**処理が済んだ(processed)行や、何度試しても処理
できず隔離された(quarantined)行になっていたら、処理はせずackだけ返して終わる**
——これがat-least-onceの「2回目」の行き先です(この「2回目」の判定結果には
duplicate(重複)という名前が付いていて、`_dispatch` の引用にも出ていました)。
送り直された同じ知らせは、1回目が既に行をprocessedに変えているので、2回目は
何もせず静かにackだけ返します。第6章でUNIQUE索引が「DBレベルの重複排除」と
書かれていた意味が、ここで実装に落ちました。

## 9.4 更新の知らせだけ10秒待つ: トレーリング窓のdebounce

行を確保したStage1.intakeは、event_typeが `updated` なら**処理を始めずに、いったん
窓へ預けます**。この待ちの仕組みが **debounce**(デバウンス)です。

なぜ更新だけ待つのでしょう。ユーザーが条件をあれこれいじる場面を思い浮かべて
ください。20:00の予定を20:30に直し、すぐ場所も変える。すると
`updated version 2`・`updated version 3` の知らせが数十秒の間に連続で飛びます。
待たずに全部処理すると、version 2のマッチング計算が終わらないうちにversion 3が来て、
その計算はすぐ無駄になります。**連打のたびに重い処理を起動する無駄を省きたい**。
そのために「最後の知らせから10秒間、新しいversionが来なかったら処理する」という窓を
置きました。窓の間に来た新しい知らせは前のものを統合してしまい、処理するのは
最新versionの1つだけです。電球のスイッチを押してから数秒後に消える階段の照明と
同じ仕組みで、「入力が落ち着くまで出力を待つ」手法を一般にdebounceと呼びます。
LATCHが採った形は、窓の解放時刻を「**最初の知らせから10秒**」でなく「**最後の
知らせから10秒**」に更新するものです。この形を特に **トレーリング**(後追い)と
呼びます。作成Event(created)を待たないのは、初回の保存には「活性化から判定まで
p95 10秒」(95%の処理が10秒以内に収まるべきという指標)という予算が割り当てられていて、
最初の1通を10秒も止めるわけには
いかないからです(06 §1の層別予算)。

実装は `worker/debounce.py` の `TrailingDebouncer` です。窓の実体は、
intent_idを鍵とした辞書と、解放時刻の比較だけです。この部品はDBもPub/Subも
使わないので、**あなたのPCで今すぐ動かせます**。時計には第3章のFakeClockを
使います。`backend/` で実行してください。

```bash
cd backend && uv run python - <<'EOF'
import uuid
from datetime import UTC, datetime, timedelta
from latch.core.clock import FakeClock
from latch.events.bus import IncomingEvent
from latch.worker.debounce import DebounceEntry, TrailingDebouncer

IID = uuid.UUID("11111111-1111-1111-1111-111111111111")
clock = FakeClock(datetime(2026, 9, 29, 3, 0, 0, tzinfo=UTC))  # JST 12:00:00

deb = TrailingDebouncer(clock=clock, window_sec=10, tick_sec=0.01,
                        on_release=lambda g: None)

def entry(version):
    ev = IncomingEvent(message_id=f"msg-{version}", data={"version": version},
                       event_type="updated", intent_id=IID, version=version,
                       ack=lambda: None)
    return DebounceEntry(event=ev, triple=("updated", IID, version),
                         row_id=uuid.UUID(int=version))

print("12:00:00  v2到着   ->", deb.submit(entry(2)))    # 新規グループ。解放時刻=12:00:10
clock.advance(timedelta(seconds=3))
print("12:00:03  v3到着   ->", deb.submit(entry(3)))    # 未知version。解放時刻は12:00:13へ延長
clock.advance(timedelta(seconds=1))
print("12:00:04  v3再配信 ->", deb.submit(entry(3)))    # 既知version。窓は延長しない
clock.advance(timedelta(seconds=1))
print("12:00:05  poll     ->", [g.latest.triple[2] for g in deb.poll()])  # [] = まだ解放時刻でない
clock.advance(timedelta(seconds=8))                     # 12:00:13
groups = deb.poll()
g = groups[0]
print("12:00:13  poll     -> 解放! latest=v%s, absorbed=%s"
      % (g.latest.triple[2], [e.triple[2] for e in g.absorbed]))
clock.advance(timedelta(seconds=1))                     # 12:00:14
print("12:00:14  poll     ->", deb.poll())              # グループは消えたので空
EOF
```

期待される出力(2026-09-29に実行して確認しました):

```
12:00:00  v2到着   -> True
12:00:03  v3到着   -> True
12:00:04  v3再配信 -> False
12:00:05  poll     -> []
12:00:13  poll     -> 解放! latest=v3, absorbed=[2]
12:00:14  poll     -> []
```

出力を読み解きます。v2の到着で窓が開き、解放時刻は12:00:10。3秒後に未知のversion
(v3)が来たので解放時刻は到着時刻+10秒の12:00:13へ**延長**されました。これが
トレーリングです。12:00:05に覗いても解放時刻前なので何も起きない。時計が
12:00:13に達したところでpollがグループを返し、中身は「処理するべき最新 = v3」と
「窓に吸収された = v2」に仕分け済み。10秒の窓の動き全体が、FakeClockの
`advance` だけで再現できています。実時間を待つテストは遅くて不安定ですが、
時計を差し替えられる設計(第3章)のおかげで、窓の試験も数秒で回るunitテストの
仲間入りをしました(`tests/unit/test_worker_debounce.py`)。

残る2つの細部も見ておきます。1つは**同じversionの再受信は窓を延長しない**ルール
です(出力の3行目 `False`)。9.1で見たとおり、at-least-onceの世界では同じ知らせが
2回来ます。再受信のたびに窓が延びると、送り直しが続くあいだは窓が永遠に
解放されない、という競合が起こり得ます。「未知のversionだけが延長の根拠」とすることで、この競合を
構造的に潰しました。2つめは**吸収された行の行き先**です。v2のmatch_events行は
pendingのまま残っています。放っておくと30秒後にフォールバックリレー(9.2)が
再publishしてしまいます。窓が解放したとき、Workerは最新versionの処理後に、
吸収行を「統合されたので処理しない」の理由つきでprocessedに閉じます
(`worker/main.py:133` の `_on_release`。理由は `discard_reason` として
payloadに記録)。全行がprocessedかquarantinedで閉じる——outboxの帳尻は
必ず合わせる、がこの設計の規律です。

最後に、大事な位置づけの話をします。**debounceは最適化であって、正しさの担保では
ありません**。本番でWorkerを複数台に増やすと、同じIntentの知らせが別々のWorkerに
散らばり、窓の統合はうまく働きません。それでも結果が壊れない仕組みが、次の節の
version検査と、UNIQUE索引(第6章)です。最適化が外れて遅くなることはあっても、
間違った結果を作ることはない。この「速さの工夫と正しさの保証を別々のレイヤに
置く」判断は、スケールを見すえた設計の読みどころです(design §2.3)。

## 9.5 知らせの鮮度を3つに分ける: version検査

debounceの窓が開いたら(または待たない種別ならすぐ)、処理の本体に入ります。
中心は `stage1.py:249` の `_process_once` で、トランザクション1本の中に
判定の階段が並びます。引用します(見通しのため一部を省略)。

```python
    async def _process_once(
        self, triple: tuple[str, uuid.UUID, int], row_id: uuid.UUID
    ) -> None:
        event_type, intent_id, version = triple
        now = self._clock.now()
        async with self._engine.begin() as conn:
            status = await self._lock_row(conn, row_id)
            if status != "pending":
                return  # 二重受領(条件UPDATE・FOR UPDATEで直列化)
            current = await self._intent_version(conn, intent_id, for_update=False)
            if current is None:
                # 削除済み等の不在=正当な遅延Event → processed破棄(06 §9)
                await self._mark(conn, _MARK_PROCESSED, row_id, "intent_not_found", now)
                return
            if version < current:
                await self._mark(conn, _MARK_PROCESSED, row_id, "stale_version", now)
                return
            if version > current:
                reread = await self._intent_version(conn, intent_id, for_update=True)
                if reread is None:
                    raise Retryable("intent vanished on reread")
                if reread != version:
                    raise Retryable(
                        f"version divergence: event={version} current={reread}"
                    )
            # version == intents.version → 種別処理(§2.5)
            ...
```

階段の主役は、**知らせのversionと、intents行の現在のversionとの比較**です。
知らせは運ばれている間にも、Intentは更新され続けます。届いたときには、その
知らせが語る内容が「過去の話」になっているかもしれない。そこで着いた知らせを
「鮮度」で3つに分けます。ここでの version は第6章6.5で学んだ、保存のたびに+1される
Intentの版数です。

**過去の知らせ(version < 現行)**。処理する意味がありません。新しい版の処理が
この後に来る(あるいはdebounceが統合済み)ので、古い版を計算するのは純粋な無駄です。
ただちに `stale_version`(stale=新鮮でない)の理由でprocessedに閉じて終わります。
**現在の知らせ(version == 現行)**。本来の処理をします(9.7)。**未来の知らせ
(version > 現行)**。これは奇妙に見えますが、起こり得ます。最初のSELECTが
「更新前の古いintents行」を読んでいた可能性があるので、行に鍵をかけて(FOR UPDATE)
読み直します。読み直して一致すれば処理へ。乖離が続くようなら、一時的な読み取り遅延か、
本当に矛盾しているか分からないので、例外を投じて再試行に回します(9.7)。

この検査とUNIQUE制約の2枚が、9.4の終わりに述べた「正しさの担保」の実体です。
配達の順番が入れ替わっても(at-least-onceの世界では順番の保証すらありません)、
古い知らせは鮮度検査で捨てられ、同じ知らせの二回目は行のstatusで捨てられる。
2重の仕分けを通過したものだけが処理される構造です。

## 9.6 「いない」は破棄、「読めない」は隔離: 2つの失敗は扱いが違う

9.5の引用に、version検査の前段として「Intentが見つからない」場合の行がありました。
`current is None` のとき、`quarantined` に**しない**で、`intent_not_found` の理由で
processed に閉じます。ここは設計判断がはっきり分かれる場所なので、独立した節にします。

Workerが見る異常は、性質の違う2種類に分かれます。

1. **参照先のIntentがいない**(削除済みなど)
2. **知らせの中身が読めない**(versionが欠落している・event_typeが6種のどれでもない)

どちらも「正常に処理できなかった」ことに変わりありませんが、LATCHはこの2つを
**破棄と隔離という別の言葉**で区別しました。match_events行のstatusは3値です
(05 §2)。`pending`(処理待ち)、`processed`(処理済み。理由つきの破棄も含む)、
そして `quarantined`(隔離)。

破棄(processed)は「**それは正しくない知らせだったが、想定内の動きだ**」という
扱いです。参照先がいないのは、多くの場合で正当なことです。例えばDELETEで
cancelledになったIntentへの知らせが、郵便局の中で少し遅れてから届く。
時系列のズレはat-least-onceの世界では正常運転です。これをいちいち隔離棚に
上げていたら、隔離棚は遅延メッセージで溢れ、アラートも鳴り続けます。
「隔離は、人間が目を向けるべきものだけを集める棚」であるべきなので、
想定内の不在は普通にprocessedとして帳消しにする、と割り切りました(06 §9)。

隔離(quarantined)は「**これはこのシステムの理解の外にある。何度やっても
処理できない**」という扱いです。対象は、3点組として読めないメッセージ(version
欠落・型不一致)と、6種以外のevent_typeです。たとえば誰かが郵便局に壊れたJSONを
直接投げ込んだとしましょう。こうしたメッセージは一般に**毒ペイロード**(処理する
側を事故らせるデータ。poison=毒)と呼ばれます。毒ペイロードを何度配達し直しても
処理できるようにはなりません。再配信のたびにWorkerが同じ箇所で転ぶなら、後続の
正常なメッセージまで渋滞します。だから処理を5回試したあと(9.7)、隔離棚に
移して配達の輪から外す。失敗理由は行のpayloadに `failure_reason` として記録され、
あとで人間が棚を見れば理由が分かる。**隔離の実体はDBのstatus**——その判断の
意味するところは、ここまでの流れのとおりです。

この区別を1枚にまとめます。

| 状況 | status | 理由コード | なぜ |
|---|---|---|---|
| 参照先Intentがいない | processed | intent_not_found | 想定内の遅延。隔離を溢れさせない |
| versionが現行より古い | processed | stale_version | 新しい版が来ているので無駄を省く |
| debounceに吸収された | processed | debounced_superceded | 窓の統合で処理対象から外れた |
| 3点組が読めない | quarantined | failure_reason=… | 再試行しても直らない(毒ペイロード) |
| event_typeが6種外 | quarantined | failure_reason=… | 同上 |

ちなみに「読めない」知らせには、対応するpending行がそもそも作れないことがあります。
3点組が組めないため、UNIQUE索引の鍵がないからです。そこでStage1は、読めなかった
生データをそのままpayloadに詰めた隔離行を新しくINSERTします
(`stage1.py:328` の `_quarantine_direct`)。このとき `source_intent_id` が不明なら
値ゼロのUUID(nil UUID)を入れておきます。UNIQUE索引はNULL(不在)の重複を妨げない
ので、壊れたメッセージが何通来ても、それぞれ別の行として隔離できます——
毒ペイロードを1つ残らず記録するためには、重複排除が効かないほうがむしろ都合がいい、
というめずらしい例です。

## 9.7 5回もがいて、それでもだめなら隔離: 再試行と、この単位の処理の実体

一時的な失敗は誰にでも起こります。DBが一瞬混んでいて読み取りがタイムアウトする。
version乖離が読み直しても解けない。そういう失敗の対処が `process` の再試行ループです
(`stage1.py:170`)。

```python
        for attempt in range(self._retry_max + 1):
            try:
                await self._process_once(triple, row_id)
                return "processed"
            except (Retryable, SQLAlchemyError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
                ...
                if attempt < self._retry_max:
                    await self._sleep(BACKOFF_SEC[attempt])
        await self._quarantine_row(row_id, triple, reason=reason or "retry exhausted")
        return "quarantined"
```

`BACKOFF_SEC` は `(1.0, 2.0, 4.0, 8.0, 16.0)`(`stage1.py:47`)。失敗するたびに
待ち時間を倍々にしていく、この工夫を **バックオフ**(back off=後ずさり)と呼びます。
相手が混んでいるときに間髪入れず再試行すると、混みの原因に自分で追い打ちを
かけることになるので、少しずつ待つ間隔を広げます。初回+再試行で最大6回の試行ののち、
それでも成功しなければ隔離行きです。回数の5回と窓の10秒は、計測(キューの滞留時間)
を見ながら調整する初期値とされています(06 §9)。

再試行をWorkerプロセスの中で数えることも、設計の選択でした。Pub/Sub自身にも
「配達し直しの間隔と回数を設定で宣言する」仕組みがありますが、使わないと決めました。
理由は2つです。1つは、ci環境のエミュレータがその機能に対応していないこと。
もう1つは、試験を決定的にしたこと——つまり待ち時間をClockで操れる形にしたことです
(design §2.7の切り捨て一覧)。外部のミドルウェアに任せた制御は、エミュレータと
実環境で挙動が揃わない恐れがあります。自分のプロセス内のループとFakeClockで
書いておけば、unit試験で秒単位の再現ができます。

version検査を通った知らせへの実際の処理(引用の `...` の部分)は、この単位では
思いのほか薄い中身です。`deleted` なら、そのIntentを含む候補(match_candidates。
候補生成は次の単位以降)を `closed` にするUPDATEを1本。`created` と `updated` なら、
**Embedding要求フック**という呼び出し位置だけが予約されていて、実体は次の単位
(ws-2)が埋めます(`stage1.py:279` から。`embedding_hook=None` なら何も起きない)。
Embeddingとは、Intentの文章を「意味の近さ」で比べるための数値の列(ベクトル)へ
変換する処理で、第1章1.7でpgvectorとセットに予告したものです。この章で経路が
開通したことで、その変換をいつ・どの知らせに対して始めるかという「呼ぶ位置」を
先に確定できました。どこから呼ぶかが決まっていないと、次の単位は経路の付け替えから
始めなければなりません。位置が決まっていれば、中身を差し込むだけで済みます。
`expired`・`scheduled`・`embedding_completed` の3種は、受信と
鮮度検査だけを定義して、処理の実体は後続単位です。どの道も最後は
processedへのUPDATEで閉じます。**土台だけを先に開通させる**——この単位の実装は
そういう性格のものでした。フックという「あとで埋めるための差込口」の考え方も、
覚えておくと後続のコードが読みやすくなります。

その後の記録(2026-09-29追記)。ws-2がEmbeddingの実体を実装したとき、埋め場所は
このフックではなく**Worker側の配線**に変わりました。フックは `_process_once` の
DBトランザクションの中で呼ばれる位置で、外部APIを最大2秒待つ処理には置けなかった
のです(「再試行なし」の規律とも衝突します)。stage1.py は1行も変えず、Workerが
Stage1のコミット後・ack前にEmbeddingを呼ぶ形で解決しました。予約した差込口が
そのまま使われない結果になっても、この章で先に開通した経路と冪等な呼び出し契約が
あれば、埋め場所をずらすだけで済む——それも「土台を先に作る」効果です。
経緯の全体は第10章10.8で読みます。

もう1つのその後(同じくws-4)。`embedding_completed` の処理実体も、同じ「フック」
の形で埋まりました(`matching_hook`。第12章12.5)。こちらはEmbeddingと理由が逆で、
マッチングはDB内の処理なので、Event行をprocessedにするトランザクションの**中で**
呼ぶのが正解でした。フックという差込口の考え方——呼ぶ位置を先に決めておいて、
実体と埋め場所はあとから選ぶ——が、処理の性質の違いに合わせて2度目も使われた
のです。

## 9.8 緑でも本番は落ちる: 4例目と5例目

第4章4.6と第6章6.7で、このリポジトリの教訓シリーズを読みました。「unit テストが
緑でも、実DB・実SDKとの組み合わせでは挙動が変わる」。ws-1はこの実例を2つ、
さらに深刻な形で追加しました。報告書 `ws-1-report.md` から読みます。

**4例目: SDKの関数の呼び出し形式**。検証役のスーパーバイザーが `make test-ci` を
走らせたところ、composeで常時起動している常設workerが
起動するなりクラッシュループしました。エラーは `TypeError: Invalid constructor
input for Topic: 'projects/latch-ci/topics/match-events'`。原因を遡ると、
Pub/Sub SDK(google-cloud-pubsub 2.41.0)の `create_topic` は、第1引数を
「設定を詰めた辞書(request)」として受け取る仕様なのに、コードはtopic名の文字列を
そのまま渡していたのでした。SDKの内部で文字列から設定メッセージを作ろうとして
TypeErrorになった、というわけです。修正後のコードが今見られる形です
(`pubsub_bus.py:53`)。

```python
                self._publisher.create_topic(request={"name": self._topic_path})
```

unitテストはこの欠陥を拾えませんでした。テストでPub/Subの役をしていたスタブは
「こう呼ばれるはず」と思い込んで同じ形式で呼び、スタブは渡されたものは何でも
受け入れるからです。**スタブには、呼び出しの意図しか検証できない**。SDKが
どういう形式を受け付けるかは、実SDKに聞くまで分かりません。だから修正と同時に、
再発防止の試験も作られました。**回帰ピン**(同じ欠陥の再発を防ぐために固定する
試験の呼び方。`tests/unit/test_pubsub_bus_sdk_calls.py`)です。SDKクライアントを
「呼ばれた引数を記録するスタブ」に差し替えて、「位置引数なし・request辞書の形式」
で呼ばれていることを検証します。修正前に実際に赤になることを確認してから、
緑にしました。

**5例目: DBが返す型の差**。`stage1.py` はSELECTの結果からUUIDの列を再構築する
箇所で、`uuid.UUID(取り出した値)` としていました。ところが実DB(asyncpg)が返す
UUIDは、uuid.UUIDの**サブクラス**です。サブクラスはそのままUUIDとして使えるのに、
改めて `uuid.UUID(サブクラスのインスタンス)` と再構築しようとすると、文字列を
期待しているコンストラクタがAttributeErrorを投げます。unitテストのスタブconnは
文字列を返すように作られていたので、この差が見えませんでした。修正は、
UUIDならそのまま・それ以外なら文字列から組む、という1つの関数に集約です
(`stage1.py:51` の `_coerce_uuid`)。同じ欠陥の系譜がM0 ws-3・M1 ws-1にもあり、
そのたびに同じ対策(値をそのまま通す関数)が置かれています。

ここまでの読者なら、この2件の共通点を自分で言えるはずです。**スタブは
「正しい形で呼ばれたか」は検証できるが、「相手がそれを受け入れるか」は検証
できない**。実SDK・実DBだけが知っている約束(呼び出し形式・戻り値の型)は、
実物と繋ぐintegration試験が担保するしかない。ws-1ではその実物が「Pub/Sub
エミュレータ」です。compose常設のエミュレータと実DBに向けた
`make test-ci`(test_events_pipeline.pyの8試験を含む)が、この経路の安全網です。
なお test-ci の実行中は常設workerが一時停止されます(Makefileが停止と復帰を
管理)。理由は面白いので考えてみてください(確認問題6)。

## 9.9 自分で確かめる

1. 9.4のスクリプトを動かしたら、`window_sec=10` を `5` に変え、
   `advance` の秒数も調整して、「v3到着からちょうど5秒で解放される」ことを
   確かめる。さらに `submit(entry(3))` をもう1回足して、窓が延びないことを確認する
2. `uv run pytest tests/unit/test_worker_debounce.py -v` を実行し、試験名を
   縦に読む。この節で学んだ規則(未知versionだけ延長・再受信は延長しない・
   intentごとの独立)が、それぞれ試験になっていることを確認する
3. `uv run pytest tests/unit/test_worker_stage1.py -v` を実行する。件数(21件)を
   確かめ、試験名から「version三分岐・行なし破棄・毒ペイロード隔離・再試行5回」の
   どこが試されているか読み分けてノートに書く
4. `rg -n "discard_reason|failure_reason" backend/src/latch/worker/stage1.py` で
   理由コードの書き込み箇所を全部数える。9.6の表の5行と付き合わせる
5. `git log --oneline -- backend/src/latch/events/` でこのパッケージの生い立ちを
   見る。ポート(bus.py)が実装(pubsub_bus.py)より先にコミットされていることを
   確認し、TDDの順(第3章)との関係を自分の言葉でまとめる

## 9.10 この章の再統合

- 保存と処理は別プロセスで、あいだにPub/Subという郵便局を挟む。APIは投函して
  すぐ応答を返し、Workerは自分のペースで消費する。Worker停止中の知らせは溜まる
- 配達はat-least-once(必ず届くが、2回来ることもある)。重複は、受け側のUNIQUE索引と
  3点組の冪等キーで吸収する。「配送の保証は最小限に、正しさはDBで」の分担
- 発行はコミットの外でpublishし、失敗は握る。30秒を過ぎたpending行だけを
  フォールバックリレーが回収する。補償の一元化
- Workerの受信は、まずmatch_eventsの行を確保(ON CONFLICT DO NOTHING+FOR UPDATE)。
  処理済みの行ならackだけ。ackはstatus遷移のコミット後で、失敗・クラッシュは
  ack deadlineの再配信が回収する
- 更新Eventだけ、最後の知らせから10秒待つトレーリング窓で統合する(debounce)。
  窓の解放はClockの比較だけで決まり、FakeClockで完全に再現できる。debounceは
  最適化であり、正しさはversion検査とUNIQUEが別途担保する
- 知らせの鮮度はversion比較で3分岐(過去は破棄・現在は処理・未来は鍵をかけて読み直す)。
  参照先不在は正当な遅延として破棄(processed)。読めない知らせ=毒ペイロードだけが
  再試行5回(バックオフ1→16秒)ののち隔離(quarantined)される
- この単位の処理実体は、deletedの候補クローズとEmbeddingフックの予約のみ。
  created/updatedの実処理は次の単位が埋める(ws-2の実際の埋め場所はWorker配線。
  第10章10.8)
- 実SDKの呼び出し形式・実DBの戻り値の型は、unitのスタブには検証できない。
  4例目・5例目の実例が、2層のテスト構成の意味をさらに裏付けた

第6章で、あなたは知らせ行がどう書かれるかを学びました。この章で、それが運ばれ、
仕分けられ、帳尻が合わされるまでを見ました。続く単位で、処理の実体がこの経路に
乗りました。Embeddingは第10章、そのベクトルを使うマッチング前半(Layer 1・Layer 2)は
第11章です。窓の解放を待つ最新versionの行が、そのまま次の処理の入力になる——
土台の上を、実体が走り始めています。

## 9.11 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| メッセージキュー | 知らせを預かって受取人に渡す係のソフトウェア |
| イベント駆動 | 出来事の知らせを起点に処理が動くシステムの組み方 |
| Pub/Sub | LATCHが使うメッセージキュー(Google Cloud)。publishとsubscribeが語源 |
| Worker | 郵便局から知らせを受け取って裏方の処理を進めるプロセス |
| topic / subscription | Pub/Subの投函箱 / 「このtopicを私に」と登録した受信枠 |
| publish / subscribe | 知らせを投函すること / 受信枠を登録して受け取ること |
| at-least-once | 必ず1回以上届くが、2回来ることもある配達の性質 |
| exactly-once | ちょうど1回だけの配達保証。追うコストが高くLATCHは採らない |
| エミュレータ | 本物と同じ作法で動く練習用の複製(環境変数で切替) |
| ストリーミングpull | 届いたそばから順次受け取るPub/Subの受信形態 |
| ack / ack deadline | 受け取り完了の返事 / これを過ぎると再配信される時間(600秒) |
| フォールバックリレー | 30秒超のpending行を再publishする、遅れた行の回収係 |
| debounce(トレーリング窓) | 入力が落ち着くまで出力を待つ工夫。最後の知らせから10秒 |
| version検査 | 知らせのversionを現行と比べ、過去は捨て未来は読み直す仕分け |
| 破棄(processed) | 想定内の除外。理由コードつきで処理済みに閉じること |
| 隔離(quarantined) | 何度試しても処理できない知らせを、人間が検査する対象として置くこと |
| 毒ペイロード | 処理する側を事故らせる壊れたデータ。再試行しても直らない |
| バックオフ | 失敗のたびに待ち時間を倍々に伸ばす再試行の間隔の工夫 |
| 回帰ピン | 同じ欠陥の再発を防ぐために固定する試験 |
| nil UUID | 値が全部ゼロのUUID。読めない知らせの隔離行の代替値 |
| フック | 処理の実体をあとで埋めるために予約しておく呼び出し位置 |

## 9.12 確認問題

1. 保存APIの中でマッチングを直接呼ぶ代わりにメッセージキューを挟むことで、
   ユーザーとWorkerのそれぞれが何から解放されるかを説明してください
2. at-least-once配信で同じ知らせが2回届いたとき、LATCHではどこで何によって
   「2回処理される」ことが防がれるか、2段階(受信の行確保・UNIQUE索引)で
   説明してください
3. publishをトランザクションの中ではなくコミットの後で呼ぶ理由を、
   「コミット前にpublishした場合」と「publish失敗時にロールバックした場合」の
   2つを作って説明してください
4. debounceの窓が「未知のversionだけを延長の根拠にする」理由を、再配信との
   関係で説明してください
5. 参照先のIntentが見つからない知らせを隔離せず破棄する理由と、逆に読めない
   知らせを破棄せず隔離する理由を、それぞれ「隔離棚は何を集める棚か」から
   説明してください
6. `make test-ci` の実行中に常設workerを一時停止するのはなぜか、
   「2つのworkerが同じsubscriptionを見ている」ときに起きることを想像して
   説明してください(ヒント: 9.4の窓はClock基準。テストの中ではFakeClock)

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった部分が、
読み返すべき節です)
