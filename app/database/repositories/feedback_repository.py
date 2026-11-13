"""Repository for the investigation_feedback table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.investigation_feedback import InvestigationFeedback


async def insert(
    session: AsyncSession, feedback: InvestigationFeedback
) -> InvestigationFeedback:
    """Persist reviewer feedback and return it with the DB-generated id."""
    session.add(feedback)
    await session.flush()
    return feedback


async def get_by_investigation(
    session: AsyncSession,
    investigation_id: str,
) -> list[InvestigationFeedback]:
    """Return all feedback for an investigation ordered by created_at."""
    result = await session.execute(
        select(InvestigationFeedback)
        .where(InvestigationFeedback.investigation_id == investigation_id)
        .order_by(InvestigationFeedback.created_at)
    )
    return list(result.scalars().all())
