"""ws-4受け入れ(a): 環境前提・シード投入・3群メトリクス到達(M4 ws-4)。"""

from helpers_seed import seed_all, wait_pipeline_settled
from obs_results import record_item, wait_prom_series

from conftest import EXPECTED_HEAD


async def test_w4_a1_env_and_seed(api, db):
    """head=0008・health・シード投入と収束待ち(≤180秒)。"""
    ver = await db.fetchval("SELECT version_num FROM alembic_version")
    assert ver == EXPECTED_HEAD
    r = await api.get("/health")
    assert r.status_code == 200
    await seed_all(api, db)
    await wait_pipeline_settled(db)
    record_item(
        item_id="ws4-a1",
        name="環境前提(0008)とシード収束",
        test="test_w4_a1_env_and_seed",
        verdict="pass",
        observed={"alembic_head": ver, "health": r.json()["status"]},
    )


async def test_w4_a2_pipeline_metrics_visible(api, db):
    """3群のメトリクスがprometheusでseriesを持つ(収束後)。"""
    checks = {
        "pending": "latch_event_queue_pending",
        "hardfilter_pass": 'latch_hardfilter_pass_total{job="latch-staging-worker"}',
        "vector_items": (
            'latch_retrieval_vector_items_count{job="latch-staging-worker"}'
        ),
        "candidates": "latch_pipeline_candidates",
        "http_requests": 'latch_http_requests_total{path="/health"}',
        "jev": 'latch_jev_executions_total{provider="typesafe_jev"}',
        "score": 'latch_score_count{shape="pair"}',
    }
    observed = {}
    for name, expr in checks.items():
        series = await wait_prom_series(expr)
        observed[name] = len(series)
        assert series, f"{name} series missing: {expr}"
    record_item(
        item_id="ws4-a2",
        name="3群メトリクス到達(prometheus)",
        test="test_w4_a2_pipeline_metrics_visible",
        verdict="pass",
        observed=observed,
    )
