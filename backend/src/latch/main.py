"""FastAPIアプリケーションファクトリ(design §3.1)。

/health は運用プローブ用でありv1 API契約の外に置く(C3の対象外)。
server_time は get_clock() 由来 — Clock差し替えが全経路で効くことの生の消費者。
"""

from typing import Annotated

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
        clock: Annotated[Clock, Depends(get_clock)],
    ) -> dict[str, str]:
        return {"status": "ok", "server_time": clock.now().isoformat()}

    return app


# composeのapiサービスが参照するエントリポイント(uvicorn latch.main:app)
app = create_app()
