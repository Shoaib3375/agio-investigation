"""SQLAlchemy ORM model for the investigation_reports table.

Each report is a versioned snapshot of the full investigation output.
LLM-generated fields (summary, possible_explanations) must be clearly
distinguished from observed_facts, which must reference evidence_ids.

The LLM is never the source of financial facts.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class InvestigationReport(Base):
    __tablename__ = "investigation_reports"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default="gen_random_uuid()",
    )
    investigation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Increments each time a new report is generated for the same investigation.
    report_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    # Short human-readable executive summary.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured facts, each referencing evidence_ids. Format:
    # [{"statement": "...", "evidence_ids": ["uuid", ...]}]
    observed_facts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Structured findings snapshot at report generation time.
    findings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Evidence that could not be retrieved.
    # [{"evidence_type": "...", "reason": "..."}]
    missing_evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Suggested actions for the human reviewer.
    recommendations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Caveats and uncertainty statements.
    limitations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # LLM model identifier if used (None for deterministic-only reports).
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Prompt template version used for LLM generation.
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=UTC),
        server_default="now()",
    )

    __table_args__ = (
        Index("ix_reports_investigation", "investigation_id"),
        Index("ix_reports_generated_at", "investigation_id", "generated_at"),
    )

    investigation: Mapped["Investigation"] = relationship(  # noqa: F821
        "Investigation", back_populates="reports"
    )
