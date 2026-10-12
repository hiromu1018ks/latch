"""PushTokenService(05 v0.9・プッシュトークン登録)のunit試験。

register(upsert)/list/removeの契約・404系(upsert後のuser不在は再検査で
UserNotFound)をCallableスタブで検証する(実SQLはintegration/契約が担う)。
"""

from __future__ import annotations

import datetime as dt
from datetime import timedelta

import pytest

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.users.errors import PushTokenNotFoundError, UserNotFoundError
from latch.users.push import PushTokenService, PushTokenView

NOW = dt.datetime(2026, 10, 12, 9, 0, tzinfo=dt.UTC)


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="push-unit-1",
        jti="jti",
        sid="sid",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _view(token_last4: str = "ab3f") -> PushTokenView:
    return PushTokenView(token_last4=token_last4, platform="web", registered_at=NOW)


def _make(
    *,
    exists: bool = True,
    upsert_row: PushTokenView | None = None,
    tokens: list[PushTokenView] | None = None,
    deleted: bool = True,
) -> tuple[PushTokenService, dict]:
    state: dict = {
        "upserts": [],
        "deleted_tokens": [],
    }

    async def exists_user(claims: AccessTokenClaims) -> bool:
        return exists

    async def upsert(claims, token, platform, now):
        if upsert_row is None:
            # exists_userがTrueでもINSERTが0行になるのは対象user不在のみ
            raise AssertionError("upsert should not be called (test setup)")
        state["upserts"].append((token, platform))
        return upsert_row

    async def list_tokens(claims: AccessTokenClaims) -> list[PushTokenView]:
        return list(tokens or [])

    async def delete_token(claims, token) -> bool:
        state["deleted_tokens"].append(token)
        return deleted

    svc = PushTokenService(
        clock=FakeClock(NOW),
        exists_user=exists_user,
        upsert=upsert,
        list_tokens=list_tokens,
        delete_token=delete_token,
    )
    return svc, state


# -- register ----------------------------------------------------------------


async def test_register_ok_returns_masked_view():
    svc, state = _make(upsert_row=_view())
    view = await svc.register(claims=_claims(), token="stub-token-ab3f", platform="web")
    assert view.token_last4 == "ab3f"
    assert view.platform == "web"
    assert state["upserts"] == [("stub-token-ab3f", "web")]


async def test_register_unknown_user_404():
    svc, _ = _make(exists=False)
    with pytest.raises(UserNotFoundError):
        await svc.register(claims=_claims(), token="t", platform="web")


async def test_register_dependency_failure_503():
    async def exists_user(claims):
        raise RuntimeError("db down")

    svc = PushTokenService(
        clock=FakeClock(NOW),
        exists_user=exists_user,
        upsert=lambda *a: None,  # 呼ばれない
        list_tokens=lambda *a: None,
        delete_token=lambda *a: None,
    )
    from latch.users.errors import DependencyUnavailableError

    with pytest.raises(DependencyUnavailableError):
        await svc.register(claims=_claims(), token="t", platform="web")


# -- list --------------------------------------------------------------------


async def test_list_ok_and_empty():
    svc, _ = _make(tokens=[_view("zz9z")])
    views = await svc.list(claims=_claims())
    assert [v.token_last4 for v in views] == ["zz9z"]

    empty_svc, _ = _make(tokens=[])
    assert await empty_svc.list(claims=_claims()) == []


async def test_list_unknown_user_404():
    svc, _ = _make(exists=False)
    with pytest.raises(UserNotFoundError):
        await svc.list(claims=_claims())


# -- remove ------------------------------------------------------------------


async def test_remove_ok():
    svc, state = _make(deleted=True)
    await svc.remove(claims=_claims(), token="stub-token-ab3f")
    assert state["deleted_tokens"] == ["stub-token-ab3f"]


async def test_remove_not_registered_404():
    svc, _ = _make(deleted=False)
    with pytest.raises(PushTokenNotFoundError):
        await svc.remove(claims=_claims(), token="unknown-token")


async def test_remove_unknown_user_404():
    svc, _ = _make(exists=False)
    with pytest.raises(UserNotFoundError):
        await svc.remove(claims=_claims(), token="t")
