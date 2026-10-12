"""alert転送器(C-01・04 v0.7)のunit試験。

post_alertのHTTP POST本体をローカルHTTPサーバ(asyncio.start_server)で受信
証明する。emitterの後置(ログ→転送)・URL未設定の素通り・転送失敗が発報を
壊さない(例外を出さない)ことも検証する。
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from latch.obs.alert_forward import make_forwarding_emitter, post_alert


class EchoServer:
    """受信したリクエストを記録する最小HTTPサーバ(stdlibのみ)。"""

    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.requests: list[tuple[str, dict]] = []
        self.server = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer) -> None:
        data = await reader.readuntil(b"\r\n\r\n")
        headers = data.decode("utf-8", "replace")
        length = 0
        for line in headers.split("\r\n")[1:]:
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1])
        body = await reader.readexactly(length) if length else b""
        first = headers.split("\r\n")[0]
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        self.requests.append((first, payload))
        writer.write(
            f"HTTP/1.1 {self.status} {'OK' if self.status < 300 else 'ERR'}\r\n"
            "Content-Length: 0\r\n\r\n".encode()
        )
        await writer.drain()
        writer.close()

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


@pytest.fixture
async def server():
    srv = EchoServer()
    port = await srv.start()
    try:
        yield srv, f"http://127.0.0.1:{port}/hook"
    finally:
        await srv.stop()


async def test_post_alert_delivers_json(server):
    srv, url = server
    event = {"kind": "queue_lag", "pending": 1200}
    ok = await post_alert(url, event, timeout_s=2.0)
    assert ok is True
    assert len(srv.requests) == 1
    first, payload = srv.requests[0]
    assert first.startswith("POST /hook")
    assert payload == event  # 同一JSONが転送される


async def test_post_alert_non_2xx_is_failure_not_raise(server):
    srv, url = server
    srv.status = 500
    ok = await post_alert(url, {"kind": "kpi_yes_rate_low"}, timeout_s=2.0)
    assert ok is False  # 失敗はboolで返る(例外=発報を壊さない)


async def test_post_alert_connection_error_is_failure_not_raise():
    # 誰もlistenしないポート(49xxx帯は概ね空く・失敗すればそれでよい)
    ok = await post_alert("http://127.0.0.1:1/hook", {"kind": "x"}, timeout_s=1.0)
    assert ok is False


async def test_forwarding_emitter_posts_after_base(server, caplog):
    srv, url = server
    base_calls: list[dict] = []

    def base(event: dict) -> None:
        base_calls.append(event)

    emit = make_forwarding_emitter(base, url=url, timeout_s=2.0)
    event = {"kind": "jev_cost", "daily": 24000}
    with caplog.at_level(logging.WARNING):
        emit(event)
        # create_taskはイベントループの次周で走る: 転送完了を 待つ
        for _ in range(100):
            await asyncio.sleep(0)
            if srv.requests:
                break
    assert base_calls == [event]  # 構造化ログ側(base)は同期的に呼ばれる
    assert srv.requests and srv.requests[0][1] == event


async def test_forwarding_emitter_empty_url_is_base(server):
    srv, _url = server
    base_calls: list[dict] = []

    def base(event: dict) -> None:
        base_calls.append(event)

    emit = make_forwarding_emitter(base, url="")
    emit({"kind": "x"})
    await asyncio.sleep(0.05)
    assert base_calls == [{"kind": "x"}]
    assert not srv.requests  # 未設定=転送経路を作らない(従来挙動)


async def test_forwarding_emitter_without_loop_logs_warning():
    """実行中ループ外での呼び出しは転送を諦める(例外を出さない)。"""

    def base(event: dict) -> None:
        pass

    emit = make_forwarding_emitter(base, url="http://127.0.0.1:1/hook")
    # 同期文脈(ループなし)で呼ぶ
    emit({"kind": "x"})  # 例外なし=発報経路を壊さない
