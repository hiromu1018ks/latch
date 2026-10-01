"""退会 delete_account のunit試験(M3 ws-6 design §2.3・§4.1)。

スタブuow(ScriptedConn)+記録cascade+記録SessionStoreで、SQL列・bind・
失効呼び出しの順序(txコミット後)を検証する。実DBでの削除結果は
integration/test_account_api.py が担う(§9-6の適合措置)。
"""

import uuid
from datetime import UTC, date, datetime

import pytest

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.users.errors import (
    DependencyUnavailableError,
    UserNotFoundError,
)
from latch.users.service import UserRow, UserService

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000011")
IID1 = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
IID2 = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
CLAIMS = AccessTokenClaims(
    auth_provider="google",
    auth_subject="sub-1",
    jti="jti-1",
    sid="sid-1",
    iat=NOW,
    exp=NOW.replace(hour=13),
)


class RowsResult:
    """複数行を返すSELECT応答(FakeResultは1行しか返せないため)。"""

    def __init__(self, rows: list):
        self._rows = rows
        self.rowcount = len(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeResultNone:
    def first(self):
        return None

    def fetchall(self):
        return []

    rowcount = 0


class ScriptedConn:
    def __init__(self, results: list):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = list(results)

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return RowsResult([])


class ScriptedUow:
    """engine.begin() と同じ形。aexitの抜けを記録(失効はコミット後の検証用)。"""

    def __init__(self, results: list):
        self.conn = ScriptedConn(results)
        self.begins = 0
        self.exits = 0

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        self.exits += 1
        return False


class RecordingSessions:
    def __init__(self):
        self.calls: list = []

    async def revoke_access(self, *, jti, exp, now):
        self.calls.append(("access", jti))

    async def revoke_family(self, *, sid):
        self.calls.append(("family", sid))


def _recording_cascade(log: list):
    async def _cascade(conn, intent_id, now):
        log.append(intent_id)

    return _cascade


async def _noop_create(_):
    raise AssertionError("create_user is not used here")


async def _fetch(_p, _s):
    return UserRow(
        id=USER_ID,
        display_name="退会者",
        profile={},
        birth_date=date(1990, 4, 1),
    )


def _svc(uow=None, sessions=None, cascade=None):
    return UserService(
        clock=FakeClock(NOW),
        create_user=_noop_create,
        fetch_by_auth=_fetch,
        uow=uow,
        sessions=sessions,
        cascade=cascade,
    )


async def test_delete_account_cascades_all_intents_and_user_updates():
    """全Intent(取得順)へcascade→messages/notifications削除→users置換。"""
    cascades: list = []
    uow = ScriptedUow(
        [
            RowsResult([(IID1,), (IID2,)]),  # _SELECT_ALL_INTENT_IDS
            FakeResultNone(),
            FakeResultNone(),
            FakeResultNone(),
        ]
    )
    sessions = RecordingSessions()
    svc = _svc(uow=uow, sessions=sessions, cascade=_recording_cascade(cascades))
    await svc.delete_account(claims=CLAIMS)
    assert cascades == [IID1, IID2]  # 取得順(id昇順)に全Intent分
    calls = uow.conn.calls
    assert "SELECT id FROM intents" in calls[0][0]
    assert calls[0][1]["user_id"] == USER_ID
    assert "DELETE FROM messages" in calls[1][0]
    assert calls[1][1]["user_id"] == USER_ID  # sender_id一致(design §2.3)
    assert "DELETE FROM notifications" in calls[2][0]
    anonymize = calls[3][0]
    assert "UPDATE users" in anonymize
    assert "退会したユーザー" in anonymize
    assert "'{}'::jsonb" in anonymize
    assert "'deleted:' || id::text" in anonymize
    assert calls[3][1] == {"user_id": USER_ID, "now": NOW}


async def test_delete_account_revokes_sessions_after_tx_exit():
    """失効(revoke_access+revoke_family)はuowのaexit後・両方呼ぶ。"""
    cascades: list = []
    uow = ScriptedUow(
        [RowsResult([]), FakeResultNone(), FakeResultNone(), FakeResultNone()]
    )
    sessions = RecordingSessions()
    svc = _svc(uow=uow, sessions=sessions, cascade=_recording_cascade(cascades))
    await svc.delete_account(claims=CLAIMS)
    # aexit(=コミット相当)が1回・その後sessions呼び出し2回
    assert uow.exits == 1
    assert sessions.calls == [("access", "jti-1"), ("family", "sid-1")]


async def test_delete_account_unknown_user_404():
    """未登録JWT: fetch_by_authがNone → UserNotFoundError・SQL/失効ゼロ。"""

    async def _none(_p, _s):
        return None

    uow = ScriptedUow([])
    sessions = RecordingSessions()
    svc = UserService(
        clock=FakeClock(NOW),
        create_user=_noop_create,
        fetch_by_auth=_none,
        uow=uow,
        sessions=sessions,
    )
    with pytest.raises(UserNotFoundError):
        await svc.delete_account(claims=CLAIMS)
    assert uow.begins == 0
    assert sessions.calls == []


async def test_delete_account_with_no_intents_is_idempotent_shape():
    """Intent 0件でもユーザー単位処理は走る(冪等再実行の形状)。"""
    cascades: list = []
    uow = ScriptedUow(
        [RowsResult([]), FakeResultNone(), FakeResultNone(), FakeResultNone()]
    )
    svc = _svc(
        uow=uow,
        sessions=RecordingSessions(),
        cascade=_recording_cascade(cascades),
    )
    await svc.delete_account(claims=CLAIMS)  # 例外なし
    assert cascades == []
    assert len(uow.conn.calls) == 4  # SELECT+messages+notifications+users


async def test_delete_account_without_uow_or_sessions_503():
    """uow/sessions未注入の呼び出しは503(失効しない退会は安全側でない)。"""
    svc = UserService(
        clock=FakeClock(NOW),
        create_user=_noop_create,
        fetch_by_auth=_fetch,
    )
    with pytest.raises(DependencyUnavailableError):
        await svc.delete_account(claims=CLAIMS)
