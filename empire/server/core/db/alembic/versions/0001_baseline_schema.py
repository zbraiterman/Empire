"""baseline schema

Revision ID: 0001
Revises:
Create Date: 2026-03-22

This is the baseline migration. For existing databases, this migration
is stamped (not run) so they are marked as up-to-date. For new databases,
tables are created via Base.metadata.create_all() in startup_db(), then
stamped to head.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Baseline: no-op. Existing schema is assumed to be correct.
    pass


def downgrade() -> None:
    # Baseline: no-op.
    pass
