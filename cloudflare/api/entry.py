"""Cloudflare API Worker エントリーポイント(本体実装・2026-10-10)。

- fetch: 本体LATCH API(latch_worker.app)+ 従来のT1互換性probe(/probe/*)
  をworkers.asgi経由で提供
- scheduled: cron(*/5)でマッチングパイプライン1周期(schedule評価・jobs回収・
  outbox掃き・notify/retention job・run_retention)
- queue: outbox envelope(created/updated)を受けて評価実行
  (T4検索+cheap judge+stub jev→apply_proposal・D-08上限・通知job)

shims(_compat)はlatchパッケージimportより先に登録する(pyjwt/sqlalchemy等の
重依存をバンドルせず本体コードを再利用するため)。LLMはstub確定
(実LLM呼出しは承認待ちのまま・外部送信なし)。

実配備probe差異への対応: scheduled/queueのenvはself.envから取得・
QueuesへはJSON文字列で送る・JsProxy値は明示変換・状態はD1永続化
(メモリ内グローバルに置かない)。
"""

from __future__ import annotations

import json
import time

from workers import asgi

from latch_worker import _compat

_compat.install_shims()

from latch_worker.app import create_workers_app  # noqa: E402
from latch_worker.probes import SCHEDULED_RUNS, create_app as create_probe_app  # noqa: E402

app = create_workers_app()

# 従来のprobe endpoint群(/probe/*)を本体アプリへ併設(cf-probe資産の存続)。
# /health は本体定義を優先させるためprobe側ルートは末尾へ追加する。
_probe = create_probe_app()
_probe_routes = [r for r in _probe.routes if getattr(r, "path", "") != "/health"]
app.router.routes.extend(_probe_routes)

_Entry = asgi.entrypoint(app)


def _plain(value) -> str:
    """JsProxy等の表示値を素のPython文字列へ(差異#5・json.dumps要件)。"""
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return repr(value)


class Default(_Entry):
    """ASGI(app) + scheduled/queue の両方を持つentrypoint。"""

    async def scheduled(self, controller, env, ctx) -> None:
        env = getattr(self, "env", None) or env  # WorkerEntrypointはself.env
        from datetime import UTC, datetime

        from latch_worker.pipeline import build_pipeline, run_cron

        scheduled_time = _plain(getattr(controller, "scheduledTime", ""))
        cron_expr = _plain(getattr(controller, "cron", ""))
        try:
            services = build_pipeline(env)
            summary = await run_cron(
                services,
                now=datetime.now(UTC),
                queue_producer=getattr(env, "JOBS", None),
            )
            outcome = {"ok": True, **summary}
        except Exception as exc:  # noqa: BLE001 — 証跡を残して次周期で再試行
            outcome = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        SCHEDULED_RUNS.append({"cron": cron_expr, "seen_at": time.time()})
        # 稼働証跡(observability offのためprobe_eventsへ要点のみ。
        # envelope・summaryはID・件数のみで原文・条件本文を含まない)
        try:
            await env.DB.batch([
                env.DB.prepare(
                    "INSERT INTO probe_events (kind, payload, created_at)"
                    " VALUES ('scheduled', ?, ?)"
                ).bind(
                    json.dumps(
                        {
                            "cron": cron_expr,
                            "scheduled_time": scheduled_time,
                            "outcome": outcome,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                    int(time.time() * 1e6),
                ),
            ])
        except Exception as exc:  # noqa: BLE001
            print(f"cron record failed: {type(exc).__name__}: {exc}")

    async def queue(self, batch, env, ctx) -> None:
        env = getattr(self, "env", None) or env  # WorkerEntrypointはself.env
        from latch_worker.pipeline import build_pipeline, handle_queue_message

        services = build_pipeline(env)
        for message in batch.messages:
            try:
                await handle_queue_message(services, str(message.body))
                message.ack()
            except Exception as exc:  # noqa: BLE001
                # 再配送を要求(max_retries超過のraiseは無視 — scheduleの
                # 30分bucket再評価が到達保証を持つ)
                print(f"queue message failed: {type(exc).__name__}: {exc}")
                try:
                    message.retry()
                except Exception:  # noqa: BLE001
                    pass
