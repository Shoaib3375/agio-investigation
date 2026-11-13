"""Repository for the investigation_reports table."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.investigation_report import InvestigationReport


async def insert(
    session: AsyncSession, report: InvestigationReport
) -> InvestigationReport:
    """Persist a report and return it with the DB-generated id."""
    session.add(report)
    await session.flush()
    return report


async def get_latest(
    session: AsyncSession,
    investigation_id: str,
) -> InvestigationReport | None:
    """Return the most recently generated report for an investigation."""
    result = await session.execute(
        select(InvestigationReport)
        .where(InvestigationReport.investigation_id == investigation_id)
        .order_by(InvestigationReport.generated_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def get_all(
    session: AsyncSession,
    investigation_id: str,
) -> list[InvestigationReport]:
    """Return all reports for an investigation ordered by version ascending."""
    result = await session.execute(
        select(InvestigationReport)
        .where(InvestigationReport.investigation_id == investigation_id)
        .order_by(InvestigationReport.report_version)
    )
    return list(result.scalars().all())


async def next_version(
    session: AsyncSession,
    investigation_id: str,
) -> int:
    """Return the next report_version number for an investigation (1-based)."""
    result = await session.execute(
        select(func.max(InvestigationReport.report_version)).where(
            InvestigationReport.investigation_id == investigation_id
        )
    )
    current_max = result.scalar()
    return (current_max or 0) + 1
