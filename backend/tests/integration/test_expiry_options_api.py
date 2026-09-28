"""GET /v1/intents/expiry-options のci環境実証(design §3.2・§4.2-4)。

実行は make test-ci(スーパーバイザーが実施。STATUS運用ルール1〜3)。
time_startの既定検証は実行時刻非依存にする(応答値から距離最小を再検証)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


async def _access_token(api_client) -> str:
    """内部CLIでIdPトークンを発行し、API発行JWTへ交換する(10 第1節)。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        "google",
        "--subject",
        f"m1ws5-{uuid_mod.uuid4().hex[:12]}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    resp = await api_client.post(
        "/v1/auth/token",
        json={"provider": "google", "idp_token": stdout.decode().strip()},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def test_1_requires_authentication(api_client):
    resp = await api_client.get("/v1/intents/expiry-options")
    assert resp.status_code == 401, resp.text


async def test_2_options_shape_without_time_start(api_client):
    token = await _access_token(api_client)
    resp = await api_client.get(
        "/v1/intents/expiry-options", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [o["label"] for o in body["options"]] == [
        "今夜 23:30",
        "明日 12:00",
        "明日 23:30",
        "3日後まで",
    ]
    assert body["default_index"] is None
    for option in body["options"]:
        assert set(option) == {"label", "expires_at", "selectable"}
    # 選択肢が全滅しない(3日後=now+72hは常に未来)
    assert any(o["selectable"] for o in body["options"])


async def test_3_default_index_is_nearest_to_start_plus_3h(api_client):
    token = await _access_token(api_client)
    time_start = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    resp = await api_client.get(
        "/v1/intents/expiry-options",
        params={"time_start": time_start},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    idx = body["default_index"]
    assert isinstance(idx, int) and 0 <= idx < 4
    assert body["options"][idx]["selectable"] is True
    target = datetime.fromisoformat(time_start) + timedelta(hours=3)
    distances = [
        abs(datetime.fromisoformat(o["expires_at"]).timestamp() - target.timestamp())
        for o in body["options"]
    ]
    selectable = [i for i, o in enumerate(body["options"]) if o["selectable"]]
    assert idx == min(selectable, key=lambda i: distances[i])
