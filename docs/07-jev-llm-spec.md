# LATCH Jev・LLM利用仕様書

- 文書バージョン: v0.4
- ステータス: Draft
- プロダクト名: LATCH
- 作成日: 2026-09-27(v0.3・v0.4更新: 同日)
- 前提文書: 01 要件定義書 v0.4 / 02 スコープ合意書 v0.3 / 03 UX仕様書 v0.4 / 04 システムアーキテクチャ設計書 v0.4 / 05 データモデル・API仕様書 v0.4 / 06 マッチングパイプライン設計書 v0.4
- v0.4の変更点(prototype整合): 下書き保存(05 v0.4)と02 v0.3 D-03再決定(公開設定 hidden_until_match / summary_only)を反映。主要変更は次のとおり。
  - Embeddingの呼び出しタイミングを「active作成・active化(draft→active)・active更新後」へ明確化。下書き保存(status=draft)ではEmbeddingしない(第1節。06 v0.4)
  - Parser入力に300字上限(raw_textのUI制限と同一値)のサーバ側検証を追加した(第2節。03 v0.4第3節)
  - 補完規則のexpires_atを4選択肢の選択式に、visibilityを公開設定(hidden_until_match / summary_only、既定hidden_until_match)へ更新(第2節)
  - 判定可能NGの例から「公開範囲」を除去(公開設定はLayer 1の判定対象ではない、06 v0.4)。Jevの正規化テキストから[hard] visibility行を除去し、social_fitの軸から「公開範囲」を除去した(第4節。表示制御であり意味判定の材料に含めない)
- v0.3の変更点: 総括レビュー(docs/reviews/final-review.md)の指摘を反映。主要変更は次のとおり。
  - FR-10: JevとEmbeddingのtimeout値・再試行回数を確定(第1節)。06 v0.3第1節の層別予算配分(初期LATCH判定p95 10秒)と整合する値を根拠付きで決定。circuit breakerのしきい値初期値の確定は06 D-15側の改版事項として第7節に記録
  - FR-43: ParserのLLM障害(503 LLM_UNAVAILABLE、再試行ボタン)と構造化不能(422 VALIDATION_ERROR、フォームフォールバック)の応答区別を規定(第2節・D-17。05 v0.3のエラーcode列挙と整合)
  - FR-42: MVPではnegative_constraintsは常に空配列である旨を明記(規則5・第2節)
  - FR-22関連: jev_resultのreason(自由文)は30日で定期削除する短期保持とし、calibration_recordsにはreasonを保存しない方針を確定(第4節・第6節。保持ポリシーの全文と削除時の範囲は08 v0.3第2.5節)

本書を読むうえでの取り決めを先に示す。本書はLLM呼び出し3系統(Intent Parser / Embedding / Jev)の仕様を確定し、未決事項D-17を解消する。プロバイダの選定基準は04 D-14に、コスト上限は04 D-16に、呼び出し条件とK上限は06に従い、本書はプロンプト・入出力・精度・性能を担う。送信データはすべて06の決定どおり正規化テキストで、raw_textを外部へ送る経路を持たない(01第21節)。

## 1. LLM呼び出しの全体像

| 系統 | 呼び出しタイミング | 同期・非同期 | timeout | 再試行 | コスト位置づけ |
|---|---|---|---|---|---|
| Intent Parser | POST /v1/intents/parse(03第3節の確認フロー) | 同期(ユーザーが待つ) | 10秒(D-17) | なし(即フォールバックへ) | 1登録1回。件数は最も多いが単価は低い |
| Embedding | Intentのactive作成・active化(draft→active)・active更新後(イベント駆動、06 v0.3第9節の第2段トリガー。**draft状態では呼び出さない** — POSTでの下書き保存・draft中のPATCH更新とも、06 v0.4第9節) | 非同期 | 2秒 | なし(失敗はD-15のバックフィル経路で回収) | 1 Intent 1回+失敗時のバックフィル(06 D-15) |
| Jev | Layer 4(06、K_j=8回) | 非同期 | 6秒 | 出力検証失敗(JSON欠損等)のみ1回。timeoutは失敗として縮退(D-15)へ | 単価が最も高い。04 D-16の日次・月次上限で制御 |

いずれもLLM Gateway(04)経由で呼び出し、送信先と送信データ種別を記録する(01第21節)。Jevの1実行回数は1候補(両方向を1呼び出しで判定)と数える(06第5節)。

**timeout・再試行の根拠(FR-10)。** 値は06 v0.3第1節の層別予算配分(初期LATCH判定p95 10秒: Embedding ≤2秒 / Layer 1〜3 ≤1秒 / Jev ≤5秒 / Layer 5+通知 ≤2秒)と整合させる。

- **Embedding timeout 2秒・再試行なし。** 予算2秒と同値とする。Embedding APIの典型的p95は数百ms〜1秒であり、1 Intent 1回の軽量呼び出しに対して十分な余裕がある。Embeddingは初期LATCH判定の必須前段ではあるが、失敗時にパイプライン全体を止めるべきでない — 再試行は10秒予算を圧迫し、失敗傾向のあるプロバイダへの連打はコストを悪化させる。よってtimeout超過は失敗としてD-15のバックフィル経路で回収する(失敗IntentはLayer 1〜2の対象外、回復後のembedding_completedでパイプラインへ復帰、06 v0.3第9節)
- **Jev timeout 6秒・timeout時の再試行なし。** 予算5秒に1秒の吸収分を加えた値であり、10第4.1節のレイテンシ注入(p50 2秒/p95 5秒)の下で予算を守りつつ、p95を超える遅延(p99側)を打ち切る。timeoutは呼び出し失敗として縮退(D-15: 提案見送り、候補はskippedで保留)へ落とす。再試行でレイテンシとコストを二重に払わないためである。出力検証失敗(JSONの欠損・値域外・パース不能、第4節)のみ1回の再試行を許す(既存規定) — これは出力の問題であり応答速度の問題ではないため、プロバイダの状態とは独立に1回で判定できる。再試行を含む処理は縮退経路であり、p95 10秒の予算判定(06 v0.3第1節)の対象外とする(予算は再試行なしの成功パスで守られる)
- **Parser timeout 10秒・再試行なし** はD-17のとおり確定済み。タイムアウト時は再試行せず構造化フォームへフォールバックする
- Jev層のcircuit breaker(LLM Gatewayのエラー率・レイテンシしきい値で開放、一定時間後に半開)の初期値は、06 D-15側の改版で確定する(第7節に反映事項を記録)

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

- **構造化不能(必須欠落)**: 422 VALIDATION_ERROR(05 v0.3のエラーcode)を返す。クライアントは構造化フォームへのフォールバック(03第3節)を提示し、選択・入力を促す
- **LLM障害(timeout・API障害・レート制限)**: 503 LLM_UNAVAILABLE(05 v0.3のエラーcode)を返す。クライアントは入力テキストを保持した再試行ボタンを提示する(07 D-17)。クライアントは503と422で分岐し、再試行と手動入力の提示を切り替えられる

time.flexibility_minutes / location.flexibilityはMVPでは常にnullとし、Parserは抽出しない(03 D-19のデフォルト=固定扱い)。06 Layer 1のflexibilityによる範囲拡張は、将来バージョンで抽出を有効化した時点で機能する。

**negative_constraintsは常に空(FR-42)。** 規則5により、判定不能NGはng_unverifiableへ入れ、negative_constraintsには入れない。判定可能なNG(ブロック)は独立カラムとLayer 1(06第2節)で判定するため、structured_data.negative_constraints(05 v0.3)はMVPでは常に空配列である(旧来「公開範囲」を判定可能NGの例としていたが、公開設定はLayer 1の判定対象ではないためv0.4で除去した。06 v0.4)。空であることが正常系である旨を明示するものであり、実装者がLayer 1での照合を二重に実装したり、09の期待値表に正例を設けたりしないこと。

**alcohol_involvedの判定と伝播(08 D-10の実装)。** alcohol_involvedは常にtrue/falseを出力し、アプリ層での補完はない。規則はプロンプト規則7のとおり、category.primary=drinkingは常にtrue、「食事のついでに軽く飲む」のようなmeal内の言及を含め、飲酒を示す語(飲む・飲み・酒・呑む・バー・ビール・サワー等)の検出でもtrueとする。フラグは保存時にintents.alcohol_involved(05)へ格納され、保存時にcategory_primary=drinkingであればサーバ側でtrueを確定する(05 v0.3)。作成時の年齢検証(20歳未満のユーザーは飲酒を含むIntentを作成不可、05)とLayer 1の年齢条件(06第2節)がこのフラグを使う。

### D-04連携(判定不能NG条件)

ng_unverifiableが空でない場合、応答のwarningsに `{code: "NG_CONDITION_DOWNGRADED", condition: ...}` を載せる(05第5節)。クライアントは条件リストに注意表示を出し(02 D-04、03 v0.4第3節)、保存時に同条件はstructured_dataのsoft側へ`downgraded_from_ng: true`のフラグ付きで格納される(05 v0.3のstructured_dataスキーマ)。Hard Filterの対象にはならない。

## 3. Embedding仕様

Embedding対象テキストは、構造化データから次の形式で生成する(raw_textは使わない、06第3節)。

```text
{category.primary} / {時間帯の表現(例: 平日夜20-23時)} / {location.name} / {人数の表現} / {soft_constraintsを「・」で連結}
```

モデルは04 D-14の基準を満たす多言語対応の埋め込みモデルとし、次元は768(05のvector(768))。モデル識別子と版をintents.embedding_modelに記録し(01第18節)、モデル変更時はこの記録で再エンベディング対象を特定してバックフィルする。Embeddingの呼び出しはtimeout 2秒・再試行なしであり(第1節)、失敗時の扱い(保存は行い、回復後にバックフィル、完了時にembedding_completedで復帰)は06 D-15・第9節に従う。

## 4. Jev判定仕様

### 送信データの正規化テキスト形式

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

### システムプロンプト(全文)

```text
あなたはLATCHのJev — 意味判定エンジンである。提示される2つのIntent
(条件付きの意思)が相互に成立し得るかを確率で判定する。判定の対象は人その
ものの相性ではなく、この時点・この条件における意図の適合性である。

出力はJSONのみとする。

7つの設問にそれぞれ0.00〜1.00の確率で答える:
- would_a_accept_b: Aがこの提案(Bとの成立)を提示されたときYESと言う確率
- would_b_accept_a: Bがこの提案(Aとの成立)を提示されたときYESと言う確率
- purpose_fit: 目的・カテゴリの適合度(飲みたい×食べたいのズレ等)
- mood_fit: 雰囲気・軽さの適合度(「軽く」×「がっつり」等)
- timing_fit: 時間帯・所要時間の適合度
- social_fit: 人数・社会的文脈(立場・関係性)の適合度(v0.4で「公開範囲」を軸から除去。公開設定は表示制御であり判定材料に含めない)
- latent_yes: どちらかが明示していないが、意図の範囲内でYESになり得る可能性

判定規則:
1. would_* は相手側の条件も考慮する。自分の条件を満たすだけでなく、相手の
   [soft]条件やNGに触れないことを含めて判定する。
2. 相手の[hard]条件と意味的に矛盾する場合(例: 予算感の著しい乖離が食事内容
   を成立させない)、対応する would_* は0.10以下とする。
3. 根拠がなければ0.50。根拠のある確信のみ0.50から離す。
4. would_* にはそれぞれ理由を50字以内で付ける。

出力JSONスキーマ(この形式のみ認める):
{
  "would_a_accept_b": {"score": 0.91, "reason": "…"},
  "would_b_accept_a": {"score": 0.86, "reason": "…"},
  "purpose_fit": 0.80,
  "mood_fit": 0.70,
  "timing_fit": 0.90,
  "social_fit": 0.85,
  "latent_yes": 0.75
}
```

### 出力の検証とスコア計算

応答はJSONスキーマ検証を通す。欠損フィールド・値域外・パース不能は呼び出し失敗とし、1回だけ再試行する(第1節のとおり、timeoutは再試行せず縮退へ)。再試行も失敗なら候補は保留(06 D-15の見送り方針)。検証を通った場合のみ次の計算を行う(01第13節どおり)。

```text
MutualScore = min(would_a_accept_b.score, would_b_accept_a.score)
L = H × MutualScore × C    (HはLayer 1の再検証、Cの初期値は1)
```

purpose_fit・mood_fit・timing_fit・social_fit・latent_yesの5軸はJev内部の判断材料であり、MutualScoreに直接寄与させない(01第13節)。5軸はCalibration(第6節)と精度分析(09)のためにjev_resultへ保存する。

**reasonの保持方針(FR-22関連)。** would_*に付くreason(50字以内の自由文)は、双方のsoft/NG条件をLLMが言い換えた個人由来のテキストであり、raw_textに準ずる機微性を持つ(01第21節の趣旨)。よって次のとおり扱う。

- jev_result(05のmatch_candidates)のreasonは**短期保持(30日)とする**。match_candidates自体が評価確定から30日で定期削除される(08 v0.3第2.5節)ため、reasonが30日を超えて存続することはない。削除・退会時は当該Intentを含むレコードごと即時削除される(08 v0.3第2.5節)
- **calibration_recordsにはreasonを保存しない**(predictionは数値とjev_5axisのみ、第6節・05 v0.3)。Calibration・Offline評価に必要なのは数値と回答データであり、自由文は対象外である
- 30日の根拠: 09のOffline評価・不具合調査の実務サイクル(ベータ期の週次評価を複数週にわたり運ぶ)をカバーする期間であり、Intentの寿命(数日、03 D-19)を大きく超えない。同一バージョン組の再評価はJevスキップ規定(06 v0.3第5節)によりjev_resultを再利用するため、reason削除による再生成コストは発生しない

## 5. 未決事項の決定

### D-17 Intent Parserの性能目標・失敗時の入力手段・構造化精度の合格基準

- 決定内容: 次のとおり確定する
  - **性能目標**: 入力確定から構造化プレビュー表示まで p95 3秒 / p99 8秒。timeoutは10秒、再試行は行わず即フォールバックへ移す
  - **失敗時の入力手段**: (1)入力テキストを保持したままの再試行ボタン、(2)構造化フォームへのフォールバック(必須3フィールド+予算・人数・soft条件の手動入力)。フォームはフォールバック専用であり、通常経路の主UIにはしない(01第9節・03との整合)。v0.3追記(FR-43): 両者の切替は応答コードで行う — LLM障害は503 LLM_UNAVAILABLE(再試行ボタン)、構造化不能は422 VALIDATION_ERROR(フォームフォールバック)。05 v0.3のエラーcode列挙と同一のcodeである
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
    prediction:          # 数値のみ。Jevのreason(自由文)は保存しない(第4節)
      would_a_accept_b / would_b_accept_a / MutualScore / L(提案時のスコア)
      jev_5axis:         # purpose_fit等の内部軸
    proposal_snapshot:   # 提示した条件サマリ(latches.proposalと同形、05 v0.3)
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

レコードは回答確定時に作成し、actual_attended / cancelled_afterは収集(D-09)の時点で更新する。ユーザー削除・退会時の扱いは08 D-13が匿名化保持に決定した(匿名化の強化仕様は08 v0.3 D-13)。ID系を除去した統計値として保持し、正式なスキーマ定義は05のcalibration_recordsによる。

## 7. 本書で解消した未決事項と01への反映

| ID | 本書での扱い | 残る作業と担当文書 |
|---|---|---|
| D-17 | 解消(p95 3秒 / timeout 10秒・再試行なし、フォームフォールバック、精度基準はフィールド別完全一致率。v0.3で応答区別(503/422)を追記) | 期待値表と検証手順の整備は09 |

01更新時に適用する反映事項は次のとおり。

- 第9節: 曖昧表現の丸め規則(第2節)、判定不能NG条件のng_unverifiable検出、失敗時のフォールバック手段、alcohol_involvedの判定規則(08 D-10)を追記
- 第20節: Parserの性能目標(p95 3秒)とtimeout 10秒を追記。Jev timeout 6秒・Embedding timeout 2秒と再試行方針(第1節)を追記
- 第13節: Jevの入出力スキーマと出力検証(再試行1回・失敗時保留)を追記。reasonの30日短期保持(FR-22関連)を追記
- 第14節: calibration_recordsの構造とデータフローを追記
- 第17節(データモデル): calibration_recordsテーブルの追記(正式定義は05)
- 第25節: #2の構造化精度の合格基準を明記
- 第26節: D-17を解消済みへ更新

**他文書への反映事項(v0.3)**

- 06(次回改版): D-15のcircuit breaker初期値(エラー率・測定窓・半開期間)の確定(FR-10。timeout値は本書第1節で確定済み)
- 08: reasonの保持ポリシー・削除時のjev_result範囲は08 v0.3第2.5節が規定する(本書第4節と整合)
