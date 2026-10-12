"""A/B割付の解決サービス(05 v0.8 第2節・09 §5・SurveyServiceと同型のCallable注入)。

参照実装(PostgreSQL)と移行実装(D1)の双方から構築できる形状:
SQL実行はすべて注入されたCallableが担い、本クラスは割付算法・ペア適用
規則・実験行キャッシュの所有者。呼び出し側は提案評価(worker/pipeline)。

重要な規律:
- 実験行が存在しない・status!='running'・開始前 → 既定値(現行定数)を返す
  (実験を1行も作らない環境の挙動は従来と一字変わらない・05 v0.8 規定5)
- 解決中の例外は握って既定値へ落とす(マッチング可用性を実験テーブルの
  故障より優先する。警告ログのみ残す)
- この経路の値はログへ出さない(実験デザインの推定を避ける。件数集計は
  別経路のkpiによる)
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from latch.ab import rules
from latch.core.clock import Clock

logger = logging.getLogger("latch.ab")

# 適用点の既定値(対照値=現行運用値)。latch_calcから遅延importしない(
# 依存方向を一方向に保つため値をここへ写す — 変更はピン試験が検出する)
DEFAULT_LATCH_THRESHOLD = 0.60
DEFAULT_NOTIFICATION_DAILY = 6
DEFAULT_NOTIFICATION_CONCURRENT = 3

EXPERIMENT_CACHE_TTL_S = 30.0  # 05 v0.8 実装規定7(伝播の遅延許容)


@dataclass(frozen=True)
class AbExperiment:
    """ab_experiments行(05 v0.8)。"""

    key: str
    status: str
    control_value: str
    treatment_value: str
    started_at: datetime


@dataclass(frozen=True)
class AbAssignmentView:
    """GET /v1/users/me/ab の応答本体(05 v0.8。stratumを含めない)。"""

    experiment: str
    variant: str
    assigned_at: datetime


FetchExperimentFn = Callable[[str], Awaitable[AbExperiment | None]]
FetchUserAssignmentsFn = Callable[
    [str, str], Awaitable[list[AbAssignmentView]]  # (auth_provider, auth_subject)
]
FetchAssignmentsFn = Callable[
    [str, Sequence[uuid.UUID]], Awaitable[dict[uuid.UUID, str]]
]
CountActiveIntentsFn = Callable[[uuid.UUID], Awaitable[int]]
InsertAssignmentFn = Callable[[str, uuid.UUID, str, str, datetime], Awaitable[None]]


class AbService:
    """割付の解決(初回適用時レイジー)とペア適用値の導出。"""

    def __init__(
        self,
        *,
        clock: Clock,
        fetch_experiment: FetchExperimentFn,
        fetch_user_assignments: FetchUserAssignmentsFn,
        fetch_assignments: FetchAssignmentsFn,
        count_active_intents: CountActiveIntentsFn,
        insert_assignment: InsertAssignmentFn,
        cache_ttl_s: float = EXPERIMENT_CACHE_TTL_S,
    ) -> None:
        self._clock = clock
        self._fetch_experiment = fetch_experiment
        self._fetch_user_assignments = fetch_user_assignments
        self._fetch_assignments = fetch_assignments
        self._count_active_intents = count_active_intents
        self._insert_assignment = insert_assignment
        self._cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[AbExperiment | None, float]] = {}

    # -- 観測API面(GET /v1/users/me/ab・割付を作らない) -------------------

    async def assignments_for_user(self, claims) -> list[AbAssignmentView]:
        """claims(auth_provider/auth_subject)から自分の割付一覧(survey流の解決)。"""
        provider, subject = claims.auth_provider, claims.auth_subject
        return list(await self._fetch_user_assignments(provider, subject))

    # -- 実験行(短TTLキャッシュ) -------------------------------------------

    async def _running_experiment(self, key: str) -> AbExperiment | None:
        now = self._clock.now().timestamp()
        cached = self._cache.get(key)
        if cached is not None and now - cached[1] < self._cache_ttl_s:
            exp = cached[0]
        else:
            try:
                exp = await self._fetch_experiment(key)
            except Exception:
                logger.warning("ab fetch_experiment failed key=%s", key, exc_info=True)
                exp = None
            self._cache[key] = (exp, now)
        if exp is None or exp.status != "running":
            return None
        if self._clock.now() < exp.started_at:
            return None
        return exp

    async def _variants(
        self, exp: AbExperiment, user_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """参加者のvariant(未割当はその場で割付=初回適用時レイジー)。"""
        try:
            found = dict(await self._fetch_assignments(exp.key, user_ids))
        except Exception:
            logger.warning("ab fetch_assignments failed key=%s", exp.key, exc_info=True)
            return {u: rules.VARIANT_CONTROL for u in user_ids}
        now = self._clock.now()
        for uid in user_ids:
            if uid in found:
                continue
            active = await self._count_active_intents(uid)
            stratum = rules.stratum_of(active)
            variant = rules.assign_variant(exp.key, stratum, uid)
            try:
                await self._insert_assignment(exp.key, uid, variant, stratum, now)
                # 挿入後に読み直した行を真実とする(並行初回割付の仲裁)
                refetched = dict(await self._fetch_assignments(exp.key, [uid]))
                found[uid] = refetched.get(uid, variant)
            except Exception:
                logger.warning(
                    "ab insert_assignment failed key=%s", exp.key, exc_info=True
                )
                found[uid] = variant
        return found

    async def _pair_value(self, key: str, user_ids: Sequence[uuid.UUID]) -> str | None:
        """ペア適用値(text)。実験が走っていない場合はNone(呼び出し側は既定値)。"""
        exp = await self._running_experiment(key)
        if exp is None:
            return None
        variants = await self._variants(exp, user_ids)
        variant = rules.pair_variant(variants.get(u) for u in user_ids)
        return rules.value_for(variant, exp.control_value, exp.treatment_value)

    # -- 適用点別の型つき解決(例外は既定値へ落ちる・05 v0.8 規定5) --------

    async def pair_threshold(self, user_ids: Sequence[uuid.UUID]) -> float:
        raw = await self._pair_value(rules.KEY_LATCH_THRESHOLD, user_ids)
        if raw is None:
            return DEFAULT_LATCH_THRESHOLD
        return rules.parse_float(raw, DEFAULT_LATCH_THRESHOLD)

    async def pair_daily_limit(self, user_ids: Sequence[uuid.UUID]) -> int:
        raw = await self._pair_value(rules.KEY_NOTIFICATION_DAILY, user_ids)
        if raw is None:
            return DEFAULT_NOTIFICATION_DAILY
        return rules.parse_int(raw, DEFAULT_NOTIFICATION_DAILY)

    async def pair_concurrent_limit(self, user_ids: Sequence[uuid.UUID]) -> int:
        raw = await self._pair_value(rules.KEY_NOTIFICATION_CONCURRENT, user_ids)
        if raw is None:
            return DEFAULT_NOTIFICATION_CONCURRENT
        return rules.parse_int(raw, DEFAULT_NOTIFICATION_CONCURRENT)

    async def drain_threshold(self) -> float:
        """drain対象抽出の閾値 = 対照値・処置値の小さい方(05 v0.8 規定5)。"""
        exp = await self._running_experiment(rules.KEY_LATCH_THRESHOLD)
        if exp is None:
            return DEFAULT_LATCH_THRESHOLD
        control = rules.parse_float(exp.control_value, DEFAULT_LATCH_THRESHOLD)
        treatment = rules.parse_float(exp.treatment_value, DEFAULT_LATCH_THRESHOLD)
        return min(control, treatment)


# -- 参照実装(PostgreSQL)の構築 ------------------------------------------------
# D1側はlatch_worker.svc_abが同じCallable形状で別実装を渡す(state.py配線)。


def _uuid_array(user_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    """uuid[]のbind param値(group_calc.uuid_arrayと同一規律: 要素はUUIDのまま
    渡しSQL側のCASTで型を確定させる — 文字列リテラルはasyncpgが拒否する)。"""
    return list(user_ids)


def make_ab_service(*, clock: Clock, engine) -> AbService:
    """実SQL関数(text())を束ねてAbServiceを構築する(参照実装・PostgreSQL)。"""
    from sqlalchemy import text  # CF側では_compat shimが提供する

    select_experiment = text("""
        SELECT experiment_key, status, control_value, treatment_value, started_at
        FROM ab_experiments WHERE experiment_key = :key
    """)
    select_user_assignments = text("""
        SELECT a.experiment_key, a.variant, a.assigned_at FROM ab_assignments a
        JOIN users u ON u.id = a.user_id
        WHERE u.auth_provider = :provider AND u.auth_subject = :subject
        ORDER BY a.assigned_at
    """)
    select_assignments = text("""
        SELECT user_id, variant FROM ab_assignments
        WHERE experiment_key = :key AND user_id = ANY(CAST(:users AS uuid[]))
    """)
    count_active = text("""
        SELECT count(*) FROM intents
        WHERE user_id = CAST(:user_id AS uuid) AND status = 'active'
    """)
    insert_assignment_sql = text("""
        INSERT INTO ab_assignments
            (experiment_key, user_id, variant, stratum, assigned_at)
        VALUES (:key, CAST(:user_id AS uuid), :variant, :stratum,
                CAST(:now AS timestamptz))
        ON CONFLICT DO NOTHING
    """)

    async def fetch_experiment(key: str) -> AbExperiment | None:
        async with engine.connect() as conn:
            row = (await conn.execute(select_experiment, {"key": key})).first()
        if row is None:
            return None
        return AbExperiment(
            key=row[0],
            status=row[1],
            control_value=row[2],
            treatment_value=row[3],
            started_at=row[4],
        )

    async def fetch_user_assignments(
        provider: str, subject: str
    ) -> list[AbAssignmentView]:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    select_user_assignments,
                    {"provider": provider, "subject": subject},
                )
            ).all()
        return [
            AbAssignmentView(experiment=r[0], variant=r[1], assigned_at=r[2])
            for r in rows
        ]

    async def fetch_assignments(
        key: str, user_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        if not user_ids:
            return {}
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    select_assignments,
                    {"key": key, "users": _uuid_array(user_ids)},
                )
            ).all()
        return {u: v for u, v in ((r[0], r[1]) for r in rows)}

    async def count_active_intents(user_id: uuid.UUID) -> int:
        async with engine.connect() as conn:
            return int(
                (
                    await conn.execute(count_active, {"user_id": str(user_id)})
                ).scalar_one()
            )

    async def insert_assignment(
        key: str,
        user_id: uuid.UUID,
        variant: str,
        stratum: str,
        now: datetime,
    ) -> None:
        async with engine.begin() as conn:
            await conn.execute(
                insert_assignment_sql,
                {
                    "key": key,
                    "user_id": str(user_id),
                    "variant": variant,
                    "stratum": stratum,
                    "now": now,
                },
            )

    return AbService(
        clock=clock,
        fetch_experiment=fetch_experiment,
        fetch_user_assignments=fetch_user_assignments,
        fetch_assignments=fetch_assignments,
        count_active_intents=count_active_intents,
        insert_assignment=insert_assignment,
    )
