"""SurveyService(05 v0.7・体感アンケート)のunit試験。

due判定・week4の28日起点・409系・404・挿入競合(ON CONFLICT後の再検査)を
Callableスタブで検証する(実SQLはintegration/契約が担う・consentと同型)。
"""

from __future__ import annotations

import datetime as dt
from datetime import timedelta

import pytest

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.users.errors import (
    SurveyAlreadyAnsweredError,
    SurveyNotDueError,
    UserNotFoundError,
)
from latch.users.survey import WEEK4_DELAY, SurveyService

NOW = dt.datetime(2026, 10, 12, 9, 0, tzinfo=dt.UTC)


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sv-unit-1",
        jti="jti",
        sid="sid",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _make(
    *,
    rounds: dict[str, dt.datetime] | None = None,
    has_intent: bool = False,
    exists: bool = True,
    insert_conflicts: bool = False,
    now: dt.datetime = NOW,
) -> tuple[SurveyService, dict]:
    state: dict = {"rounds": dict(rounds or {}), "inserted": []}

    async def fetch_rounds(claims: AccessTokenClaims) -> dict[str, dt.datetime]:
        return dict(state["rounds"])

    async def has_activated_intent(claims: AccessTokenClaims) -> bool:
        return has_intent

    async def exists_user(claims: AccessTokenClaims) -> bool:
        return exists

    async def insert_answer(claims, round_, answers, free_comment, at):
        if insert_conflicts:
            # ON CONFLICT DO NOTHINGと同じ応答(0行)=再検査で当該roundが見える
            state["rounds"][round_] = at
            return None
        state["rounds"][round_] = at
        state["inserted"].append((round_, answers, free_comment))
        return at

    svc = SurveyService(
        clock=FakeClock(now),
        fetch_rounds=fetch_rounds,
        has_activated_intent=has_activated_intent,
        exists_user=exists_user,
        insert_answer=insert_answer,
    )
    return svc, state


# -- status ------------------------------------------------------------------


async def test_status_unknown_user_404():
    svc, _ = _make(exists=False)
    with pytest.raises(UserNotFoundError):
        await svc.status(claims=_claims())


async def test_status_all_not_due_without_intent():
    svc, _ = _make(has_intent=False)
    st = await svc.status(claims=_claims())
    assert st.onboarding.status == "not_due"
    assert st.onboarding.available_at is None
    assert st.week4.status == "not_due"
    assert st.week4.available_at is None


async def test_status_onboarding_due_with_activated_intent():
    svc, _ = _make(has_intent=True)
    st = await svc.status(claims=_claims())
    assert st.onboarding.status == "due"
    assert st.week4.status == "not_due"


async def test_status_answered_rounds_never_due_again():
    svc, _ = _make(rounds={"onboarding": NOW, "week4": NOW}, has_intent=True)
    st = await svc.status(claims=_claims())
    assert st.onboarding.status == "answered"
    assert st.week4.status == "answered"


async def test_status_week4_not_due_before_28d_with_available_at():
    answered = NOW - timedelta(days=10)
    svc, _ = _make(rounds={"onboarding": answered}, has_intent=True)
    st = await svc.status(claims=_claims())
    assert st.week4.status == "not_due"
    assert st.week4.available_at == answered + WEEK4_DELAY


async def test_status_week4_due_after_28d():
    svc, _ = _make(rounds={"onboarding": NOW - timedelta(days=28)}, has_intent=True)
    st = await svc.status(claims=_claims())
    assert st.week4.status == "due"
    # available_at = onboarding回答+28日(境界=ちょうど28日でdue)
    assert st.week4.available_at == NOW


async def test_status_week4_never_due_when_onboarding_unanswered():
    # 未回答者への再催促なし(05 v0.7 配信契機2)
    svc, _ = _make(has_intent=True)
    st = await svc.status(claims=_claims())
    assert st.week4.status == "not_due"
    assert st.week4.available_at is None


# -- submit ------------------------------------------------------------------


async def test_submit_onboarding_ok():
    svc, state = _make(has_intent=True)
    res = await svc.submit(
        claims=_claims(),
        round_="onboarding",
        answers={"convenience": 4},
        free_comment=None,
    )
    assert res.round == "onboarding"
    assert state["inserted"] == [("onboarding", {"convenience": 4}, None)]


async def test_submit_duplicate_409():
    svc, _ = _make(rounds={"onboarding": NOW}, has_intent=True)
    with pytest.raises(SurveyAlreadyAnsweredError):
        await svc.submit(
            claims=_claims(),
            round_="onboarding",
            answers={"convenience": 4},
            free_comment=None,
        )


async def test_submit_onboarding_without_intent_409_not_due():
    svc, _ = _make(has_intent=False)
    with pytest.raises(SurveyNotDueError):
        await svc.submit(
            claims=_claims(),
            round_="onboarding",
            answers={"convenience": 4},
            free_comment=None,
        )


async def test_submit_week4_too_early_409_not_due():
    svc, _ = _make(rounds={"onboarding": NOW - timedelta(days=3)}, has_intent=True)
    with pytest.raises(SurveyNotDueError):
        await svc.submit(
            claims=_claims(),
            round_="week4",
            answers={"convenience": 4, "continuation": 5},
            free_comment=None,
        )


async def test_submit_week4_after_28d_ok():
    svc, state = _make(rounds={"onboarding": NOW - timedelta(days=29)}, has_intent=True)
    res = await svc.submit(
        claims=_claims(),
        round_="week4",
        answers={"convenience": 4, "continuation": 5},
        free_comment="待つだけで届く",
    )
    assert res.round == "week4"
    assert state["inserted"][0][0] == "week4"
    assert state["inserted"][0][2] == "待つだけで届く"


async def test_submit_week4_without_onboarding_409_not_due():
    svc, _ = _make(has_intent=True)
    with pytest.raises(SurveyNotDueError):
        await svc.submit(
            claims=_claims(),
            round_="week4",
            answers={"convenience": 4, "continuation": 5},
            free_comment=None,
        )


async def test_submit_unknown_user_404():
    svc, _ = _make(exists=False)
    with pytest.raises(UserNotFoundError):
        await svc.submit(
            claims=_claims(),
            round_="onboarding",
            answers={"convenience": 4},
            free_comment=None,
        )


async def test_submit_insert_race_conflict_recheck_409():
    # 挿入が競合し再検査で当該roundが既にある=二重回答(UNIQUE(user_id,round)強制)
    svc, _ = _make(has_intent=True, insert_conflicts=True)
    with pytest.raises(SurveyAlreadyAnsweredError):
        await svc.submit(
            claims=_claims(),
            round_="onboarding",
            answers={"convenience": 4},
            free_comment=None,
        )
