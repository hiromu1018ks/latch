"""認証ユースケース(design §2.6・§1.3)。

token / refresh / logout の3ユースケースとusers読み取り(生SQL 1本)。
SQLAlchemyモデル・リポジトリ層はM1(design §1.3-2)。DB・Redis等の依存障害は
DependencyUnavailableError(503)へ包む(05 第5節「全API」・design §2.7)。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.auth.errors import (
    AuthError,
    DependencyUnavailableError,
    UnauthenticatedError,
)
from latch.auth.idp import IdPVerifier, IdPVerifyConfig
from latch.auth.sessions import SessionStore
from latch.auth.testkeys import (
    TEST_ACCESS_SECRET,
    TEST_AUDIENCE,
    TEST_ISSUER_PREFIX,
)
from latch.auth.tokens import (
    ACCESS_TTL_S,
    AccessTokenClaims,
    issue_access_token,
    verify_access_token,
)
from latch.core.clock import Clock
from latch.ratelimit.errors import RateLimitedError
from latch.settings import Settings

if TYPE_CHECKING:
    from latch.ratelimit import RateLimiter

UserLookup = Callable[[str, str], Awaitable[uuid.UUID | None]]

# auth_provider+auth_subject はUNIQUE(uq_users_auth_provider_subject)なので高々1行
_USER_SELECT = text(
    "SELECT id FROM users WHERE auth_provider = :provider AND auth_subject = :subject"
)


def _coerce_user_id(value: object) -> uuid.UUID | None:
    """行のid値をUUIDへ正規化する。

    asyncpgはuuid列をUUID「インスタンス」で返す(uuid.UUID(row[0]) は
    AttributeErrorになる — スーパーバイザー検証のtest-ci失敗1)。UUIDは
    そのまま返し、文字列など他型のみ uuid.UUID(str(value)) で構築する。
    """
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def make_user_lookup(engine: AsyncEngine) -> UserLookup:
    """users表の読み取り専用lookup(行なければNone=初回登録待ち)。

    マイグレーションは追加しない(design §2.6)。User作成経路はM1。
    """

    async def user_lookup(provider: str, subject: str) -> uuid.UUID | None:
        async with engine.connect() as conn:
            result = await conn.execute(
                _USER_SELECT, {"provider": provider, "subject": subject}
            )
            row = result.first()
            return _coerce_user_id(row[0]) if row is not None else None

    return user_lookup


@dataclass(frozen=True)
class TokenResult:
    """POST /v1/auth/token の200応答本体(05 第5節)。"""

    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str
    user_id: uuid.UUID | None
    profile_complete: bool


@dataclass(frozen=True)
class RefreshResult:
    """POST /v1/auth/refresh の200応答本体(回転式・05 第5節)。"""

    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str


class AuthService:
    """token/refresh/logout ユースケース(05 第5節)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        secret: str,
        sessions: SessionStore,
        idp: IdPVerifier,
        user_lookup: UserLookup,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._clock = clock
        self._secret = secret
        self._sessions = sessions
        self._idp = idp
        self._user_lookup = user_lookup
        self._limiter = limiter

    async def token(self, *, provider: str, idp_token: str) -> TokenResult:
        try:
            return await self._token(provider=provider, idp_token=idp_token)
        except (AuthError, RateLimitedError):
            # RateLimitedError(429)は透過(design §2.4: 429は503にしない)
            raise
        except Exception as exc:
            raise DependencyUnavailableError("auth dependency unavailable") from exc

    async def _token(self, *, provider: str, idp_token: str) -> TokenResult:
        _, subject = await self._idp.verify(
            provider=provider, idp_token=idp_token, clock=self._clock
        )
        if self._limiter is not None:
            # IdP検証の直後(design §2.5): 401優先を保ったうえで429
            await self._limiter.check_auth(provider=provider, subject=subject)
        user_id = await self._user_lookup(provider, subject)
        issued = await self._sessions.create_refresh(provider=provider, subject=subject)
        access = issue_access_token(
            clock=self._clock,
            secret=self._secret,
            provider=provider,
            subject=subject,
            sid=issued.sid,
        )
        # profile_completeは「User行が存在する」から導出(列は存在しない — design §1.3-3)
        return TokenResult(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL_S,
            refresh_token=issued.token,
            user_id=user_id,
            profile_complete=user_id is not None,
        )

    async def refresh(self, *, refresh_token: str) -> RefreshResult:
        try:
            return await self._refresh(refresh_token=refresh_token)
        except (AuthError, RateLimitedError):
            # RateLimitedError(429)は透過(design §2.4: 429は503にしない)
            raise
        except Exception as exc:
            raise DependencyUnavailableError("auth dependency unavailable") from exc

    async def _refresh(self, *, refresh_token: str) -> RefreshResult:
        rotated = await self._sessions.rotate_refresh(
            token=refresh_token, now=self._clock.now()
        )
        if self._limiter is not None:
            # rotate(検証を兼ねる)の後にprovider+subjectが確定する(design §2.5)
            await self._limiter.check_auth(
                provider=rotated.provider, subject=rotated.subject
            )
        access = issue_access_token(
            clock=self._clock,
            secret=self._secret,
            provider=rotated.provider,
            subject=rotated.subject,
            sid=rotated.sid,
        )
        return RefreshResult(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL_S,
            refresh_token=rotated.token,
        )

    async def logout(self, *, claims: AccessTokenClaims) -> None:
        try:
            await self._logout(claims=claims)
        except AuthError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("auth dependency unavailable") from exc

    async def _logout(self, *, claims: AccessTokenClaims) -> None:
        # 対象JWTをRedis失効リストへ(TTL=残り有効期限)+リフレッシュ族を全失効(05 第5節)
        await self._sessions.revoke_access(
            jti=claims.jti, exp=claims.exp, now=self._clock.now()
        )
        await self._sessions.revoke_family(sid=claims.sid)

    async def authenticate(self, *, token: str) -> AccessTokenClaims:
        """保護エンドポイント用: JWT検証(署名・iss・alg・exp手動)+失効リスト照会。

        05 第5節「失効リスト掲載」を含む401 UNAUTHENTICATEDの判定点。
        require_authenticated(deps)はこれを呼ぶだけ(design §2.3)。
        """
        try:
            claims = verify_access_token(
                clock=self._clock, secret=self._secret, token=token
            )
            if await self._sessions.is_revoked(jti=claims.jti):
                raise UnauthenticatedError("access token revoked")
            return claims
        except AuthError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("auth dependency unavailable") from exc


def build_auth_service(
    *,
    clock: Clock,
    settings: Settings,
    redis_client: aioredis.Redis,
    user_lookup: UserLookup,
    limiter: RateLimiter | None = None,
) -> AuthService:
    """設定からAuthServiceを構築する(design §2.5-3)。

    prod(app_env="prod")でテスト既定値(空secret・同梱JWKS・テストissuer/audience)
    が残存する場合はValueErrorで拒否する(ws-2のllm_mode拒否と同一パターン)。
    ci/stagingは既定値でそのまま動く。redis_client の生成・解体は呼び出し側
    (main.py lifespan)の責務 — この関数は純粋な構築のみ行う。
    limiterはNone=無効(ci試験既定)。main.py lifespanがrate_limiterを渡す。
    """
    if settings.app_env == "prod":
        if (
            not settings.auth_access_secret
            or settings.auth_access_secret == TEST_ACCESS_SECRET
        ):
            raise ValueError(
                "prod requires a dedicated auth_access_secret "
                "(empty/test secret is not allowed)"
            )
        for provider in ("google", "apple"):
            if not getattr(settings, f"auth_idp_jwks_url_{provider}"):
                raise ValueError(f"prod requires auth_idp_jwks_url_{provider}")
            if getattr(settings, f"auth_idp_issuer_{provider}").startswith(
                TEST_ISSUER_PREFIX
            ):
                raise ValueError(f"prod requires a real issuer for {provider}")
            if getattr(settings, f"auth_idp_audience_{provider}") == TEST_AUDIENCE:
                raise ValueError(f"prod requires a real audience for {provider}")
    secret = settings.auth_access_secret or TEST_ACCESS_SECRET
    configs: dict[str, IdPVerifyConfig] = {}
    for provider in ("google", "apple"):
        url = getattr(settings, f"auth_idp_jwks_url_{provider}")
        configs[provider] = IdPVerifyConfig(
            issuer=getattr(settings, f"auth_idp_issuer_{provider}"),
            audience=getattr(settings, f"auth_idp_audience_{provider}"),
            jwks_url=url or None,
        )
    return AuthService(
        clock=clock,
        secret=secret,
        sessions=SessionStore(redis_client),
        idp=IdPVerifier(configs=configs),
        user_lookup=user_lookup,
        limiter=limiter,
    )
