"""FastAPIアプリケーションファクトリ。

統合履歴: M0 ws-3 認証 / M1 ws-1 users API / M1 ws-2 parse API / M1 ws-4 レート制限。

/health は運用プローブ用でありv1 API契約の外に置く(C3の対象外)。
server_time は get_clock() 由来 — Clock差し替えが全経路で効くことの生の消費者。
lifespanでredis・db engine・AuthService・UserService・IntentParseServiceを構築する。
auth_service / users_service / intent_parse_service はcreate_app引数で注入済みなら
該当サービスの構築を個別にスキップする(サービスごとの独立判定 — M1 ws-1 design §2.5・
M1 ws-2 design §2.7)。engineは全サービスの共有資産、user_lookupはauthとintentsの共有。
"""

import asyncio
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
from latch.events import FallbackRelay, make_event_bus
from latch.intents import IntentsError, make_intent_parse_service, parse_router
from latch.intents.routes import intents_crud_router
from latch.intents.service import make_intent_service
from latch.latches import LatchesError, latches_router, make_latches_service
from latch.ratelimit import make_rate_limiter
from latch.ratelimit.errors import RateLimitError
from latch.settings import Settings
from latch.users.errors import UsersError
from latch.users.routes import users_router
from latch.users.service import make_user_service

logger = logging.getLogger("latch.auth")
users_logger = logging.getLogger("latch.users")
intents_logger = logging.getLogger("latch.intents")
latches_logger = logging.getLogger("latch.latches")
ratelimit_logger = logging.getLogger("latch.ratelimit")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # サービスごとの独立スキップ判定(テスト注入があれば構築しない)
    build_auth = not hasattr(app.state, "auth_service")
    build_users = not hasattr(app.state, "users_service")
    build_intents = not hasattr(app.state, "intent_parse_service")
    build_intents_crud = not hasattr(app.state, "intent_service")
    build_latches = not hasattr(app.state, "latches_service")
    build_rate_limit = not hasattr(app.state, "rate_limiter")
    build_events = not hasattr(app.state, "event_bus")
    if not (
        build_auth
        or build_users
        or build_intents
        or build_intents_crud
        or build_latches
        or build_rate_limit
        or build_events
    ):
        yield
        return
    settings: Settings = app.state.settings
    redis_client = None
    if build_auth or build_rate_limit:
        # Redisはauth(失効リスト)とレート制限カウンタの共用(design §2.8)
        redis_client = aioredis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
    # engine は auth・users・intents の共有資産
    engine = getattr(app.state, "db_engine", None)
    if engine is None:
        engine = create_db_engine(settings)
        app.state.db_engine = engine
    # user_lookup は auth と intents の共有。api_rate_limited も消費する
    # (user_id単位のカウント — design §2.4)
    user_lookup = make_user_lookup(engine)
    app.state.user_lookup = user_lookup
    if build_rate_limit:
        app.state.rate_limiter = make_rate_limiter(
            clock=app.state.clock,
            redis_client=redis_client,
            settings=settings,
        )
    if build_auth:
        app.state.auth_service = build_auth_service(
            clock=app.state.clock,
            settings=settings,
            redis_client=redis_client,
            user_lookup=user_lookup,
            limiter=app.state.rate_limiter if build_rate_limit else None,
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
    event_relay_task = None
    event_relay_stop = None
    if build_events:
        # ci=エミュレータ・本番=実GCP(settings切替のみ — design §2.1)。
        # ensure(topic作成)は呼ばない: unit試験のlifespan(app fixtureがbus未注入)
        # で実GCPへのRPC接続待ちが発生するため。topicはWorker起動時のensureが
        # 作り、APIはpublish失敗(NotFound)を握ってフォールバックリレーが回収する
        event_bus = make_event_bus(settings)
        app.state.event_bus = event_bus
        app.state.intent_service = make_intent_service(
            clock=app.state.clock,
            engine=engine,
            limiter=app.state.rate_limiter if build_rate_limit else None,
            event_bus=event_bus,
        )
        relay = FallbackRelay(
            engine=engine, clock=app.state.clock, bus=event_bus, settings=settings
        )
        event_relay_stop = asyncio.Event()
        app.state.event_relay_stop = event_relay_stop
        event_relay_task = asyncio.create_task(relay.run(stop=event_relay_stop))
        app.state.event_relay_task = event_relay_task
    elif build_intents_crud:
        # event_bus注入済み(テスト)でもintent_service未構築なら構築する
        app.state.intent_service = make_intent_service(
            clock=app.state.clock,
            engine=engine,
            limiter=app.state.rate_limiter if build_rate_limit else None,
            event_bus=getattr(app.state, "event_bus", None),
        )
    if build_latches:
        app.state.latches_service = make_latches_service(
            clock=app.state.clock, engine=engine
        )
    try:
        yield
    finally:
        if event_relay_stop is not None:
            event_relay_stop.set()
        if event_relay_task is not None:
            event_relay_task.cancel()
            try:
                await event_relay_task
            except asyncio.CancelledError:
                pass
        if redis_client is not None:
            await redis_client.aclose()
        if build_events:
            await app.state.event_bus.close()
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
    rate_limiter=None,
    latches_service=None,
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
    if rate_limiter is not None:
        app.state.rate_limiter = rate_limiter
    if latches_service is not None:
        app.state.latches_service = latches_service

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
    app.include_router(latches_router)

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

    @app.exception_handler(LatchesError)
    async def latches_error_handler(
        request: Request, exc: LatchesError
    ) -> JSONResponse:
        latches_logger.warning("latches.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit_error_handler(
        request: Request, exc: RateLimitError
    ) -> JSONResponse:
        ratelimit_logger.warning("ratelimit.error code=%s", exc.code)
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
