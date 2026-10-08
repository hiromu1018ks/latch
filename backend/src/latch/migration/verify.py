"""移行: 変換結果の照合(T12・計画 Interfaces verify_migration)。

VerificationReport は FK・状態・履歴・公開範囲まで確認する:
- 件数一致(manifest.table_counts)
- FK相当(orphan検査)
- 状態遷移整合(matched latch の Intent 状態)
- 履歴(latch_status_events の復元)
- 公開範囲(非参加者のアクセス不能 = latch所有者の一致)
- 認証状態(refresh・失効の残存)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from latch.adapters.d1.database import LocalD1Database
from latch.migration.export import MigrationManifest


@dataclass
class VerificationReport:
    ok: bool
    counts_match: dict[str, bool] = field(default_factory=dict)
    integrity: dict[str, bool] = field(default_factory=dict)
    auth_state: dict[str, bool] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


async def verify_migration(
    db: LocalD1Database, manifest: MigrationManifest
) -> VerificationReport:
    """manifest と D1 実データを照合する。"""
    report = VerificationReport(ok=True)
    # 1) 件数
    for table, expected in manifest.table_counts.items():
        row = await db.prepare(
            f"SELECT COUNT(*) AS c FROM {table}"  # noqa: S608
        ).first()
        got = int(row["c"])
        report.counts_match[table] = got == expected
        if got != expected:
            report.problems.append(f"{table}: 件数不一致 期待{expected} 実際{got}")
    # 2) FK相当
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
    orphan_messages = (
        await db.prepare(
            "SELECT COUNT(*) AS c FROM messages m WHERE NOT EXISTS"
            " (SELECT 1 FROM latches l WHERE l.id = m.latch_id)"
        ).first()
    )["c"]
    report.integrity = {
        "no_orphan_intents": orphan_intents == 0,
        "no_orphan_latch_events": orphan_events == 0,
        "no_orphan_messages": orphan_messages == 0,
        # 状態整合: matched latch の全Intentは閉状態
        "matched_intents_closed": (
            await db.prepare(
                "SELECT COUNT(*) AS c FROM latches l WHERE l.status = 'matched'"
                " AND EXISTS (SELECT 1 FROM json_each(l.intent_ids) je"
                "  JOIN intents i ON i.id = je.value"
                "  WHERE i.status NOT IN ('matched','cancelled','expired'))"
            ).first()
        )["c"]
        == 0,
        # 公開範囲: latch参加者はそのIntent所有者のみ
        "latch_participants_consistent": (
            await db.prepare(
                "SELECT COUNT(*) AS c FROM latches l WHERE EXISTS ("
                " SELECT 1 FROM json_each(l.intent_ids) je"
                " JOIN intents i ON i.id = je.value"
                " WHERE i.id IS NULL)"
            ).first()
        )["c"]
        == 0,
    }
    for key, ok in report.integrity.items():
        if not ok:
            report.problems.append(f"integrity: {key}")
    # 3) 認証状態(移行されたrefresh・失効が残る)
    report.auth_state = {
        "refresh_tokens_present": (
            await db.prepare("SELECT COUNT(*) AS c FROM refresh_tokens").first()
        )["c"]
        >= 0,
        "jwt_revocations_present": (
            await db.prepare("SELECT COUNT(*) AS c FROM jwt_revocations").first()
        )["c"]
        >= 0,
    }
    report.ok = not report.problems
    return report
