"""SQLAlchemy ORM model for the event_inbox table.

Every inbound RabbitMQ event is persisted here before it is ACKed.
Duplicate delivery is harmless: event_id has a UNIQUE index and the
repository uses INSERT ... ON CONFLICT DO NOTHING.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.schemas.events import EventEnvelope


class EventInbox(Base):
    """Durable inbox for all inbound RabbitMQ events."""

    __tablename__ = "event_inbox"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default="gen_random_uuid()",
    )
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    event_version: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="1.0"
    )
    producer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processing_status: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="received"
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("uq_event_inbox_event_id", "event_id", unique=True),
        Index("ix_event_inbox_event_type", "event_type"),
        Index("ix_event_inbox_entity", "entity_type", "entity_id"),
        Index("ix_event_inbox_status", "processing_status", "received_at"),
    )

    @classmethod
    def create_from_envelope(cls, envelope: EventEnvelope) -> "EventInbox":
        """Build an EventInbox instance from a validated EventEnvelope.

        Does NOT set ``id`` or ``received_at`` — the database generates them.
        """
        return cls(
            event_id=envelope.event_id,
            event_type=envelope.event_type,
            event_version=envelope.event_version,
            producer=envelope.producer,
            entity_type=envelope.entity.type,
            entity_id=envelope.entity.id,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            occurred_at=envelope.occurred_at,
            received_at=datetime.now(tz=UTC),
            payload=envelope.model_dump(mode="json"),
            processing_status="received",
            attempt_count=0,
        )
