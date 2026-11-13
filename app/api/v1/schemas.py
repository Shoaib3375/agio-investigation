"""API response schemas for the admin-facing investigation API.

These are the outbound shapes returned by the FastAPI endpoints.
They are distinct from the inbound Laravel evidence schemas.
All monetary values remain Decimal strings when present.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Shared / pagination
# ---------------------------------------------------------------------------


class PaginatedMeta(BaseModel):
    page: int
    per_page: int
    total: int
    total_pages: int


class PaginatedResponse(BaseModel):
    data: list[Any]
    meta: PaginatedMeta


# ---------------------------------------------------------------------------
# Investigation
# ---------------------------------------------------------------------------


class InvestigationSummary(BaseModel):
    id: str
    external_reference: str
    entity_type: str
    investigation_type: str
    status: str
    priority: str
    trigger_event_id: str | None = None
    correlation_id: str | None = None
    attempt_count: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InvestigationDetail(InvestigationSummary):
    last_error: str | None = None


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class EvidenceRecordOut(BaseModel):
    id: str
    investigation_id: str
    evidence_type: str
    source_system: str
    source_record_type: str
    source_record_id: str
    source_version: str | None = None
    retrieved_at: datetime
    content_hash: str
    is_verified: bool
    verification_method: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EvidenceRecordWithSnapshot(EvidenceRecordOut):
    """Full evidence record including the snapshot — only returned on explicit /evidence/{id}."""
    snapshot: dict[str, Any]


# ---------------------------------------------------------------------------
# Timeline (reconstructed from order_events snapshot)
# ---------------------------------------------------------------------------


class TimelineEvent(BaseModel):
    id: int | None = None
    from_status: str | None = None
    to_status: str
    actor_type: str
    actor_id: int | None = None
    reason: str | None = None
    metadata: dict[str, Any] | None = None
    occurred_at: datetime


class TimelineResponse(BaseModel):
    investigation_id: str
    external_reference: str
    events: list[TimelineEvent]
    source: str = "order_events_snapshot"


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class FindingOut(BaseModel):
    id: str
    investigation_id: str
    finding_code: str
    category: str
    severity: str
    confidence: Decimal | None = None
    title: str
    description: str
    evidence_ids: list[str]
    source: str
    rule_version: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


class ReportOut(BaseModel):
    id: str
    investigation_id: str
    report_version: int
    summary: str | None = None
    observed_facts: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    missing_evidence: list[dict[str, Any]]
    recommendations: list[Any]
    limitations: list[Any]
    model_name: str | None = None
    prompt_version: str | None = None
    generated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


class FeedbackIn(BaseModel):
    finding_id: str | None = None
    reviewer_reference: str
    decision: str  # accepted | rejected | needs_more_evidence
    feedback: str | None = None


class FeedbackOut(BaseModel):
    id: str
    investigation_id: str
    finding_id: str | None = None
    reviewer_reference: str
    decision: str
    feedback: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Request-review
# ---------------------------------------------------------------------------


class RequestReviewIn(BaseModel):
    reason: str | None = None


class RequestReviewOut(BaseModel):
    investigation_id: str
    status: str
    message: str
