"""consent serviceのunit(M4 ws-5 計画Task 3・design §2.1・§2.3)。

Callable注入で決定的に検証する(UserServiceのunitと同型)。時刻はClock由来
の明示値のみ(tz-aware UTC — Clock規約)。冪等=後勝ちで時刻更新(UPDATE 1本・
特殊分岐なし)。
"""

import uuid
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.users.consent import ConsentService, make_consent_service
from latch.users.errors import DependencyUnavailableError, UserNotFoundError

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 3, 13, 0, 0, tzinfo=UTC)
USER_ID = uuid.uuid4()


class StubClaims:
    auth_provider = "google"
    auth_subject = "sub-1"


class _Svc:
    """record/fetch/consented/existsの記録スタブ。"""

    def __init__(
        self,
        *,
        record=None,
        consented_at=None,
        consented=True,
        exists=True,
        fail=False,
    ):
        self.record_calls = []
        self._record = record
        self._consented_at = consented_at
        self._consented = consented
        self._exists = exists
        self._fail = fail

    async def record(self, claims, now):
        self.record_calls.append((claims, now))
        if self._fail:
            raise RuntimeError("db down")
        return self._record

    async def fetch(self, claims):
        if self._fail:
            raise RuntimeError("db down")
        return self._consented_at

    async def consented(self, user_id):
        if self._fail:
            raise RuntimeError("db down")
        return self._consented

    async def exists(self, claims):
        if self._fail:
            raise RuntimeError("db down")
        return self._exists  # SELECT consent_atのNoneを未同意と区別するため


def _service(svc: _Svc, *, clock_value=NOW) -> ConsentService:
    return ConsentService(
        clock=FakeClock(clock_value),
        record_consent=svc.record,
        fetch_consent=svc.fetch,
        consented=svc.consented,
        exists_fn=svc.exists,
    )


async def test_record_returns_state_with_clock_time():
    svc = _Svc(record=NOW)
    claims = StubClaims()
    state = await _service(svc).record(claims=claims)
    assert state.consented_at == NOW
    assert svc.record_calls == [(claims, NOW)]  # Clock由来の時刻が渡る


async def test_record_overwrites_timestamp_later_wins():
    """再POSTは後勝ちで時刻更新(UPDATE 1本のため特殊分岐なし — 計画§5-1)。"""
    svc = _Svc(record=LATER)
    state = await _service(svc, clock_value=LATER).record(claims=StubClaims())
    assert state.consented_at == LATER


async def test_record_raises_404_when_row_missing():
    svc = _Svc(record=None)  # UPDATE ... RETURNING が空=未登録
    with pytest.raises(UserNotFoundError):
        await _service(svc).record(claims=StubClaims())


async def test_record_wraps_unexpected_as_503():
    svc = _Svc(fail=True)
    with pytest.raises(DependencyUnavailableError):
        await _service(svc).record(claims=StubClaims())


async def test_get_returns_none_when_never_consented():
    """未同意は200 null(404ではない — 設定画面の初回表示が壊れない)。"""
    svc = _Svc(consented_at=None)
    state = await _service(svc).get(claims=StubClaims())
    assert state.consented_at is None


async def test_get_returns_timestamp():
    svc = _Svc(consented_at=NOW)
    state = await _service(svc).get(claims=StubClaims())
    assert state.consented_at == NOW


async def test_get_raises_404_when_row_missing():
    """SELECT consent_at=Noneのとき行もなければ404(未同意のnullと区別)。"""

    class _NoRow(_Svc):
        def __init__(self):
            super().__init__(consented_at=None, exists=False)

        async def fetch(self, claims):
            return None  # 行なし=未登録

    with pytest.raises(UserNotFoundError):
        await _service(_NoRow()).get(claims=StubClaims())


async def test_consented_returns_bool():
    assert await _service(_Svc(consented=True)).consented(USER_ID) is True
    assert await _service(_Svc(consented=False)).consented(USER_ID) is False


async def test_consented_wraps_unexpected_as_503():
    with pytest.raises(DependencyUnavailableError):
        await _service(_Svc(fail=True)).consented(USER_ID)


class _FakeEngine:
    """make_consent_serviceの結線確認用(実SQLはtest-ciが所有)。"""


def test_make_consent_service_returns_service():
    """ファクトリがConsentServiceを返す(engineはクロージャで束縛)。"""
    svc = make_consent_service(clock=FakeClock(NOW), engine=_FakeEngine())
    assert isinstance(svc, ConsentService)
