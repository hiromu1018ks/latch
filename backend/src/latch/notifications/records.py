"""プッシュ送信記録(M3 ws-3 design §2.3)。

LLMのSendRecord(08 §3「内容を含まない」)と異なり、本文を含む — 10 §1が
「プッシュ本文が汎用文であること」を記録で検証することを要求するため。
本文はtemplates.pyの固定定数のみを通り得るため機微は入り得ない
(許可リスト方式・08 §2.4の型固定は踏襲)。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LOGGER_NAME = "latch.push.send"

SendStatus = Literal["ok", "timeout", "error"]


class PushSendRecord(BaseModel):
    """ドライラン送信記録1件(design §2.3)。出力フィールドをこの型で固定。"""

    occurred_at: datetime  # Clock.now()(tz-aware UTC)
    user_id: str  # 宛先ユーザー
    notification_type: str  # proposal / nearby_candidate / attendance_request
    latch_id: str  # 参照先
    title: str  # テンプレート定数
    body: str  # テンプレート定数(汎用文)
    status: SendStatus
    error_code: str | None = None  # 例外IDのみ(08 §2.4)


def push_log(record: PushSendRecord) -> None:
    """ロガー 'latch.push.send' へJSON 1行で出力(latch.llm.sendと同型)。"""
    logging.getLogger(LOGGER_NAME).info(record.model_dump_json())
