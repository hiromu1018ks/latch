"""embedding_completed 起点の第2段前半のオーケストレータ(design §2.4)。

起点検証→Layer 2検索→match_candidates記録。トランザクションは呼び出し側
(engine.begin() で包む — design §2.3)。例外は握らず呼び出し側(stage1の
再試行ループに載る将来経路)へ伝播させる。モジュール属性経由で
origin/layer2/candidates を呼ぶ(unit試験がmonkeypatchで差し替え可能)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncConnection

from latch.core.clock import Clock
from latch.worker.matching import candidates, layer2, origin
from latch.worker.matching.candidates import CandidatePair


@dataclass(frozen=True)
class RetrievalOutcome:
    """run_candidate_retrieval の結果(design §2.4 — 試験assertと
    将来トレースの供給源)。"""

    intent_id: uuid.UUID
    version: int | None  # no-op時はNone
    layer1_pass_count: int
    pairs: list[CandidatePair] = field(default_factory=list)
    skip_reason: str | None = None


async def run_candidate_retrieval(
    conn: AsyncConnection, clock: Clock, intent_id: uuid.UUID
) -> RetrievalOutcome:
    """起点検証→Layer 2検索→UPSERT(design §2.3・§2.4)。"""
    loaded = await origin.load_origin(conn, clock, intent_id)
    if loaded.skip_reason is not None:
        return RetrievalOutcome(
            intent_id=intent_id,
            version=None,
            layer1_pass_count=0,
            skip_reason=loaded.skip_reason,
        )
    assert loaded.origin is not None  # skip_reason Noneならoriginがある
    org = loaded.origin
    rows, pass_count = await layer2.retrieve_topk(conn, org)
    pairs: list[CandidatePair] = []
    for c in rows:
        pairs.append(
            await candidates.upsert_candidate(
                conn,
                origin=org,
                candidate_id=c.intent_id,
                candidate_version=c.version,
                similarity=c.similarity,
            )
        )
    return RetrievalOutcome(
        intent_id=org.intent_id,
        version=org.version,
        layer1_pass_count=pass_count,
        pairs=pairs,
    )
