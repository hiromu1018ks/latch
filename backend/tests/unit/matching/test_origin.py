"""origin(起点読み込み・no-op判定・bind params)のunit試験(design §4.1)。

スタブ行で決定的に。20歳計算はusers.age_yearsと同じJST暦日の満年齢
(06 §2二重防御の基準一致)。実DBのSQL正当性はintegration(test-ci)が担う。
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from latch.core.clock import FakeClock
from latch.worker.matching.origin import (
    SKIP_EMBEDDING_NULL,
    SKIP_GEO_MISSING,
    SKIP_NOT_ACTIVE,
    SKIP_NOT_FOUND,
    SKIP_PARTICIPANTS,
    SKIP_TIME_MISSING,
    _embedding_text,
    bind_params,
    load_origin,
)

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-29 21:00
IID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
USER = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
LEAP_NOW = datetime(2026, 2, 28, 3, 0, 0, tzinfo=UTC)  # JST 2026-02-28 12:00


class _FakeMappings:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return _FakeMappings(self._row)


class StubConn:
    """load_originが1回呼ぶexecuteへスタブ行を返す。"""

    def __init__(self, row):
        self.row = row

    async def execute(self, stmt, params=None):
        return _FakeResult(self.row)


def _row(**overrides):
    """active・補完対象ありの標準行(時間はNOW+3h開始・end未補完)。"""
    base = dict(
        id=str(IID),
        version=1,
        user_id=str(USER),
        category_primary="drinking",
        alcohol_involved=False,
        budget_max=None,
        participants_min=2,
        participants_max=4,
        geo_radius_m=None,
        time_start=NOW + timedelta(hours=3),
        time_end=None,
        status="active",
        embedding="[1.0,0.0]",
        lon=130.5581,
        lat=31.5965,
        birth_date=date(1990, 4, 1),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _load(row, clock=None):
    return await load_origin(StubConn(row), clock or FakeClock(NOW), IID)


async def test_active_origin_built_with_completions():
    """active行は補完済みでOrigin化(end未指定→+3h・radius NULL→1000)。"""
    loaded = await _load(_row())
    assert loaded.skip_reason is None
    org = loaded.origin
    assert org is not None
    assert org.intent_id == IID and org.user_id == USER  # str→UUID復元
    assert org.time_end == org.time_start + timedelta(hours=3)  # default_time_end
    assert org.geo_radius_m == 1000  # DEFAULT_RADIUS_M補完
    assert org.embedding == "[1.0,0.0]"
    assert org.evaluated_at == NOW
    assert org.user_ge_20 is True  # 1990年生まれ・2026年で36歳


async def test_skip_not_found():
    loaded = await _load(None)
    assert loaded.origin is None
    assert loaded.skip_reason == SKIP_NOT_FOUND


async def test_skip_not_active_for_draft():
    loaded = await _load(_row(status="draft"))
    assert loaded.skip_reason == SKIP_NOT_ACTIVE


async def test_skip_embedding_null():
    loaded = await _load(_row(embedding=None))
    assert loaded.skip_reason == SKIP_EMBEDDING_NULL


async def test_skip_geo_missing():
    loaded = await _load(_row(lon=None, lat=None))
    assert loaded.skip_reason == SKIP_GEO_MISSING


async def test_skip_time_missing():
    loaded = await _load(_row(time_start=None))
    assert loaded.skip_reason == SKIP_TIME_MISSING


async def test_skip_participants_range():
    """起点が人数条件 2∈[min,max] を満たさない→no-op(06 §2・design §2.5)。"""
    loaded = await _load(_row(participants_min=3, participants_max=4))
    assert loaded.skip_reason == SKIP_PARTICIPANTS


async def test_user_ge_20_at_exact_birthday_and_day_before():
    """誕生日当日=満20歳(True)・誕生日前日=19歳(False)。JST暦日基準。"""
    on_day = await _load(_row(birth_date=date(2006, 9, 29)))  # JST今日が誕生日
    assert on_day.origin is not None and on_day.origin.user_ge_20 is True
    day_before = await _load(_row(birth_date=date(2006, 9, 30)))  # 誕生日は明日
    assert day_before.origin is not None and day_before.origin.user_ge_20 is False


async def test_user_ge_20_leap_day_boundary():
    """2/29生まれ: うるう年でない年の2/28はまだ加算されず21歳(20歳以上)。"""
    loaded = await _load(_row(birth_date=date(2004, 2, 29)), clock=FakeClock(LEAP_NOW))
    assert loaded.origin is not None and loaded.origin.user_ge_20 is True


def test_embedding_text_variants():
    """文字列はそのまま・配列は組立直し・NoneはNone(design §5-2の防御)。"""
    assert _embedding_text("[0.1,0.2]") == "[0.1,0.2]"
    assert _embedding_text(None) is None
    assert _embedding_text([0.1, 0.2]) == "[0.1,0.2]"


async def test_bind_params_keys_and_values():
    """Layer1/2共通bind param一式(§9固定値・CAST対象を含む)。"""
    loaded = await _load(_row())
    assert loaded.origin is not None
    params = bind_params(loaded.origin)
    assert set(params) == {
        "origin_user_id",
        "origin_category",
        "origin_time_start",
        "origin_time_end",
        "origin_lon",
        "origin_lat",
        "origin_radius_m",
        "origin_budget",
        "origin_alcohol",
        "origin_user_ge_20",
        "now",
    }
    assert params["origin_user_id"] == USER
    assert params["origin_budget"] is None
    assert params["now"] == loaded.origin.evaluated_at
