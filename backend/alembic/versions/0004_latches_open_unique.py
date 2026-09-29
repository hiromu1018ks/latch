"""latchesへ開いている行の部分UNIQUE索引を追加(M2 ws-6・design §2.3)。

同一メンバー集合(intent_ids)の開いている(status IN ('candidate',
'proposed','partial_accept'))latchesを1行に強制する。at-least-once再実行の
冪等(ON CONFLICT DO NOTHING)とnearby行の昇格(既存candidate行の更新として
実装)の両要件をDBで担保する最小構成(supervisor承認事項1・2026-09-29)。
閉じた行(expired/rejected/cancelled/matched/completed)は制約対象外のため
D-07検査を通れば新規latchesを作れる。05 §2への追記は次回docs改版に含める
(ws-5 skip_reason前例)。

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_latches_intent_ids_open ON latches (intent_ids)"
        " WHERE status IN ('candidate', 'proposed', 'partial_accept')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_latches_intent_ids_open")
