"""05 §5保存API契約の入力検証モデル(design §2.3〜§2.4・§3.1)。

parseのParserOutput(schema.py)とは別契約。1契約1モデル: 全フィールド
OptionalのStructuredIntentInputをactive/draft両経路で使う(05 §5はPATCHと
POSTを同一契約と規定)。Pydanticは形式・値域のみを検証し、必須3フィール
ド・時刻(過去/7日)・年齢・ジオコーディングはactive経路のサービス層検証
(design §2.3)。draftは「Pydantic通過=形式検証完了」(確定値3・7)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _IgnoreExtraModel(BaseModel):
    """余分キーを無視する(07 §4の失敗分類に揃える・schema.pyと同設定)。"""

    model_config = ConfigDict(extra="ignore")


class CategoryInput(_IgnoreExtraModel):
    primary: Literal["meal", "drinking", "activity"] | None = None
    secondary: str | None = None


class TimeInput(_IgnoreExtraModel):
    start: datetime | None = None
    end: datetime | None = None
    flexibility_minutes: None = None  # MVPでは常にnull(03 D-19の固定扱い)

    @field_validator("start", "end")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("time must be tz-aware ISO8601")
        return v


class LocationInput(_IgnoreExtraModel):
    name: str | None = Field(default=None, min_length=1)
    radius_m: int | None = Field(default=None, ge=1)


class BudgetInput(_IgnoreExtraModel):
    max: int | None = Field(default=None, ge=0)
    currency: Literal["JPY"] = "JPY"


class ParticipantsInput(_IgnoreExtraModel):
    min: int | None = Field(default=None, ge=1, le=4)
    max: int | None = Field(default=None, ge=1, le=4)


class StructuredIntentInput(_IgnoreExtraModel):
    """保存APIのstructured_intent(全フィールドOptional — design §2.4)。"""

    category: CategoryInput | None = None
    alcohol_involved: bool | None = None
    time: TimeInput | None = None
    location: LocationInput | None = None
    budget: BudgetInput | None = None
    participants: ParticipantsInput | None = None
    visibility: Literal["hidden_until_match", "summary_only"] | None = None
    notification_level: Literal["proposals_only", "nearby_also", "muted"] | None = None
    expires_at: datetime | None = None
    soft_constraints: list[str] | None = None
    negative_constraints: list[str] | None = None  # 受け取るが保存は常に空配列(FR-42)
    ng_unverifiable: list[str] | None = None

    @field_validator("expires_at")
    @classmethod
    def _expires_must_be_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("time must be tz-aware ISO8601")
        return v


class IntentCreateRequest(BaseModel):
    """POST /v1/intents(05 §5)。statusはactive(既定・省略可)またはdraft。"""

    raw_text: str = Field(min_length=1, max_length=300)
    status: Literal["active", "draft"] = "active"
    structured_intent: StructuredIntentInput | None = None


class IntentPatchRequest(BaseModel):
    """PATCH /v1/intents/{id}(design §2.10の契約詳細)。status未指定=現状維持。"""

    raw_text: str = Field(min_length=1, max_length=300)
    status: Literal["active", "draft"] | None = None
    structured_intent: StructuredIntentInput | None = None
