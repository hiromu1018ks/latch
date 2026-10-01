# 第22章 消すことの設計: カスケード・退会・30日の掃除

- 種別: 解説(通読して理解を積む章)
- 前提知識: 第4章(FKと制約・3層の経路)・第6章(DELETE /v1/intents のcancelled遷移と
  version)・第9章(outboxとEvent経由の処理)・第17章(latchesのstatusと回答)・
  第20章(チャットと履歴)
- この章を読み終えるとできるようになること:
  - 「消す」が1回のDELETEで済まない理由を、テーブル間の参照(FK)から説明できる
  - 削除カスケードの処理順序がFK依存から導かれることを、実際のSQLで追える
  - 単発のIntent削除がEvent経由・退会が同期トランザクションと、経路が2つに
    分かれている理由を、それぞれの要求(構成の維持と確実性)から説明できる
  - 退会したあとにusers行が残り続ける理由と、`auth_subject='deleted:'` という
    書き換えがログインを遮断する仕組みを説明できる
  - 30日定期削除が候補テーブルだけを対象にし、latchesは消さない理由を説明できる
  - 匿名化の第一段が「IDを切る」だけで「粒度を下げない」ことと、それを2段階に
    分けた理由を説明できる
- 対応コード: `backend/src/latch/intents/deletion.py`(新設)・
  `users/service.py` の `delete_account`・`users/routes.py`・
  `worker/retention.py`(新設)・`worker/stage1.py` の差し替え・
  `intents/service.py` の `delete`・`worker/main.py`。マイグレーションは
  ありません(必要な列はすべて0001〜0006に存在)
- 設計の根拠: `docs/plans/M3/ws-6-design.md`・`ws-6-report.md`。
  本文の「NN §X」は `docs/NN-*.md` の第X節を指します(08 §2.5=削除範囲の
  確定・FR-19/FR-22・D-13=匿名化)
- 次に読むもの: この章が2026-10-02時点の最終章です。読む順路上の直前は
  Lab 9(ws-7の3画面を動かす)です。以降の章(お知らせ一覧・設定画面)は
  実装の進行(ws-8)とともに追加されます

## 22.1 「消す」という注文は、範囲の注文

この教科書でこれまで読んできたコードは、すべて「作る」側でした。Intentを保存し、
候補を評価し、提案を作り、回答を集め、LATCHを成立させ、チャットを流す。しかし
サービスにはもうひとつ、逆の注文が必ず来ます。「消してください」。

LATCHではこの注文に2つの形があります。**単発の削除**(Intentを1つ消す)と、
**退会**(アカウントごと消す)です。どちらも「消す」と言うけれど、中身は
まったく違う処理です。まず仕様(08 §2.5)が注文した「消す範囲」を、そのまま
並べてみます。

1. 該当するIntentの行(raw_text・structured_data・embeddingを含む)
2. そのIntentを含む**すべての** match_candidates・group_candidates
   (処理済みかどうかを問わない)
3. そのIntentを含む回答待ちのlatches(cancelledで閉じる)
4. そのユーザーのmessages・notifications(退会のとき)
5. calibration_recordsの該当行の**匿名化**(ID系の除去)
6. 退会のときはさらに、関与したlatches内の表示名を「退会したユーザー」へ置き換える

読んで気づくことが2つあります。第1に、**消す対象が複数のテーブルにまたがる**。
第2に、全部が「消える」わけではない。latchesは閉じるだけで残るし、
calibration_recordsは消さずに匿名化する。表示名は置き換える。つまり
「消すことの設計」の本体は、実は**「何を残すか」の決定**です。履歴として
残すもの・形を変えて残すもの・完全に消すものを、ひとつずつ仕分けた結果が、
この6項目のリストになっています。

では、なぜ1回のDELETEでは済まないのか。理由は第4章で
学んだ**FK(外部キー)**にあります。おさらいすると、FKとは「この列の値は、
あのテーブルのあの行を指す」という制約でした。LATCHのデータベースでは、
match_candidates は intents へ、latches は group_candidates へ、
calibration_records は latches へ、それぞれFKで繋がっています(第4章4.5)。
FKのある世界で親の行を消すと、子の行は「指す先のない状態」になります。
PostgreSQLはこれを許しません。デフォルトの設定(RESTRICT)では、参照されている
行の削除を拒否するのです。

```text
intents ← match_candidates     (1対1の候補)
group_candidates ← latches     (グループの候補)
latches ← calibration_records  (品質調査の記録)
```

つまり削除には**順序**があります。参照する側(子)から順に片付けていくか、
参照を切ってから消すか。この順序設計こそが、この章の主役です。

## 22.2 削除カスケード: 6段の階段

単発削除と退会のどちらでも必要になる「1Intent分の完全な削除」。その実体は
`backend/src/latch/intents/deletion.py` に、たった1つの関数として書かれています。

```python
async def cascade_delete_intent(
    conn: AsyncConnection, intent_id: uuid.UUID, now: datetime
) -> None:
    """1Intent分の削除カスケード。呼び出し元のtxに乗る(txは開かない)。"""
    await conn.execute(_DELETE_MATCH_CANDIDATES, {"intent_id": intent_id})
    await conn.execute(_DETACH_GROUP_CANDIDATES, {"intent_id": intent_id})
    await conn.execute(_DELETE_GROUP_CANDIDATES, {"intent_id": intent_id})
    await close_latches_on_delete(conn, intent_id, now)
    await conn.execute(_ANONYMIZE_CALIBRATION, {"intent_id": intent_id, "now": now})
    await conn.execute(_DELETE_INTENT, {"intent_id": intent_id})
```

`deletion.py:69` のこの関数を、段ごとに読んでいきます。名前のとおり、
削除は一度に全部を消すのではなく、**カスケード**(滝のように段を落ちるように
連鎖する処理)で進みます。

**第1段: 1対1の候補を消す**

```python
_DELETE_MATCH_CANDIDATES = text("""
    DELETE FROM match_candidates
     WHERE intent_a_id = CAST(:intent_id AS uuid)
        OR intent_b_id = CAST(:intent_id AS uuid)
""")  # 全status・処理済み含む(FR-22・design引用#1-②)
```

match_candidatesはintentsを参照している子なので、親(intent)より先に消します。
ここで注目すべきは、statusによる条件が**ない**ことです。第11章〜第13章で
見たとおり、候補は evaluated・skipped・closed と状態を遷移しながら残りますが、
削除の前には「処理済みだから残す」という区別がありません。仕様のFR-22が
「処理済みを含む全削除」と注文しているためです。自分のIntentが消えたあとに、
その条件で計算された判定値が相手側の記録に残り続けるのは、プライバシー上
まずい——この判断がSQLの形にそのまま出ています。

**第2段と第3段: グループの候補を、参照を切ってから消す**

```python
_DETACH_GROUP_CANDIDATES = text("""
    UPDATE latches SET group_candidate_id = NULL
     WHERE group_candidate_id IN (
         SELECT id FROM group_candidates
          WHERE CAST(:intent_id AS uuid) = ANY(intent_ids))
""")
```

グループ候補(group_candidates)は、latches から参照されています(第15章。
3〜4人の集合から提案を作るとき、latches.group_candidate_id が元の集合を指す)。
そこで、latches側の参照(`group_candidate_id`)をまず NULL にしてから、
group_candidates の行を消します。NULLは「何も指していない」の意味なので、
FKに触れません。閉じたlatchesのこの列を再評価で使うことはない、という
確認の上での処理です。

**第4段: latchesを閉じる(FR-19)**

`close_latches_on_delete` は新規の関数ではありません。第9章・第17章の
世界にいた worker/stage1.py から**中身を変えずに移設**してきたものです。
開いているlatches(candidate・proposed・partial_accept)と成立済みのlatches
(matched)を cancelled へ遷移させ、latch_status_events に
記録を残し、matchedの場合は残った参加者のIntentを復帰させます
(expires_atを過ぎていればexpiredへ、まだならactiveへ。再びマッチングの
対象になります)。誰が消したのかは開示しない——残った参加者に見せるのは
「この提案は成立しませんでした」の一文だけ(03 §7・D-20)。

**第5段: calibration_recordsを匿名化する**

第17章17.7で見たとおり、提案が不成立・成立で確定すると、予測と実際の回答が
calibration_records に記録されます。この行は品質調査のために残したい。しかし
user_idやlatch_idが入ったままでは、消えたはずのユーザーの情報が残ることに
なります。そこで行ごと消すのではなく、**ID系の列だけNULLにする**匿名化を
します。詳しくは22.6で。

**第6段: Intentそのものを消す**

最後に、ようやく親の行です。raw_text(ユーザーが書いた文章)・
structured_data(LLMが構造化したデータ)・embedding(意味の数値)は、すべて
この行の列なので、行が消えれば一緒に消えます。

6段を通して、 **「FKで繋がっている子から順に、親は最後」** という規則が
貫かれています。これは設計者の工夫というより、制約から自動的に決まる順序です。
FKのある世界で削除順序を間違えると、RESTRICTに拒否されてエラーになります。
順序は「美しい設計」ではなく「制約が許す唯一の道」なのです。

もうひとつ、関数の作法を見てください。`cascade_delete_intent` は自分で
トランザクションを開きません。引数で渡された `conn` に、そのまま乗ります。
第17章の回答APIや第20章のチャットと同じ作法です——**トランザクションの
寿命は呼び出し元が決める**。呼び出し元が失敗すれば全部が巻き戻り、
成功すれば全部が確定する。削除の途中で「候補だけ消えてlatchesが閉じ損ねた」
中間状態が残らない構造です。

## 22.3 単発削除は2段ロケット: cancelled遷移 → Event → 物理削除

第6章6.5で、DELETE /v1/intents/{id} を叩いても行は消えず、
`status='cancelled'` へ書き換わるだけだと読みました。そして「将来の削除連鎖は
別単位(M3)で実装される」と書いたと思います。この章が、その予約の回収です。

M3 ws-6で、削除は次の2段構えになりました。

```text
第1段(API)  : cancelled遷移 + EVENT_DELETED発行 …… 従来どおり
第2段(worker): Event処理で cascade_delete_intent 実行 …… 新しい
```

API側の変更はごく小さいです。`intents/service.py:758` の `delete()` は、
遷移の受理範囲(allowed_from)を全statusへ広げただけです。

```python
    async def delete(
        self, *, auth_provider: str, auth_subject: str, intent_id: uuid.UUID
    ) -> None:
        await self._transition(
            ...,
            # 全status受理(matched=FR-19経路・expired=raw_text残存回避・
            # cancelled=冪等再削除。M3 ws-6 design §2.2)
            allowed_from=(
                "draft",
                "active",
                "paused",
                "matched",
                "expired",
                "cancelled",
            ),
            new_status="cancelled",  # Event処理(stage1)で物理削除(M3 ws-6)
            version_delta=0,
            event_type=EVENT_DELETED,
        )
```

いままでは draft・active・paused の3状態しか受理しませんでした。matchedの
Intentに対するDELETEは422で断られていたのです。そのままでは、成立済みの
LATCHに参加しているIntentを消す経路(FR-19)が通りません。expiredも同様に、
期限切れのIntentのraw_textが「預けた意思」として残り続けてしまいます。
そこで全statusへ広げました。cancelledの再受理は冪等性(同じ操作を2回しても
安全なこと)のためです。カスケードの全工程が「0行に影響する」だけで
害がないので、削除済みへの再削除は204を返します。

第2段の受け皿は第9章で読んだ worker のstage1です。EVENT_DELETEDを受け取った
ときの処理が、候補をclosedへ UPDATE する旧実装から、カスケードの呼び出しに
差し替わりました(`worker/stage1.py:277`)。

```python
            if event_type == EVENT_DELETED:
                # 物理削除カスケード(M3 ws-6・08 §2.5。移設済みの
                # close_latches_on_delete含む・cascade内で呼ばれる)
                await cascade_delete_intent(conn, intent_id, now)
```

なぜAPIのトランザクション内で直接カスケードを実行しないのでしょうか。
答えは第9章の設計にあります。LATCHでは「保存の経路」と「波及の経路」を
分けています(09 §2.7)。APIは事実を1行書くことだけに専念し、波及(候補の
評価・削除の連鎖)はEventを経由して裏方が担う。この構成のおかげで、APIの
応答は速く、Workerが詰まっても保存は止まりません。単発削除もこの構成を
踏襲しました。削除完了は通常は数秒(Eventはdebounce対象外の即時処理なので)、
Workerが停止している間だけ遅れます。

**発展的な注意**: この構成には「Event処理までの窓」があります。cancelledに
遷移した直後・Event処理の前の数秒間、Intentの行はまだDBに存在します
(status=cancelledのラベルつきで)。この窓を許容できるかどうかは、次の退会の
設計と対比するとよく分かります。続けます。

## 22.4 退会: 同期トランザクションで全部やる

退会は、単発削除とは逆の判断をしました。**APIのトランザクション内で、
すべてを同期的に実行する**のです。

窓口は `DELETE /v1/users/me` です(`users/routes.py:119` → 
`users/service.py:213` の `delete_account`)。処理の全体像はこうです。

```python
    async def _delete_account(self, *, claims: AccessTokenClaims) -> None:
        ...
        now = self._clock.now()
        cascade = self._cascade or cascade_delete_intent
        async with self._uow() as conn:
            rows = (
                await conn.execute(_SELECT_ALL_INTENT_IDS, {"user_id": row.id})
            ).fetchall()
            for (iid,) in rows:
                await cascade(conn, _coerce_user_id(iid), now)
            await conn.execute(_DELETE_MESSAGES, {"user_id": row.id})
            await conn.execute(_DELETE_NOTIFICATIONS, {"user_id": row.id})
            await conn.execute(_ANONYMIZE_USER, {"user_id": row.id, "now": now})
        # uowコミット後に失効(引用#13。失効が先でも無害・logoutと同じ順序)
        await self._sessions.revoke_access(jti=claims.jti, exp=claims.exp, now=now)
        await self._sessions.revoke_family(sid=claims.sid)
```

順に読むと: 自分のIntentを全部選び、それぞれに `cascade_delete_intent` を
適用し(22.2の6段がIntentの数だけ繰り返される)、自分が送信したmessagesと
自分宛のnotificationsを消し、最後にusers行を匿名化する。これが**1つの
トランザクション**です。応答204が返った時点で、もう生データは消えています。

**単発はEvent経由、退会は同期**。この違いはどこから来るのか。設計書
(ws-6 design §2.3)はこう比較しています。Event経由にすると、Workerが停止中・
隔離中のあいだ、退会したユーザーのraw_textがDBに残り続ける「窓」が開きます。
単発削除なら数秒の窓でも、退会は「アカウントの終了」です。ユーザーが
「消して」と言ったその瞬間に消えてほしい。08 §1の「ユーザーが削除を求めた
とき、預けた意思はシステムから消える」という約束を、Workerの稼働状態に
依存せずに守るには、同期実行しかありません。

とはいえ、削除の中身(何をどの順序で消すか)が2箇所に重複して書かれている
わけではありません。単発・退会のどちらも、実体は22.2の
`cascade_delete_intent` 1本です。経路が2つあっても、**削除範囲の正しさを
検証する場所は1つ**で済む。これが「一元化」の意味です。

最後の2行、`revoke_access` と `revoke_family` は第3章で見たRedisの失効リスト
への登録です(ログアウトと同じ仕組み)。トランザクションがコミットされた
**後**に呼ばれます。順序が逆(失効が先)でも害はないのですが、退会の途中で
エラーになったときに「ログインできないのにデータが残っている」状態を
避けるため、データの消去を先に済ませるのが安全側です。

## 22.5 残るもの: 履歴と、users行の匿名化

退会処理を見て、「あれは消されないの?」と気になった表があるかもしれません。
設計書は残すものを明示的に列挙しています。**blocks・reports・latches・
他人のmessages・users行**です。ひとつずつ理由を読みます。

**blocks・reportsは残す。** 第21章の通り、ブロックは「自分の世界から相手を
外す」記録で、通報は運用側への申し立てです。通報は「受付・記録 → レビュー →
対処」の流れの途中にあるかもしれません。退会を理由に対応途中の記録を消す
根拠はない、という判断です。退会ユーザーとの新しい候補が生まれることも
構造的にありません(候補はactiveなIntent同士から作られ、退会で全Intentが
消えるため)。

**latchesは残す。** 相手側の履歴です。「関与したLATCHは、相手にとっての
履歴として残す。ただし個人との紐付きを切る」——これが08 §2.5の注文です。
紐付きの切断は2つの形で行われます。

1つは、22.2の第4段で見た cancelled への閉じ(チャットは読み取り専用に
なります)。もう1つが、**users行そのものの書き換え**です。

```sql
_ANONYMIZE_USER:
  UPDATE users
     SET display_name = '退会したユーザー',
         profile = '{}'::jsonb,
         auth_subject = 'deleted:' || id::text,
         updated_at = CAST(:now AS timestamptz)
   WHERE id = CAST(:user_id AS uuid)
```

users行を消せない理由は、まさにこの章の冒頭に書いたFKです。blocks・reports・
intents(とlatches詳細の表示)がusersを参照していて、参照されている行は
消せない。すべてを消すには、相手側の履歴ごと消す必要がありますが、それは
「残す」という注文と衝突します。だから**行は残して、中身を替える**。

`display_name` の「退会したユーザー」への置き換えは、latches詳細APIが
users表を結合(join)して表示名を取ってくる構造(第17章17.6)を利用してい
ます。users行を書き換えるだけで、関与したすべてのlatchesの表示が自動的に
「退会したユーザー」に変わる。latches側を1行も書き換える必要がありません。

`auth_subject = 'deleted:' || id` は、ログインの遮断です。認証は
auth_provider と auth_subject の組でusers行を探します(第4章)。書き換え後の
subjectは「deleted:元のUUID」という、どのIdPも決して発行しない文字列なので、
同じGoogleアカウントで再ログインしても、この行には**決して紐付かない**。
新規ユーザーとして初回登録からやり直すことになります。列を1つ追加して
「退会済みフラグ」を作る代わりに、既存の列の値を替えるだけで実現できる
工夫です(設計書はこの判断のトレードオフも記録しています。フラグ列の方が
明示的だが、マイグレーションと照合処理の変更が要る。ベータ規模では
書き換えで十分、と判断されました)。

## 22.6 30日定期削除: 掃除のジョブ

ここまでの削除は、すべてユーザーの注文(削除要求・退会)が起点でした。
FR-22は、注文が来なくても動く掃除も注文しています。**評価が確定してから
30日経った候補(match_candidates・group_candidates)を、毎日消す**。

なぜ候補を溜めておけないのか。第17章で見たとおり、候補行には両者の条件で
計算したスコア(jev_result・latch_score)が入ります。評価が確定した時点で、
品質調査に必要な値はcalibration_recordsへ移っています(第17章17.7)。だから
候補を30日以上残しても、得られるものはなく、失うものはあります。削除済み
ユーザーの条件由来の判定値が、相手との候補行に残り続ける——これが
「削除したのに消えていない」と同じ状態になる経路だからです。

実装は `backend/src/latch/worker/retention.py` の `RetentionJob` です。
中身は、第18章で読んだリセットジョブ(ResetJob)と同じ形をしています。
JSTの0時まで眠り、起きたら `run_once` を1回。失敗したら5分後に再試行。

```python
    async def run_once(self) -> tuple[int, int]:
        """0時発火の本体: 窓で空になるまで削除。戻り=(group, match)件数。"""
        cutoff = self._clock.now() - timedelta(days=RETENTION_DAYS)
        groups = matches = 0
        async with self._engine.begin() as conn:
            while True:
                params = {"cutoff": cutoff, "batch_limit": self._batch_limit}
                await conn.execute(_DETACH_GROUP_LATCHES, params)
                n = (await conn.execute(_DELETE_GROUP_BATCH, params)).rowcount
                groups += n
                if n < self._batch_limit:
                    break
            ...
```

`cutoff`(切る日時)= 今 − 30日。これより前の updated_at を持つ行が
削除対象です。ここで2つの読みどころがあります。

**1つは、削除が500件ずつの窓で行われること**(`batch_limit`)。1回のDELETEで
何十万行も消すと、その間ロックが長く続いて、他の処理を待たせます。500件
消しては、まだ残っていれば次の500件、と繰り返す「窓」であれば、各周の
ロックは短くて済みます。ループの終わりの条件が「削除件数が窓より小さかった
ら(=もう対象がない)」なのも、件数を数えるための特別な問い合わせを
足さない、最小の作りです。

**もう1つは、latches はここでも消さないこと**。対象は2つの候補テーブル
だけです。latchesは相手側の履歴(22.5)なので、定期削除の対象から外れて
います。ただしgroup_candidatesを消す前には、例のFK解消(latchesの
group_candidate_idをNULLに)が入ります。22.2の第2段と同じ理屈が、
スケジュールされた掃除のなかでもう1回出てくるわけです。

**Statusやpendingの区別もありません。** 仕様は「評価確定から30日」と言って
いますが、実装は「updated_at が30日より前なら全status一律」です。pendingの
まま30日経った行も消します。Intentの寿命は数日(03 D-19)なので、30日も
前に作られたpending候補の起点Intentは、もう確実に失効しています。厳密に
「評価確定時刻」を追いかけるよりも、一律判定のほうが安全側(削除しすぎる
リスクがない)に立っている、という判断です。

## 22.7 匿名化の第一段: IDを切る、粒度は下げない

最後に、22.2で先送りにした第5段、calibration_recordsの匿名化を読みます。

calibration_recordsは「予測は当たったか」を記録する表でした(第17章17.7)。
提案スコア(matchedの予測)と、実際の回答(actual_responses)。品質を測り、
しきい値の調整に使うための、LATCHの学習データです。この表は削除対象では
**ありません**。匿名化対象です。なぜなら、削除されたユーザーが関わった
提案の記録も、統計としては価値があるからです。「0.85のスコアの提案は
実際に成立しやすいか?」という問いに答えるには、数が必要です。

しかし user_id が入ったままでは、統計の材料として使いにくい。そこで
D-13(設計判断13番)は匿名化を**2段階**で行うと決めました。

```python
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
""")
```

**第一段(この単位で実装)**: latch_id と intent_ids をNULLにして、
actual_responses(JSON配列)の各要素から user_id キーを除去します。回答の
中身(yes/no/defer)と時刻はそのまま残します。**IDと結びつく糸を切る**のが
第一段です。

**第二段(将来の運用設計)**: 時刻を日単位に丸め、条件サマリの粒度を下げ、
anonymized_at に日付を立てます。**「誰か」を特定できない粒度まで落とす**のが
第二段です。第二段は月次バッチとして、運用設計に委ねられています。

なぜ最初から両方やらないのか。理由は匿名化の難しさにあります。
「時刻を日単位に丸める」「時間帯を昼/夕/夜の3つに丸める」のような粒度の
低下は、どういう丸め方なら再識別(絞り込みで個人を特定すること)を防げるか
という運用上の判断を要します。削除・退会への即時対応(第一段)と、
じっくり決めるべき丸め設計(第二段)を分ければ、前者だけを先に、確実に
実装できるのです。anonymized_at(匿名化済みの印)は第二段のバッチが
立てるので、第一段のあとの行は「IDは切れたが、まだ丸めていない」状態です。
CHECK制約(anonymized_at がNOT NULLならID系はNULL)は、この中間状態を
許すように最初から作られていました(0001)。

SQLの技巧をひとつだけ見ておきます。actual_responses はJSONBの配列です。
「配列の各要素からキーを1つ消す」は、普通のUPDATEの書き方では書けません。
このSQLは、`jsonb_array_elements` で配列を行にほどき、`e - 'user_id'`
(JSONBからキーを引く演算)でuser_idを消し、`jsonb_agg(... ORDER BY ord)`
で元の順序を保ったまま配列に戻しています。WITH ORDINALITY は「ほどいた
行に番号を付ける」構文で、順序保存の鍵です。JSONB関数の合わせ技は、
第20章のlatches.responsesを読んだときにも出てきました。 PostgreSQLの
JSONBは、中身をいじるための小さな関数言語を持っている、と覚えると
今後も読みやすくなります。

## 22.8 この章の再統合

- 「消す」の設計は「何を残すか」の設計だった。残す履歴(latches)・形を替えて
  残す記録(calibration_records・users行)・完全に消す生データ(Intent・候補)を
  仕分けた結果が、削除範囲のリスト
- 削除の順序はFKが決める。子を消してから親を消す、参照を切ってから行を
  消す。6段のカスケードは制約が許す唯一の道を歩いている
- 単発削除と退会で経路を分けた。単発は第9章のEvent経路の構成を維持し、
  退会は応答時完結を優先して同期トランザクションにした。どちらも実体は
  `cascade_delete_intent` 1本なので、正しさの検証場所は1つ
- 退会後もusers行は残る。参照(FK)があるから。代わりに表示名を置き換え、
  auth_subjectを「deleted:」付きに書き換えてログインを遮断する
- 30日定期削除(RetentionJob)は、注文を待たない掃除。品質調査に必要な値は
  calibration_recordsに移っているので、候補は30日で消してよい
- 匿名化は2段階。第一段(この単位)はID系をNULLにして糸を切るだけ。
  粒度を下げる第二段は運用設計に委ねられ、anonymized_atはそのとき初めて
  立つ

削除・退会を実装したM3 ws-6は、マイグレーションを1つも追加しませんでした。
既存の列と制約の範囲で、すべての注文を実現できたからです。設計書はこれを
「YAGNI(必要になるまで作らない)の切り捨て一覧」として記録しています。
何を追加しないかまで設計書に書く——第7章・第8章で見たのと同じ作法が、
「消す」という最後の大きな機能にも貫かれています。

## 22.9 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| 削除カスケード | 1つの削除が、参照される側から順に連鎖して複数表を片付ける処理 |
| 物理削除 / 論理削除 | 行を本当に消すこと / 消した印を付けて残すこと |
| RESTRICT | FKで参照されている行の削除・更新を拒否する、PostgreSQLの既定の動作 |
| 単発削除 / 退会 | Intentを1つ消すこと / アカウントごと全部消すこと |
| FR-19 / FR-22 | 成立済みLATCH参加Intent削除の規定 / 候補の全削除と30日定期削除の規定 |
| 匿名化(第一段/第二段) | ID系をNULLにして紐付きを切ること/粒度を下げて再識別を防ぐこと |
| 再識別 | 統計データの絞り込みで、個人が特定されてしまうこと |
| jsonb_array_elements / jsonb_agg | JSONB配列を行にほどく/行から配列へ戻す関数 |
| WITH ORDINALITY | ほどいた行に通し番号を付けるSQL構文(順序保存に使う) |
| RetentionJob | 30日経過候補をJST 0時に日次削除するworkerのジョブ |
| batch_limit | 1回のDELETEの最大件数。ロックを短く保つ窓 |
| YAGNI | 必要になるまで作らない、という設計の節約原則 |

## 22.10 確認問題

1. cascade_delete_intent の6段の順序を、FKの参照関係から説明してください。
   第2段(latches.group_candidate_idのNULL化)を飛ばすと何が起きますか
2. 単発削除と退会で、なぜ経路(Event経由/同期)を分けたのか、それぞれの
   要求を一言ずつで述べてください
3. 退会したユーザーと同じGoogleアカウントで再ログインすると何が起きますか。
   auth_subjectの書き換えと初回登録の関係で説明してください
4. RetentionJobはlatchesを消しません。またpendingの候補も30日で消します。
   それぞれの理由を説明してください
5. calibration_recordsの匿名化で、第一段が「IDを切るだけ」にとどめた理由は
   何ですか。第二段に委ねられたものは何ですか
6. 削除済みのIntentにDELETEを再送すると、エラーにならず204が返ります。
   どの性質(この教科書で何度も出た言葉)が働いているか、カスケードの
   各段で確認してください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった
部分が、読み返すべき節です)
