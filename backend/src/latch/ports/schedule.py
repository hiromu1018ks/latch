"""再評価スケジュールのport(計画§5.4・T8)。

due時刻と変更世代(generation)で再評価・期限処理を駆動する。
抑制中の新しい版を消さない(旧Redis抑制の欠陥対策)。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ScheduleRepository(Protocol):
    """T8の request_evaluation / claim_due / complete_generation。"""

    async def request_evaluation(
        self, intent_id: str, revision: int, reason: str, due_at
    ) -> int:
        """generationを進めて要求を記録(戻り値=新しいgeneration)。"""
        ...

    async def claim_due(self, now, limit: int, cursor: str | None) -> list[Any]:
        """due到達の仕事を上限付きbatch・cursorで公平に取得。"""
        ...

    async def complete_generation(
        self, claim: Any, result: dict[str, Any], next_due_at
    ) -> bool:
        """完了時の条件付き更新(claim後の更新を消さない)。"""
        ...
