"""safetyユースケース(M3 ws-5 design §2.3〜§2.5)。

block_userは単一トランザクションで「登録→D-23 cancelled化」までを行い、
コミット後にキャッシュDEL(§2.2 — コミット前DELは並行read-throughが
コミット前DBで旧値を再キャッシュする窓を残す)。cancelled化の通知は
送らない(引用#13)。予期しない例外はintents/latchesと同じラップ方針
(例外クラス名のみログへ残して503)。
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.latches import store as latches_store
from latch.safety import store
from latch.safety.cache import BlockCache
from latch.safety.errors import (
    SafetyDependencyUnavailableError,
    SafetyError,
    SafetyNotFoundError,
    SafetyValidationError,
)

logger = logging.getLogger("latch.safety")

# reports.reasonの選択式4値(08 §5.2・表示文言はフロントが持つ・design §2.5)
REPORT_REASONS = (
    "inappropriate_content",
    "unpleasant_behavior",
    "suspected_impersonation",
    "other",
)
# status値域はpending(受付)→reviewed(レビュー済)→resolved(対処済)。
# 本単位はpending投入のみ・遷移操作は運用面(M4+・design §2.5)
REPORT_STATUS_PENDING = "pending"


def _wrap_unexpected(exc: Exception) -> SafetyDependencyUnavailableError:
    """予期しない例外を503へ包む。例外のクラス名のみログへ残す(08 §2.4)。"""
    logger.warning("safety.unexpected class=%s", type(exc).__name__)
    return SafetyDependencyUnavailableError("safety dependency unavailable")


def encode_block_cursor(created_at: datetime, block_id: uuid.UUID) -> str:
    """blocks一覧のキーセットcursor 2キー: base64url("ISO|uuid")。

    latchesのencode_message_cursorと同型。ソート順
    (created_at DESC, id DESC)をタプル比較で表現する。
    """
    raw = f"{created_at.isoformat()}|{block_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_block_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """cursor復元。形式不正は422 VALIDATION_ERROR(同型)。"""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ct, bid = raw.split("|")
        return datetime.fromisoformat(ct), uuid.UUID(bid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SafetyValidationError("invalid cursor") from exc


class BlockReportService:
    """ブロック登録・解除・一覧・通報のユースケース(design §2.3〜§2.5)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        engine: AsyncEngine,
        block_cache: BlockCache | None = None,
    ) -> None:
        self._clock = clock
        self._engine = engine
        self._cache = block_cache

    async def _me(self, conn, auth_provider: str, auth_subject: str) -> uuid.UUID:
        """claims→users.id。未登録JWTは404(引用#14・latchesと同型)。"""
        user_id = await latches_store.fetch_user_id(conn, auth_provider, auth_subject)
        if user_id is None:
            raise SafetyNotFoundError("user not found")
        return user_id

    async def _invalidate(self, a: uuid.UUID, b: uuid.UUID) -> None:
        """コミット後に両者のキーをDEL(§2.2)。未注入時はスキップ。"""
        if self._cache is not None:
            await self._cache.invalidate([a, b])

    async def block_user(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        target_user_id: uuid.UUID,
    ) -> uuid.UUID:
        """POST /v1/users/{id}/block(design §2.4手順1〜7・単一tx)。冪等201。"""
        try:
            async with self._engine.begin() as conn:
                me = await self._me(conn, auth_provider, auth_subject)
                if target_user_id == me:
                    raise SafetyValidationError("cannot block yourself")
                if not await store.user_exists(conn, target_user_id):
                    raise SafetyNotFoundError("user not found")
                if await store.block_exists(conn, blocker=me, target=target_user_id):
                    return target_user_id  # 冪等201: 手順5〜7スキップ(引用#16)
                now = self._clock.now()
                await store.insert_block(
                    conn, blocker=me, blocked=target_user_id, now=now
                )
                await self._cancel_open_latches(
                    conn, me=me, other=target_user_id, now=now
                )
            await self._invalidate(me, target_user_id)  # コミット後DEL(§2.2)
            return target_user_id
        except SafetyError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    async def _cancel_open_latches(
        self, conn, *, me: uuid.UUID, other: uuid.UUID, now: datetime
    ) -> None:
        """D-23: 両者を共に含む進行中latchesのcancelled化(design §2.3)。

        対象はcandidateを含む3状態(承認事項②)。cancel_latchは競合負け
        Falseで読み飛ばし、成功時のみイベント挿入(user_id=blocker — 引用#12)。
        参加Intentはactiveのまま(引用#11)・通知は送らない(引用#13)。
        """
        my_intents = await store.select_user_intent_ids(conn, me)
        their_intents = await store.select_user_intent_ids(conn, other)
        rows = await store.select_open_latches_between(conn, my_intents, their_intents)
        for latch_id, from_status in rows:
            if await latches_store.cancel_latch(conn, latch_id):
                await latches_store.insert_latch_event(
                    conn, latch_id, from_status, "cancelled", me, now
                )

    async def unblock_user(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        target_user_id: uuid.UUID,
    ) -> None:
        """DELETE /v1/users/{id}/block(design §2.4)。行なし404・遡及なし。"""
        try:
            async with self._engine.begin() as conn:
                me = await self._me(conn, auth_provider, auth_subject)
                if not await store.delete_block(
                    conn, blocker=me, blocked=target_user_id
                ):
                    raise SafetyNotFoundError("block not found")
            await self._invalidate(me, target_user_id)
        except SafetyError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    async def list_blocks(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list, str | None]:
        """GET /v1/users/me/blocks(design §2.4)。created_at降順cursor改頁。"""
        try:
            async with self._engine.connect() as conn:
                me = await self._me(conn, auth_provider, auth_subject)
                before = decode_block_cursor(cursor) if cursor else None
                rows = await store.select_blocks_page(
                    conn, me=me, before=before, limit=limit + 1
                )
            items = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = encode_block_cursor(last.created_at, last.id)
            return items, next_cursor
        except SafetyError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc
