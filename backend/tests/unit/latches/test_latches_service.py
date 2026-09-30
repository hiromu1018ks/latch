"""latchesドメインservice・例外・スキーマのunit試験(M3 ws-1 design §4.1)。

storeはスタブ(行値を返すだけ)でSQLに依存しない。時刻はFakeClock。
"""

from datetime import UTC, datetime

import pytest

from latch.latches.errors import (
    AlreadyAnsweredError,
    DependencyUnavailableError,
    ForbiddenError,
    LatchClosedError,
    LatchesError,
    LatchExpiredError,
    LatchNotFoundError,
    LatchValidationError,
)
from latch.latches.schemas import (
    LatchDetailOut,
    LatchSummaryOut,
    ParticipantOut,
    ResponseRequest,
)

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def test_error_codes_and_statuses():
    """例外はhttp_status+code固定(design §2.9の対応表・05 §5エラー形式)。"""
    cases = [
        (LatchNotFoundError, 404, "NOT_FOUND"),
        (ForbiddenError, 403, "FORBIDDEN"),
        (LatchValidationError, 422, "VALIDATION_ERROR"),
        (LatchExpiredError, 409, "LATCH_EXPIRED"),
        (AlreadyAnsweredError, 409, "ALREADY_ANSWERED"),
        (LatchClosedError, 409, "LATCH_CLOSED"),
        (DependencyUnavailableError, 503, "DEPENDENCY_UNAVAILABLE"),
    ]
    for exc_cls, status, code in cases:
        assert exc_cls.http_status == status, exc_cls
        assert exc_cls.code == code, exc_cls
        assert issubclass(exc_cls, LatchesError)


def test_response_request_accepts_three_values_only():
    """responseはyes/no/deferの3値のみ(引用#2)。値域外はValidationError。"""
    assert ResponseRequest(response="yes").response == "yes"
    assert ResponseRequest(response="no").response == "no"
    assert ResponseRequest(response="defer").response == "defer"
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ResponseRequest(response="maybe")


def test_summary_shape_has_no_responses_field():
    """LatchSummaryOutはresponses配列を持たない(引用#22・Review Focus 5)。

    応答へ他者の回答種別が漏れない構造ピン。
    """
    fields = set(LatchSummaryOut.model_fields)
    assert "responses" not in fields
    assert fields == {
        "id",
        "status",
        "response_deadline",
        "expires_at",
        "created_at",
        "completed_at",
        "proposal",
        "is_group",
        "my_response",
        "remaining_responses",
    }


def test_detail_extends_summary_with_release_fields():
    """詳細は一覧要素+解放情報3字段(matched/completedのみ値が入る・design §2.8)。"""
    assert set(LatchDetailOut.model_fields) - set(LatchSummaryOut.model_fields) == {
        "participants",
        "time_summary",
        "area_name",
    }
    assert "user_id" in ParticipantOut.model_fields
    assert "display_name" in ParticipantOut.model_fields
