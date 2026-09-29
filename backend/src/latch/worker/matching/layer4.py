"""Layer 4の選択SQL・H再検証・配分純関数(06 §5・§8 D-24・M2 ws-5)。

design §2.5〜§2.7。K_j=8(1イベント処理あたりのJev実行上限・回数ベース)で
cheap_judge_score降順上位を取り、同一評価世代スキップ(FR-07: jev_result
IS NULL)と再選択規則(§2.7の4分岐)をSQLで表現する。H再検証はLayer 1の
LAYER1_WHEREをimportして再利用する(試験と本番のWHERE乖離なし — ws-3規律
の継承・layer1.pyは変更しない)。
SQLはtext()生SQL・CAST(:x AS ...)形式(§2グローバル制約)。時刻境界
(jst_day_start/jst_month_start)はClock.jst_date()から導出する(C2)。
グループマッチ(ws-7)の拡張点: pair_kindタグとselect_jev_targets。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.core.clock import JST
from latch.worker.matching.layer1 import LAYER1_WHERE
from latch.worker.matching.origin import Origin, bind_params

K_J = 8  # 06 §8 D-24(1イベント処理あたりのJev実行上限)
ONE_ON_ONE_MIN = 4  # 1対1最低保証(引用#11)
PAIR_KIND_ONE_ON_ONE = "one_on_one"  # ws-7がグループ種別を追加する拡張点

# deny理由のうち予算系=期間切れ後・障害系=即時(design §2.7)
RESELECT_ALWAYS = ("llm_failure", "invalid_output")
RESELECT_DAILY = ("intent_daily", "user_daily", "global_daily")
RESELECT_MONTHLY = ("global_monthly",)

_SELECT_JEV_ROWS = text("""
    SELECT mc.id, mc.intent_a_id, mc.intent_b_id, mc.intent_a_version,
           mc.intent_b_version, mc.cheap_judge_score, mc.status, mc.skip_reason
    FROM match_candidates mc
    WHERE (mc.intent_a_id = CAST(:origin AS uuid)
           OR mc.intent_b_id = CAST(:origin AS uuid))
      AND (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                THEN mc.intent_a_version
                ELSE mc.intent_b_version END) = :origin_version
      AND mc.jev_result IS NULL
      AND (
        mc.status = 'pending'
        OR (mc.status = 'skipped'
            AND mc.skip_reason IN ('llm_failure', 'invalid_output'))
        OR (mc.status = 'skipped'
            AND mc.skip_reason IN ('intent_daily', 'user_daily', 'global_daily')
            AND mc.updated_at < CAST(:jst_day_start AS timestamptz))
        OR (mc.status = 'skipped' AND mc.skip_reason = 'global_monthly'
            AND mc.updated_at < CAST(:jst_month_start AS timestamptz))
      )
    ORDER BY mc.cheap_judge_score DESC,
             CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                  THEN mc.intent_b_id
                  ELSE mc.intent_a_id END ASC
    LIMIT 8
""")

_H_RECHECK = text(f"""
    SELECT 1 FROM intents i JOIN users u ON u.id = i.user_id
    WHERE i.id = CAST(:candidate_id AS uuid)
      AND {LAYER1_WHERE}
    LIMIT 1
""")

_CLOSE_BROKEN = text(f"""
    UPDATE match_candidates mc
    SET status = 'closed', updated_at = :now
    WHERE (mc.intent_a_id = CAST(:origin AS uuid)
           OR mc.intent_b_id = CAST(:origin AS uuid))
      AND (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                THEN mc.intent_a_version
                ELSE mc.intent_b_version END) = :origin_version
      AND mc.jev_result IS NOT NULL
      AND mc.status = 'evaluated'
      AND NOT EXISTS (
        SELECT 1 FROM intents i JOIN users u ON u.id = i.user_id
        WHERE i.id = (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                           THEN mc.intent_b_id
                           ELSE mc.intent_a_id END)
          AND {LAYER1_WHERE}
      )
    RETURNING mc.id
""")


@dataclass(frozen=True)
class JevCandidateRow:
    """_SELECT_JEV_ROWSの1行(design §2.5)。pair_kindはws-7拡張点。"""

    row_id: uuid.UUID
    intent_a_id: uuid.UUID
    intent_b_id: uuid.UUID
    intent_a_version: int
    intent_b_version: int
    cheap_judge_score: float | None
    status: str
    skip_reason: str | None
    pair_kind: str = PAIR_KIND_ONE_ON_ONE


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策 — 3度目の教訓)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def jst_day_start(d: date) -> datetime:
    """JST暦日の0時(再選択境界。C2: Clock.jst_date()から導出)。"""
    return datetime.combine(d, time(0, 0), tzinfo=JST)


def jst_month_start(d: date) -> datetime:
    """JST暦月の月初0時(再選択境界)。"""
    return datetime.combine(d.replace(day=1), time(0, 0), tzinfo=JST)


async def select_jev_rows(
    conn: AsyncConnection,
    origin_id: uuid.UUID,
    origin_version: int,
    day_start: datetime,
    month_start: datetime,
) -> list[JevCandidateRow]:
    """起点の現在versionにおける未評価ペアから上位K_j(§2.5 SQL)。

    Python引数名はday_start/month_start(モジュール関数jst_day_start/
    jst_month_startとの衝突回避)。SQLのbind param名はjst_day_start/
    jst_month_startのまま。LIMIT 8はK_Jと同値(二重防御)。
    """
    rows = (
        await conn.execute(
            _SELECT_JEV_ROWS,
            {
                "origin": origin_id,
                "origin_version": origin_version,
                "jst_day_start": day_start,
                "jst_month_start": month_start,
            },
        )
    ).all()
    return [
        JevCandidateRow(
            row_id=_coerce_uuid(r[0]),
            intent_a_id=_coerce_uuid(r[1]),
            intent_b_id=_coerce_uuid(r[2]),
            intent_a_version=r[3],
            intent_b_version=r[4],
            cheap_judge_score=None if r[5] is None else float(r[5]),
            status=r[6],
            skip_reason=r[7],
        )
        for r in rows
    ]


def select_jev_targets(rows: list[JevCandidateRow]) -> list[JevCandidateRow]:
    """K_j配分の純関数(ws-5時点は恒等+上限再適用)。

    ws-5では入力は全て1対1のため順序維持のままK_jで打ち切り。グループ由来
    ペアの規則(1)〜(4)(06 §8 D-24)はws-7がこの関数の拡張として追加する。
    """
    return [r for r in rows if r.pair_kind == PAIR_KIND_ONE_ON_ONE][:K_J]


async def hard_constraint_holds(
    conn: AsyncConnection, origin: Origin, candidate_id: uuid.UUID
) -> bool:
    """H再検証(1対1判定): 相手Intentが現時点でもLayer 1を通るか(§2.6)。"""
    params = bind_params(origin)
    params["candidate_id"] = candidate_id
    row = (await conn.execute(_H_RECHECK, params)).first()
    return row is not None


async def close_broken_pairs(
    conn: AsyncConnection, origin: Origin, now: datetime
) -> int:
    """H不成立行の一括close(同一バージョン組のevaluated行のみ。§2.6)。

    pending行は閉じない(jev_result未評価のため再選択対象のまま)。
    bind_params(origin)に含まれるnow(origin.evaluated_at)と同一値の再設定
    となるが害はない(§9-8)。
    """
    params = bind_params(origin)
    params["origin"] = origin.intent_id
    params["origin_version"] = origin.version
    params["now"] = now
    rows = (await conn.execute(_CLOSE_BROKEN, params)).all()
    return len(rows)
