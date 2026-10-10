"""Cloudflare実装(D1/Workers)のAPI契約パリティ試験(本体実装)。

契約119ケース(CONTRACT_CASES)をCloudflare実装へ流す:
- 既定(local): in-process ASGI(latch_worker.app + LocalD1Database)
  — miniflare不要の高速検証。routes/検証規則はWorkers版そのもの。
- LATCH_CF_REMOTE=1 + LATCH_CF_API_URL: 実環境URL + wrangler d1 seed
  (cloudflare_harness.WranglerD1)。

実行: cd backend && uv run pytest tests/contracts/test_cloudflare_parity.py -q
リモート: LATCH_CF_REMOTE=1 LATCH_CF_API_URL=https://latch-api... \
  CLOUDFLARE_API_TOKEN=... uv run pytest ... -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts.cases import CONTRACT_CASES  # noqa: E402
from contracts.cloudflare_harness import CloudflareHarness, WranglerD1  # noqa: E402
from contracts.runner import ReferenceTransport, run_case  # noqa: E402

pytestmark = pytest.mark.integration

_GEO_SEED = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "fixtures"
    / "geofeatures_seed.sql"
)
_WORKER_DIR = str(Path(__file__).resolve().parents[3] / "cloudflare" / "api")

REMOTE = os.environ.get("LATCH_CF_REMOTE") == "1"
D1_LOCAL = os.environ.get("LATCH_CF_D1_LOCAL") == "1"
BASE_URL = os.environ.get(
    "LATCH_CF_API_URL", "https://latch-api.hiromu1018ks.workers.dev"
)


@pytest.fixture(scope="session")
def local_stack():
    """(app, db) — in-process Workers構成(state構築済み・geofeatures投入済み)。"""
    if REMOTE:
        yield None
        return
    sys.path.insert(0, _WORKER_DIR)
    from latch_worker.app import create_workers_app
    from latch_worker.state import build_state

    from latch.adapters.d1.database import LocalD1Database

    db = LocalD1Database()
    db.migrate()
    db._conn.executescript(_GEO_SEED.read_text(encoding="utf-8"))  # noqa: SLF001

    class EnvBox:
        DB = db
        LATCH_ENV = "contract-local"

    app = create_workers_app()

    async def _build() -> None:
        await build_state(app, EnvBox())

    import asyncio

    asyncio.run(_build())
    yield app, db
    db.close()


@pytest.fixture
async def harness(local_stack):
    if REMOTE:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
            h = CloudflareHarness(client, wrangler=WranglerD1(local=D1_LOCAL))
            yield h
            await h.cleanup()
        return
    app, db = local_stack
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://contract-local"
    ) as client:
        h = CloudflareHarness(client, db=db)
        yield h
        await h.cleanup()


@pytest.fixture
async def transport(local_stack):
    if REMOTE:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
            yield ReferenceTransport(client)
        return
    app, _db = local_stack
    transport_ = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport_, base_url="http://contract-local"
    ) as client:
        yield ReferenceTransport(client)


@pytest.mark.parametrize(
    "case", CONTRACT_CASES, ids=[c.case_id for c in CONTRACT_CASES]
)
async def test_cloudflare_contract_case(case, harness, transport):
    variables = {**harness.clock_vars(), **await harness.prepare_case_vars(case)}
    problems = await run_case(
        case,
        transport,
        resolve_token=harness.resolve_token,
        seed_fn=harness.seed,
        variables=variables,
    )
    assert not problems, "\n".join(problems)
