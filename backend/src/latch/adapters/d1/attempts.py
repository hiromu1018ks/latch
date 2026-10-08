"""D1版・LLM試行記録(T5・計画§4)。

Gateway境界から独立させ、PostgreSQL版(records.pyの構造化ログ)と
Workers版(DB永続)の共通契約面にする:
- 送信前に prepare(試行が消えない)
- 応答後に finish(usage・実費・unknown追記)
- usage不明は cost_confirmed をNULLのまま(0円として計上しない)
- 本文を持たない(機微非混入・08 §2.4)
"""

from __future__ import annotations

import uuid as uuid_mod

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import micros

_FINISH_STATUSES = ("succeeded", "failed", "unknown")


class D1AttemptRecorder:
    """llm_attempts への記録(evaluations Gatewayから分離)。"""

    def __init__(self, db: LocalD1Database) -> None:
        self.db = db

    async def prepare(
        self,
        *,
        system: str,
        requested_provider: str,
        requested_model: str,
        price_version: str,
        job_id: str | None = None,
        evaluation_key: str | None = None,
        now,
    ) -> str:
        attempt_id = str(uuid_mod.uuid4())
        now_us = micros(now)
        await self.db.batch(
            [
                self.db.prepare(
                    "INSERT INTO llm_attempts"
                    " (attempt_id, system, job_id, evaluation_key,"
                    "  requested_provider, requested_model, price_version,"
                    "  status, started_at, prepared_at, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?)"
                ).bind(
                    attempt_id,
                    system,
                    job_id,
                    evaluation_key,
                    requested_provider,
                    requested_model,
                    price_version,
                    now_us,
                    now_us,
                    now_us,
                ),
            ]
        )
        return attempt_id

    async def finish(
        self,
        attempt_id: str,
        *,
        status: str,
        now,
        actual_provider: str | None = None,
        actual_model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_estimate_micros: int | None = None,
        cost_confirmed_micros: int | None = None,
        error_kind: str | None = None,
        sdk_internal_retry: int = 0,
    ) -> bool:
        if status not in _FINISH_STATUSES:
            raise ValueError(f"invalid finish status: {status}")
        res = (
            await self.db.prepare(
                "UPDATE llm_attempts SET status = ?, actual_provider = ?,"
                " actual_model = ?, input_tokens = ?, output_tokens = ?,"
                " cost_estimate_micros = ?, cost_confirmed_micros = ?,"
                " error_kind = ?, sdk_internal_retry = ?, finished_at = ?"
                " WHERE attempt_id = ? AND status IN ('prepared', 'sending')"
            )
            .bind(
                status,
                actual_provider,
                actual_model,
                input_tokens,
                output_tokens,
                cost_estimate_micros,
                cost_confirmed_micros,
                error_kind,
                sdk_internal_retry,
                micros(now),
                attempt_id,
            )
            .run()
        )
        return res.changes == 1

    async def get(self, attempt_id: str):
        return (
            await self.db.prepare("SELECT * FROM llm_attempts WHERE attempt_id = ?")
            .bind(attempt_id)
            .first()
        )

    async def counts_by_system(self) -> dict:
        rows = await self.db.prepare(
            "SELECT system, status, COUNT(*) AS c,"
            " SUM(COALESCE(cost_confirmed_micros, 0)) AS confirmed_micros"
            " FROM llm_attempts GROUP BY system, status"
        ).all()
        out: dict = {}
        for r in rows:
            out.setdefault(r["system"], {})[r["status"]] = {
                "count": r["c"],
                "confirmed_micros": r["confirmed_micros"] or 0,
            }
        return out
