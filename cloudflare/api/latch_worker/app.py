"""Workers上の本体LATCH API組立(本体実装・2026-10-09)。

backend/src/latch/main.py の組立を参考に、lifespan/uvicorn/Redis/PubSub
依存を外したFastAPIアプリを構築する:
- routes・schemas・domain計算・errors・例外ハンドラは本体をそのまま再利用
- DBアクセスは adapters/d1/(atomic・reader)へ差し替え
- 認証は純Python JWT(_compat経由のjwt shim)・sessionはD1SessionStore
- LLMはStubLLM(stub応答のみ・実LLM呼出しは承認待ち)

 WorkersのenvはASGI scope経由で届く(lifespanでは取得できない)ため、
初回リクエスト時にscope["env"].DBへservicesを構築するミドルウェア方式。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("latch.workers")

_ERROR_BODY = lambda code, message: {  # noqa: E731
    "error": {"code": code, "message": message, "details": None}
}


def _install_error_handlers(app: FastAPI) -> None:
    """main.pyと同一の例外ハンドラ一式(05 第5節エラー形式の再現)。"""

    from latch.auth.errors import AuthError
    from latch.intents import IntentsError
    from latch.latches import LatchesError
    from latch.notifications import NotificationsError
    from latch.ratelimit.errors import RateLimitError
    from latch.safety import SafetyError
    from latch.users.errors import UsersError

    def handler(error_cls: type, logger_: logging.Logger):
        async def _handler(request: Request, exc: Exception) -> JSONResponse:
            if getattr(exc, "code", "") == "DEPENDENCY_UNAVAILABLE":
                # Workers debug: 原因例外の連鎖をtailで見えるようにする
                chain = []
                cause = exc.__cause__
                while cause is not None and len(chain) < 5:
                    chain.append(f"{type(cause).__name__}: {cause}")
                    cause = cause.__cause__
                print(f"DEBUG dependency chain: {chain}")
            logger_.warning("%s.error code=%s", error_cls.__name__, exc.code)
            return JSONResponse(
                status_code=exc.http_status,  # type: ignore[attr-defined]
                content=_ERROR_BODY(exc.code, str(exc)),  # type: ignore[attr-defined]
            )

        return _handler

    app.add_exception_handler(AuthError, handler(AuthError, logging.getLogger("latch.auth")))
    app.add_exception_handler(
        UsersError, handler(UsersError, logging.getLogger("latch.users"))
    )
    app.add_exception_handler(
        IntentsError, handler(IntentsError, logging.getLogger("latch.intents"))
    )
    app.add_exception_handler(
        LatchesError, handler(LatchesError, logging.getLogger("latch.latches"))
    )
    app.add_exception_handler(
        NotificationsError,
        handler(NotificationsError, logging.getLogger("latch.notifications")),
    )
    app.add_exception_handler(
        SafetyError, handler(SafetyError, logging.getLogger("latch.safety"))
    )
    app.add_exception_handler(
        RateLimitError, handler(RateLimitError, logging.getLogger("latch.ratelimit"))
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if any(err.get("type") == "json_invalid" for err in exc.errors()):
            return JSONResponse(
                status_code=400,
                content=_ERROR_BODY("MALFORMED_REQUEST", "request body is not valid JSON"),
            )
        return JSONResponse(
            status_code=422,
            content=_ERROR_BODY("VALIDATION_ERROR", "request validation failed"),
        )


def create_workers_app(*, llm_mode: str = "stub") -> FastAPI:
    """本体LATCH APIのWorkers版。stubモードのみ(llm_modeは表示用検証)。"""
    from latch.auth.routes import logout_router, public_router
    from latch.intents.routes import intents_crud_router, parse_router
    from latch.latches.routes import latches_router
    from latch.notifications.routes import notifications_router
    from latch.safety.routes import safety_router
    from latch.users.routes import users_router

    app = FastAPI(title="LATCH API", version="workers-d1")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(public_router)
    app.include_router(logout_router)
    app.include_router(users_router)
    app.include_router(parse_router)
    app.include_router(intents_crud_router)
    app.include_router(latches_router)
    app.include_router(notifications_router)
    app.include_router(safety_router)

    from latch_worker.state import WorkerStateMiddleware

    app.add_middleware(WorkerStateMiddleware)

    _install_error_handlers(app)
    app.state.llm_mode = llm_mode
    return app
