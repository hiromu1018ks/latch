"""usersルータ(M1 ws-1。05 第5節 認証・ユーザー系)。

POST /v1/users(初回登録・201)と GET /v1/users/me(本人のみ・200)。
DELETE /v1/users/me(退会・204・M3 ws-6 design §2.3)。
ルータ単位で require_authenticated を付す(C3の強制方法 — 05 第5節冒頭)。
profileの正規化(bio=None → bioキーなし)はこの層で行う(design §2.7)。
ログはイベント名と結果/コードのみ — subject・claim値・display_nameは出さない
(08 第2.4節)。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.ratelimit.deps import api_rate_limited
from latch.users.consent import ConsentService, ConsentState
from latch.users.service import UserCreated, UserMe, UserService
from latch.users.survey import SurveyAnswered, SurveyService, SurveyStatus

logger = logging.getLogger("latch.users")

users_router = APIRouter(
    prefix="/v1/users",
    tags=["users"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4)
)


def get_users_service(request: Request) -> UserService:
    """app.state.users_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.users_service


def get_consent_service(request: Request) -> ConsentService:
    """app.state.consent_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.consent_service


def get_survey_service(request: Request) -> SurveyService:
    """app.state.survey_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.survey_service


def get_ab_service(request: Request):
    """app.state.ab_service へのアクセス(lifespanまたはテスト注入で載る・v0.8)。"""
    return request.app.state.ab_service


class ConsentStateResponse(BaseModel):
    """GET/POST /v1/users/me/consent の200応答(設計§2.3)。"""

    consented_at: datetime | None


class UserProfileInput(BaseModel):
    """登録リクエストのprofileオブジェクト(bioのみ — 05 第5節)。"""

    bio: str | None = None


class UserCreateRequest(BaseModel):
    # 上限なし・文字種検証なし(docsに規定なし — design §2.7)
    display_name: str = Field(min_length=1)  # 空文字=必須欠落相当(422)
    birth_date: date  # YYYY-MM-DD以外は422(既存ハンドラがenvelope化)
    profile: UserProfileInput | None = None


class UserCreatedBody(BaseModel):
    id: uuid.UUID
    display_name: str


class UserCreatedResponse(BaseModel):
    """POST /v1/users の201応答(05 第5節・入れ子)。"""

    user: UserCreatedBody


class UserMeResponse(BaseModel):
    """GET /v1/users/me の200応答(05 第5節)。birth_dateは本人のみに返す。"""

    id: uuid.UUID
    display_name: str
    profile: dict
    birth_date: date
    profile_complete: bool


@users_router.post("", status_code=201, response_model=UserCreatedResponse)
async def register_users(
    body: UserCreateRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[UserService, Depends(get_users_service)],
) -> UserCreatedResponse:
    """初回登録。auth_provider/auth_subject はJWT claimに紐付く(05 第5節)。"""
    bio = body.profile.bio if body.profile is not None else None
    profile = {"bio": bio} if bio is not None else {}
    created: UserCreated = await svc.register(
        claims=claims,
        display_name=body.display_name,
        birth_date=body.birth_date,
        profile=profile,
    )
    logger.info("users.register ok")
    return UserCreatedResponse(
        user=UserCreatedBody(id=created.id, display_name=created.display_name)
    )


@users_router.get("/me", response_model=UserMeResponse)
async def get_users_me(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[UserService, Depends(get_users_service)],
) -> UserMeResponse:
    """本人のみ(05 第5節)。me応答にjti/sid等のセッション情報を含めない。"""
    me: UserMe = await svc.get_me(claims=claims)
    logger.info("users.me ok")
    return UserMeResponse(
        id=me.id,
        display_name=me.display_name,
        profile=me.profile,
        birth_date=me.birth_date,
        profile_complete=me.profile_complete,
    )


@users_router.delete("/me", status_code=204)
async def delete_users_me(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[UserService, Depends(get_users_service)],
) -> None:
    """退会(M3 ws-6・design §2.3)。全Intentカスケード+ユーザー単位処理を
    1トランザクションで同期実行し、コミット後にセッションを失効する。"""
    await svc.delete_account(claims=claims)
    logger.info("users.delete_me ok")


@users_router.post("/me/consent", response_model=ConsentStateResponse)
async def record_consent(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[ConsentService, Depends(get_consent_service)],
) -> ConsentStateResponse:
    """同意の記録(冪等・後勝ちで時刻更新。08 D-14)。ログはイベント名のみ。"""
    state: ConsentState = await svc.record(claims=claims)
    logger.info("users.consent_record ok")
    return ConsentStateResponse(consented_at=state.consented_at)


@users_router.get("/me/consent", response_model=ConsentStateResponse)
async def get_consent(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[ConsentService, Depends(get_consent_service)],
) -> ConsentStateResponse:
    """同意状態(未同意も200 null。08 D-14「設定画面から常に再確認できる」)。"""
    state: ConsentState = await svc.get(claims=claims)
    logger.info("users.consent_state ok")
    return ConsentStateResponse(consented_at=state.consented_at)


# -- 体感アンケート(05 v0.7・09 §2.3) ---------------------------------------


class SurveyAnswersInput(BaseModel):
    """5段階設問の値(1〜5)。round別の必須/禁止はSurveySubmitRequestで検査。"""

    convenience: int = Field(ge=1, le=5)  # 便利さ(仮説1)
    continuation: int | None = Field(
        default=None, ge=1, le=5
    )  # 継続意向(仮説5・week4のみ)


class SurveySubmitRequest(BaseModel):
    round: Literal["onboarding", "week4"]
    answers: SurveyAnswersInput
    # week4のみ任意(空白のみはNoneへ正規化。onboardingへの付与は422)
    free_comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _round_shape(self) -> SurveySubmitRequest:
        if self.round == "onboarding":
            if self.answers.continuation is not None:
                raise ValueError("continuation is only accepted for week4")
            if self.free_comment is not None:
                raise ValueError("free_comment is only accepted for week4")
        elif self.answers.continuation is None:
            raise ValueError("continuation is required for week4")
        return self


class RoundStatusResponse(BaseModel):
    status: Literal["due", "answered", "not_due"]
    available_at: datetime | None


class SurveyStatusResponse(BaseModel):
    onboarding: RoundStatusResponse
    week4: RoundStatusResponse


class SurveyAnsweredBody(BaseModel):
    round: str
    answered_at: datetime


class SurveyAnsweredResponse(BaseModel):
    survey: SurveyAnsweredBody


@users_router.get("/me/survey", response_model=SurveyStatusResponse)
async def get_survey_status(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[SurveyService, Depends(get_survey_service)],
) -> SurveyStatusResponse:
    """ラウンド別のdue/answered判定(配信すべきラウンドの判定用・アクセス時判定)。"""
    state: SurveyStatus = await svc.status(claims=claims)
    logger.info("users.survey_status ok")
    return SurveyStatusResponse(
        onboarding=RoundStatusResponse(
            status=state.onboarding.status, available_at=state.onboarding.available_at
        ),
        week4=RoundStatusResponse(
            status=state.week4.status, available_at=state.week4.available_at
        ),
    )


@users_router.post("/me/survey", status_code=201, response_model=SurveyAnsweredResponse)
async def submit_survey(
    body: SurveySubmitRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[SurveyService, Depends(get_survey_service)],
) -> SurveyAnsweredResponse:
    """回答の保存(ラウンド単位・単発)。ログはroundのみ(回答値・自由記述は出さない)。"""
    free_comment = body.free_comment.strip() or None if body.free_comment else None
    answers: dict = {"convenience": body.answers.convenience}
    if body.answers.continuation is not None:
        answers["continuation"] = body.answers.continuation
    answered: SurveyAnswered = await svc.submit(
        claims=claims,
        round_=body.round,
        answers=answers,
        free_comment=free_comment,
    )
    logger.info("users.survey_submit ok round=%s", body.round)
    return SurveyAnsweredResponse(
        survey=SurveyAnsweredBody(
            round=answered.round, answered_at=answered.answered_at
        )
    )


class AbAssignmentBody(BaseModel):
    experiment: str
    variant: str
    assigned_at: datetime


class AbAssignmentsResponse(BaseModel):
    assignments: list[AbAssignmentBody]


@users_router.get("/me/ab", response_model=AbAssignmentsResponse)
async def get_my_ab_assignments(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[object, Depends(get_ab_service)],
) -> AbAssignmentsResponse:
    """自分のA/B割付一覧(05 v0.8・読み取り専用・割付を作らない・UIなし)。"""
    views = await svc.assignments_for_user(claims)
    logger.info("users.ab_assignments ok n=%d", len(views))
    return AbAssignmentsResponse(
        assignments=[
            AbAssignmentBody(
                experiment=v.experiment, variant=v.variant, assigned_at=v.assigned_at
            )
            for v in views
        ]
    )
