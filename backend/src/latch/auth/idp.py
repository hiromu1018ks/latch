"""IdPトークン(Google/Apple)のJWKS検証(design §2.4)。

- 署名検証はRS256にピン留め(実IdPのJWKSはRS256公開鍵配布モデル)
- JWKSソースは2方式: jwks_url指定=PyJWKClient(URL取得・キャッシュ)=本番経路 /
  未指定=同梱テストJWKSから静的構築(ci/staging既定。「取得失敗」という状態が存在しない)
- expはClock手動比較(§2.8)、sub(=auth_subject)の非空を検証
- JWKS取得の接続失敗は503 DEPENDENCY_UNAVAILABLE(05 第5節)、kid不一致は401
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt as pyjwt

from latch.auth.errors import (
    DependencyUnavailableError,
    InvalidIdpTokenError,
)
from latch.auth.testkeys import load_jwks
from latch.core.clock import Clock


@dataclass(frozen=True)
class IdPVerifyConfig:
    """providerごとの検証設定(issuer/audience/JWKSソース)。"""

    issuer: str
    audience: str
    jwks_url: str | None = None  # None=同梱テストJWKS


class IdPVerifier:
    """IdPトークンを検証し (provider, subject) を返す(D-21のJWKS検証経路)。"""

    def __init__(self, *, configs: dict[str, IdPVerifyConfig]) -> None:
        self._configs = configs
        # 同梱方式の静的鍵(kid→PyJWK)。URL方式はURLごとにPyJWKClientを遅延生成
        self._static_keys = {
            jwk["kid"]: pyjwt.PyJWK.from_dict(jwk, algorithm="RS256")
            for jwk in load_jwks()["keys"]
        }
        self._clients: dict[str, pyjwt.PyJWKClient] = {}

    async def verify(
        self, *, provider: str, idp_token: str, clock: Clock
    ) -> tuple[str, str]:
        cfg = self._configs.get(provider)
        if cfg is None:
            raise ValueError(f"unknown idp provider: {provider!r}")
        try:
            # 鍵解決もdecode側の例外網に含める(get_unverified_header は
            # 形式不正トークンで InvalidTokenError を投げる → 401が正)
            key = await self._resolve_key(cfg, idp_token)
            payload = pyjwt.decode(
                idp_token,
                key.key,
                algorithms=["RS256"],
                audience=cfg.audience,
                issuer=cfg.issuer,
                options={"verify_exp": False, "verify_iat": False},
            )
        except pyjwt.InvalidTokenError as exc:
            raise InvalidIdpTokenError("idp token rejected") from exc
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        if exp <= clock.now():
            raise InvalidIdpTokenError("idp token expired")
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidIdpTokenError("idp token subject missing")
        return provider, subject

    async def _resolve_key(self, cfg: IdPVerifyConfig, token: str) -> pyjwt.PyJWK:
        if cfg.jwks_url:
            client = self._clients.get(cfg.jwks_url)
            if client is None:
                client = pyjwt.PyJWKClient(cfg.jwks_url, timeout=5.0)
                self._clients[cfg.jwks_url] = client
            try:
                # 同期APIをスレッドへ逃がす(この版のPyJWKClientにasync版が
                # ないため — 計画書Task 4注記の代替経路)
                return await asyncio.to_thread(client.get_signing_key_from_jwt, token)
            except pyjwt.PyJWKClientConnectionError as exc:
                raise DependencyUnavailableError("jwks fetch failed") from exc
            except pyjwt.PyJWKClientError as exc:
                raise InvalidIdpTokenError("signing key not found for kid") from exc
        header = pyjwt.get_unverified_header(token)
        key = self._static_keys.get(header.get("kid", ""))
        if key is None:
            raise InvalidIdpTokenError("signing key not found for kid")
        return key
