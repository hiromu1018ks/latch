"""D-16カウンタ操作(M4 ws-7・design §2.3案A)。

対象キーはworker/cost/store.pyと同一形式(jev:daily:{yyyymmdd} TTL 48時間・
jev:monthly:{yyyymm} TTL 45日)。バケット文字列はClock(JST)から導出する
(guard._day_bucketと同一・C2)。SETは値の上書き+TTL再設定・GETは不在None・
DELETEはキー削除。境界試験(alert 24,000・上限30,000・月次480,000)の母体。
実Event投入(publish updated)と組み合わせてalert発報と上限到達を通す。
"""

from __future__ import annotations

from latch.core.clock import Clock

DAY_TTL_S = 48 * 3600
MONTH_TTL_S = 45 * 24 * 3600


def daily_key(clock: Clock) -> str:
    return f"jev:daily:{clock.jst_date().strftime('%Y%m%d')}"


def monthly_key(clock: Clock) -> str:
    return f"jev:monthly:{clock.jst_date().strftime('%Y%m')}"


def _key_for(clock: Clock, scope: str) -> str:
    if scope == "daily":
        return daily_key(clock)
    if scope == "monthly":
        return monthly_key(clock)
    raise ValueError(f"unknown scope: {scope!r} (daily/monthly)")


async def set_counter(redis, clock: Clock, scope: str, value: int) -> None:
    """カウンタ上書き+TTL再設定(workerの値と同一TTL・掃除用)。"""
    key = _key_for(clock, scope)
    ttl = DAY_TTL_S if scope == "daily" else MONTH_TTL_S
    await redis.set(key, str(value), ex=ttl)


async def get_counter(redis, clock: Clock, scope: str) -> int | None:
    raw = await redis.get(_key_for(clock, scope))
    return int(raw) if raw is not None else None


async def delete_counter(redis, clock: Clock, scope: str) -> int:
    return int(await redis.delete(_key_for(clock, scope)))
