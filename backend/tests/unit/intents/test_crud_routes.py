"""intents CRUDルーティング(design §4.1-5)。スタブサービス注入+
dependency_overrides。7エンドポイントの応答形状・envelope・認証を検証。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.errors import UnauthenticatedError
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.intents.errors import (
    ForbiddenError,
    GeocodingFailedError,
    IntentNotFoundError,
    UnderAgeError,
)
from latch.intents.service import PageResult
from latch.intents.store import IntentRow
from latch.main import create_app

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
INTENT_ID = uuid.uuid4()


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sub-1",
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _row(**overrides) -> IntentRow:
    base = dict(
        id=INTENT_ID,
        user_id=uuid.uuid4(),
        category_primary="drinking",
        alcohol_involved=True,
        raw_text="今夜天文館で飲みたい",
        structured_data={
            "category_secondary": "焼肉",
            "soft_constraints": [
                {"text": "軽く飲みたい", "downgraded_from_ng": False},
                {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
            ],
            "negative_constraints": [],
            "time_flexibility_minutes": None,
            "location_flexibility": None,
            "location_name": "天文館",
        },
        geo_radius_m=2000,
        budget_max=5000,
        participants_min=2,
        participants_max=4,
        visibility="hidden_until_match",
        notification_level="proposals_only",
        status="active",
        version=1,
        time_start=datetime(2026, 9, 27, 21, 0, 0, tzinfo=UTC),
        time_end=datetime(2026, 9, 28, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2026, 9, 27, 23, 30, 0, tzinfo=UTC),
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return IntentRow(**base)


class StubService:
    """IntentServiceのテストスタブ(引数を記録・結果を仕込む)。"""

    def __init__(self, row=None, page=None, error=None):
        self.row = row if row is not None else _row()
        self.page = (
            page if page is not None else PageResult(rows=[self.row], next_cursor=None)
        )
        self.error = error
        self.calls = []

    async def _handle(self, name, kwargs):
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error
        return self.row if name != "list" else self.page

    async def create(self, **kw):
        return await self._handle("create", kw)

    async def list(self, **kw):
        return await self._handle("list", kw)

    async def get(self, **kw):
        return await self._handle("get", kw)

    async def update(self, **kw):
        return await self._handle("update", kw)

    async def pause(self, **kw):
        return await self._handle("pause", kw)

    async def resume(self, **kw):
        return await self._handle("resume", kw)

    async def delete(self, **kw):
        return await self._handle("delete", kw)


def _client(service=None, auth_override=None):
    service = service or StubService()
    app = create_app(clock=FakeClock(NOW), intent_service=service)
    app.dependency_overrides[require_authenticated] = (
        auth_override if auth_override is not None else _claims
    )
    return app, AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _full_body():
    return {
        "raw_text": "今夜20時から天文館で軽く飲みたい",
        "status": "active",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "焼肉"},
            "alcohol_involved": True,
            "time": {"start": "2026-09-27T21:00:00+09:00"},
            "location": {"name": "天文館", "radius_m": 2000},
            "budget": {"max": 5000, "currency": "JPY"},
            "participants": {"min": 2, "max": 4},
            "visibility": "hidden_until_match",
            "notification_level": "proposals_only",
            "expires_at": "2026-09-27T23:30:00+09:00",
            "soft_constraints": ["軽く飲みたい"],
            "ng_unverifiable": ["会社関係の人は避けたい"],
            "negative_constraints": [],
        },
    }


async def test_post_returns_201_with_intent_envelope_shape():
    service = StubService()
    app, client = _client(service)
    async with client:
        resp = await client.post("/v1/intents", json=_full_body())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"intent"}
    intent = body["intent"]
    assert set(intent) == {
        "id",
        "status",
        "version",
        "raw_text",
        "structured_data",
        "structured_intent",
        "expires_at",
        "created_at",
        "updated_at",
    }
    assert intent["id"] == str(INTENT_ID)
    si = intent["structured_intent"]
    assert si["location"]["name"] == "天文館"
    assert si["soft_constraints"] == ["軽く飲みたい"]
    assert si["ng_unverifiable"] == ["会社関係の人は避けたい"]
    assert intent["structured_data"]["location_name"] == "天文館"
    name, kw = service.calls[0]
    assert name == "create"
    assert kw["raw_text"] == "今夜20時から天文館で軽く飲みたい"
    assert kw["status"] == "active"
    assert kw["auth_provider"] == "google"


async def test_get_list_returns_items_and_next_cursor():
    service = StubService(page=PageResult(rows=[_row()], next_cursor="abc"))
    app, client = _client(service)
    async with client:
        resp = await client.get("/v1/intents", params={"status": "draft", "limit": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"items", "next_cursor"}
    assert len(body["items"]) == 1
    assert body["next_cursor"] == "abc"
    name, kw = service.calls[0]
    assert name == "list"
    assert kw["status"] == "draft"
    assert kw["limit"] == 2
    assert kw["cursor"] is None


async def test_get_by_id_patch_pause_resume_return_intent_envelope():
    app, client = _client(StubService())
    async with client:
        got = await client.get(f"/v1/intents/{INTENT_ID}")
        assert got.status_code == 200
        patched = await client.patch(f"/v1/intents/{INTENT_ID}", json=_full_body())
        assert patched.status_code == 200
        paused = await client.post(f"/v1/intents/{INTENT_ID}/pause")
        assert paused.status_code == 200
        resumed = await client.post(f"/v1/intents/{INTENT_ID}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["intent"]["status"] == "active"


async def test_delete_returns_204_empty_body():
    app, client = _client(StubService())
    async with client:
        resp = await client.delete(f"/v1/intents/{INTENT_ID}")
    assert resp.status_code == 204
    assert resp.content == b""


async def test_cursor_query_param_forwarded():
    service = StubService()
    app, client = _client(service)
    async with client:
        await client.get("/v1/intents", params={"cursor": "xyz"})
    assert service.calls[0][1]["cursor"] == "xyz"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        pytest.param(IntentNotFoundError("x"), 404, "NOT_FOUND", id="404"),
        pytest.param(ForbiddenError("x"), 403, "FORBIDDEN", id="403"),
        pytest.param(UnderAgeError("x"), 422, "UNDER_AGE", id="under-age"),
        pytest.param(
            GeocodingFailedError("x"), 422, "GEOCODING_FAILED", id="geocoding"
        ),
    ],
)
async def test_domain_errors_map_to_envelope(error, status, code):
    app, client = _client(StubService(error=error))
    async with client:
        resp = await client.post("/v1/intents", json=_full_body())
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code


async def test_unauthenticated_returns_401_envelope():
    async def _raise() -> AccessTokenClaims:
        raise UnauthenticatedError("missing bearer token")

    app, client = _client(StubService(), auth_override=_raise)
    async with client:
        for method, path in [
            ("post", "/v1/intents"),
            ("get", "/v1/intents"),
            ("get", f"/v1/intents/{INTENT_ID}"),
            ("patch", f"/v1/intents/{INTENT_ID}"),
            ("delete", f"/v1/intents/{INTENT_ID}"),
            ("post", f"/v1/intents/{INTENT_ID}/pause"),
            ("post", f"/v1/intents/{INTENT_ID}/resume"),
        ]:
            kwargs = {"json": _full_body()} if method in ("post", "patch") else {}
            resp = await getattr(client, method)(path, **kwargs)
            assert resp.status_code == 401, (method, path)
            assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("limit", [0, 101])
async def test_list_limit_out_of_range_returns_422(limit):
    """Review Focus #5: limit=0/101は422(05 §5ページネーション規定)。"""
    app, client = _client(StubService())
    async with client:
        resp = await client.get("/v1/intents", params={"limit": limit})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_list_invalid_status_filter_returns_422():
    """Review Focus #5: status=drafty(綴り不正)は422 Literal検証。"""
    app, client = _client(StubService())
    async with client:
        resp = await client.get("/v1/intents", params={"status": "drafty"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_post_raw_text_301_chars_returns_422():
    body = _full_body()
    body["raw_text"] = "あ" * 301
    app, client = _client(StubService())
    async with client:
        resp = await client.post("/v1/intents", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_raw_text_never_appears_in_logs(caplog):
    """08 §2.4: ログに本文を出さない。"""
    secret = "内緒の飲み会の件"
    body = _full_body()
    body["raw_text"] = secret
    app, client = _client(StubService())
    async with client:
        with caplog.at_level(logging.INFO):
            resp = await client.post("/v1/intents", json=body)
    assert resp.status_code == 201
    assert secret not in caplog.text
