"""HTTP基本メトリクスmiddleware(design §2.9・supervisor承認2026-10-04)。

docs 3群の明示的拡張。ラベルはmethod・pathテンプレート・statusのみ。
user_id・クエリ・body・Intent文言・座標は含めない(08 §2.4と同規律)。
/metricsの自己アクセスは計上しない(Prometheusのscrape周期で自己増殖
するため)。理由: ws-6の性能5目標(10 §4.1)が絶対値合格であり、API側の
p95 histogramが測定基盤として実質前提(単位表でws-6はws-4に「測定基盤
前提」で依存)。

レイテンシ計測はClock経由(C2・arch testのtime.*禁止に従う)。
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from latch.obs.metrics import metrics


class HTTPMetricsMiddleware(BaseHTTPMiddleware):
    """全リクエストをmethod・pathテンプレート・statusで計上する。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path.startswith("/metrics"):
            return await call_next(request)
        method = request.method
        clock = request.app.state.clock
        t0 = clock.now()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        elapsed = (clock.now() - t0).total_seconds()
        metrics.http_requests.labels(
            method=method, path=path, status=str(response.status_code)
        ).inc()
        metrics.http_request_duration.labels(method=method, path=path).observe(elapsed)
        return response
