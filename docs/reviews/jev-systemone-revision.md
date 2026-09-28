# Jev System One改版メモ(v0.5改版・2026-09-28)

- 改版の根拠: 2026-09-28オーナー裁定(C案)「Layer 4(Jev系統)の第一候補を実物のTypeSafe Jev(System Oneモデル)とし、LLMを縮退・フォールバックとする」
- 本メモはdocs/01〜12とdocs/plans/T1-llm-provider-selection.mdの改版の変更箇所一覧と出典であり、レビューの対象文書である
- 出典はすべて2026-09-28に一次資料から直接確認した。取得手段はcontext7(/websites/typesafe_ai)とexa MCP(web_search_exa / web_fetch_exa)。記憶に基づく記述はしていない

## 1. 一次資料で確認した事実と出典

| # | 事実 | 出典 |
|---|---|---|
| 1 | API: POST https://api.typesafe.ai/v1/systemone・Bearer認証・Content-Type: application/json。リクエストはstate(文字列/JSONオブジェクト/配列)+model+questions(質問名→定義のマップ)。質問型はnoul(YES確率)/choice(最大255択・選択+確率分布+confidence)/score(2〜10段階・確率加重値+分布+confidence)の3種。instructionsは文字列/オブジェクト/配列可(質問と参照データを分離)。応答はmodel(バージョンID)+answers(同キー)+usage(input_tokens/output_tokens)。キーはモデルに送られない | https://docs.typesafe.ai/api ・ https://docs.typesafe.ai/introduction/quickstart(context7 /websites/typesafe_ai でも同一内容を取得) |
| 2 | モデル: jev-1.13.0(エイリアス jev-latest/jev-preview・両方現在同一を指す)。入力$42/Btok(=$0.042/MTok)・出力無料。レート制限250,000トークン/秒+1,200リクエスト/分(動的調整・予告なく変わる・超過で429)。コンテキスト64k/リクエスト(state+全質問)・state+最長質問32k。入力はテキストのみ。**Jevは顧客データでfine-tune/LoRA適応されない(RLCD訓練・全アカウント共通重み)**。エイリアスは移動するため閾値を調整した場合はバージョンIDをpinせよと明記 | https://docs.typesafe.ai/models |
| 3 | 429・529時はSDKが既定で指数バックオフ再試行(retry-afterヘッダ尊重)。直接HTTPの場合は Handling rate limits 参照 | https://docs.typesafe.ai/api(Errors・Handling rate limits)・ https://docs.typesafe.ai/models |
| 4 | **言語サポート: 英語が主訓練言語であり精度が現在最も高い。CJKを含む他言語は「handled but not equally well」(扱えるが同等ではない)。非英語ワークロードでJevに頼る前に自分のコンテンツで試すこと・ルーティング時はconfidenceに注目せよ、と公式に明記** | https://docs.typesafe.ai/models(Language support節) |
| 5 | データ取り扱い: 「Jev is not trained on customer requests or responses」。ZDRはエンタープライズ顧客向けオプション(sales@typesafe.ai経由) | https://docs.typesafe.ai/models(Data handling節) |
| 6 | MCA(2026-09-23版): §4.1でCustomer Dataをモデル重み変更を目的とする学習データセットに含めない(顧客の事前同意なしに)旨を契約明示。§4.3でTelemetry(技術ログ・ハッシュ・要約統計・分類・メトリクス・learnings)を無制限に処理可能(契約終了後も存続・§10.4)。§10.3で保持義務なし・いつでも削除可。§9.1でuptime SLA・サービスクレジットなし(Documentationどおりの実質動作の保証のみ) | https://typesafe.ai/legal/mca ・ 整理は https://jevwiki.ai/wiki/reference/legal-and-data.md |
| 7 | DPA: セキュリティ事故の72時間以内通知、12ヶ月ごとの顧客監査権、サブプロセッサの異議窓口15日、EU SCC(Module 2)とUK Addendum。Privacy Policy: 米国ホスティング・Inputの学習禁止・Inputの第三者非開示 | https://typesafe.ai/legal/data-processing ・ https://jevwiki.ai/wiki/reference/legal-and-data.md(Privacy Policyの文言は同整理と第三者レビュー経由で確認) |
| 8 | サブプロセッサは4社すべて米国(AWS・Modal・Slack・Google Workspace)。SOC 2 Type II(2026)がtrust.typesafe.aiに記載・レポートはRequest accessゲート(申請制) | https://trust.typesafe.ai/(直接取得はせず、https://timewell.jp/en/columns/jev-typesafe-enterprise-terms-review ・ https://wunderlandmedia.com/typesafe-ai-jev-terms-of-service-gdpr の2026-09-20〜22のレポートと jevwiki legal-and-data で確認) |
| 9 | 性能: エンドツーエンド70ms〜500ms(frontier LLM比40〜200倍)。「optimized for structured outputs and can't hallucinate」(出力がスキーマから外れない=型エラーが生じない)・較正済み確率とconfidence(RLCD)。生成LLMを同一決定API互換の出力へ制約する「System One LLM wrapper」の存在も言及 | https://typesafe.ai/blog/introducing-system-one-models-and-jev(2026-09-15発表) |
| 10 | 既知の弱点(Jev 1.13 jaggedness): 算術・カウント・日付順序・間接参照・大きく無関係なstate・adversarial text・命令の直訳的読み取り等。Noulとyes/no Choiceは異なる数値を返す(閾値流用禁止) | https://jevwiki.ai/wiki/syntheses/faq.md(FAQ 5・docs「Jev 1.13 jaggedness」ページの整理)・ https://guzli.com/blog/jev-typesafe-ai-use-cases |

## 2. 文書別の変更箇所

### 07 Jev・LLM利用仕様書(v0.4→v0.5・改版の主役)

| 節 | 変更内容 | 根拠(出典URL) |
|---|---|---|
| ヘッダ | v0.5・変更点の追記・前提文書のバージョン更新 | —(改版規律) |
| §1前置き | JevはSystem Oneモデル第一候補+生成LLMフォールバック。Parser・Embeddingのプロバイダ推奨はT1 v0.2に基づく旨を追記 | オーナー裁定・docs/plans/T1-llm-provider-selection.md v0.2 |
| §1系統表 | プロバイダ列を新設。Jev行に「TypeSafe Jev・jev-1.13.0(System Oneモデル)+フォールバックAnthropic Claude API・Sonnet 5」を明記。再試行を「なし(429・529・timeoutはフォールバック切替・出力検証失敗の再試行は廃止)」へ。コスト位置づけに「入力課金のみの低単価($0.042/MTok・出力無料)」 | docs.typesafe.ai/models(#2)・typesafe.ai/blog(#9)・T1 v0.2 |
| §1 timeout・再試行の根拠 | timeout 6秒維持(公称レイテンシ70〜500msで予算に余裕)。出力検証失敗の再試行廃止(スキーマ保証)。429・529・timeoutをフォールバック切替条件へ。SDK既定のbackoff retry無効化(timeout予算圧迫)。フォールバックLLMも再試行なし | docs.typesafe.ai/models(#2・#3)・docs.typesafe.ai/api(#1・#3)・typesafe.ai/blog(#9) |
| §1 circuit breaker | 初期値は06 D-15で確定済み・切替先をフォールバックLLMと明記 | 06 v0.5 D-15 |
| §2・§3(Parser・Embedding) | 内容は不変。相互参照のバージョン(05・06・08)を現行へ更新のみ | — |
| §4 全面書き換え | System One APIの呼び出し形式(エンドポイント・リクエスト・3質問型・応答・コンテキスト・レート制限)。state=正規化テキスト2件(正規化テキスト形式はv0.4から不変)。7軸の質問写像表(would_*・latent_yes→noul、4軸→score 5段階)。質問定義の実装イメージ(7質問のJSON)。modelはjev-1.13.0固定(エイリアス不使用)。検証規定(スキーマ保証のため再試行廃止・防御的検証のみ)。スコア計算は不変(MutualScore=min)。confidenceはnoulに付かない旨。reason廃止。フォールバックLLM切替表(429・529・timeout→切替・フォールバック失敗→縮退)。フォールバック実装(System One互換answers形式・Sonnet 5・provider記録)。送信先=TypeSafe AI(米国)。日本語リスク節(公式留保の引用・M2/G2必須・実装側軽減・観測) | docs.typesafe.ai/api(#1)・docs.typesafe.ai/models(#2・#4)・typesafe.ai/blog(#9)・jevwiki faq(#10) |
| §5(D-17) | 変更なし(Parser仕様のため) | — |
| §6 Calibration | predictionにprovider/modelキーを追加・jev_5axisの正規化とconfidenceを明記・reasonの言及を除去 | 07 §4(内部整合) |
| §7 反映事項 | 他文書への反映事項(v0.5)を新設(01・04・05・06・08・09・10・12) | —(内部整合) |

### 06 マッチングパイプライン設計書(v0.4→v0.5)

| 節 | 変更内容 | 根拠 |
|---|---|---|
| ヘッダ | v0.5・変更点の追記・前提文書のバージョン更新 | — |
| §1パイプライン図 | Layer 4の行を「Jev(TypeSafe Jev+フォールバックLLM、07)」へ | 07 v0.5 §4 |
| §1層別予算表 | Jev行に第一候補の公称レイテンシ(70〜500ms)とtimeout 6秒維持・timeout超過はフォールバック切替を記載。フォールバック切替時のレイテンシ注入(p50 2秒/p95 5秒)は10 v0.4第4.1節が検証対象とする旨 | docs.typesafe.ai/blog(#9)・07 v0.5 §1 |
| §5 Layer 4 | 呼び出し先(TypeSafe Jev jev-1.13.0固定+フォールバック切替)・送信先(TypeSafe AI・Anthropic)・jev_result格納形(provider/model)・Layer 5は経路非依存を追記。D-16のコスト前提注記(回数上限は回数ベースで不変・第一候補低単価/フォールバック高単価・80% alertに内訳・フォールバック呼び出しも1実行回数に計上) | 07 v0.5 §4・04 v0.5 D-16 |
| D-15 | 縮退条件(1)を「第一候補とフォールバックLLMの双方のAPI障害」へ。circuit breaker開放中は第一候補を停止しフォールバックLLMで継続・フォールバック継続失敗のみskipped保留。半開は第一候補への試験。v0.5注記(単発429・529は第一候補内の切替で吸収・継続障害のみcircuit breaker) | 07 v0.5 §1・§4 |

### 04 システムアーキテクチャ設計書(v0.4→v0.5)

| 節 | 変更内容 | 根拠 |
|---|---|---|
| ヘッダ | v0.5・変更点の追記 | — |
| §3技術スタック表 | 「LLM(Jev・Parser)」を「LLM(Jev)」「LLM(Parser)」の2行に分離。Jev=TypeSafe Jev第一候補+生成LLMフォールバック(選定理由)。Parser=Anthropic Haiku 4.5(T1 v0.2推奨)。Embedding行に768次元条件を追記 | T1 v0.2・04 D-14 v0.5 |
| D-14 | 基準3にSystem Oneモデルの区分追加。**v0.5追記(採用記録)**: Layer 4第一候補=TypeSafe Jev・フォールバック=Anthropic Sonnet 5。採用根拠(基準3の読み替え・学習禁止明記・可用性はフォールバック構成で担保) | docs.typesafe.ai/models(#2・#4・#5)・typesafe.ai/legal/mca(#6)・typesafe.ai/blog(#9) |
| D-16 | 再試行計上条項を「実際のAPI呼び出しをすべて計上(フォールバックLLM切替呼び出しを含む)」へ読み替え(v0.5)。コスト前提注記(第一候補低単価・フォールバック切替の長期化はコスト増)。80% alertの源流レポートに第一候補・フォールバック別の内訳を追加 | 07 v0.5 §1・T1 v0.2第4節 |
| §5 | コスト保護のフロー図に「レポート(第一候補とフォールバック別の実行回数内訳を含む、v0.5)」を追記 | 04 D-16 v0.5と内部整合 |
| §8 | 反映表のD-14・D-16行を更新・他文書への反映事項(v0.5)を新設 | — |

### 01 要件定義書(v0.4→v0.5)

| 節 | 変更内容 | 根拠 |
|---|---|---|
| ヘッダ | v0.5・変更点の追記 | — |
| 第12節 | Layer 4定義を「LLMベースの判定エンジン」からSystem Oneモデル(TypeSafe Jev)第一候補+生成LLMフォールバックの定義へ | 07 v0.5 §4・04 D-14 v0.5 |
| 第13節冒頭 | 「単一スコアを出させない」の方針がSystem One質問群(noul/score)のインターフェースと合致する旨を追記 | 07 v0.5 §4 |
| 第13節末尾 | 出力検証規定を更新(第一候補はスキーマ保証で再試行なし・429・529・timeoutでフォールバック・フォールバック失敗で保留)。reasonは生成しない旨に更新 | 07 v0.5 §1・§4 |
| 第21節 | 送信先にTypeSafe AI(api.typesafe.ai・米国)を明記・Parser/Embedding/フォールバックの送信先も列挙。reason言及節を「自由文を含めない」へ更新し30日定期削除は維持 | 07 v0.5 §4・08 v0.5 D-14 |
| 第26節 | D-14の記録に「v0.5でLayer 4のプロバイダを確定(オーナー裁定C案)」を追記 | — |

### 08 プライバシー・安全設計書(v0.4→v0.5)

| 節 | 変更内容 | 根拠 |
|---|---|---|
| ヘッダ | v0.5・変更点の追記・前提文書のバージョン更新 | — |
| §2.5 | 削除範囲の根拠を更新(jev_resultには個人由来の自由文は格納されない・reason廃止。条件由来の判定値の残存経路を閉じるのが目的)。30日定期削除は維持 | 07 v0.5 §4 |
| §3 | 送信先にTypeSafe AI(api.typesafe.ai・米国)を明記。Parser・Embedding・フォールバックLLMも列挙。フォールバック送信も同一記録経路 | 07 v0.5 §4 |
| D-14 | **v0.5追記(TypeSafe Jevの契約面の評価と受容)**: 採用根拠(学習禁止のdocs・MCA §4.1・Privacy Policy明文)。受容(米国のみ・保持日数明記なし・SLAなし → LLMフォールバック構成で担保)。DPAの内容(72時間通知・監査権)。M2期間中の詳細確認(ZDR・漏洩通知条項・Telemetry条項)。日本語リスクの一言(管理は09の日本語評価ゲート) | docs.typesafe.ai/models(#4・#5)・typesafe.ai/legal/mca(#6)・typesafe.ai/legal/data-processing(#7) |
| §6 | 反映表のD-14行を更新・他文書への反映事項(v0.5)を新設 | — |

### 05 データモデル・API仕様書(v0.4→v0.5)

| 節 | 変更内容 | 根拠 |
|---|---|---|
| ヘッダ | v0.5・変更点の追記 | — |
| 第2節 match_candidates.jev_result | 格納形をSystem One互換へ更新(would_*はnoul確率0〜1・jev_5axisはscore正規化値0〜1とconfidence・provider・model)。**reason廃止**(実物Jevはテキスト生成せず・フォールバックLLM時も一貫して無し) | 07 v0.5 §4・docs.typesafe.ai/api(#1) |
| 第2節 calibration_records.prediction | 数値のみを明記。jev_5axisの正規化値+confidence・provider/modelキー追加 | 07 v0.5 §4・§6 |

### 09 検証・評価計画書(v0.4→v0.5)

| 節 | 変更内容 | 根拠 |
|---|---|---|
| ヘッダ | v0.5・変更点の追記・前提文書のバージョン更新 | — |
| 第4節冒頭 | **評価対象の明確化**(TypeSafe Jevのnoul確率・score正規化値・confidence。フォールバックLLMも同一ゴールドセットでprovider分離観測)。**日本語評価をM2(G2)で必須**と規定(公式留保の引用・合格基準の値はG2前にオーナーが事前固定・不合格時はフォールバック繰り上げのオーナー判断) | docs.typesafe.ai/models(Language support)・07 v0.5 §4 |
| 第7節 | 他文書への反映事項(v0.5)を新設(12・07・10) | — |

### 10 テスト環境・非機能検証計画書(v0.3→v0.4)

| 節 | 変更内容 | 根拠 |
|---|---|---|
| ヘッダ | v0.4・変更点の追記・前提文書のバージョン更新 | — |
| 第1節 | **StubJevの応答形式をSystem One互換のanswers形式**へ規定(model+answers(noul値・score値・confidence)+usage)。フォールバック経路のスタブは同形式でconfidence=null。第3節の「Jevスタブで双方向判定値を固定」にも形式参照を追記 | docs.typesafe.ai/api(#1)・07 v0.5 §4 |
| 第4.1節 | Jevのレイテンシ注入(p50 2秒/p95 5秒)の意図を明記(第一候補の公称レイテンシではなくフォールバックLLM切替時の上限を検証) | docs.typesafe.ai/blog(#9)・07 v0.5 §1 |
| 第4.5節 | 縮退試験の確認事項を更新: (1)第一候補の429・529・timeout注入でフォールバック切替・判定継続(providerにfallback_llm記録)(2)フォールバック継続失敗のみskipped保留(3)提案なし(4)circuit breaker開放中はフォールバックLLMで継続・半開で第一候補へ再試験(5)(6)は従来どおり | 07 v0.5 §1・§4・06 v0.5 D-15 |

### 12 開発ロードマップ(v0.1→v0.2)

| 節 | 変更内容 | 根拠 |
|---|---|---|
| ヘッダ | v0.2・v0.2の変更点を新設 | — |
| §1.1前提 | 「LLMプロバイダのみ未確定」を更新(Layer 4は確定済み・Parser・EmbeddingはT1 v0.2推奨を契約確認後に確定) | — |
| M2スコープ6 | Layer 4を「第一候補=TypeSafe Jev(System Oneモデル・jev-1.13.0)+フォールバックLLM(Anthropic Sonnet 5)切替。出力検証失敗の再試行は廃止・429・529・timeoutで切替・provider/model記録・フォールバック呼び出しも1実行回数に計上」へ更新 | 07 v0.5 §1・§4・04 v0.5 D-16 |
| M2スコープ9・10 | 源流レポートに内訳(v0.2)・縮退運転をフォールバック切替込みの二段構成へ更新 | 04 v0.5 D-16・06 v0.5 D-15 |
| G2完了条件 | 縮退試験の文言をSystem One構成へ更新。**日本語評価(G2必須・09 v0.5第4節)を追加**(合格しない場合はフォールバック繰り上げのオーナー判断) | docs.typesafe.ai/models(#4)・09 v0.5第4節 |
| §4 T1トラック | Layer 4確定済み・Parser・Embeddingの契約確認・**M2期間中にTypeSafe契約詳細(ZDR・漏洩通知条項・Telemetry条項)を確認**へ更新 | 08 v0.5 D-14 |
| §7リスク表 | T1停滞行の更新と**「TypeSafe Jevの日本語精度が不合格」の行を新設**(フォールバックLLMへ繰り上げ・実装はanswers形式で統一済みのため切替は経路設定の変更で済む) | 09 v0.5第4節・07 v0.5 §4 |

### docs/plans/T1-llm-provider-selection.md(v0.1→v0.2)

| 節 | 変更内容 | 根拠 |
|---|---|---|
| ヘッダ | v0.2・Jev系統の全面改訂を記録 | — |
| 結論(要約) | Jev系統(TypeSafe Jev第一候補+Sonnet 5フォールバック)への改訂・Parser・Embedding不変。**コスト訂正(v0.1の計算誤り)**を明記 | オーナー裁定 |
| §1 | 3系統要件表のJev行をSystem One APIの要件へ更新 | 07 v0.5 §4 |
| §2.4(新設) | TypeSafe Jevの比較(System One仕様のサマリ・D-14の6基準+契約5条件での判定表・出典一覧) | docs.typesafe.ai/api・models・typesafe.ai/blog・typesafe.ai/legal/mca・legal/data-processing |
| §3 | 推奨表のJev行をTypeSafe Jevへ。Gateway切替の優先順にJev系統(TypeSafe Jev→Sonnet 5→Bedrock)を追加。04 v0.5の技術スタック表の行分けに追従 | — |
| §4 | **コスト試算の全面書き換え**。v0.1の計算誤りの訂正を明記(式に対する結果の1/1000記載: Jev $1.80→$1,800・Parser $0.11→$114。Embeddingの計算は正しかった)。v0.2推奨案(Jev=TypeSafe Jev)は通常時月次約$140・上限時約$190。フォールバック全件切替の継続時は約$1,915/月に達する旨と80% alert内訳観測の指摘 | T1 §4の式(式自体はv0.1と同一) |
| §5 | 推奨をJev系統(裁定済み・採用条件=G2日本語評価合格)とParser・Embedding(不変)に整理。縮退先を更新 | — |
| §6 | チェックリスト表にTypeSafe列を追加(5条件すべての現状とM2確認事項) | typesafe.ai/legal・trust.typesafe.ai |
| §7 | 未確認事項に(9)TypeSafe Jevの日本語精度実測・(10)ZDR・Telemetry条項の詳細を追加 | — |

### docs/reviews/jev-systemone-revision.md(本メモ)

新設。変更箇所一覧と一次出典(本メモ自体)。

## 3. 未確認事項(正直な申告)

1. **MCA/DPA/Privacy Policyの全条精読はしていない。** 本改版で参照したのはMCA §4.1・§4.3・§9.1・§10.3・§10.4、DPA §5.2(72時間通知)・§5.3(監査)・§6.1(SCC)に相当する条項と、その整理(jevwiki legal-and-data)である。条項番号は整理文書の引用に基づく
2. **SOC 2 Type IIの存在はtrust.typesafe.aiの直接取得ではなく、第三者レビュー(timewell.jp・wunderlandmedia.com・2026-09-20〜22)とjevwikiの整理を経由して確認した。** レポート本体は申請制のため未読
3. **Privacy Policyの原文は取得できておらず、文言はjevwikiと第三者レビューの引用(「We will not train or fine tune...」・米国ホスティング)を経由している。** 契約前に原文の直接確認を推奨
4. **「Jev 1.13 jaggedness」のdocsページそのものは取得できていない**。既知の弱点の内容はjevwiki faqとguzliの引用に基づく
5. **SDKのretry無効化の実装手段(Python typesafe-sdkのRetryPolicy)はdocs.typesafe.aiのSDK APIページのsystem_oneにretryパラメータが存在すること(context7で取得)までしか確認していない。** 実装時のSDK docs確認を推奨
6. **System One LLM wrapper(公式ブログの言及)の提供形態(docs上のページ・SDKの有無)は未確認。** 本改版では外部アダプタの採用を必須とせず、Gateway実装内で同等形式を規定する方針にした(07 v0.5 §4)
7. **レート制限は「動的調整・予告なく変わる」ことがdocsに明記されており**、本改版で記載した値(250,000 tok/s・1,200 req/min)は2026-09-28時点の値である

## 4. 確定済み仕様判断との対応(変更しなかったもの)

| 確定済み判断 | 本改版での扱い |
|---|---|
| jev_resultのreason(言い換え自由文)は廃止・フォールバックLLM時も一貫して無し | 07 §4・05・08 §2.5・01第13節・第21節に反映。30日定期削除規定自体は維持(根拠文言のみ更新) |
| timeout 6秒維持(層別予算p95 10秒・Jev≤5秒と無矛盾) | 07 §1・06 §1で維持を明記 |
| 出力検証失敗の再試行は廃止・429/529・timeoutはフォールバック切替条件 | 07 §1・§4・06 §5・10 §4.5・12 M2スコープ6に反映 |
| SDK既定のbackoff retry無効化 | 07 §1に規定 |
| D-15 circuit breakerと接続(切替先=フォールバックLLM) | 06 D-15・10 §4.5に反映 |
| D-16回数上限は回数ベースで不変・コスト前提の注記 | 04 D-16・06 §5に反映(フォールバックLLM計上への読み替えを含む) |
| MVP採用根拠=学習禁止明記・米国所在/SLAなしはLLMフォールバック構成で担保・ZDRと漏洩通知条項はM2確認 | 08 D-14 v0.5・04 D-14 v0.5・T1 §2.4・12 T1トラックに反映 |
| 日本語リスクの08と09への記録・M2で日本語評価 | 08 D-14 v0.5(一言)・09第4節(G2必須)・07 §4・12 G2条件に反映 |
| K_j=8・D-16回数上限の数値・層別予算・raw_text不送信・正規化テキストのみ送信・送信記録の内容規定 | いずれも変更せず |
