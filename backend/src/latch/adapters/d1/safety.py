"""D1版・safety読取(blocks一覧・block判定)と書込みは atomic.apply_safety。"""

from __future__ import annotations

import json

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import from_micros


def _loads(text: str | None, default):
    if text is None:
        return default
    return json.loads(text)


class D1SafetyReader:
    def __init__(self, db: LocalD1Database) -> None:
        self.db = db

    async def blocked_between(self, user_a: str, user_b: str) -> bool:
        """双方向block判定(D1直読み・cacheは別layer)。"""
        row = (
            await self.db.prepare(
                "SELECT 1 AS x FROM blocks WHERE (blocker_id = ? AND blocked_id = ?)"
                " OR (blocker_id = ? AND blocked_id = ?) LIMIT 1"
            )
            .bind(user_a, user_b, user_b, user_a)
            .first()
        )
        return row is not None

    async def list_blocks(self, user_id: str, *, limit: int = 20) -> dict:
        rows = (
            await self.db.prepare(
                "SELECT b.blocked_id, b.created_at, u.display_name"
                " FROM blocks b JOIN users u ON u.id = b.blocked_id"
                " WHERE b.blocker_id = ?"
                " ORDER BY b.created_at DESC, b.id DESC LIMIT ?"
            )
            .bind(user_id, limit)
            .all()
        )
        items = [
            {
                "blocked_id": r["blocked_id"],
                "display_name": r["display_name"],
                "created_at": from_micros(r["created_at"]).isoformat(),
            }
            for r in rows
        ]
        return {"items": items, "next_cursor": None}

    async def chat_sendable(self, latch_id: str, sender_id: str) -> bool:
        """matched latch参加者・非block・参加取り消し未済の判定(chat 409分岐用)。"""
        row = (
            await self.db.prepare(
                "SELECT intent_ids, status, left_user_ids FROM latches WHERE id = ?"
            )
            .bind(latch_id)
            .first()
        )
        if row is None or row["status"] != "matched":
            return False
        if sender_id in _loads(row["left_user_ids"], []):
            return False  # 参加取り消し済み(05 §5 leave・D-08)
        owner = (
            await self.db.prepare(
                "SELECT user_id FROM intents WHERE id IN ("
                " SELECT value FROM json_each(?)) AND user_id = ?"
            )
            .bind(row["intent_ids"], sender_id)
            .first()
        )
        if owner is None:
            return False
        others = (
            await self.db.prepare(
                "SELECT DISTINCT user_id FROM intents WHERE id IN ("
                " SELECT value FROM json_each(?)) AND user_id != ?"
            )
            .bind(row["intent_ids"], sender_id)
            .all()
        )
        for o in others:
            if await self.blocked_between(sender_id, o["user_id"]):
                return False
        return True
