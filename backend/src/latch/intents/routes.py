"""parseルータ(design §2.7・05 §5)。

ルータ単位で require_authenticated(C3・確定値12)。claimsはプリミティブ
(provider/subject)としてサービスへ渡す(サービスはauthの型をimportしない)。
ログはイベント名とcodeのみ — text・condition本文は出さない(08 §2.4)。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.intents.intent_input import (
    IntentCreateRequest,
    IntentPatchRequest,
)
from latch.intents.mapping import to_response_structured
from latch.intents.schema import ParserOutput
from latch.intents.service import (
    IntentParseService,
    IntentService,
    PageResult,
    ParseResult,
)
from latch.intents.store import IntentRow

logger = logging.getLogger("latch.intents")

parse_router = APIRouter(
    prefix="/v1/intents",
    tags=["intents"],
    dependencies=[Depends(require_authenticated)],  # C3(05 §5全API認証済み)
)


class ParseRequest(BaseModel):
    # 07 §2解釈規則: 300字上限・切り詰めなし・空文字は必須欠落相当(422)
    text: str = Field(min_length=1, max_length=300)


class WarningOut(BaseModel):
    """05 §5応答例のwarnings要素と同形。"""

    code: str
    condition: str
    message: str


class ParseResponse(BaseModel):
    structured_intent: ParserOutput
    warnings: list[WarningOut]


def get_intent_parse_service(request: Request) -> IntentParseService:
    """app.state.intent_parse_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.intent_parse_service


def _to_response(result: ParseResult) -> ParseResponse:
    return ParseResponse(
        structured_intent=result.structured_intent,
        warnings=[
            WarningOut(code=w.code, condition=w.condition, message=w.message)
            for w in result.warnings
        ],
    )


@parse_router.post("/parse", response_model=ParseResponse)
async def parse_intent(
    body: ParseRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentParseService, Depends(get_intent_parse_service)],
) -> ParseResponse:
    """POST /v1/intents/parse(05 §5)。保存しない(SP-4)・補完を応答に適用しない。"""
    result = await svc.parse(
        text=body.text,
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
    )
    logger.info("intents.parse ok")
    return _to_response(result)


# ---------------------------------------------------------------------------
# M1 ws-3: intents CRUD(design §2.10・05 §5)


class CategoryOut(BaseModel):
    primary: str | None = None
    secondary: str | None = None


class TimeOut(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    flexibility_minutes: None = None


class LocationOut(BaseModel):
    name: str | None = None
    radius_m: int | None = None
    flexibility: None = None


class BudgetOut(BaseModel):
    max: int | None = None
    currency: Literal["JPY"] = "JPY"


class ParticipantsOut(BaseModel):
    min: int | None = None
    max: int | None = None


class StructuredIntentOut(BaseModel):
    """応答structured_intent(リクエストと同形の再構成 — design §2.10・§6-6)。"""

    category: CategoryOut
    alcohol_involved: bool
    time: TimeOut
    location: LocationOut
    budget: BudgetOut
    participants: ParticipantsOut
    visibility: str
    notification_level: str
    expires_at: datetime | None = None
    soft_constraints: list[str]
    ng_unverifiable: list[str]
    negative_constraints: list[str]


class IntentOut(BaseModel):
    """全CRUD応答で同形(design §6-6)。座標・embeddingは返さない(出力最小化)。"""

    id: uuid.UUID
    status: str
    version: int
    raw_text: str
    structured_data: dict
    structured_intent: StructuredIntentOut
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class IntentEnvelope(BaseModel):
    intent: IntentOut


class IntentListResponse(BaseModel):
    items: list[IntentOut]
    next_cursor: str | None = None  # 次ページがなければnull(05 §5)


intents_crud_router = APIRouter(
    prefix="/v1/intents",
    tags=["intents"],
    dependencies=[Depends(require_authenticated)],  # C3(05 §5全API認証済み)
)


def get_intent_service(request: Request) -> IntentService:
    """app.state.intent_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.intent_service


def make_intent_out(row: IntentRow) -> IntentOut:
    return IntentOut(
        id=row.id,
        status=row.status,
        version=row.version,
        raw_text=row.raw_text,
        structured_data=row.structured_data,
        structured_intent=StructuredIntentOut.model_validate(
            to_response_structured(row)
        ),
        expires_at=row.expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


StatusFilter = Annotated[
    Literal["draft", "active", "paused", "matched", "expired", "cancelled"] | None,
    Query(description="statusフィルタ(未指定は全status)"),
]


@intents_crud_router.post("", status_code=201, response_model=IntentEnvelope)
async def create_intent(
    body: IntentCreateRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentEnvelope:
    """POST /v1/intents(05 §5)。active=全検証+created Event / draft=raw_textのみ。"""
    row = await svc.create(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        raw_text=body.raw_text,
        status=body.status,
        structured_intent=body.structured_intent,
    )
    logger.info("intents.create ok status=%s", body.status)
    return IntentEnvelope(intent=make_intent_out(row))


@intents_crud_router.get("", response_model=IntentListResponse)
async def list_intents(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
    status: StatusFilter = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> IntentListResponse:
    """GET /v1/intents(自Intent一覧・design §2.10キーセットcursor)。"""
    page: PageResult = await svc.list(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return IntentListResponse(
        items=[make_intent_out(r) for r in page.rows],
        next_cursor=page.next_cursor,
    )


@intents_crud_router.get("/{intent_id}", response_model=IntentEnvelope)
async def get_intent(
    intent_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentEnvelope:
    row = await svc.get(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        intent_id=intent_id,
    )
    logger.info("intents.get ok")
    return IntentEnvelope(intent=make_intent_out(row))


@intents_crud_router.patch("/{intent_id}", response_model=IntentEnvelope)
async def patch_intent(
    intent_id: uuid.UUID,
    body: IntentPatchRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentEnvelope:
    """PATCH /v1/intents/{id}(全置換・draft再保存・draft→active — design §2.10)。"""
    row = await svc.update(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        intent_id=intent_id,
        raw_text=body.raw_text,
        status=body.status,
        structured_intent=body.structured_intent,
    )
    logger.info("intents.update ok")
    return IntentEnvelope(intent=make_intent_out(row))


@intents_crud_router.delete("/{intent_id}", status_code=204)
async def delete_intent(
    intent_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> Response:
    """DELETE /v1/intents/{id}(cancelled遷移+deleted Event・物理削除しない)。"""
    await svc.delete(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        intent_id=intent_id,
    )
    logger.info("intents.delete ok")
    return Response(status_code=204)


@intents_crud_router.post("/{intent_id}/pause", response_model=IntentEnvelope)
async def pause_intent(
    intent_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentEnvelope:
    row = await svc.pause(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        intent_id=intent_id,
    )
    logger.info("intents.pause ok")
    return IntentEnvelope(intent=make_intent_out(row))


@intents_crud_router.post("/{intent_id}/resume", response_model=IntentEnvelope)
async def resume_intent(
    intent_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentEnvelope:
    row = await svc.resume(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        intent_id=intent_id,
    )
    logger.info("intents.resume ok")
    return IntentEnvelope(intent=make_intent_out(row))
