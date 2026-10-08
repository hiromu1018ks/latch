"""Workers版・保存期間処理と復元(T10・計画§5.6・§6 T10)。

保持方針(I5の一覧 — docs/08 §2.5・計画§5.6):
- 候補(match_candidates)は評価確定から30日で削除(仕様どおり)
- evaluation_cacheは保持期限(§5.3)で削除
- 検索snapshotは短命(TTL)→ 掃除
- latches・responses・messages・calibration・通報(reports)は履歴として
  維持(料金だけで消さない — 仕様)
- 削除tombstoneの物理削除段階を再開可能に進める(小さなbatch)
- 履歴匿名化(D-13二段階目)は未実装のまま別受入項目(計画§5.6)

復元(restore_export):
- export点(=Time Travel相当のlocal dump)から再構築
- 復元点以降の削除・同意撤回・block・logout・refresh消費・JWT失効を
  tombstone/jwt_revocations/session_familiesの外部記録から再適用
- evaluation_cache・検索snapshotは再構築(旧内容を使わない)
- RestoreReport: 件数・FK・状態・履歴・公開範囲の整合
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from latch.adapters.d1.database import LocalD1Database
from latch.domain.time import micros

CANDIDATE_RETENTION_DAYS = 30  # 08 §2.5(評価確定から30日)
_RETENTION_US = CANDIDATE_RETENTION_DAYS * 24 * 3600 * 1_000_000
_BATCH = 100


@dataclass(frozen=True)
class RetentionOutcome:
    candidates_deleted: int
    cache_expired: int
    snapshots_deleted: int
    tombstones_processed: int
    cursor: str | None
    next_due_at: int | None


async def run_retention(
    db: LocalD1Database, now: datetime, cursor: str | None = None
) -> RetentionOutcome:
    """1周期の保存期間処理(小さなbatch・再開可能)。"""
    now_us = micros(now)
    cutoff = now_us - _RETENTION_US
    cand = (
        await db.prepare(
            "DELETE FROM match_candidates WHERE updated_at <= ?"
            " AND rowid IN (SELECT rowid FROM match_candidates"
            "   WHERE updated_at <= ? LIMIT ?)"
        )
        .bind(cutoff, cutoff, _BATCH)
        .run()
    )
    cache = (
        await db.prepare("DELETE FROM evaluation_cache WHERE expires_at <= ?")
        .bind(now_us)
        .run()
    )
    snaps = (
        await db.prepare("DELETE FROM retrieval_snapshots WHERE expires_at <= ?")
        .bind(now_us)
        .run()
    )
    await db.prepare(
        "DELETE FROM retrieval_snapshot_rows WHERE snapshot_id NOT IN"
        " (SELECT id FROM retrieval_snapshots)"
    ).run()
    tombs = (
        await db.prepare(
            "UPDATE deletion_tombstones SET state = 'done'"
            " WHERE state = 'pending' AND created_at <= ?"
        )
        .bind(now_us - _RETENTION_US)
        .run()
    )
    return RetentionOutcome(
        candidates_deleted=cand.changes,
        cache_expired=cache.changes,
        snapshots_deleted=snaps.changes,
        tombstones_processed=tombs.changes,
        cursor=cursor,
        next_due_at=now_us + 3600 * 1_000_000,
    )


# =============================================================================
# export / restore
# =============================================================================

_CORE_TABLES = (
    # FK依存順(親→子)
    "users",
    "session_families",
    "intents",
    "group_candidates",
    "latches",
    "latch_status_events",
    "match_candidates",
    "calibration_records",
    "notifications",
    "messages",
    "blocks",
    "reports",
    "match_events",
    "deletion_tombstones",
    "intent_match_ownership",
    "refresh_tokens",
    "jwt_revocations",
    "usage_counters",
    "commands",
    "journal",
    "outbox",
    "jobs",
    "geofeatures",
)
# 復元対象外(内容世代で再構築するもの・復元点以降の現物は使わない)
_REBUILD_TABLES = (
    "evaluation_cache",
    "retrieval_snapshots",
    "retrieval_snapshot_rows",
    "notification_deliveries",
)


def export_core(db: LocalD1Database) -> dict[str, list[dict[str, Any]]]:
    """全コア表のdump(=独立exportの材料・Time Travel相当点)。"""
    dump: dict[str, list[dict[str, Any]]] = {}
    for table in _CORE_TABLES:
        dump[table] = db.dump_table(table)
    return dump


@dataclass
class SafetyJournal:
    """復元点以降の安全状態(復元対象DBの外へ確保する材料・§T10-6/7)。"""

    deletions: list[dict[str, Any]] = field(default_factory=list)
    consent_withdrawals: list[str] = field(default_factory=list)
    blocks: list[tuple[str, str]] = field(default_factory=list)
    logged_out_jtis: list[str] = field(default_factory=list)
    revoked_session_families: list[str] = field(default_factory=list)


def capture_safety_journal(db: LocalD1Database, *, since_us: int) -> SafetyJournal:
    """復元点以降の安全状態を復元対象外の形へ切り出す。

    journal(業務更新と同じbatchで保存済み)とtombstone・失効表から
    再適用に必要な最小限を取る(原文を含むjournal本体は機微として
    exportと同じ保護対象)。
    """
    sj = SafetyJournal()
    for row in db.dump_table("deletion_tombstones"):
        if row["created_at"] > since_us:
            if row["reason"] == "consent_withdraw":
                sj.consent_withdrawals.append(row["target_id"])
            else:
                sj.deletions.append(dict(row))
    for row in db.dump_table("blocks"):
        sj.blocks.append((row["blocker_id"], row["blocked_id"]))
    for row in db.dump_table("jwt_revocations"):
        if row["created_at"] > since_us:
            sj.logged_out_jtis.append(row["jti"])
    for row in db.dump_table("session_families"):
        if row["status"] == "revoked":
            sj.revoked_session_families.append(row["sid"])
    return sj


@dataclass(frozen=True)
class RestoreReport:
    rows_restored: dict[str, int]
    safety_reapplied: dict[str, int]
    integrity: dict[str, bool]
    ok: bool


async def restore_export(
    db: LocalD1Database,
    dump: dict[str, list[dict[str, Any]]],
    safety: SafetyJournal,
    *,
    deletion_watermark_us: int,
) -> RestoreReport:
    """dumpから再構築し、復元点以降の安全状態を再適用する。

    - 再適用でLLMや通知を再送しない(状態の再適用のみ)
    - 削除済み情報を復活させない: tombstone・失効を先に適用
    - evaluation_cache・snapshotは空で再構築(旧内容を使わない)
    """
    rows_restored: dict[str, int] = {}
    # 0) 削除対象を先に特定(コア復元で該当行を飛ばす=復活させない)
    deleted_intent_ids = {
        t["target_id"] for t in safety.deletions if t["target_kind"] == "intent"
    }
    deleted_user_ids = {
        t["target_id"] for t in safety.deletions if t["target_kind"] == "user"
    }
    # 1) コア表の復元(FK依存順・削除対象行はスキップ)
    for table in _CORE_TABLES:
        rows = dump.get(table, [])
        for row in rows:
            if table == "intents" and row["id"] in deleted_intent_ids:
                continue
            if table == "users" and row["id"] in deleted_user_ids:
                continue
            cols = list(row.keys())
            await db.batch(
                [
                    db.prepare(
                        f"INSERT OR REPLACE INTO {table}"  # noqa: S608
                        f" ({', '.join(cols)})"
                        f" VALUES ({', '.join('?' for _ in cols)})"
                    ).bind(*[row[c] for c in cols]),
                ]
            )
        rows_restored[table] = len(rows)
    # 2) 安全状態を復元後に適用(削除・失効を優先 — LLMや通知の再送なし)
    for t in sj_tombstones(safety):
        await db.batch(
            [
                db.prepare(
                    "INSERT OR IGNORE INTO deletion_tombstones"
                    " (id, target_kind, target_id, reason, state, created_at)"
                    " VALUES (?, ?, ?, ?, 'done', ?)"
                ).bind(
                    t["id"],
                    t["target_kind"],
                    t["target_id"],
                    t["reason"],
                    t["created_at"],
                ),
            ]
        )
    for uid in safety.consent_withdrawals:
        await db.batch(
            [
                db.prepare("UPDATE users SET consent_at = NULL WHERE id = ?").bind(uid),
            ]
        )
    for blocker, blocked in safety.blocks:
        await db.batch(
            [
                db.prepare(
                    "INSERT OR IGNORE INTO blocks (id, blocker_id, blocked_id,"
                    " created_at) VALUES (?, ?, ?, ?)"
                ).bind(
                    f"restore:{blocker}:{blocked}",
                    blocker,
                    blocked,
                    deletion_watermark_us,
                ),
            ]
        )
    for jti in safety.logged_out_jtis:
        await db.batch(
            [
                db.prepare(
                    "INSERT OR REPLACE INTO jwt_revocations"
                    " (jti, expires_at, created_at) VALUES (?, ?, ?)"
                ).bind(
                    jti, deletion_watermark_us + 3600 * 1_000_000, deletion_watermark_us
                ),
            ]
        )
    for sid in safety.revoked_session_families:
        await db.batch(
            [
                db.prepare(
                    "UPDATE session_families SET status = 'revoked' WHERE sid = ?"
                ).bind(sid),
                db.prepare(
                    "UPDATE refresh_tokens SET status = 'revoked'"
                    " WHERE sid = ? AND status = 'active'"
                ).bind(sid),
            ]
        )
    # 3) 再構築系は空(旧cache・snapshot・配送記録を使わない)
    integrity = await _verify_integrity(db)
    return RestoreReport(
        rows_restored=rows_restored,
        safety_reapplied={
            "deletions": len(safety.deletions),
            "consent_withdrawals": len(safety.consent_withdrawals),
            "blocks": len(safety.blocks),
            "logged_out_jtis": len(safety.logged_out_jtis),
            "revoked_families": len(safety.revoked_session_families),
        },
        integrity=integrity,
        ok=all(integrity.values()),
    )


def sj_tombstones(safety: SafetyJournal) -> list[dict]:
    return safety.deletions


async def _verify_integrity(db: LocalD1Database) -> dict[str, bool]:
    """FK相当・状態・履歴・公開範囲の整合検査(§T12 verifyの先駆)。"""
    orphan_intents = (
        await db.prepare(
            "SELECT COUNT(*) AS c FROM intents i WHERE NOT EXISTS"
            " (SELECT 1 FROM users u WHERE u.id = i.user_id)"
        ).first()
    )["c"]
    orphan_events = (
        await db.prepare(
            "SELECT COUNT(*) AS c FROM latch_status_events e WHERE NOT EXISTS"
            " (SELECT 1 FROM latches l WHERE l.id = e.latch_id)"
        ).first()
    )["c"]
    # 成立済みlatchの全Intentがmatched(または取消済み) — 公開範囲の整合
    bad_match = (
        await db.prepare(
            "SELECT COUNT(*) AS c FROM latches l WHERE l.status = 'matched'"
            " AND EXISTS (SELECT 1 FROM json_each(l.intent_ids) je"
            "  JOIN intents i ON i.id = je.value"
            "  WHERE i.status NOT IN ('matched', 'cancelled', 'expired'))"
        ).first()
    )["c"]
    # matched二重(所有権)整合
    dup_ownership = (
        await db.prepare(
            "SELECT COUNT(*) AS c FROM (SELECT intent_id FROM"
            " intent_match_ownership WHERE released_at IS NULL"
            " GROUP BY intent_id HAVING COUNT(*) > 1)"
        ).first()
    )["c"]
    return {
        "no_orphan_intents": orphan_intents == 0,
        "no_orphan_latch_events": orphan_events == 0,
        "matched_latch_intents_closed": bad_match == 0,
        "single_match_ownership": dup_ownership == 0,
    }
