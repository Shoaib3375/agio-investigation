"""SQLAlchemy ORM model for the evidence_records table.

Each row stores a single piece of collected evidence with full provenance:
source system, record type, record ID, retrieval time, content hash,
and the sanitised JSON snapshot.

is_verified must only be set True after a genuine verification step
(schema match, referential check, financial cross-check) — not simply
because the API returned HTTP 200.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"

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
    # Category: order | timeline | ledger | blockchain | provider |
    #           account | risk | reconciliation | withdrawal | deposit
    evidence_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Always "agio-paymentexchange" for the initial integration.
    source_system: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default="agio-paymentexchange"
    )
    # Laravel model type: order | order_event | blockchain_deposit | journal_entry | etc.
    source_record_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # Public identifier of the source record (UUID, integer id, or compound key).
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Laravel resource version / response schema version if available.
    source_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # SHA-256 hex digest of the canonicalised JSON snapshot.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # True only when a genuine validation was performed (not just HTTP 200).
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Description of how verification was performed (e.g. "schema_match", "referential_check").
    verification_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Sanitised evidence snapshot — no secrets, tokens, or raw provider responses.
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=UTC),
        server_default="now()",
    )

    __table_args__ = (
        Index("ix_evidence_investigation", "investigation_id"),
        Index("ix_evidence_type", "evidence_type"),
        Index("ix_evidence_source", "source_record_type", "source_record_id"),
    )

    investigation: Mapped["Investigation"] = relationship(  # noqa: F821
        "Investigation", back_populates="evidence_records"
    )
