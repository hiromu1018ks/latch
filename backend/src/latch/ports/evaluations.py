"""外部呼出しを伴う仕事の処理権・試行記録のport(計画§4・T5)。

LLM送信前にclaimし、fencing token・Intent版を確認した時だけ結果を
確定する。外部API待ちの間DB transactionを保持しない。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from latch.domain.types import ClaimResult, EvaluationKey


@runtime_checkable
class EvaluationGateway(Protocol):
    """I1(処理前の確保)と§4(全試行の記録)の境界。"""

    async def claim_evaluation(
        self, key: EvaluationKey, owner: str, now
    ) -> ClaimResult: ...

    async def complete_evaluation(
        self, key: EvaluationKey, fencing_token: int, result: dict[str, Any], now
    ) -> bool:
        """結果確定。fencing token・Intent版が古い場合はFalse(破棄)。"""
        ...

    async def record_attempt(self, attempt: dict[str, Any]) -> None:
        """LLM試行1件の記録(送信前prepared→応答後usage更新・本文無し)。"""
        ...
