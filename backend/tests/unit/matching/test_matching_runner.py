"""runnerの配線(no-op判定→検索→記録)のunit試験(design §4.1)。

monkeypatchでorigin/layer2/candidatesを差し替え、no-op時に検索・記録が
走らないこととOutcomeの構成を検証する(スタブconn・決定的)。
runnerはモジュール属性経由で関数を呼ぶため差し替え可能(§9固定値)。
"""

import uuid
from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock
from latch.worker.matching import runner
from latch.worker.matching.candidates import CandidatePair
from latch.worker.matching.layer2 import RetrievedCandidate
from latch.worker.matching.origin import (
    SKIP_EMBEDDING_NULL,
    Origin,
    OriginLoad,
)

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
IID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
TARGET = uuid.UUID("00000000-0000-4000-8000-0000000000c3")


def _origin() -> Origin:
    return Origin(
        intent_id=IID,
        version=1,
        user_id=uuid.UUID("00000000-0000-4000-8000-0000000000b2"),
        category_primary="drinking",
        alcohol_involved=False,
        budget_max=None,
        participants_min=2,
        participants_max=2,
        geo_lon=130.5581,
        geo_lat=31.5965,
        geo_radius_m=1000,
        time_start=NOW,
        time_end=NOW + timedelta(hours=3),
        embedding="[1.0]",
        user_ge_20=True,
        evaluated_at=NOW,
    )


async def test_noop_skips_layer2_and_candidates(monkeypatch):
    """no-op(design §2.5)では検索も記録も呼ばれない。"""
    calls = {"topk": 0, "upsert": 0}

    async def fake_load(conn, clock, iid):
        assert iid == IID
        return OriginLoad(origin=None, skip_reason=SKIP_EMBEDDING_NULL)

    async def fake_topk(conn, org):
        calls["topk"] += 1
        return [], 0

    async def fake_upsert(conn, **kwargs):
        calls["upsert"] += 1
        raise AssertionError("no-opでは呼ばれない")

    monkeypatch.setattr(runner.origin, "load_origin", fake_load)
    monkeypatch.setattr(runner.layer2, "retrieve_topk", fake_topk)
    monkeypatch.setattr(runner.candidates, "upsert_candidate", fake_upsert)

    outcome = await runner.run_candidate_retrieval(None, FakeClock(NOW), IID)
    assert outcome.skip_reason == SKIP_EMBEDDING_NULL
    assert outcome.version is None
    assert outcome.layer1_pass_count == 0
    assert outcome.pairs == []
    assert calls == {"topk": 0, "upsert": 0}


async def test_normal_path_records_pairs(monkeypatch):
    """検索結果の各対象を記録へ回しOutcomeへ載せる(pass_count含む)。"""
    org = _origin()

    async def fake_load(conn, clock, iid):
        return OriginLoad(origin=org, skip_reason=None)

    async def fake_topk(conn, o):
        assert o is org
        return ([RetrievedCandidate(TARGET, 2, 0.98)], 7)

    upserted = {}

    async def fake_upsert(conn, *, origin, candidate_id, candidate_version, similarity):
        upserted["args"] = (candidate_id, candidate_version, similarity)
        a, b = sorted((origin.intent_id, candidate_id))
        return CandidatePair(a, b, similarity)

    monkeypatch.setattr(runner.origin, "load_origin", fake_load)
    monkeypatch.setattr(runner.layer2, "retrieve_topk", fake_topk)
    monkeypatch.setattr(runner.candidates, "upsert_candidate", fake_upsert)

    outcome = await runner.run_candidate_retrieval(None, FakeClock(NOW), IID)
    assert outcome.skip_reason is None
    assert outcome.intent_id == IID
    assert outcome.version == 1
    assert outcome.layer1_pass_count == 7
    assert len(outcome.pairs) == 1
    assert outcome.pairs[0].retrieval_score == 0.98
    assert upserted["args"] == (TARGET, 2, 0.98)


async def test_empty_result_records_nothing(monkeypatch):
    """Layer 1通過0件(検索結果空)でも正常終了(skip扱いにしない)。"""
    org = _origin()

    async def fake_load(conn, clock, iid):
        return OriginLoad(origin=org, skip_reason=None)

    async def fake_topk(conn, o):
        return [], 0

    async def fake_upsert(conn, **kwargs):
        raise AssertionError("0件では呼ばれない")

    monkeypatch.setattr(runner.origin, "load_origin", fake_load)
    monkeypatch.setattr(runner.layer2, "retrieve_topk", fake_topk)
    monkeypatch.setattr(runner.candidates, "upsert_candidate", fake_upsert)

    outcome = await runner.run_candidate_retrieval(None, FakeClock(NOW), IID)
    assert outcome.skip_reason is None
    assert outcome.version == 1
    assert outcome.pairs == [] and outcome.layer1_pass_count == 0
