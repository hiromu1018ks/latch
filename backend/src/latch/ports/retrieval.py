"""正確な候補検索のport(計画§3.3・T4)。

snapshotは検索元と候補のID・版・条件・Embeddingを固定した短期の
検索集合。結果の50件への切り詰めは全hard条件・距離/cosineの後。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from latch.domain.types import IntentSnapshot


@runtime_checkable
class RetrievalRepository(Protocol):
    """T4のcreate_retrieval_snapshot / retrieve_exact。"""

    async def create_retrieval_snapshot(
        self, origin_id: str, expected_revision: int, now
    ) -> str:
        """snapshot_idを返す(候補の版・条件を原子的に固定)。"""
        ...

    async def retrieve_exact(
        self, origin: IntentSnapshot, snapshot_id: str
    ) -> dict[str, Any]:
        """候補ID・版・similarity・正確なpass_countを返す(RetrievalResult)。"""
        ...
