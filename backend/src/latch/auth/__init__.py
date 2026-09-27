"""認証(M0 ws-3)。token/refresh/logout・Redis失効リスト・JWKS検証・テスト用認証構成。

M1以降の呼び出し側はこのパッケージ越しにAuthService・require_authenticated・
ルータを利用する(design §3.1)。
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
from latch.auth.routes import logout_router, public_router
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
    "logout_router",
    "make_user_lookup",
    "public_router",
    "require_authenticated",
    "verify_access_token",
]
