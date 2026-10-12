"""プッシュ通知トークン登録の schema(05 v0.9・backlog V-01)。

- push_tokens: FCMトークン(Web Push)の登録。tokenは端末+ブラウザ単位で
  一意(UNIQUE)。同一トークンの別ユーザー再登録は所有権移動(upsert)。
  user_idはFK CASCADE(退会でトークンも消える=送信先の自動失効)。
  トークン値をログへ出さない規律はアプリ層(05 v0.9 実装規定2)。

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_STATEMENTS = [
    """
    CREATE TABLE push_tokens (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL,
        token text NOT NULL,
        platform text NOT NULL DEFAULT 'web',
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        last_seen_at timestamptz,
        CONSTRAINT push_tokens_pkey PRIMARY KEY (id),
        CONSTRAINT fk_push_tokens_user FOREIGN KEY (user_id)
            REFERENCES users (id) ON DELETE CASCADE,
        CONSTRAINT push_tokens_platform_check
            CHECK (platform IN ('web')),
        CONSTRAINT push_tokens_token_check CHECK (char_length(token) BETWEEN 1 AND 4096)
    )
    """,
    """
    CREATE UNIQUE INDEX uq_push_tokens_token ON push_tokens (token)
    """,
    """
    CREATE INDEX idx_push_tokens_user ON push_tokens (user_id)
    """,
]

DOWN_STATEMENTS = [
    "DROP TABLE push_tokens",
]


def upgrade() -> None:
    for stmt in SQL_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN_STATEMENTS:
        op.execute(stmt)
