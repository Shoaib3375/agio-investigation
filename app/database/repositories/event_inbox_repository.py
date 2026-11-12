"""Repository for the event_inbox table.

All methods are async and work with an AsyncSession. The caller is responsible
for committing the session after insert_if_not_exists.
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_inbox import EventInbox
from app.schemas.events import EventEnvelope

logger = structlog.get_logger()

# processing_status values
STATUS_RECEIVED = "received"
STATUS_PROCESSING = "processing"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"

_MAX_RETRIES_DEFAULT = 3


async def insert_if_not_exists(
    session: AsyncSession, envelope: EventEnvelope
) -> tuple[EventInbox, bool]:
    """Insert the envelope into event_inbox if it has not been seen before.

    Uses ``INSERT ... ON CONFLICT DO NOTHING`` so duplicate event_ids are
    silently ignored.  Returns ``(record, created)`` where ``created`` is
    ``True`` for a fresh insertion and ``False`` for a duplicate.
    """
    row = EventInbox.create_from_envelope(envelope)

    stmt = (
        pg_insert(EventInbox)
        .values(
            event_id=row.event_id,
            event_type=row.event_type,
            event_version=row.event_version,
            producer=row.producer,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            correlation_id=row.correlation_id,
            causation_id=row.causation_id,
            occurred_at=row.occurred_at,
            received_at=row.received_at,
            payload=row.payload,
            processing_status=row.processing_status,
            attempt_count=row.attempt_count,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(EventInbox)
    )

    result = await session.execute(stmt)
    inserted = result.scalars().first()

    if inserted is not None:
        return inserted, True

    # Conflict: fetch the existing row.
    existing_stmt = select(EventInbox).where(EventInbox.event_id == envelope.event_id)
    existing_result = await session.execute(existing_stmt)
    existing = existing_result.scalars().one()
    return existing, False


async def mark_processing(session: AsyncSession, event_inbox_id: str) -> None:
    """Transition status to 'processing' and increment attempt_count."""
    stmt = (
        update(EventInbox)
        .where(EventInbox.id == event_inbox_id)
        .values(
            processing_status=STATUS_PROCESSING,
            attempt_count=EventInbox.attempt_count + 1,
        )
    )
    await session.execute(stmt)


async def mark_processed(session: AsyncSession, event_inbox_id: str) -> None:
    """Transition status to 'processed' and record processed_at timestamp."""
    stmt = (
        update(EventInbox)
        .where(EventInbox.id == event_inbox_id)
        .values(
            processing_status=STATUS_PROCESSED,
            processed_at=datetime.now(tz=UTC),
        )
    )
    await session.execute(stmt)


async def mark_failed(
    session: AsyncSession, event_inbox_id: str, error: str
) -> None:
    """Transition status to 'failed' and store the error message."""
    stmt = (
        update(EventInbox)
        .where(EventInbox.id == event_inbox_id)
        .values(
            processing_status=STATUS_FAILED,
            last_error=error,
        )
    )
    await session.execute(stmt)


async def get_pending(
    session: AsyncSession,
    limit: int = 100,
    max_retries: int = _MAX_RETRIES_DEFAULT,
) -> list[EventInbox]:
    """Return rows in received/failed state that have not exceeded max_retries.

    Ordered by received_at ascending so oldest events are processed first.
    """
    stmt = (
        select(EventInbox)
        .where(
            EventInbox.processing_status.in_([STATUS_RECEIVED, STATUS_FAILED]),
            EventInbox.attempt_count < max_retries,
        )
        .order_by(EventInbox.received_at)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
