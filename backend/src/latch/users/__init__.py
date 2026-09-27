"""usersドメイン(M1 ws-1)。初回登録(POST /v1/users)と本人取得(GET /v1/users/me)。

M1以降の呼び出し側(intents CRUD等)はこのパッケージ越しにUserService・
users_router・例外を利用する(design §3.1)。
"""

from latch.users.errors import (
    DependencyUnavailableError,
    UnderAgeError,
    UserExistsError,
    UserNotFoundError,
    UsersError,
)
from latch.users.routes import users_router
from latch.users.service import (
    UserCreated,
    UserMe,
    UserRow,
    UserService,
    age_years,
    make_user_service,
)

__all__ = [
    "DependencyUnavailableError",
    "UnderAgeError",
    "UserCreated",
    "UserExistsError",
    "UserMe",
    "UserNotFoundError",
    "UserRow",
    "UserService",
    "UsersError",
    "age_years",
    "make_user_service",
    "users_router",
]
