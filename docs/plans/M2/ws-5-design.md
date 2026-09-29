# M2 ws-5(Layer 4 Jev)設計メモ

- 作成: 2026-09-29(agent1)
- 前提: M2 ws-1〜ws-4 マージ済み(0bb6a2d・059aece・0c45aa7・90a83d4+296e9fe・マージ後main test-ci 818 passed)。並走単位なし(実行waveはws-5が単独)
- 参照仕様: 06 §5・§8(D-15・D-24)・§9 / 07 v0.5 §1・§4 / 04 §4 D-16・§5 / 05 §2 / 10 §1・§4.1・§4.6 / 02 §4(#11)
- 外部SDK/API仕様はcontext7・claude-apiスキルで一次確認(2026-09-29・§1.3に 出典つきで記録。
  docs/reviews/jev-systemone-revision.md(2026-09-28)からの変化有無も§1.3末尾で照合)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

4つの部品を実装する。

1. **LLM GatewayへのSystem One IF追加**(06 §5・07 §4): `judge_pair` を実装化する —
   第一候補 TypeSafe Jev(`jev-1.13.0` 固定)+フォールバックLLM(Anthropic Sonnet 5)。
   429・529・timeoutでフォールバックへ切替(SDK backoff無効化・再試行なし)
2. **Layer 4実行部(JevWorker)**: K_j=8の選択(1対1最低4回保証の枠組み)・JevCostGuard呼び出し
   (ws-4引継ぎ契約どおり)・`jev_result` 記録(provider/model)・**同一評価世代スキップとH再検証**(FR-07)
3. **StubJevのSystem One互換形式化**(10 §1 v0.4): スタブ応答を answers 形式へ更新
4. **実APIスモーク資産**(`make jev-smoke`): 実行はスーパーバイザー検証時のみ(課金のため設計・実装段階では実行しない)

マイグレーションは **0003を1本追加**(`match_candidates.skip_reason` — §2.7)。依存追加は
`typesafe-sdk` 1件(§5-3。anthropic・redis等は既存)。実行waveが単独のため共有ci-dbの
alembic_version取り合いは計画上発生しない(運用ルール1・2は念のため報告書へ明記)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | 呼び出し条件は「Layer 3通過の上位候補」。1候補につき1回のAPI呼び出しで両方向(would_a_accept_b / would_b_accept_a)を判定し、これがD-16の「1実行回数」。フォールバックLLMの呼び出しも同一の1実行回数に計上 | 06 §5・04 §4 D-16 |
| 2 | 呼び出先はSystem Oneモデル「TypeSafe Jev」(**jev-1.13.0固定・エイリアス不使用**)。429・529・timeoutの際はフォールバックLLM(Anthropic Claude API・Sonnet 5)へ切替。**SDK既定のbackoff retryは無効化・再試行なし**。モデル更新時はバージョンを上げて日本語評価を再実行してから切替 | 06 §5・07 §4・07 §1 |
| 3 | 入力は両Intentの構造化データの正規化テキスト(soft constraintsとD-04降格のNG条件を含む)でraw_textは送らない。visibility・notification_levelは正規化テキストに含めない。stateは正規化テキスト2件をJSONオブジェクト(`intent_a`/`intent_b`キー)で運ぶ | 07 §4・06 §5・01 §21 |
| 4 | 7軸の写像: would_a_accept_b・would_b_accept_a・latent_yes→**noul**、purpose_fit・mood_fit・timing_fit・social_fit→**score(5段階・0〜4)**。7質問は1リクエストで送信。質問の構造・型・キー名が確定値で、instructions・criteria文言の最終確定はM2の日本語評価(09 §4) | 07 §4 |
| 5 | 出力検証: answersの同キー性・noul値域[0,1]・score値域[0,4]を防御的に検証し、失敗は再試行せず縮退(D-15: skipped保留)へ落とす(実装不整合として扱う)。応答のmodelフィールドをjev_resultへ記録 | 07 §4 |
| 6 | スコア計算はLayer 5の担い(MutualScore=min・L=H×MutualScore×C)。5軸は判定モデル内部の判断材料でMutualScoreに直接寄与させない。scoreの値(0〜4)は正規化して[0,1]の値をjev_resultへ保存。confidenceは5軸の分析用として保存しwould_*には保存しない(noulにconfidenceは付かない。フォールバックLLMはconfidenceを返さないためnull) | 07 §4 |
| 7 | **reasonは廃止**(第一候補・フォールバック双方で一貫して生成しない)。jev_resultに自由文は格納しない | 07 §4・05 §2・08 §2.5 |
| 8 | jev_result格納形: `would_a_accept_b` / `would_b_accept_a`(noul確率0〜1)/ `jev_5axis`(5軸の正規化値0〜1とconfidence)/ `provider`("typesafe_jev" / "fallback_llm")/ `model`(第一候補時の応答バージョンID・フォールバック時はnull)。同一バージョン組の再評価では再実行せず保持 | 05 §2 |
| 9 | フォールバック切替表: 429→即座に切替(backoff再試行なし)/ 529→同上 / timeout 6秒→同上 / フォールバックLLMの失敗(timeout・429・5xx・出力検証失敗)→縮退(skipped保留・circuit breaker開放対象)。フォールバックLLMは同じstate・同じ7質問に対し、キー同一・値はnoul→[0,1]確率・score→[0,4]値のJSONをstructured outputで返す。Layer 5以降は判定経路を意識しない | 07 §4 |
| 10 | timeoutは第一候補・フォールバックとも6秒(層別予算Jev ≤5秒+1秒吸収)。双方で再試行を設けないことで遅延とコストの二重払いを構造的に排除。circuit breakerの切替先はフォールバックLLM(本体実装はws-8) | 07 §1・06 §8 D-15 |
| 11 | K_j=8は「1イベント処理あたりのJev実行回数の上限」(回数ベース)。配分規則: (1)1対1に最低4回保証(K_c候補をcheap_score降順で上位4件) (2)残り最大4回をグループ集合の構成ペアへcheap_score降順で割当 (3)全ペア判定が揃わない集合は提案化せずgroup_candidates.status=candidateで保持し未判定ペアを次の再評価のJev予算最優先 (4)割当順序は1対1最低枠確保→継続評価(優先)→新規グループ。グループ由来ペアが存在しないイベントでは残り枠を1対1候補のcheap_score順に繰り上げ(1対1の実効上限はK_j=8) | 06 §5・06 §8 D-24 |
| 12 | 同一評価世代スキップ(FR-07): 再評価の際、対象ペアが(a)同一バージョン組かつ(b)jev_result既存在の場合、Jevを再実行せずjev_resultを保持したままLayer 1の再検証(Hの再計算)のみ行う。H再検証でHard Constraint不成立となったペアはstatus=closedへ閉じる。Layer 2〜3の順位変動で新たに上位に入ってきたペアは新しいペアとして通常どおりJev実行対象 | 06 §5 |
| 13 | Jev予算3層(1Intent 40回/日・1ユーザー120回/日・再評価頻度30分)とD-16回数上限(日次30,000・月次600,000・80% alert+内訳)はws-4実装済み。Guard呼び出し契約: `request_execution(intent_id, user_id)` をLayer 4実行直前に呼ぶ・deny時はmatch_candidates.status=skipped遷移と理由記録・`record_execution(provider)` は実際のAPI呼び出しベースで切替完了側(ws-5)が呼ぶ・INCR先行(denyも消費) | 06 §5・04 §4 D-16・04 §5・ws-4報告書§5-6・ws-4設計§2.4 |
| 14 | 縮退(D-15): (1)第一候補とフォールバックLLMの双方のAPI障害 (2)D-16回数上限到達 (3)Jev予算・頻度制限由来のスキップ — のいずれかで提案は見送り・候補は保留(status=skipped)。保留は日次リセット後や障害回復後のMatch Event・再評価経路で再評価される。単発の429・529・timeoutは第一候補内の切替で吸収し、継続障害のみcircuit breaker(ws-8) | 06 §8 D-15 |
| 15 | StubJevの応答形式はSystem One互換のanswers形式: model(バージョンID)+answers(質問と同キー。would_*はnoul値[0,1]・5軸はscore値[0,4]とconfidence)+usage。実装は第一候補と同じanswers取り出し経路を通す。フォールバック経路のスタブも同形式でconfidence=null。スタブの値は決定的に固定 | 10 §1 |
| 16 | 層別予算: 初期LATCH判定p95 10秒のうちJev ≤5秒。フォールバック切替時のレイテンシ注入(p50 2秒/p95 5秒)は10 §4.1が検証対象(ws-8の障害注入で使用) | 06 §1・10 §4.1 |
| 17 | K上限の裏付け: 密集配置で Jev ≤8回 が記録で守られ、切り詰めが決定的(同一入力2回実行で同一結果)。02#11(1イベントあたりJev実行回数が上限を超えない)の観測はLayer 4実装後 | 10 §4.6・02 §4 #11 |
| 18 | 送信先と送信記録: Jev系統の送信先はTypeSafe AI(api.typesafe.ai・米国)・フォールバック時はAnthropic Claude API。LLM Gatewayが送信先・データ種別・時刻を記録(既存のlatch.llm.send構造化ログ)。フォールバック送信も同一記録経路 | 07 §4・08 §3 |
| 19 | Layer 1〜5はembedding_completedを起点にのみ走る(作成・更新Eventの処理はEmbedding要求のキックまで)。処理失敗は5回再試行→隔離(stage1の既存経路) | 06 §1・§9 |
| 20 | 日本語リスク: TypeSafe Jevの公式言語サポートは英語主訓練・CJKは同等でない。実装側の軽減として算術・日付はコード側で行い正規化テキストは最小限の構成を保つ。jev_resultのprovider・model・confidence記録で第一候補とフォールバックを分離観測。精度評価はG2(09 §4) | 07 §4 |

### 1.3 context7等で一次確認した外部SDK/APIの事実(2026-09-29)

**TypeSafe(System One・Jev)** — 取得手段: context7 `/websites/typesafe_ai`
(docs.typesafe.ai の api・models・sdk/python/api・introduction/quickstart 各頁)。

| # | 事実 | 出典 |
|---|---|---|
| T1 | API: `POST https://api.typesafe.ai/v1/systemone`・Bearer認証・Content-Type: application/json。リクエストは state(文字列/JSONオブジェクト/配列)+ model + questions(質問名→定義マップ)。キーはLATCH側で命名し応答は同キー | docs.typesafe.ai/api・introduction/quickstart |
| T2 | 質問型3種: noul(instructionsのみ)/ choice(criteriaはマップ)/ **score(criteriaは配列・2〜10段階)**。instructionsは文字列/オブジェクト/配列可(バッククォートでstate参照を分離) | 同上 |
| T3 | 応答: `model`(応答したバージョンID)+ `answers`(同キー)+ `usage`(input_tokens/output_tokens)。noul応答は `{"type":"noul","noul":0.999}` の数値確率・score応答は `{"type":"score","score":1.035,"legend":{...},"confidence":0.842}`(scoreは確率加重の**float値**)・choice応答はchoice+probabilities+confidence | docs.typesafe.ai/introduction/quickstart(応答例全文) |
| T4 | Python SDK: `typesafe_sdk` パッケージ。`AsyncTypeSafeClient`(async context manager・`await client.system_one(state, questions, model, ...)`)と同期 `TypeSafeClient`。コンストラクタは api_key(環境変数TYPESAFE_API_KEYより明示指定優先)・base_url・timeout・retry/retry_policy | docs.typesafe.ai/sdk/python/api/clients/{sync,async}/client・primitives |
| T5 | **retry無効化の実装手段**: `RetryPolicy(max_retries=0)`(max_retries=0が「retries無効」の意味。既定はmax_retries=2・backoff_initial=0.5・backoff_max=5.0・timeout=30.0)。client.system_one には呼び出し単位の retry/timeout 上書きもある | docs.typesafe.ai/sdk/python/api/retries |
| T6 | 例外階層: `TypeSafeError`(基底)/ `TypeSafeAPIError`(HTTPエラー応答。属性 `status`(int)・`body`・`headers`・`endpoint`・`request_id`)/ `TypeSafeRateLimitError`(429・TypeSafeAPIError継承・`retry_after_ms`)/ `TypeSafeAPIConnectionError`(HTTP応答なしの失敗=接続エラー・**タイムアウト含む**) | docs.typesafe.ai/sdk/python/api/exceptions |
| T7 | 429・529時はSDKが既定で指数バックオフ再試行(Handling rate limits に明記)→ LATCHはT5で無効化する | docs.typesafe.ai/api(Errors・Handling rate limits) |
| T8 | **jev-latest は現在も jev-1.13.0 を指す**(consistency cookbook の実行出力「requested: jev-latest / returned: jev-1.13.0 ×15」)。ピン固定の前提は変えていない | docs.typesafe.ai/cookbooks/consistency_noul_cookbook |

**改版メモ(2026-09-28)からの変化の照合**: エンドポイント・リクエスト/応答形式・質問型・
レート制限(250,000 tok/s+1,200 req/min・動的調整)・SDKのretryパラメータ存在・例外の
いずれも同一で、**変化なし**。変化がないためdocs(07 §4等)の記述はそのまま正しい。
また改版メモ§3-5の未確認事項「SDK retry無効化の実装手段」は今回T5で解消した
(`RetryPolicy(max_retries=0)`)。
**留意1点**: docs.typesafe.ai/api のAPIDOC要約例は noul 応答を `{"type":"noul","value":true}`
と示すが、quickstartの応答例とSDKの型(`answers[key].noul` がfloat)は `noul` 数値確率である。
**SDKの型(float)を採用**する(§2.4)。実APIスモークで最終確認(§5-7)。

**Anthropic(フォールバックLLM・Sonnet 5)** — 取得手段: claude-apiスキル(2026-06-24キャッシュ
のモデル表)+ リポジトリ既存実装(anthropic.py・G1実績あり)。

| # | 事実 | 出典 |
|---|---|---|
| A1 | モデルID: `claude-sonnet-5`。1Mコンテキスト。単価は入力$2.00/出力$10.00 per 1Mトークン(80% alertのコスト試算(T1 v0.2)の前提と整合をM4で再検算) | claude-apiスキル Current Models 表 |
| A2 | **sampling系パラメータ(temperature・top_p・top_k)は廃止 — 送信すると400**。thinkingは `{type:"adaptive"}` が唯一のon・`{type:"disabled"}` は受理される。`budget_tokens` は400 | 同上(model表・API Drift表) |
| A3 | structured outputs: `output_config: {format: {type:"json_schema", schema}}` を `messages.create` へ渡す(旧 `output_format` は非推奨)。**リポジトリ既存のAnthropicParserProviderと同一形式**(G1で実API動作済み)。min/max等の数値制約は対応しないためスキーマから除く(既存 `_STRIP_KEYS` の知見を流用) | 同上 Architecture 節・backend/src/latch/llm/anthropic.py |
| A4 | SDK設定: `max_retries` 既定2(429/5xx・接続エラーを再試行)→ **0へ設定**。`timeout` は秒(float)。タイムアウト時は `anthropic.APITimeoutError`。例外: `RateLimitError`(429)・`APIStatusError`(`.status_code` で判定・529=overloaded_errorは≥500側)・`APIConnectionError` | 同上 Client config・Error Handling・shared/error-codes.md |

### 1.4 既存実装資産との接続(すべてマージ済みmain)

- `llm/gateway.py`: `judge_pair(intent_a, intent_b, intent_ids) -> dict` の署名と
  `_call`(asyncio.timeout+SendRecord)がある。**本単位でjudge_pairの中身を実装化し
  フォールバック切替を組む**(§2.2)。`build_embedding_gateway`(Worker・スモーク用)は
  **`build_worker_gateway` へ改名拡張**(embedding+jevのreal化。§2.8)
- `llm/providers.py`: `JevProvider` ABC(`judge(intent_a, intent_b) -> dict`)。戻り値を
  System One envelope(model+answers+usage)へ約束変更(呼び出し元はGatewayのみ)
- `llm/stub.py`: `DEFAULT_JEV_RESPONSE` はv0.4形式(score/reasonキー)。**10 §1 v0.4が要求する
  answers形式へ更新**(§2.8・機械的追随)
- `llm/anthropic.py`: `adapt_schema_for_anthropic`・`_STRIP_KEYS`・明示base_url渡しの規律を
  フォールバック実装へ流用
- `worker/cost/`: `JevCostGuard.request_execution(intent_id, user_id) -> JevDecision`
  (allowed/deny_reason)・`JevCostStore.record_execution(provider, day)`・`ReevalGuard`。
  **Layer 4から呼ぶだけ**(ws-4引継ぎ契約・§2.5)
- `worker/matching/runner.py`: L1〜3をstage1トランザクション内で実行し
  `RetrievalOutcome.topkc`(上位K_c=20)を返す。**Layer 4はtopkcを直接消費せず
  永続化されたmatch_candidates行から選択する**(§2.6)
- `worker/matching/layer1.py`: `LAYER1_WHERE`(Layer 1全条件の確定文字列)。
  **H再検証はこの文字列を再利用**(試験と本番のWHERE乖離なし — ws-3設計の規律継承)
- `worker/embedding.py`: **2フェーズ構成の実例**(短トランザクション読取→API呼び出しは
  トランザクション外→ガード付きUPDATE)。Layer 4はこの構成を踏襲(§2.1)
- `worker/main.py`: `_dispatch` は processed/duplicate で `_kick_embedding` を呼ぶ
  (コミット後・ack前)。**同一パターンで `_kick_jev` を追加**。redisクライアントは
  run()内で構築済み(reeval用)— cost store も同じ接続を使う
- `worker/stage1.py`: **変更不要**(L1〜3はmatching_hookのまま。Layer 4はコミット後の
  Worker側キックのため stage1 は関与しない)
- `worker/embedding_text.py`: 正規化テキスト生成の実例。ただし**Embedding用形式
  (「/」連結)とJev用形式([hard]/[soft]行)は別物** — 新規純関数 `build_jev_text`(§2.3)
- alembic 0002 = head。`match_candidates` にskip_reason列は存在しない(→0003追加・§2.7)

### 1.5 スコープ外(後続単位へ渡すもの。本単位では作らない)

1. **circuit breaker・障害注入・G2ハーネス**(ws-8・10 §4.5)。本単位は単発429・529・timeoutの
   切替のみ(D-15注記どおり)
2. **Layer 5 LATCH Engine**(ws-6): MutualScore計算・閾値・D-08上限・proposal。本単位は
   jev_result記録まで(latch_scoreは書かない)
3. **30分Bucket再評価・catch-upスキャン**(ws-6): 本単位のLayer 4はembedding_completed起点の
   Event処理のみ。ws-6がbucket起点から同一のJevWorkerを呼ぶ(reevalガードは入口で共用)
4. **グループマッチ**(ws-7): 候補Pool・貪欲法・グループ由来ペアのK_j配分(規則2〜4)・
   未判定ペア継続優先。本単位は1対1のみ(→ws-7で選択関数を拡張。§2.5)
5. **リセットジョブ本体**(M3-4): skipped候補の再評価イベント発行。本単位は選択側の
   再選択規則(§2.7)で再評価可能性を担保
6. **質問文言(instructions/criteria)の最終調整**(G2日本語評価・07 §4)。本単位は07 §4の
   実装イメージを初期値として固定し全文ピン試験で守る
7. **usageトークンの恒久記録・コスト集計**(M4 Observability)。送信記録(構造化ログ)は既存枠組み

## 2. 実装方式の選択と推奨

### 2.1 Layer 4の実行位置 — 推奨: Stage1コミット後のWorkerキック(EmbeddingWorkerと同一の2フェーズ構成)

**案A(不採用)**: stage1のmatching_hook内(L1〜3と同一トランザクション)でLayer 4まで実行。
外部API呼び出し(最大8回×6秒)がDBトランザクション内に入り、match_events行のFOR UPDATEと
match_candidates書き込みを最悪48秒保持する。さらにstage1の再試行(5回)でロールバック後に
Jev呼び出しをやり直し、**課金の二重払い**が構造的に発生する。06 §9の再試行経路は
「トランザクション処理」の設計であり、冪等でない外部呼び出しを載せる前提にない

**案B(推奨)**: `Worker._dispatch` でembedding_completedのEventが processed/duplicate に
なった後に `_kick_jev(intent_id)` を呼ぶ(`_kick_embedding` と対称・コミット後ack前)。
Layer 4本体は `worker/jev.py` の **JevWorker** として独立させ、embedding.py と同じ
**2フェーズ構成**で組む:

```text
フェーズ1(短トランザクション): 起点読取+選択SELECT(§2.5)+H再検証(§2.6)
  → 起点が読み取れない(削除・非active・version更新)場合はno-opで終える(構造化ログ)
フェーズ2(トランザクション外): ペア毎に Guard判定→allowなら
   Gateway.judge_pair(API呼び出し)→record_execution
フェーズ3(ペア毎の短トランザクション): 結果のガード付きUPDATE
   (成功=jev_result+status=evaluated / deny・LLM失敗・検証失敗=skipped+skip_reason)
```

- 利点: 外部API呼び出しがトランザクション外。**Event再配信で回収できる** — DB失敗が
  伝播すれば_dispatchの既存exceptが受けてackなし→再配信→stage1はduplicate→
  `_kick_jev` が再実行され、冪等ガード(jev_result IS NULL)で完了分は飛ばして再開する
  (embeddingの回収経路と同一)。LLM失敗・denyは例外にせずskippedへ記録(§2.9)
- トレードオフ: at-least-onceで同一ペアのAPI呼び出しが重複し得る(再配信・並行Event)。
  embedding.pyと同一の受容判断(コスト影響はJev入力課金のみで小さい。フォールバック時は
  高単価だが再配信は稀)。D-16の計上は実際のAPI呼び出しごとに行われるため整合する

**案C(不採用)**: Jev専用Workerプロセス+pending行スキャン。起点がEvent文脈(K_j=8は
1イベント処理あたりの上限)を失い、配分規則の実装ができない。プロセス増もMVP過剰

### 2.2 LLM GatewayのSystem One IFとフォールバック切替 — 推奨: Gateway内切替+系統別プロバイダ2実装

**構成**。`JevProvider` ABCの実装を2つ作り、Gatewayが切替を制御する:

- `llm/typesafe.py` — `TypeSafeJevProvider`(第一候補)。`AsyncTypeSafeClient`
  (api_key・base_url明示・timeout=6.0・RetryPolicy(max_retries=0))。
  `system_one(state={intent_a,intent_b}, model=JEV_MODEL, questions=JEV_QUESTIONS)`。
  例外の翻訳(§2.2-表)。name="typesafe"(08 §3送信記録の送信先)
- `llm/anthropic_jev.py` — `AnthropicJevFallbackProvider`(フォールバック)。
  既存 `AsyncAnthropic`(api_key・base_url明示・timeout=6.0・max_retries=0)。
  `messages.create`(model="claude-sonnet-5"・system=フォールバックプロンプト・
  `output_config={"format":{"type":"json_schema","schema":<7キーnumberのスキーマ>}}`・
  `thinking={"type":"disabled"}`・max_tokens=512)。応答JSONをSystem One envelope
  (model=None・answers・usage)へ組立て直す。name="anthropic"

**Gateway.judge_pair の切替ロジック**(各呼び出しは既存 `_call` でasyncio.timeout(6秒)+
SendRecord付き — 第一候補とフォールバックで各1記録・送信先で区別):

| 第一候補の失敗 | 扱い | 実装 |
|---|---|---|
| timeout(6秒) | フォールバックへ切替 | `_call` が送出する `LLMTimeoutError` をjudge_pair内で捕捉 |
| 429 | 同上 | TypeSafeJevProviderが `TypeSafeRateLimitError` を `LLMRateLimitError`(新設・LLMError継承)へ翻訳 |
| 529 | 同上 | `TypeSafeAPIError(status==529)` を `LLMOverloadedError`(新設)へ翻訳 |
| 接続障害(DNS・拒否・SDKタイムアウト) | 同上 | `TypeSafeAPIConnectionError` を `LLMConnectionError`(新設)へ翻訳。07 §4「Jevに到達できない場合」の概念実装 — Gatewayのasyncio.timeout(6秒)が先に効くため通常はLLMTimeoutError側 |
| 400等のその他HTTPエラー | **切替しない**で伝播 | `LLMProviderError` のまま(LLMGateway既存wrap)。リクエスト不備はフォールバックでも解決しない(07 §4の切替条件は429・529・timeoutのみ) |
| 出力検証失敗(スキーマ不一致) | **切替しない**で伝播 | 検証はjudge_pair内で実施(§2.4)。07 §4「失敗は再試行せず縮退へ(実装不整合)」 |

フォールバック呼び出しの失敗(timeout・429・5xx・検証失敗)はそのまま例外として
Layer 4へ伝播し、skipped(llm_failure / invalid_output)へ記録する(D-15・引用#9)。

- **タイムアウト予算**: 第一候補6秒+フォールバック6秒の最悪12秒は仕様どおり
  (引用#10: 双方に6秒を適用)。通常時はTypeSafe公称70〜500msで収まる
- **JevJudgment**: `judge_pair` の戻り値を `dataclass JevJudgment(provider: str, model: str | None, result: dict)` とする。`result` は検証・正規化済みのjev_result用dict(§2.4)。
  Layer 4は `jev_result = {**result, "provider": provider, "model": model}` を書き込むだけ
- **record_execution はGatewayでなくLayer 4が呼ぶ**: Gatewayはproviderを返すのみ。
  「実際のAPI呼び出しベースで切替完了側が呼ぶ」(ws-4契約)の実体は、切替を完了した
  Gatewayが経路を確定して返し、実行の主体(Layer 4)が計上する構成。GatewayをRedis非依存の
  まま保つ(APIプロセスと共用のため)
- **circuit breaker(ws-8)の接続点**: judge_pairの第一候補呼び出しを包む位置に
  後続単位で挟める構造(brakerオブジェクトの注入ポイント)だけ見込んでおく。
  実装しない(§2.10)

### 2.3 質問定義と正規化テキスト(07 §4の実装化)

**JEV_QUESTIONS定数**(`llm/jev.py`)。07 §4の実装イメージJSONをそのまま定数化する
(7質問・キー名・型・criteria)。dict形式で保持しSDKへ渡す(SDKのNoul/Scoreクラスを使わない
— 定数がJSON serializableになり、全文ピン試験が単純になる)。モデル定数
`JEV_MODEL = "jev-1.13.0"`(エイリアス不使用・引用#2)。文言は07 §4の初期値のまま
最終確定はG2(引用#4)。**全文ピン試験**で変更を検知する(PARSER_SYSTEM_PROMPTと同一規律)

**build_jev_text純関数**(`llm/jev.py`)。07 §4の正規化テキスト形式を生成する:

```text
Intent A:
[hard] category: drinking
[hard] time: 2026-09-26 20:00–23:00
[hard] location: 天文館周辺 半径2km
[hard] participants: 2–4人
[hard] budget_max: 5000円
[soft] 軽く飲みたい
[soft] 会社関係の人は避けたい(システムで判定不能)
```

実装定義(docsが例示のみの部分):

| 項目 | 定義 | 根拠 |
|---|---|---|
| time行 | JST表記 `YYYY-MM-DD HH:MM–HH:MM`。起点・対象の両側ともtime_end補完後(intents/completion.pyのdefault_time_end — origin.load_originと同じ補完規則)の値を表示する | 07 §4例・算術はコード側(引用#20) |
| location行 | `structured_data.location_name` + `半径{geo_radius_m/1000}km`(端数はm表記) | 07 §4例「天文館周辺 半径2km」 |
| participants行 | `{min}–{max}人`(min==maxは`{n}人`) | 07 §4例 |
| budget_max行 | `{額}円`。**NULL(制約なし)は行を省略** | 最小構成の維持(引用#20)。NULL=制約なしは省略が意味を壊さない |
| [soft]行 | soft_constraintsの全項目(降格込み)。`downgraded_from_ng=true` の項目は末尾に`(システムで判定不能)`を付ける | 引用#3・07 §4例の2つ目のsoft行がまさにこの形 |
| 含めないもの | visibility・notification_level・alcohol_involved・category_secondary・raw_text | 引用#3・07 §4例にこれらの行はない |

入力dataclassはraw_textを保持しない(embedding_text.pyと同じ構造的ピン)。
stateは `{"intent_a": <text_a>, "intent_b": <text_b>}`(07 §4どおり)。
A/Bの割当は**match_candidatesの正規化順(intent_a_id < intent_b_id)のrow側**をそのまま
使う(決定的・テストの再現性)。origin/candidateのどちらがAになるかはUUID順で決まる

### 2.4 出力検証・正規化・jev_result格納

**検証(第一候補・フォールバック共通・`llm/jev.py` の純関数)**。envelope(または
フォールバック組立て済みenvelope)に対して:

1. answersのキー集合が質問の7キーと完全一致(同キー性 — 引用#5)
2. noul(would_a_accept_b・would_b_accept_a・latent_yes)は[0,1]の数値
3. score(purpose_fit・mood_fit・timing_fit・social_fit)は[0,4]の数値
4. confidence(score応答のみ・任意): [0,1]の数値またはnull

失敗は `JevOutputInvalidError`(LLMError継承・新設)として送出し、**再試行もフォールバック
切替もしない**(引用#5・§2.2表)。Layer 4がskipped(invalid_output)へ記録する

**正規化とjev_result構造**(05 §2どおり):

```json
{
  "would_a_accept_b": 0.83,
  "would_b_accept_a": 0.71,
  "jev_5axis": {
    "purpose_fit": {"value": 0.75, "confidence": 0.8},
    "mood_fit":     {"value": 0.50, "confidence": 0.6},
    "timing_fit":   {"value": 0.50, "confidence": 0.7},
    "social_fit":   {"value": 0.75, "confidence": 0.6},
    "latent_yes":   {"value": 0.40, "confidence": null}
  },
  "provider": "typesafe_jev",
  "model": "jev-1.13.0"
}
```

- **score→[0,1]正規化の分母は4(最大レベル)**: 5段階の水準は0〜4であり、4で割ったときのみ
  「[0,1]へ正規化」(07 §4)・「0〜1正規化値」(05 §2)が成立する。5で割ると上限が0.8になり
  docsの文言と矛盾するため不採用(§5-5に解釈記録)。System Oneのscoreは確率加重のfloat
  (例: 1.035)のため値域検査は[0,4]の実数に対して行う
- latent_yesはnoulのため値は[0,1]のまま(正規化不要)。confidenceはnull(noulに付かない)
- フォールバックLLMはprovider="fallback_llm"・model=null(引用#8)・jev_5axisのconfidenceは
  全てnull(引用#6)
- **reasonは生成も格納もしない**(引用#7)

### 2.5 K_j選択・Guard契約・コスト計上

**選択(フェーズ1のSELECT)**。起点Intent(origin)の現在versionにおける評価世代の
未評価ペアから、cheap_judge_score降順(同点は相手intent_id昇順 — D-24)で最大8件を選ぶ:

```sql
SELECT mc.id, mc.intent_a_id, mc.intent_b_id, mc.intent_a_version, mc.intent_b_version
FROM match_candidates mc
WHERE (mc.intent_a_id = :origin OR mc.intent_b_id = :origin)
  AND (CASE WHEN mc.intent_a_id = :origin THEN mc.intent_a_version
            ELSE mc.intent_b_version END) = :origin_version
  AND mc.jev_result IS NULL
  AND (mc.status = 'pending' OR <再選択規則 — §2.7>)
ORDER BY mc.cheap_judge_score DESC, <相手intent_id> ASC
LIMIT 8
```

- **jev_result IS NULL が同一評価世代スキップ(引用#12)のフィルタ**である: 評価済みの
  同一バージョン組行は選ばれず、jev_resultは変わらず保持される
- **topkc(RetrievalOutcome)からでなく永続行から選ぶ理由**: Layer 4はコミット後の別フェーズで
  動くためin-memoryのtopkcを渡す経路を作るとstage1への状態保持が発生する。永続行からの
  選択は(i)Event再配信・ワーカー再起動で選択が失われない(ii)過去ランの同一世代未評価
  ペア(前回K_jで切り捨てられた分)も自然に対象化し、引用#12「Layer 2〜3の順位変動で
  新たに上位に入ってきたペアは新しいペアとして通常どおりJev実行対象」を包含する。
  topkc自体はws-4の外部契約としてそのまま残す(試験・トレース用)
- **K_c=20との関係**: K_j=8 < K_c=20のため、cheap_score順の上位8件は常に上位20件の
  部分集合。20件の切り詰めを先に適用する必要はない
- **純関数 `select_jev_targets`**(`worker/matching/layer4.py`): 選択済み行リストへ
  配分規則(引用#11)を適用する純関数。ws-5時点では入力が全て1対1のため
  「1対1最低4回保証+繰り上げ上限8」の恒等操作になるが、**K_J=8・ONE_ON_ONE_MIN=4の
  定数と1対1/グループのタグ引数をここに置き**、ws-7がグループペアの割当(規則2〜4)を
  この関数の拡張として追加する。LIMIT 8はSQL側・関数側の二重防御

**Guard呼び出し(フェーズ2・ペア評価毎)** — ws-4引継ぎ契約(引用#13)どおり:

1. 各ペアのAPI呼び出し直前に `guard.request_execution(origin_intent_id, origin_user_id)`。
   **課税先は起点Intent(当該Eventのsource)とその作成者のみ** — 実装定義。根拠: 06 §5の
   予算は「1Intentあたりの詳細評価」であり、評価を引き起こす主体はEventの起点。悪用経路
   (自己のIntentを反復更新してEventを量産)の消費が起点側カウンタで正確に抑えられ、
   Guard契約の署名(intent_id, user_id)とも一致する。候補側として評価される消費は
   起点側のEvent処理に属するため二重計上にならない
2. deny → 当該ペア行を `status='skipped', skip_reason=<deny_reason>` へUPDATE
   (jev_resultはNULLのまま)。**INCR先行のためdenyもカウンタを消費する**(ws-4承認どおり)
3. allow → `gateway.judge_pair` → 成功時 `store.record_execution(<provider>, <day>)`
   (dayはClock.jst_date()から導出 — C2)。**双障害(フォールバックも失敗)時は最後に
   実際に呼んだ経路(fallback_llm)を計上**する — API呼び出しは発生しており、内訳の
   観測性を優先する。 deny時はAPI呼び出しがないため計上しない
4. **K_jは選択上限であり補充はしない**: denyされたペアが発生しても残枠へ順位を繰り上げ
   ない。deny理由(intent/user/global)は同一Event内の後続ペアでも同じ判定になるため
   補充は無駄なINCRであり、D-15の「日次リセット後の次のMatch Eventで再評価」の
   予定と一致する

**H再検証のタイミング**: 選択されたpending行はAPI呼び出しの前にペア単位のLayer 1判定を
行い(§2.6)、不成立なら評価しない(Hard Constraint不成立ペアへのJev消費を源流で止める
— D-16の根拠「1イベントあたり平均1〜2回」の維持)。このときGuardを呼ばない(実行しない
ものは課税しない)

### 2.6 同一評価世代スキップとH再検証(FR-07)

**H再検証の実装**(`worker/matching/layer4.py`)。`layer1.LAYER1_WHERE` を再利用した
ペア単位判定SQL — 起点パラメータ(bind_params)と相手Intent行を1対1で照合する:

```sql
SELECT 1 FROM intents i JOIN users u ON u.id = i.user_id
WHERE i.id = :candidate_id AND {LAYER1_WHERE} LIMIT 1
```

(行が存在すればH成立。LAYER1_WHERE文字列の再利用により、パイプライン本体のLayer 1と
H再検証の条件乖離が構造的に起きない — ws-3設計の規律継承)

適用対象と結果の扱いを、行の状態で区別する:

| 対象行 | H不成立時の扱い | 根拠 |
|---|---|---|
| **jev_result保持行(status='evaluated'・同一世代)** | `status='closed'` へ閉じる(バッチ1 SQL: NOT EXISTS形式で不成立行を一括SELECT→UPDATE) | 06 §5の規定そのもの(引用#12) |
| **選択されたpending行** | 今回は評価せず**pendingのまま**(閉じない) | 規定の閉じる指示はjev_result保持行に対するもの。pending行はblock解除等で条件が回復する余地があり、閉じると同バージョン組での再評価機会を失う。次のEvent/Bucket再評価で再判定される |

起点側がversion更新されていた場合(フェーズ1のversionガードで読み取れない)は
当該行の処理を飛ばす(新世代行が別Eventで作られる。旧世代行はjev_result保持のまま)

### 2.7 skip_reason列と再選択規則 — マイグレーション0003(supervisor承認事項)

**課題**: 引継ぎ契約はdeny時の理由記録を要求し(引用#13)、D-15はskipped候補の回収経路を
理由で区別する — 予算系(intent_daily・user_daily・global_daily・global_monthly)は
**日次/月次リセット後**に、API障害系は**回復後の次のEventで**再評価される(引用#14)。
`updated_at` のみではこの区別を実装できない(同日内の予算denyと障害denyが区別できない)

**決定**: `match_candidates` へ `skip_reason text NULL` を追加するマイグレーション0003を
作る。値域(実装定義・小文字スネーク):

`intent_daily` / `user_daily` / `global_daily` / `global_monthly`(Guardのdeny_reasonと同一
文字列 — ws-4設計§2.4-2の列挙)/ `llm_failure`(第一候補+フォールバック双方の失敗)/
`invalid_output`(出力検証失敗)

**再選択規則(選択SQLの条件)**:

| 行の状態 | 再選択可否 |
|---|---|
| status='pending' | 常に選択可能 |
| status='skipped' かつ skip_reason ∈ {llm_failure, invalid_output} | 常に選択可能(障害回復後のEventで即時・D-15) |
| status='skipped' かつ skip_reason ∈ {intent_daily, user_daily, global_daily} | `updated_at < 本日のJST 0時` のとき選択可能(日次リセット後) |
| status='skipped' かつ skip_reason = global_monthly | `updated_at < 今月の暦月初JST 0時` のとき選択可能(月次リセット後 — D-16) |

JST境界はClockから導く(C2・引用#15と同じ規律。ws-4の `_next_month_start_jst` と対の
導出)。これにより「日次リセット後の次のMatch Eventで再評価される」(06 §5)が
M3-4のリセットジョブ(保留キュー再評価イベント)を待たずに選択側で担保される

- 05 §2(status列の説明)へのskip_reason追記は次回のdocs改版に含める方式をとる
  (ws-3のlocation_nameキー追加と同じ扱い — §5-1で承認を仰ぐ)
- jev_result JSONBへskip_reasonを格納する代替は不採用: 「jev_result IS NULL=未評価」の
  フィルタ(§2.5)を壊し、世代スキップの判定を複雑化するため

### 2.8 Stub・設定・Gateway構成

**StubLLM**(`llm/stub.py`):

- `DEFAULT_JEV_RESPONSE` をSystem One envelopeへ更新(引用#15):
  `{"model": "jev-1.13.0", "answers": {would_a_accept_b: {"type":"noul","noul":0.5}, …,
  purpose_fit: {"type":"score","score":2.0,"confidence":0.5}, …}, "usage": {"input_tokens": 0,
  "output_tokens": 0}}`。値は決定的に固定(noul=0.5・score=2.0・confidence=0.5)
- コンストラクタ引数 `jev_response` はdictのまま(envelope形式を渡す)。`fail_jev` は
  LLMProviderError送出のまま(切替不可能→llm_failure経路の試験に使う)
- **フォールバック経路のスタブ**: Gatewayの `jev_fallback` に別インスタンスのStubLLMを
  注入できるようにする(単体試験で第一候補fail_jev=True+フォールバック正常の切替を再現)。
  ci構成(stub mode)では同一インスタンスを両方へ渡す(stubは切替可能な例外を出さないため
  問題ない)。フォールバック側スタブは `jev_response` のconfidenceをnullにした
  envelopeを渡して使う(10 §1どおり)

**Settings追加**(`settings.py`・AliasChoices形式はllm_anthropic_api_keyと同一):

- `llm_typesafe_api_key`(env `LATCH_TYPESAFE_API_KEY`・既定""): SDKが環境変数
  `TYPESAFE_API_KEY` を自動採用するため、明示渡し経路のみとする(gemini鍵と同じ規律)
- `llm_typesafe_base_url`(env `LATCH_TYPESAFE_BASE_URL`・既定 `https://api.typesafe.ai`):
  SDKは `TYPESAFE_BASE_URL` 環境変数を採用するため、ANTHROPIC_BASE_URL事故(M1 ws-6 §5-1)
  と同じ構造の予防として明示渡しする

モデルIDはsettings化しない — `JEV_MODEL="jev-1.13.0"`・
`ANTHROPIC_JEV_FALLBACK_MODEL="claude-sonnet-5"` を各プロバイダのmodule定数とする
(ws-4 §2.8-4の規律: docs確定値は定数。調整はdocs改版を伴う)

**Gateway構成**(`build_worker_gateway` への改名拡張):

- `build_embedding_gateway` を `build_worker_gateway(clock, settings)` へ改名し、
  real時に**embedding+jev両系統をreal化**(gemini鍵+typesafe鍵+anthropic鍵。フォールバックは
  parserと同一の `llm_anthropic_api_key` を使う)。鍵の欠落はfail-fast(静かにスタブへ
  落ちない — 既存規律)。stub時は3系統スタブ+jev_fallback=同一スタブ
- 呼び出し側2箇所を追従: `worker/main.py`(Worker DI)・`llm/embed_smoke.py`。
  APIプロセスの `build_llm_gateway`(parser real)は無変更
- 変更理由: Workerはembedding workerとLayer 4の両方で同一Gatewayを使うため、
  embedding用のビルダにjevを足して名称を実態へ合わせる

### 2.9 障害時の挙動と再配信(設計のまとめ)

| 事象 | Layer 4の扱い | 行の状態 |
|---|---|---|
| Guard deny(予算/回数上限) | 例外にせず記録 | skipped + skip_reason=<deny_reason> |
| 第一候補429/529/timeout/接続障害 | Gateway内でフォールバックへ切替(送信記録2件) | (評価成功なら)evaluated |
| フォールバックも失敗(双障害) | 例外をLLM失敗として記録 | skipped + skip_reason=llm_failure |
| 出力検証失敗(どちらの経路でも) | JevOutputInvalidErrorを記録 | skipped + skip_reason=invalid_output |
| GuardのRedis失敗(JevCostDependencyError) | **伝播**(fail-closed — 実行の通り抜けを許さない)→ _dispatchの既存except→ackなし再配信→duplicate経由で_kick_jev再実行 | 変えない(pending) |
| DB書き込み失敗 | 伝播→再配信で回収(冪等ガードで完了分は飛ばす) | 変えない |
| 並行Eventによる同一行の二重評価 | 許容(at-least-once)。各API呼び出しはD-16の1実行回数として計上済み | 後勝ち(同一世代のため値差は小さい) |

Workerは1Eventずつ直列処理するため、Layer 4で最悪12秒×8ペアの遅延が後続Eventに
波及し得る。通常時(TypeSafe 70〜500ms×8)は問題ない。並列化は計測後に判断(§2.10)。
ws-8でcircuit breakerとWorker同時実行数上限がこの面を改めて扱う

### 2.10 採用しないもの(YAGNIによる切り捨て一覧)

1. **circuit breaker**(06 D-15の継続障害検知・開放/半開)。ws-8。Gatewayの注入ポイントは
   確保するが実装しない
2. **グループ由来ペアのK_j配分(規則2〜4)・未判定ペア継続優先**。ws-7(候補Poolがないため
   実装不能)。定数と純関数の形だけ用意(§2.5)
3. **30分Bucket再評価・catch-upスキャン起点のLayer 4呼び出し**。ws-6(JevWorkerのIFは
   `handle(intent_id)` と起点非依存にしておき、ws-6が同じ部品を呼ぶ)
4. **ペア評価の並行化(asyncio.gather)**。直列で開始。レイテンシ問題が計測で出た場合のみ
5. **行単位のFOR UPDATEクレーム(二重評価の排除)**。at-least-onceで十分(§2.1・§2.9)
6. **jev_resultへのskip理由格納**。§2.7の代替として不採用(フィルタを壊す)
7. **フォールバック時のmodel記録**。05 §2がnullを確定(引用#8)
8. **usageトークンのDB記録・コスト集計**。M4。送信記録は構造化ログで既存枠組み
9. **質問文言の最終調整**。G2日本語評価(07 §4)。初期値の全文ピン試験で意図しない変更を検知
10. **reevalガードのLayer 4入口への追加**。Layer 4はL1〜3を通過したEventの後段であり、
    ガードは `_run_matching` の入口(ws-4実装)で効いている。ws-6がbucket起点を足すときに
    同一ガードで統制する

## 3. ファイル構成

### 3.1 作るもの(新規ファイル)

```text
backend/src/latch/llm/jev.py
    # System One共通(07 §4): JEV_MODEL・JEV_QUESTIONS(7質問のdict定数・全文ピン対象)
    #   build_jev_text(input)->str([hard]/[soft]形式・§2.3の表どおり)
    #   validate_and_normalize(envelope)->dict(jev_result用result部・§2.4)
    #     キー同値性・値域検証→JevOutputInvalidError・score/4正規化・confidence抽出
    #   JevJudgment(provider, model, result) dataclass・FALLBACK_STATE envelope組立てヘルパ
backend/src/latch/llm/typesafe.py
    # TypeSafeJevProvider(JevProvider実装・第一候補):
    #   AsyncTypeSafeClient(api_key・base_url明示・timeout=6.0・RetryPolicy(max_retries=0))
    #   judge→system_one(state={intent_a,intent_b}, model=JEV_MODEL, questions=JEV_QUESTIONS)
    #   応答をenvelope(dict)へ・例外翻訳: TypeSafeRateLimitError→LLMRateLimitError
    #   TypeSafeAPIError(status=529)→LLMOverloadedError・TypeSafeAPIConnectionError→
    #   LLMConnectionError・その他→LLMProviderError。name="typesafe"
backend/src/latch/llm/anthropic_jev.py
    # AnthropicJevFallbackProvider(JevProvider実装・フォールバック):
    #   AsyncAnthropic(api_key・base_url明示・timeout=6.0・max_retries=0)
    #   model=claude-sonnet-5・output_config json_schema(7キーnumber)・
    #   thinking={"type":"disabled"}・max_tokens=512(sampling系は送信しない — 400)
    #   フォールバックプロンプト定数(全文ピン対象)・応答JSON→envelope(model=None)
    #   SDK例外は翻訳せずGatewayの既存wrapへ。name="anthropic"
backend/src/latch/worker/matching/layer4.py
    # 選択とH再検証(connベース・layer1/layer2と同型):
    #   select_jev_rows(conn, origin_id, origin_version, jst_day, jst_month_start)->rows(§2.5 SQL)
    #   hard_constraint_holds(conn, origin, candidate_id)->bool(LAYER1_WHERE再利用)
    #   close_broken_pairs(conn, origin, now)->int(jev_result保持行のH不成立一括close)
    #   select_jev_targets(rows)->rows(配分純関数・K_J=8・ONE_ON_ONE_MIN=4・ws-7拡張点)
backend/src/latch/worker/jev.py
    # JevWorker(2フェーズ・§2.1案B): __init__(engine, clock, gateway, guard, cost_store)
    #   handle(intent_id): フェーズ1(短tx: 起点読取+選択+H再検証対象)
    #     →フェーズ2(tx外: ペア毎にguard→deny記録/gateway.judge_pair→record_execution)
    #     →フェーズ3(短tx: ガード付きUPDATE jev_result+evaluated / skipped+skip_reason)
    #   versionガード読取(起点・相手ともrowのversionと一致するときのみ評価)
    #   例外方針は§2.9の表どおり
backend/src/latch/llm/jev_smoke.py
    # 実APIスモーク(--fallbackフラグでフォールバック直接呼び出しも可)。
    #   07 §4例の正規化テキスト2件を固定入力としJevJudgment+送信記録を出力。
    #   実行はスーパーバイザー検証時のみ(課金のため)
backend/alembic/versions/0003_match_candidates_skip_reason.py
    # ALTER TABLE match_candidates ADD COLUMN skip_reason text NULL
backend/tests/unit/llm/test_jev_format.py
    # build_jev_text全境界・validate_and_normalize全境界・JEV_QUESTIONS全文ピン
backend/tests/unit/llm/test_typesafe_jev.py
    # ペイロード組立・例外翻訳4種・RetryPolicy(max_retries=0)・鍵欠落fail-fast
backend/tests/unit/llm/test_anthropic_jev_fallback.py
    # リクエスト組立(model・schema・thinking disabled・sampling不送・max_retries=0)・
    # envelope組立・プロンプト全文ピン
backend/tests/unit/llm/test_gateway_jev_switch.py
    # judge_pair切替マトリクス(§2.2表の全行)・SendRecord 2件の送信先・record対象provider
backend/tests/unit/matching/test_layer4.py
    # select_jev_targets配分・選択SQL/H再検証SQLの句ピン(test_layer_sql.py流儀)
backend/tests/unit/test_worker_jev.py
    # JevWorker.handleの全分岐(§2.9表)・冪等ガード・versionガード・record_execution呼び出し
backend/tests/integration/test_matching_jev.py
    # 実DB・実Redis・スタブGatewayでのE2E(§4.2)
```

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更内容 |
|---|---|
| `llm/gateway.py` | `judge_pair` の実装化(切替ロジック+JevJudgment戻り値)・`jev_fallback` プロバイダ引数・`build_embedding_gateway` → `build_worker_gateway` 改名拡展(embedding+jev real) |
| `llm/errors.py` | `LLMRateLimitError`・`LLMOverloadedError`・`LLMConnectionError`・`JevOutputInvalidError` 追加(いずれもLLMError継承) |
| `llm/providers.py` | `JevProvider.judge` の戻り値docstringをenvelope形式へ約束変更(署名不変) |
| `llm/stub.py` | `DEFAULT_JEV_RESPONSE` をSystem One envelopeへ(引用#15) |
| `llm/__init__.py` | 公開API追記(build_worker_gateway・新例外・TypeSafe/AnthropicJev) |
| `worker/main.py` | `_kick_jev`(embedding_completed processed/duplicate後)・JevWorker DI(guard・cost_storeはreevalと同一redis接続から構築)・`build_worker_gateway` への呼び替え |
| `llm/embed_smoke.py` | `build_worker_gateway` へのimport先変更のみ(ロジック無変更・§2.8) |
| `settings.py` | `llm_typesafe_api_key`・`llm_typesafe_base_url` 追加 |
| `worker/cost/__init__.py` | (export済みの場合は変更なし)JevCostGuard・JevCostStoreの再export確認 |
| `backend/pyproject.toml` | 依存へ `typesafe-sdk` 追加(パッケージ名は§5-3で確認) |
| `Makefile` | `jev-smoke` ターゲット追加(embed-smokeと同型・`uv run --env-file ../.env`) |
| `tests/unit/llm/test_gateway_jev.py` | スタブ応答形式追随(envelope)・戻り値JevJudgment化(機械的追随) |
| `tests/unit/llm/test_stub.py` | JEV_KEYS期待値をenvelope形式へ(機械的追随) |
| `tests/unit/llm/test_llm_factory.py` | llm_*設定ピンへtypesafe 2項目追記(機械的追随 — ws-2の前例と同じsupervisor承認事項) |
| `tests/unit/llm/test_llm_gemini.py` | `build_embedding_gateway` → `build_worker_gateway` 改名追随(機械的追随) |
| `tests/unit/test_worker.py` | `_kick_jev` 配線のピン追加(機械的追随+新規) |

既存integration(test_matching_cheapjudge.py等)は `run_candidate_retrieval` の外部契約が
不変のため無変更で動く見込み。影響が出た場合は機械的追随を報告書に記録する

### 3.3 触らないもの

- `backend/src/latch/worker/stage1.py`(**変更なし** — Layer 4はコミット後キックのため)
- `backend/src/latch/intents/`・`auth/`・`users/`・`ratelimit/`・`geo/`・`g1gate/`・`events/`
- `backend/src/latch/worker/` のうち stage1.py・main.py 以外の既存ファイル
  (embedding.py・embedding_text.py・debounce.py・backfill.py・__main__.py・
  matching/{origin,layer1,layer2,layer3,candidates,runner}.py)
- `backend/src/latch/llm/` のうち §3.2 列挙以外(anthropic.py・gemini.py・records.py)
- `backend/alembic/` の既存分(0001・0002不変。0003追加のみ)
- `compose.yaml`・`frontend/`・`prototype/`・`docs/`(01〜12改版不要。§2.7の05追記は
  次回改版へ含める方針 — §5-1)
- `backend/tests/` の§3.2列挙以外の既存試験

## 4. テスト方針

### 4.1 unit(`make test`。外部プロセス不要・スタブで決定的)

- **test_jev_format.py**:
  - build_jev_text: 07 §4例の再現(固定入力→全文一致)・budget NULL行の省略・
    participants min==max・降格NGのsuffix・visibility/notification_level/alcohol/
    category_secondaryが現れないこと・raw_textを保持しない構造ピン
  - JEV_QUESTIONS全文ピン(7質問の型・キー・instructions・criteria — 変更検知)
  - validate_and_normalize: 正常系(数値の取り出し・/4正規化・confidence抽出・latent_yesは
    [0,1]のまま・model/providersなしresult部であること)/ 異常系(キー欠損・余分キー・
    noul>1・score>4・型不一致→JevOutputInvalidError)/ score境界(0・4・1.035)
- **test_typesafe_jev.py**(SDKクライアントをスタブ注入):
  - system_one呼び出し引数(state・model=jev-1.13.0・questions=JEV_QUESTIONS)・
    RetryPolicy(max_retries=0)とtimeout=6.0の設定ピン・base_url/api_key明示渡し
  - 例外翻訳: TypeSafeRateLimitError→LLMRateLimitError・status=529→LLMOverloadedError・
    status=400→LLMProviderError(切替しない側)・TypeSafeAPIConnectionError→LLMConnectionError
  - 鍵欠落でValueError(fail-fast)
- **test_anthropic_jev_fallback.py**(AsyncAnthropicスタブ注入):
  - messages.create引数(model="claude-sonnet-5"・output_config json_schema 7キー・
    thinking={"type":"disabled"}・**temperature等のsampling系が引数にないこと**・
    max_tokens=512・max_retries=0・timeout=6.0・base_url明示)
  - 応答JSON→envelope(model=None・confidenceなし)組立て・プロンプト全文ピン
- **test_gateway_jev_switch.py**(第一候補・フォールバック各スタブ):
  - 切替マトリクス(§2.2表): 第一候補正常→provider=typesafe_jev・model=応答model /
    LLMTimeoutError→フォールバック→provider=fallback_llm・model=None /
    LLMRateLimitError→切替 / LLMOverloadedError→切替 / LLMConnectionError→切替 /
    LLMProviderError(400系)→**切替せず**伝播 / 検証失敗→**切替せず**JevOutputInvalidError /
    フォールバック失敗→LLMエラー伝播
  - SendRecord: 切替時に2件(destination=typesafe→anthropic)・双方timeout/error含む
- **test_layer4.py**:
  - select_jev_targets: 8件上限・1対1のみで恒等・同点順序
  - SQL句ピン(test_layer_sql.py流儀): 選択WHERE(origin関与・起点version・jev_result IS NULL・
    再選択規則の4分岐)/ ORDER BY(cheap_judge_score DESC・相手id ASC)/ LIMIT 8 /
    H再検証SQLがLAYER1_WHEREを含むこと・close一括UPDATEの条件
- **test_worker_jev.py**(fakeredis guard・スタブgateway・スタブengine接続は
  test_matching_runner.py流儀のmonkeypatch):
  - deny→skipped+skip_reason=deny_reason・record_execution不呼出
  - 成功→jev_result(provider・model・jev_5axis正規化値)・status=evaluated・
    record_execution(provider)呼出・skip_reason=NULLクリア
  - 双障害→skipped/llm_failure・record_execution("fallback_llm")
  - 検証失敗→skipped/invalid_output
  - H不成立(pending)→評価せずpending維持・guard不呼出 / H不成立(evaluated行)→closed
  - versionガード: 起点または相手がversion更新済み→行を飛ばす
  - 冪等: jev_result存在行への再handle→API・guard不呼出
  - Guard Redis例外→伝播(raise)/ DB書込失敗→伝播
- **test_worker.py追記**: embedding_completed processed/duplicateで_kick_jev呼出・
  created/updatedでは呼ばない・_kick_jev未注入時no-op
- **機械的追随**(§3.2): test_gateway_jev・test_stub・test_llm_factory(+2項目)・
  test_llm_gemini(改名)

### 4.2 integration(`make test-ci`。compose常設DB・実Redis・スタブGateway)

**対抗策はws-4で確立したものを踏襲**(supervisor裁定・実証済み):

1. **時間窓分離**: 本単位の試験が作る全Intentの基準時刻を `BASE_HOURS=120`(now+5日)へ
   統一。他試験・残存データ(+1〜+7時間帯・過去)はLayer 1の狭義時間交差で構造的に
   交差しない
2. **teardown完全性**: user作成をfixtureで追跡しtry/finallyでFK順
   (match_candidates→match_events→intents→users)削除
3. **Redisキー分離**: JevCostStore/Guardへ試験ごとの一意key_prefixを渡し、teardownで
   `SCAN {prefix}*`+DELETE(jev:daily等の共用干渉防止)
4. 件数assertは自己完結配置に限定し、特定行の存在/不在を基本とする

**試験内容(test_matching_jev.py — stub Gatewayで決定的)**:

1. **E2E評価**: ユーザー登録→active Intent(API)→embedding直接UPDATE(ws-3流儀)→
   `run_candidate_retrieval`→`JevWorker.handle`→jev_result記録
   (provider="typesafe_jev"・model=stub固定値・jev_5axisの正規化値とconfidence)・
   status='evaluated'
2. **K_j=8上限の決定性**: 候補10件(同点を作出)→8件評価・cheap_score降順・
   相手intent_id昇順・残り2件pending(10 §4.6のJev部分)。同一入力2回で同一結果
3. **同一評価世代スキップ**: handle再実行→jev_result不変(更新時刻不変)・
   API呼び出し0回(カウンタ付きラップで検証)・Guard不呼出
4. **H再検証close**: 評価済みペアの相手Intentを時間窓外へUPDATE→起点側handle再実行→
   当該行closed・jev_resultは保持
5. **Guard deny(実Redis)**: key_prefix付きStoreで起点のintent別カウンタを40へseed→
   handle→1件目からskipped+skip_reason=intent_daily・API呼び出し0回・
   denyもカウンタ増加(INCR先行)
6. **再選択規則**: llm_failureのskipped行は即再選択 / intent_dailyのskipped行は
   FakeClockをJST翌日に進めると再選択・同日は不可
7. **record_execution内訳**: 成功時 `jev:exec:{day}:typesafe_jev` が+1(月次キー不在)・
   双障害時fallback_llmが+1
8. **配線(stage1経由)**: embedding_completed Eventをstage1.intakeへ投入→match_candidates
   生成(processed)→JevWorkerが呼ばれる流れをworker._kick_jev相当の直接呼び出しで確認
   (workerプロセス起動不要・ws-4と同じ流儀)
9. 既存全数(test-ci 818)のグリーン維持

### 4.3 検証手順(報告書への明記用)

1. `make test`(unit)→ `make lint`(ws-4運用メモ: test-ciにlintが含まれないため
   マージ前のlint再実行を検証手順に含める)
2. **マイグレーション0003追加あり**: 実装側は共有ci-dbへのtest-ci/migrateを実行せず
   unit試験で開発(運用ルール1)。報告書に「test-ci=スーパーバイザー検証待ち」と記録。
   なお本単位はwave単独のため並走とのDB取り合いは計画上なし
3. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d`
   が空(新規basename: test_jev_format・test_typesafe_jev・test_anthropic_jev_fallback・
   test_gateway_jev_switch・test_layer4・test_worker_jev・test_matching_jev — 既存と衝突なし)
4. スーパーバイザー検証時: `docker compose build api`→`make migrate`(0003適用確認)→
   `make test-ci`→`make jev-smoke`(実API 1呼び出し・課金。--fallbackも任意)
5. test-ci後、Redis残存(prefix掃除)とuser残存(subject LIKE 'm2ws5-%'=0件)を1回手動確認

## 5. 未解決の論点・実装時確認事項

1. **(supervisor承認事項)skip_reason列追加とマイグレーション0003**(§2.7)。deny理由の
   行毎記録(引継ぎ契約)とD-15の回収経路区別(予算系=期間切れ後・障害系=即時)の実装に
   必要。05 §2への追記は次回docs改版へ含める方式(ws-3のlocation_name前例)
2. **(supervisor承認事項)既存試験の機械的追随**(§3.2): test_gateway_jev(stub応答の
   envelope化)・test_stub・test_llm_factory(+2項目)・test_llm_gemini(改名)。
   10 §1 v0.4が規定するスタブ形式変更と設定追加に伴う正当な更新(ws-2・M1 ws-5の前例)
3. **(実装時確認)typesafe-sdkのPyPIパッケージ名とバージョン**: docs上のimport名は
   `typesafe_sdk`(§1.3-T4)。pipパッケージ名(typesafe-sdk等)と最新版をuv add時に確認。
   **取得不能な場合の代替**: APIはRESTで完全に規定済み(§1.3-T1〜T3)のためhttpx直実装で
   プロバイダを書き直しても設計不変(その場合は例外分類をHTTPステータスから直接行う)
4. **(実装時確認)AsyncTypeSafeClientのretry引数名**: docs表記に揺れがある(同期
   TypeSafeClientは `retry`・AsyncTypeSafeClientのAPIDOCは `retry_policy`・§1.3-T5)。
   実装時にSDKの署名を確認してどちらかを明示渡しする(効果は同じ max_retries=0)
5. **(解釈記録)score正規化の分母=4**(§2.4): 07 §4「レベル数で割って[0,1]へ正規化」は
   最大レベル4で割る読みでのみ「[0,1]」が成立する(5で割ると上限0.8)。/4を採用。
   もし/5が意図だった場合は07・05の文言修正が必要(較正・G2評価の前提にも影響するため
   この解釈を明示的に残す)
6. **(解釈記録)Guard課税は起点Intent側のみ**(§2.5-1): 1ペア評価を起点のintent/userへ
   1実行回数として課す。候補側への課税は行わない(二重計上を避け、悪用モデル=自己の
   Event量産に一致)。120回/日の理論値(5×40)はこの読みでの整合
7. **(実装時確認→スモークで最終確認)noul応答のJSON形状**(§1.3留意): api頁のAPIDOC例は
   `value:true`・quickstart+SDK型は `noul:<float>`。SDK型(float)を採用するが、実API
   スモークで応答形状を確認し、万が一異なれば検証関数を実測へ合わせる(設計の他の部分に
   影響しない・検証関数1箇所)
8. **(先送り記録)フォールバック生成パラメータ**: thinking=disabled・max_tokens=512は
    実装定義の初期値(レイテンシ6秒予算と出力サイズから)。G2日本語評価で精度問題が
    あれば調整(adjustはプロバイダ1箇所)
9. **(ws-6引継ぎ)JevWorkerの再利用**: `handle(intent_id)` は起点非依存のIFにしてある。
    Bucket再評価・catch-upスキャンから同一部品を呼ぶ(reevalガードは入口で共用 —
    ws-4報告書§5-7)。latch_score計算・保留キューはws-6
10. **(ws-7引継ぎ)K_j配分の拡張点**: select_jev_targetsの純関数とK_J/ONE_ON_ONE_MIN定数を
    用意した。グループ由来ペア(規則2〜4)・未判定ペア継続優先・group_candidates保持はws-7
11. **(ws-8引継ぎ)circuit breaker接続点**: judge_pairの第一候補呼び出しを包む形で
    ブレーカを注入する構造にしておく(§2.2)。開放中の第一候補停止・半開の1リクエスト
    試験・p95レイテンシ測定(窓1分)はws-8
12. **(先送り記録)フォールバック切替の連続時コスト**: 切替長期化は同一回数でコスト増
    (T1 v0.2試算: 全件長期化で約$1,915/月)。80% alertの内訳レポート(ws-4実装済み)で
    観測する。しきい値での自動繰上げはG2後のオーナー判断(09 §4)
