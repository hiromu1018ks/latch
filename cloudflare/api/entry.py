"""Cloudflare API Worker エントリーポイント(本体実装・2026-10-09)。

- fetch: 本体LATCH API(latch_worker.app)+ 従来のT1互換性probe(/probe/*)
  をworkers.asgi経由で提供
- scheduled: Cron(cron5分毎・probe記録をD1へ永続化)
- queue: Queues consumerの受領記録(at-least-onceはT5資産・ここでは記録+ack)

shims(_compat)はlatchパッケージimportより先に登録する(pyjwt/sqlalchemy等の
重依存をバンドルせず本体コードを再利用するため)。
"""

from __future__ import annotations

import json
import time

from workers import asgi

from latch_worker import _compat

_compat.install_shims()

from latch_worker.app import create_workers_app  # noqa: E402
from latch_worker.probes import QUEUE_MESSAGES, SCHEDULED_RUNS, create_app as create_probe_app  # noqa: E402

app = create_workers_app()

# 従来のprobe endpoint群(/probe/*)を本体アプリへ併設(cf-probe資産の存続)。
# /health は本体定義を優先させるためprobe側ルートは末尾へ追加する。
_probe = create_probe_app()
_probe_routes = [r for r in _probe.routes if getattr(r, "path", "") != "/health"]
app.router.routes.extend(_probe_routes)

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
