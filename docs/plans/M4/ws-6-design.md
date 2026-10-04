# M4 ws-6(load負荷試験+総当たり検出)設計メモ

- 作成: 2026-10-04(agent1)
- 前提: M4 ws-1〜ws-5完了(マージ後main test-ci 1664 passed・alembic head=0008)。本単位が依存する
  測定基盤はws-4が構築済み(HTTP基本メトリクス込み=G4時確認事項1)。**本単位はマイグレーション追加なし**
  ・既存プロダクトロジックの改変なし(計測の観測点追加・stub遅延の分布化・load環境接続・注入器新設のみ)
- 参照仕様: 10 §4.1〜§4.2(主)・§1(テスト環境)・§2(テストデータ)・§5(証拠)・04 §7(計測)・12 M4-4・
  08 §5.4(レート制限=負荷用ユーザー設計の制約)・06 §2〜§6(観測対象の各層)・STATUS運用ルール6・
  ws-1 design §2.6(ci一時停止運用・stub分布拡張のスコープ確定)・ws-4 design(測定基盤)
- 参照先の既存資産(STATUS完了記録・実コード確認済み): load環境(compose.load.yaml・api2+worker2+nginx・
  ws-1)、シード生成器(backend/src/latch/testassets/generator.py・**--count 1万〜10万の決定的拡張済み**=
  ws-2「残余割付の件数スケール化」)、LLMスタブのレイテンシ注入(llm/stub.py・**固定ms・p50/p95分布化は
  本単位スコープ**=ws-1 design §2.6)、測定基盤(obs/・Prometheus pull型・Grafana・ws-4)、
  staging投入ハーネス(staging_tests/helpers_seed.py・API経由投入の前例)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

負荷条件下で性能5目標(10 §4.1)を測定して絶対値合格を証拠化し、Active Intent母集合を1万→5万→10万と
増やして総当たり検出(10 §4.2・02#5の本格検証)を実施する。加えて両試験の実行手順へ、測定精度のための
**ci常設環境の一時停止運用**(STATUS運用ルール6・ws-1 design §2.6の確定手順)を組み込む。G4完了条件
「非機能試験グリーン(性能5目標・総当たり検出)」の先行確認の本体。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | 負荷条件: **Intent保存API 50 rps・Match Event 100イベント/秒のバースト**(04のMVP規模想定のピーク3倍)。Jevはレイテンシ注入(**p50 2秒/p95 5秒** — 第一候補の公称70〜500msではなく**フォールバックLLM切替時のレイテンシ上限を検証する条件**)・Parserはレイテンシ注入(**p50 1秒/p95 2.5秒**)で実運用を模擬 | 10 §4.1 |
| 2 | 性能5目標(絶対値合格): ①Intent保存 p95 500ms ②Parser構造化プレビュー p95 3秒・p99 8秒 ③Candidate Retrieval p95 1秒(Layer 1〜2・トレースID紐付け) ④初期LATCH判定 p95 10秒(**Event発火(active化の保存コミット)から通知記録まで**・初回投入Event〔active作成またはdraft→active化〕経由の初回提案対象・更新Event経由の再評価は別計測) ⑤通知は判定後5秒以内(LATCH Engine確定からFCM記録まで) | 10 §4.1 |
| 3 | 層別予算のトレース分離: **Embedding ≤2秒 / Layer 1〜3 ≤1秒 / Jev ≤5秒 / Layer 5+通知 ≤2秒**のどの区間が予算を超えたか特定できること。初期判定10秒超過時の打ち手はこの分離が前提 | 10 §4.1・12 §7 |
| 4 | 総当たり検出: Active Intent数を**1万→5万→10万**と段階的に増やし、各段階で**同一のイベント注入(新規Intent作成100件)**。観測は1イベントあたりのVector Retrieval件数・判定レイテンシ・Jev実行回数。合格=**処理件数とレイテンシがIntent数に応じて急増しない(イベント数比例の範囲)**・**Vector Retrieval件数がK_v=50以内でIntent数と無関係に安定** | 10 §4.2 |
| 5 | load環境: 隔離環境(別プロジェクトlatch-load・28000系ポート・独立DB/Redis)。**Workerオートスケールは手動scale読み替え(台数の段階変更=stop/start)と確定済み** | 10 §1・ws-1 design §2.3 |
| 6 | スタブは決定的: LLM呼び出しはLLM Gatewayのテストモード(記録済み応答スタブ・レイテンシ注入付き)で**決定的にする**。乱数を持ち込まない | 10 §1 |
| 7 | 証拠: 試験ID・実施日・**環境**・入力条件・合否とともにdocs/testassets/results/へ保管。非機能試験はリリース前に1回 | 10 §5 |
| 8 | 測定はレイテンシ分布による(Cloud Monitoring)。ローカル代替=Prometheus+histogram(ws-4で承認済み=G4時確認事項2) | 04 §7・G4時確認事項2 |
| 9 | 負荷試験測定中のci一時停止: 手順=**ci停止(make down・ボリューム保持)→load環境で試験→load-down→ci復帰(make up)**。同一ホストのリソース競合が絶対値合格へノイズを乗せるため | STATUS運用ルール6・ws-1 design §2.6 |
| 10 | レート制限(負荷用ユーザー設計の制約): Active 5件・作成20件/日・更新6回/時・**API 60req/分/ユーザー** | 08 §5.4 |
| 11 | 時刻参照はすべてClock経由・テスト環境はClock制御注入可(load composeは`LATCH_CLOCK_MODE: controlled`)。K_v・K_c・K_j等の運用パラメータはゲート回避のために変えない | 10 §1・04 §5・12 §7 |

### 1.3 実装の接続点(既存コード・実測)

| 素材 | 現状 | 接続点 |
|---|---|---|
| obs/metrics.py(ws-4) | HTTPヒストグラム(method+pathテンプレート)・`retrieval_vector_items`(bucket 1〜50・**02#5検証素材とコメント明記**)・`jev_executions_total`/`jev_latency_seconds`(provider別)・`event_queue_pending`等。**区間時間系のヒストグラムはLayer別に存在しない** | 性能測定の主系統。欠落区間(Embedding/Retrieval/L5+通知/E2E/通知)のヒストグラム追加 |
| llm/stub.py `_delay_ms` | 固定ms(docstringに**「p50/p95分布版(M4)への差し替えはこの1関数で完結する」**と明記)。env=llm_stub_delay_{parser,embedding,jev}_ms。gateway.pyのstub構築点2箇所が渡す | 分布化の差し替え面はこの1関数+構築引数 |
| worker/main.py | 第2段チェーン=`_kick_jev`→`_run_post_retrieval`(group→jev→latch→finalize直列)・`observe_retrieval`(L1通過件数+Vector件数)を_run_matching/_run_direct_pipelineの両経路で呼出済み。Pub/Sub受信は`run_coroutine_threadsafe`(**コルーチン投稿後すぐ戻る=メッセージ並行処理**。LLM待ち・DB待ちで並行消化する) | Retrieval区間ヒストグラムはobserve_retrieval隣へ。E2E・L5区間はlatch_engine側。並行モデルの記録を手順冒頭の観察事項とする |
| worker/embedding.py | handle(LLM込みの処理)。時間観測なし | Embedding区間ヒストグラムの挿入点 |
| worker/matching/latch_engine.py | handle(提案確定→notifications先行書込み〔tx内〕→コミット直後にpush送信)・observe_latch_score呼出済み。起点読取でIntent行を参照 | L5+通知区間・**初期判定E2E**・通知区間の観測点。E2E起点=**投入経路別に決定**(active作成=created_at・draft→active化=active化コミット時刻・§2.3) |
| prometheus.yml + compose.dashboard.yaml | stagingのcomposeネットワーク(latch-staging_default)参加でコンテナ名直結scrape。**load targetsは未接続 — 「load環境(28000系)のtargetはws-6が追加する」をdashboard composeコメントに予約済み**(ws-4実装でhost-gateway経由は127.0.0.1バインドポートへ届かない実測あり) | 本単位でload targets追加+loadネットワーク参加(§2.6) |
| testassets/generator.py | --count変更時も決定的(1万〜10万が同一コード経路=ws-2実装記録)。YAML(users+intents+aggregate期待値)を出力 | 総当たり母集合の生成源(§2.4) |
| worker/embedding_text.py + llm/stub.py `_stub_vector` | 正規化テキスト生成規律・入力テキスト由来の決定的768次元ベクトル | 母集合のembedding直書きに再利用(§2.4) |
| staging_tests/helpers_seed.py | API経由投入(users→consent→intents)の前例。consent無しは422 CONSENT_REQUIRED(ws-5) | 負荷用ユーザーの事前作成手順(API経由=正規経路)の型(§2.5) |
| make staging-token / .staging/keys | テストJWT発行(load composeは同一keysをroマウント=ws-1) | 注入器の認証材料 |

## 2. 実装方式の選択肢と推奨

### 2.1 負荷注入器 — 自作asyncio注入器(推奨)

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A. 自作asyncio注入器(推奨)** | backend/src/latch/loadgen/へ新設。httpx(既存依存>=0.27・追加ゼロ)による**時刻駆動スケジューラ**: 応答完了を待たずに次の送出時刻を決めるオープンモデル(k6のconstant-arrival-rateと同型)。50rps定常と100件バーストを1コマンドのシナリオで実行・クライアント側応答時間も参考値として記録 | **測定の主系統はサーバ側Prometheusヒストグラム**(引用#8・G4時確認事項1)であり、クライアントは「決められたレートで投げる注入器」で足りる。既存Python資産(鍵・token発行・consent・シード・パブリッシャー)と同一環境で動き・証拠YAML出力も既存ハーネス(staging_tests_obs/obs_results.py)と同型にできる。応答を待たない設計で**coordinated omission**(応答遅延が投入間隔を遅らせて測定を甘くする現象)を原理回避 |
| B. k6(Grafana) | **context7一次確認済み**(/grafana/k6-docs・2026-10-04): `constant-arrival-rate` executorがオープンモデルの固定到着率を一次機能として提供(rate/timeUnitで直接rps指定)。docker image(grafana/k6)で導入 | rps制御の正確性とクライアント側計測の実績は強い。ただしシナリオはJS、既存資産はPython(users登録・consent・token発行・母集合投入・証拠YAML)で**2言語化**が発生。docker image持ち込み。この規模(50rps)では能力過剰。不採用 |
| C. Locust | **context7一次確認済み**(/locustio/locust・2026-10-04): wait_time=constant_pacingは**ユーザーごとのペーシング**で、目標rps=ユーザー数÷ペーシング秒の合成。オープンな固定到着率の一次機能なし(クローズドモデル寄り) | pip依存追加(locust+gevent)が重い・rps制御が間接的。不採用 |

**採用: 案A。** 決定的要素は2つ。第一に、本試験の合格判定は絶対値(10 §4.1)で、その測定器はws-4で
構築済みのサーバ側ヒストグラムである。第二に、注入対象の操作(users・consent・Intent作成)が既存の
Python資産と密接に結びつくため、別言語・別実行環境に置く橋渡しコストが採用メリットを上回る。

### 2.2 レイテンシ注入の分布化 — 決定的ハッシュ振り分け(推奨)

**論点の所在。** 10 §4.1は注入値をp50とp95の2点で規定する(引用#1)。p50値のみの一様注入ではp95観測が
再現されず、p95値のみでは全件が最悪値になる(過大な注入)。分布を再現しつつ**決定性**(引用#6・10 §1
「スタブで決定的にする」)を保つ必要がある。

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A. 決定的ハッシュ振り分け(推奨)** | 呼び出し単位の既存入力(正規化テキスト・intent_id等)のハッシュから決定的に区間を選ぶ: **50%→p50値・44%→中間値(線形補間)・6%→p95値**。上位グループ(p95値)がnearest-rank定義の95%ランク(ceil(0.95N)番目の標本)を含むよう下位2グループの合計を94%に抑えた割付で・分布の50/95分位点が指定値どおりになる(50番目=p50値・95番目=p95値)。下位合計が95%を跨ぐ割付(例: 45%/5%の旧案)では95番目が中間値になり観測p95が中間値へ落ちるため採らない。同入力→同遅延で再現する | 10 §1の決定性を保ったままp50/p95を同時に再現する。パラメータは新env(llm_stub_latency_{parser,jev}_{p50,p95}_ms計4本)へ。既存固定ms env3本は**無変更で維持**(既存unit試験・ci挙動の互換)・分布envが非0のときのみ分布側へ切替。Prometheus側の観測も、層別・E2Eヒストグラムの**bucket境界にp50/p95注入値を含める**ことでhistogram_quantileの補間推定が指定値と一致する(§2.3) |
| B. 乱数による分布 | Python乱数で50%/5%に振る | **10 §1の決定性を破る**(同一入力→同一応答が崩れ・失敗再現ができない)。不採用 |
| C. 固定値2本の時間切替 | 一定期間でp50値⇔p95値を切り替える | 分位点が「観測窓内の時間配分」に依存し、p95位置が設定値と一致する保証がない。不採用 |

**採用: 案A。** embedding系統は10 §4.1に注入規定がない(層別予算の観測は対象だが注入条件は未規定)ため
**分布注入を作らない(YAGNI)**。既存固定ms env(llm_stub_delay_embedding_ms含む)で個別に注入可能なまま
残る。適用対象はStubLLMのjev系統=第一候補経路とフォールバックstub経路の両方が同一クラスを通るため
**Jev判定の実行全経路に分布がかかる**(引用#1の「Jevの判定レイテンシ上限を模擬」と整合)。既存envとの
優先順位は「分布env非0 > 固定ms」とし、load compose既定は**固定ms=0・分布envのみ設定**とする
(本番値は10 §4.1どおりParser 1000/2500・Jev 2000/5000)。

### 2.3 層別・E2E・通知の計測 — Prometheusヒストグラム追加(推奨)

**論点の所在。** 引用#3(層別予算のトレース分離)に対し、ローカル代替基盤での「分離」の実現方式を決める。

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A. ヒストグラム追加(推奨)** | obs/metrics.pyへ新3本: ①`pipeline_layer_seconds`(label layer=embedding/retrieval/proposal — 層別予算4区間のうちJevは既存jev_latency_secondsで観測済み) ②`latch_initial_e2e_seconds`(初期判定E2E) ③`notification_latency_seconds`(提案確定→push記録)。計測はClock経由((clock.now()-t0)・jev.pyと同型。ControlledClockは実時間レートで進むので時間計測として正しい) | 引用#8の「測定はレイテンシ分布」に忠実(p95/p99がPrometheusで直接出る)。04 §3のOTLPトレースとの差分は**G4時確認事項2と同型のローカル代替差分**(ws-4で承認済みの延長・G4時にあわせて確認)。実装は既存observe_*と同型の横流しで・本体ロジック無変更 |
| B. SQL集計(KpiJob/kpi_dailyへp95を書く) | PostGIS percentile_contでcreated_at起点のE2Eを集計 | kpi_dailyはKPI観測(日次・分母分子)の器であり負荷区間の分布観察に向かない。Prometheus単一基盤(引用#8)から外れる。不採用 |
| C. OTelトレースの持ち込み | 本番確定値(OTLP)に最も近い | Collector・SDK初期化の持ち込み=ローカル代替の過剰構成(ws-4 §2.1で案B不採用と同判断)。不採用 |

**採用: 案A。** 観測の挿入点は次のとおり(いずれも観測が本体を壊さない既存規律どおり)。

- **Embedding区間**: worker/embedding.py handle冒頭〜末尾(LLM込み)
- **Retrieval区間(Layer 1〜3)**: worker/main.pyの`run_candidate_retrieval`前後(`observe_retrieval`呼出し隣。Event経路とBucket/catch-up直接経路の両方)
- **Layer 5+通知区間**: latch_engine.handleの一連処理(提案確定〜push送信まで)
- **初期判定E2E**: latch_engineの提案確定点で`observe(通知書込時刻 − 起点時刻)`。**起点は10 §4.1 v0.3
  どおり「Event発火(active化の保存コミット)」で・投入経路別に列を使い分ける**: active作成=
  intents.created_at(=保存コミット)・draft→active化=**active化コミット時刻**(draft作成時刻を起点に
  すると起草時間がE2Eへ混入し証拠が無意味になる。候補はupdated_at — embedding書込み等の後続処理で
  更新されない列であることを実装の既存起点読取と突合して計画書で確定)。ヒストグラムにはlabel
  route=create/activateを付け経路別の分布として証拠化する(§2.5の区別記録)。観測対象の絞り込みは
  **「初回投入Event(created種 — active作成とdraft→active化を含む〔ws-4設計§1.3・intents/service.py:742
  の実装記録〕)経由の初回提案」**: (a)初回投入Event処理チェーン内の直接キック時のみフラグを立てる
  (Bucket/catch-up再評価経路では立てない)(b)当該Intentに対する提案が初回であること。更新Event
  (version>1)由来・再評価由来は対象外(引用#2の「更新Event経由の再評価は別計測」)
- **通知区間**: 提案確定→push送信記録(StubPushSenderはログ記録のみなので区間はごく小さくなる —
  測定の意図は「この区間の構造上の大きさを測り実FCM時の上限を推定できる状態」にすること)
- **HTTPヒストグラムのbucket拡張**: 目標②のp99 8秒判定のため既存`http_request_duration_seconds`の
  bucketに8秒近傍を追加(prometheus-clientのhistogram bucketはメトリクス単位で定義可・追加は既存
  クエリに破壊なし。ダッシュボード4枚のクエリへの影響は計画書で確認)。保存p95 500ms・parse p95 3秒の
  解像度も同時に整える。新設の層別・E2Eヒストグラムのbucket境界は**§2.2のp50/p95注入値を含む位置**
  に置き、histogram_quantileの補間推定が注入値と一致するようにする

### 2.4 総当たりの母集合投入 — DB直接投入ローダー(推奨)

**論点の所在。** Active Intent 1万〜10万件の母集合の投入経路。10 §4.2は「Active Intent数を増加させる」
とだけ規定し経路は指定しない。

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A. DB直接投入ローダー(推奨)** | loadgenのサブコマンド: generator.py(--count拡張済み)の決定的YAMLを読み・users/intentsをバルクINSERT・**embedding列へ直書き**(正規化テキストはembedding_text.py経由・ベクトルは`_stub_vector`で決定的生成)・active条件(status='active'・expires_at未来・embedding非null・LAYER1_WHEREどおり)を満たす行を作る。**Event発行なし** | (a)API経由は制限(引用#10: Active 5件/ユーザー・作成20件/日・60req/分)で10万件投入が実用にならない — 制限は源流保護の本体(08 §5.4)であり試験用に外せない (b)母集合投入をパイプラインに通すと投入区間自体の処理が観測に混ざり・Jevカウンタ(D-16・予算)を消費する (c)決定的生成器と同一の母集合を再現でき・段階間の比較が公平になる。embedding_model列には既存のスタブ系記録と同じ値を書く |
| B. API経由(ws-3 seed_all方式) | 正規経路を全部通る | 300件規模の実績はあるが**10万件は実用外**(制限・時間)・投入中にパイプラインがフル稼働して観測を汚す。300件以下の少量用途なら選べるが本試験では不採用 |

**採用: 案A。** 母集合は「検索対象として存在するActive Intent」であればよく、その生成経路まで
Event経路に一致させる必要はない(観測対象の新規100件だけが正規のEvent経路を通る)。投入時間
(10万件INSERT+HNSW逐次構築)は測定対象外だが手順に実測記録を残す(運用メモ)。母集合の分布は
既存生成器の分布(10 §2・7地名分散)に従い、特定時間帯・エリアへの過度な密集を作らない。

### 2.5 Eventバースト・負荷用ユーザー — API作成バースト(字義どおり)

- **総当たりの注入(引用#4)**: 「新規Intent作成100件」の字義どおり**API作成**で実現(100件の作成が
  100件のcreated Eventを発行=正規経路でパイプラインを通る)。作成ユーザーは母集合ユーザーから
  1ユーザー1件(Active残枠≥1・作成20件/日・60req/分すべて安全に収まる)。1秒相当の短時間で100件を
  投げる(バースト)。3段階とも**同一の注入条件**にする(段階比較の前提)。
  疑似publish CLIの直発行(created/version上げ)は既知version再受信やdebounce統合の挙動が絡み
  「新規作成」の字義から外れるため使わない。
- **性能試験の負荷構成(引用#1)**: 1測定区間内に①**定常50rps区間**(POST /v1/intents・60秒想定=
  3000件)と②**バースト区間**(100件を約1秒)を配置し、パイプライン側は区間終了後の収束待ち
  (消化完了)まで観察する。分布(⑤系統すべて)は区間全体から採取する。
- **負荷用ユーザー**: perf区間の件数÷制限に合わせて**users+consentをAPI経由で事前作成**
  (1000人想定=3000件を1人3件以内に収める。引用#10の3制限すべてに抵触しない配分)。
  helpers_seed.pyの投入型(users→consent)を踏襲する。
- **draft→active化の経路**: 引用#2は初回投入=「active作成またはdraft→active化」の双方を測定対象に
  含め、実施時に投入経路を区別して記録することを求める。注入器はactive作成を主経路とし・
  draft→active化を区間末尾の少数(例: 10件)で追加して**E2Eヒストグラムのroute label(create/activate)
  で区別**する。draft→active分のE2E起点はactive化コミット時刻(§2.3)で・下書き作成時刻の起草時間を
  含めない。

### 2.6 load環境のPrometheus接続 — staging同型のネットワーク参加(推奨)

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A. staging同型のネットワーク参加(推奨)** | prometheus.ymlへload targets追記(api1:8000・api2:8000・worker1:9100・worker2:9100)+compose.dashboard.yamlへexternal network(latch-load_default)参加の追記。dashboard composeコメントの予約どおり | ws-4で実測済みの方式の延長・dashboardの承認条件(**127.0.0.1明示バインド**維持)と衝突しない。制約としてexternal networkはload-up後でないと存在しないため**起動順序を手順化**(load-up→dashboard-up。dashboard稼働中にload-upした場合はprometheus再起動〔dashboard-down/up〕を手順に含める) |
| B. prometheusをnetwork_mode: hostにして127.0.0.1公開ポートへ直scrape | 起動順序の制約が消える(loadのポートは既に127.0.0.1公開) | Linux専用機能で・stagingのコンテナ名直結と方式が混在し・prometheus.ymlのtarget体系が2系統化する。不採用 |
| C. loadのportsを0.0.0.0公開 | — | **承認条件(127.0.0.1明示バインド)に反する**。不採用 |

**採用: 案A。** 起動順序の制約は、本単位の試験が「手順実行」であること(§4.2)で吸収する。

### 2.7 性能測定の区間設計と合格の扱い

- **5目標は負荷条件下で同時採取**(引用#1の字義: 負荷条件を設定したうえで実運用を模擬する)。
  分布が目標を超えた場合は、層別ヒストグラム(§2.3)で超過区間を特定する — これが12 §7の打ち手
  (「層別予算をトレースで分離し、超過区間を特定してから対処」)の実行そのもの。対処後の再測・
  基準見直しの判断は10 §6(元の設計文書の改版を経て反映)の規定どおり。
- **総当たりはレイテンシ注入なしで実施**(引用#1は10 §4.1の性能試験の条件であり・10 §4.2に注入規定は
  ない)。注入ありでは100イベント×最大8ペア×p95 5秒のJevで消化に数十分級かかり観測区間が非実用的に
  なる。総当たりの観測対象(Vector件数・判定レイテンシ・Jev実行回数)は注入なしで採取でき・E2E系は
  性能試験区間で採取済みである。
- **「急増しない」の量的線**: 引用#4は定性基準(docsに量的線なし)。証拠は段階比較表
  (1万/5万/10万×Vector件数分布・Retrieval p95・L1通過率・イベントあたりJev回数)とし、**Vector件数
  ≤50の機械判定**と・増幅率の参考値(段階5倍に対する指標の増幅がおおむね整数倍以内=イベント比例の
  範囲)を添える。**正式判定はG4の人間側**(量の線を新設するなら10 §6のdocs改版が前提)。
- **実施環境の記録**: 引用#7の証拠形式に「環境」欄があるため、ローカルdocker(ホスト・CPU・メモリの
  申告込み)を証拠YAMLへ記録する。絶対値合格の解釈・判定はG4の人間側がこの記録を前提にする。

## 3. ファイル構成

### 3.1 作るもの

| ファイル | 内容 |
|---|---|
| `backend/src/latch/loadgen/__init__.py` | 空パッケージ |
| `backend/src/latch/loadgen/__main__.py` | CLI: `python -m latch.loadgen users`(負荷用ユーザー事前作成)/`perf`(定常50rps+バースト+収束待ち)/`bruteforce`(母集合投入+100件注入)。宛先はenv上書き(load宛28000系。既存規律) |
| `backend/src/latch/loadgen/schedule.py` | 時刻駆動スケジューラ(オープンモデル・決定的な送出時刻列生成。50rps定常とバーストの2種) |
| `backend/src/latch/loadgen/client.py` | httpxクライアント(users・consent・token発行〔既存CLI関数の呼出し〕・Intent作成)・クライアント側応答時間の記録 |
| `backend/src/latch/loadgen/massload.py` | 母集合投入(YAML→users/intentsバルクINSERT+embedding直書き) |
| `backend/src/latch/loadgen/collect.py` | 証拠採取(Prometheus query・段階比較表組み立て・証拠YAML出力。obs_results.py同型) |
| `backend/src/latch/llm/stub.py` | `_delay_ms`の分布化(決定的ハッシュ振り分け。§2.2案A) |
| `backend/src/latch/llm/gateway.py` | stub構築点2箇所へ新遅延引数の追記 |
| `backend/src/latch/settings.py` | 追記: 遅延分布env 4本(llm_stub_latency_parser_p50_ms/p95_ms・llm_stub_latency_jev_p50_ms/p95_ms。既定0=無効) |
| `backend/src/latch/obs/metrics.py` | 追加3本+observe関数+httpヒストグラムのbucket拡張(§2.3) |
| `backend/src/latch/worker/main.py` | 追加: Retrieval区間のobserve(`observe_retrieval`隣・両経路) |
| `backend/src/latch/worker/embedding.py` | 追加: Embedding区間のobserve |
| `backend/src/latch/worker/matching/latch_engine.py` | 追加: L5+通知区間・初期判定E2E(条件つき)・通知区間のobserve |
| `compose.dashboard.yaml` + `docker/prometheus/prometheus.yml` | load targets追加+loadネットワーク参加(§2.6) |
| `compose.load.yaml` | 追記: 遅延分布envをapi-common・worker-commonのenvironmentへ(値は**試験実行時に指定**できるよう既定0で記載とするか・10 §4.1値を既定とするかは計画書で確定 — 手順の切替手順込み) |
| `Makefile` | 追記: loadperf・loadbf・loadmass・loadusers(注入器のラップ。ci停止→load実行→ci復帰は**手順文書側**に記載し、ターゲット1発でciまで止めない〔停止は運用判断を含むため明示手順とする〕) |
| `backend/tests/unit/test_loadgen_*.py` | スケジューラ・massload・証拠採取のunit(§4) |
| `backend/tests/unit/test_llm_stub_latency.py` | 遅延分布の決定性・分位点・優先順位 |
| `backend/tests/unit/` 既存追随 | test_llm_factory(gateway構築点)・test_settings(env追加)・test_obs_metrics(メトリクス登録)・test_worker系(observe挿入の無傷確認) |
| `docs/plans/M4/ws-6-report.md` | 報告書(手順証拠+段階比較表+証拠YAMLへのリンク) |

### 3.2 触らないもの

- `compose.yaml`(ci)・`compose.staging.yaml`・ci環境のMakefileターゲット
- **パイプライン本体のロジック**(stage1・各Engine・guard・sweeper等。観測は横流しのみ)
- **既存固定遅延env(llm_stub_delay_*_ms)とStubLLMの応答契約**(既存試験・ci挙動の互換)
- JevCostGuard・CircuitBreaker・KpiJob・既存メトリクスの定義(既存name/bucketの変更はしない —
  httpヒストグラムは**bucket追加のみ**で既存bucket値を変えない)
- DBスキーマ(マイグレーションなし)・staging_tests系(ws-3/ws-4資産)・frontend・prototype
- docs本文(本単位でdocs改版なし・改版候補も§7に記載なし)

## 4. テスト方針

### 4.1 自動試験(unit・毎コミットの既存規律)

- **遅延分布(stub)**: 決定性(同一入力・同設定→同一遅延)・**全サンプル検証**(ハッシュ振り分けは決定的
  なので・パラメータ例p50=1000/p95=2500・割付50/44/6で全振り分けを列挙し**nearest-rankの50分位点
  =1000・95分位点=2500**(95番目の標本がp95値グループに含まれること込み)を機械検証)・
  優先順位(分布env非0>固定ms・両0は無遅延)・既存stub試験(test_llm_stub.py)の無傷
- **スケジューラ(loadgen)**: 送出時刻列の決定性(50rps×10秒=500件の時刻列・バースト100件=約1秒相当)・
  **オープンモデルの性質**(応答待ちが次の送出時刻を遅らせない=モッククライアントで応答遅延を模擬して
  送出間隔が保たれること)・スケジュールどおりの送出件数・エラー集計
- **massload**: 少量fixture DBでINSERT+embedding列の検証(正規化テキスト経由・active条件充足・
  Event発行ゼロ=match_events無増加)・生成器YAMLのusers/intents対応が崩れないこと
- **metrics**: 新規3本の登録・label・bucket・observe関数(test_obs_metricsの許可リスト規律に追随)・
  observeが本体例外を握って壊さないこと(既存observe_*と同型)
- **E2E観測の絞り込み**: version==1チェーンかつ初回提案のときだけobserve・再評価経路ではobserveしない
  (fixtureで両経路を比較)
- **settings/ピン追随**: 新env4本の追加でtest_settings・test_llm_factoryの機械ピンを追随
  (M1 ws-5/M2 ws-2の「機械的追従」前例どおり・期待値の意味変更時はBLOCKED)
- **arch test**: Clock経由時刻計測・geo配下import禁止等の既存規律でグリーン維持
- **integration(test-ci)**: observe挿入を既存integration試験が壊さないこと(test-ci 1664維持)。
  負荷試験・総当たりはtest-ciに含めない(実環境試験=4.2の手順証拠が本体)

### 4.2 環境実行手順(負荷試験の実行手順 — ci一時停止運用を組み込み)

> STATUS運用ルール6・ws-1 design §2.6の確定手順どおり。証拠はdocs/testassets/results/へ(10 §5形式:
> 試験ID・実施日・環境・入力条件・合否)。

1. **事前準備**: `make load-up`→`make migrate`(load宛)→geo-import→worker2台稼働確認・
   `python -m latch.loadgen users`で負荷用ユーザー1000人作成(users+consent・API経由)
2. **ci一時停止**: `make down`(ボリューム保持・ci常設を止める)。※この手順を踏む理由=同一ホストの
   CPU/ディスク競合が絶対値合格(p95等)へノイズを乗せるため
3. **観察環境**: `make dashboard-up`(load-up後が前提=§2.6の順序制約)・prometheusがload targetsを
   scrapeしたことを確認
4. **性能試験区間**: `make loadperf` — 定常50rps(60秒想定=3000件・active主経路+draft→active化10件
   で区別記録)+バースト100件+収束待ち・その間のPrometheus採取(5目標の分布+層別4区間+queue pending
   の推移)を`loadgen collect`で証拠YAML化。Client側応答時間は参考値として併記
5. **総当たり**: `make loadmass`で母集合1万投入→`make loadbf`(100件注入+収束待ち+採取)→
   母集合5万→…→10万→…の段階比較。**総当たり区間はレイテンシ注入なし**(§2.7)・Vector件数≤50は
   機械判定・段階比較表と増幅率参考値を証拠に
6. **片付け**: `make load-down`→**`make up`でci復帰**→ci常設稼働継続の確認(load環境とstaging・
   dashboardは独立プロジェクトのため通常時の共存可・運用ルール6の「load-down→ci復帰」どおり)
7. **報告**: ws-6-report.mdへ手順証拠・段階比較表・5目標の合否・実施環境(ホスト情報込み)を記録

実測パラメータ(区間秒数・件数・母集合投入時間)は手順実行時に記録し・手順書の初期値との差分は
報告書に残す。段階間のprometheus再起動(dashboard-down/up)を行う場合は採取を先に済ませる順序を
守る(seriesはprometheusのメモリに載るため)。

## 5. supervisor承認事項(草案 — 承認後に計画書化)

1. **負荷注入器=自作asyncio注入器**(§2.1)。k6/locustはcontext7一次確認済みで不採用
   (2言語化・依存追加・オープンモデル機能の不在)。依存追加はhttpx既存のみでゼロ
2. **レイテンシ注入の分布化=決定的ハッシュ振り分け**(§2.2)。割付は**50/44/6**(上位グループが
   nearest-rankの95%ランクを含む形 — 旧案45/5では観測p95が中間値へ落ちるため修正)。新env 4本
   (parser/jev×p50/p95)追加・既存固定ms env 3本は無変更維持・優先順位は「分布env非0>固定ms」。
   embedding系統は注入規定なしのため分布を作らない(YAGNI)
3. **追加メトリクス3本+httpヒストグラムのbucket追加**(§2.3)。「トレース分離」のローカル代替=
   Prometheusヒストグラムである旨はG4時確認事項2と同型の**ゲート時確認対象**(04 §3本番値への
   対する代替差分として記録をsupervisor側へ依頼)
4. **総当たり母集合=DB直接投入**(§2.4)。Event発行なし・embedding直書き。API経由不成立の根拠は
   レート制限(引用#10)と観測汚染
5. **load環境のPrometheus接続=staging同型のネットワーク参加**(§2.6)。dashboard composeの
   external network追加・起動順序の制約は手順化で吸収
6. **総当たりはレイテンシ注入なしで実施**(§2.7)。10 §4.2に注入規定なしの読み
7. **E2E観測の絞り込み規則**(§2.3): 初回投入Event(created種=active作成とdraft→active化を含む)チェーン
   かつ初回提案のみ。**起点は経路別**: active作成=intents.created_at・draft→active化=active化コミット
   時刻(起草時間の混入防止)。route label(create/activate)で経路別分布として証拠化。実装詳細
   (起点列の突合・チェーンフラグの受け渡し)は計画書で確定
8. **Makefileターゲットの範囲**(§3.1): ci停止/復帰はターゲットに組み込まず明示手順とする
   (運用判断を含む操作を自動化に隠さない)

## 6. 未解決の論点 — なし(記録事項のみ)

1. **「急増しない」の量的合格線はdocs未規定**(§2.7): 本設計は証拠の形式(段階比較表+増幅率参考値+
   Vector件数≤50の機械判定)を固定する。量的線の新設を必要とする場合は10 §6のdocs改版が前提
2. **KpiJobへの恒久値化はしない**: 初期判定E2E等のp95をkpi_dailyへ書く設計は行わない(kpi_dailyは
   09 §2.2のKPIの器であり・負荷試験の証拠はPrometheus+証拠YAMLで完結する)。将来の連続監視に
   需要が出た時点で再検討
3. **worker並行モデルの観察**(§1.3): Pub/Sub受信の並行性(run_coroutine_threadsafe後のasyncio消化)は
   コード構造上並行だが・実効並行度はDB接続プールとCPUに依存する。手順4の冒頭でpending推移を観察し
   ボトルネックがWorker側かキュー側かを層別ヒストグラムとあわせて読める状態にする(性能試験の
   分析材料・実装変更はしない)
