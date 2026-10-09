"""safety(block・通報)のD1版サービス(本体実装)。

block/unblock は atomic.apply_safety(block登録+D-23 cancelled化を1batchで
確定)を使う。検証順序(自分自身拒否・相手実在・latch参加者検査)は
参照実装(safety/service.py)と同一。
"""

from __future__ import annotations

import base64
import uuid as uuid_mod
from dataclasses import dataclass
from datetime import datetime

from latch.adapters.d1.atomic import D1AtomicRepository
from latch.domain.time import from_micros, micros
from latch.domain.types import CommandStatus
from latch.safety.service import REPORT_STATUS_PENDING

from latch_worker.gjson import loads_json


@dataclass(frozen=True)
class BlockRow:
    """blocks一覧の1行(safety/store.BlockRow相当)。"""

    id: uuid_mod.UUID
    blocked_id: uuid_mod.UUID
    display_name: str
    created_at: datetime


def _encode_block_cursor(created_at: datetime, block_id) -> str:
    raw = f"{created_at.isoformat()}|{block_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


class D1SafetyService:
    """ブロック登録・解除・一覧・通報(BlockReportService相当)。"""

    def __init__(self, *, clock, db, atomic: D1AtomicRepository) -> None:
        self._clock = clock
        self._d1 = db
        self._atomic = atomic

    async def _me(self, provider: str, subject: str):
        from latch.safety.errors import SafetyNotFoundError

        row = (
            await self._d1.prepare(
                "SELECT id FROM users WHERE auth_provider = ? AND auth_subject = ?"
            )
            .bind(provider, subject)
            .first()
        )
        if row is None:
            raise SafetyNotFoundError("user not found")
        return uuid_mod.UUID(str(row["id"]))

    async def block_user(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        target_user_id,
    ):
        from latch.safety.errors import (
            SafetyDependencyUnavailableError,
            SafetyError,
            SafetyNotFoundError,
            SafetyValidationError,
        )

        try:
            me = await self._me(auth_provider, auth_subject)
            if target_user_id == me:
                raise SafetyValidationError("cannot block yourself")
            exists = (
                await self._d1.prepare("SELECT 1 AS x FROM users WHERE id = ?")
                .bind(str(target_user_id))
                .first()
            )
            if exists is None:
                raise SafetyNotFoundError("user not found")
            block_exists = (
                await self._d1.prepare(
                    "SELECT 1 AS x FROM blocks"
                    " WHERE blocker_id = ? AND blocked_id = ?"
                )
                .bind(str(me), str(target_user_id))
                .first()
            )
            if block_exists is not None:
                return target_user_id  # 冪等201(引用#16)
            result = await self._atomic.apply_safety(
                str(uuid_mod.uuid4()),
                {
                    "kind": "block",
                    "blocker_id": str(me),
                    "blocked_id": str(target_user_id),
                },
                self._clock.now(),
            )
            if result.status == CommandStatus.REJECTED:
                if result.error_code == "NOT_FOUND":
                    raise SafetyNotFoundError("user not found")
                raise SafetyValidationError("cannot block yourself")
            return target_user_id
        except SafetyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SafetyDependencyUnavailableError(
                "safety dependency unavailable"
            ) from exc

    async def unblock_user(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        target_user_id,
    ) -> None:
        from latch.safety.errors import (
            SafetyDependencyUnavailableError,
            SafetyError,
            SafetyNotFoundError,
        )

        try:
            me = await self._me(auth_provider, auth_subject)
            res = (
                await self._d1.prepare(
                    "DELETE FROM blocks WHERE blocker_id = ? AND blocked_id = ?"
                )
                .bind(str(me), str(target_user_id))
                .run()
            )
            if res.changes == 0:
                raise SafetyNotFoundError("block not found")
        except SafetyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SafetyDependencyUnavailableError(
                "safety dependency unavailable"
            ) from exc

    async def list_blocks(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        cursor: str | None = None,
        limit: int = 20,
    ):
        from latch.safety.errors import (
            SafetyDependencyUnavailableError,
            SafetyError,
            SafetyValidationError,
        )

        try:
            me = await self._me(auth_provider, auth_subject)
            before = None
            if cursor:
                padded = cursor + "=" * (-len(cursor) % 4)
                try:
                    raw = base64.urlsafe_b64decode(padded.encode()).decode()
                    ct, bid = raw.split("|")
                    before = (datetime.fromisoformat(ct), uuid_mod.UUID(bid))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise SafetyValidationError("invalid cursor") from exc
            clauses = ["b.blocker_id = ?"]
            params: list = [str(me)]
            if before is not None:
                clauses.append("(b.created_at < ? OR (b.created_at = ? AND b.id < ?))")
                params.extend(
                    [micros(before[0]), micros(before[0]), str(before[1])]
                )
            rows = (
                await self._d1.prepare(
                    "SELECT b.id, b.blocked_id, u.display_name, b.created_at"
                    " FROM blocks b JOIN users u ON u.id = b.blocked_id"
                    " WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY b.created_at DESC, b.id DESC LIMIT ?"
                )
                .bind(*params, limit + 1)
                .all()
            )
            items = [
                BlockRow(
                    id=uuid_mod.UUID(str(r["id"])),
                    blocked_id=uuid_mod.UUID(str(r["blocked_id"])),
                    display_name=r["display_name"],
                    created_at=from_micros(r["created_at"]),
                )
                for r in rows[:limit]
            ]
            next_cursor = None
            if len(rows) > limit:
                last = items[limit - 1]
                next_cursor = _encode_block_cursor(last.created_at, last.id)
            return items, next_cursor
        except SafetyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SafetyDependencyUnavailableError(
                "safety dependency unavailable"
            ) from exc

    async def report_user(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        reportee_id=None,
        latch_id=None,
        reason: str = "other",
    ):
        from latch.safety.errors import (
            SafetyDependencyUnavailableError,
            SafetyError,
            SafetyNotFoundError,
            SafetyValidationError,
        )

        try:
            me = await self._me(auth_provider, auth_subject)
            if reportee_id is None and latch_id is None:
                raise SafetyValidationError("reportee_id or latch_id required")
            if latch_id is not None:
                latch = (
                    await self._d1.prepare(
                        "SELECT intent_ids FROM latches WHERE id = ?"
                    )
                    .bind(str(latch_id))
                    .first()
                )
                if latch is None:
                    raise SafetyNotFoundError("latch not found")
                participants = (
                    await self._d1.prepare(
                        "SELECT DISTINCT user_id FROM intents WHERE id IN"
                        " (SELECT value FROM json_each(?))"
                    )
                    .bind(latch["intent_ids"])
                    .all()
                )
                participant_ids = [uuid_mod.UUID(str(p["user_id"])) for p in participants]
                if me not in participant_ids:
                    raise SafetyValidationError("not latch participants")
                if reportee_id is None:
                    others = [u for u in participant_ids if u != me]
                    if len(others) != 1:
                        raise SafetyValidationError(
                            "cannot resolve reportee from latch"
                        )
                    reportee_id = others[0]
                elif reportee_id not in participant_ids:
                    raise SafetyValidationError("not latch participants")
            if reportee_id == me:
                raise SafetyValidationError("cannot report yourself")
            exists = (
                await self._d1.prepare("SELECT 1 AS x FROM users WHERE id = ?")
                .bind(str(reportee_id))
                .first()
            )
            if exists is None:
                raise SafetyNotFoundError("user not found")
            report_id = str(uuid_mod.uuid4())
            now_us = micros(self._clock.now())
            await self._d1.batch(
                [
                    self._d1.prepare(
                        "INSERT INTO reports"
                        " (id, reporter_id, reportee_id, latch_id, reason,"
                        "  status, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)"
                    ).bind(
                        report_id,
                        str(me),
                        str(reportee_id),
                        str(latch_id) if latch_id is not None else None,
                        reason,
                        REPORT_STATUS_PENDING,
                        now_us,
                    )
                ]
            )
            return uuid_mod.UUID(report_id)
        except SafetyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SafetyDependencyUnavailableError(
                "safety dependency unavailable"
            ) from exc
