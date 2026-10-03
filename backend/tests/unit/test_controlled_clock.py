"""ControlledClockとmake_clockのunit試験(M4 ws-1 design §4.1)。

時刻計算は純関数_compute_nowで決定的に、Redisはfakeredis(同期・
decode_responses=True)で決定的に検証する。実時間待ちは置かない。
"""

from datetime import UTC, datetime, timedelta, timezone

import fakeredis
import pytest

from latch.core.clock import (
    ControlledClock,
    SystemClock,
    _compute_now,
    make_clock,
)
from latch.settings import Settings

JST = timezone(timedelta(hours=9))


def _clock() -> ControlledClock:
    return ControlledClock(fakeredis.FakeRedis(decode_responses=True))


def test_compute_now_offsets_by_elapsed_real_time():
    # 基準(real=1000.0, virtual=2000.0)から実時間が5秒経過 → 仮想2005.0
    assert _compute_now(1000.0, 2000.0, 1005.0) == datetime.fromtimestamp(
        2005.0, tz=UTC
    )


def test_compute_now_returns_tz_aware_utc():
    got = _compute_now(0.0, 0.0, 10.0)
    assert got.tzinfo is not None
    assert got.utcoffset() == timedelta(0)


def test_key_absent_now_is_real_time():
    # キー不在=オフセット0=SystemClockと等価(design §2.2)
    clock = _clock()
    before = SystemClock().now()
    got = clock.now()
    after = SystemClock().now()
    assert before <= got <= after
    assert clock.read_base() is None


def test_set_writes_anchor_and_now_follows():
    clock = _clock()
    when = datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC)
    clock.set(when)
    base = clock.read_base()
    assert base[1] == when.timestamp()  # virtual基準点=指定時刻
    assert abs(base[0] - SystemClock().now().timestamp()) < 60  # real=発行時点
    first = clock.now()
    assert abs((first - when).total_seconds()) < 5  # 実時間経過分だけ進む


def test_set_accepts_jst_and_normalizes_to_utc():
    clock = _clock()
    clock.set(datetime(2026, 10, 4, 9, 0, 0, tzinfo=JST))
    assert (
        clock.read_base()[1] == datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC).timestamp()
    )


def test_set_rejects_naive():
    with pytest.raises(ValueError):
        _clock().set(datetime(2026, 10, 4, 0, 0, 0))


def test_set_backwards_moves_virtual_back():
    # 後退可(FakeClock.setと同一契約。「過去不可」境界試験の両方向に対応)
    clock = _clock()
    clock.set(datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC))
    clock.set(datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC))
    assert clock.now() < datetime(2026, 9, 1, 0, 1, 0, tzinfo=UTC)


def test_advance_adds_to_virtual_keeping_real_anchor():
    clock = _clock()
    clock.set(datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC))
    base_before = clock.read_base()
    clock.advance(timedelta(days=1))
    base_after = clock.read_base()
    assert base_after[0] == base_before[0]  # 実アンカー保持(経過分はnow()が加算)
    assert base_after[1] == base_before[1] + 86400.0


def test_advance_on_uncontrolled_key_initializes_anchor():
    clock = _clock()
    clock.advance(timedelta(days=1))
    assert clock.read_base() is not None  # 制御未開始でも基準点を作る
    expected = SystemClock().now() + timedelta(days=1)
    assert abs((clock.now() - expected).total_seconds()) < 5


def test_now_is_tz_aware_utc_under_control():
    clock = _clock()
    clock.set(datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC))
    got = clock.now()
    assert got.tzinfo is not None
    assert got.utcoffset() == timedelta(0)


def test_redis_failure_propagates_fail_closed():
    # Redis到達不能は例外で伝播(「制御下にあるつもりが実時間」の静かな誤りを
    # 許さない — design §2.2)。握って実時間へ落とす実装を禁止するピン
    class _BrokenClient:
        def get(self, key):
            raise ConnectionError("redis down")

        def set(self, key, value):
            raise ConnectionError("redis down")

    clock = ControlledClock(_BrokenClient())
    with pytest.raises(ConnectionError):
        clock.now()
    with pytest.raises(ConnectionError):
        clock.set(datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC))


def test_make_clock_system_mode(monkeypatch):
    monkeypatch.delenv("LATCH_CLOCK_MODE", raising=False)
    assert isinstance(make_clock(Settings()), SystemClock)


def test_make_clock_controlled_mode_with_injected_client():
    client = fakeredis.FakeRedis(decode_responses=True)
    clock = make_clock(Settings(clock_mode="controlled"), redis_client=client)
    assert isinstance(clock, ControlledClock)
    clock.set(datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC))  # 注入クライアントで動く
    assert clock.read_base() is not None


def test_make_clock_unknown_mode_raises():
    # 未実装値はValueError(push_mode/llm_modeと同一のfail-fast規律)
    with pytest.raises(ValueError):
        make_clock(Settings(clock_mode="bogus"))
