"""Workers state構築(env bindings → services)とミドルウェア(本体実装)。

WorkersではenvがASGI scope経由で届くため(lifespanでは取得できない)、
初回HTTPリクエスト時にscope["env"]からD1 bindingを取りservicesを構築する
(1isolateで1回 — servicesはstatelessなため安全)。testkeys.load_jwksは
Pyodideでdata fileを読めないため埋め込みJWKSへ差し替える。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

logger = logging.getLogger("latch.workers")


def _var(env, name: str, default: str = "") -> str:
    value = getattr(env, name, None)
    return str(value) if value is not None else default


def _patch_testkeys_loader() -> None:
    """load_jwks を埋め込みJWKSへ差し替え(ファイル読み込み不能なため)。"""
    from latch_worker.testjwks import EMBEDDED_JWKS

    import latch.auth.testkeys as testkeys
    import latch.auth.idp as idp_mod

    testkeys.load_jwks = lambda: EMBEDDED_JWKS
    idp_mod.load_jwks = lambda: EMBEDDED_JWKS


async def build_state(app: FastAPI, env) -> None:
    """scope["env"] からservices一式をapp.stateへ構築する。"""
    from latch.adapters.d1.atomic import D1AtomicRepository
    from latch.auth.idp import IdPVerifier, IdPVerifyConfig
    from latch.auth.service import AuthService
    from latch.auth.testkeys import TEST_ACCESS_SECRET
    from latch.core.clock import SystemClock
    from latch.ratelimit.limiter import RateLimiter, RateLimits

    from latch_worker.authn import AuthSessionStoreD1, D1RateLimitStore, make_d1_user_lookup
    from latch_worker.d1binding import RealD1Binding
    from latch_worker.geocode_d1 import D1GeoService
    from latch_worker.svc_intents import D1IntentService, build_parse_service
    from latch_worker.svc_latches import D1LatchesService
    from latch_worker.svc_notifications import D1NotificationsService
    from latch_worker.svc_safety import D1SafetyService
    from latch_worker.svc_users import D1UsersFacade, build_consent_service

    _patch_testkeys_loader()

    db = RealD1Binding(env.DB)
    clock = SystemClock()
    atomic = D1AtomicRepository(db)
    sessions = AuthSessionStoreD1(db, clock=clock)
    user_lookup = make_d1_user_lookup(db)
    limiter = RateLimiter(
        store=D1RateLimitStore(db),
        clock=clock,
        limits=RateLimits(
            api_per_min=int(_var(env, "LATCH_RATE_LIMIT_API_PER_MIN", "60")),
            create_per_day=int(_var(env, "LATCH_RATE_LIMIT_CREATE_PER_DAY", "20")),
            update_per_hour=int(_var(env, "LATCH_RATE_LIMIT_UPDATE_PER_HOUR", "6")),
            active_intents=int(_var(env, "LATCH_RATE_LIMIT_ACTIVE_INTENTS", "5")),
        ),
    )
    secret = _var(env, "LATCH_AUTH_ACCESS_SECRET") or TEST_ACCESS_SECRET
    configs = {}
    for provider in ("google", "apple"):
        configs[provider] = IdPVerifyConfig(
            issuer=_var(
                env,
                f"LATCH_AUTH_IDP_ISSUER_{provider.upper()}",
                f"https://idp.ci.latch.test/{provider}",
            ),
            audience=_var(env, f"LATCH_AUTH_IDP_AUDIENCE_{provider.upper()}", "latch-test-app"),
        )
    app.state.clock = clock
    app.state.rate_limiter = limiter
    app.state.user_lookup = user_lookup
    app.state.auth_service = AuthService(
        clock=clock,
        secret=secret,
        sessions=sessions,
        idp=IdPVerifier(configs=configs),
        user_lookup=user_lookup,
        limiter=limiter,
    )
    consent_service = build_consent_service(db, clock)
    app.state.users_service = D1UsersFacade(
        clock=clock, db=db, atomic=atomic, sessions=sessions
    )
    app.state.consent_service = consent_service
    app.state.intent_parse_service = build_parse_service(
        clock, user_lookup, consent_service.consented
    )
    geo = D1GeoService(db)
    app.state.intent_service = D1IntentService(
        clock=clock, db=db, atomic=atomic, geocoder=geo, limiter=limiter
    )
    app.state.safety_service = D1SafetyService(clock=clock, db=db, atomic=atomic)
    app.state.latches_service = D1LatchesService(
        clock=clock, db=db, atomic=atomic, geo=geo, push=None
    )
    app.state.notifications_service = D1NotificationsService(clock=clock, db=db)
    app.state.workers_ready = True
    logger.info("workers.state built llm_mode=%s", getattr(app.state, "llm_mode", "?"))


class WorkerStateMiddleware:
    """初回リクエスト時にscope["env"]からstateを構築するASGIミドルウェア。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            app = scope.get("app")
            if app is not None and not getattr(app.state, "workers_ready", False):
                env = scope.get("env")
                if env is not None:
                    await build_state(app, env)
        await self.app(scope, receive, send)
