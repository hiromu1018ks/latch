"""Settingsのデフォルトと環境変数上書き(design §4.4)。"""

from latch.settings import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("LATCH_APP_ENV", raising=False)
    monkeypatch.delenv("LATCH_LOG_LEVEL", raising=False)
    s = Settings()
    assert s.app_env == "ci"
    assert s.log_level == "INFO"


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
