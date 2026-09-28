# M2 ws-2(Embedding Worker)設計メモ

- 作成: 2026-09-29(agent1)
- 対象: STATUS.md M2作業単位表 ws-2(出典 M2-2 / 07 §3・06 §9・06 D-15・05 §2・04 §5)
- 前提: ws-1マージ後main(マージ0bb6a2d・test-ci 677 passed・alembic 0002=head・pubsubエミュレータ常設)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

ws-1が開通させたイベント経路(作成・更新Event → Stage1処理)にEmbeddingの実体を載せる。
(1) 正規化テキスト生成(raw_text不使用)、(2) LLM Gateway Embedding系統のreal化
(gemini-embedding-001)、(3) intents.embedding/embedding_model書き込み、(4) 派生イベント
embedding_completedの発行、(5) 失敗Intent(embedding NULL)のバックフィル、である。
embedding_completed受信時の第2段(Layer 1〜5)はws-3が並走で実装するため、本単位は
「embedding_completedが発行され、ws-3が消費できる状態」を完成ラインとする。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | Embedding対象テキストは構造化データから次の形式で生成(raw_textは使わない): `{category.primary} / {時間帯の表現(例: 平日夜20-23時)} / {location.name} / {人数の表現} / {soft_constraintsを「・」で連結}` | 07 §3 |
| 2 | モデルは04 D-14基準を満たす多言語対応の埋め込みモデル。次元は768(05のvector(768))。モデル識別子と版をintents.embedding_modelに記録(01 §18)。モデル変更時はこの記録で再エンベディング対象を特定してバックフィル | 07 §3・05 §2・01 §18 |
| 3 | Embeddingの呼び出しはtimeout 2秒・再試行なし。失敗時は「保存は行い、回復後にバックフィル、完了時にembedding_completedで復帰」 | 07 §1・07 §3・06 §1(層別予算Embedding区間≤2秒) |
| 4 | 第2段トリガー: Embedding WorkerがLLM Gateway経由でEmbedding APIを呼び、intents.embeddingとembedding_modelを書き込んだうえで、派生イベント`embedding_completed`(match_eventsに記録)を発行する。Matching WorkerがCandidate Retrievalからパイプラインを実行する | 06 §9・06 §1 |
| 5 | embedding_completedのidempotency keyは(embedding_completed, source_intent_id, version)。version検査は第1段と同一(payload<現行は破棄・>現行は再試行) | 06 §9 |
| 6 | Embedding要求のキックはintents.embedding IS NULLのときのみ行う。embeddingが既に存在する場合(resume由来のversion+1 Event — テキストは不変のため再エンベディングは不要)はEmbeddingをスキップし、第2段相当のパイプライン投入へ直接進む | 06 §9 |
| 7 | 呼び出しタイミングはIntentのactive作成・active化(draft→active)・active更新後(イベント駆動)。draft状態では呼び出さない | 07 §1 |
| 8 | Embedding失敗のIntentはembedding=NULLで保存し、回復後に一括再エンベディングのバックフィルを行い、それまで候補の生成元にも対象にもしない。バックフィル完了時はembedding_completedを発行してパイプラインへ復帰。Embedding未完了(実行待ち)は縮退ではなく第2段トリガーの到着待ち | 06 D-15・06 §3 |
| 9 | draftはEmbeddingしない(生成元にも対象にもならない)。draft→active化の検証通過時にembeddingを確定し、初回投入は作成種Eventと同一の経路 | 06 §3・06 §9-0・05 §5 |
| 10 | 送信データは正規化テキストに限り、raw_textを外部へ送る経路を持たない。LLM Gateway経由で呼び出し、送信先・データ種別・時刻を記録 | 01 §21・07 §1・08 §3 |
| 11 | 作成Eventは窓なし即時・更新Eventのみ10秒トレーリング窓で統合(ws-1実装済み)。初回投入Eventの計測起点はactive化の保存コミット時点 | 06 §9・06 §1 |
| 12 | Embeddingプロバイダ=Google Gemini API有料tier・gemini-embedding-001。768は「推奨値に明記・パラメータ指定で768が取れる」・有料tierで学習不使用・入力上限2,048トークン | T1 v0.2 §2.4・§3 |
| 13 | T1コストモデルのEmbedding回数の内訳は「Active作成・active化・active更新」(月次5万回・1回あたり約100トークン・約$0.75/月) | T1 v0.2 §4 |
| 14 | intents.embedding=vector(768) NULL=未完了または失敗 / embedding_model=text モデル識別子+版。HNSW cosine索引あり | 05 §2・05 §3 |
| 15 | テスト環境は本番と同じ種類で規模縮小。時刻操作はClock経由。テスト用パブリッシャーでembedding_completedを手動発火できる | 10 §1 |
| 16 | outbox(match_events)→publish→フォールバックリレー(30秒超pending行の再publish)・Worker内subscribe/debounce・冪等受領(ON CONFLICT DO NOTHING)はws-1実装済みの経路をそのまま使う | M2 ws-1(06 §9の実装) |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

**呼び出し側(本単位が実体を置く)**

- `worker/stage1.py`: created/updated処理時に`embedding_hook`(予約・None)を呼ぶ位置がある
  (§2.1で本設計はこのフックを使わない判断を述べる)。embedding_completedは6値のevent_type
  として受信時にprocessedで閉じる(第2段の実体はws-3)
- `worker/main.py`: `Worker._dispatch`(intake→ack)と`_on_release`(debounce解放)が配線点。
  bus/engine/stage1/debouncerは注入可能・未注入はrun()で構築
- `intents/events.py`: outbox INSERT(pending)と5種定数。**embedding_completed定数の追加は
  本単位のスコープ(ws-1報告書どおり)**

**LLM Gateway(M0 ws-2実装・本単位でEmbedding系統のみreal化)**

- `llm/gateway.py`: `embed_intent(text, intent_id)`実装済み・`TIMEOUT_EMBEDDING_S=2.0`
  (asyncio.timeoutでwrap・送信記録→例外送出の順)・`build_llm_gateway`はParser系統のみreal
  (llm_mode="real"・Embedding/Jevはstub継続の現契約)
- `llm/stub.py`: StubLLM.embed=入力テキスト由来の決定的768次元ベクトル・
  `fail_embedding`/`delay_embedding_ms`フラグあり(試験でそのまま使う)
- `llm/providers.py`: EmbeddingProvider ABC・`EMBEDDING_DIMENSIONS=768`

**その他**

- `intents/store.py` `_UPDATE`(全置換UPDATE・version+1と同一SQL): §2.3で2行追記する
- `core/clock.py`: `JST`(固定+9)・FakeClock(set/advance)
- `.env`の`LATCH_GEMINI_API_KEY`実値設定済み(スーパーバイザー確認・make g1-gateと同じ
  `uv run --env-file ../.env`経路でのみ渡す。ciのcomposeへは渡さない)

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

1. **embedding_completed受信時の第2段実行**(Layer 1〜5・embedding IS NULL再検査5回→隔離):
   ws-3。stage1のembedding_completed受信時処理は現状(processed)のまま変更しない
2. **モデル変更時のバックフィル**(embedding_model不一致対象の再エンベディング): 07 §3の
   規定はモデル切替が発生した時点の設計課題。本単位のバックフィル対象は「実行失敗=embedding
   NULL」に限る(STATUS ws-2行どおり)。ws-3がfixtureへ直書きするembedding(embedding_model
   NULL)を誤って再エンベディングしない\ためでもある
3. **実GCP Pub/Sub・staging構成**: ws-1の抽象(設定切替)のまま
4. **Embedding精度ゲート**(G1/G2相当): docsに規定なし。埋め込み品質はLayer 3/4のスコアと
   G2日本語評価が間接的に担保する
5. **バックフィルの動的レート調整・per-intentリトライ状態管理**: 06 §9-10と同じく計測後の調整課題

## 2. 実装方式の選択肢と推奨

### 2.1 Embedding実行の配置 — 推奨: Stage1コミット後のWorker配線(worker/main.py追記)。Stage1フックは不使用

| 選択肢 | 評価 |
|---|---|
| A. ws-1予約の`embedding_hook`へ実体を注入 | フックは`Stage1._process_once`の**DBトランザクション内**で呼ばれる。(1)外部API呼び出し(timeout 2秒)をトランザクションオープン中に行うか、hook内で別トランザクションをネストする形になる。ネストはversion>current再読込経路でStage1側がintents行をFOR UPDATEで保持したままhook側が同一行をUPDATEしてブロックする自己デッドロックの可能性がある(到達稀だが構造的欠陥)。(2)埋め込み失敗を例外で伝播させるとStage1の再試行5回またはackなし再配信が起こり「再試行なし」(確定値#3)に違反する。握ってvoidを返す実装にしても、stage1が行をprocessed/duplicateへ遷移させた後の再キック経路がない |
| **B. Worker配線でStage1コミット後に実行(推奨)** | `Worker._dispatch`(即時種別)と`_on_release`(debounce解放)で、stage1の処理コミット後・ack前に`EmbeddingWorker.handle`をawaitする。API呼び出しはStage1のトランザクション外。stage1.pyは無変更 |

推奨Bの補足:

- **kickはackの前に完了する**(06 §9「作成・更新Eventの処理はEmbedding要求のキックまで」)。
  クラッシュでhandle未了ならackされず再配信され、冪等なhandleが回収する
- **duplicate経路でもhandleを呼ぶ**: stage1コミット〜handle完了の間(埋め込み実行中=最大2秒)の
  クラッシュは再配信ではduplicateになるため。handleは冪等(§2.2のガード)なので、duplicateでの
  再実行はバックフィルを待たない即時回収になる
- フック(ws-1予約)は未使用のまま残す。ws-1設計§2.5は「フックの実体はws-2」とだけ定め、実体の
  置き場所までは指定していない。トランザクション内呼び出しという実装上の制約が「再試行なし」
  と両立しないため、配線位置をWorkerへ移す判断は同設計の趣旨の範囲内と判断する

### 2.2 Embedding実行のトランザクション形態 — 推奨: 2フェーズ(read → API → ガード付きwrite)

```text
EmbeddingWorker.handle(intent_id, version)
  フェーズ1(短トランザクション):
    SELECT version, status, (embedding IS NOT NULL) AS has_embedding,
           category_primary, structured_data, participants_min, participants_max,
           time_start, time_end            -- raw_textは選択しない(確定値#10)
    FROM intents WHERE id = :id
      行なし / version ≠ 引数version        → no-op return(新versionのEventが担当・旧Eventの焼き直し)
      status ∉ {active, paused}            → no-op return(削除済み等。Layer 1〜2の対象外)
      has_embedding = true                 → embedding_completed行INSERT(ON CONFLICT DO NOTHING)
                                              のみ=第2段相当へ直接投入(確定値#6。resume等)
      has_embedding = false                → フェーズ2へ
  フェーズ2(API呼び出しはトランザクション外):
    text = 正規化テキスト生成(§2.4)
    vec = await gateway.embed_intent(text, intent_id)   -- timeout 2秒・再試行なし
      LLMError(timeout/provider)          → ログ警告のみでreturn(例外にしない)。
                                             embeddingはNULLのまま=バックフィル対象(確定値#8)
      len(vec) ≠ 768                      → 同上(契約違反として失敗扱い・ERRORログ)
    UPDATE intents SET embedding = CAST(:vec AS vector), embedding_model = :model,
           updated_at = :now
    WHERE id = :id AND version = :version AND embedding IS NULL
    RETURNING id
      0行                                  → no-op return(並行handleが先に書いた/更新が入った)
      1行                                  → embedding_completed行INSERT(ON CONFLICT DO NOTHING)
  コミット後: bus.publish_match_event(embedding_completed, intent_id, version)
      失敗は握る(ログ)→フォールバックリレーがpending行を30秒後に回収(ws-1と同一)
```

- **embedding_modelに書く値**: EmbeddingWorkerのコンストラクタ引数`model_id`
  (既定=GEMINI_EMBEDDING_MODEL="gemini-embedding-001"定数。設定には出さない — docs確定値
  でありenvで黙って変えられる経路を作らない。Timeoutsと同じ規律)。llm_mode=stubでも同一
  ラベルを書く — 同列は本番real経路での再エンベディング特定(01 §18)にのみ意味を持ち、
  stubでの値は試験の期待値としてのみ使われる
- **ガードの根拠**: versionは内容変更(update)と同一トランザクションで+1されるため、
  `WHERE version = :version`は「フェーズ1で読んだ内容が現行のままであること」を保証する。
  `AND embedding IS NULL`は二重書き込みを排除する。CAST(:vec AS vector)の明示CAST形式は
  M1 ws-3のbind paramリテラル落ち事故(`::`短縮CAST)の回帰予防と同じ流儀
- 選択肢との比較: 1トランザクション(SELECT→API→UPDATE→INSERT→コミット)は原子性が高いが、
  API呼び出し(最大2秒・stub遅延注入も可)をDB接続保持・行ロックの下で行う。2フェーズは
  「読み取りは参考情報・正しさはUPDATEのガードが担保」であり、外部I/Oをトランザクション外に
  出せることを優先する
- トレードオフ(受け入れるもの): フェーズ1〜2の間に同一intentへ並行handle(再配信・バックフィル
  の重なり)が走るとAPI呼び出しが二重に発生し得る(at-least-onceの帰結。片方はUPDATE 0行で廃棄)。
  コスト影響は1回分(約100トークン×$0.15/MTok≈$0.000015)で無視できる。ストリーミングpullの
  callbackは`run_coroutine_threadsafe`で並行投入されるため、並行handleは構造的に起こり得る

### 2.3 内容更新時の再エンベディング — 推奨: store._UPDATEでembedding/embedding_modelをNULLクリア

**論点**: 06 §9は「キックはembedding IS NULLのときのみ。既存の場合(resume由来 — テキストは
不変)はスキップ」と定める。activeな内容更新(PATCH全置換・version+1)はテキストが変わり得るが、
docsのどこにも「更新時にembeddingをNULLへ戻す」規定がない。クリアしなければ更新後のIntentは
常に旧ベクトルで検索され、Layer 2の意味が崩ける(06 §3の趣旨に反する)。

**判断**: クリア方式を採る。根拠はdocs内に3つある:

1. **T1 v0.2 §4のコストモデルがEmbedding回数を「Active作成・active化・active更新」と数えて
   いる**(確定値#13)— 内容更新の再エンベディングを織り込み済み
2. 06 §9の「embedding既存=テキスト不変(resume)」という例示が成り立つ前提は
   「embedding存在⇔テキスト不変」。この不変条件を内容更新で保つにはクリア以外に手段がない
3. 05 §5「draft→active受理時にgeo_center・embeddingを確定」— 内容確定の遷移でembeddingを
   確定するパターンが既にある

**実装**: `intents/store.py`の`_UPDATE`へ`embedding = NULL, embedding_model = NULL`の2行を
追記する。`_UPDATE`は全置換UPDATEであり、全呼び出し経路で一貫して作用する:

| 経路(service.py) | クリアの効き |
|---|---|
| active/paused内容更新(:610・version+1・updated Event) | 既存embedding→NULL→更新Eventのキックで再エンベディング |
| draft再保存(:658・Eventなし) | draftはembedding NULLのためno-op |
| draft→active化(:695・created Event) | 同上no-op(初回埋め込みはcreated経路) |
| resume(:800は`update_status`を使用) | **クリアされない**=06 §9のresume規定どおり |

トレードオフ: visibilityなどテキストに関係しない項目のみの更新でも再エンベディングされる
(テキスト同一でも1呼び出し)。T1コストモデルが更新を回数に含めるためコスト試算と整合し、
影響は1回分で無視できる。「テキスト要素の差分比較でクリアを条件付ける」実装は正規化テキスト
導出をservice経路へ逆輸入する構造になるため採らない。

**docs改版候補の申告**: 本判断は確定値の組み合わせから導いた実装解釈であり、docsに一文言が
ない。05 §5のPATCH行または06 §9への注記を改版候補としてsupervisorに申告する(本単位では
docs無改版で進める。§2.9)。

### 2.4 正規化テキストの導出規則(07 §3形式の実装解釈)

07 §4の規律(算術・日付処理はコード側で行う・モデルに計算させない)に準じて、各要素を
決定的な純関数で導出する。**帯区分表はdocsに規定がなく実装定義**(07 §3の例示との整合のみが
拘束)。埋め込みは一貫性が効くため、固定表を採用し全文をテストでピン留めする:

| 要素 | 導出規則 |
|---|---|
| {category.primary} | intents.category_primary列 |
| 時間帯 | time_startをJST(core.clock.JST・固定+9)へ変換し、(a)曜日: 月〜金=「平日」・土日=「週末」 (b)帯: 開始時刻hで 5≤h<11=「朝」/11≤h<16=「昼」/16≤h<19=「夕方」/19≤h<23=「夜」/その他(23・0〜4時)=「深夜」 (c)時刻: `{start.hour}-{end.hour}時`(time_endのJST時刻。time_end NULL例: `{start.hour}時以降`)。例: 月曜20:00〜23:00 → 「平日夜20-23時」(07 §3の例と一致) |
| {location.name} | structured_data.location_name(M1 ws-3 supervisor承認済みキー) |
| 人数の表現 | participants_min == participants_max → `{N}人` / 異なれば `{min}-{max}人` |
| soft_constraints | structured_data.soft_constraints[].textを「・」で連結(downgraded_from_ng=trueの降格条件も含む。06 §3「soft constraintsの文言」。Layer 3の語彙重なり除外判定はLayer 3側の責務) |

- 空要素は省略し、非空要素を「 / 」で連結する(soft_constraints空 → 末尾の区切りが残らない)
- 形式に含まれないもの: raw_text(確定値#10)・visibility・notification_level(07 §4と同じ
  位置づけ: 表示・通知の制御であり判定材料でない)・budget・alcohol_involved・category_secondary
- time_start NULL(.active/pausedでは起こらない想定・05 §2)は時間帯要素全体を省略する
- 実装は`worker/embedding_text.py`の純関数とし、入力dataclassにraw_textを持たせない
  (構造的にraw_textが経路に入らないことのピン。確定値#10)

### 2.5 バックフィル(D-15)— 推奨: Worker内の周期タスク。対象=embedding IS NULL かつ status='active'

**起動方式の選択肢**:

| 選択肢 | 評価 |
|---|---|
| A. 手動CLI/管理コマンド | D-15の「回復後に」を検知する運用者がいる前提になる。自動回復を欠く |
| **B. Worker内の周期タスク(推奨)** | 周期ごとに対象を抽出してhandle()へ渡す。プロバイダ障害中は失敗がNULLのまま残り、次周期で自然に再試行される=「回復後の一括再エンベディング」を自動達成。WorkerはEmbeddingの実行者(06 §1の経路)でもある |
| C. APIプロセス内(フォールバックリレーと同じ) | APIはEmbeddingを実行しない。役割不一致 |

**対象抽出**: `WHERE embedding IS NULL AND status = 'active' ORDER BY updated_at LIMIT :batch`

- status='active'のみ: Layer 1〜2の対象はactive(06 §3・05 §3の部分Index)。pausedは
  resume時のキック(handleのIS NULL判定がversion合致で働く)で回収され、expired/cancelled/
  matchedは再エンベディングの価値がない
- embedding IS NULLのみを対象とする(§1.4-2): ws-3がfixture直入れするembedding
  (embedding_model NULL)を誤って再エンベディングしないための防線でもある
- ORDER BY updated_atで古い失敗から。LIMITが1周期あたりのAPI消費上限になる

**初期値(設定化)**: 周期`embedding_backfill_interval_sec=300`・バッチ
`embedding_backfill_batch_limit=50`。初回は周期待ちから開始する(sleep-first)。
理由: (1)起動直後のバースト回避、(2)ws-3の統合試験(テスト内Worker起動時に直撃runすると
fixtureへ干渉し得る。test-ci中は常設worker停止だがテスト内Workerにも同じ構成を使うため)。
回復レイテンシ=周期300秒は初期LATCH判定p95 10秒(06 §1)の対象外(確定値#8の復帰経路)。

**ループ停止**: Workerのstop Eventと同じものを共有し、`asyncio.wait_for(stop.wait(),
timeout=interval)`で即応する(300秒のsleep中でもshutdownに追従)。

### 2.6 Gemini実プロバイダ — 推奨: google-genai SDK・明示api_key・SDK再試行の明示無効化

- **SDK**: `google-genai`(Python公式統一SDK。旧google-generativeaiではない)。依存へ追加
- **呼び出し**: `client.aio.models.embed_content(model="gemini-embedding-001",
  contents=text, config=types.EmbedContentConfig(output_dimensionality=768))` →
  `response.embeddings[0].values`(list[float])
- **鍵の明示渡し**(ANTHROPIC_BASE_URL事故の教訓・supervisor指示どおり): SDKは環境変数
  GEMINI_API_KEY/GOOGLE_API_KEYを自動採用するため、`genai.Client(api_key=...)`へ設定値を
  明示渡しする(暗黙の環境採用を排除)。settingsへ`llm_gemini_api_key`(alias
  LATCH_GEMINI_API_KEY・llm_anthropic_api_keyと同じAliasChoices形式)を追加
- **timeout・再試行**: Gatewayのasyncio.timeout(2.0)に加え、SDK側を
  `types.HttpOptions(timeout=2000, retry_options=types.HttpRetryOptions(attempts=1))`で
  構成する。**SDK既定ではtenacityによる再試行(_RETRY_ATTEMPTS)が入り得るため、明示的な
  1試行化が「再試行なし」(確定値#3)の必須条件**。timeout値はTIMEOUT_EMBEDDING_Sと同値
  (anthropic.pyのANTHROPIC_PARSER_TIMEOUT_Sと同じ規律。同値性はunit試験でピン)
- **次元検証**: len(values)==768をprovider内で検証(768はパラメータ指定・T1 §2.4)。不一致は
  LLMProviderError(=失敗。D-15経路。恒常的な不一致は実装不整合でありERRORログで表面化)
- **送信記録**: Gateway経由のため既存SendRecord(system="embedding"・destination="google"・
  intent_ids=[intent_id])が自動出力される(確定値#10)。providerのname="google"
- **例外**: SDKのerrors.APIError等はこの層で握らず素通り — Gatewayの既存wrap
  (LLMTimeoutError/LLMProviderError)と送信記録が最終関門(anthropic.pyと同じ)

### 2.7 real化の検証 — スモークハーネス(make embed-smoke)

ci(compose)のworkerはllm_mode=stubのまま(鍵を渡さない)のため、real経路はスモークで初回検証
する(ws-6の401事故の教訓: 実APIを叩くまで分からない契約ズレの検出)。

- `make embed-smoke`: `cd backend && uv run --env-file ../.env python -m latch.llm.embed_smoke`
  (g1-gateと同じ.env経路)
- 内容: build_embedding_gatewayでGateway構築(llm_mode=real・fail-fast確認)→固定サンプル
  テキスト1件(07 §3形式)をエンベディング→768次元・レイテンシ・モデル名を出力→exit 0/1
- コスト1呼び出し(約$0.000015)。SendRecordも通常どおり出力される

### 2.8 Gateway構成の拡張 — 推奨: build_embedding_gatewayを新設(worker・スモーク用)

| 選択肢 | 評価 |
|---|---|
| A. build_llm_gatewayのreal定義を「Parser+Embedding real」へ拡張 | APIプロセス(g1-gate)はEmbeddingを呼ばないのにgemini鍵が必須になり、既存契約(M1 ws-6承認)を変える |
| **B. build_embedding_gatewayを新設(推奨)** | worker・スモーク専用の構築関数。llm_mode=stub→3系統stub / llm_mode=real→**embeddingのみGemini実**(parser・jevはstub)・gemini鍵欠落はfail-fast(ValueError)。build_llm_gatewayは無変更 |

Gateway本体(embed_intent・_call・SendRecord・Timeouts)は無変更。本単位は
「系統の差し替え単位=プロバイダ」(M0 ws-2設計・04 D-14基準6)のとおり、Embedding系統の
差し替えのみを行う。Jev系統のreal化はws-5が同じ形で追加する。

### 2.9 マイグレーション・docs改版 — 追加なし

- alembic: 追加なし(0002がheadのまま)。intents.embedding/embedding_model・match_events・
  UNIQUE索引はすべて0001で作成済みであり、本単位の全機能が現スキーマで成立する
- docs 01〜12: 改版不要。ただし§2.3(内容更新時embeddingクリア)は実装解釈のため、
  05 §5/06 §9への一文言追記を**改版候補としてsupervisorへ申告**する(判断はsupervisor)

### 2.10 採用しないもの(YAGNIによる切り捨て一覧)

1. **モデル変更バックフィル**(embedding_model不一致の再エンベディング)— モデル変更未発生
   (07 §3は切替時の規定。§1.4-2)
2. **Embedding呼び出しの同時実行セマフォ** — 正しさはversion+IS NULLガードが担保。
   同時実行数の制御はWorker水平スケール(04 §6)の領域
3. **バックフィルの指数バックオフ・per-intentリトライ状態** — 周期タスクの自然再試行で十分
   (06 §9-10と同じく計測後に調整)
4. **テキスト要素の差分比較によるクリア条件付け** — §2.3のトレードオフ参照
5. **embedding_completedの手動発行CLI** — テストパブリッシャー(ws-1のbus直publish・10 §1)
   で足りる
6. **Embedding精度ゲート** — docs規定なし(§1.4-4)
7. **新マイグレーション**(バックフィル管理表・published_at列等)— ws-1と同じ判断

## 3. ファイル構成

### 3.1 作るもの

```text
backend/src/latch/llm/gemini.py
    # GeminiEmbeddingProvider(google-genai・明示api_key・HttpOptionsでtimeout=2000ms・
    # retry attempts=1・output_dimensionality=768・768検証・name="google"・
    # inject client対応)。GEMINI_EMBEDDING_MODEL="gemini-embedding-001"・
    # GEMINI_EMBEDDING_TIMEOUT_S=2.0(TIMEOUT_EMBEDDING_Sと同値ピン)
backend/src/latch/llm/embed_smoke.py
    # 実APIスモーク(python -m latch.llm.embed_smoke。§2.7)
backend/src/latch/worker/embedding_text.py
    # 正規化テキスト導出の純関数+帯区分表(§2.4)。入力dataclassはraw_text非保持
backend/src/latch/worker/embedding.py
    # EmbeddingWorker.handle(§2.2の2フェーズ・ガード付きUPDATE・embedding_completed行
    # INSERT(ON CONFLICT DO NOTHING)・コミット後publish)。SQLはtext()生SQL・
    # 時刻はClock明示値(Stage1と同じ規律)
backend/src/latch/worker/backfill.py
    # BackfillRunner(run_once=対象抽出+handle委譲・run=stop応答の周期ループ・§2.5)
backend/tests/unit/test_llm_gemini.py
backend/tests/unit/test_worker_embedding_text.py
backend/tests/unit/test_worker_embedding.py        # handleの冪等・失敗経路・配線を含む
backend/tests/unit/test_worker_backfill.py
backend/tests/unit/test_embedding_clear_on_update.py
backend/tests/integration/test_embedding_pipeline.py
```

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更 |
|---|---|
| `backend/src/latch/intents/events.py` | `EVENT_EMBEDDING_COMPLETED = "embedding_completed"` 定数の追記(ws-1報告書どおり本単位スコープ) |
| `backend/src/latch/intents/store.py` | `_UPDATE`のSET句へ`embedding = NULL, embedding_model = NULL`の2行(§2.3) |
| `backend/src/latch/llm/gateway.py` | `build_embedding_gateway`の新設(追記)。既存のbuild_llm_gateway・各系統メソッドは無変更 |
| `backend/src/latch/llm/__init__.py` | `GeminiEmbeddingProvider`・`build_embedding_gateway`のexport追記 |
| `backend/src/latch/worker/main.py` | Workerへ`embedding`/`backfill`のオプション注入+run()での未注入時構築(gateway=build_embedding_gateway)+`_dispatch`/`_on_release`へのhandle呼び出し挿入+backfillタスク起動/停止。**既存行の変更をしない追記(§3.4)** |
| `backend/src/latch/settings.py` | `llm_gemini_api_key`(AliasChoices LATCH_GEMINI_API_KEY)・`embedding_backfill_interval_sec=300`・`embedding_backfill_batch_limit=50`(末尾へ追記) |
| `backend/pyproject.toml` | 依存へ`google-genai`を追加 |
| `Makefile` | `embed-smoke`ターゲット(追記) |
| `backend/tests/integration/test_events_pipeline.py` | **期待値の機械的追従のみ**(§3.4): ws-2によりcreated/updated処理でembedding_completed行が追加発生するため、行数・絞り込みの期待値が変わる箇所の修正(試験の意図は変えない) |

### 3.3 触らないもの

- `backend/src/latch/worker/stage1.py` — **無変更**(§2.1-B。ローカル定数
  `_EVENT_EMBEDDING_COMPLETED`・未使用のembedding_hookはそのまま残す。ws-3が
  embedding_completed受信時の第2段を独立に設計できる状態を保つ)
- `backend/src/latch/intents/service.py`・routes.py・schema.py・mapping.py・completion.py・
  prompt.py(§2.3のクリアはstore._UPDATE内で完結するためserviceは無変更)
- `backend/src/latch/events/`(bus.py・pubsub_bus.py・relay.py — ws-1の経路をそのまま使用)
- `backend/src/latch/llm/`の既存ファイル群の既存行(stub.py・records.py・anthropic.py・
  providers.py・errors.py・gateway.pyの既存メソッド)
- `backend/alembic/`(0002がheadのまま)・`compose.yaml`(ci workerはstubのため鍵渡し不要)
- auth / users / ratelimit / geo / g1gate / frontend / prototype / docs 01〜12

### 3.4 競合回避方針(ws-3並走・supervisor指示)

交差ファイル(worker/main.py・intents/service.py・tests)への追加は「既存行を変更しない追記」
を基本とする。具体的には:

1. **worker/main.py**: (a)`__init__`への引数追加は引数並びの**末尾**に限る(default付き)。
   (b)run()内の構築・タスク起動は既存行の**後ろへの追記**。(c)`_dispatch`/`_on_release`への
   handle呼び出しは**既存行の間への純挿入**(既存行の変更・削除はしない)。ws-3のstage2配線も
   同一規約で行えば、マージ時の競合は「両側追記保持」(STATUSのsettings.py前例と同じ)で解消できる
2. **intents/service.py**: 本設計では**無変更**(§2.3のクリアはstore._UPDATEの2行挿入で
   完結。store.pyはws-3の設計対象外を想定するが、万一触れても別ブロック)
3. **tests**: 新規ファイルのbasename一意(運用ルール5。`test_embedding_pipeline.py`など
   ws-3のLayer試験と重複しない名称)。**test_events_pipeline.pyの期待値追従**は発生が
   構造的に不可避(embedding_completed行の追加)であり、修正は期待値の値に限定して試験意図を
   変えない。この追従はws-3(stage2実装時)でも再度発生する前提で、supervisorの機械的追随
   許可(ws-5の契約カウンタ前例)を求む
4. **マイグレーション**: 追加なし(§2.9)。共有ci-dbのalembic_version取り合い
   (STATUS運用ルール1)は本単位では発生しない
5. マージはsupervisorが直列実施(運用ルール2)

## 4. テスト方針

### 4.1 unit(`make test`。外部プロセス不要・FakeClock+スタブで決定的)

| 対象 | 内容 |
|---|---|
| embedding_text | 07 §3例の再現(月曜20:00-23:00 → 「平日夜20-23時」)。帯表の各帯(朝/昼/夕方/夜/深夜)と境界時刻。平日/週末。日跨ぎ(endが翌日)。人数(min==max/範囲)。soft_constraints空・複数・downgraded込みの連結。location_name欠損・time_start NULLの要素省略。区切り「 / 」の仕様(空要素で区切りが残らない)。**入力dataclassにraw_textがないことのピン(確定値#10)** |
| worker/embedding(handle) | (a)embedding NULL→embed呼び出し(引数text=期待する正規化テキスト)→ガード付きUPDATE→embedding_completed行INSERT→コミット後publishの順序。(b)embedding既存→Gateway不呼び出し+イベント行+publish(resume直接投入=確定値#6)。(c)version不一致・status非対象・行なし→no-op。(d)LLMTimeoutError/LLMProviderError→例外なし・書き込みなし・イベント行なし(D-15)。(e)768以外の応答→失敗扱い。(f)UPDATE 0行→イベント行もpublishもなし。(g)publish失敗→握って例外なし(行はpending=リレー回収)。(h)**SELECT文がraw_textを含まないことのSQLピン** |
| worker/backfill | 対象抽出SQL(embedding IS NULL AND status='active'のみ・ORDER BY updated_at・LIMIT)。handleへの委譲。項目単位の例外握りと継続。stop応答ループ(FakeClockでなくsleep注入) |
| llm/gemini | 構成ピン(明示api_key・HttpOptions timeout=2000/retry attempts=1・output_dimensionality=768)。embed呼び出しパラメータ(inject clientスタブでmodel・contents・configを検証)。鍵空→ValueError(fail-fast)。SDK例外の素通り。768検証 |
| Gateway構成 | build_embedding_gateway: stub→3系統stub / real+gemini鍵→embedding=Gemini・parser/jev=stub / real+鍵なし→ValueError |
| clear_on_update | `_UPDATE`がembedding/embedding_modelをNULL化すること(SQL文字列ピン+スタブconnの実行パラメータ検証) |
| worker/main配線 | _dispatch: processed/duplicate × created/updated → handle呼び出し。deleted/expired/scheduled/embedding_completed → 呼ばない。_on_release: 最新versionの処理後にhandle。handleの例外でackしない(再配信回収) |

### 4.2 integration(`make test-ci`。実エミュレータ+実DB・Gateway=stub)

ws-1のtest_events_pipeline.py構成(worker停止・テスト内Worker=実bus+FakeClock+注入部品・
試験専用subscription)を踏襲する。GatewayはStubLLMラップのカウントスタブ(呼び出し回数・
渡されたtextを記録)を注入する。

| 試験 | 内容 | 対応確定値 |
|---|---|---|
| E2E(作成) | POST /v1/intents(active)→intents.embedding NOT NULL・embedding_model="gemini-embedding-001"・embedding_completed行processed。**Gatewayへ渡されたtextが正規化形式でraw_textを含まないこと**(実経路での#10確認) | #1・#2・#4 |
| 内容更新 | PATCH→embedding NULL化(§2.3)→debounce 10秒→再エンベディング→embedding_completed(v2) | #6・#13 |
| resume | pause→resume(version+1)→埋め込み再実行なし(カウントスタブ)・embedding_completed(新version)発行 | #6 |
| 失敗→バックフィル | fail_embedding=Trueスタブ→embedding NULLのまま・created行processed・embedding_completed行なし→backfill.run_once(成功スタブへ差し替え)→埋め込み+embedding_completed行 | #3・#8 |
| draft | draft作成→埋め込みなし・Eventなし→active化(PATCH)→埋め込み+created経路のembedding_completed | #7・#9 |
| 削除 | DELETE API→handleしない・match_candidates無効化はws-1どおり | 06 §1(ws-1再確認) |
| 重複投入 | 同一created Event 2回publish→埋め込み1回(2回目はduplicate→handle冪等でAPI不呼び出し) | #5・#16 |

### 4.3 本単位では実施しない

- 実APIスモーク(make embed-smoke・§2.7)はsupervisor実行(実値・契約の確認領域)
- embedding_completed受信後のLayer 1〜2実行・embedding IS NULL再検査(ws-3)
- 冪等性G2試験(match_candidates二重生成・10 §4.7)はws-8。本単位は「embedding_completed
  発行1回(重複受領は処理1回)」の土台のみ検証

## 5. 未解決の論点・実装時確認事項

**設計判断として未解決のものはなし**。主要論点(実行配置・トランザクション形態・内容更新時の
再エンベディング・バックフィル起動・SDK再試行無効化)はいずれもdocsの確定値を根拠に§2で決定し
トレードオフを明記した。実装時の確認事項:

1. **google-genaiの実機挙動**(§2.6・§2.7): HttpRetryOptions(attempts=1)が意図どおり
   1試行となること・embed_contentの応答形式(embeddings[0].values)・タイムアウト時の例外型を
   スモークで最初に確認する( ws-6の401事故と同じ位置づけ。SDKバージョンは
   `google-genai>=1.33`を想定し、乖離があれば実装時計画書へ記録)
2. **バックフィル周期300秒・バッチ50**は初期値(06 §9-10と同じ位置づけ・計測後に調整)
3. **test_events_pipeline.pyの期待値追従範囲**(§3.4-3): 実装時に実際に赤化する箇所を
   最小限(期待値の値のみ)修正する。試験意図の変更が必要になった場合はその時点でsupervisorへ
   報告する
4. **docs改版候補**(§2.3): 「内容更新時のembeddingクリア」を05 §5または06 §9へ一文言として
   明記するかはsupervisor判断(本単位はdocs無改版で進める)
