"""アクセスJWTの発行/検証(design §2.2・§2.8)。

HS256(対称鍵)にピン留め — 検証者=発行者=API Layerのみ(04 第2節)であり、
alg confusionの余地を構造的に塞ぐ。exp/iatはPyJWTに検証させず
Clock.now() との手動比較で行う(C2: 時刻参照の単一経路。FakeClockで
期限切れを決定的に再現できる)。失効リスト照会は行わない(service.authenticate の責務)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt as pyjwt

from latch.auth.errors import UnauthenticatedError
from latch.core.clock import Clock

ACCESS_TTL_S = 3600  # 04 D-21: 独自JWT 有効期限1時間
REFRESH_TTL_S = 30 * 24 * 3600  # 04 D-21: リフレッシュトークン30日

_ISSUER = "latch-api"
_AUDIENCE = "latch-app"
_ALGORITHM = "HS256"


@dataclass(frozen=True)
class AccessTokenClaims:
    """検証済みアクセスJWTのclaim(require_authenticated の戻り型)。"""

    auth_provider: str
    auth_subject: str
    jti: str
    sid: str
    iat: datetime
    exp: datetime


def issue_access_token(
    *, clock: Clock, secret: str, provider: str, subject: str, sid: str
) -> str:
    """アクセスJWTを発行する(auth_provider/auth_subject を必須claimとして運ぶ)。"""
    now = clock.now()
    payload = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": f"{provider}:{subject}",
        "auth_provider": provider,
        "auth_subject": subject,
        "jti": str(uuid.uuid4()),
        "sid": sid,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ACCESS_TTL_S)).timestamp()),
    }
    return pyjwt.encode(payload, secret, algorithm=_ALGORITHM)


def verify_access_token(*, clock: Clock, secret: str, token: str) -> AccessTokenClaims:
    """署名・iss・aud・algを検証し、expをClock手動比較で判定する。

    失敗はすべて UnauthenticatedError(署名改ざん・alg混入・期限切れ等の
    区別を応答に漏らさない)。
    """
    try:
        payload = pyjwt.decode(
            token,
            secret,
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            audience=_AUDIENCE,
            options={"verify_exp": False, "verify_iat": False},
        )
    except pyjwt.InvalidTokenError as exc:
        raise UnauthenticatedError("access token rejected") from exc
    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
    if exp <= clock.now():
        raise UnauthenticatedError("access token expired")
    return AccessTokenClaims(
        auth_provider=payload["auth_provider"],
        auth_subject=payload["auth_subject"],
        jti=payload["jti"],
        sid=payload["sid"],
        iat=datetime.fromtimestamp(payload["iat"], tz=UTC),
        exp=exp,
    )
