"""usersへconsent_at列を追加(M4 ws-5・design §2.1案A・08 D-14)。

オンボーディング同意の取得日時(timestamptz・Nullable・NULL=未同意)。
判定式はconsent_at IS NOT NULLに一元し、API遮断(422 CONSENT_REQUIRED)と
Layer 1 Hard条件(AND u.consent_at IS NOT NULL)の2層から参照される。
既存行へのバックフィルは行わない(同意の取得日時を捏造しない — design §2.1。
staging/試験のユーザーは投入手順で同意APIを叩く)。取り消しは非実装
(2026-10-03ユーザー了承・docsに規定なし)。05 §2 users表への追記は
次回docs改版候補(design §3.4)。

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN consent_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS consent_at")
