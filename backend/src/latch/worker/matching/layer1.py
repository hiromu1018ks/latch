"""Layer 1 Hard Filter(06 §2・design §2.1案A・§2.6確定表)。

SQL+PostGISのみ(AI不使用)。全条件を WHERE 句の確定文字列に集約し、
Layer 2 が同一文字列を使う(層順を1クエリで体現・試験と本番のWHERE乖離
なし — design §2.1)。pass/fail判定はDBへ一任しPythonで再判定しない。
visibilityは判定対象外(06 §2)。flexibilityはMVPで常にnullのため実装
しない(design §1.4-5)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.worker.matching.origin import Origin, bind_params

# 06 §2: ペア予算 min(budget_max_a, budget_max_b)(NULLは無視)が
# 500円未満の場合のみfail(500・比較演算は06 §2の確定値そのまま)
PAIR_BUDGET_MIN_YEN = 500

# ペア予算式(NULLは無視=双方制約なしはfailしない — 06 §2)
_BUDGET_PAIR = """(
        CASE
            WHEN CAST(:origin_budget AS int) IS NULL AND i.budget_max IS NULL THEN NULL
            WHEN CAST(:origin_budget AS int) IS NULL THEN i.budget_max
            WHEN i.budget_max IS NULL THEN CAST(:origin_budget AS int)
            ELSE LEAST(CAST(:origin_budget AS int), i.budget_max)
        END
    )"""

# Layer 1 の全条件(06 §2・design §2.6)。layer2 が同一文字列を使用する
LAYER1_WHERE = f"""
    i.status = 'active'
    AND i.embedding IS NOT NULL
    AND i.user_id <> CAST(:origin_user_id AS uuid)
    AND i.category_primary = :origin_category
    AND i.time_start < :origin_time_end
    AND :origin_time_start < COALESCE(i.time_end, i.time_start + interval '3 hours')
    AND i.geo_center IS NOT NULL
    AND ST_DWithin(
        i.geo_center,
        ST_SetSRID(
            ST_MakePoint(CAST(:origin_lon AS float8), CAST(:origin_lat AS float8)),
            4326)::geography,
        CAST(
            CAST(:origin_radius_m AS int) + COALESCE(i.geo_radius_m, 1000)
            AS double precision))
    AND ({_BUDGET_PAIR} IS NULL
         OR {_BUDGET_PAIR} >= {PAIR_BUDGET_MIN_YEN})
    AND i.participants_min <= 2
    AND i.participants_max >= 2
    AND NOT EXISTS (
        SELECT 1 FROM blocks b
        WHERE (b.blocker_id = CAST(:origin_user_id AS uuid)
               AND b.blocked_id = i.user_id)
           OR (b.blocker_id = i.user_id
               AND b.blocked_id = CAST(:origin_user_id AS uuid)))
    AND (
        (NOT (:origin_alcohol OR i.alcohol_involved))
        OR (
            :origin_user_ge_20
            AND EXTRACT(YEAR FROM AGE(
                (CAST(:now AS timestamptz) AT TIME ZONE 'Asia/Tokyo'),
                CAST(u.birth_date AS timestamp))) >= 20
        )
    )
"""

_SELECT_HARD = text(f"""
    SELECT i.id, i.version, i.user_id
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE {LAYER1_WHERE}
""")


@dataclass(frozen=True)
class HardCandidate:
    """Layer 1 通過対象(02#9単体試験・将来トレース用 — design §3.1)。"""

    intent_id: uuid.UUID
    version: int
    user_id: uuid.UUID


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策 — 3度目の教訓)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def hard_filter_candidates(
    conn: AsyncConnection, origin: Origin
) -> list[HardCandidate]:
    """Layer 1 通過集合(ORDER BYなし・LIMITなし。design §3.1)。"""
    rows = (await conn.execute(_SELECT_HARD, bind_params(origin))).all()
    return [
        HardCandidate(
            intent_id=_coerce_uuid(r[0]), version=r[1], user_id=_coerce_uuid(r[2])
        )
        for r in rows
    ]
