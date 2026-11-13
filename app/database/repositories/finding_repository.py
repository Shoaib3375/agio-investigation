"""Repository for the investigation_findings table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.investigation_finding import InvestigationFinding


async def insert(
    session: AsyncSession, finding: InvestigationFinding
) -> InvestigationFinding:
    """Persist a new finding and return it with the DB-generated id."""
    session.add(finding)
    await session.flush()
    return finding


async def insert_many(
    session: AsyncSession, findings: list[InvestigationFinding]
) -> list[InvestigationFinding]:
    """Persist a batch of findings and return them."""
    for f in findings:
        session.add(f)
    await session.flush()
    return findings


async def get_by_investigation(
    session: AsyncSession,
    investigation_id: str,
) -> list[InvestigationFinding]:
    """Return all findings for an investigation ordered by severity then created_at."""
    _severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "informational": 4}
    result = await session.execute(
        select(InvestigationFinding)
        .where(InvestigationFinding.investigation_id == investigation_id)
        .order_by(InvestigationFinding.created_at)
    )
    findings = list(result.scalars().all())
    # Sort by severity importance in Python (avoid dialect-specific CASE in SQL).
    findings.sort(key=lambda f: _severity_order.get(f.severity, 99))
    return findings


async def get_by_code(
    session: AsyncSession,
    investigation_id: str,
    finding_code: str,
) -> list[InvestigationFinding]:
    """Return findings with a specific code for an investigation."""
    result = await session.execute(
        select(InvestigationFinding).where(
            InvestigationFinding.investigation_id == investigation_id,
            InvestigationFinding.finding_code == finding_code,
        )
    )
    return list(result.scalars().all())
