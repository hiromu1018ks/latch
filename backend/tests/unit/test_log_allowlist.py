"""構造化ログ許可リストの機械検査(M4 ws-5 計画Task 1・08 §2.4第1・第3規定)。

登録表(ロガー名→レコード型)と不許可フィールド列挙の突合、型登録の強制、
Serializer段階での不許可キー落下を検証する。test-ci常設で毎回走る
(G4「ログ許可リスト有効」の継続的保証)。既存2ログは無変更で登録済み
(設計§2.6 — 本試験は変更を強制しない・登録の存在のみ強制する)。
"""

from datetime import UTC, datetime

from latch.core.logallow import (
    ALLOWED_STRUCTURED_LOGS,
    FORBIDDEN_FIELD_NAMES,
    serialize_allowed,
)
from latch.llm.records import SendRecord
from latch.notifications.records import PushSendRecord
from latch.notifications.templates import (
    PUSH_BODY_LATCH,
    PUSH_BODY_MATCHED,
    PUSH_BODY_NOTICE,
    PUSH_TITLE,
    build_push,
)
from latch.notifications.types import (
    NOTIFICATION_ATTENDANCE_REQUEST,
    NOTIFICATION_MATCHED,
    NOTIFICATION_NEARBY,
    NOTIFICATION_PROPOSAL,
)

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)


def test_registered_log_types_have_no_forbidden_fields():
    """機械検査1: 登録表の各型の全フィールド名が不許可列挙と衝突しない。"""
    assert set(ALLOWED_STRUCTURED_LOGS) == {"latch.llm.send", "latch.push.send"}
    for name, model in ALLOWED_STRUCTURED_LOGS.items():
        for field in model.model_fields:
            assert field not in FORBIDDEN_FIELD_NAMES, (name, field)


def test_record_types_are_registered():
    """機械検査2: records.py側の型が登録表に載っている(import突合)。

    新規に構造化ログの型を作ったら登録必須という規律の強制点。
    """
    assert ALLOWED_STRUCTURED_LOGS["latch.llm.send"] is SendRecord
    assert ALLOWED_STRUCTURED_LOGS["latch.push.send"] is PushSendRecord


def test_serialize_allowed_drops_forbidden_keys():
    """機械検査3: 不許可キーを混ぜたdictが出力JSONに現れない
    (=「Serializerの段階で落とす」08 §2.4の挙動試験)。"""
    raw = {
        "occurred_at": NOW,
        "system": "intent_parser",
        "destination": "Anthropic",
        "status": "ok",
        "raw_text": "今夜 天文館で飲みたい",
        "geo": {"lat": 31.5965, "lon": 130.5581},
        "constraints": ["19時以降"],
        "structured_data": {"x": 1},
    }
    record = serialize_allowed("latch.llm.send", raw)
    assert isinstance(record, SendRecord)
    dumped = record.model_dump_json()
    assert "raw_text" not in dumped
    assert "今夜 天文館で飲みたい" not in dumped
    assert "geo" not in dumped
    assert "constraints" not in dumped
    assert record.destination == "Anthropic"  # 許可フィールドは保持される


def test_serialize_allowed_rejects_unregistered_logger():
    """未登録ロガーはKeyError(新規ログは登録必須という規律の入口)。"""
    import pytest

    with pytest.raises(KeyError):
        serialize_allowed("latch.unknown.send", {})


def test_push_title_body_are_template_constants_only():
    """機械検査4: PushSendRecordのtitle/bodyがtemplates固定定数のみ
    (raw_text不許可の実質保証。定数の全文ピンはM3 ws-3の
    test_templates.pyが所有するため本試験では写像の全体性のみ再確認)。"""
    mapped = {
        build_push(t)
        for t in (
            NOTIFICATION_PROPOSAL,
            NOTIFICATION_NEARBY,
            NOTIFICATION_MATCHED,
            NOTIFICATION_ATTENDANCE_REQUEST,
        )
    }
    for title, body in mapped:
        assert title == PUSH_TITLE
        assert body in {
            PUSH_BODY_LATCH,
            PUSH_BODY_NOTICE,
            PUSH_BODY_MATCHED,
        }
