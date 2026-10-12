"""A/B基盤・MLRベースラインの3表(V1-P4・05 v0.8・09 §5/D-02)。

ab_experiments: A/B実験の登録(開始/停止はCLIのみ・公開APIなし)。
ab_assignments: ユーザー割付(PK(experiment_key, user_id)が割付の安定性を
DB強制。variantの再計算は行わない)。
mlr_baselines: MLR週次中央値ベースラインの記録(A-03・09 D-02の器。
追記のみ・判定はrecorded_at最新行)。

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_STATEMENTS = [
    """
    CREATE TABLE ab_experiments (
        experiment_key text NOT NULL,
        status text NOT NULL
            CONSTRAINT ab_experiments_status_check
            CHECK (status IN ('running', 'stopped')),
        control_value text NOT NULL,
        treatment_value text NOT NULL,
        started_at timestamptz NOT NULL,
        stopped_at timestamptz,
        CONSTRAINT ab_experiments_pkey PRIMARY KEY (experiment_key),
        CONSTRAINT ab_experiments_stopped_at_check
            CHECK (status != 'stopped' OR stopped_at IS NOT NULL)
    )
    """,
    """
    CREATE TABLE ab_assignments (
        experiment_key text NOT NULL,
        user_id uuid NOT NULL,
        variant text NOT NULL
            CONSTRAINT ab_assignments_variant_check
            CHECK (variant IN ('control', 'treatment')),
        stratum text NOT NULL,
        assigned_at timestamptz NOT NULL,
        CONSTRAINT ab_assignments_pkey PRIMARY KEY (experiment_key, user_id),
        CONSTRAINT ab_assignments_experiment_key_fkey FOREIGN KEY
            (experiment_key) REFERENCES ab_experiments (experiment_key)
            ON DELETE CASCADE,
        CONSTRAINT ab_assignments_user_id_fkey FOREIGN KEY (user_id)
            REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_ab_assignments_user ON ab_assignments (user_id)",
    """
    CREATE TABLE mlr_baselines (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        value numeric NOT NULL,
        source_weeks jsonb NOT NULL,
        recorded_at timestamptz NOT NULL,
        CONSTRAINT mlr_baselines_pkey PRIMARY KEY (id)
    )
    """,
]


def upgrade() -> None:
    for stmt in SQL_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mlr_baselines")
    op.execute("DROP TABLE IF EXISTS ab_assignments")
    op.execute("DROP TABLE IF EXISTS ab_experiments")
