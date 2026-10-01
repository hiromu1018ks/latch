"""notifications APIの入出力スキーマ(M3 ws-3 design §2.5・§3.1)。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class NotificationLatchOut(BaseModel):
    """お知らせitemのlatch要素(LEFT JOIN埋め込み・design §2.5案A)。

    文言はクライアント/テンプレートがこの構造から組み立てる(05 §2・引用#8)。
    hidden_until_match行はheadcount+match_levelのみ・nearby行は最小構成で
    格納済みのため、条件サマリは構造的に出ない(表示規制のサーバ側担保)。
    """

    id: uuid.UUID
    status: str
    response_deadline: datetime
    expires_at: datetime
    completed_at: datetime | None = None
    proposal: dict


class NotificationOut(BaseModel):
    """お知らせ一覧のitem(design §2.5の応答スキーマ)。"""

    id: uuid.UUID
    type: str
    # payload参照。先行書き込みは常に {"latch_id": "<uuid>"} を書くため通常は
    # 値が入る。不正値はnull(防御・Review Focus 5)
    latch_id: uuid.UUID | None = None
    read_at: datetime | None = None
    created_at: datetime
    latch: NotificationLatchOut | None = None


class NotificationListResponse(BaseModel):
    """GET /v1/notifications応答(05 §5ページネーション共通規定)。"""

    items: list[NotificationOut]
    next_cursor: str | None = None
