"""POST /v1/sessions(05 §19・04 §2アクセス系基本計測)。

契約: 認証必須・request body空・INSERT(user_idと時刻のみ)・204。
users未登録は404(users系と同一挙動・05 §19に明記はないが05 §5の
USER_NOT_FOUNDで整合)。
Intentの文言を運ぶ列はテーブルに構造的に存在しない(05 §19「user_idと
時刻のみを記録し、Intentの文言を含まない」の構造的担保 — 0008参照)。
rate limit: ルータ単位でapi_rate_limitedを付す(v1全認証付きルートが持つ
アーキテクチャレベルの傘・M4 ws-4スーパーバイザー裁定2026-10-04。
当初計画の「付けない」から変更 — 04 §5・08 §5.4のAPI全体60req/分は
05 §19の契約規定とは別層。401→429の順は依存の内包で保証)。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import Clock
from latch.core.deps import get_clock
from latch.ratelimit.deps import api_rate_limited

obs_sessions_router = APIRouter(
    prefix="/v1/sessions",
    tags=["sessions"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4作法・M4 ws-4裁定)
)

_INSERT_SESSION = text("""
    INSERT INTO sessions (user_id, created_at)
    SELECT u.id, CAST(:now AS timestamptz)
    FROM users u
    WHERE u.auth_provider = :provider AND u.auth_subject = :subject
    RETURNING id
""")


class SessionStore:
    """sessionsテーブルへのINSERT(薄いストア・unitはスタブ注入)。"""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def record(
        self, provider: str, subject: str, now: datetime
    ) -> uuid.UUID | None:
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    _INSERT_SESSION,
                    {"provider": provider, "subject": subject, "now": now},
                )
            ).first()
        return row[0] if row is not None else None


def get_session_store(request: Request) -> SessionStore | object:
    """app.state.session_store注入を優先(テスト)・無ければ実ストア。"""
    store = getattr(request.app.state, "session_store", None)
    if store is not None:
        return store
    return SessionStore(request.app.state.db_engine)


def _error_body(code: str, message: str) -> dict:
    # 05 §5 エラー形式(main.pyの_error_bodyと同一形状)
    return {"error": {"code": code, "message": message, "details": None}}


@obs_sessions_router.post("", status_code=204)
async def create_session(
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    clock: Annotated[Clock, Depends(get_clock)],
    store: Annotated[object, Depends(get_session_store)],
) -> Response:
    body = await request.body()
    if body.strip():
        return JSONResponse(
            status_code=422,
            content=_error_body("VALIDATION_ERROR", "request body must be empty"),
        )
    user_id = await store.record(claims.auth_provider, claims.auth_subject, clock.now())
    if user_id is None:
        return JSONResponse(
            status_code=404,
            content=_error_body("USER_NOT_FOUND", "user is not registered"),
        )
    return Response(status_code=204)
