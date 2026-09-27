"""parseルータ(design §2.7・05 §5)。

ルータ単位で require_authenticated(C3・確定値12)。claimsはプリミティブ
(provider/subject)としてサービスへ渡す(サービスはauthの型をimportしない)。
ログはイベント名とcodeのみ — text・condition本文は出さない(08 §2.4)。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.intents.schema import ParserOutput
from latch.intents.service import IntentParseService, ParseResult

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
