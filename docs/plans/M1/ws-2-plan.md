# M1 ws-2 実装計画 — Intent Parser + POST /v1/intents/parse

> **実装エージェント(agent3)へ**: 本計画は superpowers:executing-plans の規律で遂行する(タスク順に・各ステップの checkbox を `- [ ]` → `- [x]` に更新しながら)。設計判断の根拠は `docs/plans/M1/ws-2-design.md`(以降 design)を参照せよ。本計画とdesignが矛盾する場合はdesignを正とし、報告ファイルに記録せよ。

**Goal**: M1スコープ2〜3(12 M1-2・M1-3) — 07 §2の確定済みシステムプロンプト・D-19アプリ層補完の単一規則・D-04連携(ng_unverifiable→warnings)を実装し、同期LLM・timeout 10秒・再試行なしの POST /v1/intents/parse(D-17: 503 LLM_UNAVAILABLE / 422 VALIDATION_ERROR 切替)を提供する。

**Architecture**: Parser本体(プロンプト定数・出力スキーマ検証・補完規則・warnings生成)は `backend/src/latch/intents/` 新パッケージへ、LLM非依存・DB非依存の純粋モジュールとして切り出す。Gateway呼び出しは構造的Protocol(`SupportsParseIntent`)で受ける(llm/へのimportは構築ファクトリのみ)。llm/・auth/等の既存資産は無差分。main.py(lifespanの独立スキップ判定化・ハンドラ1個・router include・テスト注入引数)だけが既存コードへの変更。

**Tech Stack**: Python 3.13 / FastAPI / pydantic v2 / pytest(asyncio_mode=auto)/ SQLAlchemy async(make_user_lookupのengineのみ・新ORMなし)。依存追加なし。

**Spec**: `docs/plans/M1/ws-2-design.md`(入力設計メモ・確定値1〜18は本計画に反映済み)

---

## 0. 前提と環境(必読)

1. **作業場所**: 実装は git worktree 内で行う。本計画のパスはすべて**リポジトリルートからの相対パス**で書いてある。
2. **git commit はしない**。コミットはスーパーバイザーが行う。タスク完了時はテスト実行結果だけを確認し、コミットステップは存在しない。
3. **共有ci-db運用**(`docs/plans/STATUS.md` 運用ルール): 開発は unit 試験(`make lint` / `make test`)で進める。**`make test-ci` はスーパーバイザーの直列検証に回してよい**(本単位はマイグレーション追加なしだが、運用に従う)。報告ファイルには「test-ci=スーパーバイザー検証待ち」と記録する。
4. **test-ci と api イメージ**: `make test-ci` は `docker compose up -d --wait` のみで **api イメージを再ビルドしない**(compose.yamlでapiはbuild型・ソースマウントなし)。コード変更後の integration 試験には先に `docker compose build api` が必要。この手順は報告ファイルの検証手順に必ず書く。
5. **並走**: ws-1(users API)が同じリポジトリで並走している。`backend/src/latch/users/` と `docs/plans/M1/ws-1-*` には触れない。main.py は両単位の交点であり、マージ時の競合解消(両側追記保持)はスーパーバイザーが行う。本計画の main.py 変更は ws-1 の存在を前提としない(ws-1非依存のコードを書く)。
6. **開始時の健全確認**: 作業開始時に `make lint && make test` を実行する。**既知の既存失敗**(`tests/unit/auth/test_tokens.py::test_token_payload_structure` — PyJWTのexp自動検証と固定リテラル時刻の組み合わせが実時間経過で赤くなる。本計画作成時点 2026-09-27 に本家mainで確認済み・ws-2と無関係)はこの1件に限り、着手をブロックしない。**それ以外の失敗があった場合は着手せず**、報告ファイルに状況を記録して作業を中止する。完了時は「新規試験が全緑・既存試験の失敗が上記1件のみ(ws-2の差分を git stash しても再現する=ws-2起因でないことを確認)」を報告する。
7. **本計画のコードは実機検証済み**: 計画作成時に、本計画に記載のコード(intents/ 7ファイル・unit/intents 6ファイル・integration 1ファイル・main.py 変更・__init__ 統合形)を一時コピーで組み立て `pytest`(新規60件全緑・既存268件緑+既知1件)・`ruff format --check`・`ruff check`・プロンプトのdocs一致をすべて確認してある。手で書き換える必要はなく、コードは記載のとおり使えば動く。

## 1. 参照仕様(節番号)

| # | 主题 | 出典 |
|---|---|---|
| 1 | システムプロンプト全文(規則1〜7・出力JSONスキーマ・`{current_date}`) | 07 §2 |
| 2 | 入力300字上限(切り詰めなし・超過422)・補完はアプリ層の単一規則・必須3フィールドとLLM障害の応答区別(FR-43) | 07 §2解釈規則 |
| 3 | D-04連携: ng_unverifiable非空→warnings `{code: NG_CONDITION_DOWNGRADED, condition, message}` | 07 §2 D-04連携・03 §3・05 §5応答例 |
| 4 | 補完表(time.end+3h / participants(2,2) / radius_m 1000 / budget null維持 / expires_at 4選択肢からtime.start+3h最近傍) | 07 §2解釈規則・03 §3 D-19 |
| 5 | 有効期限4選択肢(今夜23:30・明日12:00・明日23:30=各JST・3日後まで=now+72h)。過ぎた選択肢は不可・同点は設計判断(§7 Review Focus) | 03 §3(FR-13) |
| 6 | negative_constraints は常に空配列(FR-42) | 07 §2規則5・解釈規則 |
| 7 | POST /v1/intents/parse 契約(200・`{structured_intent, warnings}`・保存しない・補完を応答に適用しない・座標を返さない) | 05 §5 |
| 8 | エラー形式 `{"error": {code, message, details}}` と code列挙(400 MALFORMED_REQUEST / 401 UNAUTHENTICATED / 422 VALIDATION_ERROR / 503 LLM_UNAVAILABLE / 503 DEPENDENCY_UNAVAILABLE) | 05 §5エラー形式表 |
| 9 | 全API認証済みのみ。ドメインルータは `require_authenticated` をルータ単位に付す(C3) | 05 §5冒頭 / auth/deps.py |
| 10 | 時刻参照はすべてClock経由・JST暦日付は `clock.jst_date()`(arch test `test_arch_no_direct_time` が自動強制) | 04 §5(FR-41)・10 §1 / core/clock.py |
| 11 | ログ・例外にIntent本文を混ぜない。送信記録は内容を含まない | 08 §3・§2.4 |
| 12 | 同期・timeout 10秒・再試行なし。タイムアウト時は再試行せずフォールバック | 07 §1・§5 D-17 |
| 13 | Gateway資産(無差分で利用): `LLMGateway.parse_intent(*, text, current_date, user_id=None) -> dict`・`build_llm_gateway(clock, settings)`・`StubLLM(parser_response, fail_parser, delay_parser_ms)`・`DEFAULT_PARSER_RESPONSE`・`LLMTimeoutError`/`LLMProviderError` | backend/src/latch/llm/(gateway.py・stub.py・errors.py) |
| 14 | user_lookup資産: `make_user_lookup(engine) -> Callable[[str, str], Awaitable[UUID | None]]`(M0 ws-3 マージ済み) | backend/src/latch/auth/service.py |
| 15 | 時刻検証(過去不可・+7日)は保存経路(ws-3)であり parse では適用しない | 05 §5 POST /v1/intents |

## 2. スコープ(作成・変更するファイル)

### 2.1 作成

```text
backend/src/latch/intents/
├── __init__.py        # 公開IFの再export
├── prompt.py          # PARSER_SYSTEM_PROMPT(07 §2全文) + format_parser_system_prompt
├── schema.py          # ParserOutput と部分モデル + WARNING_MESSAGE_NG_DOWNGRADED
├── completion.py      # D-19補完の単一規則(純粋関数のみ)
│                      #   ※visibility/notification_levelの既定値は保存経路の値であり
│                      #   ws-3が格納時に扱う(design §3.1のインターフェース要旨に含まれない)
├── errors.py          # IntentsError + LLMUnavailableError / UnstructurableError / DependencyUnavailableError
├── service.py         # SupportsParseIntent(Protocol)・ParseWarning・ParseResult・IntentParseService・make_intent_parse_service
└── routes.py          # parse_router・ParseRequest/ParseResponse・get_intent_parse_service
backend/tests/unit/intents/
├── __init__.py を置かない(既存 tests/unit/auth・llm と同じく __init__.py なしのディレクトリ)
├── test_errors.py
├── test_prompt.py
├── test_completion.py
├── test_service.py
├── test_parser_output.py   # ※既存 auth/test_routes.py・integration/test_schema.py との
└── test_parse_routes.py    #   同名衝突を避ける命名(tests/は__init__.pyなし運用のため)
backend/tests/integration/test_intents_parse_api.py
docs/plans/M1/ws-2-report.md   # 報告ファイル(§5参照)
```

### 2.2 変更(既存ファイルは main.py のみ)

- `backend/src/latch/main.py` — (1) parse_router の include、(2) lifespan をサービスごとの独立スキップ判定へ変更(engineはauthと共有取得)、(3) `IntentsError` ハンドラ1個、(4) `create_app` に `intent_parse_service` テスト注入引数。

## 3. 禁止(触れてはいけないもの・スコープ外判断基準)

### 3.1 触れてはいけないファイル

- `backend/src/latch/llm/`(Gateway・StubLLM・送信記録 — **無差分**)
- `backend/src/latch/auth/`・`backend/src/latch/core/`・`backend/src/latch/geo/`・`backend/src/latch/worker/`(参照のみ)
- `backend/src/latch/users/`・`docs/plans/M1/ws-1-*`(ws-1の並走成果物。存在しても触れない)
- `backend/alembic/`・`backend/src/latch/settings.py`・`backend/pyproject.toml`・`backend/uv.lock`(マイグレーション追加なし・新設定なし・依存追加なし)
- `compose.yaml`・`Makefile`・`backend/Dockerfile`・`.mise.toml`
- `docs/01〜12`・`docs/learn/`・`docs/reviews/`・`docs/plans/STATUS.md`・`docs/plans/M0/`(STATUSはスーパーバイザー管理)
- `prototype/`・`README.md`・`backend/README.md`・`.claude/`・`.agents/`・`.hermes/`
- 既存テストファイル(tests/unit/auth・llm・geo・test_app_health.py 等)への変更。**例外**: 本計画が新規作成するファイルのみ作成する

### 3.2 スコープ外と判断する基準(実装中に迷ったら作らない)

- parse応答への補完適用(time.end・radius_m等の埋め戻し)— 05 §5応答例がnullのまま返すことを確定。補完の**消費**はws-3(保存)とws-5(UI)
- POST /v1/intents(CRUD)・ジオコーディング・alcohol_involvedのサーバ側確定・時刻検証(過去不可・+7日)— ws-3
- レート制限(429 RATE_LIMITED)— ws-4
- 実プロバイダ(llm_mode="real")・httpx・API鍵 — T1確定後の別単位
- 精度ゲートharness・入力セット・期待値表 — T3・09 §4.3
- p95/p99計測 — M4
- parse結果キャッシュ・ORM・リポジトリ層・新設定項目・依存追加 — docsに規定なし(YAGNI)
- 構造化フォームAPI — クライアント側機能(03 §3)

判断に迷う変更が出たら、その時点で作らずに報告ファイルの「計画からの逸脱・判断」へ記録する。

## 4. 完了条件(テストで証明できる形)

1. `make lint` がグリーン(ruff format検査+lint)。
2. `make test` がグリーン(§0.6 の既知の既存失敗1件を除く全件。新規unit試験60件を含む。新規試験数は報告ファイルに記録)。
3. D-17応答切替(503 LLM_UNAVAILABLE / 422 VALIDATION_ERROR)・D-04 warnings生成・規則5正規化(negative_constraints→ng_unverifiable)・user_id帰属(User行あり→str(id)、なし→None、lookup失敗→503)・current_dateのJST暦日付(UTC 15:00境界)が、すべてunit試験の関数名として存在し通過している(test_service.py・test_parse_routes.py)。
4. プロンプト全文ピン留め試験(test_prompt.py)が、docs/07-jev-llm-spec.md の最初の ```text ブロックと `PARSER_SYSTEM_PROMPT` の一致を検証していて通過している。
5. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが `core/clock.py` のみ(arch test `test_arch_no_direct_time.py` も自動強制)。
6. `git status` の差分が §2 の一覧どおり(llm/・auth/・core/・geo/・worker/・alembic/・settings.py・pyproject.toml・uv.lock・compose.yaml・Makefile に差分なし)。
7. `backend/tests/integration/test_intents_parse_api.py` が design §4.2-1〜5(ハッピーパス・301字/300字・text欠落/JSON破損・401系・未登録subject)をカバーして存在する。**実行(test-ci)はスーパーバイザー検証待ちでよい**。報告ファイルの検証手順に「`docker compose build api` → `make test-ci`」を明記する。

## 5. 報告形式

成果物: `docs/plans/M1/ws-2-report.md`。Task 8 で作成する。以下の節を含める。

```markdown
# M1 ws-2 実装報告(Intent Parser + POST /v1/intents/parse)

- 作業単位: ws-2(design: docs/plans/M1/ws-2-design.md / plan: docs/plans/M1/ws-2-plan.md)
- 実装日: <日付>

## 実装概要
- 作成・変更ファイルの一覧(§2 との対応)

## 検証結果
- make lint: <結果>
- make test: <結果・件数>(新規試験の内訳)
- rg 時刻参照検査: <ヒットが core/clock.py のみであること>
- git status の差分一覧(§3.1 の禁止領域に差分がないこと)

## test-ci(スーパーバイザー検証待ち)
- test-ci=スーパーバイザー検証待ち(STATUS運用ルール)
- 検証手順: cd <リポジトリルート> && docker compose build api && make test-ci
  (make test-ci はapiイメージを再ビルドしないため、コード変更後は build api が必須)

## 完了条件の達成状況
- §4 の7項目それぞれに証拠(コマンドと出力抜粋)

## 計画からの逸脱・判断
- なければ「なし」と書く
```

## 6. グローバル制約(全タスク共通)

- Python 3.13 / ruff target py313。`from __future__ import annotations` を各モジュール冒頭に付ける(既存コードと同じ)。
- docstring・コメントは日本語で、既存コード(llm/gateway.py 等)と同じ密度でdocs出典(節番号)を書く。
- テストの時刻は**固定リテラル**(2026-09-27 基準・FakeClock)。実時間参照は tests も含め使わない(arch testはsrcのみ対象だが、決定性のため)。
- テキスト系のログ出力・例外メッセージにユーザー入力text・condition本文を混ぜない(08 §2.4・§3)。ログはイベント名とcodeのみ。
- pydantic v2 の記法(`model_config = ConfigDict(...)`, `Field(...)`, `field_validator`)を使う。
- pytest は `asyncio_mode=auto`(async def テストにデコレータ不要)。
- 依存追加・設定追加をしない。
- `intents/` 配下から `latch.llm` のimportは `service.py` の `build_llm_gateway` のみ(design §2.7)。`latch.auth` はimportしない(claimsはプリミティブで受ける)。

## 7. Review Focus(specが含意するが、個別タスクの試験だけでは拾いにくい失敗モード)

1. **プロンプトとdocsの無音の乖離**(07 §2改版時にコード側が更新されない)→ Task 2 が docs の ```text ブロック抽出との一致でピン留め(変われば試験が落ちる=仕様変更イベント)。
2. **current_dateがUTC暦日付になる**(JST 0時跨ぎ。確定値13: プロンプトへ渡すのはJST暦日付)→ Task 5 の UTC 14:59/15:00 境界試験が `clock.jst_date()` 経由を強制。
3. **LLMが規則5違反で negative_constraints 非空を返し、注意表示なしで下流へ流れる**(FR-42・D-04「黙って降格させない」)→ Task 5 の正規化試験。
4. **ログ・例外・送信記録へのユーザー本文混入**(08 §2.4・§3。Parser系統の送信内容=生テキスト)→ Task 6 の caplog 試験(応答textがログに現れない)+ 例外メッセージは固定文言のみ。
5. **parse応答へ補完を適用してしまう**(time.end・radius_mの埋め戻し。確定値10: 応答はnullのまま)→ Task 6・Task 7 の200応答試験が `end is None`・`radius_m is None` を表明。

---

## 8. 実装ステップ

### Task 1: intentsパッケージ雛形と例外階層

**Files:**
- Create: `backend/src/latch/intents/__init__.py`
- Create: `backend/src/latch/intents/errors.py`
- Test: `backend/tests/unit/intents/test_errors.py`

**Interfaces:**
- Produces: `IntentsError`(基底・`http_status`/`code`属性)・`LLMUnavailableError`(503 LLM_UNAVAILABLE)・`UnstructurableError`(422 VALIDATION_ERROR)・`DependencyUnavailableError`(503 DEPENDENCY_UNAVAILABLE)。Task 6 のハンドラと Task 5 のサービスがこの型に依存する。

- [x] **Step 1: 失敗するテストを書く**

`backend/tests/unit/intents/test_errors.py` を作成:

```python
"""intents例外階層(design §2.7)。http_status/codeは05 §5エラー形式表の固定値。"""

from latch.intents import (
    DependencyUnavailableError,
    IntentsError,
    LLMUnavailableError,
    UnstructurableError,
)


def test_llm_unavailable_is_503_llm_unavailable():
    exc = LLMUnavailableError("intent parser unavailable")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == 503
    assert exc.code == "LLM_UNAVAILABLE"


def test_unstructurable_is_422_validation_error():
    exc = UnstructurableError("structured intent is not extractable")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == 422
    assert exc.code == "VALIDATION_ERROR"


def test_dependency_unavailable_is_503():
    exc = DependencyUnavailableError("intent parse dependency unavailable")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == 503
    assert exc.code == "DEPENDENCY_UNAVAILABLE"
```

- [x] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_errors.py -v`
Expected: FAIL(ModuleNotFoundError: latch.intents がない)

- [x] **Step 3: 最小実装**

`backend/src/latch/intents/errors.py` を作成:

```python
"""intentsドメイン例外階層(design §2.7・§3.1)。

各例外は http_status と code(05 §5エラー形式表)を固定で持つ。main.py の
ハンドラが共通envelopeへ変換する。例外メッセージにIntent本文(text・condition)
を混ぜない(08 §2.4)— 呼び出し側は固定文言のみを渡す。
"""

from __future__ import annotations


class IntentsError(Exception):
    """intentsドメインエラーの基底。http_status/code を持つ(ハンドラが消費する)。"""

    http_status: int
    code: str


class LLMUnavailableError(IntentsError):
    """Parser系LLM障害(timeout・API障害・レート制限。07 §2・05 §5)。

    クライアントは入力テキストを保持した再試行ボタンへ分岐する(D-17)。
    """

    http_status = 503
    code = "LLM_UNAVAILABLE"


class UnstructurableError(IntentsError):
    """構造化不能(必須3フィールド抽出不能・スキーマ不適合。07 §2・design §2.4)。

    クライアントは構造化フォームフォールバックへ分岐する(D-17・FR-43)。
    """

    http_status = 422
    code = "VALIDATION_ERROR"


class DependencyUnavailableError(IntentsError):
    """DB・Redis等の依存障害(05 §5「全API」。user_lookup失敗・design §2.3)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"
```

`backend/src/latch/intents/__init__.py` を作成(以降のタスクで追記する):

```python
"""Intentドメイン(M1 ws-2)。Parser本体・補完規則・parse API。"""

from latch.intents.errors import (
    DependencyUnavailableError,
    IntentsError,
    LLMUnavailableError,
    UnstructurableError,
)

__all__ = [
    "DependencyUnavailableError",
    "IntentsError",
    "LLMUnavailableError",
    "UnstructurableError",
]
```

- [x] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_errors.py -v`
Expected: PASS 3件

- [x] **Step 5: 書式確認**

Run: `cd backend && uv run ruff format src/latch/intents tests/unit/intents && uv run ruff check src/latch/intents tests/unit/intents`
Expected: エラーなし

### Task 2: プロンプト定数とフォーマッタ(prompt.py)

**Files:**
- Create: `backend/src/latch/intents/prompt.py`
- Modify: `backend/src/latch/intents/__init__.py`(export追加)
- Test: `backend/tests/unit/intents/test_prompt.py`

**Interfaces:**
- Produces: `PARSER_SYSTEM_PROMPT: str`(07 §2全文・`{current_date}` プレースホルダ1箇所)・`format_parser_system_prompt(current_date: date) -> str`。将来の実プロバイダ(T1後)と精度ゲートharness(T3)が消費する。本単位のランタイム消費者はいない(design §2.6トレードオフどおり)。

- [x] **Step 1: 失敗するテストを書く**

`backend/tests/unit/intents/test_prompt.py` を作成:

```python
"""プロンプト定数のピン留め(design §2.6・§4.1-1)。

docs/07-jev-llm-spec.md の最初の```textブロックと完全一致させる — プロンプト
変更は仕様変更(07 §2改版)であり、本試験の失敗として検出する。ゲート(G1)
再実行の起点(09 §4.3)。
"""

import re
from datetime import date
from pathlib import Path

from latch.intents import PARSER_SYSTEM_PROMPT, format_parser_system_prompt

DOCS_07 = Path(__file__).resolve().parents[4] / "docs" / "07-jev-llm-spec.md"


def _first_text_block(doc: str) -> str:
    """07 §2のプロンプト本文(ファイル中最初の```textブロック)を取り出す。"""
    match = re.search(r"```text\n(.*?)\n```", doc, flags=re.DOTALL)
    assert match is not None
    return match.group(1)


def test_prompt_is_pinned_to_docs_07_section2():
    doc = DOCS_07.read_text(encoding="utf-8")
    assert PARSER_SYSTEM_PROMPT == _first_text_block(doc)


def test_prompt_contains_current_date_placeholder_exactly_once():
    assert PARSER_SYSTEM_PROMPT.count("{current_date}") == 1


def test_formatter_substitutes_iso_date():
    formatted = format_parser_system_prompt(date(2026, 9, 27))
    assert "2026-09-27" in formatted
    assert "{current_date}" not in formatted
    # ほかの{...}(出力JSONスキーマ)が壊れていない
    assert '"category"' in formatted
    assert '"ng_unverifiable"' in formatted
```

補足: `parents[4]` は `tests/unit/intents/test_prompt.py` から `backend/` を挟んでリポジトリルートへ至る経路(`parents[0]=intents, [1]=unit, [2]=tests, [3]=backend, [4]=リポジトリルート`)。worktree内でもリポジトリルートに `docs/` があるため成立する。

- [x] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_prompt.py -v`
Expected: FAIL(ImportError: PARSER_SYSTEM_PROMPT がない)

- [x] **Step 3: 最小実装**

`backend/src/latch/intents/prompt.py` を作成。**下記をそのまま使う**(join形式の由来は §「実装メモ」参照。07 §2との値の一致はピン留め試験が強制する — 手で書き換えないこと):

```python
"""07 §2の確定済みシステムプロンプト(design §2.6)。

単一のバージョン管理された定数 — 変更は即コード差分として現れ、
docsピン留め試験(test_prompt.py)が落ちる。プロンプト変更=仕様変更であり、
設計書(07 §2)の更新とG1両ゲートの再実行(09 §4.3)を要する。

表記は行リスト+join: 07 §2の全文と1文字・改行位置まで一致させたまま
ruffのE501(行幅は東アジア文字幅で計算)を満たすため、docs由来の長い行は
隣接リテラル連結で分割してある(値はdocsと同一 — ピン留め試験が強制)。
実プロバイダ(T1確定後)は構成時(main/lifespan)にこの定数を注入されて使う。
フォーマッタは{current_date}のみを差し替える(str.formatは使わない —
出力JSONスキーマの{...}と衝突するため)。
"""

from __future__ import annotations

from datetime import date

PARSER_SYSTEM_PROMPT = "\n".join(
    [
        "あなたはLATCHのIntent Parserである。ユーザーが書いた「条件付きの意思」を",
        "構造化データへ変換する。出力は指定のJSONのみとし、説明文を含めない。",
        "",
        "入力は日本語の自然文で(最大300字。アプリ層で事前検証す"
        "る)、「今〜数日以内の食事・飲み・軽いアクティビティ」に",
        "関する意思である。現在日付は {current_date} とする。",
        "",
        "規則:",
        "1. 抽出できるフィールドのみ埋める。推測で値を作らない。",
        "2. 「〜くらい」「〜程度」の曖昧表現は次の丸め規則で確定値に変換する。",
        "   - 予算「5000円くらい」→ budget.max = 5000(上限として扱う。minは設定しない)",
        "   - 人数「2〜4人くらい」→ min=2, max=4。「3人以上なら」→ min=3, max=4",
        "     (参加人数の上限は4)。人数に言及がない場合は両方nullを返す",
        "   - 時間「20時以降」→ start=20:00, end=null(未指定)。終了時刻は推測しない",
        "3. 相対表現(今日・明日・土曜・今夜)は現在日付から解決する。",
        "4. 地名・ランドマークは location.name にそのまま残す。座標は補完しない",
        "   (後段のジオコーディングで補完する)。",
        "5. 「会社関係の人は避けたい」のように、システムが判定データを持たない除外",
        "   条件は ng_unverifiable 配列に入れる。negative_constraints には入れない",
        "   (negative_constraints はMVPでは常に空配列である。理由は次節)。",
        "6. ユーザーの感情や意向の補足(「軽く」「ゆっくり」等)は soft_constraints へ",
        "   抜き出す。",
        "7. 飲酒の関与は alcohol_involved(boolean)で判定する(08 D-10)。",
        "   - category.primary が drinking なら常に true。",
        "   - 他のカテゴリでも「食事のついでに軽く飲む」のような meal 内の言及を含め、",
        "     飲酒を示す語(飲む・飲み・酒・呑む・"
        "バー・ビール・サワー等)があれば true。",
        "   - アルコールを指さない用法(「コーヒーを飲む」等)は false。",
        "",
        "出力JSONスキーマ(この形式のみ認める):",
        "{",
        '  "category": {"primary": "meal|drinkin'
        'g|activity", "secondary": string|null},',
        '  "alcohol_involved": boolean,',
        '  "time": {"start": "ISO8601(JST)", "end": "'
        'ISO8601|null", "flexibility_minutes": null},',
        '  "location": {"name": string, "radius_'
        'm": integer|null, "flexibility": null},',
        '  "budget": {"max": integer|null, "currency": "JPY"},',
        '  "participants": {"min": integer|null, "max": integer|null},',
        '  "soft_constraints": [string],',
        '  "negative_constraints": [string],',
        '  "ng_unverifiable": [string]',
        "}",
    ]
)


def format_parser_system_prompt(current_date: date) -> str:
    """{current_date} をISO日付(YYYY-MM-DD)へ差し替える(07 §2・design §2.6)。"""
    return PARSER_SYSTEM_PROMPT.replace("{current_date}", current_date.isoformat())
```

`backend/src/latch/intents/__init__.py` の import 節と `__all__` へ `PARSER_SYSTEM_PROMPT`・`format_parser_system_prompt`(`from latch.intents.prompt import ...`)を追記する。

**実装メモ(join形式の由来)**: 07 §2のプロンプトには東アジア幅で88字を超える行があり(例: 入力説明行・出力JSONスキーマの category/location 行)、ruffのE501は**東アジア文字幅ベース**で行長を測るため、三重引用符ブロックではlintが通らない。プロンプトの文字列値はdocsと1文字・改行位置まで一致が必須のため行の折返しはできず、pyproject.toml(per-file-ignores)は本単位の禁止領域 — よって**行リスト+`"\n".join`** とし、docs由来の長い行だけ隣接リテラル連結で物理行を分割する(連結後の値は元の行と同一。ピン留め試験が強制する)。この形は計画作成時に ruff format / ruff check / docs一致のすべてを実機検証済み。

- [x] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_prompt.py -v`
Expected: PASS 3件。**ピン留め試験(test_prompt_is_pinned_to_docs_07_section2)がFAILする場合は定数の値がdocsと1文字でも違う** — 手で書き換えず、Step 3 のコードをそのまま再適用すること(値の一致は試験が強制する)。

- [x] **Step 5: 書式確認**

Run: `cd backend && uv run ruff format src/latch/intents tests/unit/intents && uv run ruff check src/latch/intents tests/unit/intents`
Expected: エラーなし(prompt.py は Step 3 のコードが ruff format 安定形・全行88幅以内であることを計画作成時に実機検証済み)

### Task 3: ParserOutputスキーマ(schema.py)

**Files:**
- Create: `backend/src/latch/intents/schema.py`
- Modify: `backend/src/latch/intents/__init__.py`(export追加)
- Test: `backend/tests/unit/intents/test_parser_output.py`

**命名注記**: テストファイル名は `test_parser_output.py` とする(既存 `backend/tests/integration/test_schema.py` と同名衝突してpytest収集が落ちるため。tests/ は `__init__.py` なし運用)。

**Interfaces:**
- Produces: `ParserOutput`(pydantic・07 §2出力JSONスキーマ)と部分モデル `ParserCategory` / `ParserTime` / `ParserLocation` / `ParserBudget` / `ParserParticipants`、定数 `WARNING_MESSAGE_NG_DOWNGRADED`。Task 5 の検証(`ParserOutput.model_validate`)と Task 6 の応答モデルが依存する。

**検証方針(design §3.1の注釈どおり)**: 必須3フィールド(category / time.start / location.name)は必須・既定なし。alcohol_involved も必須(常にLLMが出力・補完なし)。budget・participants・3配列は省略時の既定で受理(規則1「抽出できるフィールドのみ」への寛容な解釈)。余分なキーは無視(07 §4の失敗分類=欠損・値域外・パース不能のみを失敗とする)。time.start は tz-aware 必須(オフセットはJSTに限定しない)。flexibility_minutes / location.flexibility は null のみ受容(03 D-19の固定扱い)。

- [x] **Step 1: 失敗するテストを書く**

`backend/tests/unit/intents/test_parser_output.py` を作成:

```python
"""ParserOutput受入・拒否(design §4.1-2)。拒否はすべてValidationError→422。"""

import copy
from datetime import datetime

import pytest
from pydantic import ValidationError

from latch.intents import WARNING_MESSAGE_NG_DOWNGRADED, ParserOutput
from latch.llm.stub import DEFAULT_PARSER_RESPONSE


def _response() -> dict:
    return copy.deepcopy(DEFAULT_PARSER_RESPONSE)


def test_accepts_stub_default_response():
    out = ParserOutput.model_validate(_response())
    assert out.category.primary == "meal"
    assert out.alcohol_involved is False
    assert out.time.start == datetime.fromisoformat("2026-09-27T19:00:00+09:00")
    assert out.time.end is None
    assert out.location.name == "東京駅"
    assert out.location.radius_m is None
    assert out.budget.max is None
    assert out.budget.currency == "JPY"
    assert out.participants.min is None
    assert out.soft_constraints == []
    assert out.negative_constraints == []
    assert out.ng_unverifiable == []


def test_accepts_full_response_with_all_fields():
    raw = _response()
    raw["category"]["secondary"] = "焼肉"
    raw["time"]["end"] = "2026-09-27T22:00:00+09:00"
    raw["location"]["radius_m"] = 2000
    raw["budget"]["max"] = 5000
    raw["participants"] = {"min": 2, "max": 4}
    raw["soft_constraints"] = ["軽く飲みたい"]
    raw["ng_unverifiable"] = ["会社関係の人は避けたい"]
    out = ParserOutput.model_validate(raw)
    assert out.category.secondary == "焼肉"
    assert out.time.end == datetime.fromisoformat("2026-09-27T22:00:00+09:00")
    assert out.location.radius_m == 2000
    assert out.budget.max == 5000
    assert out.participants.min == 2
    assert out.participants.max == 4
    assert out.soft_constraints == ["軽く飲みたい"]


def test_accepts_minimal_response_with_optional_groups_omitted():
    """budget/participants/3配列の省略は既定で受理(規則1への寛容な解釈)。"""
    raw = _response()
    del raw["budget"]
    del raw["participants"]
    del raw["soft_constraints"]
    del raw["negative_constraints"]
    del raw["ng_unverifiable"]
    out = ParserOutput.model_validate(raw)
    assert out.budget.max is None
    assert out.budget.currency == "JPY"
    assert out.participants.min is None
    assert out.soft_constraints == []
    assert out.negative_constraints == []
    assert out.ng_unverifiable == []


def test_accepts_non_jst_offset():
    """tz-awareなら受理(JST限定しない。design §6-5)。"""
    raw = _response()
    raw["time"]["start"] = "2026-09-27T10:00:00+00:00"
    out = ParserOutput.model_validate(raw)
    assert out.time.start.utcoffset().total_seconds() == 0


def test_ignores_extra_keys():
    raw = _response()
    raw["unknown_field"] = {"nested": 1}
    out = ParserOutput.model_validate(raw)
    assert out.category.primary == "meal"


EXPECTED_WARNING_MESSAGE = "この条件は確実には除外できません。参考条件として扱います"


def test_warning_message_constant():
    assert WARNING_MESSAGE_NG_DOWNGRADED == EXPECTED_WARNING_MESSAGE


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.pop("category"), id="category-missing"),
        pytest.param(
            lambda r: r["category"].update({"primary": "shopping"}),
            id="primary-invalid",
        ),
        pytest.param(lambda r: r.pop("time"), id="time-missing"),
        pytest.param(
            lambda r: r["time"].update({"start": "2026-09-27T19:00:00"}),
            id="start-naive",
        ),
        pytest.param(
            lambda r: r["time"].update({"end": "2026-09-27T22:00:00"}),
            id="end-naive",
        ),
        pytest.param(lambda r: r.pop("location"), id="location-missing"),
        pytest.param(lambda r: r["location"].update({"name": ""}), id="name-empty"),
        pytest.param(lambda r: r.pop("alcohol_involved"), id="alcohol-missing"),
        pytest.param(
            lambda r: r["time"].update({"flexibility_minutes": 30}),
            id="flexibility-minutes-non-null",
        ),
        pytest.param(
            lambda r: r["location"].update({"flexibility": "near"}),
            id="location-flexibility-non-null",
        ),
        pytest.param(
            lambda r: r["budget"].update({"currency": "USD"}), id="currency-usd"
        ),
    ],
)
def test_rejects_invalid_responses(mutate):
    raw = _response()
    mutate(raw)
    with pytest.raises(ValidationError):
        ParserOutput.model_validate(raw)


def test_rejects_non_dict_input():
    """JSON文字列等のdictでない応答もValidationError(design §2.4の一元)。"""
    with pytest.raises(ValidationError):
        ParserOutput.model_validate('{"category": "broken"}')
```

- [x] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_parser_output.py -v`
Expected: FAIL(ImportError: ParserOutput がない)

- [x] **Step 3: 最小実装**

`backend/src/latch/intents/schema.py` を作成:

```python
"""07 §2出力JSONスキーマの検証モデル(design §3.1・§2.4)。

出力の検証は ParserOutput.model_validate に一元化する — 検証失敗はすべて
ValidationError となり、サービス層で422 VALIDATION_ERROR(構造化不能)へ
替わる(design §2.4)。失敗とするのは欠損・値域外・パース不能のみで、
余分なキーは無視する(07 §4の失敗分類に揃える)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _IgnoreExtraModel(BaseModel):
    """余分キーを無視する共通設定(07 §4の失敗分類に揃える)。"""

    model_config = ConfigDict(extra="ignore")


class ParserCategory(_IgnoreExtraModel):
    primary: Literal["meal", "drinking", "activity"]  # 必須・既定なし
    secondary: str | None = None


class ParserTime(_IgnoreExtraModel):
    start: datetime  # tz-aware必須(オフセットはJSTに限定しない)
    end: datetime | None = None
    flexibility_minutes: None = None  # MVPでは常にnull(03 D-19の固定扱い)

    @field_validator("start", "end")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("time must be tz-aware ISO8601")
        return v


class ParserLocation(_IgnoreExtraModel):
    name: str = Field(min_length=1)  # 必須3フィールドの1つ
    radius_m: int | None = None
    flexibility: None = None  # MVPでは常にnull(03 D-19の固定扱い)


class ParserBudget(_IgnoreExtraModel):
    max: int | None = None
    currency: Literal["JPY"] = "JPY"


class ParserParticipants(_IgnoreExtraModel):
    min: int | None = None
    max: int | None = None


class ParserOutput(_IgnoreExtraModel):
    """07 §2出力JSONスキーマ(この形式のみ認める)。"""

    category: ParserCategory
    alcohol_involved: bool  # 常にtrue/false(規則7)・アプリ層補完なし
    time: ParserTime
    location: ParserLocation
    budget: ParserBudget = ParserBudget()
    participants: ParserParticipants = ParserParticipants()
    soft_constraints: list[str] = []
    negative_constraints: list[str] = []  # 常に空が正常系(FR-42・規則5)
    ng_unverifiable: list[str] = []


# 03 §3・05 §5応答例の文言(D-04注意表示)。クライアントはこの文言を表示する
WARNING_MESSAGE_NG_DOWNGRADED = (
    "この条件は確実には除外できません。参考条件として扱います"
)
```

`backend/src/latch/intents/__init__.py` へ `ParserOutput`・`ParserCategory`・`ParserTime`・`ParserLocation`・`ParserBudget`・`ParserParticipants`・`WARNING_MESSAGE_NG_DOWNGRADED`(`from latch.intents.schema import ...`)を追記する。

- [x] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_parser_output.py -v`
Expected: PASS 18件(受付5件+定数1件+パラメータ化拒否11件+非dict1件。件数は目安、全緑であればよい)

- [x] **Step 5: 書式確認**

Run: `cd backend && uv run ruff format src/latch/intents tests/unit/intents && uv run ruff check src/latch/intents tests/unit/intents`
Expected: エラーなし

### Task 4: D-19アプリ層補完の単一規則(completion.py)

**Files:**
- Create: `backend/src/latch/intents/completion.py`
- Modify: `backend/src/latch/intents/__init__.py`(export追加)
- Test: `backend/tests/unit/intents/test_completion.py`

**Interfaces:**
- Produces: `DEFAULT_RADIUS_M: int`・`DEFAULT_PARTICIPANTS: tuple[int, int]`・`default_time_end(time_start: datetime) -> datetime`・`expires_at_candidates(now: datetime) -> list[datetime]`・`nearest_expires_at(time_start: datetime, now: datetime) -> datetime`。すべて純粋関数(時刻引数はClock由来の値を呼び出し側が渡す)。**消費はws-3(保存)とws-5(UI計算)** — 本単位では単一実装を提供するのみ。

**仕様の確定値(03 §3 FR-13・07 §2解釈規則)**: 4選択肢は「今夜23:30(当日JST 23:30)/ 明日12:00(翌日JST 12:00)/ 明日23:30(翌日JST 23:30)/ 3日後まで(now+72時間)」。現在より過ぎた候補は選択不可。既定=選択可能な候補のうち time.start+3時間に最も近い値(同点は最早)。`now` は tz-aware UTC(Clock契約)。

- [x] **Step 1: 失敗するテストを書く**

`backend/tests/unit/intents/test_completion.py` を作成:

```python
"""D-19補完の単一規則(design §4.1-3)。すべてFakeClock由来の固定時刻で決定的。"""

from datetime import UTC, datetime, timedelta

from latch.core.clock import JST
from latch.intents import (
    DEFAULT_PARTICIPANTS,
    DEFAULT_RADIUS_M,
    default_time_end,
    expires_at_candidates,
    nearest_expires_at,
)


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def _jst(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=JST)


def test_constants():
    assert DEFAULT_RADIUS_M == 1000
    assert DEFAULT_PARTICIPANTS == (2, 2)


def test_default_time_end_is_plus_3_hours():
    start = _jst(2026, 9, 27, 19, 0)
    assert default_time_end(start) == _jst(2026, 9, 27, 22, 0)


def test_expires_at_candidates_afternoon_jst():
    """JST 15:00 → 今夜23:30 / 明日12:00 / 明日23:30 / now+72h(03 §3の順)。"""
    now = _utc(2026, 9, 27, 6, 0)  # JST 09-27 15:00
    c = expires_at_candidates(now)
    assert c[0] == _jst(2026, 9, 27, 23, 30)
    assert c[1] == _jst(2026, 9, 28, 12, 0)
    assert c[2] == _jst(2026, 9, 28, 23, 30)
    assert c[3] == now + timedelta(hours=72)
    assert c == sorted(c)


def test_expires_at_candidates_around_jst_midnight():
    """JST 0時跨ぎ: UTC 16:00 = JST 翌01:00 → 「当日」はJST暦日付の09-28。"""
    now = _utc(2026, 9, 27, 16, 0)  # JST 09-28 01:00
    c = expires_at_candidates(now)
    assert c[0] == _jst(2026, 9, 28, 23, 30)
    assert c[1] == _jst(2026, 9, 29, 12, 0)
    assert c[2] == _jst(2026, 9, 29, 23, 30)
    assert c[3] == now + timedelta(hours=72)


def test_nearest_expires_at_prefers_closest_future():
    """time.start 19:00 JST → +3h=22:00 → 最近傍は今夜23:30。"""
    now = _utc(2026, 9, 27, 6, 0)  # JST 15:00
    time_start = _jst(2026, 9, 27, 19, 0)
    assert nearest_expires_at(time_start, now) == _jst(2026, 9, 27, 23, 30)


def test_nearest_expires_at_excludes_passed_tonight():
    """JST 23:45 → 「今夜23:30」は過ぎているので除外(03 §3 disabled規定)。"""
    now = _utc(2026, 9, 27, 14, 45)  # JST 09-27 23:45
    time_start = _jst(2026, 9, 27, 20, 0)  # target 23:00(過去側)
    assert nearest_expires_at(time_start, now) == _jst(2026, 9, 28, 12, 0)


def test_nearest_expires_at_tie_breaks_earliest():
    """target=翌17:45 は明日12:00と明日23:30のちょうど中間 → 最早(12:00)。"""
    now = _utc(2026, 9, 27, 3, 0)  # JST 09-27 12:00
    time_start = _jst(2026, 9, 28, 14, 45)  # target = 09-28 17:45
    assert nearest_expires_at(time_start, now) == _jst(2026, 9, 28, 12, 0)


def test_nearest_expires_at_with_past_time_start_returns_earliest_future():
    """time.startが過去でも最早の選択可能候補を返す(ws-3のdraft保存等での利用)。"""
    now = _utc(2026, 9, 27, 14, 45)  # JST 09-27 23:45
    time_start = _jst(2026, 9, 27, 10, 0)  # 過去
    assert nearest_expires_at(time_start, now) == _jst(2026, 9, 28, 12, 0)
```

- [x] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_completion.py -v`
Expected: FAIL(ImportError: completion の公開名がない)

- [x] **Step 3: 最小実装**

`backend/src/latch/intents/completion.py` を作成:

```python
"""D-19アプリ層補完の単一規則(07 §2解釈規則・03 §3)。

Parserは抽出のみを行い、デフォルト補完はこのモジュールが単一実装として
持つ(二重実装による不整合を防ぐ — 07 §2)。消費者は保存経路(ws-3)と
UI計算(ws-5)。時刻参照は行わない(引数で受け取る — C2)。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from latch.core.clock import JST

# 07 §2解釈規則(03 D-19)の確定値
DEFAULT_RADIUS_M = 1000  # location.radius_m の既定半径(メートル)
DEFAULT_PARTICIPANTS = (2, 2)  # participants の既定(min, max)

DEFAULT_DURATION = timedelta(hours=3)  # time.end と期限既定の基準幅


def default_time_end(time_start: datetime) -> datetime:
    """time.end null → time.start + 3時間(07 §2解釈規則)。"""
    return time_start + DEFAULT_DURATION


def _at_jst(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=JST)


def expires_at_candidates(now: datetime) -> list[datetime]:
    """有効期限4選択肢(03 §3 FR-13)。UIの選択肢順で返す。

    今夜23:30=当日JST 23:30 / 明日12:00・明日23:30=翌日JST / 3日後まで=now+72h。
    now は tz-aware(Clock.now() と同じ UTC 契約)。過ぎた候補の除外は
    nearest_expires_at 側で行う(一覧表示にも候補全値が要るため)。
    """
    jst_today = now.astimezone(JST).date()
    tomorrow = jst_today + timedelta(days=1)
    return [
        _at_jst(jst_today, 23, 30),
        _at_jst(tomorrow, 12, 0),
        _at_jst(tomorrow, 23, 30),
        now + timedelta(hours=72),
    ]


def nearest_expires_at(time_start: datetime, now: datetime) -> datetime:
    """既定の期限=time.start+3時間に最も近い選択可能候補(03 §3)。

    選択可能=候補が now より未来(過ぎた選択肢は選択不可)。同点は最早。
    time_start が過去でも成立する(target との距離順で最早の未来候補が選ばれる)。
    """
    target = default_time_end(time_start)
    selectable = [c for c in expires_at_candidates(now) if c > now]
    return min(selectable, key=lambda c: (abs(c - target), c))
```

`backend/src/latch/intents/__init__.py` へ `DEFAULT_PARTICIPANTS`・`DEFAULT_RADIUS_M`・`default_time_end`・`expires_at_candidates`・`nearest_expires_at`(`from latch.intents.completion import ...`)を追記する。

- [x] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_completion.py -v`
Expected: PASS 8件

- [x] **Step 5: 書式確認**

Run: `cd backend && uv run ruff format src/latch/intents tests/unit/intents && uv run ruff check src/latch/intents tests/unit/intents`
Expected: エラーなし

### Task 5: IntentParseService(service.py)

**Files:**
- Create: `backend/src/latch/intents/service.py`
- Modify: `backend/src/latch/intents/__init__.py`(export追加)
- Test: `backend/tests/unit/intents/test_service.py`

**Interfaces:**
- Consumes: Task 3 の `ParserOutput`・Task 1 の例外・Gateway資産 `LLMGateway.parse_intent(*, text, current_date, user_id=None) -> dict`(シグネチャ一致で構造的適合)・`make_user_lookup` の関数型 `Callable[[str, str], Awaitable[UUID | None]]`
- Produces: `SupportsParseIntent`(Protocol)・`ParseWarning`(frozen dataclass: code/condition/message)・`ParseResult`(frozen dataclass: structured_intent/warnings)・`IntentParseService`(`__init__(*, clock, parser, user_lookup)`・`async parse(*, text, auth_provider, auth_subject) -> ParseResult`)・`make_intent_parse_service(*, clock, settings, user_lookup) -> IntentParseService`。Task 6 のルータが `IntentParseService.parse` と `ParseResult` に依存する。

**例外処理の設計(design §2.2・§2.7を両立させる実装方針)**: `intents/` から `latch.llm` のimportは `make_intent_parse_service` 内の `build_llm_gateway` のみに限る。`parse_intent` の失敗はGateway契約上 `LLMTimeoutError` / `LLMProviderError`(共通基底 `LLMError`)のみであり、サービス本体は import なしで「`Exception` 全般 → `LLMUnavailableError`(503)」として受ける(ValidationError はこの呼び出しでは発生しない — 検証は後段)。unit試験で実Gatewayの両例外(§4.1-4c)が503へ替わることを立証する。

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/intents/test_service.py` を作成:

```python
"""IntentParseService ユースケース(design §4.1-4)。

Protocolスタブで決定的に、(c)(d)は実Gateway+StubLLMで実際の例外経路を検証。
ログ・例外にユーザー本文が出ないことも確認する(08 §2.4)。
"""

import copy
import logging
import uuid
from datetime import UTC, date, datetime

import pytest

from latch.core.clock import FakeClock
from latch.intents.errors import (
    DependencyUnavailableError,
    LLMUnavailableError,
    UnstructurableError,
)
from latch.intents.schema import WARNING_MESSAGE_NG_DOWNGRADED, ParserOutput
from latch.intents.service import IntentParseService
from latch.llm.gateway import LLMGateway, Timeouts
from latch.llm.stub import DEFAULT_PARSER_RESPONSE, StubLLM

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)  # JST 09-27 21:00


class StubParser:
    """SupportsParseIntent のテストスタブ。受け取った引数を記録する。"""

    def __init__(self, response: object = None, error: Exception | None = None):
        self.response = (
            copy.deepcopy(DEFAULT_PARSER_RESPONSE) if response is None else response
        )
        self.error = error
        self.calls: list[dict] = []

    async def parse_intent(
        self, *, text: str, current_date: date, user_id: str | None = None
    ) -> dict:
        self.calls.append(
            {"text": text, "current_date": current_date, "user_id": user_id}
        )
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.response)


def _clock() -> FakeClock:
    return FakeClock(NOW)


def _service(parser: StubParser, lookup) -> IntentParseService:
    return IntentParseService(clock=_clock(), parser=parser, user_lookup=lookup)


def _lookup_returning(user_id):
    async def lookup(provider: str, subject: str):
        return user_id

    return lookup


async def _lookup_none(provider: str, subject: str):
    return None


def _lookup_failing():
    async def lookup(provider: str, subject: str):
        raise RuntimeError("db down")

    return lookup


# --- (a) ハッパス(design §4.1-4a)---


async def test_happy_path_returns_parse_result_with_no_warnings():
    parser = StubParser()
    svc = _service(parser, _lookup_returning(None))
    result = await svc.parse(
        text="今夜20時から天文館で軽く飲みたい",
        auth_provider="google",
        auth_subject="sub-1",
    )
    assert result.structured_intent == ParserOutput.model_validate(
        copy.deepcopy(DEFAULT_PARSER_RESPONSE)
    )
    assert result.warnings == []
    assert parser.calls[0]["text"] == "今夜20時から天文館で軽く飲みたい"


async def test_multiple_ng_unverifiable_produce_warning_per_condition():
    """(a/D-04) ng_unverifiableの要素ごとに1件のwarning。"""
    response = copy.deepcopy(DEFAULT_PARSER_RESPONSE)
    response["ng_unverifiable"] = ["会社関係の人は避けたい", "元同僚は避けたい"]
    svc = _service(StubParser(response=response), _lookup_none)
    result = await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert len(result.warnings) == 2
    first, second = result.warnings
    assert first.code == "NG_CONDITION_DOWNGRADED"
    assert first.condition == "会社関係の人は避けたい"
    assert first.message == WARNING_MESSAGE_NG_DOWNGRADED
    assert second.condition == "元同僚は避けたい"
    assert second.message == WARNING_MESSAGE_NG_DOWNGRADED


# --- (b) 規則5正規化(design §4.1-4b・§2.5)---


async def test_negative_constraints_are_normalized_into_ng_unverifiable():
    """LLMが規則5違反でnegative_constraints非空→ng_unverifiableへ結合し警告も出る。"""
    response = copy.deepcopy(DEFAULT_PARSER_RESPONSE)
    response["negative_constraints"] = ["会社関係の人は避けたい"]
    svc = _service(StubParser(response=response), _lookup_none)
    result = await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert result.structured_intent.negative_constraints == []
    assert result.structured_intent.ng_unverifiable == ["会社関係の人は避けたい"]
    assert [w.condition for w in result.warnings] == ["会社関係の人は避けたい"]


# --- (c) 503切替: 実Gateway+StubLLM(design §4.1-4c)---


def _real_gateway(clock: FakeClock, stub: StubLLM, **timeout_s: float) -> LLMGateway:
    return LLMGateway(
        clock=clock,
        parser=stub,
        embedding=stub,
        jev=stub,
        timeouts=Timeouts(**timeout_s) if timeout_s else None,
    )


async def test_provider_error_maps_to_503_llm_unavailable():
    clock = _clock()
    gateway = _real_gateway(clock, StubLLM(fail_parser=True))
    svc = IntentParseService(clock=clock, parser=gateway, user_lookup=_lookup_none)
    with pytest.raises(LLMUnavailableError) as ei:
        await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert ei.value.http_status == 503
    assert ei.value.code == "LLM_UNAVAILABLE"


async def test_timeout_maps_to_503_llm_unavailable():
    clock = _clock()
    gateway = _real_gateway(clock, StubLLM(delay_parser_ms=200), parser_s=0.05)
    svc = IntentParseService(clock=clock, parser=gateway, user_lookup=_lookup_none)
    with pytest.raises(LLMUnavailableError):
        await svc.parse(text="t", auth_provider="google", auth_subject="s")


# --- (d) 422切替(design §4.1-4d・§2.4)---


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.pop("category"), id="category-missing"),
        pytest.param(lambda r: r.pop("time"), id="time-missing"),
        pytest.param(lambda r: r.pop("location"), id="location-missing"),
    ],
)
async def test_missing_required_field_maps_to_422_unstructurable(mutate):
    response = copy.deepcopy(DEFAULT_PARSER_RESPONSE)
    mutate(response)
    svc = _service(StubParser(response=response), _lookup_none)
    with pytest.raises(UnstructurableError) as ei:
        await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert ei.value.http_status == 422
    assert ei.value.code == "VALIDATION_ERROR"


async def test_non_dict_response_maps_to_422_unstructurable():
    """JSON文字列を返した場合も422へ一元(design §2.4)。"""
    svc = _service(StubParser(response='{"category": "broken"'), _lookup_none)
    with pytest.raises(UnstructurableError):
        await svc.parse(text="t", auth_provider="google", auth_subject="s")


# --- (e) user_lookup帰属(design §4.1-4e・§2.3)---


async def test_lookup_hit_passes_str_user_id_to_parser():
    user_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    parser = StubParser()
    svc = _service(parser, _lookup_returning(user_id))
    await svc.parse(text="t", auth_provider="google", auth_subject="sub-1")
    assert parser.calls[0]["user_id"] == str(user_id)


async def test_lookup_none_passes_none_user_id():
    parser = StubParser()
    svc = _service(parser, _lookup_none)
    await svc.parse(text="t", auth_provider="google", auth_subject="sub-1")
    assert parser.calls[0]["user_id"] is None


async def test_lookup_failure_maps_to_503_dependency_unavailable():
    svc = _service(StubParser(), _lookup_failing())
    with pytest.raises(DependencyUnavailableError) as ei:
        await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert ei.value.http_status == 503
    assert ei.value.code == "DEPENDENCY_UNAVAILABLE"


# --- (f) current_date はJST暦日付(design §4.1-4f・確定値13)---


async def test_current_date_uses_jst_calendar_date_across_midnight():
    """UTC 14:59=JST 23:59(同日) / UTC 15:00=JST 翌0:00(翌日)。"""
    parser = StubParser()
    clock = FakeClock(datetime(2026, 9, 27, 14, 59, 0, tzinfo=UTC))
    svc = IntentParseService(clock=clock, parser=parser, user_lookup=_lookup_none)
    await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert parser.calls[0]["current_date"] == date(2026, 9, 27)

    clock.set(datetime(2026, 9, 27, 15, 0, 0, tzinfo=UTC))
    await svc.parse(text="t", auth_provider="google", auth_subject="s")
    assert parser.calls[1]["current_date"] == date(2026, 9, 28)


# --- (g) 機微非混入(08 §2.4・Review Focus #4)---


async def test_user_text_never_appears_in_logs(caplog):
    secret = "会社の同僚と内緒の飲み会"
    svc = _service(StubParser(), _lookup_none)
    with caplog.at_level(logging.INFO):
        await svc.parse(text=secret, auth_provider="google", auth_subject="s")
    assert secret not in caplog.text


async def test_error_messages_contain_no_user_text():
    """422/503例外のメッセージは固定文言のみ(ハンドラがenvelopeへ出すため)。"""
    response = copy.deepcopy(DEFAULT_PARSER_RESPONSE)
    response.pop("category")
    secret = "内緒の飲み会の件"
    svc = _service(StubParser(response=response), _lookup_none)
    with pytest.raises(UnstructurableError) as ei:
        await svc.parse(text=secret, auth_provider="google", auth_subject="s")
    assert secret not in str(ei.value)
```

**import部について**: テストは `latch.llm` の `LLMGateway`/`Timeouts`/`StubLLM`/`DEFAULT_PARSER_RESPONSE` を import する(テストコードはllm/の利用者であり、Protocol構造適合の実証に実Gatewayを使う — design §2.2トレードオフ)。

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_service.py -v`
Expected: FAIL(ModuleNotFoundError: latch.intents.service がない)

- [ ] **Step 3: 最小実装**

`backend/src/latch/intents/service.py` を作成:

```python
"""parseユースケース(design §2.7)。

IntentParseService 本体は LLM 非依存・DB非依存: Gateway は
SupportsParseIntent Protocol への構造的適合(design §2.2)、user_lookup は
Callable注入。フロー: user_lookup(失敗→503)→ parse_intent(current_date=
clock.jst_date()、失敗→503 LLM_UNAVAILABLE)→ 規則5正規化 →
ParserOutput.model_validate(ValidationError→422)→ warnings構築。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from pydantic import ValidationError

from latch.core.clock import Clock
from latch.intents.errors import (
    DependencyUnavailableError,
    LLMUnavailableError,
    UnstructurableError,
)
from latch.intents.schema import WARNING_MESSAGE_NG_DOWNGRADED, ParserOutput
from latch.llm.gateway import build_llm_gateway
from latch.settings import Settings

UserLookup = Callable[[str, str], Awaitable[uuid.UUID | None]]


class SupportsParseIntent(Protocol):
    """Parser系統の構造的Protocol(design §2.2)。LLMGateway.parse_intent と適合。

    失敗は Gateway 契約どおり LLMTimeoutError / LLMProviderError
    (latch.llm.errors)を送出する(サービスは基底をimportせず
    Exception として受ける — intents/のllm非依存を守るため)。
    """

    async def parse_intent(
        self, *, text: str, current_date: date, user_id: str | None = None
    ) -> dict: ...


@dataclass(frozen=True)
class ParseWarning:
    """D-04の注意表示1件(05 §5応答例のwarnings要素と同形)。"""

    code: str  # "NG_CONDITION_DOWNGRADED"
    condition: str
    message: str  # WARNING_MESSAGE_NG_DOWNGRADED


@dataclass(frozen=True)
class ParseResult:
    """parseユースケースの結果(routes が応答へ変換する)。"""

    structured_intent: ParserOutput
    warnings: list[ParseWarning]


def _normalize_rule5(raw: dict) -> dict:
    """規則5違反の回復(design §2.5)。

    negative_constraints 非空(LLM違反)の要素を ng_unverifiable へ結合し、
    negative_constraints は空配列で応答する(FR-42の不変式回復。「常に空」の
    回復できる唯一の場所=parse境界)。結合由来の条件は warnings 生成対象に
    なるため D-04 の注意表示も出る(黙って降格させない)。
    """
    normalized = dict(raw)
    negative = normalized.get("negative_constraints") or []
    ng = list(normalized.get("ng_unverifiable") or [])
    normalized["negative_constraints"] = []
    normalized["ng_unverifiable"] = [*ng, *negative]
    return normalized


class IntentParseService:
    """POST /v1/intents/parse のユースケース(同期・再試行なしはGateway側)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        parser: SupportsParseIntent,
        user_lookup: UserLookup,
    ) -> None:
        self._clock = clock
        self._parser = parser
        self._user_lookup = user_lookup

    async def parse(
        self, *, text: str, auth_provider: str, auth_subject: str
    ) -> ParseResult:
        try:
            user_id = await self._user_lookup(auth_provider, auth_subject)
        except Exception as exc:
            raise DependencyUnavailableError(
                "intent parse dependency unavailable"
            ) from exc
        try:
            raw = await self._parser.parse_intent(
                text=text,
                current_date=self._clock.jst_date(),
                user_id=str(user_id) if user_id is not None else None,
            )
        except Exception as exc:
            # Gateway契約上ここで飛ぶのはLLMError系(timeout・API障害)のみ
            # (design §2.7)。検証(ValidationError)は後段なので含まれない。
            raise LLMUnavailableError("intent parser unavailable") from exc
        if not isinstance(raw, dict):
            raise UnstructurableError("structured intent is not extractable")
        try:
            structured = ParserOutput.model_validate(_normalize_rule5(raw))
        except ValidationError as exc:
            raise UnstructurableError("structured intent is not extractable") from exc
        warnings = [
            ParseWarning(
                code="NG_CONDITION_DOWNGRADED",
                condition=condition,
                message=WARNING_MESSAGE_NG_DOWNGRADED,
            )
            for condition in structured.ng_unverifiable
        ]
        return ParseResult(structured_intent=structured, warnings=warnings)


def make_intent_parse_service(
    *, clock: Clock, settings: Settings, user_lookup: UserLookup
) -> IntentParseService:
    """設定からIntentParseServiceを構築する(design §2.7)。

    llm/へのimport(build_llm_gateway)はこのファクトリに限る — サービス本体は
    LLM非依存(design §2.2)。llm_mode="stub" は build_llm_gateway が検証する
    (M0と同一パターン)。
    """
    gateway = build_llm_gateway(clock, settings)
    return IntentParseService(clock=clock, parser=gateway, user_lookup=user_lookup)
```

`backend/src/latch/intents/__init__.py` へ `ParseResult`・`ParseWarning`・`SupportsParseIntent`・`IntentParseService`・`make_intent_parse_service`(`from latch.intents.service import ...`)を追記する。

`backend/src/latch/intents/__init__.py` へ `ParseResult`・`ParseWarning`・`SupportsParseIntent`・`IntentParseService`・`make_intent_parse_service`(`from latch.intents.service import ...`)を追記する。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_service.py -v`
Expected: PASS 15件(ハッピー2+正規化1+503系2+422系4+lookup3+JST境界1+機微2。全緑であればよい)

- [ ] **Step 5: 書式確認**

Run: `cd backend && uv run ruff format src/latch/intents tests/unit/intents && uv run ruff check src/latch/intents tests/unit/intents`
Expected: エラーなし

### Task 6: parseルータと main.py 統合(routes.py・main.py)

**Files:**
- Create: `backend/src/latch/intents/routes.py`
- Modify: `backend/src/latch/intents/__init__.py`(export追加)
- Modify: `backend/src/latch/main.py`(include・lifespan独立スキップ判定・ハンドラ・注入引数)
- Test: `backend/tests/unit/intents/test_parse_routes.py`

**命名注記**: テストファイル名は `test_parse_routes.py` とする(既存 `backend/tests/unit/auth/test_routes.py` と同名衝突してpytest収集が落ちるため)。

**Interfaces:**
- Consumes: Task 5 の `IntentParseService.parse(*, text, auth_provider, auth_subject) -> ParseResult`・Task 3 の `ParserOutput`・`require_authenticated`(auth/deps.py・戻り `AccessTokenClaims`・属性 `auth_provider`/`auth_subject`)・main.py の `_error_body`
- Produces: `parse_router`(prefix="/v1/intents")・`get_intent_parse_service`・`create_app(clock, settings, auth_service, intent_parse_service)`(第4引数がテスト注入・app.state.intent_parse_service に載る)。ASGITransport は lifespan を実行しないため既存unit試験への影響なし(design §4.3)。

**main.py の lifespan 変更は ws-1 との唯一の衝突点**(design §2.7・§6-6)。ws-1 の存在を前提としないコードを書く(マージ時の両側追記保持はスーパーバイザーが行う)。

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/intents/test_parse_routes.py` を作成:

```python
"""parseルーティング(design §4.1-5)。スタブサービス注入+dependency_overrides。

create_app への注入と require_authenticated の上書きで認証・応答形状・
エラー切替を検証する。ASGITransport(lifespan不実行)のためDBなしで動く。
"""

import copy
import logging
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.errors import UnauthenticatedError
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.intents.errors import (
    DependencyUnavailableError,
    LLMUnavailableError,
    UnstructurableError,
)
from latch.intents.schema import WARNING_MESSAGE_NG_DOWNGRADED, ParserOutput
from latch.intents.service import ParseResult, ParseWarning
from latch.llm.stub import DEFAULT_PARSER_RESPONSE
from latch.main import create_app

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sub-1",
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _parse_result() -> ParseResult:
    return ParseResult(
        structured_intent=ParserOutput.model_validate(
            copy.deepcopy(DEFAULT_PARSER_RESPONSE)
        ),
        warnings=[],
    )


class StubService:
    """IntentParseService のテストスタブ。受け取った引数を記録する。"""

    def __init__(
        self, result: ParseResult | None = None, error: Exception | None = None
    ):
        self.result = result if result is not None else _parse_result()
        self.error = error
        self.calls: list[dict] = []

    async def parse(
        self, *, text: str, auth_provider: str, auth_subject: str
    ) -> ParseResult:
        self.calls.append(
            {"text": text, "auth_provider": auth_provider, "auth_subject": auth_subject}
        )
        if self.error is not None:
            raise self.error
        return self.result


def _client(service: StubService, auth_override=None):
    app = create_app(clock=FakeClock(NOW), intent_parse_service=service)
    app.dependency_overrides[require_authenticated] = (
        auth_override if auth_override is not None else _claims
    )
    return app, AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_200_response_shape_without_completion():
    """200・{structured_intent, warnings}・補完は適用しない(確定値10)。"""
    app, client = _client(StubService())
    async with client:
        resp = await client.post(
            "/v1/intents/parse", json={"text": "今夜20時から軽く飲みたい"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"structured_intent", "warnings"}
        si = body["structured_intent"]
        assert si["category"]["primary"] == "meal"
        assert si["alcohol_involved"] is False
        assert si["time"]["end"] is None  # 補完(time.start+3h)を適用しない
        assert si["time"]["flexibility_minutes"] is None
        assert si["location"]["radius_m"] is None  # 補完(1000)を適用しない
        assert si["location"]["flexibility"] is None
        assert si["negative_constraints"] == []
        assert body["warnings"] == []


async def test_claims_and_text_forwarded_to_service():
    service = StubService()
    app, client = _client(service)
    async with client:
        await client.post("/v1/intents/parse", json={"text": "明日18時に駅前で"})
    assert service.calls == [
        {"text": "明日18時に駅前で", "auth_provider": "google", "auth_subject": "sub-1"}
    ]


async def test_warnings_shape_matches_05_section5_example():
    result = _parse_result()
    result = ParseResult(
        structured_intent=result.structured_intent,
        warnings=[
            ParseWarning(
                code="NG_CONDITION_DOWNGRADED",
                condition="会社関係の人は避けたい",
                message=WARNING_MESSAGE_NG_DOWNGRADED,
            )
        ],
    )
    app, client = _client(StubService(result=result))
    async with client:
        resp = await client.post("/v1/intents/parse", json={"text": "t"})
        assert resp.status_code == 200
        (warning,) = resp.json()["warnings"]
        assert warning == {
            "code": "NG_CONDITION_DOWNGRADED",
            "condition": "会社関係の人は避けたい",
            "message": "この条件は確実には除外できません。参考条件として扱います",
        }


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="text-missing"),
        pytest.param({"text": ""}, id="text-empty"),
        pytest.param({"text": "あ" * 301}, id="text-301-chars"),
        pytest.param({"text": 123}, id="text-not-string"),
    ],
)
async def test_invalid_text_returns_422_validation_error_envelope(payload):
    app, client = _client(StubService())
    async with client:
        resp = await client.post("/v1/intents/parse", json=payload)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_malformed_json_returns_400_malformed_request():
    """JSON形式不正=400(tests/unit/auth/test_routes.py と同じ送り方)。"""
    app, client = _client(StubService())
    async with client:
        resp = await client.post(
            "/v1/intents/parse",
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "MALFORMED_REQUEST"


async def test_llm_unavailable_maps_to_503():
    app, client = _client(
        StubService(error=LLMUnavailableError("intent parser unavailable"))
    )
    async with client:
        resp = await client.post("/v1/intents/parse", json={"text": "t"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "LLM_UNAVAILABLE"


async def test_unstructurable_maps_to_422():
    app, client = _client(
        StubService(error=UnstructurableError("structured intent is not extractable"))
    )
    async with client:
        resp = await client.post("/v1/intents/parse", json={"text": "t"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_dependency_unavailable_maps_to_503():
    app, client = _client(
        StubService(
            error=DependencyUnavailableError("intent parse dependency unavailable")
        )
    )
    async with client:
        resp = await client.post("/v1/intents/parse", json={"text": "t"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


async def test_unauthenticated_returns_401_envelope():
    async def _raise_unauthenticated() -> AccessTokenClaims:
        raise UnauthenticatedError("missing bearer token")

    app, client = _client(StubService(), auth_override=_raise_unauthenticated)
    async with client:
        resp = await client.post("/v1/intents/parse", json={"text": "t"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_text_never_appears_in_logs(caplog):
    """Review Focus #4: ログにユーザー本文を出さない(08 §2.4)。"""
    secret = "会社の同僚と内緒の飲み会"
    app, client = _client(StubService())
    async with client:
        with caplog.at_level(logging.INFO):
            await client.post("/v1/intents/parse", json={"text": secret})
    assert secret not in caplog.text
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_parse_routes.py -v`
Expected: FAIL(create_app が intent_parse_service を受け付けない TypeError、または /v1/intents/parse が404)

- [ ] **Step 3: 最小実装**

`backend/src/latch/intents/routes.py` を作成:

```python
"""parseルータ(design §2.7・05 §5)。

ルータ単位で require_authenticated(C3・確定値12)。claimsはプリミティブ
(provider/subject)としてサービスへ渡す(サービスはauthの型をimportしない)。
ログはイベント名とcodeのみ — text・condition本文は出さない(08 §2.4)。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.intents.schema import ParserOutput
from latch.intents.service import IntentParseService, ParseResult

logger = logging.getLogger("latch.intents")

parse_router = APIRouter(
    prefix="/v1/intents",
    tags=["intents"],
    dependencies=[Depends(require_authenticated)],  # C3(05 §5全API認証済み)
)


class ParseRequest(BaseModel):
    # 07 §2解釈規則: 300字上限・切り詰めなし・空文字は必須欠落相当(422)
    text: str = Field(min_length=1, max_length=300)


class WarningOut(BaseModel):
    """05 §5応答例のwarnings要素と同形。"""

    code: str
    condition: str
    message: str


class ParseResponse(BaseModel):
    structured_intent: ParserOutput
    warnings: list[WarningOut]


def get_intent_parse_service(request: Request) -> IntentParseService:
    """app.state.intent_parse_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.intent_parse_service


def _to_response(result: ParseResult) -> ParseResponse:
    return ParseResponse(
        structured_intent=result.structured_intent,
        warnings=[
            WarningOut(code=w.code, condition=w.condition, message=w.message)
            for w in result.warnings
        ],
    )


@parse_router.post("/parse", response_model=ParseResponse)
async def parse_intent(
    body: ParseRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentParseService, Depends(get_intent_parse_service)],
) -> ParseResponse:
    """POST /v1/intents/parse(05 §5)。保存しない(SP-4)・補完を応答に適用しない。"""
    result = await svc.parse(
        text=body.text,
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
    )
    logger.info("intents.parse ok")
    return _to_response(result)
```

`backend/src/latch/intents/__init__.py` へ `parse_router`(`from latch.intents.routes import parse_router`)を追記する。

`backend/src/latch/main.py` を次のとおり変更する(変更後の関係部のみ。既存の `/health`・`public_router`/`logout_router` の include・AuthError ハンドラ・RequestValidationError ハンドラはそのまま):

(a) import 節へ追加:

```python
from latch.intents import IntentsError, make_intent_parse_service, parse_router
```

(b) `_lifespan` を次の全文へ置換:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    # サービスごとの独立スキップ判定(テスト注入があれば構築しない)
    build_auth = not hasattr(app.state, "auth_service")
    build_intents = not hasattr(app.state, "intent_parse_service")
    if not (build_auth or build_intents):
        yield
        return
    settings: Settings = app.state.settings
    redis_client = None
    if build_auth:
        redis_client = aioredis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
    # engine は auth と intents の共有資産(lookup 1本・design §2.7)
    engine = getattr(app.state, "db_engine", None)
    if engine is None:
        engine = create_db_engine(settings)
        app.state.db_engine = engine
    user_lookup = make_user_lookup(engine)
    if build_auth:
        app.state.auth_service = build_auth_service(
            clock=app.state.clock,
            settings=settings,
            redis_client=redis_client,
            user_lookup=user_lookup,
        )
    if build_intents:
        app.state.intent_parse_service = make_intent_parse_service(
            clock=app.state.clock,
            settings=settings,
            user_lookup=user_lookup,
        )
    try:
        yield
    finally:
        if redis_client is not None:
            await redis_client.aclose()
        await engine.dispose()
```

(c) `create_app` を次のとおり変更(シグネチャに第4引数・注入・include・ハンドラ):

```python
def create_app(
    clock: Clock | None = None,
    settings: Settings | None = None,
    auth_service=None,
    intent_parse_service=None,
) -> FastAPI:
    app = FastAPI(title="LATCH API", lifespan=_lifespan)
    app.state.clock = clock if clock is not None else SystemClock()
    app.state.settings = settings if settings is not None else Settings()
    if auth_service is not None:
        app.state.auth_service = auth_service
    if intent_parse_service is not None:
        app.state.intent_parse_service = intent_parse_service

    @app.get("/health")
    async def health(
        clock: Annotated[Clock, Depends(get_clock)],
    ) -> dict[str, str]:
        return {"status": "ok", "server_time": clock.now().isoformat()}

    app.include_router(public_router)
    app.include_router(logout_router)
    app.include_router(parse_router)

    @app.exception_handler(AuthError)
    async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
        logger.warning("auth.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )

    @app.exception_handler(IntentsError)
    async def intents_error_handler(
        request: Request, exc: IntentsError
    ) -> JSONResponse:
        intents_logger.warning("intents.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 05 第5節の使い分け: JSON形式不正=400 / 必須欠落・値域外=422
        if any(err.get("type") == "json_invalid" for err in exc.errors()):
            return JSONResponse(
                status_code=400,
                content=_error_body(
                    "MALFORMED_REQUEST", "request body is not valid JSON"
                ),
            )
        return JSONResponse(
            status_code=422,
            content=_error_body("VALIDATION_ERROR", "request validation failed"),
        )

    return app
```

(d) モジュール末尾のロガー定義部へ1行追加(`logger = logging.getLogger("latch.auth")` の直後):

```python
intents_logger = logging.getLogger("latch.intents")
```

(e) モジュールdocstringの「lifespanでredis・db engine・AuthServiceを構築する(design §2.6)。第3引数 auth_service 指定時は構築をスキップ(テスト注入)。」の文を「lifespanでredis・db engine・AuthService・IntentParseServiceを構築する。第3引数 auth_service / 第4引数 intent_parse_service 指定時は該当サービスの構築をスキップ(テスト注入・サービスごとの独立判定)。」へ更新する。

- [ ] **Step 4: テストが通ること・既存unit試験が壊れていないことを確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_parse_routes.py -v`
Expected: PASS 13件(全緑であればよい)

Run: `cd backend && uv run pytest -m "not integration"`
Expected: 既存209件+新規全部 PASS(lifespan変更はASGITransportがlifespanを実行しないため既存試験に影響しない — design §4.3)

- [ ] **Step 5: 書式確認**

Run: `cd backend && uv run ruff format src/latch tests/unit/intents && uv run ruff check src/latch tests/unit/intents`
Expected: エラーなし

### Task 7: integration試験の作成(実行はスーパーバイザー検証)

**Files:**
- Create: `backend/tests/integration/test_intents_parse_api.py`

**Interfaces:**
- Consumes: compose常設api(`api_client` fixture: 127.0.0.1:8000)・内部ツール `python -m latch.auth issue-idp-token`(M0 ws-3資産・tests/integration/conftest.py)
- Produces: design §4.2-1〜5 の試験(§4.2-6「事後処理不要」はDB書込みを行わないこのファイルの構成で体現)。**実行(make test-ci)はスーパーバイザーが行う** — 本タスクはファイル作成と収集確認まで。

503/422の構造化不能経路はスタブの設定経路がcompose api にないためunit(design §4.1-4/5)で証明済みであり、integrationでは検証しない(M0 ws-3と同じ振り分け・design §4.2末尾)。

- [ ] **Step 1: 試験ファイルを作成する**

`backend/tests/integration/test_intents_parse_api.py` を作成:

```python
"""POST /v1/intents/parse のci環境実証(design §4.2)。

実HTTP(compose api=127.0.0.1:8000)。実行は make test-ci — composeのapiは
build型・ソースマウントなしのため、コード変更後は必ず
`docker compose build api` を先に行うこと(Makefileのtest-ciは再ビルドしない)。
parseはDB書込みゼロ(05 §5 SP-4)のためUser行INSERTも掃除も行わない(§4.2-6)。
subjectは毎回ユニーク値(未登録=user_id=None経路の検証を兼ねる)。
"""

import asyncio
import sys
import uuid as uuid_mod

import pytest

pytestmark = pytest.mark.integration

# latch.llm.stub.DEFAULT_PARSER_RESPONSE と同形の期待値(イメージ内コード由来・確定)
EXPECTED_STUB_INTENT = {
    "category": {"primary": "meal", "secondary": None},
    "alcohol_involved": False,
    "time": {
        "start": "2026-09-27T19:00:00+09:00",
        "end": None,
        "flexibility_minutes": None,
    },
    "location": {"name": "東京駅", "radius_m": None, "flexibility": None},
    "budget": {"max": None, "currency": "JPY"},
    "participants": {"min": None, "max": None},
    "soft_constraints": [],
    "negative_constraints": [],
    "ng_unverifiable": [],
}


def _unique_subject() -> str:
    return f"m1ws2-{uuid_mod.uuid4().hex[:12]}"


async def _access_token(api_client) -> str:
    """内部ツールでIdPトークンを発行し、API発行JWTへ交換する(10 第1節)。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        "google",
        "--subject",
        _unique_subject(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    resp = await api_client.post(
        "/v1/auth/token",
        json={"provider": "google", "idp_token": stdout.decode().strip()},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _parse(api_client, token: str, json=None, content=None):
    headers = {"Authorization": f"Bearer {token}"}
    if content is not None:
        return await api_client.post(
            "/v1/intents/parse", headers=headers, content=content
        )
    return await api_client.post("/v1/intents/parse", headers=headers, json=json)


async def test_1_happy_path_returns_stub_shaped_intent(api_client):
    """§4.2-1: 200・structured_intent=スタブ既定応答と同形・warnings=[]。"""
    token = await _access_token(api_client)
    resp = await _parse(
        api_client,
        token,
        json={"text": "今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"structured_intent", "warnings"}
    assert body["structured_intent"] == EXPECTED_STUB_INTENT
    assert body["warnings"] == []


async def test_2_text_length_limits(api_client):
    """§4.2-2: 300字ちょうどは200・301字は422(切り詰めなし・07 §2)。"""
    token = await _access_token(api_client)
    ok = await _parse(api_client, token, json={"text": "あ" * 300})
    assert ok.status_code == 200, ok.text
    over = await _parse(api_client, token, json={"text": "あ" * 301})
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_3_missing_text_and_malformed_json(api_client):
    """§4.2-3: text欠落=422 VALIDATION_ERROR / JSON破損=400 MALFORMED_REQUEST。"""
    token = await _access_token(api_client)
    missing = await _parse(api_client, token, json={})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"
    malformed = await api_client.post(
        "/v1/intents/parse",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        content=b"{not valid json",
    )
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "MALFORMED_REQUEST"


async def test_4_authentication_required(api_client):
    """§4.2-4: Authorization欠落・改ざんJWT → 401 UNAUTHENTICATED。"""
    no_header = await api_client.post("/v1/intents/parse", json={"text": "t"})
    assert no_header.status_code == 401
    assert no_header.json()["error"]["code"] == "UNAUTHENTICATED"
    forged = await api_client.post(
        "/v1/intents/parse",
        headers={"Authorization": "Bearer forged.jwt.value"},
        json={"text": "t"},
    )
    assert forged.status_code == 401
    assert forged.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_5_unregistered_subject_can_parse(api_client):
    """§4.2-5: User行のないsubjectでも200(User行をparseの前提にしない)。"""
    token = await _access_token(api_client)  # users行は作らない
    resp = await _parse(api_client, token, json={"text": "今夜20時から軽く飲みたい"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["structured_intent"] == EXPECTED_STUB_INTENT
```

- [ ] **Step 2: 収集確認(実行はしない)**

Run: `cd backend && uv run pytest --collect-only -q tests/integration/test_intents_parse_api.py`
Expected: 5件の試験が収集され、importエラーがない

- [ ] **Step 3: 書式確認**

Run: `cd backend && uv run ruff format tests/integration/test_intents_parse_api.py && uv run ruff check tests/integration/test_intents_parse_api.py`
Expected: エラーなし

### Task 8: 全体回帰と報告ファイル

**Files:**
- Create: `docs/plans/M1/ws-2-report.md`(§5の形式)

- [ ] **Step 1: 全体lint**

Run: `make lint`
Expected: エラーなし

- [ ] **Step 2: 全体unit試験**

Run: `make test`
Expected: 既存209件+新規(test_errors 3・test_prompt 3・test_parser_output 18・test_completion 8・test_service 15・test_parse_routes 13)。**§0.6 の既知の既存失敗1件(test_token_payload_structure)を除いて全部 PASS**。件数は概算、失敗0(既知1件を除く)と総件数を報告ファイルへ記録する。既知1件については「git stash で ws-2 差分を外しても同一失敗」を確認して ws-2 起因でないことを示す(§0.6)

- [ ] **Step 3: 時刻参照検査(完了条件5)**

Run: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src`
Expected: ヒットは `backend/src/latch/core/clock.py` のみ

- [ ] **Step 4: 差分の範囲確認(完了条件6)**

Run: `git status --porcelain` と `git diff --stat`
Expected: 差分は次の一覧のみ — `backend/src/latch/intents/`(新規7ファイル)・`backend/src/latch/main.py`・`backend/tests/unit/intents/`(新規6ファイル)・`backend/tests/integration/test_intents_parse_api.py`(新規)・`docs/plans/M1/ws-2-plan.md`(checkbox更新)・`docs/plans/M1/ws-2-report.md`(新規)。§3.1 の禁止領域に差分があれば、その時点で修正して再確認する

- [ ] **Step 5: integration収集の最終確認**

Run: `cd backend && uv run pytest --collect-only -q`
Expected: unit+integration 全試験が収集される(importエラーなし)

- [ ] **Step 6: 報告ファイルを作成する**

`docs/plans/M1/ws-2-report.md` を §5 の形式で作成する。特に:
- 「test-ci=スーパーバイザー検証待ち(STATUS運用ルール)」と明記する
- 検証手順として「`docker compose build api` → `make test-ci`」を明記する(make test-ci はapiイメージを再ビルドしないため)
- 完了条件 §4 の7項目それぞれにコマンドと出力抜粋を貼る
- 逸脱・判断があれば「計画からの逸脱・判断」へ書く(なければ「なし」)

- [ ] **Step 7: 計画書のcheckboxを更新する**

本計画書(docs/plans/M1/ws-2-plan.md)の実行済みステップの `- [ ]` を `- [x]` へ更新する。

---

## 9. 実装エージェント向け最終確認(Task 8完了前の自査)

1. **design §5(完了条件)との対応**: 本計画 §4 は design §5 を検証可能な形に展開したもの。design §5-2 の test-ci 実行はスーパーバイザー検証へ委ね、報告ファイルにその旨を残す。
2. **禁止領域**: `git diff --stat` をもう一度見る。llm/・auth/・core/・geo/・worker/・alembic/・settings.py・pyproject.toml・uv.lock・compose.yaml・Makefile・users/ に差分があってはならない。
3. **未実装の残置がないか**: `rg -n 'TODO|FIXME|PLACEHOLDER' backend/src/latch/intents backend/tests/unit/intents` が空であること。
4. **プロンプト**: test_prompt.py がグリーンである限り、`PARSER_SYSTEM_PROMPT` は 07 §2 と一致している(docsが正)。

