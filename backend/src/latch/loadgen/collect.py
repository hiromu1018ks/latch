"""負荷試験・総当たりの証拠採取(M4 ws-6・10 §5形式)。

Prometheus照合(staging_tests_obs/obs_results.pyと同型の/api/v1/query)・
histogram_quantileによる分布採取・総当たりの機械判定・証拠YAML出力。
絶対値5目標の合否判定はG4の人間側(design §2.7)であり・本モジュールは
測定値と機械判定(Vector件数≤50)を記録する。実行時刻はClock経由(C2)。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import yaml

from latch.core.clock import Clock

PROM_URL = "http://127.0.0.1:19090"
RESULTS_DIR = Path("docs/testassets/results")

# perf区間のPromQL(§2.5の表・jobはload側のみ)。{WINDOW}は実行時に差し替える。
# label値はprometheus-clientの露出形式(reprの浮動小数表記・例 le="50.0")
PERF_QUERIES = {
    # 目標① Intent保存 p95 500ms(10 §4.1)
    "save_p95_s": (
        "histogram_quantile(0.95, sum by (le) (rate("
        'latch_http_request_duration_seconds_bucket{job="latch-load-api",'
        'method="POST",path="/v1/intents"}[{WINDOW}])))'
    ),
    "save_p99_s": (
        "histogram_quantile(0.99, sum by (le) (rate("
        'latch_http_request_duration_seconds_bucket{job="latch-load-api",'
        'method="POST",path="/v1/intents"}[{WINDOW}])))'
    ),
    # 目標② Parser構造化プレビュー p95 3秒・p99 8秒(相A=parse独立相で採取)
    "parse_p95_s": (
        "histogram_quantile(0.95, sum by (le) (rate("
        'latch_http_request_duration_seconds_bucket{job="latch-load-api",'
        'method="POST",path="/v1/intents/parse"}[{WINDOW}])))'
    ),
    "parse_p99_s": (
        "histogram_quantile(0.99, sum by (le) (rate("
        'latch_http_request_duration_seconds_bucket{job="latch-load-api",'
        'method="POST",path="/v1/intents/parse"}[{WINDOW}])))'
    ),
    # 目標③ Candidate Retrieval p95 1秒(層別=Retrieval区間)
    "retrieval_p95_s": (
        "histogram_quantile(0.95, sum by (le) (rate("
        'latch_pipeline_layer_seconds_bucket{job="latch-load-worker",'
        'layer="retrieval"}[{WINDOW}])))'
    ),
    # 層別予算の残り(Embedding 2秒・L5+通知 2秒)とJev(5秒=既存ヒストグラム)
    "embedding_p95_s": (
        "histogram_quantile(0.95, sum by (le) (rate("
        'latch_pipeline_layer_seconds_bucket{job="latch-load-worker",'
        'layer="embedding"}[{WINDOW}])))'
    ),
    "proposal_p95_s": (
        "histogram_quantile(0.95, sum by (le) (rate("
        'latch_pipeline_layer_seconds_bucket{job="latch-load-worker",'
        'layer="proposal"}[{WINDOW}])))'
    ),
    "jev_p95_s": (
        "histogram_quantile(0.95, sum by (le) (rate("
        'latch_jev_latency_seconds_bucket{job="latch-load-worker"}[{WINDOW}])))'
    ),
    # 目標④ 初期判定E2E p95 10秒(route=create/activateで経路別・読み替え1)
    "e2e_p95_create_s": (
        "histogram_quantile(0.95, sum by (le) (rate("
        'latch_initial_e2e_seconds_bucket{job="latch-load-worker",'
        'route="create"}[{WINDOW}])))'
    ),
    "e2e_p95_activate_s": (
        "histogram_quantile(0.95, sum by (le) (rate("
        'latch_initial_e2e_seconds_bucket{job="latch-load-worker",'
        'route="activate"}[{WINDOW}])))'
    ),
    # 目標⑤ 通知は判定後5秒以内
    "notify_p95_s": (
        "histogram_quantile(0.95, sum by (le) (rate("
        'latch_notification_latency_seconds_bucket{job="latch-load-worker"}'
        "[{WINDOW}])))"
    ),
    # 収束観察(処理待ちの滞留)
    "queue_pending_max": (
        'max_over_time(latch_event_queue_pending{job="latch-load-worker"}[{WINDOW}])'
    ),
}

# 総当たり段階のPromQL(10 §4.2・観測=Vector件数・判定レイテンシ・Jev回数)
BRUTEFORCE_QUERIES = {
    # Vector件数≤50の機械判定: le="50.0"の累積=総累積なら50超えゼロ
    "vector_items_le50_rate": (
        "sum by (le) (increase(latch_retrieval_vector_items_bucket"
        '{job="latch-load-worker",le="50.0"}[{WINDOW}]))'
    ),
    "vector_items_inf_rate": (
        "sum by (le) (increase(latch_retrieval_vector_items_bucket"
        '{job="latch-load-worker",le="+Inf"}[{WINDOW}]))'
    ),
    "retrieval_p95_s": (
        "histogram_quantile(0.95, sum by (le) (rate("
        'latch_pipeline_layer_seconds_bucket{job="latch-load-worker",'
        'layer="retrieval"}[{WINDOW}])))'
    ),
    # Layer 1通過率(通過/入力・段階比較表の素材)
    "hardfilter_pass_rate": (
        'sum(increase(latch_hardfilter_pass_total{job="latch-load-worker"}'
        "[{WINDOW}])) / clamp_min(sum(increase("
        'latch_hardfilter_input_total{job="latch-load-worker"}[{WINDOW}])), 1)'
    ),
    # Jev実行回数の増分(1イベントあたり値は報告書がinjected_eventsで割る)
    "jev_executions_total_increase": (
        'sum(increase(latch_jev_executions_total{job="latch-load-worker"}[{WINDOW}]))'
    ),
}


def _q(expr: str, window: str) -> str:
    return expr.replace("{WINDOW}", window)


async def prom_query(expr: str) -> list[dict]:
    """Prometheusの即時クエリ(結果seriesのlist・obs_results.pyと同型)。"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{PROM_URL}/api/v1/query", params={"query": expr})
    if r.status_code != 200:
        raise RuntimeError(f"prometheus query failed: {r.status_code} {r.text[:200]}")
    return r.json()["data"]["result"]


async def query_scalar(expr: str) -> float | None:
    """即時クエリの単値(結果が空ならNone=観測窓に標本なし)。"""
    result = await prom_query(expr)
    if not result:
        return None
    return float(result[0]["value"][1])


def env_note() -> dict:
    """実施環境の記録(10 §5の「環境」欄・ローカルdockerの申告込み)。"""
    import platform

    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "note": (
            "ローカルdocker(latch-loadプロジェクト・ci一時停止運用)。"
            "絶対値の解釈はG4の人間側が本記録込みで判定(design §2.7)"
        ),
    }


async def collect_perf_report(
    *,
    window: str,
    stats_summary: dict,
    env_note_data: dict,
    clock: Clock,
) -> dict:
    """perf区間の証拠(10 §5形式)。判定はrecorded・G4の人間側(design §2.7)。"""
    measurements = {}
    for name, expr in PERF_QUERIES.items():
        measurements[name] = await query_scalar(_q(expr, window))
    return {
        "unit": "M4-ws-6",
        "test_id": "perf-load",
        "executed_at": clock.now().isoformat(),
        "environment": env_note_data,
        "input_conditions": {
            "load_profile": (
                "相A parse 50rps x60s → 相B save 50rps x60s"
                "(active主経路+draft→active化) → 相C burst 100件/1s → 収束待ち"
            ),
            "window": window,
            "parse_phase_note": (
                "目標②はparse独立相で採取(負荷条件は相B+相Cで字義どおり)"
            ),
            "injection_stats": stats_summary,
        },
        "verdict": "recorded (絶対値5目標の判定はG4の人間側・design §2.7)",
        "measurements": measurements,
    }


async def collect_bruteforce_report(
    *,
    stage: int,
    window: str,
    injected_events: int,
    env_note_data: dict,
    clock: Clock,
) -> dict:
    """総当たり段階の証拠。Vector件数≤50は機械判定(10 §4.2)。

    機械判定: le="50.0"の累積が総累積(+Inf)と一致(±丸め許容)なら
    Vector件数50超えの観測はゼロ。段階比較・増幅率は報告書(Task 10)が
    複数段階の証拠から組み立てる。
    """
    measurements = {}
    for name, expr in BRUTEFORCE_QUERIES.items():
        measurements[name] = await query_scalar(_q(expr, window))
    le50 = measurements["vector_items_le50_rate"]
    total = measurements["vector_items_inf_rate"]
    vector_ok: bool | None = None
    if le50 is not None and total is not None and total > 0:
        vector_ok = le50 >= total - 0.5
    return {
        "unit": "M4-ws-6",
        "test_id": f"bruteforce-stage-{stage}",
        "executed_at": clock.now().isoformat(),
        "environment": env_note_data,
        "input_conditions": {
            "stage_active_intents": stage,
            "injected_events": injected_events,
            "latency_injection": "none",
            "window": window,
        },
        "verdict": {
            "vector_le_50_ok": vector_ok,
            "note": (
                "正式判定はG4の人間側(段階比較表+増幅率参考値・design §2.7)。"
                "量的合格線の新設は10 §6のdocs改版が前提"
            ),
        },
        "measurements": measurements,
    }


def write_evidence(
    report: dict, *, prefix: str, clock: Clock, results_dir: Path = RESULTS_DIR
) -> Path:
    """証拠YAML出力(10 §5・タイムスタンプ付き・docs/testassets/results/)。

    ファイル名の一意化のためのタイムスタンプもClock経由(C2・src/latch配下の
    実時間直接参照禁止規律に従う)。
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = clock.now().strftime("%Y%m%d-%H%M%S")
    path = results_dir / f"{prefix}-{ts}.yaml"
    path.write_text(
        yaml.safe_dump(report, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path
