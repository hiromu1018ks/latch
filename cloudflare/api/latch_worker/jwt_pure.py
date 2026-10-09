"""純Python実装のpyjwt互換サブセット(Workersバンドル用・pyjwt不採用)。

backend/src/latch/auth/tokens.py・idp.py が利用するAPI面のみを実装する:
- encode(payload, secret, algorithm="HS256") — アクセスJWT発行
- decode(token, key, algorithms, audience, issuer, options) — HS256/RS256検証
  (verify_exp/verify_iat無効化+手動exp比較は呼び出し側規律のため対応)
- PyJWK.from_dict(jwk, algorithm="RS256") → .key
- get_unverified_header(token)
- InvalidTokenError例外階層・PyJWKClient*(JWKS URL経路は未使用のためstub)

意味論は実pyjwtと揃える: 署名比較はhmac.compare_digest・alg不一致拒否・
issuer/audience検証・exp/iatはoptionsで無効化。RS256検証はPKCS#1 v1.5の
modexp+パディング検査(cloudflare/api/latch_worker/probes.py の実証実装と
同一方式)。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

# DigestInfo prefix for SHA-256 (RFC 8017 §9.2)
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


# -- 例外階層(実pyjwtと同名・すべてInvalidTokenErrorへ集約) -------------------

class InvalidTokenError(Exception):
    """検証失敗の基底(呼び出し側はこれだけを捕まえる)。"""


class DecodeError(InvalidTokenError):
    pass


class InvalidSignatureError(DecodeError):
    pass


class InvalidAlgorithmError(InvalidTokenError):
    pass


class InvalidIssuerError(InvalidTokenError):
    pass


class InvalidAudienceError(InvalidTokenError):
    pass


class ExpiredSignatureError(InvalidTokenError):
    pass


class ImmatureSignatureError(InvalidTokenError):
    pass


class MissingRequiredClaimError(InvalidTokenError):
    pass


class PyJWKClientError(Exception):
    """JWKS URL取得経路の失敗(Workers構成では未使用)。"""


class PyJWKClientConnectionError(PyJWKClientError):
    pass


class PyJWKError(Exception):
    pass


class PyJWKSetError(PyJWKError):
    pass


# -- base64url helpers ---------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    if not isinstance(data, str):
        raise DecodeError("invalid token")
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad)
    except Exception as exc:  # noqa: BLE001
        raise DecodeError("invalid base64url") from exc


class _HSKey:
    """HS256検証鍵(secret文字列)。"""

    __slots__ = ("secret",)

    def __init__(self, secret: str) -> None:
        self.secret = secret


class _RSAPubKey:
    """RS256検証鍵(JWKのn/e)。署名は行わない(検証のみ)。"""

    __slots__ = ("n", "e")

    def __init__(self, n: int, e: int) -> None:
        self.n = n
        self.e = e


class PyJWK:
    """JWK dict→検証鍵の変換(kid保持・.keyで鍵を渡す)。"""

    def __init__(self, key: Any, algorithm: str | None = None, kid: str | None = None):
        self.key = key
        self.algorithm = algorithm
        self.kid = kid

    @classmethod
    def from_dict(cls, obj: dict, algorithm: str | None = None) -> PyJWK:
        if not isinstance(obj, dict):
            raise PyJWKError("jwk must be a dict")
        kty = obj.get("kty")
        if kty != "RSA":
            raise PyJWKError(f"unsupported kty: {kty!r}")
        n_b64, e_b64 = obj.get("n"), obj.get("e")
        if not n_b64 or not e_b64:
            raise PyJWKError("RSA jwk requires n and e")
        try:
            n = int.from_bytes(_b64url_decode(n_b64), "big")
            e = int.from_bytes(_b64url_decode(e_b64), "big")
        except DecodeError as exc:
            raise PyJWKError("invalid RSA jwk") from exc
        return cls(_RSAPubKey(n, e), algorithm=algorithm, kid=obj.get("kid"))


class PyJWKClient:
    """JWKS URL経路(Workers構成では静的JWKSのみ使用のため未実装)。

    idp.pyのimportと例外処理を満たすための定義。実呼び出しは
    NotImplementedError(到達は構成ミス＝気付ける)。
    """

    def __init__(self, uri: str, cache_keys: bool = True, max_cached_keys: int = 16, timeout: int = 30, headers: dict | None = None) -> None:  # noqa: E501
        self.uri = uri

    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        raise NotImplementedError("PyJWKClient is not available on Workers (static JWKS only)")  # noqa: E501


# -- header --------------------------------------------------------------------

def get_unverified_header(token: str) -> dict:
    try:
        header_b64 = token.split(".", 2)[0]
        header = json.loads(_b64url_decode(header_b64))
    except (DecodeError, ValueError, IndexError) as exc:
        raise DecodeError("invalid header") from exc
    if not isinstance(header, dict):
        raise DecodeError("invalid header")
    return header


def _decode_claims(payload_b64: str) -> dict:
    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except (DecodeError, ValueError) as exc:
        raise DecodeError("invalid payload") from exc
    if not isinstance(claims, dict):
        raise DecodeError("invalid payload")
    return claims


# -- verify --------------------------------------------------------------------

def _verify_hs256(signing_input: bytes, sig: bytes, secret: str) -> None:
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise InvalidSignatureError("signature verification failed")


def _verify_rs256(signing_input: bytes, sig: bytes, key: _RSAPubKey) -> None:
    k = (key.n.bit_length() + 7) // 8
    if len(sig) != k:
        raise InvalidSignatureError("signature verification failed")
    s = int.from_bytes(sig, "big")
    if s >= key.n:
        raise InvalidSignatureError("signature verification failed")
    m = pow(s, key.e, key.n)
    em = m.to_bytes(k, "big")
    digest = hashlib.sha256(signing_input).digest()
    expected = (
        b"\x00\x01"
        + b"\xff" * (k - len(_SHA256_DIGEST_INFO) - len(digest) - 3)
        + b"\x00"
        + _SHA256_DIGEST_INFO
        + digest
    )
    if not hmac.compare_digest(em, expected):
        raise InvalidSignatureError("signature verification failed")


def decode(
    token: str | bytes,
    key: Any,
    algorithms: list[str] | None = None,
    *,
    audience: str | None = None,
    issuer: str | None = None,
    options: dict | None = None,
    **kwargs,  # noqa: ARG001 — 実pyjwt互換の unused 引数
) -> dict:
    """JWT検証(署名・alg・issuer・audience。exp/iatはoptions既定で無効化)。

    呼び出し側(tokens.py/idp.py)は options={"verify_exp": False,
    "verify_iat": False} でexp手動比較規律を使うため、ここでも
    verify_exp/verify_iat/verify_nbf は実施しない(optionsで明示指定が
    あれば従う — 呼び出し側は常にFalse指定)。
    """
    opts = {"verify_exp": False, "verify_iat": False, "verify_nbf": False}
    if options:
        for name in ("verify_exp", "verify_iat", "verify_nbf", "verify_aud", "verify_iss"):
            if name in options:
                opts[name] = options[name]
    if not algorithms:
        raise InvalidAlgorithmError("algorithms is required")
    if isinstance(token, bytes):
        token = token.decode()
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except (ValueError, AttributeError) as exc:
        raise DecodeError("not enough segments") from exc
    header = get_unverified_header(token)
    alg = header.get("alg")
    if alg not in algorithms:
        raise InvalidAlgorithmError("the token alg is not allowed")
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = _b64url_decode(sig_b64)
    if alg == "HS256":
        secret = key.secret if isinstance(key, _HSKey) else key
        if not isinstance(secret, str):
            secret = secret.decode() if isinstance(secret, bytes) else str(secret)
        _verify_hs256(signing_input, sig, secret)
    elif alg == "RS256":
        if not isinstance(key, _RSAPubKey):
            raise InvalidAlgorithmError("RS256 requires an RSA public key")
        _verify_rs256(signing_input, sig, key)
    else:
        raise InvalidAlgorithmError("unsupported algorithm")
    claims = _decode_claims(payload_b64)
    if opts.get("verify_iss", issuer is not None):
        if claims.get("iss") != issuer:
            raise InvalidIssuerError("invalid issuer")
    if opts.get("verify_aud", audience is not None):
        aud = claims.get("aud")
        if isinstance(aud, str):
            aud_list = [aud]
        elif isinstance(aud, list):
            aud_list = aud
        else:
            raise InvalidAudienceError("invalid audience")
        if audience not in aud_list:
            raise InvalidAudienceError("audience doesn't match")
    if opts.get("verify_exp"):
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)):
            raise MissingRequiredClaimError("exp")
        import time as _time

        if exp <= _time.time():
            raise ExpiredSignatureError("token expired")
    if opts.get("verify_iat") and "iat" in claims:
        iat = claims.get("iat")
        if not isinstance(iat, (int, float)):
            raise InvalidTokenError("invalid iat")
    return claims


def encode(payload: dict, key: str, algorithm: str = "HS256", headers: dict | None = None, **kwargs) -> str:  # noqa: ARG001
    """JWT発行(Workersで使うのはHS256のみ)。"""
    if algorithm != "HS256" or not isinstance(key, str):
        raise InvalidAlgorithmError("only HS256 encode is supported")
    header = {"alg": "HS256", "typ": "JWT"}
    if headers:
        header.update(headers)
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(key.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


# -- モジュールを「jwt」として使うための公開面 -------------------------------

def _as_module_dict() -> dict[str, Any]:
    """_compat.install_shims が sys.modules['jwt'] へ登録する公開面。"""
    module = {
        "__name__": "jwt",
        "encode": encode,
        "decode": decode,
        "get_unverified_header": get_unverified_header,
        "PyJWK": PyJWK,
        "PyJWKClient": PyJWKClient,
        "PyJWKClientError": PyJWKClientError,
        "PyJWKClientConnectionError": PyJWKClientConnectionError,
        "PyJWKError": PyJWKError,
        "PyJWKSetError": PyJWKSetError,
        "InvalidTokenError": InvalidTokenError,
        "DecodeError": DecodeError,
        "InvalidSignatureError": InvalidSignatureError,
        "InvalidAlgorithmError": InvalidAlgorithmError,
        "InvalidIssuerError": InvalidIssuerError,
        "InvalidAudienceError": InvalidAudienceError,
        "ExpiredSignatureError": ExpiredSignatureError,
        "ImmatureSignatureError": ImmatureSignatureError,
        "MissingRequiredClaimError": MissingRequiredClaimError,
        # 実pyjwtの例外は ValueError 系ではなく OAuth2/PyJWTError階層。
        # reference実装の except 句に ValueError がないため不一致は無害。
    }
    return module
