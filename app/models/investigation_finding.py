"""SQLAlchemy ORM model for the investigation_findings table.

Stores structured findings produced by deterministic rules (PY-M05) or
human review. The LLM may produce findings in PY-M07 — those are stored
here with source="llm" and must reference supporting evidence_ids.

A confidence score is not an AML probability and must not be presented as one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class InvestigationFinding(Base):
    __tablename__ = "investigation_findings"

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
    # Stable machine-readable code (e.g. "BLOCKCHAIN_AMOUNT_MISMATCH").
    finding_code: Mapped[str] = mapped_column(String(100), nullable=False)
    # financial | blockchain | provider | account | compliance | ledger
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # informational | low | medium | high | critical
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="informational"
    )
    # Optional confidence score [0.0, 1.0]. Not an AML probability.
    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON array of evidence_record UUIDs that support this finding.
    evidence_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # deterministic_rule | llm | human
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="deterministic_rule"
    )
    # Version string of the rule that produced this finding (e.g. "1.0.0").
    rule_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=UTC),
        server_default="now()",
    )

    __table_args__ = (
        Index("ix_findings_investigation", "investigation_id"),
        Index("ix_findings_code", "finding_code"),
        Index("ix_findings_severity", "severity"),
    )

    investigation: Mapped["Investigation"] = relationship(  # noqa: F821
        "Investigation", back_populates="findings"
    )
