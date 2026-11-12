"""initial placeholder

Revision ID: 0001
Revises:
Create Date: 2026-11-12 00:00:00.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Placeholder migration proving the Alembic toolchain is wired."""


def downgrade() -> None:
    """Reverse the placeholder migration."""
