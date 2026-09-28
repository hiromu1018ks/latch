# M2 ws-4(Layer 3 Cheap Judge+コスト保護)設計メモ

- 作成: 2026-09-29(agent1)
- 前提: M2 ws-1〜ws-3 マージ済み(0bb6a2d・059aece・0c45aa7・main test-ci 769 passed)。並走単位なし(実行waveはws-4が単独)
- 参照仕様: 06 §4〜§5・04 §4 D-16・04 §5・05 §2・06 §8(D-15・D-24)・06 §9・07 §3・02 §4(#11)・10 §4.6

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

3つの部品を実装する。

1. **Layer 3 Cheap Judge**(06 §4): cheap_score = 0.5×類似度 + 0.3×ルールスコア + 0.2×語彙重なり
   の線形合成を **Layer 2 通過候補(K_v=50)全件**に計算して match_candidates.cheap_judge_score へ記録し、
   **上位 K_c=20**(cheap_score 降順・同点は intent_id 昇順)を次層(Layer 4・ws-5)への出力とする
2. **コスト保護の未実装2層**(06 §5・04 §5): Jev予算(1Intent 40回/日・1ユーザー120回/日・
   再評価頻度30分)と D-16 回数上限(日次30,000・月次600,000・80% alert+第一候補/フォールバック内訳)。
   **Jev実行の実体はws-5**のため、本単位は「実行直前に呼ばれる部品」としてのカウンタStore・判定Guard・
   再評価ガード(reeval)を作り、このうち**再評価ガードのみ本単位でパイプラインに組み込む**
3. **embedding_completed 起点の配線**(ws-3設計1.4-1が後続単位へ委ねたもの): stage1 への
   matching フック追加・worker/main.py への DI。Layer 1〜3 が初めて Event から駆動される

マイグレーション追加なし(cheap_judge_score カラムは 0001 で作成済み — STATUS.md 運用ルール1・2の
DB取り合いは発生しない)。依存追加なし(redis・fakeredis は既存)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | cheap_score = 0.5×埋め込み類似度(cosine そのもの) + 0.3×ルールスコア(時間帯・ペア予算の近さを0〜1に正規化) + 0.2×soft constraintsの語彙重なり(トークン一致率)。D-04降格のNG条件は`downgraded_from_ng`フラグで区別し語彙重もりの計算対象から明示的に除外(Layer 3を素通りしLayer 4入力へ渡る)。選定理由は決定的(追加APIコストゼロ)であること。**重みの初期値は運用データで調整する** | 06 §4・06 §2 |
| 2 | K上限: Cheap Judge判定 K_c=20。Kは「その層から次層へ渡す出力数の上限」。切り詰めはcheap_score上位、同点はintent_id昇順で決定的に崩す | 06 §8 D-24・01 §11 |
| 3 | Layer 1〜3(Hard Filter + Candidate Retrieval + Cheap Judge)の予算は p95 ≤1秒。Layer 3はルール計算のみ | 06 §1 |
| 4 | Jev予算3層: (a)1Intent 40回/日(JST 0時リセット)超過は候補をJev未判定として保留(match_candidates.status=skipped・D-15と同一の縮退。日次リセット後の次のMatch Eventで再評価) (b)1ユーザー120回/日(Active上限5×40の理論値と一致する共有プール)超過は同上 (c)同一Intentの更新による再評価は直近の詳細評価(パイプライン投入)から30分空ける。Event自体は処理済みとし再評価しない。次の更新Eventまたは30分Bucket再評価で拾われる | 06 §5(FR-03表) |
| 5 | 予算カウンタはRedis(04 §5のJevカウンタと同一経路)で user_id・intent_id・日付キーで保持し、実行要求側(Matching Worker)でINCRする。頻度制限はTTL 30分のキー(reeval:{intent_id})で判定する | 06 §5 |
| 6 | D-16: 日次上限30,000回(80%の24,000回でalert)・月次上限600,000回(80%でalert)。月次リセット時点は暦月初のJST 0時。月次到達時は運用者へ通知し復帰予定(暦月初)を明示。上限到達時はJev実行をスキップし対象候補を「Jev未判定」として記録。**実際のAPI呼び出しをすべて「1実行回数」に計上(フォールバックLLMへの切替呼び出しも同一の1実行回数)** | 04 §4 D-16 |
| 7 | 80% alertにはEvent源流上位ユーザーのレポートを添付: 当該期間のIntent別・ユーザー別のJev消費上位リスト(予算・頻度制限適用済みの消費)と第一候補・フォールバック別の実行回数内訳 | 04 §4 D-16・06 §5 |
| 8 | コスト保護の経路: Jev実行要求(Matching Worker)→Redisの日次/月次カウンタをINCR→80%到達でalert発報→上限到達で実行スキップ・候補を「Jev未判定」として記録。日次カウンタはJST 0時・月次は暦月初JST 0時でリセット。**TTL方式はUTC基準となりJST 0時と9時間ずれるため用いない**。リセットジョブの失敗時は最初のJev実行要求でカウンタの日付キーを検査し前日以前のキーを検知したらリセット(自己修復)。**カウンタは実行要求側(Matching Worker)で増やす(並行実行でも超過幅を1要求分に抑える)** | 04 §5 |
| 9 | alertの宛先とエスカレーションは運用設計に委ねる(本書は検知と制御の経路のみ確定) | 04 §5 |
| 10 | 縮退(D-15): 回数上限到達・Jev予算/頻度制限由来のスキップは「提案見送り・候補保留」。保留はmatch_candidates.status=skippedで表現し、日次リセット後や障害回復後のMatch Event・再評価経路で再評価される | 06 §8 D-15 |
| 11 | Layer 1〜5はembedding_completedを起点にのみ走る(作成・更新Eventの処理はEmbedding要求のキックまで) | 06 §1・§9 |
| 12 | match_candidates: retrieval_score / cheap_judge_score = 各層の結果。status値域は pending / evaluated / skipped(Jev未判定) / closed。同一バージョン内の再評価は既存レコードを更新 | 05 §2 |
| 13 | soft_constraints は `{text, downgraded_from_ng}` の配列(downgraded_from_ng=trueはD-04降格条件)。negative_constraintsはMVPで常に空 | 05 §2 |
| 14 | K上限の裏付け: 密集配置で Cheap Judge判定 ≦20 が記録で守られ、切り詰めが決定的(同点intent_id昇順。同一入力2回実行で同一結果)。02#11(1イベントあたりJev実行回数が上限を超えない)の本格観測はLayer 4実装後 | 10 §4.6・02 §4 #11 |
| 15 | 時刻参照はすべてClock経由(JST 0時リセットジョブとRedisカウンタのJST日付キーを含む)。テスト環境ではClock操作で再現できる | 04 §5・10 §1 |
| 16 | Embedding対象テキストは `{category.primary} / {時間帯} / {location.name} / {人数} / {soft_constraintsを「・」で連結}`(降格込み。実体はws-2実装済み) | 07 §3・06 §3 |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

- `worker/matching/runner.py`: `run_candidate_retrieval(conn, clock, intent_id) -> RetrievalOutcome`。
  起点検証→Layer 2検索→UPSERT。**Layer 3はこの延長として組む**(§2.1)
- `worker/matching/layer2.py`: `retrieve_topk` は SELECT に id/version/similarity/pass_count のみ。
  **ルールスコア・語彙重なりに必要な対象側データ(time_start・budget_max・structured_data)の列追加が必要**
- `worker/matching/origin.py`: Origin は budget_max・time_start/time_end を保有(起点側ルールスコアに使える)。
  **structured_data を持たないため SELECT への追加と Origin への語彙抽出が必要**
- `worker/matching/candidates.py`: UPSERT は retrieval_score のみ書く(DO UPDATE も同)。
  **cheap_judge_score の書き込み拡張が必要**
- `worker/stage1.py`: `_process_once` の種別処理で embedding_completed は「処理実体なし(processed)」。
  `embedding_hook: Callable[[str, uuid.UUID, int], Awaitable[None]]` のDIパターンがあり、
  ws-4は同じ形の **matching フック**を追加する(§2.7)
- `worker/main.py`: bus・engine・stage1・debouncer・embedding・backfill のDI構築。**redis 未接続**
  (redis_url は settings にあり・auth/ratelimit が使用)
- `ratelimit/limiter.py`・`ratelimit/store.py`: **INCR先行(429・422失敗も消費)**・バケット文字列は
  Clockから導出・Redis例外はfail-closed・INCRとEXPIREはpipeline併発・TTLは掃除用でリセット表現に
  使わない — Jevカウンタもこの規律を踏襲(M1 ws-4 supervisor承認「INCR先行」の前例)
- `worker/embedding_text.py`: structured_data から soft_constraints 抽出の実例
  (`_soft_constraints_phrase`。降格は連結に含む=Embedding用。Layer 3は降格を除外して別抽出)
- `tests/integration/conftest.py`: `redis_client` fixture(compose常設Redis)あり
- `tests/unit/ratelimit/`: fakeredis による store/limiter 試験の流儀
- alembic 0002 = head。**match_candidates.cheap_judge_score は 0001 で作成済み**

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

1. **Layer 4 Jev 本体**(ws-5): System One IF・フォールバック切替・K_j=8配分・jev_result記録・
   **Guard deny時の match_candidates.status=skipped 遷移の実行**(遷移先の意味は本単位が確定するが
   書き込むのはJev実行の直前=ws-5)・同一評価世代スキップ
2. **30分Bucket再評価・catch-upスキャン**(ws-6)。ただしその入口でも reeval ガード(本単位の部品)を
   再利用する
3. **circuit breaker・障害注入・G2ハーネス**(ws-8・10 §4.5)
4. **リセットジョブのスケジューラ本体**(M3-4: 0時キックと**完了時の保留キュー再評価イベント発行**が
   本体。カウンタのJSTリセット正確性は本単位の日付キー方式が常時担保する — §2.5)
5. **alertの通知媒体**(FCM等)。ci段階は構造化ログ(§2.4。M0 ws-2の送信記録と同型のsupervisor承認事項)
6. **トレース計測・Intent別/ユーザー別ダッシュボード**(M4 Observability)。本単位はalert用の集計関数のみ
7. **cheap_score重み・正規化パラメータの運用調整**(06 §4のとおり初期値。評価は09・運用データ)

## 2. 実装方式の選択と推奨

### 2.1 Layer 3の実行位置 — 推奨: Layer 2と同一トランザクション・同一実行経路(runnerの延長)

**案A(推奨)**: `run_candidate_retrieval` の内部で、Layer 2 の SELECT 結果(§2.1-補足の列拡張をした
`retrieve_topk`)を受け取り、Python 純関数で cheap_score を計算、既存の UPSERT に
cheap_judge_score を乗せて全件記録、上位 K_c=20 を選定して Outcome へ載せる。トランザクションは
呼び出し側(stage1 の `_process_once`)の1本に同乗(ws-3設計2.4の契約どおり AsyncConnection を受け取る)。

- 利点: 06 §1 の層構成(L1→L2→L3 の直列)を1経路で体現する。Layer 1〜3 合計 p95 ≤1秒(引用#3)の
  予算管理も単一経路で済む。中間結果の再取得がない。UPSERT 1回で retrieval_score と
  cheap_judge_score の両方が原子に書かれる(2層に分けたトランザクション間で値が混ざらない)
- トレードオフ: runner の責務が広がる。→ layer3.py に計算を純関数として隔離し、runner は呼ぶだけ

**案B(不採用)**: `run_cheap_judge` を別関数・別トランザクションに分ける。層独立性は明示的だが、
Layer 2 の結果(K_v=50)を一度受け渡す必要があり(DB再読みかプロセス内受け渡し)、
トランザクション分割で「retrieval_score更新済み・cheap_judge_score未更新」の中間状態が観測され得る。
06 §1 は層を分けて述べるが実行の原子性を損なえいない(冪等性はUNIQUEで担保されるが、失敗時の
再試行で部分更新が残る)。利点が符号計算の隔離だけで、案Aでも純関数隔離で達成できる

**案C(不採用)**: ルールスコア・語彙重なりをSQLで一括計算(UPDATE ... SET cheap_judge_score = ...)。
bigram生成・Jaccard計算をSQLで書くと複雑かつ決定性の unit 検査(09のOffline評価と同じ値)が
供給できなくなる。K_v=50 と小さく Python 計算で十分(引用#3「Layer 3はルール計算のみ」)

### 2.2 cheap_score 構成要素の実装定義(docsが規定しない部分の初期値)

06 §4 は要素と重み(0.5/0.3/0.2)のみを確定し、正規化の詳細を規定しない。**決定的(同一入力→
同一出力・APIコストゼロ)であれば 06 §4 の要件(09 Offline評価と10試験で同じ値を扱える)を満たす**ため、
次のとおり実装定義する。重みと同じく**運用データでの調整対象**とする(引用#1の精神)。

| 要素 | 定義 | 境界値 |
|---|---|---|
| 類似度 | Layer 2 が計算した cosine 類似度(retrieval_score と同一の値)を**再計算せずそのまま**用いる | cos類似度は[-1,1]を返しうるが正規化しない(06 §4「そのもの」。負値はスコアを引き下げるだけで安全側) |
| ルールスコア | **(時間近さ + 予算近さ) / 2**(docs列挙の2要素を等分) | — |
| └ 時間近さ | `1 − min(Δmin, 180) / 180`。Δmin = 両Intentの time_start 差の絶対値(分) | Δ=0で1.0。Δ≥180分で0.0。180分=Intent実効寿命3時間(03 D-19)を飽和点にする |
| └ 予算近さ | 双方 budget_max 非NULL: `1 − min(|Δbudget|, 3000) / 3000`。片方または双方NULL: **0.5(中立)** | 差0円で1.0。差≥3,000円で0.0。NULL=制約なし(05 §2)で近さが定義できないため中立 |
| 語彙重なり | 後述の**文字bigram集合のJaccard係数**(|A∩B| / |A∪B|) | 双方の語彙集合が空(soft constraintsなし): **0.5(中立)** |

- **時間近さは開始時刻差のみ**(交差判定はLayer 1が持つため、Layer 3は「近いほど高い」の距離感だけを
  担う。time_end との組合せ評価はしない)
- **3000円飽和**は初期値: Layer 1 のペア予算fail線500円(06 §2)の6倍で、典型的な飲食予算帯の
  上限相当。調整対象
- **NULL中立0.5・語彙空中立0.5 の根拠**: いずれも「情報がない」ことを満点にも零点にもしない。
  満点にすると条件なしIntentが常に上位を占め、語彙一致ペア(意図して高い)より優遇され得る
- **トークン化に文字bigramを選ぶ理由**: 形態素解析器(sudachi等)は依存追加と辞書版による値の揺らぎ
  (決定性の喪失)があり、06 §4 の決定性要件と衝突する。文字bigramは依存ゼロ・完全決定で、
  日本語短文(soft constraintsの文言)の語彙一致検出に実用十分
- **語彙抽出**: structured_data.soft_constraints のうち `downgraded_from_ng != true` の要素の
  `text` のみ(引用#1・#13)。各textをNFKC正規化し、2文字未満のtextはbigramなし(空集合扱い)、
  textをまたいだbigramは作らない(文言単位で集合を作り和集合を取る)
- **丸め**: 計算・DB書き込みとも丸めない(float→numericへそのまま。丸め基準を導入すると同点判定と
  10 §4.6 の決定性検証が丸め桁に依存する)

### 2.3 K_c 選定と cheap_judge_score の記録 — 推奨: 全件計算・全件記録・上位K_cを次層出力

- **Layer 2 通過全件(≤50)に cheap_score を計算し全件の cheap_judge_score を UPSERT する**。
  K_c 内外の全候補の値が記録に残ることは 10 §4.6(記録で守られる検証)と将来の重み調整(09)の
  供給源になる。計算は決定的でコストゼロ(引用#1)のため全件計算にデメリットがない
- **次層(Layer 4)への出力は上位 K_c=20**: `sorted(候補, key=(-cheap_score, intent_id))[:20]`。
  同点は intent_id 昇順(引用#2)。ws-4 の時点ではこの選定結果を `RetrievalOutcome.topkc` として
  返すのみで、消費者(Layer 4)は ws-5
- **K_c 外の行は status='pending' のまま**(引用#12: pending=Jev未評価。skipped への遷移は
  Jev実行の直前〔ws-5〕、evaluated は Jev評価後〔ws-5〕、closed は stage1 削除処理〔実装済み〕が担う。
  本単位は status に触れない)

### 2.4 コスト保護部品 — 推奨: `worker/cost/` パッケージ(store+guard+reeval)

**モジュール構成**: Redis操作(store)・判定(guard)・再評価ガード(reeval)を分け、 ratelimit/ と
対称の構成にする(worker内で完結するため worker/ 配下に置く)。

**キー設計(実装定義・接頭辞 `jev:` — ratelimit/store.py のコメント「JevカウンタはM2で別接頭辞」どおり)**

| キー | 用途 | TTL |
|---|---|---|
| `jev:daily:{yyyymmdd}` | D-16 日次グローバルカウンタ(30,000) | 48時間 |
| `jev:monthly:{yyyymm}` | D-16 月次グローバルカウンタ(600,000) | 45日 |
| `jev:intent:{intent_id}:{yyyymmdd}` | 1Intent 40回/日 | 48時間 |
| `jev:user:{user_id}:{yyyymmdd}` | 1ユーザー120回/日 | 48時間 |
| `jev:exec:{yyyymmdd}:{provider}` | 第一候補/フォールバック内訳(provider=typesafe_jev / fallback_llm) | 48時間 |
| `reeval:{intent_id}` | 再評価頻度30分(06 §5のキー名そのまま) | 30分(1800秒) |

yyyymmdd / yyyymm は `clock.jst_date()` から導出(引用#15。実時間参照禁止 — C2)。TTLは掃除用で
リセット表現には使わない(ratelimit と同一規律。リセット=日付キー切替、§2.5)。

**INCRの規律(INCR先行 — ratelimit・M1 ws-4 supervisor承認と同一)**

1. `guard.request_execution(intent_id, user_id)` を Jev 実行の直前に呼ぶ(ws-5。本単位は部品のみ)
2. 日次・月次・intent別・user別の4カウンタを INCR し、**INCR後の値が上限を超えていたら deny**
   (理由: intent_daily / user_daily / global_daily / global_monthly)。**denyされた要求もカウントを
   消費する**(判定とカウントの間の並行すり抜けをRedis上で潰す・引用#8「超過幅を1要求分」)。
   許可される要求の数は各カウンタで必ず上限以下に収まる
3. **内訳カウンタ(jev:exec)は実際の実行経路確定後に +1** する `record_execution(provider)` を
   別メソッドとして持つ(D-16「実際のAPI呼び出しを計上」を正確に満たすため。フォールバック切替は
   ws-5 が呼ぶ)
4. **80% alert**: 各INCRの戻り値が**丁度しきい値(24,000 / 480,000)に等しいとき**に1回だけ発報する
   (prev < threshold ≤ curr の跨ぎ検出。Redis INCR の原子性により跨ぎは1回しか起きない。
   自己修復リセット後の再跨ぎは別期間として正当に再発報)。月次上限到達(30万+1到達時)は
   復帰予定(暦月初JST 0時)を明示した到達ログを別途1回出す(引用#6)
5. **alert媒体は構造化ログ**(`latch.cost.alert` logger。レポート本文を添える)。宛先は運用設計に
   委ねられている(引用#9)ため、媒体差し替え可能な形でログ出力関数をguardから分離する。
   M0 ws-2「送信記録は構造化ログで開始」と同型のsupervisor承認事項として扱う
6. **80% alertに添えるレポート**(引用#7): `jev:intent:*:{yyyymmdd}` と `jev:user:*:{yyyymmdd}` を
   SCAN+GET で集計した消費上位リストと `jev:exec:{yyyymmdd}:*` の内訳を、alertログのフィールドに
   載せる。alertは80%跨ぎ時のみ(1日/月に高々数回)のためSCANのコストは許容する
7. **Redis例外はfail-closed**: guard内で捕捉し専用例外へ包んで投げる(ratelimitの
   RateLimitDependencyError と同型)。呼び出し側(ws-5のLayer 4直前・ws-4のreeval)は再試行経路へ
   載せる。コスト保護の失敗を実行の通り抜けにしない(D-16「上限はコストの保証」)

### 2.5 JSTリセットと自己修復 — 推奨: 日付キー切替方式(ジョブ不在でも正確)

04 §5 は「JST 0時のリセットジョブ」を規定し、「TTL方式はUTC基準でずれるため不採用」「ジョブ失敗時は
実行要求で前日以前のキーを検知して自己修復」と続ける。本単位は**カウンタのキー名にJST日付
(yyyymmdd / yyyymm)を含める**方式でこれを満たす(引用#5「日付キー」・引用#8)。

- Clock.jst_date() から導いた本日キーへ INCR するため、**JST 0時を跨ぐと新キーで0から始まる**
  (=リセット)。TTLは掃除のみに使う(§2.4)。月次も暦月初のyyyymm切替で同一
- 「前日以前のキーを検知して自己修復」は、**キー名を実行時のClockから常に導く**ことで
  「昨日のキーを使い続ける」状態が構造的に発生しない(=自己修復を内包した実装)
- 04 §5・06 §10・M3-4 のリセットジョブの本体は**保留キュー(skipped候補)再評価イベントの発行**に
  ある(引用#10「日次リセット後の…Match Event・再評価経路で再評価される」の起点)。カウンタの
  リセット正確性に依存しないためM3-4への先送りは安全。この位置づけをsupervisor承認事項とする(§5)

### 2.6 再評価頻度ガード(reeval)のパイプライン組み込み

- matching フック実体(worker/main.py の `_run_matching`)の**先頭**で `reeval:{intent_id}` を
  `SET NX EX 1800` する。**キーが既に存在する場合は再評価をスキップして return し、Event は
  通常どおり processed で閉じる**(引用#4(c)「Event自体は処理済みとし、再評価は行わない」)。
  version検査は stage1 が済ませてからフックを呼ぶため、ガードはこれより後で効く。スキップ理由は
  構造化ログ(`latch.matching` logger)に記録する
- 初回(作成Event由来)はキーがないため必ず評価される。30分経過(TTL切れ)後の次のEventで再評価される
- Bucket再評価・catch-upスキャン(ws-6)も同じガード部品を使う(起点を問わず「直近の詳細評価から
  30分」を統一)。**本単位ではフック経由の呼び出しのみ実装**
- Redis例外はfail-closed(§2.4-7)。フックが例外を出せば `_process_once` のトランザクションが
  ロールバックされ、stage1 の再試行5回→quarantined の既存経路に載る

### 2.7 stage1 matching フックと worker DI(ws-3設計1.4-1の配線)

- `Stage1.__init__` に `matching_hook: Callable[[AsyncConnection, uuid.UUID], Awaitable[None]] | None`
  を追加(embedding_hook と同型。conn を渡すのは `run_candidate_retrieval` の契約(AsyncConnection
  を第一引数に取る純関数)への同乗を可能にするため — ws-3設計2.4)
- `_process_once` の種別処理で `embedding_completed` のときフックを呼ぶ:
  `await self._matching_hook(conn, intent_id)`。**Event行の processed マークと同一トランザクション**
  (マッチング失敗→ロールバック→再試行。06 §9 の再試行経路に自然に載る)
- フック実体(worker/main.py): reevalガード(§2.6)→`run_candidate_retrieval(conn, clock, intent_id)`
  (Layer 1〜3)。reevalのRedis呼び出しがDBトランザクション内に入るが、1回のSET NX(低レイテンシ・
  compose内ネットワーク)であって接続の長期保持を生まないため許容する
- `worker/main.py` の DI 追加: redis.asyncio クライアント(`settings.redis_url`・
  `decode_responses=True`)を run() 内で構築・finally で aclose。reevalガードを Worker へ注入可能に
  (unit試験はスタブ注入・未注入時はrun()で構築 — 既存DIの流儀)

### 2.8 マイグレーション・docs改版 — 追加なし

- alembic は 0002 のまま(cheap_judge_score は0001作成済み)。**共有ci-dbの alembic_version を
  進めない**(運用ルール1・2のDB取り合いが発生しない)
- docs(01〜12)の改版不要。§2.2 の実装定義は 06 §4 が初期値として委ねた範囲、§2.5 の解釈は
  04 §5 の要件(JST基準リセット・自己修復)を満たす実装方式の選択であり、それぞれ本メモに記録する

### 2.9 採用しないもの(YAGNIによる切り捨て一覧)

1. **cheap_scoreのSQL内一括計算**(§2.1案C)。決定性のunit供給源を失う
2. **形態素解析器の導入**(§2.2)。依存追加と辞書版依存の非決定性
3. **カウンタの固定キー+0時DELETEジョブ方式**(§2.5)。日付キー切替が優る(ジョブ不在でも正確)
4. **K_c=20・各上限値のsettings化**(D-24・D-16・06 §5 の確定値は module 定数。ws-3 §2.8-3と
   同一規律。調整はdocs改版を伴う)
5. **alertの外部通知実装**(媒体は運用設計。ciは構造化ログで開始 — supervisor承認事項)
6. **Jev予算GuardのLayer 3パイプラインへの組み込み**(Guardの呼び出しは「Jev実行の直前」=ws-5。
   本単位は部品とunit試験のみ)
7. **リセットジョブ本体・保留キュー再評価イベント**(M3-4)
8. **reevalスキップ理由の match_events payload への記録**(processedのextraは既存の破棄理由用。
   06 §5 は理由の保存を要求しない。構造化ログで足りる)
9. **cheap_judge_scoreの小数丸め**(§2.2。同点判定と決定性を桁依存にしない)
10. **Layer 2のSELECTを分割してstructured_dataを別取得**(1クエリに列を足すだけで済む。往復増は不利)

## 3. ファイル構成

### 3.1 作るもの(新規ファイル)

```text
backend/src/latch/worker/matching/layer3.py
    # cheap_score計算の純関数群: rule_score(origin, cand)・vocab_overlap(a_texts, b_texts)
    #   ・_bigrams(texts)→frozenset(NFKC+文字bigram・2文字未満は空)
    #   ・_soft_texts(structured_data)→downgraded_from_ng=true除外のtext抽出
    #   ・cheap_score(similarity, rule, vocab)=0.5/0.3/0.2合成
    #   ・select_top_kc(scored)→降順・同点intent_id昇順の上位K_CHEAP=20
    #   ・定数: K_CHEAP=20・TIME_SATURATION_MIN=180・BUDGET_SATURATION_YEN=3000・NEUTRAL=0.5
backend/src/latch/worker/cost/__init__.py
    # 公開API: JevCostStore・JevCostGuard・ReevalGuard・deny理由の定数
backend/src/latch/worker/cost/store.py
    # Redis操作を閉じ込めるStore(ratelimit/store.pyと対称):
    #   incr_daily/incr_monthly/incr_intent/incr_user(INCR+EXPIRE pipeline)
    #   record_execution(provider)(jev:exec:{yyyymmdd}:{provider}のINCR)
    #   set_reeval_nx(intent_id)(SET NX EX 1800)・scan_report(day)(SCAN+GET上位集計)
    #   key_prefix引数(既定""。integration試験での共用Redis干渉防止用)
backend/src/latch/worker/cost/guard.py
    # request_execution(intent_id, user_id)→Allow/Deny(理由4種): 4カウンタINCR先行判定
    #   ・80%跨ぎalertログ1回(24,000/480,000)・月次上限到達ログ(復帰予定明示)
    #   ・alertログ出力は関数分離(媒体差し替え可能)
    #   ・Redis例外→専用例外(fail-closed)
    #   ・定数: DAILY_LIMIT=30000・DAILY_ALERT=24000・MONTHLY_LIMIT=600000
    #     MONTHLY_ALERT=480000・INTENT_DAILY_LIMIT=40・USER_DAILY_LIMIT=120
backend/src/latch/worker/cost/reeval.py
    # ReevalGuard.allow(intent_id)→bool: SET NX EX 1800(失敗=例外=fail-closed)
backend/tests/unit/matching/test_layer3.py
    # 純関数の全境界(§4.1)
backend/tests/unit/cost/test_store.py
backend/tests/unit/cost/test_guard.py
backend/tests/unit/cost/test_reeval.py
backend/tests/integration/test_matching_cheapjudge.py
    # 実DB・実RedisでのLayer 3記録・K_c選定・reeval(§4.2)
```

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更内容 |
|---|---|
| `worker/matching/layer2.py` | `_SELECT_TOPK` へ `i.time_start, i.budget_max, i.structured_data` の列追加。`RetrievedCandidate` に同フィールド(jsonbはstrで返りうるためdictへ解釈) |
| `worker/matching/origin.py` | `_SELECT_ORIGIN` へ `i.structured_data` 追加。`Origin` に `soft_texts: tuple[str, ...]`(降格除外済み)を追加 |
| `worker/matching/candidates.py` | `_UPSERT` の INSERT 列と DO UPDATE 句へ cheap_judge_score を追加。`upsert_candidate` に引数 |
| `worker/matching/runner.py` | Layer 3 組込み: retrieve_topk 結果全件のcheap_score計算→全件UPSERT→`select_top_kc`。`RetrievalOutcome` に `topkc: list` を追加 |
| `worker/matching/__init__.py` | 公開APIの追記(layer3・RetrievalOutcome拡張) |
| `worker/stage1.py` | `matching_hook` パラメータ追加と `_process_once` の embedding_completed 種別での呼び出し(§2.7) |
| `worker/main.py` | redis クライアント構築・ReevalGuard・matching フック実体 `_run_matching` と stage1 への DI(§2.7) |
| `tests/unit/matching/test_layer_sql.py` | SELECT句・UPSERT句ピンの期待値更新(機械的追随。§4.1) |
| `tests/unit/matching/test_origin.py` | structured_data 読み取り・soft_texts 抽出の追記 |
| `tests/unit/matching/test_runner.py` | Layer 3 呼び出しの追加検証(スタブ) |
| `tests/unit/test_worker_stage1.py` | matching フック試験の追記(embedding_hook試験パターンを踏襲) |
| `tests/unit/test_worker.py` | `_run_matching` DI 配線のピン(最小) |

既存 integration(test_matching_hardfilter.py・test_matching_retrieval.py)は `run_candidate_retrieval`
の外部契約(引数・skip理由)が不変のため**無変更で動く見込み**。回帰で確認し、期待値の機械的追随が
必要になった場合は報告書に理由を記録する。

### 3.3 触らないもの

- `backend/src/latch/intents/`・`auth/`・`users/`・`ratelimit/`・`geo/`・`llm/`・`g1gate/`・`events/`
  (ratelimit は規律の参照元。コードは変更しない)
- `backend/src/latch/worker/` の既存ファイルのうち stage1.py・main.py 以外(embedding.py・
  embedding_text.py・debounce.py・backfill.py・__main__.py)
- `backend/alembic/`(0002 が head のまま)
- `backend/tests/` の既存ファイルのうち §3.2 に列挙した以外(特に test_events_pipeline.py・
  test_matching_hardfilter.py・test_matching_retrieval.py)
- `compose.yaml`・`Makefile`・`backend/pyproject.toml`(依存追加なし)・`frontend/`・`prototype/`
- `docs/`(01〜12。改版対象なし)

## 4. テスト方針

### 4.1 unit(`make test`。外部プロセス不要・fakeredis・スタブで決定的)

- **test_layer3.py**:
  - 時間近さ: Δ0分=1.0・Δ180分=0.0・Δ90分=0.5・Δ240分=0.0(飽和)
  - 予算近さ: 差0=1.0・差3000=0.0・差1500=0.5・片方NULL=0.5・双方NULL=0.5
  - 語彙重なり: 同一文言=1.0・共通なし=0.0・部分一致の中間値をbigram手計算でピン
    (例: 「焼肉」→{焼肉} vs 「焼肉好き」→{焼肉,肉好,好き} → Jaccard=1/3)。
    downgraded_from_ng=true の文言が集合に入らないこと。2文字未満の文言=空寄与。
    NFKC(全角半角混在の正規化)
  - 双方語彙空=0.5。cheap_score合成の重みピン(0.5/0.3/0.2・代表的な1ケースを手計算値と照合)
  - select_top_kc: 降順・**同点はintent_id昇順**(意図的に同点を作出)・21件以上で20件ピン・
    丸めなし(同一入力で完全同一float)
- **test_store.py**(fakeredis・ratelimit/test_store.py流儀): キー形式ピン(接頭辞jev:・日付・
  provider別)・TTL設定値(48h/45d/1800s)・INCR+EXPIREがpipelineであること・record_executionの
  経路別キー・key_prefixの付与
- **test_guard.py**(fakeredis):
  - INCR先行: denyされた要求もカウンタが増えること(41回目deny後さらに要求→カウンタ42)
  - deny理由4種の判定(intent別41回目・user別121回目・日次30001回目・月次600001回目)。
    guard の上限定数は実際の値(30,000等)のまま試験する。実際に30,000回INCRするのは
    重いため、**Storeをスタブ化して任意のカウンタ値を返させ**、境界(23999/24000/24001・
    30000/30001・480000・600000・41・121)を検証する(Store自体のINCR正確性はtest_storeが担保)
  - 80%跨ぎ: INCR戻り値==24000のとき1回だけalert(23999・24001では発報しない)・
    月次480000も同様・月次600000到達で復帰予定明示ログ1回
  - レポート集計: スタブRedis(fakeredisにseed)から上位リスト・内訳が取れること
  - Redis例外→専用例外(fail-closed)
- **test_reeval.py**(fakeredis): 初回allow・キー残存時deny(SET NX)・TTL=1800・Redis例外で例外
- **test_worker_stage1.py追記**: embedding_completed で matching_hook が conn と intent_id で
  呼ばれること・hookなし従来挙動(processedのみ)不変・hook例外でprocessedにならない(再試行側へ)
- **test_layer_sql.py期待値更新**(機械的追随): SELECT列追加(time_start・budget_max・structured_data)・
  UPSERT句にcheap_judge_score(INSERT列とDO UPDATE両方)・bind paramsに増分なし
- **test_origin.py追記**: structured_data(str返りを含む)からのsoft_texts抽出・降格除外
- **test_runner.py追記**: layer3計算→全件UPSERT→topkc選定の呼び出し順(スタブ)

### 4.2 integration(`make test-ci`。compose常設DB・pgvector・実Redis)

**DB・Redis残存への対抗策(ws-3レビュー引継ぎ — STATUS.md M2 ws-3記録の趣旨を制度化)**

1. **カテゴリ分離**: 本単位の試験が作る Intent の category_primary を**テスト専用値**
   (例: `ws4cheap`・試験ファイル内定数)に統一する。Layer 1 の完全一致条件(06 §2)により、
   ci-db に残存する他試験・学習資産検証由来の Intent と**構造的に交差しない**。
   「結果が空」型・件数型の期待が安定する
2. **teardown完全性**: 試験が作成した user を fixture で追跡し、成功・失敗にかかわらず
   try/finally で **FK順(match_candidates → match_events → intents → users)** で削除するヘルパーを
   試験ファイル内に置く(test_events_pipeline.py user_env の FK 順に同じ)。assert前のデータ検証で
   pytest.fail しても teardown が走る構造にする
3. **件数assertの限定**: 全体件数の期待はカテゴリ分離された自己完結配置(K_c境界試験等)に限る。
   特定ペアの存在/不在の期待を基本とする
4. **Redisキー分離**: Store の key_prefix(§3.1)に試験ごとの一意接頭辞(uuid)を渡し、teardown で
   `SCAN {prefix}*` + DELETE(グローバル日次カウンタ `jev:daily:{yyyymmdd}` 等の共用干渉を防ぐ)。
   FakeClock 固定日付による他試験との同日キー衝突も同じ仕組みで回避する
5. **grepドリフト確認**(実装時): テストの意図が「空リスト」等の全域比較に倒れていないか
   再読し、倒れている場合は1〜3の手段へ置き換える

**試験内容(test_matching_cheapjudge.py)**

1. **cheap_judge_score実DB記録**: API でユーザー登録・active Intent 作成 → embedding は
   `UPDATE intents SET embedding = CAST(:vec AS vector)` で直接挿入(ws-3流儀) →
   `engine.begin()` で `run_candidate_retrieval` を直接呼ぶ → 全候補行に cheap_judge_score と
   retrieval_score が記録・status='pending' 維持・バージョン組はSELECT時点
2. **K_c=20切り詰めの決定性**: テスト専用カテゴリで Hard条件成立な対象25件(一部は
   embedding・条件を同一にして完全同点を作出)→ 全25件に cheap_judge_score 記録・
   **topkc は20件・降順・同点は intent_id 昇順**。同一入力2回実行で同一結果(10 §4.6 の
   Layer 3 部分・02#11 の前段)
3. **スコア構成の実挙動対照**: 時間近い/遠い・予算近い/遠い・語彙一致/不一致の対照ペアで
   cheap_score の大小関係が定義どおり(§2.2 の表の値を実測と照合)
4. **reevalガード(実Redis)**: key_prefix付きReevalGuardで初回allow・即再投入はskip・
   FakeClock を30分進めるとallow(TTL切れ)
5. **配線(stage1フック経由)**: embedding_completed を表す Event を stage1.intake へ投入
   (または `_process_once` 相当の直接呼び出し)し、スタブでない実 runner(
   fakeredisのReevalGuard・実DB)で match_candidates 行が増えること・Event行がprocessedに
   遷移することを確認(workerプロセス起動は不要。ws-3と同じ直接呼び出し流儀)
6. 既存全数(test-ci 769)のグリーン維持

### 4.3 検証手順(報告書への明記用)

1. `make test`(unit)→ lint
2. `docker compose build api` → `make test-ci`(運用ル則4。**マイグレーション追加なしのため
   alembic_version は不動・DB取り合いなし**。並走単位なしなので実行タイミングの制約もなし)
3. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
   (basename一意性・運用ルール5。新規ファイルは test_layer3・test_store・test_guard・test_reeval・
   test_matching_cheapjudge で既存と衝突しない)
4. test-ci 実行後、試験が残した Redis キー(prefix掃除)と user データ(teardown)が残っていない
   ことを1回手動確認(対抗策の実効性検証・初回のみ)

## 5. 未解決の論点・実装時確認事項

1. **(supervisor承認事項)alert媒体=構造化ログで開始**(§2.4-5)。04 §5 は宛先を運用設計へ委ねる。
   M0 ws-2 の送信記録(構造化ログで開始・DB化は差し替え可能)と同型の判断。媒体の実装は運用設計確定後
2. **(supervisor承認事項)04 §5 リセットジョブの位置づけ**(§2.5): カウンタのJSTリセットは日付キー
   切替で常時担保され、ジョブ本体(保留キュー再評価イベント発行)はM3-4とする解釈。ジョブをws-4で
   先行実装する場合はスケジューラ(docker cron・asyncioタスク)の選定が追加で必要
3. **(supervisor承認事項)既存試験の期待値更新**(§3.2): test_layer_sql.py(SELECT句・UPSERT句)等の
   機械的追随。設計変更に伴う正当な更新であり、合流試験の期待値変更は実装報告で明示する
4. **(実装時確認)jsonb列の読み取り型**: layer2 SELECT 追加後の structured_data が str で返るか
   dict で返るか(asyncpg・SQLAlchemy asyncio の組合せ)。str の場合は json.loads(embedding.py と
   同規律)。いずれでも設計不変
5. **(実装時確認)integrationのtest_matching_retrieval.py回帰**: runner外部契約は不変のため
   無変更で通る見込み(§3.2)。UPSERT句追加が既存期待に影響した場合は機械的追随を報告する
6. **(ws-5引継ぎ)Guardの呼び出し契約**: `request_execution(intent_id, user_id)` を Layer 4 実行直前に
   呼ぶこと・deny時の match_candidates.status=skipped 遷移とその理由記録はws-5が実装すること。
   `record_execution(provider)` はフォールバック切替完了側が呼ぶこと(実際のAPI呼び出しベース)
7. **(ws-6引継ぎ)reevalガードの再利用**: 30分Bucket再評価・catch-upスキャンの入口でも同一部品を
   使用(起点を問わず30分頻度を統一)。ガード拒否時のBucket側の扱い(スキップ)はws-6設計で確定
8. **(先送り記録)正規化パラメータの調整**: 180分・3000円飽和・NULL/空語彙の中立0.5は初期値
   (§2.2)。09 の評価と運用データで調整する。変更は layer3.py の定数1箇所に閉じる
9. **(先送り記録)cosine類似度の負値**: 極端に逆相なベクトルで cheap_score が負になり得るが
   正規化しない(06 §4「そのもの」)。ランキング下位に落ちるだけで機能障害なし。運用で問題が
   顕在化した場合のみdocs改版を協議する
