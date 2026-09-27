"""users API(POST /v1/users・GET /v1/users/me)のci環境実証(M1 ws-1 design §4.2)。

実HTTP(compose api=127.0.0.1:8000)・実DB。**実行はapiイメージ再ビルド後の
make test-ci のみ**(make test-ci の compose up はイメージを再ビルドしない
— design §1.2確定値13。ws-2並走中は作成のみで、スーパーバイザーが検証時に
実行)。subjectは実行ごとにユニークな値(uuid接尾辞)を用い、User行は試験内で
DELETE(共有ci-dbの汚染回避 — M0 ws-3と同じ規約)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration


def _unique_subject(prefix: str) -> str:
    return f"m1ws1-{prefix}-{uuid_mod.uuid4().hex[:12]}"


async def _cli_idp_token(provider: str, subject: str) -> str:
    """内部ツール(python -m latch.auth)でテストユーザーJWTを発行(10 第1節)。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        provider,
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def _exchange(api_client, provider: str, subject: str) -> dict:
    idp_token = await _cli_idp_token(provider, subject)
    resp = await api_client.post(
        "/v1/auth/token", json={"provider": provider, "idp_token": idp_token}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _birth_17() -> str:
    """実行日(jst_date)時点で常に17歳になるbirth_date(design §4.2-2)。

    「18歳の誕生日の前日」= 今日の18年前同月同日の翌日。12/31実行(年跨ぎ)・
    2/28実行(うるう日近傍)のいずれでも age_years の式により常に17になる。
    """
    today = SystemClock().jst_date()
    tomorrow_birthday = date(today.year - 18, today.month, today.day) + timedelta(
        days=1
    )
    return tomorrow_birthday.isoformat()


async def _delete_users_by_subject(db_engine, subject: str) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM users WHERE auth_subject = :s"), {"s": subject}
        )


async def test_1_registration_full_flow(api_client, db_engine):
    """§4.2-1: token(false)→登録201→me 200→再token(true)。完了条件6の本体"""
    provider, subject = "google", _unique_subject("flow")
    try:
        first = await _exchange(api_client, provider, subject)
        assert first["user"] == {"id": None, "profile_complete": False}

        headers = {"Authorization": f"Bearer {first['access_token']}"}
        created = await api_client.post(
            "/v1/users",
            headers=headers,
            json={
                "display_name": "テスト太郎",
                "birth_date": "1990-04-01",
                "profile": {"bio": "よろしく"},
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert set(body) == {"user"}
        assert set(body["user"]) == {"id", "display_name"}
        user_id = body["user"]["id"]
        uuid_mod.UUID(user_id)  # UUID文字列形式(新規採番)
        assert body["user"]["display_name"] == "テスト太郎"

        me = await api_client.get("/v1/users/me", headers=headers)
        assert me.status_code == 200, me.text
        me_body = me.json()
        assert set(me_body) == {
            "id",
            "display_name",
            "profile",
            "birth_date",
            "profile_complete",
        }
        assert me_body["id"] == user_id
        assert me_body["birth_date"] == "1990-04-01"  # 申告どおりのISO文字列
        assert me_body["profile"] == {"bio": "よろしく"}  # dictとして出る
        assert me_body["profile_complete"] is True

        again = await _exchange(api_client, provider, subject)
        assert again["user"]["id"] == user_id
        assert again["user"]["profile_complete"] is True
    finally:
        await _delete_users_by_subject(db_engine, subject)


async def test_2_under_age_rejected_and_no_row(api_client, db_engine):
    """§4.2-2: 17歳相当→422 UNDER_AGE・DBに行なし"""
    subject = _unique_subject("u17")
    try:
        body = await _exchange(api_client, "google", subject)
        resp = await api_client.post(
            "/v1/users",
            headers={"Authorization": f"Bearer {body['access_token']}"},
            json={"display_name": "17歳", "birth_date": _birth_17()},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "UNDER_AGE"
        async with db_engine.connect() as conn:
            count = await conn.scalar(
                text("SELECT count(*) FROM users WHERE auth_subject = :s"),
                {"s": subject},
            )
        assert count == 0
    finally:
        await _delete_users_by_subject(db_engine, subject)


async def test_3_validation_errors(api_client):
    """§4.2-3: display_name欠落・birth_date形式不正→422 VALIDATION_ERROR"""
    body = await _exchange(api_client, "google", _unique_subject("v"))
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    missing = await api_client.post(
        "/v1/users", headers=headers, json={"birth_date": "1990-04-01"}
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"
    bad_format = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "形式不正", "birth_date": "1990/04/01"},
    )
    assert bad_format.status_code == 422
    assert bad_format.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_4_duplicate_registration_409(api_client, db_engine):
    """§4.2-4: 同一subjectでの再登録→409 USER_EXISTS(UNIQUE制約経路)"""
    subject = _unique_subject("dup")
    try:
        first = await _exchange(api_client, "google", subject)
        ok = await api_client.post(
            "/v1/users",
            headers={"Authorization": f"Bearer {first['access_token']}"},
            json={"display_name": "先着", "birth_date": "1990-04-01"},
        )
        assert ok.status_code == 201, ok.text
        # 同一subjectで新JWTを取得して再登録
        second = await _exchange(api_client, "google", subject)
        retry = await api_client.post(
            "/v1/users",
            headers={"Authorization": f"Bearer {second['access_token']}"},
            json={"display_name": "後着", "birth_date": "1990-04-01"},
        )
        assert retry.status_code == 409, retry.text
        assert retry.json()["error"]["code"] == "USER_EXISTS"
    finally:
        await _delete_users_by_subject(db_engine, subject)


async def test_5_me_without_user_row_404(api_client):
    """§4.2-5: 未登録subjectでGET /v1/users/me→404 NOT_FOUND(design §2.4)"""
    body = await _exchange(api_client, "google", _unique_subject("nf"))
    resp = await api_client.get(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_6_unauthenticated_rejected_on_both_endpoints(api_client):
    """§4.2-6: Authorization欠落・改ざんJWT→401 UNAUTHENTICATED(両エンドポイント)"""
    no_auth_post = await api_client.post(
        "/v1/users", json={"display_name": "x", "birth_date": "1990-04-01"}
    )
    assert no_auth_post.status_code == 401
    assert no_auth_post.json()["error"]["code"] == "UNAUTHENTICATED"
    no_auth_me = await api_client.get("/v1/users/me")
    assert no_auth_me.status_code == 401
    assert no_auth_me.json()["error"]["code"] == "UNAUTHENTICATED"

    body = await _exchange(api_client, "google", _unique_subject("t"))
    token = body["access_token"]
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    resp = await api_client.get(
        "/v1/users/me", headers={"Authorization": f"Bearer {tampered}"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"
