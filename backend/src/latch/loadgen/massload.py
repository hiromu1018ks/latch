"""総当たり母集合のDB直接投入(M4 ws-6・design §2.4案A)。

generatorの決定的シード(dict)を受け・users/intentsをバルクINSERT・
embedding列へ直書き(_stub_vector・正規化テキストはbuild_embedding_text
経由=workerと同一の決定規則)。Event発行なし(match_events無増加=観測を
汚さない)。API側レート制限は源流保護の本体(08 §5.4)で試験用に外せない
ためAPI経由を採らない。usersのauth_subjectはmassload-接頭辞で・既存データ
(integration試験のmasstest-・stagingハーネス)と混在しない。宛先は
load環境DB(127.0.0.1:25432)でtest-ci対象外(§2.2(c))。
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime

import asyncpg

from latch.intents.completion import nearest_expires_at
from latch.llm.gemini import GEMINI_EMBEDDING_MODEL
from latch.llm.stub import _stub_vector
from latch.testassets.vocab import LOCATIONS
from latch.worker.embedding_text import EmbeddingTextInput, build_embedding_text

# 決定的id生成のnamespace(同一seed id→同一UUID=段階間の累積投入がべき等)
MASSLOAD_NS = uuid.UUID("6f6f6f6f-6f6f-4f6f-8f6f-6f6f6f6f6f6f")
BATCH_SIZE = 1000
USER_PREFIX = "massload-"

# users(auth_subjectはmassload-接頭辞・consent_at=投入時刻でLayer 1対象に)
INSERT_USER = """
    INSERT INTO users (display_name, profile, birth_date, auth_provider,
                       auth_subject, consent_at, created_at, updated_at)
    VALUES ($1, $2::jsonb, $3, 'google', $4, $5, $5, $5)
    ON CONFLICT (auth_provider, auth_subject) DO NOTHING
"""
SELECT_USER_IDS = "SELECT id, auth_subject FROM users WHERE auth_provider = 'google'"
INSERT_INTENT = """
    INSERT INTO intents (id, user_id, category_primary, alcohol_involved, raw_text,
        structured_data, geo_center, geo_radius_m, budget_max, participants_min,
        participants_max, visibility, notification_level, status, version,
        time_start, time_end, expires_at, embedding, embedding_model,
        created_at, updated_at)
    VALUES ($1, $2, $3, $4, $5, $6::jsonb,
        ST_SetSRID(ST_MakePoint($7, $8), 4326)::geography,
        $9, $10, $11, $12, $13, $14, 'active', 1,
        $15, $16, $17, $18, $19, $20, $20)
    ON CONFLICT (id) DO NOTHING
"""


def FLAT_STRUCTURED(i: dict) -> dict:
    """YAML structured(API形式)→DB保存形式(mapping.pyと同型の平らな5キー
    +location_name・05 §2)。"""
    s = i["structured"]
    return {
        "category_secondary": s["category"].get("secondary"),
        "soft_constraints": [
            {"text": t, "downgraded_from_ng": False}
            for t in (s.get("soft_constraints") or [])
        ],
        "negative_constraints": [],
        "time_flexibility_minutes": None,
        "location_flexibility": None,
        "location_name": s["location"]["name"],
    }


def to_user_rows(seed: dict, now: datetime) -> list[tuple]:
    """YAML users→INSERT_USERパラメータ(決定的)。"""
    rows = []
    for u in seed["users"]:
        rows.append(
            (
                u["display_name"],
                json.dumps(u.get("profile") or {}, ensure_ascii=False),
                date.fromisoformat(str(u["birth_date"])[:10]),
                f"{USER_PREFIX}{u['id']}",
                now,
            )
        )
    return rows


def to_intent_rows(
    seed: dict, user_ids: dict[str, uuid.UUID], now: datetime
) -> list[tuple]:
    """YAML intents→INSERT_INTENTパラメータ(決定的・embedding直書き)。

    id=uuid5(MASSLOAD_NS, seed id)。expires_at=nearest_expires_at(time_start,
    now)(API保存と同一規則・active条件の「未来」を満たす)。embedding=
    _stub_vector(build_embedding_text(...))・model=GEMINI_EMBEDDING_MODEL
    (workerのスタブ系記録と同一値・design §2.4)。status='active'・version=1。
    """
    rows: list[tuple] = []
    for i in seed["intents"]:
        s = i["structured"]
        flat = FLAT_STRUCTURED(i)
        time_start = datetime.fromisoformat(str(s["time"]["start"]))
        time_end_raw = s["time"].get("end")
        time_end = datetime.fromisoformat(str(time_end_raw)) if time_end_raw else None
        text = build_embedding_text(
            EmbeddingTextInput(
                category_primary=s["category"]["primary"],
                structured_data=flat,
                participants_min=s["participants"]["min"],
                participants_max=s["participants"]["max"],
                time_start=time_start,
                time_end=time_end,
            )
        )
        vec = _stub_vector(text)
        vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
        lat, lon = LOCATIONS[s["location"]["name"]]
        rows.append(
            (
                uuid.uuid5(MASSLOAD_NS, i["id"]),
                user_ids[f"{USER_PREFIX}{i['user']}"],
                s["category"]["primary"],
                bool(s["alcohol_involved"]),
                i["raw_text"],
                json.dumps(flat, ensure_ascii=False),
                lon,
                lat,
                s["location"].get("radius_m"),
                s.get("budget", {}).get("max"),
                s["participants"]["min"],
                s["participants"]["max"],
                s["visibility"],
                s["notification_level"],
                time_start,
                time_end,
                nearest_expires_at(time_start, now),
                vec_literal,
                GEMINI_EMBEDDING_MODEL,
                now,
            )
        )
    return rows


async def load_population(*, dsn: str, seed: dict, now: datetime) -> dict:
    """母集合投入(決定的・べき等)。戻り値は投入・母集合の集計。

    INSERT前後のcount比較で実挿入数を計上する(executemanyはON CONFLICTの
    分も進行するため)。match_eventsへは一切書かない(INSERT_USER・
    INSERT_INTENTのみ)。
    """
    conn = await asyncpg.connect(dsn)
    try:
        before_users = await conn.fetchval(
            "SELECT count(*) FROM users WHERE auth_provider = 'google'"
            " AND auth_subject LIKE $1",
            f"{USER_PREFIX}%",
        )
        await conn.executemany(INSERT_USER, to_user_rows(seed, now))
        after_users = await conn.fetchval(
            "SELECT count(*) FROM users WHERE auth_provider = 'google'"
            " AND auth_subject LIKE $1",
            f"{USER_PREFIX}%",
        )
        ids = {
            row["auth_subject"]: row["id"] for row in await conn.fetch(SELECT_USER_IDS)
        }
        rows = to_intent_rows(seed, ids, now)
        before_intents = await conn.fetchval("SELECT count(*) FROM intents")
        for start in range(0, len(rows), BATCH_SIZE):
            await conn.executemany(INSERT_INTENT, rows[start : start + BATCH_SIZE])
        after_intents = await conn.fetchval("SELECT count(*) FROM intents")
        active_total = await conn.fetchval(
            "SELECT count(*) FROM intents WHERE status='active'"
        )
        events_total = await conn.fetchval("SELECT count(*) FROM match_events")
        return {
            "users_requested": len(seed["users"]),
            "users_inserted": after_users - before_users,
            "intents_requested": len(rows),
            "intents_inserted": after_intents - before_intents,
            "active_total": active_total,
            "match_events_total": events_total,
        }
    finally:
        await conn.close()
