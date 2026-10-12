"""体感アンケートの回答保存・due判定の小サービス(05 v0.7・09 §2.3)。

ConsentServiceと同型のCallable注入構造(routesとservice関数で契約を固定)。
判定はすべてアクセス時(スケジューラなし・通知typeなし=05 v0.7配信契機)。
回答値・自由記述本文をログへ出さない(08 第2.4節)。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import Clock
from latch.users.errors import (
    DependencyUnavailableError,
    SurveyAlreadyAnsweredError,
    SurveyNotDueError,
    UserNotFoundError,
    UsersError,
)

# 05 v0.7 第2節: ラウンドは2値・4週=固定28日
SURVEY_ROUNDS = ("onboarding", "week4")
WEEK4_DELAY = timedelta(days=28)

Round = Literal["onboarding", "week4"]


@dataclass(frozen=True)
class RoundStatus:
    """GET /v1/users/me/survey のラウンド別応答本体(05 v0.7)。"""

    status: str  # "due" | "answered" | "not_due"
    available_at: datetime | None  # week4のみ(onboarding回答+28日・他はNone)


@dataclass(frozen=True)
class SurveyStatus:
    onboarding: RoundStatus
    week4: RoundStatus


@dataclass(frozen=True)
class SurveyAnswered:
    round: str
    answered_at: datetime


FetchRoundsFn = Callable[[AccessTokenClaims], Awaitable[dict[str, datetime]]]
HasIntentFn = Callable[[AccessTokenClaims], Awaitable[bool]]
ExistsFn = Callable[[AccessTokenClaims], Awaitable[bool]]
InsertFn = Callable[
    [AccessTokenClaims, str, dict, str | None, datetime], Awaitable[datetime | None]
]


def _week4_available(onboarded_at: datetime) -> datetime:
    return onboarded_at + WEEK4_DELAY


class SurveyService:
    """status(配信すべきラウンド判定)/ submit(回答保存・単発)のユースケース。"""

    def __init__(
        self,
        *,
        clock: Clock,
        fetch_rounds: FetchRoundsFn,
        has_activated_intent: HasIntentFn,
        exists_user: ExistsFn,
        insert_answer: InsertFn,
    ) -> None:
        self._clock = clock
        self._fetch_rounds = fetch_rounds
        self._has_activated_intent = has_activated_intent
        self._exists_user = exists_user
        self._insert_answer = insert_answer

    async def status(self, *, claims: AccessTokenClaims) -> SurveyStatus:
        """ラウンド別のdue/answered/not_due(05 v0.7 配信契機1〜2)。"""
        try:
            answered = await self._fetch_rounds(claims)
            if not answered and not await self._exists_user(claims):
                raise UserNotFoundError("user not found")
            if "onboarding" in answered:
                onboarding = RoundStatus(status="answered", available_at=None)
            elif await self._has_activated_intent(claims):
                onboarding = RoundStatus(status="due", available_at=None)
            else:
                onboarding = RoundStatus(status="not_due", available_at=None)

            if "week4" in answered:
                week4 = RoundStatus(status="answered", available_at=None)
            elif "onboarding" in answered:
                available = _week4_available(answered["onboarding"])
                due = self._clock.now() >= available
                week4 = RoundStatus(
                    status="due" if due else "not_due", available_at=available
                )
            else:
                week4 = RoundStatus(status="not_due", available_at=None)
            return SurveyStatus(onboarding=onboarding, week4=week4)
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users survey dependency unavailable"
            ) from exc

    async def submit(
        self,
        *,
        claims: AccessTokenClaims,
        round_: str,
        answers: dict,
        free_comment: str | None,
    ) -> SurveyAnswered:
        """回答の保存(ラウンド単位・初回のみ。競合は再検査で409へ)。"""
        try:
            answered = await self._fetch_rounds(claims)
            if not answered and not await self._exists_user(claims):
                raise UserNotFoundError("user not found")
            if round_ in answered:
                raise SurveyAlreadyAnsweredError("survey round already answered")
            await self._ensure_due(round_=round_, answered=answered, claims=claims)
            at = await self._insert_answer(
                claims, round_, answers, free_comment, self._clock.now()
            )
            if at is None:
                # ON CONFLICT DO NOTHING(競合)か対象user不在かを再検査で確定
                recheck = await self._fetch_rounds(claims)
                if round_ in recheck:
                    raise SurveyAlreadyAnsweredError("survey round already answered")
                raise UserNotFoundError("user not found")
            return SurveyAnswered(round=round_, answered_at=at)
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError(
                "users survey dependency unavailable"
            ) from exc

    async def _ensure_due(
        self, *, round_: str, answered: dict[str, datetime], claims: AccessTokenClaims
    ) -> None:
        if round_ not in SURVEY_ROUNDS:
            raise SurveyNotDueError("survey round unknown")
        if round_ == "onboarding":
            # 配信契機1: active化されたIntent(status != 'draft')の存在
            if not await self._has_activated_intent(claims):
                raise SurveyNotDueError("survey round not due")
        else:
            # 配信契機2: onboarding回答から28日(未回答者への再催促なし)
            onboarded_at = answered.get("onboarding")
            if onboarded_at is None:
                raise SurveyNotDueError("survey round not due")
            if self._clock.now() < _week4_available(onboarded_at):
                raise SurveyNotDueError("survey round not due")


def make_survey_service(*, clock: Clock, engine: AsyncEngine) -> SurveyService:
    """実SQL関数(text())を束ねてSurveyServiceを構築する(参照実装・PostgreSQL)。"""

    async def fetch_rounds(claims: AccessTokenClaims) -> dict[str, datetime]:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    _SELECT_ROUNDS,
                    {"provider": claims.auth_provider, "subject": claims.auth_subject},
                )
            ).all()
        return {row[0]: row[1] for row in rows}

    async def has_activated_intent(claims: AccessTokenClaims) -> bool:
        async with engine.connect() as conn:
            return bool(
                (
                    await conn.execute(
                        _SELECT_HAS_ACTIVATED,
                        {
                            "provider": claims.auth_provider,
                            "subject": claims.auth_subject,
                        },
                    )
                ).scalar()
            )

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

    async def insert_answer(
        claims: AccessTokenClaims,
        round_: str,
        answers: dict,
        free_comment: str | None,
        now: datetime,
    ) -> datetime | None:
        async with engine.begin() as conn:
            return await conn.scalar(
                _INSERT_ANSWER,
                {
                    "provider": claims.auth_provider,
                    "subject": claims.auth_subject,
                    "round": round_,
                    "answers": json.dumps(answers, ensure_ascii=False),
                    "free_comment": free_comment,
                    "now": now,
                },
            )

    return SurveyService(
        clock=clock,
        fetch_rounds=fetch_rounds,
        has_activated_intent=has_activated_intent,
        exists_user=exists_user,
        insert_answer=insert_answer,
    )


_SELECT_ROUNDS = text("""
    SELECT round, created_at FROM survey_responses
     WHERE user_id IN (
         SELECT id FROM users
          WHERE auth_provider = :provider AND auth_subject = :subject
     )
""")

# 配信契機1: active化されたIntent = status != 'draft'(matched/expired/cancelledも
# 「かつてactiveだった」痕跡として含む=05 v0.7)
_SELECT_HAS_ACTIVATED = text("""
    SELECT EXISTS(
        SELECT 1
          FROM intents i
          JOIN users u ON u.id = i.user_id
         WHERE u.auth_provider = :provider AND u.auth_subject = :subject
           AND i.status != 'draft'
    )
""")

_SELECT_EXISTS = text("""
    SELECT EXISTS(
        SELECT 1 FROM users
         WHERE auth_provider = :provider AND auth_subject = :subject
    )
""")

# user_idはprovider+subjectから解決(users行なし=0行=INSERTされない)。
# ON CONFLICT DO NOTHINGがUNIQUE(user_id, round)の単発配信をDBで強制する。
_INSERT_ANSWER = text("""
    INSERT INTO survey_responses
        (id, user_id, round, answers, free_comment, created_at)
    SELECT gen_random_uuid(), u.id, :round,
           CAST(:answers AS jsonb), :free_comment, CAST(:now AS timestamptz)
      FROM users u
     WHERE u.auth_provider = :provider AND u.auth_subject = :subject
    ON CONFLICT (user_id, round) DO NOTHING
    RETURNING created_at
""")
