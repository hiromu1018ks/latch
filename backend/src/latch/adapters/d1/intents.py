"""D1版・intents読取(保存は atomic.apply_intent)。"""

from __future__ import annotations

import json

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import from_micros


class D1IntentsReader:
    def __init__(self, db: LocalD1Database) -> None:
        self.db = db

    async def get(self, intent_id: str) -> dict | None:
        row = (
            await self.db.prepare("SELECT * FROM intents WHERE id = ?")
            .bind(intent_id)
            .first()
        )
        return self._to_api(row) if row else None

    async def list_for_user(
        self, user_id: str, *, status: str | None = None, limit: int = 20
    ) -> dict:
        if status:
            rows = (
                await self.db.prepare(
                    "SELECT * FROM intents WHERE user_id = ? AND status = ?"
                    " ORDER BY created_at ASC, id ASC LIMIT ?"
                )
                .bind(user_id, status, limit)
                .all()
            )
        else:
            rows = (
                await self.db.prepare(
                    "SELECT * FROM intents WHERE user_id = ?"
                    " ORDER BY created_at ASC, id ASC LIMIT ?"
                )
                .bind(user_id, limit)
                .all()
            )
        return {"items": [self._to_api(r) for r in rows], "next_cursor": None}

    @staticmethod
    def _to_api(row) -> dict:
        structured = json.loads(row["structured_data"])
        return {
            "id": row["id"],
            "status": row["status"],
            "version": row["version"],
            "raw_text": row["raw_text"],
            "structured_intent": structured,
            "visibility": row["visibility"],
            "notification_level": row["notification_level"],
            "expires_at": (
                from_micros(row["expires_at"]).isoformat()
                if row["expires_at"]
                else None
            ),
            "created_at": from_micros(row["created_at"]).isoformat(),
            "updated_at": from_micros(row["updated_at"]).isoformat(),
        }
