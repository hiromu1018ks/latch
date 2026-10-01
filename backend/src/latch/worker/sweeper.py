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

import json
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.intents.events import EVENT_EXPIRED, insert_match_event
from latch.notifications.types import NOTIFICATION_ATTENDANCE_REQUEST

if TYPE_CHECKING:
    from latch.notifications.sender import PushSender

logger = logging.getLogger(__name__)

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


class ExpirySweeper:
    """期限切れバッチ本体(06 §6・design §2.2〜2.4・§2.7)。

    ReevalRunner.run_onceの先頭から呼ばれる(60秒tick・design §2.1案A)。
    run_onceはpublic(unit/integrationから直接呼ぶ・ReevalRunnerと同型)。
    独立した周期ループは持たない(切替・停止はReevalRunnerの単一ジョブ)。
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock,  # Clock(既存のClock型・core.clock)
        latch,  # LatchEngine(drain呼び出し・design §2.7)
        batch_limit: int,
        push: PushSender | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._latch = latch
        self._batch_limit = batch_limit
        self._push = push  # PushSender | None(未注入ならno-op・design §2.7)
        self._last_tick: datetime = clock.now()  # 起動時刻(§2.7)

    async def run_once(self) -> int:
        """1周期分: latches期限切れ→Intent期限切れ→completed→クローズ検知drain。

        戻り値=遷移させた行数(latches+intents+completedの合計・drain呼出を
        含まない)。例外は握らない(ReevalRunner.runが握って次周期で回収)。
        1tick=1時刻: 抽出・UPDATE・挿入すべてで同一のnowを使う(design §2.2)。
        """
        now = self._clock.now()
        done = 0
        for latch_id, status in await _select_expiring_latches(
            self._engine, now, self._batch_limit
        ):
            if await self._expire_latch(latch_id, status, now):
                done += 1
        for intent_id, _status in await _select_expiring_intents(
            self._engine, now, self._batch_limit
        ):
            if await self._expire_intent(intent_id, now):
                done += 1
        for latch_id in await _select_completion_targets(
            self._engine, now, self._batch_limit
        ):
            if await self._complete_latch(latch_id, now):
                done += 1
        # 観測→drainの順(自分の書いたexpiredも観測=枠回復を同じtickで。
        # drain後の観測はしない=再帰なし・Review Focus 4)
        if await _has_recent_close(self._engine, self._last_tick):
            await self._latch.drain()
        self._last_tick = now
        return done

    async def _expire_latch(
        self, latch_id: uuid.UUID, from_status: str, now: datetime
    ) -> bool:
        """行単位tx: 条件付きUPDATE→影響1ならlatch_status_events同一tx挿入。

        抽出時のstatusでUPDATE文を使い分ける(candidateは期限のみ判定)。
        影響0(回答API等が先行)はイベントなしで無視(引用#3)。
        """
        async with self._engine.begin() as conn:
            stmt = (
                _EXPIRE_CANDIDATE_LATCH
                if from_status == "candidate"
                else _EXPIRE_RESPONSE_LATCH
            )
            res = await conn.execute(stmt, {"latch_id": latch_id, "now": now})
            if res.first() is None:
                return False
            await conn.execute(
                _INSERT_LATCH_EVENT_SQL,
                {
                    "latch_id": latch_id,
                    "from_status": from_status,
                    "to_status": "expired",
                    "user_id": None,
                    "now": now,
                },
            )
            logger.info("sweeper.expired latch_id=%s from=%s", latch_id, from_status)
            return True

    async def _expire_intent(self, intent_id: uuid.UUID, now: datetime) -> bool:
        """行単位tx: expired化+expiredイベント同一tx発行(引用#19・保存と同一慣行)。

        draft(下書き)も対象(引用#5)。paused→expiredも対象(resume Event競合は
        version検査とstatus検査で二重遷移なし)。match_candidates等は閉じない
        (supervisor承認⑤・Layer 1のstatus='active'条件とH再検証で自然無力化)。
        """
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _EXPIRE_INTENT, {"intent_id": intent_id, "now": now}
            )
            row = res.first()
            if row is None:
                return False
            await insert_match_event(
                conn,
                event_type=EVENT_EXPIRED,
                intent_id=intent_id,
                version=int(row[0]),
                now=now,
            )
            logger.info("sweeper.intent_expired intent_id=%s", intent_id)
            return True

    async def _complete_latch(self, latch_id: uuid.UUID, now: datetime) -> bool:
        """行単位tx: completed化+イベント+実施自己申告の通知先行書き込み(D-09)。

        cancelled(解散済み)はstatus='matched'でなく対象外=通知を送らない
        (引用#15・構造的担保)。参加者はintents.user_idを行順に全員へ(§9-9)。
        M3 ws-3: プッシュ送信はtxコミット後(design §2.1案A)。
        """
        targets: list[uuid.UUID] = []
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _COMPLETE_LATCH, {"latch_id": latch_id, "now": now}
            )
            row = res.first()
            if row is None:
                return False
            await conn.execute(
                _INSERT_LATCH_EVENT_SQL,
                {
                    "latch_id": latch_id,
                    "from_status": "matched",
                    "to_status": "completed",
                    "user_id": None,
                    "now": now,
                },
            )
            users = await conn.execute(
                _SELECT_LATCH_PARTICIPANT_USERS,
                {"ids": [_coerce_uuid(x) for x in row[0]]},
            )
            for (user_id,) in users.fetchall():
                await conn.execute(
                    _INSERT_ATTENDANCE_NOTIFICATION,
                    {
                        "user_id": _coerce_uuid(user_id),
                        "type": NOTIFICATION_ATTENDANCE_REQUEST,
                        "payload": json.dumps({"latch_id": str(latch_id)}),
                        "now": now,
                    },
                )
                targets.append(_coerce_uuid(user_id))
            logger.info("sweeper.completed latch_id=%s", latch_id)
        await self._send_attendance_pushes(latch_id, targets)
        return True

    async def _send_attendance_pushes(
        self, latch_id: uuid.UUID, targets: list[uuid.UUID]
    ) -> None:
        """txコミット後のattendanceプッシュ送信(design §2.1案A・§2.7)。

        未注入(既存試験構成)はno-op。送信例外はsender側で握る。
        """
        if self._push is None:
            return
        for user_id in targets:
            await self._push.send(
                user_id=user_id,
                notification_type=NOTIFICATION_ATTENDANCE_REQUEST,
                latch_id=latch_id,
            )
