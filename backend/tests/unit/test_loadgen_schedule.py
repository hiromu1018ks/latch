"""決定的送出時刻列とオープンモデル実行器(M4 ws-6・design §2.1案A)。

実時間の測定(perf_counter)はテストコードのみ(§0規律・既存test_llm_factory
と同一作法)。オープンモデル性(応答待ちが送出間隔を遅らせない)は・遅い
応答を模擬したモック送出下で・クローズドモデルの所要を大きく下回ることで
検証する。
"""

import asyncio
import time

from latch.core.clock import SystemClock
from latch.loadgen.schedule import burst_plan, constant_rate_plan, run_open_model


def test_constant_rate_plan_is_deterministic():
    plans = constant_rate_plan(rate_per_sec=50.0, count=500, kind="create")
    assert [p.at for p in plans[:5]] == [0.0, 0.02, 0.04, 0.06, 0.08]
    assert len(plans) == 500
    assert plans[499].at == 499 * 0.02
    assert all(p.kind == "create" for p in plans)
    assert [p.seq for p in plans] == list(range(500))


def test_burst_plan_spans_one_second():
    plans = burst_plan(count=100, duration_s=1.0, kind="create")
    assert len(plans) == 100
    assert plans[0].at == 0.0
    assert abs(plans[-1].at - 0.99) < 1e-9
    assert all(plans[i].at < plans[i + 1].at for i in range(99))


async def test_open_model_sends_all_plans_in_order():
    """予定どおりの件数を送出する(senderは送出順に呼ばれる)。"""
    sent: list[tuple[str, int]] = []

    async def sender(plan):
        sent.append((plan.kind, plan.seq))

    plans = constant_rate_plan(rate_per_sec=1000.0, count=20, kind="parse")
    stats = await run_open_model(plans, sender=sender, clock=SystemClock())
    assert stats.planned == 20
    assert stats.sent == 20
    assert stats.ok == 20
    assert stats.error == 0
    assert sent == [("parse", i) for i in range(20)]


async def test_open_model_does_not_slow_down_with_slow_responses():
    """応答待ちが送出間隔を遅らせない(coordinated omissionの原理回避)。"""
    import sys

    if sys.platform == "win32":
        return  # Windowsのtimer精度では検証にならない(実環境はLinux)

    async def sender(plan):
        await asyncio.sleep(0.3)  # 遅い応答を模擬

    plans = constant_rate_plan(rate_per_sec=200.0, count=20, kind="create")
    start = time.perf_counter()
    stats = await run_open_model(plans, sender=sender, clock=SystemClock())
    elapsed = time.perf_counter() - start
    assert stats.ok == 20
    # クローズドモデルなら 20 × (0.005 + 0.3) ≈ 6.1秒。オープンモデルでは
    # 送出が0.1秒で終わり・全体は最後の応答(約0.3秒)待ちのみ
    assert elapsed < 2.0


async def test_open_model_records_client_elapsed_ms():
    async def sender(plan):
        await asyncio.sleep(0.01)

    plans = burst_plan(count=5, duration_s=0.05, kind="create")
    stats = await run_open_model(plans, sender=sender, clock=SystemClock())
    assert len(stats.client_elapsed_ms) == 5
    assert stats.client_p95_ms >= 10.0  # nearest-rank p95は応答時間を下回らない


async def test_open_model_counts_errors_and_continues():
    async def sender(plan):
        if plan.seq % 3 == 0:
            raise RuntimeError("injected")

    plans = constant_rate_plan(rate_per_sec=1000.0, count=9, kind="parse")
    stats = await run_open_model(plans, sender=sender, clock=SystemClock())
    assert stats.ok == 6
    assert stats.error == 3
    assert len(stats.errors) == 3
