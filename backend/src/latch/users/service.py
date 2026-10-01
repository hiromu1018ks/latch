"""usersユースケース(M1 ws-1。design §2.1〜§2.5)。

永続化は text() 生SQLの関数(INSERT 1本+SELECT 1本)をCallableで注入する
(auth の user_lookup 注入と同型。ORMモデル・リポジトリ層の導入判断は
intents CRUD(ws-3 M1)に委ねる — design §2.1)。時刻列はClock由来の明示値
(DB時刻関数のDEFAULTは使わない)。検証順序は年齢(422)→INSERT(409/503)に
固定(design §2.2 — 17歳かつ登録済みならUNDER_AGE)。
M3 ws-6: 退会 delete_account(全Intentカスケード+ユーザー単位処理を
同期txで・design §2.3)を追加。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import Clock
from latch.intents.deletion import cascade_delete_intent
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

    sqlstate=23505(unique_violation)+ 対象制約名のときのみ UserExistsError。
    それ以外はNone(呼び出し側で再送出 → UserServiceが503へ包む — design §2.2)。

    exc.orig に入る例外は2つの形がありうる(どちらも検査する):
    - 生のasyncpg例外(sqlstate・constraint_name を直接持つ)
    - SQLAlchemy 2.1 asyncpg dialect のDBAPIラッパー(AsyncAdapt_asyncpg_dbapi.*。
      sqlstate は持つが constraint_name は持たず、生例外は driver_exception
      の先にある — 2026-09-27 compose ci-DB実測。ラッパー直参照のみだと
      重複登録が503化する)
    """
    orig = exc.orig
    sqlstate = getattr(orig, "sqlstate", None)
    constraint_name = getattr(orig, "constraint_name", None)
    if constraint_name is None:
        driver = getattr(orig, "driver_exception", None)
        if driver is not None:
            constraint_name = getattr(driver, "constraint_name", None)
            if sqlstate is None:
                sqlstate = getattr(driver, "sqlstate", None)
    if sqlstate != "23505":
        return None
    if constraint_name == _USERS_SUBJECT_CONSTRAINT:
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
UnitOfWork = Callable[[], AbstractAsyncContextManager[AsyncConnection]]
CascadeFn = Callable[[AsyncConnection, uuid.UUID, datetime], Awaitable[None]]


class UserService:
    """register / get_me / delete_account ユースケース(05 第5節・M3 ws-6)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        create_user: CreateUserFn,
        fetch_by_auth: FetchByAuthFn,
        uow: UnitOfWork | None = None,
        sessions=None,
        cascade: CascadeFn | None = None,
    ) -> None:
        self._clock = clock
        self._create_user = create_user
        self._fetch_by_auth = fetch_by_auth
        self._uow = uow
        self._sessions = sessions
        self._cascade = cascade

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

    async def delete_account(self, *, claims: AccessTokenClaims) -> None:
        """退会: 全Intentのカスケード+ユーザー単位処理を1txで(設計 §2.3案A)。

        応答204の時点で生データが消える(Worker稼働状態に非依存)。
        コミット後にセッション失効(Redis失効リスト・引用#13)。
        """
        try:
            await self._delete_account(claims=claims)
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("users dependency unavailable") from exc

    async def _delete_account(self, *, claims: AccessTokenClaims) -> None:
        if self._uow is None or self._sessions is None:
            # 失効しない退会は安全側でない(旧JWTが1時間有効のまま残る)
            raise DependencyUnavailableError("delete_account not wired")
        try:
            row = await self._fetch_by_auth(claims.auth_provider, claims.auth_subject)
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("users dependency unavailable") from exc
        if row is None:
            raise UserNotFoundError("user not found")
        now = self._clock.now()
        cascade = self._cascade or cascade_delete_intent
        async with self._uow() as conn:
            rows = (
                await conn.execute(_SELECT_ALL_INTENT_IDS, {"user_id": row.id})
            ).fetchall()
            for (iid,) in rows:
                await cascade(conn, _coerce_user_id(iid), now)
            await conn.execute(_DELETE_MESSAGES, {"user_id": row.id})
            await conn.execute(_DELETE_NOTIFICATIONS, {"user_id": row.id})
            await conn.execute(_ANONYMIZE_USER, {"user_id": row.id, "now": now})
        # uowコミット後に失効(引用#13。失効が先でも無害・logoutと同じ順序)
        await self._sessions.revoke_access(jti=claims.jti, exp=claims.exp, now=now)
        await self._sessions.revoke_family(sid=claims.sid)


def _coerce_user_id(value: object) -> uuid.UUID:
    """行のid値をUUIDへ正規化する。

    asyncpgはuuid列をUUID「インスタンス」で返す(uuid.UUID(row[0]) は
    AttributeErrorになる — M0 ws-3検証のtest-ci失敗1と同じ落ち穴)。
    auth/service.py と同じ対応(coreへ共有化せずauth不変を優先 — design §2.6)。
    """
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


# idはDEFAULT gen_random_uuid()に任せRETURNINGで受け取る(§2.5)。profileは
# text()経由ではSQLAlchemyの型変換が入らないためJSON文字列で渡してCASTする
_INSERT = text("""
    INSERT INTO users
        (display_name, profile, birth_date, auth_provider, auth_subject,
         created_at, updated_at)
    VALUES
        (:display_name, CAST(:profile AS jsonb), :birth_date, :auth_provider,
         :auth_subject, :created_at, :updated_at)
    RETURNING id
""")

# auth_provider+auth_subject はUNIQUE(uq_users_auth_provider_subject)なので高々1行
_SELECT = text("""
    SELECT id, display_name, profile, birth_date
    FROM users
    WHERE auth_provider = :provider AND auth_subject = :subject
""")

# 退会(M3 ws-6 design §2.3)。users行は残置し認証紐付けを切替(§2.4案A)
_SELECT_ALL_INTENT_IDS = text("""
    SELECT id FROM intents
    WHERE user_id = CAST(:user_id AS uuid)
    ORDER BY id
""")
_DELETE_MESSAGES = text("""
    DELETE FROM messages WHERE sender_id = CAST(:user_id AS uuid)
""")
_DELETE_NOTIFICATIONS = text("""
    DELETE FROM notifications WHERE user_id = CAST(:user_id AS uuid)
""")
_ANONYMIZE_USER = text("""
    UPDATE users
       SET display_name = '退会したユーザー',
           profile = '{}'::jsonb,
           auth_subject = 'deleted:' || id::text,
           updated_at = CAST(:now AS timestamptz)
     WHERE id = CAST(:user_id AS uuid)
""")


def make_user_service(
    *, clock: Clock, engine: AsyncEngine, sessions=None
) -> UserService:
    """実SQL関数(text())を束ねてUserServiceを構築する(design §2.5)。

    engineはクロージャで束縛する(UserService自身はengineを知らない)。
    INSERT衝突はUNIQUE制約で検出し、対象制約のみUserExistsErrorへ変換
    (それ以外のIntegrityErrorは再送出 → UserServiceが503へ包む — design §2.2)。
    M3 ws-6: uow/cascade/sessionsも束ねる(退会delete_account用・design §2.3)。
    """

    async def create_user(new_user: NewUser) -> uuid.UUID:
        try:
            async with engine.begin() as conn:
                row_id = await conn.scalar(
                    _INSERT,
                    {
                        "display_name": new_user.display_name,
                        "profile": json.dumps(new_user.profile),
                        "birth_date": new_user.birth_date,
                        "auth_provider": new_user.auth_provider,
                        "auth_subject": new_user.auth_subject,
                        "created_at": new_user.created_at,
                        "updated_at": new_user.updated_at,
                    },
                )
                return _coerce_user_id(row_id)
        except IntegrityError as exc:
            classified = classify_integrity_error(exc)
            if classified is not None:
                raise classified from exc
            raise

    async def fetch_by_auth(provider: str, subject: str) -> UserRow | None:
        async with engine.connect() as conn:
            result = await conn.execute(
                _SELECT, {"provider": provider, "subject": subject}
            )
            row = result.first()
            if row is None:
                return None
            profile = row.profile
            if isinstance(profile, str):
                # Review Focus #3: asyncpgのjsonbはstrで返る場合がある
                profile = json.loads(profile)
            return UserRow(
                id=_coerce_user_id(row.id),
                display_name=row.display_name,
                profile=profile,
                birth_date=row.birth_date,
            )

    return UserService(
        clock=clock,
        create_user=create_user,
        fetch_by_auth=fetch_by_auth,
        uow=engine.begin,
        sessions=sessions,
        cascade=cascade_delete_intent,
    )
