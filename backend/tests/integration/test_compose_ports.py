"""ci常設環境(compose)への到達確認(design §4.1 integration)。

make test-ci(compose up --wait 後に pytest)から実行する。
雛形ではTCP到達と /health 応答のみ(実際のSQL試験はws-1)。
"""

import socket

import httpx
import pytest

pytestmark = pytest.mark.integration

REACHABLE = [
    ("127.0.0.1", 5432, "db"),
    ("127.0.0.1", 6379, "redis"),
]


@pytest.mark.parametrize(("host", "port", "name"), REACHABLE)
def test_reach_service(host, port, name):
    with socket.create_connection((host, port), timeout=3.0):
        pass  # 接続成立で十分(TCP到達の証明)


async def test_api_health_on_ci():
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get("http://127.0.0.1:8000/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
