"""usersユースケース(M1 ws-1。design §2.1〜§2.5)。

永続化は text() 生SQLの関数(INSERT 1本+SELECT 1本)をCallableで注入する
(auth の user_lookup 注入と同型。ORMモデル・リポジトリ層の導入判断は
intents CRUD(ws-3 M1)に委ねる — design §2.1)。時刻列はClock由来の明示値
(DB時刻関数のDEFAULTは使わない)。検証順序は年齢(422)→INSERT(409/503)に
固定(design §2.2 — 17歳かつ登録済みならUNDER_AGE)。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.exc import IntegrityError

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import Clock
from latch.users.errors import (
    DependencyUnavailableError,
    UnderAgeError,
    UserExistsError,
    UserNotFoundError,
    UsersError,
)


def age_years(birth_date: date, today: date) -> int:
    """満年齢(design §2.3)。(月, 日)タプル比較 — 誕生日当日に加算。

    2月29日生まれは、その暦日に2月29日を持たない年は3月1日に加算される
    (自己申告値のため1日の差は許容して比較を単純に保つ)。
    """
    return (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )


# users表のUNIQUE制約名(alembic 0001)。IntegrityError分類に使う
_USERS_SUBJECT_CONSTRAINT = "uq_users_auth_provider_subject"


def classify_integrity_error(exc: IntegrityError) -> UsersError | None:
    """UNIQUE(auth_provider, auth_subject)違反のみ409 USER_EXISTSへ変換。

    asyncpgの例外(exc.orig)は sqlstate と constraint_name 属性を持つ。
    sqlstate=23505(unique_violation)+ 対象制約名のときのみ UserExistsError。
    それ以外はNone(呼び出し側で再送出 → UserServiceが503へ包む — design §2.2)。
    """
    orig = exc.orig
    if getattr(orig, "sqlstate", None) != "23505":
        return None
    if getattr(orig, "constraint_name", None) == _USERS_SUBJECT_CONSTRAINT:
        return UserExistsError("user already registered")
    return None


@dataclass(frozen=True)
class NewUser:
    """registerの中間表現(INSERT列・時刻はClock由来の明示値 — design §2.5)。"""

    display_name: str
    birth_date: date
    profile: dict
    auth_provider: str
    auth_subject: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UserCreated:
    """POST /v1/users の201応答本体 {"user": {...}}(05 第5節)。"""

    id: uuid.UUID
    display_name: str


@dataclass(frozen=True)
class UserRow:
    """fetch_by_auth の戻り(SELECT全列 — design §3.1)。"""

    id: uuid.UUID
    display_name: str
    profile: dict
    birth_date: date


@dataclass(frozen=True)
class UserMe:
    """GET /v1/users/me の200応答本体(05 第5節)。profile_completeは常にTrue。"""

    id: uuid.UUID
    display_name: str
    profile: dict
    birth_date: date
    profile_complete: bool


CreateUserFn = Callable[[NewUser], Awaitable[uuid.UUID]]
FetchByAuthFn = Callable[[str, str], Awaitable[UserRow | None]]


class UserService:
    """register / get_me ユースケース(05 第5節)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        create_user: CreateUserFn,
        fetch_by_auth: FetchByAuthFn,
    ) -> None:
        self._clock = clock
        self._create_user = create_user
        self._fetch_by_auth = fetch_by_auth

    async def register(
        self,
        *,
        claims: AccessTokenClaims,
        display_name: str,
        birth_date: date,
        profile: dict,
    ) -> UserCreated:
        """初回登録。User行はJWT claim(auth_provider/auth_subject)に紐けて生成。"""
        try:
            return await self._register(
                claims=claims,
                display_name=display_name,
                birth_date=birth_date,
                profile=profile,
            )
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("users dependency unavailable") from exc

    async def _register(
        self,
        *,
        claims: AccessTokenClaims,
        display_name: str,
        birth_date: date,
        profile: dict,
    ) -> UserCreated:
        if age_years(birth_date, self._clock.jst_date()) < 18:
            raise UnderAgeError("under 18 years old")
        now = self._clock.now()
        new_user = NewUser(
            display_name=display_name,
            birth_date=birth_date,
            profile=profile,
            auth_provider=claims.auth_provider,
            auth_subject=claims.auth_subject,
            created_at=now,
            updated_at=now,
        )
        user_id = await self._create_user(new_user)
        return UserCreated(id=user_id, display_name=display_name)

    async def get_me(self, *, claims: AccessTokenClaims) -> UserMe:
        """本人の行を返す。行なし=初回登録待ち → 404(design §2.4)。"""
        try:
            row = await self._fetch_by_auth(claims.auth_provider, claims.auth_subject)
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("users dependency unavailable") from exc
        if row is None:
            raise UserNotFoundError("user not found")
        return UserMe(
            id=row.id,
            display_name=row.display_name,
            profile=row.profile,
            birth_date=row.birth_date,
            profile_complete=True,  # 行の存在=true(design §1.2-9)
        )
