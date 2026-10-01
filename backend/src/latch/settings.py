"""アプリ設定(design §2.8: コードが消費しない設定は作らない)。"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LATCH_")

    app_env: str = "ci"  # ci / staging / prod(10 第1節の環境)
    log_level: str = "INFO"

    # --- DB接続(ws-1。design §3.1)---
    database_url: str = "postgresql+asyncpg://latch:latch@127.0.0.1:5432/latch"

    # --- LLM Gateway(ws-2。design §3.2)---
    # "stub": 3系統すべてスタブ / "real": Parser系統のみAnthropic実API
    # (Embedding/JevはM2までスタブ継続 — design §2.4)
    llm_mode: str = "stub"
    # T1 Parser契約(2026-09-28・Anthropic Haiku 4.5)のAPI鍵。実値は.env
    # (git管理外)へ書き、make g1-gate(uv run --env-file ../.env)経由で
    # のみプロセスへ渡す。ci環境(compose)へは渡さない。
    # env名はLATCH_ANTHROPIC_API_KEY(.env・Makefileと同一) — env_prefixの
    # 自動写像(LATCH_LLM_ANTHROPIC_API_KEY)を探させないためalias必須
    llm_anthropic_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "LATCH_ANTHROPIC_API_KEY", "llm_anthropic_api_key"
        ),
    )
    # 接続先API URL。SDKは明示api_key指定でも環境変数ANTHROPIC_BASE_URLを自動
    # 採用するため、プロキシ設定混在環境(z.ai等)で鍵が別系統へ送られる事故を
    # 防ぐ(公式APIを明示渡しする。design §3.2のsupervisor承認済み拡張)
    llm_anthropic_base_url: str = Field(
        default="https://api.anthropic.com",
        validation_alias=AliasChoices(
            "LATCH_ANTHROPIC_BASE_URL", "llm_anthropic_base_url"
        ),
    )
    # 10 第1節レイテンシ注入(既定は無効)。p50/p95分布はM4でスタブ内で拡張
    llm_stub_delay_parser_ms: int = 0
    llm_stub_delay_embedding_ms: int = 0
    llm_stub_delay_jev_ms: int = 0

    # --- 認証(ws-3。design §2.5)---
    redis_url: str = "redis://127.0.0.1:6379/0"
    # HS256用secret。空=同梱テスト鍵(ci/staging)。prodで空/テスト鍵は起動拒否
    auth_access_secret: str = ""
    # IdP JWKS URL。空=同梱テストJWKS(ci/staging)。prodは実URL必須
    auth_idp_jwks_url_google: str = ""
    auth_idp_jwks_url_apple: str = ""
    # IdP検証のissuer/audience。既定=テストIdP値。prodは実IdPの値必須
    auth_idp_issuer_google: str = "https://idp.ci.latch.test/google"
    auth_idp_issuer_apple: str = "https://idp.ci.latch.test/apple"
    auth_idp_audience_google: str = "latch-test-app"
    auth_idp_audience_apple: str = "latch-test-app"

    # --- 地物データ(ws-4。design §2.6)---
    geo_area_name: str = "ci-provisional"  # エリア表示名(記録用。T2確定後に差し替え)
    geo_isj_city_codes: str = (
        "46201"  # カンマ区切り。ISJ取り込みの市区町村コードフィルタ
    )
    geo_osm_bbox: str = (
        "130.5420,31.5825,130.5740,31.6095"  # min_lon,min_lat,max_lon,max_lat
    )

    # --- レート制限(M1 ws-4。08 §5.4)---
    rate_limit_api_per_min: int = 60
    rate_limit_create_per_day: int = 20
    rate_limit_update_per_hour: int = 6
    rate_limit_active_intents: int = 5

    # --- イベント駆動(M2 ws-1。design §3.2)---
    # Pub/Sub(04 §3選定)。ci=エミュレータ(composeのpubsubサービス)。
    # pubsub_emulator_host が空なら実GCP(本番)。非空ならSDKの
    # PUBSUB_EMULATOR_HOST 経由でエミュレータへ接続する
    pubsub_project_id: str = "latch-ci"
    pubsub_topic_match_events: str = "match-events"
    pubsub_subscription_match_events: str = "match-events-sub"
    pubsub_emulator_host: str = ""
    pubsub_ack_deadline_sec: int = (
        600  # 最大値(debounce10s+backoff31s+処理が収まる — design §2.3)
    )
    # フォールバックリレー(design §2.2-B): 通常処理はしきい値に到達しない
    # (=通常時の再publishゼロ)。debounce 10秒+処理 < 30秒
    event_fallback_relay_after_sec: int = 30
    event_fallback_poll_sec: int = 5
    event_debounce_window_sec: int = 10  # 06 §9-1(初期値。調整はQueue lag計測で)
    event_retry_max: int = 5  # 06 §9-4(初回+再試行5回。バックオフ1,2,4,8,16秒)

    # --- Embedding Worker(M2 ws-2。design §3.2)---
    # Gemini API鍵(Embedding系統real化・T1 v0.2 §2.4)。実値は.env(git管理外)へ
    # 書き、make embed-smoke(uv run --env-file ../.env)経由でのみプロセスへ渡す
    # (g1-gateと同じ規律)。ci環境(compose)へは渡さない(workerはstubのため)。
    # SDKは環境変数GEMINI_API_KEY/GOOGLE_API_KEYを自動採用するため、aliasで
    # 明示渡し経路のみとする(llm_anthropic_api_keyと同じAliasChoices形式)
    llm_gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LATCH_GEMINI_API_KEY", "llm_gemini_api_key"),
    )
    # TypeSafe Jev API鍵(Layer 4第一候補・M2 ws-5)。gemini鍵と同一規律: 実値は
    # .env(git管理外)へ書き、make jev-smoke(uv run --env-file ../.env)経由での
    # みプロセスへ渡す。ci環境(compose)へは渡さない(workerはstubのため)
    llm_typesafe_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LATCH_TYPESAFE_API_KEY", "llm_typesafe_api_key"),
    )
    # 接続先API URL(公式APIを明示渡し — anthropic_base_urlと同一判断)
    llm_typesafe_base_url: str = Field(
        default="https://api.typesafe.ai",
        validation_alias=AliasChoices(
            "LATCH_TYPESAFE_BASE_URL", "llm_typesafe_base_url"
        ),
    )
    # バックフィル周期タスク(06 D-15・design §2.5)。初期値(計測後に調整 — 06 §9-10)
    embedding_backfill_interval_sec: int = 300
    embedding_backfill_batch_limit: int = 50

    # --- 再評価Runner(M2 ws-6・06 §9・design §2.8)---
    # catch-upスキャン・30分Bucket再評価の周期と1周期あたりの投入上限
    # (60秒はexpiry_sweeperと同一周期・06 §9。初期値。計測後に調整)
    reeval_runner_interval_sec: int = 60
    reeval_runner_batch_limit: int = 50

    # --- 期限切れバッチ・リセットジョブ(M3 ws-2・06 §6・04 §5)---
    # expiry_sweeper(latches/Intent期限切れ・completed遷移)の1tickあたり処理
    # 上限。周期はreeval_runner_interval_secと同一スケジューラ(60秒・06 §6)
    sweeper_batch_limit: int = 50
    # リセットジョブ失敗時の再試行待機秒(04 §5「翌0時まで放置しない」)
    reset_retry_sec: int = 300

    # --- プッシュ通知(M3 ws-3・design §3.2)---
    # "stub": ドライラン(送信内容を構造化ログlatch.push.sendへ記録のみ)。
    # "real"(実FCM・FirebasePushSender)はG3後。未実装値の指定は
    # build_push_senderがValueError(静かにスタブへ落ちない — llm_mode規律)
    push_mode: str = "stub"
    # スタブの遅延注入ms(10 第1節レイテンシ注入・llm_stub_delay_*と同型)
    push_stub_delay_ms: int = 0
