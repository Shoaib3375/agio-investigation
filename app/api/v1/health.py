"""Liveness, readiness, and metrics endpoints."""

from __future__ import annotations

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.config import settings
from app.core.metrics import metrics
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
    """Readiness probe — checks all critical dependencies.

    Returns 200 when all required dependencies are reachable.
    Returns 503 when any critical dependency (database) is down.
    Non-critical dependency failures (RabbitMQ, Laravel) degrade
    the check but do not block readiness.
    """
    checks: dict[str, str] = {}
    degraded: list[str] = []
    critical_failed = False

    # ── Database (critical) ────────────────────────────────────────────
    if await ping_database():
        checks["database"] = "up"
    else:
        checks["database"] = "down"
        critical_failed = True

    # ── RabbitMQ (non-critical — service starts without it) ────────────
    if settings.rabbitmq_url:
        try:
            import aio_pika
            conn = await aio_pika.connect(
                settings.rabbitmq_url,
                timeout=3,
            )
            await conn.close()
            checks["rabbitmq"] = "up"
        except Exception:
            checks["rabbitmq"] = "down"
            degraded.append("rabbitmq")
    else:
        checks["rabbitmq"] = "not_configured"

    # ── Laravel internal API (non-critical) ───────────────────────────
    if settings.laravel_internal_api_url:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                url = settings.laravel_internal_api_url.rstrip("/").replace(
                    "/api/internal/investigation/v1", ""
                )
                resp = await client.get(f"{url}/up")
                checks["laravel"] = "up" if resp.status_code < 500 else "degraded"
        except Exception:
            checks["laravel"] = "down"
            degraded.append("laravel")
    else:
        checks["laravel"] = "not_configured"

    if critical_failed:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "checks": checks,
                "degraded": degraded,
            },
        )

    status = "degraded" if degraded else "ready"
    return JSONResponse(
        status_code=200 if status == "ready" else 200,  # degraded still serves traffic
        content={"status": status, "checks": checks, "degraded": degraded},
    )


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics() -> str:
    """Prometheus-format metrics — scrape with prometheus.io/scrape."""
    return metrics.render_text()
