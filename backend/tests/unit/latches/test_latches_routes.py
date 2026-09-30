"""latchesルーティングのunit試験(M3 ws-1)。スタブサービス注入で応答形状を検証。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.latches.errors import LatchClosedError
from latch.main import create_app

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
LATCH_ID = "d1a7b6c0-0000-4000-8000-00000000d10a"


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sub-1",
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _summary(**overrides) -> SimpleNamespace:
    base = dict(
        id=LATCH_ID,
        status="proposed",
        response_deadline=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(days=5),
        created_at=NOW,
        completed_at=None,
        proposal={"headcount": 2, "match_level": "medium"},
        is_group=False,
        my_response=None,
        remaining_responses=2,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class StubService:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def respond(self, **kwargs):
        self.calls.append(("respond", kwargs))
        return _summary(
            status="partial_accept", my_response="yes", remaining_responses=1
        )

    async def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return ([_summary()], "next-token")

    async def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return SimpleNamespace(
            **vars(_summary(status="matched")),
            participants=None,
            time_summary=None,
            area_name=None,
        )


def _app(svc: StubService):
    app = create_app(latches_service=svc)
    app.dependency_overrides[require_authenticated] = lambda: _claims()
    return app


@pytest.fixture
def stub():
    return StubService()


async def test_response_endpoint_returns_latch_envelope(stub):
    """POST /v1/latches/{id}/response は {"latch": {...}}(05 §5)。"""
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/v1/latches/{LATCH_ID}/response", json={"response": "yes"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"latch"}
    latch = body["latch"]
    assert latch["status"] == "partial_accept"
    assert latch["my_response"] == "yes"
    assert latch["remaining_responses"] == 1
    assert "responses" not in latch  # 引用#22
    assert stub.calls[0][1]["response"] == "yes"


async def test_response_invalid_value_422(stub):
    """response="maybe" は422 VALIDATION_ERROR(引用#1・試験8の本体)。"""
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/v1/latches/{LATCH_ID}/response", json={"response": "maybe"}
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_domain_error_maps_to_envelope(stub):
    """LatchesErrorは {"error":{"code",...}} へ(引用#18)。"""

    async def _raise(**kwargs):
        raise LatchClosedError("latch closed")

    stub.respond = _raise
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/v1/latches/{LATCH_ID}/response", json={"response": "yes"}
        )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "LATCH_CLOSED"


async def test_list_forwards_cursor_and_limit(stub):
    """GET /v1/latches はcursor・limit(既定20・上限100)をserviceへ流す。"""
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/latches", params={"cursor": "abc", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["next_cursor"] == "next-token"
    assert len(body["items"]) == 1
    assert stub.calls[0][1]["cursor"] == "abc"
    assert stub.calls[0][1]["limit"] == 5


async def test_limit_out_of_range_422(stub):
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/latches", params={"limit": 101})
    assert resp.status_code == 422


async def test_unauthenticated_401(stub):
    """認証なしは401(認証ハンドラ・スタブは呼ばれない)。

    ASGITransportのunit試験ではlifespanが走らないため、既存intents流儀
    (test_crud_routes.py)と同一のdependency_overridesで未認証を表現する。
    """
    from latch.auth.errors import UnauthenticatedError

    async def _raise() -> AccessTokenClaims:
        raise UnauthenticatedError("missing bearer token")

    app = create_app(latches_service=stub)
    app.dependency_overrides[require_authenticated] = _raise
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/latches")
    assert resp.status_code == 401
    assert stub.calls == []
