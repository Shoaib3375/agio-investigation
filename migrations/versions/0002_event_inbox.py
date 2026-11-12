"""event_inbox table

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the event_inbox table with all required indexes."""
    op.create_table(
        "event_inbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("event_version", sa.String(20), nullable=False, server_default="1.0"),
        sa.Column("producer", sa.String(255), nullable=True),
        sa.Column("entity_type", sa.String(255), nullable=True),
        sa.Column("entity_id", sa.String(255), nullable=True),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column("causation_id", sa.String(255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "processing_status",
            sa.String(50),
            nullable=False,
            server_default="received",
        ),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_event_inbox_event_id",
        "event_inbox",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "ix_event_inbox_event_type",
        "event_inbox",
        ["event_type"],
    )
    op.create_index(
        "ix_event_inbox_entity",
        "event_inbox",
        ["entity_type", "entity_id"],
    )
    op.create_index(
        "ix_event_inbox_status",
        "event_inbox",
        ["processing_status", "received_at"],
    )


def downgrade() -> None:
    """Drop the event_inbox table and its indexes."""
    op.drop_index("ix_event_inbox_status", table_name="event_inbox")
    op.drop_index("ix_event_inbox_entity", table_name="event_inbox")
    op.drop_index("ix_event_inbox_event_type", table_name="event_inbox")
    op.drop_index("uq_event_inbox_event_id", table_name="event_inbox")
    op.drop_table("event_inbox")
