"""match_candidatesへskip_reason列を追加(M2 ws-5・design §2.7)。

deny理由(design §2.4-2の列挙)とD-15の回収経路区別(予算系=期間切れ後・
障害系=即時)をupdated_atだけで実装できないための列追加。値域は
intent_daily / user_daily / global_daily / global_monthly / llm_failure /
invalid_output(小文字スネーク・小文字固定)。evaluated・closed・pending行はNULL。
05 §2への追記は次回docs改版に含める(ws-3のlocation_name前例)。

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE match_candidates ADD COLUMN skip_reason text NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE match_candidates DROP COLUMN skip_reason")
