"""investigations, evidence_records, findings, reports, feedback tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── investigations ────────────────────────────────────────────────────────
    op.create_table(
        "investigations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("external_reference", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("trigger_event_id", sa.String(255), nullable=True),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column(
            "investigation_type",
            sa.String(50),
            nullable=False,
            server_default="order_lifecycle",
        ),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "priority",
            sa.String(20),
            nullable=False,
            server_default="normal",
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_investigations_external_reference", "investigations", ["external_reference"])
    op.create_index("ix_investigations_entity", "investigations", ["entity_type", "external_reference"])
    op.create_index("ix_investigations_status", "investigations", ["status", "created_at"])
    op.create_index("ix_investigations_trigger_event", "investigations", ["trigger_event_id"])

    # ── evidence_records ──────────────────────────────────────────────────────
    op.create_table(
        "evidence_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_type", sa.String(50), nullable=False),
        sa.Column(
            "source_system",
            sa.String(100),
            nullable=False,
            server_default="agio-paymentexchange",
        ),
        sa.Column("source_record_type", sa.String(100), nullable=False),
        sa.Column("source_record_id", sa.String(255), nullable=False),
        sa.Column("source_version", sa.String(50), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("verification_method", sa.String(100), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_evidence_investigation", "evidence_records", ["investigation_id"])
    op.create_index("ix_evidence_type", "evidence_records", ["evidence_type"])
    op.create_index("ix_evidence_source", "evidence_records", ["source_record_type", "source_record_id"])

    # ── investigation_findings ────────────────────────────────────────────────
    op.create_table(
        "investigation_findings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("finding_code", sa.String(100), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column(
            "severity",
            sa.String(20),
            nullable=False,
            server_default="informational",
        ),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "source",
            sa.String(30),
            nullable=False,
            server_default="deterministic_rule",
        ),
        sa.Column("rule_version", sa.String(20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_findings_investigation", "investigation_findings", ["investigation_id"])
    op.create_index("ix_findings_code", "investigation_findings", ["finding_code"])
    op.create_index("ix_findings_severity", "investigation_findings", ["severity"])

    # ── investigation_reports ─────────────────────────────────────────────────
    op.create_table(
        "investigation_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("observed_facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("missing_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("recommendations", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(20), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_reports_investigation", "investigation_reports", ["investigation_id"])
    op.create_index("ix_reports_generated_at", "investigation_reports", ["investigation_id", "generated_at"])

    # ── investigation_feedback ────────────────────────────────────────────────
    op.create_table(
        "investigation_feedback",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("investigation_findings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewer_reference", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_feedback_investigation", "investigation_feedback", ["investigation_id"])
    op.create_index("ix_feedback_finding", "investigation_feedback", ["finding_id"])


def downgrade() -> None:
    op.drop_index("ix_feedback_finding", table_name="investigation_feedback")
    op.drop_index("ix_feedback_investigation", table_name="investigation_feedback")
    op.drop_table("investigation_feedback")

    op.drop_index("ix_reports_generated_at", table_name="investigation_reports")
    op.drop_index("ix_reports_investigation", table_name="investigation_reports")
    op.drop_table("investigation_reports")

    op.drop_index("ix_findings_severity", table_name="investigation_findings")
    op.drop_index("ix_findings_code", table_name="investigation_findings")
    op.drop_index("ix_findings_investigation", table_name="investigation_findings")
    op.drop_table("investigation_findings")

    op.drop_index("ix_evidence_source", table_name="evidence_records")
    op.drop_index("ix_evidence_type", table_name="evidence_records")
    op.drop_index("ix_evidence_investigation", table_name="evidence_records")
    op.drop_table("evidence_records")

    op.drop_index("ix_investigations_trigger_event", table_name="investigations")
    op.drop_index("ix_investigations_status", table_name="investigations")
    op.drop_index("ix_investigations_entity", table_name="investigations")
    op.drop_index("ix_investigations_external_reference", table_name="investigations")
    op.drop_table("investigations")
