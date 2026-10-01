"""safetyルーティングのunit試験(M3 ws-5・design §4.1)。

スタブサービス注入で応答形状を検証(test_latches_routes.pyと同型・実HTTPは
test_safety_api.py)。reasonの4値検証(limit外422)・cursor/limit検証は
FastAPI+pydanticが担うため、ここで契約ごとピンする。
"""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.main import create_app
from latch.safety.errors import SafetyNotFoundError
from latch.safety.store import BlockRow

NOW = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
TARGET = str(uuid.uuid4())
REPORT_ID = str(uuid.uuid4())


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sub-1",
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _block_row() -> BlockRow:
    return BlockRow(
        id=uuid.uuid4(),
        blocked_id=uuid.UUID(TARGET),
        display_name="相手",
        created_at=NOW - timedelta(minutes=5),
    )


class StubService:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def block_user(self, **kwargs):
        self.calls.append(("block_user", kwargs))
        return uuid.UUID(TARGET)

    async def unblock_user(self, **kwargs):
        self.calls.append(("unblock_user", kwargs))
        return None

    async def list_blocks(self, **kwargs):
        self.calls.append(("list_blocks", kwargs))
        return ([_block_row()], None)

    async def report_user(self, **kwargs):
        self.calls.append(("report_user", kwargs))
        return uuid.UUID(REPORT_ID)


def _app(svc: StubService):
    app = create_app(safety_service=svc)
    app.dependency_overrides[require_authenticated] = lambda: _claims()
    return app


async def test_block_returns_201():
    """POST /v1/users/{id}/block は201 {"blocked_id"}(design §2.4)。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(f"/v1/users/{TARGET}/block")
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"blocked_id": TARGET}
    assert stub.calls[0][1]["target_user_id"] == uuid.UUID(TARGET)


async def test_unblock_returns_204():
    """DELETE /v1/users/{id}/block は204(authのlogoutと同型)。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/v1/users/{TARGET}/block")
    assert resp.status_code == 204
    assert resp.content == b""


async def test_blocks_list_shape():
    """GET /v1/users/me/blocks の応答形状(design §2.4)。

    items要素はblocked_id/display_name/created_atの3要素のみ。
    """
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/users/me/blocks")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    item = body["items"][0]
    assert set(item) == {"blocked_id", "display_name", "created_at"}
    assert item["blocked_id"] == TARGET
    assert item["display_name"] == "相手"
    assert stub.calls[0][1]["cursor"] is None
    assert stub.calls[0][1]["limit"] == 20  # 既定(引用#8)


async def test_report_returns_201():
    """POST /v1/reports は201 {"report_id"}(design §2.5)。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/reports",
            json={
                "reportee_id": TARGET,
                "latch_id": str(uuid.uuid4()),
                "reason": "inappropriate_content",
            },
        )
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"report_id": REPORT_ID}


async def test_report_invalid_reason_422():
    """reason値域外は422 VALIDATION_ERROR(4値コード・design §2.5)。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/reports",
            json={"reportee_id": TARGET, "reason": "unknown_reason"},
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_blocks_limit_over_422():
    """limit 101は422(改頁共通規定・引用#8)。limit=1は受理。"""
    stub = StubService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        over = await client.get("/v1/users/me/blocks", params={"limit": 101})
        ok = await client.get("/v1/users/me/blocks", params={"limit": 1})
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "VALIDATION_ERROR"
    assert ok.status_code == 200


async def test_domain_error_maps_to_envelope():
    """SafetyErrorは共通error envelopeへ(引用#15・NOT_FOUND)。"""

    class _RaiseService(StubService):
        async def block_user(self, **kwargs):
            raise SafetyNotFoundError("user not found")

    async with AsyncClient(
        transport=ASGITransport(app=_app(_RaiseService())), base_url="http://test"
    ) as client:
        resp = await client.post(f"/v1/users/{TARGET}/block")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert set(body["error"]) == {"code", "message", "details"}
