"""D1版の認証基盤: user lookup・session adapter・レート制限store(本体実装)。

- user_lookup: AuthService/api_rate_limited/parse が共有する users 1行読取
- AuthSessionStoreD1: D1SessionStore を Redis SessionStore と同じ
  「例外で失敗を返す」意味論へ合わせるadapter(create_refresh/rotate_refresh/
  revoke_access/revoke_family/is_revoked)
- D1RateLimitStore: usage_counters 1行upsertでINCR相当(原子・バケットはkeyに埋込)
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import datetime

from latch.adapters.d1.sessions import D1SessionStore
from latch.auth.errors import InvalidRefreshTokenError
from latch.auth.sessions import RefreshIssued, RotationResult
from latch.domain.time import micros


def make_d1_user_lookup(db):
    """users表の読取専用lookup(行なければNone=初回登録待ち)。

    make_user_lookup(engine) と同じ契約・結果はUUIDへ正規化。
    """

    async def user_lookup(provider: str, subject: str) -> uuid_mod.UUID | None:
        row = (
            await db.prepare(
                "SELECT id FROM users WHERE auth_provider = ? AND auth_subject = ?"
            )
            .bind(provider, subject)
            .first()
        )
        if row is None or row.get("id") is None:
            return None
        return uuid_mod.UUID(str(row["id"]))

    return user_lookup


class AuthSessionStoreD1:
    """D1SessionStore → auth.SessionStore 意味論adapter。

    - create_refresh: sidをこちらで生成(参照実装と同一・hex)
    - rotate_refresh: 失敗(None)は InvalidRefreshTokenError(401)へ
    - revoke_access: jwt_revocationsへINSERT OR REPLACE
    - revoke_family: family失効(refresh_tokensも・nowはclock由来)
    - is_revoked: jwt_revocations照会
    """

    def __init__(self, db, *, clock, ttl_seconds: int = 30 * 24 * 3600) -> None:
        self._store = D1SessionStore(db, ttl_seconds=ttl_seconds)
        self._clock = clock

    async def create_refresh(self, *, provider: str, subject: str) -> RefreshIssued:
        sid = uuid_mod.uuid4().hex
        token = await self._store.create_family(
            sid=sid, provider=provider, subject=subject, now=self._clock.now()
        )
        return RefreshIssued(token=token, sid=sid)

    async def rotate_refresh(self, *, token: str, now: datetime) -> RotationResult:
        rotated = await self._store.rotate(token=token, now=now)
        if rotated is None:
            # D1SessionStoreは再利用検出でfamily失効まで済ませている
            raise InvalidRefreshTokenError("refresh token rejected")
        return RotationResult(
            token=rotated["new_token"],
            sid=rotated["sid"],
            provider=rotated["provider"],
            subject=rotated["subject"],
        )

    async def revoke_access(self, *, jti: str, exp: datetime, now: datetime) -> None:
        await self._store.revoke_jwt(jti=jti, expires_at=exp, now=now)

    async def revoke_family(self, *, sid: str) -> None:
        await self._store.revoke_family(sid=sid, now=self._clock.now())

    async def is_revoked(self, *, jti: str) -> bool:
        return await self._store.jwt_revoked(jti=jti)


class D1RateLimitStore:
    """usage_counters 1行upsertによるINCR(Redis INCR+EXPIRE相当)。

    keyにバケット文字列が埋め込まれているため時間の進行で自然に新keyへ
    切替わる(=リセット)。window_start/expires_atは掃除用の記録。
    原子性: INSERT..ON CONFLICT..DO UPDATE..RETURNING の単一SQL。
    """

    def __init__(self, db) -> None:
        self._db = db

    async def _incr(self, key: str, ttl_s: int) -> int:
        from latch.core.clock import SystemClock

        now_us = micros(SystemClock().now())
        row = (
            await self._db.prepare(
                "INSERT INTO usage_counters (key, value, window_start, expires_at)"
                " VALUES (?, 1, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = value + 1"
                " RETURNING value"
            )
            .bind(key, now_us, now_us + ttl_s * 1_000_000)
            .first()
        )
        return int(row["value"]) if row is not None else 1

    async def incr_api(self, user_key: str, bucket: str) -> int:
        return await self._incr(f"rl:api:{user_key}:{bucket}", 120)

    async def incr_create(self, user_id: str, bucket: str) -> int:
        return await self._incr(f"rl:create:{user_id}:{bucket}", 48 * 3600)

    async def incr_update(self, intent_id: str, bucket: str) -> int:
        return await self._incr(f"rl:update:{intent_id}:{bucket}", 13 * 3600)

    async def incr_auth(self, provider: str, subject_sha: str, bucket: str) -> int:
        return await self._incr(f"rl:auth:{provider}:{subject_sha}:{bucket}", 120)
