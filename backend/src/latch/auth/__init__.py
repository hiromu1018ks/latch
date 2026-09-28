"""認証(M0 ws-3)。token/refresh/logout・Redis失効リスト・JWKS検証・テスト用認証構成。

M1以降の呼び出し側はこのパッケージ越しにAuthService・require_authenticatedを
利用する(design §3.1)。ルータ(logout_router・public_router)はM1 ws-4以降
latch.auth.routes から直接importする — パッケージ初期化がroutes経由で
ratelimit.depsを引き込み、ratelimit.deps→auth.deps との初期化順循環が
生じるため(M1 ws-4)。
"""

from latch.auth.deps import get_auth_service, require_authenticated
from latch.auth.errors import (
    AuthError,
    DependencyUnavailableError,
    InvalidIdpTokenError,
    InvalidRefreshTokenError,
    UnauthenticatedError,
)
from latch.auth.idp import IdPVerifier, IdPVerifyConfig
from latch.auth.service import (
    AuthService,
    RefreshResult,
    TokenResult,
    build_auth_service,
    make_user_lookup,
)
from latch.auth.sessions import RefreshIssued, RotationResult, SessionStore
from latch.auth.tokens import (
    ACCESS_TTL_S,
    REFRESH_TTL_S,
    AccessTokenClaims,
    issue_access_token,
    verify_access_token,
)

__all__ = [
    "ACCESS_TTL_S",
    "AuthError",
    "AuthService",
    "AccessTokenClaims",
    "DependencyUnavailableError",
    "IdPVerifyConfig",
    "IdPVerifier",
    "InvalidIdpTokenError",
    "InvalidRefreshTokenError",
    "REFRESH_TTL_S",
    "RefreshIssued",
    "RefreshResult",
    "RotationResult",
    "SessionStore",
    "TokenResult",
    "UnauthenticatedError",
    "build_auth_service",
    "get_auth_service",
    "issue_access_token",
    "make_user_lookup",
    "require_authenticated",
    "verify_access_token",
]
