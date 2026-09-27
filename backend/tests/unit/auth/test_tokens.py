"""アクセスJWT: 発行/検証・claim構成・期限(FakeClock)・algピン(design §2.2他)。"""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from latch.auth.errors import UnauthenticatedError
from latch.auth.testkeys import load_private_key
from latch.auth.tokens import (
    ACCESS_TTL_S,
    REFRESH_TTL_S,
    issue_access_token,
    verify_access_token,
)
from latch.core.clock import FakeClock

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
SECRET = "unit-test-access-secret-0123456789abcdef"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _issue(clock: FakeClock, *, provider="google", subject="sub-1", sid="sid-1") -> str:
    return issue_access_token(
        clock=clock, secret=SECRET, provider=provider, subject=subject, sid=sid
    )


def test_ttl_constants_match_d21():
    assert ACCESS_TTL_S == 3600  # 04 D-21: 独自JWT 有効期限1時間
    assert REFRESH_TTL_S == 30 * 24 * 3600  # 04 D-21: リフレッシュ30日


def test_roundtrip_returns_expected_claims(clock):
    token = _issue(clock)
    claims = verify_access_token(clock=clock, secret=SECRET, token=token)
    assert claims.auth_provider == "google"
    assert claims.auth_subject == "sub-1"
    assert claims.jti
    assert claims.sid == "sid-1"
    assert claims.iat == NOW
    assert claims.exp == NOW + timedelta(seconds=ACCESS_TTL_S)


def test_token_payload_structure(clock):
    # 05 第5節: auth_provider/auth_subject が必須claim。他はdesign §2.2の構成
    token = _issue(clock, provider="apple", subject="apple-sub", sid="f-1")
    # verify_iat無効化: PyJWT既定のiat検証はシステムクロック比較のため
    # FakeClock時刻(NOW)との取り合わせで非決定的になる。構成検査には不要。
    # verify_aud無効化: audience未指定decodeはtokenのaud存在だけで拒否される
    decode_opts = {"verify_iat": False, "verify_aud": False}
    payload = pyjwt.decode(token, SECRET, algorithms=["HS256"], options=decode_opts)
    assert payload["iss"] == "latch-api"
    assert payload["aud"] == "latch-app"
    assert payload["sub"] == "apple:apple-sub"
    assert payload["auth_provider"] == "apple"
    assert payload["auth_subject"] == "apple-sub"
    assert payload["sid"] == "f-1"
    assert set(payload) == {
        "iss",
        "aud",
        "sub",
        "auth_provider",
        "auth_subject",
        "jti",
        "sid",
        "iat",
        "exp",
    }
    # 発行ごとにjtiは一意(uuid4)
    other = pyjwt.decode(
        _issue(clock, provider="apple", subject="apple-sub", sid="f-1"),
        SECRET,
        algorithms=["HS256"],
        options=decode_opts,
    )
    assert other["jti"] != payload["jti"]


def test_expired_after_ttl(clock):
    # design §2.8: expはClock手動比較(FakeClock.advanceで決定的に再現)
    token = _issue(clock)
    clock.advance(timedelta(seconds=ACCESS_TTL_S + 1))
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token=token)


def test_valid_just_before_expiry(clock):
    token = _issue(clock)
    clock.advance(timedelta(seconds=ACCESS_TTL_S - 1))
    claims = verify_access_token(clock=clock, secret=SECRET, token=token)
    assert claims.auth_subject == "sub-1"


def test_tampered_token_rejected(clock):
    token = _issue(clock)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token=tampered)


def test_wrong_secret_rejected(clock):
    token = _issue(clock)
    with pytest.raises(UnauthenticatedError):
        verify_access_token(
            clock=clock, secret="different-secret-0123456789abcdef", token=token
        )


def test_rejects_alg_none_token(clock):
    # Review Focus #1: alg:none攻撃(手組みの署名なしトークン)
    header = {"alg": "none", "typ": "JWT"}

    def b64u(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    payload = {
        "iss": "latch-api",
        "aud": "latch-app",
        "sub": "google:sub-1",
        "auth_provider": "google",
        "auth_subject": "sub-1",
        "jti": "x",
        "sid": "s",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(hours=2)).timestamp()),
    }
    unsigned = (
        f"{b64u(json.dumps(header).encode())}.{b64u(json.dumps(payload).encode())}."
    )
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token=unsigned)


def test_rejects_rs256_signed_token(clock):
    # Review Focus #1: テスト秘密鍵でRS256署名したトークンをHS256検証に通そうする混入
    header = {"alg": "RS256", "typ": "JWT", "kid": "test-idp-1"}
    unsigned = pyjwt.encode(
        {
            "iss": "latch-api",
            "aud": "latch-app",
            "sub": "google:sub-1",
            "auth_provider": "google",
            "auth_subject": "sub-1",
            "jti": "x",
            "sid": "s",
            "iat": int(NOW.timestamp()),
            "exp": int((NOW + timedelta(hours=2)).timestamp()),
        },
        load_private_key(),
        algorithm="RS256",
        headers=header,
    )
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token=unsigned)


def test_garbage_token_rejected(clock):
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token="not-a-jwt")
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token="")
