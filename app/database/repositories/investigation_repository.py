"""Repository for the investigations table.

Deduplication policy:
  An investigation is considered active if it targets the same entity
  (entity_type + external_reference) and its status is NOT in a terminal state
  (completed, failed).  New events for the same entity are correlated into
  the existing active investigation rather than creating a new one.

  A new investigation is created when:
    - No active investigation exists for the entity.
    - The existing investigation is in a terminal state (completed / failed).
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.investigation import Investigation

logger = structlog.get_logger()

# Non-terminal statuses — events for these investigations are correlated in.
_ACTIVE_STATUSES = {"queued", "collecting", "analyzing", "review_required"}
_TERMINAL_STATUSES = {"completed", "failed"}


async def get_by_id(session: AsyncSession, investigation_id: str) -> Investigation | None:
    result = await session.execute(
        select(Investigation).where(Investigation.id == investigation_id)
    )
    return result.scalars().first()


async def get_active_for_entity(
    session: AsyncSession,
    entity_type: str,
    external_reference: str,
) -> Investigation | None:
    """Return the most recently created non-terminal investigation for this entity."""
    result = await session.execute(
        select(Investigation)
        .where(
            Investigation.entity_type == entity_type,
            Investigation.external_reference == external_reference,
            Investigation.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(Investigation.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def create(
    session: AsyncSession,
    *,
    external_reference: str,
    entity_type: str,
    trigger_event_id: str | None = None,
    correlation_id: str | None = None,
    investigation_type: str = "order_lifecycle",
    priority: str = "normal",
) -> Investigation:
    """Insert a new investigation row and return it."""
    investigation = Investigation(
        external_reference=external_reference,
        entity_type=entity_type,
        trigger_event_id=trigger_event_id,
        correlation_id=correlation_id,
        investigation_type=investigation_type,
        priority=priority,
        status="queued",
        attempt_count=0,
    )
    session.add(investigation)
    await session.flush()  # populate server-generated id
    logger.info(
        "investigation.created",
        investigation_id=investigation.id,
        entity_type=entity_type,
        external_reference=external_reference,
        investigation_type=investigation_type,
    )
    return investigation


async def set_status(
    session: AsyncSession,
    investigation_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Transition an investigation to a new status."""
    values: dict = {"status": status, "updated_at": datetime.now(tz=UTC)}
    if status == "collecting":
        values["started_at"] = datetime.now(tz=UTC)
        values["attempt_count"] = Investigation.attempt_count + 1
    elif status in _TERMINAL_STATUSES:
        values["completed_at"] = datetime.now(tz=UTC)
    if error is not None:
        values["last_error"] = error[:2000]  # guard against huge stack traces

    await session.execute(
        update(Investigation)
        .where(Investigation.id == investigation_id)
        .values(**values)
    )


async def list_queued(
    session: AsyncSession,
    limit: int = 50,
) -> list[Investigation]:
    """Return up to ``limit`` queued investigations ordered by creation time."""
    result = await session.execute(
        select(Investigation)
        .where(Investigation.status == "queued")
        .order_by(Investigation.created_at)
        .limit(limit)
    )
    return list(result.scalars().all())
