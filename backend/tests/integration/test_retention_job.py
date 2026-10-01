"""RetentionJobのintegration試験(M3 ws-6 design §4.2)。

実DB(compose常設)。RetentionJobを直接構築しrun_onceを呼ぶ(worker常設は
test-ciで停止中)。時間値はSystemClock相対(30日境界のタイムボム回避)。
"""

import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock
from latch.worker.retention import RetentionJob

pytestmark = pytest.mark.integration

SUBJECT_PREFIX = "m3ws6-"


@pytest.fixture
async def field(db_engine):
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        # FK順: latch_status_events(latches FK) → latches(group FK元) →
        # group_candidates → match_candidates(intents FK) → intents → users
        await conn.execute(
            text(
                "DELETE FROM latch_status_events WHERE latch_id IN (SELECT id FROM"
                " latches WHERE intent_ids && ARRAY(SELECT id FROM intents WHERE"
                " user_id IN (SELECT id FROM users WHERE auth_subject LIKE :p)))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM latches WHERE intent_ids && ARRAY("
                "SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
                "SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)


async def _user_row(db_engine, prefix: str, name: str) -> str:
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "INSERT INTO users (display_name, profile, birth_date,"
                " auth_provider, auth_subject, created_at, updated_at)"
                " VALUES (:n, '{}', DATE '1990-04-01', 'google', :s,"
                " CAST(:now AS timestamptz), CAST(:now AS timestamptz))"
                " RETURNING id"
            ),
            {
                "n": name,
                "s": f"{prefix}{uuid_mod.uuid4().hex[:8]}",
                "now": now,
            },
        )
    return str(res.first()[0])


async def _intent_row(db_engine, user_id: str) -> str:
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "INSERT INTO intents (user_id, category_primary,"
                " alcohol_involved, raw_text, structured_data, status, version,"
                " time_start, expires_at, created_at, updated_at)"
                " VALUES (CAST(:u AS uuid), 'meal', false, 'retention', '{}',"
                " 'expired', 1, CAST(:t AS timestamptz),"
                " CAST(:e AS timestamptz), CAST(:now AS timestamptz),"
                " CAST(:now AS timestamptz)) RETURNING id"
            ),
            {
                "u": uuid_mod.UUID(user_id),
                "t": now + timedelta(hours=2),
                "e": now + timedelta(hours=5),
                "now": now,
            },
        )
    return str(res.first()[0])


async def _candidate(db_engine, a: str, b: str, *, aged: float) -> None:
    """match_candidates行(updated_atを日数指定で経過させる)。"""
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO match_candidates (intent_a_id, intent_b_id,"
                " intent_a_version, intent_b_version, status, created_at,"
                " updated_at) VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 1, 1,"
                " :status, CAST(:c AS timestamptz), CAST(:u AS timestamptz))"
            ),
            {
                "a": uuid_mod.UUID(min(a, b)),
                "b": uuid_mod.UUID(max(a, b)),
                "status": "pending",  # pendingも削除対象であることの検証
                "c": now - timedelta(days=aged),
                "u": now - timedelta(days=aged),
            },
        )


_PAIR_COUNT = text("""
    SELECT count(*) FROM match_candidates WHERE
     (intent_a_id = CAST(:a AS uuid) AND intent_b_id = CAST(:b AS uuid))
     OR (intent_a_id = CAST(:b AS uuid) AND intent_b_id = CAST(:a AS uuid))
""")


async def test_1_retention_deletes_aged_all_statuses_keeps_recent(db_engine, field):
    """30日+1秒=削除・29日=残る・pendingも削除(design §2.5)。"""
    uid = await _user_row(db_engine, field, "retention1")
    i1, i2, i3 = (await _intent_row(db_engine, uid) for _ in range(3))
    await _candidate(db_engine, i1, i2, aged=30 + 1 / 86400)  # 30日+1秒
    await _candidate(db_engine, i2, i3, aged=29.0)  # 29日
    job = RetentionJob(engine=db_engine, clock=SystemClock())
    groups, matches = await job.run_once()
    assert matches >= 1
    async with db_engine.connect() as conn:
        n_aged = (
            await conn.execute(
                _PAIR_COUNT, {"a": uuid_mod.UUID(i1), "b": uuid_mod.UUID(i2)}
            )
        ).scalar()
        n_recent = (
            await conn.execute(
                _PAIR_COUNT, {"a": uuid_mod.UUID(i2), "b": uuid_mod.UUID(i3)}
            )
        ).scalar()
    assert n_aged == 0  # 30日+1秒は削除
    assert n_recent == 1  # 29日は残る


async def test_2_retention_detaches_latches_fk(db_engine, field):
    """group削除前にlatches.group_candidate_idをNULL化・latches行は残る。"""
    uid = await _user_row(db_engine, field, "retention2")
    ids = [await _intent_row(db_engine, uid) for _ in range(3)]
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        gid = str(
            (
                await conn.execute(
                    text(
                        "INSERT INTO group_candidates (intent_ids, status,"
                        " created_at, updated_at) VALUES"
                        " (CAST(:ids AS uuid[]), 'candidate',"
                        " CAST(:c AS timestamptz), CAST(:u AS timestamptz))"
                        " RETURNING id"
                    ),
                    {
                        "ids": [uuid_mod.UUID(i) for i in sorted(ids, key=str)],
                        "c": now - timedelta(days=31),
                        "u": now - timedelta(days=31),
                    },
                )
            ).first()[0]
        )
        lid = str(
            (
                await conn.execute(
                    text(
                        "INSERT INTO latches (intent_ids, group_candidate_id, proposal,"
                        " score, status, response_deadline, expires_at, created_at)"
                        " VALUES (CAST(:ids AS uuid[]), CAST(:gid AS uuid), '{}', 0.5,"
                        " 'cancelled', CAST(:d AS timestamptz),"
                        " CAST(:e AS timestamptz), CAST(:n AS timestamptz))"
                        " RETURNING id"
                    ),
                    {
                        "ids": [uuid_mod.UUID(i) for i in ids],
                        "gid": uuid_mod.UUID(gid),
                        "d": now + timedelta(hours=1),
                        "e": now + timedelta(hours=2),
                        "n": now,
                    },
                )
            ).first()[0]
        )
    job = RetentionJob(engine=db_engine, clock=SystemClock())
    await job.run_once()
    async with db_engine.connect() as conn:
        g_count = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM group_candidates WHERE id = CAST(:g AS uuid)"
                ),
                {"g": uuid_mod.UUID(gid)},
            )
        ).scalar()
        latch = (
            await conn.execute(
                text(
                    "SELECT status, group_candidate_id FROM latches WHERE"
                    " id = CAST(:l AS uuid)"
                ),
                {"l": uuid_mod.UUID(lid)},
            )
        ).first()
    assert g_count == 0
    assert latch is not None and latch[0] == "cancelled"  # latches行は残る
    assert latch[1] is None  # FK解消
