"""ExpirySweeperのunit試験(M3 ws-2 design §2.2〜2.4・§2.7・§4.1)。

SQLピン(対象条件・SKIP LOCKED・LIMIT・部分索引条件との一致)とcompile検査は
SQL定数へ直接(reeval流儀)。run_onceの分岐は抽出関数をmonkeypatchで差し替え、
行単位処理はFakeEngine/FakeConnスタブで検証する。
"""

import uuid
from datetime import UTC, datetime

from latch.worker.sweeper import (
    _COMPLETE_LATCH,
    _EXPIRE_CANDIDATE_LATCH,
    _EXPIRE_INTENT,
    _EXPIRE_RESPONSE_LATCH,
    _INSERT_ATTENDANCE_NOTIFICATION,
    _INSERT_LATCH_EVENT_SQL,
    _SELECT_COMPLETION_TARGETS,
    _SELECT_EXPIRING_INTENTS,
    _SELECT_EXPIRING_LATCHES,
    _SELECT_LATCH_PARTICIPANT_USERS,
    _SELECT_RECENT_CLOSE,
)

LATCH1 = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
LATCH2 = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
INTENT1 = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
INTENT2 = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
USER1 = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
USER2 = uuid.UUID("00000000-0000-4000-8000-0000000000c2")

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


# --- 1. latches期限切れ抽出SQLピン(引用#2・design §2.2) ---


def test_select_expiring_latches_pins_conditions():
    sql = str(_SELECT_EXPIRING_LATCHES)
    assert "status IN ('proposed', 'partial_accept')" in sql
    assert "response_deadline <= CAST(:now AS timestamptz)" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    # candidateは保留キュー(期限のみ)
    assert "status = 'candidate' AND expires_at <= CAST(:now AS timestamptz)" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT :batch_limit" in sql


def test_expire_response_latch_pins_same_truth_as_response_api():
    """proposed/partial_accept行のUPDATEは回答API(_UPDATE_RESPONSE)と同一の
    真実(期限とstatus)を再検査する(引用#3・C9)。"""
    sql = str(_EXPIRE_RESPONSE_LATCH)
    assert "SET status = 'expired'" in sql
    assert "status IN ('proposed', 'partial_accept')" in sql
    assert "response_deadline <= CAST(:now AS timestamptz)" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "RETURNING id" in sql


def test_expire_candidate_ignores_response_deadline():
    """candidate行はresponse_deadline(昇格時への暫定値)を判定に使わない
    (引用#2・Review Focus 2)。"""
    sql = str(_EXPIRE_CANDIDATE_LATCH)
    assert "SET status = 'expired'" in sql
    assert "status = 'candidate'" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "response_deadline" not in sql
    assert "RETURNING id" in sql


# --- 2. Intent期限切れSQLピン(引用#5/#6・design §2.3) ---


def test_select_expiring_intents_matches_partial_index():
    """抽出WHEREはidx_intents_expiresの部分索引条件と同一(design引用#6)。"""
    sql = str(_SELECT_EXPIRING_INTENTS)
    assert "status IN ('draft', 'active', 'paused')" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT :batch_limit" in sql


def test_expire_intent_pins_update_and_version_return():
    sql = str(_EXPIRE_INTENT)
    assert "SET status = 'expired', updated_at = CAST(:now AS timestamptz)" in sql
    assert "status IN ('draft', 'active', 'paused')" in sql
    assert "expires_at <= CAST(:now AS timestamptz)" in sql
    assert "RETURNING version" in sql


# --- 3. matched→completed SQLピン(引用#15・design §2.4) ---


def test_select_completion_targets_pins_max_time_start():
    sql = str(_SELECT_COMPLETION_TARGETS)
    assert "l.status = 'matched'" in sql
    assert "(SELECT max(i.time_start) FROM intents i" in sql
    assert "<= CAST(:now AS timestamptz)" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


def test_complete_latch_pins_matched_guard_and_completed_at():
    """cancelled(解散済み)はstatus='matched'でなく対象外=通知を送らない
    ことを構造的に担保(引用#15・Review Focus 3)。"""
    sql = str(_COMPLETE_LATCH)
    assert "SET status = 'completed', completed_at = CAST(:now AS timestamptz)" in sql
    assert "AND status = 'matched'" in sql
    assert "RETURNING intent_ids" in sql


# --- 4. クローズ検知観測SQLピン(design §2.7) ---


def test_select_recent_close_pins_window_and_statuses():
    sql = str(_SELECT_RECENT_CLOSE)
    assert "created_at > CAST(:last_tick AS timestamptz)" in sql
    assert "to_status IN ('rejected', 'expired', 'cancelled', 'matched')" in sql
    assert "LIMIT 1" in sql


# --- 5. compile検査(postgresql dialectで未変換の ':name' が残らない) ---


def test_sql_bind_params_compile():
    from sqlalchemy.dialects import postgresql

    targets = {
        _SELECT_EXPIRING_LATCHES: {"now", "batch_limit"},
        _EXPIRE_RESPONSE_LATCH: {"latch_id", "now"},
        _EXPIRE_CANDIDATE_LATCH: {"latch_id", "now"},
        _SELECT_EXPIRING_INTENTS: {"now", "batch_limit"},
        _EXPIRE_INTENT: {"intent_id", "now"},
        _SELECT_COMPLETION_TARGETS: {"now", "batch_limit"},
        _COMPLETE_LATCH: {"latch_id", "now"},
        _SELECT_LATCH_PARTICIPANT_USERS: {"ids"},
        _INSERT_LATCH_EVENT_SQL: {
            "latch_id",
            "from_status",
            "to_status",
            "user_id",
            "now",
        },
        _INSERT_ATTENDANCE_NOTIFICATION: {"user_id", "type", "payload", "now"},
        _SELECT_RECENT_CLOSE: {"last_tick"},
    }
    for stmt, keys in targets.items():
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        for key in keys:
            assert f":{key}" not in compiled, (key, compiled)
