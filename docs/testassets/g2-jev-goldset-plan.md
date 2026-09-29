# LATCH G2ゲート Jev日本語評価 ゴールドセット整備計画書

- ステータス: draft — スーパーバイザーレビュー後に確定(確定時に本書とg2-jev-goldset.yamlのmeta.statusをconfirmedへ)
- 作成日: 2026-09-29(a5・並行トラック)
- 出典: 09 v0.5 §4〜§4.2(G2必須・評価方法・ゴールドセット方針)/ 07 v0.5 §4(7軸の質問写像・正規化テキスト)/ 06 §2〜§5(Layer 1〜4の合格条件)/ 10 §2(共有資産)/ T1 v0.2(単価)/ 12 v0.2 G2完了条件
- 運用方針(2026-09-29ユーザー確定): ①評価実施は実課金(実行前に金額試算を報告してから動かす) ②正解ラベルは原則スーパーバイザー/エージェント側で確定(各ケースにrationaleを1行残す。ユーザー確認は可能な限りゼロへ寄せる) ③合格基準はスーパーバイザー草案→オーナー決定

## 1. 目的と位置づけ

TypeSafe Jev(jev-1.13.0)の日本語評価(09 v0.5 §4のG2必須条件)に使う評価ペアを整備する。TypeSafe Jevには「英語が主訓練言語・CJKは同等ではない」の公式留保があり、日本語stateでの実測が唯一の採用判断材料である。本書はペアの設計・正解ラベルの導出規則・ラベル形式・コスト試算を固定し、レビュー・確定・G2実施の一式を同じ場所に置く。

09 §4.1が定めるペア正解ラベルの作成手順「2名が独立して判定し、不一致は3人目の裁定」は、2026-09-29のユーザー確定により「エージェント/スーパーバイザー側がrationale付きで確定し、仕様の空白・価値観が絡む境界だけflagを付けてまとめて1回確認する」運用へ置き換える。本セットのラベルはこの運用で付けている。

評価ペアの本体は docs/testassets/g2-jev-goldset.yaml(520件・status: draft)である。本書はその設計根拠と、G2実施手順の受け渡し書である。

## 2. 成果物の規模(実測)

| 項目 | 値 | 出典・目標 |
|---|---|---|
| 評価ペア | 520件 | 09 §4.1「500件以上」 |
| 成立し得る / し得ない(gold true/false) | 208 / 312(2:3) | 09 §4.1「概ね2:3」 |
| intentプール | 304件 | 09 §4.1「200〜300件」+4(§3.5で説明) |
| 出所 machine / drafted | 299 / 221(57.5 / 42.5%) | §3.4 |
| Layer 1通過 / 非通過 | 490 / 30 | 非通過はF5検証用のみ(§4.1) |
| 1対1 / グループ志向 | 480 / 40 | グループ構成ペアもJev対象(06 §5) |
| 語彙一致 / 意味近接(lexical/semantic) | 184 / 336(35.4%) | 09 §2.3のセグメント分類に対応 |
| flag(確認対象の境界) | 6件 | §9 |

## 3. ペアの設計

### 3.1 規模とバランス

520件(真208:偽312=2:3)。真偽比は09 §4.1の指定どおり。520とし「500件以上」に余裕を持たせた。

### 3.2 直交条件

ペアは次の8軸の組み合わせで構成した。水準はいずれも運用でLayer 3〜4に到達し得る分布(06 §2〜§5)から選んでいる。

| 軸 | 水準 |
|---|---|
| カテゴリ(primary) | drinking / meal / activity(ペアは同カテゴリ原則。F5検証用のみ不一致) |
| 時間帯 | 夜(20–23時台)/ 夕(18–19時台)/ 昼(12–13時台)/ その他(朝・午後・深夜)。交差率(交差幅÷短い方の所要)を0・<20%・20–50%・50–85%・≥85%の5段階で散らす |
| 予算 | 800〜10000円・NULL(制約なし)。ペア予算比<2倍(釣り合い)/ ≥3倍(乖離)/ min<500円(Layer 1非通過) |
| 語彙重なり | lexical(secondary完全一致またはsoft文言の一致)/ semantic(意味近接・語彙不一致) |
| 飲酒 | alcohol_involved true/false。飲酒ペアは作成者双方20歳以上(Layer 1年齢条件を常に満たす) |
| 年齢 | 18〜58歳。18〜19歳は非飲酒intentのみ(27件・intent単位で飲酒×未成年ゼロ) |
| 人数 | 2–2 / 2–4 / 3–4(補完後)。1対1は「2∈双方」、グループ志向(双方min≥3)は人数範囲の共通包含 |
| 境界値 | 時間交差の20%線ぎりぎり・予算3倍線ぎりぎり(→flag)。Layer 1の境界(交差0分・ペア予算500円・人数非包含)はF5検証用30件 |

intentの時間帯・場所・人数・予算・softの組み合わせテンプレートと典型表現のバリエーションという09 §4.1の作成方針に沿い、テンプレート直交生成(machine)+自然文草案(drafted)の2本立てで作った。

### 3.3 導出規則(T/Fの9規則)

ペアは9つの導出規則で構成する。T=成立し得る(gold true)、F=成立し得ない(gold false)。

| 規則 | 件数 | 内容 | 期待バンド |
|---|---|---|---|
| T1-親和 | 96 | hard条件が一致し(同日・時間重なり大・近接地域・予算釣り合い)softも親和 | high×high |
| T2-語彙一致 | 56 | secondaryが完全一致(lexicalセグメントの成立例) | high×high |
| T3-広条件 | 34 | 一方が「系ならどこでも」等の広いsoftで、他方の具体条件を包む | high×high・latent high |
| T4-弱信号 | 22 | 条件は矛盾しないが「まずは様子見」等の慎重soft | mid×mid(gold true) |
| F1-時間不足 | 58 | 同日だが交差率<20%(Layer 1は通過・実質成立できない) | low×low(片側quickならmid) |
| F2-予算乖離 | 50 | ペア予算比≥3倍で食事内容が釣り合わない | low×low |
| F3-soft矛盾 | 118 | 雰囲気(静か×わいわい等)・食(肉×野菜中心)・店(個室×立ち飲み)の矛盾 | low×low |
| F4-NG抵触 | 56 | 判定不能NG(会社関係回避等)×相手の仕事文脈soft | low×mid(非対称) |
| F5-hard検証 | 30 | Layer 1非通過(state上可視のhard矛盾)。Jevもyesから遠ざけるべき | low×low |

F4・F1の一部・F2の一部は非対称(片方向だけreject)で、全体で68件。min方向のMutualScoreが正しく低くなることを検証する材料になる(09 §4.2 Mutual Acceptance Precision)。T4のmid×mid(22件)は「成立し得るがJevが0.8を付けなければ提案されない」Recall測定の難例である。

### 3.4 machine / drafted の比率と内訳

- machine 299件(57.5%): 直交テンプレートからの決定的生成(乱数seed固定。soft文言はタグ付き語彙プールから選択)
- drafted 221件(42.5%): 自然文草案のintent(手書き80件:「仕事終わりに一杯だけ」「月曜なので控えめに」等の自然なsoft文言)を少なくとも1つ含むペア

drafted intent 80件のうち72件がペアに使用された(8件は条件の組合せで未使用となり、YAMLには出力していない)。Jevへの入力は正規化テキストであり、soft_constraintsの文言の自然さ・揺らぎが判定に効くため、drafted比率を4割強とした。

### 3.5 分布の実測と目標との対応

10 §2の分布目標は本来フルシード(M4・機能負荷検証用)に適用するもの。G2部分セットはg1と同じく「精神を参考にした近似」にとどめる。

intent実測(304件): カテゴリ drinking 40% / meal 39% / activity 21%(目標50/40/10)。時間帯 夜30% / 夕29% / 昼19% / その他22%(目標40/30/20/10)。18〜19歳27件(8.9%)。飲酒true 135件(44%)。予算NULL 62件(20%)。

- ペア側のカテゴリ分布は drinking 260(50%) / meal 208(40%) / activity 52(10%)で目標どおり
- intent側のactivity比率(21%)が目標(10%)より高いのは、activity対の条件成立(T1の時間重なり等)に必要な材料が初期プールで揃いにくく、補充生成が効いたため。ペア520件中52件(10%)という評価対象の分布は目標を満たしており、intent数は構成の副産物と位置づける
- intentプール304件は09 §4.1のシード規模(200〜300件)を4件超える。M4フルシード整備時に本intentsを母集合として200〜300件へ再編する(§7)

## 4. 正解ラベルの導出規則

### 4.1 仕様から一意に決まるもの

| 規則 | 仕様根拠 |
|---|---|
| F5のlayer1非通過判定 | 06 §2のHard Filter条件(時間交差・ST_DWithin(r_a+r_b)・ペア予算min≥500・2∈人数・category完全一致・飲酒は双方20歳以上・自己ペア除外)。ペアのlayer1_passは生成時にこの条件で判定し、検証スクリプト(§10)が独立に再計算して一致を確認する |
| F5のgold=false | 07 §4のwould_*instructions「相手が[hard]条件と意味的に矛盾する場合はyesから遠ざける」。F5ペアはstate上で可視のhard矛盾(カテゴリ不一致・時間交差なし・人数非包含・ペア予算<500)のみで構成する。年齢(飲酒)は正規化テキストに現れないためJevには可視でなく、F5の対象外とする(年齢条件はLayer 1の責務) |
| T1/T2のgold=true | hard条件一致+soft親和。親和の判定はmood_fit表(§4.2)で機械的に出す |
| T2のlexical | secondary完全一致(09 §2.3の語彙一致セグメントの定義に対応) |
| 飲酒×未成年を Layer 1通過ペアに混入しない | 06 §2の年齢条件。生成側・検証側の双方で検査 |

### 4.2 エージェント判断に任せる境界の判断基準

仕様に明示の数値がない線引きは、次の根拠で本書が固定し、全ケースに機械適用した。線引き自体の妥当性を確認したい境界だけflagを付けている(§9)。

| 境界 | 本書の規則 | 根拠 |
|---|---|---|
| F1の「実質交差不足」 | 交差率(交差幅÷短い方の所要)20%未満をfalse | 07 §4 timing_fit criteria「開始は合うが所要が合わない=1」を量化。3時間同士で30分前後しか重ならない場合は食事・飲みとして成立しない |
| F2の「予算の著しい乖離」 | ペア予算比3倍以上をfalse | 07 §4 instructions「予算感の著しい乖離が食事内容を成立させない」を量化。2倍未満は釣り合い(T1)、2〜3倍は個別に作らない(中間帯の恣意を避ける) |
| F3の雰囲気矛盾対 | mood_fit表(静か×わいわい=0・軽く×がっつり=1・ゆっくり×早く=1・しっぽり×わいわい=1)。同一または親和の組はtrue側 | 01 §13のmood_fit軸の定義(「軽く」×「がっつり」等)から導く。親和(値3)・中立(値2=どちらでも成立する)はtrue側 |
| F3の食の矛盾 | 肉×野菜中心をpurpose_fit=1でfalse | 同上purpose_fit軸 |
| F3の店・重さの矛盾 | 個室×立ち飲みをfalse | mood_fit軸の「重さが大きく異なる」 |
| F4の抵触 | ng_unverifiableは「会社関係の人は避けたい」「元同僚とは会いたくない」のwork文脈2種のみ。相手softがwork文脈(仕事の話・転職の話等)なら抵触 | 07 §4 instructions「aの[soft]条件と判定不能NG条件に触れないことを含めて判定する」。ng側はreject・相手側はmid(非対称)の基本形とし、相手にwork文脈がなければT1として扱う |
| T3の広条件 | 「系ならどこでも」「時間が合えばどこでも」等のscope:broad文言を持つ側 | 07 §4 latent_yesの例(「焼肉に行きたい」×「今日は肉系ならどこでもいい」) |
| T4の弱信号 | 「まずは1杯だけ様子を見たい」等の慎重soft | mid帯の存在根拠(07 §4「根拠がなければ0.5付近」の中間帯を評価で分解するため) |

### 4.3 rationaleの書式

全ペアに1行のrationaleを付ける。書式は「[規則ID-名称] 条件A×条件B→判定」。条件の抜粋はintentのsoft文言・時刻・予算そのものを使い、ラベルの根拠がレビューで追えるようにする。例:

- `[T1-親和] hard条件一致(10/1 20:00〜23:00×20:00〜23:00・天文館×天文館・予算3500×3000円)+soft親和(「仕事終わりに一杯だけ」×「静かにやりたい」)→双方YESし得る`
- `[F3-雰囲気矛盾] 「わいわい騒ぎたい」×「静かにやりたい」→雰囲気・重さが相容れない`
- `[F5-hard] category不一致(drinking×meal)→Layer 1非通過。Jevも[hard]矛盾からyesを遠ざけるべき`

## 5. ラベル形式(07 §4出力への対応)

評価対象(07 §4)は would_a_accept_b / would_b_accept_a のnoul確率(0〜1)、purpose_fit / mood_fit / timing_fit / social_fit のscore(0〜4)、latent_yes のnoul確率である。ラベルは次の形で対応する。

| ラベル | 形式 | 離散か確率帯か・理由 |
|---|---|---|
| gold_mutual | 2値(true=双方YESし得る) | 離散。09 §4.2の全指標(Precision・Recall・ECE・Brier・Mutual Acceptance Precision)が「実YES=2値」を供給源として定義されているため。離散が主ラベルである |
| would_a_accept_b / would_b_accept_a | label(accept/reject)+band(low/mid/high) | 離散(label)がgold_mutualと連動する主ラベル。bandは診断用の確率帯で、離散と併記する。Jevは較正済み確率を返すため、単発の離散だけでは較正評価の診断ができない。bandはECEの10分割ビンより粗い3帯(low<0.35 / 0.35≤mid<0.65 / high≥0.65)とし、ケース単位の帯ズレ分析に使う。境界0.35/0.65は07 §4「根拠がなければ0.5付近の値として自然に返る」のmid帯を挟む対称な幅 |
| purpose_fit / mood_fit / timing_fit / social_fit | 0〜4の整数 | 07 §4のscore値域そのまま。期待値との一致は「±1以内」を参考基準とする(5段階順序尺度で±1は隣接段階の揺らぎ、±2以上を軸の読み違いとみなす運用。G2実施時に正式の集計方法を確定する) |
| latent_yes | band(low/mid/high) | 3帯。noul確率だがMutualScoreに直接寄与しない分析軸(07 §4)のため、帯で十分 |

labelとbandの組合せは accept×low・reject×high を禁止し(検証項目)、gold_mutual=true ⇔ 双方向acceptの一貫性を機械検査で保証する。

## 6. Layer 1メタとグループ志向ペア

各ペアはlayer1_pass(true/false)と、false時のfail_reason(category_mismatch / time0 / people / budgetmin)を持つ。非通過30件はF5検証用で、運用ではLayer 1が先に落とすためJevに入らないが、07 §4のinstructions(hard矛盾でyesから遠ざける)の単体検証として含める。残る490件はLayer 1通過条件を満たし、Jevが呼ばれる運用分布に対応する。

グループ志向ペア40件(双方min≥3)は、06 §5のグループ構成ペア(集合内の全ペアをJevで判定する)に対応する。kind: groupと区別し、Layer 1人数判定は「人数範囲の共通包含」で検証する。

## 7. M4シード資産との形式共用

- intentsのstructured形式はアプリ層補完後(03 D-19)の値(participants NULL→2/2・time.end NULL→start+3h・radius NULL→1000)。Parser出力段階ではない点がg1系(g1はParser出力段階の期待値)と異なる。Jevへの入力は補完後の正規化テキスト(07 §4)であるため、評価資産として補完後で持つ
- id体系は intent=SI-xxx・pair=GP-xxx。g1のP-/A-/E-と衝突しない
- M4フルシード(200〜300件・09 §4.1)整備時に、本intents(304件)を母集合に再編する。activityの再利用率が低い分(§3.5)を間引き、カテゴリ・時間帯分布を10 §2の目標へ寄せる。評価ペア520件の構成は変えない
- 各intentはuser(U-xxx)とauthor_ageを持つ。同一userのintent同士をペアにしない(自己ペア除外・06 §2)。userと年齢はM4フルセットでユーザーとIntentを紐付ける土台であり、08 D-10の動作試験(18〜19歳の飲酒Intent作成拒否)への流用も想定する
- 09 §4.1の運用開始後の方針(同意を得た匿名化実データの追加)は、本セットとは別の追加ファイルで行う(本セットのid・期待値を書き換えない)

## 8. コスト試算(実行前報告用)

前提: 1ペア=1リクエスト。入力トークンはT1 v0.2 §4と同一の概算(質問群instructions・criteria約2k+正規化テキスト2件約1k=約3k/リクエスト)。日本語トークン換算に±50%の幅をみる(T1と同一前提)。

| 経路 | 単価 | 計算 | 金額 |
|---|---|---|---|
| 第一候補 TypeSafe Jev(jev-1.13.0) | 入力$0.042/MTok・出力無料 | 520リクエスト×3k tok=$1.56M tok | **$0.07** |
| フォールバック Anthropic Sonnet 5 | $2/MTok入力・$10/MTok出力(出力約0.3k) | 520×(3,000×$2+300×$10)/1M | **$4.68** |
| G2実施(両経路を評価・09 §4) | — | 上記の合計 | **約$4.75** |
| 再実行2回(質問文言調整後等)を見込む | — | フォールバック側が支配的(+$9.36) | 上限見込み 約$14.3 |

- G2実行前にスーパーバイザーが報告する実行予算の目安: **$15**(再実行2回込みの保守値)
- レート制限(TypeSafe: 250,000 tok/s+1,200 req/min・2026-09-28時点の値で動的調整あり)に対し、520×2経路=1,040リクエストは制約にならない(数分で完了する規模)
- 実測時はusage(input_tokens)を記録し、3k/リクエスト前提の誤差を確認する

## 9. flag対象の見積り(6件)

ラベルは§4の規則から機械的に出しており、個別の価値判断は残っていない。flagを付けたのは規則の線引きそのものの妥当性確認である(まとめて1回の確認に回す)。

| id | 規則 | 内容 | 確認事項 |
|---|---|---|---|
| GP-132 / GP-359 / GP-491 | F1 | 交差35分(交差率約19.4%)をfalse | 交差率20%未満を「成立し得ない」とする線 |
| GP-158 / GP-382 / GP-498 | F2 | 予算9600円×3000円(3.2倍)をfalse | ペア予算比3倍を「著しい乖離」とする線 |

いずれも線のすぐ内側の値で作っており、線の位置を動かす場合は該当ケースのgold・bandの更新が必要になる。

## 10. 機械検査

生成物は全件機械検査済みである(検証スクリプト全文は付録A)。

| 検査項目 | 結果 |
|---|---|
| YAMLとしてvalid・意図した構造 | OK |
| ペア数≥500・id一意(intent・pair全体)・ペア重複なし・intent参照整合 | OK(520件) |
| metaの件数宣言と実測の一致(全項目) | OK |
| gold真偽比が2:3(0.38〜0.42) | OK(0.40) |
| 自己ペアなし(同一userの対なし) | OK |
| label/band整合(accept×low・reject×highの禁止) | OK |
| gold_mutual ⇔ 双方向accept の一致 | OK |
| 5軸の値域(0〜4)・latent band値域 | OK |
| gold=trueなのにtiming/purpose/mood_fit=0の軸がある矛盾 | なし |
| layer1_passの独立再計算(時間交差・距離haversine・ペア予算・人数・カテゴリ・飲酒年齢)との一致 | OK(490/30) |
| kind(1to1/group)整合 | OK |
| layer1 failペアのgold=trueなし | OK |
| 日付が2026-10-01〜10-07(基準日から作成上限7日)の範囲内 | OK |
| intent単位: drinking×alcohol falseなし・飲酒×18〜19歳なし・participants 2〜4・半径≤3000 | OK |

## 11. G2実施手順(評価実行側への受け渡し)

1. スーパーバイザーが本書とYAMLをレビューし、flag 6件(§9)を含めて確定する(status: confirmed)
2. 合格基準の草案をスーパーバイザーがまとめ、オーナーがG2実施前に確定する(事前固定の原則・09 §4)。既存の事前固定値である「閾値0.80でPrecision 0.60以上」(09 §2.3仮説3)がPrecisionの下限としてそのまま使える。ECE・Brierの水準はオーナー決定に委ね、本書は参考として、ECEは10分割ビン・Brierはgold 2値(208/312の構成比でベースライン約0.24=全件p=0.4のBrier)を計算根拠として残す
3. ハーネス(ws-8)がintentペアを正規化テキスト(07 §4の形式。[hard]/[soft]行・visibility/notification_levelを含めない)へ組み立て、System One APIへstate+7質問を送る(1ペア1リクエスト)
4. answersのwould_*からMutualScore=min(would_a, would_b)・L=H×MutualScore×C(C=1)を計算する
5. 09 §4.2の指標を算出する: Precision/Recallは閾値0.70/0.80/0.90の3点(D-01)、ECE・Brier・Mutual Acceptance Precision(実際に双方YESとなるペア〔gold true〕とそうでない分布の分離度)
6. 同一セットをフォールバックLLM(Sonnet 5・System One互換answers形式)でも実行し、jev_resultのproviderキーで分離して記録・比較する(09 §4)
7. 第一候補が不合格の場合、フォールバックLLMの第一候補繰上げ判断をオーナーへ持ち帰る(09 §4・07 §4)

## 12. ステータスと確定フロー

- 現在: draft(g2-jev-goldset.yamlのmeta.statusもdraft)
- 確定: スーパーバイザーレビュー(§9のflag 6件の裁定を含む)後、meta.statusをconfirmedへ。裁定記録はYAML末尾へコメントで残す(g1系と同一の流儀)
- 変更時: 正規化テキスト形式・7質問の文言・閾値0.80等の前提が変わった場合は、本セットの期待値(特にF5・T系)を再検討してから再実行する。Parserプロンプト変更の再実行規定(09 §4.3)と同じ運用
- docs/testassets/README.mdのファイル一覧表へ本書とYAMLを追記する作業は、確定時に行う(本書作成時点では2ファイルの草案のため未追記)

## 付録A: 検証スクリプト(全文)

```python
#!/usr/bin/env python3
"""g2-jev-goldset.yaml の機械検証(§10の項目を実装)。
使い方: python3 verify.py [path/to/g2-jev-goldset.yaml]
"""
import math
import sys
import yaml
from collections import Counter
from datetime import datetime

PATH = sys.argv[1] if len(sys.argv) > 1 else "g2-jev-goldset.yaml"
AREAS = {
    "天文館": (31.5960, 130.5565), "天文館通り": (31.5945, 130.5560),
    "呉服町": (31.5985, 130.5535), "高見馬場": (31.5965, 130.5510),
    "加治屋町": (31.5970, 130.5490), "鹿児島中央駅前": (31.5910, 130.5390),
    "鹿児島中央駅周辺": (31.5915, 130.5405), "城山": (31.6035, 130.5580),
    "浜町": (31.6025, 130.5650),
}

def hav(a, b):
    la1, lo1 = a; la2, lo2 = b
    p1, p2 = math.radians(la1), math.radians(la2)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(x))

def _p(iso):
    return datetime.fromisoformat(iso)

d = yaml.safe_load(open(PATH, encoding="utf-8"))
ins, pairs, meta = d["intents"], d["pairs"], d["meta"]
byid = {i["id"]: i for i in ins}
errs = []

def err(m):
    errs.append(m)

# 1) 構造
if meta["pair_count"] < 500:
    err(f"ペア数不足: {meta['pair_count']}")
if not (200 <= meta["intent_count"] <= 310):
    err(f"intent数が範囲外: {meta['intent_count']}")
ids = [i["id"] for i in ins] + [p["id"] for p in pairs]
if len(ids) != len(set(ids)):
    err("id重複")
for p in pairs:
    if p["intent_a"] not in byid or p["intent_b"] not in byid:
        err(f"{p['id']}: 参照不通")
if len(set(tuple(sorted((p["intent_a"], p["intent_b"]))) for p in pairs)) != len(pairs):
    err("ペア重複")

# 2) meta数値と実測の一致
n_true = sum(1 for p in pairs if p["expected"]["gold_mutual"])
n_m = sum(1 for p in pairs if p["source"] == "machine")
n_pass = sum(1 for p in pairs if p["layer1_pass"])
n_g = sum(1 for p in pairs if p["kind"] == "group")
n_lex = sum(1 for p in pairs if p["segment"] == "lexical")
n_flag = sum(1 for p in pairs if p["flag"])
for name, actual, declared in [
    ("pair_count", len(pairs), meta["pair_count"]),
    ("gold_true", n_true, meta["gold_true"]),
    ("source_machine", n_m, meta["source_machine"]),
    ("layer1_pass", n_pass, meta["layer1_pass"]),
    ("group_pairs", n_g, meta["group_pairs"]),
    ("segment_lexical", n_lex, meta["segment_lexical"]),
    ("flag_count", n_flag, meta["flag_count"]),
    ("intent_count", len(ins), meta["intent_count"]),
]:
    if actual != declared:
        err(f"meta.{name} 不一致: 実測{actual} vs 記載{declared}")
if not (0.38 <= n_true / len(pairs) <= 0.42):
    err(f"true比率が2:3から乖離: {n_true}/{len(pairs)}")

# 3) 各ペアの整合
for p in pairs:
    a, b = byid[p["intent_a"]], byid[p["intent_b"]]
    e = p["expected"]
    if a["user"] == b["user"]:
        err(f"{p['id']}: 自己ペア")
    for w in ("would_a_accept_b", "would_b_accept_a"):
        lbl, bd = e[w]["label"], e[w]["band"]
        if bd not in ("low", "mid", "high") or lbl not in ("accept", "reject"):
            err(f"{p['id']}: 値域外 {w}")
        if lbl == "accept" and bd == "low":
            err(f"{p['id']}: label/band不整合 {w}")
        if lbl == "reject" and bd == "high":
            err(f"{p['id']}: label/band不整合 {w}")
    both = e["would_a_accept_b"]["label"] == "accept" and e["would_b_accept_a"]["label"] == "accept"
    if both != e["gold_mutual"]:
        err(f"{p['id']}: goldとラベル不整合")
    for ax in ("purpose_fit", "mood_fit", "timing_fit", "social_fit"):
        if not (isinstance(e[ax], int) and 0 <= e[ax] <= 4):
            err(f"{p['id']}: {ax}値域外")
    if e["latent_yes"]["band"] not in ("low", "mid", "high"):
        err(f"{p['id']}: latent band値域外")
    if e["gold_mutual"] and (e["timing_fit"] == 0 or e["purpose_fit"] == 0 or e["mood_fit"] == 0):
        err(f"{p['id']}: gold=trueなのにfit=0の軸あり")
    # Layer 1判定の独立再計算
    sa, sb = a["structured"], b["structured"]
    reasons = []
    if sa["category"]["primary"] != sb["category"]["primary"]:
        reasons.append("category_mismatch")
    da, db = sa["time"]["start"], sb["time"]["start"]
    if da[:10] != db[:10]:
        reasons.append("no_time_overlap")
    else:
        ov = (min(_p(sa["time"]["end"]), _p(sb["time"]["end"]))
              - max(_p(da), _p(db))).total_seconds() / 60
        if ov <= 0:
            reasons.append("no_time_overlap")
    dist = hav(AREAS[sa["location"]["name"]], AREAS[sb["location"]["name"]]) * 1000
    if dist > sa["location"]["radius_m"] + sb["location"]["radius_m"]:
        reasons.append("distance")
    vals = [x["budget"]["max"] for x in (sa, sb) if x["budget"]["max"] is not None]
    if vals and min(vals) < 500:
        reasons.append("pair_budget_lt500")
    pa_, pb_ = sa["participants"], sb["participants"]
    if pa_["min"] >= 3 and pb_["min"] >= 3:
        if min(pa_["max"], pb_["max"]) < max(pa_["min"], pb_["min"]):
            reasons.append("participants")
    elif not (pa_["min"] <= 2 <= pa_["max"] and pb_["min"] <= 2 <= pb_["max"]):
        reasons.append("participants")
    if (sa["alcohol_involved"] or sb["alcohol_involved"]) and (a["author_age"] < 20 or b["author_age"] < 20):
        reasons.append("age_alcohol")
    if (not reasons) != p["layer1_pass"]:
        err(f"{p['id']}: layer1_pass不整合(recalc={not reasons}, reasons={reasons})")
    if p["kind"] != ("group" if (pa_["min"] >= 3 and pb_["min"] >= 3) else "1to1"):
        err(f"{p['id']}: kind不整合")
    if not p["layer1_pass"] and e["gold_mutual"]:
        err(f"{p['id']}: layer1 failなのにgold=true")
    for x in (da, db):
        if not ("2026-10-01" <= x[:10] <= "2026-10-07"):
            err(f"{p['id']}: 日付範囲外 {x}")

# 4) intent妥当性
for i in ins:
    s = i["structured"]
    if s["category"]["primary"] not in ("meal", "drinking", "activity"):
        err(f"{i['id']}: category値域外")
    if s["alcohol_involved"] and i["author_age"] < 20:
        err(f"{i['id']}: 飲酒intent×未成年")
    if s["category"]["primary"] == "drinking" and not s["alcohol_involved"]:
        err(f"{i['id']}: drinkingなのにalcohol false")
    if s["participants"]["max"] > 4 or s["participants"]["min"] < 2:
        err(f"{i['id']}: participants値域(2〜4)外")
    if s["location"]["radius_m"] > 3000:
        err(f"{i['id']}: 半径3000超")
    if i["source"] not in ("machine", "drafted"):
        err(f"{i['id']}: source値域外")

print(f"検査対象: {len(ins)} intents / {len(pairs)} pairs")
print(f"gold true/false: {n_true}/{len(pairs) - n_true}  machine/drafted: {n_m}/{len(pairs) - n_m}")
print(f"layer1 pass/fail: {n_pass}/{len(pairs) - n_pass}  group: {n_g}  lexical: {n_lex}  flag: {n_flag}")
if errs:
    print(f"\n** 検証エラー {len(errs)} 件 **")
    for m in errs[:30]:
        print(" -", m)
    sys.exit(1)
print("\nALL CHECKS PASSED")
```

## 付録B: 生成の再現性について

評価ペアは決定的生成(乱数seed=13固定のスクリプト)で作った。生成ロジックは§3〜§4の規則の実装であり、語彙プール(machine用のタグ付きsoft文言・drafted 80件の自然文)がコード内のデータとして存在する。生成スクリプトは一時作業ディレクトリ(/tmp/g2gen/generate.py)にあるため、確定時に恒久保管するかはスーパーバイザー判断とする(レビュー・G2実施にはYAML本体と付録Aの検証スクリプトで足りる。YAMLを書き換えた場合は本書の分布実測(§2・§3.5)との一致を付録Aで再確認すること)。
