"""ci/試験用のIdP鍵ペアとテスト既定値のローダ(design §2.5)。

鍵ファイルはテスト専用でコミットされている。prodでの使用は
build_auth_service の起動ガードが拒否する(README.md参照)。
"""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

_DIR = Path(__file__).resolve().parent

DEFAULT_KID = "test-idp-1"
TEST_ACCESS_SECRET = "latch-ci-test-access-secret-0123456789abcdef"
TEST_ISSUER_PREFIX = "https://idp.ci.latch.test/"
TEST_AUDIENCE = "latch-test-app"


def load_private_key() -> RSAPrivateKey:
    """同梱のci/試験用RS256秘密鍵を返す(テストユーザーJWT発行の署名鍵)。"""
    key = serialization.load_pem_private_key(
        (_DIR / "idp_test_private.pem").read_bytes(), password=None
    )
    assert isinstance(key, RSAPrivateKey)
    return key


def load_jwks() -> dict:
    """同梱JWKS(kid=DEFAULT_KID)をdictで返す(検証側の静的鍵ソース)。"""
    return json.loads((_DIR / "idp_test_jwks.json").read_text(encoding="utf-8"))
