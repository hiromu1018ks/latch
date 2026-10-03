"""ci/試験用のIdP鍵ペアとテスト既定値のローダ(design §2.5)。

鍵ファイルはテスト専用でコミットされている。prodでの使用は
build_auth_service の起動ガードが拒否する(README.md参照)。
"""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from latch.settings import Settings

_DIR = Path(__file__).resolve().parent

DEFAULT_KID = "test-idp-1"
TEST_ACCESS_SECRET = "latch-ci-test-access-secret-0123456789abcdef"
TEST_ISSUER_PREFIX = "https://idp.ci.latch.test/"
TEST_AUDIENCE = "latch-test-app"


def _keys_dir() -> Path:
    """鍵ディレクトリを決める(既定=同梱・LATCH_AUTH_TESTKEYS_DIRで差し替え)。

    staging専用鍵ペアの注入(10 §1「認証のテスト構成」・M4 ws-1 design §2.4)。
    発行側(issue-idp-token)と検証側(JWKS)が同一関数経由のため、ディレクトリ
    差し替えで自然に揃う。空=同梱(ci既定・無影響)。
    """
    override = Settings().auth_testkeys_dir.strip()
    return Path(override) if override else _DIR


def load_private_key() -> RSAPrivateKey:
    """ci/試験用RS256秘密鍵を返す(テストユーザーJWT発行の署名鍵)。"""
    key = serialization.load_pem_private_key(
        (_keys_dir() / "idp_test_private.pem").read_bytes(), password=None
    )
    assert isinstance(key, RSAPrivateKey)
    return key


def load_jwks() -> dict:
    """JWKS(kid=DEFAULT_KID)をdictで返す(検証側の静的鍵ソース)。"""
    return json.loads((_keys_dir() / "idp_test_jwks.json").read_text(encoding="utf-8"))
