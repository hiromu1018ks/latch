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
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from latch.auth.errors import AuthError
from latch.auth.routes import logout_router, public_router
from latch.auth.service import build_auth_service, make_user_lookup
from latch.auth.sessions import SessionStore
from latch.core.clock import Clock, make_clock
from latch.core.db import create_db_engine
from latch.core.deps import get_clock
from latch.events import FallbackRelay, make_event_bus
from latch.intents import IntentsError, make_intent_parse_service, parse_router
from latch.intents.routes import intents_crud_router
from latch.intents.service import make_intent_service
from latch.latches import LatchesError, latches_router, make_latches_service
from latch.notifications import (
    NotificationsError,
    build_push_sender,
    make_notifications_service,
    notifications_router,
)
from latch.obs.alert_forward import make_forwarding_emitter
from latch.obs.lag_alert import (
    QueueLagAlert,
    structured_log_alert,
    structured_log_resolve,
)
from latch.obs.middleware import HTTPMetricsMiddleware
from latch.obs.sessions import obs_sessions_router
from latch.ratelimit import make_rate_limiter
from latch.ratelimit.errors import RateLimitError
from latch.safety import (
    BlockCache,
    SafetyError,
    make_safety_service,
    safety_router,
)
from latch.settings import Settings
from latch.users.consent import ConsentService, make_consent_service
from latch.users.errors import UsersError
from latch.users.push import PushTokenService, make_push_token_service
from latch.users.routes import users_router
from latch.users.service import make_user_service
from latch.users.survey import SurveyService, make_survey_service

logger = logging.getLogger("latch.auth")
users_logger = logging.getLogger("latch.users")
intents_logger = logging.getLogger("latch.intents")
latches_logger = logging.getLogger("latch.latches")
notifications_logger = logging.getLogger("latch.notifications")
ratelimit_logger = logging.getLogger("latch.ratelimit")
safety_logger = logging.getLogger("latch.safety")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # サービスごとの独立スキップ判定(テスト注入があれば構築しない)
    build_auth = not hasattr(app.state, "auth_service")
    build_users = not hasattr(app.state, "users_service")
    build_intents = not hasattr(app.state, "intent_parse_service")
    build_intents_crud = not hasattr(app.state, "intent_service")
    build_latches = not hasattr(app.state, "latches_service")
    build_notifications = not hasattr(app.state, "notifications_service")
    build_rate_limit = not hasattr(app.state, "rate_limiter")
    build_events = not hasattr(app.state, "event_bus")
    build_safety = not hasattr(app.state, "safety_service")
    build_consent = not hasattr(app.state, "consent_service")
    build_survey = not hasattr(app.state, "survey_service")
    build_ab = not hasattr(app.state, "ab_service")
    build_push = not hasattr(app.state, "push_token_service")
    if not (
        build_auth
        or build_users
        or build_intents
        or build_intents_crud
        or build_latches
        or build_notifications
        or build_rate_limit
        or build_events
        or build_safety
        or build_consent
        or build_survey
        or build_ab
        or build_push
    ):
        yield
        return
    settings: Settings = app.state.settings
    redis_client = None
    if build_auth or build_rate_limit or build_safety or build_users:
        # Redisはauth(失効リスト)・レート制限カウンタ・ブロックキャッシュ(blk:)
        # の共用(design §2.8・ws-5 §2.2)。M3 ws-6: 退会のセッション失効
        # (SessionStore)もusers構築時に必要なため条件へ追加
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
            clock=app.state.clock,
            engine=engine,
            sessions=SessionStore(redis_client) if redis_client is not None else None,
        )
    if build_consent:
        app.state.consent_service = make_consent_service(
            clock=app.state.clock, engine=engine
        )
    if build_survey:
        app.state.survey_service = make_survey_service(
            clock=app.state.clock, engine=engine
        )
    if build_push:
        # プッシュトークン登録(05 v0.9・V-01)。engine束ねのみ(redis不使用)
        app.state.push_token_service = make_push_token_service(
            clock=app.state.clock, engine=engine
        )
    if build_ab:
        # A/B割付の観測API面(05 v0.8・読み取り専用。適用面はworker側が構築)
        from latch.ab.service import make_ab_service

        app.state.ab_service = make_ab_service(clock=app.state.clock, engine=engine)
    if build_intents:
        app.state.intent_parse_service = make_intent_parse_service(
            clock=app.state.clock,
            settings=settings,
            user_lookup=user_lookup,
            consent_lookup=app.state.consent_service.consented,
        )
    event_relay_task = None
    event_relay_stop = None
    lag_alert_task = None
    lag_alert_stop = None
    lag_alert_redis = None
    if settings.alert_queue_enabled:
        # Queue lag alert発報器(M4 ws-7・design §2.5案A)。既定無効(ci無効
        # 維持)・stagingのみcompose.staging.nfr.yamlで有効化。独立Redis接続
        # (既存redis_clientのbuild条件には触らない)・engineはlifespan共有資産
        lag_alert_redis = aioredis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
        queue_alert = QueueLagAlert(
            engine=engine,
            clock=app.state.clock,
            redis=lag_alert_redis,
            pending_threshold=settings.alert_queue_pending,
            oldest_threshold=settings.alert_queue_oldest_seconds,
            arm_ttl_s=settings.alert_queue_arm_ttl_sec,
            interval_sec=settings.alert_queue_interval_sec,
            # C-01(04 v0.7): 構造化ログの後置にWebhook転送(未設定=ログのみ)
            alert_emitter=make_forwarding_emitter(
                structured_log_alert,
                url=settings.alert_webhook_url,
                timeout_s=settings.alert_webhook_timeout_s,
            ),
            resolve_emitter=structured_log_resolve,
        )
        lag_alert_stop = asyncio.Event()
        lag_alert_task = asyncio.create_task(queue_alert.run(stop=lag_alert_stop))
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
    block_cache = None
    if build_safety:
        # BlockCache は safety(書込み側)と latches(送信判定)の共有資産
        block_cache = BlockCache(redis_client=redis_client, engine=engine)
        app.state.safety_service = make_safety_service(
            clock=app.state.clock, engine=engine, block_cache=block_cache
        )
    if build_latches:
        # 成立通知のプッシュ(m3-g3fix design §2.4): APIプロセスでも
        # push_mode設定が有効。stubはネットワーク呼び出しゼロ(例外は
        # sender側で握る・API応答への影響は送信先障害時のtimeout上限のみ)
        app.state.latches_service = make_latches_service(
            clock=app.state.clock,
            engine=engine,
            block_cache=block_cache,
            push=build_push_sender(app.state.clock, settings),
        )
    if build_notifications:
        app.state.notifications_service = make_notifications_service(
            clock=app.state.clock, engine=engine
        )
    try:
        yield
    finally:
        if lag_alert_stop is not None:
            lag_alert_stop.set()
        if lag_alert_task is not None:
            lag_alert_task.cancel()
            try:
                await lag_alert_task
            except asyncio.CancelledError:
                pass
        if lag_alert_redis is not None:
            await lag_alert_redis.aclose()
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
    notifications_service=None,
    safety_service=None,
    consent_service: ConsentService | None = None,
    survey_service: SurveyService | None = None,
    push_token_service: PushTokenService | None = None,
) -> FastAPI:
    app = FastAPI(title="LATCH API", lifespan=_lifespan)
    app.state.settings = settings if settings is not None else Settings()
    app.state.clock = clock if clock is not None else make_clock(app.state.settings)
    if auth_service is not None:
        app.state.auth_service = auth_service
    if users_service is not None:
        app.state.users_service = users_service
    if consent_service is not None:
        app.state.consent_service = consent_service
    if survey_service is not None:
        app.state.survey_service = survey_service
    if push_token_service is not None:
        app.state.push_token_service = push_token_service
    if intent_parse_service is not None:
        app.state.intent_parse_service = intent_parse_service
    if intent_service is not None:
        app.state.intent_service = intent_service
    if rate_limiter is not None:
        app.state.rate_limiter = rate_limiter
    if latches_service is not None:
        app.state.latches_service = latches_service
    if notifications_service is not None:
        app.state.notifications_service = notifications_service
    if safety_service is not None:
        app.state.safety_service = safety_service

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
    app.include_router(notifications_router)
    app.include_router(safety_router)
    app.include_router(obs_sessions_router)

    # HTTP基本メトリクス(M4 ws-4・design §2.9)。/metricsの自己アクセス
    # はmiddleware内で除外される
    app.add_middleware(HTTPMetricsMiddleware)

    # /metrics(認証外・無レート制限の生エンドポイント・design §2.5)。
    # nginx経由でなくapi直のポートで公開(127.0.0.1バインド・承認事項4)。
    # make_asgi_app()のMountではなく通常ルート: Mountは/metrics(スラッシュ
    # 無し)を307で/metrics/へリダイレクトするため、Prometheusのscrape先
    # (スラッシュ無し)が200を返すようヘルパールートで生やす(exposition
    # 形式の生成はprometheus_clientに一任・gzip非対応のみ差)
    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

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

    @app.exception_handler(NotificationsError)
    async def notifications_error_handler(
        request: Request, exc: NotificationsError
    ) -> JSONResponse:
        notifications_logger.warning("notifications.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )

    @app.exception_handler(SafetyError)
    async def safety_error_handler(request: Request, exc: SafetyError) -> JSONResponse:
        safety_logger.warning("safety.error code=%s", exc.code)
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
