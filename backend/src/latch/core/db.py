"""DB接続の工場(design §2.3: API/WorkerへのDIはM1。ここには工場のみを置く)。"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from latch.settings import Settings


def create_db_engine(settings: Settings) -> AsyncEngine:
    """settings.database_url のasync engine(asyncpg)を生成する。

    - 接続のタイムゾーンはUTCに固定(timestamptzはinstantで保持され、JST運用は
      アプリ層の表現。Clock契約 now()=tz-aware UTC と同じ規約)
    - 時刻の生成はアプリ層のClock由来の明示値(DB時刻関数DEFAULTは持たない — design §2.8)
    """
    return create_async_engine(
        settings.database_url,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
