"""UI v2 Phase 1B: 参加取り消し(D-08)の schema(05 §2・§5)。

- latches.left_user_ids: 参加取り消し済みのuser_id集合(書込拒否判定・
  ALREADY_LEFT判定の永続化。statusはmatchedのまま変わらない)
- messages.sender_id のNULL許容: 参加取り消しのシステム行
  (「{表示名}が参加を取り消しました」)は送信者を持たない

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_STATEMENTS = [
    """
    ALTER TABLE latches
        ADD COLUMN left_user_ids uuid[] NOT NULL DEFAULT '{}'
    """,
    "ALTER TABLE messages ALTER COLUMN sender_id DROP NOT NULL",
]

DOWN_STATEMENTS = [
    """
    DELETE FROM messages WHERE sender_id IS NULL
    """,
    "ALTER TABLE messages ALTER COLUMN sender_id SET NOT NULL",
    "ALTER TABLE latches DROP COLUMN left_user_ids",
]


def upgrade() -> None:
    for stmt in SQL_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN_STATEMENTS:
        op.execute(stmt)
