"""ws-4受け入れ(c): /metrics・grafana 4枚・非ループバック拒否(承認条件(d))。"""

import socket

import httpx
from obs_results import GRAFANA_URL, PROM_URL, record_item


async def test_w4_c1_api_metrics_direct():
    """api1直(18001)/metricsが200・text/plain(nginx経由でなく直scrape)。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("http://127.0.0.1:18001/metrics")
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"].startswith("text/plain")
    assert "latch_http_requests_total" in r.text


async def test_w4_c2_worker_metrics_direct():
    """worker1直(18101)も200(apiと同一レジストリ名・別プロセス値)。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("http://127.0.0.1:18101/metrics")
    assert r.status_code == 200, r.text[:200]
    assert "latch_event_queue_pending" in r.text


async def test_w4_c3_grafana_dashboards_provisioned():
    """grafana APIでダッシュボード4枚が自動ロード済み。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{GRAFANA_URL}/api/search",
            auth=("admin", "admin"),
            params={"query": "", "type": "dash-db"},
        )
        assert r.status_code == 200, r.text[:200]
        titles = {d["title"] for d in r.json()}
    assert len(titles) >= 4, f"ダッシュボードが4枚無い: {titles}"
    record_item(
        item_id="ws4-c3",
        name="grafanaダッシュボード4枚稼働",
        test="test_w4_c3_grafana_dashboards_provisioned",
        verdict="pass",
        observed={"titles": sorted(titles)},
    )


async def test_w4_c4_prometheus_scrapes_all_targets():
    """prometheusが4 targetをup=1でscrapeしている。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{PROM_URL}/api/v1/query", params={"query": "up"})
    assert r.status_code == 200
    result = r.json()["data"]["result"]
    up_targets = {s["metric"].get("job"): s["value"][1] for s in result}
    for job in ("latch-staging-api", "latch-staging-worker", "prometheus"):
        assert up_targets.get(job) == "1", f"{job} is not up: {up_targets}"
    record_item(
        item_id="ws4-c4",
        name="prometheus 4 target up",
        test="test_w4_c4_prometheus_scrapes_all_targets",
        verdict="pass",
        observed=up_targets,
    )


def test_w4_c5_metrics_not_reachable_from_non_loopback():
    """承認条件(d)項: ホスト外(非ループバック)から/metricsへ届かない。

    composeのportsは全て127.0.0.1明示バインドのため、非ループバックIP
    への接続は拒否される。ホストの非ループバックIPは実経路(8.8.8.8への
    UDP接続のsockname)で取得する。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        host_ip = s.getsockname()[0]
    finally:
        s.close()
    assert host_ip != "127.0.0.1"
    for port in (18001, 18002, 18101, 18102, 19090, 13000):
        with socket.socket() as sock:
            sock.settimeout(3.0)
            code = sock.connect_ex((host_ip, port))
        assert code != 0, (
            f"非ループバックIP {host_ip}:{port} に接続できてしまう(127.0.0.1"
            "バインドの承認条件違反)"
        )
    record_item(
        item_id="ws4-c5",
        name="非ループバックから/metricsへ届かない(承認条件d)",
        test="test_w4_c5_metrics_not_reachable_from_non_loopback",
        verdict="pass",
        observed={"host_ip": host_ip},
    )
