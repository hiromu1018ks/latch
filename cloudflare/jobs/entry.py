"""Cloudflare Jobs Worker(T1スケルトン・queue consumer / scheduled)。

API Worker(cloudflare/api)と別プロセスになる想定のjobs側entrypoint。
T1ではQueues消費とCron起動の実証のみ行い、業務処理(jobs実行・
outbox配送)はT5で実装する。API Workerと資産を共有しない
(wranglerはworkerディレクトリ単位でuploadするため)。
"""

from __future__ import annotations

import json
import time

from workers import WorkerEntrypoint

# probe記録(プロセス内グローバル・/probe/report で読み出す)
SCHEDULED_RUNS: list[dict] = []
QUEUE_MESSAGES: list[dict] = []


class Default(WorkerEntrypoint):
    """queue consumer + scheduled + fetch(probe用の最小応答)。"""

    async def fetch(self, request, env=None, ctx=None):
        from js import Response

        url = str(request.url)
        if "/probe/report" in url:
            body = json.dumps(
                {
                    "worker": "latch-jobs",
                    "scheduled_runs": len(SCHEDULED_RUNS),
                    "queue_messages": len(QUEUE_MESSAGES),
                    "last_scheduled": SCHEDULED_RUNS[-1] if SCHEDULED_RUNS else None,
                    "last_messages": QUEUE_MESSAGES[-5:],
                }
            )
            return Response.new(body, headers={"content-type": "application/json"})
        return Response.new(
            '{"status":"ok","worker":"latch-jobs"}',
            headers={"content-type": "application/json"},
        )

    async def scheduled(self, controller, env, ctx) -> None:
        SCHEDULED_RUNS.append(
            {
                "cron": controller.cron,
                "scheduled_time": controller.scheduledTime,
                "seen_at": time.time(),
                "worker": "jobs",
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
                    "worker": "jobs",
                }
            )
            message.ack()
