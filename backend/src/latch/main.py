"""FastAPIアプリケーションファクトリ。

統合履歴: M0 ws-3 認証 / M1 ws-1 users API / M1 ws-2 parse API。

/health は運用プローブ用でありv1 API契約の外に置く(C3の対象外)。
server_time は get_clock() 由来 — Clock差し替えが全経路で効くことの生の消費者。
lifespanでredis・db engine・AuthService・UserService・IntentParseServiceを構築する。
auth_service / users_service / intent_parse_service はcreate_app引数で注入済みなら
該当サービスの構築を個別にスキップする(サービスごとの独立判定 — M1 ws-1 design §2.5・
M1 ws-2 design §2.7)。engineは全サービスの共有資産、user_lookupはauthとintentsの共有。
"""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from latch.auth.errors import AuthError
from latch.auth.routes import logout_router, public_router
from latch.auth.service import build_auth_service, make_user_lookup
from latch.core.clock import Clock, SystemClock
from latch.core.db import create_db_engine
from latch.core.deps import get_clock
from latch.intents import IntentsError, make_intent_parse_service, parse_router
from latch.intents.routes import intents_crud_router
from latch.intents.service import make_intent_service
from latch.settings import Settings
from latch.users.errors import UsersError
from latch.users.routes import users_router
from latch.users.service import make_user_service

logger = logging.getLogger("latch.auth")
users_logger = logging.getLogger("latch.users")
intents_logger = logging.getLogger("latch.intents")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # サービスごとの独立スキップ判定(テスト注入があれば構築しない)
    build_auth = not hasattr(app.state, "auth_service")
    build_users = not hasattr(app.state, "users_service")
    build_intents = not hasattr(app.state, "intent_parse_service")
    build_intents_crud = not hasattr(app.state, "intent_service")
    if not (build_auth or build_users or build_intents or build_intents_crud):
        yield
        return
    settings: Settings = app.state.settings
    redis_client = None
    if build_auth:
        redis_client = aioredis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
    # engine は auth・users・intents の共有資産
    engine = getattr(app.state, "db_engine", None)
    if engine is None:
        engine = create_db_engine(settings)
        app.state.db_engine = engine
    # user_lookup は auth と intents の共有(索引済みSELECT 1本のファクトリ・純関数)
    user_lookup = make_user_lookup(engine)
    if build_auth:
        app.state.auth_service = build_auth_service(
            clock=app.state.clock,
            settings=settings,
            redis_client=redis_client,
            user_lookup=user_lookup,
        )
    if build_users:
        app.state.users_service = make_user_service(
            clock=app.state.clock, engine=engine
        )
    if build_intents:
        app.state.intent_parse_service = make_intent_parse_service(
            clock=app.state.clock,
            settings=settings,
            user_lookup=user_lookup,
        )
    if build_intents_crud:
        app.state.intent_service = make_intent_service(
            clock=app.state.clock, engine=engine
        )
    try:
        yield
    finally:
        if redis_client is not None:
            await redis_client.aclose()
        await engine.dispose()


def _error_body(code: str, message: str) -> dict:
    # 05 第5節 エラー形式
    return {"error": {"code": code, "message": message, "details": None}}


def create_app(
    clock: Clock | None = None,
    settings: Settings | None = None,
    auth_service=None,
    users_service=None,
    intent_parse_service=None,
    intent_service=None,
) -> FastAPI:
    app = FastAPI(title="LATCH API", lifespan=_lifespan)
    app.state.clock = clock if clock is not None else SystemClock()
    app.state.settings = settings if settings is not None else Settings()
    if auth_service is not None:
        app.state.auth_service = auth_service
    if users_service is not None:
        app.state.users_service = users_service
    if intent_parse_service is not None:
        app.state.intent_parse_service = intent_parse_service
    if intent_service is not None:
        app.state.intent_service = intent_service

    @app.get("/health")
    async def health(
        clock: Annotated[Clock, Depends(get_clock)],
    ) -> dict[str, str]:
        return {"status": "ok", "server_time": clock.now().isoformat()}

    app.include_router(public_router)
    app.include_router(logout_router)
    app.include_router(users_router)
    app.include_router(parse_router)
    app.include_router(intents_crud_router)

    @app.exception_handler(AuthError)
    async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
        logger.warning("auth.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )

    @app.exception_handler(UsersError)
    async def users_error_handler(request: Request, exc: UsersError) -> JSONResponse:
        users_logger.warning("users.error code=%s", exc.code)
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


# composeのapiサービスが参照するエントリポイント(uvicorn latch.main:app)
app = create_app()
