"""FastAPI middleware — structured request/response logging.

Each request gets a correlation_id (from X-Request-ID header or generated).
Every response is logged with: method, path, status, latency_ms, correlation_id.
Sensitive headers (Authorization, Cookie) are never logged.
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger()

_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "x-api-key"})


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with correlation_id, method, path, status, latency."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Reuse X-Request-ID from upstream proxy if present, otherwise generate.
        correlation_id = request.headers.get("x-request-id") or str(uuid.uuid4())

        # Bind correlation_id to structlog context for this request lifetime.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start) * 1000, 1)
            logger.error(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=500,
                latency_ms=elapsed_ms,
                error=str(exc),
            )
            raise
        finally:
            structlog.contextvars.clear_contextvars()

        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        logger.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=elapsed_ms,
        )
        response.headers["X-Request-ID"] = correlation_id
        return response
