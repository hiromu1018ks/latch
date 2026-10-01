"""StubPushSenderとbuild_push_senderのunit試験(M3 ws-3 design §2.2〜2.3・§4.1-2)。"""

import json
import logging
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.notifications.records import LOGGER_NAME
from latch.notifications.sender import (
    PUSH_TIMEOUT_S,
    StubPushSender,
    build_push_sender,
)
from latch.notifications.types import (
    NOTIFICATION_ATTENDANCE_REQUEST,
    NOTIFICATION_PROPOSAL,
)
from latch.settings import Settings

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
USER = "00000000-0000-4000-8000-0000000000c1"
LATCH = "00000000-0000-4000-8000-0000000000a1"


def _records(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME]


async def test_stub_send_ok_record_full_fields(caplog):
    """ok記録の全フィールド(design §2.3)。本文=テンプレート定数。"""
    sender = StubPushSender(clock=FakeClock(NOW))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await sender.send(
            user_id=USER, notification_type=NOTIFICATION_PROPOSAL, latch_id=LATCH
        )
    (rec,) = _records(caplog)
    assert rec == {
        # pydantic model_dump_jsonのRFC 3339形式(+00:00→Z・§9-3固定値どおり)
        "occurred_at": "2026-10-01T12:00:00Z",
        "user_id": USER,
        "notification_type": "proposal",
        "latch_id": LATCH,
        "title": "LATCH",
        "body": "LATCH候補があります。\n詳細はアプリでご確認ください。",
        "status": "ok",
        "error_code": None,
    }


async def test_stub_send_short_delay_within_timeout_ok(caplog):
    """delay<timeout → ok(レイテンシ注入・LLMスタブと同型)。"""
    sender = StubPushSender(clock=FakeClock(NOW), delay_ms=10, timeout_s=5.0)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await sender.send(
            user_id=USER,
            notification_type=NOTIFICATION_ATTENDANCE_REQUEST,
            latch_id=LATCH,
        )
    (rec,) = _records(caplog)
    assert rec["status"] == "ok"
    assert (
        rec["body"] == "LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"
    )


async def test_stub_send_delay_beyond_timeout_records_timeout(caplog):
    """delay>timeout → status=timeout・error_code=PushTimeoutError(asyncio.timeoutが
    即打ち切りするため試験は速い・design §2.2)。"""
    sender = StubPushSender(clock=FakeClock(NOW), delay_ms=5000, timeout_s=0.01)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await sender.send(
            user_id=USER, notification_type=NOTIFICATION_PROPOSAL, latch_id=LATCH
        )
    (rec,) = _records(caplog)
    assert rec["status"] == "timeout"
    assert rec["error_code"] == "PushTimeoutError"


async def test_stub_send_failure_records_error_and_swallows(caplog):
    """送出例外(fail差し替え)→ error記録+握る(呼び出し元に例外が出ない・§2.7)。"""
    sender = StubPushSender(clock=FakeClock(NOW), fail=True)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        # 例外が送出されたらこの試験自体が落ちる=握りの証明
        await sender.send(
            user_id=USER, notification_type=NOTIFICATION_PROPOSAL, latch_id=LATCH
        )
    (rec,) = _records(caplog)
    assert rec["status"] == "error"
    assert rec["error_code"] == "RuntimeError"


async def test_stub_send_unknown_type_raises_value_error():
    """未知typeはValueError=fail-fast(握らない・design §2.7)。記録型は出ない。"""
    sender = StubPushSender(clock=FakeClock(NOW))
    with pytest.raises(ValueError, match="notification_type"):
        await sender.send(user_id=USER, notification_type="bogus", latch_id=LATCH)


def test_push_timeout_default_is_three_seconds():
    """PUSH_TIMEOUT_S=3.0(コード定数・層別予算Layer5+通知≤2秒はp95目標で
    timeoutは上限・design §2.2)。"""
    assert PUSH_TIMEOUT_S == 3.0


def test_build_push_sender_stub_uses_settings():
    """push_mode=stub → StubPushSender(delayはsettings反映・§9-5)。"""
    sender = build_push_sender(
        FakeClock(NOW), Settings(push_mode="stub", push_stub_delay_ms=7)
    )
    assert isinstance(sender, StubPushSender)
    assert sender.name == "stub"
    assert sender._delay_ms == 7


@pytest.mark.parametrize("mode", ["real", "production"])
def test_build_push_sender_rejects_unsupported_mode(mode):
    """未実装値はValueError(静かにスタブへ落ちない — llm_modeと同一規律)。"""
    with pytest.raises(ValueError, match="push_mode"):
        build_push_sender(FakeClock(NOW), Settings(push_mode=mode))
