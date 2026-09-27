# M0 ws-2(LLM Gateway)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依存ハード制約C4を解消する — Intent Parser・Embedding・Jevの3系統の外部LLM呼び出しを単一の `LLMGateway` へ集約し、(1)送信記録(送信先・データ種別・時刻。内容を含まない)、(2)系統別プロバイダABCによる差し替え抽象化、(3)テストモード(決定的応答スタブ+レイテンシ注入+エラー注入)をM0で作る。実プロバイダ接続はT1確定後(M1以降)。

**Architecture:** `LLMGateway` 1クラスに系統別メソッド3つ(parse_intent / embed_intent / judge_pair)。プロバイダ抽象は系統別ABC×3(`ParserProvider` / `EmbeddingProvider` / `JevProvider`)で、M0の実装は3 ABCを実装する単一の `StubLLM` のみ。送信記録は構造化ログ(ロガー `latch.llm.send` へのJSON 1行)とし、レコード型 `SendRecord`(pydantic)が許可リスト(08 第2.4節)の実体 — この型に存在しないフィールドは出力され得ない。系統別timeout(Parser 10秒/Embedding 2秒/Jev 6秒)はGateway内の `asyncio.timeout` で強制し、成否にかかわらず送信記録を出してから例外を送出する(記録が先・送出が後)。app/workerへのプロセス統合は行わない(消費者はM1のparse API。design §2.7)。

**Tech Stack:** 既存スタックのみ(Python 3.13 / pydantic(v2・pydantic-settingsに同梱) / pytest+pytest-asyncio / ruff)。**依存追加なし**(design §2.8 — pyproject.toml・uv.lock・Dockerfile・compose.yaml・Makefileはすべて無変更)。

**Spec:** `docs/plans/M0/ws-2-design.md`(agent1設計メモ。本計画はこの文書の§3ファイル構成・§4テスト方針・§5完了条件を各タスクへ展開したもの。design.md §6の論点はすべて推奨で固定済み — §6-1送信記録=構造化ログはsupervisor承認済み)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m0-ws-2`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。
- **設計判断の固定値**: design.md §6の4論点は推奨で固定済みとして本計画に落としてある(送信記録=構造化ログ、Parserのuser_id引数予約、レイテンシ=固定遅延のみ、Jev再試行はGateway IFに入れない)。**固定値(ABCの構成・timeout値・フィールド構成・ロガー名等)を変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする(12 第8節 運用ルール5)。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由(システムPython 3.14と衝突させないため)。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| 3系統の外部送信は単一Gateway集約・送信先とデータ種別の記録・プロバイダ差し替え抽象化(C4) | 04 第2節 / 12 第2節 C4 |
| 送信記録は送信先・データ種別・時刻。**内容を含まない**。問い合わせ時に開示できる状態 | 08 第3節 / 01 第21節 |
| Embedding/Jevの送信範囲=正規化テキスト(raw_text不使用)。Parser系統の位置づけ | 01 第21節 / design §1.3 |
| 構造化ログは許可リスト方式(列挙型で固定。raw_text・geo座標・constraints系は不許可。例外はIDのみ) | 08 第2.4節 |
| 3系統の呼び出し特性: Parser=同期・10秒・再試行なし / Embedding=非同期・2秒・再試行なし / Jev=非同期・6秒・出力検証失敗のみ1回再試行 | 07 第1節・07 第5節 D-17 |
| Parser出力JSONスキーマ(9キー。必須3フィールド=category/time.start/location.name) | 07 第2節 |
| Embedding次元768・モデル識別子はintents.embedding_modelへ(C11: Gateway抽象化により未確定のまま進行可) | 07 第3節・05 第2節 / 12 第2節 C11 |
| Jev入力=2 Intentの正規化テキスト([hard]/[soft]タグ付き・visibility/notification_level不含)・出力=7設問JSON | 07 第4節 |
| テストモード=記録済み応答スタブ+レイテンシ注入でLLM呼び出しを決定的に | 10 第1節「疑似イベント注入」 |
| LLM障害試験=Gatewayへのエラー注入(100%エラーおよびtimeout) | 10 第4.5節 |
| M0スコープ5の文言とG0「LLM Gatewayがスタブで3系統の呼び出しを記録できる」 | 12 第3節 M0 |
| 送信記録の時刻もClock経由(arch testがbackend/src全体で強制) | 04 第5節(FR-41)・雛形 test_arch_no_direct_time |
| 複数プロバイダ併存が将来像(差し替え単位=系統)・契約面足切り先行 | 04 第4節 D-14基準6・基準1 |
| 完了条件6項目・テスト方針8項目・ファイル構成 | design.md §3〜§5 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **依存追加なし**。`backend/pyproject.toml`・`backend/uv.lock`・`backend/Dockerfile`・ルート `compose.yaml`・`Makefile` に差分を出さない(design §2.8)
- 製品コード(`backend/src/latch/`)で実時間を直接参照するAPI(`datetime.now` / `datetime.utcnow` / `date.today` / `time.time` / `time.monotonic` / `time.sleep` / `from time import`)の使用禁止。例外は `core/clock.py` のみ。**`asyncio.sleep` は許容**(レイテンシ注入の待機は時刻参照ではない。design §2.6) — ただしarch testの禁止トークンに触れないよう `import asyncio` + `asyncio.sleep(...)` の形式で書くこと
- 実時間の計測(`from time import perf_counter` 等)と実時間の待機は**テストコードのみ**で行う(arch testのスキャン対象は `backend/src` のみ。design §4)
- 送信記録ロガー名は **`latch.llm.send`**(固定)。出力はJSON 1行(`SendRecord.model_dump_json()`)
- 系統別timeoutの定数値は **Parser 10.0秒 / Embedding 2.0秒 / Jev 6.0秒**(07 第1節)。環境変数・Settingsにtimeout項目を作らない(上書きは `Timeouts` のコード引数のみ。design §2.4)
- `EMBEDDING_DIMENSIONS = 768`(05 第2節 vector(768))
- SendRecordのフィールドはdesign §3.1の7フィールド固定(occurred_at / system / destination / status / error_code / intent_ids / user_id)。**テキストを運ぶフィールドを追加しない**(08 第2.4節)
- naive datetimeを扱わない。`occurred_at` はClock(`now()`=tz-aware UTC)由来のみ
- `backend/tests/conftest.py`・既存テスト群・`main.py`・`worker/`・`core/` は**変更しない**(design §3.2〜§3.3)
- 遅延注入の実時間待機を伴うテストは注入値を数十msに抑える(design §4)

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **機微テキストのログ混入** — SendRecordにテキスト系フィールドが1つでも増えると01 第21節・08 第2.4節違反になる → Task 1の `model_fields` 完全一致検査と Task 4の「入力テキストがログに現れない」検証が所有
2. **timeout/エラー時の送信記録漏れ** — 例外送出を先にすると失敗時の記録が飛ばされる(G0の「記録できる」が崩れる)→ Task 4/5のtimeout・error試験(例外送出と記録出力を同一テスト内で検証)が所有
3. **occurred_atの実時間化** — `clock.now()` 以外で時刻を取るとClock差し替えが効かずテスト再現性が壊れる(arch testはトークン検査であり、Clockを参照しない実装は検出できない)→ Task 4の「occurred_at == 注入したFakeClockの時刻」一致検証が所有
4. **スタブベクトルの次元歪み** — 768以外を返すとM2 pgvector格納試験の前提が崩れる(C11)→ Task 2の `len(v) == EMBEDDING_DIMENSIONS == 768` 二重検証が所有
5. **LLM設定のenv経路の過剰供給** — timeoutやfailフラグがenvから変更できるとdocs確定値(07 第1節)が黙って変わる → Task 6の「Settingsのllm_*フィールドが4項目のみ」検査が所有

## 4. スコープ(作成・変更するファイル一覧)

作成(design.md §3.1どおり):

```text
backend/src/latch/llm/__init__.py            (Task 1でdocstringのみ作成 → Task 7で公開IFの再exportへ更新)
backend/src/latch/llm/errors.py
backend/src/latch/llm/records.py
backend/src/latch/llm/providers.py
backend/src/latch/llm/gateway.py
backend/src/latch/llm/stub.py
backend/tests/unit/llm/test_gateway_parse.py
backend/tests/unit/llm/test_gateway_embed.py
backend/tests/unit/llm/test_gateway_jev.py
backend/tests/unit/llm/test_send_record.py
backend/tests/unit/llm/test_stub.py
backend/tests/unit/llm/test_llm_factory.py
docs/plans/M0/ws-2-report.md                (報告ファイル。Task 7で作成)
```

変更(追記のみ):

- `backend/src/latch/settings.py` — LLM設定4項目を追記(Task 6)。既存項目(app_env・log_level)・構造は変更しない

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`(gitignore済み)。**`backend/tests/unit/llm/` に `__init__.py`・`conftest.py` は作らない**(design §3.1の一覧にない。各テストファイル内に必要なfixtureを定義する)。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- `backend/src/latch/main.py`・`backend/src/latch/worker/`・`backend/src/latch/core/`(design §2.7 — Gatewayのapp.state登録・get_llm依存はM1のparse API設計に委ねる)
- `backend/tests/conftest.py`・`backend/tests/unit/` の既存テストファイル・`backend/tests/integration/`(並走するws-1との共通ファイル衝突を避ける。design §3.3)
- `backend/pyproject.toml`・`backend/uv.lock`・`backend/Dockerfile`(依存追加なし)
- ルートの `compose.yaml`・`Makefile`・`docker/`・`.mise.toml`・`.gitignore`・`README.md`
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/M0/` の他ファイル(design・ws-1系ファイル)
- `prototype/` 全体・`.claude/`
- スコープ外と判断する基準: 実プロバイダadapter・httpx等のHTTPクライアント・API鍵管理(T1後)/ POST /v1/intents/parse・Embedding Worker・Jev Layer 4・circuit breaker・出力JSONスキーマ検証・再試行制御(M1/M2)/ 正規化テキスト生成(呼び出し側の責務)/ Parser必須3フィールド判定(M1)/ レイテンシ分布(p50/p95)・トークン数・cost計上(M4)/ 送信記録の検索・開示UI(M4)/ Pub/Sub・DB接続・Redis(M2以降・ws-1/ws-3)。これらが必要になったと感じても作らない — design §1.4に列挙された後続単位のスコープ
- mainブランチへのコミット・push・マージ

## 6. 完了条件(design.md §5の6項目。検証コマンドつき。Task 7で全て実行し報告ファイルに証拠を残す)

1. **`make lint` `make test` がグリーン(unit追加分を含む)**
   検証: `make lint && make test` — ともにexit 0
2. **3系統のスタブ呼び出しで送信記録(送信先・データ種別・時刻=Clock由来)が出力されること(G0文言。design §4-1〜3)**
   検証: `cd backend && uv run pytest tests/unit/llm/test_gateway_parse.py tests/unit/llm/test_gateway_embed.py tests/unit/llm/test_gateway_jev.py -v` — exit 0(ok/timeout/errorの全記録試験を含む)
3. **決定性・レイテンシ注入・エラー注入・許可リスト・ファクトリの試験がグリーン(design §4-4〜7)**
   検証: `cd backend && uv run pytest tests/unit/llm -v` — exit 0(test_stub・test_send_record・test_llm_factoryを含む全体)
4. **実時間参照が `core/clock.py` のみ(llm/配下はヒットしない。雛形の完了条件5を維持)**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0(既存arch testがllm/を自動スキャン)
5. **依存追加なし**
   検証: `git diff --stat main -- backend/pyproject.toml backend/uv.lock` — 出力なし
6. **触るファイルが `settings.py`(追記)と `llm/`・`tests/unit/llm/`(新規)と報告ファイルのみ**
   検証: `git diff --name-only main | sort` が§4の一覧と完全一致。`git diff main -- backend/src/latch/settings.py` が追記のみ(既存行の変更・削除を含まない)

## 7. 報告形式

**結果ファイル**: `docs/plans/M0/ws-2-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M0 ws-2(LLM Gateway) 実行報告

- ブランチ: m0-ws-2 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る> |
| 2 | 3系統の送信記録(G0文言) | PASS/FAIL | <pytestサマリー行> |
| 3 | 決定性・注入・許可リスト・ファクトリ | PASS/FAIL | <pytestサマリー行> |
| 4 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 5 | 依存追加なし | PASS/FAIL | <git diff --stat出力(空なら「空」)> |
| 6 | 触るファイルがスコープどおり | PASS/FAIL | <git diff --name-only出力> |

## 固定値の変更有無(design.md §6・本計画§2)
- 送信記録=構造化ログ(latch.llm.send・SendRecord 7フィールド): 変更なし / 変更あり(<前→後+理由>)
- 系統別ABC×3 + StubLLM単一クラス: 変更なし / 変更あり(<前→後+理由>)
- timeout 10/2/6秒のGateway内強制(Timeouts上書き・envなし): 変更なし / 変更あり(<前→後+理由>)
- スタブ応答=コンストラクタ上書き+遅延/エラー注入: 変更なし / 変更あり(<前→後+理由>)

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件6項目のPASS/FAIL一覧。

---

### Task 1: 例外階層 + SendRecord / send_log(許可リストの実体)

**Files:**
- Create: `backend/src/latch/llm/__init__.py`(この時点ではdocstringのみ。再exportはTask 7)、`backend/src/latch/llm/errors.py`、`backend/src/latch/llm/records.py`
- Test: `backend/tests/unit/llm/test_send_record.py`

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: `latch.llm.errors.LLMError / LLMTimeoutError / LLMProviderError`(例外階層)。`latch.llm.records.SendRecord`(pydantic・7フィールド固定)・`send_log(record: SendRecord) -> None`(ロガー `latch.llm.send` へJSON 1行)・`SystemName` / `SendStatus`(Literal型)・`LOGGER_NAME = "latch.llm.send"`。以降全タスクとM1/M2が利用

- [ ] **Step 1: パッケージとモジュールの土台を作る**

```bash
mkdir -p backend/src/latch/llm backend/tests/unit/llm
printf '"""LLM Gateway(M0 ws-2)。3系統集約・送信記録・プロバイダ抽象・スタブ。"""\n' > backend/src/latch/llm/__init__.py
```

(再exportはTask 7でこのファイルに追記する。`__init__.py` は `latch.llm` パッケージの成立のためにこの時点で作る)

- [ ] **Step 2: 失敗するテストを書く**

`backend/tests/unit/llm/test_send_record.py`:

```python
"""SendRecordの許可リスト(08 第2.4節)とJSON 1行出力(design §4-6)。"""

import json
import logging
from datetime import UTC, datetime

from latch.llm.records import LOGGER_NAME, SendRecord, send_log

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)

ALLOWED_FIELDS = {
    "occurred_at",
    "system",
    "destination",
    "status",
    "error_code",
    "intent_ids",
    "user_id",
}


def _record(**overrides) -> SendRecord:
    base = {
        "occurred_at": NOW,
        "system": "jev",
        "destination": "stub",
        "status": "ok",
        "intent_ids": ["i-1", "i-2"],
    }
    return SendRecord(**{**base, **overrides})


def test_send_record_fields_are_exactly_the_allowlist():
    # 08 第2.4節: 構造化ログは許可リスト方式。この型のフィールド=出力フィールド。
    assert set(SendRecord.model_fields) == ALLOWED_FIELDS


def test_send_record_has_no_text_or_location_fields():
    # 01 第21節・08 第2.4節: raw_text・位置・constraints系は不許可。
    for forbidden in (
        "text",
        "raw_text",
        "prompt",
        "payload",
        "content",
        "location",
        "geo",
        "constraints",
        "reason",
    ):
        assert forbidden not in SendRecord.model_fields


def test_send_record_system_is_enum_like():
    rec = _record(system="embedding")
    assert rec.system == "embedding"


def test_send_log_emits_single_json_line(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        send_log(_record(user_id="user-1"))
    messages = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert len(messages) == 1  # 1呼び出し=1行
    payload = json.loads(messages[0].getMessage())
    assert payload == {
        "occurred_at": payload["occurred_at"],  # 表記(Z/+00:00)に依存しない
        "system": "jev",
        "destination": "stub",
        "status": "ok",
        "error_code": None,
        "intent_ids": ["i-1", "i-2"],
        "user_id": "user-1",
    }
    assert datetime.fromisoformat(payload["occurred_at"]) == NOW
    assert datetime.fromisoformat(payload["occurred_at"]).utcoffset().total_seconds() == 0


def test_send_log_json_never_contains_free_text(caplog):
    # 機微(入力テキストの断片)をレコードに渡す経路がそもそも存在しないことを、
    # ログ出力の実測でピン留めする(Review Focus #1)。
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        send_log(_record(error_code="LLMProviderError"))
    assert "スタブ" not in caplog.text  # 例外メッセージ等の自由文が混入しない
    payload = json.loads(
        [r for r in caplog.records if r.name == LOGGER_NAME][0].getMessage()
    )
    assert set(payload) == ALLOWED_FIELDS  # 追加キーが現れない
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_send_record.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.llm.records'`)

- [ ] **Step 4: 最小実装**

`backend/src/latch/llm/errors.py`:

```python
"""LLM Gateway例外階層(design §3.1)。

08 第2.4節「例外・エラーはIDのみ」— 例外メッセージにIntent本文を混ぜない。
"""

from __future__ import annotations


class LLMError(Exception):
    """LLM Gateway経由の呼び出し失敗の基底。"""


class LLMTimeoutError(LLMError):
    """系統別timeout超過(07 第1節)。再試行なし — 呼び出し側は縮退/フォールバックへ。"""


class LLMProviderError(LLMError):
    """プロバイダ側の失敗(10 第4.5節の100%エラー注入が再現する状態)。"""
```

`backend/src/latch/llm/records.py`:

```python
"""LLM送信記録(08 第3節: 送信先・データ種別・時刻。内容を含まない)。

SendRecordが構造化ログの許可リストの実体(08 第2.4節)。出力できるフィールドを
この型で固定する — この型に存在しないフィールドは出力され得ない。
時刻occurred_atはClock.now()(tz-aware UTC)由来のみ(design確定値16)。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LOGGER_NAME = "latch.llm.send"

SystemName = Literal["intent_parser", "embedding", "jev"]
SendStatus = Literal["ok", "timeout", "error"]


class SendRecord(BaseModel):
    """送信記録1件(08 第3節)。機微テキストを運ぶフィールドは持たない。"""

    occurred_at: datetime  # Clock.now()(tz-aware UTC)
    system: SystemName  # データ種別(どの系統の送信か)
    destination: str  # 送信先(Provider.name)
    status: SendStatus
    error_code: str | None = None  # 例外IDのみ(08 第2.4節「例外・エラーはIDのみ」)
    intent_ids: list[str] | None = None  # Embedding=1件・Jev=2件・Parser=None
    user_id: str | None = None  # Parser(M1のparse API実装で値が入る。design §6-2)


def send_log(record: SendRecord) -> None:
    """ロガー 'latch.llm.send' へJSON 1行で出力(04 第3節ログ集計への接続点)。"""
    logging.getLogger(LOGGER_NAME).info(record.model_dump_json())
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_send_record.py -v`
Expected: PASS(5件)

- [ ] **Step 6: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/llm backend/tests/unit/llm
git commit -m "feat: LLM Gateway例外階層とSendRecord送信記録(許可リスト・08 第2.4節)"
```

---

### Task 2: 系統別Provider ABC + StubLLM(決定的応答)

**Files:**
- Create: `backend/src/latch/llm/providers.py`、`backend/src/latch/llm/stub.py`
- Test: `backend/tests/unit/llm/test_stub.py`

**Interfaces:**
- Consumes: Task 1の `latch.llm.errors`
- Produces: `latch.llm.providers.EMBEDDING_DIMENSIONS = 768`・`ParserProvider`(`name: str` + `async complete_structured(text: str, current_date: date) -> dict`)・`EmbeddingProvider`(`name` + `async embed(text: str) -> list[float]`)・`JevProvider`(`name` + `async judge(intent_a: str, intent_b: str) -> dict`)。`latch.llm.stub.StubLLM`(3 ABC実装・`name == "stub"`・`parser_response` / `embedding_response` / `jev_response` コンストラクタ上書き)・`DEFAULT_PARSER_RESPONSE`・`DEFAULT_JEV_RESPONSE`。Task 3〜6とM1/M2が利用

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/llm/test_stub.py`:

```python
"""StubLLM: ABC実装・決定性・デフォルト応答・次元(design §4-4)。"""

from datetime import date

import pytest

from latch.llm.providers import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    JevProvider,
    ParserProvider,
)
from latch.llm.stub import DEFAULT_JEV_RESPONSE, DEFAULT_PARSER_RESPONSE, StubLLM

# 07 第2節 出力JSONスキーマの全キー
PARSER_KEYS = {
    "category",
    "alcohol_involved",
    "time",
    "location",
    "budget",
    "participants",
    "soft_constraints",
    "negative_constraints",
    "ng_unverifiable",
}
# 07 第4節 出力JSONスキーマの全キー(7設問)
JEV_KEYS = {
    "would_a_accept_b",
    "would_b_accept_a",
    "purpose_fit",
    "mood_fit",
    "timing_fit",
    "social_fit",
    "latent_yes",
}


def test_provider_abcs_cannot_be_instantiated():
    # ABC: 実装漏れを静かに防ぐ(design §2.2・Clockと同じ判断)
    with pytest.raises(TypeError):
        ParserProvider()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        EmbeddingProvider()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        JevProvider()  # type: ignore[abstract]


def test_stub_implements_three_abcs():
    stub = StubLLM()
    assert isinstance(stub, ParserProvider)
    assert isinstance(stub, EmbeddingProvider)
    assert isinstance(stub, JevProvider)
    assert stub.name == "stub"  # 送信記録のdestination(design §3.1)


async def test_stub_parser_default_response_matches_07_section2():
    resp = await StubLLM().complete_structured(
        "今週末20時から軽く飲みたい", date(2026, 9, 27)
    )
    assert set(resp) == PARSER_KEYS
    # design §2.5: 必須3フィールド(category/time.start/location.name)を含む
    assert resp["category"]["primary"] in ("meal", "drinking", "activity")
    assert resp["time"]["start"]
    assert resp["location"]["name"]


async def test_stub_jev_default_response_matches_07_section4():
    resp = await StubLLM().judge(
        "Intent A:\n[hard] category: drinking", "Intent B:\n[hard] category: meal"
    )
    assert set(resp) == JEV_KEYS
    assert set(resp["would_a_accept_b"]) == {"score", "reason"}


async def test_stub_embed_returns_deterministic_768_vector():
    stub = StubLLM()
    text = "meal / 平日夜20-23時 / 東京駅 / 2人 / 軽く"
    v1 = await stub.embed(text)
    v2 = await stub.embed(text)
    # Review Focus #4: 次元は768(05 第2節 vector(768)。C11)であることを二重検証
    assert len(v1) == EMBEDDING_DIMENSIONS
    assert EMBEDDING_DIMENSIONS == 768
    assert v1 == v2  # 決定性(10 第1節): 同一入力・同一設定→同一応答
    assert all(isinstance(x, float) for x in v1[:8])


async def test_stub_embed_is_input_derived():
    # 入力由来(ハッシュ)の生成であること — 全入力が同一ベクトルではM2以降の試験ができない
    stub = StubLLM()
    v_a = await stub.embed("meal / 東京駅")
    v_b = await stub.embed("drinking / 天文館")
    assert v_a != v_b


def test_default_response_constants_match_spec():
    assert set(DEFAULT_PARSER_RESPONSE) == PARSER_KEYS
    assert set(DEFAULT_JEV_RESPONSE) == JEV_KEYS


async def test_stub_responses_are_deterministic_across_instances():
    # 同一設定の別インスタンスでも同一応答(乱数・実時間参照なしの証明)
    text = "今週末20時から駅前で軽く飲みたい"
    a = await StubLLM().complete_structured(text, date(2026, 9, 27))
    b = await StubLLM().complete_structured(text, date(2026, 9, 27))
    assert a == b
    assert a == DEFAULT_PARSER_RESPONSE


async def test_stub_response_override():
    # テストが任意の応答・検証失敗応答を注入できる(design §2.5)
    bad_parser = {"category": None}  # M1のスキーマ検証失敗再現の例
    got = await StubLLM(parser_response=bad_parser).complete_structured(
        "t", date(2026, 1, 1)
    )
    assert got == bad_parser
    fixed = [0.25] * 768
    assert await StubLLM(embedding_response=fixed).embed("t") == fixed
    bad_jev = {"would_a_accept_b": {}}  # M2の出力検証失敗再現の例
    assert await StubLLM(jev_response=bad_jev).judge("a", "b") == bad_jev
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_stub.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.llm.providers'`)

- [ ] **Step 3: providers.py を実装**

`backend/src/latch/llm/providers.py`:

```python
"""系統別プロバイダABC(design §2.2)。M0の実装はStubLLMのみ(T1確定後に実装が現れる)。

差し替え単位=系統(04 第4節 D-14基準6: 複数プロバイダのGateway経由併存)。
実装漏れを静かに防ぐABC(Clockと同じ判断。1モジュールに閉じる)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

EMBEDDING_DIMENSIONS = 768  # 05 第2節 vector(768)。C11: モデル確定までの固定値


class ParserProvider(ABC):
    """07 第2節 Intent Parser。送信内容=ユーザー入力テキスト(design §1.3)。"""

    name: str  # 送信記録の送信先(08 第3節)。実装クラスが設定する

    @abstractmethod
    async def complete_structured(self, text: str, current_date: date) -> dict: ...


class EmbeddingProvider(ABC):
    """07 第3節 Embedding。正規化テキスト(raw_text不使用)→768次元ベクトル。"""

    name: str

    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...


class JevProvider(ABC):
    """07 第4節 Jev。2 Intent分の正規化テキスト([hard]/[soft]タグ付き)→7設問JSON。"""

    name: str

    @abstractmethod
    async def judge(self, intent_a: str, intent_b: str) -> dict: ...
```

- [ ] **Step 4: stub.py を実装(このタスクでは応答系のみ。遅延・failはTask 3)**

`backend/src/latch/llm/stub.py`:

```python
"""テストモードのスタブプロバイダ(10 第1節「疑似イベント注入」)。

3系統のABCを実装する単一クラス。乱数・実時間参照なし=決定的
(同一入力・同一設定→同一応答)。応答はコンストラクタ引数で差し替え可能。
"""

from __future__ import annotations

import hashlib
from datetime import date

from latch.llm.providers import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    JevProvider,
    ParserProvider,
)

# 07 第2節 出力JSONスキーマ適合の固定応答(必須3フィールドを含む)
DEFAULT_PARSER_RESPONSE: dict = {
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

# 07 第4節 7設問JSONスキーマ適合の固定応答(根拠なき0.50=規則3)
DEFAULT_JEV_RESPONSE: dict = {
    "would_a_accept_b": {"score": 0.5, "reason": "根拠なしのため0.50(規則3)"},
    "would_b_accept_a": {"score": 0.5, "reason": "根拠なしのため0.50(規則3)"},
    "purpose_fit": 0.5,
    "mood_fit": 0.5,
    "timing_fit": 0.5,
    "social_fit": 0.5,
    "latent_yes": 0.5,
}


def _stub_vector(text: str) -> list[float]:
    """入力テキスト由来の決定的768次元ベクトル。値に意味はない(スタブである以上)。"""
    values: list[float] = []
    block = 0
    while len(values) < EMBEDDING_DIMENSIONS:
        digest = hashlib.sha256(f"{block}:{text}".encode("utf-8")).digest()
        values.extend(byte / 255.0 for byte in digest)
        block += 1
    return values[:EMBEDDING_DIMENSIONS]


class StubLLM(ParserProvider, EmbeddingProvider, JevProvider):
    """テストモード(10 第1節)。name="stub"。"""

    def __init__(
        self,
        *,
        parser_response: dict | None = None,
        embedding_response: list[float] | None = None,
        jev_response: dict | None = None,
    ) -> None:
        self.name = "stub"
        self._parser_response = (
            parser_response if parser_response is not None else DEFAULT_PARSER_RESPONSE
        )
        self._embedding_response = embedding_response
        self._jev_response = (
            jev_response if jev_response is not None else DEFAULT_JEV_RESPONSE
        )

    async def complete_structured(self, text: str, current_date: date) -> dict:
        return self._parser_response

    async def embed(self, text: str) -> list[float]:
        if self._embedding_response is not None:
            return self._embedding_response
        return _stub_vector(text)

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        return self._jev_response
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_stub.py -v`
Expected: PASS(9件)

- [ ] **Step 6: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/llm/providers.py backend/src/latch/llm/stub.py backend/tests/unit/llm/test_stub.py
git commit -m "feat: 系統別プロバイダABCと決定的スタブ(10 第1節テストモード)"
```

---

### Task 3: StubLLMのレイテンシ注入・エラー注入

**Files:**
- Modify: `backend/src/latch/llm/stub.py`(遅延とfailフラグを追加)
- Test: `backend/tests/unit/llm/test_stub.py`(追記)

**Interfaces:**
- Consumes: Task 2の `StubLLM`
- Produces: `StubLLM` コンストラクタ引数の追加 — `delay_parser_ms` / `delay_embedding_ms` / `delay_jev_ms: int = 0`(asyncio.sleepによる実遅延)+ `fail_parser` / `fail_embedding` / `fail_jev: bool = False`(Trueなら `LLMProviderError` 送出)。遅延の与え方は `_delay_ms(system)` 1関数に閉じる(M4のp50/p95分布版への差し替え点。design §6-3)。Task 4〜6のtimeout・エラー試験が利用

- [ ] **Step 1: 失敗するテストを書く(test_stub.py に追記)**

ファイル先頭のimport節へ追記:

```python
from time import perf_counter  # 実時間計測はテストコードのみ(design §4)

from latch.llm.errors import LLMProviderError
```

(tests/配下はarch testのスキャン対象外。`from time import perf_counter` はテストでのみ許される)

ファイル末尾へ追記:

```python
async def test_stub_delay_injects_real_latency():
    # design §2.6: 系統別の固定遅延。呼び出し前にasyncio.sleepする(時刻参照ではない)
    stub = StubLLM(delay_jev_ms=50)
    start = perf_counter()
    await stub.judge("a", "b")
    assert (perf_counter() - start) * 1000 >= 50


async def test_stub_delay_is_per_system():
    stub = StubLLM(delay_jev_ms=50)
    start = perf_counter()
    await stub.embed("t")  # embeddingは遅延なし → 即返る
    elapsed_ms = (perf_counter() - start) * 1000
    assert elapsed_ms < 50


async def test_stub_zero_delay_by_default():
    stub = StubLLM()
    start = perf_counter()
    await stub.complete_structured("t", date(2026, 1, 1))
    await stub.embed("t")
    await stub.judge("a", "b")
    assert (perf_counter() - start) * 1000 < 50  # 既定は無効(10 第1節)


async def test_stub_fail_flags_raise_provider_error():
    # design §2.6 / 10 第4.5節: 100%エラー注入の再現
    with pytest.raises(LLMProviderError):
        await StubLLM(fail_parser=True).complete_structured("t", date(2026, 1, 1))
    with pytest.raises(LLMProviderError):
        await StubLLM(fail_embedding=True).embed("t")
    with pytest.raises(LLMProviderError):
        await StubLLM(fail_jev=True).judge("a", "b")


async def test_stub_fail_is_per_system():
    stub = StubLLM(fail_parser=True)
    with pytest.raises(LLMProviderError):
        await stub.complete_structured("t", date(2026, 1, 1))
    assert len(await stub.embed("t")) == 768  # 他系統は影響を受けない
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_stub.py -v`
Expected: 新規5件がFAIL(`TypeError: StubLLM.__init__() got an unexpected keyword argument 'delay_jev_ms'` 等)

- [ ] **Step 3: stub.py へ遅延・failを実装**

`backend/src/latch/llm/stub.py` のimport節を変更(standard順: `asyncio` を先頭に追加):

```python
from __future__ import annotations

import asyncio
import hashlib
from datetime import date

from latch.llm.errors import LLMProviderError
from latch.llm.providers import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    JevProvider,
    ParserProvider,
)
```

`StubLLM.__init__` を差し替え、メソッド群を書き換え:

```python
class StubLLM(ParserProvider, EmbeddingProvider, JevProvider):
    """テストモード(10 第1節)。name="stub"。"""

    def __init__(
        self,
        *,
        parser_response: dict | None = None,
        embedding_response: list[float] | None = None,
        jev_response: dict | None = None,
        delay_parser_ms: int = 0,
        delay_embedding_ms: int = 0,
        delay_jev_ms: int = 0,
        fail_parser: bool = False,
        fail_embedding: bool = False,
        fail_jev: bool = False,
    ) -> None:
        self.name = "stub"
        self._parser_response = (
            parser_response if parser_response is not None else DEFAULT_PARSER_RESPONSE
        )
        self._embedding_response = embedding_response
        self._jev_response = (
            jev_response if jev_response is not None else DEFAULT_JEV_RESPONSE
        )
        self._delay_parser_ms = delay_parser_ms
        self._delay_embedding_ms = delay_embedding_ms
        self._delay_jev_ms = delay_jev_ms
        self._fail_parser = fail_parser
        self._fail_embedding = fail_embedding
        self._fail_jev = fail_jev

    def _delay_ms(self, system: str) -> int:
        """系統→遅延ms。p50/p95分布版(M4)への差し替えはこの1関数で完結する。"""
        return {
            "intent_parser": self._delay_parser_ms,
            "embedding": self._delay_embedding_ms,
            "jev": self._delay_jev_ms,
        }[system]

    async def _apply_delay(self, system: str) -> None:
        delay_ms = self._delay_ms(system)
        if delay_ms > 0:
            # 待機であり時刻参照ではない(arch testの禁止対象外。design §2.6)
            await asyncio.sleep(delay_ms / 1000)

    async def complete_structured(self, text: str, current_date: date) -> dict:
        await self._apply_delay("intent_parser")
        if self._fail_parser:
            raise LLMProviderError("stub: fail_parser=True")
        return self._parser_response

    async def embed(self, text: str) -> list[float]:
        await self._apply_delay("embedding")
        if self._fail_embedding:
            raise LLMProviderError("stub: fail_embedding=True")
        if self._embedding_response is not None:
            return self._embedding_response
        return _stub_vector(text)

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        await self._apply_delay("jev")
        if self._fail_jev:
            raise LLMProviderError("stub: fail_jev=True")
        return self._jev_response
```

(`Task 2で作った同名クラスをこの内容で置き換える。`__init__` に3+3+3引数が並ぶ形になる)

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_stub.py -v`
Expected: PASS(14件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/llm/stub.py backend/tests/unit/llm/test_stub.py
git commit -m "feat: スタブのレイテンシ注入とエラー注入(10 第4.5節障害再現)"
```

---

### Task 4: LLMGateway共通経路 + Parser系統(timeout・送信記録)

**Files:**
- Create: `backend/src/latch/llm/gateway.py`
- Test: `backend/tests/unit/llm/test_gateway_parse.py`

**Interfaces:**
- Consumes: Task 1(errors・records)・Task 2/3(StubLLM)・雛形の `latch.core.clock.Clock` / `FakeClock`
- Produces: `latch.llm.gateway.TIMEOUT_PARSER_S = 10.0` / `TIMEOUT_EMBEDDING_S = 2.0` / `TIMEOUT_JEV_S = 6.0`(07 第1節)・`Timeouts`(frozen dataclass・`parser_s` / `embedding_s` / `jev_s` デフォルト=定数値)・`LLMGateway(clock, parser, embedding, jev, timeouts=None)`(keyword-only)・`async parse_intent(*, text: str, current_date: date, user_id: str | None = None) -> dict`。`embed_intent` / `judge_pair` はTask 5、`build_llm_gateway` はTask 6で追加

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/llm/test_gateway_parse.py`:

```python
"""Parser系統: 応答・送信記録・timeout・エラー(design §4-1〜3)。

送信内容=ユーザー入力テキスト(design §1.3) — だからこそ記録に内容が出ない
こと(08 第2.4節)をこの系統で最も厳しく検証する。
"""

import json
import logging
from datetime import UTC, date, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import LLMProviderError, LLMTimeoutError
from latch.llm.gateway import (
    TIMEOUT_EMBEDDING_S,
    TIMEOUT_JEV_S,
    TIMEOUT_PARSER_S,
    LLMGateway,
    Timeouts,
)
from latch.llm.records import LOGGER_NAME
from latch.llm.stub import StubLLM

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 9, 27)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _gateway(clock: FakeClock, stub: StubLLM, **timeout_s: float) -> LLMGateway:
    timeouts = Timeouts(**timeout_s) if timeout_s else None
    return LLMGateway(clock=clock, parser=stub, embedding=stub, jev=stub, timeouts=timeouts)


def _send_payloads(caplog) -> list[dict]:
    messages = [r for r in caplog.records if r.name == LOGGER_NAME]
    return [json.loads(m.getMessage()) for m in messages]


def test_timeout_constants_match_07_section1():
    assert TIMEOUT_PARSER_S == 10.0
    assert TIMEOUT_EMBEDDING_S == 2.0
    assert TIMEOUT_JEV_S == 6.0


def test_default_timeouts_are_the_constants():
    t = Timeouts()
    assert (t.parser_s, t.embedding_s, t.jev_s) == (10.0, 2.0, 6.0)


async def test_parse_returns_stub_response(clock):
    result = await _gateway(clock, StubLLM()).parse_intent(
        text="今週末20時から駅前で軽く飲みたい", current_date=TODAY
    )
    assert result["category"]["primary"] == "meal"  # デフォルト応答(07 第2節スキーマ)
    assert result["time"]["start"] == "2026-09-27T19:00:00+09:00"


async def test_parse_records_send_record_with_clock_time(clock, caplog):
    # G0の証明(12 M0): スタブで3系統の呼び出しを記録できる — Parser系統分
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).parse_intent(
            text="今週末20時から駅前で軽く飲みたい",
            current_date=TODAY,
            user_id="user-1",
        )
    payloads = _send_payloads(caplog)
    assert len(payloads) == 1
    payload = payloads[0]
    # Review Focus #3: occurred_atはClock由来(FakeClockの時刻と一致=実時間ではない)
    occurred = datetime.fromisoformat(payload["occurred_at"])
    assert occurred == NOW
    assert occurred.utcoffset() == timedelta(0)
    assert payload["system"] == "intent_parser"
    assert payload["destination"] == "stub"
    assert payload["status"] == "ok"
    assert payload["error_code"] is None
    assert payload["intent_ids"] is None  # parseはIntent未作成(design §6-2)
    assert payload["user_id"] == "user-1"


async def test_parse_send_record_keys_are_exactly_the_allowlist(clock, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).parse_intent(text="t", current_date=TODAY)
    (payload,) = _send_payloads(caplog)
    assert set(payload) == {
        "occurred_at",
        "system",
        "destination",
        "status",
        "error_code",
        "intent_ids",
        "user_id",
    }


async def test_parse_input_text_never_appears_in_send_record(clock, caplog):
    # Review Focus #1: Parser系統の送信内容=ユーザー生テキスト。記録は内容を含まない
    secret = "会社の同僚と内緒の飲み会"
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).parse_intent(text=secret, current_date=TODAY)
    assert secret not in caplog.text
    assert "内緒" not in caplog.text


async def test_parse_timeout_records_then_raises(clock, caplog):
    # design §2.4/§2.6: 遅延>timeoutでtimeoutが発火(10 第4.5節のtimeoutエラー注入)
    stub = StubLLM(delay_parser_ms=200)
    gw = _gateway(clock, stub, parser_s=0.05)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMTimeoutError):
            await gw.parse_intent(text="t", current_date=TODAY)
    # Review Focus #2: 例外送出の前に記録が出ている(失敗時も記録が漏れない)
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "timeout"
    assert payload["error_code"] == "LLMTimeoutError"
    assert payload["system"] == "intent_parser"


async def test_parse_provider_error_records_then_raises(clock, caplog):
    # 10 第4.5節: 100%エラー注入(failフラグ)の再現
    gw = _gateway(clock, StubLLM(fail_parser=True))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMProviderError):
            await gw.parse_intent(text="t", current_date=TODAY)
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "error"
    assert payload["error_code"] == "LLMProviderError"


async def test_parse_unexpected_exception_is_wrapped(clock, caplog):
    # プロバイダがLLMError以外を送出してもLLMProviderErrorへ包む(design §3.1)
    class BrokenParser(StubLLM):
        async def complete_structured(self, text: str, current_date: date) -> dict:
            raise RuntimeError("provider crashed")

    gw = _gateway(clock, BrokenParser())
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMProviderError):
            await gw.parse_intent(text="t", current_date=TODAY)
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "error"
    assert payload["error_code"] == "RuntimeError"  # 元例外のIDのみ(08 第2.4節)
    assert "provider crashed" not in caplog.text  # 例外メッセージ(自由文)は記録しない
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_gateway_parse.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.llm.gateway'`)

- [ ] **Step 3: gateway.py を実装(Parser系統 + 共通 `_call`)**

`backend/src/latch/llm/gateway.py`:

```python
"""LLMGateway本体(design §2.1・§2.4)。Parser/Embedding/Jevの単一共通経路(C4)。

各系統メソッドは共通の形: asyncio.timeoutでProvider呼び出しを包み、成否に
かかわらずSendRecordを構築してsend_logで出し、成功なら応答を返し、timeoutは
LLMTimeoutError、プロバイダ例外はLLMProviderErrorで送出する。
送信記録が先・例外送出が後 — 失敗時も記録が漏れない。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from latch.core.clock import Clock
from latch.llm.errors import LLMError, LLMProviderError, LLMTimeoutError
from latch.llm.providers import EmbeddingProvider, JevProvider, ParserProvider
from latch.llm.records import SendRecord, SendStatus, SystemName, send_log

TIMEOUT_PARSER_S = 10.0  # 07 第1節(D-17)同期・再試行なし
TIMEOUT_EMBEDDING_S = 2.0  # 07 第1節 非同期・再試行なし
TIMEOUT_JEV_S = 6.0  # 07 第1節 非同期(timeoutは再試行せず縮退へ)


@dataclass(frozen=True)
class Timeouts:
    """系統別timeout(07 第1節の確定値がデフォルト)。

    unit試験で短縮注入するための上書き経路(design §2.4)。環境変数には
    出さない — docs確定値の恒久的な変更をenvで黙って行える経路を作らない。
    """

    parser_s: float = TIMEOUT_PARSER_S
    embedding_s: float = TIMEOUT_EMBEDDING_S
    jev_s: float = TIMEOUT_JEV_S


class LLMGateway:
    """3系統の外部LLM呼び出しの単一共通経路(C4)。送信記録とtimeoutをここで持つ。"""

    def __init__(
        self,
        *,
        clock: Clock,
        parser: ParserProvider,
        embedding: EmbeddingProvider,
        jev: JevProvider,
        timeouts: Timeouts | None = None,
    ) -> None:
        self._clock = clock
        self._parser = parser
        self._embedding = embedding
        self._jev = jev
        self._timeouts = timeouts if timeouts is not None else Timeouts()

    async def parse_intent(
        self, *, text: str, current_date: date, user_id: str | None = None
    ) -> dict:
        """07 第2節。timeout 10秒・再試行なし。送信内容=ユーザー入力テキスト(design §1.3)。"""
        return await self._call(
            system="intent_parser",
            destination=self._parser.name,
            timeout_s=self._timeouts.parser_s,
            intent_ids=None,
            user_id=user_id,
            invoke=lambda: self._parser.complete_structured(text, current_date),
        )

    async def _call(
        self,
        *,
        system: SystemName,
        destination: str,
        timeout_s: float,
        intent_ids: list[str] | None,
        user_id: str | None,
        invoke: Callable[[], Awaitable[Any]],
    ) -> Any:
        """送信記録→応答/例外の共通経路。status: ok / timeout / error。"""
        occurred_at = self._clock.now()
        try:
            async with asyncio.timeout(timeout_s):
                result = await invoke()
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                status: SendStatus = "timeout"
                error_code = "LLMTimeoutError"
                wrapped: LLMError = LLMTimeoutError(
                    f"{system} timed out after {timeout_s}s"
                )
            elif isinstance(exc, LLMError):
                status = "error"
                error_code = type(exc).__name__
                wrapped = exc
            else:
                status = "error"
                error_code = type(exc).__name__
                wrapped = LLMProviderError(f"{system} provider failed: {error_code}")
            send_log(
                SendRecord(
                    occurred_at=occurred_at,
                    system=system,
                    destination=destination,
                    status=status,
                    error_code=error_code,
                    intent_ids=intent_ids,
                    user_id=user_id,
                )
            )
            raise wrapped from exc
        send_log(
            SendRecord(
                occurred_at=occurred_at,
                system=system,
                destination=destination,
                status="ok",
                error_code=None,
                intent_ids=intent_ids,
                user_id=user_id,
            )
        )
        return result
```

(embed_intent / judge_pair はTask 5で、build_llm_gateway はTask 6でこのファイルへ追加する)

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_gateway_parse.py -v`
Expected: PASS(9件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/llm/gateway.py backend/tests/unit/llm/test_gateway_parse.py
git commit -m "feat: LLMGateway共通経路とParser系統(timeout・送信記録・C4)"
```

---

### Task 5: LLMGatewayのEmbedding・Jev系統

**Files:**
- Modify: `backend/src/latch/llm/gateway.py`(`embed_intent` / `judge_pair` を追加)
- Test: `backend/tests/unit/llm/test_gateway_embed.py`、`backend/tests/unit/llm/test_gateway_jev.py`

**Interfaces:**
- Consumes: Task 4の `LLMGateway._call`・`Timeouts`
- Produces: `async embed_intent(*, text: str, intent_id: str) -> list[float]`(timeout 2秒・記録のintent_ids=[intent_id])・`async judge_pair(*, intent_a: str, intent_b: str, intent_ids: list[str]) -> dict`(timeout 6秒・記録のintent_ids=引数のまま2件)。M2のEmbedding Worker・Jev Layer 4が利用

- [ ] **Step 1: 失敗するテストを書く(Embedding系統)**

`backend/tests/unit/llm/test_gateway_embed.py`:

```python
"""Embedding系統: 768次元応答・送信記録・timeout・エラー(design §4-1〜3)。"""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import LLMProviderError, LLMTimeoutError
from latch.llm.gateway import LLMGateway, Timeouts
from latch.llm.providers import EMBEDDING_DIMENSIONS
from latch.llm.records import LOGGER_NAME
from latch.llm.stub import StubLLM

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _gateway(clock: FakeClock, stub: StubLLM, **timeout_s: float) -> LLMGateway:
    timeouts = Timeouts(**timeout_s) if timeout_s else None
    return LLMGateway(clock=clock, parser=stub, embedding=stub, jev=stub, timeouts=timeouts)


def _send_payloads(caplog) -> list[dict]:
    messages = [r for r in caplog.records if r.name == LOGGER_NAME]
    return [json.loads(m.getMessage()) for m in messages]


async def test_embed_returns_768_dim_vector(clock):
    vector = await _gateway(clock, StubLLM()).embed_intent(
        text="meal / 平日夜20-23時 / 東京駅 / 2人 / 軽く", intent_id="i-1"
    )
    assert len(vector) == EMBEDDING_DIMENSIONS  # 05 第2節 vector(768)


async def test_embed_records_send_record(clock, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).embed_intent(
            text="meal / 平日夜20-23時 / 東京駅 / 2人 / 軽く", intent_id="i-1"
        )
    (payload,) = _send_payloads(caplog)
    occurred = datetime.fromisoformat(payload["occurred_at"])
    assert occurred == NOW  # Clock由来
    assert occurred.utcoffset() == timedelta(0)
    assert payload["system"] == "embedding"
    assert payload["destination"] == "stub"
    assert payload["status"] == "ok"
    assert payload["intent_ids"] == ["i-1"]  # Embedding=1件(design §3.1)
    assert payload["user_id"] is None


async def test_embed_timeout_records_then_raises(clock, caplog):
    stub = StubLLM(delay_embedding_ms=200)
    gw = _gateway(clock, stub, embedding_s=0.05)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMTimeoutError):
            await gw.embed_intent(text="t", intent_id="i-1")
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "timeout"
    assert payload["error_code"] == "LLMTimeoutError"


async def test_embed_provider_error_records_then_raises(clock, caplog):
    gw = _gateway(clock, StubLLM(fail_embedding=True))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMProviderError):
            await gw.embed_intent(text="t", intent_id="i-1")
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "error"
    assert payload["error_code"] == "LLMProviderError"


async def test_embed_input_text_never_appears_in_send_record(clock, caplog):
    # 正規化テキストは構造化データ由来だが、記録は内容を含まない(08 第2.4節)
    secret = "天文館周辺で軽く飲みたい"
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).embed_intent(text=secret, intent_id="i-1")
    assert secret not in caplog.text
```

- [ ] **Step 2: 失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_gateway_embed.py -v`
Expected: FAIL(`AttributeError: 'LLMGateway' object has no attribute 'embed_intent'`)

- [ ] **Step 3: 失敗するテストを書く(Jev系統)**

`backend/tests/unit/llm/test_gateway_jev.py`:

```python
"""Jev系統: 7設問応答・送信記録(intent_ids 2件)・timeout・エラー(design §4-1〜3)。"""

import json
import logging
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import LLMProviderError, LLMTimeoutError
from latch.llm.gateway import LLMGateway, Timeouts
from latch.llm.records import LOGGER_NAME
from latch.llm.stub import DEFAULT_JEV_RESPONSE, StubLLM

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
INTENT_A = "Intent A:\n[hard] category: drinking\n[hard] time: 2026-09-26 20:00–23:00"
INTENT_B = "Intent B:\n[hard] category: meal\n[hard] time: 2026-09-26 19:30–22:30"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _gateway(clock: FakeClock, stub: StubLLM, **timeout_s: float) -> LLMGateway:
    timeouts = Timeouts(**timeout_s) if timeout_s else None
    return LLMGateway(clock=clock, parser=stub, embedding=stub, jev=stub, timeouts=timeouts)


def _send_payloads(caplog) -> list[dict]:
    messages = [r for r in caplog.records if r.name == LOGGER_NAME]
    return [json.loads(m.getMessage()) for m in messages]


async def test_judge_returns_7_question_response(clock):
    resp = await _gateway(clock, StubLLM()).judge_pair(
        intent_a=INTENT_A, intent_b=INTENT_B, intent_ids=["i-1", "i-2"]
    )
    assert resp == DEFAULT_JEV_RESPONSE  # 07 第4節 7設問JSON


async def test_judge_records_send_record_with_two_intent_ids(clock, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).judge_pair(
            intent_a=INTENT_A, intent_b=INTENT_B, intent_ids=["i-1", "i-2"]
        )
    (payload,) = _send_payloads(caplog)
    assert datetime.fromisoformat(payload["occurred_at"]) == NOW  # Clock由来
    assert payload["system"] == "jev"
    assert payload["destination"] == "stub"
    assert payload["status"] == "ok"
    assert payload["intent_ids"] == ["i-1", "i-2"]  # Jev=2件(design §3.1)
    assert payload["user_id"] is None


async def test_judge_timeout_records_then_raises(clock, caplog):
    stub = StubLLM(delay_jev_ms=200)
    gw = _gateway(clock, stub, jev_s=0.05)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMTimeoutError):
            await gw.judge_pair(intent_a=INTENT_A, intent_b=INTENT_B, intent_ids=["i-1", "i-2"])
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "timeout"
    assert payload["error_code"] == "LLMTimeoutError"


async def test_judge_provider_error_records_then_raises(clock, caplog):
    gw = _gateway(clock, StubLLM(fail_jev=True))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMProviderError):
            await gw.judge_pair(intent_a=INTENT_A, intent_b=INTENT_B, intent_ids=["i-1", "i-2"])
    (payload,) = _send_payloads(caplog)
    assert payload["status"] == "error"
    assert payload["error_code"] == "LLMProviderError"


async def test_judge_input_texts_never_appear_in_send_record(clock, caplog):
    # 正規化テキスト(soft/NG条件を含む)が記録へ出ない(01 第21節・design §1.3)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _gateway(clock, StubLLM()).judge_pair(
            intent_a="Intent A:\n[soft] 会社関係の人は避けたい",
            intent_b="Intent B:\n[soft] 軽く飲したい",
            intent_ids=["i-1", "i-2"],
        )
    assert "会社関係" not in caplog.text
    assert "飲したい" not in caplog.text
```

- [ ] **Step 4: gateway.py に2メソッドを実装**

`backend/src/latch/llm/gateway.py` の `parse_intent` の後に追加:

```python
    async def embed_intent(self, *, text: str, intent_id: str) -> list[float]:
        """07 第3節。正規化テキスト→768次元。timeout 2秒・再試行なし。"""
        return await self._call(
            system="embedding",
            destination=self._embedding.name,
            timeout_s=self._timeouts.embedding_s,
            intent_ids=[intent_id],
            user_id=None,
            invoke=lambda: self._embedding.embed(text),
        )

    async def judge_pair(
        self, *, intent_a: str, intent_b: str, intent_ids: list[str]
    ) -> dict:
        """07 第4節。2 Intent分の正規化テキスト→7設問JSON。timeout 6秒。"""
        return await self._call(
            system="jev",
            destination=self._jev.name,
            timeout_s=self._timeouts.jev_s,
            intent_ids=intent_ids,
            user_id=None,
            invoke=lambda: self._jev.judge(intent_a, intent_b),
        )
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm -v`
Expected: PASS(test_gateway_embed.py 5件 + test_gateway_jev.py 5件を含む全体)

- [ ] **Step 6: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/llm/gateway.py backend/tests/unit/llm/test_gateway_embed.py backend/tests/unit/llm/test_gateway_jev.py
git commit -m "feat: Embedding/Jev系統メソッド(768次元・intent_ids記録・timeout)"
```

---

### Task 6: LLM設定4項目 + build_llm_gateway ファクトリ

**Files:**
- Modify: `backend/src/latch/settings.py`(LLM設定4項目を追記)、`backend/src/latch/llm/gateway.py`(`build_llm_gateway` を追加)
- Test: `backend/tests/unit/llm/test_llm_factory.py`

**Interfaces:**
- Consumes: Task 4/5の `LLMGateway`・Task 2/3の `StubLLM`・雛形の `Settings`
- Produces: `Settings` の新フィールド — `llm_mode: str = "stub"`・`llm_stub_delay_parser_ms: int = 0`・`llm_stub_delay_embedding_ms: int = 0`・`llm_stub_delay_jev_ms: int = 0`(env prefix `LATCH_`)。`build_llm_gateway(clock: Clock, settings: Settings) -> LLMGateway`(llm_mode="stub"以外はValueError)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/llm/test_llm_factory.py`:

```python
"""build_llm_gatewayとLLM設定(design §4-7・§2.6)。"""

import json
import logging
from datetime import UTC, date, datetime
from time import perf_counter  # 実時間計測はテストコードのみ(design §4)

import pytest

from latch.core.clock import FakeClock
from latch.llm.gateway import build_llm_gateway
from latch.llm.records import LOGGER_NAME
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)

LLM_ENV_VARS = (
    "LATCH_LLM_MODE",
    "LATCH_LLM_STUB_DELAY_PARSER_MS",
    "LATCH_LLM_STUB_DELAY_EMBEDDING_MS",
    "LATCH_LLM_STUB_DELAY_JEV_MS",
)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _clean_settings(monkeypatch, **overrides) -> Settings:
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(**overrides)


def test_llm_settings_defaults(monkeypatch):
    s = _clean_settings(monkeypatch)
    assert s.llm_mode == "stub"
    assert s.llm_stub_delay_parser_ms == 0
    assert s.llm_stub_delay_embedding_ms == 0
    assert s.llm_stub_delay_jev_ms == 0


def test_llm_settings_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_LLM_STUB_DELAY_JEV_MS", "120")
    for var in LLM_ENV_VARS:
        if var != "LATCH_LLM_STUB_DELAY_JEV_MS":
            monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.llm_stub_delay_jev_ms == 120


def test_llm_settings_are_exactly_four_fields(monkeypatch):
    # Review Focus #5: timeout・failフラグのenv経路を作らない(design §2.4・§2.6)。
    # LLM系設定はこの4項目のみであることを機械検査する。
    _clean_settings(monkeypatch)
    llm_fields = {f for f in Settings.model_fields if f.startswith("llm_")}
    assert llm_fields == {
        "llm_mode",
        "llm_stub_delay_parser_ms",
        "llm_stub_delay_embedding_ms",
        "llm_stub_delay_jev_ms",
    }


async def test_factory_builds_working_stub_gateway(clock, caplog, monkeypatch):
    gw = build_llm_gateway(clock, _clean_settings(monkeypatch))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = await gw.parse_intent(text="t", current_date=date(2026, 9, 27))
    assert result["category"]["primary"] == "meal"
    messages = [r for r in caplog.records if r.name == LOGGER_NAME]
    (payload,) = [json.loads(m.getMessage()) for m in messages]
    assert payload["destination"] == "stub"  # StubLLM内包の挙動証明
    assert payload["status"] == "ok"


async def test_factory_passes_delay_settings_to_stub(clock, monkeypatch):
    gw = build_llm_gateway(
        clock, _clean_settings(monkeypatch, llm_stub_delay_jev_ms=50)
    )
    start = perf_counter()
    await gw.judge_pair(intent_a="a", intent_b="b", intent_ids=["i-1", "i-2"])
    assert (perf_counter() - start) * 1000 >= 50


def test_factory_rejects_unknown_mode(clock, monkeypatch):
    # M0では"stub"のみ。将来の"real"(T1確定後)もM0の時点では拒否する
    s = _clean_settings(monkeypatch, llm_mode="real")
    with pytest.raises(ValueError, match="llm_mode"):
        build_llm_gateway(clock, s)
    s = _clean_settings(monkeypatch, llm_mode="production")
    with pytest.raises(ValueError, match="llm_mode"):
        build_llm_gateway(clock, s)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_llm_factory.py -v`
Expected: FAIL(`AttributeError: 'Settings' object has no attribute 'llm_mode'` または `ImportError: cannot import name 'build_llm_gateway'`)

- [ ] **Step 3: settings.py へLLM設定4項目を追記(追記のみ)**

`backend/src/latch/settings.py` — `Settings` クラスの `log_level` 行の後に追記し、docstringコメントを更新:

```python
"""アプリ設定(design §2.8: コードが消費しない設定は作らない)。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LATCH_")

    app_env: str = "ci"  # ci / staging / prod(10 第1節の環境)
    log_level: str = "INFO"

    # --- LLM Gateway(ws-2。design §3.2)---
    llm_mode: str = "stub"  # T1確定後に "real" を追加(M0ではstubのみ)
    # 10 第1節レイテンシ注入(既定は無効)。p50/p95分布はM4でスタブ内で拡張
    llm_stub_delay_parser_ms: int = 0
    llm_stub_delay_embedding_ms: int = 0
    llm_stub_delay_jev_ms: int = 0
```

(app_env・log_level の行は既存のまま変更しない。LLM系4項目の追記のみ)

- [ ] **Step 4: gateway.py へ build_llm_gateway を追加**

`backend/src/latch/llm/gateway.py` のimport節に追記:

```python
from latch.settings import Settings
from latch.llm.stub import StubLLM
```

(ruffのisort順に並べ替えること。`latch.core.clock` → `latch.llm.errors` → `latch.llm.providers` → `latch.llm.records` → `latch.llm.stub` → `latch.settings` の順になる)

ファイル末尾(`LLMGateway` クラスの後)へ追加:

```python
def build_llm_gateway(clock: Clock, settings: Settings) -> LLMGateway:
    """設定からGatewayを構築する(design §2.7)。

    M0ではllm_mode="stub"のみ。T1確定後の実装追加で"real"を選択できるように
    なるが、その分岐はこの関数に閉じる(呼び出し側はGateway IFのみを知る)。
    """
    if settings.llm_mode != "stub":
        raise ValueError(
            f"unknown llm_mode: {settings.llm_mode!r} (M0では 'stub' のみ)"
        )
    stub = StubLLM(
        delay_parser_ms=settings.llm_stub_delay_parser_ms,
        delay_embedding_ms=settings.llm_stub_delay_embedding_ms,
        delay_jev_ms=settings.llm_stub_delay_jev_ms,
    )
    return LLMGateway(clock=clock, parser=stub, embedding=stub, jev=stub)
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_llm_factory.py -v`
Expected: PASS(6件)

- [ ] **Step 6: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/settings.py backend/src/latch/llm/gateway.py backend/tests/unit/llm/test_llm_factory.py
git commit -m "feat: LLM設定4項目とbuild_llm_gatewayファクトリ(設定→StubLLM)"
```

---

### Task 7: 公開IFの再export + 受渡し検証と報告ファイル

**Files:**
- Modify: `backend/src/latch/llm/__init__.py`(再export)、`backend/tests/unit/llm/test_llm_factory.py`(再export検証1件を追記)
- Create: `docs/plans/M0/ws-2-report.md`(§7の形式)

**Interfaces:**
- Consumes: 全タスク
- Produces: `latch.llm` 公開API(M1/M2はこれ越しにGatewayを利用する)。完了条件6項目の証拠と報告。スーパーバイザーがこれを見てG0の一部(C4立証)を判断する

- [ ] **Step 1: 失敗するテストを書く(test_llm_factory.py に追記)**

ファイル末尾へ追記:

```python
def test_public_api_reexports():
    import latch.llm as api

    for name in (
        "LLMGateway",
        "build_llm_gateway",
        "Timeouts",
        "TIMEOUT_PARSER_S",
        "TIMEOUT_EMBEDDING_S",
        "TIMEOUT_JEV_S",
        "LLMError",
        "LLMTimeoutError",
        "LLMProviderError",
        "ParserProvider",
        "EmbeddingProvider",
        "JevProvider",
        "EMBEDDING_DIMENSIONS",
        "StubLLM",
        "SendRecord",
        "send_log",
    ):
        assert getattr(api, name, None) is not None, name
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_llm_factory.py::test_public_api_reexports -v`
Expected: FAIL(`AssertionError: LLMGateway` — 再export前なので属性がない)

- [ ] **Step 3: `llm/__init__.py` を再exportへ書き換える**

`backend/src/latch/llm/__init__.py` の内容を丸ごと置き換える:

```python
"""LLM Gateway(M0 ws-2)。3系統集約・送信記録・プロバイダ抽象・スタブ。

M1/M2の呼び出し側はこのパッケージ越しにGatewayを利用する(design §3.1)。
"""

from latch.llm.errors import LLMError, LLMProviderError, LLMTimeoutError
from latch.llm.gateway import (
    TIMEOUT_EMBEDDING_S,
    TIMEOUT_JEV_S,
    TIMEOUT_PARSER_S,
    LLMGateway,
    Timeouts,
    build_llm_gateway,
)
from latch.llm.providers import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    JevProvider,
    ParserProvider,
)
from latch.llm.records import SendRecord, send_log
from latch.llm.stub import StubLLM

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "EmbeddingProvider",
    "JevProvider",
    "LLMError",
    "LLMGateway",
    "LLMProviderError",
    "LLMTimeoutError",
    "ParserProvider",
    "SendRecord",
    "StubLLM",
    "TIMEOUT_EMBEDDING_S",
    "TIMEOUT_JEV_S",
    "TIMEOUT_PARSER_S",
    "Timeouts",
    "build_llm_gateway",
    "send_log",
]
```

- [ ] **Step 4: テストが通ることを確認してCommit**

Run: `cd backend && uv run pytest tests/unit/llm -v`
Expected: PASS(全llm試験。test_llm_factory.py は7件)

```bash
make lint && make test
git add backend/src/latch/llm/__init__.py backend/tests/unit/llm/test_llm_factory.py
git commit -m "feat: latch.llm公開IFの再export(M1/M2呼び出し側の窓口)"
```

- [ ] **Step 5: 完了条件1 — lint / test**

```bash
make lint
make test
```

Expected: 2コマンドともexit 0。出力末尾のpytestサマリー行を記録する。

- [ ] **Step 6: 完了条件2・3 — llm試験全体(G0文言の証明)**

```bash
cd backend && uv run pytest tests/unit/llm/test_gateway_parse.py tests/unit/llm/test_gateway_embed.py tests/unit/llm/test_gateway_jev.py -v
uv run pytest tests/unit/llm -v
```

Expected: どちらもexit 0。1つ目で3系統のok/timeout/error記録試験が、2つ目で決定性・注入・許可リスト・ファクトリを含む全体がグリーン。

- [ ] **Step 7: 完了条件4 — 実時間参照の所在**

```bash
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
```

Expected: rgのヒットが `backend/src/latch/core/clock.py` の行のみ(SystemClockの `datetime.now(UTC)` 1行)。arch test はexit 0(llm/配下が自動スキャン対象に含まれていることの確認 — 雛形のarch testは `src/latch` 全体を走査するため新規ファイルは自動的に対象になる)。

- [ ] **Step 8: 完了条件5・6 — 差分の所在**

```bash
git diff --stat main -- backend/pyproject.toml backend/uv.lock
git diff --name-only main | sort
git diff main -- backend/src/latch/settings.py
git status --short
```

Expected: 1つ目は出力なし(依存追加なし)。2つ目は§4の一覧(llm/6ファイル・tests/unit/llm/6ファイル・settings.py・docs/plans/M0/ws-2-report.md)と完全一致。3つ目は追記のみ(既存行の変更・削除を含まない)。4つ目は空(未コミットの変更がないこと)。

- [ ] **Step 9: ruff format を通す**

```bash
cd backend && uv run ruff format . && uv run ruff check .
git status --short
```

変更があった場合のみ:

```bash
git add -u && git commit -m "style: ruff format 適用"
```

- [ ] **Step 10: 報告ファイルを作成してコミット**

`docs/plans/M0/ws-2-report.md` を §7 の形式で作成する。Step 5〜8の出力要点を貼る。固定値の変更有無を確認し記録する(変更が1つでもあれば「変更あり(前→後+理由)」と書く。全くなければ各行「変更なし」)。

```bash
git add docs/plans/M0/ws-2-report.md
git commit -m "docs: M0 ws-2の実行報告(完了条件6項目の証拠)"
```

- [ ] **Step 11: 最終返信**

報告ファイルのパスと完了条件6項目のPASS/FAIL一覧を返信する。FAILが1つでもあれば、それも隠さず返信する。

---

## 実行後のセルフレビュー(実装者がTask 7のStep 5に入る前に一度だけ読む)

- design.md §5の6項目がすべて§6(完了条件)に検証コマンドつきで対応しているか
- design.md §4の8項目(3系統記録・timeout・エラー・決定性・遅延・許可リスト・ファクトリ・arch)がすべて何れかのタスクの試験に対応しているか
- `LLMGateway(clock=, parser=, embedding=, jev=, timeouts=)`・`parse_intent(*, text, current_date, user_id)`・`embed_intent(*, text, intent_id)`・`judge_pair(*, intent_a, intent_b, intent_ids)`・`build_llm_gateway(clock, settings)`・`StubLLM(parser_response=, embedding_response=, jev_response=, delay_*_ms=, fail_*)` の各名前・引数がタスク間・テスト間で一致しているか
- `Timeouts(parser_s=, embedding_s=, jev_s=)` のキー名が `TIMEOUT_PARSER_S` 等の定数と対になっているか(単位混同なし)
- 製品コードに `datetime.now` / `time.sleep` / `from time import` が入っていないか(arch testが自動検出するが、入れた瞬間にレッドになることを自覚しておく)
- 依存を追加していないか(`pyproject.toml`・`uv.lock` が無差分)
- `settings.py`・`conftest.py`・`main.py`・`worker/`・`core/` が無変更(または追記のみ)か
- 実装中にdesign.mdの固定値を変えた箇所があれば報告ファイルに書いたか

