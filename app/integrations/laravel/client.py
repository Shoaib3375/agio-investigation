"""Base HTTPX async client for the Laravel internal investigation API.

Responsibilities:
- Bearer token auth (Sanctum ``service:internal`` ability).
- Shared timeouts and connection pool.
- Bounded retries on safe GET requests (network errors / 5xx only).
- Sanitised structured error logging — never logs tokens or response secrets.
- Raises ``LaravelApiError`` subclasses so callers can handle them uniformly.

Usage::

    async with LaravelClient.from_settings(settings) as client:
        data = await client.get("/orders/{uuid}")
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from app.core.config import Settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class LaravelApiError(Exception):
    """Base class for all Laravel API integration errors."""


class LaravelNotFoundError(LaravelApiError):
    """The requested resource does not exist (HTTP 404)."""


class LaravelAuthError(LaravelApiError):
    """The service token is missing, invalid, or lacks required ability (401/403)."""


class LaravelServerError(LaravelApiError):
    """Laravel returned a 5xx response after all retry attempts."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class LaravelTimeoutError(LaravelApiError):
    """The request to Laravel timed out."""


class LaravelUnavailableError(LaravelApiError):
    """Laravel is unreachable (network error, connection refused, etc.)."""


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS = {500, 502, 503, 504}
_RETRY_BACKOFF_SECONDS = [0.5, 1.5]  # two retries → up to ~2s extra wait


async def _with_retries(
    fn: Any,
    max_retries: int,
    *,
    log: Any,
) -> httpx.Response:
    """Call async ``fn()`` with bounded exponential backoff on network errors / 5xx."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response: httpx.Response = await fn()
            if response.status_code not in _RETRYABLE_STATUS or attempt == max_retries:
                return response
            wait = _RETRY_BACKOFF_SECONDS[min(attempt, len(_RETRY_BACKOFF_SECONDS) - 1)]
            log.warning(
                "laravel.client.retry",
                status_code=response.status_code,
                attempt=attempt + 1,
                wait_seconds=wait,
            )
            await asyncio.sleep(wait)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            wait = _RETRY_BACKOFF_SECONDS[min(attempt, len(_RETRY_BACKOFF_SECONDS) - 1)]
            log.warning(
                "laravel.client.network_retry",
                error=type(exc).__name__,
                attempt=attempt + 1,
                wait_seconds=wait,
            )
            await asyncio.sleep(wait)

    if last_exc is not None:
        if isinstance(last_exc, httpx.TimeoutException):
            raise LaravelTimeoutError(str(last_exc)) from last_exc
        raise LaravelUnavailableError(str(last_exc)) from last_exc

    # Should be unreachable if max_retries >= 0 and fn always returns.
    raise LaravelUnavailableError("Exhausted retries with no response.")  # pragma: no cover


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LaravelClient:
    """Async HTTPX client wrapping the Laravel internal investigation API.

    Use as an async context manager or call ``aclose()`` explicitly.
    """

    def __init__(
        self,
        base_url: str,
        service_token: str,
        timeout_seconds: int = 10,
        max_retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {service_token}",
                "Accept": "application/json",
                "X-Service": "agio-investigation",
            },
            timeout=httpx.Timeout(timeout=float(timeout_seconds)),
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=30.0,
            ),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "LaravelClient":
        """Construct from application settings.

        Raises ``ValueError`` if the required settings are not configured.
        """
        if not settings.laravel_internal_api_url:
            raise ValueError("LARAVEL_INTERNAL_API_URL is not configured.")
        if not settings.laravel_service_token:
            raise ValueError("LARAVEL_SERVICE_TOKEN is not configured.")
        return cls(
            base_url=settings.laravel_internal_api_url,
            service_token=settings.laravel_service_token,
            timeout_seconds=settings.laravel_api_timeout_seconds,
            max_retries=settings.laravel_api_max_retries,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "LaravelClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Core request method
    # ------------------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform an authenticated GET and return the parsed JSON body.

        Parameters
        ----------
        path:
            Path relative to ``base_url`` — e.g. ``"/users/6/profile"``.
        params:
            Optional query string parameters.

        Raises
        ------
        LaravelNotFoundError
            HTTP 404.
        LaravelAuthError
            HTTP 401 or 403.
        LaravelServerError
            HTTP 5xx after all retries.
        LaravelTimeoutError
            Request timed out.
        LaravelUnavailableError
            Network-level failure.
        """
        log = logger.bind(path=path)

        async def _call() -> httpx.Response:
            return await self._http.get(path, params=params)

        try:
            response = await _with_retries(_call, self._max_retries, log=log)
        except httpx.TimeoutException as exc:
            log.warning("laravel.client.timeout", path=path)
            raise LaravelTimeoutError(f"Timeout requesting {path}") from exc
        except httpx.NetworkError as exc:
            log.warning("laravel.client.unavailable", path=path, error=str(exc))
            raise LaravelUnavailableError(f"Network error requesting {path}") from exc

        self._handle_status(response, path, log)

        try:
            return response.json()
        except Exception as exc:  # noqa: BLE001
            log.error("laravel.client.invalid_json", path=path, status=response.status_code)
            raise LaravelServerError(response.status_code, f"Non-JSON response from {path}") from exc

    # ------------------------------------------------------------------
    # Status code handling
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_status(response: httpx.Response, path: str, log: Any) -> None:
        if response.status_code == 200:
            return
        if response.status_code == 404:
            raise LaravelNotFoundError(f"Resource not found: {path}")
        if response.status_code in (401, 403):
            # Do NOT log the actual token or response body.
            log.error(
                "laravel.client.auth_error",
                path=path,
                status_code=response.status_code,
            )
            raise LaravelAuthError(
                f"Service token rejected by Laravel (HTTP {response.status_code}) for {path}"
            )
        if response.status_code >= 500:
            log.error(
                "laravel.client.server_error",
                path=path,
                status_code=response.status_code,
            )
            raise LaravelServerError(
                response.status_code,
                f"Laravel returned HTTP {response.status_code} for {path}",
            )
        # 4xx other than 401/403/404 — treat as not found for safety.
        log.warning(
            "laravel.client.unexpected_status",
            path=path,
            status_code=response.status_code,
        )
        raise LaravelNotFoundError(
            f"Unexpected HTTP {response.status_code} from {path}"
        )
