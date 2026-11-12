"""RabbitMQ message handler — ingestion into the event_inbox table.

PY-M02 responsibility: parse, validate, persist.  Routing/investigation
logic is deferred to PY-M04/M05.

ACK/NACK contract:
  - Malformed JSON or Pydantic validation failure → NACK requeue=False (DLQ)
  - Unknown event_type (not in WIRED_EVENT_TYPES) → ACK (silent skip)
  - DB write fails → do NOT ACK (message returns to queue for RabbitMQ retry)
  - DB write succeeds → ACK
  - Unexpected crash → NACK requeue=False (prevents infinite unacked messages)
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import aio_pika
import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories.event_inbox_repository import insert_if_not_exists
from app.schemas.events import WIRED_EVENT_TYPES, EventEnvelope

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()

SessionFactory = async_sessionmaker[AsyncSession]


async def handle_message(
    message: aio_pika.IncomingMessage,
    session_factory: SessionFactory,
) -> None:
    """Core ingestion handler — called once per RabbitMQ delivery.

    Parameters
    ----------
    message:
        The incoming message from aio-pika.
    session_factory:
        An ``async_sessionmaker`` used to open a new session per message.
    """
    try:
        await _handle(message, session_factory)
    except Exception as exc:
        # Last-resort: NACK without requeue so the message goes to the DLQ
        # rather than being left unacked and blocking the channel.
        logger.exception(
            "rabbitmq.handler.unexpected_error",
            error=str(exc),
        )
        try:
            await message.nack(requeue=False)
        except Exception:
            pass


async def _handle(
    message: aio_pika.IncomingMessage,
    session_factory: SessionFactory,
) -> None:
    """Inner handler — raises on unexpected errors (caught by handle_message)."""
    # 1. Parse body as JSON.
    try:
        raw = json.loads(message.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning(
            "rabbitmq.handler.invalid_json",
            error=str(exc),
            body_preview=message.body[:200],
        )
        await message.nack(requeue=False)
        return

    # 2. Validate against EventEnvelope.
    try:
        envelope = EventEnvelope.model_validate(raw)
    except ValidationError as exc:
        event_id = raw.get("event_id", "<unknown>") if isinstance(raw, dict) else "<unknown>"
        logger.warning(
            "rabbitmq.handler.validation_error",
            event_id=event_id,
            errors=exc.errors(),
        )
        await message.nack(requeue=False)
        return

    # 3. Skip unknown event types (may appear for future event types).
    if envelope.event_type not in WIRED_EVENT_TYPES:
        logger.debug(
            "rabbitmq.handler.unknown_event_type",
            event_id=envelope.event_id,
            event_type=envelope.event_type,
        )
        await message.ack()
        return

    # 4–6. Write to DB, then ACK.
    try:
        async with session_factory() as session:
            record, created = await insert_if_not_exists(session, envelope)
            await session.commit()
    except Exception as exc:
        # DB failure: do NOT ACK — let RabbitMQ redeliver.
        logger.error(
            "rabbitmq.handler.db_error",
            event_id=envelope.event_id,
            event_type=envelope.event_type,
            error=str(exc),
        )
        # Do not ack or nack — message returns to queue automatically.
        return

    # 7. ACK only after a successful commit.
    await message.ack()

    logger.info(
        "rabbitmq.handler.ingested",
        event_id=envelope.event_id,
        event_type=envelope.event_type,
        entity_type=envelope.entity.type,
        entity_id=envelope.entity.id,
        created=created,
    )


def make_message_handler(
    session_factory: SessionFactory,
) -> Callable[[aio_pika.IncomingMessage], Coroutine[Any, Any, None]]:
    """Return a bound message handler that captures ``session_factory``.

    Usage::

        handler = make_message_handler(async_session_factory)
        await consumer.start_consuming(handler)
    """

    async def _bound(message: aio_pika.IncomingMessage) -> None:
        await handle_message(message, session_factory)

    return _bound
