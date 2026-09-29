"""グループマッチ(Group Search)本体(M2 ws-7・06 §7〜§8・design §2.2〜2.4)。

GroupEngine.handle: 起点読取(max>=3専用ガード)→Pool構築(人数緩和検索・
cheap_score降順上位15)→Pool×起点ペアUPSERT→互換行列(1 SQL事前計算)→
貪欲法(group_calc.build_groups)→group_candidates記録+メンバー間ペアUPSERT。
finalize(集約)はdesign §2.5(I-1対策のtx構成)。

冪等(0005部分UNIQUE・UPSERT・aggregate_score IS NULLガード)。モジュール属性
経由で origin/layer1(BASE)/layer3/candidates/latch_calc/group_calc を呼ぶ
(runnerと同一規律・unit試験がmonkeypatchで差し替え可能)。SQLはtext()生SQL・
CAST(:x AS ...)形式(§2グローバル制約)。uuid[]のbindは文字列リテラル形式
(group_calc.uuid_array・§9-14)。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from latch.core.clock import Clock
from latch.intents.completion import DEFAULT_RADIUS_M, default_time_end
from latch.users.service import age_years
from latch.worker.matching import candidates, group_calc, latch_calc, layer3
from latch.worker.matching import origin as origin_mod
from latch.worker.matching import proposal as proposal_mod
from latch.worker.matching.layer1 import LAYER1_WHERE_BASE
from latch.worker.matching.origin import Origin, OriginLoad, bind_params
from latch.worker.matching.proposal import LatchIntentInputs

logger = logging.getLogger(__name__)

# 起点の種になり得ない(max < 3)ときのskip理由(design §2.2・承認事項2)
SKIP_GROUP_ORIGIN_MAX = "origin_not_group"

# 起点読取(origin.pyの_SELECT_ORIGINと同一列・人数ガードはload_group_origin側で
# 「max >= 3」へ差し替え — design §2.2のPool緩和の起点版・Review Focus 2)
_SELECT_GROUP_ORIGIN = text("""
    SELECT i.id, i.version, i.user_id, i.category_primary, i.alcohol_involved,
           i.budget_max, i.participants_min, i.participants_max,
           i.geo_radius_m, i.time_start, i.time_end, i.status, i.embedding,
           i.structured_data,
           ST_X(i.geo_center::geometry) AS lon,
           ST_Y(i.geo_center::geometry) AS lat,
           u.birth_date
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE i.id = CAST(:intent_id AS uuid)
""")

# 候補Pool検索(design §2.2案B・承認事項1)。起点との互換はLAYER1_WHERE_BASE
# (人数行を緩和)・Bucketは起点time_startの属する30分Bucket(解釈記録9)
_POOL_SEARCH = text(f"""
    SELECT i.id, i.version, i.user_id, i.participants_min, i.participants_max,
           i.time_start, i.budget_max, i.structured_data,
           1 - (i.embedding <=> CAST(:origin_embedding AS vector)) AS similarity
    FROM intents i
    JOIN users u ON u.id = i.user_id
    WHERE {LAYER1_WHERE_BASE}
      AND i.participants_min <= 4
      AND i.participants_max >= 3
      AND i.time_start >= CAST(:bucket_start AS timestamptz)
      AND i.time_start < CAST(:bucket_end AS timestamptz)
    ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC, i.id ASC
    LIMIT {group_calc.POOL_SEARCH_LIMIT}
""")

# 互換行列(design §2.3)。対称条件のため i1.id < i2.id の一方向だけ取る。
# 人数条件は含まない(最終判定は貪欲法の共通包含とfinalizeのH再検証が担う)。
# soft_texts・rule計算に必要な列も同時取得(メンバー間ペアのcheap_score用)
_PAIR_COMPAT = text("""
    SELECT i1.id AS a_id, i2.id AS b_id,
           1 - (i1.embedding <=> i2.embedding) AS similarity,
           i1.time_start AS a_time_start, i1.budget_max AS a_budget_max,
           i1.structured_data AS a_structured,
           i2.time_start AS b_time_start, i2.budget_max AS b_budget_max,
           i2.structured_data AS b_structured
    FROM intents i1
    JOIN intents i2 ON i1.id < i2.id
    JOIN users u1 ON u1.id = i1.user_id
    JOIN users u2 ON u2.id = i2.user_id
    WHERE i1.id = ANY(CAST(:ids AS uuid[]))
      AND i2.id = ANY(CAST(:ids AS uuid[]))
      AND i1.status = 'active' AND i2.status = 'active'
      AND i1.time_start < COALESCE(i2.time_end, i2.time_start + interval '3 hours')
      AND i2.time_start < COALESCE(i1.time_end, i1.time_start + interval '3 hours')
      AND ST_DWithin(i1.geo_center, i2.geo_center,
                     CAST(i1.geo_radius_m + COALESCE(i2.geo_radius_m, 1000)
                          AS double precision))
      AND i1.user_id <> i2.user_id
      AND NOT EXISTS (
          SELECT 1 FROM blocks b
          WHERE (b.blocker_id = i1.user_id AND b.blocked_id = i2.user_id)
             OR (b.blocker_id = i2.user_id AND b.blocked_id = i1.user_id))
      AND (
          (NOT (i1.alcohol_involved OR i2.alcohol_involved))
          OR (
              EXTRACT(YEAR FROM AGE(
                  (CAST(:now AS timestamptz) AT TIME ZONE 'Asia/Tokyo'),
                  CAST(u1.birth_date AS timestamp))) >= 20
              AND EXTRACT(YEAR FROM AGE(
                  (CAST(:now AS timestamptz) AT TIME ZONE 'Asia/Tokyo'),
                  CAST(u2.birth_date AS timestamp))) >= 20
          )
      )
""")

# 集合の記録(design §2.3・§2.8)。ON CONFLICTは0005部分UNIQUE索引へ推論
_INSERT_GROUP = text("""
    INSERT INTO group_candidates
        (intent_ids, member_scores, status, created_at, updated_at)
    VALUES (CAST(:ids AS uuid[]), CAST(:member_scores AS jsonb),
            'candidate', :now, :now)
    ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed')
    DO NOTHING
    RETURNING id
""")

# 集合メンバーの現行version・人数・user_id(世代判定・H再検証・INSERT前user相異検査)
_SELECT_GROUP_VERSIONS = text("""
    SELECT id, version, user_id, participants_min, participants_max
    FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

# finalize対象(design §2.5): 起点が属する開いている集合
_SELECT_PENDING_GROUPS = text("""
    SELECT g.id, g.intent_ids, g.member_scores, g.aggregate_score
    FROM group_candidates g
    WHERE g.status = 'candidate'
      AND CAST(:origin AS uuid) = ANY(g.intent_ids)
""")

# 集合内ペア行(全ペア揃い判定とMutualScore計算の材料)
_SELECT_GROUP_PAIRS = text("""
    SELECT id, intent_a_id, intent_b_id, intent_a_version, intent_b_version,
           jev_result
    FROM match_candidates
    WHERE intent_a_id = ANY(CAST(:ids AS uuid[]))
      AND intent_b_id = ANY(CAST(:ids AS uuid[]))
""")

# 世代リセット(design §2.5手順1・引用#10: 新評価世代はprevを持たない)
_RESET_GENERATION = text("""
    UPDATE group_candidates
    SET aggregate_score = NULL, prev_aggregate_score = NULL,
        member_scores = CAST(:member_scores AS jsonb), updated_at = :now
    WHERE id = CAST(:gid AS uuid) AND status = 'candidate'
""")

# 退避つき集約更新(06 §10手順1〜2のgroup版・I-1対策の本体前半)
_UPDATE_AGGREGATE = text("""
    UPDATE group_candidates
    SET prev_aggregate_score = aggregate_score,
        aggregate_score = CAST(:score AS numeric), updated_at = :now
    WHERE id = CAST(:gid AS uuid) AND aggregate_score IS NULL
    RETURNING id, prev_aggregate_score
""")

# latches生成(design §2.5。0004部分UNIQUEへON CONFLICT・group_candidate_idつき)
_INSERT_GROUP_LATCH = text("""
    INSERT INTO latches
        (intent_ids, group_candidate_id, proposal, score, status,
         response_deadline, expires_at, created_at)
    VALUES (CAST(:ids AS uuid[]), CAST(:gid AS uuid), CAST(:proposal AS jsonb),
            :score, 'candidate', CAST(:deadline AS timestamptz),
            CAST(:expires AS timestamptz), :now)
    ON CONFLICT (intent_ids) WHERE status IN ('candidate', 'proposed', 'partial_accept')
    DO NOTHING
    RETURNING id
""")

# ON CONFLICTで飛んだ場合の既存開いている行特定(1対1の_FIND_OPEN_LATCHと同型)
_FIND_OPEN_GROUP_LATCH = text("""
    SELECT id, status FROM latches
    WHERE intent_ids = CAST(:ids AS uuid[])
      AND status IN ('candidate', 'proposed', 'partial_accept')
""")

# D-07履歴(集合版・uuid[]等値。3〜4要素でそのまま動く — design §2.5)
_SELECT_GROUP_LATCH_RESPONSES = text("""
    SELECT responses FROM latches
    WHERE intent_ids = CAST(:ids AS uuid[])
      AND responses <> CAST('[]' AS jsonb)
""")

# 集合のproposed遷移(latches生成と同一tx・design §2.5)
_MARK_GROUP_PROPOSED = text("""
    UPDATE group_candidates
    SET status = 'proposed', updated_at = :now
    WHERE id = CAST(:gid AS uuid) AND status = 'candidate'
""")

# H再検証不成立(design §2.5手順3: 集合のみ閉じる・構成ペア行は閉じない)
_CLOSE_GROUP_BY_ID = text("""
    UPDATE group_candidates
    SET status = 'closed', updated_at = :now
    WHERE id = CAST(:gid AS uuid) AND status = 'candidate'
""")

# 昇格時のcandidate行更新(latch_engine._UPDATE_FOR_PROMOTIONと同型)
_UPDATE_GROUP_LATCH_FOR_PROMOTION = text("""
    UPDATE latches
    SET score = :score, proposal = CAST(:proposal AS jsonb),
        response_deadline = CAST(:deadline AS timestamptz)
    WHERE id = CAST(:latch_id AS uuid) AND status = 'candidate'
    RETURNING id
""")

# latch_status_events挿入(latch_engine._INSERT_LATCH_EVENTと同一SQL)
_INSERT_LATCH_EVENT = text("""
    INSERT INTO latch_status_events
        (latch_id, from_status, to_status, user_id, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:from_status AS text),
            :to_status, CAST(:user_id AS uuid), :now)
""")

# 集約材料読取(latch_engine._SELECT_INTENT_INPUTSと同一SQL・§9-4注記)
_SELECT_INTENT_INPUTS = text("""
    SELECT id, user_id, visibility, notification_level, time_start,
           expires_at, budget_max, category_primary, structured_data,
           ST_X(geo_center::geometry) AS lon, ST_Y(geo_center::geometry) AS lat
    FROM intents WHERE id = CAST(:intent_id AS uuid)
""")


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT/RETURNING結果のUUID列復元(asyncpgサブクラス対策・origin.pyと同一)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _embedding_text(raw: object) -> str | None:
    """vector列の読み取り値を文字列へ(origin.pyと同一規律・design §5-2)。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return "[" + ",".join(repr(float(v)) for v in raw) + "]"


async def load_group_origin(
    conn: AsyncConnection, clock: Clock, intent_id: uuid.UUID
) -> OriginLoad:
    """起点を読みno-op判定(design §2.2)。人数ガードのみ「種になれる(max>=3)」
    へ差し替え(origin.load_originのPool緩和版・Review Focus 2)。"""
    row = (
        (await conn.execute(_SELECT_GROUP_ORIGIN, {"intent_id": intent_id}))
        .mappings()
        .first()
    )
    if row is None:
        return OriginLoad(origin=None, skip_reason=origin_mod.SKIP_NOT_FOUND)
    if row.status != "active":
        return OriginLoad(origin=None, skip_reason=origin_mod.SKIP_NOT_ACTIVE)
    embedding = _embedding_text(row.embedding)
    if embedding is None:
        return OriginLoad(origin=None, skip_reason=origin_mod.SKIP_EMBEDDING_NULL)
    if row.lon is None or row.lat is None:
        return OriginLoad(origin=None, skip_reason=origin_mod.SKIP_GEO_MISSING)
    if row.time_start is None:
        return OriginLoad(origin=None, skip_reason=origin_mod.SKIP_TIME_MISSING)
    if row.participants_max < group_calc.GROUP_MIN:
        return OriginLoad(origin=None, skip_reason=SKIP_GROUP_ORIGIN_MAX)
    origin = Origin(
        intent_id=_coerce_uuid(row.id),
        version=row.version,
        user_id=_coerce_uuid(row.user_id),
        category_primary=row.category_primary,
        alcohol_involved=row.alcohol_involved,
        budget_max=row.budget_max,
        participants_min=row.participants_min,
        participants_max=row.participants_max,
        geo_lon=float(row.lon),
        geo_lat=float(row.lat),
        geo_radius_m=(
            row.geo_radius_m if row.geo_radius_m is not None else DEFAULT_RADIUS_M
        ),
        time_start=row.time_start,
        time_end=(
            row.time_end
            if row.time_end is not None
            else default_time_end(row.time_start)
        ),
        embedding=embedding,
        soft_texts=layer3.soft_texts(row.structured_data),
        user_ge_20=age_years(row.birth_date, clock.jst_date()) >= 20,
        evaluated_at=clock.now(),
    )
    return OriginLoad(origin=origin, skip_reason=None)


async def _pool_search(conn: AsyncConnection, origin: Origin) -> list[tuple]:
    """Pool候補のHNSW検索(人数緩和・Bucketは起点time_startの30分Bucket)。"""
    bucket_start = latch_calc.bucket_start(origin.time_start)
    bucket_end = bucket_start + timedelta(minutes=latch_calc.BUCKET_MINUTES)
    params = {
        **bind_params(origin),
        "origin_embedding": origin.embedding,
        "bucket_start": bucket_start,
        "bucket_end": bucket_end,
    }
    rows = (await conn.execute(_POOL_SEARCH, params)).all()
    return [
        (
            _coerce_uuid(r[0]),
            r[1],
            _coerce_uuid(r[2]),
            r[3],
            r[4],
            r[5],
            r[6],
            r[7],
            float(r[8]),
        )
        for r in rows
    ]


async def _pair_compat(
    conn: AsyncConnection, ids: list[uuid.UUID], now
) -> dict[tuple[uuid.UUID, uuid.UUID], tuple[float, float]]:
    """互換行列(design §2.3)。キーは(a_id, b_id)とする(a < b・SQL保証)。

    値は (similarity, cheap)。cheap はPool構築と同一のLayer 3計算
    (rule_score + vocab_overlap + cheap_score)をペア両側の列から行ごとに計算。
    """
    rows = (
        await conn.execute(
            _PAIR_COMPAT,
            {"ids": group_calc.uuid_array(ids), "now": now},
        )
    ).all()
    out: dict[tuple[uuid.UUID, uuid.UUID], tuple[float, float]] = {}
    for r in rows:
        a_id, b_id = _coerce_uuid(r[0]), _coerce_uuid(r[1])
        sim = float(r[2])
        rule = layer3.rule_score(
            origin_time_start=r[3],
            cand_time_start=r[6],
            origin_budget=r[4],
            cand_budget=r[7],
        )
        vocab = layer3.vocab_overlap(layer3.soft_texts(r[5]), layer3.soft_texts(r[8]))
        out[(a_id, b_id)] = (sim, layer3.cheap_score(sim, rule, vocab))
    return out


async def _select_group_versions(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[tuple]:
    """集合メンバーの現行version・user_id・人数(INSERT前検査・世代判定)。"""
    rows = (
        await conn.execute(_SELECT_GROUP_VERSIONS, {"ids": group_calc.uuid_array(ids)})
    ).all()
    return [(_coerce_uuid(r[0]), r[1], _coerce_uuid(r[2]), r[3], r[4]) for r in rows]


async def _insert_group(
    conn: AsyncConnection,
    *,
    ids: list[uuid.UUID],
    member_scores: dict,
    now,
) -> uuid.UUID | None:
    """集合INSERT(0005部分UNIQUEへON CONFLICT DO NOTHING)。None=開いている同一集合。"""
    res = await conn.execute(
        _INSERT_GROUP,
        {
            "ids": group_calc.uuid_array(ids),
            "member_scores": json.dumps(member_scores, ensure_ascii=False),
            "now": now,
        },
    )
    row = res.first()
    return _coerce_uuid(row[0]) if row is not None else None


async def _select_pending_groups(
    conn: AsyncConnection, origin_id: uuid.UUID
) -> list[tuple]:
    """起点が属する開いている集合(design §2.5)。"""
    rows = (await conn.execute(_SELECT_PENDING_GROUPS, {"origin": origin_id})).all()
    out: list[tuple] = []
    for r in rows:
        ids = [_coerce_uuid(x) for x in r[1]]
        ms = r[2] if isinstance(r[2], dict) else json.loads(r[2])
        out.append((_coerce_uuid(r[0]), ids, ms, r[3]))
    return out


async def _select_group_pairs(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[tuple]:
    """集合内ペア行(全ペア揃い判定とMutualScore計算の材料)。"""
    rows = (
        await conn.execute(_SELECT_GROUP_PAIRS, {"ids": group_calc.uuid_array(ids)})
    ).all()
    return [
        (
            _coerce_uuid(r[0]),
            _coerce_uuid(r[1]),
            _coerce_uuid(r[2]),
            r[3],
            r[4],
            r[5],
        )
        for r in rows
    ]


async def _reset_generation(
    conn: AsyncConnection, gid: uuid.UUID, member_scores: dict, now
) -> None:
    """世代リセット(aggregate/prev=NULL・versions=現行組)。"""
    await conn.execute(
        _RESET_GENERATION,
        {
            "gid": gid,
            "member_scores": json.dumps(member_scores, ensure_ascii=False),
            "now": now,
        },
    )


async def _update_aggregate(
    conn: AsyncConnection, gid: uuid.UUID, score: float, now
) -> tuple[uuid.UUID, float | None] | None:
    """退避つき集約UPDATE。None=競合負け(aggregate計算済み・冪等)。"""
    res = await conn.execute(
        _UPDATE_AGGREGATE, {"gid": gid, "score": score, "now": now}
    )
    row = res.first()
    if row is None:
        return None
    return (_coerce_uuid(row[0]), float(row[1]) if row[1] is not None else None)


async def _insert_group_latch(
    conn: AsyncConnection,
    *,
    ids: list[uuid.UUID],
    gid: uuid.UUID,
    proposal: dict,
    score: float,
    deadline,
    expires,
    now,
) -> uuid.UUID | None:
    """グループlatches INSERT(ON CONFLICT DO NOTHING)。None=開いている行あり。"""
    res = await conn.execute(
        _INSERT_GROUP_LATCH,
        {
            "ids": group_calc.uuid_array(ids),
            "gid": gid,
            "proposal": json.dumps(proposal, ensure_ascii=False),
            "score": score,
            "deadline": deadline,
            "expires": expires,
            "now": now,
        },
    )
    row = res.first()
    return _coerce_uuid(row[0]) if row is not None else None


async def _find_open_group_latch(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> tuple[uuid.UUID, str] | None:
    """ON CONFLICTで飛んだ場合の既存開いている行特定。"""
    res = await conn.execute(
        _FIND_OPEN_GROUP_LATCH, {"ids": group_calc.uuid_array(ids)}
    )
    row = res.first()
    if row is None:
        return None
    return (_coerce_uuid(row[0]), row[1])


async def _select_group_latch_responses(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[dict]:
    """D-07履歴(集合版・uuid[]等値・responses空は除外)。マージ済みlistを返す。"""
    res = await conn.execute(
        _SELECT_GROUP_LATCH_RESPONSES, {"ids": group_calc.uuid_array(ids)}
    )
    merged: list[dict] = []
    for row in res.fetchall():
        responses = row[0]
        if isinstance(responses, str):
            responses = json.loads(responses)
        if responses:
            merged.extend(responses)
    return merged


async def _update_group_latch_for_promotion(
    conn: AsyncConnection,
    latch_id: uuid.UUID,
    *,
    score: float,
    proposal: dict,
    deadline,
) -> bool:
    """昇格時のcandidate行更新(score/proposal/response_deadlineの3列のみ)。"""
    res = await conn.execute(
        _UPDATE_GROUP_LATCH_FOR_PROMOTION,
        {
            "latch_id": latch_id,
            "score": score,
            "proposal": json.dumps(proposal, ensure_ascii=False),
            "deadline": deadline,
        },
    )
    return res.first() is not None


async def _insert_latch_event(
    conn: AsyncConnection,
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


async def _mark_group_proposed(conn: AsyncConnection, gid: uuid.UUID, now) -> None:
    """集合のproposed遷移(latches生成と同一tx)。"""
    await conn.execute(_MARK_GROUP_PROPOSED, {"gid": gid, "now": now})


async def _close_group_by_id(conn: AsyncConnection, gid: uuid.UUID, now) -> None:
    """H再検証不成立: 集合のみ閉じる(構成ペア行は閉じない)。"""
    await conn.execute(_CLOSE_GROUP_BY_ID, {"gid": gid, "now": now})


async def _read_member_inputs(engine: AsyncEngine, intent_id: uuid.UUID):
    """集約材料の1Intent分(latch_engine._read_intent_inputsと同一構築)。

    行なし/expires_at NULLはNone(latches.expires_at NOT NULLのため対象外)。
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


@dataclass(frozen=True)
class GroupContext:
    """GroupEngine.handleの戻り値(design §2.1)。JevWorkerの配分順序判定に使う。"""

    group_ids: frozenset[uuid.UUID]  # 今回INSERTしたgroup_candidatesのid
    new_pair_row_ids: frozenset[uuid.UUID]  # 今回UPSERTしたメンバー間ペアの行id


@dataclass(frozen=True)
class _ReadyGroup:
    """finalize読取フェーズの確定1集合(集約txの入力・D-06順ソート用)。"""

    gid: uuid.UUID
    ids: list[uuid.UUID]
    ms: dict
    inputs: list[LatchIntentInputs]
    score: float
    target: datetime
    min_expires: datetime
    area: str | None
    has_no: bool
    latest_defer_at: datetime | None


class GroupEngine:
    """グループマッチ本体(06 §7〜§8・design §2.2〜2.8)。冪等(0005部分UNIQUE・
    UPSERT・aggregate_score IS NULLガード)。モジュール属性経由で
    origin(型のみ)・layer1(BASE)・layer3・candidates・latch_calc・proposal・
    latch_engine(try_promote)を呼ぶ(runnerと同一規律・unit試験が
    monkeypatchで差し替え可能)。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        geo=None,
        latch=None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._geo = geo  # GeoService | None(Noneならarea_name=None)
        self._latch = latch  # LatchEngine | None(try_promote委譲用)

    async def handle(self, intent_id: uuid.UUID) -> GroupContext | None:
        """生成(設計§2.2〜2.3手順1〜8)。skip・Pool空はno-op(構造化ログ)。"""
        async with self._engine.begin() as conn:
            loaded = await load_group_origin(conn, self._clock, intent_id)
        if loaded.skip_reason is not None or loaded.origin is None:
            logger.info(
                "group origin no-op intent_id=%s reason=%s",
                intent_id,
                loaded.skip_reason,
            )
            return None
        org = loaded.origin
        async with self._engine.begin() as conn:
            rows = await _pool_search(conn, org)
        if not rows:
            logger.info("group pool empty intent_id=%s", intent_id)
            return None
        # 手順3〜4: 各行へcheap_score(Layer 3同一計算)→降順上位POOL_LIMIT
        scored: list[tuple[tuple, float]] = []
        for r in rows:
            rule = layer3.rule_score(
                origin_time_start=org.time_start,
                cand_time_start=r[5],
                origin_budget=org.budget_max,
                cand_budget=r[6],
            )
            vocab = layer3.vocab_overlap(org.soft_texts, layer3.soft_texts(r[7]))
            cheap = layer3.cheap_score(r[8], rule, vocab)
            scored.append((r, cheap))
        scored.sort(key=lambda t: (-t[1], t[0][0]))
        pool = scored[: group_calc.POOL_LIMIT]
        logger.info("group pool built intent_id=%s pool_size=%d", intent_id, len(pool))
        # 手順4b: Pool全メンバー×起点のペアUPSERT(種×メンバーはここで済ませる)
        new_pair_row_ids: set[uuid.UUID] = set()
        async with self._engine.begin() as conn:
            for r, cheap in pool:
                row_id = await candidates.upsert_pair(
                    conn,
                    intent_a_id=org.intent_id,
                    intent_b_id=r[0],
                    intent_a_version=org.version,
                    intent_b_version=r[1],
                    similarity=r[8],
                    cheap_score=cheap,
                    now=org.evaluated_at,
                )
                new_pair_row_ids.add(row_id)
        # 手順5: 互換行列(Pool全員+起点・最大16)
        ids_all = [org.intent_id, *(r[0] for r, _ in pool)]
        async with self._engine.begin() as conn:
            pair_info = await _pair_compat(conn, ids_all, org.evaluated_at)
        # 手順6〜7: 貪欲法→各集合1tx(INSERT+メンバー間ペアUPSERT)
        seed = group_calc.PoolEntry(
            intent_id=org.intent_id,
            user_id=org.user_id,
            participants_min=org.participants_min,
            participants_max=org.participants_max,
        )
        entries = [
            group_calc.PoolEntry(
                intent_id=r[0],
                user_id=r[2],
                participants_min=r[3],
                participants_max=r[4],
            )
            for r, _ in pool
        ]
        groups = group_calc.build_groups(entries, seed, frozenset(pair_info))
        group_ids: set[uuid.UUID] = set()
        for ids, seed_id in groups:
            async with self._engine.begin() as conn:
                versions = await _select_group_versions(conn, ids)
                if len(versions) != len(ids):
                    logger.info("group members missing intent_id=%s", intent_id)
                    continue
                if len({v[2] for v in versions}) != len(ids):
                    logger.info("group user conflict skipped intent_id=%s", intent_id)
                    continue
                vmap = {v[0]: v for v in versions}
                member_scores = {
                    "seed_id": str(seed_id),
                    "versions": {str(i): vmap[i][1] for i in ids},
                }
                gid = await _insert_group(
                    conn, ids=ids, member_scores=member_scores, now=org.evaluated_at
                )
                if gid is None:
                    logger.info("group duplicate skipped intent_id=%s", intent_id)
                    continue
                group_ids.add(gid)
                # メンバー間ペア(起点を含まない全ペア・手順7-d)
                others = [i for i in ids if i != org.intent_id]
                for i in range(len(others)):
                    for j in range(i + 1, len(others)):
                        a, b = sorted((others[i], others[j]))
                        sim, cheap = pair_info[(a, b)]
                        row_id = await candidates.upsert_pair(
                            conn,
                            intent_a_id=a,
                            intent_b_id=b,
                            intent_a_version=vmap[a][1],
                            intent_b_version=vmap[b][1],
                            similarity=sim,
                            cheap_score=cheap,
                            now=org.evaluated_at,
                        )
                        new_pair_row_ids.add(row_id)
        # 手順8
        return GroupContext(frozenset(group_ids), frozenset(new_pair_row_ids))

    async def finalize(self, intent_id: uuid.UUID) -> None:
        """集約(design §2.5。I-1対策: 失敗しうる読取をすべて集約txの前に済ませる)。

        各評価処理の末尾で「全ペアのjev_resultが揃った集合」を確定させる。
        未判定ペア残り・H再検証不成立・読取欠損は当該集合のみスキップ
        (status=candidate保持・構造化ログ)。確定対象はD-06順
        (aggregate降順→サイズ昇順→intent_ids辞書順)にソートしてから
        集約tx+try_promoteを実行する(設計補完・supervisor裁定: try_promoteの
        上位チェックは作成済みlatchesを見るため、高位→低位の処理順で
        下位が正しく抑制される)。
        """
        async with self._engine.begin() as conn:
            loaded = await load_group_origin(conn, self._clock, intent_id)
        if loaded.skip_reason is not None or loaded.origin is None:
            logger.info(
                "group finalize origin no-op intent_id=%s reason=%s",
                intent_id,
                loaded.skip_reason,
            )
            return
        org = loaded.origin
        async with self._engine.begin() as conn:
            groups = await _select_pending_groups(conn, org.intent_id)
        # 読取フェーズ: 検査(世代リセット・全ペア揃い・H再検証)と材料読取・
        # score計算をすべて済ませ、確定対象を集める(書込はリセット/closeのみ)
        ready: list[_ReadyGroup] = []
        for gid, ids, ms, _aggregate_score in groups:
            async with self._engine.begin() as conn:
                version_rows = await _select_group_versions(conn, ids)
            vmap = {v[0]: (v[1], v[2], v[3], v[4]) for v in version_rows}
            if len(vmap) != len(ids):
                continue  # メンバー欠損(削除等)は対象外
            current = {i: vmap[i][0] for i in ids}
            # 手順c: 世代判定(引用#10: 新評価世代はprevを持たない)
            ms_versions = {uuid.UUID(k): v for k, v in ms.get("versions", {}).items()}
            if ms_versions != current:
                new_ms = {
                    "seed_id": ms.get("seed_id", str(ids[0])),
                    "versions": {str(i): current[i] for i in ids},
                }
                async with self._engine.begin() as conn:
                    await _reset_generation(conn, gid, new_ms, org.evaluated_at)
                ms = new_ms
                # リセット後は aggregate_score IS NULL(_UPDATE_AGGREGATEの
                # ガードが再計算を許す・引用#10)
            # 手順d: 全ペア揃い判定(現行version組の|S|C2ペア・引用#4)
            expected_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = sorted((ids[i], ids[j]))
                    expected_pairs.append((a, b))
            async with self._engine.begin() as conn:
                pair_rows = await _select_group_pairs(conn, ids)
            pair_map = {(_coerce_uuid(r[1]), _coerce_uuid(r[2])): r for r in pair_rows}
            mutuals: list[float] | None = []
            for a, b in expected_pairs:
                r = pair_map.get((a, b))
                if (
                    r is None
                    or r[5] is None
                    or r[3] != current[a]
                    or r[4] != current[b]
                ):
                    mutuals = None
                    break
                jev = r[5] if isinstance(r[5], dict) else json.loads(r[5])
                mutuals.append(
                    min(
                        float(jev["would_a_accept_b"]),
                        float(jev["would_b_accept_a"]),
                    )
                )
            if mutuals is None:
                continue  # 未判定ペア残り(次評価の継続枠で回収)
            # 手順e: H集合再検証(人数包含+互換行列・読取のみ)
            lo_all = max(vmap[i][2] for i in ids)
            hi_all = min(vmap[i][3] for i in ids)
            inclusion = (
                len(ids) >= group_calc.GROUP_MIN and lo_all <= len(ids) <= hi_all
            )
            compat_ok = False
            if inclusion:
                async with self._engine.begin() as conn:
                    pair_info = await _pair_compat(conn, ids, org.evaluated_at)
                compat_ok = all(key in pair_info for key in expected_pairs)
            if not compat_ok:
                async with self._engine.begin() as conn:
                    await _close_group_by_id(conn, gid, org.evaluated_at)
                continue  # 構成ペア行は閉じない(引用#12)
            # 手順f: 集約材料読取(すべてtx前・I-1対策)
            inputs: list[LatchIntentInputs] = []
            ok = True
            for iid in ids:
                inp = await _read_member_inputs(self._engine, iid)
                if inp is None:
                    ok = False
                    break
                inputs.append(inp)
            if not ok:
                continue  # aggregate NULLのまま(次の評価処理で再選択)
            target = group_calc.group_target_time(m.time_start for m in inputs)
            min_expires = min(m.expires_at for m in inputs)
            area = await self._area_name(inputs)
            score = group_calc.aggregate_score(mutuals)
            async with self._engine.begin() as conn:
                responses = await _select_group_latch_responses(conn, ids)
            has_no, latest_defer_at = latch_calc.d07_history_inputs(responses)
            ready.append(
                _ReadyGroup(
                    gid=gid,
                    ids=ids,
                    ms=ms,
                    inputs=inputs,
                    score=score,
                    target=target,
                    min_expires=min_expires,
                    area=area,
                    has_no=has_no,
                    latest_defer_at=latest_defer_at,
                )
            )
        # D-06順(aggregate降順→集合サイズ昇順→intent_ids辞書順)で集約txを実行
        ready.sort(key=lambda r: (-r.score, len(r.ids), r.ids))
        for r in ready:
            # 手順g: 集約tx(1集合1tx・I-1対策の本体)
            now = self._clock.now()
            latch_id: uuid.UUID | None = None
            async with self._engine.begin() as conn:
                row = await _update_aggregate(conn, r.gid, r.score, now)
                if row is None:
                    continue  # 冪等スキップ(他の実行が計算済み)
                prev = row[1]
                if r.score < latch_calc.LATCH_THRESHOLD:
                    continue  # candidateのまま(aggregate_scoreは入る)
                deadline0 = latch_calc.response_deadline(now, r.target, r.min_expires)
                if not latch_calc.d07_allows(
                    has_no_response=r.has_no,
                    latest_defer_at=r.latest_defer_at,
                    now=now,
                    target_time=r.target,
                    new_score=r.score,
                    prev_latch_score=prev,
                ):
                    logger.info("group d07 denied gid=%s", r.gid)
                    continue
                seed_uuid = uuid.UUID(r.ms.get("seed_id", str(r.ids[0])))
                ordered = [m for m in r.inputs if m.intent_id == seed_uuid] + [
                    m for m in r.inputs if m.intent_id != seed_uuid
                ]
                proposal = proposal_mod.build_group_proposal(
                    members=ordered, score=r.score, area_name=r.area
                )
                latch_id = await _insert_group_latch(
                    conn,
                    ids=r.ids,
                    gid=r.gid,
                    proposal=proposal,
                    score=r.score,
                    deadline=deadline0,
                    expires=r.min_expires,
                    now=now,
                )
                if latch_id is None:  # ON CONFLICT(開いている行あり)
                    found = await _find_open_group_latch(conn, r.ids)
                    if found is None or found[1] != "candidate":
                        logger.info("group open latch exists gid=%s", r.gid)
                        continue
                    if not latch_calc.d07_allows(
                        has_no_response=r.has_no,
                        latest_defer_at=r.latest_defer_at,
                        now=now,
                        target_time=r.target,
                        new_score=r.score,
                        prev_latch_score=prev,
                    ):
                        logger.info("group d07 denied on promotion gid=%s", r.gid)
                        continue
                    if not await _update_group_latch_for_promotion(
                        conn,
                        found[0],
                        score=r.score,
                        proposal=proposal,
                        deadline=deadline0,
                    ):
                        continue
                    latch_id = found[0]
                else:
                    await _insert_latch_event(
                        conn, latch_id, None, "candidate", None, now
                    )
                await _mark_group_proposed(conn, r.gid, now)
            # 手順h: tx後のtry_promote(D-06上位チェックはtry_promote内側)
            if latch_id is not None and self._latch is not None:
                await self._latch.try_promote(latch_id)

    async def _area_name(self, members: list[LatchIntentInputs]):
        """全メンバーgeo_center平均点の逆転ジオコーディング(解釈記録11)。"""
        if self._geo is None:
            return None
        lon = sum(m.geo_lon for m in members) / len(members)
        lat = sum(m.geo_lat for m in members) / len(members)
        return await self._geo.reverse_geocode(lon, lat)
