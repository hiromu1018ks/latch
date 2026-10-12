"""プッシュトークン登録API(05 v0.9・V-01)のintegration試験。

実HTTP(compose常設api)。POST/GET/DELETE /v1/users/me/push-tokenの往復・
upsertの所有権移動(同一トークンを別ユーザーが再登録)・422(空トークン・
platform値外)・404(未登録JWT・他ユーザーへ移動済みトークンの解除)・
push_tokens表の存在(0012)を検証する。
"""

from __future__ import annotations

import asyncio
import sys
import uuid as uuid_mod

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

PREFIX = "v1p5push-"


@pytest.fixture(autouse=True)
async def _purge_push_users(db_engine):
    """各試験後にv1p5push-ユーザー系の行をFK順に全削除する(teardown完全性)。"""
    yield
    p = {"p": f"{PREFIX}%"}
    async with db_engine.begin() as conn:
        for sql in (
            # push_tokensはusers FK CASCADEで消えるが明示掃除(残余0の基準)
            "DELETE FROM push_tokens WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM users WHERE auth_subject LIKE :p",
        ):
            await conn.execute(text(sql), p)


async def _register(api_client) -> dict:
    subject = f"{PREFIX}{uuid_mod.uuid4().hex[:12]}"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        "google",
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    idp = stdout.decode().strip()
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "v1p5", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    return {
        "headers": headers,
        "subject": subject,
        "user_id": created.json()["user"]["id"],
    }


# -- スキーマ(0012) --------------------------------------------------------


async def test_push_tokens_table_exists(db_engine):
    """0012適用でpush_tokens(token UNIQUE・platform CHECK・FK CASCADE)がある。"""
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_name = 'push_tokens'"
                )
            )
        ).scalar()
        assert row == 1
        # UNIQUE(token)索引の実在(upsertの前提)
        idx = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE tablename = 'push_tokens'"
                    " AND indexname = 'uq_push_tokens_token'"
                )
            )
        ).scalar()
        assert idx == 1


# -- POST(register・upsert) ------------------------------------------------


async def test_register_roundtrip_masked_response(api_client):
    user = await _register(api_client)
    token = f"stub-{uuid_mod.uuid4().hex}"
    resp = await api_client.post(
        "/v1/users/me/push-token",
        headers=user["headers"],
        json={"token": token, "platform": "web"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()["push_token"]
    assert body["token_last4"] == token[-4:]
    assert body["platform"] == "web"
    assert "token" not in body  # トークン全文は応答へ出さない(05 v0.9 規定2)

    # GET一覧にもlast4のみ
    listed = await api_client.get("/v1/users/me/push-token", headers=user["headers"])
    assert listed.status_code == 200, listed.text
    items = listed.json()["tokens"]
    assert len(items) == 1
    assert items[0]["token_last4"] == token[-4:]


async def test_register_same_token_moves_ownership(api_client, db_engine):
    """同一トークンの別ユーザー再登録は所有権移動(409にしない・05 v0.9 規定1)。"""
    user_a = await _register(api_client)
    user_b = await _register(api_client)
    token = f"stub-{uuid_mod.uuid4().hex}"
    first = await api_client.post(
        "/v1/users/me/push-token",
        headers=user_a["headers"],
        json={"token": token},
    )
    assert first.status_code == 201, first.text
    second = await api_client.post(
        "/v1/users/me/push-token",
        headers=user_b["headers"],
        json={"token": token},
    )
    assert second.status_code == 201, second.text

    async with db_engine.connect() as conn:
        owner = (
            await conn.execute(
                text("SELECT user_id FROM push_tokens WHERE token = :t"), {"t": token}
            )
        ).scalar()
    assert str(owner) == user_b["user_id"]
    # 旧所有者の一覧から消え・新所有者に見える
    la = await api_client.get("/v1/users/me/push-token", headers=user_a["headers"])
    assert la.json()["tokens"] == []
    lb = await api_client.get("/v1/users/me/push-token", headers=user_b["headers"])
    assert [t["token_last4"] for t in lb.json()["tokens"]] == [token[-4:]]


async def test_register_re_register_same_user_idempotent(api_client, db_engine):
    user = await _register(api_client)
    token = f"stub-{uuid_mod.uuid4().hex}"
    for _ in range(2):
        resp = await api_client.post(
            "/v1/users/me/push-token", headers=user["headers"], json={"token": token}
        )
        assert resp.status_code == 201, resp.text
    async with db_engine.connect() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM push_tokens WHERE token = :t"), {"t": token}
            )
        ).scalar()
    assert n == 1


async def test_register_validation_422(api_client):
    user = await _register(api_client)
    empty = await api_client.post(
        "/v1/users/me/push-token",
        headers=user["headers"],
        json={"token": ""},
    )
    assert empty.status_code == 422, empty.text
    assert empty.json()["error"]["code"] == "VALIDATION_ERROR"
    too_long = await api_client.post(
        "/v1/users/me/push-token",
        headers=user["headers"],
        json={"token": "x" * 4097},
    )
    assert too_long.status_code == 422
    bad_platform = await api_client.post(
        "/v1/users/me/push-token",
        headers=user["headers"],
        json={"token": "t", "platform": "ios"},
    )
    assert bad_platform.status_code == 422


async def test_register_requires_auth(api_client):
    resp = await api_client.post(
        "/v1/users/me/push-token", json={"token": "t", "platform": "web"}
    )
    assert resp.status_code == 401


async def test_register_unregistered_user_404(api_client):
    """未登録JWT(users行なし)への登録は404 NOT_FOUND。"""
    subject = f"{PREFIX}{uuid_mod.uuid4().hex[:12]}"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        "google",
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    tok = await api_client.post(
        "/v1/auth/token",
        json={"provider": "google", "idp_token": stdout.decode().strip()},
    )
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    resp = await api_client.post(
        "/v1/users/me/push-token", headers=headers, json={"token": "t"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# -- DELETE ------------------------------------------------------------------


async def test_delete_roundtrip(api_client, db_engine):
    user = await _register(api_client)
    token = f"stub-{uuid_mod.uuid4().hex}"
    reg = await api_client.post(
        "/v1/users/me/push-token", headers=user["headers"], json={"token": token}
    )
    assert reg.status_code == 201, reg.text
    deleted = await api_client.request(
        "DELETE",
        "/v1/users/me/push-token",
        headers=user["headers"],
        json={"token": token},
    )
    assert deleted.status_code == 204, deleted.text
    listed = await api_client.get("/v1/users/me/push-token", headers=user["headers"])
    assert listed.json()["tokens"] == []
    # 不在トークンの再解除は404
    again = await api_client.request(
        "DELETE",
        "/v1/users/me/push-token",
        headers=user["headers"],
        json={"token": token},
    )
    assert again.status_code == 404, again.text


async def test_delete_moved_token_404(api_client):
    """他ユーザーへ所有権移動済みトークンの解除は404(存在探査に使えない形)。"""
    user_a = await _register(api_client)
    user_b = await _register(api_client)
    token = f"stub-{uuid_mod.uuid4().hex}"
    await api_client.post(
        "/v1/users/me/push-token", headers=user_a["headers"], json={"token": token}
    )
    await api_client.post(
        "/v1/users/me/push-token", headers=user_b["headers"], json={"token": token}
    )
    resp = await api_client.request(
        "DELETE",
        "/v1/users/me/push-token",
        headers=user_a["headers"],
        json={"token": token},
    )
    assert resp.status_code == 404, resp.text


# -- 退会CASCADE -------------------------------------------------------------


async def test_delete_account_removes_tokens(api_client, db_engine):
    """退会(users DELETE)でpush_tokensもFK CASCADEで消える(送信先の自動失効)。"""
    user = await _register(api_client)
    token = f"stub-{uuid_mod.uuid4().hex}"
    await api_client.post(
        "/v1/users/me/push-token", headers=user["headers"], json={"token": token}
    )
    resp = await api_client.delete("/v1/users/me", headers=user["headers"])
    assert resp.status_code == 204, resp.text
    async with db_engine.connect() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM push_tokens WHERE token = :t"), {"t": token}
            )
        ).scalar()
    assert n == 0
