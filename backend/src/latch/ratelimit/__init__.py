"""レート制限(M1 ws-4)。08 §5.4の上限4種をRedisカウンタとDB計上で強制する。

横断関心事のためintentsドメインの外に置く(design §2.1-A)。StoreにRedis操作を
閉じ込め・limiterが判断する(auth/のSessionStoreと対称な構成)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from latch.ratelimit.errors import (
    RateLimitDependencyError,
    RateLimitedError,
    RateLimitError,
)
from latch.ratelimit.limiter import RateLimiter, RateLimits
from latch.ratelimit.store import RateLimitStore

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from latch.core.clock import Clock
    from latch.settings import Settings


def make_rate_limiter(
    *, clock: Clock, redis_client: aioredis.Redis, settings: Settings
) -> RateLimiter:
    """設定からRateLimiterを構築する(main.py lifespanが呼ぶ)。"""
    return RateLimiter(
        store=RateLimitStore(redis_client),
        clock=clock,
        limits=RateLimits(
            api_per_min=settings.rate_limit_api_per_min,
            create_per_day=settings.rate_limit_create_per_day,
            update_per_hour=settings.rate_limit_update_per_hour,
            active_intents=settings.rate_limit_active_intents,
        ),
    )


__all__ = [
    "RateLimitDependencyError",
    "RateLimitError",
    "RateLimitedError",
    "RateLimiter",
    "RateLimitStore",
    "RateLimits",
    "make_rate_limiter",
]
