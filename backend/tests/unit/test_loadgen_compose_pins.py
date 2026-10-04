"""load環境接続の静的ピン(M4 ws-6)。

compose.load.yamlのstub遅延既定(10 §4.1値・${VAR:-既定}形式)・load側
別名aliases(prometheusの両ネットワーク参加時のDocker DNSあいまい解決の
回避・読み替え6)・dashboardのloadネットワーク参加・prometheus.ymlの
load job・Makefileターゲット。
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _load_compose() -> dict:
    return yaml.safe_load((ROOT / "compose.load.yaml").read_text(encoding="utf-8"))


def test_load_compose_defaults_to_10_4_1_latency_targets():
    env = _load_compose()["x-backend-env"]
    assert env["LATCH_LLM_STUB_LATENCY_PARSER_P50_MS"] == (
        "${LATCH_LLM_STUB_LATENCY_PARSER_P50_MS:-1000}"
    )
    assert env["LATCH_LLM_STUB_LATENCY_PARSER_P95_MS"] == (
        "${LATCH_LLM_STUB_LATENCY_PARSER_P95_MS:-2500}"
    )
    assert env["LATCH_LLM_STUB_LATENCY_JEV_P50_MS"] == (
        "${LATCH_LLM_STUB_LATENCY_JEV_P50_MS:-2000}"
    )
    assert env["LATCH_LLM_STUB_LATENCY_JEV_P95_MS"] == (
        "${LATCH_LLM_STUB_LATENCY_JEV_P95_MS:-5000}"
    )


def test_load_compose_has_no_fixed_ms_env():
    """既存固定ms env(llm_stub_delay_*)はload composeへ設定しない(0維持)。"""
    for key in _load_compose()["x-backend-env"]:
        assert not key.startswith("LATCH_LLM_STUB_DELAY_"), key


def test_load_services_have_aliased_names():
    """api1/api2/worker1/worker2へload-別名(prometheus target用)。"""
    compose = _load_compose()
    for svc, alias in (
        ("api1", "load-api1"),
        ("api2", "load-api2"),
        ("worker1", "load-worker1"),
        ("worker2", "load-worker2"),
    ):
        networks = compose["services"][svc]["networks"]
        assert networks["default"]["aliases"] == [alias], svc


def test_ports_stay_loopback_bound():
    """load composeのportsは既存値のまま(127.0.0.1明示バインド・承認条件)。"""
    compose = _load_compose()
    assert compose["services"]["api1"]["ports"] == ["127.0.0.1:28001:8000"]
    assert compose["services"]["worker1"]["ports"] == ["127.0.0.1:28101:9100"]


def test_dashboard_joins_load_network():
    compose = yaml.safe_load(
        (ROOT / "compose.dashboard.yaml").read_text(encoding="utf-8")
    )
    assert compose["networks"]["load-obs"] == {
        "external": True,
        "name": "latch-load_default",
    }
    assert "load-obs" in compose["services"]["prometheus"]["networks"]
    # 既存staging参加の無傷
    assert "staging-obs" in compose["services"]["prometheus"]["networks"]


def test_prometheus_targets_aliased_load_containers():
    text = (ROOT / "docker" / "prometheus" / "prometheus.yml").read_text(
        encoding="utf-8"
    )
    assert "job_name: latch-load-api" in text
    assert "job_name: latch-load-worker" in text
    assert "load-api1:8000" in text
    assert "load-api2:8000" in text
    assert "load-worker1:9100" in text
    assert "load-worker2:9100" in text
    # 既存staging jobの無傷
    assert "job_name: latch-staging-api" in text
    assert "job_name: latch-staging-worker" in text


def test_makefile_has_load_targets():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in (
        "loadusers:",
        "loadperf:",
        "loadbf:",
        "loadmass:",
        "load-stub-flat:",
        "load-migrate:",
        "load-geo-import:",
    ):
        assert target in mk, target
    phony = mk.split(".PHONY:")[1].split("\n")[0]
    for name in (
        "loadusers",
        "loadperf",
        "loadbf",
        "loadmass",
        "load-stub-flat",
        "load-migrate",
        "load-geo-import",
    ):
        assert name in phony, name
