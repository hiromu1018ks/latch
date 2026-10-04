"""全メトリクスの単一真実(design §3.1・04 §2の3群+アクセス系+HTTP)。

namespace="latch"固定。08 §2.4規律: labelはmethod・pathテンプレート・
status・provider・metric名等のシステム属性のみ。user_id・クエリ・body・
Intent原文・座標を含めない。本番SDK移行時(Cloud Monitoring+OTLP・04 §3)
の差し替え面は本モジュールのみ(design §2.1案A・supervisor承認)。

テストはbuild_metrics(CollectorRegistry())で独立インスタンスを作り値を
assertする。本番はモジュール単例metrics(既定REGISTRY)をapi・workerで
共用する(uvicorn/workerプロセスごとに独立したカウンタを持つ=pull型の
正しい使い方・Prometheus側がtarget別にscrapeしてPromQLで集約する)。
"""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

# D-01通知閾値0.60周辺の観察(A/B・Phase 3前提の器 — design §2.8)
SCORE_BUCKETS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
# Vector Retrieval件数(K_v=50上限の分布 — 02#5の検証素材)
VECTOR_BUCKETS = (1, 5, 10, 20, 30, 40, 50)
# HTTPのbucket(M4 ws-6・§2.3の表)。prometheus-client既定DEFAULT_BUCKETSに
# 3.0(目標②のp95 3秒)と8.0(目標②のp99 8秒)を追加した列 — 既存値は1つも
# 変えず・明示列へ切り替える(bucket追加は既存クエリに破壊なし)
HTTP_BUCKETS = (
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    3.0,
    5.0,
    7.5,
    8.0,
    10.0,
)
# 層別・E2Eの共通bucket(M4 ws-6)。10 §4.1注入値(p50 1秒/2秒・p95 2.5秒/5秒)と
# 予算境界(Embedding 2秒・Retrieval 1秒・L5+通知 2秒)とE2E目標10秒を含む
LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0)
# 通知区間(提案確定→push記録)のbucket。StubPushSender構成ではごく小さい。
# 構造上の大きさを観察し実FCM時の上限を推定できる解像度・目標⑤の5秒境界込み
NOTIFY_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0)


@dataclass
class Metrics:
    """metrics.pyで定義する全メトリクス(属性名=コードが消費するIF)。"""

    http_requests: Counter
    http_request_duration: Histogram
    event_queue_pending: Gauge
    event_queue_oldest_pending_seconds: Gauge
    pipeline_candidates: Gauge
    hardfilter_pass_total: Counter
    hardfilter_input_total: Counter
    retrieval_vector_items: Histogram
    breaker_state: Gauge
    jev_executions_total: Counter
    jev_latency_seconds: Histogram
    jev_usage_tokens_total: Counter
    score: Histogram
    kpi_daily_numerator: Gauge
    kpi_daily_denominator: Gauge
    pipeline_layer_seconds: Histogram
    latch_initial_e2e_seconds: Histogram
    notification_latency_seconds: Histogram


def build_metrics(registry: CollectorRegistry | None = None) -> Metrics:
    """registry=Noneは既定REGISTRYへ登録(本番1本)。テストは専用レジストリ。

    注意: prometheus-client 0.26では registry=None の明示渡しは「未登録」を
    意味する(公式ドキュメント)。そのため既定REGISTRYへの登録は本関数内で
    REGISTRYを明示的に渡して行う(モジュール単例の契約 — import=1回構築で
    api・worker共用。テストは専用CollectorRegistryを渡し独立する)。
    """
    if registry is None:
        registry = REGISTRY
    return Metrics(
        http_requests=Counter(
            "http_requests_total",
            "Total HTTP requests by method, path template and status",
            labelnames=("method", "path", "status"),
            namespace="latch",
            registry=registry,
        ),
        http_request_duration=Histogram(
            "http_request_duration_seconds",
            "HTTP request latency by method and path template",
            labelnames=("method", "path"),
            namespace="latch",
            buckets=HTTP_BUCKETS,
            registry=registry,
        ),
        event_queue_pending=Gauge(
            "event_queue_pending",
            "match_events rows in status=pending (Event Queue lag量)",
            namespace="latch",
            registry=registry,
        ),
        event_queue_oldest_pending_seconds=Gauge(
            "event_queue_oldest_pending_seconds",
            "Age in seconds of the oldest pending match_event",
            namespace="latch",
            registry=registry,
        ),
        pipeline_candidates=Gauge(
            "pipeline_candidates",
            "match_candidates total rows (生成済み候補ペア数)",
            namespace="latch",
            registry=registry,
        ),
        hardfilter_pass_total=Counter(
            "hardfilter_pass_total",
            "Layer 1 pass count before LIMIT (accumulated)",
            namespace="latch",
            registry=registry,
        ),
        hardfilter_input_total=Counter(
            "hardfilter_input_total",
            "Retrieval runs (each run counts 1 input)",
            namespace="latch",
            registry=registry,
        ),
        retrieval_vector_items=Histogram(
            "retrieval_vector_items",
            "Vector retrieval item count per run (LIMIT後)",
            buckets=VECTOR_BUCKETS,
            namespace="latch",
            registry=registry,
        ),
        breaker_state=Gauge(
            "breaker_state",
            "CircuitBreaker state: closed=0 open=1 half_open=2",
            namespace="latch",
            registry=registry,
        ),
        jev_executions_total=Counter(
            "jev_executions_total",
            "Jev executions by provider (実API呼び出しをすべて計上)",
            labelnames=("provider",),
            namespace="latch",
            registry=registry,
        ),
        jev_latency_seconds=Histogram(
            "jev_latency_seconds",
            "Jev call latency by provider",
            labelnames=("provider",),
            namespace="latch",
            registry=registry,
        ),
        jev_usage_tokens_total=Counter(
            "jev_usage_tokens_total",
            "Jev token usage by provider and kind (input/output)",
            labelnames=("provider", "kind"),
            namespace="latch",
            registry=registry,
        ),
        score=Histogram(
            "score",
            "LATCH score at proposal (pair=latch_score, group=aggregate)",
            labelnames=("shape",),
            namespace="latch",
            buckets=SCORE_BUCKETS,
            registry=registry,
        ),
        kpi_daily_numerator=Gauge(
            "kpi_daily_numerator",
            "kpi_daily numerator by metric_key (latest row)",
            labelnames=("metric",),
            namespace="latch",
            registry=registry,
        ),
        kpi_daily_denominator=Gauge(
            "kpi_daily_denominator",
            "kpi_daily denominator by metric_key (latest row)",
            labelnames=("metric",),
            namespace="latch",
            registry=registry,
        ),
        pipeline_layer_seconds=Histogram(
            "pipeline_layer_seconds",
            "Layer latency (layer=embedding/retrieval/proposal; jev is separate)",
            labelnames=("layer",),
            namespace="latch",
            buckets=LATENCY_BUCKETS,
            registry=registry,
        ),
        # 定義名はinitial_e2e_seconds・露出名はlatch_initial_e2e_seconds_*
        # (design §2.3の「latch_initial_e2e_seconds」は露出名の表記)
        latch_initial_e2e_seconds=Histogram(
            "initial_e2e_seconds",
            "Initial LATCH decision e2e by injection route (create/activate)",
            labelnames=("route",),
            namespace="latch",
            buckets=LATENCY_BUCKETS,
            registry=registry,
        ),
        notification_latency_seconds=Histogram(
            "notification_latency_seconds",
            "Proposal decision to push send record",
            namespace="latch",
            buckets=NOTIFY_BUCKETS,
            registry=registry,
        ),
    )


# モジュール単例(本番)。import時に1回のみ構築する(複数回はduplicate
# registration例外になるため、本モジュールは再importで再構築しない)
metrics = build_metrics()


def observe_retrieval(*, layer1_pass_count: int, vector_items: int) -> None:
    """Layer 2検索1回分の観測(worker側から呼ぶ・design §2.6)。"""
    metrics.hardfilter_pass_total.inc(layer1_pass_count)
    metrics.hardfilter_input_total.inc(1)
    metrics.retrieval_vector_items.observe(vector_items)


def observe_jev_execution(
    *, provider: str, latency_s: float, usage: dict | None
) -> None:
    """Jev実行1回分の観測(jev.pyのjudge_pair後・design §2.7)。

    usageはenvelopeのusage辞書(input_tokens/output_tokensキー・stub定数と
    同一)。None・キー欠落は0扱い(観測は本体処理を壊さない)。
    """
    metrics.jev_executions_total.labels(provider=provider).inc()
    metrics.jev_latency_seconds.labels(provider=provider).observe(latency_s)
    usage = usage or {}
    metrics.jev_usage_tokens_total.labels(provider=provider, kind="input").inc(
        int(usage.get("input_tokens") or 0)
    )
    metrics.jev_usage_tokens_total.labels(provider=provider, kind="output").inc(
        int(usage.get("output_tokens") or 0)
    )


def observe_latch_score(*, shape: str, score: float) -> None:
    """提案確定時のscore観測(latch_engine=pair・group_engine=group)。"""
    metrics.score.labels(shape=shape).observe(float(score))


def observe_pipeline_layer(*, layer: str, elapsed_s: float) -> None:
    """層別区間1回分の観測(M4 ws-6・layer=embedding/retrieval/proposal)。

    層別予算(Embedding ≤2秒/Layer 1〜3 ≤1秒/L5+通知 ≤2秒・12 §7)の
    どの区間が超えたか特定する器。Jevは既存jev_latency_secondsが観測済み。
    """
    metrics.pipeline_layer_seconds.labels(layer=layer).observe(float(elapsed_s))


def observe_initial_e2e(*, route: str, elapsed_s: float) -> None:
    """初期判定E2Eの観測(M4 ws-6・route=create/activate)。

    初回投入Event(created種)チェーンかつ初回提案のみを観測する
    (呼び出し側=latch_engineの絞り込み条件・design §2.3)。
    """
    metrics.latch_initial_e2e_seconds.labels(route=route).observe(float(elapsed_s))


def observe_notification_latency(*, elapsed_s: float) -> None:
    """通知区間(提案確定→push送信記録)の観測(M4 ws-6)。"""
    metrics.notification_latency_seconds.observe(float(elapsed_s))
