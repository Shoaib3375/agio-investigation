"""Repository for the evidence_records table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence_record import EvidenceRecord


async def insert(session: AsyncSession, record: EvidenceRecord) -> EvidenceRecord:
    """Persist a new EvidenceRecord and return it with the DB-generated id."""
    session.add(record)
    await session.flush()
    return record


async def get_by_investigation(
    session: AsyncSession,
    investigation_id: str,
) -> list[EvidenceRecord]:
    """Return all evidence records for an investigation ordered by retrieved_at."""
    result = await session.execute(
        select(EvidenceRecord)
        .where(EvidenceRecord.investigation_id == investigation_id)
        .order_by(EvidenceRecord.retrieved_at)
    )
    return list(result.scalars().all())


async def get_by_type(
    session: AsyncSession,
    investigation_id: str,
    evidence_type: str,
) -> list[EvidenceRecord]:
    """Return evidence records of a specific type for an investigation."""
    result = await session.execute(
        select(EvidenceRecord)
        .where(
            EvidenceRecord.investigation_id == investigation_id,
            EvidenceRecord.evidence_type == evidence_type,
        )
        .order_by(EvidenceRecord.retrieved_at)
    )
    return list(result.scalars().all())


async def exists_for_source(
    session: AsyncSession,
    investigation_id: str,
    source_record_type: str,
    source_record_id: str,
) -> bool:
    """Return True if evidence for this source record is already stored."""
    result = await session.execute(
        select(EvidenceRecord.id).where(
            EvidenceRecord.investigation_id == investigation_id,
            EvidenceRecord.source_record_type == source_record_type,
            EvidenceRecord.source_record_id == source_record_id,
        )
    )
    return result.first() is not None
