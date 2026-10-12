"""D-13第二段月次匿名化バッチのintegration試験(08 §3.4・V-02・10 §4相当)。

実DB(calibration_records)で第一段済み行の変換結果・対象外行の非変換・
冪等性(再実行0件)を検証する。月末境界の駆動はSQL過去化+FakeClockの月初直撃
(暦月境界をClock操作でなく実行時刻の決定的固定で表現する — V1-P4知見と同型)。
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock
from latch.worker.anonymize import AnonymizeJob

pytestmark = pytest.mark.integration

PREFIX = "v1p5anon-"

# JST 2026-11-01 00:05(月初0時の発火直後を表現)
NOW = datetime(2026, 10, 31, 15, 5, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def _purge(db_engine):
    yield
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM calibration_records WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": ROW_IDS[:]},
        )
        await conn.execute(
            text("DELETE FROM latches WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": LATCH_IDS[:]},
        )


ROW_IDS: list[str] = []
LATCH_IDS: list[str] = []


async def _insert_row(
    db_engine,
    *,
    first_stage: bool,
    responses: list[dict],
    snapshot: dict,
    created_at: datetime,
) -> str:
    """calibration_records行を直接INSERT(第一段済み=ID系NULL)。戻り=row id。"""
    row_id = str(uuid_mod.uuid4())
    async with db_engine.begin() as conn:
        if not first_stage:
            # 未第一段行はFKのためlatches行も最小INSERTする
            latch_id = str(uuid_mod.uuid4())
            await conn.execute(
                text(
                    "INSERT INTO latches (id, intent_ids, group_candidate_id, proposal,"
                    " score, status, response_deadline, expires_at, created_at)"
                    " VALUES (CAST(:id AS uuid), ARRAY[gen_random_uuid(),"
                    " gen_random_uuid()]::uuid[], NULL,"
                    " CAST(:proposal AS jsonb), 0.85, 'expired',"
                    " now() - interval '1 day', now() - interval '1 day',"
                    " now() - interval '2 day')"
                ),
                {"id": latch_id, "proposal": json.dumps({"headcount": 2})},
            )
            LATCH_IDS.append(latch_id)
            await conn.execute(
                text(
                    "INSERT INTO calibration_records"
                    " (id, latch_id, intent_ids, prediction, proposal_snapshot,"
                    "  actual_responses, matched, created_at, updated_at)"
                    " VALUES (CAST(:id AS uuid), CAST(:latch AS uuid),"
                    " ARRAY[gen_random_uuid(), gen_random_uuid()]::uuid[],"
                    " CAST(:prediction AS jsonb), CAST(:snapshot AS jsonb),"
                    " CAST(:responses AS jsonb), true,"
                    " CAST(:created AS timestamptz), CAST(:created AS timestamptz))"
                ),
                {
                    "id": row_id,
                    "latch": latch_id,
                    "prediction": json.dumps({"segment": "lexical"}),
                    "snapshot": json.dumps(snapshot, ensure_ascii=False),
                    "responses": json.dumps(responses, ensure_ascii=False),
                    "created": created_at,
                },
            )
        else:
            await conn.execute(
                text(
                    "INSERT INTO calibration_records"
                    " (id, latch_id, intent_ids, prediction, proposal_snapshot,"
                    "  actual_responses, matched, created_at, updated_at)"
                    " VALUES (CAST(:id AS uuid), NULL, NULL,"
                    " CAST(:prediction AS jsonb), CAST(:snapshot AS jsonb),"
                    " CAST(:responses AS jsonb), true,"
                    " CAST(:created AS timestamptz), CAST(:created AS timestamptz))"
                ),
                {
                    "id": row_id,
                    "prediction": json.dumps({"segment": "semantic"}),
                    "snapshot": json.dumps(snapshot, ensure_ascii=False),
                    "responses": json.dumps(responses, ensure_ascii=False),
                    "created": created_at,
                },
            )
    ROW_IDS.append(row_id)
    return row_id


async def _fetch(db_engine, row_id: str) -> dict:
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT actual_responses, proposal_snapshot, anonymized_at,"
                    " created_at FROM calibration_records"
                    " WHERE id = CAST(:id AS uuid)"
                ),
                {"id": row_id},
            )
        ).first()
    assert row is not None
    return {
        "responses": row[0],
        "snapshot": row[1],
        "anonymized_at": row[2],
        "created_at": row[3],
    }


_SNAPSHOT = {
    "time_summary": "2026-09-20 20:00",
    "area_name": "鹿児島市天文館",
    "headcount": 3,
    "category_primary": "meal",
    "category_secondary": "焼肉",
    "budget": 5000,
    "match_level": "medium",
}
_RESPONSES = [
    {"user_id": "u1", "value": "yes", "answered_at": "2026-09-20T21:11:22+09:00"},
    {"value": "defer", "answered_at": "2026-09-20T22:00:00+09:00"},
]


async def test_second_stage_transforms_first_stage_rows(db_engine):
    row = await _insert_row(
        db_engine,
        first_stage=True,
        responses=_RESPONSES,
        snapshot=_SNAPSHOT,
        created_at=datetime(2026, 9, 20, 12, 34, 56, tzinfo=UTC),
    )
    job = AnonymizeJob(engine=db_engine, clock=FakeClock(NOW))
    n = await job.run_once()
    assert n >= 1
    after = await _fetch(db_engine, row)
    assert after["anonymized_at"] is not None
    assert after["responses"] == [
        {"value": "yes", "answered_date": "2026-09-20"},
        {"value": "defer", "answered_date": "2026-09-20"},
    ]
    assert after["snapshot"] == {
        "category_primary": "meal",
        "budget": 5000,
        "match_level": "medium",
        "time_of_day": "夜",
        "area_municipality": "鹿児島市",
        "headcount_bin": "2-3人",
    }
    # created_atのJST日丸め(2026-09-20T12:34Z=JST 9/20 21:34→9/20 0時JST=9/19 15:00Z)
    assert after["created_at"].isoformat().startswith("2026-09-19T15:00:00+00:00")


async def test_second_stage_skips_unanonymized_and_already_done(db_engine):
    """第一段未実施(latch_idあり)行と変換済み行は対象外。"""
    untouched = await _insert_row(
        db_engine,
        first_stage=False,
        responses=_RESPONSES,
        snapshot=_SNAPSHOT,
        created_at=datetime(2026, 9, 20, 12, 34, 56, tzinfo=UTC),
    )
    done = await _insert_row(
        db_engine,
        first_stage=True,
        responses=[{"value": "yes", "answered_date": "2026-08-01"}],
        snapshot={"category_primary": "meal"},
        created_at=datetime(2026, 8, 1, 1, 0, 0, tzinfo=UTC),
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE calibration_records SET anonymized_at = now()"
                " WHERE id = CAST(:id AS uuid)"
            ),
            {"id": done},
        )

    job = AnonymizeJob(engine=db_engine, clock=FakeClock(NOW))
    n = await job.run_once()
    assert n == 0  # 対象外のみ

    kept = await _fetch(db_engine, untouched)
    assert kept["anonymized_at"] is None
    assert kept["responses"][0].get("user_id") == "u1"  # 変換されていない


async def test_second_stage_idempotent_rerun(db_engine):
    row = await _insert_row(
        db_engine,
        first_stage=True,
        responses=_RESPONSES,
        snapshot=_SNAPSHOT,
        created_at=datetime(2026, 9, 20, 12, 34, 56, tzinfo=UTC),
    )
    job = AnonymizeJob(engine=db_engine, clock=FakeClock(NOW))
    first = await job.run_once()
    second = await job.run_once()
    assert first >= 1
    assert second == 0  # anonymized_atガードで再変換なし
    after = await _fetch(db_engine, row)
    assert after["anonymized_at"] is not None
