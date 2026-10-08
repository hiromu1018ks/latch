"""cloudflare試験の共通設定。

- latch_worker(cloudflare/api)をCPythonからimport可能にする
- cloudflare_local marker: ローカル `pywrangler dev` が起動中のときのみ
  実行する実測probe。未起動ならskipする(通常のtest-ciには混ぜない)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CF_API_DIR = REPO_ROOT / "cloudflare" / "api"
if str(CF_API_DIR) not in sys.path:
    sys.path.insert(0, str(CF_API_DIR))

BACKEND_DIR = Path(__file__).resolve().parents[2]

CF_API_URL = os.environ.get("LATCH_CF_API_URL", "http://127.0.0.1:8787")


@pytest.fixture(scope="session")
def migrated_db() -> None:
    """比較基準PostgreSQL(compose常設)をheadまで移行(冪等)。"""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
async def db_engine(migrated_db):
    """比較基準PostgreSQLへのengine(T4 parity等で使用)。"""
    from latch.core.db import create_db_engine
    from latch.settings import Settings

    engine = create_db_engine(Settings())
    yield engine
    await engine.dispose()


def _worker_ready() -> bool:
    try:
        resp = httpx.get(f"{CF_API_URL}/health", timeout=3.0)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def cf_worker_url() -> str:
    if not _worker_ready():
        pytest.skip(
            "cloudflare api worker が起動していない"
            "(make cf-dev で pywrangler dev を起動してから実行)"
        )
    return CF_API_URL
