"""RabbitMQ consumer for the AGIO Investigation service.

Uses aio-pika robust connection so automatic reconnect is handled by the
library. Topology is declared passively first (queues/exchanges are owned by
Laravel), falling back to active declarations for resilience.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import aio_pika
import aio_pika.abc
import structlog

from app.core.config import Settings

logger = structlog.get_logger()

MessageHandler = Callable[[aio_pika.IncomingMessage], Coroutine[Any, Any, None]]


class InvestigationConsumer:
    """Consumes from the ``agio.investigation`` queue via a robust connection."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._consumer_tag: str | None = None
        self._queue: aio_pika.abc.AbstractQueue | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open a robust connection and configure channel QoS."""
        self._connection = await aio_pika.connect_robust(
            self._settings.rabbitmq_url,  # type: ignore[arg-type]
        )
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self._settings.rabbitmq_prefetch)
        logger.info(
            "rabbitmq.connected",
            url=self._settings.rabbitmq_url,
            prefetch=self._settings.rabbitmq_prefetch,
        )

    # ------------------------------------------------------------------
    # Topology
    # ------------------------------------------------------------------

    async def declare_topology(self) -> None:
        """Declare exchanges and queues, passive first then active fallback.

        Laravel owns the topology, so passive=True is tried first to avoid
        redeclaring with incompatible arguments.  If the resource does not yet
        exist (e.g. fresh environment), an active declaration is attempted.
        """
        assert self._channel is not None, "Call connect() before declare_topology()"

        # --- exchanges ---
        await self._declare_exchange(
            self._settings.rabbitmq_exchange, "topic", durable=True
        )
        await self._declare_exchange(self._settings.rabbitmq_dlx, "direct", durable=True)
        await self._declare_exchange(
            self._settings.rabbitmq_retry_exchange, "topic", durable=True
        )

        # --- main consumer queue ---
        main_queue = await self._declare_queue(
            self._settings.rabbitmq_queue,
            durable=True,
            arguments={"x-dead-letter-exchange": self._settings.rabbitmq_dlx},
        )
        await main_queue.bind(
            self._settings.rabbitmq_exchange,
            routing_key=self._settings.rabbitmq_routing_key,
        )
        self._queue = main_queue

        # --- dead-letter queue ---
        dlq = await self._declare_queue(self._settings.rabbitmq_dlq, durable=True)
        await dlq.bind(self._settings.rabbitmq_dlx, routing_key="#")

        # --- retry queue ---
        retry_queue = await self._declare_queue(
            self._settings.rabbitmq_retry_queue,
            durable=True,
            arguments={
                "x-message-ttl": self._settings.rabbitmq_retry_ttl_ms,
                "x-dead-letter-exchange": self._settings.rabbitmq_exchange,
            },
        )
        await retry_queue.bind(
            self._settings.rabbitmq_retry_exchange, routing_key="#"
        )

        logger.info("rabbitmq.topology.declared")

    async def _declare_exchange(
        self, name: str, exchange_type: str, *, durable: bool
    ) -> aio_pika.abc.AbstractExchange:
        """Declare an exchange passively, falling back to active."""
        assert self._channel is not None
        try:
            exchange = await self._channel.declare_exchange(
                name,
                type=aio_pika.ExchangeType(exchange_type),
                durable=durable,
                passive=True,
            )
            logger.debug("rabbitmq.exchange.passive_ok", exchange=name)
            return exchange
        except aio_pika.exceptions.ChannelClosed:
            # Channel is closed after a failed passive declare — reopen it.
            self._channel = await self._connection.channel()  # type: ignore[union-attr]
            await self._channel.set_qos(prefetch_count=self._settings.rabbitmq_prefetch)
        except Exception:
            pass

        exchange = await self._channel.declare_exchange(
            name,
            type=aio_pika.ExchangeType(exchange_type),
            durable=durable,
            passive=False,
        )
        logger.info("rabbitmq.exchange.declared", exchange=name)
        return exchange

    async def _declare_queue(
        self,
        name: str,
        *,
        durable: bool,
        arguments: dict | None = None,
    ) -> aio_pika.abc.AbstractQueue:
        """Declare a queue passively, falling back to active."""
        assert self._channel is not None
        try:
            queue = await self._channel.declare_queue(
                name,
                durable=durable,
                arguments=arguments,
                passive=True,
            )
            logger.debug("rabbitmq.queue.passive_ok", queue=name)
            return queue
        except aio_pika.exceptions.ChannelClosed:
            self._channel = await self._connection.channel()  # type: ignore[union-attr]
            await self._channel.set_qos(prefetch_count=self._settings.rabbitmq_prefetch)
        except Exception:
            pass

        queue = await self._channel.declare_queue(
            name,
            durable=durable,
            arguments=arguments,
            passive=False,
        )
        logger.info("rabbitmq.queue.declared", queue=name)
        return queue

    # ------------------------------------------------------------------
    # Consuming
    # ------------------------------------------------------------------

    async def start_consuming(self, message_handler: MessageHandler) -> None:
        """Begin consuming messages from the main queue (blocking coroutine).

        Passes messages to ``message_handler`` with ``no_ack=False`` so the
        handler is responsible for acking/nacking.
        """
        assert self._queue is not None, "Call declare_topology() first"
        consumer_tag = await self._queue.consume(message_handler, no_ack=False)
        self._consumer_tag = consumer_tag
        logger.info("rabbitmq.consuming.started", queue=self._settings.rabbitmq_queue)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Cancel the consumer and close channel + connection cleanly."""
        if self._queue is not None and self._consumer_tag is not None:
            try:
                await self._queue.cancel(self._consumer_tag)
            except Exception as exc:
                logger.warning("rabbitmq.consumer.cancel_failed", error=str(exc))
            self._consumer_tag = None

        if self._channel is not None and not self._channel.is_closed:
            try:
                await self._channel.close()
            except Exception as exc:
                logger.warning("rabbitmq.channel.close_failed", error=str(exc))
            self._channel = None

        if self._connection is not None and not self._connection.is_closed:
            try:
                await self._connection.close()
            except Exception as exc:
                logger.warning("rabbitmq.connection.close_failed", error=str(exc))
            self._connection = None

        logger.info("rabbitmq.stopped")

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> InvestigationConsumer:
        await self.connect()
        await self.declare_topology()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
