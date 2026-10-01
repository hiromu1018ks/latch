"""BlockReportService(block系)のunit試験(M3 ws-5 design §4.1)。

storeはスタブ(monkeypatch差し替え)でSQLに依存しない(test_chat_service.py
と同型)。時刻はFakeClock。latches側資産(cancel_latch・insert_latch_event)
も差し替え。SQL検査はtest_safety_store_sql.py・実HTTPは
test_safety_api.py(integration)の担い。
"""

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.latches import store as latches_store
from latch.safety import store as safety_store
from latch.safety.errors import SafetyValidationError
from latch.safety.service import (
    BlockReportService,
    decode_block_cursor,
    encode_block_cursor,
)
from latch.safety.store import BlockRow

NOW = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
ME = uuid.uuid4()
TARGET = uuid.uuid4()
LATCH1 = uuid.uuid4()
LATCH2 = uuid.uuid4()
LATCH3 = uuid.uuid4()


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


def _ret(value):
    """monkeypatch差し替え用の「常にvalueを返すasync関数」ファクトリ。"""

    async def _inner(*args, **kwargs):
        return value

    return _inner


class _StubCache:
    """BlockCacheのスタブ(invalidate呼び出し記録のみ・§2.2)。"""

    def __init__(self):
        self.invalidated: list[list[uuid.UUID]] = []

    async def invalidate(self, user_ids):
        self.invalidated.append(list(user_ids))


def _svc(cache=None) -> BlockReportService:
    return BlockReportService(
        clock=FakeClock(NOW), engine=_NoopEngine(), block_cache=cache
    )


def _row(**overrides) -> BlockRow:
    base = dict(
        id=uuid.uuid4(),
        blocked_id=uuid.uuid4(),
        display_name="相手",
        created_at=NOW - timedelta(minutes=5),
    )
    base.update(overrides)
    return BlockRow(**base)


def _b64(s: str) -> str:
    """テスト用: 不透明cursor候補の生成(base64url・パディング除去)。"""
    return base64.urlsafe_b64encode(s.encode()).rstrip(b"=").decode()


def test_block_cursor_roundtrip():
    """2キー(created_at,id)のencode/decode往復(design §2.4)。"""
    token = encode_block_cursor(NOW, LATCH1)
    assert decode_block_cursor(token) == (NOW, LATCH1)
    assert "=" not in token  # base64urlのパディング除去(不透明文字列)


def test_block_cursor_invalid_422():
    """形式不正cursorは422 VALIDATION_ERROR(latches/intentsと同型)。"""
    for bad in (
        _b64("no-pipe-here"),  # 区切りなし
        _b64("not-a-datetime|" + str(uuid.uuid4())),  # 日時復元失敗
        "!!!",  # base64urlとして不正
    ):
        with pytest.raises(SafetyValidationError):
            decode_block_cursor(bad)


async def test_block_self_422(monkeypatch):
    """自分自身のブロックは422(引用#15 — 専用codeなし)。"""
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    with pytest.raises(SafetyValidationError):
        await _svc().block_user(
            auth_provider="google", auth_subject="s", target_user_id=ME
        )


async def test_block_unregistered_jwt_404(monkeypatch):
    """未登録JWTは404(design §2.4手順1・チャットAPIと同じ扱い)。"""
    from latch.safety.errors import SafetyNotFoundError

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(None))
    with pytest.raises(SafetyNotFoundError):
        await _svc().block_user(
            auth_provider="google", auth_subject="s", target_user_id=TARGET
        )


async def test_block_target_missing_404(monkeypatch):
    """相手ユーザー不在は404(design §2.4手順3)。"""
    from latch.safety.errors import SafetyNotFoundError

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(False))
    with pytest.raises(SafetyNotFoundError):
        await _svc().block_user(
            auth_provider="google", auth_subject="s", target_user_id=TARGET
        )


async def test_block_inserts_and_cancels_d23(monkeypatch):
    """登録はINSERT+D-23 cancelled化+イベント(user_id=blocker・§2.3)。

    Review Focus 2: 対象3行(candidate/proposed/partial_accept)すべてへ
    cancel_latch→insert_latch_event。イベントのuser_idはblocker(引用#12)。
    """
    inserts: list[dict] = []
    events: list[tuple] = []

    async def _insert_block(conn, *, blocker, blocked, now):
        inserts.append({"blocker": blocker, "blocked": blocked, "now": now})
        return uuid.uuid4()

    async def _event(conn, latch_id, from_status, to_status, user_id, now):
        events.append((latch_id, from_status, to_status, user_id, now))

    cancelled: list[uuid.UUID] = []

    async def _cancel(conn, latch_id):
        cancelled.append(latch_id)
        return True

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))
    monkeypatch.setattr(safety_store, "block_exists", _ret(False))
    monkeypatch.setattr(safety_store, "insert_block", _insert_block)
    monkeypatch.setattr(safety_store, "select_user_intent_ids", _ret([uuid.uuid4()]))
    monkeypatch.setattr(
        safety_store,
        "select_open_latches_between",
        _ret(
            [
                (LATCH1, "candidate"),
                (LATCH2, "proposed"),
                (LATCH3, "partial_accept"),
            ]
        ),
    )
    monkeypatch.setattr(latches_store, "cancel_latch", _cancel)
    monkeypatch.setattr(latches_store, "insert_latch_event", _event)
    got = await _svc().block_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )
    assert got == TARGET  # 201応答のblocked_id
    assert inserts == [{"blocker": ME, "blocked": TARGET, "now": NOW}]
    assert cancelled == [LATCH1, LATCH2, LATCH3]  # ORDER BY id相当の対象順
    assert events == [
        (LATCH1, "candidate", "cancelled", ME, NOW),
        (LATCH2, "proposed", "cancelled", ME, NOW),
        (LATCH3, "partial_accept", "cancelled", ME, NOW),
    ]


async def test_block_cancel_race_skips_event(monkeypatch):
    """cancel_latch競合負け(False)の行はイベントを挿まない(§2.3)。"""
    events: list[tuple] = []

    async def _event(conn, latch_id, from_status, to_status, user_id, now):
        events.append(latch_id)

    async def _cancel(conn, latch_id):
        return latch_id == LATCH1  # LATCH2は負け

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))
    monkeypatch.setattr(safety_store, "block_exists", _ret(False))
    monkeypatch.setattr(safety_store, "insert_block", _ret(uuid.uuid4()))
    monkeypatch.setattr(safety_store, "select_user_intent_ids", _ret([]))
    monkeypatch.setattr(
        safety_store,
        "select_open_latches_between",
        _ret([(LATCH1, "proposed"), (LATCH2, "proposed")]),
    )
    monkeypatch.setattr(latches_store, "cancel_latch", _cancel)
    monkeypatch.setattr(latches_store, "insert_latch_event", _event)
    await _svc().block_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )
    assert events == [LATCH1]


async def test_block_idempotent_existing_201(monkeypatch):
    """既存ブロック時はINSERTもcancelled化もしない(冪等201・§2.4手順4)。"""
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))
    monkeypatch.setattr(safety_store, "block_exists", _ret(True))

    async def _fail(*args, **kwargs):
        raise AssertionError("呼ばれないはず")

    monkeypatch.setattr(safety_store, "insert_block", _fail)
    monkeypatch.setattr(safety_store, "select_user_intent_ids", _fail)
    monkeypatch.setattr(safety_store, "select_open_latches_between", _fail)
    monkeypatch.setattr(latches_store, "cancel_latch", _fail)
    got = await _svc().block_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )
    assert got == TARGET


async def test_block_invalidates_cache_after_commit(monkeypatch):
    """コミット後に両者のキーをDEL(§2.2・Review Focus 3)。

    invalidateはtx抜け後の呼び出し(insert_blockと同一conn上でない)。
    コード構造で担保し、ここでは「登録成功で両者へ1回だけ呼ばれる」をピン。
    """
    cache = _StubCache()
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "user_exists", _ret(True))
    monkeypatch.setattr(safety_store, "block_exists", _ret(False))
    monkeypatch.setattr(safety_store, "insert_block", _ret(uuid.uuid4()))
    monkeypatch.setattr(safety_store, "select_user_intent_ids", _ret([]))
    monkeypatch.setattr(safety_store, "select_open_latches_between", _ret([]))
    await _svc(cache).block_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )
    assert cache.invalidated == [[ME, TARGET]]
    # 未注入時はスキップ(例外にならない)
    await _svc().block_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )


async def test_unblock_missing_404(monkeypatch):
    """blocks行なしの解除は404(冪等204にしない・design §2.4)。"""
    from latch.safety.errors import SafetyNotFoundError

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "delete_block", _ret(False))
    with pytest.raises(SafetyNotFoundError):
        await _svc().unblock_user(
            auth_provider="google", auth_subject="s", target_user_id=TARGET
        )


async def test_unblock_deletes_and_invalidates(monkeypatch):
    """解除成功は行削除+コミット後キャッシュDEL(§2.4)。遡及処理なし。"""
    cache = _StubCache()
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "delete_block", _ret(True))
    await _svc(cache).unblock_user(
        auth_provider="google", auth_subject="s", target_user_id=TARGET
    )
    assert cache.invalidated == [[ME, TARGET]]


async def test_list_blocks_pagination(monkeypatch):
    """limit+1件取得→溢れたらnext_cursor生成(latches一覧と同型・§2.4)。"""
    r1 = _row()
    r2 = _row()
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(safety_store, "select_blocks_page", _ret([r1, r2]))
    items, next_cursor = await _svc().list_blocks(
        auth_provider="google", auth_subject="s", limit=1
    )
    assert items == [r1]  # limit=1で1件だけ返す
    assert next_cursor == encode_block_cursor(r1.created_at, r1.id)
    # cursor渡しはdecodeしてbeforeへ渡される
    monkeypatch.setattr(safety_store, "select_blocks_page", _ret([r2]))
    items2, next2 = await _svc().list_blocks(
        auth_provider="google",
        auth_subject="s",
        cursor=encode_block_cursor(r1.created_at, r1.id),
        limit=1,
    )
    assert items2 == [r2]
    assert next2 is None  # 次頁なし


async def test_list_blocks_invalid_cursor_422(monkeypatch):
    """形式不正cursorは422(design §2.4)。未登録JWTも404。"""
    from latch.safety.errors import SafetyNotFoundError

    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(ME))
    with pytest.raises(SafetyValidationError):
        await _svc().list_blocks(auth_provider="google", auth_subject="s", cursor="!!!")
    monkeypatch.setattr(latches_store, "fetch_user_id", _ret(None))
    with pytest.raises(SafetyNotFoundError):
        await _svc().list_blocks(auth_provider="google", auth_subject="s")
