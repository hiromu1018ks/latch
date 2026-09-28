"""認証ルータ(design §2.7)。

public_router: token/refresh — v1契約で認証不要の2エンドポイントのみ(05 第5節)
logout_router: require_authenticated 依存の保護ルータ
ログはイベント名と結果/コードのみ — トークン・claim・subjectは出さない(08 第2.4節)。
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from latch.auth.deps import get_auth_service, require_authenticated
from latch.auth.service import AuthService
from latch.auth.tokens import ACCESS_TTL_S, AccessTokenClaims
from latch.ratelimit.deps import api_rate_limited

logger = logging.getLogger("latch.auth")

public_router = APIRouter(prefix="/v1/auth", tags=["auth"])
logout_router = APIRouter(
    prefix="/v1/auth", tags=["auth"], dependencies=[Depends(api_rate_limited)]
)


class TokenRequest(BaseModel):
    provider: Literal["google", "apple"]  # 04 D-21: IdPはGoogle/Appleの2種
    idp_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserInfo(BaseModel):
    id: uuid.UUID | None
    profile_complete: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = ACCESS_TTL_S
    refresh_token: str
    user: UserInfo


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = ACCESS_TTL_S
    refresh_token: str


@public_router.post("/token", response_model=TokenResponse)
async def token(
    body: TokenRequest,
    svc: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    result = await svc.token(provider=body.provider, idp_token=body.idp_token)
    logger.info("auth.token ok")
    return TokenResponse(
        access_token=result.access_token,
        token_type=result.token_type,
        expires_in=result.expires_in,
        refresh_token=result.refresh_token,
        user=UserInfo(id=result.user_id, profile_complete=result.profile_complete),
    )


@public_router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    body: RefreshRequest,
    svc: Annotated[AuthService, Depends(get_auth_service)],
) -> RefreshResponse:
    result = await svc.refresh(refresh_token=body.refresh_token)
    logger.info("auth.refresh ok")
    return RefreshResponse(
        access_token=result.access_token,
        token_type=result.token_type,
        expires_in=result.expires_in,
        refresh_token=result.refresh_token,
    )


@logout_router.post("/logout", status_code=204)
async def logout(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    """request {}(AuthorizationヘッダーのJWTが対象)→ 204(05 第5節)。冪等。"""
    await svc.logout(claims=claims)
    logger.info("auth.logout ok")
