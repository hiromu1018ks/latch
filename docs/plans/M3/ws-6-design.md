# M3 ws-6 設計 — 削除・退会(FR-19/FR-22・D-13第一段)

作成: 2026-10-01(agent1)。参照仕様: 12 M3-8 / 08 §2.5・D-13 / 05 §2・§5〜§6 / 06 §6・§9 / 01 §21 / 09 §2.2(D-09) / 03 §5(D-20)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

FR-19/FR-22の削除範囲を物理削除で実装し、calibration_records匿名化の第一段(D-13)を置く。現行の削除経路(M1 ws-3以降)はIntent行のcancelled遷移と候補のclosed化という論理止めであり、08 §2.5が要求する「該当Intent行(raw_text・structured_data・embeddingを含む)の削除」「候補の処理済み含む全削除」には届いていない。この差分を埋めるのが本単位である。具体的には次の5点。

1. **削除カスケードの実体**(`intents/deletion.py`新設): 単発削除・退会の両経路から呼ぶ、1Intent分の物理削除関数(候補DELETE→latchesクローズ→calibration匿名化→Intent行DELETE。§2.1)
2. **単発Intent削除の物理削除化**: stage1の削除Event処理をclosed化から物理削除へ差し替え(ws-1 §2.7のEvent経由構成は維持)。あわせてDELETE APIの受理statusを全statusへ拡張(matchedが受理外のままではFR-19の経路が不通。§2.2)
3. **退会API**(`DELETE /v1/users/me`新設): 全Intent処理+当該ユーザーのmessages・notifications削除+表示名置換+認証紐付け切断を1トランザクションで同期実行(§2.3〜2.4)
4. **30日定期削除ジョブ**(`worker/retention.py`新設): 両候補テーブルの評価確定から30日経過行を日次削除(§2.5)
5. **calibration_records匿名化第一段**(D-13): ID系NULL化+latches.responses内user_id除去。anonymized_atは月次バッチ(第二段)で立てる(§2.6)

ws-5からの引継ぎ(退会時のblocks/reports行の扱い)には§2.7で答える。マイグレーションは追加しない(必要な列・制約はすべて0001〜0006に存在。§2.8)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | ユーザーによるIntent削除・退会時の削除範囲: ①該当Intent行(raw_text・structured_data・embeddingを含む。pgvectorの値は同カラムなので同時に消える) ②当該Intentを含むすべてのmatch_candidates・group_candidates(**処理済みを含む**。evaluated・skipped・closedを問わず削除) ③当該Intentを含む回答待ちのlatches(cancelledで閉じる) ④当該ユーザーのmessages・notifications ⑤calibration_records該当行の匿名化(ID系の除去と粒度低下。処理方式はD-13) ⑥退会時は上記全てに加え、関与したlatches内の表示名を「退会したユーザー」へ置換する(相手側の履歴は残すが、個人との紐付きを切る) | 08 §2.5 |
| 2 | FR-19: matched済みLATCHの参加Intentが削除された場合も当該LATCHをcancelledで閉じる。チャットは読み取り専用化(新規送信不可・閲覧可能。D-23と同じ線)、残った参加者への表示は「この提案は成立しませんでした」の一文のみ(削除したのが誰か・理由は開示しない)。実施自己申告(D-09)の通知はcancelled LATCHには送らない。退会(全Intent処理)と単発のIntent削除でこの扱いは一致する。解散時に残る参加Intentは、expires_at経過済みならexpired、経過前ならactiveへ復帰し再提案可能(次の時間Bucket再評価の対象) | 08 §2.5 |
| 3 | FR-22保持期間: 削除要求がない場合も、match_candidates・group_candidatesのレコードは**評価確定から30日で定期削除する**。Intentの寿命は数日(03 D-19)であり、09の品質調査に必要な値は回答確定時にcalibration_recordsへ移っている。保持期間を設けない場合、削除されたユーザーの条件由来の判定値が相手側レコードに恒久残存する経路となる | 08 §2.5 |
| 4 | D-13第一段: **匿名化は月次バッチで実行し、退会時の即時個別変換は行わない。退会・削除時点でID系(user_id・latch_id・intent_ids)をNULL化する(第一段)。anonymized_atはバッチ実行時に立てる**(calibration_recordsのCHECK「anonymized_at NOT NULLならID系NULL」と矛盾しない) | 08 D-13 |
| 5 | D-13バッチ実行時の変換内容(第二段・運用設計): (1)時刻系(actual_responses内の回答時刻・created_at等)を日単位へ丸める (2)proposal_snapshotの粒度低下(時間帯は昼/夕/夜のビン・地域名は市区町村単位・category_secondaryと対象日時除去・人数はビン化) (3)actual_responsesは回答種別と丸め済み日付のみ保持。「バッチ実装の詳細は運用設計」 | 08 D-13・08 §6 |
| 6 | calibration_records: anonymized_atはNULL=未処理。NOT NULLならlatch_id・intent_idsはNULL(CHECKで強制)。actual_responsesは「匿名化でuser_idを除去し、回答種別と時刻は残す」。latch_id・intent_idsは匿名化(D-13)でNULLへ | 05 §2・0001 |
| 7 | latches・responses・messagesはユーザーの履歴として維持する(削除範囲は引用#1のとおり。退会時の表示名置換・ID除去は適用する) | 08 §2.5 |
| 8 | LATCH遷移: matched→cancelled(参加Intentの削除)。残る参加IntentはIntent側遷移表の復帰規則に従う | 05 §6 |
| 9 | Intent遷移: matched→active / matched→expired(参加Intentを含むLATCHが解散した場合。expires_at経過済みならexpiredへ・経過前ならactiveへ復帰。復帰Intentは次の時間Bucket再評価の対象) | 05 §6 |
| 10 | 削除済みIntentへの参照Eventは正当な遅延Eventとしてstatus=processedで破棄し(理由をpayloadに記録)、quarantinedにはしない。match_events.source_intent_idはFKなし(削除済みIntentへの遅延Event保存の担保) | 05 §2・06 §9・0001 |
| 11 | D-09実施自己申告の通知はcancelled LATCHには送らない(構造担保: sweeperのcompleted化はstatus='matched'行のみ対象) | 09 §2.2・ws-2実装 |
| 12 | 不成立の理由は「この提案は成立しませんでした」の一文に統一(03 D-20の二値化と同一)。matched済みLATCHの参加Intent削除時も残った参加者への表示はこの一文のみ | 08 §2.5・03 §5 |
| 13 | セッションの失効(ブロック・退会の即時反映)はRedisの失効リストで行う | 08 §5.3・04 |
| 14 | M1 ws-3の引継ぎ: DELETE /v1/intents/{id}はcancelled遷移+deleted Eventのみ(「行の物理削除は行わない」。matched・expired・cancelled行へのDELETEは422)。「08 §2.5『該当Intent行…を削除する』は物理削除とも読める」という解釈の緊張を認識し、完全実装(候補削除・latchesクローズ・30日定期削除)はM3-8へ引継ぎと明記 | M1 ws-3 design §2.8・§6-3 |
| 15 | 12 M3-8のスコープ文言: 「削除・退会: FR-19/FR-22の削除範囲(候補を処理済み含め全削除・30日定期削除)、calibration_records匿名化の第一段(08 第2.5節・D-13)」 | 12 §3 M3-8 |
| 16 | ws-5引継ぎ: 退会時のblocks/reports行の扱い(削除範囲)はws-5では扱っていない | ws-5-report引継ぎ欄 |
| 17 | users: display_name・auth_provider(CHECK IN ('google','apple'))・auth_subjectはNOT NULL。UNIQUE(auth_provider, auth_subject)。FKでusersを参照するテーブルはblocks・reports・notifications・messages(intents.user_id含む) — users行の物理削除はこれら全行の削除を要求する | 0001 |
| 18 | FKの連鎖(いずれもON DELETE句なし=RESTRICT): match_candidates→intents、latches→group_candidates、calibration_records→latches。削除順序の制約になる | 0001 |
| 19 | C8: Intent保存APIは同期LLM非依存(DB書き込み+Event発行に絞る)。C9: 回答API・競合クローズ・expiry_sweeperは同一の直列化方式(FOR UPDATE+同一UPDATE条件) | 12 §2 |

### 1.3 既存実装との差分(=本単位のスコープ)

| # | 既存資産 | 位置 | 現状 | 08 §2.5との差分 → 本単位の扱い |
|---|---|---|---|---|
| a | `IntentService.delete` | `intents/service.py:758` | cancelled遷移(version不変)+EVENT_DELETED発行。allowed_from=(draft, active, paused)でmatched・expired・cancelledは422 | **matchedが受理外のままではFR-19(参加Intent削除)のAPI経路が不通**。expiredもraw_textが残存し続ける。→ allowed_fromを全statusへ拡張+「物理削除しない」コメントの更新(§2.2) |
| b | stage1の削除Event処理(`_CLOSE_CANDIDATES`/`_CLOSE_GROUPS`) | `worker/stage1.py:95,101,331-335` | 候補をstatus='closed'へUPDATE(行は残置) | 処理済み含む**物理削除**が要求(引用#1-②)。→ `cascade_delete_intent`呼び出しへ差し替え(§2.1/2.2) |
| c | `close_latches_on_delete`(FR-19のlatches側) | `worker/stage1.py:455` | 開いているlatches(candidate/proposed/partial_accept)→cancelled+events、matched→cancelled+events+残存Intent復帰。イベントuser_id=NULL(システム起因) | 要求(引用#2・#8・#9)を満たす。→ **中身は不変**のまま`intents/deletion.py`へ移設(単発・退会の両経路から利用) |
| d | 退会API | なし(05 §5にも規定なし) | — | 全Intent処理+messages/notifications削除+表示名置換(引用#1-④⑥)。→ `DELETE /v1/users/me`新設(§2.3〜2.4) |
| e | 30日定期削除ジョブ | なし | — | 両候補テーブルの30日削除(引用#3)。→ `RetentionJob`新設(§2.5) |
| f | calibration匿名化 | なし(calibration_recordsはws-1で作成・ws-4でattendance更新) | — | 第一段のID系NULL化(引用#4・#6)。→ 匿名化SQLをcascade内に新設(§2.6) |
| g | 退会時のblocks/reports | 未扱い(ws-5引継ぎ・引用#16) | — | 削除リスト(引用#1)に含まれない。→ **残置**(根拠は§2.7) |
| h | D-09通知のcancelled除外 | `worker/sweeper.py:297`(_complete_latch) | completed化はstatus='matched'行のみ対象=cancelled LATCHへの通知は構造的に発生しない | 要求(引用#11)を既に満たす。→ 触らない(確認のみ) |

### 1.4 スコープ外(後続単位・領域へ渡すもの。本単位では作らない)

- D-13第二段(月次バッチ: 時刻の日単位丸め・proposal_snapshotの粒度低下・actual_responsesの丸め済み日付化・anonymized_at設定) → 運用設計(08 §6が「バッチ実装の詳細は運用設計」と明記。引用#5)
- 「この提案は成立しませんでした」の一文表示・「退会したユーザー」表示・ブロック管理画面上の退会者表示 → ws-7/ws-8(フロント。引用#12)
- match_eventsの定期削除・容量管理 → 08 §2.5の対象は両候補テーブルのみ(引用#3)。恒久蓄積は将来の容量課題として記録するが実装しない(§2.8)
- 通報への運用対応(警告・アカウント停止)・アカウント停止の運用UI → M4+の運用面(ws-5と同線)
- friendships(将来版)

## 2. 実装方式の選択と推奨

### 2.1 削除カスケードの実体 — intents/deletion.pyへ一元化(FK順序どおりの5段)

単発削除と退会の両方が「1Intent分の完全削除」を必要とする。この実体を`worker/stage1.py`に置いたままにするとusers(退会API)からworkerへの依存が生じるため、`src/latch/intents/deletion.py`(store層のSQL定数+純関数。intents配下だがintents.serviceへのimportは持たない)へ新設し、両経路から呼ぶ。FK依存(引用#18)が削除順序を決める。

```python
# intents/deletion.py(要点。SQL全文は計画書へ)

_DELETE_MATCH_CANDIDATES = text("""
    DELETE FROM match_candidates
     WHERE intent_a_id = CAST(:intent_id AS uuid)
        OR intent_b_id = CAST(:intent_id AS uuid)
""")  # 全status・処理済み含む(FR-22・引用#1-②)

_DETACH_GROUP_CANDIDATES = text("""
    UPDATE latches SET group_candidate_id = NULL
     WHERE group_candidate_id IN (
         SELECT id FROM group_candidates
          WHERE CAST(:intent_id AS uuid) = ANY(intent_ids))
""")  # latches→group_candidates FK(RESTRICT)の解消。
      # 閉じたlatchesのgroup_candidate_idは再評価で使わないためNULL化してよい

_DELETE_GROUP_CANDIDATES = text("""
    DELETE FROM group_candidates
     WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
""")

# _SELECT/_CANCEL/_RESTORE系とclose_latches_on_delete本体は
# worker/stage1.py:455から移設(中身不変)。併せて
# _CLOSE_CANDIDATES/_CLOSE_GROUPS(95,101行)は削除される

_ANONYMIZE_CALIBRATION = text("""
    UPDATE calibration_records
       SET latch_id = NULL,
           intent_ids = NULL,
           actual_responses = COALESCE((
               SELECT jsonb_agg(e - 'user_id' ORDER BY ord)
                 FROM jsonb_array_elements(actual_responses)
                      WITH ORDINALITY AS t(e, ord)
           ), '[]'::jsonb),
           updated_at = CAST(:now AS timestamptz)
     WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
       AND latch_id IS NOT NULL
""")  # D-13第一段(§2.6)。latch_id IS NOT NULL=未匿名化行のみ(冪等ガード)

_DELETE_INTENT = text("""
    DELETE FROM intents WHERE id = CAST(:intent_id AS uuid)
""")  # raw_text・structured_data・embedding(pgvector)ごと消える(引用#1-①)

async def cascade_delete_intent(conn, intent_id: uuid.UUID, now) -> None:
    """1Intent分の削除カスケード。呼び出し元のトランザクションに乗る。
    順序=FK依存: ①1対1候補 ②group FK解消 ③group候補 ④latchesクローズ
    (close_latches_on_delete・FR-19) ⑤calibration匿名化 ⑥Intent行。"""
```

呼び出し側はトランザクションを開いて渡す(stage1の`_process_once`と退会API)。関数自身はtxを開かない(C9の直列化方式=呼び出し元のFOR UPDATE・条件付きUPDATEと同じ寿命で動く)。

**移設に伴う既存試験の追従**: `test_worker_stage1.py`の削除Event試験(336行・362行)はclosed化を期待しているため物理削除期待へ書き換える(機械的追従)。`tests/integration/test_matching_groupengine.py`がEVENT_DELETEDを参照している場合は同様に追従する(実装時に機械確認)。

### 2.2 単発Intent削除 — Event経由を維持し、stage1の処理だけ物理削除化+受理status拡張

| 案 | 内容 | 判定 |
|---|---|---|
| **A(推奨)** | APIは現状どおりcancelled遷移+EVENT_DELETED発行。stage1の`EVENT_DELETED`処理を`cascade_delete_intent`呼び出しへ差し替え | ws-1 §2.7の承認済み構成(「削除の波及はEvent経由でstage1が担う」)を維持したまま物理削除へ届く。API・Event発行系は無変更 |
| B | DELETE APIのtx内でカスケードを直接実行 | ws-1 §2.7でB案として既に否決された構成(「M1 ws-3以降の構成を二重化し、APIのtxが長くなる」)の再提出になる。却下 |

A案の動作: DELETE APIはcancelled遷移(version不変)でEventを発行 → stage1がversion検査を通過し(現行versionと一致)カスケードを実行 → Intent行が消える。後続の遅延Event(created/updated等)は既存の「intent_not_found」破棄経路(引用#10)に載る。Event処理はdebounce対象外の即時処理(06 §9)のため、削除完了は通常数秒以内。

**受理statusの拡張**(§1.3-a)。`delete()`のallowed_fromを`("draft","active","paused")`から**全status**(`"draft","active","paused","matched","expired","cancelled"`)へ拡張する。

- matched: FR-19(引用#2)は「matched済みLATCHの参加Intentが削除された場合」を規定する=matched Intentの削除要求を受け付けることが前提。現行の422はM1時点の遷移表解釈(引用#14)であり、08 §2.5の実装単位である本単位で解消する
- expired: 08 §2.5の削除リストはstatusを問わない(期限切れIntentのraw_textも「預けた意思」であり、削除要求に応じて消す。放置すればexpired行のraw_textが恒久残存する)
- cancelled: 削除済み行への再削除要求。cascadeの再実行は全体が冪等(候補DELETE=0行・latches条件付きUPDATE=影響0)で、Event発行は`insert_match_event`のON CONFLICT DO NOTHINGで同一3点組の重複を挿入しない。応答204のまま冪等
- cancelled遷移は「Event処理までの一時標識」として残す(update_statusのexpected_status条件・version不変の既存構造を変えない。matched→cancelled・expired→cancelledは05 §6遷移表に行がないため、追記をdocs改版候補として§5へ記録)

### 2.3 退会 — DELETE /v1/users/me新設・APIトランザクション内で同期完結

| 案 | 内容 | 判定 |
|---|---|---|
| **A(推奨)** | 退会APIのtx内で全Intentのカスケード+ユーザー単位処理(messages/notifications削除・表示名置換・認証紐付け切断)まで実行。Eventは発行しない | **退会の原子性・確実性**。応答204の時点で生データ(Intent行・候補・messages・notifications)が消えている。Workerの稼働状態に依存しない |
| B | 全Intentをcancelled遷移+EVENT_DELETED×N発行し、物理削除はEvent処理へ委ねる(単発削除と完全に同一経路) | Worker停止中・隔離時に退会ユーザーのraw_textが残存し続ける窓が開く。08 §1「ユーザーが削除を求めたとき、預けた意思はシステムから消える」の確実性で劣る。却下 |

2経路(単発=Event経由・退会=同期)になるが、削除の実体は§2.1の`cascade_delete_intent`1本に一元化しており、ロジックの二重化はない。退会は「アカウントの終了」という性質上、応答時完結が最優先で、ws-1 §2.7のB案否決理由(単発削除における構成二重化)とは状況が異なる。

API仕様(05 §5への規定は現状なし。§5にdocs改版候補として記録):

```text
DELETE /v1/users/me — 退会
request:  {} (AuthorizationヘッダーのJWTが対象)
response: 204
errors:   401 UNAUTHENTICATED
```

処理手順(1トランザクション):

```python
# users/service.py に delete_account を追加(要点)
async def delete_account(self, *, claims: AccessTokenClaims) -> None:
    user = await self._require_user(claims.auth_provider, claims.auth_subject)
    now = self._clock.now()
    async with self._uow() as conn:
        intent_ids = (await conn.execute(
            _SELECT_ALL_INTENT_IDS, {"user_id": user.id})).fetchall()  # 全status
        for (iid,) in intent_ids:
            await cascade_delete_intent(conn, _coerce(iid), now)      # §2.1
        await conn.execute(_DELETE_MESSAGES, {"user_id": user.id, ...})
        await conn.execute(_DELETE_NOTIFICATIONS, {"user_id": user.id, ...})
        await conn.execute(_ANONYMIZE_USER, {"user_id": user.id, "now": now})
    # コミット後にセッション失効(引用#13。失効が先でも無害・logoutと同じ順序)
    await self._sessions.revoke_access(jti=claims.jti, ...)
    await self._sessions.revoke_family(sid=claims.sid)
```

```sql
-- ユーザー単位のSQL(確定値)
_SELECT_ALL_INTENT_IDS:
  SELECT id FROM intents WHERE user_id = CAST(:user_id AS uuid) ORDER BY id
_DELETE_MESSAGES:
  DELETE FROM messages WHERE sender_id = CAST(:user_id AS uuid)
_DELETE_NOTIFICATIONS:
  DELETE FROM notifications WHERE user_id = CAST(:user_id AS uuid)
_ANONYMIZE_USER:
  UPDATE users
     SET display_name = '退会したユーザー',
         profile = '{}'::jsonb,
         auth_subject = 'deleted:' || id::text,
         updated_at = CAST(:now AS timestamptz)
   WHERE id = CAST(:user_id AS uuid)
```

- **messagesはsender_id一致行のみ削除**(引用#1-④「当該ユーザーのmessages」)。残る参加者の送信行は履歴として維持(引用#7)。「当該ユーザーの」の範囲を送信行と読む解釈は§5へ記録
- **表示名置換はusers行へのUPDATEで達成する**: latches詳細APIは`users.display_name`をjoin参照している(`latches/store.py:255`)ため、users行を置換すれば関与したlatches全ての表示が「退会したユーザー」になる。latches行を個別に書き換える処理は不要。profile(bio)も同じjoinで相手に見えるため`'{}'`へクリアする(「個人との紐付きを切る」の趣旨)
- **認証紐付け切断はauth_subjectの書き換えで達成する**(§2.4)
- 退会時のFR-19(matched LATCH解散・残存Intent復帰)は`cascade_delete_intent`内の`close_latches_on_delete`が担う(引用#2「退会と単発で扱いは一致」)。退会ユーザーのIntentは全て消えるため復帰対象は常に他参加者のみ
- 冪等性: 2回目の呼び出しはintent_ids空・各DELETE=0行・users UPDATEは同一値で無害。204を返す
- トランザクション規模: ベータ規模(1ユーザーのIntent=高々数十+候補・メッセージ数百行)で1txに収まる

**users/serviceへのDI追加**: `SessionStore`(auth/sessions.py・失効操作)と`uow`を注入する。main.py lifespanの組み立てを1行更新(§3.2)。

### 2.4 退会後のusers行 — 残置+認証紐付け切断(auth_subject切替。マイグレーションなし)

| 案 | 内容 | 判定 |
|---|---|---|
| **A(推奨)** | users行は残置し`auth_subject = 'deleted:' || id`へ書き換える | users行の物理削除はFK(blocks・reports・messages・notifications・intentsが参照・引用#17)で不可能。auth/tokenのUser照合(auth_provider+auth_subject一致)が切れるため、**退会済みJWTはUser不在→401、同一IdPでの再ログインは「新規ユーザー」として初回登録フローに落ちる**。auth/service.pyの照合SQLを一切変えずに済む。UNIQUE(provider, subject)とも衝突しない(idは一意) |
| B | usersへdeleted_at列を追加(マイグレーション0007)し照合に`deleted_at IS NULL`条件を足す | 列が増え・auth/service.pyの照合・`POST /v1/users`の409 USER_EXISTS判定も触ることになる。得られる明示性に対して変更が広い |

birth_date・location_preferences等の残存列の扱いは§5(オーナー確認候補)へ置く。実装は案Aで確定する。

### 2.5 30日定期削除 — RetentionJob新設(ResetJobと同型のJST 0時発火・独立task)

| 案 | 内容 | 判定 |
|---|---|---|
| **A(推奨)** | `worker/retention.py`に`RetentionJob`を新設。ResetJobと同型の`next_jst_midnight`待機runループ+失敗時retry_sec再試行。worker/main.pyでResetJobと並ぶ独立task(engineのみで動く・redis不要) | 責務の分離(リセット=カウンタ解放、リテンション=行削除)とunit試験の独立性。ResetJobのrun_once契約(cost_store掃除+drain)・既存試験を一切壊さない |
| B | ResetJob.run_onceへ削除処理を追記(日次定期処理の1ジョブ化) | task数は増えないが、cost_store掃除とDB削除の失敗が同一retryに巻き込まれ、試験も双方のsetupを要求する。ws-2 §2.1の「単一ジョブ管理」要求(06 §6・§9由来)は60秒系のlatches/Intent期限切れ/catch-upについてであり、30日削除はその文言の対象外 |
| C | 60秒系(ReevalRunner)のtickに載せる | 60秒周期で30日境界を検査する無駄。日次で十分な処理をパイプラインの遅延要因に加えることになる |

```sql
-- RetentionJob.run_once(cutoff = now - 30日。batch_limit=500の窓で空になるまで)
-- ① FK解消(latches→group_candidates)
UPDATE latches SET group_candidate_id = NULL
 WHERE group_candidate_id IN (
     SELECT id FROM group_candidates
      WHERE updated_at < CAST(:cutoff AS timestamptz)
      ORDER BY id LIMIT :batch_limit)
-- ② group_candidates
DELETE FROM group_candidates
 WHERE id IN (SELECT id FROM group_candidates
               WHERE updated_at < CAST(:cutoff AS timestamptz)
               ORDER BY id LIMIT :batch_limit)
-- ③ match_candidates
DELETE FROM match_candidates
 WHERE id IN (SELECT id FROM match_candidates
               WHERE updated_at < CAST(:cutoff AS timestamptz)
               ORDER BY id LIMIT :batch_limit)
```

- **対象は全status一律で`updated_at < cutoff`**(pendingも含む)。08 §2.5は「評価確定から30日」だが、pendingのまま30日経過した行の起点Intentは確実に失効している(Intentの寿命は数日・引用#3)ため、一律判定で安全側に立つ。latchesは削除しない(履歴維持・引用#7)
- ①のNULL化は②より前で必須(FK RESTRICT・引用#18)。latches行自体は残り、proposal(表示用サマリ)も残るため相手側の履歴表示に影響しない
- Indexは追加しない。updated_atの部分Indexなしのフルスキャンになるが、日次1回・ベータ規模(数万行)では問題にならない。件数が問題になる規模までは将来課題(§5)
- 発火時刻をJST 0時とした根拠: 削除境界の厳密性は要求されておらず(評価確定±1日の精度で十分)、「暦日で動く日次処理は0時」というResetJobの既存約束と揃える。30日判定自体はClock.now()(UTC)の実時間で行う
- run_onceは削除件数をログに出す。runループ・graceful shutdown・注入sleepはResetJobと同一契約(unit試験が決定的に回る)

### 2.6 calibration_records匿名化第一段(D-13)の変換内容

§2.1の`_ANONYMIZE_CALIBRATION`が第一段の全体である。変換内容と根拠を確定値として固定する。

| 項目 | 第一段(本単位)の扱い | 根拠 |
|---|---|---|
| latch_id | NULLへ | 引用#4・#6 |
| intent_ids | NULLへ(配列CHECK(2〜4)はNULLで回避) | 引用#4・#6 |
| actual_responses | 各要素から`user_id`キーを除去。response・answered_atは**そのまま**(時刻の丸めは第二段) | 引用#4「ID系をNULL化」+引用#6「匿名化でuser_idを除去し、回答種別と時刻は残す」 |
| anonymized_at | **NULLのまま**(立てない) | 引用#4「anonymized_atはバッチ実行時に立てる」 |
| prediction・proposal_snapshot・matched・actual_attended・cancelled_after | 触らない(数値・分類値のみでID系を含まない。粒度低下は第二段) | 引用#5 |

対象行の特定は`intent_ids @> 当該Intent`(=ANY一致)。単発削除では削除Intentを含む行、退会では全カスケードの繰り返しで退会ユーザーのIntentを含む行が全て掴まる。行単位の匿名化(他参加者のIntentも含む行全体のID系をNULL化)が08 §2.5「calibration_records該当行の匿名化」の読みどおりである。`latch_id IS NOT NULL`条件で匿名化済み行をスキップする(冪等ガード・CHECK(引用#6)とも矛盾しない)。

### 2.7 blocks・reports・match_events・users残存列の扱い(ws-5引継ぎへの回答)

- **blocks: 残置する**。08 §2.5の削除リスト(引用#1)にblocksは含まれない。退会ユーザーとの新候補生成は構造的に起こらない(候補生成はactiveなIntent同士のみで、退会で全Intentが消えるため)。BlockCache(blk:u:)の行もTTL 3600で自然失効し、Layer 1の判定はSQL直読み(ws-5 §5-1の明示残置)のため退会後の参照問題もない
- **reports: 残置する**。削除リストに含まれないことに加え、通報は「受付・記録 → 運用者によるレビュー → 対処」(08 §5.2)の手動対応の途中にある可能性があり、退会を理由に対応記録を消す根拠がない。reporter/reporteeの表示名はusers行の置換(§2.3)で「退会したユーザー」になる
- **match_events: 残置する**。source_intent_idはFKなし(引用#10)で、処理済み・隔離の記録は運用観測の資産。08 §2.5の定期削除対象は両候補テーブルのみ(引用#3)
- **users残存列(birth_date・location_preferences・visibility_preferences・trust_score)**: 残置する。NOT NULL制約(birth_date)があり、退会後は本人の照会経路が消える(ログイン不能・birth_dateを返すAPIは本人のみ)。ただし「DB内部に生年月日が残る」点是08の思想と完全には噛み合わないため、§5のオーナー確認候補に載せる
- **latches.responses内の退会ユーザーのuser_id**: 残置する。引用#7はresponsesを履歴維持の対象に挙げる。表示はusers joinの表示名で「退会したユーザー」に変わるため、UUIDの残存は「個人との紐付き」をUI上に残さない

### 2.8 採用しないもの(YAGNIによる切り捨て一覧)

- **マイグレーション0007** — 本単位の全変更は既存列・既存制約の範囲で成立する。alembic head=0006不変
- match_eventsの30日削除 — 引用#3の対象外(§2.7)
- latches・messages(他人分)・latch_status_eventsの削除 — 履歴維持(引用#7)
- deleted_at列・退会status列の新設 — §2.4案Bの却下
- 退会時のblocks行削除・BlockCacheの明示DEL — §2.7のとおり残置
- 削除完了のユーザーへの通知・確認メール — 規定なし
- 削除処理の進捗API(非同期化した場合の照会) — 同期完結(§2.3案A)のため不要
- updated_at部分Index — ベータ規模では不要(§2.5)

## 3. ファイル構成

### 3.1 作るもの(新規)

| ファイル | 内容 |
|---|---|
| `backend/src/latch/intents/deletion.py` | 削除カスケードの実体。§2.1のSQL定数+`cascade_delete_intent`+`close_latches_on_delete`(stage1から移設・中身不変) |
| `backend/src/latch/worker/retention.py` | `RetentionJob`(30日定期削除)。ResetJobと同型のrunループ・retry・注入sleep |
| `backend/tests/unit/intents/test_deletion.py` | カスケードのSQLピン(§4) |
| `backend/tests/unit/test_worker_retention.py` | RetentionJobのunit(worker系はunit直下のtest_worker_*命名に従う) |
| `backend/tests/unit/users/test_account_delete.py` | 退会serviceのunit |
| `backend/tests/integration/test_deletion_api.py` | 単発削除APIのintegration |
| `backend/tests/integration/test_account_api.py` | 退会APIのintegration |
| `backend/tests/integration/test_retention_job.py` | 30日削除のintegration |

新規テストのbasename一意は計画書§0の機械確認に載せる(unit直下に`test_retention.py`等の衝突候補は現状ないことを確認済み)。

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更 |
|---|---|
| `backend/src/latch/worker/stage1.py` | EVENT_DELETED処理を`cascade_delete_intent`呼び出しへ差し替え。`close_latches_on_delete`と関連SQL定数をdeletion.pyへ移設(インポート参照へ)。`_CLOSE_CANDIDATES`/`_CLOSE_GROUPS`を削除 |
| `backend/src/latch/intents/service.py` | `delete()`のallowed_fromを全statusへ拡張。「物理削除しない(05 §6・M3-8参照)」コメントを更新(削除Event処理で物理削除・§2.2) |
| `backend/src/latch/users/service.py` | `delete_account`追記・SessionStore/uowのDI(§2.3) |
| `backend/src/latch/users/routes.py` | `DELETE /v1/users/me`エンドポイント追記 |
| `backend/src/latch/main.py` | users serviceの組み立てにSessionStoreを渡す(lifespan) |
| `backend/src/latch/worker/main.py` | RetentionJobの構築+`asyncio.create_task`(redis有無に依存しない) |
| `backend/tests/unit/test_worker_stage1.py` | 削除Event2試験(336・362行)の期待を物理削除へ書き換え(機械的追従) |
| `backend/tests/unit/intents/`既存 | deleteの受理status拡張に伴う追試験の追記(404/403は既存のまま) |

### 3.3 触らないもの

- `latches/`(store・service・routes・calibration)— 表示名置換はusers行で自動、D-09通知除外はsweeper構造担保済み(§1.3-h)
- `worker/sweeper.py`・`worker/reset.py`・`worker/reeval.py` — RetentionJobは独立taskで既存ジョブに触れない
- `safety/`・`notifications/`・`worker/matching/`(Layer 1〜5)— 候補のclosed化を書く経路(layer4・latch_engine・group_engine)はそのまま残り、定期削除と単発カスケードが掃除する
- `alembic/`(head=0006不変)・`frontend/`・`prototype/`
- `auth/service.py`・`auth/deps.py` — auth_subject切替(§2.4案A)は照合変更不要

## 4. テスト方針

### 4.1 unit

- **test_deletion.py(カスケードのSQLピン)**: DB fixtureに「1対1候補3status(evaluated/skipped/closed)+pending」「group_candidates+latches(group_candidate_id FKつき)」「開いているlatches(candidate/proposed/partial_accept)」「matched latches(他参加Intentつき)」「calibration_records(intent_ids・actual_responses・prediction)」を仕込み、cascade実行後に①候補全行の消滅(status問わず=FR-22) ②latches.group_candidate_id=NULL+group行消滅 ③latches cancelled+latch_status_events(from/to/user_id=NULL) ④残存Intentの復帰(expires_at経過前=active/経過済み=expired) ⑤calibration行のlatch_id・intent_ids=NULL・actual_responsesからuser_id除去(回答種別・answered_atは保存・配列順も保存) ⑥Intent行の消滅(raw_text・embeddingごと)を検証。他ユーザーのIntent・候補・calibration行が無傷なことも検証する。二重実行の冪等(全操作が0行/無変化)
- **test_worker_retention.py**: cutoff境界(29日残・30日+1秒で削除)・pending行も削除されること・latchesのgroup_candidate_id NULL化→group削除の順序・batch_limitの窓(2周で空になる)・run(注入sleep・stop応答)・run_once失敗時のretry_sec再試行
- **test_account_delete.py**: serviceのProtocolスタブでcascade呼び出し回数(全Intent分)・messages/notifications削除・users置換(display_name='退会したユーザー'・profile='{}'・auth_subject='deleted:'+id)・コミット後の失効呼び出し(revoke_access+revoke_family)を検証
- **test_worker_stage1.py追従分**: EVENT_DELETED→cascade差し替え後、Intent行の物理不在・後続Eventのintent_not_found破棄が従来どおり動くこと

### 4.2 integration

- **test_deletion_api.py**: `DELETE /v1/intents/{id}`→204。matched Intentでも204(従来422の拡張・FR-19経路)・expired Intentでも204・cancelled済みへの再実行も204(冪等)。削除後のDB状態(単発の物理削除はEvent処理経由のため、Workerを動かすかstage1.processを直接呼ぶ構成で検証=ws-1の統合試験と同型)。他ユーザーIntentの無傷・404/403
- **test_account_api.py**: `DELETE /v1/users/me`→204。全Intent行消滅・候補消滅・messages(sender分)・notifications消滅・blocks/reportsは**残置**・users行のdisplay_name='退会したユーザー'・latches詳細APIの表示名が置換後になる・`POST /v1/auth/token`(同一IdP)が旧Userに紐付かず`profile_complete: false`・退会前JWTでのAPI呼び出しが401
- **test_retention_job.py**: 実DBで30日経過行の削除・latches FK解消・未経過の行(29日)は残る

### 4.3 既存試験への影響

- `test_worker_stage1.py`2試験の期待書き換え(§2.1)のほか、`tests/integration/test_matching_groupengine.py`・`test_latches_api.py`・`test_expiry_batches.py`のEVENT_DELETED/closed参照を機械検索(`rg 'EVENT_DELETED|closed' tests/`)し、該当するものは物理削除期待へ追従する。追従はすべて期待値の機械的変更で、本単位で挙動を変えるのは削除範囲のみ
- 期待件数の積算(unit/integrationの追加件数・main 1426からの増分)は計画書で確定させる

## 5. 実装時確認事項(G3時確認候補)+未解決の論点

1. **DELETE APIの受理status拡張**(§2.2)。matchedはFR-19(08 §2.5)からの必然。expired/cancelled受理は「08 §2.5の削除リストがstatusを問わない」ことからの演繹であり、05 §6遷移表に「matched→cancelled」「expired→cancelled」の行はない。**05 §6への追記(削除受理statusの明記)をdocs改版候補**として残す
2. **退会API(DELETE /v1/users/me)の05 §5への規定追加**(§2.3)。API形態(パス・204・401のみ)は本設計の確定値。docs改版候補
3. **users行の残存列**(§2.7)。birth_date等はNOT NULLかつ照会経路消失のため残置するが、「退会後もDBに生年月日が残る」点是08のプライバシー思想と完全には噛み合わない。完全匿名化(ダミー値化・列削除)を選ぶならdocs改版+マイグレーションを要する。**オーナー確認候補**
4. **auth_subject='deleted:'+idの暗黙契約**(§2.4案A)。マイグレーション回避とのトレードオフ。退会状態の明示的表現としてdeleted_at列を将来追加する場合の移行は単純(接頭辞を行特定に使える)
5. **単発削除の物理削除完了がEvent処理依存**(§2.2案A)。通常は数秒(debounce対象外の即時処理)。quarantinedに落ちた場合の回収は運用(隔離行の再投入)。同期削除(案B)との再検討は、遅延が実測で問題になる場合に限る
6. **match_eventsの恒久蓄積**(§2.7)。08 §2.5の定期削除対象外。容量が問題になる規模での削除policyは将来課題
7. **updated_at部分Indexの不在**(§2.5)。両候補テーブルの行数が数十万を超える段階で`WHERE updated_at < :cutoff`用のindexを検討する

## 6. 補足(設計判断の記録)

- **messages削除の範囲をsender_id一致と読んだ**(§2.3)。08 §2.5は「当該ユーザーのmessages」を削除し、直後に「messagesはユーザーの履歴として維持する(退会時の表示名置換・ID除去は適用する)」とも書く。削除リストの「当該ユーザーの」を「自分が送信した行」、「維持」を「相手側の履歴」と読めば両立する。残る参加者から見て、退会者の送信行まで消えると会話の文脈が壊れるが、これも「個人との紐付きを切る」方向と整合する
- **単発削除と退会で経路が分かれた理由**(§2.2・2.3)。単発削除はws-1 §2.7の承認済み構成(Event経由)を維持し、退会はアカウント終了としての原子性を優先した。どちらも`cascade_delete_intent`単一実体を呼ぶため、削除範囲の正しさは1箇所の検証で担保される
