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
from latch.core.clock import JST, Clock
from latch.core.deps import get_clock
from latch.intents.completion import expires_at_candidates, nearest_expires_at
from latch.intents.errors import IntentValidationError
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
from latch.ratelimit.deps import api_rate_limited

logger = logging.getLogger("latch.intents")

parse_router = APIRouter(
    prefix="/v1/intents",
    tags=["intents"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4・design §2.4)
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
# M1 ws-5: 有効期限選択肢の提供(design §2.6・05 §5追記分)


class ExpiryOptionOut(BaseModel):
    """期限選択肢1件(03 §3 FR-13・design §2.6)。"""

    label: str
    expires_at: datetime
    selectable: bool


class ExpiryOptionsResponse(BaseModel):
    options: list[ExpiryOptionOut]
    default_index: int | None = None  # time_start未指定はnull


EXPIRY_LABELS = ("今夜 23:30", "明日 12:00", "明日 23:30", "3日後まで")

TimeStartParam = Annotated[
    datetime | None,
    Query(description="time.start(ISO 8601・tz-aware)"),
]


@parse_router.get("/expiry-options", response_model=ExpiryOptionsResponse)
async def expiry_options(
    clock: Annotated[Clock, Depends(get_clock)],
    time_start: TimeStartParam = None,
) -> ExpiryOptionsResponse:
    """GET /v1/intents/expiry-options(05 §5追記・design §2.6)。

    completion.py の単一実装を呼ぶだけ(新規計算ロジックなし — 07 §2)。
    UI計算(ws-5)がこのAPI経由で消費する。parse_routerに置くことで
    intents_crud_router の GET /{intent_id} より先にマッチする
    (main.py のinclude順)。
    """
    if time_start is not None and time_start.tzinfo is None:
        raise IntentValidationError("time_start must be tz-aware ISO8601")
    now = clock.now()
    candidates = expires_at_candidates(now)
    default_index: int | None = None
    if time_start is not None:
        nearest = nearest_expires_at(time_start, now)
        default_index = candidates.index(nearest)
    return ExpiryOptionsResponse(
        options=[
            # 応答のexpires_atはJST表記へ統一(now+72hはUTCのままZ表記になるのを防ぐ)
            ExpiryOptionOut(
                label=label, expires_at=at.astimezone(JST), selectable=at > now
            )
            for label, at in zip(EXPIRY_LABELS, candidates, strict=True)
        ],
        default_index=default_index,
    )


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
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4・design §2.4)
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
