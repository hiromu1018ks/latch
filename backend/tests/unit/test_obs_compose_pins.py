"""compose追記の静的ピン(M4 ws-4・承認事項4・YAMLアンカー構造維持)。

pyyaml(既存依存)でパースし・127.0.0.1明示バインドとアンカー構造を検査
する。dockerはこの試験では起動しない(実起動の確認はTask 13)。
"""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]


def _load_compose(name: str) -> dict:
    return yaml.safe_load((REPO / name).read_text(encoding="utf-8"))


def _worker_ports(compose: dict, service: str) -> list[str]:
    return [p for p in compose["services"][service].get("ports", [])]


def test_staging_worker_metrics_env_in_worker_common():
    compose = _load_compose("compose.staging.yaml")
    env = compose["services"]["worker1"]["environment"]
    assert env["LATCH_OBS_WORKER_METRICS_PORT"] == "9100"


def test_staging_worker_ports_are_loopback_bound():
    compose = _load_compose("compose.staging.yaml")
    for service, host_port in (("worker1", 18101), ("worker2", 18102)):
        ports = _worker_ports(compose, service)
        assert f"127.0.0.1:{host_port}:9100" in ports


def test_load_worker_ports_are_loopback_bound():
    compose = _load_compose("compose.load.yaml")
    for service, host_port in (("worker1", 28101), ("worker2", 28102)):
        ports = _worker_ports(compose, service)
        assert f"127.0.0.1:{host_port}:9100" in ports


def test_dashboard_compose_binds_loopback_only():
    compose = _load_compose("compose.dashboard.yaml")
    assert compose["name"] == "latch-dashboard"
    prom_ports = compose["services"]["prometheus"]["ports"]
    graf_ports = compose["services"]["grafana"]["ports"]
    assert "127.0.0.1:19090:9090" in prom_ports
    assert "127.0.0.1:13000:3000" in graf_ports


def test_dashboard_prometheus_has_host_gateway():
    compose = _load_compose("compose.dashboard.yaml")
    extra = compose["services"]["prometheus"]["extra_hosts"]
    assert "host.docker.internal:host-gateway" in extra


def test_prometheus_scrapes_staging_targets():
    raw = (REPO / "docker/prometheus/prometheus.yml").read_text(encoding="utf-8")
    config = yaml.safe_load(raw)
    targets = []
    for job in config["scrape_configs"]:
        for group in job.get("static_configs", []):
            targets.extend(group["targets"])
    assert {
        "host.docker.internal:18001",
        "host.docker.internal:18002",
        "host.docker.internal:18101",
        "host.docker.internal:18102",
    } <= set(targets)
    # 既存ポート番号との衝突がない(18000=nginx・5432/6379/8085等は未使用)
    assert "host.docker.internal:18000" not in targets


def test_makefile_has_dashboard_and_obs_test_targets():
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    for target in (
        "dashboard-up:",
        "dashboard-down:",
        "dashboard-ps:",
        "dashboard-logs:",
        "staging-obs-test:",
    ):
        assert target in makefile
    phony = makefile.splitlines()[0]
    for name in (
        "dashboard-up",
        "dashboard-down",
        "dashboard-ps",
        "dashboard-logs",
        "staging-obs-test",
    ):
        assert name in phony
