# 第27章 使っていい条件と出していい条件を、形で固定する: 同意とログ許可リスト

- 種別: 解説(通読して理解を積む章)
- 前提知識: 第5章(LLM境界・422と503)・第6章(draftとactiveの検証切替)・
  第11章(Layer 1 Hard Filter)・第19章(構造化ログlatch.push.send)・
  第23章(設定画面)。第10章(SendRecord)が読めていれば27.6はさらに腹に落ちます
- この章を読み終えるとできるようになること:
  - 「LLM送信への同意」が、DBの1列(users.consent_at)と2層の防御
    (APIの422遮断+Layer 1のSQL条件)で担保される構造を説明できる
  - draft(下書き)だけが同意なしで保存できる理由(LLM送信ゼロ)を説明できる
  - 構造化ログの「許可リスト」が、レコード型(Pydanticモデル)と登録表、
    そしてtest-ciで毎回走る機械検査で守られる仕組みを説明できる
  - 新しい構造化ログを書く人が「登録しないと試験が落ちる」規律に乗っていることを
    説明できる
- 次に読むもの: `labs/lab13-consent.md`(この章の構造を、curlで一巡して
  自分の目で確認します)

## 27.1 二つの「してはいけない」を、約束でなく形で守る

この章で扱う単位(M4 ws-5)は、一見まったく別の2つのものを作りました。

1つは**告知・同意フロー**です。LATCHはあなたが入力したIntentの内容を、マッチング判定の
ために外部のAIサービスへ送ります(第5章・第10章・第13章で読んだ、parse・Embedding・
Jevの呼び出しです)。個人データを外に出すのですから、何をどこへ送るのかをあらかじめ
告げて、利用者に同意を取らなければなりません。そして「同意していない人」のIntentは、
送信も、マッチングの相手になることも、させない。

もう1つは**構造化ログの許可リスト**です。プログラムは動作の記録(ログ)を残しますが、
その記録に利用者の原文や位置情報のような機微なものを混ぜてはいけません。LATCHの仕様
(08 §2.4)は「ログに出してよいフィールドを列挙して固定し、許可リスト外は出す段階で
落とす」と決めています。

この2つは、守るべき決まりが違うのに、守り方が同じです。**決まりを人間の記憶と
注意(約束)に預けず、コードの形(型・条件・機械検査)に落として、破れたら試験が
落ちるようにする**。この教科書で何度も出てきた作り方——第19章「文面をtypeしか
受け取れない関数に縛る」、第24章「規律は文書でなくテスト(番人)で守る」——の、
もう1つずつの実例です。

## 27.2 同意は1列で持つ: users.consent_at

まず、同意をどこにどう記録するかからです。

選んだ答えは、users表に1列足すことでした。マイグレーション(第1章1.7の「DB定義の
バージョン付き変更」)0007がそれです。

```python
# backend/alembic/versions/0007_users_consent.py:26-31
def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN consent_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS consent_at")
```

この列は**NULLでよい**(is_nullableがYES)。NULL(何も入っていない)は「まだ同意して
いない」、日時が入っているは「同意した」という意味です。決まりはたった1つ、
**「consent_atがNULLでなければ同意済み」**。この判定式に全部を一元するので、
あとの章で出てくる2層の防御は、どちらも同じ1文を参照するだけになります。

「同意の履歴を全部持つ表を作ったらどうか?」という考えもありました(いつ同意したか、
どの文面に同意したか、を1行ずつ追記していく形)。でも今回見送りました。なぜなら、
仕様(D-14)は「同意を取ること」と「設定画面で再確認できること」だけを定めていて、
履歴や文面のバージョン管理は規定していないからです。決め手のない機能を推測で
作り込むのはYAGNI(まだ要らないものは作らない)の判断です。撤回も簡単です——
仕様が将来「履歴が必要」と改版されたとき、1行の記録はconsentsという新しい表に
移せます。先に1列で始めておけば、退化させる手間だけですむ。

ここにはもう1つ、小さくて大事な判断があります。**既存ユーザーに同意の日時を
後から入れて回らない**(バックフィルしない)。既存のユーザーは開発・試験環境の
ものですが、「2026-09-15に同意した」という列に誰も同意していない日時を書き込むのは、
同意の取得日時を**捏造する**ことだからです。必要な環境は、後述の同意APIを実際に
叩いて日時を取ります。

そして、この単位で明確になった取り決めを1つ。**同意の取り消しは作りません**。
仕様は取得と再確認だけを定め、取り消しの規定がありません(最小構成の原則)。取り
消しを将来作るなら、先に仕様(08 D-14)を改版する——「コードが仕様に先行して
決めごとを作らない」順序の逆転を、ここでも避けています。

## 27.3 使う入口を閉じる: 422 CONSENT_REQUIREDと遮断3経路

同意の列ができても、それが守られなければ意味がありません。第1層の防御は、
**同意していない利用者のリクエストをAPIの窓口で断る**ことです。

断るときの応答は422(利用者起因の失敗)で、エラーcodeは`CONSENT_REQUIRED`です。
第5章5.4で学んだ「422は利用者に別の案内をすべき失敗」という区別に沿っています。
422ならクライアントは「同意を取る画面へ誘導」という分岐ができて、503の「少し待って
再試行」とは扱いが違う。しかもcode文字列(`CONSENT_REQUIRED`)まで契約に含めてある
ので、フロントエンドは応答の中身を見て機械的に誘導を決められます。

遮断する経路は3つです。選ぶ基準は「**LLMへの送信や、マッチングに乗る記録が生まれる
入口を全部閉じる**」です。

1. **parse**(`POST /v1/intents/parse`)——LLM送信の最上游。同意なしでパースさせる
   のは「同意していない送信」そのもの
2. **active作成**(`POST /v1/intents`・statusがactive)——保存と同時にEventが発行され、
   Embedding(Gemini送信)へつながる経路の起点
3. **draft→active化**(`PATCH /v1/intents/{id}`)——下書きを確定へ移す入り口。
   遮断がなければ、先にdraftで溜めておいたものが一気にマッチングへ流れる

で、**draft(下書き)の保存は、同意がなくても通ります**。ここが面白いところです。
なぜか。draftはLLMを呼ばず、Eventも出さない。「構造化されたデータへの変換(LLM送信)
に同意していない」のに対して、「書きかけの文章を自分の棚にしまう」のは送信を
一切伴わないからです。設計書(design §2.2)はここを「LLM送信なし・ユーザーが同意後に
預けられる」と記録しています。同意ゲートは「何かを全部禁じる」のでなく、**送信の
ある入口だけを禁じる**。自分が守っているものの境界線を明確に持っておくと、
禁止の線もまっすぐ引ける——好例だと思います。

コードでは、検査はServiceの1つの関数にまとめられています。

```python
# backend/src/latch/intents/service.py:309-313
    @staticmethod
    def _require_consent(user: UserRow) -> None:
        """同意なしのactive化を遮断(08 D-14・設計§2.2第1層)。draftは許可。"""
        if user.consent_at is None:
            raise ConsentRequiredError("consent required")
```

`user.consent_at is None`——27.2の判定式そのものです。active作成のService
(service.py:459)と、draft→active化のService(service.py:713)が、どちらも
この関数を呼びます。parseのService(service.py:144-157)は、user_idが分かっていて
同意していない場合に同じエラーを投げます。

```python
# backend/src/latch/intents/service.py:144-157
        if user_id is not None and self._consent_lookup is not None:
            # 同意検査は登録済みユーザーのみ(未登録JWT経路はg1gate資産と
            # M1確定のとおり現行挙動 — 計画§4-2)。LLM送信前に遮断する
            try:
                consented = await self._consent_lookup(user_id)
            except IntentsError:
                raise
            except Exception as exc:
                raise DependencyUnavailableError(
                    "intent parse dependency unavailable"
                ) from exc
            if not consented:
                raise ConsentRequiredError("consent required")
```

**LLMを呼ぶ前に**検査が終わる点が重要です。送信してから断るのでは、遅すぎます。
「送信の最上游で止める」がこの検査の立地です。

## 27.4 最後の関所でも落とす: Layer 1の1行

「APIを全部閉じたのなら、もう十分では?」——ここからが第2層です。

第11章11.3で、飲酒Intentの二重防御を覚えていますか。API側で19歳の飲酒Intentは
422 UNDER_AGEにするのに、Layer 1(Hard Filter)でも「20歳未満は落とす」SQL条件が
動いていました。理由は「APIの検証をすり抜けたデータがDBに入っていたとき、最後の
関所でも落とせるように」でした。同意も同じ構造に乗ります。開発者が直接SQLで
INSERTしたデータ、試験用の手動投入——**正規の窓口を通らないデータ**は、APIの
遮断では止まりません。だからマッチングの相手を選ぶSQL(Layer 1)にも、同じ条件を
1行足しておく。

```python
# backend/src/latch/worker/matching/layer1.py:45-56(LAYER1_WHEREの前半)
    AND i.embedding IS NOT NULL
    AND i.user_id <> CAST(:origin_user_id AS uuid)
    AND i.category_primary = :origin_category
    AND i.time_start < :origin_time_end
    AND :origin_time_start < COALESCE(i.time_end, i.time_start + interval '3 hours')
    AND i.geo_center IS NOT NULL
    ...(距離・予算の条件が続く)...
    AND u.consent_at IS NOT NULL
```

最後の1行が第2層です。27.2の判定式が、また出てきました。ブロックや飲酒年齢と
同じく、SQLのWHERE句に直接書かれているので、この検索から同意していない相手が
1件も出てきません。第11章11.2で「8条件」だった表に、この1行で**9つ目の条件**が
加わったことになります。Layer 2(並べ替え)はLayer 1のWHERE文字列をそのまま
使うので(11.2)、第2層はこの1行だけで自動的に2か所に効きます。

2層の内訳を、表で並べ直します。

| 層 | 場所 | 防げるもの | 防げないもの |
|---|---|---|---|
| 第1層: API遮断 | parse・active作成・active化のService | 正規窓口でのLLM送信とEvent発行 | 窓口を通らないデータ(DB直書き) |
| 第2層: Layer 1条件 | peer抽出のSQL | 窓口を通らないデータがマッチング候補になること | 候補には出ない評価起点の処理そのもの |

「防げないもの」の列を見ると、2層を合わせても「DB直書きで評価の起点(Event)を
直書きした場合、Embedding送信を含む評価が1回走る」という隙間が残っています。
これは検討のうえ、明示的に**将来課題に残しました**(origin前段スキップの省略)。
API遮断があればEvent自体が発行されないし、Layer 1で候補にはならない。「あと1本
防御を足す」ときに守れるものと試験が増える量を比べて、今は足さない——この章の
単位にも、YAGNIの判断が2か所目として入っています。

## 27.5 同意の窓口: /v1/users/me/consentと2つの画面

同意を記録するためのAPIの窓口は2つ、`POST /v1/users/me/consent`と
`GET /v1/users/me/consent`です。どちらも本人のアクセストークン(認証)が
必要で、応答は`{"consented_at": "..."}`の1キー。未同意の状態でGETしても200で
`{"consented_at": null}`を返します(404にしない)。状態を聞くことと、同意して
いないこと、を区別するためです。行そのものがない(未登録ユーザー)場合は404。

記録のSQLはこの1本です。

```python
# backend/src/latch/users/consent.py:167-174
# auth_provider+auth_subjectはUNIQUE(0001)なので高々1行。後勝ち=常にUPDATE
_UPDATE_CONSENT = text("""
    UPDATE users
       SET consent_at = CAST(:now AS timestamptz),
           updated_at = CAST(:now AS timestamptz)
     WHERE auth_provider = :provider AND auth_subject = :subject
    RETURNING consent_at
""")
```

INSERTでなくUPDATE1本なのが肝です。auth_providerとauth_subjectの組は第4章で
見たUNIQUE制約で1行しかないので、同意ボタンを何度押しても行が増えず、日時だけが
「あとに押したほう」で更新される(**後勝ち**)。挿入と更新の分岐を書かない——
単純な決まりほどSQLを1本にして済ませる、という定石の例としても読めます。
時刻は`self._clock.now()`、つまりClock(第3章)由来の値だけが入ります。

画面側は2か所です。

1. **オンボーディング同意画面**(`#/onboarding`)——文言表示・チェックボックス
   (未チェックならボタン無効)・`POST /v1/users/me/consent`・同意済みなら
   現状表示。接続直後にGETで未同意と分かった場合と、422を受け取った場合の
   2本の誘導経路でここへ来ます
2. **設定画面の第3セクション**「LLM送信の同意」——いつでも文言と同意日時を
   再確認できる(D-14「設定画面から常に再確認できる」)。こちらは表示専用で
   ボタンはありません

誘導経路の2本目がコード的に美しいところです。フロントの全Intent操作は1つの
`client.call`を通ります(第8章)。main.jsのその1か所で422のcodeを見て、
`CONSENT_REQUIRED`ならどの画面からの操作でも同意画面へ飛ばせる。

```javascript
// frontend/src/main.js:23-37(骨格)
// 422 CONSENT_REQUIREDの横断検出(誘導経路(b))。全Intent操作はこのcallを
// 通るため、個別画面の変更なしで全経路を拾う。
...
      if (err?.status === 422 && err?.code === "CONSENT_REQUIRED") {
        consent?.notifyRequired();
```

個別の画面9つ(第23章の設定など含む)に誘導コードを書くのではなく、**全員が
通る関所1か所に置く**。Layer 1が「条件を1つのWHEREに集める」設計だったのと、
同じ感覚です。

なお、この第3セクションは既存の「通知許可」セクションと**別物として分離**して
あります。見た目は似ていますが、通知許可(D-18)はOS・ブラウザの通知機能の
許可で、LLM送信の同意(D-14)は外部AIサービスへの送信許可。根拠の規定も効果も
違うものを1つの「許可」として混ぜると、利用者は何を許したのか分からなくなる。
設定画面の作法(第23章)に3つ目のセクションが増えた形ですが、増える位置の
判断は「利用者が混同しないこと」でした。

## 27.6 出していいものを型で固定する: 構造化ログの許可リスト

後半はログです。まず仕様(08 §2.4)の決まりを並べます。

1. 構造化ログ(JSONレコード)は**許可リスト方式**。出力できるフィールドを列挙型で
   固定し、**許可リスト外のフィールドはSerializerの段階で落とす**。raw_text・
   geo座標・constraints系は不許可
2. Analyticsは集計値のみ
3. 例外・エラーはIDのみ(ロガー名つきのprintf型ログはこれの運用)

「許可リストを作る」と聞いてデータベースや新しい仕組みを想像したあなたに
朗報があります。**LATCHの構造化ログは、すでに許可リスト方式でした**。第10章と
第13章で読んだ`SendRecord`(latch.llm.send。フィールドはoccurred_at/system/
destination/status/error_code/intent_ids/user_id)も、第19章で読んだ
`PushSendRecord`(latch.push.send。title/bodyはtemplatesの固定文面だけ)も、
**「この型のフィールド以外は存在できない」**というPydanticモデルの形で、
出てよい情報が固定されていました。08 §2.4が書かれたときと実装が構造的に
一致していたのです。

ではこの単位で何をしたのか。**実装済みの2本を「許可リスト済み」として登録する
表を作り、以後の新規ログに「登録必須」の規律を機械で強制した**のです。
`latch/core/logallow.py`という1ファイルです。

```python
# backend/src/latch/core/logallow.py:19-51(骨格)
ALLOWED_STRUCTURED_LOGS: dict[str, type[BaseModel]] = {
    "latch.llm.send": SendRecord,  # 08 §3 送信記録(M0 ws-2)
    "latch.push.send": PushSendRecord,  # 10 §1 FR-21検証用(M3 ws-3)
}

FORBIDDEN_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "raw_text",
        "geo",
        "lat",
        ...
        "structured_data",
    }
)

def serialize_allowed(logger_name: str, record_dict: dict) -> BaseModel:
    """許可リスト外のキーを落としてから型へ渡す(新規ログ用の規律の入口)。

    08 §2.4「許可リスト外のフィールドはSerializerの段階で落とす」の実体。
    未登録ロガーはKeyError(登録必須を呼び出し側に強制する)。
    """
    model = ALLOWED_STRUCTURED_LOGS[logger_name]
    known = set(model.model_fields)
    return model.model_validate({k: v for k, v in record_dict.items() if k in known})
```

3つの部品があります。

- **ALLOWED_STRUCTURED_LOGS**(登録表)——「ロガー名 → レコード型」の対応が
  全部載った辞書。ここが**単一真実**で、G4(ゲート検査)の「許可リスト一覧」
  という証拠になり、問い合わせ時に送信記録を開示する資料の雛形にもなります
- **FORBIDDEN_FIELD_NAMES**(不許可の列挙)——raw_text・geo座標・constraints系
  を、機械が照合できる語として列挙したもの
- **serialize_allowed**——将来の新規ログが使う入口の関数。登録表にないロガー名は
  KeyErrorで落ちる

これだけなら「表を書いただけ」です。この章の締めくくりは、その表が**壊されたら
自動で見つかる**という仕組みです。`tests/unit/test_log_allowlist.py`に機械検査が
5本あり、test-ciで毎回走ります。3本目が最も分かりやすい。

```python
# backend/tests/unit/test_log_allowlist.py:52-72(骨格)
def test_serialize_allowed_drops_forbidden_keys():
    """機械検査3: 不許可キーを混ぜたdictが出力JSONに現れない
    (=「Serializerの段階で落とす」08 §2.4の挙動試験)。"""
    raw = {
        "occurred_at": NOW,
        ...
        "raw_text": "今夜 天文館で飲みたい",
        "geo": {"lat": 31.5965, "lon": 130.5581},
        "constraints": ["19時以降"],
        "structured_data": {"x": 1},
    }
    record = serialize_allowed("latch.llm.send", raw)
    ...
    dumped = record.model_dump_json()
    assert "raw_text" not in dumped
    assert "今夜 天文館で飲みたい" not in dumped
```

許可フィールドと不許可フィールドを**わざと混ぜた辞書**を作り、serialize_allowedに
通し、出力に不許可の中身が現れないことを確かめます。「落とす」という挙動そのものを
試験している。残る4本は、登録表の各型が不許可列挙と衝突しないこと・records.pyの
型が登録表に載っていること(import突合——**新規にSendRecord型を作ったら登録必須、
という規律の強制点**)・未登録ロガーがKeyErrorになること・PushSendRecordの
title/bodyがtemplatesの固定定数のみであること。

ここに、第24章の「規律は文書でなくテスト(番人)で守る」が、新たな形で現れて
います。番人までが「既存の振る舞いが壊れないこと」を見張っていたのに対し、
この試験は**「これから書く人が守るべき手順」を強制する**。誰かが新しい構造化ログを
latch.legacy.sendなどと作っても、登録表に載せなければ試験が赤になります。表への
登録を強制されるから、G4の証拠と開示資料も自動で最新を保てる。書く人に手順を
覚えてもらうのではなく、試験が手順を待ち構えている——**教える代わりに、忘れたら
落ちる世界を作る**のが、この仕組みの本質です。

printf型のモジュールログ(latch.usersやlatch.intentsの、人間が読む1行ログ)は
この許可リストの対象外です。仕様(08 §2.4第3規定)が「例外・エラーはIDのみ」
という運用として区別しているからで、設計時に現存するログを全部調べて、raw_text等を
引数に取るものがないことが確認されています。変えるべきでないものを、そのままに
しておくのも設計の仕事です。

## 27.7 この章の再統合

この章の単位(M4 ws-5)を、1つの問いに置き換えるとこうなります。
**「決まりは、どこに持つか」**。

- 同意の決まりは「consent_at IS NOT NULL」の1式に持つ。APIは422で断り、Layer 1は
  SQLで落とす。2層は同じ判定式を参照するので、決まりが1つでも守り手が2つに増える
- ログの決まりは「型のフィールド+登録表+機械検査」に持つ。破ったら赤、守れば
  証拠が自動でたまる
- どちらも「約束(文書と人の注意)」に持たない。文書は決まりの**根拠**(08 D-14・
  08 §2.4)としては必要だが、守り手にはならない

そして、守り手がすべてコードの形をしていることの副産物として、この章で扱った
判断(YAGNIの2件・明示残置の1件)はすべて「変えたいときの入口」が明確です。
履歴が要るなら新表へ、取り消しが要るなら仕様を先に改版してから、origin前段の
スキップが要るなら1本足して試験も1本足す。**後から変えられる形に、最初から
なっている**。

なお、users表へのconsent_at追記や`CONSENT_REQUIRED`のcode追加は、仕様書
(docs/05)への反映を待つ「docs改版候補」として記録されています(設計§3.4)。
実装が先行するときも、仕様側に追いつかれるべき箇所を明示して残す——この
プロジェクトの慣行(00 運用ルール7)が、ここでも機能しています。

## 27.8 用語集(この章で登場した言葉)

| 用語 | 一言でいうと |
|---|---|
| 告知・同意フロー | 外部送信の内容をあらかじめ告げ、利用者の同意を取る流れ(D-14) |
| consent_at | users表の1列。NULL=未同意・日時=同意済み。判定式は「IS NOT NULL」 |
| マイグレーション0007 | usersにconsent_at列を足すDB定義の変更 |
| CONSENT_REQUIRED | 同意がないことを示す422のエラーcode。フロント誘導分岐の契約 |
| 遮断3経路 | parse・active作成・draft→active化。LLM送信とEvent発行の入口 |
| draft許可 | 下書き保存はLLM送信ゼロのため同意なしでも通る判断 |
| Layer 1の同意条件 | peer抽出SQLの`AND u.consent_at IS NOT NULL`(9つ目のHard条件) |
| 二重防御 | APIの入り口の利便性と、Layer 1の出口の正しさ。第11章の飲酒年齢と同型 |
| 同意API | POST/GET /v1/users/me/consent。冪等・後勝ち・未同意も200 null |
| オンボーディング画面 | #/onboarding。チェックボックスと同意ボタンのある画面 |
| 横断検出 | main.jsのclient.callの1か所で422のcodeを見て全画面を誘導する作り方 |
| 許可リスト | 構造化ログに出してよいフィールドの列挙(08 §2.4) |
| logallow.py | 登録表+不許可列挙+serialize_allowedの1ファイル(単一真実) |
| SendRecord / PushSendRecord | 既存の2つの構造化ログの型。型のフィールド固定=許可リスト |
| serialize_allowed | 許可リスト外のキーを落としてから型へ渡す入口の関数 |
| G4 | マージの前に取るゲート検査の1つ。「同意とログ許可リスト有効」の証拠を集める |

## 27.9 確認問題

1. 「同意していない利用者のdraft保存が許可される」理由を、LLM送信との関係で
   説明してください
2. APIの遮断(第1層)だけでなく、Layer 1のSQL条件(第2層)を足した理由を、
   「APIを通らないデータ」を例に説明してください
3. 同意APIがINSERTでなくUPDATE1本で済むのはなぜですか。auth_providerと
   auth_subjectのどの性質に依存していますか
4. 構造化ログの許可リストは、この単位で「新しく作った」のでしょうか、
   「実装済みのものを一元化した」のでしょうか。2型のレコード型を例に説明してください
5. 新しい構造化ログを作った人が登録表への登録を忘れたとき、何が起きますか。
   それはどの試験が検知していますか
6. この章の単位でYAGNI(まだ作らない)と判断されたものを2つ挙げ、それぞれ
   「変えたくなったときの入口」がどこになるか説明してください

(解答例は用意していません。自分の言葉で答えられたら合格です。答えに詰まった部分が、
読み返すべき節です)
