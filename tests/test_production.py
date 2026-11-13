"""PY-M09 tests — production hardening.

Covers:
  - Metrics registry: counter increment, deduplication, Prometheus text render
  - Request logging middleware: correlation_id header, X-Request-ID propagation
  - /api/v1/metrics endpoint: returns plain text with correct metric names
  - /api/v1/ready: improved readiness with per-dependency status
  - RetentionWorker: purge logic (requires_db)
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEST_API_KEY = "test-admin-key-for-unit-tests"


@pytest.fixture(autouse=True)
def set_admin_key(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "admin_api_key", _TEST_API_KEY)


@pytest_asyncio.fixture
async def api_client():
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session():
    from app.database.session import ping_database
    try:
        ok = await ping_database()
    except Exception:
        ok = False
    if not ok:
        pytest.skip("investigation database not reachable on 5437")
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.core.config import settings
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


# ===========================================================================
# Metrics registry unit tests
# ===========================================================================


class TestMetricsRegistry:
    def setup_method(self):
        """Fresh registry for each test."""
        from app.core.metrics import MetricsRegistry
        self.m = MetricsRegistry()

    def test_counter_starts_at_zero(self):
        assert self.m.get_counter("events", status="received") == 0

    def test_counter_increments(self):
        self.m.inc("events", status="received")
        self.m.inc("events", status="received")
        assert self.m.get_counter("events", status="received") == 2

    def test_different_labels_independent(self):
        self.m.inc("events", status="received")
        self.m.inc("events", status="failed")
        assert self.m.get_counter("events", status="received") == 1
        assert self.m.get_counter("events", status="failed") == 1

    def test_different_families_independent(self):
        self.m.inc("events", status="received")
        self.m.inc("investigations", status="created")
        assert self.m.get_counter("events", status="received") == 1
        assert self.m.get_counter("investigations", status="created") == 1

    def test_histogram_observe(self):
        self.m.observe_duration("llm_duration", 1.5)
        self.m.observe_duration("llm_duration", 0.3)
        snap = self.m._histograms["llm_duration"].snapshot()
        assert snap["count"] == 2
        assert abs(snap["sum"] - 1.8) < 0.0001

    def test_render_text_contains_metric_names(self):
        text = self.m.render_text()
        assert "agio_investigation_events_total" in text
        assert "agio_investigation_investigations_total" in text
        assert "agio_investigation_findings_total" in text
        assert "agio_investigation_llm_requests_total" in text
        assert "agio_investigation_llm_duration_seconds" in text
        assert "agio_investigation_processing_duration_seconds" in text

    def test_render_text_has_help_and_type_lines(self):
        text = self.m.render_text()
        assert "# HELP" in text
        assert "# TYPE" in text

    def test_render_text_reflects_counts(self):
        self.m.inc("events", status="received")
        self.m.inc("events", status="received")
        self.m.inc("events", status="failed")
        text = self.m.render_text()
        assert 'agio_investigation_events_total{status="received"} 2' in text
        assert 'agio_investigation_events_total{status="failed"} 1' in text

    def test_render_text_histogram_buckets(self):
        self.m.observe_duration("llm_duration", 0.5)
        text = self.m.render_text()
        assert "llm_duration_seconds_bucket" in text
        assert "llm_duration_seconds_sum" in text
        assert "llm_duration_seconds_count" in text


# ===========================================================================
# Request logging middleware
# ===========================================================================


async def test_middleware_adds_request_id_header(api_client: AsyncClient):
    """Every response must have X-Request-ID header."""
    r = await api_client.get("/api/v1/health")
    assert r.status_code == 200
    assert "x-request-id" in r.headers


async def test_middleware_echoes_request_id(api_client: AsyncClient):
    """When X-Request-ID is sent, the same value is echoed back."""
    custom_id = "custom-correlation-123"
    r = await api_client.get(
        "/api/v1/health",
        headers={"X-Request-ID": custom_id},
    )
    assert r.headers.get("x-request-id") == custom_id


async def test_middleware_generates_id_when_absent(api_client: AsyncClient):
    """When no X-Request-ID header, the middleware generates a UUID."""
    r = await api_client.get("/api/v1/health")
    request_id = r.headers.get("x-request-id", "")
    assert len(request_id) == 36  # UUID format: 8-4-4-4-12


# ===========================================================================
# /api/v1/metrics endpoint
# ===========================================================================


async def test_metrics_endpoint_returns_text(api_client: AsyncClient):
    r = await api_client.get("/api/v1/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]


async def test_metrics_endpoint_contains_expected_metrics(api_client: AsyncClient):
    r = await api_client.get("/api/v1/metrics")
    body = r.text
    assert "agio_investigation_events_total" in body
    assert "agio_investigation_llm_duration_seconds" in body
    assert "# TYPE" in body


# ===========================================================================
# /api/v1/ready improved readiness
# ===========================================================================


async def test_ready_returns_checks_dict(api_client: AsyncClient):
    r = await api_client.get("/api/v1/ready")
    # Should be 200 or 503 depending on DB availability
    assert r.status_code in (200, 503)
    body = r.json()
    assert "checks" in body
    assert "database" in body["checks"]


async def test_ready_has_degraded_list(api_client: AsyncClient):
    r = await api_client.get("/api/v1/ready")
    body = r.json()
    assert "degraded" in body
    assert isinstance(body["degraded"], list)


async def test_ready_reports_unconfigured_rabbitmq(api_client: AsyncClient, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "rabbitmq_url", None)
    r = await api_client.get("/api/v1/ready")
    body = r.json()
    # With no RABBITMQ_URL, rabbitmq check should be "not_configured"
    assert body["checks"].get("rabbitmq") == "not_configured"


# ===========================================================================
# RetentionWorker (requires_db)
# ===========================================================================


@pytest.mark.requires_db
async def test_retention_purges_old_processed_events(db_session: AsyncSession):
    """Old processed events are deleted; recent ones and failed ones are kept."""
    from app.models.event_inbox import EventInbox
    from app.workers.retention import RetentionWorker
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession as AS
    from app.core.config import settings

    engine = create_async_engine(settings.investigation_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AS, expire_on_commit=False)

    old_time = _now() - timedelta(days=35)
    recent_time = _now() - timedelta(days=5)

    # Old processed — should be deleted
    old_event = EventInbox(
        event_id=f"evt-old-{_uid()}",
        event_type="order.created",
        event_version="1.0",
        producer="test",
        entity_type="order",
        entity_id=_uid(),
        correlation_id=_uid(),
        occurred_at=old_time,
        received_at=old_time,
        payload={},
        processing_status="processed",
        attempt_count=1,
        processed_at=old_time,
    )
    # Recent processed — should be kept
    recent_event = EventInbox(
        event_id=f"evt-recent-{_uid()}",
        event_type="order.created",
        event_version="1.0",
        producer="test",
        entity_type="order",
        entity_id=_uid(),
        correlation_id=_uid(),
        occurred_at=recent_time,
        received_at=recent_time,
        payload={},
        processing_status="processed",
        attempt_count=1,
        processed_at=recent_time,
    )
    # Old failed — should NOT be deleted (only processed are cleaned)
    failed_event = EventInbox(
        event_id=f"evt-failed-{_uid()}",
        event_type="order.created",
        event_version="1.0",
        producer="test",
        entity_type="order",
        entity_id=_uid(),
        correlation_id=_uid(),
        occurred_at=old_time,
        received_at=old_time,
        payload={},
        processing_status="failed",
        attempt_count=3,
    )

    db_session.add_all([old_event, recent_event, failed_event])
    await db_session.commit()

    old_id = old_event.event_id
    recent_id = recent_event.event_id
    failed_id = failed_event.event_id

    worker = RetentionWorker(factory, event_inbox_days=30, investigation_days=90, interval_seconds=999999)
    await worker._run_once()

    # Reload and verify
    from sqlalchemy import select
    result = await db_session.execute(
        select(EventInbox.event_id).where(
            EventInbox.event_id.in_([old_id, recent_id, failed_id])
        )
    )
    remaining = {row[0] for row in result.fetchall()}

    assert old_id not in remaining, "Old processed event should have been deleted"
    assert recent_id in remaining, "Recent processed event should be kept"
    assert failed_id in remaining, "Failed events should never be deleted"

    await engine.dispose()


@pytest.mark.requires_db
async def test_retention_does_not_purge_recent_investigations(db_session: AsyncSession):
    """Recently completed investigations are not deleted."""
    from app.database.repositories import investigation_repository
    from app.workers.retention import RetentionWorker
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession as AS
    from app.core.config import settings

    engine = create_async_engine(settings.investigation_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AS, expire_on_commit=False)

    inv = await investigation_repository.create(
        db_session,
        external_reference=_uid(),
        entity_type="order",
    )
    await investigation_repository.set_status(db_session, inv.id, "completed")
    await db_session.commit()

    worker = RetentionWorker(factory, event_inbox_days=30, investigation_days=90, interval_seconds=999999)
    await worker._run_once()

    # Recently completed investigation should still exist
    refreshed = await investigation_repository.get_by_id(db_session, inv.id)
    assert refreshed is not None, "Recently completed investigation should not be deleted"

    await engine.dispose()
