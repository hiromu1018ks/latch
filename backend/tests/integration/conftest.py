"""integration試験の共通フィクスチャ(design §3.1・§4.1)。

compose常設DBに対し、sessionスコープで alembic upgrade head(冪等)してから
functionスコープの AsyncEngine を提供する。エンジンは試験ごとに生成・破棄する
(イベントループをまたぐ接続再利用を避ける)。
"""

from pathlib import Path

import pytest
import redis.asyncio as redis_async
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.db import create_db_engine
from latch.settings import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def migrated_db() -> None:
    """DBをheadまでマイグレーションする(冪等・sessionで1回)。"""
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
async def db_engine(migrated_db) -> AsyncEngine:
    engine = create_db_engine(Settings())
    yield engine
    await engine.dispose()


@pytest.fixture
async def redis_client():
    """compose常設Redis(127.0.0.1:6379)へのクライアント(design §4.2-7)。"""
    client = redis_async.Redis.from_url(Settings().redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def api_client():
    """compose常設api(127.0.0.1:8000)へのHTTPクライアント(G0の実HTTP証拠)。"""
    async with AsyncClient(base_url="http://127.0.0.1:8000") as c:
        yield c


# -- worker_env(ws-8: test_events_pipelineから移設+JevWorker注入オプション) --
# テストプロセスからはport映射経由(コンテナ内はpubsub:8085 — compose.yaml)。
# env設定後にimport(PubsubEventBus構築がLATCH_PUBSUB_EMULATOR_HOSTを読むため)
import asyncio  # noqa: E402
import contextlib  # noqa: E402
import os  # noqa: E402
import uuid as uuid_mod  # noqa: E402
from typing import NamedTuple  # noqa: E402

os.environ.setdefault("LATCH_PUBSUB_EMULATOR_HOST", "127.0.0.1:8085")

from latch.core.clock import FakeClock, SystemClock  # noqa: E402
from latch.events import PubsubEventBus  # noqa: E402
from latch.worker.main import Worker  # noqa: E402


async def _instant(_seconds: float) -> None:
    return None  # 実時間sleepさせない(移設元test_events_pipeline._instantと同一)


class _WorkerEnv(NamedTuple):
    bus: PubsubEventBus
    clock: FakeClock
    worker: Worker
    task: asyncio.Task


@contextlib.asynccontextmanager
async def make_worker_env(db_engine, *, jev=None):
    """テストプロセス内Worker環境。jevへJevWorkerを渡すとWorker DIで注入
    (縮退E2E試験8 — design §2.4)。未注入はrun()内で再構築(既存挙動)。
    """
    sub_name = f"match-events-test-{uuid_mod.uuid4().hex[:8]}"
    settings = Settings()
    bus = PubsubEventBus(settings, subscription=sub_name)
    for _ in range(40):  # エミュレータ起動待ち(最大20秒)
        try:
            await bus.ensure()
            break
        except Exception:
            await asyncio.sleep(0.5)
    else:
        pytest.fail("pubsub emulator not reachable at 127.0.0.1:8085")
    clock = FakeClock(SystemClock().now())
    worker = Worker(
        clock=clock,
        settings=settings,
        bus=bus,
        engine=db_engine,
        sleep=_instant,
        jev=jev,
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)  # subscribe開始を待つ
    try:
        yield _WorkerEnv(bus=bus, clock=clock, worker=worker, task=task)
    finally:
        worker.request_shutdown()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except TimeoutError:
            task.cancel()
        # 残余メッセージを次試験へ残さない。**closeの前に**削除する
        # (close後のgRPCチャネルはクローズ済みで "Cannot invoke RPC on
        # closed channel" になる — スーパーバイザー検証で検出)
        bus.delete_subscription()
        await bus.close()


@pytest.fixture
async def worker_env(db_engine):
    async with make_worker_env(db_engine) as env:
        yield env


@pytest.fixture
def worker_env_factory():
    """jev注入付きWorker環境のfactory(縮退E2E試験8)。teardownはctx抜けで走る。"""
    return make_worker_env
