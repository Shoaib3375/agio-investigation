"""SQLAlchemy ORM model for the investigations table.

One row represents a single investigation workflow instance.
An investigation may be triggered by an event or created manually.
Multiple events for the same entity are correlated into one investigation
using the deduplication policy in InvestigationService.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default="gen_random_uuid()",
    )
    # Public reference to the entity being investigated (e.g. order UUID, user id).
    external_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    # Type of entity: order | user | withdrawal | risk_case
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # event_id from event_inbox that triggered this investigation.
    trigger_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Propagated from the triggering event.
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Category of investigation: order_lifecycle | deposit_mismatch | provider_issue |
    # ledger_discrepancy | account_risk | reconciliation | manual
    investigation_type: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="order_lifecycle"
    )
    # queued | collecting | analyzing | completed | failed | review_required
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="queued"
    )
    # low | normal | high | critical
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="normal"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=UTC),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=UTC),
        onupdate=lambda: datetime.now(tz=UTC),
        server_default="now()",
    )

    __table_args__ = (
        Index("ix_investigations_external_reference", "external_reference"),
        Index("ix_investigations_entity", "entity_type", "external_reference"),
        Index("ix_investigations_status", "status", "created_at"),
        Index("ix_investigations_trigger_event", "trigger_event_id"),
    )

    # Relationships — populated by joined loads in repositories.
    evidence_records: Mapped[list["EvidenceRecord"]] = relationship(  # noqa: F821
        "EvidenceRecord", back_populates="investigation", lazy="select"
    )
    findings: Mapped[list["InvestigationFinding"]] = relationship(  # noqa: F821
        "InvestigationFinding", back_populates="investigation", lazy="select"
    )
    reports: Mapped[list["InvestigationReport"]] = relationship(  # noqa: F821
        "InvestigationReport", back_populates="investigation", lazy="select"
    )
    feedback: Mapped[list["InvestigationFeedback"]] = relationship(  # noqa: F821
        "InvestigationFeedback", back_populates="investigation", lazy="select"
    )
