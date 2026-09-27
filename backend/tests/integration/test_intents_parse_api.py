"""POST /v1/intents/parse のci環境実証(design §4.2)。

実HTTP(compose api=127.0.0.1:8000)。実行は make test-ci — composeのapiは
build型・ソースマウントなしのため、コード変更後は必ず
`docker compose build api` を先に行うこと(Makefileのtest-ciは再ビルドしない)。
parseはDB書込みゼロ(05 §5 SP-4)のためUser行INSERTも掃除も行わない(§4.2-6)。
subjectは毎回ユニーク値(未登録=user_id=None経路の検証を兼ねる)。
"""

import asyncio
import sys
import uuid as uuid_mod

import pytest

pytestmark = pytest.mark.integration

# latch.llm.stub.DEFAULT_PARSER_RESPONSE と同形の期待値(イメージ内コード由来・確定)
EXPECTED_STUB_INTENT = {
    "category": {"primary": "meal", "secondary": None},
    "alcohol_involved": False,
    "time": {
        "start": "2026-09-27T19:00:00+09:00",
        "end": None,
        "flexibility_minutes": None,
    },
    "location": {"name": "東京駅", "radius_m": None, "flexibility": None},
    "budget": {"max": None, "currency": "JPY"},
    "participants": {"min": None, "max": None},
    "soft_constraints": [],
    "negative_constraints": [],
    "ng_unverifiable": [],
}


def _unique_subject() -> str:
    return f"m1ws2-{uuid_mod.uuid4().hex[:12]}"


async def _access_token(api_client) -> str:
    """内部ツールでIdPトークンを発行し、API発行JWTへ交換する(10 第1節)。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        "google",
        "--subject",
        _unique_subject(),
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


async def _parse(api_client, token: str, json=None, content=None):
    headers = {"Authorization": f"Bearer {token}"}
    if content is not None:
        return await api_client.post(
            "/v1/intents/parse", headers=headers, content=content
        )
    return await api_client.post("/v1/intents/parse", headers=headers, json=json)


async def test_1_happy_path_returns_stub_shaped_intent(api_client):
    """§4.2-1: 200・structured_intent=スタブ既定応答と同形・warnings=[]。"""
    token = await _access_token(api_client)
    resp = await _parse(
        api_client,
        token,
        json={"text": "今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"structured_intent", "warnings"}
    assert body["structured_intent"] == EXPECTED_STUB_INTENT
    assert body["warnings"] == []


async def test_2_text_length_limits(api_client):
    """§4.2-2: 300字ちょうどは200・301字は422(切り詰めなし・07 §2)。"""
    token = await _access_token(api_client)
    ok = await _parse(api_client, token, json={"text": "あ" * 300})
    assert ok.status_code == 200, ok.text
    over = await _parse(api_client, token, json={"text": "あ" * 301})
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_3_missing_text_and_malformed_json(api_client):
    """§4.2-3: text欠落=422 VALIDATION_ERROR / JSON破損=400 MALFORMED_REQUEST。"""
    token = await _access_token(api_client)
    missing = await _parse(api_client, token, json={})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"
    malformed = await api_client.post(
        "/v1/intents/parse",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        content=b"{not valid json",
    )
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "MALFORMED_REQUEST"


async def test_4_authentication_required(api_client):
    """§4.2-4: Authorization欠落・改ざんJWT → 401 UNAUTHENTICATED。"""
    no_header = await api_client.post("/v1/intents/parse", json={"text": "t"})
    assert no_header.status_code == 401
    assert no_header.json()["error"]["code"] == "UNAUTHENTICATED"
    forged = await api_client.post(
        "/v1/intents/parse",
        headers={"Authorization": "Bearer forged.jwt.value"},
        json={"text": "t"},
    )
    assert forged.status_code == 401
    assert forged.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_5_unregistered_subject_can_parse(api_client):
    """§4.2-5: User行のないsubjectでも200(User行をparseの前提にしない)。"""
    token = await _access_token(api_client)  # users行は作らない
    resp = await _parse(api_client, token, json={"text": "今夜20時から軽く飲みたい"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["structured_intent"] == EXPECTED_STUB_INTENT
