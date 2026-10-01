"""Layer 5 LATCH Engine本体(06 §6・§10・design §2.1案A・§2.2〜2.5・§2.9の表)。

latch_score計算(L = H × MutualScore × C)・latches行生成/昇格・D-07再提案
制御・nearby存在通知・D-08上限検査・75分ルール・D-05回答期限式・提示順drain。
永続化はtext()生SQLのみ(jev.pyと同一形式)。時刻はClock経由のみ。
ws-7: 1対1経路はグループ所属ペアを除外(_SELECT_TARGETS)し、try_promoteは
|S|人(2〜4要素)へ対応・グループlatchesのみD-06重複上位チェックを行う。
_evaluate_pairは読取tx前+書込tx1の統合構成(I-1対策・承認事項4)。
intent_ids正規化はsorted(集合側も同様に正規化する)。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.worker.matching import group_calc, latch_calc, layer4
from latch.worker.matching import origin as origin_mod
from latch.worker.matching import proposal as proposal_mod
from latch.worker.matching.proposal import LatchIntentInputs

logger = logging.getLogger(__name__)

NOTIFICATION_PROPOSAL = "proposal"
NOTIFICATION_NEARBY = "nearby_candidate"

# フェーズ1: 起点に紐づく「計算済みでない評価行」(design §2.2。
# 起点version一致・latch_score IS NULL(冪等ガード)・相手version=相手現行のEXISTS。
# グループ所属ペアは対象外(design §2.7-1 — 集合評価と1対1評価の混線防止)
_SELECT_TARGETS = text(f"""
    SELECT mc.id, mc.intent_a_id, mc.intent_b_id, mc.intent_a_version,
           mc.intent_b_version, mc.jev_result, mc.cheap_judge_score
    FROM match_candidates mc
    WHERE (mc.intent_a_id = CAST(:origin AS uuid)
           OR mc.intent_b_id = CAST(:origin AS uuid))
      AND (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                THEN mc.intent_a_version
                ELSE mc.intent_b_version END) = :origin_version
      AND mc.status = 'evaluated'
      AND mc.jev_result IS NOT NULL
      AND mc.latch_score IS NULL
      AND EXISTS (
          SELECT 1 FROM intents p
          WHERE p.id = (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                             THEN mc.intent_b_id ELSE mc.intent_a_id END)
            AND p.version = (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                                  THEN mc.intent_b_version
                                  ELSE mc.intent_a_version END))
      AND NOT {layer4.GROUP_PAIR_EXISTS}
    ORDER BY mc.cheap_judge_score DESC NULLS LAST,
             (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                   THEN mc.intent_b_id ELSE mc.intent_a_id END) ASC
""")

# H再検証不成立行のclose(WHEREにstatus条件 — design §2.2-1)
_CLOSE_H_BROKEN = text("""
    UPDATE match_candidates
    SET status = 'closed', updated_at = :now
    WHERE id = CAST(:row_id AS uuid) AND status = 'evaluated'
    RETURNING id
""")

# 退避つきlatch_score UPDATE(design §2.2-4・06 §10手順1〜2。RETURNINGの
# prev_latch_scoreは退避された旧値(初回計算はNULL=手順3「prevがNULLなら
# 無条件に変化あり」を自然に作る)。行数0=他の実行が先に計算済み)
_RECORD_SCORE = text("""
    UPDATE match_candidates
    SET prev_latch_score = latch_score,
        prev_evaluated_at = updated_at,
        latch_score = :score,
        updated_at = :now
    WHERE id = CAST(:row_id AS uuid) AND latch_score IS NULL
    RETURNING id, prev_latch_score
""")

# proposal入力・try_promote参加者読取(design §2.5。visibility・
# notification_levelは0001のCHECKつき列・category_secondaryはstructured_data内)
_SELECT_INTENT_INPUTS = text("""
    SELECT id, user_id, visibility, notification_level, time_start,
           expires_at, budget_max, category_primary, structured_data,
           ST_X(geo_center::geometry) AS lon, ST_Y(geo_center::geometry) AS lat
    FROM intents WHERE id = CAST(:intent_id AS uuid)
""")

# D-07履歴(同一組み合わせの全latches・responses空は除外。design §2.4-1)
_SELECT_LATCH_RESPONSES = text("""
    SELECT responses FROM latches
    WHERE intent_ids = ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]
      AND responses <> CAST('[]' AS jsonb)
""")

# latches生成(0004部分UNIQUE索引へON CONFLICT・design §2.3)
_INSERT_LATCH = text("""
    INSERT INTO latches
        (intent_ids, proposal, score, status, response_deadline, expires_at, created_at)
    VALUES (ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[],
            CAST(:proposal AS jsonb), :score, 'candidate', :deadline, :expires, :now)
    ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed', 'partial_accept')
    DO NOTHING
    RETURNING id
""")

# ON CONFLICTで飛んだ場合の既存開いている行特定(design §2.3)
_FIND_OPEN_LATCH = text("""
    SELECT id, status FROM latches
    WHERE intent_ids = ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[]
      AND status IN ('candidate', 'proposed', 'partial_accept')
""")

# 昇格時のcandidate行更新(score/proposal/deadlineの3列のみ — latchesに
# updated_at列なし。design §2.3)
_UPDATE_FOR_PROMOTION = text("""
    UPDATE latches
    SET score = :score, proposal = CAST(:proposal AS jsonb),
        response_deadline = :deadline
    WHERE id = CAST(:latch_id AS uuid) AND status = 'candidate'
    RETURNING id
""")

# latch_status_events挿入(from_status・user_idはNULL可 — 05 §2・引用#16)
_INSERT_LATCH_EVENT = text("""
    INSERT INTO latch_status_events
        (latch_id, from_status, to_status, user_id, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:from_status AS text),
            :to_status, CAST(:user_id AS uuid), :now)
""")

# notifications INSERT(payloadは{"latch_id"}の最小参照 — 承認事項2)
_INSERT_NOTIFICATION = text("""
    INSERT INTO notifications (user_id, type, payload, created_at)
    VALUES (CAST(:user_id AS uuid), :type, CAST(:payload AS jsonb), :now)
""")

# D-08日次上限カウント(真実はnotifications・design §2.6。0時リセットは
# day_start/day_nextの日付条件の動的切り替えで成立 — カウンタリセットジョブ不要。
# |S|人対応: user_id = ANY(uuid[])(design §2.7-2)
_COUNT_DAILY_NOTIFICATIONS = text("""
    SELECT user_id, COUNT(*) FROM notifications
    WHERE user_id = ANY(CAST(:users AS uuid[]))
      AND type IN ('proposal', 'nearby_candidate')
      AND created_at >= CAST(:day_start AS timestamptz)
      AND created_at < CAST(:day_next AS timestamptz)
    GROUP BY user_id
""")

# D-08同時進行上限(muted参加Intentもproposed数に計上 — 引用#10)
_COUNT_OPEN_PROPOSED = text("""
    SELECT COUNT(*) FROM latches
    WHERE status IN ('proposed', 'partial_accept')
      AND intent_ids @> ARRAY[CAST(:intent_id AS uuid)]
""")

# try_promote手順1: 行ロック(design §2.6・06 §6の直列化方式。
# ws-7: group_candidate_id・scoreを追加 — D-06上位チェック用)
_SELECT_LATCH_FOR_UPDATE = text("""
    SELECT id, status, intent_ids, expires_at, group_candidate_id, score
    FROM latches WHERE id = CAST(:latch_id AS uuid)
    FOR UPDATE
""")

# D-06通知順序: メンバーが重なる開いている集合(design §2.6)
_SELECT_HIGHER_GROUP_LATCH = text("""
    SELECT l.score, l.intent_ids
    FROM latches l
    WHERE l.status IN ('candidate', 'proposed', 'partial_accept')
      AND l.group_candidate_id IS NOT NULL
      AND l.id <> CAST(:self AS uuid)
      AND l.intent_ids && CAST(:my_ids AS uuid[])
""")

# 75分/deadline<=nowの破棄(条件付きUPDATE — design §2.6手順2)
_EXPIRE_LATCH = text("""
    UPDATE latches SET status = 'expired'
    WHERE id = CAST(:latch_id AS uuid) AND status = 'candidate'
    RETURNING id
""")

# proposed遷移(提示時D-05再計算・条件付きUPDATE — design §2.6手順6)
_PROMOTE_LATCH = text("""
    UPDATE latches
    SET status = 'proposed', response_deadline = CAST(:deadline AS timestamptz)
    WHERE id = CAST(:latch_id AS uuid) AND status = 'candidate'
    RETURNING id
""")

# drain: 提示順(対象時刻昇順・score降順・同点latch_id昇順)。nearby行
# (score < 閾値)は対象外・期限切れ行は対象外(design §2.6)
_DRAIN_CANDIDATES = text("""
    SELECT l.id,
           (SELECT max(i.time_start) FROM intents i
            WHERE i.id = ANY(l.intent_ids)) AS target_time
    FROM latches l
    WHERE l.status = 'candidate'
      AND l.expires_at > CAST(:now AS timestamptz)
      AND l.score >= CAST(:threshold AS numeric)
    ORDER BY target_time ASC, l.score DESC, l.id ASC
""")


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策・origin.pyと同一規律)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


@dataclass(frozen=True)
class LatchTargetRow:
    """_SELECT_TARGETSの1行。"""

    row_id: uuid.UUID
    intent_a_id: uuid.UUID
    intent_b_id: uuid.UUID
    jev_result: dict  # _parse_structured済み(Gateway検証済みの生JSONB読取のみ)


@dataclass(frozen=True)
class Participant:
    """try_promote用の参加Intent情報(同一tx内で再読取)。"""

    intent_id: uuid.UUID
    user_id: uuid.UUID
    notification_level: str
    time_start: datetime
    expires_at: datetime


async def _select_target_rows(
    conn, origin_id: uuid.UUID, origin_version: int
) -> list[LatchTargetRow]:
    """フェーズ1: 起点に紐づく計算済みでない評価行(design §2.2)。"""
    res = await conn.execute(
        _SELECT_TARGETS, {"origin": origin_id, "origin_version": origin_version}
    )
    rows = res.mappings().all()
    out: list[LatchTargetRow] = []
    for r in rows:
        jev = r["jev_result"]
        if isinstance(jev, str):
            jev = json.loads(jev)
        out.append(
            LatchTargetRow(
                row_id=_coerce_uuid(r["id"]),
                intent_a_id=_coerce_uuid(r["intent_a_id"]),
                intent_b_id=_coerce_uuid(r["intent_b_id"]),
                jev_result=jev,
            )
        )
    return out


async def _read_intent_inputs(engine: AsyncEngine, intent_id: uuid.UUID):
    """proposal入力の1Intent分(tx外の短tx内包・design §2.5)。

    expires_atがNULLの行はNone(latches.expires_at NOT NULLのため対象外)。
    structured_dataがstrならjson.loads(embedding.pyと同一規律)。
    """
    async with engine.begin() as conn:
        row = (
            (await conn.execute(_SELECT_INTENT_INPUTS, {"intent_id": intent_id}))
            .mappings()
            .first()
        )
    if row is None or row["expires_at"] is None:
        return None
    sd = row["structured_data"]
    if isinstance(sd, str):
        sd = json.loads(sd)
    # structured_dataの保存形式は平らなcategory_secondaryキー(05 §2の5キー。
    # mapping.py _structured_dataと対応 — API入力のcategory.secondary入れ子ではない)
    secondary = sd.get("category_secondary") if isinstance(sd, dict) else None
    return LatchIntentInputs(
        intent_id=_coerce_uuid(row["id"]),
        user_id=_coerce_uuid(row["user_id"]),
        visibility=row["visibility"],
        notification_level=row["notification_level"],
        time_start=row["time_start"],
        expires_at=row["expires_at"],
        budget_max=row["budget_max"],
        category_primary=row["category_primary"],
        category_secondary=secondary,
        geo_lon=float(row["lon"]),
        geo_lat=float(row["lat"]),
    )


async def _close_h_broken(conn, row_id: uuid.UUID, now) -> bool:
    """H再検証不成立行のclose(WHEREにstatus条件)。RETURNINGなければFalse。"""
    res = await conn.execute(_CLOSE_H_BROKEN, {"row_id": row_id, "now": now})
    return res.first() is not None


async def _record_score(
    conn, row_id: uuid.UUID, score: float, now
) -> tuple[uuid.UUID, float | None] | None:
    """退避つきlatch_score UPDATE。None=競合負け(latch_score計算済み)。"""
    res = await conn.execute(
        _RECORD_SCORE, {"row_id": row_id, "score": score, "now": now}
    )
    row = res.first()
    if row is None:
        return None
    return (_coerce_uuid(row[0]), float(row[1]) if row[1] is not None else None)


async def _read_latch_responses(
    engine: AsyncEngine, a_id: uuid.UUID, b_id: uuid.UUID
) -> list[dict]:
    """D-07履歴(同一組み合わせの全latches・responses空は除外・design §2.4-1)。"""
    async with engine.begin() as conn:
        res = await conn.execute(_SELECT_LATCH_RESPONSES, {"a": a_id, "b": b_id})
        rows = res.fetchall()
    merged: list[dict] = []
    for row in rows:
        responses = row[0]
        if isinstance(responses, str):
            responses = json.loads(responses)
        if responses:
            merged.extend(responses)
    return merged


async def _insert_latch(
    conn,
    *,
    a_id: uuid.UUID,
    b_id: uuid.UUID,
    proposal: dict,
    score: float,
    deadline,
    expires,
    now,
) -> uuid.UUID | None:
    """latches INSERT(ON CONFLICT DO NOTHING)。None=開いている行あり。"""
    res = await conn.execute(
        _INSERT_LATCH,
        {
            "a": a_id,
            "b": b_id,
            "proposal": json.dumps(proposal),
            "score": score,
            "deadline": deadline,
            "expires": expires,
            "now": now,
        },
    )
    row = res.first()
    return _coerce_uuid(row[0]) if row is not None else None


async def _find_open_latch(
    conn, a_id: uuid.UUID, b_id: uuid.UUID
) -> tuple[uuid.UUID, str] | None:
    """ON CONFLICTで飛んだ場合の既存開いている行特定。"""
    res = await conn.execute(_FIND_OPEN_LATCH, {"a": a_id, "b": b_id})
    row = res.first()
    if row is None:
        return None
    return (_coerce_uuid(row[0]), row[1])


async def _update_for_promotion(
    conn,
    latch_id: uuid.UUID,
    *,
    score: float,
    proposal: dict,
    deadline,
) -> bool:
    """昇格時のcandidate行更新(score/proposal/deadlineの3列のみ)。"""
    res = await conn.execute(
        _UPDATE_FOR_PROMOTION,
        {
            "latch_id": latch_id,
            "score": score,
            "proposal": json.dumps(proposal),
            "deadline": deadline,
        },
    )
    return res.first() is not None


async def _insert_latch_event(
    conn,
    latch_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
    user_id: uuid.UUID | None,
    now,
) -> None:
    """latch_status_events挿入(システム起因=Layer 5はuser_id=NULL)。"""
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


async def _insert_notification(
    conn, user_id: uuid.UUID, ntype: str, latch_id: uuid.UUID, now
) -> None:
    """notifications INSERT(payloadは{"latch_id"}の最小参照 — 承認事項2)。"""
    await conn.execute(
        _INSERT_NOTIFICATION,
        {
            "user_id": user_id,
            "type": ntype,
            "payload": json.dumps({"latch_id": str(latch_id)}),
            "now": now,
        },
    )


async def _count_daily_notifications(
    conn, user_ids: list[uuid.UUID], day_start, day_next
) -> dict[uuid.UUID, int]:
    """D-08日次上限カウント(真実はnotifications・design §2.6・0時リセットは
    日付条件の切替で成立)。user_idsは0〜4要素(2者または3〜4者の参加者)。
    空listはSQLを実行せず{}(上限消費なし)。"""
    if not user_ids:
        return {}
    res = await conn.execute(
        _COUNT_DAILY_NOTIFICATIONS,
        {
            "users": group_calc.uuid_array(user_ids),
            "day_start": day_start,
            "day_next": day_next,
        },
    )
    rows = res.fetchall()
    return {_coerce_uuid(uid): int(cnt) for uid, cnt in rows}


async def _count_open_proposed(conn, intent_id: uuid.UUID) -> int:
    """D-08同時進行上限カウント(開いているproposed/partial_accept件数)。"""
    res = await conn.execute(_COUNT_OPEN_PROPOSED, {"intent_id": intent_id})
    return int(res.scalar_one())


async def _select_latch_for_update(conn, latch_id: uuid.UUID) -> tuple | None:
    """try_promote手順1: 行ロック(id, status, intent_ids, expires_at,
    group_candidate_id, score)。"""
    res = await conn.execute(_SELECT_LATCH_FOR_UPDATE, {"latch_id": latch_id})
    row = res.first()
    if row is None:
        return None
    return (
        _coerce_uuid(row[0]),
        row[1],
        [_coerce_uuid(x) for x in row[2]],
        row[3],
        row[4],
        row[5],
    )


async def _has_higher_group_latch(
    conn, self_id: uuid.UUID, self_ids: list[uuid.UUID], self_score: float
) -> bool:
    """自分より上位(aggregate降順→サイズ昇順→辞書順)の重複集合があるか。"""
    res = await conn.execute(
        _SELECT_HIGHER_GROUP_LATCH,
        {"self": self_id, "my_ids": group_calc.uuid_array(self_ids)},
    )
    for score, ids in res.fetchall():
        other_ids = sorted(_coerce_uuid(x) for x in ids)
        if group_calc.dominates(self_score, self_ids, float(score), other_ids):
            return True
    return False


async def _read_participants(conn, intent_ids: list[uuid.UUID]) -> list[Participant]:
    """全参加者の_SELECT_INTENT_INPUTS読取(2〜4要素)。

    行なし・expires_at NULLのIntentは除外(呼び出し側が
    len(parts) < len(intent_ids) で対象外化)。順序はintent_idsどおり。
    """
    out: list[Participant] = []
    for intent_id in intent_ids:
        row = (
            (await conn.execute(_SELECT_INTENT_INPUTS, {"intent_id": intent_id}))
            .mappings()
            .first()
        )
        if row is None or row["expires_at"] is None:
            continue
        out.append(
            Participant(
                intent_id=_coerce_uuid(row["id"]),
                user_id=_coerce_uuid(row["user_id"]),
                notification_level=row["notification_level"],
                time_start=row["time_start"],
                expires_at=row["expires_at"],
            )
        )
    return out


async def _expire_latch(conn, latch_id: uuid.UUID, now) -> bool:
    """75分切れ/deadline<=nowの破棄(条件付きUPDATE)。"""
    res = await conn.execute(_EXPIRE_LATCH, {"latch_id": latch_id})
    return res.first() is not None


async def _promote_latch(conn, latch_id: uuid.UUID, deadline, now) -> bool:
    """proposed遷移(提示時D-05再計算・条件付きUPDATE)。"""
    res = await conn.execute(
        _PROMOTE_LATCH, {"latch_id": latch_id, "deadline": deadline}
    )
    return res.first() is not None


async def _drain_candidates(
    engine: AsyncEngine, now, threshold: float
) -> list[uuid.UUID]:
    """drain対象の提示順抽出(対象時刻昇順・score降順・engine.begin()内包)。"""
    async with engine.begin() as conn:
        res = await conn.execute(
            _DRAIN_CANDIDATES, {"now": now, "threshold": threshold}
        )
        rows = res.fetchall()
    return [_coerce_uuid(r[0]) for r in rows]


class LatchEngine:
    """Layer 5本体(06 §6・§10・design §2.1案A・§2.9の表)。

    handle(intent_id) は起点非依存IF(JevWorkerと同型・Bucket/catch-upから
    同一部品を呼ぶ)。冪等: 選択SQLのlatch_score IS NULL・ON CONFLICT・
    条件付きUPDATE。モジュール属性経由で origin/layer4/latch_calc/proposal
    を呼ぶ(runnerと同一規律・unit試験がmonkeypatchで差し替え可能)。
    try_promote(latch_id) はpublic(GroupEngine.finalizeが呼ぶ・ws-7)。
    """

    def __init__(self, *, engine: AsyncEngine, clock: Clock, geo=None) -> None:
        self._engine = engine
        self._clock = clock
        self._geo = geo  # GeoService | None(Noneならarea_name=None)

    async def handle(self, intent_id: uuid.UUID) -> None:
        """フェーズ1(読取)→各行評価→drain(design §2.9のtx分割)。"""
        async with self._engine.begin() as conn:
            loaded = await origin_mod.load_origin(conn, self._clock, intent_id)
        if loaded.origin is None:
            logger.info(
                "latch origin no-op intent_id=%s reason=%s",
                intent_id,
                loaded.skip_reason,
            )
            return
        org = loaded.origin
        async with self._engine.begin() as conn:
            rows = await _select_target_rows(conn, org.intent_id, org.version)
        for row in rows:
            await self._evaluate_pair(org, row)
        await self.drain()

    async def _evaluate_pair(self, org, row: LatchTargetRow) -> None:
        """1ペア: 材料読取(tx前)→tx1(H再検証+退避つきUPDATE+D-07+latches+events)。

        I-1対策(design §2.7-4・承認事項4): 失敗しうる読取をtx前に済ませ、
        計算(latch_score退避UPDATE)と生成物(latches INSERT・status_events)を
        同一txで書く。読取段階の失敗はlatch_score NULLのまま残るため、
        復旧後の再handleで再選択される(ws-6の引継ぎ空白の構造解消)。
        """
        now = self._clock.now()
        peer_id = (
            row.intent_b_id if row.intent_a_id == org.intent_id else row.intent_a_id
        )
        a_id, b_id = sorted((org.intent_id, peer_id))
        # tx前の読取(全材料・短tx内包): peer入力が欠けていれば書かない
        origin_inputs = await _read_intent_inputs(self._engine, org.intent_id)
        peer_inputs = await _read_intent_inputs(self._engine, peer_id)
        if origin_inputs is None or peer_inputs is None:
            logger.info(
                "latch inputs missing row_id=%s peer_id=%s", row.row_id, peer_id
            )
            return
        target = latch_calc.pair_target_time(
            origin_inputs.time_start, peer_inputs.time_start
        )
        min_expires = min(origin_inputs.expires_at, peer_inputs.expires_at)
        area = await self._area_name(origin_inputs, peer_inputs)
        responses = await _read_latch_responses(self._engine, a_id, b_id)
        has_no, latest_defer_at = latch_calc.d07_history_inputs(responses)
        mutual = min(
            float(row.jev_result["would_a_accept_b"]),
            float(row.jev_result["would_b_accept_a"]),
        )
        score = latch_calc.LATCH_C * mutual
        deadline0 = latch_calc.response_deadline(now, target, min_expires)
        # tx1: H再検証(SELECT) + 退避つきlatch_score UPDATE + 生成物
        latch_id: uuid.UUID | None = None
        async with self._engine.begin() as conn:
            if not await layer4.hard_constraint_holds(conn, org, peer_id):
                await _close_h_broken(conn, row.row_id, now)
                return
            rec = await _record_score(conn, row.row_id, score, now)
            if rec is None:
                return
            _, prev_latch_score = rec
            if score >= latch_calc.LATCH_THRESHOLD:
                if not latch_calc.d07_allows(
                    has_no_response=has_no,
                    latest_defer_at=latest_defer_at,
                    now=now,
                    target_time=target,
                    new_score=score,
                    prev_latch_score=prev_latch_score,
                ):
                    logger.info("latch d07 denied a=%s b=%s", a_id, b_id)
                    return
                proposal = proposal_mod.build_proposal(
                    origin=origin_inputs,
                    peer=peer_inputs,
                    score=score,
                    area_name=area,
                )
                latch_id = await _insert_latch(
                    conn,
                    a_id=a_id,
                    b_id=b_id,
                    proposal=proposal,
                    score=score,
                    deadline=deadline0,
                    expires=min_expires,
                    now=now,
                )
                if latch_id is not None:
                    await _insert_latch_event(
                        conn, latch_id, None, "candidate", None, now
                    )
                else:
                    found = await _find_open_latch(conn, a_id, b_id)
                    if found is None:
                        return
                    lid, status = found
                    if status != "candidate":
                        logger.info(
                            "latch open row exists latch_id=%s status=%s",
                            lid,
                            status,
                        )
                        return
                    if not latch_calc.d07_allows(
                        has_no_response=has_no,
                        latest_defer_at=latest_defer_at,
                        now=now,
                        target_time=target,
                        new_score=score,
                        prev_latch_score=prev_latch_score,
                    ):
                        logger.info(
                            "latch d07 denied on promotion a=%s b=%s", a_id, b_id
                        )
                        return
                    if not await _update_for_promotion(
                        conn, lid, score=score, proposal=proposal, deadline=deadline0
                    ):
                        return
                    latch_id = lid
            else:
                # nearbyはtry_promoteしない(candidateのまま・引用#9)
                await self._nearby_in_tx(
                    conn,
                    a_id=a_id,
                    b_id=b_id,
                    score=score,
                    origin_inputs=origin_inputs,
                    peer_inputs=peer_inputs,
                    deadline0=deadline0,
                    min_expires=min_expires,
                    has_no=has_no,
                    now=now,
                )
        if latch_id is not None:
            await self.try_promote(latch_id)

    async def _nearby_in_tx(
        self,
        conn,
        *,
        a_id,
        b_id,
        score,
        origin_inputs,
        peer_inputs,
        deadline0,
        min_expires,
        has_no,
        now,
    ) -> uuid.UUID | None:
        """閾値未満: nearby_also参加者への存在通知(§2.7・引用#9・集約tx内)。

        戻り値は「新規INSERT成功ならlatch_id・開いている行あり/対象なしはNone」。
        nearbyはtry_promoteしない(candidateのまま)。
        """
        notify_targets = [
            inp
            for inp in (origin_inputs, peer_inputs)
            if inp.notification_level == "nearby_also"
        ]
        if not notify_targets:
            return None
        if has_no:
            return None  # 存在通知も出さない(D-07の一貫適用・設計確定)
        latch_id = await _insert_latch(
            conn,
            a_id=a_id,
            b_id=b_id,
            proposal=proposal_mod.nearby_proposal(),
            score=score,
            deadline=deadline0,
            expires=min_expires,
            now=now,
        )
        if latch_id is None:
            return None  # 開いている行あり・閾値未満評価では既存行を更新しない
        await _insert_latch_event(conn, latch_id, None, "candidate", None, now)
        day_start = layer4.jst_day_start(self._clock.jst_date())
        day_next = day_start + timedelta(days=1)
        counts = await _count_daily_notifications(
            conn, [u.user_id for u in notify_targets], day_start, day_next
        )
        for u in notify_targets:
            if counts.get(u.user_id, 0) < latch_calc.D08_DAILY_LIMIT:
                await _insert_notification(
                    conn, u.user_id, NOTIFICATION_NEARBY, latch_id, now
                )
            else:
                logger.info("latch nearby daily limit uid=%s", u.user_id)
        return latch_id

    async def try_promote(self, latch_id: uuid.UUID) -> None:
        """提示判定(1tx・design §2.6手順1〜9・Review Focus 4の手順順序)。

        手順: 行ロック→(now採取・参加者読取)→75分ルール→expires_at切れ
        対象外化→D-06重複上位チェック(グループのみ)→D-05再計算(deadline<=nowは
        75分と同一扱い)→D-08日次→D-08同時→条件付きproposed遷移→
        イベント+notifications(muted除外)。上限超過はcandidateのままreturn
        (破棄しない — 引用#8)。ws-7で|S|人対応(2〜4要素のintent_ids)。
        """
        async with self._engine.begin() as conn:
            row = await _select_latch_for_update(conn, latch_id)
            if row is None or row[1] != "candidate":
                return
            now = self._clock.now()  # FOR UPDATE取得後に採取
            parts = await _read_participants(conn, row[2])
            if len(parts) < len(row[2]):
                return  # 参加Intent欠損(削除等)は対象外
            target = max(p.time_start for p in parts)
            min_expires = min(p.expires_at for p in parts)
            # 75分ルール: 通知せず破棄(candidate→expired・引用#5)
            if target - now < latch_calc.PROMPT_LEAD_MIN:
                await _expire_latch(conn, latch_id, now)
                await _insert_latch_event(
                    conn, latch_id, "candidate", "expired", None, now
                )
                return
            # expires_at切れはexpiry_sweeper(M3-3)の担当・ここでは対象外化のみ
            if row[3] <= now:
                return
            if row[4] is not None:  # グループlatchesのみ(design §2.6)
                if await _has_higher_group_latch(conn, latch_id, row[2], float(row[5])):
                    return  # candidateのまま(上位の行が閉じた後のdrainで提示)
            deadline = latch_calc.response_deadline(now, target, min_expires)
            if deadline <= now:
                # 導出期限が通知時刻を過ぎない場合は通知しない(引用#4・防御)
                await _expire_latch(conn, latch_id, now)
                await _insert_latch_event(
                    conn, latch_id, "candidate", "expired", None, now
                )
                return
            notify = [p for p in parts if p.notification_level != "muted"]
            day_start = layer4.jst_day_start(self._clock.jst_date())
            day_next = day_start + timedelta(days=1)
            counts = await _count_daily_notifications(
                conn, [p.user_id for p in notify], day_start, day_next
            )
            if any(
                counts.get(p.user_id, 0) >= latch_calc.D08_DAILY_LIMIT for p in notify
            ):
                return  # candidateのまま(破棄しない)
            for p in parts:  # muted含む全参加者がproposed数に計上(引用#10)
                if (
                    await _count_open_proposed(conn, p.intent_id)
                    >= latch_calc.D08_CONCURRENT_LIMIT
                ):
                    return
            if not await _promote_latch(conn, latch_id, deadline, now):
                return  # 他経路で遷移済み(条件付きUPDATEの行数0)
            await _insert_latch_event(
                conn, latch_id, "candidate", "proposed", None, now
            )
            for p in notify:
                await _insert_notification(
                    conn, p.user_id, NOTIFICATION_PROPOSAL, latch_id, now
                )

    async def drain(self) -> None:
        """保留キューを提示順に走査し各行へtry_promote(評価経路のたび・引用#17)。

        M3 ws-2(design §2.6): 0時リセット完了時・クローズ検知(sweeper §2.7)からも
        呼ばれるpublic IF。差分化(引用#12)に正確に対応する — Jevを呼ばず
        提示順の再計算のみ(try_promoteの行単位上限判定が上限内の件数のみ処理し、
        残りは次トリガーへ委ねる)。

        大量保留時は行単位の上限判定で自然に上限内のみ処理され、残りは
        次トリガーへ(design §2.10-8: Worker 1構成を前提に行ロックのみ)。
        """
        now = self._clock.now()
        ids = await _drain_candidates(self._engine, now, latch_calc.LATCH_THRESHOLD)
        for latch_id in ids:
            await self.try_promote(latch_id)

    async def _area_name(self, a: LatchIntentInputs, b: LatchIntentInputs):
        """geo中点の逆転ジオコーディング(承認済み解釈・読取のみtx外)。"""
        if self._geo is None:
            return None
        return await self._geo.reverse_geocode(
            (a.geo_lon + b.geo_lon) / 2, (a.geo_lat + b.geo_lat) / 2
        )
