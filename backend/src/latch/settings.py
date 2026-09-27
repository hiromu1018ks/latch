"""アプリ設定(design §2.8: コードが消費しない設定は作らない)。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LATCH_")

    app_env: str = "ci"  # ci / staging / prod(10 第1節の環境)
    log_level: str = "INFO"
