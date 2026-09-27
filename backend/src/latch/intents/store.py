"""intents永続化(design §2.9〜§2.10・§2.5の競合制御)。

text()生SQLのみ(ws-1流儀の継続 — design §2.9)。SQL一式をこのモジュールへ
集約し、トランザクション境界はserviceが持つ(fetch_user_row以外はconnを
受け取る)。fetch_user_rowはトランザクション不要のためengine直
(uq_users_auth_provider_subject索引へのSELECT 1本 — design §2.6)。
時刻列はClock由来の明示値。geo_centerはST_SetSRID(ST_MakePoint(lon,lat),
4326)のgeography(座標はserviceがジオコーディング結果を渡す)。実SQLの試験
はintegration(test-ci)。unitはスタブstoreで置き換える(design §4.1)。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

if TYPE_CHECKING:
    from latch.intents.mapping import ResolvedColumns


@dataclass(frozen=True)
class UserRow:
    """fetch_user_rowの戻り(年齢検証に必要な最小列 — design §2.6)。"""

    id: uuid.UUID
    birth_date: date


@dataclass(frozen=True)
class IntentRow:
    """intents表の保存行(geo_center座標・embeddingは含めない — design §2.10)。"""

    id: uuid.UUID
    user_id: uuid.UUID
    category_primary: str
    alcohol_involved: bool
    raw_text: str
    structured_data: dict
    geo_radius_m: int | None
    budget_max: int | None
    participants_min: int
    participants_max: int
    visibility: str
    notification_level: str
    status: str
    version: int
    time_start: datetime | None
    time_end: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _coerce_uuid(value: object) -> uuid.UUID:
    """asyncpgはuuid列をUUIDインスタンスで返す(M0 ws-3と同じ落ち穴対策)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


_ROW_COLS = (
    "id, user_id, category_primary, alcohol_involved, raw_text, structured_data, "
    "geo_radius_m, budget_max, participants_min, participants_max, visibility, "
    "notification_level, status, version, time_start, time_end, expires_at, "
    "created_at, updated_at"
)

_GEO_CENTER_EXPR = (
    # :geo_lon::float8 の形は SQLAlchemy text() の bind param 正規表現が
    # ':name' 直後の ':' を認識せずリテラル落ちするため CAST 構文を使う
    # (test_store_sql.py が compile 結果で強制する)
    "CASE WHEN CAST(:geo_lon AS float8) IS NULL THEN NULL "
    "ELSE ST_SetSRID(ST_MakePoint(:geo_lon, :geo_lat), 4326)::geography END"
)

_SELECT_BY_ID = text(f"SELECT {_ROW_COLS} FROM intents WHERE id = :intent_id")

_SELECT_FOR_UPDATE = text(
    f"SELECT {_ROW_COLS} FROM intents WHERE id = :intent_id FOR UPDATE"
)

_SELECT_USER = text(
    "SELECT id, birth_date FROM users "
    "WHERE auth_provider = :provider AND auth_subject = :subject"
)

_INSERT = text(f"""
    INSERT INTO intents (
        user_id, category_primary, alcohol_involved, raw_text, structured_data,
        geo_center, geo_radius_m, budget_max, participants_min, participants_max,
        visibility, notification_level, status, version,
        time_start, time_end, expires_at, created_at, updated_at
    ) VALUES (
        :user_id, :category_primary, :alcohol_involved, :raw_text,
        CAST(:structured_data AS jsonb),
        {_GEO_CENTER_EXPR},
        :geo_radius_m, :budget_max, :participants_min, :participants_max,
        :visibility, :notification_level, :status, :version,
        :time_start, :time_end, :expires_at, :created_at, :updated_at
    )
    RETURNING id
""")

_UPDATE = text(f"""
    UPDATE intents SET
        category_primary = :category_primary,
        alcohol_involved = :alcohol_involved,
        raw_text = :raw_text,
        structured_data = CAST(:structured_data AS jsonb),
        geo_center = {_GEO_CENTER_EXPR},
        geo_radius_m = :geo_radius_m,
        budget_max = :budget_max,
        participants_min = :participants_min,
        participants_max = :participants_max,
        visibility = :visibility,
        notification_level = :notification_level,
        status = :status,
        version = :version,
        time_start = :time_start,
        time_end = :time_end,
        expires_at = :expires_at,
        updated_at = :updated_at
    WHERE id = :intent_id AND status = :expected_status
""")

_UPDATE_STATUS = text("""
    UPDATE intents
    SET status = :status, version = :version, updated_at = :updated_at
    WHERE id = :intent_id AND status = :expected_status
""")


def _list_sql(*, with_status: bool, with_before: bool):
    """キーセット一覧(design §2.10)。ROW比較 (created_at, id) < (:ts, :id)。"""
    clauses = ["user_id = :user_id"]
    if with_status:
        clauses.append("status = :status")
    if with_before:
        clauses.append("(created_at, id) < (:before_ts, :before_id)")
    return text(
        f"SELECT {_ROW_COLS} FROM intents WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at DESC, id DESC LIMIT :limit"
    )


_LIST = {
    (False, False): _list_sql(with_status=False, with_before=False),
    (True, False): _list_sql(with_status=True, with_before=False),
    (False, True): _list_sql(with_status=False, with_before=True),
    (True, True): _list_sql(with_status=True, with_before=True),
}


def _row_from(mapping) -> IntentRow:
    structured = mapping.structured_data
    if isinstance(structured, str):
        structured = json.loads(structured)  # asyncpgのjsonbがstrで返る場合(users先例)
    return IntentRow(
        id=_coerce_uuid(mapping.id),
        user_id=_coerce_uuid(mapping.user_id),
        category_primary=mapping.category_primary,
        alcohol_involved=mapping.alcohol_involved,
        raw_text=mapping.raw_text,
        structured_data=structured,
        geo_radius_m=mapping.geo_radius_m,
        budget_max=mapping.budget_max,
        participants_min=mapping.participants_min,
        participants_max=mapping.participants_max,
        visibility=mapping.visibility,
        notification_level=mapping.notification_level,
        status=mapping.status,
        version=mapping.version,
        time_start=mapping.time_start,
        time_end=mapping.time_end,
        expires_at=mapping.expires_at,
        created_at=mapping.created_at,
        updated_at=mapping.updated_at,
    )


def _col_params(cols: ResolvedColumns) -> dict:
    """INSERT/UPDATE共通の保存列パラメータ(structured_dataはJSON文字列で渡す)。"""
    return {
        "raw_text": cols.raw_text,
        "category_primary": cols.category_primary,
        "alcohol_involved": cols.alcohol_involved,
        "structured_data": json.dumps(cols.structured_data),
        "geo_lon": cols.geo_lon,
        "geo_lat": cols.geo_lat,
        "geo_radius_m": cols.geo_radius_m,
        "budget_max": cols.budget_max,
        "participants_min": cols.participants_min,
        "participants_max": cols.participants_max,
        "visibility": cols.visibility,
        "notification_level": cols.notification_level,
        "time_start": cols.time_start,
        "time_end": cols.time_end,
        "expires_at": cols.expires_at,
    }


class IntentStore:
    """intents表へのSQL実行(conn注入・トランザクションなし — design §2.10)。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def fetch_user_row(self, provider: str, subject: str) -> UserRow | None:
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        _SELECT_USER, {"provider": provider, "subject": subject}
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return UserRow(id=_coerce_uuid(row.id), birth_date=row.birth_date)

    async def fetch(
        self, conn: AsyncConnection, intent_id: uuid.UUID
    ) -> IntentRow | None:
        row = (
            (await conn.execute(_SELECT_BY_ID, {"intent_id": intent_id}))
            .mappings()
            .first()
        )
        return None if row is None else _row_from(row)

    async def fetch_for_update(
        self, conn: AsyncConnection, intent_id: uuid.UUID
    ) -> IntentRow | None:
        row = (
            (await conn.execute(_SELECT_FOR_UPDATE, {"intent_id": intent_id}))
            .mappings()
            .first()
        )
        return None if row is None else _row_from(row)

    async def insert(
        self,
        conn: AsyncConnection,
        cols: ResolvedColumns,
        *,
        user_id: uuid.UUID,
        status: str,
        now: datetime,
    ) -> uuid.UUID:
        row_id = await conn.scalar(
            _INSERT,
            {
                **_col_params(cols),
                "user_id": user_id,
                "status": status,
                "version": cols.version,
                "created_at": now,
                "updated_at": now,
            },
        )
        return _coerce_uuid(row_id)

    async def update(
        self,
        conn: AsyncConnection,
        intent_id: uuid.UUID,
        cols: ResolvedColumns,
        *,
        status: str,
        version: int,
        now: datetime,
        expected_status: str,
    ) -> int:
        """全置換UPDATE。戻りは影響行数(0=競合でstatusが変わっていた)。"""
        result = await conn.execute(
            _UPDATE,
            {
                **_col_params(cols),
                "intent_id": intent_id,
                "status": status,
                "version": version,
                "updated_at": now,
                "expected_status": expected_status,
            },
        )
        return result.rowcount

    async def update_status(
        self,
        conn: AsyncConnection,
        intent_id: uuid.UUID,
        *,
        status: str,
        version: int,
        now: datetime,
        expected_status: str,
    ) -> int:
        """pause/resume/delete(cancelled)の条件付きUPDATE(pause/resume用)。"""
        result = await conn.execute(
            _UPDATE_STATUS,
            {
                "intent_id": intent_id,
                "status": status,
                "version": version,
                "updated_at": now,
                "expected_status": expected_status,
            },
        )
        return result.rowcount

    async def list_page(
        self,
        conn: AsyncConnection,
        user_id: uuid.UUID,
        *,
        status: str | None,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[IntentRow]:
        """created_at降順・id降順(design §2.10)。limit+1件を呼び出し側が取る。"""
        sql = _LIST[(status is not None, before is not None)]
        params: dict = {"user_id": user_id, "limit": limit}
        if status is not None:
            params["status"] = status
        if before is not None:
            params["before_ts"], params["before_id"] = before
        rows = (await conn.execute(sql, params)).mappings().all()
        return [_row_from(r) for r in rows]
