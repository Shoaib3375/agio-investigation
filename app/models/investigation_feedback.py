"""SQLAlchemy ORM model for the investigation_feedback table.

Stores authorised human reviewer decisions on investigations or individual
findings. Feedback does not automatically retrain any model — it may become
reviewed historical knowledge after an explicit approval process.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class InvestigationFeedback(Base):
    __tablename__ = "investigation_feedback"

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
    # Optional — feedback may target a specific finding.
    finding_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("investigation_findings.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Pseudonymous identifier of the authorised reviewer (not a raw user record).
    reviewer_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    # accepted | rejected | needs_more_evidence
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    # Human-readable explanation from the reviewer.
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=UTC),
        server_default="now()",
    )

    __table_args__ = (
        Index("ix_feedback_investigation", "investigation_id"),
        Index("ix_feedback_finding", "finding_id"),
    )

    investigation: Mapped["Investigation"] = relationship(  # noqa: F821
        "Investigation", back_populates="feedback"
    )
    finding: Mapped["InvestigationFinding | None"] = relationship(  # noqa: F821
        "InvestigationFinding"
    )
