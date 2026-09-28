"""Embedding Worker本体(M2 ws-2・design §2.2)。

Stage1コミット後・ack前にWorkerから呼ばれる(配置はdesign §2.1-B・フック不使用)。
2フェーズ(短トランザクションread → API呼び出しはトランザクション外 →
ガード付きUPDATE+embedding_completed行INSERTの1トランザクション)で外部API
呼び出し(最大2秒)をDBトランザクションの外へ出す。正しさはUPDATEのガード
(version一致+embedding IS NULL)が担保し、at-least-onceの重複API呼び出しは
許容する(design §2.2のトレードオフ・コスト影響は無視できる)。
LLM失敗は握ってembedding NULLのままreturn=バックフィル対象(06 D-15)。
DB失敗は例外を伝播し、呼び出し側(Worker._dispatch)のackなし再配信が回収する。
SQLはtext()生SQL・時刻はClock明示値(ws-1と同じ規律)。SELECTにraw_textは
含めない(確定値#10)。
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.events.bus import EventBus
from latch.intents.events import EVENT_EMBEDDING_COMPLETED
from latch.llm.errors import LLMError
from latch.llm.gemini import GEMINI_EMBEDDING_MODEL
from latch.llm.providers import EMBEDDING_DIMENSIONS
from latch.worker.embedding_text import EmbeddingTextInput, build_embedding_text

logger = logging.getLogger(__name__)

# Layer 1〜2の対象はactive(06 §3)。pausedはresume時のキック(IS NULL判定)で回収
_STATUS_TARGETS = frozenset({"active", "paused"})

_SELECT_INTENT_FOR_EMBEDDING = text("""
    SELECT version, status, (embedding IS NOT NULL) AS has_embedding,
           category_primary, structured_data, participants_min, participants_max,
           time_start, time_end
    FROM intents WHERE id = :intent_id
""")
# ガード: フェーズ1で読んだ内容が現行のままであること(version) + 二重書き込み排除
# (embedding IS NULL)。CAST(:vec AS vector)の明示CASTはbind param直後の'::'短縮
# CASTがSQLAlchemyに認識されない事故(M1 ws-3)の回帰予防
_UPDATE_EMBEDDING = text("""
    UPDATE intents
    SET embedding = CAST(:vec AS vector), embedding_model = :model,
        updated_at = :now
    WHERE id = :intent_id AND version = :version AND embedding IS NULL
    RETURNING id
""")
# payloadは{"version": N}のみ(UNIQUE索引が参照するpayload->>'version'と同じ形式)
_INSERT_EMBEDDING_COMPLETED = text("""
    INSERT INTO match_events
        (event_type, source_intent_id, payload, status, created_at)
    VALUES (:event_type, :source_intent_id, CAST(:payload AS jsonb), 'pending',
            :created_at)
    ON CONFLICT DO NOTHING
""")


class EmbeddingWorker:
    """Embedding実行とembedding_completed発行(design §2.2)。冪等(handle再実行可)。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        gateway,
        bus: EventBus,
        model_id: str = GEMINI_EMBEDDING_MODEL,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._gateway = gateway
        self._bus = bus
        self._model_id = model_id

    async def handle(self, intent_id: uuid.UUID, version: int) -> None:
        row = await self._read_target(intent_id)
        if row is None:
            return
        db_version, status, has_embedding = row[0], row[1], row[2]
        if db_version != version:
            return  # 旧Eventの焼き直し(新versionのEventが担当)
        if status not in _STATUS_TARGETS:
            return  # 削除済み等(Layer 1〜2の対象外)
        if has_embedding:
            # 既存(resume由来 — テキスト不変)は再エンベディングせず直接投入(06 §9)
            await self._emit_completed(intent_id, version)
            return
        await self._embed_and_emit(intent_id, version, row)

    # -- フェーズ1(短トランザクション・readのみ)--

    async def _read_target(self, intent_id: uuid.UUID) -> tuple | None:
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _SELECT_INTENT_FOR_EMBEDDING, {"intent_id": intent_id}
            )
            return res.first()

    # -- フェーズ2(API呼び出しはトランザクション外)--

    async def _embed_and_emit(
        self, intent_id: uuid.UUID, version: int, row: tuple
    ) -> None:
        _, _, _, category_primary, structured, pmin, pmax, t_start, t_end = row
        if isinstance(structured, str):  # asyncpgのjsonbがstrで返る場合(store.py規律)
            structured = json.loads(structured)
        inp = EmbeddingTextInput(
            category_primary=category_primary,
            structured_data=structured,
            participants_min=pmin,
            participants_max=pmax,
            time_start=t_start,
            time_end=t_end,
        )
        text = build_embedding_text(inp)
        try:
            vec = await self._gateway.embed_intent(text=text, intent_id=str(intent_id))
        except LLMError as exc:
            # 再試行なし(07 §1)。embeddingはNULLのまま=バックフィル対象(06 D-15)
            logger.warning(
                "embedding failed (backfill target) intent_id=%s version=%s error=%s",
                intent_id,
                version,
                type(exc).__name__,
            )
            return
        if len(vec) != EMBEDDING_DIMENSIONS:
            logger.error(
                "embedding dimension mismatch intent_id=%s version=%s dim=%d",
                intent_id,
                version,
                len(vec),
            )
            return
        vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
        now = self._clock.now()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _UPDATE_EMBEDDING,
                {
                    "intent_id": intent_id,
                    "version": version,
                    "vec": vec_literal,
                    "model": self._model_id,
                    "now": now,
                },
            )
            if res.first() is None:
                return  # 並行handleが先に書いた/更新が入った(ガードが排除)
            await self._insert_completed(conn, intent_id, version, now)
        await self._publish(intent_id, version)

    async def _emit_completed(self, intent_id: uuid.UUID, version: int) -> None:
        now = self._clock.now()
        async with self._engine.begin() as conn:
            await self._insert_completed(conn, intent_id, version, now)
        await self._publish(intent_id, version)

    async def _insert_completed(
        self, conn, intent_id: uuid.UUID, version: int, now
    ) -> None:
        await conn.execute(
            _INSERT_EMBEDDING_COMPLETED,
            {
                "event_type": EVENT_EMBEDDING_COMPLETED,
                "source_intent_id": intent_id,
                "payload": json.dumps({"version": version}),
                "created_at": now,
            },
        )

    async def _publish(self, intent_id: uuid.UUID, version: int) -> None:
        """コミット後publish。失敗は握る(行はpending=リレーが30秒後に回収)。"""
        try:
            await self._bus.publish_match_event(
                event_type=EVENT_EMBEDDING_COMPLETED,
                intent_id=intent_id,
                version=version,
            )
        except Exception:
            logger.warning(
                "embedding_completed publish failed intent_id=%s version=%s",
                intent_id,
                version,
            )
