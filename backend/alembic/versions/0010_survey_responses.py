"""体感アンケート回答の schema(05 v0.7・09 §2.3)。

- survey_responses: ラウンド(onboarding/week4)別の回答(ユーザー単位・単発)。
  answersは5段階値のjsonb・free_commentはweek4のみ任意。
  UNIQUE(user_id, round)が「単発の配信」(09 §2.3)をDBレベルで強制する。
  個別Intentとの紐付け列を持たない(08 §2.4)。

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_STATEMENTS = [
    """
    CREATE TABLE survey_responses (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL,
        round text NOT NULL,
        answers jsonb NOT NULL,
        free_comment text,
        created_at timestamptz NOT NULL,
        CONSTRAINT survey_responses_pkey PRIMARY KEY (id),
        CONSTRAINT fk_survey_responses_user FOREIGN KEY (user_id)
            REFERENCES users (id),
        CONSTRAINT survey_responses_round_check
            CHECK (round IN ('onboarding', 'week4'))
    )
    """,
    """
    CREATE UNIQUE INDEX uq_survey_responses_user_round
        ON survey_responses (user_id, round)
    """,
]

DOWN_STATEMENTS = [
    "DROP TABLE survey_responses",
]


def upgrade() -> None:
    for stmt in SQL_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN_STATEMENTS:
        op.execute(stmt)
