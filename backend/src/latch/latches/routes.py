"""latchesルータ(M3 ws-1 design §2.8・05 §5)。

claimsはプリミティブ(provider/subject)としてサービスへ渡す(intentsと
同型)。ログはstatus/codeのみ — Proposal本文・回答内容は出さない(08 §2.4)。
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.latches.schemas import (
    AttendanceRequest,
    AttendanceResponse,
    LatchDetailEnvelope,
    LatchEnvelope,
    LatchListResponse,
    MessageEnvelope,
    MessageListResponse,
    MessageRequest,
    ResponseRequest,
)
from latch.latches.service import LatchesService
from latch.ratelimit.deps import api_rate_limited

logger = logging.getLogger("latch.latches")

latches_router = APIRouter(
    prefix="/v1/latches",
    tags=["latches"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4と同型)
)


def get_latches_service(request: Request) -> LatchesService:
    """app.state.latches_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.latches_service


@latches_router.get("", response_model=LatchListResponse)
async def list_latches(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LatchListResponse:
    """GET /v1/latches(05 §5。本人関与かつcandidate除外・cursor改頁)。"""
    items, next_cursor = await svc.list(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        cursor=cursor,
        limit=limit,
    )
    return LatchListResponse(items=items, next_cursor=next_cursor)


@latches_router.get("/{latch_id}", response_model=LatchDetailEnvelope)
async def get_latch(
    latch_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
) -> LatchDetailEnvelope:
    """GET /v1/latches/{id}(05 §5。成立後は解放情報を含む)。"""
    detail = await svc.get(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        latch_id=latch_id,
    )
    logger.info("latches.get ok status=%s", detail.status)
    return LatchDetailEnvelope(latch=detail)


@latches_router.post("/{latch_id}/response", response_model=LatchEnvelope)
async def respond_to_latch(
    latch_id: uuid.UUID,
    body: ResponseRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
) -> LatchEnvelope:
    """POST /v1/latches/{id}/response(05 §5・06 §6の直列化)。"""
    summary = await svc.respond(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        latch_id=latch_id,
        response=body.response,
    )
    logger.info("latches.response ok status=%s", summary.status)
    return LatchEnvelope(latch=summary)


@latches_router.post(
    "/{latch_id}/messages", response_model=MessageEnvelope, status_code=201
)
async def send_message(
    latch_id: uuid.UUID,
    body: MessageRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
) -> MessageEnvelope:
    """POST /v1/latches/{id}/messages(05 §5・matchedのみ書込可・承認事項⑦の201)。"""
    message = await svc.send_message(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        latch_id=latch_id,
        body=body.body,
    )
    return MessageEnvelope(message=message)


@latches_router.get("/{latch_id}/messages", response_model=MessageListResponse)
async def list_messages(
    latch_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MessageListResponse:
    """GET /v1/latches/{id}/messages(05 §5改頁共通規定・閲覧は状態を問わず)。"""
    items, next_cursor = await svc.list_messages(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        latch_id=latch_id,
        cursor=cursor,
        limit=limit,
    )
    return MessageListResponse(items=items, next_cursor=next_cursor)


@latches_router.post("/{latch_id}/attendance", response_model=AttendanceResponse)
async def submit_attendance(
    latch_id: uuid.UUID,
    body: AttendanceRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
) -> AttendanceResponse:
    """POST /v1/latches/{id}/attendance(D-09・3日以内・初回のみ受理)。"""
    return await svc.submit_attendance(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        latch_id=latch_id,
        attended=body.attended,
    )
