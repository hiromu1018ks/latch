"""usersルータ(M1 ws-1。05 第5節 認証・ユーザー系)。

POST /v1/users(初回登録・201)と GET /v1/users/me(本人のみ・200)。
DELETE /v1/users/me(退会・204・M3 ws-6 design §2.3)。
ルータ単位で require_authenticated を付す(C3の強制方法 — 05 第5節冒頭)。
profileの正規化(bio=None → bioキーなし)はこの層で行う(design §2.7)。
ログはイベント名と結果/コードのみ — subject・claim値・display_nameは出さない
(08 第2.4節)。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.ratelimit.deps import api_rate_limited
from latch.users.service import UserCreated, UserMe, UserService

logger = logging.getLogger("latch.users")

users_router = APIRouter(
    prefix="/v1/users",
    tags=["users"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4)
)


def get_users_service(request: Request) -> UserService:
    """app.state.users_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.users_service


class UserProfileInput(BaseModel):
    """登録リクエストのprofileオブジェクト(bioのみ — 05 第5節)。"""

    bio: str | None = None


class UserCreateRequest(BaseModel):
    # 上限なし・文字種検証なし(docsに規定なし — design §2.7)
    display_name: str = Field(min_length=1)  # 空文字=必須欠落相当(422)
    birth_date: date  # YYYY-MM-DD以外は422(既存ハンドラがenvelope化)
    profile: UserProfileInput | None = None


class UserCreatedBody(BaseModel):
    id: uuid.UUID
    display_name: str


class UserCreatedResponse(BaseModel):
    """POST /v1/users の201応答(05 第5節・入れ子)。"""

    user: UserCreatedBody


class UserMeResponse(BaseModel):
    """GET /v1/users/me の200応答(05 第5節)。birth_dateは本人のみに返す。"""

    id: uuid.UUID
    display_name: str
    profile: dict
    birth_date: date
    profile_complete: bool


@users_router.post("", status_code=201, response_model=UserCreatedResponse)
async def register_users(
    body: UserCreateRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[UserService, Depends(get_users_service)],
) -> UserCreatedResponse:
    """初回登録。auth_provider/auth_subject はJWT claimに紐付く(05 第5節)。"""
    bio = body.profile.bio if body.profile is not None else None
    profile = {"bio": bio} if bio is not None else {}
    created: UserCreated = await svc.register(
        claims=claims,
        display_name=body.display_name,
        birth_date=body.birth_date,
        profile=profile,
    )
    logger.info("users.register ok")
    return UserCreatedResponse(
        user=UserCreatedBody(id=created.id, display_name=created.display_name)
    )


@users_router.get("/me", response_model=UserMeResponse)
async def get_users_me(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[UserService, Depends(get_users_service)],
) -> UserMeResponse:
    """本人のみ(05 第5節)。me応答にjti/sid等のセッション情報を含めない。"""
    me: UserMe = await svc.get_me(claims=claims)
    logger.info("users.me ok")
    return UserMeResponse(
        id=me.id,
        display_name=me.display_name,
        profile=me.profile,
        birth_date=me.birth_date,
        profile_complete=me.profile_complete,
    )


@users_router.delete("/me", status_code=204)
async def delete_users_me(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[UserService, Depends(get_users_service)],
) -> None:
    """退会(M3 ws-6・design §2.3)。全Intentカスケード+ユーザー単位処理を
    1トランザクションで同期実行し、コミット後にセッションを失効する。"""
    await svc.delete_account(claims=claims)
    logger.info("users.delete_me ok")
