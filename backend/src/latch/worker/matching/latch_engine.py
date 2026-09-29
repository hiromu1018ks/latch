"""Layer 5 LATCH Engine本体(06 §6・§10・design §2.1案A・§2.2〜2.5・§2.9の表)。

latch_score計算(L = H × MutualScore × C)・latches行生成/昇格・D-07再提案
制御・nearby存在通知・D-08上限検査・75分ルール・D-05回答期限式・提示順drain。
永続化はtext()生SQLのみ(jev.pyと同一形式)。時刻はClock経由のみ。
ws-7拡張点: latches生成・proposal生成・drain・try_promoteは1対1構成。
intent_ids正規化はsorted(集合側も同様に正規化する)。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.worker.matching import latch_calc, layer4
from latch.worker.matching import origin as origin_mod
from latch.worker.matching import proposal as proposal_mod
from latch.worker.matching.proposal import LatchIntentInputs

logger = logging.getLogger(__name__)

NOTIFICATION_PROPOSAL = "proposal"
NOTIFICATION_NEARBY = "nearby_candidate"

# フェーズ1: 起点に紐づく「計算済みでない評価行」(design §2.2。
# 起点version一致・latch_score IS NULL(冪等ガード)・相手version=相手現行のEXISTS)
_SELECT_TARGETS = text("""
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
# day_start/day_nextの日付条件の動的切り替えで成立 — カウンタリセットジョブ不要)
_COUNT_DAILY_NOTIFICATIONS = text("""
    SELECT user_id, COUNT(*) FROM notifications
    WHERE user_id IN (CAST(:u0 AS uuid), CAST(:u1 AS uuid))
      AND type IN ('proposal', 'nearby_candidate')
      AND created_at >= CAST(:day_start AS timestamptz)
      AND created_at < CAST(:day_next AS timestamptz)
    GROUP BY user_id
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
    secondary = None
    if isinstance(sd, dict):
        cat = sd.get("category")
        if isinstance(cat, dict):
            secondary = cat.get("secondary")
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
    日付条件の切替で成立)。user_idsは2要素固定(u0/u1へ展開)。"""
    u0, u1 = user_ids
    res = await conn.execute(
        _COUNT_DAILY_NOTIFICATIONS,
        {"u0": u0, "u1": u1, "day_start": day_start, "day_next": day_next},
    )
    rows = res.fetchall()
    return {_coerce_uuid(uid): int(cnt) for uid, cnt in rows}


async def _drain_candidates(
    engine: AsyncEngine, now, threshold: float
) -> list[uuid.UUID]:
    """drain対象の提示順抽出(対象時刻昇順・score降順)。Task 5で実装。"""
    return []


class LatchEngine:
    """Layer 5本体(06 §6・§10・design §2.1案A・§2.9の表)。

    handle(intent_id) は起点非依存IF(JevWorkerと同型・Bucket/catch-upから
    同一部品を呼ぶ)。冪等: 選択SQLのlatch_score IS NULL・ON CONFLICT・
    条件付きUPDATE。モジュール属性経由で origin/layer4/latch_calc/proposal
    を呼ぶ(runnerと同一規律・unit試験がmonkeypatchで差し替え可能)。
    ws-7拡張点: latches生成・proposal・drain・try_promoteは1対1構成。
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
        await self._drain()

    async def _evaluate_pair(self, org, row: LatchTargetRow) -> None:
        """1ペア: H再検証→スコア計算→latches生成/昇格 or nearby(design §2.2〜2.7)。"""
        now = self._clock.now()
        peer_id = (
            row.intent_b_id if row.intent_a_id == org.intent_id else row.intent_a_id
        )
        a_id, b_id = sorted((org.intent_id, peer_id))
        # tx1: H再検証 + 退避つきlatch_score UPDATE(design §2.2)
        async with self._engine.begin() as conn:
            if not await layer4.hard_constraint_holds(conn, org, peer_id):
                await _close_h_broken(conn, row.row_id, now)
                return
            mutual = min(
                float(row.jev_result["would_a_accept_b"]),
                float(row.jev_result["would_b_accept_a"]),
            )
            score = latch_calc.LATCH_C * mutual
            rec = await _record_score(conn, row.row_id, score, now)
        if rec is None:
            return
        _, prev_latch_score = rec
        # tx外の読取: proposal入力・geo中点・D-07履歴(短tx内包)
        origin_inputs = await _read_intent_inputs(self._engine, org.intent_id)
        peer_inputs = await _read_intent_inputs(self._engine, peer_id)
        if origin_inputs is None or peer_inputs is None:
            return
        target = latch_calc.pair_target_time(
            origin_inputs.time_start, peer_inputs.time_start
        )
        min_expires = min(origin_inputs.expires_at, peer_inputs.expires_at)
        area = await self._area_name(origin_inputs, peer_inputs)
        responses = await _read_latch_responses(self._engine, a_id, b_id)
        has_no, latest_defer_at = latch_calc.d07_history_inputs(responses)
        deadline0 = latch_calc.response_deadline(now, target, min_expires)
        if score >= latch_calc.LATCH_THRESHOLD:
            await self._proposal_path(
                a_id=a_id,
                b_id=b_id,
                score=score,
                prev=prev_latch_score,
                origin_inputs=origin_inputs,
                peer_inputs=peer_inputs,
                target=target,
                min_expires=min_expires,
                area=area,
                deadline0=deadline0,
                has_no=has_no,
                latest_defer_at=latest_defer_at,
                now=now,
            )
            return
        await self._nearby_path(
            a_id=a_id,
            b_id=b_id,
            score=score,
            origin_inputs=origin_inputs,
            peer_inputs=peer_inputs,
            min_expires=min_expires,
            deadline0=deadline0,
            has_no=has_no,
            now=now,
        )

    async def _proposal_path(
        self,
        *,
        a_id,
        b_id,
        score,
        prev,
        origin_inputs,
        peer_inputs,
        target,
        min_expires,
        area,
        deadline0,
        has_no,
        latest_defer_at,
        now,
    ) -> None:
        """閾値超過: D-07判定→latches INSERT/昇格→try_promote(§2.3〜2.4)。"""
        allowed = latch_calc.d07_allows(
            has_no_response=has_no,
            latest_defer_at=latest_defer_at,
            now=now,
            target_time=target,
            new_score=score,
            prev_latch_score=prev,
        )
        if not allowed:
            logger.info("latch d07 denied a=%s b=%s", a_id, b_id)
            return
        proposal = proposal_mod.build_proposal(
            origin=origin_inputs,
            peer=peer_inputs,
            score=score,
            area_name=area,
        )
        async with self._engine.begin() as conn:
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
                await _insert_latch_event(conn, latch_id, None, "candidate", None, now)
            else:
                # ON CONFLICT: 既存開いている行の昇格判定(§2.3)
                found = await _find_open_latch(conn, a_id, b_id)
                if found is None:
                    return
                lid, status = found
                if status != "candidate":
                    logger.info(
                        "latch open row exists latch_id=%s status=%s", lid, status
                    )
                    return
                if not latch_calc.d07_allows(
                    has_no_response=has_no,
                    latest_defer_at=latest_defer_at,
                    now=now,
                    target_time=target,
                    new_score=score,
                    prev_latch_score=prev,
                ):
                    logger.info("latch d07 denied on promotion a=%s b=%s", a_id, b_id)
                    return
                if not await _update_for_promotion(
                    conn, lid, score=score, proposal=proposal, deadline=deadline0
                ):
                    return
                latch_id = lid
        await self._try_promote(latch_id)

    async def _nearby_path(
        self,
        *,
        a_id,
        b_id,
        score,
        origin_inputs,
        peer_inputs,
        min_expires,
        deadline0,
        has_no,
        now,
    ) -> None:
        """閾値未満: nearby_also参加者への存在通知(§2.7・引用#9)。"""
        notify_targets = [
            inp
            for inp in (origin_inputs, peer_inputs)
            if inp.notification_level == "nearby_also"
        ]
        if not notify_targets:
            return
        if has_no:
            return  # 存在通知も出さない(D-07の一貫適用・設計確定)
        async with self._engine.begin() as conn:
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
                return  # 開いている行あり・閾値未満評価では既存行を更新しない
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

    async def _try_promote(self, latch_id: uuid.UUID) -> None:
        """提示判定(75分・D-08・proposed遷移・notifications)。Task 5で実装。"""

    async def _drain(self) -> None:
        """保留キューの提示順drain。Task 5で実装。"""

    async def _area_name(self, a: LatchIntentInputs, b: LatchIntentInputs):
        """geo中点の逆転ジオコーディング(承認済み解釈・読取のみtx外)。"""
        if self._geo is None:
            return None
        return await self._geo.reverse_geocode(
            (a.geo_lon + b.geo_lon) / 2, (a.geo_lat + b.geo_lat) / 2
        )
