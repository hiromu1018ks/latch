# M3 ws-3(通知の媒体確定)設計メモ

作成: 2026-10-01(agent1・brainstormingスキル適用)
対象: 12 M3-5「通知: FCM(汎用文のみ・条件サマリ禁止・drinkingも同様式)、アプリ内通知(お知らせ)、D-05回答期限式の実装、D-08上限(日6件・同時3件)と保留の提示順制御」のうち、
STATUS単位表(ws-6設計§5-3承認どおり)**媒体と残務**の担当分。D-08上限・提示順・D-05再計算の本体はM2 ws-6が実装済み
(latch_engine.try_promote/drain・latch_calc)。本単位は通知の「送り先と届け方」を作る。

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M2 ws-6(latch_engine)とM3 ws-2(sweeper)が先行書き込みしたnotifications行の媒体を確定させる。
先行書き込みは3種で、いずれもpayloadは `{"latch_id": "<uuid>"}` の最小参照(通知文を持たない):

| type | 書き込み元 | 通知対象者 | 出典 |
|---|---|---|---|
| proposal | latch_engine.try_promote(proposed遷移と同一tx) | notification_level != mutedの参加者(2〜4人) | ws-6設計§2.7 |
| nearby_candidate | latch_engine._nearby_in_tx(閾値未満評価のtx内) | notification_level == nearby_alsoの参加者(日次上限内のみ) | ws-6設計§2.7 |
| attendance_request | sweeper._complete_latch(matched→completedと同一tx) | 参加Intentのuser_id全員(cancelledは対象外) | M3 ws-2設計§5②・05 §6 |

本単位で作るものは次の3つ。

1. **FCMプッシュ送信(スタブ=ドライラン)** — 04「LATCH Engineの確定後、NotificationがFCM APIを同期的に呼び出す」の実装。実送信はG3待ち事項(人間領域)として資材分離済みのため、LLM Gatewayスタブと同型のドライランで進める
2. **アプリ内通知(お知らせ)API** — GET /v1/notifications・POST /v1/notifications/{id}/read(05 §5)。お知らせUI(topbar popover+未読ドット)自体はws-8
3. **通知文面の供給規則** — プッシュ本文=typeごとの固定文言のみ。アプリ内通知=latches.proposal構造からクライアントが組み立て(文言をサーバが完成形で持たない)

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | プッシュ本文は汎用文「LATCH候補があります。詳細はアプリでご確認ください」のみ。条件サマリ(人数・時間帯・地域名・カテゴリ・予算)を含めない。**drinkingも様式同一**(カテゴリ推察自体を防ぐ)。この規制はプッシュ許可の有無と独立 | 03 §4・08 §2.6(FR-21) |
| 2 | アプリ内通知(summary_only)=「LATCH候補があります。/ 今夜 20:00〜 / 天文館 / 3人 / 焼肉 / 予算上限5,000円 / あなたの条件との一致度が高い候補です。」/ アプリ内通知(hidden_until_match)=「条件が合う候補があります。一致度が高い候補です。今夜中にご回答ください。」(条件サマリ部分を出さない) | 03 §4様式 |
| 3 | 通知手段はプッシュ+アプリ内通知の併用。許可を取得できないユーザーにはアプリ内通知で到達(プッシュは合図のみ・内容の確認と回答はアプリ内) | 03 §4・D-18 |
| 4 | 提案通知は双方(グループでは必要人数全員)へ同時送信 | 03 §8 D-20・01 §8 |
| 5 | nearby存在通知もD-08日次上限(日6件)に含む。同時進行上限(3件)はproposed数の上限のためcandidateのままの存在通知には適用しない。nearby存在通知には条件サマリ・一致度・相手情報を含めない | 03 §4・01 §10・06 §6 |
| 6 | mutedのIntentは通知を送らない(proposed遷移などの挙動は通常どおり) | 06 §6・05 §6 |
| 7 | GET /v1/notifications / POST /v1/notifications/{id}/read — 本人のみ。一覧はページネーション共通規定(`?cursor=&limit=1〜100`・既定20・超過422・`{"items":[], "next_cursor"}`・不透明cursor)・ソートはnotificationsがcreated_at降順 | 05 §5・§5ページネーション共通規定 |
| 8 | latches.proposalは通知文・提案画面の表示要素のデータソース。**文言はクライアント/Layer 5のテンプレートがこの構造から組み立てる**(A/B対象「通知文」の変更に構造が引かれないため)。hidden_until_matchではheadcount+match_levelのみ格納 | 05 §2 |
| 9 | 通知タップは提案詳細画面へ遷移。バナー上の直接回答ボタンは設けない | 03 §4(FR-46) |
| 10 | 実施自己申告: matched→completed時点でプッシュ+アプリ内通知により1問の自己申告を求める。設問「実際に会いましたか?」・回答期限3日。cancelled LATCHには送らない | 05 §6・09 §2.2 D-09 |
| 11 | **FCMはドライラン(送信内容を記録のみ)**とし、通知の到達と本文の表示規制(FR-21)は記録で検証する。#13はmutedケース(通知ドライラン記録に提案通知が出力されないこと)を含む。追加試験はプッシュ本文が汎用文であること・条件サマリがプッシュへ出力されないこと(drinking含む)・summary_onlyはお知らせ/提案画面にサマリ表示・hiddenは非表示を確認 | 10 §1・§3(#13・追加試験) |
| 12 | 通知2経路: プッシュ=FCM統一(APNsはFCM経由・Web段階ではWeb Push経路のみ)。アプリ内通知=DB保存+自前表示 | 04 §2・§3 |
| 13 | 通知は判定後5秒以内(LATCH Engine確定後、NotificationがFCM APIを同期的に呼び出す)。層別予算はLayer 5+通知送信≤2秒 | 04 性能目標表・06 §1 |
| 14 | 構造化ログは許可リスト方式(出力フィールドを型で固定)。例外・エラーはIDのみ | 08 §2.4 |
| 15 | time_summary=JST `YYYY-MM-DD HH:MM`・area_name=geo中点の逆転ジオコーディング・nearby提案は`{"headcount", "match_level": "low"}`最小構成 — いずれもws-6実装済みの解釈(G2承認) | ws-6設計§2.5・§2.7・STATUS引き継ぎメモ② |

### 1.3 既存実装資産との接続(マージ済みmain)

- **notificationsテーブル(0001)**: `id/user_id/type/payload(jsonb)/read_at/created_at`・typeにCHECK制約なし(ws-6実装時確認事項9で確認済み)・usersへのFKのみ。**マイグレーション追加は不要**
- **書き込み経路は2箇所のみ**: `latch_engine._insert_notification`(proposal/nearby_candidate)と`sweeper._INSERT_ATTENDANCE_NOTIFICATION`(attendance_request)。グループ提案もtry_promote経由で同じSQLに集約される(GroupEngineはnotificationsを書かない)
- **D-08カウントの真実はnotifications行**(type IN ('proposal','nearby_candidate')のJST日付窓COUNT・ws-6設計§2.6)。**attendance_requestは提案通知でないため日次上限を消費しない**(03 §4「提案通知の頻度」の文言どおり・カウントSQLどおり。本単位で変更しない)
- **LLM Gatewayスタブの型**(模倣対象): Provider protocol+StubLLM(遅延注入・決定的応答)・SendRecord→構造化ログ`latch.llm.send`(status ok/timeout/error・機微を含まない)・settings `llm_mode` stub/real(realは鍵欠落でfail-fast)
- **API側の型**: latchesパッケージ(routes/service/store/schemas分離・`require_authenticated`+claims→user解決・encode_cursor/decode_cursorのbase64urlキーセット方式・`{"items": [], "next_cursor": null}`応答)
- **試験互換の規律**: 依存注入はNone許容(未注入ならno-op — `_kick_embedding`と同型)・api_rate_limitedは全v1ルートに適用(test_rate_limit_wiring.pyのピンへの機械的追従が必要)

### 1.4 スコープ外(触らないもの・後続への明示)

1. **お知らせUI・通知許可の要求・設定画面**(ws-8・フロントエンド)。未読ドットの専用カウントAPIは05に規定がないため作らない(一覧itemsのread_atで判定可能)
2. **実FCM送信の資材**(Firebaseプロジェクト・サーバー認証情報・VAPID鍵 — STATUS「G3判定の待ち事項1」の人間領域)。firebase-admin依存の追加・デバイストークン管理・WebpushConfigのclick URL(FR-46の実機側)はすべてG3後
3. **D-08上限判定・提示順・D-05再計算の本体**(ws-6実装済み・再実装しない)
4. **チャット・実施自己申告の回答API**(ws-4)。attendance_request通知の**回答側**は本単位の外(通知の媒体だけが本単位)
5. **提案詳細画面の残時間表示**(ws-7)。回答期限の値はresponse_deadline列として既に供給済み

## 2. 実装方式の選択と推奨

### 2.1 プッシュ送信の実行位置 — 推奨: notifications書き込みtxのコミット直後・worker内で直接呼び出す(案A)

3案の比較:

| 案 | 内容 | 評価 |
|---|---|---|
| **A(推奨)** | latch_engine/sweeperへPushSenderを注入。try_promote・_nearby_in_tx・_complete_latchの各txで送信対象を集め、**txコミット後に送信**(呼び出しは1回) | 04(引用#13)「確定後、NotificationがFCM APIを同期的に呼び出す」の直接実装。マイグレーション不要・常設ループ不要。D-20同時送信は「同一txで全員分の行を書く→コミット直後に同一処理で送る」で担保される |
| B | プッシュoutbox: notificationsへpush_sent_at列を追加し、ディスパッチャが未送信行をポーリング | 障害耐性(プロセス死亡時の再送)では上。ただしスタブ段階でマイグレーション+常設ジョブを導入するのは過剰。送信失敗の体験的損失は小さい(引用#3: アプリ内通知が常時フォールバック)。実FCM化(G3後)に再検討する価値はある |
| C | APIプロセスが未送信行を拾って送る | WorkerとAPIの2プロセスで送信責務が分かれ、コミットとの整合(二重送信・送信漏れの窓)が複雑化。不採用 |

**順序は「txコミット→送信」で固定する。** 逆にすると、送信後にtxが失敗した場合「notifications行がないのにプッシュだけ飛んだ」状態が生まれ、D-08カウントの真実(通知の事実)と配送が乖離する。

**失敗時の扱い: 送信メソッドは例外を呼び出し元へ出さない(握って記録)。** notifications行=通知の事実はコミット済みであり、プッシュは配信経路の一つにすぎない(引用#3)。エンジン側の再実行は冪等ガード(latch_score IS NULL・条件付きUPDATE)で送信経路に入らないため、再送の試みすら発生しない。失敗はドライラン記録のstatus=errorで監視する。プロセス死亡でコミット済み・未送信が残る余地は案Aの既知の限界として記録する(§5-5)。

### 2.2 PushSenderのIF — Admin SDKの呼び出し形を模倣したスタブ

**実送信側IFの一次確認(2026-09-29ユーザー指示・context7 `/firebase/firebase-admin-python`)**:

- 送信単位: `messaging.Message(notification=messaging.Notification(title=..., body=...), token=<device token>)`。1トークン1Message
- 一括: `await messaging.send_each_async(messages)`(≤500件)→ `BatchResponse(success_count/failure_count/responses: List[SendResponse]`)。同一文言を多トークンへは `messaging.MulticastMessage(tokens=[...])` + `send_each_for_multicast_async()`
- 検証だけの `dry_run=True` 引数が存在する(FCM公式のドライラン。我々のスタブと意味が一致する)
- 初期化: `credentials.Certificate(<serviceAccountKey.jsonのパス|dict>)` + `firebase_admin.initialize_app(cred)`。Web Pushのタップ遷移は `messaging.WebpushConfig(fcm_options=messaging.WebpushFcmOptions(link=<URL>))` で指定(FR-46の実機側)
- 非同期HTTPはSDK内部のHttpxAsyncClientが担う(asyncio環境でそのままawaitできる)

これを踏まえたIF(ユーザ単位で抽象・トークン解決はreal実装の内部):

```python
class PushSender(Protocol):
    name: str
    async def send(
        self, *, user_id: uuid.UUID, notification_type: str, latch_id: uuid.UUID
    ) -> None:  # 例外を出さない(記録して握る)。戻り値なし
```

- **StubPushSender**(本単位で実装): `templates.build_push(notification_type)`で(title, body)を得て`asyncio.sleep(delay)`後、PushSendRecord(status="ok")を出す。`delay > PUSH_TIMEOUT_S`でtimeout記録になる(タイムアウト経路の試験用)。**ネットワーク呼び出しは一切ない**
- **FirebasePushSender**(G3後・本単位では不作成): 上記SDK事実に基づき、user_id→デバイストークン解決(トークン保管はG3時点で設計)→`Message(notification=Notification(title, body), token=...)`をトークン数だけ組み立て→`send_each_async`→BatchResponseを記録に変換。`credentials.Certificate`はsettingsの資材パスから`initialize_app`(資材欠落はfail-fast — llm_mode="real"の鍵規律と同一)。トークン0件のユーザーは「送信0件」の記録(スキップ)
- **PUSH_TIMEOUT_S = 3.0**(コード定数・unit試験で短縮注入)。層別予算「Layer 5+通知≤2秒」(引用#13)はp95目標であり、timeoutは上限。スタブ既定delay=0のためci実時間への影響なし

### 2.3 ドライラン記録(PushSendRecord)— LLM送信記録との違いは「本文を含む」こと

10 §1(引用#11)が「プッシュ本文が汎用文であること」を**記録で検証**することを要求するため、記録には本文が必要になる。LLMのSendRecordが「内容を含まない」(08 §3)のに対し、ここは意図的な差分です。本文は§2.4の固定定数のみを通り得るため機微は入り得ない(許可リスト方式・引用#14の型固定は踏襲)。

```python
class PushSendRecord(BaseModel):
    occurred_at: datetime            # Clock.now()(tz-aware UTC)
    user_id: str                     # 宛先ユーザー
    notification_type: str           # proposal / nearby_candidate / attendance_request
    latch_id: str                    # 参照先
    title: str                       # テンプレート定数
    body: str                        # テンプレート定数(汎用文)
    status: Literal["ok", "timeout", "error"]
    error_code: str | None = None    # 例外IDのみ
```

出力先は構造化ログ `latch.push.send`(`latch.llm.send`と同型・JSON 1行)。ci integration試験はcaplogで、staging(ws-9 E2E)はログ経由で本文検証する。

### 2.4 プッシュ文面 — type→固定文言の写像のみ(案A・構造によるFR-21担保)

| 案 | 内容 | 評価 |
|---|---|---|
| **A(推奨)** | `build_push(notification_type) -> (title, body)`。引数はtypeのみで、proposal/latchのデータを受け取らない | 文言に条件サマリを混ぜる経路が**型の上で存在しない**。FR-21(引用#1)を査閲ではなく構造で担保する |
| B | サーバ側で文言組み立て関数(引数にproposalを取る) | 「組み立てれば混ぜられる」経路が残る。レビュー依存の保証になる |

初期値(テンプレート定数・03 §4の様式どおり):

```python
PUSH_TITLE = "LATCH"
PUSH_BODY_LATCH = "LATCH候補があります。\n詳細はアプリでご確認ください。"      # proposal / nearby_candidate
PUSH_BODY_NOTICE = "LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"  # attendance_request
```

- **proposal/nearby_candidateは同一の文言**にする。2つを分けると「閾値未満の候補である」ことがOS経路(ロック画面等)に漏れる。drinkingも含めカテゴリ・visibility・スコアの一切が本文に出ない(引用#1)
- **attendance_requestは第二の汎用文**とする。実施済みLATCHの存在を示す文面は成立前の意思ではないが、08 §2.6の趣旨(第三者に見える経路に載せない)を全プッシュへ準用する。docsに文言規定がないため設計確定とし、G3時確認候補に記録する(§5-1)
- hidden_until_matchの提案もプッシュ本文は同一(アプリ内通知の文言分岐=引用#2はクライアント側の組み立て。§2.5)

### 2.5 お知らせ一覧のitem構成 — 推奨: latchesをLEFT JOINで埋め込み、文言はクライアントが組み立てる(案A)

| 案 | 内容 | 評価 |
|---|---|---|
| **A(推奨)** | `GET /v1/notifications`の各行に `payload->>'latch_id'` でlatchesをLEFT JOINし、latch要素(id/status/response_deadline/expires_at/completed_at/proposal)を埋める | お知らせは履歴を含む(expired・rejected・attendanceの行はGET /v1/latches〔proposed以降・候補一覧〕では取れない)。05 §2(引用#8)「文言はクライアント/テンプレートがこの構造から組み立てる」に一致し、A/B文言変更がサーバに引かれない |
| B | latch_idのみ返し、クライアントが個別にGET /v1/latches/{id} | 一覧1画面あたりN+1。加えてcandidateのままのnearby行は同APIの対象外で組み立て不能 |
| C | 通知文を完成形でnotifications.payloadに格納 | ws-6の最小参照設計(payloadに文言を置かない=文言変更が構造に引かれない)と衝突。不採用 |

応答スキーマ:

```text
{"items": [{
   "id": uuid, "type": str, "latch_id": uuid,
   "read_at": datetime|null, "created_at": datetime,
   "latch": {                      # LEFT JOIN。参照先不明時のみnull(防御)
     "id": uuid, "status": str,
     "response_deadline": datetime, "expires_at": datetime,
     "completed_at": datetime|null, "proposal": object
   } | null
 }], "next_cursor": str|null}
```

- **hidden_until_matchの行はproposalがheadcount+match_levelのみ**(ws-6実装済み)のため、JOINで埋めても条件サマリは構造的に出ない(引用#2・#8)。nearby行も最小構成(`match_level="low"`)でサマリ・相手情報が出ない(引用#5)。**表示の規制をサーバ側のデータ構造で担保したまま、文言だけをクライアントに委ねる**
- responses配列・score・intent_ids・参加者情報はitemに出さない(お知らせの表示に不要・03 §7の非開示規定と無関係に最小化)
- ソートはcreated_at降順・同点はid降順(同一tx内の複数行が同時刻になるためタイブレーク必須)。cursorはbase64url 2キー`(created_at, id)`(latchesのencode_cursor方式の流用)
- attendance_requestの行はlatch.status=completedで返る。クライアントは設問(引用#10)を表示し、回答操作はws-4のAPIへ

### 2.6 既読API

- `POST /v1/notifications/{id}/read`: 対象行をSELECT(`WHERE id AND user_id=:me`)→**行がなければ404 NOT_FOUND**(他人のid・存在しないidを区別しない)→`read_at IS NULL`なら`UPDATE ... SET read_at=:now`→**204 No Content**(POST /v1/sessionsと同型)。既読済みならUPDATEせず204(冪等)
- エラーは401(認証)・404・422(limit/cursor形式不正。共通規定)

### 2.7 例外方針・冪等性(送信側)

| 状況 | 扱い |
|---|---|
| 送信成功 | PushSendRecord(status="ok") |
| 遅延>timeout | status="timeout"・error_code="PushTimeoutError"・握る |
| 送信側例外 | status="error"・error_code=例外クラス名・握る(呼び出し元の再試行に載せない — §2.1) |
| PushSender未注入(既存試験構成) | no-op(latch_engine/sweeperのctor引数None許容・`_kick_embedding`と同型) |
| notifications書き込みtx失敗 | プッシュは送らない(コミット後のみ送るため構造的に排除) |
| 不明なnotification_type | build_pushがValueError(fail-fast・握らない)=実装バグの即時顕在化。記録型は出ない |

トランザクション分割: latch_engine・sweeperの既存tx構成は変更しない。追加するのは**tx外の送信呼び出しのみ**(LLM呼び出しのtx外化と同じ規律)。

### 2.8 採用しないもの(YAGNIによる切り捨て)

1. **push outbox(push_sent_at列+ディスパッチャ)** — §2.1案B。実FCM化時に再検討
2. **未読カウント専用API・users/meへの未読フィールド** — 05に規定なし。一覧で判定可能
3. **firebase-admin依存の先行追加** — importだけの実装は資材分離(G3待ち)に反する
4. **notificationsテーブルへのtype CHECK制約・文言列** — type値域はコード側Literal(ws-6実装時確認事項9の判断を継承)。文言は格納しない(§2.5案C不採用の理由)
5. **FCM topic配信** — 全員へ同一文言とはいえ、トピック購読管理は実機資材と不可分。ユーザ単位送信で十分
6. **一覧の絞り込み(unread_only等)** — 05に規定なし。popoverは全件時系列で足りる

## 3. ファイル構成

### 3.1 作るもの(新規)

| ファイル | 内容 |
|---|---|
| `backend/src/latch/notifications/__init__.py` | パッケージ公開IF(make_notifications_service・notifications_router・PushSender) |
| `backend/src/latch/notifications/types.py` | type定数の正本(NOTIFICATION_PROPOSAL/"proposal"・NOTIFICATION_NEARBY/"nearby_candidate"・NOTIFICATION_ATTENDANCE/"attendance_request")。latch_engine・sweeperはimport切替で単一ソース化(機械的変更) |
| `backend/src/latch/notifications/templates.py` | PUSH_TITLE・PUSH_BODY_LATCH・PUSH_BODY_NOTICE・`build_push(notification_type)`。FR-21の構造担保(§2.4) |
| `backend/src/latch/notifications/records.py` | PushSendRecord・SendStatus・`push_log`(ロガー`latch.push.send`・records.pyと同型) |
| `backend/src/latch/notifications/sender.py` | PushSender protocol・StubPushSender(遅延注入・timeout・例外握り)・`build_push_sender(settings, clock)`・PUSH_TIMEOUT_S |
| `backend/src/latch/notifications/store.py` | `select_notifications_page`(LEFT JOIN latches・キーセット改頁)・`select_notification_owned`・`mark_read` |
| `backend/src/latch/notifications/service.py` | make_notifications_service(claims→user解決・cursor encode/decode・list/read) |
| `backend/src/latch/notifications/routes.py` | notifications_router: GET ""・POST "/{notification_id}/read" |
| `backend/src/latch/notifications/schemas.py` | NotificationOut・NotificationLatchOut・NotificationListResponse |
| `backend/tests/unit/notifications/test_templates.py` | 文言定数の全文ピン+写像の一意性 |
| `backend/tests/unit/notifications/test_push_sender.py` | スタブ記録・timeout・例外握り・build_push_sender |
| `backend/tests/unit/notifications/test_notifications_service.py` | cursor・read分岐(スタブstore)・SQL compile検査(store_sql回帰試験の型) |
| `backend/tests/integration/test_notifications_api.py` | §4.2のintegration群 |

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更 |
|---|---|
| `worker/matching/latch_engine.py` | ①type定数をnotifications.typesへ移譲(import切替) ②ctorへ`push: PushSender \| None = None` ③try_promote: tx内で送信対象(user_id, type, latch_id)を集め、tx後に送信 ④_nearby_in_tx: 戻り値に通知済みuser_id群を追加(呼び出し元_evaluate_pairがtx後に送信) |
| `worker/sweeper.py` | ①type定数import切替 ②ctorへpush引数 ③_complete_latch: tx後送信 |
| `worker/main.py` | build_push_senderで構築しLatchEngine・ExpirySweeperへ注入(§2.1案Aの配線) |
| `main.py` | notifications_router登録・既存スキップ判定(build_notificationsフラグ・latchesと同型) |
| `settings.py` | `push_mode: str = "stub"`・`push_stub_delay_ms: int = 0`("real"はG3後に実装。未実装値の指定はValueError — 静かにスタブへ落ちない規律)。llm_9項目ピン試験とはプレフィックスが別系で無干渉 |
| `tests/unit/test_settings.py` | push_2項目の既定値ピンへ追従(機械的) |
| `tests/unit/worker/test_sweeper.py` 等 | attendance送信の期待追従・注入なし構成の互換確認(機械的) |
| `tests/unit/ratelimit/test_rate_limit_wiring.py` | 新ルート2行の追従(機械的・M1 ws-5前例) |

変更ファイルの目安: 新規13+既存8(ピン追従の試験群を含む)。

### 3.3 触らないもの(明示)

- `worker/matching/proposal.py`・`group_engine.py`・`layer*.py`(通知書き込みなし・try_promote経由)
- `latches/`配下(回答API・calibration — ws-1資産)
- `alembic/`(マイグレーション追加なし・head=0005不変)
- `pyproject.toml`(依存追加なし)
- `frontend/`・`prototype/`(ws-7/ws-8)
- docs本流(12 M3-5とSTATUS単位表の記載差はws-6承認事項3で解決済み・改版不要)
- チャット・attendance回答API(ws-4)・blocks/reports(ws-5)・削除系(ws-6)

## 4. テスト方針

### 4.1 unit(`make test`・スタブで決定的)

1. **templates**: 3定数の全文ピン(汎用文の恒久固定=FR-21試験の対)。build_pushは全typeで(title, body)を返す・未知typeはValueError・**写像の値にproposal語彙(time_summary・area_name等)が現れないこと**(定数のみであることの検査)
2. **push_sender(stub)**: ok記録の全フィールド・delay<timeoutでok・delay>timeoutでtimeout記録・送出例外をスタブ内差し替えでerror記録+握る(呼び出し元に例外が出ない)・未登録typeでValueError
3. **notifications_service**: cursor encode/decode(形式不正422)・read(所有404/未読204/既読204の冪等)・list応答組み立て(latch=null防御)
4. **store**: SQL compile検査(CAST(:x AS jsonb)等のbind param正規化 — ws-3〔M1〕の回帰試験と同型)
5. **latch_engine注入**: FakeSender(記録リスト)で試験2相当のE2E前提 — 提案2名=2件送信・muted参加者=0件・nearby=nearby_also側のみ・try_promote上限時(candidate保留)=0件。**未注入構成で既存試験が全て緑のまま**(no-op互換)

### 4.2 integration(`make test-ci`・compose常設DB・実Redis)

`test_notifications_api.py`(prefix=通知系fixture・`m3ws3-`・teardownはFK順・時間窓now+5日統一の対抗策):

1. **E2E送信**(02#13・追加試験の下地): ユーザー2名+Intent(now+5日窓)→jev_result直入れ→LatchEngine.handle→caplogの`latch.push.send`に**本文=汎用文フル文字列**のok記録2件(ユーザー毎)→GET /v1/notificationsで同一latchのitemが2人それぞれに見える
2. **FR-21下地(本文の均一性)**: summary_only×2・hidden_until_match混在・drinking Intentの3ケースで、プッシュ記録の本文が**3ケースともbyte同一**(文言分岐なしの証明)
3. **#13 muted下地**: 片方muted→push記録1件のみ・notifications行1行(muted側)・latches.status=proposed(遷移は通常どおり)
4. **お知らせ一覧**: 作成順に降順・同一tx内同時刻行のid降順タイブレーク・cursor改頁(limit=1で2頁取得)・limit=101で422・cursor形式不正422・hidden行のitem.proposalがheadcount+match_levelのみ(#22の下地)
5. **既読**: 204・他人のidは404・存在しないidは404・二回目も204(冪等)・read_at反映
6. **attendance送信**: matched→completedをsweeperで発生→push記録(第二汎用文)・一覧にattendance_request行・latch.status=completed
7. **nearby**: 閾値未満+nearby_also→存在通知のpush記録・日次上限到達時は記録なし(candidate行は残る)
8. **認証**: 無token 401(GET・POST両経路)

### 4.3 検証手順(報告書への明記用)

1. `make lint` / `make test`(全unit)
2. `uv run pytest --collect-only tests/integration/test_notifications_api.py -q`(収集件数の記録・実行はスーパーバイザー検証時)
3. テストbasename一意(運用ルール5のコマンドが空)
4. alembic head=0005不変・依存追加なし(`git diff main -- pyproject.toml uv.lock`が空)
5. スーパーバイザー検証時: `docker compose build api worker` → `make test-ci`(基準1301+新規) → 残存確認(m3ws3-%・notifications/users 0件)

## 5. 未解決の論点(supervisor承認事項・G3時確認候補)

**supervisor承認を求める事項:**

1. **attendance_request等のプッシュ文言PUSH_BODY_NOTICE**(§2.4)。docsに文言規定なし(09 D-09は「プッシュ+アプリ内通知により1問の自己申告を求める」まで)。08 §2.6の趣旨の準用として第二の汎用文「LATCHからのお知らせがあります。詳細はアプリでご確認ください。」を設ける。原文言の「LATCH候補があります」は実施後の申告には不適切のため別定数とした
2. **nearby存在通知のプッシュ本文も提案と同一文言**(§2.4)。文面を分けると閾値未満候補であることがOS経路に漏れるため同一が最も安全、という設計判断
3. **送信はtxコミット直後・失敗しても再送なし**(§2.1案A)。notifications行=通知の事実・プッシュは配信経路の一つ(D-18のフォールバック構成)。プロセス死亡で未送信が残る余地とoutbox再検討(G3後)を承認の前提として記録
4. **お知らせ一覧へのlatch要素埋め込み**(§2.5案A)。文言組み立てをクライアントに委ねる05 §2の規定の読み。表示規制(hidden・nearbyの最小proposal)はサーバ側構造で担保されたまま

**解釈記録(G3時確認候補・STATUS「G3時確認事項」へ追記提案):**

5. **成立(matched)通知は作らない**。05 §6遷移表が通知を規定するのはcandidate→proposed(提案)とmatched→completed(申告)のみで、proposed→matchedの通知規定がない。01 §10・03 §4の具体規定も候補通知と申告通知に限る。ユーザーはホーム/お知らせ経由で成立を知る構成。01 第46行「成立時にだけ通知が来る」は概念の導入文であり具体規定(03 §4・05 §6)が優先する、の読み。製品として成立プッシュを足すならdocs改版を要する
6. **attendance_requestはD-08日次上限を消費しない**。上限の対象は「提案通知」(03 §4)で、engineのカウントSQL(type IN proposal/nearby_candidate)どおり。本単位で変更しない

**実装時確認事項(実装エージェントが確認して報告):**

7. latch_engine・sweeperの型定数import切替後、既存試験(test_matching_latchengine.py・test_sweeper.py等)が文字列リテラル参照で無傷であること(定数値は不変のため期待どおり無影響のはず)
8. test_rate_limit_wiring.pyの全ルート列挙への新規2行追従が他単位(ws-4並走)と衝突しないこと(追記位置は機械的・マージ時は両側保持)
