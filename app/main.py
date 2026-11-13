"""FastAPI application factory and lifespan startup."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.middleware import RequestLoggingMiddleware
from app.database.session import async_session_factory, ping_database
from app.investigation.orchestrator import InvestigationOrchestrator
from app.messaging.consumer import InvestigationConsumer
from app.messaging.handlers import make_message_handler
from app.workers.retention import RetentionWorker

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise logging, probe the database, start the RabbitMQ consumer."""
    configure_logging()

    if await ping_database():
        logger.info("startup.database.ok")
    else:
        logger.warning(
            "startup.database.unavailable",
            note="degrading gracefully; readiness will report degraded",
        )

    # --- Investigation orchestrator (PY-M04) ---
    orchestrator = InvestigationOrchestrator(async_session_factory, settings)

    # --- RabbitMQ consumer (optional) ---
    consumer: InvestigationConsumer | None = None
    consume_task: asyncio.Task | None = None  # type: ignore[type-arg]

    if settings.rabbitmq_url:
        try:
            consumer = InvestigationConsumer(settings)
            await consumer.connect()
            await consumer.declare_topology()
            handler = make_message_handler(async_session_factory, orchestrator)
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

    # --- Retention worker (PY-M09) ---
    retention_worker = RetentionWorker(
        async_session_factory,
        event_inbox_days=settings.retention_event_inbox_days,
        investigation_days=settings.retention_investigation_days,
        interval_seconds=settings.retention_interval_seconds,
    )
    retention_task = asyncio.create_task(retention_worker.run())

    yield

    # --- Shutdown ---
    retention_task.cancel()
    try:
        await retention_task
    except asyncio.CancelledError:
        pass

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
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(v1_router)
    return app


app = create_app()
