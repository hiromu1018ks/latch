"""レート制限の判断(design §2.2・§2.4・§2.7)。

INCR先行: storeでカウントを進めてから上限と比較する(判定とカウントの間の
並行すり抜けをRedis上で潰す — 429を返したリクエスト・422で失敗した作成も
カウント済み)。バケット文字列はClockから導出する(実時間参照禁止 — C2)。
Redis例外はこの層で503へ包む(fail-closed — design §2.6)。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from latch.core.clock import JST, Clock
from latch.ratelimit.errors import RateLimitDependencyError, RateLimitedError
from latch.ratelimit.store import RateLimitStore


@dataclass(frozen=True)
class RateLimits:
    """上限4種(08 §5.4)。Settings(LATCH_環境変数)から構築される。"""

    api_per_min: int = 60
    create_per_day: int = 20
    update_per_hour: int = 6
    active_intents: int = 5


class RateLimiter:
    """カウンタ種別ごとのINCR+上限判定。上限値はRateLimitsで注入。"""

    def __init__(
        self, *, store: RateLimitStore, clock: Clock, limits: RateLimits
    ) -> None:
        self._store = store
        self._clock = clock
        self._limits = limits

    @property
    def active_limit(self) -> int:
        """Active Intent数上限(serviceが422判定に使う — design §2.3)。"""
        return self._limits.active_intents

    # -- バケット導出(JST暦。日付はjst_date()・時分はnow()のJST変換 --

    def _minute_bucket(self) -> str:
        return self._clock.now().astimezone(JST).strftime("%Y%m%d%H%M")

    def _hour_bucket(self) -> str:
        return self._clock.now().astimezone(JST).strftime("%Y%m%d%H")

    def _day_bucket(self) -> str:
        return self._clock.jst_date().strftime("%Y%m%d")

    async def _check(self, count: int, limit: int) -> None:
        if count > limit:
            raise RateLimitedError("rate limit exceeded")

    async def _guard(self, incr: Callable[[], Awaitable[int]]) -> int:
        try:
            return await incr()
        except RateLimitDependencyError:
            raise
        except Exception as exc:
            raise RateLimitDependencyError("rate limit dependency unavailable") from exc

    async def check_api(self, *, user_key: str) -> None:
        """API全体60req/分(user_key=user_id or anon-… — design §2.4)。"""
        count = await self._guard(
            lambda: self._store.incr_api(user_key, self._minute_bucket())
        )
        await self._check(count, self._limits.api_per_min)

    async def check_create(self, *, user_id: uuid.UUID) -> None:
        """Intent作成20件/日(draft・active両方。POST /v1/intents全体)。"""
        count = await self._guard(
            lambda: self._store.incr_create(str(user_id), self._day_bucket())
        )
        await self._check(count, self._limits.create_per_day)

    async def check_update(self, *, intent_id: uuid.UUID) -> None:
        """Intent更新6回/時(PATCH全分岐+resume — design §2.4告白1)。"""
        count = await self._guard(
            lambda: self._store.incr_update(str(intent_id), self._hour_bucket())
        )
        await self._check(count, self._limits.update_per_hour)

    async def check_auth(self, *, provider: str, subject: str) -> None:
        """auth/token・refresh 60req/分(provider+subject単位 — design §2.5)。

        subject(メールアドレス等のPIIになり得る)はsha256でハッシュ化して
        キーへ埋める(SessionStoreがrefresh tokenをSHA-256保持するのと同規律)。
        """
        subject_sha = hashlib.sha256(subject.encode()).hexdigest()
        count = await self._guard(
            lambda: self._store.incr_auth(provider, subject_sha, self._minute_bucket())
        )
        await self._check(count, self._limits.api_per_min)
