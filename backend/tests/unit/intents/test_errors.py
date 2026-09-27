"""intents例外階層(design §2.7)。http_status/codeは05 §5エラー形式表の固定値。"""

from latch.intents import (
    DependencyUnavailableError,
    IntentsError,
    LLMUnavailableError,
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
