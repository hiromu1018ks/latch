"""retrieval・Jev・score観測の増分(M4 ws-4・ロジック変更なしの観測追加)。

単例metrics(グローバルREGISTRY)はpytestプロセス内で累積するため、
すべて差分(before/after)で検証する。
"""

import uuid
from datetime import UTC, datetime

from prometheus_client import REGISTRY

from latch.obs.metrics import observe_jev_execution
from latch.worker.main import Worker
from latch.worker.matching.runner import RetrievalOutcome

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _outcome(pass_count: int, vector_items: int) -> RetrievalOutcome:
    return RetrievalOutcome(
        intent_id=uuid.uuid4(),
        version=1,
        layer1_pass_count=pass_count,
        pairs=[],
        topkc=[],
    )


def test_observe_retrieval_is_called_from_run_matching(monkeypatch, fake_clock):
    """Worker._run_matchingがrun_candidate_retrievalの結果を観測する。"""
    from latch.worker import main as worker_main

    captured = []

    async def fake_retrieval(conn, clock, intent_id):
        captured.append(clock)
        return _outcome(pass_count=13, vector_items=7)

    monkeypatch.setattr(worker_main, "run_candidate_retrieval", fake_retrieval)
    worker = Worker(clock=fake_clock, engine=object())
    before_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    before_pass = REGISTRY.get_sample_value("latch_hardfilter_pass_total") or 0
    import asyncio

    asyncio.run(worker._run_matching(conn=object(), intent_id=uuid.uuid4()))
    assert captured  # 検索が実行された
    after_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    after_pass = REGISTRY.get_sample_value("latch_hardfilter_pass_total") or 0
    assert after_input == before_input + 1
    assert after_pass == before_pass + 13
    assert REGISTRY.get_sample_value("latch_retrieval_vector_items_count") is not None


def test_observe_retrieval_from_direct_pipeline(monkeypatch, fake_clock):
    """Bucket/catch-up起点の直接投入も同じ観測を通る。"""
    from latch.worker import main as worker_main

    async def fake_retrieval(conn, clock, intent_id):
        return _outcome(pass_count=3, vector_items=2)

    monkeypatch.setattr(worker_main, "run_candidate_retrieval", fake_retrieval)
    worker = Worker(clock=fake_clock, engine=object())
    before_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    import asyncio

    # engineはbegin()のみの最小スタブ(既存test_worker.pyの
    # _FakePipelineEngineと同一作法・計画書のobject()ではbegin()が無い)
    class _Tx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    class _Engine:
        def begin(self):
            return _Tx()

    asyncio.run(worker._run_direct_pipeline(engine=_Engine(), intent_id=uuid.uuid4()))
    after_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    assert after_input == before_input + 1


def test_jev_observe_helper_is_used_for_execution_accounting():
    """usage無し・未知providerでも例外を上げない(観測は本体処理を壊さない)。"""
    before = (
        REGISTRY.get_sample_value(
            "latch_jev_executions_total", {"provider": "fallback_llm"}
        )
        or 0
    )
    observe_jev_execution(provider="fallback_llm", latency_s=0.0, usage=None)
    after = (
        REGISTRY.get_sample_value(
            "latch_jev_executions_total", {"provider": "fallback_llm"}
        )
        or 0
    )
    assert after == before + 1
