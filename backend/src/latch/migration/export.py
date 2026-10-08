"""移行: 旧系(PostgreSQL)からのexport(T12・計画 §6)。

本番データが無い場合も seed で変換・復元を検証する(計画T12-1)。
MigrationManifest は export時点・schema・件数・整合性hash・削除watermark・
writer世代・未処理job・journal参照を持つ(計画T12 Interfaces)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_SOURCE_TABLES = (
    "users",
    "intents",
    "latches",
    "latch_status_events",
    "match_candidates",
    "group_candidates",
    "calibration_records",
    "notifications",
    "messages",
    "blocks",
    "reports",
    "match_events",
    "geofeatures",
)


@dataclass
class MigrationManifest:
    export_at: datetime
    schema_head: str
    table_counts: dict[str, int]
    integrity_hashes: dict[str, str]
    deletion_watermark: str  # 削除再適用の起点(tombstones参照)
    writer_generation: int = 1
    pending_jobs: int = 0  # 旧系の未処理job(match_events pending等)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "export_at": self.export_at.isoformat(),
            "schema_head": self.schema_head,
            "table_counts": self.table_counts,
            "integrity_hashes": self.integrity_hashes,
            "deletion_watermark": self.deletion_watermark,
            "writer_generation": self.writer_generation,
            "pending_jobs": self.pending_jobs,
            "notes": self.notes,
        }


def _row_hash(rows: list[tuple]) -> str:
    payload = json.dumps(
        [list(r) for r in rows], default=str, ensure_ascii=True, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


async def export_source(
    conn,
) -> tuple[dict[str, list[dict[str, Any]]], MigrationManifest]:
    """旧系PostgreSQLからコア表をexport(生値のまま)。"""
    from sqlalchemy import text

    dump: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    hashes: dict[str, str] = {}
    for table in _SOURCE_TABLES:
        res = await conn.execute(text(f"SELECT * FROM {table}"))  # noqa: S608
        rows = [dict(r._mapping) for r in res]
        dump[table] = rows
        counts[table] = len(rows)
        hashes[table] = _row_hash([tuple(str(v) for v in r.values()) for r in rows])
    pending = await conn.execute(
        text("SELECT COUNT(*) FROM match_events WHERE status = 'pending'")
    )
    from latch.core.clock import SystemClock

    manifest = MigrationManifest(
        export_at=SystemClock().now(),
        schema_head=_alembic_head(),
        table_counts=counts,
        integrity_hashes=hashes,
        deletion_watermark="tombstones:full",
        writer_generation=1,
        pending_jobs=int(pending.scalar() or 0),
        notes=[
            "旧系への書込み停止後に取得すること(T12-2)",
            "権限・秘密情報のコピーは必要範囲だけ(鍵類は設定移行・DBへ入れない)",
        ],
    )
    return dump, manifest


def _alembic_head() -> str:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).resolve().parents[3]
    cfg = Config(str(backend_dir / "alembic.ini"))
    # script_locationはini内で相対指定のためCWDに依存しない絶対へ上書き
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()
