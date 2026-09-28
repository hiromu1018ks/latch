"""API全体60req/分の強制点(design §2.4)。

require_authenticated を内包する(401→429の順を構造で保証 — 攻撃的な流量に
ドメイン検証を消費させる前に429で返す)。claimsにuser_idは無いため(C3)、
app.state.user_lookup で解決し、未登録はanonフォールバックキーへ(計画§Task 4)。
ルータ単位で付すためミドルウェア不採用(v1 API契約にのみ適用を構造で表現)。
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import Depends, Request

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.ratelimit.errors import RateLimitDependencyError


def _anon_key(provider: str, subject: str) -> str:
    return "anon-" + hashlib.sha256(f"{provider}:{subject}".encode()).hexdigest()


async def api_rate_limited(
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
) -> AccessTokenClaims:
    """認証成立後に user_id 単位でINCRする(未載荷/Noneのlimiterは素通り)。"""
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return claims
    lookup = getattr(request.app.state, "user_lookup", None)
    if lookup is None:
        user_key = _anon_key(claims.auth_provider, claims.auth_subject)
    else:
        try:
            user_id = await lookup(claims.auth_provider, claims.auth_subject)
        except Exception as exc:
            raise RateLimitDependencyError("rate limit dependency unavailable") from exc
        user_key = (
            str(user_id)
            if user_id is not None
            else _anon_key(claims.auth_provider, claims.auth_subject)
        )
    await limiter.check_api(user_key=user_key)
    return claims
