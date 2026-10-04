"""Observability用3テーブル(M4 ws-4・design §2.4/§2.8・supervisor承認3)。

sessions: アクセス系基本計測(04 §2・05 §19。user_idと時刻のみ・Intentの
文言を運ぶ列は構造的に存在しない)。
kpi_daily: KPI日次集計の恒久値((metric_key, day)行持ち・分子/分母のみを
保持し比率は保存しない — design §2.3)。09 §2に規定のないテーブルのため
05 §2への追記はdocs改版候補(design §7論点3・次回docs改版へ)。
kpi_muted_user_snapshot: 通知オフ率の素材(mutedは履歴を持たないため日次
スナップショット差分 — design §2.8)。
3テーブルともFKなし(design §4「他テーブルの削除順(FK)に依存しない独立
表」。user_idはusers行消滅後も残る観測用素材)。

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_STATEMENTS = [
    # sessions
    """
    CREATE TABLE sessions (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL,
        created_at timestamptz NOT NULL,
        CONSTRAINT sessions_pkey PRIMARY KEY (id)
    )
    """,
    "CREATE INDEX idx_sessions_user_time ON sessions (user_id, created_at)",
    # kpi_daily
    """
    CREATE TABLE kpi_daily (
        metric_key text NOT NULL,
        day date NOT NULL,
        numerator numeric NOT NULL DEFAULT 0,
        denominator numeric NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        CONSTRAINT kpi_daily_pkey PRIMARY KEY (metric_key, day)
    )
    """,
    # kpi_muted_user_snapshot
    """
    CREATE TABLE kpi_muted_user_snapshot (
        day date NOT NULL,
        user_id uuid NOT NULL,
        created_at timestamptz NOT NULL,
        CONSTRAINT kpi_muted_user_snapshot_pkey PRIMARY KEY (day, user_id)
    )
    """,
]


def upgrade() -> None:
    for stmt in SQL_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kpi_muted_user_snapshot")
    op.execute("DROP TABLE IF EXISTS kpi_daily")
    op.execute("DROP TABLE IF EXISTS sessions")
