"""group_candidatesへ開いている行の部分UNIQUE索引を追加(M2 ws-7・design §2.8)。

同一intent_idsの開いている(status IN ('candidate','proposed'))集合を1行に
強制する。at-least-once再実行と複数メンバー起点(いずれも種になり得る)の
再構成による同一集合重複生成をDBで防ぐ最小構成(supervisor承認事項3・
2026-09-29)。閉じた集合(closed)の履歴は複数行保持できる(D-07履歴検査・
集計の供給源)。05 §2への追記は次回docs改版に含める(ws-5/ws-6前例)。

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_group_candidates_intent_ids_open"
        " ON group_candidates (intent_ids)"
        " WHERE status IN ('candidate', 'proposed')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_group_candidates_intent_ids_open")
