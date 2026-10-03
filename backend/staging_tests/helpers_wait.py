"""条件ポーリング(M4 ws-3・設計§2.4)。

待ち上限はtick(60秒)の2〜3倍=120〜180秒で呼び出す。タイムアウトは
what(何を待っていたか)入りで失敗する。pytestタイムアウトプラグインは
使わない(依存追加禁止・全待機をこの関数で上限化)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic  # 待ち上限計測のみ・arch testはsrc/latchが対象(設計§4-6)


async def wait_until(
    cond: Callable[[], Awaitable[bool]],
    *,
    timeout_s: float,
    interval_s: float = 5.0,
    what: str = "condition",
) -> None:
    """async条件が成立するまでinterval_s間隔でポーリングする。上限で失敗。"""
    deadline = monotonic() + timeout_s
    while True:
        if await cond():
            return
        if monotonic() >= deadline:
            raise AssertionError(f"タイムアウト({timeout_s}s): {what}")
        await asyncio.sleep(interval_s)
