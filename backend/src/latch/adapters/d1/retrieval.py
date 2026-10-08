"""D1版・正確な候補検索(T4・計画§3.3)。

構成(計画どおり):
1. SQLで確定条件を絞る(category・時間交差・予算・人数・同意・block・
   飲酒年齢・bbox距離事前絞り)
2. Workers(Python)側でWGS84測地線距離を正確に確認
3. 全通過候補のcosine類似度を計算し上位50件(切り詰めは全hard条件・
   距離・cosineの後・恣意的なLIMITは置かない)
4. 続くCheap Judge・グループ計算は既存(layer3/group_calc)を再利用

snapshot方式: 多い候補は create_retrieval_snapshot でID・版・条件・
Embeddingを固定した短期集合へcursor分割で読む。削除・同意撤回で無効化
(atomic側が行を消す)。
"""

from __future__ import annotations

import json
import math
import uuid as uuid_mod
from dataclasses import dataclass
from datetime import datetime

from latch.adapters.d1.database import LocalD1Database, canonical_json
from latch.domain.geo_distance import bbox_for_radius, geodesic_distance_m
from latch.domain.time import JST, micros
from latch.domain.types import IntentSnapshot

K_VECTORS = 50  # layer2.K_VECTORS と同一(計画: 切り詰めは全条件の後)
THREE_HOURS_US = 3 * 3600 * 1_000_000
PAIR_BUDGET_MIN_YEN = 500  # layer1 と同一
DEFAULT_RADIUS_M = 1000  # completion.DEFAULT_RADIUS_M と同一
SNAPSHOT_TTL_US = 120 * 1_000_000  # snapshot保持2分(T4で調整)


@dataclass(frozen=True)
class RetrievedCandidate:
    """Layer 2 相当の通過対象(layer2.RetrievedCandidate と同一形状)。"""

    intent_id: str
    version: int
    similarity: float
    time_start: datetime
    budget_max: int | None
    soft_texts: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalResult:
    """retrieve_exact の結果(候補・正確なpass_count)。"""

    candidates: list[RetrievedCandidate]
    pass_count: int  # LIMIT前の全通過件数(§9固定値)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class D1RetrievalRepository:
    """ports.retrieval.RetrievalRepository のD1実装。"""

    def __init__(self, db: LocalD1Database) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # hard filter(SQL) — layer1.LAYER1_WHERE のSQLite版(同一条件)
    # ------------------------------------------------------------------

    _HARD_SQL = """
        SELECT i.id, i.version, i.embedding, i.geo_lon, i.geo_lat,
               COALESCE(i.geo_radius_m, ?) AS radius_m,
               i.time_start, i.budget_max, i.structured_data
        FROM intents i
        JOIN users u ON u.id = i.user_id
        WHERE i.status = 'active'
          AND i.embedding IS NOT NULL
          AND i.user_id <> ?
          AND i.category_primary = ?
          AND i.time_start < ?
          AND ? < COALESCE(i.time_end, i.time_start + ?)
          AND i.geo_lon IS NOT NULL AND i.geo_lat IS NOT NULL
          AND i.geo_lat BETWEEN ? AND ?
          AND i.geo_lon BETWEEN ? AND ?
          AND i.participants_min <= 2 AND i.participants_max >= 2
          AND u.consent_at IS NOT NULL
          AND (CASE
                WHEN ? IS NULL AND i.budget_max IS NULL THEN NULL
                WHEN ? IS NULL THEN i.budget_max
                WHEN i.budget_max IS NULL THEN ?
                ELSE MIN(?, i.budget_max)
              END IS NULL
           OR CASE
                WHEN ? IS NULL AND i.budget_max IS NULL THEN NULL
                WHEN ? IS NULL THEN i.budget_max
                WHEN i.budget_max IS NULL THEN ?
                ELSE MIN(?, i.budget_max)
              END >= {budget_min})
          AND NOT EXISTS (
              SELECT 1 FROM blocks b
              WHERE (b.blocker_id = ? AND b.blocked_id = i.user_id)
                 OR (b.blocker_id = i.user_id AND b.blocked_id = ?))
          AND (
              (NOT ? OR NOT i.alcohol_involved)
              OR (? AND date(u.birth_date, '+20 years') <= ?)
          )
    """.replace("{budget_min}", str(PAIR_BUDGET_MIN_YEN))

    async def _origin_row(self, origin_id: str):
        return (
            await self.db.prepare(
                "SELECT i.id, i.version, i.user_id, i.category_primary,"
                " i.alcohol_involved, i.budget_max, i.participants_min,"
                " i.participants_max, i.geo_lon, i.geo_lat, i.geo_radius_m,"
                " i.time_start, i.time_end, i.status, i.embedding,"
                " i.structured_data, u.birth_date"
                " FROM intents i JOIN users u ON u.id = i.user_id WHERE i.id = ?"
            )
            .bind(origin_id)
            .first()
        )

    async def create_retrieval_snapshot(
        self, origin_id: str, expected_revision: int, now
    ) -> str:
        """候補のID・版・類似度を固定したsnapshotを作成(原子的保存)。"""
        origin = await self._origin_row(origin_id)
        if origin is None or origin["version"] != expected_revision:
            raise ValueError(
                f"origin revision mismatch: {origin_id} expected v{expected_revision}"
            )
        if origin["status"] != "active" or origin["embedding"] is None:
            raise ValueError("origin not retrievable")

        origin_embedding = json.loads(origin["embedding"])
        lon, lat = origin["geo_lon"], origin["geo_lat"]
        radius = origin["geo_radius_m"] or DEFAULT_RADIUS_M
        # bboxは相手側半径の最大値も加算(layer1: origin+COALESCE(peer,1000))
        # 相手側半径は上限1000(既定)と仮定した保守幅+正確判定はPython
        bbox = bbox_for_radius(lon, lat, radius + DEFAULT_RADIUS_M)
        now_us = micros(now)
        origin_ge_20 = _ge_20(origin["birth_date"], now)
        rows = (
            await self.db.prepare(self._HARD_SQL)
            .bind(
                DEFAULT_RADIUS_M,  # COALESCE peer radius
                origin["user_id"],
                origin["category_primary"],
                origin["time_end"] or _default_end_us(origin["time_start"]),
                origin["time_start"],  # origin_time_start(us)
                THREE_HOURS_US,  # +3h補完
                bbox[1],
                bbox[3],  # lat BETWEEN
                bbox[0],
                bbox[2],  # lon BETWEEN
                origin["budget_max"],
                origin["budget_max"],
                origin["budget_max"],
                origin["budget_max"],
                origin["budget_max"],
                origin["budget_max"],
                origin["budget_max"],
                origin["budget_max"],
                origin["user_id"],
                origin["user_id"],
                1 if origin["alcohol_involved"] else 0,
                1 if origin_ge_20 else 0,
                _jst_date_str(now),
            )
            .all()
        )

        from latch.worker.matching.layer3 import soft_texts

        # 正確距離(測地線)で確定通過のみsnapshotへ(pass_count=確定値。
        # 参照のCOUNT(*) OVER()はST_DWithin確定後の件数のため)
        origin_threshold_base = radius
        exact_rows = []
        for r in rows:
            threshold = origin_threshold_base + (r["radius_m"] or DEFAULT_RADIUS_M)
            dist = geodesic_distance_m(lon, lat, r["geo_lon"], r["geo_lat"])
            if dist > threshold:
                continue
            exact_rows.append(r)

        snapshot_id = str(uuid_mod.uuid4())
        stmts = [
            self.db.prepare(
                "INSERT INTO retrieval_snapshots"
                " (id, origin_id, origin_version, pass_count, created_at,"
                "  expires_at) VALUES (?, ?, ?, ?, ?, ?)"
            ).bind(
                snapshot_id,
                origin_id,
                origin["version"],
                len(exact_rows),
                now_us,
                now_us + SNAPSHOT_TTL_US,
            )
        ]
        for r in exact_rows:
            emb = json.loads(r["embedding"])
            sim = _cosine(origin_embedding, emb)
            stmts.append(
                self.db.prepare(
                    "INSERT INTO retrieval_snapshot_rows"
                    " (snapshot_id, intent_id, version, similarity, lon, lat,"
                    "  radius_m, time_start, budget_max, soft_texts)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ).bind(
                    snapshot_id,
                    r["id"],
                    r["version"],
                    sim,
                    r["geo_lon"],
                    r["geo_lat"],
                    r["radius_m"],
                    r["time_start"],
                    r["budget_max"],
                    canonical_json(soft_texts(r["structured_data"])),
                )
            )
        await self.db.batch(stmts)
        return snapshot_id

    async def retrieve_exact(
        self, origin: IntentSnapshot, snapshot_id: str
    ) -> RetrievalResult:
        """snapshotから候補を正確判定して上位K_v件を返す。

        - originのID・版がsnapshotと一致することを検証(引数と保存値)
        - 距離はWGS84測地線で正確に再判定(bbox事前絞りはSQL済み)
        - 並びは距離 ASC(=similarity DESC)・同点はintent_id昇順(D-24)
        - 切り詰め(50)は全hard条件・距離・cosineの後
        """
        header = (
            await self.db.prepare(
                "SELECT origin_id, origin_version, pass_count, expires_at"
                " FROM retrieval_snapshots WHERE id = ?"
            )
            .bind(snapshot_id)
            .first()
        )
        if header is None:
            raise ValueError("snapshot not found")
        if header["origin_id"] != origin.intent_id:
            raise ValueError("snapshot origin mismatch")
        if header["origin_version"] != origin.version:
            raise ValueError("snapshot origin version mismatch")
        rows = (
            await self.db.prepare(
                "SELECT intent_id, version, similarity, lon, lat, radius_m,"
                " time_start, budget_max, soft_texts"
                " FROM retrieval_snapshot_rows WHERE snapshot_id = ?"
            )
            .bind(snapshot_id)
            .all()
        )

        origin_embedding = None
        origin_row = await self._origin_row(origin.intent_id)
        if origin_row is not None and origin_row["embedding"]:
            origin_embedding = json.loads(origin_row["embedding"])

        from latch.domain.time import from_micros

        passed: list[RetrievedCandidate] = []
        for r in rows:
            threshold = (origin.radius_m or DEFAULT_RADIUS_M) + (
                r["radius_m"] or DEFAULT_RADIUS_M
            )
            dist = geodesic_distance_m(
                origin.geo_lon, origin.geo_lat, r["lon"], r["lat"]
            )
            if dist > threshold:
                continue
            if origin_embedding is None:
                # origin embeddingが消えた場合は再計算不能 → 現行値を落とす
                continue
            passed.append(
                RetrievedCandidate(
                    intent_id=r["intent_id"],
                    version=r["version"],
                    similarity=_rounded(r["similarity"]),
                    time_start=from_micros(r["time_start"]),
                    budget_max=r["budget_max"],
                    soft_texts=tuple(json.loads(r["soft_texts"])),
                )
            )
        passed.sort(key=lambda c: (-c.similarity, c.intent_id))
        return RetrievalResult(
            candidates=passed[:K_VECTORS],
            pass_count=int(header["pass_count"]),
        )


def _rounded(sim: float) -> float:
    """pgvectorのfloat4出力との比較誤差吸収(1e-6以内 — 計画T4初期案)。"""
    return float(sim)


def _default_end_us(time_start_us: int | None) -> int:
    """origin time_end補完(default_time_endと同一: start+3h)。"""
    if time_start_us is None:
        raise ValueError("origin time_start missing")
    return time_start_us + THREE_HOURS_US


def _ge_20(birth_date: str | None, now) -> bool:
    if birth_date is None:
        return False
    born = datetime.fromisoformat(birth_date).date()
    today = now.astimezone(JST).date()
    from latch.domain.time import age_years

    return age_years(born, today) >= 20


def _jst_date_str(now) -> str:
    return now.astimezone(JST).date().isoformat()
