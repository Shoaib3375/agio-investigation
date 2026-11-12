"""FastAPI application factory and lifespan startup."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.database.session import async_session_factory, ping_database
from app.messaging.consumer import InvestigationConsumer
from app.messaging.handlers import make_message_handler

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise logging, probe the database, and start the RabbitMQ consumer."""
    configure_logging()

    if await ping_database():
        logger.info("startup.database.ok")
    else:
        logger.warning(
            "startup.database.unavailable",
            note="degrading gracefully; readiness will report degraded",
        )

    # --- RabbitMQ consumer (optional) ---
    consumer: InvestigationConsumer | None = None
    consume_task: asyncio.Task | None = None  # type: ignore[type-arg]

    if settings.rabbitmq_url:
        try:
            consumer = InvestigationConsumer(settings)
            await consumer.connect()
            await consumer.declare_topology()
            handler = make_message_handler(async_session_factory)
            consume_task = asyncio.create_task(consumer.start_consuming(handler))
            logger.info("startup.rabbitmq.consumer.started")
        except Exception as exc:
            logger.warning(
                "startup.rabbitmq.unavailable",
                error=str(exc),
                note="service starts without RabbitMQ consumer",
            )
            consumer = None
            consume_task = None
    else:
        logger.warning(
            "startup.rabbitmq.not_configured",
            note="RABBITMQ_URL not set; consumer skipped",
        )

    yield

    # --- Shutdown ---
    if consume_task is not None:
        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass

    if consumer is not None:
        await consumer.stop()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="AGIO Investigation Service",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(v1_router)
    return app


app = create_app()
