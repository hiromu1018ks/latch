"""GET /v1/intents/expiry-options(design §2.6・05 §5追記分)。

completion.py の単一実装をAPIが正しく露出するかをFakeClockで決定的に検証する。
依存上書きは test_parse_routes.py と同じ require_authenticated オーバーライド
(ws-4適用後の api_rate_limited は require_authenticated を内包するため、
sub-dependency の上書きで効く。既存unit試験と同一の方法)。
"""

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.main import create_app

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-27 21:00

LABELS = ["今日 22:00", "今日 23:59", "明日 5:00"]


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sub-1",
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _client(clock: datetime | None = None) -> AsyncClient:
    app = create_app(clock=FakeClock(NOW if clock is None else clock))
    app.dependency_overrides[require_authenticated] = _claims
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_1_options_without_time_start():
    """time_startなし: 3選択肢・ラベル順・全selectable・default_index=None。"""
    async with _client() as client:
        resp = await client.get("/v1/intents/expiry-options")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [o["label"] for o in body["options"]] == LABELS
    # JST 21:00 実行相当: 今日22:00はまだ未来
    assert [o["selectable"] for o in body["options"]] == [True, True, True]
    assert body["default_index"] is None
    expires = [o["expires_at"] for o in body["options"]]
    assert expires[0] == "2026-09-27T22:00:00+09:00"
    assert expires[1] == "2026-09-27T23:59:00+09:00"
    assert expires[2] == "2026-09-28T05:00:00+09:00"


async def test_2_default_index_with_time_start():
    """time_start=JST 23:00 → target=翌02:00 → 最近は今日23:59(index=1)。"""
    async with _client() as client:
        resp = await client.get(
            "/v1/intents/expiry-options",
            params={"time_start": "2026-09-27T23:00:00+09:00"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_index"] == 1


async def test_3_passed_option_disabled_and_default_shifts():
    """JST 22:01以降: 今日22:00はselectable=false・既定は未来のみから選ぶ。"""
    late = datetime(2026, 9, 27, 13, 1, 0, tzinfo=UTC)  # JST 22:01
    async with _client(clock=late) as client:
        resp = await client.get(
            "/v1/intents/expiry-options",
            params={"time_start": "2026-09-27T21:00:00+09:00"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["options"][0]["selectable"] is False  # 今日22:00は過ぎた
    assert body["default_index"] == 1  # target=翌00:00 → 最近の未来は今日23:59
    # 選択肢が全滅しない(明日5:00は常に未来側の選択肢 — Review Focus 3)
    assert any(o["selectable"] for o in body["options"])


async def test_4_naive_time_start_is_422():
    """tz-naiveのtime_startは422 VALIDATION_ERROR(envelope)。"""
    async with _client() as client:
        resp = await client.get(
            "/v1/intents/expiry-options",
            params={"time_start": "2026-09-27T23:00:00"},
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
