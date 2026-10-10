"""移行: PostgreSQL dump → D1形式への変換(T12・計画 §3.1)。

変換規律(0001_core.sql と同一):
- uuid → TEXT(文字列表現そのまま)
- timestamptz → INTEGER(UTCマイクロ秒)
- date → TEXT(ISO)
- jsonb/uuid[] → TEXT(JSON文字列・表示順保持)
- boolean → INTEGER(0/1)
- geography(Point) → geo_lon/geo_lat REAL
- vector → embedding TEXT(JSON配列)
- numeric → REAL

認証状態(Redis)は session_snapshot 経由で refresh_tokens/
session_families/jwt_revocations/usage_counters へ移す(T12-3)。
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def to_micros(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError(f"naive datetime in transform: {value!r}")
    return int(value.timestamp() * 1_000_000)


def to_bool_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def to_json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    if isinstance(value, str):
        return value  # 既にJSON文字列(asyncpg jsonb)
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def to_array_json(value: Any) -> str | None:
    """uuid[]/text[] → JSON配列文字列(表示順保持)。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value  # 既にJSON
    if isinstance(value, (list, tuple)):
        return json.dumps([str(v) for v in value], separators=(",", ":"))
    raise TypeError(f"unsupported array: {type(value)}")


def transform_users(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        out.append(
            {
                "id": str(r["id"]),
                "display_name": r["display_name"],
                "profile": to_json_text(r.get("profile")) or "{}",
                "birth_date": (
                    r["birth_date"].isoformat()
                    if isinstance(r["birth_date"], date)
                    else str(r["birth_date"])
                ),
                "auth_provider": r["auth_provider"],
                "auth_subject": r["auth_subject"],
                "location_preferences": to_json_text(r.get("location_preferences")),
                "visibility_preferences": to_json_text(r.get("visibility_preferences")),
                "trust_score": (
                    float(r["trust_score"])
                    if isinstance(r.get("trust_score"), Decimal)
                    else r.get("trust_score")
                ),
                "consent_at": to_micros(r.get("consent_at")),
                "created_at": to_micros(r["created_at"]),
                "updated_at": to_micros(r["updated_at"]),
            }
        )
    return out


def transform_intents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        geo = r.get("geo_center")
        lon = lat = None
        if geo is not None:
            # geography WKB/ WKT 両対応の最小パース(PostGIS text形式想定)
            lon, lat = _parse_point(geo)
        embedding = r.get("embedding")
        if isinstance(embedding, list):
            embedding = json.dumps(embedding, separators=(",", ":"))
        out.append(
            {
                "id": str(r["id"]),
                "user_id": str(r["user_id"]),
                "category_primary": r["category_primary"],
                "category_secondary": r.get("category_secondary"),
                "alcohol_involved": to_bool_int(r["alcohol_involved"]),
                "raw_text": r["raw_text"],
                "structured_data": to_json_text(r.get("structured_data")) or "{}",
                "geo_lon": lon,
                "geo_lat": lat,
                "geo_radius_m": r.get("geo_radius_m"),
                "budget_max": r.get("budget_max"),
                "participants_min": r.get("participants_min", 2),
                "participants_max": r.get("participants_max", 2),
                "visibility": r.get("visibility", "hidden_until_match"),
                "notification_level": r.get("notification_level", "proposals_only"),
                "status": r["status"],
                "version": r["version"],
                "time_start": to_micros(r.get("time_start")),
                "time_end": to_micros(r.get("time_end")),
                "expires_at": to_micros(r.get("expires_at")),
                "embedding": embedding,
                "embedding_model": r.get("embedding_model"),
                "created_at": to_micros(r["created_at"]),
                "updated_at": to_micros(r["updated_at"]),
            }
        )
    return out


def _parse_point(geo: Any) -> tuple[float | None, float | None]:
    """geography(Point)表現 → (lon, lat)。"""
    if geo is None:
        return None, None
    if isinstance(geo, str):
        s = geo.strip()
        # hex WKB(asyncpg経由のgeometry既定表現)
        if len(s) >= 42 and all(c in "0123456789abcdefABCDEF" for c in s):
            return _wkb_point(bytes.fromhex(s))
        # EWKT: 'SRID=4326;POINT(lon lat)' / 'POINT(lon lat)'
        body = s.split(";")[-1].strip().removeprefix("POINT(").removesuffix(")")
        parts = body.split()
        if len(parts) >= 2:
            return float(parts[0]), float(parts[1])
        return None, None
    if isinstance(geo, (tuple, list)) and len(geo) >= 2:
        return float(geo[0]), float(geo[1])
    if isinstance(geo, dict) and "lon" in geo:
        return float(geo["lon"]), float(geo["lat"])
    if isinstance(geo, (bytes, bytearray, memoryview)):
        return _wkb_point(bytes(geo))
    if isinstance(geo, str) and geo.strip()[:2] in ("00", "01") and len(geo) >= 42:
        return _wkb_point(bytes.fromhex(geo.strip()))
    return None, None


def _wkb_point(wkb: bytes) -> tuple[float | None, float | None]:
    """WKB Point の最小パース(geography(Point)のexport用)。"""
    import struct

    if len(wkb) < 21:
        return None, None
    byte_order = "<" if wkb[0] == 1 else ">"
    geom_type = struct.unpack(f"{byte_order}I", wkb[1:5])[0]
    if geom_type & 0xFF != 1:  # POINT(下位8bit・上位はSRID等フラグ)
        return None, None
    offset = 5
    if geom_type & 0x20000000:  # SRID付き(PostGIS既定)
        offset += 4
    if len(wkb) < offset + 16:
        return None, None
    lon, lat = struct.unpack(f"{byte_order}dd", wkb[offset : offset + 16])
    return lon, lat


def transform_simple(rows, *, table: str) -> list[dict[str, Any]]:
    """時刻・bool・JSON・配列の変換のみの表。"""
    time_cols = _TIME_COLUMNS.get(table, ())
    bool_cols = _BOOL_COLUMNS.get(table, ())
    json_cols = _JSON_COLUMNS.get(table, ())
    array_cols = _ARRAY_COLUMNS.get(table, ())
    out = []
    for r in rows:
        item = {}
        for key, value in r.items():
            if key in time_cols:
                item[key] = to_micros(value)
            elif key in bool_cols:
                item[key] = to_bool_int(value)
            elif key in json_cols:
                item[key] = to_json_text(value)
            elif key in array_cols:
                item[key] = to_array_json(value)
            elif isinstance(value, Decimal):
                item[key] = float(value)
            elif isinstance(value, uuid_mod.UUID):
                item[key] = str(value)
            elif isinstance(value, datetime):
                item[key] = to_micros(value)
            elif isinstance(value, date):
                item[key] = value.isoformat()
            else:
                item[key] = value
        out.append(item)
    return out


_TIME_COLUMNS = {
    "latches": ("response_deadline", "expires_at", "created_at", "completed_at"),
    "latch_status_events": ("created_at",),
    "match_candidates": ("prev_evaluated_at", "created_at", "updated_at"),
    "group_candidates": ("created_at", "updated_at"),
    "calibration_records": (
        "anonymized_at",
        "created_at",
        "updated_at",
    ),
    "notifications": ("read_at", "created_at"),
    "messages": ("created_at",),
    "blocks": ("created_at",),
    "reports": ("created_at",),
    "match_events": ("created_at", "processed_at"),
    "users": ("consent_at",),
}
_BOOL_COLUMNS = {
    "calibration_records": ("matched", "actual_attended", "cancelled_after"),
}
_JSON_COLUMNS = {
    "latches": ("proposal", "responses"),
    "group_candidates": ("member_scores",),
    "calibration_records": (
        "prediction",
        "proposal_snapshot",
        "actual_responses",
    ),
    "notifications": ("payload",),
    "match_candidates": ("jev_result",),
    "match_events": ("payload",),
}
_ARRAY_COLUMNS = {
    # left_user_idsはD-08(v0.6)追加のuuid[](D1ではJSON配列TEXT)
    "latches": ("intent_ids", "left_user_ids"),
    "group_candidates": ("intent_ids",),
    "calibration_records": ("intent_ids",),
}


def transform_geofeatures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """geofeatures: geom(EWKT) → lon/lat(D1列構成へ射影)。"""
    out = []
    for r in rows:
        lon, lat = _parse_point(r.get("geom"))
        out.append(
            {
                "id": str(r["id"]),
                "source": r["source"],
                "kind": r.get("kind"),
                "name": r["name"],
                "city_name": r.get("city_name"),
                "pref_name": r.get("pref_name"),
                "lon": lon,
                "lat": lat,
            }
        )
    return out


def transform_dump(
    dump: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """PostgreSQL dump → D1形式。"""
    out: dict[str, list[dict[str, Any]]] = {}
    if "users" in dump:
        out["users"] = transform_users(dump["users"])
    if "intents" in dump:
        out["intents"] = transform_intents(dump["intents"])
    if "geofeatures" in dump:
        out["geofeatures"] = transform_geofeatures(dump["geofeatures"])
    for table in (
        "latches",
        "latch_status_events",
        "match_candidates",
        "group_candidates",
        "calibration_records",
        "notifications",
        "messages",
        "blocks",
        "reports",
        "match_events",
    ):
        if table in dump:
            out[table] = transform_simple(dump[table], table=table)
    return out


# =============================================================================
# 認証状態(Redis)の移行(T12-3)
# =============================================================================


def transform_session_snapshot(
    snapshot: dict[str, Any], *, now_us: int
) -> dict[str, list[dict[str, Any]]]:
    """Redis session状態 → D1表。

    snapshot = {
      refresh_tokens: [{token_hash, sid, provider, subject, status,
                        rotated_to, created_at, expires_at(us)}],
      families: [{sid, provider, subject, status, created_at(us)}],
      revoked_jtis: [{jti, expires_at(us)}],
      counters: [{key, value, expires_at(us)}],
    }
    """
    return {
        "session_families": [
            {
                "sid": f["sid"],
                "provider": f["provider"],
                "subject": f["subject"],
                "status": f.get("status", "active"),
                "revoked_at": f.get("revoked_at"),
                "created_at": f["created_at"],
            }
            for f in snapshot.get("families", [])
        ],
        "refresh_tokens": [
            {
                "token_hash": t["token_hash"],
                "sid": t["sid"],
                "provider": t["provider"],
                "subject": t["subject"],
                "status": t.get("status", "active"),
                "rotated_to": t.get("rotated_to"),
                "created_at": t["created_at"],
                "expires_at": t["expires_at"],
            }
            for t in snapshot.get("refresh_tokens", [])
        ],
        "jwt_revocations": [
            {
                "jti": j["jti"],
                "expires_at": j["expires_at"],
                "created_at": j.get("created_at", now_us),
            }
            for j in snapshot.get("revoked_jtis", [])
        ],
        "usage_counters": [
            {
                "key": c["key"],
                "value": c["value"],
                "window_start": c.get("window_start", now_us),
                "expires_at": c["expires_at"],
            }
            for c in snapshot.get("counters", [])
        ],
    }
