"""移行: 切替と巻き戻し(T12・計画 §6)。

- writer世代: 切替時に世代を進め、旧環境の遅れた書込みを拒否する。
  新旧両方の書込境界で世代を確認する(outbox/commandsが担う)。
- 巻き戻し(コードのみ戻す案が第一・DBまで戻す場合):
  受付停止 → D1の追加command journalを旧DB(PostgreSQL)へreplay →
  照合 → 再開。replayは状態の再適用のみ(LLMや通知を再送しない)。
- 処理済みLLM試行・cache・通知IDは引き継ぎ、既知の処理済み仕事を
  再送しない(outbox event_id・notification_deliveries・llm_attempts)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import from_micros

CURRENT_WRITER_GENERATION = 2  # 切替後の世代(旧=1)


@dataclass
class CutoverGate:
    """新writer受付開始前の関門(T12-9: 1つでもNGなら開かない)。"""

    source_drained: bool = False  # 旧writer停止の確認(未確認なら開かない)
    verify_ok: bool = False  # verify_migration合格
    journal_ready: bool = False  # journal生成・連続性実証済み
    replay_proven: bool = False  # 旧DBへのreplay実証済み
    problems: list[str] = field(default_factory=list)

    @property
    def can_open_writer(self) -> bool:
        if not (
            self.source_drained
            and self.verify_ok
            and self.journal_ready
            and self.replay_proven
        ):
            return False
        return not self.problems


def writer_generation_ok(db: LocalD1Database, generation: int) -> bool:
    """書込境界の世代確認(同期関数・呼び出し側でbatch前に使う)。"""
    return generation >= CURRENT_WRITER_GENERATION


async def export_new_journal(
    db: LocalD1Database, *, since_command: str | None = None
) -> list[dict[str, Any]]:
    """切替後に蓄積したD1 journalを旧DB再適用形へ切り出す。"""
    rows = await db.prepare(
        "SELECT seq, command_id, sequence_in_command, change_kind,"
        " table_name, row_id, before_row, after_row, created_at"
        " FROM journal ORDER BY seq ASC"
    ).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "seq": r["seq"],
                "command_id": r["command_id"],
                "sequence_in_command": r["sequence_in_command"],
                "change_kind": r["change_kind"],
                "table_name": r["table_name"],
                "row_id": r["row_id"],
                "before": json.loads(r["before_row"]) if r["before_row"] else None,
                "after": json.loads(r["after_row"]) if r["after_row"] else None,
                "created_at_us": r["created_at"],
            }
        )
    return out


# D1( INTEGER micros / TEXT json ) → PostgreSQL( timestamptz / jsonb )
_REPLAY_TIME_COLUMNS = {
    "users": ("consent_at", "created_at", "updated_at"),
    "intents": (
        "time_start",
        "time_end",
        "expires_at",
        "created_at",
        "updated_at",
    ),
    "latches": (
        "response_deadline",
        "expires_at",
        "created_at",
        "completed_at",
    ),
    "latch_status_events": ("created_at",),
}
_REPLAY_JSON_COLUMNS = {
    "latches": ("proposal", "responses", "intent_ids"),
    "notifications": ("payload",),
    "calibration_records": (
        "prediction",
        "proposal_snapshot",
        "actual_responses",
        "intent_ids",
    ),
}


def _pg_value(table: str, column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in _REPLAY_TIME_COLUMNS.get(table, ()):
        dt = from_micros(int(value))
        return dt
    if column in _REPLAY_JSON_COLUMNS.get(table, ()):
        if isinstance(value, str):
            return value  # PostgreSQLはJSON文字列を受け入れる
        return json.dumps(value, ensure_ascii=False)
    return value


async def replay_to_postgres(
    journal: list[dict[str, Any]], conn, *, skip_commands: set[str] | None = None
) -> dict[str, int]:
    """D1 journalを旧PostgreSQLへ再適用(状態のみ・LLM/通知再送なし)。

    冪等: skip_commands(再適用済みcommand)はスキップ。
    """
    from sqlalchemy import text

    skip = skip_commands or set()
    applied: dict[str, int] = {}
    for entry in journal:
        if entry["command_id"] in skip:
            continue
        table = entry["table_name"]
        after = entry["after"]
        if entry["change_kind"] == "upsert" and after:
            # journalのafterは最小snapshot → upsertは値が存在する列のみ
            cols = list(after.keys())
            assignments = ", ".join(f"{c} = :{c}" for c in cols if c != "id")
            params = {c: _pg_value(table, c, v) for c, v in after.items()}
            params["id"] = entry["row_id"]
            if assignments:
                await conn.execute(
                    text(
                        f"UPDATE {table} SET {assignments}"  # noqa: S608
                        f" WHERE id = CAST(:id AS uuid)"
                    ),
                    params,
                )
        applied[table] = applied.get(table, 0) + 1
    return applied
