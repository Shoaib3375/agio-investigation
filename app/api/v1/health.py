"""Liveness and readiness endpoints."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.database.session import ping_database

router = APIRouter(tags=["health"])

VERSION = "0.1.0"
SERVICE = "agio-investigation"


@router.get("/health")
async def health() -> dict:
    """Liveness probe — never depends on external services."""
    return {"status": "healthy", "service": SERVICE, "version": VERSION}


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — verifies the database accepts SELECT 1."""
    if await ping_database():
        return JSONResponse({"status": "ready", "checks": {"database": "up"}})
    return JSONResponse(
        status_code=503,
        content={"status": "degraded", "checks": {"database": "down"}},
    )
