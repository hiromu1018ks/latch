"""アプリ設定(design §2.8: コードが消費しない設定は作らない)。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LATCH_")

    app_env: str = "ci"  # ci / staging / prod(10 第1節の環境)
    log_level: str = "INFO"

    # --- DB接続(ws-1。design §3.1)---
    database_url: str = "postgresql+asyncpg://latch:latch@127.0.0.1:5432/latch"

    # --- LLM Gateway(ws-2。design §3.2)---
    llm_mode: str = "stub"  # T1確定後に "real" を追加(M0ではstubのみ)
    # 10 第1節レイテンシ注入(既定は無効)。p50/p95分布はM4でスタブ内で拡張
    llm_stub_delay_parser_ms: int = 0
    llm_stub_delay_embedding_ms: int = 0
    llm_stub_delay_jev_ms: int = 0

    # --- 地物データ(ws-4。design §2.6)---
    geo_area_name: str = "ci-provisional"  # エリア表示名(記録用。T2確定後に差し替え)
    geo_isj_city_codes: str = (
        "46201"  # カンマ区切り。ISJ取り込みの市区町村コードフィルタ
    )
    geo_osm_bbox: str = (
        "130.5420,31.5825,130.5740,31.6095"  # min_lon,min_lat,max_lon,max_lat
    )
