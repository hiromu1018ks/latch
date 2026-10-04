"""massloadの実DB検証(M4 ws-6・integration・test-ciで実行)。

少量fixture(20件)を投げ・active条件の全列を照合し・投入した行を自前で
掃除する(masstest-接頭辞・既存行への触りなし・§2.2(c))。

接続先はLATCH_TEST_MASSLOAD_DSN(既定=load環境DB 127.0.0.1:25432・
スーパーバイザー検証時はload環境起動後にtest-ciで走る)。既存db_engine
fixtureはSettings()(ci宛)のため・本ファイルは同一DSN宛のengineを自前で
持つ(照合と投入が同一DBであることの保証)。実行はスーパーバイザー検証時
のみ(§0規律)。
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

# load宛DSN(環境変数で上書き可・test-ci実行時はスーパーバイザーが指定する)
_MASSLOAD_DSN = os.environ.get(
    "LATCH_TEST_MASSLOAD_DSN",
    "postgresql://latch:latch@127.0.0.1:25432/latch",
)
_DB_URL = _MASSLOAD_DSN
if _DB_URL.startswith("postgresql://"):
    _DB_URL = "postgresql+asyncpg://" + _DB_URL[len("postgresql://") :]

_NOW_BASE = "2026-10-04T02:00:00"


def _seed_20() -> dict:
    """20件fixture(決定的・EX素材は先頭64件のためSD-001〜020は確実に在る)。"""
    from datetime import datetime

    from latch.testassets.generator import build_seed

    full = build_seed(datetime.fromisoformat(_NOW_BASE), count=70)
    want = {f"SD-{n:03d}" for n in range(1, 21)}
    return {
        "users": full["users"],
        "intents": [i for i in full["intents"] if i["id"] in want],
    }


@pytest.fixture
async def load_db_engine():
    """投入先(load DB)へのengine(db_engine fixtureのload宛対称形)。"""
    engine = create_async_engine(_DB_URL)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _cleanup_masstest(engine) -> None:
    """masstest-接頭辞の自前行を掃除(intents→usersの順・§2.2(c))。"""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE 'masstest-%')"
            )
        )
        await conn.execute(
            text("DELETE FROM users WHERE auth_subject LIKE 'masstest-%'")
        )


async def _massload_20(engine) -> dict:
    """20件fixtureをmasstest-接頭辞で投入(実施後は呼び出し側で_cleanup)。

    冒頭の自前掃除は行わない: 各試験のfinallyが掃除を担い・べき等試験は
    1回目と2回目の投入の間で掃除が入ってはならないため(supervisor修正 2026-10-04)。
    """
    from latch.core.clock import SystemClock
    from latch.loadgen import massload

    prev_prefix = massload.USER_PREFIX
    massload.USER_PREFIX = "masstest-"  # 投入元を識別させる(§2.2(c))
    try:
        return await massload.load_population(
            dsn=_MASSLOAD_DSN,
            seed=_seed_20(),
            now=SystemClock().now(),  # 実行時刻はClock経由(C2規律)
        )
    finally:
        massload.USER_PREFIX = prev_prefix


async def test_massload_inserts_active_rows_with_embedding(load_db_engine):
    """20件投入→active条件の全列照合(status・embedding・geo・expires・consent)。"""
    from datetime import UTC, datetime

    result = await _massload_20(load_db_engine)
    try:
        assert result["users_inserted"] == 100  # usersは100人固定
        assert result["intents_inserted"] == 20
        async with load_db_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT i.status, i.embedding_model,"
                        " (i.embedding IS NOT NULL) AS has_embedding,"
                        " (i.geo_center IS NOT NULL) AS has_geo,"
                        " (i.expires_at > :now) AS expires_future,"
                        " (u.consent_at IS NOT NULL) AS has_consent"
                        " FROM intents i JOIN users u ON u.id = i.user_id"
                        " WHERE u.auth_subject LIKE 'masstest-%'"
                    ),
                    {"now": datetime.now(UTC)},
                )
            ).fetchall()
        assert len(rows) == 20
        for status, model, has_embedding, has_geo, expires_future, has_consent in rows:
            assert status == "active"
            assert model == "gemini-embedding-001"
            assert has_embedding and has_geo and expires_future and has_consent
    finally:
        await _cleanup_masstest(load_db_engine)


async def test_massload_publishes_no_events(load_db_engine):
    """投入前後でmatch_events countが不変(Event発行なし・design §2.4)。"""
    async with load_db_engine.connect() as conn:
        before = (
            await conn.execute(text("SELECT count(*) FROM match_events"))
        ).scalar()
    await _massload_20(load_db_engine)
    try:
        async with load_db_engine.connect() as conn:
            after = (
                await conn.execute(text("SELECT count(*) FROM match_events"))
            ).scalar()
        assert after == before
    finally:
        await _cleanup_masstest(load_db_engine)


async def test_massload_is_idempotent(load_db_engine):
    """2回投入で行数不変(ON CONFLICT DO NOTHING・段階間の累積投入の根拠)。"""
    r1 = await _massload_20(load_db_engine)
    try:
        assert r1["intents_inserted"] == 20
        r2 = await _massload_20(load_db_engine)
        assert r2["intents_inserted"] == 0  # 同一uuid5 id→全行スキップ
        assert r2["users_inserted"] == 0
        async with load_db_engine.connect() as conn:
            (intent_count,) = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM intents i JOIN users u"
                        " ON u.id = i.user_id WHERE u.auth_subject LIKE 'masstest-%'"
                    )
                )
            ).fetchone()
        assert intent_count == 20
    finally:
        await _cleanup_masstest(load_db_engine)


async def test_massload_cleanup_after_run(load_db_engine):
    """投入行のDELETE(接頭辞で絞る)でusers/intentsとも元の件数へ戻る。"""
    async with load_db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE 'masstest-%')"
            )
        )
        await conn.execute(
            text("DELETE FROM users WHERE auth_subject LIKE 'masstest-%'")
        )
        base_users = (
            await conn.execute(
                text("SELECT count(*) FROM users WHERE auth_subject LIKE 'masstest-%'")
            )
        ).scalar()
        base_intents = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM intents i JOIN users u ON u.id = i.user_id"
                    " WHERE u.auth_subject LIKE 'masstest-%'"
                )
            )
        ).scalar()
        assert base_users == 0 and base_intents == 0
    await _massload_20(load_db_engine)
    async with load_db_engine.connect() as conn:
        mid_users = (
            await conn.execute(
                text("SELECT count(*) FROM users WHERE auth_subject LIKE 'masstest-%'")
            )
        ).scalar()
    assert mid_users == 100
    await _cleanup_masstest(load_db_engine)
    async with load_db_engine.connect() as conn:
        (after_users,) = (
            await conn.execute(
                text("SELECT count(*) FROM users WHERE auth_subject LIKE 'masstest-%'")
            )
        ).fetchone()
        (after_intents,) = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM intents i JOIN users u ON u.id = i.user_id"
                    " WHERE u.auth_subject LIKE 'masstest-%'"
                )
            )
        ).fetchone()
    assert after_users == base_users and after_intents == base_intents
