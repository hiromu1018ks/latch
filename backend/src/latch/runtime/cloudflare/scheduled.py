"""Workers scheduled(Cron)駆動の評価scheduler(T8・§5.4)。

1分周期のCronでdue仕事を上限付きbatch・cursorで公平に配送し、評価を
adapters経由で実行、完了は条件付き更新で確定する。業務計算そのものは
既存(runner/latch_engine等)をT9以降で接続する。ここではscheduler骨格
(回収・公平性・世代保全)の実証を担う。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from latch.adapters.d1.schedule import (
    D1ScheduleRepository,
    ScheduleClaim,
    compute_next_due,
)

Evaluator = Callable[[ScheduleClaim, datetime], Awaitable[dict]]


@dataclass(frozen=True)
class SchedulerRunResult:
    claimed: int
    completed: int
    superseded: int  # 処理中に新しい世代が来て破棄された数


class ScheduledEvaluator:
    """due回収→評価→完了確定の1周期。"""

    def __init__(
        self,
        repo: D1ScheduleRepository,
        evaluator: Evaluator,
        *,
        batch_limit: int = 50,
    ) -> None:
        self.repo = repo
        self.evaluator = evaluator
        self.batch_limit = batch_limit

    async def run_once(
        self, now: datetime, *, intent_states: dict | None = None
    ) -> SchedulerRunResult:
        """1周期。intent_states: intent_id→{time_start, expires_at, ...}."""
        await self.repo.recover_expired_leases(now)
        claimed_total = 0
        completed = 0
        superseded = 0
        cursor: tuple[int, str] | None = None
        while True:
            claims = await self.repo.claim_due(now, self.batch_limit, cursor)
            if not claims:
                break
            claimed_total += len(claims)
            cursor = self.repo._cursor_of(claims[-1])
            for claim in claims:
                state = (intent_states or {}).get(claim.intent_id, {})
                result = await self.evaluator(claim, now)
                next_due = compute_next_due(
                    now,
                    time_start=state.get("time_start"),
                    expires_at=state.get("expires_at"),
                )
                ok = await self.repo.complete_generation(claim, result, next_due, now)
                if ok:
                    completed += 1
                else:
                    superseded += 1
            if len(claims) < self.batch_limit:
                break
        return SchedulerRunResult(
            claimed=claimed_total, completed=completed, superseded=superseded
        )
