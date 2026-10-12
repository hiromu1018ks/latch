"""users・consentのD1版構築(本体実装)。

UserService・ConsentService の本体クラスをそのまま再利用し、
永続化Callable(create_user/fetch_by_auth/record_consent/...)だけD1 SQLへ
差し替える(main.py lifespanがengine束ねで行っているのと同じ注入構造)。

退会(delete_account)は参照実装の「全Intent cascade→messages/notifications
削除→匿名化→session失効」を、D1では atomic.apply_safety(account_delete)
(匿名化・session失効・open latch取消を1 batchで確定)+ 前段の
messages/notifications削除で構成する。
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from datetime import date, datetime

from latch.adapters.d1.atomic import D1AtomicRepository
from latch.auth.tokens import AccessTokenClaims
from latch.domain.time import from_micros, micros
from latch.users.consent import ConsentService
from latch.users.errors import UserExistsError, UserNotFoundError
from latch.users.push import PushTokenService, PushTokenView
from latch.users.service import NewUser, UserRow, UserService
from latch.users.survey import SurveyService

from latch_worker.gjson import loads_json


def _coerce_uuid(value) -> uuid_mod.UUID:
    if isinstance(value, uuid_mod.UUID):
        return value
    return uuid_mod.UUID(str(value))


async def _fetch_user_row(db, provider: str, subject: str) -> UserRow | None:
    row = (
        await db.prepare(
            "SELECT id, display_name, profile, birth_date, consent_at FROM users"
            " WHERE auth_provider = ? AND auth_subject = ?"
        )
        .bind(provider, subject)
        .first()
    )
    if row is None:
        return None
    return UserRow(
        id=_coerce_uuid(row["id"]),
        display_name=row["display_name"],
        profile=loads_json(row["profile"], {}),
        birth_date=date.fromisoformat(str(row["birth_date"])),
    )


class D1UsersFacade(UserService):
    """UserService本体 + D1版delete_account(参照実装と同じ業務結果)。

    register/get_me は注入したD1関数で本体メソッドをそのまま使用する。
    """

    def __init__(self, *, clock, db, atomic: D1AtomicRepository, sessions) -> None:
        async def create_user(new_user: NewUser) -> uuid_mod.UUID:
            user_id = str(uuid_mod.uuid4())
            try:
                await db.batch(
                    [
                        db.prepare(
                            "INSERT INTO users"
                            " (id, display_name, profile, birth_date,"
                            "  auth_provider, auth_subject, created_at, updated_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                        ).bind(
                            user_id,
                            new_user.display_name,
                            json.dumps(new_user.profile, ensure_ascii=True),
                            new_user.birth_date.isoformat(),
                            new_user.auth_provider,
                            new_user.auth_subject,
                            micros(new_user.created_at),
                            micros(new_user.updated_at),
                        )
                    ]
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if "UNIQUE" in message and (
                    "auth_provider" in message or "auth_subject" in message
                ):
                    raise UserExistsError("user already registered") from exc
                raise
            return uuid_mod.UUID(user_id)

        async def fetch_by_auth(provider: str, subject: str) -> UserRow | None:
            return await _fetch_user_row(db, provider, subject)

        super().__init__(
            clock=clock,
            create_user=create_user,
            fetch_by_auth=fetch_by_auth,
        )
        self._d1 = db
        self._atomic = atomic
        self._sessions = sessions

    async def delete_account(self, *, claims: AccessTokenClaims) -> None:
        """退会(参照実装と同じ応答意味: 404→204・退会後は旧JWT/subject無効)。"""
        from latch.users.errors import (
            DependencyUnavailableError,
            UserNotFoundError,
        )

        try:
            row = await _fetch_user_row(self._d1, claims.auth_provider, claims.auth_subject)
            if row is None:
                raise UserNotFoundError("user not found")
            now = self._clock.now()
            user_id = str(row.id)
            # messages/notifications/push_tokens削除(参照実装の単位。
            # blocks/reportsは保持・users行は残置のためpush_tokensは明示削除)
            await self._d1.batch(
                [
                    self._d1.prepare(
                        "DELETE FROM messages WHERE sender_id = ?"
                    ).bind(user_id),
                    self._d1.prepare(
                        "DELETE FROM notifications WHERE user_id = ?"
                    ).bind(user_id),
                    self._d1.prepare(
                        "DELETE FROM push_tokens WHERE user_id = ?"
                    ).bind(user_id),
                ]
            )
            result = await self._atomic.apply_safety(
                str(uuid_mod.uuid4()),
                {"kind": "account_delete", "user_id": user_id},
                now,
            )
            if result.status.value != "applied":
                raise UserNotFoundError("user not found")
            # uowコミット後の失効(引用#13・logoutと同じ順序)
            await self._sessions.revoke_access(
                jti=claims.jti, exp=claims.exp, now=now
            )
            await self._sessions.revoke_family(sid=claims.sid)
        except (UserNotFoundError, UserExistsError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise DependencyUnavailableError(
                "users dependency unavailable"
            ) from exc


def build_consent_service(db, clock) -> ConsentService:
    """ConsentService本体へD1関数を注入(users.consent_at 1行UPDATE/SELECT)。"""

    async def record_consent(claims: AccessTokenClaims, now: datetime):
        row = (
            await db.prepare(
                "UPDATE users SET consent_at = ?, updated_at = ?"
                " WHERE auth_provider = ? AND auth_subject = ?"
                " RETURNING consent_at"
            )
            .bind(micros(now), micros(now), claims.auth_provider, claims.auth_subject)
            .first()
        )
        if row is None:
            return None
        from latch.domain.time import from_micros

        return from_micros(int(row["consent_at"]))

    async def fetch_consent(claims: AccessTokenClaims):
        row = (
            await db.prepare(
                "SELECT consent_at FROM users"
                " WHERE auth_provider = ? AND auth_subject = ?"
            )
            .bind(claims.auth_provider, claims.auth_subject)
            .first()
        )
        if row is None or row["consent_at"] is None:
            return None
        from latch.domain.time import from_micros

        return from_micros(int(row["consent_at"]))

    async def exists_user(claims: AccessTokenClaims) -> bool:
        row = (
            await db.prepare(
                "SELECT 1 AS x FROM users"
                " WHERE auth_provider = ? AND auth_subject = ?"
            )
            .bind(claims.auth_provider, claims.auth_subject)
            .first()
        )
        return row is not None

    async def consented(user_id: uuid_mod.UUID) -> bool:
        row = (
            await db.prepare(
                "SELECT 1 AS x FROM users WHERE id = ? AND consent_at IS NOT NULL"
            )
            .bind(str(user_id))
            .first()
        )
        return row is not None

    return ConsentService(
        clock=clock,
        record_consent=record_consent,
        fetch_consent=fetch_consent,
        consented=consented,
        exists_fn=exists_user,
    )


def build_survey_service(db, clock) -> SurveyService:
    """SurveyService本体へD1関数を注入(体感アンケート・05 v0.7)。

    due判定の規則(onboarding=active化Intent存在・week4=onboarding回答+28日)
    は本体SurveyServiceが持ち、D1側は永続化Callableのみ(consentと同型)。
    """

    async def fetch_rounds(claims: AccessTokenClaims) -> dict:
        rows = (
            await db.prepare(
                "SELECT round, created_at FROM survey_responses"
                " WHERE user_id = (SELECT id FROM users"
                "  WHERE auth_provider = ? AND auth_subject = ?)"
            )
            .bind(claims.auth_provider, claims.auth_subject)
            .all()
        )
        return {row["round"]: from_micros(int(row["created_at"])) for row in rows}

    async def has_activated_intent(claims: AccessTokenClaims) -> bool:
        row = (
            await db.prepare(
                "SELECT 1 AS x FROM intents i"
                " JOIN users u ON u.id = i.user_id"
                " WHERE u.auth_provider = ? AND u.auth_subject = ?"
                "   AND i.status != 'draft' LIMIT 1"
            )
            .bind(claims.auth_provider, claims.auth_subject)
            .first()
        )
        return row is not None

    async def exists_user(claims: AccessTokenClaims) -> bool:
        row = (
            await db.prepare(
                "SELECT 1 AS x FROM users"
                " WHERE auth_provider = ? AND auth_subject = ?"
            )
            .bind(claims.auth_provider, claims.auth_subject)
            .first()
        )
        return row is not None

    async def insert_answer(claims, round_, answers, free_comment, now):
        import json as json_mod
        import uuid as uuid_mod

        row = (
            await db.prepare(
                "INSERT INTO survey_responses"
                " (id, user_id, round, answers, free_comment, created_at)"
                " SELECT ?, u.id, ?, ?, ?, ?"
                "  FROM users u"
                " WHERE u.auth_provider = ? AND u.auth_subject = ?"
                " ON CONFLICT (user_id, round) DO NOTHING"
                " RETURNING created_at"
            )
            .bind(
                str(uuid_mod.uuid4()),
                round_,
                json_mod.dumps(answers, ensure_ascii=False),
                free_comment,
                micros(now),
                claims.auth_provider,
                claims.auth_subject,
            )
            .first()
        )
        if row is None:
            return None
        return from_micros(int(row["created_at"]))

    return SurveyService(
        clock=clock,
        fetch_rounds=fetch_rounds,
        has_activated_intent=has_activated_intent,
        exists_user=exists_user,
        insert_answer=insert_answer,
    )


def build_push_token_service(db, clock) -> PushTokenService:
    """PushTokenService本体へD1関数を注入(05 v0.9・V-01)。

    upsert=所有権移動の規則は本体PushTokenServiceが持つIF契約どおり
    (ON CONFLICT(token) DO UPDATE)。時刻はmicros整数。
    """

    async def exists_user(claims: AccessTokenClaims) -> bool:
        row = (
            await db.prepare(
                "SELECT 1 AS x FROM users"
                " WHERE auth_provider = ? AND auth_subject = ?"
            )
            .bind(claims.auth_provider, claims.auth_subject)
            .first()
        )
        return row is not None

    async def upsert(claims, token, platform, now):
        import uuid as uuid_mod

        row = (
            await db.prepare(
                "INSERT INTO push_tokens"
                " (id, user_id, token, platform, created_at, updated_at, last_seen_at)"
                " SELECT ?, u.id, ?, ?, ?, ?, ?"
                "  FROM users u"
                " WHERE u.auth_provider = ? AND u.auth_subject = ?"
                " ON CONFLICT (token) DO UPDATE"
                "  SET user_id = excluded.user_id, platform = excluded.platform,"
                "      updated_at = excluded.updated_at,"
                "      last_seen_at = excluded.last_seen_at"
                " RETURNING substr(token, -4) AS last4, platform, last_seen_at"
            )
            .bind(
                str(uuid_mod.uuid4()),
                token,
                platform,
                micros(now),
                micros(now),
                micros(now),
                claims.auth_provider,
                claims.auth_subject,
            )
            .first()
        )
        if row is None:
            raise UserNotFoundError("user not found")
        return PushTokenView(
            token_last4=row["last4"],
            platform=row["platform"],
            registered_at=from_micros(int(row["last_seen_at"])),
        )

    async def list_tokens(claims: AccessTokenClaims) -> list:
        rows = (
            await db.prepare(
                "SELECT substr(token, -4) AS last4, platform, last_seen_at"
                " FROM push_tokens"
                " WHERE user_id = (SELECT id FROM users"
                "  WHERE auth_provider = ? AND auth_subject = ?)"
                " ORDER BY last_seen_at DESC, token"
            )
            .bind(claims.auth_provider, claims.auth_subject)
            .all()
        )
        return [
            PushTokenView(
                token_last4=row["last4"],
                platform=row["platform"],
                registered_at=from_micros(int(row["last_seen_at"])),
            )
            for row in rows
        ]

    async def delete_token(claims: AccessTokenClaims, token: str) -> bool:
        row = (
            await db.prepare(
                "DELETE FROM push_tokens"
                " WHERE token = ? AND user_id = (SELECT id FROM users"
                "  WHERE auth_provider = ? AND auth_subject = ?)"
                " RETURNING id"
            )
            .bind(token, claims.auth_provider, claims.auth_subject)
            .first()
        )
        return row is not None

    return PushTokenService(
        clock=clock,
        exists_user=exists_user,
        upsert=upsert,
        list_tokens=list_tokens,
        delete_token=delete_token,
    )
