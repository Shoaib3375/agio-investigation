"""Admin-facing investigation API — read-only except feedback and request-review.

All endpoints require the Authorization: Bearer <ADMIN_API_KEY> header.

Endpoints:
  GET  /investigations                    — paginated list with filters
  GET  /investigations/{id}               — full investigation detail
  GET  /investigations/{id}/evidence      — evidence record list (no snapshots)
  GET  /investigations/{id}/evidence/{eid} — single evidence record with snapshot
  GET  /investigations/{id}/timeline      — reconstructed timeline from order_events snapshot
  GET  /investigations/{id}/findings      — structured findings ordered by severity
  GET  /investigations/{id}/report        — latest investigation report
  POST /investigations/{id}/feedback      — record human reviewer decision
  POST /investigations/{id}/request-review — re-queue for another analysis pass

No endpoint modifies Laravel state. No financial operations here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import AdminAuth, DBSession, Pagination
from app.api.v1.schemas import (
    EvidenceRecordOut,
    EvidenceRecordWithSnapshot,
    FeedbackIn,
    FeedbackOut,
    FindingOut,
    InvestigationDetail,
    InvestigationSummary,
    PaginatedMeta,
    PaginatedResponse,
    ReportOut,
    RequestReviewIn,
    RequestReviewOut,
    TimelineEvent,
    TimelineResponse,
)
from app.database.repositories import (
    evidence_record_repository,
    feedback_repository,
    finding_repository,
    investigation_repository,
    report_repository,
)
from app.models.evidence_record import EvidenceRecord
from app.models.investigation import Investigation
from app.models.investigation_feedback import InvestigationFeedback
from app.models.investigation_report import InvestigationReport

router = APIRouter(prefix="/investigations", tags=["investigations"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_investigation_or_404(
    investigation_id: str, session: AsyncSession
) -> Investigation:
    inv = await investigation_repository.get_by_id(session, investigation_id)
    if inv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"Investigation '{investigation_id}' not found."},
        )
    return inv


# ---------------------------------------------------------------------------
# GET /investigations
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse)
async def list_investigations(
    _auth: AdminAuth,
    session: DBSession,
    entity_type: str | None = Query(default=None),
    investigation_status: str | None = Query(default=None, alias="status"),
    priority: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse:
    """Return a paginated list of investigations with optional filters."""
    pagination = Pagination(page=page, per_page=per_page)

    base_query = select(Investigation)
    count_query = select(func.count()).select_from(Investigation)

    if entity_type:
        base_query = base_query.where(Investigation.entity_type == entity_type)
        count_query = count_query.where(Investigation.entity_type == entity_type)
    if investigation_status:
        base_query = base_query.where(Investigation.status == investigation_status)
        count_query = count_query.where(Investigation.status == investigation_status)
    if priority:
        base_query = base_query.where(Investigation.priority == priority)
        count_query = count_query.where(Investigation.priority == priority)

    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    rows_result = await session.execute(
        base_query
        .order_by(Investigation.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    rows = list(rows_result.scalars().all())

    total_pages = max(1, (total + per_page - 1) // per_page)

    return PaginatedResponse(
        data=[InvestigationSummary.model_validate(r) for r in rows],
        meta=PaginatedMeta(page=page, per_page=per_page, total=total, total_pages=total_pages),
    )


# ---------------------------------------------------------------------------
# GET /investigations/{id}
# ---------------------------------------------------------------------------


@router.get("/{investigation_id}", response_model=InvestigationDetail)
async def get_investigation(
    investigation_id: str,
    _auth: AdminAuth,
    session: DBSession,
) -> InvestigationDetail:
    inv = await _get_investigation_or_404(investigation_id, session)
    return InvestigationDetail.model_validate(inv)


# ---------------------------------------------------------------------------
# GET /investigations/{id}/evidence
# ---------------------------------------------------------------------------


@router.get("/{investigation_id}/evidence", response_model=list[EvidenceRecordOut])
async def list_evidence(
    investigation_id: str,
    _auth: AdminAuth,
    session: DBSession,
) -> list[EvidenceRecordOut]:
    """Return evidence record list without snapshots (snapshots may be large)."""
    await _get_investigation_or_404(investigation_id, session)
    records = await evidence_record_repository.get_by_investigation(session, investigation_id)
    return [EvidenceRecordOut.model_validate(r) for r in records]


# ---------------------------------------------------------------------------
# GET /investigations/{id}/evidence/{evidence_id}
# ---------------------------------------------------------------------------


@router.get("/{investigation_id}/evidence/{evidence_id}", response_model=EvidenceRecordWithSnapshot)
async def get_evidence_detail(
    investigation_id: str,
    evidence_id: str,
    _auth: AdminAuth,
    session: DBSession,
) -> EvidenceRecordWithSnapshot:
    """Return a single evidence record including its full snapshot."""
    await _get_investigation_or_404(investigation_id, session)
    result = await session.execute(
        select(EvidenceRecord).where(
            EvidenceRecord.id == evidence_id,
            EvidenceRecord.investigation_id == investigation_id,
        )
    )
    record = result.scalars().first()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"Evidence record '{evidence_id}' not found."},
        )
    return EvidenceRecordWithSnapshot.model_validate(record)


# ---------------------------------------------------------------------------
# GET /investigations/{id}/timeline
# ---------------------------------------------------------------------------


@router.get("/{investigation_id}/timeline", response_model=TimelineResponse)
async def get_timeline(
    investigation_id: str,
    _auth: AdminAuth,
    session: DBSession,
) -> TimelineResponse:
    """Return the reconstructed order timeline from the stored order_events snapshot.

    The timeline is derived from the 'timeline' EvidenceRecord snapshot —
    not fetched live from Laravel. This is the authoritative evidence-grounded
    view at the time of investigation.
    """
    inv = await _get_investigation_or_404(investigation_id, session)

    timeline_records = await evidence_record_repository.get_by_type(
        session, investigation_id, "timeline"
    )
    if not timeline_records:
        return TimelineResponse(
            investigation_id=investigation_id,
            external_reference=inv.external_reference,
            events=[],
            source="no_timeline_evidence",
        )

    snapshot = timeline_records[0].snapshot or {}
    raw_events = snapshot.get("order_events", [])
    events: list[TimelineEvent] = []
    for ev in raw_events:
        occurred = ev.get("created_at")
        if not occurred:
            continue
        try:
            occurred_dt = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        events.append(TimelineEvent(
            id=ev.get("id"),
            from_status=ev.get("from_status"),
            to_status=ev.get("to_status", ""),
            actor_type=ev.get("actor_type", "unknown"),
            actor_id=ev.get("actor_id"),
            reason=ev.get("reason"),
            metadata=ev.get("metadata"),
            occurred_at=occurred_dt,
        ))

    # Sort ascending by occurred_at (source events may arrive out of order)
    events.sort(key=lambda e: e.occurred_at)

    return TimelineResponse(
        investigation_id=investigation_id,
        external_reference=inv.external_reference,
        events=events,
        source="order_events_snapshot",
    )


# ---------------------------------------------------------------------------
# GET /investigations/{id}/findings
# ---------------------------------------------------------------------------


@router.get("/{investigation_id}/findings", response_model=list[FindingOut])
async def list_findings(
    investigation_id: str,
    _auth: AdminAuth,
    session: DBSession,
    severity: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> list[FindingOut]:
    """Return findings ordered by severity. Optionally filter by severity or category."""
    await _get_investigation_or_404(investigation_id, session)
    findings = await finding_repository.get_by_investigation(session, investigation_id)
    if severity:
        findings = [f for f in findings if f.severity == severity]
    if category:
        findings = [f for f in findings if f.category == category]
    return [FindingOut.model_validate(f) for f in findings]


# ---------------------------------------------------------------------------
# GET /investigations/{id}/report
# ---------------------------------------------------------------------------


@router.get("/{investigation_id}/report", response_model=ReportOut)
async def get_report(
    investigation_id: str,
    _auth: AdminAuth,
    session: DBSession,
    version: int | None = Query(default=None, description="Specific report version; defaults to latest"),
) -> ReportOut:
    """Return the latest investigation report, or a specific version."""
    await _get_investigation_or_404(investigation_id, session)

    if version is not None:
        result = await session.execute(
            select(InvestigationReport).where(
                InvestigationReport.investigation_id == investigation_id,
                InvestigationReport.report_version == version,
            )
        )
        report = result.scalars().first()
    else:
        report = await report_repository.get_latest(session, investigation_id)

    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "No report available for this investigation."},
        )
    return ReportOut.model_validate(report)


# ---------------------------------------------------------------------------
# POST /investigations/{id}/feedback
# ---------------------------------------------------------------------------


@router.post("/{investigation_id}/feedback", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    investigation_id: str,
    body: FeedbackIn,
    _auth: AdminAuth,
    session: DBSession,
) -> FeedbackOut:
    """Record a human reviewer decision on this investigation or one of its findings.

    decision must be one of: accepted | rejected | needs_more_evidence
    """
    await _get_investigation_or_404(investigation_id, session)

    valid_decisions = {"accepted", "rejected", "needs_more_evidence"}
    if body.decision not in valid_decisions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_DECISION",
                "message": f"decision must be one of {sorted(valid_decisions)}.",
            },
        )

    # Optionally validate that finding_id belongs to this investigation
    if body.finding_id:
        from app.models.investigation_finding import InvestigationFinding
        f_result = await session.execute(
            select(InvestigationFinding).where(
                InvestigationFinding.id == body.finding_id,
                InvestigationFinding.investigation_id == investigation_id,
            )
        )
        if f_result.scalars().first() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "NOT_FOUND",
                    "message": f"Finding '{body.finding_id}' not found in this investigation.",
                },
            )

    fb = InvestigationFeedback(
        investigation_id=investigation_id,
        finding_id=body.finding_id,
        reviewer_reference=body.reviewer_reference,
        decision=body.decision,
        feedback=body.feedback,
    )
    saved = await feedback_repository.insert(session, fb)
    await session.commit()
    return FeedbackOut.model_validate(saved)


# ---------------------------------------------------------------------------
# POST /investigations/{id}/request-review
# ---------------------------------------------------------------------------


@router.post("/{investigation_id}/request-review", response_model=RequestReviewOut)
async def request_review(
    investigation_id: str,
    body: RequestReviewIn,
    _auth: AdminAuth,
    session: DBSession,
) -> RequestReviewOut:
    """Re-queue an investigation for another analysis pass.

    Transitions status back to 'queued' so the next worker run picks it up.
    Only valid when the investigation is in a terminal or review_required state.
    """
    inv = await _get_investigation_or_404(investigation_id, session)

    _re_queueable = {"completed", "failed", "review_required"}
    if inv.status not in _re_queueable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INVALID_STATE",
                "message": (
                    f"Cannot request review for investigation in status '{inv.status}'. "
                    f"Must be one of {sorted(_re_queueable)}."
                ),
            },
        )

    await investigation_repository.set_status(session, investigation_id, "queued")
    await session.commit()

    return RequestReviewOut(
        investigation_id=investigation_id,
        status="queued",
        message="Investigation re-queued for review.",
    )
