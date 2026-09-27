# LATCH マッチングパイプライン設計書

- 文書バージョン: v0.3
- ステータス: Draft
- プロダクト名: LATCH
- 作成日: 2026-09-27
- 前提文書: 01 要件定義書 v0.2 / 02 スコープ合意書 v0.1 / 03 UX仕様書 v0.1 / 04 システムアーキテクチャ設計書 v0.2 / 05 データモデル・API仕様書 v0.3
- v0.3の変更点: 総括レビュー(docs/reviews/final-review.md)の指摘を反映。主要変更は次のとおり。
  - FR-01(Critical): Embedding完了を第2段トリガーとする2段構成に確定。派生イベント`embedding_completed`の設計(idempotency key・version検査・embedding IS NULLの再検査)と、初期LATCH判定p95 10秒の層別予算配分(Embedding / Layer 1〜3 / Jev / 通知)+Jev・Embeddingのtimeout値(第1節・第9節)
  - FR-03(Critical): Jev予算(1Intent日次40回・1ユーザー日次120回)と同一Intent再評価の30分頻度制限、保留キュー再評価の差分化を追加(第5節・第10節)
  - FR-05: 再提案のスコア比較手順を具体化(prev_latch_score退避・|Δ|≧0.05を変化とみなす)(第10節)
  - FR-06: 保留キューの表現(latches.status=candidate)、提示時のresponse_deadline再計算、75分ルール該当候補の破棄、クローズ検知方式(第6節・第10節)
  - FR-07: 同一評価世代でのJev再実行のスキップ規定(jev_result保持・Hard Filterのみ再検証)(第9節)
  - FR-08: 回答処理のUPDATE条件へresponse_deadline / expires_at比較を追加、LATCH期限切れバッチ(expiry_sweeper・60秒周期)を規定(第6節)
  - FR-09: 参照先Intent不在は破棄、payload不正は隔離の区別(第9節)
  - FR-15: debounceの意味論を確定(作成Event即時・更新Eventのみ10秒窓統合)、p95 10秒の計測起点を確定(第9節)
  - FR-16: グループ由来ペアのK_j配分規則(1対1に最低4回保証・グループ由来は最大4回)、Jev未判定ペアを含む集合の保留規定(第5節)
  - FR-17: Layer 1に自己ペア除外(user_id不一致)をHard条件として追加、グループのHard互換条件にも反映(第2節・第7節)
  - FR-45: D-06に必要人数=集合サイズ|S|を明記(第8節)
  - FR-12: 保留キュー再評価トリガー(クローズ検知)の観測点をlatch_status_events(05 v0.3)の挿入と同一トランザクション化(第10節。09第2.1節の集計根拠)
  - fixerレビュー対応: K_j配分でグループ由来ペア不在時の残り枠の行き先を明記(第5節)、「03 F4」を「03 D-08」へ置換(FR-30)、D-15へcircuit breakerの初期値(エラー率50%超またはp95レイテンシ超過×測定窓1分で開放、半開60秒後)を追記(FR-10)、LATCH解散時の残存参加Intentの復帰規定を第6節に追記、起点補償ジョブ(resume時のversion+1再評価Event発行・期限前catch-upスキャン)を第9節に追記(FR-18)

本書を読むうえでの取り決めを先に示す。本書は01第IV部(第11〜16節)を実装設計に落とし、未決事項(D-24, D-06の成立規則・通知順序, D-15)を解消する。Jevのプロンプト・出力スキーマの詳細は07 Jev・LLM利用仕様書が、LLM Gatewayと計測基盤は04が担当する。本書のスコア計算は01第13節の定義(L = H × MutualScore × C)を変更しない。

## 1. パイプライン全体像

パイプラインの起動は、Embeddingの非同期完了(07第1節)に合わせて2段に分かれる。

```text
第1段(即時)
Match Event(作成・更新・削除・期限切れ・指定時刻, 01第5節)
  → Pub/Sub(04) → debounce / idempotency / version検査(第9節)
      作成Event   → Embedding WorkerへEmbedding要求(窓なし即時)
      更新Event   → 10秒窓で統合し、解放時の最新versionでEmbedding要求
      削除Event   → 当該Intentを含む候補・保留の無効化(Layerを経ない)
      期限切れEvent → 当該Intentを含む回答待ち提案のクローズ(Layerを経ない)
  → Embedding Worker → LLM Gateway → Embedding API(非同期)
  → intents.embedding書き込み → embedding_completed発行(match_events、05)

第2段(Embedding完了後)
embedding_completed(第2段トリガー、第9節)
  → Matching Worker
      Layer 1  Hard Filter(SQL / PostGIS / ルール)     pass / fail
      Layer 2  Candidate Retrieval(pgvector ANN)        類似度上位 K_v=50
      Layer 3  Cheap Judge(ルール+類似度)               cheap_score上位 K_c=20
      Layer 4  Jev(原子的判断、07)                       K_j=8回を判定
      Layer 5  LATCH Engine(L算出・閾値・通知生成)        → Notification
  → group_candidates(第7節)/ match_candidates(05)へ記録
```

作成・更新Eventはこの段ではLayer 1以降を実行しない(embeddingがまだ確定していないため)。Layer 1〜5はembedding_completedを起点にのみ走る。詳細は第9節。

各層の上限Kは出力数の上限とする(第8節D-24)。ただしK_jは1イベント処理あたりの「Jev実行回数」の上限であり、1対1候補1件=1回、グループ集合は構成ペアごとに1回を数える(第5節)。どの層でもAIを全件探索には使わない(原則1)。Layer 4を通過した候補のみがLATCH Scoreを持ち、閾値0.80と比較される。

**初期LATCH判定 p95 10秒の層別予算配分(01第20節)。** 本目標の計測対象は作成Event経由の初回提案とする。計測起点は「対象Intentの保存コミット時点」、終点は「提案通知の送信記録時点」とする。作成Eventはdebounce窓を持たず即時処理する(第9節)ため、起点から第1段が直接計時され、窓待ちが予算に乗らない。更新Event経由の再評価は10秒窓の統合を含むため対象外とし、別途計測する(窓込みで15秒以内を目安とする運用目標)。予算配分は次のとおり。

| 区間 | 予算(p95) | 備考 |
|---|---|---|
| Embedding(debounce込み。作成Eventは窓なし) | ≤ 2秒 | timeout 2秒・再試行なし。失敗はD-15のバックフィル経路へ |
| Layer 1〜3(Hard Filter + Candidate Retrieval + Cheap Judge) | ≤ 1秒 | 01第20節の「Candidate Retrieval p95 1秒」(10第4.1節はLayer 1〜2のトレース測定)と同じ枠。Layer 3はルール計算のみ |
| Jev(Layer 4) | ≤ 5秒 | timeout 6秒。10第4.1節のレイテンシ注入(p50 2秒/p95 5秒)をそのまま予算とする。timeout超過は呼び出し失敗(D-15の保留へ。07の再試行1回は出力検証失敗時のみで、timeoutでは再試行しない) |
| Layer 5 + 通知送信 | ≤ 2秒 | 01第20節の「判定後5秒以内」の内側 |
| 合計 | ≤ 10秒 | 超過分は縮退(D-15)ではなく計測対象(第9節の計測起点)で検出する |

Embeddingのtimeout 2秒はEmbedding APIの典型的p95(数百ms〜1秒)に対して十分な余裕を取りつつ、10秒予算の後段を圧迫しない値である。Jevのtimeout 6秒は10の注入試験条件(p95 5秒)を超える打ち切り線として、予算5秒の順守とp99側の吸収を両立する。

## 2. Layer 1 Hard Filter

時間・距離・予算・人数・公開範囲・ブロック・カテゴリ・年齢(飲酒)・自己除外を、SQLとIndex(PostGIS、05第3節)で判定する。判定はpass / failのみで、AIは使わない(原則2)。ペアの成立判定規則を次に確定する。

| 条件 | 判定規則 |
|---|---|
| 自己除外 | intent_a.user_id ≠ intent_b.user_id。同一ユーザーのIntent同士は本層で除外する。レート制限内のActive Intent 5件が同一時間帯・カテゴリに並ぶのは通常利用でも起こり、embedding対象テキストが類似する自己ペアは後段を高スコアで通過するため、自己提案・自己成立を構造的に遮断する。グループ側の集合制約は第7節・05のgroup_candidates |
| 時間 | [time_start, time_end) の交差が存在すること。flexibility(05のstructured_data)があればその分だけ範囲を拡張する |
| 距離 | ST_DWithin(center_a, center_b, r_a + r_b)。両者の許容圏の重なりが成立条件。正確な位置は相手へ表示しない(01第22節) |
| 予算 | ペア予算 = min(budget_max_a, budget_max_b)(NULLは無視)。これが500円未満の場合のみfail。提案表示の予算上限はこのペア予算 |
| 人数(1対1) | 2 ∈ [min_i, max_i] が双方で成立すること |
| 公開範囲 | 片方でもfriends_onlyなら、friendships(05)に承認済み関係が必要 |
| ブロック | blocks(05)に (A,B)(B,A) のいずれも存在しないこと |
| カテゴリ | category_primaryの完全一致。secondaryは本層の対象外(意味的な差はLayer 2以降が担う) |
| 年齢(飲酒) | ペアのいずれかのIntentがalcohol_involved=true(07 Parserの判定、05のカラム)の場合は、双方の作成者が20歳以上(users.birth_dateの自己申告値)であることを検査し、満たさなければfail。飲酒を含むIntentは20歳以上同士の間でのみ候補になる(08 D-10)。APIの作成・更新時検証(05)に対する二重防御である |
| 絶対NG | 判定可能なもの(上記の公開範囲・ブロック)のみ。会社関係者の除外のような判定データを欠くNG条件は本層で除外しない |

**D-04降格条件の経路。** 判定不能なNG条件はSoft Constraintとしてstructured_dataに格納され(05、`downgraded_from_ng: true`)、Layer 3を素通りしたうえでLayer 4のJev入力のsoft_constraintsへ渡る。Jevが「このNG条件と相手の条件から成立し得るか」を意味判定し、would_*_accept_* に反映する。Cheap Judgeは埋め込み類似度ベースのためこの条件を扱わない。プロンプト上の扱いは07が定める。

## 3. Layer 2 Candidate Retrieval

Embedding対象を確定する。raw_text(非公開の原文、01第21節)は埋め込みに使わず、構造化データの正規化テキスト(category、時間帯、場所の地域名、人数、soft constraintsの文言)をembed対象とする。外部送信を判定に必要な最小限へ絞り(01第21節)、raw_textがEmbedding APIへ渡る経路を排除する。ANN SearchはpgvectorのHNSW(cosine、05第3節)で、起点Intentのembeddingをクエリに上位K_v件を取得する。

embedding IS NULL(未完了または失敗)のIntentは、Layer 1〜2の生成元にも対象にもならない(05第2節のカラム説明と同趣旨)。未完了のIntentは第2段トリガー(第9節)のembedding_completedの到着でパイプラインへ投入され、失敗したIntentはD-15のバックフィル完了時のembedding_completedで投入される。D-15のembedding=NULLを「失敗」と扱う規定は、Embeddingの実行自体に失敗した場合に限定され、実行待ち(未完了)は本節の「対象外」として区別する。

## 4. Layer 3 Cheap Judge

MVPの選定は、小型LLMではなくルール+埋め込み類似度の線形合成とする。構成は次の3要素だ。

- 埋め込み類似度(cosine)そのもの — 意味的な遠い候補を落とす
- 時間帯・ペア予算の近さを0〜1に正規化したルールスコア
- soft constraintsの語彙重なり(トークン一致率)。D-04降格のNG条件は`downgraded_from_ng`フラグ(05)で区別し、この計算の対象から明示的に除外する(第2節の「Layer 3を素通り」との整合)

cheap_score = 0.5×類似度 + 0.3×ルールスコア + 0.2×語彙重なり。選定理由は、追加のAPIコストがゼロで決定的(入力に対する出力が再現する)であり、09のOffline評価と10の試験で同じ値を扱えることにある。小型LLM・軽量分類器はcheap_scoreの精度が問題になった段階(09の評価結果)で差し替える。重みの初期値は運用データで調整する。

## 5. Layer 4 Jev

呼び出し条件は「Layer 3通過の上位候補」。1候補につき1回のAPI呼び出しで両方向(would_a_accept_b / would_b_accept_a)を判定し、これを04 D-16の「1実行回数」と数える。入力は両Intentの構造化データの正規化テキスト(soft constraintsとD-04降格のNG条件を含む)で、raw_textは送らない。出力は01第13節の原子的判断(7軸)とする。プロンプト・出力スキーマ・temperature等は07が確定する。コスト制御は呼び出し直前の回数カウンタ(04第5節)による。失敗・上限時の扱いは第8節D-15に従う。

**K_j=8の数え方と配分規則(FR-16)。** K_j=8は「1イベント処理あたりのJev実行回数の上限」とする(回数ベース)。1対1由来のペア1件とグループ集合由来のペア1件を同一の回数単位で数え、次の配分規則を適用する。

1. **1対1の最低枠保証。** 1対1由来のペアに最低4回を保証する。1対1候補(K_c=20)をcheap_score降順で上位4件判定する。MVPの主眼である1対1が、グループ密度の高い時間帯(グループ集合1つが最大6ペアを消費する)に飢餓するのを防ぐ
2. **グループ由来の残り枠。** 残り最大4回を、グループ集合の構成ペアにcheap_score降順で割り当てる
3. **未完了集合の保留。** 3人集合は3ペア、4人集合は6ペアを要するため、4回では全ペアを判定できないことがある。全ペアの判定が揃わない集合は、当該イベント処理では提案化しない。aggregate_score(min over ペア)は未判定ペアがいては確定しないためである。当該集合はgroup_candidates.status=candidateで保持し、未判定ペアを次の再評価(30分Bucket・保留キュー再評価)でのJev予算の最優先対象とする。これにより部分評価が次の再評価で完結し、4人集合は高々2回の再評価で確定する
4. **割当の優先順序。** この回数上限の内側での割当順序は、(a)1対1の最低4回を先に確保し、(b)残り最大4回を、前回未判定ペアの継続評価(優先)→新規グループ集合のペア(新規)の順で割り当てる。1対1の最低4回は継続評価があっても確保する。グループ由来ペアが存在しない(または規則2・3の割当に至らなかった)イベントでは、残り枠を1対1候補のcheap_score順に繰り上げて判定する(1対1の実効上限はこの場合K_j=8)

**同一評価世代でのJevスキップ(FR-07)。** 再評価(30分Bucket・同一IntentのEvent再処理・skipped候補の回収)の際、対象ペアが(a)同一バージョン組(どちらかのIntentが更新されていない)で、かつ(b)match_candidates.jev_resultが既に存在する場合、Jevを再実行せずjev_resultを保持したままLayer 1の再検証(Hの再計算)のみ行う。Intent更新によるバージョン組の変化があった場合のみ新規評価(Jev実行)となる。このスキップがないと、Active Intentが到来BucketごとにK_j=8を消費し、04 D-16の根拠(1イベントあたり平均1〜2回・正常時1万回/日以内)が崩れる。Layer 1の再検証でHard Constraintが不成立となったペアはstatus=closedへ閉じる。Layer 2〜3の順位変動で新たに上位に入ってきたペアは新しいペアであり、通常どおりJev実行の対象となる。

**Jev予算と再評価の頻度制限(FR-03)。** K上限(K_j=8)は1イベントあたりの上限であり、1ユーザーあたりの再評価回数の上限がないと、08第5.4節のレート制限の範囲内(Active 5件×更新6回/時×24時=最大720 Event/日)で日次上限30,000回を枯渇させ、D-15による全ユーザーの提案見送りに持ち込める。よって次の3層の保護を追加する。

| 制限 | 上限 | 超過時 |
|---|---|---|
| 1Intentあたりの詳細評価(Jev実行) | 40回 / 日 / Intent(JST 0時リセット) | 候補をJev未判定として保留(match_candidates.status=skipped、D-15と同一の縮退)。日次リセット後の次のMatch Eventで再評価される |
| 1ユーザーあたりの詳細評価(Jev実行) | 120回 / 日 / ユーザー(Active上限5件×40回の理論値と一致する共有プール) | 同上 |
| 同一Intentの更新による再評価 | 直近の詳細評価(パイプライン投入)から30分空ける | Event自体は処理済みとし、再評価は行わない。次の更新Eventまたは30分Bucket再評価で拾われる |

- 値の根拠。正常利用では1Intentあたりの更新は実効数回/日・1更新あたりの詳細評価は平均1〜2回(04 D-16)であり、40回/日は正常の概ね2〜3倍のマージンである。悪用Intent1本の消費上限は40回/日、悪用ユーザー1人(Active 5件全件を悪用)でもユーザー単位の120回/日で頭打ちとなる。悪用ユーザー10人でも1,200回/日に抑えられ、これは日次上限30,000回の4%であり、ピーク帯の提案停止(D-15)には至らない。30分の頻度制限は時間Bucketの周期と同じ値とし、更新6回/時(10分間隔)の反復更新がそのまま再評価に転化するのを源流で止める。対象領域が今〜数日以内の短寿命Intent(03 D-19)であるため、再評価が最長30分遅延しても価値は保たれる
- カウンタはRedis(04第5節のJevカウンタと同一経路)で、user_id・intent_id・日付キーで保持し、実行要求側(Matching Worker)でINCRする。頻度制限はTTL 30分のキー(reeval:{intent_id})で判定する
- 計測: Intent別・ユーザー別のJev消費はダッシュボード化し、D-16の80% alert(04)には源流上位ユーザーのレポート添付を04次改版で依頼する(異常検知に使う)

## 6. Layer 5 LATCH Engine

スコア算出は01第13節の定義をそのまま実装する。

```text
L = H × MutualScore × C
MutualScore = min(would_a_accept_b, would_b_accept_a)
H: Layer 1の判定時点の再検証(通過=1、不成立=0)
C: 初期値1(Calibrationデータ蓄積後に調整、01第14節)
```

L >= 0.80(D-01、検証は09)なら提案候補とする。提案化の前に03 D-08の上限(1ユーザー日6件、1Intent同時3件)を検査し、上限内なら直ちにproposedへ遷移させて通知する。超過分は保留キューへ置く(表現と提示時の処理は第10節)。提示順は03 D-08のとおり対象時刻昇順、タイブレークはLATCH Score降順。proposal(05第2節の正式構造)の生成はLayer 5が行う(表示用サマリのみ、08第2.3節)。競合制御・回答処理・期限切れ処理は次のとおり確定する。

**回答処理の競合制御(FR-08)。** 回答APIは単一トランザクション内の条件付きUPDATEで直列化する。対象のlatches行をFOR UPDATEで排他してからstatus遷移を適用する。UPDATEのWHERE条件はstatusのみでなく期限比較を含める。

```sql
UPDATE latches SET ... 
 WHERE id = :id
   AND status IN ('proposed','partial_accept')
   AND response_deadline > :now
   AND expires_at > :now
```

`:now`はアプリ層のClock.now()(10第1節のClockインターフェース。DBのclock_timestamp()は使わない。テストのClock注入と時刻源を揃えるため)。影響行数0のときは処理を中止し、事前の読み取り結果に応じて409を返す(期限切れ=LATCH_EXPIRED、既回答=ALREADY_ANSWERED、競合クローズ後=LATCH_CLOSED。05のエラーcode表)。この条件により、期限切れバッチが遅延してstatusがまだproposedのままでも、期限直後の回答は受理されない(期限後成立の構造的排除)。競合クローズは、あるIntentのLATCHがmatchedになった時点で、そのIntentを含むcandidate・proposed・partial_acceptのlatchesをcancelledへ閉じる(01第8節)。回答処理と競合クローズは同一の直列化方式で競合しない。

**解散時の参加Intent復帰。** LATCHがmatchedから遷移して解散した場合(matched→cancelled: 参加Intentの削除、08 v0.3第2.5節)、残る参加Intentは次のとおり復帰させる — expires_at経過済みならexpired、経過前ならactiveへ復帰し再提案可能とする(05第6節の遷移表と同一の規定)。復帰したIntentは次の時間Bucket(第9節)の再評価対象に含めるため、復帰後に再評価されず放置されることはない。なお、進行中の提案の解散(ブロックによるcancelled、D-23)では参加Intentはもともとactiveのままであるため、復帰処理は不要である。

**LATCH期限切れバッチ(expiry_sweeper)。** 実行者はMatching Worker上の定期ジョブ`latch_expiry_sweeper`とする。

- 周期: 60秒。時刻源はアプリ層のClock.now()(10第1節)
- 対象と遷移: `status IN ('proposed','partial_accept')`かつ`(response_deadline <= :now OR expires_at <= :now)` → expired。`status = 'candidate'`(保留キュー)かつ`expires_at <= :now` → expired
- 取得はFOR UPDATE SKIP LOCKEDで行い、回答APIと同一の遷移規則・直列化方式で処理する。バッチと回答APIが同一行で競合しても、UPDATE条件が同一の真実(期限とstatus)を参照するため不整合は生じない
- Intent側の期限切れバッチ(05第6節、idx_intents_expiresの対象)と同一のスケジューラで動かし、切替・停止は単一ジョブとして管理する。60秒はresponse_deadlineの表示精度(「あと◯分」、03第5節)に対して十分な粒度である

## 7. グループマッチ(Group Search)

全組み合わせ探索は行わない(01第15節)。候補Pool(同一時間Bucket・地域・カテゴリでLayer 3を通過したIntent群、上限は第8節D-24)から、次の貪欲法で集合を構成する。

1. Pool内のIntentをcheap_score降順(タイブレークはintent_id昇順)に走査し、種となるIntentを選ぶ
2. 種のparticipants.max >= 3 である場合、Hard互換(人数範囲の共通包含、時間・距離・可視性・年齢(飲酒、第2節)・自己除外 — 集合内のIntentの作成user_idは互いに異なること(第2節) — の成立)なIntentをcheap_score順に追加し、3〜4人の集合を構成する
3. 構成した集合をgroup_candidates(05)へ記録し、全ペアのmatch_candidatesを評価世代付きで生成する。集合のJev判定は第5節の配分規則に従い、未判定ペアが残る集合はstatus=candidateのまま提案対象から外す

集合Sの成立条件は |S| ≧ max(min_i) かつ |S| ≦ min(max_i)(全員の人数条件を満たす)。集約スコアと成立確定条件は第8節D-06で確定する。

## 8. 未決事項の決定

### D-24 各段階の候補数上限Kの向きと確定値

- 決定内容: Kはすべて「その層から次層へ渡す出力数の上限」とする。確定値は Vector検索 K_v=50、Cheap Judge判定 K_c=20、Jev判定 K_j=8。グループの候補Pool上限は15。切り詰め順序は各層のスコア上位(Vector=類似度、Cheap Judge=cheap_score、Pool=cheap_score)とし、同点はintent_id昇順で決定的に崩す。K_jのみ回数ベース(1イベント処理あたりのJev実行回数、1対1ペア=1回・グループ集合=構成ペアごとに1回)とし、第5節の配分規則(1対1に最低4回保証)を適用する。Layer 4への入力順序は、1対1候補とグループ由来ペアの配分が確定したうえでの便宜の並びである
- 根拠: 出力上限とすれば「前層の出力=次層の処理件数」と鎖が繋がり、層ごとの実装と計測(01第20節のCandidate数・Vector Retrieval件数)が一致する。K_j=8は01の「最大5〜10」の中点として、04 D-16の想定(1イベントあたり平均1〜2回、正常時1万回/日以内)と釣り合う。Pool=15は01の「最大10〜20」の中点である。K_jの回数ベース化は、グループ集合のJev消費構造(第5節)をD-16の回数上限と同じ次元に揃えるためである
- 01への影響: 第11節のK上限を確定値へ更新

### D-06 グループLATCHの成立確定条件・通知順序・集合選択・集約規則

- 決定内容: 次のとおり確定する
  - **成立確定条件**: 集合全員のYESが必要(partial_acceptは途中状態)。必要人数は集合サイズ|S|と一致する(集合内全員)。「あと2人の回答が必要」(03第5節)の人数表示は|S|−回答済み人数であり、成立に必要なのは必ず全員である。回答期限の到来時に必要人数が揃っていなければexpiredで閉じる。部分成立(例: 4人提案の3人YESで3人成立)は行わない
  - **通知順序**: 同一Poolから複数の成立可能集合があった場合、aggregate_score降順・同点は集合サイズの小さい順・さらに同点ならintent_id辞書順に順位付け、最上位の1集合のみを通知する。その提案が閉じた後に次の集合を評価して通知できる
  - **成立集合の選択規則**: 第7節の貪欲法(cheap_score降順の種+Hard互換な追加)で確定
  - **集約規則**: aggregate_score = H × min over ペア(MutualScore) × C。集合内全ペアのMutualScoreの最小値を採る。通知閾値は1対1と同じ0.80を適用する(別閾値を設けない)
- 根拠: 部分成立を認めると「4人以上なら行きたい」の人数条件というHard Constraintを事後に緩めることになり、原則2と矛盾する。人数の下限はユーザーが明示した絶対条件である。集約にminを採るのは原則5(片方向の高評価をLATCHとしない)のグループ版であり、最弱ペアが成立確率の上限を決めるため平均を使わない。通知を1集合ずつ行うのは、同一ユーザー群への重複提案によるD-08上限の消費と、選択の混乱を避けるためだ
- 01への影響: 第15節に成立確定条件・通知順序・集約規則を追記

### D-15 縮退運転の内容

- 決定内容: 次のいずれかに該当する場合、**提案は見送り、候補は保留とする**。(1)JevのLLM API障害、(2)04 D-16の回数上限到達、(3)第5節のJev予算(1Intent日次40回・1ユーザー日次120回)または頻度制限由来のスキップ。Cheap Judgeまでの結果で提案しない。保留はmatch_candidates.status=skipped(04・05)で表現し、日次リセット後や障害回復後のMatch Event、および第5節の再評価経路(Bucket再評価・頻度制限解除後の次のEvent)で再評価される。Jev層にはcircuit breakerを設ける。**初期値は次のとおり確定する(FR-10)** — 測定窓1分の間に、(a)LLM Gateway経由のJev呼び出しのエラー率が50%を超える、または(b)同呼び出しのp95レイテンシがtimeout(6秒、07 v0.3第1節)を超える、のいずれかを検知したら開放する。開放中のJev呼び出しはすべて失敗扱い(候補はskippedで保留)。開放から60秒後に半開へ移行し、半開では1リクエストのみを試験し、成功なら閉じ、失敗なら開放へ戻す。根拠: 窓1分はJev予算5秒(第1節)に対して十分短く、障害検知から提案見送りへの切替を1〜2分で完了できる。50%のしきい値は、単発のtimeoutやレート制限のスパイクのような一時的エラーでは発動せず、継続障害のみを拾う(誤開放による提案供給の無駄な抑制を防ぐ)。半開の60秒後に再試験を1リクエストに限定するのは、通常提案への影響を最小化しつつ回復を検知するためであり、開放⇔半開の往復は障害の継続する限り何度でも許す。Embedding失敗のIntentはembedding=NULLで保存し、回復後に一括再エンベディングのバックフィルを行い、それまで候補の生成元にも対象にもしない(バックフィル完了時はembedding_completedを発行してパイプラインへ復帰させる。第9節)。Embedding未完了(実行待ち)は本項の縮退ではなく、第2段トリガー(第9節)の到着待ちとして扱う
- 根拠: 01第16節は「誤った提案を提示するよりも、提示を見送ることを優先する」と定める。Cheap JudgeのスコアはMutualScoreの代替ではなく、これで提案すれば精度未検証の提案が量産され、Mutual Latch Rate(01第23節)を劣化させる。短期間の提案減はコストで、精度の低下は検証(仮説3)の失敗に直結する。予算・頻度制限によるスキップを縮退に含めるのは、悪用による日次上限の枯渇が全ユーザーの提案停止に転化するのを防ぐためである(第5節)
- 01への影響: 第20節に縮退運転の内容を追記

## 9. イベント駆動処理の実装

01第16節のとおり実装する。確定値を次に示す。

```text
Intent API → DB保存 → Match Event発行(payloadにIntent version)
  → Pub/Sub → Matching Worker(第1段)
      1. debounce: 作成Eventは窓を持たず即時処理する。更新Eventのみ、
         同一source_intent_idのEventを10秒のトレーリング窓で統合し、
         窓解放時の最新versionのみ処理する。窓内の統合後は1回の評価に
         合流する(リードエッジ+差し替えの二重評価はしない)。
         削除・期限切れ・指定時刻Eventは統合せず即時処理する
      2. idempotency key: (event_type, source_intent_id, version)。
         match_eventsのUNIQUE制約(05)がDBレベルで重複を排除する
      3. version検査: payloadのversion = 現行versionのときのみ処理する。
         payload version < 現行version → 破棄(古いEvent)。
         payload version > 現行version → 読み取り遅延の可能性として
         IntentをFOR UPDATEで再読み込みし、乖離が続く限り再試行キューへ戻す
      4. 処理失敗: 5回まで再試行 → 失敗理由を保持して隔離
         (match_events.status=quarantined、05)
```

**第2段トリガー(FR-01)。** Embeddingは非同期(07第1節)であるため、作成・更新Eventの処理はEmbedding要求のキックまでである。Embedding WorkerがLLM Gateway経由でEmbedding APIを呼び出し、intents.embeddingとembedding_modelを書き込んだうえで、派生イベント`embedding_completed`(match_eventsに記録、05)を発行する。これが第2段トリガーであり、Matching WorkerがCandidate Retrievalからパイプラインを実行する。

- idempotency key: (embedding_completed, source_intent_id, version)。第1段と同一のUNIQUE制約(05)で重複排除する
- version検査: payload version = 現行versionのときのみパイプラインを実行する。payload version < 現行versionは破棄する(旧versionのembedding。新versionのEmbedding要求が窓統合後に投げられ、その完了イベントが処理を引き継ぐ)。payload version > 現行versionは再試行に回す(読み取り遅延。上限で隔離)
- **Embedding要求のキックはintents.embedding IS NULLのときのみ行う。** embeddingが既に存在する場合(resume由来のversion+1 Event、05第6節 — テキストは不変のため再エンベディングは不要)はEmbeddingをスキップし、第2段相当のパイプライン投入へ直接進む
- embedding IS NULLの再検査: 処理開始時にintents.embedding IS NULLであれば、Embedding完了の書き込みが遅延しているものとして再試行する(通常は発生しない。Embedding Workerが完了後にイベントを発行するため)。5回再試行でもNULLのままなら隔離する
- 未完了(Embedding実行待ち)のIntentはembedding IS NULLかつ現行versionであり、Layer 1〜2の生成元にも対象にもならない(第3節)。実行自体に失敗した場合はD-15のバックフィル対象であり、バックフィル完了時にembedding_completedを発行して復帰する

**30分Bucket再評価と参照先不在・payload不正の区別(FR-09)。** 時間経由の再評価は30分Bucket(01第16節)で、到来したBucketに属するIntentのみを対象にCandidate Retrievalから再実行する。起点Intentのembedding IS NULL(未完了・バックフィル中)の場合は本Bucketをスキップし、embedding_completedの到着で回収される。Eventの取り扱いは次のとおり区別する。

- **参照先Intent不在(削除済み)のEvent**: 正当な遅延Eventである。破棄とし、status=processedとして閉じる(破棄理由をpayloadに記録)。隔離(quarantined)にはしない — 正常な削除後の遅延Eventを隔離すると隔離滞留と誤alertが続発する。当該Intentを含む候補は削除処理(第1段)で無効化済みであり、Event単位の追加処理は不要である
- **payload不正(構造違反・version欠落・型不一致等)**: 処理できない不正ペイロードとして、5回の再試行後に失敗理由を保持してquarantinedへ隔離する(01第16節・10第4.7節の毒ペイロード試験の対象はこちら)

**起点補償ジョブ(FR-18)。** 再評価の起点がMatch Event・Bucketのいずれかに偏ると、欠落した再評価が恒久に回収されない。よって次の2つの補償を設ける。

- **resume時のEvent発行。** pause/resumeのうちresumeは、version+1の再評価Event(update種)を発行する(05第6節)。versionを+1するのは、idempotencyキー(UNIQUE(event_type, source_intent_id, version))が同一versionの再発行を許さないためであり(pause→resumeの繰り返しで同一キーが必ず衝突する)、resume後の再評価を「状態が変わった新たな評価世代」として扱う03 D-07の世代管理とも整合する。Embeddingはテキスト不変のため再実行せず、embedding IS NULLでなければ第2段相当のパイプライン投入へ直接進む(前項のキック分岐)。開始時刻のBucketが経過した後のresumeでも、このEventにより再評価・提案が発生し得る状態に復帰する
- **期限前スキャン(catch-upスキャン)。** expiry_sweeper(第6節)と同一の60秒周期スケジューラで、`status='active'`かつ`expires_atまで2時間以内`かつ`直近の詳細評価の実施から30分以上経過`のIntentを抽出し、Candidate Retrievalからパイプラインを直接投入する(Eventを発行しない。起動経路がスキャンであるだけで処理内容は第2段と同一。Event発行方式だと同一versionのidempotencyキー衝突が生じるため)。Bucket到来Eventの遅延・欠落や、頻度制限・障害の復旧待ちで未評価のまま放置されたIntentを期限前に回収する。値の根拠 — 2時間はIntentの実効寿命(デフォルトexpires_at = time_start+3時間、03 D-19)の大半をカバーし、提案の通知打ち切り線である「対象開始時刻まで75分」(03 D-05)の前に最低2回の再評価機会(30分周期×2時間)を保障する。30分は第5節の頻度制限と同一周期とし、通常経路とcatch-upが二重に評価するのを防ぐ。60秒のスキャン周期はexpiry_sweeperと同一スケジューラで動かし、追加のジョブ管理を発生させない。embedding IS NULLのIntentは対象から除外する(embedding_completedの到着で回収される)

debounceの10秒・retryの5回・頻度制限の30分は初期値とし、計測(Queue lag、01第20節)を見て調整する。

## 10. D-07・D-08の実装箇所

**再提案制御(03 D-07)。** 実装箇所はLayer 5 LATCH Engineの提案化の直前である。検査は2段で、(1)同一Intent組み合わせ(intent_a_id, intent_b_id)のlatches履歴にdefer(見送り)回答がある場合、min(24時間, 対象開始時刻までの残時間の半分)の抑制期間が経過したかを回答時刻から判定する。(2)スコア変化の判定は次の手順で実装する。

1. 評価トランザクション内で、対象ペアの現行match_candidates行(同一バージョン組)をFOR UPDATEで読み、現行のlatch_scoreとupdated_atをprev_latch_score・prev_evaluated_at(05)へ退避する
2. 新しい評価値(jev_result・latch_score)でUPDATEする
3. 「スコア変化あり」の判定: prev_latch_scoreがNULL(初回評価)なら新候補として無条件に変化あり。NULLでなければ |新latch_score − prev_latch_score| ≧ 0.05 を変化ありとみなす。0.05未満は変化なしとして再提案を抑制する(Jev出力の実務精度は±0.05程度であり、0.79→0.801のような閾値近傍のノイズで不毛な再提案を連発させないため)
4. グループ集合も同一手順で、group_candidates.prev_aggregate_score(05)と新aggregate_scoreの差で判定する
5. 評価世代(バージョン組)そのものの変化 — どちらかのIntent更新による新評価 — も条件(b)を満たす(03 D-07の「Intent更新による新候補」)。新バージョン組のレコードはprev値を持たないため、無条件に変化ありとなる。スコアが偶然同一でも、評価世代が新しければ再提案を許す

両方を満たさない再提案はブロックする。no回答が存在する場合はIntent期限まで再提案しない(03 D-07)。

**通知上限と提示順・保留キュー(03 D-08)。** 実装箇所はLayer 5の通知生成と保留キューである。閾値0.80超過の候補は、上限検査の結果にかかわらずlatches行をcandidateとして作成する(05。保留キューの実体はlatches.status=candidateの行)。上限内(日次6件/ユーザー・同時3件/Intent)のものは直ちにproposedへ遷移させて通知し、超過分はcandidateのまま保持する。candidate作成時のresponse_deadlineは保留登録時点の03 D-05式の暫定値であり、提示判定には使わない。

キューの提示順は対象時刻昇順、タイブレークをLATCH Score降順とする(03 D-08)。**提示時(candidate→proposed遷移)には、提示時点で03 D-05の式を再計算し、response_deadlineを上書きする。** 暫定値を流用すると「通知時刻+15分」の下限を割った期限が表示・判定に使われるためである(05のカラム説明も参照)。提示対象を取り出した時点で対象開始時刻まで75分を切っている候補は通知せず破棄する(03 D-05、candidate→expired、05第6節)。保留中のcandidateはexpiry_sweeper(第6節)がexpires_at経過でexpiredにする。

**再評価トリガーと差分化。** すべてのlatchesのstatus遷移は、遷移を書くトランザクション(回答API・競合クローズ・expiry_sweeper・Layer 5のproposed遷移)の内側でlatch_status_events(05)へ記録する(from_status / to_status / user_id / created_at。09第2.1節のMutual Latch Rate集計の供給源)。既存提案のクローズの検知は、当該トランザクションのコミット時に保留キュー再評価イベントを発行する方式とする(イベントの内容はlatch_status_eventsへの挿入と同一トランザクションで確定するため、クローズの取りこぼしがない)。0時リセットは日次カウンタのリセットジョブ(04第5節)の完了時に再評価イベントを発行する。キューの再評価時にすべてのcandidateを再評価はしない。次のいずれかを満たす候補のみ再評価する(差分化、FR-03)。

- 直近の評価以降に、参加Intentの更新(バージョン組の変化)があった
- 対応するmatch_candidatesのlatch_scoreが第10節の0.05基準で変化した(グループ集合はaggregate_scoreの同基準)
- 保留登録後に一度も再評価されていない

変化のない候補は提示順の再計算のみを行い、Jevの再実行はしない(第5節のJevスキップと同一の根拠。キュー長に比例した再評価コストの増幅を断つ)。差分化後も大量保留が続く場合、再評価対象は対象時刻昇順・LATCH Score降順の提示順に沿って上限内の件数のみ処理し、残りは次のトリガーに委ねる。

## 11. 本書で解消した未決事項と01への反映

| ID | 本書での扱い | 残る作業と担当文書 |
|---|---|---|
| D-24 | 解消(出力上限: 50 / 20 / 8、Pool 15、切り詰め=スコア上位+intent_id順。K_jは回数ベース+第5節の配分規則) | — |
| D-06 | 解消(全員YES必要=|S|・部分成立なし、通知はaggregate降順で1集合ずつ、集約=ペアmin、閾値0.80共通) | 検証方法の補完は09 |
| D-15 | 解消(障害・上限・予算超過時は提示見送り+skipped保留、circuit breaker、Embeddingはバックフィル→embedding_completedで復帰) | 試験の裏付けは10 |

01更新時に適用する反映事項は次のとおり。

- 第11節: K上限の向きと確定値(D-24。K_jの回数ベースと配分規則を含む)を更新
- 第12節: Layer 1の年齢条件(飲酒Intentは20歳以上同士、D-10)と自己ペア除外(user_id不一致)を追記
- 第15節: グループの成立確定条件(必要人数=|S|)・通知順序・集約規則(D-06)を追記
- 第16節: debounceの意味論(作成即時・更新のみ10秒窓統合)、Embedding完了を第2段トリガーとする構成(embedding_completed、idempotency・version検査)、破棄/隔離の区別を追記
- 第20節: 縮退運転の内容(D-15。Jev予算・頻度制限由来のスキップを含む)、初期LATCH判定p95 10秒の層別予算配分と計測起点(第1節)を追記
- 第5節・第9節: 判定不能NG条件の経路(Layer 4 Jev入力)を追記(02 D-04と整合)
- 第26節: D-06・D-15・D-24を解消済みへ更新
