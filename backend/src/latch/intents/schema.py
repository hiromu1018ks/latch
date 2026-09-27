"""07 §2出力JSONスキーマの検証モデル(design §3.1・§2.4)。

出力の検証は ParserOutput.model_validate に一元化する — 検証失敗はすべて
ValidationError となり、サービス層で422 VALIDATION_ERROR(構造化不能)へ
替わる(design §2.4)。失敗とするのは欠損・値域外・パース不能のみで、
余分なキーは無視する(07 §4の失敗分類に揃える)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _IgnoreExtraModel(BaseModel):
    """余分キーを無視する共通設定(07 §4の失敗分類に揃える)。"""

    model_config = ConfigDict(extra="ignore")


class ParserCategory(_IgnoreExtraModel):
    primary: Literal["meal", "drinking", "activity"]  # 必須・既定なし
    secondary: str | None = None


class ParserTime(_IgnoreExtraModel):
    start: datetime  # tz-aware必須(オフセットはJSTに限定しない)
    end: datetime | None = None
    flexibility_minutes: None = None  # MVPでは常にnull(03 D-19の固定扱い)

    @field_validator("start", "end")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("time must be tz-aware ISO8601")
        return v


class ParserLocation(_IgnoreExtraModel):
    name: str = Field(min_length=1)  # 必須3フィールドの1つ
    radius_m: int | None = None
    flexibility: None = None  # MVPでは常にnull(03 D-19の固定扱い)


class ParserBudget(_IgnoreExtraModel):
    max: int | None = None
    currency: Literal["JPY"] = "JPY"


class ParserParticipants(_IgnoreExtraModel):
    min: int | None = None
    max: int | None = None


class ParserOutput(_IgnoreExtraModel):
    """07 §2出力JSONスキーマ(この形式のみ認める)。"""

    category: ParserCategory
    alcohol_involved: bool  # 常にtrue/false(規則7)・アプリ層補完なし
    time: ParserTime
    location: ParserLocation
    budget: ParserBudget = ParserBudget()
    participants: ParserParticipants = ParserParticipants()
    soft_constraints: list[str] = []
    negative_constraints: list[str] = []  # 常に空が正常系(FR-42・規則5)
    ng_unverifiable: list[str] = []


# 03 §3・05 §5応答例の文言(D-04注意表示)。クライアントはこの文言を表示する
WARNING_MESSAGE_NG_DOWNGRADED = (
    "この条件は確実には除外できません。参考条件として扱います"
)
