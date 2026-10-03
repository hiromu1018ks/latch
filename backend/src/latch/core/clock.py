"""C2: すべての時刻参照はこのモジュール経由で行う(12 第2節 C2・04 第5節)。

製品コードが実時間を直接参照することを禁止する規律の中心。
SystemClock以外の実装・テストコードはClockを経由して時刻を得る。
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, timedelta, timezone

from latch.settings import Settings

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
        # Clock契約(now()はtz-aware UTC)を守るため非UTC表現は正規化する。
        self._now = initial.astimezone(UTC)
        self._lock = threading.Lock()

    def set(self, when: datetime) -> None:
        """任意時刻へ移動する(後退も可。「過去不可」境界試験の両方向に使用)。"""
        if when.tzinfo is None:
            raise ValueError("FakeClock.set は tz-aware な時刻を要求する")
        with self._lock:
            self._now = when.astimezone(UTC)

    def advance(self, delta: timedelta) -> None:
        """時刻を delta だけ前進させる(期限・debounce・バッチ周期の再現に使用)。"""
        with self._lock:
            self._now += delta

    def now(self) -> datetime:
        with self._lock:
            return self._now


class ControlledClock(Clock):
    """staging/load用の共有制御Clock(10 §1「時刻操作」・M4 ws-1 design §2.2)。

    Redis 1キーに「基準点(実時刻↔仮想時刻の対応)」を置き、api 2台+worker
    の複数プロセスが同一キー参照で同一の仮想時刻を見る。操作はホスト側CLI
    (python -m latch.core.clock_cli)から行う。
    - キー不在 = オフセット0 = 実時間(「制御していない状態」はSystemClockと等価)
    - Redis到達不能は例外でfail-closed(例外を握って実時間へ落とさない。
      stagingのRedisは常設restart: unless-stopped)
    - rate(速度倍率)は設けない(YAGNI): sweeper/Runnerの待機はasyncio.sleep
      (実時間)のため速くならない。Clock操作で期限を未来へ動かすのが10 §1の
      規定手段
    """

    DEFAULT_KEY = (
        "latch:clock:control"  # ci環境のRedisとは別インスタンスのため名前衝突なし
    )

    def __init__(self, client, *, key: str = DEFAULT_KEY) -> None:
        # clientは同期redisクライアント(get/setを持つオブジェクト。unit試験はfakeredis)
        self._client = client
        self._key = key

    def read_base(self) -> tuple[float, float] | None:
        """基準点(real, virtual)の生値をunix秒floatで返す。キー不在はNone。"""
        raw = self._client.get(self._key)
        if raw is None:
            return None
        data = json.loads(raw)
        return float(data["real"]), float(data["virtual"])

    def set(self, when: datetime) -> None:
        """基準点を「いま実時間→when仮想時間」へ書き換える。

        後退可(FakeClock.setと同一契約)。
        """
        if when.tzinfo is None:
            raise ValueError("ControlledClock.set は tz-aware な時刻を要求する")
        self._write_base(
            datetime.now(UTC).timestamp(), when.astimezone(UTC).timestamp()
        )

    def advance(self, delta: timedelta) -> None:
        """仮想基準点をdeltaだけ前進させる(実アンカーは保持 — 経過分はnow()が加算)。"""
        base = self.read_base()
        if base is None:
            base = self._initialize_base()
        self._write_base(base[0], base[1] + delta.total_seconds())

    def now(self) -> datetime:
        base = self.read_base()
        if base is None:
            return datetime.now(UTC)
        return _compute_now(base[0], base[1], datetime.now(UTC).timestamp())

    def _write_base(self, real: float, virtual: float) -> None:
        self._client.set(self._key, json.dumps({"real": real, "virtual": virtual}))

    def _initialize_base(self) -> tuple[float, float]:
        """制御未開始の状態で基準点を作る(いま実時間=いま仮想時間)。"""
        real_now = datetime.now(UTC).timestamp()
        self._write_base(real_now, real_now)
        return real_now, real_now


def _compute_now(base_real: float, base_virtual: float, real_now: float) -> datetime:
    """基準点+実時間経過から仮想時刻を計算する(純関数 — unit試験は決定的)。"""
    return datetime.fromtimestamp(base_virtual + (real_now - base_real), tz=UTC)


def make_clock(settings: Settings, *, redis_client=None) -> Clock:
    """clock_mode設定でSystemClock/ControlledClockを選ぶ組立ヘルパー(design §2.2)。

    create_app()・worker/__main__.pyの既定構築経路。clock引数を明示する既存DI
    (unit/integration試験)はこの関数を通らないため無傷。redis_clientはunit
    試験がfakeredisを注入するためのDI(既定=redis_urlから構築。redis-pyは
    遅延接続のため構築のみでI/Oは起きない)。
    """
    mode = settings.clock_mode
    if mode == "system":
        return SystemClock()
    if mode == "controlled":
        if redis_client is None:
            import redis as redis_sync  # 遅延import(clock.pyの依存を最小化)

            redis_client = redis_sync.Redis.from_url(
                settings.redis_url, decode_responses=True
            )
        return ControlledClock(redis_client)
    raise ValueError(f"unknown clock_mode: {mode!r} (system/controlled)")
