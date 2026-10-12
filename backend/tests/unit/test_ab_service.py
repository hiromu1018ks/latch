"""AbServiceの解決規則(unit・Callable注入を偽装・05 v0.8 実装規定)。"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from latch.ab.rules import (
    VARIANT_CONTROL,
    VARIANT_TREATMENT,
    assign_variant,
)
from latch.ab.service import (
    DEFAULT_LATCH_THRESHOLD,
    DEFAULT_NOTIFICATION_CONCURRENT,
    DEFAULT_NOTIFICATION_DAILY,
    AbExperiment,
    AbService,
)
from latch.core.clock import FakeClock


def _uid_with_variant(key: str, stratum: str, wanted: str, start: int = 1) -> uuid.UUID:
    """ハッシュがwantedになるUUIDを決定的に探す(割付は決定的なので事前計算可能)。"""
    n = start
    while True:
        uid = uuid.UUID(int=n)
        if assign_variant(key, stratum, uid) == wanted:
            return uid
        n += 1


class FakeStore:
    """ab_experiments/ab_assignmentsの偽装(挿入は辞書へ・冪等)。"""

    def __init__(self) -> None:
        self.experiments: dict[str, AbExperiment] = {}
        self.assignments: dict[tuple[str, uuid.UUID], str] = {}
        self.active_counts: dict[uuid.UUID, int] = {}
        self.inserts: list[tuple] = []

    def service(self, clock=None) -> AbService:
        async def fetch_experiment(key):
            return self.experiments.get(key)

        async def fetch_user_assignments(provider, subject):
            return []

        async def fetch_assignments(key, user_ids):
            ids = list(user_ids)
            return {
                u: v for (k, u), v in self.assignments.items() if k == key and u in ids
            }

        async def count_active_intents(uid):
            return self.active_counts.get(uid, 0)

        async def insert_assignment(key, uid, variant, stratum, now):
            self.assignments.setdefault((key, uid), variant)
            self.inserts.append((key, uid, variant, stratum))

        return AbService(
            clock=clock or FakeClock(datetime(2026, 10, 12, 12, tzinfo=UTC)),
            fetch_experiment=fetch_experiment,
            fetch_user_assignments=fetch_user_assignments,
            fetch_assignments=fetch_assignments,
            count_active_intents=count_active_intents,
            insert_assignment=insert_assignment,
        )


def _running(key="latch_threshold", control="0.6", treatment="0.5"):
    return AbExperiment(
        key=key,
        status="running",
        control_value=control,
        treatment_value=treatment,
        started_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_no_experiment_returns_defaults():
    store = FakeStore()
    svc = store.service()
    pair = (uuid.uuid4(), uuid.uuid4())
    assert await svc.pair_threshold(pair) == pytest.approx(DEFAULT_LATCH_THRESHOLD)
    assert await svc.pair_daily_limit(pair) == DEFAULT_NOTIFICATION_DAILY
    assert await svc.pair_concurrent_limit(pair) == DEFAULT_NOTIFICATION_CONCURRENT
    assert await svc.drain_threshold() == pytest.approx(DEFAULT_LATCH_THRESHOLD)
    assert store.assignments == {}


@pytest.mark.asyncio
async def test_stopped_experiment_applies_control_to_all():
    """停止=全群へ対照値(09 §5持続規定・05 v0.8 規定6)。treatment割付済みでも0.6。"""
    store = FakeStore()
    store.experiments["latch_threshold"] = AbExperiment(
        key="latch_threshold",
        status="stopped",
        control_value="0.6",
        treatment_value="0.5",
        started_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    svc = store.service()
    t1 = _uid_with_variant("latch_threshold", "0", VARIANT_TREATMENT)
    t2 = _uid_with_variant("latch_threshold", "0", VARIANT_TREATMENT, start=100)
    assert await svc.pair_threshold((t1, t2)) == pytest.approx(0.6)
    assert store.assignments == {}  # 停止中は割付も発生しない


@pytest.mark.asyncio
async def test_future_start_is_not_running_yet():
    store = FakeStore()
    store.experiments["latch_threshold"] = AbExperiment(
        key="latch_threshold",
        status="running",
        control_value="0.6",
        treatment_value="0.5",
        started_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    svc = store.service()
    assert await svc.pair_threshold((uuid.uuid4(),)) == pytest.approx(
        DEFAULT_LATCH_THRESHOLD
    )


@pytest.mark.asyncio
async def test_all_treatment_pair_uses_treatment_value_and_assigns():
    store = FakeStore()
    store.experiments["latch_threshold"] = _running()
    svc = store.service()
    a = _uid_with_variant("latch_threshold", "0", VARIANT_TREATMENT)
    b = _uid_with_variant("latch_threshold", "0", VARIANT_TREATMENT, start=10_000)

    value = await svc.pair_threshold((a, b))
    assert value == pytest.approx(0.5)
    assert store.assignments[("latch_threshold", a)] == VARIANT_TREATMENT
    assert store.assignments[("latch_threshold", b)] == VARIANT_TREATMENT
    # stratumはactive count 0→'0'として記録(05 v0.8 規定2・3)
    assert ("latch_threshold", a, VARIANT_TREATMENT, "0") in [
        (k, u, v, s) for (k, u, v, s) in store.inserts
    ]


@pytest.mark.asyncio
async def test_mixed_pair_uses_control_value():
    store = FakeStore()
    store.experiments["latch_threshold"] = _running()
    svc = store.service()
    a = _uid_with_variant("latch_threshold", "0", VARIANT_TREATMENT)
    b = _uid_with_variant("latch_threshold", "0", VARIANT_CONTROL, start=10_000)

    assert await svc.pair_threshold((a, b)) == pytest.approx(0.6)  # FR-25


@pytest.mark.asyncio
async def test_existing_assignment_is_reused_not_recomputed():
    """割付済みユーザーは再計算しない(永続化行が真実)。"""
    store = FakeStore()
    store.experiments["notification_daily_limit"] = _running(
        "notification_daily_limit", "6", "8"
    )
    a, b = uuid.uuid4(), uuid.uuid4()
    store.assignments[("notification_daily_limit", a)] = VARIANT_TREATMENT
    store.assignments[("notification_daily_limit", b)] = VARIANT_TREATMENT
    svc = store.service()
    assert await svc.pair_daily_limit((a, b)) == 8
    assert await svc.pair_daily_limit((a, b)) == 8
    assert store.inserts == []


@pytest.mark.asyncio
async def test_stratum_recorded_from_active_intent_count():
    store = FakeStore()
    store.experiments["latch_threshold"] = _running()
    a = uuid.uuid4()
    store.active_counts[a] = 4
    svc = store.service()
    # aがtreatmentになるハッシュ前提ではなく値の記録だけ検査
    await svc.pair_threshold((a,))
    strata = [s for (k, u, v, s) in store.inserts if u == a]
    assert strata == ["3+"]


@pytest.mark.asyncio
async def test_drain_threshold_takes_min_of_both_values():
    store = FakeStore()
    store.experiments["latch_threshold"] = _running("latch_threshold", "0.6", "0.5")
    assert await store.service().drain_threshold() == pytest.approx(0.5)
    store.experiments["latch_threshold"] = _running("latch_threshold", "0.6", "0.7")
    assert await store.service().drain_threshold() == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_fetch_experiment_failure_falls_back_to_default():
    store = FakeStore()
    base = store.service()

    async def broken(key):
        raise RuntimeError("db down")

    svc = AbService(
        clock=FakeClock(datetime(2026, 10, 12, 12, tzinfo=UTC)),
        fetch_experiment=broken,
        fetch_user_assignments=base._fetch_user_assignments,
        fetch_assignments=base._fetch_assignments,
        count_active_intents=base._count_active_intents,
        insert_assignment=base._insert_assignment,
    )
    assert await svc.pair_threshold((uuid.uuid4(),)) == pytest.approx(
        DEFAULT_LATCH_THRESHOLD
    )


@pytest.mark.asyncio
async def test_experiment_cache_expires_after_ttl():
    """30秒キャッシュ(05 v0.8 規定7)。TTL後は開始された実験が見える。"""
    clock = FakeClock(datetime(2026, 10, 12, 12, tzinfo=UTC))
    store = FakeStore()
    svc = store.service(clock=clock)
    treatment_uid = _uid_with_variant("latch_threshold", "0", VARIANT_TREATMENT)
    assert await svc.pair_threshold((treatment_uid,)) == pytest.approx(0.6)

    store.experiments["latch_threshold"] = _running("latch_threshold", "0.6", "0.9")
    assert await svc.pair_threshold((treatment_uid,)) == pytest.approx(
        0.6
    )  # キャッシュ内
    clock.advance(timedelta(seconds=31))
    assert await svc.pair_threshold((treatment_uid,)) == pytest.approx(0.9)
