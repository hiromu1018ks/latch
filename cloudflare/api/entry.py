"""Cloudflare API Worker エントリーポイント(T1 probe)。

- fetch: FastAPI appをASGI経由で提供(workers.asgi.entrypoint)
- scheduled: Cron Triggerのprobe記録(§5.4 I3のCron前提確認)
- queue: Queues consumerのprobe記録(at-least-once再配送はT5で扱う)

probeは stub のみ・外部送信なし。実配備は別承認後。
"""

from __future__ import annotations

import json
import time

from workers import asgi

from latch_worker.probes import QUEUE_MESSAGES, SCHEDULED_RUNS, create_app

app = create_app()

_Entry = asgi.entrypoint(app)


class Default(_Entry):
    """ASGI(app) + scheduled/queue の両方を持つentrypoint。"""

    async def scheduled(self, controller, env, ctx) -> None:
        SCHEDULED_RUNS.append(
            {
                "cron": controller.cron,
                "scheduled_time": controller.scheduledTime,
                "seen_at": time.time(),
                "env_label": getattr(env, "LATCH_ENV", None),
            }
        )

    async def queue(self, batch, env, ctx) -> None:
        for message in batch.messages:
            try:
                payload = json.loads(message.body)
            except Exception:  # noqa: BLE001
                payload = {"raw": str(message.body)}
            QUEUE_MESSAGES.append(
                {
                    "id": message.id,
                    "payload": payload,
                    "seen_at": time.time(),
                }
            )
            message.ack()
