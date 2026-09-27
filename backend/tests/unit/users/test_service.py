"""UserService: 年齢検証・INSERT委譲・エラー変換・me導出(design §4.1-2・§4.1-3)。

永続化はスタブ関数で差し替え(外部プロセス不要・決定的)。IntegrityErrorの
制約名分類はasyncpg例外を偽装して純粋関数として検証する(実DB経路は
integration #4が所有 — design §2.2)。
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.core.db import create_db_engine
from latch.settings import Settings
from latch.users.errors import (
    DependencyUnavailableError,
    UnderAgeError,
    UserExistsError,
    UserNotFoundError,
)
from latch.users.service import (
    NewUser,
    UserCreated,
    UserRow,
    UserService,
    classify_integrity_error,
    make_user_service,
)

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-27 21:00
USER_ID = uuid.uuid4()

CLAIMS = AccessTokenClaims(
    auth_provider="google",
    auth_subject="sub-1",
    jti="jti-1",
    sid="sid-1",
    iat=NOW,
    exp=NOW + timedelta(seconds=3600),
)


class CreateRecorder:
    """スタブcreate_user: 呼び出し記録+結果/例外の差し込み。"""

    def __init__(self, result=USER_ID, error=None):
        self.calls: list[NewUser] = []
        self._result = result
        self._error = error

    async def __call__(self, new_user: NewUser) -> uuid.UUID:
        self.calls.append(new_user)
        if self._error is not None:
            raise self._error
        return self._result


class FetchRecorder:
    """スタブfetch_by_auth: 呼び出し記録+結果/例外の差し込み。"""

    def __init__(self, result=None, error=None):
        self.calls: list[tuple[str, str]] = []
        self._result = result
        self._error = error

    async def __call__(self, provider: str, subject: str) -> UserRow | None:
        self.calls.append((provider, subject))
        if self._error is not None:
            raise self._error
        return self._result


ROW = UserRow(
    id=USER_ID,
    display_name="テスト太郎",
    profile={"bio": "よろしく"},
    birth_date=date(1990, 4, 1),
)


def _svc(clock, create, fetch) -> UserService:
    return UserService(clock=clock, create_user=create, fetch_by_auth=fetch)


async def test_register_under_age_raises_and_never_persists():
    # design §2.2: 年齢検証が先。17歳ならINSERT(list)は呼ばれない
    clock = FakeClock(NOW)  # jst_date() = 2026-09-27
    create = CreateRecorder()
    svc = _svc(clock, create, FetchRecorder())
    with pytest.raises(UnderAgeError):
        await svc.register(
            claims=CLAIMS,
            display_name="17歳",
            birth_date=date(2008, 9, 28),  # 誕生日前日=17歳
            profile={},
        )
    assert create.calls == []


async def test_register_adult_persists_with_clock_timestamps():
    clock = FakeClock(NOW)
    create = CreateRecorder()
    svc = _svc(clock, create, FetchRecorder())
    created = await svc.register(
        claims=CLAIMS,
        display_name="テスト太郎",
        birth_date=date(1990, 4, 1),
        profile={"bio": "よろしく"},
    )
    assert created == UserCreated(id=USER_ID, display_name="テスト太郎")
    (new_user,) = create.calls
    assert new_user.display_name == "テスト太郎"
    assert new_user.birth_date == date(1990, 4, 1)
    assert new_user.profile == {"bio": "よろしく"}
    assert new_user.auth_provider == CLAIMS.auth_provider  # JWT claim由来
    assert new_user.auth_subject == CLAIMS.auth_subject
    # 時刻はClock由来の明示値(design §2.5・確定値11)
    assert new_user.created_at == NOW
    assert new_user.updated_at == NOW


async def test_register_jst_midnight_boundary():
    # Review Focus #1: JST暦日の深夜0時で判定が転換する(UTC比較の9時間ずれ排除)
    birth = date(2008, 9, 28)  # 2026-09-28が18歳の誕生日
    clock = FakeClock(NOW)
    clock.set(datetime(2026, 9, 27, 14, 59, 59, tzinfo=UTC))  # JST 9/27 23:59:59
    svc = _svc(clock, CreateRecorder(), FetchRecorder())
    with pytest.raises(UnderAgeError):
        await svc.register(
            claims=CLAIMS, display_name="境界", birth_date=birth, profile={}
        )
    clock.set(datetime(2026, 9, 27, 15, 0, 0, tzinfo=UTC))  # JST 9/28 00:00:00
    ok = await svc.register(
        claims=CLAIMS, display_name="境界", birth_date=birth, profile={}
    )
    assert ok.display_name == "境界"
    assert ok.id == USER_ID


async def test_register_propagates_user_exists():
    create = CreateRecorder(error=UserExistsError("user already registered"))
    svc = _svc(FakeClock(NOW), create, FetchRecorder())
    with pytest.raises(UserExistsError):
        await svc.register(
            claims=CLAIMS,
            display_name="x",
            birth_date=date(1990, 4, 1),
            profile={},
        )


async def test_register_wraps_generic_error_as_503():
    create = CreateRecorder(error=RuntimeError("db down"))
    svc = _svc(FakeClock(NOW), create, FetchRecorder())
    with pytest.raises(DependencyUnavailableError):
        await svc.register(
            claims=CLAIMS,
            display_name="x",
            birth_date=date(1990, 4, 1),
            profile={},
        )


async def test_get_me_row_present_returns_complete_true():
    fetch = FetchRecorder(result=ROW)
    svc = _svc(FakeClock(NOW), CreateRecorder(), fetch)
    me = await svc.get_me(claims=CLAIMS)
    assert me.id == USER_ID
    assert me.display_name == "テスト太郎"
    assert me.profile == {"bio": "よろしく"}
    assert me.birth_date == date(1990, 4, 1)
    assert me.profile_complete is True  # 行の存在=true(design §1.2-9)
    assert fetch.calls == [("google", "sub-1")]  # claims由来の照会


async def test_get_me_row_missing_raises_404():
    # design §2.4: 初回登録待ちJWTのme → 404 NOT_FOUND
    svc = _svc(FakeClock(NOW), CreateRecorder(), FetchRecorder(result=None))
    with pytest.raises(UserNotFoundError):
        await svc.get_me(claims=CLAIMS)


async def test_get_me_wraps_generic_error_as_503():
    svc = _svc(
        FakeClock(NOW), CreateRecorder(), FetchRecorder(error=RuntimeError("db down"))
    )
    with pytest.raises(DependencyUnavailableError):
        await svc.get_me(claims=CLAIMS)


class _FakeAsyncpgError(Exception):
    """asyncpg例外の偽装(sqlstate・constraint_name 属性を持つ)。"""

    def __init__(self, sqlstate: str, constraint_name: str | None = None):
        super().__init__(f"fake dbapi error {sqlstate}")
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


def _integrity_error(sqlstate: str, constraint_name: str | None = None):
    return IntegrityError(
        "INSERT INTO users ...", {}, _FakeAsyncpgError(sqlstate, constraint_name)
    )


def test_classify_unique_violation_on_subject_constraint():
    # Review Focus #2: 対象制約のunique_violation(23505)のみ409へ
    err = classify_integrity_error(
        _integrity_error("23505", "uq_users_auth_provider_subject")
    )
    assert isinstance(err, UserExistsError)
    assert err.http_status == 409


def test_classify_other_constraint_returns_none():
    # 主キー違反等は409にせらず503扱い(None → 再送出)
    assert classify_integrity_error(_integrity_error("23505", "users_pkey")) is None


def test_classify_non_unique_violation_returns_none():
    # CHECK違反(23514)等は制約名が一致してもNone
    assert (
        classify_integrity_error(
            _integrity_error("23514", "uq_users_auth_provider_subject")
        )
        is None
    )


class _FakeDbapiWrapperError(Exception):
    """SQLAlchemy 2.1 asyncpg dialectラッパーの偽装(実測の形・2026-09-27)。

    IntegrityError.orig に実際に入るのは AsyncAdapt_asyncpg_dbapi.* ラッパーで、
    sqlstate/pgcode は持つが constraint_name は持たない。生のasyncpg例外は
    driver_exception (.orig) の先にある。
    """

    def __init__(self, sqlstate: str, constraint_name: str | None = None):
        driver = _FakeAsyncpgError(sqlstate, constraint_name)
        super().__init__(str(driver))
        self.sqlstate = sqlstate
        self.pgcode = sqlstate
        self.driver_exception = driver
        self.orig = driver


def _wrapped_integrity_error(sqlstate: str, constraint_name: str | None = None):
    return IntegrityError(
        "INSERT INTO users ...", {}, _FakeDbapiWrapperError(sqlstate, constraint_name)
    )


def test_classify_sqlalchemy_asyncpg_wrapper_shape():
    # スーパーバイザー検証で発見: 実経路の.origは dialectラッパーで
    # constraint_name を持たず503化した。ラッパー経由でも409へ分類できること
    err = classify_integrity_error(
        _wrapped_integrity_error("23505", "uq_users_auth_provider_subject")
    )
    assert isinstance(err, UserExistsError)
    assert err.http_status == 409


def test_classify_wrapper_other_constraint_returns_none():
    # ラッパー経由でも制約名不一致はNone(503扱い)のまま
    assert (
        classify_integrity_error(_wrapped_integrity_error("23505", "users_pkey"))
        is None
    )


async def test_make_user_service_returns_service():
    # 実SQL関数の実行はintegrationが所有。unitでは構築がUserServiceを
    # 返すことのみ(engineの接続は呼び出し時まで発生しない)
    engine = create_db_engine(Settings(app_env="ci"))
    try:
        svc = make_user_service(clock=FakeClock(NOW), engine=engine)
        assert isinstance(svc, UserService)
    finally:
        await engine.dispose()
