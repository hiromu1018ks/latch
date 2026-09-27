"""Clock基本試験(design §4.1・§4.2)。"""

from datetime import UTC, datetime, timedelta

from latch.core.clock import JST, SystemClock


def test_system_clock_returns_tz_aware_utc():
    before = datetime.now(UTC)
    got = SystemClock().now()
    after = datetime.now(UTC)
    assert got.tzinfo is not None
    assert got.utcoffset() == timedelta(0)
    assert before <= got <= after


def test_jst_is_fixed_offset_plus9():
    assert JST.utcoffset(None) == timedelta(hours=9)
