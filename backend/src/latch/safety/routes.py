"""safetyルータ(M3 ws-5 design §2.1・§2.4・§2.5・05 §5)。

/v1/users/me/blocks・/v1/users/{id}/block(登録/解除)・/v1/reportsの
4エンドポイント。claimsはプリミティブ(provider/subject)としてサービスへ
渡す(intents/latchesと同型)。ログはイベント名と結果/コードのみ —
通報理由・表示名は出さない(08 §2.4)。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.ratelimit.deps import api_rate_limited
from latch.safety.service import BlockReportService

logger = logging.getLogger("latch.safety")

safety_router = APIRouter(
    prefix="/v1",
    tags=["safety"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4と同型)
)

# 通報理由は選択式4値の英語コード(08 §5.2・design §2.5。表示文言はフロント)
ReportReason = Literal[
    "inappropriate_content",
    "unpleasant_behavior",
    "suspected_impersonation",
    "other",
]


def get_safety_service(request: Request) -> BlockReportService:
    """app.state.safety_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.safety_service


class BlockResponse(BaseModel):
    """POST /v1/users/{id}/block の201応答(design §2.4)。冪等(既存でも同形)。"""

    blocked_id: uuid.UUID


class BlockOut(BaseModel):
    """blocks一覧の1行(design §2.4・display_nameはsupervisor承認⑤)。"""

    model_config = ConfigDict(from_attributes=True)

    blocked_id: uuid.UUID
    display_name: str
    created_at: datetime


class BlockListResponse(BaseModel):
    """GET /v1/users/me/blocks 応答(05 §5共通規定・cursor改頁)。"""

    items: list[BlockOut]
    next_cursor: str | None = None


class ReportRequest(BaseModel):
    """POST /v1/reports のbody(design §2.5・ws-7案X§2.7)。

    reportee_idは省略可(latch_idからサーバが通報者以外を解決 — 1対1
    解決・グループは422)。latch_idも省略可だが両方の省略はserviceが
    422へ。提案段階(1対1)の通報が省略形。
    """

    reportee_id: uuid.UUID | None = None
    latch_id: uuid.UUID | None = None
    reason: ReportReason


class ReportResponse(BaseModel):
    """POST /v1/reports の201応答。"""

    report_id: uuid.UUID


@safety_router.get("/users/me/blocks", response_model=BlockListResponse)
async def list_blocks(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[BlockReportService, Depends(get_safety_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> BlockListResponse:
    """GET /v1/users/me/blocks(05 §5。created_at降順・本人のみ)。"""
    items, next_cursor = await svc.list_blocks(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        cursor=cursor,
        limit=limit,
    )
    return BlockListResponse(items=items, next_cursor=next_cursor)


@safety_router.post(
    "/users/{user_id}/block", response_model=BlockResponse, status_code=201
)
async def block_user(
    user_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[BlockReportService, Depends(get_safety_service)],
) -> BlockResponse:
    """POST /v1/users/{id}/block(05 §5。冪等201・D-23はserviceが実行)。"""
    blocked_id = await svc.block_user(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        target_user_id=user_id,
    )
    logger.info("safety.block ok")
    return BlockResponse(blocked_id=blocked_id)


@safety_router.delete("/users/{user_id}/block", status_code=204)
async def unblock_user(
    user_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[BlockReportService, Depends(get_safety_service)],
) -> None:
    """DELETE /v1/users/{id}/block(05 §5。204・行なし404・遡及なし)。"""
    await svc.unblock_user(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        target_user_id=user_id,
    )
    logger.info("safety.unblock ok")


@safety_router.post("/reports", response_model=ReportResponse, status_code=201)
async def report_user(
    body: ReportRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[BlockReportService, Depends(get_safety_service)],
) -> ReportResponse:
    """POST /v1/reports(08 §5.2。受付・記録のみ・status=pending)。"""
    report_id = await svc.report_user(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        reportee_id=body.reportee_id,
        latch_id=body.latch_id,
        reason=body.reason,
    )
    logger.info("safety.report ok")
    return ReportResponse(report_id=report_id)
