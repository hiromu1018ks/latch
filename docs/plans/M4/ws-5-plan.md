# M4 ws-5(告知・同意フロー+ログ許可リスト)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 12 M4-6(告知・同意フローと構造化ログの許可リスト)を実装し、G4チェックリストの「告知・同意とログ許可リスト有効」の実体を作る。

**Architecture:** 同意はusers.consent_at列(0007・NULL=未同意)に1値で記録し、API層の422 CONSENT_REQUIRED遮断(parse・active作成・draft→active)とLayer 1 Hard条件(`AND u.consent_at IS NOT NULL`)の2層で担保する(設計§2.2)。許可リストは既存2構造化ログを「許可リスト済み」として登録表に一元化し、unit機械検査で強制する(設計§2.5)。フロントエンドは既存hashルーターに`#/onboarding`を追加し、設定画面に第3セクションを足す(設計§2.4)。

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy(text()生SQL) / Alembic / Pydantic(v2) / pytest / vanilla JS(hashルーター自作) / vitest / Vite 6.4.2

**Spec:** docs/plans/M4/ws-5-design.md(agent1設計メモ・コミットbb6194c。全論点確定済み§6・§7)。本計画は設計メモに則って書かれているため、実装者は両方を読むこと。迷いが出たら設計メモの該当節を開く。

---

## §0 実装エージェント(agent3)への規律

1. **worktree内で実装する。** 毎コミットでunit試験をグリーンにしてからコミットする。
2. **共有ci-dbへのtest-ci/migrate実行は禁止。** 本単位はマイグレーション追加単位(0007)のため特に厳守。agent3はunit試験だけで開発し、integration試験は**コードを書いてcollect-onlyまで**確認する(実行はしない)。報告ファイルには`test-ci=スーパーバイザー検証待ち`と記録する。スーパーバイザー検証時にapiイメージ再構築+migrate+test-ciが走る(本計画「§影響表」の前提)。
3. **テストファイルのbasenameは一意。** 追加の前後に衝突確認:
   ```bash
   find backend/tests -name 'test_*.py' -printf '%f\n' | sort | uniq -d
   # 出力が空であること(既存の運用ルールどおり)
   ```
4. **integration試験のcollect-onlyは可・実行は不可**(前述2と同じ理由)。
5. 完了条件にない作業を発生させたら計画書の「禁止」節と突き合わせ、スコープ外ならやらない(報告ファイルに残置事項として書く)。

## 1. 参照仕様(docsの節番号)

| 項目 | 出典 |
|---|---|
| 告知・同意の要件(3要素+送信先名称・利用条件・再確認) | 08 D-14 |
| 送信先の確定値(TypeSafe AI / Anthropic / Google Gemini) | 08 §3 |
| 構造化ログの許可リスト方式・例外はIDのみ | 08 §2.4(第1・第3規定) |
| サーバ側強制(クライアント入力によらない) | 08 §5.3・01第22節 |
| Layer 1 Hard条件の語彙・試験構造の先例(D-10) | 08 D-10・06 §2・02#9 |
| users表・API・エラーcode | 05 §2・05 §5(422系個別codeの先例=UNDER_AGE) |
| 初回登録フローの延長に同意UIを置く | 05 §5(`POST /v1/auth/token`→`POST /v1/users`) |
| 画面一覧・通知許可との区別 | 03 §2・03 D-18(通知許可=OS通知・別物) |
| 本単位の位置づけ(M4-6・G4完了条件) | 12 M4-6・12 M4完了条件 |
| 同意文言の確定(2026-10-03ユーザー承認) | 設計メモ§2.4(本計画Task 9の定数と一字一句同じ) |
| 取り消し非実装・origin前段スキップ省略・直列化 | 設計メモ§6(全裁定済み) |

## 2. スコープ(作成・変更するファイル)

### 2.1 作成(新規)

| ファイル | 内容 |
|---|---|
| `backend/alembic/versions/0007_users_consent.py` | usersへconsent_at timestamptz追加。revision=0007・down_revision=0006(確定済み) |
| `backend/src/latch/users/consent.py` | 同意の記録・取得・consented(user_id)の小サービス |
| `backend/src/latch/core/logallow.py` | 許可リスト登録表+不許可フィールド列挙+serialize_allowed |
| `backend/tests/unit/users/test_consent_service.py` | consent serviceのunit |
| `backend/tests/unit/test_log_allowlist.py` | 許可リスト機械検査4項目のunit |
| `backend/tests/integration/test_consent_api.py` | consent APIのintegration(実HTTP。実行はスーパーバイザー検証時) |
| `frontend/src/latch/consent.js` | オンボーディング+設定再確認ブロック+誘導の制御 |
| `frontend/tests/latch-consent.test.js` | vitest |
| `docs/plans/M4/ws-5-report.md` | 報告ファイル(Task 10) |

### 2.2 変更

| ファイル | 変更 |
|---|---|
| `backend/src/latch/users/errors.py` | `ConsentRequiredError`(422 CONSENT_REQUIRED)追加 |
| `backend/src/latch/users/routes.py` | `POST/GET /v1/users/me/consent`2ルート(users_routerのrouter-level `api_rate_limited`を継承) |
| `backend/src/latch/users/__init__.py` | `ConsentRequiredError`をexportに追加 |
| `backend/src/latch/intents/errors.py` | `ConsentRequiredError`(IntentsError・422 CONSENT_REQUIRED)追加 |
| `backend/src/latch/intents/__init__.py` | 同上をexportに追加 |
| `backend/src/latch/intents/store.py` | `UserRow`へ`consent_at`フィールド+_SELECT_USERへ列追加 |
| `backend/src/latch/intents/service.py` | create(active)・draft→activeで同意検査+parseで同意検査(consent_lookup注入) |
| `backend/src/latch/worker/matching/layer1.py` | `LAYER1_WHERE_HEAD`の末尾に`AND u.consent_at IS NOT NULL`の1行(HEADに入るためBASE=グループPool検索・H再検証にも効く) |
| `backend/src/latch/main.py` | consent_serviceのlifespan構築+create_app引数+parse wiring |
| `backend/tests/unit/test_rate_limit_wiring.py` | Counterへ`"/v1/users/me/consent": 2`追加 |
| `backend/tests/unit/matching/test_layer_sql.py` | 同意条件のSQLピン追加(既存ピンは無変更で通る — 影響表のとおり) |
| `backend/tests/unit/intents/test_intents_service.py` | StubStore既定のUserRow+4箇所の直接構築に`consent_at`追加+新規3試験 |
| `backend/tests/unit/intents/test_event_publish.py`・`test_time_validation.py` | UserRow構築1箇所ずつに`consent_at`追加 |
| `backend/tests/unit/intents/test_service.py` | parse同意検査の新規4試験(既存は無変更で通る) |
| `backend/tests/integration/test_geo.py` | headピン`test_head_is_0006`→`test_head_is_0007`(全数=この1件。rgで確認済み) |
| `backend/tests/integration/`の20ファイル21ヘルパー | ユーザー登録ヘルパーに同意記録2行(影響表§3-1の全数リスト。supervisor実査でカウント訂正: 表#1〜#20=20ファイル・#7が2箇所で計21ヘルパー) |
| `backend/tests/integration/test_matching_hardfilter.py` | 同意なしpeer除外ケース(test_14)追加 |
| `frontend/src/latch/texts.js` | `CONSENT_*`定数群(Task 9の確定文言) |
| `frontend/src/router.js` | `#/onboarding`追加(1行) |
| `frontend/index.html` | `#onboardingScreen`+設定画面第3セクションのDOM |
| `frontend/src/latch/settings.js` | `createSettings({ permissionControl, blocks, consent })`へ拡張 |
| `frontend/src/main.js` | consent生成+422横断検出+誘導経路2本+screens追加 |
| `frontend/tests/router.test.js` | `#/onboarding`のparseHash試験追加 |
| `frontend/tests/latch-settings.test.js` | createSettingsシグネチャ追従+index.html構造id追加+main.js配線assert追加 |

## 3. 禁止(触ってはいけないもの・スコープ外判断基準)

| 領域 | 理由 |
|---|---|
| `llm/records.py`・`notifications/records.py`・`notifications/templates.py` | 既存2ログは許可リスト済み(設計§2.6)。登録表に「載せる」だけで型・フィールドは**無変更** |
| `worker/matching/origin.py`・`layer2.py`・`layer4.py`・`group_engine.py`・`testassets/reference_l1.py`・`m4-seed.yaml`・期待値表EX | origin前段スキップ省略(設計§6-5)・シード全員同意済み運用で参照実装不変。layer2/layer4/group_engineはLAYER1_WHERE*文字列を埋め込むだけなので**変更不要**(条件はlayer1.pyの定数に入る) |
| `auth/`(JWT・失効リスト)・`ratelimit/` | 同意は認証後のユーザー状態。新ルートはusers_routerのrouter-level依存を継承するだけでauth/ratelimit不変 |
| `user_lookup`(auth/service.pyのmake_user_lookup) | authとintentsの共有資産。シグネチャ変更禁止(consentは別callableで注入する) |
| M3資産の通知経路(`latches/`・`notifications/`・`safety/`) | 同意・許可の記録はスタブ送信時点で成立(設計§3.2)。FCM実機はws-9 |
| 既存のprintf型モジュールログ | §2.4第3規定の運用として現状維持(設計§2.6) |
| M3 ws-6の削除・退会処理 | consent_atはusers行と共に消える。追加処理不要 |
| ws-3領域(`staging_tests/`・`helpers_seed.py`・m4-seed計画) | 直列化裁定(§6-3)によりws-3側の実装。触れない |
| `g1gate/`(src・tests両方) | G1ゲート資産。parseの同意検査は登録済みユーザーのみに掛け、未登録JWT経路(設計§2.8-A)は現行挙動を維持する(本計画§4-2)ため変更不要 |
| GET /v1/users/meの応答・`UserMeResponse` | 応答キー集合比較試験(G3方式)を壊さないため方式C不採用(設計§2.3) |

**スコープ外と判断する基準:** (1) docs改版(05 §2・05 §5・08 D-14・03 §2への追記)は本単位では行わない(設計§3.4・次回docs改版にまとめる)。(2) 同意の取り消し・同意履歴・同意バージョンは実装しない(§6-2)。(3) 送信記録の開示UI/APIは作らない(設計§2.3補足)。(4) Analytics計測3群はws-4の担当(08 §2.4第2規定)。(5) 上記以外でも「設計メモ§3のファイル一覧にないsrc/testsファイル」へ変更が及ぶときは、その変更をせず報告ファイルに残置事項として記す。

## 4. 設計上の確定解釈(agent3が迷わないための裁定メモ)

1. **Layer 1条件の置き場所と「双向」の意味。** `AND u.consent_at IS NOT NULL`は`LAYER1_WHERE_HEAD`の末尾(uのJOINは全埋め込みSQLに既存 — origin.py:43・layer1.py:85・layer2.py:39・layer4.py:86・group_engine.py:52,63で実査済み)に置く。HEADに入るため1対1検索・グループPool検索(`_POOL_SEARCH`)・緩和H再検証(`_H_RECHECK_GROUP`)のすべてに効く。この条件は**peer側(対象側)のみ**を検査する。起点側の同意は第1層のAPI遮断が担保し、DB直書きで起点だけ未同意の運用異常時は「その起点の評価が走り得る(Embedding送信を含む)」ことが設計§2.2の表が明示する既知の限界であり、origin前段スキップ省略(§6-5)と同じ残置事項である。正常経路では全activeユーザーが同意済みのため、成立ペアの双方が同意済みになる。
2. **parseの同意検査は登録済みユーザーのみ。** `user_lookup`がNone(未登録JWT)を返した場合は同意検査せず現行どおりparseする。根拠: ①D-14が規制するのは「ユーザーのIntent内容」の送信であり、同意記録はusers行に紐づく(05 §2)②未登録JWT経路はg1gate資産とM1 unit試験がピンする挙動で、これを変えるとG1ゲートの再現性が壊れる(禁止節)③通常のUIフローは登録後の利用である。副作用として未登録JWTのparseは送信記録にuser_id=Noneで残る(現行どおり)。将来的に閉じるならdocs改版(05 §5)が先。
3. **同意検査の位置と429/422の順序。** create/updateでは、既存の「レート制限INCR先行(422で失敗する作成も消費)」の意味論を維持するため、同意検査は`check_create`/`check_update`の**後**・uow内の他の422検証より**前**に置く(設計§2.2の遮断は検証順の変更を意図しない)。
4. **users側`ConsentRequiredError`は本単位で送出する経路がない。** 05 §5のusers系エラー列挙への対向として配置する(UnderAgeErrorの二重配置先例と同型・設計§3.3の指定どおり)。送出するのはintents側のみ。
5. **同意ヘルパーの追加対象は「POST /v1/usersでユーザーを作る全integrationヘルパー」。** Layer 1を通すか否かにかかわらず、登録ユーザーでactive Intentを作る経路を持つ試験はすべて422遮断で壊れるため(影響表§3-1の全数リスト)。

## 5. Review Focus(仕様が黙っていても壊れてはいけない入力と、それを钉付けする試験)

1. **再POSTでの時刻更新(後勝ち)** — 同意は冪等だが再取得でconsent_atが更新される。→ Task 3の`test_record_overwrites_timestamp_later_wins`+Task 8のintegration冪等試験。
2. **未同意ユーザーがstatus省略でPOST /v1/intentsした場合** — statusの既定値はactive(IntentCreateRequest)のため422 CONSENT_REQUIREDになる。→ Task 8の遮断試験でstatus省略payloadを使う。
3. **GET /v1/users/me/consentの未同意=200 null(404ではない)** — 設定画面の初回表示が404で壊れないこと。→ Task 3の`test_get_returns_none_when_never_consented`+Task 8。
4. **draft保存は同意なしで許可(LLM送信ゼロ)** — 遮断はactive化のみ。→ Task 5の`test_create_draft_without_consent_allowed`。
5. **未知hash(`#/onboarding/`等の末尾付加)はホームのまま** — ルーターの厳密一致規約を壊さない。→ Task 9のrouter.test.js追従試験。

---

# 実装ステップ

## Task 1: ログ許可リスト機構(logallow.py)

**Files:**
- Create: `backend/src/latch/core/logallow.py`
- Test: `backend/tests/unit/test_log_allowlist.py`

**Interfaces:**
- Consumes: `latch.llm.records.SendRecord`・`latch.notifications.records.PushSendRecord`(既存・無変更)
- Produces: `ALLOWED_STRUCTURED_LOGS: dict[str, type[BaseModel]]`・`FORBIDDEN_FIELD_NAMES: frozenset[str]`・`serialize_allowed(logger_name: str, record_dict: dict) -> BaseModel`(未登録ロガーはKeyError)

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/test_log_allowlist.py`を新規作成:

```python
"""構造化ログ許可リストの機械検査(M4 ws-5 計画Task 1・08 §2.4第1・第3規定)。

登録表(ロガー名→レコード型)と不許可フィールド列挙の突合、型登録の強制、
Serializer段階での不許可キー落下を検証する。test-ci常設で毎回走る
(G4「ログ許可リスト有効」の継続的保証)。既存2ログは無変更で登録済み
(設計§2.6 — 本試験は変更を強制しない・登録の存在のみ強制する)。
"""

from datetime import UTC, datetime

from latch.core.logallow import (
    ALLOWED_STRUCTURED_LOGS,
    FORBIDDEN_FIELD_NAMES,
    serialize_allowed,
)
from latch.llm.records import SendRecord
from latch.notifications.records import PushSendRecord
from latch.notifications.templates import (
    PUSH_BODY_LATCH,
    PUSH_BODY_MATCHED,
    PUSH_BODY_NOTICE,
    PUSH_TITLE,
    build_push,
)
from latch.notifications.types import (
    NOTIFICATION_ATTENDANCE_REQUEST,
    NOTIFICATION_MATCHED,
    NOTIFICATION_NEARBY,
    NOTIFICATION_PROPOSAL,
)

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)


def test_registered_log_types_have_no_forbidden_fields():
    """機械検査1: 登録表の各型の全フィールド名が不許可列挙と衝突しない。"""
    assert set(ALLOWED_STRUCTURED_LOGS) == {"latch.llm.send", "latch.push.send"}
    for name, model in ALLOWED_STRUCTURED_LOGS.items():
        for field in model.model_fields:
            assert field not in FORBIDDEN_FIELD_NAMES, (name, field)


def test_record_types_are_registered():
    """機械検査2: records.py側の型が登録表に載っている(import突合)。

    新規に構造化ログの型を作ったら登録必須という規律の強制点。
    """
    assert ALLOWED_STRUCTURED_LOGS["latch.llm.send"] is SendRecord
    assert ALLOWED_STRUCTURED_LOGS["latch.push.send"] is PushSendRecord


def test_serialize_allowed_drops_forbidden_keys():
    """機械検査3: 不許可キーを混ぜたdictが出力JSONに現れない
    (=「Serializerの段階で落とす」08 §2.4の挙動試験)。"""
    raw = {
        "occurred_at": NOW,
        "system": "intent_parser",
        "destination": "Anthropic",
        "status": "ok",
        "raw_text": "今夜 天文館で飲みたい",
        "geo": {"lat": 31.5965, "lon": 130.5581},
        "constraints": ["19時以降"],
        "structured_data": {"x": 1},
    }
    record = serialize_allowed("latch.llm.send", raw)
    assert isinstance(record, SendRecord)
    dumped = record.model_dump_json()
    assert "raw_text" not in dumped
    assert "今夜 天文館で飲みたい" not in dumped
    assert "geo" not in dumped
    assert "constraints" not in dumped
    assert record.destination == "Anthropic"  # 許可フィールドは保持される


def test_serialize_allowed_rejects_unregistered_logger():
    """未登録ロガーはKeyError(新規ログは登録必須という規律の入口)。"""
    import pytest

    with pytest.raises(KeyError):
        serialize_allowed("latch.unknown.send", {})


def test_push_title_body_are_template_constants_only():
    """機械検査4: PushSendRecordのtitle/bodyがtemplates固定定数のみ
    (raw_text不許可の実質保証。定数の全文ピンはM3 ws-3の
    test_templates.pyが所有するため本試験では写像の全体性のみ再確認)。"""
    mapped = {
        build_push(t)
        for t in (
            NOTIFICATION_PROPOSAL,
            NOTIFICATION_NEARBY,
            NOTIFICATION_MATCHED,
            NOTIFICATION_ATTENDANCE_REQUEST,
        )
    }
    for title, body in mapped:
        assert title == PUSH_TITLE
        assert body in {
            PUSH_BODY_LATCH,
            PUSH_BODY_NOTICE,
            PUSH_BODY_MATCHED,
        }
```

- [ ] **Step 2: 試験が失敗することを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_log_allowlist.py -q`
Expected: FAIL(ModuleNotFoundError: No module named 'latch.core.logallow')

- [ ] **Step 3: 実装を書く**

`backend/src/latch/core/logallow.py`を新規作成:

```python
"""構造化ログの許可リスト(M4 ws-5・08 §2.4第1・第3規定)。

出力できるフィールドを列挙型で固定したレコード型(既存2型)を登録表に
一元化し、不許可フィールド(raw_text・geo座標・constraints系)を列挙する。
新規に構造化ログを作る際はレコード型をここへ登録必須
(test_log_allowlist.pyの機械検査が強制する)。既存の
latch.llm.send・latch.push.sendは「許可リスト済み構造化ログ」として
無変更で登録する(設計§2.6)。printf型モジュールログは§2.4第3規定の
運用(例外・エラーはIDのみ)であり変換対象外。
"""

from __future__ import annotations

from pydantic import BaseModel

from latch.llm.records import SendRecord
from latch.notifications.records import PushSendRecord

# 単一真実: ロガー名 → レコード型(Pydanticモデルのフィールド固定が
# 許可リストそのもの)。G4証拠(許可リスト一覧)と問い合わせ開示資料の雛形
ALLOWED_STRUCTURED_LOGS: dict[str, type[BaseModel]] = {
    "latch.llm.send": SendRecord,  # 08 §3 送信記録(M0 ws-2)
    "latch.push.send": PushSendRecord,  # 10 §1 FR-21検証用(M3 ws-3)
}

# 08 §2.4「raw_text・geo座標・constraints系フィールドは不許可」の機械突合用列挙
FORBIDDEN_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "raw_text",
        "geo",
        "lat",
        "lon",
        "geo_center",
        "geo_radius_m",
        "constraints",
        "soft_constraints",
        "ng_conditions",
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

- [ ] **Step 4: 試験が通ることを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_log_allowlist.py -q`
Expected: PASS(5 passed)

- [ ] **Step 5: lintしてコミット**

Run: `cd backend && .venv/bin/ruff check src/latch/core/logallow.py tests/unit/test_log_allowlist.py`

```bash
git add backend/src/latch/core/logallow.py backend/tests/unit/test_log_allowlist.py
git commit -m "feat: 構造化ログ許可リスト機構(logallow・登録表+機械検査)"
```

## Task 2: マイグレーション0007+headピン追従

**Files:**
- Create: `backend/alembic/versions/0007_users_consent.py`
- Modify: `backend/tests/integration/test_geo.py:33-39`(headピン)
- Test: `backend/tests/integration/test_geo.py`(integration・実行はスーパーバイザー検証時)

**Interfaces:**
- Produces: users.consent_at timestamptz(NULL=未同意)。Task 3〜7・8がこの列に依存する。

- [ ] **Step 1: マイグレーションを書く**

`backend/alembic/versions/0007_users_consent.py`を新規作成(0006の慣行どおりdocstringに根拠とdocs改版候補を記録):

```python
"""usersへconsent_at列を追加(M4 ws-5・design §2.1案A・08 D-14)。

オンボーディング同意の取得日時(timestamptz・Nullable・NULL=未同意)。
判定式はconsent_at IS NOT NULLに一元し、API遮断(422 CONSENT_REQUIRED)と
Layer 1 Hard条件(AND u.consent_at IS NOT NULL)の2層から参照される。
既存行へのバックフィルは行わない(同意の取得日時を捏造しない — design §2.1。
staging/試験のユーザーは投入手順で同意APIを叩く)。取り消しは非実装
(2026-10-03ユーザー了承・docsに規定なし)。05 §2 users表への追記は
次回docs改版候補(design §3.4)。

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN consent_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS consent_at")
```

- [ ] **Step 2: headピン試験を0007へ追従させる**

`backend/tests/integration/test_geo.py`の`test_head_is_0006`(33-39行目)を変更する。全リポジトリでhead=0006をピンする試験はこの1件のみ(`rg -n "0006" backend/tests -g '*.py'`で確認済み):

```python
async def test_head_is_0007(db_engine):
    """0007がhead(migrated_dbがheadまで進めた結果・M4 ws-5で0007追加)。"""
    async with db_engine.connect() as conn:
        version = (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar()
    assert version == "0007"
```

- [ ] **Step 3: collect-onlyとマイグレーションファイルの構文確認**

Run: `cd backend && .venv/bin/python -c "import importlib.util; spec = importlib.util.spec_from_file_location('m', 'alembic/versions/0007_users_consent.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); assert m.revision == '0007' and m.down_revision == '0006'; print('ok')"`
Expected: `ok`

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_geo.py --collect-only -q | tail -3`
Expected: collect成功(test_head_is_0007が並ぶ・実行はしない)

- [ ] **Step 4: コミット**

```bash
git add backend/alembic/versions/0007_users_consent.py backend/tests/integration/test_geo.py
git commit -m "feat: users.consent_at列追加(0007・NULL=未同意)+headピン追従"
```

## Task 3: users/errors.py+consent service

**Files:**
- Modify: `backend/src/latch/users/errors.py`
- Modify: `backend/src/latch/users/__init__.py`
- Create: `backend/src/latch/users/consent.py`
- Test: `backend/tests/unit/users/test_consent_service.py`

**Interfaces:**
- Consumes: `latch.core.clock.Clock`・`latch.auth.tokens.AccessTokenClaims`・`latch.users.errors`(既存)
- Produces(Task 4・6が消費):
  - `ConsentService.record(*, claims: AccessTokenClaims) -> ConsentState`(未登録は`UserNotFoundError`)
  - `ConsentService.get(*, claims: AccessTokenClaims) -> ConsentState`(未同意も`ConsentState(None)`・未登録は404)
  - `ConsentService.consented(user_id: uuid.UUID) -> bool`(DB障害は`DependencyUnavailableError`)
  - `ConsentState(consented_at: datetime | None)`
  - `make_consent_service(*, clock: Clock, engine: AsyncEngine) -> ConsentService`

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/users/test_consent_service.py`を新規作成:

```python
"""consent serviceのunit(M4 ws-5 計画Task 3・design §2.1・§2.3)。

Callable注入で決定的に検証する(UserServiceのunitと同型)。時刻はClock由来
の明示値のみ(tz-aware UTC — Clock規約)。冪等=後勝ちで時刻更新(UPDATE 1本・
特殊分岐なし)。
"""

import uuid
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.users.consent import ConsentService, make_consent_service
from latch.users.errors import DependencyUnavailableError, UserNotFoundError

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 3, 13, 0, 0, tzinfo=UTC)
USER_ID = uuid.uuid4()


class StubClaims:
    auth_provider = "google"
    auth_subject = "sub-1"


class _Svc:
    """record/fetch/consented/existsの記録スタブ。"""

    def __init__(
        self,
        *,
        record=None,
        consented_at=None,
        consented=True,
        exists=True,
        fail=False,
    ):
        self.record_calls = []
        self._record = record
        self._consented_at = consented_at
        self._consented = consented
        self._exists = exists
        self._fail = fail

    async def record(self, claims, now):
        self.record_calls.append((claims, now))
        if self._fail:
            raise RuntimeError("db down")
        return self._record

    async def fetch(self, claims):
        if self._fail:
            raise RuntimeError("db down")
        return self._consented_at

    async def consented(self, user_id):
        if self._fail:
            raise RuntimeError("db down")
        return self._consented

    async def exists(self, claims):
        if self._fail:
            raise RuntimeError("db down")
        return self._exists  # SELECT consent_atのNoneを未同意と区別するため


def _service(svc: _Svc, *, clock_value=NOW) -> ConsentService:
    return ConsentService(
        clock=FakeClock(clock_value),
        record_consent=svc.record,
        fetch_consent=svc.fetch,
        consented=svc.consented,
        exists_fn=svc.exists,
    )


async def test_record_returns_state_with_clock_time():
    svc = _Svc(record=NOW)
    state = await _service(svc).record(claims=StubClaims())
    assert state.consented_at == NOW
    assert svc.record_calls == [(StubClaims(), NOW)]  # Clock由来の時刻が渡る


async def test_record_overwrites_timestamp_later_wins():
    """再POSTは後勝ちで時刻更新(UPDATE 1本のため特殊分岐なし — 計画§5-1)。"""
    svc = _Svc(record=LATER)
    state = await _service(svc, clock_value=LATER).record(claims=StubClaims())
    assert state.consented_at == LATER


async def test_record_raises_404_when_row_missing():
    svc = _Svc(record=None)  # UPDATE ... RETURNING が空=未登録
    with pytest.raises(UserNotFoundError):
        await _service(svc).record(claims=StubClaims())


async def test_record_wraps_unexpected_as_503():
    svc = _Svc(fail=True)
    with pytest.raises(DependencyUnavailableError):
        await _service(svc).record(claims=StubClaims())


async def test_get_returns_none_when_never_consented():
    """未同意は200 null(404ではない — 設定画面の初回表示が壊れない)。"""
    svc = _Svc(consented_at=None)
    state = await _service(svc).get(claims=StubClaims())
    assert state.consented_at is None


async def test_get_returns_timestamp():
    svc = _Svc(consented_at=NOW)
    state = await _service(svc).get(claims=StubClaims())
    assert state.consented_at == NOW


async def test_get_raises_404_when_row_missing():
    """SELECT consent_at=Noneのとき行もなければ404(未同意のnullと区別)。"""

    class _NoRow(_Svc):
        def __init__(self):
            super().__init__(consented_at=None, exists=False)

        async def fetch(self, claims):
            return None  # 行なし=未登録

    with pytest.raises(UserNotFoundError):
        await _service(_NoRow()).get(claims=StubClaims())


async def test_consented_returns_bool():
    assert await _service(_Svc(consented=True)).consented(USER_ID) is True
    assert await _service(_Svc(consented=False)).consented(USER_ID) is False


async def test_consented_wraps_unexpected_as_503():
    with pytest.raises(DependencyUnavailableError):
        await _service(_Svc(fail=True)).consented(USER_ID)


class _FakeEngine:
    """make_consent_serviceの結線確認用(実SQLはtest-ciが所有)。"""


def test_make_consent_service_returns_service():
    """ファクトリがConsentServiceを返す(engineはクロージャで束縛)。"""
    svc = make_consent_service(clock=FakeClock(NOW), engine=_FakeEngine())
    assert isinstance(svc, ConsentService)
```

- [ ] **Step 2: 試験が失敗することを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/users/test_consent_service.py -q`
Expected: FAIL(ModuleNotFoundError: No module named 'latch.users.consent')

- [ ] **Step 3: users/errors.pyへConsentRequiredErrorを追加**

`backend/src/latch/users/errors.py`の`UnderAgeError`の後に追加(設計§3.3。本単位でusers側から送出する経路はない — 05 §5のusers系エラー列挙への対向。計画§4-4):

```python
class ConsentRequiredError(UsersError):
    """同意なしでの利用(422 CONSENT_REQUIRED・08 D-14)。

    本単位では送出経路なし(intents側が送出)。05 §5のusers系エラー列挙に
    対向する配置(UnderAgeErrorの二重配置先例と同型 — 計画§4-4)。
    """

    http_status = 422
    code = "CONSENT_REQUIRED"
```

- [ ] **Step 4: users/__init__.pyのexportに追加**

`from latch.users.errors import (...)`のimportリストに`ConsentRequiredError`を追加し、`__all__`へ`"ConsentRequiredError"`をアルファベット順に追加。

- [ ] **Step 5: consent.pyを実装**

`backend/src/latch/users/consent.py`を新規作成:

```python
"""consent記録・取得の小サービス(M4 ws-5・design §2.1・§2.3・08 D-14)。

users.consent_at(NULL=未同意)へのUPDATE 1本+SELECT 1本+consented判定
SELECT 1本。make_user_serviceと同型のCallable注入(UserServiceへの統合は
不採用 — 契約をroutesとservice関数で固定するため独立モジュールとする)。
時刻はClock由来の明示値のみ。取り消しは非実装(設計§6-2)。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import Clock
from latch.users.errors import (
    DependencyUnavailableError,
    UserNotFoundError,
    UsersError,
)


@dataclass(frozen=True)
class ConsentState:
    """GET/POST /v1/users/me/consentの200応答本体(design §2.3)。"""

    consented_at: datetime | None  # None=未同意


RecordConsentFn = Callable[[AccessTokenClaims, datetime], Awaitable[datetime | None]]
FetchConsentFn = Callable[[AccessTokenClaims], Awaitable[datetime | None]]
ConsentedFn = Callable[[uuid.UUID], Awaitable[bool]]
ExistsFn = Callable[[AccessTokenClaims], Awaitable[bool]]


class ConsentService:
    """record / get / consented のユースケース(design §2.3)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        record_consent: RecordConsentFn,
        fetch_consent: FetchConsentFn,
        consented: ConsentedFn,
        exists_fn: ExistsFn,
    ) -> None:
        self._clock = clock
        self._record_consent = record_consent
        self._fetch_consent = fetch_consent
        self._consented_fn = consented
        self._exists_fn = exists_fn

    async def record(self, *, claims: AccessTokenClaims) -> ConsentState:
        """同意の記録(冪等・後勝ちで時刻更新。08 D-14)。"""
        try:
            at = await self._record_consent(claims, self._clock.now())
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users consent dependency unavailable"
            ) from exc
        if at is None:
            raise UserNotFoundError("user not found")
        return ConsentState(consented_at=at)

    async def get(self, *, claims: AccessTokenClaims) -> ConsentState:
        """同意状態(未同意もConsentState(None)・行なしは404)。"""
        try:
            at = await self._fetch_consent(claims)
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users consent dependency unavailable"
            ) from exc
        if at is None and not await self._row_exists(claims):
            raise UserNotFoundError("user not found")
        return ConsentState(consented_at=at)

    async def consented(self, user_id: uuid.UUID) -> bool:
        """user_id単位の同意判定(intents parseから消費する・設計§2.2第1層)。"""
        try:
            return await self._consented_fn(user_id)
        except Exception as exc:
            raise DependencyUnavailableError(
                "users consent dependency unavailable"
            ) from exc

    async def _row_exists(self, claims: AccessTokenClaims) -> bool:
        """SELECT consent_atのNoneが「未同意」と「行なし」のどちらかを判定。"""
        try:
            return await self._exists_fn(claims)
        except Exception as exc:
            raise DependencyUnavailableError(
                "users consent dependency unavailable"
            ) from exc


def make_consent_service(*, clock: Clock, engine: AsyncEngine) -> ConsentService:
    """実SQL関数(text())を束ねてConsentServiceを構築する(design §2.1)。"""

    async def record_consent(
        claims: AccessTokenClaims, now: datetime
    ) -> datetime | None:
        async with engine.begin() as conn:
            return await conn.scalar(
                _UPDATE_CONSENT,
                {
                    "now": now,
                    "provider": claims.auth_provider,
                    "subject": claims.auth_subject,
                },
            )

    async def fetch_consent(claims: AccessTokenClaims) -> datetime | None:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    _SELECT_CONSENT,
                    {
                        "provider": claims.auth_provider,
                        "subject": claims.auth_subject,
                    },
                )
            ).scalar()

    async def exists_user(claims: AccessTokenClaims) -> bool:
        async with engine.connect() as conn:
            return bool(
                (
                    await conn.execute(
                        _SELECT_EXISTS,
                        {
                            "provider": claims.auth_provider,
                            "subject": claims.auth_subject,
                        },
                    )
                ).scalar()
            )

    async def consented(user_id: uuid.UUID) -> bool:
        async with engine.connect() as conn:
            return bool(
                (
                    await conn.execute(
                        _SELECT_CONSENTED, {"user_id": user_id}
                    )
                ).scalar()
            )

    return ConsentService(
        clock=clock,
        record_consent=record_consent,
        fetch_consent=fetch_consent,
        consented=consented,
        exists_fn=exists_user,
    )


# auth_provider+auth_subjectはUNIQUE(0001)なので高々1行。後勝ち=常にUPDATE
_UPDATE_CONSENT = text("""
    UPDATE users
       SET consent_at = CAST(:now AS timestamptz),
           updated_at = CAST(:now AS timestamptz)
     WHERE auth_provider = :provider AND auth_subject = :subject
    RETURNING consent_at
""")

_SELECT_CONSENT = text("""
    SELECT consent_at FROM users
     WHERE auth_provider = :provider AND auth_subject = :subject
""")

_SELECT_EXISTS = text("""
    SELECT EXISTS(
        SELECT 1 FROM users
         WHERE auth_provider = :provider AND auth_subject = :subject
    )
""")

_SELECT_CONSENTED = text("""
    SELECT EXISTS(
        SELECT 1 FROM users
         WHERE id = CAST(:user_id AS uuid) AND consent_at IS NOT NULL
    )
""")
```

(`exists_fn`は「SELECT consent_atのNone」が未同意と未登録の両方で起きるための区別に使う。`get`のみが消費し、`record`/`consented`は消費しない。Step 1の`_Svc.exists`と`_NoRow`がこの契約に対応する)

- [ ] **Step 6: 試験が通ることを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/users/test_consent_service.py tests/unit/users -q`
Expected: PASS(既存users unit試験も含め全グリーン)

- [ ] **Step 7: lintしてコミット**

Run: `cd backend && .venv/bin/ruff check src/latch/users tests/unit/users`

```bash
git add backend/src/latch/users/errors.py backend/src/latch/users/__init__.py backend/src/latch/users/consent.py backend/tests/unit/users/test_consent_service.py
git commit -m "feat: users同意サービス(consent_at記録・取得・consented判定)"
```

## Task 4: consent APIルート+main.py配線+レート制限Counter

**Files:**
- Modify: `backend/src/latch/users/routes.py`
- Modify: `backend/src/latch/main.py`
- Modify: `backend/tests/unit/test_rate_limit_wiring.py:53-73`(Counter)
- Modify: `backend/tests/unit/users/test_users_routes.py`(Stub追加+新規describe)
- Test: `backend/tests/unit/users/test_users_routes.py`・`backend/tests/unit/test_rate_limit_wiring.py`

**Interfaces:**
- Consumes: Task 3の`ConsentService`・`ConsentState`
- Produces: `POST /v1/users/me/consent`(200・`{"consented_at": string|null}`)・`GET /v1/users/me/consent`(200・同形・未同意はnull)。401/404は既存envelope。router-level `api_rate_limited`を継承(users_routerの`dependencies=[Depends(api_rate_limited)]`)

- [ ] **Step 1: 失敗するルート試験を書く**

`backend/tests/unit/users/test_users_routes.py`へ追加。importに`from datetime import datetime`が既にあるか確認(ある — 9行目)。`ConsentState`をimportに追加:

```python
from latch.users.consent import ConsentState, ConsentService
from latch.users.errors import UserNotFoundError  # 既存importに追加
```

ファイル末尾へ追加:

```python
# --- M4 ws-5: 同意API(計画Task 4) -----------------------------------------


class StubConsentService(ConsentService):
    """ルーティング試験用のConsentServiceスタブ(記録呼び出しを数える)。"""

    def __init__(self, *, state=None, error=None):
        super().__init__(
            clock=FakeClock(NOW),
            record_consent=None,
            fetch_consent=None,
            consented=None,
            exists_fn=None,
        )
        self.state = state if state is not None else ConsentState(consented_at=NOW)
        self.error = error
        self.recorded = 0
        self.fetched = 0

    async def record(self, *, claims):
        self.recorded += 1
        if self.error is not None:
            raise self.error
        return self.state

    async def get(self, *, claims):
        self.fetched += 1
        if self.error is not None:
            raise self.error
        return self.state


async def test_consent_post_returns_200_with_timestamp():
    stub = StubConsentService(state=ConsentState(consented_at=NOW))
    app = create_app(
        clock=FakeClock(NOW),
        settings=Settings(app_env="ci"),
        users_service=StubUserService(),
        consent_service=stub,
    )
    app.dependency_overrides[require_authenticated] = lambda: CLAIMS
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/v1/users/me/consent", headers={"Authorization": "b"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"consented_at": NOW.isoformat()}
    assert stub.recorded == 1


async def test_consent_get_returns_null_when_not_consented():
    """未同意は200 null(404ではない — 計画§5-3)。"""
    stub = StubConsentService(state=ConsentState(consented_at=None))
    app = create_app(
        clock=FakeClock(NOW),
        settings=Settings(app_env="ci"),
        users_service=StubUserService(),
        consent_service=stub,
    )
    app.dependency_overrides[require_authenticated] = lambda: CLAIMS
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/users/me/consent", headers={"Authorization": "b"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"consented_at": None}
    assert stub.fetched == 1


async def test_consent_unregistered_maps_to_404_envelope():
    stub = StubConsentService(error=UserNotFoundError("user not found"))
    app = create_app(
        clock=FakeClock(NOW),
        settings=Settings(app_env="ci"),
        users_service=StubUserService(),
        consent_service=stub,
    )
    app.dependency_overrides[require_authenticated] = lambda: CLAIMS
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/v1/users/me/consent", headers={"Authorization": "b"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
```

- [ ] **Step 2: 試験が失敗することを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/users/test_users_routes.py -q`
Expected: FAIL(create_appにconsent_service引数がない・ConsentServiceのコンストラクタにexists_fnがない)

- [ ] **Step 3: routes.pyへ2ルートを追加**

`backend/src/latch/users/routes.py`: importへ`from datetime import datetime`を追加し、`from latch.users.consent import ConsentService, ConsentState`と`from latch.users.errors`は使わない(サービスが変換済み)。`get_users_service`の後に追加:

```python
def get_consent_service(request: Request) -> ConsentService:
    """app.state.consent_service へのアクセス(lifespanまたはテスト注入)。"""
    return request.app.state.consent_service


class ConsentStateResponse(BaseModel):
    """GET/POST /v1/users/me/consent の200応答(設計§2.3)。"""

    consented_at: datetime | None


@users_router.post("/me/consent", response_model=ConsentStateResponse)
async def record_consent(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[ConsentService, Depends(get_consent_service)],
) -> ConsentStateResponse:
    """同意の記録(冪等・後勝ちで時刻更新。08 D-14)。ログはイベント名のみ。"""
    state: ConsentState = await svc.record(claims=claims)
    logger.info("users.consent_record ok")
    return ConsentStateResponse(consented_at=state.consented_at)


@users_router.get("/me/consent", response_model=ConsentStateResponse)
async def get_consent(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[ConsentService, Depends(get_consent_service)],
) -> ConsentStateResponse:
    """同意状態(未同意も200 null。08 D-14「設定画面から常に再確認できる」)。"""
    state: ConsentState = await svc.get(claims=claims)
    logger.info("users.consent_state ok")
    return ConsentStateResponse(consented_at=state.consented_at)
```

(router-levelの`api_rate_limited`により2ルートは自動で対象になる — deps追加不要)

- [ ] **Step 4: main.pyへconsent_serviceを配線**

`backend/src/latch/main.py`への変更は4点:

1. import追加: `from latch.users.consent import ConsentService, make_consent_service`(importブロックのusers群の近く・Iルールで整形される)
2. `create_app`シグネチャへ`consent_service=None`を追加し、bodyへ既存パターンどおり:
   ```python
   if consent_service is not None:
       app.state.consent_service = consent_service
   ```
3. `_lifespan`冒頭のbuild判定群へ`build_consent = not hasattr(app.state, "consent_service")`を追加し、短絡guardのorリストに`or build_consent`を追加
4. `if build_users:`ブロックの直後に追加:
   ```python
   if build_consent:
       app.state.consent_service = make_consent_service(
           clock=app.state.clock, engine=engine
       )
   ```

- [ ] **Step 5: test_rate_limit_wiring.pyのCounterに追加**

`backend/tests/unit/test_rate_limit_wiring.py`のCounter dict(53-73行目)へ、`"/v1/users/me": 2,`の直後に1行追加:

```python
        "/v1/users/me/consent": 2,  # M4 ws-5(POST + GET)
```

- [ ] **Step 6: 試験が通ることを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/users/test_users_routes.py tests/unit/test_rate_limit_wiring.py -q`
Expected: PASS

- [ ] **Step 7: lintしてコミット**

Run: `cd backend && .venv/bin/ruff check src/latch/users/routes.py src/latch/main.py tests/unit`

```bash
git add backend/src/latch/users/routes.py backend/src/latch/main.py backend/tests/unit/test_rate_limit_wiring.py backend/tests/unit/users/test_users_routes.py
git commit -m "feat: POST/GET /v1/users/me/consent(422遮断の外の同意API・レート制限配線追従)"
```

## Task 5: intents同意ゲート(create active・draft→active)

**Files:**
- Modify: `backend/src/latch/intents/errors.py`(`ConsentRequiredError`追加)
- Modify: `backend/src/latch/intents/__init__.py`(export追加)
- Modify: `backend/src/latch/intents/store.py`(UserRow+_SELECT_USER)
- Modify: `backend/src/latch/intents/service.py`(`_require_consent`+2箇所)
- Modify: `backend/tests/unit/intents/test_intents_service.py`(Stub既定+4箇所+新規3試験)
- Modify: `backend/tests/unit/intents/test_event_publish.py:68`・`test_time_validation.py:55`(UserRow構築1箇所ずつ)
- Test: `backend/tests/unit/intents/test_intents_service.py`

**Interfaces:**
- Consumes: `UserRow.consent_at`(Task 5で追加)
- Produces: `ConsentRequiredError`(IntentsError・422・code="CONSENT_REQUIRED")。create(active)/draft→activeで未同意なら送出。draft保存は許可

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/intents/test_intents_service.py`: importへ`ConsentRequiredError`を追加。`NOW`定数は既存。StubStoreの既定UserRow(81行目)を変更:

```python
            UserRow(id=USER_ID, birth_date=date(2000, 1, 1), consent_at=NOW)  # 26歳・同意済み
```

4箇所の直接構築(368・380・395・788行目)にも`, consent_at=NOW`を追加。`test_event_publish.py:68`と`test_time_validation.py:55`のUserRow構築にも`, consent_at=NOW`を追加。

同ファイル末尾へ新規試験を追加:

```python
# --- M4 ws-5: 同意ゲート(計画Task 5・08 D-14「同意は利用条件」) ------------


async def test_create_active_without_consent_rejected():
    """同意なしのactive作成=422 CONSENT_REQUIRED(遮断3経路の1・計画§5-2)。"""
    svc, _, _, _, _ = _service(
        store=StubStore(user_row=UserRow(id=USER_ID, birth_date=date(2000, 1, 1), consent_at=None))
    )
    with pytest.raises(ConsentRequiredError) as ei:
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),
        )
    assert ei.value.http_status == 422
    assert ei.value.code == "CONSENT_REQUIRED"


async def test_create_draft_without_consent_allowed():
    """draft保存は同意なしで許可(LLM送信なし・設計§2.2)。"""
    svc, _, _, _, _ = _service(
        store=StubStore(user_row=UserRow(id=USER_ID, birth_date=date(2000, 1, 1), consent_at=None))
    )
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=_active_input(),
    )
    assert row.status == "draft"


async def test_activation_from_draft_without_consent_rejected():
    """draft→active化も同意なし=422 CONSENT_REQUIRED(遮断3経路の2)。"""
    row = _draft_row_of(_active_input())  # 既存のdraft行ヘルパー
    svc, _, _, _, _ = _service(
        store=StubStore(
            user_row=UserRow(id=USER_ID, birth_date=date(2000, 1, 1), consent_at=None),
            rows={row.id: row},
        )
    )
    with pytest.raises(ConsentRequiredError):
        await _update(svc, row, structured=_active_input(), status="active")
```

(注: `_update`は既存ヘルパー(632行目・`status`と`structured`を受ける)・`_draft_row_of`は既存のdraft行ヘルパーをそのまま使う)

- [ ] **Step 2: 試験が失敗することを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/intents/test_intents_service.py -q`
Expected: FAIL(UserRowにconsent_atがない → TypeError、またはConsentRequiredErrorが未定義)

- [ ] **Step 3: intents/errors.pyへ追加**

`backend/src/latch/intents/errors.py`の`UnderAgeError`の後に追加:

```python
class ConsentRequiredError(IntentsError):
    """同意なしのactive化・parse(08 D-14「同意はマッチング機能の利用条件」)。

    API層の遮断(設計§2.2第1層)。draft保存は同意不要(LLM送信なし)。
    """

    http_status = 422
    code = "CONSENT_REQUIRED"
```

`backend/src/latch/intents/__init__.py`のerrors importリストと`__all__`に`ConsentRequiredError`をアルファベット順に追加。

- [ ] **Step 4: store.pyのUserRow+_SELECT_USER**

`backend/src/latch/intents/store.py`:

```python
@dataclass(frozen=True)
class UserRow:
    """fetch_user_rowの戻り(年齢・同意検証に必要な最小列 — design §2.6・M4 ws-5)。"""

    id: uuid.UUID
    birth_date: date
    consent_at: datetime | None  # None=未同意(08 D-14・M4 ws-5)
```

`_SELECT_USER`を変更:

```python
_SELECT_USER = text(
    "SELECT id, birth_date, consent_at FROM users "
    "WHERE auth_provider = :provider AND auth_subject = :subject"
)
```

`fetch_user_row`の戻りを変更:

```python
        return UserRow(
            id=_coerce_uuid(row.id), birth_date=row.birth_date, consent_at=row.consent_at
        )
```

- [ ] **Step 5: service.pyへ同意検査を追加**

`backend/src/latch/intents/service.py`: importに`ConsentRequiredError`を追加。`_require_age_20`の後にヘルパーを追加:

```python
    @staticmethod
    def _require_consent(user: UserRow) -> None:
        """同意なしのactive化を遮断(08 D-14・設計§2.2第1層)。draftは許可。"""
        if user.consent_at is None:
            raise ConsentRequiredError("consent required")
```

`_create`のactive分岐uow内(`async with self._uow() as conn:`の直後・`_check_active_limit`より前)に1行追加:

```python
            async with self._uow() as conn:
                self._require_consent(user)
                if self._limiter is not None:
                    await self._check_active_limit(conn, user.id, now=now)
```

`_update_draft`のdraft→active化セクション(「全量必須・全検証」コメントの直後・`_check_active_limit`より前)に1行追加:

```python
        # draft→active化: 全量必須・全検証(不通なら422でdraft据え置き)
        self._require_consent(user)
        if self._limiter is not None:
            await self._check_active_limit(conn, user.id, now=now)
```

- [ ] **Step 6: 試験が通ることを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/intents -q`
Expected: PASS(既存全試験を含む — 既定Stubが同意済みのため期待値不変)

- [ ] **Step 7: lintしてコミット**

Run: `cd backend && .venv/bin/ruff check src/latch/intents tests/unit/intents`

```bash
git add backend/src/latch/intents/errors.py backend/src/latch/intents/__init__.py backend/src/latch/intents/store.py backend/src/latch/intents/service.py backend/tests/unit/intents/test_intents_service.py backend/tests/unit/intents/test_event_publish.py backend/tests/unit/intents/test_time_validation.py
git commit -m "feat: 同意なしのactive化を422 CONSENT_REQUIREDで遮断(create active・draft→active)"
```

## Task 6: parse同意ゲート

**Files:**
- Modify: `backend/src/latch/intents/service.py`(IntentParseService+make_intent_parse_service)
- Modify: `backend/src/latch/main.py`(wiring)
- Test: `backend/tests/unit/intents/test_service.py`

**Interfaces:**
- Consumes: Task 3の`ConsentService.consented(user_id) -> bool`
- Produces: `IntentParseService(*, clock, parser, user_lookup, consent_lookup=None)`。`consent_lookup: Callable[[uuid.UUID], Awaitable[bool]] | None`(None=検査なし・unit既定 — `limiter=None`と同型の無効パターン)

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/intents/test_service.py`: importへ`ConsentRequiredError`を追加。ファイル末尾へ追加:

```python
# --- M4 ws-5: parse同意ゲート(計画Task 6・08 D-14) -------------------------


def _consent_lookup_returning(ok: bool):
    async def lookup(user_id):
        return ok

    return lookup


async def test_parse_blocked_without_consent_and_parser_not_called():
    """同意なし(登録済み)=422 CONSENT_REQUIRED・LLM呼び出し前に遮断。"""
    parser = StubParser()
    svc = IntentParseService(
        clock=_clock(),
        parser=parser,
        user_lookup=_lookup_returning(USER_ID_PARSE),
        consent_lookup=_consent_lookup_returning(False),
    )
    with pytest.raises(ConsentRequiredError) as ei:
        await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert ei.value.http_status == 422
    assert ei.value.code == "CONSENT_REQUIRED"
    assert parser.calls == []  # 送信前に遮断(D-14の本体)


async def test_parse_proceeds_when_consented():
    parser = StubParser()
    svc = IntentParseService(
        clock=_clock(),
        parser=parser,
        user_lookup=_lookup_returning(USER_ID_PARSE),
        consent_lookup=_consent_lookup_returning(True),
    )
    result = await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert result.structured_intent.category is not None


async def test_parse_without_consent_lookup_keeps_current_behavior():
    """consent_lookup=None=検査なし(limiter=Noneと同型の無効パターン)。"""
    parser = StubParser()
    svc = IntentParseService(
        clock=_clock(),
        parser=parser,
        user_lookup=_lookup_returning(USER_ID_PARSE),
    )
    result = await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert len(parser.calls) == 1


async def test_parse_unregistered_jwt_skips_consent_check():
    """未登録JWT(user_lookup=None)は検査せず現行どおり(g1gate資産保護・計画§4-2)。"""
    parser = StubParser()
    called = []

    async def lookup(user_id):
        called.append(user_id)  # 呼ばれたら記録(呼ばれないことが期待)
        return False

    svc = IntentParseService(
        clock=_clock(),
        parser=parser,
        user_lookup=_lookup_none,
        consent_lookup=lookup,
    )
    result = await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert result.structured_intent is not None
    assert called == []  # 未登録には同意検査を掛けない


async def test_parse_consent_lookup_failure_maps_to_503():
    async def broken(user_id):
        raise RuntimeError("db down")

    svc = IntentParseService(
        clock=_clock(),
        parser=StubParser(),
        user_lookup=_lookup_returning(USER_ID_PARSE),
        consent_lookup=broken,
    )
    with pytest.raises(DependencyUnavailableError) as ei:
        await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert ei.value.http_status == 503
```

(注: `USER_ID_PARSE = uuid.uuid4()`を追加ブロックの直前に定義。`_lookup_returning`・`_lookup_none`・`StubParser`・`DependencyUnavailableError`は同ファイルの既存定義を使う)

- [ ] **Step 2: 試験が失敗することを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/intents/test_service.py -q`
Expected: FAIL(consent_lookup引数がない)

- [ ] **Step 3: 実装**

`backend/src/latch/intents/service.py`:

1. importに`ConsentRequiredError`を追加。`UserLookup`定義の近くへ追加:
   ```python
   ConsentLookup = Callable[[uuid.UUID], Awaitable[bool]]
   ```
2. `IntentParseService.__init__`に`consent_lookup: ConsentLookup | None = None`を追加して`self._consent_lookup = consent_lookup`。
3. `parse`メソッドのuser_lookupブロックの直後に追加:
   ```python
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
4. `make_intent_parse_service`に`consent_lookup: ConsentLookup | None = None`引数を追加し、戻りの`IntentParseService(...)`へ渡す。

`backend/src/latch/main.py`: `make_intent_parse_service(...)`呼び出し(build_intents分岐)へ`consent_lookup=app.state.consent_service.consented,`を1行追加(build_intentsが真のときconsent_serviceは構築済みまたは注入済み — guardが保証)。

- [ ] **Step 4: 試験が通ることを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/intents tests/unit/g1gate -q`
Expected: PASS(g1gateは無変更で通る — consent_lookup既定Noneのため)

- [ ] **Step 5: lintしてコミット**

Run: `cd backend && .venv/bin/ruff check src/latch/intents src/latch/main.py tests/unit/intents`

```bash
git add backend/src/latch/intents/service.py backend/src/latch/main.py backend/tests/unit/intents/test_service.py
git commit -m "feat: parseを同意ゲート化(LLM送信前・登録済み未同意のみ遮断)"
```

## Task 7: Layer 1 Hard条件+既存integrationヘルパーの同意付与

**Files:**
- Modify: `backend/src/latch/worker/matching/layer1.py:37-55`(HEAD末尾)
- Modify: `backend/tests/unit/matching/test_layer_sql.py`(ピン追加)
- Modify: `backend/tests/integration/test_matching_hardfilter.py`(test_14追加)
- Modify: 影響表§3-1の20ファイル21ヘルパー(同意記録2行)
- Test: `backend/tests/unit/matching/test_layer_sql.py`(実行可)+`test_matching_hardfilter.py`(integration・collect-only)

**Interfaces:**
- Consumes: users.consent_at(Task 2)
- Produces: `LAYER1_WHERE`・`LAYER1_WHERE_BASE`に同意条件が入る(HEADに入るため全埋め込みSQL — layer2・layer4・group_engine — に自動反映・**これらのファイルは無変更**)

- [ ] **Step 1: 失敗するSQLピン試験を書く**

`backend/tests/unit/matching/test_layer_sql.py`の`test_layer1_where_keeps_participants_and_unchanged_pins`の後に追加:

```python
def test_layer1_where_requires_peer_consent():
    """同意なしユーザーのIntentはpeer抽出対象外(M4 ws-5・08 D-14)。

    HEADに入るためLAYER1_WHERE_BASE(グループPool検索・緩和H再検証)にも
    効く。peer側のみを検査する(起点側はAPI遮断が担保 — 計画§4-1)。
    """
    assert "AND u.consent_at IS NOT NULL" in layer1.LAYER1_WHERE
    assert "AND u.consent_at IS NOT NULL" in layer1.LAYER1_WHERE_BASE
    # 検査対象はu(peer側ユーザー)・origin側のユーザー行は条件に現れない
    assert ":origin" not in layer1.LAYER1_WHERE.split("u.consent_at")[1][:40]
```

- [ ] **Step 2: 試験が失敗することを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/matching/test_layer_sql.py -q`
Expected: FAIL(同意条件がまだない)

- [ ] **Step 3: layer1.pyのHEADに条件1行を追加**

`LAYER1_WHERE_HEAD`(37-55行目)の予算条件の後に1行追加:

```python
LAYER1_WHERE_HEAD = f"""
    i.status = 'active'
    AND i.embedding IS NOT NULL
    AND i.user_id <> CAST(:origin_user_id AS uuid)
    AND i.category_primary = :origin_category
    AND i.time_start < :origin_time_end
    AND :origin_time_start < COALESCE(i.time_end, i.time_start + interval '3 hours')
    AND i.geo_center IS NOT NULL
    AND ST_DWithin(
        i.geo_center,
        ST_SetSRID(
            ST_MakePoint(CAST(:origin_lon AS float8), CAST(:origin_lat AS float8)),
            4326)::geography,
        CAST(
            CAST(:origin_radius_m AS int) + COALESCE(i.geo_radius_m, 1000)
            AS double precision))
    AND ({_BUDGET_PAIR} IS NULL
         OR {_BUDGET_PAIR} >= {PAIR_BUDGET_MIN_YEN})
    AND u.consent_at IS NOT NULL
"""
```

(コメントは**f-stringの外**・`LAYER1_WHERE_HEAD`代入文の直前にPythonコメントとして足す。f-stringの中に`#`行を入れるとPostgreSQLのコメント構文(`--`)でないためSQLが壊れる): `# 08 D-14: 同意はマッチング機能の利用条件(M4 ws-5。peer側検査 — 計画§4-1)`

- [ ] **Step 4: ピン試験と既存unitが通ることを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/matching -q`
Expected: PASS。特に既存の`test_layer1_where_excludes_visibility`(「visibility」を含まない)・`test_layer4.py:205`(`LAYER1_WHERE_BASE.strip()[:40]`ピン・先頭はi.statusのままで不変)・`test_group_engine.py:306`が**無変更で通ること**(影響表§3-2の根拠)

- [ ] **Step 5: integrationヘルパー20ファイル21箇所に同意記録2行を追加**

各ファイルのユーザー登録ヘルパー内、`assert created.status_code == 201, created.text`の直後に挿入する(全ヘルパー共通の同一2行):

```python
    consented = await api_client.post("/v1/users/me/consent", headers=headers)
    assert consented.status_code == 200, consented.text
```

対象ヘルパー全数(「シード・既存試験ユーザーを同意済みにする運用」— 設計§2.2。POST /v1/users後にactive Intentを作る経路を持つファイルの全数。`test_users_api.py`は登録契約そのものを試すため対象外・`test_intents_parse_api.py`は未登録JWT経路のため対象外・`test_schema.py`はユーザーを作らない):

| # | ファイル | ヘルパー(行はマージ時点の目安) |
|---|---|---|
| 1 | tests/integration/test_matching_hardfilter.py | `_register`(56) |
| 2 | tests/integration/test_matching_retrieval.py | `_user`(107) |
| 3 | tests/integration/test_matching_cheapjudge.py | `_user`(124) |
| 4 | tests/integration/test_matching_jev.py | `_user`(127) |
| 5 | tests/integration/test_matching_latchengine.py | `_user`(160) |
| 6 | tests/integration/test_matching_groupengine.py | `_user`(167) |
| 7 | tests/integration/test_notifications_api.py | `_user`(100)・`_user_with_subject`(117)の2箇所 |
| 8 | tests/integration/test_degraded_e2e.py | `_user`(172) |
| 9 | tests/integration/test_g3_acceptance_e2e.py | `_user`(103) |
| 10 | tests/integration/test_k_limits_e2e.py | `_user`(168) |
| 11 | tests/integration/test_events_pipeline.py | `_register`(49) |
| 12 | tests/integration/test_embedding_pipeline.py | `_register`(65) |
| 13 | tests/integration/test_intents_crud_api.py | `_register`(48) |
| 14 | tests/integration/test_ratelimit_api.py | `_register`(50) |
| 15 | tests/integration/test_safety_api.py | `_user`(163) |
| 16 | tests/integration/test_account_api.py | `_user`(140)(`_user_tracked`(253)は`_user`経由のため追従不要) |
| 17 | tests/integration/test_deletion_api.py | `_user`(133) |
| 18 | tests/integration/test_latches_api.py | `_user`(124) |
| 19 | tests/integration/test_chat_attendance_api.py | `_user`(143) |
| 20 | tests/integration/test_expiry_batches.py | `_user`(148) |

挿入後、各ファイルで`rg -n "created.status_code == 201" <file>`の直下に同意2行があることを目視確認する。

- [ ] **Step 6: hardfilterへ同意なしpeer除外ケースを追加**

`backend/tests/integration/test_matching_hardfilter.py`の`test_13`の後に追加(integration・collect-onlyのみ):

```python
async def test_14_consent_required_for_peer(api_client, db_engine, field):
    """M4 ws-5: 同意なしユーザーのIntentは他条件が揃ってもpeer抽出対象外
    (08 D-14「同意はマッチング機能の利用条件」・Layer 1 Hard条件)。

    同意済みの同条件ペアが通ることの対照(既存試験の期待値が変わらない
    ことの実証を兼ねる)→ 対象ユーザーのconsent_atをDB直接UPDATEでNULLへ
    戻して除外されること(test_7と同じAPI検証バイパスの手法)。
    """
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, t_user = await _user(api_client, field)
    t = await _intent(api_client, db_engine, h1, _payload(_structured()))

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t["id"] in ids  # 対照: 双方同意済みなら通る(既存期待値どおり)

    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET consent_at = NULL WHERE id = CAST(:u AS uuid)"),
            {"u": t_user},
        )
    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t["id"] not in ids  # 対象が未同意 → peer除外
```

- [ ] **Step 7: collect-onlyとunitグリーンを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_matching_hardfilter.py --collect-only -q | tail -3`
Expected: collect成功(test_14を含む・実行はしない)

Run: `cd backend && .venv/bin/python -m pytest tests/unit -q`
Expected: PASS(全unitグリーン)

- [ ] **Step 8: lintしてコミット**

Run: `cd backend && .venv/bin/ruff check src/latch/worker/matching/layer1.py tests`

```bash
git add backend/src/latch/worker/matching/layer1.py backend/tests/unit/matching/test_layer_sql.py backend/tests/integration/
git commit -m "feat: Layer 1に同意Hard条件(AND u.consent_at IS NOT NULL)+試験ユーザーの同意付与+peer除外試験"
```

## Task 8: integration試験(test_consent_api.py)

**Files:**
- Create: `backend/tests/integration/test_consent_api.py`
- Test: 同ファイル(integration・**collect-onlyのみ。実行はスーパーバイザー検証時・apiイメージ再構築後**)

**Interfaces:**
- Consumes: Task 2(0007)・Task 4(ルート)・Task 5・6(遮断)

- [ ] **Step 1: 試験ファイルを書く**

`backend/tests/integration/test_consent_api.py`を新規作成:

```python
"""consent API(M4 ws-5 計画Task 8・design §2.3・§4)のintegration試験。

実HTTP(compose常設api)。POST/GET /v1/users/me/consent・401・404(未登録)・
422 CONSENT_REQUIREDの遮断3経路(parse・active作成・draft→active)と同意後の
解除、users.consent_at列の存在(0007)を検証する。実行はtest-ci
(スーパーバイザー検証時・apiイメージ再構築後)。レート制限はルータ単位の
api_rate_limited継承をtest_rate_limit_wiring.pyが静的担保する(M1 ws-4流儀)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration


async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        provider,
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def _token(api_client, subject: str) -> str:
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": await _cli_idp_token("google", subject)}
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


async def _register(api_client, *, consent: bool) -> dict:
    """ユーザー登録(同意する/しないを選べる)。headersとsubjectを返す。"""
    subject = f"ws5-{uuid_mod.uuid4().hex[:12]}"
    access = await _token(api_client, subject)
    headers = {"Authorization": f"Bearer {access}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "ws5", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    if consent:
        resp = await api_client.post("/v1/users/me/consent", headers=headers)
        assert resp.status_code == 200, resp.text
    return {"headers": headers, "subject": subject}


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured() -> dict:
    return {
        "category": {"primary": "meal", "secondary": None},
        "alcohol_involved": False,
        "time": {"start": _future(3), "end": None},
        "location": {"name": "天文館"},
    }


# -- スキーマ(0007) --


async def test_users_consent_at_column_exists(db_engine):
    """0007適用でusersにconsent_at timestamptzがある。"""
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'users' AND column_name = 'consent_at'"
                )
            )
        ).first()
    assert row is not None and row[0] == "timestamp with time zone"


# -- 同意APIの基本挙動 --


async def test_get_returns_null_then_post_records(api_client):
    """未同意=200 null → POST 200(時刻入)→ GET 200(時刻入)。
    再POSTは後勝ちで時刻更新(冪等200)。"""
    user = await _register(api_client, consent=False)
    h = user["headers"]
    before = await api_client.get("/v1/users/me/consent", headers=h)
    assert before.status_code == 200, before.text
    assert before.json() == {"consented_at": None}

    first = await api_client.post("/v1/users/me/consent", headers=h)
    assert first.status_code == 200, first.text
    at1 = first.json()["consented_at"]
    assert isinstance(at1, str)

    again = await api_client.get("/v1/users/me/consent", headers=h)
    assert again.json() == {"consented_at": at1}

    second = await api_client.post("/v1/users/me/consent", headers=h)
    assert second.status_code == 200
    assert second.json()["consented_at"] >= at1  # 後勝ち(同時刻以上)


async def test_unauthenticated_consent_is_401(api_client):
    assert (await api_client.get("/v1/users/me/consent")).status_code == 401
    assert (await api_client.post("/v1/users/me/consent")).status_code == 401


async def test_unregistered_consent_is_404(api_client):
    """登録前JWT(行なし)は404 NOT_FOUND(users/meと同一挙動)。"""
    access = await _token(api_client, f"ws5-unreg-{uuid_mod.uuid4().hex[:8]}")
    h = {"Authorization": f"Bearer {access}"}
    resp = await api_client.get("/v1/users/me/consent", headers=h)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# -- 422 CONSENT_REQUIREDの遮断3経路+同意後の解除 --


async def test_parse_blocked_without_consent_then_allowed(api_client):
    """経路1: parse(登録済み未同意=422・LLM送信前)。同意後は200。"""
    user = await _register(api_client, consent=False)
    h = user["headers"]
    blocked = await api_client.post(
        "/v1/intents/parse", headers=h, json={"text": "今夜 天文館で飲みたい"}
    )
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"  # フロント誘導分岐の契約

    await api_client.post("/v1/users/me/consent", headers=h)
    ok = await api_client.post(
        "/v1/intents/parse", headers=h, json={"text": "今夜 天文館で飲みたい"}
    )
    assert ok.status_code == 200, ok.text


async def test_active_create_blocked_without_consent_then_allowed(api_client):
    """経路2: POST /v1/intents(status省略=active既定も含む)。同意後は201。"""
    user = await _register(api_client, consent=False)
    h = user["headers"]
    payload = {
        "raw_text": "今夜 天文館で飲みたい",
        "structured_intent": _structured(),
    }
    blocked = await api_client.post("/v1/intents", headers=h, json=payload)
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"

    blocked_explicit = await api_client.post(
        "/v1/intents", headers=h, json={**payload, "status": "active"}
    )
    assert blocked_explicit.status_code == 422

    await api_client.post("/v1/users/me/consent", headers=h)
    ok = await api_client.post("/v1/intents", headers=h, json=payload)
    assert ok.status_code == 201, ok.text


async def test_draft_allowed_and_activation_blocked_then_allowed(api_client):
    """経路3: draft保存は許可・draft→active化は遮断。同意後は遷移できる。"""
    user = await _register(api_client, consent=False)
    h = user["headers"]
    draft = await api_client.post(
        "/v1/intents",
        headers=h,
        json={"raw_text": "今夜 天文館で飲みたい", "status": "draft", "structured_intent": _structured()},
    )
    assert draft.status_code == 201, draft.text  # draftは同意不要(LLM送信ゼロ)
    intent_id = draft.json()["intent"]["id"]

    blocked = await api_client.patch(
        f"/v1/intents/{intent_id}",
        headers=h,
        json={"raw_text": "今夜 天文館で飲みたい", "status": "active", "structured_intent": _structured()},
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"

    # draftは据え置き(遷移しない)
    still = await api_client.get(f"/v1/intents/{intent_id}", headers=h)
    assert still.json()["intent"]["status"] == "draft"

    await api_client.post("/v1/users/me/consent", headers=h)
    ok = await api_client.patch(
        f"/v1/intents/{intent_id}",
        headers=h,
        json={"raw_text": "今夜 天文館で飲みたい", "status": "active", "structured_intent": _structured()},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["intent"]["status"] == "active"
```

- [ ] **Step 2: collect-onlyで収集できることを確認**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_consent_api.py --collect-only -q | tail -3`
Expected: collect成功(8試験)。**実行はしない**

- [ ] **Step 3: basename衝突確認+コミット**

Run: `find backend/tests -name 'test_*.py' -printf '%f\n' | sort | uniq -d`
Expected: 出力なし

```bash
git add backend/tests/integration/test_consent_api.py
git commit -m "test: consent API遮断3経路+同意後解除のintegration(実行はスーパーバイザー検証時)"
```

## Task 9: フロントエンド(オンボーディング+設定第3セクション+誘導)

**Files:**
- Modify: `frontend/src/latch/texts.js`(末尾へ定数追加)
- Modify: `frontend/src/router.js:8-13`(parseHash 1行)
- Modify: `frontend/index.html`(onboarding section+設定第3セクション)
- Create: `frontend/src/latch/consent.js`
- Modify: `frontend/src/latch/settings.js`(引数拡張)
- Modify: `frontend/src/main.js`(生成+422横断検出+誘導2本+screens)
- Modify: `frontend/tests/router.test.js`・`frontend/tests/latch-settings.test.js`
- Create: `frontend/tests/latch-consent.test.js`

**Interfaces:**
- Consumes: Task 4の`POST/GET /v1/users/me/consent`(client.call)・`ApiError`(status+code — client.js既存)
- Produces: `createConsent({ client, chrome, el: { onboarding, settings } })` → `{ load, show, ensureGuidance, notifyRequired }`。`createSettings({ permissionControl, blocks, consent })`

- [ ] **Step 1: 失敗するvitestを書く**

`frontend/tests/latch-consent.test.js`を新規作成:

```js
// 同意フローのvitest(M4 ws-5 計画Task 9)。文言はtexts.js定数・APIは
// client.callのスタブ。happy-domのDOMで描画を検証する(permission.js試験と同型)。
import { describe, expect, it, vi } from "vitest";
import {
  CONSENT_AGREE_LABEL,
  CONSENT_CHECK_LABEL,
  CONSENT_CONDITION_TEXT,
  CONSENT_DESTINATIONS_TEXT,
  CONSENT_LEAD_TEXT,
  CONSENT_NO_TRAINING_TEXT,
  CONSENT_REQUIRED_TOAST_TEXT,
  CONSENT_SCOPE_TEXT,
  CONSENT_STATE_NOT_AGREED_TEXT,
  CONSENT_TITLE,
  CONSENT_TOAST_TEXT,
} from "../src/latch/texts.js";
import { createConsent } from "../src/latch/consent.js";

const NOW_ISO = "2026-10-03T12:00:00+00:00";

const mountConsent = async ({ consented_at = null, postError = false } = {}) => {
  const onboarding = document.createElement("div");
  const settings = document.createElement("div");
  const client = {
    call: vi.fn(async (method, path) => {
      if (method === "GET" && path === "/v1/users/me/consent") {
        return { consented_at };
      }
      if (method === "POST" && path === "/v1/users/me/consent") {
        if (postError) throw new Error("network");
        return { consented_at: NOW_ISO };
      }
      throw new Error(`unexpected ${method} ${path}`);
    }),
  };
  const chrome = { showToast: vi.fn() };
  const consent = createConsent({ client, chrome, el: { onboarding, settings } });
  return { consent, client, chrome, onboarding, settings };
};

// notifyRequiredはfetchしないため同期待ち(chromeのトーストとhash遷移のみ検証)
const mountConsentSync = () => {
  const onboarding = document.createElement("div");
  const settings = document.createElement("div");
  const client = { call: vi.fn() };
  const chrome = { showToast: vi.fn() };
  const consent = createConsent({ client, chrome, el: { onboarding, settings } });
  return { consent, chrome };
};

describe("オンボーディング画面(design §2.4)", () => {
  it("文言表示: 承認済み文言の全定数が載る", async () => {
    const { consent, onboarding } = await mountConsent();
    await consent.show();
    const text = onboarding.textContent;
    expect(text).toContain(CONSENT_TITLE);
    expect(text).toContain(CONSENT_LEAD_TEXT);
    expect(text).toContain(CONSENT_DESTINATIONS_TEXT);
    expect(text).toContain(CONSENT_SCOPE_TEXT);
    expect(text).toContain(CONSENT_NO_TRAINING_TEXT);
    expect(text).toContain(CONSENT_CONDITION_TEXT);
  });

  it("未同意: チェックボックスとボタンがあり・未チェック時はボタン無効", async () => {
    const { consent, onboarding } = await mountConsent({ consented_at: null });
    await consent.show();
    const check = onboarding.querySelector("[data-role=consent-check]");
    const button = onboarding.querySelector("[data-role=consent-agree]");
    expect(check).not.toBeNull();
    expect(button.disabled).toBe(true);
    check.checked = true;
    check.dispatchEvent(new Event("change"));
    expect(button.disabled).toBe(false);
    expect(button.textContent).toBe(CONSENT_AGREE_LABEL);
    expect(onboarding.textContent).toContain(CONSENT_CHECK_LABEL);
    expect(onboarding.textContent).toContain(CONSENT_STATE_NOT_AGREED_TEXT);
  });

  it("同意済み: 操作なし・状態と日時を表示", async () => {
    const { consent, onboarding } = await mountConsent({ consented_at: NOW_ISO });
    await consent.show();
    expect(onboarding.querySelector("[data-role=consent-agree]")).toBeNull();
    expect(onboarding.textContent).toContain("同意済み");
  });

  it("POST成功: 同意APIを叩き・トースト・ホームへ戻る", async () => {
    const { consent, client, chrome, onboarding } = await mountConsent();
    await consent.show();
    const check = onboarding.querySelector("[data-role=consent-check]");
    check.checked = true;
    check.dispatchEvent(new Event("change"));
    onboarding.querySelector("[data-role=consent-agree]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(client.call).toHaveBeenCalledWith("POST", "/v1/users/me/consent");
    expect(chrome.showToast).toHaveBeenCalledWith(CONSENT_TOAST_TEXT);
    expect(onboarding.textContent).toContain("同意済み");
    expect(window.location.hash).toBe("#/");
  });

  it("POST失敗: ボタンを再試行可能なまま戻す", async () => {
    const { consent, onboarding } = await mountConsent({ postError: true });
    await consent.show();
    const check = onboarding.querySelector("[data-role=consent-check]");
    check.checked = true;
    check.dispatchEvent(new Event("change"));
    const button = onboarding.querySelector("[data-role=consent-agree]");
    button.click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(button.disabled).toBe(false);
    expect(onboarding.textContent).toContain(CONSENT_STATE_NOT_AGREED_TEXT);
  });
});

describe("設定画面第3セクション(design §2.4・D-14「設定画面から常に再確認できる」)", () => {
  it("loadは設定mountへ文言と状態を描く(操作要素なし)", async () => {
    const { consent, settings } = await mountConsent({ consented_at: NOW_ISO });
    await consent.load();
    expect(settings.textContent).toContain(CONSENT_TITLE);
    expect(settings.textContent).toContain("同意済み");
    expect(settings.querySelector("button")).toBeNull();
  });
});

describe("誘導経路2本(design §2.4)", () => {
  it("ensureGuidance: 未同意なら #/onboarding へ遷移", async () => {
    const { consent } = await mountConsent({ consented_at: null });
    await consent.ensureGuidance();
    expect(window.location.hash).toBe("#/onboarding");
  });

  it("ensureGuidance: 同意済みなら遷移しない", async () => {
    const { consent } = await mountConsent({ consented_at: NOW_ISO });
    window.location.hash = "#/";
    await consent.ensureGuidance();
    expect(window.location.hash).toBe("#/");
  });

  it("notifyRequired: トースト+ #/onboarding へ遷移(422検出の後段)", () => {
    const { consent, chrome } = mountConsentSync();
    consent.notifyRequired();
    expect(chrome.showToast).toHaveBeenCalledWith(CONSENT_REQUIRED_TOAST_TEXT);
    expect(window.location.hash).toBe("#/onboarding");
  });
});
```

(`mountConsentSync`は`mountConsent`からawaitを除いた同型ヘルパー — notifyRequiredはfetchしないため。ファイル冒頭の`afterEach(() => { window.location.hash = ""; })`をrouter.test.jsと同型で入れる)

- [ ] **Step 2: 試験が失敗することを確認**

Run: `cd frontend && npm test -- latch-consent`
Expected: FAIL(consent.jsが存在しない)

- [ ] **Step 3: texts.jsへ定数追加**

`frontend/src/latch/texts.js`の末尾へ追加(**文言は設計§2.4の2026-10-03ユーザー承認済み確定版。一字一句変えない・「・」は`<ul>`で表現するため定数から除く**):

```js
// --- M4 ws-5追記: 告知・同意(08 D-14・2026-10-03ユーザー承認済み文言) -----

export const CONSENT_TITLE = "LLM送信の同意";
export const CONSENT_LEAD_TEXT =
  "LATCHでは、あなたが入力したIntentの内容を、マッチング判定のために外部のAIサービスへ送信します。";
export const CONSENT_DESTINATIONS_TEXT =
  "送信先: TypeSafe AI(マッチングの判定)/ Anthropic(条件の読み取り・判定の予備)/ Google Gemini(条件の数値化)";
export const CONSENT_SCOPE_TEXT =
  "送信するのは判定に必要な最小限の情報で、送信記録を保持します(お問い合わせいただければ開示します)";
export const CONSENT_NO_TRAINING_TEXT = "送信データは、AIサービスの学習には利用されません";
export const CONSENT_CONDITION_TEXT =
  "この同意は、マッチング機能を利用するための条件です。同意がない場合、Intentを預ける(マッチングに供給する)ことはできません。";
export const CONSENT_CHECK_LABEL = "上記に同意します";
export const CONSENT_AGREE_LABEL = "同意する";
export const CONSENT_STATE_AGREED_TEXT = "同意済み: ";
export const CONSENT_STATE_NOT_AGREED_TEXT = "同意していません";
export const CONSENT_TOAST_TEXT = "同意を記録しました";
export const CONSENT_REQUIRED_TOAST_TEXT =
  "マッチングの利用には同意が必要です。同意画面へ移動します。";
```

- [ ] **Step 4: router.jsへ1行追加**

```js
export const parseHash = (hash) => {
  if (hash === "#/settings") return { name: "settings", id: null };
  if (hash === "#/onboarding") return { name: "onboarding", id: null }; // M4 ws-5・厳密一致
  const match = LATCH_HASH.exec(hash ?? "");
  if (match) return { name: "latch", id: match[1] };
  return { name: "home", id: null };
};
```

- [ ] **Step 5: index.htmlへDOM追加**

`#settingsScreen`のsettings-inner内・ブロック管理sectionの**後**に第3セクション:

```html
            <section class="settings-section" aria-labelledby="consentSettingsTitle">
              <div class="section-heading"><span aria-hidden="true"></span><h2 id="consentSettingsTitle">LLM送信の同意</h2></div>
              <div id="consentControl"></div>
            </section>
```

`#detailScreen`の**前**(`<div id="homeScreen">`の閉じタグの後)にオンボーディング画面:

```html
        <section class="onboarding-view" id="onboardingScreen" hidden>
          <div class="settings-inner">
            <p class="section-kicker"><span></span>ONBOARDING</p>
            <h1>LLM送信の同意</h1>
            <div id="consentOnboarding"></div>
          </div>
        </section>
```

- [ ] **Step 6: consent.jsを作成**

`frontend/src/latch/consent.js`を新規作成:

```js
// 同意フロー(M4 ws-5 計画Task 9・08 D-14)。オンボーディング画面(#/onboarding)
// と設定画面第3セクション「LLM送信の同意」を制御する。文言はtexts.jsの
// CONSENT_*定数(2026-10-03ユーザー承認済み・単一ソース)。APIは
// POST/GET /v1/users/me/consent(冪等・後勝ち)。通知許可(D-18・OS通知)とは
// 根拠も効果も別物のため、タイトルで明示分離する(design §2.4)。
import {
  CONSENT_AGREE_LABEL,
  CONSENT_CHECK_LABEL,
  CONSENT_CONDITION_TEXT,
  CONSENT_DESTINATIONS_TEXT,
  CONSENT_LEAD_TEXT,
  CONSENT_NO_TRAINING_TEXT,
  CONSENT_REQUIRED_TOAST_TEXT,
  CONSENT_SCOPE_TEXT,
  CONSENT_STATE_AGREED_TEXT,
  CONSENT_STATE_NOT_AGREED_TEXT,
  CONSENT_TITLE,
  CONSENT_TOAST_TEXT,
} from "./texts.js";
import { escapeHtml, noticeTimeText } from "./view.js";

const buildBody = () => `
  <p class="consent-lead">${escapeHtml(CONSENT_LEAD_TEXT)}</p>
  <ul class="consent-points">
    <li>${escapeHtml(CONSENT_DESTINATIONS_TEXT)}</li>
    <li>${escapeHtml(CONSENT_SCOPE_TEXT)}</li>
    <li>${escapeHtml(CONSENT_NO_TRAINING_TEXT)}</li>
  </ul>
  <p class="consent-condition">${escapeHtml(CONSENT_CONDITION_TEXT)}</p>`;

const stateLine = (consentedAt) =>
  consentedAt == null
    ? CONSENT_STATE_NOT_AGREED_TEXT
    : `${CONSENT_STATE_AGREED_TEXT}${noticeTimeText(consentedAt)}`;

export const createConsent = ({ client, chrome, el }) => {
  // el: { onboarding: $("#consentOnboarding"), settings: $("#consentControl") }
  let state = { consented_at: null };

  const renderInto = (mount, { interactive }) => {
    const agreed = state.consented_at != null;
    mount.innerHTML = `
      <p class="consent-title">${escapeHtml(CONSENT_TITLE)}</p>
      ${buildBody()}
      <p class="consent-state" data-role="consent-state">${escapeHtml(stateLine(state.consented_at))}</p>
      ${
        agreed || !interactive
          ? ""
          : `<label class="consent-check"><input type="checkbox" data-role="consent-check" /> ${escapeHtml(CONSENT_CHECK_LABEL)}</label>
             <button type="button" class="submit-button" data-role="consent-agree" disabled>${escapeHtml(CONSENT_AGREE_LABEL)}</button>`
      }`;
    if (agreed || !interactive) return;
    const check = mount.querySelector("[data-role=consent-check]");
    const button = mount.querySelector("[data-role=consent-agree]");
    check.addEventListener("change", () => {
      button.disabled = !check.checked; // 未チェック時は無効(design §2.4②)
    });
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        state = await client.call("POST", "/v1/users/me/consent"); // design §2.4③
        chrome.showToast(CONSENT_TOAST_TEXT);
        renderInto(el.onboarding, { interactive: true });
        location.hash = "#/"; // 同意済み → ホームへ戻す
      } catch {
        button.disabled = false; // 通信失敗時は再試行可能なまま(状態維持)
      }
    });
  };

  const fetchState = async () => {
    state = await client.call("GET", "/v1/users/me/consent"); // design §2.4④
    return state;
  };

  return {
    async load() {
      // 設定画面第3セクションの再確認表示(現在状態・同意日時・文言全文)
      await fetchState();
      renderInto(el.settings, { interactive: false });
    },
    async show() {
      // オンボーディング画面(チェックボックス+ボタン付き)
      await fetchState();
      renderInto(el.onboarding, { interactive: true });
    },
    async ensureGuidance() {
      // 誘導経路(a): 未同意ならオンボーディングへ
      const current = await fetchState();
      if (current.consented_at == null) location.hash = "#/onboarding";
    },
    notifyRequired() {
      // 誘導経路(b): 422 CONSENT_REQUIRED検出時(main.jsの横断検出から呼ばれる)
      chrome.showToast(CONSENT_REQUIRED_TOAST_TEXT);
      location.hash = "#/onboarding";
    },
  };
};
```

- [ ] **Step 7: settings.jsを拡張**

```js
// 設定画面(M3 ws-8 design §2.4・M4 ws-5)。3セクション(通知許可・ブロック管理・
// LLM送信の同意)のマウントと#/settings表示。規定のない要素(プロフィール編集・
// ログアウト・テーマ等)は置かない(design §1.4)。通知許可(D-18)とLLM送信同意
// (D-14)は根拠も効果も別物のため区分表示を維持する。
export const createSettings = ({ permissionControl, blocks, consent }) => ({
  show() {
    permissionControl.render();
    blocks.load().catch(() => {}); // 通信失敗時は空状態のまま(再訪で再試行)
    consent.load().catch(() => {}); // 第3セクション(LLM送信の同意・M4 ws-5)
  },
});
```

- [ ] **Step 8: main.jsを配線**

変更4点:

1. import追加: `import { createConsent } from "./latch/consent.js";`
2. client生成(21-25行目)を横断検出つきに差し替え(422 CONSENT_REQUIREDの全操作検出 — 設計§2.4誘導経路(b)):
   ```js
   // 422 CONSENT_REQUIREDの横断検出(誘導経路(b))。全Intent操作はこのcallを
   // 通るため、個別画面の変更なしで全経路を拾う。consentは後段で生成するため
   // 参照解決は呼び出し時(実行時)。
   let consent;
   const baseClient = createClient({
     session,
     onSessionExpired: () => showTokenPanel(),
   });
   const client = {
     call: async (method, path, opts) => {
       try {
         return await baseClient.call(method, path, opts);
       } catch (err) {
         if (err?.status === 422 && err?.code === "CONSENT_REQUIRED") {
           consent?.notifyRequired();
         }
         throw err;
       }
     },
   };
   ```
   (元の`const client = createClient({...})`は`baseClient`へ改名)
3. settings生成(140行目)を変更+consent生成をその直前に追加。**consentはclient定義時に`let consent;`で宣言済みのため、ここは宣言ではなく代入(`const`を付けない — 再宣言はSyntaxErrorになる)**:
   ```js
   consent = createConsent({
     client,
     chrome,
     el: { onboarding: $("#consentOnboarding"), settings: $("#consentControl") },
   });
   const settings = createSettings({ permissionControl, blocks, consent });
   ```
4. token connect成功時(65-67行目の`session.save(tokens);`の後)とrouter起動後に誘導経路(a)を追加:
   ```js
       consent.ensureGuidance().catch(() => {}); // (a) 接続後の未同意誘導
   ```
   ```js
   router.start();
   // (a) トークン接続済みで起動した場合も未同意なら誘導
   if (session.hasTokens()) consent.ensureGuidance().catch(() => {});
   ```
   routerのscreens/onRouteへonboardingを追加:
   ```js
   const router = createRouter({
     screens: {
       home: $("#homeScreen"),
       latch: $("#detailScreen"),
       settings: $("#settingsScreen"),
       onboarding: $("#onboardingScreen"),
     },
     onRoute: (route) => {
       if (route.name === "home") {
         home.load().catch(() => {});
         home.loadIntents().catch(() => {});
       } else if (route.name === "settings") {
         settings.show();
       } else if (route.name === "onboarding") {
         consent.show().catch(() => {});
       } else {
         detail.show(route.id).catch(() => {});
       }
     },
   });
   ```

- [ ] **Step 9: 既存2試験ファイルを追従**

`frontend/tests/router.test.js`の`#/settings`試験の後に追加:

```js
  it("#/onboarding をonboardingへ解析する(厳密一致・M4 ws-5)", () => {
    expect(parseHash("#/onboarding")).toEqual({ name: "onboarding", id: null });
    expect(parseHash("#/onboarding/")).toEqual({ name: "home", id: null });
    expect(parseHash("#/onboarding/x")).toEqual({ name: "home", id: null });
  });
```

`frontend/tests/latch-settings.test.js`の3箇所:

1. `settings画面骨組`の`showはpermissionControl.renderとblocks.loadを呼ぶ`試験を変更:
   ```js
   it("showはpermissionControl.render・blocks.load・consent.loadを呼ぶ", () => {
     const permissionControl = { render: vi.fn() };
     const blocks = { load: vi.fn(async () => {}) };
     const consent = { load: vi.fn(async () => {}) };
     const settings = createSettings({ permissionControl, blocks, consent });
     settings.show();
     expect(permissionControl.render).toHaveBeenCalledTimes(1);
     expect(blocks.load).toHaveBeenCalledTimes(1);
     expect(consent.load).toHaveBeenCalledTimes(1);
   });
   ```
2. `index.html構造`試験のidリストへ`"onboardingScreen", "consentOnboarding", "consentControl", "consentSettingsTitle"`を追加。
3. `main.js配線`試験へ3行追加:
   ```js
   expect(mainJs).toContain('from "./latch/consent.js"');
   expect(mainJs).toContain('onboarding: $("#onboardingScreen")');
   expect(mainJs).toContain('CONSENT_REQUIRED');
   ```

- [ ] **Step 10: vitest全グリーン+build成功**

Run: `cd frontend && npm test`
Expected: PASS(既存全試験を含む — 既存はtoContain/追加系のため期待値不変)

Run: `cd frontend && npm run build`
Expected: 成功

- [ ] **Step 11: コミット**

```bash
git add frontend/
git commit -m "feat: オンボーディング同意画面+設定再確認ブロック+422誘導(#/onboarding)"
```

## Task 10: 全体検証+報告

**Files:**
- Create: `docs/plans/M4/ws-5-report.md`

- [ ] **Step 1: backend全unit+ruff**

Run: `cd backend && .venv/bin/python -m pytest tests/unit -q`
Expected: PASS(全グリーン)

Run: `cd backend && .venv/bin/ruff check src tests`
Expected: エラーなし

- [ ] **Step 2: integration collect-only(実行しない)**

Run: `cd backend && .venv/bin/python -m pytest tests/integration --collect-only -q | tail -5`
Expected: 収集成功・エラーなし

- [ ] **Step 3: frontend全試験+build**

Run: `cd frontend && npm test && npm run build`
Expected: PASS・成功

- [ ] **Step 4: basename衝突の最終確認**

Run: `find backend/tests -name 'test_*.py' -printf '%f\n' | sort | uniq -d && find frontend/tests -name '*.test.js' -printf '%f\n' | sort | uniq -d`
Expected: 出力なし

- [ ] **Step 5: 報告ファイルを書く**

`docs/plans/M4/ws-5-report.md`を新規作成し、以下の見出しで記す:

```markdown
# M4 ws-5 実装報告

- 実装: agent3(本計画docs/plans/M4/ws-5-plan.mdどおり)
- ブランチ/worktree: <ブランチ名>・最終コミット: <hash>
- unit試験: <件数> passed(pytest tests/unit)/ ruff: エラーなし
- frontend: vitest <件数> passed / npm run build: 成功
- integration: collect-only成功(<件数>試験)。**test-ci=スーパーバイザー検証待ち**
  (apiイメージ再構築後に実施。ヘルパー同意付与19ファイル・test_14・
  test_consent_api.pyが対象)

## スーパーバイザー検証時の手順

1. `docker compose build api worker`(同意ゲート・consent APIは新コードが必要 —
   常設コンテナが旧コードのままだとintegrationが全滅する)
2. `alembic upgrade head`(conftestのmigrated_dbが自動実行・head=0007を確認)
3. `make test-ci`(全グリーンを確認。特にtest_consent_api.py・test_matching_hardfilter
   test_14・test_geo headピン)
4. G4証拠3点の採取(docs/testassets/results/へ・10 §5):
   ①同意フローのstaging手順実行記録(未同意Intentの422→同意→active可の順序観察
   ・test-ci試験のstaging再現+ログ添付)
   ②test_log_allowlistの実行結果+登録表の写し
   (ALLOWED_STRUCTURED_LOGS=latch.llm.send→SendRecord・latch.push.send→
   PushSendRecord。許可リスト一覧=問い合わせ開示資料の雛形)
   ③`docker logs`からlatch.llm.send等の出力にraw_text相当が含まれないことの
   サンプル照合(ws-3のpushログ読取と同型)

## 既知の限界・残置事項(設計§6で承認済み)

- origin前段スキップ省略: DB直書きで起点だけ未同意の運用異常時、その起点の
  評価(Embedding送信を含む)は走り得る(Layer 1はpeer側のみ検査 — 計画§4-1)
- 未登録JWTのparseは現行どおり(user_id=Noneで送信記録。計画§4-2)
- 同意の取り消しは非実装(§6-2)
- docs改版候補(05 §2・05 §5・08 D-14・03 §2)は次回docs改版にまとめる(§3.4)

## 引き継ぎ(watcher向け)

- **ws-5マージ後〜ws-3実装完了まで、seed投入→マッチング検証のフロー
  (seed-verify・期待値表検証)は実行しないこと。** seedのユーザーは同意未記録で
  Layer 1除外される。ws-3がhelpers_seed.pyへ同意API呼び出しを追加するまで
  機能しない(直列化裁定・設計§6-3の既定の経過状態)
- ws-3のstaging conftestのhead前提ピンは0007へ読み替え(設計§6-3)
```

- [ ] **Step 6: コミット**

```bash
git add docs/plans/M4/ws-5-report.md
git commit -m "docs: M4 ws-5実装報告(unit完・test-ci=スーパーバイザー検証待ち・G4証拠手順)"
```

---

# 完了条件(検証可能な形・設計§5ドラフトの確定版)

- [ ] migration 0007が存在し、`revision="0007"`・`down_revision="0006"`・upgradeでusers.consent_at timestamptz追加・downgradeでDROP。**証明: スーパーバイザー検証時のtest-ciで`test_head_is_0007`(test_geo.py)と`test_users_consent_at_column_exists`(test_consent_api.py)がグリーン**
- [ ] 同意なしでparse・intents active作成(status省略含む)・draft→activeが422 CONSENT_REQUIRED・同意後は通る。draft保存は同意なしで通る。**証明: test_consent_api.pyの3経路試験がtest-ciでグリーン+unit(test_intents_service/test_service)グリーン**
- [ ] Layer 1で同意なしpeerが候補化しない。**証明: test_matching_hardfilter.py test_14がtest-ciでグリーン+`test_layer1_where_requires_peer_consent`(unit・agent3実行済み)グリーン**
- [ ] 許可リスト機械検査4項目(+未登録ロガー拒否)がunit常設でグリーン・既存2ログは無変更のまま登録済み。**証明: test_log_allowlist.pyがagent3実行済みでグリーン+`git diff main -- backend/src/latch/llm/records.py backend/src/latch/notifications/records.py backend/src/latch/notifications/templates.py`が空(無変更の証明)**
- [ ] オンボーディング画面・設定画面再確認ブロックが動作。**証明: vitest全グリーン+latch-consent.test.jsの全試験+npm run build成功(agent3実行済み)**
- [ ] test_rate_limit_wiring.py追従(Counter `"/v1/users/me/consent": 2`)。**証明: agent3実行済みunitグリーン**
- [ ] 既存全unit試験・既存全vitestが無変更の期待値で通る(追従変更はUserRow構築のフィールド追加とsettingsテストの引数追加のみ)。**証明: agent3実行済みの全unit/vitestグリーン**
- [ ] マージ後mainでtest-ci全グリーン・G4証拠3点がdocs/testassets/results/に保管。**証明: スーパーバイザー検証(ws-5-report.mdの手順どおり)**

# 報告形式

- 結果ファイル: `docs/plans/M4/ws-5-report.md`(Task 10 Step 5のテンプレートどおり)
- 最終返信: オーケストレーターへ「DONE」+報告ファイルのパス。失敗・中断した場合は「BLOCKED: <論点>」

---

# Self-Review記録(計画書作成エージェントによる見直し・M3 ws-5運用)

**1. 設計メモとの突合(§3.1・§3.3の全ファイルをTaskへ割り付けたか):**
作成9ファイル→Task 1(logallow)・2(0007)・3(consent.py+test_consent_service)・8(test_consent_api)・9(consent.js+latch-consent.test.js)・10(report)。変更全14ファイル→Task 2(test_geo)・3(errors.py+__init__)・4(routes.py・main.py・test_rate_limit_wiring・test_users_routes)・5(intents errors/__init__/store/service+unit追従)・6(service+main)・7(layer1+test_layer_sql+hardfilter+19ヘルパー)・9(texts/router/index.html/settings/main.js+2試験)。**欠けなし。**

**2. プレースホルダー検査:** 「TBD」「適切に」「類似Task N」なし。全ステップに実コードまたは全数リスト(§Task 7 Step 5のヘルパー20箇所表)あり。

**3. 型・名前の整合:**
- `ConsentService`コンストラクタは`record_consent/fetch_consent/consented/exists_fn`(Task 3 Step 1の`_Svc`・Step 5の実装・Task 4の`StubConsentService`(`exists_fn=None`)で一致)
- `serialize_allowed(logger_name, record_dict)` — Task 1の試験と実装で引数順一致
- `createConsent({ client, chrome, el: { onboarding, settings } })` — Task 9の試験・実装・main.js配線で一致。テストの`mountConsent`は`load/show/ensureGuidance`を待つためasync・`notifyRequired`はsync(`mountConsentSync`)で分けた
- `UserRow(id, birth_date, consent_at)` — Task 5の全構築箇所(既定1+直接4+他ファイル2)に`consent_at`追加を列挙済み。users側`UserRow`(users/service.py)は**別クラス**で変更しない(users試験は無影響)
- Task 3のunit試験`test_get_returns_none_when_never_consented`は`exists`既定Trueで404にならずnullを返す ✓ / `test_get_raises_404_when_row_missing`は`exists=False`上書きを指示済み ✓

**4. 既存試験への影響の機械突合用サマリ(スーパーバイザー検証用):**

| # | 項目 | 根拠・内容 |
|---|---|---|
| 3-1 | integration 20ファイル21ヘルパーに同意2行 | Task 7 Step 5の全数表(rgで「POST /v1/users後にactive Intentを作る経路」を全数調査済み。test_users_apiは登録契約試験・test_intents_parse_apiは未登録経路・test_schemaはユーザーなしで対象外。supervisor実査でカウント訂正済み) |
| 3-2 | 既存SQLピンが無変更で通る根拠 | 条件はHEAD**末尾**追加のため`test_layer4.py:205`の`LAYER1_WHERE_BASE.strip()[:40]`(先頭=i.status)不変・`test_layer_sql.py`の全ピン(37-53・58・63・154-161・165-180行)はsubstring/構造比較で通る・`test_layer4.py:94,105`と`test_group_engine.py:306`は`LAYER1_WHERE*.strip() in sql`で通る・bind param列挙(`_WHERE_KEYS`)不変(新bindなし) |
| 3-3 | GET /v1/users/me無傷の根拠 | 同意APIは独立エンドポイント(設計§2.3方式A)。`UserMeResponse`・`appState.ensureMe`・`app-state.test.js`のキー集合比較に触れない。`frontend/tests/app-state.test.js`は無変更で通る |
| 3-4 | test_rate_limit_wiring | Counterへ1行(`"/v1/users/me/consent": 2`)。users_routerのrouter-level依存により2ルートが自動保護され構成強制を通る |
| 3-5 | frontend既存試験 | router.test.js: 未知hash→ホーム規約不変(`#/onboarding`は厳密一致の新規分岐)。latch-settings.test.js: index.html構造試験はtoContain(追加可)・`show`試験のみ引数追加(Step 9で差し替え)・main.js配線試験はtoContain(追加可)。latch-consent.test.jsは新規 |
| 3-6 | unit既存の必須追従 | UserRow構築7箇所(test_intents_service 5・test_event_publish 1・test_time_validation 1)に`consent_at=NOW`。IntentParseServiceは`consent_lookup=None`既定のためg1gate・test_service既存は無変更 |
| 3-7 | alembic head=0006ピンの全数 | `rg -n "0006" backend/tests -g '*.py'`で1件のみ(test_geo.py:33-39)。Task 2で0007へ追従 |
| 3-8 | LAYER1_WHEREバイト同一ピンの全数 | バイト同一ピンは存在しない(構造比較のみ・`rg -n "LAYER1_WHERE" backend/tests`で全11箇所を確認・全てsubstring/合成一致)。Task 7 Step 1の新ピンが同意条件を钉付け |

**5. Review Focusの試験化:** §5の5項目それぞれにTask番号で試験を割り当て済み(§5の矢印参照)。
