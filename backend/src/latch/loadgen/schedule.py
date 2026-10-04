"""決定的送出時刻列とオープンモデル実行器(M4 ws-6・design §2.1案A)。

予定時刻(開始からの相対秒)の列を決定的に生成する。実行側は予定時刻まで
待って送出し・応答の完了を送出間隔に含めない(k6のconstant-arrival-rateと
同型のオープンモデル — coordinated omission〔応答遅延が投入間隔を遅らせて
測定を甘くする現象〕を原理回避)。時刻参照はClock経由(C2・arch test対象)。
待機はasyncio.sleep(待機であり時刻参照ではない — stub.pyと同一規律)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from latch.core.clock import Clock


@dataclass(frozen=True)
class SendPlan:
    """1送出の予定(開始からの相対秒・種別・順序番号)。"""

    at: float  # 開始からの相対秒(送出予定時刻)
    kind: str  # "parse" | "create" | "draft_activate"
    seq: int  # シナリオ内の通し番号(ペイロード決定生成の入力)


def constant_rate_plan(*, rate_per_sec: float, count: int, kind: str) -> list[SendPlan]:
    """固定到着率の予定列(0, 1/rate, 2/rate, ...秒・決定的)。"""
    interval = 1.0 / rate_per_sec
    return [SendPlan(at=i * interval, kind=kind, seq=i) for i in range(count)]


def burst_plan(*, count: int, duration_s: float, kind: str) -> list[SendPlan]:
    """バーストの予定列(duration_sへ均し配置・決定的)。"""
    interval = duration_s / count
    return [SendPlan(at=i * interval, kind=kind, seq=i) for i in range(count)]


@dataclass
class RunStats:
    """オープンモデル実行の結果(送出数・成否・クライアント側応答時間)。"""

    planned: int
    sent: int = 0
    ok: int = 0
    error: int = 0
    errors: list[str] = field(default_factory=list)
    client_elapsed_ms: list[float] = field(default_factory=list)

    @property
    def client_p95_ms(self) -> float:
        """クライアント側応答時間の参考値(nearest-rank p95)。"""
        if not self.client_elapsed_ms:
            return 0.0
        ordered = sorted(self.client_elapsed_ms)
        return ordered[max(0, round(0.95 * len(ordered)) - 1)]


async def run_open_model(
    plans: list[SendPlan],
    *,
    sender,
    clock: Clock,
) -> RunStats:
    """オープンモデル実行(予定時刻まで待つ・応答は送出間隔に含めない)。

    sender(plan)は応答時間を内部で測るためawaitするが・その完了は次の
    送出予定を遅らせない(create_taskで投げっぱなし・送出ループは予定時刻
    だけを見る)。例外は集計して続行(注入器の失敗が試験を止めない)。
    戻り値の統計は全応答完了後に返す(収束待ち=wait_settledは別・Task 8)。
    """
    stats = RunStats(planned=len(plans))
    start = clock.now()

    async def _one(plan: SendPlan) -> None:
        t0 = clock.now()
        stats.sent += 1
        try:
            await sender(plan)
            stats.ok += 1
            stats.client_elapsed_ms.append((clock.now() - t0).total_seconds() * 1000)
        except Exception as exc:
            stats.error += 1
            stats.errors.append(f"{plan.kind}#{plan.seq}: {type(exc).__name__}")

    tasks: list[asyncio.Task] = []
    try:
        for plan in plans:
            now = clock.now()
            delay = plan.at - (now - start).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(_one(plan)))
        if tasks:
            await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    return stats
