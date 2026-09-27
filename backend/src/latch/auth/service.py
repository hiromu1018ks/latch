"""認証ユースケース(design §2.6・§1.3)。

token / refresh / logout の3ユースケースとusers読み取り(生SQL 1本)。
SQLAlchemyモデル・リポジトリ層はM1(design §1.3-2)。DB・Redis等の依存障害は
DependencyUnavailableError(503)へ包む(05 第5節「全API」・design §2.7)。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.auth.errors import (
    AuthError,
    DependencyUnavailableError,
    UnauthenticatedError,
)
from latch.auth.idp import IdPVerifier
from latch.auth.sessions import SessionStore
from latch.auth.tokens import (
    ACCESS_TTL_S,
    AccessTokenClaims,
    issue_access_token,
    verify_access_token,
)
from latch.core.clock import Clock

UserLookup = Callable[[str, str], Awaitable[uuid.UUID | None]]

# auth_provider+auth_subject はUNIQUE(uq_users_auth_provider_subject)なので高々1行
_USER_SELECT = text(
    "SELECT id FROM users WHERE auth_provider = :provider AND auth_subject = :subject"
)


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
            return uuid.UUID(row[0]) if row is not None else None

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
    ) -> None:
        self._clock = clock
        self._secret = secret
        self._sessions = sessions
        self._idp = idp
        self._user_lookup = user_lookup

    async def token(self, *, provider: str, idp_token: str) -> TokenResult:
        try:
            return await self._token(provider=provider, idp_token=idp_token)
        except AuthError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("auth dependency unavailable") from exc

    async def _token(self, *, provider: str, idp_token: str) -> TokenResult:
        _, subject = await self._idp.verify(
            provider=provider, idp_token=idp_token, clock=self._clock
        )
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
        except AuthError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("auth dependency unavailable") from exc

    async def _refresh(self, *, refresh_token: str) -> RefreshResult:
        rotated = await self._sessions.rotate_refresh(
            token=refresh_token, now=self._clock.now()
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
