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
    """ASGI(app) + scheduled/queue の両方を持つentrypoint。

    本番はマルチisolateのため、scheduled/queueの記録はメモリだけでなく
    D1(probe_events)へ永続化する(§2.3: 状態をグローバルに置かない)。
    """

    async def scheduled(self, controller, env, ctx) -> None:
        env = getattr(self, "env", None) or env  # WorkerEntrypointはself.env
        record = {
            "cron": controller.cron,
            "scheduled_time": controller.scheduledTime,
            "seen_at": time.time(),
            "env_label": getattr(env, "LATCH_ENV", None),
        }
        SCHEDULED_RUNS.append(record)
        try:
            await env.DB.batch([
                env.DB.prepare(
                    "INSERT INTO probe_events (kind, payload, created_at)"
                    " VALUES ('scheduled', ?, ?)"
                ).bind(json.dumps(record, ensure_ascii=False), int(time.time() * 1e6)),
            ])
        except Exception as exc:  # noqa: BLE001 — tailで原因確認(debug)
            print(f"probe scheduled D1 write failed: {type(exc).__name__}: {exc}")

    async def queue(self, batch, env, ctx) -> None:
        env = getattr(self, "env", None) or env  # WorkerEntrypointはself.env
        for message in batch.messages:
            try:
                payload = json.loads(str(message.body))
            except Exception:  # noqa: BLE001
                payload = {"raw": str(message.body)}
            QUEUE_MESSAGES.append(
                {"id": message.id, "payload": payload, "seen_at": time.time()}
            )
            try:
                await env.DB.batch([
                    env.DB.prepare(
                        "INSERT INTO probe_events (kind, payload, created_at)"
                        " VALUES ('queue_message', ?, ?)"
                    ).bind(
                        json.dumps(payload, ensure_ascii=False),
                        int(time.time() * 1e6),
                    ),
                ])
            except Exception as exc:  # noqa: BLE001
                print(f"probe queue D1 write failed: {type(exc).__name__}: {exc}")
            message.ack()
