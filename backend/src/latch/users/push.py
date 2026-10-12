"""プッシュ通知トークン登録の小サービス(05 v0.9・08 §2.6・backlog V-01)。

SurveyService/ConsentServiceと同型のCallable注入構造(routesとservice関数で
契約を固定・参照実装のmake_*はengine束ね・D1版はsvc_users.pyがSQL差し替え)。
登録はupsert: 同一トークンの別ユーザー再登録は所有権移動(05 v0.9 規定1)。
トークン値をログへ出さない・応答はtoken_last4マスクのみ(規定2)。
送信側(実FCM)はpush_mode=stubのまま=本表を参照しない(ws-9でreal化)。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import Clock
from latch.users.errors import (
    DependencyUnavailableError,
    PushTokenNotFoundError,
    UserNotFoundError,
    UsersError,
)

PLATFORMS = ("web",)  # MVPはWeb Pushのみ(05 v0.9・モバイル段階で値域追加)
TOKEN_MAX_LEN = 4096


@dataclass(frozen=True)
class PushTokenView:
    """応答本体(05 v0.9)。token全文は含めない(last4マスク)。"""

    token_last4: str
    platform: str
    registered_at: datetime


ExistsUserFn = Callable[[AccessTokenClaims], Awaitable[bool]]
UpsertFn = Callable[[AccessTokenClaims, str, str, datetime], Awaitable[PushTokenView]]
ListFn = Callable[[AccessTokenClaims], Awaitable[list[PushTokenView]]]
DeleteFn = Callable[[AccessTokenClaims, str], Awaitable[bool]]


class PushTokenService:
    """register(upsert)/ list / remove のユースケース。"""

    def __init__(
        self,
        *,
        clock: Clock,
        exists_user: ExistsUserFn,
        upsert: UpsertFn,
        list_tokens: ListFn,
        delete_token: DeleteFn,
    ) -> None:
        self._clock = clock
        self._exists_user = exists_user
        self._upsert = upsert
        self._list_tokens = list_tokens
        self._delete_token = delete_token

    async def register(
        self, *, claims: AccessTokenClaims, token: str, platform: str
    ) -> PushTokenView:
        """トークン登録(upsert・所有権移動)。値域検証(空・字数・platform)は
        routesのpydanticが担う(422 VALIDATION_ERROR)。"""
        try:
            if not await self._exists_user(claims):
                raise UserNotFoundError("user not found")
            return await self._upsert(claims, token, platform, self._clock.now())
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users push dependency unavailable"
            ) from exc

    async def list(self, *, claims: AccessTokenClaims) -> list[PushTokenView]:
        """登録済みトークン一覧(last4マスク・0件可)。"""
        try:
            if not await self._exists_user(claims):
                raise UserNotFoundError("user not found")
            return await self._list_tokens(claims)
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users push dependency unavailable"
            ) from exc

    async def remove(self, *, claims: AccessTokenClaims, token: str) -> None:
        """登録解除。自分のトークンでなければ404(存在探査に使えない形)。"""
        try:
            if not await self._exists_user(claims):
                raise UserNotFoundError("user not found")
            if not await self._delete_token(claims, token):
                raise PushTokenNotFoundError("push token not registered")
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users push dependency unavailable"
            ) from exc


def make_push_token_service(*, clock: Clock, engine: AsyncEngine) -> PushTokenService:
    """実SQL関数(text())を束ねてPushTokenServiceを構築(参照実装・PostgreSQL)。"""

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

    async def upsert(
        claims: AccessTokenClaims, token: str, platform: str, now: datetime
    ) -> PushTokenView:
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    _UPSERT_TOKEN,
                    {
                        "provider": claims.auth_provider,
                        "subject": claims.auth_subject,
                        "token": token,
                        "platform": platform,
                        "now": now,
                    },
                )
            ).first()
        assert row is not None  # exists_user前提で必ず1行(RETURNING)
        return PushTokenView(token_last4=row[0], platform=row[1], registered_at=row[2])

    async def list_tokens(claims: AccessTokenClaims) -> list[PushTokenView]:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    _SELECT_TOKENS,
                    {
                        "provider": claims.auth_provider,
                        "subject": claims.auth_subject,
                    },
                )
            ).all()
        return [
            PushTokenView(token_last4=r[0], platform=r[1], registered_at=r[2])
            for r in rows
        ]

    async def delete_token(claims: AccessTokenClaims, token: str) -> bool:
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    _DELETE_TOKEN,
                    {
                        "provider": claims.auth_provider,
                        "subject": claims.auth_subject,
                        "token": token,
                    },
                )
            ).first()
        return row is not None

    return PushTokenService(
        clock=clock,
        exists_user=exists_user,
        upsert=upsert,
        list_tokens=list_tokens,
        delete_token=delete_token,
    )


_SELECT_EXISTS = text("""
    SELECT EXISTS(
        SELECT 1 FROM users
         WHERE auth_provider = :provider AND auth_subject = :subject
    )
""")

# upsert(05 v0.9 規定1): ON CONFLICT(token)で所有権移動(user_id=登録者へ置換)。
# 応答のregistered_atはlast_seen_at(再登録=更新の時刻)。
_UPSERT_TOKEN = text("""
    INSERT INTO push_tokens
        (id, user_id, token, platform, created_at, updated_at, last_seen_at)
    SELECT gen_random_uuid(), u.id, :token, :platform,
           CAST(:now AS timestamptz), CAST(:now AS timestamptz),
           CAST(:now AS timestamptz)
      FROM users u
     WHERE u.auth_provider = :provider AND u.auth_subject = :subject
    ON CONFLICT (token) DO UPDATE
        SET user_id = EXCLUDED.user_id,
            platform = EXCLUDED.platform,
            updated_at = EXCLUDED.updated_at,
            last_seen_at = EXCLUDED.last_seen_at
    RETURNING right(token, 4), platform, last_seen_at
""")

_SELECT_TOKENS = text("""
    SELECT right(token, 4), platform, last_seen_at
      FROM push_tokens
     WHERE user_id IN (
         SELECT id FROM users
          WHERE auth_provider = :provider AND auth_subject = :subject
     )
     ORDER BY last_seen_at DESC, token
""")

# 削除は自分の行のみ(所有権移動済みトークンは0行=404)。
_DELETE_TOKEN = text("""
    DELETE FROM push_tokens
     WHERE token = :token
       AND user_id IN (
           SELECT id FROM users
            WHERE auth_provider = :provider AND auth_subject = :subject
       )
    RETURNING id
""")
