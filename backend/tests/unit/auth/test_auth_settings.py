"""認証設定8項目の既定値と環境変数上書き(design §2.5-2)。

既存tests/unit/test_settings.pyとは別ファイル(並走ws-4との共通ファイル衝突回避。
ws-2設計§3.3と同一判断)。
"""

from latch.auth.testkeys import TEST_ACCESS_SECRET, TEST_AUDIENCE, TEST_ISSUER_PREFIX
from latch.settings import Settings

AUTH_ENV_VARS = (
    "LATCH_REDIS_URL",
    "LATCH_AUTH_ACCESS_SECRET",
    "LATCH_AUTH_IDP_JWKS_URL_GOOGLE",
    "LATCH_AUTH_IDP_JWKS_URL_APPLE",
    "LATCH_AUTH_IDP_ISSUER_GOOGLE",
    "LATCH_AUTH_IDP_ISSUER_APPLE",
    "LATCH_AUTH_IDP_AUDIENCE_GOOGLE",
    "LATCH_AUTH_IDP_AUDIENCE_APPLE",
)


def _clean_settings(monkeypatch, **overrides) -> Settings:
    for var in AUTH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(**overrides)


def test_auth_settings_defaults(monkeypatch):
    s = _clean_settings(monkeypatch)
    assert s.redis_url == "redis://127.0.0.1:6379/0"
    assert s.auth_access_secret == ""  # 空=同梱テスト鍵(prod拒否対象)
    assert s.auth_idp_jwks_url_google == ""
    assert s.auth_idp_jwks_url_apple == ""
    assert s.auth_idp_issuer_google == f"{TEST_ISSUER_PREFIX}google"
    assert s.auth_idp_issuer_apple == f"{TEST_ISSUER_PREFIX}apple"
    assert s.auth_idp_audience_google == TEST_AUDIENCE
    assert s.auth_idp_audience_apple == TEST_AUDIENCE


def test_auth_settings_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("LATCH_AUTH_ACCESS_SECRET", "prod-secret-0123456789abcdef012345")
    monkeypatch.setenv("LATCH_AUTH_IDP_JWKS_URL_GOOGLE", "https://example.com/jwks")
    for var in AUTH_ENV_VARS:
        if var not in (
            "LATCH_REDIS_URL",
            "LATCH_AUTH_ACCESS_SECRET",
            "LATCH_AUTH_IDP_JWKS_URL_GOOGLE",
        ):
            monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.redis_url == "redis://redis:6379/0"
    assert s.auth_access_secret == "prod-secret-0123456789abcdef012345"
    assert s.auth_idp_jwks_url_google == "https://example.com/jwks"


def test_auth_settings_are_exactly_eight_fields(monkeypatch):
    # 設定の過剰供給を防ぐ機械検査(design §2.5-2の一覧どおり)
    _clean_settings(monkeypatch)
    auth_fields = {f for f in Settings.model_fields if f.startswith("auth_")}
    redis_fields = {f for f in Settings.model_fields if f.startswith("redis_")}
    assert auth_fields == {
        "auth_access_secret",
        "auth_idp_jwks_url_google",
        "auth_idp_jwks_url_apple",
        "auth_idp_issuer_google",
        "auth_idp_issuer_apple",
        "auth_idp_audience_google",
        "auth_idp_audience_apple",
    }
    assert redis_fields == {"redis_url"}


def test_defaults_never_equal_test_secret(monkeypatch):
    # 既定(空)がTEST_ACCESS_SECRETそのものにならない(空文字での判別を維持)
    s = _clean_settings(monkeypatch)
    assert s.auth_access_secret != TEST_ACCESS_SECRET
