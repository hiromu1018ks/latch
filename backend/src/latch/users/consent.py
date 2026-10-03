"""consent記録・取得の小サービス(M4 ws-5・design §2.1・§2.3・08 D-14)。

users.consent_at(NULL=未同意)へのUPDATE 1本+SELECT 1本+consented判定
SELECT 1本。make_user_serviceと同型のCallable注入(UserServiceへの統合は
不採用 — 契約をroutesとservice関数で固定するため独立モジュールとする)。
時刻はClock由来の明示値のみ。取り消しは非実装(設計§6-2)。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import Clock
from latch.users.errors import (
    DependencyUnavailableError,
    UserNotFoundError,
    UsersError,
)


@dataclass(frozen=True)
class ConsentState:
    """GET/POST /v1/users/me/consentの200応答本体(design §2.3)。"""

    consented_at: datetime | None  # None=未同意


RecordConsentFn = Callable[[AccessTokenClaims, datetime], Awaitable[datetime | None]]
FetchConsentFn = Callable[[AccessTokenClaims], Awaitable[datetime | None]]
ConsentedFn = Callable[[uuid.UUID], Awaitable[bool]]
ExistsFn = Callable[[AccessTokenClaims], Awaitable[bool]]


class ConsentService:
    """record / get / consented のユースケース(design §2.3)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        record_consent: RecordConsentFn,
        fetch_consent: FetchConsentFn,
        consented: ConsentedFn,
        exists_fn: ExistsFn,
    ) -> None:
        self._clock = clock
        self._record_consent = record_consent
        self._fetch_consent = fetch_consent
        self._consented_fn = consented
        self._exists_fn = exists_fn

    async def record(self, *, claims: AccessTokenClaims) -> ConsentState:
        """同意の記録(冪等・後勝ちで時刻更新。08 D-14)。"""
        try:
            at = await self._record_consent(claims, self._clock.now())
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users consent dependency unavailable"
            ) from exc
        if at is None:
            raise UserNotFoundError("user not found")
        return ConsentState(consented_at=at)

    async def get(self, *, claims: AccessTokenClaims) -> ConsentState:
        """同意状態(未同意もConsentState(None)・行なしは404)。"""
        try:
            at = await self._fetch_consent(claims)
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users consent dependency unavailable"
            ) from exc
        if at is None and not await self._row_exists(claims):
            raise UserNotFoundError("user not found")
        return ConsentState(consented_at=at)

    async def consented(self, user_id: uuid.UUID) -> bool:
        """user_id単位の同意判定(intents parseから消費する・設計§2.2第1層)。"""
        try:
            return await self._consented_fn(user_id)
        except Exception as exc:
            raise DependencyUnavailableError(
                "users consent dependency unavailable"
            ) from exc

    async def _row_exists(self, claims: AccessTokenClaims) -> bool:
        """SELECT consent_atのNoneが「未同意」と「行なし」のどちらかを判定。"""
        try:
            return await self._exists_fn(claims)
        except Exception as exc:
            raise DependencyUnavailableError(
                "users consent dependency unavailable"
            ) from exc


def make_consent_service(*, clock: Clock, engine: AsyncEngine) -> ConsentService:
    """実SQL関数(text())を束ねてConsentServiceを構築する(design §2.1)。"""

    async def record_consent(
        claims: AccessTokenClaims, now: datetime
    ) -> datetime | None:
        async with engine.begin() as conn:
            return await conn.scalar(
                _UPDATE_CONSENT,
                {
                    "now": now,
                    "provider": claims.auth_provider,
                    "subject": claims.auth_subject,
                },
            )

    async def fetch_consent(claims: AccessTokenClaims) -> datetime | None:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    _SELECT_CONSENT,
                    {
                        "provider": claims.auth_provider,
                        "subject": claims.auth_subject,
                    },
                )
            ).scalar()

    async def exists_user(claims: AccessTokenClaims) -> bool:
        async with engine.connect() as conn:
            return bool(
                (
                    await conn.execute(
                        _SELECT_EXISTS,
                        {
                            "provider": claims.auth_provider,
                            "subject": claims.auth_subject,
                        },
                    )
                ).scalar()
            )

    async def consented(user_id: uuid.UUID) -> bool:
        async with engine.connect() as conn:
            return bool(
                (await conn.execute(_SELECT_CONSENTED, {"user_id": user_id})).scalar()
            )

    return ConsentService(
        clock=clock,
        record_consent=record_consent,
        fetch_consent=fetch_consent,
        consented=consented,
        exists_fn=exists_user,
    )


# auth_provider+auth_subjectはUNIQUE(0001)なので高々1行。後勝ち=常にUPDATE
_UPDATE_CONSENT = text("""
    UPDATE users
       SET consent_at = CAST(:now AS timestamptz),
           updated_at = CAST(:now AS timestamptz)
     WHERE auth_provider = :provider AND auth_subject = :subject
    RETURNING consent_at
""")

_SELECT_CONSENT = text("""
    SELECT consent_at FROM users
     WHERE auth_provider = :provider AND auth_subject = :subject
""")

_SELECT_EXISTS = text("""
    SELECT EXISTS(
        SELECT 1 FROM users
         WHERE auth_provider = :provider AND auth_subject = :subject
    )
""")

_SELECT_CONSENTED = text("""
    SELECT EXISTS(
        SELECT 1 FROM users
         WHERE id = CAST(:user_id AS uuid) AND consent_at IS NOT NULL
    )
""")
