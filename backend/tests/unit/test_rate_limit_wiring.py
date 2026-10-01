"""依存差し替えの網羅(design §2.4「差し替え忘れは試験で網羅確認」)。

v1 API契約の全ルートが api_rate_limited を持ち、認証不要2経路+/healthが
対象外であることを構成で強制する(実HTTP不要の静的検査)。
"""

from collections import Counter

from fastapi.routing import APIRoute

from latch.main import create_app
from latch.ratelimit.deps import api_rate_limited

# C3(05 §5): 認証不要はtoken/refreshのみ。/health はv1契約の外(確定値13)
UNPROTECTED = {"/v1/auth/token", "/v1/auth/refresh", "/health"}


def _iter_api_routes(app):
    """app.routes をAPIRoute単位へ展開する。

    FastAPI 0.141はinclude_routerを_IncludedRouter(遅延評価)として載せるため
    ルータのルートがフラットなapp.routesへ出ない。original_router.routes
    (ルータ依存をマージ済み)を展開して辿る。
    """
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route
        elif type(route).__name__ == "_IncludedRouter":
            yield from route.original_router.routes


def _dep_target(dep):
    # FastAPI 0.141の params.Depends は dependency フィールド(旧 .call は廃止)
    return getattr(dep, "dependency", None)


def _has_rate_limit(route: APIRoute) -> bool:
    return any(_dep_target(dep) is api_rate_limited for dep in route.dependencies)


def test_all_v1_routes_are_rate_limited():
    app = create_app()
    protected = []
    for route in _iter_api_routes(app):
        if not isinstance(route, APIRoute):
            continue
        if route.path in UNPROTECTED:
            assert not _has_rate_limit(route), f"対象外のはず: {route.path}"
        else:
            assert _has_rate_limit(route), f"差し替え漏れ: {route.path}"
            protected.append(route.path)
    # method別にAPIRouteが分かれるためパス件数で数える(M1 ws-4時点の全契約)
    assert Counter(protected) == {
        "/v1/auth/logout": 1,
        "/v1/intents": 2,  # GET + POST
        "/v1/intents/expiry-options": 1,  # M1 ws-5追加(スーパーバイザー許可 2026-09-28)
        "/v1/intents/parse": 1,
        "/v1/intents/{intent_id}": 3,  # GET + PATCH + DELETE
        "/v1/intents/{intent_id}/pause": 1,
        "/v1/intents/{intent_id}/resume": 1,
        "/v1/latches": 1,  # M3 ws-1(一覧)
        "/v1/latches/{latch_id}": 1,  # M3 ws-1(詳細)
        "/v1/latches/{latch_id}/attendance": 1,  # M3 ws-4(実施自己申告)
        "/v1/latches/{latch_id}/messages": 2,  # M3 ws-4(送信+取得)
        "/v1/latches/{latch_id}/response": 1,  # M3 ws-1(回答)
        "/v1/notifications": 1,  # M3 ws-3(お知らせ一覧)
        "/v1/notifications/{notification_id}/read": 1,  # M3 ws-3(既読)
        "/v1/users": 1,
        "/v1/users/me": 1,
    }
