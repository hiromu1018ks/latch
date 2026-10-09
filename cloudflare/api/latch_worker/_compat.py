"""Workers実行環境向けのimport互換層(本体実装・2026-10-09)。

バンドル上限(gzip 10MB)のため venv に入れられない重依存
(sqlalchemy/redis/cryptography/anthropic/typesafe_sdk/pyjwt)のうち、
backend/src/latch の**module top-level import**を満たすための最小stubを
sys.modules へ登録する。実行時にこれらへ到達する経路(Real LLM・Redis・
SQLAlchemy engine)はWorkers構成に存在しない — 到達した場合は即例外で
気付けるようにしてある。

- `jwt` だけは機能実装(jwt_pure: HS256発行/検証・RS256検証)を登録する。
  auth/tokens.py・idp.py が実pyjwtと同じAPI面で動くための差し替え。
- この install_shims() はWorkersのentry.py起動時のみ呼ぶ(CPython試験では
  呼ばない — 実パッケージが使われる)。
"""

from __future__ import annotations

import sys
import types


class _UseRaise:
    """stub属性の実体。呼び出し/初期化された時点で構成ミスとして例外。"""

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError(f"{self._name} is not available on Workers")


def _lazy_stub_module(name: str, attrs: dict[str, object] | None = None) -> types.ModuleType:
    """任意の属性要求へ _UseRaise を返すmodule(instantiationで例外)。"""

    mod = types.ModuleType(name)
    mod.__getattr__ = lambda attr: _UseRaise(f"{name}.{attr}")  # type: ignore[method-assign]
    for key, value in (attrs or {}).items():
        setattr(mod, key, value)
    return mod


class _TextClauseStub:
    """sqlalchemy.text() の戻り(module定義時SQL定数で使われる・実行はされない)。"""

    __slots__ = ("text",)

    def __init__(self, text_value: str) -> None:
        self.text = text_value


class _StubIntegrityError(Exception):
    pass


def _sqlalchemy_modules() -> dict[str, types.ModuleType]:
    root = types.ModuleType("sqlalchemy")
    root.text = lambda s: _TextClauseStub(s)
    exc = types.ModuleType("sqlalchemy.exc")
    exc.IntegrityError = _StubIntegrityError
    ext = types.ModuleType("sqlalchemy.ext")
    exta = types.ModuleType("sqlalchemy.ext.asyncio")
    exta.AsyncEngine = object
    exta.AsyncConnection = object
    exta.AsyncSession = object
    return {
        "sqlalchemy": root,
        "sqlalchemy.exc": exc,
        "sqlalchemy.ext": ext,
        "sqlalchemy.ext.asyncio": exta,
    }


def _redis_modules() -> dict[str, types.ModuleType]:
    root = _lazy_stub_module("redis")
    asyncio_mod = _lazy_stub_module(
        "redis.asyncio", attrs={"Redis": _UseRaise("redis.asyncio.Redis")}
    )

    class _RedisError(Exception):
        pass

    exceptions_mod = types.ModuleType("redis.exceptions")
    exceptions_mod.RedisError = _RedisError
    exceptions_mod.ConnectionError = _RedisError
    exceptions_mod.TimeoutError = _RedisError
    return {
        "redis": root,
        "redis.asyncio": asyncio_mod,
        "redis.exceptions": exceptions_mod,
    }


def _cryptography_modules() -> dict[str, types.ModuleType]:
    def make(name: str, attrs: dict[str, object] | None = None):
        return _lazy_stub_module(name, attrs=attrs)

    serialization = make(
        "cryptography.hazmat.primitives.serialization",
        attrs={
            "load_pem_private_key": _UseRaise("serialization.load_pem_private_key"),
            "load_pem_public_key": _UseRaise("serialization.load_pem_public_key"),
        },
    )
    rsa = make(
        "cryptography.hazmat.primitives.asymmetric.rsa",
        attrs={"RSAPrivateKey": object, "RSAPublicKey": object},
    )
    return {
        "cryptography": make("cryptography"),
        "cryptography.hazmat": make("cryptography.hazmat"),
        "cryptography.hazmat.primitives": make("cryptography.hazmat.primitives"),
        "cryptography.hazmat.primitives.serialization": serialization,
        "cryptography.hazmat.primitives.asymmetric": make(
            "cryptography.hazmat.primitives.asymmetric"
        ),
        "cryptography.hazmat.primitives.asymmetric.rsa": rsa,
    }


class _StubModel:
    """typesafe_sdk の pydantic模型クラス相当(module定義時のmodel_validate通過用)。"""

    @classmethod
    def model_validate(cls, value):  # noqa: ANN001
        return value

    @classmethod
    def model_validate_json(cls, value):  # noqa: ANN001
        return value


def _typesafe_module() -> types.ModuleType:
    mod = types.ModuleType("typesafe_sdk")

    class _TSAPIError(Exception):
        pass

    class _TSConnectionError(_TSAPIError):
        pass

    class _TSRateLimitError(_TSAPIError):
        pass

    mod.AsyncTypeSafeClient = _UseRaise("typesafe_sdk.AsyncTypeSafeClient")
    mod.Noul = _StubModel
    mod.Score = _StubModel
    mod.RetryPolicy = _UseRaise("typesafe_sdk.RetryPolicy")
    mod.TypeSafeAPIError = _TSAPIError
    mod.TypeSafeAPIConnectionError = _TSConnectionError
    mod.TypeSafeRateLimitError = _TSRateLimitError
    return mod


def _jwt_module() -> types.ModuleType:
    from latch_worker import jwt_pure

    mod = types.ModuleType("jwt")
    mod.__dict__.update(jwt_pure._as_module_dict())
    return mod


_INSTALLED = False


def install_shims() -> None:
    """Workers起動時に一度だけ呼ぶ(既存実パッケージを上書きしない前提)。"""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    to_register: dict[str, types.ModuleType] = {}
    to_register["jwt"] = _jwt_module()
    to_register.update(_sqlalchemy_modules())
    to_register.update(_redis_modules())
    to_register.update(_cryptography_modules())
    to_register["anthropic"] = _lazy_stub_module(
        "anthropic", attrs={"AsyncAnthropic": _UseRaise("anthropic.AsyncAnthropic")}
    )
    to_register["typesafe_sdk"] = _typesafe_module()
    for name, mod in to_register.items():
        sys.modules[name] = mod
    # 子module親参照(import文の属性解決用)
    sys.modules["sqlalchemy"].exc = sys.modules["sqlalchemy.exc"]  # type: ignore[attr-defined]
    sys.modules["sqlalchemy"].ext = sys.modules["sqlalchemy.ext"]  # type: ignore[attr-defined]
    sys.modules["sqlalchemy.ext"].asyncio = sys.modules[  # type: ignore[attr-defined]
        "sqlalchemy.ext.asyncio"
    ]
    sys.modules["redis"].asyncio = sys.modules["redis.asyncio"]  # type: ignore[attr-defined]
    sys.modules["redis"].exceptions = sys.modules[  # type: ignore[attr-defined]
        "redis.exceptions"
    ]
