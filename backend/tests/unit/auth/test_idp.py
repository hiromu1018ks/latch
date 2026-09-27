"""テスト用IdP鍵ペアと例外階層(design §2.5・§3.1)。

IdPVerifier本体の試験はTask 4でこのファイルへ追記する。
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from latch.auth.errors import (
    DependencyUnavailableError,
    InvalidIdpTokenError,
    InvalidRefreshTokenError,
    UnauthenticatedError,
)
from latch.auth.idp import IdPVerifier, IdPVerifyConfig
from latch.auth.testkeys import (
    DEFAULT_KID,
    TEST_ACCESS_SECRET,
    TEST_AUDIENCE,
    TEST_ISSUER_PREFIX,
    load_jwks,
    load_private_key,
)
from latch.core.clock import FakeClock


def _b64u_int(value: str) -> int:
    padding = "=" * (-len(value) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(value + padding), "big")


def test_error_statuses_and_codes():
    # 05 第5節 エラー形式の対応表。ハンドラはこれを信じてstatus/codeを出す
    assert (InvalidIdpTokenError("x").http_status, InvalidIdpTokenError("x").code) == (
        401,
        "INVALID_IDP_TOKEN",
    )
    assert (UnauthenticatedError("x").http_status, UnauthenticatedError("x").code) == (
        401,
        "UNAUTHENTICATED",
    )
    assert (
        InvalidRefreshTokenError("x").http_status,
        InvalidRefreshTokenError("x").code,
    ) == (401, "INVALID_REFRESH_TOKEN")
    assert (
        DependencyUnavailableError("x").http_status,
        DependencyUnavailableError("x").code,
    ) == (503, "DEPENDENCY_UNAVAILABLE")


def test_test_key_constants():
    # design §2.5: ci既定値(prodガードがこの残存を検出する)
    assert DEFAULT_KID == "test-idp-1"
    assert TEST_ISSUER_PREFIX == "https://idp.ci.latch.test/"
    assert TEST_AUDIENCE == "latch-test-app"
    assert len(TEST_ACCESS_SECRET) >= 32  # RFC 7518 HS256推奨鍵長


def test_jwks_shape_matches_default_kid():
    jwks = load_jwks()
    (jwk,) = jwks["keys"]
    assert jwk["kid"] == DEFAULT_KID
    assert jwk["kty"] == "RSA"
    assert jwk["alg"] == "RS256"
    assert jwk["use"] == "sig"


def test_private_key_is_rsa_and_matches_jwks():
    # 秘密鍵とJWKS公開値の整合(検証経路が成立する前提)
    priv = load_private_key()
    assert isinstance(priv, rsa.RSAPrivateKey)
    assert priv.key_size == 2048
    numbers = priv.public_key().public_numbers()
    jwk = load_jwks()["keys"][0]
    assert _b64u_int(jwk["n"]) == numbers.n
    assert _b64u_int(jwk["e"]) == numbers.e


# --- IdPVerifier(design §2.4・§4.1-1のJWKS検証経路。Task 4で追記)---

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
ISSUER_G = "https://idp.ci.latch.test/google"
AUDIENCE = "latch-test-app"

_KEY = load_private_key()


def _clock() -> FakeClock:
    return FakeClock(NOW)


def _sign(
    *,
    subject="sub-1",
    issuer=ISSUER_G,
    audience=AUDIENCE,
    expires_in=3600,
    kid=DEFAULT_KID,
    extra=None,
) -> str:
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(seconds=expires_in)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return pyjwt.encode(payload, _KEY, algorithm="RS256", headers={"kid": kid})


def _verifier(jwks_url: str | None = None) -> IdPVerifier:
    return IdPVerifier(
        configs={
            "google": IdPVerifyConfig(
                issuer=ISSUER_G, audience=AUDIENCE, jwks_url=jwks_url
            )
        }
    )


async def test_verify_returns_provider_and_subject():
    provider, subject = await _verifier().verify(
        provider="google", idp_token=_sign(subject="user-abc"), clock=_clock()
    )
    assert (provider, subject) == ("google", "user-abc")


async def test_rejects_wrong_issuer():
    token = _sign(issuer="https://evil.example/")
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=_clock())


async def test_rejects_wrong_audience():
    token = _sign(audience="someone-elses-app")
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=_clock())


async def test_rejects_bad_signature():
    # 別鍵で署名(検証側は同梱JWKSのみ知る)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    payload = {
        "iss": ISSUER_G,
        "aud": AUDIENCE,
        "sub": "sub-1",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(seconds=3600)).timestamp()),
    }
    token = pyjwt.encode(
        payload, other_key, algorithm="RS256", headers={"kid": DEFAULT_KID}
    )
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=_clock())


async def test_rejects_unknown_kid():
    token = _sign(kid="other-kid")
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=_clock())


async def test_rejects_expired_token():
    clock = _clock()
    token = _sign(expires_in=600)
    clock.advance(timedelta(seconds=601))
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=clock)


async def test_rejects_missing_or_empty_subject():
    token = _sign(subject="")
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=_clock())
    payload = {
        "iss": ISSUER_G,
        "aud": AUDIENCE,
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(seconds=3600)).timestamp()),
    }
    no_sub = pyjwt.encode(
        payload, _KEY, algorithm="RS256", headers={"kid": DEFAULT_KID}
    )
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=no_sub, clock=_clock())


def _serve_jwks(jwks: dict) -> str:
    """URL方式試験用の最小ローカルHTTPサーバーを立ててURLを返す(使い捨て)。

    design §2.4はfile://を想定していたが、PyJWT>=2.15はfile://スキームを
    拒否する(jkuインジェクション対策)ためin-processのHTTPサーバーで代位。
    外部プロセス不要・127.0.0.1の空きポートで決定的。
    """
    import http.server
    import threading

    body = json.dumps(jwks).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # noqa: N002
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}/jwks.json"


def _stop_jwks(url: str) -> None:
    pass  # daemonスレッド+プロセス終了で片付く(portは使い捨て)


async def test_url_mode_with_http_jwks():
    # URL方式(PyJWKClient)の結合試験 — ローカルHTTPサーバー経由(design §2.4)
    url = _serve_jwks(load_jwks())
    try:
        verifier = IdPVerifier(
            configs={
                "google": IdPVerifyConfig(
                    issuer=ISSUER_G, audience=AUDIENCE, jwks_url=url
                )
            }
        )
        provider, subject = await verifier.verify(
            provider="google", idp_token=_sign(subject="url-mode"), clock=_clock()
        )
        assert (provider, subject) == ("google", "url-mode")
    finally:
        _stop_jwks(url)


async def test_url_mode_unreachable_is_dependency_unavailable():
    # 到達不能なURL(閉じたポート)→ 503 DEPENDENCY_UNAVAILABLE(05 第5節)
    verifier = IdPVerifier(
        configs={
            "google": IdPVerifyConfig(
                issuer=ISSUER_G,
                audience=AUDIENCE,
                jwks_url="http://127.0.0.1:9/jwks.json",  # port 9(discard)は閉じている
            )
        }
    )
    with pytest.raises(DependencyUnavailableError):
        await verifier.verify(provider="google", idp_token=_sign(), clock=_clock())


async def test_url_mode_kid_not_found_is_invalid():
    # JWKSは取得できるがkidが一致しない → 401(503ではない)
    jwks = load_jwks()
    jwks["keys"][0]["kid"] = "rotated-key"
    url = _serve_jwks(jwks)
    try:
        verifier = IdPVerifier(
            configs={
                "google": IdPVerifyConfig(
                    issuer=ISSUER_G, audience=AUDIENCE, jwks_url=url
                )
            }
        )
        with pytest.raises(InvalidIdpTokenError):
            await verifier.verify(provider="google", idp_token=_sign(), clock=_clock())
    finally:
        _stop_jwks(url)


async def test_unknown_provider_raises_value_error():
    # provider値はroutesのLiteral検証が守る(内部契約違反は素のValueError)
    with pytest.raises(ValueError):
        await _verifier().verify(provider="line", idp_token=_sign(), clock=_clock())
