"""LLM送信記録(08 第3節: 送信先・データ種別・時刻。内容を含まない)。

SendRecordが構造化ログの許可リストの実体(08 第2.4節)。出力できるフィールドを
この型で固定する — この型に存在しないフィールドは出力され得ない。
時刻occurred_atはClock.now()(tz-aware UTC)由来のみ(design確定値16)。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LOGGER_NAME = "latch.llm.send"

SystemName = Literal["intent_parser", "embedding", "jev"]
SendStatus = Literal["ok", "timeout", "error"]


class SendRecord(BaseModel):
    """送信記録1件(08 第3節)。機微テキストを運ぶフィールドは持たない。"""

    occurred_at: datetime  # Clock.now()(tz-aware UTC)
    system: SystemName  # データ種別(どの系統の送信か)
    destination: str  # 送信先(Provider.name)
    status: SendStatus
    error_code: str | None = None  # 例外IDのみ(08 第2.4節「例外・エラーはIDのみ」)
    intent_ids: list[str] | None = None  # Embedding=1件・Jev=2件・Parser=None
    user_id: str | None = None  # Parser(M1のparse API実装で値が入る。design §6-2)


def send_log(record: SendRecord) -> None:
    """ロガー 'latch.llm.send' へJSON 1行で出力(04 第3節ログ集計への接続点)。"""
    logging.getLogger(LOGGER_NAME).info(record.model_dump_json())
