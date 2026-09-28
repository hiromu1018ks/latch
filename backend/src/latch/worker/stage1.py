"""Matching Worker第1段(06 §9・design §2.3〜§2.5)。

行確保(ON CONFLICT DO NOTHING→SELECT FOR UPDATE)・version検査
(<現行=破棄 /=現行=処理 />現行=FOR UPDATE再読込)・種別処理・再試行
(5回・バックオフ)→quarantined。削除済みIntentへの参照Eventは正当な
遅延Eventとしてprocessed破棄、payload不正のみ隔離(06 §9・design §2.4)。
SQLはtext()生SQL・時刻はClock明示値(§2グローバル制約)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from latch.core.clock import Clock
from latch.events import IncomingEvent
from latch.intents.events import (
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_UPDATED,
)
from latch.settings import Settings

logger = logging.getLogger(__name__)

# 6値(05 §2)。embedding_completedの定数追加はws-2のためここでは文字列
_EVENT_EMBEDDING_COMPLETED = "embedding_completed"
EVENT_TYPES = frozenset(
    {
        EVENT_CREATED,
        EVENT_UPDATED,
        EVENT_DELETED,
        "expired",
        "scheduled",
        _EVENT_EMBEDDING_COMPLETED,
    }
)
BACKOFF_SEC: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
_NIL_UUID = uuid.UUID(int=0)

_INSERT_EVENT = text("""
    INSERT INTO match_events
        (event_type, source_intent_id, payload, status, created_at)
    VALUES
        (:event_type, :source_intent_id, CAST(:payload AS jsonb), :status,
         :created_at)
    ON CONFLICT DO NOTHING
    RETURNING id
""")
_SELECT_CLAIM = text("""
    SELECT id, status FROM match_events
    WHERE event_type = :event_type AND source_intent_id = :source_intent_id
      AND payload->>'version' = :version_text
    FOR UPDATE
""")
_LOCK_ROW = text("SELECT status FROM match_events WHERE id = :row_id FOR UPDATE")
_SELECT_INTENT = text("SELECT version FROM intents WHERE id = :intent_id")
_SELECT_INTENT_FOR_UPDATE = text(
    "SELECT version FROM intents WHERE id = :intent_id FOR UPDATE"
)
# 理由追記は payload || :extra(別キー追加)。payload->>'version' は不変(design §2.4)
_MARK_PROCESSED = text("""
    UPDATE match_events
    SET status = 'processed', processed_at = :now,
        payload = payload || CAST(:extra AS jsonb)
    WHERE id = :row_id AND status = 'pending'
""")
_MARK_QUARANTINED = text("""
    UPDATE match_events
    SET status = 'quarantined', processed_at = :now,
        payload = payload || CAST(:extra AS jsonb)
    WHERE id = :row_id AND status = 'pending'
""")
_CLOSE_CANDIDATES = text("""
    UPDATE match_candidates
    SET status = 'closed', updated_at = :now
    WHERE (intent_a_id = :intent_id OR intent_b_id = :intent_id)
      AND status <> 'closed'
""")


class Stage1Error(Exception):
    """Stage1内の処理失敗(再試行ループの対象)。"""


class PayloadInvalid(Stage1Error):
    """構造違反(version欠落・型不一致・6値外event_type — 永続的失敗)。"""


class Retryable(Stage1Error):
    """一時的失敗(version乖離の継続・読み取り遅延)。"""


@dataclass
class IntakeResult:
    kind: str  # "debounce" | "processed" | "duplicate" | "quarantined"
    triple: tuple[str, uuid.UUID, int] | None = None
    row_id: uuid.UUID | None = None
    reason: str | None = None


class Stage1:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        embedding_hook: Callable[[str, uuid.UUID, int], Awaitable[None]] | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._retry_max = settings.event_retry_max
        self._sleep = sleep
        self._embedding_hook = embedding_hook  # ws-2が実体を置く(design §2.5)

    # -- 受信入口 --

    async def intake(self, event: IncomingEvent) -> IntakeResult:
        """検証→行確保→分岐。構造違反は再試行5回→quarantine直行(10 §4.7)。"""
        triple: tuple[str, uuid.UUID, int] | None = None
        reason: str | None = None
        for attempt in range(self._retry_max + 1):
            try:
                triple = self._validate(event)
                break
            except PayloadInvalid as exc:
                reason = str(exc)
                logger.warning(
                    "stage1 invalid event message_id=%s attempt=%d reason=%s",
                    event.message_id,
                    attempt + 1,
                    reason,
                )
                if attempt < self._retry_max:
                    await self._sleep(BACKOFF_SEC[attempt])
        if triple is None:
            await self._quarantine_direct(event, reason=reason or "invalid payload")
            return IntakeResult(kind="quarantined", reason=reason)
        row_id, status = await self._claim(triple)
        if status != "pending":
            return IntakeResult(kind="duplicate", triple=triple, row_id=row_id)
        if triple[0] == EVENT_UPDATED:
            return IntakeResult(kind="debounce", triple=triple, row_id=row_id)
        outcome = await self.process(event, triple, row_id)
        return IntakeResult(kind=outcome, triple=triple, row_id=row_id)

    # -- 処理(debounce解放後・即時種別共通)--

    async def process(
        self,
        event: IncomingEvent,
        triple: tuple[str, uuid.UUID, int],
        row_id: uuid.UUID,
    ) -> str:
        """1トランザクション処理を再試行ループで。成功=processed・上限=quarantined。"""
        reason: str | None = None
        for attempt in range(self._retry_max + 1):
            try:
                await self._process_once(triple, row_id)
                return "processed"
            except (Retryable, SQLAlchemyError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "stage1 process event_type=%s intent_id=%s attempt=%d failed: %s",
                    triple[0],
                    triple[1],
                    attempt + 1,
                    reason,
                )
                if attempt < self._retry_max:
                    await self._sleep(BACKOFF_SEC[attempt])
        await self._quarantine_row(row_id, triple, reason=reason or "retry exhausted")
        return "quarantined"

    async def discard(self, row_id: uuid.UUID, *, reason: str) -> None:
        """debounce吸収行のprocessed閉包(design §2.3)。"""
        async with self._engine.begin() as conn:
            await conn.execute(
                _MARK_PROCESSED,
                {
                    "row_id": row_id,
                    "now": self._clock.now(),
                    "extra": json.dumps({"discard_reason": reason}),
                },
            )

    # -- 内部 --

    def _validate(self, event: IncomingEvent) -> tuple[str, uuid.UUID, int]:
        triple = event.triple()
        if triple is None:
            raise PayloadInvalid(
                "unextractable triple (missing or invalid event_type/"
                "source_intent_id/version)"
            )
        if triple[0] not in EVENT_TYPES:
            raise PayloadInvalid(f"unknown event_type {triple[0]!r}")
        return triple

    async def _claim(self, triple: tuple[str, uuid.UUID, int]) -> tuple[uuid.UUID, str]:
        """行確保(冪等受領 — design §2.3)。API経由は既存行・直投入はここでINSERT。"""
        event_type, intent_id, version = triple
        params = {
            "event_type": event_type,
            "source_intent_id": intent_id,
            "payload": json.dumps({"version": version}),
            "status": "pending",
            "created_at": self._clock.now(),
        }
        async with self._engine.begin() as conn:
            res = await conn.execute(_INSERT_EVENT, params)
            row = res.first()
            if row is not None:
                return uuid.UUID(row[0]), "pending"
            res = await conn.execute(
                _SELECT_CLAIM,
                {
                    "event_type": event_type,
                    "source_intent_id": intent_id,
                    "version_text": str(version),
                },
            )
            row = res.first()
            if row is None:
                raise Retryable("claim conflicted but row not found")
            return uuid.UUID(row[0]), row[1]

    async def _process_once(
        self, triple: tuple[str, uuid.UUID, int], row_id: uuid.UUID
    ) -> None:
        event_type, intent_id, version = triple
        now = self._clock.now()
        async with self._engine.begin() as conn:
            status = await self._lock_row(conn, row_id)
            if status != "pending":
                return  # 二重受領(条件UPDATE・FOR UPDATEで直列化)
            current = await self._intent_version(conn, intent_id, for_update=False)
            if current is None:
                # 削除済み等の不在=正当な遅延Event → processed破棄(06 §9)
                await self._mark(conn, _MARK_PROCESSED, row_id, "intent_not_found", now)
                return
            if version < current:
                await self._mark(conn, _MARK_PROCESSED, row_id, "stale_version", now)
                return
            if version > current:
                reread = await self._intent_version(conn, intent_id, for_update=True)
                if reread is None:
                    raise Retryable("intent vanished on reread")
                if reread != version:
                    raise Retryable(
                        f"version divergence: event={version} current={reread}"
                    )
            # version == intents.version → 種別処理(§2.5)
            if event_type == EVENT_DELETED:
                await conn.execute(
                    _CLOSE_CANDIDATES, {"intent_id": intent_id, "now": now}
                )
            elif event_type in (EVENT_CREATED, EVENT_UPDATED):
                if self._embedding_hook is not None:
                    await self._embedding_hook(event_type, intent_id, version)
            # expired / scheduled / embedding_completed は処理実体なし(processed)
            await conn.execute(
                _MARK_PROCESSED, {"row_id": row_id, "now": now, "extra": "{}"}
            )

    async def _lock_row(self, conn: AsyncConnection, row_id: uuid.UUID) -> str | None:
        res = await conn.execute(_LOCK_ROW, {"row_id": row_id})
        row = res.first()
        if row is None:
            raise Retryable("match_events row vanished")
        return row[0]

    async def _intent_version(
        self, conn: AsyncConnection, intent_id: uuid.UUID, *, for_update: bool
    ) -> int | None:
        stmt = _SELECT_INTENT_FOR_UPDATE if for_update else _SELECT_INTENT
        res = await conn.execute(stmt, {"intent_id": intent_id})
        row = res.first()
        return None if row is None else row[0]

    async def _mark(
        self, conn: AsyncConnection, stmt, row_id: uuid.UUID, reason: str, now: datetime
    ) -> None:
        await conn.execute(
            stmt,
            {
                "row_id": row_id,
                "now": now,
                "extra": json.dumps({"discard_reason": reason}),
            },
        )

    async def _quarantine_row(
        self, triple: tuple[str, uuid.UUID, int], row_id: uuid.UUID, *, reason: str
    ) -> None:
        """claim済み行の隔離遷移(失敗理由をpayloadへ保持 — 05 §2)。"""
        async with self._engine.begin() as conn:
            await conn.execute(
                _MARK_QUARANTINED,
                {
                    "row_id": row_id,
                    "now": self._clock.now(),
                    "extra": json.dumps({"failure_reason": reason}),
                },
            )

    async def _quarantine_direct(self, event: IncomingEvent, *, reason: str) -> None:
        """行なし受領(毒ペイロード)の隔離行INSERT(design §2.4)。

        3点組が組める場合はその3点組・組めない場合は受信生データを保存する
        (source_intent_id は nil UUID — 行挿入を確実にしUNIQUEのNULL重複許容
        は「複数の毒ペイロードを妨げない」意図どおり — design §2.4)。
        """
        triple = event.triple()
        now = self._clock.now()
        async with self._engine.begin() as conn:
            if triple is not None:
                event_type, intent_id, version = triple
                res = await conn.execute(
                    _INSERT_EVENT,
                    {
                        "event_type": event_type,
                        "source_intent_id": intent_id,
                        "payload": json.dumps(
                            {"version": version, "failure_reason": reason}
                        ),
                        "status": "quarantined",
                        "created_at": now,
                    },
                )
                if res.first() is not None:
                    return  # quarantined直行の挿入が成功
                # UNIQUE競合(過去の同一3点組)→ 既存行をquarantined遷移
                res = await conn.execute(
                    _SELECT_CLAIM,
                    {
                        "event_type": event_type,
                        "source_intent_id": intent_id,
                        "version_text": str(version),
                    },
                )
                existing = res.first()
                if existing is not None:
                    await conn.execute(
                        _MARK_QUARANTINED,
                        {
                            "row_id": uuid.UUID(existing[0]),
                            "now": now,
                            "extra": json.dumps({"failure_reason": reason}),
                        },
                    )
                return
            await conn.execute(
                _INSERT_EVENT,
                {
                    "event_type": (
                        event.data.get("event_type")
                        if isinstance(event.data.get("event_type"), str)
                        else "invalid"
                    ),
                    "source_intent_id": event.intent_id or _NIL_UUID,
                    "payload": json.dumps(
                        {"raw": event.data, "failure_reason": reason}
                    ),
                    "status": "quarantined",
                    "created_at": now,
                },
            )
