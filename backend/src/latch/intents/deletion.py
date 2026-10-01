"""削除カスケードの実体(M3 ws-6 design §2.1・08 §2.5)。

単発Intent削除(stage1のEVENT_DELETED処理)と退会(users service)の両経路から
呼ばれる1Intent分の物理削除。intents配下だが intents.service へのimportは
持たない(users→worker依存を生まない)。関数自身はtxを開かず、呼び出し元の
トランザクションに乗る(C9の直列化方式と同じ寿命)。
削除順序はFK依存の固定順: ①1対1候補(処理済み含む全status・FR-22)
②latches.group_candidate_id NULL化(RESTRICT解消) ③group候補 ④latches
クローズ(FR-19・matched解散と残存Intent復帰) ⑤calibration匿名化
(D-13第一段) ⑥Intent行(raw_text・structured_data・embeddingごと)。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT/RETURNING結果のUUID列復元(pgproto.UUID対策 — stage1と同じ)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


_DELETE_MATCH_CANDIDATES = text("""
    DELETE FROM match_candidates
     WHERE intent_a_id = CAST(:intent_id AS uuid)
        OR intent_b_id = CAST(:intent_id AS uuid)
""")  # 全status・処理済み含む(FR-22・design引用#1-②)

_DETACH_GROUP_CANDIDATES = text("""
    UPDATE latches SET group_candidate_id = NULL
     WHERE group_candidate_id IN (
         SELECT id FROM group_candidates
          WHERE CAST(:intent_id AS uuid) = ANY(intent_ids))
""")  # latches→group_candidates FK(RESTRICT)の解消。
# 閉じたlatchesのgroup_candidate_idは再評価で使わないためNULL化してよい

_DELETE_GROUP_CANDIDATES = text("""
    DELETE FROM group_candidates
     WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
""")

# D-13第一段(design §2.6): ID系NULL化+actual_responsesからuser_id除去。
# latch_id IS NOT NULL=未匿名化行のみ(冪等ガード)。anonymized_atは第二段。
_ANONYMIZE_CALIBRATION = text("""
    UPDATE calibration_records
       SET latch_id = NULL,
           intent_ids = NULL,
           actual_responses = COALESCE((
               SELECT jsonb_agg(e - 'user_id' ORDER BY ord)
                 FROM jsonb_array_elements(actual_responses)
                      WITH ORDINALITY AS t(e, ord)
           ), '[]'::jsonb),
           updated_at = CAST(:now AS timestamptz)
     WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
       AND latch_id IS NOT NULL
""")

_DELETE_INTENT = text("""
    DELETE FROM intents WHERE id = CAST(:intent_id AS uuid)
""")  # raw_text・structured_data・embedding(pgvector)ごと消える(引用#1-①)


async def cascade_delete_intent(
    conn: AsyncConnection, intent_id: uuid.UUID, now: datetime
) -> None:
    """1Intent分の削除カスケード。呼び出し元のtxに乗る(txは開かない)。"""
    await conn.execute(_DELETE_MATCH_CANDIDATES, {"intent_id": intent_id})
    await conn.execute(_DETACH_GROUP_CANDIDATES, {"intent_id": intent_id})
    await conn.execute(_DELETE_GROUP_CANDIDATES, {"intent_id": intent_id})
    await close_latches_on_delete(conn, intent_id, now)
    await conn.execute(_ANONYMIZE_CALIBRATION, {"intent_id": intent_id, "now": now})
    await conn.execute(_DELETE_INTENT, {"intent_id": intent_id})


# -- 以下、worker/stage1.py から中身不変で移設(M3 ws-1 design §2.7・引用#19・#20) --

_SELECT_OPEN_LATCHES_ON_DELETE = text("""
    SELECT id, status FROM latches
    WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
      AND status IN ('candidate', 'proposed', 'partial_accept')
    ORDER BY id
    FOR UPDATE
""")
_CANCEL_LATCH_ON_DELETE = text("""
    UPDATE latches SET status = 'cancelled'
    WHERE id = CAST(:latch_id AS uuid)
      AND status IN ('candidate', 'proposed', 'partial_accept')
    RETURNING id
""")
_SELECT_MATCHED_LATCHES_ON_DELETE = text("""
    SELECT id, intent_ids FROM latches
    WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
      AND status = 'matched'
    ORDER BY id
    FOR UPDATE
""")
_CANCEL_MATCHED_LATCH_ON_DELETE = text("""
    UPDATE latches SET status = 'cancelled'
    WHERE id = CAST(:latch_id AS uuid) AND status = 'matched'
    RETURNING id
""")
# 解散時の残る参加Intent復帰(引用#9: expires_at経過→expired・それ以外→active)
_RESTORE_INTENTS_ON_DISSOLVE = text("""
    UPDATE intents
    SET status = CASE WHEN expires_at <= CAST(:now AS timestamptz)
                      THEN 'expired' ELSE 'active' END,
        updated_at = CAST(:now AS timestamptz)
    WHERE id = ANY(CAST(:ids AS uuid[])) AND status = 'matched'
    RETURNING id, status
""")
# latch_status_events挿入(latch_engine._INSERT_LATCH_EVENTと同一SQL)
_INSERT_LATCH_EVENT_SQL = text("""
    INSERT INTO latch_status_events
        (latch_id, from_status, to_status, user_id, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:from_status AS text),
            :to_status, CAST(:user_id AS uuid), CAST(:now AS timestamptz))
""")


async def close_latches_on_delete(
    conn: AsyncConnection, intent_id: uuid.UUID, now
) -> None:
    """削除Event処理のlatches波及(M3 ws-1 design §2.7・FR-19)。

    1) 開いているlatches(candidate/proposed/partial_accept)→cancelled+events
    2) matched行→cancelled(解散)+events+残る参加Intentの復帰
       (削除されたIntent自身はcascadeの⑥で消えるため対象外)。
    イベントのuser_idはNULL(システム起因)。
    """
    open_rows = (
        await conn.execute(_SELECT_OPEN_LATCHES_ON_DELETE, {"intent_id": intent_id})
    ).fetchall()
    for latch_id, from_status in open_rows:
        res = await conn.execute(
            _CANCEL_LATCH_ON_DELETE, {"latch_id": _coerce_uuid(latch_id)}
        )
        if res.first() is None:
            continue  # 同一tx内で他経路が閉じた(通常ない防御)
        await conn.execute(
            _INSERT_LATCH_EVENT_SQL,
            {
                "latch_id": latch_id,
                "from_status": from_status,
                "to_status": "cancelled",
                "user_id": None,
                "now": now,
            },
        )
    matched_rows = (
        await conn.execute(_SELECT_MATCHED_LATCHES_ON_DELETE, {"intent_id": intent_id})
    ).fetchall()
    for latch_id, intent_ids in matched_rows:
        res = await conn.execute(
            _CANCEL_MATCHED_LATCH_ON_DELETE, {"latch_id": _coerce_uuid(latch_id)}
        )
        if res.first() is None:
            continue
        await conn.execute(
            _INSERT_LATCH_EVENT_SQL,
            {
                "latch_id": latch_id,
                "from_status": "matched",
                "to_status": "cancelled",
                "user_id": None,
                "now": now,
            },
        )
        rest = [i for i in (_coerce_uuid(x) for x in intent_ids) if i != intent_id]
        if rest:
            await conn.execute(_RESTORE_INTENTS_ON_DISSOLVE, {"ids": rest, "now": now})
