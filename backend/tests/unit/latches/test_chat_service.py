"""チャット+実施自己申告のunit試験(M3 ws-4 design §4.1)。

storeはスタブ(monkeypatch差し替え)でSQLに依存しない(test_latches_service.py
と同型)。時刻はFakeClock。SQL検査はtest_chat_store.py・実HTTPは
test_chat_attendance_api.py(integration)の担い。
"""

import base64
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from latch.core.clock import FakeClock
from latch.latches.errors import (
    AttendanceAlreadySubmittedError,
    AttendanceWindowClosedError,
    ChatReadonlyError,
    DependencyUnavailableError,
    LatchClosedError,
    LatchesError,
    LatchNotFoundError,
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
    LatchesService,
    decode_message_cursor,
    encode_message_cursor,
    is_attendance_window_open,
)
from latch.latches.store import CalibrationAttendanceRow

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


# -- send_message / list_messages(design §2.1手順1〜7) --


class _NoopEngine:
    """store全体をmonkeypatch差し替えするための空エンジン(conn不使用)。"""

    def begin(self):
        return self

    def connect(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _row(**overrides) -> SimpleNamespace:
    """LatchRow相当のスタブ(serviceは属性アクセスのみ)。"""
    base = dict(
        id=uuid.uuid4(),
        status="matched",
        intent_ids=[I1, uuid.uuid4()],
        completed_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _ret(value):
    """monkeypatch差し替え用の「常にvalueを返すasync関数」ファクトリ。"""

    async def _inner(*args, **kwargs):
        return value

    return _inner


def _msg(row, sender=ME, body="こんにちは", minutes_ago=0) -> object:
    from latch.latches.store import MessageRow

    return MessageRow(
        id=uuid.uuid4(),
        latch_id=row.id,
        sender_id=sender,
        body=body,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )


async def test_send_message_authorization_404_403(monkeypatch):
    """未登録JWTは404・参加者以外は403(引用#17・#18・design §2.1手順1〜3)。"""
    from latch.latches import store as store_mod
    from latch.latches.errors import ForbiddenError, LatchNotFoundError

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(None))
    with pytest.raises(LatchNotFoundError):
        await LatchesService(clock=FakeClock(NOW), engine=_NoopEngine()).send_message(
            auth_provider="google", auth_subject="s", latch_id=uuid.uuid4(), body="x"
        )
    row = _row()
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(None))
    with pytest.raises(ForbiddenError):
        await LatchesService(clock=FakeClock(NOW), engine=_NoopEngine()).send_message(
            auth_provider="google", auth_subject="s", latch_id=row.id, body="x"
        )


async def test_send_message_non_matched_statuses_409(monkeypatch):
    """matched以外の全7状態は409 CHAT_READONLY(design §2.1案A・引用#1/#5/#6)。

    Review Focus 1: 部分条件(proposedだけ等)ではcompleted後の送信が
    通ってしまう。全状態を網羅ピンする。
    """
    from latch.latches import store as store_mod

    for status in (
        "proposed",
        "partial_accept",
        "completed",
        "cancelled",
        "rejected",
        "expired",
        "candidate",
    ):
        row = _row(status=status)
        monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
        monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
        monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
        with pytest.raises(ChatReadonlyError):
            await LatchesService(
                clock=FakeClock(NOW), engine=_NoopEngine()
            ).send_message(
                auth_provider="google", auth_subject="s", latch_id=row.id, body="x"
            )


async def test_send_message_matched_inserts_and_blocks(monkeypatch):
    """matched+blocksなしならINSERT→MessageRow(design §2.1手順4〜7)。"""
    from latch.latches import store as store_mod

    row = _row(status="matched")
    out_msg = _msg(row, body="はじめまして")
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "fetch_participant_user_ids", _ret([ME, PEER]))
    monkeypatch.setattr(store_mod, "select_block_between", _ret(False))
    monkeypatch.setattr(store_mod, "insert_message", _ret(out_msg))
    got = await LatchesService(clock=FakeClock(NOW), engine=_NoopEngine()).send_message(
        auth_provider="google", auth_subject="s", latch_id=row.id, body="はじめまして"
    )
    assert got is out_msg
    # blocks引っかかりは同コード(手順5・design §2.2)
    monkeypatch.setattr(store_mod, "select_block_between", _ret(True))
    with pytest.raises(ChatReadonlyError):
        await LatchesService(clock=FakeClock(NOW), engine=_NoopEngine()).send_message(
            auth_provider="google", auth_subject="s", latch_id=row.id, body="x"
        )


async def test_list_messages_builds_next_cursor(monkeypatch):
    """limit+1件取得→溢れたらnext_cursor生成(latches一覧と同型・design §2.1)。"""
    from latch.latches import store as store_mod

    row = _row(status="completed")  # completedでも閲覧可(引用#5)
    m1 = _msg(row, body="1通目", minutes_ago=2)
    m2 = _msg(row, sender=PEER, body="2通目", minutes_ago=1)
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch", _ret(row))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "select_messages_page", _ret([m1, m2]))
    items, next_cursor = await LatchesService(
        clock=FakeClock(NOW), engine=_NoopEngine()
    ).list_messages(auth_provider="google", auth_subject="s", latch_id=row.id, limit=1)
    assert items == [m1]  # limit=1で1件だけ返す
    assert next_cursor == encode_message_cursor(m1.created_at, m1.id)
    # cursor渡しはdecodeしてbeforeへ渡される(形式不正は422)
    monkeypatch.setattr(store_mod, "select_messages_page", _ret([m2]))
    items2, next2 = await LatchesService(
        clock=FakeClock(NOW), engine=_NoopEngine()
    ).list_messages(
        auth_provider="google",
        auth_subject="s",
        latch_id=row.id,
        cursor=encode_message_cursor(m1.created_at, m1.id),
        limit=1,
    )
    assert items2 == [m2]
    assert next2 is None  # 次頁なし


# -- submit_attendance(design §2.3手順1〜7) --


async def test_submit_attendance_records_and_returns(monkeypatch):
    """attendedのboolがそのままactual_attendedへ(cancelled_afterは排反)。

    UPDATEは同一tx・now=Clock(design §2.3手順6)。
    """
    from latch.latches import store as store_mod

    row = _row(status="completed", completed_at=NOW - timedelta(hours=1))
    row2 = _row(status="completed", completed_at=NOW - timedelta(hours=2))
    calls: list[dict] = []

    async def _update(conn, *, latch_id, attended, now):
        calls.append({"latch_id": latch_id, "attended": attended, "now": now})
        return True

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch", _ret(row))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(
        store_mod,
        "select_calibration_attendance",
        _ret(CalibrationAttendanceRow(actual_attended=None)),
    )
    monkeypatch.setattr(store_mod, "update_attendance", _update)
    svc = LatchesService(clock=FakeClock(NOW), engine=_NoopEngine())
    out = await svc.submit_attendance(
        auth_provider="google", auth_subject="s", latch_id=row.id, attended=True
    )
    assert out == AttendanceResponse(latch_id=row.id, actual_attended=True)
    # attended=Falseの対: actual_attended=Falseへ(引用#10・#13)
    monkeypatch.setattr(store_mod, "select_latch", _ret(row2))
    out2 = await svc.submit_attendance(
        auth_provider="google", auth_subject="s", latch_id=row2.id, attended=False
    )
    assert out2 == AttendanceResponse(latch_id=row2.id, actual_attended=False)
    assert calls == [
        {"latch_id": row.id, "attended": True, "now": NOW},
        {"latch_id": row2.id, "attended": False, "now": NOW},
    ]


async def test_submit_attendance_classifications(monkeypatch):
    """手順3〜7の分類: 404/409×2種/503(design §2.3・引用#9/#10)。"""
    from latch.latches import store as store_mod

    svc = LatchesService(clock=FakeClock(NOW), engine=_NoopEngine())
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    done = _row(status="completed", completed_at=NOW - timedelta(hours=1))
    monkeypatch.setattr(store_mod, "select_latch", _ret(done))
    monkeypatch.setattr(
        store_mod,
        "select_calibration_attendance",
        _ret(CalibrationAttendanceRow(actual_attended=None)),
    )
    # (a) 参加者以外は404(messagesの403と扱いを分ける・引用#9)
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(None))
    with pytest.raises(LatchNotFoundError):
        await svc.submit_attendance(
            auth_provider="google", auth_subject="s", latch_id=done.id, attended=True
        )
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    # (b) matched(対象時刻前)・cancelled等は409 LATCH_CLOSED(手順4)
    for status in ("matched", "cancelled", "proposed"):
        monkeypatch.setattr(store_mod, "select_latch", _ret(_row(status=status)))
        with pytest.raises(LatchClosedError):
            await svc.submit_attendance(
                auth_provider="google",
                auth_subject="s",
                latch_id=uuid.uuid4(),
                attended=True,
            )
    # (c) 3日+1秒経過は409 ATTENDANCE_WINDOW_CLOSED(手順5)
    monkeypatch.setattr(
        store_mod,
        "select_latch",
        _ret(
            _row(
                status="completed",
                completed_at=NOW - timedelta(days=3) - timedelta(seconds=1),
            )
        ),
    )
    with pytest.raises(AttendanceWindowClosedError):
        await svc.submit_attendance(
            auth_provider="google",
            auth_subject="s",
            latch_id=uuid.uuid4(),
            attended=True,
        )
    # (d) 回答済みは409 ATTENDANCE_ALREADY_SUBMITTED(手順7事前検査)
    monkeypatch.setattr(store_mod, "select_latch", _ret(done))
    monkeypatch.setattr(
        store_mod,
        "select_calibration_attendance",
        _ret(CalibrationAttendanceRow(actual_attended=True)),
    )
    with pytest.raises(AttendanceAlreadySubmittedError):
        await svc.submit_attendance(
            auth_provider="google", auth_subject="s", latch_id=done.id, attended=True
        )
    # (e) calibration行不在は503(手順7・承認事項⑤)
    monkeypatch.setattr(store_mod, "select_calibration_attendance", _ret(None))
    with pytest.raises(DependencyUnavailableError):
        await svc.submit_attendance(
            auth_provider="google", auth_subject="s", latch_id=done.id, attended=True
        )
