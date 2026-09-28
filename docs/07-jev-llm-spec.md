# LATCH Jev・LLM利用仕様書

- 文書バージョン: v0.6
- ステータス: Draft
- プロダクト名: LATCH
- 作成日: 2026-09-27(v0.3・v0.4更新: 同日。v0.5・v0.6更新: 2026-09-28)
- 前提文書: 01 要件定義書 v0.5 / 02 スコープ合意書 v0.3 / 03 UX仕様書 v0.4 / 04 システムアーキテクチャ設計書 v0.5 / 05 データモデル・API仕様書 v0.5 / 06 マッチングパイプライン設計書 v0.5
- v0.6(2026-09-28): 規則7に場所の語の優先順位とノンアルコール明示を追記(G1実測FN=A-034対応・オーナー承認)
- v0.5の変更点(TypeSafe Jev採用): Layer 4(Jev系統)の第一候補を、テキスト生成を行わないSystem Oneモデル「Jev」(TypeSafe AI・jev-1.13.0)とし、生成LLMを縮退・フォールバックとする(2026-09-28オーナー裁定・C案)。全変更箇所と出典はdocs/reviews/jev-systemone-revision.mdに記録。主要変更は次のとおり。
  - §1: 系統表にJevのプロバイダ(TypeSafe Jev第一候補・生成LLMフォールバック)を明記。timeout 6秒は維持し、再試行規定を再設計 — System Oneモデルは出力スキーマを保証するため「出力検証失敗の再試行1回」を廃止し、429・529・timeoutをフォールバックLLMへの切替条件とする。SDK既定のbackoff retryはtimeout予算を圧迫するため無効化する
  - §4: Jev判定仕様を全面書き換え。「LLMにプロンプトで7軸の確率JSONを生成させる」方式を廃止し、System One APIへの質問群(7軸をnoul/scoreへ写像)の送信に置き換えた。jev_resultのreason(言い換え自由文)は廃止 — 実物のJevはテキストを生成せず、フォールバックLLM時も含め一貫して無しとする(05 v0.5・08 v0.5第2.5節と整合)
  - Jevの送信先はTypeSafe AI(api.typesafe.ai・米国)を明記(第4節・01第21節)
  - 日本語リスク: TypeSafe Jevの公式の言語サポートは「英語が主訓練言語・CJKは同等ではない」と明記する。LATCHのstateは日本語であるため、リスク記述を第4節に設け、M2(G2)での日本語評価を必須とする(09 v0.5第4節)
- v0.4の変更点(prototype整合): 下書き保存(05 v0.4)と02 v0.3 D-03再決定(公開設定 hidden_until_match / summary_only)を反映。主要変更は次のとおり。
  - Embeddingの呼び出しタイミングを「active作成・active化(draft→active)・active更新後」へ明確化。下書き保存(status=draft)ではEmbeddingしない(第1節。06 v0.4)
  - Parser入力に300字上限(raw_textのUI制限と同一値)のサーバ側検証を追加した(第2節。03 v0.4第3節)
  - 補完規則のexpires_atを4選択肢の選択式に、visibilityを公開設定(hidden_until_match / summary_only、既定hidden_until_match)へ更新(第2節)
  - 判定可能NGの例から「公開範囲」を除去(公開設定はLayer 1の判定対象ではない、06 v0.4)。Jevの正規化テキストから[hard] visibility行を除去し、social_fitの軸から「公開範囲」を除去した(第4節。表示制御であり意味判定の材料に含めない)
- v0.3の変更点: 総括レビュー(docs/reviews/final-review.md)の指摘を反映。主要変更は次のとおり。
  - FR-10: JevとEmbeddingのtimeout値・再試行回数を確定(第1節)。06 v0.3第1節の層別予算配分(初期LATCH判定p95 10秒)と整合する値を根拠付きで決定。circuit breakerのしきい値初期値の確定は06 D-15側の改版事項として第7節に記録
  - FR-43: ParserのLLM障害(503 LLM_UNAVAILABLE、再試行ボタン)と構造化不能(422 VALIDATION_ERROR、フォームフォールバック)の応答区別を規定(第2節・D-17。05 v0.3のエラーcode列挙と整合)
  - FR-42: MVPではnegative_constraintsは常に空配列である旨を明記(規則5・第2節)
  - FR-22関連: jev_resultのreason(自由文)は30日で定期削除する短期保持とし、calibration_recordsにはreasonを保存しない方針を確定(第4節・第6節。保持ポリシーの全文と削除時の範囲は08 v0.3第2.5節。なおv0.5でreason自体を廃止した)

本書を読むうえでの取り決めを先に示す。本書はLLM呼び出し3系統(Intent Parser / Embedding / Jev)の仕様を確定し、未決事項D-17を解消する。3系統のうちJevはSystem Oneモデル(TypeSafe Jev)を第一候補とし、生成LLMをフォールバックとする(2026-09-28オーナー裁定・C案)。ParserとEmbeddingは生成・埋め込みLLMであり、プロバイダの推奨はdocs/plans/T1-llm-provider-selection.md(v0.2、以下T1 v0.2)に基づく。プロバイダの選定基準は04 D-14に、コスト上限は04 D-16に、呼び出し条件とK上限は06に従い、本書は質問群・入出力・精度・性能を担う。送信データはすべて06の決定どおり正規化テキストで、raw_textを外部へ送る経路を持たない(01第21節)。

## 1. LLM呼び出しの全体像

| 系統 | プロバイダ(第一候補) | 呼び出しタイミング | 同期・非同期 | timeout | 再試行 | コスト位置づけ |
|---|---|---|---|---|---|---|
| Intent Parser | Anthropic Claude API・Haiku 4.5(T1 v0.2) | POST /v1/intents/parse(03第3節の確認フロー) | 同期(ユーザーが待つ) | 10秒(D-17) | なし(即フォールバックへ) | 1登録1回。件数は最も多いが単価は低い |
| Embedding | Google Gemini API有料tier・gemini-embedding-001(T1 v0.2) | Intentのactive作成・active化(draft→active)・active更新後(イベント駆動、06 v0.5第9節の第2段トリガー。**draft状態では呼び出さない** — POSTでの下書き保存・draft中のPATCH更新とも、06 v0.5第9節) | 非同期 | 2秒 | なし(失敗はD-15のバックフィル経路で回収) | 1 Intent 1回+失敗時のバックフィル(06 D-15) |
| Jev | **TypeSafe Jev・jev-1.13.0(System Oneモデル)**。フォールバックはAnthropic Claude API・Sonnet 5(第4節) | Layer 4(06、K_j=8回) | 非同期 | 6秒 | なし(429・529・timeoutはフォールバックLLMへ切替。出力検証失敗の再試行は廃止、第4節) | 第一候補は入力課金のみの低単価($0.042/MTok・出力無料)。フォールバックLLMは高単価。04 D-16の日次・月次上限で制御 |

いずれもLLM Gateway(04)経由で呼び出し、送信先と送信データ種別を記録する(01第21節)。Jevの送信先はTypeSafe AI(api.typesafe.ai)であり、フォールバックLLMはAnthropic Claude APIである(第4節)。Jevの1実行回数は1候補(両方向を1呼び出しで判定)と数える(06第5節)。フォールバックLLMによる判定も同一の「1実行回数」に計上する(04 D-16)。

**timeout・再試行の根拠(FR-10)。** 値は06 v0.5第1節の層別予算配分(初期LATCH判定p95 10秒: Embedding ≤2秒 / Layer 1〜3 ≤1秒 / Jev ≤5秒 / Layer 5+通知 ≤2秒)と整合させる。

- **Embedding timeout 2秒・再試行なし。** 予算2秒と同値とする。Embedding APIの典型的p95は数百ms〜1秒であり、1 Intent 1回の軽量呼び出しに対して十分な余裕がある。Embeddingは初期LATCH判定の必須前段ではあるが、失敗時にパイプライン全体を止めるべきでない — 再試行は10秒予算を圧迫し、失敗傾向のあるプロバイダへの連打はコストを悪化させる。よってtimeout超過は失敗としてD-15のバックフィル経路で回収する(失敗IntentはLayer 1〜2の対象外、回復後のembedding_completedでパイプラインへ復帰、06 v0.5第9節)
- **Jev timeout 6秒・再試行なし(v0.5で再設計)。** timeout 6秒は予算5秒に1秒の吸収分を加えた値であり、v0.4以前と同一で維持する。第一候補のTypeSafe Jevの公称レイテンシは70〜500ms(typesafe.ai公式ブログ・2026-09-15)であり、予算に対して十分な余裕を持つ。再試行規定は次のとおり再設計した。
  - **出力検証失敗の再試行(1回)は廃止する。** System Oneモデルは出力が質問で定義したスキーマの外に出ない(型エラーが生じない。typesafe.ai公式ブログ)ため、v0.4の「JSON欠損等の再試行1回」の前提が消えた。受信側の値域検証は残すが(第4節)、失敗は実装不整合として扱い再試行しない
  - **429(レート制限)・529(過負荷)・timeoutをフォールバックLLMへの切替条件とする。** TypeSafeのSDKは429・529時にbackoff retryが既定動作であるが(docs.typesafe.ai/api)、LATCH側はこれを無効化する — retryはtimeout 6秒の予算を圧迫し、10秒の層別予算の後段を危うくする。切替によって判定は止まらず、06 D-15のcircuit breakerの切替先はフォールバックLLMである
  - フォールバックLLMも同じtimeout 6秒・再試行なしを適用する。フォールバックLLMの失敗(timeout・429・5xx・出力検証失敗)は縮退(D-15: 候補はskippedで保留)へ落とす。第一候補とフォールバックの双方で再試行を設けないことで、遅延とコストの二重払いを構造的に排除する
- **Parser timeout 10秒・再試行なし** はD-17のとおり確定済み。タイムアウト時は再試行せず構造化フォームへフォールバックする
- Jev層のcircuit breaker(LLM Gatewayのエラー率・レイテンシしきい値で開放、一定時間後に半開)の初期値は06 v0.5 D-15で確定済みである(エラー率50%超またはp95レイテンシ超過×測定窓1分で開放、60秒後に半開)。v0.5では開放中の切替先をフォールバックLLMと明記した(第4節)

## 2. Intent Parser仕様

### システムプロンプト(全文)

```text
あなたはLATCHのIntent Parserである。ユーザーが書いた「条件付きの意思」を
構造化データへ変換する。出力は指定のJSONのみとし、説明文を含めない。

入力は日本語の自然文で(最大300字。アプリ層で事前検証する)、「今〜数日以内の食事・飲み・軽いアクティビティ」に
関する意思である。現在日付は {current_date} とする。

規則:
1. 抽出できるフィールドのみ埋める。推測で値を作らない。
2. 「〜くらい」「〜程度」の曖昧表現は次の丸め規則で確定値に変換する。
   - 予算「5000円くらい」→ budget.max = 5000(上限として扱う。minは設定しない)
   - 人数「2〜4人くらい」→ min=2, max=4。「3人以上なら」→ min=3, max=4
     (参加人数の上限は4)。人数に言及がない場合は両方nullを返す
   - 時間「20時以降」→ start=20:00, end=null(未指定)。終了時刻は推測しない
3. 相対表現(今日・明日・土曜・今夜)は現在日付から解決する。
4. 地名・ランドマークは location.name にそのまま残す。座標は補完しない
   (後段のジオコーディングで補完する)。
5. 「会社関係の人は避けたい」のように、システムが判定データを持たない除外
   条件は ng_unverifiable 配列に入れる。negative_constraints には入れない
   (negative_constraints はMVPでは常に空配列である。理由は次節)。
6. ユーザーの感情や意向の補足(「軽く」「ゆっくり」等)は soft_constraints へ
   抜き出す。
7. 飲酒の関与は alcohol_involved(boolean)で判定する(08 D-10)。
   - category.primary が drinking なら常に true。
   - 他のカテゴリでも「食事のついでに軽く飲む」のような meal 内の言及を含め、
     飲酒を示す語(飲む・飲み・酒・呑む・バー・ビール・サワー等)があれば true。
   - バー・酒場など飲酒の場を表す語があるときは、本人がアルコールを飲まない
     つもりでも(「コーラにする」等)true とする。場所の語を飲み物の意向より
     優先する(08 D-10: 飲酒を伴う場に身を置く機会として扱うため)。
   - 「ノンアルコールビール」のようにアルコールでないことが明示された飲み物は
     true の根拠にしない。
   - アルコールを指さない用法(「コーヒーを飲む」等)は false。

出力JSONスキーマ(この形式のみ認める):
{
  "category": {"primary": "meal|drinking|activity", "secondary": string|null},
  "alcohol_involved": boolean,
  "time": {"start": "ISO8601(JST)", "end": "ISO8601|null", "flexibility_minutes": null},
  "location": {"name": string, "radius_m": integer|null, "flexibility": null},
  "budget": {"max": integer|null, "currency": "JPY"},
  "participants": {"min": integer|null, "max": integer|null},
  "soft_constraints": [string],
  "negative_constraints": [string],
  "ng_unverifiable": [string]
}
```

### 解釈規則(03 D-19の実装)

**入力長の検証(v0.4)。** parseリクエストのtextはraw_textの上限300字と同一値で検証し、超過は422 VALIDATION_ERRORを返す。UIはmaxlengthで入力を遮断し、文字数カウンタを表示する(03 v0.4第3節)ため、APIへの直接呼び出しに対する防御であり、切り詰めは行わない。

Parserはフィールドの抽出のみを行い、デフォルトの補完はParser後のアプリ層で単一の規則として適用する。二重実装による不整合を防ぐためだ。

| フィールド | Parserの出力 | アプリ層の補完(03 D-19) |
|---|---|---|
| time.end | null可 | nullなら time.start + 3時間 |
| expires_at | Parserは出力しない | time.start + 3時間に最も近い選択肢(4値の選択式「今夜 23:30 / 明日 12:00 / 明日 23:30 / 3日後まで」。03 v0.4第3節) |
| participants | null可 | min=2 / max=2 |
| visibility | Parserは出力しない | hidden_until_match(公開設定。「条件一致までは非公開 / 候補にだけ概要を表示」の2値。確認モーダルで選択、02 v0.3 D-03) |
| notification_level | Parserは出力しない | proposals_only(お知らせ設定。「一致したときだけ / 近い候補も知らせる / 通知しない」の3値。預け方パネルで選択、03 v0.4第3節。v0.4新設) |
| budget.max | null可 | nullのまま(制約なし) |
| location.radius_m | null可 | nullなら1,000(既定半径)。条件リストで修正できる(03 v0.4のD-19表にも行がある) |

必須3フィールド(category / time.start / location.name)が抽出できない場合と、LLM障害の場合とでは応答を区別する(FR-43)。

- **構造化不能(必須欠落)**: 422 VALIDATION_ERROR(05 v0.5のエラーcode)を返す。クライアントは構造化フォームへのフォールバック(03第3節)を提示し、選択・入力を促す
- **LLM障害(timeout・API障害・レート制限)**: 503 LLM_UNAVAILABLE(05 v0.5のエラーcode)を返す。クライアントは入力テキストを保持した再試行ボタンを提示する(07 D-17)。クライアントは503と422で分岐し、再試行と手動入力の提示を切り替えられる

time.flexibility_minutes / location.flexibilityはMVPでは常にnullとし、Parserは抽出しない(03 D-19のデフォルト=固定扱い)。06 Layer 1のflexibilityによる範囲拡張は、将来バージョンで抽出を有効化した時点で機能する。

**negative_constraintsは常に空(FR-42)。** 規則5により、判定不能NGはng_unverifiableへ入れ、negative_constraintsには入れない。判定可能なNG(ブロック)は独立カラムとLayer 1(06第2節)で判定するため、structured_data.negative_constraints(05 v0.5)はMVPでは常に空配列である(旧来「公開範囲」を判定可能NGの例としていたが、公開設定はLayer 1の判定対象ではないためv0.4で除去した。06 v0.4)。空であることが正常系である旨を明示するものであり、実装者がLayer 1での照合を二重に実装したり、09の期待値表に正例を設けたりしないこと。

**alcohol_involvedの判定と伝播(08 D-10の実装)。** alcohol_involvedは常にtrue/falseを出力し、アプリ層での補完はない。規則はプロンプト規則7のとおり、category.primary=drinkingは常にtrue、「食事のついでに軽く飲む」のようなmeal内の言及を含め、飲酒を示す語(飲む・飲み・酒・呑む・バー・ビール・サワー等)の検出でもtrueとする。バー・酒場など飲酒の場を表す語は飲み物の意向に優先してtrueとし、「ノンアルコールビール」のようにアルコールでないことが明示された飲み物はtrueの根拠にしない(v0.6)。フラグは保存時にintents.alcohol_involved(05)へ格納され、保存時にcategory_primary=drinkingであればサーバ側でtrueを確定する(05 v0.5)。作成時の年齢検証(20歳未満のユーザーは飲酒を含むIntentを作成不可、05)とLayer 1の年齢条件(06第2節)がこのフラグを使う。

### D-04連携(判定不能NG条件)

ng_unverifiableが空でない場合、応答のwarningsに `{code: "NG_CONDITION_DOWNGRADED", condition: ...}` を載せる(05第5節)。クライアントは条件リストに注意表示を出し(02 D-04、03 v0.4第3節)、保存時に同条件はstructured_dataのsoft側へ`downgraded_from_ng: true`のフラグ付きで格納される(05 v0.5のstructured_dataスキーマ)。Hard Filterの対象にはならない。

## 3. Embedding仕様

Embedding対象テキストは、構造化データから次の形式で生成する(raw_textは使わない、06第3節)。

```text
{category.primary} / {時間帯の表現(例: 平日夜20-23時)} / {location.name} / {人数の表現} / {soft_constraintsを「・」で連結}
```

モデルは04 D-14の基準を満たす多言語対応の埋め込みモデルとし、次元は768(05のvector(768))。モデル識別子と版をintents.embedding_modelに記録し(01第18節)、モデル変更時はこの記録で再エンベディング対象を特定してバックフィルする。Embeddingの呼び出しはtimeout 2秒・再試行なしであり(第1節)、失敗時の扱い(保存は行い、回復後にバックフィル、完了時にembedding_completedで復帰)は06 D-15・第9節に従う。

## 4. Jev判定仕様(v0.5: TypeSafe Jev・System Oneモデル)

Layer 4のJev判定は、System Oneモデル「Jev」(TypeSafe AI・2026-09-15発表)を第一候補として実装する。System Oneモデルは自由文の生成を放棄した代わりに、入力されたstateに対して定義済みの質問群を並列評価し、型安全な値と較正済み確率を返すモデルクラスである。出力がスキーマから外れないため、v0.4の「LLMにプロンプトで7軸の確率JSONを生成させる」方式は廃止し、本節の質問群方式へ置き換えた。Jevに到達できない場合(429・529・timeout)は生成LLMへフォールバックする(§1・本節末尾)。

### 呼び出し形式

第一候補のAPI仕様は次のとおりである(出典: docs.typesafe.ai/api・docs.typesafe.ai/models。確認日2026-09-28、全出典はdocs/reviews/jev-systemone-revision.md)。

- エンドポイント: POST https://api.typesafe.ai/v1/systemone。Authorization: Bearer <API_KEY>・Content-Type: application/json
- リクエスト: state(評価対象。文字列/JSONオブジェクト/配列)+ model + questions(質問名→質問定義のマップ)。質問のキーはLATCH側で命名し、応答は同キーで返る(キー自体はモデルに送られない)
- 質問型は3種ある: **noul**(yes/no質問。YES確率を返す)/ **choice**(最大255択の選択。選択値+確率分布+confidence)/ **score**(2〜10段階の順序尺度。確率加重値+分布+confidence)
- instructionsは文字列のほかオブジェクトが使え、質問本体と参照データを分離できる(バッククォートで参照)
- 応答: model(応答したバージョンID)+ answers(リクエストと同キー)+ usage(input_tokens/output_tokens)
- コンテキストは64kトークン/リクエスト(state+全質問)、うちstate+最長質問は32kトークン。LATCHの正規化テキスト2件+7質問に対して十分な大きさである
- レート制限は250,000トークン/秒+1,200リクエスト/分(docs.typesafe.ai/models。動的調整が明記され、予告なく変わる)。超過時は429が返る(§1のとおりフォールバックへ)

送信データはv0.4と同じく、2つのIntentの正規化テキストに限る(raw_textは送らない、06第5節・01第21節)。stateは正規化テキスト2件をJSONオブジェクトで運ぶ。

```text
state = {
  "intent_a": "Intent A:\n[hard] category: drinking\n…(正規化テキスト)",
  "intent_b": "Intent B:\n[hard] category: meal\n…"
}
```

### 正規化テキスト形式

2つのIntentを次の形式に正規化して送る(06)。hard/softの区別をタグで示す。**visibility(公開設定)は表示レベルの制御であり、成立可能性の意味判定の材料に含めないため、正規化テキストには含めない(v0.4)**。ついでにお知らせ設定(notification_level)も含めない(通知経路の設定であり判定材料ではない)。

```text
Intent A:
[hard] category: drinking
[hard] time: 2026-09-26 20:00–23:00
[hard] location: 天文館周辺 半径2km
[hard] participants: 2–4人
[hard] budget_max: 5000円
[soft] 軽く飲みたい
[soft] 会社関係の人は避けたい(システムで判定不能)

Intent B:
[hard] category: meal
[hard] time: 2026-09-26 19:30–22:30
…(同形式)
```

### 質問の定義(7軸の写像)

01第13節の7軸をSystem Oneの質問型へ写像する。would_a_accept_b・would_b_accept_a・latent_yesの3軸はYES確率そのものを信号とするためnoulへ、purpose_fit・mood_fit・timing_fit・social_fitの4軸は段階的な適合度であるためscore(5段階)へ写像する。7質問は1リクエストで送信し、System Oneモデルはstateを1回読み込んで全質問を並列評価する(質問の追加は応答時間にほぼ影響しない。docs.typesafe.ai/models)。

| 軸(01第13節) | 質問型 | 質問の内容 | 尺度 |
|---|---|---|---|
| would_a_accept_b | noul | Aの作成者の立場で、Bとの成立を提示されたときyesと答える確率 | 0(no)〜1(yes) |
| would_b_accept_a | noul | Bの作成者の立場で、Aとの成立を提示されたときyesと答える確率 | 同上 |
| purpose_fit | score | 目的・カテゴリの適合度(飲みたい×食べたいのズレ等) | 5段階(0〜4) |
| mood_fit | score | 雰囲気・軽さの適合度(「軽く」×「がっつり」等) | 同上 |
| timing_fit | score | 時間帯・所要時間の適合度 | 同上 |
| social_fit | score | 人数・社会的文脈(立場・関係性)の適合度 | 同上 |
| latent_yes | noul | どちらかが明示していないが、意図の範囲内でYESになり得る可能性 | 0〜1 |

would_*の質問には、v0.4の判定規則1・2(相手側の条件も考慮する・[hard]条件との矛盾はyesから遠ざける)の趣旨をinstructionsに含める。規則3(根拠がなければ0.50)は不要である — System OneモデルはRLCD(Reinforcement Learning for Calibrated Decisions)で較正されており、根拠がなければ0.5付近の値として自然に返る。規則4(reason付け)は廃止した(本節末尾)。

質問定義の実装イメージを次に示す。構造・質問型・キー名が確定値であり、instructions・criteriaの文言の最終確定はM2の日本語評価(09 v0.5第4節)で行う。

```json
{
  "state": {
    "intent_a": "Intent A:\n[hard] category: drinking\n…",
    "intent_b": "Intent B:\n[hard] category: meal\n…"
  },
  "model": "jev-1.13.0",
  "questions": {
    "would_a_accept_b": {
      "type": "noul",
      "instructions": {
        "a": "`state.intent_a`",
        "b": "`state.intent_b`",
        "question": "`a`の作成者が`b`との成立を提示されたときyesと答える確率。`a`の[hard]条件を満たすか、`a`の[soft]条件と判定不能NG条件に触れないことを含めて判定する。`b`が`a`の[hard]条件と意味的に矛盾する場合(予算感の著しい乖離が食事内容を成立させない等)はyesから遠ざける"
      },
      "criteria": { "true": "成立し得る提案である", "false": "成立しない提案である" }
    },
    "would_b_accept_a": { "…(逆方向・同形式)": "…" },
    "purpose_fit": {
      "type": "score",
      "instructions": "`a`と`b`の目的・カテゴリの適合度(飲みたい×食べたいのズレ等)",
      "criteria": ["目的が根本的に異なる", "目的が近いが核心がずれる", "目的が部分的に重なる", "ほぼ同一目的", "同一目的"]
    },
    "mood_fit": {
      "type": "score",
      "instructions": "`a`と`b`の雰囲気・軽さの適合度(「軽く」×「がっつり」等)",
      "criteria": ["雰囲気が相容れない", "重さが大きく異なる", "どちらでも成立する", "重さが近い", "雰囲気が同一"]
    },
    "timing_fit": {
      "type": "score",
      "instructions": "`a`と`b`の時間帯・所要時間の適合度",
      "criteria": ["時間帯が合わない", "開始は合うが所要が合わない", "開始・所要が部分的に重なる", "時間帯・所要が近い", "同一時間帯"]
    },
    "social_fit": {
      "type": "score",
      "instructions": "`a`と`b`の人数・社会的文脈(立場・関係性)の適合度",
      "criteria": ["人数・文脈が成立しない", "人数は合うが社会的文脈がずれる", "人数・文脈が部分的に合う", "人数・文脈が近い", "人数・文脈が同一"]
    },
    "latent_yes": {
      "type": "noul",
      "instructions": "どちらかが明示していないが、そのIntentの記述の範囲内でYESになり得る可能性(「焼肉に行きたい」×「今日は肉系ならどこでもいい」等)"
    }
  }
}
```

modelはエイリアス(jev-latest等)ではなくバージョンIDの**jev-1.13.0固定**とする。エイリアスはリリースのたびに指す先が変わり、較正閾値と日本語評価の前提が崩れるためである(docs.typesafe.ai/modelsも、閾値を調整した場合はバージョンIDをpinし、応答のmodelフィールドで記録するよう推奨する)。モデルの更新時はバージョンを上げて日本語評価(09 v0.5第4節)を再実行したうえで切替える。

### 出力の扱いとスコア計算

**検証(第一候補)。** System Oneモデルの出力は質問で定義したスキーマの外に出ない。よってv0.4の「欠損・値域外・パース不能を1回再試行」の規定は廃止した(§1)。受信側ではanswersの同キー性・noul値域[0,1]・score値域[0,4]を防御的に検証するが、失敗は再試行せず縮退(D-15: skipped保留)へ落とす(実装不整合として扱う)。応答のmodelフィールドはjev_resultへ記録し、実際に答えたバージョンを常に特定できるようにする。

**スコア計算(01第13節どおり・不変)。** 検証を通った場合のみ次の計算を行う。

```text
MutualScore = min(would_a_accept_b, would_b_accept_a)
L = H × MutualScore × C    (HはLayer 1の再検証、Cの初期値は1)
```

noulの値はそのまま[0,1]の確率としてMutualScoreへ入る。scoreの値(0〜4)はレベル数で割って[0,1]へ正規化した値をjev_resultへ保存する。purpose_fit・mood_fit・timing_fit・social_fit・latent_yesの5軸はJev(判定モデル)の内部の判断材料であり、MutualScoreに直接寄与させない(01第13節)。5軸はCalibration(第6節)と精度分析(09)のためにjev_resultへ保存する。scoreにはconfidenceが付くが、noulには付かない(System One仕様)— confidenceは5軸の分析用としてjev_resultへ併せて保存し、would_*には保存しない。フォールバックLLMはconfidenceを返さないためnullを保存する。

**reasonは廃止(確定判断)。** v0.4のwould_*に付くreason(50字以内の自由文)はSystem Oneモデルのインターフェースに存在しない(テキスト生成を行わない)。フォールバックLLM時も一貫してreasonを生成させない — 09のOffline評価・不具合調査に自由文は不要であり、個人由来テキストの保持経路を増やさないためである。jev_result・calibration_recordsにreasonは格納されない(05 v0.5・08 v0.5第2.5節と整合)。v0.3〜v0.4の「reasonの30日短期保持」の規定は前提ごと消滅した — match_candidates・group_candidates自体の30日定期削除は維持される(08 v0.5第2.5節)。

### フォールバックLLMへの切替

切替条件と切替先は次のとおりである。

| 事象 | 扱い |
|---|---|
| 429 Too Many Requests(TypeSafeのレート制限) | 即座にフォールバックLLMへ切替。backoff再試行はしない(§1) |
| 529 Overloaded(TypeSafe側の一時的な過負荷) | 同上 |
| timeout 6秒 | 同上 |
| フォールバックLLMの失敗(timeout・429・5xx・出力検証失敗) | 縮退へ(06 D-15: 候補はskippedで保留・circuit breakerの開放対象) |

フォールバックLLMはAnthropic Claude API・Sonnet 5(T1 v0.2の推奨)とし、System One互換の決定形式 — 同じstate・同じ7質問に対するJSON(キーは同一、値はnoul→[0,1]の確率・score→[0,4]の値)をstructured outputで返させる。reasonを生成させない点・5軸の正規化・MutualScoreの計算は第一候補と同一であり、Layer 5以降はどちらで判定されたかに依存しない。jev_resultにはprovider("typesafe_jev" / "fallback_llm")を記録し、第一候補時は加えて応答のmodelフィールドを記録する。TypeSafeの公式ブログには、生成LLMを同じ決定API互換の出力へ制約する「System One LLM wrapper」の存在が言及されるが、本書は外部アダプタの採用を必須とせず、Gateway実装内で同等の出力形式を規定する(実装手段の選択は開発側に委ねる)。

circuit breaker(06 v0.5 D-15)の切替先は本フォールバックLLMであり、開放中は第一候補を呼ばずフォールバックLLMで判定を続ける。フォールバックLLM自体が継続障害のときのみskipped保留へ落ちる(§1)。

### 送信先と送信記録

Jev系統の送信先は**TypeSafe AI Inc.(api.typesafe.ai・サーバーは米国)**である(01第21節)。LLM Gatewayは送信先・データ種別・時刻を記録する(従来規定の維持)。フォールバックLLM(Anthropic Claude API)への送信も同一の記録経路に載る。送信データはいずれも2件の正規化テキストに限り、raw_textは送らない。TypeSafeのデータ取り扱い(学習に使わない旨・Telemetry条項・ZDRの位置づけ)は08 v0.5 D-14に評価と受容の記録がある。

### 日本語リスク

TypeSafe Jevの公式の言語サポート(docs.typesafe.ai/models「Language support」)は次のとおりである — 英語が主訓練言語であり精度が現在最も高い。CJKを含む他の言語は「扱えるが同等ではない(handled but not equally well)」。非英語のワークロードでJevに頼る前に自分のコンテンツで試すこと、ルーティング時はconfidenceに注目すること、と公式に明記されている。LATCHのstateと質問群は日本語であるため、この留保は本系統の第一リスクである。

- **必須ゲート(M2/G2)**: ゴールドセット(日本語・評価ペア500件以上、09 v0.5第4.1節)によるJev精度・較正評価をG2で必須とする(09 v0.5第4節)。合格しない場合、フォールバックLLMを第一候補へ繰り上げる判断をオーナーに持ち帰る
- **実装側の軽減**: 算術・日付の処理はコード側で行う(正規化テキストは既に構造化済みであり、モデルに計算させない)。大きく無関係なstate・指示の直訳的読み取りはSystem Oneの既知の弱点(docs「Jev 1.13 jaggedness」)であるため、正規化テキストは最小限の構成を保つ(06第5節の入力規定の維持)
- **観測**: jev_resultのprovider・model・confidenceの記録により、第一候補とフォールバックLLMで精度指標(ECE・Brier、09 v0.5第4.2節)を分離して観測する

## 5. 未決事項の決定

### D-17 Intent Parserの性能目標・失敗時の入力手段・構造化精度の合格基準

- 決定内容: 次のとおり確定する
  - **性能目標**: 入力確定から構造化プレビュー表示まで p95 3秒 / p99 8秒。timeoutは10秒、再試行は行わず即フォールバックへ移す
  - **失敗時の入力手段**: (1)入力テキストを保持したままの再試行ボタン、(2)構造化フォームへのフォールバック(必須3フィールド+予算・人数・soft条件の手動入力)。フォームはフォールバック専用であり、通常経路の主UIにはしない(01第9節・03との整合)。v0.3追記(FR-43): 両者の切替は応答コードで行う — LLM障害は503 LLM_UNAVAILABLE(再試行ボタン)、構造化不能は422 VALIDATION_ERROR(フォームフォールバック)。05 v0.5のエラーcode列挙と同一のcodeである
  - **構造化精度の合格基準**: 事前準備の入力セット(30件以上、01第6節の典型表現を含む。期待値表は09)に対するフィールド完全一致率で、category 85% / time.start 90% / location 90%(地域の一致で判定、座標の厳密一致は求めない)/ participants 80% / budget 90%(「くらい」表現の丸め後の値で判定)を合格とする
- 根拠: Parserは登録体験(仮説1)の直前関門で、応答を長く待たせるほどIntent設置そのものが失われる。p95 3秒は待ち時間として許容できる上限目標、timeout 10秒で打ち切って代替手段へ逸すのが体験上最良である。精度基準は、誤構造化がHard Filterの誤判定に直結する必須フィールドほど高く(90%)、言葉の揺れが大きい人数はやや緩く(80%)設定した。02第4節#1・#2の検証と接続する
- 01への影響: 第20節にParserの性能目標とtimeoutを追記、第9節にフォールバック手段を追記、第25節#2の合格基準を明記

## 6. Calibration実装(01第14節)

保存する結果レコードは次の構造とする。

```yaml
calibration_records:
  - id:
    latch_id:
    intent_ids:          # ペア・グループを区別せず集合全体
    prediction:          # 数値のみ。自由文(reason)は生成しない(v0.5で確定、第4節)
      would_a_accept_b / would_b_accept_a / MutualScore / L(提案時のスコア)
      jev_5axis:         # purpose_fit等の内部軸(0〜1へ正規化した値+confidence)
      provider / model:  # 判定経路とバージョン(v0.5新設。typesafe_jev / fallback_llm・応答のmodel)
    proposal_snapshot:   # 提示した条件サマリ(latches.proposalと同形、05 v0.5)
    actual_responses:    # 全員の回答(種別 yes/no/defer と時刻)
    matched:             # 実際に成立したか
    actual_attended:     # 実際に参加したか(D-09、収集方法は09が決定)
    cancelled_after:     # 成立後にキャンセルしたか(同上)
    anonymized_at:       # 匿名化(08 D-13)の実施時刻
    created_at / updated_at:
```

データフローは01第14節のとおり、次の連鎖として実装する。

```text
Human Intent(自然言語)
  → Intent Parser構造化          Prediction上流
  → Jev判定(would_* / L)         Prediction
  → 閾値超過で提案・回答          Proposal → Actual YES / NO
  → 成立後の行動                  Actual Action(参加・キャンセル、D-09)
  → calibration_recordsへ集約
  → C係数の調整・Offline評価(Precision / Recall / Calibration Error /
    Brier Score、01第24節 — 評価の実施は09)
```

レコードは回答確定時に作成し、actual_attended / cancelled_afterは収集(D-09)の時点で更新する。ユーザー削除・退会時の扱いは08 D-13が匿名化保持に決定した(匿名化の強化仕様は08 v0.5 D-13)。ID系を除去した統計値として保持し、正式なスキーマ定義は05のcalibration_recordsによる。

## 7. 本書で解消した未決事項と01への反映

| ID | 本書での扱い | 残る作業と担当文書 |
|---|---|---|
| D-17 | 解消(p95 3秒 / timeout 10秒・再試行なし、フォームフォールバック、精度基準はフィールド別完全一致率。v0.3で応答区別(503/422)を追記) | 期待値表と検証手順の整備は09 |

01更新時に適用する反映事項は次のとおり。

- 第9節: 曖昧表現の丸め規則(第2節)、判定不能NG条件のng_unverifiable検出、失敗時のフォールバック手段、alcohol_involvedの判定規則(08 D-10)を追記
- 第20節: Parserの性能目標(p95 3秒)とtimeout 10秒を追記。Jev timeout 6秒・Embedding timeout 2秒と再試行方針(第1節)を追記
- 第13節: Jevの入出力スキーマと出力検証(再試行1回・失敗時保留)を追記。reasonの30日短期保持(FR-22関連)を追記(なおこの2点はv0.5で見直し済み — 再試行1回の規定廃止・reason廃止。下方の「他文書への反映事項(v0.5)」を参照)
- 第14節: calibration_recordsの構造とデータフローを追記
- 第17節(データモデル): calibration_recordsテーブルの追記(正式定義は05)
- 第25節: #2の構造化精度の合格基準を明記
- 第26節: D-17を解消済みへ更新

**他文書への反映事項(v0.3)**

- 06(次回改版): D-15のcircuit breaker初期値(エラー率・測定窓・半開期間)の確定(FR-10。timeout値は本書第1節で確定済み)
- 08: reasonの保持ポリシー・削除時のjev_result範囲は08 v0.3第2.5節が規定する(本書第4節と整合)

**他文書への反映事項(v0.5)**

- 01 v0.5: 第12節のLayer 4定義(System Oneモデル+フォールバック)、第13節の利用方針と失敗時の扱い(再試行廃止・reason廃止)、第21節の送信先(TypeSafe AIを明記)
- 04 v0.5: D-14へSystem Oneモデルの区分と採用記録、技術スタック表の「LLM(Jev)」と「LLM(Parser)」の行分け、D-16へのフォールバックLLM計上の追記
- 05 v0.5: jev_resultの格納形(System One互換のanswers値・provider/model・reason廃止)
- 06 v0.5: Layer 4の呼び出し先(TypeSafe Jev+フォールバックLLM)、D-15のcircuit breaker切替先(フォールバックLLM)、第5節へD-16のコスト前提注記
- 08 v0.5: §2.5のreason廃止の整合、§3の送信記録へTypeSafe AIを含める、D-14へTypeSafe契約面の評価と受容の記録
- 09 v0.5: 第4節へTypeSafe Jevの評価規定(較正確率の対象・M2/G2での日本語評価必須)
- 10 v0.4: StubJevの応答形式(System One互換のanswers形式)
- 12 v0.2: M2スコープ6の文言(Layer 4=System One+フォールバック)、G2条件への日本語評価追加
