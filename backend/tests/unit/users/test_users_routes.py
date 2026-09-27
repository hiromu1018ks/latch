"""usersルータ: 応答形状・エラーenvelope・400/422振り分け(design §4.1-4)。

create_app へスタブUserServiceを注入し、require_authenticated は
dependency_overrides で差し替える(users単体の試験でauthスタックを構築しない
— design §2.5。401経路の実証はintegration #6が所有)。
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.main import create_app
from latch.settings import Settings
from latch.users.errors import UnderAgeError, UserNotFoundError
from latch.users.service import UserCreated, UserMe

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
USER_ID = uuid.uuid4()

CLAIMS = AccessTokenClaims(
    auth_provider="google",
    auth_subject="sub-1",
    jti="jti-1",
    sid="sid-1",
    iat=NOW,
    exp=NOW + timedelta(seconds=3600),
)

ME = UserMe(
    id=USER_ID,
    display_name="テスト太郎",
    profile={"bio": "よろしく"},
    birth_date=date(1990, 4, 1),
    profile_complete=True,
)


class StubUserService:
    """ルーティング試験用のUserServiceスタブ(design §4.1-4)。"""

    def __init__(self):
        self.registered: list[dict] = []
        self.register_error: Exception | None = None
        self.me: UserMe | None = ME

    async def register(self, *, claims, display_name, birth_date, profile):
        self.registered.append(
            {
                "claims": claims,
                "display_name": display_name,
                "birth_date": birth_date,
                "profile": profile,
            }
        )
        if self.register_error is not None:
            raise self.register_error
        return UserCreated(id=USER_ID, display_name=display_name)

    async def get_me(self, *, claims):
        if self.me is None:
            raise UserNotFoundError("user not found")
        return self.me


@pytest.fixture
def stub():
    return StubUserService()


@pytest.fixture
def app(stub):
    app = create_app(
        clock=FakeClock(NOW),
        settings=Settings(app_env="ci"),
        users_service=stub,
    )
    app.dependency_overrides[require_authenticated] = lambda: CLAIMS
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _assert_envelope(body: dict, code: str) -> None:
    # 05 第5節 エラー形式: {"error": {code, message, details}}
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert body["error"]["details"] is None
    assert body["error"]["message"]


def test_users_service_injected_on_state(app, stub):
    # create_app 第4引数 → app.state.users_service(テスト注入の経路)
    assert app.state.users_service is stub


async def test_register_returns_201_shape(client, stub):
    resp = await client.post(
        "/v1/users",
        json={
            "display_name": "テスト太郎",
            "birth_date": "1990-04-01",
            "profile": {"bio": "よろしく"},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"user"}
    assert set(body["user"]) == {"id", "display_name"}
    assert body["user"]["id"] == str(USER_ID)  # UUID文字列
    assert body["user"]["display_name"] == "テスト太郎"
    (call,) = stub.registered
    assert call["claims"] is CLAIMS
    assert call["display_name"] == "テスト太郎"
    assert str(call["birth_date"]) == "1990-04-01"
    assert call["profile"] == {"bio": "よろしく"}


async def test_register_profile_omitted_normalizes_empty(client, stub):
    # Review Focus #5: profile省略 → サービスへ渡るのは {}
    resp = await client.post(
        "/v1/users",
        json={"display_name": "bioなし", "birth_date": "1990-04-01"},
    )
    assert resp.status_code == 201
    (call,) = stub.registered
    assert call["profile"] == {}


async def test_register_bio_none_normalizes_empty(client, stub):
    # Review Focus #5: {"bio": null} → {"bio": null} を格納しない(design §2.7)
    resp = await client.post(
        "/v1/users",
        json={
            "display_name": "bio-null",
            "birth_date": "1990-04-01",
            "profile": {"bio": None},
        },
    )
    assert resp.status_code == 201
    (call,) = stub.registered
    assert call["profile"] == {}


async def test_register_missing_display_name_422(client):
    resp = await client.post("/v1/users", json={"birth_date": "1990-04-01"})
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


async def test_register_empty_display_name_422(client):
    # 空文字は「必須欠落」相当(min_length=1 — design §2.7)
    resp = await client.post(
        "/v1/users", json={"display_name": "", "birth_date": "1990-04-01"}
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


async def test_register_bad_birth_date_format_422(client):
    resp = await client.post(
        "/v1/users",
        json={"display_name": "形式不正", "birth_date": "1990/04/01"},
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


async def test_register_under_age_422_envelope(client, stub):
    # UNDER_AGE(ユースケース内の検証)のenvelope化 — design §2.2の順序1〜2
    stub.register_error = UnderAgeError("under 18 years old")
    resp = await client.post(
        "/v1/users",
        json={"display_name": "17歳", "birth_date": "2008-09-28"},
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "UNDER_AGE")


async def test_me_returns_200_shape(client):
    resp = await client.get("/v1/users/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "id",
        "display_name",
        "profile",
        "birth_date",
        "profile_complete",
    }
    assert body["id"] == str(USER_ID)
    assert body["display_name"] == "テスト太郎"
    assert body["profile"] == {"bio": "よろしく"}
    assert body["birth_date"] == "1990-04-01"  # ISO文字列(本人のみ — 05 §5)
    assert body["profile_complete"] is True


async def test_me_missing_row_404_envelope(client, stub):
    stub.me = None
    resp = await client.get("/v1/users/me")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), "NOT_FOUND")


async def test_malformed_json_400(client):
    # 既存ハンドラの振り分けがusersルータにも効く(確認値7)
    resp = await client.post(
        "/v1/users",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    _assert_envelope(resp.json(), "MALFORMED_REQUEST")
