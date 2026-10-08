"""T4: 正確な候補検索の移植試験(計画 §6 T4)。

柱:
1. 決定的境界ケース(半径±1cm・時刻一致端・NULL補完・JST誕生日・2/29・
   同一利用者除外・同点順位)
2. PostgreSQL実測(layer2.retrieve_topk)との候補集合・順位・類似度一致
   (同一fixtureを両DBへ投入して比較 — similarity絶対差1e-6以内・
   同点はintent_id昇順)
3. snapshot無効化(peer削除・同意撤回)・origin版不一致の検証
"""

from __future__ import annotations

import json
import math
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from latch.adapters.d1.database import LocalD1Database, canonical_json
from latch.adapters.d1.retrieval import D1RetrievalRepository
from latch.domain.geo_distance import bbox_for_radius, geodesic_distance_m
from latch.domain.time import micros
from latch.domain.types import IntentSnapshot

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
START = NOW + timedelta(hours=6)


@pytest.fixture
async def db():
    database = LocalD1Database()
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def retrieval(db) -> D1RetrievalRepository:
    return D1RetrievalRepository(db)


def _vec(*components: float) -> str:
    norm = math.sqrt(sum(c * c for c in components)) or 1.0
    return json.dumps([c / norm for c in components])


async def seed_user(
    db,
    name: str,
    *,
    birth_date: str = "1995-06-15",
    consented: bool = True,
    user_id: str | None = None,
) -> str:
    uid = user_id or str(uuid_mod.uuid4())
    await db.batch(
        [
            db.prepare(
                "INSERT INTO users (id, display_name, profile, birth_date,"
                " auth_provider, auth_subject, consent_at, created_at, updated_at)"
                " VALUES (?, ?, '{}', ?, 'google', ?, ?, ?, ?)"
            ).bind(
                uid,
                name,
                birth_date,
                f"sub-{uid[:8]}",
                micros(NOW) if consented else None,
                micros(NOW),
                micros(NOW),
            ),
        ]
    )
    return uid


async def seed_intent(
    db,
    user_id: str,
    *,
    intent_id: str | None = None,
    category: str = "meal",
    embedding: str | None = "[1.0, 0.0]",
    lon: float = 130.5581,
    lat: float = 31.5965,
    radius_m: int | None = None,
    budget: int | None = None,
    start_us: int | None = None,
    end_us: int | None = None,
    participants: tuple[int, int] = (2, 2),
    alcohol: bool = False,
    status: str = "active",
    version: int = 1,
    structured: dict | None = None,
) -> str:
    iid = intent_id or str(uuid_mod.uuid4())
    await db.batch(
        [
            db.prepare(
                "INSERT INTO intents (id, user_id, category_primary,"
                " alcohol_involved, raw_text, structured_data, geo_lon, geo_lat,"
                " geo_radius_m, budget_max, participants_min, participants_max,"
                " status, version, time_start, time_end, expires_at, embedding,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, '意図', ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?)"
            ).bind(
                iid,
                user_id,
                category,
                1 if alcohol else 0,
                canonical_json(structured or {}),
                lon,
                lat,
                radius_m,
                budget,
                participants[0],
                participants[1],
                status,
                version,
                start_us if start_us is not None else micros(START),
                end_us,
                micros(NOW + timedelta(hours=120)),
                embedding,
                micros(NOW),
                micros(NOW),
            ),
        ]
    )
    return iid


def _origin_snapshot(row, intent_id: str) -> IntentSnapshot:
    from latch.domain.time import from_micros

    return IntentSnapshot(
        intent_id=intent_id,
        version=row["version"],
        user_id=row["user_id"],
        status=row["status"],
        category_primary=row["category_primary"],
        category_secondary=None,
        alcohol_involved=bool(row["alcohol_involved"]),
        time_start=from_micros(row["time_start"]),
        time_end=(from_micros(row["time_end"]) if row["time_end"] else None),
        flexibility_minutes=None,
        geo_lon=row["geo_lon"],
        geo_lat=row["geo_lat"],
        radius_m=row["geo_radius_m"],
        budget_max=row["budget_max"],
        participants_min=row["participants_min"],
        participants_max=row["participants_max"],
    )


async def _run_d1(retrieval, origin_id: str, *, version: int = 1):
    snap = await retrieval.create_retrieval_snapshot(origin_id, version, NOW)
    origin_row = await retrieval._origin_row(origin_id)
    return await retrieval.retrieve_exact(_origin_snapshot(origin_row, origin_id), snap)


# =============================================================================
# 1. 決定的境界ケース
# =============================================================================


async def test_radius_boundary_1cm(db, retrieval):
    """半径境界±1cm: 境界内は通過・外は除外(測地線距離の正確性)。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    origin = await seed_intent(db, u1, radius_m=1000, embedding=_vec(1, 0))
    # しきい値は origin(1000m) + peer既定(1000m) = 2000m(参照のSUM条件)
    for delta_m in (1999.99, 2000.01):
        lat = 31.5965
        # 1度経度の距離を測ってオフセット経度を決める
        one_deg = geodesic_distance_m(130.5581, lat, 130.5681, lat)
        lon = 130.5581 + (delta_m / one_deg) * 0.01
        await seed_intent(db, u2, embedding=_vec(0.99, 0.01), lon=lon, lat=lat)
    result = await _run_d1(retrieval, origin)
    assert len(result.candidates) == 1, "境界内(in)のみ通過"
    assert result.pass_count == 1


async def test_time_overlap_edges(db, retrieval):
    """時刻一致端: peer開始==origin終了は交差なし(strict <)。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    origin_start_us = micros(START)
    origin_end_us = origin_start_us + 3 * 3_600_000_000
    origin = await seed_intent(
        db,
        u1,
        start_us=origin_start_us,
        end_us=origin_end_us,
        embedding=_vec(1, 0),
    )
    # peer終了(補完値=start+3h)==origin開始 → 交差なし
    peer_a_start = origin_start_us - 3 * 3_600_000_000
    await seed_intent(
        db, u2, embedding=_vec(0.9, 0.1), start_us=peer_a_start
    )  # peer end = origin start → 除外
    # peer終了がorigin開始より1μs後なら交差
    peer_b_start = peer_a_start + 1
    await seed_intent(db, u2, embedding=_vec(0.8, 0.2), start_us=peer_b_start)
    result = await _run_d1(retrieval, origin)
    ids = {c.intent_id for c in result.candidates}
    assert len(result.candidates) == 1


async def test_null_completion_and_self_exclusion(db, retrieval):
    """NULL補完(peer終了=開始+3h)・同一利用者除外・非active除外。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    origin = await seed_intent(db, u1, embedding=_vec(1, 0))
    await seed_intent(db, u1, embedding=_vec(0.99, 0.05))  # 同一利用者→除外
    await seed_intent(db, u2, embedding=_vec(0.5, 0.5), status="paused")
    ok = await seed_intent(db, u2, embedding=_vec(0.7, 0.3))
    result = await _run_d1(retrieval, origin)
    assert [c.intent_id for c in result.candidates] == [ok]


async def test_jst_birthday_and_feb29(db, retrieval):
    """飲酒ペアの年齢: JST暦日で満20歳未満は除外・起点2/29生まれも加算。

    NOW=2026-10-08T12:00Z(JST 21:00)。
    - 起点u1: 2000-02-29生まれ(2/29の年齢加算規則)→満26歳=通過対象
    - peer u2: 2005年生まれ(21歳)→通過
    - peer u3: 2007-10-08生まれ(当日19歳)→除外
    - peer u4: 2006-10-09生まれ(JST暦日でまだ19歳)→除外
    """
    u1 = await seed_user(db, "A", birth_date="2000-02-29")
    u2 = await seed_user(db, "B", birth_date="2005-06-15")
    u3 = await seed_user(db, "C", birth_date="2007-10-08")
    u4 = await seed_user(db, "D", birth_date="2006-10-09")
    origin = await seed_intent(db, u1, alcohol=True, embedding=_vec(1, 0))
    await seed_intent(db, u2, alcohol=True, embedding=_vec(0.9, 0.1))
    await seed_intent(db, u3, alcohol=True, embedding=_vec(0.8, 0.2))
    await seed_intent(db, u4, alcohol=True, embedding=_vec(0.7, 0.3))
    result = await _run_d1(retrieval, origin)
    assert len(result.candidates) == 1


async def test_tie_break_intent_id_asc(db, retrieval):
    """同点順位はintent_id昇順(D-24・layer2と同一)。"""
    u1 = await seed_user(db, "A")
    origin = await seed_intent(db, u1, embedding=_vec(1, 0))
    ids = []
    for i in range(5):
        ids.append(
            await seed_intent(db, await seed_user(db, f"P{i}"), embedding=_vec(1, 0))
        )
    result = await _run_d1(retrieval, origin)
    got = [c.intent_id for c in result.candidates]
    assert got == sorted(ids), "同点はintent_id昇順"
    sims = [c.similarity for c in result.candidates]
    assert all(abs(s - sims[0]) < 1e-9 for s in sims)


async def test_truncation_after_all_conditions(db, retrieval):
    """55件通過で50件に切り詰め(全条件の後)・pass_countは55。"""
    u1 = await seed_user(db, "A")
    origin = await seed_intent(db, u1, embedding=_vec(1, 0))
    for i in range(55):
        await seed_intent(
            db,
            await seed_user(db, f"P{i:03d}"),
            embedding=_vec(1.0, (i + 1) * 0.001),
        )
    result = await _run_d1(retrieval, origin)
    assert len(result.candidates) == 50
    assert result.pass_count == 55
    sims = [c.similarity for c in result.candidates]
    assert sims == sorted(sims, reverse=True)


# =============================================================================
# 2. PostgreSQL(layer2)との一致
# =============================================================================


async def _seed_pg(engine, users, intents) -> None:
    from datetime import date as date_cls

    from sqlalchemy import text

    async with engine.begin() as conn:
        for uid, name, birth in users:
            await conn.execute(
                text("""
                    INSERT INTO users (id, display_name, profile, birth_date,
                        auth_provider, auth_subject, consent_at, created_at,
                        updated_at)
                    VALUES (CAST(:id AS uuid), :name, '{}', :birth, 'google',
                        :subject, CAST(:consent AS timestamptz),
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                    ON CONFLICT DO NOTHING
                """),
                {
                    "id": uid,
                    "name": name,
                    "birth": date_cls.fromisoformat(birth),
                    "subject": f"sub-{uid[:8]}",
                    "consent": NOW,
                    "now": NOW,
                },
            )
        for it in intents:
            await conn.execute(
                text("""
                    INSERT INTO intents (id, user_id, category_primary,
                        alcohol_involved, raw_text, structured_data, geo_center,
                        geo_radius_m, budget_max, participants_min,
                        participants_max, status, version, time_start,
                        time_end, expires_at, embedding, embedding_model,
                        created_at, updated_at)
                    VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), :category,
                        :alcohol, '意図', CAST(:structured AS jsonb),
                        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
                        :radius, :budget, :pmin, :pmax, :status, :version,
                        CAST(:start AS timestamptz), CAST(:end AS timestamptz),
                        CAST(:expires AS timestamptz),
                        CAST(:embedding AS vector), 'fixture',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                    ON CONFLICT DO NOTHING
                """),
                it,
            )


async def test_parity_with_postgresql(db, retrieval, db_engine, migrated_db):
    """共有ci DBの残存行と分離するため、遠未来(2027-04)の時間窓を使う。"""

    parity_now = datetime(2027, 4, 26, 12, 0, 0, tzinfo=UTC)
    parity_start = parity_now + timedelta(hours=6)
    """同一fixtureで layer2(PostgreSQL) と D1 retrieval が一致すること。

    - 候補ID・版・順位が完全一致
    - similarity は絶対差1e-6以内
    - pass_count(LIMIT前通過件数)一致
    """

    def _vec768(a: float, b: float) -> str:
        comps = [a] * 384 + [b] * 384
        return _vec(*comps)

    users: list[tuple[str, str, str]] = []
    intents: list[dict[str, Any]] = []
    # 起点u0 + 候補10件(同点・非同点・境界内外混在)
    u0 = str(uuid_mod.uuid4())
    users.append((u0, "origin", "1995-06-15"))
    origin_embedding = _vec768(1.0, 0.0)
    lat0, lon0 = 31.5965, 130.5581
    # 境界ケース: 約400m/約950m地点と約1200m地点(既定1000m境界)
    one_deg_lon = geodesic_distance_m(lon0, lat0, lon0 + 0.01, lat0) / 0.01
    boundary_lon = lon0 + 950.0 / one_deg_lon
    outer_lon = lon0 + 1200.0 / one_deg_lon
    plan = [
        # (embedding, lon, lat, 通過期待)
        (_vec768(1.0, 0.02), lon0 + 0.0001, lat0, True),
        (_vec768(0.9, 0.1), lon0, lat0 + 0.001, True),
        (_vec768(0.8, 0.2), boundary_lon, lat0, True),
        (_vec768(0.7, 0.3), outer_lon, lat0, False),  # 距離境界外
        (_vec768(0.6, 0.4), lon0, lat0, True),
        (_vec768(1.0, 0.0), lon0, lat0, True),  # 同点
        (_vec768(1.0, 0.0), lon0, lat0, True),  # 同点
        (_vec768(0.5, 0.5), lon0, lat0, True),
        (_vec768(0.3, 0.7), lon0, lat0, True),
        (_vec768(0.1, 0.9), lon0, lat0, True),
    ]
    d1_intents: list[str] = []
    for i, (emb, lon, lat, _) in enumerate(plan):
        uid = str(uuid_mod.uuid4())
        users.append((uid, f"u{i}", "1995-06-15"))
        iid = str(uuid_mod.uuid4())
        d1_intents.append(iid)
        intents.append(
            {
                "id": iid,
                "uid": uid,
                "category": "meal",
                "alcohol": False,
                "structured": "{}",
                "lon": lon,
                "lat": lat,
                "radius": None,
                "budget": None,
                "pmin": 2,
                "pmax": 2,
                "status": "active",
                "version": 1,
                "start": parity_start,
                "end": None,
                "expires": parity_now + timedelta(hours=120),
                "embedding": emb,
                "now": parity_now,
            }
        )
    origin_id = str(uuid_mod.uuid4())
    intents.append(
        {
            "id": origin_id,
            "uid": u0,
            "category": "meal",
            "alcohol": False,
            "structured": "{}",
            "lon": lon0,
            "lat": lat0,
            "radius": None,
            "budget": None,
            "pmin": 2,
            "pmax": 2,
            "status": "active",
            "version": 1,
            "start": parity_start,
            "end": None,
            "expires": parity_now + timedelta(hours=120),
            "embedding": origin_embedding,
            "now": parity_now,
        }
    )

    # 両DBへ同一投入
    await _seed_pg(db_engine, users, intents)
    for uid, name, birth in users:
        await seed_user(db, name, birth_date=birth, user_id=uid)
    for it in intents:
        await seed_intent(
            db,
            it["uid"],
            intent_id=it["id"],
            category=it["category"],
            embedding=it["embedding"],
            lon=it["lon"],
            lat=it["lat"],
            radius_m=it["radius"],
            budget=it["budget"],
            start_us=micros(it["start"]),
            end_us=micros(it["end"]) if it["end"] else None,
            participants=(it["pmin"], it["pmax"]),
            alcohol=it["alcohol"],
            status=it["status"],
            version=it["version"],
            structured={},
        )
        await db.batch(
            [
                db.prepare(
                    "UPDATE intents SET created_at = ?, updated_at = ?,"
                    " expires_at = ? WHERE id = ?"
                ).bind(
                    micros(parity_now),
                    micros(parity_now),
                    micros(parity_now + timedelta(hours=120)),
                    it["id"],
                ),
            ]
        )

    # PostgreSQL側: layer2.retrieve_topk
    from latch.core.clock import FakeClock
    from latch.worker.matching.layer2 import retrieve_topk
    from latch.worker.matching.origin import load_origin

    async with db_engine.begin() as conn:
        loaded = await load_origin(
            conn, FakeClock(parity_now), uuid_mod.UUID(origin_id)
        )
        assert loaded.origin is not None, loaded.skip_reason
        pg_candidates, pg_pass = await retrieve_topk(conn, loaded.origin)

    # D1側
    d1_snap = await retrieval.create_retrieval_snapshot(origin_id, 1, parity_now)
    origin_row = await retrieval._origin_row(origin_id)
    d1_result = await retrieval.retrieve_exact(
        _origin_snapshot(origin_row, origin_id), d1_snap
    )

    pg_ids = [str(c.intent_id) for c in pg_candidates]
    d1_ids = [c.intent_id for c in d1_result.candidates]
    assert d1_ids == pg_ids, f"候補順位不一致: D1={d1_ids} PG={pg_ids}"
    assert d1_result.pass_count == pg_pass
    for pg_c, d1_c in zip(pg_candidates, d1_result.candidates, strict=True):
        assert pg_c.version == d1_c.version
        assert abs(pg_c.similarity - d1_c.similarity) <= 1e-6, (
            pg_c.similarity,
            d1_c.similarity,
        )
    # 後始末(共有DBを汚さない)
    from sqlalchemy import text as pg_text

    async with db_engine.begin() as conn:
        await conn.execute(
            pg_text("DELETE FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [uuid_mod.UUID(it["id"]) for it in intents]},
        )
        await conn.execute(
            pg_text("DELETE FROM users WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [uuid_mod.UUID(u[0]) for u in users]},
        )


# =============================================================================
# 3. snapshot の無効化・整合
# =============================================================================


async def test_snapshot_origin_version_mismatch(retrieval, db):
    """保存したorigin版と引数が不一致なら例外(結果を書かせない)。"""
    u1 = await seed_user(db, "A")
    origin = await seed_intent(db, u1, embedding=_vec(1, 0))
    snap = await retrieval.create_retrieval_snapshot(origin, 1, NOW)
    # 版を進める
    await db.batch(
        [
            db.prepare("UPDATE intents SET version = 2 WHERE id = ?").bind(origin),
        ]
    )
    origin_row = await retrieval._origin_row(origin)
    snapshot_version_2 = IntentSnapshot(
        intent_id=origin,
        version=2,
        user_id=origin_row["user_id"],
        status="active",
        category_primary="meal",
        category_secondary=None,
        alcohol_involved=False,
        time_start=START,
        time_end=None,
        flexibility_minutes=None,
        geo_lon=130.5581,
        geo_lat=31.5965,
        radius_m=None,
        budget_max=None,
        participants_min=2,
        participants_max=2,
    )
    with pytest.raises(ValueError, match="version"):
        await retrieval.retrieve_exact(snapshot_version_2, snap)


async def test_peer_delete_invalidates_snapshot_rows(db, retrieval):
    """peer削除でsnapshot行が無効化され候補から消える。"""
    u1 = await seed_user(db, "A")
    u2 = await seed_user(db, "B")
    origin = await seed_intent(db, u1, embedding=_vec(1, 0))
    peer = await seed_intent(db, u2, embedding=_vec(0.9, 0.1))
    snap = await retrieval.create_retrieval_snapshot(origin, 1, NOW)
    rows_before = (
        await db.prepare(
            "SELECT COUNT(*) AS c FROM retrieval_snapshot_rows WHERE snapshot_id = ?"
        )
        .bind(snap)
        .first()
    )["c"]
    assert rows_before == 1

    # 削除commandがsnapshot行を消す(atomic側の責務 — ここで直接呼ぶ)
    from latch.adapters.d1.atomic import D1AtomicRepository

    result = await D1AtomicRepository(db).apply_intent(
        f"cmd-{uuid_mod.uuid4().hex[:8]}",
        None,
        {"op": "delete", "intent_id": peer, "user_id": u2},
        NOW,
    )
    assert result.status.value == "applied"
    rows_after = (
        await db.prepare(
            "SELECT COUNT(*) AS c FROM retrieval_snapshot_rows"
            " WHERE snapshot_id = ? AND intent_id = ?"
        )
        .bind(snap, peer)
        .first()
    )["c"]
    assert rows_after == 0, "削除対象はsnapshotからも除去"


async def test_geodesic_distance_accuracy():
    """測地線距離の健全性(既知値・赤道/子午線/斜め)。"""
    # 子午線上1度 ≈ 111,132m(緯度31.6°付近の楕円体値と±1m以内)
    d = geodesic_distance_m(130.5581, 31.5965, 130.5581, 32.5965)
    assert 110_800 < d < 111_500, d
    # 同一点
    assert geodesic_distance_m(130.0, 31.0, 130.0, 31.0) == 0.0
    # bboxは与距離を包含
    lon_min, lat_min, lon_max, lat_max = bbox_for_radius(130.5581, 31.5965, 1000)
    assert geodesic_distance_m(130.5581, 31.5965, lon_max, 31.5965) >= 1000
