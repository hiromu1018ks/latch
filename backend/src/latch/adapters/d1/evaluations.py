"""D1版・評価の処理権と全LLM試行記録(T5・計画§4・I1)。

- claim_evaluation: 厳密な評価キーでleaseを原子的に確保(所有者だけが
  外部APIを呼ぶ)。lease切れは回収可能。fencing tokenは世代。
- complete_evaluation: fencing token・Intent版を確認した時だけ確定。
- 試行は送信前に attempt_id + prepared を保存し、応答・失敗・結果不明を
  同じIDへ追記する(§4: 通信とDB更新の間の境界でも試行が消えない)。
- usage不明は cost_confirmed をNULLのままにする(0円として計上しない)。
"""

from __future__ import annotations

from typing import Any

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import micros
from latch.domain.types import ClaimResult, EvaluationKey

_UNKNOWN_RELIMIT_DEFAULT = 2  # unknown後の再実行上限(T5で記録する運用方針)


class D1EvaluationGateway:
    """ports.evaluations.EvaluationGateway のD1実装。"""

    def __init__(self, db: LocalD1Database, *, lease_seconds: float = 35.0):
        self.db = db
        self.lease_us = int(lease_seconds * 1_000_000)
        from latch.adapters.d1.attempts import D1AttemptRecorder

        self.attempts = D1AttemptRecorder(db)

    # -- 試行記録は D1AttemptRecorder へ委譲(attempts.py) ------------------

    async def prepare_attempt(self, **kwargs) -> str:
        return await self.attempts.prepare(**kwargs)

    async def finish_attempt(self, attempt_id: str, **kwargs) -> bool:
        return await self.attempts.finish(attempt_id, **kwargs)

    async def get_attempt(self, attempt_id: str):
        return await self.attempts.get(attempt_id)

    async def attempt_counts(self) -> dict:
        return await self.attempts.counts_by_system()

    # ------------------------------------------------------------------
    # claim / complete
    # ------------------------------------------------------------------

    async def claim_evaluation(
        self, key: EvaluationKey, owner: str, now
    ) -> ClaimResult:
        now_us = micros(now)
        key_text = self._key_text(key)
        # 1) 空きへの新規確保(INSERT OR IGNORE — 既存なら影響0)
        res = (
            await self.db.prepare(
                "INSERT OR IGNORE INTO evaluation_claims"
                " (evaluation_key, kind, owner, lease_expires_at, fencing_token,"
                "  intent_versions, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 1, '{}', 'claimed', ?, ?)"
            )
            .bind(key_text, key.kind, owner, now_us + self.lease_us, now_us, now_us)
            .run()
        )
        if res.changes == 1:
            return ClaimResult(
                acquired=True,
                fencing_token=1,
                lease_expires_at=now,
                stale_owner=False,
            )
        # 2) 既存行: lease切れなら奪取(条件付きUPDATEが原子仲裁)
        row = (
            await self.db.prepare(
                "SELECT owner, lease_expires_at, fencing_token, status"
                " FROM evaluation_claims WHERE evaluation_key = ?"
            )
            .bind(key_text)
            .first()
        )
        if row is None:  # 理論上起きない(INSERT OR IGNORE後の読取)
            return ClaimResult(acquired=False)
        if row["status"] == "completed":
            return ClaimResult(acquired=False)
        if row["lease_expires_at"] > now_us:
            return ClaimResult(acquired=False)
        takeover = (
            await self.db.prepare(
                "UPDATE evaluation_claims SET owner = ?, lease_expires_at = ?,"
                " fencing_token = fencing_token + 1, status = 'claimed',"
                " updated_at = ?"
                " WHERE evaluation_key = ? AND lease_expires_at <= ?"
                " AND status = 'claimed'"
            )
            .bind(owner, now_us + self.lease_us, now_us, key_text, now_us)
            .run()
        )
        if takeover.changes == 0:
            return ClaimResult(acquired=False)
        return ClaimResult(
            acquired=True,
            fencing_token=int(row["fencing_token"]) + 1,
            lease_expires_at=now,
            stale_owner=True,
        )

    async def complete_evaluation(
        self, key: EvaluationKey, fencing_token: int, result: dict[str, Any], now
    ) -> bool:
        """結果確定。fencing tokenが古い(奪取済み)場合はFalseで破棄。"""
        key_text = self._key_text(key)
        res = (
            await self.db.prepare(
                "UPDATE evaluation_claims SET status = 'completed',"
                " lease_expires_at = 0, updated_at = ?"
                " WHERE evaluation_key = ? AND fencing_token = ?"
                " AND status = 'claimed'"
            )
            .bind(micros(now), key_text, fencing_token)
            .run()
        )
        return res.changes == 1

    async def release_evaluation(
        self, key: EvaluationKey, fencing_token: int, now
    ) -> bool:
        """不要になったclaimの解放(入力不成立等)。"""
        key_text = self._key_text(key)
        res = (
            await self.db.prepare(
                "UPDATE evaluation_claims SET status = 'obsolete',"
                " lease_expires_at = 0, updated_at = ?"
                " WHERE evaluation_key = ? AND fencing_token = ?"
            )
            .bind(micros(now), key_text, fencing_token)
            .run()
        )
        return res.changes == 1

    @staticmethod
    def _key_text(key: EvaluationKey) -> str:
        import hashlib

        digest = hashlib.sha256(
            f"{key.kind}|{key.pair_key}|{key.fingerprint}|"
            f"{key.context_version}".encode()
        ).hexdigest()[:32]
        return f"{key.kind}:{digest}"
