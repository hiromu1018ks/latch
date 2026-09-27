"""ツール: issue-idp-token↔検証・gen-keypair・CLI経由(design §2.5-4・§4.1-7)。"""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from latch.auth.errors import InvalidIdpTokenError
from latch.auth.idp import IdPVerifier, IdPVerifyConfig
from latch.auth.testkeys import DEFAULT_KID
from latch.auth.tools import generate_keypair, issue_idp_token, main
from latch.core.clock import FakeClock
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
SETTINGS = Settings(app_env="ci")
ISSUER_G = SETTINGS.auth_idp_issuer_google
AUDIENCE = SETTINGS.auth_idp_audience_google


def _verifier(jwks_url: str | None = None) -> IdPVerifier:
    return IdPVerifier(
        configs={
            "google": IdPVerifyConfig(
                issuer=ISSUER_G, audience=AUDIENCE, jwks_url=jwks_url
            )
        }
    )


def _serve_jwks(jwks: dict) -> str:
    """URL方式試験用の最小ローカルHTTPサーバー(daemonスレッド・使い捨て)。

    PyJWT>=2.15はfile://スキームを拒否するためin-process HTTPで代位
    (test_idp.pyの同一方式。conftestなしの慣例により各ファイルで定義)。
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


async def test_issue_idp_token_verifies_with_default_settings():
    token = issue_idp_token(
        provider="google", subject="sub-x", iat=int(NOW.timestamp())
    )
    provider, subject = await _verifier().verify(
        provider="google", idp_token=token, clock=FakeClock(NOW)
    )
    assert (provider, subject) == ("google", "sub-x")


async def test_issue_idp_token_custom_issuer_rejected_by_default_config():
    # issuer/audience既定がSettingsと一致していることの裏返し(ずらすと拒否)
    token = issue_idp_token(
        provider="google",
        subject="s",
        issuer="https://other.example/",
        iat=int(NOW.timestamp()),
    )
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(
            provider="google", idp_token=token, clock=FakeClock(NOW)
        )


async def test_issue_idp_token_expiry():
    token = issue_idp_token(
        provider="google", subject="s", expires_in=10, iat=int(NOW.timestamp())
    )
    clock = FakeClock(NOW)
    await _verifier().verify(provider="google", idp_token=token, clock=clock)  # 10秒内
    clock.advance(timedelta(seconds=11))
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=clock)


async def test_gen_keypair_roundtrip(tmp_path):
    generate_keypair(out_dir=tmp_path)
    pem = (tmp_path / "idp_test_private.pem").read_bytes()
    jwks = json.loads((tmp_path / "idp_test_jwks.json").read_text())
    assert jwks["keys"][0]["kid"] == DEFAULT_KID
    # staging注入ペアで発行 → そのJWKS(URL方式)で検証(専用ペア経路の立証)。
    # PyJWT>=2.15はfile://を拒否するため、in-process HTTPサーバーでJWKSを配信
    token = issue_idp_token(
        provider="google",
        subject="staging-sub",
        private_key_pem=pem,
        iat=int(NOW.timestamp()),
    )
    provider, subject = await _verifier(_serve_jwks(jwks)).verify(
        provider="google", idp_token=token, clock=FakeClock(NOW)
    )
    assert (provider, subject) == ("google", "staging-sub")


def test_main_issue_idp_token_prints_jwt(capsys):
    assert main(["issue-idp-token", "--provider", "google", "--subject", "demo"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.count(".") == 2  # header.payload.signature


def test_main_gen_keypair(tmp_path):
    assert main(["gen-keypair", "--out-dir", str(tmp_path)]) == 0
    assert (tmp_path / "idp_test_private.pem").exists()
    assert (tmp_path / "idp_test_jwks.json").exists()


def test_main_requires_known_command():
    with pytest.raises(SystemExit):
        main(["no-such-command"])


async def test_cli_issue_token_verifies():
    # 完了条件7のunit立証: python -m latch.auth のCLI出力がIdPVerifierで検証できる。
    # --iat で発行時刻をNOWに固定(実行時刻とFakeClockの取り合わせを排除 — 計画書注記)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "latch.auth",
            "issue-idp-token",
            "--provider",
            "google",
            "--subject",
            "demo",
            "--iat",
            str(int(NOW.timestamp())),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    token = proc.stdout.strip()
    provider, subject = await _verifier().verify(
        provider="google", idp_token=token, clock=FakeClock(NOW)
    )
    assert (provider, subject) == ("google", "demo")
