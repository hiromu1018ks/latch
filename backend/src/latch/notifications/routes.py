"""notificationsルータ(M3 ws-3 design §2.5・§2.6・05 §5)。

claimsはプリミティブ(provider/subject)としてサービスへ渡す(latchesと
同型)。ログはstatus/codeのみ — 通知文面は出さない(08 §2.4)。
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.notifications.schemas import NotificationListResponse
from latch.notifications.service import NotificationsService
from latch.ratelimit.deps import api_rate_limited

logger = logging.getLogger("latch.notifications")

notifications_router = APIRouter(
    prefix="/v1/notifications",
    tags=["notifications"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4と同型)
)


def get_notifications_service(request: Request) -> NotificationsService:
    """app.state.notifications_service へのアクセス(lifespan/テスト注入で載る)。"""
    return request.app.state.notifications_service


@notifications_router.get("", response_model=NotificationListResponse)
async def list_notifications(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[NotificationsService, Depends(get_notifications_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> NotificationListResponse:
    """GET /v1/notifications(05 §5・design §2.5。本人のお知らせ一覧)。"""
    items, next_cursor = await svc.list(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        cursor=cursor,
        limit=limit,
    )
    return NotificationListResponse(items=items, next_cursor=next_cursor)


@notifications_router.post("/{notification_id}/read", status_code=204)
async def mark_notification_read(
    notification_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[NotificationsService, Depends(get_notifications_service)],
) -> None:
    """POST /v1/notifications/{id}/read(05 §5・design §2.6。204冪等)。"""
    await svc.read(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        notification_id=notification_id,
    )
