"""JevCostGuard: INCR先行判定・deny理由・80%跨ぎalert・fail-closed(design §4.1)。

上限定数は実際の値(30,000等)のまま扱う。実際に30,000回INCRするのは重いため
Storeをスタブ化して任意のカウンタ値を返させ、境界(24000/24001・30001・480000・
600001・41・121)を検証する(Store自体のINCR正確性はtest_cost_storeが担保)。
"""

import logging
import uuid
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.worker.cost import errors as cost_errors
from latch.worker.cost.guard import (
    DAILY_ALERT,
    DAILY_LIMIT,
    DENY_GLOBAL_DAILY,
    DENY_GLOBAL_MONTHLY,
    DENY_INTENT_DAILY,
    DENY_USER_DAILY,
    INTENT_DAILY_LIMIT,
    MONTHLY_ALERT,
    MONTHLY_LIMIT,
    USER_DAILY_LIMIT,
    JevCostGuard,
    structured_log_alert,
)

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-29 21:00
IID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
UID = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


class StubStore:
    """初期値から+1ずつ返すStore差し替え(design §4.1)。scan_reportは空。"""

    def __init__(self, *, intent=0, user=0, daily=0, monthly=0) -> None:
        self.counts = {
            "intent": intent,
            "user": user,
            "daily": daily,
            "monthly": monthly,
        }

    async def incr_intent(self, intent_id: str, day: str) -> int:
        self.counts["intent"] += 1
        return self.counts["intent"]

    async def incr_user(self, user_id: str, day: str) -> int:
        self.counts["user"] += 1
        return self.counts["user"]

    async def incr_daily(self, day: str) -> int:
        self.counts["daily"] += 1
        return self.counts["daily"]

    async def incr_monthly(self, month: str) -> int:
        self.counts["monthly"] += 1
        return self.counts["monthly"]

    async def scan_report(self, day: str) -> dict:
        return {"intent_top": [], "user_top": [], "exec_breakdown": {}}


def _guard(store, emitter=None) -> JevCostGuard:
    return JevCostGuard(
        store=store,
        clock=FakeClock(NOW),
        alert_emitter=emitter if emitter is not None else (lambda event: None),
    )


def test_limit_constants_pin_docs_values():
    """D-16・06 §5 の確定値ピン(§9固定値)。"""
    assert DAILY_LIMIT == 30_000
    assert DAILY_ALERT == 24_000  # 80%
    assert MONTHLY_LIMIT == 600_000
    assert MONTHLY_ALERT == 480_000  # 80%
    assert INTENT_DAILY_LIMIT == 40
    assert USER_DAILY_LIMIT == 120


async def test_allow_within_all_limits():
    """全上限ちょうど(+1要求で境界値に達するが超えない)は許可。"""
    store = StubStore(intent=39, user=119, daily=23_999, monthly=479_999)
    decision = await _guard(store).request_execution(IID, UID)
    assert decision.allowed is True
    assert decision.deny_reason is None
    # 要求後のカウンタは境界値ちょうど(41/121/24000/480000ではない=超過なし)
    assert store.counts == {
        "intent": 40,
        "user": 120,
        "daily": 24_000,
        "monthly": 480_000,
    }


async def test_deny_reasons_at_each_boundary():
    """deny理由4種の判定(design §4.1の境界)。"""
    cases = [
        (dict(intent=40), DENY_INTENT_DAILY),  # 41回目
        (dict(user=120), DENY_USER_DAILY),  # 121回目
        (dict(daily=30_000), DENY_GLOBAL_DAILY),  # 30001回目
        (dict(monthly=600_000), DENY_GLOBAL_MONTHLY),  # 600001回目
    ]
    for initial, expected in cases:
        store = StubStore(**initial)
        decision = await _guard(store).request_execution(IID, UID)
        assert decision.allowed is False, initial
        assert decision.deny_reason == expected, initial


async def test_deny_priority_follows_design_enumeration():
    """複数上限同時超過は design §2.4-2 の列挙順(intent→user→daily→monthly)。"""
    store = StubStore(intent=40, user=120, daily=30_000, monthly=600_000)
    decision = await _guard(store).request_execution(IID, UID)
    assert decision.deny_reason == DENY_INTENT_DAILY


async def test_denied_requests_still_consume_counter():
    """INCR先行: deny後の再要求でもカウンタは増える(design §2.4-2・引用#8)。"""
    store = StubStore()
    guard = _guard(store)
    for _ in range(41):
        await guard.request_execution(IID, UID)
    assert store.counts["intent"] == 41  # 41回目でdeny済み
    await guard.request_execution(IID, UID)
    assert store.counts["intent"] == 42  # deny後も消費


async def test_daily_80pct_alert_fires_once_at_threshold():
    """戻り値==24000のとき1回だけ(23999・24001では発報しない — design §2.4-4)。"""
    events: list[dict] = []
    store = StubStore(daily=23_998)  # →23999: 発報しない
    await _guard(store, emitter=events.append).request_execution(IID, UID)
    assert events == []
    store2 = StubStore(daily=23_999)  # →24000: 発報
    await _guard(store2, emitter=events.append).request_execution(IID, UID)
    assert len(events) == 1
    assert events[0]["kind"] == "daily_80pct"
    assert events[0]["day"] == "20260929"
    assert events[0]["count"] == DAILY_ALERT
    assert events[0]["report"] == {
        "intent_top": [],
        "user_top": [],
        "exec_breakdown": {},
    }
    store3 = StubStore(daily=24_000)  # →24001: 発報しない
    await _guard(store3, emitter=events.append).request_execution(IID, UID)
    assert len(events) == 1


async def test_monthly_80pct_and_limit_alerts_with_resume_at():
    """月次480000跨ぎalert・600001到達で復帰予定(暦月初JST 0時)明示ログ1回。"""
    events: list[dict] = []
    store = StubStore(monthly=479_999)  # →480000: 80%発報
    decision = await _guard(store, emitter=events.append).request_execution(IID, UID)
    assert decision.allowed is True  # 480000 ≤ 上限
    assert [e["kind"] for e in events] == ["monthly_80pct"]

    events2: list[dict] = []
    store2 = StubStore(monthly=599_999)  # →600000: 上限内・発報なし
    decision2 = await _guard(store2, emitter=events2.append).request_execution(IID, UID)
    assert decision2.allowed is True
    assert events2 == []

    events3: list[dict] = []
    store3 = StubStore(monthly=600_000)  # →600001: deny+到達ログ
    decision3 = await _guard(store3, emitter=events3.append).request_execution(IID, UID)
    assert decision3.allowed is False
    assert decision3.deny_reason == DENY_GLOBAL_MONTHLY
    assert [e["kind"] for e in events3] == ["monthly_limit"]
    assert events3[0]["resume_at"] == "2026-10-01T00:00:00+09:00"  # 暦月初JST 0時


async def test_redis_exception_wrapped_fail_closed():
    """Redis例外は専用例外へ(fail-closed — design §2.4-7)。"""

    class BrokenStore(StubStore):
        async def incr_intent(self, intent_id: str, day: str) -> int:
            raise ConnectionError("redis down")

    with pytest.raises(cost_errors.JevCostDependencyError):
        await _guard(BrokenStore()).request_execution(IID, UID)


def test_structured_log_alert_emits_to_latch_cost_alert(caplog):
    """alert媒体=構造化ログ(supervisor承認・design §2.4-5)。"""
    with caplog.at_level(logging.WARNING, logger="latch.cost.alert"):
        structured_log_alert({"kind": "daily_80pct", "day": "20260929"})
    assert caplog.records[0].name == "latch.cost.alert"
    assert "daily_80pct" in caplog.records[0].getMessage()
