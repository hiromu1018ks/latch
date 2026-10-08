"""原子的な業務commandのport(計画§3.2・T2)。

D1のbatch()は準備済みSQL群の原子的実行を保証するが、途中SELECTで
Pythonが分岐するinteractive transactionではない。このためDB操作の
境界は業務command単位で提供する(engine.begin()の模倣を作らない)。

PostgreSQL版の実装は既存service(latches/service.py等)が、Workers版は
adapters/d1/atomic.py が担う(T3)。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from latch.domain.types import CommandResult


@runtime_checkable
class AtomicRepository(Protocol):
    """§3.2の四つのapply_*。戻り値はCommandResult。

    command_id は内部再配送の識別(既存APIの2度目の回答409を成功へ
    変えるものではない)。expected_revision は楽観的排他。
    """

    async def apply_intent(
        self,
        command_id: str,
        expected_revision: int | None,
        mutation: dict[str, Any],
        now,  # datetime — domain.time.to_utc 済みであること
    ) -> CommandResult: ...

    async def apply_response(
        self,
        command_id: str,
        expected_revision: int | None,
        response: dict[str, Any],
        now,
    ) -> CommandResult: ...

    async def apply_safety(
        self,
        command_id: str,
        mutation: dict[str, Any],
        now,
    ) -> CommandResult: ...

    async def apply_proposal(
        self,
        command_id: str,
        expected_revisions: dict[str, int],
        proposal: dict[str, Any],
        now,
    ) -> CommandResult: ...
