"""D1版・latches読取(一覧・詳細・回答API応答の構成)。

書込みは atomic.apply_response を使う(ここでは読取のみ)。
"""

from __future__ import annotations

from typing import Any

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import from_micros

_RESPONSE_ACCEPTING = ("proposed", "partial_accept")


class D1LatchesReader:
    """latches一覧・詳細のD1読取。"""

    def __init__(self, db: LocalD1Database) -> None:
        self.db = db

    async def get_latch(self, latch_id: str, *, viewer_user_id: str) -> dict | None:
        row = (
            await self.db.prepare("SELECT * FROM latches WHERE id = ?")
            .bind(latch_id)
            .first()
        )
        if row is None:
            return None
        import json

        intent_ids = json.loads(row["intent_ids"])
        if viewer_user_id not in await self._owners(intent_ids):
            return None  # 呼び出し側で403へ分類
        return self._summary(row, viewer_user_id, detailed=True)

    async def list_latches(self, user_id: str, *, limit: int = 20, before=None) -> dict:
        rows = (
            await self.db.prepare(
                "SELECT l.* FROM latches l"
                " JOIN intents i ON i.user_id = ?"
                " WHERE l.status != 'candidate'"
                " AND EXISTS (SELECT 1 FROM json_each(l.intent_ids) je"
                "             WHERE je.value = i.id)"
                " ORDER BY l.created_at ASC, l.id ASC LIMIT ?"
            )
            .bind(user_id, limit + 1)
            .all()
        )
        items = [self._summary(r, user_id) for r in rows[:limit]]
        return {"items": items, "next_cursor": None}

    async def _owners(self, intent_ids: list[str]) -> list[str]:
        from latch.adapters.d1.database import canonical_json

        rows = (
            await self.db.prepare(
                "SELECT user_id FROM intents WHERE id IN ("
                " SELECT value FROM json_each(?))"
            )
            .bind(canonical_json(intent_ids))
            .all()
        )
        return [r["user_id"] for r in rows]

    def _summary(self, row, user_id: str, *, detailed: bool = False) -> dict:
        import json

        intent_ids = json.loads(row["intent_ids"])
        responses = json.loads(row["responses"])
        mine = next((r for r in responses if r.get("user_id") == user_id), None)
        total = len(intent_ids)
        yes = sum(1 for r in responses if r.get("response") == "yes")
        out: dict[str, Any] = {
            "id": row["id"],
            "status": row["status"],
            "is_group": total > 2,
            "proposal": json.loads(row["proposal"]),
            "response_deadline": from_micros(row["response_deadline"]).isoformat(),
            "expires_at": from_micros(row["expires_at"]).isoformat(),
            "created_at": from_micros(row["created_at"]).isoformat(),
            "my_response": mine["response"] if mine else None,
            "remaining_responses": max(0, total - yes),
        }
        if detailed:
            out["group_candidate_id"] = row["group_candidate_id"]
            # 成立後のみ参加者開示(03 §5・公開範囲維持)
            if row["status"] in ("matched", "completed"):
                out["participants"] = None  # T4で表示統合。構造のみ担保
                out["time_summary"] = None
                out["area_name"] = None
            else:
                out["participants"] = None
                out["time_summary"] = None
                out["area_name"] = None
        return out
