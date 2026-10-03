# 第24章 「できた」を証明する: 受け入れ試験の設計

- 種別: 解説(通読して理解を積む章)+コードリーディングの案内
- 前提知識: 第3章(fixtureとテストの基本)・第14章(proposalの内容とvisibility分岐)・
  第15章(グループマッチとD-06)・第17章(回答と競合クローズ・409)・
  第18章(期限切れバッチとFakeClock)・第19章(StubPushSenderのドライラン記録)。
  忘れていたら、その章の該当節に戻る案内を本文に入れてあります
- この章を読み終えるとできるようになること:
  - 受け入れ試験とは何か(unit試験との違い)と、LATCHのG3ゲートが何を判定するのかを説明できる
  - 仕様の文言を試験に翻訳するとき、最初に対応表を作る理由を、重複試験の害から説明できる
  - 「通知が出ない」「欄が存在しない」といった**ないことの証明**が、完全列挙と
    キー集合の一致という2つの形でどう書かれているか、A/B比較で差の原因を
    確定する構成とあわせて説明できる
  - 仕様の文言に忠実な配置(「3人YESでも成立しない」は4人組で作る)を選べる
  - 壊れていても緑になり続ける試験(偽陰性)の危険と、対照を置く対抗法を説明できる
  - 試験がデータを残さないためのteardown設計と、掃除の追加でなく構造の修正を
    選ぶ判断を説明できる
- 対応コード: `backend/tests/integration/test_g3_acceptance_e2e.py`(新設・816行・6試験)と
  `test_events_pipeline.py`・`test_embedding_pipeline.py`(teardownへusers削除の追加)。
  **製品コード(src/)の変更は1行もありません**
- 設計の根拠: `docs/plans/M3/ws-9-design.md`(特に§2.1の対応表・§2.3の試験設計)・
  `ws-9-report.md`。本文の「02 §4」等は `docs/02-*.md` の節を指します
- 次に読むもの: この章で終わりではなく、M4の実装とともに章が追加されます。
  なお本章にはLabがありません。扱う試験が共有のci-dbを使うため、
  手を動かす代わりにコードを読み込む章にしてあります(24.8で代わりの
  確認手段を用意してあります)。「staging環境が自立したら、同じ約束を
  手を動かして試せるLabを置きたい」という引き継ぎがあり、その実現が
  第28章とLab 14です(M4 ws-3でstaging専用の試験ハーネスができ、
  同じ27項目を共有DBに触れずに回せるようになりました)

## 24.1 実装が終わっても、まだ「できた」ではない: 証明する仕事が残る

ここまでの章では、コードの読み方と、部品の正しさを確かめるテストの書き方を
学んできました。第3章で書いたテストは「この関数にこの入力を与えたら、この
出力が返る」という形でした。あれは **unit テスト**、部品単体の試験です。

開発の終わりには、別の種類の問いが残ります。「仕様書に書いたとおりに
全体が動くことを、どうやって示すか」。この問いに答える試験を
**受け入れ試験(うけいれしけん、acceptance test)** と呼びます。利用者が
「これで引き取れる」と判定する基準を受け入れ基準といい、それを満たすことを
試験で証明するからです。unit テストが「部品は正しい」を積み上げるのに対し、
受け入れ試験は「約束全体が守られている」を直接確認します。

LATCHでは、この約束の全体像が `docs/02-*.md` の第4節に表になっています。
#1〜#27の各行に「検証方法」と「合格証拠」が対で並ぶ表で、
**マッピング表**と呼ばれます。たとえば#13の検証方法は
「閾値超過の候補を発生させる(お知らせ設定がproposals_onlyのIntentと
mutedのIntentの双方を含める)」です。合格証拠は「通知の到達記録。
mutedのIntentでは通知ドライラン記録に提案通知が出力されないこと、
latches.statusがproposedへ遷移しホームのLATCH候補から回答可能なこと」。
この文言がそのまま、本章で読む試験の設計図になります。

プロジェクトの区切りとして、LATCHには**ゲート**という関所があります。
マイルストーン(M0〜M4)の完了条件が満たされているかを、人間のオーナーが
資料を見て承認する場です。M3のゲートが **G3** です。G1はParserの精度を
実APIで測定するゲートでした(`howtos/g1-gate.md`)。G3は性質が違います。
判定材料は「02 §4の#13〜#23がグリーン」「グループLATCHの6検証がグリーン」
「追加試験4項目がグリーン」「プロトタイプ6画面が実装済み」の4つ
(docs/12-*.md M3節)。つまりG3のために作る装置は、測定器ではなく
**試験そのもの**です。ある判定のために整えた試験一式を
**ハーネス(harness)** と呼びます。馬に着けて操るための用具一式を
馬具と呼びますが、試験対象を同じように操って動かす一式、という
連想からの業界用語です。

M3 ws-9は、このG3ハーネスを作った作業単位です(2026-10-02マージ・
コミット24f88f6)。何が変わったかを `git show 24f88f6 --stat` で見ると、
変更は4ファイル・899行の追加だけです。新設の
`backend/tests/integration/test_g3_acceptance_e2e.py`(816行・6試験)、
既存2ファイルへのteardown修正(後で24.7で読みます)、それと作業記録の
報告書。**src/ の製品コードは1行も変わっていません**。

実装を1行も変えない作業単位が成立するのは、このプロジェクトがずっと
テストと実装を一緒に育ててきたからです。第14章から第21章までで読んだ
部品は、そのほとんどに既に試験が付いていました。ws-9の仕事は、その
資産の上に「仕様の文言を満たしていない部分」を数え、足りない分だけを
書くことでした。どう数えるかが、次の節の主題です。

ひとつだけ、環境の話を先に済ませます。02 §4は#13〜#23の実施場所を
staging(本番に近い検証環境)としていますが、stagingの構築は次の
マイルストーンM4の予定です。そこで今回は、G2のときと同じ解釈で
**ci環境(compose常設・スタブLLM)で実施してG3の証拠とする**ことが
記録されています(`docs/plans/STATUS.md` の「G3判定の待ち
事項」)。この章の試験がintegration ディレクトリに置かれているのは、
そのためです。なおこの読み替えの後始末も、実際に済みました。M4 ws-3で
staging環境が独立し、02#13〜#23の11項目がstagingで**再実行**されて
います(第28章)。ciでの実施は「stagingの準備が整うまでの代用」であり、
約束の文言どおりの場所での証明に置き換わった——この章と第28章が
約束の証明の2段階に分かれた、というのが実際の経過です。

## 24.2 最初の一歩は、書くことではなく数えること

試験を書く前に、まず調べることがあります。**今ある試験が、
仕様の各文言をどこまで証明しているか**を全部洗います。洗い上げの結果を
「文言→担当試験」の表にまとめたものが**対応表(たいおうひょう)**です。
ws-9の設計書 §1.3は、この調査そのものを15項目のリストとして残しています。
mainの実装を実査した記録で、設計の半分近くを占めます。

調査を終えると、世界は3つに分かれて見えます。

- ①文言を既存試験がすでに満たしている(何もしなくてよい)
- ②部品単位の試験はあるが、文言の一気通貫がない(新設)
- ③その文言を覆う試験が全くない(新設)

設計書 §2.1は、この仕分けを3枚の表にまとめました。
表1(02#13〜#23の11項目)から抜粋します。

| # | 担当試験(新=ws-9新設) | 備考 |
|---|---|---|
| 13 | 既存: notifications test_1・test_3/**新: 試験1(muted回答可能チェーン)** | 合格証拠の「ホームのLATCH候補から回答可能」をmuted Intentで一気に実証 |
| 17 | 既存: latches test_16 | 成立前後の可視範囲比較 |
| 22 | 既存: latchengine test_2・frontend latch-detail/**新: 試験2(公開設定2値A/B比較)** | 同一条件2組を実際のパイプラインでproposed化しAPI応答を比較 |
| 23 | **新: 試験2に統合**(応答キー規定集合ピン=座標系フィールド不存在) | 位置表現はarea_name(地名)のみ |

表2(グループLATCHの6検証)では6項目のうち新設は期限確定・競合クローズ・
部分成立のdefer側の3つ。表3(追加試験4項目)では自己重複集合の1つだけ。
合わせて**新設は6試験**。11+6+4=21項目のうち、7割方は既存試験だけで
覆えていたことになります。

対応表を作る理由は2つあります。

第一に、**重複した試験を書かないため**です。同じ文言を2つの試験が
証明すると、保守する対象が単純に2倍になります。それだけではありません。
文言の解釈が2箇所に分かれるので、将来どちらかが直されたとき、もう一方と
意味がずれます。ずれた2つの緑は、文言のどちらの版を守っているのか
分からなくなります。

第二に、**「不足」が不足だと分かるため**です。対応表の各行には
「既存のどこまで証明済みか」を書きます。この記述がないと、新設する試験の
中身を決められません。たとえば#13(muted)の既存分は
`test_notifications_api.py` のtest_3で「muted側のpush送信が0件・
notifications行が0行・latches.status=proposed」まで証明済みでした。
すると新設すべき差分は「ホームのLATCH候補一覧に現れること」「そこから
回答が受理されること」の2点だけだと決まります。試験1のdocstringには
この差分が「既存test_3(notifications)との差分=③④」と書かれていて、
読む人は「この試験が既存と重複しない理由」をすぐ確認できます
(`test_g3_acceptance_e2e.py:425`)。

第9章で見た「実装の前に調査がある」という作法の、試験版だと思って
ください。なお、対応表の元になる既存試験の調査には時間がかかりますが、
それを省略して「念のため」全部新規に書くのが、いちばん高くつく選択です。

## 24.3 「通知が出ない」の証明: 出るものを全部あげて、それだけと言い切る

新設6試験のうち3本を、これから順に読みます。まず試験1
(`test_1_muted_notification_chain_answerable`、
`test_g3_acceptance_e2e.py:416`)。証明するのは02#13の後半、
「mutedのIntentでは通知が出ない。それでも提案は作られ、ホームから回答できる」
です。

配置と実行はこうなっています(引数を簡単にして示します)。

```python
# test_g3_acceptance_e2e.py:427-448(試験1の前半)
clock = _clock()
start = _future(BASE_HOURS)
expires = _future(FAR_EXPIRES_H)
ha = await _user(api_client, field)
a = await _intent(api_client, db_engine, ha,
    _structured(start=start, expires=expires, notification_level="proposals_only"))
hb = await _user(api_client, field)
b = await _intent(api_client, db_engine, hb,
    _structured(start=start, expires=expires, notification_level="muted"))
async with db_engine.begin() as conn:
    await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.85)
with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
    await _handle(db_engine, clock, a["id"], push=StubPushSender(clock=clock))
```

ユーザーA(Intent a・お知らせ設定proposals_only)とユーザーB(Intent b・
同muted)を同じ条件で作り、第11章の候補検索(`run_candidate_retrieval`)を
回し、第13章のJevの結果(would_a_accept_b=0.9等)を直接DBへ注入し
(`_seed_jev`)、第14章の提案エンジン(`LatchEngine.handle`)をBに届かない
**StubPushSender**(第19章の、送らずに記録だけ残す送信者)つきで動かします。
そのあとBの目線でAPIを叩きます。APIからIntent作成まで、実DBと実API
(常設のapi)を繋いだ一連の流れです。このような、利用者の操作から結果まで
を端から端まで通す試験を**一気通貫(いっきつうかん)試験**、英語で
**E2E(End-to-End)** と呼びます。

ここでWorkerプロセスは立てません。観察したいのはLayer 5が作る提案の中身と
API応答であって、知らせが運ばれる経路そのものではないからです。経路の実証は
既存試験(M2 ws-8試験8・M3 ws-2試験7)が持っています。エンジンを直接呼ぶ
構成は、タイミングの揺らぎも入りません(設計書 §2.6-4の判断)。

### 出ないことの証明は、完全列挙で

前半の核心は、pushの記録を検証するこの1行です。

```python
# test_g3_acceptance_e2e.py:452-453
# ①muted側(b)の提案通知はドライラン記録に出ない(完全列挙)
assert [r["user_id"] for r in recs] == [uid_a]
```

「B宛ての通知がない」ことを言いたいだけなら、`uid_b not in ...` という
書き方もできます。しかしそれは「B宛てがリストに含まれていない」しかし
言いません。リスト全体がAの1件ちょうどだと**言い切る**のが `==` です。
もしB宛ての代わりに、誰か第三者宛ての通知が混ざっていても、このassertは
落ちます。「出るべきものが全部出て、それ以外は何も出ていない」。
出現しうるものを全部あげて、集合として一致させる書き方を、本章では
**完全列挙(かんぜんれっきょ)** と呼びます(試験コードのコメントも
同じ語を使っています)。

ないことの証明が難しいのには、本質的な理由があります。「Aがあった」は
Aを1つ見つければ済みます。「Bがなかった」を言うには、探した範囲が
閉じていて、その範囲にBがいないことを示す必要があります。 `==` による
比較は、範囲を閉じる工夫です。

### 「何も起きていない」と「何も残っていない」は違う

さらに、この試験は「通知が出ない」だけを証明して終わりません。続く③④で
「通知以外の全部は起きている」を確かめます。

```python
# test_g3_acceptance_e2e.py:460-465(③)・466-471(④)より
latch_id = await _latch_id_of(db_engine, [a["id"], b["id"]])
resp = await api_client.get("/v1/latches", headers=hb)
items = [i for i in resp.json()["items"] if i["id"] == latch_id]
assert len(items) == 1
assert items[0]["status"] == "proposed"      # ③ホームのLATCH候補にある
r = await _respond(api_client, hb, latch_id, "yes")
assert r.status_code == 200, r.text          # ④Bの回答が受理される
```

仮に③④がない試験を想像してください。通知0件・notifications行0件だけを
assertする試験は、提案そのものが作られていなくても緑です。「mutedのとき
通知を止める」実装が、間違えて「提案を何も作らない」実装になっていても、
同じ緑です。③④を同じ試験の中に入れることで、「止まっているのは通知だけ」
であることを、1本の試験が最初から最後まで証明します。24.6でこの問題を
一般化して扱います。

## 24.4 「欄が存在しない」の証明: 鍵の集合をまるごと言い当てる

試験2(`test_2_visibility_ab_comparison_and_no_coordinates`、
`test_g3_acceptance_e2e.py:530`)は、見え方の制御が仕様どおりかを証明します。
対象は02#22(公開設定2値のA/B比較)と02#23(座標の非開示)の2文言です。

### 1つだけ違う2組を並べる: A/B比較

02#22の検証方法は「同一候補をvisibility=hidden_until_matchとsummary_onlyの
双方で配置し、提案画面の内容を比較する」です。公開設定(visibility)は、
成立前に相手へ見せる情報の範囲を決める第14章の設定で、summary_onlyなら
条件サマリ(時間帯・地名・カテゴリ・予算)まで見せ、hidden_until_matchなら
成立まで「条件が合う候補があります」の段階まで隠します。

試験は、それを文字どおり「1つだけ違う2組」で実現します。

```python
# test_g3_acceptance_e2e.py:549-552
cases = [  # (a側visibility, b側visibility) — 以外の条件は両組完全同一
    ("summary_only", "summary_only"),
    ("hidden_until_match", "summary_only"),
]
```

両組とも時間・地域・カテゴリ・「焼肉」・予算5000を同じにし、jevも0.9/0.9で
同じにして、両方を実際のパイプライン(retrieval→jev注入→handle)で
proposed化します。visibilityだけが違う2組。この形なら、両組の応答に差が
あれば、その差の原因はvisibility以外にありえません。1つの変数だけを
動かして差を見る実験の組み方を、**A/B比較**と呼びます。差を調べたい
もの以外を全部同じに揃えるのが骨子で、実験計画の基本形です。

hidden側の相手(b2)をsummary_onlyにしてよいのは、分岐が「いずれかが
hiddenなら隠す」で発動するからです(第14章14.4)。両側hiddenにする必要は
ありません。

### キー集合の一致: 「これとこれだけ」と言い切る

比較の中身は、proposalの中身を**キー(辞書の欄名)の集合**で比べます。

```python
# test_g3_acceptance_e2e.py:476-500(定義)・597-611(検証)より
EXPECTED_DETAIL_KEYS = {
    "id", "status", "response_deadline", "expires_at", "created_at",
    "completed_at", "proposal", "is_group", "my_response",
    "remaining_responses", "participants", "time_summary", "area_name",
}
SUMMARY_PROPOSAL_KEYS = {
    "time_summary", "area_name", "headcount", "category_primary",
    "category_secondary", "budget", "match_level",
}
...
# ①組1(summary_only×2): 条件サマリ7キー
for body in summary_views:
    assert set(body["proposal"]) == SUMMARY_PROPOSAL_KEYS
...
# ②組2(hidden混在): {headcount, match_level}のみ
for body in hidden_views:
    assert set(body["proposal"]) == {"headcount", "match_level"}
```

ここでも効いているのは `==` です。 `set(body["proposal"])` は応答に
実際に入っている欄の集合です。それが定数と**完全一致**するということは、
「この7欄が入っていて、それ以外の欄は何も入っていない」まで言っています。
値の中身(headcount==2・budget=={"max": 5000}など)も別途検証しますが、
権利のある欄が過不足なくそろっていることの証明は、キー集合の一致が
担います。

さらに試験は、両組の差が条件サマリ5情報ちょうどであることを、引き算で
確かめます。

```python
# test_g3_acceptance_e2e.py:612-619
# ③キー集合の差分==条件サマリ5情報のみ
assert SUMMARY_PROPOSAL_KEYS - {"headcount", "match_level"} == {
    "time_summary", "area_name", "category_primary",
    "category_secondary", "budget",
}
```

7から2を引いた残りが、仕様の言う「条件サマリ」の一覧と一致する。
A/Bの差が「見せるべき情報の範囲」だけであることを、両側から挟み込んで
います。

### ブラックリストとホワイトリスト: どちらが「ないこと」を言えるか

02#23は「座標精度の値が表示されない」です。加えて02#22は、raw_text(入力の
自然文)・NG条件・名前・プロフィールが表示されないことも求めています。
「入っていない」ことを調べるリストも、試験は用意しています。

```python
# test_g3_acceptance_e2e.py:502-527(定義)・620-622・634(検証)より
FORBIDDEN_KEYS = {
    "raw_text", "soft_constraints", "ng_conditions", "soft_texts",
    "display_name", "profile", "user_id", "bio", "name",
}
GEO_KEYS = {
    "lon", "lat", "geo_lon", "geo_lat", "geo_center", "geo_radius_m",
    "latitude", "longitude", "coordinates", "location", "accuracy", "radius",
}
...
for body in views:
    assert _all_keys(body) & FORBIDDEN_KEYS == set(), body
...
    assert _all_keys(body) & GEO_KEYS == set(), body
```

`_all_keys` は辞書を再帰的に歩いて全キーを集めるヘルパーです
(`test_g3_acceptance_e2e.py:400-410`)。値がリストのときは、その中の
辞書まで対象にします。集めた全体と禁止リストの共通部分(`&`)が空である
ことを確認します。

この書き方には、上限がはっきり見えています。FORBIDDEN_KEYSもGEO_KEYSも
**ブラックリスト**、つまり「悪いと分かっている名前」の列挙だからです。
`bio` という欄が混入していれば検出できますが、実装者が `self_introduction`
という新しい欄を足したら、リストに入っていないので検出できません。
ないことの証明として、列挙の網は原理的に完璧になりません。

だから試験の本命は、もう一つのリストです。応答のトップレベル(辞書の
一番外側)のキー集合が、**規定の13欄と完全一致**すること。

```python
# test_g3_acceptance_e2e.py:623-625
for body in views:
    assert set(body) == EXPECTED_DETAIL_KEYS
```

こちらは**ホワイトリスト**、「許されるもの全部」の列挙との完全一致です。
13欄の名前をあげて、それ以外は何ひとつ認めない。座標を運びそうな欄が
未来のどんな名前で追加されても、このassertは(良い意味で)落ちます。
辞書のキーは閉じた集合なので、列挙が完璧になりうる珍しい場所です。
「ないこと」を証明したい場面では、禁止する名前を増やすより、許す名前を
全部あげて残りを一括拒否するほうが強い。本章でいちばん持って帰って
ほしい形です。

位置の情報が完全に消えているわけではない点も、仕様どおりです。地名
(area_name)は表示されます。試験は `p["area_name"]` が空でない文字列で
あることを確かめています(`test_g3_acceptance_e2e.py:607`)。地名は
逆転ジオコーディング(座標から地名を引く、第1章のgeo)で得た文字列で、
数値の座標そのものではありません。隠すべきは「会える場所の幾何学」で
あって「地域の名前」ではない、という仕様判断(02 §3)の表れです。

### 「表示されない」をAPI応答で証明して足りるのか

02#22の合格証拠は「提案画面の内容を比較する」です。画面の話なのに、
試験はAPI応答を比べています。これは設計書 §5-3で承認を得た読み替えです。
理由は2つあります。第一に、フロントエンドはAPI応答を描画するだけで、
応答に存在しない情報は絶対に表示されません。提案詳細の部品
(`frontend/src/latch/detail.js`)は、応答のtime_summaryキーの有無で
表示を出し分ける実装です。この分岐自体は、既存の
`frontend/tests/latch-detail.test.js` がunitで証明済みです。第二に、
このプロジェクトの証拠の定義(10 §5)は「API応答・DB記録・通知ドライラン
記録」の3つで、ブラウザを動かす試験は含まれていません。層を分けて、
それぞれの層で証明責任を果たす構成です。

## 24.5 文言を配置に翻訳する

残る3本(試験3〜5)は、グループLATCHの6検証(09 §3)のうち、既存試験が
覆っていなかった側を埋めます。ここでの腕のみどころは、仕様の文言を
**配置(このユーザーが何人いて、誰がどう答えるか)** に忠実に翻訳する
ことです。

### 試験3: 期限時の確定条件

09 §3の文言は「全員YESが揃わないままresponse_deadlineを経過させる。
expiredで閉じる(3人YESでも成立しない)」です。

読み方を誤りやすいのは「3人YES」の部分です。3人組で3人YESとすると、
それは全員YES、つまり成立(matched)です。成立するケースで「成立しない」は
証明できません。文言が言っているのは、**必要人数に満たない3人YES**の
こと。だから試験は4人組を作り、3人だけYES(partial_accept、あと1人の
回答が残った状態)にしてから期限を経過させます
(`test_g3_acceptance_e2e.py:648-656`)。

```python
# test_g3_acceptance_e2e.py:657-676(後半)
# deadlineを過去へ(sweeper遅延でstatus=partial_acceptのままの状態を再現)
async with db_engine.begin() as conn:
    await conn.execute(
        text(
            "UPDATE latches SET response_deadline ="
            " CAST(:past AS timestamptz) WHERE id = CAST(:l AS uuid)"
        ),
        {"past": SystemClock().now() - timedelta(hours=1), "l": latch},
    )
clock = FakeClock(SystemClock().now())
# ①run_onceが当該latchを処理(他試験残骸はteardown+時間窓分離で不在)
assert await _sweeper(db_engine, clock).run_once() == 1
# ②expired(matchedにならない)
assert (await _latch_row(db_engine, latch))["status"] == "expired"
# ③latch_status_events: partial_accept→expired・user_id=NULL(システム起因)
assert ("partial_accept", "expired", None) in await _events(db_engine, latch)
# ④4人目の回答は409
r4 = await _respond(api_client, hs[3], latch, "yes")
assert r4.status_code == 409, r4.text
assert r4.json()["error"]["code"] == "LATCH_EXPIRED"
```

期限の経過は、実時間を待つのではなく `response_deadline` をDBで過去に
書き換えて作ります。api常設プロセスの時計は差し替えられない(そのプロセスの
中のClockには手が届かない)ため、代わりにDBの値を動かすのです。Lab 6で
自分の手でやったのと同じ発想です。試験ファイル自身の `_latch` ヘルパー
(第18章の試験と同じ流儀の複製)のdocstringに
「now基準はSystemClock(api常設プロセスのClockと同一・回答APIと干渉しない)」
とあるのは、このねらいを明記したものです(`test_g3_acceptance_e2e.py:288`)。

assert①の `run_once() == 1`(sweeperがこの1行だけを処理した)は、
「sweeperが動いた証拠」を処理件数で残す工夫です。③のuser_id=NULLは
「誰の操作でもなくシステムが閉じた」ことの記録で、第17章17.3の遷移の
台帳(latch_status_events)の読み方そのものです。

この試験のdocstringには、観察**しない**ことまで書いてあります。
「FakeClockをset/advanceしないためrun_once内nowがsweeper構築時の
last_tickと同一時刻になり(created_at > last_tick不成立)クローズ検知
drainは観測しない」(`test_g3_acceptance_e2e.py:644-646`)。drain(閉じた
提案の保留キュー解放・第18章18.7)は別の既存試験の担当です。試験が
見ていないものを明記しておくと、あとで読む人は「この緑はdrainを含まない」
と誤読せずに済みます。

### 試験4: 競合クローズ(グループ成立側)

09 §3は「あるIntentが属する2つの提案を用意し、片方を成立させる。
もう片方はcancelledで閉じる」を検証項目として求めます。第17章17.5で
既存試験(test_latches_api test_11)が1対1×1対1の組でこれを証明しました。
ws-9が足したのは、**成立側をグループにした**組です。

共有するのはIntentです。i2を含む2つの提案を用意します。1つはグループ
{i1,i2,i3}、もう1つは1対1{i2,iX}です(iXは部外者U4のIntent)。
グループの3人がYES:

```python
# test_g3_acceptance_e2e.py:702-707
assert (await _latch_row(db_engine, group_latch))["status"] == "matched"
# ②1対1側はcancelled(もう片方はcancelledで閉じる)
assert (await _latch_row(db_engine, pair_latch))["status"] == "cancelled"
# ③latch_status_events(1対1側: proposed→cancelled・user_id=NULL=システム起因)
assert ("proposed", "cancelled", None) in await _events(db_engine, pair_latch)
```

最後のassertで、iX(非共有のIntent)は**activeのまま**であることも確かめます
(`test_g3_acceptance_e2e.py:728`)。閉じるのはi2を含む提案であって、iXの
人生ではありません。iXは次の候補探しに戻れます。1対1×1対1の既存試験と
対にすることで、競合クローズが「グループ成立が1対1を閉じる」方向も
覆しました。

### 試験5: 部分成立の禁止(defer側)

6検証の「部分成立の禁止」は、文言が「一部が**noまたはdefer**、残りがyes」
と書いています。no側は既存試験(latches test_9)が証明済みでした。残る
defer側(今回は見送る)を、3人組の2人YES→1人deferで埋めます
(`test_g3_acceptance_e2e.py:740-749`)。いちばん大事なのは最後の2行です。

```python
row = await _latch_row(db_engine, latch)
assert row["status"] == "rejected"                     # 即時・部分成立なし
assert row["responses"][-1]["response"] == "defer"     # 区別保持
```

deferで閉じた行も、記録上はちゃんとdeferと区別されて残る。文言の「または」
は、no側とdefer側を別々の試験で覆いました。1本に詰め込むと、落ちたときに
どちら側の問題か分かりにくくなるからです。

## 24.6 壊れていても緑の試験: 偽陰性と、その対抗としての対照

ここまで読んだ試験設計の多くは、ひとつの危険と戦っていました。それに
名前を付けます。

テストが赤になるとき、本当に壊れているとは限りません。壊れていないのに
赤になることを**偽陽性(ぎようせい)**と言い、誤報とも呼ばれます。
より恐ろしいのは逆で、壊れているのに緑のままになること——
**偽陰性(ぎいんせい)**、見逃しです。偽陽性は調査のムダで済みますが、
偽陰性は本番の事故としてあとで見つかります。試験を設計するとき、
削るべきは偽陰性です。

試験1の③④(24.3)は偽陰性への対抗でした。「通知0件」だけでは、実は
何も起きていない可能性が残ります。だから「通知以外は起きている」を
同じ試験で証明しました。試験2にも同じ工夫があります
(`assert body["status"] == "proposed"` に「パイプラインが提案化した前提の
保証」というコメント、`test_g3_acceptance_e2e.py:626`)。`_seed_jev` の
`assert res.rowcount == 1, "jev直接投入対象行なし"`(:212)も同類で、
前段のretrievalが本当に候補行を作っていたことを、投入時にすぐ確かめて
います。前提が破れていたら、奥のassertまで行かずに、その場で分かりやすい
メッセージとともに落ちる。**早く落ちる試験は、切り分けがやすい試験**です。

### 試験6: 自己重複集合の除外

この対抗がいちばん大きな形で効いているのが試験6
(`test_6_self_duplicate_group_excluded`、`test_g3_acceptance_e2e.py:752`)
です。証明するのは追加試験のFR-17後半「同一ユーザーのIntentを2つ含む
ような3〜4人集合が、group_candidatesに生成されない」こと。

配置は、同一ユーザーUのIntentを2つ(s・s2)と、別ユーザーV・WのIntent
各1つ。全員同じ時間・地域・カテゴリ・人数希望(2〜4人)にして、
GroupEngine.handle(s)を1回だけ回します。主張の中心はこのassertです。

```python
# test_g3_acceptance_e2e.py:798-810(①)
# ①{s,s2}を同時に含む集合行==0件
async with db_engine.connect() as conn:
    n_bad = (
        await conn.execute(
            text(
                "SELECT COUNT(*) FROM group_candidates"
                " WHERE intent_ids @>"
                " ARRAY[CAST(:s AS uuid), CAST(:s2 AS uuid)]"
            ),
            {"s": s["id"], "s2": s2["id"]},
        )
    ).scalar_one()
assert n_bad == 0
```

「悪い行が0件」。ここで、このassertだけの試験を想像してください。もし
GroupEngineが何らかの故障で**何も生成しなくなって**いたら? n_badは
0のまま、試験は緑です。Pool検索(第15章15.2)が空を返しても緑です。
0件は「悪いものがない」ことと「そもそも何も起きていない」ことの
両方と整合してしまうのです。

そこで試験は、**対照(たいいしょう)**を置きます。実験科学では、処理を
施した群と施さない群を並べて差の意味を確かめます。この並べた相手を
コントロールと呼びます。本章の対照は、そのコントロールのうち
「正しく動いていれば必ず生じる結果」の側です。

```python
# test_g3_acceptance_e2e.py:811-814(②)
# ②対照: {s,V,W}(Uの2つ目を含まない3人集合)は生成される
# (全員の[min,max]に3が∈のため成立・偽陰性の対抗)
gc = await _group_of(db_engine, [s["id"], v["id"], w["id"]])
assert gc is not None and gc[1] == "candidate"
```

Uの2つ目を含まない3人集合{s,V,W}は、仕様上**生成されているはず**の
ものです(人数希望2〜4人の全員に3人が収まる)。この行が実際にできている
ことを確かめてから、「その上で、自己重複の集合だけは0件」と言う。
エンジンが正しく動いている世界でしか、0件のassertは意味を持ちません。
対照がその世界を保証します。③(`:815-816`)はs×s2の1対1候補行
(match_candidates)も出ないことの確認で、1対1側の自己ペア除外は既存の
hardfilter test_10が担当、という分担もdocstringに書かれています。

なお、この排除は1箇所のifで守られているのではありません。Pool検索の
条件(起点ユーザー以外、`i.user_id <> :origin_user_id`)が前段でs2をPool
から外します。その後は、ペア互換のSQL条件(`i1.user_id <> i2.user_id`)、
貪欲法の中のユーザー重複検査、そしてINSERT直前の最終検査が続きます。
1つの障壁が破られても次が止める、**多層防御(たそうぼうぎょ)** の層です。
unit側には
SQL文字列そのものをピンする試験もいます
(`tests/unit/matching/test_group_engine.py:338` が
`assert "i1.user_id <> i2.user_id" in sql`)。試験6はその多層の外側、
実DBに配置して全体を回したときの実効を確認する最後の一枚です。

### 試験にも限界がある。それを書き残す

対照を置いても、守れないものがあります。ws-9の報告書は、レビューで
判明した試験6の限界をそのまま記録しています。「対照assertは『何も生成
されない』偽陰性には効くが、外部残留Intentが貪欲法の選択を置き換える
失敗まではガードしない — teardown+purge-match-subが前提」
(`docs/plans/M3/ws-9-report.md` 補足)。共有DBに誰かの試験の残骸Intentが
残っていたら、Poolの15人が入れ替わり、対照の{s,V,W}が選ばれなくなる
恐れがある、という話です。試験が自分の前提を文書に残すこと——これも
ハーネスの品質のうちです。前提を守る仕掛けが次節の主題です。

## 24.7 試験のあとを片付ける: 掃除の追加でなく、構造の修正

ws-9は6試験の新設と同時に、地味なもう一つの仕事をしていました。
2つの既存ファイルへの、小さな変更です。

### 漏れていたusers

`test_events_pipeline.py` と `test_embedding_pipeline.py` には、試験の
土台を作って片付ける `user_env` というfixtureがあります。fixtureの
後片付け側の処理を **teardown(ティアダウン)** と呼びます。この
teardownはmatch_eventsとintentsまで削除して、**users行を消していませんでした**。
1行漏れるだけなら大したことにならないのですが、test-ciは全試験を回す
ので、1回あたり約12件のusers行がDBに蓄積します。M3 ws-1の検証時には
400件が見つかって、その場で掃除しました。しかし漏れ自体は止まって
いないので、ws-9のマージ検証のときにはまた276件が溜まっていました。
1回の掃除は未来を止めない。これがこの問題の教訓です。

対処の選択肢は2つありました。

- (a) 検証手順に「毎回、掃除SQLを打つ」を書き足す
- (b) fixtureのteardownへusers削除を足す

設計書が選んだのは(b)です。テストコードを2ファイル直すだけで、以後の
すべての実行から漏れが消えます。(a)は手順書という人間の記憶と規律に
依存するので、うっかり再発が起こります。

```python
# test_events_pipeline.py:174-180(test_embedding_pipeline.py:240-246も同形)
# usersも消す(STATUS ws-1節の観察: test-ci 1回あたり約12件のusers
# 蓄積。intents削除の後=FK順。...)
await conn.execute(
    text("DELETE FROM users WHERE id = CAST(:uid AS uuid)"),
    {"uid": user_id},
)
```

第9章9.8で読んだ `purge-match-sub` と同じ思想です。あのときは「溜まった
知らせを毎回掃除する」のでなく「subscriptionごと消して、溜まりようが
ない形にする」ことで直しました。今回は「掃除SQLを手順に足す」でなく
「teardownが元から消す形にする」。**レシピに掃除を足すのでなく、構造が
汚れないように直す**。これを恒久対策と呼びます。なお、既に溜まっていた
過去分は、1回限りの掃除SQLを報告書の検証手順に記録して処理しました
(恒久対策は未来を止める、過去は一度掃く、という分担です)。

### FK順という削除の順番

usersを消す位置は、intentsを消した**あと**です。usersの行はintentsから
参照(外部キー、FK)されているので、usersを先に消すとDBが参照切れを
許しません。被参照側をあとに消す、参照側を先に消す、この順序を
**FK順**と呼びます(第22章の削除カスケードで同じ理由を追いました)。

新設ファイルの `field` fixtureは、この順序を9段のDELETEで全面的に
書いたものです(`test_g3_acceptance_e2e.py:46-77`)。
latch_status_events→calibration_records→notifications→latches→
match_candidates→group_candidates→match_events→intents→users。
前から順に、参照する側から参照される側へ削除が進みます。各DELETEの
対象は `auth_subject LIKE :p` のプレフィックス一致で、試験ごとに一意な
`m3ws9-` + uuid8の接頭辞に絞られます。**自分が作った行だけを、自分の
番号で消す**。共有DBで試験同士が邪魔し合わないための、このプロジェクト
の確立した流儀です(第17章・第18章の試験も同じ形です)。

もう一つ、時間の側の分離もあります。試験のIntentは開始時刻を
now+5日(BASE_HOURS=120)に統一してあります。既存試験の一部は
「まもなく始まるIntent」(期限が近い)を対象にするので、時間帯が重なると
意図せず相手の対象に入ってしまいます。+5日の窓に全部置くことで、
干渉から構造的に離れるのです(期限切れを試す試験3だけは、DBの値を
過去へ書き換えて再現します。実時間を待たない)。

### 動いた証拠

恒久対策の効果は、マージ後の検証で確かめられています。2026-10-02の
mainで `make test-ci` が**1475 passed**(=1469+6)。teardownの側は、
修正前に溜まっていたm2ws1-/m2ws2-の276件を掃除で0件にしたあと、
test-ciを1回走らせても**0件のまま増えない**ことが確認されました
(`docs/plans/STATUS.md` のws-9マージ記録)。unit テスト1215件・
frontend 217件・lint 274ファイルも緑のまま、製品コードには一切
触れていません。なお、同じ日の後のマージ(m3-g3fix。お知らせの文言分岐と
成立通知の実装・第23章23.2)で、unitは**1219件**・frontendは**223件**・
test-ciは**1480件**まで増えています(第1章の数値はこの現況です)。

## 24.8 自分で確かめる

この章の試験は共有のci-dbを使うので、本章では実行しません。代わりに、
DBに触らずに確かめられることを3つ用意しました。

1. 試験の収集だけを見る(実行はしない)。収集(collection)はテストの
   名簿だけを作る段階で、DBには接続しません。

   ```
   cd backend
   uv run pytest --collect-only tests/integration/test_g3_acceptance_e2e.py -q
   ```

   期待される出力の末尾:

   ```
   6 tests collected in 0.01s
   ```

2. 自己重複除外のunit側親戚を動かす(第3章と同じ、外部環境なしの試験)。

   ```
   cd backend
   uv run pytest tests/unit/matching/test_group_engine.py -v
   ```

   30件が約0.1秒で緑になります。`test_handle_user_conflict_skips_insert`
   (user_id重複メンバー→INSERT前検査でスキップ)が、24.6の試験6の
   unit側の対応物です。

3. 対応表を自分の目で読む。`docs/plans/M3/ws-9-design.md` の§2.1に
   3枚の表があります。表1の11行について、「既存」と付く行がどの試験
   ファイルのどの試験かを、実際に開いて確認してみてください。
   (例: #17のlatches test_16は `tests/integration/test_latches_api.py`
   にあります。)対応表の行を本物のコードにつなげられることが、
   この章の到達点です。

## 24.9 この章の再統合

M3の最後の作業単位ws-9は、製品コードを1行も変えませんでした。仕事の
全部は「仕様の文言を、証拠に換えること」でした。その手順として、
この章が扱った知識を並べ直します。

1. まず**対応表**を作る。既存試験が各文言をどこまで証明しているかを
   調べて、不足だけを新設する。重複試験は保守を2倍にし、解釈を2箇所に
   割る
2. **ないことの証明**には範囲を閉じる。出るものの完全列挙(リストとの
   `==`)、入る欄の規定集合との完全一致(キー集合の `==`)。ブラックリスト
   よりホワイトリスト
3. **文言に忠実な配置**を選ぶ。「3人YESでも成立しない」は4人組の
   partial_acceptで作る。「または」の両側(no/defer)は別々の試験で覆う
4. **対照**を置く。0件のassertは「何も起きていない」世界でも緑なので、
   「正しく動いていれば必ず起きること」を同じ試験で先に証明する
5. **teardownを構造にする**。掃除を手順に足すのではなく、fixtureが
   片付ける形に直す。自分の行は自分のプレフィックスとFK順と時間窓で
   他と離す

この作法は受け入れ試験に限りません。第24章までに読んできた実装の試験も、
同じ規律の上に書かれています。次にあなたが自分のプロジェクトでテストを
書くとき、「このassertが緑なら、何が
言えるか」を1回聞いてから書き始めるのが、この章のいちばん小さくて
効く習慣です。

## 24.10 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| 受け入れ試験 | 仕様の受け入れ基準を満たすことを証明する試験。部品単体のunit テストと対になる |
| マッピング表 | 仕様書02 §4の、検証方法と合格証拠を#番号ごとに並べた表。試験設計の元になる約束 |
| ゲート | マイルストーンの完了条件を人間が承認する関所。G3はM3のゲート |
| ハーネス | ある判定のために整えた試験一式。G3ハーネス=test_g3_acceptance_e2e.py等 |
| 対応表 | 仕様の各文言に「既存/新設」の担当試験を割り当てた表。重複を防ぎ不足を見つける |
| 一気通貫(E2E) | 利用者の操作から結果までを端から端まで通す試験。End-to-End |
| 完全列挙 | 出るものを全部あげて集合として一致させる書き方。「それ以外は何もない」まで言う |
| ホワイトリスト | 許されるもの全部を列挙して残りを一括拒否する表。ブラックリスト(禁止リスト)の逆 |
| A/B比較 | 1つの変数だけが違う2組を並べて、差の原因を確定する実験の組み方 |
| 偽陽性 | 壊れていないのに赤になること(誤報) |
| 偽陰性 | 壊れているのに緑のままになること(見逃し)。試験設計で削るべき側 |
| 対照 | 正しく動いていれば必ず起きる結果の側。0件assertの偽陰性への対抗 |
| 多層防御 | 1つの障壁でなく複数の層で守る作り。自己除外はPool検索からINSERT前検査まで4層 |
| teardown | fixtureの後片付け。FK順に従って自分の行だけを消す |
| 恒久対策 | 手順の追加でなく構造の修正で再発を止めること。purge-match-subと同思想 |

## 24.11 確認問題

1. ws-9が製品コードを1行も変えずに済んだ理由を、「テストと実装を
   同時に育ててきた」ことと「対応表」との関係から説明してください
2. 試験1の `assert [r["user_id"] for r in recs] == [uid_a]` は、
   `assert uid_b not in [r["user_id"] for r in recs]` より何を多く
   言っているか、第三者宛ての通知が混ざった場合を例に説明してください
3. 試験1から③(B視点の一覧にproposedで含まれる)と④(Bの回答が200)を
   消すと、どんな実装の間違いが見逃されるようになるかを言ってください
4. 試験2で `set(body) == EXPECTED_DETAIL_KEYS`(13キー完全一致)が
   FORBIDDEN_KEYS・GEO_KEYSのブラックリスト検査より強い理由を、
   実装者が `self_introduction` という新しい欄を足した未来で説明してください
5. 「3人YESでも成立しない」を試験3が4人組の3人YES(partial_accept)で
   作る理由を、3人組で作った場合に起きることを使って説明してください
6. 試験6の対照assert({s,V,W}の生成確認)を消すと、GroupEngineが
   「何も生成しない」故障を起こしたとき試験はどうなるか。また、対照が
   あっても防げない失敗として報告書が挙げているものは何かを言ってください
7. m2ws1-/m2ws2-のusers蓄積を「検証手順の掃除SQL」ではなくteardownへの
   1行で直した判断を、第9章9.8のpurge-match-subの判断と同じ言葉で
   説明してください
8. `field` fixtureの9段のDELETEがlatch_status_eventsから始めてusersで
   終わる順序の根拠を、FK(外部キー)の参照の向きで説明してください
