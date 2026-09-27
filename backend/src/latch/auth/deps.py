"""認証Dependency(design §2.7)。

require_authenticated がC3の強制点 — M1以降のドメインルータはルータ単位で
この依存を付す(v1契約上の認証不要2エンドポイントのみが依存を持たない)。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from latch.auth.errors import UnauthenticatedError
from latch.auth.service import AuthService
from latch.auth.tokens import AccessTokenClaims


def get_auth_service(request: Request) -> AuthService:
    """app.state.auth_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.auth_service


async def require_authenticated(
    svc: Annotated[AuthService, Depends(get_auth_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenClaims:
    """Authorization: Bearer <JWT> を検証しclaimsを返す(05 第5節)。

    欠落・非Bearer・無効・期限切れ・失効リスト掲載は UnauthenticatedError
    (401 UNAUTHENTICATED。ハンドラが共通envelopeへ出す)。
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise UnauthenticatedError("missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    return await svc.authenticate(token=token)
