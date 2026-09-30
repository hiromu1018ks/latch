"""latches APIの入出力スキーマ(M3 ws-1 design §2.8・§3.1)。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ResponseValue = Literal["yes", "no", "defer"]


class ResponseRequest(BaseModel):
    """POST /v1/latches/{id}/response のbody(05 §5)。値域外は422(引用#1)。"""

    response: ResponseValue


class LatchSummaryOut(BaseModel):
    """一覧要素・POST response応答のlatch要素(design §2.8)。

    responses配列は持たない(他者の回答種別を応答へ出さない — 引用#22)。
    my_responseは自分の回答のみ・remaining_responsesは人数のみ。
    from_attributes: serviceが返す行オブジェクト(dataclass/namespace)から
    の直接構築を許可する(unit試験のスタブ注入のため)。
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    response_deadline: datetime
    expires_at: datetime
    created_at: datetime
    completed_at: datetime | None = None
    proposal: dict
    is_group: bool
    my_response: str | None = None
    remaining_responses: int


class LatchEnvelope(BaseModel):
    """POST /v1/latches/{id}/response の200応答(05 §5の {"latch": {...}})。"""

    latch: LatchSummaryOut


class ParticipantOut(BaseModel):
    """成立後の参加者情報(03 §6・引用#21)。profileはbio等をそのまま返す。"""

    user_id: uuid.UUID
    display_name: str
    profile: dict


class LatchDetailOut(LatchSummaryOut):
    """詳細応答のlatch要素。

    participants等はmatched/completedのみ非null(design §2.8)。
    """

    participants: list[ParticipantOut] | None = None
    time_summary: str | None = None
    area_name: str | None = None


class LatchDetailEnvelope(BaseModel):
    latch: LatchDetailOut


class LatchListResponse(BaseModel):
    """GET /v1/latches 応答(05 §5共通規定・cursor改頁)。"""

    items: list[LatchSummaryOut]
    next_cursor: str | None = None
