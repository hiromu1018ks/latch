"""EventBusポート(アダプタはpubsub_bus。ヘキサゴナルの端口)。

IncomingEventはAPI経由(pending行あり)とテストパブリッシャー直投入(行なし)
の両方を同じ形で扱う(design §2.3の冪等受領)。3点組(event_type,
source_intent_id, version)がidempotency key(06 §9-2)。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class IncomingEvent:
    """受信メッセージ。3点組の抽出不能(欠落・型不一致)は各要素None。"""

    message_id: str
    data: dict  # デコード済み生JSON(JSON不正時は {"_raw": "<文字列>"})
    event_type: str | None
    intent_id: uuid.UUID | None
    version: int | None
    ack: Callable[[], None] = field(repr=False)

    @classmethod
    def from_payload(
        cls, *, message_id: str, payload: bytes, ack: Callable[[], None]
    ) -> IncomingEvent:
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            data = {"_raw": payload.decode(errors="replace")}
        if not isinstance(data, dict):
            data = {"_raw": json.dumps(data)}
        et = data.get("event_type")
        iid = data.get("source_intent_id")
        ver = data.get("version")
        return cls(
            message_id=message_id,
            data=data,
            event_type=et if isinstance(et, str) else None,
            intent_id=_as_uuid(iid),
            version=ver if isinstance(ver, int) and not isinstance(ver, bool) else None,
            ack=ack,
        )

    def triple(self) -> tuple[str, uuid.UUID, int] | None:
        """3点組。一部でも欠ければNone(構造違反経路へ — design §2.4)。"""
        if self.event_type is None or self.intent_id is None or self.version is None:
            return None
        return (self.event_type, self.intent_id, self.version)


def _as_uuid(raw: object) -> uuid.UUID | None:
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


class Subscription(Protocol):
    """subscribeの戻り。stop()でストリーミングpullを終了する(未ackは再配信)。"""

    def stop(self) -> None: ...


class EventBus(Protocol):
    """Match Eventトランスポートのポート(design §3.1)。"""

    async def ensure(self) -> None:
        """topic/subscriptionを存在なければ作成する(冪等)。"""
        ...

    async def publish_match_event(
        self, *, event_type: str, intent_id: uuid.UUID, version: int
    ) -> None:
        """3点組をJSONでpublishする(publish形式は実装の契約)。"""
        ...

    async def subscribe(
        self, on_message: Callable[[IncomingEvent], Awaitable[None]]
    ) -> Subscription:
        """ストリーディングpullを開始する。ackはIncomingEvent.ack経由。"""
        ...

    async def close(self) -> None:
        """クライアントの後始末(プロセス終了時)。"""
        ...
