"""Settingsのデフォルトと環境変数上書き(design §4.4)。"""

from latch.settings import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("LATCH_APP_ENV", raising=False)
    monkeypatch.delenv("LATCH_LOG_LEVEL", raising=False)
    monkeypatch.delenv("LATCH_CLOCK_MODE", raising=False)
    monkeypatch.delenv("LATCH_AUTH_TESTKEYS_DIR", raising=False)
    s = Settings()
    assert s.app_env == "ci"
    assert s.log_level == "INFO"
    assert s.reeval_runner_interval_sec == 60  # M2 ws-6(design §2.8)
    assert s.reeval_runner_batch_limit == 50
    assert s.sweeper_batch_limit == 50  # M3 ws-2(06 §6・期限切れバッチ1tick上限)
    assert s.reset_retry_sec == 300  # M3 ws-2(04 §5・リセット失敗時の再試行間隔)
    assert s.push_mode == "stub"  # M3 ws-3(design §3.2・realはG3後)
    assert s.push_stub_delay_ms == 0  # M3 ws-3(スタブ遅延注入)
    assert s.clock_mode == "system"  # M4 ws-1(design §2.2・ciはSystemClock既定)
    assert s.auth_testkeys_dir == ""  # M4 ws-1(design §2.4・空=同梱鍵)


def test_settings_clock_mode_and_testkeys_dir_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_CLOCK_MODE", "controlled")
    monkeypatch.setenv("LATCH_AUTH_TESTKEYS_DIR", "/keys")
    s = Settings()
    assert s.clock_mode == "controlled"
    assert s.auth_testkeys_dir == "/keys"


def test_settings_env_override_with_latch_prefix(monkeypatch):
    monkeypatch.setenv("LATCH_APP_ENV", "staging")
    monkeypatch.setenv("LATCH_LOG_LEVEL", "DEBUG")
    s = Settings()
    assert s.app_env == "staging"
    assert s.log_level == "DEBUG"


def test_settings_non_prefixed_env_is_ignored(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("LATCH_APP_ENV", raising=False)
    s = Settings()
    assert s.app_env == "ci"


def test_settings_database_url_default(monkeypatch):
    monkeypatch.delenv("LATCH_DATABASE_URL", raising=False)
    s = Settings()
    assert s.database_url == "postgresql+asyncpg://latch:latch@127.0.0.1:5432/latch"


def test_settings_database_url_env_override(monkeypatch):
    monkeypatch.setenv(
        "LATCH_DATABASE_URL", "postgresql+asyncpg://u:p@db.example.com:5432/latchdb"
    )
    s = Settings()
    assert s.database_url == "postgresql+asyncpg://u:p@db.example.com:5432/latchdb"


def test_obs_settings_defaults(monkeypatch):
    monkeypatch.delenv("LATCH_OBS_WORKER_METRICS_PORT", raising=False)
    monkeypatch.delenv("LATCH_OBS_COLLECT_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("LATCH_KPI_BACKFILL_DAYS", raising=False)
    s = Settings()
    assert s.obs_worker_metrics_port == 0  # M4 ws-4(design §3.1・0=無効・ci無効維持)
    assert s.obs_collect_interval_sec == 60  # M4 ws-4(collector tick)
    assert s.kpi_backfill_days == 4  # M4 ws-4(D-09自己申告3日+マージン)


def test_obs_settings_are_exactly_three_fields(monkeypatch):
    # obs系設定の過剰供給を防ぐ機械検査(auth_8項目ピンと同一作法)
    monkeypatch.delenv("LATCH_OBS_WORKER_METRICS_PORT", raising=False)
    monkeypatch.delenv("LATCH_OBS_COLLECT_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("LATCH_KPI_BACKFILL_DAYS", raising=False)
    obs_fields = {f for f in Settings.model_fields if f.startswith("obs_")}
    kpi_fields = {f for f in Settings.model_fields if f.startswith("kpi_")}
    assert obs_fields == {"obs_worker_metrics_port", "obs_collect_interval_sec"}
    assert kpi_fields == {"kpi_backfill_days"}


def test_obs_settings_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_OBS_WORKER_METRICS_PORT", "9100")
    monkeypatch.setenv("LATCH_OBS_COLLECT_INTERVAL_SEC", "30")
    monkeypatch.setenv("LATCH_KPI_BACKFILL_DAYS", "5")
    s = Settings()
    assert s.obs_worker_metrics_port == 9100
    assert s.obs_collect_interval_sec == 30
    assert s.kpi_backfill_days == 5


def test_alert_settings_defaults(monkeypatch):
    monkeypatch.delenv("LATCH_ALERT_QUEUE_ENABLED", raising=False)
    monkeypatch.delenv("LATCH_ALERT_QUEUE_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("LATCH_ALERT_QUEUE_PENDING", raising=False)
    monkeypatch.delenv("LATCH_ALERT_QUEUE_OLDEST_SECONDS", raising=False)
    monkeypatch.delenv("LATCH_ALERT_QUEUE_ARM_TTL_SEC", raising=False)
    s = Settings()
    assert s.alert_queue_enabled is False  # M4 ws-7(design §2.5案A・ciは無効のまま)
    assert s.alert_queue_interval_sec == 60  # 発報器のtick(collectorと同一周期)
    assert s.alert_queue_pending == 1000  # design §5-5(docs未規定の初期値)
    assert s.alert_queue_oldest_seconds == 300  # design §5-5
    assert s.alert_queue_arm_ttl_sec == 180  # アームキーTTL(§2.3)


def test_alert_settings_are_exactly_five_fields(monkeypatch):
    # alert系設定の過剰供給を防ぐ機械検査(obs系集合ピンと同一作法)
    for key in (
        "LATCH_ALERT_QUEUE_ENABLED",
        "LATCH_ALERT_QUEUE_INTERVAL_SEC",
        "LATCH_ALERT_QUEUE_PENDING",
        "LATCH_ALERT_QUEUE_OLDEST_SECONDS",
        "LATCH_ALERT_QUEUE_ARM_TTL_SEC",
    ):
        monkeypatch.delenv(key, raising=False)
    alert_fields = {f for f in Settings.model_fields if f.startswith("alert_")}
    assert alert_fields == {
        "alert_queue_enabled",
        "alert_queue_interval_sec",
        "alert_queue_pending",
        "alert_queue_oldest_seconds",
        "alert_webhook_url",  # C-01(V1-P5・04 v0.7)
        "alert_webhook_timeout_s",
        "alert_queue_arm_ttl_sec",
    }


def test_alert_settings_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_ALERT_QUEUE_ENABLED", "true")
    monkeypatch.setenv("LATCH_ALERT_QUEUE_INTERVAL_SEC", "30")
    monkeypatch.setenv("LATCH_ALERT_QUEUE_PENDING", "5")
    monkeypatch.setenv("LATCH_ALERT_QUEUE_OLDEST_SECONDS", "10")
    monkeypatch.setenv("LATCH_ALERT_QUEUE_ARM_TTL_SEC", "60")
    s = Settings()
    assert s.alert_queue_enabled is True
    assert s.alert_queue_interval_sec == 30
    assert s.alert_queue_pending == 5
    assert s.alert_queue_oldest_seconds == 10
    assert s.alert_queue_arm_ttl_sec == 60
