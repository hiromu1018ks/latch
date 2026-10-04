"""obs/metrics.pyのメトリクス定義ピン(design §4・許可リストと同型の規律)。

新規メトリクスの追加は「metrics.pyの定義」と「EXPECTED_*定数の更新」の
2箇所必須にする(名前一覧化=許可リストと同型の機械規律・08 §2.4への新規
露出の注意喚起)。値のassertはカスタムCollectorRegistryの独立インスタンス
で行う(design §2.1・prometheus-client公式のテスト作法)。
"""

from datetime import UTC, datetime, timedelta

from prometheus_client import REGISTRY, CollectorRegistry

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.obs.metrics import build_metrics

# Counterは露出時に _total が付く・Histogramは _bucket/_count/_sum・
# Gaugeは素の名前。namespace="latch" が接頭辞になる。
EXPECTED_SAMPLE_NAMES = {
    # HTTP基本メトリクス(design §2.9)
    "latch_http_requests_total",
    "latch_http_request_duration_seconds_bucket",
    "latch_http_request_duration_seconds_count",
    "latch_http_request_duration_seconds_sum",
    # パイプライン群(04 §2)
    "latch_event_queue_pending",
    "latch_event_queue_oldest_pending_seconds",
    "latch_pipeline_candidates",
    "latch_hardfilter_pass_total",
    "latch_hardfilter_input_total",
    "latch_retrieval_vector_items_bucket",
    "latch_retrieval_vector_items_count",
    "latch_retrieval_vector_items_sum",
    "latch_breaker_state",
    # Jev群(04 §2・§7)
    "latch_jev_executions_total",
    "latch_jev_latency_seconds_bucket",
    "latch_jev_latency_seconds_count",
    "latch_jev_latency_seconds_sum",
    "latch_jev_usage_tokens_total",
    # 提案と行動(04 §2)
    "latch_score_bucket",
    "latch_score_count",
    "latch_score_sum",
    # KPI(design §2.3)
    "latch_kpi_daily_numerator",
    "latch_kpi_daily_denominator",
}


def _sample_names(registry: CollectorRegistry) -> set[str]:
    """露出sample名の列挙(prometheus-client 0.26対応)。

    0.26はsampleの遅延生成のため、collect()のfamily名はCounterから
    _totalを除いた名・ラベル付きseriesはlabels()呼び出しまでsampleを
    持たない。そこでfamily名とtypeから露出sample名を決定的に展開する
    (Counter=+_total・Histogram=+_bucket/_count/_sum・Gauge=素の名前。
    _createdはprometheus-clientが付与する技術seriesなので対象外)。
    許可リスト完全一致ピンの意図は不変。
    """
    names = set()
    for family in registry.collect():
        name = family.name
        if family.type == "counter":
            names.add(name + "_total")
        elif family.type == "histogram":
            names.update({name + "_bucket", name + "_count", name + "_sum"})
        else:
            names.add(name)
    return names


def test_metric_sample_names_are_the_approved_list():
    """露出sample名が許可リストと完全一致(名前一覧化の機械規律)。"""
    registry = CollectorRegistry()
    build_metrics(registry)
    assert _sample_names(registry) == EXPECTED_SAMPLE_NAMES


def test_build_metrics_on_fresh_registry_is_isolated():
    """カスタムレジストリのインスタンスは既定REGISTRYと独立(値assert経路)。"""
    registry = CollectorRegistry()
    m = build_metrics(registry)
    m.event_queue_pending.set(7)
    m.jev_executions_total.labels(provider="typesafe_jev").inc(3)
    assert registry.get_sample_value("latch_event_queue_pending") == 7.0
    assert (
        registry.get_sample_value(
            "latch_jev_executions_total", {"provider": "typesafe_jev"}
        )
        == 3.0
    )
    # カスタムへのset(7)が既定REGISTRYへ漏れていない(set前後で単例の値が
    # 変わらない=独立。単例はREGISTRYに登録済みのため is None ではなく
    # 差分で検査する — 単例の値は他テスト(collector等)の累積で変わるため
    # 固定値0は使わない)
    single_before = REGISTRY.get_sample_value("latch_event_queue_pending")
    assert REGISTRY.get_sample_value("latch_event_queue_pending") == single_before


def test_module_singleton_is_registered_once():
    """モジュール単例は既定REGISTRYに存在する(import=1回構築の契約)。"""
    names = _sample_names(REGISTRY)
    assert "latch_http_requests_total" in names
    assert "latch_event_queue_pending" in names


def test_observe_helpers_increment_the_right_series():
    """observeヘルパーがラベル付きseriesを正しく増やす(後続タスクのIF契約)。"""
    from latch.obs import metrics as obs_metrics_module

    # observe_retrieval等はモジュール単例へ作用するため、ここでは単例の
    # 差分で検証する(プロセス内累積はpytest同一プロセス内で増え続ける)
    before_pass = REGISTRY.get_sample_value("latch_hardfilter_pass_total") or 0
    before_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    obs_metrics_module.observe_retrieval(layer1_pass_count=12, vector_items=5)
    after_pass = REGISTRY.get_sample_value("latch_hardfilter_pass_total") or 0
    after_input = REGISTRY.get_sample_value("latch_hardfilter_input_total") or 0
    assert after_pass == before_pass + 12
    assert after_input == before_input + 1

    before_exec = (
        REGISTRY.get_sample_value(
            "latch_jev_executions_total", {"provider": "typesafe_jev"}
        )
        or 0
    )
    obs_metrics_module.observe_jev_execution(
        provider="typesafe_jev",
        latency_s=0.4,
        usage={"input_tokens": 10, "output_tokens": 0},
    )
    after_exec = (
        REGISTRY.get_sample_value(
            "latch_jev_executions_total", {"provider": "typesafe_jev"}
        )
        or 0
    )
    assert after_exec == before_exec + 1
    assert (
        REGISTRY.get_sample_value(
            "latch_jev_usage_tokens_total",
            {"provider": "typesafe_jev", "kind": "input"},
        )
        == 10.0
    )

    before_score = (
        REGISTRY.get_sample_value("latch_score_count", {"shape": "pair"}) or 0
    )
    obs_metrics_module.observe_latch_score(shape="pair", score=0.9)
    after_score = REGISTRY.get_sample_value("latch_score_count", {"shape": "pair"}) or 0
    assert after_score == before_score + 1

    # usage=None・未知shapeは例外を上げない(観測は本体処理を壊さない)
    obs_metrics_module.observe_jev_execution(
        provider="fallback_llm", latency_s=0.1, usage=None
    )
    obs_metrics_module.observe_latch_score(shape="group", score=0.7)


# -- middleware(Task 4)--

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_CLAIMS = AccessTokenClaims(
    auth_provider="google",
    auth_subject="obs-mw-1",
    jti="jti",
    sid="sid",
    iat=_NOW,
    exp=_NOW + timedelta(seconds=3600),
)


async def test_http_metrics_increments_on_health(app, client, fake_clock):
    """/health 1回でcounter+1・histogramに観測が積まれる。"""
    before = (
        REGISTRY.get_sample_value(
            "latch_http_requests_total",
            {"method": "GET", "path": "/health", "status": "200"},
        )
        or 0
    )
    before_count = (
        REGISTRY.get_sample_value(
            "latch_http_request_duration_seconds_count",
            {"method": "GET", "path": "/health"},
        )
        or 0
    )
    resp = await client.get("/health")
    assert resp.status_code == 200
    after = (
        REGISTRY.get_sample_value(
            "latch_http_requests_total",
            {"method": "GET", "path": "/health", "status": "200"},
        )
        or 0
    )
    after_count = (
        REGISTRY.get_sample_value(
            "latch_http_request_duration_seconds_count",
            {"method": "GET", "path": "/health"},
        )
        or 0
    )
    assert after == before + 1
    assert after_count == before_count + 1


async def test_http_metrics_unknown_path_is_unmatched(client):
    """route未マッチ(404)はpathテンプレートがunmatchedになる(クエリ・生pathを
    labelに含まない — 08 §2.4規律)。"""
    resp = await client.get("/no-such-path")
    assert resp.status_code == 404
    samples = []
    for family in REGISTRY.collect():
        for sample in family.samples:
            if (
                sample.name == "latch_http_requests_total"
                and sample.labels.get("path") == "/no-such-path"
            ):
                samples.append(sample)
    assert samples == []  # 生pathはlabelに現れない
    assert (
        REGISTRY.get_sample_value(
            "latch_http_requests_total",
            {"method": "GET", "path": "unmatched", "status": "404"},
        )
        is not None
    )


async def test_http_metrics_labels_are_system_only(app, client):
    """HTTPメトリクスのlabelはmethod・path・statusのみ(user_id不使用)。"""
    for family in REGISTRY.collect():
        if family.name != "latch_http_requests_total":
            continue
        for sample in family.samples:
            if sample.name == "latch_http_requests_total":
                assert set(sample.labels.keys()) <= {"method", "path", "status"}


async def test_http_metrics_protected_route_records_204(app, client):
    """認証保護経路(POST /v1/sessions・Task 5のルーターがまだ無い場合404)でも
    middlewareは経路テンプレートで計上する(Task 10以降は /v1/sessions が
    templateになる)。ここでは存在しない保護的メソッドでunmatched経路のみ確認。"""
    app.dependency_overrides[require_authenticated] = lambda: _CLAIMS
    await client.post("/no-such-protected-path")
    assert (
        REGISTRY.get_sample_value(
            "latch_http_requests_total",
            {"method": "POST", "path": "unmatched", "status": "404"},
        )
        is not None
    )
    app.dependency_overrides.pop(require_authenticated, None)


# -- /metricsマウント(Task 10)--


async def test_metrics_endpoint_serves_registry(app, client):
    """/metricsがtext/plainで既定REGISTRYの内容を返す(ASGIマウント)。"""
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "latch_http_requests_total" in resp.text


async def test_metrics_path_itself_is_not_counted(app, client):
    """/metricsの自己アクセスは計上しない(Review Focus 3・自己増殖防止)。"""
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == "latch_http_requests_total":
                assert sample.labels.get("path") != "/metrics"
