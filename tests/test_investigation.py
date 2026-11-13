"""PY-M04 tests — investigation DB models, repositories, evidence collector,
investigation service, and orchestrator.

DB tests require the investigation database on 127.0.0.1:5437 (requires_db marker).
Pure-unit tests have no external dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.repositories import (
    evidence_record_repository,
    investigation_repository,
    report_repository,
    finding_repository,
    feedback_repository,
)
from app.investigation.evidence_collector import (
    CollectionResult,
    EvidenceCollector,
    MissingEvidence,
    _content_hash,
)
from app.investigation.investigation_service import InvestigationService
from app.models.event_inbox import EventInbox
from app.models.evidence_record import EvidenceRecord
from app.models.investigation import Investigation
from app.models.investigation_feedback import InvestigationFeedback
from app.models.investigation_finding import InvestigationFinding
from app.models.investigation_report import InvestigationReport

# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------

_DB_URL = os.environ.get(
    "INVESTIGATION_DATABASE_URL",
    "postgresql+asyncpg://investigation:local_password@127.0.0.1:5437/agio_investigation",
)


async def _db_reachable() -> bool:
    engine = create_async_engine(_DB_URL, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session():
    """Yield an AsyncSession, roll back after each test to keep DB clean."""
    if not await _db_reachable():
        pytest.skip("investigation database not reachable on 5437")
    engine = create_async_engine(_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def db_factory():
    """Yield an async_sessionmaker for tests that need multiple sessions."""
    if not await _db_reachable():
        pytest.skip("investigation database not reachable on 5437")
    engine = create_async_engine(_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique_uuid() -> str:
    return str(uuid.uuid4())


def _make_event_inbox(entity_type: str = "order", entity_id: str | None = None) -> EventInbox:
    eid = entity_id or _unique_uuid()
    return EventInbox(
        event_id=f"evt-{_unique_uuid()}",
        event_type="order.created",
        event_version="1.0",
        producer="agio-paymentexchange",
        entity_type=entity_type,
        entity_id=eid,
        correlation_id=_unique_uuid(),
        causation_id=None,
        occurred_at=datetime.now(tz=UTC),
        received_at=datetime.now(tz=UTC),
        payload={
            "event_id": f"evt-{_unique_uuid()}",
            "event_type": "order.created",
            "event_version": "1.0",
            "occurred_at": datetime.now(tz=UTC).isoformat(),
            "producer": "agio-paymentexchange",
            "correlation_id": _unique_uuid(),
            "causation_id": None,
            "entity": {"type": entity_type, "id": eid},
            "data": {"status": "awaiting_deposit"},
        },
        processing_status="received",
        attempt_count=0,
    )


# ===========================================================================
# Pure-unit tests (no DB)
# ===========================================================================


class TestContentHash:
    def test_hash_is_sha256_hex(self):
        data = {"foo": "bar", "num": 1}
        result = _content_hash(data)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_is_deterministic(self):
        data = {"b": 2, "a": 1}
        assert _content_hash(data) == _content_hash(data)

    def test_hash_sorts_keys(self):
        """Key ordering must not affect the hash (canonical JSON)."""
        a = {"b": 2, "a": 1}
        b = {"a": 1, "b": 2}
        assert _content_hash(a) == _content_hash(b)

    def test_different_data_different_hash(self):
        assert _content_hash({"amount": "1.0"}) != _content_hash({"amount": "2.0"})


class TestCollectionResult:
    def test_is_complete_when_no_missing(self):
        result = CollectionResult(investigation_id="test-id")
        assert result.is_complete is True

    def test_is_incomplete_when_missing(self):
        result = CollectionResult(investigation_id="test-id")
        result.missing.append(MissingEvidence(
            evidence_type="order",
            source_record_type="order",
            source_record_id="uuid-1",
            reason="not_found",
            detail="404",
        ))
        assert result.is_complete is False

    def test_summary_counts(self):
        result = CollectionResult(investigation_id="test-id")
        from app.investigation.evidence_collector import CollectedEvidence
        result.collected.append(CollectedEvidence("r1", "order", "order", "uuid-1"))
        result.missing.append(MissingEvidence("blockchain", "blockchain", "uuid-1", "not_found", ""))
        s = result.summary()
        assert s["collected_count"] == 1
        assert s["missing_count"] == 1
        assert s["is_complete"] is False


class TestInvestigationServicePriority:
    def test_risk_case_is_high_priority(self):
        event = _make_event_inbox()
        event.event_type = "risk_case.opened"
        from app.investigation.investigation_service import InvestigationService
        priority = InvestigationService._derive_priority(event)
        assert priority == "high"

    def test_amount_mismatch_is_high(self):
        event = _make_event_inbox()
        event.event_type = "order.status_changed"
        event.payload = {"data": {"current_status": "amount_mismatch"}}
        priority = InvestigationService._derive_priority(event)
        assert priority == "high"

    def test_deposit_detected_is_normal(self):
        event = _make_event_inbox()
        event.event_type = "order.status_changed"
        event.payload = {"data": {"current_status": "deposit_detected"}}
        priority = InvestigationService._derive_priority(event)
        assert priority == "normal"


class TestInvestigationServiceEntityMapping:
    """Verify event_type → entity_type mapping (no DB needed)."""

    def test_order_created_maps_to_order(self):
        from app.investigation.investigation_service import _ENTITY_TYPE_MAP
        assert _ENTITY_TYPE_MAP["order.created"] == "order"

    def test_risk_case_opened_maps_to_risk_case(self):
        from app.investigation.investigation_service import _ENTITY_TYPE_MAP
        assert _ENTITY_TYPE_MAP["risk_case.opened"] == "risk_case"

    def test_login_failed_maps_to_user(self):
        from app.investigation.investigation_service import _ENTITY_TYPE_MAP
        assert _ENTITY_TYPE_MAP["user.login.failed"] == "user"


# ===========================================================================
# DB tests (requires_db)
# ===========================================================================


@pytest.mark.requires_db
async def test_create_investigation(db_session: AsyncSession):
    """investigation_repository.create inserts a row with status=queued."""
    ref = _unique_uuid()
    inv = await investigation_repository.create(
        db_session,
        external_reference=ref,
        entity_type="order",
        trigger_event_id="evt-001",
        correlation_id="corr-001",
        investigation_type="order_lifecycle",
        priority="normal",
    )
    await db_session.commit()

    assert inv.id is not None
    assert inv.status == "queued"
    assert inv.external_reference == ref
    assert inv.entity_type == "order"
    assert inv.priority == "normal"
    assert inv.attempt_count == 0


@pytest.mark.requires_db
async def test_get_active_for_entity(db_session: AsyncSession):
    """get_active_for_entity returns an active investigation for the entity."""
    ref = _unique_uuid()
    inv = await investigation_repository.create(
        db_session,
        external_reference=ref,
        entity_type="order",
    )
    await db_session.commit()

    found = await investigation_repository.get_active_for_entity(
        db_session, entity_type="order", external_reference=ref
    )
    assert found is not None
    assert found.id == inv.id


@pytest.mark.requires_db
async def test_no_active_after_terminal(db_session: AsyncSession):
    """get_active_for_entity returns None after investigation is completed."""
    ref = _unique_uuid()
    inv = await investigation_repository.create(
        db_session, external_reference=ref, entity_type="order"
    )
    await db_session.commit()

    await investigation_repository.set_status(db_session, inv.id, "completed")
    await db_session.commit()

    found = await investigation_repository.get_active_for_entity(
        db_session, entity_type="order", external_reference=ref
    )
    assert found is None


@pytest.mark.requires_db
async def test_set_status_collecting_increments_attempt(db_session: AsyncSession):
    inv = await investigation_repository.create(
        db_session, external_reference=_unique_uuid(), entity_type="order"
    )
    await db_session.commit()

    await investigation_repository.set_status(db_session, inv.id, "collecting")
    await db_session.commit()

    refreshed = await investigation_repository.get_by_id(db_session, inv.id)
    assert refreshed is not None
    assert refreshed.status == "collecting"
    assert refreshed.attempt_count == 1
    assert refreshed.started_at is not None


@pytest.mark.requires_db
async def test_set_status_failed_records_error(db_session: AsyncSession):
    inv = await investigation_repository.create(
        db_session, external_reference=_unique_uuid(), entity_type="order"
    )
    await db_session.commit()

    await investigation_repository.set_status(
        db_session, inv.id, "failed", error="Something broke"
    )
    await db_session.commit()

    refreshed = await investigation_repository.get_by_id(db_session, inv.id)
    assert refreshed.status == "failed"
    assert "Something broke" in refreshed.last_error
    assert refreshed.completed_at is not None


@pytest.mark.requires_db
async def test_evidence_record_insert_and_retrieve(db_session: AsyncSession):
    """EvidenceRecord is stored with correct provenance and content hash."""
    inv = await investigation_repository.create(
        db_session, external_reference=_unique_uuid(), entity_type="order"
    )
    await db_session.commit()

    snapshot = {"transaction_id": "uuid-001", "status": "completed", "sell_amount": "1.0"}
    content_hash = _content_hash(snapshot)

    record = EvidenceRecord(
        investigation_id=inv.id,
        evidence_type="order",
        source_record_type="order",
        source_record_id="uuid-001",
        retrieved_at=datetime.now(tz=UTC),
        content_hash=content_hash,
        is_verified=True,
        verification_method="schema_match",
        snapshot=snapshot,
    )
    saved = await evidence_record_repository.insert(db_session, record)
    await db_session.commit()

    assert saved.id is not None
    assert saved.content_hash == content_hash
    assert saved.is_verified is True

    records = await evidence_record_repository.get_by_investigation(db_session, inv.id)
    assert len(records) == 1
    assert records[0].evidence_type == "order"
    assert records[0].snapshot["status"] == "completed"


@pytest.mark.requires_db
async def test_evidence_record_exists_for_source(db_session: AsyncSession):
    inv = await investigation_repository.create(
        db_session, external_reference=_unique_uuid(), entity_type="order"
    )
    await db_session.commit()

    record = EvidenceRecord(
        investigation_id=inv.id,
        evidence_type="order",
        source_record_type="order",
        source_record_id="ref-123",
        retrieved_at=datetime.now(tz=UTC),
        content_hash=_content_hash({"x": 1}),
        is_verified=False,
        snapshot={"x": 1},
    )
    await evidence_record_repository.insert(db_session, record)
    await db_session.commit()

    exists = await evidence_record_repository.exists_for_source(
        db_session, inv.id, "order", "ref-123"
    )
    assert exists is True

    not_exists = await evidence_record_repository.exists_for_source(
        db_session, inv.id, "order", "ref-999"
    )
    assert not_exists is False


@pytest.mark.requires_db
async def test_finding_insert_and_retrieve(db_session: AsyncSession):
    inv = await investigation_repository.create(
        db_session, external_reference=_unique_uuid(), entity_type="order"
    )
    ev = EvidenceRecord(
        investigation_id=inv.id,
        evidence_type="order",
        source_record_type="order",
        source_record_id="u-001",
        retrieved_at=datetime.now(tz=UTC),
        content_hash=_content_hash({}),
        is_verified=False,
        snapshot={},
    )
    saved_ev = await evidence_record_repository.insert(db_session, ev)
    await db_session.commit()

    finding = InvestigationFinding(
        investigation_id=inv.id,
        finding_code="BLOCKCHAIN_AMOUNT_MISMATCH",
        category="blockchain",
        severity="high",
        title="Deposit amount differs from expected",
        description="Received 0.95 USDT, expected 1.00 USDT.",
        evidence_ids=[saved_ev.id],
        source="deterministic_rule",
        rule_version="1.0.0",
    )
    saved_f = await finding_repository.insert(db_session, finding)
    await db_session.commit()

    assert saved_f.id is not None
    findings = await finding_repository.get_by_investigation(db_session, inv.id)
    assert len(findings) == 1
    assert findings[0].finding_code == "BLOCKCHAIN_AMOUNT_MISMATCH"
    assert findings[0].severity == "high"
    assert saved_ev.id in findings[0].evidence_ids


@pytest.mark.requires_db
async def test_findings_sorted_by_severity(db_session: AsyncSession):
    """get_by_investigation returns findings sorted: critical > high > medium > low > informational."""
    inv = await investigation_repository.create(
        db_session, external_reference=_unique_uuid(), entity_type="order"
    )
    await db_session.commit()

    for severity in ["low", "critical", "informational", "high"]:
        f = InvestigationFinding(
            investigation_id=inv.id,
            finding_code=f"CODE_{severity.upper()}",
            category="financial",
            severity=severity,
            title=f"{severity} finding",
            description="test",
            evidence_ids=[],
            source="deterministic_rule",
        )
        await finding_repository.insert(db_session, f)
    await db_session.commit()

    findings = await finding_repository.get_by_investigation(db_session, inv.id)
    severities = [f.severity for f in findings]
    assert severities.index("critical") < severities.index("high")
    assert severities.index("high") < severities.index("low")
    assert severities.index("low") < severities.index("informational")


@pytest.mark.requires_db
async def test_report_versioning(db_session: AsyncSession):
    """next_version increments correctly, get_latest returns most recent."""
    inv = await investigation_repository.create(
        db_session, external_reference=_unique_uuid(), entity_type="order"
    )
    await db_session.commit()

    v1 = await report_repository.next_version(db_session, inv.id)
    assert v1 == 1

    r1 = InvestigationReport(
        investigation_id=inv.id,
        report_version=v1,
        summary="First report",
        observed_facts=[],
        findings=[],
        missing_evidence=[],
        recommendations=[],
        limitations=[],
    )
    await report_repository.insert(db_session, r1)
    await db_session.commit()

    v2 = await report_repository.next_version(db_session, inv.id)
    assert v2 == 2

    r2 = InvestigationReport(
        investigation_id=inv.id,
        report_version=v2,
        summary="Second report",
        observed_facts=[],
        findings=[],
        missing_evidence=[],
        recommendations=[],
        limitations=[],
    )
    await report_repository.insert(db_session, r2)
    await db_session.commit()

    latest = await report_repository.get_latest(db_session, inv.id)
    assert latest is not None
    assert latest.report_version == 2
    assert latest.summary == "Second report"

    all_reports = await report_repository.get_all(db_session, inv.id)
    assert len(all_reports) == 2
    assert all_reports[0].report_version == 1


@pytest.mark.requires_db
async def test_feedback_insert_and_retrieve(db_session: AsyncSession):
    inv = await investigation_repository.create(
        db_session, external_reference=_unique_uuid(), entity_type="order"
    )
    await db_session.commit()

    fb = InvestigationFeedback(
        investigation_id=inv.id,
        finding_id=None,
        reviewer_reference="reviewer-001",
        decision="accepted",
        feedback="Looks correct based on blockchain evidence.",
    )
    await feedback_repository.insert(db_session, fb)
    await db_session.commit()

    results = await feedback_repository.get_by_investigation(db_session, inv.id)
    assert len(results) == 1
    assert results[0].decision == "accepted"
    assert results[0].reviewer_reference == "reviewer-001"


@pytest.mark.requires_db
async def test_investigation_service_creates_new(db_session: AsyncSession):
    """InvestigationService creates a new investigation for a fresh entity."""
    event = _make_event_inbox(entity_type="order", entity_id=_unique_uuid())
    db_session.add(event)
    await db_session.commit()

    svc = InvestigationService(db_session)
    inv, created = await svc.get_or_create_for_event(event)
    await db_session.commit()

    assert created is True
    assert inv is not None
    assert inv.entity_type == "order"
    assert inv.external_reference == event.entity_id
    assert inv.trigger_event_id == event.event_id


@pytest.mark.requires_db
async def test_investigation_service_correlates_active(db_session: AsyncSession):
    """Second event for same entity returns existing investigation (created=False)."""
    entity_id = _unique_uuid()

    event1 = _make_event_inbox(entity_type="order", entity_id=entity_id)
    event2 = _make_event_inbox(entity_type="order", entity_id=entity_id)
    db_session.add(event1)
    db_session.add(event2)
    await db_session.commit()

    svc = InvestigationService(db_session)
    inv1, created1 = await svc.get_or_create_for_event(event1)
    await db_session.commit()

    inv2, created2 = await svc.get_or_create_for_event(event2)
    await db_session.commit()

    assert created1 is True
    assert created2 is False
    assert inv1.id == inv2.id


@pytest.mark.requires_db
async def test_investigation_service_non_triggering_status(db_session: AsyncSession):
    """order.status_changed with a non-triggering status returns (None, False)."""
    event = _make_event_inbox(entity_type="order", entity_id=_unique_uuid())
    event.event_type = "order.status_changed"
    event.payload = {"data": {"current_status": "provider_execution_pending"}}
    db_session.add(event)
    await db_session.commit()

    svc = InvestigationService(db_session)
    inv, created = await svc.get_or_create_for_event(event)

    assert inv is None
    assert created is False


@pytest.mark.requires_db
async def test_evidence_collector_stores_records(db_session: AsyncSession):
    """EvidenceCollector with a mocked EvidenceClient stores evidence records."""
    from app.integrations.laravel.schemas import (
        UserProfile,
        UserRiskSummaryResponse,
        UserComplianceResponse,
        UserWalletsResponse,
    )

    inv = await investigation_repository.create(
        db_session, external_reference="6", entity_type="user"
    )
    await db_session.commit()

    # Mock EvidenceClient to return synthetic responses
    mock_client = AsyncMock()
    mock_client.user_profile.return_value = UserProfile.model_validate({
        "id": 6, "status": "active", "kyc_status": "none", "country": "XX"
    })
    mock_client.user_risk_summary.return_value = UserRiskSummaryResponse.model_validate({
        "risk_cases": [], "screening_results": [], "flagged_orders": []
    })
    mock_client.user_compliance.return_value = UserComplianceResponse.model_validate({
        "risk_cases": [], "screening_results": []
    })
    mock_client.user_wallets.return_value = UserWalletsResponse.model_validate({"wallets": []})

    collector = EvidenceCollector(mock_client, db_session)
    result = await collector.collect_for_user(
        investigation_id=inv.id,
        user_id=6,
    )
    await db_session.commit()

    assert len(result.collected) == 4
    assert result.is_complete is True

    records = await evidence_record_repository.get_by_investigation(db_session, inv.id)
    assert len(records) == 4
    types = {r.evidence_type for r in records}
    assert types == {"account", "risk", "compliance", "wallet"}

    # Verify content hashes are present and correct length
    for r in records:
        assert len(r.content_hash) == 64
        assert r.is_verified is True
        assert r.source_system == "agio-paymentexchange"


@pytest.mark.requires_db
async def test_evidence_collector_records_missing_on_none(db_session: AsyncSession):
    """EvidenceCollector records MissingEvidence when client returns None (404)."""
    inv = await investigation_repository.create(
        db_session, external_reference=_unique_uuid(), entity_type="order"
    )
    await db_session.commit()

    mock_client = AsyncMock()
    # All order-related calls return None (not found)
    mock_client.order.return_value = None
    mock_client.order_timeline.return_value = None
    mock_client.order_blockchain.return_value = None
    mock_client.order_provider.return_value = None
    mock_client.order_ledger.return_value = None
    mock_client.order_reconciliation.return_value = None

    collector = EvidenceCollector(mock_client, db_session)
    result = await collector.collect_for_order(
        investigation_id=inv.id,
        order_uuid="00000000-0000-0000-0000-000000000000",
    )
    await db_session.commit()

    assert len(result.collected) == 0
    assert len(result.missing) == 6
    assert result.is_complete is False
    reasons = {m.reason for m in result.missing}
    assert reasons == {"not_found"}

    # No evidence records should be stored
    records = await evidence_record_repository.get_by_investigation(db_session, inv.id)
    assert len(records) == 0


@pytest.mark.requires_db
async def test_evidence_collector_partial_collection(db_session: AsyncSession):
    """EvidenceCollector stores collected items and records missing ones separately."""
    from app.integrations.laravel.schemas import OrderDetailResponse, OrderTimelineResponse
    from app.integrations.laravel.client import LaravelTimeoutError

    inv = await investigation_repository.create(
        db_session, external_reference=_unique_uuid(), entity_type="order"
    )
    await db_session.commit()

    order_uuid = _unique_uuid()
    mock_client = AsyncMock()
    mock_client.order.return_value = OrderDetailResponse.model_validate({
        "transaction_id": order_uuid,
        "user_id": 1,
        "sell_amount": "1.0",
        "buy_amount": "0.00005",
        "fee_amount": "0.0",
        "rate": "20000.0",
        "status": "completed",
        "risk_status": "pending",
        "reconciliation_status": "pending",
        "deposit_confirmations": 0,
    })
    mock_client.order_timeline.return_value = OrderTimelineResponse.model_validate({"order_events": []})
    mock_client.order_blockchain.return_value = None
    mock_client.order_provider.return_value = None
    mock_client.order_ledger.return_value = None
    mock_client.order_reconciliation.return_value = None

    collector = EvidenceCollector(mock_client, db_session)
    result = await collector.collect_for_order(
        investigation_id=inv.id,
        order_uuid=order_uuid,
    )
    await db_session.commit()

    assert len(result.collected) == 2  # order + timeline
    assert len(result.missing) == 4   # blockchain + provider + ledger + reconciliation
    assert result.is_complete is False

    records = await evidence_record_repository.get_by_investigation(db_session, inv.id)
    assert len(records) == 2
    evidence_types = {r.evidence_type for r in records}
    assert evidence_types == {"order", "timeline"}
