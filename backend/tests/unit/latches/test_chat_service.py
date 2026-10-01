"""チャット+実施自己申告のunit試験(M3 ws-4 design §4.1)。

storeはスタブ(monkeypatch差し替え)でSQLに依存しない(test_latches_service.py
と同型)。時刻はFakeClock。SQL検査はtest_chat_store.py・実HTTPは
test_chat_attendance_api.py(integration)の担い。
"""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from latch.latches.errors import (
    AttendanceAlreadySubmittedError,
    AttendanceWindowClosedError,
    ChatReadonlyError,
    LatchesError,
)
from latch.latches.schemas import (
    AttendanceRequest,
    AttendanceResponse,
    MessageEnvelope,
    MessageListResponse,
    MessageOut,
    MessageRequest,
)

NOW = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
ME = uuid.uuid4()
PEER = uuid.uuid4()
I1 = uuid.uuid4()


def test_ws4_error_codes_and_statuses():
    """新規3例外はhttp_status+code固定(design §2.1・§2.3・05 §5エラー形式)。"""
    cases = [
        (ChatReadonlyError, 409, "CHAT_READONLY"),
        (AttendanceAlreadySubmittedError, 409, "ATTENDANCE_ALREADY_SUBMITTED"),
        (AttendanceWindowClosedError, 409, "ATTENDANCE_WINDOW_CLOSED"),
    ]
    for exc_cls, status, code in cases:
        assert exc_cls.http_status == status, exc_cls
        assert exc_cls.code == code, exc_cls
        assert issubclass(exc_cls, LatchesError)


def test_message_request_body_rules():
    """bodyはtrim後1〜1000字・空白のみ不可(design §2.1・承認事項③)。

    保存は元文字列のまま(trimしない)。trim後長さで判定する。
    """
    assert MessageRequest(body="こんにちは").body == "こんにちは"
    # 前後の空白は保存される(検査のみtrim)
    assert MessageRequest(body=" こんにちは ").body == " こんにちは "
    assert MessageRequest(body="あ" * 1000).body == "あ" * 1000  # ちょうど1000:受理
    assert MessageRequest(body="1行目\n2行目").body == "1行目\n2行目"  # 改行込み:受理
    for invalid in ("", " ", "\n\t 　", "あ" * 1001):
        with pytest.raises(ValidationError):
            MessageRequest(body=invalid)


def test_message_and_attendance_response_shapes():
    """MessageOutは5要素のみ(表示名を含まない・design §2.1)。

    フロントはLATCH詳細のparticipantsで名前を解決するため、
    messages応答に表示名を載せない構造ピン(Review Focus参照)。
    """
    assert set(MessageOut.model_fields) == {
        "id",
        "latch_id",
        "sender_id",
        "body",
        "created_at",
    }
    assert set(MessageListResponse.model_fields) == {"items", "next_cursor"}
    assert set(MessageEnvelope.model_fields) == {"message"}
    assert set(AttendanceResponse.model_fields) == {
        "latch_id",
        "actual_attended",
    }  # cancelled_afterは応答に出さない(design §2.3)
    assert AttendanceRequest(attended=True).attended is True
    assert AttendanceRequest(attended=False).attended is False
    with pytest.raises(ValidationError):
        AttendanceRequest(attended="maybe")  # boolへパース不能な文字列は422
