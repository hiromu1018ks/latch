"""parseルーティング(design §4.1-5)。スタブサービス注入+dependency_overrides。

create_app への注入と require_authenticated の上書きで認証・応答形状・
エラー切替を検証する。ASGITransport(lifespan不実行)のためDBなしで動く。
"""

import copy
import logging
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.errors import UnauthenticatedError
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.intents.errors import (
    DependencyUnavailableError,
    LLMUnavailableError,
    UnstructurableError,
)
from latch.intents.schema import WARNING_MESSAGE_NG_DOWNGRADED, ParserOutput
from latch.intents.service import ParseResult, ParseWarning
from latch.llm.stub import DEFAULT_PARSER_RESPONSE
from latch.main import create_app

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sub-1",
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _parse_result() -> ParseResult:
    return ParseResult(
        structured_intent=ParserOutput.model_validate(
            copy.deepcopy(DEFAULT_PARSER_RESPONSE)
        ),
        warnings=[],
    )


class StubService:
    """IntentParseService のテストスタブ。受け取った引数を記録する。"""

    def __init__(
        self, result: ParseResult | None = None, error: Exception | None = None
    ):
        self.result = result if result is not None else _parse_result()
        self.error = error
        self.calls: list[dict] = []

    async def parse(
        self, *, text: str, auth_provider: str, auth_subject: str
    ) -> ParseResult:
        self.calls.append(
            {"text": text, "auth_provider": auth_provider, "auth_subject": auth_subject}
        )
        if self.error is not None:
            raise self.error
        return self.result


def _client(service: StubService, auth_override=None):
    app = create_app(clock=FakeClock(NOW), intent_parse_service=service)
    app.dependency_overrides[require_authenticated] = (
        auth_override if auth_override is not None else _claims
    )
    return app, AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_200_response_shape_without_completion():
    """200・{structured_intent, warnings}・補完は適用しない(確定値10)。"""
    app, client = _client(StubService())
    async with client:
        resp = await client.post(
            "/v1/intents/parse", json={"text": "今夜20時から軽く飲みたい"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"structured_intent", "warnings"}
        si = body["structured_intent"]
        assert si["category"]["primary"] == "meal"
        assert si["alcohol_involved"] is False
        assert si["time"]["end"] is None  # 補完(time.start+3h)を適用しない
        assert si["time"]["flexibility_minutes"] is None
        assert si["location"]["radius_m"] is None  # 補完(1000)を適用しない
        assert si["location"]["flexibility"] is None
        assert si["negative_constraints"] == []
        assert body["warnings"] == []


async def test_claims_and_text_forwarded_to_service():
    service = StubService()
    app, client = _client(service)
    async with client:
        await client.post("/v1/intents/parse", json={"text": "明日18時に駅前で"})
    assert service.calls == [
        {"text": "明日18時に駅前で", "auth_provider": "google", "auth_subject": "sub-1"}
    ]


async def test_warnings_shape_matches_05_section5_example():
    result = _parse_result()
    result = ParseResult(
        structured_intent=result.structured_intent,
        warnings=[
            ParseWarning(
                code="NG_CONDITION_DOWNGRADED",
                condition="会社関係の人は避けたい",
                message=WARNING_MESSAGE_NG_DOWNGRADED,
            )
        ],
    )
    app, client = _client(StubService(result=result))
    async with client:
        resp = await client.post("/v1/intents/parse", json={"text": "t"})
        assert resp.status_code == 200
        (warning,) = resp.json()["warnings"]
        assert warning == {
            "code": "NG_CONDITION_DOWNGRADED",
            "condition": "会社関係の人は避けたい",
            "message": "この条件は確実には除外できません。参考条件として扱います",
        }


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="text-missing"),
        pytest.param({"text": ""}, id="text-empty"),
        pytest.param({"text": "あ" * 301}, id="text-301-chars"),
        pytest.param({"text": 123}, id="text-not-string"),
    ],
)
async def test_invalid_text_returns_422_validation_error_envelope(payload):
    app, client = _client(StubService())
    async with client:
        resp = await client.post("/v1/intents/parse", json=payload)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_malformed_json_returns_400_malformed_request():
    """JSON形式不正=400(tests/unit/auth/test_routes.py と同じ送り方)。"""
    app, client = _client(StubService())
    async with client:
        resp = await client.post(
            "/v1/intents/parse",
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "MALFORMED_REQUEST"


async def test_llm_unavailable_maps_to_503():
    app, client = _client(
        StubService(error=LLMUnavailableError("intent parser unavailable"))
    )
    async with client:
        resp = await client.post("/v1/intents/parse", json={"text": "t"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "LLM_UNAVAILABLE"


async def test_unstructurable_maps_to_422():
    app, client = _client(
        StubService(error=UnstructurableError("structured intent is not extractable"))
    )
    async with client:
        resp = await client.post("/v1/intents/parse", json={"text": "t"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_dependency_unavailable_maps_to_503():
    app, client = _client(
        StubService(
            error=DependencyUnavailableError("intent parse dependency unavailable")
        )
    )
    async with client:
        resp = await client.post("/v1/intents/parse", json={"text": "t"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


async def test_unauthenticated_returns_401_envelope():
    async def _raise_unauthenticated() -> AccessTokenClaims:
        raise UnauthenticatedError("missing bearer token")

    app, client = _client(StubService(), auth_override=_raise_unauthenticated)
    async with client:
        resp = await client.post("/v1/intents/parse", json={"text": "t"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_text_never_appears_in_logs(caplog):
    """Review Focus #4: ログにユーザー本文を出さない(08 §2.4)。"""
    secret = "会社の同僚と内緒の飲み会"
    app, client = _client(StubService())
    async with client:
        with caplog.at_level(logging.INFO):
            await client.post("/v1/intents/parse", json={"text": secret})
    assert secret not in caplog.text
