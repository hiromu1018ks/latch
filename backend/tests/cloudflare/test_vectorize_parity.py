"""T9: Vectorizeは正確検索と比較して導入する(計画 §6 T9)。

決定的比較: index未接続では retrieve_vectorized は正確検索へ
フォールバックするため、両者は完全一致しなければならない(§T9:
「初期の決定的ケースは正確検索と一致を要求する」)。
実indexでの品質・反映遅延・削除・費用比較は実配備の別承認後に行う
(採否記録: docs/testassets/results/cloudflare-vectorize-decision.yaml)。
"""

from __future__ import annotations

import json
import math
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from latch.adapters.cloudflare.vectorize import (
    VECTORIZE_DIMENSIONS,
    VectorizeRetrievalRepository,
    parse_vector_id,
    vector_id,
)
from latch.adapters.d1.database import LocalD1Database
from latch.adapters.d1.retrieval import D1RetrievalRepository
from latch.domain.time import micros
from latch.domain.types import IntentSnapshot

REPO_ROOT = None
NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def db():
    database = LocalD1Database()
    database.migrate()
    yield database
    database.close()


def _vec(*c: float) -> str:
    norm = math.sqrt(sum(x * x for x in c)) or 1.0
    return json.dumps([x / norm for x in c])


async def _seed_pair(db):
    uid_a, uid_b = str(uuid_mod.uuid4()), str(uuid_mod.uuid4())
    for uid in (uid_a, uid_b):
        await db.batch([
            db.prepare(
                "INSERT INTO users (id, display_name, profile, birth_date,"
                " auth_provider, auth_subject, consent_at, created_at,"
                " updated_at) VALUES (?, 'u', '{}', '1995-06-15', 'google',"
                " ?, ?, ?, ?)"
            ).bind(
                uid, f"s-{uid[:8]}", micros(NOW), micros(NOW), micros(NOW)
            ),
        ])
    ids = []
    for uid, emb in ((uid_a, _vec(1.0, 0.0)), (uid_b, _vec(0.7, 0.3))):
        iid = str(uuid_mod.uuid4())
        await db.batch([
            db.prepare(
                "INSERT INTO intents (id, user_id, category_primary,"
                " alcohol_involved, raw_text, structured_data, geo_lon,"
                " geo_lat, status, version, time_start, expires_at,"
                " embedding, created_at, updated_at)"
                " VALUES (?, ?, 'meal', 0, 'x', '{}', 130.5581, 31.5965,"
                " 'active', 1, ?, ?, ?, ?, ?)"
            ).bind(
                iid, uid, micros(NOW + timedelta(hours=6)),
                micros(NOW + timedelta(days=3)), emb,
                micros(NOW), micros(NOW),
            ),
        ])
        ids.append(iid)
    return ids[0], ids[1]


def _snapshot(db_row, intent_id: str) -> IntentSnapshot:
    from latch.domain.time import from_micros

    return IntentSnapshot(
        intent_id=intent_id,
        version=db_row["version"],
        user_id=db_row["user_id"],
        status="active",
        category_primary="meal",
        category_secondary=None,
        alcohol_involved=False,
        time_start=from_micros(db_row["time_start"]),
        time_end=None,
        flexibility_minutes=None,
        geo_lon=db_row["geo_lon"],
        geo_lat=db_row["geo_lat"],
        radius_m=db_row["geo_radius_m"],
        budget_max=db_row["budget_max"],
        participants_min=db_row["participants_min"],
        participants_max=db_row["participants_max"],
    )


async def test_deterministic_fallback_matches_exact(db):
    """index未接続では正確検索と完全一致(決定的ケース要件)。"""
    exact = D1RetrievalRepository(db)
    vec = VectorizeRetrievalRepository(db, exact, index=None)
    origin_id, peer_id = await _seed_pair(db)
    snap = await exact.create_retrieval_snapshot(origin_id, 1, NOW)
    origin_row = await exact._origin_row(origin_id)
    snap_obj = _snapshot(origin_row, origin_id)
    exact_result = await exact.retrieve_exact(snap_obj, snap)
    vec_result = await vec.retrieve_vectorized(snap_obj, snap)
    assert vec_result.candidates == exact_result.candidates
    assert vec_result.pass_count == exact_result.pass_count
    assert [c.intent_id for c in vec_result.candidates] == [peer_id]


async def test_vector_id_scheme():
    """vector ID = Intent ID + 内容世代(旧vectorを混ぜない)。"""
    vid = vector_id("intent-1", "emb-v1")
    assert parse_vector_id(vid) == ("intent-1", "emb-v1")
    vid2 = vector_id("intent-1", "emb-v2")
    assert vid != vid2, "内容世代が変われば別ID"


def test_dimensions_match_embedding_model():
    """768次元 = 既定Embedding(gemini-embedding-001)と同一(再利用前提)。"""
    assert VECTORIZE_DIMENSIONS == 768


def test_index_query_requires_real_binding(db):
    """実index問合せは承認後(未実装ガード)。"""
    exact = D1RetrievalRepository(db)
    vec = VectorizeRetrievalRepository(db, exact, index=object())
    import asyncio

    with pytest.raises(NotImplementedError):
        asyncio.run(vec._query_index(None, "snap"))


def test_adoption_decision_recorded():
    """採否記録が存在し、見送り(再判断条件付き)であること。"""
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "testassets"
        / "results"
        / "cloudflare-vectorize-decision.yaml"
    )
    assert path.exists(), "採否記録が無い"
    record = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert record["decision"] in ("deferred", "adopted", "rejected")
    assert record["decision"] == "deferred"
    assert record["rationale"], "根拠が空"
    assert record["revisit_conditions"], "再判断条件が空"
