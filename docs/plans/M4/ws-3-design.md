# M4 ws-3(機能試験27項目のstagingフル実行)設計メモ

- 作成: 2026-10-03(agent1)
- 前提: M4 ws-1(staging環境・マージ165242e)・ws-2(共有テスト資産・マージ398375c)完了。
  マージ後main test-ci 1561 passed・alembic head=0006。実行wave2はws-3∥ws-5(並行・ws-5は同意フロー)
- 参照仕様: 10 §3(機能検証の実行計画・追加試験)・02 §4(27項目マッピング表)・
  12 M4-3・10 §1(staging構成・時刻操作・疑似イベント・認証テスト構成=ws-1で実装済み)・
  10 §2(シード分布=ws-2で実装済み)・10 §5(合格判定と証拠)・01 §23(MLR数え方)・
  09 §3(グループ6検証=G3でci実装済み)・08 §2.6(FR-21)・D-10/D-23(08)・02 D-03/#22/#23
- 本単位は**マイグレーション追加なし**(head=0006不変。運用ルール1の「マイグレーション追加単位との
  test-ci同時実行禁止」については、並行ws-5がマイグレーションを追加する場合に備えて、
  test-ci実行はスーパーバイザー検証時に直列化する前提を守る)
- **確定**: embedding経路=案A(スタブのまま・2026-10-03ユーザーコスト承認)+supervisor承認事項
  6件(§6)。未解決の論点なし

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

02 §4の27項目のうちstaging振り分け項目とG3繰越(02#13〜#23のstaging再実行)を、
ws-1のstaging環境(latch-stagingプロジェクト)+ws-2のシード資産(m4-seed.yaml 300件)上で
**自動化ハーネスとして実行し、10 §5の形式の証拠をdocs/testassets/results/へ残す**。
作るものは3つ:

1. **stagingハーネス**: シード投入→パイプライン実行→項目別の検査と証拠出力を1コマンド
   (make staging-test)で回すpytestスイート
2. **stub Jev判定値の固定経路**: 10 §3が要求する「Jevスタブで双方向判定値を固定」を
   staging環境で実現する設定経路(現状のStubLLMは0.5固定で提案閾値0.60に届かず、
   このままでは提案を要う14項目が実行不能 — §2.2)
3. **証拠**: 02#項目IDつきの結果YAML(試験ID・実施日・環境・入力条件・合否)

実装コード(製品ロジック)への変更はstub応答値の設定経路追加がほぼ全てで、
パイプライン本体(API/Worker/Layer 1〜5)には触らない。

### 1.2 実行対象の確定(10 §3の振り分け+G3繰越の和集合)

| 区分 | 項目 | 出典 |
|---|---|---|
| staging振り分け(10 §3表2行目) | #5(スモーク)・#6〜#13・#15〜#19・#26 | 10 §3 |
| 追加試験(staging・機能検証フェーズ) | D-10(#27動作面)・D-23・FR-21 | 10 §3「追加試験」 |
| 配置ケース | 自己ペア除外・自己重複集合(FR-17)・部分YES・通知順序 | 10 §3 |
| G3繰越 | 02#13〜#23のstaging再実行 | STATUS「G3判定の待ち事項2」→2026-10-02ユーザー承認済み |

和集合=**#5,#6,#7,#8,#9,#10,#11,#12,#13,#14,#15,#16,#17,#18,#19,#20,#21,#22,#23,#26 の20項目**
+追加試験3件+配置ケース。#14・#20・#21は10 §3ではci割り当てだが、G3繰越(02#13〜#23)の
staging再実行に含まれる。ci専任分(#1〜#4,#9単体,#24,#25,#27精度ゲート)はtest-ci常設で
緑維持する(本単位では新規unit追加のみで既存1561を壊さない)。

**#27の読み(解釈の明示)**: 10 §3のci行に#27、staging行に「#27(セット共通)」とある。
#27の合格は「拒否の応答記録+alcohol_involved精度ゲート(09 §4.3)」の2本立てで、
精度ゲートは既存g1-alcohol.yaml+make g1-gate(G1で合格済み・ci資産)が担い、
staging側で新たに走らせるべきは**動作面(D-10: 422 UNDER_AGE+Layer 1年齢条件)**である。
よって本単位はD-10動作面をstagingで実行し、精度ゲートはci資産を参照する(単位表の
staging振り分け項目リストに#27がないこととも整合)。

### 1.3 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | stagingの機能検証は「#5(スモーク)、#6〜#13、#15〜#19、#26、#27(セット共通)」。**「Jevスタブで双方向判定値を固定(応答形式はSystem One互換のanswers)」**が明記され、判定値固定=決定的な提案成立がdocsに規定されている | 10 §3表staging行備考 |
| 2 | 時刻参照はすべてClock経由で、stagingではテスト用Clock制御を注入できる(適用範囲6項目)。期限系はClock操作で再現し実時間待ちを排除 | 10 §1「時刻操作」(ws-1実装済み: ControlledClock・make staging-clock) |
| 3 | 疑似イベント注入: Match Event 5種+embedding_completedを手動発火できる。LLMはテストモード(記録済み応答スタブorレイテンシ注入)で決定的。FCMはドライラン(送信内容を記録のみ) | 10 §1「疑似イベント注入」(ws-1実装済み: make staging-publish) |
| 4 | 証拠: 試験ごとに試験ID・実施日・環境・入力条件・合否とともにdocs/testassets/results/へ。API応答・DB記録・通知ドライラン記録でよい。**CI・stagingの機能試験は自動化し、リリース前ゲートとして全項目グリーンを必須** | 10 §5 |
| 5 | 自己ペアの除外(同一ユーザー2 Intent→match_candidates不生成・提案通知なし)、自己重複集合の除外(同一ユーザー2 Intentを含む3〜4人集合→group_candidates不生成)。テストデータの分布には自己重複を検出する意図的配置を含める | 10 §3「配置ケース」 |
| 6 | 部分YES: 3〜4人セットの提案に一部のみyes→残り未回答のままresponse_deadline後へClock進行(D-06期限時確定)。通知順序: 同一Poolから複数集合→proposedはaggregate_score降順の1つだけ(group_candidates記録で確認) | 10 §3 |
| 7 | #9は二段構成: ciでHard Filter単体試験(G2で実施済み)+**stagingでE2E確認**(予算・時間・距離・人数を意図的に外すペアがいずれの候補にも現れない) | 10 §3表ci行備考 |
| 8 | #13(v0.3改訂): mutedのIntentのケース(通知送信なし・proposed遷移・ホームのLATCH候補から回答可。**通知ドライラン記録で通知0件を確認**)を含める。#22(v0.3改訂): 公開設定2値の分岐確認(hidden_until_matchとsummary_onlyで提案画面の内容比較) | 10 §3表staging行備考・02 §4 #13/#22 |
| 9 | D-10: 18〜19歳ユーザーのalcohol_involved=true Intent作成拒否(422 UNDER_AGE)+飲酒Intentが20歳未満ユーザーとの候補に現れない(Layer 1)。D-23: matched LATCHで一方がブロック→進行中提案cancelled・チャット読み取り専用・「このチャットは利用できません」。FR-21: ドライラン記録のプッシュ本文が汎用文+条件サマリ不含・drinkingも同一様式・summary_onlyはお知らせと提案画面にサマリ表示・hiddenは両方に表示なし | 10 §3「追加試験」・08 D-10/D-23/§2.6 |
| 10 | MLR(#26)の数え方: 分母=当該期間に通知を送信したLATCH候補数(1候補1・グループも1・期限切れ/NO/競合クローズも除かない)、分子=必要人数全員YESした候補数。latch_status_eventsからの集計 | 01 §23・02 §4 #26 |
| 11 | グループ系(#18ほか)は09 §3の6検証と同一手順(G3でci実装済み=ws-9対応表。stagingでは同一検査をstaging観測で再実行) | 10 §3表staging行備考 |
| 12 | シード投入はusers→intents順・structured_intent明示(EX-004/005素材はraw_text経由だとParser規則7でalcohol true化し422のため必須)。投入直前に同一コマンドで再生成(差分は対象日付のみ)。突合はreference_l1で無向化比較 | ws-2報告(STATUS M4 ws-2引継ぎ)・ws-2 design §3.3 |
| 13 | 保存APIはraw_text必須+structured_intent全フィールドOptional。保存経路にParser呼び出しがない(構造化は明示渡し・アプリ層検証のみ)。ジオコーディング正転は保存時に組み込み。レート制限=Active 5件・作成20件/日(ユーザー単位) | 05 §5(実装: intents/service.py create)・08 §5.4 |
| 14 | latch_score = C × min(would_a, would_b)・C=1.0・提案閾値0.60。グループaggregate = C × min(ペアMutualScore)、D-06提示順はaggregate降順→同点サイズ昇順→intent_id辞書順(同点でも決定的) | latch_calc.py/latch_engine.py/group_calc.py・06 §6・G2改版(閾値0.60) |
| 15 | stubの既定Jev応答=would_a/would_b/latent 0.5+score軸2.0 → mutual=0.5<0.60で**既定のままでは提案が1件も成立しない**。StubLLMはコンストラクタで応答差し替え可だが、settings/env経由の差し替え経路は未整備 | llm/stub.py DEFAULT_JEV_RESPONSE・llm/gateway.py build系(stub分岐は引数なし構築) |
| 16 | push_mode=stubはドライランで`latch.push.send`ロガーへJSON 1行記録(実装: notifications/records.py)。push記録の取得経路はコンテナログのみ | 10 §1「FCMはドライラン」・notifications/records.py:17,36 |
| 17 | staging環境: 別プロジェクトlatch-staging・nginx 18000/api 18001-18002/db 15432/redis 16379/pubsub 18085・api2+worker2・.staging/keysはworktree毎に別実体・staging/loadのボリュームはプロジェクト名単位で共用 | ws-1 design §2.1〜§2.3・ws-1 report |
| 18 | worker再評価系のtickは実時間周期(reeval_runner_interval_sec既定60秒・sweeper 60秒周期)でClock仮想時刻を読む。Clockを進めれば次tick(≤60秒実時間)で発火 | ws-1 design「ws-3以降への引継ぎ」・worker/reeval.py・sweeper.py |

## 2. 実装方式の選択肢と推奨

### 2.1 ハーネスの器 — pytestスイート(testpaths外の新ディレクトリ)(推奨)

| 案 | 内容 | 評価 |
|---|---|---|
| **A. pytestスイートを`backend/staging_tests/`へ(推奨)** | tests/配下**外**の新ディレクトリにconftest+シナリオ試験を置き、`uv run pytest staging_tests`で実行。pyprojectのtestpaths=["tests"]により既存のmake test/test-ciからは収集されない | pytestの報告・失敗局所化・-k選択がそのまま使え、agent3が27単位で培ったintegration試験の型と同じ書き方ができる。**既存の試験実行系(make test/test-ci・pyproject)に一切触れずに既定収集から外せる**(testpathsは引数なし時のみ適用されるため)。証拠出力だけconftestに小型の収集器を置く |
| B. ハーネスCLI(g1-gate/g2-gateと同型のsrcモジュール) | `python -m latch.staging_harness`で一連の検査を実行しYAMLを出す | 証拠YAML形式は最も自由。ただし検査~20項目分のassert/報告/失敗表示を自作になり、失敗時の局所化と修正サイクルで不利。pytestで書く内容(条件待ち+DB照合+応答比較)を車輪の再発明する |
| C. 既存ci integration試験をstaging向けにenv切替で再利用 | test_g3_acceptance_e2e等をstaging URLで動かす | 既存試験はin-process app+FakeClock+DB直操作が前提で、常設コンテナ+実時間tick+ControlledClockのstaging観測と合わない。env分岐で1つの試験に2つの実行形態を持たせるのは複雑化の元 |

採用理由: 「証拠YAMLを出す自動ゲート」性はBと同じだが、検査本体の記述・保守・失敗調査は
pytestが明確に有利。g1/g2ゲートがCLIである理由(実API課金の実行・単一レポート)は本単位には
当てはまらず(課金ゼロ・pytest report+YAML両立)、**証拠YAML出力はsession finish hookで
足せる**ためAの欠点は小さい。

### 2.2 stub Jev判定値の固定経路 — settings+gateway構築への渡し(要・製品コード変更)

**論点の所在。** 10 §3 staging行は「Jevスタブで双方向判定値を固定」を規定する(引用確定値#1)。
現状のStubLLMはwould=0.5固定(引用確定値#15)で、score=C×mutual=0.5<0.60となり
**stagingで提案が1件も成立せず**、#13〜#19・#22・#26・D-23・FR-21・部分YES・通知順序の
全てが実行不能。ci試験はStubLLM直接構築で値を差し替えているが、stagingの常設workerは
settingsからbuild_worker_llm_gatewayで構築するため、**env→settings→stub応答の経路が未知例**。

| 案 | 内容 | 評価 |
|---|---|---|
| **A. settings新項目+gateway構築時にstub応答を組む(推奨)** | settingsへ`llm_stub_jev_noul`(既定0.5)・`llm_stub_jev_score`(既定2.0)を追加。build_llm_gateway/build_worker_llm_gatewayのstub分岐で、既定値と同一形状のjev_response辞書をこの2値から組み立ててStubLLMへ渡す | 経路がenv上書き可能(settingsの既存規律)・**既定値=現行定数なのでci/test-ciは挙動不変**(ピン追随のみ)・stubのみの分岐でreal経路に触らない。score軸は4軸同一値・noulは3キー同一値の固定でよい(#12の「双方の判定値の記録」は両キーの記録で成立) |
| B. composeから応答JSON全体を差し込む(envにJSON) | LATCH_LLM_STUB_JEV_RESPONSE='{"answers":{…}}'をそのままパース | 7キー全文をcomposeに書く=形状の知識がYAMLへ漏れる。キー追加時の追随が二重化。不採用 |
| C. 管理APIで実行時に書き換える | workerプロセスのstubへ後から注入 | 製品契約の汚染(ws-1が管理APIを退けたのと同型の理由)+再起動不要の利益も本単位では不要(値は固定・10 §3どおり) |

**stagingでの固定値: noul=0.90(=would_a/would_b/latent_yesの3キー共通)・score=3.6(=0.9換算)**。
根拠: (a)score=0.90は閾値0.60に対し明確なマージンがあり、match_level=high(≥0.90)経路も通る
(b)全ペア同一値のため**D-06・提示順の同点決着は実装の決定的ソート(aggregate降順→サイズ昇順→
intent_id辞書順・提示順=対象時刻昇順)で解決する**(引用確定値#14 — 同点でも非決定性にならない
ことをws-7の修正〔d87dec7②〕が保証済み)。(c)テスト間で値を変える必要がある項目はない
(閾値未満の負側証拠はLayer 1のEX failケースとG2のJev評価が担う)。

副作用の設計上の取扱い: would=0.9でL1通過ペアが軒並み提案対象になるため、D-08(日6件/ユーザー・
同時3件/Intent)で保留が大量発生する。これは**#8(時刻到来による再評価)のシナリオ材料として
積極利用する**(§2.4)と同時に、ユーザーあたり提案数が実装の提示順ソートに依存して
**実行ごとに揺れうる**リスクがある。対抗: (a)固定期待を置く検査は「参考L1実装で数えられる
決定的な量」(候補集合・EX個別ペア・集合構成)に限定する (b)提案の成否自体に依存する検査
(#15〜#19・D-23等)は**ハーネスが自前で作った専用シナリオIntent**(そのユーザーの適合ペアが
D-08上限以下になるように孤立配置)で行い、シード母集合の提案群に依存しない (c)計画時に
reference_l1でユーザーあたり適合ペア数を事前集計し、シードcluster素材がD-08に削られないことを
確認する(計画書の検証ステップに繰り込む)。

### 2.3 シード投入と観測経路

- **投入**: POST /v1/users(100人: display_name/birth_dateはYAMLどおり・auth subject=SU-xxxで
  JWT発行)→POST /v1/intents(300件: raw_text+structured_intent全フィールド明示・status=active)。
  保存経路にParser呼び出しがないためstub Parserの出蛇口に触れず、422/429は0件が前提
  (レート制限はユーザー単位で100人×3件=制限内・引用確定値#13)。JWTはハーネス内で
  worktreeの.staging/keys/idp_test_private.pemから発行(既存auth tools・composeと同一worktree
  の鍵で自動整合 — ws-1の環境メモ)
- **観測**: (a)API応答(nginx 18000経由・提出/回答/通知/チャット/ブロック/通報)
  (b)**DB読取**(ハーネス専用の読取接続を127.0.0.1:15432へ。SELECT専用・match_candidates/
  group_candidates/latches/latch_status_events/notifications/match_eventsの証拠読取。
  書き込みはAPI正規経路のみに限定するのが原則 — 19歳fixture DB強制のような直書きは
  今回は不要〔EX-004/005は20歳以上×18-19歳の非飲酒×飲酒ペアでLayer 1年齢条件を検証する
  設計済み・ws-2 §2.4〕)(c)push記録=`docker compose -f compose.staging.yaml logs`の
  `latch.push.send`行をパース(引用確定値#16)
- **Clock操作**: ControlledClockをハーネス内で直接構築(redis→16379)しset/advanceを呼ぶ
  (clock_cliと同一コード経路・サブプロセスなし)。set後のDB行矛盾を避ける作法
  (ws-1引継ぎ)として、**ハーネスは後退を行わず前進のみ**を使う
- **イベント注入**: シード投入自体がcreated EventをAPI経路で自然発行するため、
  make staging-publishは本番経路では不要(嵌入失敗時の回収・観察用に存在)

### 2.4 時刻・周期の設計(ws-1引継ぎ2件の回答)

- **tick周期は既定値のまま**(sweeper 60秒・reeval_runner_interval_sec 60秒): 60秒周期は
  04 §5/06 §6の確定値であり、stagingだけ短縮すると「確定値どおりに動くこと」の証拠が弱くなる。
  ハーネスの条件待ち上限はtickの2〜3倍(120〜180秒)とし、実行時間の増加(全行程45分目標)を
  受け入れる
- **Clock前進の旅路(シナリオ順序の制約)**: シード対象日はD+1〜D+5のため、大きな前進は
  シナリオの末尾へ置く。順序=①投入時は実時間(=仮想)のまま収束待ち→②期限系(#16・#8)は
  +3時間程度からJST 0時跨ぎまで段階前進→③最後にD+5以降へ進めてIntent期限切れ(expired
  バッチ)を確認。各シナリオが使う時刻値はISO明示(タイムボム回避・M3 ws-1の実例)

### 2.5 シナリオ→02#対応表(ハーネスの検査本体)

| 02# | 検査の骨子(staging観測) | 主な材料 |
|---|---|---|
| #5 | 総当たりスモーク: シード全Event処理後、Intentあたりの候補生成数がK_v=50以内に収まること+Jev実行がK_j=8/イベント以内であることの記録照合(本格検証はws-6) | 全シード・DB記録 |
| #6 | 既存Intentを含む候補の生成記録(EX passペアが有向両方向でmatch_candidatesに存在) | EX-006/008/010/013/015/017/020 |
| #7 | PATCH(時間)→debounce 10秒→updated Event→再評価で候補の生成/消失が記録される | ハーネス作成の専用ペア |
| #8 | 時刻到来による再評価: D-08日次上限で保留中の候補が、ClockでJST 0時を跨ぎ(リセットジョブ+保留キュー再評価)proposedへ昇格する記録 | 2.2の大量保留を材料に利用 |
| #9 | Hard Filter E2E: EX failペア(time0/distance/people/budget)がmatch_candidatesに**存在しない**+passペアは存在する(無向化突合) | EX-007/009/011/012/014/016/018/019/023/024 vs pass組 |
| #10 | 意味近接ペア(焼肉/肉系)が候補生成される。stub埋め込みでは「L1通過+K_v余裕ゆえの生成」であり意味近接 ranking の実証は実系統のみ(案A確定・§2.6の承認事項3) | ハーネス作成の専用ペア |
| #11 | 密集配置(互換Intent 30件をハーネスがAPI作成)→1イベントでL3通過がK_c=20・Jev実行がK_j=8を超えない記録 | ハーネス作成の密集群(ユーザー2人×15件でレート制限回避) |
| #12 | jev_resultにwould_a/would_b双方の値+provider/modelが記録される | シード評価行 |
| #13 | 閾値超過候補→notifications先行書き込み+pushドライラン記録。**muted Intentの提案はpush記録0件・latches proposed・回答可**(3値すべて実在: proposals_only 132/muted 87/nearby_also 81) | シード+pushログ |
| #14 | yes/no回答の記録と遷移(candidate→proposed→answered系イベント・responses) | #13の提案 |
| #15 | 双方YES→matched+チャット解放(POST messages 200) | 専用シナリオペア |
| #16 | 部分YESの期限確定(EX-027)+Intent期限切れ(expiredバッチ)。Clock前進で実時間待ち排除 | EX-027クラスタ・Clock |
| #17 | 成立前後の可視範囲比較(提案詳細応答の前後差分: 相手情報は成立後解放) | 専用シナリオペア |
| #18 | 3〜4人集合の全員YES成立(09 §3の6検証と同一手順のstaging再実行) | EX-028(3人)+専用4人組 |
| #19 | 成立LATCHで双方向の送受信記録 | #18の成立集合 |
| #20 | ブロック設定後に一致Intentを配置→候補生成されない | ハーネス作成ユーザー |
| #21 | 提案/成立画面経路の通報→reports受付記録 | #15の成立LATCH |
| #22 | visibility 2値分岐: hidden=条件サマリなし(「条件が合う候補があります」・一致度・回答期限のみ)/summary_only=サマリあり。応答キー集合比較(G3 ws-9方式のstaging移植) | シード(hidden 162/summary_only 138) |
| #23 | 座標精度値が応答に現れない(キー集合+値パターン検査) | 同上 |
| #26 | MLR算出: latch_status_eventsから分母=通知送信候補数・分子=全員YES候補数(グループも1)を集計し証拠化 | 全シーン終了時点 |
| D-10 | intended_reject_cases(EX-029〜033)の422 UNDER_AGE応答記録+EX-004/005(age_alcohol)が候補に現れないこと(S0突合で証拠化) | シード |
| D-23 | matched LATCHでブロック→提案cancelled・チャット409 CHAT_READONLY・読取専用応答 | 専用シナリオペア |
| FR-21 | push本文=汎用文とバイト一致・条件サマリ不含・drinking同一様式・summary_onlyはお知らせ+提案画面にサマリあり・hiddenは両方なし | pushログ+notifications API |
| FR-17 | 自己ペア(EX-001〜003)不生成・自己重複集合(EX-025)のgroup_candidates不生成+対照assert | シード |

実装順序の依存: S0(環境前提・投入・収束待ち・**L1無向突合**=#6/#9/#10/#D-10の土台と
aggregate照合〔ws-2 reference_l1再利用〕)→S1(候補系)→S2(提案・通知系)→S3(グループ・
期限系=Clock前進)→S4(成立後・D-23)→S5(安全系)。#14/#20/#21はG3繰越タグつきで
証拠に二重記録する。

### 2.6 embedding経路 — 案A(スタブのまま)に確定(2026-10-03ユーザーコスト承認)

| 案 | 内容 | コスト | 評価 |
|---|---|---|---|
| **A. スタブembeddingのまま(採用)** | compose.staging.yamlのLATCH_LLM_MODE=stubをそのまま使う。embeddingはテキストのハッシュ由来決定的ベクトル | **ゼロ** | 全項目の検査は成立する(L1はembeddingの**存在**のみ参照・候補生成はK_v余裕があるため発生する)。#10が「意味近接ゆえの検索」でなく「L1通過ゆえの生成」になる=文字面の合格はするがranking実証としては弱い。決定性・ci整合・コスト全面で有利 |
| B. 実系統gemini-embedding-001に切替(不採用) | 混合モード設定`llm_embedding_mode`新設でembedding系統のみrealへ | フル投入1回=$0.005〜0.011(試算の根拠は記録に残す: 300 Intent×正規化テキスト100〜250トークン×$0.15/1M・2026-10-03時点の公表価格) | #10とLayer 2/3が実運用に近づくが、docs上の必須要件ではない+混合モードという新たな設定意味論を持ち込む。**2026-10-03ユーザー裁定で不採用**。将来の再判断用に試算を残す |

採用根拠(案A): (a)M4設計前提「実課金ゼロ」(b)10 §1はテストモードを「記録済み応答スタブ
orレイテンシ注入」と規定し、10 §3 staging行はJevスタブ固定のみを明示 — embeddingの実系統は
docs上の必須要件ではない (c)#10の文字面合格基準(「候補として生成される」)は案Aで満たされる。
設定変更は一切なく(llm_embedding_mode新設も不要)、S0の収束待ちはstubレイテンシのままでよい。

## 3. ファイル構成

### 3.1 作るもの

| ファイル | 内容 |
|---|---|
| `backend/staging_tests/conftest.py` | session fixtures: staging前提確認(8サービス・head=0006・geo実データ)・APIクライアント(18000)・DB読取engine(15432・SELECT専用)・Clock操作(16379)・JWT発行(.staging/keys)・pushログ読取(docker logs)・結果収集器+sessionfinishで証拠YAML出力 |
| `backend/staging_tests/helpers_seed.py` | m4-seed.yaml読込→users/intents投入ペイロード変換・投入実行(users→intents順・structured明示)・reference_l1無向化突合ヘルパ |
| `backend/staging_tests/helpers_wait.py` | 条件ポーリング(タイムアウト・間隔・原因表示) |
| `backend/staging_tests/helpers_scenario.py` | 専用シナリオ作成(ユーザー+Intent: #7/#10/#11/#15/#18/#20/#21/D-23用・密集群・ブロック前提) |
| `backend/staging_tests/test_s0_env_seed.py` | 環境前提・シード投入・収束待ち・L1無向突合+aggregate照合(#6/#9/#10/D-10の土台) |
| `backend/staging_tests/test_s1_candidate_items.py` | #5スモーク・#6・#7・#8・#9・#10・#11・#12 |
| `backend/staging_tests/test_s2_proposal_items.py` | #13(muted含む)・#14・#17・#22・#23・#26・FR-21 |
| `backend/staging_tests/test_s3_group_deadline_items.py` | #18・#16(部分YES期限確定+Intent期限)・通知順序(EX-026)・自己重複集合(EX-025) |
| `backend/staging_tests/test_s4_aftermatch_items.py` | #15・#19・#21・D-23 |
| `backend/staging_tests/test_s5_safety_items.py` | #20・D-10(422応答記録) |
| `Makefile` | 追記: `staging-test`(uv run pytest staging_tests)・`staging-reset`(down -v→up→migrate→geo-importの初期化。stagingボリュームのみ削除・ci無干渉)。.PHONY更新 |
| `backend/src/latch/settings.py` | 追記: `llm_stub_jev_noul`(既定0.5)・`llm_stub_jev_score`(既定2.0)。embedding系統の変更はなし(§2.6案A確定) |
| `backend/src/latch/llm/gateway.py` | stub分岐2関数でsettings値からjev_response辞書を組んでStubLLMへ渡す(既定値=現行定数のため挙動不変) |
| `compose.staging.yaml` | api/worker共通envへ`LATCH_LLM_STUB_JEV_NOUL: "0.90"`・`LATCH_LLM_STUB_JEV_SCORE: "3.6"`を追記(10 §3の「判定値を固定」の実体。YAMLアンカー1箇所) |
| `backend/tests/unit/test_llm_stub_response_settings.py` | 新規: stub応答がsettings値に従うこと・既定値が現行定数と一致すること(回帰ピン) |
| `backend/tests/unit/test_settings.py` ほか | 追随: 新settings項目の機械ピン(test_llm_factory/test_settingsの既存機械検査への追記) |
| `docs/plans/M4/ws-3-report.md` | 報告書(02#対応表の実行結果・G3繰越回収の対応・証拠パス) |
| `docs/testassets/results/ws3-staging-result-<ts>.yaml` | 証拠(試験ID=02#・実施日・環境=latch-staging・シードbase_datetime・stub設定値・入力条件・合否・観測値) |

### 3.2 触らないもの

- `compose.yaml`(ci)・ci系Makefileターゲット・pyproject(testpaths含む)・既存テストの実行系
- パイプライン本体(layer1〜layer4・latch_engine・group_engine・worker handlers・sweeper・API契約)
- シード生成器・m4-seed.yaml本体構造(実行直前の`make seed-generate`再生成のみ・差分は対象日付)
- g1/g2資産(g2-jev-goldset.yaml・g1-*.yaml)・frontend・prototype・docs本文(本単位でdocs改版なし)
- ws-1のstaging/load資産の構造(compose.staging.yamlへのenv 2行追記を除く)

### 3.3 実行手順(報告書に再現可能な形で記録)

1. `make staging-reset`(down -v→up)→`make staging-keys`→`make staging-migrate`→
   `make staging-geo-import`(backend/data/geoの資材はciと共用・git管理外)
2. `make seed-generate ARGS="--base-datetime <実施日11:00JST>"`→`make seed-verify`
   (YAML差分は対象日付のみであることをgit statusで確認)
3. `make staging-test`(全シナリオ実行・証拠YAML出力)。全行程45分目標
4. 証拠YAMLと報告書を確認(ws-2のseed-statusをconfirmedへ改めるのは実使用後 — README規定)

## 4. テスト方針

1. **unit(毎コミット規律)**: 新settings項目のピン・stub応答が設定値に従うこと・既定値が
   現行定数とバイト一致すること(回帰)。実装はTDD(先にピンを赤→実装→緑)
2. **stagingハーネス自体の検証**: 本単位の本体はハーネスであり、その合否が10 §5の証拠になる。
   環境前提の不備(サービス停止・head不一致・geo不在・鍵不在)はS0で明確な名前つき失敗にする
   (原因不明の連鎖失敗を防ぐ)。再実行はstaging-resetからのフルリセットを前提とする
   (冪等な再入は行わない=状態依存を排除)
3. **決定性の担保**: 固定期待はreference_l1で機械再計算できる量に限定し、実行順序依存の
   提案割り当てに依存する検査は専用シナリオで隔離(§2.2の対抗策)。計画時にユーザーあたり
   適合ペア数を事前集計しD-08の削られ先を特定する
4. **test-ci緑維持**: マージ後main test-ci 1561+新規ピン。**マイグレーションなし・依存追加なし**
   (ハーネスの必要物は全て既存dev/main依存に存在: httpx・asyncpg・redis・pyyaml)。並行ws-5が
   マイグレーションを追加する場合、test-ci実行はスーパーバイザー検証時に直列化(運用ルール1)
5. **basename一意**: tests配下の新規unit試験ファイル名の衝突検査(運用ルール5)。staging_testsは
   tests配下外だが、ファイル名にも一意な接頭辞(test_sN_*)を付ける
6. **lint**: ruff format/check対象にstaging_testsを含める(backend配下のため自動)。arch testは
   src/latchのみが対象のためハーネスのtime参照(monotonic等)は影響外・ControlledClock経由を
   原則として使う

## 5. 完了条件(計画書への引き取り用ドラフト)

- [ ] `make staging-test`で全シナリオグリーン・証拠YAMLがdocs/testassets/results/へ出力
      (02#20項目+追加試験3件+配置ケースの対応表つき)
- [ ] G3繰越02#13〜#23のstaging再実行証拠が回収されている(報告書にG3判定資料との対応を記録)
- [ ] stub Jev判定値の固定経路がunitピンで担保され、ci/test-ciの挙動が不変(1561維持)
- [ ] lint緑・basename一意・マイグレーションなし(head=0006不変)・依存追加なし
- [ ] 実行手順が報告書に再現可能な形で記録(staging-resetからのフル手順)

## 6. supervisor承認事項(2026-10-03・6件とも裁定済み — 未解決の論点なし)

1. **§2.1ハーネス方式**: 承認。pytestスイート(testpaths外のbackend/staging_tests/)。
   B(CLIハーネス)・C(env切替で既存試験再利用)の不採用理由は§2.1に記載のとおり
2. **§2.2 stub判定値の固定経路**: 承認。settings新項目2つ(llm_stub_jev_noul/score・
   既定=現行定数)+gateway構築変更+compose.staging.yamlへの0.90/3.6追記。
   製品コード(settings・gateway builders)への変更を伴う点を含めて承認
   (ci挙動不変・ピン追随のみで既存試験への影響なし)
3. **§2.5シナリオ対応表**: 承認。#8=D-08保留→JST 0時跨ぎ→昇格・#10=stub下では文字面合格
   (ranking実証は実系統のみ — 案A確定によりこの取扱いで確定)・#11の密集群はハーネスがAPI作成
4. **§1.2の#27読み**: 承認。動作面=staging(D-10)・精度ゲート=ci資産(make g1-gate)参照
5. **§2.3観測経路**: 承認。DB読取(SELECT専用)・push記録=docker logsパース・ハーネス内直接の
   ControlledClock操作(clock_cliと同一コード経路)
6. **§2.4 tick周期は既定60秒のまま**: 承認(短縮しない)。全行程45分目標の受領

## 7. 未解決の論点

なし。embedding経路は案A(スタブのまま・ゼロ円)で確定(2026-10-03ユーザーコスト承認・
案Bの試算は§2.6に記録)。設計は確定・計画書(agent2)へ引き渡し可能。
