"""Clock適用範囲6点(04 第5節・10 第1節)の再現性証明(design §4.2)。

ドメイン実装がまだ無いM0時点では、各点の判定形をテスト内ヘルパーとして模倣し、
FakeClock の set/advance のみで判定結果を制御できること(=実時間の待ちを排除できる
こと)を立証する。ヘルパー内の時刻取得はすべて引数の clock から行う。
"""

from datetime import UTC, date, datetime, timedelta

from latch.core.clock import Clock, FakeClock


def _deadline_expired(deadline: datetime, clock: Clock) -> bool:
    """(1) API期限判定の判定形。DBのclock_timestamp()は使わない。"""
    return clock.now() >= deadline


def _next_run_due(last_run: datetime, clock: Clock) -> bool:
    """(2) 期限バッチ(expiry_sweeper)60秒周期の到来判定形。"""
    return clock.now() >= last_run + timedelta(seconds=60)


def _bucket_start(now: datetime) -> datetime:
    """(3) 30分Bucket境界。nowから30分区切り(切捨て)を導く。詳細は06(M2で確定)。"""
    minute = (now.minute // 30) * 30
    return now.replace(minute=minute, second=0, microsecond=0)


def _in_debounce_window(last_event_at: datetime, clock: Clock) -> bool:
    """(4) debounce 10秒窓の判定形。窓の境界計算はClock値から導く。"""
    return clock.now() - last_event_at <= timedelta(seconds=10)


def _start_at_valid(start_at: datetime, clock: Clock) -> bool:
    """(6) 作成・更新の時刻検証(過去不可・上限7日)の判定形(05 第5節)。"""
    now = clock.now()
    return now <= start_at <= now + timedelta(days=7)


def test_1_api_deadline_judgement():
    clock = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    deadline = clock.now() + timedelta(hours=2)
    assert not _deadline_expired(deadline, clock)  # 期限前
    clock.advance(timedelta(hours=2, seconds=1))  # Δ+ε 進める(実時間の待ちなし)
    assert _deadline_expired(deadline, clock)  # 期限切れ


def test_2_expiry_sweeper_schedule():
    clock = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    last_run = clock.now()
    clock.advance(timedelta(seconds=59))
    assert not _next_run_due(last_run, clock)  # 未到来
    clock.advance(timedelta(seconds=2))  # 計61秒 = 60s+ε
    assert _next_run_due(last_run, clock)  # 到来


def test_3_thirty_min_bucket_boundary():
    clock = FakeClock(datetime(2026, 9, 27, 12, 29, 59, tzinfo=UTC))
    assert _bucket_start(clock.now()) == datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
    clock.advance(timedelta(seconds=1))  # 境界直後
    assert _bucket_start(clock.now()) == datetime(2026, 9, 27, 12, 30, 0, tzinfo=UTC)


def test_4_debounce_ten_second_window():
    clock = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    last_event_at = clock.now()
    clock.advance(timedelta(seconds=9))
    assert _in_debounce_window(last_event_at, clock)  # 窓内
    clock.advance(timedelta(seconds=2))  # 計11秒
    assert not _in_debounce_window(last_event_at, clock)  # 窓外


def test_5_jst_reset_boundary():
    clock = FakeClock(datetime(2026, 9, 30, 14, 59, 59, tzinfo=UTC))
    assert clock.jst_date() == date(2026, 9, 30)  # JST 23:59:59 = まだ9/30
    clock.advance(timedelta(seconds=1))  # JST 10-01 0:00
    assert clock.jst_date() == date(2026, 10, 1)  # リセットジョブの起動境界


def test_6_start_at_validation_flips_with_set():
    clock = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    start_at = datetime(2026, 9, 27, 13, 0, 0, tzinfo=UTC)
    assert _start_at_valid(start_at, clock)  # 未来 → 受理
    clock.set(datetime(2026, 9, 27, 14, 0, 0, tzinfo=UTC))
    assert not _start_at_valid(start_at, clock)  # 過去に反転 → 拒否


def test_6_start_at_validation_seven_day_upper_bound():
    clock = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    within = clock.now() + timedelta(days=7)
    over = clock.now() + timedelta(days=7, seconds=1)
    assert _start_at_valid(within, clock)  # 上限内
    assert not _start_at_valid(over, clock)  # 超過
