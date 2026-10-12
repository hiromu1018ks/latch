"""D1版AbService構築(latch.ab.service本体へD1関数を注入・05 v0.8)。

割付算法・ペア適用規則・実験行キャッシュは本体AbServiceが持ち、ここは
永続化Callableのみ(consent/surveyと同型)。パイプライン(_promote_scored_pairs)
とGET /v1/users/me/abの両方がこの構成を消費する。
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import datetime

from latch.ab.service import AbAssignmentView, AbExperiment, AbService
from latch.domain.time import from_micros, micros


def build_ab_service(db, clock) -> AbService:
    """AbService本体へD1関数を注入(ab_experiments/ab_assignments)。"""

    async def fetch_experiment(key: str) -> AbExperiment | None:
        row = (
            await db.prepare(
                "SELECT experiment_key, status, control_value, treatment_value,"
                " started_at FROM ab_experiments WHERE experiment_key = ?"
            )
            .bind(key)
            .first()
        )
        if row is None:
            return None
        return AbExperiment(
            key=row["experiment_key"],
            status=row["status"],
            control_value=row["control_value"],
            treatment_value=row["treatment_value"],
            started_at=from_micros(int(row["started_at"])),
        )

    async def fetch_user_assignments(provider: str, subject: str):
        rows = (
            await db.prepare(
                "SELECT a.experiment_key, a.variant, a.assigned_at"
                " FROM ab_assignments a JOIN users u ON u.id = a.user_id"
                " WHERE u.auth_provider = ? AND u.auth_subject = ?"
                " ORDER BY a.assigned_at"
            )
            .bind(provider, subject)
            .all()
        )
        return [
            AbAssignmentView(
                experiment=r["experiment_key"],
                variant=r["variant"],
                assigned_at=from_micros(int(r["assigned_at"])),
            )
            for r in rows
        ]

    async def fetch_assignments(key: str, user_ids):
        ids = [str(u) for u in user_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = (
            await db.prepare(
                "SELECT user_id, variant FROM ab_assignments"
                f" WHERE experiment_key = ? AND user_id IN ({placeholders})"
            )
            .bind(key, *ids)
            .all()
        )
        return {uuid_mod.UUID(r["user_id"]): r["variant"] for r in rows}

    async def count_active_intents(user_id) -> int:
        row = (
            await db.prepare(
                "SELECT COUNT(*) AS c FROM intents"
                " WHERE user_id = ? AND status = 'active'"
            )
            .bind(str(user_id))
            .first()
        )
        return int(row["c"]) if row is not None else 0

    async def insert_assignment(
        key: str, user_id, variant: str, stratum: str, now: datetime
    ) -> None:
        await db.prepare(
            "INSERT INTO ab_assignments"
            " (experiment_key, user_id, variant, stratum, assigned_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (experiment_key, user_id) DO NOTHING"
        ).bind(key, str(user_id), variant, stratum, micros(now)).first()

    return AbService(
        clock=clock,
        fetch_experiment=fetch_experiment,
        fetch_user_assignments=fetch_user_assignments,
        fetch_assignments=fetch_assignments,
        count_active_intents=count_active_intents,
        insert_assignment=insert_assignment,
    )
