"""Layer 2 Candidate Retrieval(06 §3・design §2.1案A・§2.2)。

Layer 1 条件込みの単一SQLで cosine 距離上位 K_v=50 を取得する。ORDER BY
の第2キー i.id(同点はintent_id昇順 — D-24・10 §4.6)を付けたためHNSW
Indexスキャンは使われず逐次ソートになる(決定性優先 — design §2.2。
負荷試験(10 §4.2)でp95 1秒を割った場合のみiterative scanで再設計)。
retrieval_score には cosine類似度(1 − 距離)を記録する(design §2.6)。
起点embeddingは文字列表現をそのまま再キャストして渡す(asyncpgがvector
列を文字列で返すため — design §2.6・§5-2)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.worker.matching.layer1 import LAYER1_WHERE
from latch.worker.matching.origin import Origin, bind_params

# D-24(06 §8・01 §11): Vector検索のK上限=次層へ渡す出力数の上限
K_VECTORS = 50

_SELECT_TOPK = text(f"""
    SELECT i.id,
           i.version,
           1 - (i.embedding <=> CAST(:origin_embedding AS vector)) AS similarity,
           COUNT(*) OVER () AS pass_count
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE {LAYER1_WHERE}
    ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC, i.id ASC
    LIMIT {K_VECTORS}
""")


@dataclass(frozen=True)
class RetrievedCandidate:
    """Layer 2 通過対象(cosine類似度つき)。"""

    intent_id: uuid.UUID
    version: int
    similarity: float


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策 — 3度目の教訓)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def retrieve_topk(
    conn: AsyncConnection, origin: Origin
) -> tuple[list[RetrievedCandidate], int]:
    """Layer 1 通過のうち距離上位K_v件と、通過件数(LIMIT前 — §9固定値)。

    pass_count は COUNT(*) OVER()(window関数はLIMIT前に評価される)で
    同一SQL内で得る(design §2.1の中間表現を持たない方針を崩さない)。
    """
    params = {**bind_params(origin), "origin_embedding": origin.embedding}
    rows = (await conn.execute(_SELECT_TOPK, params)).all()
    candidates = [
        RetrievedCandidate(
            intent_id=_coerce_uuid(r[0]), version=r[1], similarity=float(r[2])
        )
        for r in rows
    ]
    pass_count = int(rows[0][3]) if rows else 0
    return candidates, pass_count
