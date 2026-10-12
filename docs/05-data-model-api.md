# LATCH データモデル・API仕様書

- 文書バージョン: v0.8
- ステータス: Draft
- プロダクト名: LATCH
- 作成日: 2026-09-27(v0.3・v0.4更新: 同日。v0.5更新: 2026-09-28。v0.6更新: 2026-10-10。v0.7更新: 2026-10-12。v0.8更新: 2026-10-12)
- 前提文書: 01 要件定義書 v0.7 / 02 スコープ合意書 v0.4 / 03 UX仕様書 v0.5 / 04 システムアーキテクチャ設計書 v0.5
- v0.8の変更点(A/B基盤・MLRベースライン): 09 §5(A/B Test計画の共通規定=仕様正本)を実装に載せるためのデータモデル・適用規則・観測APIを規定した(13最終目的地ロードマップ V1-P4・A-05/A-03)。新表**ab_experiments / ab_assignments**(第2節)・**mlr_baselines**(第2節)・**GET /v1/users/me/ab**(第5節・割付の観測点)を追加。割付の算法(ユーザーIDハッシュ+Active Intent数層化)・割付時点(初回適用時)・ペア適用規則(混在ペアは対照値・FR-25)の実装規定・適用点(D-01閾値・D-08通知上限)・停止ルールと判定不能時の持続規定(09 §5)の表現を第2節に固定した。error codeの追加なし(新APIは読み取り専用で401のみ)
- v0.7の変更点(体感アンケート): 09 §2.3の体験アンケート(仮説1・5の判定材料・13 §3表2のPhase 1開始前要件)をデータモデル・APIとして規定した。新表**survey_responses**(第2節)・**GET / POST /v1/users/me/survey**(第5節)・error code 2件(SURVEY_ALREADY_ANSWERED / SURVEY_NOT_DUE)・配信契機の実装方式(アクセス時判定・通知typeなし)と集計(`python -m latch.obs.survey`)を追加。回答データはユーザー単位とし、**個別Intentとの紐付け列を持たない**(08 §2.4・09 §2.3)
- v0.6の変更点(UI v2基準切替): プロトタイプv2「夜と翡翠」への基準切替に伴うAPI改版(決定記録はdocs/plans/ui-v2/phase0-decisions.md)。主要変更は次のとおり。
  - **Phase 1B実装完了(2026-10-10)**: 上記5APIのbackend実装・契約試験・エンドポイント表・遷移表への反映まで完了。実装報告はdocs/plans/ui-v2/phase1b-report.md
  - expiry-optionsの選択肢を4値から**3値(今日 22:00 / 今日 23:59 / 明日 5:00)**へ変更(D-01。completion.pyの選択肢生成を更新。実装はUI v2 Phase 1B)
  - structured_dataへ**participants_includes_owner**(あなたを含むか・既定true)を追加(D-04。「含まない」の場合はクライアントがparticipants実効値を+1して送信する)
  - **回答取り消しAPI**(POST /v1/latches/{id}/response/undo)を追加(D-06。回答済み→未回答へ戻す。04のjoined/declined状態の「回答を取り消す」に対応)
  - responsesへ**reason_codes**(見送り理由・time/place/size/mood)を追加(D-07。defer時のみ受理・任意複数。相手への開示なし)
  - **参加取り消しAPI**(POST /v1/latches/{id}/leave)を追加(D-08。自分の参加のみ取り消す・チャットへシステム行・自分は書込不可・LATCHはmatched継続)
  - visibility・notification_levelのUI選択導線廃止に伴い、両フィールドは「UIからは常に既定値で送信される」旨を明記(値域・既定値は不変。D-02)
- v0.5の変更点(TypeSafe Jev採用): 07 v0.5(2026-09-28オーナー裁定・C案)に追従し、match_candidates.jev_resultとcalibration_records.predictionの格納形をSystem Oneモデル「TypeSafe Jev」の応答に合わせて更新した。全変更箇所と出典はdocs/reviews/jev-systemone-revision.mdに記録。主要変更は次のとおり。
  - jev_result: reason(自由文)を廃止 — System Oneモデルはテキストを生成せず、フォールバックLLM時も一貫して無し(07 v0.5第4節)。格納するのはanswers値(noul確率・score正規化値)とconfidence、provider、model(応答のバージョンID)
  - prediction: 数値のみの前提を明記し、provider・modelキーを追加。既存のキー(would_*・jev_5axis等)と型は不変
- v0.4の変更点(prototype整合): フロントエンドの実装基準を`prototype/`に合わせる02 v0.3(D-03再決定)・プロダクトオーナー決定を反映。主要変更は次のとおり。
  - FR-52: intents.visibilityをhidden_until_match(条件一致までは非公開)/ summary_only(候補にだけ概要を表示)の2値へ再定義(02 v0.3 D-03再決定)。friendshipsのMVP実装は取りやめ(将来版として残置)。Layer 1の公開範囲チェックは廃止し、開示範囲の制御はlatches.proposalの生成分岐(第2節)へ移す(06 v0.4)
  - FR-53: 下書き保存を追加。POST /v1/intentsに`status: "draft"`を許可し(raw_text必須のみで保存、Embedding・MatchEventなし)、PATCH /v1/intents/{id}でのdraft→active遷移時に通常の検証とEvent発行を行う。GET /v1/intents(自Intent一覧・statusフィルタ)を追加(02 v0.3・03 v0.4の「下書き保存」)
  - FR-54: Intent遷移表にdraftを追加(第6節)。draft→activeの検証経路と、draftのexpires_at経過時の扱いを規定
  - FR-55: proposalの生成条件にvisibility分岐を追記 — hidden_until_matchの候補には条件サマリを格納せず、headcount・match_levelのみとする(第2節「proposalの構造」)
  - FR-56: intents.notification_level(お知らせ設定: proposals_only / nearby_also / muted、既定proposals_only)を新設(03 v0.4第3節)。nearby_alsoは閾値未満候補の存在通知のみを送り、D-08の日次上限(日6件/ユーザー)に含める(同時進行上限には適用しない。06 v0.4第6節)。UI文言は「お知らせ」(預け方パネル)
- v0.3の変更点: 総括レビュー(docs/reviews/final-review.md)の指摘を反映。主要変更は次のとおり。
  - FR-02(Critical): 認証・ユーザー系APIを追加(04 D-21の決定を契約化)。`POST /v1/auth/token`・`POST /v1/auth/refresh`・`POST /v1/auth/logout`・`GET /v1/users/me`・`POST /v1/users`(birth_date・18歳検証を含む初回登録)
  - FR-04: structured_dataの正式JSONスキーマ(D-04降格フラグ`downgraded_from_ng`を含む)を定義。POST/PATCHのlocationの受け方とジオコーディング失敗応答(422 GEOCODING_FAILED)を規定。parse応答例を07のParser出力スキーマと整合(visibility・座標を除去)
  - FR-05: match_candidatesに`prev_latch_score` / `prev_evaluated_at`、group_candidatesに`prev_aggregate_score`を追加(再提案のスコア比較元の保持。比較手順は06第10節)
  - FR-06: 保留キューを`latches.status=candidate`で表現。candidate→expired遷移とresponse_deadlineの暫定値の説明を追記
  - FR-08: LATCH期限切れバッチ(expiry_sweeper、06第6節)への参照を遷移表に追記
  - FR-09: match_eventsにUNIQUE(event_type, source_intent_id, payload内version)を明記。削除済みIntent参照Eventの破棄とpayload不正の隔離の区別
  - FR-11: `POST /v1/sessions`・`POST /v1/latches/{id}/attendance`・ブロック一覧/解除・友人拒否・申請取り消しを追加
  - FR-12: LATCH状態遷移イベントの記録構造としてlatch_status_eventsを新設(09第2.1節のMutual Latch Rate集計の根拠、06第10節の保留キュー再評価トリガーの観測点)
  - FR-13: latches.proposalの正式フィールド一覧と各フィールドの生成元を定義
  - FR-14: POST/PATCHリクエストに`expires_at`の受け渡しを明記
  - FR-17: group_candidatesに集合内user_id一意制約を追加
  - FR-33: 自己参照「第7節」を「第6節」へ修正 / FR-34: 「01 SP-4」を「01レビューSP-4」へ修正
  - FR-39: エラーcode列挙表を追加(併せてレート制限のステータスコードを08第5.4節と整合: Active上限のみ422、その他は429)
  - FR-40: 一覧系APIのcursorページネーション共通規定
  - FR-44: 作成・更新の時刻検証(time_start過去不可・対象領域上限7日)を422条件に追加
  - fixerレビュー対応: match_levelにlow区分を追加、POST /v1/usersのJWT・claim前提を明記、「03 F4」を「03 D-08」へ置換(FR-30)、alcohol_involvedのサーバ側確定規則を追記、遷移表へLATCH解散時の参加Intent復帰(matched→active/expired)とmatched→cancelledを追記、resume行へversion+1の再評価Event発行を追記(FR-18)

本書を読むうえでの取り決めを先に示す。本書は01第17節の5 EntityをPostgreSQL(04の選定: +PostGIS / +pgvector)上のスキーマとして確定し、API契約を固定する。02〜04の決定(visibility 2値、回答種別の区別、友人関係、認証)をすべて反映する。カラム型はPostgreSQLの型名で書く。時刻はtimestamptz(JST運用)、金額は整数(円)とする。

## 1. ER概要

```text
users ──1:N── intents ──1:N── match_events
  │              │
  │ 2:1(ペア)    │ 3..4:1(集合)
  │              ├── match_candidates
  │              └── group_candidates
  │
  ├── M:N── users   (friendships: 相互承認 / blocks: 単方向)
  │
  └── latches ──N:M── intents (latch.intent_ids[])
        │
        ├── 1:N── responses (latches.responses: jsonb配列)
        ├── 1:N── latch_status_events(状態遷移履歴)
        ├── 1:N── messages(チャット)
        ├── 1:N── calibration_records(回答確定時に生成、ID系は匿名化でNULL)
        └── nullable FK ── group_candidates

users ──1:N── notifications / reports

users ──1:N── survey_responses(体感アンケート回答・Intentと紐付かない・v0.7)

users ──1:N── ab_assignments ──N:1── ab_experiments(A/B割付・v0.8)
mlr_baselines(MLR週次中央値ベースラインの記録・v0.8・usersと紐付かない)
```

01第17節の5 Entity(User, Intent, MatchCandidate, Latch, MatchEvent)に、D-06中間表現のgroup_candidates、そして第25節Safetyと03 D-18の実装に必要なblocks / reports / notifications / messagesを加える。friendshipsは02 D-03由来だが、**v0.4でMVP実装から外れた(将来版としてEntity定義のみ残置。MVPの参照経路は存在しない)**(02 v0.3 D-03再決定)。

## 2. スキーマ定義

### users

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| id | uuid | PK | |
| display_name | text | NOT NULL | |
| profile | jsonb | NOT NULL DEFAULT '{}' | 最小限のプロフィール(表示名・ひとこと) |
| birth_date | date | NOT NULL | 生年月日(自己申告、08 D-10)。IdPからは取得しない。登録時の18歳以上検証と、alcohol_involved=trueのIntentの作成可否(20歳)の判定に使う |
| auth_provider | text | NOT NULL, CHECK IN ('google','apple') | 04 D-21 |
| auth_subject | text | NOT NULL | IdPのsub識別子。UNIQUE(auth_provider, auth_subject) |
| location_preferences / visibility_preferences | jsonb | | 01第17節の項目をJSONで保持 |
| trust_score | numeric | NULL | MVPでは使用しない予約(01第17節) |
| created_at / updated_at | timestamptz | NOT NULL | |

### intents

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| id | uuid | PK | |
| user_id | uuid | FK→users, NOT NULL | |
| category_primary | text | NOT NULL | 03 D-19必須。secondaryはstructured_data内 |
| alcohol_involved | boolean | NOT NULL | 飲酒の関与(08 D-10)。07 Parserが判定し、category_primary=drinkingは常にtrue。作成時の年齢検証(20歳未満は作成不可)とLayer 1の年齢条件(06)が参照する |
| raw_text | text | NOT NULL | 入力原文。非公開・ログ出力禁止(01第21節) |
| structured_data | jsonb | NOT NULL | 下記「structured_dataのJSONスキーマ」のとおり |
| geo_center | geography(Point,4326) | NULL | PostGIS。**draftではNULL可(v0.4: 下書きはraw_textのみ必須、第5節)。active化(PATCH)時にlocation.nameのジオコーディング(04第3節)で確定する**。active行ではNULL不可(active化時の検証で強制する。DB CHECKではなくアプリ層+active化経路で保証する) |
| geo_radius_m | integer | NULL | draftではNULL可(v0.4)。active化時に確定(未指定は1,000m既定半径、03 v0.4 D-19) |
| budget_max | integer | NULL | NULL=制約なし(03 D-19)。minは原則使わない |
| participants_min / participants_max | smallint | NOT NULL, DEFAULT 2 | 提案者自身を含む人数(01第15節)。**上限は5(v0.6実装・D-04の「含まない」+1換算であなた+2〜4人=合計3〜5人を送信し得るため。LATCHサイズ上限4はlatches.intent_idsのCHECKが担う)** |
| visibility | text | NOT NULL, CHECK IN ('hidden_until_match','summary_only'), DEFAULT 'hidden_until_match' | 公開設定(02 v0.3 D-03再決定)。hidden_until_match=条件一致までは非公開(提案画面に条件サマリを表示せず、成立時に解放)、summary_only=候補にだけ概要を表示(提案画面に条件サマリを表示)。Layer 1の判定対象ではない(開示範囲はproposal生成分岐で制御)。**UIからの変更導線はv0.6で廃止(D-02)。クライアントは常に既定値hidden_until_matchで送信する(値域・A/B試験時のAPI直叩きによる変更は可)** |
| notification_level | text | NOT NULL, CHECK IN ('proposals_only','nearby_also','muted'), DEFAULT 'proposals_only' | お知らせ設定(03 v0.4第3節、v0.4新設)。proposals_only=閾値超過の提案のみ通知、nearby_also=閾値未満候補の発生通知も送る(条件サマリ・相手情報は含まない。D-08の日次上限に含めるが同時進行上限には適用しない、06 v0.4第6節)、muted=提案通知を送らない(遷移自体はproposedまで行う、06 v0.4第6節)。UI名称は「お知らせ」(預け方パネル) |
| status | text | NOT NULL | draft/active/paused/matched/expired/cancelled(第6節) |
| version | integer | NOT NULL, DEFAULT 1 | 更新ごとに+1。MatchEvent payloadに含む(01第16節) |
| time_start | timestamptz | NULL | **draftではNULL可(v0.4: 下書きは中途データでも保存する、第5節)。active行では必須(active化時の検証で強制、03 v0.4 D-19)**。activeの作成・更新時には過去時刻・上限7日超を422とする(第5節) |
| time_end | timestamptz | NULL | 未指定はtime_start+3時間(03)。draftではNULL可(v0.4) |
| expires_at | timestamptz | NULL | **draftではNULL可(v0.4)。active化時に、未指定ならtime_start+3時間に最も近い有効期限選択肢(03 v0.4第3節の4値)と同時刻を補完する**。activeの作成・更新時には過去・上限7日超を422とする(第5節) |
| embedding | vector(768) | NULL | pgvector。拡張次元はEmbeddingモデル確定時に固定。NULL=Embedding未完了または失敗(06第3節・第9節) |
| embedding_model | text | NULL | モデル識別子+版(01第18節)。再エンベディング特定用 |
| created_at / updated_at | timestamptz | NOT NULL | |

Hard Constraintの実体(geo / budget / participants / time)は独立カラムとし、soft / negative_constraintsはstructured_data内のJSONに置く。判定データを欠くnegative条件はSoft Constraint側へ格納する(02 D-04)。visibilityは独立カラムに保持するがLayer 1の判定対象ではなく(v0.4。02 v0.3 D-03再決定)、開示範囲の制御はlatches.proposalの生成分岐(第2節「proposalの構造」)で行う。

### structured_dataのJSONスキーマ(正式構造)

intents.structured_dataは、Hard Constraintとして独立カラム化した項目を含まない(二重管理を避ける)。structured_dataが保持するのは次のキーのみとする。

```json
{
  "category_secondary": "焼肉",
  "participants_includes_owner": true,
  "soft_constraints": [
    {"text": "軽く飲みたい", "downgraded_from_ng": false},
    {"text": "会社関係の人は避けたい", "downgraded_from_ng": true}
  ],
  "negative_constraints": [],
  "time_flexibility_minutes": null,
  "location_flexibility": null
}
```

| キー | 型 | 説明 |
|---|---|---|
| category_secondary | string \| null | 07 Parser出力のcategory.secondary。03第4節の「焼肉」等の表示に使う(proposalの生成元、第5節)。primaryはintents.category_primary |
| participants_includes_owner | boolean | 「あなたを含むか」の回答(v0.6新設・D-04)。既定true。02の人数確認segで回答し、「含まない」の場合はクライアントがparticipantsの実効値を+1して(あなた+2〜4人=合計3〜5人)送信する。03の「条件を直す」で02へ戻った際のseg復元に使う。マッチング判定は+1後のparticipantsで行う(送信値が正) |
| soft_constraints | オブジェクト配列 | 各要素は`text`(条件の文言)と`downgraded_from_ng`(boolean)を持つ。02 D-04の降格条件の格納時に、元の`ng_unverifiable`の各要素を`downgraded_from_ng: true`としてここへ格納する。`ng_unverifiable`そのものは保存しない(降格事実はフラグで保持)。このフラグは06第4節のLayer 3語彙重なり計算の除外判定と、06第2節のJev入力タグ化([soft]+判定不能表示)が参照する |
| negative_constraints | string配列 | 判定可能なnegative条件(ブロック)は独立カラム+Layer 1で判定するため、07規則5の帰結としてMVPでは常に空配列である(v0.4で公開設定はLayer 1の判定対象から外れたため「公開範囲」を例から除去。06 v0.4)。空であることが正常系である旨を明示する(06 Layer 1・09の期待値表もこれに従う) |
| time_flexibility_minutes / location_flexibility | null | MVPでは常にnull(03 D-19の固定扱い)。将来の抽出有効化に備えた予約 |

### match_candidates(1対1ペア)

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| id | uuid | PK | |
| intent_a_id / intent_b_id | uuid | FK→intents, NOT NULL | 正規化: intent_a_id < intent_b_id |
| intent_a_version / intent_b_version | integer | NOT NULL | 評価時点の各Intent version。UNIQUE(intent_a_id, intent_b_id, intent_a_version, intent_b_version) — 評価はIntentバージョン組ごとに1レコードとし、Intent更新後の再評価・D-07の再提案は新しいバージョン組で新レコードを作る。同一バージョン内の再評価(時間Bucket等)は既存レコードを更新する |
| prev_latch_score | numeric | NULL | 同一バージョン組内の直前評価のlatch_score。更新トランザクション内で上書き前に退避する(次行)。03 D-07の「スコア変化」判定の比較元である(比較手順は06第10節) |
| prev_evaluated_at | timestamptz | NULL | 直前評価の時刻。更新トランザクション内でupdated_atとともに退避する |
| retrieval_score / cheap_judge_score | numeric | NULL | 各層の結果 |
| jev_result | jsonb | NULL | System One互換の判定結果(v0.5・07 v0.5第4節)。キーは would_a_accept_b / would_b_accept_a(noul確率、0〜1)/ jev_5axis(purpose_fit等の5軸。scoreの0〜4値をレベル数で割った0〜1正規化値とconfidence)/ provider("typesafe_jev" / "fallback_llm")/ model(第一候補時の応答modelバージョンID。フォールバック時はnull)。自由文は格納しない(v0.5でreasonを廃止。実物のJevはテキストを生成せず、フォールバックLLM時も一貫して無し)。同一バージョン組の再評価では再実行せず保持する(06第9節のJevスキップ) |
| latch_score | numeric | NULL | L = H × MutualScore × C |
| status | text | NOT NULL | pending / evaluated / skipped(Jev未判定、04 D-16) / closed |
| created_at / updated_at | timestamptz | NOT NULL | |

### group_candidates(D-06中間表現、詳細は第4節)

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| id | uuid | PK | |
| intent_ids | uuid[] | NOT NULL, CHECK(3≦配列長≦4) | 集合を構成するIntent。生成時トランザクションで、配列が参照する全Intentのuser_idが互いに異なることを検査する(同一ユーザーのIntentを集合に含めない。06第7節と対になる生成側の制約)。DB上は配列参照のためCHECKで直接書けないため、INSERT前検査+アプリ層で強制する |
| member_scores | jsonb | NOT NULL DEFAULT '{}' | メンバー間・集合評価の結果参照。再評価(バージョン組不変)で上書きする |
| aggregate_score | numeric | NULL | 集約スコア(集約規則は06) |
| prev_aggregate_score | numeric | NULL | 同一バージョン組内の直前評価のaggregate_score。更新トランザクション内で退避する。保留キューの「スコア変化」判定に使う(06第10節) |
| status | text | NOT NULL | candidate / proposed / closed |
| created_at / updated_at | timestamptz | NOT NULL | |

### latches

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| id | uuid | PK | |
| intent_ids | uuid[] | NOT NULL, CHECK(2≦配列長≦4) | |
| group_candidate_id | uuid | NULL, FK→group_candidates | グループ成立の場合のみ |
| proposal | jsonb | NOT NULL | 下記「proposalの構造」のとおり。表示用サマリのみ(08第2.3節) |
| score | numeric | NOT NULL | 提示時のスコア |
| responses | jsonb | NOT NULL DEFAULT '[]' | 下記の構造。回答種別を区別保持(03 D-07) |
| status | text | NOT NULL | candidate / proposed / partial_accept / matched / rejected / expired / cancelled / completed(第6節)。candidateは保留キューの行である(06第10節) |
| left_user_ids | uuid[] | NOT NULL, DEFAULT '{}' | 参加取り消し済みのuser_id(v0.6実装・D-08)。matchedのまま自分のみ脱退した参加者の集合。以後のチャット送信は409 CHAT_READONLY・再leaveは409 ALREADY_LEFT。解散判定・headcount再判定には使わない |
| response_deadline | timestamptz | NOT NULL | 03 D-05の導出規則で設定。candidate状態では保留登録時点の暫定値であり、提示(proposed遷移・通知送信)時に必ず再計算して上書きする(06第10節)。回答判定・表示はこの確定値のみを使う |
| expires_at | timestamptz | NOT NULL | 参加Intentのexpires_atの最小値 |
| created_at / completed_at | timestamptz | | |

```json
"responses": [
  {"user_id": "...", "intent_id": "...",
   "response": "yes | no | defer", "answered_at": "2026-09-26T19:40:00+09:00",
   "reason_codes": ["time", "place"]}
]
```

`defer`が「今回は見送る」である。状態遷移はnoと同じrejected相当だが、回答種別はこのフィールドで保持し、再提案制御(03 D-07)の判定に使う。`reason_codes`は見送り理由(v0.6新設・D-07・任意・複数・値はtime/place/size/mood)で、defer回答時のみ受理する。提案精度改善のための匿名データであり、相手への開示・通知には使わない(03 v0.5第7節)。回答取り消し(D-06)で見送りを取り消した場合はreason_codesも破棄する。calibration_records.actual_responsesにも同形で保持する(09の判定資料)。

**proposalの構造(正式フィールド一覧と生成元)。** latches.proposalは通知文・提案画面の表示要素(03第4節・第5節)のデータソースである。文言はクライアント/Layer 5のテンプレートがこの構造から組み立てる(A/B対象の「通知文」(09第5節)の変更に構造が引かれないため)。格納禁止はraw_text・soft/NG条件の文言・座標(08第2.3節・08 D-11)。

| フィールド | 型 | 生成元 |
|---|---|---|
| time_summary | string | 集合の対象時刻(参加Intentのtime_start、03第4節「今夜 20:00〜」)。生成は06 Layer 5 |
| area_name | string | geo_centerを約1kmグリッドへ丸めた代表点の地物名(04第3節・08 D-11の逆転ジオコーディング)。座標は格納しない |
| headcount | integer | 集合サイズ|S|(1対1は2) |
| category_primary | string | intents.category_primary(集合内で共通、06 Layer 1の完全一致条件) |
| category_secondary | string \| null | 参加Intentのstructured_data.category_secondary(第2節)。03第4節「焼肉」の表示は非nullの場合のみ。集合内で複数のsecondaryがある場合、集合の種(Intent)の値を採る |
| budget | object \| null | ペア予算(参加Intentのbudget_maxの最小値、06第2節)。`{"max": 5000}`。全体がNULLならnull(「制約なし」の表示は03の規定に従う) |
| match_level | string | 一致度の区切り表示(03第5節)。`high`(0.90以上)/ `medium`(0.80以上0.90未満)/ `low`(提案閾値以上0.80未満)。下端は運用中の提案閾値に連動する(09 D-01のA/B処置群0.50で提案された場合もlowに区分され、未定義区間を生じさせない・v0.6)。内部スコア生値は格納しない |

calibration_records.proposal_snapshotはこのproposalと同形とする(09 D-09の収集対象と同一の内容を固定するため)。

**visibilityによる生成分岐(v0.4、02 v0.3 D-03再決定)。** proposalの内容は参加Intentのvisibilityで分岐する。既定のsummary_onlyでは上表の全フィールドを生成する。hidden_until_matchのIntentを含む候補では、相手へ開示する情報を成立まで最小化するため、**headcountとmatch_levelのみ**を格納し、time_summary・area_name・category_primary・category_secondary・budgetは格納しない。提案画面は「条件が合う候補があります」の通知文・一致度・回答期限(・グループなら必要人数)のみで構成され(03 v0.4第5節)、表示名・プロフィールはsummary_onlyでもhidden_until_matchでも成立まで表示しない(08 v0.4第2.2節)。1対1の候補で双方のvisibilityが異なる場合は、より厳しい方(いずれかがhidden_until_matchならhidden_until_match扱い)を優先する。proposal_snapshot(09 D-09)もこの分岐の結果をそのまま保持する。提案画面の情報量の差は09のA/B対象(表示情報量)の検証対象である。

### match_events / latch_status_events / friendships / blocks / reports / notifications / messages

| テーブル | 主要カラム | 備考 |
|---|---|---|
| match_events | id / event_type(5種+派生1種) / source_intent_id / payload(発火時のIntent versionを含む, 01第16節) / status(pending/processed/quarantined) / created_at / processed_at | event_typeは01第5節の5種に、Embedding完了の派生イベント`embedding_completed`(06 v0.3の第2段トリガー)を加えた6値を取る。UNIQUE(event_type, source_intent_id, payload内version) — 冪等キー(06第9節)のDBレベル保証。Pub/Subのat-least-once配信に対して並行Workerの重複処理をDBで排除する。隔離(quarantined)は失敗理由をpayloadに保持。削除済みIntentへの参照Eventは正当な遅延Eventとしてstatus=processedで破棄し(理由をpayloadに記録)、payload不正(構造違反・version欠落等)のみquarantinedへ隔離する(06第9節) |
| latch_status_events | id / latch_id / from_status / to_status / user_id / created_at | LATCH状態遷移の履歴。遷移を書くトランザクション(回答API・競合クローズ・expiry_sweeper・Layer 5のproposed遷移)と同時に挿入する。from_statusはNULL可(candidate作成時)。user_idは遷移の引き金となったユーザーで、システム起因(sweeper・競合クローズ等)はNULL。09第2.1節のMutual Latch Rate集計(proposed遷移=分母、matched遷移=分子)の供給源であり、06第10節の保留キュー再評価トリガー(クローズ検知)の観測点でもある |
| friendships | id / requester_id / addressee_id / status(pending/accepted) / created_at / accepted_at | **MVPでは使用しない(v0.4)**。02 v0.3 D-03再決定により、友人関係(friends_only)の実装は将来版へ移された。Entity定義は将来版への復帰用として残置する。MVPの参照経路(Layer 1・API・Index)は存在しない |
| blocks | id / blocker_id / blocked_id / created_at | 単方向。マッチングは双方向 (A,B)(B,A) を確認。解除は当該行の削除(第5節) |
| reports | id / reporter_id / reportee_id / latch_id / reason / status / created_at | 第25節Safetyの通報 |
| notifications | id / user_id / type / payload / read_at / created_at | 03 D-18のアプリ内通知 |
| messages | id / latch_id / sender_id / body / created_at | matched以降のみ書き込み可。**sender_idはNULL可(v0.6実装・D-08の参加取り消しシステム行「{表示名}が参加を取り消しました」のみNULL=送信者なし)**。ブロック適用中のLATCH(08 D-23)は読み取り専用(書き込みは409 CHAT_READONLY) |

### calibration_records(07第6節の構造の形式化)

07第6節の構造をスキーマへ落とす。レコードは回答確定時に作成し、actual_attended / cancelled_afterは収集時(D-09)に更新する。削除・退会時の匿名化(08 D-13)は、ID系(latch_id・intent_ids・actual_responses内のuser_id)の除去としてこのテーブルへ適用する。

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| id | uuid | PK | |
| latch_id | uuid | NULL, FK→latches | 対象となった提案。匿名化(D-13)でNULLへ |
| intent_ids | uuid[] | NULL, CHECK(2≦配列長≦4) | ペア・グループを区別せず集合全体を保持(07のintent_a/bとグループのintent_idsを統一)。匿名化でNULLへ |
| prediction | jsonb | NOT NULL | 数値のみ(v0.5で明記。自由文reasonは生成しない、07 v0.5第4節)。would_a_accept_b / would_b_accept_a / MutualScore / L(提案時のスコア)とjev_5axis(purpose_fit等の5軸の0〜1正規化値とconfidence、07 v0.5第4節)。v0.5でprovider("typesafe_jev" / "fallback_llm")・model(応答のバージョンID)をキーとして追加(判定経路の分離観測、09 v0.5第4節) |
| proposal_snapshot | jsonb | NOT NULL | 提示した条件サマリ。latches.proposalと同形(第2節)。soft/NG条件の文言は含まない(08第2.3節)。匿名化後も保持 |
| actual_responses | jsonb | NOT NULL | 全員の回答(latches.responsesと同形)。匿名化でuser_idを除去し、回答種別と時刻は残す |
| matched | boolean | NOT NULL | 実際に成立したか |
| actual_attended | boolean | NULL | 実際に参加したか(D-09)。更新経路は第5節のattendance API |
| cancelled_after | boolean | NULL | 成立後にキャンセルしたか(同上) |
| anonymized_at | timestamptz | NULL | 匿名化の実施時刻。NULL=未処理。NOT NULLならlatch_id・intent_idsはNULL(CHECKで強制) |
| created_at / updated_at | timestamptz | NOT NULL | updated_atはactual_attended / cancelled_afterの収集時(D-09)更新で進む |

### survey_responses(体感アンケートの回答・v0.7新設)

09 §2.3の体験アンケート(仮説1「待つ体験を便利と感じる」・仮説5「検索しない様式が継続につながる」の判定材料)の回答を保持する。**回答はユーザー単位・ラウンド単位であり、個別Intentとの紐付け列を持たない**(08 §2.4「アンケート等の意図的な集計イベントを追加する場合も、個別Intentの文言を含めない」・09 §2.3「回答データは個別Intentの文言と紐付けない」)。

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| id | uuid | PK | |
| user_id | uuid | NOT NULL, FK→users(退会cascade) | 回答者 |
| round | text | NOT NULL, CHECK(round IN ('onboarding', 'week4')) | 配信ラウンド。onboarding=オンボーディング直後(初回Intent active化後)・week4=4週後。**UNIQUE(user_id, round)が「単発の配信」(09 §2.3)をDBレベルで強制する**(回答は初回のみ受理・訂正不可) |
| answers | jsonb | NOT NULL | 設問種→5段階値の整数。`{"convenience": <1〜5>}`(onboarding)、`{"convenience": <1〜5>, "continuation": <1〜5>}`(week4)。convenience=便利さ(仮説1)・continuation=継続意向(仮説5)。値域外は422 VALIDATION_ERROR。設問種の追加は本書の改版を経る |
| free_comment | text | NULL | week4のみの任意自由記述(最大2000字)。onboardingでは常にNULL |
| created_at | timestamptz | NOT NULL | 回答時刻(Clock由来)。week4のdue判定の起点(初回回答から4週=28日) |

### ab_experiments / ab_assignments(A/B割付・v0.8新設)

09 §5(A/B Test計画の共通規定)の8対象のうち、サーバ側で値を解決してパイプラインへ適用する対象のための保持構造。**割付・適用状況はユーザーからは観測API(GET /v1/users/me/ab・第5節)を通してしか見えず、UIを持たない**(表示・通知の体感を変えない。frontendは本改版では不変)。

#### ab_experiments(実験の登録)

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| experiment_key | text | PK | 実験キー=適用点名。本実装が解決するサーバ側適用点は `latch_threshold`(D-01提案閾値・float)・`notification_daily_limit`(D-08日次上限・int)・`notification_concurrent_limit`(D-08同時進行上限・int)の3つ。残る5対象(Candidate数・Jev判定軸・表示情報量・通知文・回答期限・グループ人数)はそれぞれの実装時にキーと値型を本書の改版を経て追加する |
| status | text | NOT NULL, CHECK IN ('running','stopped') | runningの間のみ処置値が適用され得る。**stoppedは全群へ対照値を適用する**(下記「停止と持続規定」) |
| control_value | text | NOT NULL | 対照値(textで保持・型はexperiment_keyの適用点が定義)。**原則として現行運用値と同値**(latch_threshold=0.60・notification_daily_limit=6・notification_concurrent_limit=3) |
| treatment_value | text | NOT NULL | 処置値(同上) |
| started_at | timestamptz | NOT NULL | 実験開始時刻(Clock由来)。この時刻以降の評価から適用される |
| stopped_at | timestamptz | NULL | 停止時刻。status=stoppedのとき必須(アプリ層で保証) |

行の作成・変更はCLI(`python -m latch.obs.ab`のstart/stop/list)のみが経路。**公開APIを持たない**(開始判断は運用=人間領域。D-02の「ベースライン計測期間中の本番変更凍結」との整合も運用規律として守る)。

#### ab_assignments(ユーザー割付)

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| experiment_key | text | NOT NULL, FK→ab_experiments(cascade), PK(experiment_key, user_id)の一部 | 対象実験 |
| user_id | uuid | NOT NULL, FK→users(退会cascade), PKの一部 | 割付対象ユーザー。**PKが「1ユーザー1実験1行=割付の安定性」をDBレベルで強制する**(一度割り当てられたvariantは再計算しない) |
| variant | text | NOT NULL, CHECK IN ('control','treatment') | 群 |
| stratum | text | NOT NULL | 割付時点のActive Intent数の層('0' / '1-2' / '3+')。層化の記録(分析用途。適用値の解決には使わない) |
| assigned_at | timestamptz | NOT NULL | 割付時刻(Clock由来) |

#### 割付と適用の実装規定(設計判断の固定・09 §5の実装解釈)

09 §5は「1テスト1変数・2群」「割付はユーザーIDのハッシュで層化(Active Intent数の層)」「ペア適用規則(FR-25)」を仕様として固定しており、以下はその実装方式の確定である。

1. **割付表かusers列か → 割付表(ab_assignments)。** 8対象が時期を異にして走るため実験軸が必要であり、users行へ列を足す方式は対象ごとの横膨張と未実施実験のNULL管理を生む。分析(群別KPI導出)は割付表とkpi_daily/latch_status_eventsの突合で完結する
2. **割付時点 → 初回適用時(レイジー)。** ユーザー登録時に割ると全員がActive Intent 0の層に落ちて層化が空転するため、**最初に提案評価へ現れた時点**(実験running中にペア/集合の参加者として評価されたとき)で割付を確定する。層は割付時点のActive Intent数で計算して記録する。割付は適用経路からのみ発生し、観測API(GET /v1/users/me/ab)は割付を作らない
3. **割付算法 → SHA-256ハッシュの偶奇。** `variant = sha256("{experiment_key}:{stratum}:{user_id}")` の先頭バイトの偶奇(奇数=treatment)。決定的(並行する初回適用が同じ値を計算)+層内でおおむね50/50。INSERTはON CONFLICT DO NOTHINGで冪等とし、挿入後に読み直した行を真実とする(層の読み時差で計算値が食い違う競合を残さない)
4. **ペア適用規則(FR-25)→ 適用値の解決はペア/集合単位。** 評価の参加者(1対1は2者・グループは3〜4者)のvariantが全員同一のときのみその群の値を適用し、**混在ペア・混在集合は対照値**を適用する(時間交互切替しない)。個人単位の適用は行わない
5. **適用点(本実装の3点)。** `latch_threshold` → Layer 5の初期評価(finalize)・保留キューの再提示(drain)・グループ集約チェック(06 §6〜§8の3経路)。`notification_daily_limit` → 提示時のD-08日次上限判定とnearby存在通知の上限判定。`notification_concurrent_limit` → 提示時のD-08同時進行上限判定。**実験行が存在しない・status!='running'・開始前のときは現行定数(latch_calcのLATCH_THRESHOLD=0.60・D08_DAILY_LIMIT=6・D08_CONCURRENT_LIMIT=3)をそのまま使う** — つまり実験を1行も作らない環境の挙動は従来と一字変わらない。drainの対象抽出は対照値・処置値の**小さい方**の閾値で行い、行ごとの適用は規則4で判定する(処置閾値を下げた実験で[処置値, 対照値)のスコア行が対象から漏れないため。閾値を上げた実験で既存candidate行が対照挙動で提示され得ることは保守側の許容)
6. **停止と判定不能時の持続規定(09 §5)の表現 → status='stopped'。** 停止(有害シグナルによるもの・最小期間2週間の満了・判定不能の確定)はすべてstatus='stopped'で表現し、**全群へ対照値を適用する通常運用へ戻る**。割付行は残す(継続観察・再開時の同一割付)。有害シグナル(通報率・通知オフ率の急増)の検知はO-06の週次警報(kpi_alert)が担い、停止操作は人間がCLIで行う(09 §5「途中の判断は有害シグナルの停止ルールに限る」の実装解釈)
7. **適用値の伝播。** 実験行の参照はプロセス内キャッシュ(30秒)で行う。開始/停止の反映は最大30秒+評価周期の遅延を持つ(即時反映を要求する運用場面はない)
8. **観測APIは割付の公開面。** GET /v1/users/me/ab(第5節)は自分の割付一覧を返す読み取り専用API。将来のクライアント側適用対象(表示情報量・通知文)の取得面を兼ねる。variantの値自体はユーザー体験に表に出ない(閾値・上限はサーバ内部値)

### mlr_baselines(MLR週次中央値ベースライン・v0.8新設)

09 D-02の「Phase 2の4週間MLR週次値の中央値をベースラインとして記録し、以降は前期比マイナス5ポイント以内を維持条件とする」の記録の器(A-03)。**users・Intentと紐付ない観測用の独立表**(kpi_dailyと同型の位置づけ)。

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| id | uuid | PK | |
| value | numeric | NOT NULL | 週次MLRの中央値(0〜1)。判定の基準値 |
| source_weeks | jsonb | NOT NULL | 起点とした4週分の `{"week_start": "YYYY-MM-DD", "weekly_mlr": 0.0nn}` の配列(記録の根拠・再計算可能な痕跡) |
| recorded_at | timestamptz | NOT NULL | 記録時刻(Clock由来) |

行は追記のみ(再ベースライン〔A/B採用後の4週間再計測〕のたびに行を追加)。**判定はrecorded_at最新行を用いる**。記録・判定はCLI(`python -m latch.obs.mlr`のbaseline/show/check)。週次MLRの導出はkpi_dailyのmlr行(日次の分子/分母)を週へ集約した値(比率は保存しない規律どおり集計時に計算)。

## 3. Index設計

01第18節の要件に対し、PostgreSQLでの実装を次のように定める。検索順序は固定せず、選択度の高い条件を複合Indexで covering する。

```sql
-- アクティブIntentのマッチング検索(Layer 1)
CREATE INDEX idx_intents_matching ON intents
  (category_primary, time_start, expires_at)
  WHERE status = 'active';
-- 地理(PostGIS)。draftのgeo_centerはNULLのため対象外(第5節の検証規則)
CREATE INDEX idx_intents_geo ON intents USING GIST (geo_center);
-- Hard Constraint用の部分Index
CREATE INDEX idx_intents_budget ON intents (budget_max) WHERE status = 'active';
CREATE INDEX idx_intents_participants ON intents (participants_min, participants_max) WHERE status = 'active';
-- 期限処理・所有者参照。draftを含む(v0.4: 下書きのままexpires_atを過ぎたらexpiredへ遷移させるため)
CREATE INDEX idx_intents_expires ON intents (expires_at) WHERE status IN ('draft','active','paused');
CREATE INDEX idx_intents_user ON intents (user_id, status);
-- Vector(pgvector, HNSW)。draftはEmbeddingしないためNULL(第2節)
CREATE INDEX idx_intents_embedding ON intents
  USING hnsw (embedding vector_cosine_ops);
```

## 4. 未決事項の決定

### D-06 グループ候補の中間表現(保持構造のみ)

- 決定内容: 3人以上の候補はgroup_candidatesテーブルで保持する。Intent集合をuuid配列(intent_ids、3〜4件)で持ち、集合単位の評価結果(member_scores)と集約スコア(aggregate_score)を置く。メンバー間のペア評価は既存のmatch_candidatesを再利用する(メンバー全組み合わせでペアレコードを生成し、group_candidatesから参照する)。成立時はlatches.group_candidate_idで紐付ける
- 根拠: match_candidatesは2 Intent固定のままペア評価・Calibrationの単位(01第14節)を保存し、集合の管理は別テーブルに分離する。MVP上限が4人で固定長に近く、参加の中間テーブルを挟む正規化の利益がないため、配列で持つ
- 本書の範囲: 保持構造のみ。部分YES(partial_accept)の確定条件、提案の通知順序、成立集合の選択規則、aggregate_scoreの集約規則と通知閾値の適用方法は、06マッチングパイプライン設計書がD-06として確定する
- 01への影響: 第17節にgroup_candidatesを追記

## 5. API仕様

認証は04 D-21に従う(`Authorization: Bearer <JWT>`)。全API認証済みユーザーのみ。認証を要求しないエンドポイントは`POST /v1/auth/token`(IdPトークンをbodyで受ける)と`POST /v1/auth/refresh`(リフレッシュトークンをbodyで受ける)の2つに限る。APIが発行するJWTのclaimには`auth_provider`と`auth_subject`を含め、認証済み操作はこの2つのclaimでUser行と紐付ける(04 D-21)。Intentの参照・更新・削除・回答は所有者本人または提案の参加者のみに許可し、公開設定による開示範囲の制御(proposal生成分岐、v0.4)とブロックの判定はサーバ側で強制する(01第22節・08 v0.4第5.3節)。レート制限(08第5.4節)の超過は、Active Intent数上限のみ422(ACTIVE_INTENT_LIMIT)、作成・更新・API全体の上限は429(RATE_LIMITED)を返す。

**ページネーション共通規定。** 一覧系API(GET /v1/latches、GET /v1/notifications、GET /v1/latches/{id}/messages、GET /v1/users/me/blocks)はcursor方式とする。リクエストは`?cursor=<opaque cursor>&limit=<1〜100>`(limitの既定値20、超過は422)。応答は`{"items": [...], "next_cursor": "..."}`(次ページがなければ`"next_cursor": null`)。cursorはサーバ生成の不透明文字列とし、クライアントは値を解釈しない。既定ソートは、latchesが対象時刻(time_start)昇順・同点はcreated_at降順(03 D-08の提示順に整合)、notificationsとblocksがcreated_at降順、messagesがcreated_at昇順(会話の自然順)。

**エラー形式(共通)**

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "time_start is required", "details": {} } }
```

クライアントはcodeで分岐し、messageは表示の参考にしか使わない。codeの列挙は次のとおり。

| HTTP | code | 意味 | 主な発生箇所 |
|---|---|---|---|
| 400 | MALFORMED_REQUEST | JSON形式不正・型不一致 | 全API |
| 401 | UNAUTHENTICATED | JWT無効・期限切れ・失効リスト掲載(04 D-21) | 全API |
| 401 | INVALID_IDP_TOKEN | IdPトークン検証失敗(JWKS署名検証等) | POST /v1/auth/token |
| 401 | INVALID_REFRESH_TOKEN | リフレッシュトークン無効・回転後の再利用検知 | POST /v1/auth/refresh |
| 403 | FORBIDDEN | 認可違反(所有者・参加者以外の操作) | 全API |
| 404 | NOT_FOUND | リソース不在 | 全API |
| 409 | LATCH_EXPIRED | response_deadline / expires_at経過後の回答 | LATCH回答 |
| 409 | ALREADY_ANSWERED | 同一LATCHへの二重回答 | LATCH回答 |
| 409 | UNANSWERED | 未回答での回答取り消し(v0.6実装・D-06) | response/undo |
| 409 | ALREADY_LEFT | 参加取り消し済みでの再取り消し(v0.6実装・D-08) | leave |
| 409 | LATCH_CLOSED | 競合クローズ後(他LATCH成立・参加Intent変化等、01第8節) | LATCH回答 |
| 409 | CHAT_READONLY | ブロック適用中(08 D-23)または参加取り消し済み(D-08)のLATCHへの送信 | チャット送信 |
| 409 | ATTENDANCE_ALREADY_SUBMITTED | 実施自己申告の二重回答(訂正不可) | attendance |
| 409 | ATTENDANCE_WINDOW_CLOSED | completed遷移から3日経過後の自己申告 | attendance |
| 409 | SURVEY_ALREADY_ANSWERED | アンケート回答の二重回答(ラウンド単位・訂正不可。v0.7) | survey |
| 409 | SURVEY_NOT_DUE | 配信対象外のラウンドへの回答(onboarding=active化されたIntentがまだない / week4=初回回答から28日未経過または初回未回答。v0.7) | survey |
| 409 | USER_EXISTS | 登録済みのauth_subjectでの初回登録 | POST /v1/users |
| 422 | VALIDATION_ERROR | 必須欠落・値域外・過去時刻・対象領域上限(7日)超過等(03 v0.4 D-19、下記の時刻検証) | intents系・users系 |
| 422 | UNDER_AGE | 18歳未満の登録・20歳未満の飲酒Intent作成(08 D-10) | POST /v1/users・intents作成更新 |
| 422 | GEOCODING_FAILED | location.nameに該当する地物が存在しない(ジオコーディング失敗) | intents作成・更新 |
| 422 | ACTIVE_INTENT_LIMIT | Active Intent 5件超過(08第5.4節) | intents作成 |
| 429 | RATE_LIMITED | 作成20件/日・更新6回/時・API 60req/分の超過(08第5.4節) | 全API |
| 503 | LLM_UNAVAILABLE | ParserのLLM障害(timeout等)。クライアントは再試行ボタンを提示し、422のフォームフォールバックとは区別する(07 D-17) | parse |
| 503 | DEPENDENCY_UNAVAILABLE | DB・Redis等の依存障害 | 全API |

### 認証・ユーザー系(04 D-21の契約化)

**POST /v1/auth/token — IdPトークン検証とJWT発行**

```json
request:  {"provider": "google" | "apple", "idp_token": "<IdPが発行したIDトークン>"}
response: 200
{
  "access_token": "<JWT>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "<回転式・30日>",
  "user": {"id": "<uuid>|null", "profile_complete": false}
}
```

APIはIdPの公開鍵(JWKS)でidp_tokenを検証し、auth_provider+auth_subjectでUserと紐付ける(04 D-21)。紐付くUserがない場合は認証自体は成功とし(200)、User作成は次の`POST /v1/users`で行う(初回登録フロー)。`profile_complete: false`が初回登録待ちの合図である。errors: 401 INVALID_IDP_TOKEN、429 RATE_LIMITED、503 DEPENDENCY_UNAVAILABLE(JWKS取得失敗等)。

**POST /v1/auth/refresh — アクセストークンの再発行(回転式)**

```json
request:  {"refresh_token": "..."}
response: 200 {"access_token": "<JWT>", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "<新しいトークン>"}
```

リフレッシュトークンは回転式で、応答時に旧トークンを無効化する(04 D-21)。無効化済みトークンの再提示は盗難の疑いとして401 INVALID_REFRESH_TOKENを返し、当該ユーザーの当該IdPセッションのトークン族全体を失効させる(安全側に倒す)。errors: 401 INVALID_REFRESH_TOKEN、429 RATE_LIMITED。

**POST /v1/auth/logout — 即時失効**

```json
request:  {} (AuthorizationヘッダーのJWTが対象)
response: 204
```

対象JWTをRedis失効リストへ登録し、対応するリフレッシュトークン族を無効化する(04 D-21の即時失効の操作面)。以後の同JWTの提示は401 UNAUTHENTICATED。

**GET /v1/users/me — 自ユーザー情報取得**

```json
response: 200
{"id": "<uuid>", "display_name": "...", "profile": {"bio": "..."},
 "birth_date": "1990-04-01", "profile_complete": true}
```

本人のみ。birth_dateは本人自身にのみ返す(相手・提案画面には一切出さない、08 D-10・D-11)。

**POST /v1/users — 初回登録(04 D-21「紐付くUserがない場合は新規登録」)**

```json
request:  {"display_name": "...", "birth_date": "1990-04-01", "profile": {"bio": "..."}}
response: 201 {"user": {"id": "<uuid>", "display_name": "..."}}
errors:   422 VALIDATION_ERROR(display_name / birth_dateの必須欠落・形式不正)
          422 UNDER_AGE(18歳未満、08 D-10)、409 USER_EXISTS
```

Authorizationヘッダーは必須である(本APIは認証不要の2エンドポイントには含まれない)。サーバは認証済みJWTの`auth_provider`・`auth_subject`claimを取り出し、この2値に紐付くUser行を本APIで生成する(User.idは新規採番)。`POST /v1/auth/token`で`profile_complete: false`を受けたクライアントが直後に呼ぶ経路である。birth_dateは必須。呼び出し時点のClock.now()から18歳未満と判定された場合は登録を拒否する(422 UNDER_AGE、08 D-10)。claimに紐付くUserが既に存在する場合は409 USER_EXISTS。表示名・プロフィールの内容審査はMVPに含めない(02 D-12)。

### POST /v1/intents/parse — 構造化プレビュー(保存しない、01レビューSP-4)

```json
request:  {"text": "今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。予算は5000円くらい。会社関係の人は避けたい。"}
response: 200
{
  "structured_intent": {
    "category": {"primary": "drinking", "secondary": null},
    "alcohol_involved": true,
    "time": {"start": "2026-09-26T20:00:00+09:00", "end": null, "flexibility_minutes": null},
    "location": {"name": "天文館", "radius_m": null, "flexibility": null},
    "budget": {"max": 5000, "currency": "JPY"},
    "participants": {"min": 2, "max": 4},
    "soft_constraints": ["軽く飲みたい"],
    "negative_constraints": [],
    "ng_unverifiable": ["会社関係の人は避けたい"]
  },
  "warnings": [
    {"code": "NG_CONDITION_DOWNGRADED", "condition": "会社関係の人は避けたい",
     "message": "この条件は確実には除外できません。参考条件として扱います"}
  ]
}
```

応答のstructured_intentは07のParser出力スキーマと同形である(visibility(公開設定)とnotification_level(お知らせ設定)はParserが出力せず、確認モーダル・預け方パネルで選択する03 v0.4 D-19のため含まない。座標を返さずlocation.nameのままとするのは、ジオコーディングが保存前に行われる04第3節の構成による)。errors: 422 VALIDATION_ERROR(必須3フィールドの抽出不能、フォームフォールバックへ。**textの300字超過も422とし、入力の修正を促す。切り詰めはしない**(07 v0.4))、503 LLM_UNAVAILABLE(LLM障害、再試行ボタンへ。07 D-17)。warningsは02 D-04の注意表示(03第3節)のデータソースである。

### POST /v1/intents — 確認済み構造データで作成(status: draft可、v0.4)

```json
request:  {"raw_text": "...", "status": "active", "structured_intent": {
            "category": {"primary": "drinking", "secondary": "焼肉"},
            "alcohol_involved": true,
            "time": {"start": "2026-09-26T20:00:00+09:00", "end": "2026-09-26T23:00:00+09:00"},
            "location": {"name": "天文館", "radius_m": 2000},
            "budget": {"max": 5000, "currency": "JPY"},
            "participants": {"min": 2, "max": 4},
            "visibility": "hidden_until_match",
            "notification_level": "proposals_only",
            "expires_at": "2026-09-26T23:00:00+09:00",
            "soft_constraints": ["軽く飲みたい"],
            "ng_unverifiable": ["会社関係の人は避けたい"],
            "negative_constraints": []
          }}
response: 201 {"intent": {"id": "…", "status": "active", "version": 1, "expires_at": "…"}}
```

statusは`active`(既定・省略可)または`draft`を取る。

- **activeで作成する場合。** 確認フロー(03 v0.4第3節)を経たデータのみを受け付ける。作成確定の直後にMatch Eventを発行する(06 v0.4第9節)
- **draftで作成する場合(下書き保存、v0.4)。** raw_textのみ必須とし、structured_intentの内容は任意(部分的な中途データでも保存する)。プロトタイプの「下書き保存」ボタン(トースト「下書きを保存しました」)に対応する経路である。検証は「raw_textの必須(最大300字)」に限定し、structured_intentの必須3フィールド・時刻検証・ジオコーディング・年齢検証は**active化時(PATCH)まで適用しない**(指定値の形式不正のみ400 MALFORMED_REQUESTで返す)。draftはEmbeddingを行わず、Layer 1〜5の対象外であり、Match Eventを発行しない(06 v0.4第9節)。visibilityの既定値(hidden_until_match)はdraftでも格納する
- `expires_at`はstructured_intent内のフィールドである(有効期限の設定経路、02第4節#4・03 v0.4 D-19)。UIの有効期限は4つの選択肢から選ぶ方式であり(03 v0.4第3節)、クライアントは選択値の絶対時刻をexpires_atとして送る。nullの場合はサーバ側で「time_start+3時間に最も近い有効期限選択肢」(03 v0.4第3節の4値。既に過ぎている選択肢を除く)と同時刻を補完する(v0.4: UIの選択式とAPI直接呼び出しの既定値を揃える。クライアントの補完に依存しない)。draftでは補完を行わずNULLのまま保存する(第6節の遷移表)

**受け渡しと検証の規則:**

- **locationの受け方とジオコーディング。** リクエストのlocationは`{"name": <地名文字列>, "radius_m": <整数|null>}`であり、座標は受けない。API保存処理内でlocation.nameをジオコーディングし(04第3節の正転、セルフホスト地物データ)、intents.geo_centerを確定する。該当地物が存在しない場合は422 GEOCODING_FAILEDを返し、クライアントはIntent入力画面の条件リストへ戻して修正を促す(03第3節)。PATCHでlocationを含む変更の場合も同様。**この検証はstatus=activeの作成・更新に適用する(draftでは適用しない。draftのgeo_centerはNULL可とし、active化時に確定する)(v0.4)**
- **structured_intentの置換単位。** structured_intentは変更後の全量を送る(全置換)。raw_textも同時に送り直す。PATCHと同一契約とし、Intentはversion単位の条件スナップショットとして扱う
- **時刻検証。** time_startが過去(呼び出し時点より前)の場合、およびtime_startが現在+7日を超える場合は422 VALIDATION_ERROR。expires_atも同様に、過去・現在+7日超は422。上限7日の根拠は01第6節の対象領域「今〜数日以内の食事・飲み」であり(03 D-19のデフォルト=time_start+3時間の逸脱は明示指定のみ起こる)、パイプラインの時間Bucket管理対象を有限(7日×48 Bucket)に保つ。**draftでは指定値が存在する場合のみ時刻の形式検証(ISO 8601)を行い、過去時刻・上限超過の検証はactive化時まで適用しない(v0.4)**
- **その他の422。** 必須欠落(category / time.start / location、03 v0.4 D-19。activeのみ)、alcohol_involved=trueかつ作成者が20歳未満(birth_dateから判定、08 D-10の作成時検証、422 UNDER_AGE)。visibilityは欠落時に既定値hidden_until_matchを格納し(04 v0.4のDEFAULT)、422の対象としない。UIでの公開設定の選択(03 v0.4第3節)は初期値として同値を表示する
- **alcohol_involvedの確定。** リクエストの値は確認フロー経由のParser判定値であるが、保存時にcategory_primary=drinkingであればサーバ側でalcohol_involved=trueを確定する(クライアント修正値より優先する。07規則7)。drinking以外のカテゴリではリクエスト値をそのまま格納する
- 保存時に07出力由来の値を05第2節の構造へ格納する(ng_unverifiableの各要素は`downgraded_from_ng: true`のsoft_constraintsへ、他は独立カラムへ)。status=activeの作成確定の直後にMatch Eventを発行する(06第9節)。**draftではEmbeddingとMatch Eventの発行を行わない(v0.4)**

### POST /v1/latches/{latch_id}/response — LATCH回答

```json
request:  {"response": "yes"}
response: 200 {"latch": {"id": "…", "status": "partial_accept", "response_deadline": "…"}}
errors:   409 LATCH_EXPIRED(回答期限切れ) / 409 ALREADY_ANSWERED(二重回答) / 409 LATCH_CLOSED(競合クローズ後)、422 VALIDATION_ERROR(response値が不正)
```

`response`は `yes` / `no` / `defer`(見送り)の3値(03 D-07)。**UI(04)から送られるのはyes(参加する)とdefer(今回は見送る)の2値で、noのUI導線は廃止した(v0.6・D-05。API値としては温存し、02#14の試験はAPI直叩きで検証する)**。defer回答では任意で`reasons`(値はtime/place/size/mood・複数可)を受け付け、responses.reason_codesへ格納する(D-07)。期限切れの判定はstatusに加えてresponse_deadline / expires_atとの比較を含む条件付きUPDATEで行い(06第6節)、バッチ遅延時にも期限後の回答が成立しない。

### POST /v1/latches/{latch_id}/response/undo — 回答取り消し(v0.6新設・D-06)

```json
request:  {}
response: 200 {"latch": {"id": "…", "status": "proposed", "response_deadline": "…"}}
errors:   409 UNANSWERED(未回答)/ 409 LATCH_CLOSED(matched済み・競合クローズ後)/ 409 LATCH_EXPIRED(回答期限・Intent期限経過)/ 404(参加者以外)
```

自分の回答を取り消して未回答へ戻す(04のjoined・declined状態の「回答を取り消す」「取り消す」)。latches.responsesから自分の回答を除去し、状態をpartial_acceptまたはproposedへ戻す。reason_codesが紐づく場合は同時に破棄する。latch_status_eventsへ遷移を記録する。回答APIと同一の直列化(FOR UPDATE+条件付きUPDATE、06第6節)で実装し、取り消しと回答の競合を排除する。実装はUI v2 Phase 1B。

**実装が確定した細目(v0.6実装・Phase 1B)。** 取り消し後の状態は残ったresponsesから再計算する(他者のyesが残る=partial_accept・誰のyesも残らない=proposed)。他者のno/deferが残る場合は取り消してもrejectedに戻れないため409 LATCH_CLOSEDで拒否する。rejected確定時に作成されたcalibration_recordsは取り消しと同じトランザクションで破棄する(「レコードは回答確定時に作成」の規則を維持し、再確定時のUNIQUE(latch_id)衝突を防ぐ)。参加者以外は404 NOT_FOUND(回答APIの403 FORBIDDENと扱いを分ける・存在の開示範囲を attendance と同じに揃える)。

### POST /v1/latches/{latch_id}/leave — 参加取り消し(v0.6新設・D-08)

```json
request:  {}
response: 200 {"latch": {"id": "…", "status": "matched", "left": true}}
errors:   409 ALREADY_LEFT(取り消し済み)/ 409 LATCH_CLOSED(matched以外)/ 404(参加者以外)
```

matchedのLATCHで自分の参加のみを取り消す(05画面の「この成立を取り消す」)。次を一つのトランザクションで行う。①messagesへシステム行(sender_idはNULL・body「{表示名}が参加を取り消しました」)を挿入する。②自分をチャット書込不可とする(latches.left_user_idsへ追記・以後の送信は409 CHAT_READONLY)。③calibration_records.cancelled_after=trueを記録する。**latches.statusはmatchedのまま変わらない(残りのメンバーの会話は継続し、LATCHは解散しない。headcountの再判定も行わない)。**残りのメンバーへはシステム行で通知する(プッシュ通知はしない)。参加Intentは待機に戻らない(削除もしない)。対象はmatchedのみで、completed後は409 LATCH_CLOSED。実装はUI v2 Phase 1B。

### POST /v1/latches/{latch_id}/attendance — 実施自己申告(09 D-09)

```json
request:  {"attended": true}
response: 200 {"latch_id": "…", "actual_attended": true}
errors:   409 ATTENDANCE_ALREADY_SUBMITTED(二重回答) / 409 ATTENDANCE_WINDOW_CLOSED(3日経過)、404(参加者以外)
```

completed遷移後のLATCHに対し、参加者が「実際に会いましたか?」に1タップで回答する(09 D-09)。`attended: true`はcalibration_records.actual_attended=trueへ、`attended: false`はcancelled_after=trueへ反映する。対象はcompletedのLATCHのみで、遷移から3日以内(超過は409)。回答は初回のみ受理し訂正不可とする(Ground Truthの純度を保つため。誤操作は運用で対処)。無回答(スキップ)はこのAPIを叩かないことで表現され、欠測として扱う(09 D-09)。

### GET /v1/users/me/survey・POST /v1/users/me/survey — 体感アンケート(v0.7新設・09 §2.3)

**GET /v1/users/me/survey — 自分の回答状況(配信すべきラウンドの判定用)**

```json
response: 200
{
  "onboarding": {"status": "not_due", "available_at": null},
  "week4":      {"status": "not_due", "available_at": null}
}
```

各ラウンドの`status`は`due`(未回答かつ配信条件を満たす)/ `answered`(回答済み)/ `not_due`(未回答かつ配信条件未達)の3値。`available_at`はweek4のみ意味を持ち、onboarding回答済みなら「onboarding回答時刻+28日」の絶対時刻、それ以外はnull。本人のみ(未登録JWTは404 NOT_FOUND・consentと同一挙動)。

**POST /v1/users/me/survey — 回答の保存(単発)**

```json
request(onboarding):  {"round": "onboarding", "answers": {"convenience": 4}}
request(week4):       {"round": "week4", "answers": {"convenience": 4, "continuation": 5},
                        "free_comment": "待っているだけで提案が届くのが楽しい(任意・最大2000字)"}
response: 201 {"survey": {"round": "onboarding", "answered_at": "2026-10-12T09:00:00+09:00"}}
errors:   409 SURVEY_ALREADY_ANSWERED(二重回答)/ 409 SURVEY_NOT_DUE(配信条件未達)
          422 VALIDATION_ERROR(round値不正・answersの値域外・必須設問欠落・onboardingへのcontinuation/ free_comment付与・free_comment 2000字超過)
          404 NOT_FOUND(未登録)
```

onboardingは`answers.convenience`のみを受理する。week4は`answers.convenience`と`answers.continuation`を必須とし、`free_comment`を任意で受理する。回答はラウンドごとに初回のみ受理し訂正不可とする(09 §2.3「単発の配信」・UNIQUE(user_id, round)のDB強制)。

**配信契機の実装方式(設計判断)。** 09 §2.3「定量5段階はオンボーディング直後(初回Intent保存後)と4週後の2回、アプリ内で配信する(1回あたり3設問以内)」を次のように実装する。

1. **onboardingラウンドのdue条件 = active化されたIntentの存在。** ユーザーに`status != 'draft'`のIntentが1件でも存在するときdueとする(active・paused・matched・expired・cancelledは「かつてactive化された」痕跡として含む。draftは保存でありactive化でないため含めない)。判定はアクセス時(GETの応答時)に行い、スケジューラを用いない。active化後に全Intentを削除したユーザーはdueでなくなる(削除はIntent行ごと痕跡を消すため。この限界はここに記録しておく)
2. **week4ラウンドのdue条件 = onboarding回答から28日(4週)経過。** 起点を「初回回答時刻(survey_responses.created_at)」に置く(本表内で完結し、計測起点が一意に定まる)。onboarding未回答者のweek4は発生しない — 単発の配信を徹底し、未回答者への再催促を行わない(09 §2.3「押しつけにならない」)
3. **通知typeは追加しない。** プッシュ・お知らせ・バッジ・モーダルによる催促を行わず(D-08の通知日次上限も消費しない)、03待機中画面への受動的な配信カードで表示する(回答しなくても他の操作を妨げない)。回答済みラウンドは二度と表示しない(status=answeredとUNIQUE制約で担保)
4. **設問構成。** onboarding=便利さ5段階(convenience)の1問。week4=便利さ(convenience)+継続意向(continuation)の5段階2問+任意の自由記述1問の計3問(1回あたり3設問以内の上限内)。設問文言(日本語)はfrontendの文言集約ファイル(texts.js)に単一ソースを置く(M3 ws-7流儀)。03への画面仕様の反映は次回03改版時に行う(プロトタイプv2にアンケート画面はなく、運用ルール6により03が実装状況との対応を管理する)

**集計(判定材料の導出)。** 仮説1・5の判定(09 §2.3の事前固定基準)に必要な集計を`python -m latch.obs.survey`(latch/obs/survey.py)が1コマンドで出力する: ラウンド別回答数・convenienceの平均(仮説1: **week4の5段階平均が4.0以上**)・continuationの1〜5分布と4点以上の割合(仮説5: **継続意向4点以上の回答者が過半**)。判定の実施はPhase 2以降であり、本実装は判定材料の導出のみを担う(行動面の指標〔週次Intent再作成率・30日継続率〕はkpi.py既存の集計による)。

### GET /v1/users/me/ab — 自分のA/B割付一覧(v0.8新設・09 §5)

```
request: (認証ヘッダのみ)
response: 200 {"assignments": [
             {"experiment": "latch_threshold",
              "variant": "control" | "treatment",
              "assigned_at": "2026-10-12T09:00:00+09:00"} ...]}
errors:   401(認証欠落・既定の認証エラー)
```

読み取り専用。割付がまだ無いユーザーには空配列を返す(**このAPIは割付を作らない** — 割付は適用経路〔提案評価〕でのみ発生する。第2節の実装規定2)。停止済み実験の割付も返す(継続観察の素材)。応答にはstratumを含めない(内部の層記録は分析用途)。UIを持たない(frontendは本改版では不変)。

**集計・運用の経路。** 実験の開始/停止・一覧は`python -m latch.obs.ab`(latch/obs/ab.py)が担う。MLRベースラインの記録と前期比判定は`python -m latch.obs.mlr`(latch/obs/mlr.py)。D-02警報線(YES率10%未満・通知オフ率週比2倍)とA/B有害シグナル(通報率急増)の自動検知は週次警報ジョブ(latch/obs/kpi_alert.py・M4 ws-4のkpi_daily集計に乗る拡張)。いずれも判定の実施はPhase 2以降の運用であり、本実装は装置とその試験まで(09 §5/§6は仕様正本のまま)。



### その他のエンドポイント

| メソッド/パス | 目的 | 認可・備考 |
|---|---|---|
| GET /v1/intents | 自Intent一覧(statusフィルタ可: `?status=draft`等。フィルタなしは全status) | 所有者のみ。ページネーション共通規定を適用。既定ソートはcreated_at降順。下書き一覧とActive Intent一覧の表示に使う(v0.4) |
| GET /v1/intents/expiry-options | 有効期限3選択肢と既定選択の取得(03 v0.5第3節・D-01で4値から3値へ変更。UIの期限計算がcompletion.pyの単一実装〔07 第2節〕を消費するための経路) | 認証済みユーザー全員。選択肢は**「今日 22:00 / 今日 23:59 / 明日 5:00」の3値**(プロトタイプv2準拠)。クエリ`time_start`(任意・ISO 8601 tz-aware・naiveは422 VALIDATION_ERROR)がある場合は`default_index`=`time_start+3時間に最も近い選択可能候補`の位置(なしはnull)。応答は`{"options": [{"label", "expires_at", "selectable"}×3], "default_index": <int\|null>}`。実装はcompletion.pyの呼び出しのみ(新規計算ロジックなし。選択肢変更はUI v2 Phase 1Bで実装) |
| GET /v1/intents/{id} | Intent取得 | 所有者のみ |
| PATCH /v1/intents/{id} | 更新(raw_text+structured_intentの全置換。version+1、Event発行)/ status遷移(下書き→預けるのactive化を含む、v0.4) | 所有者のみ。検証はPOSTと同一(時刻検証・ジオコーディング・年齢検証を含む)。構造データの変更でalcohol_involved=trueとなる場合(category変更を含む)は作成者の年齢検証を行い、20歳未満なら422 UNDER_AGE(08 D-10)。**draft中のPATCH(v0.4)**: status=draftのIntentへのPATCH(下書き内容の更新・再保存)では、raw_textの必須(最大300字)以外の検証・ジオコーディング・Embedding・Match Event発行を行わない(下書き保存と同一の扱い。06 v0.4第9節)。**draft→activeの遷移(v0.4)**: statusを`active`に変更するPATCHはactive作成と同一の全検証(必須3フィールド・時刻検証・ジオコーディング・年齢検証)を通過して初めて受理し、検証不通なら422でstatusはdraftのまま据え置く。受理時にgeo_center・embeddingを確定し、初回のMatch Eventを発行する。条件内容の実質変更を伴わないactive化はversionを据え置く(全置換後のstructured_dataが同一の場合)。active→draftへの逆遷移は不可(打切りはcancel) |
| DELETE /v1/intents/{id} | 削除(01第21節の削除範囲を適用) | 所有者のみ |
| POST /v1/intents/{id}/pause / resume | 停止・再開 | 所有者のみ |
| GET /v1/latches | 一覧(本人関与かつproposed以降、03第2節)。ページネーション共通規定を適用 | 本人のみ |
| GET /v1/latches/{id} | 詳細(成立後に解放情報を含む、03第6節) | 参加者のみ |
| POST /v1/latches/{id}/messages | チャット送信 | 参加者・matched以降。ブロック適用中のLATCHは409 CHAT_READONLY(08 D-23) |
| GET /v1/latches/{id}/messages | チャット取得 | 参加者。ページネーション共通規定を適用 |
| POST /v1/sessions | セッション開始(04第2節・09第2.2節のDAU・再来訪計測) | request bodyは空。user_idと時刻のみを記録し、Intentの文言を含まない(08第2.4節)。response: 204 |
| GET /v1/users/me/survey | 体感アンケートの自分の回答状況(v0.7・09 §2.3) | 本人のみ。round別のdue/answered判定+week4のavailable_at |
| POST /v1/users/me/survey | 体感アンケートの回答保存(v0.7) | 本人のみ。ラウンド単位・単発(二重は409 SURVEY_ALREADY_ANSWERED)。設問構成と配信契機は上節の規定どおり |
| GET /v1/users/me/ab | 自分のA/B割付一覧(v0.8・09 §5) | 本人のみ。読み取り専用・割付を作らない(空配列可)。UIなし |
| GET /v1/users/me/blocks | ブロック一覧 | 本人のみ。ページネーション共通規定を適用 |
| POST /v1/users/{id}/block | ブロック | 本人操作 |
| DELETE /v1/users/{id}/block | ブロック解除 | 本人操作。過去の候補除外・読み取り専用化には遡及しない(08 D-23の「回収しない」と同じ線) |
| POST /v1/reports | 通報 | 本人操作 |
| GET /v1/notifications / POST /v1/notifications/{id}/read | アプリ内通知(03 D-18) | 本人のみ。一覧にページネーション共通規定を適用 |
| POST /v1/friends | 友人申請(02 D-03) | **将来版(v0.4でMVP実装から外れた。02 v0.3 D-03再決定)。定義は将来版への復帰用として残置** |
| POST /v1/friends/{id}/accept | 承認 | 同上 |
| POST /v1/friends/{id}/reject | 拒否 | 同上。当該friendships行を削除する(pendingを永久に残さない) |
| DELETE /v1/friends/{id} | 申請の取り消し(申請者)/ 友人関係の解消(当事者) | 同上 |

## 6. 状態遷移のデータ表現

**Intent(01第10節・03 v0.4との整合)**

| 遷移 | トリガー |
|---|---|
| (作成)→active | POST /v1/intents(確認済み保存) |
| (作成)→draft | POST /v1/intents(status=draft、下書き保存。v0.4。Embedding・MatchEventなし) |
| draft→active | PATCH /v1/intents/{id}(status=active。「預ける」操作。全検証通過後に受理し、geo_center・embeddingを確定して初回のMatch Eventを発行する。検証不通は422でdraftのまま、v0.4) |
| draft→expired | expires_at経過(expiry_sweeper。idx_intents_expiresはdraftを含む、第3節 v0.4。active化されないまま期限が切れた下書きの扱いとする) |
| draft→cancelled | DELETE・ユーザーの打切り |
| active→paused / paused→active | pause / resume。resume時はversion+1の再評価Event(update種)を発行する(06第9節)。version+1は、idempotencyキー(UNIQUE(event_type, source_intent_id, version))が同一versionの再発行を許さない(pause→resumeの繰り返しで必ず衝突する)ことへの対応であり、resume後の再評価を「状態が変わった新たな評価世代」として扱う03 D-07の世代管理とも整合する。Embeddingはテキスト不変のため再実行しない(06第9節) |
| active・paused→cancelled | DELETE・ユーザーの打切り |
| active・paused→expired | expires_at経過(時間バッチ。idx_intents_expiresの対象と一致) |
| active→matched | 自身を含むLATCHの成立(同時に他の回答待ち提案は競合クローズ、01第8節) |
| matched→active / matched→expired | 参加Intentを含むLATCHが解散した場合(matched→cancelled、参加Intentの削除・08 v0.3第2.5節)。expires_at経過済みならexpiredへ遷移し、経過前ならactiveへ復帰する(再提案可能。Intentのstatusが「成立済み」のまま凍結しない)。復帰Intentは次の時間Bucket再評価(06第9節)の対象に含める |

**LATCH(回答種別はresponsesに保持)**

| 遷移 | トリガー |
|---|---|
| candidate→proposed | 閾値超過かつD-08上限内での提示(通知送信。**mutedのIntentでは通知送信のみを除き、遷移自体は行う**(06 v0.4第6節))。提示時にresponse_deadlineを再計算して上書きする(06第10節) |
| candidate→expired | 保留中に参加Intentのexpires_at経過(expiry_sweeper)、または提示時に対象開始時刻まで75分を切った保留候補の破棄(03 D-05、06第10節) |
| proposed→partial_accept | 参加者の一部がyes |
| partial_accept→matched | 必要人数全員がyes |
| partial_accept→proposed / rejected→proposed・partial_accept | 回答取り消し(v0.6実装・D-06。取り消し後の状態は残ったresponsesから再計算。他者のno/deferが残るrejectedは取り消し不可=409 LATCH_CLOSED) |
| proposed・partial_accept→rejected | no回答、またはdefer(見送り) — 03 D-07 |
| proposed・partial_accept→expired | response_deadline / expires_atの経過(expiry_sweeper。実行者・周期は06第6節) |
| proposed・partial_accept→cancelled | ブロック・他LATCH成立による競合・参加Intent更新によるHard Constraintの変化(01第8節)。公開範囲違反によるcancelledはv0.4で友人関係の将来版化(02 v0.3 D-03)により経路から外れた |
| matched→cancelled | 参加Intentの削除(08 v0.3第2.5節)。残る参加IntentはIntent側の遷移表(上表)の復帰規則に従う |
| matched→completed | 対象時刻(time_start)の経過。遷移時に実施自己申告の通知を送る(09 D-09) |

## 7. 本書で解消した未決事項と01への反映

| ID | 本書での扱い | 残る作業と担当文書 |
|---|---|---|
| D-06(中間表現) | 解消(group_candidates新設、配列保持+ペア評価再利用) | 成立規則・通知順序・集約規則は06 |

01更新時に適用する反映事項は次のとおり。

- 第17節: group_candidates・friendships・blocks・reports・notifications・messagesの追記、latches.responsesの構造化(yes/no/defer)、response_deadlineの追記、match_candidatesの評価世代(Intentバージョン組)の追記
- 第17節(v0.2追加分): users.birth_date・intents.alcohol_involved・calibration_recordsの追記、messagesのブロック時読み取り専用化の追記(08 D-10・D-13・D-23)
- 第17節(v0.3追加分): structured_dataの正式JSONスキーマ(downgraded_from_ngフラグ)、match_candidates.prev_latch_score・prev_evaluated_at、group_candidates.prev_aggregate_scoreと集合内user_id一意制約、latches.proposalの正式構造、match_eventsのUNIQUE制約とevent_typeへのembedding_completed追加、latch_status_eventsの新設(06 v0.3)
- 第18節: Index設計(第3節)の反映
- 第17節(v0.7追加分): survey_responsesの追記(未反映・次回01改版時に反映)
- 第19節(v0.7追加分): survey 2 API(GET/POST /v1/users/me/survey)の追加(未反映・次回01改版時に反映)
- 第17節(v0.8追加分): ab_experiments・ab_assignments・mlr_baselinesの追記(未反映・次回01改版時に反映)
- 第19節(v0.8追加分): GET /v1/users/me/abの追加(未反映・次回01改版時に反映)
- 第19節: API一覧の拡張(parseのwarnings、responseの3値、友人・通知・通報系の追加)。第19節(v0.3追加分): 認証・ユーザー系(auth/token・refresh・logout、users/me、初回登録birth_date・18歳検証、04 D-21)、sessions、attendance(09 D-09)、ブロック一覧・解除、友人拒否・取り消し、ページネーション共通規定、エラーcode列挙。**第19節(v0.4追加分)**: GET /v1/intents(自Intent一覧・statusフィルタ)の追加、friends系の将来版化
- 第26節: D-06は中間表現のみ解消(残りは06)と記録。**v0.4の反映**は02 v0.3 D-03再決定(visibility 2値の再定義・friendshipsの将来版化・proposal生成分岐)と下書き保存(draftの作成・active化・期限管理)として第26節へ記録済み(01 v0.4)
