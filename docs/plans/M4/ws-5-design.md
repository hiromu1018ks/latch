# M4 ws-5(告知・同意フロー+ログ許可リスト)設計メモ

- 作成: 2026-10-03(agent1)
- 前提: M4 ws-1(マージ165242e)・ws-2(マージ398375c)完了・マージ後main
  test-ci 1561 passed・alembic head=0006。**本単位はマイグレーション追加あり(0007)**。
  wave2はws-3∥ws-5の並行予定だったが、**2026-10-03 supervisor裁定で実装順序を
  直列化**: ws-5を先に実装・マージし、ws-3の実装worktreeはws-5マージ後のmain
  (head=0007)から作る(運用ルール1のtest-ci同時実行禁止もこれで自然に満たる。
  §6-3)
- 参照仕様: 08 D-14(告知・同意・契約条件)・08 §2.4(ログ・計測・Analyticsからの排除)・
  08 §3(LLM送信のプライバシー)・12 M4-6・08 §2.6(FR-21・通知許可との区別)・
  08 D-13(退会同意文・本単位では触らない)・05 §2(users表)・05 §5(API・エラーcode)・
  03 §2(画面一覧)・03 D-18(通知許可)
- **確定**: 同意文言=2026-10-03ユーザー承認(§2.4)・取り消し非実装=2026-10-03
  ユーザー了承・並走調整=直列化(§6-3)・エラーcode=CONSENT_REQUIRED・origin前段
  スキップ省略=明示残置 — **supervisor承認事項5件とも裁定済み(§6)。未解決の論点なし**

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

12 M4-6「告知・同意フロー(オンボーディング・LLM送信の明示・設定画面からの再確認。08 D-14)
と構造化ログの許可リスト(08 第2.4節)」を実装する。G4チェックリストの
「告知・同意とログ許可リスト有効」(12 M4完了条件)の実体。作るものは4つ:

1. **同意の記録とAPI**: オンボーディング同意をusersに記録し、APIで取得・記録できる
2. **マッチング利用条件化**: 同意なしユーザーのIntentがパースされず・activeにならず・
   候補に出ないこと(§2.2 — 引継ぎ5への回答)
3. **フロントエンド**: オンボーディング同意画面(#/onboarding)+設定画面の再確認ブロック
   (D-14「設定画面から常に再確認できる」)
4. **許可リスト機構**: 構造化ログの許可リスト登録表と機械検査(§2.5 — 既存の
   型固定ログを「許可リスト済み」として登録・一元化する。引継ぎ3への回答)

### 1.2 引用確定値(節番号つき)

- **08 D-14(告知・同意)**: オンボーディングで「Intentの内容は、マッチング判定のために
  外部のAIサービスへ送信されること(**送信先の名称を含む**)、送信範囲は判定に必要な
  最小限であること、学習には利用されないこと」を明示し、同意を取得する。**同意は
  マッチング機能の利用条件**であり、設定画面から常に再確認できる。送信記録(§3)は
  問い合わせ時に開示する。→ 文面に含むべき要素はこの3要素+送信先名称。取り消し・
  同意履歴・同意バージョンの規定は**ない**(§2.1の選択根拠)
- **08 §3(送信先の確定値)**: 送信先は「JevがTypeSafe AI(api.typesafe.ai・サーバーは
  米国)」「ParserがAnthropic Claude API」「EmbeddingがGoogle Gemini API」「Jevの
  フォールバックがAnthropic Claude API」。LLM Gatewayは送信先・データ種別・時刻を記録し
  **この記録は内容を含まない**。→ UI文言に列挙する送信先3社のdocs根拠。フォールバック
  (Anthropic)は第一候補と同一社のため名称上は3社
- **08 §2.4(3規定)**: ①構造化ログは**許可リスト方式**。出力できるフィールドを列挙型で
  固定し(intent_id / status / version / error_code等)、**許可リスト外のフィールドは
  Serializerの段階で落とす**。raw_text・geo座標・constraints系フィールドは不許可
  ②Analyticsは集計値のみ(個別Intentの文言に紐づくイベントは送らない)③例外・エラーは
  **IDのみ**(Parser・Jevの失敗ログはintent_idとエラーコードで表現)。→ ②の計測3群の
  実装はM4 ws-4の担当。本単位は①③を機械的に守る器を作る
- **08 §5.3(サーバ側強制)**: 「ブロックの判定はクライアント入力によらずサーバ側で
  強制する(01第22節)」。同意の有無もクライアントUIだけに頼らない(§2.2の根拠)
- **08 D-10(同型の先例)**: 「alcohol_involved=trueのIntentは、20歳以上のユーザー同士の
  間でしか候補にならない(**Layer 1のHard条件**。06第2節)」— ユーザー単位の条件を
  Layer 1 SQLで硬く閉じる既存の語彙と試験構造が存在する(02#9・test_matching_hardfilter)
- **03 §2(画面一覧)**: 画面は6つ(設定=通知許可/ブロック管理)。オンボーディング同意
  画面の規定はない — **03改版候補**(§3.3)。実装基準はprototype側を正(00 運用ルール6)だが、
  同意画面はプロトタイプに存在しないため、本単位で新規画面として設計する
- **05 §5(初回登録フロー)**: `POST /v1/auth/token`で`profile_complete: false`→
  `POST /v1/users`の経路。オンボーディング同意UIはこのフローの延長に置く
- **05 §5(エラーcode列挙)**: 422系はVALIDATION_ERROR・UNDER_AGE・ACTIVE_INTENT_LIMIT等。
  同意なしは**422 CONSENT_REQUIRED**(新code・docs改版候補。UNDER_AGEと同型の
  個別code。機械的に識別できてフロントの誘導分岐に使える)

## 2. 実装方式の選択肢と推奨

### 2.1 同意の記録方式 — users列追加(推奨)

| 方式 | 内容 | 評価 |
|---|---|---|
| **A. users列追加(推奨)** | `ALTER TABLE users ADD COLUMN consent_at timestamptz`(Nullable・NULL=未同意) | 同意は現時点1値(取得日時)のみ。履歴・バージョンはdocs規定なし(YAGNI)。退会時にusers行と共に消える(M3 ws-6の削除経路がそのまま働く・別処理不要)。問い合わせ開示(いつ同意したか)を1クエリで返せる。マイグレーション1本(0007)で済む |
| B. consents新テーブル | (user_id, agreed_at, version, ...)の追記型 | 同意履歴・バージョン管理を持てるが、docsに規定がなく将来のdocs改版まで決め打ちできない。ws-3と並走するM4でのテーブル追加はリスクが高い。不採用 |
| C. Redis | TTLつきで同意フラグ | 永続性がなく「問い合わせ時に開示」の要件に耐えない。不採用 |

NULL許容(既存ユーザー=開発・試験環境のユーザーは同意未取得のまま残る)とし、
判定式は `consent_at IS NOT NULL` に一元する。既存行へのバックフィルは行わない
(同意の取得日時を捏造しない・staging/試験のユーザーは投入手順で同意APIを叩く)。

### 2.2 マッチング利用条件化の担保層 — API層422遮断+Layer 1 Hard条件(推奨)

引継ぎ5「どの層で担保するか」への回答。08の規定精読結果: D-14は「同意はマッチング機能の
利用条件」とのみ規定し、層は指定しない。§5.3の「クライアント入力によらずサーバ側で
強制」より、UIの表示分けだけでは不十分。よって2層で担保する。

**第1層: API層の遮断(正規経路の全閉鎖)。** 同意なしでは以下を422 CONSENT_REQUIREDで
拒む:
- `POST /v1/intents/parse`(Parser=Anthropic送信。D-14の「外部のAIサービスへ送信」の
  最上游。同意なしでパースさせるのがD-14違反)
- `POST /v1/intents`(status=active)・`PATCH`(draft→active遷移を含む)— MatchEvent
  発行の全起点(M1 ws-3・M2 ws-1)。ここを閉じるとEmbedding送信(Gemini)に繋がる経路も
  遮断される。draft保存自体は許可する(LLM送信なし・ユーザーが同意後に預けられる)

**第2層: Layer 1 Hard条件(最終防衛線)。** layer1.pyのpeer抽出SQLに
`AND u.consent_at IS NOT NULL`(双向)を追加する。`JOIN users u`は同SQLに既に存在する
(origin.py:43・layer1.py:85・layer2.py:39・layer4.py:86等すべてusers JOIN済み)ため
**変更は条件1行**。DB直書き・手動運用由来の異常データでも候補化しない。D-10と同じ
「Layer 1のHard条件」の語彙・試験構造(02#9)に乗る。

**origin前段スキップは省略する。** origin.pyにskip reason(`origin_consent_required`)を
足す案は不採用 — API遮断があればEvent自体が発行されずoriginに到達しない。DB直書きの
異常時もLayer 1で候補化しないため、要件「マッチング候補に出ない」は達成される。
省略の代償は「無駄な評価起点の処理」のみで、これは将来課題。三重に書くと試験対象が
増える割に防御は増えない。

| 方式 | 防げるもの | 防げないもの | 判定 |
|---|---|---|---|
| **API+Layer 1(推奨)** | 正規経路の全送信・全投入(第1層)+異常データの候補化(第2層) | DB直書き+Event直書きの運用異常によるEmbedding送信(候補には出ない) | 送信と候補化を別層で硬く閉じる。追加は小さく、docs改版候補も小さい |
| APIのみ | 正規経路の送信・投入 | DB直書きの候補化(保証が運用手順頼み) | G4の説明性で弱い |
| Layer 1のみ | 候補化 | 同意なしparse(Anthropic送信)・active保存(Event)が動き続ける | D-14違反の正規経路を防げず不採用 |
| 3層(origin含む) | 上記+評価起点化の抑制 | (同上) | 冗長・試験増。将来課題へ |

試験資産への影響(重要): Layer 1に条件を入れても、**シード・既存試験ユーザーを
同意済みにする運用**にすればreference_l1.py(ws-2資産)とm4-seed.yamlの期待値表は
不変(全員同意済み=条件は常にtrue)。ws-3のhelpers_seed.pyのusers投入後に同意APIを
呼ぶ追加が要る — **この実装はws-3側に含める**(2026-10-03 supervisor裁定・§6-3。
ws-5が先行マージされるため、ws-3実装時点で同意APIはmainで利用可能)。

### 2.3 同意APIの形状 — /v1/users/me/consent(推奨)

| 方式 | 内容 | 評価 |
|---|---|---|
| **A. 独立エンドポイント(推奨)** | `POST /v1/users/me/consent`(冪等・認証必須・成功200。同意の再取得は後勝ちで時刻更新)/`GET /v1/users/me/consent`(200 `{consented_at: string|null}`・未同意も200) | 既存契約への非侵襲。設定画面の再確認に同一経路。機械的追従はtest_rate_limit_wiring.pyのCounter+2行のみ(M1 ws-5・M2 ws-2の契約カウンタ衝突前例と同型の最小追従)。既存GET /v1/users/meの応答キー集合比較試験(G3方式)も壊さない |
| B. POST /v1/usersに同意フィールド | 初回登録bodyにconsent_agreed等 | 05 §5契約の変更+既存users試験群の追従。登録済みユーザー(同意前)の扱いも曖昧になる。不採用 |
| C. GET /v1/users/meの応答にconsent_at | 既存応答へ追加 | 応答キー集合比較試験が全量影響。AのGETで十分。不採用 |

補足: 送信記録の「問い合わせ時に開示」(D-14)は運用手続きであり、開示UI/APIを作る
規定はない(01 第25節の最小構成)。本単位では作らない。

### 2.4 フロントエンドの構成 — #/onboarding+設定画面第3セクション

既存資産の延長(M3 ws-7/ws-8のhashルーター自作・texts.js文言集約・settings.jsの
セクション注入型)で次のように組む:

- **router.js**: `#/onboarding`をparseHashに追加(既存パターン・1行)。未知hashはホーム
  のまま不変
- **latch/consent.js(新)**: オンボーディング画面の制御。①文言表示(texts.js定数)②
  チェックボックス(未チェック時ボタン無効)③`POST /v1/users/me/consent`④同意済み
  状態の表示。`GET /v1/users/me/consent`で状態を引く
- **誘導経路2本**: (a) トークン接続後・`GET consent`で未同意ならオンボーディングを
  表示(b) Intent操作で422 CONSENT_REQUIREDを受けたらトースト+オンボーディングへ
  遷移(api/client.jsのerr.status+code)
- **settings.js**: `createSettings({ permissionControl, blocks, consent })`へ拡張。
  第3セクション「LLM送信の同意」(現在状態・同意日時・文言全文)。既存2セクション
  (通知許可=03 D-18・ブロック管理)と**別物として区分表示** — 通知許可(OS通知・
  ブラウザNotification)とLLM送信同意(外部AIサービスへの送信)は根拠規定も効果も
  異なる(08 D-18 vs D-14)。混同を防ぐためタイトルで明示分離する
- **texts.js**: `CONSENT_*`定数群(§2.4末尾の草案)を集約(既存規約どおり)
- **index.html**: `#onboardingScreen` section(既存settingsScreenと同型)+設定画面に
  consentSectionのDOM

**同意文言の確定**(texts.js定数・2026-10-03ユーザー承認済み。D-14の3要素+送信先名称+
開示+利用条件を含む):

> **LLM送信の同意**
> LATCHでは、あなたが入力したIntentの内容を、マッチング判定のために外部のAIサービスへ
> 送信します。
> ・送信先: TypeSafe AI(マッチングの判定)/ Anthropic(条件の読み取り・判定の予備)/
> 　Google Gemini(条件の数値化)
> ・送信するのは判定に必要な最小限の情報で、送信記録を保持します(お問い合わせいただければ
> 　開示します)
> ・送信データは、AIサービスの学習には利用されません
> この同意は、マッチング機能を利用するための条件です。同意がない場合、Intentを預ける
> (マッチングに供給する)ことはできません。
> [ ] 上記に同意します [同意する]

送信先3社・役割は08 §3の確定値。実装側で送信先を変える場合はdocs 08改版とtexts.js
更新が対で動く旨を計画書に記す。D-13の退会同意文(匿名化統計保持)はM3 ws-6領域であり
本単位では触らない。

### 2.5 許可リストSerializer — 登録表+機械検査(推奨)

| 方式 | 内容 | 評価 |
|---|---|---|
| **A. 登録表+機械検査(推奨)** | 許可リストの単一真実(ロガー名→レコード型の登録表)+不許可フィールド列挙を新モジュールに置き、unit試験で突合する。既存2ログは無変更で登録済みとして扱う | 既存実装は既に「型固定=Serializer段階で落とす」を実現済み(§2.6)。重複実装せず登録表で一元化。G4証拠(許可リスト一覧)と問い合わせ開示の資料になる。新規構造化ログを作る際の規律が機械化される |
| B. 共通Formatter/Filter | logging層で全ログを1つのSerializerへ通す | printf型モジュールログ(§2.4第3規定の運用)まで巻き込み過大。既存の ロガー=用途別 の構造を壊す。不採用 |
| C. Serializerクラス導入でrecords.py差し替え | `send_log`経由を共通Serializerに差し替え | 挙動は既存2型と同等(差し替えの実益が登録表だけ)で、既存実装変更のリスクが純増。Aが同じ実益を無変更で得られる。不採用 |

**新設モジュール(`latch/core/logallow.py`・仮名)の内容**:
- `ALLOWED_STRUCTURED_LOGS: dict[str, type[BaseModel]]` — 登録表。初期登録は
  `"latch.llm.send" → llm.records.SendRecord` と `"latch.push.send" →
  notifications.records.PushSendRecord`
- `FORBIDDEN_FIELD_NAMES: frozenset[str]` — 不許可フィールドの列挙。
  `raw_text` / `geo` / `lat` / `lon` / `geo_center` / `geo_radius_m` / `constraints`
  / `soft_constraints` / `ng_conditions` / `structured_data`(§2.4の
  raw_text・geo座標・constraints系を機械突合できる語に展開)
- `serialize_allowed(record_dict)` — 許可リスト外のキーを落としてから型へ渡す関数
  (将来の新規ログ用の規律の入口。08 §2.4「Serializerの段階で落とす」の実体)

**機械検査(unit: test_log_allowlist.py)**:
1. 登録表の各型の全フィールド名が`FORBIDDEN_FIELD_NAMES`と衝突しない
2. records.py側の型が登録表に載っている(import突合。新規にSendRecord型を作ったら
   登録必須という規律の強制点)
3. `SendRecord(**{"raw_text": "...", ...})`のように不許可キーを混ぜたdictが、Pydantic
   の既定ignore(モデル外フィールドは無視)で**出力JSONに現れないこと**(=「Serializer
   の段階で落とす」の挙動試験)
4. PushSendRecordの`title`/`body`の取り得る値がtemplates.pyの固定定数のみであることの
   再確認(既存M3 ws-3試験があればそれに委譲・無ければ1件追加 — raw_text不許可の
   実質保証)

### 2.6 既存ログと許可リストの対応(引継ぎ3への回答)

既存の構造化ログ2本は**いずれも「許可リスト済み構造化ログ」であり、型(Pydanticモデル
のフィールド固定)が許可リストそのもの**:

- `latch.llm.send`(llm/records.py・M0 ws-2)— SendRecord。フィールド=
  occurred_at/system/destination/status/error_code/intent_ids/user_id。docstringに
  「SendRecordが構造化ログの許可リストの実体(08 §2.4)」と明記済み。08 §3の
  「送信先・データ種別・時刻を記録・内容を含まない」をそのまま実装済み
- `latch.push.send`(notifications/records.py・M3 ws-3)— PushSendRecord。本文
  (title/body)を含む唯一の例外だが、templates.pyの固定定数のみを通り得る構造で
  機微は入り得ない(docstring明記)。10 §1「プッシュ本文が汎用文であること」の
  FR-21検証用記録
- モジュール別printf型ログ(latch.users / latch.intents / latch.worker.* 等)=
  §2.4第3規定「例外・エラーはIDのみ」の運用(ID・status・code中心。§4で実際に
  現存ログを調査した結果、raw_text等を引数に取るinfo/debug/exceptionログは0件 —
  logger.exception3箇所もmessage_id/intent_id/例外のみ)。§2.4第1規定の「構造化ログ」
  (JSONレコード)と区別し、**変換対象外・現状維持**

つまり実装済みの型固定方式を「正」として登録表に載せ、以後の新規ログは登録必須とする
のが本単位の構造化ログの設計。既存ログへの変更は**ゼロ**。

## 3. ファイル構成

### 3.1 作るもの(新規)

| ファイル | 内容 |
|---|---|
| `backend/alembic/versions/0007_users_consent.py` | `ALTER TABLE users ADD COLUMN consent_at timestamptz` / downgrade=`DROP COLUMN`。docstringに根拠(D-14)とdocs改版候補を記録(0006の慣行どおり)。**revision=0007・down_revision=0006を本計画で確定** |
| `backend/src/latch/users/consent.py` | consent記録・取得の小サービス(UPDATE 1本+SELECT 1本・user_storeのmake_user_serviceと同型のCallable注入)。UserServiceへの統合か独立かは実装時に統合コストで決めてよい(契約はroutesとservice関数で固定) |
| `backend/src/latch/core/logallow.py` | 許可リスト登録表+不許可列挙+serialize_allowed(§2.5) |
| `backend/tests/users/test_consent_service.py` | unit: 記録の冪等・NULL初期値・Clock由来時刻 |
| `backend/tests/integration/test_consent_api.py` | integration(実HTTP): POST/GET・未同意200 null・認証401・422遮断(parse・intents active)・レート制限付き |
| `backend/tests/unit/test_log_allowlist.py` | §2.5の機械検査4項目 |
| `frontend/src/latch/consent.js` | オンボーディング+再確認ブロックの制御 |
| `frontend/tests/latch-consent.test.js` | vitest: 表示・未チェック無効・POST・誘導 |
| `docs/plans/M4/ws-5-plan.md`(後続) | 計画書(本設計の承認後に作成) |

### 3.2 触らないもの

| 領域 | 理由 |
|---|---|
| `llm/records.py`・`notifications/records.py`・templates.py | 既存2ログは許可リスト済み(§2.6)。無変更が前提の設計 |
| `worker/matching/origin.py`・`reference_l1.py`・`m4-seed.yaml`・期待値表EX | origin前段skip省略(§2.2)・シード全員同意済み運用で参照実装不変 |
| `auth/`(JWT・失効リスト)・`ratelimit/` | 同意は認証後のユーザー状態であり、認証契約に触れない。新ルートは既存のapi_rate_limited依存パターンに従う |
| M3資産の通知経路(`latches/`・`notifications/`) | FCM実機(ws-9)との関係: 同意・許可の記録は**スタブ送信時点で成立**(push_logの走る時点でFR-21記録と同じ・引継ぎ4の回答)。ws-9は送信手段の差し替えのみ |
| 既存のprintf型モジュールログ | §2.4第3規定の運用として現状維持(§2.6) |
| M3 ws-6の削除・退会 | users列追加のため退会時のconsent_atは行と共に消える。追加処理不要 |

### 3.3 変更するもの

| ファイル | 変更 |
|---|---|
| `backend/src/latch/users/errors.py` | `ConsentRequiredError`(422 CONSENT_REQUIRED)追加(UnderAgeErrorと同型) |
| `backend/src/latch/users/routes.py` | `POST /v1/users/me/consent`・`GET /v1/users/me/consent`(api_rate_limited付き) |
| `backend/src/latch/intents/errors.py` | `ConsentRequiredError`(IntentsError・422 CONSENT_REQUIRED。UnderAgeErrorと同型のintents側配置) |
| `backend/src/latch/intents/service.py` | create(active)・update(draft→active)・parse APIで同意検査(§2.2第1層) |
| `backend/src/latch/worker/matching/layer1.py` | peer SQLに`AND u.consent_at IS NOT NULL`(双向・§2.2第2層) |
| `backend/tests/unit/test_rate_limit_wiring.py` | Counterへ`"/v1/users/me/consent": 2`追加(機械的追従・POST+GET) |
| `backend/tests/integration/test_matching_hardfilter.py` | 同意なしpeer除外ケース追加(02#9の延長) |
| `frontend/src/router.js` | `#/onboarding`追加 |
| `frontend/src/main.js` | consent配線(誘導経路2本) |
| `frontend/src/latch/settings.js` | consentセクション追加(引数拡張) |
| `frontend/src/latch/texts.js` | CONSENT_*定数(確定文言・§2.4・2026-10-03ユーザー承認) |
| `frontend/index.html` | onboardingScreen+設定画面第3セクションのDOM |
| `frontend/tests/latch-settings.test.js`・`router.test.js` | 追従 |

### 3.4 docs改版候補(次回docs改版に含める・00 運用ルール7の慣行)

- 05 §2: users表へ`consent_at timestamptz`(NULL=未同意・D-14)追記
- 05 §5: `POST/GET /v1/users/me/consent`の2エンドポイント+エラーcode列挙へ
  422 CONSENT_REQUIRED(intents系・users系)追記
- 08 D-14: 「同意はusers.consent_atに記録する・設定画面で再確認表示する」の実装追記候補
- 03 §2: 画面一覧へオンボーディング同意+設定画面の「LLM送信の同意」ブロック追記候補

## 4. テスト方針

- **unit(users)**: consent記録の冪等(2回POSTで時刻不変か否かの方針=後勝ち上書きで時刻
  更新。UPDATE 1本のため特殊分岐なし)・NULL初期値・Clock由来時刻(tz-aware UTCのみ —
  Clock規約)
- **unit(logallow)**: §2.5の機械検査4項目。これはG4「ログ許可リスト有効」の継続的保証
  (test-ci常設で毎回走る)
- **integration(API)**: 実HTTPでPOST/GET consent・401(未認証)・**422遮断の3経路**
  (parse・intents active作成・draft→active)・同意後は通ること・422のcode文字列が
  CONSENT_REQUIREDであること(フロント誘導分岐の契約)
- **integration(matching)**: Layer 1同意条件 — 同意なしpeerが候補に現れない
  (test_matching_hardfilterへ追加)。同意済み対照群で既存のK上限・件数が不変である
  ことの確認も兼ねる
- **frontend(vitest)**: オンボーディング表示・未チェック時のボタン無効・POST成功後の
  遷移・422 CONSENT_REQUIRED検出での誘導・設定画面の再確認ブロック(状態・日時・文言)
- **レート制限配線**: test_rate_limit_wiring.pyへのCounter追従(差し替え忘れの網羅 —
  M1 ws-4確立の静的検査)
- **G4証拠(10 §5・docs/testassets/results/)**: ①同意フローのstaging手順実行記録
  (未同意Intentの422→同意→active可の順序観察。test-ci試験のstaging再現+ログ添付)
  ②test_log_allowlistの実行結果+登録表の写し(許可リスト一覧=問い合わせ開示資料の
  雛形)③`docker logs`からlatch.llm.send等の出力にraw_text相当が含まれないことの
  サンプル照合(ws-3のpushログ読取と同型の手法)
- **実施環境**: 同意系はci実行可能(②①のstaging部分は手順書レベルで最小に — ws-3の
  staging_testsディレクトリには**触れない**(並走干渉回避))

## 5. 完了条件(計画書への引き取り用ドラフト)

- [ ] migration 0007適用でhead=0007・既存12テーブル+1列(users.consent_at)
- [ ] 同意なしでparse・intents activeが422 CONSENT_REQUIRED・同意後は通る
- [ ] Layer 1で同意なしpeerが候補化しない(hardfilter試験グリーン)
- [ ] 許可リスト機械検査4項目がtest-ci常設でグリーン・既存2ログは無変更のまま登録済み
- [ ] オンボーディング画面・設定画面再確認ブロックが動作(vitestグリーン+build成功)
- [ ] test_rate_limit_wiring.py追従・マージ後mainでtest-ci全グリーン
- [ ] G4証拠がdocs/testassets/results/に保管(§4の3点)

## 6. supervisor承認事項(2026-10-03・5件とも裁定済み — 未解決の論点なし)

1. **同意文言**(§2.4): **2026-10-03ユーザー承認済み(草案どおり)**。§2.4の文言を
   確定としてtexts.js定数へ固定
2. **同意の取り消しは非実装**: **2026-10-03ユーザー了承済み(設計判断として確定)**。
   docsに規定なし(D-14は取得と再確認のみ)・最小構成(01 第25節)。取り消しを将来
   要求する場合はdocs改版(08 D-14)を先に行う。副作用として「同意後にactiveにした
   Intentが取り消し不能でマッチングされ続ける」挙動が確定
3. **並走ws-3との調整**: **2026-10-03 supervisor裁定 — 実装順序を直列化する。
   ws-5を先に実装・マージし、ws-3の実装worktreeはws-5マージ後のmain(head=0007)から
   作る**。①helpers_seed.py(users投入後の同意API呼び出し)はws-3側の実装に含める
   (§2.2)②ws-3のstaging conftestのhead前提ピンは**0007へ読み替え**(ws-3設計の
   「head=0006」前提は本裁定により0007とする)。計画書(agent2)は両単位並行で作成可 —
   マイグレーション番号はws-5側0007で確定済み・衝突なし
4. **エラーcode名=422 CONSENT_REQUIRED**: **2026-10-03 supervisor承認(UNDER_AGEと
   同型)**。05 §5エラー列挙への追記はdocs改版候補のまま(§3.4)
5. **origin前段スキップの省略**(§2.2): **2026-10-03 supervisor承認・明示残置**。
   「無駄な評価起点の処理の抑制」は将来課題(M3 ws-5設計§5-1の「明示残置」慣行に同型)

## 7. 未解決の論点

なし(§6の5件が2026-10-03にすべて裁定済み)。
