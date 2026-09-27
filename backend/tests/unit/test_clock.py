"""Clock基本試験(design §4.1・§4.2)。"""

from datetime import UTC, date, datetime, timedelta

import pytest

from latch.core.clock import JST, FakeClock, SystemClock


def test_system_clock_returns_tz_aware_utc():
    before = datetime.now(UTC)
    got = SystemClock().now()
    after = datetime.now(UTC)
    assert got.tzinfo is not None
    assert got.utcoffset() == timedelta(0)
    assert before <= got <= after


def test_jst_is_fixed_offset_plus9():
    assert JST.utcoffset(None) == timedelta(hours=9)


@pytest.fixture
def fake() -> FakeClock:
    return FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))


def test_fake_clock_now_returns_initial(fake):
    assert fake.now() == datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def test_fake_clock_set_moves_both_directions(fake):
    fake.set(datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC))  # 後退
    assert fake.now().hour == 10
    fake.set(datetime(2026, 9, 28, 23, 59, 59, tzinfo=UTC))  # 前進
    assert fake.now().day == 28


def test_fake_clock_advance(fake):
    fake.advance(timedelta(hours=2, seconds=1))
    assert fake.now() == datetime(2026, 9, 27, 14, 0, 1, tzinfo=UTC)


def test_fake_clock_rejects_naive_initial():
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 9, 27, 12, 0, 0))


def test_fake_clock_set_rejects_naive(fake):
    with pytest.raises(ValueError):
        fake.set(datetime(2026, 9, 27, 12, 0, 0))


def test_fake_clock_thread_safe():
    import threading

    clock = FakeClock(datetime(2026, 9, 27, 0, 0, 0, tzinfo=UTC))
    barrier = threading.Barrier(8)

    def tick():
        barrier.wait()
        for _ in range(100):
            clock.advance(timedelta(seconds=1))

    threads = [threading.Thread(target=tick) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 8スレッド×100回のadvanceが1つもロストしない(決定性)
    assert clock.now() == datetime(2026, 9, 27, 0, 13, 20, tzinfo=UTC)


def test_jst_date_boundary_at_midnight():
    clock = FakeClock(datetime(2026, 9, 30, 14, 59, 59, tzinfo=UTC))
    assert clock.jst_date() == date(2026, 9, 30)  # JST 23:59:59
    clock.advance(timedelta(seconds=1))
    assert clock.jst_date() == date(2026, 10, 1)  # JST 10-01 0:00(=UTC 09-30 15:00)


def test_jst_date_month_start_is_not_utc_month_start():
    # UTCの暦日付はまだ9月のまま、JST日付は10月 — TTL方式不採用の根拠となったずれ
    clock = FakeClock(datetime(2026, 9, 30, 15, 0, 0, tzinfo=UTC))
    assert clock.now().date() == date(2026, 9, 30)
    assert clock.jst_date() == date(2026, 10, 1)
