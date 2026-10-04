"""POST /v1/sessions契約(05 §19・design §2.4)。

users試験と同一作法: require_authenticatedはdependency_overridesで差し
替え(authスタックを構築しない)。storeはapp.state.session_storeへスタブ
を注入する(DB不要・unit)。INSERTの実DB検証はintegration(test_obs_api_ci)
とstaging受け入れが所有する。
"""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.main import create_app

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
CLAIMS = AccessTokenClaims(
    auth_provider="google",
    auth_subject="obs-sess-1",
    jti="jti-1",
    sid="sid-1",
    iat=NOW,
    exp=NOW + timedelta(seconds=3600),
)


_UNSET = object()


class StubSessionStore:
    """ルーティング試験用スタブ(recordの呼び出しを記録する)。

    user_id=Noneは「users未登録」の明示指定(404試験)。or で既定uuidを
    作るとNone指定が潰れるため、未指定(_UNSET)との差で分ける。
    """

    def __init__(self, user_id: uuid.UUID | None = _UNSET):
        self.user_id = uuid.uuid4() if user_id is _UNSET else user_id
        self.calls: list[tuple[str, str, datetime]] = []

    async def record(self, provider: str, subject: str, now: datetime):
        self.calls.append((provider, subject, now))
        return self.user_id


def _make_app_with(store):
    clock = FakeClock(NOW)
    app = create_app(clock=clock)
    app.state.session_store = store
    app.dependency_overrides[require_authenticated] = lambda: CLAIMS
    return app


async def _client(app) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_post_sessions_returns_204_and_records_user_and_time():
    store = StubSessionStore()
    app = _make_app_with(store)
    async for client in _client(app):
        resp = await client.post("/v1/sessions")
    assert resp.status_code == 204
    assert len(store.calls) == 1
    provider, subject, now = store.calls[0]
    assert provider == "google"
    assert subject == "obs-sess-1"
    assert now == NOW  # Clock経由(fake_clock固定値)


async def test_post_sessions_with_body_is_422():
    """request bodyは空が契約(05 §19)。非空は422 VALIDATION_ERROR。"""
    store = StubSessionStore()
    app = _make_app_with(store)
    async for client in _client(app):
        resp = await client.post("/v1/sessions", json={"note": "x"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert store.calls == []  # body非空ではrecordしない


async def test_post_sessions_unregistered_user_is_404():
    store = StubSessionStore(user_id=None)
    app = _make_app_with(store)
    async for client in _client(app):
        resp = await client.post("/v1/sessions")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "USER_NOT_FOUND"


async def test_post_sessions_without_auth_is_401():
    """認証なしは401(require_authenticated依存・例外をraiseするoverride)。"""
    from latch.auth.errors import UnauthenticatedError

    clock = FakeClock(NOW)
    app = create_app(clock=clock)
    app.state.session_store = StubSessionStore()

    def _deny():
        raise UnauthenticatedError()  # http_status=401・code="UNAUTHENTICATED"

    app.dependency_overrides[require_authenticated] = _deny
    async for client in _client(app):
        resp = await client.post("/v1/sessions")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"
