"""テスト用認証ツール(design §2.5-4。テストユーザーJWT発行の内部ツール)。

python -m latch.auth のCLIサブコマンド:
  issue-idp-token — テストユーザーのIdPトークン(JWT)を発行(staging鍵ペアで署名)
  gen-keypair     — staging専用のRS256鍵ペア(秘密鍵PEM+JWKS JSON)を生成

発行時刻はClock(SystemClock)経由。--iat で上書き可(arch test準拠 —
本モジュールも backend/src 内のため実時間の直接参照は書かない)。
アクセストークンの直接発行サブコマンドは持たない — 常により本物の経路
(POST /v1/auth/token)を残す(design §2.5-4)。
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.core.clock import SystemClock
from latch.settings import Settings


def _b64u(value: int) -> str:
    size = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


def issue_idp_token(
    *,
    provider: str,
    subject: str,
    expires_in: int = 3600,
    private_key_pem: bytes | None = None,
    issuer: str | None = None,
    audience: str | None = None,
    iat: int | None = None,
) -> str:
    """IdPトークンを発行する(issuer/audience既定=Settings=API側検証設定と自動一致)。"""
    settings = Settings()
    issuer = (
        issuer
        if issuer is not None
        else getattr(settings, f"auth_idp_issuer_{provider}")
    )
    audience = (
        audience
        if audience is not None
        else getattr(settings, f"auth_idp_audience_{provider}")
    )
    now = SystemClock().now()
    issued_at = iat if iat is not None else int(now.timestamp())
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": issued_at,
        "exp": issued_at + expires_in,
    }
    if private_key_pem is None:
        key = load_private_key()
    else:
        key = serialization.load_pem_private_key(private_key_pem, password=None)
    return pyjwt.encode(payload, key, algorithm="RS256", headers={"kid": DEFAULT_KID})


def generate_keypair(*, out_dir: Path) -> None:
    """staging専用のRS256鍵ペア(秘密鍵PEM+JWKS JSON・kid=DEFAULT_KID)を書き出す。

    鍵ローテーション時の差し替えにも使用する(design §1.4)。
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.joinpath("idp_test_private.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": DEFAULT_KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }
    out_dir.joinpath("idp_test_jwks.json").write_text(
        json.dumps({"keys": [jwk]}, indent=2) + "\n"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m latch.auth", description="LATCHテスト用認証ツール(10 第1節)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser(
        "issue-idp-token", help="テストユーザーのIdPトークン(JWT)を発行"
    )
    issue.add_argument("--provider", required=True, choices=["google", "apple"])
    issue.add_argument("--subject", required=True)
    issue.add_argument("--expires-in", type=int, default=3600)
    issue.add_argument(
        "--private-key", default=None, help="秘密鍵PEMパス(既定=同梱テスト鍵)"
    )
    issue.add_argument(
        "--issuer", default=None, help="既定=Settings の provider別issuer"
    )
    issue.add_argument(
        "--audience", default=None, help="既定=Settings の provider別audience"
    )
    issue.add_argument(
        "--iat", type=int, default=None, help="発行unix秒(既定=現在時刻)"
    )

    gen = sub.add_parser("gen-keypair", help="RS256鍵ペア(秘密鍵PEM+JWKS JSON)を生成")
    gen.add_argument("--out-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.command == "issue-idp-token":
        pem = Path(args.private_key).read_bytes() if args.private_key else None
        token = issue_idp_token(
            provider=args.provider,
            subject=args.subject,
            expires_in=args.expires_in,
            private_key_pem=pem,
            issuer=args.issuer,
            audience=args.audience,
            iat=args.iat,
        )
        print(token)
        return 0
    if args.command == "gen-keypair":
        generate_keypair(out_dir=Path(args.out_dir))
        return 0
    return 1
