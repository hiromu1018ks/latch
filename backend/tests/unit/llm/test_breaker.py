"""CircuitBreaker状態機のunit試験(ws-8 design §2.2・§4.1)。

純部品(FakeClock注入)でdocs確定値の機械検査を行う。実DB・実Redis不要。
"""

from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.llm.breaker import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    BreakerParams,
    CircuitBreaker,
)

NOW = datetime(2026, 10, 1, 2, 30, 0, tzinfo=UTC)  # 11:30 JST(goldset基準)


def _clock() -> FakeClock:
    return FakeClock(NOW)


def test_1_params_defaults_pin():
    """BreakerParams既定値=docs確定値(窓60秒・50%超・半開60秒・最小2)。"""
    p = BreakerParams()
    assert p.window_s == 60.0
    assert p.error_rate_threshold == 0.5
    assert p.half_open_after_s == 60.0
    assert p.min_samples == 2


def test_2_single_error_does_not_open():
    """窓内1失敗だけでは開放しない(引用#1「単発では発動せず」・min_samples=2)。"""
    b = CircuitBreaker(clock=_clock())
    b.record(error=True, latency_s=0.1)
    assert b.state == CLOSED


def test_3_error_rate_opens_at_two_failures():
    """2失敗/2呼(100%>50%)で開放(design §2.4試験3の性質)。"""
    b = CircuitBreaker(clock=_clock())
    b.record(error=True, latency_s=0.1)
    b.record(error=True, latency_s=0.1)
    assert b.state == OPEN


def test_4_error_rate_3_of_5_opens():
    b = CircuitBreaker(clock=_clock())
    for _ in range(3):
        b.record(error=True, latency_s=0.1)
    for _ in range(2):
        b.record(error=False, latency_s=0.1)
    assert b.state == OPEN  # 3/5=60%>50%


def test_5_error_rate_2_of_5_stays_closed():
    b = CircuitBreaker(clock=_clock())
    # 成功を先に記録し全時点でエラー率≤50%を保つ(2失敗が先だと2/2=100%で
    # test_3どおり正しく開放するため、40%判定を分離して検証する)
    for _ in range(3):
        b.record(error=False, latency_s=0.1)
    for _ in range(2):
        b.record(error=True, latency_s=0.1)
    assert b.state == CLOSED  # 2/5=40%≤50%


def test_6_window_eviction_excludes_old_failures():
    """窓追い出し: 60秒前の失敗は判定から除外される。"""
    clock = _clock()
    b = CircuitBreaker(clock=clock)
    b.record(error=True, latency_s=0.1)  # t0の失敗
    clock.advance(timedelta(seconds=61))  # 窓外へ
    b.record(error=True, latency_s=0.1)  # t61の失敗
    # 窓内は新1件のみ(N=1<min_samples)→開放しない
    assert b.state == CLOSED
    assert b.samples() == ((True, 0.1),)  # 旧サンプルは除去済み


def test_7_p95_boundary_n20():
    """p95位置境界: N=20でtimeout(=threshold値)1件=5%は不開放・2件=10%は開放。

    timeout呼び出しのレイテンシは打ち切り時点のtimeout_sとして記録される
    (design §2.2・承認事項1)。p95位置=ceil(0.95×N)−1(0-indexed)。
    """
    b = CircuitBreaker(clock=_clock(), latency_threshold_s=6.0)
    for _ in range(19):
        b.record(error=False, latency_s=0.1)
    b.record(error=True, latency_s=6.0)  # timeout打ち切り(1件=5%)
    assert b.state == CLOSED  # p95位置=18番目は成功(0.1)

    b2 = CircuitBreaker(clock=_clock(), latency_threshold_s=6.0)
    for _ in range(18):
        b2.record(error=False, latency_s=0.1)
    b2.record(error=True, latency_s=6.0)
    b2.record(error=True, latency_s=6.0)  # 2件=10%・エラー率も10%≤50%
    assert b2.state == OPEN  # p95位置=18番目が6.0≥6.0


def test_8_half_open_transition_after_60s():
    b = CircuitBreaker(clock=_clock())
    b.record(error=True, latency_s=0.1)
    b.record(error=True, latency_s=0.1)
    assert b.state == OPEN
    assert b.allow(b._clock.now()) is False  # 60秒未満は拒否
    b._clock.advance(timedelta(seconds=60))
    assert b.allow(b._clock.now()) is True  # 半開の試験リクエスト
    assert b.state == HALF_OPEN


def test_9_half_open_success_closes_and_resets_window():
    clock = _clock()
    b = CircuitBreaker(clock=clock)
    b.record(error=True, latency_s=0.1)
    b.record(error=True, latency_s=0.1)
    clock.advance(timedelta(seconds=60))
    assert b.allow(clock.now()) is True
    b.record(error=False, latency_s=0.1)  # 試験成功→closed+窓リセット
    assert b.state == CLOSED
    assert b.samples() == ()  # 窓リセット
    b.record(error=True, latency_s=0.1)  # リセット後は1失敗で開放しない
    assert b.state == CLOSED


def test_10_half_open_failure_reopens_and_extends():
    clock = _clock()
    b = CircuitBreaker(clock=clock)
    b.record(error=True, latency_s=0.1)
    b.record(error=True, latency_s=0.1)
    clock.advance(timedelta(seconds=60))
    assert b.allow(clock.now()) is True
    b.record(error=True, latency_s=0.1)  # 試験失敗→open戻し(opened_at更新)
    assert b.state == OPEN
    assert b.allow(clock.now()) is False
    clock.advance(timedelta(seconds=60))  # 再び60秒→再度半開(往復)
    assert b.allow(clock.now()) is True
    assert b.state == HALF_OPEN


def test_11_half_open_probe_pending_defense():
    """半開の試験リクエストが未recordのうちの再allowはFalse(防御)。"""
    clock = _clock()
    b = CircuitBreaker(clock=clock)
    b.record(error=True, latency_s=0.1)
    b.record(error=True, latency_s=0.1)
    clock.advance(timedelta(seconds=60))
    assert b.allow(clock.now()) is True
    assert b.allow(clock.now()) is False  # 未recordの2回目は拒否


def test_12_state_transitions_logged(caplog):
    """状態遷移は構造化ログ(latch.breaker)へ(design §2.11-3)。"""
    import logging

    clock = _clock()
    b = CircuitBreaker(clock=clock)
    with caplog.at_level(logging.INFO, logger="latch.breaker"):
        b.record(error=True, latency_s=0.1)
        b.record(error=True, latency_s=0.1)  # closed -> open
        clock.advance(timedelta(seconds=60))
        b.allow(clock.now())  # open -> half_open
        b.record(error=False, latency_s=0.1)  # half_open -> closed
    messages = [r.message for r in caplog.records]
    assert any("closed -> open" in m for m in messages)
    assert any("open -> half_open" in m for m in messages)
    assert any("half_open -> closed" in m for m in messages)
