# Lab 12 シードを生んで確かめる: seed-generateとseed-verify

- 種別: チュートリアル(手を動かして必ず成功体験を得る。壊しても元に戻せます)
- 前提知識: 第26章(決定的生成器・期待値表の2層)。Lab 1の `make` の使い方と、gitが
  「差分を記録する道具」であること(第1章1.9)を思い出せるとスムーズです
- 所要目安: 10〜15分。データベースもdockerも使いません。お金もかかりません。何度でも
  やり直せます
- このLabでできるようになること: 決定的生成器で300件のシードを生み、gitに「再生成しても
  1バイトも変わらない」ことを確認してもらえる。検査器が何を見ているか出力で確かめ、
  YAMLに1か所の壊れを入れて**検査がそれを見つける瞬間**を観察できる。さらに、基準日を
  変えて生み直したときに「何が変わって、何が変わらないか」を自分の目で分類できる
- 次に読むもの: 第26章で触れたとおり、このシードはstagingでの機能試験(ws-3)の素材です。
  「生んだ資産が、後の単位でどう消費されるか」は、その単位の教材が開放されたときの
  楽しみに取っておいてください

## 0. このLabで何をするか

このLabの登場人物は3つです(第26章で読んだとおり)。

| 登場人物 | 何をするもの | 入り口 |
|---|---|---|
| 生成器 | 固定シード42で、Intent300件+ユーザー100人を生む | `make seed-generate` |
| 検査器 | 生まれたYAMLを5項目で機械検査する | `make seed-verify` |
| m4-seed.yaml | 生成物。期待値表(2層)を含む本体 | `docs/testassets/m4-seed.yaml` |

大事な前提を最初に。このLabで生まれるものは**git管理下のファイル**です。そのため、
どんなにいじっても `git checkout`(第1章で読んだ「記録した時点へ戻す」)で元に戻せます。
ci環境のコンテナにも、staging環境にも、触れません。安心して壊してください。

以降、すべての手順をリポジトリのルート(`latch/`)で実行します。

## 1. シードを生んで、gitに確かめてもらう

まず、生成器を動かします。基準日には「**自分が実行する日の翌日**の11時」をJST付きで指定
します(時刻検証が過去を拒むため。第6章6.3)。以下の例は、日本時間の10月3日に実行した
ものです。あなたが実行する日で読み替えてください。

```bash
make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"
```

何が起きるか: 生成器が300件のIntentと100人の合成ユーザーを組み立て、全ペアのLayer 1判定
を計算して、YAMLを書き出します。数秒で終わります。

```
cd backend && uv run python -m latch.testassets.generator --base-datetime 2026-10-04T11:00:00+09:00
generated ../docs/testassets/m4-seed.yaml: intents=300 users=100 pair_total=44850 layer1_pass=576
```

`intents=300 users=100` は生成した件数、`pair_total=44850` は全ペア数、`layer1_pass=576`
はそのうちLayer 1を通ったペアの数(第26章の集計と同じ値)です。

**実行する前に予測を書いてください**(my-notes.mdへ)。今、gitに「何か変わった?」と聞くと、
何と答えると予想しますか?あなたはたった今、すでにコミット済みの資産と同じ引数で生成を
実行しました(マージ済みのm4-seed.yamlは、この同じコマンドで生まれています)。

では、聞いてみます。

```bash
git status --short docs/testassets/
```

期待される出力:

```
(何も表示されない)
```

**何も出ません**。これが「バイト同一」です。gitは「差分ゼロ」を宣言しました——あなたが
たった今生んだ300件は、レビューを通ってコミット済みの資産と、1バイトも違わない
(第26章26.3)。テスト資産が「証拠」である理由が、この静けさです。

## 2. 検査に通す

生んだYAMLを、検査器にかけます。

```bash
make seed-verify
```

期待される出力:

```
cd backend && uv run python -m latch.testassets.verify
OK: ../docs/testassets/m4-seed.yaml の機械検査に合格(a構造/b分布/c意図的配置/d aggregate照合/e地名)
```

末尾の `(a)(b)(c)(d)(e)` が、検査の5項目です。

- **a構造**: idの一意性・必須3フィールド・時刻が基準日の1〜5日後に入っているか等
- **b分布**: 時間帯60/90/120/30・カテゴリ150/120/30・志向210/90が正確に実現しているか
- **c意図的配置**: EX期待値(33件)を、参照実装で**再判定**してexpectedと突き合わせ
- **d集計照合**: 参照実装で44,850ペアを**全部再計算**し、YAMLの集計値と一致するか
- **e地名**: 場所が7地点の許可リスト内か(第26章26.7)

(c)(d)の「再計算」が、第26章で読んだ二重実装の突合です。検査器は生成器の数字を信じないで、
別の機会に自分で計算し直しています。

## 3. 生成物の中身を覗く

YAMLは約8000行あります。全部は読みません。条件に合う行を探して表示する `rg` コマンド
(`rg -A5 パターン ファイル` で「パターンに合う行と、そのあと5行」を表示)で、
4か所だけ覗きます。

```bash
sed -n '1,15p' docs/testassets/m4-seed.yaml
rg -A11 "^- id: SD-001$" docs/testassets/m4-seed.yaml
rg -A7 "^  - id: EX-004$" docs/testassets/m4-seed.yaml
rg -A12 "^  aggregate:" docs/testassets/m4-seed.yaml
```

`sed -n '1,15p'` は「先頭15行を表示」のコマンドです。2つ目以降の `-A11` 等は
「パターンに合う行と、そのあと11行」の表示指定です。EXの `id` はリストの中のリスト
(2スペース下がった位置)に書かれているため、パターンにもインデント( `^  - id:` )が
必要です。期待される出力(順に):

```yaml
meta:
  status: draft
  created: '2026-10-03'
  generator: latch.testassets.generator
  base_datetime: '2026-10-04T11:00:00+09:00'
  random_seed: 42
  user_count: 100
  intent_count: 300
  pair_total: 44850
  time_bucket_targets:
    day: 60
    evening: 90
    night: 120
    other: 30
  category_targets:
```

`meta` が、第26章26.3の「記録しておくもの」の実体です。自分が渡した引数
(`base_datetime`)と、シード値(42)が書かれています。この数行のおかげで、後から
「この資産はどう生まれたのか」を正確に再実行できます。`time_bucket_targets` は、
分布の目標値(昼60/夕90/夜120/その他30)の記録です。

```yaml
- id: SD-001
  user: SU-010
  ex_group: EX-001
  raw_text: 今夜天文館あたりで居酒屋に行きたい。のんびり席を温めたい
  structured:
    category:
      primary: drinking
      secondary: 居酒屋
    alcohol_involved: true
    time:
      start: '2026-10-05T20:00:00+09:00'
      end: '2026-10-05T23:00:00+09:00'
```

Intent本体の1件です。`ex_group: EX-001` は「このIntentは、意図的配置(EX-001の素材)として
必然的に置かれたもの」の印です(第26章26.4の第1段階)。場所は許可リストの「天文館」、
時刻は基準日の1日後(10月5日)の夜・20時から23時。分布の「夜」バケットに入っています。

```yaml
  - id: EX-004
    kind: pair
    intents:
    - SD-007
    - SD-008
    expected: fail
    fail_reason: age_alcohol
    basis: 'D-10(10 §3 #27)・06 §2(飲酒は双方20歳以上)'
```

個別期待(EX)の1件です。SD-007とSD-008のペアは「fail・理由はage_alcohol(飲酒は
双方20歳以上の条件)」で、根拠の仕様の節番号まで書いてあります。18〜19歳のユーザーの
Intentと20歳以上の飲酒Intentが組み合わさったとき、Layer 1がこれを落とす——それが
**起きるはず**という約束です。

```yaml
  aggregate:
    pair_total: 44850
    layer1_pass: 576
    layer1_fail: 44274
    fail_breakdown:
      self_pair: 305
      category_mismatch: 25926
      time0: 17263
      distance: 80
      people: 574
      budgetmin: 9
      blocked: 0
      age_alcohol: 117
```

第2層の集計です。44,274件が落ちる理由の内訳まで書いてあり、内訳の合計は
`305+25926+17263+80+574+9+0+117=44274` ときっちり一致します。書き漏らしの
心配が不要な作り(第26章26.5)が、この足し算です。

## 4. 検査に、壊れを見つけさせる

ここがこのLabの見せ場です。YAMLに1か所、意図的な壊れを入れます。SD-001のカテゴリを、
`drinking`(飲酒)から `meal`(食事)へ書き換えます。

**実行する前に予測を書いてください**。§2の検査5項目の中で、どの項目がこの書き換えを
見つけるでしょう?また、見つけ方は「EX-001の判定が変わった」でしょうか、「別の何か」
でしょうか?

次の `sed` コマンドは「SD-001の行からSD-002の手前までを範囲にして、`primary: drinking`
を `primary: meal` に書き換える」の意味です( `-i` はファイルを直接書き換える指定)。

```bash
sed -i '/^- id: SD-001$/,/^- id: SD-002$/s/primary: drinking/primary: meal/' docs/testassets/m4-seed.yaml
git diff docs/testassets/m4-seed.yaml | head -12
```

期待される出力(差分の見出し部分。書き換えは1か所だけです):

```diff
-      primary: drinking
+      primary: meal
```

では、検査にかけます。今回は最後に「makeがエラーを報告する」ので、出力の末尾まで
見てください。

```bash
make seed-verify
```

期待される出力(先頭と末尾。検出は8件です):

```
FAIL: 8件の違反
  - カテゴリ分布: drinking 実測149 vs 目標150
  - カテゴリ分布: meal 実測121 vs 目標120
  - aggregate.layer1_pass不一致: 記載576 vs 再計算564
  - aggregate.layer1_fail不一致: 記載44274 vs 再計算44286
  - fail_breakdown.category_mismatch不一致: 記載25926 vs 再計算25954
  - fail_breakdown.time0不一致: 記載17263 vs 再計算17241
  - fail_breakdown.people不一致: 記載574 vs 再計算582
  - fail_breakdown.age_alcohol不一致: 記載117 vs 再計算115
make: *** [Makefile:111: seed-verify] エラー 1
```

予測は当たりましたか?検出のしかたには2つの見どころがあります。

1つ目は、**EX-001の判定は変わっていない**ことです。EX-001は「SD-001とSD-002のペアは、
自分同士なのでfail(理由self_pair)」という期待です。Layer 1の判定では理由に優先順位が
あり、self_pairが最優先(第26章26.5・`fail_breakdown` の並び順)。ユーザーが別のままで
ある限り、カテゴリを書き換えても自己ペア判定は変わりません。検査は「EX-001が違う」とは
言わない。**黙って正しい**のです。

2つ目は、それでも**8件で見つかった**ことです。カテゴリの分布(飲酒150・食事120)は
1件ぶんずれ、44,850ペアの再計算では理由ごとの内訳が全部ずれます。EXの期待値だけを
検査していたら、この書き換えは見逃せていました。**個別期待と集計の2層があるから、
「個別には何も破れない壊れ」も分布と集計で捕まる**——第26章で読んだ2層の意味が、
検出の現場で確かめられました。

壊れはそのままにしないで、元に戻して検査を通しておきます。

```bash
git checkout docs/testassets/m4-seed.yaml
make seed-verify
```

期待される出力(§2と同じもの):

```
OK: ../docs/testassets/m4-seed.yaml の機械検査に合格(a構造/b分布/c意図的配置/d aggregate照合/e地名)
```

## 5. 基準日を変えて生み直す

最後に、生成の引数を変えたときの「何が変わるか」を分類します。基準日を1日後ろへずらして
生み直します。

```bash
make seed-generate ARGS="--base-datetime 2026-10-05T11:00:00+09:00"
git diff --stat docs/testassets/m4-seed.yaml
```

期待される出力(生成側の数値は§1と同じ。変えた行数だけが表示されます):

```
generated ../docs/testassets/m4-seed.yaml: intents=300 users=100 pair_total=44850 layer1_pass=576
 docs/testassets/m4-seed.yaml | 1346 +++++++++++++++++++++---------------------
 1 file changed, 673 insertions(+), 673 deletions(-)
```

生成時の数値(`intents=300`・`layer1_pass=576`)は§1と**まったく同じ**です。差分は
673行。中身を見てみます。

```bash
git diff docs/testassets/m4-seed.yaml | head -30
```

期待される出力(抜粋。時刻まわりの行だけが並び替わります):

```diff
-  base_datetime: '2026-10-04T11:00:00+09:00'
+  base_datetime: '2026-10-05T11:00:00+09:00'
   random_seed: 42
   user_count: 100
   intent_count: 300
@@ -22,502 +22,502 @@ meta:
 users:
 - id: SU-001
   display_name: 合成はると001
-  birth_date: '2008-04-04'
+  birth_date: '2008-04-05'
```

変わったのは、基準日そのもの(`meta.base_datetime`)・合成ユーザーの誕生日
(基準日から7日後でも18〜19歳を保つため、誕生日が基準日に連動します)・各Intentの
時刻——**日付まわりの行**です。件数・分布・期待値の集計(`aggregate` の数字)は、
§1と同じまま。実施日が延びた試験でも「再生成して日付だけずらす」運用ができるのは
この構造のおかげです(第26章26.3)。

細かい予測試験をもう1つ。差分673行の中に `aggregate:` の節の数字は含まれるでしょうか。
答えが予測できたら、次のコマンドで確かめてください(何も出なければ「含まれない」です)。

```bash
git diff docs/testassets/m4-seed.yaml | rg "layer1_pass"
```

期待される出力:

```
(何も表示されない)
```

終わったら、資産を元の状態へ戻して、検査を通しておきます。

```bash
git checkout docs/testassets/m4-seed.yaml
make seed-verify
```

期待される出力:

```
OK: ../docs/testassets/m4-seed.yaml の機械検査に合格(a構造/b分布/c意図的配置/d aggregate照合/e地名)
```

## 6. 振り返りとノート

このLabで、あなたは「生む・確かめる・壊す・見つける・戻す」を一巡しました。my-notes.mdに、
次の3つを書き残すのをおすすめします。

1. **§1の静けさ**。`git status` が何も出なかったことの意味を、「バイト同一」と
   「テスト資産は証拠」の言葉で説明する
2. **§4の2つの見どころ**。EX-001の判定が変わらなかった理由(fail_reasonの優先順位)と、
   それでも8件で見つかった理由(分布と集計の2層)を、自分の言葉で書く
3. **§5の分類表**。基準日を変えたとき「変わるもの」(基準日・誕生日・Intentの時刻)と
   「変わらないもの」(件数・分布・aggregate)の対比表を、差分の中身で確認しながら
   自分のノートに書く

疑問が残ったら、それもノートに書いてください。このシードは、stagingでの機能試験の
材料として後の単位で消費されます。「自分が生んだ300件が試験の中心役になる」——
そのとき、このLabで確認した再現性が「試験の証拠」の下地になります。
