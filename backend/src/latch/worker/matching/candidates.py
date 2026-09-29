"""match_candidates の生成・記録(05 §2・design §2.3)。

正規化 intent_a_id < intent_b_id(UUID比較はPython側)のうえ、
UNIQUE(a, b, av, bv) を狙った UPSERT を行う。新規行は status='pending'。
DO UPDATE は retrieval_score・cheap_judge_score・updated_at のみ(M2 ws-4で
cheap_judge_score追加。同一バージョン内の再評価は既存レコードを更新 —
05 §2)。statusの遷移(evaluated/skipped はws-5・closed はstage1の削除処理)
はここでは扱わない。対象側versionはSELECT時点のi.version をそのまま記録す
る(評価世代)。時刻はOrigin.evaluated_at(Clock明示値・design §2.3)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.worker.matching.origin import Origin

_UPSERT = text("""
    INSERT INTO match_candidates (
        intent_a_id, intent_b_id, intent_a_version, intent_b_version,
        retrieval_score, cheap_judge_score, status, created_at, updated_at
    ) VALUES (
        :intent_a_id, :intent_b_id, :intent_a_version, :intent_b_version,
        :retrieval_score, :cheap_judge_score, 'pending', :now, :now
    )
    ON CONFLICT (intent_a_id, intent_b_id, intent_a_version, intent_b_version)
    DO UPDATE SET
        retrieval_score = EXCLUDED.retrieval_score,
        cheap_judge_score = EXCLUDED.cheap_judge_score,
        updated_at = EXCLUDED.updated_at
""")


@dataclass(frozen=True)
class CandidatePair:
    """記録した1ペア(design §2.4「(a, b, score) 一覧」の要素)。"""

    intent_a_id: uuid.UUID
    intent_b_id: uuid.UUID
    retrieval_score: float


def normalize_pair(
    origin: Origin, candidate_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """正規化 intent_a_id < intent_b_id(design §2.3)。"""
    if origin.intent_id < candidate_id:
        return origin.intent_id, candidate_id
    return candidate_id, origin.intent_id


async def upsert_candidate(
    conn: AsyncConnection,
    *,
    origin: Origin,
    candidate_id: uuid.UUID,
    candidate_version: int,
    similarity: float,
    cheap_score: float,
) -> CandidatePair:
    """1ペアのUPSERT(design §2.3)。

    retrieval_score には cosine類似度・cheap_judge_score には Layer 3 スコア
    (Layer 2通過全件を記録 — design §2.3)。丸めない(design §2.2)。
    """
    a_id, b_id = normalize_pair(origin, candidate_id)
    versions = {origin.intent_id: origin.version, candidate_id: candidate_version}
    await conn.execute(
        _UPSERT,
        {
            "intent_a_id": a_id,
            "intent_b_id": b_id,
            "intent_a_version": versions[a_id],
            "intent_b_version": versions[b_id],
            "retrieval_score": similarity,
            "cheap_judge_score": cheap_score,
            "now": origin.evaluated_at,
        },
    )
    return CandidatePair(intent_a_id=a_id, intent_b_id=b_id, retrieval_score=similarity)
