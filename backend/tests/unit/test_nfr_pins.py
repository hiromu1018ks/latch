"""ws-7の静的ピン(M4 ws-7)。override・Makefile・pyproject・lifespan wiring。

既定環境(compose.staging.yaml等)は無変更のため・「既定に漏れていないこと」
の無傷ピンも本ファイルが担う(loadgen composeピンと同一作法)。
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_main_lifespan_registers_lag_alert_when_enabled():
    """lifespanの登録コードが在ることの静的ピン(lifespan発火unitは存在しない)。"""
    text = (ROOT / "backend" / "src" / "latch" / "main.py").read_text(encoding="utf-8")
    assert "settings.alert_queue_enabled" in text
    assert "QueueLagAlert(" in text
    # finallyでの停止・クローズ(event_relayと同型の規律)
    assert "lag_alert_stop.set()" in text
    assert "lag_alert_task.cancel()" in text
    assert "lag_alert_redis.aclose()" in text


def test_pyproject_has_nfrtools_entry():
    text = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" in text
    assert 'latch-nfrtools = "latch.nfrtools.__main__:main"' in text


NFR_COMPOSE = yaml.safe_load(
    (ROOT / "compose.staging.nfr.yaml").read_text(encoding="utf-8")
)


def test_nfr_compose_enables_lag_alert_on_both_apis():
    """lag alertの有効化はoverrideのみ(compose.staging.yamlは無傷)。"""
    for svc in ("api1", "api2"):
        assert (
            NFR_COMPOSE["services"][svc]["environment"]["LATCH_ALERT_QUEUE_ENABLED"]
            == "true"
        ), svc


def test_compose_staging_untouched_by_nfr():
    """既定環境にalert envが漏れていないこと+既定envの無傷ピン。"""
    staging = yaml.safe_load(
        (ROOT / "compose.staging.yaml").read_text(encoding="utf-8")
    )
    for svc in ("api1", "api2", "worker1", "worker2"):
        env = staging["services"][svc]["environment"]
        assert "LATCH_ALERT_QUEUE_ENABLED" not in env, svc
        assert env["LATCH_DATABASE_URL"].endswith("@db:5432/latch"), svc
    # nginx・db・redis・pubsubの既定は無変更(ports既存値)
    assert staging["services"]["nginx"]["ports"] == ["127.0.0.1:18000:80"]
    assert staging["services"]["db"]["ports"] == ["127.0.0.1:15432:5432"]


def test_recovery_compose_defines_db2_with_profile_and_loopback_port():
    recovery = yaml.safe_load(
        (ROOT / "compose.staging.recovery.yaml").read_text(encoding="utf-8")
    )
    db2 = recovery["services"]["db2"]
    assert db2["container_name"] == "latch-staging-db2-1"
    assert db2["profiles"] == ["recovery"]  # --profile recovery指定時のみ起動
    assert db2["ports"] == ["127.0.0.1:16543:5432"]
    assert "latch-staging-db2:/var/lib/postgresql/data" in db2["volumes"]
    assert recovery["volumes"] == {"latch-staging-db2": None}
    assert db2["build"] == {"context": "docker/postgres"}


def test_recovery_compose_switches_db_and_speeds_backfill():
    """api/workerのDB向き先差し替え+backfill周期短縮(既存envの上書きのみ)。"""
    recovery = yaml.safe_load(
        (ROOT / "compose.staging.recovery.yaml").read_text(encoding="utf-8")
    )
    for svc in ("api1", "api2", "worker1", "worker2"):
        env = recovery["services"][svc]["environment"]
        assert env["LATCH_DATABASE_URL"] == (
            "postgresql+asyncpg://latch:latch@db2:5432/latch"
        ), svc
        assert env["LATCH_EMBEDDING_BACKFILL_INTERVAL_SEC"] == "5", svc


def test_makefile_has_nfr_targets():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in (
        "staging-nfr-reset:",
        "staging-nfr-test:",
        "staging-probe:",
        "staging-d16:",
        "staging-dump:",
        "staging-db2-up:",
        "staging-restore:",
        "staging-db2-switch:",
        "staging-db2-down:",
    ):
        assert target in mk, target
    phony = mk.split(".PHONY:")[1].split("\n")[0]
    for name in (
        "staging-nfr-reset",
        "staging-nfr-test",
        "staging-probe",
        "staging-d16",
        "staging-dump",
        "staging-db2-up",
        "staging-restore",
        "staging-db2-switch",
        "staging-db2-down",
    ):
        assert name in phony, name
