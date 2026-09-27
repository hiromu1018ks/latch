"""RedisセッションStore: 回転・再利用検知→族失効・不在401・失効TTL(design §2.3他)。

fakeredis(redis-pyのコマンド解釈経路をそのまま実行)で決定的に検証する。
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest

from latch.auth.errors import InvalidRefreshTokenError
from latch.auth.sessions import REFRESH_TTL_S, SessionStore
from latch.core.clock import FakeClock

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _store(redis) -> SessionStore:
    return SessionStore(redis)


async def test_create_refresh_stores_hashed_token_with_ttl(redis):
    store = _store(redis)
    issued = await store.create_refresh(provider="google", subject="sub-1")
    assert issued.token
    assert issued.sid
    # 生値をRedisに置かない: 鍵名はSHA-256ハッシュのみ
    keys = sorted(await redis.keys("auth:*"))
    assert issued.token not in "".join(keys)
    raw = await redis.get(f"auth:rt:{_sha(issued.token)}")
    data = json.loads(raw)
    assert data == {"provider": "google", "subject": "sub-1", "family": issued.sid}
    ttl = await redis.ttl(f"auth:rt:{_sha(issued.token)}")
    assert 0 < ttl <= REFRESH_TTL_S  # 30日(design §2.3)
    assert await redis.smembers(f"auth:family:{issued.sid}") == {_sha(issued.token)}


async def test_rotate_returns_new_token_and_keeps_family(redis, clock):
    store = _store(redis)
    issued = await store.create_refresh(provider="google", subject="sub-1")
    result = await store.rotate_refresh(token=issued.token, now=clock.now())
    assert result.token != issued.token
    assert result.sid == issued.sid  # 族は不変
    assert result.provider == "google"
    assert result.subject == "sub-1"
    # 旧トークンは消費済(GETDEL)・消費済みマーカーに族id・新トークン格納
    assert await redis.get(f"auth:rt:{_sha(issued.token)}") is None
    assert await redis.get(f"auth:used:{_sha(issued.token)}") == issued.sid
    assert await redis.get(f"auth:rt:{_sha(result.token)}") is not None
    assert await redis.smembers(f"auth:family:{issued.sid}") == {
        _sha(issued.token),
        _sha(result.token),
    }


async def test_reuse_of_consumed_token_revokes_whole_family(redis, clock):
    # Review Focus #2: 回転済みトークンの再提示=盗難疑い → 族全失効(05 第5節 確定値3)
    store = _store(redis)
    issued = await store.create_refresh(provider="google", subject="sub-1")
    rotated = await store.rotate_refresh(token=issued.token, now=clock.now())
    with pytest.raises(InvalidRefreshTokenError):
        await store.rotate_refresh(token=issued.token, now=clock.now())
    # 回転後の新トークンも同一族なので失効している
    with pytest.raises(InvalidRefreshTokenError):
        await store.rotate_refresh(token=rotated.token, now=clock.now())
    assert await redis.exists(f"auth:family:{issued.sid}") == 0
    assert await redis.get(f"auth:rt:{_sha(rotated.token)}") is None


async def test_unknown_token_rejected_without_family_revocation(redis, clock):
    # 不在・期限切れトークンから族を特定できない → 族失効しない(design §2.3)
    store = _store(redis)
    other = await store.create_refresh(provider="google", subject="sub-2")
    with pytest.raises(InvalidRefreshTokenError):
        await store.rotate_refresh(token="totally-unknown-token", now=clock.now())
    assert await redis.get(f"auth:rt:{_sha(other.token)}") is not None
    assert await redis.exists(f"auth:family:{other.sid}") == 1


async def test_revoke_access_ttl_is_remaining_lifetime(redis, clock):
    # Review Focus #3: 失効リストTTLは残り有効期限(exp−now)以下 — 無限に伸びない
    store = _store(redis)
    exp = NOW + timedelta(seconds=1200)
    await store.revoke_access(jti="jti-1", exp=exp, now=clock.now())
    ttl = await redis.ttl("auth:revoked:jti-1")
    assert 0 < ttl <= 1200
    assert await redis.get("auth:revoked:jti-1") == "1"


async def test_is_revoked(redis, clock):
    store = _store(redis)
    assert await store.is_revoked(jti="jti-x") is False
    await store.revoke_access(
        jti="jti-x", exp=NOW + timedelta(seconds=60), now=clock.now()
    )
    assert await store.is_revoked(jti="jti-x") is True


async def test_revoke_family_deletes_rt_keys_and_index(redis, clock):
    store = _store(redis)
    issued = await store.create_refresh(provider="google", subject="sub-1")
    rotated = await store.rotate_refresh(token=issued.token, now=clock.now())
    await store.revoke_family(sid=issued.sid)
    for token in (issued.token, rotated.token):
        assert await redis.get(f"auth:rt:{_sha(token)}") is None
    assert await redis.exists(f"auth:family:{issued.sid}") == 0
