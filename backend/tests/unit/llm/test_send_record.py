"""SendRecordの許可リスト(08 第2.4節)とJSON 1行出力(design §4-6)。"""

import json
import logging
from datetime import UTC, datetime

from latch.llm.records import LOGGER_NAME, SendRecord, send_log

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)

ALLOWED_FIELDS = {
    "occurred_at",
    "system",
    "destination",
    "status",
    "error_code",
    "intent_ids",
    "user_id",
}


def _record(**overrides) -> SendRecord:
    base = {
        "occurred_at": NOW,
        "system": "jev",
        "destination": "stub",
        "status": "ok",
        "intent_ids": ["i-1", "i-2"],
    }
    return SendRecord(**{**base, **overrides})


def test_send_record_fields_are_exactly_the_allowlist():
    # 08 第2.4節: 構造化ログは許可リスト方式。この型のフィールド=出力フィールド。
    assert set(SendRecord.model_fields) == ALLOWED_FIELDS


def test_send_record_has_no_text_or_location_fields():
    # 01 第21節・08 第2.4節: raw_text・位置・constraints系は不許可。
    for forbidden in (
        "text",
        "raw_text",
        "prompt",
        "payload",
        "content",
        "location",
        "geo",
        "constraints",
        "reason",
    ):
        assert forbidden not in SendRecord.model_fields


def test_send_record_system_is_enum_like():
    rec = _record(system="embedding")
    assert rec.system == "embedding"


def test_send_log_emits_single_json_line(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        send_log(_record(user_id="user-1"))
    messages = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert len(messages) == 1  # 1呼び出し=1行
    payload = json.loads(messages[0].getMessage())
    assert payload == {
        "occurred_at": payload["occurred_at"],  # 表記(Z/+00:00)に依存しない
        "system": "jev",
        "destination": "stub",
        "status": "ok",
        "error_code": None,
        "intent_ids": ["i-1", "i-2"],
        "user_id": "user-1",
    }
    assert datetime.fromisoformat(payload["occurred_at"]) == NOW
    assert (
        datetime.fromisoformat(payload["occurred_at"]).utcoffset().total_seconds() == 0
    )


def test_send_log_json_never_contains_free_text(caplog):
    # 機微(入力テキストの断片)をレコードに渡す経路がそもそも存在しないことを、
    # ログ出力の実測でピン留めする(Review Focus #1)。
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        send_log(_record(error_code="LLMProviderError"))
    assert "スタブ" not in caplog.text  # 例外メッセージ等の自由文が混入しない
    payload = json.loads(
        [r for r in caplog.records if r.name == LOGGER_NAME][0].getMessage()
    )
    assert set(payload) == ALLOWED_FIELDS  # 追加キーが現れない
