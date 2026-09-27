"""C2: すべての時刻参照はこのモジュール経由で行う(12 第2節 C2・04 第5節)。

製品コードが実時間を直接参照することを禁止する規律の中心。
SystemClock以外の実装・テストコードはClockを経由して時刻を得る。
"""

from __future__ import annotations

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
