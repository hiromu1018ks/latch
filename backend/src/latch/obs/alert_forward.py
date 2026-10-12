"""alert転送器(C-01・04 v0.7・V1-P5)。

alert系3種(D-16 80%到達〔cost guard〕・Queue lag〔lag_alert〕・D-02警報線/
A/B有害シグナル〔kpi_alert〕)の媒体に「構造化ログ+Webhook転送」を与える
(04 v0.7の確定)。URL未設定(既定)は構造化ログのみ=従来挙動。

転送は発報の後置(04 v0.7): 既存のemitter(構造化ログ)を呼んだ後に非同期で
HTTP POSTするfire-and-forget。転送の失敗・タイムアウトはWARNINGログに収め
警報自体を落とさない。宛先URLの調達(Slack互換Webhook等)は運用=人間領域。

emitterはsync callable(Clock・guardと同一IF)のため、実行中のイベントループへ
create_taskでPOSTを投げる(ループ外で呼ばれた場合は転送を諦めてログのみ)。
POST本体(post_alert)はasync・httpx既存依存・試験はローカルHTTPサーバで受信証明。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import httpx

logger = logging.getLogger("latch.alert.forward")


async def post_alert(
    url: str,
    event: dict,
    *,
    timeout_s: float = 3.0,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> bool:
    """eventをJSON POSTする。成否をboolで返す(例外を出さない=発報を壊さない)。"""
    try:
        factory = client_factory or (lambda: httpx.AsyncClient(timeout=timeout_s))
        async with factory() as client:
            resp = await client.post(url, json=event)
            ok = 200 <= resp.status_code < 300
            if not ok:
                logger.warning(
                    "alert forward non-2xx url=%s status=%d", url, resp.status_code
                )
            return ok
    except Exception:
        logger.warning("alert forward failed url=%s", url, exc_info=True)
        return False


def make_forwarding_emitter(
    base_emitter: Callable[[dict], None],
    *,
    url: str,
    timeout_s: float = 3.0,
) -> Callable[[dict], None]:
    """base_emitter(構造化ログ等)の後置にWebhook転送を付けたemitter。

    URLが空ならbaseをそのまま返す(設定なし=従来挙動・転送経路を作らない)。
    POSTはfire-and-forget(発報経路の待機に転送の成否を束ねない)。
    """
    if not url:
        return base_emitter

    def emit(event: dict) -> None:
        base_emitter(event)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("alert forward skipped (no running loop)")
            return
        loop.create_task(post_alert(url, event, timeout_s=timeout_s))

    return emit
