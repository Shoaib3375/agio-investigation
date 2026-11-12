"""Consumer-side retry helper.

Publishes a failed message to the retry exchange with an incremented
``x-retry-count`` header.  If max retries is exceeded the message is NACKed
to the DLQ instead.
"""

from __future__ import annotations

import aio_pika
import structlog

logger = structlog.get_logger()

_RETRY_COUNT_HEADER = "x-retry-count"


async def republish_to_retry(
    channel: aio_pika.abc.AbstractChannel,
    message: aio_pika.IncomingMessage,
    retry_exchange: str,
    max_retries: int = 3,
) -> None:
    """Republish ``message`` to ``retry_exchange`` or NACK to DLQ.

    The function reads the ``x-retry-count`` header (defaulting to 0) and
    increments it.  If the new count exceeds ``max_retries`` the message is
    NACKed with ``requeue=False`` so it flows to the dead-letter queue.

    Parameters
    ----------
    channel:
        An open aio-pika channel used for publishing.
    message:
        The incoming message to retry.
    retry_exchange:
        Name of the retry exchange (e.g. ``"agio.retry"``).
    max_retries:
        Maximum number of consumer-side retry attempts before the message is
        sent to the DLQ.
    """
    headers: dict = dict(message.headers or {})
    retry_count: int = int(headers.get(_RETRY_COUNT_HEADER, 0))
    new_retry_count = retry_count + 1

    if new_retry_count > max_retries:
        logger.warning(
            "rabbitmq.retry.max_exceeded",
            routing_key=message.routing_key,
            retry_count=retry_count,
            max_retries=max_retries,
        )
        await message.nack(requeue=False)
        return

    headers[_RETRY_COUNT_HEADER] = new_retry_count

    exchange = await channel.get_exchange(retry_exchange)
    await exchange.publish(
        aio_pika.Message(
            body=message.body,
            headers=headers,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            routing_key=message.routing_key or "#",
        ),
        routing_key=message.routing_key or "#",
    )

    logger.info(
        "rabbitmq.retry.republished",
        routing_key=message.routing_key,
        retry_count=new_retry_count,
        retry_exchange=retry_exchange,
    )
    await message.ack()
