# T1 LLMプロバイダ選定(選定資料)

- 文書バージョン: v0.1
- ステータス: Draft(オーナー確認待ち。契約手続きは未実施)
- 作成日: 2026-09-28
- 調査方法: exa MCP(web_search_exa / web_fetch_exa / agent_run)。出典はすべて2026-09-28に公式ページから直接確認した(確認日を明記した出典を各節に付す)。記憶に基づく記述はしていない
- 目的: 04 D-14の6基準+08 D-14の契約5条件で候補を比較し、LLM 3系統(Parser / Jev / Embedding、07 §1)ごとの推奨とコスト見通しを確定材料として出す

## 結論(要約)

- **第一候補は生成と埋め込みを分ける構成にする — 生成はAnthropic Claude API、埋め込みはGoogle Gemini API有料tier。** モデルはParserがHaiku 4.5、JevがSonnet 5、埋め込みがgemini-embedding-001。学習禁止はAnthropicで規約本文にあり、05のvector(768)にスキーマ変更なしで入る埋め込みAPIはGeminiだけだった
- コストはベータ規模で月次約$2.7(推奨案)。Jevが回数上限の60万回/月まで増えても約$6に収まり、コスト基準は足切り要因にならない
- 弱い点はAnthropic第一partyのデータ所在地(リージョン指定不可)と公開SLAの不在。国内常駐とSLA 99.9%を要件にするなら、第一候補をAWS BedrockのClaude(東京リージョン)に繰り上げる(第5節)

## 1. 選定基準の再掲

**技術選定基準(04 D-14、この順でフィルタする)。**

1. 学習利用の禁止 — 入力・出力をモデル学習に利用しないことを契約上明示できること(Zero Data Retention相当)。満たさないプロバイダは他の基準に関係なく除外
2. データの取り扱い — 保持期間・データ所在地を制御できること。送信記録(01第21節)と合わせ追跡可能
3. 機能 — JSON構造化出力の安定性(Parser・Jev)、温度等の生成制御、日本語の埋め込み品質(Embedding)
4. 性能 — 初期LATCH判定p95 10秒(01第20節)の配分を満たす応答速度と安定性(Parserはプレビュー表示p95 3秒 / p99 8秒、07 D-17)
5. コスト — トークン単価とD-16の回数上限から月次コストが予測できること
6. 可用性 — SLAの明示。複数プロバイダをGateway経由で併存させ、障害時の縮退(D-15、06)に備えられること

**契約条件(08 D-14、この5条件を契約上確定できないプロバイダは、技術基準を満たしても採用しない)。**

(1) 入出力の学習利用禁止 (2) 保持期間の最小化(Zero Data Retention相当) (3) サブプロセッサの開示 (4) データ所在地の明示 (5) 漏洩時の通知義務

**3系統の要件(07 §1〜§3の要約)。**

| 系統 | 要件 |
|---|---|
| Parser | 同期(ユーザーが待つ)。日本語自然文300字→JSON構造化。timeout 10秒・再試行なし。プレビュー表示p95 3秒 / p99 8秒 |
| Jev | 非同期。正規化テキスト2件→7軸の確率JSON。timeout 6秒、JSON検証失敗のみ1回再試行。回数上限は04 D-16(日次30,000回・月次600,000回、通常時は概ね1万回/日以内) |
| Embedding | 非同期。多言語(日本語)対応・次元は768(05のvector(768)固定)。timeout 2秒・再試行なし。失敗はD-15バックフィルで回収 |

## 2. 候補比較

### 2.1 基準1(学習利用禁止)の足切り結果

5社とも、**商用・有料のAPI契約では入出力の学習利用を禁止できる**。ただし「無条件でない」かどうかに差があり、禁止条項の根拠と付帯条件は次のとおり。

| プロバイダ | 学習利用禁止の根拠(原文の趣旨) | 付帯条件 | 判定 |
|---|---|---|---|
| Anthropic Claude API | 商用規約B節「Anthropic may not train models on Customer Content」 | 条項は契約本体に明記。デフォルトで会話内容(proンプト・応答)は非保持。ZDRは組織単位・申請制。Fable 5.1 / Mythos 5.1 / Fable 5 / Mythos 5はCovered Modelsとして30日保持が必須でZDR不可(明示的承認があれば可)。Sonnet 5・Opus 5・Haiku 4.5は非該当 | ○ |
| OpenAI API | Services Agreement 4.2「OpenAI will not use Customer Content to develop or improve the Services, unless Customer explicitly agrees」 | 学習不使用は標準。ただしabuse monitoringログに顧客コンテンツが最大30日保持される。ZDRまたはModified Abuse Monitoringは事前承認制(sales経由・追加要件の受諾)。承認後は組織/プロジェクト単位で設定 | ○ |
| Google Gemini API(有料) | 追加規約のPaid Services節「Google doesn't use your prompts ... or responses to improve our products」 | **無料tier・無料quotaは学習利用・人間レビューの対象**。有料(Cloud Billing紐付け)に限定することが運用条件。ZDR相当はプロジェクト単位の申請・承認制(承認後はabuseログをサニタイズ) | ○(有料に限る) |
| Microsoft Azure OpenAI | データ・プライバシー文書「are NOT used to train any generative AI foundation models without your permission or instruction」「NOT available to OpenAI」 | 学習不使用+OpenAI等への非共有を明記。モデルはステートレス。ただしstateful機能(Files / vector store / Stored completions等)は別途保持される | ○ |
| AWS Bedrock | データ保護文書。モデルプロバイダはBedrockのログや顧客の入出力にアクセス不可。入出力は基盤モデルの学習に使われない | リージョン内処理(In-Region)・Geo(Japan)ルーティングが選べる。保持は `data_retention_mode=none`(対応モデルのみ)でZDR相当が取れる | ○ |

出典(いずれも確認日2026-09-28):

- Anthropic商用規約 <https://www.anthropic.com/legal/commercial-terms>(B節)/ データ保持とZDR <https://platform.claude.com/docs/en/manage-claude/api-and-data-retention> / ZDR適用製品 <https://privacy.claude.com/en/articles/8956058-i-have-a-zero-data-retention-agreement-with-anthropic-what-products-does-it-apply-to>
- OpenAI Services Agreement <https://openai.com/policies/business-terms>(2025-12-01更新・2026-01-01効力、4.2)/ データ管理とZDR <https://developers.openai.com/api/docs/guides/your-data>
- Gemini API追加規約 <https://ai.google.dev/gemini-api/terms>(2026-03-23効力、Unpaid / Paid Services節)/ Gemini APIのZDR <https://ai.google.dev/gemini-api/docs/zdr>
- Azure OpenAIデータ・プライバシー <https://learn.microsoft.com/en-us/legal/cognitive-services/openai/data-privacy>
- AWS Bedrockデータ保護 <https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html> / データ保持 <https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html>

### 2.2 基準1を満たす候補の比較表

○=明確・確認済み / △=条件付きまたは未確認 / ×=不可。

| 基準 | Anthropic Claude API | OpenAI API | Gemini API(有料) | Azure OpenAI | AWS Bedrock |
|---|---|---|---|---|---|
| 1. 学習利用禁止 | ○(規約B節) | ○(規約4.2) | ○(有料のみ。無料は×) | ○ | ○ |
| 2a. 保持(ZDR相当) | △ デフォルト非保持。ZDRは組織単位・sales申請 | △ abuseログ30日。ZDRは承認制 | △ abuseログあり。ZDRはプロジェクト承認制 | ○ ステートレス・他社非共有(stateful機能を避ければ) | ○ In-Region+`data_retention_mode=none`(対応モデル) |
| 2b. データ所在地の制御 | △ 第一partyはリージョン指定なし(`inference_geo: us`のみ1.1倍) | △ リージョン処理オプションあり(+10%料金)。日本リージョンの有無は未確認 | × 処理国はGoogle側で選べない(規約記載) | ○ Japan EastのRegional配備(モデル別対応は要確認) | ○ 東京リージョンIn-Region / Geo(Japan) |
| 3a. 構造化出力 | ○ `output_config.format`のjson_schema+strict tool use。ZDR対象(qualified) | ○ Structured Outputs(json_schema) | ○ JSON Schema(response_schema / Interactions) | ○ OpenAIと同系(json_schema) | ○ Converse APIのStructured Outputs(Draft 2020-12サブセット。対応モデルあり) |
| 3b. 日本語(生成) | ○ | ○ | ○ | ○(モデル次第) | △ モデル次第(Nova / Claude / Llama等) |
| 4. 応答速度 | 実測前提(本調査では測定せず) | 同左 | 同左 | 同左 | 同左 |
| 5. コスト(代表モデル) | Haiku 4.5 $1 / $5、Sonnet 5 $2 / $10 | GPT-5.4 mini $0.75 / $4.50、GPT-5.4 $2.50 / $15 | gemini-3.8-flash $0.75 / $3.75(〜2026-12-31。2027-01-01から倍額) | 動的レンダリングで単価を取得できず(未確認) | モデル・リージョンごと(東京のClaude単価は未確認) |
| 6. 可用性(SLA) | △ 標準tierは公開SLAなし。Priority Tier(コミットメント)が99.5%目標 | △ 標準は公開SLAなし。Scale Tier(コミットメント購入)が99.9% + レイテンシSLA | △ Gemini Developer APIにSLAなし(Vertex AIならあり) | ○ 製品ページが「at least 99.9%」と保証(配備タイプにより差あり。Standardはbest-effortと別文書にあり) | ○ 月次99.9% SLA(クレジット制度あり) |

出典(確認日2026-09-28):

- Anthropic料金 <https://docs.claude.com/en/docs/about-claude/pricing>(Sonnet 5は$2/$10が標準価格に確定・2026-09-01の値上げ中止を明記)/ 構造化出力 <https://platform.claude.com/docs/en/build-with-claude/structured-outputs>(ZDR eligible・対応モデル列挙)/ service tiers <https://platform.claude.com/docs/en/api/service-tiers>
- OpenAI料金 <https://openai.com/api/pricing/> / Scale Tier <https://openai.com/api-scale-tier/>
- Gemini料金 <https://ai.google.dev/gemini-api/docs/pricing>(ページ更新2026-09-24)/ 構造化出力 <https://ai.google.dev/gemini-api/docs/structured-output>
- Azureデプロイタイプ <https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/deployment-types>(2026-08-12更新)/ 製品ページのSLA FAQ <https://azure.microsoft.com/en-us/products/ai-foundry/models/openai> / 料金 <https://azure.microsoft.com/en-us/pricing/details/azure-openai/>
- Bedrock SLA <https://aws.amazon.com/bedrock/sla/>(Last Updated 2023-10-04)/ リージョン別モデル <https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html> / 構造化出力 <https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html>

### 2.3 日本語埋め込みの候補(768次元の観点が最重要)

05のvector(768)をスキーマ変更なしに満たせるかが第一の分岐点である。

| 候補 | 料金(USD/1Mトークン) | 次元 | 768への対応 | 学習利用・保持 | 判定 |
|---|---|---|---|---|---|
| Google gemini-embedding-001 | $0.15 | 入力上限2,048トークン。出力は128〜3072で可変、**推奨値に768を明記** | ○(パラメータ指定) | 有料tierで学習不使用。ZDRはプロジェクト承認制 | ◎ |
| OpenAI text-embedding-3-large | $0.13 | 3,072既定。dimensionsパラメータで短縮可(MRL) | △ 短縮可能と明記されるが768という値が公式に列挙されているかは未確認 | APIデータは学習に使わない。embeddingsエンドポイントはZDR適格 | ○(要確認) |
| OpenAI text-embedding-3-small | $0.02 | 1,536既定。dimensionsパラメータで短縮可(MRL) | △ 同上 | 同上 | ○(要確認) |
| Voyage voyage-4 / 4-lite / 4-large | $0.06 / $0.02 / $0.12 | 既定1,024、256 / 512 / 2,048を選択可。**768は選択肢にない** | ×(スキーマ変更が前提) | 学習取扱いの一次出典を本調査では取得できず(未確認) | × |
| Cohere embed-v4.0 | $0.12(従量の参考値。中確度) | 256 / 512 / 1,024 / 1,536。**768は選択肢にない** | ×(同上) | enterprise data commitmentsにZDR相当あり | × |
| AWS Titan Text Embeddings V2 | 未確認 | 1,024系 | ×(同上) | Bedrockの取り扱いに準ずる | × |

出典(確認日2026-09-28):

- Gemini embeddings <https://ai.google.dev/gemini-api/docs/embeddings> / gemini-embedding-001モデルページ <https://ai.google.dev/gemini-api/docs/models/gemini-embedding-001>(「Flexible, supports: 128 - 3072, Recommended: 768, 1536, 3072」)/ 料金 <https://ai.google.dev/gemini-api/docs/pricing>
- OpenAI embeddingsガイド <https://platform.openai.com/docs/guides/embeddings>(dimensionsパラメータとMRL)/ モデルページ <https://developers.openai.com/api/docs/models/text-embedding-3-small>($0.02・3-largeは$0.13)
- Voyage料金 <https://docs.voyageai.com/docs/pricing> / モデル一覧 <https://docs.voyageai.com/docs/embeddings>
- Cohere embed <https://docs.cohere.com/docs/cohere-embed> / 料金 <https://cohere.com/pricing> / <https://cohere.com/enterprise-data-commitments>
- Titan Text Embeddings V2の東京リージョン対応 <https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html>

## 3. LATCHの3系統への推奨

**結論: 生成(Parser・Jev)と埋め込み(Embedding)は分ける。生成はAnthropic、埋め込みはGoogle Geminiにする。** 1社で揃える案(Azure・OpenAI・Bedrock)も機能面では成立するが、埋め込みの768次元と生成のデータ取り扱いを同時に最良にする組合せはこの分離だけだった。

| 系統 | 第一候補 | 理由 |
|---|---|---|
| Jev | **Claude Sonnet 5**($2 / $10、文脈1M) | 7軸の確率判定がMutualScoreの中核であり品質を最優先。学習禁止が規約本文にあり、デフォルトで会話内容非保持。構造化出力対応。コスト差は月次数ドル(第4節)で品質優先が成立する |
| Parser | **Claude Haiku 4.5**($1 / $5) | 単純抽出・最安クラス。構造化出力対応。07 D-17の精度ゲート(30件以上の入力セットでフィールド一致率合格)を通るかで確定する。不合格ならSonnet 5へ統一 |
| Embedding | **gemini-embedding-001**(有料tier、$0.15) | 05のvector(768)にそのまま入る唯一の主要API(推奨次元に768を明記)。多言語(100以上の言語)。有料tierなら学習不使用。timeout 2秒・非同期・バックフィルあり(07 §1)なのでSLA不在の弱点は実害が小さい |

**Gateway経由の併存(04 D-14基準6)。** 04の技術スタック表は「LLM(Jev・Parser)をD-14基準で一次選定し、Gatewayで差し替え可能」と既に定めている。本推奨もGateway抽象化の内側に各系統の宛先を持つ形とする。切替の優先順(障害時・契約問題時)は次のとおり。

- 生成: (1)Anthropic direct(本命)→ (2)**AWS BedrockのClaude(東京リージョン、In-Region)** — 同系モデルのままデータ所在地とSLA 99.9%を得られる。BedrockではClaude Haiku 4.5・Sonnet 4.5/4.6・Opus 4.5/4.6で構造化出力が使える(出典: Anthropic構造化出力docsのプラットフォーム表)→ (3)OpenAI GPT-5.4 mini
- 埋め込み: (1)gemini-embedding-001 → (2)OpenAI text-embedding-3系(dimensions=768が取れるかの実測込み)。埋め込みモデルの切替は07 §3どおりintents.embedding_modelの記録に基づき再エンベディング(バックフィル)で対応する

**Gemini APIの運用上の線引き(採用する場合の必須規律)。** 無料tierは学習利用・人間レビューの対象である(追加規約)。EmbeddingでGeminiを使うなら、契約前の社内規律として次の4点を明記する(出典: Gemini ZDR docs、確認日2026-09-28)。

- 当該プロジェクトはCloud Billing必須の有料限定とし、無料quotaの鍵をコード・設定に置かない
- Grounding(Google Search / Maps)は30日保持が無効化不可のため使わない
- File API・明示的コンテキストキャッシュは使わない
- Interactions APIはstore=falseとする

Embeddingの入力は07 §3どおり構造化データからの正規化テキストに限られ、raw_textは送らない。

## 4. コスト試算

**前提(仮定を明示する。実運用後に差し替える)。**

- Jev回数: 通常時1万回/日×実効20日=20万回/月(04 D-16の根拠どおり)。上限時60万回/月(04 D-16)
- Parser回数: 月次3万回を仮定(ベータ規模DAU数百〜数千・Active Intent 1万件前後から、Intent登録は1日1,000〜1,500回の想定)
- Embedding回数: 月次5万回(Active作成・active化・active更新。1回あたり約100トークンの正規化テキスト。07 §3の形式)
- トークン: Parserは入力約1.8k(システムプロンプト約1.2k+入力300字約0.6k)・出力約0.4k。Jevは入力約3k(システムプロンプト約2k+正規化テキスト2件約1k)・出力約0.3k。07 §2・§4のプロンプト全文からの概算。日本語のトークン換算に±50%の幅をみる
- プロンプトキャッシュ(Anthropic $0.2〜0.25/MTok、Gemini $0.075/MTok、OpenAI $0.075〜0.25/MTok)を効かせればさらに下がる。ここでは効かせていない保守値

**月次概算(USD)。** 式: 回数×(入力トークン×入力単価+出力トークン×出力単価)/100万。

| 案 | Jev(20万回) | Parser(3万回) | Embedding | 合計(通常時) | 合計(Jev上限60万回) |
|---|---|---|---|---|---|
| Sonnet 5 + Haiku 4.5(推奨案) | $1.80 | $0.11 | $0.75 | **約$2.7** | $6.30 |
| すべてSonnet 5 | $1.80 | $0.23 | $0.75 | 約$2.8 | $5.63 |
| OpenAI GPT-5.4 mini | $0.72 | $0.09 | $0.75 | 約$1.6 | $2.98 |
| Gemini gemini-3.8-flash | $0.68 | $0.09 | $0.75 | 約$1.5 | $2.80(2027-01から倍額で約$5.6) |

**結論: ベータ規模ではどの案も月次数ドルであり、コスト(基準5)は足切り要因にならない。** 決め手は基準1〜3(学習禁止・データ取り扱い・機能)と、精度ゲート(07 D-17のParser合格基準)の実測結果になる。回数上限が本気で効くのは異常時(Event洪水)であり、その場合も上限到達時はJevスキップ(D-16)で止まるため、月次上限時でも約$3〜6の範囲に収まる。

## 5. 推奨

- **第一候補: Parser・JevはAnthropic Claude API(Sonnet 5 / Haiku 4.5)、EmbeddingはGoogle Gemini API有料tier(gemini-embedding-001)。**
  - 理由: 基準1の学習禁止がAnthropicでは規約本体に明記され、会話内容もデフォルトで非保持。構造化出力がZDR対象。Gemini embeddingだけが05の768次元をスキーマ変更なしに満たし、有料tierで学習不使用。合計コストは月次約$3で予測可能
  - 留保: 基準2の「データ所在地の制御」は第一party Anthropicでは弱い(リージョン指定不可)。日本国内でのデータ常駐を強く求めるなら第一候補を**AWS BedrockのClaude(東京リージョン)**に繰り上げる構成が代替本命になる。BedrockはSLA 99.9%・学習不使用・東京In-Regionで三条件が揃う。埋め込みだけGemini(またはBedrock外)と組み合わせる
- **代替: Azure OpenAI(1社化案)。** Japan EastのRegional配備+学習不使用+SLA FAQ 99.9%。Azure上に他コンポーネントを置くなら運用が1社に集約される。ただしAzure単価が動的レンダリングで読めず、Japan Eastでのモデル別可用性も要確認のため、現時点では第二位
- **縮退先(D-15、06のGateway切替):** 生成はBedrockのClaude(東京)→OpenAI(GPT-5.4 mini)。埋め込みはOpenAI text-embedding-3系。Jevの縮退判定は06 D-15(timeout・検証失敗・circuit breaker)、埋め込みの縮退は07 §1どおりバックフィルで回収する

## 6. 契約前チェックリスト(08 D-14の5条件をどこで確認するか)

| # | 契約条件 | Anthropic | OpenAI | Google(Gemini) | Azure | AWS Bedrock |
|---|---|---|---|---|---|---|
| 1 | 学習利用禁止 | 商用規約B節(既に明文あり) | Services Agreement 4.2(既に明文あり) | 追加規約Paid Services節。**無料quotaを一切使わない規律** | データ・プライバシー文書(MSA/DPAと接続する条項を契約書で確認) | Bedrockのデータ保護文書+AUP |
| 2 | 保持期間の最小化 | ZDRの組織有効化(sales経由・settingsで確認可能) | ZDRまたはModified Abuse Monitoringの承認(Settings→Organization→Data controls) | プロジェクト単位のZDR申請(承認後はログがsanitized) | stateless利用の構成証明+保持条項(DPDに記載) | `data_retention_mode=none`のモデル対応確認 |
| 3 | サブプロセッサの開示 | DPA(DPA参照先のサブプロセッサ一覧) | Security Measures+Services Agreement | DPA for Products Where Google is a Data Processor | Microsoft Products and Services DPA | AWS DPA+サブプロセッサ一覧 |
| 4 | データ所在地の明示 | 第一partyは制御不可。所在地を要求するならBedrock(東京/Geo Japan)またはAzure(Japan East)へ | リージョン処理オプション(+10%)の地域一覧をsalesに確認 | 「Googleまたはそのエージェントが施設を維持する国」(規約記載)。所在地指定は不可 | Japan East Regional配備(モデル別可用性を要確認) | 東京In-Region / Geo(Japan)(既に文書で確認) |
| 5 | 漏洩時の通知義務 | DPAの通知条項 | Security Measuresの通知条項 | Google DPAの通知条項 | Microsoft DPAの通知条項(SLAと別途) | AWS DPAの通知条項 |

いずれも「本調査で読めた公開文書」と「契約時に締結するDPA本文」の2層がある。条件1〜2は公開文書でほぼ確定できるが、3〜5はDPAの署名版で最終確認する(08 D-14の選定基準「契約上確定できること」の趣旨どおり)。

## 7. 残る未確認事項(人間が確認するもの)

1. **ZDR申請の審査実務**: Anthropic(sales経由・組織単位)とOpenAI(sales経由・承認制)の所要期間・審査基準・小規模法人への適用可否。GeminiのZDRプロジェクト承認も同様。契約手続きの最初の窓口事項
2. **組織・アカウント要件**: Anthropic商用組織(Commercial organization)の開設要件、OpenAIの組織検証(verify)、GeminiのCloud Billing口座設定。日本の法人(個人事業主含む)での締結可否
3. **Azure OpenAIの単価とJapan Eastのモデル別可用性**: 料金ページが動的レンダリングのため本調査では数値を取得できなかった。Region availabilityページとAzureポータル(契約サブスクリプション)で確認
4. **Bedrock東京リージョンのClaude単価と、Sonnet 5 / Haiku 4.5の東京In-Region対応**: 本調査で東京対応を直接確認できたのはClaude Fable 5.1・Mythos 5.1など(リージョン表の前半部分)まで。Sonnet 5・Haiku 4.5の東京対応は要確認(Fable 5.1系はCovered Modelsで30日保持必須のため、ZDR目的では非該当モデルを使う)
5. **OpenAI text-embedding-3系のdimensions=768の可否**: dimensionsパラメータによる短縮(MRL)は公式docsにあるが、対応値の一覧(768が含まれるか)は未確認。Embedding切替時に実測する
6. **Voyage・Cohereの学習取扱いの一次出典**: 本調査でVoyageの学習・保持条項の一次文書を取得できなかった。768非対応のため第一候補から外れるが、参考比較には要確認
7. **第一party Anthropic / OpenAI標準 / Gemini directのSLA不在の受け方**: 公開SLAがない前提での縮退設計(D-15・circuit breakerのしきい値)が06側と整合するかの確認。SLAが必要になった時点で、Bedrock(SLA 99.9%済)またはOpenAI Scale Tier / Anthropic Priority Tier(コミットメント契約)へ
8. **各社の日本語性能・レイテンシの実測**: 本調査は料金・契約面の調査であり、07 D-17の精度ゲート(Parser 30件以上の入力セット)と性能試験(10 §4.1)は実プロバイダ契約後の実測で確定する
