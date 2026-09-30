# M3 ws-1(LATCH応答系コア)設計メモ

- 作成: 2026-09-30(agent1)
- 前提: M2 ws-1〜ws-8 マージ済み(最終 f1eb6eb+8da6423・マージ後main test-ci 1188 passed)。
  実行wave上の後続は(ws-2 ∥ ws-3 ∥ ws-4)で並走あり(いずれもws-1依存・本単位の完了待ち)
- 参照仕様: 05 §5〜§6 / 06 §6・§8(D-06)・§10 / 07 §6 / 09 §2.3・第3節 / 03 §2・§5〜§7・D-05・D-07 /
  01 §8 / 02 §4(#13〜#18・#22・#25) / 10 §3 / 12 M3-1・M3-2・M3-9・C9・C10
- 本単位は外部SDKを扱わない(API+DB+既存Layer資産の再利用のみ)。context7確認は不要
- ws-7からの引継ぎ(STATUS M2完了記録・単位表ws-1欄): ON CONFLICT昇格で
  latches.group_candidate_idが旧gidのまま残りうる → §2.10で確定対処
- マイグレーション追加なし(latches・latch_status_events・calibration_recordsとも0001で作成済み・
  head=0005のまま)。共有ci-dbの運用ルール1・2の対象外で、本単位はtest-ciを通常どおり実行できる

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M2が「提示(proposed)まで」を作ったのに対し、本単位は「提示された提案への回答から成立・不成立まで」を作る。
スコープは回答API3本と、成立・不成立を確定する遷移群、Calibration記録。通知送信はws-3、期限切れバッチはws-2。

1. **GET /v1/latches**: 本人関与かつproposed以降の一覧(cursor・対象時刻昇順)
2. **GET /v1/latches/{id}**: 詳細。成立後(matched/completed)は解放情報(参加者表示名・集合情報)を含む
3. **POST /v1/latches/{id}/response**: 回答3値(yes/no/defer)。FOR UPDATE+条件付きUPDATEで直列化・
   409分岐(LATCH_EXPIRED / ALREADY_ANSWERED / LATCH_CLOSED)・422 VALIDATION_ERROR
4. **LATCH遷移の残り**: proposed→partial_accept→matched・rejected(no/defer)。latch_status_events同時挿入
   (回答起因はuser_idつき・システム起因はNULL)
5. **グループ回答**: 全員YES成立・部分成立禁止(D-06)。期限確定(期限時点で全員揃わなければexpired)は
   期限切れ側の409とws-2のsweeperが担い、本単位は「揃ったら成立」のみ
6. **競合クローズ**: matched成立と同一トランザクションで、参加Intentを含む他の開いているlatchesをcancelledへ
7. **Intent遷移**: active→matched(成立時)。matched→cancelled(解散)と参加Intent復帰(active/expired)は
   stage1の削除Event処理を拡張して実装(§2.7)
8. **Calibration記録**(M3-9): 回答確定時に作成・prediction(数値のみ+segment)・proposal_snapshot・actual_responses
9. **ws-7引継ぎの確定**: グループlatches昇格UPDATEへgroup_candidate_id列を追加(§2.10)

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | 回答API契約: request `{"response": "yes"}`・response 200 `{"latch": {"id", "status", "response_deadline", ...}}`。errors: 409 LATCH_EXPIRED(回答期限切れ) / 409 ALREADY_ANSWERED(二重回答) / 409 LATCH_CLOSED(競合クローズ後) / 422 VALIDATION_ERROR(response値が不正) | 05 §5 |
| 2 | `response`はyes / no / defer(見送り)の3値。deferはnoと同じrejected相当だが、回答種別はresponsesフィールドで区別保持し、再提案制御(D-07)の判定に使う | 05 §2・03 D-07 |
| 3 | 回答APIは単一トランザクション内の条件付きUPDATEで直列化。対象行をFOR UPDATEで排他してからstatus遷移を適用。UPDATEのWHERE条件はstatusのみでなく期限比較を含む: `status IN ('proposed','partial_accept') AND response_deadline > :now AND expires_at > :now` | 06 §6 |
| 4 | 影響行数0のときは処理を中止し、事前の読み取り結果に応じて409を返す(期限切れ=LATCH_EXPIRED・既回答=ALREADY_ANSWERED・競合クローズ後=LATCH_CLOSED) | 06 §6 |
| 5 | `:now`はアプリ層のClock.now()(DBのclock_timestamp()は使わない。テストのClock注入と時刻源を揃えるため) | 06 §6・04 §5(C2) |
| 6 | この条件により、期限切れバッチが遅延してstatusがまだproposedのままでも、期限直後の回答は受理されない(期限後成立の構造的排除) | 06 §6 |
| 7 | 競合クローズ: あるIntentのLATCHがmatchedになった時点で、そのIntentを含むcandidate・proposed・partial_acceptのlatchesをcancelledへ閉じる。回答処理と競合クローズは同一の直列化方式で競合しない(同一トランザクションで実施する根拠) | 06 §6・01 §8 |
| 8 | LATCH遷移表: proposed→partial_accept(参加者の一部がyes)/ partial_accept→matched(必要人数全員がyes)/ proposed・partial_accept→rejected(no回答またはdefer)/ proposed・partial_accept→expired(response_deadline・expires_at経過=expiry_sweeper・ws-2)/ matched→cancelled(参加Intentの削除)/ matched→completed(対象時刻経過) | 05 §6 |
| 9 | Intent遷移: active→matched(自身を含むLATCHの成立。同時に他の回答待ち提案は競合クローズ)/ matched→active・matched→expired(LATCH解散時。expires_at経過済みならexpired・経過前ならactiveへ復帰し再提案可能。復帰Intentは次の時間Bucket再評価の対象) | 05 §6 |
| 10 | D-06 成立確定条件: 集合全員のYESが必要(必要人数は集合サイズ\|S\|と一致)。部分成立(4人提案の3人YESで成立等)は行わない。回答期限の到来時に必要人数が揃っていなければexpiredで閉じる | 06 §8 |
| 11 | responsesの構造: `[{"user_id", "intent_id", "response": "yes\|no\|defer", "answered_at"}]` | 05 §2 |
| 12 | latch_status_events: 遷移を書くトランザクション(回答API・競合クローズ・expiry_sweeper・Layer 5のproposed遷移)と同時に挿入。from_statusはcandidate作成時のみNULL可。user_idは遷移の引き金となったユーザーで、システム起因(競合クローズ等)はNULL | 05 §2・06 §10 |
| 13 | Calibration: レコードは回答確定時に作成。predictionは数値のみ(would_a_accept_b / would_b_accept_a / MutualScore / L〔提案時のスコア〕とjev_5axis〔0〜1正規化値+confidence〕・provider / model)。proposal_snapshotはlatches.proposalと同形。actual_responsesはlatches.responsesと同形。matched=実際に成立したか。actual_attended / cancelled_afterは収集時(D-09・ws-4) | 07 §6・05 §2 |
| 14 | segment(仮説2): proposed時点の語彙一致/意味近接を表すフラグ。calibration_records.predictionへ`segment`キー("lexical"/"semantic")として記録。判定規則は両Intentのcategory_secondary(一方がnullの場合は当該Intentのsoft_constraintsの文言)の間で表層のトークン一致があればlexical、なければsemantic(方向性を持たない対称判定)。グループ提案では集約スコアのminとなるペアのフラグを適用 | 09 §2.3 |
| 15 | GET /v1/latches: 一覧(本人関与かつproposed以降)。cursorページネーション共通規定(limit既定20・上限100)。既定ソートは対象時刻(time_start)昇順・同点はcreated_at降順 | 05 §5 |
| 16 | GET /v1/latches/{id}: 詳細(成立後に解放情報を含む)。参加者のみ | 05 §5 |
| 17 | 403 FORBIDDEN=認可違反(所有者・参加者以外の操作。存在秘匿ではなく403)。404 NOT_FOUND=リソース不在。未登録JWT(User行なし)も同じ404 — intents系と同型 | 05 §5・M1 ws-3実装 |
| 18 | エラー形式は`{"error": {"code", "message", "details"}}`。クライアントはcodeで分岐 | 05 §5 |
| 19 | 削除Eventの処理: 当該Intentを含む候補・保留の無効化(Layerを経ない)。candidate(保留キュー)はlatches.status=candidateの行で表現される | 06 §1・§9・§10 |
| 20 | 解散時の参加Intent復帰: matched→cancelled(参加Intentの削除)の際、残る参加Intentはexpires_at経過済みならexpired・経過前ならactiveへ復帰。進行中の提案の解散(ブロック等)では参加Intentはもともとactiveのため復帰処理不要 | 06 §6 |
| 21 | 成立後の解放(03 §6): チャット・相手の表示名・最小限のプロフィール・集合情報(対象日時・場所の要約)・次アクションの提示。成立済みLATCHは対象時刻経過でcompleted(遷移自体はws-2のバッチ系が担う) | 03 §6 |
| 22 | 回答操作は[参加する]/[今回は見送る]/[辞退する]の3択。グループ提案では必要人数と現況(「あと2人の回答が必要」)を人数のみ表示(誰が回答済みかの特定は表示しない) | 03 §5 |
| 23 | 不成立の表示は「この提案は成立しませんでした」の一文に統一。相手の回答種別・不成立理由は開示しない — APIも種別・理由を通知しない | 03 §7 |
| 24 | ユーザーに見せるLATCH状態はproposed以降。candidateの詳細はシステム内部のデータとして画面に出さない(nearby_alsoの存在通知は候補をcandidateのままにする) | 03 §2 |
| 25 | match_candidates: UNIQUE(intent_a_id, intent_b_id, intent_a_version, intent_b_version)。評価はIntentバージョン組ごとに1レコード。jev_result(would_*・jev_5axis・provider・model)を保持 | 05 §2 |
| 26 | latches.scoreは提示時のスコア(L = H × MutualScore × C・H=1・C初期値1)。response_deadlineはcandidate作成時が暫定値・提示時にD-05式で再計算済み(ws-6実装) | 05 §2・06 §6 |
| 27 | 02#14(YES/NOを回答できる)はci環境割当。#15(双方YES成立)・#16(期限切れ処理)・#18(グループ成立)・#22(公開設定分岐)・#25(回答の記録)はstaging割当(ws-9 E2E) — 本単位はci統合試験で#14の本体を実施 | 10 §3 |
| 28 | D-05回答期限式(提示時再計算に使用済み・回答判定はresponse_deadline値との比較のみ): `min(max(通知時刻+15分, min(通知時刻+2時間, 対象開始時刻−60分)), latches.expires_at)` | 03 D-05・latch_calc.py実装済み |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

| 資産 | 本単位からの使い方 |
|---|---|
| `worker/matching/latch_calc.py` | `response_deadline`・`match_level`・`pair_target_time`・`RESPONSE_NO/DEFER`・`LATCH_C`をimportして再利用(再実装しない)。d07系は回答APIからは使わない(latch_engineがlatches.responsesを読んで判定する仕組みは完成済み) |
| `worker/matching/latch_engine.py` | 回答APIと同一の直列化方式の参照実装(`_SELECT_LATCH_FOR_UPDATE`・条件付きUPDATEの型)。本単位はlatch_engineを変更しない。`d07_history_inputs`は「answered_atつきdefer要素」を本単位が書く前提の実装になっている(latch_calc.py docstringに明記) |
| `worker/matching/proposal.py` | proposal構造(visibility分岐)は読み取りのみ。hidden_until_matchの提案はheadcount+match_levelのみ格納済みのため、成立後の解放情報はAPI側で再構築する(§2.8) |
| `worker/matching/group_engine.py` | §2.10で`_UPDATE_GROUP_LATCH_FOR_PROMOTION`へgroup_candidate_id列を追加。他は変更しない。member_scoresは`{"seed_id": str, "versions": {intent_id: version}}`(STATUS引継ぎ③) |
| `worker/matching/layer3.py` | `_bigrams`(文字bigram集合)をpublic化してsegment判定へ再利用(§2.6)。語彙計算のトークン化をパイプライン内で一つに保つ |
| `worker/stage1.py` | 削除Event処理の`_CLOSE_CANDIDATES`/`_CLOSE_GROUPS`と同一トランザクションへlatchesクローズ+解散復帰を追加(§2.7) |
| `intents/`(service・errors) | ドメイン構成(routes/service/store・errorsのhttp_status+code)の定型。404/403の挙動も踏襲 |
| `core/deps.py`・`core/clock.py` | `get_clock`(app.state.clock)。時刻参照はすべてClock経由(arch test強制) |
| `geo/service.py` | GeoService(engine)をAPI層で構築し、成立後のarea_name再構築に使用(§2.8) |
| alembic 0001〜0005 | 変更なし。calibration_records・latch_status_eventsは0001で作成済み |

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

- expiry_sweeper(60秒周期・SKIP LOCKED)・Intent期限切れバッチ・catch-upスキャン・リセットジョブ、
  および**matched→completed遷移**(対象時刻経過。sweeperと同一スケジューラの定期ジョブが自然な担い手)→ ws-2
- FCM・アプリ内通知API・D-08上限の通知面・回答期限の通知 → ws-3
- チャットAPI・実施自己申告(attendance・D-09) → ws-4
- ブロック成立後即時適用(D-23) → ws-5
- 削除・退会(calibration匿名化第一段を含む) → ws-6
- フロントエンド3画面・周辺2画面・02#13〜#23のstaging E2E整備 → ws-7〜ws-9
- 回答APIからの通知送信(matched成立通知・不成立通知)。成立・クローズの検知はlatch_status_eventsの
  挿入で観測点が確保されるため、通知はws-3がlatch_status_events/状態を参照して実装できる

## 2. 実装方式の選択と推奨

### 2.1 ドメイン構成 — 推奨: `src/latch/latches/`新設(intentsと同型の3層)

| 案 | 内容 | 判定 |
|---|---|---|
| **A(推奨)** | `src/latch/latches/`へroutes・service・store・errors・schemas・calibrationを新設。永続化はtext()生SQL(intents・latch_engineと同一規律) | intentsドメインと対称で、API系の変更がworker側に波及しない。latch_engine(Worker資産)は無変更のまま参照実装として残る |
| B | `worker/matching/`配下へ回答処理を置く | WorkerとAPIの責務境界面が濁る。回答APIは06 §6が「回答API」としてAPI層の直列化と規定しており、worker配下は不整合 |
| C | latches用のSQLAlchemy ORM entityを定義 | 既存コードは全量text()生SQL(asyncpgのUUID復元問題への対抗策_coerce_uuid規律も生SQL前提)。途中導入は二重規律になる |

### 2.2 回答処理のトランザクションとSQL — 推奨: FOR UPDATE→事前検査→条件付きUPDATE(06 §6の直接実装+参加Intent検査の厳格化)

単一トランザクション内で次の手順を走らせる。時刻`:now`は手順1のFOR UPDATE取得後にClock.now()で
採取する(latch_engine.try_promoteと同一規律。ミリ秒揺れで期限境界がtx内で動かないよう、検査と
UPDATEで同一の値を使い回す)。

```text
手順0: latch不在 → 404 NOT_FOUND(FOR UPDATEの行なしで判定)
手順1: SELECT ... FROM latches WHERE id = :id FOR UPDATE
       (id, status, intent_ids, responses, response_deadline, expires_at, score,
        proposal, group_candidate_id, created_at)
手順2: 事前検査(同一tx・行ロック保持後に分類材料を揃える)
  2a: 参加者判定 — intents WHERE id = ANY(intent_ids) AND user_id = :me を検索
      → 0行なら 403 FORBIDDEN(存在秘匿にせず403・引用#17)。参加Intentのidもここで確定
  2b: responses内に user_id = :me の要素があれば 409 ALREADY_ANSWERED
  2c: status not in ('proposed','partial_accept') のとき:
      response_deadline <= :now または expires_at <= :now → 409 LATCH_EXPIRED
      (期限切れバッチ遅延を前提とした06 §6の分類。statusがまだproposedでも期限後はこちら)
      それ以外(rejected/cancelled/matched/completed)→ 409 LATCH_CLOSED
      candidate(未提示)→ 409 LATCH_CLOSED(回答できる状態にない・引用#24)
手順3: 新status計算(純計算・§2.4)
  responseが no/defer → 'rejected'
  responseが yes → yes回答数(追記後)== len(intent_ids) なら 'matched'、でなければ 'partial_accept'
手順4: 条件付きUPDATE(1文・引用#3のWHEREに参加Intent検査を追加)
  UPDATE latches
  SET responses = responses || CAST(:item AS jsonb),
      status = :new_status
  WHERE id = :latch_id
    AND status IN ('proposed','partial_accept')
    AND response_deadline > :now
    AND expires_at > :now
    AND NOT EXISTS (
        SELECT 1 FROM intents i
        WHERE i.id = ANY(latches.intent_ids)
          AND i.status NOT IN ('active','paused'))
  RETURNING id, status
  :item = {"user_id", "intent_id", "response", "answered_at"(ISO・Clock由来)}
手順5: 影響行数0 → 手順2の分類で409を返す(引用#4)
手順6: 遷移の記録と後続(同一tx)
  6a: latch_status_events挿入(from_status=手順1のstatus・to_status=:new_status・user_id=:me)
  6b: :new_status = 'rejected' なら Calibration作成(§2.5)
  6c: :new_status = 'matched' なら:
      - 参加Intentをmatchedへ(§2.3)
      - 競合クローズ(§2.3)
      - Calibration作成(§2.5)
  6d: 'partial_accept' なら6aのみ(イベント挿入はpartial_accept遷移にも行う)
```

**参加Intent検査(手順4のEXISTS)について(承認事項2)。** 06 §6のWHERE条件はstatus+期限比較であり、これがdocsの
確定値である。追加のEXISTSはこの条件への**厳格化**(満たす集合を狭める方向)で、緩和ではない。入れる理由は
削除経路のレース防御 — stage1のlatchesクローズ(§2.7)はEvent駆動でAPIより遅れ得るため、検査なしでは
「削除済み・cancelledのIntentを含む提案への回答」がStage1処理前に受理され、成立してしまう窓がある。
EXISTSをUPDATE文に含めることでFOR UPDATE〜UPDATE間の隙間も同一文で封じられる。影響行数0時の分類は
「参加Intentがactive/pausedでない → 409 LATCH_CLOSED(参加Intent変化による競合・01 §8の不成立経路)」
とする(手順2cの分類優先度は期限切れが上)。

**latchesのupdated_at列は存在しない**(0001スキーマどおり・ws-6設計§2.3と同じ理由)ため、UPDATEで
書く列はresponses・statusの2列のみ。completed_atはcompleted遷移(ws-2)まで入れない。

**冪等性・リトラインバウンド。** 二重回答はALREADY_ANSWERED(手順2b)で冪等ガードされる。ネットワーク
再送で手順4のみが2回流れる場合も、2回目はWHEREのstatus条件(partial_accept/matched/rejected外)または
2bで排除される。

### 2.3 成立(matched)と競合クローズ・Intent遷移 — 推奨: 回答tx内の直接UPDATE(イベント経由にしない)

| 案 | 内容 | 判定 |
|---|---|---|
| **A(推奨)** | 手順6cで、回答と同一トランザクション内に Intent matched化+競合クローズを直接UPDATE | 引用#7「回答処理と競合クローズは同一の直列化方式」の文言どおり。成立がコミットされた瞬間にクローズも確定し、01 §8「成立した時点で閉じる」を満たす。latch_status_eventsも同一txで観測点が揃う |
| B | 成立時にイベントを発行しWorkerでクローズ | 非同期の遅延中に閉じたはずの提案へ回答が受理されうる(06 §6が排除したいレース)。イベント増も発生 |

```sql
-- Intent matched化(引用#9・pausedを含む=承認事項3)。戻り行数 != len(intent_ids) はtx失敗(503)とする
-- (手順4のEXISTSでactive/pausedは担保済みのため、通常は起きない防御)
UPDATE intents SET status = 'matched', updated_at = :now
 WHERE id = ANY(CAST(:ids AS uuid[])) AND status IN ('active','paused')
 RETURNING id

-- 競合クローズ対象の取得(行ロック→from_status確定のため先にSELECT)
SELECT id, status FROM latches
 WHERE id <> :self_id
   AND status IN ('candidate','proposed','partial_accept')
   AND intent_ids && CAST(:member_ids AS uuid[])
 FOR UPDATE

-- 各行へ(条件付きUPDATE+イベント挿入。from_statusはSELECT値)
UPDATE latches SET status = 'cancelled'
 WHERE id = :x AND status IN ('candidate','proposed','partial_accept') RETURNING id
INSERT INTO latch_status_events (latch_id, from_status, to_status, user_id, created_at)
 VALUES (:x, :from, 'cancelled', NULL, :now)   -- システム起因=引用#12
```

`intent_ids && :member_ids`(配列交差)で「参加Intentを含む」を判定。同時進行上限(1Intentあたり3件)と
Active上限(5件)から対象行数は高々十数行で、FOR UPDATEの一括取得は十分小さい。自己行(:self_id)は除外する。
競合クローズされたlatchへの回答は、以降の手順2cで409 LATCH_CLOSEDとなる。

### 2.4 グループ回答(全員YES成立・部分成立禁止)— D-06のAPI側実装

new_statusの計算は1対1とグループで同一の式で済む(必要人数=len(intent_ids)がD-06の確定値のため)。

- `yes_count = count(responses where response = 'yes')`(追記後の全要素)
- response=yes かつ `yes_count == len(intent_ids)` → matched
- response=yes かつ未満 → partial_accept
- response=no/defer → rejected(グループでも即閉じ・部分成立なし。「3人YESでも成立しない」の逆側:
  誰かのno/deferで即rejectedも05 §6遷移表どおり)

「あとN人の回答が必要」(引用#22)は `remaining = len(intent_ids) - yes_count` でAPI応答へ出す
(§2.8)。誰が回答済みかの特定はしない(回答者のuser_idは応答に含めない — 03 §5の情報最小化)。
期限到来時に揃わない場合のexpired遷移はws-2(本単位は期限切れ409のみ)。**期限確定(残時間の提示)**も
response_deadlineの値そのままであり、本単位で新たに計算するものはない(提示時に再計算済み・引用#28)。

### 2.5 Calibration記録 — 推奨: 回答tx内でrejected/matched時に作成(承認事項1)

| 案 | 作成タイミング | 判定 |
|---|---|---|
| **A(推奨)** | 回答APIのトランザクション内で、latchがrejected/matchedへ遷移したとき | 07 §6「レコードは回答確定時に作成」の直接の読み。actual_responses(全員の回答)・matched(実際に成立したか)はこの時点で確定値になる。評価(Brier・仮説2)に使える実測もここで確定する |
| B | 全終端遷移(expired/cancelled含む)で作成(ws-2・競合クローズ側にも実装が必要) | 文言「回答確定時」から離れる。expired/cancelledは回答によらない閉鎖であり、無回答を対象に含めるとD-09の欠測思想(無回答は補完しない)とも扱いが混ざる |

レコードの内容(07 §6・05 §2・引用#13・#14):

```text
latch_id      = 対象latch
intent_ids    = latches.intent_ids(ペア・グループ区別せず集合全体)
prediction    = {
    would_a_accept_b, would_b_accept_a,   # §2.11/§2.12で特定した評価行(minペア)から
    MutualScore,                            # min(would_a, would_b)
    L,                                      # latches.score(提示時のスコア・引用#26)
    jev_5axis,                              # 同評価行から(0〜1正規化値+confidence)
    provider, model,                        # 同評価行から(typesafe_jev/fallback_llm)
    segment,                                # "lexical"/"semantic"(§2.6)
  }
proposal_snapshot = latches.proposal(そのままコピー・同形保証)
actual_responses  = latches.responses(回答追記後の全量)
matched           = (new_status == 'matched')
actual_attended / cancelled_after = NULL(収集はws-4)
anonymized_at     = NULL(ws-6)
```

**1対1で「評価行が特定できない」場合の扱い。** §2.11の3段階特定でも全段失敗した場合(理論上は
latches.scoreと全評価行のlatch_scoreが不一致かつjev_result欠損の時のみ)、レコードを作成せずに
構造化ログ(`latch.calibration.missing`)へ記録して回答処理自体は成功させる。評価用の統計値が
1行欠ける損失と、回答成立を落とす損失を比べれば後者が大きい。この分岐はunit試験で再現不可能に
近いため、実装はログ1行の防御のみとする(実装しない分岐を量産しない)。

### 2.6 segment判定 — 推奨: layer3の文字bigramを再利用(表層トークン一致=bigram積集合が非空。承認事項4)

引用#14の「表層のトークン一致」に対し、日本語テキストで追加依存なしに決定的に走るトークン化として、
Layer 3語彙重なりと同じ文字bigram集合を採用する。パイプライン内でトークン化が二つに割れると
「Layer 3は語彙一致と判定したがsegmentはsemantic」のような不整合が生じうるため、既存実装を使う。

```python
# layer3.py: _bigrams を public化(エイリアス関数 bigrams を追加・_bigramsは削除せず両参照可)
def segment_texts(structured_data) -> tuple[str, ...]:
    """segment判定対象のテキスト組(09 §2.3)。
    category_secondary が非nullならその1要素、nullならsoft_constraints全文言
    (downgraded_from_ng を含む — 09は文言からの除外を指定していない)。"""
    # structured_data解析は layer3.soft_texts と同様の防御的パース
def classify_segment(texts_a, texts_b) -> str:
    """bigram積集合が非空 → 'lexical'、空 → 'semantic'。"""
```

対象テキストの選び方(どちらか一方がnullならその側だけsoft_constraintsに落ちる)は引用#14の規定どおり。
layer3.soft_texts(語彙重なり用・降格文言を除外)とsegment_texts(降格文言を含む)は**対象が違う別関数**と
する — Layer 3はD-04降格を計算対象外とする06 §4の確定値、segmentは09 §2.3の文言指定に従う。

### 2.7 削除経路のlatchesクローズと解散復帰 — 推奨: stage1の削除Event処理を拡張(承認事項7)

M2 ws-1の削除Event処理(stage1)はmatch_candidates・group_candidatesをclosedにしているが、
latchesには触れていない(当時latchesが存在しなかった)。引用#19「当該Intentを含む候補・**保留**の無効化」
の保留=latches.status=candidateは未実装のままのため、本単位で同じ処理ブロックへ追加する。

```text
_CLOSE_CANDIDATES / _CLOSE_GROUPS と同一トランザクションへ:
1) 開いているlatches(当該Intentを含む・status IN ('candidate','proposed','partial_accept'))
   → cancelled + latch_status_events(user_id=NULL)
2) matched行(当該Intentを含む)→ cancelled(解散・05 §6遷移表) + events(user_id=NULL)
   + 残る参加Intentの復帰: expires_at <= :now → expired / それ以外 → active(引用#9・#20)
   (復帰Intentは既存の30分Bucket再評価・catch-up(ws-2実装)で再評価対象になる)
```

| 案 | 内容 | 判定 |
|---|---|---|
| **A(推奨)** | stage1(Worker)の削除Event処理へ追加 | 候補無効化と同じ寿命・同じトランザクションに置く構成(M2以降の実装構成)を変えない。C8(APIはDB書き込み+Event発行に絞る)も維持される |
| B | intents DELETE APIのtx内で直接latchesを処理 | C8の文言上はDB書き込みのため違反ではないが、M1 ws-3以降「削除の波及はEvent経由でstage1が担う」構成を二重化し、APIのtxが長くなる |
| C | ws-2のexpiry_sweeperに任せる | 05 §6の遷移先(cancelled)と合わない(expiredになってしまう)上、最大60秒の遅延が入る |

matched行は削除されたIntent自身も含む点に注意(削除Intentはintents側で既にcancelled遷移済み —
M1実装。latches側はそれと区別せずcancelledへ閉じ、復帰処理は**残る**参加Intentのみに適用する)。
レース(stage1処理前の回答)は§2.2手順4のEXISTS検査で封じている。

### 2.8 GET系APIの応答設計

**GET /v1/latches(一覧)。** 05 §5の共通規定どおり。

```sql
SELECT l.id, l.status, l.response_deadline, l.expires_at, l.proposal, l.responses,
       l.created_at, l.completed_at, l.group_candidate_id,
       (SELECT max(i.time_start) FROM intents i WHERE i.id = ANY(l.intent_ids)) AS target_time
  FROM latches l
 WHERE l.status <> 'candidate'
   AND EXISTS (SELECT 1 FROM intents i
                WHERE i.id = ANY(l.intent_ids) AND i.user_id = CAST(:me AS uuid))
   AND (cursor条件)
 ORDER BY target_time ASC, l.created_at DESC, l.id ASC
 LIMIT :limit + 1
```

cursorはキーセット3キー(target_time ASC, created_at DESC, id ASC)をbase64エンコードした不透明文字列
(intentsのencode/decode_cursorパターンの拡張・比較はタプル順に展開)。`status <> 'candidate'`で
nearby候補・保留を除外し、rejected/expired/cancelled/completedも一覧に残す(03 §7「終了済みとして
扱われる」のため — 削除しない)。

items各要素(内部スコア生値は返さない — 一致度はproposal.match_levelで返す・03 §5):

```json
{"id", "status", "response_deadline", "expires_at", "created_at", "completed_at",
 "proposal",                       // latches.proposalをそのまま
 "is_group",                       // group_candidate_id != null
 "my_response",                    // 自分の回答種別(yes/no/defer)・未回答はnull
 "remaining_responses"}            // len(intent_ids) - yes数(matched以降は0)
```

**GET /v1/latches/{id}(詳細)。** 一覧要素に加え、statusがmatched/completedのときだけ解放情報を出す
(引用#16・#21・03 §6)。それ以外のstatusではparticipants等はnull(成立前の情報最小化・08 §2.2)。

```json
{"latch": { <一覧要素>,
  "participants": [ {"user_id", "display_name", "profile": {"bio"}} ],   // matched/completedのみ
  "time_summary": "10月1日 20:00",                                       // matched/completedのみ
  "area_name": "鹿児島市天文館" }}                                        // matched/completedのみ
```

- participants: latches.intent_ids → intents.user_id → users(display_name・profile)。表示名・プロフィールは
  成立後に解放する情報(03 §6)。bio以外のprofile内容はそのまま返す(現在のusers実装はbioのみ)
- time_summary: 対象開始時刻=max(time_start)(pair_target_timeと同型)をJST書式の文字列へ
  (引き継ぎ②の書式「JST YYYY-MM-DD HH:MM」。summary_onlyの提案はproposal.time_summaryと同一の値になる)
- area_name: 参加Intentのgeo_centerから(1対1=中点・グループ=平均点。latch_engine._area_name /
  group_engine._area_nameと同じ計算)でGeoService.reverse_geocode。hidden_until_matchの提案は
  proposalにarea_nameを持たないため、ここで初めて構築する。地物なしはnull
- チャットは別エンドポイント(ws-4)。本APIはチャットURL等の次アクション情報を持たない

認可は引用#17どおり: 不在=404 NOT_FOUND・参加者以外=403 FORBIDDEN。未登録JWTは404(intentsと同型)。

### 2.9 例外・エラー分類の対応表(service層の写像)

| 条件 | HTTP | code | 備考 |
|---|---|---|---|
| latch不在 / User行なし | 404 | NOT_FOUND | 手順0・2a |
| 参加者以外 | 403 | FORBIDDEN | 手順2a |
| response値がyes/no/defer外・欠落 | 422 | VALIDATION_ERROR | Pydanticのバリデーション |
| responses内に自分の回答あり | 409 | ALREADY_ANSWERED | 手順2b |
| 期限切れ(deadline/expires_at) | 409 | LATCH_EXPIRED | 手順2c・UPDATE影響0 |
| 終端済み(rejected/cancelled/matched/completed・未提示candidate)・参加Intentがactive/pausedでない | 409 | LATCH_CLOSED | 手順2c・手順4のEXISTS |
| DB・Redis等の依存障害 | 503 | DEPENDENCY_UNAVAILABLE | intentsの_wrap_unexpectedと同型 |

予期しない例外はintentsドメインと同じラップ方針(例外クラス名をログに残して503へ)。

### 2.10 ws-7引継ぎの確定 — グループlatches昇格UPDATEへgroup_candidate_id列を追加(承認事項8)

`_UPDATE_GROUP_LATCH_FOR_PROMOTION`(group_engine.py)はscore/proposal/response_deadlineの3列のみを
書くため、同一intent_idsの新しい世代の集合(gid')が既存の開いているlatches行へ昇格するとき
latches.group_candidate_idが旧gidのまま残る。本単位のprediction組み立て(§2.12)とgroup_candidatesの
状態整合(_mark_group_proposedは新gid'へproposedを書く)の両面で不整合が残るため、**昇格UPDATEへ
`group_candidate_id = CAST(:gid AS uuid)`を追加する**。観測の変化は「latches.group_candidate_idが
常に当該昇格時点の最新gidを指す」への改善のみ(既存試験にgroup_candidate_idの期待値があれば機械的追従)。

### 2.11 1対1の評価行特定 — 推奨: score一致を主とする3段階(承認事項5)

calibration.predictionのwould_*・jev_5axis・provider/modelはmatch_candidatesの評価行から取る。
latchesに1対1の評価行への参照カラムはなく(05 §2・group_candidate_idはグループのみ)、次で特定する。

```text
第1段: intent対(a<b)の評価行のうち latch_score = latches.score と一致する行
       (ORDER BY updated_at DESC LIMIT 1)
第2段: 第1段で取れない場合、updated_at <= latches.created_at の行のうち最新(jev_result IS NOT NULL)
第3段: 第2段でも取れない場合、同対の行のうち最新(世代不問)
```

第1段で実務上ほぼ確実な根拠: `_RECORD_SCORE`(latch_score計算)とlatches INSERT/昇格UPDATEは同一
トランザクション・同一`:now`で実行される(ws-6/7実装)ため、latches.scoreと同一のlatch_scoreを書いた行は
常に当該トランザクション由来。数値一致はNUMERIC同値で判定する(浮動小数の丸め問題は起きない)。
latchesへの評価スナップショット列追加(マイグレーション+05改版)は、この特定で足りるため採らない
(YAGNI。恒久スナップショットはcalibration_recordsが担う)。

### 2.12 グループのprediction — 推奨: 集約minペアの値をそのまま記録(承認事項6)

07 §6のprediction構造(would_a/would_b/MutualScore/L)は1対1の書き方であり、グループの格納形はdocsに
明示がない。09 §2.3が「グループ提案では集約スコアのminとなるペアのフラグを適用する」と定めているのと
同じ規則をprediction全体へ準用し、**MutualScore最小ペアのjev_result**をwould_*・jev_5axis・provider/
model・segmentの値として採る。Lだけlatches.score(=aggregate_score)を使う。minペアの特定手順:

```text
1. latches.group_candidate_id → group_candidates.member_scores.versions(intent_id→version)
2. 全ペア(a<b)の評価行をUNIQUE(intent_a_id, intent_b_id, intent_a_version, intent_b_version)
   (引用#25)で一意に取得
3. MutualScore = min(would_a, would_b) が最小のペア(同点は(a,b)辞書順最小で決定的に)を選ぶ
```

### 2.13 採用しないもの(YAGNIによる切り捨て一覧)

- latchesへの評価スナップショット列・match_candidates参照列(マイグレーション+05改版) — §2.11の特定で足りる
- 回答APIからの通知送信 — ws-3(§1.4)
- 回答API内でのexpired遷移 — 期限切れは409のみ・遷移はws-2のsweeper(引用#6・#8)
- 一覧のOFFSETページネーション — 05 §5はcursor方式で規定
- SQLAlchemy ORM導入 — §2.1案Cのとおり
- responses要素の更新・削除(回答の訂正) — 規定なし。attendanceだけが訂正不可規定を持つ(05 §5)が
  回答にも同じ構造を採る(書き込みは追記のみ)
- 一覧のindex追加(latches向け) — 本人関与判定はintents EXISTS経由でidx_intents_userが効く。
  規模が動き出してから(10 §4の性能試験)検討する

## 3. ファイル構成

### 3.1 作るもの(新規ファイル)

| ファイル | 内容 |
|---|---|
| `backend/src/latch/latches/__init__.py` | 空固有 — intentsと同型 |
| `backend/src/latch/latches/errors.py` | LatchesError基底+NotFoundError(404)/ForbiddenError(403)/LatchExpiredError・AlreadyAnsweredError・LatchClosedError(409系)/DependencyUnavailableError(503)。http_status+code属性(intents/errors.pyと同型) |
| `backend/src/latch/latches/schemas.py` | ResponseRequest(response: Literal["yes","no","defer"])・LatchSummaryOut・LatchDetailOut・LatchListResponse(items+next_cursor)。Pydantic |
| `backend/src/latch/latches/store.py` | text()生SQL: FOR UPDATE読取・回答の条件付きUPDATE(§2.2手順4)・競合クローズSELECT+UPDATE(§2.3)・Intent matched化・一覧(§2.8)・詳細行読取・participants読取・calibration INSERT・評価行特定(§2.11)・グループペア取得(§2.12)。asyncpg UUID復元は_coerce_uuid規律 |
| `backend/src/latch/latches/calibration.py` | 純計算寄り: prediction組み立て(1対1/グループ分岐)・segment_texts/classify_segment(§2.6)・calibration INSERT呼び出しの組み立て |
| `backend/src/latch/latches/service.py` | 手順0〜6の判定フロー・409分類(§2.9)・remaining/my_response算出・Clock採取・detailの解放情報構築(GeoService) |
| `backend/src/latch/latches/routes.py` | APIRouter(prefix="/v1/latches")。GET ""・GET "/{latch_id}"・POST "/{latch_id}/response"。get_latches_service依存(request.app.state経由・intentsと同型) |
| `backend/tests/unit/latches/test_latches_service.py` | 409分類・new_status算出・remaining・my_response(stub store) |
| `backend/tests/unit/latches/test_calibration.py` | segment判定(bigram・降格文言含む)・prediction組み立て・minペア選択・評価行3段階特定 |
| `backend/tests/unit/latches/test_latches_store_sql.py` | SQLのcompile検査(WHERE句・回答UPDATEの条件ピン。intents/test_store_sql.pyと同型) |
| `backend/tests/integration/test_latches_api.py` | §4.2の統合試験(実HTTP・compose常設DB) |

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更 |
|---|---|
| `backend/src/latch/main.py` | latches_routerを登録(既存4ルータに追記) |
| `backend/src/latch/worker/matching/group_engine.py` | `_UPDATE_GROUP_LATCH_FOR_PROMOTION`へgroup_candidate_id列を追加+呼び出し側へgid渡し(§2.10) |
| `backend/src/latch/worker/matching/layer3.py` | `_bigrams`のpublic化(`bigrams`関数を追加し内部からも同一実装を使用。§2.6) |
| `backend/src/latch/worker/stage1.py` | 削除Event処理へlatchesクローズ+matched解散・復帰を追加(§2.7) |
| `backend/tests/unit/test_worker_stage1.py` | stage1拡張のunit追従(latchesクローズの検査) |
| 既存試験の期待値 | group_candidate_id列追加に伴う機械的追従があれば(test_matching_groupengine.py等・計画書で確認) |

### 3.3 触らないもの(明示)

- `worker/matching/latch_engine.py`(回答を書かない。d07はlatches.responsesを読むだけで完成済み)
- `worker/matching/latch_calc.py`・`proposal.py`(importして使うのみ)
- `worker/`(stage1以外)・`events/`・`llm/`・`intents/`・`users/`・`auth/`・`geo/`・`ratelimit/`
- `backend/alembic/`(マイグレーション追加なし・head=0005不変)
- `frontend/`・`prototype/`・`docs/`(設計メモ本体以外)
- 通知系(notificationsテーブルへの新規書き込みはしない — M2 ws-6の先行書き込みが提案通知の実体)

## 4. テスト方針

### 4.1 unit(`make test`。スタブで決定的)

- **test_latches_service.py**: 手順2の409分類表を全分岐(期限切れ・既回答・終端済み・candidate・
  参加者外403・不在404)・new_status計算(1対1・3人・4人・yes/no/defer・残り人数)・remaining/my_response。
  storeはスタブ(行値を返すだけ)でSQLに依存しない
- **test_calibration.py**:
  - segment: category_secondary同士(bigram一致→lexical・不一致→semantic)・一方nullでsoft_constraints
    (降格文言を含むことの検査)・双方null(empty→semantic)
  - prediction組み立て: 1対1(評価行スタブ)・グループ(versions→ペア行スタブでminペア選択・同点辞書順)
  - 評価行3段階: 第1段ヒット・score不一致で第2段・jev_result欠損で第3段
- **test_latches_store_sql.py**: 回答UPDATEのWHERE句ピン(status IN句・期限2条件・NOT EXISTS参加Intent
  検査・responses||追記)・競合クローズの&&条件・一覧のEXISTS/ORDER BY(実dialect compile・
  M1 ws-3で確立した検査方式)
- **test_worker_stage1.py追従**: 削除Eventでlatches(開いている3状態)がcancelled+events・
  matched行がcancelled+残Intent復帰(expires_atで expired/active 分岐)
- 既存試験の期待値追従(group_engineの列追加)は機械的追従のみ

### 4.2 integration(`make test-ci`。compose常設DB・実Redis・API実HTTP)

test_matching_latchengine.pyの流儀を引き継ぐ(subjectプレフィックス `m3ws1-`・jev_resultは
match_candidatesへ直接UPDATE・embedding fixture直入れ・時間窓はnow+5日系で分離・teardownは
latch_status_events→calibration_records→notifications→latches→match_candidates→group_candidates→
intents→usersの順で完全削除・Redisはm3ws1-プレフィックス掃除)。

| # | 試験 | 確認事項 |
|---|---|---|
| 1 | 1対1の双方YES | A yes→200 partial_accept・B yes→200 matched。responses 2要素・answered_atはFakeClock由来 |
| 2 | noとdefer | no→rejected・別latchでdefer→rejected。responsesのresponse値が区別保持(no/defer) |
| 3 | 二重回答 | 同一ユーザーの2回目→409 ALREADY_ANSWERED |
| 4 | 期限切れ | FakeClockをresponse_deadline経過へ進める→409 LATCH_EXPIRED(statusはproposedのまま・遷移しない) |
| 5 | 競合クローズ | 1ユーザーのIntentを含む2つのlatchをfixtureで用意・片方を成立→他方はcancelled・イベント(from/to・user_id=NULL)・成立側の回答は200 |
| 6 | クローズ後の回答 | cancelled済みlatchへの回答→409 LATCH_CLOSED |
| 7 | 認可・不在 | 参加者以外→403・存在しないid→404・未登録JWT→404 |
| 8 | 422 | response="maybe"→422 VALIDATION_ERROR |
| 9 | グループ3人 | 2人yes→partial_accept(remaining=1)・3人目yes→matched。途中でno→rejected(部分成立なし) |
| 10 | グループ4人・group_candidate_id昇格 | 4人集合latchのfixture・全員YES→matched・latches.group_candidate_idが期待gid |
| 11 | Intent遷移・競合 | 成立時 全参加Intentがmatched・他latchesがcancelled |
| 12 | latch_status_events | 回答起因(from_status/to_status/user_id=回答者)・競合クローズ起因(user_id=NULL)の全行 |
| 13 | Calibration | matched/rejected各1件: prediction(would_*・MutualScore・L=latches.score・segment・provider)・proposal_snapshot=proposal・actual_responses・matched真偽。グループはminペアの値 |
| 14 | segment | category_secondaryが一致する組(「焼肉」/「焼肉」)→lexical・不一致(「焼肉」/「イタリアン」)→semantic の2ケースをlatches fixtureで |
| 15 | GET一覧 | candidate除外(nearby候補が出ない)・ソート(対象時刻昇順・同点created_at降順)・cursor改頁(2頁目以降)・my_response・remaining・limit既定20 |
| 16 | GET詳細 | proposed: participants等がnull(hidden_until_matchで条件サマリなし・match_levelは出る)・matched: participants(表示名)・time_summary(JST書式)・area_name(実データgeo)解放・参加者以外403 |
| 17 | 削除経路(stage1拡張) | 参加Intent DELETE→開いているlatchesがcancelled・matched latch DELETE→cancelled+残Intentがactive復帰(expires_at経過側はexpired) |

02#14(YES/NOを回答できる・ci割当)はこのうち1〜3・6〜8で本体を検証する。#15/#16/#18/#22/#25の
staging E2Eはws-9(スコープ外・§1.4)。

### 4.3 検証手順(報告書への明記用)

- マイグレーション追加なしのため、共有ci-dbの運用ルール1・2の制約を受けない。test-ciは通常どおり実行可
- 本単位完了後の並走(ws-2∥ws-3∥ws-4)開始前に `docker compose build api` を含むtest-ci再実行で
  mainの状態を確定させる(運用ルール4)
- テストファイルのbasename一意(運用ルール5): `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることを報告書に記録

## 5. 未解決の論点(supervisor承認事項・G3時確認候補)

1. **Calibration作成タイミングの解釈**(§2.5案A)。07 §6「回答確定時に作成」を「回答APIの処理で
   latchがrejected/matchedへ遷移した時」と読み、expired(sweeper)・cancelled(競合クローズされた側・
   削除)では作らない。partial_accept→expiredの部分回答は記録から漏れる。全終端で作る案Bは
   文言からの離れとws-2への波及が代償
2. **回答UPDATEのWHEREへ参加Intent検査(NOT EXISTS)を追加**(§2.2)。06 §6の確定値
   (status+期限)への厳格化で、削除レース防御が目的。06改版ではなく実装上の追加と位置づける
3. **paused参加Intentを含む成立を許容**(active・paused→matched)。05 §6の遷移表はactive→matchedのみ
   明記だが、expired/cancelled行は「active・paused」表記であり、matched行のみがactive限定とは読みにくい。
   提示後にpauseされたIntentの提案も回答可能にする(止めるのは表示ではなくWorker側の再評価クローズ)
4. **segmentのトークン化=layer3の文字bigram再利用・soft_constraintsは降格文言(downgraded_from_ng)を
   含む**(§2.6)。09 §2.3は「表層のトークン一致」の実装方式と降格文言の除外を指定していない。
   降格を含める根拠は「文言」の字義どおり
5. **1対1の評価行特定=score一致3段階**(§2.11)。latchesへのスナップショット列追加(05改版)はしない。
   第3段まで失敗した場合はレコードを作らず構造化ログのみ(§2.5)
6. **グループのprediction=minペアの値**(§2.12)。09 §2.3のsegment規定をprediction全体へ準用する読み。
   would_a/would_b・jev_5axis・provider/model・segmentともminペアの値で一貫させる
7. **stage1へのlatchesクローズ+解散復帰追加**(§2.7案A)。06 §1「候補・保留の無効化」の保留
   (latches.status=candidate)がM2 ws-1で未実装だったことの追随。API直接処理(案B)は採らず
   Event駆動の既存構成を維持する
8. **group_engine昇格UPDATEへgroup_candidate_id列追加**(§2.10・ws-7引継ぎの確定)。観測不変の
   整合改善。既存試験の期待値追従があれば機械的追従とする
