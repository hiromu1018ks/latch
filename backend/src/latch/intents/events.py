"""MatchEvent発行(outbox — design §2.2A)。

match_eventsへのINSERTがM1時点の発行の実体(Pub/SubリレーはM2-1)。Intent
保存と同一トランザクションで呼ぶ(原子性 — 保存が成功した行に必ずEvent行が
伴う)。event_typeの文字列はembedding_completed(05 §2唯一の確定文字列)の
過去分詞形に揃えた(design §2.2・§6-4。DB永続値のためM2設計が引き継ぐ)。
M3 ws-6: 同一3点組の再発行はON CONFLICT DO NOTHINGで挿入しない
(cancelled再削除の冪等・design §2.2)。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

EVENT_CREATED = "created"
EVENT_UPDATED = "updated"
EVENT_DELETED = "deleted"
EVENT_EXPIRED = "expired"  # 発行経路はM3-3(expiry_sweeper)
EVENT_SCHEDULED = "scheduled"  # 発行経路はM2以降(§2.11)
EVENT_EMBEDDING_COMPLETED = "embedding_completed"  # 発行経路はM2 ws-2(Embedding Worker)

_INSERT_EVENT = text("""
    INSERT INTO match_events
        (event_type, source_intent_id, payload, status, created_at)
    VALUES
        (:event_type, :source_intent_id, CAST(:payload AS jsonb), 'pending',
         :created_at)
    ON CONFLICT DO NOTHING
""")


async def insert_match_event(
    conn: AsyncConnection,
    *,
    event_type: str,
    intent_id: uuid.UUID,
    version: int,
    now: datetime,
) -> None:
    """payloadは{"version": N}のみ(06 §9。WorkerはIntentを再読込して使う)。"""
    await conn.execute(
        _INSERT_EVENT,
        {
            "event_type": event_type,
            "source_intent_id": intent_id,
            "payload": json.dumps({"version": version}),
            "created_at": now,
        },
    )
