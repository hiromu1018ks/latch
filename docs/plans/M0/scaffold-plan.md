# M0雛形(scaffold)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依存制約C2「時刻参照はすべてClock経由」を最初に満たすバックエンド土台(uvプロジェクト・FastAPI最小構成・テスト基盤・compose常設ci環境)を作り、ws-1〜ws-4がすべて同一の構成の上で動くようにする。

**Architecture:** repo直下に `backend/`(uv・srcレイアウト・Python 3.13)を追加し、`compose.yaml`(ルート)で db(postgres:17+PostGIS+pgvector)/ redis / api / worker の4サービスを常設する。時刻は `core/clock.py` のClock ABC(SystemClock/FakeClock)に一本化し、APIは `app.state.clock` + `get_clock()` dependency、Workerは起動時の明示的構築で注入する。雛形単体ではDB接続・LLM・認証の実コードは持たない。

**Tech Stack:** Python 3.13 / uv 0.12.19 / FastAPI / pydantic-settings / uvicorn / pytest(+asyncio・httpx) / ruff / Docker Compose(postgres:17・PostGIS 3・pgvector・redis:8-alpine)

**Spec:** `docs/plans/M0/scaffold-design.md`(agent1設計メモ。本計画はこの文書の§5完了条件・§3構成を各タスクへ展開したもの)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeのブランチは `m0-scaffold` とする(既に切られていればそのまま使う)。
- **設計判断の固定値**: design.md §6の4論点は推奨で固定済みとして本計画に落としてある。**固定値(Python/PostgreSQL/Redis/uvのバージョン、composeの構成、DI方式)を変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Committers形式(`feat:` `test:` `chore:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする(12 第8節 運用ル則5の雛形時点での実体)。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由で実行する(システムPython 3.14と衝突させないため)。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| ci環境=最小構成(常設)。API 1・Worker 1・DB共用 | 10 第1節環境表 / 12 第3節 M0-1 |
| テスト環境は本番と同じ種類で規模縮小(PostgreSQL+PostGIS+pgvector / Redis / コンテナ) | 10 第1節冒頭 |
| Clock適用範囲6点・時刻操作で実時間の待ちを排除 | 04 第5節(FR-41)・10 第1節「時刻操作」 |
| リセットはジョブ方式(UTC基準TTLはJST 0時と9時間ずれるため不採用)・JST日付境界参照 | 04 第5節 |
| Clock最初に作ること=依存制約・テスト再現性はこれに依存 | 12 第2節 C2 |
| 認証不要エンドポイントはtoken/refreshのみ(**v1 API契約**の話。`/health`はv1配下に置かない) | 12 第2節 C3 / 05 第5節 |
| G0「時刻参照が全てClock経由であることをコード検査で確認」 | 12 第3節 M0・STATUS.md G0 |
| 毎コミットでci環境の機能試験を回し続ける | 12 第8節 運用ル則5 |
| prototype/は非接触・docsは実装基準(運用ル則6・7) | 00 運用ル則6・7 / 12 前提1.1 |
| リポジトリ構成・Clock IF・DI・DBイメージ・品質ツールの選定根拠 | design.md §2 / STATUS.md M0行「雛形」 |
| 完了条件6項目・テスト方針・ファイル構成 | design.md §3〜§5 |

## 2. グローバル制約(全タスクに暗黙に適用)

- Pythonは**3.13**(`backend/.python-version`)。システムの3.14は使わない(uvが3.13を自動導入する)。**変更時は報告欄へ記録**(design §6-4)
- uvは**0.12.19**(`.mise.toml`で固定。mise shim経由)。**変更時は報告欄へ記録**(design §6-4)
- 実行依存は**fastapi / uvicorn / pydantic-settings の3つのみ**。dev依存は**pytest / pytest-asyncio / httpx / ruff の4つのみ**。それ以外の追加(DBドライバ・httpクライアント・JWT等)は禁止(ws-1〜ws-3のスコープ。design §2.8)
- 製品コード(`backend/src/latch/`)で実時間を直接参照するAPI(`datetime.now` / `datetime.utcnow` / `date.today` / `time.time` / `time.monotonic` / `time.sleep` / `from time import`)の使用禁止。例外は `core/clock.py` のみ(C2)。`asyncio.sleep` は許容だが、debounce窓・バッチ周期の**境界計算はClock値から導く**こと(待機の許容と境界のClock経由は別物)
- PostgreSQLは**17**(`postgres:17` + `postgresql-17-postgis-3` + `postgresql-17-pgvector`)、Redisは**8**(`redis:8-alpine`)。**変更時は報告欄へ記録**(design §6-4)
- `GET /health` はv1配下に置かない(パスは `/health`)。応答は `{"status":"ok","server_time":"<ISO8601 UTC>"}`
- naive datetimeを扱わない。now()はtz-aware UTC、JST暦日付は `jst_date()` のみ
- コンテナ・composeのポートはすべて `127.0.0.1` にのみ公開(外部公開しない)

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **naive datetimeの混入** — `FakeClock(naive)` や `set(naive)` が黙って通るとJST変換で例外化ける → Task 3のconstructor/setのValueError試験が所有
2. **arch testの迂回import** — `from time import time` 形式は `"time.time"` トークン検査を抜ける → Task 6が `from time import` トークンも検出対象に含めて所有
3. **server_timeのタイムゾーン欠落** — `/health` がnaiveやローカルTZの文字列を返すとクライアント側解釈が割れる → Task 8の `fromisoformat` + tz-aware UTC 検証が所有
4. **Clock差し替えの片経路化** — `dependency_overrides` だけ効いて `create_app(clock=)` が効かない(逆も)と後続wsで差し替え不能になる → Task 8の両経路試験が所有
5. **WorkerのClock固定** — Workerがimport時刻やモジュールグローバルのClockを掴むとテストから制御できない → Task 9の注入試験(デフォルトSystemClock/注入FakeClockの両検証)が所有

## 4. スコープ(作成・変更するファイル一覧)

作成(design.md §3.1どおり):

```text
.mise.toml
compose.yaml
Makefile
docker/postgres/Dockerfile
backend/.python-version
backend/pyproject.toml
backend/uv.lock                    (uv sync の生成物。コミットする)
backend/README.md
backend/Dockerfile
backend/src/latch/__init__.py
backend/src/latch/main.py
backend/src/latch/settings.py
backend/src/latch/core/__init__.py
backend/src/latch/core/clock.py
backend/src/latch/core/deps.py
backend/src/latch/worker/__init__.py
backend/src/latch/worker/main.py
backend/src/latch/worker/__main__.py
backend/tests/conftest.py
backend/tests/unit/test_clock.py
backend/tests/unit/test_clock_reproducibility.py
backend/tests/unit/test_arch_no_direct_time.py
backend/tests/unit/test_app_health.py
backend/tests/unit/test_settings.py
backend/tests/unit/test_worker.py
backend/tests/integration/test_compose_ports.py
docs/plans/M0/scaffold-report.md   (報告ファイル。Task 12で作成)
```

変更:

- `.gitignore` — **追記のみ**(Python系のignore。既存のnode/env規則は変更しない)

生成されるがコミットしないもの: `backend/.venv/`(gitignore対象)。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- `docs/00〜12`・`docs/reviews/`(仕様書群。運用ル則7)
- `docs/plans/STATUS.md`(スーパーバイザー管理)
- `docs/plans/M0/scaffold-design.md`(入力設計メモ。読み取り専用)
- `prototype/` 全体(node_modules等も含め完全非接触。運用ル則6)
- `.claude/`(prompts・settings)
- ルート `README.md`(docs索引の役割を保つ。開発手順は `backend/README.md` へ)
- `.gitignore` は追記のみ(既存行の変更・削除禁止)
- スコープ外と判断する基準: DB接続コード・マイグレーション(ws-1)/ Gateway・httpクライアント(ws-2)/ JWT・Redisクライアント実コード(ws-3)/ 地物データ(ws-4)/ Pub/Sub emulator(M2)/ OTLP・staging(M4)/ GitHub Actions(remote無し)。これらが必要になったと感じても作らない — design.md §1.3に列挙された後続単位のスコープ
- mainブランチへのコミット・push・マージ

## 6. 完了条件(design.md §5の6項目。検証コマンドつき。Task 12で全て実行し報告ファイルに証拠を残す)

1. **クリーンな環境で `make setup` → `make lint` `make test` が成功**
   検証: `rm -rf backend/.venv && make setup && make lint && make test` — すべてexit 0
2. **`make up` 後、4サービスhealthy・`/health` がok**
   検証: `make up && docker compose ps` のSTATUS列が4サービスとも `healthy`(`running (healthy)` 等を含む)。`curl -s http://127.0.0.1:8000/health` が `"status":"ok"` を含む
3. **`make test-ci`(unit+integration)がグリーン**
   検証: `make test-ci` — exit 0、出力末尾に `passed`
4. **6点再現性試験とarch testが存在してグリーン(=C2立証)**
   検証: `cd backend && uv run pytest tests/unit/test_clock_reproducibility.py tests/unit/test_arch_no_direct_time.py -v` — exit 0
5. **実時間参照が `core/clock.py` のみ**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが `src/latch/core/clock.py` の行のみ
6. **禁止領域に差分なし**
   検証: `git diff --name-only main -- prototype README.md .claude 'docs/0*.md' 'docs/1*.md' docs/reviews docs/plans/STATUS.md` が空行なし(出力なし)。`.gitignore` の差分が追記のみであることも `git diff main -- .gitignore` で目視確認

## 7. 報告形式

**結果ファイル**: `docs/plans/M0/scaffold-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M0雛形(scaffold) 実行報告

- ブランチ: m0-scaffold / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make setup/lint/test | PASS/FAIL | <出力末尾を貼る> |
| 2 | 4サービスhealthy + /health ok | PASS/FAIL | <docker compose ps の表 + curl 出力> |
| 3 | make test-ci | PASS/FAIL | <pytest サマリー行> |
| 4 | 6点再現性+arch test | PASS/FAIL | <pytest サマリー行> |
| 5 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg 出力> |
| 6 | 禁止領域差分なし | PASS/FAIL | <git diff 出力(空なら「空」)> |

## 固定値の変更有無(design.md §6)
- ci環境=ローカルcompose常設: 変更なし / 変更あり(<理由>)
- DB共用=ci内API/Worker共有: 変更なし / 変更あり(<理由>)
- Python 3.13 / PostgreSQL 17 / Redis 8 / uv 0.12.19: 変更なし / 変更あり(<前→後+理由>)

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件6項目のPASS/FAIL一覧。

---

### Task 1: uvプロジェクト初期化と品質ツール設定

**Files:**
- Create: `.mise.toml`, `backend/.python-version`, `backend/pyproject.toml`, `backend/src/latch/__init__.py`, `backend/src/latch/core/__init__.py`, `backend/src/latch/worker/__init__.py`
- Modify: `.gitignore`(追記のみ)

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: uvプロジェクト(`uv run pytest` / `uv run ruff` が動く)。パッケージ `latch`(srcレイアウト)。pytest設定 `asyncio_mode="auto"`・marker `integration`・testpaths `tests`

- [ ] **Step 1: ツールバージョンを固定する**

`.mise.toml`(repoルート):

```toml
[tools]
uv = "0.12.19"
```

`backend/.python-version`:

```text
3.13
```

worktree内で `mise install` を実行し、`uv --version` が `0.12.19` を返すことを確認する(mise shimが `.mise.toml` を解決する)。

- [ ] **Step 2: pyproject.toml を作る**

`backend/pyproject.toml`:

```toml
[project]
name = "latch"
version = "0.1.0"
description = "LATCH backend (FastAPI)"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "pydantic-settings>=2.6",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "ruff>=0.7",
]

[tool.hatch.build.targets.wheel]
packages = ["src/latch"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "integration: compose常設環境(ci)への到達を確認する試験(make test-ciで実行)",
]

[tool.ruff]
target-version = "py313"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

パッケージディレクトリ(中身は後続タスクで作る):

```bash
mkdir -p backend/src/latch/core backend/src/latch/worker backend/tests/unit backend/tests/integration
printf '"""LATCH backend package."""\n' > backend/src/latch/__init__.py
printf '' > backend/src/latch/core/__init__.py
printf '' > backend/src/latch/worker/__init__.py
```

(tests配下に `__init__.py` は不要。conftestとユニークなテストファイル名で解決する)

- [ ] **Step 3: `uv sync` とツール動作確認(このタスクの「テスト」)**

```bash
cd backend && uv sync
uv run python -c "import sys, latch; assert sys.version_info[:2] == (3, 13), sys.version; print('py', sys.version_info[:2], 'latch ok')"
uv run pytest   # テストがまだ無いことの exit 5 (no tests ran) を確認
uv run ruff check .
```

期待: 1つ目のpythonコマンドが `(3, 13)` を表示。pytestは「no tests ran」でexit 5(正常・まだテスト無し)。ruffはexit 0。`uv.lock` と `backend/.venv/` が生成される。

- [ ] **Step 4: `.gitignore` に追記(追記のみ)**

`.gitignore` の末尾に:

```gitignore

# Python (backend)
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/
```

- [ ] **Step 5: Commit**

```bash
git add .mise.toml .gitignore backend/pyproject.toml backend/.python-version backend/uv.lock backend/src
git commit -m "chore: uvプロジェクトとruff/pytest設定の初期化(M0雛形 Task1)"
```

### Task 2: Clock ABC + SystemClock

**Files:**
- Create: `backend/src/latch/core/clock.py`
- Test: `backend/tests/unit/test_clock.py`

**Interfaces:**
- Consumes: なし
- Produces: `latch.core.clock.JST`(tzinfo)・`Clock`(ABC: `now() -> datetime` 抽象・`jst_date() -> date` 具象)・`SystemClock`。後続全タスクとws-1〜ws-4が利用する

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_clock.py`:

```python
"""Clock基本試験(design §4.1・§4.2)。"""

from datetime import datetime, timedelta, timezone

from latch.core.clock import JST, SystemClock


def test_system_clock_returns_tz_aware_utc():
    before = datetime.now(timezone.utc)
    got = SystemClock().now()
    after = datetime.now(timezone.utc)
    assert got.tzinfo is not None
    assert got.utcoffset() == timedelta(0)
    assert before <= got <= after


def test_jst_is_fixed_offset_plus9():
    assert JST.utcoffset(None) == timedelta(hours=9)
```

(テストコードでの `datetime.now` は正当。design §4.3「実時間の使用が正当なのは試験のみ」。arch testの対象は `src/` のみ)

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_clock.py -v`
Expected: FAIL( `ModuleNotFoundError: No module named 'latch.core.clock'` )

- [ ] **Step 3: 最小実装**

`backend/src/latch/core/clock.py`(このファイルは実時間参照を許される**唯一**の場所):

```python
"""C2: すべての時刻参照はこのモジュール経由で行う(12 第2節 C2・04 第5節)。

製品コードが実時間を直接参照することを禁止する規律の中心。
SystemClock以外の実装・テストコードはClockを経由して時刻を得る。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
import threading

# 日本に夏時間はないため、固定オフセット+9時間で確定させる。
JST = timezone(timedelta(hours=+9))


class Clock(ABC):
    """時刻参照の単一経路(C2)。now()はtz-aware UTC、JST暦日付はjst_date()。"""

    @abstractmethod
    def now(self) -> datetime:
        """tz-aware UTC の現在時刻を返す。naive datetime は返さない。"""

    def jst_date(self) -> date:
        """now() を JST 暦日付へ変換する(JST日付キー・リセットジョブ判定用)。"""
        return self.now().astimezone(JST).date()


class SystemClock(Clock):
    """本番用。実時間の現在時刻を返す(実時間参照が許される唯一の実装)。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_clock.py -v`
Expected: PASS(2件)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/core/clock.py backend/tests/unit/test_clock.py
git commit -m "feat: Clock ABCとSystemClock(C2・tz-aware UTC・JST固定オフセット)"
```

### Task 3: FakeClock(テスト用モック)

**Files:**
- Modify: `backend/src/latch/core/clock.py`(FakeClockを追加)
- Test: `backend/tests/unit/test_clock.py`(追記)

**Interfaces:**
- Consumes: Task 2の `Clock`
- Produces: `FakeClock(initial: datetime)` — `set(when)`(後退可)・`advance(delta)`・`now()`。10 第1節「時刻操作」の土台。conftestの `fake_clock` fixture(Task 8)と全再現性試験(Task 5)が利用

- [ ] **Step 1: 失敗するテストを書く(test_clock.py に追記)**

```python
from datetime import date  # ファイル先頭のimport節へ追記

import pytest  # 同上

from latch.core.clock import FakeClock  # 同上


@pytest.fixture
def fake() -> FakeClock:
    return FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc))


def test_fake_clock_now_returns_initial(fake):
    assert fake.now() == datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


def test_fake_clock_set_moves_both_directions(fake):
    fake.set(datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc))  # 後退
    assert fake.now().hour == 10
    fake.set(datetime(2026, 9, 28, 23, 59, 59, tzinfo=timezone.utc))  # 前進
    assert fake.now().day == 28


def test_fake_clock_advance(fake):
    fake.advance(timedelta(hours=2, seconds=1))
    assert fake.now() == datetime(2026, 9, 27, 14, 0, 1, tzinfo=timezone.utc)


def test_fake_clock_rejects_naive_initial():
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 9, 27, 12, 0, 0))


def test_fake_clock_set_rejects_naive(fake):
    with pytest.raises(ValueError):
        fake.set(datetime(2026, 9, 27, 12, 0, 0))


def test_fake_clock_thread_safe():
    import threading

    clock = FakeClock(datetime(2026, 9, 27, 0, 0, 0, tzinfo=timezone.utc))
    barrier = threading.Barrier(8)

    def tick():
        barrier.wait()
        for _ in range(100):
            clock.advance(timedelta(seconds=1))

    threads = [threading.Thread(target=tick) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 8スレッド×100回のadvanceが1つもロストしない(決定性)
    assert clock.now() == datetime(2026, 9, 27, 0, 13, 20, tzinfo=timezone.utc)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_clock.py -v`
Expected: 新規6件がFAIL( `ImportError: cannot import name 'FakeClock'` で採集エラー)

- [ ] **Step 3: FakeClockを実装(clock.py に追記)**

```python
class FakeClock(Clock):
    """テスト用。set()/advance() で決定的な時刻を再現する(10 第1節「時刻操作」)。"""

    def __init__(self, initial: datetime) -> None:
        if initial.tzinfo is None:
            raise ValueError("FakeClock は tz-aware な初期時刻を要求する")
        self._now = initial
        self._lock = threading.Lock()

    def set(self, when: datetime) -> None:
        """任意時刻へ移動する(後退も可。「過去不可」境界試験の両方向に使用)。"""
        if when.tzinfo is None:
            raise ValueError("FakeClock.set は tz-aware な時刻を要求する")
        with self._lock:
            self._now = when

    def advance(self, delta: timedelta) -> None:
        """時刻を delta だけ前進させる(期限・debounce・バッチ周期の再現に使用)。"""
        with self._lock:
            self._now += delta

    def now(self) -> datetime:
        with self._lock:
            return self._now
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_clock.py -v`
Expected: PASS(8件)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/core/clock.py backend/tests/unit/test_clock.py
git commit -m "feat: FakeClock(set/advance・決定的・スレッド安全)"
```

### Task 4: jst_date(JST日付境界)

**Files:**
- Test: `backend/tests/unit/test_clock.py`(追記。実装はTask 2の `Clock.jst_date()` に既にあるため、このタスクは境界の立証が主)

**Interfaces:**
- Consumes: Task 2/3の `Clock.jst_date()` / `FakeClock`
- Produces: JST境界試験(TTL不採用の根拠となった9時間ずれのピン留め)。Task 5の(5)再現性試験の基礎

- [ ] **Step 1: 失敗する(または誤値で落ちる)テストを書く(test_clock.py に追記)**

```python
def test_jst_date_boundary_at_midnight():
    clock = FakeClock(datetime(2026, 9, 30, 14, 59, 59, tzinfo=timezone.utc))
    assert clock.jst_date() == date(2026, 9, 30)  # JST 23:59:59
    clock.advance(timedelta(seconds=1))
    assert clock.jst_date() == date(2026, 10, 1)  # JST 10-01 0:00(=UTC 09-30 15:00)


def test_jst_date_month_start_is_not_utc_month_start():
    # UTCの暦日付はまだ9月のまま、JST日付は10月 — TTL方式不採用の根拠となったずれ
    clock = FakeClock(datetime(2026, 9, 30, 15, 0, 0, tzinfo=timezone.utc))
    assert clock.now().date() == date(2026, 9, 30)
    assert clock.jst_date() == date(2026, 10, 1)
```

- [ ] **Step 2: テストを実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_clock.py -v`
Expected: PASS(10件)。`jst_date()` はTask 2で実装済みのため赤→赤でなくてもよいが、**JST計算を誤っていればここで落ちる**。仮に落ちた場合は `Clock.jst_date()` の `astimezone(JST)` 実装を修正する(修正はこのタスクの範囲)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_clock.py
git commit -m "test: JST日付境界(0時・月初・UTCとの9時間ずれ)の立証"
```

### Task 5: 適用範囲6点の再現性証明(C2立証の本体)

**Files:**
- Test: `backend/tests/unit/test_clock_reproducibility.py`(新規。実装コードは不要 — FakeClockのprimitivesの組合せだけで証明する)

**Interfaces:**
- Consumes: Task 3の `FakeClock`
- Produces: 「6点がすべてClock操作で再現できる」ことの証明(12 M0スコープ3・G0の一部)。完了条件4の対象ファイル

- [ ] **Step 1: 試験を書く(表駆動・design §4.2の表をそのままコード化)**

`backend/tests/unit/test_clock_reproducibility.py`:

```python
"""Clock適用範囲6点(04 第5節・10 第1節)の再現性証明(design §4.2)。

ドメイン実装がまだ無いM0時点では、各点の判定形をテスト内ヘルパーとして模倣し、
FakeClock の set/advance のみで判定結果を制御できること(=実時間の待ちを排除できる
こと)を立証する。ヘルパー内の時刻取得はすべて引数の clock から行う。
"""

from datetime import date, datetime, timedelta, timezone

from latch.core.clock import Clock, FakeClock

UTC = timezone.utc


def _deadline_expired(deadline: datetime, clock: Clock) -> bool:
    """(1) API期限判定の判定形。DBのclock_timestamp()は使わない。"""
    return clock.now() >= deadline


def _next_run_due(last_run: datetime, clock: Clock) -> bool:
    """(2) 期限バッチ(expiry_sweeper)60秒周期の到来判定形。"""
    return clock.now() >= last_run + timedelta(seconds=60)


def _bucket_start(now: datetime) -> datetime:
    """(3) 30分Bucket境界。nowから30分区切り(切捨て)を導く。定義の詳細は06(M2で確定)。"""
    minute = (now.minute // 30) * 30
    return now.replace(minute=minute, second=0, microsecond=0)


def _in_debounce_window(last_event_at: datetime, clock: Clock) -> bool:
    """(4) debounce 10秒窓の判定形。窓の境界計算はClock値から導く。"""
    return clock.now() - last_event_at <= timedelta(seconds=10)


def _start_at_valid(start_at: datetime, clock: Clock) -> bool:
    """(6) 作成・更新の時刻検証(過去不可・上限7日)の判定形(05 第5節)。"""
    now = clock.now()
    return now <= start_at <= now + timedelta(days=7)


def test_1_api_deadline_judgement():
    clock = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    deadline = clock.now() + timedelta(hours=2)
    assert not _deadline_expired(deadline, clock)  # 期限前
    clock.advance(timedelta(hours=2, seconds=1))   # Δ+ε 進める(実時間の待ちなし)
    assert _deadline_expired(deadline, clock)      # 期限切れ


def test_2_expiry_sweeper_schedule():
    clock = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    last_run = clock.now()
    clock.advance(timedelta(seconds=59))
    assert not _next_run_due(last_run, clock)      # 未到来
    clock.advance(timedelta(seconds=2))            # 計61秒 = 60s+ε
    assert _next_run_due(last_run, clock)          # 到来


def test_3_thirty_min_bucket_boundary():
    clock = FakeClock(datetime(2026, 9, 27, 12, 29, 59, tzinfo=UTC))
    assert _bucket_start(clock.now()) == datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    clock.advance(timedelta(seconds=1))            # 境界直後
    assert _bucket_start(clock.now()) == datetime(2026, 9, 27, 12, 30, tzinfo=UTC)


def test_4_debounce_ten_second_window():
    clock = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    last_event_at = clock.now()
    clock.advance(timedelta(seconds=9))
    assert _in_debounce_window(last_event_at, clock)   # 窓内
    clock.advance(timedelta(seconds=2))                # 計11秒
    assert not _in_debounce_window(last_event_at, clock)  # 窓外


def test_5_jst_reset_boundary():
    clock = FakeClock(datetime(2026, 9, 30, 14, 59, 59, tzinfo=UTC))
    assert clock.jst_date() == date(2026, 9, 30)   # JST 23:59:59 = まだ9/30
    clock.advance(timedelta(seconds=1))            # JST 10-01 0:00
    assert clock.jst_date() == date(2026, 10, 1)   # リセットジョブの起動境界


def test_6_start_at_validation_flips_with_set():
    clock = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    start_at = datetime(2026, 9, 27, 13, 0, 0, tzinfo=UTC)
    assert _start_at_valid(start_at, clock)        # 未来 → 受理
    clock.set(datetime(2026, 9, 27, 14, 0, 0, tzinfo=UTC))
    assert not _start_at_valid(start_at, clock)    # 過去に反転 → 拒否


def test_6_start_at_validation_seven_day_upper_bound():
    clock = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    assert _start_at_valid(clock.now() + timedelta(days=7), clock)          # 上限内
    assert not _start_at_valid(clock.now() + timedelta(days=7, seconds=1), clock)  # 超過
```

- [ ] **Step 2: テストを実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_clock_reproducibility.py -v`
Expected: PASS(7件)。落ちた場合はヘルパーかFakeClockのどこかに誤りがあるので、`systematic-debugging` に従って原因を特定してから直す(implementationを摩り替えて緑にしない)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_clock_reproducibility.py
git commit -m "test: Clock適用範囲6点の再現性証明(期限/バッチ/Bucket/debounce/JSTリセット/時刻検証)"
```

### Task 6: C2強制のarch test(G0「コード検査」の自動化)

**Files:**
- Test: `backend/tests/unit/test_arch_no_direct_time.py`(新規)

**Interfaces:**
- Consumes: Task 1〜5で作った `backend/src/latch/`(走査対象)
- Produces: 製品コードでの実時間直接参照の永久禁止機構(毎コミットで自動実行)。完了条件4・5の対象。allowlist(将来のアダプタ層で `time.sleep` 等を限定的に許可する際に明示的に追加する場所)

- [ ] **Step 1: 試験を書く**

`backend/tests/unit/test_arch_no_direct_time.py`:

```python
"""C2強制のarch test(12 M0・G0「コード検査」の毎コミット自動化。design §4.3)。

backend/src/latch/ 配下の製品コードで実時間への直接参照を禁止する。
唯一の例外は core/clock.py(SystemClock の実装場所)。
tests/ はスキャン対象外(実時間の使用が正当なのは試験のみ)。
"""

from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "latch"

# datetime.now / datetime.utcnow / date.today は「from datetime import datetime」
# 経由の呼び出しでもトークンにヒットする。"from time import" は
# 「from time import time」等の迂回importを検出するためのトークン。
FORBIDDEN_TOKENS = (
    "datetime.now",
    "datetime.utcnow",
    "date.today",
    "time.time",
    "time.monotonic",
    "time.sleep",
    "from time import",
)

# 実時間参照を許される唯一の場所。将来アダプタ層で待機等を限定的に許可する場合は
# ここに明示的に追加する(追加は報告ファイルに記録する)。
ALLOWED = {SRC / "core" / "clock.py"}


def test_no_direct_time_reference_in_product_code():
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(SRC)}: {token}")
    assert not offenders, (
        "実時間の直接参照は禁止(C2)。Clock(latch.core.clock)経由に修正すること: "
        + ", ".join(offenders)
    )
```

- [ ] **Step 2: 検出力の確認(赤を演出してから戻す。Review Focus #2の迂回importもここで証明する)**

```bash
cd backend
uv run pytest tests/unit/test_arch_no_direct_time.py -v   # 1) 現状グリーンを確認
printf 'import time\nprint(time.time())\n' > src/latch/_arch_probe.py
uv run pytest tests/unit/test_arch_no_direct_time.py -v   # 2) FAILすることを確認
printf 'from time import monotonic\nprint(monotonic())\n' > src/latch/_arch_probe.py
uv run pytest tests/unit/test_arch_no_direct_time.py -v   # 3) 迂回importでもFAILすることを確認
rm src/latch/_arch_probe.py
uv run pytest tests/unit/test_arch_no_direct_time.py -v   # 4) 再びグリーン
```

Expected: 1) PASS → 2) FAIL( `_arch_probe.py: time.time` を含むメッセージ)→ 3) FAIL( `_arch_probe.py: from time import` を含むメッセージ)→ 4) PASS。2)か3)でFAILしなければ検査に穴がある — FORBIDDEN_TOKENS のトークン照合を修正すること

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_arch_no_direct_time.py
git commit -m "test: 実時間直接参照を禁止するarch test(C2・G0コード検査の自動化)"
```

### Task 7: settings(pydantic-settings)

**Files:**
- Create: `backend/src/latch/settings.py`
- Test: `backend/tests/unit/test_settings.py`

**Interfaces:**
- Consumes: なし
- Produces: `Settings`(env prefix `LATCH_`・`app_env: str = "ci"`・`log_level: str = "INFO"`)。Task 8/9が利用。**それ以外の設定項目は追加しない**(design §2.8 — コードが消費しない設定は作らない)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_settings.py`:

```python
"""Settingsのデフォルトと環境変数上書き(design §4.4)。"""

from latch.settings import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("LATCH_APP_ENV", raising=False)
    monkeypatch.delenv("LATCH_LOG_LEVEL", raising=False)
    s = Settings()
    assert s.app_env == "ci"
    assert s.log_level == "INFO"


def test_settings_env_override_with_latch_prefix(monkeypatch):
    monkeypatch.setenv("LATCH_APP_ENV", "staging")
    monkeypatch.setenv("LATCH_LOG_LEVEL", "DEBUG")
    s = Settings()
    assert s.app_env == "staging"
    assert s.log_level == "DEBUG"


def test_settings_non_prefixed_env_is_ignored(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("LATCH_APP_ENV", raising=False)
    s = Settings()
    assert s.app_env == "ci"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_settings.py -v`
Expected: FAIL( `ModuleNotFoundError: No module named 'latch.settings'` )

- [ ] **Step 3: 最小実装**

`backend/src/latch/settings.py`:

```python
"""アプリ設定(design §2.8: コードが消費しない設定は作らない)。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LATCH_")

    app_env: str = "ci"      # ci / staging / prod(10 第1節の環境)
    log_level: str = "INFO"
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_settings.py -v`
Expected: PASS(3件)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/settings.py backend/tests/unit/test_settings.py
git commit -m "feat: Settings(app_env/log_levelのみ・LATCH_プレフィックス)"
```

### Task 8: create_app ファクトリ + /health + get_clock dependency

**Files:**
- Create: `backend/src/latch/main.py`, `backend/src/latch/core/deps.py`, `backend/tests/conftest.py`
- Test: `backend/tests/unit/test_app_health.py`

**Interfaces:**
- Consumes: Task 2/3のClock群・Task 7のSettings
- Produces: `create_app(clock: Clock | None = None, settings: Settings | None = None) -> FastAPI`(`app.state.clock` / `app.state.settings` を設定)・`get_clock(request: Request) -> Clock`・`GET /health`(v1配下でない)・モジュール変数 `app`(uvicorn用)・conftestの `fake_clock` / `app` / `client` fixture(後続タスクとws単位が利用)

- [ ] **Step 1: conftest.py を作る(フィクスチャ)**

`backend/tests/conftest.py`:

```python
"""共通フィクスチャ(design §3.1)。"""

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from latch.core.clock import FakeClock
from latch.main import create_app


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc))


@pytest.fixture
def app(fake_clock):
    return create_app(clock=fake_clock)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

- [ ] **Step 2: 失敗するテストを書く**

`backend/tests/unit/test_app_health.py`:

```python
"""/health と Clock差し替えの両経路(design §4.4)。/health はv1配下に置かない。"""

from datetime import datetime, timezone

from httpx import ASGITransport, AsyncClient

from latch.core.clock import FakeClock
from latch.core.deps import get_clock
from latch.main import create_app


async def _get_body(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    return resp.json()


async def test_health_returns_ok_and_clock_time(app, fake_clock):
    body = await _get_body(app)
    assert body["status"] == "ok"
    assert body["server_time"] == fake_clock.now().isoformat()


async def test_health_server_time_is_iso8601_utc(app):
    body = await _get_body(app)
    parsed = datetime.fromisoformat(body["server_time"])  # ISO8601として妥当
    assert parsed.tzinfo is not None                      # naiveでない
    assert parsed.utcoffset().total_seconds() == 0        # UTC


async def test_health_create_app_injection():
    clock = FakeClock(datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc))
    app = create_app(clock=clock)
    body = await _get_body(app)
    assert body["server_time"] == clock.now().isoformat()


async def test_health_dependency_override(app):
    other = FakeClock(datetime(2030, 6, 15, 3, 30, 0, tzinfo=timezone.utc))
    app.dependency_overrides[get_clock] = lambda: other
    try:
        body = await _get_body(app)
    finally:
        app.dependency_overrides.clear()
    assert body["server_time"] == other.now().isoformat()


async def test_health_not_under_v1(app, client):
    resp = await client.get("/v1/health")
    assert resp.status_code == 404  # v1契約の外(C3の例外とはしない)
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_app_health.py -v`
Expected: FAIL( `ModuleNotFoundError: No module named 'latch.main'` でconftestのimportから採集エラー)

- [ ] **Step 4: 最小実装**

`backend/src/latch/core/deps.py`:

```python
"""FastAPI依存: app.state.clock へのアクセスを一元化(design §2.4 DI方式)。"""

from fastapi import Request

from latch.core.clock import Clock


def get_clock(request: Request) -> Clock:
    return request.app.state.clock
```

`backend/src/latch/main.py`:

```python
"""FastAPIアプリケーションファクトリ(design §3.1)。

/health は運用プローブ用でありv1 API契約の外に置く(C3の対象外)。
server_time は get_clock() 由来 — Clock差し替えが全経路で効くことの生の消費者。
"""

from fastapi import Depends, FastAPI

from latch.core.clock import Clock, SystemClock
from latch.core.deps import get_clock
from latch.settings import Settings


def create_app(clock: Clock | None = None, settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="LATCH API")
    app.state.clock = clock if clock is not None else SystemClock()
    app.state.settings = settings if settings is not None else Settings()

    @app.get("/health")
    async def health(
        clock: Clock = Depends(get_clock),
    ) -> dict[str, str]:
        return {"status": "ok", "server_time": clock.now().isoformat()}

    return app


# composeのapiサービスが参照するエントリポイント(uvicorn latch.main:app)
app = create_app()
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit -v`
Expected: PASS(全unit試験 — test_app_health.py の5件を含む)

- [ ] **Step 6: Commit**

```bash
git add backend/src/latch/main.py backend/src/latch/core/deps.py backend/tests/conftest.py backend/tests/unit/test_app_health.py
git commit -m "feat: create_app ファクトリと /health(Clock差し替え両経路)"
```

### Task 9: Worker起動本体

**Files:**
- Create: `backend/src/latch/worker/main.py`, `backend/src/latch/worker/__main__.py`
- Test: `backend/tests/unit/test_worker.py`

**Interfaces:**
- Consumes: Task 2のSystemClock・Task 3のFakeClock・Task 7のSettings
- Produces: `Worker(clock=None, settings=None)`(`request_shutdown()` / `await run()`)と `python -m latch.worker` 入口。composeのworkerサービス(design §3.1)とM2以降のWorker拡張が利用。**起動時の1回の明示的構築**がDI方式(design §2.4)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker.py`:

```python
"""Worker起動経路(FakeClock注入・即時shutdown。design §4.4)。"""

import asyncio

from latch.core.clock import FakeClock, SystemClock
from latch.worker.main import Worker


async def test_worker_accepts_injected_clock_and_stops_immediately(fake_clock):
    worker = Worker(clock=fake_clock)
    assert worker.clock is fake_clock          # 注入したClockをそのまま使う
    worker.request_shutdown()                  # shutdownイベントを先にセット
    await asyncio.wait_for(worker.run(), timeout=1.0)  # 即座にgraceful終了


async def test_worker_defaults_to_system_clock():
    worker = Worker()
    assert isinstance(worker.clock, SystemClock)
    worker.request_shutdown()
    await asyncio.wait_for(worker.run(), timeout=1.0)


async def test_worker_run_returns_after_shutdown_request():
    worker = Worker()
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0)                     # run() が待機に入ることを許す(asyncio.sleepは待機であり時刻参照ではない)
    assert not task.done()
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=1.0)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker.py -v`
Expected: FAIL( `ModuleNotFoundError: No module named 'latch.worker.main'` )

- [ ] **Step 3: 最小実装**

`backend/src/latch/worker/main.py`:

```python
"""Worker本体(design §3.1)。ci環境では api と同じイメージ・別プロセス(Worker 1)。

実処理(Embedding・Layer 1〜5等)はM2以降が追加する。雛形では起動・graceful
shutdown と Clock/Settings の明示的構築のみを担保する。
"""

from __future__ import annotations

import asyncio
import logging

from latch.core.clock import Clock, SystemClock
from latch.settings import Settings

logger = logging.getLogger(__name__)


class Worker:
    """継続実行の土台。Clock は起動時の1回の明示的構築(design §2.4 DI方式)。"""

    def __init__(self, clock: Clock | None = None, settings: Settings | None = None) -> None:
        self.clock: Clock = clock if clock is not None else SystemClock()
        self.settings: Settings = settings if settings is not None else Settings()
        self._stop = asyncio.Event()

    def request_shutdown(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        logger.info("worker started (app_env=%s)", self.settings.app_env)
        await self._stop.wait()
        logger.info("worker stopped")
```

`backend/src/latch/worker/__main__.py`:

```python
"""`python -m latch.worker` の入口(composeのworkerサービスが使用)。"""

from __future__ import annotations

import asyncio
import logging
import signal

from latch.settings import Settings
from latch.worker.main import Worker


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker = Worker(settings=settings)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, worker.request_shutdown)
        await worker.run()

    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker.py -v`
Expected: PASS(3件)

- [ ] **Step 5: Commit**

```bash
git add backend/src/latch/worker/main.py backend/src/latch/worker/__main__.py backend/tests/unit/test_worker.py
git commit -m "feat: Worker本体と python -m latch.worker 入口(graceful shutdown)"
```

### Task 10: ci環境(compose)とルート資産

**Files:**
- Create: `docker/postgres/Dockerfile`, `backend/Dockerfile`, `compose.yaml`, `Makefile`, `backend/README.md`

**Interfaces:**
- Consumes: Task 8の `latch.main:app`・Task 9の `python -m latch.worker`
- Produces: 4サービス常設ci環境(`make up`)と `make setup/up/down/ps/logs/lint/test/test-ci`。Task 11・12とws-1〜ws-4が利用

- [ ] **Step 1: DBイメージ(postgres:17 + PostGIS 3 + pgvector。拡張の同梱まで。CREATE EXTENSIONはws-1)**

`docker/postgres/Dockerfile`:

```dockerfile
# design §2.5: 公式postgres(Debian/PGDG)に拡張2つをaptで足す。
# CREATE EXTENSION はws-1のマイグレーションが行う(雛形は同梱まで)。
FROM postgres:17

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-17-postgis-3 \
        postgresql-17-pgvector \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: backendイメージ(api/worker共用・非root)**

`backend/Dockerfile`:

```dockerfile
# api/worker 共用イメージ(design §3.1)。実依存のみ(--no-dev)をfrozenで導入。
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /usr/local/bin/

WORKDIR /app

# 依存レイヤー(srcの変更で再インストールされないよう分離)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 latch
USER latch

CMD ["uvicorn", "latch.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: compose.yaml(4サービス・常設・healthcheck。10 第1節どおり)**

`compose.yaml`(repoルート):

```yaml
# ci環境(10 第1節): 最小構成・常設。API 1インスタンス / Worker 1 / DB共用。
# ポートはすべて127.0.0.1のみに公開。DB接続先は将来の分離に備え環境変数で与える。
name: latch-ci

services:
  db:
    build:
      context: docker/postgres
    environment:
      POSTGRES_USER: ${LATCH_DB_USER:-latch}
      POSTGRES_PASSWORD: ${LATCH_DB_PASSWORD:-latch}
      POSTGRES_DB: ${LATCH_DB_NAME:-latch}
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - latch-ci-db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 3s
      retries: 12
    restart: unless-stopped

  redis:
    image: redis:8-alpine
    ports:
      - "127.0.0.1:6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 12
    restart: unless-stopped

  api:
    build:
      context: backend
    # 単一プロセス = API 1インスタンス(10 第1節)
    command: uvicorn latch.main:app --host 0.0.0.0 --port 8000
    ports:
      - "127.0.0.1:8000:8000"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')\""]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 10s
    restart: unless-stopped

  worker:
    build:
      context: backend
    # Worker 1(10 第1節)
    command: python -m latch.worker
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

volumes:
  latch-ci-db:
```

- [ ] **Step 4: Makefile(規律の入口。design §2.7)**

`Makefile`(repoルート。タブインデントであることに注意 — 以下の字下げはすべてタブで書く):

```makefile
.PHONY: setup up down ps logs lint test test-ci

setup: ## uv依存の導入
	cd backend && uv sync

up: ## ci常設環境を起動(healthy待ち)
	docker compose up -d --wait

down: ## ci常設環境を停止(ボリューム保持)
	docker compose down

ps: ## ci常設環境の状態
	docker compose ps

logs: ## ci常設環境のログ(follow)
	docker compose logs -f

lint: ## ruff(format検査+lint)
	cd backend && uv run ruff format --check . && uv run ruff check .

test: ## unit試験(毎コミットの規律)
	cd backend && uv run pytest -m "not integration"

test-ci: ## ci環境試験(compose起動 → unit+integration)
	docker compose up -d --wait
	cd backend && uv run pytest
```

- [ ] **Step 5: backend/README.md**

`backend/README.md` の内容:

````markdown
# LATCH backend

FastAPIバックエンド(uv・srcレイアウト・Python 3.13)。仕様は `../docs/` を参照。

## クイックスタート

```bash
mise install            # .mise.toml の uv を導入(初回のみ)
make setup              # uv sync(backend/.venv 構築)
make lint && make test  # 毎コミットの規律
make up && make ps      # ci常設環境(db/redis/api/worker)
make test-ci            # unit+integration
```

## 規律

- 製品コード(`src/latch/`)で実時間を直接参照しない — すべて `latch.core.clock` 経由(C2)。
  `tests/unit/test_arch_no_direct_time.py` が毎コミットで強制する
- `python`/`pip` を直接使わない。`uv run` 経由
- 新しい依存は計画書のスコープ確認を経て追加する(現状: fastapi/uvicorn/pydantic-settings のみ)
````

- [ ] **Step 6: 動作確認(このタスクの「テスト」)**

```bash
make lint && make test          # unitがグリーン
make up                         # イメージビルド + 4サービス起動(初回は数分)
docker compose ps               # 4サービスが healthy になるまで待つ
curl -s http://127.0.0.1:8000/health   # {"status":"ok",...}
docker compose logs worker --tail 5    # worker started のログ
```

Expected: `ps` のSTATUS列が4サービスとも `(healthy)`。curl応答に `"status":"ok"`。失敗した場合: `docker compose logs api` / `logs worker` / `logs db` で原因を確認(大半はビルドかhealthcheck)。**ポート5432/6379/8000が開発機で既に使用中の場合は報告ファイルに記録し、`compose.yaml` のホスト側ポートのみ変更する(コンテナ側とサービス間接続は変えない。変更は報告欄へ)**

- [ ] **Step 7: Commit**

```bash
git add docker/postgres/Dockerfile backend/Dockerfile compose.yaml Makefile backend/README.md
git commit -m "feat: ci常設環境(compose 4サービス)とMakeターゲット・README"
```

### Task 11: integration試験と make test-ci

**Files:**
- Test: `backend/tests/integration/test_compose_ports.py`(新規)

**Interfaces:**
- Consumes: Task 10のcompose常設環境
- Produces: `make test-ci`(unit+integration)の完了する経路。実際のSQL試験はws-1がドライバ導入後に追加する(design §4.1)

- [ ] **Step 1: 試験を書く**

`backend/tests/integration/test_compose_ports.py`:

```python
"""ci常設環境(compose)への到達確認(design §4.1 integration)。

make test-ci(compose up --wait 後に pytest)から実行する。
雛形ではTCP到達と /health 応答のみ(実際のSQL試験はws-1)。
"""

import socket

import httpx
import pytest

pytestmark = pytest.mark.integration

REACHABLE = [
    ("127.0.0.1", 5432, "db"),
    ("127.0.0.1", 6379, "redis"),
]


@pytest.mark.parametrize(("host", "port", "name"), REACHABLE)
def test_reach_service(host, port, name):
    with socket.create_connection((host, port), timeout=3.0):
        pass  # 接続成立で十分(TCP到達の証明)


async def test_api_health_on_ci():
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get("http://127.0.0.1:8000/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

- [ ] **Step 2: unitでは実行されないことを確認**

```bash
cd backend && uv run pytest -m "not integration" -v --collect-only | rg test_compose_ports || echo "not collected (ok)"
```

Expected: `not collected (ok)`(unit実行ではスキップされる)

- [ ] **Step 3: `make test-ci` がグリーンになることを確認**

Run: `make test-ci`
Expected: exit 0。pytestサマリーに `passed`(unit+integration合計。deselectされたintegrationは `-m "not integration"` 実行時のみ)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_compose_ports.py
git commit -m "test: ci常設環境へのintegration到達試験(db/redis/api)"
```

### Task 12: 受渡し検証と報告ファイル

**Files:**
- Create: `docs/plans/M0/scaffold-report.md`(§7の形式)

**Interfaces:**
- Consumes: 全タスク
- Produces: 完了条件6項目の証拠と報告。スーパーバイザーがこれを見てG0の一部(C2立証)を判断する

- [ ] **Step 1: 完了条件1 — クリーン環境での setup/lint/test**

```bash
rm -rf backend/.venv
make setup
make lint
make test
```

Expected: 3コマンドすべてexit 0。出力末尾を記録する。

- [ ] **Step 2: 完了条件2 — 4サービスhealthyと /health**

```bash
make up
docker compose ps
curl -s http://127.0.0.1:8000/health
```

Expected: `ps` の表にdb/redis/api/workerの4行、STATUSがすべてhealthyを含む。curl応答に `"status":"ok"`。

- [ ] **Step 3: 完了条件3・4 — test-ci と C2立証試験**

```bash
make test-ci
cd backend && uv run pytest tests/unit/test_clock_reproducibility.py tests/unit/test_arch_no_direct_time.py -v
```

Expected: 両方exit 0。

- [ ] **Step 4: 完了条件5 — 実時間参照の所在**

```bash
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src
```

Expected: ヒットが `backend/src/latch/core/clock.py` の行のみ(SystemClockの `datetime.now(timezone.utc)` 1行)。

- [ ] **Step 5: 完了条件6 — 禁止領域の差分なし**

```bash
git diff --name-only main -- prototype README.md .claude 'docs/0*.md' 'docs/1*.md' docs/reviews docs/plans/STATUS.md
git diff main -- .gitignore
git status --short
```

Expected: 1つ目は出力なし。2つ目は追記のみ(既存行の変更・削除を含まない)。3つ目は出力が空(worktreeに未コミットの変更がないこと。build成果物等が出る場合は `.gitignore` の追記漏れなので報告ファイルに記録して対応する)。

- [ ] **Step 6: ruff format を通す**

```bash
cd backend && uv run ruff format . && uv run ruff check .
git status --short            # formatによる変更の有無を確認
```

変更があった場合のみ:

```bash
git add -u && git commit -m "style: ruff format 適用"
```

- [ ] **Step 7: 報告ファイルを作成してコミット**

`docs/plans/M0/scaffold-report.md` を §7 の形式で作成する。各Step 1〜5の出力要点を貼る。固定値の変更有無を確認し記録する(変更が1つでもあれば「変更あり(前→後+理由)」と書く。全くなければ各行「変更なし」)。

```bash
git add docs/plans/M0/scaffold-report.md
git commit -m "docs: M0雛形の実行報告(完了条件6項目の証拠)"
```

- [ ] **Step 8: 最終返信**

報告ファイルのパスと完了条件6項目のPASS/FAIL一覧を返信する。FAILが1つでもあれば、それも隠さず返信する。

---

## 実行後のセルフレビュー(実装者がTask 12の前に一度だけ読む)

- design.md §5の6項目がすべて§6(完了条件)に検証コマンドつきで対応しているか
- `latch.main:app`・`python -m latch.worker`・`get_clock`・`create_app(clock=...)` の各名前がcompose・テスト・実装で一致しているか
- 追加した依存が fastapi/uvicorn/pydantic-settings(+dev 4種)に収まっているか
- 実装中にdesign.mdの固定値を変えた箇所があれば報告ファイルに書いたか
