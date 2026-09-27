"""テスト用IdP鍵ペアと例外階層(design §2.5・§3.1)。

IdPVerifier本体の試験はTask 4でこのファイルへ追記する。
"""

import base64

from cryptography.hazmat.primitives.asymmetric import rsa

from latch.auth.errors import (
    DependencyUnavailableError,
    InvalidIdpTokenError,
    InvalidRefreshTokenError,
    UnauthenticatedError,
)
from latch.auth.testkeys import (
    DEFAULT_KID,
    TEST_ACCESS_SECRET,
    TEST_AUDIENCE,
    TEST_ISSUER_PREFIX,
    load_jwks,
    load_private_key,
)


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
