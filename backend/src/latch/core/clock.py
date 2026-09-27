"""C2: すべての時刻参照はこのモジュール経由で行う(12 第2節 C2・04 第5節)。

製品コードが実時間を直接参照することを禁止する規律の中心。
SystemClock以外の実装・テストコードはClockを経由して時刻を得る。
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, timedelta, timezone

# 日本に夏時間はないため、固定オフセット+9時間で確定させる。
JST = timezone(timedelta(hours=+9))


class Clock(ABC):
    """時刻参照の単一経路(C2)。now()はtz-aware UTC、JST暦日付はjst_date()。"""

    @abstractmethod
    def now(self) -> datetime:
        """tz-aware UTC の現在時刻を返す。naive datetime は返さない。"""

    def jst_date(self) -> date:
        """now() を JST 暦日付へ変換する(JST日付キー・リセットジョブ判定用)。"""
        return self.now().astimezone(JST).date()


class SystemClock(Clock):
    """本番用。実時間の現在時刻を返す(実時間参照が許される唯一の実装)。"""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock(Clock):
    """テスト用。set()/advance() で決定的な時刻を再現する(10 第1節「時刻操作」)。"""

    def __init__(self, initial: datetime) -> None:
        if initial.tzinfo is None:
            raise ValueError("FakeClock は tz-aware な初期時刻を要求する")
        self._now = initial
        self._lock = threading.Lock()

    def set(self, when: datetime) -> None:
        """任意時刻へ移動する(後退も可。「過去不可」境界試験の両方向に使用)。"""
        if when.tzinfo is None:
            raise ValueError("FakeClock.set は tz-aware な時刻を要求する")
        with self._lock:
            self._now = when

    def advance(self, delta: timedelta) -> None:
        """時刻を delta だけ前進させる(期限・debounce・バッチ周期の再現に使用)。"""
        with self._lock:
            self._now += delta

    def now(self) -> datetime:
        with self._lock:
            return self._now
