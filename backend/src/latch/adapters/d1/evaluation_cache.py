"""D1版・厳密な評価cache(T6・計画§5.3・I2)。

規律:
- 保存するのは検証済み数値結果と由来のみ。最終提案・公開許可は
  保存しない(hit時もhard条件・公開可否は現行状態で再確認する)。
- 要求モデルと実応答モデルを分けて記録。fallback結果(actual != requested)
  は主モデルのcacheとして保存しない。実モデル不明は対象外。
- 保持期限は min(両Intentの期限, 評価確定+30日)。ユーザー削除・同意撤回は
  期限より優先して失効させる。
- 削除後に遅れて返る応答からcacheを再作成しない(削除tombstone確認)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from latch.adapters.d1.database import LocalD1Database
from latch.domain.evaluation_key import cache_key_of
from latch.domain.time import micros

RETENTION_DAYS = 30
_RETENTION_US = RETENTION_DAYS * 24 * 3600 * 1_000_000


@dataclass(frozen=True)
class CachedEvaluation:
    result: dict[str, Any]
    actual_provider: str
    actual_model: str
    requested_model: str
    created_at: int
    cache_hit: bool = True


class D1EvaluationCache:
    """evaluation_cache の読み書きと失効。"""

    def __init__(self, db: LocalD1Database) -> None:
        self.db = db

    async def get_cached_evaluation(self, key, now) -> CachedEvaluation | None:
        """期限・失効を確認して結果を返す(無ければNone)。"""
        from latch.domain.types import EvaluationKey

        assert isinstance(key, EvaluationKey)
        ck = cache_key_of(key)
        row = (
            await self.db.prepare("SELECT * FROM evaluation_cache WHERE cache_key = ?")
            .bind(ck)
            .first()
        )
        if row is None:
            return None
        if row["expires_at"] <= micros(now):
            return None
        import json

        await self.db.batch(
            [
                self.db.prepare(
                    "UPDATE evaluation_cache SET last_hit_at = ? WHERE cache_key = ?"
                ).bind(micros(now), ck),
            ]
        )
        return CachedEvaluation(
            result=json.loads(row["result"]),
            actual_provider=row["actual_provider"],
            actual_model=row["actual_model"],
            requested_model=row["requested_model"],
            created_at=row["created_at"],
        )

    async def store_evaluation(
        self,
        key,
        *,
        result: dict[str, Any],
        actual_provider: str,
        actual_model: str,
        requested_provider: str,
        requested_model: str,
        intent_expires_at: list,  # 両Intentのexpires(datetime) — None可
        now,
    ) -> tuple[bool, str | None]:
        """結果を保存。拒否理由を返す(保存成功は (True, None))。

        - 実モデル不明(actual_* の欠落)は対象外
        - fallback(actual≠requested)は主モデルcacheとして保存しない
        - Intentが削除済み(tombstone)なら保存しない(遅延応答対策)
        """
        from latch.domain.types import EvaluationKey

        assert isinstance(key, EvaluationKey)
        if actual_model is None or actual_provider is None:
            return False, "actual model unknown"
        if (actual_provider, actual_model) != (requested_provider, requested_model):
            return False, "fallback not stored as main cache"
        # 削除済みIntentへの遅延応答は再作成しない
        for iid in key.pair_key.split(":")[1:3]:
            tomb = (
                await self.db.prepare(
                    "SELECT 1 AS x FROM deletion_tombstones"
                    " WHERE target_kind = 'intent' AND target_id = ?"
                    " AND reason IN ('intent_delete','user_delete','consent_withdraw')"
                )
                .bind(iid)
                .first()
            )
            if tomb is not None:
                return False, "intent deleted"
        now_us = micros(now)
        expires_candidates = [now_us + _RETENTION_US]
        for exp in intent_expires_at:
            if exp is not None:
                expires_candidates.append(micros(exp))
        expires_at = min(expires_candidates)
        import json

        a_id, b_id = key.pair_key.split(":")[1:3]
        await self.db.batch(
            [
                self.db.prepare(
                    "INSERT OR REPLACE INTO evaluation_cache"
                    " (cache_key, pair_key, intent_a_id, intent_b_id, kind,"
                    "  fingerprint, context_version, requested_provider,"
                    "  requested_model, actual_provider, actual_model, result,"
                    "  expires_at, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ).bind(
                    cache_key_of(key),
                    key.pair_key,
                    a_id,
                    b_id,
                    key.kind,
                    key.fingerprint,
                    key.context_version,
                    requested_provider,
                    requested_model,
                    actual_provider,
                    actual_model,
                    json.dumps(result, ensure_ascii=False),
                    expires_at,
                    now_us,
                ),
            ]
        )
        return True, None

    async def invalidate_for_intents(self, intent_ids: list[str], now) -> int:
        """削除・同意撤回でpairに触れるcacheを全て失効(期限より優先)。"""
        total = 0
        for iid in intent_ids:
            res = (
                await self.db.prepare(
                    "DELETE FROM evaluation_cache"
                    " WHERE intent_a_id = ? OR intent_b_id = ?"
                )
                .bind(iid, iid)
                .run()
            )
            total += res.changes
        return total

    async def expire_now(self, now) -> int:
        """期限切れ行の掃除(retention jobから呼ぶ — T10)。"""
        res = (
            await self.db.prepare("DELETE FROM evaluation_cache WHERE expires_at <= ?")
            .bind(micros(now))
            .run()
        )
        return res.changes
