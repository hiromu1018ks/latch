# M0 ws-2(LLM Gateway)設計メモ

- 作業単位: ws-2 — LLM Gateway: 3系統集約・送信記録・プロバイダ抽象・スタブ(応答記録+レイテンシ注入)(docs/plans/STATUS.md M0表・依存「雛形」)
- 作成: 2026-09-27(agent1 / superpowers:brainstorming使用)
- 次工程: 計画書(ws-2-plan.md)へ変換 → 実装エージェントがworktree内でTDD実装
- 実装言語: Python (FastAPI)(雛形d2b53d7の基盤の上に載せる)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

依存ハード制約**C4(LLM GatewayがParser/Embedding/Jevの単一共通経路。送信記録とプロバイダ抽象化はここで実装)**を解消する。3系統(Intent Parser・Embedding・Jev)の外部LLM呼び出しを単一Gatewayへ集約し、(1)送信記録(送信先・データ種別・時刻)、(2)プロバイダ差し替えの抽象化、(3)テストモード(記録済み応答スタブ+レイテンシ注入)をM0で作る。**実プロバイダ接続は作らない**(T1未確定。C11「Gateway抽象化によりプロバイダ未確定のまま実装は進行可能」・12 第4節「契約未確定プロバイダは技術基準を満たしても不採用」)。呼び出し側(Parser API=M1、Embedding Worker/Jev Layer 4=M2)はまだ存在しないため、本単位はGateway単体+unit試験で完了を証明する。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | Intent Parser・Embedding・Jevの外部送信は単一のLLM Gateway経由に集約し、送信先と送信データ種別の記録(01 第21節)と、プロバイダ差し替えの抽象化をGatewayで行う | 04 第2節構成の要点 / 12 第2節 C4 |
| 2 | 送信範囲は判定に必要な最小限(構造化データからの正規化テキスト)とし、raw_textを送信経路に入れない。送信先と送信データの種別を記録し、問い合わせ時に開示できる状態を保つ | 01 第21節 |
| 3 | LLM Gatewayは送信先・データ種別・時刻を記録する。この記録は**内容を含まない**(どのIntentの何種別をいつ送ったか)。問い合わせ時に開示できる状態を保つ | 08 第3節 |
| 4 | 構造化ログは許可リスト方式。出力できるフィールドを列挙型で固定(raw: intent_id / status / version / error_code等)。raw_text・geo座標・constraints系フィールドは不許可。例外・エラーはIDのみ | 08 第2.4節 |
| 5 | 3系統の呼び出し特性 — **Parser: 同期・timeout 10秒・再試行なし**(D-17。タイムアウト時は構造化フォームへフォールバック)/ **Embedding: 非同期・timeout 2秒・再試行なし**(失敗はD-15バックフィルで回収)/ **Jev: 非同期・timeout 6秒・出力検証失敗(JSON欠損等)のみ1回再試行、timeoutは再試行せず縮退へ**。いずれもLLM Gateway経由で呼び出し、送信先と送信データ種別を記録 | 07 第1節・07 第5節 D-17 |
| 6 | Parser入力は日本語の自然文(最大300字・アプリ層で事前検証)、出力は07 第2節のJSONスキーマのみ。現在日付({current_date})をプロンプトへ渡す | 07 第2節 |
| 7 | Embedding入力は構造化データから生成した正規化テキスト形式(raw_textは使わない)。次元は768(05のvector(768))。モデル識別子と版をintents.embedding_modelへ記録 | 07 第3節・05 第2節 / 12 第2節 C11 |
| 8 | Jev入力は2 Intentの正規化テキスト形式([hard]/[soft]タグ付き。visibility・notification_levelは含めない)。出力は07 第4節の7設問JSONスキーマのみ。出力のJSONスキーマ検証・スコア計算は検証を通った場合のみ実行 | 07 第4節 |
| 9 | Embeddingモデル確定がvector次元固定の前提。**ただしGateway抽象化によりプロバイダ未確定のまま実装は進行可能** | 12 第2節 C11 |
| 10 | LLM呼び出しはLLM Gatewayの**テストモード(記録済み応答を返すスタブ、またはレイテンシ注入付き)で決定的にする** | 10 第1節「疑似イベント注入」 |
| 11 | レイテンシ注入の目標値(性能試験の負荷条件): Jev p50 2秒 / p95 5秒、Parser p50 1秒 / p95 2.5秒(D-17の目標をそのまま破らない注入) | 10 第4.1節 |
| 12 | LLM障害試験は「LLM Gatewayにエラー注入(100%エラーおよびtimeout)」で行う(縮退・503/422切替・バックフィルの確認はM2以降の試験) | 10 第4.5節 |
| 13 | 複数プロバイダをGateway経由で併存させ、障害時の縮退(D-15)に備えられる(可用性基準)。契約面の足切り(学習利用禁止等6条)を先に行う | 04 第4節 D-14基準6・基準1 |
| 14 | M0スコープ5の文言: 「LLM Gateway: 3系統集約、送信先・データ種別・時刻の記録、プロバイダ差し替え抽象化、テストモード(記録済み応答スタブ+レイテンシ注入。10 第1節)」。G0完了条件: 「LLM Gatewayがスタブで3系統の呼び出しを記録できる」 | 12 第3節 M0 |
| 15 | Jev層のcircuit breaker(エラー率・レイテンシしきい値で開放)の初期値は06 D-15側で確定(実装はM2。12 M2スコープ10) | 07 第1節・12 第3節 M2 |
| 16 | 送信記録の時刻もすべてClock経由(時刻参照はClockインターフェース経由。arch testが `backend/src` 全体で強制) | 04 第5節(FR-41)・雛形 test_arch_no_direct_time |
| 17 | 計測基盤はCloud Monitoring+OTLP+ログ集計(Jev実行回数・latency・cost等の計測はM4 Observability) | 04 第3節・12 M4-5 |

### 1.3 Parser系統だけがユーザー生テキストを送る点の整理

01 第21節「raw_textを送信経路に入れない」は、**保存済みIntentデータを送る系統(Embedding・Jev)の規定**である(送信範囲=構造化データからの正規化テキスト)。Intent Parserはユーザー入力テキストを構造化データへ変換する仕事そのもの(07 第2節)であり、parseは保存を伴わないプレビュー(05 第5節 SP-4)なので、Parser系統の送信内容はユーザー入力テキストそのものになる。これは仕様の矛盾ではなく役割分担であり、Gateway設計上は**「3系統とも、送信記録に送信内容(テキスト)を一切含めない」**(確定値3・4)で一貫して機微を護る。本文の設計はこの理解に立つ。

### 1.4 スコープ外(後続単位へ渡すもの。ws-2では作らない)

- 実プロバイダadapter・httpx等のHTTPクライアント・API鍵管理 → **T1確定後**(M1以降の単位)
- 呼び出し側 — POST /v1/intents/parse(M1)、Embedding Worker・Jev Layer 4・circuit breaker・出力検証・再試行制御(M2。12 M2スコープ2・6・10)
- 正規化テキスト生成(07 第2〜4節の形式でテキストを組み立てる処理。呼び出し側の責務)
- Parser/Jev応答のスキーマ検証・スコア計算(07 第2節必須3フィールド判定=M1、07 第4節出力検証=M2)
- レイテンシ分布(p50/p95)模擬・トークン数・cost計上 → **M4**(10 第4.1節の分布注入・04 第3節計測)
- 送信記録の検索・開示UI(問い合わせ対応の実務。08 D-14の告知・同意とセットでM4)
- Pub/Sub・DB接続・Redis(M2以降・ws-1/ws-3)

## 2. 実装方式の選択肢と推奨

### 2.1 Gatewayの公開インターフェース — 推奨: 1クラス・系統別メソッド3つ

選択肢:

- **A(推奨). `LLMGateway` 1クラスに系統別メソッド3つ(parse_intent / embed_intent / judge_pair)**
- B. 系統別の汎用メソッド1つ(`call(system, payload)`)+呼び出し側で型判別
- C. 系統別Gatewayクラス3つ(ParserGateway / EmbeddingGateway / JevGateway)

推奨の根拠: C4の趣旨は「単一共通経路」であり、単一クラス(+単一の送信記録経路)がそれを最も直接的に表す。Bは呼び出し側がsystem文字列と生payloadを扱うことになり、型の保護(後述の入力型)と送信記録のsystem属性付与を呼び出し側に委ねることになる。Cは共通化(記録・timeout・将来のcircuit breaker)を3クラスに複製するか、基底クラスを1つ作る結果になり、実体がAに戻る。Aは系統差(timeout・入出力型・関連ID)をメソッドの引数とデフォルト値で表現でき、呼び出し側(M1/M2)はGatewayのみを知ればよい。

トレードオフ: Aは1クラスが3系統の知識を持つ。ただし知識の実体は「timeout秒数と記録時の関連ID」のみで、プロンプト・検証は呼び出し側(§1.4)のため、肥大化の余地が小さい。

### 2.2 プロバイダ抽象 — 推奨: 系統別ABC×3、M0の実装はStubのみ

選択肢:

- **A(推奨). 系統別の抽象基底クラス `ParserProvider` / `EmbeddingProvider` / `JevProvider`(雛形のClockと同じABC判断)。Gatewayは3つのProviderを注入されて構築。M0では `StubLLM`(3 ABCを実装する単一クラス)のみ**
- B. 1つの `LLMProvider` IFに3メソッドを持たせる(実プロバイダが3メソッド全部を実装する単位)
- C. litellm等の既存マルチプロバイダライブラリを導入する

推奨の根拠: D-14基準6(確定値13)は「複数プロバイダをGateway経由で**併存**」を将来像としており、差し替えの単位は系統(Jevだけ別プロバイダへ、等)と読める。系統別ABCはこの差し替え単位をそのまま型にしたものであり、Embeddingモデルの確定タイミングとParser/Jevのプロバイダ確定タイミングがズレる現実(T1の契約確認)にも追従できる。Bは「Embeddingだけ別プロバイダ」が表現しにくい(未実装メソッドを持つプロバイダが生じる)。Cは確定値13の契約面の足切り(学習利用禁止・データ所在地)と、T1未確定の段階で外部ライブラリの抽象に実装を預けることになるため不採用。ABC vs ProtocolもClockと同じ判断(実装者がこれから現れるため、実装漏れを静かに防ぐABC。結合が強い分、1モジュールに閉じる)。

トレードオフ: T1確定時に実プロバイダの実際のAPI形状とABCがズレる可能性がある。その場合はABCの改定(引数追加等)で追随する — Gatewayの呼び出し側IF(§2.1)を変えずに吸収できるのがこの二層構造の目的(C11の「抽象化により未確定のまま進行」の実体)。

### 2.3 送信記録の保存先 — 推奨: 構造化ログ(JSON)。DBテーブル・Redisにしない

スーパーバイザー指示により「docsの確定値を引用して決める」。確認の結果、**docsは送信記録の保存媒体を確定していない**:

- 05 第2節のスキーマ定義に送信記録テーブルは存在しない(users〜calibration_recordsまで。llm送信記録に対応する表はない)
- 04 第2節の構成図で「送信記録(01第21節)」はLLM Gatewayから出る引出線として描かれるが、接続先の箱は描かれていない(PostgreSQLの箱への線はない)
- 08 第3節は記録の内容(送信先・データ種別・時刻、内容を含まない)と開示可能性のみを要求し、媒体を指定しない
- 08 第2.4節は構造化ログの許可リスト方式(出力フィールドを列挙型で固定)を規定しており、送信記録のフィールド(時刻・種別・送信先・intent_id等)はこの許可リストの例示に過不足なく一致する

この前提で選択肢は次のとおり。

- **A(推奨). 専用ロガー(`latch.llm.send`)へJSON 1行を出力する構造化ログ。レコード型(pydantic)でフィールドを固定(=許可リストの実体)**

    - B. PostgreSQLテーブル(ws-1のマイグレーションに追加)
    - C. Redis(リスト/ストリーム)
    - D. ローカルファイル(JSONL)

推奨の根拠: (1)**ws-2の依存は「雛形」のみ**(STATUS.md M0表。実行waveは雛形→ws-1∥ws-2の並行)。BはDB接続コード・ドライバ・テーブル定義を要求しws-1への依存が生まれ、並行構成を壊す。CのRedisは04 第3節で失効リスト・ブロックリスト・友人関係・Jevカウンタのキャッシュ用途と位置づけられており、揮発する媒体に「問い合わせ時に開示できる状態」(確定値3)の記録を置くのは設計目的に反する。DはRotating等の運用を自前で抱え込み、複数プロセス(api/worker)からの書き込み競合も生じる。(2)Aは08 第2.4節の許可リスト規定に直接乗る。送信記録は「内容を含まない」(確定値3)ためログに落としても機微は出ず、機微を構造的に出せなくする手段(pydanticモデルにフィールドが存在しない=シリアライズされない)が同じ型で強制できる。(3)04 第3節の計測基盤(Cloud Monitoring+OTLP+**ログ集計**)がM4で整備される道に接続する。

トレードオフ: ログはDBに比べて問い合わせ時の検索性で劣る(docker logs / 将来のログ基盤での検索になる)。記録の永続性はログの保全設定に依存する。この弱点は**未解決の論点として§6に明示**し、開示の実務が必要になった時点で `send_log` 出力関数(§3.1)の差し替え+ws-1依存の追加作業としてDBテーブル化を再計画できる設計にする(本単位ではRecorder IFの先駆けとして「レコード型+出力関数」のみを作り、IFの抽象は作らない — YAGNI)。

### 2.4 timeoutの执行位置 — 推奨: Gateway内で系統別デフォルト値を強制

選択肢:

- **A(推奨). Gatewayが07 第1節の系統別timeout(Parser 10秒 / Embedding 2秒 / Jev 6秒)を定数に持ち、`asyncio.timeout` で呼び出しを包む。timeout超過は `LLMTimeoutError`、プロバイダ例外は `LLMProviderError` で送出**
- B. timeoutを呼び出し側(M1/M2の実装)が持つ

推奨の根拠: timeoutは系統属性(確定値5)であり、3系統の呼び出し特性を単一経路で管理するC4の範囲内にある。Gateway内に置くことで、(1)送信記録がtimeout失敗も同じ経路で記録できる(status="timeout")、(2)スタブのレイテンシ注入>timeoutでtimeoutが発火する=10 第4.5節の「timeoutエラー注入」がスタブの遅延設定だけで再現できる、(3)M2のcircuit breaker(確定値15)が「Gateway内の呼び出しを包む層」として同じ位置に後置きできる。

トレードオフ: unit試験で実時間を待たないため、timeout値の上書き手段が必要になる。Gatewayの構築引数で系統別timeoutを差し替え可能にする(デフォルト=確定値)。環境変数には出さない(docs確定値の恒久的な変更をenvで黙って行える経路を作らない。試験はコードで注入する)。

再試行については、Parser/Embeddingは「再試行なし」(確定値5)なのでGatewayにも呼び出し側にも再試行ループを作らない。Jevの「出力検証失敗のみ1回」は出力のJSONスキーマ検証(07 第4節)と一体の処理であり、検証自体がM2 Layer 4の責務(§1.4)なので、**ws-2のGateway IFには含めない**(M2で検証実装とセットで追加する。Gatewayは検証失敗を表す例外型の追加だけで対応できる見込み)。

### 2.5 スタブ(テストモード)の応答と決定性 — 推奨: 系統別デフォルト応答+コンストラクタ上書き

10 第1節(確定値10)はスタブを「記録済み応答を返す」「決定的にする」と要求する。選択肢:

- **A(推奨). `StubLLM` は系統別のデフォルト応答(07 第2節・第4節のJSONスキーマに適合する固定値、Embeddingは768次元の決定的ベクトル)を返す。応答はコンストラクタ引数で差し替え可能(テストが任意の応答・検証失敗応答を注入できる)。内部に乱数・実時間時刻を使わない**
- B. 入力テキストのハッシュ→応答のルックアップテーブル(完全な「記録済み応答」再現)
- C. 呼び出し順に応答を返すスクリプト化シーケンス

推奨の根拠: M0時点でスタブの消費者はunit試験のみであり、試験は「必要な応答を明示的に与える」方が決定性の証明として強い(10 第2節のゴールドセット由来の応答定義はM1以降・T3)。Bの完全ルックアップは応答定義資産(docs/testassets/)が整うM1以降に価値が出るもので、M0で作ると使い手がない。Cは呼び出し順への依存(並行試験で壊れる)を持ち込み「決定的」の趣旨に反する。Aは「同一入力・同一設定→同一応答」(決定性)を構造的に満たす — 応答が固定値であり、乱数・時刻参照がないため。Embeddingの768次元は確定値7の次元そのもの(スタブが次元を歪めるとM2以降のpgvector格納試験の前提が崩れる)。ベクトル値は決定的な生成(入力由来のハッシュ等。計画書で確定)とし、値の意味はない(スタブである以上、意味は要求されない)。

トレードオフ: Aでは「実プロバイダの多様な応答を再現する」素材はテスト側が用備する必要がある。これは10 第4.5節のエラー注入と合わせ、エラー注入(常時失敗フラグ)をStubLLMに持たせることで最低限をカバーする(§2.6)。

### 2.6 レイテンシ注入・エラー注入 — 推奨: 系統別の固定遅延ms + 常時失敗フラグ

- **レイテンシ注入**: StubLLMは系統別の遅延(初期値0ms)を持ち、呼び出し前に `asyncio.sleep(delay)` する。設定は `Settings`(`LATCH_LLM_STUB_DELAY_PARSER_MS` 等・系統別3項目)。待機は実時間asyncio.sleepであり、**時刻参照ではないためarch testの禁止対象外**(雛形設計§4.3と同じ規定。窓や境界の判定はClockだが、注入された待機自体は待機)。10 第4.1節のp50/p95分布模擬はM4のload試験まで不要なので固定値のみとし、分布を差し込めるよう遅延の与え方を1関数(系統→遅延ms)に閉じる
- **エラー注入**: StubLLMに系統別の常時失敗フラス(コンストラクタ引数。Trueなら `LLMProviderError` を送出)を持たせる。10 第4.5節「100%エラーおよびtimeout」の前者をこのフラグで、後者を遅延>timeout(§2.4)で再現する。env設定には出さない(試験コードからの注入のみ。常設ci環境で常時失敗にする運用はM2の縮退試験時に改めて検討)

### 2.7 DIとプロセス統合 — 推奨: M0ではapp/workerへの統合をしない

Gatewayは `Clock` を注入される(送信記録の時刻=確定値16。FakeClockで決定的に試験する)。構築は `build_llm_gateway(clock, settings)` ファクトリ(llmモジュール内)。ただしM0の段階でGatewayを消費するのはunit試験のみであり、`create_app` への引数追加・`app.state` 登録・worker mainでの構築は**行わない** — 呼び出し側が存在するM1(parse API)が統合の適切な時期で、統合方法(app.state.llm + get_llm依存)はM1設計に委ねる。これにより `main.py`・`worker/` に触れず、並走するws-1との衝突面を最小にする。

トレードオフ: 「api/workerプロセスでGatewayが構築される」ことの証明はM0では得られない。G0の文言は「スタブで3系統の呼び出しを記録できる」(確定値14)でありプロセス統合を要求しないため、unit試験での証明で足りる。

### 2.8 依存追加 — 推奨: なし(追加依存ゼロで完結)

実プロバイダのHTTPクライアント(httpx等)はT1確定後の実装単位まで追加しない(スタブは通信しない)。SendRecord・応答の型は既存依存に含まれるpydanticで足りる。結果として **pyproject.toml・uv.lock・Dockerfile・compose.yaml・Makefileはすべて無変更** となり、ws-1(並走)との接触面は `settings.py` の追記のみになる。

### 2.9 採用しないもの(YAGNIによる切り捨て一覧)

- httpx・実プロバイダSDK・API鍵設定(T1後。§2.8)
- SendRecorder抽象IF・DB/Redisシンク(§2.3。必要になった時点で出力関数を差し替える)
- circuit breaker(エラー率/p95レイテンシ測定・開放/半開)(M2。12 M2-10)
- Jev出力検証と再試行1回・Parser必須3フィールド検証(M1/M2。§2.4)
- トークン数・cost・latencyメトリクス(M4 Observability。確定値17)
- p50/p95分布遅延・レイテンシプロファイル(M4 load。§2.6)
- Gatewayのヘルス/管理エンドポイント(消費者なし。C3のv1契約にも不要)
- streaming応答・temperature等の生成パラメータ(07の3系統いずれも要求しない。必要時にProvider IFへ追加)

## 3. ファイル構成

### 3.1 作るもの

```text
backend/src/latch/llm/
├── __init__.py            # 公開IFの再export(LLMGateway, build_llm_gateway, 例外, Provider ABC)
├── errors.py              # LLMError(基底) / LLMTimeoutError / LLMProviderError
├── records.py             # SendRecord(pydantic・フィールド固定) + send_log(record)出力関数
├── providers.py           # 系統別ABC×3 + EMBEDDING_DIMENSIONS=768
├── gateway.py             # LLMGateway(3系統メソッド・timeout・送信記録) + build_llm_gateway
└── stub.py                # StubLLM(3 ABC実装・デフォルト応答・遅延注入・エラー注入)
backend/src/latch/settings.py  # LLM設定4項目を追記(§3.2)
backend/tests/unit/llm/
├── test_gateway_parse.py     # Parser系統: 応答・送信記録・timeout
├── test_gateway_embed.py     # Embedding系統: 応答(768次元)・送信記録・timeout
├── test_gateway_jev.py       # Jev系統: 応答・送信記録・timeout
├── test_send_record.py       # 許可リスト検査(08 §2.4)・JSON化・時刻のClock由来
├── test_stub.py              # 決定性・遅延注入・エラー注入
└── test_llm_factory.py       # build_llm_gateway(設定→StubLLM・未実装modeの拒否)
```

インターフェースの要旨(実装詳細は計画書・TDDで確定させる):

```python
# providers.py
EMBEDDING_DIMENSIONS = 768  # 05 第2節 vector(768)(C11: モデル確定までの固定値)

class ParserProvider(ABC):
    name: str                                  # 送信記録の送信先(識別名)
    @abstractmethod
    async def complete_structured(self, text: str, current_date: date) -> dict: ...

class EmbeddingProvider(ABC):
    name: str
    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...

class JevProvider(ABC):
    name: str
    @abstractmethod
    async def judge(self, intent_a: str, intent_b: str) -> dict: ...

# gateway.py
TIMEOUT_PARSER_S = 10.0    # 07 第1節(D-17)
TIMEOUT_EMBEDDING_S = 2.0  # 07 第1節
TIMEOUT_JEV_S = 6.0        # 07 第1節

class LLMGateway:
    def __init__(self, *, clock: Clock, parser: ParserProvider,
                 embedding: EmbeddingProvider, jev: JevProvider,
                 timeouts: Timeouts | None = None) -> None: ...

    async def parse_intent(self, *, text: str, current_date: date,
                           user_id: str | None = None) -> dict:
        """07 第2節。送信内容=ユーザー入力テキスト(§1.3)。timeout 10秒・再試行なし。"""

    async def embed_intent(self, *, text: str, intent_id: str) -> list[float]:
        """07 第3節。正規化テキスト→768次元。timeout 2秒・再試行なし。"""

    async def judge_pair(self, *, intent_a: str, intent_b: str,
                         intent_ids: list[str]) -> dict:
        """07 第4節。2 Intent分の正規化テキスト→7設問JSON。timeout 6秒。"""

# records.py — 許可リスト(08 §2.4)。この型に存在しないフィールドは出力され得ない。
class SendRecord(BaseModel):
    occurred_at: datetime                                   # Clock.now()(tz-aware UTC)
    system: Literal["intent_parser", "embedding", "jev"]    # データ種別(08 第3節)
    destination: str                                        # 送信先(Provider.name)
    status: Literal["ok", "timeout", "error"]
    error_code: str | None = None
    intent_ids: list[str] | None = None                     # Embedding=1件・Jev=2件・Parser=None
    user_id: str | None = None                              # Parser(M1以降に値が入る)

def send_log(record: SendRecord) -> None:
    """logger 'latch.llm.send' へ JSON 1行で出力(将来のログ集計=04 第3節への接続点)。"""

# stub.py
class StubLLM(ParserProvider, EmbeddingProvider, JevProvider):
    """テストモード(10 第1節)。name="stub"。乱数・実時間参照なし=決定的。"""
    def __init__(self, *, parser_response: dict | None = None,
                 embedding_response: list[float] | None = None,
                 jev_response: dict | None = None,
                 delay_parser_ms: int = 0, delay_embedding_ms: int = 0,
                 delay_jev_ms: int = 0,
                 fail_parser: bool = False, fail_embedding: bool = False,
                 fail_jev: bool = False) -> None: ...
    # デフォルト応答: parser_response は07 第2節スキーマ適合の最小JSON
    # (category/time.start/location.nameを含む)、jev_response は07 第4節の
    # 7設問JSONスキーマ適合、embed は768次元の決定的ベクトル

# settings.py(追記分)
llm_mode: str = "stub"                      # T1確定後に "real" を追加
llm_stub_delay_parser_ms: int = 0           # 10 第1節レイテンシ注入(既定は無効)
llm_stub_delay_embedding_ms: int = 0
llm_stub_delay_jev_ms: int = 0
```

Gatewayの各メソッドの動作は共通の形にする: `asyncio.timeout` でProvider呼び出しを包み、成否にかかわらずSendRecordを構築して `send_log` で出し、成功なら応答を返し、timeoutなら `LLMTimeoutError`、プロバイダ例外は `LLMProviderError` に包んで送出する(送信記録が先、例外送出が後 — 失敗時も記録が漏れない)。

### 3.2 触るもの(既存ファイルへの追記のみ)

- `backend/src/latch/settings.py` — LLM設定4項目(§3.1)。既存項目・構造は変更しない

### 3.3 触らないもの

- `backend/src/latch/main.py`・`worker/`・`core/`(§2.7。M1が統合する)
- `backend/tests/conftest.py`・既存テスト群 — Gateway用fixtureは `tests/unit/llm/` 内のヘルパで組み立てる(並走するws-1との共通ファイル衝突を避ける)
- `backend/pyproject.toml`・`uv.lock`・`Dockerfile`(§2.8 依存追加なし)
- ルートの `compose.yaml`・`Makefile`・`docker/`・`.mise.toml`・`.gitignore`
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/M0/`の他ファイル
- `prototype/` 全体・`README.md`・`.claude/`

## 4. テスト方針

すべてunit試験(外部プロセス不要。`make test` で毎コミット実行)。FakeClockを注入して時刻を決定的にする。実時間のsleepを含む試験(遅延注入)は注入値を数十msに抑え、全体の実行時間に影響しないようにする。実時間の計測(time.perf_counter等)はテストコードのみで行う(arch testのスキャン対象は `backend/src` のみ)。

1. **3系統の呼び出しと送信記録(G0の証明)** — 各系統について、StubLLMを注入したGatewayを呼び出し、(a)応答が得られること(Parser/Jevはdict・Embeddingは長さ768のlist[float])、(b)logger `latch.llm.send` に出力されたJSON 1行に `occurred_at`(=注入したFakeClockの時刻と一致)・`system`(系統名)・`destination`("stub")・`status`("ok")・関連ID(intent_ids)が含まれることを検証(caplog使用)
2. **timeout** — 系統別timeoutを構築引数で短縮したGateway(例: 0.05秒)に遅延注入(例: 200ms)したStubLLMを渡し、`LLMTimeoutError` が送出され、送信記録のstatusが"timeout"になることを検証(§2.6のtimeoutエラー注入の再現)
3. **プロバイダエラー** — failフラグを立てた系統で `LLMProviderError` とstatus="error"の記録を検証
4. **決定性(10 第1節)** — 同一入力・同一設定のStubLLMを2回呼び出し、応答が完全一致すること(embedは同一ベクトル、parser/jevは同一dict)
5. **レイテンシ注入** — 遅延ms>0で応答までの実時間>=遅延であることを検証(小さい値で)
6. **許可リスト(08 §2.4)** — `SendRecord.model_fields` が許可したフィールドのみを持ち、テキスト系フィールド(raw_text・prompt・入力テキスト・位置・conditions)が型に存在しないことを機械検査。JSON化した出力に機微キーが現れないこと
7. **ファクトリ** — `build_llm_gateway(clock, settings)` が `llm_mode="stub"` でStubLLM内包のGatewayを構築すること、遅延設定3項目がStubLLMへ伝わること、`llm_mode` が未知値(将来の"real"を含む)ならValueErrorで起動を拒否すること
8. **arch規律の維持** — 既存 `test_arch_no_direct_time.py` がllm/配下を自動スキャンするため、llm/にdatetime.now等が入らないことが既存試験で強制される(追加のarch testは不要)

G0「LLM Gatewayがスタブで3系統の呼び出しを記録できる」の証拠は、上記1〜3のunit試験グリーン(実行記録)とする(§2.7のとおりM0にプロセス統合はない)。

## 5. 完了条件(この単位の受渡し判定。計画書が参照する)

1. `make lint` `make test` がグリーン(unit追加分を含む)
2. §4-1〜3の試験により、3系統のスタブ呼び出しで送信記録(送信先・データ種別・時刻=Clock由来)が出力されることが立証されている(G0文言)
3. §4-4〜7の試験(決定性・遅延・エラー注入・許可リスト・ファクトリ)がグリーン
4. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが**引き続き `core/clock.py` のみ**(llm/配下は時刻をClock経由で得るためヒットしない。雛形の完了条件5を維持)
5. 依存追加なし(`git diff --stat` で `backend/pyproject.toml`・`uv.lock` に差分がないこと)
6. 触るファイルが `settings.py`(追記)と `backend/src/latch/llm/`・`backend/tests/unit/llm/`(新規)のみであること(main.py・worker/・core/・compose.yaml等に差分なし)

## 6. 未解決の論点(設計は推奨で固定済み。supervisor確認事項)

1. **送信記録の検索・開示の実務**(§2.3): 保存先を構造化ログ(ログ集計はM4・04 第3節)とする設計に固定した。docsは媒体を確定しておらず(05にテーブルなし・04 第2節図で接続先なし)、ws-2の依存(雛形のみ)・並行waveを壊さない選択としている。問い合わせ開示(08 第3節)の実務が「ユーザーごとの検索」を要求する強度になった場合、SendRecordをDBテーブルへ書く後段追加(ws-1統合後)を要する — その場合は設計変更として再計画する。**この判断(ログで開始)の承認をsupervisorに求める**
2. **Parser送信記録のuser_id紐づけ**(§1.3): parseはIntent未作成のためintent_idを持たず、08 第3節の「どのIntentの」に直接対応するIDがない。Gateway IFは `user_id` 引数を予約したが、値を渡すのはM1のparse API実装(認証ユーザー=ws-3依存)の設計に委ねる。M1での判断事項として記録する
3. **レイテンシ分布(p50/p95)への拡張**(§2.6): 10 第4.1節の分布注入(Jev p50 2秒/p95 5秒等)はM4 load試験で必要になる。現設計は固定遅延のみ。遅延の与え方を1関数に閉じているため、分布版への差し替えはスタブの内部変更で完結する(Gateway IF不変)
4. **Jev再試行1回のIF反映**(§2.4): 出力検証と一体でM2実装。Gateway側の追加は例外型(LLMOutputError相当)の追加のみで済む見込みだが、M2設計時に「Gateway呼び出し単位で再試行するか、呼び出し側で再呼び出しするか」を確定する必要がある(D-16の回数計上は実API呼び出し単位 — 04 第4節 D-16)。本設計は再試行をGateway IFに入れないことでM2に選択の余地を残している
