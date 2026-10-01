"""プッシュ送信(M3 ws-3 design §2.2)。

PushSenderはユーザ単位の抽象(トークン解決はreal実装の内部)。StubPushSender
はネットワーク呼び出しを一切行わないドライラン: build_pushの文言で
PushSendRecord(status=ok)を出す。実FCM(FirebasePushSender)はG3後の
"real"実装(Admin SDKのsend_each_async/dry_run形を模倣したIF・design §2.2)。
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from latch.core.clock import Clock
from latch.notifications.records import PushSendRecord, SendStatus, push_log
from latch.notifications.templates import build_push
from latch.settings import Settings

PUSH_TIMEOUT_S = 3.0  # design §2.2(コード定数・unit試験で短縮注入)


class PushSender(Protocol):
    """送信IF(design §2.2)。例外を出さない(記録して握る)。戻り値なし。"""

    name: str

    async def send(self, *, user_id, notification_type: str, latch_id) -> None: ...


class StubPushSender:
    """テスト/ドライラン用スタブ(10 第1節)。name="stub"。

    delay_ms>0なら asyncio.sleep を挟む(レイテンシ注入 — LLMスタブの
    _apply_delayと同型・待機であり時刻参照ではない)。timeout打ち切りは
    asyncio.timeout(gateway._callと同型)。fail=Trueなら送出例外を発生させ
    error記録の経路試験に使う(§4.1-2)。
    """

    def __init__(
        self,
        *,
        clock: Clock,
        delay_ms: int = 0,
        timeout_s: float = PUSH_TIMEOUT_S,
        fail: bool = False,
    ) -> None:
        self.name = "stub"
        self._clock = clock
        self._delay_ms = delay_ms
        self._timeout_s = timeout_s
        self._fail = fail

    async def send(self, *, user_id, notification_type: str, latch_id) -> None:
        """ドライラン送信。例外を出さない(§2.1)。ただし未知typeの
        ValueError(fail-fast)は握らない(§2.7)。"""
        title, body = build_push(notification_type)
        occurred_at = self._clock.now()
        status: SendStatus = "ok"
        error_code: str | None = None
        try:
            async with asyncio.timeout(self._timeout_s):
                if self._delay_ms > 0:
                    # 待機であり時刻参照ではない(arch test禁止対象外)
                    await asyncio.sleep(self._delay_ms / 1000)
                if self._fail:
                    raise RuntimeError("stub push failure (injected)")
        except TimeoutError:
            status = "timeout"
            error_code = "PushTimeoutError"
        except Exception as exc:  # 送信側例外は握る(§2.7)
            status = "error"
            error_code = type(exc).__name__
        push_log(
            PushSendRecord(
                occurred_at=occurred_at,
                user_id=str(user_id),
                notification_type=notification_type,
                latch_id=str(latch_id),
                title=title,
                body=body,
                status=status,
                error_code=error_code,
            )
        )


def build_push_sender(clock: Clock, settings: Settings) -> PushSender:
    """設定からPushSenderを構築(worker/main.py配線用・design §2.2)。

    "real"(実FCM)はG3後。未実装値はValueError — 静かにスタブへ落ちない
    規律(llm_modeのbuild_llm_gatewayと同一)。
    """
    if settings.push_mode == "stub":
        return StubPushSender(clock=clock, delay_ms=settings.push_stub_delay_ms)
    raise ValueError(
        f"unknown push_mode: {settings.push_mode!r} "
        "('stub' only — 'real' arrives after G3)"
    )
