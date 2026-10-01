# M3 ws-5 設計 — ブロック・通報

作成: 2026-10-01(agent1)。参照仕様: 12 M3-7 / 08 第5節・D-23 / 06 §2・§6・§10 / 05 §2・§5〜§6 / 03 §5 / 01 §22

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

ブロックと通報のAPI面を実装し、D-23(成立後のブロック即時適用)を確定する。ws-4が受信側(チャット送信時の409 CHAT_READONLY分岐)を先行実装済みであり、本単位は登録側を作って両者を閉じる。具体的には次の4点である。

1. **blocks API**: `POST /v1/users/{user_id}/block`(登録)・`DELETE /v1/users/{user_id}/block`(解除)・`GET /v1/users/me/blocks`(一覧・cursor改頁)の3エンドポイント
2. **D-23成立後即時適用**: ブロック登録の副作用として、自分と相手を共に含む進行中のLATCHをcancelledで閉じる(candidateを含む。§2.3)。matchedはcancelled化せず、チャットの読み取り専用化はws-4実装の送信時判定が担う
3. **Redisブロックキャッシュ**: ws-4が明け渡した差し替え点(`store.select_block_between`呼び出し)をキャッシュ参照へ載せ替える(08 §5.1)
4. **reports API**: `POST /v1/reports`(受付・記録のみ。運用者の手動対応に回す)

「このチャットは利用できません」の表示文はフロント(ws-7/ws-8)の領域であり、本単位はAPI・データ面だけを持つ。マイグレーションは追加しない(blocks・reportsテーブルは0001で作成済み・§2.8)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | blocksは単方向の記録で、マッチングは (A,B)(B,A) の双方向を確認して候補から除外する。Layer 1の参照は毎回DBを見ずRedisキャッシュから行い、ブロック設定の反映はキャッシュ更新を経由する。解除はDELETE /v1/users/{id}/blockで、遡及効果は持たない(候補除外・読み取り専用化を取り消さない) | 08 §5.1 |
| 2 | 通報はPOST /v1/reportsで受け付け、運用者の手動対応に回す。フローは「受付・記録 → レビュー → 対処(警告・アカウント停止・ブロック支援)」で、MVPでは自動検知・自動停止を行わない。通報理由は選択式(不適切な内容 / 不快な対応 / なりすまし疑い / その他)。通報は提案画面・チャット画面から常に可能 | 08 §5.2 |
| 3 | D-23: ブロックは成立後も即時に適用する。効果は3つ — ①今後の候補生成から除外(Layer 1) ②進行中の提案(proposed / partial_accept)はcancelledで閉じる ③成立済み(matched)のLATCHはチャットを読み取り専用化して新規送信を不可にする。completedへの遷移は通常どおり。解放済みの情報の回収は行わない。ブロックされた側の画面には「このチャットは利用できません」のみ表示し、ブロックされた事実を直接通知しない | 08 D-23 |
| 4 | latches遷移: proposed・partial_accept→cancelledのトリガーに「ブロック」が明記。matched→cancelledは参加Intentの削除のみ(ブロックではmatchedをcancelledにしない) | 05 §6 |
| 5 | blocksテーブル: id / blocker_id / blocked_id / created_at。解除は当該行の削除 | 05 §2 |
| 6 | reportsテーブル: id / reporter_id / reportee_id / latch_id / reason / status / created_at。latch_idはNULL可(LATCH文脈を欠く通報も想定) | 05 §2・0001 |
| 7 | API表: GET /v1/users/me/blocks(本人のみ・改頁共通規定)・POST /v1/users/{id}/block(本人操作)・DELETE /v1/users/{id}/block(本人操作・過去の候補除外・読み取り専用化には遡及しない)・POST /v1/reports(本人操作) | 05 §5 |
| 8 | 改頁共通規定: `?cursor=&limit=`(limit 1〜100・既定20・超過は422)。応答は`{"items": [...], "next_cursor"}`。blocksの既定ソートはcreated_at降順 | 05 §5 |
| 9 | messages: ブロック適用中のLATCH(08 D-23)は読み取り専用(書き込みは409 CHAT_READONLY)。取得(閲覧)は参加者ならstatusを問わず可能 | 05 §2・05 §5(ws-4実装) |
| 10 | Redisの用途は4つ(セッション失効リスト・ブロックリスト・友人関係〔MVP外〕・Jev実行カウンタ)。ブロックリストはHard Filterが高頻度参照するため | 04 §2 |
| 11 | 進行中の提案の解散(ブロックによるcancelled、D-23)では参加Intentはもともとactiveのままのため、復帰処理は不要 | 06 §6 |
| 12 | latch_status_eventsは遷移を書くトランザクションと同時に挿入する。user_idは遷移の引き金となったユーザーで、システム起因(sweeper・競合クローズ等)はNULL。クローズ検知(保留キュー再評価トリガー)の観測点でもある | 05 §2・06 §10 |
| 13 | 不成立の理由(相手の回答・期限切れ・ブロック・競合等)は「この提案は成立しませんでした」の一文に統一し、ブロックされた事実は一切開示しない | 03 §5 |
| 14 | すべてのAPIは認証済みユーザーのみ利用可能。ブロックの判定はクライアント入力によらずサーバ側で強制する | 08 §5.3・01 §22 |
| 15 | エラーcode列挙にblocks/reports専用codeの定義なし(404 NOT_FOUND・403 FORBIDDEN・422 VALIDATION_ERROR・429 RATE_LIMITED等の共通codeで構成する) | 05 §5 |
| 16 | blocksのUNIQUE(blocker_id, blocked_id)はDB制約に入れずアプリ層で担保する(0001作成時の判断)。reports.statusもCHECKなし・値域はM3で確定する(0001コメント) | 0001 |
| 17 | 競合クローズはcandidate・proposed・partial_acceptのlatchesをcancelledへ閉じる(条件付きUPDATE+ORDER BY idのFOR UPDATE。実装はlatches/store.pyの`cancel_latch`・`select_conflicting_latches`) | 05 §6・ws-1実装 |
| 18 | sweeperのクローズ検知drain(to_status IN ('rejected','expired','cancelled','matched')のlatch_status_events観測)が、クローズで空いた枠の保留キュー再評価を同じtickで実行する | 06 §10・ws-2実装 |

### 1.3 既存実装資産との接続(すべてマージ済みmain・head=0006)

| 資産 | 位置 | 本単位での扱い |
|---|---|---|
| `store.select_block_between`(DB直読みの双方向判定EXISTS) | `latches/store.py:631` | **差し替え点**。関数自体は残置(キャッシュのフォールバックと既存試験が使う) |
| `send_message`内の判定呼び出し | `latches/service.py:263` | `BlockCache`注入時はキャッシュ優先・未注入時は従来どおりDB直読み(既存試験互換・§2.2) |
| `cancel_latch`(条件付きcancelled UPDATE)+`insert_latch_event` | `latches/store.py:425` | D-23 cancelled化でそのまま再利用(ws-1の競合クローズと同型・§2.3) |
| Redisクライアント生成(lifespan・`decode_responses=True`) | `main.py` | `build_auth`/`build_rate_limit`と同じredis_clientを`build_safety`でも共用 |
| Redis操作を閉じ込めるStoreクラス・キー接頭辞の名前空間(`auth:`・`rl:`) | `auth/sessions.py`・`ratelimit/store.py` | 同一パターンで`safety/cache.py`を作る(接頭辞`blk:`) |
| Layer 1のblocks除外SQL(NOT EXISTS) | `worker/matching/layer1.py`(LAYER1_WHERE_TAIL) | **触らない**。§2.2・§5-1のとおりSQL維持 |
| integrationテストの実Redis fixture(compose常設) | `tests/integration/conftest.py` | キャッシュの実挙動試験でそのまま使用 |

### 1.4 スコープ外(後続単位へ渡すもの。本単位では作らない)

- 「このチャットは利用できません」の表示・ブロック管理画面・通報フォーム → ws-8(フロント。03 §8)。チャット画面の読取専切替もws-7が409 CHAT_READONLYを受けて行う(§5-4)
- 通報の運用レビューUI・statusの遷移操作(警告・アカウント停止等の対処) → M4+の運用面。本単位は受付・記録のみ(引用#2)
- 退会時の削除・calibration匿名化・Redis失効リストの退会利用 → ws-6
- GET /v1/latches/{id}応答への読取専用手がかり(`chat_readonly`等)の追加 → 05に規定なし。送信409でws-7が切替可能なため作らない(§5-4)
- Layer 1(worker側)のRedisキャッシュ参照化 → 本単位ではAPI側のみキャッシュ化する(§2.2・§5-1)

## 2. 実装方式の選択と推奨

### 2.1 モジュール構成 — safety/を新設する

ブロック・通報を`users/`へ足すか、新規モジュールにするか。`users/`は現在`POST /v1/users`(登録)と`GET /v1/users/me`の2エンドポイントだけで、ドメインも登録・年齢検証に絞られている。本単位はlatchesテーブルのcancelled化(ws-1資産の再利用)・Redisキャッシュ・cursor改頁と、usersドメインの外にある依存を3つ持つ。`users/service.py`へ足すと責務が混ざるため、**`safety/`を新設する**(ブロックと通報は01 §22の「相手からの防護」群で、機能のまとまりとしても独立する)。

```
backend/src/latch/safety/
  __init__.py
  cache.py     # BlockCache(Redis読み書きを閉じ込める・§2.2)
  store.py     # blocks/reportsテーブルSQL(cancelled化対象検索を含む)
  service.py   # BlockReportService(登録・解除・一覧・通報の順序づけ)
  routes.py    # safety_router(4エンドポイント)
  errors.py    # SafetyError系(既存のintents/errors.pyパターン)
```

ルータは`APIRouter(prefix="/v1")`で、パスを`/users/me/blocks`・`/users/{user_id}/block`・`/reports`と書く。パスは05 §5の表(引用#7)どおり。`users_router`(`/v1/users`)とは別インスタンスになるが、FastAPIのルーティングに衝突はない(`/v1/users/{user_id}/block`はusers側に存在しない)。依存は既存ルータと同じ`api_rate_limited`(401→429の順)+`require_authenticated`。DIは`get_safety_service`(app.state経由・既存パターン)。

`latches/`への接触は2箇所だけである(§2.2の差し替えと`main.py`の構成)。

### 2.2 Redisブロックキャッシュ — ユーザー単位のJSON一覧・read-through・更新時DEL

#### キーの形

08 §5.1(引用#1)は「参照は毎回DBを見ずRedisキャッシュから」「ブロック設定の反映はキャッシュ更新を経由する」と定める。キャッシュの形を3案比較する。

| 軸 | 案A(推奨): ユーザー単位JSON一覧 | 案B: ペア単位の判定結果 | 案C: SET型の集合 |
|---|---|---|---|
| キー数 | チャット送信に関わるユーザー数で頭打ち(`blk:u:{user_id}`) | 送信ペアの数だけ膨張する(ブロックなしペアもキャッシュされる) | 案Aと同数 |
| 空の扱い | `"[]"`をキャッシュでき、ブロックゼロの大多数の参照も1回のDB読みで賄える | `"0"`をキャッシュ可 | 空SETは存在できないため、ブロックゼロのユーザーが毎回ミスになる |
| 双方向判定 | 自分と相手の一覧をMGETし、互いに含むかをメモリ判定(1往復) | ペア毎にGET(グループでは参加者数分) | SMISMEMBERを双方向に(往復増) |
| 更新 | ブロック登録・解除時に両者のキーをDEL(§2.4) | 当該ペアのキーを書換/DEL | 片側のキーだけSADD/SREM |

案Aを採る。キーと値は次のとおり。

```
blk:u:{user_id} = '["<blocked_uuid>", ...]'   (そのユーザーがブロックしている相手の一覧・JSON配列)
SET EX 3600
```

接頭辞`blk:`は`auth:`・`rl:`と名前空間を分ける(既存パターン)。TTL 3600秒は掃除用ではなく、後述のDEL失敗時の最終収束期間である(ブロック反映の取りこぼしが最長1時間で自動的に解消する)。

#### 参照(read-through)と差し替え点

チャット送信時の判定は、ws-4実装の`select_block_between`呼び出し(`latches/service.py:263`)を`BlockCache`経由へ差し替える。`LatchesService`へ`block_cache`を注入し、`send_message`はキャッシュ優先・未注入時は従来どおりDB直読みとする(既存の`test_chat_service.py`はモック注入で動くため影響なし)。

判定の流れ(`BlockCache.is_blocked_between(me, others)`):

1. `MGET blk:u:{me} blk:u:{o1} ... blk:u:{on}`(送信1回)
2. キー欠落があれば、そのユーザーのブロック一覧をDBから読み(`SELECT blocked_id FROM blocks WHERE blocker_id = :u`)、`SET EX 3600`して値とする(空は`"[]"`)
3. メモリ判定: 自分の一覧にothersの誰かが含まれる、またはothersのいずれかの一覧に自分が含まれれば`True`(双方向・引用#1)

Redisで例外が出たら(RedisError)、DBの`select_block_between`へフォールバックする。ブロック判定は安全性にかかる判定で、Redis断でチャットを止めるよりDBを見るほうが正しい(可用性と安全性を両立する。warningログ1行)。DB読み込みは`BlockCache`が自分のengine接続で行い、送信トランザクション(FOR UPDATE保持中)と直列しない——blocks行をロックしない単純SELECTのため競合しない。

#### 更新(反映はキャッシュ経由・08 §5.1)

ブロック登録・解除の**DBコミット後に**、両者のキーをDELする(`blk:u:{blocker}`と`blk:u:{blocked}`の2本。双方向判定に双方のキーが関わるため)。コミット前にDELすると、並行するread-throughがコミット前のDB状態で旧値を再キャッシュする窓が残る。DELの失敗(Redis断)はTTL 3600秒で収束し、warningログ1行で済ませる(登録API自体はDBコミット済みなので成功扱い。キャッシュは性能の最適化であって真実はDBにある)。

キャッシュを使うのはチャット送信判定のみとする。ブロック一覧APIはDBから直接読む(cursor改頁にcreated_atが必要で、キャッシュはID一覧しか持たないため)。Layer 1(worker側)はSQL現状維持である(§5-1)。

### 2.3 D-23 cancelled化 — ブロック登録APIのトランザクション内で同期実行する

進行中の提案のcancelled化を、登録API内の同期処理にするか、ジョブに分けるか。

| 軸 | 案A(推奨): 登録API内で同期 | 案B: ジョブで非同期 |
|---|---|---|
| 反映の即時性 | コミットと同時に確定(D-23「即時に適用」のとおり) | ジョブ周期の遅れの間に提案がmatchedになり得る(その場合は読取専用で代替されるが、「進行中はcancelled」との不一致が残る) |
| 実装規模 | ws-1の競合クローズと同型・store関数を再利用するだけ | ジョブの追加と冪等管理が発生 |
| 対象の大きさ | 自分と相手を共に含む進行中latches(通常0〜数行)で、トランザクションの長期化は無視できる | — |

案Aを採る。手順は`POST /v1/users/{user_id}/block`の単一トランザクションで、番号は後述の§2.4手順に組み込む。

**対象はcandidateを含む(candidate・proposed・partial_accept)**。08 D-23(引用#3)は「進行中の提案(proposed / partial_accept)はcancelledで閉じる」と書き、candidateには触れない。しかし実装を確認すると、保留キューの提示判定(`latch_engine.try_promote`)はD-05再計算とD-08上限検査のみを行い、blocksの再検査を持たない。ブロック後にcandidateを放置すれば、次のdrainでブロック済みの相手へ提案が提示されてしまう。D-23の①「今後の候補生成から除外(Layer 1)」は提示を含むマッチング経路全体の遮断を意図しており(根拠: 「マッチングとコミュニケーションの両経路を断つ」)、candidateも閉じるのが仕様の精神に忠実である。加えて、ws-1の競合クローズは既にcandidateをcancelledへ閉じる対象に含んでおり(引用#17・`cancel_latch`のWHERE条件)、本単位はこの関数をそのまま再利用するだけで同じ構造になる。05 §6の遷移表にcandidate→cancelledの行がない点は、競合クローズと同じ拡張解釈としてG3時確認候補に登録する(§5-2)。

対象検索のSQL(競合クローズ`select_conflicting_latches`と同型・ORDER BY idでロック順序を固定):

```sql
SELECT l.id, l.status FROM latches l
WHERE l.status IN ('candidate', 'proposed', 'partial_accept')
  AND l.intent_ids && CAST(:my_intents AS uuid[])
  AND l.intent_ids && CAST(:their_intents AS uuid[])
ORDER BY l.id
FOR UPDATE
```

`my_intents`・`their_intents`はそれぞれのユーザーの全Intent(全status。latches側のstatus条件で実質active/paused分に絞られる)。グループLATCHで第三のメンバーを含む行も、自分と相手の双方が参加していれば対象にする(部分成立禁止の提案を、メンバー間のブロックで閉じる。05 §6のcancelled遷移と同じ扱い)。

1行ごとの処理はws-1と同じ2stepである。

1. `cancel_latch`(条件付きUPDATE: status IN ('candidate','proposed','partial_accept')。競合負けはFalseで読み飛ばし)
2. 成功時に`insert_latch_event(from_status, 'cancelled', user_id=blocker, now)`(引用#12。ブロックはユーザー操作のため引き金のuser_idを記録する。競合クローズのNULL=システム起因と区別する)

参加Intentはactiveのまま手を触れない(引用#11・復帰処理不要)。**cancelled化の通知は送らない** — 03 §5(引用#13)は不成立理由を一文に統一し、ブロックされた事実の開示を禁じる。競合クローズ(ws-1)も通知を送っていない。cancelled後の表示はホーム一覧からの消退と「この提案は成立しませんでした」(ws-8)で足りる。

matched LATCHはcancelled化の対象外である(引用#4)。読み取り専用化はws-4の送信時判定が自動的に担う: blocks行の追加→コミット後のキャッシュDEL→次回送信時のキャッシュ再構築で409 CHAT_READONLYになる。completedへの遷移もsweeperが通常どおり行い(blocksを見ない)、実施自己申告も妨げない(引用#3)。

クローズで空いた提示枠の再評価は、ws-2のsweeperが持つクローズ検知drain(引用#18)がlatch_status_eventsのcancelled挿入を観測して同じtickで実行する。本単位はイベント挿入までを担い、再評価経路への追加実装は不要である。

### 2.4 blocks API — POSTは冪等201・解除は404・一覧はdisplay_nameつき

#### POST /v1/users/{user_id}/block

単一トランザクションで次の手順をとる。

1. `fetch_user_id`(auth_provider+subject→UUID。未登録JWTは404 NOT_FOUND — 既存のチャットAPIと同じ扱い)
2. `user_id == 自分` → 422 VALIDATION_ERROR(自分自身のブロック。引用#15に専用codeなしのため共通code)
3. 相手ユーザーの実在検査 → 404 NOT_FOUND
4. 自分→相手のblocks行の存在検査。あれば**冪等201として手順5〜7をスキップ**(引用#16のとおりDBにUNIQUE制約がなく、二重行は一覧の重複表示以外に影響しないが、存在検査で通常経路の二重登録を防ぐ)
5. INSERT(blocks行・idはDB DEFAULT)
6. D-23 cancelled化(§2.3の対象検索→cancel→イベント挿入)
7. コミット後、キャッシュDEL(`blk:u:{自分}`・`blk:u:{相手}`の2本)

応答は`201 {"blocked_id": "<uuid>"}`(既存ブロック時も201。クライアントは状態を気にせず扱える)。

#### DELETE /v1/users/{user_id}/block

1. `fetch_user_id` → 404(未登録JWT)
2. blocks行の削除(DELETE … RETURNING id)。行がなければ**404 NOT_FOUND**(冪等204にしない — 05 §5に規定がなく、誤UI操作で既に解除済みなら知らせるほうが親切。引用#15の共通codeで構成する)
3. コミット後、キャッシュDEL(両者)

応答は204。**遡及効果はない**(引用#1・#7): cancelledにした提案は戻さず、過去の候補除外も手当てしない。解除後の未来については、Layer 1のSQLがblocks現物を見るため候補生成が自然に再開し、チャット送信判定もblocks現物(キャッシュ再構築後)に従う。すなわち**判定はblocksの現物参照で、一度ブロックされたLATCHの読み取り専用を解除後も維持はしない**。08 §5.1の「読み取り専用化を取り消さない」は「過去に起こした状態変化(cancelled等)を戻さない」の意と読む(05 §2が「ブロック**適用中**のLATCHは読み取り専用」と現物の適用状態で表現していること、誤ブロック→即解除の救済を壊さないことから、この解釈を優先する)。対案(一度適用されたら恒続のsticky扱い・latchesへフラグ追加)はマイグレーションとws-4判定の変更を要するため採らず、§5-3に承認事項として残す。

#### GET /v1/users/me/blocks

cursor改頁(引用#8: created_at降順・同点はid・limit 1〜100既定20・超過422。cursorはサーバ生成の不透明文字列 — ws-4のmessages改頁と同型実装)。応答は次の形とする。

```json
{"items": [{"blocked_id": "…", "display_name": "…", "created_at": "…"}], "next_cursor": null}
```

`display_name`はusersをJOINした追加である(05に応答フィールドの規定がなく、ブロック管理画面〔ws-8〕が表示名で一覧する需要から設計判断として含める)。cursor符号化・limit検証はws-4で作った改頁部品の方式を踏襲する。

### 2.5 reports — 受付・記録のみ・reasonは4値のコード

リクエストと応答を次で確定する(05 §5は目的と認可のみ規定し、bodyの契約を持たない)。

```json
request:  {"reportee_id": "<uuid>", "latch_id": "<uuid|null>", "reason": "inappropriate_content"}
response: 201 {"report_id": "<uuid>"}
```

- **reasonは選択式4値を英語コードで受ける**: `inappropriate_content`(不適切な内容) / `unpleasant_behavior`(不快な対応) / `suspected_impersonation`(なりすまし疑い) / `other`(その他)。08 §5.2(引用#2)の選択肢をAPI契約に落としたもので、表示文言はフロント(ws-8)が持つ。日本語文言をDBに保存する形式はロケール変更に弱く、code値を契約とする
- **latch_idは省略可**(既定null。引用#6の「LATCH文脈を欠く通報も想定」に沿う)
- 検査: `reportee_id == reporter` → 422。reportee実在 → 404。latch_id指定時はlatch実在(404)・自分が参加者・reporteeが参加者を検査し、不成立は422 VALIDATION_ERROR(通報は自分が関わる提案・チャットの相手に対して行う文脈・引用#2)
- **statusは'pending'を固定で投入**する。0001コメント「status値域はM3」(引用#16)への対応として、値域をpending(受付)→ reviewed(レビュー済)→ resolved(対処済)に確定し、本単位はpending投入のみ・遷移操作は運用面(M4+)とする。DB CHECKは追加しない(0001踏襲・マイグレーションなし)
- **重複制限は置かない**(同一通報の再送も記録する)。自動検知・自動停止を行わない(引用#2)ため、重複の整理は運用者側で行う

処理は単一INSERTのみで、他テーブルに触れない。

### 2.6 08 §5.3「セッション失効(ブロック・退会)」— 1対1ブロックでは失効リストを使わない

08 §5.3は「セッションの失効(ブロック・退会の即時反映)はRedisの失効リストで行う」と書く。ここで**1対1のブロック登録で相手のJWTを失効させることはしない**。理由は3つある。第一に、ブロックはアカウント停止ではなく「相手との接触機会の排除」であり(D-23)、ブロックされた側は自分のIntent・他のLATCHを通常どおり使える。第二に、セッションを失効させれば再ログインを強いられ、「ブロックされた事実を直接通知しない」(引用#3)と正面から衝突する。第三に、失効リストの実運用はlogout(実装済み)・退会(ws-6)・運用者によるアカウント停止(reports対応・引用#2)に寄る。08 §5.3の「ブロックの即時反映」の実体は、判定経路の即時性(送信時の都度判定+キャッシュDEL)と§2.3のcancelled化の同期実行が担う、と読む。この解釈は§5-4に承認事項として残す。

### 2.7 マイグレーション — 追加なし

blocks(引用#5・UNIQUEなし=アプリ層担保)・reports(引用#6・status CHECKなし)とも0001で作成済みで、本単位はカラムもIndexも追加しない。**alembic headは0006のまま**。共有ci-dbの運用ルール1(並走単位の同時test-ci禁止)の束縛を受けず、test-ciは通常どおり実行できる。`test_schema.py`への追従もない。

## 3. ファイル構成

| ファイル | 作る/触る | 内容 |
|---|---|---|
| `backend/src/latch/safety/__init__.py` | 作る | `make_safety_service`等の組み立て |
| `backend/src/latch/safety/cache.py` | 作る | `BlockCache`(MGET・read-through・`is_blocked_between`・`invalidate(user_ids)`・RedisError時DBフォールバック) |
| `backend/src/latch/safety/store.py` | 作る | blocks行の存在検査/INSERT/DELETE・一覧改頁SELECT(users JOIN)・ユーザーのブロック一覧SELECT(キャッシュミス用)・cancelled化対象検索(§2.3のSQL)・reports INSERT・相手実在/参加者検査 |
| `backend/src/latch/safety/service.py` | 作る | `BlockReportService`(block/unblock/list_blocks/reportの手順・§2.3〜§2.5) |
| `backend/src/latch/safety/routes.py` | 作る | safety_router(4エンドポイント・api_rate_limited+require_authenticated) |
| `backend/src/latch/safety/errors.py` | 作る | SafetyError系(共通error codeへの写像) |
| `backend/src/latch/main.py` | 触る | `build_safety`フラグ・`build_latches`へ`block_cache`注入(redis_client共用) |
| `backend/src/latch/latches/service.py` | 触る | `send_message`の判定差し替え(cache注入時は`BlockCache`優先・数行) |
| `backend/src/latch/latches/store.py` | 触らない | `select_block_between`は残置のみ(キャッシュのフォールバック・既存試験が使う) |
| `backend/src/latch/worker/**` | 触らない | Layer 1・latch_engine・sweeperとも変更なし(§2.2・§2.3) |
| `backend/src/latch/users/`・`auth/`・`intents/`・`notifications/`・`geo/` | 触らない | — |
| `backend/alembic/` | 触らない | 追加なし(§2.7) |

## 4. テスト方針

### 4.1 unit(スタブで確定できるもの — `tests/unit/safety/`)

- **test_cache.py**: read-through(ミス→SELECT→SET EX 3600・JSON配列の直列化)・ヒット(MGET1回でDB問い合わせゼロ)・空一覧`"[]"`のキャッシュ・双方向判定(片方向のみでTrue・無関係でFalse・グループ複数相手)・`invalidate`で両者DEL・RedisError→DBフォールバック(warningログ)。Redisはスタブ(asyncメソッドを持つ偽オブジェクト)
- **test_store_sql.py**: SQL文字列のピン(存在検査/INSERT/DELETE/一覧改頁/キャッシュ用一覧/cancelled化対象の`intent_ids &&`×2とFOR UPDATE ORDER BY id/reports INSERT)。既存の`test_store_sql.py`(intents/latches)と同形式
- **test_service.py**: 手順の分岐 — 自分自身422・相手不在404・冪等201(INSERTとcancelled化をスキップ)・D-23で`cancel_latch`+`insert_latch_event`(user_id=blocker)の呼び出し・コミット後のキャッシュDEL順・DELETEで行なし404・reportsの422系(reportee==reporter・reason値域外・latch_id非参加)・pending投入。latches/store関数はmonkeypatch
- **test_routes.py**: 4エンドポイントの契約(応答形式・201/204/404/422・改頁パラメータ)
- **`tests/unit/latches/test_chat_service.py`への追記**: `block_cache`注入時の送信409(キャッシュヒット経路)と未注入時の従来経路(既存試験は`select_block_between`モックのまま残る)

### 4.2 integration(`tests/integration/test_safety_api.py`・実DB+実Redis)

1. ブロック登録→一覧(created_at降順・cursor改頁・display_name)→解除(204・再解除404)のサイクル
2. ブロック後のチャット送信409 CHAT_READONLY(matched LATCH・**キャッシュ経由**: 登録前に一度送信してキャッシュを温め、登録後に送信してDEL→再構築で409になることを確認)
3. 解除後は送信可に戻る(§2.4の現物参照)
4. D-23: proposedをcancelled化+latch_status_events(user_id=blocker)・matchedはcancelled化されず送信だけ409・candidateもcancelled化・参加Intentはactiveのまま
5. グループLATCHでの双方向判定(第三メンバーを含む提案のcancelled)
6. reportsの受付201・422系(reportee==reporter・reason値域外・latch_id参加者検査)・latch_idなし受付

### 4.3 機械チェック(計画書レビュー時の確定事項)

- `test_rate_limit_wiring.py`: 新ルート4本が走査対象へ自動追加される(ルート数のピンがあれば機械的追従・M1 ws-5前例)
- `test_schema.py`: マイグレーションなしのため追従なし
- basename衝突・DB残存干渉: ファイルは`tests/unit/safety/`・`tests/integration/test_safety_api.py`で既存と衝突なし。head不変のため運用ルール1の束縛なし

## 5. 未解決の論点(supervisor承認事項・G3時確認候補)

1. **Layer 1(worker側)のblocks参照はSQL直読みのまま**とする(§2.2)。08 §5.1(引用#1)は「Layer 1の参照は毎回DBを見ずRedisキャッシュから」と書き、本設計はAPI側(チャット送信)のみをキャッシュ化する。理由: ①ブロック判定は安全性にかかり、キャッシュ欠落時のfail-open構造をworker側に持ち込まない方が安全(DB直読みは常に真実) ②Layer 1の判定頻度はバッチ周期のみで、MVP規模のDB負荷は問題にならない ③Layer 1のblocks条件はlayer1/layer2/H再検証/Pool検索が共有するSQL文字列に組み込まれており、キャッシュ化はM2資産の広範な再構成になる。**08 §5.1の当該文言との差分は解消されず残る**(性能面の将来課題として先送り)。G3時確認候補
2. **D-23 cancelled化の対象にcandidateを含める**(§2.3)。05 §6の遷移表はproposed・partial_accept→cancelledのみ規定し、candidate→cancelledの行はない。しかし提示判定(try_promote)にblocks再検査がなく、放置するとブロック済み相手への提示が起こるため、競合クローズ(引用#17)と同じcandidate含みを採る。G3時確認候補
3. **ブロック解除後にチャット送信が可能に戻る(現物参照)**(§2.4)。08 §5.1「読み取り専用化を取り消さない」を「過去の状態変化を戻さない」の意と読む。対案(sticky: 一度適用されたら解除後も読取専用を維持)はlatchesへのフラグ追加+マイグレーション+ws-4判定変更を要する。G3時確認候補
4. **1対1ブロックでセッション失効リストを使わない**(§2.6)。08 §5.3の「ブロック・退会の即時反映は失効リスト」の「ブロック」を、判定経路の即時性(キャッシュDEL)とcancelled化の同期実行で担うと読む。失効リストはlogout・退会(ws-6)・運用者停止に寄せる。G3時確認候補
5. (軽)`GET /v1/users/me/blocks`応答への`display_name`込み(05に規定なし)、reports.reasonの英語コード化・status値域pending/reviewed/resolvedの確定(§2.5) — いずれも05が契約を持たない部分の設計判断。フロント(ws-7/ws-8)と共有すべき値

## 6. 完了条件(本単位の検証対象)

- 4エンドポイント(POST/DELETE block・GET me/blocks・POST reports)が§2.4・§2.5の契約どおり動作する(unit+integration)
- チャット送信のブロック判定がキャッシュ経由に載き替わる(差し替え後も409 CHAT_READONLY・Redis断でDBフォールバック・既存の`select_block_between`経路も残る)
- D-23が確定する: candidate/proposed/partial_acceptのcancelled化+latch_status_events(user_id=blocker)・matchedはcancelled化せず送信409のみ・参加Intentはactiveのまま・cancelled化の通知なし
- ブロック登録・解除後にキャッシュDELが走り、次回参照へ反映される(TTL 3600の収束もunitで確認)
- マイグレーション追加なし(head=0006不変)・main test-ciがグリーン(1378 passed基準への追加)
