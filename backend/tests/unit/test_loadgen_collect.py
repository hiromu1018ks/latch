"""証拠採取の照合と組み立て(M4 ws-6)。

Prometheus照合の実動作は手順(§8)が所有する。unitではクエリの静的性質
(load側jobのみ・5目標と層別の網羅)と・モック照合での機械判定を検証する。
"""

from latch.core.clock import SystemClock
from latch.loadgen import collect


def test_perf_queries_cover_five_targets_and_layers():
    """5目標(①保存②parse③retrieval④E2E⑤通知)+層別+queueのクエリが揃う。"""
    keys = set(collect.PERF_QUERIES.keys())
    assert {
        "save_p95_s",
        "save_p99_s",
        "parse_p95_s",
        "parse_p99_s",
        "retrieval_p95_s",
        "embedding_p95_s",
        "proposal_p95_s",
        "jev_p95_s",
        "e2e_p95_create_s",
        "e2e_p95_activate_s",
        "notify_p95_s",
        "queue_pending_max",
    } <= keys


def test_queries_target_load_jobs_only():
    """perf・bfの全クエリはload側jobのみを叩く(stagingのseriesを混ぜない)。"""
    for expr in collect.PERF_QUERIES.values():
        assert 'job="latch-load-' in expr
        assert 'job="latch-staging-' not in expr
    for expr in collect.BRUTEFORCE_QUERIES.values():
        assert 'job="latch-load-' in expr


def test_window_replacement():
    assert collect._q("foo_bar[{WINDOW}]", "15m") == "foo_bar[15m]"


async def test_query_scalar_returns_none_when_empty(monkeypatch):
    async def fake_query(expr):
        return []

    monkeypatch.setattr(collect, "prom_query", fake_query)
    assert await collect.query_scalar("anything") is None


async def test_bruteforce_verdict_marks_vector_le50(monkeypatch):
    """le=50累積==総累積 → vector_le_50_ok=True(10 §4.2の機械判定)。"""
    values = {
        "vector_items_le50_rate": 123.0,
        "vector_items_inf_rate": 123.0,
        "retrieval_p95_s": 0.4,
        "hardfilter_pass_rate": 0.6,
        "jev_executions_total_increase": 132.0,
    }

    async def fake_scalar(expr):
        for name, expr_raw in collect.BRUTEFORCE_QUERIES.items():
            if collect._q(expr_raw, "30m") == expr:
                return values[name]
        return None

    monkeypatch.setattr(collect, "query_scalar", fake_scalar)
    report = await collect.collect_bruteforce_report(
        stage=10000,
        window="30m",
        injected_events=100,
        env_note_data={"host": "test"},
        clock=SystemClock(),
    )
    assert report["verdict"]["vector_le_50_ok"] is True
    assert report["input_conditions"]["injected_events"] == 100
    assert report["input_conditions"]["latency_injection"] == "none"


async def test_bruteforce_verdict_marks_violation(monkeypatch):
    """le=50を超える観測がある → 機械判定はFalse(修正を待たずに明示)。"""
    values = {
        "vector_items_le50_rate": 120.0,
        "vector_items_inf_rate": 123.0,
        "retrieval_p95_s": 0.4,
        "hardfilter_pass_rate": 0.6,
        "jev_executions_total_increase": 132.0,
    }

    async def fake_scalar(expr):
        for name, expr_raw in collect.BRUTEFORCE_QUERIES.items():
            if collect._q(expr_raw, "30m") == expr:
                return values[name]
        return None

    monkeypatch.setattr(collect, "query_scalar", fake_scalar)
    report = await collect.collect_bruteforce_report(
        stage=10000,
        window="30m",
        injected_events=100,
        env_note_data={"host": "test"},
        clock=SystemClock(),
    )
    assert report["verdict"]["vector_le_50_ok"] is False
