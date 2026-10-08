"""Workers側ジョブ実行の組立(T5・runtime/cloudflare)。

業務計算はdomain/adaptersを、外部LLM呼出しは注入された sender で行う
(試験はstub senderで呼出し数を正確に数える — 計画§5.1)。

I1の流れ(§5.2):
  claim_evaluation(原子的lease) → 所有者だけが prepare_attempt →
  sender(外部API) → finish_attempt(usage) → complete_evaluation
  (fencing token・Intent版を確認した時だけ結果確定)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from latch.adapters.d1.evaluations import D1EvaluationGateway
from latch.adapters.d1.jobs import D1JobCoordinator
from latch.domain.types import EvaluationKey


@dataclass(frozen=True)
class SendResult:
    status: str  # succeeded / failed / unknown
    actual_provider: str | None = None
    actual_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_confirmed_micros: int | None = None
    error_kind: str | None = None
    sdk_internal_retry: int = 0
    payload: dict[str, Any] | None = None


Sender = Callable[[EvaluationKey], Awaitable[SendResult]]


class TimeoutError_(Exception):
    """senderが結果不明(timeout)を表明する例外(cost不明の典型)。"""


class EvaluationJobRunner:
    """I1(処理前の確保)+§4(全試行の記録)に従う評価実行。"""

    def __init__(
        self,
        gateway: D1EvaluationGateway,
        coordinator: D1JobCoordinator | None = None,
    ) -> None:
        self.gateway = gateway
        self.coordinator = coordinator

    async def run(
        self,
        key: EvaluationKey,
        *,
        owner: str,
        sender: Sender,
        job_id: str | None = None,
        requested_provider: str,
        requested_model: str,
        price_version: str,
        now,
    ) -> dict[str, Any]:
        """1評価の実行。戻り値: {claimed, called, completed, stale}。"""
        claim = await self.gateway.claim_evaluation(key, owner, now)
        if not claim.acquired:
            return {"claimed": False, "called": 0, "completed": False}
        attempt_id = await self.gateway.prepare_attempt(
            system=key.kind,
            requested_provider=requested_provider,
            requested_model=requested_model,
            price_version=price_version,
            job_id=job_id,
            evaluation_key=self.gateway._key_text(key),
            now=now,
        )
        called = 0
        try:
            sent = await sender(key)
            called = 1
            await self.gateway.finish_attempt(
                attempt_id,
                status=sent.status,
                now=now,
                actual_provider=sent.actual_provider,
                actual_model=sent.actual_model,
                input_tokens=sent.input_tokens,
                output_tokens=sent.output_tokens,
                cost_confirmed_micros=sent.cost_confirmed_micros,
                error_kind=sent.error_kind,
                sdk_internal_retry=sent.sdk_internal_retry,
            )
            completed = await self.gateway.complete_evaluation(
                key, claim.fencing_token, {"result": sent.payload}, now
            )
            return {
                "claimed": True,
                "called": called,
                "completed": completed,
                "stale": not completed,
                "attempt_id": attempt_id,
            }
        except TimeoutError_:
            # 送信後のtimeout: 結果不明(課金された可能性)。即再送しない
            await self.gateway.finish_attempt(
                attempt_id,
                status="unknown",
                now=now,
                error_kind="timeout",
            )
            if self.coordinator is not None and job_id is not None:
                # jobはoutcome_unknownとして残す(§4の回収方針対象)
                await self.gateway.db.batch(
                    [
                        self.gateway.db.prepare(
                            "UPDATE jobs SET status = 'outcome_unknown',"
                            " last_error = 'outcome_unknown'"
                            " WHERE id = ?"
                        ).bind(job_id),
                    ]
                )
            return {
                "claimed": True,
                "called": 1,
                "completed": False,
                "unknown": True,
                "attempt_id": attempt_id,
            }
