"""PY-M02 consumer and event inbox tests.

Tests marked ``requires_db`` need a live PostgreSQL instance on 127.0.0.1:5437.
Run the full suite with the DB container up, or skip DB tests with:

    pytest -m 'not requires_db'
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.repositories.event_inbox_repository import insert_if_not_exists
from app.models.event_inbox import EventInbox
from app.schemas.events import WIRED_EVENT_TYPES, EventEnvelope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ENVELOPE_DICT = {
    "event_id": "evt-test-001",
    "event_type": "order.created",
    "event_version": "1.0",
    "occurred_at": "2026-11-12T10:00:00Z",
    "producer": "agio-paymentexchange",
    "correlation_id": "corr-001",
    "causation_id": None,
    "entity": {"type": "order", "id": "order-uuid-001"},
    "data": {"order_uuid": "order-uuid-001", "status": "awaiting_deposit"},
}


def _make_envelope(**overrides) -> dict:
    """Return a copy of the valid envelope dict with optional overrides."""
    return {**_VALID_ENVELOPE_DICT, **overrides}


# ---------------------------------------------------------------------------
# DB fixture (skips if not reachable)
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
    """Yield an AsyncSession connected to the investigation DB.

    The fixture opens a transaction and rolls it back after each test so the
    DB is left clean without needing a truncate.
    """
    if not await _db_reachable():
        pytest.skip("investigation database not reachable on 5437")

    engine = create_async_engine(_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Envelope schema tests (no DB required)
# ---------------------------------------------------------------------------


def test_envelope_valid():
    """A well-formed envelope dict is accepted and all fields round-trip."""
    envelope = EventEnvelope.model_validate(_VALID_ENVELOPE_DICT)
    assert envelope.event_id == "evt-test-001"
    assert envelope.event_type == "order.created"
    assert envelope.event_version == "1.0"
    assert envelope.producer == "agio-paymentexchange"
    assert envelope.correlation_id == "corr-001"
    assert envelope.causation_id is None
    assert envelope.entity.type == "order"
    assert envelope.entity.id == "order-uuid-001"
    assert envelope.data["status"] == "awaiting_deposit"


def test_envelope_rejects_integer_version():
    """event_version must be a string; an integer value must fail validation."""
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(_make_envelope(event_version=1))


def test_envelope_rejects_wrong_version_string():
    """Only '1.0' is a supported event_version; any other string must fail."""
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(_make_envelope(event_version="2.0"))


def test_envelope_wired_event_types():
    """WIRED_EVENT_TYPES must contain exactly 10 entries including the umbrella."""
    assert len(WIRED_EVENT_TYPES) == 10
    assert "order.status_changed" in WIRED_EVENT_TYPES


# ---------------------------------------------------------------------------
# DB tests (require investigation-database container on 5437)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
async def test_insert_new_event(db_session: AsyncSession):
    """Inserting a fresh event returns created=True and status='received'."""
    unique_id = f"evt-new-{uuid.uuid4()}"
    envelope = EventEnvelope.model_validate(_make_envelope(event_id=unique_id))

    record, created = await insert_if_not_exists(db_session, envelope)
    await db_session.commit()

    assert created is True
    assert record.event_id == unique_id
    assert record.processing_status == "received"
    assert record.attempt_count == 0


@pytest.mark.requires_db
async def test_duplicate_idempotency(db_session: AsyncSession):
    """Inserting the same event_id twice returns created=False the second time.

    The DB row count must stay at 1 (ON CONFLICT DO NOTHING).
    """
    unique_id = f"evt-dup-{uuid.uuid4()}"
    envelope = EventEnvelope.model_validate(_make_envelope(event_id=unique_id))

    # First insert.
    record1, created1 = await insert_if_not_exists(db_session, envelope)
    await db_session.commit()

    # Second insert with the same event_id.
    record2, created2 = await insert_if_not_exists(db_session, envelope)
    await db_session.commit()

    assert created1 is True
    assert created2 is False
    assert record1.event_id == record2.event_id

    # Verify only one row exists.
    result = await db_session.execute(
        select(EventInbox).where(EventInbox.event_id == unique_id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1
