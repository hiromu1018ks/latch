"""D1版・session(refresh回転・再利用検出・JWT失効 — Redis置換)。

原子性: 回転は usage と異なり1行の条件付きUPDATEで確定する
(Redis成功後rollback問題の解消 — T0回帰ケース
redis_success_then_rollback のD1側回答)。
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import micros

_TTL_SECONDS = 30 * 24 * 3600  # refresh有効期限(参照実装と同一)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class D1SessionStore:
    """refresh tokenの発行・回転・検証とJWT失効リスト。"""

    def __init__(self, db: LocalD1Database, *, ttl_seconds: int = _TTL_SECONDS):
        self.db = db
        self.ttl_seconds = ttl_seconds

    async def create_family(self, *, sid: str, provider: str, subject: str, now) -> str:
        """新規session family + 先頭refresh token。tokenは呼び出し側へ返す。"""
        token = secrets.token_urlsafe(32)
        now_us = micros(now)
        await self.db.batch(
            [
                self.db.prepare(
                    "INSERT INTO session_families"
                    " (sid, provider, subject, status, created_at)"
                    " VALUES (?, ?, ?, 'active', ?)"
                ).bind(sid, provider, subject, now_us),
                self.db.prepare(
                    "INSERT INTO refresh_tokens"
                    " (token_hash, sid, provider, subject, status, created_at,"
                    "  expires_at) VALUES (?, ?, ?, ?, 'active', ?, ?)"
                ).bind(
                    _hash_token(token),
                    sid,
                    provider,
                    subject,
                    now_us,
                    now_us + self.ttl_seconds * 1_000_000,
                ),
            ]
        )
        return token

    async def rotate(self, token: str, now) -> dict[str, Any] | None:
        """回転。再利用(失効token再呈示)はfamily全体をrevokedへ。

        戻り値: {new_token, sid, provider, subject} | None(無効)
        """
        token_hash = _hash_token(token)
        row = (
            await self.db.prepare(
                "SELECT token_hash, sid, provider, subject, status, expires_at"
                " FROM refresh_tokens WHERE token_hash = ?"
            )
            .bind(token_hash)
            .first()
        )
        now_us = micros(now)
        if row is None:
            return None
        if row["status"] == "rotated":
            # 再利用検出 → family失効(参照実装と同一)
            await self.db.batch(
                [
                    self.db.prepare(
                        "UPDATE session_families SET status = 'revoked',"
                        " revoked_at = ? WHERE sid = ?"
                    ).bind(now_us, row["sid"]),
                    self.db.prepare(
                        "UPDATE refresh_tokens SET status = 'revoked'"
                        " WHERE sid = ? AND status IN ('active','rotated')"
                    ).bind(row["sid"]),
                ]
            )
            return None
        if row["status"] == "revoked" or row["expires_at"] <= now_us:
            return None
        new_token = secrets.token_urlsafe(32)
        # 1行の条件付きUPDATEが回転の原子性を担う
        res = (
            await self.db.prepare(
                "UPDATE refresh_tokens SET status = 'rotated', rotated_to = ?"
                " WHERE token_hash = ? AND status = 'active'"
            )
            .bind(_hash_token(new_token), token_hash)
            .run()
        )
        if res.changes == 0:
            return None
        await self.db.batch(
            [
                self.db.prepare(
                    "INSERT INTO refresh_tokens"
                    " (token_hash, sid, provider, subject, status, created_at,"
                    "  expires_at) VALUES (?, ?, ?, ?, 'active', ?, ?)"
                ).bind(
                    _hash_token(new_token),
                    row["sid"],
                    row["provider"],
                    row["subject"],
                    now_us,
                    now_us + self.ttl_seconds * 1_000_000,
                ),
            ]
        )
        return {
            "new_token": new_token,
            "sid": row["sid"],
            "provider": row["provider"],
            "subject": row["subject"],
        }

    async def revoke_family(self, sid: str, now) -> None:
        await self.db.batch(
            [
                self.db.prepare(
                    "UPDATE session_families SET status = 'revoked', revoked_at = ?"
                    " WHERE sid = ? AND status = 'active'"
                ).bind(micros(now), sid),
                self.db.prepare(
                    "UPDATE refresh_tokens SET status = 'revoked'"
                    " WHERE sid = ? AND status IN ('active','rotated')"
                ).bind(sid),
            ]
        )

    async def revoke_jwt(self, jti: str, expires_at, now) -> None:
        await self.db.batch(
            [
                self.db.prepare(
                    "INSERT OR REPLACE INTO jwt_revocations"
                    " (jti, expires_at, created_at) VALUES (?, ?, ?)"
                ).bind(jti, micros(expires_at), micros(now)),
            ]
        )

    async def jwt_revoked(self, jti: str) -> bool:
        row = (
            await self.db.prepare("SELECT 1 AS x FROM jwt_revocations WHERE jti = ?")
            .bind(jti)
            .first()
        )
        return row is not None
