"""messagesのlatch/created_at複合Indexとcalibration_recordsの部分UNIQUEを追加
(M3 ws-4・design §2.5)。

idx_messages_latch: GET /messages改頁のキーセット(latch_id絞り+created_at
昇順)。05 §3にmessagesのIndex規定がなく設計判断(supervisor承認事項⑥)。
ux_calibration_latch: attendanceの行特定を「latch_id→高々1行」と構造保証
(latches部分UNIQUE(0004)・group_candidates部分UNIQUE(0005)と同型)。
匿名化(D-13・ws-6)でlatch_idがNULLへ変わると対象外になるため部分UNIQUE。
05 §3への追記は次回docs改版候補(design §5-6)。

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX idx_messages_latch ON messages (latch_id, created_at)")
    op.execute(
        "CREATE UNIQUE INDEX ux_calibration_latch"
        " ON calibration_records (latch_id)"
        " WHERE latch_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_calibration_latch")
    op.execute("DROP INDEX IF EXISTS idx_messages_latch")
