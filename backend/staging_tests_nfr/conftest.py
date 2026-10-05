"""ws-7 NFRハーネス共通(M4 ws-7・ws-3ハーネス型の踏襲)。

tests/配下でもstaging_tests/でもない独立ディレクトリ(pyproject testpaths外)
のため既存のmake test/test-ciからは収集されない。実行: make staging-nfr-test
(順序固定 n5→n4→n3→n2→n1・部分実行はNFR_FILES)。実行前提はmake
staging-nfr-resetからのフル起動(compose.staging.nfr.yaml併用=lag alert有効)。
証拠YAMLは本conftestのsessionfinishのみが出力する(10 §5形式・実行ごとに
ws7-nfr-result-<ts>.yaml)。session状態はモジュール辞書+ヘルパー関数で持つ
(sessionスコープのasync fixtureを使わない — pytest-asyncioのloop scope結合
を避ける・ws-3と同一作法)。staging_testsのhelpersはimportしない(独立完結
のためobs側のsys.pathハックは不要)。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import asyncpg
import httpx
import pytest
import yaml

from latch.auth.tools import issue_idp_token
from latch.core.clock import ControlledClock

REPO_ROOT = (
    Path(__file__).resolve().parents[2]
)  # backend/staging_tests_nfr → repoルート
API_BASE = "http://127.0.0.1:18000"  # nginx入口
DSN = "postgresql://latch:latch@127.0.0.1:15432/latch"  # 既定DB(SELECT専用)
RECOVERY_DSN = "postgresql://latch:latch@127.0.0.1:16543/latch"  # db2(RW可)
REDIS_URL = "redis://127.0.0.1:16379/0"
KEYS_DIR = REPO_ROOT / ".staging" / "keys"
SEED_PATH = REPO_ROOT / "docs" / "testassets" / "m4-seed.yaml"
RESULTS_DIR = REPO_ROOT / "docs" / "testassets" / "results"
EXPECTED_HEAD = "0008"
COMPOSE_BASE = (
    "docker",
    "compose",
    "-f",
    "compose.staging.yaml",
    "-f",
    "compose.staging.nfr.yaml",
)
WORKER_CONTAINERS = ("latch-staging-worker1-1", "latch-staging-worker2-1")
API_CONTAINERS = ("latch-staging-api1-1", "latch-staging-api2-1")

# テストモジュールの `from conftest import ...` / `from helpers_nfr import ...`
# を本モジュール(pytestが読んだ実体)へ解決する。--import-mode=importlibでは
# 収集ディレクトリがsys.pathに入らないため・helpers_nfrの解決先を明示する
# (obs側conftestと同一の作法。計画コードからの追記は報告書の逸脱記録へ)
_NFR_DIR = Path(__file__).resolve().parent
if str(_NFR_DIR) not in sys.path:
    sys.path.insert(0, str(_NFR_DIR))
sys.modules.setdefault("conftest", sys.modules[__name__])

# --- 証拠収集(10 §5の形式・sessionfinishでYAML出力) ---

_results: list[dict] = []


def record_item(
    *, item_id: str, name: str, test: str, verdict: str, observed: dict
) -> None:
    """証拠1件を収集する(試験ID・入力条件・合否・観測値)。"""
    _results.append(
        {
            "id": item_id,
            "name": name,
            "test": test,
            "verdict": verdict,
            "observed": observed,
        }
    )


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    """session終了時に証拠YAMLを出力する(失敗時のitemsも含めて出す)。"""
    from datetime import datetime, timedelta, timezone

    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "unit": "M4-ws-7",
        "executed_at": now.isoformat(),
        "environment": "latch-staging(nfr override)",
        "items": list(_results),
    }
    ts = now.strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"ws7-nfr-result-{ts}.yaml"
    path.write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


# --- fixtures ---


@pytest.fixture
async def api():
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as c:
        yield c


@pytest.fixture
async def db():
    conn = await asyncpg.connect(DSN)
    # jsonbはstrで返りうる(asyncpg・コードベース規約)ためクライアント側codec
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.execute("SET default_transaction_read_only = on")
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def db_recovery_rw():
    """n2専用: db2(16543)へのRW接続(embedding NULL化UPDATEで使う)。"""
    conn = await asyncpg.connect(RECOVERY_DSN)
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def redis_nfr():
    """n3用: staging Redis(async・D-16カウンタ操作とguardキー観察)。"""
    import redis.asyncio as aioredis

    r = aioredis.Redis.from_url(REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


# --- 共通ヘルパ ---

_clock = None


def get_clock() -> ControlledClock:
    """staging Redis基準の共有制御Clock(clock_cliと同一コード経路)。"""
    global _clock
    if _clock is None:
        import redis as redis_sync

        _clock = ControlledClock(
            redis_sync.Redis.from_url(REDIS_URL, decode_responses=True)
        )
    return _clock


def issue_token(subject: str) -> str:
    """staging鍵でIdP JWTを発行し/v1/auth/tokenでaccess_tokenへ交換する。

    ws-3ハーネスと同一経路。仮想Clock前進対策の30日IdPトークンも同一。
    """
    pem = (KEYS_DIR / "idp_test_private.pem").read_bytes()
    idp_token = issue_idp_token(
        provider="google",
        subject=subject,
        private_key_pem=pem,
        expires_in=30 * 24 * 3600,
    )
    r = httpx.post(
        f"{API_BASE}/v1/auth/token",
        json={"provider": "google", "idp_token": idp_token},
        timeout=30.0,
    )
    if r.status_code != 200:
        raise AssertionError(f"auth/token交換失敗 {subject}: {r.status_code} {r.text}")
    return r.json()["access_token"]


def auth_header(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(subject)}"}


def compose(*args: str) -> str:
    """staging NFR構成のcomposeコマンド実行(cwd=リポジトリルート)。"""
    r = subprocess.run(
        [*COMPOSE_BASE, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise AssertionError(
            f"compose {' '.join(args)} failed: rc={r.returncode} {r.stderr[:500]}"
        )
    return r.stdout


def workers_stop() -> None:
    compose("stop", "worker1", "worker2")


def workers_start() -> None:
    compose("start", "worker1", "worker2")


def docker_logs(container: str) -> str:
    """コンテナの全ログを取得する(発報検査用・パイプ禁止規律はPython内照合)。"""
    r = subprocess.run(
        ["docker", "logs", container],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout + r.stderr


def publish_event(
    event_type: str,
    intent_id: str,
    *,
    version: int | None = None,
    raw_payload: str | None = None,
) -> None:
    """Match Eventを手動発行する(make staging-publishと同一経路・3点組)。"""
    cmd = [
        "uv",
        "run",
        "python",
        "-m",
        "latch.events",
        "publish",
        event_type,
        "--intent-id",
        str(intent_id),
    ]
    if version is not None:
        cmd += ["--version", str(version)]
    if raw_payload is not None:
        cmd += ["--raw-payload", raw_payload]
    import os

    env = dict(os.environ)
    env["LATCH_PUBSUB_EMULATOR_HOST"] = "127.0.0.1:18085"
    env["LATCH_PUBSUB_PROJECT_ID"] = "latch-staging"
    r = subprocess.run(
        cmd,
        cwd=REPO_ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise AssertionError(
            f"publish {event_type} intent={intent_id} "
            f"失敗: rc={r.returncode} {r.stderr}"
        )
