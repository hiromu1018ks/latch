"""latches永続化(text()生SQL・M3 ws-1 design §2.2・§2.3・§2.8・§2.11・§2.12)。

SQL定数+connを受け取るasync関数群(latch_engine/group_engine流儀。
トランザクションはserviceが統轄)。asyncpgのUUID復元は_coerce_uuid規律・
uuid[]のbindはlist[uuid.UUID]+SQL側CAST(group_calc.uuid_array §9-14規律)。
latchesにupdated_at列はなく、回答UPDATEが書くのはresponses・statusのみ
(design §2.2)。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.latches.calibration import EvalRow, PairEvalRow


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT/RETURNING結果のUUID列復元(asyncpgサブクラス対策・origin.pyと同一)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _jsonb(value: object) -> object:
    """JSONB列の読取値復元(strならjson.loads・worker/embedding.pyと同規律)。"""
    if isinstance(value, str):
        return json.loads(value)
    return value


@dataclass(frozen=True)
class LatchRow:
    """latches 1行(_SELECT_LATCH_FOR_UPDATE / _SELECT_LATCH の読取結果)。"""

    id: uuid.UUID
    status: str
    intent_ids: list[uuid.UUID]
    responses: list[dict]
    response_deadline: datetime
    expires_at: datetime
    score: Decimal
    proposal: dict
    group_candidate_id: uuid.UUID | None
    created_at: datetime
    completed_at: datetime | None = None


@dataclass(frozen=True)
class GeoRow:
    """fetch_intents_geoの1行(詳細APIの集合情報構築用)。"""

    intent_id: uuid.UUID
    time_start: datetime
    lon: float
    lat: float


@dataclass(frozen=True)
class ParticipantRow:
    """fetch_participantsの1行(成立後の解放情報・引用#21)。"""

    intent_id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    profile: dict


@dataclass(frozen=True)
class MessageRow:
    """messages 1行(挿入RETURNING・改頁選択の読取結果・design §2.1)。"""

    id: uuid.UUID
    latch_id: uuid.UUID
    sender_id: uuid.UUID
    body: str
    created_at: datetime


@dataclass(frozen=True)
class CalibrationAttendanceRow:
    """attendance事前読取の1行(行の有無と回答済みの区別用・design §2.3手順7)。"""

    actual_attended: bool | None


@dataclass(frozen=True)
class PageRow:
    """一覧の1行(design §2.8。target_timeはソートキー計算値)。"""

    id: uuid.UUID
    status: str
    intent_ids: list[uuid.UUID]
    responses: list[dict]
    response_deadline: datetime
    expires_at: datetime
    proposal: dict
    created_at: datetime
    completed_at: datetime | None
    group_candidate_id: uuid.UUID | None
    target_time: datetime


# -- 回答処理(design §2.2手順1〜4・06 §6の直列化方式) --

_SELECT_LATCH_FOR_UPDATE = text("""
    SELECT id, status, intent_ids, responses, response_deadline, expires_at,
           score, proposal, group_candidate_id, created_at, completed_at
    FROM latches WHERE id = CAST(:latch_id AS uuid)
    FOR UPDATE
""")

_SELECT_PARTICIPANT_INTENT = text("""
    SELECT id FROM intents
    WHERE id = ANY(CAST(:ids AS uuid[])) AND user_id = CAST(:me AS uuid)
""")

# 手順4: 条件付きUPDATE(06 §6のWHERE+参加Intent検査NOT EXISTS=design §2.2承認事項2)
_UPDATE_RESPONSE = text("""
    UPDATE latches
    SET responses = responses || CAST(:item AS jsonb),
        status = :new_status
    WHERE id = CAST(:latch_id AS uuid)
      AND status IN ('proposed', 'partial_accept')
      AND response_deadline > CAST(:now AS timestamptz)
      AND expires_at > CAST(:now AS timestamptz)
      AND NOT EXISTS (
          SELECT 1 FROM intents i
          WHERE i.id = ANY(latches.intent_ids)
            AND i.status NOT IN ('active', 'paused'))
    RETURNING id, status
""")

# Intent matched化(active+paused=承認事項3・design §2.3)
_MATCH_INTENTS = text("""
    UPDATE intents SET status = 'matched', updated_at = CAST(:now AS timestamptz)
    WHERE id = ANY(CAST(:ids AS uuid[])) AND status IN ('active', 'paused')
    RETURNING id
""")

# 競合クローズ対象(引用#7・design §2.3。ORDER BY id でロック順序を固定し
# 回答tx同士のデッドロック交差を構造的に排除 — 本計画§9-1)
_SELECT_CONFLICTING_LATCHES = text("""
    SELECT id, status FROM latches
    WHERE id <> CAST(:self_id AS uuid)
      AND status IN ('candidate', 'proposed', 'partial_accept')
      AND intent_ids && CAST(:member_ids AS uuid[])
    ORDER BY id
    FOR UPDATE
""")

_CANCEL_LATCH = text("""
    UPDATE latches SET status = 'cancelled'
    WHERE id = CAST(:latch_id AS uuid)
      AND status IN ('candidate', 'proposed', 'partial_accept')
    RETURNING id
""")

_INSERT_LATCH_EVENT = text("""
    INSERT INTO latch_status_events
        (latch_id, from_status, to_status, user_id, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:from_status AS text),
            :to_status, CAST(:user_id AS uuid), CAST(:now AS timestamptz))
""")

# -- Calibration(design §2.5・引用#13) --

_INSERT_CALIBRATION = text("""
    INSERT INTO calibration_records
        (latch_id, intent_ids, prediction, proposal_snapshot,
         actual_responses, matched, created_at, updated_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:intent_ids AS uuid[]),
            CAST(:prediction AS jsonb), CAST(:proposal AS jsonb),
            CAST(:responses AS jsonb), :matched,
            CAST(:now AS timestamptz), CAST(:now AS timestamptz))
    RETURNING id
""")

# -- 評価行特定(design §2.11・§2.12) --

_SELECT_PAIR_ROWS = text("""
    SELECT jev_result, latch_score, updated_at
    FROM match_candidates
    WHERE intent_a_id = CAST(:a AS uuid) AND intent_b_id = CAST(:b AS uuid)
      AND jev_result IS NOT NULL
    ORDER BY updated_at DESC
""")

_SELECT_GROUP_PAIRS = text("""
    SELECT intent_a_id, intent_b_id, intent_a_version, intent_b_version, jev_result
    FROM match_candidates
    WHERE intent_a_id = ANY(CAST(:ids AS uuid[]))
      AND intent_b_id = ANY(CAST(:ids AS uuid[]))
      AND jev_result IS NOT NULL
    ORDER BY intent_a_id, intent_b_id, updated_at DESC
""")

_SELECT_MEMBER_SCORES = text("""
    SELECT member_scores FROM group_candidates WHERE id = CAST(:gid AS uuid)
""")

# -- 読取系(一覧・詳細・解放情報・design §2.8) --

_SELECT_USER_ID = text("""
    SELECT id FROM users
    WHERE auth_provider = :provider AND auth_subject = :subject
""")

_SELECT_LATCHES_PAGE = text("""
    SELECT l.id, l.status, l.intent_ids, l.responses, l.response_deadline,
           l.expires_at, l.proposal, l.created_at, l.completed_at,
           l.group_candidate_id,
           (SELECT max(i.time_start) FROM intents i
            WHERE i.id = ANY(l.intent_ids)) AS target_time
    FROM latches l
    WHERE l.status <> 'candidate'
      AND EXISTS (SELECT 1 FROM intents i
                  WHERE i.id = ANY(l.intent_ids)
                    AND i.user_id = CAST(:me AS uuid))
      AND (CAST(:tt AS timestamptz) IS NULL OR
           (SELECT max(i.time_start) FROM intents i
            WHERE i.id = ANY(l.intent_ids)) > CAST(:tt AS timestamptz)
           OR ((SELECT max(i.time_start) FROM intents i
                WHERE i.id = ANY(l.intent_ids)) = CAST(:tt AS timestamptz)
               AND (l.created_at < CAST(:ct AS timestamptz)
                    OR (l.created_at = CAST(:ct AS timestamptz)
                        AND l.id > CAST(:lid AS uuid)))))
    ORDER BY target_time ASC, l.created_at DESC, l.id ASC
    LIMIT :limit
""")

_SELECT_LATCH = text("""
    SELECT id, status, intent_ids, responses, response_deadline, expires_at,
           score, proposal, group_candidate_id, created_at, completed_at
    FROM latches WHERE id = CAST(:latch_id AS uuid)
""")

_SELECT_INTENTS_STRUCTURED = text("""
    SELECT id, structured_data FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

_SELECT_INTENTS_GEO = text("""
    SELECT id, time_start,
           ST_X(geo_center::geometry) AS lon, ST_Y(geo_center::geometry) AS lat
    FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

_SELECT_PARTICIPANTS = text("""
    SELECT i.id AS intent_id, i.user_id, u.display_name, u.profile
    FROM intents i JOIN users u ON u.id = i.user_id
    WHERE i.id = ANY(CAST(:ids AS uuid[]))
""")

# -- チャット(M3 ws-4 design §2.1・§2.2) --

_INSERT_MESSAGE = text("""
    INSERT INTO messages (latch_id, sender_id, body, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:sender_id AS uuid), :body,
            CAST(:now AS timestamptz))
    RETURNING id, latch_id, sender_id, body, created_at
""")

# GET改頁のキーセット(latch_id絞り+(created_at,id)昇順・design §2.1)
_SELECT_MESSAGES_PAGE = text("""
    SELECT id, latch_id, sender_id, body, created_at
    FROM messages
    WHERE latch_id = CAST(:latch_id AS uuid)
      AND (CAST(:ct AS timestamptz) IS NULL
           OR created_at > CAST(:ct AS timestamptz)
           OR (created_at = CAST(:ct AS timestamptz)
               AND id > CAST(:mid AS uuid)))
    ORDER BY created_at ASC, id ASC
    LIMIT :limit
""")

# 送信時のblocks判定対象(自分以外の参加者・design §2.2)
_SELECT_PARTICIPANT_USER_IDS = text("""
    SELECT user_id FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

# blocks双方向判定(引用#7・design §2.2。ws-5のRedisキャッシュ差し替え点)
_SELECT_BLOCK_BETWEEN = text("""
    SELECT EXISTS (
        SELECT 1 FROM blocks
        WHERE (blocker_id = CAST(:me AS uuid)
               AND blocked_id = ANY(CAST(:others AS uuid[])))
           OR (blocker_id = ANY(CAST(:others AS uuid[]))
               AND blocked_id = CAST(:me AS uuid))
    )
""")

# -- 実施自己申告(M3 ws-4 design §2.3) --

_SELECT_CALIBRATION_ATTENDANCE = text("""
    SELECT actual_attended FROM calibration_records
    WHERE latch_id = CAST(:latch_id AS uuid)
""")

# 手順6: 条件付きUPDATE(二重回答の排他の本体・design §2.3)
_UPDATE_ATTENDANCE = text("""
    UPDATE calibration_records
    SET actual_attended = :attended,
        cancelled_after = NOT :attended,
        updated_at = CAST(:now AS timestamptz)
    WHERE latch_id = CAST(:latch_id AS uuid)
      AND actual_attended IS NULL
    RETURNING id
""")


def _latch_row(mapping) -> LatchRow:
    """_SELECT_LATCH(_FOR_UPDATE)のmappings行→LatchRow。"""
    return LatchRow(
        id=_coerce_uuid(mapping["id"]),
        status=mapping["status"],
        intent_ids=[_coerce_uuid(x) for x in mapping["intent_ids"]],
        responses=list(_jsonb(mapping["responses"]) or []),
        response_deadline=mapping["response_deadline"],
        expires_at=mapping["expires_at"],
        score=(
            mapping["score"]
            if isinstance(mapping["score"], Decimal)
            else Decimal(str(mapping["score"]))
        ),
        proposal=dict(_jsonb(mapping["proposal"]) or {}),
        group_candidate_id=(
            _coerce_uuid(mapping["group_candidate_id"])
            if mapping["group_candidate_id"] is not None
            else None
        ),
        created_at=mapping["created_at"],
        completed_at=mapping["completed_at"],
    )


async def fetch_user_id(
    conn: AsyncConnection, provider: str, subject: str
) -> uuid.UUID | None:
    """認証subject→users.id。未登録JWT=None(呼び出し側は404・引用#17)。"""
    res = await conn.execute(
        _SELECT_USER_ID, {"provider": provider, "subject": subject}
    )
    row = res.first()
    return _coerce_uuid(row[0]) if row is not None else None


async def select_latch_for_update(
    conn: AsyncConnection, latch_id: uuid.UUID
) -> LatchRow | None:
    """手順1: 行ロックつき読取(design §2.2)。"""
    row = (
        (await conn.execute(_SELECT_LATCH_FOR_UPDATE, {"latch_id": latch_id}))
        .mappings()
        .first()
    )
    return _latch_row(row) if row is not None else None


async def select_latch(conn: AsyncConnection, latch_id: uuid.UUID) -> LatchRow | None:
    """詳細API用の読取専用行(FOR UPDATEなし)。"""
    row = (await conn.execute(_SELECT_LATCH, {"latch_id": latch_id})).mappings().first()
    return _latch_row(row) if row is not None else None


async def select_participant_intent(
    conn: AsyncConnection, intent_ids: list[uuid.UUID], user_id: uuid.UUID
) -> uuid.UUID | None:
    """手順2a: 本人の参加Intent特定(0行=None→呼び出し側403・引用#17)。"""
    res = await conn.execute(
        _SELECT_PARTICIPANT_INTENT, {"ids": list(intent_ids), "me": user_id}
    )
    row = res.first()
    return _coerce_uuid(row[0]) if row is not None else None


async def update_response(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    item: dict,
    new_status: str,
    now: datetime,
) -> tuple[uuid.UUID, str] | None:
    """手順4: 条件付きUPDATE。None=影響0(呼び出し側は事前検査の分類で409)。"""
    res = await conn.execute(
        _UPDATE_RESPONSE,
        {
            "latch_id": latch_id,
            "item": json.dumps(item, ensure_ascii=False),
            "new_status": new_status,
            "now": now,
        },
    )
    row = res.first()
    if row is None:
        return None
    return _coerce_uuid(row[0]), row[1]


async def match_intents(
    conn: AsyncConnection, intent_ids: list[uuid.UUID], now: datetime
) -> list[uuid.UUID]:
    """成立時の参加Intent matched化(引用#9・design §2.3)。"""
    res = await conn.execute(_MATCH_INTENTS, {"ids": list(intent_ids), "now": now})
    return [_coerce_uuid(r[0]) for r in res.fetchall()]


async def select_conflicting_latches(
    conn: AsyncConnection, self_id: uuid.UUID, member_ids: list[uuid.UUID]
) -> list[tuple[uuid.UUID, str]]:
    """競合クローズ対象の行ロック取得(ORDER BY id・§9-1)。"""
    res = await conn.execute(
        _SELECT_CONFLICTING_LATCHES,
        {"self_id": self_id, "member_ids": list(member_ids)},
    )
    return [(_coerce_uuid(r[0]), r[1]) for r in res.fetchall()]


async def cancel_latch(conn: AsyncConnection, latch_id: uuid.UUID) -> bool:
    """競合クローズ1行の条件付きcancelled UPDATE(競合負けはFalse)。"""
    res = await conn.execute(_CANCEL_LATCH, {"latch_id": latch_id})
    return res.first() is not None


async def insert_latch_event(
    conn: AsyncConnection,
    latch_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
    user_id: uuid.UUID | None,
    now: datetime,
) -> None:
    """latch_status_events挿入(回答起因=user_id・システム起因=None・引用#12)。"""
    await conn.execute(
        _INSERT_LATCH_EVENT,
        {
            "latch_id": latch_id,
            "from_status": from_status,
            "to_status": to_status,
            "user_id": user_id,
            "now": now,
        },
    )


async def insert_calibration(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    intent_ids: list[uuid.UUID],
    prediction: dict,
    proposal: dict,
    responses: list[dict],
    matched: bool,
    now: datetime,
) -> uuid.UUID:
    """calibration_records INSERT(design §2.5)。"""
    res = await conn.execute(
        _INSERT_CALIBRATION,
        {
            "latch_id": latch_id,
            "intent_ids": list(intent_ids),
            "prediction": json.dumps(prediction, ensure_ascii=False),
            "proposal": json.dumps(proposal, ensure_ascii=False),
            "responses": json.dumps(responses, ensure_ascii=False),
            "matched": matched,
            "now": now,
        },
    )
    return _coerce_uuid(res.first()[0])


async def fetch_pair_rows(
    conn: AsyncConnection, a_id: uuid.UUID, b_id: uuid.UUID
) -> list[EvalRow]:
    """1対1の評価行(jev_resultあり・updated_at降順・design §2.11)。"""
    res = await conn.execute(_SELECT_PAIR_ROWS, {"a": a_id, "b": b_id})
    out: list[EvalRow] = []
    for r in res.fetchall():
        jev = _jsonb(r[0])
        out.append(
            EvalRow(
                jev_result=jev if isinstance(jev, dict) else {},
                latch_score=(
                    r[1]
                    if r[1] is None or isinstance(r[1], Decimal)
                    else Decimal(str(r[1]))
                ),
                updated_at=r[2],
            )
        )
    return out


async def fetch_group_pairs(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[PairEvalRow]:
    """集合内ペアの評価行(jev_resultあり・design §2.12)。"""
    res = await conn.execute(_SELECT_GROUP_PAIRS, {"ids": list(ids)})
    out: list[PairEvalRow] = []
    for r in res.fetchall():
        jev = _jsonb(r[4])
        out.append(
            PairEvalRow(
                a=_coerce_uuid(r[0]),
                b=_coerce_uuid(r[1]),
                va=r[2],
                vb=r[3],
                jev_result=jev if isinstance(jev, dict) else {},
            )
        )
    return out


async def fetch_member_scores(conn: AsyncConnection, gid: uuid.UUID) -> dict | None:
    """group_candidates.member_scores({"seed_id","versions"}・STATUS引継ぎ③)。"""
    res = await conn.execute(_SELECT_MEMBER_SCORES, {"gid": gid})
    row = res.first()
    if row is None:
        return None
    ms = _jsonb(row[0])
    return ms if isinstance(ms, dict) else {}


async def fetch_intents_structured(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[dict]:
    """参加Intentのstructured_data(ids順に並べ替え・segment材料)。"""
    res = await conn.execute(_SELECT_INTENTS_STRUCTURED, {"ids": list(ids)})
    by_id: dict[uuid.UUID, dict] = {}
    for r in res.fetchall():
        sd = _jsonb(r[1])
        by_id[_coerce_uuid(r[0])] = sd if isinstance(sd, dict) else {}
    return [by_id[i] for i in ids if i in by_id]


async def fetch_intents_geo(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[GeoRow]:
    """参加Intentのtime_start・geo中心(ids順・詳細APIの集合情報)。"""
    res = await conn.execute(_SELECT_INTENTS_GEO, {"ids": list(ids)})
    by_id: dict[uuid.UUID, GeoRow] = {}
    for r in res.fetchall():
        by_id[_coerce_uuid(r[0])] = GeoRow(
            intent_id=_coerce_uuid(r[0]),
            time_start=r[1],
            lon=float(r[2]),
            lat=float(r[3]),
        )
    return [by_id[i] for i in ids if i in by_id]


async def fetch_participants(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[ParticipantRow]:
    """参加者の表示名・profile(ids順・成立後の解放情報・引用#21)。"""
    res = await conn.execute(_SELECT_PARTICIPANTS, {"ids": list(ids)})
    by_id: dict[uuid.UUID, ParticipantRow] = {}
    for r in res.fetchall():
        profile = _jsonb(r[3])
        by_id[_coerce_uuid(r[0])] = ParticipantRow(
            intent_id=_coerce_uuid(r[0]),
            user_id=_coerce_uuid(r[1]),
            display_name=r[2],
            profile=profile if isinstance(profile, dict) else {},
        )
    return [by_id[i] for i in ids if i in by_id]


async def select_latches_page(
    conn: AsyncConnection,
    *,
    me: uuid.UUID,
    before: tuple[datetime, datetime, uuid.UUID] | None,
    limit: int,
) -> list[PageRow]:
    """一覧(design §2.8)。before=cursor位置(target_time, created_at, id)。"""
    params: dict = {
        "me": me,
        "tt": before[0] if before else None,
        "ct": before[1] if before else None,
        "lid": before[2] if before else None,
        "limit": limit,
    }
    res = await conn.execute(_SELECT_LATCHES_PAGE, params)
    out: list[PageRow] = []
    for r in res.fetchall():
        out.append(
            PageRow(
                id=_coerce_uuid(r[0]),
                status=r[1],
                intent_ids=[_coerce_uuid(x) for x in r[2]],
                responses=list(_jsonb(r[3]) or []),
                response_deadline=r[4],
                expires_at=r[5],
                proposal=dict(_jsonb(r[6]) or {}),
                created_at=r[7],
                completed_at=r[8],
                group_candidate_id=r[9],
                target_time=r[10],
            )
        )
    return out


def _message_row(mapping) -> MessageRow:
    """messages行→MessageRow(UUID復元)。"""
    return MessageRow(
        id=_coerce_uuid(mapping["id"]),
        latch_id=_coerce_uuid(mapping["latch_id"]),
        sender_id=_coerce_uuid(mapping["sender_id"]),
        body=mapping["body"],
        created_at=mapping["created_at"],
    )


async def fetch_participant_user_ids(
    conn: AsyncConnection, intent_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """参加Intent→user_id一覧(送信時のblocks判定対象・design §2.2)。"""
    res = await conn.execute(_SELECT_PARTICIPANT_USER_IDS, {"ids": list(intent_ids)})
    return [_coerce_uuid(r[0]) for r in res.fetchall()]


async def select_block_between(
    conn: AsyncConnection, me: uuid.UUID, others: list[uuid.UUID]
) -> bool:
    """自分と参加相手の間のblocks双方向判定(引用#7・design §2.2)。

    ws-5がRedisキャッシュ差し替え点として使う(本実装はDB直読み)。
    """
    res = await conn.execute(_SELECT_BLOCK_BETWEEN, {"me": me, "others": list(others)})
    return bool(res.scalar())


async def insert_message(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    sender_id: uuid.UUID,
    body: str,
    now: datetime,
) -> MessageRow:
    """messages挿入(design §2.1手順6)。idはDB DEFAULT→RETURNINGで受け取る。"""
    res = await conn.execute(
        _INSERT_MESSAGE,
        {"latch_id": latch_id, "sender_id": sender_id, "body": body, "now": now},
    )
    row = res.mappings().first()
    assert row is not None
    return _message_row(row)


async def select_messages_page(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    before: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[MessageRow]:
    """messages改頁選択(design §2.1)。before=(created_at, id)・昇順。"""
    res = await conn.execute(
        _SELECT_MESSAGES_PAGE,
        {
            "latch_id": latch_id,
            "ct": before[0] if before else None,
            "mid": before[1] if before else None,
            "limit": limit,
        },
    )
    return [_message_row(r) for r in res.mappings().all()]


async def select_calibration_attendance(
    conn: AsyncConnection, latch_id: uuid.UUID
) -> CalibrationAttendanceRow | None:
    """attendance事前読取(design §2.3手順7)。None=行そのものがない。"""
    row = (
        await conn.execute(_SELECT_CALIBRATION_ATTENDANCE, {"latch_id": latch_id})
    ).first()
    if row is None:
        return None
    return CalibrationAttendanceRow(actual_attended=row[0])


async def update_attendance(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    attended: bool,
    now: datetime,
) -> bool:
    """手順6: 条件付きUPDATE。False=影響0行(呼び出し側はAlreadySubmitted)。"""
    res = await conn.execute(
        _UPDATE_ATTENDANCE,
        {"latch_id": latch_id, "attended": attended, "now": now},
    )
    return res.first() is not None
