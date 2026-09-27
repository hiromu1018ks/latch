"""Redis上のセッション状態(design §2.3の4鍵型)。

鍵(接頭辞 auth: — Jevカウンタ等M1+と名前空間を分ける):
  失効リスト:   SET  auth:revoked:{jti}        "1"            PX <(exp−now)ms>
  リフレッシュ: SET  auth:rt:{sha256(token)}   <JSON>          PX 30d
  族索引:       SADD auth:family:{fid}          {sha256(token)} PX 30d
  消費済み:     SET  auth:used:{sha256(token)}  {fid}          PX 30d

リフレッシュは不透明トークン(secret.token_urlsafe(32))をRedisにはSHA-256
ハッシュで保持する(生値を置かない。ダンプ漏洩時の悪用防止)。
回転はGETDELでアトミック消費し、消費済みトークンの再提示は盗難疑いとして
族(当該IdPセッションのトークン族)全体を失効する(05 第5節。安全側)。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

import redis.asyncio as aioredis

from latch.auth.errors import InvalidRefreshTokenError
from latch.auth.tokens import REFRESH_TTL_S


def _sha256_hex(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class RefreshIssued:
    """create_refresh の結果(トークン本体と族id=sid)。"""

    token: str
    sid: str


@dataclass(frozen=True)
class RotationResult:
    """rotate_refresh の結果(新トークン・族id・紐付け情報)。"""

    token: str
    sid: str
    provider: str
    subject: str


class SessionStore:
    """Redis操作を閉じ込めるStore(decode_responses=True のRedisを注入)。"""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def create_refresh(self, *, provider: str, subject: str) -> RefreshIssued:
        """token発行時: 族idを生成し、rt鍵と族索引(初期メンバー)を書く。"""
        token = secrets.token_urlsafe(32)
        sid = uuid.uuid4().hex
        data = json.dumps({"provider": provider, "subject": subject, "family": sid})
        pipe = self._redis.pipeline()
        pipe.set(f"auth:rt:{_sha256_hex(token)}", data, ex=REFRESH_TTL_S)
        pipe.sadd(f"auth:family:{sid}", _sha256_hex(token))
        pipe.expire(f"auth:family:{sid}", REFRESH_TTL_S)
        await pipe.execute()
        return RefreshIssued(token=token, sid=sid)

    async def rotate_refresh(self, *, token: str, now: datetime) -> RotationResult:
        """refresh回転: GETDELでアトミック消費 → 新トークン発行+消費済みマーカー。

        消費済みトークンの再提示は盗難疑いとして族全失効のうえ401。
        不在・期限切れは族失効せず401(族を特定できないため。
        また期限切れは盗難と区別できないが対象外 — design §2.3)。
        """
        sha = _sha256_hex(token)
        raw = await self._redis.getdel(f"auth:rt:{sha}")
        if raw is None:
            used = await self._redis.get(f"auth:used:{sha}")
            if used is not None:
                await self._revoke_family(used)
                raise InvalidRefreshTokenError("refresh token reuse detected")
            raise InvalidRefreshTokenError("refresh token not found or expired")
        data = json.loads(raw)
        sid = data["family"]
        new_token = secrets.token_urlsafe(32)
        new_sha = _sha256_hex(new_token)
        pipe = self._redis.pipeline()
        pipe.set(f"auth:rt:{new_sha}", raw, ex=REFRESH_TTL_S)
        pipe.sadd(f"auth:family:{sid}", new_sha)
        pipe.expire(f"auth:family:{sid}", REFRESH_TTL_S)
        pipe.set(f"auth:used:{sha}", sid, ex=REFRESH_TTL_S)
        await pipe.execute()
        return RotationResult(
            token=new_token,
            sid=sid,
            provider=data["provider"],
            subject=data["subject"],
        )

    async def revoke_access(self, *, jti: str, exp: datetime, now: datetime) -> None:
        """logout: 失効リストへ登録(TTL=残り有効期限 — expで自動消滅)。"""
        remaining_ms = max(int((exp - now).total_seconds() * 1000), 1)
        await self._redis.set(f"auth:revoked:{jti}", "1", px=remaining_ms)

    async def revoke_family(self, *, sid: str) -> None:
        """logout・族失効: 族メンバーのrt鍵と族索引を消す(usedは残す=再検知の連鎖)。"""
        await self._revoke_family(sid)

    async def is_revoked(self, *, jti: str) -> bool:
        """認証Dependency: 失効リスト掲載の照会(05 第5節)。"""
        return bool(await self._redis.exists(f"auth:revoked:{jti}"))

    async def _revoke_family(self, sid: str) -> None:
        members = await self._redis.smembers(f"auth:family:{sid}")
        pipe = self._redis.pipeline()
        for sha in members:
            pipe.delete(f"auth:rt:{sha}")
        pipe.delete(f"auth:family:{sid}")
        await pipe.execute()
