"""FastAPI application factory and lifespan startup."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.v1.router import router as v1_router
from app.core.logging import configure_logging
from app.database.session import ping_database

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise logging, then probe the database without crashing."""
    configure_logging()
    if await ping_database():
        logger.info("startup.database.ok")
    else:
        logger.warning(
            "startup.database.unavailable",
            note="degrading gracefully; readiness will report degraded",
        )
    yield


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
