"""intents例外階層(design §2.7)。http_status/codeは05 §5エラー形式表の固定値。"""

import pytest

from latch.intents import (
    DependencyUnavailableError,
    ForbiddenError,
    GeocodingFailedError,
    IntentNotFoundError,
    IntentsError,
    IntentValidationError,
    InvalidTransitionError,
    LLMUnavailableError,
    UnderAgeError,
    UnstructurableError,
)


def test_llm_unavailable_is_503_llm_unavailable():
    exc = LLMUnavailableError("intent parser unavailable")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == 503
    assert exc.code == "LLM_UNAVAILABLE"


def test_unstructurable_is_422_validation_error():
    exc = UnstructurableError("structured intent is not extractable")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == 422
    assert exc.code == "VALIDATION_ERROR"


def test_dependency_unavailable_is_503():
    exc = DependencyUnavailableError("intent parse dependency unavailable")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == 503
    assert exc.code == "DEPENDENCY_UNAVAILABLE"


# --- M1 ws-3: CRUD例外(design §3.1・05 §5エラー形式表)---


@pytest.mark.parametrize(
    ("exc_type", "http_status", "code"),
    [
        pytest.param(IntentValidationError, 422, "VALIDATION_ERROR", id="validation"),
        pytest.param(UnderAgeError, 422, "UNDER_AGE", id="under-age"),
        pytest.param(GeocodingFailedError, 422, "GEOCODING_FAILED", id="geocoding"),
        pytest.param(IntentNotFoundError, 404, "NOT_FOUND", id="not-found"),
        pytest.param(ForbiddenError, 403, "FORBIDDEN", id="forbidden"),
        pytest.param(InvalidTransitionError, 422, "VALIDATION_ERROR", id="transition"),
    ],
)
def test_crud_error_codes(exc_type, http_status, code):
    exc = exc_type("fixed message")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == http_status
    assert exc.code == code
