"""チャット+実施自己申告のunit試験(M3 ws-4 design §4.1)。

storeはスタブ(monkeypatch差し替え)でSQLに依存しない(test_latches_service.py
と同型)。時刻はFakeClock。SQL検査はtest_chat_store.py・実HTTPは
test_chat_attendance_api.py(integration)の担い。
"""

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from latch.latches.errors import (
    AttendanceAlreadySubmittedError,
    AttendanceWindowClosedError,
    ChatReadonlyError,
    LatchesError,
    LatchValidationError,
)
from latch.latches.schemas import (
    AttendanceRequest,
    AttendanceResponse,
    MessageEnvelope,
    MessageListResponse,
    MessageOut,
    MessageRequest,
)
from latch.latches.service import (
    decode_message_cursor,
    encode_message_cursor,
    is_attendance_window_open,
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


def _b64(s: str) -> str:
    """テスト用: 不透明cursor候補の生成(base64url・パディング除去)。"""
    return base64.urlsafe_b64encode(s.encode()).rstrip(b"=").decode()


def test_message_cursor_roundtrip():
    """2キー(created_at,id)のencode/decode往復(design §2.1)。"""
    created = NOW - timedelta(minutes=5)
    mid = uuid.uuid4()
    token = encode_message_cursor(created, mid)
    assert decode_message_cursor(token) == (created, mid)
    assert "=" not in token  # base64urlのパディング除去(不透明文字列)


def test_message_cursor_invalid_422():
    """形式不正cursorは422 VALIDATION_ERROR(latches/intentsと同型)。"""
    for bad in (
        _b64("no-pipe-here"),  # 区切りなし
        _b64("not-a-datetime|" + str(uuid.uuid4())),  # 日時復元失敗
        "!!!",  # base64urlとして不正
    ):
        with pytest.raises(LatchValidationError):
            decode_message_cursor(bad)


def test_is_attendance_window_open_boundaries():
    """3日窓は閉区間: ちょうど3日まで受理・+1秒で閉じ(design §2.3手順5)。

    Review Focus 3: 「以内」を開区間に読むとちょうど3日目の正当な
    申告を409で捨てる。境界を明示ピンする。
    """
    assert is_attendance_window_open(NOW - timedelta(days=2), NOW) is True
    assert (
        is_attendance_window_open(NOW - timedelta(days=3), NOW) is True
    )  # ちょうど3日
    assert (
        is_attendance_window_open(NOW - timedelta(days=3) - timedelta(seconds=1), NOW)
        is False
    )
    assert is_attendance_window_open(None, NOW) is False  # completed_at未設定は閉
