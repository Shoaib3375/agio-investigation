"""RabbitMQ message handler — ingestion into the event_inbox table.

PY-M02 responsibility: parse, validate, persist.
PY-M04 addition: dispatch InvestigationOrchestrator as a background task
                 immediately after ACK.

ACK/NACK contract:
  - Malformed JSON or Pydantic validation failure → NACK requeue=False (DLQ)
  - Unknown event_type (not in WIRED_EVENT_TYPES) → ACK (silent skip)
  - DB write fails → do NOT ACK (message returns to queue for RabbitMQ retry)
  - DB write succeeds → ACK, then fire orchestrator task
  - Unexpected crash → NACK requeue=False (prevents infinite unacked messages)

The orchestrator is fired via asyncio.create_task so it never blocks delivery.
If the orchestrator itself fails it logs the error and transitions the
investigation to "failed" — the inbox event is already ACKed and durable.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import aio_pika
import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories.event_inbox_repository import insert_if_not_exists
from app.schemas.events import WIRED_EVENT_TYPES, EventEnvelope
from app.core.metrics import metrics

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.investigation.orchestrator import InvestigationOrchestrator

logger = structlog.get_logger()

SessionFactory = async_sessionmaker[AsyncSession]


async def handle_message(
    message: aio_pika.IncomingMessage,
    session_factory: SessionFactory,
    orchestrator: "InvestigationOrchestrator | None" = None,
) -> None:
    """Core ingestion handler — called once per RabbitMQ delivery."""
    try:
        await _handle(message, session_factory, orchestrator)
    except Exception as exc:
        logger.exception("rabbitmq.handler.unexpected_error", error=str(exc))
        try:
            await message.nack(requeue=False)
        except Exception:
            pass


async def _handle(
    message: aio_pika.IncomingMessage,
    session_factory: SessionFactory,
    orchestrator: "InvestigationOrchestrator | None",
) -> None:
    # 1. Parse JSON
    try:
        raw = json.loads(message.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("rabbitmq.handler.invalid_json", error=str(exc), body_preview=message.body[:200])
        await message.nack(requeue=False)
        return

    # 2. Validate envelope
    try:
        envelope = EventEnvelope.model_validate(raw)
    except ValidationError as exc:
        event_id = raw.get("event_id", "<unknown>") if isinstance(raw, dict) else "<unknown>"
        logger.warning("rabbitmq.handler.validation_error", event_id=event_id, errors=exc.errors())
        await message.nack(requeue=False)
        return

    # 3. Skip unknown event types
    if envelope.event_type not in WIRED_EVENT_TYPES:
        logger.debug(
            "rabbitmq.handler.unknown_event_type",
            event_id=envelope.event_id,
            event_type=envelope.event_type,
        )
        await message.ack()
        return

    # 4–6. Write to DB, then ACK
    inbox_id: str | None = None
    try:
        async with session_factory() as session:
            record, created = await insert_if_not_exists(session, envelope)
            await session.commit()
            inbox_id = record.id
    except Exception as exc:
        logger.error(
            "rabbitmq.handler.db_error",
            event_id=envelope.event_id,
            event_type=envelope.event_type,
            error=str(exc),
        )
        metrics.inc("events", status="failed")
        # Do not ACK — let RabbitMQ redeliver
        return

    # 7. ACK after successful commit
    await message.ack()

    if created:
        metrics.inc("events", status="received")
    else:
        metrics.inc("events", status="duplicate")

    logger.info(
        "rabbitmq.handler.ingested",
        event_id=envelope.event_id,
        event_type=envelope.event_type,
        entity_type=envelope.entity.type,
        entity_id=envelope.entity.id,
        created=created,
    )

    # 8. Fire investigation orchestrator as a background task (post-ACK)
    if orchestrator is not None and inbox_id is not None:
        asyncio.create_task(
            _run_orchestrator(orchestrator, inbox_id, envelope.event_id),
            name=f"investigate:{envelope.event_id}",
        )


async def _run_orchestrator(
    orchestrator: "InvestigationOrchestrator",
    inbox_id: str,
    event_id: str,
) -> None:
    """Wrapper that catches and logs orchestrator failures without crashing the consumer."""
    try:
        await orchestrator.process_event(inbox_id)
    except Exception as exc:
        logger.exception(
            "orchestrator.task_failed",
            inbox_id=inbox_id,
            event_id=event_id,
            error=str(exc),
        )


def make_message_handler(
    session_factory: SessionFactory,
    orchestrator: "InvestigationOrchestrator | None" = None,
) -> Callable[[aio_pika.IncomingMessage], Coroutine[Any, Any, None]]:
    """Return a bound message handler capturing session_factory and orchestrator.

    Usage::

        handler = make_message_handler(async_session_factory, orchestrator)
        await consumer.start_consuming(handler)
    """

    async def _bound(message: aio_pika.IncomingMessage) -> None:
        await handle_message(message, session_factory, orchestrator)

    return _bound
