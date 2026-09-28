"""更新Eventのみのトレーリングdebounce(06 §9-1・design §2.3)。

窓は最適化であり正しさはversion検査+UNIQUE制約がDBで担保する(設計判断)。
解放時刻=未知version到着時刻(Clock.now())+window_sec。同一versionの
再提出は窓を延長しない(at-least-once再受信・フォールバックリレーの
再publishが窓を永久に延長する競合の構造的排除 — design §2.3)。
tickはasyncioの短周期ループでClock.now()と比較する(実時間を参照しない)。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from latch.core.clock import Clock
from latch.events import IncomingEvent

logger = logging.getLogger(__name__)


@dataclass
class DebounceEntry:
    """窓へ投入される1メッセージ(行確保済み — design §2.3の冪等受領)。"""

    event: IncomingEvent
    triple: tuple[str, uuid.UUID, int]
    row_id: uuid.UUID


@dataclass
class DebounceGroup:
    """窓解放時にon_releaseへ渡るグループ(最新version+吸収行)。"""

    intent_id: uuid.UUID
    latest: DebounceEntry
    absorbed: list[DebounceEntry] = field(default_factory=list)


@dataclass
class _Group:
    release_at: datetime
    entries: list[DebounceEntry] = field(default_factory=list)
    known_versions: set[int] = field(default_factory=set)


class TrailingDebouncer:
    def __init__(
        self,
        *,
        clock: Clock,
        window_sec: float,
        tick_sec: float,
        on_release: Callable[[DebounceGroup], Awaitable[None]],
    ) -> None:
        self._clock = clock
        self._window = timedelta(seconds=window_sec)
        self._tick_sec = tick_sec
        self._on_release = on_release
        self._groups: dict[uuid.UUID, _Group] = {}

    def submit(self, entry: DebounceEntry) -> bool:
        """True=新規作成/未知versionによる延長。False=既知version(何もしない)。"""
        _, intent_id, version = entry.triple
        group = self._groups.get(intent_id)
        if group is None:
            self._groups[intent_id] = _Group(
                release_at=self._clock.now() + self._window,
                entries=[entry],
                known_versions={version},
            )
            return True
        if version in group.known_versions:
            return False
        group.known_versions.add(version)
        group.entries.append(entry)
        group.release_at = self._clock.now() + self._window  # 未知version→延長
        return True

    def poll(self) -> list[DebounceGroup]:
        """解放時刻に達したグループを取り出す(削除して返す)。"""
        now = self._clock.now()
        released: list[DebounceGroup] = []
        due = [iid for iid, g in self._groups.items() if now >= g.release_at]
        for intent_id in due:
            group = self._groups.pop(intent_id)
            entries = sorted(group.entries, key=lambda e: e.triple[2])
            released.append(
                DebounceGroup(
                    intent_id=intent_id, latest=entries[-1], absorbed=entries[:-1]
                )
            )
        return released

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """tick周期でpollし、on_releaseを待つ(shutdownで窓内は破棄=未ack再配信)。"""
        while stop is None or not stop.is_set():
            await asyncio.sleep(self._tick_sec)
            for group in self.poll():
                try:
                    await self._on_release(group)
                except Exception:
                    logger.exception(
                        "debounce release failed intent_id=%s version=%s",
                        group.intent_id,
                        group.latest.triple[2],
                    )
