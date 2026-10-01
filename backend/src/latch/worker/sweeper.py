"""期限切れバッチExpirySweeper(M3-3・06 §6・design §2.2〜2.4・§2.7)。

latches期限切れ(proposed/partial_accept×回答期限/Intent期限・candidate×
期限切れ)・Intent期限切れ(draft/active/paused×expires_at)・matched→completed
遷移・クローズ検知drain(latch_status_events観測)の4処理を1つのrun_onceで
実行する。ReevalRunnerのrun_once先頭から呼ばれる(同一スケジューラ・単一
ジョブ管理・design §2.1案A)。

直列化(06 §6引用#3): 抽出はFOR UPDATE SKIP LOCKED(抽出txは即コミット)、
行ごとに個別txの条件付きUPDATE(抽出と実行の間に他経路が行を変えても影響
行数0で無視)。影響行数1のとき遷移イベント(latch_status_events・
match_events expired)・attendance通知を**同一tx**で挿入する(C10)。
1tick=1時刻: run_once冒頭のClock.now()を抽出・UPDATE・挿入すべてで使う。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# 実施自己申告の通知type(実装定義・supervisor承認②。媒体・読み取りはws-3)
NOTIFICATION_ATTENDANCE_REQUEST = "attendance_request"

# latches期限切れ対象抽出(06 §6の対象条件・design §2.2)。
# ORDER BYは期限切れが古い順(response_deadline・expires_at・id)
_SELECT_EXPIRING_LATCHES = text("""
    SELECT id, status FROM latches
    WHERE (status IN ('proposed', 'partial_accept')
           AND (response_deadline <= CAST(:now AS timestamptz)
                OR expires_at <= CAST(:now AS timestamptz)))
       OR (status = 'candidate' AND expires_at <= CAST(:now AS timestamptz))
    ORDER BY response_deadline, expires_at, id
    LIMIT :batch_limit
    FOR UPDATE SKIP LOCKED
""")

# proposed/partial_accept行(回答API _UPDATE_RESPONSEと同一の真実を再検査)
_EXPIRE_RESPONSE_LATCH = text("""
    UPDATE latches SET status = 'expired'
    WHERE id = CAST(:latch_id AS uuid)
      AND status IN ('proposed', 'partial_accept')
      AND (response_deadline <= CAST(:now AS timestamptz)
           OR expires_at <= CAST(:now AS timestamptz))
    RETURNING id
""")

# candidate行(保留キュー。response_deadlineは暫定値なので判定に使わない・引用#2)
_EXPIRE_CANDIDATE_LATCH = text("""
    UPDATE latches SET status = 'expired'
    WHERE id = CAST(:latch_id AS uuid)
      AND status = 'candidate'
      AND expires_at <= CAST(:now AS timestamptz)
    RETURNING id
""")

# Intent期限切れ対象抽出(WHEREはidx_intents_expiresの部分索引条件と同一・引用#6)
_SELECT_EXPIRING_INTENTS = text("""
    SELECT id, status FROM intents
    WHERE status IN ('draft', 'active', 'paused')
      AND expires_at <= CAST(:now AS timestamptz)
    ORDER BY expires_at, id
    LIMIT :batch_limit
    FOR UPDATE SKIP LOCKED
""")

_EXPIRE_INTENT = text("""
    UPDATE intents SET status = 'expired', updated_at = CAST(:now AS timestamptz)
    WHERE id = CAST(:intent_id AS uuid)
      AND status IN ('draft', 'active', 'paused')
      AND expires_at <= CAST(:now AS timestamptz)
    RETURNING version
""")

# matched→completed対象抽出(対象時刻=max(参加Intentのtime_start)経過・design §2.4)
_SELECT_COMPLETION_TARGETS = text("""
    SELECT l.id FROM latches l
    WHERE l.status = 'matched'
      AND (SELECT max(i.time_start) FROM intents i
           WHERE i.id = ANY(l.intent_ids)) <= CAST(:now AS timestamptz)
    ORDER BY (SELECT max(i.time_start) FROM intents i
              WHERE i.id = ANY(l.intent_ids)), l.id
    LIMIT :batch_limit
    FOR UPDATE SKIP LOCKED
""")

# cancelled(解散済み)はstatus='matched'でなく対象外=申告通知を送らない(引用#15)
_COMPLETE_LATCH = text("""
    UPDATE latches SET status = 'completed', completed_at = CAST(:now AS timestamptz)
    WHERE id = CAST(:latch_id AS uuid) AND status = 'matched'
    RETURNING intent_ids
""")

# completed遷移時の実施自己申告通知の宛先(D-09・参加者全員)
_SELECT_LATCH_PARTICIPANT_USERS = text("""
    SELECT user_id FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

# latch_status_events挿入(latch_engine._INSERT_LATCH_EVENTと同一SQL。
# システム起因=本バッチはuser_id=NULL・引用#13)
_INSERT_LATCH_EVENT_SQL = text("""
    INSERT INTO latch_status_events
        (latch_id, from_status, to_status, user_id, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:from_status AS text),
            :to_status, CAST(:user_id AS uuid), :now)
""")

# attendance通知先行書き込み(payload={latch_id}最小参照・引用#16)
_INSERT_ATTENDANCE_NOTIFICATION = text("""
    INSERT INTO notifications (user_id, type, payload, created_at)
    VALUES (CAST(:user_id AS uuid), :type, CAST(:payload AS jsonb), :now)
""")

# クローズ検知(design §2.7): last_tick以降のクローズ系遷移の1行存在検査
_SELECT_RECENT_CLOSE = text("""
    SELECT 1 FROM latch_status_events
    WHERE created_at > CAST(:last_tick AS timestamptz)
      AND to_status IN ('rejected', 'expired', 'cancelled', 'matched')
    LIMIT 1
""")


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策・origin.pyと同一規律)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def _select_expiring_latches(
    engine: AsyncEngine, now: datetime, batch_limit: int
) -> list[tuple[uuid.UUID, str]]:
    """latches期限切れ対象抽出(engine.begin()内包・抽出txは即コミット)。"""
    async with engine.begin() as conn:
        res = await conn.execute(
            _SELECT_EXPIRING_LATCHES, {"now": now, "batch_limit": batch_limit}
        )
        rows = res.fetchall()
    return [(_coerce_uuid(r[0]), r[1]) for r in rows]


async def _select_expiring_intents(
    engine: AsyncEngine, now: datetime, batch_limit: int
) -> list[tuple[uuid.UUID, str]]:
    """Intent期限切れ対象抽出(idx_intents_expires部分索引と同一条件)。"""
    async with engine.begin() as conn:
        res = await conn.execute(
            _SELECT_EXPIRING_INTENTS, {"now": now, "batch_limit": batch_limit}
        )
        rows = res.fetchall()
    return [(_coerce_uuid(r[0]), r[1]) for r in rows]


async def _select_completion_targets(
    engine: AsyncEngine, now: datetime, batch_limit: int
) -> list[uuid.UUID]:
    """matched→completed対象抽出(対象時刻昇順)。"""
    async with engine.begin() as conn:
        res = await conn.execute(
            _SELECT_COMPLETION_TARGETS, {"now": now, "batch_limit": batch_limit}
        )
        rows = res.fetchall()
    return [_coerce_uuid(r[0]) for r in rows]


async def _has_recent_close(engine: AsyncEngine, last_tick: datetime) -> bool:
    """§2.7観測: last_tick以降にクローズ系イベントがあるか(1行存在検査)。

    60秒窓の走査対象は遷移時のみ書かれるlatch_status_eventsの僅か行数
    (Index不要・design §2.7)。期限切れ処理自身が書いたexpired行も観測する
    (呼び出し順「書き込み→観測→drain」が期限切れで空いた枠の同tick昇格を許す)。
    """
    async with engine.begin() as conn:
        res = await conn.execute(_SELECT_RECENT_CLOSE, {"last_tick": last_tick})
        return res.first() is not None
