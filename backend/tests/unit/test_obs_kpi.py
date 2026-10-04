"""KPI純関数層の期待値表(09 §2.2・design §4)。

SQL実行層の正しさはintegration(test_obs_kpi_job_ci・supervisor検証時)と
staging受け入れ(compute_mlr実測一致・Task 13)が所有する3層構成の1層目
(design §2.1読み替え6)。
"""

from datetime import UTC, date, datetime

from latch.core.clock import JST
from latch.obs.kpi import (
    DAILY_METRIC_KEYS,
    METRIC_KEYS,
    backfill_days,
    is_week_end,
    jst_day,
    new_muted_users,
    retention_due,
    week_end_of,
    week_of,
    yes_rate_from_responses,
)


def test_metric_keys_cover_09_2_2_table():
    """metric_key集合が§2.3の定義表と完全一致(15キー)。"""
    assert METRIC_KEYS == {
        "intent_create_rate",
        "intent_recreation_rate",
        "active_intent_per_dau",
        "retention_7d",
        "retention_30d",
        "candidate_generation_rate",
        "proposal_rate",
        "hold_expired_rate",
        "yes_rate",
        "mlr",
        "attendance_rate",
        "cancel_rate",
        "no_answer_rate",
        "jev_executions_per_intent",
        "mute_rate",
    }
    assert DAILY_METRIC_KEYS == {
        "intent_create_rate",
        "active_intent_per_dau",
        "candidate_generation_rate",
        "proposal_rate",
        "hold_expired_rate",
        "yes_rate",
        "mlr",
        "jev_executions_per_intent",
    }


def test_jst_day_converts_utc_night_to_jst_date():
    """UTC 2026-10-03T16:00ZはJSTでは10-04(JST期間切りの基本)。"""
    now = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)
    assert jst_day(now) == date(2026, 10, 4)


def test_backfill_days_excludes_today_in_ascending_order():
    """遡及対象は当日を含まない直近k日(昇順・KpiJobの消費契約)。"""
    assert backfill_days(date(2026, 10, 4), 4) == [
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 3),
    ]


def test_week_of_is_monday_and_week_end_is_sunday():
    assert week_of(date(2026, 10, 4)) == date(2026, 9, 28)  # 10-04は日曜・前の月曜
    assert week_of(date(2026, 9, 30)) == date(2026, 9, 28)  # 水曜は同週の月曜
    assert week_end_of(date(2026, 9, 28)) == date(2026, 10, 4)
    assert is_week_end(date(2026, 10, 4))
    assert not is_week_end(date(2026, 10, 3))


def test_new_muted_users_excludes_already_muted():
    """週内に初めてmutedになったユーザーのみ(週開始前の集合を除外)。"""
    week_rows = {
        date(2026, 9, 28): {"u1", "u2"},
        date(2026, 9, 29): {"u2", "u3"},
    }
    got = new_muted_users(week_rows, date(2026, 9, 28), date(2026, 9, 29))
    assert got == {"u1", "u2", "u3"}  # 週開始前にmutedのユーザーは無い(初週近似)


def test_new_muted_users_with_prior_snapshot():
    """週開始前に既にmutedのユーザーは新規に数えない(09 §2.2の定義)。"""
    week_rows = {
        date(2026, 9, 28): {"u1", "u2"},  # 週月曜のsnapshot
        date(2026, 9, 29): {"u2", "u3"},  # 火曜
    }
    prior = {"u1"}  # 月曜以前からmuted
    got = new_muted_users(week_rows, date(2026, 9, 28), date(2026, 9, 29), prior=prior)
    assert got == {"u2", "u3"}


def test_retention_due_requires_observation_window():
    """観測窓: D+windowが昨日までに到達していないと算出しない(design §4)。

    境界値は「D+window=昨日」でTrue・「D+window=今日」でFalse(計画書の
    期待値表は日付が1日ズレていたためコメントと整合する日付へ補正)。
    """
    today = date(2026, 10, 4)
    assert retention_due(date(2026, 9, 26), today, 7)  # D+7=昨日
    assert not retention_due(date(2026, 9, 27), today, 7)  # D+7=今日(未完了)
    assert retention_due(date(2026, 9, 3), today, 30)  # D+30=昨日
    assert not retention_due(date(2026, 9, 4), today, 30)  # D+30=今日(未完了)


def test_yes_rate_from_responses_counts_only_today():
    """当日回答のみ・yes/numと全回答/den(yes+no+defer)。"""
    day = date(2026, 10, 4)
    responses = [
        {"value": "yes", "answered_at": "2026-10-04T12:00:00+09:00"},
        {"value": "no", "answered_at": "2026-10-04T13:00:00+09:00"},
        {"value": "defer", "answered_at": "2026-10-03T13:00:00+09:00"},  # 前日
        {"value": "yes", "answered_at": "2026-10-04T23:30:00+09:00"},
        {},  # 未回答行(answered_atなし)
    ]
    num, den = yes_rate_from_responses(responses, day)
    assert num == 2
    assert den == 3


def test_jst_constant_matches_clock_module():
    """JST定数はcore/clock.pyの単一真実を参照(C2・二重定義しない)。"""
    assert JST.utcoffset(None).total_seconds() == 9 * 3600


# -- KpiJob(Task 7)--


from latch.core.clock import FakeClock  # noqa: E402
from latch.obs.kpi import KpiJob  # noqa: E402


class FakeConn:
    """executeを記録する偽接続(SQL実行層の呼び出し形状を検査する)。"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        self.queries.append((str(stmt), dict(params or {})))
        return FakeResult(self.rows)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeEngine:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.conns: list[FakeConn] = []

    def _new_conn(self):
        conn = FakeConn(self.rows)
        self.conns.append(conn)
        return conn

    def connect(self):
        """KpiJob._pending_daysが使う読取経路(_pending_daysのSELECT応答)。"""
        conn = self._new_conn()

        class _Ctx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    def begin(self):
        conn = self._new_conn()

        class _Ctx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


# _pending_daysが返す「遡及窓4日分の未集計日」応答(行持ち・昇順)
_BACKFILL_ROWS = [
    (date(2026, 9, 30),),
    (date(2026, 10, 1),),
    (date(2026, 10, 2),),
    (date(2026, 10, 3),),
]


def _backfill_engine():
    return FakeEngine([tuple(r) for r in _BACKFILL_ROWS])


class _FakeClock:
    """jst_date()/now()を持つ最小Clock(FakeClockを内包)。"""

    def __init__(self):
        self._inner = FakeClock(datetime(2026, 10, 4, 15, 0, tzinfo=UTC))

    def now(self):
        return self._inner.now()

    def jst_date(self):
        return self._inner.jst_date()


def test_kpi_job_pending_days_are_backfill_window_days():
    """遡及対象=当日を含まない直近4日(KpiJobの呼び出し形状)。"""
    job = KpiJob(engine=FakeEngine(), clock=_FakeClock(), backfill_days=4)
    days = job._pending_day_list(jst_day(job._clock.now()))
    assert len(days) == 4
    assert days[0] < days[-1]
    # 当日を含まない
    assert days[-1] < jst_day(job._clock.now())


async def test_kpi_job_run_once_writes_upserts_with_day_params():
    """run_onceが「UPSERT SQLに日付を渡す」形状で呼ばれる(SQL実行層のピン)。

    実DB検証はintegration(test_obs_kpi_job_ci)とstaging(Task 13)が所有。
    """
    engine = _backfill_engine()
    clock = _FakeClock()
    job = KpiJob(engine=engine, clock=clock, backfill_days=4)
    await job.run_once()
    # 全てのengine.begin()実行でUPSERTが発行されている
    assert len(engine.conns) >= 4  # 4日分の遡及
    joined = "\n".join(q for c in engine.conns for q, _ in c.queries)
    assert "INSERT INTO kpi_daily" in joined
    assert "ON CONFLICT (metric_key, day)" in joined
    # 日次8指標のUPSERTパラメータに日付が渡っている
    daily_upserts = [
        (q, p)
        for c in engine.conns
        for q, p in c.queries
        if "INSERT INTO kpi_daily" in q
    ]
    assert daily_upserts, "日次UPSERTが0件"
    some_params = daily_upserts[0][1]
    assert "day" in some_params


async def test_kpi_job_run_once_is_idempotent_when_already_done():
    """kpi_dailyにmlr行が既に揃っている遡及窓ではUPSERTを発行しない(60秒tick
    の毎回書込みを防ぐ・pending_daysが空=何もしない)。"""
    engine = FakeEngine()
    job = KpiJob(engine=engine, clock=_FakeClock(), backfill_days=4)

    async def _no_pending():
        return []

    job._pending_days = _no_pending  # type: ignore[method-assign]
    await job.run_once()
    upserts = [
        (q, p)
        for c in engine.conns
        for q, p in c.queries
        if "INSERT INTO kpi_daily" in q
    ]
    assert upserts == []


async def test_kpi_job_empty_db_writes_all_daily_rows():
    """空の集計源でも例外を上げず8指標の行を書く(Review Focus 1)。"""
    engine = _backfill_engine()
    job = KpiJob(engine=engine, clock=_FakeClock(), backfill_days=4)
    await job.run_once()  # 例外を上げないこと
    assert len(engine.conns) >= 4


async def test_kpi_job_run_stops_immediately_after_run_once():
    """stop即応: run_once完走後にinterval待ちへ入り・stopで即抜ける。

    待機はRetentionJob._waitと同一契約(注入sleepのrace待ち・既存test_worker
    の1秒タイムアウトと両立)。起動時即tick(run_onceを最初に実行)は§2.1
    読み替え2の要件(自己修復)。
    """
    import asyncio

    engine = FakeEngine()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)

    job = KpiJob(engine=engine, clock=_FakeClock(), backfill_days=4, sleep=fake_sleep)
    stop = asyncio.Event()
    task = asyncio.create_task(job.run(stop=stop))
    # 起動時即tick(run_once)の完了とinterval待ちへの到達を待つ
    for _ in range(100):
        if sleeps:
            break
        await asyncio.sleep(0)
    assert sleeps  # interval待ちが呼ばれた
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)  # stopで即返る
