"""PY-M06 tests — admin-facing investigation API.

Tests cover:
  - Auth: missing token, invalid token, unconfigured key (503)
  - GET /investigations: paginated list, filters
  - GET /investigations/{id}: detail, 404
  - GET /investigations/{id}/evidence: list, 404 investigation
  - GET /investigations/{id}/evidence/{eid}: with snapshot, 404 evidence
  - GET /investigations/{id}/timeline: from snapshot, empty when no evidence
  - GET /investigations/{id}/findings: list, severity/category filter
  - GET /investigations/{id}/report: latest, by version, 404 no report
  - POST /investigations/{id}/feedback: success, invalid decision, bad finding_id
  - POST /investigations/{id}/request-review: re-queue, invalid state

DB tests require investigation database on 127.0.0.1:5437 (requires_db marker).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.evidence_collector import _content_hash
from app.models.evidence_record import EvidenceRecord
from app.models.investigation import Investigation
from app.models.investigation_feedback import InvestigationFeedback
from app.models.investigation_finding import InvestigationFinding
from app.models.investigation_report import InvestigationReport

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_TEST_API_KEY = "test-admin-key-for-unit-tests"
_AUTH = {"Authorization": f"Bearer {_TEST_API_KEY}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def set_admin_key(monkeypatch):
    """Inject a test admin API key into settings for every test."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "admin_api_key", _TEST_API_KEY)


@pytest_asyncio.fixture
async def api_client():
    """HTTPX client bound to the FastAPI app (no lifespan)."""
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _db_reachable() -> bool:
    """Check DB reachability using the app's own engine."""
    from app.database.session import ping_database
    try:
        return await ping_database()
    except Exception:
        return False


@pytest_asyncio.fixture
async def db_session():
    """Yield a fresh AsyncSession per test using NullPool to avoid cross-loop issues.

    NullPool creates one connection per test and closes it immediately after,
    preventing asyncpg from trying to return a connection to a closed event loop.
    """
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.core.config import settings

    if not await _db_reachable():
        pytest.skip("investigation database not reachable on 5437")

    engine = create_async_engine(settings.investigation_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _create_investigation(session: AsyncSession, **kwargs) -> Investigation:
    from app.database.repositories import investigation_repository
    inv = await investigation_repository.create(
        session,
        external_reference=kwargs.get("external_reference", _uid()),
        entity_type=kwargs.get("entity_type", "order"),
        investigation_type=kwargs.get("investigation_type", "order_lifecycle"),
        priority=kwargs.get("priority", "normal"),
    )
    await session.commit()
    return inv


async def _create_evidence(session: AsyncSession, investigation_id: str, ev_type: str = "order") -> EvidenceRecord:
    snap = {"transaction_id": _uid(), "status": "completed", "evidence_type": ev_type}
    rec = EvidenceRecord(
        investigation_id=investigation_id,
        evidence_type=ev_type,
        source_record_type=ev_type,
        source_record_id=_uid(),
        retrieved_at=_now(),
        content_hash=_content_hash(snap),
        is_verified=True,
        verification_method="schema_match",
        snapshot=snap,
    )
    session.add(rec)
    await session.flush()
    await session.commit()
    return rec


async def _create_finding(session: AsyncSession, investigation_id: str) -> InvestigationFinding:
    finding = InvestigationFinding(
        investigation_id=investigation_id,
        finding_code="ORDER_AMOUNT_MISMATCH",
        category="financial",
        severity="high",
        title="Amount mismatch",
        description="Test finding",
        evidence_ids=[],
        source="deterministic_rule",
        rule_version="1.0.0",
    )
    session.add(finding)
    await session.flush()
    await session.commit()
    return finding


async def _create_report(session: AsyncSession, investigation_id: str) -> InvestigationReport:
    report = InvestigationReport(
        investigation_id=investigation_id,
        report_version=1,
        summary="Test report",
        observed_facts=[{"statement": "test", "evidence_ids": []}],
        findings=[],
        missing_evidence=[],
        recommendations=[],
        limitations=["Test limitation"],
    )
    session.add(report)
    await session.flush()
    await session.commit()
    return report


# ===========================================================================
# Auth tests (no DB needed)
# ===========================================================================


async def test_auth_missing_token(api_client: AsyncClient):
    """No Authorization header → 401."""
    r = await api_client.get("/api/v1/investigations")
    assert r.status_code == 401


async def test_auth_wrong_token(api_client: AsyncClient):
    """Wrong token → 401."""
    r = await api_client.get(
        "/api/v1/investigations",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


async def test_auth_no_scheme(api_client: AsyncClient):
    """Token without 'Bearer' scheme → 401."""
    r = await api_client.get(
        "/api/v1/investigations",
        headers={"Authorization": _TEST_API_KEY},
    )
    assert r.status_code == 401


async def test_auth_unconfigured_returns_503(api_client: AsyncClient, monkeypatch):
    """When admin_api_key is None → 503."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "admin_api_key", None)
    r = await api_client.get(
        "/api/v1/investigations",
        headers=_AUTH,
    )
    assert r.status_code == 503


async def test_auth_valid_token(api_client: AsyncClient):
    """Valid token → not 401/403 (may be 200 or 404 depending on DB)."""
    r = await api_client.get("/api/v1/investigations", headers=_AUTH)
    assert r.status_code not in (401, 403)


# ===========================================================================
# GET /investigations — list
# ===========================================================================


@pytest.mark.requires_db
async def test_list_investigations_empty(api_client: AsyncClient):
    """List returns a paginated structure even when empty."""
    r = await api_client.get("/api/v1/investigations?per_page=5", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert "meta" in body
    assert body["meta"]["page"] == 1
    assert body["meta"]["per_page"] == 5


@pytest.mark.requires_db
async def test_list_investigations_with_data(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session, entity_type="order")
    r = await api_client.get("/api/v1/investigations", headers=_AUTH)
    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["data"]]
    assert inv.id in ids


@pytest.mark.requires_db
async def test_list_investigations_filter_entity_type(api_client: AsyncClient, db_session: AsyncSession):
    await _create_investigation(db_session, entity_type="order")
    await _create_investigation(db_session, entity_type="user")
    r = await api_client.get("/api/v1/investigations?entity_type=user", headers=_AUTH)
    assert r.status_code == 200
    for item in r.json()["data"]:
        assert item["entity_type"] == "user"


@pytest.mark.requires_db
async def test_list_investigations_filter_status(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    from app.database.repositories import investigation_repository
    await investigation_repository.set_status(db_session, inv.id, "completed")
    await db_session.commit()
    r = await api_client.get("/api/v1/investigations?status=completed", headers=_AUTH)
    assert r.status_code == 200
    for item in r.json()["data"]:
        assert item["status"] == "completed"


@pytest.mark.requires_db
async def test_list_investigations_pagination(api_client: AsyncClient, db_session: AsyncSession):
    for _ in range(3):
        await _create_investigation(db_session)
    r = await api_client.get("/api/v1/investigations?per_page=2&page=1", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["per_page"] == 2
    assert len(body["data"]) <= 2


# ===========================================================================
# GET /investigations/{id}
# ===========================================================================


@pytest.mark.requires_db
async def test_get_investigation_detail(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session, entity_type="order", priority="high")
    r = await api_client.get(f"/api/v1/investigations/{inv.id}", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == inv.id
    assert body["entity_type"] == "order"
    assert body["priority"] == "high"


@pytest.mark.requires_db
async def test_get_investigation_not_found(api_client: AsyncClient):
    r = await api_client.get(f"/api/v1/investigations/{_uid()}", headers=_AUTH)
    assert r.status_code == 404


# ===========================================================================
# GET /investigations/{id}/evidence
# ===========================================================================


@pytest.mark.requires_db
async def test_list_evidence_empty(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    r = await api_client.get(f"/api/v1/investigations/{inv.id}/evidence", headers=_AUTH)
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.requires_db
async def test_list_evidence_no_snapshot(api_client: AsyncClient, db_session: AsyncSession):
    """Evidence list must NOT include the snapshot field."""
    inv = await _create_investigation(db_session)
    await _create_evidence(db_session, inv.id, "order")
    r = await api_client.get(f"/api/v1/investigations/{inv.id}/evidence", headers=_AUTH)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    assert "snapshot" not in items[0]


@pytest.mark.requires_db
async def test_list_evidence_investigation_not_found(api_client: AsyncClient):
    r = await api_client.get(f"/api/v1/investigations/{_uid()}/evidence", headers=_AUTH)
    assert r.status_code == 404


# ===========================================================================
# GET /investigations/{id}/evidence/{eid}
# ===========================================================================


@pytest.mark.requires_db
async def test_get_evidence_detail_includes_snapshot(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    rec = await _create_evidence(db_session, inv.id, "order")
    r = await api_client.get(
        f"/api/v1/investigations/{inv.id}/evidence/{rec.id}", headers=_AUTH
    )
    assert r.status_code == 200
    body = r.json()
    assert "snapshot" in body
    assert body["evidence_type"] == "order"
    assert body["is_verified"] is True


@pytest.mark.requires_db
async def test_get_evidence_detail_not_found(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    r = await api_client.get(
        f"/api/v1/investigations/{inv.id}/evidence/{_uid()}", headers=_AUTH
    )
    assert r.status_code == 404


# ===========================================================================
# GET /investigations/{id}/timeline
# ===========================================================================


@pytest.mark.requires_db
async def test_get_timeline_empty(api_client: AsyncClient, db_session: AsyncSession):
    """No timeline evidence → empty events list."""
    inv = await _create_investigation(db_session)
    r = await api_client.get(f"/api/v1/investigations/{inv.id}/timeline", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["events"] == []
    assert body["investigation_id"] == inv.id


@pytest.mark.requires_db
async def test_get_timeline_from_snapshot(api_client: AsyncClient, db_session: AsyncSession):
    """Timeline returns sorted events from stored snapshot."""
    inv = await _create_investigation(db_session)
    timeline_snap = {
        "order_events": [
            {
                "id": 2, "from_status": "awaiting_deposit",
                "to_status": "deposit_detected",
                "actor_type": "system", "actor_id": None,
                "reason": "tatum_webhook", "metadata": None,
                "created_at": "2026-11-13T10:01:00Z",
            },
            {
                "id": 1, "from_status": None,
                "to_status": "awaiting_deposit",
                "actor_type": "system", "actor_id": None,
                "reason": None, "metadata": None,
                "created_at": "2026-11-13T10:00:00Z",
            },
        ]
    }
    rec = EvidenceRecord(
        investigation_id=inv.id,
        evidence_type="timeline",
        source_record_type="order_events",
        source_record_id=inv.external_reference,
        retrieved_at=_now(),
        content_hash=_content_hash(timeline_snap),
        is_verified=True,
        snapshot=timeline_snap,
    )
    db_session.add(rec)
    await db_session.commit()

    r = await api_client.get(f"/api/v1/investigations/{inv.id}/timeline", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    events = body["events"]
    assert len(events) == 2
    # Must be sorted ascending by occurred_at
    assert events[0]["to_status"] == "awaiting_deposit"
    assert events[1]["to_status"] == "deposit_detected"
    assert events[0]["occurred_at"] < events[1]["occurred_at"]


# ===========================================================================
# GET /investigations/{id}/findings
# ===========================================================================


@pytest.mark.requires_db
async def test_list_findings_empty(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    r = await api_client.get(f"/api/v1/investigations/{inv.id}/findings", headers=_AUTH)
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.requires_db
async def test_list_findings_returns_all(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    await _create_finding(db_session, inv.id)
    r = await api_client.get(f"/api/v1/investigations/{inv.id}/findings", headers=_AUTH)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    assert items[0]["finding_code"] == "ORDER_AMOUNT_MISMATCH"
    assert items[0]["severity"] == "high"


@pytest.mark.requires_db
async def test_list_findings_filter_severity(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    await _create_finding(db_session, inv.id)
    r = await api_client.get(
        f"/api/v1/investigations/{inv.id}/findings?severity=high", headers=_AUTH
    )
    assert r.status_code == 200
    for item in r.json():
        assert item["severity"] == "high"


@pytest.mark.requires_db
async def test_list_findings_filter_category(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    await _create_finding(db_session, inv.id)
    r = await api_client.get(
        f"/api/v1/investigations/{inv.id}/findings?category=financial", headers=_AUTH
    )
    assert r.status_code == 200
    for item in r.json():
        assert item["category"] == "financial"


@pytest.mark.requires_db
async def test_list_findings_filter_no_match(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    await _create_finding(db_session, inv.id)
    r = await api_client.get(
        f"/api/v1/investigations/{inv.id}/findings?severity=critical", headers=_AUTH
    )
    assert r.status_code == 200
    assert r.json() == []


# ===========================================================================
# GET /investigations/{id}/report
# ===========================================================================


@pytest.mark.requires_db
async def test_get_report_latest(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    await _create_report(db_session, inv.id)
    r = await api_client.get(f"/api/v1/investigations/{inv.id}/report", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["report_version"] == 1
    assert body["summary"] == "Test report"
    assert isinstance(body["limitations"], list)


@pytest.mark.requires_db
async def test_get_report_by_version(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    await _create_report(db_session, inv.id)
    r = await api_client.get(
        f"/api/v1/investigations/{inv.id}/report?version=1", headers=_AUTH
    )
    assert r.status_code == 200
    assert r.json()["report_version"] == 1


@pytest.mark.requires_db
async def test_get_report_version_not_found(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    await _create_report(db_session, inv.id)
    r = await api_client.get(
        f"/api/v1/investigations/{inv.id}/report?version=99", headers=_AUTH
    )
    assert r.status_code == 404


@pytest.mark.requires_db
async def test_get_report_no_report(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    r = await api_client.get(f"/api/v1/investigations/{inv.id}/report", headers=_AUTH)
    assert r.status_code == 404


# ===========================================================================
# POST /investigations/{id}/feedback
# ===========================================================================


@pytest.mark.requires_db
async def test_submit_feedback_success(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    payload = {
        "reviewer_reference": "admin-001",
        "decision": "accepted",
        "feedback": "Evidence supports the finding.",
    }
    r = await api_client.post(
        f"/api/v1/investigations/{inv.id}/feedback",
        json=payload,
        headers=_AUTH,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["decision"] == "accepted"
    assert body["reviewer_reference"] == "admin-001"
    assert body["investigation_id"] == inv.id
    assert body["finding_id"] is None


@pytest.mark.requires_db
async def test_submit_feedback_with_finding(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    finding = await _create_finding(db_session, inv.id)
    payload = {
        "finding_id": finding.id,
        "reviewer_reference": "admin-002",
        "decision": "rejected",
        "feedback": "False positive.",
    }
    r = await api_client.post(
        f"/api/v1/investigations/{inv.id}/feedback",
        json=payload,
        headers=_AUTH,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["finding_id"] == finding.id
    assert body["decision"] == "rejected"


@pytest.mark.requires_db
async def test_submit_feedback_invalid_decision(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    payload = {"reviewer_reference": "admin-001", "decision": "maybe"}
    r = await api_client.post(
        f"/api/v1/investigations/{inv.id}/feedback",
        json=payload,
        headers=_AUTH,
    )
    assert r.status_code == 422


@pytest.mark.requires_db
async def test_submit_feedback_invalid_finding_id(api_client: AsyncClient, db_session: AsyncSession):
    inv = await _create_investigation(db_session)
    payload = {
        "finding_id": _uid(),   # random UUID not belonging to this investigation
        "reviewer_reference": "admin-001",
        "decision": "accepted",
    }
    r = await api_client.post(
        f"/api/v1/investigations/{inv.id}/feedback",
        json=payload,
        headers=_AUTH,
    )
    assert r.status_code == 404


@pytest.mark.requires_db
async def test_submit_feedback_investigation_not_found(api_client: AsyncClient):
    payload = {"reviewer_reference": "admin-001", "decision": "accepted"}
    r = await api_client.post(
        f"/api/v1/investigations/{_uid()}/feedback",
        json=payload,
        headers=_AUTH,
    )
    assert r.status_code == 404


# ===========================================================================
# POST /investigations/{id}/request-review
# ===========================================================================


@pytest.mark.requires_db
async def test_request_review_from_completed(api_client: AsyncClient, db_session: AsyncSession):
    from app.database.repositories import investigation_repository
    inv = await _create_investigation(db_session)
    await investigation_repository.set_status(db_session, inv.id, "completed")
    await db_session.commit()

    r = await api_client.post(
        f"/api/v1/investigations/{inv.id}/request-review",
        json={"reason": "New evidence discovered."},
        headers=_AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["investigation_id"] == inv.id


@pytest.mark.requires_db
async def test_request_review_from_review_required(api_client: AsyncClient, db_session: AsyncSession):
    from app.database.repositories import investigation_repository
    inv = await _create_investigation(db_session)
    await investigation_repository.set_status(db_session, inv.id, "review_required")
    await db_session.commit()

    r = await api_client.post(
        f"/api/v1/investigations/{inv.id}/request-review",
        json={},
        headers=_AUTH,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


@pytest.mark.requires_db
async def test_request_review_invalid_state(api_client: AsyncClient, db_session: AsyncSession):
    """Cannot request review when investigation is still collecting."""
    from app.database.repositories import investigation_repository
    inv = await _create_investigation(db_session)
    await investigation_repository.set_status(db_session, inv.id, "collecting")
    await db_session.commit()

    r = await api_client.post(
        f"/api/v1/investigations/{inv.id}/request-review",
        json={},
        headers=_AUTH,
    )
    assert r.status_code == 409


@pytest.mark.requires_db
async def test_request_review_not_found(api_client: AsyncClient):
    r = await api_client.post(
        f"/api/v1/investigations/{_uid()}/request-review",
        json={},
        headers=_AUTH,
    )
    assert r.status_code == 404
