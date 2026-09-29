"""起点Intentの読み込みとLayer 1/2共通パラメータの組立(design §2.5・§2.6)。

06 §3「生成元にも対象にもならない」条件(非active・embedding NULL等)は
検索の前にここで落とす(no-op)。time_end・geo_radius_m の NULL は
保存時補完(intents/completion.py)への防御としてここでも補完する。
20歳判定はAPI側検証(users.age_years・JST暦日)と同じ基準(06 §2の二重防御)。
SQLはtext()生SQL・CAST(:x AS ...)形式(§2グローバル制約)。
M2 ws-4: structured_data を読み Layer 3 語彙計算用の soft_texts(降格除外済み)
を Origin へ持たせる(design §3.2)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.core.clock import Clock
from latch.intents.completion import DEFAULT_RADIUS_M, default_time_end
from latch.users.service import age_years
from latch.worker.matching.layer3 import soft_texts

# no-op理由(design §2.5・RetrievalOutcome.skip_reason の値域)
SKIP_NOT_FOUND = "origin_not_found"
SKIP_NOT_ACTIVE = "origin_not_active"
SKIP_EMBEDDING_NULL = "origin_embedding_null"
SKIP_GEO_MISSING = "origin_geo_missing"
SKIP_TIME_MISSING = "origin_time_missing"
SKIP_PARTICIPANTS = "origin_participants_range"

_SELECT_ORIGIN = text("""
    SELECT i.id, i.version, i.user_id, i.category_primary, i.alcohol_involved,
           i.budget_max, i.participants_min, i.participants_max,
           i.geo_radius_m, i.time_start, i.time_end, i.status, i.embedding,
           i.structured_data,
           ST_X(i.geo_center::geometry) AS lon,
           ST_Y(i.geo_center::geometry) AS lat,
           u.birth_date
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE i.id = CAST(:intent_id AS uuid)
""")


@dataclass(frozen=True)
class Origin:
    """評価の起点(design §2.5)。time_end・geo_radius_m は補完済み。"""

    intent_id: uuid.UUID
    version: int
    user_id: uuid.UUID
    category_primary: str
    alcohol_involved: bool
    budget_max: int | None
    participants_min: int
    participants_max: int
    geo_lon: float
    geo_lat: float
    geo_radius_m: int
    time_start: datetime
    time_end: datetime
    embedding: str  # '[0.1, ...]'(asyncpgはvector列を文字列で返す — design §5-2)
    soft_texts: tuple[str, ...]  # 降格除外済みのsoft_constraints文言(06 §4語彙計算用)
    user_ge_20: bool  # 評価時点のJST暦日で満20歳(users.age_yearsと同基準)
    evaluated_at: datetime  # load_origin時点のclock.now()(bind_paramsのnow)


@dataclass(frozen=True)
class OriginLoad:
    """load_originの戻り。originとskip_reasonは排他。"""

    origin: Origin | None
    skip_reason: str | None


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策 — 3度目の教訓)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _embedding_text(raw: object) -> str | None:
    """vector列の読み取り値をCAST(:x AS vector)へ渡せる文字列へ(design §5-2)。

    asyncpgはvector型を文字列'[...]'で返す想定。想定が外れて配列等で
    返った場合も文字列へ組立直す(設計不変 — design §5-2)。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return "[" + ",".join(repr(float(v)) for v in raw) + "]"


async def load_origin(
    conn: AsyncConnection, clock: Clock, intent_id: uuid.UUID
) -> OriginLoad:
    """起点を読みno-op判定(design §2.5)。行なしはskip理由付きの空結果。"""
    row = (
        (await conn.execute(_SELECT_ORIGIN, {"intent_id": intent_id}))
        .mappings()
        .first()
    )
    if row is None:
        return OriginLoad(origin=None, skip_reason=SKIP_NOT_FOUND)
    if row.status != "active":
        return OriginLoad(origin=None, skip_reason=SKIP_NOT_ACTIVE)
    embedding = _embedding_text(row.embedding)
    if embedding is None:
        return OriginLoad(origin=None, skip_reason=SKIP_EMBEDDING_NULL)
    if row.lon is None or row.lat is None:
        return OriginLoad(origin=None, skip_reason=SKIP_GEO_MISSING)
    if row.time_start is None:
        return OriginLoad(origin=None, skip_reason=SKIP_TIME_MISSING)
    if not (row.participants_min <= 2 <= row.participants_max):
        return OriginLoad(origin=None, skip_reason=SKIP_PARTICIPANTS)
    origin = Origin(
        intent_id=_coerce_uuid(row.id),
        version=row.version,
        user_id=_coerce_uuid(row.user_id),
        category_primary=row.category_primary,
        alcohol_involved=row.alcohol_involved,
        budget_max=row.budget_max,
        participants_min=row.participants_min,
        participants_max=row.participants_max,
        geo_lon=float(row.lon),
        geo_lat=float(row.lat),
        geo_radius_m=(
            row.geo_radius_m if row.geo_radius_m is not None else DEFAULT_RADIUS_M
        ),
        time_start=row.time_start,
        time_end=(
            row.time_end
            if row.time_end is not None
            else default_time_end(row.time_start)
        ),
        embedding=embedding,
        soft_texts=soft_texts(row.structured_data),
        user_ge_20=age_years(row.birth_date, clock.jst_date()) >= 20,
        evaluated_at=clock.now(),
    )
    return OriginLoad(origin=origin, skip_reason=None)


def bind_params(origin: Origin) -> dict:
    """Layer 1/2 共通の bind param 一式(design §2.6)。layer2は+embedding。

    NULLを渡しうる origin_budget はSQL側でCAST(:x AS int)する(§2制約)。
    now は飲酒年齢のJST変換(evaluated_at=tz-aware UTC)に使う。
    """
    return {
        "origin_user_id": origin.user_id,
        "origin_category": origin.category_primary,
        "origin_time_start": origin.time_start,
        "origin_time_end": origin.time_end,
        "origin_lon": origin.geo_lon,
        "origin_lat": origin.geo_lat,
        "origin_radius_m": origin.geo_radius_m,
        "origin_budget": origin.budget_max,
        "origin_alcohol": origin.alcohol_involved,
        "origin_user_ge_20": origin.user_ge_20,
        "now": origin.evaluated_at,
    }
